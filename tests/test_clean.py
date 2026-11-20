# -*- coding: utf-8 -*-
"""字段取值清洗（clean.py）单元测试 —— 全部跑在临时库上，不碰 procurement.db。

跑法：python tests/test_clean.py
"""
import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import clean          # noqa: E402
import app as A       # noqa: E402  （只借 suggest_targets / key_similarity）

ok = 0
fail = 0


def chk(name, cond, extra=None):
    global ok, fail
    if cond:
        ok += 1
        print("  [OK] " + name)
    else:
        fail += 1
        print("  [XX] " + name + ("" if extra is None else " -> " + str(extra)))


def build_db(path):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE map_config (id INTEGER PRIMARY KEY, source_table TEXT, "
                "source_col TEXT, target_table TEXT, target_col TEXT, kind TEXT, "
                "cardinality TEXT, note TEXT, enabled INTEGER DEFAULT 1, "
                "advice_override TEXT, ignored INTEGER DEFAULT 0)")
    con.execute("CREATE TABLE dim_department (id TEXT, DepartmentName TEXT PRIMARY KEY, "
                "DepartmentEn TEXT)")
    con.execute("CREATE TABLE fct_po (单号 TEXT, 部门 TEXT)")
    con.execute("CREATE TABLE fct_prpo (单号 TEXT, 部门 TEXT)")

    dims = [
        ("1", "Used Car-二手车部", "Used Car"),
        ("2", "UsedCar-二手车部", "Used Car"),
        ("3", "Marketing-BMW-市场部-BMW", "Marketing"),
        ("4", "Marketing_BMW-市场部_BMW", "Marketing"),
        ("5", "单独的部门", "Solo"),
    ]
    con.executemany("INSERT INTO dim_department VALUES (?,?,?)", dims)

    con.executemany("INSERT INTO fct_po VALUES (?,?)", [
        ("P1", "Used Car-二手车部"), ("P2", "Used Car-二手车部"),
        ("P3", "UsedCar-二手车部"),
        ("P4", "Marketing-BMW-市场部-BMW"), ("P5", "Marketing_BMW-市场部_BMW"),
        ("P6", "单独的部门"),
    ])
    con.executemany("INSERT INTO fct_prpo VALUES (?,?)", [
        ("R1", "UsedCar-二手车部"), ("R2", "单独的部门"),
    ])
    con.execute("INSERT INTO map_config (source_table,source_col,target_table,target_col,kind,enabled) "
                "VALUES ('fct_po','部门','dim_department','DepartmentName','dim',1)")
    con.execute("INSERT INTO map_config (source_table,source_col,target_table,target_col,kind,enabled) "
                "VALUES ('fct_prpo','部门','dim_department','DepartmentName','dim',1)")
    con.commit()
    return con


def orphans(con, table, col="部门"):
    return con.execute(
        f'SELECT COUNT(*) FROM "{table}" WHERE "{col}" IS NOT NULL '
        f'AND "{col}" NOT IN (SELECT DepartmentName FROM dim_department)').fetchone()[0]


