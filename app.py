# -*- coding: utf-8 -*-
"""
采购数据库 Web 管理页面 (Flask + SQLite)
=========================================
功能：
  1) 浏览 16 张表的数据（分页 / 搜索 / 排序）
  2) 对数据行进行 新增 / 编辑 / 删除
  3) 导入 CSV 到指定表（复用 db_manager 的「追加新数据、跳过重复」逻辑）

依赖：Flask（隔离 venv 中）
启动：start-dbadmin.bat 或 `python app.py`（默认绑定 0.0.0.0:5000，启动后自动打开浏览器访问 http://localhost:5000；设 BROWSER=0 关闭自动打开）
"""
import os
import re
import csv
import io
import json
import time
import threading
import webbrowser
import sqlite3
import urllib.parse
from difflib import SequenceMatcher

from flask import Flask, request, jsonify, render_template, Response, send_file, make_response
from werkzeug.serving import make_server

import db_manager as db

# 持有服务器对象，供「终止服务」接口调用
SERVER = None

DB_PATH = db.DB_PATH
KNOWN_TABLES = {t for t, _, _ in db.FILE_MAP.values()}
# 单独导入维护的维度表（非 FILE_MAP 批量导入来源），需在此显式注册
KNOWN_TABLES.add("fct_company_location")

# 表名 -> (中文简称, 一句话说明)  —— 供前端展示与检索使用
TABLE_LABELS = {
    # 维度 / 主数据表
    "dim_department": ("部门", "部门 / 成本中心主数据"),
    "dim_procurement_method": ("采购方法", "采购方式（报价采购、招标等）"),
    "dim_category": ("采购类别", "一级采购类别"),
    "dim_subcategory": ("采购子类别", "二级采购子类别"),
    "dim_company": ("公司", "公司主体主数据"),
    "dim_supplier_attribute": ("供应商属性", "供应商分类（A 完全豁免 / B / C）"),
    "dim_supplier_attribute_detail": ("供应商属性明细", "供应商属性明细项"),
    "dim_exception_reason": ("豁免理由", "单一来源 / 豁免理由字典"),
    "dim_expense_type": ("支出类型", "支出类型（CAPEX / OPEX 等）"),
    # 事实 / 交易表
    "fct_supplier": ("供应商列表", "供应商准入 / 资格提交记录（事实表）"),
    "fct_preapproval": ("集采前置审批", "集采项目前置审批单"),
    "fct_framework_agreement": ("框架协议", "框架合同 / 协议"),
    "fct_bom": ("物料清单", "框架协议下的物料明细"),
    "fct_prpo": ("采购申请 PRPO", "采购申请单（PR / PO 一体表）"),
    "fct_po": ("采购订单 PO", "采购订单（含金额，数据量最大）"),
    "fct_company_location": ("公司位置(经纬度)", "公司门店中文名 → 省/市/区经纬度（地理事实表）"),
}


def table_label(table):
    """返回表的中文简称；未登记时回退为原表名。"""
    return TABLE_LABELS.get(table, (table, ""))[0]


def table_desc(table):
    return TABLE_LABELS.get(table, (table, ""))[1]


# ---------------------------------------------------------------------------
# 关系补录建议：(事实表, 事实列) -> (等级, 原因)
#   等级 recommend=建议补录 / optional=按需 / not_needed=无需补录
#   未登记的关系走默认规则：目标为 dim_* → recommend；目标为 fct_* → not_needed
# 原因文案会直接展示给用户，说明"为什么建议/为什么不建议"。
# ---------------------------------------------------------------------------
RELATION_ADVICE = {
    # —— 建议补录：维度主数据缺成员，补齐后才能完整按该维度分析 ——
    ("fct_po", "部门"): ("recommend",
        "「部门」是核心分析维度，缺失会让按部门汇总出现空白行。源数据里存在少量部门名变种"
        "（如 Sales-销售部_RR），补录后即可完整统计。"),
    ("fct_prpo", "部门"): ("recommend",
        "同 PO：「部门」缺失会影响按部门的口径统计，且多为名称变种，建议补录。"),
    ("fct_prpo", "豁免单一来源的理由"): ("recommend",
        "属于字典项缺失。补录理由字典能让「单一来源采购占比」等分析不漏项。"),
    # —— 无需补录：事实表之间的引用缺口，多为正常业务，补录会产生假数据 ——
    ("fct_prpo", "框架合同编号"): ("not_needed",
        "约半数 PRPO 未挂框架协议，多为**单一来源 / 直接采购**的正常业务形态；"
        "补录相当于凭空造一个框架合同号，会污染事实表，故不建议。"),
    ("fct_po", "已批准的采购申请参考"): ("not_needed",
        "PO 引用的 PRPO 单可能未纳入本次导出或属历史单据，属数据范围问题；"
        "往 PRPO 表补录会伪造采购申请单，不建议。"),
    ("fct_bom", "合同编号CN"): ("not_needed",
        "物料清单本就依附于框架协议，缺失说明源数据不完整。补录框架号不能恢复物料归属，"
        "应回到源系统核对，不建议在此补录。"),
    ("fct_framework_agreement", "前置审批单号"): ("not_needed",
        "前置审批单可能未纳入导出范围，属正常的范围差异，按需即可。"),
    ("fct_preapproval", "采购类别"): ("optional",
        "若缺失较多说明类别主数据不全，可按需补录；少量缺失不影响整体分析。"),
}


# ---------------------------------------------------------------------------
# 缺失值 → 现存维度值 的相似度建议（仅供参考，绝不自动应用）
#   匹配思路：去掉分隔符后整体相似度 + 分词 Jaccard + 子串包含 + 首词前缀加成
# ---------------------------------------------------------------------------
_SPLIT_RE = re.compile(r"[\s_\-—–·,，.。()（）\[\]【】/\\]+")


def _norm_txt(s):
    return _SPLIT_RE.sub("", ("" if s is None else str(s)).lower())


def _tokens(s):
    return [t for t in _SPLIT_RE.split(("" if s is None else str(s)).lower()) if t]


def key_similarity(a, b):
    """0~1 的相似度；1 = 几乎同一个值（仅比较名称本身，不用缩写列，避免误报）。"""
    na, nb = _norm_txt(a), _norm_txt(b)
    if not na or not nb:
        return 0.0
    best = SequenceMatcher(None, na, nb).ratio()          # 去分隔符后整体相似度
    ta, tb = set(_tokens(a)), set(_tokens(b))
    if ta or tb:
        best = max(best, len(ta & tb) / len(ta | tb))     # 分词 Jaccard
    # 一方完整包含另一方（且长度相当，避免 "Sales" 命中 "Point of Sales…" 这类短词误报）
    short, long_ = (na, nb) if len(na) <= len(nb) else (nb, na)
    if short in long_ and len(short) / len(long_) >= 0.6:
        best = max(best, 0.9)
    ta2, tb2 = _tokens(a), _tokens(b)
    if (ta2 and tb2 and len(ta2[0]) >= 3 and len(tb2[0]) >= 3
            and (ta2[0].startswith(tb2[0]) or tb2[0].startswith(ta2[0]))):
        best = min(1.0, best + 0.08)                      # 首词前缀相同（如 Admin/Administration）
    return best


def _head_word(s):
    t = _tokens(s)
    return t[0] if t else ""


def suggest_targets(con, rt, rc, keys, threshold=0.62):
    """为每个缺失值推荐一个最相近的现存维度值。返回 {缺失值: {value, score, confidence}}"""
    if not keys:
        return {}
    cols = [r[1] for r in con.execute(f"PRAGMA table_info({db.qid(rt)})").fetchall()]
    if rc not in cols:
        return {}
    if con.execute(f"SELECT COUNT(*) FROM {db.qid(rt)}").fetchone()[0] > 3000 or len(keys) > 800:
        return {}          # 维度表过大则不计算，避免拖慢页面
    rows = [r[0] for r in con.execute(
        f"SELECT {db.qid(rc)} FROM {db.qid(rt)} WHERE {db.qid(rc)} IS NOT NULL").fetchall()]
    out = {}
    for k in keys:
        best_v, best_s = None, 0.0
        for v in rows:
            s = key_similarity(k, v)
            if s > best_s:
                best_s, best_v = s, v
        if best_v is not None and best_s >= threshold:
            # 中置信度必须「首词一致」：避免仅因共享品牌词（如 BMW）把 Admin 误归并到 Marketing
            if best_s >= 0.80 or _head_word(k) == _head_word(best_v):
                out[k] = {"value": best_v, "score": round(best_s, 2),
                          "confidence": "high" if best_s >= 0.80 else "mid"}
    return out


def dim_extra_cols(con, rt, rc):
    """维度表的「附带字段」：除主键、关联列、自增 id 之外的列。

    如 dim_department 的 DepartmentEn（Admin / Aftersales / Sales …）——
    新建维度成员时应一并写入，否则该成员在按分类汇总时会落在空白组。
    """
    out = []
    for r in con.execute(f"PRAGMA table_info({db.qid(rt)})").fetchall():
        # PRAGMA table_info 列序：cid, name, type, notnull, dflt_value, pk
        name, is_pk = r[1], r[5]
        if is_pk or name == rc or str(name).lower() == "id":
            continue
        out.append(name)
    return out


