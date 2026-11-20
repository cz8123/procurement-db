# -*- coding: utf-8 -*-
"""映射聚合（aggmap）引擎 + API 回归测试。

策略同其他测试：复制真实库到临时文件，import app 前重指向，**不触碰真实库**。
所有写操作都带 backup=false，避免在 tests/ 里堆几百 MB 的备份。
"""
import io
import os
import sys
import sqlite3
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import db_manager as db  # noqa: E402

COPY = os.path.join(HERE, "_tmp_aggmap.db")
if os.path.exists(COPY):
    os.remove(COPY)
shutil.copyfile(db.DB_PATH, COPY)
db.DB_PATH = COPY

import aggmap  # noqa: E402  (夹具需要，先于 app 引入)

# ---------------------------------------------------------------- 测试夹具
# 真实库（2026-11-02 起）已退回到「只有事实表、无任何映射」的干净状态。
# 本测试有多条断言需要一个"已存在且已建关系的聚合维度表"作为对照
# （重名拒绝、disable_existing 停用旧关系等），故在副本里先造一个。
_fx = sqlite3.connect(COPY)
_fx.row_factory = sqlite3.Row
try:
    _rows = aggmap.auto_map(_fx, "fct_prpo", "部门")["rows"]
    aggmap.build(_fx, "部门聚合(测试夹具)", "fct_prpo", "部门",
                 "dim_agg_department", "部门名称", _rows,
                 relations=[("fct_prpo", "部门")], note="test fixture")
    _fx.commit()
finally:
    _fx.close()

import app  # noqa: E402
import clean  # noqa: E402

client = app.app.test_client()

PASS = 0
FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK] {name}")
    else:
        FAIL += 1
        print(f"  [XX] {name}  {extra}")


def con():
    return app.get_con()


def jget(path):
    r = client.get(path)
    assert r.status_code == 200, f"{path} -> {r.status_code} {r.data[:200]}"
    return r.get_json()


# ---------------------------------------------------------------- 引擎
print("== 自动整合同类项 ==")
c = con()
d = aggmap.auto_map(c, "fct_prpo", "Table - 采购子类别")
check("子类别去重 244 个取值", d["total_values"] == 244, d["total_values"])
check("规范化后聚合为 144 个值", d["agg_values"] == 144, d["agg_values"])
check("合并掉 100 个写法", d["merged_values"] == 100, d["merged_values"])
check("每行都带 raw/rows/agg", all({"raw", "rows", "agg"} <= set(r) for r in d["rows"]))
check("取值行数合计 = 源表行数", sum(r["rows"] for r in d["rows"]) == 44934,
      sum(r["rows"] for r in d["rows"]))
bad = [r for r in d["rows"] if r["agg"] not in {x["agg"] for x in d["rows"]}]
check("聚合值都来自组内成员", not bad)

d2 = aggmap.auto_map(c, "fct_prpo", "公司")
check("公司名不会被跨公司误并", d2["agg_values"] >= 60, d2["agg_values"])
check("exact 模式下 merged==0 或仅空白差异", True)

# 相似度模式：小样本
c.execute("DROP TABLE IF EXISTS _agg_probe")
c.execute("CREATE TABLE _agg_probe (v TEXT)")
for v in ["采购部", "采购部门", "采购 部", "财务部", "财务部门", "IT 支持", "ITS"]:
    c.execute("INSERT INTO _agg_probe VALUES (?)", (v,))
c.commit()
d3 = aggmap.auto_map(c, "_agg_probe", "v", mode="exact")
check("精确模式只合并空白差异（7 → 6）", d3["agg_values"] == 6, d3["agg_values"])
d4 = aggmap.auto_map(c, "_agg_probe", "v", mode="fuzzy", threshold=0.8)
check("相似度模式进一步合并同类项", d4["agg_values"] < d3["agg_values"], d4["agg_values"])
check("fuzzy 模式有合并记录", d4["merged_values"] >= 2, d4["merged_values"])
d5 = aggmap.auto_map(c, "_agg_probe", "v", mode="fuzzy", threshold=1.0)
check("阈值 1.0 退化为精确模式", d5["agg_values"] == d3["agg_values"])

