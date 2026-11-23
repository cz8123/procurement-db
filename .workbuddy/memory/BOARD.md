# 看板 /dash 细节（MEMORY.md §16 拆分，2026-11-23）

## 16 单页看板 `/dash`
**文件**：`templates/board.html` · `static/board/{board.css,board.js,snapshot.js,logo-sime.png}` · `board_api.py` · `board_export.py` · `supplier_name.py` · `supplier_en_overrides.json`；测试见 §8；细节见附录 G。

**口径**：`dash_po` ← `fct_prpo`（**不用 fct_po**）；FY2627 = ¥221.6M / 5,892 单 / 1,081 供应商 / 52 门店 / PO 实付 ¥84.6M / 中位 3.78 天；RMB 原值，`RM = RMB/1.5`；周期展示**天**。去掉 Sub-Category 汇总、Spend Flag、PO_flag 占比；供应商只用 `Supplier_cn`；Server → `Region_cn`。
`dash_build.py`：`Expense Type` 必须取 `fct_prpo."支出类型"` 再查 `dim_agg_支出类型`（曾漏取导致全 NULL）；`PO Paid` = 该 PRPO 关联所有 PO 的 `总支付总额` 之和（Cancelled 不计）。
**执行率**：分子只算 `fct_prpo."豁免单一来源的理由"='Ongoing Frame Contract'`（**原始值不映射**，用户已定）；协议汇总 `SUM(PA)/SUM(预估金额_可加)`，**绝不能 SUM 预估金额**（虚高 5,832 万）。

**交互**：点图 → 全页交叉筛选；点 Top10 / 热力格 → 右侧抽屉；底部「框架协议统计」**独立数据源，不吃 PRPO 全局筛选**。
**全局筛选 5 组 + Region 内嵌 Company**：`FDEFS = brand/category/expense/level/region`，`company` 带 `parent:'region'` 不单独占格；`renderFilters()` 按 `fsec(f)` 拼段，Region 下拉两段（区域 + `Company within region`），各段自己的 `[data-k]` + 独立 Clear（Region 的 Clear 不清 Company）。勾选/清空只走 `updateFilterLabels()` + `refreshCompanyOptions()`（只重渲染公司段），**不整条重建、面板不关闭**；`qs(extra, skip)` 支持排除某键（公司选项查询排除 company 自身，否则自锁）。后端 `options()` 只跳过 supplier（~5000 值走抽屉搜索），`region=…` 时公司收窄（南区 61→22）；Region 额外显示 `· N companies`。
**抽屉 = 按公司聚合**：`/api/board/rows?by=company` 走 `_company_rows()`，`GROUP BY Company_cn, Region_cn` 出 `Company/Region/PRs/Suppliers/Spend/PO Paid/Avg Days`（7 列，`days=AVG("Time Taken")`），不列 PRPO 单号；`total` 是公司数，`amount/poPaid` 是范围内汇总（可与 KPI 对账）。排序键 `_AG_SORTS`，`size` 最小 10。前端 `AG_COLS` + `rowsHtml()`、`pagerHtml(total,page,size,unit)`、副标题 `N companies`；离线 `snapshot.rowsResp()` 有同构分支（已比对 20 家 / ¥17M / ¥9M 一致）。**改列时同步 `board_check` 的「7 列 / 一行一家公司 / 不含 PR No.」「不足一页 Next 禁用」断言**；CSV 导出仍保留逐单 No.。`#drawerPager` 必须自己绑 click（`button[data-pg]` → `S.drawer.page` → `loadDrawer()`）；宽度 `min(1080px,96vw)`、`table-layout:fixed` + `white-space:normal`；`_ROWS_SELECT`/`export.csv` 同步 `poPaid`，CSV 表头 `PO Paid (RMB/RM)`。
**锁背景滚动必须锁 html**：`html` 设过 `overflow-x` 后 body 的 `overflow:hidden` 不再向上传播，`.drawer-open` 要同时加在 `html`/`body`（配 `html{scrollbar-gutter:stable}` 防抖），关时 `window.scrollTo(0,_scrollY)`；`.drawer .tbl-wrap{flex:1;min-height:0}` 让表格自己滚，二者加 `overscroll-behavior:contain`，并在 `#drawer`/`#mask` 上吞掉非表格区域的 `wheel`（分页条在外，滚轮会穿透）。
**Category Details**：7 列 `Category | Sub-Category | Suppliers | PRs | Spend | PO Paid | Share`，列宽 18/19/8/8/14/13/20%。`pareto.rows[].po_paid` 来自 `SUM(COALESCE(p."PO Paid",0))`；**子类别默认折叠**：`pareto.subsMap[cat]` 给全部子类别（v/n/po 按金额降序，`pareto.subMap` 仍给最高项），表格只显示最高项 + `+N`，点开插 `tr.cd-subrow`（`.cs-list` 网格**用 fr 不用 %**，百分比列 + gap 会撑破被裁），列表 `max-height:290px` 自己滚、mini 表头 sticky；`S.catSub` 存展开态，`.cs-tog` 点击要 stopPropagation 否则触发类别筛选。`#catQ` 只过滤表格行，不动 KPI/图表；数据条 `MAXB=0.68`。

