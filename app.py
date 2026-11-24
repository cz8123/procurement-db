# -*- coding: utf-8 -*-
"""
采购数据库 Web 管理页面 (Flask + SQLite)
=========================================
功能：
  1) 浏览 16 张表的数据（分页 / 搜索 / 排序）
  2) 对数据行进行 新增 / 编辑 / 删除
  3) 导入 CSV 到指定表（复用 db_manager 的「追加新数据、跳过重复」逻辑）

依赖：Flask（隔离 venv 中）
启动：start-dbadmin.bat 或 `python app.py`
  默认绑定 127.0.0.1:5000（仅本机可访问），启动后自动打开浏览器 http://localhost:5000。
  环境变量：
    HOST=0.0.0.0        允许局域网访问（此时强烈建议同时设 DBADMIN_TOKEN）
    DBADMIN_TOKEN=xxx   访问口令；设置后所有请求需携带（浏览器首访 ?token=xxx 即可，之后靠 cookie）
    BROWSER=0           不自动打开浏览器
  安全边界：默认仅本机可访问；「终止服务」接口永远只接受本机回环调用。
"""
import os
import re
import csv
import io
import hmac
import json
import time
import sys
import shutil
import datetime
import subprocess
import threading
import webbrowser
import sqlite3
import urllib.parse
from difflib import SequenceMatcher

from flask import (Flask, request, jsonify, render_template, Response, send_file,
                   make_response, stream_with_context, redirect)
from werkzeug.serving import make_server

import db_manager as db
import transform as tf
import clean
import aggmap

# 持有服务器对象，供「终止服务」接口调用
SERVER = None

DB_PATH = db.DB_PATH
KNOWN_TABLES = {t for t, _, _ in db.FILE_MAP.values()}
# 单独导入维护的维度表（非 FILE_MAP 批量导入来源），需在此显式注册
KNOWN_TABLES.add("fct_company_location")
# 供应商名称归并映射（supplier_dedup.py 生成）
KNOWN_TABLES.add("dim_supplier_alias")

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
    "dim_supplier_alias": ("供应商别名", "供应商名称变体 → 规范名（空格/大小写/标点归并）"),
    # 事实 / 交易表
    "fct_supplier": ("供应商列表", "供应商准入 / 资格提交记录（事实表）"),
    "fct_preapproval": ("集采前置审批", "集采项目前置审批单"),
    "fct_framework_agreement": ("框架协议", "框架合同 / 协议"),
    "fct_bom": ("物料清单", "框架协议下的物料明细"),
    "fct_prpo": ("采购申请 PRPO", "采购申请单（PR / PO 一体表）"),
    "fct_po": ("采购订单 PO", "采购订单（含金额，数据量最大）"),
    "fct_company_location": ("公司位置(经纬度)", "公司门店中文名 → 省/市/区经纬度（地理事实表）"),
    # 分析视图（只读）
    "vw_fa_analysis": ("框架协议分析", "框架协议为主视角：带出前置审批预估金额与 PRPO 实际采购额"),
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
    # 维度里若存在「同一写法的多个变体」（空格/下划线/全角括号差异），
    # 相同相似度时选写法更规整的那个当目标 —— 否则补录会把值归到另一个变体上，
    # 分析时仍然被拆成两组，等于白洗。
    ugly = {v: clean.ugliness(v) for v in rows}
    out = {}
    for k in keys:
        best_v, best_s, best_rank = None, 0.0, None
        for v in rows:
            s = key_similarity(k, v)
            rank = (round(s, 6), -ugly[v], len(str(v)))
            if best_rank is None or rank > best_rank:
                best_s, best_v, best_rank = s, v, rank
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
    # 目标为事实表：区分「维度→参照型事实表」与「事实→事实」
    if not fact.startswith("fct_"):
        return ("not_needed",
                "目标为参照型事实表（如公司位置）：系统默认不自动补齐，但可在数据质量页手动补录——"
                "补的是参照主数据、非交易流水（需二次确认并先备份 procurement.db）。")
    return ("not_needed",
            "目标为事实表：事实表之间多为「业务上本就不引用」的正常缺口，不建议补录。")


app = Flask(__name__, template_folder="templates", static_folder="static")
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024  # 上传上限 50MB
# 模板热重载：改 board.html / index.html 后无需重启服务
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.jinja_env.auto_reload = True

# 单页看板（/api/board/*）—— 参考 Sime Darby Motors HK 采购支出分析 POC
try:
    from board_api import board_bp
    app.register_blueprint(board_bp)
except Exception as _e:  # noqa: BLE001
    print("！看板模块未加载：%s" % _e)


# SQLite 连接级调优（PRAGMA 不跨连接持久化，每次 connect 都要重设）
#   cache_size=-65536 → 64MB 页缓存（默认仅 2MB，对 100MB+ 的库命中率太低）
#   busy_timeout=8000 → 并发写冲突时最多等 8s 再报 locked（默认 0：立刻抛错）
#   temp_store=MEMORY → 排序 / 临时表走内存
_CON_PRAGMAS = (
    "PRAGMA cache_size=-65536",
    "PRAGMA busy_timeout=8000",
    "PRAGMA temp_store=MEMORY",
)


def get_con():
    con = sqlite3.connect(DB_PATH, timeout=8.0)
    con.row_factory = sqlite3.Row
    for p in _CON_PRAGMAS:
        con.execute(p)
    return con


# ---------------------------------------------------------------------------
# 访问控制
#   DBADMIN_TOKEN 未设置 → 完全沿用原行为（本机零门槛）。
#   设置后 → 所有请求需带 token：首访 ?token=xxx，通过后写 cookie（HttpOnly），
#            之后浏览器自动携带；也可用 X-Token 头（脚本/自动化）。
# ---------------------------------------------------------------------------
ACCESS_TOKEN = (os.environ.get("DBADMIN_TOKEN", "") or "").strip()


def _req_token():
    return (request.args.get("token") or request.headers.get("X-Token")
            or request.cookies.get("dbadmin_token") or "")


# 预热线程用内部 test_client 打自己的接口，没有 token/cookie；
# 用线程局部标记放行，避免设了口令时预热全被挡成 401。
_WARMUP_TLS = threading.local()


@app.before_request
def _access_guard():
    if not ACCESS_TOKEN or getattr(_WARMUP_TLS, "on", False):
        return None
    tok = _req_token()
    if tok and hmac.compare_digest(tok, ACCESS_TOKEN):
        return None
    return Response("需要访问口令：请在地址后追加 ?token=你的口令", status=401,
                    mimetype="text/plain")


@app.after_request
def _remember_token(resp):
    """用 ?token= 首次登录成功后写 cookie，后续导航就不必再带参数。"""
    if ACCESS_TOKEN and request.args.get("token") == ACCESS_TOKEN:
        resp.set_cookie("dbadmin_token", ACCESS_TOKEN, max_age=7 * 86400,
                        httponly=True, samesite="Lax")
    return resp


# ---------------------------------------------------------------------------
# 聚合维度表（dim_agg_*，由「映射聚合」动态创建）
#   动态建出来的表必须登记进 KNOWN_TABLES，否则侧栏 / 模型视图 / 关系下拉都看不到。
#   启动时从 agg_task 恢复，建/删任务时同步增删。
# ---------------------------------------------------------------------------
AGG_TABLES = {}


def register_agg_table(dim_table, name, source_table, source_col, value_col="聚合值"):
    AGG_TABLES[dim_table] = {"name": name, "source_table": source_table,
                             "source_col": source_col, "value_col": value_col}
    KNOWN_TABLES.add(dim_table)
    TABLE_LABELS[dim_table] = (
        "聚合·" + (name or dim_table)[:20],
        f"由 {source_table}.{source_col} 聚合生成（{value_col} 供可视化分组）")


def unregister_agg_table(dim_table):
    AGG_TABLES.pop(dim_table, None)
    KNOWN_TABLES.discard(dim_table)
    TABLE_LABELS.pop(dim_table, None)


def load_agg_meta():
    """启动时把已建的聚合维度表重新登记（幂等）。

    顺带把历史表里的「源行数 / 写法数」两列清掉 —— 维度表只留
    「原始值 + 聚合值（+ 附加字段）」，统计口径不再落库。
    """
    try:
        con = get_con()
        try:
            rows = con.execute(
                "SELECT dim_table,name,source_table,source_col,value_col FROM agg_task").fetchall()
            try:
                dropped = aggmap.drop_stats_columns(con)
                if dropped:
                    con.commit()
                    print("[aggmap] 已清理统计列：",
                          "，".join(f"{d['table']}({'/'.join(d['dropped'])})"
                                   for d in dropped), flush=True)
            except sqlite3.Error as e:
                print("[aggmap] 清理统计列失败：", e, flush=True)
        finally:
            con.close()
    except sqlite3.Error:
        return
    for r in rows:
        register_agg_table(r["dim_table"], r["name"], r["source_table"],
                           r["source_col"], r["value_col"])


# ---------------------------------------------------------------------------
# 独立维度表（「导入数据 → 新建独立维度表」创建）
#   与映射聚合不同：把一份现成的映射/字典 CSV 原样导成一张表，不动事实表，
#   不生成 FOREIGN KEY（避免维度表下线后留下悬空外键），关系由用户在模型视图建。
#   元数据落 imported_table 并启动恢复，否则重启后 KNOWN_TABLES 回到常量、侧栏丢表。
# ---------------------------------------------------------------------------
IMPORTED_TABLES = {}
_NEWTABLE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


def ensure_imported_table(con):
    con.execute(
        "CREATE TABLE IF NOT EXISTS imported_table ("
        " table_name TEXT PRIMARY KEY,"
        " label TEXT NOT NULL,"
        " descr TEXT,"
        " pk_col TEXT,"
        " source_file TEXT,"
        " row_count INTEGER,"
        " created_at TEXT)")
    con.commit()


def register_imported_table(table, label, descr, pk_col, source_file, row_count):
    IMPORTED_TABLES[table] = {"label": label, "descr": descr, "pk_col": pk_col,
                              "source_file": source_file, "row_count": row_count}
    KNOWN_TABLES.add(table)
    TABLE_LABELS[table] = (label, descr or "")


def load_imported_tables():
    """启动时重新登记独立维度表（幂等；表被手工 DROP 掉就不登记）。"""
    try:
        con = get_con()
        try:
            ensure_imported_table(con)
            # 不调用 _existing_tables：本函数在文件里定义得比它早
            exist = {r[0] for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            rows = con.execute(
                "SELECT table_name,label,descr,pk_col,source_file,row_count "
                "FROM imported_table").fetchall()
        finally:
            con.close()
    except sqlite3.Error:
        return
    for r in rows:
        if r["table_name"] not in exist:
            continue
        register_imported_table(r["table_name"], r["label"], r["descr"],
                                r["pk_col"], r["source_file"], r["row_count"])


# 确保元数据表（映射配置 / 质量规则）存在（幂等；仅在 map_config 表首次新建时
# 用 FKS 播种基线，已存在即使为空也不再自动补回，尊重用户清空映射的决定）。
# 注意：必须放在 get_con 定义之后，否则会 NameError 被静默吞掉。
try:
    _mc = get_con(); db.ensure_meta_tables(_mc); _mc.close()
except Exception:
    pass
load_agg_meta()
load_imported_tables()


def get_pk(table, con):
    for r in con.execute(f"PRAGMA table_info({db.qid(table)})").fetchall():
        if r["pk"]:
            return r["name"]
    return None


def auto_pk_cols(table, con):
    """返回可以自动分配取值的主键列（集合，通常 0 或 1 个元素）。

    SQLite 里单列 `INTEGER PRIMARY KEY` 是 rowid 的别名，插入 NULL / 省略该列时
    自动取 max(rowid)+1，建表语句写不写 AUTOINCREMENT 都一样。前端据此在「新增一行」
    时把该字段置灰并提示自动生成，避免用户以为 ID 必须手填。
    复合主键、TEXT 主键（如事实表的「表单编号」）都不在返回结果里。
    """
    try:
        info = con.execute(f"PRAGMA table_info({db.qid(table)})").fetchall()
    except sqlite3.Error:
        return set()
    pks = [r for r in info if r["pk"]]
    if len(pks) != 1:
        return set()
    return {pks[0]["name"]} if (pks[0]["type"] or "").strip().upper() == "INTEGER" else set()


def _existing_tables(con):
    """库里真实存在的表 / 视图名集合。

    KNOWN_TABLES、FILE_MAP、TABLE_LABELS 都是代码里的常量，允许库里暂时没有这些表
    （例如维度表被手动 DROP 掉、重建聚合维度表替代）。凡是要按常量遍历表的接口，
    都必须先跟这个集合求交集，否则侧栏、导入下拉、聚合页下拉、模型视图会列出
    点进去就报错的空壳表。
    """
    try:
        return {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table','view') "
            "AND name NOT LIKE 'sqlite_%'")}
    except sqlite3.Error:
        return set()


def _table_meta():
    out = []
    con = get_con()
    seen = set()
    exist = _existing_tables(con)
    # 先按 FILE_MAP 顺序输出 15 张核心表
    for fn in db.LOAD_ORDER:
        t, pk, kind = db.FILE_MAP[fn]
        if t not in exist:
            continue
        out.append({"table": t, "source": fn, "kind": kind, "pk": pk})
        seen.add(t)
    # 补充 KNOWN_TABLES 中手动导入的额外表（如 fct_company_location）：按 fct_/dim_ 前缀判定 fact/dim
    for t in sorted(KNOWN_TABLES - seen):
        if t not in exist:
            continue
        pk = None
        for r in con.execute(f"PRAGMA table_info({db.qid(t)})").fetchall():
            if r["pk"]:
                pk = r["name"]
                break
        tkind = "fact" if t.startswith("fct_") else "dim"
        src = ("(聚合维度表)" if t in AGG_TABLES else
               "(独立维度表)" if t in IMPORTED_TABLES else "(手动导入表)")
        out.append({"table": t, "source": src, "kind": tkind, "pk": pk})
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


@app.route("/dash")
def dash_page():
    """采购支出分析看板（单页）：参考 Sime Darby Motors HK 的 POC 结构，数据来自 dash_po"""
    # 静态资源用 mtime 作版本号，改完 JS/CSS 刷新即生效，不用清缓存
    def _stamp(rel):
        try:
            return int(os.path.getmtime(os.path.join(app.static_folder, rel)))
        except OSError:
            return 0

    resp = make_response(render_template(
        "board.html",
        v_css=_stamp("board/board.css"),
        v_js=_stamp("board/board.js"),
    ))
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    return resp


