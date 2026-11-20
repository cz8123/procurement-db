# -*- coding: utf-8 -*-
"""映射聚合：把一个字段的多种写法聚合成一张「聚合维度表」。

和 clean.py 的区别
------------------
  clean   就地改写源表 —— 把变体合并掉，源数据被永久修改（有损）。
  aggmap  不动源数据 —— 生成一张 原始值 → 聚合值 的桥接维度表：

        dim_agg_xxx
        ┌──────────┬──────────┐
        │ 原始值    │ 采购子类别 │   ← 一个原始写法一行
        ├──────────┼──────────┤
        │ IT 硬件   │ IT 硬件   │
        │ IT硬件    │ IT 硬件   │   ← 聚合值相同 = 同一类
        └──────────┴──────────┘

  维度表只放「原始值 / 聚合值」（可再加附加字段）——行数、写法数是统计口径，
  需要时现算，不落库，免得维度表被一堆统计列撑成报表。

  事实表仍按「原始值」关联（因为是照抄源字段去重出来的，孤儿率天然为 0），
  可视化时改用「聚合值」分组，统计口径统一，而源数据一个字节都没改。

为什么不直接写回事实表
----------------------
写回会永久覆盖原始写法，用户无法回溯；且不同表对同一字段的写法差异需要各自
维护。桥接维度表把「合并规则」变成一条可编辑、可导出、可复用的数据，改映射
只改这张小表，事实表与关系都不受影响。
"""

import csv
import io
import os
import re
import sqlite3
import time
from collections import Counter
from difflib import SequenceMatcher

import clean

COL_RAW = "原始值"
# 旧版本建表时会带这两列统计口径；现在不再建，读取时兼容（有就用来排序，没有就现算），
# 存量表由 drop_stats_columns() 在启动时清掉。
COL_ROWS = "源行数"
COL_VARIANTS = "写法数"
_STATS_COLS = (COL_ROWS, COL_VARIANTS)

# 附加字段（聚合表除两个固定列之外，用户自己加的列，如 Brand / 区域 / 城市）
# 全部手工维护：建表时只建空列，值在「补录 / 编辑」里逐行填。
EXTRA_TYPES = {"text": "TEXT", "number": "REAL", "integer": "INTEGER", "date": "TEXT"}
# 保留列仍含旧统计列名：避免用户把附加字段起成「源行数」「写法数」后与历史表撞车
_RESERVED_COLS = {"id", COL_RAW, COL_ROWS, COL_VARIANTS}
_IDENT_RE = re.compile(r"^[A-Za-z_\u4e00-\u9fa5][\w\u4e00-\u9fa5]*$")

# 一次扫描最多处理多少个不同取值（超过则截断，避免前端卡死）
MAX_VALUES = 50000
# 相似度整合的规模上限：再大就不是「人工复核」能覆盖的量，改用导出/导入
MAX_FUZZY = 800

_RAW_ALIASES = {"原始值", "原值", "源值", "原始文本", "源文本", "原始", "待聚合值",
                "raw", "raw_value", "source", "source_value", "value"}
_AGG_ALIASES = {"聚合值", "聚合", "聚合后", "目标值", "标准值", "规范值", "映射值",
                "归并值", "分组", "分组值", "agg", "agg_value", "target", "group", "mapped"}
_ROWS_ALIASES = {"行数", "出现行数", "次数", "频次", "rows", "count"}


def qid(name):
    return '"' + str(name).replace('"', '""') + '"'


# ---------------------------------------------------------------------------
# 基础
# ---------------------------------------------------------------------------
def columns(con, table):
    try:
        return [r[1] for r in con.execute(f"PRAGMA table_info({qid(table)})").fetchall()]
    except sqlite3.Error:
        return []


def has_table(con, table):
    row = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
    return bool(row)


def _value_rows(con, table, col):
    """去重后的 (取值, 行数)，按行数降序。"""
    return con.execute(
        f"SELECT CAST({qid(col)} AS TEXT) AS v, COUNT(*) AS n FROM {qid(table)} "
        f"WHERE {qid(col)} IS NOT NULL AND TRIM(CAST({qid(col)} AS TEXT)) <> '' "
        f"GROUP BY 1 ORDER BY n DESC, v").fetchall()


def _pick_canonical(items):
    """从 [{value, rows}] 里挑规范写法：写法最规整 > 用得最多 > 更长。"""
    return sorted(items, key=lambda m: (clean.ugliness(m["value"]), -m["rows"],
                                        -len(str(m["value"])), str(m["value"])))[0]["value"]


def _ascii_slug(text):
    """把列名变成英文表名片段；含中文时返回空（中文名直接进表名更好认）。"""
    t = re.sub(r"^\s*table\s*-\s*", "", str(text or ""), flags=re.I)
    if re.search(r"[\u4e00-\u9fa5]", t):
        return ""
    s = re.sub(r"[^0-9A-Za-z]+", "_", t).strip("_").lower()
    return s if re.search(r"[a-z]", s) else ""


