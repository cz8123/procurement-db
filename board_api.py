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
import os
import sqlite3

from flask import Blueprint, Response, jsonify, request

from supplier_name import split_en, clean_en, english_variants

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "procurement.db")
board_bp = Blueprint("board", __name__, url_prefix="/api/board")

# 看板筛选维度（key → dash_po 列）
FILTERS = {
    "brand": "Brand",
    "category": "Category",
    "expense": "Expense Type",
    "level": "Supplier Category",
    "region": "Region_cn",
    "company": "Company_cn",
    "method": "Procurement Method",
    "supplier": "Supplier_cn",
}

CP_METHODS = ("SS-Exempt", "SS-Non Exempt", "Tender")
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
        parts.append("%s IN (%s)" % (_id(col), ",".join("?" * len(vals))))
        params.extend(vals)
    return parts, params


def _scope(args, fy, extra=None):
    """返回 (WHERE 片段, 参数)，已带财年"""
    parts, params = _filters(args)
    w = ['c."FiscalYearName" = ?'] + parts + list(extra or [])
    return " AND ".join(w), [fy] + params


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
    c = con()
    try:
        fys = _fy_list(c)
        out = {"fy": fys, "latestFy": fys[-1] if fys else None,
               "range": dict(zip(("from", "to"), c.execute(
                   'SELECT MIN("Date"),MAX("Date") FROM dash_po').fetchone())),
               "rows": c.execute('SELECT COUNT(*) FROM dash_po').fetchone()[0],
               "options": {}}
        for key, col in FILTERS.items():
            if key in ("company", "supplier"):     # 61 / 4982 个值，前端用搜索式明细，不进筛选下拉
                continue
            rows = c.execute(
                'SELECT %s AS v, COUNT(*) AS n FROM dash_po WHERE %s IS NOT NULL '
                'AND TRIM(%s)<>\'\' GROUP BY 1 ORDER BY n DESC'
                % (_id(col), _id(col), _id(col))).fetchall()
            out["options"][key] = [{"v": r["v"], "n": r["n"]} for r in rows]
        return jsonify(out)
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
            {"k": "txn", "label": "PR Count", "fmt": "int",
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
        # 区域按财年总额降序，图例与堆叠顺序稳定
        order = sorted(reg_sum, key=lambda k: -sum(reg_sum[k]))
        series = [{"k": k, "vals": reg_sum[k]} for k in order]

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
                 'COUNT(DISTINCT p."Supplier_cn") sup ' + base +
                 ' GROUP BY 1 ORDER BY v DESC')
        spec["pareto"] = psql2
        prow = [dict(r) for r in c.execute(psql2, params)]
        tot = sum((r["v"] or 0) for r in prow) or 0
        acc = 0.0
        for r in prow:
            acc += r["v"] or 0
            r["cum"] = round(acc / tot, 4) if tot else None
            r["share"] = round((r["v"] or 0) / tot, 4) if tot else None
        pareto = {"rows": prow, "total": round(tot, 2)}

        # ---------------- Chart 3 支出类型环形
        dsql = ('SELECT COALESCE(NULLIF(TRIM(p."Expense Type"),\'\'),\'Unclassified\') k, '
                'SUM(p."Total Price") v, COUNT(p."No.") n ' + base +
                ' GROUP BY 1 ORDER BY v DESC')
        spec["donut"] = dsql
        donut = [dict(r) for r in c.execute(dsql, params)]
        dtot = sum((r["v"] or 0) for r in donut) or 0
        for r in donut:
            r["share"] = round((r["v"] or 0) / dtot, 4) if dtot else None

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
            "donut": donut, "top": top, "heat": heat, "palette": PALETTE,
            "spec": spec,
            # 当前生效的取数参数（供「计算逻辑」说明展示）
            "params": {
                "fy": fy, "prevFy": pfy, "cur": cur, "rate": rate,
                "where": where, "values": [str(x) for x in params],
                "filters": {k: request.args.getlist(k) for k in
                            ("brand", "category", "expense", "supplier_attr", "region")
                            if request.args.getlist(k)},
                "cpMethods": list(CP_METHODS),
            },
        })
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
                'COUNT(DISTINCT p."Category") cats '
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
}