def suggest_extras(con, rt, rc, extra_cols, keys, max_rows=3000):
    """为每条缺失值推荐「新建成员时该填的附带字段值」。

    对每个附带字段独立判断，两步：
      1) 前缀命中：缺失值开头的英文词（先试前 2 个词，再试第 1 个词）在该字段现有取值里能找到
         → 直接取用（如 Marketing-MINI-市场部-Mini → DepartmentEn=Marketing）
      2) 近邻复制：否则取与缺失值最相似的现存成员，复制它的该字段值
         （如 Head Office-Business Development-Property-… → 参考 …-DD-… → BD）
    返回 {缺失值: {字段: 值}}；仅返回确有把握的字段。
    """
    if not keys or not extra_cols:
        return {}
    n = con.execute(f"SELECT COUNT(*) FROM {db.qid(rt)}").fetchone()[0]
    if n > max_rows:
        return {}
    sel = [rc] + extra_cols
    rows = con.execute(
        f"SELECT {','.join(db.qid(c) for c in sel)} FROM {db.qid(rt)}"
    ).fetchall()
    if not rows:
        return {}

    # 每个附带字段的「取值小写 -> 原值」池（按列序号取值，不依赖 row_factory）
    pool = {}
    for j, c in enumerate(extra_cols, start=1):
        m = {}
        for r in rows:
            v = r[j]
            if v is not None and str(v).strip() != "":
                m.setdefault(str(v).strip().lower(), str(v).strip())
        pool[c] = m

    out = {}
    for k in keys:
        toks = _tokens(k)
        cands = []
        if len(toks) >= 2:
            cands.append(" ".join(toks[:2]))
        if toks:
            cands.append(toks[0])
        cands.append(str(k).strip().lower())

        best_row, best_s = None, 0.0
        for r in rows:
            s = key_similarity(k, r[0])
            if s > best_s:
                best_s, best_row = s, r
        near_ok = best_row is not None and best_s >= 0.55

        picked = {}
        for j, c in enumerate(extra_cols, start=1):
            v = None
            for cand in cands:
                if not cand:
                    continue
                hit = pool[c].get(cand)
                if hit is None:
                    hit = pool[c].get(cand.replace(" ", "_"))
                if hit is not None:
                    v = hit
                    break
            if v is None and near_ok:
                bv = best_row[j]
                if bv is not None and str(bv).strip() != "":
                    v = str(bv).strip()
            if v:
                picked[c] = v
        if picked:
            out[k] = picked
    return out


def relation_advice(fact, fact_col, dim):
    """返回 (等级, 原因)。优先查登记表，否则按目标表类型给默认建议。"""
    if (fact, fact_col) in RELATION_ADVICE:
        return RELATION_ADVICE[(fact, fact_col)]
    if dim.startswith("dim_"):
        return ("recommend",
                "目标为维度表：缺失即维度主数据不全，补录后关系完整、分析不漏项。")
    return ("not_needed",
            "目标为事实表：事实表之间多为「业务上本就不引用」的正常缺口，不建议补录。")


app = Flask(__name__, template_folder="templates", static_folder="static")
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024  # 上传上限 50MB


def get_con():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


# 确保元数据表（映射配置 / 质量规则）存在（幂等，首次运行播种 15 条映射）。
# 注意：必须放在 get_con 定义之后，否则会 NameError 被静默吞掉。
try:
    _mc = get_con(); db.ensure_meta_tables(_mc); _mc.close()
except Exception:
    pass


def get_pk(table, con):
    for r in con.execute(f"PRAGMA table_info({db.qid(table)})").fetchall():
        if r["pk"]:
            return r["name"]
    return None


def _table_meta():
    out = []
    con = get_con()
    seen = set()
    # 先按 FILE_MAP 顺序输出 15 张核心表
    for fn in db.LOAD_ORDER:
        t, pk, kind = db.FILE_MAP[fn]
        out.append({"table": t, "source": fn, "kind": kind, "pk": pk})
        seen.add(t)
    # 补充 KNOWN_TABLES 中手动导入的额外表（如 fct_company_location）：按 fct_/dim_ 前缀判定 fact/dim
    for t in sorted(KNOWN_TABLES - seen):
        pk = None
        for r in con.execute(f"PRAGMA table_info({db.qid(t)})").fetchall():
            if r["pk"]:
                pk = r["name"]
                break
        tkind = "fact" if t.startswith("fct_") else "dim"
        out.append({"table": t, "source": "(手动导入表)", "kind": tkind, "pk": pk})
    con.close()
    return out


# ---------------------------------------------------------------------------
# 页面
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    # 禁用浏览器缓存首页 HTML：避免用户一直加载旧的 index.html（滚动修复等前端改动即时生效，无需手动清缓存）
    resp = make_response(render_template("index.html"))
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp


@app.route("/favicon.ico")
def favicon():
    icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "procurement.ico")
    if os.path.exists(icon_path):
        return send_file(icon_path, mimetype="image/x-icon")
    return ("", 204)


# ---------------------------------------------------------------------------
# 表列表
# ---------------------------------------------------------------------------
@app.route("/api/tables")
def api_tables():
    con = get_con()
    res = []
    for item in _table_meta():
        t = item["table"]
        try:
            n = con.execute(f"SELECT COUNT(*) FROM {db.qid(t)}").fetchone()[0]
        except sqlite3.Error:
            n = None
        res.append({"table": t, "source": item["source"], "kind": item["kind"],
                    "pk": item["pk"], "rows": n,
                    "label": table_label(t), "desc": table_desc(t)})
    con.close()
    return jsonify(res)


# ---------------------------------------------------------------------------
# 表结构
# ---------------------------------------------------------------------------
@app.route("/api/tables/<table>/schema")
def api_schema(table):
    if table not in KNOWN_TABLES:
        return jsonify({"error": "未知表"}), 404
    con = get_con()
    cols = []
    for r in con.execute(f"PRAGMA table_info({db.qid(table)})").fetchall():
        cols.append({"name": r["name"], "type": r["type"], "pk": r["pk"],
                     "notnull": r["notnull"]})
    con.close()
    return jsonify(cols)


# ---------------------------------------------------------------------------
# 数据浏览（分页 / 搜索 / 排序 / 列筛选）
# ---------------------------------------------------------------------------
_NUM_OPS = {"gt": ">", "gte": ">=", "lt": "<", "lte": "<="}


def _build_where(cols, coltype, search, filters):
    """构造 WHERE 子句：全局搜索(OR) 与 列筛选(AND) 组合。"""
    clauses, params = [], []
    if search:
        likes = []
        for c in cols:
            likes.append(f"{db.qid(c)} LIKE ?")
            params.append(f"%{search}%")
        clauses.append("(" + " OR ".join(likes) + ")")
    for f in (filters or []):
        col = f.get("col"); op = f.get("op"); val = f.get("val")
        if col not in cols or not op:
            continue
        qc = db.qid(col)
        is_num = (coltype.get(col) == "REAL")
        if op == "empty":
            clauses.append(f"({qc} IS NULL OR {qc} = '')")
        elif op == "notempty":
            clauses.append(f"({qc} IS NOT NULL AND {qc} != '')")
        elif op == "contains":
            clauses.append(f"{qc} LIKE ?"); params.append(f"%{val}%")
        elif op == "startswith":
            clauses.append(f"{qc} LIKE ?"); params.append(f"{val}%")
        elif op in ("eq", "ne"):
            if is_num:
                try:
                    v = float(val)
                except (TypeError, ValueError):
                    continue
                clauses.append(f"CAST({qc} AS REAL) {'=' if op == 'eq' else '!='} ?"); params.append(v)
            else:
                clauses.append(f"{qc} {'=' if op == 'eq' else '!='} ?"); params.append(val)
        elif op in _NUM_OPS:
            try:
                v = float(val)
            except (TypeError, ValueError):
                continue
            clauses.append(f"CAST({qc} AS REAL) {_NUM_OPS[op]} ?"); params.append(v)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    return where, params