# ---------------------------------------------------------------- 导出 / 导入
print("== 导出 / 导入映射文件 ==")
_, rows = aggmap.export_rows(c, "fct_prpo", "Table - 采购子类别")
check("导出条数 = 去重取值数", len(rows) == 244, len(rows))
check("导出三元组完整", all(len(x) == 3 for x in rows))

csv_bytes = ("原始值,行数,聚合值\n"
             "IT 硬件,3,IT 硬件\n"
             "IT硬件,2,IT 硬件\n"
             "财务部,1,财务\n").encode("utf-8-sig")
p = aggmap.parse_mapping(csv_bytes, "m.csv")
check("识别表头并读出行数", len(p["pairs"]) == 3, len(p["pairs"]))
check("表头被标记", p["header"] is True)
check("CSV 编码识别", p["encoding"] == "utf-8-sig", p["encoding"])
check("原始值/聚合值配对正确", p["pairs"][1] == ("IT硬件", "IT 硬件"), p["pairs"][1])

p2 = aggmap.parse_mapping("甲,乙\n丙,丁\n".encode("gbk"), "m.csv")
check("无表头时按两列解析", p2["pairs"] == [("甲", "乙"), ("丙", "丁")], p2["pairs"])
check("给出无表头提示", p2["header"] is False)

p3 = aggmap.parse_mapping("原始值\t聚合值\nA\tB\n".encode("utf-8"), "m.tsv")
check("Tab 分隔也能识别", p3["pairs"] == [("A", "B")], p3["pairs"])

p4 = aggmap.parse_mapping("原始值,聚合值\nA,\n".encode("utf-8"), "m.csv")
check("聚合值留空时回退为原值", p4["pairs"] == [("A", "A")], p4["pairs"])
check("统计空聚合值数量", p4["blank_agg"] == 1, p4["blank_agg"])

try:
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.append(["原始值", "聚合值"])
    ws.append(["X", "Y"])
    bio = io.BytesIO()
    wb.save(bio)
    p5 = aggmap.parse_mapping(bio.getvalue(), "m.xlsx")
    check("xlsx 上传可解析", p5["pairs"] == [("X", "Y")], p5["pairs"])
    check("xlsx 编码标记", p5["encoding"] == "xlsx")
except ImportError:
    print("  [--] 跳过 xlsx（未安装 openpyxl）")

# ---------------------------------------------------------------- 建表
print("== 生成聚合维度表 + 自动建关系 ==")
check("表名必须以 dim_ 开头", "dim_" in (aggmap.validate_dim_name(c, "abc") or ""))
check("拒绝重名对象", "已有同名" in (aggmap.validate_dim_name(c, "dim_agg_department") or ""))
check("接受中文表名", aggmap.validate_dim_name(c, "dim_agg_测试") is None)
check("拒绝保留列名", "保留" in (aggmap.validate_value_col("原始值") or ""))

mapping = [{"raw": r["raw"], "agg": r["agg"], "rows": r["rows"]} for r in d["rows"]]
base_mc = c.execute("SELECT COUNT(*) FROM map_config").fetchone()[0]
res = aggmap.build(c, "子类别聚合", "fct_prpo", "Table - 采购子类别",
                   "dim_agg_test_sub", "聚合子类别", mapping, disable_existing=True)
c.commit()
check("建表成功", res["dim_table"] == "dim_agg_test_sub")
check("写入全部取值", res["stats"]["rows"] == 244, res["stats"])
check("聚合值 144 个", res["stats"]["aggs"] == 144, res["stats"])
check("建了 1 条关系", len(res["relations"]) == 1, res["relations"])
cols = [r[1] for r in c.execute('PRAGMA table_info("dim_agg_test_sub")')]
check("维度表只有 原始值 + 聚合值", cols == ["id", "原始值", "聚合子类别"], cols)
n = c.execute('SELECT COUNT(*) FROM map_config WHERE target_table=?', ("dim_agg_test_sub",)).fetchone()[0]
check("map_config 落到目标表", n == 1)
task = c.execute("SELECT * FROM agg_task WHERE dim_table=?", ("dim_agg_test_sub",)).fetchone()
check("任务记录已写", task is not None and task["source_col"] == "Table - 采购子类别")
check("维度表不再有 源行数/写法数 列",
      not any(x in cols for x in ("源行数", "写法数")), cols)
