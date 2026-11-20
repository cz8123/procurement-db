# -*- coding: utf-8 -*-
"""
数据转换引擎（类 Power Query）
==============================
把一个「源表 + 有序步骤链」编译成 SQLite SQL（CTE 链），用于：
  · 预览任意步骤的中间结果
  · 落地为视图（实时刷新）或物化表（数据快照）

设计取舍：
  · 编译成 SQL 而不是用 pandas —— 零新依赖、结果能被 Power BI 直接消费、
    语义透明（用户能看到最终 SQL）、视图刷新即取最新数据。
  · 每步生成一个独立 CTE，既能看单步 SQL，也方便逐步定位问题。
  · 预览一律走只读连接，彻底杜绝误写。

对外：
    list_sources(con)                       -> 可用源表（含视图）
    get_columns(con, table)                 -> [{name,type,pk}]
    compile_query(con, source, steps, upto) -> {sql, columns, steps[...]}
    apply_query(con, name, source, steps, mode) -> 建视图 / 物化表
"""
import re
import time

import db_manager as db

qid = db.qid

# ---------------------------------------------------------------------------
# 步骤定义：op -> 中文名 / 说明（供前端「添加步骤」菜单直接使用）
# ---------------------------------------------------------------------------
STEP_LABELS = {
    "filter":         ("筛选行",   "按条件保留行，可多个条件"),
    "keep_columns":   ("仅保留列", "只留下选中的列"),
    "remove_columns": ("删除列",   "去掉选中的列"),
    "rename_column":  ("重命名列", "把列改成新名字"),
    "change_type":    ("更改类型", "文本 / 整数 / 小数 / 日期，金额会自动清洗"),
    "replace_value":  ("替换值",   "整体替换或按文本片段替换"),
    "clean_text":     ("清理文本", "去首尾空格 / 转小写 / 转大写"),
    "split_column":   ("拆分列",   "按分隔符拆成多列"),
    "extract_text":   ("提取文本", "取左 N 位 / 右 N 位 / 两标记之间"),
    "add_column":     ("自定义列", "用 SQL 表达式新增一列"),
    "group_by":       ("分组汇总", "按列分组并求和/计数/平均"),
    "sort":           ("排序",     "按一列或多列排序"),
    "distinct":       ("删除重复", "整行去重"),
    "limit":          ("保留前 N 行", "只取前若干行"),
    "join":           ("合并查询", "按关联键横向连接另一张表"),
    "append":         ("追加查询", "把另一张表纵向拼接进来"),
    "unpivot":        ("逆透视",   "宽表转长表（多列压成两列）"),
    "fill_down":      ("向下填充", "用上一行的值填补空值"),
}

# 数值清洗：项目里金额列存的是 '¥30,000.00' 这类文本
MONEY_REPLACES = [("HK$", ""), ("¥", ""), ("￥", ""), ("$", ""), (",", ""), (" ", "")]


def num_expr(col_sql):
    """把可能带货币符号/千分位的文本列转成数值。"""
    e = col_sql
    for a, b in MONEY_REPLACES:
        e = f"REPLACE({e}, {_lit(a)}, {_lit(b)})"
    return f"CAST({e} AS REAL)"


def _lit(v):
    """SQL 字符串字面量（单引号转义）。"""
    if v is None:
        return "NULL"
    return "'" + str(v).replace("'", "''") + "'"


def _like_pattern(v, mode):
    """构造 LIKE 模式并转义通配符。"""
    s = str(v).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    if mode == "contains":
        return f"'%{s}%'"
    if mode == "starts":
        return f"'{s}%'"
    if mode == "ends":
        return f"'%{s}'"
    return f"'{s}'"


def _is_num_type(t):
    return (t or "").upper() in ("REAL", "INTEGER", "NUMERIC", "DOUBLE", "FLOAT", "DECIMAL")


def _escape_ident(name, ctx="标识符"):
    s = str(name or "").strip()
    if not s:
        raise TransformError(f"{ctx}不能为空")
    if len(s) > 120:
        raise TransformError(f"{ctx}过长")
    return s


class TransformError(Exception):
    """步骤配置或编译错误，message 直接展示给用户。"""


# ---------------------------------------------------------------------------
# 元信息
# ---------------------------------------------------------------------------
def list_sources(con):
    """可作为转换源的库对象：普通表 + 视图（视图允许链式转换）。"""
    rows = con.execute(
        "SELECT name, type FROM sqlite_master "
        "WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%' "
        "AND name NOT LIKE 'map_%' AND name NOT LIKE 'quality_%' "
        "AND name NOT LIKE 'transform_%' ORDER BY type DESC, name"
    ).fetchall()
    out = []
    for r in rows:
        name = r["name"] if isinstance(r, dict) or hasattr(r, "keys") else r[0]
        typ = r["type"] if isinstance(r, dict) or hasattr(r, "keys") else r[1]
        try:
            n = con.execute(f"SELECT COUNT(*) FROM {qid(name)}").fetchone()[0]
        except Exception:
            n = None
        cols = get_columns(con, name)
        out.append({"name": name, "type": typ, "rows": n, "cols": len(cols)})
    return out


def get_columns(con, table):
    """返回 [{name, type, pk, store}]；表不存在抛 TransformError。

    注意 store：本项目金额列声明类型是 REAL，实际却存 '¥30,000.00' 这类文本，
    只按声明类型判断会导致求和结果为 0。这里对数值型列采样一次 typeof，
    发现实际是文本就标记 store='text'，后续聚合/比较据此决定是否清洗。
    """
    try:
        info = con.execute(f"PRAGMA table_info({qid(table)})").fetchall()
    except Exception as e:
        raise TransformError(f"读取表结构失败：{e}")
    if not info:
        raise TransformError(f"表或视图不存在：{table}")
    out = []
    for r in info:
        d = dict(r) if hasattr(r, "keys") else {"name": r[1], "type": r[2], "pk": r[5]}
        out.append({"name": d["name"], "type": (d.get("type") or "").upper(),
                    "pk": bool(d.get("pk")), "store": None})
    num_cols = [c for c in out if _is_num_type(c["type"])]
    for c in num_cols:
        try:
            vals = con.execute(
                f"SELECT DISTINCT typeof({qid(c['name'])}) FROM {qid(table)} "
                f"WHERE {qid(c['name'])} IS NOT NULL LIMIT 5").fetchall()
            kinds = {x[0] for x in vals}
            if kinds and kinds <= {"text"}:
                c["store"] = "text"
        except Exception:
            pass
    return out


