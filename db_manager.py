# -*- coding: utf-8 -*-
"""
采购数据库管理脚本 (SQLite)
=================================
用途：
  1) 把数据导入本地 SQLite 数据库 procurement.db（15 张表）
  2) 事实表（6 张）优先从 KUBE采购数据导出.xlsx 的工作表读取；维度表（9 张）读 CSV
  3) 每张表对应一个数据源，维度/主数据表与事实表之间通过业务键建立外键映射
  4) 数据更新遵循「新数据追加、重复数据跳过」原则（基于主键 INSERT OR IGNORE）

用法：
  python db_manager.py build     # 首次建库并全量导入
  python db_manager.py update    # 重新导入（追加新行、跳过已存在主键）-> 日常数据更新
  python db_manager.py verify    # 校验表行数 + 外键孤儿（数据质量）报告
  python db_manager.py tables    # 列出所有表与字段

说明：
  - 事实表数据源为 xlsx（KUBE采购数据导出.xlsx，需 pip install openpyxl）；
    找不到 xlsx/工作表时自动回退到同名 CSV；维度表仍读 CSV
  - CSV 编码自动探测 (utf-8-sig / utf-8 / gbk / gb18030)
  - 表名使用中文原名（与源文件一致），便于 Power BI 直接识别
  - 金额类字段自动解析为 REAL（去除 ¥、逗号），便于分析
  - 主键采用业务自然键（表单编号 / 参考编号 / 供应商编码 / 公司名 等）
"""

import csv
import os
import sqlite3
import sys

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "procurement.db")

# ---------------------------------------------------------------------------
# 文件 -> 表 映射配置
#   filename: (table_name, pk_header, kind)
#   kind: 'dim' 维度/主数据表 ; 'fact' 事实/交易表
# ---------------------------------------------------------------------------
FILE_MAP = {
    "部门映射表.csv": ("dim_department", "DepartmentName", "dim"),
    "采购方法表.csv": ("dim_procurement_method", "ProcurementMethod", "dim"),
    "采购类别表.csv": ("dim_category", "Category", "dim"),
    "采购子类别表.csv": ("dim_subcategory", "SubCategory", "dim"),
    "公司映射表.csv": ("dim_company", "Company", "dim"),
    "供应商属性表.csv": ("dim_supplier_attribute", "SupplierCate", "dim"),
    "供应商属性详情表.csv": ("dim_supplier_attribute_detail", "CateDetail", "dim"),
    "豁免单一理由表.csv": ("dim_exception_reason", "ExceptionReason", "dim"),
    "支出类型表.csv": ("dim_expense_type", "ExpenseType", "dim"),
    "供应商列表_搜索提交_20260924150045.csv": ("fct_supplier", "Vendor Code", "fact"),
    "集采项目前置审批_搜索提交_20260924150204.csv": ("fct_preapproval", "参考编号", "fact"),
    "框架协议_搜索提交_20260924150133.csv": ("fct_framework_agreement", "合同编号（中文）", "fact"),
    "物料清单_搜索提交_20260924150459.csv": ("fct_bom", "参考编号", "fact"),
    "Purchase Requisition and Purchase Order (PRPO) Form-采购申请和采购订单（PRPO）表格_搜索提交_20260924144802.csv": ("fct_prpo", "表单编号", "fact"),
    "Purchase Order-采购订单（仅供查看与下载）_搜索提交_20260924150323.csv": ("fct_po", "表单编号", "fact"),
}

# 导入顺序：维度表优先，事实表按依赖顺序（被引用的先建/先入）
LOAD_ORDER = [
    "部门映射表.csv",
    "采购方法表.csv",
    "采购类别表.csv",
    "采购子类别表.csv",
    "公司映射表.csv",
    "供应商属性表.csv",
    "供应商属性详情表.csv",
    "豁免单一理由表.csv",
    "支出类型表.csv",
    "供应商列表_搜索提交_20260924150045.csv",
    "集采项目前置审批_搜索提交_20260924150204.csv",
    "框架协议_搜索提交_20260924150133.csv",
    "物料清单_搜索提交_20260924150459.csv",
    "Purchase Requisition and Purchase Order (PRPO) Form-采购申请和采购订单（PRPO）表格_搜索提交_20260924144802.csv",
    "Purchase Order-采购订单（仅供查看与下载）_搜索提交_20260924150323.csv",
]

