# -*- coding: utf-8 -*-
"""映射聚合「打开即现状」回归测试：加载已保存映射 / 新增取值 1:1 追加 / 保存写回。

背景：聚合页简化后，前端不再有「自动整合 / 任务列表」，每次打开都调
/api/agg/scan?saved=1。若该字段已保存过映射，必须以库里的映射为准，
否则一打开页面就等于把手改的结果冲掉；源字段新增的取值要 1:1 追加并标记。

策略：复制真实库到临时文件，重指向 db.DB_PATH 后再 import app，不碰真实库。
"""
import os
import sys
import shutil
import sqlite3

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import db_manager as db  # noqa: E402

COPY = os.path.join(HERE, "_tmp_agg_saved.db")
if os.path.exists(COPY):
    os.remove(COPY)
shutil.copyfile(db.DB_PATH, COPY)
db.DB_PATH = COPY

import app  # noqa: E402

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


TABLE = "fct_prpo"
COL = "Table - 采购子类别"
DIM = "dim_agg_saved_scan_test"
NEW_PK = "__SAVED_SCAN_NEW__"
NEW_RAW = "测试新增取值ZZZ"


def scan(saved=1):
    r = client.get(f"/api/agg/scan?table={TABLE}&col={COL}&refs=0&saved={saved}")
    assert r.status_code == 200, r.data[:300]
    return r.get_json()


def con():
    c = sqlite3.connect(COPY)
    c.row_factory = sqlite3.Row
    return c


print("=== 1. 未保存过映射：给自动整合的初始值 ===")
d0 = scan()
check("接口 ok", d0.get("ok") is True, str(d0)[:200])
check("没有 saved 标记", not d0.get("saved"), str(d0.get("saved")))
check("有取值行", len(d0["rows"]) > 0, str(len(d0["rows"])))
total0 = len(d0["rows"])
rows0 = [{"raw": r["raw"], "agg": r["agg"], "rows": int(r.get("rows") or 0)} for r in d0["rows"]]
# 手动改两行，保存后必须原样回来（不能被自动整合重算掉）
rows0[0]["agg"] = "手工合并值AAA"
rows0[1]["agg"] = "手工合并值AAA"

print("=== 2. 首次保存 = 新建维度表 ===")
r = client.post("/api/agg/build", json={
    "source_table": TABLE, "source_col": COL,
    "name": "保存回归测试", "dim_table": DIM, "value_col": "子类别",
    "rows": rows0, "backup": False})
jb = r.get_json()
check("建表成功", r.status_code == 200 and jb.get("ok"), str(jb)[:300])
task_id = jb.get("task_id")
check("返回 task_id", bool(task_id), str(task_id))

print("=== 3. 再次加载：以库里的映射为准 ===")
d1 = scan()
check("标记 saved", d1.get("saved") is True, str(d1.get("saved")))
check("task_id 一致", d1.get("task_id") == task_id, f'{d1.get("task_id")} vs {task_id}')
check("dim_table / value_col 回传",
      d1.get("dim_table") == DIM and d1.get("value_col") == "子类别",
      f'{d1.get("dim_table")} / {d1.get("value_col")}')
check("取值行数不变", len(d1["rows"]) == total0, f'{len(d1["rows"])} vs {total0}')
hand = [x for x in d1["rows"] if x["raw"] in (rows0[0]["raw"], rows0[1]["raw"])]
check("手工合并值保留（没被自动整合冲掉）",
      len(hand) == 2 and all(x["agg"] == "手工合并值AAA" for x in hand),
      str(hand)[:200])
check("没有新增取值", (d1.get("new_count") or 0) == 0, str(d1.get("new_count")))

print("=== 4. 源字段出现新取值：1:1 追加并标记 ===")
c = con()
try:
    c.execute(f'INSERT INTO {TABLE} ("表单编号","{COL}") VALUES (?,?)', (NEW_PK, NEW_RAW))
    c.commit()
finally:
    c.close()
d2 = scan()
check("new_count = 1", d2.get("new_count") == 1, str(d2.get("new_count")))
new_rows = [x for x in d2["rows"] if x["raw"] == NEW_RAW]
check("新取值出现在列表里", len(new_rows) == 1, str(new_rows))
check("新取值按 1:1、并标记 new",
      bool(new_rows) and new_rows[0]["agg"] == NEW_RAW and new_rows[0].get("new") is True,
      str(new_rows))
check("总数 = 原数 + 1", len(d2["rows"]) == total0 + 1, f'{len(d2["rows"])} vs {total0 + 1}')

print("=== 5. 保存修改：写回同一张表，不新建 ===")
rows2 = [{"raw": x["raw"], "agg": x["agg"], "rows": int(x.get("rows") or 0)} for x in d2["rows"]]
for x in rows2:
    if x["raw"] == NEW_RAW:
        x["agg"] = "手工合并值AAA"
r = client.put(f"/api/agg/task/{task_id}", json={"rows": rows2, "backup": False})
check("保存成功", r.status_code == 200 and r.get_json().get("ok"), r.data[:300])
c = con()
try:
    n = c.execute(f'SELECT COUNT(*) FROM "{DIM}"').fetchone()[0]
    got = c.execute(f'SELECT "子类别" FROM "{DIM}" WHERE "原始值"=?', (NEW_RAW,)).fetchone()
    ntask = c.execute("SELECT COUNT(*) FROM agg_task WHERE source_col=?", (COL,)).fetchone()[0]
finally:
    c.close()
check("维度表行数 = 原数 + 1", n == total0 + 1, f"{n} vs {total0 + 1}")
check("新取值已合并到手工值", got and got[0] == "手工合并值AAA", str(got))
check("没有重复建任务", ntask == 1, str(ntask))

print("=== 6. 清理 ===")
r = client.delete(f"/api/agg/task/{task_id}?backup=0")
check("删除任务", r.status_code == 200 and r.get_json().get("ok"), r.data[:200])
c = con()
try:
    c.execute(f'DELETE FROM {TABLE} WHERE "表单编号"=?', (NEW_PK,))
    c.commit()
    gone = c.execute("SELECT COUNT(*) FROM sqlite_master WHERE name=?", (DIM,)).fetchone()[0]
finally:
    c.close()
check("维度表已删除", gone == 0, str(gone))
if os.path.exists(COPY):
    try:
        os.remove(COPY)
    except OSError:
        pass

print(f"\n结果：{PASS} 通过 / {FAIL} 失败")
sys.exit(1 if FAIL else 0)