def _needs_wash(col):
    """数值语义的列是否需要用 num_expr 清洗后再算（声明 REAL 实为文本）。"""
    t = (col.get("type") or "").upper()
    if not _is_num_type(t):
        return True
    return col.get("store") == "text"


def _require_col(cols, name, ctx="列"):
    names = [c["name"] for c in cols]
    if name not in names:
        raise TransformError(f"{ctx}「{name}」在当前结果中不存在")
    return name


# ---------------------------------------------------------------------------
# 编译核心
# ---------------------------------------------------------------------------
class _Builder:
    def __init__(self, con, source):
        self.con = con
        self.source = source
        self.ctes = []          # [(cte_name, body_sql)]
        self.rel = qid(source)  # 当前关系（首步就是源表）
        self.cols = get_columns(con, source)
        self.n = 0
        self.aux = 0            # 辅助 CTE 计数（_x1、_x2…）
        self.mat = set()        # 需要物化的辅助 CTE 名
        self.step_sql = []      # 每步的 CTE 片段，供前端展示

    def push(self, body, cols, label):
        """body 形如 'SELECT ... FROM {prev}'，prev 由调用方填入。"""
        self.n += 1
        name = f"s{self.n}"
        self.ctes.append((name, body))
        self.step_sql.append({"index": self.n, "label": label,
                              "sql": f"{name} AS (\n  {body}\n)",
                              "columns": [c["name"] for c in cols],
                              "types": {c["name"]: c["type"] for c in cols}})
        self.rel = qid(name)
        self.cols = cols
        return name

    def push_aux(self, body, rel, materialized=False):
        """插入一层「辅助 CTE」（如日期解析的中间量），不改变当前列、不计入步骤。

        SQLite 默认把 CTE 内联展开：一个被引用多次的中间量会被重复求值多次，
        复杂表达式会因此慢一个数量级。materialized=True 让 SQLite 物化一次再用。
        rel 传上一层的关系名（首层就是 self.rel），BODY 里用 __PREV__ 占位。
        """
        self.aux += 1
        name = f"_x{self.aux}"
        self.ctes.append((name, body.replace("__PREV__", rel)))
        if materialized:
            self.mat.add(name)
        return name

    def select_all(self):
        return ", ".join(qid(c["name"]) for c in self.cols)

    def final_sql(self, tail=""):
        if not self.ctes:
            return f"SELECT * FROM {qid(self.source)}{tail}"
        head = "WITH " + ",\n".join(
            f"{n} AS {'MATERIALIZED ' if n in self.mat else ''}(\n  {b}\n)"
            for n, b in self.ctes)
        return f"{head}\nSELECT * FROM {self.rel}{tail}"


def _sub(cols, mapping, drop=None, keep=None):
    """按当前列生成 SELECT 列表；mapping: {原列名: 表达式或新列名}。"""
    drop = set(drop or [])
    out = []
    for c in cols:
        n = c["name"]
        if n in drop:
            continue
        if keep is not None and n not in keep:
            continue
        if mapping and n in mapping:
            m = mapping[n]
            if m is None:
                continue
            out.append(m if m != n else qid(n))
        else:
            out.append(qid(n))
    return ", ".join(out)


def _apply_filter(b, step):
    col = _require_col(b.cols, step.get("column"), "筛选列")
    # 注意：比较方式用 cmp，不能复用 op —— op 是步骤类型
    op = (step.get("cmp") or step.get("compare") or "eq").lower()
    val = step.get("value")
    as_num = bool(step.get("as_number")) or _is_num_type(
        next(c["type"] for c in b.cols if c["name"] == col))
    expr = num_expr(qid(col)) if as_num else qid(col)

    if op in ("is_null", "not_null", "is_empty", "not_empty"):
        cond = {
            "is_null":   f"{qid(col)} IS NULL",
            "not_null":  f"{qid(col)} IS NOT NULL",
            "is_empty":  f"({qid(col)} IS NULL OR TRIM({qid(col)}) = '')",
            "not_empty": f"({qid(col)} IS NOT NULL AND TRIM({qid(col)}) <> '')",
        }[op]
    elif op in ("contains", "not_contains", "starts", "ends"):
        pat = _like_pattern(val, op.replace("not_", ""))
        cond = f"{qid(col)} {'NOT ' if op == 'not_contains' else ''}LIKE {pat} ESCAPE '\\'"
    elif op in ("in", "not_in"):
        vals = val if isinstance(val, (list, tuple)) else [x for x in str(val).split(",") if x.strip() != ""]
        if not vals:
            raise TransformError("「包含于」需要一个或多个值")
        if as_num:
            items = ", ".join(num_expr(_lit(v)) for v in vals)
        else:
            items = ", ".join(_lit(v) for v in vals)
        cond = f"{qid(col)} {'NOT ' if op == 'not_in' else ''}IN ({items})"
    elif op == "between":
        v1, v2 = step.get("value"), step.get("value2")
        if v1 is None or v2 is None or str(v1).strip() == "" or str(v2).strip() == "":
            raise TransformError("「介于」需要起始值和结束值")
        if as_num:
            cond = f"{expr} BETWEEN {num_expr(_lit(v1))} AND {num_expr(_lit(v2))}"
        else:
            cond = f"{qid(col)} BETWEEN {_lit(v1)} AND {_lit(v2)}"
    else:
        sym = {"eq": "=", "ne": "!=", "gt": ">", "ge": ">=", "lt": "<", "le": "<="}.get(op)
        if not sym:
            raise TransformError(f"不支持的比较方式：{op}")
        if val is None or (isinstance(val, str) and val.strip() == ""):
            # 想筛空值请用「为空(含空文本)」，这里留空视为还没填
            raise TransformError("请填写比较值（筛空值请改用「为空」）")
        cond = f"{expr} {sym} " + (num_expr(_lit(val)) if as_num else _lit(val))

    return b.push(f"SELECT {b.select_all()} FROM {b.rel} WHERE {cond}",
                  b.cols, f"筛选 {col} {op}")