# ---------------------------------------------------------------------------
# Excel 源配置：KUBE采购数据导出.xlsx 一张工作簿容纳 6 张事实表
#   维度表（9 张）不在导出范围内，仍读 CSV；事实表优先读 xlsx，找不到才回退 CSV
#   XLSX_SHEET_OF_TABLE: 表名 -> xlsx 工作表名
# ---------------------------------------------------------------------------
XLSX_SHEET_OF_TABLE = {
    "fct_supplier": "供应商列表",
    "fct_preapproval": "集采项目前置审批",
    "fct_framework_agreement": "框架协议",
    "fct_bom": "物料清单",
    "fct_po": "采购订单PO",
    "fct_prpo": "采购申请和采购订单PRPO",
}

# 新导出把个别列名改了写法，映射回库中现有列名（否则插入时报"列不存在"）
HEADER_ALIASES = {
    "总采购订单总额（人民币）": "总采购订单总额（人民币)",
    "这是一项与数字技术（DT）相关的采购吗？": "这是一项与数字技术（\"DT\"）相关的采购吗？",
}

# 新导出中与维度表值存在个别文字差异的已知变体 -> 维度表标准值
#   结构: {表名: {列名: {变体: 标准值}}}；在导入时逐行归一化，避免产生孤儿
VALUE_ALIASES = {
    "fct_prpo": {
        "支出类型": {
            "Operational Expenditure-OPEX - 营运开支及授权申请":
                "Operational Expenditure-OPEX - 营运开支及授权申请表",
        },
    },
}

# ---------------------------------------------------------------------------
# 历史命名迁移：把旧表名升级到当前命名（幂等，仅供已有库升级用）
#   供应商列表按业务口径属「事实表」，表名由历史 dim_supplier 改为 fct_supplier
# ---------------------------------------------------------------------------
RENAMES = [
    ("dim_supplier", "fct_supplier"),
    # 公司位置(经纬度)按业务口径由维度表改为事实表
    ("dim_company_location", "fct_company_location"),
]

# ---------------------------------------------------------------------------
# 外键映射关系（事实表列 -> 维度/主数据表(列)）
#   用于在 SQLite 中声明 FOREIGN KEY，并使 Power BI 能建立关系
# ---------------------------------------------------------------------------
FKS = [
    # 采购订单 PO
    ("fct_po", "公司", "dim_company", "Company"),
    ("fct_po", "部门", "dim_department", "DepartmentName"),
    ("fct_po", "已批准的采购申请参考", "fct_prpo", "表单编号"),
    # PRPO 主表
    ("fct_prpo", "公司", "dim_company", "Company"),
    ("fct_prpo", "部门", "dim_department", "DepartmentName"),
    ("fct_prpo", "支出类型", "dim_expense_type", "ExpenseType"),
    ("fct_prpo", "采购类别", "dim_category", "Category"),
    ("fct_prpo", "豁免单一来源的理由", "dim_exception_reason", "ExceptionReason"),
    ("fct_prpo", "单一来源采购理由", "dim_exception_reason", "ExceptionReason"),
    ("fct_prpo", "框架合同编号", "fct_framework_agreement", "合同编号（中文）"),
    # 框架协议
    ("fct_framework_agreement", "前置审批单号", "fct_preapproval", "参考编号"),
    # 物料清单
    ("fct_bom", "合同编号CN", "fct_framework_agreement", "合同编号（中文）"),
    # 集采前置审批
    ("fct_preapproval", "采购类别", "dim_category", "Category"),
]

# ---------------------------------------------------------------------------
# 派生列（规范化关联键 supplier_cn / procurement_method_key / supplier_attribute_key）
# 已按业务决定不再导入生成，故此处不再产生派生键；保留空字典与函数签名以兼容既有引用。
# 历史维度匹配如需关联，请基于原始列（供应商名称 / 选择采购方法 / 供应商属性）进行。
# ---------------------------------------------------------------------------
import re

EXTRA = {}


def compute_extras(table, raw_dict):
    # 派生列已不再生成，恒返回空列表（保留签名以兼容 load_table）
    return []

AMOUNT_KEYWORDS = ("金额", "总额", "总价", "AMOUNT", "Amount", "amount")


