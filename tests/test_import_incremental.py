# -*- coding: utf-8 -*-
"""增量导入（新增/一致/冲突）+ 冲突覆盖 + 聚合表附加字段 + 新增取值提醒 回归测试。

策略同其他测试：复制真实库到临时文件，把 db.DB_PATH 重指向副本后再 import app，
整个过程**不触碰真实 procurement.db**。
"""
import io
import csv
import json
import os
import shutil
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import db_manager as db  # noqa: E402

COPY = os.path.join(HERE, "_tmp_import.db")
if os.path.exists(COPY):
    os.remove(COPY)
shutil.copyfile(db.DB_PATH, COPY)
db.DB_PATH = COPY

import app  # noqa: E402
import aggmap  # noqa: E402

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


TABLE = "fct_preapproval"
PK = "参考编号"


def con():
    c = sqlite3.connect(COPY)
    c.row_factory = sqlite3.Row
    return c


_c = con()
COLS = [r["name"] for r in _c.execute(f"PRAGMA table_info({TABLE})")]
BASE = [dict(r) for r in _c.execute(
    f"SELECT * FROM {TABLE} ORDER BY {db.qid(PK)} LIMIT 2")]
_c.close()


def make_csv(rows):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(COLS)
    for d in rows:
        w.writerow(["" if d.get(c) is None else d.get(c) for c in COLS])
    return buf.getvalue()


def post(path, text, extra=None):
    form = {"table": TABLE, "file": (io.BytesIO(text.encode("utf-8")), "t.csv")}
    form.update(extra or {})
    return client.post(path, data=form, content_type="multipart/form-data")


def preview(text):
    return post("/api/import-preview", text).get_json()


# ---------------------------------------------------------------- 列表头追加列
def with_extra_col(d, name, val):
    e = dict(d)
    e[name] = val
    return e


print("== A. 增量判定：新增 / 一致 / 冲突 ==")
a = dict(BASE[0])
b = dict(BASE[1]); b["状态"] = "异常-测试"
c = dict(BASE[1]); c[PK] = "TEST-NEW-001"
rep = preview(make_csv([a, b, c]))
plan = rep["plan"]
check("纯新增 1 条", plan["new"] == 1, plan["new"])
check("与库一致 1 条", plan["same"] == 1, plan["same"])
check("冲突 1 条", plan["conflict"] == 1, plan["conflict"])
check("冲突字段统计到「状态」",
      any(f["col"] == "状态" and f["rows"] == 1 for f in plan["conflict_fields"]),
      plan["conflict_fields"])
check("冲突明细给出旧值与新值",
      plan["conflicts"] and plan["conflicts"][0]["fields"][0]["old"] == BASE[1]["状态"]
      and plan["conflicts"][0]["fields"][0]["new"] == "异常-测试",
      plan["conflicts"][:1])
check("对比范围覆盖文件提供的全部列",
      sorted(plan["updatable_cols"]) == sorted(COLS), plan["updatable_cols"][:5])
check("预览不回传冲突主键列表", "conflict_keys" not in plan)

print("\n== B. 冲突处理：skip / overwrite / fields ==")
r = post("/api/import", make_csv([a, b, c]), {"conflict_mode": "skip"}).get_json()
check("skip：只插入新增行", r["inserted"] == 1, r["inserted"])
check("skip：不覆盖冲突行", r["updated"] == 0, r["updated"])
_c = con()
old = _c.execute(f"SELECT 状态 FROM {TABLE} WHERE {db.qid(PK)}=?",
                 (b[PK],)).fetchone()[0]
check("skip：库里仍是原值", old == BASE[1]["状态"], old)
_c.close()

# 第二次导入同一个文件：c 已在库中且一致
r = post("/api/import", make_csv([a, b, c]), {"conflict_mode": "overwrite"}).get_json()
check("overwrite：覆盖冲突行", r["updated"] == 1, r["updated"])
check("overwrite：已无新增", r["inserted"] == 0, r["inserted"])
_c = con()
new = _c.execute(f"SELECT 状态 FROM {TABLE} WHERE {db.qid(PK)}=?",
                 (b[PK],)).fetchone()[0]
check("overwrite：库里改成新值", new == "异常-测试", new)
_c.close()

# 字段级：两个字段都变了，只允许盖「状态」
d = dict(BASE[1]); d["状态"] = "状态X"; d["项目名称"] = "项目X"
rep = preview(make_csv([d]))
check("字段级：检出 2 个字段变化", rep["plan"]["conflict"] == 1
      and rep["plan"]["conflicts"][0]["n_fields"] == 2,
      rep["plan"]["conflicts"][:1])
