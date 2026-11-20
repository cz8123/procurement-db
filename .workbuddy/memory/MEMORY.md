# 采购数据库建设 · 长期记忆

SQLite `procurement.db`（~178MB），Power BI 经 64 位 SQLite ODBC 接入。实现细节见 `前端建设/00_实现路径规划.md`（附录 G 看板、H 表头筛选）。

## 1 启动/代码
`"C:/Users/CONG ZHU/.workbuddy/binaries/python/envs/procurement/Scripts/python.exe" app.py`；默认 `127.0.0.1:5000`，共享 `HOST=0.0.0.0 DBADMIN_TOKEN=口令`（`?token=` cookie / `X-Token`），`BROWSER=0`。**改前后端必须重启**：taskkill 5000 后 `run_in_background=true`（尾随 `&` 会被回收）。核心：`db_manager.py`(LOAD_ORDER/FILE_MAP/FKS)·`app.py`(KNOWN_TABLES/TABLE_LABELS)·`transform.py`·`aggmap.py`·`clean.py`·`dash_build.py`·`board_api.py`·`templates/index.html`。

## 2 结构
7 事实表 + 视图(`vw_prpo`/`vw_fa_analysis`) + 6 元数据 + `dim_company_map`(63)。`fct_supplier`(6127)/`fct_preapproval`(204)/`fct_framework_agreement`(190)/`fct_bom`/`fct_prpo`(44987)/`fct_po`(48007)/`fct_company_location`(62)。主键多为 TEXT「表单编号」。

## 3 硬约束
- 源数据单文件 `KUBE采购数据导出.xlsx`（`find_xlsx()` 取最新、缺表回退 CSV）；`update` 幂等需 openpyxl。三层适配 `HEADER_ALIASES`/`VALUE_ALIASES`/`map_method`；`load_table` 按 `PRAGMA table_info` 过滤列。
- **Dataverse**：带 `Table - ` 前缀是 lookup 显示文本（真实数据），不带的空 ID。
- **派生键必须保留** `supplier_cn`/`procurement_method_key`/`supplier_attribute_key`（原始列孤儿 97%/99%，派生 0%）。
- 前缀 `fct_`/`dim_`（聚合 `dim_agg_`）；中文名单一出处 `app.TABLE_LABELS`。`_existing_tables` = `KNOWN_TABLES` ∩ `sqlite_master`，否则删表后侧栏空壳。
- `ensure_meta_tables` 只在 `map_config` 新建时播种 `FKS`；改元数据用表级 JSON 快照。备份留「最老1 + 最新N-1」，禁 `ls -t|tail -n +4|rm`。
- **常量重名会静默失效**（`_NUM_TYPES` 曾覆盖→合计全空）；`create_table` 不建 FOREIGN KEY；`verify` 有 `dangling_fks()`。

## 4 数据转换 `#transform`
步骤链→CTE SQL→视图/物化表，18 种步骤（新增同步 `STEP_LABELS`/`_APPLIERS`/`_apply_xxx`/`TF_FIELDS`）。**视图只读**，映射建在事实表。
1. `filter` 比较字段叫 `cmp`，不能与 `op` 同 dict（互相覆盖→筛选失效）。
2. **金额列声明 REAL 实存 `'¥30,000.00'`，裸 `SUM()`=0.0**；`get_columns` 采样 `typeof` 标 `store='text'`，`_needs_wash()` 决定套 `num_expr`（去 `HK$/¥/￥/$/,/空格` 再 CAST）。
3. 预览走 `_ro_con()`；`check_expr` 拦 `; -- /* */ attach/drop/insert/update/delete/load_extension`。
4. **`date(列)` 只认 ISO**；`_date_parse()` 出 5 层 CTE 纯 SQL，**不能注册 Python 函数**（ODBC 报 `no such function`）。`date('45000')` 返非 NULL，判序 `_p1` 年在先/`_p3` 年在后，解析不出 `keep_raw=True`。
5. `_ROW_PRESERVING` 快路径（否则全表 14s）；`count_all` 分组项别 `continue` 掉。弹层 `position:fixed` 挂 body（`.tf-prev-wrap` 会裁剪），滚动关弹层留 260ms；列名含 `"` 时禁内联 `onclick`，用 `data-col` + 事件委托。
6. **改类型报错写预览区 `.tf-err`**（非 toast）。静默失败：非数字 `CAST→0.0`、整数截断、日期认不出。`suggest_name()` 去前缀→查重加序号（`tf.nameTouched` 防覆盖）。