def qid(name):
    """安全包裹 SQL 标识符，转义名称内部的双引号。"""
    return '"' + str(name).replace('"', '""') + '"'


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------
def detect_encoding(path):
    for enc in ("utf-8-sig", "utf-8", "gbk", "gb18030"):
        try:
            with open(path, "r", encoding=enc, newline="") as fh:
                fh.read(4096)
            return enc
        except (UnicodeDecodeError, UnicodeError):
            continue
    return "latin-1"


def parse_amount(value):
    """把 '¥5,978.19' / 'HK$ 123' 等解析为 float，空或非法返回 None。"""
    if value is None:
        return None
    s = str(value).strip()
    if s == "":
        return None
    s = s.replace("¥", "").replace("￥", "").replace("$", "").replace(",", "").replace(" ", "")
    if s == "":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def is_amount_header(header):
    return any(k in header for k in AMOUNT_KEYWORDS)


def get_csv_meta(path):
    """返回 (encoding, delimiter, raw_header, data_row_count)。"""
    enc = detect_encoding(path)
    with open(path, "r", encoding=enc, newline="", errors="replace") as fh:
        sample = fh.read(8192)
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=[",", ";", "\t", "|"])
        delim = dialect.delimiter
    except Exception:
        delim = ","
    return enc, delim


def dedupe_headers(raw_header):
    """处理重复列名（如两个 'Quoted Amount (HKD/MOP)'），返回去重后的列名列表。"""
    seen = {}
    colnames = []
    for i, h in enumerate(raw_header):
        base = (h or f"col{i}").strip()
        if base in seen:
            seen[base] += 1
            name = f"{base}_{seen[base]}"
        else:
            seen[base] = 1
            name = base
        colnames.append(name)
    return colnames


def apply_header_aliases(raw_header):
    """把新导出中改了写法的列名映射回库中现有列名。"""
    return [HEADER_ALIASES.get(h, h) for h in raw_header]


# ---------------------------------------------------------------------------
# 数据源抽象：xlsx 工作表（新导出，优先）/ CSV 文件（旧格式，回退）
#   两者都提供 header() 与 rows() 两个接口，导入逻辑与格式解耦
# ---------------------------------------------------------------------------
class CsvSource:
    """CSV 文件数据源（每表一个文件的历史格式）。"""

    def __init__(self, path):
        self.path = path
        self.enc, self.delim = get_csv_meta(path)

    def header(self):
        with open(self.path, "r", encoding=self.enc, newline="", errors="replace") as fh:
            return next(csv.reader(fh, delimiter=self.delim))

    def rows(self):
        with open(self.path, "r", encoding=self.enc, newline="", errors="replace") as fh:
            reader = csv.reader(fh, delimiter=self.delim)
            next(reader)  # 跳过表头
            for r in reader:
                yield r

    def close(self):
        pass


class XlsxSource:
    """Excel 工作簿数据源（KUBE采购数据导出.xlsx，一张工作表一张事实表）。

    依赖 openpyxl（pip install openpyxl）；read_only 模式流式读取，内存友好。
    单元格可能是 str/int/float/datetime/bool，由 load_table 统一归一化。
    """

    def __init__(self, path, sheet):
        self.path = path
        self.sheet = sheet
        self.desc = ""
        self._wb = None

    def _open(self):
        if self._wb is None:
            try:
                import openpyxl
            except ImportError:
                raise SystemExit("读取 xlsx 需要 openpyxl，请先执行: pip install openpyxl")
            self._wb = openpyxl.load_workbook(self.path, read_only=True, data_only=True)
        return self._wb

    def header(self):
        ws = self._open()[self.sheet]
        for row in ws.iter_rows(values_only=True):
            return ["" if h is None else str(h).strip() for h in row]
        return []

    def rows(self):
        ws = self._open()[self.sheet]
        it = ws.iter_rows(values_only=True)
        next(it, None)  # 跳过表头
        for row in it:
            if row is None:
                continue
            if all(v is None or (isinstance(v, str) and not v.strip()) for v in row):
                continue  # 跳过空行
            yield ["" if v is None else v for v in row]

    def close(self):
        if self._wb is not None:
            try:
                self._wb.close()
            finally:
                self._wb = None


def find_xlsx():
    """在项目目录查找 xlsx 导出文件（排除 ~$ 临时文件）；多个时取最新修改的。"""
    import glob
    here = os.path.dirname(os.path.abspath(__file__))
    cands = [p for p in glob.glob(os.path.join(here, "*.xlsx"))
             if not os.path.basename(p).startswith("~$")]
    if not cands:
        return None
    cands.sort(key=os.path.getmtime, reverse=True)
    return cands[0]