def main():
    print("=== 1. 规范化键：只有「同一种写法的变体」才归一 ===")
    chk("下划线/空格/连写 等价", clean.norm_key("Used Car-二手车部") == clean.norm_key("UsedCar_二手车部"))
    chk("全角括号等价", clean.norm_key("A（B）-中文") == clean.norm_key("A(B)-中文"))
    chk("大小写不敏感", clean.norm_key("Marketing-MINI") == clean.norm_key("marketing-mini"))
    chk("大小写差异合并", clean.norm_key("销售部-MINI") == clean.norm_key("销售部-Mini"))
    chk("不同中文不归一", clean.norm_key("公共配套设备") != clean.norm_key("运输设备"))
    chk("不同公司不归一（富悦 vs 启悦）",
        clean.norm_key("Shenzhen Fu Yue Motors-深圳市森那美富悦汽车") !=
        clean.norm_key("Shenzhen Qi Yue Motors-深圳市森那美启悦汽车"))

    print("=== 2. 写法规整度（用于挑规范值） ===")
    chk("双空格更丑", clean.ugliness("A  B") > clean.ugliness("A B"))
    chk("括号残缺更丑", clean.ugliness("1-零件(非厂家") > clean.ugliness("1-零件(非厂家)"))
    chk("驼峰粘连更丑", clean.ugliness("UsedCar") > clean.ugliness("Used Car"))
    # 单字母代号（B部分豁免 / A完全豁免）是中文里的正常写法，不算中英粘连
    chk("单字母代号不判丑", clean.ugliness("B部分豁免") == clean.ugliness("B 部分豁免"))
    chk("中英无分隔更丑",
        clean.ugliness("Aftersales售后部") > clean.ugliness("Aftersales 售后部"))
    # 英文长串丢空格（foodandbeverages）比双空格更离谱
    chk("英文长串粘连更丑",
        clean.ugliness("foodandbeverages-食品饮品")
        > clean.ugliness("food and  beverages-食品饮品"))
    chk("中文前的「 -」更丑",
        clean.ugliness("Administrative Materials -行政类物资")
        > clean.ugliness("Administrative Materials-行政类物资"))
    chk("中文后的「- 」更丑",
        clean.ugliness("售后类服务 -Aftersales Service")
        > clean.ugliness("售后类服务-Aftersales Service"))

    tmp = tempfile.mkstemp(suffix=".db")[1]
    con = build_db(tmp)

    print("=== 3. 值剖析：自动分组 + 规范值 + 影响面 ===")
    p = clean.value_profile(con, "dim_department", "DepartmentName")
    chk("取值 5 个", p["total_values"] == 5, p["total_values"])
    chk("识别 2 组重复", p["group_count"] == 2, p["group_count"])
    chk("涉及 4 个值", p["grouped_values"] == 4, p["grouped_values"])
    chk("归并后 3 个取值", p["after_values"] == 3, p["after_values"])
    chk("级联到 fct_po.部门 与 fct_prpo.部门",
        sorted((x["table"], x["col"]) for x in p["cascade"]) ==
        [("fct_po", "部门"), ("fct_prpo", "部门")], p["cascade"])
    g1 = [g for g in p["groups"] if "Used" in g["canonical"]][0]
    chk("规范值取写法规整的那个（有空格）", g1["canonical"] == "Used Car-二手车部", g1["canonical"])
    chk("使用量统计到事实表（PO 2 行）",
        [m for m in g1["members"] if m["value"] == "Used Car-二手车部"][0]["usage"] == 2,
        g1["members"])
    chk("维度列不产生 missing 标记", p["missing_values"] == 0, p["missing_values"])

    print("=== 4. 执行归并：级联改写 + 删除冗余维度行 ===")
    before_po = orphans(con, "fct_po")
    before_prpo = orphans(con, "fct_prpo")
    r = clean.apply_merge(con, "dim_department", "DepartmentName",
                          [{"to": g1["canonical"], "from": [m["value"] for m in g1["members"]]}])
    con.commit()
    chk("改写 fct_po 1 行", r["updated"].get("fct_po") == 1, r["updated"])
    chk("改写 fct_prpo 1 行", r["updated"].get("fct_prpo") == 1, r["updated"])
    chk("删除 1 个冗余维度成员", r["deleted_rows"] == 1, r["deleted_rows"])
    chk("取值 5 → 4", (r["before_values"], r["after_values"]) == (5, 4),
        (r["before_values"], r["after_values"]))
    chk("归并前后事实表孤儿不变（都没变多）",
        orphans(con, "fct_po") == before_po and orphans(con, "fct_prpo") == before_prpo,
        (orphans(con, "fct_po"), orphans(con, "fct_prpo")))
    n_ugly = con.execute("SELECT COUNT(*) FROM fct_po WHERE 部门='UsedCar-二手车部'").fetchone()[0]
    chk("事实表里已无丑写法", n_ugly == 0, n_ugly)

    print("=== 5. 拒绝把值归并到「不存在的维度成员」 ===")
    try:
        clean.apply_merge(con, "fct_po", "部门",
                          [{"to": "根本不存在的部门", "from": ["单独的部门"]}])
        chk("应报错却成功了", False)
    except ValueError as e:
        chk("拒绝并给出可读原因", "不在" in str(e), str(e))

    print("=== 6. 事实列也可直接归并（同列内合并写法） ===")
    con.execute("INSERT INTO fct_po VALUES ('P7','单独的部门')")
    con.commit()
    r2 = clean.apply_merge(con, "fct_po", "部门",
                           [{"to": "单独的部门", "from": ["单独的部门"]}])
    chk("无实际变化时不变更数据", r2["updated_rows"] == 0, r2)
    con.execute("UPDATE fct_po SET 部门='单独的部门 ' WHERE 单号='P7'")
    con.commit()
    r3 = clean.apply_merge(con, "fct_po", "部门",
                           [{"to": "单独的部门", "from": ["单独的部门 "] }])
    chk("事实列尾空格变体被归并", r3["updated"].get("fct_po") == 1, r3["updated"])

    print("=== 7. 补录建议应优先「写法规整」的同义值 ===")
    sug = A.suggest_targets(con, "dim_department", "DepartmentName", ["Used_Car-二手车部"])
    chk("有建议", "Used_Car-二手车部" in sug, sug)
    if "Used_Car-二手车部" in sug:
        chk("建议指向规整写法而非驼峰变体",
            sug["Used_Car-二手车部"]["value"] == "Used Car-二手车部",
            sug["Used_Car-二手车部"]["value"])

    print("=== 8. 边界：空值 / 空串不算取值 ===")
    con.execute("INSERT INTO fct_po VALUES ('P8', NULL)")
    con.execute("INSERT INTO fct_po VALUES ('P9', '   ')")
    con.commit()
    p2 = clean.value_profile(con, "fct_po", "部门")
    chk("NULL 与空白串被排除", p2["total_rows"] == 7, p2["total_rows"])

    con.close()
    try:
        os.remove(tmp)
    except OSError:
        pass          # Windows 上偶发句柄未释放，临时文件留在系统临时目录即可
    print(f"\n===== {ok} 通过 / {fail} 失败 =====")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
