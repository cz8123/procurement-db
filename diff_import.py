#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""导入前差异预检（只读，不改数据库、不改源文件）。

默认取项目目录里修改时间最新的两个 xlsx：old = 次新，new = 最新。
也可显式指定：python diff_import.py old.xlsx new.xlsx

每张事实表输出：
  表头：新增列 / 消失列 / 新文件有但库里没有的列
  数据：新增主键 / 消失主键 / 内容变化行（列出变化最多的列）
  重复：文件内部重复主键（导入时会被 INSERT OR IGNORE 跳过）

金额按数值比较（避免 "¥30,000.00" 与 30000 的假差异），文本去首尾空格，
已知写法变体按 db_manager.VALUE_ALIASES 归一化。
"""
import os
import sqlite3
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db_manager as db

HERE = os.path.dirname(os.path.abspath(__file__))


def pick_two():
    import glob
    cands = [p for p in glob.glob(os.path.join(HERE, "*.xlsx"))
             if not os.path.basename(p).startswith("~$")]
    cands.sort(key=os.path.getmtime, reverse=True)
    if len(cands) < 2:
        raise SystemExit("需要两个 xlsx 才能对比（旧版 + 新版），当前只有 %d 个" % len(cands))
    return cands[1], cands[0]


def norm(v, col, amt_cols, aliases):
    if v is None:
        return None
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return None
        s = aliases.get(col, {}).get(s, s)
    else:
        s = str(v).strip()
    if col in amt_cols:
        try:
            return db.parse_amount(s)
        except Exception:
            return s
    return s


def read_sheet(path, sheet, table):
    src = db.XlsxSource(path, sheet)
    try:
        raw = db.apply_header_aliases(src.header())
        hdr = db.dedupe_headers(raw)
        amt_cols = {raw[i] for i, h in enumerate(raw) if db.is_amount_header(h)}
        aliases = db.VALUE_ALIASES.get(table, {})
        pk_header = next((v[1] for v in db.FILE_MAP.values() if v[0] == table), None)
        try:
            pk_i = raw.index(pk_header)
        except ValueError:
            raise SystemExit("[%s] 找不到主键列 %r" % (sheet, pk_header))
        pk_col = hdr[pk_i]
        data, dup = {}, 0
        for row in src.rows():
            row = list(row) + [None] * (len(raw) - len(row))
            pk = row[pk_i]
            pk = "" if pk is None else str(pk).strip()
            if not pk:
                continue
            if pk in data:
                dup += 1
                continue
            data[pk] = tuple(norm(row[i], hdr[i], amt_cols, aliases) for i in range(len(hdr)))
        return hdr, pk_col, data, dup
    finally:
        src.close()


def main():
    old, new = (sys.argv[1], sys.argv[2]) if len(sys.argv) >= 3 else pick_two()
    print("旧文件:", os.path.basename(old))
    print("新文件:", os.path.basename(new))
    con = sqlite3.connect(os.path.join(HERE, "procurement.db"))
    total_add = total_del = total_chg = 0

    for fn in db.LOAD_ORDER:
        table = db.FILE_MAP[fn][0]
        sheet = db.XLSX_SHEET_OF_TABLE.get(table)
        if not sheet:
            continue
        try:
            oh, pk, od, odup = read_sheet(old, sheet, table)
            nh, _, nd, ndup = read_sheet(new, sheet, table)
        except KeyError:
            print("\n[%s] 某个文件缺少工作表 %s，跳过" % (table, sheet))
            continue

        oset, nset = set(od), set(nd)
        added, removed = nset - oset, oset - nset
        common = oset & nset
        cols = [c for c in nh if c in oh]
        idx = [nh.index(c) for c in cols]
        oidx = [oh.index(c) for c in cols]
        changed, colcnt = 0, Counter()
        for k in common:
            a, b = od[k], nd[k]
            if a == b:
                continue
            changed += 1
            for ci, oi in zip(idx, oidx):
                if a[oi] != b[ci]:
                    colcnt[cols[ci]] += 1

        dbcols = {r[1] for r in con.execute('PRAGMA table_info("%s")' % table)}
        only_new = [c for c in nh if c not in oh]
        gone = [c for c in oh if c not in nh]
        not_in_db = [c for c in nh if c and c not in dbcols]
        dbrows = con.execute('SELECT COUNT(*) FROM "%s"' % table).fetchone()[0]

        total_add += len(added)
        total_del += len(removed)
        total_chg += changed
        print("\n=== %s（工作表：%s）===" % (table, sheet))
        print("  行数      旧 %6d / 新 %6d / 库 %6d" % (len(oset), len(nset), dbrows))
        print("  主键差异  新增 %5d / 消失 %5d / 内容变化 %5d" % (len(added), len(removed), changed))
        print("  文件内重复主键  旧 %d / 新 %d（导入时会跳过）" % (odup, ndup))
        if only_new: print("  新增列:", only_new)
        if gone: print("  消失列:", gone)
        if not_in_db: print("  新文件中库里没有的列（导入会跳过）:", not_in_db)
        if colcnt:
            print("  变化最多的列:", ", ".join("%s(%d)" % x for x in colcnt.most_common(6)))
        if added:
            print("  新增主键样例:", sorted(added)[:5])
        if removed:
            print("  消失主键样例:", sorted(removed)[:5])

    con.close()
    print("\n合计：新增主键 %d / 消失主键 %d / 内容变化 %d" % (total_add, total_del, total_chg))
    print("提示：本脚本只读，不写库；确认后再执行导入。")


if __name__ == "__main__":
    main()