def _name_slug(text):
    """列名 → 表名片段：英文列转小写，中文列保留中文（去掉标点）。"""
    t = re.sub(r"^\s*table\s*-\s*", "", str(text or ""), flags=re.I).strip()
    slug = _ascii_slug(t)
    if slug:
        return slug
    t = re.sub(r"[\s\-_/\\|·.,、，。()（）\[\]【】{}<>&+'\"“”‘’~!@#$%^*=+:;?！？；：…—－]+", "", t)
    t = re.sub(r"[^0-9A-Za-z_\u4e00-\u9fa5]", "", t)
    return t[:24]


def suggest_dim_name(con, table, col):
    """给聚合维度表起名：dim_agg_ + 字段名（优先沿用已关联维度表的英文名）。"""
    stem = ""
    try:
        row = con.execute(
            "SELECT target_table FROM map_config WHERE source_table=? AND source_col=? "
            "AND enabled=1 AND target_table GLOB 'dim_*' ORDER BY id LIMIT 1",
            (table, col)).fetchone()
        if row:
            stem = re.sub(r"^dim_", "", row[0])
    except sqlite3.Error:
        stem = ""
    if not stem:
        stem = _name_slug(col)
    if not stem and str(table).startswith("dim_"):
        stem = re.sub(r"^dim_", "", str(table))
    if not stem:
        stem = re.sub(r"^(dim_|fct_)", "", str(table))
    stem = re.sub(r"[^0-9A-Za-z_\u4e00-\u9fa5]+", "_", stem).strip("_") or "field"
    base = "dim_agg_" + stem
    name, i = base, 2
    while has_table(con, name) or name in _views(con):
        name = f"{base}_{i}"
        i += 1
    return name


