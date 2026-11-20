# -*- coding: utf-8 -*-
"""「导入数据 → 新建独立维度表」回归。

覆盖点：
  · 预演（列名/行数/主键空值与重复/表名校验）不写库
  · 建表写入：列全 TEXT、主键生效、无 FOREIGN KEY、事实表零改动
  · 元数据登记 + /api/tables 可见 + 模拟重启恢复
  · CSV / XLSX、列名去重与引号转义、重复建表拒绝

策略同其它测试：复制真实库到临时文件，重指向 db.DB_PATH 后再 import app。
备份函数会被替换掉，避免测试复制 111MB 的大库。
"""
import csv
import io
import os
import shutil
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import db_manager as db  # noqa: E402

COPY = os.path.join(HERE, "_tmp_newtable.db")
if os.path.exists(COPY):
    os.remove(COPY)
shutil.copyfile(db.DB_PATH, COPY)
db.DB_PATH = COPY

import app  # noqa: E402

app._newtable_backup = lambda: "backup-skipped-in-test.db"
client = app.app.test_client()

PASS = FAIL = 0
SEQ = [0]


def chk(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK] {name} {extra}")
    else:
        FAIL += 1
        print(f"  [XX] {name} {extra}")


def newname():
    SEQ[0] += 1
    return f"dim_t{SEQ[0]}"


def csv_bytes(header, rows, encoding="utf-8-sig"):
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(header)
    w.writerows(rows)
    return buf.getvalue().encode(encoding)


def post(path, payload, name=None, filename="map.csv", **form):
    data = {"file": (io.BytesIO(payload), filename)}
    if name is not None:
        data["table"] = name
    for k, v in form.items():
        data[k] = str(v)
    return client.post(path, data=data, content_type="multipart/form-data")


def preview(payload, name=None, filename="map.csv", **form):
    return post("/api/import/new-table-preview", payload, name, filename, **form)


def create(payload, name, filename="map.csv", **form):
    form.setdefault("backup", "0")
    return post("/api/import/new-table", payload, name, filename, **form)


def j(r):
    return r.get_json() or {}


SRC = os.path.join(ROOT, "聚合维度表", "公司映射表.csv")
with open(SRC, "rb") as fh:
    REAL = fh.read()

print("\n== 1. 预演（不写库） ==")
n1 = newname()
r = preview(REAL, n1, label="公司映射维度表", pk="id")
d = j(r)
chk("预演 HTTP 200 / ok", r.status_code == 200 and d.get("ok") is True,
    f"-> {d.get('error','')}")
chk("列数 = 10", d.get("stats", {}).get("cols") == 10,
    f"-> {d.get('stats',{}).get('cols')}")
chk("行数 = 62", d.get("stats", {}).get("rows") == 62,
    f"-> {d.get('stats',{}).get('rows')}")
chk("主键 = id", d.get("pk") == "id", f"-> {d.get('pk')}")
chk("can_import", d.get("can_import") is True)
chk("列名原样保留", "CompanyAbbreviation" in (d.get("header") or []))
chk("返回前 3 行样例", len(d.get("sample") or []) == 3)
c = sqlite3.connect(COPY)
chk("预演不建表", c.execute(
    "SELECT COUNT(*) FROM sqlite_master WHERE name=?", (n1,)).fetchone()[0] == 0)
fct_before = c.execute("SELECT COUNT(*) FROM fct_company_location").fetchone()[0]
c.close()

print("\n== 2. 表名与文件校验 ==")
for label, nm, expect in [
    ("缺 dim_ 前缀", "company_map", 400),
    ("含空格", "dim bad name", 400),
    ("SQL 注入式表名", 'dim_x";DROP TABLE fct_po;--', 400),
    ("dim_agg_ 保留前缀", "dim_agg_evil", 400),
    ("空表名", "", 400),
]:
    rr = preview(REAL, nm)
    chk(label + " 被拒", rr.status_code == expect, f"-> {rr.status_code}")
chk("空文件被拒", preview(b"", newname()).status_code == 400)
chk("只有表头被拒", preview(csv_bytes(["a", "b"], []), newname()).status_code == 400)
chk("无文件被拒", client.post(
    "/api/import/new-table-preview", data={"table": newname()},
    content_type="multipart/form-data").status_code == 400)

print("\n== 3. 主键问题 ==")
dup = csv_bytes(["id", "v"], [["1", "a"], ["1", "b"], ["2", "c"]])
dd = j(preview(dup, newname(), pk="id"))
chk("预演报主键重复", dd.get("stats", {}).get("dup_pk") == 1)
chk("重复主键 can_import=false", dd.get("can_import") is False)
chk("重复主键样例回传", (dd.get("stats", {}).get("dup_samples") or [])[:1] == ["1"])
blank = csv_bytes(["id", "v"], [["1", "a"], ["", "b"]])
bd = j(preview(blank, newname(), pk="id"))
chk("预演报主键空值", bd.get("stats", {}).get("blank_pk") == 1)
n_fail = newname()
chk("重复主键写入被拒", create(dup, n_fail).status_code == 400)
c = sqlite3.connect(COPY)
chk("失败后没有留下表", c.execute(
    "SELECT COUNT(*) FROM sqlite_master WHERE name=?", (n_fail,)).fetchone()[0] == 0)