**KPI 卡**：无小图标（`KPI_ICONS` 已废）。卡面 `#F8F9FB` + `border:1px solid #D7DAE1` + `overflow:hidden`，`::before` 为**左内缘 4px 竖条** `var(--c)=#435D95`（随外框圆角裁切），`padding-left:13px`。`.k-label` 13px 600 `#5B6473` + `.k-fy` 9.5px + `.k-val` 22px 700 + `.k-bot` 绝对定位右侧两枚直角色块（负 `#C62828` / 正 `#1F7A44`）+ `.k-prev`「vs FY…」。`min-height:90px`；数值右侧留 `calc(100% - 104px)` 防重叠；**没 pv 就不出色块**；`kpiVal()` 符号拼外层（`-¥513 M`）。

**视觉**：画布 `#E7E9ED`、页头 `#1E2F4B→#2C4771`；**只外层视觉对象圆角 `--r:8px`，内部控件与图表元素一律直角**（口径弹层也算外层）。栅格 gap **20px**、卡片边框 `#D6DAE1`、`.grid{align-items:start}` 按内容高度摆放。月度图 g9 / 环形 g3 / 帕累托 g7 / Top10 g5 / 类别明细与热力图 g6 / 协议表 g12。
**图表内文字由 CSS 变量控**：`.chart svg text`=**12px**、`.t-note`=11px、`.t-big`=18px；卡片标题 14px、图例/提示 11px、表头 11 / 正文 11.5px。**改字号必须同步 JS 里 `truncPx(..., fs)` 的 fs 参数**，否则溢出。界面全英文、数据值中文、「?」弹层中文（禁 SQL）。
**不画背景横线**：月度/帕累托删掉所有网格 `<line>`，只留刻度文字；区域与双柱共用**同一条零轴**，帕累托 80% 红虚线是业务判据。所有图表/控件 `rx=0`。**柱形按数值做色阶**：共用 `ramp(v,max,light,dark,minK)`；双柱 Total `#B1BFD5→#1E2F4B`、Framework `#D6E2F0→#3B5E96`（**同色系蓝阶、不用橙色**）；帕累托与 Top10 浅蓝→藏青。热力图 `mix('#E8F1FE','#1E2F4B',k)`、空格 `#F7FAFF`、**无线框**、格高 40px。**Top10 金额写在条内右端**（条长 > 文字宽+14 才内嵌，浅条 `#10304F`、深条白字）。帕累托累计折线与点为橙色 `#E66C37`。
**表头**（11-23 终版）：统一令牌 `--hdr-bg:#8CAACD` / hover `#7B9BC1` / 边线 `#6E8DB4` / 字 `#122745`（对比度 6.24）；`.tbl` 与 `.cd-tbl` 同一套。Usage 数据条相应调浅到 `#B6CCE5`（加深后撞色）。
**协议表**：`table-layout:fixed` + `th:nth-child(n)` 百分比 23/16/9.5/9.5/10.5/9/9/9.5/4（`FC_COLS` 不写 px）；Usage 列 `td.u-cell` 条宽 = `sqrt(usage/本页maxUsage)` 开方归一 + 4% 保底，超 100% 转 `#E08C8C`；**Remain Days = 17px 正圆徽标 + 白字 11px/700**，>90 天绿 `#2F7D4F` ✓、≤90 琥珀 `#C8912A` !、负数红 `#C0392B` ✕、无到期日灰 `#BFC5CE` –（`title` 给英文语义），天数本身 `--ink-2` 不跟徽标变色。

**月度图两种图示**：`.seg-sw` 切 `S.mMode`。`monthlyGeom(d)`/`monthLabels(d)`/`yAxisText()` 两图**共用坐标与 scale**：`L=58,R=14,T=42,B=42,H=290`、`ymax=niceMax(max(all)*1.12)`，**切换时刻度/基线完全不动**；月份都用 `labelsLong`。`region`=区域堆叠+上期虚线+点柱筛区域；`pair`=**单柱堆叠**（底段 Framework `#C9D9EC→#1E3A63`、上段 Non-framework `#E3EDF9→#8FB3D9`，柱顶=当月 Total，图例 `Framework Contract`/`Non-framework`），点柱下钻该月（`{fm:n}`）；**两者都画满 12 个财月**。共用 `barW(slot)=min(52,slot*0.62)`；**柱顶胶囊 `mCap()` 已废**，金额用 `segLabel()` **写在柱段内**（段高 <17px 或文字超宽就不显示，浅底深字、深底白字）。后端 `monthly` 一条 SQL 返回 `series/total/frame/labelsLong/cur/prev`；框架判据 `Exception Reason='Ongoing Frame Contract'` 原始值。快照 `ROW_SQL` 加 `Exception Reason`（`ROW_COLS` 加 `exc`），`snapshot.monthlyOf()` 同步算 frame。
**坑**：`export.csv` 的 `c.close()` 必须放生成器 `finally`；`html{overflow-x:hidden}`（放 body 会让 sticky 失效）；供应商英文名 `supEn()` + `supplier_en_map()`，缺空格用「同名带空格写法」修（`supplier_name.py`，只改显示）；`snapshot.js` 复算须与后端逐项对齐（空集合 SUM=**null 不是 0**、中位数 `(v[(n-1)/2]+v[n/2])/2`、CSV 带 BOM）。