def _apply_keep(b, step):
    cols = step.get("columns") or []
    if not cols:
        raise TransformError("请至少选择一列")
    for c in cols:
        _require_col(b.cols, c)
    new = [c for c in b.cols if c["name"] in cols]
    return b.push(f"SELECT {_sub(b.cols, None, keep=cols)} FROM {b.rel}", new, f"保留 {len(cols)} 列")


def _apply_remove(b, step):
    cols = step.get("columns") or []
    if not cols:
        raise TransformError("请至少选择一列")
    for c in cols:
        _require_col(b.cols, c)
    new = [c for c in b.cols if c["name"] not in cols]
    if not new:
        raise TransformError("不能删除全部列")
    return b.push(f"SELECT {_sub(b.cols, None, drop=cols)} FROM {b.rel}", new, f"删除 {len(cols)} 列")


def _apply_rename(b, step):
    old = _require_col(b.cols, step.get("column"), "原列名")
    new = _escape_ident(step.get("new_name"), "新列名")
    if new == old:
        raise TransformError("新列名与原列名相同")
    if any(c["name"] == new for c in b.cols):
        raise TransformError(f"列「{new}」已存在")
    mapping = {old: f"{qid(old)} AS {qid(new)}"}
    newcols = [{**c, "name": (new if c["name"] == old else c["name"])} for c in b.cols]
    return b.push(f"SELECT {_sub(b.cols, mapping)} FROM {b.rel}", newcols, f"重命名 {old} → {new}")


# 日期解析：真实数据里的日期长得五花八门，SQLite 的 date() 只认 ISO 8601
# （'YYYY-MM-DD'），遇到 '2025/4/30'、'31/5/2025'、'30-Sep-22' 一律返回 NULL ——
# 这就是「改成日期类型后整列变 null」的根因。下面用纯 SQL 做归一化解析，
# 不依赖任何自定义函数，这样落成视图后 Power BI 通过 ODBC 也能直接查。
_MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
           "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]


def _month_num(expr):
    """把月份段转数字：'Sep'→9、'09'→9。含字母才展开 12 层 REPLACE。"""
    e = f"UPPER(TRIM(COALESCE({expr}, '')))"
    for i, mo in enumerate(_MONTHS, 1):
        e = f"REPLACE({e}, {_lit(mo)}, {_lit('%02d' % i)})"
    return f"CASE WHEN {expr} GLOB '*[^0-9]*' THEN {e} ELSE {expr} END"


def _date_parse(b, col, order="auto", keep_raw=True):
    """为 col 生成日期解析链（若干辅助 CTE），返回 (关系名, 最终表达式)。

    支持：ISO 带/不带时间、年/月/日、日/月/年、月/日/年、日-英文月-年（含两位年）。
    order 控制「两段都 ≤12」时的歧义消解：auto（默认月/日/年）/ dmy / mdy / ymd。
    keep_raw=True 时解析不了的行保留原值（不静默丢数据），否则置 NULL。
    """
    q = qid(col)
    raw = f"TRIM(CAST({q} AS TEXT))"
    # ① 去掉时间部分，把 / . 年月 日 等各种分隔符统一成 '-'
    cut = (f"CASE WHEN instr({raw}, ' ') > 0 THEN substr({raw}, 1, instr({raw}, ' ') - 1) "
           f"ELSE {raw} END")
    norm = cut
    for a in ("/", ".", "年", "月"):
        norm = f"REPLACE({norm}, {_lit(a)}, '-')"
    norm = f"REPLACE(REPLACE({norm}, '日', ''), '　', '')"
    # 首段不是数字开头（自由文本、备注）直接短路，省掉后面的全套解析
    r0 = b.push_aux(
        f"SELECT *, CASE WHEN {raw} GLOB '[0-9]*' THEN {norm} ELSE NULL END AS _r0 "
        f"FROM __PREV__", b.rel)

    # ② 按 '-' 拆成三段
    t = "_r0"
    s1 = f"instr({t}||'-', '-')"
    r1 = f"substr({t}, {s1} + 1)"
    s2 = f"instr({r1}||'-', '-')"
    r2 = f"substr({r1}, {s2} + 1)"
    s3 = f"instr({r2}||'-', '-')"
    p1 = f"substr({t}, 1, {s1} - 1)"
    p2 = f"substr({r1}, 1, {s2} - 1)"
    p3 = f"substr({r2}, 1, {s3} - 1)"
    parts = b.push_aux(
        f"SELECT *, {p1} AS _p1, {p2} AS _p2, {p3} AS _p3 FROM __PREV__", r0)

    # ③ 月份段转数字（英文缩写 → 数字）。只有 p1/p2 可能是月份（年在前时日必定是 p3）,
    #    多映射一段就多一层 12 段 REPLACE 的展开，实测这里是日期解析的主要成本
    mons = b.push_aux(
        f"SELECT *, {_month_num('_p1')} AS _m1, {_month_num('_p2')} AS _m2 "
        f"FROM __PREV__", parts)

    # ④ 判定年 / 月 / 日
    y, mo, dy = "CAST(_p1 AS INTEGER)", "CAST(_m1 AS INTEGER)", "CAST(_m2 AS INTEGER)"
    year = (f"CASE WHEN _p1 GLOB '[0-9][0-9][0-9][0-9]' THEN {y} "
            f"WHEN _p3 GLOB '[0-9][0-9][0-9][0-9]' THEN CAST(_p3 AS INTEGER) "
            f"WHEN _p3 GLOB '[0-9][0-9]' THEN CASE WHEN CAST(_p3 AS INTEGER) < 70 "
            f"THEN 2000 + CAST(_p3 AS INTEGER) ELSE 1900 + CAST(_p3 AS INTEGER) END "
            f"ELSE NULL END")
    # 年在前（2025/4/30）：固定按 年-月-日 解析，不做月日换位 ——
    # '2024-13-01' 就是「13 月」写错了，应当判为失败而不是猜成 1 月 13 日
    front_m = "CAST(_m2 AS INTEGER)"
    front_d = "CAST(_p3 AS INTEGER)"
    if order == "dmy":
        back_m, back_d = "CAST(_m2 AS INTEGER)", "CAST(_m1 AS INTEGER)"
    elif order in ("mdy", "ymd"):
        back_m, back_d = "CAST(_m1 AS INTEGER)", "CAST(_m2 AS INTEGER)"
    else:                                   # auto：哪段 >12 就认定它是日
        back_m = ("CASE WHEN CAST(_m1 AS INTEGER) > 12 THEN CAST(_m2 AS INTEGER) "
                  "WHEN CAST(_m2 AS INTEGER) > 12 THEN CAST(_m1 AS INTEGER) "
                  "ELSE CAST(_m1 AS INTEGER) END")
        back_d = ("CASE WHEN CAST(_m1 AS INTEGER) > 12 THEN CAST(_m1 AS INTEGER) "
                  "WHEN CAST(_m2 AS INTEGER) > 12 THEN CAST(_m2 AS INTEGER) "
                  "ELSE CAST(_m2 AS INTEGER) END")
    month = f"CASE WHEN _p1 GLOB '[0-9][0-9][0-9][0-9]' THEN {front_m} ELSE {back_m} END"
    day = f"CASE WHEN _p1 GLOB '[0-9][0-9][0-9][0-9]' THEN {front_d} ELSE {back_d} END"
    ym = b.push_aux(
        f"SELECT *, {year} AS _y, {month} AS _mo, {day} AS _dy FROM __PREV__", mons)

    # ⑤ 拼回 ISO 并校验。用 date(iso) = iso 而不是 IS NOT NULL：
    # SQLite 会把 '2024-02-30' 静默修正成 '2024-03-01'，只判非空会放过不存在的日期
    iso = "printf('%04d-%02d-%02d', _y, _mo, _dy)"
    ok = (f"_y BETWEEN 1900 AND 2100 AND _mo BETWEEN 1 AND 12 "
          f"AND _dy BETWEEN 1 AND 31 AND date({iso}) = {iso}")
    # 失败时回退到原值。raw 是 TRIM 过的，不能用它回退 —— 纯空格会被抹成空串，
    # 而且「不像日期」被短路成 NULL 时也会丢掉原值
    fallback = f"CAST({q} AS TEXT)" if keep_raw else "NULL"
    parsed = f"CASE WHEN {ok} THEN {iso} ELSE {fallback} END"
    # 本来就是标准 ISO 的走快通道，跳过整套解析（提交日期这类列能快 10 倍）。
    # 光格式匹配不够：'2024-02-30' 格式合法但 date() 会静默修正成 3-01，
    # '1899-05-06' 更是越界 —— 所以要求 date() 原样吐回来，否则交给完整解析判
    final = (f"CASE WHEN {raw} GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]*' "
             f"AND substr({raw}, 1, 4) BETWEEN '1900' AND '2100' "
             f"AND date(substr({raw}, 1, 10)) = substr({raw}, 1, 10) "
             f"THEN substr({raw}, 1, 10) ELSE {parsed} END")
    return ym, final


