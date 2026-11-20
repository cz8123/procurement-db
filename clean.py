# -*- coding: utf-8 -*-
"""字段时间的「取值清洗」：把写法不同但含义相同的值归并成一个。

定位（与 transform.py 的分工）
------------------------------
  transform  面向整张表做步骤式加工 → 产出视图/数据表，不改源数据。
  clean      面向单个字段做「值级」清洗 → 直接落到源表，供统计与可视化使用。

为什么需要它
------------
维度表里常存在同一事物的多种写法（空格/下划线/全角括号/中英混排差异），例如
    dim_category.Category   98 个取值里 19 组是重复写法（52 个值）
    dim_subcategory         249 个取值里 60 组是重复写法（164 个值）
关系「孤儿率」为 0 并不代表干净：事实表照抄了这些写法，按类别做可视化时会被
拆成十几个几乎相同的分组。本模块就是把它们合成一个规范值，并把事实表列一起改写，
使统计口径与可视化分组真正对齐。

归并口径
--------
* 判定重复用「规范化键」：NFKC（全角→半角）+ 小写 + 去掉全部空白与标点分隔符。
  只有规范化键完全相同才算重复 —— 宁可漏，不可错（公司名之间相似度常年 0.99，
  用模糊相似度归并会把不同公司合并掉）。
* 规范值默认取「事实表使用行数最多」的那个；使用量相同时取写法更规整的
  （无重复空格、括号配对、中英文之间有分隔、无驼峰粘连），再取较长者。
* 维度列归并会级联改写所有指向它的关系列（map_config），最后才删除多余的维度行，
  保证事实表不会因此产生新的孤儿。
"""

import re
import sqlite3
import unicodedata

# 参与「规范化」的分隔符：空白、常见中英标点。规范化时整串去掉，
# 因此 "Used Car-二手车部" 与 "UsedCar_二手车部" 会落到同一个键上。
_SEP = re.compile(
    r"[\s\-_/\\|·.,、，。()（）\[\]【】{}<>&+'\"“”‘’~!@#$%^*=+:;?！？；：…—－]+"
)

_ANOMALY_CJK_LATIN = re.compile(r"[\u4e00-\u9fa5][A-Za-z]{2,}")
_ANOMALY_LATIN_CJK = re.compile(r"[A-Za-z]{2,}[\u4e00-\u9fa5]")
_ANOMALY_CAMEL = re.compile(r"[a-z][A-Z]")
# 英文长串无分隔：foodandbeverages、Professionalconsultingservices —— 多半是丢了空格
_ANOMALY_LATIN_LONG = re.compile(r"(?<![A-Za-z])[A-Za-z]{15,}(?![A-Za-z])")
# 连字符/斜杠后多一个空格再接中文：「B部分豁免- 集团相关」这种中文里不该出现的排版
_ANOMALY_DASH_SPACE_CJK = re.compile(r"[-–—/]\s+[\u4e00-\u9fa5]")
# 连字符前多一个空格再接中文：「Administrative Materials -行政类物资」——中文只紧贴连字符
_ANOMALY_SPACE_DASH_CJK = re.compile(r"\s+[-–—]\s*[\u4e00-\u9fa5]")
# 反过来的「中文 + 空格 + 连字符」：「售后类服务 -Aftersales Service」
_ANOMALY_CJK_SPACE_DASH = re.compile(r"[\u4e00-\u9fa5]\s+[-–—]")


def qid(name):
    """SQL 标识符转义（与 app.db.qid 同规则，避免依赖 app 造成循环导入）。"""
    return '"' + str(name).replace('"', '""') + '"'


def norm_key(value):
    """规范化键：全角转半角 + 小写 + 去空白与标点。同键即视为同一事物的不同写法。"""
    if value is None:
        return ""
    text = unicodedata.normalize("NFKC", str(value)).lower()
    return _SEP.sub("", text)


