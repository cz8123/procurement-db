#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""采购支出分析看板数据接口（Flask Blueprint，/api/board）

参考对象：Sime Darby Motors HK「Procurement Spend Intelligence Dashboard」POC
（6 KPI + 全局筛选 + 5 张图）。字段按本项目实际数据落地：
  Spend → Total Price / MonthStart → 财月 / Server → Region_cn
  PurchaseCategory → Category / ExpenditureAccount → Expense Type（OPEX/TRADE/A&P/CAPEX）
  Supplier → Supplier_cn（不用 Supplier_en）

金额：库内是 RMB 原值，前端按 rate 换算（RM = RMB / 1.5），与旧口径一致。
挂载（app.py）：app.register_blueprint(board_bp)
"""
import csv
import io
import json
import os
import sqlite3
import time

from flask import Blueprint, Response, jsonify, request
from werkzeug.datastructures import MultiDict

from supplier_name import split_en, clean_en, english_variants

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "procurement.db")
board_bp = Blueprint("board", __name__, url_prefix="/api/board")

# 看板筛选维度（key → dash_po 列）
FILTERS = {
    "brand": "Brand",
    "category": "Category",
    "sub": "Sub-Category",
    "expense": "Expense Type",
    "level": "Supplier Category",
    "region": "Region_cn",
    "company": "Company_cn",
    "method": "Procurement Method",
    "supplier": "Supplier_cn",
}

CP_METHODS = ("SS-Exempt", "SS-Non Exempt", "Tender")

# 子类别面板点选的是「聚合值」：筛选必须比聚合值，否则下钻后一条都选不中。
# 用单表相关子查询而不是 JOIN，dash_po 的每个查询都能直接用（244 行小表，代价可忽略）
SUB_EXPR = ('COALESCE((SELECT m."采购子类别" FROM dim_agg_采购子类别 m '
            'WHERE m."原始值" = p."Sub-Category"), p."Sub-Category")')
# 只有 sub 需要换表达式，其余仍是裸列名（_id() 会加引号）
FILTER_EXPR = {"sub": SUB_EXPR}

# 区域图例统一英文：Region_en 里「二手车 / 多品牌」都写成 Others 会撞名，
# 所以这里给一份全站唯一对照（取不到就回落中文原值）
REGION_EN = {"中国区": "HO", "南区": "South", "西区": "West",
             "超豪": "Super Luxury", "二手车": "Used Cars", "多品牌": "Multi-brand"}

# 采购方式饼图的第二层：单一来源两种方式各自按「理由」聚合（走 dim_agg 映射）
REASON_DRILL = {
    "SS-Exempt": ("豁免单一来源的理由", "dim_agg_豁免单一来源的理由"),
    "SS-Non Exempt": ("单一来源采购理由", "dim_agg_单一来源采购理由"),
}
REASON_FILTERS = {
    "exempt_reason": "豁免单一来源的理由",
    "single_reason": "单一来源采购理由",
}
RATES = {"RM": 1.5, "RMB": 1.0}

# 主题色：取 Regional Sales Sample.pbip 的 Fidex 主题（蓝灰阶 + 主题 min/max）
PALETTE = ["#1E2F4B", "#3B5E96", "#627EAB", "#A0D1FF",
           "#B1BFD5", "#4D6172", "#8FA3C0", "#2C4771"]

# 财月轴标签：财年 1 月 = 自然 7 月（界面全英文）
_MONTH = {1: "Jan", 2: "Feb", 3: "Mar", 4: "Apr", 5: "May", 6: "Jun",
          7: "Jul", 8: "Aug", 9: "Sep", 10: "Oct", 11: "Nov", 12: "Dec"}
_MONTH_LONG = {1: "January", 2: "February", 3: "March", 4: "April",
               5: "May", 6: "June", 7: "July", 8: "August",
               9: "September", 10: "October", 11: "November", 12: "December"}


def con():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


# ---------------------------------------------------------------------------
# 轻量缓存：以库文件的 (mtime_ns, size) 当版本号
#   任何写入（导入、管理台改行、删表）都会改 mtime → 缓存自动失效，
#   既不会读到旧数据，也不用手工清缓存。
#   为什么需要：首屏要打 options/data/top/contracts 四个接口，每个都要扫
#   dash_po（46k 行）或 vw_fa_analysis（视图，一次 ~80ms）；缓存的都是
#   「与筛选无关或变化极慢」的部分（筛选项、协议 Type/日期边界）。
# ---------------------------------------------------------------------------
_CACHE = {}


def _db_stamp():
    """库版本号：(主库, -wal) 的 (mtime_ns, size)。
    默认 journal_mode=delete，写入直接改主库 mtime；万一以后切到 WAL，
    主库 mtime 可能长时间不变，所以把 -wal 也算进来，避免缓存看到旧数据。"""
    out = []
    for p in (DB, DB + "-wal"):
        try:
            s = os.stat(p)
            out.append((s.st_mtime_ns, s.st_size))
        except OSError:
            out.append(None)
    return tuple(out)


def _cached(key, build):
    stamp = _db_stamp()
    hit = _CACHE.get(key)
    if hit and hit[0] == stamp:
        return hit[1]
    val = build()
    _CACHE[key] = (stamp, val)
    return val


# 整响应缓存：只缓存 GET 的只读聚合接口，命中条件同样是「库没被改过」。
# 刷新一次页面会重复请求同一组参数（默认财年 + 无筛选），缓存后基本 0ms；
# 一旦导入/改行改了 mtime，全部自动失效。
_JSON_CACHE_PATHS = ("/api/board/data", "/api/board/top",
                     "/api/board/contracts", "/api/board/options",
                     "/api/board/detail", "/api/board/detail/values",
                     "/api/board/method-drill", "/api/board/geo")
_JSON_CACHE_MAX = 40


def _arg_key():
    return tuple(sorted((k, v) for k, v in request.args.items(multi=True)))


@board_bp.before_request
def _json_cache_hit():
    if request.method != "GET" or request.path not in _JSON_CACHE_PATHS:
        return None
    hit = _CACHE.get(("resp", request.path) + _arg_key())
    if hit and hit[0] == _db_stamp():
        return Response(hit[1], mimetype="application/json")
    return None


@board_bp.after_request
def _json_cache_store(resp):
    if (request.method != "GET" or request.path not in _JSON_CACHE_PATHS
            or resp.status_code != 200 or resp.direct_passthrough):
        return resp
    live = [k for k in _CACHE if isinstance(k, tuple) and k and k[0] == "resp"]
    if len(live) >= _JSON_CACHE_MAX:
        for k in live[:len(live) - _JSON_CACHE_MAX + 1]:
            _CACHE.pop(k, None)
    _CACHE[("resp", request.path) + _arg_key()] = (_db_stamp(), resp.get_data())
    return resp


# ---------------------------------------------------------------------------
# 启动预热
#   首访的耗时不在 SQL 本身，而在「第一次读盘」：dash_po 全表扫描 +
#   vw_fa_analysis 求值 + 供应商英文映射，刚开机/进程刚起时实测 5.9s。
#   启动后用内部 test_client 把首屏那 4 个默认请求先算一遍，
#   页面打开时直接命中整响应缓存（连同 SQLite 页缓存一起热起来）。
#
#   URL 必须与前端首访请求逐字一致——参数进了缓存键：
#     data      = qs()                        → 只有 cur（fy 未选）
#     top       = qs({category:'',method:''}) → 空值不下发，但此时 S.fy 已被
#                 data 返回的 fy 填上，所以实际是 cur + fy（财年每次开年都会变，
#                 故不能在常量里写死，跑完 data 再从响应里取 fy 拼）
#     contracts = fcqs()                      → cur/page/size/sort/dir
#   改前端默认参数（如默认排序）时同步这里，否则预热白做。
# ---------------------------------------------------------------------------
WARMUP_URLS = (
    "/api/board/options",
    "/api/board/data?cur=RMB",
    "/api/board/contracts?cur=RMB&page=1&size=50&sort=start&dir=desc",
)
WARMUP_TOP = "/api/board/top?cur=RMB"


def warm_up(app, log=print):
    """把首屏默认请求跑一遍填满缓存。必须在后台线程调用（冷盘要 1-6s），
    否则 serve_forever 会被挡住。返回 (成功数, 总数, 总用时秒)。"""
    t_all = time.time()
    ok = total = 0
    client = app.test_client()

    def hit(url):
        nonlocal ok, total
        total += 1
        t0 = time.time()
        try:
            resp = client.get(url)
        except Exception as e:  # noqa: BLE001
            log("  预热 %s 失败：%s" % (url, e))
            return None
        if resp.status_code != 200:
            log("  预热 %s → HTTP %s（未命中，跳过）" % (url, resp.status_code))
            return None
        ok += 1
        log("  预热 %-56s %6.0fms" % (url, (time.time() - t0) * 1000))
        return resp

    fy = None
    for url in WARMUP_URLS:
        resp = hit(url)
        if resp is not None and url.startswith("/api/board/data"):
            try:
                fy = (resp.get_json(silent=True) or {}).get("fy")
            except Exception:  # noqa: BLE001
                fy = None
    # 前端在 data 返回后才知道当前财年，Top10 请求会带上它
    hit(WARMUP_TOP + ("&fy=%s" % fy if fy else ""))

    cost = time.time() - t_all
    log("看板缓存预热完成：%d/%d，用时 %.1fs" % (ok, total, cost))
    return ok, total, cost


def _id(name):
    return '"' + name.replace('"', '""') + '"'


def _num(v):
    return round(v, 2) if isinstance(v, float) else v


def _ratio(num, den):
    """占比 0~1；分母缺失或为 0 时返回 None"""
    if num is None or not den:
        return None
    return round(num / den, 4)


def _rate(args):
    cur = (args.get("cur") or "RMB").upper()
    if cur not in RATES:
        cur = "RMB"
    return cur, RATES[cur]


# ---------------------------------------------------------------- 供应商英文名
# 供应商在 fct_supplier / dash_po 里写作「中文全称-English name」，但
# vw_fa_analysis.供应商名称 是单语种（要么纯中文、要么纯英文），所以框架协议表
# 想显示英文只能反查这张「中文 → 英文」映射。
def _with_en(rows, con):
    """给供应商聚合行补一个 en（显示用英文名，已补回被源系统吞掉的空格）。"""
    variants = english_variants(con)
    for r in rows:
        r["en"] = clean_en(split_en(r.get("k")), variants)
    return rows


_EN_CACHE = {"key": None, "map": {}}


def supplier_en_map(con):
    """中文名 → 英文名。按库文件 mtime 缓存，避免每次请求都重建。"""
    try:
        key = os.path.getmtime(DB)
    except OSError:
        key = None
    if _EN_CACHE["key"] == key and _EN_CACHE["map"]:
        return _EN_CACHE["map"]
    m = {}
    variants = english_variants(con)
    for sql in ('SELECT DISTINCT "Vendor Name" FROM fct_supplier',
                'SELECT DISTINCT "Supplier_cn" FROM dash_po'):
        try:
            rows = con.execute(sql).fetchall()
        except sqlite3.Error:
            continue
        for r in rows:
            name = r[0]
            en_name = clean_en(split_en(name), variants)   # 顺手补回被吞掉的空格
            if not en_name:
                continue
            i = name.find("-")
            m[name[:i].strip()] = en_name     # 中文段 → 英文
            m[name] = en_name                 # 全称 → 英文
    _EN_CACHE["key"] = key
    _EN_CACHE["map"] = m
    return m


def _filters(args):
    parts, params = [], []
    for key, col in FILTERS.items():
        vals = [v for v in args.getlist(key) if v not in ("", None)]
        if not vals:
            continue
        expr = FILTER_EXPR.get(key) or _id(col)
        parts.append("%s IN (%s)" % (expr, ",".join("?" * len(vals))))
        params.extend(vals)
    # 「理由」只存在于 fct_prpo（dash_po 无此列）：用单号子查询接入，避免每次查询都 JOIN
    for key, col in REASON_FILTERS.items():
        vals = [v for v in args.getlist(key) if v not in ("", None)]
        if vals:
            parts.append('p."No." IN (SELECT "表单编号" FROM fct_prpo WHERE %s IN (%s))'
                         % (_id(col), ",".join("?" * len(vals))))
            params.extend(vals)
    return parts, params


def _scope(args, fy, extra=None):
    """返回 (WHERE 片段, 参数)，已带财年。
    dateFrom / dateTo 是 Detail 页的日期范围切片器（ISO 字符串，闭区间）。"""
    parts, params = _filters(args)
    d1 = (args.get("dateFrom") or "").strip()
    d2 = (args.get("dateTo") or "").strip()
    if d1:
        parts.append('p."Date" >= ?')
        params.append(d1)
    if d2:
        parts.append('p."Date" <= ?')
        params.append(d2)
    # 指定了日期范围就以日期为准，不再叠加财年（Detail 的日期切片器可跨财年）
    has_date = bool(d1 or d2)
    w = ([] if has_date else ['c."FiscalYearName" = ?']) + parts + list(extra or [])
    return " AND ".join(w), ([] if has_date else [fy]) + params


def _fy_list(c):
    return [r[0] for r in c.execute(
        'SELECT DISTINCT "FiscalYearName" FROM dash_calendar '
        'WHERE "Date" IN (SELECT "Date" FROM dash_po) ORDER BY 1')]


def _prev_fy(fy):
    if not fy or len(fy) != 6:
        return None
    try:
        return "FY%02d%02d" % (int(fy[2:4]) - 1, int(fy[4:6]) - 1)
    except ValueError:
        return None


def _yoy(cur, prev):
    if not prev or cur is None:
        return None
    return (cur - prev) / prev


def _median_days(c, where, params):
    """SQL 侧算中位数，避免把几万行取回 Python"""
    sql = ('SELECT AVG(t) FROM ('
           '  SELECT t, ROW_NUMBER() OVER (ORDER BY t) rn, COUNT(*) OVER () n FROM ('
           '    SELECT p."Time Taken" t FROM dash_po p '
           '    JOIN dash_calendar c ON p."Date"=c."Date" '
           '    WHERE %s AND p."Time Taken" IS NOT NULL'
           '  ) WHERE t IS NOT NULL'
           ') WHERE rn IN ((n + 1) / 2, (n + 2) / 2)') % where
    v = c.execute(sql, params).fetchone()[0]
    return round(v, 2) if v is not None else None


# ------------------------------------------------------------------ options
@board_bp.get("/options")
def options():
    regs = tuple(v for v in request.args.getlist("region") if v)
    return jsonify(_cached(("options",) + regs, lambda: _build_options(regs)))


def _build_options(regs=()):
    c = con()
    try:
        fys = _fy_list(c)
        out = {"fy": fys, "latestFy": fys[-1] if fys else None,
               "range": dict(zip(("from", "to"), c.execute(
                   'SELECT MIN("Date"),MAX("Date") FROM dash_po').fetchone())),
               "rows": c.execute('SELECT COUNT(*) FROM dash_po').fetchone()[0],
               "options": {}}
        # 每个财年实际的日期区间：Detail 的日期切片器与财年下拉双向联动用
        # （选财年 → 日期自动填这个区间；改日期 → 财年置空）
        out["fyRange"] = {r[0]: {"from": r[1], "to": r[2]} for r in c.execute(
            'SELECT FY, MIN("Date"), MAX("Date") FROM dash_po '
            "WHERE FY IS NOT NULL AND TRIM(FY) <> '' GROUP BY FY ORDER BY FY")}
        for key, col in FILTERS.items():
            # supplier 4982 个值，前端走抽屉搜索；sub 只在帕累托下钻时点选，
            # 都不进筛选下拉，省掉一次全表 GROUP BY
            if key in ("supplier", "sub"):
                continue
            extra, extra_p = [], []
            # Company 是 Region 的下级：选中区域后只展示这些区域下的公司
            if key == "company":
                if regs:
                    extra.append('%s IN (%s)' % (_id(FILTERS["region"]),
                                                 ",".join("?" * len(regs))))
                    extra_p.extend(regs)
            rows = c.execute(
                'SELECT %s AS v, COUNT(*) AS n FROM dash_po WHERE %s IS NOT NULL '
                'AND TRIM(%s)<>\'\' %s GROUP BY 1 ORDER BY n DESC'
                % (_id(col), _id(col), _id(col),
                   (' AND ' + " AND ".join(extra)) if extra else ''), extra_p).fetchall()
            out["options"][key] = [{"v": r["v"], "n": r["n"]} for r in rows]
        return out
    finally:
        c.close()


# -------------------------------------------------------------------- data
@board_bp.get("/data")
def data():
    """一页看板的全部数据：KPI + 5 张图（一次请求）"""
    c = con()
    try:
        fys = _fy_list(c)
        fy = request.args.get("fy") or (fys[-1] if fys else None)
        if not fy:
            return jsonify({"error": "dash_po 为空，请先运行 dash_build.py"}), 400
        pfy = _prev_fy(fy)
        cur, rate = _rate(request.args)

        where, params = _scope(request.args, fy)
        base = ('FROM dash_po p JOIN dash_calendar c ON p."Date"=c."Date" WHERE '
                + where)
        cp_in = "(" + ",".join("'%s'" % m for m in CP_METHODS) + ")"
        # spec：每个视觉对象的「取值计算逻辑」说明（SQL 模板 + 当前实际参数）
        spec = {}

        # ---------------- KPI（本期 / 上期）
        ksql = ('SELECT SUM(p."Total Price") amount, '
                'SUM(CASE WHEN p."Procurement Method" IN %s THEN p."Total Price" END) cp, '
                'SUM(COALESCE(p."PO Paid",0)) po_paid, '
                'COUNT(p."No.") txn, COUNT(DISTINCT p."Supplier_cn") suppliers, '
                'COUNT(DISTINCT p."Company_cn") stores, COUNT(DISTINCT p."Category") cats '
                + base) % cp_in
        spec["kpi"] = ksql
        row = dict(c.execute(ksql, params).fetchone())
        prv = {}
        if pfy:
            pwhere, pparams = _scope(request.args, pfy)
            pbase = 'FROM dash_po p JOIN dash_calendar c ON p."Date"=c."Date" WHERE ' + pwhere
            prv = dict(c.execute(
                ('SELECT SUM(p."Total Price") amount, '
                 'SUM(CASE WHEN p."Procurement Method" IN %s THEN p."Total Price" END) cp, '
                 'SUM(COALESCE(p."PO Paid",0)) po_paid, '
                 'COUNT(p."No.") txn, COUNT(DISTINCT p."Supplier_cn") suppliers, '
                 'COUNT(DISTINCT p."Company_cn") stores, COUNT(DISTINCT p."Category") cats '
                 + pbase) % cp_in, pparams).fetchone())

        days = _median_days(c, where, params)
        pdays = _median_days(c, *_scope(request.args, pfy)) if pfy else None

        kpi = [
            {"k": "amount", "label": "Total Spend", "fmt": "money",
             "v": row["amount"], "pv": prv.get("amount")},
            {"k": "cp", "label": "Centralised Spend", "fmt": "money",
             "v": row["cp"], "pv": prv.get("cp"), "sub": "SS / Tender"},
            {"k": "poSpend", "label": "PO Paid", "fmt": "money",
             "v": row["po_paid"], "pv": prv.get("po_paid"), "sub": "Actually paid"},
            {"k": "txn", "label": "Orders", "fmt": "int",
             "v": row["txn"], "pv": prv.get("txn")},
            {"k": "suppliers", "label": "Suppliers", "fmt": "int",
             "v": row["suppliers"], "pv": prv.get("suppliers"), "sub": "unique"},
            {"k": "stores", "label": "Stores Covered", "fmt": "int",
             "v": row["stores"], "pv": prv.get("stores")},
            {"k": "days", "label": "Median Cycle Time", "fmt": "days",
             "v": days, "pv": pdays, "sub": "submit → approve"},
        ]
        for k in kpi:
            k["yoy"] = _yoy(k["v"], k.get("pv"))
            k["v"] = _num(k["v"])
            if k.get("pv") is not None:
                k["pv"] = _num(k["pv"])

        # ---------------- Chart 1 月度：Total vs Framework Contract（双柱）
        # 两个系列同口径：Total = 全部采购申请；Frame = 通过框架协议方式申请的部分
        # （判据与执行率一致，用原始值 'Ongoing Frame Contract'，不映射）
        msql = ('SELECT c."FiscalMonthNumber" fm, '
                'COALESCE(NULLIF(TRIM(p."Region_cn"),\'\'),\'Unassigned\') r, '
                'SUM(p."Total Price") v, '
                'SUM(CASE WHEN p."Exception Reason" = ? '
                'THEN p."Total Price" ELSE 0 END) frm ' + base + ' GROUP BY 1,2')
        spec["monthly"] = msql
        labels = [_MONTH[(fm + 5) % 12 + 1] for fm in range(1, 13)]
        labels_long = [_MONTH_LONG[(fm + 5) % 12 + 1] for fm in range(1, 13)]
        tot_s = [0.0] * 12
        frm_s = [0.0] * 12
        reg_sum = {}
        for r in c.execute(msql, ['Ongoing Frame Contract'] + params):
            fm = r["fm"]
            if not (isinstance(fm, int) and 1 <= fm <= 12):
                continue
            v = r["v"] or 0
            tot_s[fm - 1] = round(tot_s[fm - 1] + v, 2)
            frm_s[fm - 1] = round(frm_s[fm - 1] + (r["frm"] or 0), 2)
            reg = r["r"] or "Unassigned"
            if reg not in reg_sum:
                reg_sum[reg] = [0.0] * 12
            reg_sum[reg][fm - 1] = round(reg_sum[reg][fm - 1] + v, 2)
        # 区域按财年总额降序，图例与堆叠顺序稳定；l = 图例英文名（k 仍是中文，筛选照旧）
        order = sorted(reg_sum, key=lambda k: -sum(reg_sum[k]))
        series = [{"k": k, "l": REGION_EN.get(k, k), "vals": reg_sum[k]} for k in order]

        # 上期总额序列（保留给口径说明/未来对比用；双柱图本身不再画虚线）
        prev_line = [0.0] * 12
        if pfy:
            pwhere, pparams = _scope(request.args, pfy)
            psql = ('SELECT c."FiscalMonthNumber" fm, SUM(p."Total Price") v '
                    'FROM dash_po p JOIN dash_calendar c ON p."Date"=c."Date" WHERE '
                    + pwhere + ' GROUP BY 1')
            spec["monthly_prev"] = psql
            for r in c.execute(psql, pparams):
                if isinstance(r["fm"], int) and 1 <= r["fm"] <= 12:
                    prev_line[r["fm"] - 1] = round(r["v"] or 0, 2)

        monthly = {"labels": labels, "labelsLong": labels_long,
                   "series": series, "total": tot_s, "frame": frm_s,
                   "cur": tot_s, "prev": prev_line}

        # ---------------- Chart 2 类别帕累托（降序 + 累计）
        psql2 = ('SELECT COALESCE(NULLIF(TRIM(p."Category"),\'\'),\'Unassigned\') k, '
                 'SUM(p."Total Price") v, COUNT(p."No.") n, '
                 'COUNT(DISTINCT p."Supplier_cn") sup, '
                 'SUM(COALESCE(p."PO Paid",0)) po_paid ' + base +
                 ' GROUP BY 1 ORDER BY v DESC')
        spec["pareto"] = psql2
        prow = [dict(r) for r in c.execute(psql2, params)]
        tot = sum((r["v"] or 0) for r in prow) or 0
        acc = 0.0
        for r in prow:
            acc += r["v"] or 0
            r["cum"] = round(acc / tot, 4) if tot else None
            r["share"] = round((r["v"] or 0) / tot, 4) if tot else None
        # 每个类别下的采购子类别（Table - 采购子类别 → dash_po."Sub-Category"）
        # 子类别有聚合（dim_agg_采购子类别：244 个原始值 → 144 个聚合名），
        # 表格与下钻一律用聚合值，和用户在管理台维护的映射保持同步
        # subMap = 金额最高的那一条（表格折叠态显示）；subsMap = 全部明细（展开时显示）
        sbase = ('FROM dash_po p JOIN dash_calendar c ON p."Date"=c."Date" '
                 'LEFT JOIN dim_agg_采购子类别 sm ON sm."原始值" = p."Sub-Category" '
                 'WHERE ' + where)
        ssql = ('SELECT COALESCE(NULLIF(TRIM(p."Category"),\'\'),\'Unassigned\') cat, '
                'COALESCE(NULLIF(TRIM(sm."采购子类别"),\'\'), '
                'NULLIF(TRIM(p."Sub-Category"),\'\'), \'Unassigned\') sub, '
                'SUM(p."Total Price") v, COUNT(p."No.") n, '
                'SUM(COALESCE(p."PO Paid",0)) po_paid, '
                'COUNT(DISTINCT p."Supplier_cn") sup ' + sbase +
                ' GROUP BY 1,2 ORDER BY 1,3 DESC')
        spec["subs"] = ssql
        sub_map, subs_map = {}, {}
        for r in c.execute(ssql, params):
            it = {"k": r["sub"], "v": round(r["v"] or 0, 2),
                  "n": r["n"] or 0, "po": round(r["po_paid"] or 0, 2),
                  "sup": r["sup"] or 0}
            subs_map.setdefault(r["cat"], []).append(it)
            if r["cat"] not in sub_map and r["sub"] != "Unassigned":
                sub_map[r["cat"]] = r["sub"]
        pareto = {"rows": prow, "total": round(tot, 2),
                  "subMap": sub_map, "subsMap": subs_map}

        # ---------------- Chart 3 支出类型环形
        dsql = ('SELECT COALESCE(NULLIF(TRIM(p."Expense Type"),\'\'),\'Unclassified\') k, '
                'SUM(p."Total Price") v, COUNT(p."No.") n ' + base +
                ' GROUP BY 1 ORDER BY v DESC')
        spec["donut"] = dsql
        donut = [dict(r) for r in c.execute(dsql, params)]
        dtot = sum((r["v"] or 0) for r in donut) or 0
        for r in donut:
            r["share"] = round((r["v"] or 0) / dtot, 4) if dtot else None

        # ---------------- Chart 3b 采购方式环形（同口径：金额 & 占比）
        msql = ('SELECT COALESCE(NULLIF(TRIM(p."Procurement Method"),\'\'),\'Unassigned\') k, '
                'SUM(p."Total Price") v, COUNT(p."No.") n ' + base +
                ' GROUP BY 1 ORDER BY v DESC')
        spec["methodDonut"] = msql
        mdonut = [dict(r) for r in c.execute(msql, params)]
        mdtot = sum((r["v"] or 0) for r in mdonut) or 0
        for r in mdonut:
            r["share"] = round((r["v"] or 0) / mdtot, 4) if mdtot else None

        # ---------------- Chart 4/5 供应商 TopN
        tsql = ('SELECT COALESCE(NULLIF(TRIM(p."Supplier_cn"),\'\'),\'Unassigned\') k, '
                'SUM(p."Total Price") v, COUNT(p."No.") n, '
                'COUNT(DISTINCT p."Category") cats ' + base +
                ' GROUP BY 1 ORDER BY v DESC LIMIT 10')
        spec["top"] = tsql
        top = _with_en([dict(r) for r in c.execute(tsql, params)], c)

        # 热力：列 = Top8 品类，行 = Top10 供应商
        cols = [r["k"] for r in prow[:8]]
        names = [r["k"] for r in top]
        heat = {"cols": cols, "rows": []}
        if names and cols:
            ph = ",".join("?" * len(names))
            pc = ",".join("?" * len(cols))
            # 取值表达式要与上面的 COALESCE 一致，否则「Unassigned」行匹配不到 NULL
            sup_expr = 'COALESCE(NULLIF(TRIM(p."Supplier_cn"),\'\'),\'Unassigned\')'
            cat_expr = 'COALESCE(NULLIF(TRIM(p."Category"),\'\'),\'Unassigned\')'
            cell = {}
            hsql = ('SELECT ' + sup_expr + ' s, ' + cat_expr + ' cat, '
                    'SUM(p."Total Price") v ' + base +
                    ' AND ' + sup_expr + ' IN (%s) AND ' % ph + cat_expr + ' IN (%s) '
                    'GROUP BY 1,2' % pc)
            spec["heat"] = hsql
            for r in c.execute(hsql, params + names + cols):
                cell[(r["s"], r["cat"])] = r["v"] or 0
            mx = max(cell.values()) if cell else 0
            en_map = {r["k"]: r.get("en") for r in top}
            heat["rows"] = [{"k": nm, "en": en_map.get(nm),
                             "vals": [round(cell.get((nm, ct), 0), 2) for ct in cols]}
                            for nm in names]
            heat["max"] = round(mx, 2)

        return jsonify({
            "fy": fy, "prevFy": pfy, "cur": cur, "rate": rate,
            "range": {"from": c.execute(
                'SELECT MIN(p."Date") ' + base, params).fetchone()[0],
                "to": c.execute('SELECT MAX(p."Date") ' + base, params).fetchone()[0]},
            "status": {"txn": row["txn"], "suppliers": row["suppliers"],
                       "stores": row["stores"]},
            "kpi": kpi, "monthly": monthly, "pareto": pareto,
            "donut": donut, "methodDonut": mdonut,
            "top": top, "heat": heat, "palette": PALETTE,
            "spec": spec,
            # 当前生效的取数参数（供「计算逻辑」说明展示）
            "params": {
                "fy": fy, "prevFy": pfy, "cur": cur, "rate": rate,
                "where": where, "values": [str(x) for x in params],
                "filters": {k: request.args.getlist(k) for k in
                            ("brand", "category", "expense", "supplier_attr", "region", "company")
                            if request.args.getlist(k)},
                "cpMethods": list(CP_METHODS),
            },
        })
    finally:
        c.close()


# ---------------------------------------------------- method drill (第二层)
@board_bp.get("/method-drill")
def method_drill():
    """采购方式饼图第二层：SS-Exempt → 豁免理由、SS-Non Exempt → 单一来源理由。
    单独接口按需取（放 /data 里会让每次整页刷新多跑两次全表 JOIN）。"""
    mk = (request.args.get("method") or "").strip()
    if mk not in REASON_DRILL:
        return jsonify({"error": "unknown method"}), 400
    c = con()
    try:
        fys = _fy_list(c)
        fy = request.args.get("fy") or (fys[-1] if fys else None)
        if not fy:
            return jsonify({"error": "dash_po 为空"}), 400
        cur, rate = _rate(request.args)
        where, params = _scope(request.args, fy)
        rcol, atbl = REASON_DRILL[mk]
        rmap = {}
        for a, raw in c.execute('SELECT %s, "原始值" FROM %s' % (_id(rcol), _id(atbl))):
            rmap.setdefault(a, []).append(raw)
        dbase = ('FROM dash_po p JOIN dash_calendar c ON p."Date"=c."Date" '
                 'LEFT JOIN fct_prpo f ON f."表单编号"=p."No." '
                 'LEFT JOIN (SELECT "原始值" src, %s agg FROM %s) m ON m.src = f.%s '
                 'WHERE %s AND p."Procurement Method"=?'
                 % (_id(rcol), _id(atbl), _id(rcol), where))
        rsql = ('SELECT COALESCE(m.agg, NULLIF(TRIM(f.%s),\'\'), \'Unassigned\') k, '
                'SUM(p."Total Price") v, COUNT(p."No.") n %s GROUP BY 1 ORDER BY v DESC'
                % (_id(rcol), dbase))
        rows = [dict(r) for r in c.execute(rsql, params + [mk])]
        tot = sum((r["v"] or 0) for r in rows) or 0
        for r in rows:
            r["share"] = round((r["v"] or 0) / tot, 4) if tot else None
            r["raws"] = rmap.get(r["k"], [])
        return jsonify({"method": mk, "reason": rcol, "rows": rows,
                        "total": round(tot, 2), "fy": fy, "cur": cur, "rate": rate})
    finally:
        c.close()


# -------------------------------------------------------------------- top
@board_bp.get("/top")
def top_suppliers():
    """Top10 供应商（供 Chart 5 的局部筛选单独刷新，不必重算全页）"""
    c = con()
    try:
        fys = _fy_list(c)
        fy = request.args.get("fy") or (fys[-1] if fys else None)
        where, params = _scope(request.args, fy)
        tsql = ('SELECT COALESCE(NULLIF(TRIM(p."Supplier_cn"),\'\'),\'Unassigned\') k, '
                'SUM(p."Total Price") v, COUNT(p."No.") n, '
                'COUNT(DISTINCT p."Category") cats, '
                'SUM(COALESCE(p."PO Paid",0)) po_paid '
                'FROM dash_po p JOIN dash_calendar c ON p."Date"=c."Date" WHERE ' + where +
                ' GROUP BY 1 ORDER BY v DESC LIMIT 10')
        rows = _with_en([dict(r) for r in c.execute(tsql, params)], c)
        return jsonify({"fy": fy, "rows": rows,
                        "spec": {"sql": tsql, "where": where,
                                 "values": [str(x) for x in params]}})
    finally:
        c.close()


# -------------------------------------------------------------------- rows
_SORTS = {
    "date": 'p."Date"', "amount": 'p."Total Price"', "days": 'p."Time Taken"',
    "supplier": 'p."Supplier_cn"', "company": 'p."Company_cn"',
    "category": 'p."Category"', "no": 'p."No."', "expense": 'p."Expense Type"',
    "poPaid": 'p."PO Paid"',
}

_ROWS_SELECT = (
    'p."No." no, p."Date" date, p."Company_cn" company, p."Region_cn" region, '
    'p."Brand" brand, p."Department" dept, p."Category" category, '
    'p."Expense Type" expense, p."Procurement Method" method, '
    'p."Supplier_cn" supplier, p."Total Price" amount, p."Time Taken" days, '
    'p."Budget Type" budget, p."Supplier Category" level, '
    'p."PO Paid" poPaid'
)


def _rows_where(args, fy, agg=False):
    where, params = _scope(args, fy)
    # 财月（1=7 月）：月度图点柱子时下钻用
    fm = (args.get("fm") or "").strip()
    if fm.isdigit() and 1 <= int(fm) <= 12:
        where += ' AND c."FiscalMonthNumber" = ?'
        params = list(params) + [int(fm)]
    q = (args.get("q") or "").strip()
    if q:
        like = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        if agg:
            # 按月/公司聚合时搜的是「公司/区域」，不能拿单号去筛行（否则只统计到那一行，金额偏低）
            where += (' AND (p."Company_cn" LIKE ? ESCAPE "\\" '
                      'OR p."Region_cn" LIKE ? ESCAPE "\\")')
            params += [like, like]
        else:
            where += (' AND (p."No." LIKE ? ESCAPE "\\" OR p."Supplier_cn" LIKE ? ESCAPE "\\" '
                      'OR p."Company_cn" LIKE ? ESCAPE "\\" OR p."Category" LIKE ? ESCAPE "\\" '
                      'OR p."Department" LIKE ? ESCAPE "\\" OR p."Brand" LIKE ? ESCAPE "\\")')
            params += [like] * 6
    return where, params


@board_bp.get("/rows")
def rows():
    c = con()
    try:
        fys = _fy_list(c)
        fy = request.args.get("fy") or (fys[-1] if fys else None)
        page = max(int(request.args.get("page", 1) or 1), 1)
        size = min(max(int(request.args.get("size", 50) or 50), 10), 200)
        by_company = (request.args.get("by") or "").strip().lower() == "company"
        where, params = _rows_where(request.args, fy, agg=by_company)
        base = 'FROM dash_po p JOIN dash_calendar c ON p."Date"=c."Date" WHERE ' + where
        # by=company：抽屉按公司聚合（一行一家公司），不再逐单列出
        if by_company:
            return jsonify(_company_rows(c, base, params, page, size, request.args))
        sort = _SORTS.get(request.args.get("sort", "date"), 'p."Date"')
        dirn = "ASC" if request.args.get("dir") == "asc" else "DESC"
        total = c.execute("SELECT COUNT(*) " + base, params).fetchone()[0]
        amt = c.execute('SELECT SUM(p."Total Price") ' + base, params).fetchone()[0]
        paid = c.execute('SELECT SUM(COALESCE(p."PO Paid",0)) ' + base, params).fetchone()[0]
        sql = ('SELECT ' + _ROWS_SELECT + ' ' + base +
               ' ORDER BY %s %s, p."No." ASC LIMIT ? OFFSET ?' % (sort, dirn))
        data = [dict(r) for r in c.execute(sql, params + [size, (page - 1) * size])]
        return jsonify({"total": total, "page": page, "size": size,
                        "amount": _num(amt), "poPaid": _num(paid), "rows": data})
    finally:
        c.close()


# 公司聚合视图可排序列（别名 → SQL 表达式），days = 平均处理天数
_AG_SORTS = {"amount": "amount", "poPaid": "paid", "n": "n", "days": "days",
             "sup": "sup", "company": "company", "region": "region"}


def _company_rows(c, base, params, page, size, args):
    """按公司聚合的下钻：公司 / 区域 / 单数 / 供应商数 / 采购额 / PO Paid / 平均周期"""
    grp = ('SELECT p."Company_cn" company, '
           'COALESCE(NULLIF(TRIM(p."Region_cn"),\'\'),\'—\') region, '
           'COUNT(*) n, COUNT(DISTINCT p."Supplier_cn") sup, '
           'SUM(p."Total Price") amount, SUM(COALESCE(p."PO Paid",0)) paid, '
           'AVG(p."Time Taken") days ' + base + ' GROUP BY 1,2')
    sk = _AG_SORTS.get(args.get("sort") or "amount", "amount")
    dirn = "ASC" if args.get("dir") == "asc" else "DESC"
    total, amt, paid = c.execute(
        'SELECT COUNT(*), SUM(amount), SUM(paid) FROM (' + grp + ')', params).fetchone()
    data = []
    for r in c.execute(grp + ' ORDER BY %s %s, company ASC LIMIT ? OFFSET ?' % (sk, dirn),
                       params + [size, (page - 1) * size]):
        d = dict(r)
        d["poPaid"] = d.pop("paid")       # 前端沿用 poPaid 键
        d["days"] = round(d["days"], 1) if d["days"] is not None else None
        d["amount"] = _num(d["amount"])
        d["poPaid"] = _num(d["poPaid"])
        data.append(d)
    return {"total": total or 0, "page": page, "size": size, "by": "company",
            "amount": _num(amt), "poPaid": _num(paid), "rows": data}


@board_bp.get("/export.csv")
def export_csv():
    c = con()
    try:
        fys = _fy_list(c)
        fy = request.args.get("fy") or (fys[-1] if fys else None)
        cur, rate = _rate(request.args)
        where, params = _rows_where(request.args, fy)
        sort = _SORTS.get(request.args.get("sort", "date"), 'p."Date"')
        dirn = "ASC" if request.args.get("dir") == "asc" else "DESC"
        base = 'FROM dash_po p JOIN dash_calendar c ON p."Date"=c."Date" WHERE ' + where
        sql = ('SELECT ' + _ROWS_SELECT + ' ' + base +
               ' ORDER BY %s %s, p."No." ASC' % (sort, dirn))

        def gen():
            # 流式响应的生成器在视图 return 之后才消费，连接必须活到这里；
            # 若沿用外层的 finally，视图返回时连接就被关掉，下载会断在表头后（curl exit 18）。
            try:
                buf = io.StringIO()
                w = csv.writer(buf)
                w.writerow(["No.", "Date", "Company", "Region", "Brand", "Department",
                            "Category", "Expense Type", "Procurement Method", "Supplier",
                            "Amount (%s)" % cur, "PO Paid (%s)" % cur, "Days",
                            "Budget Type", "Supplier Category"])
                yield "\ufeff" + buf.getvalue()
                buf.seek(0)
                buf.truncate(0)
                n = 0
                for r in c.execute(sql, params):
                    amt = r["amount"]
                    w.writerow([r["no"], r["date"], r["company"], r["region"], r["brand"],
                                r["dept"], r["category"], r["expense"], r["method"],
                                r["supplier"],
                                round(amt / rate, 2) if amt is not None else "",
                                round((r["poPaid"] or 0) / rate, 2),
                                r["days"], r["budget"], r["level"]])
                    n += 1
                    if n % 800 == 0:
                        yield buf.getvalue()
                        buf.seek(0)
                        buf.truncate(0)
                yield buf.getvalue()
            finally:
                c.close()
        fname = "Procurement_Detail_%s_%s.csv" % (fy, cur)
        return Response(gen(), mimetype="text/csv",
                        headers={"Content-Disposition":
                                 "attachment; filename*=UTF-8''%s" %
                                 __import__("urllib.parse", fromlist=["quote"]).quote(fname)})
    except Exception:
        c.close()
        raise


# ================================================================ 框架协议统计
# 参考图示「Frame Contract Info」：Contract / Supplier / CV / PA / Usage /
# Start Date / Expiry Date / Remain Days，带 Type、日期区间、名称搜索。
# 数据源 vw_fa_analysis（190 行，框架协议视角，不随 PRPO 丢行）。
# 【执行率口径】实际采购额(PA) 只统计「通过框架协议方式申请」的采购单：
#   PBI 1_Frame Contract = CALCULATE(SUM(PO[Total Price]), PO[Exception Reason]="Framework Contract")
#   本项目在视图 agg CTE 里用 fct_prpo."豁免单一来源的理由"='Ongoing Frame Contract' 过滤
#   （CSV 源措辞 Ongoing Frame Contract == PBI 的 Framework Contract，同一语义）。
#   当前库 13,764 行有框架合同编号的单 100% 命中该条件，口径先固化以防未来混入非框架协议单。
CONTRACT_SORTS = {
    "contract": '"合同名称CN"', "supplier": '"供应商名称"',
    "cv": '"预估金额"', "pa": '"实际采购额"', "usage": '"执行率"',
    "start": '"开始日期"', "expiry": '"结束日期"', "remain": '"剩余天数"',
    "type": '"适用领域"',
}
CONTRACT_COLS = (
    '"合同名称CN" contract, "供应商名称" supplier, "预估金额" cv, '
    '"实际采购额" pa, "执行率" usage, "开始日期" start, "结束日期" expiry, '
    '"剩余天数" remain, "适用领域" type'
)


def _cn_by_en(con, s):
    """供应商英文名 → 中文名。框架协议表显示的是英文，搜索框却打的「供应商名称」
    （单语种，多半是中文），不反查就会出现「表里明明有 Kaisi，搜 Kaisi 却 0 行」。"""
    low = s.lower()
    try:
        m = supplier_en_map(con)
    except sqlite3.Error:
        return []
    return [cn for cn, en in m.items() if en and low in en.lower()][:500]


def _contract_where(args, con=None):
    """合同表是独立数据源，不吃 PRPO 的全局筛选（无品牌/区域/品类字段）"""
    parts, params = ["1=1"], []
    if args.get("type"):
        parts.append('"适用领域" = ?')
        params.append(args["type"])
    if args.get("df"):
        parts.append('"开始日期" >= ?')
        params.append(args["df"])
    if args.get("dt"):
        parts.append('"开始日期" <= ?')
        params.append(args["dt"])
    for key, col in (("q", '"合同名称CN"'), ("sup", '"供应商名称"')):
        s = (args.get(key) or "").strip()
        if s:
            like = "%" + s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            cn_hits = _cn_by_en(con, s) if (key == "sup" and con is not None) else []
            if cn_hits:
                parts.append('(' + col + ' LIKE ? ESCAPE "\\" OR ' + col + ' IN (' +
                             ",".join("?" * len(cn_hits)) + '))')
                params.append(like)
                params.extend(cn_hits)
            else:
                parts.append(col + ' LIKE ? ESCAPE "\\"')
                params.append(like)
    status = args.get("status")
    if status == "expired":
        parts.append('"剩余天数" < 0')
    elif status == "expiring":
        parts.append('"剩余天数" BETWEEN 0 AND 90')
    elif status == "active":
        parts.append('"剩余天数" > 90')
    return " AND ".join(parts), params


@board_bp.get("/contracts")
def contracts():
    c = con()
    try:
        where, params = _contract_where(request.args, c)
        page = max(int(request.args.get("page", 1) or 1), 1)
        size = min(max(int(request.args.get("size", 50) or 50), 10), 200)
        sort = CONTRACT_SORTS.get(request.args.get("sort", "start"), '"开始日期"')
        dirn = "ASC" if request.args.get("dir") == "asc" else "DESC"

        # vw_fa_analysis 是视图（每次求值 ~80ms）。以前 summary / rows / types / bounds
        # 各扫一遍 = 4 次 ≈ 330ms；现在窗口函数把汇总并进取数那一次（OVER () 在
        # WHERE 之后、LIMIT 之前算，正好是「筛选后的全集」），types/bounds 走缓存。
        rows_sql = (
            'SELECT ' + CONTRACT_COLS +
            ', COUNT(*) OVER () AS _n'
            ', SUM("预估金额_可加") OVER () AS _cv'
            ', SUM("实际采购额") OVER () AS _pa'
            ', SUM(CASE WHEN "剩余天数" < 0 THEN 1 ELSE 0 END) OVER () AS _expired'
            ', SUM(CASE WHEN "剩余天数" BETWEEN 0 AND 90 THEN 1 ELSE 0 END) OVER () AS _expiring'
            ', SUM(CASE WHEN "执行率" > 1 THEN 1 ELSE 0 END) OVER () AS _overrun'
            ' FROM vw_fa_analysis WHERE ' + where +
            ' ORDER BY %s %s LIMIT ? OFFSET ?' % (sort, dirn))
        raw = [dict(r) for r in c.execute(rows_sql, params + [size, (page - 1) * size])]
        if raw:
            h = raw[0]
            summary = {"n": int(h["_n"] or 0), "cv": h["_cv"], "pa": h["_pa"],
                       "expired": int(h["_expired"] or 0),
                       "expiring": int(h["_expiring"] or 0),
                       "overrun": int(h["_overrun"] or 0)}
            rows = [{k: v for k, v in r.items() if not k.startswith("_")} for r in raw]
        else:
            # 页码越界（当前筛选下一页为空）：退回单查汇总，保证分页器数字正确
            summary = dict(c.execute(
                'SELECT COUNT(*) n, SUM("预估金额_可加") cv, SUM("实际采购额") pa, '
                'SUM(CASE WHEN "剩余天数" < 0 THEN 1 ELSE 0 END) expired, '
                'SUM(CASE WHEN "剩余天数" BETWEEN 0 AND 90 THEN 1 ELSE 0 END) expiring, '
                'SUM(CASE WHEN "执行率" > 1 THEN 1 ELSE 0 END) overrun '
                'FROM vw_fa_analysis WHERE ' + where, params).fetchone())
            rows = []
        cv, pa = summary.get("cv") or 0, summary.get("pa") or 0
        summary["cv"] = round(cv, 2)
        summary["pa"] = round(pa, 2)
        summary["usage"] = round(pa / cv, 4) if cv else None
        for k in ("n", "expired", "expiring", "overrun"):
            summary[k] = int(summary.get(k) or 0)
        smap = supplier_en_map(c)
        variants = english_variants(c)
        for r in rows:
            # 前端显示英文名（已补空格）；hover 用原 supplier（全称）
            r["supplier_en"] = clean_en(
                smap.get(r.get("supplier")) or split_en(r.get("supplier")), variants)
        # Type 选项与日期边界跟筛选无关（不随 where 变），按库版本缓存
        meta = _cached("fa_meta", lambda: {
            "types": [dict(r) for r in c.execute(
                'SELECT "适用领域" k, COUNT(*) n FROM vw_fa_analysis '
                'GROUP BY 1 ORDER BY n DESC')],
            "bounds": dict(c.execute(
                'SELECT MIN("开始日期") mn, MAX("开始日期") mx '
                'FROM vw_fa_analysis').fetchone()),
        })
        types, bounds = meta["types"], meta["bounds"]
        return jsonify({"total": summary["n"], "page": page, "size": size,
                        "summary": summary, "types": types, "bounds": bounds,
                        "rows": rows,
                        "spec": {
                            "summary": ('COUNT(*)/SUM(...) 与取数同一次扫描：'
                                        'COUNT(*) OVER (), SUM(...) OVER () '
                                        'FROM vw_fa_analysis WHERE ' + where),
                            "rows": rows_sql,
                            "params": [str(x) for x in params] + [str(size), str((page - 1) * size)],
                            "where": where,
                        }})
    finally:
        c.close()


@board_bp.get("/contracts.csv")
def contracts_csv():
    import urllib.parse
    _c0 = con()
    try:
        where, params = _contract_where(request.args, _c0)
    finally:
        _c0.close()
    cur, rate = _rate(request.args)
    sort = CONTRACT_SORTS.get(request.args.get("sort", "start"), '"开始日期"')
    dirn = "ASC" if request.args.get("dir") == "asc" else "DESC"
    sql = ('SELECT ' + CONTRACT_COLS + ' FROM vw_fa_analysis WHERE ' + where +
           ' ORDER BY %s %s' % (sort, dirn))

    def gen():
        # 流式响应：连接只能在生成器里关（同 export.csv 的教训）
        c = con()
        try:
            buf = io.StringIO()
            w = csv.writer(buf)
            w.writerow(["Contract", "Supplier", "CV (%s)" % cur,
                        "PA (%s)" % cur, "Usage", "Start Date",
                        "Expiry Date", "Remain Days", "Type"])
            yield "\ufeff" + buf.getvalue()
            buf.seek(0); buf.truncate(0)
            n = 0
            for r in c.execute(sql, params):
                w.writerow([
                    r["contract"], r["supplier"],
                    round(r["cv"] / rate, 2) if r["cv"] is not None else "",
                    round(r["pa"] / rate, 2) if r["pa"] is not None else "",
                    "" if r["usage"] is None else round(r["usage"] * 100, 2),
                    r["start"], r["expiry"], r["remain"], r["type"],
                ])
                n += 1
                if n % 200 == 0:
                    yield buf.getvalue()
                    buf.seek(0); buf.truncate(0)
            yield buf.getvalue()
        finally:
            c.close()

    fname = "Framework_Agreements_%s.csv" % cur
    return Response(gen(), mimetype="text/csv",
                    headers={"Content-Disposition":
                             "attachment; filename*=UTF-8''%s" % urllib.parse.quote(fname)})


# -------------------------------------------------------------------- detail
# Detail 页大表格：区域 / 公司 / 类别 / 供应商 / 采购方法 / 说明 / PRPO金额 / PO Paid
# 每列都可搜索（LIKE）、筛选（多选取值 / 数值区间）、排序；三级树 + 全集合计，不分页。
DETAIL_TEXT = {
    "region": "Region", "city": "City", "company": "Company", "category": "Category",
    "sub": "Sub-Category",
    # 表头短名（显示名以 board.js 的 DT_COLS 为准，这里只作语义标注）
    "supplier": "Supplier", "method": "Method",
    # 豁免理由 / 单一来源理由 两列互斥（FY2627 同时非空 0 条），合并成一列
    "reason": "Reason",
    # 财政年度：行级字段 dash_po.FY（FY2425/FY2526/FY2627），跨财年日期范围时
    # 同一张表会同时出现多个财年，所以每行带出自己的 FY 并支持按财年筛选
    "fy": "FY",
}
# 长文本列：只给「搜索」，不给取值勾选（说明有 4 万多种，勾选没意义）
DETAIL_LONG = {"note"}
# 只给搜索、不给取值勾选：说明（取值太多）+ 日期（几百个日期勾选没意义）
DETAIL_SEARCH_ONLY = DETAIL_LONG | {"date"}
DETAIL_EXPR = {
    "region": 'p."Region_cn"',
    # 城市在 dash_po 上就是行级字段（26 个取值、0 空值，且公司→城市 1:1），
    # 不用再 JOIN fct_company_location；城市是区域的下级、公司的上级（五级树）
    "city": 'p."City_cn"',
    "company": 'p."Company_cn"',
    "category": 'p."Category"',
    # 采购子类别：走 dim_agg 聚合（244 原始值 → 144 聚合名），与筛选口径同一表达式
    "sub": SUB_EXPR,
    "supplier": 'p."Supplier_cn"',
    "method": 'p."Procurement Method"',
    # 豁免单一来源的理由 与 单一来源采购理由 不会同时出现，合并一列：
    # 优先取 dim_agg 的聚合值，聚合表里没有该原始值就回落原文
    "reason": ('COALESCE(mx."豁免单一来源的理由", f."豁免单一来源的理由", '
               'ms."单一来源采购理由", f."单一来源采购理由")'),
    # 财政年度：dash_po 上已有的行级字段，与日期表 FiscalYearName 一致
    "fy": 'p."FY"',
    # fct_prpo.说明 → dash_po."Note"（PRPO 的申请说明）
    "note": 'p."Note"',
    # 申请日期：dash_po."Date" 已是 ISO 文本，直接显示/排序，不参与金额换算
    "date": 'p."Date"',
    "amount": 'p."Total Price"',
    "paid": 'COALESCE(p."PO Paid",0)',
}
DETAIL_SORTS = dict(DETAIL_EXPR)
DETAIL_SELECT = ', '.join(
    '%s %s' % (expr, key) for key, expr in DETAIL_EXPR.items())
# fct_prpo."表单编号" 唯一（46,013/46,013），1:1 LEFT JOIN 不会放大行数；
# 两张 dim_agg 的主键是原始值，也是 1:1
DETAIL_BASE = ('FROM dash_po p JOIN dash_calendar c '
               'ON p."Date"=c."Date" '
               'LEFT JOIN fct_prpo f ON f."表单编号"=p."No." '
               'LEFT JOIN dim_agg_豁免单一来源的理由 mx '
               'ON mx."原始值"=f."豁免单一来源的理由" '
               'LEFT JOIN dim_agg_单一来源采购理由 ms '
               'ON ms."原始值"=f."单一来源采购理由" WHERE ')


def _like_esc(v):
    """LIKE 通配符转义，配合 SQL 的 ESCAPE '\\'。"""
    return (str(v).replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_'))


def _detail_extra(args, skip=None):
    """每列的搜索 / 取值筛选 / 数值区间 → (WHERE 片段, 参数)。
    skip 用于取值列表：排除自身那一列，否则选中某值后列表只剩自己。"""
    parts, params = [], []
    for key, expr in DETAIL_EXPR.items():
        if key == skip:
            continue
        q = (args.get("q_" + key) or "").strip()
        # 长文本列（说明）只支持全文搜索，不做取值勾选
        if q and (key in DETAIL_TEXT or key in DETAIL_SEARCH_ONLY):
            parts.append("%s LIKE ? ESCAPE '\\'" % expr)
            params.append("%" + _like_esc(q) + "%")
        raw = args.get("f_" + key)
        if raw and key in DETAIL_TEXT:
            try:
                vals = json.loads(raw)
            except (ValueError, TypeError):
                vals = None
            if isinstance(vals, list) and vals:
                vals = [str(v) for v in vals if str(v) != ""]
                if vals:
                    parts.append("%s IN (%s)" % (expr, ",".join("?" * len(vals))))
                    params.extend(vals)
        if key in ("amount", "paid"):
            for side, op in (("min", ">="), ("max", "<=")):
                raw = args.get(side + "_" + key)
                if raw in (None, ""):
                    continue
                try:
                    num = float(raw)
                except (ValueError, TypeError):
                    continue
                parts.append("%s %s ?" % (expr, op))
                params.append(num)
    return parts, params


def _detail_scope(args, fy, extra_parts, extra_params):
    where, params = _scope(args, fy, extra_parts)
    return where, params + extra_params      # _scope 不拼 extra 的参数，这里补上


@board_bp.get("/detail")
def detail():
    """Detail 大表格：三级树（区域→公司→明细），一次返回筛选全集，不分页。
    合计 / 区域小计 / 公司小计都按筛选全集算，前端只做折叠渲染。"""
    c = con()
    try:
        fys = _fy_list(c)
        fy = request.args.get("fy") or (fys[-1] if fys else None)
        if not fy:
            return jsonify({"error": "dash_po 为空，请先运行 dash_build.py"}), 400
        cur, rate = _rate(request.args)
        ep, ev = _detail_extra(request.args)
        where, params = _detail_scope(request.args, fy, ep, ev)
        base = DETAIL_BASE + where

        tot = dict(c.execute(
            'SELECT COUNT(*) n, SUM(%s) amount, SUM(%s) paid %s'
            % (DETAIL_EXPR["amount"], DETAIL_EXPR["paid"], base), params).fetchone())
        sort = DETAIL_SORTS.get(request.args.get("sort"), 'p."Date"')
        dirn = "ASC" if request.args.get("dir") == "asc" else "DESC"
        # 默认按区域升序，让同一区域/公司的行聚在一起；其余排序在组内生效
        sql = ('SELECT ' + DETAIL_SELECT + ' ' + base +
               ' ORDER BY %s %s, p."No." DESC' % (sort, dirn))
        rows = [dict(r) for r in c.execute(sql, params)]
        for r in rows:
            for k in ("amount", "paid"):
                r[k] = None if r[k] is None else round(r[k] / rate, 2)
            for k in DETAIL_TEXT:
                if r[k] is None:
                    r[k] = ""
            # 说明：表格只显示一行，全文走悬停浮窗。库里最长 2,367 字符，超过 500 的
            # 只有 25 行，放开上限只多约 30KB，换来浮窗能给出完整说明
            if r.get("note") is None:
                r["note"] = ""
            elif len(r["note"]) > 4000:
                r["note"] = r["note"][:4000]
        sums = {k: (None if tot.get(k) is None else round(tot[k] / rate, 2))
                for k in ("amount", "paid")}
        # 区域 / 公司分组行小计：按筛选全集算
        rt = [dict(r) for r in c.execute(
            'SELECT %s region, COUNT(*) n, SUM(%s) amount, SUM(%s) paid %s '
            'GROUP BY 1 ORDER BY 3 DESC'
            % (DETAIL_EXPR["region"], DETAIL_EXPR["amount"], DETAIL_EXPR["paid"], base), params)]
        # 城市小计：Region → City → Company → Category 树的第二级
        yt = [dict(r) for r in c.execute(
            'SELECT %s region, %s city, COUNT(*) n, SUM(%s) amount, SUM(%s) paid %s '
            'GROUP BY 1, 2 ORDER BY 1, 5 DESC'
            % (DETAIL_EXPR["region"], DETAIL_EXPR["city"],
               DETAIL_EXPR["amount"], DETAIL_EXPR["paid"], base), params)]
        ct = [dict(r) for r in c.execute(
            'SELECT %s region, %s city, %s company, COUNT(*) n, '
            'SUM(%s) amount, SUM(%s) paid %s '
            'GROUP BY 1, 2, 3 ORDER BY 1, 2, 6 DESC'
            % (DETAIL_EXPR["region"], DETAIL_EXPR["city"], DETAIL_EXPR["company"],
               DETAIL_EXPR["amount"], DETAIL_EXPR["paid"], base), params)]
        # 第四级（类目）小计：Region → Company → Category → 明细
        gt = [dict(r) for r in c.execute(
            'SELECT %s region, %s city, %s company, %s category, COUNT(*) n, '
            'SUM(%s) amount, SUM(%s) paid %s '
            'GROUP BY 1, 2, 3, 4 ORDER BY 1, 2, 3, 7 DESC'
            % (DETAIL_EXPR["region"], DETAIL_EXPR["city"], DETAIL_EXPR["company"],
               DETAIL_EXPR["category"], DETAIL_EXPR["amount"], DETAIL_EXPR["paid"], base), params)]
        for group in (rt, yt, ct, gt):
            for r in group:
                r["region"] = r["region"] or ""
                if "city" in r:
                    r["city"] = r["city"] or ""
                if "company" in r:
                    r["company"] = r["company"] or ""
                if "category" in r:
                    r["category"] = r["category"] or ""
                for k in ("amount", "paid"):
                    r[k] = None if r[k] is None else round(r[k] / rate, 2)
        return jsonify({"rows": rows, "total": int(tot["n"] or 0), "sums": sums,
                        "regionTotals": rt, "cityTotals": yt,
                        "companyTotals": ct, "categoryTotals": gt,
                        "fy": fy, "cur": cur, "rate": rate,
                        "columns": list(DETAIL_TEXT) + ["note", "date", "amount", "paid"]})
    finally:
        c.close()


@board_bp.get("/detail/values")
def detail_values():
    """某一文本列的取值及次数（用于表头筛选面板，带搜索）。"""
    col = request.args.get("col")
    if col not in DETAIL_TEXT:
        return jsonify({"error": "unknown column"}), 400
    c = con()
    try:
        fys = _fy_list(c)
        fy = request.args.get("fy") or (fys[-1] if fys else None)
        ep, ev = _detail_extra(request.args, skip=col)
        parts = list(ep)
        params = list(ev)
        q = (request.args.get("q") or "").strip()
        if q:
            parts.append("%s LIKE ? ESCAPE '\\'" % DETAIL_EXPR[col])
            params.append("%" + _like_esc(q) + "%")
        where, params = _detail_scope(request.args, fy, parts, params)
        rows = c.execute(
            'SELECT %s v, COUNT(*) n %s GROUP BY 1 ORDER BY n DESC LIMIT 200'
            % (DETAIL_EXPR[col], DETAIL_BASE + where), params).fetchall()
        return jsonify([{"v": (r["v"] if r["v"] is not None else ""), "n": r["n"]}
                        for r in rows])
    finally:
        c.close()


# -------------------------------------------------------------------- geo
# 地图 / 同比页的四个可切换口径。金额一律取 dash_po（RMB 原值，已物化成数值），
# 「竞争力不足」只在 fct_prpo 里，走主键子查询接入，避免 JOIN 放大行数。
GEO_METRICS = {
    "prpo": ("PRPO Spend", 'SUM(p."Total Price")'),
    "paid": ("PO Paid", 'SUM(COALESCE(p."PO Paid", 0))'),
    "micro": ("Micro Purchase",
              'SUM(CASE WHEN p."Procurement Method"=\'Micro\' '
              'THEN p."Total Price" ELSE 0 END)'),
    "inadequate": ("SS - Inadequate Competition",
                   'SUM(CASE WHEN p."No." IN (SELECT "表单编号" FROM fct_prpo '
                   'WHERE "单一来源采购理由"=\'Inadequate Competition\') '
                   'THEN p."Total Price" ELSE 0 END)'),
}
# 坐标表一家公司一行（63 行 63 家），不再按城市合并；英文简称取 dim_company_map
_GEO_LOC = ('WITH loc AS ('
            'SELECT l."Company_cn" company, l.longitude lng, l.latitude lat, '
            'l."Color" color, l."Region" region, l."市" city, '
            'm."CompanyAbbreviationEN" abbr '
            'FROM fct_company_location l '
            'LEFT JOIN dim_company_map m ON m."CompanyAbbreviation"=l."Company_cn") ')


@board_bp.get("/geo")
def geo():
    """地图气泡 + Zebra BI 式 Category 同比：同一指标在 FY / 上一财年各聚合一次。"""
    c = con()
    try:
        fys = _fy_list(c)
        fy = request.args.get("fy") or (fys[-1] if fys else None)
        if not fy:
            return jsonify({"error": "dash_po 为空，请先运行 dash_build.py"}), 400
        pfy = _prev_fy(fy)
        metric = (request.args.get("metric") or "prpo").lower()
        if metric not in GEO_METRICS:
            return jsonify({"error": "unknown metric", "metrics": list(GEO_METRICS)}), 400
        label, expr = GEO_METRICS[metric]
        cur, rate = _rate(request.args)
        where, params = _scope(request.args, fy)
        base = ('FROM dash_po p JOIN dash_calendar c ON p."Date"=c."Date" '
                'WHERE ' + where)
        pwhere, pparams = (_scope(request.args, pfy) if pfy else (None, None))

        def sums(group, w, prm, with_loc=False):
            if not w:
                return {}, {}
            sel, rest = group
            if with_loc:
                sel += ', loc.lng, loc.lat, loc.color, loc.region, loc.city, loc.abbr'
            sql = ((_GEO_LOC if with_loc else '') +
                   'SELECT %s, %s v %s GROUP BY 1' % (sel, expr, rest % w))
            out, meta = {}, {}
            for r in c.execute(sql, prm):
                k = r["k"] or "Others"
                out[k] = r["v"] or 0
                if with_loc:
                    meta[k] = {"lng": r["lng"], "lat": r["lat"],
                               "color": r["color"], "region": r["region"],
                               "city": r["city"], "abbr": r["abbr"] or k}
            return out, meta

        cat_sql = 'FROM dash_po p JOIN dash_calendar c ON p."Date"=c."Date" WHERE %s'
        bubble_sql = ('FROM dash_po p JOIN dash_calendar c ON p."Date"=c."Date" '
                      'LEFT JOIN loc ON loc.company=p."Company_cn" WHERE %s')
        cats, _ = sums(('p."Category" k', cat_sql), where, params)
        pcats, _ = sums(('p."Category" k', cat_sql), pwhere, pparams)
        # 公司列表不能吃 company 自己的筛选：否则勾一家后列表只剩这一家，无法继续多选。
        # 其它全局筛选（区域 / 类别 / 支出类型…）照常生效。
        args_nc = MultiDict((k, v) for k, v in request.args.items(multi=True) if k != "company")
        where_nc, params_nc = _scope(args_nc, fy)
        pwhere_nc, pparams_nc = (_scope(args_nc, pfy) if pfy else (None, None))
        comps, cmeta = sums(('p."Company_cn" k', bubble_sql), where_nc, params_nc, True)
        pcomps, _ = sums(('p."Company_cn" k', bubble_sql), pwhere_nc, pparams_nc, True)
        # 表格自身点击类别时不能把其它行删掉（否则无法再点第二次取消）：
        # categoriesAll 忽略 category 筛选，前端用它画全表并对未选中行做淡化。
        args_nocat = MultiDict((k, v) for k, v in request.args.items(multi=True)
                               if k != "category")
        where_ac, params_ac = _scope(args_nocat, fy)
        pwhere_ac, pparams_ac = (_scope(args_nocat, pfy) if pfy else (None, None))
        cats_all, _ = sums(('p."Category" k', cat_sql), where_ac, params_ac)
        pcats_all, _ = sums(('p."Category" k', cat_sql), pwhere_ac, pparams_ac)

        def rows(a, b, key, meta=None):
            out = []
            for k in sorted(set(a) | set(b), key=lambda x: -(a.get(x, 0))):
                v, pv = a.get(k, 0), b.get(k, 0)
                r = {key: k, "v": _num(v), "pv": _num(pv), "yoy": _yoy(v, pv)}
                if meta:
                    r.update(meta.get(k) or {})
                out.append(r)
            return out

        categories = rows(cats, pcats, "cat")
        # 一家公司一个气泡：坐标直接取公司经纬度表，同城 15 家店就是 15 个气泡
        companies = rows(comps, pcomps, "company", cmeta)
        bubbles = [r for r in companies if r.get("lat") is not None and r.get("lng") is not None]
        total_v, total_pv = sum(cats.values()), sum(pcats.values())
        return jsonify({
            "metric": metric, "label": label, "fy": fy, "pyFy": pfy,
            "cur": cur, "rate": rate, "fys": fys,
            "total": {"v": _num(total_v), "pv": _num(total_pv),
                      "yoy": _yoy(total_v, total_pv)},
            "categories": categories, "companies": companies, "bubbles": bubbles,
            "categoriesAll": rows(cats_all, pcats_all, "cat"),
        })
    finally:
        c.close()
