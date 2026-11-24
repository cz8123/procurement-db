"""供应商名称归并（数据库端，非破坏性）

问题：同一家供应商因为「大小写 / 空格 / 全半角标点」被当成两家，例如
    EntServ Malaysia Sdn. Bhd.   vs   EntServMalaysiaSdn.Bhd.
    深圳市菲格文化传媒有限公司     vs   深圳市菲格文化传媒有限公 司

做法（不改原表，只新增映射表）：
    1. norm_key()：全半角统一（NFKC）→ 去掉所有空白（含全角空格/不换行空格）
       → 去掉标点 → casefold，得到归一键；
    2. 同一归一键下的多个原文，选一个「规范名」（频次最高 + 格式最规范）；
    3. 写入 dim_supplier_alias（别名 → 规范名），来源 auto / manual；
       manual 行永不被自动重算覆盖，供人工纠偏（同名但确属两家公司时）；
    4. 建视图 vw_supplier_dedup，分析时 JOIN 它即可按规范名聚合。

用法：
    python supplier_dedup.py --report     # 只读，列出会被合并的组（默认）
    python supplier_dedup.py --apply      # 建/更新 dim_supplier_alias + 视图
"""
import argparse
import re
import sqlite3
import unicodedata
from collections import defaultdict

DB = "procurement.db"

_WS = re.compile(r"[\s\u00a0\u3000]+")
# 归一时去掉的标点（保留中文汉字/字母/数字）；点号是 Sdn.Bhd 这类差异的主要来源
_PUNCT = re.compile(r"[.,，。、;；:：'\"“”‘’()（）\[\]【】\-—_/\\&＆]")
_CO_LTD = re.compile(r"co\.?\s*,?\s*ltd\.?$", re.I)
_CAMEL = re.compile(r"[a-z][A-Z]")
_CJK = re.compile(r"[\u4e00-\u9fff]")


def norm_key(s):
    """归一键，分三种情况：

    1. 「中文全称-英文」→ 用中文全称。中国公司全称由工商登记保证唯一，
       所以 `…深圳分公司-Ping An Property & Casualty…` 与 `…-China Ping An…`
       是同一家；也顺带合并「纯中文」与「中文-英文」两种写法。
    2. 含中文但不是「中文-英文」结构（如 `salesforce.com新加坡有限公司-…`）
       → 去掉空白与标点后整串小写。
    3. 纯英文 → 去掉空白与标点后小写（`EntServ Malaysia Sdn. Bhd.` = `EntServMalaysiaSdn.Bhd.`）。
    """
    if s is None:
        return ""
    t = _WS.sub("", unicodedata.normalize("NFKC", str(s)))
    # 1) 中文全称在前、英文在后：取「最后一个中文后面、且其后不再有中文」的分隔符。
    #    不能取第一个 —— `中外运-敦豪国际航空快件…-Sinotrans-DHL…` 的连字符在公司名内部；
    #    也不能取「后面还有中文」的 —— 那是同一中文名里的连字符。
    cut = -1
    for i, ch in enumerate(t):
        if ch in "-_" and i and _CJK.match(t[i - 1]) and not _CJK.search(t[i + 1:]):
            cut = i
    if cut > 0:
        head = _PUNCT.sub("", t[:cut])
        if len(_CJK.findall(head)) >= 2:
            return "cn:" + head.casefold()
    # 2) 英文在前、中文全称结尾（`Dell (China) Co. LTD-戴尔中国有限公司`）：用尾部中文块
    #    仅当尾部中文块之前没有别处中文时才用（否则 `中外运-敦豪…分公司` 会被截掉「中外运」）。
    m = re.search(r"([\u4e00-\u9fff（）()·]{4,})$", t)
    if m and m.start() > 0 and not _CJK.search(t[:m.start()]):
        return "cn:" + _PUNCT.sub("", m.group(1)).casefold()
    t = _PUNCT.sub("", t)
    return ("cn:" if _CJK.search(t) else "") + t.casefold()


def _format_score(name):
    """同一组里挑规范名用的格式分：偏好「词间有空格 + Co., Ltd. 标准写法」。"""
    s = 0.0
    if _CO_LTD.search(name):
        s += 3
    s += min(name.count(" "), 8) * 0.25          # 有空格分隔比连写更像规范名
    if "LTD" in name:                             # 全大写后缀通常是随手敲的
        s -= 2
    if _CAMEL.search(name):                        # SoftwareOneExperts 这类驼峰连写
        s -= 1.5
    if name != name.strip():                       # 首尾带空白
        s -= 1
    latin = sum(1 for ch in name if ch.isascii() and ch.isalpha())
    if latin and all(ch.isupper() for ch in name if ch.isascii() and ch.isalpha()):
        s -= 4                                     # 整串全大写（ENTSERV...）明显是脏数据
    elif " " not in name and latin >= 8:
        s -= 4                                     # 长英文名完全不空格：连写脏数据
    return s


def pick_canonical(items):
    """items: [(原文名, 出现次数)] → 规范名。

    综合分 = 格式分 × 10 + √次数：格式分足够高时，宁可选出现次数少的规范写法
    （`EntServ Malaysia Sdn. Bhd.` 优于出现更多的 `EntServMalaysiaSdn.Bhd.`），
    因为归并后所有行都显示它，可读性比频次更重要；次数仍有保底权重。
    优先在「首尾无多余空白」的候选里选，避免规范名自己带空格尾巴。"""
    clean = [(n, c) for n, c in items if n == n.strip()] or items
    return sorted(clean, key=lambda x: (-(_format_score(x[0]) * 10 + min(x[1], 300) ** 0.5),
                                        len(x[0])))[0][0]