_ROWS_SELECT = (
    'p."No." no, p."Date" date, p."Company_cn" company, p."Region_cn" region, '
    'p."Brand" brand, p."Department" dept, p."Category" category, '
    'p."Expense Type" expense, p."Procurement Method" method, '
    'p."Supplier_cn" supplier, p."Total Price" amount, p."Time Taken" days, '
    'p."Budget Type" budget, p."Supplier Category" level'
)


def _rows_where(args, fy):
    where, params = _scope(args, fy)
    # 财月（1=7 月）：月度图点柱子时下钻用
    fm = (args.get("fm") or "").strip()
    if fm.isdigit() and 1 <= int(fm) <= 12:
        where += ' AND c."FiscalMonthNumber" = ?'
        params = list(params) + [int(fm)]
    q = (args.get("q") or "").strip()
    if q:
        like = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
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
        sort = _SORTS.get(request.args.get("sort", "date"), 'p."Date"')
        dirn = "ASC" if request.args.get("dir") == "asc" else "DESC"
        where, params = _rows_where(request.args, fy)
        base = 'FROM dash_po p JOIN dash_calendar c ON p."Date"=c."Date" WHERE ' + where
        total = c.execute("SELECT COUNT(*) " + base, params).fetchone()[0]
        amt = c.execute('SELECT SUM(p."Total Price") ' + base, params).fetchone()[0]
        sql = ('SELECT ' + _ROWS_SELECT + ' ' + base +
               ' ORDER BY %s %s, p."No." ASC LIMIT ? OFFSET ?' % (sort, dirn))
        data = [dict(r) for r in c.execute(sql, params + [size, (page - 1) * size])]
        return jsonify({"total": total, "page": page, "size": size,
                        "amount": _num(amt), "rows": data})
    finally:
        c.close()


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
                            "Amount (%s)" % cur, "Days", "Budget Type", "Supplier Category"])
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


def _contract_where(args):
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
        where, params = _contract_where(request.args)
        page = max(int(request.args.get("page", 1) or 1), 1)
        size = min(max(int(request.args.get("size", 50) or 50), 10), 200)
        sort = CONTRACT_SORTS.get(request.args.get("sort", "start"), '"开始日期"')
        dirn = "ASC" if request.args.get("dir") == "asc" else "DESC"

        summary = dict(c.execute(
            'SELECT COUNT(*) n, SUM("预估金额_可加") cv, SUM("实际采购额") pa, '
            'SUM(CASE WHEN "剩余天数" < 0 THEN 1 ELSE 0 END) expired, '
            'SUM(CASE WHEN "剩余天数" BETWEEN 0 AND 90 THEN 1 ELSE 0 END) expiring, '
            'SUM(CASE WHEN "执行率" > 1 THEN 1 ELSE 0 END) overrun '
            'FROM vw_fa_analysis WHERE ' + where, params).fetchone())
        cv, pa = summary.get("cv") or 0, summary.get("pa") or 0
        summary["cv"] = round(cv, 2)
        summary["pa"] = round(pa, 2)
        summary["usage"] = round(pa / cv, 4) if cv else None
        for k in ("n", "expired", "expiring", "overrun"):
            summary[k] = int(summary.get(k) or 0)

        rows_sql = ('SELECT ' + CONTRACT_COLS + ' FROM vw_fa_analysis WHERE ' + where +
                    ' ORDER BY %s %s LIMIT ? OFFSET ?' % (sort, dirn))
        rows = [dict(r) for r in c.execute(rows_sql, params + [size, (page - 1) * size])]
        smap = supplier_en_map(c)
        variants = english_variants(c)
        for r in rows:
            # 前端显示英文名（已补空格）；hover 用原 supplier（全称）
            r["supplier_en"] = clean_en(
                smap.get(r.get("supplier")) or split_en(r.get("supplier")), variants)
        types = [dict(r) for r in c.execute(
            'SELECT "适用领域" k, COUNT(*) n FROM vw_fa_analysis GROUP BY 1 ORDER BY n DESC')]
        bounds = dict(c.execute(
            'SELECT MIN("开始日期") mn, MAX("开始日期") mx FROM vw_fa_analysis').fetchone())
        return jsonify({"total": summary["n"], "page": page, "size": size,
                        "summary": summary, "types": types, "bounds": bounds,
                        "rows": rows,
                        "spec": {
                            "summary": ('SELECT COUNT(*) n, SUM("预估金额_可加") cv, '
                                        'SUM("实际采购额") pa, ... '
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
    where, params = _contract_where(request.args)
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
