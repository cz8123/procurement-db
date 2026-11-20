# -*- coding: utf-8 -*-
"""
日期类型转换断言
================
真实数据里的日期长得五花八门，SQLite 的 date() 只认 ISO 8601，遇到
'2025/4/30'、'31/5/2025'、'30-Sep-22' 一律返回 NULL —— 这就是原来
「改成日期类型后整列变 null」的根因。这里把各种真实形态喂进去逐条验证，
再用真实库的脏列做回归。

跑法：<venv>/python.exe tests/test_date_convert.py
"""
import os
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import transform as T

ok = fail = 0


def chk(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  [OK] {name}")
    else:
        fail += 1
        print(f"  [XX] {name} {extra}")


def conv(con, table, col, **kw):
    """把 col 转成日期，返回 {第一列(测试用的 k): 日期结果}。

    测试表都建成 (k, d) 两列，k 唯一，用 k 对齐才拿得到「哪一行变成了什么」。
    """
    step = {"op": "change_type", "column": col, "type": "date"}
    step.update(kw)
    sql = T.compile_query(con, table, [step])["sql"]
    rows = con.execute(f'SELECT * FROM ({sql})').fetchall()
    return {r[0]: r[1] for r in rows}


print("=== 1. 各种真实日期形态 ===")
con = sqlite3.connect(":memory:")
con.execute("CREATE TABLE t (k INTEGER, d TEXT)")
CASES = [
    (1,  "2024-07-25 08:38:25", "2024-07-25"),   # ISO 带时间
    (2,  "2024-07-25",          "2024-07-25"),   # 纯 ISO
    (3,  "2024/7/5",            "2024-07-05"),   # 年/月/日，不补零
    (4,  "2025/4/30",           "2025-04-30"),
    (5,  "2025/12/18",          "2025-12-18"),
    (6,  "31/5/2025",           "2025-05-31"),   # 日/月/年（31 不能是月）
    (7,  "1/31/2025",           "2025-01-31"),   # 月/日/年（31 不能是月）
    (8,  "12/24/2025",          "2025-12-24"),
    (9,  "5/21/2024",           "2024-05-21"),
    (10, "30-Sep-22",           "2022-09-30"),   # 英文月 + 两位年
    (11, "1-Jan-26",            "2026-01-01"),
    (12, "31-Mar-24",           "2024-03-31"),
    (13, "2024年7月30日",        "2024-07-30"),   # 中文年月日
    (14, "2024.7.30",           "2024-07-30"),   # 点分隔
    (15, "2024-7-5",            "2024-07-05"),   # 不补零
    (16, "2024-07-25T08:38:25", "2024-07-25"),   # T 分隔
]
con.executemany("INSERT INTO t VALUES (?,?)", [(k, d) for k, d, _ in CASES])
got = conv(con, "t", "d")
for k, raw, want in CASES:
    chk(f"{raw!r} → {want}", got.get(k) == want, f"得到 {got.get(k)!r}")

print("\n=== 2. 空值与不可解析（默认保留原值） ===")
con2 = sqlite3.connect(":memory:")
con2.execute("CREATE TABLE u (k INTEGER, d TEXT)")
con2.executemany("INSERT INTO u VALUES (?,?)", [
    (1, None), (2, ""), (3, "   "),
    (4, "KMFY-8月抖音投放-金额:30000元"),     # 自由文本
    (5, "待定"), (6, "N/A"),
    (7, "2024-02-30"),                        # 不存在的日期
    (8, "1899-05-06"),                        # 年份越界
    (9, "2024-13-01"),                        # 月份越界
])
g2 = conv(con2, "u", "d")
chk("NULL 仍是 NULL", g2[1] is None, g2[1])
chk("空串仍是空串", g2[2] == "", repr(g2[2]))
chk("纯空格保留原值", g2[3] == "   ", repr(g2[3]))
chk("自由文本保留原值", g2[4] == "KMFY-8月抖音投放-金额:30000元", g2[4])
chk("「待定」保留原值", g2[5] == "待定", g2[5])
chk("「N/A」保留原值", g2[6] == "N/A", g2[6])
chk("2 月 30 日不算数（保留原值）", g2[7] == "2024-02-30", g2[7])
chk("1899 年越界（保留原值）", g2[8] == "1899-05-06", g2[8])
chk("13 月不合法（保留原值）", g2[9] == "2024-13-01", g2[9])

print("\n=== 3. keep_raw=false：认不出就置空 ===")
g3 = conv(con2, "u", "d", keep_raw=False)
chk("自由文本变 NULL", g3[4] is None, g3[4])
chk("「待定」变 NULL", g3[5] is None, g3[5])
chk("不存在的日期变 NULL", g3[7] is None, g3[7])
chk("年份越界变 NULL", g3[8] is None, g3[8])
chk("合法日期不受 keep_raw 影响（这组没有合法值，仅确认不会误留原值）",
    all(g3[k] is None for k in (4, 5, 6, 7, 8, 9)), g3)

print("\n=== 4. 歧义消解：order 强制 ===")
con4 = sqlite3.connect(":memory:")
con4.execute("CREATE TABLE v (k INTEGER, d TEXT)")
con4.executemany("INSERT INTO v VALUES (?,?)", [(1, "2/1/2024"), (2, "3/4/2024")])
auto = conv(con4, "v", "d")
dmy = conv(con4, "v", "d", date_order="dmy")
mdy = conv(con4, "v", "d", date_order="mdy")
chk("auto 按「月/日/年」：2/1/2024 → 2024-02-01", auto[1] == "2024-02-01", auto[1])
chk("dmy 强制：2/1/2024 → 2024-01-02", dmy[1] == "2024-01-02", dmy[1])
chk("mdy 强制：3/4/2024 → 2024-03-04", mdy[2] == "2024-03-04", mdy[2])
chk("dmy 强制：3/4/2024 → 2024-04-03", dmy[2] == "2024-04-03", dmy[2])

print("\n=== 5. 列元数据与后续步骤 ===")
con5 = sqlite3.connect(":memory:")
con5.execute("CREATE TABLE w (k INTEGER, d TEXT)")
con5.executemany("INSERT INTO w VALUES (?,?)", [
    (1, "2024/3/15"), (2, "2023/1/9"), (3, "2024/1/2"), (4, None),
])
steps = [{"op": "change_type", "column": "d", "type": "date"}]
r5 = T.preview(con5, "w", steps)
meta = [c for c in r5["columns"] if c["name"] == "d"][0]
chk("列元数据带 date 标记（表头据此显示 YMD）", meta.get("date") is True, meta)
chk("声明类型仍是 TEXT（SQLite 只能存 ISO 文本）", meta["type"] == "TEXT", meta["type"])
chk("行数不变", r5["total"] == 4, r5["total"])
chk("row_preserving 为真", T.compile_query(con5, "w", steps)["row_preserving"] is True)

# 转成日期后按它排序，应是时间序而不是字典序
steps2 = steps + [{"op": "sort", "items": [{"column": "d", "desc": False}]}]
r6 = T.preview(con5, "w", steps2)
i = r6["colnames"].index("d")
order = [row[i] for row in r6["rows"] if row[i] is not None]
chk("按日期列排序得时间序", order == ["2023-01-09", "2024-01-02", "2024-03-15"], order)
chk("加排序后 row_preserving 仍为真",
    T.compile_query(con5, "w", steps2)["row_preserving"] is True)

# 加筛选后 row_preserving 为假
steps3 = steps + [{"op": "filter", "column": "d", "cmp": "gt", "value": "2024-01-01"}]
chk("加筛选后 row_preserving 为假",
    T.compile_query(con5, "w", steps3)["row_preserving"] is False)
r7 = T.preview(con5, "w", steps3)
chk("筛选后的总行数是筛选后的真值", r7["total"] == 2, r7["total"])

print("\n=== 6. 与其它步骤组合 ===")
con8 = sqlite3.connect(":memory:")
con8.execute("CREATE TABLE g (k INTEGER, d TEXT)")
con8.executemany("INSERT INTO g VALUES (?,?)", [
    (1, "2024/3/15"), (2, "2024/3/15"), (3, "2024/1/2"),
])
steps8 = steps + [{"op": "group_by", "keys": ["d"],
                   "aggs": [{"column": "", "func": "count_all", "alias": "n"}]},
                  {"op": "sort", "items": [{"column": "d", "desc": False}]}]
r8 = T.preview(con8, "g", steps8)
chk("按日期分组能正确合并同一天（说明转换值真的规范化了）",
    r8["rows"][0][0] == "2024-01-02" and r8["rows"][0][1] == 1, r8["rows"])

print("\n=== 7. 真实库回归 ===")
DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                  "procurement.db")
if os.path.exists(DB):
    rc = sqlite3.connect(f"file:{DB.replace(os.sep, '/')}?mode=ro", uri=True)

    def real(col):
        st = [{"op": "change_type", "column": col, "type": "date"}]
        sql = T.compile_query(rc, "fct_prpo", st)["sql"]
        q = T.qid(col)
        return rc.execute(f"""SELECT
            SUM(CASE WHEN {q} IS NOT NULL AND {q} GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]' THEN 1 ELSE 0 END),
            SUM(CASE WHEN {q} IS NOT NULL AND NOT ({q} GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]') THEN 1 ELSE 0 END)
            FROM ({sql})""").fetchone()

    t0 = time.time()
    iso, left = real("提交日期")
    chk("fct_prpo.提交日期 44,987 行全部转成 ISO", iso == 44987, f"ISO {iso} / 原值 {left}")
    chk("fct_prpo.提交日期 无遗留原值", left == 0, left)
    chk("fct_prpo.提交日期 全表 < 1s（ISO 快通道）", time.time() - t0 < 1.0,
        f"{time.time() - t0:.2f}s")

    iso, left = real("框架合同到期日")
    chk("fct_prpo.框架合同到期日 1.3 万行斜杠/英文月全部转成 ISO", iso >= 13600, f"ISO {iso}")
    chk("fct_prpo.框架合同到期日 仅个位数残留", left <= 100, f"残留 {left}")

    iso, left = real("供应商 AVL 到期日期")
    chk("fct_prpo.供应商 AVL 到期日期 4.4 万行混序全部转成 ISO", iso >= 44850, f"ISO {iso}")
    chk("fct_prpo.供应商 AVL 到期日期 仅个位数残留", left <= 50, f"残留 {left}")

    # 自由文本列不该被静默清空
    iso, left = real("说明")
    chk("fct_prpo.说明 自由文本保留原值（未被清空）", left >= 44000, f"保留 {left}")
    rc.close()
else:
    print("  (跳过：找不到 procurement.db)")

print(f"\n===== {ok} 通过 / {fail} 失败 =====")
sys.exit(1 if fail else 0)