@app.route("/dash/general")
@app.route("/dash/analysis")
def dash_legacy_redirect():
    """旧版 page 已下线，统一回到单页看板"""
    return redirect("/dash", code=302)


@app.route("/favicon.ico")
def favicon():
    icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "procurement.ico")
    if os.path.exists(icon_path):
        return send_file(icon_path, mimetype="image/x-icon")
    return ("", 204)


# ---------------------------------------------------------------------------
# 离线看板快照（单文件 HTML）—— 按需点击导出，不在每次改前端后自动重跑
#   POST /api/board/export          起后台线程跑 `python board_export.py`
#   GET  /api/board/export/status   轮询进度 + exports/ 下的历史文件
#   GET  /api/board/export/download 下载导出的文件（只允许 exports/ 下的 .html）
#
# 为什么用子进程而不是 import board_export：
#   `python app.py` 启动时本模块叫 __main__，board_export 里的 `from app import app`
#   会把 app.py 再完整加载一遍（第二个 Flask 实例 + 重新初始化元数据），
#   子进程既避免二次加载，也不会把导出耗时卡在 Web 线程里。
# ---------------------------------------------------------------------------
_APP_DIR = os.path.dirname(os.path.abspath(__file__))
EXPORT_DIR = os.path.join(_APP_DIR, "exports")
_EXPORT_JOB = {
    "state": "idle",      # idle / running / done / error
    "started": 0.0, "ended": 0.0,
    "name": "", "path": "", "size": 0, "rows": 0, "agreements": 0,
    "error": "", "tail": "",
}
_EXPORT_LOCK = threading.Lock()


def _export_files(limit=8):
    """exports/ 目录下最近的单文件看板（新 → 旧）。"""
    try:
        names = [n for n in os.listdir(EXPORT_DIR)
                 if n.lower().endswith(".html") and not n.startswith(".")]
    except OSError:
        return []
    items = []
    for n in names:
        p = os.path.join(EXPORT_DIR, n)
        try:
            st = os.stat(p)
        except OSError:
            continue
        items.append({"name": n, "size": st.st_size,
                      "mtime": int(st.st_mtime)})
    items.sort(key=lambda x: x["mtime"], reverse=True)
    return items[:limit]


def _export_job_view():
    j = dict(_EXPORT_JOB)
    j["elapsed"] = round((j["ended"] or time.time()) - j["started"], 1) \
        if j["started"] else 0
    return j


def _run_board_export():
    """后台线程：子进程跑 board_export.py，把结果写回 _EXPORT_JOB。"""
    global _EXPORT_JOB
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
    try:
        proc = subprocess.run(
            [sys.executable, "board_export.py"],
            cwd=_APP_DIR, env=env, timeout=900,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
        out = (proc.stdout or b"").decode("utf-8", "replace")
        if proc.returncode != 0:
            raise RuntimeError("board_export.py 退出码 %s" % proc.returncode)
        m_rows = re.search(r"rows\s+([\d,]+)\s*/\s*agreements\s+(\d+)", out)
        if not m_rows:
            raise RuntimeError("导出输出里没有行数信息，请检查脚本")
        # 输出行形如：输出：D:\...\exports\采购看板_单文件_20261123.html  4.68 MB
        m_out = re.search(r"输出：(.+?\.html)", out)
        path = m_out.group(1).strip() if m_out else ""
        if not (path and os.path.exists(path)):
            newest = _export_files(1)
            path = os.path.join(EXPORT_DIR, newest[0]["name"]) if newest else ""
        if not (path and os.path.exists(path)):
            raise RuntimeError("导出脚本执行完毕但没找到产出文件")
        st = os.stat(path)
        with _EXPORT_LOCK:
            _EXPORT_JOB.update(
                state="done", ended=time.time(),
                name=os.path.basename(path), path=path, size=st.st_size,
                rows=int(m_rows.group(1).replace(",", "")),
                agreements=int(m_rows.group(2)),
                error="", tail=out.strip().splitlines()[-1] if out.strip() else "",
            )
    except Exception as exc:  # noqa: BLE001 —— 任何失败都要落成界面可读的状态
        with _EXPORT_LOCK:
            _EXPORT_JOB.update(state="error", ended=time.time(),
                               error=str(exc)[:500], path="", name="", size=0)


@app.route("/api/board/export", methods=["POST"])
def api_board_export():
    with _EXPORT_LOCK:
        if _EXPORT_JOB["state"] == "running":
            return jsonify({"error": "已有导出任务在进行中，请稍候"}), 409
        _EXPORT_JOB.update(state="running", started=time.time(), ended=0.0,
                           name="", path="", size=0, rows=0, agreements=0,
                           error="", tail="")
    threading.Thread(target=_run_board_export, daemon=True).start()
    return jsonify({"ok": True, "job": _export_job_view()})


@app.route("/api/board/export/status")
def api_board_export_status():
    return jsonify({"job": _export_job_view(), "files": _export_files(),
                    "dir": EXPORT_DIR})


@app.route("/api/board/export/download")
def api_board_export_download():
    name = os.path.basename((request.args.get("f") or _EXPORT_JOB.get("name") or "").strip())
    path = os.path.join(EXPORT_DIR, name)
    if not name.lower().endswith(".html") or not os.path.isfile(path):
        return jsonify({"error": "文件不存在：%s" % name}), 404
    return send_file(path, as_attachment=True, download_name=name,
                     mimetype="text/html")


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
                    "label": table_label(t), "desc": table_desc(t), "readonly": False})
    # 追加「数据转换」产出的视图：可浏览、可查询，但只读（不能直接改视图数据）
    try:
        tf_label = {r["name"]: r["label"] for r in
                    con.execute("SELECT name, label FROM transform_query").fetchall()}
    except sqlite3.Error:
        tf_label = {}
    seen = {x["table"] for x in res}
    try:
        views = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='view' AND name NOT LIKE 'sqlite_%' "
            "ORDER BY name").fetchall()]
    except sqlite3.Error:
        views = []
    for v in views:
        if v in seen:
            continue
        try:
            n = con.execute(f"SELECT COUNT(*) FROM {db.qid(v)}").fetchone()[0]
        except sqlite3.Error:
            n = None
        res.append({"table": v, "source": "(转换视图)" if v in tf_label else "(分析视图)",
                    "kind": "view", "pk": None, "rows": n,
                    "label": tf_label.get(v) or table_label(v),
                    "desc": table_desc(v) or "数据转换产出（只读）", "readonly": True})
    con.close()
    return jsonify(res)


# ---------------------------------------------------------------------------
# 表结构
# ---------------------------------------------------------------------------
@app.route("/api/tables/<table>/schema")
def api_schema(table):
    if not _readable(table):
        return jsonify({"error": "未知表"}), 404
    con = get_con()
    autos = auto_pk_cols(table, con)
    cols = []
    for r in con.execute(f"PRAGMA table_info({db.qid(table)})").fetchall():
        cols.append({"name": r["name"], "type": r["type"], "pk": r["pk"],
                     "notnull": r["notnull"], "auto": r["name"] in autos})
    con.close()
    return jsonify(cols)


# ---------------------------------------------------------------------------
# 删除表（不可逆）
#   两道闸：① 存在阻塞项一律不动（元数据表 / 视图 / 转换查询引用 / 外键引用）；
#          ② 必须把表名原样回填才执行，默认先整库备份。
#   连带清理：映射关系、模型布局、质量规则、指向该表的聚合任务、独立维度表登记。
# ---------------------------------------------------------------------------
_META_TABLES = {"map_config", "agg_task", "imported_table", "map_layout",
                "quality_rules", "transform_query"}


def _db_backup(tag, keep=5):
    """写库前整库备份，按 tag 分文件，保留最老 1 份 + 最新 keep-1 份。

    只清理同 tag 前缀的文件（不碰其他 tag，也不碰用户手工放的备份）。
    """
    try:
        bdir = os.path.join(os.path.dirname(os.path.abspath(DB_PATH)), "backups")
        os.makedirs(bdir, exist_ok=True)
        prefix = f"procurement_before_{tag}_"
        path = os.path.join(bdir, prefix + time.strftime("%Y%m%d_%H%M%S") + ".db")
        shutil.copy2(DB_PATH, path)
        olds = sorted(f for f in os.listdir(bdir)
                      if f.startswith(prefix) and f.endswith(".db"))
        keep_set = set(olds[:1])
        if keep > 1 and len(olds) > 1:
            keep_set |= set(olds[-(keep - 1):])
        for f in olds:
            if f in keep_set:
                continue
            try:
                os.remove(os.path.join(bdir, f))
            except OSError:
                pass
        return path
    except OSError:
        return None


def _drop_info(con, table):
    """删表前的影响评估：blockers 非空则不允许删。"""
    info = {"table": table, "label": table_label(table), "rows": None,
            "relations": [], "agg_tasks": [], "agg_source_tasks": [],
            "imported": False, "quality_rules": 0, "layout": False,
            "transform": [], "fk_refs": [], "blockers": []}
    otype = _obj_type(table)
    if table in _META_TABLES or str(table).startswith("sqlite_"):
        info["blockers"].append("这是系统元数据表，不允许删除")
        return info
    if otype is None:
        info["blockers"].append("库里没有这张表（可能已被删除）")
        return info
    if otype == "view":
        info["blockers"].append("这是视图，不是物理表；请到「数据转换」删除对应查询")
        return info
    try:
        info["rows"] = con.execute(f"SELECT COUNT(*) FROM {db.qid(table)}").fetchone()[0]
    except sqlite3.Error:
        pass
    # 该表参与的映射关系（作为事实表侧或维度表侧都会被连带删除）
    try:
        info["relations"] = [dict(r) for r in con.execute(
            "SELECT id,source_table,source_col,target_table,target_col FROM map_config "
            "WHERE target_table=? OR source_table=? ORDER BY id", (table, table)).fetchall()]
    except sqlite3.Error:
        pass
    # 以该表为「源」的聚合任务：删了源，映射就失去依据 → 阻塞
    try:
        info["agg_source_tasks"] = [dict(r) for r in con.execute(
            "SELECT id,name,dim_table FROM agg_task WHERE source_table=? ORDER BY id",
            (table,)).fetchall()]
    except sqlite3.Error:
        pass
    # 该表自己是聚合维度表 → 连带删任务
    try:
        info["agg_tasks"] = [dict(r) for r in con.execute(
            "SELECT id,name FROM agg_task WHERE dim_table=? ORDER BY id", (table,)).fetchall()]
    except sqlite3.Error:
        pass
    try:
        info["imported"] = bool(con.execute(
            "SELECT 1 FROM imported_table WHERE table_name=?", (table,)).fetchone())
    except sqlite3.Error:
        pass
    try:
        info["quality_rules"] = con.execute(
            "SELECT COUNT(*) FROM quality_rules WHERE target_table=?", (table,)).fetchone()[0]
    except sqlite3.Error:
        pass
    try:
        info["layout"] = bool(con.execute(
            "SELECT 1 FROM map_layout WHERE table_name=?", (table,)).fetchone())
    except sqlite3.Error:
        pass
    # 数据转换：产出同名对象，或把该表当源 → 都阻塞（先处理转换查询）
    try:
        info["transform"] = [dict(r) for r in con.execute(
            "SELECT id,name,label,source_table FROM transform_query "
            "WHERE name=? OR source_table=? ORDER BY id", (table, table)).fetchall()]
    except sqlite3.Error:
        pass
    # 真正的 FOREIGN KEY（本库建表不写外键，但可能有手工建的）
    try:
        for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' "
                             "AND name NOT LIKE 'sqlite_%'").fetchall():
            for fk in con.execute(f"PRAGMA foreign_key_list({db.qid(r['name'])})").fetchall():
                if fk["table"] == table:
                    info["fk_refs"].append(f"{r['name']}.{fk['from']} → {fk['to']}")
    except sqlite3.Error:
        pass

    if info["agg_source_tasks"]:
        info["blockers"].append(
            f"有 {len(info['agg_source_tasks'])} 个映射聚合任务以该表为源，"
            "请先到「映射聚合」删除这些任务")
    if info["transform"]:
        names = "、".join(x["name"] for x in info["transform"])
        info["blockers"].append(f"数据转换里有查询引用该表（{names}），请先删除或改源")
    if info["fk_refs"]:
        info["blockers"].append("有外键约束引用该表：" + "、".join(info["fk_refs"]))
    return info


@app.route("/api/tables/<table>/drop-info")
def api_drop_info(table):
    """返回删表的影响评估（blockers 非空 = 不允许删，前端据此禁用确认）。"""
    con = get_con()
    try:
        info = _drop_info(con, table)
    finally:
        con.close()
    return jsonify(info)


@app.route("/api/tables/<table>", methods=["DELETE"])
def api_drop_table(table):
    """删表：必须回填表名确认；默认整库备份；连带清掉引用它的元数据。"""
    body = request.get_json(silent=True) or {}
    confirm = str(body.get("confirm") or request.args.get("confirm") or "").strip()
    raw_bk = body.get("backup") if "backup" in body else request.args.get("backup", "1")
    want_backup = str(raw_bk).strip().lower() not in ("0", "false", "no", "off")

    con = get_con()
    try:
        info = _drop_info(con, table)
        if info["blockers"]:
            return jsonify({"ok": False, "error": "；".join(info["blockers"]),
                            "info": info}), 400
        if confirm != table:
            return jsonify({"ok": False, "error": "确认表名不一致，未执行删除",
                            "info": info}), 400
        backup_path = _db_backup("drop") if want_backup else None
        counts = {"relations": 0, "layout": 0, "quality_rules": 0,
                  "agg_tasks": 0, "imported": 0}
        try:
            counts["relations"] = con.execute(
                "DELETE FROM map_config WHERE target_table=? OR source_table=?",
                (table, table)).rowcount
            counts["layout"] = con.execute(
                "DELETE FROM map_layout WHERE table_name=?", (table,)).rowcount
            counts["quality_rules"] = con.execute(
                "DELETE FROM quality_rules WHERE target_table=?", (table,)).rowcount
            counts["agg_tasks"] = con.execute(
                "DELETE FROM agg_task WHERE dim_table=?", (table,)).rowcount
            counts["imported"] = con.execute(
                "DELETE FROM imported_table WHERE table_name=?", (table,)).rowcount
            con.execute(f"DROP TABLE IF EXISTS {db.qid(table)}")
            con.commit()
        except sqlite3.Error as e:
            con.rollback()
            return jsonify({"ok": False, "error": f"删除失败：{e}"}), 500
        # 内存里的登记一并撤掉，否则侧栏/模型视图还留着空壳
        unregister_agg_table(table)
        IMPORTED_TABLES.pop(table, None)
        KNOWN_TABLES.discard(table)
        TABLE_LABELS.pop(table, None)
        parts = []
        if counts["relations"]:
            parts.append(f"关系 {counts['relations']} 条")
        if counts["agg_tasks"]:
            parts.append(f"聚合任务 {counts['agg_tasks']} 个")
        if counts["quality_rules"]:
            parts.append(f"质量规则 {counts['quality_rules']} 条")
        if counts["imported"]:
            parts.append("独立维度表登记")
        if counts["layout"]:
            parts.append("模型布局")
        msg = f"已删除表「{info['label'] or table}」（{(info['rows'] or 0):,} 行）"
        if parts:
            msg += "，同时清理：" + "、".join(parts)
        if backup_path:
            msg += "；备份：" + os.path.basename(backup_path)
        return jsonify({"ok": True, "message": msg, "counts": counts,
                        "backup": os.path.basename(backup_path) if backup_path else None})
    finally:
        con.close()