## 5 聚合映射 `#agg`
`dim_agg_*` 结构 **`id|原始值(PK)|聚合值列`（+附加字段）**，不建「源行数」列（`_value_rows()` 现算），关系建在「原始值」。
**`/api/agg/scan?saved=1`**：字段已有 task 以库里映射为准，源新增取值 1:1 追加标 `new`，否则 `auto_map` 建议；不这样一打开页面就冲掉手改。`agSave()` 唯一写库入口（有 task→PUT，无→POST）；scan 带 `refs=0`。
`rewrite_mapping` 按原始值 diff（不能 DELETE+重插，手工附加字段全丢）；`scan_cascade(allow_same_name=True)`。
**导入「表头即字段」**：第一列永远是原始值（不看表头名），只有表头写「聚合值」的列才改聚合值，没这列 `agg=None`＝不动；`/api/agg/import` 收 `base` 当基线，文件里没有的取值/留空字段保持现状。表头认不出时首行两列都对不上源字段则判表头丢弃。删表后前端缓存旧 task → `agSave` 捕 PUT 404、快照、重 scan、套回、走 build。

## 6 增量导入
`classify_import()` 临时表存主键再 JOIN，分 `new/same/conflict`；**只比文件提供的列**（缺列不清空），金额按数值比、其余 trim。`_apply_conflicts()` 支持 skip/overwrite/fields；`type_risks()` 用 `change_type` 预跑报失败行。

## 7 管理台页面坑
- `#model` 悬浮高亮两端、其余淡到 .10（线宽/透明度只由 CSS 定）；`mvSizeStage`/`mvDrawEdges` 必须 `requestAnimationFrame` 延迟量算，`_mvRenderToken` 防旧请求。
- `/api/backfill-map`：`dim→fact` 需 `confirm:true`，`fact→fact` 400；默认 skip，≥0.80 预填且 `qual.touched` 防覆盖。
- `showResult()` 必须显式 `display="block"`（inline `display:none` 优先级更高）；`api_rows.num_cols` 排除主键；新增行不填 ID（schema 每列带 `auto`）；`/api/tables` 追加视图为 `kind=view/readonly=true`，别进 `KNOWN_TABLES`。

## 8 测试
后端 592 / 前端 240 / 看板 251 + 快照 60。puppeteer 在 `C:/Users/CONG ZHU/AppData/Local/Temp/ui-probe/node_modules`（`NODE_PATH` 指定）。**别写死表数/关系 id**、别取「第一个按钮」、断言主键跳过 `vw_`、自增用 `sqlite_sequence`。CSS 大改后跑 `grid_sum_check`/`tf_click_check`/`tf_qb_check`/`colfilter_check`。无头坑：`page.goto(url#hash2)` 不重载→加 `?t=TS`；**别用固定 sleep**，轮询文本；SVG `<g>` 没 `.click()`，用 `dispatchEvent`。

## 9 数据缺口（勿编造补）
供应商仅 6127 家（`fct_po.supplier_cn` 孤儿 3.16%）；**PRPO 框架合同号 51.36% 孤儿**（只导 2026-04~10，用户已定不补历史、主看 2026）；部门名 0.6% 变种；`框架合同到期日` 97 行是 Excel 序列号；`fct_company_location` 缺 Color 的是「深圳悦之宝」(62)。公司键：`fct_prpo` 零孤儿、`fct_po` 2 行 NULL。

