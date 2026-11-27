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

**KPI 卡**：无小图标（`KPI_ICONS` 已废）。卡面 `#F8F9FB` + `border:1px solid #D7DAE1` + `overflow:hidden`，`::before` 为**左内缘 4px 竖条** `var(--c)=#435D95`（随外框圆角裁切），`padding-left:13px`。`.k-label` 14px 600 `#1F2A36` + `.k-fy` 10.5px + `.k-mid`（flex 行，垂直居中）内 `.k-val` 24px 700 + `.k-bot` 两枚 `min-height:20px / 10.5px` 直角色块（负 `#C62828` / 正 `#1F7A44`）+ `.k-prev`「vs FY…」。`min-height:96px`；**没 pv 就不出色块**；`kpiVal()` 符号拼外层（`-¥513 M`）。7 列在 ≤1840px 降 4 列两行（1726 下 7 列会把 `¥184.7M` 截成 `¥184…`）。

**视觉**：画布 `#E7E9ED`、页头 `#1E2F4B→#2C4771`；**只外层视觉对象圆角 `--r:8px`，内部控件与图表元素一律直角**（口径弹层也算外层）。栅格 gap **20px**、卡片边框 `#D6DAE1`、`.grid{align-items:start}` 按内容高度摆放。月度图 g9 / 环形 g3 / 帕累托 g7 / Top10 g5 / 类别明细与热力图 g6 / 协议表 g12。
**图表内文字由 CSS 变量控**：`.chart svg text`=**13px / font-weight 600（全图统一字重，不再混杂 400/600）**、`.t-note`=11.5px（同为 600）、`.t-big`=20px；卡片标题 15.5px、图例/提示 11.5px、表头 12 / 正文 12.5px。**改字号必须同步 JS 里 `truncPx(..., fs)` 的 fs 参数**（现为 13），否则溢出。界面全英文、数据值中文、「?」弹层中文（禁 SQL）。正文 `--ink:#0E1720`、`--ink-2:#1F2A36`；**只有小字备注允许灰**（`--note:#6E6E6E`），轴/图例/表格正文统一深色。
**不画背景横线**：月度/帕累托删掉所有网格 `<line>`，只留刻度文字；区域与双柱共用**同一条零轴**，帕累托 80% 红虚线是业务判据。所有图表/控件 `rx=0`。**柱形不再按数值做色阶**（用户 11-23 明确：图例靠颜色区分，不要用色阶表达大小）：区域堆叠用 `CAT_COLORS` 固定分类色（`#1E2F4B/#3B5E96/#5E9BB8/#2F7D6B/#C08A2E/#8A5A8F/#6B7A8F/#B5544A`），双柱 Framework `PAIR_FRM=#1E3A63` / Non-framework `PAIR_OTHER=#8FB3D9`，帕累托与 Top10 单一 `#3B5E96`，类别明细/子类别条 `#3B5E96/#8FB3D9`。**热力图仍保留色阶**（颜色=数值，本身是热力语义）。**Top10 金额写在条内右端**（条长 > 文字宽+14 才内嵌，深条白字，其余 `#1F2A36`）。帕累托累计折线与点为橙色 `#E66C37`。**所有坐标轴数值取整**（`axisMoney` 0 位小数，不再 18.5M）。
**表头**（11-23 终版）：统一令牌 `--hdr-bg:#8CAACD` / hover `#7B9BC1` / 边线 `#6E8DB4` / 字 `#122745`（对比度 6.24）；`.tbl` 与 `.cd-tbl` 同一套。Usage 数据条相应调浅到 `#B6CCE5`（加深后撞色）。
**协议表**：`table-layout:fixed` + `th:nth-child(n)` 百分比 23/16/9.5/9.5/10.5/9/9/9.5/4（`FC_COLS` 不写 px）；Usage 列 `td.u-cell` 条宽 = `sqrt(usage/本页maxUsage)` 开方归一 + 4% 保底，超 100% 转 `#E08C8C`；**Remain Days = 17px 正圆徽标 + 白字 11px/700**，>90 天绿 `#2F7D4F` ✓、≤90 琥珀 `#C8912A` !、负数红 `#C0392B` ✕、无到期日灰 `#BFC5CE` –（`title` 给英文语义），天数本身 `--ink-2` 不跟徽标变色。

**月度图两种图示**：`.seg-sw` 切 `S.mMode`。`monthlyGeom(d)`/`monthLabels(d)`/`yAxisText()` 两图**共用坐标与 scale**：`L=58,R=14,T=42,B=42,H=290`、`ymax=niceMax(max(all)*1.12)`，**切换时刻度/基线完全不动**；月份都用 `labelsLong`。`region`=区域堆叠+上期虚线+点柱筛区域；`pair`=**单柱堆叠**（底段 Framework `#C9D9EC→#1E3A63`、上段 Non-framework `#E3EDF9→#8FB3D9`，柱顶=当月 Total，图例 `Framework Contract`/`Non-framework`），点柱下钻该月（`{fm:n}`）；**两者都画满 12 个财月**。共用 `barW(slot)=min(52,slot*0.62)`；**柱顶胶囊 `mCap()` 已废**，金额用 `segLabel()` **写在柱段内**（段高 <17px 或文字超宽就不显示，浅底深字、深底白字）。后端 `monthly` 一条 SQL 返回 `series/total/frame/labelsLong/cur/prev`；框架判据 `Exception Reason='Ongoing Frame Contract'` 原始值。快照 `ROW_SQL` 加 `Exception Reason`（`ROW_COLS` 加 `exc`），`snapshot.monthlyOf()` 同步算 frame。
**坑**：`export.csv` 的 `c.close()` 必须放生成器 `finally`；`html{overflow-x:hidden}`（放 body 会让 sticky 失效）；供应商英文名 `supEn()` + `supplier_en_map()`，缺空格用「同名带空格写法」修（`supplier_name.py`，只改显示）；`snapshot.js` 复算须与后端逐项对齐（空集合 SUM=**null 不是 0**、中位数 `(v[(n-1)/2]+v[n/2])/2`、CSV 带 BOM）。

## 17 看板接口缓存与首屏性能（11-23）
- **首屏慢的真因是串行 + 重复请求，不是 SQL**：原来 `options → contracts?size=1 → data → top/contracts` 五连 @1519ms。改：①`init()` 用 `Promise.all([loadOptions(), loadAll()])`（data 不带 fy 时后端取最新财年，与 `options.latestFy` 同口径）；②`loadOptions` 不再单打 `contracts?size=1` 拿 Type/日期边界，改由 `loadContracts` 响应的 `types/bounds` 经 `fillContractMeta()` 填充（只在空时构建）。→ **4 个请求**、冷启 329ms、热刷新图表就绪 **~97ms**（`perf_check.js` 守）。
- **三层缓存**：`_db_stamp()` = (`procurement.db`,`-wal`) 的 (mtime_ns,size)，写入自动失效（touch 库 → 508ms 重算，再请求 5ms）。①`_cached(key,build)` 给 `options` 与 `fa_meta`；②蓝图级 `before_request/after_request` 缓存整段 JSON（`/data /top /contracts /options`，上限 40 条，键=路径+排序后 args）；③`supplier_en_map` 进程内缓存（首次 367ms，之后 0）。
- **协议接口 4 次视图扫描压到 1 次**：`vw_fa_analysis` 一次求值 ~125ms。rows 查询用 `COUNT(*) OVER ()`、`SUM(...) OVER ()` 把汇总并进去（窗口在 WHERE 后、LIMIT 前＝筛选全集），types/bounds 走缓存，越界空页回退单查汇总。353ms → 122ms 冷 / 19ms 热，数值逐项不变。
- **启动预热（11-24）**：`app.py` 的 `__main__` 在 `serve_forever` 前起 `board-warmup` daemon 线程，调 `board_api.warm_up(app)`；内部用 `app.test_client()` 打首屏那 4 条默认 URL（`WARMUP_URLS` + 动态拼 `WARMUP_TOP`），把整响应缓存、`supplier_en_map`、SQLite 页缓存一起热起来。**冷盘首访 5.9s → 预热后首访服务端 2–25ms、图表就绪 ~80ms**；预热本身约 1.0s（磁盘热时），冷盘更久但不挡开服。`WARMUP=0` 可关。
  - **URL 必须与前端默认请求逐字一致**（参数进缓存键）：`/options`、`/data?cur=RMB`、`/contracts?cur=RMB&page=1&size=50&sort=start&dir=desc`、`/top?cur=RMB&fy=<当前财年>`。**Top10 的 fy 不能写死**——前端是 data 返回后才填 `S.fy`，所以预热要先跑 data、从响应里取 `fy` 再拼（`perf_check.js` 第 4 组断言钉住这条契约，改前端默认参数会红）。
  - 设了 `DBADMIN_TOKEN` 时预热请求没有 token/cookie 会被 401 挡掉 → `app.py` 加 `_WARMUP_TLS` 线程局部标记，`_access_guard` 见到就放行（只影响预热线程，实测无 token 的正常请求仍 401）。前端 XHR 用的是 cookie 不带 `token` 参数，所以缓存键与预热一致；手动 `?token=…` 调试会 miss，属正常。
  - 排查要 `PYTHONUNBUFFERED=1` 启动，否则 print 被块缓冲、日志一条都看不到。

## 18 离线单文件导出（按需点击）
`POST /api/board/export`：后台线程 `subprocess` 调 `board_export.py`，**不能 import**——`python app.py` 时本模块是 `__main__`，`from app import app` 会二次加载并建第二个 Flask 实例。另 `GET /api/board/export/status`（`_EXPORT_JOB` + `exports/` 最近 8 个文件）、`GET /api/board/export/download?f=`（只允许 `exports/` 下 `.html`，已挡路径穿越）。单任务互斥（重复 POST 409），导出约 2.6–3s。
前端：顶栏「导出看板」→ 弹层 `#bemMask`（`.bem-*`，含暗色分支）→「开始导出」，显示文件名/大小/行数/协议数/用时 + 历史列表。
**竞态坑**：点按钮前发出的轮询可能带上一轮 `done` 晚到，把新任务盖成已完成、按钮卡在「导出中…」→ `bemSeq` 自增序号，回调 `if(seq!==bemSeq) return`，`bemRun()` 先 `bemSeq++` 再 POST 并同步写「正在启动导出…」。

## 19 视觉对象返回 / 版式 / 零位移（11-23）
- **每张图只对自己造成的维度负责**：`<button class="v-back" data-clear="region|expense|category" hidden>`，`updateBacks()` 在 `renderChips()` 末尾刷新，点击 `setSel(k,[])` + `renderFilters()` + `loadAll()`。**形态按 Power BI 视觉对象头部**（用户否决文字胶囊版）：22×22 纯图标（`↰` 曲线箭头、无文字）、无边框、灰 `#5B6473`、hover 才出浅底 `#E8EDF4`、`title` 写「Back — clear Region: 南区」，放 `.card-tools` **最后一位**。月度图**外包等宽 `.v-slot`(22px) 占位**，图标出现/消失时 `#mSw` 不位移（用户提过两次）。
- **饼图满环必须可点**：`frac > 0.9999` 那支曾画成无 `data-exp` 的 `<circle>`，筛到单一类型后整圈点不动。已补 `class="dseg" data-exp`。任何可点击图形元素都要带 class + data。
- **版式**：`--pad`（44px；≤1280→24、≤900→16）统一 `.hd/.kpis/.fbar/.grid` 左右留白，四块左边缘对齐。KPI 7 列在 ≤1840 降 4 列两行（1726 下 7 列会把大数值截断）；≤1280 单列布局时 `.card-tools` 允许换行（`flex:1 1 100%`），否则协议卡筛选会把页面顶出 58px。
- **钻取时整页不许位移**：`#chips` **在筛选条 `.fbar-r` 里、Reset 左边**——`flex-wrap:nowrap` + `max-width:min(38vw,380px)` + 横向滚动（滚动条隐藏）、`:empty{display:none}`（用户否掉 26px 固定留白行）。筛选条高度由 24px 的 Reset 决定，chips 出现不撑高。另：`.card-tools{min-height:22px}` + `.card-h{min-height:32px}`，返回图标出现不顶高标题栏。实测 1440/1920 连点「类别→类型→区域」，fbar 63 / sticky 338·236 / 5 张图 top 全部不变；≤1280 单列堆叠时因卡片内容变矮会重排（内容变化，非布局 bug）。

## 20 标题栏页签：多页面切换基础（11-24）
- 用户给了 Power BI 页头截图（白底选中页 + 灰底未选中页 + 下方小标题/下划线），要求在标题栏加页签作后续多页面基础。
- **结构**：`templates/board.html` 顶栏顺序 = `.hd-l`（品牌）→ `.hd-r`（FY / 币种 / Export View，挪到中段）→ `<nav class="pg-tabs" id="pgTabs">`（**贴最右**，按用户 11-24 下午要求从原来的中间位置换过去）。`.hd-r` 与 `.pg-tabs` 各带 `margin-left:auto`，把剩余空间均分成两段，控件自然落在中间；JS 按 `PAGES` 注册表生成按钮。`.pg-tab` 固定 50px＝顶栏内容高度（上块 30 + 下标题行 20），页签只切 class、不换结构 → 顶栏/控件/KPI 零位移。
- **视觉（11-24 终版）**：用户要求**不要小标题行**，只留大标题 → 删掉 `.pg-tab-sub` 与下划线，`.pg-tab-top` 撑满整块 50px、字 14px/700；选中页 `#fff` + `#0E1720`，未选中 `#A9B1BC` + 深灰字，圆角上沿 `6px`、hover 浅一号，`aria-current="page"`。**页签下沿必须与顶栏下沿齐平**：`.pg-tabs{position:relative;top:9px}`（顶栏上下各 9px padding，relative 不占布局，顶栏仍 68px）。
- **接口**：`board.js` 里 `PAGES = {overview, spend, framework}`，`window.BoardPages = {go(key), register(key,{label,render}), current()}`。新增真页面只要 `register` 带 `render`（切页时跑），页签自动出现。**未实现页点击只 toast「… is under preparation」，不改当前页**——否则页头显示新页、内容还是 Overview，会误导。Overview 的 render = `loadAll`。
- **响应式**：`@media ≤1600` 缩标题/副标题/logo，`≤1280` 页签 112px + 12px 字，保证 1280–1920 顶栏恒定 68px 且不溢出/不截断；≤900 顶栏允许换行（移动端回落）。
- **测试**：`tests/page_tabs_check.js` **15/0**（页签结构/选中态样式/未实现页不伪装/零位移/register+go/两档宽度）。离线单文件复用同一套 HTML/CSS/JS，自动带上页签，用临时导出跑 `snapshot_check` 校验。


