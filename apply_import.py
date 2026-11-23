#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一次性导入最新 xlsx：追加新行 + 更新已有行的变化列。

用法：
  python apply_import.py            # 预演，只打印将发生的写入，不落库
  python apply_import.py --yes      # 真正写入（会先备份 procurement.db）

规则：
  · 目标表 = XLSX_SHEET_OF_TABLE 里的 6 张事实表；主键取 FILE_MAP。
  · 文件里库中没有的列一律跳过（不自动 ALTER TABLE）——例如「参考编号_2」与
    「参考编号」完全重复、「采购子类别」用户明确忽略。
  · 新主键 INSERT OR IGNORE；已存在主键只更新「文件内容确实与库里不同」的列，
    未变化列、派生列、库特有列都不动（fields 语义，避免覆盖人工改动）。
  · 金额按数值比较（0.005 容差），文本去首尾空格，VALUE_ALIASES 归一化。
"""
import argparse
import os
import shutil
import sqlite3
import sys
from collections import Counter
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import db_manager as db

DB = os.path.join(HERE, "procurement.db")


def norm_row(row, meta, raw_header, aliases):
    """与 db_manager.load_table 完全一致的逐行归一化，返回 {列名: 值}。"""
    n_src = len(raw_header)
    row = list(row)
    if len(row) < n_src:
        row += [None] * (n_src - len(row))
    elif len(row) > n_src:
        row = row[:n_src]
    raw_dict = dict(zip(raw_header, row))
    for col, amap in aliases.items():
        if col in raw_dict:
            v = raw_dict[col]
            if v is not None and str(v).strip() in amap:
                row[raw_header.index(col)] = amap[str(v).strip()]
    full = row + db.compute_extras(meta["table"], raw_dict)
    out = {}
    for i in meta["keep"]:
        v = full[i]
        if v is None:
            out[meta["colnames"][i]] = None
        elif isinstance(v, str):
            s = v.strip()
            if not s:
                out[meta["colnames"][i]] = None
            elif i in meta["amount_idx"]:
                out[meta["colnames"][i]] = db.parse_amount(v)
            else:
                out[meta["colnames"][i]] = s
        elif isinstance(v, bool):
            out[meta["colnames"][i]] = "TRUE" if v else "FALSE"
        elif i in meta["amount_idx"]:
            out[meta["colnames"][i]] = db.parse_amount(v)
        else:
            out[meta["colnames"][i]] = str(v)
    return out


def db_norm(v, col, amount_cols):
    if v is None:
        return None
    if col in amount_cols:
        return db.parse_amount(v)
    return str(v).strip()


def same(a, b, col, amount_cols):
    if col in amount_cols and a is not None and b is not None:
        try:
            return abs(float(a) - float(b)) <= 0.005
        except (TypeError, ValueError):
            pass
    return a == b


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--yes", action="store_true", help="真正写入；默认只预演")
    ap.add_argument("--file", default=None, help="指定 xlsx，默认取目录里最新修改的")
    args = ap.parse_args()

    xlsx = args.file or db.find_xlsx()
    print("数据源:", os.path.basename(xlsx))
    print("模式:", "写入" if args.yes else "预演（不落库）")
    if not os.path.exists(xlsx):
        raise SystemExit("找不到 xlsx: " + xlsx)

    backup = None
    if args.yes:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        bdir = os.path.join(HERE, "backups")
        os.makedirs(bdir, exist_ok=True)
        backup = os.path.join(bdir, "procurement_before_import_%s.db" % stamp)
        shutil.copy2(DB, backup)
        print("已备份:", os.path.relpath(backup, HERE))

    con = sqlite3.connect(DB)
    con.execute("PRAGMA foreign_keys=OFF")
    try:
        grand_new = grand_upd = grand_cells = 0
        for fn in db.LOAD_ORDER:
            table = db.FILE_MAP[fn][0]
            sheet = db.XLSX_SHEET_OF_TABLE.get(table)
            if not sheet:
                continue
            src = db.XlsxSource(xlsx, sheet)
            try:
                raw = db.apply_header_aliases(src.header())
                if not raw:
                    print("跳过", table, "工作表为空:", sheet)
                    continue
                meta = db.create_table(con, fn, src)
                table_cols = [r[1] for r in con.execute('PRAGMA table_info("%s")' % table)]
                meta["keep"] = [i for i, c in enumerate(meta["colnames"]) if c in table_cols]
                skipped_cols = [c for i, c in enumerate(meta["colnames"]) if i not in meta["keep"]]
                pk_col = meta["pk_col"]
                insert_cols = [meta["colnames"][i] for i in meta["keep"]]
                pk_pos = insert_cols.index(pk_col) if pk_col in insert_cols else -1
                if pk_pos < 0:
                    raise SystemExit("[%s] 主键列不在可写列里" % table)
                amount_cols = {meta["colnames"][i] for i in meta["amount_idx"] if i in meta["keep"]}
                aliases = db.VALUE_ALIASES.get(table, {})

                # 0) 先快照库里现有主键（必须在插入前抓，否则新增行会被算成“已存在”）
                old = {}
                for r in con.execute('SELECT * FROM "%s"' % table):
                    k = r[pk_pos]
                    old[str(k).strip() if k is not None else ""] = r

                # 1) 追加：与 CLI update 完全相同的 INSERT OR IGNORE
                if args.yes:
                    db.load_table(con, fn, meta, src, verbose=False)

                # 2) 更新：重新读一遍文件，逐列比对库里现有行
                new_rows, dup = {}, 0
                for r in src.rows():
                    vals = norm_row(r, meta, raw, aliases)
                    pk = vals.get(pk_col)
                    pk = "" if pk is None else str(pk).strip()
                    if not pk:
                        continue
                    if pk in new_rows:
                        # 同一主键多行时保留最后一行：导出会把草稿/旧版本排在前面，
                        # 库里原本存的是后面的正式行（2026-11-23 实测：协议 56、BOM 46）。
                        # 取第一行会把 参考编号 写成 FormRef-_Draft_*，打断 PRPO 关联。
                        dup += 1
                    new_rows[pk] = vals   # 覆盖，保留最后一次出现（正式行）

                changed_rows, changed_cells, colcnt = 0, 0, Counter()
                pending = []
                for pk, vals in new_rows.items():
                    if pk not in old:
                        continue
                    cur = old[pk]
                    diffs = []
                    for col in insert_cols:
                        if col == pk_col:
                            continue
                        nv = vals[col]
                        ov = db_norm(cur[insert_cols.index(col)], col, amount_cols)
                        if not same(nv, ov, col, amount_cols):
                            diffs.append((col, nv))
                    if diffs:
                        changed_rows += 1
                        changed_cells += len(diffs)
                        for c, _ in diffs:
                            colcnt[c] += 1
                        pending.append((pk, diffs))

                if args.yes and pending:
                    for pk, diffs in pending:
                        for col, val in diffs:
                            con.execute('UPDATE "%s" SET "%s"=? WHERE "%s"=?' % (table, col, pk_col),
                                        (val, pk))
                    con.commit()

                print("\n%-26s 新增 %5d / 更新 %5d 行 / %5d 个单元格（文件重复主键 %d 已跳过）"
                      % (table, sum(1 for k in new_rows if k not in old),
                         changed_rows, changed_cells, dup))
                if skipped_cols:
                    print("  跳过库中不存在的列:", skipped_cols)
                if colcnt:
                    print("  变化列:", ", ".join("%s(%d)" % x for x in colcnt.most_common(6)))
                grand_new += sum(1 for k in new_rows if k not in old)
                grand_upd += changed_rows
                grand_cells += changed_cells
            finally:
                src.close()
        if args.yes:
            con.commit()
        print("\n合计：新增 %d 行 / 更新 %d 行 / %d 个单元格" % (grand_new, grand_upd, grand_cells))
        if not args.yes:
            print("这是预演。确认无误后加 --yes 真正写入。")
    finally:
        con.close()


if __name__ == "__main__":
    main()