# ---------------------------------------------------------------------------
# 数据浏览（分页 / 搜索 / 排序 / 列筛选）
# ---------------------------------------------------------------------------
_NUM_OPS = {"gt": ">", "gte": ">=", "lt": "<", "lte": "<="}


_NUM_TYPES = {"REAL", "INTEGER", "NUMERIC", "DOUBLE", "FLOAT", "DECIMAL"}


def _is_num_type(t):
    """声明类型是不是数值。

    注意 SQLite 只按声明判断。本库大量金额列声明 REAL、实际存的是
    '¥30,000.00' 这类文本（Excel 导出的原样），裸 CAST 会得到 0 —— 所以
    凡是要参与运算的地方都必须套 transform.num_expr() 先洗币符和千分位。
    """
    return (t or "").upper().split("(")[0].strip() in _NUM_TYPES


def _like_esc(v):
    """转义 LIKE 通配符。

    必须配合 SQL 里的 ESCAPE '\' 使用，否则用户搜索 "_" 会匹配任意单字符、
    搜索 "50%" 会退化成前缀模糊匹配 —— 实测搜 "_" 会返回全表。
    """
    return (str(v).replace("\\", "\\\\").replace("%", "\\%")
            .replace("_", "\\_"))


def _build_where(cols, coltype, search, filters):
    """构造 WHERE 子句：全局搜索(OR) 与 列筛选(AND) 组合。"""
    clauses, params = [], []
    if search:
        likes = []
        for c in cols:
            likes.append(f"{db.qid(c)} LIKE ? ESCAPE '\\'")
            params.append(f"%{_like_esc(search)}%")
        clauses.append("(" + " OR ".join(likes) + ")")
    for f in (filters or []):
        col = f.get("col"); op = f.get("op"); val = f.get("val")
        if col not in cols or not op:
            continue
        qc = db.qid(col)
        is_num = _is_num_type(coltype.get(col))
        if op == "empty":
            clauses.append(f"({qc} IS NULL OR {qc} = '')")
        elif op == "notempty":
            clauses.append(f"({qc} IS NOT NULL AND {qc} != '')")
        elif op in ("in", "notin"):
            # 表头下拉筛选：按「取值集合」精确匹配（Excel 式自动筛选）。
            # 直接用原列比较、不做数值转换 —— 下拉里列出的就是库里存的原文
            # （金额可能是 '¥30,000.00'），走 num_expr 反而匹配不上。
            vals = f.get("vals")
            if vals is None:
                vals = [val] if val not in (None, "") else []
            elif isinstance(vals, (str, int, float)):
                vals = [vals]
            vals = [str(v) for v in vals if v is not None]
            parts = []
            if vals:
                parts.append(f"{qc} IN ({','.join('?' * len(vals))})")
                params.extend(vals)
            if f.get("inclEmpty"):
                parts.append(f"({qc} IS NULL OR {qc} = '')")
            if not parts:
                continue
            expr = "(" + " OR ".join(parts) + ")"
            clauses.append(expr if op == "in" else f"NOT {expr}")
        elif op == "contains":
            clauses.append(f"{qc} LIKE ? ESCAPE '\\'"); params.append(f"%{_like_esc(val)}%")
        elif op == "startswith":
            clauses.append(f"{qc} LIKE ? ESCAPE '\\'"); params.append(f"{_like_esc(val)}%")
        elif op in ("eq", "ne"):
            if is_num:
                try:
                    v = float(val)
                except (TypeError, ValueError):
                    continue
                clauses.append(f"{tf.num_expr(qc)} {'=' if op == 'eq' else '!='} ?"); params.append(v)
            else:
                clauses.append(f"{qc} {'=' if op == 'eq' else '!='} ?"); params.append(val)
        elif op in _NUM_OPS:
            try:
                v = float(val)
            except (TypeError, ValueError):
                continue
            clauses.append(f"{tf.num_expr(qc)} {_NUM_OPS[op]} ?"); params.append(v)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    return where, params


@app.route("/api/tables/<table>/rows")
def api_rows(table):
    if not _readable(table):
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
            # 数值列必须按数值排，否则 '¥9,362.05' 会按字典序排在 '¥30,000.00' 前面
            sort_expr = (tf.num_expr(db.qid(sort)) if _is_num_type(coltype.get(sort))
                         else db.qid(sort))
            order_sql = f" ORDER BY {sort_expr} {'DESC' if order == 'desc' else 'ASC'}"

        offset = (page - 1) * size
        sql = f"SELECT * FROM {db.qid(table)}{where}{order_sql} LIMIT ? OFFSET ?"
        rows = [dict(r) for r in con.execute(sql, params + [size, offset]).fetchall()]

        # 数字列汇总（对当前筛选后的全集计算，非仅本页）
        # num_expr 先洗 ¥/￥/$/HK$/千分位/空格，再 CAST —— 直接用 CAST 会把
        # '¥30,000.00' 静默算成 0，合计恒等于 0
        # 主键列排除在外：自增 id 是 INTEGER，算 Σ 没有业务含义（1..62 合计 1953），
        # 只会让表底多出一个莫名其妙的数字；业务主键（单号等）同理不该求和
        pk_col = get_pk(table, con)
        num_cols = [c for c in cols if _is_num_type(coltype.get(c)) and c != pk_col]
        aggregates = {}
        if num_cols:
            # 物化 CTE：num_expr 是 6 层嵌套 REPLACE，同一列的表达式原本被
            # SUM/AVG/MIN/MAX 各算一遍（4 次）。实测 fct_po（4 列 × 4 次）
            # 671ms → 185ms，结果完全一致。COUNT 用 v{i} 与原列等价：
            # num_expr 对 NULL 返回 NULL，对任何非 NULL 返回数值（哪怕 0.0）。
            sel = ", ".join(f"{tf.num_expr(db.qid(c))} AS v{i}"
                            for i, c in enumerate(num_cols))
            parts = []
            for i in range(len(num_cols)):
                parts.append(f"SUM(v{i}) AS s{i}")
                parts.append(f"AVG(v{i}) AS a{i}")
                parts.append(f"MIN(v{i}) AS mn{i}")
                parts.append(f"MAX(v{i}) AS mx{i}")
                parts.append(f"COUNT(v{i}) AS n{i}")
            arow = con.execute(
                f"WITH _m AS MATERIALIZED (SELECT {sel} FROM {db.qid(table)}{where}) "
                f"SELECT {', '.join(parts)} FROM _m", params).fetchone()
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
# 列取值下拉（表头自动筛选）：某一列的去重取值 + 出现次数
# ---------------------------------------------------------------------------
@app.route("/api/tables/<table>/values")
def api_col_values(table):
    """给表头下拉筛选列出该列的真实取值。

    直接 GROUP BY 原列取前 N 个（按出现次数降序），额外回报：
      distinct  该列共有多少种取值
      empty     空值（NULL / 空串）行数，供勾「（空值）」
      truncated 是否被 limit 截断（截断时前端提示「可搜索」）
    带 q 时在服务端 LIKE 过滤，避免把 5000 个供应商全推给前端。
    """
    if not _readable(table):
        return jsonify({"error": "未知表"}), 404
    col = (request.args.get("col") or "").strip()
    q = (request.args.get("q") or "").strip()
    try:
        limit = max(1, min(500, int(request.args.get("limit", 200))))
    except (TypeError, ValueError):
        limit = 200
    con = get_con()
    try:
        info = con.execute(f"PRAGMA table_info({db.qid(table)})").fetchall()
        if col not in [r["name"] for r in info]:
            return jsonify({"error": "未知字段"}), 400
        qc = db.qid(col)
        tn = db.qid(table)
        where = f"{qc} IS NOT NULL AND {qc} != ''"
        params = []
        if q:
            where += f" AND {qc} LIKE ? ESCAPE '\\'"
            params.append(f"%{_like_esc(q)}%")
        # 多取一条用来判断是否被截断
        rows = con.execute(
            f"SELECT {qc} AS v, COUNT(*) AS n FROM {tn} WHERE {where} "
            f"GROUP BY 1 ORDER BY n DESC, 1 ASC LIMIT ?", params + [limit + 1]).fetchall()
        truncated = len(rows) > limit
        rows = rows[:limit]
        distinct = con.execute(
            f"SELECT COUNT(DISTINCT {qc}) FROM {tn} "
            f"WHERE {qc} IS NOT NULL AND {qc} != ''").fetchone()[0] or 0
        empty = con.execute(
            f"SELECT COUNT(*) FROM {tn} WHERE {qc} IS NULL OR {qc} = ''").fetchone()[0] or 0
        return jsonify({"col": col, "values": [{"v": r["v"], "n": r["n"]} for r in rows],
                        "distinct": distinct, "empty": empty,
                        "limit": limit, "truncated": truncated,
                        "num": _is_num_type(dict(
                            (r["name"], r["type"]) for r in info).get(col))})
    except sqlite3.Error as e:
        return jsonify({"error": str(e)}), 500
    finally:
        con.close()


# ---------------------------------------------------------------------------
# 导出当前视图为 CSV（应用当前的搜索 / 列筛选 / 排序 / 列选择）
# ---------------------------------------------------------------------------
# 导出分批大小：8000 行/批（约 4-9MB）。太小 → socket 写次数多、总时长变长；
# 太大 → 内存峰值和首字节延迟上升。2000 行实测比 8000 行慢约 40%。
_EXPORT_CHUNK = 8000


