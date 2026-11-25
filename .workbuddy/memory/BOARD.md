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
