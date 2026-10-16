# -*- coding: utf-8 -*-
"""测试：default_action=keep 时未指定的值也带上分类；重复执行幂等；切换关系不串台。

注意：全程只操作 procurement.db 的副本，不会改动真实库。
运行：python tests/test_backfill_附带字段2.py
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
TEST_DB = os.path.join(HERE, "_work2.db")
if os.path.exists(TEST_DB):
    os.remove(TEST_DB)
shutil.copy2(os.path.join(ROOT, "procurement.db"), TEST_DB)
A.DB_PATH = TEST_DB
cli = A.app.test_client()
OK = FAIL = 0


def chk(cond, msg):
    global OK, FAIL
    if cond:
        OK += 1; print("  [OK]  " + msg)
    else:
        FAIL += 1; print("  [!!]  " + msg)


def q(sql, args=()):
    con = sqlite3.connect(TEST_DB); con.row_factory = sqlite3.Row
    try:
        return con.execute(sql, args).fetchall()
    finally:
        con.close()


print("=" * 74)
print("A  default_action=keep：本页只给「部分行」填了分类，其余行各自新建同名成员也应带分类")
print("=" * 74)
# 只把 2 行做归并，其余全部按 keep 新建（前端会把带分类的行都发上来）
mk = cli.get("/api/missing-keys", query_string={
    "fact": "fct_po", "fact_col": "部门", "dim": "dim_department",
    "dim_col": "DepartmentName", "page": 1, "size": 200}).get_json()
items = mk["items"]
mappings = []
for it in items:
    ex = (it.get("extra") or {}).get("DepartmentEn")
    if it["key"].startswith("Marketing-MINI") or it["key"].startswith("Sales-MINI"):
        mappings.append({"key": it["key"], "target": ""})          # 归并/同名，不带分类
    elif ex:
        mappings.append({"key": it["key"], "target": "", "extra": {"DepartmentEn": ex}})
    else:
        mappings.append({"key": it["key"], "target": ""})          # 无建议 → 留空
rep = cli.post("/api/backfill-map", json={
    "fact": "fct_po", "fact_col": "部门", "dim": "dim_department",
    "dim_col": "DepartmentName", "mappings": mappings, "default_action": "keep"}).get_json()
print("  返回：", json.dumps({k: v for k, v in rep.items() if k != "created_values"},
                              ensure_ascii=False))
chk(rep["ok"] and rep["created"] == len(mappings), f"按 keep 全部新建（{rep['created']} 个）")
rows = q("SELECT DepartmentName, DepartmentEn FROM dim_department WHERE DepartmentName IN (%s)" %
         ",".join("?" * len(mappings)), tuple(m["key"] for m in mappings))
filled = {r["DepartmentName"]: r["DepartmentEn"] for r in rows}
chk(all(filled.get(m["key"]) for m in mappings if m.get("extra")),
    "带分类的行都写入了 DepartmentEn")
chk(all(not filled.get(m["key"]) for m in mappings if not m.get("extra")),
    "无建议的行保持空（未凭空造分类）")

print()
print("=" * 74)
print("B  再次执行：应无缺失、零改动（幂等）")
print("=" * 74)
before = q("SELECT COUNT(*) FROM dim_department")[0][0]
rep2 = cli.post("/api/backfill-map", json={
    "fact": "fct_po", "fact_col": "部门", "dim": "dim_department",
    "dim_col": "DepartmentName", "mappings": [], "default_action": "keep"}).get_json()
after = q("SELECT COUNT(*) FROM dim_department")[0][0]
print("  返回：", json.dumps(rep2, ensure_ascii=False))
chk(rep2.get("created") == 0 and rep2.get("remaining") == 0, "第二次执行零新建、零剩余")
chk(before == after, f"维度表行数不变（{before} -> {after}）")

print()
print("=" * 74)
print("C  fct_prpo.部门 同样支持（同一维度表、不同事实表）")
print("=" * 74)
mk2 = cli.get("/api/missing-keys", query_string={
    "fact": "fct_prpo", "fact_col": "部门", "dim": "dim_department",
    "dim_col": "DepartmentName", "page": 1, "size": 200}).get_json()
ex_ok = all((it.get("extra") or {}).get("DepartmentEn") for it in mk2["items"] if it["key"] in
            ["Marketing-MINI-市场部-Mini", "Admin_BMW_行政部-BMW", "HSE-HSE"])
chk(ex_ok, f"fct_prpo 的 {len(mk2['items'])} 个缺失值也给出了分类建议")
rep3 = cli.post("/api/backfill-map", json={
    "fact": "fct_prpo", "fact_col": "部门", "dim": "dim_department", "dim_col": "DepartmentName",
    "mappings": [{"key": it["key"], "target": "", "extra": it.get("extra")}
                 for it in mk2["items"]],
    "default_action": "skip"}).get_json()
print("  返回：", json.dumps({k: v for k, v in rep3.items() if k != "created_values"},
                              ensure_ascii=False))
orphan = q("SELECT COUNT(*) FROM fct_prpo WHERE 部门 IS NOT NULL "
           "AND 部门 NOT IN (SELECT DepartmentName FROM dim_department)")[0][0]
chk(orphan == 0, f"fct_prpo 部门 孤儿清零（{orphan}）")

print()
print("=" * 74)
print("D  附带字段 >3 个的维度表（dim_company）不会破坏逻辑")
print("=" * 74)
cols = A.dim_extra_cols(sqlite3.connect(TEST_DB), "dim_company", "Company")
print("  dim_company 附带字段：", cols)
chk("id" not in cols and len(cols) > 3, f"带出 {len(cols)} 个字段且已排除自增 id")

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