def open_source(filename):
    """解析数据源：优先 xlsx 工作表，其次 CSV 文件。

    返回 (source, desc)；两者都没有时返回 (None, None)。
    """
    table = FILE_MAP[filename][0]
    sheet = XLSX_SHEET_OF_TABLE.get(table)
    if sheet:
        xlsx = find_xlsx()
        if xlsx:
            src = XlsxSource(xlsx, sheet)
            try:
                if src.header():
                    src.desc = f"xlsx[{sheet}]"
                    return src, src.desc
            except KeyError:
                print(f"  提示：{os.path.basename(xlsx)} 中无工作表[{sheet}]，回退 CSV")
            except Exception as e:  # noqa: BLE001
                print(f"  提示：读取 xlsx[{sheet}] 失败（{e}），回退 CSV")
            src.close()
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)
    if os.path.exists(path):
        src = CsvSource(path)
        src.desc = "csv"
        return src, "csv"
    return None, None


# ---------------------------------------------------------------------------
# 元数据表：映射配置 / 自定义质量规则（非 CSV 来源，供 Web 管理页 CRUD）
# ---------------------------------------------------------------------------
def migrate_schema(con):
    """幂等迁移：按 RENAMES 重命名历史表，并同步元数据表里对旧表名的引用。

    由 ensure_meta_tables() 调用，因此 build / update / Web 服务启动都会自动升级。
    """
    try:
        names = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    except sqlite3.Error:
        return
    for old, new in RENAMES:
        if old in names and new not in names:
            try:
                con.execute(f"ALTER TABLE {qid(old)} RENAME TO {qid(new)}")
                for sql in (
                    "UPDATE map_config SET source_table=? WHERE source_table=?",
                    "UPDATE map_config SET target_table=? WHERE target_table=?",
                    "UPDATE quality_rules SET target_table=? WHERE target_table=?",
                    "UPDATE map_layout SET table_name=? WHERE table_name=?",
                ):
                    try:
                        con.execute(sql, (new, old))
                    except sqlite3.Error:
                        pass
                con.commit()
                print(f"[迁移] 表重命名：{old} -> {new}")
            except sqlite3.Error as e:
                print(f"[迁移] {old} -> {new} 跳过：{e}")


def ensure_meta_tables(con, seed=True):
    """创建 map_config / quality_rules 两张元数据表，并按需在 map_config 为空时
    用代码里的 FKS 常量完成首次播种。幂等，可重复调用。"""
    con.execute("""CREATE TABLE IF NOT EXISTS map_config (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source_table TEXT NOT NULL,
        source_col  TEXT NOT NULL,
        target_table TEXT NOT NULL,
        target_col  TEXT NOT NULL,
        kind TEXT NOT NULL DEFAULT 'dim',   -- dim / fact / derived
        enabled INTEGER NOT NULL DEFAULT 1,
        note TEXT,
        advice_override TEXT,               -- recommend / optional / not_needed / NULL
        ignored INTEGER NOT NULL DEFAULT 0
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS quality_rules (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        target_table TEXT NOT NULL,
        check_type TEXT NOT NULL,           -- not_null / unique
        check_col TEXT,
        severity TEXT NOT NULL DEFAULT 'warn',
        note TEXT,
        enabled INTEGER NOT NULL DEFAULT 1
    )""")
    # 「模型视图」画布位置：记录每张表框被拖到哪儿（Power BI 风格模型布局）
    con.execute("""CREATE TABLE IF NOT EXISTS map_layout (
        table_name TEXT PRIMARY KEY,
        x REAL NOT NULL DEFAULT 0,
        y REAL NOT NULL DEFAULT 0
    )""")
    # 历史命名迁移（如 dim_supplier -> fct_supplier），须在元数据表建好之后执行
    migrate_schema(con)
    # 兼容已有库：补充 cardinality 列（Power BI 风格基数徽章 *:1 / 1:* / 1:1）
    try:
        _mc = [r[1] for r in con.execute("PRAGMA table_info(map_config)").fetchall()]
        if "cardinality" not in _mc:
            con.execute("ALTER TABLE map_config ADD COLUMN cardinality TEXT")
        # 一次性回填历史空值：事实关联按 1:1、维度/派生映射按 *:1（幂等，已填的不动）
        con.execute(
            "UPDATE map_config SET cardinality = CASE WHEN kind='fact' THEN '1:1' ELSE '*:1' END "
            "WHERE cardinality IS NULL OR TRIM(cardinality) = ''")
    except sqlite3.Error:
        pass
    # 兼容已有库：补充 height 列（模型视图卡片拖底边调整后的高度，持久化）
    try:
        _ml = [r[1] for r in con.execute("PRAGMA table_info(map_layout)").fetchall()]
        if "height" not in _ml:
            con.execute("ALTER TABLE map_layout ADD COLUMN height REAL")
    except sqlite3.Error:
        pass
    if seed:
        cur = con.execute("SELECT COUNT(*) FROM map_config").fetchone()[0]
        if cur == 0:
            _derived = {"supplier_cn", "procurement_method_key", "supplier_attribute_key"}
            for (t, col, rt, rc) in FKS:
                kind = "derived" if col in _derived else ("dim" if rt.startswith("dim_") else "fact")
                _card = "*:1" if kind in ("dim", "derived") else "1:1"
                con.execute(
                    "INSERT INTO map_config "
                    "(source_table,source_col,target_table,target_col,kind,enabled,cardinality) "
                    "VALUES (?,?,?,?,?,1,?)", (t, col, rt, rc, kind, _card))
    con.commit()


