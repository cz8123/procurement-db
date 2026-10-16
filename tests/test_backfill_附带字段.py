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
r = cli.post("/api/backfill-map", json={
    "fact": "fct_po", "fact_col": "部门", "dim": "dim_department",
    "dim_col": "DepartmentName", "mappings": mappings, "default_action": "skip"})
rep = r.get_json()
print("  返回：", json.dumps({k: v for k, v in rep.items() if k != "created_values"},
                              ensure_ascii=False))
chk(rep.get("ok"), "接口返回 ok")
chk(rep.get("created") == 6, f"新建成员数 = {rep.get('created')}（预期 6）")
chk(rep.get("auto_id") is True, "id 列自动续号生效")
chk(rep.get("filled") == ["DepartmentEn"], f"filled = {rep.get('filled')}")
chk(rep.get("remaining") == 0, f"剩余缺失行数 = {rep.get('remaining')}（预期 0）")

print()
print("=" * 74)
print("步骤 4  校验写入结果")
print("=" * 74)
after_dim = q("SELECT COUNT(*) FROM dim_department")[0][0]
after_po = q("SELECT COUNT(*) FROM fct_po")[0][0]
chk(after_dim == before_dim + 6, f"dim_department {before_dim} -> {after_dim}（+6）")
chk(after_po == before_po, f"fct_po 行数不变（{before_po} -> {after_po}）")

print("\n  新建的部门成员（含自动 id 与 DepartmentEn）：")
newrows = q("SELECT id, DepartmentName, DepartmentEn FROM dim_department "
            "WHERE CAST(id AS INTEGER) > ? ORDER BY CAST(id AS INTEGER)", (before_maxid,))
for x in newrows:
    print(f"    id={x['id']:<4} DepartmentEn={str(x['DepartmentEn']):<10} {x['DepartmentName']}")
chk(len(newrows) == 6, f"按 id 筛出新成员 {len(newrows)} 条")
chk(all(x["DepartmentEn"] for x in newrows), "6 个新成员的 DepartmentEn 均已填写（不为空）")
ids = [int(x["id"]) for x in newrows]
chk(ids == list(range(int(before_maxid) + 1, int(before_maxid) + 7)),
    f"id 连续续号 {ids}")

orphan = q("SELECT COUNT(*) FROM fct_po WHERE 部门 IS NOT NULL "
           "AND 部门 NOT IN (SELECT DepartmentName FROM dim_department)")[0][0]
chk(orphan == 0, f"fct_po 部门 孤儿数 = {orphan}（预期 0）")

print("\n  归并是否只改写了事实表、没新建冗余成员：")
for v in ["Marketing-MINI-市场部-MINI", "Sales-MINI-销售部-MINI", "Marketing-Motorrad-市场部-Motorrad"]:
    n = q("SELECT COUNT(*) FROM dim_department WHERE DepartmentName=?", (v,))[0][0]
    c = q("SELECT COUNT(*) FROM fct_po WHERE 部门=?", (v,))[0][0]
    chk(n == 1, f"「{v}」维度成员仍只有 1 条（未被重复新建），现有 {c} 行 PO 引用它")

print("\n  未指定的缺失值是否被正确跳过（default_action=skip）：")
for v in ["Administration-行政部"]:
    pass
leftover = q("SELECT COUNT(DISTINCT 部门) FROM fct_po WHERE 部门 IS NOT NULL "
             "AND 部门 NOT IN (SELECT DepartmentName FROM dim_department)")[0][0]
chk(leftover == 0, f"全部处理完毕，剩余 {leftover} 个未处理值")

print("\n  旧数据未被误改（抽查原成员 id/分类）：")
spot = q("SELECT id, DepartmentName, DepartmentEn FROM dim_department WHERE id IN ('1','78')")
for x in spot:
    print(f"    id={x['id']} {x['DepartmentName']} -> {x['DepartmentEn']}")
chk(len(spot) == 2 and spot[1]["DepartmentEn"] == "Vehicle management", "原有成员未受影响")

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