def ugliness(value):
    """写法「不规整」程度，越小越适合当规范值。

    只用于「同一规范键内挑一个代表写法」，不影响是否合并。
    """
    text = "" if value is None else str(value)
    score = 0
    if "  " in text:
        score += 2                                   # 连续空格
    if text.count("(") != text.count(")") or text.count("（") != text.count("）"):
        score += 3                                   # 括号不配对（常见于截断）
    if _ANOMALY_LATIN_CJK.search(text) or _ANOMALY_CJK_LATIN.search(text):
        score += 2                                   # 英文单词与中文之间没有分隔
    if _ANOMALY_LATIN_LONG.search(text):
        score += 3                                   # 英文单词粘连（丢了空格），比中英粘连更离谱
    if _ANOMALY_DASH_SPACE_CJK.search(text):
        score += 2                                   # 中文里的「- 」
    if _ANOMALY_SPACE_DASH_CJK.search(text):
        score += 1                                   # 中文前的「 -」
    if _ANOMALY_CJK_SPACE_DASH.search(text):
        score += 1                                   # 中文后的「- 」
    if _ANOMALY_CAMEL.search(text):
        score += 1                                   # 单词粘连成驼峰
    return score


def cascade_targets(con, table, col):
    """指向该维度列的其它表列（用于级联改写）。"""
    try:
        rows = con.execute(
            "SELECT source_table, source_col FROM map_config "
            "WHERE target_table=? AND target_col=? AND enabled=1 AND source_table<>? "
            "ORDER BY id", (table, col, table)).fetchall()
        return [(r[0], r[1]) for r in rows]
    except sqlite3.Error:
        return []


def _chunks(seq, size):
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


def _usage(con, targets, values):
    """统计每个取值在各引用列中的总行数（用于挑选规范值与展示影响面）。"""
    usage = {}
    for table, col in targets:
        try:
            for chunk in _chunks(values, 400):
                ph = ",".join("?" * len(chunk))
                sql = (f"SELECT CAST({qid(col)} AS TEXT) AS v, COUNT(*) AS n "
                       f"FROM {qid(table)} WHERE {qid(col)} IN ({ph}) GROUP BY 1")
                for v, n in con.execute(sql, chunk):
                    usage[v] = usage.get(v, 0) + n
        except sqlite3.Error:
            continue
    return usage


def _columns(con, table):
    return [r[1] for r in con.execute(f"PRAGMA table_info({qid(table)})").fetchall()]


def _dim_relation(con, table, col):
    """事实列 → 维度表的关系（若配置过）。"""
    try:
        row = con.execute(
            "SELECT target_table, target_col FROM map_config "
            "WHERE source_table=? AND source_col=? AND enabled=1 "
            "AND target_table GLOB 'dim_*' ORDER BY id LIMIT 1",
            (table, col)).fetchone()
    except sqlite3.Error:
        return None
    return (row[0], row[1]) if row else None