## 10 管理台设计系统
**既有 class/id 与几何必须保留**：`.tf-prev-wrap` overflow / `.tf-pop` fixed / `.tf-left` 250 / `.tf-qb` 5px；**旧变量名保留**（`--primary`/`--card`/`--fact`/`--dim`）。顶栏 58 + 可折叠左栏 252（`#sbToggle`/Ctrl+B/localStorage `dbadmin-sb`）。亮色令牌：品牌 `#1e4d78/#153a5c/#eef3f8`、画布 `#e8eaee`、文字 `#18222d/#4e5a69/#7d8797`、状态 `ok:#0f7a5a/warn:#a9721c/danger:#b4232b`。双主题 `[data-theme="dark"]`（`#171c23`）+ `<head>` 内联脚本防闪白。页面级视图灰画布、表格页纯白；硬编码颜色变量化（暗色斑马纹 `--row-alt:#222932`）；**数据表头禁 `text-transform:uppercase`**。
**表头下拉筛选**（11-20）：列头漏斗 → `#filterMenu.pm-wide` 300px：取值勾选（带次数）+ 搜索 + 固定「（空值）」行 `#pmNull` + 全选/清空 + 折叠高级操作符。后端 `GET /api/tables/<t>/values?col=&q=&limit=200`；`_build_where` 的 `in`/`notin`（`inclEmpty`）**原列比较、不走 `num_expr`**（金额存原文，数值化反而匹配不到）。`PM` 面板态与 `state.filters` 分离，重开用 `vals` 回显。实测 fct_po.供应商名称 156ms（3,268 种）。

## 11 性能（勿回退）
聚合列 `WITH _m AS MATERIALIZED`（0.72s→0.19s）；LIKE 加 `_like_esc()`+`ESCAPE '\'`；导出流式 `fetchmany(8000)`+按下标 `writerows`；`_CON_PRAGMAS` 在 `get_con`/`_ro_con` 都设。

## 12 待决（用户暂缓）
**双轨数字化**：事实表留 `¥30,000.00` 原文 + 视图 `change_type` 转 REAL，绝不原地覆盖（`CAST('abc' AS REAL)`=0.0、精度漂移、丢币种）。

## 13 公司维度
**独立维度表**：`/api/import/new-table-preview|new-table`；全列 TEXT + 单列 PK、不写 FK，必须落 `imported_table` 才能恢复。
`dim_company_map`(63×10) 主键已重编号 `INTEGER PRIMARY KEY AUTOINCREMENT`（`reid_table.py`），**再导入别带 id 列**。关系 `fct_prpo.公司→dim_company_map.Company`（id=37）100% 命中；`fct_po.公司` 同源暂未建关系。地图侧 `CompanyAbbreviation→fct_company_location.Company_cn`（id=48）62→63 扇出 1。**「北京启悦」id=34 + id=38 是历史遗留，用户确认保留两条，勿再合并**；map 63 vs 坐标 62 是预期。

## 14 删除表
`GET /api/tables/<t>/drop-info` + `DELETE /api/tables/<t>`（`confirm` 必须等于表名）。拒绝：`_META_TABLES`、`sqlite_*`、视图、被 `transform_query`/`agg_task` 引用、真外键。级联清 map_config/map_layout/quality_rules/agg_task/imported_table；`_db_backup(tag, keep=5)`。

## 15 框架协议视图 `vw_fa_analysis`
190 行恒定。**`预估金额` 绝不能直接 SUM**（7 张前置审批被 18 份协议共用，行级虚高 5,832 万），汇总一律 `预估金额_可加`（`ROW_NUMBER() OVER (PARTITION BY 前置审批单号)=1`）。金额用 `transform.num_expr()`，防空串 CAST→0。快照：预估 5.83 亿、实际 2.25 亿、执行率 38.6%，105 条 <50%、10 条 >150%、71 条过期仍被引用。

