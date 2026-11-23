#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生成采购分析看板的数据底座（数据源：procurement.db 的 fct_prpo 等表）

产出三张表：
  dim_category_map  —— 采购类别原值(94) → PBI 口径的 11 类
  dash_calendar     —— 日期表（自然年 + 财年，财年 7/1 起，与 PBIP 日期表一致）
  dash_po           —— 分析宽表（34 列，列名与 PBIP 的 po 表一致）

用法：
  python dash_build.py            # 建表 + 灌数
  python dash_build.py --check    # 只做校验，不重建
  python dash_build.py --with-map # 额外用 采购分析报表.xlsx 校正类别映射（可选）

非破坏性：只创建/覆盖 dash_ 前缀的三张表，不动 fct_* / dim_* 任何数据。
"""
import argparse
import os
import re
import sqlite3
import sys
from datetime import date, timedelta

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "procurement.db")

CATEGORIES = [
    "Accessory", "Administration", "Aftersales", "DT", "Engineering", "Marketing",
    "Operational Gift", "Others", "Sales", "Used Car", "Vehicles/AAP (Principal)",
]

# 关键词兜底规则（按顺序匹配，先命中先用）；键为去空格小写后的原值
RULES = [
    (("usedcar", "二手车"), "Used Car"),
    (("operationalgift", "运营礼品"), "Operational Gift"),
    (("vehicle", "aap", "整车", "原厂"), "Vehicles/AAP (Principal)"),
    (("accessor", "附件", "精品"), "Accessory"),
    (("aftersales", "aftermarket", "售后"), "Aftersales"),
    (("dt", "computer", "communication", "计算机", "通讯", "数字化"), "DT"),
    (("engineering", "工程"), "Engineering"),
    (("administration", "administrative", "行政"), "Administration"),
    (("market", "市场"), "Marketing"),
    (("sales", "销售"), "Sales"),
    (("professional", "consulting", "专业"), "Others"),
]


def norm(s):
    return re.sub(r"[\s\-_（）()、/]+", "", (s or "")).lower()


def to_amount(v):
    """金额列兼容两种存储形态：老行是 '¥30,000.00' 文本，新导入行是数值。

    直接 re.sub 到 float 会在数值行上抛 TypeError（2026-11-23 导入后实测）。
    """
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(re.sub(r"[¥￥,\s]", "", str(v)))
    except ValueError:
        return None


def rule_category(raw):
    n = norm(raw)
    for keys, cat in RULES:
        if any(k in n for k in keys):
            return cat
    return None


def has_cjk(s):
    return any("\u4e00" <= ch <= "\u9fff" for ch in s or "")


def split_supplier(s):
    """'English name-中文名' → (中文名, 英文名)。取最后一个分隔符且其后含中文。"""
    if not s or not s.strip():
        return None, None
    s = s.strip()
    for i in range(len(s) - 1, -1, -1):
        if s[i] in "-_":
            tail = s[i + 1:].strip()
            if tail and has_cjk(tail):
                head = s[:i].strip()
                return tail, (head or None)
    return (s, None) if has_cjk(s) else (None, s)


def money(expr):
    """'¥30,000.00' → 30000.0；空/坏值 → NULL"""
    return ("CASE WHEN {e} IS NULL OR TRIM({e})='' THEN NULL ELSE "
            "CAST(REPLACE(REPLACE(REPLACE(REPLACE(TRIM({e}),'¥',''),'￥',''),',',''),' ','') AS REAL) END").format(e=expr)


def ident(n):
    return '"' + n.replace('"', '""') + '"'


# ---------------------------------------------------------------- calendar
def build_calendar(con):
    con.execute("DROP TABLE IF EXISTS dash_calendar")
    con.execute("""CREATE TABLE dash_calendar(
        "Date" TEXT PRIMARY KEY, "Year" INT, "Quarter" INT, "QuarterName" TEXT,
        "MonthNumber" INT, "MonthName" TEXT, "MonthNameShort" TEXT,
        "年月" TEXT, "年季度" TEXT, "YearMonthSort" INT,
        "FiscalYear" INT, "FiscalYearName" TEXT, "FiscalQuarter" INT,
        "FiscalQuarterName" TEXT, "FiscalMonthNumber" INT, "FiscalYearSort" INT,
        "FiscalYearMonthSort" INT)""")
    months_en = ["January", "February", "March", "April", "May", "June", "July",
                 "August", "September", "October", "November", "December"]
    rows = []
    d = date(2023, 1, 1)
    end = date(2028, 12, 31)
    while d <= end:
        m = d.month
        fy = d.year if m >= 7 else d.year - 1
        fq = 1 if 7 <= m <= 9 else 2 if 10 <= m <= 12 else 3 if 1 <= m <= 3 else 4
        fmn = (m - 7) % 12 + 1
        rows.append((
            d.isoformat(), d.year, (m - 1) // 3 + 1, "Q%d" % ((m - 1) // 3 + 1),
            m, months_en[m - 1], months_en[m - 1][:3],
            "%d年%d月" % (d.year, m), "%d年Q%d" % (d.year, (m - 1) // 3 + 1),
            d.year * 100 + m,
            fy, "FY%02d%02d" % (fy % 100, (fy + 1) % 100), fq,
            "FY%02d%02d Q%d" % (fy % 100, (fy + 1) % 100, fq), fmn,
            fy * 100 + fq, fy * 100 + fmn,
        ))
        d += timedelta(days=1)
    con.executemany("INSERT INTO dash_calendar VALUES (%s)" % ",".join("?" * 17), rows)
    con.commit()
    return len(rows)


# ---------------------------------------------------------------- category map
def build_category_map(con, xlsx=None):
    """1) 用 xlsx 的 (Sub-Category→Category) 关系对库里的原值投票；2) 剩余用关键词规则。"""
    cats = [r[0] for r in con.execute(
        'SELECT DISTINCT "采购类别" FROM fct_prpo WHERE "采购类别" IS NOT NULL ORDER BY 1')]
    votes = {}
    if xlsx and os.path.exists(xlsx):
        try:
            import openpyxl
            wb = openpyxl.load_workbook(xlsx, read_only=True, data_only=True)
            ws = wb["报表1"] if "报表1" in wb.sheetnames else wb[wb.sheetnames[0]]
            it = ws.iter_rows(values_only=True)
            hdr = list(next(it))
            i_sub, i_cat = hdr.index("Sub-Category"), hdr.index("Category")
            pair = {}
            for r in it:
                s, c = r[i_sub], r[i_cat]
                if s and c:
                    pair[(s, c)] = pair.get((s, c), 0) + 1
            sub2cat = {}
            best = {}
            for (s, c), n in pair.items():
                if n > best.get(s, (0, None))[0]:
                    best[s] = (n, c)
            sub2cat = {s: v[1] for s, v in best.items()}
            # 库里 (原值, 子类别) 共现 → 投票
            for raw, sub, n in con.execute(
                    'SELECT "采购类别","Table - 采购子类别",COUNT(*) FROM fct_prpo GROUP BY 1,2'):
                if sub in sub2cat:
                    votes.setdefault(raw, {})
                    votes[raw][sub2cat[sub]] = votes[raw].get(sub2cat[sub], 0) + n
            wb.close()
        except Exception as e:  # noqa: BLE001
            print("  ! xlsx 投票跳过：%s" % e)

    con.execute("DROP TABLE IF EXISTS dim_category_map")
    con.execute('CREATE TABLE dim_category_map("原始值" TEXT PRIMARY KEY, "采购类别" TEXT, "来源" TEXT)')
    rows, by_rule, unresolved = [], 0, []
    for raw in cats:
        v = votes.get(raw)
        if v:
            cat, src = max(v.items(), key=lambda kv: kv[1])[0], "vote"
        else:
            cat, src = rule_category(raw), "rule"
            by_rule += 1 if cat else 0
        if not cat:
            cat, src = "Others", "fallback"
            unresolved.append(raw)
        rows.append((raw, cat, src))
    con.executemany('INSERT INTO dim_category_map VALUES (?,?,?)', rows)
    con.commit()
    return len(rows), by_rule, unresolved


# ---------------------------------------------------------------- dash_po
COLS = ["No.", "Date", "Company", "Company_cn", "Company_en", "Region_cn", "Region_en",
        "City_cn", "City_en", "Brand", "Department", "Expense Type", "FY", "Budget Type",
        "DT-related", "Procurement Method", "Note", "Contract Type", "Contract Name",
        "Contract No.", "Contract Value", "Contract Start Date", "Contract Expiry Date",
        "Category", "Sub-Category", "Category Description", "Exception Reason",
        "Total Price", "Time Taken", "Supplier_cn", "Supplier_en", "AVL No.",
        "Supplier Category", "Category Detail", "PO_flag", "PO Paid"]


def build_po(con):
    exp = {r[0]: r[1] for r in con.execute('SELECT 原始值,支出类型 FROM dim_agg_支出类型')}
    pm = {r[0]: r[1] for r in con.execute('SELECT 原始值,选择采购方法 FROM dim_agg_选择采购方法')}
    cmap = {r[0]: r[1] for r in con.execute('SELECT 原始值,采购类别 FROM dim_category_map')}
    comp = {r[0]: r[1:] for r in con.execute(
        'SELECT Company,CompanyAbbreviation,CompanyAbbreviationEN,RegionAbbreviationCN,'
        'RegionAbbreviationEN,CityCN,CityEN,Brand FROM dim_company_map')}
    cal = {r[0]: r[1] for r in con.execute('SELECT "Date",FiscalYearName FROM dash_calendar')}
    # PO_flag：已关联采购订单的 PRPO 单号（fct_po.已批准的采购申请参考 → fct_prpo.表单编号）
    po_nos = {r[0] for r in con.execute(
        'SELECT DISTINCT TRIM("已批准的采购申请参考") FROM fct_po '
        'WHERE COALESCE("已批准的采购申请参考",\'\')<>\'\'')}
    # PO 实付金额（总支付总额）按 PRPO 单号汇总：Cancelled 的 PO 不计，
    # 金额是带 ¥ 的文本，需清洗；同一 PRPO 可对应多张 PO，求和。
    po_paid = {}
    for _ref, _amt in con.execute(
            'SELECT TRIM("已批准的采购申请参考"), "总支付总额（人民币）" FROM fct_po '
            'WHERE COALESCE("已批准的采购申请参考",\'\')<>\'\' '
            'AND COALESCE("采购状态",\'\')<>\'Cancelled\''):
        if not _ref:
            continue
        _v = to_amount(_amt)
        if _v is None:
            continue
        po_paid[_ref] = po_paid.get(_ref, 0.0) + _v

    pcols = [r[1] for r in con.execute("PRAGMA table_info(fct_prpo)")]
    dt_col = next(c for c in pcols if "数字技术" in c)
    avl_col = next((c for c in pcols if "AVL 属性" in c), None)
    budget_col = next(c for c in pcols if "已编入预算" in c)

    # 「支出类型」必须取自 fct_prpo 原始列，再经 dim_agg_支出类型 映射成 OPEX/TRADE/A&P/CAPEX。
    # 之前这里漏了该列，又把 dept 传给 exp.get()，导致 dash_po."Expense Type" 全为 NULL。
    src = con.execute(
        'SELECT 表单编号,提交日期,公司,部门,"支出类型",{budget},{dt},选择采购方法,说明,框架合同类型,'
        '框架合同名称,框架合同编号,框架合同到期日,采购类别,"Table - 采购子类别",'
        '"Table - 采购类型说明",豁免单一来源的理由,含增值税总价,审批时间,供应商名称,'
        '"供应商 AVL 编号",供应商属性,{avl} FROM fct_prpo'.format(
            budget=ident(budget_col), dt=ident(dt_col),
            avl=ident(avl_col) if avl_col else "NULL")).fetchall()

    con.execute("DROP TABLE IF EXISTS dash_po")
    con.execute("CREATE TABLE dash_po(%s)" % ",".join(ident(c) for c in COLS))
    rows = []
    for (no, sub, comp_raw, dept, exp_raw, budget, dtrel, method, note, ctype, cname, cno, cexp,
         cat_raw, subcat, catdesc, exc, price, appr, sup, avlno, supattr, avldetail) in src:
        d = (sub or "")[:10]
        c = comp.get(comp_raw) or (None,) * 7
        sup_cn, sup_en = split_supplier(sup)
        amt = to_amount(price)
        tt = None
        if appr and sub:
            try:
                from datetime import datetime
                a = datetime.fromisoformat(appr)
                s = datetime.fromisoformat(sub)
                tt = round(max(0.0, (a - s).total_seconds() / 86400.0), 2)
            except ValueError:
                tt = None
        first = (supattr or "").strip()[:1].upper()
        supcat = {"A": "A-Fully Exempt", "B": "B-Partially Exempt",
                  "C": "C-Not Exempt"}.get(first, "Unknown")
        rows.append((
            no, d or None, comp_raw, c[0], c[1], c[2], c[3], c[4], c[5], c[6],
            dept, exp.get(__strip_ws(exp, exp_raw)), cal.get(d), budget,
            dtrel, pm.get(__strip_ws(pm, method)), note, ctype, cname, cno,
            None, None, cexp,
            cmap.get(cat_raw, rule_category(cat_raw) or "Others"), subcat, catdesc,
            exc, amt, tt, sup_cn, sup_en, avlno, supcat, avldetail,
            1 if (no and no.strip() in po_nos) else 0,
            round(po_paid.get((no or "").strip(), 0.0), 2),
        ))

    con.executemany("INSERT INTO dash_po VALUES (%s)" % ",".join("?" * len(COLS)), rows)
    con.execute('CREATE INDEX IF NOT EXISTS ix_dash_po_date ON dash_po("Date")')
    con.execute('CREATE INDEX IF NOT EXISTS ix_dash_po_brand ON dash_po("Brand")')
    con.execute('CREATE INDEX IF NOT EXISTS ix_dash_po_cat ON dash_po("Category")')
    con.execute('CREATE INDEX IF NOT EXISTS ix_dash_po_sup ON dash_po("Supplier_cn")')
    con.commit()
    return len(rows)


def __strip_ws(mapping, v):
    """映射表键可能有多余空格，做一次宽松匹配"""
    if v is None:
        return None
    if v in mapping:
        return v
    for k in mapping:
        if k and k.strip() == v.strip():
            return k
    return v


# ---------------------------------------------------------------- check
def check(con):
    q = lambda s: con.execute(s).fetchone()[0]  # noqa: E731
    n = q('SELECT COUNT(*) FROM dash_po')
    amt = q('SELECT ROUND(SUM("Total Price"),2) FROM dash_po')
    sup = q('SELECT COUNT(DISTINCT "Supplier_cn") FROM dash_po')
    cat = q('SELECT COUNT(DISTINCT "Category") FROM dash_po')
    sub = q('SELECT COUNT(DISTINCT "Sub-Category") FROM dash_po')
    brd = q('SELECT COUNT(DISTINCT "Brand") FROM dash_po')
    sto = q('SELECT COUNT(DISTINCT "Company_cn") FROM dash_po')
    d0, d1 = con.execute('SELECT MIN("Date"),MAX("Date") FROM dash_po').fetchone()
    fy = con.execute('SELECT "FY",COUNT(*) FROM dash_po GROUP BY 1 ORDER BY 1 DESC LIMIT 4').fetchall()
    print("dash_po  %s 行  ¥%s  供应商 %s  类别 %s  子类别 %s  品牌 %s  门店 %s" %
          (n, format(amt or 0, ",.2f"), sup, cat, sub, brd, sto))
    print("  期间 %s ~ %s；财年 %s" % (d0, d1, fy))
    print("  类别分布 %s" % con.execute(
        'SELECT "Category",ROUND(SUM("Total Price")/1e6,1) FROM dash_po GROUP BY 1 ORDER BY 2 DESC').fetchall())
    print("  Unknown 供应商属性 %.1f%%" % (100.0 * q(
        "SELECT COUNT(*) FROM dash_po WHERE \"Supplier Category\"='Unknown'") / max(n, 1)))
    print("  空金额 %s 行；时间中位数 %s" % (
        q('SELECT COUNT(*) FROM dash_po WHERE "Total Price" IS NULL'),
        _median(con)))


def _median(con):
    v = [r[0] for r in con.execute('SELECT "Time Taken" FROM dash_po WHERE "Time Taken" IS NOT NULL ORDER BY 1')]
    if not v:
        return None
    m = len(v) // 2
    return round(v[m], 2) if len(v) % 2 else round((v[m - 1] + v[m]) / 2, 2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--with-map", metavar="XLSX", help="用采购分析报表.xlsx 校正类别映射")
    a = ap.parse_args()
    con = sqlite3.connect(DB)
    if not a.check:
        print("· dash_calendar  %s 行" % build_calendar(con))
        n, r, un = build_category_map(con, a.with_map)
        print("· dim_category_map %s 条（规则补 %s，未定 %s）" % (n, r, len(un)))
        if un:
            print("  未定：%s" % un)
        print("· dash_po     %s 行" % build_po(con))
    check(con)
    con.close()


if __name__ == "__main__":
    sys.exit(main())
