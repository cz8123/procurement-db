# 采购数据库 · Power BI 接入与可视化分析指南

> 本地数据库文件：`D:\code\采购数据库建设\procurement.db`（SQLite，单文件，无需服务器）
> 管理脚本：`D:\code\采购数据库建设\db_manager.py`

---

## 一、数据库总览

本数据库由目录下 15 张 CSV 自动建成，采用**星型/关系型架构**：

- **维度 / 主数据表（dim_ 前缀，9 张）**：部门、采购方法、采购类别、采购子类别、公司、供应商属性、供应商属性详情、豁免单一理由、支出类型。
- **事实 / 交易表（fct_ 前缀，6 张）**：集采项目前置审批、框架协议、物料清单、PRPO 主表、采购订单（PO）、供应商列表（`fct_supplier`，供应商准入 / 资格提交记录）。

金额字段已自动解析为**数值型（REAL）**，日期字段为 `YYYY-MM-DD HH:MM:SS` 文本，导入 Power BI 后可直接做度量值与图表。

---

## 二、表清单与行数

| 表名 | 来源 CSV | 行数 | 类型 |
|---|---|---:|---|
| dim_department | 部门映射表.csv | 78 | 维度 |
| dim_procurement_method | 采购方法表.csv | 6 | 维度 |
| dim_category | 采购类别表.csv | 98 | 维度 |
| dim_subcategory | 采购子类别表.csv | 231 | 维度 |
| dim_company | 公司映射表.csv | 63 | 维度 |
| dim_supplier_attribute | 供应商属性表.csv | 3 | 维度 |
| dim_supplier_attribute_detail | 供应商属性详情表.csv | 26 | 维度 |
| dim_exception_reason | 豁免单一理由表.csv | 29 | 维度 |
| dim_expense_type | 支出类型表.csv | 4 | 维度 |
| fct_preapproval | 集采项目前置审批_*.csv | 183 | 事实 |
| fct_framework_agreement | 框架协议_*.csv | 186 | 事实 |
| fct_bom | 物料清单_*.csv | 15,602 | 事实 |
| fct_prpo | PRPO_*.csv | 52,113 | 事实 |
| fct_po | Purchase Order_*.csv | 46,687 | 事实 |
| fct_supplier | 供应商列表_*.csv | 6,127 | 事实 |

---

## 三、数据模型与映射关系

各表之间通过**业务自然键**建立关联（即 Power BI 中“建立关系”所用的字段）。下表为推荐在 Power BI「模型」视图中创建的关系（均为**多对一**，从事实表指向维度表）：

| 关系（从 → 到） | 关联字段 | 说明 |
|---|---|---|
| fct_po.公司 → dim_company.Company | 公司全称 | 0% 孤儿，完全匹配 |
| fct_po.部门 → dim_department.DepartmentName | 部门名 | 0.6% 变种未覆盖 |
| fct_po.已批准的采购申请参考 → fct_prpo.表单编号 | PRPO 单号 | PO 对应的请购单 |
| fct_prpo.公司 → dim_company.Company | 公司全称 | 0% 孤儿 |
| fct_prpo.部门 → dim_department.DepartmentName | 部门名 | 0.6% 变种未覆盖 |
| fct_prpo.支出类型 → dim_expense_type.ExpenseType | 支出类型 | 0% 孤儿 |
| fct_prpo.采购类别 → dim_category.Category | 采购类别 | 0% 孤儿 |
| fct_prpo.豁免单一来源的理由 → dim_exception_reason.ExceptionReason | 豁免理由 | 0% 孤儿 |
| fct_prpo.procurement_method_key → dim_procurement_method.ProcurementMethod | 采购方法 | 中文→英文规范化，0% 孤儿 |
| fct_prpo.supplier_attribute_key → dim_supplier_attribute.SupplierCate | 供应商属性 | A/B/C 规范化，0% 孤儿 |
| fct_prpo.框架合同编号 → fct_framework_agreement.合同编号（中文） | 框架合同号 | 仅约 48% 命中（见数据质量说明） |
| fct_framework_agreement.前置审批单号 → fct_preapproval.参考编号 | 前置审批单号(FAPA-xxx) | 0% 孤儿 |
| fct_bom.合同编号CN → fct_framework_agreement.合同编号（中文） | 框架合同号 | 0% 孤儿（清单归属合同） |
| fct_preapproval.采购类别 → dim_category.Category | 采购类别 | 0% 孤儿 |

> **供应商列表（`fct_supplier`）归为事实表**：它记录的是供应商准入 / 资格的**提交记录**（含参考编号、提交者、提交日期、状态、Approved on、End Date 等流程字段），按业务口径属事实表，故不参与维度关联（仍正常入库，6,127 行）。上表中原 3 条供应商相关关系已移除；如需启用供应商维度分析，恢复其外键即可。

