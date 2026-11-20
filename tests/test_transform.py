# -*- coding: utf-8 -*-
"""
transform.py 编译器单元测试
============================
大部分用例用内存库构造，速度快、语义可断言；
最后一组针对真实 procurement.db 只读回归「金额列声明 REAL 实际存 '¥30,000.00' 文本」
这个真实数据坑（不修就会 SUM 出 0）。

跑法：<venv>/python.exe tests/test_transform.py
"""
import os
import sqlite3
import sys

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


def make_db():
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript("""
    CREATE TABLE t_sales (
        id TEXT PRIMARY KEY,
        d TEXT,           -- 日期
        n TEXT,           -- 客户
        cat TEXT,         -- 类别
        amt REAL,         -- 金额（故意存成 '¥1,200' 文本，复现真实库问题）
        qty INTEGER,
        note TEXT
    );
    INSERT INTO t_sales VALUES
      ('A1','2026-01-05','甲','X','¥1,200.00',3,NULL),
      ('A2','2026-01-18','乙','X','¥800',1,'ok'),
      ('A3','2026-02-02','甲','Y','¥2,000.50',5,NULL),
      ('A4','2026-02-20','丙','Y','¥300',2,'ok'),
      ('A5','2026-03-11','乙','X','¥1,000',1,NULL);
    CREATE TABLE t_lookup (code TEXT PRIMARY KEY, name TEXT, grp TEXT);
    INSERT INTO t_lookup VALUES ('甲','Alpha','G1'),('乙','Beta','G1'),('丙','Gamma','G2');
    CREATE TABLE t_other (a TEXT, b TEXT);
    INSERT INTO t_other VALUES ('z1','q'),('z2','w');
    """)
    # 把金额列真的存成文本，模拟实际数据
    for rid, val in [("A1", "¥1,200.00"), ("A2", "¥800"), ("A3", "¥2,000.50"),
                     ("A4", "¥300"), ("A5", "¥1,000")]:
        con.execute("UPDATE t_sales SET amt=? WHERE id=?", (val, rid))
    con.commit()
    return con


def prev(con, steps, limit=50, upto=None):
    return T.preview(con, "t_sales", steps, upto=upto, limit=limit)


print("=== 1. 金额文本清洗（声明 REAL 实存 '¥1,200.00'） ===")
con = make_db()
cols = {c["name"]: c for c in T.get_columns(con, "t_sales")}
chk("amt 声明 REAL", cols["amt"]["type"] == "REAL")
chk("amt 实际标记 store=text", cols["amt"].get("store") == "text", str(cols["amt"]))
chk("裸 SUM 得 0（复现坑）", con.execute("SELECT SUM(amt) FROM t_sales").fetchone()[0] == 0.0)
r = prev(con, [{"op": "group_by", "keys": ["cat"],
                "aggs": [{"column": "amt", "func": "sum", "alias": "总额"}]}])
vals = {row[0]: row[1] for row in r["rows"]}
chk("分组求和 X=3000", abs(vals.get("X", 0) - 3000.0) < 0.01, str(vals))
chk("分组求和 Y=2300.5", abs(vals.get("Y", 0) - 2300.5) < 0.01, str(vals))

print("\n=== 2. 筛选各比较方式 ===")
chk("eq 文本", prev(con, [{"op": "filter", "column": "n", "cmp": "eq", "value": "甲"}])["total"] == 2)
chk("ne", prev(con, [{"op": "filter", "column": "n", "cmp": "ne", "value": "甲"}])["total"] == 3)
chk("contains", prev(con, [{"op": "filter", "column": "d", "cmp": "contains", "value": "2026-02"}])["total"] == 2)
chk("starts", prev(con, [{"op": "filter", "column": "n", "cmp": "starts", "value": "乙"}])["total"] == 2)
chk("ends", prev(con, [{"op": "filter", "column": "note", "cmp": "ends", "value": "ok"}])["total"] == 2)
chk("in", prev(con, [{"op": "filter", "column": "cat", "cmp": "in", "value": ["X"]}])["total"] == 3)
chk("not_in", prev(con, [{"op": "filter", "column": "cat", "cmp": "not_in", "value": ["X"]}])["total"] == 2)
chk("is_null", prev(con, [{"op": "filter", "column": "note", "cmp": "is_null"}])["total"] == 3)
chk("not_null", prev(con, [{"op": "filter", "column": "note", "cmp": "not_null"}])["total"] == 2)
chk("is_empty", prev(con, [{"op": "filter", "column": "note", "cmp": "is_empty"}])["total"] == 3)
_bare_gt = con.execute("SELECT COUNT(*) FROM t_sales WHERE amt > 1000").fetchone()[0]
chk("字符串直接比较会误判（复现坑）", _bare_gt == 5, f"-> {_bare_gt}")
chk("数值 gt（自动清洗 ¥ 和逗号）",
    prev(con, [{"op": "filter", "column": "amt", "cmp": "gt", "value": "1000"}])["total"] == 2)