@app.route("/api/tables/<table>/rows")
def api_rows(table):
    if table not in KNOWN_TABLES:
        return jsonify({"error": "未知表"}), 404
    con = get_con()
    try:
        page = max(1, int(request.args.get("page", 1)))
        size = max(1, min(200, int(request.args.get("size", 50))))
        search = (request.args.get("search", "") or "").strip()
        sort = request.args.get("sort", "")
        order = "desc" if (request.args.get("order", "asc").lower() == "desc") else "asc"
        try:
            filters = json.loads(request.args.get("filters", "[]") or "[]")
        except (ValueError, TypeError):
            filters = []

        info = con.execute(f"PRAGMA table_info({db.qid(table)})").fetchall()
        cols = [r["name"] for r in info]
        coltype = {r["name"]: r["type"] for r in info}

        where, params = _build_where(cols, coltype, search, filters)
        total = con.execute(f"SELECT COUNT(*) FROM {db.qid(table)}{where}", params).fetchone()[0]

        order_sql = ""
        if sort and sort in cols:
            order_sql = f" ORDER BY {db.qid(sort)} {'DESC' if order == 'desc' else 'ASC'}"

        offset = (page - 1) * size
        sql = f"SELECT * FROM {db.qid(table)}{where}{order_sql} LIMIT ? OFFSET ?"
        rows = [dict(r) for r in con.execute(sql, params + [size, offset]).fetchall()]

        # 数字列汇总（对当前筛选后的全集计算，非仅本页）
        num_cols = [c for c in cols if coltype.get(c) == "REAL"]
        aggregates = {}
        if num_cols:
            parts = []
            for i, c in enumerate(num_cols):
                qc = db.qid(c)
                parts.append(f"SUM(CAST({qc} AS REAL)) AS s{i}")
                parts.append(f"AVG(CAST({qc} AS REAL)) AS a{i}")
                parts.append(f"MIN(CAST({qc} AS REAL)) AS mn{i}")
                parts.append(f"MAX(CAST({qc} AS REAL)) AS mx{i}")
                parts.append(f"COUNT({qc}) AS n{i}")
            arow = con.execute(
                f"SELECT {', '.join(parts)} FROM {db.qid(table)}{where}", params).fetchone()
            for i, c in enumerate(num_cols):
                aggregates[c] = {"sum": arow[f"s{i}"], "avg": arow[f"a{i}"],
                                 "min": arow[f"mn{i}"], "max": arow[f"mx{i}"], "count": arow[f"n{i}"]}
    except sqlite3.Error as e:
        con.close()
        return jsonify({"error": str(e)}), 500
    con.close()
    return jsonify({"rows": rows, "total": total, "page": page, "size": size,
                    "columns": cols, "aggregates": aggregates})