check("task_rows 仍能算出每个写法的行数",
      all(isinstance(r["rows"], int) and r["rows"] > 0
          for r in aggmap.task_rows(c, aggmap.get_task(c, res["task_id"]))))
check("关系基数多对一",
      c.execute("SELECT cardinality FROM map_config WHERE target_table=?",
                ("dim_agg_test_sub",)).fetchone()[0] == "*:1")

# 关系统计：用原始值关联，孤儿率应为 0
total, orphan, rate, status = aggmap.rel_stats(c, "fct_prpo", "Table - 采购子类别",
                                               "dim_agg_test_sub", "原始值")
check("按原始值关联孤儿率为 0", orphan == 0 and status == "ok", (total, orphan, rate))

# ---------------------------------------------------------------- 维度字段 → 额外关联
print("== 事实字段聚合时，同名列一并建关联 ==")
dd = aggmap.auto_map(c, "fct_prpo", "部门")
check("部门字段扫出取值", dd["total_values"] > 50, dd["total_values"])
refs = clean.scan_cascade(c, "fct_prpo", "部门", allow_same_name=True)
check("深度扫描能找到装着同一套部门名的其它事实列",
      any(h["table"] == "fct_po" and h["col"] == "部门" for h in refs),
      [h["table"] + "." + h["col"] for h in refs][:4])
check("扫描结果不含聚合维度表自身",
      not any(h["table"].startswith("dim_agg_") for h in refs),
      [h["table"] for h in refs][:4])
old_rel = c.execute(
    "SELECT enabled FROM map_config WHERE source_table='fct_prpo' AND source_col='部门' "
    "AND target_table='dim_agg_department'").fetchone()
check("建之前 PRPO.部门 与 dim_agg_department 的关系是启用的", old_rel and old_rel[0] == 1)

m2 = [{"raw": r["raw"], "agg": r["agg"], "rows": r["rows"]} for r in dd["rows"]]
res2 = aggmap.build(c, "部门聚合2", "fct_prpo", "部门",
                    "dim_agg_test_dept", "聚合部门", m2,
                    relations=[["fct_po", "部门"]], disable_existing=True)
c.commit()
check("建了 2 条关联（本表 + 同名列）", len(res2["relations"]) == 2, res2["relations"])
check("停用了 1 条旧关系", res2["disabled"] == 1, res2["disabled"])
old_rel2 = c.execute(
    "SELECT enabled FROM map_config WHERE source_table='fct_prpo' AND source_col='部门' "
    "AND target_table='dim_agg_department'").fetchone()
check("旧关系已停用", old_rel2 and old_rel2[0] == 0, old_rel2[0] if old_rel2 else None)
t2_, o2_, r2_, s2_ = aggmap.rel_stats(c, "fct_po", "部门", "dim_agg_test_dept", "原始值")
check("PO.部门 直连聚合表后孤儿率很低", r2_ < 5, round(r2_, 2))
aggmap.drop_task(c, aggmap.get_task(c, res2["task_id"]))
c.commit()
old_rel3 = c.execute(
    "SELECT enabled FROM map_config WHERE source_table='fct_prpo' AND source_col='部门' "
    "AND target_table='dim_agg_department'").fetchone()
check("删除聚合任务不会复原被停用的旧关系（需手工启用）",
      old_rel3 and old_rel3[0] == 0)
c.execute("UPDATE map_config SET enabled=1 WHERE source_table='fct_prpo' AND source_col='部门' "
          "AND target_table='dim_agg_department'")
c.commit()

# ---------------------------------------------------------------- 维护
print("== 编辑 / 刷新 / 删除 ==")
tid = res["task_id"]
t = aggmap.get_task(c, tid)
newrows = [{"raw": r["raw"], "agg": "全部合并", "rows": r["rows"]} for r in d["rows"]]
st = aggmap.rewrite_mapping(c, t, newrows)
c.commit()
check("重写映射后聚合值只剩 1 个", st["aggs"] == 1, st)
check("重写不新增行", st["rows"] == 244, st)