def _apply_change_type(b, step):
    col = _require_col(b.cols, step.get("column"), "列")
    typ = (step.get("type") or "text").lower()
    if typ in ("number", "real", "decimal"):
        expr = num_expr(qid(col))
        newtype = "REAL"
        src = b.rel
    elif typ in ("integer", "int"):
        expr = f"CAST({num_expr(qid(col))} AS INTEGER)"
        newtype = "INTEGER"
        src = b.rel
    elif typ == "date":
        order = (step.get("date_order") or "auto").lower()
        if order not in ("auto", "dmy", "mdy", "ymd"):
            raise TransformError(f"不支持的日期顺序：{order}")
        src, expr = _date_parse(b, col, order, keep_raw=bool(step.get("keep_raw", True)))
        # 日期在 SQLite 里以 ISO 文本存储，声明类型仍是 TEXT
        newtype = "TEXT"
    elif typ == "text":
        expr = f"CAST({qid(col)} AS TEXT)"
        newtype = "TEXT"
        src = b.rel
    else:
        raise TransformError(f"不支持的目标类型：{typ}")
    label = "改类型 %s → %s" % (col, "日期" if typ == "date" else typ)
    newcols = [{**c, "type": (newtype if c["name"] == col else c["type"])} for c in b.cols]
    for c in newcols:
        if c["name"] == col:
            c["date"] = (typ == "date")
            # 转换后 store 已无意义：转数字时 num_expr 已经把 '¥30,000.00' 洗成数字，
            # 留着旧的 'text' 会让表头一直标着「声明是数字实存文本」的告警
            c["store"] = None
    return b.push(f"SELECT {_sub(b.cols, {col: f'{expr} AS {qid(col)}'})} FROM {src}",
                  newcols, label)


def _apply_replace(b, step):
    col = _require_col(b.cols, step.get("column"), "列")
    find = step.get("find")
    repl = step.get("replace")
    if find is None or find == "":
        raise TransformError("请填写要查找的值")
    exact = bool(step.get("exact", True))
    if exact:
        expr = f"CASE WHEN {qid(col)} = {_lit(find)} THEN {_lit(repl)} ELSE {qid(col)} END"
    else:
        expr = f"REPLACE({qid(col)}, {_lit(find)}, {_lit(repl)})"
    return b.push(f"SELECT {_sub(b.cols, {col: f'{expr} AS {qid(col)}'})} FROM {b.rel}",
                  b.cols, f"替换 {col}")


def _apply_clean_text(b, step):
    cols = step.get("columns") or ([step["column"]] if step.get("column") else [])
    if not cols:
        raise TransformError("请至少选择一列")
    mode = (step.get("mode") or "trim").lower()
    fn = {"trim": "TRIM", "lower": "LOWER", "upper": "UPPER"}.get(mode)
    if not fn:
        raise TransformError(f"不支持的清理方式：{mode}")
    mapping = {}
    for c in cols:
        _require_col(b.cols, c)
        mapping[c] = f"{fn}({qid(c)}) AS {qid(c)}"
    return b.push(f"SELECT {_sub(b.cols, mapping)} FROM {b.rel}", b.cols, f"清理文本({mode})")