def get_fks(con):
    """返回当前生效的映射关系列表 [(源表,源列,目标表,目标列), ...]。

    优先读取 map_config（支持 Web 页 CRUD），表不存在时回退到代码常量 FKS。
    """
    try:
        rows = con.execute(
            "SELECT source_table,source_col,target_table,target_col "
            "FROM map_config WHERE enabled=1"
        ).fetchall()
        if rows:
            return [(r[0], r[1], r[2], r[3]) for r in rows]
    except sqlite3.Error:
        pass
    return list(FKS)


# ---------------------------------------------------------------------------
# 建表
# ---------------------------------------------------------------------------
def create_table(con, filename, source):
    table, pk_header, kind = FILE_MAP[filename]
    raw_header = apply_header_aliases(source.header())
    csv_colnames = dedupe_headers(raw_header)
    amount_idx = {i for i, h in enumerate(raw_header) if is_amount_header(h)}

    if pk_header not in raw_header:
        raise SystemExit(
            f"[{table}] 数据源表头中找不到主键列 {pk_header!r}"
            f"（表头前几项: {raw_header[:8]}）")
    pk_idx = raw_header.index(pk_header)
    pk_col = csv_colnames[pk_idx]

    col_defs = []
    for i, name in enumerate(csv_colnames):
        ctype = "REAL" if i in amount_idx else "TEXT"
        col_defs.append(f"{qid(name)} {ctype}")

    extra_cols = EXTRA.get(table, [])
    for cname, ctype in extra_cols:
        col_defs.append(f"{qid(cname)} {ctype}")

    colnames = csv_colnames + [c for c, _ in extra_cols]

    fk_clauses = []
    for (t, col, rt, rc) in get_fks(con):
        if t == table:
            fk_clauses.append(f"FOREIGN KEY({qid(col)}) REFERENCES {qid(rt)}({qid(rc)})")

    sql = f"CREATE TABLE IF NOT EXISTS {qid(table)} (\n  " + ",\n  ".join(col_defs)
    sql += f",\n  PRIMARY KEY({qid(pk_col)})"
    if fk_clauses:
        sql += ",\n  " + ",\n  ".join(fk_clauses)
    sql += "\n)"
    con.execute(sql)
    return {
        "table": table,
        "colnames": colnames,
        "csv_colnames": csv_colnames,
        "amount_idx": amount_idx,
        "pk_col": pk_col,
        "pk_idx": pk_idx,
        "raw_header": raw_header,
    }