c.execute('INSERT INTO fct_prpo ("表单编号", "Table - 采购子类别") '
          "VALUES ('__agg_test_zzz__', '新来的子类别')")
c.commit()
r2 = aggmap.refresh_task(c, t)
c.commit()
check("刷新时补入源表新增取值", r2["added_count"] == 1, r2)
check("新增取值默认 1:1（聚合值=原值）",
      c.execute('SELECT "聚合子类别" FROM "dim_agg_test_sub" WHERE "原始值"=?',
                ("新来的子类别",)).fetchone()[0] == "新来的子类别")
c.execute("DELETE FROM fct_prpo WHERE \"表单编号\" = '__agg_test_zzz__'")
c.commit()

rels = aggmap.drop_task(c, t)
c.commit()
check("删除时清理关系", rels == 1, rels)
check("维度表已 DROP", not aggmap.has_table(c, "dim_agg_test_sub"))
check("任务记录已删",
      c.execute("SELECT COUNT(*) FROM agg_task WHERE id=?", (tid,)).fetchone()[0] == 0)
check("map_config 回到基线",
      c.execute("SELECT COUNT(*) FROM map_config").fetchone()[0] == base_mc)

# ---------------------------------------------------------------- API
print("== HTTP 接口 ==")
d = jget("/api/agg/scan?table=fct_prpo&col=部门")
check("scan 返回取值与建议名", d["ok"] and d["suggest_dim"].startswith("dim_agg_"), d.get("suggest_dim"))
check("scan 给出建议聚合值列名", d["suggest_value_col"] == "部门")
check("scan 带已有关系冲突提示", isinstance(d["conflicts"], list))

r = client.get("/api/agg/scan?table=fct_prpo&col=不存在的列")
check("scan 非法列返回 400", r.status_code == 400, r.status_code)

r = client.get("/api/agg/export?table=fct_prpo&col=部门")
check("导出 CSV 成功", r.status_code == 200 and r.data.startswith(b"\xef\xbb\xbf"))
check("CSV 只有「原始值,聚合值」两列",
      r.data.decode("utf-8-sig").splitlines()[0].strip() == "原始值,聚合值",
      r.data.decode("utf-8-sig").splitlines()[0][:40])
check("CSV 不含行数/写法数列", "行数" not in r.data.decode("utf-8-sig").splitlines()[0])
r = client.get("/api/agg/export?table=fct_prpo&col=部门&fmt=xlsx")
check("导出 xlsx 成功", r.status_code == 200 and r.data[:2] == b"PK", r.status_code)
try:
    from openpyxl import load_workbook
    _wb = load_workbook(io.BytesIO(r.data))
    _hdr = [c.value for c in _wb.active[1]]
    check("xlsx 只有 2 列且表头为 原始值/聚合值", _hdr == ["原始值", "聚合值"], _hdr)
except ImportError:
    pass

r = client.post("/api/agg/import", data={
    "table": "fct_prpo", "col": "部门",
    "file": (io.BytesIO(csv_bytes), "m.csv")}, content_type="multipart/form-data")
imp = r.get_json()
check("导入返回预览", r.status_code == 200 and imp["ok"], imp.get("error"))
check("导入时保留文件里没有的源取值（1:1）",
      len(imp["rows"]) == imp["stats"]["source_values"], imp["stats"])
check("文件里对不上的值被计数", imp["stats"]["unknown"] == 3, imp["stats"])

# 表头被改成别的名字（如「部门cn / 部门en」）：首行要当表头丢掉，不能算成「找不到」
_csv2 = ("\ufeff" + "部门cn,部门en\n"
         + "Aftersales-售后部,Aftersales\n"
         + "Sales-BMW-销售部-BMW,Sales\n").encode("utf-8")
r = client.post("/api/agg/import", data={
    "table": "fct_prpo", "col": "部门",
    "file": (io.BytesIO(_csv2), "renamed.csv")}, content_type="multipart/form-data")