def _apply_split(b, step):
    col = _require_col(b.cols, step.get("column"), "列")
    delim = step.get("delimiter")
    if delim is None or delim == "":
        raise TransformError("请填写分隔符")
    if '"' in str(delim) or "\\" in str(delim):
        raise TransformError("分隔符不能包含双引号或反斜杠")
    parts = int(step.get("count") or 2)
    if parts < 2 or parts > 10:
        raise TransformError("拆分列数需在 2~10 之间")
    prefix = _escape_ident(step.get("prefix") or (col + "_"), "新列名前缀")
    typ = next(c["type"] for c in b.cols if c["name"] == col)

    # 用 JSON 数组技巧取任意一段：'["a","b"]' -> json_extract('$[i]')
    json_arr = f"('[\"' || REPLACE({qid(col)}, {_lit(delim)}, '\",\"') || '\"]')"
    mapping = {col: None}          # 原列被替换掉
    newcols = []
    for i in range(parts):
        nm = f"{prefix}{i + 1}"
        mapping[col] = None
        newcols.append({"name": nm, "type": "TEXT", "pk": False})
    # 逐列表达式（放在 SELECT 里，原列位置展开为多列）
    exprs = []
    for c in b.cols:
        if c["name"] == col:
            for i in range(parts):
                exprs.append(f"json_extract({json_arr}, '$[{i}]') AS {qid(prefix + str(i + 1))}")
        else:
            exprs.append(qid(c["name"]))
    outcols = []
    for c in b.cols:
        if c["name"] == col:
            outcols.extend(newcols)
        else:
            outcols.append(c)
    return b.push(f"SELECT {', '.join(exprs)} FROM {b.rel}", outcols, f"拆分 {col} → {parts} 列")


def _apply_extract(b, step):
    col = _require_col(b.cols, step.get("column"), "列")
    mode = (step.get("mode") or "left").lower()
    n = int(step.get("count") or 3)
    if n <= 0:
        raise TransformError("长度/序号需大于 0")
    if mode == "left":
        expr = f"substr({qid(col)}, 1, {n})"
    elif mode == "right":
        expr = f"substr({qid(col)}, -{n})"
    elif mode == "mid":
        expr = f"substr({qid(col)}, {n})"
    elif mode == "between":
        start, end = step.get("start"), step.get("end")
        if not start or not end:
            raise TransformError("「两标记之间」需要起始标记和结束标记")
        expr = (f"substr({qid(col)}, instr({qid(col)}, {_lit(start)}) + {len(str(start))},"
                f" instr({qid(col)}, {_lit(end)}) - instr({qid(col)}, {_lit(start)}) - {len(str(start))})")
    else:
        raise TransformError(f"不支持的提取方式：{mode}")
    newname = str(step.get("new_name") or col)
    mapping = {col: f"{expr} AS {qid(newname)}"}
    newcols = [{**c, "name": (newname if c["name"] == col else c["name"]), "type": "TEXT"}
               for c in b.cols]
    return b.push(f"SELECT {_sub(b.cols, mapping)} FROM {b.rel}", newcols, f"提取 {col}({mode})")


# 自定义列表达式黑名单：禁止破坏语句结构或触碰库结构/权限
_BAD_EXPR = re.compile(
    r"(;|--|/\*|\*/|\b(attach|detach|pragma|vacuum|reindex|drop|create|alter|"
    r"insert|update|delete|replace\s+into|load_extension|writefile|readfile)\b)",
    re.I)


def check_expr(expr):
    s = str(expr or "").strip()
    if not s:
        raise TransformError("请填写表达式")
    if len(s) > 500:
        raise TransformError("表达式过长（上限 500 字符）")
    m = _BAD_EXPR.search(s)
    if m:
        raise TransformError(f"表达式含不允许的内容：{m.group(1)}")
    return s


def _apply_add_column(b, step):
    name = _escape_ident(step.get("new_name"), "新列名")
    if any(c["name"] == name for c in b.cols):
        raise TransformError(f"列「{name}」已存在")
    expr = check_expr(step.get("expr"))
    outcols = [{"name": name, "type": "TEXT", "pk": False}] + b.cols
    return b.push(f"SELECT {expr} AS {qid(name)}, {b.select_all()} FROM {b.rel}",
                  outcols, f"自定义列 {name}")


def _apply_group(b, step):
    keys = step.get("keys") or []
    aggs = step.get("aggs") or []
    if not keys and not aggs:
        raise TransformError("请至少选择分组列或汇总项")
    for k in keys:
        _require_col(b.cols, k)
    key_sql = [qid(k) for k in keys]
    outcols = [dict(next(c for c in b.cols if c["name"] == k)) for k in keys]
    agg_sql = []
    for a in aggs:
        fn = (a.get("func") or "sum").lower()
        col = (a.get("column") or "").strip()
        if not col and fn != "count_all":
            continue          # 界面上留空的汇总行直接忽略，不报错
        if fn == "count_all":
            # 计数(行数) 本来就不需要指定列。之前这里被「空列就跳过」误杀，
            # 导致按列分组计数只输出键列、没有计数列。
            alias = _escape_ident((a.get("alias") or "").strip() or "行数", "汇总列名")
            agg_sql.append(f"COUNT(*) AS {qid(alias)}")
            outcols.append({"name": alias, "type": "INTEGER", "pk": False})
            continue
        col = _require_col(b.cols, col, "汇总列")
        alias = _escape_ident(a.get("alias") or f"{fn}_{col}", "汇总列名")
        colinfo = next(c for c in b.cols if c["name"] == col)
        typ = colinfo["type"]
        # 数值语义的列若实际存成文本（如 '¥30,000.00'），求和/平均前必须清洗
        base = qid(col)
        if fn in ("sum", "avg") and _needs_wash(colinfo):
            base = num_expr(qid(col))
        if fn == "sum":
            e = f"SUM({base})"
        elif fn == "avg":
            e = f"AVG({base})"
        elif fn == "count":
            e = f"COUNT({qid(col)})"
        elif fn == "count_all":
            e = "COUNT(*)"
        elif fn == "count_distinct":
            e = f"COUNT(DISTINCT {qid(col)})"
        elif fn == "min":
            e = f"MIN({qid(col)})"
        elif fn == "max":
            e = f"MAX({qid(col)})"
        else:
            raise TransformError(f"不支持的汇总方式：{fn}")
        agg_sql.append(f"{e} AS {qid(alias)}")
        outcols.append({"name": alias, "type": ("INTEGER" if fn.startswith("count") else "REAL"),
                        "pk": False})
    sel = ", ".join(key_sql + agg_sql)
    grp = ", ".join(str(i + 1) for i in range(len(keys))) if keys else ""
    sql = f"SELECT {sel} FROM {b.rel}" + (f" GROUP BY {grp}" if grp else "")
    return b.push(sql, outcols, f"分组汇总 {len(keys)} 键 / {len(aggs)} 汇总")