## 21 Detail 大表格 + 三页真正切换（11-24）
- **三页都可点**：`PAGES = {overview, detail, framework}`，`goPage()` 先调旧页 `leave()` 再调新页 `enter()`。`#pgOverview / #pgDetail / #pgFramework` 三个 `<main>` 靠 `hidden` 切换；`main[hidden]{display:none!important}`。
- **Framework 页不复制数据**：`enterFramework()` 把 `#fcCard` **搬**到 `#pgFramework`，`leaveFramework()` 放回原处（记 `FC_HOME={parent,next}`）。避免重复取数/重复绑定；`board_check` 默认在 Overview，不受影响。
- **第二页改名 Detail**：`/api/board/detail` 分页返回 7 列 `Region/Company/Category/Supplier/Procurement Method/PRPO Amount/PO Paid`，`SUM` 走筛选全集（不是当前页）；`/api/board/detail/values?col=` 给表头取值列表（带搜索、限 200）。全局筛选沿用 `_scope(args, fy)`；列筛选参数 `q_<key>`（LIKE 转义）、`f_<key>`（JSON 数组 IN）、`min_/max_amount|paid`（区间）。**两条路径都进了 `_JSON_CACHE_PATHS`**。注意 `board_api.py` 必须 `import json`，否则 `f_*` 解析直接 500。
- **前端**：`DT_COLS` 定列宽 11/15/14/25/15/10/10%；点标题排序（数值默认 desc，文本 asc），点漏斗开 `#dtPop`：文本列 = 搜索（220ms 防抖拉 values）+ 勾选，数值列 = Min/Max；`Clear filters` 一键清。数值列淡色数据条 `amount:#DCE7F5`、`paid:#D7EBE5`，`tfoot` 合计行 sticky。`loadAll()` 里若当前在 Detail 页会同步 `loadDetail()`（全局筛选/币种切换即时刷新）。
- **离线**：`snapshot.js` 复用已内嵌的 46k 行（`region/company/category/supplier/method/amount/poPaid` 都在 `ROW_COLS`）本地复算 `detail` / `detail/values` 两个垫片，合计/筛选与在线逐项一致。
- 测试：新增 `tests/detail_table_check.js` **14/0**（7 列 + 每列排序筛选、合计=全集合计、取值/搜索/区间、分页、RMB→RM 换算 0 JS 报错）；`page_tabs_check` 改为「三页真切换 + 协议卡搬家归位」**19/0**。


## 22 三页版式收敛：Overview 去协议表 / Detail 区域折叠（11-24）
- **Overview 不再有框架协议表**：`#fcCard` 从 `main.grid` 移到 `#pgFramework` 内（不是切换时搬 DOM），Overview 底部以热力图收尾。Framework 页用它自己的局部筛选（type/status/date/q/supplier），**不依赖全局筛选**。
- **Detail / Framework 隐藏全局筛选条**：`showFilterBar(false)` 在 `enterDetail/enterFramework`，回 Overview 时恢复；`main[hidden],.page[hidden],.fbar[hidden]{display:none!important}`。KPI 行仍保留。注意隐藏的 `main` 拿不到几何——`ui_focus_check` 量列宽要切 Framework，量留白要切回 Overview。
- **Detail 区域分组（已被 §23 三级树取代）**：早期按页 100 行、只有区域一级折叠；用户纠正「怎么只有中国区和南区」→ 分页导致只看到第一页。
- **回归调整**：`board_check` 进第 9 节协议表前先切 Framework、第 11 节切回 Overview；`snapshot_check` 点 `#fcExport` 前切 Framework；`ui_focus_check` 分两页量几何。

## 23 Detail 三级树：默认全折叠 + 无分页（11-24）
- **用户要求**：「默认折叠全部，不要分页，公司也要有折叠」。`/api/board/detail` 去掉 LIMIT/OFFSET、一次返回筛选全集（FY2627 = 5,892 行），排序仍生效；新增 `companyTotals`（`GROUP BY region, company` 的 n/amount/paid，按币种换算），与 `regionTotals`/`sums` 同口径都是筛选全集。**响应不再有 page/size 字段**，前端 `dtParams()` 也不再传 page/size；模板删掉 `#dtPager`，`$('.pager')` 逻辑仅剩 Framework/抽屉用。
- **三级树渲染**：`renderDetail()` 按行顺序建 region→company→rows 树（默认 sort=region asc，其他排序在组内生效），依次渲染 `.dt-group`（区域，首列 `−/+` + 名 + 条数 + 小计占两数值列）→ `.dt-company`（公司，`dtCoKey=region+'\u0001'+company`，Company 列 `−/+` + 名 + 条数，colspan=3 "company subtotal"）→ `.dt-child`（明细，首列 `└`）。**默认全折叠**：`S.detail.expR[region]` / `S.detail.expC[coKey]` 空＝全收起；点按钮只重渲染缓存 `S.detail.data`，不重新请求。`data-toggle-company` 藏在按钮上，事件委托先判公司再判区域。
- **离线同步**：`snapshot.js` `detailResp()` 去掉 slice、返回全量行 + 同名 `companyTotals`，折叠逻辑与在线完全同构。`exports/` 官方单文件**不自动重跑**（用户约定），回归验证用 `board_export.py --out <临时路径>` + `SNAP_FILE=<路径>` 跑 `snapshot_check`。
- **测试**：`detail_table_check.js` 重写为三级树断言 **19/0**（7 列/合计/默认全折叠/区域展开→公司行/公司展开→明细行数=公司小计 n/再点收起/排序/取值+搜索筛选/区间/币种，无分页条）。其它回归：`board_check` 286/0、`snapshot_check`(临时快照) 60/0、`ui_focus_check` 38/0、`page_tabs_check` 20/0。

## 24 后两页隐藏 KPI 行（11-24）
- **用户要求**：「后两个页面也不需要上方的 KPI 卡片了」。固定区里的 `#kpis` 与 `.fbar` 一样是全局 DOM（不在各 `<main>` 里），原来 `showFilterBar()` 只管筛选条；改名 `showChrome(show)` **同时切 `bar.hidden` 与 `kpis.hidden`**，Overview 的 `enter` 调 `showChrome(true)`，Detail/Framework 的 `enter/leave` 调 `false/true`。
- **CSS**：`.kpis[hidden]{display:none!important}` 必须补（`.kpis{display:grid}` 会盖掉 hidden 默认样式）——和 `main[hidden],.fbar[hidden]` 同一条规则。切页顶栏控件零位移不受影响（量的是 `.hd` 与控件坐标，不是 KPI）。
- **测试**：`page_tabs_check` 原「切页不移动 KPI」改为「不移动顶栏控件 + Detail/Framework 隐藏 KPI/筛选条 + 回 Overview 恢复」，现 20/0；`board_check` 286/0、`ui_focus_check` 38/0、`detail_table_check` 19/0 均通过。

## 25 后两页表格铺满视口（11-24）
- **用户要求**：「两页的表格长一些，铺满页面」。隐藏 KPI/筛选后固定区只剩 68px 顶栏，但 `.dt-wrap{max-height:calc(100vh-330px)}`（老布局预留）和 `.tbl-wrap{max-height:400px}` 让表格只占上半屏；实测 Detail 213px / Framework 400px，下方大片空白。
- **改法**：`#pgDetail,#pgFramework{height:calc(100vh-68px);display:flex;flex-direction:column}`；`> .card{flex:1 1 auto;min-height:0;display:flex;flex-direction:column}`；`.dt-wrap` / `#pgFramework .tbl-wrap` 改 `flex:1 1 auto;min-height:0;max-height:none`（响应式 1280px 那条 `max-height:calc(100vh-420px)` 也改 none）。
- **坑**：Flex 列容器下 `.card-h`/`.fc-summary`/`.pager` 必须 `flex:0 0 auto`，否则 `.fc-summary` 上移盖住 `#fcReset`（`elementFromPoint` 命中 `.fs-l`），`board_check` 的「fc reset」waitFor 超时。修后按钮命中自身。
- **实测**（1600×1000）：Detail 表格 213→840px、Framework 400→697px（+分页条），`bodyScroll=1000` 正好等于视口、无多余滚动；1280px 同样铺满。回归：`page_tabs_check` 20/0、`ui_focus_check` 38/0、`detail_table_check` 19/0、`board_check` 286/0、`snapshot_check`(临时快照) 60/0。

## 26 区域按金额排序 / 全选 / 采购方式饼图（11-24）
- **用户要求**：①区域按采购额降序（大的在上），所有筛选加全选；②Overview 饼图右边再加一个按 Procurement Method 的饼图。
- **区域排序**：`renderDetail()` 建树后 `tree.sort((a,b)=>reg[b].amount-reg[a].amount)`、公司 `g.order.sort` 同样按 `companyTotals.amount` 降序；明细行组内仍按后端排序。后端 `regionTotals` ORDER BY 改 `3 DESC`（金额）。
- **全选**：`fsec()` 头部加 `<span class="fh-acts"><a data-act="all">All</a><a data-act="none">Clear</a></span>`（CSS `.fh-acts{display:inline-flex;gap:10px}`），`#fbar` click 事件 `data-act=all` 时勾选该段全部 input 并 setSel（All 语义=全选=不过滤）；Detail `#dtPop` 文本列加 `[data-act=all]` 按钮，`fetchDtValues` 把当前列表存 `S.detail.tmp.list`，点 All 勾选全部。管理台 `#filterMenu` 早有全选无需改。
- **采购方式饼图**：`board_api.py` `/data` 新增 `methodDonut`（SQL 同 donut，`p."Procurement Method"` 空值归 Unassigned），`spec["methodDonut"]`；`FDEFS` 加顶层 `method`（chips/返回/全选自动支持），`BACK_LABEL` 早有 method。模板第一行 g9→g6，新增 g3 `#chMethodDonut`/`#methodDonutLegend` + `v-back[data-clear=method]` + `spec-btn[data-spec=methodDonut]`。`renderDonut(field,chartId,legendId)` 泛化，两个调用点；点击 `bindDonut(chartId,legendId,key)` 共用，tooltip/SPEC 同步。离线 `snapshot.js` `donutOf(list,field,blank)` 泛化、`methodDonut=donutOf(list,'method','Unassigned')`。
- **视觉代价与修复**：g9→g6 后月度图柱宽 52→34px，`segLabel` 窄图用整数格式 + 阈值 `txt.length*6 > w-4`，月份标签 `truncPx(...,slot*0.16)` 保证 12 个全称仍显示；`.g3 .legend-list{height:80px}` 固定图例高度 + **两列 grid**（8 项 = 4 行正好放满，见 §30），点扇区筛到单类时下方热力图不位移（ui_focus 断言）。
- **测试**：`board_check` 291/0（筛选下拉 5→6、说明按钮 20→22、新增 method 扇区点击+chip+金额+返回）、`detail_table_check` 21/0（区域金额序 + All）、`snapshot_check` 61/0、`ui_focus_check` 38/0、`page_tabs_check` 20/0。

## 27 采购方式饼图两层下钻（11-24）
- **用户要求**：点 SS-Exempt / SS-Non Exempt 时饼图下钻到子类别 —— 前者按「豁免单一来源的理由」、后者按「单一来源采购理由」，都走 `dim_agg_*` 聚合映射。
- **后端**：`REASON_DRILL = {SS-Exempt: (豁免单一来源的理由, dim_agg_豁免单一来源的理由), SS-Non Exempt: (单一来源采购理由, dim_agg_单一来源采购理由)}`；**单独接口** `GET /api/board/method-drill?method=`（0.16s）。放 `/data` 里会让每次整页刷新多跑两次全表 JOIN（实测 2.5s），所以按需取；已进 `_JSON_CACHE_PATHS`。SQL 用 `dash_po p LEFT JOIN fct_prpo f ON f."表单编号"=p."No."` + 子查询别名映射表（`SELECT "原始值" src, X agg`），避开同名列歧义；不加 JOIN 会放大行数（已验证 JOIN 后 5,892 行/¥221,626,955.19 与不 JOIN 一致）。
- **理由筛选**：`REASON_FILTERS = {exempt_reason: 豁免单一来源的理由, single_reason: 单一来源采购理由}`，在 `_filters()` 里拼 `p."No." IN (SELECT "表单编号" FROM fct_prpo WHERE 列 IN (...))`，避免主查询都 JOIN；列名带 `p.` 前缀其余 FILTERS 不带（它们只在 dash_po 有，不歧义）。
- **前端**：`S.methodDrill`（null|SS-Exempt|SS-Non Exempt）+ `S.drill` 缓存；`renderMethodDonut()` 决定第一层/理由层，标题 `#mDonutTitle` 切成 `Exemption Reason · SS-Exempt`；点第一层两种方式=下钻（不产生 chip），点理由=同时锁 `method` + 理由交叉筛选（两个 chip）；**返回按钮在有 `methodDrill` 时先退出下钻**（不是清 method），`#btnReset`/chip 移除也会退出。其它方式（Quotation/Micro/Tender/VO）仍是普通交叉筛选。`renderChips` 用 `EXTRA_CHIP_LABEL` 补 `exempt_reason/single_reason`（它们不在 FDEFS，不占筛选下拉）。
- **离线**：`board_export.py` `ROW_COLS` 加 `ssr`（`单一来源采购理由` 子查询取）、注入 `reasonMap`（17 条映射）；`snapshot.js` `methodDrillOf(list)` + `methodDrillResp(sp)` + fetch 垫片 `method-drill` 路径；`readFilters/pass` 支持两个 reason key（映射到行内 `exc`/`ssr`）。
- **测试**：`board_check` 重写 5b 为两层断言（下钻标题/理由列表/返回按钮 → 点理由两 chip + 金额收窄 → 返回退出清筛选 → SS-Non Exempt 另一张表 → Quotation 普通筛选）294/0；`snapshot_check` 加离线下钻同构 62/0。

## 28 看板切换到供应商归并口径（11-24）
- **用户要求**：「改成归并口径」。`dash_build.py` 建 `dash_po` 前自动跑 `supplier_dedup.build(con, apply=True, quiet=True)`，再用 `dim_supplier_alias` 把完整原文换成规范名，然后 `split_supplier()` 拆中英文写入 `Supplier_cn/en`；**原文保留在新增的 `Supplier_raw` 列**（dash_po 37 列）。
- **归一键扩展（关键）**：原规则只做「去空白/去标点/casefold」，只能合并格式变体；新增**中文主体名**归并——「中文全称-英文」取中文全称，分界点是「最后一个紧跟中文且其后无中文的 `-/_`」（取第一个会把 `中外运-敦豪…-Sinotrans-DHL…` 截成「中外运」）；「英文-中文」倒序取尾部 ≥4 字中文块（且其前无其他中文）。中国公司全称工商唯一，按它合并安全。规范名 = `格式分×10 + √次数`（`EntServ Malaysia Sdn. Bhd.` 胜出连写版）。
- **纯英文供应商修复**：`split_supplier` 判为纯英文时 `Supplier_cn` 原为 NULL，导致 KPI/TopN/明细漏掉它们（976 行）；现在 `if not sup_cn and sup_en: sup_cn = sup_en`。
- **结果**：8,330 原文 → 3,344 键（3,212 组，映射表 8,198 行）；FY2627 供应商 **1,081 → 1,041**；dash_po 全表 5,099 → 3,271；46,013 行 / ¥1,632,226,016.6 守恒。浦诺菲合并后 356 行 ¥19.18M 升至第一。
- **连带修复**：`supplier_name._distinct_names()` 必须加 `fct_po`/`fct_prpo` 的原始 `供应商名称`——归并后 dash_po 只剩规范名，连写变体消失，协议表英文名补空格会失效（`board_check` 的 Dongguan 断言失败）。测试断言改成「连写被还原」而非写死具体英文后缀（源数据升级会变 auto supplies → Car Accessories Supplies）。
- **回归**：`board_check` 294/0、`detail_table_check` 21/0、`page_tabs_check` 20/0、`ui_focus_check` 38/0、`snapshot_check` 62/0。