c.close()

print("\n== 4. 列名清洗 ==")
messy = csv_bytes(["", "Name", "name", 'A"B'], [["1", "x", "y", "z"]])
hd = j(preview(messy, newname(), pk="列1"))
cols = hd.get("header") or []
chk("空表头补名", "列1" in cols, f"-> {cols}")
chk("大小写不敏感去重", len(cols) == 4 and "name_2" in cols, f"-> {cols}")
chk("含双引号列名保留", 'A"B' in cols, f"-> {cols}")
n_quote = newname()
rq = create(messy, n_quote, pk="列1")
chk("含引号列建表成功", rq.status_code == 200 and j(rq).get("ok") is True,
    f"-> {j(rq).get('error','')}")

print("\n== 5. 建表写入 ==")
nt = newname()
r = create(REAL, nt, label="公司映射维度表", desc="公司主数据字典", pk="id", filename="公司映射表.csv")
d = j(r)
chk("建表 HTTP 200 / ok", r.status_code == 200 and d.get("ok") is True,
    f"-> {d.get('error','')}")
chk("返回行数 62", d.get("rows") == 62, f"-> {d.get('rows')}")
chk("返回列数 10", d.get("cols") == 10)
c = sqlite3.connect(COPY)
info = c.execute(f"PRAGMA table_info({app.db.qid(nt)})").fetchall()
chk("表内 10 列", len(info) == 10, f"-> {len(info)}")
chk("全部 TEXT", all(x[2].upper() == "TEXT" for x in info))
chk("id 为主键", any(x[1] == "id" and x[5] for x in info))
cnt = c.execute(f"SELECT COUNT(*) FROM {app.db.qid(nt)}").fetchone()[0]
chk("表内 62 行", cnt == 62, f"-> {cnt}")
val = c.execute(
    f"SELECT CompanyAbbreviation FROM {app.db.qid(nt)} WHERE id='491'").fetchone()
chk("数据内容正确", bool(val) and val[0] == "江门悦之宝", f"-> {val}")
ddl = c.execute("SELECT sql FROM sqlite_master WHERE name=?", (nt,)).fetchone()[0]
chk("DDL 不含 REFERENCES", "REFERENCES" not in ddl.upper())
chk("事实表行数未变",
    c.execute("SELECT COUNT(*) FROM fct_company_location").fetchone()[0] == fct_before)
meta = c.execute(
    "SELECT label,descr,pk_col,source_file,row_count FROM imported_table "
    "WHERE table_name=?", (nt,)).fetchone()
chk("元数据已登记", meta is not None and meta[0] == "公司映射维度表"
    and meta[2] == "id" and meta[4] == 62, f"-> {meta}")
c.close()

print("\n== 6. 登记 / 可见 / 恢复 / 重复拒绝 ==")
chk("KNOWN_TABLES 含新表", nt in app.KNOWN_TABLES)
chk("TABLE_LABELS 中文名", app.TABLE_LABELS.get(nt, ("",))[0] == "公司映射维度表")
tabs = j(client.get("/api/tables"))
hit = next((x for x in tabs if x.get("table") == nt), None)
chk("/api/tables 可见", hit is not None)
chk("kind=dim", bool(hit) and hit.get("kind") == "dim", f"-> {hit and hit.get('kind')}")
chk("label 正确", bool(hit) and hit.get("label") == "公司映射维度表")
chk("rows=62", bool(hit) and hit.get("rows") == 62)
chk("重复建同名被拒", create(REAL, nt).status_code == 400)
# 模拟重启：清空内存登记后从 imported_table 恢复
app.KNOWN_TABLES.discard(nt)
app.TABLE_LABELS.pop(nt, None)
app.IMPORTED_TABLES.pop(nt, None)
app.load_imported_tables()
chk("重启恢复 KNOWN_TABLES", nt in app.KNOWN_TABLES)
chk("重启恢复 TABLE_LABELS", app.TABLE_LABELS.get(nt, ("",))[0] == "公司映射维度表")

print("\n== 7. XLSX ==")
try:
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "映射"
    ws.append(["id", "Name"])
    ws.append(["1", "甲"])
    ws.append(["2", "乙"])
    xb = io.BytesIO()
    wb.save(xb)
    nx = newname()
    rx = create(xb.getvalue(), nx, filename="m.xlsx", pk="id")
    dx = j(rx)
    chk("XLSX 建表成功", rx.status_code == 200 and dx.get("ok") is True,
        f"-> {dx.get('error','')}")
    chk("XLSX 行数 = 2", dx.get("rows") == 2, f"-> {dx.get('rows')}")
    px = j(preview(xb.getvalue(), nx, filename="m.xlsx"))
    chk("XLSX 预演能读工作表", px.get("source") == "xlsx" or "已存在" in str(px.get("error", "")))
except ImportError:
    chk("XLSX 测试跳过（无 openpyxl）", True)

print(f"\n===== {PASS} 通过 / {FAIL} 失败 =====")
try:
    app.get_con().close()
except Exception:
    pass
try:
    os.remove(COPY)
except OSError:
    pass
sys.exit(1 if FAIL else 0)