@app.route("/api/tables/<table>/export")
def api_export(table):
    if not _readable(table):
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
        sql = f"SELECT {col_list} FROM {db.qid(table)}{where}{order_sql}"
        ascii_name = f"{table}_export.csv"
        utf8_name = urllib.parse.quote(f"{table_label(table)}_{table}_导出.csv")

        def _gen():
            """分批 fetchmany 边查边写：内存占用与总行数无关。

            原来是 fetchall + StringIO 全量驻留，fct_prpo 5 万行就要 53MB，
            数据涨到几十万行有 OOM 风险；改为生成器后峰值只与 _EXPORT_CHUNK 有关。
            这里单开只读连接（请求期的 con 在响应返回时就关了，生成器是之后才迭代的）。
            """
            c = sqlite3.connect("file:" + DB_PATH.replace("\\", "/") + "?mode=ro", uri=True)
            try:
                cur = c.execute(sql, params)      # 不设 row_factory：按下标取值比按列名快
                buf = io.StringIO()
                w = csv.writer(buf)
                w.writerow(export_cols)
                yield "\ufeff" + buf.getvalue()
                buf.seek(0); buf.truncate(0)
                while True:
                    rows = cur.fetchmany(_EXPORT_CHUNK)
                    if not rows:
                        break
                    w.writerows(rows)             # 列顺序 = export_cols 顺序，无需再映射
                    yield buf.getvalue()
                    buf.seek(0); buf.truncate(0)
            finally:
                c.close()

        resp = Response(stream_with_context(_gen()),
                        mimetype="text/csv; charset=utf-8",
                        headers={"Content-Disposition":
                                 f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{utf8_name}"})
        # 反向代理（若有）不要缓冲，否则流式白做；直连时此头无副作用
        resp.headers["X-Accel-Buffering"] = "no"
        return resp
    finally:
        con.close()


# ---------------------------------------------------------------------------
# 新增一行
# ---------------------------------------------------------------------------
@app.route("/api/tables/<table>/rows", methods=["POST"])
def api_insert(table):
    if table not in KNOWN_TABLES:
        if _obj_type(table) == "view":
            return jsonify({"ok": False, "error": "这是数据转换生成的视图，只读；请修改转换步骤后刷新"}), 400
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
        cur = con.execute(f"INSERT INTO {db.qid(table)} ({col_list}) VALUES ({ph})", vals)
        con.commit()
    except sqlite3.Error as e:
        con.close()
        return jsonify({"ok": False, "error": str(e)}), 400
    pkcol = get_pk(table, con)
    new_id = cur.lastrowid if pkcol in auto_pk_cols(table, con) else None
    con.close()
    return jsonify({"ok": True, "id": new_id})


# ---------------------------------------------------------------------------
# 编辑一行
# ---------------------------------------------------------------------------
@app.route("/api/tables/<table>/rows/<path:pk>", methods=["PUT"])
def api_update(table, pk):
    if table not in KNOWN_TABLES:
        if _obj_type(table) == "view":
            return jsonify({"ok": False, "error": "这是数据转换生成的视图，只读；请修改转换步骤后刷新"}), 400
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
        if _obj_type(table) == "view":
            return jsonify({"ok": False, "error": "这是数据转换生成的视图，只读；请修改转换步骤后刷新"}), 400
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
    mode = (request.form.get("conflict_mode") or "skip").strip().lower()
    if mode not in ("skip", "overwrite", "fields"):
        mode = "skip"
    fields = [x.strip() for x in (request.form.get("conflict_fields") or "").split(",")
              if x.strip()]
    add_missing = request.form.get("add_missing_cols") == "1"
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
    added_cols = []
    try:
        plan = classify_import(con, table, rep)
        # 库里没有的列：按用户选择加到事实表，然后重新解析（新列才有值可写）
        if add_missing and plan["file_cols_not_in_db"]:
            for c in plan["file_cols_not_in_db"]:
                con.execute(f"ALTER TABLE {db.qid(table)} ADD COLUMN {db.qid(c)} TEXT")
                added_cols.append(c)
            con.commit()
            try:
                f.stream.seek(0)
            except Exception:
                pass
            rep = _build_import_report(table, f)
            plan = classify_import(con, table, rep)

        insert_cols = rep["db_columns"]
        col_list = ",".join(db.qid(c) for c in insert_cols)
        ph = ",".join("?" * len(insert_cols))
        sql = f"INSERT OR IGNORE INTO {db.qid(table)} ({col_list}) VALUES ({ph})"
        before = con.total_changes
        con.executemany(sql, rep["valid_rows"])
        inserted = con.total_changes - before

        updated = 0
        if added_cols:
            # 新加到事实表的列对已有行也要有值，否则加列等于白加
            _backfill_cols(con, table, rep, added_cols)
        if mode in ("overwrite", "fields") and plan["conflict"]:
            updated = _apply_conflicts(con, table, rep, plan, mode, fields)
        con.commit()

        # 导入后提醒：聚合表有哪些取值还没映射；历史类型转换有没有转不出来的行
        try:
            pending = aggmap.pending_values(con)
        except Exception:
            pending = []
        risks = type_risks(con, table, rep)
    finally:
        con.close()

    c = rep["counts"]
    payload = {
        "ok": True, "encoding": rep.get("encoding"), "total": c["total"], "valid": c["valid"],
        "inserted": inserted, "skipped": c["valid"] - inserted,
        "skipped_blank_pk": c["blank_pk"], "error_rows": c["col_mismatch"],
        "amount_warn": c["amount_warn"], "dup_in_file": c["dup_in_file"],
        # 增量分类与冲突处理结果
        "new": plan["new"], "same": plan["same"], "conflict": plan["conflict"],
        "conflict_mode": mode, "updated": updated,
        "conflict_fields": plan["conflict_fields"],
        "added_cols": added_cols,
        "file_cols_not_in_db": plan["file_cols_not_in_db"],
        "pending_agg": [{"dim_table": x["dim_table"], "name": x["name"],
                         "source_table": x["source_table"], "source_col": x["source_col"],
                         "new_count": x["new_count"], "samples": x["samples"]}
                        for x in pending],
        "type_risks": risks,
    }
    return jsonify(payload)


def _backfill_cols(con, table, rep, cols):
    """把「刚加到事实表的新列」的值回填到已存在的行（只动这几列，其它列不碰）。

    INSERT OR IGNORE 对已存在的主键是跳过的，所以新列在旧行上会一直是空；
    这里单独做一次窄更新把它补上。主键不存在的行更新影响 0 行，天然安全。
    """
    allcols = rep["db_columns"]
    pk = rep["pk"]
    if pk not in allcols:
        return 0
    pk_idx = allcols.index(pk)
    use = [c for c in cols if c in allcols and c != pk]
    if not use:
        return 0
    idx = [allcols.index(c) for c in use]
    sql = (f"UPDATE {db.qid(table)} SET "
           + ", ".join(f"{db.qid(c)}=?" for c in use)
           + f" WHERE {db.qid(pk)}=?")
    payload = [tuple(r[i] for i in idx) + (r[pk_idx],) for r in rep["valid_rows"]]
    if not payload:
        return 0
    before = con.total_changes
    con.executemany(sql, payload)
    return con.total_changes - before


def _apply_conflicts(con, table, rep, plan, mode, fields):
    """把「有变化」的行用新数据覆盖回库。mode=overwrite 整行；fields 只盖指定列。"""
    cols = rep["db_columns"]
    pk = rep["pk"]
    pk_idx = cols.index(pk)
    use = plan["updatable_cols"] if mode == "overwrite" else [
        c for c in plan["updatable_cols"] if c in set(fields)]
    use = [c for c in use if c != pk]
    if not use:
        return 0
    idx = [cols.index(c) for c in use]
    sql = (f"UPDATE {db.qid(table)} SET "
           + ", ".join(f"{db.qid(c)}=?" for c in use)
           + f" WHERE {db.qid(pk)}=?")
    by_key = {str(r[pk_idx]): r for r in rep["valid_rows"]}
    payload = []
    for k in plan.get("conflict_keys") or []:
        r = by_key.get(str(k))
        if r is None:
            continue
        payload.append(tuple(r[i] for i in idx) + (r[pk_idx],))
    if not payload:
        return 0
    before = con.total_changes
    con.executemany(sql, payload)
    return con.total_changes - before


_DATE_FMTS = ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y%m%d",
              "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%d.%m.%Y",
              "%d-%b-%y", "%d-%b-%Y", "%d %b %Y", "%b %d, %Y",
              "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S")


def _looks_like_date(s):
    """判断一个文本能否被常见的日期格式解析（用于提前预警「转日期后变空」）。"""
    s = (s or "").strip()
    if not s:
        return False
    if len(s) > 10:                      # 带时间的先截到日期部分再试
        head = s[:10]
        if any(_try_date(head, f) for f in _DATE_FMTS[:8]):
            return True
    return any(_try_date(s, f) for f in _DATE_FMTS)


def _try_date(s, fmt):
    try:
        datetime.datetime.strptime(s, fmt)
        return True
    except ValueError:
        return False


_TF_NUM_TYPES = {"number", "real", "float", "double", "integer", "int"}


def type_risks(con, table, rep):
    """历史「数据转换」里对该表做过的类型转换，新数据有多少行过不去。

    视图是查询期求值，导入后自动跟随，不需要手工重跑；这里只负责提前告诉你
    哪几行会失败 —— 数字转不出来的会静默变成 0，日期转不出来的会变空或保留原值。
    """
    try:
        qs = con.execute(
            "SELECT id,name,source_table,output_mode,steps FROM transform_query "
            "WHERE enabled=1 AND source_table=?", (table,)).fetchall()
    except sqlite3.Error:
        return []
    cols = rep["db_columns"]
    rows = rep["valid_rows"]
    out = []
    for q in qs:
        try:
            steps = json.loads(q["steps"] or "[]")
        except Exception:
            continue
        for st in steps:
            if not isinstance(st, dict) or st.get("op") != "change_type":
                continue
            col = st.get("column")
            typ = str(st.get("type") or "").lower()
            if col not in cols:
                continue
            i = cols.index(col)
            bad = []
            for r in rows:
                v = r[i]
                if v is None:
                    continue
                s = str(v).strip()
                if not s:
                    continue
                ok = True
                if typ in _TF_NUM_TYPES:
                    ok = db.parse_amount(v) is not None
                elif typ in ("date", "datetime"):
                    ok = _looks_like_date(s)
                if not ok:
                    bad.append({"value": s[:40]})
                    if len(bad) >= 10:
                        break
            if bad:
                out.append({
                    "query": q["name"], "object": q["name"],
                    "output_mode": q["output_mode"],
                    "column": col, "type": typ, "failed": len(bad),
                    "samples": bad,
                    "message": (f"「{q['name']}」的 {col} 转 {typ} 有 {len(bad)}+ 行会失败"
                                + ("（数字转不出来会变成 0）" if typ in _TF_NUM_TYPES
                                   else "（日期转不出来的会变空或保留原值）")),
                })
    return out


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
    con = get_con()
    try:
        plan = classify_import(con, table, rep)
    finally:
        con.close()
    plan.pop("conflict_keys", None)   # 冲突主键只在执行时用，预览不必回传
    rep.pop("valid_rows", None)       # 预览不返回大数据
    return jsonify({"ok": True, **rep, "plan": plan})


# ---------------------------------------------------------------------------
# 导入为独立维度表（两步：预览 → 建表写入）
#   把一份现成的映射/字典文件原样导成一张 dim_* 表，不动任何事实表。
#   列全部按 TEXT 存（审计优先，不做隐式类型转换），不写 FOREIGN KEY。
# ---------------------------------------------------------------------------
def _parse_newtable_file(data, filename):
    """CSV / XLSX → (表头, 数据行)。不做表头匹配，列名原样保留。"""
    low = (filename or "").lower()
    is_xlsx = low.endswith((".xlsx", ".xlsm"))
    sheet = None
    if is_xlsx:
        try:
            import openpyxl
        except ImportError:
            raise ValueError("读取 Excel 需要 openpyxl")
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        try:
            ws = wb.worksheets[0]
            sheet = ws.title
            grid = [[("" if v is None else str(v)) for v in row]
                    for row in ws.iter_rows(values_only=True)]
        finally:
            wb.close()
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
        grid = [list(r) for r in csv.reader(io.StringIO(text), delimiter=delim)]
    grid = [r for r in grid if any(str(c).strip() for c in r)]
    if not grid:
        raise ValueError("文件里没有读到任何数据行")
    if len(grid) < 2:
        raise ValueError("文件只有表头、没有数据行")
    header = [str(c).strip().lstrip("\ufeff") for c in grid[0]]
    rows = [[("" if c is None else str(c).strip()) for c in r] for r in grid[1:]]
    return {"header": header, "rows": rows, "encoding": enc,
            "sheet": sheet, "source": "xlsx" if is_xlsx else "csv"}


def _clean_headers(header):
    """列名去 BOM/空白、补空名、按大小写不敏感去重（SQLite 列名大小写不敏感）。"""
    out, seen = [], set()
    for i, h in enumerate(header):
        base = (h or "").strip().lstrip("\ufeff") or f"列{i+1}"
        name, n = base, 2
        while name.lower() in seen:
            name = f"{base}_{n}"
            n += 1
        seen.add(name.lower())
        out.append(name)
    return out


def _check_newtable_name(con, name):
    name = (name or "").strip()
    if not _NEWTABLE_RE.match(name):
        return "表名只能含字母、数字、下划线，且必须以字母开头"
    if not name.startswith("dim_"):
        return "独立维度表名必须以 dim_ 开头"
    if name.startswith("dim_agg_"):
        return "dim_agg_ 前缀由「映射聚合」专用，请换一个名字"
    if con.execute("SELECT COUNT(*) FROM sqlite_master WHERE name=?", (name,)).fetchone()[0]:
        return f"「{name}」已存在，请换一个名字，或改用「追加到现有数据表」"
    return None


def _newtable_common(f, form, check_exists=True):
    """预演与写入共用的解析 + 校验，返回 (ctx, error)。"""
    if f is None or not f.filename:
        return None, "未收到文件"
    data = f.read()
    if not data:
        return None, "文件是空的"
    try:
        parsed = _parse_newtable_file(data, f.filename)
    except ValueError as e:
        return None, str(e)
    except Exception as e:
        return None, f"文件解析失败：{e}"
    name = (form.get("table") or "").strip()
    label = (form.get("label") or "").strip() or name
    descr = (form.get("desc") or "").strip()
    header = _clean_headers(parsed["header"])
    rows = parsed["rows"]
    pk = (form.get("pk") or "").strip()
    if pk not in header:
        pk = header[0]
    con = get_con()
    try:
        if check_exists:
            err = _check_newtable_name(con, name)
            if err:
                return None, err
        elif not _NEWTABLE_RE.match(name) or not name.startswith("dim_"):
            return None, "表名不合法"
    finally:
        con.close()
    return {"name": name, "label": label, "descr": descr, "header": header,
            "rows": rows, "pk": pk, "parsed": parsed, "filename": f.filename}, None


def _newtable_stats(ctx):
    """主键空值/重复、列数不齐统计。"""
    header, rows, pk = ctx["header"], ctx["rows"], ctx["pk"]
    i = header.index(pk)
    blank = dup = mismatch = 0
    seen, dup_samples = set(), []
    for r in rows:
        v = r[i] if i < len(r) else ""
        if len(r) != len(header):
            mismatch += 1
        if not v:
            blank += 1
            continue
        if v in seen:
            dup += 1
            if len(dup_samples) < 5:
                dup_samples.append(v)
        seen.add(v)
    return {"cols": len(header), "rows": len(rows), "blank_pk": blank,
            "dup_pk": dup, "dup_samples": dup_samples, "col_mismatch": mismatch}


def _newtable_backup():
    """建表前备份整库。只清理本功能自己产生的前缀，保留最近 3 份，绝不动其它备份。"""
    try:
        bdir = os.path.join(os.path.dirname(os.path.abspath(DB_PATH)), "backups")
        os.makedirs(bdir, exist_ok=True)
        path = os.path.join(bdir, "procurement_before_newdim_"
                            + time.strftime("%Y%m%d_%H%M%S") + ".db")
        shutil.copy2(DB_PATH, path)
        olds = sorted((f for f in os.listdir(bdir)
                       if f.startswith("procurement_before_newdim_") and f.endswith(".db")),
                      reverse=True)
        for f in olds[3:]:
            try:
                os.remove(os.path.join(bdir, f))
            except OSError:
                pass
        return os.path.basename(path)
    except OSError:
        return None


@app.route("/api/import/new-table-preview", methods=["POST"])
def api_import_newtable_preview():
    ctx, err = _newtable_common(request.files.get("file"), request.form)
    if err:
        return jsonify({"ok": False, "error": err}), 400
    st = _newtable_stats(ctx)
    return jsonify({
        "ok": True, "table": ctx["name"], "label": ctx["label"],
        "header": ctx["header"], "pk": ctx["pk"], "stats": st,
        "sample": ctx["rows"][:3], "encoding": ctx["parsed"]["encoding"],
        "source": ctx["parsed"]["source"], "sheet": ctx["parsed"]["sheet"],
        "can_import": st["rows"] > 0 and not st["blank_pk"] and not st["dup_pk"]})


@app.route("/api/import/new-table", methods=["POST"])
def api_import_newtable():
    ctx, err = _newtable_common(request.files.get("file"), request.form)
    if err:
        return jsonify({"ok": False, "error": err}), 400
    st = _newtable_stats(ctx)
    if not st["rows"]:
        return jsonify({"ok": False, "error": "没有数据行"}), 400
    if st["blank_pk"] or st["dup_pk"]:
        return jsonify({
            "ok": False,
            "error": f"主键列「{ctx['pk']}」有 {st['blank_pk']} 个空值、"
                     f"{st['dup_pk']} 个重复值，无法作为主键；请换一列或修正文件"}), 400
    name, header, pk = ctx["name"], ctx["header"], ctx["pk"]
    backup = _newtable_backup() if (request.form.get("backup") or "1") == "1" else None
    # 全部 TEXT + 单一主键；不写 FOREIGN KEY（维度表下线会留下悬空外键）
    coldefs = [f"{db.qid(h)} TEXT" + (" PRIMARY KEY" if h == pk else "")
               for h in header]
    ddl = f"CREATE TABLE {db.qid(name)} (\n  " + ",\n  ".join(coldefs) + "\n)"
    ins = f"INSERT INTO {db.qid(name)} VALUES ({','.join('?' for _ in header)})"
    norm = [(r + [""] * len(header))[:len(header)] for r in ctx["rows"]]
    con = get_con()
    try:
        ensure_imported_table(con)          # 内部会 commit，必须在 BEGIN 之前
        err = _check_newtable_name(con, name)
        if err:
            return jsonify({"ok": False, "error": err}), 400
        con.execute("BEGIN")
        con.execute(ddl)
        con.executemany(ins, norm)
        con.execute(
            "INSERT OR REPLACE INTO imported_table "
            "(table_name,label,descr,pk_col,source_file,row_count,created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (name, ctx["label"], ctx["descr"], pk, ctx["filename"], len(norm),
             datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        con.commit()
    except Exception as e:
        con.rollback()
        return jsonify({"ok": False, "error": f"建表失败，已回滚：{e}"}), 500
    finally:
        con.close()
    register_imported_table(name, ctx["label"], ctx["descr"], pk,
                            ctx["filename"], len(norm))
    return jsonify({"ok": True, "table": name, "label": ctx["label"],
                    "cols": len(header), "rows": len(norm), "pk": pk,
                    "backup": backup})


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
                    # 补录只写维度表：目标非维度表一律不开放，且不存在归并模式
                    "allowed_modes": (["placeholder", "manual"] if is_dim else []),
                    "needs_confirm": False,   # 事实表永不改写，无需二次确认
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
        exist = _existing_tables(con)
        for t in sorted(KNOWN_TABLES & exist):
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
                "needs_confirm": False,   # 补录不改写事实表
                "advice": adv, "advice_reason": adv_reason,
            })
        # 2) 表 + 完整字段明细（名称 / 声明类型 / 是否主键 / 是否金额列）
        tables = []
        exist = _existing_tables(con)
        for t in sorted(KNOWN_TABLES & exist):
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
        # 维度自身是否还有「同一写法的多个变体」：有的话应先做「取值清洗」，
        # 否则补录会把值归到另一个变体上，分析时仍被拆成两组。
        dup_groups = 0
        if rt.startswith("dim_"):
            try:
                dup_groups = clean.value_profile(con, rt, rc)["group_count"]
            except (ValueError, sqlite3.Error):
                dup_groups = 0
        return jsonify({"keys": [i["key"] for i in items], "items": items,
                        "total": total, "rows_total": rows_total,
                        "extra_cols": extra_cols, "extra_options": extra_options,
                        "dup_groups": dup_groups,
                        "page": page, "size": size})
    finally:
        con.close()