## 29 视觉对象头部：钻取 / 筛选浮层 / 突出显示（11-24）
- **用户要求**（参考 Power BI）：①返回按钮改上下钻取箭头组（上钻、转至下一级、展开所有下一级）；②显示「影响此视觉对象的筛选器与切片器」；③突出显示开关（点柱条是淡化其他还是仅留当前）。
- **图标组**（`templates/board.html` + `.v-ico`）：每卡 `card-tools` 末尾依次 `v-back`(上钻) / `v-down`(下一级) / `v-exp`(展开所有下一级) / `v-flt`(漏斗) / `v-hl`(突出显示)。全部 22×22、无边框、hover 才亮底；**只在该图有此能力时显示**（`updateVisualTools()` 逐类判断），否则标题会被挤没。`.v-back` 类名保留（旧测试 `.v-back[data-clear=x]` 仍然可用，只是图标换成 ↑、tooltip 改成 `Drill up — clear X`）。月度图的 up 仍在 `.v-slot` 内，保证 `#mSw` 零位移。
- **层级**：①采购方式饼图 Method→Reason（`S.methodDrill`，v-down 仅在未下钻时出现）；②帕累托 Category→Sub-category（`S.subLevel='sub'`，数据取 `/data.pareto.subsMap` 前端合并重算累计，不新增接口；**先选类别才能下钻**，否则 toast 提示）；③类目明细 v-exp 一次把所有多子类别的 `S.catSub[k]=1`；展开完按钮 disabled；**展开态必须有对应的上钻**（11-24 补）：`S.catSub` 非空时该卡 v-back 出现，tooltip `Drill up — collapse N expanded sub-categories`，点一下 `S.catSub={}` 收全部并隐藏（单个 `+N` 手点展开同样算展开态），优先级高于「清 category 筛选」。v-back 的分支判断必须按卡片 `#chCat` / `#chPareto` 区分——两者都 `data-clear="category"`，否则会串台。退级规则：子类别层 → 回类别层（保留 category 筛选）；类别层 → 清 category + sub。
- **子类别层**：Top 14 + 一根 `Other · N`（`data-other=1`，点击不筛选），否则 26+ 根柱标签糊成一团。点子类别柱走新筛选 key `sub`（`FILTERS["sub"]="Sub-Category"`，`_build_options` 里和 supplier 一样 skip，省一次全表 GROUP BY），chip 用 `EXTRA_CHIP_LABEL.sub` 显示。移除 sub chip / 上钻 / 重置都要把 `S.subLevel` 复位，否则留子类别层会把全部类别的子类别摊出来。
- **筛选浮层** `#vtPop`（`position:fixed`，挂在 body）：三段 `Slicers`(财年/币种) / `Filters`(S.sel 全部维度，本图自己造成的标 `.mine` 浅蓝底) / `This visual`(top 的 category/method、contracts 的 type/status/日期/搜索、catdetail 搜索与展开数、下钻层级)。按钮放在触发按钮下方右对齐；点外部 / Esc / 滚动关闭。
- **突出显示**：`S.hlOn`（localStorage `board-hl`，默认开）+ `S.hl={src,key,vals,base}`。点图元时 `beginHl()` 抓「点击前那份 `/data`」存 `base`，渲染时 `hlBase()` 用 base、`hlDim()` 给未命中项加 `.dv`（CSS `opacity:.22`，SVG 也生效），柱子/扇区/表格行位置完全不动 —— 这就是 PBI 的交叉筛选高亮。filter 模式（关掉）不存 base，本图直接用筛选后数据，只剩当前项。`loadAll()` 用 `keepHl = S.hlFresh` 判断：不是图内点击造成的刷新就清高亮，避免筛选条改完还留着上一张图的淡化态。切换月度图图示（region/pair）也 `clearHl()`。
- **测试**：新增 `tests/visual_tools_check.js` **28/0**（图标按能力出现、高亮淡而不隐、浮层三段内容与 mine 标记、帕累托下钻↔上钻、`Other` 合并、expand all、开关两种模式）。`board_check` 改「点类别行后只剩 1 行」为「11 行 + 10 个 `.dv`」294/0；`ui_focus_check` 在返回段先关突出显示（否则满环断言不成立）再恢复 38/0；`snapshot_check` 62/0（离线同样支持三者，`FILTER_KEYS` 加 `sub`）。标题 "Spend by Procurement Method"→"Spend by Method" 避免 g3 卡片被图标挤断。

## 30 环形图图例：下钻后不溢出卡片（11-24）
- **现象**：点 SS-Exempt / SS-Non Exempt 下钻后，方式饼图图例从 6 项变 8 项（理由名更长），固定高 76px 的 `.g3 .legend-list` 装不下 → 溢出卡片外 / 最后一行被切一半。
- **修法**：`.g3 .legend-list` 改 `height:80px` + `display:grid; grid-template-columns:1fr 1fr; gap:4px 10px`（8 项 = 4 行正好放满），保留 `overflow-y:auto` 兜底（项再多就框内滚动，不溢出）。`.li` 加 `min-width:0; overflow:hidden`，名称包 `<span class="lb">` 做 ellipsis，`title` 里带全名 + 金额 + 占比。
- **紧凑模式**：`drawDonut` 里 `compact = rows.length > 6` → 下钻层图例只留名称（金额与占比在扇区标签和悬浮提示里都有），expense 的 4 项仍显示「名称 + 金额 + 占比」，两列 2 行更整齐。
- **实测**：0 层 6 项 / 下钻 8 项 / 单源 7 项，`clientHeight === scrollHeight === 80`，`overflowsCard=false`，无横向滚动。
- **测试**：`board_check` 的「点类别行 → 淡化」等待条件补 `.cd-row.dv > 0`（chips 是同步渲染、重绘是异步，只等 chips 会读到旧 DOM）294/0 连跑两次稳定。

## 31 Detail 加 PRPO 说明列（11-24）
- **用户要求**：Detail 加 fct_prpo 的「说明」，默认一行 + 省略号，位置在 Procurement Method 与 PRPO Amount 之间。
- **字段来源**：`fct_prpo.说明` 早已写进 `dash_po."Note"`（`dash_build` 的 SELECT 里就有），**无需重建**。46,011/46,013 行有值，最长 2,517 字符。
- **后端** `board_api.py`：`DETAIL_EXPR["note"]='p."Note"'`（进 SELECT / 排序 / LIKE），新增 `DETAIL_LONG={"note"}`；`_detail_extra` 的文本搜索条件从 `key in DETAIL_TEXT` 放宽成 `key in DETAIL_TEXT or key in DETAIL_LONG`（**只加 DETAIL_EXPR 不够，搜索会被静默忽略**）；响应里说明截到 500 字（表格只显示一行，title 看全文）。`columns` 补 note。
- **前端**：`DT_COLS` 8 列，宽度重新分配 8/12/12/18/13/22/8/7；`{k:'note', t:'Note', long:1}` 渲染成 `<td class="dt-note" title="全文">`，CSS `max-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap`（表格本身 `table-layout:fixed`，否则 ellipsis 不生效）。区域行 `colspan` 4→5、公司行 3→4（列数变了，不改会错位）。
- **浮层**：长文本列只给搜索框（不拉取值列表——说明有 4 万多种）；`fetchDtValues` 与 input debounce 都加了 long / 空 key 保护，否则 Apply 后 220ms 的延迟请求会带 `col=null` 打 400。
- **离线**：`board_export.py` `ROW_COLS` 在 method 后插 note、`ROW_SQL` 加 `"Note"`、行内截前 60 字（字典编码对高唯一值列本来就会跳过，60 字可控体积）；`snapshot.js` `DETAIL_KEYS` 加 note、`detailResp` 的 field/out 同步。离线单文件 4.56MB → 9.07MB。
- **测试**：`detail_table_check` 8 列/合计列索引 6-7/新增 1c 段（第 6 列位置、ellipsis、title、只搜索无勾选、关键词现场探测 `0<n<total`）**27/0** 连跑两次；`snapshot_check` 加 5b 离线段 **64/0**；board 294/0、ui_focus 38/0、page_tabs 20/0、visual_tools 31/0。

## 32 Detail 列宽按实测内容重排 + 单元格悬停浮窗（11-24）
- **为什么要量**：旧宽 8/12/12/18/13/22/8/7 是拍的。用 puppeteer 在 1600/1920 两档量「表头需要多宽、分组行需要多宽、明细行截断几条」才发现：Region 需 85px 实给 143、Company 需 133 实给 215、Category 需 154 实给 215、Method 需 168 实给 233，合计浪费 ~235px；而 `PRPO Amount (RMB)` 表头需 162px 只给 143（表头被省略号截 19px）、Supplier 46/340 行、Note 258/340 行截断。
- **新宽（%）**：Region 6.5 / Company 9.8 / Category 10.8 / Supplier 22.5 / Method 11.2 / Note 18.7 / PRPO Amount 11.5 / PO Paid 9 = 100。原则：短列只留「表头 + 漏斗 + 一个分组行（含计数徽标）」的宽度，余量全给 Supplier / Note；**金额列必须容得下表头带币种的文字**，否则看起来像数据缺失。1600 视口（表宽 1471）：96/144/159/331/165/275/169/132，8 个表头 0 截断、短列明细 0 截断。
- **浮窗**：`#dtTip`（`.dt-tip`，body 级 `position:fixed`，`max-width:440px; max-height:300px`，`white-space:pre-wrap` 保留说明里的换行，`pointer-events:none`）。文本列统一改成 `data-tip="原值"`，**不再写 `title`**（否则浏览器原生 tooltip 和浮窗同时弹）；`mouseover` 委托在 `#dtTbl`，80ms 延迟，同一格不重复定位；`.dt-note` 恒显示、其余列只在 `scrollWidth > clientWidth` 时显示；定位贴单元格下沿，越界翻到上方并夹在视口内；`mouseleave`、任意滚动（捕获相）、`renderDetail` 开头都收起。
- **说明不再截 500**：`board_api.detail` 上限提到 4000（库里最长 2367 字，>500 的只有 25 行，多约 30KB），浮窗才给得出整段。离线 `board_export.NOTE_LIMIT` 60 → 3000（全量说明），单文件 9.07 → 12.25MB，属于用户可接受的代价。
- **测试**：`detail_table_check` 把 title 断言换成 `data-tip`，新增「悬停弹浮窗（fixed + 落在视口内）」「浮窗文本 = data-tip」「移出收起」三条 **30/0**；`snapshot_check` 5b 段改断言 `data-tip` + 离线文件里有 `#dtTip` **64/0**；board 294/0、page_tabs 20/0、visual_tools 31/0。

## 33 七项体验修正（11-24 下午）
- **刷新留在原页面**：页签写进 URL hash（`#detail` / `#framework`），`pageFromHash()` 在 init 的 `Promise.all` **之后**才切页——早切会让 Overview 在 `hidden` 容器里量到 0 宽、图表全空。`goPage(key, fromHash)`，`hashchange` 时不再回写 hash（否则死循环）；`history.replaceState` 失败（老浏览器）回落 `location.hash`。
- **Sub-Category 走聚合**：`dim_agg_采购子类别` 244 个原始值 → 144 个聚合名。`/data` 的 subs SQL 改成 `LEFT JOIN dim_agg_采购子类别 sm ON sm."原始值" = p."Sub-Category"`（原始值唯一、无空值）；筛选必须同步，`FILTER_EXPR = {"sub": SUB_EXPR}`，`_filters()` 里 `sub` 用 `COALESCE((SELECT 采购子类别 …), p."Sub-Category")` 而不是裸列名——否则下钻点选聚合名一条都选不中（实测：`sub=fixed assets-…` 得 ¥14,066,040 / 40 单，与 subsMap 该项一致）。**管理台改映射即时生效**（`/data` 缓存按 `_db_stamp()` 失效）。离线在 `board_export.build_rows` 用同一张表做字典替换。
- **视觉对象标题换色**：`--title:#2E5177`（低饱和钢蓝），`.card-h h2` 用它；正文仍是 `--ink`。
- **卡片半透明**（用户问「是否有效果」）：`--card-a:rgba(255,255,255,.80)`、描边 `rgba(30,47,75,.10)`。实测卡面 **#FAFBFB**（纯白 #FFF、画布 #E7E9ED、KPI/筛选条 #F8F9FB）——只比纯白暗 5/255≈2%，**肉眼几乎看不出**；真要让透明可感知，得把画布压深（`--bg`）或把 alpha 降到 ~0.6，两者都会连带影响表格与图表底。弹层 / 筛选条继续用实色 `--card`，避免半透明叠在图表上。
- **PR Count → Orders**：KPI `label`、Category Details 列头、子类别明细「n orders · x%」、抽屉列名、各处 tooltip、页头副标题全部改英文 Orders/orders。副标题变长 2 字符后 1280 下会换行把顶栏从 68 顶到 74 → `.hd-sub` 加 `nowrap + ellipsis`（顶栏高度是硬约束）。
- **月度图例英文**：后端 series 加 `l`（`REGION_EN`：HO / South / West / Super Luxury / Used Cars / Multi-brand），`k` 仍是中文（筛选、下钻都用它）；离线 `snapshot.js` 用注入的 `regionEn`。**不能用 `Region_en` 列**——「二手车 / 多品牌」都写成 Others 会撞名。
- **Category Details 与热力卡等高**：`.grid` 是 `align-items:start`，原来两卡各自高度（494 vs 555）→ 下沿横线差 61px。改 `#catCard{align-self:stretch}`（**只拉左卡**，热力卡拉高会空一大片）+ `.cd-wrap{flex:1 1 auto; min-height:0}` + `fitCatTable()`：内容比卡片矮时给 `table` 写死可用高度，行高被摊开、合计行贴着卡片底；内容更高（展开子类别）就不设高度。`ResizeObserver(#catCard)` + 渲染后双 rAF 重铺。
- **Detail 列宽再收**：Region 7 / Company 9.4 / Category 9.8 / Supplier 22.5 / Method 11.2 / Note 19.9 / Amount 11.2 / Paid 9。1600 视口（表宽 1471）→ 103/138/144/331/165/293/165/132：表头 0 截断、Region 与 Category 明细 0 截断，Company 只剩 2 个超长公司名的分组行截断。分组行 `<td>` 也带 `data-tip`（悬停给全名），但鼠标落在 `.dt-toggle` 上时不弹浮窗（按钮自带 title）。
- **测试**：board 294/0（副标题断言改 `orders`）、detail 30/0、snapshot 64/0（临时 `--out`）、page_tabs 20/0、visual_tools 31/0、ui_focus 38/0、perf 11/0。

