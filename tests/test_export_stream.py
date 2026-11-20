# -*- coding: utf-8 -*-
"""导出接口回归（流式改造后）。

覆盖点：
  · 分批 fetchmany 跨批边界的行数完整性（44987 不是 8000 的整数倍）
  · 搜索 / 列筛选 / 排序 / 列选择仍生效
  · BOM、Content-Disposition、mimetype 等下载头没被流式改造破坏
  · 视图可导出、未知表 404、空结果只留表头

策略同其它测试：复制真实库到临时文件，重指向 db.DB_PATH 后再 import app。
"""
import csv
import io
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import db_manager as db  # noqa: E402

COPY = os.path.join(HERE, "_tmp_export.db")
if os.path.exists(COPY):
    os.remove(COPY)
shutil.copyfile(db.DB_PATH, COPY)
db.DB_PATH = COPY

import app  # noqa: E402

client = app.app.test_client()

PASS = FAIL = 0


def chk(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK] {name} {extra}")
    else:
        FAIL += 1
        print(f"  [XX] {name} {extra}")


def get_rows(url):
    """返回 (response, 解析后的二维表)。"""
    r = client.get(url)
    txt = r.data.decode("utf-8-sig")
    return r, list(csv.reader(io.StringIO(txt)))


def db_count(sql, params=()):
    con = app.get_con()
    try:
        return con.execute(sql, params).fetchone()[0]
    finally:
        con.close()


print("=== 1. 基本导出（跨批边界：44987 行 / 每批 8000）===")
r, rows = get_rows("/api/tables/fct_prpo/export")
n = db_count("SELECT COUNT(*) FROM fct_prpo")
chk("HTTP 200", r.status_code == 200)
chk("行数 = 库行数 + 表头", len(rows) - 1 == n, f"-> {len(rows) - 1} / {n}")
chk("分批后尾批不丢行", (n % app._EXPORT_CHUNK) != 0 and len(rows) - 1 == n,
    f"-> 尾批 {n % app._EXPORT_CHUNK} 行")
chk("带 UTF-8 BOM（Excel 打开不乱码）", r.data[:3] == b"\xef\xbb\xbf")
chk("mimetype 为 text/csv", "text/csv" in r.mimetype)
cd = r.headers.get("Content-Disposition", "")
chk("Content-Disposition 带 filename", "attachment" in cd and "filename=" in cd)
chk("中文文件名被 URL 编码", "filename*=UTF-8''" in cd)
con = app.get_con()
ncol = len(con.execute("PRAGMA table_info(fct_prpo)").fetchall())
con.close()
chk("列数与表结构一致", len(rows[0]) == ncol, f"-> {len(rows[0])} / {ncol}")

print("=== 2. 列选择 / 排序 / 搜索 / 过滤 ===")
r, rows = get_rows("/api/tables/fct_po/export?cols=%E9%83%A8%E9%97%A8,%E5%85%AC%E5%8F%B8")
chk("cols 只导出指定列", r.status_code == 200 and len(rows[0]) == 2, f"-> {rows[0]}")
chk("列选择后行数仍完整", len(rows) - 1 == db_count("SELECT COUNT(*) FROM fct_po"))

r, rows = get_rows("/api/tables/fct_po/export?cols=不存在的列A,不存在的列B")
chk("cols 全非法时回退全部列", len(rows[0]) > 2, f"-> {len(rows[0])} 列")

r, rows = get_rows("/api/tables/fct_po/export?search=%E9%87%87%E8%B4%AD")
chk("search 命中子集且非全表", 0 < len(rows) - 1 < db_count("SELECT COUNT(*) FROM fct_po"),
    f"-> {len(rows) - 1} 行")

r, rows = get_rows("/api/tables/fct_po/export?search=zzz_%E4%B8%8D%E5%AD%98%E5%9C%A8_zzz")
chk("空结果只留表头", r.status_code == 200 and len(rows) == 1, f"-> {len(rows)} 行")
chk("空结果仍返回列名", len(rows[0]) > 2)

r, rows = get_rows("/api/tables/fct_po/export?search=_")
chk("下划线按字面匹配（不退回全表）",
    0 < len(rows) - 1 < db_count("SELECT COUNT(*) FROM fct_po"), f"-> {len(rows) - 1} 行")

import json as _json  # noqa: E402

r, rows = get_rows("/api/tables/fct_po/export?filters=" +
                   app.urllib.parse.quote(_json.dumps([{"col": "部门", "op": "empty"}])))
chk("filters(empty) 生效", r.status_code == 200 and len(rows) - 1 == db_count(
    "SELECT COUNT(*) FROM fct_po WHERE 部门 IS NULL OR 部门 = ''"),
    f"-> {len(rows) - 1} 行")

r, rows = get_rows("/api/tables/fct_po/export?filters=" +
                   app.urllib.parse.quote(_json.dumps([{"col": "部门", "op": "contains", "val": "IT"}])))
chk("filters(contains) 生效", r.status_code == 200 and 0 < len(rows) - 1 < db_count(
    "SELECT COUNT(*) FROM fct_po"), f"-> {len(rows) - 1} 行")

r, rows = get_rows("/api/tables/fct_po/export?sort=%E9%87%87%E8%B4%AD%E8%AE%A2%E5%8D%95%E5%8F%B7&order=desc")
chk("排序参数被接受", r.status_code == 200 and len(rows) - 1 == db_count("SELECT COUNT(*) FROM fct_po"))

print("=== 3. 视图 / 边界 ===")
r, rows = get_rows("/api/tables/vw_prpo/export")
chk("视图可导出（只读对象）", r.status_code == 200 and len(rows) - 1 == db_count("SELECT COUNT(*) FROM vw_prpo"),
    f"-> {len(rows) - 1} 行")

r = client.get("/api/tables/not_exist_table/export")
chk("未知表 404", r.status_code == 404)
r = client.get("/api/tables/map_config/export")
chk("元数据表不可导出(404)", r.status_code == 404)

print(f"\n===== {PASS} 通过 / {FAIL} 失败 =====")
try:
    os.remove(COPY)
except OSError:
    pass
sys.exit(1 if FAIL else 0)