def _apply_sort(b, step):
    items = step.get("items") or ([{"column": step.get("column"),
                                    "desc": step.get("desc")}] if step.get("column") else [])
    if not items:
        raise TransformError("请选择排序列")
    parts = []
    for it in items:
        col = _require_col(b.cols, it.get("column"), "排序列")
        typ = next(c["type"] for c in b.cols if c["name"] == col)
        expr = num_expr(qid(col)) if (_is_num_type(typ) or it.get("as_number")) else qid(col)
        parts.append(f"{expr} {'DESC' if it.get('desc') else 'ASC'}")
    return b.push(f"SELECT {b.select_all()} FROM {b.rel} ORDER BY {', '.join(parts)}",
                  b.cols, f"排序 {len(items)} 列")


def _apply_distinct(b, step):
    return b.push(f"SELECT DISTINCT {b.select_all()} FROM {b.rel}", b.cols, "删除重复行")


def _apply_limit(b, step):
    n = int(step.get("count") or 100)
    if n <= 0:
        raise TransformError("行数需大于 0")
    return b.push(f"SELECT {b.select_all()} FROM {b.rel} LIMIT {n}", b.cols, f"保留前 {n} 行")


def _apply_join(b, step):
    table = _escape_ident(step.get("table"), "目标表")
    left = _require_col(b.cols, step.get("left_on"), "本表关联列")
    rcols = get_columns(b.con, table)
    right = step.get("right_on")
    if right not in [c["name"] for c in rcols]:
        raise TransformError(f"目标表「{table}」没有列「{right}」")
    how = (step.get("type") or "left").upper()
    if how not in ("LEFT", "INNER"):
        raise TransformError("仅支持左连接 / 内连接")
    pick = step.get("columns") or [c["name"] for c in rcols]
    for c in pick:
        if c not in [x["name"] for x in rcols]:
            raise TransformError(f"目标表没有列「{c}」")
    existing = {c["name"] for c in b.cols}
    exprs, outcols = [], list(b.cols)
    for c in b.cols:
        exprs.append(f"a.{qid(c['name'])}")
    for c in pick:
        alias = c
        while alias in existing:
            alias = alias + "_2"
        existing.add(alias)
        exprs.append(f"b.{qid(c)} AS {qid(alias)}")
        outcols.append({"name": alias, "type": next(x["type"] for x in rcols if x["name"] == c),
                        "pk": False})
    body = (f"SELECT {', '.join(exprs)} FROM {b.rel} a "
            f"{how} JOIN {qid(table)} b ON a.{qid(left)} = b.{qid(right)}")
    return b.push(body, outcols, f"{how} JOIN {table}")


def _apply_append(b, step):
    table = _escape_ident(step.get("table"), "目标表")
    rcols = [c["name"] for c in get_columns(b.con, table)]
    mine = [c["name"] for c in b.cols]
    n = min(len(mine), len(rcols))
    if n == 0:
        raise TransformError("两表没有可对齐的列")
    a_cols = ", ".join(qid(c) for c in mine[:n])
    b_cols = ", ".join(qid(c) for c in rcols[:n])
    body = (f"SELECT {a_cols} FROM {b.rel} "
            f"UNION ALL SELECT {b_cols} FROM {qid(table)}")
    return b.push(body, b.cols[:n], f"追加 {table}（按前 {n} 列对齐）")


def _apply_unpivot(b, step):
    ids = step.get("id_columns") or []
    vals = step.get("value_columns") or []
    if not vals:
        raise TransformError("请选择要逆透视的值列")
    for c in ids + vals:
        _require_col(b.cols, c)
    name_col = _escape_ident(step.get("name_column") or "属性", "属性列名")
    val_col = _escape_ident(step.get("value_column") or "值", "值列名")
    id_sql = ", ".join(qid(c) for c in ids)
    chunks = []
    for v in vals:
        sel = (f"{id_sql + ', ' if id_sql else ''}{_lit(v)} AS {qid(name_col)}, "
               f"{qid(v)} AS {qid(val_col)}")
        chunks.append(f"SELECT {sel} FROM {b.rel}")
    outcols = [dict(next(c for c in b.cols if c["name"] == i)) for i in ids]
    outcols.append({"name": name_col, "type": "TEXT", "pk": False})
    outcols.append({"name": val_col, "type": "TEXT", "pk": False})
    return b.push(" UNION ALL ".join(chunks), outcols, f"逆透视 {len(vals)} 列")


def _apply_fill_down(b, step):
    col = _require_col(b.cols, step.get("column"), "填充列")
    order = step.get("order_by")
    if order:
        order = _require_col(b.cols, order, "排序列")
        ord_sql = qid(order)
    else:
        ord_sql = "ROW_NUMBER() OVER ()"
    # 累计非空计数作为分组键，再在组内取最大（=该组唯一非空值）
    inner = (f"SELECT *, COUNT({qid(col)}) OVER (ORDER BY {ord_sql}) AS __g "
             f"FROM {b.rel}")
    exprs = []
    for c in b.cols:
        if c["name"] == col:
            exprs.append(f"COALESCE({qid(col)}, "
                         f"MAX({qid(col)}) OVER (PARTITION BY __g)) AS {qid(col)}")
        else:
            exprs.append(qid(c["name"]))
    body = f"SELECT {', '.join(exprs)} FROM ({inner})"
    return b.push(body, b.cols, f"向下填充 {col}")


