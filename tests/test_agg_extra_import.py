# -*- coding: utf-8 -*-
"""映射聚合：自定义表头 → 附加字段 的导入 / 保存 / 导出 回归测试。

用法场景（用户提出）：
    部门cn,部门en
    Aftersales-售后部,Aftersales
    ...
第一列是原始值（表头叫什么都行），第二列起都是「映射列字段」，
导入后要按表头在聚合维度表上建出同名附加字段并填值。

策略同其他测试：复制真实库到临时文件再重指向，**不触碰真实库**；
写操作都带 backup=false，避免在 tests/ 里堆备份。
"""
import io
import os
import sqlite3
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import db_manager as db  # noqa: E402

COPY = os.path.join(HERE, "_tmp_agg_extra.db")
for _f in (COPY, COPY + "-wal", COPY + "-shm"):
    if os.path.exists(_f):
        os.remove(_f)
shutil.copyfile(db.DB_PATH, COPY)
db.DB_PATH = COPY

import aggmap  # noqa: E402
import app  # noqa: E402

client = app.app.test_client()

PASS = FAIL = 0


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


def cols(table):
    c = con()
    try:
        return [r[1] for r in c.execute(f'PRAGMA table_info("{table}")')]
    finally:
        c.close()


def scalar(sql, args=()):
    c = con()
    try:
        return c.execute(sql, args).fetchone()
    finally:
        c.close()


TABLE, COL = "fct_prpo", "部门"
c0 = con()
_scan = aggmap.auto_map(c0, TABLE, COL)
c0.close()
TOP = [r["raw"] for r in _scan["rows"][:3]]
print("源字段前 3 个取值：", TOP)

# ===========================================================================
print("== 1. 解析：自定义表头 → 附加字段 ==")
csv_cn_en = ("\ufeff部门cn,部门en\n"
             + f"{TOP[0]},DepA\n{TOP[1]},DepB\n").encode("utf-8")
known = {r["raw"] for r in _scan["rows"]}
p = aggmap.parse_mapping(csv_cn_en, "m.csv", is_known=lambda v: v if v in known else None)
check("首行被认成表头", p["header"] is True and p["header_guess"] is True, p)
check("第一列 = 原始值，不受表头名字影响", p["records"][0]["raw"] == TOP[0], p["records"][:1])
check("没有「聚合值」列 → 不覆盖现有聚合值（agg 为 None）",
      all(r["agg"] is None for r in p["records"]), p["records"][:1])
check("第二列按表头建附加字段", p["extra_cols"] == ["部门en"], p["extra_cols"])
check("附加字段值逐行带出", p["records"][0]["extra"] == {"部门en": "DepA"}, p["records"][:1])
check("agg_from_file 标记为 False", p["agg_from_file"] is False)
check("记录条数 = 数据行", len(p["records"]) == 2, len(p["records"]))

print("== 2. 解析：老格式与多列混排 ==")
p2 = aggmap.parse_mapping(
    "原始值,行数,聚合值\nIT 硬件,3,IT 硬件\nIT硬件,2,IT 硬件\n".encode("utf-8-sig"), "m.csv")
check("旧导出的「行数」列被跳过", "行数" not in p2["extra_cols"], p2["extra_cols"])
check("旧格式仍按两列读", p2["pairs"] == [("IT 硬件", "IT 硬件"), ("IT硬件", "IT 硬件")], p2["pairs"])

p3 = aggmap.parse_mapping(
    "原始值,聚合值,部门en\nA,a,Apple\nB,a,Banana\n".encode("utf-8"), "m.csv")
check("有聚合值列 + 附加字段列", p3["extra_cols"] == ["部门en"]
      and p3["pairs"] == [("A", "a"), ("B", "a")], (p3["pairs"], p3["extra_cols"]))
check("附加字段值与原始值对齐", p3["records"][1]["extra"] == {"部门en": "Banana"}, p3["records"])

p4 = aggmap.parse_mapping(
    "原始值,聚合值,部门 en(备注)\nA,a,x\n".encode("utf-8"), "m.csv")
check("非法表头被改成合法列名", p4["extra_cols"] == ["部门en_备注"], p4["extra_cols"])
check("改名有记录可提示", p4["renamed"] == [{"from": "部门 en(备注)", "to": "部门en_备注"}],
      p4["renamed"])

p5 = aggmap.parse_mapping("甲,乙\n丙,丁\n".encode("gbk"), "m.csv")
check("无表头两列保持老行为", p5["pairs"] == [("甲", "乙"), ("丙", "丁")], p5["pairs"])
check("无表头时不生成附加字段", p5["extra_cols"] == [], p5["extra_cols"])

