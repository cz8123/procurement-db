# -*- coding: utf-8 -*-
"""
列质量统计（表头「饱满度」色条的数据源）断言
=============================================
内存库构造边界语义（NULL / 空串 / 全空列 / 空结果集），
再用真实 procurement.db 只读回归几个已知列的实际占比。

跑法：<venv>/python.exe tests/test_colstats.py
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


print("=== 1. 基础语义（NULL 与空白串都算空） ===")
con = sqlite3.connect(":memory:")
con.execute("CREATE TABLE t (id INTEGER, name TEXT, amt TEXT, tag TEXT)")
con.executemany("INSERT INTO t VALUES (?,?,?,?)", [
    (1, "a",    "100", "x"),
    (2, "b",    "",    "x"),      # 空串：看起来是空的
    (3, None,   "300", None),     # NULL
    (4, "d",    "400", None),
    (5, "e",    None,  "  "),     # 纯空格
    (6, "  ",   "600", "x"),      # 纯空格文本
])
r = T.column_stats(con, "t", [])
chk("总行数 6", r["total"] == 6, r["total"])
s = r["columns"]

chk("id 全满", s["id"]["fill"] == 1.0 and s["id"]["nulls"] == 0, s["id"])
chk("name 有效 4 行（NULL + 空格串算空）",
    s["name"]["filled"] == 4 and s["name"]["nulls"] == 2,
    s["name"])
chk("name 不是单值列（a/b/d/e 多个取值）", s["name"]["const"] is False, s["name"])
chk("name 饱满度 66.7%", s["name"]["fill"] == round(4 / 6, 4), s["name"]["fill"])
chk("amt 有效 4（'' 与 NULL 算空）",
    s["amt"]["filled"] == 4, s["amt"]["filled"])
chk("amt 与 name 口径不同（'' vs NULL 都算空）",
    s["amt"]["nulls"] == 2, s["amt"]["nulls"])
chk("tag 有效 3（纯空格 '  ' 算空）",
    s["tag"]["filled"] == 3 and s["tag"]["nulls"] == 3, s["tag"])

print("=== 2. 全空列 / 空结果集 ===")
con.execute("CREATE TABLE t2 (a TEXT, b TEXT)")
con.executemany("INSERT INTO t2 VALUES (?,?)", [(None, None), ("", ""), (None, "")])
r2 = T.column_stats(con, "t2", [])
chk("全空列 fill = 0", r2["columns"]["a"]["fill"] == 0.0, r2["columns"]["a"])
chk("全空列 const = False（没有有效值谈不上单值）",
    r2["columns"]["a"]["const"] is False, r2["columns"]["a"])
chk("全空列 nulls = 总行数",
    r2["columns"]["a"]["nulls"] == r2["total"] == 3, r2["columns"]["a"])

con.execute("CREATE TABLE t3 (a TEXT)")
r3 = T.column_stats(con, "t3", [])
chk("空表 total = 0 不除零", r3["total"] == 0, r3["total"])
chk("空表 fill = 0", r3["columns"]["a"]["fill"] == 0.0, r3["columns"]["a"])

print("=== 3. 统计跟着步骤链走（不是永远读源表） ===")
con.execute("CREATE TABLE t4 (k TEXT, v TEXT)")
con.executemany("INSERT INTO t4 VALUES (?,?)",
                [("a", "1"), ("a", None), ("b", "3"), ("b", None)])
base = T.column_stats(con, "t4", [])
chk("筛选前 v 有效 2/4", base["columns"]["v"]["filled"] == 2, base["columns"]["v"])
steps = [{"op": "filter", "column": "v", "cmp": "not_empty"}]
after = T.column_stats(con, "t4", steps)
chk("筛掉空值后只有 2 行", after["total"] == 2, after["total"])
chk("筛后 v 饱满度 100%", after["columns"]["v"]["fill"] == 1.0, after["columns"]["v"])
g = T.column_stats(con, "t4", [{"op": "group_by", "keys": ["k"],
                                "aggs": [{"func": "count_all", "alias": "n"}]}])
chk("分组后只剩 2 行 2 列", g["total"] == 2 and len(g["columns"]) == 2, (g["total"], list(g["columns"])))
chk("分组结果 count_all 列 100% 满", g["columns"]["n"]["fill"] == 1.0, g["columns"]["n"])
chk("upto=0 忽略后续步骤（回到源表口径）",
    T.column_stats(con, "t4", steps, upto=0)["total"] == 4,
    T.column_stats(con, "t4", steps, upto=0)["total"])

print("=== 4. 只读：统计不会写库 ===")
import shutil, tempfile
tmpdb = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_tmp_stats.db")
shutil.copy(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "procurement.db"), tmpdb)
ro = sqlite3.connect("file:" + tmpdb.replace("\\", "/") + "?mode=ro", uri=True)
t0 = time.time()
real = T.column_stats(ro, "fct_prpo", [])
ela = time.time() - t0
ro.close()
try:
    os.remove(tmpdb)
except OSError:
    pass

print(f"  （真实库 35 列统计耗时 {ela:.2f}s）")
chk("真实库 fct_prpo 总行数 44,987", real["total"] == 44987, real["total"])
chk("真实库列数 35", len(real["columns"]) == 35, len(real["columns"]))
row = real["columns"].get("支出子类") or {}
chk("支出子类 饱满度 43.8%", abs(row.get("fill", 0) - 0.4379) < 0.005, row)
capex = real["columns"].get("CAPEX 项目批准参考号") or {}
chk("CAPEX 项目批准参考号 几乎全空（<0.2%）", 0 < capex.get("fill", 1) < 0.002, capex)
amt = real["columns"].get("含增值税总价") or {}
chk("含增值税总价 饱满度 100%", amt.get("fill") == 1.0, amt)
chk("含增值税总价 不是单值列", amt.get("const") is False, amt.get("const"))
chk("统计耗时 < 8s（真跑一遍全表）", ela < 8.0, f"{ela:.2f}s")

print("=== 5. 前端涂色规则的一致性（fill=0 时不该画绿段） ===")
chk("全空列 fill 恰为 0.0（前端据此跳过绿段）", r2["columns"]["a"]["fill"] == 0.0)
con.execute("CREATE TABLE t5 (a TEXT)")
con.executemany("INSERT INTO t5 VALUES (?)", [("X",), ("X",), (None,)])
c5 = T.column_stats(con, "t5", [])["columns"]["a"]
chk("单值列 const = True（前端据此把绿条调暗）", c5["const"] is True, c5)
chk("单值列饱满度仍是 66.7%（空值照样扣）", c5["fill"] == round(2 / 3, 4), c5["fill"])
con.execute("CREATE TABLE t6 (a TEXT)")
con.executemany("INSERT INTO t6 VALUES (?)", [("X",), ("X ",), (" X",)])
c6 = T.column_stats(con, "t6", [])["columns"]["a"]
chk("单值判定忽略前后空格（'X' / 'X ' / ' X' 算同一个值）", c6["const"] is True, c6)

print()
print(f"通过 {ok} / 失败 {fail}")
sys.exit(1 if fail else 0)