_APPLIERS = {
    "filter": _apply_filter,
    "keep_columns": _apply_keep,
    "remove_columns": _apply_remove,
    "rename_column": _apply_rename,
    "change_type": _apply_change_type,
    "replace_value": _apply_replace,
    "clean_text": _apply_clean_text,
    "split_column": _apply_split,
    "extract_text": _apply_extract,
    "add_column": _apply_add_column,
    "group_by": _apply_group,
    "sort": _apply_sort,
    "distinct": _apply_distinct,
    "limit": _apply_limit,
    "join": _apply_join,
    "append": _apply_append,
    "unpivot": _apply_unpivot,
    "fill_down": _apply_fill_down,
}

# 不改变行数的步骤：这些步骤之后的 COUNT(*) 恒等于源表行数，
# 预览可以直接查源表而不必重算整条 SQL（日期解析这类重活因此不必全表跑）
_ROW_PRESERVING = frozenset([
    "keep_columns", "remove_columns", "rename_column", "change_type",
    "replace_value", "clean_text", "split_column", "extract_text",
    "add_column", "sort", "fill_down",
])


def step_title(step, i=None):
    op = step.get("op")
    lab = STEP_LABELS.get(op, (op, ""))[0]
    if op == "filter":
        extra = f" {step.get('column', '')}"
    elif op in ("keep_columns", "remove_columns", "clean_text"):
        extra = f" ({len(step.get('columns') or [])} 列)"
    elif op == "group_by":
        extra = f" ({len(step.get('keys') or [])} 键)"
    elif op == "join":
        extra = f" ({step.get('table', '')})"
    elif op == "append":
        extra = f" ({step.get('table', '')})"
    else:
        extra = ""
    return f"{i + 1}. {lab}{extra}" if i is not None else f"{lab}{extra}"


def compile_query(con, source, steps, upto=None):
    """
    编译步骤链。upto=None 表示全部；否则只编译到第 upto 步（1-based，含）。
    返回 {sql, columns, steps:[{index,label,sql}], errors}
    """
    if not source:
        raise TransformError("请选择源表")
    if upto is not None:
        steps = list(steps or [])[:max(0, int(upto))]
    b = _Builder(con, source)
    for i, step in enumerate(steps or []):
        op = (step or {}).get("op")
        fn = _APPLIERS.get(op)
        if not fn:
            raise TransformError(f"第 {i + 1} 步：不支持的步骤类型「{op}」")
        if step.get("disabled"):
            continue
        try:
            fn(b, step)
        except TransformError as e:
            raise TransformError(f"第 {i + 1} 步（{STEP_LABELS.get(op, (op,))[0]}）：{e}")
        except Exception as e:
            raise TransformError(f"第 {i + 1} 步（{STEP_LABELS.get(op, (op,))[0]}）执行出错：{e}")
    # 行数是否与源表一致：全部步骤都不增删行时，预览的总行数可以直接查源表，
    # 不必把整条 SQL（含日期解析等昂贵表达式）重算一遍 —— 这是预览秒开的关键。
    keep_rows = all((s or {}).get("disabled") or (s or {}).get("op") in _ROW_PRESERVING
                    for s in (steps or []))
    return {"sql": b.final_sql(), "columns": b.cols, "steps": b.step_sql,
            "row_preserving": keep_rows}


def preview(con, source, steps, upto=None, limit=200):
    """只读预览；返回列、行、总行数与 SQL。"""
    t0 = time.time()
    comp = compile_query(con, source, steps, upto)
    sql = comp["sql"]
    if comp["row_preserving"]:
        total = con.execute(f"SELECT COUNT(*) FROM {qid(source)}").fetchone()[0]
    else:
        total = con.execute(f"SELECT COUNT(*) FROM ({sql})").fetchone()[0]
    rows = con.execute(f"SELECT * FROM ({sql}) LIMIT {int(limit)}").fetchall()
    cols = [d[0] for d in (rows and [tuple(r) for r in rows] or [])]
    colnames = [c["name"] for c in comp["columns"]]
    data = [list(r) for r in rows]
    return {
        "columns": comp["columns"],
        "colnames": colnames,
        "rows": data,
        "total": total,
        "shown": len(data),
        "sql": sql,
        "step_sql": comp["steps"],
        "elapsed_ms": int((time.time() - t0) * 1000),
    }


def column_values(con, source, steps, column, upto=None, limit=500):
    """取当前结果某一列的去重取值（按出现次数降序），供「点表头筛选」的值面板使用。

    返回 {column, values:[{v,n}], distinct, total, truncated}
    v 可能是 None（NULL 值），前端单独渲染成「(空)」。
    """
    comp = compile_query(con, source, steps, upto)
    names = [c["name"] for c in comp["columns"]]
    if column not in names:
        raise TransformError(f"列「{column}」在当前结果中不存在")
    sql = comp["sql"]
    q = qid(column)
    lim = max(1, min(int(limit or 500), 2000))
    rows = con.execute(
        f"SELECT {q} AS v, COUNT(*) AS n FROM ({sql}) "
        f"GROUP BY {q} ORDER BY 2 DESC, 1 LIMIT {lim}").fetchall()
    vals = [{"v": r[0], "n": r[1]} for r in rows]
    distinct = con.execute(
        f"SELECT COUNT(*) FROM (SELECT {q} FROM ({sql}) GROUP BY {q})").fetchone()[0]
    total = con.execute(f"SELECT COUNT(*) FROM ({sql})").fetchone()[0]
    return {"column": column, "values": vals, "distinct": int(distinct),
            "total": int(total), "truncated": int(distinct) > len(vals)}


