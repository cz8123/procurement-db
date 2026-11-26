#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 /dash 单页看板导出成一个**可离线分享的单文件 HTML**（数据全部内嵌）。

思路（沿用 v1 的做法，见 `_archive/dash_v1/dash_export.py`）：
  1. 从库里把行级数据、框架协议全表压成紧凑 JSON（字符串列做字典编码）；
  2. 把 board.css / board.js / logo 全部内联；
  3. 注入 `static/board/snapshot.js` 作为 fetch 垫片 —— 它在浏览器里
     复算 /api/board/*（KPI、5 张图、明细抽屉、框架协议表、两个 CSV 导出），
     所以打开文件后 **财年切换、筛选、下钻、导出全部照旧可用**，不连服务器。

用法：
    python board_export.py                     # 输出到 exports/
    python board_export.py --out D:/桌面/采购看板.html
"""
from __future__ import annotations

import argparse
import base64
import datetime
import json
import os
import re
import sqlite3
import sys

from app import app
from board_api import PALETTE, REGION_EN, supplier_en_map
from supplier_name import split_en, clean_en, english_variants

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(APP_DIR, "procurement.db")
STATIC = os.path.join(APP_DIR, "static", "board")
OUT_DIR = os.path.join(APP_DIR, "exports")

# 行级数据列（顺序即 JSON 数组里的顺序）
ROW_COLS = [
    "no", "date", "fy", "company", "region", "city", "brand", "dept", "category", "sub",
    "expense", "method", "note", "supplier", "amount", "days", "budget", "level",
    "poPaid", "month", "exc", "ssr",
    # 明细表「Single Source Reason」列：豁免理由 / 单一来源理由合并（两者互斥），
    # 已按 dim_agg 映射成聚合名，离线表格与在线逐字一致
    "reason",
]
# 这几列是数值，其余做字典编码
NUM_COLS = {"amount", "days", "poPaid", "month"}
# 说明是长文本、几乎条条不同（字典编码会因唯一值过多被跳过）。离线给全量说明，
# 悬停浮窗才和在线一致（库里最长 2,367 字）；代价是文件从 9.1MB 到 ~12.2MB，
# 3000 只是防呆上限，正常碰不到
NOTE_LIMIT = 3000

ROW_SQL = """
SELECT dash_po."No.",dash_po."Date",dash_po."FY",dash_po."Company_cn",dash_po."Region_cn",
       dash_po."City_cn",
       dash_po."Brand",dash_po."Department",dash_po."Category",dash_po."Sub-Category",
       dash_po."Expense Type",dash_po."Procurement Method",dash_po."Note",
       dash_po."Supplier_cn",dash_po."Total Price",dash_po."Time Taken",
       dash_po."Budget Type",dash_po."Supplier Category",dash_po."PO Paid",
       dash_po."Exception Reason",
       (SELECT "单一来源采购理由" FROM fct_prpo f WHERE f."表单编号"=dash_po."No."),
       COALESCE(mx."豁免单一来源的理由", f."豁免单一来源的理由",
                ms."单一来源采购理由", f."单一来源采购理由")
FROM dash_po
LEFT JOIN fct_prpo f ON f."表单编号" = dash_po."No."
LEFT JOIN dim_agg_豁免单一来源的理由 mx ON mx."原始值" = f."豁免单一来源的理由"
LEFT JOIN dim_agg_单一来源采购理由 ms ON ms."原始值" = f."单一来源采购理由"
"""

CONTRACT_SQL = """
SELECT "合同名称CN","供应商名称","预估金额","预估金额_可加","实际采购额","执行率",
       "开始日期","结束日期","剩余天数","适用领域"
FROM vw_fa_analysis
"""


def month_map(con):
    """日期 → 财月序号（1 = 7 月）"""
    return {d: m for d, m in con.execute(
        'SELECT "Date","FiscalMonthNumber" FROM dash_calendar')}


def build_rows(con):
    cal = month_map(con)
    # 子类别走聚合映射（与 board_api 同口径）：244 个原始值 → 144 个聚合名
    submap = {r[0]: r[1] for r in con.execute(
        'SELECT "原始值","采购子类别" FROM dim_agg_采购子类别')}
    data, dicts = [], {}
    cols = ROW_COLS
    for row in con.execute(ROW_SQL):
        (no, date, fy, company, region, city, brand, dept, category, sub,
         expense, method, note, supplier, amount, days, budget, level, po_paid,
         exc, ssr, reason) = row
        note = (note or "").strip()
        if sub:
            sub = submap.get(sub, sub)
        vals = [no, date, fy, company, region, city, brand, dept, category, sub,
                expense, method, note[:NOTE_LIMIT] or None,
                supplier, amount, days, budget, level, po_paid,
                cal.get(date), exc, ssr, reason]
        data.append(vals)

    # 字典编码：字符串列去重后存索引，体积约为裸 JSON 的 1/4
    for ci, col in enumerate(cols):
        if col in NUM_COLS:
            continue
        uniq = sorted({v[ci] for v in data if v[ci] is not None})
        if len(uniq) > len(data) * 0.5:      # 唯一值太多（如单号），不做字典
            continue
        pos = {v: i for i, v in enumerate(uniq)}
        dicts[col] = uniq
        for v in data:
            v[ci] = -1 if v[ci] is None else pos[v[ci]]

    return {"cols": cols, "dict": dicts, "data": data}


def build_contracts(con):
    """框架协议全表；末尾补一列「供应商英文名」供前端显示（hover 仍用全称）。

    与 /api/board/contracts 用同一份映射，保证在线/离线一致。
    """
    smap = supplier_en_map(con)
    variants = english_variants(con)
    out = []
    for r in con.execute(CONTRACT_SQL):
        v = list(r)
        v.append(clean_en(smap.get(v[1]) or split_en(v[1]), variants))
        out.append(v)
    return out


def build_en_fix(con):
    """离线快照用：{英文原名: 补好空格的名字}，只收「确实要改」的那些。

    在线时后端直接给 `en`；离线时 snapshot.js 没有后端，board.js 的 supEn()
    用这份表把空格补回来，保证两边显示一字不差。
    """
    variants = english_variants(con)
    fix = {}
    for (name,) in con.execute(
            'SELECT DISTINCT "Supplier_cn" FROM dash_po WHERE "Supplier_cn" IS NOT NULL'):
        en = split_en(name)
        if not en:
            continue
        fixed = clean_en(en, variants)
        if fixed != en:
            fix[en] = fixed
    return fix


def build_reason_map(con):
    """单一来源两层的「原始值 → 聚合值」映射（供离线复算下钻饼图）。"""
    return {
        "exempt": {r[0]: r[1] for r in con.execute(
            'SELECT "原始值","豁免单一来源的理由" FROM dim_agg_豁免单一来源的理由')},
        "single": {r[0]: r[1] for r in con.execute(
            'SELECT "原始值","单一来源采购理由" FROM dim_agg_单一来源采购理由')},
    }


def build_geo_loc(con):
    """离线地图气泡需要的公司 → 坐标 / 区域色 / 城市映射。"""
    out = {}
    try:
        for r in con.execute(
                'SELECT l."Company_cn", l.longitude, l.latitude, l."Color", l."Region", '
                'l."市", m."CompanyAbbreviationEN" FROM fct_company_location l '
                'LEFT JOIN dim_company_map m ON m."CompanyAbbreviation"=l."Company_cn"'):
            out[r[0]] = {"lng": r[1], "lat": r[2], "color": r[3],
                         "region": r[4], "city": r[5], "abbr": r[6] or r[0]}
    except sqlite3.Error:
        pass
    return out


def build_snapshot():
    con = sqlite3.connect(DB)
    try:
        rows = build_rows(con)
        contracts = build_contracts(con)
        en_fix = build_en_fix(con)
        reason_map = build_reason_map(con)
        geo_loc = build_geo_loc(con)
    finally:
        con.close()

    client = app.test_client()
    r = client.get("/api/board/options")
    if r.status_code != 200:
        raise SystemExit("/api/board/options 返回 %s" % r.status_code)
    options = r.get_json()

    return {
        "ver": 1,
        "today": datetime.date.today().isoformat(),
        "palette": PALETTE,
        "cpMethods": ["SS-Exempt", "SS-Non Exempt", "Tender"],
        "options": options,
        "rows": rows,
        "contracts": contracts,
        "enFix": en_fix,
        "reasonMap": reason_map,
        "regionEn": REGION_EN,
        "geoLoc": geo_loc,
    }


def data_uri(path, mime):
    with open(path, "rb") as f:
        return "data:%s;base64,%s" % (mime, base64.b64encode(f.read()).decode())


SNAP_CSS = """
/* ============ 静态快照附加样式 ============ */
.snap-badge{
  position:fixed; right:14px; bottom:14px; z-index:200;
  background:rgba(24,55,92,.92); color:#DCE8F4; border-radius:20px;
  padding:6px 13px; font:600 11.5px/1.4 var(--font);
  box-shadow:0 6px 18px rgba(24,55,92,.28); pointer-events:none;
}
.snap-badge b{ color:#fff; font-weight:700; }
.snap-badge span{ color:#9DB7D2; margin-left:6px; }
"""


def build(out_path=None):
    snap = build_snapshot()
    n_rows = len(snap["rows"]["data"])
    n_c = len(snap["contracts"])
    print("rows %s / agreements %s / fiscal years %s" % (
        format(n_rows, ","), n_c, ", ".join(snap["options"].get("fy") or [])))

    client = app.test_client()
    html = client.get("/dash").get_data(as_text=True)
    # 离线单文件是 file:// 打开的：腾讯地图 JS SDK 明确不支持该协议，
    # 内嵌 Key 只会让控制台刷错误、并且把 Key 泄漏进可转发的文件。统一清空，
    # 地图区届时显示配置提示，公司列表与同比表照常可用。
    html = re.sub(r'window\.TMAP_KEY\s*=\s*"[^"]*"', 'window.TMAP_KEY = ""', html)

    # 1) 样式内联
    css = open(os.path.join(STATIC, "board.css"), encoding="utf-8").read() + SNAP_CSS
    html, n_css = re.subn(
        r'<link rel="stylesheet" href="[^"]*board\.css[^"]*">',
        lambda m: "<style>\n" + css + "\n</style>",
        html,
    )

    # 2) logo 内联
    logo = data_uri(os.path.join(STATIC, "logo-sime.png"), "image/png")
    html, n_logo = re.subn(
        r'(<img class="hd-logo" src=")[^"]*(")',
        lambda m: m.group(1) + logo + m.group(2),
        html,
    )

    # 3) 数据 + 垫片 + 脚本
    board_js = open(os.path.join(STATIC, "board.js"), encoding="utf-8").read()
    snap_js = open(os.path.join(STATIC, "snapshot.js"), encoding="utf-8").read()
    blob = json.dumps(snap, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    inline = (
        "<script>\nwindow.__SNAP__=" + blob + ";\n</script>\n"
        "<script>\n" + snap_js + "\n</script>\n"
        "<script>\n" + board_js + "\n</script>"
    )
    html, n_js = re.subn(
        r'<script src="[^"]*board/board\.js[^"]*"></script>',
        lambda m: inline,
        html,
    )

    # 4) 标题 / 角标
    today = datetime.date.today().strftime("%Y-%m-%d")
    html = html.replace(
        "<title>Procurement Data Dashboard</title>",
        "<title>Procurement Data Dashboard · Snapshot %s</title>" % today,
    )
    html = html.replace(
        "</body>",
        '<div class="snap-badge"><b>Static snapshot</b>data embedded'
        '<span>exported %s</span></div>\n</body>' % today,
    )

    if not (n_css and n_js and n_logo):
        raise SystemExit("内联替换失败（css=%s, js=%s, logo=%s），模板结构可能变了"
                         % (n_css, n_js, n_logo))

    if not out_path:
        os.makedirs(OUT_DIR, exist_ok=True)
        out_path = os.path.join(
            OUT_DIR, "采购看板_单文件_%s.html" % today.replace("-", ""))
    else:
        os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)

    size = os.path.getsize(out_path) / 1048576
    print("输出：%s  %.2f MB" % (out_path, size))
    return out_path


def main() -> int:
    ap = argparse.ArgumentParser(description="导出 /dash 为单文件 HTML（数据全内嵌）")
    ap.add_argument("--out", help="输出文件路径")
    a = ap.parse_args()
    build(a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