## 34 月份缩写 / 刷新保持层级 / 明细加理由列（11-24 傍晚）
- **X 轴 3 字母月份**：`monthLabels()` 原来优先 `labelsLong`，12 个月在 690px 里互相压字（AuguSemptOcto…）；改成优先 `d.labels`（`_MONTH` 本就是 Jan/Feb…）。`renderMonthlyRegion` / `renderMonthlyPair` 共用这个函数；tooltip 仍直接取 `labelsLong` 给全称。测试 `board_check` 里「月份改用全称」断言改成 3 字母正则。
- **刷新保持分级展示**：`sessionStorage['board-view-v1']` 存 `{fy,cur,page,sel,methodDrill,subLevel,catSub,mMode,expR,expC}`；`init()` 在 `Promise.all` **之前** `restoreView(readView())`（筛选本来就要进首个 `/data` 的 query，晚还原等于白跑一次全量），`loadOptions` 里校验财年是否还在列表并 `sel.value=S.fy`；`syncControls()` 点亮币种/月度分段按钮。落盘点：`loadAll()`、`goPage()`、`renderCatDetails()`、`mSw`、Detail 折叠、`onMethodPick`、`v-down`，再加 **`pagehide`/`beforeunload` 兜底**——饼图下钻、类目展开这些不经过 `loadAll`，第一版只靠显式调用时刷新后下钻层会丢。高亮 `S.hl` 不存（依赖点击前那份数据）。实测同时勾选 Region+Category、下钻方式/帕累托、展开类目、切 RM、Detail 展开区域后 F5：页签、chips、币种、两处下钻标题、类目清单、树展开态全部还原，0 JS 报错。
- **明细第 6 列 `Single Source Reason`**：两字段 `豁免单一来源的理由` / `单一来源采购理由` 同时非空 **0 条**（FY2627 e_only 2,616 / s_only 210），可以合并。`DETAIL_BASE` 加 `LEFT JOIN fct_prpo f ON f.表单编号=p.No.`（主键唯一 46,013/46,013，不放大行数）+ 两张 `dim_agg_* ON 原始值`；`DETAIL_EXPR['reason']` = `COALESCE(mx.聚合, f.原文, ms.聚合, f.原文)`，映射表没覆盖的原始值回落原文。加进 `DETAIL_TEXT` → 自动获得排序、LIKE 搜索、表头取值勾选（16 个聚合名）；**改了 `DETAIL_BASE` 会让 `/detail`、`/detail/values`、合计/分组小计 4 类 SQL 都带上 JOIN**，实测冷 0.165s / 缓存 0.044s、响应 3.90MB，金额守恒 ¥221,626,955。离线 `board_export.ROW_SQL` 同款 LEFT JOIN + 新增 `reason` 列，`snapshot.js` 的 `DETAIL_KEYS` 加 `reason`，单文件 12.25→12.34MB。
- **9 列列宽**：表头 9 个文字本征宽合计 + 漏斗/箭头/内边距 = 1471px，几乎无余量。收紧 `thead th` padding → `0 2px 0 8px`、`.dt-th gap` → 2、`.dt-arrow` → 8px，最终 % 6.25/8.43/8.84/18.7/10.94/11.08/16.59/10.81/8.36（1600 视口 92/124/130/275/161/163/244/159/123），**9 个表头 0 截断**（长表头各留 3-6px，避免字体回退时又出省略号）。分组行 colspan 5→6、4→5；`tfoot`/测试里所有硬编码列号 6/7 → 7/8。
- **测试**：board 294/0（月份断言改缩写）、detail 30/0（`nth-child(7)`→8、9 列断言）、snapshot 64/0（临时 `--out` 验 12.34MB 单文件）、page_tabs 20/0、visual_tools 31/0、ui_focus 38/0、perf 11/0。

## 35 刷新闪回 Overview 修复（11-25 上午）
- **症状**：刷新 `#detail` / `#framework` 时先闪一下 Overview，逐帧采样窗口约 87ms（46→133ms）。
- **根因**：`init()` 只 `restoreView()` 恢复筛选 / 下钻 / 展开态，不恢复 `page`；`renderTabs()` 先画 Overview，`goPage(hash)` 被放在 `Promise.all` 之后。
- **修复**：`init` 在 `bind` 后按 `pageFromHash()` 早切页（改 `S.page` + 调 `enter` + 隐藏 Overview）；`<head>` 内联脚本按 hash 给 `<html>` 加 `boot-other`，CSS 隐藏 `#pgOverview` / `#kpis` / `.fbar`，3s 超时兜底，早切完成后移除；`loadDetail` 加 `pending` 去重；概览渲染封装 `renderOverviewVisuals()`，隐藏态渲染置 `S.overviewHiddenRender`，`PAGES.overview.enter` 双 rAF 后重画（隐藏时 `chartW()` 只能拿 fallback 737→实际 737 验证一致）。
- **测试**：`page_tabs_check` 加 4 条逐帧断言（刷新全程无 Overview + 最终停在目标页），24/24。

## 36 第四页 Geo Spend：地图 + Category 同比（11-25 下午）
- **参考图**：用户给的 Power BI 截图 = Protomaps/OpenStreetMap 深色气泡地图 + Zebra BI 式 Category Amount YoY Variance（灰条当前值 / ΔPY 正负条 / ΔPY% 点线）。**底图合规红线**：截图里的 OSM 海外瓦片不能直接用，白名单只有腾讯 / 高德 / 百度 / 天地图，本页默认腾讯地图 GL JS。
- **Key 不走前端硬编码**：`app.py` 的 `/dash` 从环境变量 `TMAP_KEY` 注入 `window.TMAP_KEY`，模板 `<head>` 用 `tojson` 输出；未配置时地图区显示配置提示，公司列表与同比表照常可用。用户申请腾讯位置服务个人 Key 后设 `TMAP_KEY` 并重启即可。前端动态加载 `https://map.qq.com/api/gljs?v=1.exp&key=…`（非默认场景、用户自备 Key），建议在开放平台配 Referer 白名单。坐标是 GCJ-02，腾讯 SDK 直接吃。
- **四个口径**（都在 `board_api.GEO_METRICS`）：`prpo` = `SUM(dash_po."Total Price")` ¥221.6M；`paid` = `SUM(PO Paid)` ¥84.6M；`micro` = `Procurement Method='Micro'` ¥5.7M；`inadequate` = `fct_prpo."单一来源采购理由"='Inadequate Competition'` ¥36.7M（**走 `p."No." IN (SELECT 表单编号 …)` 子查询，不 JOIN，避免放大行数**）。FY2627 当前 vs FY2526 上期，FY2627 进行中→整体降幅偏大，副标题与 spec 都注明。
- **接口**：`GET /api/board/geo?metric=&fy=&cur=&<全局筛选>`，纳入 `_JSON_CACHE_PATHS`。返回 `total/categories/companies/bubbles`，气泡按城市合并（坐标与颜色取该城金额最大公司），底图 `TMap.MultiCircle`，半径 `28000 + sqrt(v/max)*300000` 米，颜色取 `fct_company_location.Color`，点击气泡切换该城公司的筛选。61 家公司在坐标表 100% 匹配、全部在中国境内。
- **前端**：`PAGES.geo` 第四页；`S.geo={metric,data,selected,q,...}`；`sessionStorage` 多存 `geoMetric/geoSelected/geoQ`；`bindGeo()` 处理下拉 / 搜索 / 复选 / resize；Zebra 表 `.zb-bar` 灰条 = 当前值，`.zb-split` 中心线 + `.zb-pos/.zb-neg` 正负条，`geoSigned()` 把负号放在币种前面（`-¥56.1 M`）。`hash=#geo` 也进首帧防闪类。
- **顶栏**：第四页签后 1280 下顶栏溢出 34px；`.pg-tab` 从 `clamp(132px,9vw,176px)` 收到 `clamp(120px,8.6vw,176px)`、≤1280 从 112px 收到 92px，1440/1280 都回到 68px 且 0 溢出（四个页签仍不截断）。
- **离线单文件**：`board_export.build_geo_loc()` 把公司→坐标/区域色/城市写进 `__SNAP__.geoLoc`，`snapshot.js` 新增 `geoResp()` 按行复算（用 `r.category` 不是 `r.cat`，第一版踩过只出 1 行的坑），`fetch` 垫片加 `path==='geo'` 分支。单文件 12.37MB。
- **测试**：新增 `tests/geo_check.js` 在线 / 离线各 **22/0**；board 294/0（spec 按钮 22→23、覆盖 9→10 个对象）、page_tabs 24/0、detail 30/0、snapshot 64/0、visual_tools 31/0、ui_focus 38/0、perf 11/0、grid_sum 14/0、colfilter 34/0。
- **已知无关失败**：`tf_click_check` 3 项 / `tf_qb_check` 2 项仍沿用旧硬编码 44,987 行与「金额列实存文本」假设（现库 46,013 行且金额已物化为数值），与 Geo 页改动无关，未在本轮修改。

## 37 Geo 页按参考图 1:1 调整（11-25 傍晚）
- **删 KPI 卡**：模板去掉 `#geoKpis`，`renderGeo()` 不再写四张卡；地图区从 `flex:0 0 46%` 提到 52%（高度归给地图）。数字口径仍由表格与公司列表承载。
- **气泡缩小**：`MultiCircle` 半径从 `28000 + sqrt(v/max)*300000` 米改成 `15000 + sqrt(v/max)*110000` 米，实测城市气泡不再互相盖住。
- **Zebra BI 1:1**：参考图是深色矩阵（截图 Protomaps 那张的下半部），所以表格改深色 `#0D1B2A` + 白字，外套 `.geo-tbl-wrap` 滚动。三列视觉编码：
  - 金额 = 灰条从左起 `#8897A8`，数值贴条尾（`geoNum()` 输出 `46.8 M`，**不带币种符号**）；
  - ΔPY = `.z-br::after` 中心 1px 零线，`.z-side.pos` 从 50% 向右绿条、`.z-side.neg` 从 50% 向左红条，数值贴条尾；
  - ΔPY% = 1px 细线 + `::after` 7px 实心圆点（正绿右端 / 负红左端）+ 线尾百分比。
  - 条宽相对半列，最多 85%（金额 82%），留空间给数值；数值格式 `+30.7` / `-35.0` / `-35.7%`。
  - 类别列右对齐贴灰条，列间 1px 竖线，行间细横线——与截图一致。
- **测试**：`geo_check` 扩到 25/0（新增 KPI 已删、深色底、金额灰条、细线圆点、中心零线断言），在线 + 离线各 25/0；board 294、page_tabs 24、detail 30、snapshot 64、visual_tools 31、ui_focus 38、perf 11、grid_sum 14、colfilter 34 全绿。
- **离线 Key**：`board_export.py` 从进程环境读 `TMAP_KEY` 注入单文件；导出时没设该变量，离线文件地图区就显示配置提示（表格照常用），这符合预期。

## 38 Geo 页三栏布局 + 亮色 Zebra（11-25 晚，替换 §37 的深色方案）
- 用户澄清：不是照搬黑底，而是借鉴 Zebra 的**色块 / 坐标轴 / 条图样式与颜色 / ΔPY% 圆点线**，整体要契合看板亮色主题。
- **三栏横向铺满**：模板把原来的「上地图+公司、下同比表」改成单个 `.geo-body`，`flex:1 1 auto` 撑满卡片余高；三栏 `地图 48% / 同比表 37% / 公司筛选 15%`（≤1280 为 46/39/15）。地图最宽、公司最窄，三栏上下沿齐平，下面不留空带。
- **亮色 Zebra**：`.geo-tbl-wrap` 白底 + `--line` 描边，表头回到 `--hdr-bg:#8CAACD` + 深字；灰条 `#A0B0C4`，中心零线 `#B4C0CF`，列间 `1px --line`，类别列右对齐深藏青。红绿与看板令牌一致：条 `--up:#3ECA74` / `--red:#EA1313`，文字 `#17834A` / `#C62828`，ΔPY% 细线圆点用稍深的 `#2CA967` / `#D93A3A`（白底上更清晰）。金额仍无币种符号、数值贴条尾；ΔPY 中心零线左右伸缩；ΔPY% 1px 线 + 7px 圆点。
- **测试**：`geo_check` 扩到 28/0（新增三栏并排、铺满卡片、占比顺序、亮色主题断言），在线 + 离线各 28/0；board 294、page_tabs 24、detail 30、snapshot 64、visual_tools 31、ui_focus 38、perf 11（首屏 URL 偶发 1 次未捕获，复跑全绿）、grid_sum 14、colfilter 34 全绿。

## 39 Geo 页公司级气泡 / 华南默认视角 / 布局溢出修复（11-25 夜）
- **白名单澄清**：腾讯位置服务 JS API GL 的 Key 编辑弹窗里**没有域名白名单字段**（截图确认），域名白名单只属于 WebServiceAPI（浏览器 JSONP 场景）；看板只用 JS GL，无需配置。
- **公司级气泡**：`_GEO_LOC` 改成 `fct_company_location l LEFT JOIN dim_company_map m ON m.CompanyAbbreviation=l.Company_cn`，一家公司一行、坐标直接取表；后端不再按城市合并，`bubbles = 有经纬度的 companies`。深圳同城 13 家 → 13 个独立气泡。FY2627 有 58 家发生交易，其中 6 家是**上财年才有交易且坐标表未收录**的历史公司（v=0、lat/lng 为空），不画气泡但保留在筛选列表。
- **英文简称**：`dim_company_map.CompanyAbbreviationEN` 63/63 全覆盖，气泡与筛选列表都带 `abbr`，列表显示简称、title 给中文全称，搜索同时匹配两者。
- **缩放联动半径**：`MultiCircle.radius` 是米制，天然随缩放变化；再在 `zoom` 事件里按 `scale=2^((zoom-5.6)*0.5)` 重画一次，基准 zoom 5.6 下 3~28px。半径从 15000+sqrt*110000 改为 `(4000+sqrt*38000)*scale`，避免聚焦华南后气泡过大。
- **默认视角**：中心 `[113.5, 22.9]`（珠三角），zoom 5.6，不再一上来铺全中国；`#geoMap.dataset.zoom/center` 记录初始值便于回归断言。
- **布局修复**：三栏原用 `flex:0 0 48%/37%/15%`，百分比合计 100% **再加 2×12px gap** 导致公司筛选栏右边界溢出画布 23px；改成 `flex:48 1 0 / 37 1 0 / 15 1 0`（≤1280 为 46/39/15），gap 自动从剩余空间扣。`.geo-comp-pane` 加 `overflow:hidden`、列表用 grid `13px minmax(0,1fr) auto` 防溢出；`.geo-tbl-wrap` 改 `overflow-x:hidden; overflow-y:auto`，横向滚动条消失。`fitGeoTbl()` 按容器高度摊 11 行行高（上限 120px），三栏纵向齐平。
- **测试**：`geo_check` 在线 36/0、离线 35/0（离线无 Key 时跳过地图视角断言）；board 294、page_tabs 24、detail 30、snapshot 64、visual_tools 31、ui_focus 38、perf 11、grid_sum 14、colfilter 34 全绿。

