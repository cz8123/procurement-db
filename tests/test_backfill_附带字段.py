# -*- coding: utf-8 -*-
"""在数据库副本上端到端测试「附带字段（DepartmentEn）补录」。

注意：全程只操作 procurement.db 的副本，不会改动真实库。
运行：python tests/test_backfill_附带字段.py
"""
import json
import os
import shutil
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as A

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TEST_DB = os.path.join(HERE, "_work1.db")
if os.path.exists(TEST_DB):
    os.remove(TEST_DB)
shutil.copy2(os.path.join(ROOT, "procurement.db"), TEST_DB)
A.DB_PATH = TEST_DB
cli = A.app.test_client()

# 夹具：本例假设这 11 个部门写法「在事实表里有、维度表里没有」。
# 真实库可能已被补录过（孤儿为 0），因此在副本里先把它们删掉，恢复到需要补录的状态，
# 让测试不依赖真实库当前的清洗进度。
FIXTURE_KEYS = [
    "Sales-销售部_RR", "Sales-销售部_LB", "Sales-MINI-销售部-Mini",
    "Marketing-MINI-市场部-Mini", "Marketing-Motorrad-市场部-摩托车",
    "Admin_BMW_行政部-BMW", "Aftersales_BMW_售后部-BMW", "BD_DD_BMW_发展部-BMW",
    "HSE-HSE", "Used Car Sales-二手车销售",
    "Head Office-Business Development-Property-中国办公室-发展部(物业)",
]
sys.path.insert(0, HERE)
import _dim_fixture  # noqa: E402

# 维度表已在真实库中删除，这里在副本里重建最小夹具（含 DepartmentEn 附带字段）
_dim_fixture.build(TEST_DB)

_fx = sqlite3.connect(TEST_DB)
for _k in FIXTURE_KEYS:
    _fx.execute("DELETE FROM dim_department WHERE DepartmentName = ?", (_k,))
_fx.commit()
_fx.close()

OK = FAIL = 0


def chk(cond, msg):
    global OK, FAIL
    if cond:
        OK += 1
        print("  [OK]  " + msg)
    else:
        FAIL += 1
        print("  [!!]  " + msg)


def q(sql, args=()):
    con = sqlite3.connect(TEST_DB)
    con.row_factory = sqlite3.Row
    try:
        return con.execute(sql, args).fetchall()
    finally:
        con.close()


print("=" * 74)
print("步骤 1  关系接口是否带出「附带字段」")
print("=" * 74)
dq = cli.get("/api/data-quality").get_json()
rel = [r for r in dq["relationships"] if r["fact"] == "fct_po" and r["fact_col"] == "部门"][0]
chk(rel["extra_cols"] == ["DepartmentEn"], f"fct_po.部门 的附带字段 = {rel['extra_cols']}")
extra_cols = rel["extra_cols"]

print()
print("=" * 74)
print("步骤 2  缺失值清单是否给出「分类推荐」")
print("=" * 74)
mk = cli.get("/api/missing-keys", query_string={
    "fact": "fct_po", "fact_col": "部门",
    "dim": "dim_department", "dim_col": "DepartmentName",
    "page": 1, "size": 200}).get_json()
chk(mk["extra_cols"] == ["DepartmentEn"], "响应含 extra_cols")
chk("DepartmentEn" in mk["extra_options"],
    f"响应含下拉可选值（{len(mk.get('extra_options', {}).get('DepartmentEn', []))} 个）")
expect = {
    "Sales-销售部_RR": "Sales",
    "Sales-销售部_LB": "Sales",
    "Sales-MINI-销售部-Mini": "Sales",
    "Marketing-MINI-市场部-Mini": "Marketing",
    "Marketing-Motorrad-市场部-摩托车": "Marketing",
    "Admin_BMW_行政部-BMW": "Admin",
    "Aftersales_BMW_售后部-BMW": "Aftersales",
    "BD_DD_BMW_发展部-BMW": "BD",
    "HSE-HSE": "HSE",
    "Used Car Sales-二手车销售": "Used Car",
    "Head Office-Business Development-Property-中国办公室-发展部(物业)": "BD",
}
bad = []
for it in mk["items"]:
    got = (it.get("extra") or {}).get("DepartmentEn")
    if expect.get(it["key"]) != got:
        bad.append((it["key"], expect.get(it["key"]), got))
chk(not bad, f"{len(expect)} 条缺失值的分类推荐全部正确" + (f"（不符：{bad}）" if bad else ""))

print()
print("=" * 74)
print("步骤 3  执行逐条补录（含附带字段）")
print("=" * 74)
_snap = {r["id"]: (r["DepartmentName"], r["DepartmentEn"])
         for r in q("SELECT id, DepartmentName, DepartmentEn FROM dim_department")}
before_dim = q("SELECT COUNT(*) FROM dim_department")[0][0]
before_po = q("SELECT COUNT(*) FROM fct_po")[0][0]
before_maxid = q("SELECT MAX(CAST(id AS INTEGER)) FROM dim_department")[0][0]