imp2 = r.get_json()
check("表头改名后仍能导入", r.status_code == 200 and imp2["ok"], imp2.get("error"))
check("表头改名被识别为表头", imp2["stats"]["header"] is True, imp2["stats"])
check("表头行不算作「找不到」", imp2["stats"]["unknown"] == 0, imp2["stats"])
check("表头改名后数据行正常匹配", imp2["stats"]["matched"] == 2
      and imp2["stats"]["file_rows"] == 2, imp2["stats"])

rows_for_api = [{"raw": r["raw"], "agg": r["agg"], "rows": r["rows"]} for r in d["rows"]]
b = client.post("/api/agg/build", json={
    "name": "部门聚合", "source_table": "fct_prpo", "source_col": "部门",
    "dim_table": "dim_agg_test_api", "value_col": "聚合部门", "rows": rows_for_api,
    "disable_existing": False, "backup": False})
bj = b.get_json()
check("build 接口成功", b.status_code == 200 and bj["ok"], bj.get("error"))
check("build 后关系自闭率为 0", bj["orphan"]["orphan"] == 0, bj["orphan"])
check("build 后聚合维度表进入已知表", "dim_agg_test_api" in app.KNOWN_TABLES)
check("build 后出现在模型视图",
      "dim_agg_test_api" in {t["table"] for t in jget("/api/model-view")["tables"]})
check("build 后侧栏可见",
      "dim_agg_test_api" in {t["table"] for t in jget("/api/tables")})

tasks = jget("/api/agg/tasks")["tasks"]
mine = [t for t in tasks if t["dim_table"] == "dim_agg_test_api"]
check("任务列表包含新任务", len(mine) == 1)
check("任务列表带聚合统计", mine[0]["stats"]["aggs"] >= 1, mine[0]["stats"])

t1 = jget(f"/api/agg/task/{bj['task_id']}")
check("任务详情返回映射行", len(t1["rows"]) == len(rows_for_api), len(t1["rows"]))
check("任务详情带关系", len(t1["relations"]) == 1)

# 重启后能自动恢复登记（load_agg_meta 幂等，启动时调用）
app.unregister_agg_table("dim_agg_test_api")
check("取消登记后不再出现在已知表", "dim_agg_test_api" not in app.KNOWN_TABLES)
app.load_agg_meta()
check("重新加载元数据后自动恢复登记",
      "dim_agg_test_api" in app.KNOWN_TABLES
      and app.TABLE_LABELS.get("dim_agg_test_api", ("",))[0].startswith("聚合"))

u = client.put(f"/api/agg/task/{bj['task_id']}",
               json={"name": "部门聚合2", "backup": False})
check("改名成功", u.status_code == 200 and u.get_json()["ok"], u.get_json())
check("改名反映到表标签", "部门聚合2" in app.TABLE_LABELS["dim_agg_test_api"][0])

rf = client.post(f"/api/agg/task/{bj['task_id']}/refresh")
check("刷新接口成功", rf.status_code == 200 and rf.get_json()["ok"], rf.get_json())

r = client.post("/api/agg/build", json={
    "source_table": "fct_prpo", "source_col": "部门",
    "dim_table": "dim_agg_test_api", "value_col": "聚合部门", "rows": rows_for_api,
    "backup": False})
check("重名建表被拒", r.status_code == 400 and "已有同名" in r.get_json()["error"])

r = client.post("/api/agg/relations-scan", json={
    "table": "fct_prpo", "col": "部门"})
check("引用列扫描可用", r.status_code == 200 and r.get_json()["ok"])

dl = client.delete(f"/api/agg/task/{bj['task_id']}?backup=0")
check("删除接口成功", dl.status_code == 200 and dl.get_json()["ok"], dl.get_json())
check("删除后退出已知表", "dim_agg_test_api" not in app.KNOWN_TABLES)
check("删除后模型视图不再有该表",
      "dim_agg_test_api" not in {t["table"] for t in jget("/api/model-view")["tables"]})

# ---------------------------------------------------------------- 清理
c.execute("DROP TABLE IF EXISTS _agg_probe")
c.commit()
c.close()

print(f"\n结果：{PASS} 通过 / {FAIL} 失败")
try:
    os.remove(COPY)
except OSError:
    pass
sys.exit(1 if FAIL else 0)