## 40 Geo 页 Zebra 1:1 复刻与三栏重排（11-25 深夜，替换 §38/§39 的表格方案）
- **参考图逐像素核对**：把用户给的 Zebra 截图放大 4 倍分别看左右两段，确认视觉语言：表头 `Category | PY | AC ↓ | ΔPY | ΔPY%`；PY 右对齐纯数字；AC 左侧一条深色竖轴 + 深灰条（#4A4A4A）从左起、数值在条尾外侧；ΔPY 中心 2px 灰零线、正右绿条 #5FC228 / 负左红条 #FA2E2E、数值深色贴条尾；ΔPY% 同款零线 + 2px 细线 + 末端黑心圆点（外圈同色），接近 0 时只剩零线上的黑点；第 6 行前一条浅灰分组横线；无列竖线、无逐行边框、白底黑字。
- **数值格式**：`zbNum()` / `zbSigned()` 统一按百万单位输出 1 位小数纯数字（不写 M / 币种），表头副标题加 `unit M`。原先 PY 直接写 raw 元（102852590.6）、AC 带 M，已统一。
- **布局**：三栏顺序改为 **筛选 16% / 地图 31% / 同比 53%**（≤1280 为 17/31/52）；`.geo-body{height:400px; flex:0 0 auto}`、`#pgGeo{height:auto}`，不再竖向铺满屏幕。`fitGeoTbl()` 变空函数（行高固定 28px），resize 仍调用但无重算。
- **气泡反向缩放**：`MultiCircle.radius` 是米制，屏幕上直径 ∝ radius × 2^zoom；把 radius 系数改成 `2^(-1.5×(zoom-5.6))` 后净效果每放大一级缩到约 0.7 倍（原来放大反而变大）。基准半径同步缩到 `(3000+sqrt*26000)` 米；`dataset.sampleRadius` + `window.__geoMap` 供回归测试验证缩放方向。
- **测试**：`geo_check` 在线 39/0（新增 5 列表头、PY 格式、深灰条、黑心圆点、2px 零线、分组横线、反向缩放断言）、离线 37/0（无 Key 时跳过地图视角与缩放）；board 294、page_tabs 24、detail 30、snapshot 64、visual_tools 31、ui_focus 38、perf 11、grid_sum 14、colfilter 34 全绿。

## 41 气泡尺寸改为「屏幕像素恒定」（11-25 深夜，替换 §40 的 1.5 次方反比）
- **问题**：上一版 `radius ∝ 2^(-1.5×zoom)`，净屏幕尺寸每放大一级缩到 0.7，用户对比三张参考截图（3km / 50km / 500km 比例尺）后指出比例不对——参考图里气泡在各级缩放下屏幕大小基本一致（500km 视图最大约 48px，3km 街道视图仍 20~30px），并非迅速缩小。
- **改法**：不再用相对缩放系数，改为**先定屏幕像素半径再反算米制半径**：`pxR = (5 + sqrt(v/max)×15) × 0.92^(zoom-5.6)`，`mpp = 156543.03392×cos(lat)/2^zoom`，`radius = pxR × mpp`。即每放大一级只缩 8%，视觉上近似恒定，同时保留轻微收缩避免街道级盖图。
- **实测**：zoom 5.6 最小气泡半径 5.3px、zoom 10 为 3.7px（4.4 级共缩 31%）；地理半径同一气泡 13,390m → 439m，说明米制半径随缩放反向变化、屏幕尺寸稳定。截图对照：街道级仍能看到各公司独立气泡与底图路网。
- **测试**：`geo_check` 在线 40/0（新增「放大时屏幕尺寸近似恒定 0.7~0.95 倍」与「地理半径反向变化 >2 倍」两条），离线 37/0；board 294/0。

## 42 Geo 页：ΔPY% 细节、行点击联动、公司多选与栏宽（11-26 凌晨）
- **ΔPY% 细节对齐参考图**：圆点改为 9px 黑心 + 2.5px 同色外圈（原来 8px、2px）；接近 0（|Δ%|<0.05%）只保留零线上的点、外圈仍按正负取绿/红（原来纯黑、且会写 +0.0%）。数值与圆点之间留 4px、字体 10.5px；条宽上限收紧（ΔPY 42%、ΔPY% 34% 半栏），修掉 flex 溢出把红条推过零线、文字压住圆点的问题。
- **Zebra 行点击联动**：`tr.z-row[data-cat]` 点一下切 `S.sel.category`（可多选、再点取消），同步 `renderFilters + loadAll + loadGeo`；选中行 `.on` 高亮、其余 `.dim` 淡化到 0.42。为避免自己把自己筛掉无法取消，后端新增 `categoriesAll`（忽略 category 筛选，仍吃区域/公司等其它筛选），表格在选中态改画这份全表。
- **公司多选修复**：`/api/board/geo` 的 `companies` 改为忽略 `company` 自身筛选（其它筛选照吃），勾一家后列表仍是 58 家可继续勾；离线 `snapshot.js` 同步用 `fCo/fCat` 两份筛选副本复算。另外前端缓存 `S.geo.baseCompanies`：其它筛选把已勾选公司暂时筛掉时，仍以斜体 + `—` 保留在列表里，保证能取消。
- **栏宽**：筛选 15% / 地图 37% / Zebra 48%（≤1280 为 16/38/46），地图比上一版 +65px、Zebra −90px；表头列宽 19/10/29/21/21，类别列不再截断。
- **测试**：`geo_check` 在线 45/0（新增公司多选、行点击高亮/淡化/chip、再次点取消）、离线 42/0；board 294、page_tabs 24、detail 30、snapshot 64、visual_tools 31、ui_focus 38、perf 11、grid_sum 14、colfilter 34 全绿。

## 43 首页 KPI 增减样式 / 页签收紧 / Detail 日期切片器 / Top10 放大修复（11-26 上午）
- **Top10 偶发放大**：概览隐藏时 `loadAll/loadTop` 仍会渲染，`chartW('chTop',480)` 拿不到宽度只能落 480；而 CSS `.chart svg{width:100%}` 会把 viewBox=480 的图拉满卡片 → 字和图一起变大。原先 `renderOverviewVisuals()` 漏了 `renderTop`，所以切回概览只有它不重画。现在 `renderOverviewVisuals` 里补 `if (S.data.top.length) renderTop()`，配合原有双 rAF，切回即按真实宽度重画（实测 SVG width 610 = 容器 610）。
- **页签收紧**：高度 50→40px、宽度 `clamp(104px,7.4vw,150px)`、字号 14→12.5（≤1280 宽度 86、字号 11.5）；`.hd` 加 `min-height:68px` 保证顶栏不被压矮，`.pg-tabs` 偏移 `top:14px` 让下沿仍齐顶栏底边。
- **KPI 增减标按参考图**：右侧两行 `ΔPY`（绝对差额）/ `Δ%`（同比百分比），灰色小标签 + 彩色数值 + 12px 圆形箭头（▲/▼）；颜色按国内习惯**增加红 #D8341F、减少绿 #78BE3C**，圆标与数值同色。替换原来的两枚胶囊 `.k-pill`。
- **Detail 日期范围切片器**：卡头新增 `#dtFrom/#dtTo`（`type=date`，min/max 取 `/data` 的 range）。后端 `_scope()` 统一认 `dateFrom/dateTo`（`p."Date" >=/<= ?`，ISO 闭区间），Detail 的行、区域/公司小计、表头取值面板全部生效；离线 `snapshot.js` 的 `detailResp` 也按 `r.date` 过滤。日期是 Detail 局部筛选，不影响 KPI/其它页；换财年自动清空；`Clear filters` 一并清空；`sessionStorage` 记 `dFrom/dTo`。
- **loadDetail 并发修复**：原来 `pending` 时直接丢弃新请求，连续改日期/筛选会丢掉最后一次。改为记 `queued`，当前请求回来后再补一次。
- **测试**：page_tabs 25/0（页签高度 40、新增切回概览 Top10 宽度断言）；board 297/0（KPI 两行 Δ、红/绿与圆标同色、箭头字符）；detail 33/0（日期切片器生效=C 同区间、Clear 同时清空）；geo 在线 45/0、离线 42/0；snapshot 64/0、visual 31/0、ui_focus 38/0、grid_sum 14/0、colfilter 34/0；perf 复跑 11/0（首次跑偶发只抓到 2 个请求，属冷启动抖动）。

## 44 KPI 箭头 / 抽屉浮动化 / 子类别可点 / 日期跨财年（11-26 中午）
- **KPI 增减标**：箭头改内联 SVG（带杆箭头，朝上/朝下），`viewBox 0 0 8 8` 在 12px 圆标里天然居中；颜色改成**增加绿 #1B8A4B、减少红 #C62828**（圆标同色），两行右对齐（`.k-bot{align-items:flex-end}`）；删掉卡内 `vs FY… · sub` 小字。
- **抽屉改浮动面板**：`.drawer` 从整页右侧 1080 宽改为 `right:20px; top:86px; 720×520（不超过视口）`、圆角 8 + 阴影，标题/副标题单行省略；表格删除 `Suppliers` 列（AG_COLS 7→6，rowsHtml 同步），列宽合计 680 适配 720。
- **Category Details 子类别可点**：`li.cs-item[data-subcat]` 点击按 `S.sel.sub` 交叉筛选（`beginHl('catdetail','sub',…)`、`setSel/renderFilters/loadAll`），选中行 `.on` 高亮；再点取消。chip 走既有 `EXTRA_CHIP_LABEL.sub = Sub-category`。
- **Detail 日期切片器跨财年**：`_scope()` 里只要带 `dateFrom/dateTo` 就不再追加 `c."FiscalYearName"=?`，日期按 ISO 闭区间；财年切换不再清空日期；换财年后 `dtMeta` 显示 `起 ~ 止` 而不是 FY；可选范围取 `/options.range`（全库范围，不受当前财年限制）。实测 2026-03-01~09-30 跨 FY2526/FY2627 返回 10,301 行，同区间限 FY2627 为 4,136 行。
- **测试**：board 301/0（KPI 绿升红降 + SVG 箭头、抽屉 6 列/浮动尺寸、子类别点击与取消）、detail 35/0（跨财年、换财年不清空）、page_tabs 25/0、geo 45/0+42/0、snapshot 64/0、visual 31/0、ui_focus 38/0、grid 14/0、colfilter 34/0、perf 11/0。

## 45 图表 1:1 像素（去缩放）/ KPI 描边箭头与卡高 / 参考图入库（11-26 上午）
- **锯齿感的真正根因：所有图表 SVG 都被缩放了 2~5%。** `chartW()` 取的是 `el.clientWidth`（**含左右 padding 各 8px**），所以 svg 的 `width` 属性比实际可绘制区宽 16px；CSS 又用 `.chart svg{width:100%}` 把它压回内容宽 → 每张图恒定缩放 0.955~0.981（实测 chDonut 341/357=0.9559、chTop 594/610=0.9739）。矢量图一旦被 CSS 缩放，细线与小字全部重采样，观感就是"分辨率低、有锯齿"。
  - 修法一：`chartW()` 扣掉 `paddingLeft/Right` 并 `Math.floor`，svg 宽度 = 内容宽整数；
  - 修法二：`.chart svg{ display:block; max-width:100%; height:auto }`，不再 `width:100%` 拉伸。
  - 实测修后 `attrWidth / renderedWidth = 1.0000`，`resize` 仍走 180ms 重绘，窗口变化不会留旧宽度。
- **KPI 箭头**：改成 12×12 视框的**描边箭头**（`stroke-width:1.3`、`stroke-linecap:round`、无填充），1 单位 = 1px 不缩放；关于 (6,6) 对称绘制。像素级实测圆标 48px（DPR4）时箭头 bbox 26×26、四边留白 **11/11/11/11**，完全居中；线宽约占直径 11%，比原来 6.8/8 的粗箭头细一半。
- **KPI 卡高**：`min-height` 96→74、padding `8/7`→`7/6`、`.k-mid` 上边距 5→4，卡高 96→**76**（含 2px 边框），下方空白（原 18~25px）归零；1920/1280 下 7 张卡无溢出、数值无截断、顶栏仍 68px。
- **离线导出不再内嵌地图 Key**：`board_export.py` 在拿到 `/dash` HTML 后把 `window.TMAP_KEY` 置空。理由：file:// 协议腾讯地图 SDK 明确不支持（控制台刷错误），且 Key 会随可转发文件泄漏。离线文件地图区显示配置提示，公司列表与 Zebra 表照常。
- **测试**：`board_check` 吸顶段改稳（滚到文档底部、断言"图表位移 == 滚动距离"而非写死 600px，KPI 变矮后原断言会误报）；`page_tabs_check` 的 Top10 宽度断言改为对比"容器内宽"并校验 `rendered == attr`。回归：board 301/0、page_tabs 25/0、detail 35/0、geo 在线 45/0、离线 42/0、snapshot 64/0、visual 31/0、ui_focus 38/0、grid_sum 14/0、colfilter 34/0、perf 11/0。
- **参考图入库**：`前端建设/microsoft官方 powerBI示例/` 新增 17 张视觉对照图（GEO/Zebra1~3/kpi/category/公司/卡片/工具提示/条图/气泡图/面积图/表格2~5，共 23MB）纳入版本库。

## 46 弧线锯齿定位 / 财年↔日期切片器双向联动（11-26 上午）
- **弧线毛边的结论（先记结论，避免以后重复排查）**：图表 SVG 现在三档 DPR（1 / 1.25 / 2）实测 `属性宽/渲染宽 = 1.0000`，没有任何缩放。剩下的锯齿是**1x 设备像素的物理极限**：Chrome 对弧边只做"1 个过渡像素"的线性混合（实测 `(129,145,166)` 单像素过渡），`shape-rendering` 三档 `auto` / `geometricPrecision` / `optimizeQuality` 渲染结果**逐像素一致**（做了 A/B 对照），所以换属性没有意义。真正能提高弧线细腻度的只有增加设备像素密度：浏览器缩放 125%~150%（Ctrl+滚轮）或系统缩放，SVG 会按更高分辨率重新栅格化。用户对比的 Power BI 参考图是 2x/3x 截屏，同尺寸比必然更细腻。
- **最后一点残余缩放也修掉了**：`chartW()` 原来用 `clientWidth`（**整数四舍五入**），容器真实内容宽 720.5 时它给 721，超出后被 `max-width:100%` 压回 → 0.07% 缩放。改为 `getBoundingClientRect().width` 的小数宽度再 `floor`。
- **财年 ↔ Detail 日期切片器双向联动**：
  - `GET /api/board/options` 新增 `fyRange`（每个财年数据库里实际的最小/最大 `Date`）。实测 FY2425 `2024-07-25~2025-06-30`、FY2526 `2025-07-01~2026-06-30`、FY2627 `2026-07-01~2026-11-22`。
  - 选财年 → 日期框自动填该财年区间，Detail 行数与该财年一致（19,686）；改日期 → 财年下拉置空（新增首项 `—`），此时 Detail 只用日期（可跨财年），全局 KPI/图表按"未指定 → 最新财年"回落。
  - `Clear filters` 清空日期后，若财年为空则回落到最新财年，避免"空财年 + 无日期"的吊空状态。
  - `sessionStorage` 允许存空财年（`restoreView` 由 `if (v.fy)` 改成 `typeof === 'string'`），刷新后仍停在日期模式。
- **首屏预热命中修复**：`/data`、`/top` 与 `/options` 是并行发出的，原来 `/top` 常常还没拿到财年就不带 `fy`，命中不了 `WARMUP_URLS` 的缓存（`perf_check` 会偶发失败）。现在 `/dash` 渲染时直接注入 `window.__FY_DEFAULT`（口径同 `_fy_list`：只算命中有数据的财年；**不能**用 `MAX(FiscalYearName)`，`dash_calendar` 覆盖到未来年度会给出 FY2829，直接空页）。
- **测试**：`detail_table_check` 扩到 37/0（新增"设日期后财年置空""选财年自动填区间""Clear filters 回最新"）；`perf_check` 增加"等 4 个资源条目到齐再统计"，连跑 3 次 11/0 稳定；board 301、page_tabs 25、geo 45/42、snapshot 64、visual 31、ui_focus 38、grid_sum 14、colfilter 34 全绿。