## 16 单页看板 `/dash`
- 文件：`templates/board.html` · `static/board/{board.css,board.js,snapshot.js,logo-sime.png}` · `board_api.py` · `board_export.py` · `supplier_name.py` · `supplier_en_overrides.json`；测试 `tests/board_check.js` 251 条、`tests/snapshot_check.js` 60 条。细节见 `前端建设/00_实现路径规划.md` 附录 G（G.1–G.20）。
- 口径：`dash_po` ← `fct_prpo`（**不用 fct_po**）；FY2627 = 184.7M / 4,869 单 / 974 供应商 / 52 门店；RMB 原值，`RM = RMB/1.5`；周期展示**天**。用户明确去掉 Sub-Category、Spend Flag、PO_flag 占比；供应商只用 `Supplier_cn`；Server → `Region_cn`。
- `dash_build.py`：`Expense Type` 必须取 `fct_prpo."支出类型"` 再查 `dim_agg_支出类型`（曾漏取导致全 NULL）；`PO Paid` = 该 PRPO 关联所有 PO 的 `总支付总额` 之和（Cancelled 不计）。
- **执行率**：分子只算「通过框架协议方式申请」的采购单（`fct_prpo."豁免单一来源的理由"='Ongoing Frame Contract'`，**原始值不映射**，用户已定，勿改回聚合值）；协议汇总 `SUM(PA)/SUM(预估金额_可加)`，**绝不能 SUM 预估金额**（会虚高 5,832 万）。
- 交互：点图 → 全页交叉筛选；点 Top10 / 热力格 → 右侧抽屉；底部「框架协议统计」**独立数据源，不吃 PRPO 全局筛选**。
- **KPI 卡 = 图标块 + 财年 + 大数值 + 差额胶囊**（11-20 照用户参考图）：`.k-top`（`.k-ico` 31px 浅蓝底 #E8F1FE / 图标 #1E6FD9、`.k-label` 12px 700、`.k-fy` 9.5px） + `.k-val` 25px 700 + `.k-bot` 两枚全圆角胶囊（负 #C62828 / 正 #1F7A44 / 差额 v−pv）+ `.k-prev`「vs FY…」。**去掉顶部色条、卡片圆角 8**，`KPI_ICONS` 7 个 inline SVG。**没 pv 就不出胶囊**；`kpiVal()` 符号拼外层（-¥513 M 非 ¥-513 M）；`sub` 前必须补「· 」否则与 " M" 粘连。sticky 高度 243→**288**。
- 视觉：Fidex 主题（画布 `#EEEEEE`、页头 `#1E2F4B→#2C4771`、图表卡**圆角 0**、KPI 卡圆角 8 无顶条）；**字号对齐 `2026-采购数据看板 - 优化度量值.pbip`**（轴 9px `#3B3A39` **600**、轴标题 9px `#605E5C`、图例 9.5px 600、卡片标题 14px、表头 10.5/正文 11px）；变量 `--note:#6E6E6E`、`--axis:#3B3A39`。界面全英文、数据值中文、「?」口径弹层中文（禁 SQL）。
- **月度图两种图示**（11-20）：`.seg-sw` 切换 `S.mMode`。`region`=按区域堆叠+上期虚线+点柱筛区域（画满 12 月）；`pair`=每月 Total(#1E2F4B)/Framework(#E66C37) 双柱+柱顶胶囊，点柱下钻该月（`{fm:n}`），**只画到最后一个有数月份**。后端 `monthly` 一条 SQL 同时返回 `series/total/frame/labelsLong/cur/prev`（`cur` 兼容旧 tooltip）；框架判据 `Exception Reason='Ongoing Frame Contract'` 原始值。**`pair` 不凑整刻度**（`ymax=peak*1.22`，niceMax 会把 72 抬到 100 把柱压到 60%）。快照：`ROW_SQL` 加 `Exception Reason`（`ROW_COLS` 加 `exc`），`snapshot.monthlyOf()` 同步算 frame。
- 坑：`export.csv` 的 `c.close()` 必须放生成器 `finally`；`html{overflow-x:hidden}`（放 body 会让 sticky 失效）；供应商英文名 `supEn()` + `supplier_en_map()`，缺空格用「同名带空格写法」修（`supplier_name.py`，只改显示）；单文件 `board_export.py` ~4.3MB 全内嵌，`snapshot.js` 复算须与后端逐项对齐（空集合 SUM=**null 不是 0**、中位数 `(v[(n-1)/2]+v[n/2])/2`、CSV 带 BOM）。