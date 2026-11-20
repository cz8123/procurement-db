# -*- coding: utf-8 -*-
"""去掉事实表中指向已删除维度表的悬空外键（在副本上验证用）。

SQLite 不支持 DROP CONSTRAINT，只能走「建新表 + 搬数据 + 换名 + 重建索引」。
保留仍指向存在表的外键（fct_po->fct_prpo、fct_prpo->fct_framework_agreement 等）。
"""
import re
import sqlite3
import sys


def qid(name):
    return '"' + str(name).replace('"', '""') + '"'


def plan(con):
    """返回 {表: (原DDL, 清理后DDL, 保留外键数, 去掉外键数)}"""
    exist = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    out = {}
    for (t,) in con.execute("SELECT name FROM sqlite_master WHERE type='table' "
                            "AND name NOT LIKE 'sqlite_%'"):
        ddl = con.execute("SELECT sql FROM sqlite_master WHERE name=?", (t,)).fetchone()[0] or ""
        if "REFERENCES" not in ddl:
            continue
        lines = ddl.strip().split("\n")
        head = lines[0]
        body = lines[1:]
        if body and body[-1].strip() == ")":
            body = body[:-1]
        kept, dropped = [], 0
        for ln in body:
            m = re.search(r"REFERENCES\s+\"?([A-Za-z_][\w]*)\"?", ln)
            if m and m.group(1) not in exist:
                dropped += 1                      # 指向已删表 → 丢掉这一行
                continue
            kept.append(ln.rstrip().rstrip(","))
        if dropped:
            # 逗号交给 join 统一生成，避免删行后出现 `A TEXT B TEXT`
            out[t] = (head + "\n" + ",\n".join(kept) + "\n)", len(kept), dropped)
    return out


def rebuild(con, table, new_ddl):
    """建新表 → 搬数据 → 删旧表 → 改名 → 重建索引 + 依赖视图。返回 (行数, 索引数)。"""
    idx_sql = [r[0] for r in con.execute(
        "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name=? AND sql IS NOT NULL",
        (table,))]
    # 依赖该表的视图必须先摘掉：DROP TABLE 后视图失效，ALTER ... RENAME 会因
    # 「error in view xxx: no such table」直接失败。重建后原样恢复。
    views = [(r[0], r[1]) for r in con.execute(
        "SELECT name, sql FROM sqlite_master WHERE type='view' AND sql LIKE ?",
        (f"%{qid(table)}%",))]
    for vname, _ in views:
        con.execute(f"DROP VIEW {qid(vname)}")
    tmp = f"__rebuild_{table}"
    con.execute(f"DROP TABLE IF EXISTS {qid(tmp)}")
    con.execute(new_ddl.replace(f'CREATE TABLE IF NOT EXISTS {qid(table)}',
                                f'CREATE TABLE {qid(tmp)}', 1)
                .replace(f'CREATE TABLE "{table}"', f'CREATE TABLE {qid(tmp)}', 1))
    cols = [r[1] for r in con.execute(f"PRAGMA table_info({qid(tmp)})")]
    collist = ",".join(qid(c) for c in cols)
    n = con.execute(f"SELECT COUNT(*) FROM {qid(table)}").fetchone()[0]
    con.execute(f"INSERT INTO {qid(tmp)} ({collist}) SELECT {collist} FROM {qid(table)}")
    con.execute(f"DROP TABLE {qid(table)}")
    con.execute(f"ALTER TABLE {qid(tmp)} RENAME TO {qid(table)}")
    for s in idx_sql:
        con.execute(s)
    for _, vsql in views:
        con.execute(vsql)
    return n, len(idx_sql)


def main(db_path):
    con = sqlite3.connect(db_path)
    con.execute("PRAGMA foreign_keys=OFF")
    todo = plan(con)
    if not todo:
        print("没有悬空外键")
        return
    print("将重建的表：")
    for t, (ddl, kept, dropped) in todo.items():
        print(f"  {t}: 去掉 {dropped} 个悬空外键")
    # 先备份：重建是破坏性结构变更，虽然只搬数据不改值，也必须留回滚点
    import datetime
    import os
    import shutil
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    bak_dir = os.path.join(os.path.dirname(os.path.abspath(db_path)), "backups")
    os.makedirs(bak_dir, exist_ok=True)
    bak = os.path.join(bak_dir, f"procurement_before_fk_fix_{ts}.db")
    try:
        dst = sqlite3.connect(bak)
        con.backup(dst)
        dst.close()
        print(f"  已备份 -> {bak} ({os.path.getsize(bak)/1048576:.1f}MB)")
    except Exception as e:
        print(f"  备份失败，中止：{e}")
        return
    con.execute("BEGIN")
    for t, (ddl, kept, dropped) in todo.items():
        before = con.execute(f"SELECT COUNT(*) FROM {qid(t)}").fetchone()[0]
        n, nidx = rebuild(con, t, ddl)
        after = con.execute(f"SELECT COUNT(*) FROM {qid(t)}").fetchone()[0]
        assert before == after == n, f"{t} 行数不符 {before}/{n}/{after}"
        print(f"  ✓ {t}: {n} 行，重建 {nidx} 个索引")
    con.commit()
    con.execute("PRAGMA foreign_keys=ON")
    print("--- 校验 ---")
    print("  foreign_key_check:", con.execute("PRAGMA foreign_key_check").fetchall()[:5] or "空（无违规）")
    print("  integrity_check:", con.execute("PRAGMA integrity_check").fetchone()[0])
    # 视图与转换仍可用
    try:
        print("  vw_prpo 行数:", con.execute("SELECT COUNT(*) FROM vw_prpo").fetchone()[0])
    except Exception as e:
        print("  vw_prpo 失效:", e)
    print("  transform_query:", con.execute(
        "SELECT COUNT(*) FROM transform_query").fetchone()[0])
    con.close()


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "procurement.db")