def value_profile(con, table, col, limit=2000):
    """扫描一个字段的全部取值，按规范化键自动分组，返回可执行的归并建议。

    返回结构::

        {table, col, is_dim, total_values, total_rows,
         groups: [{key, canonical, rows, members: [{value, rows, usage, missing}], anomaly}],
         singles: [{value, rows, missing}],
         cascade: [{table, col, rows}],
         dim: {table, col} | None,
         truncated: bool}
    """
    if col not in _columns(con, table):
        raise ValueError(f"列不存在：{table}.{col}")

    is_dim = str(table).startswith("dim_")
    rows = con.execute(
        f"SELECT CAST({qid(col)} AS TEXT) AS v, COUNT(*) AS n FROM {qid(table)} "
        f"WHERE {qid(col)} IS NOT NULL AND TRIM(CAST({qid(col)} AS TEXT)) <> '' "
        f"GROUP BY 1 ORDER BY n DESC, v").fetchall()

    values = [r[0] for r in rows]
    counts = {r[0]: r[1] for r in rows}

    # 维度列 → 统计它在事实表里的使用量；事实列 → 使用量就是自身行数
    targets = cascade_targets(con, table, col) if is_dim else []
    usage = _usage(con, targets, values) if targets else {}
    if not is_dim:
        for v in values:
            usage.setdefault(v, counts.get(v, 0))
            usage[v] = max(usage[v], counts.get(v, 0))

    # 事实列：标出哪些取值在维度表里找不到（与「关系健康度」同口径）
    dim_rel = None if is_dim else _dim_relation(con, table, col)
    dim_values = set()
    if dim_rel:
        try:
            dim_values = {r[0] for r in con.execute(
                f"SELECT CAST({qid(dim_rel[1])} AS TEXT) FROM {qid(dim_rel[0])}")}
        except sqlite3.Error:
            dim_rel = None

    buckets = {}
    for value in values:
        key = norm_key(value)
        if not key:
            continue
        buckets.setdefault(key, []).append(value)

    groups, singles = [], []
    for key, members in buckets.items():
        if len(members) < 2:
            v = members[0]
            singles.append({"value": v, "rows": counts.get(v, 0),
                            "missing": bool(dim_rel) and v not in dim_values})
            continue
        items = []
        for v in members:
            items.append({
                "value": v,
                "rows": counts.get(v, 0),
                "usage": usage.get(v, 0),
                "missing": bool(dim_rel) and v not in dim_values,
            })
        # 规范值：写法最规整优先（避免选中「双空格」「括号残缺」的写法），其次看使用量，
        # 再取较长者。使用量只作并列时的裁决，不压过写法规整度。
        items.sort(key=lambda m: (ugliness(m["value"]), -m["usage"],
                                  -len(str(m["value"])), str(m["value"])))
        canonical = items[0]["value"]
        groups.append({
            "key": key,
            "canonical": canonical,
            "rows": sum(m["rows"] for m in items),
            "members": items,
        })

    groups.sort(key=lambda g: (-g["rows"], g["canonical"]))
    singles.sort(key=lambda s: (-s["rows"], s["value"]))

    cascade = []
    for t, c in targets:
        try:
            n = con.execute(
                f"SELECT COUNT(*) FROM {qid(t)} WHERE {qid(c)} IS NOT NULL "
                f"AND TRIM(CAST({qid(c)} AS TEXT)) <> ''").fetchone()[0]
        except sqlite3.Error:
            continue
        cascade.append({"table": t, "col": c, "rows": n})

    shown_groups = groups
    shown_singles = singles[:limit]
    total_values = len(values)
    # 归并后还剩几个取值：每组 n 个值合成 1 个
    after_values = total_values - sum(len(g["members"]) - 1 for g in groups)

    return {
        "table": table, "col": col, "is_dim": is_dim,
        "total_values": total_values,
        "total_rows": sum(counts.values()),
        "group_count": len(groups),
        "grouped_values": sum(len(g["members"]) for g in groups),
        "after_values": after_values,
        "groups": shown_groups,
        "singles": shown_singles,
        "singles_total": len(singles),
        "truncated": len(singles) > len(shown_singles),
        "cascade": cascade,
        "dim": {"table": dim_rel[0], "col": dim_rel[1]} if dim_rel else None,
        "missing_values": sum(1 for s in singles if s["missing"])
                          + sum(1 for g in groups for m in g["members"] if m["missing"]),
    }


def _distinct(con, table, col):
    return con.execute(
        f"SELECT COUNT(DISTINCT {qid(col)}) FROM {qid(table)} "
        f"WHERE {qid(col)} IS NOT NULL AND TRIM(CAST({qid(col)} AS TEXT)) <> ''"
    ).fetchone()[0]


def scan_cascade(con, table, col, exclude_tables=(), max_values=600,
                 allow_same_name=False):
    """深度扫描：找出还有哪些表列装着这个字段的取值。

    仅当 map_config 里没有指向该列的显式关系时使用（1~2 秒），
    结果交给用户勾选是否一起改写，避免「维度洗干净了、事实表却变孤儿」。

    allow_same_name=True 时不跳过「列名相同」的候选（默认跳过）。清洗场景下同名列
    往往就是源列本身，跳过可省一次全表扫；但「映射聚合」要找的正是别的表里
    同名的那一列（如 fct_prpo.部门 ↔ fct_po.部门），必须允许。
    """
    try:
        values = [r[0] for r in con.execute(
            f"SELECT DISTINCT CAST({qid(col)} AS TEXT) FROM {qid(table)} "
            f"WHERE {qid(col)} IS NOT NULL AND TRIM(CAST({qid(col)} AS TEXT)) <> '' "
            f"LIMIT ?", (max_values,))]
    except sqlite3.Error:
        return []
    if not values:
        return []

    skip = set(exclude_tables) | {table}
    cands = []
    for t in [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]:
        if t in skip:
            continue
        # 聚合维度表是「映射聚合」的产出物，永远不是被引用的原始列
        if t.startswith("dim_agg_"):
            continue
        try:
            for r in con.execute(f"PRAGMA table_info({qid(t)})"):
                name, decl, is_pk = r[1], (r[2] or "").upper(), r[5]
                if is_pk:
                    continue
                if name == col and not allow_same_name:
                    continue
                if decl and not any(k in decl for k in ("TEXT", "CHAR", "CLOB")):
                    continue
                cands.append((t, name))
        except sqlite3.Error:
            continue

    hits = []
    for t, c in cands:
        try:
            # 先用第一个值快速排除（大多数列一次全表扫就走完，命中才会继续）
            quick = con.execute(
                f"SELECT COUNT(*) FROM {qid(t)} WHERE {qid(c)} = ?", (values[0],)).fetchone()[0]
            if not quick:
                continue
            ph = ",".join("?" * len(values))
            n = con.execute(
                f"SELECT COUNT(*) FROM {qid(t)} WHERE {qid(c)} IN ({ph})", values).fetchone()[0]
            if n:
                hits.append({"table": t, "col": c, "rows": n})
        except sqlite3.Error:
            continue
    hits.sort(key=lambda h: -h["rows"])
    return hits


