# -*- coding: utf-8 -*-
"""删除表（/api/tables/<t>/drop-info 与 DELETE）回归测试。

要覆盖的「安全边界」：
  1. 元数据表 / 视图 / 被转换查询引用的表 → 一律拒绝，不能靠接口删掉
  2. 表名回填不一致 → 拒绝（防误点）
  3. 删除时连带清理：映射关系、模型布局、质量规则、独立维度表登记、聚合任务
  4. 内存登记（KNOWN_TABLES / TABLE_LABELS / IMPORTED_TABLES / AGG_TABLES）同步撤销
  5. backup=1 生成备份文件，backup=0 不生成

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

COPY = os.path.join(HERE, "_tmp_drop_table.db")
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


def q(sql, args=()):
    con = sqlite3.connect(COPY)
    con.row_factory = sqlite3.Row
    try:
        return con.execute(sql, args).fetchall()
    finally:
        con.close()


def ex(sql, args=()):
    con = sqlite3.connect(COPY)
    try:
        cur = con.execute(sql, args)
        con.commit()
        return cur.rowcount
    finally:
        con.close()


TMP = "dim_tmp_drop"
TMP2 = "dim_tmp_drop2"
AGG = "dim_agg_drop_test"


def make_fixtures():
    ex(f"DROP TABLE IF EXISTS {TMP}")
    ex(f"CREATE TABLE {TMP} (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, note TEXT)")
    ex(f"INSERT INTO {TMP}(name,note) VALUES ('a','x'),('b','y'),('c','z')")
    ex("INSERT INTO imported_table(table_name,label,descr,pk_col,source_file,row_count,created_at) "
       "VALUES (?,?,?,?,?,?,datetime('now'))", (TMP, "临时表", "测试用", "id", "t.csv", 3))
    ex("INSERT INTO map_config(source_table,source_col,target_table,target_col,kind,enabled) "
       "VALUES (?,?,?,?,?,1)", ("fct_prpo", "临时列X", TMP, "name", "dim"))
    ex("INSERT INTO map_layout(table_name,x,y,height) VALUES (?,10,20,100)", (TMP,))
    ex("INSERT INTO quality_rules(target_table,check_type,check_col,severity,note,enabled) "
       "VALUES (?,?,?,?,?,1)", (TMP, "not_null", "name", "warn", "临时规则"))
    ex(f"DROP TABLE IF EXISTS {AGG}")
    ex(f"CREATE TABLE {AGG} (id INTEGER PRIMARY KEY AUTOINCREMENT, 原始值 TEXT, 值 TEXT, 源行数 INT, 写法数 INT)")
    ex(f"INSERT INTO {AGG}(原始值,值,源行数,写法数) VALUES ('a','A',1,1)")
    ex("INSERT INTO agg_task(name,source_table,source_col,dim_table,key_col,value_col,method,"
       "created_at,updated_at) VALUES (?,?,?,?,?,?,?,datetime('now'),datetime('now'))",
       ("临时聚合", "fct_prpo", "临时列Z", AGG, "原始值", "值", "exact"))
    app.register_imported_table(TMP, "临时表", "测试用", "id", "t.csv", 3)


make_fixtures()
imported_id = q("SELECT rowid FROM imported_table WHERE table_name=?", (TMP,))[0][0]
agg_task_id = q("SELECT id FROM agg_task WHERE dim_table=?", (AGG,))[0]["id"]

print("== 1. 影响评估 ==")
r = client.get(f"/api/tables/{TMP}/drop-info")
d = r.get_json()
check("drop-info 200", r.status_code == 200, r.status_code)
check("评估带行数", d.get("rows") == 3, d.get("rows"))
check("评估出 1 条关系", len(d.get("relations") or []) == 1, d.get("relations"))
check("评估到独立维度表登记", d.get("imported") is True, d.get("imported"))
check("评估到模型布局", d.get("layout") is True, d.get("layout"))
check("评估到 1 条质量规则", d.get("quality_rules") == 1, d.get("quality_rules"))
check("无阻塞项", not (d.get("blockers") or []), d.get("blockers"))

print("== 2. 拒绝删保护对象 ==")
r = client.delete("/api/tables/map_config", json={"confirm": "map_config", "backup": False})
check("元数据表被拒", r.status_code == 400 and "元数据" in r.get_json().get("error", ""),
      r.get_json())
check("元数据表仍在", bool(q("SELECT 1 FROM sqlite_master WHERE name='map_config'")))

r = client.delete("/api/tables/vw_prpo", json={"confirm": "vw_prpo", "backup": False})
check("视图被拒", r.status_code == 400 and "视图" in r.get_json().get("error", ""),
      r.get_json())
check("视图仍在", bool(q("SELECT 1 FROM sqlite_master WHERE name='vw_prpo'")))

r = client.delete("/api/tables/fct_prpo", json={"confirm": "fct_prpo", "backup": False})
check("被转换查询引用的表被拒", r.status_code == 400 and "数据转换" in r.get_json().get("error", ""),
      r.get_json())
check("fct_prpo 仍在", bool(q("SELECT 1 FROM sqlite_master WHERE name='fct_prpo'")))

r = client.get("/api/tables/nope_not_exist/drop-info")
check("不存在的表给阻塞提示", "没有这张表" in "".join(r.get_json().get("blockers") or []),
      r.get_json())

print("== 3. 表名回填不一致 ==")
r = client.delete(f"/api/tables/{TMP}", json={"confirm": "dim_tmp_dropX", "backup": False})
check("表名不一致被拒", r.status_code == 400, r.status_code)
check("表未被删", bool(q("SELECT 1 FROM sqlite_master WHERE name=?", (TMP,))))
check("关系未被误删", len(q("SELECT 1 FROM map_config WHERE target_table=?", (TMP,))) == 1)

print("== 4. 正常删除（backup=0）==")
r = client.delete(f"/api/tables/{TMP}", json={"confirm": TMP, "backup": False})
j = r.get_json()
check("删除成功", r.status_code == 200 and j.get("ok"), j)
check("表已不存在", not q("SELECT 1 FROM sqlite_master WHERE name=?", (TMP,)))
check("同时删了 1 条关系",
      not q("SELECT 1 FROM map_config WHERE target_table=? OR source_table=?", (TMP, TMP)))
check("同时删了模型布局", not q("SELECT 1 FROM map_layout WHERE table_name=?", (TMP,)))
check("同时删了质量规则", not q("SELECT 1 FROM quality_rules WHERE target_table=?", (TMP,)))
check("同时删了维度表登记", not q("SELECT 1 FROM imported_table WHERE table_name=?", (TMP,)))
check("内存登记已撤销",
      TMP not in app.KNOWN_TABLES and TMP not in app.TABLE_LABELS
      and TMP not in app.IMPORTED_TABLES)
check("侧栏清单里也没有了",
      not any(x["table"] == TMP for x in client.get("/api/tables").get_json()))

print("== 5. 聚合维度表连带删任务 ==")
app.register_agg_table(AGG, "临时聚合", "fct_prpo", "临时列Z", "值")
r = client.delete(f"/api/tables/{AGG}", json={"confirm": AGG, "backup": False})
check("聚合维度表删除成功", r.status_code == 200 and r.get_json().get("ok"), r.get_json())
check("指向它的聚合任务已删", not q("SELECT 1 FROM agg_task WHERE id=?", (agg_task_id,)))
check("聚合登记已撤销", AGG not in app.AGG_TABLES and AGG not in app.KNOWN_TABLES)

print("== 6. 备份开关 ==")
bdir = os.path.join(HERE, "backups")
before = len([f for f in os.listdir(bdir)]) if os.path.isdir(bdir) else 0
ex(f"CREATE TABLE {TMP2} (id INTEGER PRIMARY KEY, v TEXT)")
r = client.delete(f"/api/tables/{TMP2}", json={"confirm": TMP2, "backup": True})
j = r.get_json()
check("带备份删除成功", r.status_code == 200 and j.get("ok"), j)
check("返回备份文件名", bool(j.get("backup")), j)
check("备份文件已生成",
      bool(j.get("backup")) and os.path.exists(os.path.join(bdir, j["backup"])), j.get("backup"))
if os.path.isdir(bdir):
    for f in os.listdir(bdir):
        if f.startswith("procurement_before_drop_"):
            try:
                os.remove(os.path.join(bdir, f))
            except OSError:
                pass
    if not os.listdir(bdir):
        try:
            os.rmdir(bdir)
        except OSError:
            pass

print("== 7. 清理 ==")
ex("DELETE FROM map_config WHERE source_col='临时列X'")
check("测试库无残留临时表",
      not q("SELECT 1 FROM sqlite_master WHERE name IN (?,?,?)", (TMP, TMP2, AGG)))
try:
    os.remove(COPY)
    check("临时库副本已删除", True)
except OSError as e:
    check("临时库副本已删除", False, str(e))

print(f"\n结果：{PASS} 通过 / {FAIL} 失败")
sys.exit(1 if FAIL else 0)