# ===========================================================================
print("== 3. 导入接口返回 extra_cols / 行内 extra ==")
r = client.post("/api/agg/import", data={
    "table": TABLE, "col": COL,
    "file": (io.BytesIO(csv_cn_en), "部门cn_en.csv")}, content_type="multipart/form-data")
imp = r.get_json()
check("导入成功", r.status_code == 200 and imp["ok"], imp.get("error"))
check("响应带 extra_cols", imp["extra_cols"] == ["部门en"], imp.get("extra_cols"))
check("命中 2 个源取值", imp["stats"]["matched"] == 2, imp["stats"])
check("表头行不算「找不到」", imp["stats"]["unknown"] == 0, imp["stats"])
_by = {x["raw"]: x for x in imp["rows"]}
check("行内带附加字段值", _by[TOP[0]].get("extra") == {"部门en": "DepA"},
      _by[TOP[0]])
_c = con()
_t = aggmap.task_of(_c, TABLE, COL)
_dbvals = {it["raw"]: it["agg"] for it in aggmap.task_rows(_c, _t)} if _t else {}
_c.close()
check("命中的行聚合值沿用库里的映射（文件没有聚合值列）",
      _by[TOP[0]]["agg"] == _dbvals.get(TOP[0]), (_by[TOP[0]], _dbvals.get(TOP[0])))

# ===========================================================================
print("== 4. 首次保存：建表 + 按表头建附加字段并填值 ==")
rows = [{"raw": x["raw"], "agg": x["agg"], "rows": x["rows"], "extra": x.get("extra")}
        for x in imp["rows"]]
b = client.post("/api/agg/build", json={
    "name": "部门聚合(附加字段)", "source_table": TABLE, "source_col": COL,
    "dim_table": "dim_agg_extra_test", "value_col": "部门名称",
    "rows": rows, "extra_cols": [{"name": "部门en", "type": "text"}],
    "backup": False, "disable_existing": False})
res = b.get_json()
check("建表成功", b.status_code == 200 and res["ok"], res.get("error"))
check("维度表有 部门en 列", "部门en" in cols("dim_agg_extra_test"), cols("dim_agg_extra_test"))
check("未凭空多出统计列",
      [x for x in cols("dim_agg_extra_test") if x in ("源行数", "写法数")] == [],
      cols("dim_agg_extra_test"))
_v = scalar('SELECT "部门en" FROM dim_agg_extra_test WHERE "原始值"=?', (TOP[0],))
check("附加字段值已落库", _v and _v[0] == "DepA", _v)
_blank = scalar('SELECT COUNT(*) FROM dim_agg_extra_test WHERE "部门en" IS NULL')[0]
check("没提供附加值的行留空（不编造）", _blank == len(rows) - 2, (_blank, len(rows)))
check("返回 extra_cols 含 部门en",
      [x["name"] for x in res.get("extra_cols") or []] == ["部门en"], res.get("extra_cols"))

tid = res["task_id"]

# ===========================================================================
print("== 5. 再次导入（新增表头）：补列 + 保留手工值 ==")
c = con()
c.execute('UPDATE dim_agg_extra_test SET "部门en"=? WHERE "原始值"=?',
          ("手工改过的英文名", TOP[1]))
c.commit()
c.close()

csv_more = ("\ufeff部门cn,部门en,部门简称\n"
            + f"{TOP[0]},DepA2,D1\n{TOP[1]},,D2\n").encode("utf-8")
r2 = client.post("/api/agg/import", data={
    "table": TABLE, "col": COL,
    "file": (io.BytesIO(csv_more), "more.csv")}, content_type="multipart/form-data")
imp2 = r2.get_json()
check("第二次导入识别两个附加字段", imp2["extra_cols"] == ["部门en", "部门简称"],
      imp2.get("extra_cols"))
rows2 = [{"raw": x["raw"], "agg": x["agg"], "rows": x["rows"], "extra": x.get("extra")}
         for x in imp2["rows"]]
u = client.put(f"/api/agg/task/{tid}", json={"rows": rows2, "backup": False,
                                            "extra_cols": [{"name": "部门en"},
                                                           {"name": "部门简称"}]})
check("保存成功（新表头自动补列）", u.status_code == 200 and u.get_json()["ok"],
      u.get_json())