## 47 修「选了财年后日期切片器无法跨财年」（11-26 上午）
- **现象**：选 FY2526 后把日期往前/往后拖到本财年之外，输入框直接回弹，改不动。
- **根因**：`renderDetail()` 里 `rg = (S.options && S.options.range) || (S.data && S.data.range)`。但 `S.options` 存的是 `d.options`（筛选值列表：category/method/region…），**没有 range 字段**，于是永远回退到 `S.data.range`，也就是**当前财年**的日期范围。HTML `input[type=date]` 的 `min/max` 一旦卡在本财年，浏览器会静默拒绝越界输入。
- **修法**：`loadOptions()` 显式保存顶层的全库范围 `S.range = d.range`，日期框 `min/max` 改用它（实测 `2024-07-25 ~ 2026-11-22`）。
- **验证**：选 FY2526 后写入 `2025-03-01 ~ 2026-09-30`，值保留、财年置空、`/detail` 带 dateFrom/dateTo 返回 27,304 行。
- **测试**：`detail_table_check` 扩到 39/0，新增「日期框可选范围是全库」「跨财年区间可以写入（值不被 min/max 挡回）」两条。

## 48 筛选条重排：Export 移到 Reset 上方 + 切片器卡片化（11-26 上午）
- **操作组**：`#btnExport` 从顶栏 `.hd-r` 移进筛选条右侧，新增 `.fbar-acts` 竖向容器（`Export View` 在上、`Reset Filters` 在下，`align-items:stretch` 等宽右对齐）。顶栏只剩财年 + 币种，更清爽。注意：`.fbar` 在 Detail / Framework / Geo 页由 `showChrome(false)` 隐藏，所以导出按钮也随筛选条一起隐藏（与 Reset 一致）。
- **按钮配色**：`.btn` 原本是"白底白边 + 深蓝字"（为深色顶栏设计），放进白色筛选条后成了隐形按钮。`.fbar-acts .btn` 覆盖为实心海军蓝 + 白字，`.btn.ghost` 为白底描边，层级清晰。
- **切片器卡片化**：`.fd-btn` 由「小标签 + 13.5px 值 + 右下角 ▾ 字形」改为等宽卡片（圆角 8、`min-height:46`、名称 10.5px `#7A8698` 在上、取值 13px 800 在下），箭头改 CSS 折角（6px、垂直居中、展开时旋转 180 度）；`.fdrop.has` 用浅蓝底 + 品牌描边表示"已选"。
- **等宽靠 grid**：`.fbar-in` 从 `flex-wrap` 改为 `grid-template-columns:repeat(auto-fit,minmax(150px,1fr))`。原先 flex 在 1100px 下把第 6 个切片器拉到整行宽（857px），grid 换行后仍等宽。实测 1920/1440/1280 一行等宽 265/185/165，1100 两行各 163，`scrollWidth-clientWidth=0`。
- **说明按钮**：`.fdrop .spec-btn` 平时 `opacity:0`（不再在卡片右上角一直挂着一个小方块），悬停/展开才浮现为 13px 圆形问号；`.fpop` 面板同步改 8px 圆角 + 柔和阴影。
- **测试**：`page_tabs_check` 的"切页不移动顶栏控件"去掉 `#btnExport`（已不在顶栏）；`ui_focus_check` 的 chips 对齐对象改成 `.fbar-acts`；`board_check` 新增 3 条（按钮在筛选条内且不在顶栏、在 Reset 正上方且同宽右对齐、6 个切片器等宽），并在量测前先切回 Overview。回归：board 304/0、page_tabs 25、detail 39、geo 45/42、snapshot 64、visual 31、ui_focus 38、grid_sum 14、colfilter 34、perf 11 全绿。

## 49 Export 回到页面右上角 + 切片器紧凑化 + chips 移到切片器下方（11-26 中午）
- **推翻 §48 的一处**：`#btnExport` 从筛选条 `.fbar-acts` 移回顶栏，并钉在**整页右上角**。为此顶栏 DOM 顺序改成「`.hd-l` 标题 → `.pg-tabs` 页签 → `.hd-r` 控件（FY / 币种 / Export）」，`.pg-tabs{margin-left:14px}`、`.hd-r{margin-left:auto}`（**不要再让两者都 auto**，否则两段空白被均分、控件跑到中间）。`.hd-r .btn` 是白底藏青字实心按钮（深色顶栏上唯一的高对比 CTA）。
- **副作用**：`.fbar-acts` 已删除，`.fbar-r` 现在只放 `?` 与 `Reset Filters`（`min-width:104`）；Export 在顶栏，所以 Detail / Framework / Geo 页仍可见（`.fbar` 由 `showChrome(false)` 隐藏，按钮不再跟着消失）。
- **切片器紧凑化**：`.fd-btn` `min-height:46→32`、`padding:2px 22px 3px 9px`（原 5/30/6/11）、圆角 8→7；`.fd-name` 10.5→9.5px、`.fd-val` 13→12.5px、行高 1.25/1.28；`.fdrop .fd-caret` right 12→10、5px；`.fbar` padding `6px 11px 7px`、行间距 5px；`.fbar-in` `minmax(150px,1fr)→minmax(132px,1fr)`、gap 10→8。整条筛选区高度 72→50（1600 宽实测），仍然一行等宽。
- **chips 改到切片器下方整行**（用户 11-26 要求）：`.fbar` 改 `flex-direction:column`，新增 `.fbar-top`（切片器 + `.fbar-r`）；`#chips` 是 `.fbar` 的第二个子元素，`display:flex; flex-wrap:wrap; width:100%`（**取消**原来的 `max-width:min(38vw,380px)` + 横向滚动）。**注意这是一次有意的约定变更**：原「钻取零位移」靠 chips 挤在同一行实现，现在有筛选时筛选条会增高 chips 的高度（实测 50→74，+24px），图表整体下移同量（已核对各图 top 位移差 ≤1px，无重叠）。`ui_focus_check` 4a 的断言已按新语义重写（不再要求 fbarH 不变，改为「增量 = 各图表下移量」「chips 在切片器下方」「通栏」「移除后回原高」）。
- **窄屏分级**（新增两个断点，避免页签压住 FY 控件）：`@media (max-width:1180px)` 页签 88px + 字号 11.5、隐掉 `Fiscal Year` 文字标签、`.hd-r` gap 6、h1 16px；`@media (max-width:1080px)` 直接 `display:none` 掉 `.hd-sub`（副标题最占宽），h1 15px。`.pg-tab` 改 `flex:0 1 auto; min-width:0`，容器再窄也不会把页签顶到控件上。实测 1024 / 1280 / 1440 / 1600 顶栏均 68px 且无溢出。
- **测试**：`board_check` §10 改断言「Export 在顶栏、顶栏内最靠右元素、垂直居中」；`page_tabs_check` §4 由「页签贴最右、控件在中间」改为「页签紧跟标题（不贴右端）+ Export 贴页面右上角」；`ui_focus_check` 4a 重写为 chips 在切片器下方 + 1px 取整容差 + 排除隐藏页的 `#fcCard`。回归：board 304/0、page_tabs 27、ui_focus 40、grid_sum 14、colfilter 34、snapshot 64 全绿。

## 50 表头筛选强化 / Framework 两个下拉搬进表头 / chips 恒定预留一行（11-26 中午）
- **Detail 列头筛选入口**：原来是 `▾` 字形（11px、无边框），落在浅蓝表头上几乎看不见。改成 **18×18 描边小方块 + 11px 矢量漏斗 SVG**（`stroke-width:2.4`），`rgba(255,255,255,.60)` 底、`rgba(18,39,71,.22)` 边框，hover 变纯白 + 加深边框，`.on` 深藏青填充白图标。`.dt-th` 的 gap 2→4。仍是 `<button class="dt-funnel" data-col>`，点击逻辑与 `.dt-pop` 没动。
- **Framework 的 Type / Status 搬进表头**（原来挤在 `.card-tools` 里，和日期/搜索/Reset 抢位置）：两列 `FC_COLS` 加 `hf` 标记（`remain → status`、`type → type`），表头渲染成「列名 + `.fc-hflt` 漏斗」。两个 `<select id=fcType|fcStatus>` **保留在模板的 `#fcHdrPool[hidden]` 里**——它们是唯一取值来源，`fillContractMeta()`、`page.select()` 测试、`S.fc` 赋值都还走它们。点漏斗弹 `.fc-pop`（fixed、196px、右端贴边时改为右边对齐按钮），选项直接由 `select.options` 生成；点选项 = 设 `select.value` + `dispatchEvent('change')`，所以口径只有一处。选中值非空 → `<th class="has">`，漏斗反色高亮；`#fcReset` / 下拉 change 都调 `markFcHdrSel()` 同步。浮层在表格滚动 / 窗口 resize / 点外部时关闭，点漏斗自身不触发排序。**注意**：`th` 内的漏斗用 `position:absolute; right:5px`，`th.fc-hth{padding-right:28px}` 留位，避免长列名（Remain Days）压到图标。
- **列宽微调**：Type 4%→7%（要放列名+漏斗），Contract 23%→21%、Supplier 16%→15% 补回来，其余不动。
- **chips 恒定预留一行**（用户要求"点图时页面不动"）：`.chips` 去掉了 `:empty{display:none}`，改 `min-height:20px` + `flex-wrap:nowrap` + 横向滚动（隐藏滚动条）。实测有/无筛选、点图前后 `fbar` 恒 75px、`sticky-head` 恒 325px、`#chMonthly` 恒 y=400，即**零位移**；超过一行宽度的 chip 走横向滚动，高度永不变化。筛选条比 §49 的无筛选态高 25px（这就是预留行的代价）。
- **测试**：`ui_focus_check` 4a 又改回零位移语义（fbarH/stickyH/各图 top 全等 + chips 行恒占位 + chips 通栏）；`board_check` 新增 6 条（下拉已移出工具条、表头 2 枚漏斗且是 SVG、浮层选项与下拉一致、筛 CP 等效、列头高亮、Reset 取消高亮）；`detail_table_check` 新增 1 条（9 个漏斗都是 18×18 且内部是 SVG、无文字）。回归：board 311/0、ui_focus 40/0（4a 10 条）、detail 40/0、page_tabs 27、visual 31、snapshot 64 全绿。

## 51 Framework 的搜索框也进表头（11-26 中午）
- **Contract / Supplier 两个搜索框**从 `.card-tools` 撤掉，改成列头漏斗：`FC_COLS` 里加 `hf: 'contract' | 'supplier'`，点漏斗弹 `.fc-pop.wide`（240px），里面**装的就是 `#fcQ` / `#fcSup` 本体**。
  - 两个 input 常驻模板里的 `#fcHdrPool[hidden]`；`openFcPop()` 先 `fcParkCtl()` 把控件搬回池、重建浮层内容、再 `appendChild` 进 `.fc-pop-s`，`closeFcPop()` 再搬回去。**节点始终是同一个** → `bindFc()` 的防抖监听、`#fcReset` 清空、离线快照全部照旧，不用改口径。
  - 打开时 `setTimeout(focus,0)` 抢焦点（直接 focus 会被 click 的默认行为抢走），并 `select()` 全选，方便直接覆盖输入。
  - `renderFc()` **不再**无条件 `closeFcPop()`：搜索每敲一下都会重载表格，关了浮层就等于输入到一半被打断。浮层只在表格滚动 / 窗口 resize / 点外部 / 再次点漏斗时关闭。
  - 浮层带 `Clear` 链接（清空输入并触发 `input`），原生 search 小叉用 `::-webkit-search-cancel-button{display:none}` 隐掉，避免两个清空入口。
  - `markFcHdrSel()` 现在两类控件都看：下拉看 `value`、搜索框看 `value.trim()`，列头 `th.has` 统一高亮。
- **顺手修的一个真实缺陷**：框架表 Supplier 列显示的是**英文名**（`supplier_en` 由 `supplier_en_map` 现算），但 `_contract_where` 的 `sup` 只 LIKE `"供应商名称"`（单语种、多半是中文）→ 表里明明有 Kaisi，搜 Kaisi 却 0 行。现在 `_contract_where(args, con)` 多接一个连接，命中时用 `_cn_by_en()` 反查中文名并 OR 进 `IN (...)`（上限 500 个），`/contracts` 与 `/contracts.csv` 都传 `c`。实测 `sup=Kaisi` → 1 行（开思时代科技（安徽）有限公司），`sup=开思时代` → 2 行。
- **测试**：`board_check` 的表头段从 4 条扩到 10 条（4 枚漏斗、搜索框在浮层里且宽 240、输入时浮层不关、Clear 清空、关闭后回池保值、英文名搜索命中）；漏斗点击改用 `evaluate(...).click()` + 到位前 `sleep(600)`，避免 `page.click` 撞上 Reset 触发的 thead 重建（`Node is detached from document`）。回归：board 316/0、snapshot 64/0、ui_focus 41/0。
- **踩坑记录**：puppeteer 的 `page.screenshot({clip})` 会临时改视口（captureBeyondViewport），**触发 window resize → 浮层被 closeFcPop 关掉**。写截图脚本时别在「打开浮层 → 输入」之间插截图，否则会误判成功能坏了。

## 52 Detail 增加日期列（11-26 中午）
- **位置**：`DT_COLS` 里插在 `note` 之后、`amount` 之前 → 列序 Region / Company / Category / Supplier / Procurement Method / Single Source Reason / Note / **Date** / PRPO Amount / PO Paid（第 8 列，索引 7）。**注意 `detail_table_check` 里 note 的索引仍是 6，金额列从 7/8 变成 8/9，币种断言取 `th:nth-child(9)`**。
- **后端**：`DETAIL_EXPR["date"] = 'p."Date"'`（dash_po 已是 ISO 文本，不参与货币换算，`DETAIL_SORTS` 自动生效）。
  新增 `DETAIL_SEARCH_ONLY = DETAIL_LONG | {"date"}`：日期列**只给列头搜索**（`q_date`，LIKE 前缀匹配，如 `2026-11` 命中整月），**不给取值勾选**（几百个日期勾选没意义），`/detail/values?col=date` 直接 400。
- **前端**：`DT_COLS` 该列标 `qonly: 1`；`openDtPop` 复用 long 分支但换成日期提示（placeholder `e.g. 2026-11 or 2026-11-02`，说明改用日期范围切片器）；`fetchDtValues` 与搜索框 input 事件两处都加 `qonly` 拦截，不发取值请求。单元格按普通文本渲染（`data-tip` 悬停照旧）。
- **列宽重新分配（合计 100%）**：6.25 / 8.43 / 8.84 / **15.0**（供应商 18.7→15.0）/ 10.94 / 11.08 / **11.79**（说明 16.59→11.79）/ **7.5**（日期）/ **11.3**（金额 10.81→11.3）/ 8.36。1471px 下实测列宽 92/125/131/222/162/164/174/111/167/124，`scrollWidth-clientWidth=0`，`PRPO Amount (RMB)`、`PO Paid (RMB)`、`Single Source Reason` 均完整不省略。
- **小计行 colspan**：区域 `6→7`、公司 `5→6`（多了一列）。
- **离线快照**：`snapshot.js` 的 `DETAIL_KEYS` 加 `date`（`q_date` 搜索可用），`detailResp` 的 field 映射与行输出补 `date`，`detailValuesResp` 对 date 返回空；`board_export.ROW_COLS` 本来就有 `date`，无需改。**`exports/` 里的官方单文件是旧的，要用含日期的版本需重跑 `board_export.py`**（本次未覆盖，按约定）。
- **测试**：`detail_table_check` → 41/0（列名 10 列 + Date ISO 与排序 + 漏斗 10 个 + 合计下标 8/9 + 金额列 nth-child(9) + 币种 th(9)）；`snapshot_check` 64/0（需用新导出文件，`SNAP_FILE=绝对路径`）；`board_check` 316/0。