# ---------------------------------------------------------------------------
# 导出当前视图为 CSV（应用当前的搜索 / 列筛选 / 排序 / 列选择）
# ---------------------------------------------------------------------------
@app.route("/api/tables/<table>/export")
def api_export(table):
    if table not in KNOWN_TABLES:
        return jsonify({"error": "未知表"}), 404
    con = get_con()
    try:
        search = (request.args.get("search", "") or "").strip()
        sort = request.args.get("sort", "")
        order = "desc" if (request.args.get("order", "asc").lower() == "desc") else "asc"
        try:
            filters = json.loads(request.args.get("filters", "[]") or "[]")
        except (ValueError, TypeError):
            filters = []

        info = con.execute(f"PRAGMA table_info({db.qid(table)})").fetchall()
        cols = [r["name"] for r in info]
        coltype = {r["name"]: r["type"] for r in info}

        want = [c for c in (request.args.get("cols", "") or "").split(",") if c]
        export_cols = [c for c in cols if c in want] if want else list(cols)
        if not export_cols:
            export_cols = list(cols)

        where, params = _build_where(cols, coltype, search, filters)
        order_sql = f" ORDER BY {db.qid(sort)} {'DESC' if order == 'desc' else 'ASC'}" if sort in cols else ""
        col_list = ",".join(db.qid(c) for c in export_cols)
        rows = con.execute(
            f"SELECT {col_list} FROM {db.qid(table)}{where}{order_sql}", params).fetchall()

        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(export_cols)
        for r in rows:
            w.writerow([r[c] for c in export_cols])
        data = ("\ufeff" + buf.getvalue()).encode("utf-8")
        ascii_name = f"{table}_export.csv"
        utf8_name = urllib.parse.quote(f"{table_label(table)}_{table}_导出.csv")
        return Response(
            data, mimetype="text/csv",
            headers={"Content-Disposition":
                     f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{utf8_name}"})
    finally:
        con.close()


# ---------------------------------------------------------------------------
# 新增一行
# ---------------------------------------------------------------------------
@app.route("/api/tables/<table>/rows", methods=["POST"])
def api_insert(table):
    if table not in KNOWN_TABLES:
        return jsonify({"ok": False, "error": "未知表"}), 404
    payload = request.get_json(force=True, silent=True) or {}
    con = get_con()
    cols = [r["name"] for r in con.execute(f"PRAGMA table_info({db.qid(table)})").fetchall()]
    keys = [k for k in payload.keys() if k in cols]
    if not keys:
        con.close()
        return jsonify({"ok": False, "error": "无有效字段"}), 400
    vals = [None if (isinstance(payload.get(k), str) and payload.get(k).strip() == "")
            else payload.get(k) for k in keys]
    col_list = ",".join(db.qid(k) for k in keys)
    ph = ",".join("?" * len(keys))
    try:
        con.execute(f"INSERT INTO {db.qid(table)} ({col_list}) VALUES ({ph})", vals)
        con.commit()
    except sqlite3.Error as e:
        con.close()
        return jsonify({"ok": False, "error": str(e)}), 400
    con.close()
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# 编辑一行
# ---------------------------------------------------------------------------
@app.route("/api/tables/<table>/rows/<path:pk>", methods=["PUT"])
def api_update(table, pk):
    if table not in KNOWN_TABLES:
        return jsonify({"ok": False, "error": "未知表"}), 404
    payload = request.get_json(force=True, silent=True) or {}
    con = get_con()
    pkcol = get_pk(table, con)
    cols = [r["name"] for r in con.execute(f"PRAGMA table_info({db.qid(table)})").fetchall()]
    keys = [k for k in payload.keys() if k in cols and k != pkcol]
    if not keys:
        con.close()
        return jsonify({"ok": False, "error": "无有效字段"}), 400
    sets = ",".join(f"{db.qid(k)}=?" for k in keys)
    vals = [None if (isinstance(payload.get(k), str) and payload.get(k).strip() == "")
            else payload.get(k) for k in keys]
    vals.append(pk)
    try:
        con.execute(f"UPDATE {db.qid(table)} SET {sets} WHERE {db.qid(pkcol)}=?", vals)
        con.commit()
    except sqlite3.Error as e:
        con.close()
        return jsonify({"ok": False, "error": str(e)}), 400
    con.close()
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# 删除一行
# ---------------------------------------------------------------------------
@app.route("/api/tables/<table>/rows/<path:pk>", methods=["DELETE"])
def api_delete(table, pk):
    if table not in KNOWN_TABLES:
        return jsonify({"ok": False, "error": "未知表"}), 404
    con = get_con()
    pkcol = get_pk(table, con)
    con.execute(f"DELETE FROM {db.qid(table)} WHERE {db.qid(pkcol)}=?", [pk])
    con.commit()
    con.close()
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# CSV / Excel 导入（追加新数据 / 跳过重复主键，复用 db_manager 逻辑）
# ---------------------------------------------------------------------------
def _build_import_report(table, f):
    """解析上传文件为校验报告：xlsx 落临时文件按工作表读，CSV 按编码探测读。"""
    data = f.stream.read()
    if (f.filename or "").lower().endswith((".xlsx", ".xlsm")):
        import tempfile
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
                tmp.write(data)
                tmp_path = tmp.name
            return _analyze_import_xlsx(table, tmp_path)
        finally:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
    text, enc = _decode(data)
    rep = _analyze_import(table, text)
    rep["encoding"] = enc
    return rep


@app.route("/api/import", methods=["POST"])
def api_import():
    table = request.form.get("table")
    f = request.files.get("file")
    if table not in KNOWN_TABLES:
        return jsonify({"ok": False, "error": "未知目标表"}), 400
    if not f or not f.filename:
        return jsonify({"ok": False, "error": "未收到文件"}), 400
    try:
        rep = _build_import_report(table, f)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    if rep["missing_pk_col"]:
        return jsonify({"ok": False,
                        "error": f"上传文件缺少主键列「{rep['pk']}」，无法导入",
                        "counts": rep["counts"]}), 400
    if not rep["counts"]["valid"]:
        return jsonify({"ok": False,
                        "error": "没有可导入的有效数据行（请检查主键与列数）",
                        "counts": rep["counts"]}), 400
    con = get_con()
    try:
        insert_cols = rep["db_columns"]
        col_list = ",".join(db.qid(c) for c in insert_cols)
        ph = ",".join("?" * len(insert_cols))
        sql = f"INSERT OR IGNORE INTO {db.qid(table)} ({col_list}) VALUES ({ph})"
        before = con.total_changes
        con.executemany(sql, rep["valid_rows"])
        after = con.total_changes
        con.commit()
        inserted = after - before
    finally:
        con.close()
    c = rep["counts"]
    skipped = c["valid"] - inserted
    return jsonify({
        "ok": True, "encoding": rep.get("encoding"), "total": c["total"], "valid": c["valid"],
        "inserted": inserted, "skipped": skipped,
        "skipped_blank_pk": c["blank_pk"], "error_rows": c["col_mismatch"],
        "amount_warn": c["amount_warn"], "dup_in_file": c["dup_in_file"],
    })


@app.route("/api/import-preview", methods=["POST"])
def api_import_preview():
    table = request.form.get("table")
    f = request.files.get("file")
    if table not in KNOWN_TABLES:
        return jsonify({"ok": False, "error": "未知目标表"}), 400
    if not f or not f.filename:
        return jsonify({"ok": False, "error": "未收到文件"}), 400
    try:
        rep = _build_import_report(table, f)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    rep.pop("valid_rows", None)  # 预览不返回大数据
    return jsonify({"ok": True, **rep})


# ---------------------------------------------------------------------------
# 数据质量 / 关系健康度（映射维度不一致、事实表有新增维度但维度表缺失）
# ---------------------------------------------------------------------------
def rel_stats(con, t, col, rt, rc):
    """计算某条关系的孤儿统计，返回 (total, orphan, rate, status)。"""
    total = con.execute(
        f"SELECT COUNT(*) FROM {db.qid(t)} WHERE {db.qid(col)} IS NOT NULL"
    ).fetchone()[0]
    orphan = con.execute(
        f"SELECT COUNT(*) FROM {db.qid(t)} "
        f"WHERE {db.qid(col)} IS NOT NULL "
        f"AND {db.qid(col)} NOT IN (SELECT {db.qid(rc)} FROM {db.qid(rt)})"
    ).fetchone()[0]
    rate = (orphan / total * 100) if total else 0.0
    status = "ok" if rate == 0 else ("warn" if rate <= 5 else "bad")
    return total, orphan, rate, status


@app.route("/api/data-quality")
def api_data_quality():
    con = get_con()
    try:
        fks = con.execute(
            "SELECT id,source_table,source_col,target_table,target_col,kind,cardinality,note,"
            "advice_override,ignored "
            "FROM map_config WHERE enabled=1 ORDER BY id").fetchall()
        rels = []
        for r in fks:
            t, col, rt, rc = r["source_table"], r["source_col"], r["target_table"], r["target_col"]
            try:
                total, orphan, rate, status = rel_stats(con, t, col, rt, rc)
                base_adv, base_reason = relation_advice(t, col, rt)
                is_dim = rt.startswith("dim_")
                ignored = r["ignored"]
                override = r["advice_override"]
                # 优先级：忽略 > 用户覆盖 > 默认建议；关系完整（无孤儿）一律降级无需补录
                if ignored:
                    adv, adv_reason = ("not_needed", "已手动忽略该关系。")
                elif override:
                    adv, adv_reason = (override, "用户自定义等级"
                                       + (("：" + r["note"]) if r["note"] else "。"))
                else:
                    adv, adv_reason = base_adv, base_reason
                if orphan == 0:
                    adv, adv_reason = ("not_needed", "关系完整：无缺失值，无需补录。")
                rels.append({
                    "mid": r["id"], "fact": t, "fact_col": col,
                    "dim": rt, "dim_col": rc, "kind": r["kind"],
                    "cardinality": r["cardinality"],
                    "total": total, "orphan": orphan, "rate": round(rate, 2),
                    "status": status, "target_is_dim": is_dim,
                    "advice": adv, "advice_reason": adv_reason,
                    "note": r["note"], "ignored": ignored, "advice_override": override,
                    "extra_cols": dim_extra_cols(con, rt, rc) if is_dim else [],
                    "allowed_modes": (["placeholder", "manual", "merge"] if is_dim else []),
                })
            except sqlite3.Error as e:
                rels.append({"mid": r["id"], "fact": t, "fact_col": col,
                             "dim": rt, "dim_col": rc, "kind": r["kind"],
                             "error": str(e), "status": "err",
                             "target_is_dim": rt.startswith("dim_")})
        total_rows = 0
        for item in _table_meta():
            total_rows += con.execute(
                f"SELECT COUNT(*) FROM {db.qid(item['table'])}").fetchone()[0]
        return jsonify({"relationships": rels,
                        "table_count": len(_table_meta()),
                        "total_rows": total_rows})
    finally:
        con.close()


# ---------------------------------------------------------------------------
# 映射配置 CRUD（map_config，持久化；支持 Web 页增删改查）
# ---------------------------------------------------------------------------
@app.route("/api/mapping-tables")
def api_mapping_tables():
    """返回已知数据表及其列，供新增/编辑映射表单的下拉选择。"""
    con = get_con()
    try:
        out = []
        for t in sorted(KNOWN_TABLES):
            cols = [r[1] for r in con.execute(f"PRAGMA table_info({db.qid(t)})").fetchall()]
            out.append({"table": t, "label": table_label(t), "columns": cols})
        return jsonify({"tables": out})
    finally:
        con.close()


@app.route("/api/model-view")
def api_model_view():
    """Power BI「模型视图」数据源：全部表 + 每张表完整字段明细 + 关系 + 画布位置。"""
    con = get_con()
    try:
        # 1) 关系（含实时孤儿率统计，与「映射关系」页同源）
        rows = con.execute(
            "SELECT id,source_table,source_col,target_table,target_col,kind,enabled,"
            "cardinality,note,advice_override,ignored FROM map_config ORDER BY id").fetchall()
        rels = []
        for r in rows:
            t, col, rt, rc = r["source_table"], r["source_col"], r["target_table"], r["target_col"]
            try:
                total, orphan, rate, status = rel_stats(con, t, col, rt, rc)
                base_adv, base_reason = relation_advice(t, col, rt)
                ignored = r["ignored"]
                override = r["advice_override"]
                # 与 /api/data-quality 完全一致：忽略 > 用户覆盖 > 默认建议；无孤儿一律降级
                if ignored:
                    adv, adv_reason = ("not_needed", "已手动忽略该关系。")
                elif override:
                    adv, adv_reason = (override, "用户自定义等级"
                                       + (("：" + r["note"]) if r["note"] else "。"))
                else:
                    adv, adv_reason = base_adv, base_reason
                if orphan == 0:
                    adv, adv_reason = ("not_needed", "关系完整：无缺失值，无需补录。")
            except sqlite3.Error:
                total, orphan, rate, status = 0, 0, 0.0, "err"
                adv, adv_reason = "not_needed", ""
            rels.append({
                "id": r["id"], "source_table": t, "source_col": col,
                "target_table": rt, "target_col": rc, "kind": r["kind"],
                "enabled": r["enabled"], "cardinality": r["cardinality"],
                "note": r["note"], "ignored": r["ignored"],
                "advice_override": r["advice_override"],
                "total": total, "orphan": orphan, "rate": round(rate, 2), "status": status,
                "target_is_dim": rt.startswith("dim_"),
                "advice": adv, "advice_reason": adv_reason,
            })
        # 2) 表 + 完整字段明细（名称 / 声明类型 / 是否主键 / 是否金额列）
        tables = []
        for t in sorted(KNOWN_TABLES):
            info = con.execute(f"PRAGMA table_info({db.qid(t)})").fetchall()
            cols = [{
                "name": r["name"],
                "type": (r["type"] or "TEXT").upper(),
                "pk": bool(r["pk"]),
                "amount": (r["type"] or "").upper().startswith("REAL"),
            } for r in info]
            try:
                row_count = con.execute(f"SELECT COUNT(*) FROM {db.qid(t)}").fetchone()[0]
            except sqlite3.Error:
                row_count = 0
            tables.append({
                "table": t, "label": table_label(t), "desc": table_desc(t),
                "kind": "dim" if t.startswith("dim_") else "fact",
                "pk": get_pk(t, con), "row_count": row_count,
                "columns": cols, "col_count": len(cols),
            })
        # 3) 画布位置（用户在模型视图拖拽后保存）
        layout = {}
        try:
            for r in con.execute("SELECT table_name,x,y,height FROM map_layout").fetchall():
                layout[r["table_name"]] = {"x": r["x"], "y": r["y"], "height": r["height"]}
        except sqlite3.Error:
            pass
        return jsonify({"tables": tables, "relationships": rels, "layout": layout})
    finally:
        con.close()


@app.route("/api/model-layout", methods=["POST"])
def api_model_layout():
    """保存 / 重置「模型视图」中表框的位置。

    入参 {positions: {表名: {x, y}}}; 或 {reset: true} 清空全部位置（回到自动布局）。
    """
    body = request.get_json(force=True, silent=True) or {}
    con = get_con()
    try:
        db.ensure_meta_tables(con, seed=False)
        if body.get("reset"):
            con.execute("DELETE FROM map_layout")
            # 重置后可同时带入新的自动布局位置
        pos = body.get("positions") or {}
        heights = body.get("heights") or {}
        saved = 0
        for t, p in pos.items():
            if t not in KNOWN_TABLES or not isinstance(p, dict):
                continue
            try:
                x, y = float(p.get("x", 0)), float(p.get("y", 0))
            except (TypeError, ValueError):
                continue
            # 过滤 NaN / 越界值（NaN 的所有比较均为 False，会被这里挡掉）
            if not (-100000 < x < 100000 and -100000 < y < 100000):
                continue
            hv = None
            if t in heights:
                try:
                    hv = float(heights[t])
                    if not (40 < hv < 2000):
                        hv = None
                except (TypeError, ValueError):
                    hv = None
            if hv is not None:
                con.execute(
                    "INSERT INTO map_layout(table_name,x,y,height) VALUES (?,?,?,?) "
                    "ON CONFLICT(table_name) DO UPDATE SET x=excluded.x, y=excluded.y, height=excluded.height",
                    (t, x, y, hv))
            else:
                con.execute(
                    "INSERT INTO map_layout(table_name,x,y) VALUES (?,?,?) "
                    "ON CONFLICT(table_name) DO UPDATE SET x=excluded.x, y=excluded.y",
                    (t, x, y))
            saved += 1
        con.commit()
        return jsonify({"ok": True, "saved": saved})
    finally:
        con.close()


@app.route("/api/mappings")
def api_mappings():
    con = get_con()
    try:
        rows = con.execute(
            "SELECT id,source_table,source_col,target_table,target_col,kind,enabled,"
            "cardinality,note,advice_override,ignored FROM map_config ORDER BY id").fetchall()
        out = []
        for r in rows:
            t, col, rt, rc = r["source_table"], r["source_col"], r["target_table"], r["target_col"]
            try:
                total, orphan, rate, status = rel_stats(con, t, col, rt, rc)
            except sqlite3.Error:
                total, orphan, rate, status = 0, 0, 0.0, "err"
            out.append({
                "id": r["id"], "source_table": t, "source_col": col,
                "target_table": rt, "target_col": rc, "kind": r["kind"],
                "enabled": r["enabled"], "cardinality": r["cardinality"], "note": r["note"],
                "advice_override": r["advice_override"], "ignored": r["ignored"],
                "total": total, "orphan": orphan, "rate": round(rate, 2), "status": status,
                "target_is_dim": rt.startswith("dim_"),
                "extra_cols": dim_extra_cols(con, rt, rc) if rt.startswith("dim_") else [],
            })
        return jsonify({"mappings": out})
    finally:
        con.close()


@app.route("/api/mappings", methods=["POST"])
def api_mapping_create():
    body = request.get_json(force=True, silent=True) or {}
    st = (body.get("source_table") or "").strip()
    sc = (body.get("source_col") or "").strip()
    tt = (body.get("target_table") or "").strip()
    tc = (body.get("target_col") or "").strip()
    if not all([st, sc, tt, tc]):
        return jsonify({"ok": False, "error": "源表/源列/目标表/目标列均必填"}), 400
    if st not in KNOWN_TABLES or tt not in KNOWN_TABLES:
        return jsonify({"ok": False, "error": "源表或目标表不是已知数据表"}), 400
    kind = (body.get("kind") or ("dim" if tt.startswith("dim_") else "fact")).strip()
    if kind not in ("dim", "fact", "derived"):
        kind = "dim" if tt.startswith("dim_") else "fact"
    note = (body.get("note") or "").strip()
    card = (body.get("cardinality") or "").strip()
    if card not in ("*:1", "1:*", "1:1"):
        card = "*:1" if kind in ("dim", "derived") else "1:1"
    con = get_con()
    try:
        for (t, c) in [(st, sc), (tt, tc)]:
            cols = [r[1] for r in con.execute(f"PRAGMA table_info({db.qid(t)})").fetchall()]
            if c not in cols:
                return jsonify({"ok": False, "error": f"表 {t} 不存在列「{c}」"}), 400
        cur = con.execute(
            "INSERT INTO map_config "
            "(source_table,source_col,target_table,target_col,kind,enabled,note,cardinality) "
            "VALUES (?,?,?,?,?,1,?,?)", (st, sc, tt, tc, kind, note, card))
        con.commit()
        return jsonify({"ok": True, "id": cur.lastrowid})
    finally:
        con.close()


@app.route("/api/mappings/<int:mid>", methods=["PUT"])
def api_mapping_update(mid):
    body = request.get_json(force=True, silent=True) or {}
    con = get_con()
    try:
        row = con.execute("SELECT id FROM map_config WHERE id=?", [mid]).fetchone()
        if not row:
            return jsonify({"ok": False, "error": "映射不存在"}), 404
        fields = {}
        for f in ("source_table", "source_col", "target_table", "target_col",
                  "kind", "note", "advice_override", "enabled", "ignored",
                  "cardinality"):
            if f in body:
                fields[f] = body[f]
        st = fields.get("source_table"); sc = fields.get("source_col")
        tt = fields.get("target_table"); tc = fields.get("target_col")
        if st is not None and st not in KNOWN_TABLES:
            return jsonify({"ok": False, "error": "源表非法"}), 400
        if tt is not None and tt not in KNOWN_TABLES:
            return jsonify({"ok": False, "error": "目标表非法"}), 400
        pairs = []
        if st and sc:
            pairs.append((st, sc))
        if tt and tc:
            pairs.append((tt, tc))
        for (t, c) in pairs:
            cols = [r[1] for r in con.execute(f"PRAGMA table_info({db.qid(t)})").fetchall()]
            if c not in cols:
                return jsonify({"ok": False, "error": f"表 {t} 不存在列「{c}」"}), 400
        if "enabled" in fields:
            fields["enabled"] = 1 if fields["enabled"] else 0
        if "ignored" in fields:
            fields["ignored"] = 1 if fields["ignored"] else 0
        if "advice_override" in fields and not fields["advice_override"]:
            fields["advice_override"] = None
        if fields:
            sets = ",".join(f"{db.qid(k)}=?" for k in fields)
            con.execute(f"UPDATE map_config SET {sets} WHERE id=?",
                        list(fields.values()) + [mid])
            con.commit()
        return jsonify({"ok": True})
    finally:
        con.close()


@app.route("/api/mappings/<int:mid>", methods=["DELETE"])
def api_mapping_delete(mid):
    con = get_con()
    try:
        con.execute("DELETE FROM map_config WHERE id=?", [mid])
        con.commit()
        return jsonify({"ok": True})
    finally:
        con.close()


# ---------------------------------------------------------------------------
# 自定义质量规则 CRUD（quality_rules）+ 评估
# ---------------------------------------------------------------------------
@app.route("/api/quality-rules")
def api_quality_rules():
    con = get_con()
    try:
        rows = con.execute(
            "SELECT id,target_table,check_type,check_col,severity,note,enabled "
            "FROM quality_rules ORDER BY id").fetchall()
        return jsonify({"rules": [dict(r) for r in rows]})
    finally:
        con.close()


@app.route("/api/quality-rules", methods=["POST"])
def api_qrule_create():
    body = request.get_json(force=True, silent=True) or {}
    t = (body.get("target_table") or "").strip()
    ct = (body.get("check_type") or "").strip()
    col = (body.get("check_col") or "").strip() or None
    sev = (body.get("severity") or "warn").strip()
    note = (body.get("note") or "").strip()
    if t not in KNOWN_TABLES:
        return jsonify({"ok": False, "error": "目标表不是已知数据表"}), 400
    if ct not in ("not_null", "unique"):
        return jsonify({"ok": False, "error": "检查类型仅支持 not_null / unique"}), 400
    if ct in ("not_null", "unique") and not col:
        return jsonify({"ok": False, "error": "该检查类型需要指定列"}), 400
    if sev not in ("warn", "bad"):
        sev = "warn"
    con = get_con()
    try:
        ccols = [r[1] for r in con.execute(f"PRAGMA table_info({db.qid(t)})").fetchall()]
        if col not in ccols:
            return jsonify({"ok": False, "error": f"表 {t} 不存在列「{col}」"}), 400
        cur = con.execute(
            "INSERT INTO quality_rules "
            "(target_table,check_type,check_col,severity,note,enabled) "
            "VALUES (?,?,?,?,?,1)", (t, ct, col, sev, note))
        con.commit()
        return jsonify({"ok": True, "id": cur.lastrowid})
    finally:
        con.close()


@app.route("/api/quality-rules/<int:rid>", methods=["PUT"])
def api_qrule_update(rid):
    body = request.get_json(force=True, silent=True) or {}
    con = get_con()
    try:
        row = con.execute("SELECT id FROM quality_rules WHERE id=?", [rid]).fetchone()
        if not row:
            return jsonify({"ok": False, "error": "规则不存在"}), 404
        fields = {}
        for f in ("target_table", "check_type", "check_col", "severity", "note", "enabled"):
            if f in body:
                fields[f] = body[f]
        if "enabled" in fields:
            fields["enabled"] = 1 if fields["enabled"] else 0
        if "check_type" in fields and fields["check_type"] not in ("not_null", "unique"):
            return jsonify({"ok": False, "error": "检查类型仅支持 not_null / unique"}), 400
        if fields:
            sets = ",".join(f"{db.qid(k)}=?" for k in fields)
            con.execute(f"UPDATE quality_rules SET {sets} WHERE id=?",
                        list(fields.values()) + [rid])
            con.commit()
        return jsonify({"ok": True})
    finally:
        con.close()


@app.route("/api/quality-rules/<int:rid>", methods=["DELETE"])
def api_qrule_delete(rid):
    con = get_con()
    try:
        con.execute("DELETE FROM quality_rules WHERE id=?", [rid])
        con.commit()
        return jsonify({"ok": True})
    finally:
        con.close()


@app.route("/api/quality-rules/evaluate")
def api_qrule_eval():
    con = get_con()
    try:
        rows = con.execute(
            "SELECT id,target_table,check_type,check_col,severity,note,enabled "
            "FROM quality_rules WHERE enabled=1 ORDER BY id").fetchall()
        res = []
        for r in rows:
            t, ct, col = r["target_table"], r["check_type"], r["check_col"]
            count, passed, detail = 0, True, ""
            try:
                if ct == "not_null":
                    count = con.execute(
                        f"SELECT COUNT(*) FROM {db.qid(t)} "
                        f"WHERE {db.qid(col)} IS NULL OR {db.qid(col)}=''").fetchone()[0]
                    passed = (count == 0)
                    detail = f"空值 {count} 行"
                elif ct == "unique":
                    count = con.execute(
                        f"SELECT COUNT(*)-COUNT(DISTINCT {db.qid(col)}) "
                        f"FROM {db.qid(t)} WHERE {db.qid(col)} IS NOT NULL").fetchone()[0]
                    passed = (count == 0)
                    detail = f"因重复值导致 {count} 行不唯一"
            except sqlite3.Error as e:
                passed = None
                detail = "检查失败：" + str(e)
            res.append({"id": r["id"], "target_table": t, "check_type": ct,
                        "check_col": col, "severity": r["severity"], "note": r["note"],
                        "enabled": r["enabled"], "passed": passed, "count": count,
                        "detail": detail})
        return jsonify({"results": res})
    finally:
        con.close()


@app.route("/api/missing-keys")
def api_missing_keys():
    """缺失值清单：每个值带「涉及行数」，按影响行数从大到小排（先处理影响最大的）。"""
    t = request.args.get("fact"); col = request.args.get("fact_col")
    rt = request.args.get("dim"); rc = request.args.get("dim_col")
    page = max(1, int(request.args.get("page", 1)))
    size = max(1, min(1000, int(request.args.get("size", 100))))
    if not all([t, col, rt, rc]) or t not in KNOWN_TABLES or rt not in KNOWN_TABLES:
        return jsonify({"error": "参数缺失或非法"}), 400
    con = get_con()
    try:
        miss_where = (
            f"WHERE {db.qid(col)} IS NOT NULL "
            f"AND {db.qid(col)} NOT IN (SELECT {db.qid(rc)} FROM {db.qid(rt)})"
        )
        rows = con.execute(
            f"SELECT {db.qid(col)} AS k, COUNT(*) AS c FROM {db.qid(t)} "
            f"{miss_where} GROUP BY {db.qid(col)} "
            f"ORDER BY c DESC, k LIMIT ? OFFSET ?", [size, (page - 1) * size]).fetchall()
        items = [{"key": r["k"], "rows": r["c"]} for r in rows]
        total = con.execute(
            f"SELECT COUNT(DISTINCT {db.qid(col)}) FROM {db.qid(t)} {miss_where}"
        ).fetchone()[0]
        rows_total = con.execute(
            f"SELECT COUNT(*) FROM {db.qid(t)} {miss_where}").fetchone()[0]
        suggests = suggest_targets(con, rt, rc, [i["key"] for i in items])
        for i in items:
            if i["key"] in suggests:
                i["suggest"] = suggests[i["key"]]
        # 附带字段建议（如新建部门成员时要填的 DepartmentEn=Admin/Aftersales）
        extra_cols = dim_extra_cols(con, rt, rc) if rt.startswith("dim_") else []
        extra_options = {}
        if extra_cols:
            ex = suggest_extras(con, rt, rc, extra_cols, [i["key"] for i in items])
            for i in items:
                if i["key"] in ex:
                    i["extra"] = ex[i["key"]]
            for c in extra_cols:      # 供输入框下拉选择（如 Admin / Aftersales / Sales …）
                extra_options[c] = [r[0] for r in con.execute(
                    f"SELECT DISTINCT {db.qid(c)} FROM {db.qid(rt)} "
                    f"WHERE {db.qid(c)} IS NOT NULL AND {db.qid(c)} != '' "
                    f"ORDER BY 1 LIMIT 200").fetchall()]
        return jsonify({"keys": [i["key"] for i in items], "items": items,
                        "total": total, "rows_total": rows_total,
                        "extra_cols": extra_cols, "extra_options": extra_options,
                        "page": page, "size": size})
    finally:
        con.close()


@app.route("/api/dim-options")
def api_dim_options():
    """返回维度表的现存成员值（供「归并到已有维度」下拉选择）。"""
    rt = request.args.get("dim"); rc = request.args.get("dim_col")
    q = (request.args.get("q") or "").strip()
    limit = max(1, min(300, int(request.args.get("limit", 60))))
    if not rt or not rc or rt not in KNOWN_TABLES:
        return jsonify({"error": "参数缺失或非法"}), 400
    con = get_con()
    try:
        cols = [r["name"] for r in con.execute(f"PRAGMA table_info({db.qid(rt)})").fetchall()]
        if rc not in cols:
            return jsonify({"error": "维度列不存在"}), 400
        params, where = [], ""
        if q:
            where = f" WHERE {db.qid(rc)} LIKE ?"
            params.append(f"%{q}%")
        total = con.execute(f"SELECT COUNT(*) FROM {db.qid(rt)}{where}", params).fetchone()[0]
        rows = [r[0] for r in con.execute(
            f"SELECT {db.qid(rc)} FROM {db.qid(rt)}{where} ORDER BY {db.qid(rc)} LIMIT ?",
            params + [limit]).fetchall()]
        return jsonify({"options": rows, "total": total,
                        "label": table_label(rt), "col": rc})
    finally:
        con.close()


@app.route("/api/backfill", methods=["POST"])
def api_backfill():
    """缺失维度补录，支持三种方式：
      placeholder — 以缺失值为主键插入占位行（其余字段留空）
      manual      — 以缺失值为主键新建维度成员，其余字段由前端提交的 values 指定
      merge       — 把事实表中的缺失值「归并」为维度表中已存在的某个值（会修改事实表）
    """
    body = request.get_json(force=True, silent=True) or {}
    t = body.get("fact"); col = body.get("fact_col")
    rt = body.get("dim"); rc = body.get("dim_col")
    mode = (body.get("mode") or "placeholder").lower()
    if not all([t, col, rt, rc]) or t not in KNOWN_TABLES or rt not in KNOWN_TABLES:
        return jsonify({"ok": False, "error": "参数缺失或非法"}), 400
    if mode not in ("placeholder", "manual", "merge"):
        return jsonify({"ok": False, "error": f"不支持的补录方式：{mode}"}), 400
    if not rt.startswith("dim_"):
        adv, reason = relation_advice(t, col, rt)
        return jsonify({"ok": False, "advice": adv,
                        "error": "该关系无需补录：" + reason}), 400

    con = get_con()
    try:
        keys = [r[0] for r in con.execute(
            f"SELECT DISTINCT {db.qid(col)} FROM {db.qid(t)} "
            f"WHERE {db.qid(col)} IS NOT NULL "
            f"AND {db.qid(col)} NOT IN (SELECT {db.qid(rc)} FROM {db.qid(rt)})"
        ).fetchall()]
        if not keys:
            return jsonify({"ok": True, "mode": mode, "inserted": 0, "affected": 0,
                            "message": "无缺失维度，无需补录"})

        # —— 方式三：归并到已有维度值（修改事实表）——
        if mode == "merge":
            target = body.get("target")
            if target is None or str(target).strip() == "":
                return jsonify({"ok": False, "error": "「归并」需要指定要归并到的现存维度值"}), 400
            hit = con.execute(
                f"SELECT 1 FROM {db.qid(rt)} WHERE {db.qid(rc)} = ? LIMIT 1", [target]).fetchone()
            if not hit:
                return jsonify({"ok": False,
                                "error": f"「{target}」不是 {table_label(rt)} 中已存在的值，无法归并"}), 400
            before = con.total_changes
            con.execute(
                f"UPDATE {db.qid(t)} SET {db.qid(col)} = ? "
                f"WHERE {db.qid(col)} IS NOT NULL "
                f"AND {db.qid(col)} NOT IN (SELECT {db.qid(rc)} FROM {db.qid(rt)})", [target])
            con.commit()
            affected = con.total_changes - before
            return jsonify({"ok": True, "mode": "merge", "target": target,
                            "merged_keys": len(keys), "affected": affected, "inserted": 0,
                            "message": f"已将 {len(keys)} 个不同缺失值（共 {affected} 行数据）归并为「{target}」"})

        # —— 方式二：手动新建维度成员（主键=缺失值，其余字段由用户填）——
        if mode == "manual":
            values = body.get("values") or {}
            cols = [r["name"] for r in con.execute(f"PRAGMA table_info({db.qid(rt)})").fetchall()]
            extra_cols, extra_vals = [], []
            for c, v in values.items():
                if c == rc or c not in cols:
                    continue
                extra_cols.append(c)
                extra_vals.append(None if (v is None or str(v).strip() == "") else v)
            insert_cols = [rc] + extra_cols
            col_list = ",".join(db.qid(c) for c in insert_cols)
            ph = ",".join("?" * len(insert_cols))
            sql = f"INSERT OR IGNORE INTO {db.qid(rt)} ({col_list}) VALUES ({ph})"
            before = con.total_changes
            for k in keys:
                con.execute(sql, [k] + extra_vals)
            con.commit()
            inserted = con.total_changes - before
            return jsonify({"ok": True, "mode": "manual", "inserted": inserted, "affected": 0,
                            "filled": extra_cols,
                            "message": f"已新建 {inserted} 个维度成员（主键=缺失值"
                                       + (f"，另填 {len(extra_cols)} 个字段）" if extra_cols else "，其余字段为空）")})

        # —— 方式一：一键占位（默认）——
        before = con.total_changes
        for k in keys:
            try:
                con.execute(f"INSERT OR IGNORE INTO {db.qid(rt)} ({db.qid(rc)}) VALUES (?)", [k])
            except sqlite3.Error:
                pass
        con.commit()
        inserted = con.total_changes - before
        return jsonify({"ok": True, "mode": "placeholder", "inserted": inserted, "affected": 0,
                        "message": f"已为 {table_label(rt)} 补录 {inserted} 个占位成员"
                                   f"（其余字段为空，可稍后在表里补全）"})
    finally:
        con.close()


@app.route("/api/backfill-map", methods=["POST"])
def api_backfill_map():
    """逐条映射补录：为每个「多出来的」缺失值指定它要归到哪个维度值。

    body = {
      fact, fact_col, dim, dim_col,
      mappings: [{key, target}],      # 逐条指定；target 空串 = 新建同名成员
      default_action: "keep"|"skip",  # 未指定的缺失值：keep=各自新建同名成员 / skip=暂不处理
      default_target: "某个已有值",    # 可选：未指定的全部归到该已有值
      extra: {字段: 值}               # 可选：新建成员时一并写入的其它字段
    }

    规则：
      target 在维度表中【已存在】→ 把事实表里该缺失值改写为 target（归并，不新建）
      target 在维度表中【不存在】→ 在维度表新建成员 target（可带 extra），并把事实表改写为 target
      target 为空 / 等于原值      → 新建同名成员（事实表无需改写）
    返回：created（新建成员数）/ merged_keys / merged_rows / remaining（剩余缺失行数）
    """
    body = request.get_json(force=True, silent=True) or {}
    t = body.get("fact"); col = body.get("fact_col")
    rt = body.get("dim"); rc = body.get("dim_col")
    if not all([t, col, rt, rc]) or t not in KNOWN_TABLES or rt not in KNOWN_TABLES:
        return jsonify({"ok": False, "error": "参数缺失或非法"}), 400
    if not rt.startswith("dim_"):
        adv, reason = relation_advice(t, col, rt)
        return jsonify({"ok": False, "advice": adv,
                        "error": "该关系无需补录：" + reason}), 400

    con = get_con()
    try:
        miss_where = (
            f"WHERE {db.qid(col)} IS NOT NULL "
            f"AND {db.qid(col)} NOT IN (SELECT {db.qid(rc)} FROM {db.qid(rt)})"
        )
        miss = con.execute(
            f"SELECT {db.qid(col)} AS k, COUNT(*) AS c FROM {db.qid(t)} "
            f"{miss_where} GROUP BY {db.qid(col)}").fetchall()
        missing = {r["k"]: r["c"] for r in miss}
        if not missing:
            return jsonify({"ok": True, "created": 0, "merged_keys": 0, "merged_rows": 0,
                            "remaining": 0, "message": "无缺失维度，无需补录"})

        # —— 构建计划：key -> 目标值；同时收集逐行附带字段（如 DepartmentEn）——
        plan = {}
        row_extra = {}
        for m in (body.get("mappings") or []):
            k = m.get("key")
            if k is None or k not in missing:
                continue
            tg = m.get("target")
            tg = "" if tg is None else str(tg).strip()
            plan[k] = tg or k          # 留空 = 新建同名成员
            ex = m.get("extra") or {}
            if isinstance(ex, dict):
                clean = {}
                for c, v in ex.items():
                    if v is None or str(v).strip() == "":
                        continue
                    clean[str(c)] = str(v).strip()
                if clean:
                    row_extra[k] = clean
        default_target = (body.get("default_target") or "").strip()
        default_action = (body.get("default_action") or "keep").lower()
        for k in missing:
            if k in plan:
                continue
            if default_target:
                plan[k] = default_target
            elif default_action == "keep":
                plan[k] = k
            # skip：不放进 plan，保持原样

        if not plan:
            return jsonify({"ok": True, "created": 0, "merged_keys": 0, "merged_rows": 0,
                            "remaining": len(missing),
                            "message": "未指定任何映射，未做改动"})

        # —— 链式解析：A→B 且 B→C 时，A 直接归到 C（避免造出没人用的中间成员）——
        def resolve(k, seen=None):
            seen = set() if seen is None else seen
            if k in seen:
                return k
            seen.add(k)
            tg = plan.get(k, k)
            if tg != k and plan.get(tg, tg) != tg:
                return resolve(tg, seen)
            return tg

        resolved = {k: resolve(k) for k in plan}

        # —— 目标值是否已存在于维度表 ——
        exists_map = {}
        for tg in set(resolved.values()):
            hit = con.execute(
                f"SELECT 1 FROM {db.qid(rt)} WHERE {db.qid(rc)} = ? LIMIT 1", [tg]).fetchone()
            exists_map[tg] = bool(hit)

        # —— 附带字段：全局默认 + 逐行覆盖（逐行优先）；id 列自动续号 ——
        dim_cols = [r["name"] for r in con.execute(
            f"PRAGMA table_info({db.qid(rt)})").fetchall()]
        base_extra = {}
        for c, v in (body.get("extra") or {}).items():
            if c == rc or c not in dim_cols or v is None or str(v).strip() == "":
                continue
            base_extra[c] = str(v).strip()

        # 每个「将被新建」的成员 -> 要写入的附带字段（逐行优先，全局默认兜底）
        create_extras = {}
        for k, tg in resolved.items():
            if exists_map.get(tg):
                continue
            ex = dict(base_extra)
            ex.update({c: v for c, v in (row_extra.get(k) or {}).items() if c in dim_cols})
            slot = create_extras.setdefault(tg, {})
            for c, v in ex.items():
                slot.setdefault(c, v)

        # 需要写入的列（各新建成员的并集）；有 id 列时自动续号，避免留空
        fill_cols = []
        for ex in create_extras.values():
            for c in ex:
                if c not in fill_cols:
                    fill_cols.append(c)
        auto_id = ("id" in dim_cols and "id" not in fill_cols)
        if auto_id:
            fill_cols.insert(0, "id")
        ins_cols = [rc] + fill_cols
        ins_sql = (f"INSERT OR IGNORE INTO {db.qid(rt)} "
                   f"({','.join(db.qid(c) for c in ins_cols)}) "
                   f"VALUES ({','.join('?' * len(ins_cols))})")
        next_id = 1
        if auto_id:
            mx = con.execute(
                f"SELECT MAX(CAST({db.qid('id')} AS INTEGER)) FROM {db.qid(rt)}").fetchone()[0]
            next_id = (int(mx) + 1) if mx is not None else 1

        created = []
        for tg in create_extras:
            vals = []
            for c in fill_cols:
                if c == "id" and auto_id:
                    vals.append(str(next_id)); next_id += 1
                else:
                    vals.append(create_extras[tg].get(c))
            try:
                cur = con.execute(ins_sql, [tg] + vals)
                if cur.rowcount:
                    created.append(tg)
            except sqlite3.Error:
                pass

        # —— 改写事实表（归并 / 改名）——
        merged_keys, merged_rows = 0, 0
        for k, tg in resolved.items():
            if tg == k:
                continue
            cur = con.execute(
                f"UPDATE {db.qid(t)} SET {db.qid(col)} = ? WHERE {db.qid(col)} = ?", [tg, k])
            if cur.rowcount:
                merged_keys += 1
                merged_rows += cur.rowcount
        con.commit()

        # —— 剩余缺失（跳过策略 / 其他页未处理的会被算进来）——
        remaining_rows = con.execute(
            f"SELECT COUNT(*) FROM {db.qid(t)} {miss_where}").fetchone()[0]
        remaining_keys = con.execute(
            f"SELECT COUNT(DISTINCT {db.qid(col)}) FROM {db.qid(t)} {miss_where}").fetchone()[0]

        note_cols = [c for c in fill_cols if not (c == "id" and auto_id)]
        parts = [f"新建 {len(created)} 个维度成员"
                 + (f"（附带写入 {'、'.join(note_cols)}）" if note_cols else "")]
        if merged_keys:
            parts.append(f"归并 {merged_keys} 个缺失值（改写事实表 {merged_rows} 行）")
        if remaining_keys:
            parts.append(f"仍有 {remaining_keys} 个缺失值 / {remaining_rows} 行未处理")
        return jsonify({
            "ok": True, "created": len(created), "created_values": created[:50],
            "merged_keys": merged_keys, "merged_rows": merged_rows,
            "filled": note_cols, "auto_id": auto_id,
            "remaining_keys": remaining_keys, "remaining": remaining_rows,
            "message": "；".join(parts) + "。",
        })
    finally:
        con.close()


def _decode(data):
    enc_used = None
    text = None
    for enc in ("utf-8-sig", "utf-8", "gbk", "gb18030"):
        try:
            text = data.decode(enc)
            enc_used = enc
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        text = data.decode("latin-1", errors="replace")
        enc_used = "latin-1(可能乱码)"
    return text, enc_used


def _normalize_col(name):
    return (name or "").strip().lstrip("﻿").replace(" ", "")


def _analyze_import(table, text):
    """解析 CSV 并返回校验报告（不写入数据库），供导入前预览与写入两步复用。"""
    sample = text[:8192]
    try:
        delim = csv.Sniffer().sniff(sample, delimiters=[",", ";", "\t", "|"]).delimiter
    except Exception:
        delim = ","
    reader = csv.reader(io.StringIO(text), delimiter=delim)
    raw_header = next(reader)
    return _analyze_rows(table, raw_header, reader, delim, kind="csv")


def _analyze_import_xlsx(table, path):
    """解析 Excel 工作表并返回校验报告（与 CSV 导入共用同一套校验/写入逻辑）。

    读哪个工作表由 XLSX_SHEET_OF_TABLE 决定：上传时选哪个表，就取该表对应的工作表。
    """
    try:
        import openpyxl  # noqa: F401
    except ImportError:
        raise ValueError("服务端缺少 openpyxl，无法读取 Excel（请在 venv 中 pip install openpyxl）")
    sheet = db.XLSX_SHEET_OF_TABLE.get(table)
    if not sheet:
        raise ValueError(f"「{table}」不在 Excel 导出范围内（Excel 只含 6 张事实表；维度表请传 CSV）")
    src = db.XlsxSource(path, sheet)
    try:
        raw_header = src.header()
        if not raw_header:
            raise ValueError(f"Excel 中找不到工作表「{sheet}」，请确认上传的是 KUBE 采购数据导出文件")
        return _analyze_rows(table, raw_header, src.rows(), None, kind="xlsx", sheet=sheet)
    except KeyError:
        raise ValueError(f"Excel 中找不到工作表「{sheet}」，请确认上传的是 KUBE 采购数据导出文件")
    finally:
        src.close()


def _analyze_rows(table, raw_header, rows, delim, kind="csv", sheet=None):
    """CSV/xlsx 共用的导入校验：表头匹配、主键检查、金额解析、派生列、行归一化。

    raw_header/rows 来自 csv.reader 或 db.XlsxSource；单元格可能是字符串（CSV）
    或 str/int/float/datetime/bool（Excel），统一归一化后再校验。
    """
    con = get_con()
    try:
        raw_header = db.apply_header_aliases([("" if h is None else str(h)).strip() for h in raw_header])
        csv_colnames = db.dedupe_headers(raw_header)
        amount_idx = {i for i, h in enumerate(raw_header) if db.is_amount_header(h)}
        n_src = len(csv_colnames)

        db_cols = [r["name"] for r in con.execute(f"PRAGMA table_info({db.qid(table)})").fetchall()]
        extra_cols = [c for c, _ in db.EXTRA.get(table, [])]
        pk = get_pk(table, con)
        aliases = db.VALUE_ALIASES.get(table, {})

        # 表头 ↔ 数据库列匹配
        norm_db = {_normalize_col(c): c for c in db_cols}
        header_mapping = []
        matched_db = set()
        for h in csv_colnames:
            key = _normalize_col(h)
            if key in norm_db and norm_db[key] not in matched_db:
                dbcol = norm_db[key]
                matched_db.add(dbcol)
                header_mapping.append({"csv": h, "db": dbcol, "matched": True})
            else:
                header_mapping.append({"csv": h, "db": None, "matched": False})

        missing_pk_col = (pk not in csv_colnames and pk not in extra_cols)
        can_import = not missing_pk_col

        insert_cols = list(db_cols)
        pk_pos = insert_cols.index(pk) if pk in insert_cols else None

        blank_pk_samples, col_mismatch_samples, amount_fail = [], [], []
        dup_in_file = []
        seen_pk = set()
        counts = {"total": 0, "valid": 0, "blank_pk": 0, "col_mismatch": 0,
                  "amount_warn": 0, "dup_in_file": 0}
        valid_rows = []

        for ridx, r in enumerate(rows, start=2):  # 文件行号（表头=1）
            if r is None:
                continue
            r = list(r)
            if kind == "csv":
                if not any((cell or "").strip() for cell in r):
                    continue
                counts["total"] += 1
                if len(r) != n_src:
                    counts["col_mismatch"] += 1
                    if len(col_mismatch_samples) < 5:
                        col_mismatch_samples.append({"line": ridx, "cols": len(r),
                                                     "expected": n_src})
                    continue
            else:  # Excel：行长度补齐/截断到表头列数
                counts["total"] += 1
                if len(r) < n_src:
                    r = r + [None] * (n_src - len(r))
                elif len(r) > n_src:
                    r = r[:n_src]
            raw_dict = dict(zip(raw_header, r))
            # 已知文本变体归一化到维度表标准值（如新导出少了"表"字的支出类型）
            for col, amap in aliases.items():
                if col in raw_dict:
                    v = raw_dict[col]
                    if v is not None and str(v).strip() in amap:
                        r[raw_header.index(col)] = amap[str(v).strip()]
            extras = db.compute_extras(table, raw_dict) if table in db.EXTRA else []
            csv_vals = {}
            row_amount_warn = False
            for i, name in enumerate(csv_colnames):
                v = r[i]
                if v is None:
                    csv_vals[name] = None
                elif isinstance(v, str):
                    s = v.strip()
                    if s == "":
                        csv_vals[name] = None
                    elif i in amount_idx:
                        parsed = db.parse_amount(v)
                        if parsed is None:
                            row_amount_warn = True
                            if len(amount_fail) < 10:
                                amount_fail.append({"line": ridx, "col": name,
                                                    "value": str(v)[:30]})
                        csv_vals[name] = parsed
                    else:
                        csv_vals[name] = v
                elif isinstance(v, bool):  # Excel 布尔单元格
                    csv_vals[name] = "TRUE" if v else "FALSE"
                elif i in amount_idx:  # Excel 金额单元格（数字）
                    parsed = db.parse_amount(v)
                    if parsed is None:
                        row_amount_warn = True
                        if len(amount_fail) < 10:
                            amount_fail.append({"line": ridx, "col": name,
                                                "value": str(v)[:30]})
                    csv_vals[name] = parsed
                else:  # Excel 数字/日期单元格 -> 文本，与命令行导入行为一致
                    csv_vals[name] = str(v)
            if row_amount_warn:
                counts["amount_warn"] += 1

            row = []
            for c in insert_cols:
                if c in csv_colnames:
                    row.append(csv_vals[c])
                elif c in extra_cols:
                    idx = extra_cols.index(c)
                    row.append(extras[idx] if idx < len(extras) else None)
                else:
                    row.append(None)

            pkval = row[pk_pos] if pk_pos is not None else None
            if pkval is None:
                counts["blank_pk"] += 1
                if len(blank_pk_samples) < 5:
                    blank_pk_samples.append({"line": ridx})
                continue
            if pkval in seen_pk:
                counts["dup_in_file"] += 1
                if len(dup_in_file) < 10:
                    dup_in_file.append(str(pkval))
                continue
            seen_pk.add(pkval)
            counts["valid"] += 1
            valid_rows.append(tuple(row))

        return {
            "delimiter": delim,
            "detected_header": raw_header,
            "db_columns": db_cols,
            "pk": pk,
            "extra_cols": extra_cols,
            "header_mapping": header_mapping,
            "missing_pk_col": missing_pk_col,
            "can_import": can_import,
            "issues": {
                "blank_pk_samples": blank_pk_samples,
                "col_mismatch_samples": col_mismatch_samples,
                "amount_fail": amount_fail,
                "dup_in_file": dup_in_file,
            },
            "counts": counts,
            "valid_rows": valid_rows,
            "source": kind,
            "sheet": sheet,
            "encoding": "xlsx" if kind == "xlsx" else None,
        }
    finally:
        con.close()


@app.route("/api/shutdown", methods=["POST"])
def api_shutdown():
    """终止本服务进程（仅用于本地管理工具，需二次确认后由页面触发）。"""
    def _do_shutdown():
        time.sleep(0.3)            # 留出时间把响应发回前端
        if SERVER is not None:
            SERVER.shutdown()      # 必须在非 serve_forever 线程中调用
    threading.Thread(target=_do_shutdown, daemon=True).start()
    return jsonify({"ok": True, "message": "服务正在关闭…"})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    host = os.environ.get("HOST", "0.0.0.0")
    browse_host = "localhost" if host in ("0.0.0.0", "::", "") else host
    url = f"http://{browse_host}:{port}"
    print(f"采购数据库管理页面已启动： {url}")
    if host in ("0.0.0.0", "::"):
        print("（已绑定所有网卡，同一局域网可通过本机 IP 访问）")
    # 服务就绪后自动打开浏览器（设 BROWSER=0 可关闭，适合无界面环境）
    if os.environ.get("BROWSER", "1") != "0":
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    SERVER = make_server(host, port, app, threaded=True)
    SERVER.serve_forever()