def _views(con):
    try:
        return {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='view'").fetchall()}
    except sqlite3.Error:
        return set()


def value_col_of(col):
    """聚合值列名默认取源列名，去掉「Table - 」前缀与不方便的字符。"""
    t = re.sub(r"^\s*table\s*-\s*", "", str(col or ""), flags=re.I).strip()
    t = re.sub(r"[\r\n\t]+", " ", t).strip()
    return t or "聚合值"


# ---------------------------------------------------------------------------
# 自动整合同类项
# ---------------------------------------------------------------------------
def _quick_ratio(ca, cb, la, lb):
    """与 SequenceMatcher.quick_ratio 同义的快速上界（预先算好字符计数）。"""
    if not la or not lb:
        return 0.0
    inter = sum((ca & cb).values())
    return 2.0 * inter / (la + lb)


def _cluster_similar(values, threshold):
    """按相似度做并查集聚类（只处理规模可控的取值集合）。"""
    n = len(values)
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    keys = [clean.norm_key(v) for v in values]
    counters = [Counter(k) for k in keys]
    lens = [len(k) for k in keys]
    order = sorted(range(n), key=lambda i: lens[i])

    for pos, i in enumerate(order):
        li = lens[i]
        for j in order[pos + 1:]:
            lj = lens[j]
            if lj > li * 1.5 + 1:          # order 按长度升序，后面只会更长
                break
            if abs(li - lj) > max(2, int(li * 0.4)):
                continue
            if _quick_ratio(counters[i], counters[j], li, lj) < threshold:
                continue
            if SequenceMatcher(None, keys[i], keys[j]).ratio() >= threshold:
                ri, rj = find(i), find(j)
                if ri != rj:
                    parent[rj] = ri
    return [find(i) for i in range(n)]


def auto_map(con, table, col, mode="exact", threshold=0.9):
    """扫描字段并生成初始映射。

    返回 {table, col, total_values, total_rows, rows:[{raw, rows, agg, auto}], ...}
    mode: exact=规范化后完全相同才算同类（保守）；fuzzy=再叠加相似度（需人工复核）。
    """
    if col not in columns(con, table):
        raise ValueError(f"列不存在：{table}.{col}")
    data = _value_rows(con, table, col)
    truncated = len(data) > MAX_VALUES
    if truncated:
        data = data[:MAX_VALUES]

    items = [{"value": r["v"], "rows": r["n"]} for r in data]
    total_rows = sum(i["rows"] for i in items)

    # ① 规范化键：全角半角、大小写、空格标点差异 → 同一类
    buckets = {}
    for it in items:
        k = clean.norm_key(it["value"]) or it["value"]
        buckets.setdefault(k, []).append(it)

    clusters = []      # [[item, ...], ...]
    for members in buckets.values():
        clusters.append(members)

    fuzzy_used = False
    fuzzy_error = ""
    if mode == "fuzzy" and len(clusters) > 1:
        if len(clusters) > MAX_FUZZY:
            fuzzy_error = (f"规范化后仍有 {len(clusters)} 个不同取值，超过相似度整合上限 "
                           f"{MAX_FUZZY} 个。请改用「导出映射 → Excel 修改 → 导入」。")
        else:
            fuzzy_used = True
            reps = [_pick_canonical(c) for c in clusters]
            roots = _cluster_similar(reps, max(0.5, min(1.0, float(threshold))))
            merged = {}
            for idx, root in enumerate(roots):
                merged.setdefault(root, []).extend(clusters[idx])
            clusters = list(merged.values())

    rows = []
    for members in clusters:
        canonical = _pick_canonical(members)
        for it in members:
            rows.append({
                "raw": it["value"], "rows": it["rows"], "agg": canonical,
                "auto": it["value"] != canonical,
            })
    rows.sort(key=lambda r: (-r["rows"], r["raw"]))

    return {
        "table": table, "col": col, "mode": mode, "threshold": threshold,
        "total_values": len(items), "total_rows": total_rows,
        "agg_values": len(clusters),
        "merged_values": sum(1 for r in rows if r["auto"]),
        "truncated": truncated, "fuzzy_used": fuzzy_used, "fuzzy_error": fuzzy_error,
        "rows": rows,
    }


# ---------------------------------------------------------------------------
# 导出 / 导入
# ---------------------------------------------------------------------------
def export_rows(con, table, col, mode="exact", threshold=0.9):
    """导出「去重后的取值 + 建议聚合值」，供人工在 Excel 里改完再导回。"""
    d = auto_map(con, table, col, mode=mode, threshold=threshold)
    return d, [(r["raw"], r["rows"], r["agg"]) for r in d["rows"]]


def _cell_text(v):
    if v is None:
        return ""
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def _decode(data):
    for enc in ("utf-8-sig", "utf-8", "gbk", "gb18030"):
        try:
            return data.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1", errors="replace"), "latin-1(可能乱码)"


def _read_xlsx(data):
    try:
        from openpyxl import load_workbook
    except ImportError:
        raise ValueError("服务器缺少 openpyxl，暂不支持 .xlsx 上传；请另存为 CSV 后再导入")
    wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    ws = wb[wb.sheetnames[0]]
    rows = []
    for row in ws.iter_rows(values_only=True):
        rows.append([_cell_text(c) for c in row])
    wb.close()
    return rows


def _col_name_fix(text, used):
    """把表头文字整理成合法列名：去空白与标点，非法字符换下划线，重名加序号。"""
    s = re.sub(r"\s+", "", str(text or "").strip())
    s = re.sub(r"[^\w\u4e00-\u9fa5]", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")[:40]
    if s and not re.match(r"^[A-Za-z_\u4e00-\u9fa5]", s):
        s = "c_" + s
    if not s:
        return "", False
    base, i = s, 2
    while s.lower() in used:
        s = f"{base}_{i}"
        i += 1
    return s, s != str(text or "").strip()


def parse_mapping(data, filename="", is_known=None):
    """解析用户上传的映射文件，返回 [{raw, agg, extra}] 及诊断信息。

    识别规则（按用户习惯，「第一列永远是原始值」）：
      · 第一列 = 原始值，表头可以自定义，不认名字也认位置；
      · 表头写着「聚合值」的那一列才是聚合值，没有这列时聚合值 = 原始值（1:1）；
      · 其余列按表头建成维度表的附加字段（如「部门en」→ 附加字段 部门en）；
      · 旧导出文件里的「行数/写法数」列自动跳过。
    表头判定：① 认得出 原始值/聚合值 别名；② 传了 is_known 时，
    首行一个已知取值都没有、而第二行有 → 首行当表头。
    """
    name = (filename or "").lower()
    if name.endswith((".xlsx", ".xlsm")):
        grid = _read_xlsx(data)
        enc = "xlsx"
    else:
        text, enc = _decode(data)
        sample = text[:8192]
        delim = ","
        try:
            delim = csv.Sniffer().sniff(sample, delimiters=[",", ";", "\t", "|"]).delimiter
        except Exception:
            if sample.count("\t") > sample.count(","):
                delim = "\t"
        grid = [row for row in csv.reader(io.StringIO(text), delimiter=delim)]

    grid = [[_cell_text(c).strip() for c in row] for row in grid if any(_cell_text(c).strip() for c in row)]
    if not grid:
        raise ValueError("文件里没有读到任何数据行")

    def norm_head(s):
        return re.sub(r"[\s_\-]+", "", str(s or "")).lower()

    raw_heads = {norm_head(x) for x in _RAW_ALIASES}
    agg_heads = {norm_head(x) for x in _AGG_ALIASES}
    rows_heads = {norm_head(x) for x in _ROWS_ALIASES}
    rows_heads |= {norm_head(x) for x in (COL_ROWS, COL_VARIANTS)}

    raw_idx = agg_idx = None
    head_row = None
    for pos, r in enumerate(grid[:3]):
        hit = False
        for i, cell in enumerate(r):
            h = norm_head(cell)
            if raw_idx is None and h in raw_heads:
                raw_idx, hit = i, True
            if agg_idx is None and h in agg_heads:
                agg_idx, hit = i, True
        if hit:
            head_row = pos
            break

    head_guess = False
    if head_row is None and is_known and len(grid) > 1:
        r0 = [c for c in grid[0] if c]
        if r0 and not any(is_known(c) for c in r0) \
                and any(is_known(c) for c in grid[1] if c):
            head_row = 0
            head_guess = True

    head_cells = grid[head_row] if head_row is not None else []
    body = grid[head_row + 1:] if head_row is not None else grid

    if raw_idx is None:
        raw_idx = 0
    # 没认出「聚合值」表头：只有在纯两列且无表头的老格式下才把第二列当聚合值
    if agg_idx is None and head_row is None:
        agg_idx = 1 if raw_idx == 0 else 0

    width = max([len(r) for r in body] + [len(head_cells), raw_idx + 1])
    extra_idx, used, renamed = [], {"id", COL_RAW.lower(), COL_ROWS.lower(), COL_VARIANTS.lower()}, []
    for i in range(width):
        if i in (raw_idx, agg_idx):
            continue
        if head_cells and i < len(head_cells) and norm_head(head_cells[i]) in rows_heads:
            continue          # 旧导出文件里的统计列，忽略
        if not any((r[i] if i < len(r) else "") for r in body):
            continue          # 整列为空
        head = head_cells[i] if i < len(head_cells) else ""
        fixed, changed = _col_name_fix(head or f"列{i + 1}", used)
        if not fixed:
            continue
        if head and changed:
            renamed.append({"from": head, "to": fixed})
        used.add(fixed.lower())
        extra_idx.append((fixed, i))

    records, blank_agg, bad = [], 0, 0
    for r in body:
        raw = r[raw_idx] if raw_idx < len(r) else ""
        if not raw:
            bad += 1
            continue
        if agg_idx is None:
            # 文件里根本没有「聚合值」列（只有映射字段）：不碰聚合值，
            # 由调用方沿用库里/屏幕上的现值，避免把已合并的结果打回 1:1。
            agg = None
        else:
            agg = r[agg_idx] if agg_idx < len(r) else ""
            if not agg:
                blank_agg += 1
                agg = raw
        ex = {}
        for col, i in extra_idx:
            v = r[i] if i < len(r) else ""
            if v:
                ex[col] = v
        records.append({"raw": raw, "agg": agg, "extra": ex})

    return {
        "records": records,
        "pairs": [(x["raw"], x["raw"] if x["agg"] is None else x["agg"])
                  for x in records],   # 兼容旧调用
        "extra_cols": [c for c, _ in extra_idx],
        "renamed": renamed,
        "encoding": enc,
        "header": head_row is not None,
        "header_guess": head_guess,
        "agg_from_file": agg_idx is not None,
        "raw_head": (head_cells[raw_idx] if raw_idx < len(head_cells) else ""),
        "rows": len(records), "blank_agg": blank_agg, "skipped": bad,
    }


# ---------------------------------------------------------------------------
# 建表 / 维护
# ---------------------------------------------------------------------------
_RE_IDENT = re.compile(r"^dim_[A-Za-z0-9_\u4e00-\u9fa5]{1,40}$")


def validate_dim_name(con, name):
    n = (name or "").strip()
    if not _RE_IDENT.match(n):
        return ("表名必须以 dim_ 开头，只用中英文、数字、下划线（总长 2~45 字符）；"
                "不要含空格与标点")
    if has_table(con, n) or n in _views(con):
        return f"库里已有同名对象「{n}」，请换一个名字"
    return None


def validate_value_col(name):
    c = (name or "").strip()
    if not c:
        return "聚合值列名不能为空"
    if len(c) > 40:
        return "聚合值列名最多 40 个字符"
    if '"' in c or "'" in c or "\n" in c:
        return "聚合值列名不能包含引号或换行"
    if c in ("id", COL_RAW, COL_ROWS, COL_VARIANTS):
        return f"「{c}」是保留列名，请换一个"
    return None


def _clean_extra(raw):
    """附加字段值清洗：只保留非空值（空串=没提供，不覆盖库里的手工值）。"""
    if not isinstance(raw, dict):
        return {}
    out = {}
    for k, v in raw.items():
        k = str(k or "").strip()
        if not k:
            continue
        s = "" if v is None else str(v).strip()
        if s:
            out[k] = s
    return out


def _normalize_rows(rows):
    """清洗前端/文件传来的映射：去空、去重（后者覆盖前者）、聚合值空则取原值。"""
    out, seen = [], {}
    for r in rows or []:
        raw = "" if r.get("raw") is None else str(r.get("raw"))
        if not raw.strip():
            continue
        agg = "" if r.get("agg") is None else str(r.get("agg"))
        agg = agg.strip() or raw
        src = int(r.get("rows") or 0)
        extra = _clean_extra(r.get("extra"))
        if raw in seen:
            cur = out[seen[raw]]
            cur.update({"agg": agg, "src_rows": src})
            if extra:
                cur["extra"].update(extra)
            continue
        seen[raw] = len(out)
        out.append({"raw": raw, "agg": agg, "src_rows": src, "extra": extra})
    return out


def normalize_extra_cols(raw, value_col, existing=()):
    """校验附加字段定义，返回 [(列名, SQL 类型), ...]。

    入参可以是 [{"name":"Brand","type":"text"}] 或 ["Brand", ...]（默认 text）。
    保留列（id/原始值/源行数/写法数）与聚合值列不能重复占用；同名忽略。
    """
    out, seen = [], {str(c).lower() for c in existing}
    reserved = {c.lower() for c in _RESERVED_COLS}
    lows = str(value_col).lower()
    for it in raw or []:
        if isinstance(it, dict):
            name = str(it.get("name") or "").strip()
            typ = str(it.get("type") or "text").strip().lower()
        else:
            name, typ = str(it or "").strip(), "text"
        if not name:
            continue
        if not _IDENT_RE.match(name):
            raise ValueError(f"附加字段名「{name}」不合法：只能用字母、数字、下划线或中文，且不能以数字开头")
        if name.lower() in reserved:
            raise ValueError(f"「{name}」是保留列名，不能作为附加字段")
        if name.lower() == lows:
            raise ValueError(f"「{name}」已经是这张表的聚合值列名，不能再当附加字段（请换个表头）")
        if name.lower() in seen:
            continue
        sql = EXTRA_TYPES.get(typ)
        if not sql:
            raise ValueError(f"附加字段「{name}」的类型不支持：{typ}（可选 {'/'.join(EXTRA_TYPES)}）")
        seen.add(name.lower())
        out.append((name, sql))
    return out


def add_columns(con, dim_table, cols, value_col=None):
    """给已存在的聚合维度表加附加字段（幂等：已存在的列跳过）。"""
    if not has_table(con, dim_table):
        raise ValueError(f"聚合维度表 {dim_table} 不存在")
    exist = columns(con, dim_table)
    if value_col is None:
        value_col = next((c for c in exist
                          if c not in _RESERVED_COLS), "")
    defs = normalize_extra_cols(cols, value_col, existing=exist)
    added = []
    for name, sql in defs:
        con.execute(f"ALTER TABLE {qid(dim_table)} ADD COLUMN {qid(name)} {sql}")
        added.append({"name": name, "type": sql})
    return added


def build(con, name, source_table, source_col, dim_table, value_col, rows,
          relations=None, disable_existing=True, note="", extra_cols=None):
    """建聚合成维度表 + 写任务记录 + 在模型视图建关系（全部在一个事务里）。"""
    err = validate_dim_name(con, dim_table)
    if err:
        raise ValueError(err)
    if source_col not in columns(con, source_table):
        raise ValueError(f"源字段不存在：{source_table}.{source_col}")
    err = validate_value_col(value_col)
    if err:
        raise ValueError(err)

    items = _normalize_rows(rows)
    if not items:
        raise ValueError("映射为空，没有可写入的取值")

    value_col = value_col.strip()
    # 附加字段：导入文件带了表头就按表头建列并填值；没有则只建空列，值后续手工维护
    row_extra = []
    for it in items:
        for k in it.get("extra") or {}:
            if k not in row_extra:
                row_extra.append(k)
    declared = list(extra_cols or []) + [{"name": k, "type": "text"} for k in row_extra]
    extras = normalize_extra_cols(declared, value_col)
    extra_sql = "".join(f", {qid(n)} {t}" for n, t in extras)
    con.execute(
        f"CREATE TABLE {qid(dim_table)} ("
        f"id INTEGER PRIMARY KEY AUTOINCREMENT, "
        f"{qid(COL_RAW)} TEXT NOT NULL UNIQUE, "
        f"{qid(value_col)} TEXT NOT NULL{extra_sql})")

    # 只落「原始值 → 聚合值」（+ 文件带来的附加字段值）；行数/写法数用时现算
    ex_names = [n for n, _ in extras]
    ins_cols = [COL_RAW, value_col] + ex_names
    con.executemany(
        f"INSERT INTO {qid(dim_table)} ({','.join(qid(c) for c in ins_cols)}) "
        f"VALUES ({','.join('?' * len(ins_cols))})",
        [(i["raw"], i["agg"], *[(i.get("extra") or {}).get(n) for n in ex_names])
         for i in items])

    now = time.strftime("%Y-%m-%d %H:%M:%S")
    con.execute(
        "INSERT INTO agg_task(name,source_table,source_col,dim_table,key_col,value_col,"
        "method,note,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (name or f"{source_col} 聚合", source_table, source_col, dim_table,
         COL_RAW, value_col, "auto", note or "", now, now))
    task_id = con.execute("SELECT last_insert_rowid()").fetchone()[0]

    made, disabled = [], 0
    targets = [(source_table, source_col)]
    for p in relations or []:
        try:
            t, c = str(p[0]), str(p[1])
        except (TypeError, IndexError):
            continue
        if (t, c) not in targets and c in columns(con, t):
            targets.append((t, c))

    for t, c in targets:
        dup = con.execute(
            "SELECT id FROM map_config WHERE source_table=? AND source_col=? "
            "AND target_table=? AND target_col=?", (t, c, dim_table, COL_RAW)).fetchone()
        if dup:
            continue
        con.execute(
            "INSERT INTO map_config(source_table,source_col,target_table,target_col,kind,"
            "enabled,note,cardinality) VALUES (?,?,?,?,?,1,?,?)",
            (t, c, dim_table, COL_RAW, "dim",
             f"映射聚合：{name or source_col}（{COL_RAW} → {value_col}）", "*:1"))
        made.append({"table": t, "col": c})

    if disable_existing:
        # 同一源列若还挂着别的维度关系，Power BI 里会有两条关系（一条只能是非活动），
        # 默认停用旧的，避免用户在建好的模型里选错。附带建的关联同样处理。
        for t, c in targets:
            cur = con.execute(
                "UPDATE map_config SET enabled=0 WHERE enabled=1 AND source_table=? AND source_col=? "
                "AND NOT (target_table=? AND target_col=?)",
                (t, c, dim_table, COL_RAW))
            disabled += cur.rowcount

    stats = dim_stats(con, dim_table, COL_RAW, value_col)
    return {"task_id": task_id, "dim_table": dim_table, "value_col": value_col,
            "relations": made, "disabled": disabled, "stats": stats,
            "extra_cols": [{"name": n, "type": t} for n, t in extras]}


def dim_stats(con, dim_table, key_col=COL_RAW, value_col="聚合值"):
    cols = columns(con, dim_table)
    if value_col not in cols:
        value_col = next((c for c in cols if c not in ("id", COL_RAW, COL_ROWS, COL_VARIANTS)), value_col)
    try:
        n = con.execute(f"SELECT COUNT(*) FROM {qid(dim_table)}").fetchone()[0]
        g = con.execute(
            f"SELECT COUNT(DISTINCT {qid(value_col)}) FROM {qid(dim_table)}").fetchone()[0]
    except sqlite3.Error:
        n = g = 0
    return {"rows": n, "aggs": g, "merged": max(0, n - g)}


def list_tasks(con):
    try:
        rows = con.execute(
            "SELECT id,name,source_table,source_col,dim_table,value_col,method,note,"
            "created_at,updated_at FROM agg_task ORDER BY id").fetchall()
    except sqlite3.Error:
        return []
    out = []
    for r in rows:
        if not has_table(con, r["dim_table"]):
            continue
        d = dim_stats(con, r["dim_table"], COL_RAW, r["value_col"])
        rels = con.execute(
            "SELECT id,source_table,source_col FROM map_config "
            "WHERE target_table=? AND target_col=? ORDER BY id",
            (r["dim_table"], COL_RAW)).fetchall()
        try:
            total, orphan, rate, status = rel_stats(
                con, r["source_table"], r["source_col"], r["dim_table"], COL_RAW)
        except sqlite3.Error:
            total, orphan, rate, status = 0, 0, 0.0, "err"
        out.append({
            "id": r["id"], "name": r["name"],
            "source_table": r["source_table"], "source_col": r["source_col"],
            "dim_table": r["dim_table"], "value_col": r["value_col"],
            "method": r["method"], "note": r["note"],
            "created_at": r["created_at"], "updated_at": r["updated_at"],
            "stats": d, "relations": [{"id": x["id"], "table": x["source_table"],
                                       "col": x["source_col"]} for x in rels],
            "total": total, "orphan": orphan, "rate": round(rate, 2), "status": status,
        })
    return out


def rel_stats(con, t, col, rt, rc):
    total = con.execute(
        f"SELECT COUNT(*) FROM {qid(t)} WHERE {qid(col)} IS NOT NULL").fetchone()[0]
    orphan = con.execute(
        f"SELECT COUNT(*) FROM {qid(t)} WHERE {qid(col)} IS NOT NULL "
        f"AND {qid(col)} NOT IN (SELECT {qid(rc)} FROM {qid(rt)})").fetchone()[0]
    rate = (orphan / total * 100) if total else 0.0
    return total, orphan, rate, ("ok" if rate == 0 else ("warn" if rate <= 5 else "bad"))


def get_task(con, tid):
    r = con.execute("SELECT * FROM agg_task WHERE id=?", (tid,)).fetchone()
    if not r:
        return None
    return {k: r[k] for k in r.keys()}


def task_of(con, table, col):
    """按源表 + 字段找已保存的聚合任务（同一字段取最后一条），维度表不存在则视为没有。"""
    try:
        r = con.execute(
            "SELECT * FROM agg_task WHERE source_table=? AND source_col=? "
            "ORDER BY id DESC LIMIT 1", (table, col)).fetchone()
    except sqlite3.Error:
        return None
    if not r:
        return None
    t = {k: r[k] for k in r.keys()}
    return t if has_table(con, t["dim_table"]) else None


def task_rows(con, task):
    """读已保存的映射：[{raw, agg, rows, extra?}]，按源行数降序。

    rows 是「该写法在源表里有多少行」的**现算**结果（旧表若还留着「源行数」列则直接读），
    仅用于页面排序与核对，不写回维度表。
    """
    cols = columns(con, task["dim_table"])
    vcol = task["value_col"] if task["value_col"] in cols else cols[2] if len(cols) > 2 else COL_RAW
    extra = extra_columns(con, task["dim_table"], vcol)
    sel_extra = "".join(f", {qid(c)}" for c in extra)
    stored = COL_ROWS in cols
    sel_rows = f", {qid(COL_ROWS)} AS n" if stored else ""
    try:
        rows = con.execute(
            f"SELECT {qid(COL_RAW)} AS raw, {qid(vcol)} AS agg{sel_rows}{sel_extra} "
            f"FROM {qid(task['dim_table'])}").fetchall()
    except sqlite3.Error:
        return []
    counts = {}
    if not stored:
        try:
            counts = {r["v"]: r["n"] for r in
                      _value_rows(con, task["source_table"], task["source_col"])}
        except sqlite3.Error:
            counts = {}
    out = []
    for x in rows:
        d = {"raw": x["raw"], "agg": x["agg"],
             "rows": (x["n"] or 0) if stored else counts.get(x["raw"], 0)}
        if extra:
            d["extra"] = {c: x[c] for c in extra}
        out.append(d)
    out.sort(key=lambda d: (-int(d["rows"] or 0), str(d["raw"])))
    return out


def extra_columns(con, dim_table, value_col):
    """聚合表上用户自建的附加字段（排除 id / 原始值 / 聚合值 / 源行数 / 写法数）。"""
    return [c for c in columns(con, dim_table)
            if c not in _RESERVED_COLS and c != value_col]


def rewrite_mapping(con, task, rows, extra_cols=None):
    """整体重写某任务的映射（关系与表名不变）。

    与「删表重插」的关键区别：**保留用户手工维护的附加字段值**。
    按原始值做 diff —— 消失的删除、新增的插入、仍在的只更新聚合值；
    只有导入文件明确带来值的附加字段才写，其余一个字节都不动。
    文件里出现的新表头会在维度表上补列（extra_cols 或行内 extra 的键名）。
    """
    items = _normalize_rows(rows)
    if not items:
        raise ValueError("映射为空，至少保留一个取值")
    dim, vcol = task["dim_table"], task["value_col"]
    if not has_table(con, dim):
        raise ValueError(f"聚合维度表 {dim} 已不存在")

    # 文件新增的表头 → 先在维度表上补列，再写值
    want = list(extra_cols or [])
    for it in items:
        for k in it.get("extra") or {}:
            want.append({"name": k, "type": "text"})
    if want:
        defs = normalize_extra_cols(want, vcol, existing=columns(con, dim))
        for name, sql in defs:
            con.execute(f"ALTER TABLE {qid(dim)} ADD COLUMN {qid(name)} {sql}")

    extra = extra_columns(con, dim, vcol)
    old = {r[0] for r in con.execute(f"SELECT {qid(COL_RAW)} FROM {qid(dim)}")}
    want_raw = {i["raw"] for i in items}
    gone = old - want_raw
    if gone:
        con.executemany(f"DELETE FROM {qid(dim)} WHERE {qid(COL_RAW)}=?",
                        [(x,) for x in gone])

    # 历史表若还留着统计列，一并写回去保持一致；新表没这两列
    have = set(columns(con, dim))
    stats = [c for c in _STATS_COLS if c in have]
    agg_count = Counter(i["agg"] for i in items)
    ins_cols = [COL_RAW, vcol] + stats + extra
    ins_sql = (f"INSERT INTO {qid(dim)} ({','.join(qid(c) for c in ins_cols)}) "
               f"VALUES ({','.join('?' * len(ins_cols))})")
    for it in items:
        raw, agg = it["raw"], it["agg"]
        ex = it.get("extra") or {}
        stat_vals = [it["src_rows"] if c == COL_ROWS else agg_count[agg] for c in stats]
        if raw in old:
            sets = [f"{qid(vcol)}=?"] + [f"{qid(c)}=?" for c in stats]
            vals = [agg] + stat_vals
            for k, v in ex.items():
                if k in extra:
                    sets.append(f"{qid(k)}=?")
                    vals.append(v)
            con.execute(f"UPDATE {qid(dim)} SET {','.join(sets)} WHERE {qid(COL_RAW)}=?",
                        vals + [raw])
        else:
            con.execute(ins_sql, [raw, agg] + stat_vals
                        + [ex.get(c) for c in extra])
    con.execute("UPDATE agg_task SET updated_at=? WHERE id=?",
                (time.strftime("%Y-%m-%d %H:%M:%S"), task["id"]))
    return dim_stats(con, dim, COL_RAW, vcol)


def set_extra_values(con, task, values):
    """批量写入附加字段的值。values = [{raw, extra:{列名: 值}}, ...]（只更新非空项）。

    返回 {updated, skipped, unknown_cols}。列名必须是该表真实存在的附加字段，
    未知列名直接报错（避免静默丢数据）。
    """
    dim, vcol = task["dim_table"], task["value_col"]
    if not has_table(con, dim):
        raise ValueError(f"聚合维度表 {dim} 已不存在")
    extra = extra_columns(con, dim, vcol)
    allowed = set(extra)
    unknown, updated, skipped = set(), 0, 0
    for it in values or []:
        raw = str(it.get("raw") or "")
        kv = it.get("extra") or {}
        if not raw:
            continue
        sets, vals = [], []
        for k, v in kv.items():
            if k not in allowed:
                unknown.add(k)
                continue
            sets.append(f"{qid(k)}=?")
            vals.append(None if v is None else str(v))
        if not sets:
            skipped += 1
            continue
        cur = con.execute(
            f"UPDATE {qid(dim)} SET {','.join(sets)} WHERE {qid(COL_RAW)}=?",
            vals + [raw])
        updated += cur.rowcount
    return {"updated": updated, "skipped": skipped,
            "unknown_cols": sorted(unknown), "columns": extra}


def refresh_task(con, task):
    """源表来了新数据后：把新增取值补进维度表（聚合值=原值）。"""
    dim, vcol = task["dim_table"], task["value_col"]
    if not has_table(con, dim):
        raise ValueError(f"聚合维度表 {dim} 已不存在")
    have = set(columns(con, dim))
    stats = [c for c in _STATS_COLS if c in have]
    data = _value_rows(con, task["source_table"], task["source_col"])
    existing = {r[0] for r in con.execute(f"SELECT {qid(COL_RAW)} FROM {qid(dim)}").fetchall()}
    added = []
    for r in data:
        raw = r["v"]
        if raw in existing:
            if COL_ROWS in stats:      # 仅历史表：顺手刷新行数
                con.execute(
                    f"UPDATE {qid(dim)} SET {qid(COL_ROWS)}=? WHERE {qid(COL_RAW)}=?",
                    (r["n"], raw))
        else:
            ins_cols = [COL_RAW, vcol] + stats
            ins_vals = [raw, raw] + [r["n"] if c == COL_ROWS else 1 for c in stats]
            con.execute(
                f"INSERT INTO {qid(dim)} ({','.join(qid(c) for c in ins_cols)}) "
                f"VALUES ({','.join('?' * len(ins_cols))})", ins_vals)
            added.append(raw)
    if COL_VARIANTS in stats:
        con.execute(
            f"UPDATE {qid(dim)} SET {qid(COL_VARIANTS)}=("
            f"SELECT COUNT(*) FROM {qid(dim)} d2 WHERE d2.{qid(vcol)}={qid(dim)}.{qid(vcol)})")
    con.execute("UPDATE agg_task SET updated_at=? WHERE id=?",
                (time.strftime("%Y-%m-%d %H:%M:%S"), task["id"]))
    return {"added": added, "added_count": len(added),
            "source_values": len(data), "stats": dim_stats(con, dim, COL_RAW, vcol)}


def drop_stats_columns(con, dim_table=None):
    """把历史聚合维度表里的「源行数 / 写法数」列删掉（新表本来就不建）。

    幂等：没有这两列就什么都不做。返回 [{table, dropped}]。
    """
    if dim_table:
        targets = [dim_table]
    else:
        try:
            targets = [r[0] for r in con.execute("SELECT dim_table FROM agg_task")]
        except sqlite3.Error:
            return []
    done = []
    for t in targets:
        if not t or not has_table(con, t):
            continue
        cols = columns(con, t)
        drop = [c for c in _STATS_COLS if c in cols]
        if not drop:
            continue
        for c in drop:
            con.execute(f"ALTER TABLE {qid(t)} DROP COLUMN {qid(c)}")
        done.append({"table": t, "dropped": drop})
    return done


def pending_values(con):
    """已建聚合表里，源表出现了但维度表还没有的取值（导入新数据后提醒补映射）。

    只做「差集」这一件事，不跑孤儿率统计，避免导入后被拖慢。
    返回 [{task_id, dim_table, name, source_table, source_col,
           new_count, new_rows, samples, values}]
    """
    try:
        tasks = con.execute(
            "SELECT id,name,source_table,source_col,dim_table,value_col "
            "FROM agg_task ORDER BY id").fetchall()
    except sqlite3.Error:
        return []
    out = []
    for t in tasks:
        dim = t["dim_table"]
        if not has_table(con, dim):
            continue
        try:
            exist = {r[0] for r in con.execute(f"SELECT {qid(COL_RAW)} FROM {qid(dim)}")}
            data = _value_rows(con, t["source_table"], t["source_col"])
        except sqlite3.Error:
            continue
        new = [{"raw": r["v"], "rows": r["n"]} for r in data if r["v"] not in exist]
        if not new:
            continue
        out.append({
            "task_id": t["id"], "name": t["name"], "dim_table": dim,
            "source_table": t["source_table"], "source_col": t["source_col"],
            "new_count": len(new), "new_rows": sum(x["rows"] for x in new),
            "samples": [x["raw"] for x in new[:12]],
            "values": new,
        })
    return out


def drop_task(con, task):
    """删除任务：先删关系、删表，再删记录。返回被清理的关系条数。"""
    dim = task["dim_table"]
    cur = con.execute(
        "DELETE FROM map_config WHERE target_table=? AND target_col=?", (dim, COL_RAW))
    rels = cur.rowcount
    if has_table(con, dim):
        con.execute(f"DROP TABLE {qid(dim)}")
    con.execute("DELETE FROM agg_task WHERE id=?", (task["id"],))
    return rels


def relation_conflicts(con, source_table, source_col, dim_table=None):
    """该源列上已有的其它关系（建聚合维度表时提示是否停用）。"""
    try:
        rows = con.execute(
            "SELECT id,source_table,source_col,target_table,target_col FROM map_config "
            "WHERE enabled=1 AND source_table=? AND source_col=? ORDER BY id",
            (source_table, source_col)).fetchall()
    except sqlite3.Error:
        return []
    out = []
    for r in rows:
        if dim_table and r["target_table"] == dim_table:
            continue
        out.append({"id": r["id"], "target_table": r["target_table"],
                    "target_col": r["target_col"]})
    return out