## 53 帕累托柱内金额 / 采购方式理由二级筛选 / 类别明细树行（11-26 下午）
- **帕累托柱内金额**：`renderPareto` 每根柱都标 `barMoney(v)`（`45.9M` 这种，不带币种，币种由坐标轴与口径说明体现）。柱子 `h>=19` 时白字 9px（柱宽 <34 用 8px）放柱内垂直居中；更矮的放**柱顶上方 4px**、7.5px 深色字——累计折线恒在柱顶之上，不会压字。`class="pamt"` + `pointer-events="none"`，点击仍然命中 `rect.pbar`。
- **采购方式下钻的二级筛选**（用户："两个 SS 补充二级筛选，是两个单一来源原因字段"）：下钻到 SS-Exempt / SS-Non Exempt 后，卡片工具栏出现 `.v-reason` 按钮（滑杆图标 + 右上角已选数量角标），点开 `.mr-pop` 浮层列出该层的理由（带订单数），可多选，**字段随 SS 切换**：SS-Exempt → `豁免单一来源的理由`（`exempt_reason`），SS-Non Exempt → `单一来源采购理由`（`single_reason`）。
  - **踩坑（务必保留）**：`S.sel[exempt_reason|single_reason]` 必须写**原始值**（`row.raws`），不能写聚合名。后端 `REASON_FILTERS` 是 `p."No." IN (SELECT 表单编号 FROM fct_prpo WHERE 原列 IN …)`，拿聚合名去筛会 0 行（KPI 直接变 `—`）。勾选态用 `mReasonOn(row, sel)` 反查原始值是否命中；`All` 展开全部 raws。和饼图图例点击（`row.raws`）同一套规则。
  - 选中后 `drawDonut` 新增第 6 参 `dimOf`：未选中的扇区/图例淡化（不改口径，只做视觉），选了才传，不选时行为与原来完全一致。
  - 浮层在窗口 resize / 点外部关闭；退出下钻时 `renderMReasonBtn()` 自动隐藏按钮并收起浮层。
- **Category Details 子类别改成 Detail 式树行**：类别行首加 `.cd-tog`（15px +/−，同 `.dt-toggle` 观感），展开后**每个子类别是一行** `tr.cd-subrow`：第 1 列 `└`、第 2 列缩进 20px 显示子类别名，Suppliers / Orders / Spend / PO Paid / Share 各自对齐到本列；点了按 Sub-category 交叉筛选（`.on` 高亮）。原来整行塞一个 `ul.cs-list` 的写法连同 `.cs-*` 样式一起删掉。后端 `pareto.subsMap` 每项新增 `sup`（`COUNT(DISTINCT p."Supplier_cn")`）供子类别行填供应商数。`fitCatTable` 的「展开时锁高 + 内部滚动」逻辑不变。
- **测试**：`board_check` 323/0（新增：每根柱都有金额、子类别是 7 列树行且首列 `└`、下钻层出现二级筛选按钮、浮层标题含字段名、勾选后 chip + 金额收窄、取消恢复、SS-Non Exempt 用 Single Source Reason 字段；`li[data-subcat]`/`cs-tog` 选择器改为 `tr.cd-subrow`/`.cd-tog`）；`visual_tools` 31/0、`snapshot` 64/0（离线类别首行改读 `.cd-cat span`，否则会把 +/− 一起读进去）、`detail` 41/0、`page_tabs` 27/0。
- 性能：`/data` 加了一个 `COUNT(DISTINCT Supplier_cn)` 后仍 0.52s（连测 3 次）。

## 54 Detail 增第四级（类目）+ Sub-Category 列；两处「展开全部 / 收起全部」（11-26 傍晚）
- **Detail 树升到四级**：Region → Company → **Category** → 明细行。分组行 td 数与 colspan：区域 `1 + colspan 8 + 2`、公司 `2 + colspan 7 + 2`、类目 `3 + colspan 6 + 2`（总 11）。类目行的名字落在 **Category 列**（td3）、公司名在 Company 列（td2），每层一个 `.dt-toggle`（`data-toggle-category` 键 = `region\u0001company\u0001category`）。
- **分组小计**：后端新增 `categoryTotals`（`GROUP BY region, company, category`，与 rt/ct 同样按筛选全集算、同样做 rate 换算与空串归一）。前端 `renderDetail` 建三层 map（`reg` / `co` / `ca`），各层按金额降序；**兜底条件也加了 `!categoryTotals.length`**，否则老快照有 rt/ct 没有 gt 时类目小计会全 0。
- **计数从名字后移到右侧说明**：`.dt-gn` 在分组行里删掉，改成 `region subtotal · 3,332 orders` 这类写法 —— 因为四级缩进把 Region/Company 列压到 85/109px，名字后面再挂计数就溢出了。
- **新增 `Sub-Category` 列**（第 4 列，紧跟 Category）：`DETAIL_EXPR["sub"] = SUB_EXPR`（`dim_agg_采购子类别` 聚合名，和筛选用同一表达式）；`DETAIL_TEXT` 也加 `"sub"`，所以该列**有取值勾选面板**（`/detail/values?col=sub` 返回 117 个聚合名），不是只搜索。
- **列宽 11 列（合计 100%）**：5.8 / 7.4 / 6.6 / 9.8 / 12.4 / 10.9 / 10.9 / 10.5 / 6.4 / 11.0 / 8.3。1471px 下实测 82/109/97/144/191/159/159/154/94/161/122，`.dt-sort` 全部 `scrollWidth == clientWidth`。**临界宽度是 1470**：`table-layout:fixed` 下百分比按表格宽度算，所以给 `.dt-tbl{min-width:1470px}` —— 1600 视口（1471）不出横向滚动，1440/1366 横向滚 159/233px，换取表头不被省略号吃掉（窄屏不许再缩列宽，缩了必然截断）。
- **两处「展开全部 / 收起全部」**（用户："能展开的位置，在父表头加个可以展开所有与收起所有的按钮"）：新增 `.xbar`（模板静态节点 `#dtXbar`、`#catXbar`），放在**表格容器外、表头之上**（表头是 sticky 的，放滚动容器里会被列头盖住），两个按钮 `.xbtn[data-xall=1|0]`。
  - Detail：`dtSetAll(open)` 用 `S.detail.keys`（renderDetail 时落下的 R/C/G 全集）全设/全清 `expR/expC/expG`，再 `renderDetail` + `saveView`。
  - Category Details：展开 = 所有 `subsMap[k].length > 1` 的类别写进 `S.catSub`（与 `.v-exp` 同口径）；收起 = `S.catSub = {}`（与 `.v-back` 同口径）。
- **状态持久化**：`S.detail.expG` 进 `board-view-v1`（saveView/restoreView），刷新后四级展开态保持（实测 374 类目 / 5892 明细行还原）。
- **离线快照**：`DETAIL_KEYS` 加 `sub`、`detailResp` 的 field 与行输出补 `sub`、新增 `categoryTotals`（与在线同结构）。`board_export.ROW_COLS` 早就有 `sub`，无需改。
- **测试**：`detail_table_check` 47/0（11 列名、11 个漏斗、类目层展开 → 明细条数 = `categoryTotals` 的 n、工具条在容器外且在表头上方、Expand all 后 `+` 号 0 个且明细数 = 全集、Collapse all 只剩区域行、子类别列有取值面板；`footCells` 改 9/10、金额列 `nth-child(10)`、币种 `th(10)`、note idx 6→7）；`board_check` 326/0（新增类别卡工具条 3 条）；`snapshot_check` 64/0（离线 11 列 + 展开到类目层）；`page_tabs` 27、`visual_tools` 31、`ui_focus` 41、`grid_sum` 14、`colfilter` 34 全绿。
- **已知取舍**：明细行第 1 列（Region）现在是空的（树符号只保留在分组行），Company/Category 列在明细行仍显示值 —— 用户要求保留这两列，视觉上略有重复但不影响识别。

## 54.1 修订：Expand all 只展到类目层（11-26 中午）
- 用户反馈「Detail 的 Expand all 非常卡」。原因：`dtSetAll(true)` 把 `expG` 也全置真，等于一次性把 **5,892 行 × 11 列**（含数据条 + data-tip）铺进 DOM，主线程要 2–4 秒。
- 改法：`dtSetAll(true)` **只设 `expR` / `expC`**，`expG` 保持空 = 展开到类目层（5 区域 + 52 公司 + 374 类目 = **431 行**）。实测 **73–133ms**（`requestAnimationFrame ×2` 计），滚动、点选都顺。明细行仍由各类目自己的 `+` 按需展开（单个类目最多几十到上百行，也是一瞬）。
- 交互提示：点 Expand all 后 `toast('Expanded to category level · click a category + to see its rows')`，按钮 `title` 写 `Expand every region, company and category (detail rows stay collapsed)`。`Collapse all` 不变（清三层，39ms）。
- **`dtSetAll` 里那句「展开全部」的旧语义不要恢复**——真要全展开，几千行 DOM 谁都救不了（分批渲染只能缓解点击那一下，后续滚动/悬停一样卡）。
- 测试：`detail_table_check` 48/0，断言改为「类目层全开、明细行 0、总行数 = 区域+公司+类目、`+` 只剩类目行」+ 性能断言「Expand all < 600ms」（实测 96ms）；`board` 326/0、`snapshot` 64/0、`ui_focus` 41/0 不受影响。

## 55 修订：两个 SS 的二级理由筛选改到「上方切片器」（11-26，用户纠正）
- **用户明确**：「不是 Procurement Method 图表的二级筛选，是上方切片器」。§53 那套图内 `.v-reason` 按钮 / `.mr-pop` 浮层 / `dimOf` 淡化**全部撤销**：`board.html` 删 `#mReasonBtn`，`board.css` 删 `.mr-pop`/`.v-reason`/`.v-badge`，`board.js` 删 `renderMReasonBtn`/`mReasonOn`/`mReasonPop`，`drawDonut` 去掉第 6 参。下钻层饼图现在直接吃 `|f` 缓存，跟着筛选走，不再做视觉淡化。
- **新增两个条件式切片器**：`FDEFS` 里 `exempt_reason`（Exemption Reason，cond `SS-Exempt`）、`single_reason`（Single Source Reason，cond `SS-Non Exempt`）**紧跟 `method` 之后**（不是末尾），`renderFilters()` 按 `cond` 过滤；`syncReasonSlicers()` 负责在 method 选择变化时局部增删这两格（只动这两格，不重建整条筛选条，否则会关掉用户正开着的下拉），并按 FDEFS 顺序插到「后面第一个已存在切片器」之前 —— **两条路径必须同序**，否则切换时理由切片器会在筛选条里跳位。不可见时同时清掉 `S.sel[k]`。
- **选项来源**：`loadReasonOpts()` 用 `loadMethodDrill(mk, cb, true)` 取 `method-drill` 的 rows（聚合名 + `n` + `raws`）。`loadMethodDrill` 现在按 `mk|f`（图表下钻，跟随筛选）/ `mk|opt`（切片器选项，排除 method 与两个理由自身筛选）**双缓存**，`drillPending` 队列合并并发请求；否则选完理由后下拉只剩已选值。
- **`S.sel` 存原始值，展示聚合名**（§53 的坑继续有效）：下拉 `<input data-raws='JSON'>`，`parseRaws()` 解析；`optBody`/`reasonText`/`chipSel` 都用 `reasonOpts[k]` 把多个原始值合并成一个聚合名 chip；移除 chip 时 `chipSel(k)` 按 label 反查 `vals` 批量删。`esc(JSON.stringify(...))` 转义后 `dataset.raws` 仍是合法 JSON —— **别改回 `join('\u0001')`**，属性里会被 HTML 转义弄坏。
- **修掉一个真实 bug**：`onMethodPick` 仍按旧键读 `S.drill[S.methodDrill]`，双缓存后变成 `undefined` → `row.raws` 拿不到 → 退化成写聚合名 → 后端 `REASON_FILTERS` 的子查询 0 行（KPI 变 `—`）。已改为 `S.drill[S.methodDrill + '|f']`。
- **`waitFor` 测试 helper** 原来只传 4 个参数，`t => ... !== t` 的第 5 个实参被丢掉（恒真）。已支持传参，5b「点理由 → 金额变化」变成真断言。
- **离线**：`snapshot.js` 早就有 `exc`/`ssr` 行内列 + `readFilters` 的 reason 键 + `methodDrillResp`，无需改代码；但 `exports/` 里官方单文件停在 11-24，缺四级树。已重跑 `board_export.py` → `exports/采购看板_单文件_20261126.html`（12.41 MB，`exports/` 在 .gitignore 里，不入库）。**发给同事前请确认用这个新文件**。
- **测试**：`board_check` 332/0（删 5 条图内断言、加「图内不再有理由按钮」+ 新增 5x 段 10 条：切片器出现/位置/选项含原始值 JSON/勾选联动/移除/换字段/两 SS 同显顺序/Reset）；`snapshot_check` 68/0（新增 5c 段 4 条离线断言）；`page_tabs` 27、`detail` 48、`visual_tools` 31、`ui_focus` 41、`grid_sum` 14、`colfilter` 34、`perf` 11、`geo` 45 全绿。