## 17 看板接口缓存与首屏性能（11-23）
- **首屏慢的真因是串行 + 重复请求，不是 SQL**：原来 `options → contracts?size=1 → data → top/contracts` 五连 @1519ms。改：①`init()` 用 `Promise.all([loadOptions(), loadAll()])`（data 不带 fy 时后端取最新财年，与 `options.latestFy` 同口径）；②`loadOptions` 不再单打 `contracts?size=1` 拿 Type/日期边界，改由 `loadContracts` 响应的 `types/bounds` 经 `fillContractMeta()` 填充（只在空时构建）。→ **4 个请求**、冷启 329ms、热刷新图表就绪 **~97ms**（`perf_check.js` 守）。
- **三层缓存**：`_db_stamp()` = (`procurement.db`,`-wal`) 的 (mtime_ns,size)，写入自动失效（touch 库 → 508ms 重算，再请求 5ms）。①`_cached(key,build)` 给 `options` 与 `fa_meta`；②蓝图级 `before_request/after_request` 缓存整段 JSON（`/data /top /contracts /options`，上限 40 条，键=路径+排序后 args）；③`supplier_en_map` 进程内缓存（首次 367ms，之后 0）。
- **协议接口 4 次视图扫描压到 1 次**：`vw_fa_analysis` 一次求值 ~125ms。rows 查询用 `COUNT(*) OVER ()`、`SUM(...) OVER ()` 把汇总并进去（窗口在 WHERE 后、LIMIT 前＝筛选全集），types/bounds 走缓存，越界空页回退单查汇总。353ms → 122ms 冷 / 19ms 热，数值逐项不变。

## 18 离线单文件导出（按需点击）
`POST /api/board/export`：后台线程 `subprocess` 调 `board_export.py`，**不能 import**——`python app.py` 时本模块是 `__main__`，`from app import app` 会二次加载并建第二个 Flask 实例。另 `GET /api/board/export/status`（`_EXPORT_JOB` + `exports/` 最近 8 个文件）、`GET /api/board/export/download?f=`（只允许 `exports/` 下 `.html`，已挡路径穿越）。单任务互斥（重复 POST 409），导出约 2.6–3s。
前端：顶栏「导出看板」→ 弹层 `#bemMask`（`.bem-*`，含暗色分支）→「开始导出」，显示文件名/大小/行数/协议数/用时 + 历史列表。
**竞态坑**：点按钮前发出的轮询可能带上一轮 `done` 晚到，把新任务盖成已完成、按钮卡在「导出中…」→ `bemSeq` 自增序号，回调 `if(seq!==bemSeq) return`，`bemRun()` 先 `bemSeq++` 再 POST 并同步写「正在启动导出…」。

## 19 视觉对象返回 / 版式 / 零位移（11-23）
- **每张图只对自己造成的维度负责**：`<button class="v-back" data-clear="region|expense|category" hidden>`，`updateBacks()` 在 `renderChips()` 末尾刷新，点击 `setSel(k,[])` + `renderFilters()` + `loadAll()`。**形态按 Power BI 视觉对象头部**（用户否决文字胶囊版）：22×22 纯图标（`↰` 曲线箭头、无文字）、无边框、灰 `#5B6473`、hover 才出浅底 `#E8EDF4`、`title` 写「Back — clear Region: 南区」，放 `.card-tools` **最后一位**。月度图**外包等宽 `.v-slot`(22px) 占位**，图标出现/消失时 `#mSw` 不位移（用户提过两次）。
- **饼图满环必须可点**：`frac > 0.9999` 那支曾画成无 `data-exp` 的 `<circle>`，筛到单一类型后整圈点不动。已补 `class="dseg" data-exp`。任何可点击图形元素都要带 class + data。
- **版式**：`--pad`（32px；≤1280→18、≤900→12）统一 `.hd/.kpis/.fbar/.grid` 左右留白，四块左边缘对齐。KPI 7 列在 ≤1680 降 4 列两行（1440 下 7 列会把大数值截成 `¥2…`）。
- **钻取时整页不许位移**：`#chips` **在筛选条 `.fbar-r` 里、Reset 左边**——`flex-wrap:nowrap` + `max-width:min(38vw,380px)` + 横向滚动（滚动条隐藏）、`:empty{display:none}`（用户否掉 26px 固定留白行）。筛选条高度由 24px 的 Reset 决定，chips 出现不撑高。另：`.card-tools{min-height:22px}` + `.card-h{min-height:32px}`，返回图标出现不顶高标题栏。实测 1440/1920 连点「类别→类型→区域」，fbar 63 / sticky 338·236 / 5 张图 top 全部不变；≤1280 单列堆叠时因卡片内容变矮会重排（内容变化，非布局 bug）。