check("新列 部门简称 已建", "部门简称" in cols("dim_agg_extra_test"), cols("dim_agg_extra_test"))
_v2 = scalar('SELECT "部门简称" FROM dim_agg_extra_test WHERE "原始值"=?', (TOP[1],))
check("新列的值已写入", _v2 and _v2[0] == "D2", _v2)
_v3 = scalar('SELECT "部门en" FROM dim_agg_extra_test WHERE "原始值"=?', (TOP[0],))
check("文件里的新值覆盖旧值", _v3 and _v3[0] == "DepA2", _v3)
_v4 = scalar('SELECT "部门en" FROM dim_agg_extra_test WHERE "原始值"=?', (TOP[1],))
check("文件里留空的单元格不清空手工值", _v4 and _v4[0] == "手工改过的英文名", _v4)

# ===========================================================================
print("== 5.5 局部导入：文件里没有的取值和留空的附加字段保持现状 ==")
import json  # noqa: E402

partial = ("\ufeff原始值,聚合值,部门en\n" + f"{TOP[0]},合并后的值,DepX\n").encode("utf-8")
_base = json.dumps([
    {"raw": TOP[0], "agg": TOP[0], "extra": {"部门en": "DepA2"}},
    {"raw": TOP[1], "agg": "BASE保留值", "extra": {"部门en": "保留英文名"}},
], ensure_ascii=False)
r = client.post("/api/agg/import", data={
    "table": TABLE, "col": COL, "base": _base,
    "file": (io.BytesIO(partial), "partial.csv")}, content_type="multipart/form-data")
imp_p = r.get_json()
_byp = {x["raw"]: x for x in imp_p["rows"]}
check("文件里的取值被更新", _byp[TOP[0]]["agg"] == "合并后的值", _byp[TOP[0]])
check("文件里的附加字段被更新", _byp[TOP[0]]["extra"] == {"部门en": "DepX"}, _byp[TOP[0]])
check("文件里没有的取值保持屏幕上那份", _byp[TOP[1]]["agg"] == "BASE保留值", _byp[TOP[1]])
check("文件里没有的行保留附加字段值",
      _byp[TOP[1]]["extra"] == {"部门en": "保留英文名"}, _byp[TOP[1]])
check("附加字段清单含基线里的列", "部门en" in imp_p["extra_cols"], imp_p["extra_cols"])

# ===========================================================================
print("== 6. 导出带上附加字段，回导不丢 ==")
e = client.post("/api/agg/export", json={
    "table": TABLE, "col": COL, "fmt": "csv", "rows": rows2})
head = e.data.decode("utf-8-sig").splitlines()[0].strip()
check("导出表头含附加字段", head == "原始值,聚合值,部门en,部门简称", head)
e2 = client.post("/api/agg/export", json={"table": TABLE, "col": COL, "fmt": "csv"})
head2 = e2.data.decode("utf-8-sig").splitlines()[0].strip()
check("不传 rows 时按维度表现状导出", head2 == "原始值,聚合值,部门en,部门简称", head2)
r3 = client.post("/api/agg/import", data={
    "table": TABLE, "col": COL,
    "file": (io.BytesIO(e.data), "roundtrip.csv")}, content_type="multipart/form-data")
imp3 = r3.get_json()
_by3 = {x["raw"]: x for x in imp3["rows"]}
check("导出的文件可原样回导", imp3["stats"]["unknown"] == 0
      and imp3["stats"]["matched"] == imp3["stats"]["source_values"], imp3["stats"])
check("回导后附加字段值一致", _by3[TOP[0]].get("extra") == {"部门en": "DepA2", "部门简称": "D1"},
      _by3[TOP[0]])

# ===========================================================================
print("== 7. 边界：附加字段名撞聚合值列要报清楚 ==")
bad = client.put(f"/api/agg/task/{tid}", json={
    "rows": rows2, "backup": False, "extra_cols": [{"name": "部门名称"}]})
check("撞聚合值列被拒", bad.status_code == 400
      and "聚合值列名" in bad.get_json().get("error", ""), bad.get_json())

# ===========================================================================
print("== 8. 清理（不留在副本里） ==")
d = client.delete(f"/api/tables/dim_agg_extra_test?confirm=dim_agg_extra_test&backup=0")
check("测试表已删除", d.status_code == 200 and d.get_json()["ok"], d.get_json())
if not FAIL:      # 库副本 ~110MB，跑通了就顺手删掉
    for _f in (COPY, COPY + "-wal", COPY + "-shm"):
        try:
            os.remove(_f)
        except OSError:
            pass

print(f"\n结果：{PASS} 通过 / {FAIL} 失败")
sys.exit(1 if FAIL else 0)