def column_stats(con, source, steps, upto=None):
    """每列的「饱满度」：有效值 / 空值 / 是否单值，供表头质量条上色。

    一条 SQL 把全部列算完，不逐列查询。所谓「空值」= NULL 或空白字符串
    （Excel 导出的空单元格常是 ''，看着就是空的，不能算成有效值）。

    这里**故意不算 COUNT(DISTINCT)**：它的开销占整体 85%（35 列 × 4.5 万行
    实测 0.86s vs 0.16s），而界面上只有「这列是不是只有一个取值」需要它，
    用 MIN/MAX 比较即可（几乎零成本）。真要取值分布，点色带走值面板。

    返回 {total, columns: {列名: {filled, nulls, nonnull, const, fill}}}
    fill 是 0~1 的有效值占比（「饱满度」）。
    """
    comp = compile_query(con, source, steps, upto)
    names = [c["name"] for c in comp["columns"]]
    if not names:
        return {"total": 0, "columns": {}}
    sql = comp["sql"]
    parts = ["COUNT(*)"]
    for n in names:
        q = qid(n)
        # 有效值 = 非 NULL 且不是空白串
        parts.append(f"SUM(CASE WHEN {q} IS NULL OR TRIM(CAST({q} AS TEXT)) = '' "
                     f"THEN 0 ELSE 1 END)")
        # 最小/最大有效值：两者相等且存在 → 整列只有一个取值（用来把绿条调暗）
        # 日期列比原值（ISO 串），这样「整列同一天」也能判出来
        parts.append(f"MIN(NULLIF(TRIM(CAST({q} AS TEXT)), ''))")
        parts.append(f"MAX(NULLIF(TRIM(CAST({q} AS TEXT)), ''))")
    try:
        # 先 MATERIALIZED 把结果集固定下来：同一列在 SUM/MIN/MAX 里被引用三次，
        # 不物化的话日期解析这类昂贵表达式会被逐次重算（实测差 3 倍）
        row = con.execute(
            f"WITH _m AS MATERIALIZED (SELECT * FROM ({sql})) "
            f"SELECT {', '.join(parts)} FROM _m").fetchone()
    except sqlite3.Error as e:
        raise TransformError(f"统计列质量失败：{e}")
    total = int(row[0] or 0)
    # 行数未变时用源表行数做基数，省掉一次全表 COUNT（日期解析列上这一步最贵）
    if comp["row_preserving"]:
        try:
            total = con.execute(f"SELECT COUNT(*) FROM {qid(source)}").fetchone()[0]
        except sqlite3.Error:
            pass
    out = {}
    for i, n in enumerate(names):
        filled = int(row[1 + i * 3] or 0)
        mn, mx = row[2 + i * 3], row[3 + i * 3]
        out[n] = {
            "filled": filled,
            "nulls": total - filled,     # NULL + 空白串，统一在界面显示为「空值」
            "const": bool(filled > 0 and mn is not None and mn == mx),
            "fill": round(filled / total, 4) if total else 0.0,
        }
    return {"total": total, "columns": out}


_SRC_PREFIX_RE = re.compile(r"^(?:dim_|fct_|vw_|tbl_)", re.IGNORECASE)


def suggest_name(con, source, mode="view"):
    """给一个可直接用的默认输出名：vw_po / tbl_po，重名自动加序号。

    命名口径与 Power Query 一致——查询默认从源表名派生，重名追加数字，
    用户不需要为「每次改完保存」再想一个新名字。
    """
    base = _SRC_PREFIX_RE.sub("", (source or "").strip()) or "query"
    base = re.sub(r"[^\w\u4e00-\u9fa5]", "_", base) or "query"
    if not re.match(r"^[A-Za-z_\u4e00-\u9fa5]", base):
        base = "q_" + base
    pre = "tbl_" if mode == "table" else "vw_"
    used = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table','view')")}
    try:
        used |= {r[0] for r in con.execute("SELECT name FROM transform_query")}
    except sqlite3.Error:
        pass
    cand, i = pre + base, 2
    while cand in used:
        cand = f"{pre}{base}{i}"
        i += 1
    return cand


def apply_query(con, name, source, steps, mode="view", overwrite=False):
    """把编译结果落地为视图（mode='view'）或物化表（mode='table'）。"""
    name = _escape_ident(name, "输出名称")
    if not re.match(r"^[A-Za-z_\u4e00-\u9fa5][\w\u4e00-\u9fa5]*$", name):
        raise TransformError("输出名称只能用字母、数字、下划线或中文，且不能以数字开头")
    if name in ("map_config", "map_layout", "quality_rules", "transform_query"):
        raise TransformError("不能覆盖系统元数据表")
    src_names = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    comp = compile_query(con, source, steps)
    sql = comp["sql"]
    typ = con.execute("SELECT type FROM sqlite_master WHERE name=?", (name,)).fetchone()
    if typ and not overwrite:
        raise TransformError(f"「{name}」已存在（{typ[0]}），如需覆盖请勾选「覆盖同名对象」")
    if name in src_names and name not in (
            {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='view'").fetchall()}):
        # 目标是真实表：物化表才允许覆盖，视图不能盖真实表
        if mode == "view":
            raise TransformError(f"「{name}」是已存在的真实表，不能用视图覆盖（否则会丢数据）")
    if mode == "view":
        con.execute(f"DROP VIEW IF EXISTS {qid(name)}")
        con.execute(f"CREATE VIEW {qid(name)} AS\n{sql}")
    else:
        con.execute(f"DROP TABLE IF EXISTS {qid(name)}")
        con.execute(f"CREATE TABLE {qid(name)} AS\n{sql}")
    con.commit()
    n = con.execute(f"SELECT COUNT(*) FROM {qid(name)}").fetchone()[0]
    return {"name": name, "mode": mode, "rows": n, "sql": sql, "columns": comp["columns"]}


def drop_output(con, name, mode=None):
    """删除转换产出（视图或表）。"""
    row = con.execute("SELECT type FROM sqlite_master WHERE name=?", (name,)).fetchone()
    if not row:
        return False
    typ = row[0]
    if typ == "view":
        con.execute(f"DROP VIEW {qid(name)}")
    elif typ == "table":
        con.execute(f"DROP TABLE {qid(name)}")
    else:
        return False
    con.commit()
    return True