chk("between",
    prev(con, [{"op": "filter", "column": "amt", "cmp": "between", "value": "500", "value2": "1500"}])["total"] == 3)

print("\n=== 3. 列操作 ===")
r = prev(con, [{"op": "keep_columns", "columns": ["id", "n"]}])
chk("keep_columns", r["colnames"] == ["id", "n"], str(r["colnames"]))
r = prev(con, [{"op": "remove_columns", "columns": ["note", "amt"]}])
chk("remove_columns", "note" not in r["colnames"] and "amt" not in r["colnames"])
r = prev(con, [{"op": "rename_column", "column": "n", "new_name": "客户"}])
chk("rename_column", "客户" in r["colnames"] and "n" not in r["colnames"])
r = prev(con, [{"op": "change_type", "column": "amt", "type": "number"}])
chk("change_type number", isinstance(r["rows"][0][r["colnames"].index("amt")], float))

print("\n=== 4. 文本处理 ===")
r = prev(con, [{"op": "replace_value", "column": "cat", "find": "X", "replace": "Z", "exact": True},
               {"op": "keep_columns", "columns": ["cat"]}, {"op": "distinct"}])
cats = sorted(x[0] for x in r["rows"])
chk("replace_value 整体替换", cats == ["Y", "Z"], str(cats))
r = prev(con, [{"op": "replace_value", "column": "d", "find": "2026-0", "replace": "26/", "exact": False},
               {"op": "keep_columns", "columns": ["d"]}])
chk("replace_value 片段替换", str(r["rows"][0][0]).startswith("26/1"), str(r["rows"][0][0]))
r = prev(con, [{"op": "add_column", "new_name": "年", "expr": "substr(\"d\",1,4)"},
               {"op": "keep_columns", "columns": ["年"]}, {"op": "distinct"}])
chk("add_column", [x[0] for x in r["rows"]] == ["2026"])
r = prev(con, [{"op": "split_column", "column": "d", "delimiter": "-", "count": 3, "prefix": "p"},
               {"op": "keep_columns", "columns": ["p1", "p2", "p3"]}, {"op": "limit", "count": 1}])
chk("split_column 3 段", r["colnames"] == ["p1", "p2", "p3"], str(r["colnames"]))
chk("split 首行拆分正确", r["rows"][0] == ["2026", "01", "05"], str(r["rows"][0]))
r = prev(con, [{"op": "extract_text", "column": "d", "mode": "left", "count": 7},
               {"op": "keep_columns", "columns": ["d"]}, {"op": "distinct"}])
chk("extract_text left", [x[0] for x in r["rows"]][0] == "2026-01", str(r["rows"]))
r = prev(con, [{"op": "clean_text", "columns": ["note"], "mode": "upper"}, {"op": "distinct"},
               {"op": "keep_columns", "columns": ["note"]}])
chk("clean_text upper", "OK" in [x[0] for x in r["rows"]], str(r["rows"]))

print("\n=== 5. 行列变换 ===")
r = prev(con, [{"op": "keep_columns", "columns": ["cat"]}, {"op": "distinct"}])
chk("distinct", r["total"] == 2, str(r["total"]))
r = prev(con, [{"op": "sort", "items": [{"column": "amt", "desc": True}]},
               {"op": "keep_columns", "columns": ["id"]}])
chk("sort 数值降序", [x[0] for x in r["rows"]][0] == "A3", str(r["rows"]))
r = prev(con, [{"op": "limit", "count": 2}])
chk("limit", r["total"] == 2)
r = prev(con, [{"op": "join", "table": "t_lookup", "left_on": "n", "right_on": "code",
                "type": "left", "columns": ["name", "grp"]},
               {"op": "keep_columns", "columns": ["id", "name"]}])
chk("join 左连接", r["total"] == 5 and r["colnames"] == ["id", "name"], str(r["colnames"]))
chk("join 取到右表值", r["rows"][0][1] == "Alpha", str(r["rows"][0]))
r = prev(con, [{"op": "group_by", "keys": ["cat"],
                "aggs": [{"column": "amt", "func": "count_all", "alias": "单数"},
                         {"column": "n", "func": "count_distinct", "alias": "客户数"}]},
               {"op": "sort", "items": [{"column": "cat"}]}])
chk("group_by 计数", r["rows"][0][1:] == [3, 2], str(r["rows"][0]))
r = prev(con, [{"op": "group_by", "keys": ["cat"],
                "aggs": [{"column": "amt", "func": "count_all", "alias": "c"}]},
               {"op": "unpivot", "id_columns": ["cat"], "value_columns": ["c"],
                "name_column": "指标", "value_column": "值"}])
chk("unpivot 列名", r["colnames"] == ["cat", "指标", "值"], str(r["colnames"]))
chk("unpivot 行数", r["total"] == 2)