def apply_merge(con, table, col, groups, cascade=True, extra_cascade=()):
    """执行值归并。groups = [{to, from:[...]}]

    维度列：先级联改写引用列（map_config + extra_cascade），再删除多余的维度行。
    事实列：直接改写该列；目标值必须存在于对应维度表，否则会把一种缺失换成另一种。
    """
    if col not in _columns(con, table):
        raise ValueError(f"列不存在：{table}.{col}")
    is_dim = str(table).startswith("dim_")
    targets = []
    if cascade and is_dim:
        targets = cascade_targets(con, table, col)
        for pair in (extra_cascade or []):
            try:
                t, c = pair[0], pair[1]
            except (TypeError, IndexError):
                continue
            if t and c and t != table and (t, c) not in targets:
                targets.append((t, c))
    dim_rel = None if is_dim else _dim_relation(con, table, col)

    before_values = _distinct(con, table, col)
    updated, deleted, done_groups, merged_values = {}, 0, 0, 0

    for g in groups or []:
        to = ("" if g.get("to") is None else str(g["to"]))
        frm = [str(x) for x in (g.get("from") or []) if x is not None]
        frm = [x for x in dict.fromkeys(frm) if x != to]
        if not to or not frm:
            continue

        # 目标值是否存在
        exists = con.execute(
            f"SELECT 1 FROM {qid(table)} WHERE {qid(col)} = ? LIMIT 1", (to,)).fetchone()
        if not exists:
            if not is_dim:
                raise ValueError(f"目标值「{to}」不在 {table} 中，无法归并")
            con.execute(f"INSERT INTO {qid(table)} ({qid(col)}) VALUES (?)", (to,))
        if dim_rel:
            hit = con.execute(
                f"SELECT 1 FROM {qid(dim_rel[0])} WHERE {qid(dim_rel[1])} = ? LIMIT 1",
                (to,)).fetchone()
            if not hit:
                raise ValueError(
                    f"目标值「{to}」不在维度表 {dim_rel[0]}.{dim_rel[1]} 中，"
                    f"归并后仍是缺失值；请先补录该维度成员")

        ph = ",".join("?" * len(frm))
        # ① 级联：把所有引用该维度列的事实列一起改写
        for t, c in targets:
            try:
                cur = con.execute(
                    f"UPDATE {qid(t)} SET {qid(c)} = ? WHERE {qid(c)} IN ({ph})",
                    [to] + frm)
                if cur.rowcount > 0:
                    updated[t] = updated.get(t, 0) + cur.rowcount
            except sqlite3.Error:
                continue

        # ② 本列：维度删行 / 事实改写
        if is_dim:
            cur = con.execute(f"DELETE FROM {qid(table)} WHERE {qid(col)} IN ({ph})", frm)
            deleted += max(0, cur.rowcount)
        else:
            cur = con.execute(
                f"UPDATE {qid(table)} SET {qid(col)} = ? WHERE {qid(col)} IN ({ph})",
                [to] + frm)
            if cur.rowcount > 0:
                updated[table] = updated.get(table, 0) + cur.rowcount

        done_groups += 1
        merged_values += len(frm)

    after_values = _distinct(con, table, col)
    return {
        "ok": True, "groups": done_groups, "merged_values": merged_values,
        "updated": updated, "updated_rows": sum(updated.values()),
        "deleted_rows": deleted,
        "before_values": before_values, "after_values": after_values,
        "cascade": [{"table": t, "col": c} for t, c in targets],
    }