r = post("/api/import", make_csv([d]),
         {"conflict_mode": "fields", "conflict_fields": "状态"}).get_json()
check("字段级：覆盖 1 行", r["updated"] == 1, r["updated"])
_c = con()
row = _c.execute(f"SELECT 状态, 项目名称 FROM {TABLE} WHERE {db.qid(PK)}=?",
                 (d[PK],)).fetchone()
check("字段级：勾选的「状态」被覆盖", row[0] == "状态X", row[0])
check("字段级：没勾的「项目名称」保留原值", row[1] == BASE[1]["项目名称"], row[1])
_c.close()

r = post("/api/import", make_csv([d]),
         {"conflict_mode": "fields", "conflict_fields": "不存在的列"}).get_json()
check("字段级：非法字段名不报错且不改数据", r["ok"] and r["updated"] == 0, r.get("updated"))

print("\n== C. 文件多出的列：提示与「加到事实表」 ==")
e = with_extra_col(dict(BASE[0]), "新列ABC", "值1")
buf = io.StringIO()
w = csv.writer(buf)
w.writerow(COLS + ["新列ABC"])
w.writerow([e.get(x) for x in COLS] + ["值1"])
rep = preview(buf.getvalue())
check("识别出库里没有的列", rep["plan"]["file_cols_not_in_db"] == ["新列ABC"],
      rep["plan"]["file_cols_not_in_db"])
r = post("/api/import", buf.getvalue(), {"add_missing_cols": "1"}).get_json()
check("按选择新增列", r["added_cols"] == ["新列ABC"], r["added_cols"])
_c = con()
cols_now = [x[1] for x in _c.execute(f"PRAGMA table_info({TABLE})")]
check("列已加到事实表", "新列ABC" in cols_now)
v = _c.execute(f'SELECT "新列ABC" FROM {TABLE} WHERE {db.qid(PK)}=?',
               (e[PK],)).fetchone()
check("新列的值已写入", v and v[0] == "值1", v[0] if v else None)
_c.close()
# 不勾选时不应加列
buf2 = io.StringIO(); w2 = csv.writer(buf2)
w2.writerow(COLS + ["另一个列"])
w2.writerow([BASE[0].get(x) for x in COLS] + ["v"])
r = post("/api/import", buf2.getvalue()).get_json()
check("不勾选则不加列", r["added_cols"] == [], r["added_cols"])
_c = con()
cols_now2 = [x[1] for x in _c.execute(f"PRAGMA table_info({TABLE})")]
check("未加列到库", "另一个列" not in cols_now2)
_c.close()

print("\n== D. 聚合维度表：附加字段 ==")
rows = aggmap.auto_map(con(), "fct_prpo", "部门")["rows"]
_c = con()
res = aggmap.build(_c, "部门聚合(测试)", "fct_prpo", "部门",
                   "dim_agg_t_dept", "部门名称", rows,
                   relations=[], extra_cols=[{"name": "DepartmentEn", "type": "text"},
                                             {"name": "成本中心", "type": "text"}])
_c.commit()
cols = [r[1] for r in _c.execute("PRAGMA table_info(dim_agg_t_dept)")]
check("建表带出附加字段", "DepartmentEn" in cols and "成本中心" in cols, cols)
check("返回结构含 extra_cols", len(res["extra_cols"]) == 2, res["extra_cols"])
check("固定两列仍在", all(x in cols for x in ("原始值", "部门名称")))
check("不再建 源行数/写法数 列", not any(x in cols for x in ("源行数", "写法数")), cols)

task = aggmap.get_task(_c, res["task_id"])
aggmap.set_extra_values(_c, task, [{"raw": rows[0]["raw"],
                                    "extra": {"DepartmentEn": "Admin", "成本中心": "CC1"}}])
_c.commit()
check("附加字段可写值",
      _c.execute('SELECT DepartmentEn FROM dim_agg_t_dept WHERE 原始值=?',
                 (rows[0]["raw"],)).fetchone()[0] == "Admin")

added = aggmap.add_columns(_c, "dim_agg_t_dept", [{"name": "City", "type": "text"}])
_c.commit()
check("可给已有表加字段", added == [{"name": "City", "type": "TEXT"}], added)
check("重复加列被跳过", aggmap.add_columns(_c, "dim_agg_t_dept", [{"name": "City"}]) == [])

