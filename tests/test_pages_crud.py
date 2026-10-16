# -*- coding: utf-8 -*-
"""映射关系 / 数据质量 两个独立页面的 CRUD + 规则评估回归测试。

策略：复制真实库到临时文件，把 db.DB_PATH 重指向副本后再 import app，
因此整个过程**不触碰真实 procurement.db**。测试结束删除副本。
"""
import os
import sys
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import db_manager as db  # noqa: E402

COPY = os.path.join(HERE, "_tmp_pages_crud.db")
if os.path.exists(COPY):
    os.remove(COPY)
shutil.copyfile(db.DB_PATH, COPY)
# 关键：在 import app 之前重指向副本，使 app.DB_PATH 捕获副本路径
db.DB_PATH = COPY

import app  # noqa: E402  (导入即触发副本上的 ensure_meta)

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


def jget(path):
    r = client.get(path)
    assert r.status_code == 200, f"{path} -> {r.status_code}"
    return r.get_json()


def jpost(path, payload):
    return client.post(path, json=payload)


def jput(path, payload):
    return client.put(path, json=payload)


def jdel(path):
    return client.delete(path)


print("== 映射关系 CRUD ==")
# 1) 列表条数以真实库播种结果为准（不写死，避免因增删关系导致测试脆弱）
m = jget("/api/mappings")
BASE = len(m["mappings"])
check("映射列表非空（继承自真实库播种）", BASE > 0, f"实际 {BASE}")
check("映射项含统计字段", all("rate" in x and "status" in x for x in m["mappings"]))

# 2) 表+列下拉接口
mt = jget("/api/mapping-tables")
check("mapping-tables 返回表列表", len(mt["tables"]) >= 15)
check("mapping-tables 每项含 columns", all("columns" in t for t in mt["tables"]))

# 3) 创建：非法列应 400
r = jpost("/api/mappings", {"source_table": "fct_po", "source_col": "不存在的列",
                            "target_table": "dim_company", "target_col": "Company"})
check("创建非法列被拒(400)", r.status_code == 400, f"-> {r.status_code}")

# 4) 创建：合法映射应成功，计数 +1，并持久化 cardinality
r = jpost("/api/mappings", {"source_table": "fct_po", "source_col": "公司",
                            "target_table": "dim_company", "target_col": "Company",
                            "kind": "dim", "cardinality": "1:1", "note": "回归测试新增"})
check("创建合法映射成功(200)", r.status_code == 200, f"-> {r.status_code}")
new_id = r.get_json().get("id")
m2 = jget("/api/mappings")
check("创建后映射数 = 基线+1", len(m2["mappings"]) == BASE + 1, f"实际 {len(m2['mappings'])}")
check("映射项均含 cardinality 键", all("cardinality" in x for x in m2["mappings"]))
_new = next(x for x in m2["mappings"] if x["id"] == new_id)
check("新建映射 cardinality 持久化=1:1", _new["cardinality"] == "1:1", str(_new.get("cardinality")))
# 更新 cardinality -> *:1
jput(f"/api/mappings/{new_id}", {"cardinality": "*:1"})
m2b = jget("/api/mappings")
_new2 = next(x for x in m2b["mappings"] if x["id"] == new_id)
check("更新 cardinality 生效=*:1", _new2["cardinality"] == "*:1", str(_new2.get("cardinality")))

# 5) 更新：禁用该映射 -> data-quality 只剩基线条（disabled 不计入）
r = jput(f"/api/mappings/{new_id}", {"enabled": 0})
check("更新映射成功(200)", r.status_code == 200)
dq = jget("/api/data-quality")
check("禁用后 data-quality 回到基线条数", len(dq["relationships"]) == BASE,
      f"实际 {len(dq['relationships'])}")
# 恢复启用
jput(f"/api/mappings/{new_id}", {"enabled": 1})
dq2 = jget("/api/data-quality")
check("恢复后 data-quality 回到基线+1", len(dq2["relationships"]) == BASE + 1,
      f"实际 {len(dq2['relationships'])}")