mappings = [
    {"key": "Marketing-MINI-市场部-Mini", "target": "Marketing-MINI-市场部-MINI"},
    {"key": "Sales-MINI-销售部-Mini", "target": "Sales-MINI-销售部-MINI"},
    {"key": "Sales-销售部_RR", "target": "Sales-销售部"},
    {"key": "Sales-销售部_LB", "target": "Sales-销售部"},
    {"key": "Marketing-Motorrad-市场部-摩托车", "target": "Marketing-Motorrad-市场部-Motorrad"},
    {"key": "Admin_BMW_行政部-BMW", "target": "Admin_BMW_行政部-BMW",
     "extra": {"DepartmentEn": "Admin"}},
    {"key": "Aftersales_BMW_售后部-BMW", "target": "Aftersales_BMW_售后部-BMW",
     "extra": {"DepartmentEn": "Aftersales"}},
    {"key": "BD_DD_BMW_发展部-BMW", "target": "BD_DD_BMW_发展部-BMW",
     "extra": {"DepartmentEn": "BD"}},
    {"key": "HSE-HSE", "target": "HSE-HSE", "extra": {"DepartmentEn": "HSE"}},
    {"key": "Used Car Sales-二手车销售", "target": "Used Car Sales-二手车销售",
     "extra": {"DepartmentEn": "Used Car"}},
    {"key": "Head Office-Business Development-Property-中国办公室-发展部(物业)",
     "target": "", "extra": {"DepartmentEn": "BD"}},
]
# 事实表「部门」列在补录前的全量快照：新语义要求这个列一个字都不能改
_po_before = {r[0]: r[1] for r in q("SELECT 表单编号, 部门 FROM fct_po")}
r = cli.post("/api/backfill-map", json={
    "fact": "fct_po", "fact_col": "部门", "dim": "dim_department",
    "dim_col": "DepartmentName", "mappings": mappings, "default_action": "skip"})
rep = r.get_json()
print("  返回：", json.dumps({k: v for k, v in rep.items() if k != "created_values"},
                              ensure_ascii=False))
chk(rep.get("ok"), "接口返回 ok")
# 新语义：target 一律忽略，11 个缺失值各自按原值新建同名成员
chk(rep.get("created") == 11, f"新建成员数 = {rep.get('created')}（预期 11，全部按原值新建）")
chk(rep.get("merged_keys") == 0 and rep.get("merged_rows") == 0, "零归并（事实表不改写）")
chk(rep.get("auto_id") is True, "id 列自动续号生效")
chk(rep.get("filled") == ["DepartmentEn"], f"filled = {rep.get('filled')}")
chk(rep.get("remaining") == 0, f"剩余缺失行数 = {rep.get('remaining')}（预期 0）")

print()
print("=" * 74)
print("步骤 4  校验写入结果")
print("=" * 74)
after_dim = q("SELECT COUNT(*) FROM dim_department")[0][0]
after_po = q("SELECT COUNT(*) FROM fct_po")[0][0]
chk(after_dim == before_dim + 11, f"dim_department {before_dim} -> {after_dim}（+11）")
chk(after_po == before_po, f"fct_po 行数不变（{before_po} -> {after_po}）")

print("\n  新建的部门成员（含自动 id 与 DepartmentEn）：")
newrows = q("SELECT id, DepartmentName, DepartmentEn FROM dim_department "
            "WHERE CAST(id AS INTEGER) > ? ORDER BY CAST(id AS INTEGER)", (before_maxid,))
for x in newrows:
    print(f"    id={x['id']:<4} DepartmentEn={str(x['DepartmentEn']):<10} {x['DepartmentName']}")
chk(len(newrows) == 11, f"按 id 筛出新成员 {len(newrows)} 条")
# 带分类建议的 6 行需写入 DepartmentEn
_ex_keys = [m["key"] for m in mappings if m.get("extra", {}).get("DepartmentEn")]
_ex_new = [x for x in newrows if x["DepartmentName"] in _ex_keys]
chk(len(_ex_new) == len(_ex_keys) and all(x["DepartmentEn"] for x in _ex_new),
    f"带分类的 {len(_ex_keys)} 个新成员 DepartmentEn 均已填写")
ids = [int(x["id"]) for x in newrows]
chk(ids == list(range(int(before_maxid) + 1, int(before_maxid) + 12)),
    f"id 连续续号 {ids}")

orphan = q("SELECT COUNT(*) FROM fct_po WHERE 部门 IS NOT NULL "
           "AND 部门 NOT IN (SELECT DepartmentName FROM dim_department)")[0][0]
chk(orphan == 0, f"fct_po 部门 孤儿数 = {orphan}（预期 0）")

print("\n  事实表「部门」列全量零改动（逐行比对）：")
_po_after = {r[0]: r[1] for r in q("SELECT 表单编号, 部门 FROM fct_po")}
_po_diff = [k for k in _po_before if _po_before[k] != _po_after.get(k)]
chk(not _po_diff,
    f"fct_po 全部 {len(_po_before)} 行的「部门」值均未变（改动 {len(_po_diff)} 行）")

leftover = q("SELECT COUNT(DISTINCT 部门) FROM fct_po WHERE 部门 IS NOT NULL "
             "AND 部门 NOT IN (SELECT DepartmentName FROM dim_department)")[0][0]
chk(leftover == 0, f"全部处理完毕，剩余 {leftover} 个未处理值")

print("\n  旧数据未被误改（补录前快照全量比对）：")
_now = {r["id"]: (r["DepartmentName"], r["DepartmentEn"])
        for r in q("SELECT id, DepartmentName, DepartmentEn FROM dim_department")}
_changed = [k for k, v in _snap.items() if _now.get(k) != v]
chk(not _changed,
    f"原有 {len(_snap)} 个成员的写法/分类均未被改写（改动 {len(_changed)} 个）")

print()
print("=" * 74)
print(f"结果：通过 {OK} 项，失败 {FAIL} 项")
print("=" * 74)
if not FAIL:
    try:
        if os.path.exists(TEST_DB):
            os.remove(TEST_DB)      # 全部通过则清理副本；有失败则保留便于排查
    except OSError:
        pass                        # 清理失败不影响测试结论
sys.exit(1 if FAIL else 0)