## 56 修订：理由不单独占格，收进 Method 下拉第二段（11-26，用户「类似 region 切片器的方式」）
- **用户纠正**：「不要这样，类似 region 切片器的方式」= 两个理由不该各占一个筛选格（§55 那两个独立 `.fdrop` 作废），要像 **Company 收在 Region 下拉第二段** 那样，收进 **Procurement Method 下拉**的第二/第三段。**§55 里除「独立格子 / FDEFS 位置 / syncReasonSlicers 增删 .fdrop」外的口径（双缓存、原始值、chip 合并、图内无按钮、离线垫片）全部保留**。
- **FDEFS**：两个理由项加 `parent: 'method'`（仍带 `cond`），紧跟 method 后；`renderFilters()` 的 `tops` 不再需要 cond 过滤，改为对 `kids[f.k]` **子段**按 cond 过滤后传给 `slicerHtml`，所以 Method 没选对应 SS 时下拉里根本没这段（Region 的 Company 无 cond、常驻）。`slicerHtml` 自动给子段加 `within procurement method` 标注。
- **`syncReasonSlicers()` 改写**：不再增删 `.fdrop`，只增删 Method `.fpop` 里的 `.fsec[data-k=…]`；已存在的段**只换 `.fpop-b`**（选项异步到位时），不动表头/不重建下拉，用户勾选 Method 时下拉不会被关掉；新增段按 FDEFS 顺序插到后面第一个已存在理由段之前，两个同显时顺序稳定。同时刷新 Method 按钮文字与 `.has`。
- **`fdText`**：多个下级时用统称 `reason`（避免拿 `kids[0].name` 硬套成 "exemption reasons"）；理由子项按 `chipSel(k).length` **聚合名**计数（原始值可能一对多），其余维度仍按取值数。
- **CSS 必须同步**：Method 下拉变成最多 3 段，原来 `.fsec + .fsec{min-height:150px}` 配 `.fpop{max-height:430px}` 会把**第三段裁掉**。改为 `min-height:110px` + `.fdrop[data-k="method"] .fpop{max-height:520px}`；两段时 `flex:1` 仍吃掉剩余空间，Region→Company 观感不变。测试里加了一条几何断言（第三段 bottom ≤ 下拉 bottom 且高度 ≥ 90px）。
- **恒 6 个切片器**：任何筛选状态下 `#fbar .fdrop` 都是 6 个（brand/category/expense/method/level/region），`board_check` 的「6 个等宽 / 6 组下拉」断言不用改。
- **离线**：重跑 `board_export.py` 覆盖 `exports/采购看板_单文件_20261126.html`（12.42 MB）。`snapshot.js` 无需改代码。
- **测试**：`board_check` 334/0（5x 段整体重写为「Method 下拉子段」：恒 6 格、段顺序 `method,exempt_reason`、段头 within 标注、Method 按钮汇总理由数、三段几何；并把 5b 三个下钻等待 8s→12s 消 flaky、catch 里打印运行期 JS 错误）；`snapshot_check` 68/0（5c 同步重写）；`page_tabs` 27、`detail` 48、`visual_tools` 31、`ui_focus` 41、`grid_sum` 14、`colfilter` 34、`perf` 11、`geo` 45 全绿。

## 57 修：「右上角的？点击没反应」（11-26）
- **排查结论**：整页筛选条最右侧的圆形 `?`（`.fbar-r > .spec-btn[data-spec=global]`）在线 + 离线、真实鼠标点击都正常弹出「看板整体口径」；**真正失效的是切片器右上角那个小 `?`** —— `SPEC_DEF` 唯独漏了 `filter_method`（`filter_brand/category/expense/level/region/company` 都有），`openSpec()` 里 `if (!def) return` **静默返回**，用户看到的就是「点了没反应」。Method 下拉刚改完，用户正在那一片操作，最容易撞上这个。
- **修复 1**：补 `SPEC_DEF.filter_method`（标题「筛选 · 采购方式」），口径里同步写清 §56 新增的两段理由筛选（SS-Exempt→Exemption Reason、SS-Non Exempt→Single Source Reason，理由段显示聚合名但筛选写原始值、段间「或」与采购方式「且」）。
- **修复 2（防复发，关键）**：`openSpec` 不再对未知 key 直接 return，改走 `fallbackSpec(key)` —— 按 `filter_`/`kpi_` 前缀推标题，弹出通用说明 + 「当前生效的条件」，并 `console.warn('[spec] missing definition: ' + key)`。**以后新增任何 `?` 即使忘写定义也必有反馈**，测试再断言警告为空。
- **修复 3（可发现性）**：`.fdrop .spec-btn` 从 `opacity:0; pointer-events:none`（仅 hover 才出现）改为常驻 `opacity:.55; pointer-events:auto`，尺寸 13→15px，hover/展开时加深。原来完全隐形，用户不易察觉那里有个按钮。
- **测试**：`board_check` 339/0（新增 5y 段 5 条：所有 ? 带 data-spec、右上角圆 ? 弹出且 display:flex、Method 小 ? 弹出「筛选 · 采购方式」、全量 sweep 逐个 `.click()` 断言每个都弹且标题非空、全程 0 条 missing definition 警告；`page.on('console')` 增加 specWarns 收集）；`snapshot_check` 72/0（新增 5d 段 4 条离线断言）。`page_tabs` 27、`detail` 48、`visual_tools` 31、`ui_focus` 41、`grid_sum` 14、`colfilter` 34、`perf` 11、`geo` 45 全绿。
- **离线**：`board.js` 改了，已重跑 `board_export.py` 覆盖 `exports/采购看板_单文件_20261126.html`（12.42 MB）；发给同事仍用这一份。

## 58 Detail 加 City 列（11-26）
- **取数**：直接用 `dash_po."City_cn"`（26 个取值、0 空值，公司→城市 **1:1**，无多城市公司），**不 JOIN `fct_company_location`**。`DETAIL_TEXT`/`DETAIL_EXPR` 加 `city`，位置在 `company` 之后；`columns` 自动含 city（第 3 位）。`companyTotals` 用 `MAX(city) city` 带出，公司分组行也能显示城市（默认折叠态就看得见）。
- **前端**：`DT_COLS` 插 City 在第 3 位（12 列）；列宽重排 sub 9.8→8.3、supplier 12.4→10.9、reason 10.9→9.4、note 10.5→9.0 腾出 city 6.0，合计仍 100%。四级树分组行 colspan 同步：区域 8→9（含 company/city/category/…/date 共 9 列），公司行 +1 个 city `<td>` 后 gmeta 仍 colspan 7（category…date），类目行 +1 个空 `<td>` 占 city 位、gmeta 仍 colspan 6。`.dt-tbl min-width` 1470→1560（12 列临界）；1920 视口下 scrollWidth=clientWidth=1791 无横滚。
- **离线**：`board_export.py` 的 `ROW_COLS`/`ROW_SQL`/`build_rows` 加 `city`（在 region 后）；`snapshot.js` 的 `DETAIL_KEYS`/排序 `field`/行输出/`cmap` 初值/`ct` 输出都加 city。重跑导出 → 单文件 12.54 MB。
- **测试**：`detail_table_check` 49/0（12 列断言插 City，列号全后移 1：金额合计 tfoot 9/10→10/11、公司小计 nth-child 4→5、子类别 4→5、说明 idx 7→8、明细金额 10→11、币种表头 10→11；新增 cityApi 断言公司→城市 1:1 + companyTotals 带城市 + columns 位置）；`snapshot_check` 73/0（离线 12 列断言 + 公司分组行与明细行城市一致）；其余 board 339、page_tabs 27、visual 31、ui_focus 41、grid_sum 14、colfilter 34、perf 11、geo 45 全绿。

## 59 修订：City 移到 Company 前 + 升为可展开树层（11-26，用户「city要在company前面，要有展开」）
- **§58 作废部分**：City 不再是 Company 后面的普通列，改到 **Company 前面**（列序 Region → City → Company → Category → Sub-Category → …）；也不再只在公司行显示城市名，而是**升为独立树层**。
- **树从四级升五级**：Region → **City** → Company → Category → 明细。后端新增 `cityTotals`（`GROUP BY region, city`）；`companyTotals` 改 `GROUP BY region, city, company`（带 city）；`categoryTotals` 改 `GROUP BY region, city, company, category`（带 city）。返回体加 `cityTotals`。
- **前端**：新增 `dtCyKey(region,city)` 与展开态 `expY`（存/读 `sessionStorage` 同步加）；树 map 多一层 `g.cys[city].cos[company].cats[category]`；各层按金额降序。`dtSetAll` 的 keys 加 `Y`，全展开仍只到类目层（区域+城市+公司+类目几百行），明细按需展开。新增 `data-toggle-city` 点击处理。CSS 加 `.dt-city` 分组行（底色介于区域/公司之间）。**各级名称必须落在本名列**：区域 x=57 / 城市 x=161 / 公司 x=268 / 类目 x=401（DOM 实测），靠 group 行 `colspan` 递减 9/8/7/6 对齐，截图缩放会造成「类目落在 Company 列」的错觉 —— 以 DOM 坐标为准。
- **离线**：`snapshot.js` 的 `DETAIL_KEYS` 把 city 移到 company 前；`detailResp` 新增 `ymap`/`yt`（cityTotals），companyTotals/categoryTotals 带 city。重导单文件 12.54 MB。
- **测试**：`detail_table_check` 52/0（1b 段改五级树：默认全折叠→展开区域出城市行→展开城市出公司行→展开公司出类目行→展开类目出明细，逐级断言小计金额与降序；Expand all 断言加 cities 且 rows=区域+城市+公司+类目）；`snapshot_check` 73/0（离线树多展开一层 + 城市分组行与明细行城市一致）；其余 board 339、page_tabs 27、visual 31、ui_focus 41、grid_sum 14、colfilter 34、perf 11、geo 45 全绿。

## 60 Detail 加 Fiscal Year 列（11-26）
- **取数**：行级 `p."FY"`（dash_po 自带 FY2425/FY2526/FY2627），与日期表 FiscalYearName 一致。**跨财年日期范围**切片器下同一张表会出现多个财年，每行显示自己的 FY；放进 `DETAIL_TEXT` 自动获得排序 + 取值筛选（只有 3 个值，`/detail/values?col=fy` 可直接勾选）。位置在 **Single Source Reason 之后、Note 之前**，使后端 `columns`（`list(DETAIL_TEXT)+note/date/amount/paid`）与前端 `DT_COLS` 顺序严格一致。
- **前端**：13 列，列宽重排（company 7.4→7.0、category 6.6→6.3、sub 8.3→7.9、supplier/method 10.9→10.4、reason 9.4→8.4、note 9.0→7.1，腾出 FY 5.0，合计仍 100%）。五级树各级 gmeta colspan 各 +1：区域 9→10、城市 8→9、公司 7→8、类目 6→7。`.dt-tbl min-width` 1560→1650。DOM 实测 FY 表头与明细单元格同 x=1171，无横滚。
- **离线**：`board_export.py` 的 ROW_COLS/ROW_SQL 早已含 `fy`，无需改；`snapshot.js` 的 `DETAIL_KEYS`、行输出、排序 `field` 加 `fy`（放在 reason 后）。重导单文件 12.54 MB。
- **测试**：`detail_table_check` 53/0（13 列断言、funnel 13、FY 值/取值筛选/columns 位置、列号后移：tfoot 10/11→11/12、说明 idx 8→9、明细金额 11→12、币种表头 11→12）；`snapshot_check` 74/0（13 列 + 离线 FY 值第 9 格）；其余 board 339、page_tabs 27、visual 31、ui_focus 41、grid_sum 14、colfilter 34、perf 11、geo 45（geo 首跑偶发地图 zoom 未就绪，单跑 45/0）全绿。
- **坑**：改 gmeta colspan 时 Edit 的 old_string 若只匹配到 `subtotal · '` 会把后面的 `' +` 一起吃掉，导致 `missing ) after argument list` 首屏全白。**改完 JS 必须 `node --check static/board/board.js`**，别只靠浏览器测。

## 61 Detail 表头改短名（11-26，用户「Fiscal Year 直接用 FY，其他过长的表头也修改下」）
- **只改显示名 `DT_COLS[].t`**（后端 `DETAIL_TEXT` 的 value 只是语义标注、`columns` 用 key，不受影响）：`Fiscal Year → FY`、`Single Source Reason → Reason`、`Procurement Method → Method`。**列宽、列序、key、colspan 全部不动**，零连锁改动。
- **依据**：1600 视口实测原来只有 `Single Source Reason`（sw124 > cw106）和 `Fiscal Year`（sw64 > cw49）被省略号截断，`Procurement Method` 虽完整但偏长；`Sub-Category`/`PRPO Amount (RMB)`/`PO Paid (RMB)` 都完整，保留原名。改后 13 个表头 `scrollWidth <= clientWidth` 全部为 true。
- **测试**：`detail_table_check` 54/0（表头断言改短名 + 新增「13 个表头在 1600 视口下全部完整，无省略号截断」断言）；`snapshot_check` 74/0（离线表头短名）；其余 board 339、page_tabs 27、visual 31、ui_focus 41、grid_sum 14、colfilter 34、perf 11、geo 45 全绿。重导单文件 12.54 MB。

## 62 财年口径确认 + 页签居中（11-26）
- **财年口径（用户问）**：顶部财年筛选走 `board_api._scope()` 的 `c."FiscalYearName" = ?` —— 由 **PRPO 提交日期**关联日期表 `dash_calendar` 得出，**不是**直接读 `fct_prpo` 的「财年」字段。`_fy_list()` 也从 `dash_calendar.FiscalYearName` 取下拉值。实测 `dash_po.FY` 与 `calendar.FiscalYearName` **46,013 行 0 处不符**，两者等价。Detail 里新加的 FY 列取行级 `p."FY"`，跨财年日期范围时逐行显示；指定 `dateFrom/dateTo` 时不再叠加财年条件（`_scope` 的 `has_date` 分支）。
- **页签位置（用户反馈「有点靠左」）**：`.pg-tabs` 原是固定 `margin-left:14px` 紧贴标题（1600 下左间距 30px、右间距 111px），与 CSS 注释「.hd-r 与 .pg-tabs 各占一个 auto margin」不符。改 `.pg-tabs{margin-left:auto}` 后两个 auto margin 均分剩余空间：1600 下页签从 x=599 移到 x=639，左右间距各 70px；1024～1600 全部顶栏 68px、无溢出、表头无截断。窄屏媒体查询里的 `margin-left:10px` 保持不变（刻意贴左防溢出）。
- **测试**：`page_tabs_check` 28/0（新增「页签左右留白接近、不紧贴标题」断言）；board 339、ui_focus 41、detail 54、snapshot 74 全绿。

## 63 离线单文件不嵌入 Geo Spend 页（11-27）
- **用户要求**：「geo页面不要嵌入导出的离线版」。在线版保留四页签（Geo 照常用），只改导出链路。
- **导出侧** `board_export.py`：删掉 `build_geo_loc()`，`build_snapshot()` 不再写 `geoLoc`，改打 `noGeo:true` 标记；`build()` 里用正则删整个 `<main class="page" id="pgGeo" hidden>…</main>`（含前置注释），匹配数必须为 1 否则中止。仍清空 `TMAP_KEY`，不把 Key 泄漏进可转发文件。
- **前端** `static/board/board.js`：顶部 `NO_GEO = !!(window.__SNAP__ && window.__SNAP__.noGeo)`（在线恒 false）。`PAGES` 定义里去掉 geo 项，改为 `if (!NO_GEO) PAGES.geo = …`；`init()` 里 `bindGeo()` 同样加条件（否则离线找不到 `#geoMetric` 直接报错）。离线打开 `#geo` 锚点时，`<head>` 防闪脚本仍会加 `boot-other`（隐藏 Overview 3 秒），`init()` 开头立刻移除 class 并 `history.replaceState('#overview')`。`saveView/restoreView` 的 geo 字段留着无害。
- **测试**：`snapshot_check` 76/0（新增「离线只有三页签、无 Geo 节点与坐标数据」+「`#geo` 回退 Overview 不白屏」；后者用新标签页打开 `file:///…#geo`，避免当前页 hash 导航不重载的坑）；`page_tabs_check` 28/0 仍断言在线 4 页签含 Geo Spend；board 339、geo 45、detail 54、visual 31、ui_focus 41、grid_sum 14、colfilter 34、perf 11 全绿。
- **交付**：重导 `exports/采购看板_单文件_20261127.html`（12.54 MB，含 §55–§62 全部改动）。**注意测试依赖路径变了**：临时目录 `Temp/ui-probe/node_modules` 被清理，puppeteer-core 现在在 `C:/Users/CONG ZHU/.workbuddy/binaries/node/workspace/node_modules`，跑测试用这个 `NODE_PATH`。