> **关于规范化键（procurement_method_key / supplier_attribute_key）**：
> 源系统中「维度表」与「交易表」使用的文字格式不一致（例如采购方法维度是英文 `Quotation Purchase`，PRPO 里是中文“报价采购…”）。脚本已自动生成这些规范化列，使关联可用，无需在 Power BI 中再写复杂映射。（`supplier_cn` 列仍保留于事实表作为供应商中文名展示字段，但因供应商表休眠而不参与关联。）

---

## 四、Power BI 接入步骤

### 方式 A（推荐）：SQLite ODBC 驱动

1. **安装驱动**：下载并安装 **64 位 SQLite ODBC 驱动**
   （官方：http://www.ch-werner.de/sqliteodbc/ ，务必与你的 Power BI Desktop 位数一致，通常都是 64 位）。
2. 打开 **Power BI Desktop → 主页 → 获取数据 → 其他 → ODBC**。
3. 展开「高级选项」，在**连接字符串**中填入（注意替换成你的实际路径）：
   ```
   database=D:\code\采购数据库建设\procurement.db
   ```
   （也可先在 Windows「ODBC 数据源管理器(64 位)」里建一个 DSN 再选。）
4. 点击「确定」→ 身份验证选 **“匿名/默认”** 即可（SQLite 文件无需账号）。
5. 导航器中出现全部 15 张表，**全选 → 加载**（或先「转换数据」做清洗）。

### 方式 B（免驱动）：Power Query SQLite 读取器

若电脑无法安装 ODBC 驱动（无管理员权限），可使用社区纯 Power Query 函数
（https://github.com/Hugoberry/PowerQuery-SQLiteReader ），在 Power Query 高级编辑器里
`SQLite(File.Contents("D:\code\采购数据库建设\procurement.db"))` 直接读取，且可在 Power BI
Service 中免网关刷新。

### 导入后在 Power BI 中建议做的处理

- **建立关系**：按第三节的 14 条关系在「模型」视图里逐条创建（多对一）。
- **类型转换**：把 `提交日期 / 提交者` 等日期列的类型从「文本」改为「日期/时间」；
  金额列已是小数，可直接用于度量值（`SUM`、`AVERAGE`）。
- **隐藏主键/编码列**：如 `序`、`id` 等仅用于关联，可在报表视图隐藏。
- **建议度量值示例**：`采购总额 = SUM(fct_po[总采购订单总额（人民币)])`、
  `PRPO 笔数 = COUNTROWS(fct_prpo)`。

---

## 五、数据更新（新数据追加 / 重复跳过）

日常拿到新的 CSV 导出后，**覆盖同名 CSV 文件**，然后运行：

```bash
cd D:\code\采购数据库建设
python db_manager.py update
```

- 基于**主键**（表单编号 / 参考编号 / 供应商编码 / 公司名等）做 `INSERT OR IGNORE`：
  - **已存在的记录（主键相同）→ 自动跳过**，不会重复入库；
  - **新增记录（主键不同）→ 自动追加**。
- 导入顺序已按依赖关系固定（维度表优先，事实表在后），保证外键引用有效。
- 运行结束会自动打印各表「新增 / 跳过(重复)」行数与关联完整性报告。
- Power BI 侧：数据模型刷新即可同步最新数据（建议用「导入模式」，变更后点「刷新」）。

> 首次建库用 `python db_manager.py build`（会重建 procurement.db 并全量导入）。
> 查看表结构用 `python db_manager.py tables`；校验关联完整性用 `python db_manager.py verify`。

---

## 六、数据质量说明（不影响整体分析）

| 项目 | 情况 | 影响 |
|---|---|---|
| 供应商列表 | `fct_supplier` 归为事实表（记录供应商准入 / 资格提交），不参与维度关联；如需启用供应商维度分析，恢复其 3 条关系即可 |
| 框架合同关联 | PRPO 中约 52% 的「框架合同编号」在框架协议表中查不到（多为单一来源 SS-xxxx、或不同年份的分支合同） | 仅约 48% 的 PRPO 能关联到框架协议；关联不上的行在 Power BI 中不会匹配到合同信息 |
| 部门名称变种 | 约 0.6% 的部门名（如 `Sales-销售部_RR`、`Marketing-MINI-市场部-Mini`）与部门维度微小不一致 | 极少数行在部门维度上不命中 |

如需消除这些孤儿，可在源系统侧补齐主数据，或在 Power BI 中用「合并查询 / 条件列」做容错匹配。

---

## 七、常见问题

- **Power BI 找不到 DSN / 架构不匹配**：几乎都是 32 位 vs 64 位驱动不匹配，重装 64 位驱动并在「ODBC 数据源管理器(64 位)」中建 DSN。
- **中文列名/表名显示乱码**：确保 CSV 以 UTF-8 with BOM 或 GBK 保存（脚本已自动探测编码）。
- **想换数据库（如 SQL Server / PostgreSQL）**：表结构与 SQL 高度兼容，迁移时把建表语句与 `INSERT OR IGNORE` 改为对应方言的 `ON CONFLICT DO NOTHING` / `MERGE` 即可，关系不变。
