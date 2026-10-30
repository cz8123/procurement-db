# -*- coding: utf-8 -*-
"""补齐维度表：让所有『事实表列 -> 维度表列』关联不再有孤儿。

策略：
  1) 对每个维度目标列，取所有引用它的事实表列的孤儿值（UNION）。
  2) 若孤儿值 trim 后已存在于维度表 -> 视为事实侧脏空格，UPDATE 事实表对齐。
  3) 否则为真正缺失的成员 -> 在维度表 INSERT 新行（id 续号，附最佳英文名/中文名）。
"""
import sqlite3
from db_manager import DB_PATH, get_fks, qid

con = sqlite3.connect(DB_PATH)
con.execute("PRAGMA foreign_keys=OFF;")

def is_cjk(ch):
    return '一' <= ch <= '鿿'

def en_part(s):
    cut = len(s)
    for i, ch in enumerate(s):
        if is_cjk(ch):
            cut = i
            break
    if cut == 0:
        return s
    e = s[:cut].strip().rstrip('_').rstrip('-').strip()
    return e if e else s

def cn_part(s):
    return s.rsplit('-', 1)[1].strip() if '-' in s else s

# 把 FKS 按 (目标表, 目标列) 分组，收集所有引用它的 (源表, 源列)
from collections import defaultdict
grp = defaultdict(list)
for (t, col, rt, rc) in get_fks(con):
    if rt.startswith("dim_"):
        grp[(rt, rc)].append((t, col))

report = {"inserted": [], "trimmed": []}

for (rt, rc), sources in grp.items():
    # 维度表现在已有的值集合（精确）
    existing = {r[0] for r in con.execute(f"SELECT {qid(rc)} FROM {qid(rt)}").fetchall()}
    # 孤儿（精确不在维度表），UNION 跨所有事实源
    orphans = set()
    for (t, col) in sources:
        q = (f"SELECT DISTINCT {qid(col)} FROM {qid(t)} "
             f"WHERE {qid(col)} IS NOT NULL AND TRIM({qid(col)}) <> '' "
             f"AND {qid(col)} NOT IN (SELECT {qid(rc)} FROM {qid(rt)} WHERE {qid(rc)} IS NOT NULL)")
        for r in con.execute(q).fetchall():
            orphans.add(r[0])
    if not orphans:
        print(f"[跳过] {rt}.{rc}：无孤儿")
        continue
    # id 续号起点
    nums = [int(r[0]) for r in con.execute(f"SELECT id FROM {qid(rt)}").fetchall() if str(r[0]).isdigit()]
    next_id = (max(nums) if nums else 0) + 1
    cols = [c[1] for c in con.execute(f"PRAGMA table_info({qid(rt)})").fetchall()]
    for v in sorted(orphans, key=lambda x: (x is None, str(x))):
        vt = v.strip() if v is not None else v
        # 情况2：trim 后已在维度表 -> 事实侧对齐
        if vt != v and vt in existing:
            for (t, col) in sources:
                n = con.execute(
                    f"UPDATE {qid(t)} SET {qid(col)}=? WHERE {qid(col)}=?", (vt, v)).rowcount
                if n:
                    report["trimmed"].append((t, col, v, vt, n))
            print(f"  [对齐] {rt}.{rc}：事实侧脏空格 {v!r} -> {vt!r}（维度已有，UPDATE 事实表）")
            continue
        # 情况3：真正缺失 -> 新建维度行
        row = {}
        row[rc] = v
        if rt == "dim_department" and "DepartmentEn" in cols:
            row["DepartmentEn"] = en_part(v)
        elif rt == "dim_exception_reason" and "ExceptionReasonEn" in cols:
            row["ExceptionReasonEn"] = v
        elif rt == "dim_category" and "CategoryName" in cols:
            row["CategoryName"] = cn_part(v)
        row["id"] = str(next_id)
        next_id += 1
        col_list = ", ".join(qid(c) for c in row)
        ph = ", ".join("?" * len(row))
        con.execute(f"INSERT INTO {qid(rt)} ({col_list}) VALUES ({ph})",
                    [row[c] for c in row])
        report["inserted"].append((rt, rc, v, dict(row)))
        print(f"  [新建] {rt}: {rc}={v!r}  id={row['id']}  其它={ {k:row[k] for k in row if k not in ('id',rc)} }")

con.commit()
con.close()

print("\n==== 汇总 ====")
print(f"新建维度行: {len(report['inserted'])}")
for rt, rc, v, rw in report["inserted"]:
    print(f"   + {rt}.{rc} = {v!r}  (id={rw.get('id')})")
print(f"事实侧空格对齐(UPDATE): {len(report['trimmed'])} 处")
for t, col, old, new, n in report["trimmed"]:
    print(f"   ~ {t}.{col}: {old!r} -> {new!r}  影响 {n} 行")