# 改映射（整体重写）后附加字段的值必须保留
new_rows = [{"raw": r["raw"], "agg": "统一部门" if i < 3 else r["agg"], "rows": r["rows"]}
            for i, r in enumerate(rows)]
aggmap.rewrite_mapping(_c, aggmap.get_task(_c, res["task_id"]), new_rows)
_c.commit()
kept = _c.execute('SELECT DepartmentEn FROM dim_agg_t_dept WHERE 原始值=?',
                  (rows[0]["raw"],)).fetchone()
check("重写映射后附加字段值保留", kept and kept[0] == "Admin", kept[0] if kept else None)
check("重写映射后聚合值已更新",
      _c.execute('SELECT COUNT(*) FROM dim_agg_t_dept WHERE 部门名称=?',
                 ("统一部门",)).fetchone()[0] == 3)
check("重写不丢行", _c.execute("SELECT COUNT(*) FROM dim_agg_t_dept").fetchone()[0] == len(rows))

# 保留列名不能当附加字段
try:
    aggmap.add_columns(_c, "dim_agg_t_dept", [{"name": "原始值"}])
    check("保留列名被拒绝", False)
except ValueError:
    check("保留列名被拒绝", True)
try:
    aggmap.add_columns(_c, "dim_agg_t_dept", [{"name": "x y"}])
    check("非法列名被拒绝", False)
except ValueError:
    check("非法列名被拒绝", True)
_c.close()

print("\n== E. 新增取值提醒（pending_values） ==")
_c = con()
_c.execute("INSERT INTO fct_prpo (表单编号, 部门) VALUES ('TEST-PEND-1','全新部门ZZZ')")
_c.commit()
pend = aggmap.pending_values(_c)
mine = [x for x in pend if x["dim_table"] == "dim_agg_t_dept"]
check("检出聚合表的新取值", mine and mine[0]["new_count"] == 1, mine[:1])
check("给出样例值", mine and "全新部门ZZZ" in mine[0]["samples"], mine[:1])
check("给出源表与字段", mine and mine[0]["source_table"] == "fct_prpo"
      and mine[0]["source_col"] == "部门")

t = aggmap.get_task(_c, res["task_id"])
aggmap.refresh_task(_c, t)
_c.commit()
pend2 = [x for x in aggmap.pending_values(_c) if x["dim_table"] == "dim_agg_t_dept"]
check("补入后不再提示", pend2 == [], pend2)
check("补入的取值已进维度表",
      _c.execute("SELECT COUNT(*) FROM dim_agg_t_dept WHERE 原始值='全新部门ZZZ'").fetchone()[0] == 1)
_c.close()

print("\n== F. 类型转换风险（type_risks） ==")
_c = con()
steps = json.dumps([{"op": "change_type", "column": "说明", "type": "number"}])
_c.execute("INSERT INTO transform_query(name,label,source_table,steps,output_mode,enabled,"
           "created_at,updated_at) VALUES ('vw_t_seq','vw_t_seq','fct_prpo',?,'view',1,'','')",
           (steps,))
_c.commit()
_c.close()


def mkrep(vals):
    cols = [r[1] for r in con().execute("PRAGMA table_info(fct_prpo)")]
    out = []
    for i, v in enumerate(vals):
        r = [None] * len(cols)
        r[cols.index("表单编号")] = f"T-{i}"
        r[cols.index("说明")] = v
        out.append(tuple(r))
    return {"db_columns": cols, "valid_rows": out}


_c = con()
risks = app.type_risks(_c, "fct_prpo", mkrep(["正常文本? no", "1", "2"]))
check("非数字值被识别为风险", risks and risks[0]["failed"] == 1, risks[:1])
check("风险说明含列名", risks and "说明" in risks[0]["message"], risks[0]["message"] if risks else "")
check("风险带样例值", risks and risks[0]["samples"][0]["value"] == "正常文本? no",
      risks[0]["samples"][:1] if risks else "")
ok = app.type_risks(_c, "fct_prpo", mkrep(["1", "2", "3"]))
check("全部可转时无风险", ok == [], ok)
_c.close()

print("\n" + "=" * 70)
print(f"结果：通过 {PASS} 项，失败 {FAIL} 项")
print("=" * 70)
try:
    os.remove(COPY)
except OSError:
    pass          # 库连接未完全释放时 Windows 会占用文件，留到下次覆盖即可
sys.exit(1 if FAIL else 0)