@app.route("/api/pending-map")
def api_pending_map():
    """只读：某维度表上「事实表有、维度表没有」的待补映射值。

    query: dim=<维度表>  或  fact=<事实表>（至少给一个）
    返回 pending: [{fact, fact_col, dim, dim_col, count(行数), keys:[取值...]}]
    仅查询，不写库；供维度表顶部横幅 + 新增行时「待补值」下拉使用。
    """
    dim_t = request.args.get("dim"); fact_t = request.args.get("fact")
    limit = max(1, min(500, int(request.args.get("limit", 200))))
    if not dim_t and not fact_t:
        return jsonify({"error": "需要 dim 或 fact 参数"}), 400
    if (dim_t and dim_t not in KNOWN_TABLES) or (fact_t and fact_t not in KNOWN_TABLES):
        return jsonify({"error": "表名非法"}), 400
    con = get_con()
    try:
        fks = con.execute(
            "SELECT source_table,source_col,target_table,target_col FROM map_config "
            "WHERE enabled=1").fetchall()
        out = []
        for r in fks:
            t, col, rt, rc = (r["source_table"], r["source_col"],
                              r["target_table"], r["target_col"])
            if not rt.startswith("dim_"):
                continue                      # 只有映射到维度表才有「补映射值」的概念
            if dim_t and rt != dim_t:
                continue
            if fact_t and t != fact_t:
                continue
            # 源表/目标表可能已被删，跳过避免 SQL 报错
            exist = {x[0] for x in con.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table','view')").fetchall()}
            if t not in exist or rt not in exist:
                continue
            where = (f"WHERE {db.qid(col)} IS NOT NULL AND "
                     f"{db.qid(col)} NOT IN (SELECT {db.qid(rc)} FROM {db.qid(rt)})")
            cnt = con.execute(
                f"SELECT COUNT(*) FROM {db.qid(t)} {where}").fetchone()[0]
            if not cnt:
                continue
            keys = [x[0] for x in con.execute(
                f"SELECT {db.qid(col)} AS k, COUNT(*) AS c FROM {db.qid(t)} {where} "
                f"GROUP BY {db.qid(col)} ORDER BY c DESC, k LIMIT ?", [limit]).fetchall()]
            out.append({"fact": t, "fact_col": col, "dim": rt, "dim_col": rc,
                        "count": cnt, "keys": keys})
        result = {"pending": out}
        # 传了 dim 时，额外返回该维度表各列的已有取值（去重、非空、限量）。
        # 供新增行/行内编辑的下拉复用已有内容：待补值 + 已有值一起给，前端合并。
        if dim_t:
            exist = {x[0] for x in con.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table','view')").fetchall()}
            if dim_t in exist:
                cols = [r["name"] for r in con.execute(
                    f"PRAGMA table_info({db.qid(dim_t)})").fetchall()]
                col_values = {}
                for c in cols:
                    try:
                        vals = [x[0] for x in con.execute(
                            f"SELECT DISTINCT {db.qid(c)} FROM {db.qid(dim_t)} "
                            f"WHERE {db.qid(c)} IS NOT NULL "
                            f"AND TRIM(CAST({db.qid(c)} AS TEXT)) <> '' "
                            f"ORDER BY {db.qid(c)} LIMIT ?", [limit]).fetchall()]
                    except sqlite3.Error:
                        vals = []
                    if vals:
                        col_values[c] = vals
                result["col_values"] = col_values
        return jsonify(result)
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
    """维度成员补录（事实表只读）。

    把「源表里有、维度表里没有」的值作为新成员写入维度表：
      placeholder — 以缺失值为主键插入占位行（其余字段留空）
      manual      — 以缺失值为主键新建维度成员，其余字段由前端提交的 values 指定

    不再支持 merge（归并会改写事实表）：归一/改名请使用「字段取值清洗」。
    目标表必须是 dim_ 维度表；任何情况下都不向事实表插行、不改写事实表。
    """
    body = request.get_json(force=True, silent=True) or {}
    t = body.get("fact"); col = body.get("fact_col")
    rt = body.get("dim"); rc = body.get("dim_col")
    mode = (body.get("mode") or "placeholder").lower()
    if not all([t, col, rt, rc]) or t not in KNOWN_TABLES or rt not in KNOWN_TABLES:
        return jsonify({"ok": False, "error": "参数缺失或非法"}), 400
    if mode not in ("placeholder", "manual"):
        return jsonify({"ok": False,
                        "error": f"不支持的补录方式：{mode}（归并/改名会改写事实表，已停用，请使用取值清洗）"}), 400
    # 硬约束：补录目标只能是维度表
    if not rt.startswith("dim_"):
        return jsonify({"ok": False,
                        "error": "补录只能写入维度表，不能修改事实表；请为维度表补录缺失成员。"}), 400

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
    """维度成员补录（事实表只读）：把「多出来的值」逐个写成维度表的新成员。

    body = {
      fact, fact_col, dim, dim_col,
      keys: ["缺失值", ...],            # 新版：要补录的缺失值（推荐）
      mappings: [{key, extra:{...}}],   # 兼容旧版；target 字段一律忽略，不归并、不改事实表
      default_action: "keep"|"skip",    # 未点名的缺失值：keep=各自新建同名成员 / skip=暂不处理
      extra: {字段: 值}                 # 可选：新建成员时一并写入的其它字段
    }

    硬约束：目标必须是 dim_ 维度表；只 INSERT 维度表，永不 UPDATE / INSERT 事实表。
    想把不同写法归一成一个值属于「字段取值清洗」，不在补录里做。
    返回：created（新建成员数）/ remaining（剩余缺失行数）
    """
    body = request.get_json(force=True, silent=True) or {}
    t = body.get("fact"); col = body.get("fact_col")
    rt = body.get("dim"); rc = body.get("dim_col")
    if not all([t, col, rt, rc]) or t not in KNOWN_TABLES or rt not in KNOWN_TABLES:
        return jsonify({"ok": False, "error": "参数缺失或非法"}), 400
    # 硬约束：补录目标只能是维度表
    if not rt.startswith("dim_"):
        return jsonify({"ok": False,
                        "error": "补录只能写入维度表，不能修改事实表；请为维度表补录缺失成员。"}), 400

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
            return jsonify({"ok": True, "created": 0, "remaining": 0,
                            "message": "无缺失维度，无需补录"})

        # —— 构建计划：每个缺失值 -> 以原值新建同名成员；附带字段按「全局默认 + 逐行覆盖」——
        plan, row_extra = set(), {}
        mps = body.get("mappings") or []
        if isinstance(mps, dict):
            mps = [{"key": k, "extra": v if isinstance(v, dict) else {}}
                   for k, v in mps.items()]
        for m in mps:
            if not isinstance(m, dict):
                continue
            k = m.get("key")
            if k is None or k not in missing:
                continue
            plan.add(k)                 # target 一律忽略：只按原值建成员
            ex = m.get("extra") or {}
            if isinstance(ex, dict):
                clean = {str(c): str(v).strip() for c, v in ex.items()
                         if v is not None and str(v).strip() != ""}
                if clean:
                    row_extra[k] = clean
        for k in (body.get("keys") or []):
            if k in missing:
                plan.add(k)
        default_action = (body.get("default_action") or "skip").lower()
        if default_action == "keep":
            plan.update(missing)        # 未点名的也各自新建同名成员
        if not plan:
            return jsonify({"ok": True, "created": 0, "remaining": len(missing),
                            "message": "未指定任何缺失值，未做改动"})

        # —— 附带字段：过滤非法列；id 列自动续号 ——
        dim_cols = [r["name"] for r in con.execute(
            f"PRAGMA table_info({db.qid(rt)})").fetchall()]
        exists = {r[0] for r in con.execute(
            f"SELECT {db.qid(rc)} FROM {db.qid(rt)}").fetchall()}
        base_extra = {}
        for c, v in (body.get("extra") or {}).items():
            if c == rc or c not in dim_cols or v is None or str(v).strip() == "":
                continue
            base_extra[c] = str(v).strip()

        create_extras = {}
        for k in plan:
            if k in exists:
                continue                # 已有同名成员 → 幂等跳过
            ex = dict(base_extra)
            ex.update({c: v for c, v in (row_extra.get(k) or {}).items() if c in dim_cols})
            create_extras[k] = ex

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
        for k in create_extras:
            vals = []
            for c in fill_cols:
                if c == "id" and auto_id:
                    vals.append(str(next_id)); next_id += 1
                else:
                    vals.append(create_extras[k].get(c))
            try:
                cur = con.execute(ins_sql, [k] + vals)
                if cur.rowcount:
                    created.append(k)
            except sqlite3.Error:
                pass
        con.commit()

        # —— 剩余缺失（未点名 / 其它页未处理的会被算进来）——
        remaining_rows = con.execute(
            f"SELECT COUNT(*) FROM {db.qid(t)} {miss_where}").fetchone()[0]
        remaining_keys = con.execute(
            f"SELECT COUNT(DISTINCT {db.qid(col)}) FROM {db.qid(t)} {miss_where}").fetchone()[0]

        note_cols = [c for c in fill_cols if not (c == "id" and auto_id)]
        parts = [f"新建 {len(created)} 个维度成员"
                 + (f"（附带写入 {'、'.join(note_cols)}）" if note_cols else "")]
        if remaining_keys:
            parts.append(f"仍有 {remaining_keys} 个缺失值 / {remaining_rows} 行未处理")
        return jsonify({
            "ok": True, "created": len(created), "created_values": created[:50],
            "merged_keys": 0, "merged_rows": 0,        # 兼容旧字段；补录永不改写事实表
            "filled": note_cols, "auto_id": auto_id,
            "remaining_keys": remaining_keys, "remaining": remaining_rows,
            "message": "；".join(parts) + "。",
        })
    finally:
        con.close()


# ---------------------------------------------------------------------------
# 字段取值清洗（整合相似文本）：扫描 → 分组 → 归并
#   与「补录」的区别：补录是补齐缺失的维度成员，这里是同一事物的多种写法合并成一个，
#   目的是让统计与可视化的分组口径统一（不再因为空格/下划线/全角括号被拆成多组）。
# ---------------------------------------------------------------------------
def _clean_target_ok(table, col):
    if table not in KNOWN_TABLES:
        return "参数缺失或非法"
    con = get_con()
    try:
        cols = [r[1] for r in con.execute(f"PRAGMA table_info({db.qid(table)})").fetchall()]
    finally:
        con.close()
    if col not in cols:
        return f"列不存在：{table}.{col}"
    return None


@app.route("/api/clean-profile")
def api_clean_profile():
    """扫描一个字段的全部取值并自动分组（按规范化键判定重复写法）。"""
    table = request.args.get("table") or ""
    col = request.args.get("col") or ""
    err = _clean_target_ok(table, col)
    if err:
        return jsonify({"ok": False, "error": err}), 400
    con = get_con()
    try:
        data = clean.value_profile(con, table, col)
        data["ok"] = True
        data["label"] = table_label(table)
        return jsonify(data)
    except (ValueError, sqlite3.Error) as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    finally:
        con.close()


@app.route("/api/clean-cascade-scan", methods=["POST"])
def api_clean_cascade_scan():
    """深度扫描：当维度列没有配置关系时，找出还有哪些表列装着同样的取值。"""
    body = request.get_json(force=True, silent=True) or {}
    table = body.get("table") or ""
    col = body.get("col") or ""
    err = _clean_target_ok(table, col)
    if err:
        return jsonify({"ok": False, "error": err}), 400
    con = get_con()
    try:
        hits = clean.scan_cascade(con, table, col, allow_same_name=True)
        for h in hits:
            h["label"] = table_label(h["table"])
        return jsonify({"ok": True, "hits": hits})
    except sqlite3.Error as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    finally:
        con.close()


@app.route("/api/clean-merge", methods=["POST"])
def api_clean_merge():
    """执行值归并：把若干种写法合并成一个规范值。

    body = {table, col, groups:[{to, from:[...]}],
            cascade:bool, extra_cascade:[[table,col],...], backup:bool}
    """
    body = request.get_json(force=True, silent=True) or {}
    table = body.get("table") or ""
    col = body.get("col") or ""
    err = _clean_target_ok(table, col)
    if err:
        return jsonify({"ok": False, "error": err}), 400
    groups = body.get("groups") or []
    if not isinstance(groups, list) or not groups:
        return jsonify({"ok": False, "error": "没有要执行的归并"}), 400
    cascade = body.get("cascade", True)
    extra = body.get("extra_cascade") or []

    backup_path = None
    if body.get("backup", True):
        try:
            bdir = os.path.join(os.path.dirname(os.path.abspath(DB_PATH)), "backups")
            os.makedirs(bdir, exist_ok=True)
            backup_path = os.path.join(
                bdir, "procurement_before_value_merge_" + time.strftime("%Y%m%d_%H%M%S") + ".db")
            shutil.copy2(DB_PATH, backup_path)
        except OSError:
            backup_path = None

    con = get_con()
    try:
        res = clean.apply_merge(con, table, col, groups,
                                cascade=bool(cascade), extra_cascade=extra)
        con.commit()
    except (ValueError, sqlite3.Error) as e:
        con.rollback()
        return jsonify({"ok": False, "error": str(e),
                        "backup": backup_path}), 400
    finally:
        con.close()

    parts = [f"归并 {res['groups']} 组（{res['merged_values']} 个写法）",
             f"取值 {res['before_values']} → {res['after_values']}"]
    if res["updated"]:
        parts.append("改写 " + "、".join(
            f"{table_label(t)} {n} 行" for t, n in res["updated"].items()))
    if res["deleted_rows"]:
        parts.append(f"删除 {res['deleted_rows']} 个多余维度成员")
    msg = "；".join(parts) + "。"
    if not res["cascade"]:
        msg += "（未检测到引用该列的其它表，未做级联改写）"
    return jsonify({"ok": True, "message": msg, "backup": backup_path,
                    "result": res,
                    "affected_rows": res["updated_rows"] + res["deleted_rows"]})


# ===========================================================================
# 映射聚合：把一个字段的多种写法聚合成一张可关联的维度表
#   ① 选表选字段 → 扫描去重 → ② 自动整合同类项 / 导出改完再导入 → ③ 生成维度表
#   全程不改源表；维度表按「原始值」与原字段关联，可视化改用「聚合值」分组。
# ===========================================================================
def _agg_backup(tag="agg"):
    """映射聚合写库前备份整个库，并只保留最近 5 份聚合备份（避免越攒越多）。"""
    try:
        bdir = os.path.join(os.path.dirname(os.path.abspath(DB_PATH)), "backups")
        os.makedirs(bdir, exist_ok=True)
        path = os.path.join(bdir, f"procurement_before_{tag}_"
                                  + time.strftime("%Y%m%d_%H%M%S") + ".db")
        shutil.copy2(DB_PATH, path)
        olds = sorted(
            (f for f in os.listdir(bdir)
             if f.startswith("procurement_before_agg") and f.endswith(".db")),
            reverse=True)
        for f in olds[5:]:
            try:
                os.remove(os.path.join(bdir, f))
            except OSError:
                pass
        return path
    except OSError:
        return None


@app.route("/api/agg/scan")
def api_agg_scan():
    """扫描字段并给出初始映射（默认按规范化键自动整合同类项）。"""
    table = request.args.get("table") or ""
    col = request.args.get("col") or ""
    err = _clean_target_ok(table, col)
    if err:
        return jsonify({"ok": False, "error": err}), 400
    mode = request.args.get("mode") or "exact"
    if mode not in ("exact", "fuzzy"):
        mode = "exact"
    try:
        threshold = float(request.args.get("threshold") or 0.9)
    except (TypeError, ValueError):
        threshold = 0.9
    con = get_con()
    try:
        d = aggmap.auto_map(con, table, col, mode=mode, threshold=threshold)
        d["ok"] = True
        d["label"] = table_label(table)
        d["suggest_dim"] = aggmap.suggest_dim_name(con, table, col)
        d["suggest_value_col"] = aggmap.value_col_of(col)
        # 该字段已保存过映射时，以库里的映射为准（源字段新增的取值按 1:1 追加），
        # 而不是每次都用自动整合重算 —— 否则打开页面就等于把手改过的映射冲掉。
        task = aggmap.task_of(con, table, col) if request.args.get("saved", "1") != "0" else None
        if task:
            rows = aggmap.task_rows(con, task)
            extra_cols = aggmap.extra_columns(con, task["dim_table"], task["value_col"])
            have = {r["raw"] for r in rows}
            added = 0
            for r in d["rows"]:
                if r["raw"] in have:
                    continue
                rows.append({"raw": r["raw"], "agg": r["raw"], "rows": r["rows"], "new": True})
                added += 1
            rows.sort(key=lambda x: (-int(x.get("rows") or 0), str(x.get("raw") or "")))
            d["rows"] = rows
            d["saved"] = True
            d["extra_cols"] = extra_cols
            d["task_id"] = task["id"]
            d["task_name"] = task["name"]
            d["dim_table"] = task["dim_table"]
            d["value_col"] = task["value_col"]
            d["new_count"] = added
            d["total_values"] = len(rows)
        d["conflicts"] = aggmap.relation_conflicts(con, table, col)
        # 顺手扫出「还有哪些表列装着同样取值」：聚合维度表时这些列一并连过去，
        # 事实表就不用走「事实 → 原维度 → 聚合表」两跳。源表是事实表时尤其重要
        # （如 fct_prpo.部门 与 fct_po.部门 是同一套部门名）。
        if request.args.get("refs", "1") != "0":
            try:
                refs = clean.scan_cascade(con, table, col, allow_same_name=True)
                # 已经建了关系的列不用再列一次
                try:
                    _done = {(r[0], r[1]) for r in con.execute(
                        "SELECT source_table, source_col FROM map_config WHERE target_table=? "
                        "AND target_col=?", (table, col))}
                except sqlite3.Error:
                    _done = set()
                refs = [h for h in refs if (h["table"], h["col"]) not in _done]
            except sqlite3.Error:
                refs = []
            for h in refs:
                h["label"] = table_label(h["table"])
            d["refs"] = refs
        return jsonify(d)
    except (ValueError, sqlite3.Error) as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    finally:
        con.close()


@app.route("/api/agg/export", methods=["GET", "POST"])
def api_agg_export():
    """导出「原始值 / 聚合值」两列，供人工在 Excel 里核对与修改。

    只导出映射本身（源行数/写法数是统计口径，不进文件），回导时也只需这两列。
    GET  ：按源字段重新扫描后导出（含自动整合建议）
    POST ：导出前端当前编辑好的映射（rows），保证导出的就是屏幕上看到的
    """
    body = (request.get_json(force=True, silent=True) or {}) if request.method == "POST" else {}
    table = body.get("table") or request.args.get("table") or ""
    col = body.get("col") or request.args.get("col") or ""
    err = _clean_target_ok(table, col)
    if err:
        return jsonify({"ok": False, "error": err}), 400
    mode = body.get("mode") or request.args.get("mode") or "exact"
    try:
        threshold = float(body.get("threshold") or request.args.get("threshold") or 0.9)
    except (TypeError, ValueError):
        threshold = 0.9
    fmt = (body.get("fmt") or request.args.get("fmt") or "csv").lower()

    given = body.get("rows")
    extra_cols, extra_vals = [], {}
    if isinstance(given, list) and given:
        rows = []
        for r in given:
            raw = "" if r.get("raw") is None else str(r.get("raw"))
            if not raw.strip():
                continue
            agg = "" if r.get("agg") is None else str(r.get("agg"))
            rows.append((raw, int(r.get("rows") or 0), agg.strip() or raw))
            ex = r.get("extra") if isinstance(r.get("extra"), dict) else {}
            ex = {str(k): str(v) for k, v in ex.items() if v not in (None, "")}
            if ex:
                for k in ex:
                    if k not in extra_cols:
                        extra_cols.append(k)
                extra_vals[raw] = ex
    else:
        con = get_con()
        try:
            _, rows = aggmap.export_rows(con, table, col, mode=mode, threshold=threshold)
            # 维度表上已有的附加字段（如「部门en」）一并导出，回导时原样写回去
            task = aggmap.task_of(con, table, col)
            if task:
                extra_cols = aggmap.extra_columns(con, task["dim_table"], task["value_col"])
                for it in aggmap.task_rows(con, task):
                    if it.get("extra"):
                        extra_vals[it["raw"]] = {k: v for k, v in it["extra"].items()
                                                 if v not in (None, "")}
        except (ValueError, sqlite3.Error) as e:
            return jsonify({"ok": False, "error": str(e)}), 400
        finally:
            con.close()

    head = [aggmap.COL_RAW, "聚合值"] + extra_cols
    safe = re.sub(r'[\\/:*?"<>|\s]+', "_", f"聚合映射_{table}_{col}")[:60]
    if fmt == "xlsx":
        try:
            from openpyxl import Workbook
            wb = Workbook()
            ws = wb.active
            ws.title = "映射"
            ws.append(head)
            for raw, n, agg in rows:
                ex = extra_vals.get(raw) or {}
                ws.append([raw, agg] + [ex.get(c, "") for c in extra_cols])
            for i in range(len(head)):
                ws.column_dimensions[ws.cell(row=1, column=i + 1).column_letter].width = \
                    42 if i < 2 else 18
            bio = io.BytesIO()
            wb.save(bio)
            bio.seek(0)
            return send_file(bio, as_attachment=True, download_name=safe + ".xlsx",
                             mimetype="application/vnd.openxmlformats-officedocument."
                                      "spreadsheetml.sheet")
        except ImportError:
            pass
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(head)
    for raw, n, agg in rows:
        ex = extra_vals.get(raw) or {}
        w.writerow([raw, agg] + [ex.get(c, "") for c in extra_cols])
    payload = "\ufeff" + buf.getvalue()
    return Response(payload, mimetype="text/csv; charset=utf-8",
                    headers={"Content-Disposition":
                             "attachment; filename*=UTF-8''" + urllib.parse.quote(safe + ".csv")})


@app.route("/api/agg/import", methods=["POST"])
def api_agg_import():
    """解析上传的映射文件（CSV / XLSX），返回与源字段对齐后的映射预览（不写库）。"""
    f = request.files.get("file")
    table = request.form.get("table") or request.args.get("table") or ""
    col = request.form.get("col") or request.args.get("col") or ""
    if f is None:
        return jsonify({"ok": False, "error": "没有收到文件"}), 400
    err = _clean_target_ok(table, col)
    if err:
        return jsonify({"ok": False, "error": err}), 400
    data = f.read()
    if not data:
        return jsonify({"ok": False, "error": "文件是空的"}), 400

    con = get_con()
    try:
        scan = aggmap.auto_map(con, table, col)
        # 文件里没有的取值不能被打回 1:1：已保存过就以库里的映射为准，
        # 没保存过就沿用自动整合的建议，否则导一个局部文件会把其余合并全冲掉。
        task = aggmap.task_of(con, table, col)
        if task:
            base = {it["raw"]: it["agg"] for it in aggmap.task_rows(con, task)}
        else:
            base = {r["raw"]: r["agg"] for r in scan["rows"]}
    finally:
        con.close()

    src = {r["raw"]: r["rows"] for r in scan["rows"]}
    by_key = {}
    for raw in src:
        by_key.setdefault(clean.norm_key(raw) or raw, raw)

    def known(v):
        return v if v in src else by_key.get(clean.norm_key(v) or v)

    # 前端把屏幕上这份映射一起发过来当基线：文件里没出现的取值、以及文件里留空的
    # 附加字段，都沿用屏幕上 / 库里的现值，不会被局部文件打回 1:1 或清空。
    base_agg, base_extra = {}, {}
    raw_base = request.form.get("base") or ""
    if raw_base:
        try:
            for it in json.loads(raw_base):
                k = known("" if it.get("raw") is None else str(it.get("raw")))
                if not k:
                    continue
                v = "" if it.get("agg") is None else str(it.get("agg")).strip()
                if v:
                    base_agg[k] = v
                ex = it.get("extra")
                if isinstance(ex, dict):
                    ex = {str(a): str(b) for a, b in ex.items() if b not in (None, "")}
                    if ex:
                        base_extra[k] = ex
        except (ValueError, TypeError, AttributeError):
            base_agg, base_extra = {}, {}
    if not base_agg:
        base_agg = base

    # 第一列永远是原始值，表头可以自定义：认不出表头时用源字段的取值反推
    # （首行一个已知取值都没有、第二行有 → 首行是表头）。
    try:
        parsed = aggmap.parse_mapping(data, f.filename or "", is_known=known)
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400

    mapping, dup, unknown = {}, 0, 0
    extras_by_raw = {}
    for rec in parsed["records"]:
        key = known(rec["raw"])
        if not key:
            unknown += 1
            continue
        if key in mapping and mapping[key] != rec["agg"]:
            dup += 1
        mapping[key] = rec["agg"]
        if rec.get("extra"):
            extras_by_raw.setdefault(key, {}).update(rec["extra"])

    rows, seen_cols = [], list(parsed["extra_cols"])
    for raw, n in src.items():
        val = mapping.get(raw) or base_agg.get(raw) or raw
        r = {"raw": raw, "rows": n, "agg": val, "auto": val != raw}
        ex = dict(base_extra.get(raw) or {})
        ex.update(extras_by_raw.get(raw) or {})
        if ex:
            r["extra"] = ex
            for k in ex:
                if k not in seen_cols:
                    seen_cols.append(k)
        rows.append(r)
    rows.sort(key=lambda r: (-r["rows"], r["raw"]))
    file_rows = len(parsed["records"])
    return jsonify({
        "ok": True, "rows": rows,
        "extra_cols": seen_cols,
        "stats": {
            "file_rows": file_rows,
            "matched": len(mapping),
            "unknown": unknown,
            "not_in_file": max(0, len(src) - len(mapping)),
            "blank_agg": parsed["blank_agg"],
            "skipped": parsed["skipped"],
            "conflict": dup,
            "encoding": parsed["encoding"],
            "header": bool(parsed["header"]),
            "agg_from_file": bool(parsed["agg_from_file"]),
            "extra_cols": seen_cols,
            "renamed": parsed["renamed"],
            "source_values": len(src),
        },
    })


@app.route("/api/agg/build", methods=["POST"])
def api_agg_build():
    """生成聚合维度表，并自动在模型视图建关系。"""
    body = request.get_json(force=True, silent=True) or {}
    table = (body.get("source_table") or "").strip()
    col = (body.get("source_col") or "").strip()
    err = _clean_target_ok(table, col)
    if err:
        return jsonify({"ok": False, "error": err}), 400
    rows = body.get("rows") or []
    if not isinstance(rows, list) or not rows:
        return jsonify({"ok": False, "error": "映射为空，没有可写入的取值"}), 400

    backup_path = _agg_backup("agg_build") if body.get("backup", True) else None
    con = get_con()
    try:
        res = aggmap.build(
            con, name=(body.get("name") or "").strip(),
            source_table=table, source_col=col,
            dim_table=(body.get("dim_table") or "").strip(),
            value_col=(body.get("value_col") or "").strip(),
            rows=rows,
            relations=body.get("relations") or [],
            disable_existing=bool(body.get("disable_existing", True)),
            extra_cols=body.get("extra_cols") or [],
            note=(body.get("note") or "").strip())
        con.commit()
    except (ValueError, sqlite3.Error) as e:
        con.rollback()
        return jsonify({"ok": False, "error": str(e), "backup": backup_path}), 400
    finally:
        con.close()

    register_agg_table(res["dim_table"], (body.get("name") or "").strip() or col + " 聚合",
                       table, col, res["value_col"])
    try:
        con = get_con()
        total, orphan, rate, status = aggmap.rel_stats(
            con, table, col, res["dim_table"], aggmap.COL_RAW)
    finally:
        con.close()
    msg = (f"已生成 {res['dim_table']}：{res['stats']['rows']} 个原始值 → "
           f"{res['stats']['aggs']} 个聚合值；建立 {len(res['relations'])} 条关联")
    if res["disabled"]:
        msg += f"，停用 {res['disabled']} 条原有关系"
    return jsonify({"ok": True, "message": msg, "backup": backup_path,
                    "dim_table": res["dim_table"], "task_id": res["task_id"],
                    "stats": res["stats"], "relations": res["relations"],
                    "extra_cols": res.get("extra_cols") or [],
                    "orphan": {"total": total, "orphan": orphan,
                               "rate": round(rate, 2), "status": status}})


@app.route("/api/agg/tasks")
def api_agg_tasks():
    con = get_con()
    try:
        tasks = aggmap.list_tasks(con)
        for t in tasks:
            t["source_label"] = table_label(t["source_table"])
        return jsonify({"tasks": tasks})
    finally:
        con.close()


@app.route("/api/agg/task/<int:tid>")
def api_agg_task(tid):
    con = get_con()
    try:
        task = aggmap.get_task(con, tid)
        if not task:
            return jsonify({"ok": False, "error": "任务不存在"}), 404
        task["rows"] = aggmap.task_rows(con, task)
        task["stats"] = aggmap.dim_stats(con, task["dim_table"], aggmap.COL_RAW, task["value_col"])
        task["relations"] = [dict(r) for r in con.execute(
            "SELECT id,source_table,source_col,target_table,target_col FROM map_config "
            "WHERE target_table=? ORDER BY id", (task["dim_table"],)).fetchall()]
        try:
            total, orphan, rate, status = aggmap.rel_stats(
                con, task["source_table"], task["source_col"], task["dim_table"], aggmap.COL_RAW)
        except sqlite3.Error:
            total, orphan, rate, status = 0, 0, 0.0, "err"
        task.update({"ok": True, "total": total, "orphan": orphan,
                     "rate": round(rate, 2), "status": status,
                     "conflicts": aggmap.relation_conflicts(
                         con, task["source_table"], task["source_col"], task["dim_table"])})
        return jsonify(task)
    finally:
        con.close()


@app.route("/api/agg/task/<int:tid>", methods=["PUT"])
def api_agg_task_update(tid):
    body = request.get_json(force=True, silent=True) or {}
    backup_path = _agg_backup("agg_update") if body.get("backup", True) else None
    con = get_con()
    try:
        task = aggmap.get_task(con, tid)
        if not task:
            return jsonify({"ok": False, "error": "任务不存在"}), 404
        stats = None
        if "rows" in body:
            stats = aggmap.rewrite_mapping(con, task, body.get("rows") or [],
                                           extra_cols=body.get("extra_cols") or [])
        if "name" in body or "note" in body:
            con.execute(
                "UPDATE agg_task SET name=COALESCE(?,name), note=COALESCE(?,note), updated_at=? "
                "WHERE id=?",
                ((body.get("name") or "").strip() or None,
                 None if body.get("note") is None else str(body.get("note")).strip(),
                 time.strftime("%Y-%m-%d %H:%M:%S"), tid))
        con.commit()
    except (ValueError, sqlite3.Error) as e:
        con.rollback()
        return jsonify({"ok": False, "error": str(e), "backup": backup_path}), 400
    finally:
        con.close()

    if "name" in body:
        t2 = aggmap_get(tid)
        if t2:
            register_agg_table(t2["dim_table"], t2["name"], t2["source_table"],
                               t2["source_col"], t2["value_col"])
    return jsonify({"ok": True, "stats": stats, "backup": backup_path})


def aggmap_get(tid):
    con = get_con()
    try:
        return aggmap.get_task(con, tid)
    finally:
        con.close()


@app.route("/api/agg/task/<int:tid>/refresh", methods=["POST"])
def api_agg_task_refresh(tid):
    con = get_con()
    try:
        task = aggmap.get_task(con, tid)
        if not task:
            return jsonify({"ok": False, "error": "任务不存在"}), 404
        res = aggmap.refresh_task(con, task)
        con.commit()
    except (ValueError, sqlite3.Error) as e:
        con.rollback()
        return jsonify({"ok": False, "error": str(e)}), 400
    finally:
        con.close()
    msg = (f"源字段新增 {res['added_count']} 个取值已补入维度表"
           if res["added_count"] else "没有新增取值，维度表无需变更")
    return jsonify({"ok": True, "message": msg, "result": res})


@app.route("/api/agg/task/<int:tid>/columns", methods=["POST"])
def api_agg_task_columns(tid):
    """给已建的聚合维度表加附加字段（只建空列，值在补录/编辑里逐行填）。"""
    body = request.get_json(force=True, silent=True) or {}
    cols = body.get("cols") or body.get("columns") or []
    con = get_con()
    try:
        task = aggmap.get_task(con, tid)
        if not task:
            return jsonify({"ok": False, "error": "任务不存在"}), 404
        added = aggmap.add_columns(con, task["dim_table"], cols, task["value_col"])
        con.commit()
        cur = [c[1] for c in con.execute(
            f"PRAGMA table_info({db.qid(task['dim_table'])})").fetchall()]
    except (ValueError, sqlite3.Error) as e:
        con.rollback()
        return jsonify({"ok": False, "error": str(e)}), 400
    finally:
        con.close()
    msg = (f"已添加 {len(added)} 个附加字段：" + "、".join(c["name"] for c in added)
           if added else "这些字段已存在，未重复添加")
    return jsonify({"ok": True, "added": added, "message": msg, "columns": cur,
                    "extra_cols": [c["name"] for c in added]})


@app.route("/api/agg/task/<int:tid>/extras", methods=["PUT"])
def api_agg_task_extras(tid):
    """批量保存附加字段的值。body: {values:[{raw, extra:{列:值}}]}"""
    body = request.get_json(force=True, silent=True) or {}
    con = get_con()
    try:
        task = aggmap.get_task(con, tid)
        if not task:
            return jsonify({"ok": False, "error": "任务不存在"}), 404
        res = aggmap.set_extra_values(con, task, body.get("values") or [])
        con.commit()
    except (ValueError, sqlite3.Error) as e:
        con.rollback()
        return jsonify({"ok": False, "error": str(e)}), 400
    finally:
        con.close()
    msg = f"已更新 {res['updated']} 行的附加字段"
    if res["unknown_cols"]:
        msg += "；以下列名不存在已忽略：" + "、".join(res["unknown_cols"])
    return jsonify({"ok": True, "message": msg, "result": res})


@app.route("/api/agg/pending")
def api_agg_pending():
    """已建聚合表里，源表新出现但还没映射的取值（新数据导入后提醒补映射）。"""
    con = get_con()
    try:
        items = aggmap.pending_values(con)
        for it in items:
            it["source_label"] = table_label(it["source_table"])
        return jsonify({"ok": True, "items": items,
                        "total_new": sum(i["new_count"] for i in items)})
    finally:
        con.close()


@app.route("/api/agg/task/<int:tid>", methods=["DELETE"])
def api_agg_task_delete(tid):
    backup_path = _agg_backup("agg_drop") if request.args.get("backup", "1") != "0" else None
    con = get_con()
    try:
        task = aggmap.get_task(con, tid)
        if not task:
            return jsonify({"ok": False, "error": "任务不存在"}), 404
        rels = aggmap.drop_task(con, task)
        con.commit()
    except sqlite3.Error as e:
        con.rollback()
        return jsonify({"ok": False, "error": str(e)}), 400
    finally:
        con.close()
    unregister_agg_table(task["dim_table"])
    return jsonify({"ok": True, "dim_table": task["dim_table"], "relations": rels,
                    "backup": backup_path,
                    "message": f"已删除 {task['dim_table']}（连带 {rels} 条关系）"})


@app.route("/api/agg/relations-scan", methods=["POST"])
def api_agg_relations_scan():
    """找还有哪些表列装着同样的取值，用于给聚合维度表多加几条关联。"""
    body = request.get_json(force=True, silent=True) or {}
    table = body.get("table") or ""
    col = body.get("col") or ""
    err = _clean_target_ok(table, col)
    if err:
        return jsonify({"ok": False, "error": err}), 400
    con = get_con()
    try:
        hits = clean.scan_cascade(con, table, col, allow_same_name=True)
        for h in hits:
            h["label"] = table_label(h["table"])
        return jsonify({"ok": True, "hits": hits})
    except sqlite3.Error as e:
        return jsonify({"ok": False, "error": str(e)}), 400
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


# ---------------------------------------------------------------------------
# 增量导入：把上传行分成「新增 / 完全一致 / 冲突」三类
#   事实表原则上不改，所以默认只加新行、冲突只提示；用户确认后才覆盖。
# ---------------------------------------------------------------------------
def _short(v, n=80):
    s = "" if v is None else str(v)
    return s if len(s) <= n else s[:n] + "…"


def _same_cell(a, b, is_amount):
    """判断上传值与库里值是否算同一个值。金额走数值比较，其余走去空白文本。"""
    if is_amount:
        na, nb = db.parse_amount(a), db.parse_amount(b)
        if na is None or nb is None:
            return na is None and nb is None
        return abs(na - nb) < 0.005
    sa = "" if a is None else str(a).strip()
    sb = "" if b is None else str(b).strip()
    return sa == sb


def classify_import(con, table, rep, sample_limit=50):
    """判定上传数据里「纯新增 / 与库一致 / 有变化」各多少条，并给出字段级差异。

    比较范围 = 文件实际提供的列（含派生列）。文件里没有的列既不参与比较，也不会
    被覆盖 —— 避免「新导出少几列」时把库里这些列清空。
    """
    cols = rep["db_columns"]
    rows = rep["valid_rows"]
    pk = rep["pk"]
    provided = {x["db"] for x in rep["header_mapping"] if x.get("matched")}
    provided |= set(rep.get("extra_cols") or [])
    updatable = [c for c in cols if c in provided]

    out = {"pk": pk, "updatable_cols": updatable,
           "new": 0, "same": 0, "conflict": 0,
           "conflicts": [], "conflict_keys": [], "conflict_fields": [],
           "truncated": False,
           "file_cols_not_in_db": [x["csv"] for x in rep["header_mapping"]
                                   if not x.get("matched")]}
    if pk not in cols or not rows:
        return out

    pk_idx = cols.index(pk)
    cmp_idx = [(i, c) for i, c in enumerate(cols) if c in set(updatable) and c != pk]
    amount = {i: db.is_amount_header(c) for i, c in cmp_idx}

    keys = {str(r[pk_idx]) for r in rows if r[pk_idx] is not None}
    con.execute("DROP TABLE IF EXISTS temp._imp_pk")
    con.execute("CREATE TEMP TABLE _imp_pk (k TEXT PRIMARY KEY)")
    con.executemany("INSERT OR IGNORE INTO temp._imp_pk (k) VALUES (?)",
                    [(k,) for k in keys])
    sel = ", ".join(db.qid(c) for c in cols)
    existing = {}
    try:
        for r in con.execute(
                f"SELECT {sel} FROM {db.qid(table)} t "
                f"JOIN temp._imp_pk p ON CAST(t.{db.qid(pk)} AS TEXT) = p.k"):
            existing[str(r[pk_idx])] = tuple(r)
    finally:
        con.execute("DROP TABLE IF EXISTS temp._imp_pk")

    hits = {}
    for r in rows:
        key = str(r[pk_idx])
        old = existing.get(key)
        if old is None:
            out["new"] += 1
            continue
        diffs = [{"col": c, "old": _short(old[i]), "new": _short(r[i])}
                 for i, c in cmp_idx if not _same_cell(r[i], old[i], amount[i])]
        if not diffs:
            out["same"] += 1
            continue
        out["conflict"] += 1
        out["conflict_keys"].append(key)
        for d in diffs:
            hits[d["col"]] = hits.get(d["col"], 0) + 1
        if len(out["conflicts"]) < sample_limit:
            out["conflicts"].append({"pk": key, "n_fields": len(diffs), "fields": diffs})
        else:
            out["truncated"] = True
    out["conflict_fields"] = [{"col": c, "rows": n}
                              for c, n in sorted(hits.items(), key=lambda x: -x[1])]
    return out


# ---------------------------------------------------------------------------
# 数据转换（类 Power Query）：源表 + 有序步骤链 → 视图 / 物化表
# ---------------------------------------------------------------------------
def _ro_con():
    """只读连接：预览一律走它，从根上杜绝误写（表达式再怎么写也改不了库）。"""
    con = sqlite3.connect("file:" + DB_PATH.replace("\\", "/") + "?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    for p in _CON_PRAGMAS:
        con.execute(p)
    return con


def _tf_query_row(r):
    d = dict(r)
    try:
        d["steps"] = json.loads(d.get("steps") or "[]")
    except Exception:
        d["steps"] = []
    return d


def _obj_type(table):
    """返回库对象的类型：table / view / None。用于放行「转换产出」的只读浏览。"""
    con = get_con()
    try:
        r = con.execute("SELECT type FROM sqlite_master WHERE name=?", (table,)).fetchone()
        return r[0] if r else None
    except sqlite3.Error:
        return None
    finally:
        con.close()


def _readable(table):
    """表或视图都可读；只读视图用于展示转换结果。"""
    return table in KNOWN_TABLES or _obj_type(table) == "view"


@app.route("/api/transform/meta")
def api_tf_meta():
    """转换页初始化：可作源的库对象 + 步骤类型 + 下拉选项。"""
    con = _ro_con()
    try:
        sources = tf.list_sources(con)
    finally:
        con.close()
    return jsonify({
        "sources": sources,
        "ops": [{"op": k, "label": v[0], "desc": v[1]} for k, v in tf.STEP_LABELS.items()],
        "compare": [
            {"v": "eq", "t": "等于"}, {"v": "ne", "t": "不等于"},
            {"v": "gt", "t": "大于"}, {"v": "ge", "t": "大于等于"},
            {"v": "lt", "t": "小于"}, {"v": "le", "t": "小于等于"},
            {"v": "contains", "t": "包含"}, {"v": "not_contains", "t": "不包含"},
            {"v": "starts", "t": "开头是"}, {"v": "ends", "t": "结尾是"},
            {"v": "in", "t": "包含于(多值)"}, {"v": "not_in", "t": "不包含于(多值)"},
            {"v": "between", "t": "介于"},
            {"v": "is_null", "t": "为空(NULL)"}, {"v": "not_null", "t": "非 NULL"},
            {"v": "is_empty", "t": "为空(含空文本)"}, {"v": "not_empty", "t": "非空"},
        ],
        "aggregates": [
            {"v": "sum", "t": "求和"}, {"v": "count_all", "t": "计数(行数)"},
            {"v": "count", "t": "计数(非空值)"}, {"v": "count_distinct", "t": "去重计数"},
            {"v": "avg", "t": "平均值"}, {"v": "min", "t": "最小值"}, {"v": "max", "t": "最大值"},
        ],
        "types": [
            {"v": "text", "t": "文本"}, {"v": "integer", "t": "整数"},
            {"v": "number", "t": "小数"}, {"v": "date", "t": "日期"},
        ],
        # 日期歧义的消解方式：'2/1/2024' 到底是 2 月 1 日还是 1 月 2 日
        "dateOrders": [
            {"v": "auto", "t": "自动识别"},
            {"v": "ymd", "t": "年-月-日"},
            {"v": "dmy", "t": "日-月-年"},
            {"v": "mdy", "t": "月-日-年"},
        ],
    })


@app.route("/api/transform/columns")
def api_tf_columns():
    table = request.args.get("table") or ""
    con = _ro_con()
    try:
        return jsonify({"ok": True, "columns": tf.get_columns(con, table)})
    except tf.TransformError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    finally:
        con.close()


@app.route("/api/transform/preview", methods=["POST"])
def api_tf_preview():
    d = request.get_json(force=True, silent=True) or {}
    con = _ro_con()
    try:
        r = tf.preview(con, d.get("source"), d.get("steps") or [],
                       upto=d.get("upto"), limit=int(d.get("limit") or 100))
        return jsonify({"ok": True, **r})
    except tf.TransformError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except sqlite3.Error as e:
        return jsonify({"ok": False, "error": f"SQL 执行失败：{e}"}), 400
    finally:
        con.close()


@app.route("/api/transform/values", methods=["POST"])
def api_tf_values():
    """点表头筛选时取某一列的去重取值，供值面板勾选。"""
    d = request.get_json(force=True, silent=True) or {}
    con = _ro_con()
    try:
        r = tf.column_values(con, d.get("source"), d.get("steps") or [],
                             d.get("column"), upto=d.get("upto"),
                             limit=int(d.get("limit") or 500))
        return jsonify({"ok": True, **r})
    except tf.TransformError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except sqlite3.Error as e:
        return jsonify({"ok": False, "error": f"SQL 执行失败：{e}"}), 400
    finally:
        con.close()


@app.route("/api/transform/colstats", methods=["POST"])
def api_tf_colstats():
    """表头质量条：每列的非空占比 / 空值数 / 去重值数。"""
    d = request.get_json(force=True, silent=True) or {}
    con = _ro_con()
    try:
        r = tf.column_stats(con, d.get("source"), d.get("steps") or [],
                            upto=d.get("upto"))
        return jsonify({"ok": True, **r})
    except tf.TransformError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except sqlite3.Error as e:
        return jsonify({"ok": False, "error": f"SQL 执行失败：{e}"}), 400
    finally:
        con.close()


@app.route("/api/transform/queries")
def api_tf_queries():
    con = get_con()
    try:
        rows = con.execute(
            "SELECT id, name, label, descr, source_table, steps, output_mode, enabled, "
            "last_run, last_rows, last_error, created_at, updated_at "
            "FROM transform_query ORDER BY id").fetchall()
        out = []
        for r in rows:
            q = _tf_query_row(r)
            q["step_count"] = len([s for s in q["steps"] if not s.get("disabled")])
            typ = con.execute("SELECT type FROM sqlite_master WHERE name=?", (q["name"],)).fetchone()
            q["exists"] = bool(typ)
            q["output_type"] = typ[0] if typ else None
            out.append(q)
        return jsonify(out)
    finally:
        con.close()


@app.route("/api/transform/queries", methods=["POST"])
def api_tf_create():
    d = request.get_json(force=True, silent=True) or {}
    name = (d.get("name") or "").strip()
    source = d.get("source_table") or d.get("source")
    steps = d.get("steps") or []
    mode = "table" if d.get("output_mode") == "table" else "view"
    if not source:
        return jsonify({"ok": False, "error": "请选择源表"}), 400

    con = get_con()
    try:
        if not name:
            name = tf.suggest_name(con, source, mode)   # 留空按源表名自动派生
        exists = con.execute("SELECT 1 FROM transform_query WHERE name=?", (name,)).fetchone()
        if exists:
            return jsonify({"ok": False, "error": f"已存在同名转换「{name}」"}), 400
        info = {"rows": None, "sql": None}
        if d.get("apply", True):
            try:
                info = tf.apply_query(con, name, source, steps, mode,
                                      overwrite=bool(d.get("overwrite")))
            except tf.TransformError as e:
                return jsonify({"ok": False, "error": str(e), "sql": None}), 400
        else:
            try:
                comp = tf.compile_query(con, source, steps)
                info = {"rows": None, "sql": comp["sql"]}
            except tf.TransformError as e:
                return jsonify({"ok": False, "error": str(e)}), 400
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        con.execute(
            "INSERT INTO transform_query (name,label,descr,source_table,steps,output_mode,"
            "enabled,last_run,last_rows,last_error,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,1,?,?,NULL,?,?)",
            (name, d.get("label") or name, d.get("descr") or "", source,
             json.dumps(steps, ensure_ascii=False), mode, now, info.get("rows"), now, now))
        con.commit()
        return jsonify({"ok": True, "name": name, "rows": info.get("rows"),
                        "sql": info.get("sql"), "message": f"已保存并生成{'视图' if mode == 'view' else '数据表'}「{name}」"})
    finally:
        con.close()


@app.route("/api/transform/queries/<int:qid_>", methods=["PUT"])
def api_tf_update(qid_):
    d = request.get_json(force=True, silent=True) or {}
    con = get_con()
    try:
        cur = con.execute("SELECT * FROM transform_query WHERE id=?", (qid_,)).fetchone()
        if not cur:
            return jsonify({"ok": False, "error": "转换不存在"}), 404
        old = dict(cur)
        name = (d.get("name") or old["name"]).strip()
        source = d.get("source_table") or old["source_table"]
        steps = d.get("steps") if d.get("steps") is not None else json.loads(old["steps"] or "[]")
        mode = "table" if (d.get("output_mode") or old["output_mode"]) == "table" else "view"

        info = {"rows": old.get("last_rows"), "sql": None}
        if d.get("apply", True):
            # 改名 / 改输出类型：先把旧对象清掉，避免留下孤儿视图
            if name != old["name"] or mode != old["output_mode"]:
                try:
                    tf.drop_output(con, old["name"])
                except Exception:
                    pass
            try:
                info = tf.apply_query(con, name, source, steps, mode, overwrite=True)
            except tf.TransformError as e:
                return jsonify({"ok": False, "error": str(e)}), 400
        else:
            try:
                info = {"rows": None, "sql": tf.compile_query(con, source, steps)["sql"]}
            except tf.TransformError as e:
                return jsonify({"ok": False, "error": str(e)}), 400
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        con.execute(
            "UPDATE transform_query SET name=?,label=?,descr=?,source_table=?,steps=?,"
            "output_mode=?,last_run=?,last_rows=?,last_error=NULL,updated_at=? WHERE id=?",
            (name, d.get("label") or old.get("label") or name, d.get("descr") if d.get("descr") is not None else old.get("descr"),
             source, json.dumps(steps, ensure_ascii=False), mode, now, info.get("rows"), now, qid_))
        con.commit()
        return jsonify({"ok": True, "name": name, "rows": info.get("rows"),
                        "sql": info.get("sql"), "message": f"已更新「{name}」"})
    finally:
        con.close()


@app.route("/api/transform/queries/<int:qid_>", methods=["DELETE"])
def api_tf_delete(qid_):
    drop = request.args.get("drop", "1") == "1"
    con = get_con()
    try:
        cur = con.execute("SELECT name, output_mode FROM transform_query WHERE id=?", (qid_,)).fetchone()
        if not cur:
            return jsonify({"ok": False, "error": "转换不存在"}), 404
        name = cur["name"]
        con.execute("DELETE FROM transform_query WHERE id=?", (qid_,))
        dropped = False
        if drop:
            try:
                dropped = tf.drop_output(con, name)
            except Exception:
                dropped = False
        con.commit()
        return jsonify({"ok": True, "message": f"已删除「{name}」" + ("（含输出对象）" if dropped else "")})
    finally:
        con.close()


@app.route("/api/transform/queries/<int:qid_>/refresh", methods=["POST"])
def api_tf_refresh(qid_):
    con = get_con()
    try:
        cur = con.execute("SELECT * FROM transform_query WHERE id=?", (qid_,)).fetchone()
        if not cur:
            return jsonify({"ok": False, "error": "转换不存在"}), 404
        q = dict(cur)
        steps = json.loads(q["steps"] or "[]")
        try:
            info = tf.apply_query(con, q["name"], q["source_table"], steps,
                                  q["output_mode"], overwrite=True)
        except tf.TransformError as e:
            con.execute("UPDATE transform_query SET last_error=?, last_run=? WHERE id=?",
                        (str(e), time.strftime("%Y-%m-%d %H:%M:%S"), qid_))
            con.commit()
            return jsonify({"ok": False, "error": str(e)}), 400
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        con.execute("UPDATE transform_query SET last_run=?, last_rows=?, last_error=NULL WHERE id=?",
                    (now, info["rows"], qid_))
        con.commit()
        return jsonify({"ok": True, "rows": info["rows"], "message": f"已刷新「{q['name']}」，{info['rows']} 行"})
    finally:
        con.close()


@app.route("/api/transform/test", methods=["POST"])
def api_tf_test():
    """不落库，只编译并试跑一次，返回 SQL 供「查看 SQL」使用。"""
    d = request.get_json(force=True, silent=True) or {}
    con = _ro_con()
    try:
        comp = tf.compile_query(con, d.get("source"), d.get("steps") or [], upto=d.get("upto"))
        return jsonify({"ok": True, "sql": comp["sql"],
                        "columns": comp["columns"], "steps": comp["steps"]})
    except tf.TransformError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    finally:
        con.close()


def _is_local_request():
    """请求是否来自本机回环（IPv4 / IPv6 / 反代透传前的原始地址）。"""
    addr = (request.remote_addr or "").strip()
    return addr in ("127.0.0.1", "::1", "localhost", "")


@app.route("/api/shutdown", methods=["POST"])
def api_shutdown():
    """终止本服务进程（仅用于本地管理工具，需二次确认后由页面触发）。"""
    # 即便服务被 HOST=0.0.0.0 暴露到局域网，也不允许远程访客顺手关掉它
    if not _is_local_request():
        return jsonify({"ok": False, "error": "仅允许在本机终止服务"}), 403

    def _do_shutdown():
        time.sleep(0.3)            # 留出时间把响应发回前端
        if SERVER is not None:
            SERVER.shutdown()      # 必须在非 serve_forever 线程中调用
    threading.Thread(target=_do_shutdown, daemon=True).start()
    return jsonify({"ok": True, "message": "服务正在关闭…"})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    # 默认只绑回环：本机可用、局域网不可达。要共享给同事再显式 HOST=0.0.0.0。
    host = os.environ.get("HOST", "127.0.0.1").strip() or "127.0.0.1"
    browse_host = "localhost" if host in ("0.0.0.0", "::", "") else host
    url = f"http://{browse_host}:{port}"
    # OPEN_PATH 让启动器把浏览器直接落到某个页面（如 /dash 采购看板），
    # 不设则打开根页面（数据表列表）。
    # 注意：在 Git Bash 里导出 OPEN_PATH=/dash 时，MSYS 会把以 / 开头的值
    # 改写成 Windows 路径（D:/Program Files/.../dash），这里把它还原成 /dash。
    _open_path = os.environ.get("OPEN_PATH", "").strip()
    if _open_path and (":" in _open_path or "\\" in _open_path):
        _open_path = "/" + _open_path.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
    if _open_path:
        if not _open_path.startswith("/"):
            _open_path = "/" + _open_path
        open_url = url + _open_path
    else:
        open_url = url
    print(f"采购数据库管理页面已启动： {url}")
    if _open_path:
        print(f"自动打开页面： {open_url}")
    if host in ("0.0.0.0", "::"):
        if ACCESS_TOKEN:
            print("（已绑定所有网卡，访问需口令：在地址后加 ?token=***）")
        else:
            print("！警告：已绑定所有网卡但没有设置 DBADMIN_TOKEN，同一局域网任何人"
                  "都可读写本库、修改映射。若只是本机使用，去掉 HOST=0.0.0.0 即可。")
    # 服务就绪后自动打开浏览器（设 BROWSER=0 可关闭，适合无界面环境）
    if os.environ.get("BROWSER", "1") != "0":
        threading.Timer(1.0, lambda: webbrowser.open(open_url)).start()

    # 首屏缓存预热：起后台线程把看板的 4 个默认接口先算一遍，
    # 用户打开页面时直接命中缓存（冷盘首访 5.9s → 约 0.2s）。WARMUP=0 可关闭。
    if os.environ.get("WARMUP", "1") != "0":
        def _warm_board():
            _WARMUP_TLS.on = True
            try:
                from board_api import warm_up
                warm_up(app, log=print)
            except Exception as _e:  # noqa: BLE001
                print("！看板缓存预热失败：%s" % _e)
            finally:
                _WARMUP_TLS.on = False
        threading.Thread(target=_warm_board, name="board-warmup", daemon=True).start()

    SERVER = make_server(host, port, app, threaded=True)
    SERVER.serve_forever()