# 6) 质量覆盖：忽略 mid=2 (fct_po.部门) -> advice=not_needed & ignored=True
r = jput("/api/mappings/2", {"ignored": 1, "note": "测试忽略"})
check("设置忽略成功(200)", r.status_code == 200)
dq3 = jget("/api/data-quality")
rel2 = next(x for x in dq3["relationships"] if x["mid"] == 2)
check("被忽略关系 advice=not_needed", rel2["advice"] == "not_needed", rel2["advice"])
check("被忽略关系 ignored=True", rel2["ignored"] == 1)
# 复位
jput("/api/mappings/2", {"ignored": 0, "note": ""})

# 7) 删除新建的映射，回到基线
r = jdel(f"/api/mappings/{new_id}")
check("删除映射成功(200)", r.status_code == 200)
m3 = jget("/api/mappings")
check("删除后回到基线条数", len(m3["mappings"]) == BASE, f"实际 {len(m3['mappings'])}")

print("== 自定义质量规则 CRUD + 评估 ==")
# 8) 初始无规则
qr = jget("/api/quality-rules")
check("初始质量规则为空", len(qr["rules"]) == 0)

# 9) 创建非空检查（选一个必有空值的列不易确定，仅验证结构正确）
r = jpost("/api/quality-rules", {"target_table": "fct_po", "check_type": "not_null",
                                 "check_col": "表单编号", "severity": "bad", "note": "测试"})
check("创建规则成功(200)", r.status_code == 200, f"-> {r.status_code}")
rid = r.get_json().get("id")
qr2 = jget("/api/quality-rules")
check("创建后规则数=1", len(qr2["rules"]) == 1)

# 10) 评估接口返回结构化结果
ev = jget("/api/quality-rules/evaluate")
check("评估返回 1 条结果", len(ev["results"]) == 1, f"实际 {len(ev['results'])}")
res0 = ev["results"][0]
check("评估结果含 passed/count/detail",
      "passed" in res0 and "count" in res0 and "detail" in res0)
check("非空检查 表单编号 应通过", res0["passed"] is True, str(res0))

# 11) 更新规则 severity -> warn
r = jput(f"/api/quality-rules/{rid}", {"severity": "warn"})
check("更新规则成功(200)", r.status_code == 200)
qr3 = jget("/api/quality-rules")
check("更新后 severity=warn", qr3["rules"][0]["severity"] == "warn")

# 12) 删除规则
r = jdel(f"/api/quality-rules/{rid}")
check("删除规则成功(200)", r.status_code == 200)
qr4 = jget("/api/quality-rules")
check("删除后规则为空", len(qr4["rules"]) == 0)

print("== 补录链路（重构后回归） ==")
# 13) 选一个孤儿>0 的维度关系，做 skip 默认的补录（不改数据，只验证接口仍可用）
dq4 = jget("/api/data-quality")
dim_rel = next((x for x in dq4["relationships"]
               if x["target_is_dim"] and x["orphan"] > 0), None)
check("存在可补录的维度关系", dim_rel is not None)
if dim_rel:
    r = jpost("/api/backfill-map", {
        "fact": dim_rel["fact"], "fact_col": dim_rel["fact_col"],
        "dim": dim_rel["dim"], "dim_col": dim_rel["dim_col"],
        "mappings": [], "default_action": "skip", "extra": {},
    })
    check("补录接口(skip)成功(200)", r.status_code == 200, f"-> {r.status_code}")

print("== 模型视图（/api/model-view + /api/model-layout） ==")
mv = jget("/api/model-view")
check("model-view 返回 15 张表", len(mv["tables"]) == 15, f"实际 {len(mv['tables'])}")
check("每张表含 columns/col_count/pk/row_count",
      all(("columns" in t and "col_count" in t and "row_count" in t and "kind" in t)
          for t in mv["tables"]))
check("columns 每项含 name/type/pk",
      all(all(("name" in c and "type" in c and "pk" in c and "amount" in c)
              for c in t["columns"]) for t in mv["tables"]))
check("col_count 与 columns 长度一致",
      all(t["col_count"] == len(t["columns"]) for t in mv["tables"]))
check("每张表都能识别出主键字段",
      all(any(c["pk"] for c in t["columns"]) for t in mv["tables"]))
