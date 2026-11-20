# -*- coding: utf-8 -*-
"""转换 API 冒烟测试（临时库副本，不污染真实库）"""
import json, os, shutil, sqlite3, sys
sys.path.insert(0, "D:/code/采购数据库建设")
os.chdir("D:/code/采购数据库建设")

TMP = "D:/code/采购数据库建设/_tmp_api_test.db"
if os.path.exists(TMP):
    os.remove(TMP)

# 先 import app（此时会对真实库幂等建好 transform_query 等元数据表），再复制快照
import app as A
shutil.copy("procurement.db", TMP)
A.DB_PATH = TMP

c = A.app.test_client()
ok = fail = 0
_base_con = sqlite3.connect(TMP)
BASE_TQ = _base_con.execute("SELECT COUNT(*) FROM transform_query").fetchone()[0]
_base_con.close()   # 真实库可能已有用户保存的转换，测试只按增量断言

def chk(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1; print(f"  [OK] {name}")
    else:
        fail += 1; print(f"  [XX] {name} {extra}")

print("=== 1. meta ===")
r = c.get("/api/transform/meta")
d = r.get_json()
chk("meta 200", r.status_code == 200)
_tbls = {t["table"] for t in c.get("/api/tables").get_json()}
_srcs = {s["name"] for s in d.get("sources", [])}
# 不写死表数（库结构会变）：断言「已登记的表都能作为转换源」，缺哪个直接报出来
chk("源表覆盖全部已登记表", _tbls <= _srcs,
    f"-> {len(_srcs)} 源 / {len(_tbls)} 表，缺 {sorted(_tbls - _srcs)}")
chk("步骤类型 18 种", len(d.get("ops", [])) == 18, f"-> {len(d.get('ops', []))}")
chk("比较方式>=10", len(d.get("compare", [])) >= 10)

print("\n=== 2. columns ===")
r = c.get("/api/transform/columns?table=fct_prpo")
d = r.get_json()
chk("列信息 35 列", len(d.get("columns", [])) == 35, f"-> {len(d.get('columns', []))}")
r2 = c.get("/api/transform/columns?table=不存在的表")
chk("坏表名 400", r2.status_code == 400, f"-> {r2.status_code}")

print("\n=== 3. preview ===")
steps = [
    {"op": "filter", "column": "支出类型", "cmp": "contains", "value": "OPEX"},
    {"op": "change_type", "column": "含增值税总价", "type": "number"},
    {"op": "group_by", "keys": ["公司"],
     "aggs": [{"column": "含增值税总价", "func": "sum", "alias": "总额"}]},
    {"op": "sort", "items": [{"column": "总额", "desc": True}]},
]
r = c.post("/api/transform/preview", json={"source": "fct_prpo", "steps": steps, "limit": 5})
d = r.get_json()
chk("preview 200", r.status_code == 200, f"-> {r.status_code} {r.get_data(as_text=True)[:200]}")
chk("返回行数<=5", len(d.get("rows", [])) == 5, f"-> {len(d.get('rows', []))}")
chk("总行数=公司数", d.get("total") == 62, f"-> {d.get('total')}")
chk("含 SQL", "WITH" in (d.get("sql") or ""))
chk("列含 公司/总额", [x["name"] for x in d.get("columns", [])] == ["公司", "总额"],
    f"-> {[x['name'] for x in d.get('columns', [])]}")

r = c.post("/api/transform/preview", json={"source": "fct_prpo",
                                          "steps": [{"op": "keep_columns", "columns": ["无此列"]}]})
chk("坏步骤 400", r.status_code == 400)
chk("错误信息含步骤号", "第 1 步" in (r.get_json().get("error") or ""), f"-> {r.get_json()}")

print("\n=== 4. upto 分步预览 ===")
for k in range(0, 5):
    r = c.post("/api/transform/preview", json={"source": "fct_prpo", "steps": steps, "upto": k, "limit": 1})
    d = r.get_json()
    chk(f"upto={k} 可预览 (列={len(d.get('columns', []))})", r.status_code == 200)

print("\n=== 5. 保存 / 列表 / 刷新 / 删除 ===")
r = c.post("/api/transform/queries", json={
    "name": "vw_test_tf", "label": "测试视图", "source_table": "fct_prpo",
    "steps": steps, "output_mode": "view", "apply": True})
d = r.get_json()
chk("新建视图 200", r.status_code == 200, f"-> {r.status_code} {d}")
chk("返回行数 62", d.get("rows") == 62, f"-> {d.get('rows')}")

lst = c.get("/api/transform/queries").get_json()
mine = [q for q in lst if q["name"] == "vw_test_tf"]
chk("列表含新查询", len(mine) == 1)
chk("列出 exists=True", mine and mine[0]["exists"] is True, f"-> {mine[0] if mine else None}")
chk("列出 step_count=4", mine and mine[0]["step_count"] == 4, f"-> {mine[0]['step_count'] if mine else None}")
qid_ = mine[0]["id"] if mine else None

tables = c.get("/api/tables").get_json()
tv = [t for t in tables if t["table"] == "vw_test_tf"]
chk("视图出现在 /api/tables", len(tv) == 1, f"-> {len(tv)}")
chk("视图标记 readonly", tv and tv[0].get("readonly") is True)
chk("视图 kind=view", tv and tv[0]["kind"] == "view")

r = c.get("/api/tables/vw_test_tf/rows?size=5")
chk("可浏览视图数据", r.status_code == 200 and len(r.get_json().get("rows", [])) == 5,
    f"-> {r.status_code}")

r = c.post("/api/tables/vw_test_tf/rows", json={"公司": "x"})
chk("视图拒绝新增(400)", r.status_code == 400, f"-> {r.status_code}")
chk("拒绝理由提示只读", "只读" in (r.get_json().get("error") or ""), f"-> {r.get_json()}")

r = c.post(f"/api/transform/queries/{qid_}/refresh")
chk("刷新 200", r.status_code == 200 and r.get_json().get("rows") == 62, f"-> {r.get_json()}")

r = c.put(f"/api/transform/queries/{qid_}", json={"label": "改名测试", "apply": False})
chk("更新(不应用) 200", r.status_code == 200, f"-> {r.get_json()}")

r = c.post("/api/transform/queries", json={"name": "vw_test_tf", "source_table": "fct_prpo", "steps": []})
chk("重名拒绝 400", r.status_code == 400, f"-> {r.status_code}")

print("\n=== 6. 物化表模式 ===")
r = c.post("/api/transform/queries", json={
    "name": "t_test_tf", "label": "测试物化", "source_table": "fct_prpo",
    "steps": [{"op": "keep_columns", "columns": ["表单编号"]}, {"op": "limit", "count": 50}],
    "output_mode": "table", "apply": True})
d = r.get_json()
chk("物化表 200", r.status_code == 200, f"-> {d}")
chk("物化 50 行", d.get("rows") == 50, f"-> {d.get('rows')}")
lst = c.get("/api/transform/queries").get_json()
mid = [q for q in lst if q["name"] == "t_test_tf"][0]["id"]
r = c.delete(f"/api/transform/queries/{mid}?drop=1")
chk("删除物化表 200", r.status_code == 200)
import sqlite3
con = sqlite3.connect(TMP)
chk("物化表已物理删除", con.execute(
    "SELECT COUNT(*) FROM sqlite_master WHERE name='t_test_tf'").fetchone()[0] == 0)
con.close()

print("\n=== 7. 删除视图 ===")
r = c.delete(f"/api/transform/queries/{qid_}?drop=1")
chk("删除视图 200", r.status_code == 200)
con = sqlite3.connect(TMP)
chk("视图已删除", con.execute(
    "SELECT COUNT(*) FROM sqlite_master WHERE name='vw_test_tf'").fetchone()[0] == 0)
chk("记录已删除（回到基线）",
    con.execute("SELECT COUNT(*) FROM transform_query").fetchone()[0] == BASE_TQ)
con.close()

print("\n=== 8. 名称留空自动派生（点一下就能存） ===")
r = c.post("/api/transform/queries", json={
    "source_table": "fct_prpo", "steps": [], "output_mode": "view", "apply": True})
d = r.get_json()
chk("空名不报错(200)", r.status_code == 200, f"-> {r.status_code} {d}")
_auto = d.get("name") or ""
chk("自动名以 vw_prpo 开头", _auto.startswith("vw_prpo"), f"-> {_auto}")
chk("自动名与源表同基名（去 fct_ 前缀）", "prpo" in _auto, f"-> {_auto}")
chk("提示含自动名", _auto and _auto in (d.get("message") or ""), f"-> {d.get('message')}")
auto1 = d.get("name")

r = c.post("/api/transform/queries", json={
    "source_table": "fct_prpo", "steps": [], "output_mode": "view", "apply": False})
d = r.get_json()
_auto2 = d.get("name") or ""
chk("重名自动换一个名", _auto2.startswith("vw_prpo") and _auto2 != _auto,
    f"-> {_auto2} (前一个 {_auto})")
auto2 = d.get("name")

r = c.post("/api/transform/queries", json={
    "source_table": "fct_prpo", "steps": [], "output_mode": "table", "apply": False})
d = r.get_json()
chk("表模式前缀 tbl_", (d.get("name") or "").startswith("tbl_prpo"), f"-> {d.get('name')}")
auto3 = d.get("name")

r = c.post("/api/transform/queries", json={
    "source_table": "fct_po", "steps": [], "output_mode": "view", "apply": False})
chk("去源表前缀 fct_po→vw_po", (r.get_json().get("name") or "").startswith("vw_po"),
    f"-> {r.get_json().get('name')}")
auto4 = r.get_json().get("name")

con = sqlite3.connect(TMP)
got = [x[0] for x in con.execute("SELECT name FROM transform_query ORDER BY id")]
chk("多出 4 条自动命名记录",
    len(got) == BASE_TQ + 4 and got[-4:] == [auto1, auto2, auto3, auto4], f"-> {got}")
chk("自动名视图已落地", con.execute(
    "SELECT COUNT(*) FROM sqlite_master WHERE name=? AND type='view'", (auto1,)).fetchone()[0] == 1)
con.close()

_lst = c.get("/api/transform/queries").get_json()
_lst = _lst if isinstance(_lst, list) else _lst.get("queries", [])
for nm in (auto1, auto2, auto3, auto4):
    _hit = [q for q in _lst if q["name"] == nm]
    if _hit:
        c.delete(f"/api/transform/queries/{_hit[0]['id']}?drop=1")

print("\n=== 9. 改类型的报错文案 ===")
def _err(steps):
    r = c.post("/api/transform/preview", json={"source": "fct_prpo", "steps": steps, "limit": 5})
    return r.status_code, (r.get_json() or {}).get("error", "")

st, msg = _err([{"op": "remove_columns", "columns": ["公司"]},
                {"op": "change_type", "column": "公司", "type": "date"}])
chk("列已删除后改类型 400", st == 400, f"-> {st}")
chk("文案=第2步（更改类型）+列不存在",
    "第 2 步（更改类型）" in msg and "「公司」在当前结果中不存在" in msg, msg)

st, msg = _err([{"op": "change_type", "column": "公司", "type": "date", "date_order": "ydm"}])
chk("非法日期顺序 400", st == 400, f"-> {st}")
chk("文案含不支持的日期顺序", "不支持的日期顺序：ydm" in msg, msg)

st, msg = _err([{"op": "change_type", "column": "公司", "type": "float"}])
chk("非法目标类型 400", st == 400, f"-> {st}")
chk("文案含不支持的目标类型", "不支持的目标类型：float" in msg, msg)

st, msg = _err([{"op": "change_type", "type": "text"}])
chk("缺列参数 400", st == 400, f"-> {st}")
chk("文案含列「None」不存在", "列「None」在当前结果中不存在" in msg, msg)

print(f"\n===== {ok} 通过 / {fail} 失败 =====")
os.remove(TMP)
sys.exit(1 if fail else 0)