print("\n=== 6. 空汇总行忽略 / 禁用步骤 / 分步预览 ===")
r = prev(con, [{"op": "group_by", "keys": ["cat"],
                "aggs": [{"column": "amt", "func": "sum", "alias": "s"},
                         {"column": "", "func": "sum", "alias": ""}]}])
chk("空汇总行被忽略", r["total"] == 2 and len(r["colnames"]) == 2, str(r["colnames"]))
r = prev(con, [{"op": "filter", "column": "n", "cmp": "eq", "value": "甲", "disabled": True}])
chk("禁用步骤跳过", r["total"] == 5)
steps = [{"op": "filter", "column": "cat", "cmp": "eq", "value": "X"},
         {"op": "keep_columns", "columns": ["id"]}]
for k, exp_rows in [(0, 5), (1, 3), (2, 3)]:
    r = prev(con, steps, upto=k)
    chk(f"upto={k} 行数 {exp_rows}", r["total"] == exp_rows, f"-> {r['total']}")

print("\n=== 7. 错误提示 ===")
def expect_err(name, steps, kw=""):
    try:
        prev(con, steps)
        chk(name, False, "未抛错")
    except T.TransformError as e:
        chk(name, kw in str(e), f"-> {e}")

expect_err("不存在的列", [{"op": "keep_columns", "columns": ["无此列"]}], "不存在")
expect_err("未知步骤", [{"op": "magic"}], "不支持")
expect_err("筛选缺值", [{"op": "filter", "column": "n", "cmp": "eq", "value": ""}], "比较值")
expect_err("分组全空", [{"op": "group_by", "keys": [], "aggs": []}], "至少")
expect_err("重命名撞名", [{"op": "rename_column", "column": "n", "new_name": "cat"}], "已存在")
expect_err("删光所有列", [{"op": "remove_columns", "columns": ["id", "d", "n", "cat", "amt", "qty", "note"]}], "全部")
expect_err("错误信息带步骤号", [{"op": "keep_columns", "columns": ["无此列"]}], "第 1 步")

print("\n=== 8. 表达式安全 ===")
for bad in ["1; DROP TABLE t", "1 -- x", "1 /* x */", "load_extension('x')",
            "update t set a=1", "attach database 'x' as y"]:
    try:
        T.check_expr(bad)
        chk(f"拦截 {bad[:20]}", False, "未拦截")
    except T.TransformError:
        chk(f"拦截 {bad[:20]}", True)
chk("允许正常表达式", T.check_expr('ROUND("amt"/1000, 2)') == 'ROUND("amt"/1000, 2)')

print("\n=== 9. 只读预览不能写 ===")
ro = sqlite3.connect("file::memory:?cache=shared", uri=True)
con2 = make_db()
# 模拟只读连接：直接用 mode=ro 打开临时文件
import tempfile
tmp = os.path.join(tempfile.gettempdir(), "_tf_ro_test.db")
if os.path.exists(tmp):
    os.remove(tmp)
disk = sqlite3.connect(tmp)
con2.backup(disk)
disk.close()
rocon = sqlite3.connect(f"file:{tmp}?mode=ro", uri=True)
try:
    rocon.execute("INSERT INTO t_sales VALUES ('Z','x','y','z','1',1,'n')")
    chk("只读连接拒绝写入", False, "竟然写成功")
except sqlite3.OperationalError as e:
    chk("只读连接拒绝写入", "readonly" in str(e).lower() or "read-only" in str(e).lower(), str(e))
rocon.close()
try:
    os.remove(tmp)
except OSError:
    pass

print("\n=== 10. 真实库：金额清洗回归 ===")
real = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "procurement.db")
if os.path.exists(real):
    rc = sqlite3.connect(f"file:{real}?mode=ro", uri=True)
    rcols = {c["name"]: c for c in T.get_columns(rc, "fct_prpo")}
    chk("真实库 含增值税总价 store=text", rcols["含增值税总价"].get("store") == "text",
        str(rcols.get("含增值税总价")))
    bare = rc.execute('SELECT SUM("含增值税总价") FROM fct_prpo').fetchone()[0]
    chk("真实库裸 SUM 为 0（确认坑存在）", bare == 0.0, str(bare))
    r = T.preview(rc, "fct_prpo", [
        {"op": "filter", "column": "支出类型", "cmp": "contains", "value": "OPEX"},
        {"op": "group_by", "keys": ["支出类型"],
         "aggs": [{"column": "含增值税总价", "func": "sum", "alias": "总额"}]}], limit=2)
    got = r["rows"][0][1]
    chk("真实库 OPEX 汇总 = 737,080,140.52", abs(got - 737080140.52) < 1, f"-> {got}")
    rc.close()
else:
    chk("真实库存在", False, real)

con.close()
con2.close()
print(f"\n===== {ok} 通过 / {fail} 失败 =====")
sys.exit(1 if fail else 0)
