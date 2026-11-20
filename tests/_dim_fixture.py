# -*- coding: utf-8 -*-
"""为「补录 · 附带字段」测试在数据库副本里重建维度夹具。

背景：真实库的 9 张维度表已被 DROP，改由「映射聚合」生成的 dim_agg_* 顶替。
但 dim_agg_* 只有 `原始值 / 聚合值`（11-18 起不再建 源行数/写法数），没有「英文名」这类附带字段，
覆盖不到 suggest_extras() 的前缀命中 / 近邻复制。所以这里自建两张维度表 + 对应关系，
内容按事实表现有的部门/公司名铺满（否则会造出海量孤儿，把「补录」测成「批量导入」），
再刻意留出 LEAVE_OUT 里的写法不建成员，用来制造「待补录」状态。

只允许作用于 tests/ 下的副本库，调用方负责确认 DB_PATH 已重指向副本。
"""
import sqlite3

# 种子成员：(部门写法, 英文分类)。这 8 条决定了 DepartmentEn 的取值池。
SEEDS = [
    ("Sales-销售部", "Sales"),
    ("Marketing-市场部", "Marketing"),
    ("Admin-行政部", "Admin"),
    ("Aftersales-售后部", "Aftersales"),
    ("HSE-安全环保部", "HSE"),
    ("UsedCar-二手车部", "Used Car"),
    ("BD-业务发展部", "BD"),
    ("Head Office-Business Development-Property-DD-中国办公室-发展部(物业)", "BD"),
]

# 故意不建成员的写法：测试要用它们制造「事实表有、维度表没有」的补录场景。
LEAVE_OUT = [
    "Sales-销售部_RR", "Sales-销售部_LB", "Sales-MINI-销售部-Mini",
    "Marketing-MINI-市场部-Mini", "Marketing-Motorrad-市场部-摩托车",
    "Admin_BMW_行政部-BMW", "Aftersales_BMW_售后部-BMW", "BD_DD_BMW_发展部-BMW",
    "HSE-HSE", "Used Car Sales-二手车销售",
    "Head Office-Business Development-Property-中国办公室-发展部(物业)",
]

# 首词 -> 英文分类，用于给「铺满」的其余部门推断 DepartmentEn
_HEAD_MAP = {
    "sales": "Sales", "marketing": "Marketing",
    "admin": "Admin", "administration": "Admin",
    "aftersales": "Aftersales", "hse": "HSE",
    "used": "Used Car", "usedcar": "Used Car",
    "bd": "BD", "dd": "BD", "business": "BD", "head": "BD",
}


def _head_en(name):
    """按第一个英文词推断分类；推断不出返回 None（保持空白，不凭空造分类）。"""
    token = ""
    for ch in str(name or ""):
        if ch.isascii() and ch.isalpha():
            token += ch.lower()
        elif token:
            break
    return _HEAD_MAP.get(token)


def build(db_path, sample_companies=40):
    con = sqlite3.connect(db_path)
    try:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS dim_department (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            DepartmentName TEXT UNIQUE,
            DepartmentEn TEXT
        );
        CREATE TABLE IF NOT EXISTS dim_company (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            Company TEXT UNIQUE,
            Brand TEXT,
            RegionAbbreviationCN TEXT,
            CityCN TEXT,
            CompanyAbbreviation TEXT
        );
        """)
        for name, en in SEEDS:
            con.execute(
                "INSERT OR IGNORE INTO dim_department(DepartmentName, DepartmentEn) "
                "VALUES (?,?)", (name, en))

        # 把事实表里出现过的部门写法都铺上（两张事实表并集），只跳过 LEAVE_OUT
        names = set()
        for tbl in ("fct_po", "fct_prpo"):
            try:
                for (v,) in con.execute(
                        f'SELECT DISTINCT "部门" FROM {tbl} '
                        f'WHERE "部门" IS NOT NULL AND TRIM("部门") <> \'\''):
                    names.add(v)
            except sqlite3.Error:
                continue
        leave = set(LEAVE_OUT)
        for v in sorted(names):
            if v in leave:
                continue
            con.execute(
                "INSERT OR IGNORE INTO dim_department(DepartmentName, DepartmentEn) "
                "VALUES (?,?)", (v, _head_en(v)))

        # 公司夹具：从事实表取前 N 个真实公司名
        rows = con.execute(
            'SELECT DISTINCT "公司" FROM fct_po WHERE "公司" IS NOT NULL '
            "AND TRIM(\"公司\") <> '' ORDER BY 1 LIMIT ?", (sample_companies,)).fetchall()
        for i, (name,) in enumerate(rows):
            con.execute(
                "INSERT OR IGNORE INTO dim_company(Company, Brand, RegionAbbreviationCN, "
                "CityCN, CompanyAbbreviation) VALUES (?,?,?,?,?)",
                (name, name.split("-")[-1] if "-" in name else name,
                 "CN", "深圳", "C%03d" % i))
        # 真实库里 PRPO 的部门值本身很干净（变体只出现在 PO 里），若不额外造几条，
        # 「同一维度表、不同事实表」的补录用例会因为没有缺失值而空跑。
        for i, v in enumerate(["Marketing-MINI-市场部-Mini", "Admin_BMW_行政部-BMW", "HSE-HSE"]):
            con.execute(
                'INSERT OR IGNORE INTO fct_prpo ("表单编号", "部门") VALUES (?,?)',
                ("__fx_prpo_dept_%d__" % i, v))
        # 关系（真实库已随维度表一起删掉，这里补回）
        for src, col, tgt, tc in [
            ("fct_po", "部门", "dim_department", "DepartmentName"),
            ("fct_prpo", "部门", "dim_department", "DepartmentName"),
            ("fct_po", "公司", "dim_company", "Company"),
        ]:
            exists = con.execute(
                "SELECT 1 FROM map_config WHERE source_table=? AND source_col=? "
                "AND target_table=? AND target_col=?", (src, col, tgt, tc)).fetchone()
            if not exists:
                con.execute(
                    "INSERT INTO map_config(source_table,source_col,target_table,target_col,"
                    "kind,enabled,cardinality) VALUES (?,?,?,?,'dim',1,'*:1')",
                    (src, col, tgt, tc))
        con.commit()
        return {
            "department": con.execute("SELECT COUNT(*) FROM dim_department").fetchone()[0],
            "company": con.execute("SELECT COUNT(*) FROM dim_company").fetchone()[0],
        }
    finally:
        con.close()