# ---------------------------------------------------------------------------
# 导入单表
# ---------------------------------------------------------------------------
def load_table(con, filename, meta, source, verbose=True):
    table = meta["table"]
    colnames = meta["colnames"]          # 数据源列 + 派生列
    amount_idx = meta["amount_idx"]
    raw_header = meta["raw_header"]

    # 只插入目标表中真实存在的列：更新场景下表结构可能比数据源旧
    # （缺列 -> 新行该列为 NULL；多列 -> 跳过并提示，由用户决定是否 ALTER TABLE）
    table_cols = [r[1] for r in con.execute(f"PRAGMA table_info({qid(table)})").fetchall()]
    keep = [i for i, c in enumerate(colnames) if c in table_cols]
    dropped = [c for i, c in enumerate(colnames) if c not in table_cols]
    if dropped and verbose:
        print(f"  [{table}] 数据源中以下列在表里不存在，已跳过: {dropped}")
    insert_cols = [colnames[i] for i in keep]
    placeholders = ",".join("?" * len(insert_cols))
    col_list = ",".join(qid(c) for c in insert_cols)
    insert_sql = f"INSERT OR IGNORE INTO {qid(table)} ({col_list}) VALUES ({placeholders})"

    aliases = VALUE_ALIASES.get(table, {})
    n_src = len(raw_header)
    rows = []
    for r in source.rows():
        r = list(r)
        if len(r) < n_src:
            r = r + [None] * (n_src - len(r))
        elif len(r) > n_src:
            r = r[:n_src]
        raw_dict = dict(zip(raw_header, r))
        # 已知文本变体归一化到维度表标准值（如新导出少了"表"字的支出类型）
        for col, amap in aliases.items():
            if col in raw_dict:
                v = raw_dict[col]
                if v is not None and str(v).strip() in amap:
                    r[raw_header.index(col)] = amap[str(v).strip()]
        extras = compute_extras(table, raw_dict)
        full = r + extras
        values = []
        for i in keep:
            v = full[i]
            if v is None:
                values.append(None)
            elif isinstance(v, str):
                s = v.strip()
                if s == "":
                    values.append(None)
                elif i in amount_idx:
                    values.append(parse_amount(v))
                else:
                    values.append(v)
            elif isinstance(v, bool):  # Excel 布尔单元格
                values.append("TRUE" if v else "FALSE")
            elif i in amount_idx:
                values.append(parse_amount(v))
            else:  # int/float/datetime 等统一转文本，与 CSV 导入行为一致
                values.append(str(v))
        rows.append(tuple(values))

    before = con.total_changes
    if rows:
        con.executemany(insert_sql, rows)
    after = con.total_changes
    inserted = after - before
    skipped = len(rows) - inserted
    if verbose:
        desc = getattr(source, "desc", "") or "csv"
        print(f"  [{table}] <- {desc}  数据行数={len(rows):>7}  新增={inserted:>7}  跳过(重复)={skipped:>7}")
    return len(rows), inserted, skipped


def create_indexes(con):
    """为外键列建立索引，提升 Power BI 查询性能。"""
    for (t, col, rt, rc) in get_fks(con):
        idx = "idx_" + re.sub(r"[^0-9A-Za-z]", "_", f"{t}_{col}")
        con.execute(f"CREATE INDEX IF NOT EXISTS {qid(idx)} ON {qid(t)}({qid(col)})")


# ---------------------------------------------------------------------------
# 命令
# ---------------------------------------------------------------------------
def run_import(con):
    """按 LOAD_ORDER 导入所有有数据源的表（建表如需要 + 逐行插入）。

    返回 (missing, stats)：
      missing: 无数据源（CSV/xlsx 均未找到）的文件名列表
      stats:  [(table, desc, rows, inserted, skipped), ...]
    """
    missing, stats = [], []
    for fn in LOAD_ORDER:
        table = FILE_MAP[fn][0]
        source, desc = open_source(fn)
        if source is None:
            missing.append(fn)
            continue
        try:
            meta = create_table(con, fn, source)
            n, ins, skip = load_table(con, fn, meta, source)
            stats.append((table, desc, n, ins, skip))
        finally:
            source.close()
    return missing, stats


def cmd_build():
    print(f"目标数据库: {DB_PATH}")
    con = sqlite3.connect(DB_PATH)
    con.execute("PRAGMA foreign_keys=OFF;")
    ensure_meta_tables(con)   # 先建元数据表并播种映射配置，供后续建表引用
    missing, stats = run_import(con)
    con.commit()
    if missing:
        print("警告：以下表没有数据源（CSV / xlsx 均未找到），未建表：")
        for fn in missing:
            print(f"  - {FILE_MAP[fn][0]}（期望 CSV: {fn}）")
    create_indexes(con)
    con.commit()
    con.close()
    total_new = sum(s[3] for s in stats)
    print(f"完成。共新增 {total_new} 行。数据库文件: {DB_PATH}\n")
    cmd_verify()