def collect(con):
    """收集两张事实表里的供应商原文及出现次数（fct_po 与 fct_prpo 合并计数）。"""
    cnt = defaultdict(int)
    for sql in ('SELECT 供应商名称, COUNT(*) FROM fct_po '
                'WHERE 供应商名称 IS NOT NULL AND TRIM(供应商名称)<>\'\' GROUP BY 1',
                'SELECT 供应商名称, COUNT(*) FROM fct_prpo '
                'WHERE 供应商名称 IS NOT NULL AND TRIM(供应商名称)<>\'\' GROUP BY 1'):
        for name, n in con.execute(sql):
            cnt[name] += n
    return cnt


def group_variants(cnt):
    """{归一键: [(原文, 次数)]}，只返回有 2 个及以上原文的组。"""
    g = defaultdict(list)
    for name, n in cnt.items():
        g[norm_key(name)].append((name, n))
    return {k: v for k, v in g.items() if len(v) > 1}


def ensure_table(con):
    con.execute('''CREATE TABLE IF NOT EXISTS dim_supplier_alias(
        别名 TEXT PRIMARY KEY, 规范名 TEXT NOT NULL, 归一键 TEXT,
        来源 TEXT NOT NULL DEFAULT 'auto', 备注 TEXT)''')
    con.execute('CREATE INDEX IF NOT EXISTS ix_sup_alias_norm ON dim_supplier_alias(归一键)')
    con.execute('''CREATE VIEW IF NOT EXISTS vw_supplier_dedup AS
        SELECT 别名 AS 原文名, 规范名, 归一键, 来源 FROM dim_supplier_alias''')


def build(con, apply=False, quiet=False):
    cnt = collect(con)
    groups = group_variants(cnt)
    ensure_table(con)

    manual = {r[0]: r[1] for r in con.execute(
        "SELECT 别名, 规范名 FROM dim_supplier_alias WHERE 来源='manual'")}

    rows, merged_rows = [], 0
    for key, items in sorted(groups.items(), key=lambda x: -sum(n for _, n in x[1])):
        canon = manual.get(items[0][0]) or pick_canonical(items)
        # 组内若有人工指定，优先用它作为整组规范名
        for nm, _ in items:
            if nm in manual:
                canon = manual[nm]
                break
        for nm, n in items:
            if nm in manual:                       # 人工结论不动
                rows.append((nm, manual[nm], key, "manual", None))
                continue
            rows.append((nm, canon, key, "auto", None))
            if nm != canon:
                merged_rows += n

    # 规范名自身也要有一行，保证 JOIN 后不丢
    keys = {r[0] for r in rows}
    for key in groups:
        canon = next((r[1] for r in rows if r[2] == key), None)
        if canon and canon not in keys:
            rows.append((canon, canon, key, "auto", None))
            keys.add(canon)

    if not quiet:
        print("原文名 %d 个 → 归一后 %d 个（需合并的组 %d，涉及原文 %d 个，可合并行 %d）"
              % (len(cnt), len({norm_key(k) for k in cnt}), len(groups),
                 sum(len(v) for v in groups.values()), merged_rows))
        for key, items in sorted(groups.items(), key=lambda x: -sum(n for _, n in x[1]))[:12]:
            canon = next((r[1] for r in rows if r[2] == key), "?")
            print("  · %s" % canon[:46])
            for nm, n in sorted(items, key=lambda x: -x[1]):
                mark = "=" if nm == canon else "~"
                print("      %s %-58s %d" % (mark, nm[:58], n))

    if not apply:
        if not quiet:
            print("\n（只读模式）确认无误后执行：python supplier_dedup.py --apply")
        return

    backup = con.execute("SELECT COUNT(*) FROM dim_supplier_alias").fetchone()[0]
    con.execute("DELETE FROM dim_supplier_alias WHERE 来源='auto'")
    con.executemany(
        'INSERT OR REPLACE INTO dim_supplier_alias(别名,规范名,归一键,来源,备注) '
        'VALUES (?,?,?,?,?)', rows)
    con.commit()
    if quiet:
        return
    print("\n已写入 dim_supplier_alias：%d 行（原 %d 行，manual 行保留）。" % (len(rows), backup))
    print("视图 vw_supplier_dedup 可用；分析时按规范名聚合：")
    print("  SELECT COALESCE(a.规范名, f.供应商名称) 供应商, SUM(...) FROM fct_po f "
          "LEFT JOIN vw_supplier_dedup a ON a.原文名=f.供应商名称 GROUP BY 1")


def main():
    ap = argparse.ArgumentParser(description="供应商名称归并（空格/大小写/标点变体）")
    ap.add_argument("--report", action="store_true", help="只读：列出会被合并的组（默认）")
    ap.add_argument("--apply", action="store_true", help="写入 dim_supplier_alias 与视图")
    ap.add_argument("--db", default=DB)
    args = ap.parse_args()
    con = sqlite3.connect(args.db)
    try:
        build(con, apply=args.apply)
    finally:
        con.close()


if __name__ == "__main__":
    main()