check("model-view 关系数与映射列表一致",
      len(mv["relationships"]) == len(jget("/api/mappings")["mappings"]),
      f"实际 {len(mv['relationships'])}")
check("每条关系含 advice/advice_reason/target_is_dim/cardinality（供模型视图弹窗与补录入口）",
      all(("advice" in r and "advice_reason" in r and "target_is_dim" in r and "cardinality" in r)
          for r in mv["relationships"]))

# 关键：每条关系的两端字段必须真实存在于对应表的字段列表里（否则模型视图画不出连线）
colset = {t["table"]: {c["name"] for c in t["columns"]} for t in mv["tables"]}
_bad = []
for r in mv["relationships"]:
    if (r["source_col"] not in colset.get(r["source_table"], set())
            or r["target_col"] not in colset.get(r["target_table"], set())):
        _bad.append(r["id"])
    if r["source_table"] not in colset or r["target_table"] not in colset:
        _bad.append(("表缺失", r["id"]))
check("所有关系两端字段都能在表结构中定位（可画线）", not _bad, f"问题关系 {_bad}")

# 命名迁移：供应商列表 = 事实表（dim_supplier -> fct_supplier）
_tnames = {t["table"] for t in mv["tables"]}
check("已无 dim_supplier", "dim_supplier" not in _tnames, str(sorted(_tnames)))
check("存在 fct_supplier", "fct_supplier" in _tnames)
_sup = next((t for t in mv["tables"] if t["table"] == "fct_supplier"), {})
check("fct_supplier kind=fact", _sup.get("kind") == "fact", str(_sup.get("kind")))
check("fct_supplier 行数>0（数据随重命名保留）", (_sup.get("row_count") or 0) > 0,
      str(_sup.get("row_count")))
check("无任何关系引用 dim_supplier",
      not any(r["source_table"] == "dim_supplier" or r["target_table"] == "dim_supplier"
              for r in mv["relationships"]))
check("fct_supplier 在 /api/tables 中且 kind=fact",
      any(t["table"] == "fct_supplier" and t["kind"] == "fact" for t in jget("/api/tables")))

# 位置保存与读取
r = jpost("/api/model-layout", {"positions": {"dim_company": {"x": 123, "y": 45}}})
check("保存布局成功(200)", r.status_code == 200, f"-> {r.status_code}")
check("保存条数=1", r.get_json().get("saved") == 1, str(r.get_json()))
mv2 = jget("/api/model-view")
check("布局已持久化(x=123)",
      (mv2["layout"].get("dim_company") or {}).get("x") == 123,
      str(mv2["layout"].get("dim_company")))
# 覆盖同一张表应更新而非重复插入
jpost("/api/model-layout", {"positions": {"dim_company": {"x": 200, "y": 60}}})
mv2b = jget("/api/model-view")
check("重复保存为更新(x=200)",
      (mv2b["layout"].get("dim_company") or {}).get("x") == 200,
      str(mv2b["layout"].get("dim_company")))
# 非法表名 / NaN 坐标必须被忽略
r = jpost("/api/model-layout", {"positions": {"no_such_table": {"x": 1, "y": 2},
                                              "fct_po": {"x": float("nan"), "y": 3}}})
check("非法表名/NaN 被忽略(saved=0)", r.get_json().get("saved") == 0, str(r.get_json()))
# 重置（同时可带入自动布局位置）
r = jpost("/api/model-layout", {"reset": True, "positions": {"fct_po": {"x": 10, "y": 20}}})
check("重置布局成功(200)", r.status_code == 200)
mv3 = jget("/api/model-view")
check("重置后仅保留随重置带入的位置",
      len(mv3["layout"]) == 1 and "fct_po" in mv3["layout"], str(mv3["layout"]))
# 收尾：清空，避免影响真实库之外的副本状态
jpost("/api/model-layout", {"reset": True})
mv4 = jget("/api/model-view")
check("清空后布局为空", len(mv4["layout"]) == 0, str(mv4["layout"]))

print(f"\n结果：通过 {PASS} / 失败 {FAIL}")
# 清理副本
try:
    os.remove(COPY)
except OSError:
    pass
sys.exit(1 if FAIL else 0)