def cmd_update():
    print(f"更新数据库: {DB_PATH}（追加新数据 / 跳过已存在主键）")
    if not os.path.exists(DB_PATH):
        print("数据库不存在，转为全量 build。")
        return cmd_build()
    con = sqlite3.connect(DB_PATH)
    con.execute("PRAGMA foreign_keys=OFF;")
    ensure_meta_tables(con)   # 确保元数据表存在（不覆盖用户已编辑的映射）
    missing, stats = run_import(con)
    create_indexes(con)
    con.commit()
    con.close()
    if missing:
        names = "、".join(FILE_MAP[fn][0] for fn in missing)
        print(f"提示：{len(missing)} 张表无数据源已跳过（{names}）")
    total_new = sum(s[3] for s in stats)
    total_skip = sum(s[4] for s in stats)
    print(f"完成。本次新增 {total_new} 行，跳过重复 {total_skip} 行。\n")
    cmd_verify()


def cmd_verify():
    if not os.path.exists(DB_PATH):
        print("数据库不存在，请先执行 build。")
        return
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    print("=" * 78)
    print("一、各表行数")
    print("=" * 78)
    tables = [FILE_MAP[f][0] for f in LOAD_ORDER]
    for t in tables:
        try:
            n = con.execute(f"SELECT COUNT(*) FROM {qid(t)}").fetchone()[0]
            print(f"  {t:<32} {n:>8} 行")
        except sqlite3.Error as e:
            print(f"  {t:<32} 错误: {e}")

    print("\n" + "=" * 78)
    print("二、外键关联完整性（事实表引用在目标表中不存在的行数 = 孤儿）")
    print("=" * 78)
    has_orphan = False
    for (t, col, rt, rc) in get_fks(con):
        try:
            q = (
                f"SELECT COUNT(*) FROM {qid(t)} "
                f"WHERE {qid(col)} IS NOT NULL AND {qid(col)} NOT IN (SELECT {qid(rc)} FROM {qid(rt)})"
            )
            orphan = con.execute(q).fetchone()[0]
            total = con.execute(f"SELECT COUNT(*) FROM {qid(t)} WHERE {qid(col)} IS NOT NULL").fetchone()[0]
            pct = (orphan / total * 100) if total else 0
            flag = "  <-- 注意" if orphan else ""
            print(f"  {t}.{col}  ->  {rt}.{rc}   : 孤儿 {orphan:>6} / {total:>7} ({pct:5.1f}%){flag}")
            if orphan:
                has_orphan = True
        except sqlite3.Error as e:
            print(f"  {t}.{col} -> {rt}.{rc} : 校验跳过 ({e})")
    if not has_orphan:
        print("  ✓ 全部外键引用均可命中目标表（关联完整，Power BI 可直接建关系）")
    else:
        print("  说明：存在少量孤儿多为源系统文本/编码差异，不影响整体分析；可在 Power BI 中按需清洗。")
    con.close()


def cmd_tables():
    if not os.path.exists(DB_PATH):
        print("数据库不存在，请先执行 build。")
        return
    con = sqlite3.connect(DB_PATH)
    for fn in LOAD_ORDER:
        t = FILE_MAP[fn][0]
        cur = con.execute(f'PRAGMA table_info("{t}")')
        cols = [r[1] for r in cur.fetchall()]
        print(f"\n## {t}  (来源: {fn})")
        print("   " + " | ".join(cols))
    con.close()


def cmd_meta():
    """确保元数据表（map_config / quality_rules）存在，并按需播种映射配置。"""
    if not os.path.exists(DB_PATH):
        print("数据库不存在，请先执行 build。")
        return
    con = sqlite3.connect(DB_PATH)
    ensure_meta_tables(con)
    n = con.execute("SELECT COUNT(*) FROM map_config").fetchone()[0]
    m = con.execute("SELECT COUNT(*) FROM quality_rules").fetchone()[0]
    print(f"元数据表已就绪：map_config 现有 {n} 条映射配置，quality_rules 现有 {m} 条自定义质量规则。")
    con.close()


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "build"
    if cmd == "build":
        cmd_build()
    elif cmd == "update":
        cmd_update()
    elif cmd == "verify":
        cmd_verify()
    elif cmd == "tables":
        cmd_tables()
    elif cmd == "meta":
        cmd_meta()
    else:
        print("用法: python db_manager.py [build|update|verify|tables|meta]")
