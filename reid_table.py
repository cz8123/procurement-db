"""把 TEXT 主键的导入表重建成 INTEGER PRIMARY KEY AUTOINCREMENT，并按原顺序重编号 1..N。

场景：导入的维度表主键来自源文件（如 491–552），既不是 1 起，也不能自增；
新增行时必须手填 id。改成 INTEGER PRIMARY KEY 后 SQLite 自动分配，且 AUTOINCREMENT
保证删除后不复用旧 id。

用法：
    python reid_table.py dim_company_map            # 默认主键列 id
    python reid_table.py 某表 某主键列 --dry-run

安全性：
- 整段 DDL（CREATE / INSERT / DROP / RENAME）在单个事务内，失败整体 rollback；
- 先把全表快照写成 JSON（backups/<表>_before_reid_<时间>.json），可用于回滚数据；
- 逐列比对重建前后内容（按新 id 顺序），任一处不一致就不提交；
- 不复制 111MB 全库（只有表级快照），改的是小维度表。
"""
import datetime
import json
import os
import sqlite3
import sys

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "procurement.db")
BK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backups")


def qid(name):
    return '"' + str(name).replace('"', '""') + '"'


def main(table, pkcol="id", dry=False):
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    try:
        info = con.execute(f"PRAGMA table_info({qid(table)})").fetchall()
        if not info:
            print(f"[X] 表不存在：{table}")
            return 1
        cols = [(r["name"], (r["type"] or "TEXT"), r["pk"]) for r in info]
        pks = [c for c in cols if c[2]]
        if len(pks) != 1:
            print(f"[X] 需要单列主键，实际 {len(pks)} 个：{[c[0] for c in pks]}")
            return 1
        if pks[0][0] != pkcol:
            print(f"[X] 主键列是「{pks[0][0]}」，与指定「{pkcol}」不符")
            return 1
        if pks[0][1].upper() == "INTEGER":
            print(f"[=] {table}.{pkcol} 已是 INTEGER 主键，无需重建")
            return 0

        names = [c[0] for c in cols]
        others = [n for n in names if n != pkcol]
        before = con.execute(
            f"SELECT * FROM {qid(table)} ORDER BY CAST({qid(pkcol)} AS INTEGER), rowid"
        ).fetchall()
        n = len(before)

        # 依赖视图/触发器会阻塞 DROP + RENAME，先查清楚
        dep = con.execute(
            "SELECT type,name FROM sqlite_master WHERE type IN ('view','trigger') "
            "AND sql LIKE ?", [f"%{table}%"]).fetchall()
        if dep:
            print(f"[X] 有对象依赖该表，先处理：{[tuple(d) for d in dep]}")
            return 1

        print(f"[i] {table}：{n} 行，主键 {pkcol} 由 {pks[0][1]} 改为 INTEGER PRIMARY KEY AUTOINCREMENT")
        if dry:
            print("[dry-run] 不落库")
            return 0

        os.makedirs(BK, exist_ok=True)
        snap = os.path.join(
            BK, f"{table}_before_reid_{datetime.datetime.now():%Y%m%d_%H%M%S}.json")
        with open(snap, "w", encoding="utf-8") as f:
            json.dump({"table": table, "pk": pkcol,
                       "ddl": con.execute("SELECT sql FROM sqlite_master WHERE name=?",
                                          [table]).fetchone()[0],
                       "columns": names,
                       "rows": [dict(r) for r in before]},
                      f, ensure_ascii=False, indent=2)
        print(f"[i] 快照：{os.path.basename(snap)}")

        new = f"{table}__reid_tmp"
        defs = ", ".join(
            f"{qid(n2)} INTEGER PRIMARY KEY AUTOINCREMENT" if n2 == pkcol
            else f"{qid(n2)} {typ}" for n2, typ, _ in cols)
        col_list = ", ".join(qid(c) for c in others)
        con.execute("BEGIN")
        try:
            con.execute(f"DROP TABLE IF EXISTS {qid(new)}")
            con.execute(f"CREATE TABLE {qid(new)} ({defs})")
            con.execute(
                f"INSERT INTO {qid(new)} ({col_list}) "
                f"SELECT {col_list} FROM {qid(table)} "
                f"ORDER BY CAST({qid(pkcol)} AS INTEGER), rowid")
            con.execute(f"DROP TABLE {qid(table)}")
            con.execute(f"ALTER TABLE {qid(new)} RENAME TO {qid(table)}")

            # 逐行逐列比对：新 id 必须是 1..N，其余列顺序内容必须一致
            after = con.execute(
                f"SELECT * FROM {qid(table)} ORDER BY {qid(pkcol)}").fetchall()
            if len(after) != n:
                raise AssertionError(f"行数变化 {n} -> {len(after)}")
            if [r[pkcol] for r in after] != list(range(1, n + 1)):
                raise AssertionError("id 不是连续的 1..N")
            for old, cur in zip(before, after):
                for c in others:
                    if (old[c] or "") != (cur[c] or ""):
                        raise AssertionError(f"第 {cur[pkcol]} 行「{c}」不一致："
                                             f"{old[c]!r} != {cur[c]!r}")
            con.commit()
        except Exception as e:
            con.rollback()
            print(f"[X] 已回滚：{e}")
            return 1

        seq = con.execute("SELECT seq FROM sqlite_sequence WHERE name=?", [table]).fetchone()
        print(f"[OK] 重建完成：{n} 行，id 1–{n}，sqlite_sequence={seq[0] if seq else 'N/A'}")
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(args[0], args[1] if len(args) > 1 else "id",
                  dry="--dry-run" in sys.argv))
