// 表头下拉字段筛选（Excel 式自动筛选）验证：
//   点列头漏斗 → 下拉列出该列真实取值（带出现次数）→ 勾选即筛 → chips / 清除 / 高级条件。
// 跑法（服务先起）：node tests/colfilter_check.js
const puppeteer = require("C:/Users/CONG ZHU/.workbuddy/binaries/node/workspace/node_modules/puppeteer-core");
const fs = require("fs");
const OUT = "D:/code/采购数据库建设/colfilter_result.txt";
const lines = [];
const log = s => { lines.push(s); console.log(s); };
const save = () => fs.writeFileSync(OUT, lines.join("\n"), "utf8");
const watchdog = setTimeout(() => { log("!! 看门狗超时"); save(); process.exit(2); }, 180000);
const sleep = ms => new Promise(r => setTimeout(r, ms));
let pass = 0, fail = 0;
function chk(name, cond, extra) {
  if (cond) { pass++; log("  [OK] " + name); }
  else { fail++; log("  [XX] " + name + (extra ? " -> " + extra : "")); }
}

(async () => {
  const browser = await puppeteer.launch({
    executablePath: "C:/Program Files/Google/Chrome/Application/chrome.exe",
    headless: "new",
    args: ["--no-sandbox", "--disable-dev-shm-usage"],
    defaultViewport: { width: 1680, height: 1050 },
  });
  const page = await browser.newPage();
  const errs = [];
  page.on("pageerror", e => errs.push("pageerror: " + e.message));
  page.on("console", m => { if (m.type() === "error") errs.push("console: " + m.text()); });

  const COL = "供应商名称";

  log("=== 1. 打开 fct_po 列表 ===");
  await page.goto("http://127.0.0.1:5000/", { waitUntil: "networkidle2" });
  await page.waitForSelector("#grid", { timeout: 20000 });
  await page.evaluate(t => selectTable(t), "fct_po");
  await page.waitForFunction(() => {
    const t = document.querySelector("#grid table");
    return t && t.querySelectorAll("tbody tr").length > 0;
  }, { timeout: 30000 });
  const totalAll = await page.evaluate(() => state.total);
  chk("表格已渲染", totalAll > 0, totalAll);

  // 后端基准值（用同一接口拿，避免写死数字）
  const api = await page.evaluate(async c => {
    const r = await fetch(`/api/tables/fct_po/values?col=${encodeURIComponent(c)}&limit=3`);
    const d = await r.json();
    const r2 = await fetch("/api/tables/fct_po/rows?page=1&size=1");
    return { v: d.values, empty: d.empty, distinct: d.distinct, total: (await r2.json()).total };
  }, COL);
  chk("取值接口有数据", api.v.length === 3, JSON.stringify(api.v.slice(0, 1)));
  chk("取值带出现次数", api.v[0].n > 0, api.v[0].n);

  log("=== 2. 点列头漏斗 → 下拉列出取值 ===");
  await page.evaluate(c => {
    const th = [...document.querySelectorAll("#grid thead th")]
      .find(t => t.querySelector(".fn") && t.querySelector(".fn").dataset.filt === c);
    th.querySelector(".fn").click();
  }, COL);
  await page.waitForFunction(() => {
    const box = document.querySelector("#pmVals");
    return box && box.querySelectorAll(".pm-val").length > 0;
  }, { timeout: 20000 });
  const panel = await page.evaluate(() => {
    const m = document.getElementById("filterMenu");
    const r = m.getBoundingClientRect();
    return {
      shown: m.classList.contains("show"),
      width: Math.round(r.width),
      rows: m.querySelectorAll(".pm-val").length,
      countText: (m.querySelector("#pmCount") || {}).textContent || "",
      first: m.querySelector(".pm-valv .pv").textContent,
      firstN: m.querySelector(".pm-valv .pn").textContent,
      last: (document.querySelector("#pmNull .pm-valnull .pv") || {}).textContent || "",
      hasEmpty: !!m.querySelector(".pm-val input[data-empty]"),
      hasSearch: !!m.querySelector("#pmSearch"),
      hasAdv: !!m.querySelector(".pm-adv #pmOp"),
      inView: r.left >= -1 && r.right <= innerWidth + 1,
    };
  });
  chk("面板已弹出", panel.shown);
  chk("面板在视口内", panel.inView, panel.width + "px");
  chk("列出取值（可滚动列表）", panel.rows >= 3, panel.rows);
  chk("标题显示取值种数", /种取值/.test(panel.countText), panel.countText);
  chk("首个取值与出现次数", panel.first === api.v[0].v && /\d/.test(panel.firstN),
      panel.first + " / " + panel.firstN);
  chk("有搜索框", panel.hasSearch);
  chk("有「（空值）」选项（固定显示在列表上方）", panel.hasEmpty && panel.last === "（空值）", panel.last);
  chk("保留高级条件（按条件筛选）", panel.hasAdv);
  chk("下拉按出现次数降序", panel.first === api.v[0].v);
  await (await page.$("#filterMenu")).screenshot({
    path: "D:/code/采购数据库建设/.shot/cf_panel.png" });
  await page.screenshot({ path: "D:/code/采购数据库建设/.shot/cf_page.png" });

  log("=== 3. 搜索取值 ===");
  await page.type("#pmSearch", "深圳");
  await sleep(700);
  const searched = await page.evaluate(() => ({
    n: document.querySelectorAll("#pmVals .pm-valv").length,
    allMatch: [...document.querySelectorAll("#pmVals .pm-valv .pv")]
      .every(e => e.textContent.includes("深圳"))
  }));
  chk("搜索后仍有结果", searched.n > 0, searched.n);
  chk("搜索结果都含关键字", searched.allMatch);
  await page.evaluate(() => { document.getElementById("pmSearch").value = ""; pmSearchChange(); });
  await sleep(700);

  log("=== 4. 勾选一个取值 → 应用 ===");
  await page.evaluate(() => document.querySelector("#pmVals .pm-val input[data-v]").click());
  const selInfo = await page.evaluate(() => document.getElementById("pmSelInfo").textContent);
  chk("勾选后提示已选 1 项", /1 项/.test(selInfo), selInfo);
  await page.evaluate(() => applyColFilter());
  await sleep(1600);
  const after = await page.evaluate(() => ({
    total: state.total,
    filters: JSON.stringify(state.filters),
    chip: (document.querySelector(".chipf") || {}).textContent || "",
    fnOn: !!document.querySelector(`thead .fn.on[data-filt="供应商名称"]`)
  }));
  chk("筛选后行数 = 该取值出现次数", after.total === api.v[0].n, after.total + " vs " + api.v[0].n);
  chk("chips 显示「属于 1 项」", /属于 1 项/.test(after.chip), after.chip);
  chk("列头漏斗高亮", after.fnOn);
  chk("筛选状态是 in + vals", /"op":"in"/.test(after.filters) && /"vals"/.test(after.filters),
      after.filters.slice(0, 120));
  await page.screenshot({ path: "D:/code/采购数据库建设/.shot/cf_filtered.png" });

  log("=== 5. 重开面板：勾选状态回显 ===");
  await page.evaluate(() => {
    document.querySelector('thead .fn[data-filt="供应商名称"]').click();
  });
  await page.waitForFunction(() => document.querySelectorAll("#pmVals .pm-val").length > 0,
    { timeout: 20000 });
  const reopened = await page.evaluate(() => ({
    checked: document.querySelectorAll('#pmVals input[data-v]:checked').length,
    info: document.getElementById("pmSelInfo").textContent
  }));
  chk("已应用的取值仍勾选", reopened.checked === 1, reopened.checked);
  chk("回显已选 1 项", /1 项/.test(reopened.info), reopened.info);

  log("=== 6. 全选 / 清空 ===");
  await page.evaluate(() => pmSelectAll());
  const allSel = await page.evaluate(() => ({
    checked: document.querySelectorAll("#pmVals input:checked").length,
    info: document.getElementById("pmSelInfo").textContent
  }));
  chk("全选勾上列表全部（含空值）", allSel.checked >= 200, allSel.checked);
  await page.evaluate(() => pmSelectNone());
  const noneSel = await page.evaluate(() => document.querySelectorAll("#pmVals input:checked").length);
  chk("清空取消全部勾选", noneSel === 0, noneSel);

  log("=== 7. 清除筛选 ===");
  await page.evaluate(() => clearColFilter());
  await sleep(1500);
  const cleared = await page.evaluate(() => ({ total: state.total, chips: document.querySelectorAll(".chipf").length }));
  chk("清除后回到全量", cleared.total === totalAll, cleared.total);
  chk("chips 清空", cleared.chips === 0);

  log("=== 8. 高级条件仍然可用（包含）===");
  await page.evaluate(() => {
    document.querySelector('thead .fn[data-filt="供应商名称"]').click();
  });
  await page.waitForFunction(() => document.querySelectorAll("#pmVals .pm-val").length > 0,
    { timeout: 20000 });
  await page.evaluate(() => {
    const d = document.querySelector(".pm-adv"); d.open = true;
    const s = document.getElementById("pmOp"); s.value = "contains";
    pmOpChange();
    document.getElementById("pmVal").value = "深圳";
    applyColFilter();
  });
  await sleep(1600);
  const adv = await page.evaluate(() => ({
    total: state.total, chip: (document.querySelector(".chipf") || {}).textContent || ""
  }));
  chk("包含筛选生效", adv.total > 0 && adv.total < totalAll, adv.total);
  chk("chips 显示「包含」", /包含/.test(adv.chip), adv.chip);

  log("=== 9. 空值筛选 ===");
  await page.evaluate(() => clearAllFilters());
  await sleep(1200);
  await page.evaluate(() => {
    document.querySelector('thead .fn[data-filt="供应商名称"]').click();
  });
  await page.waitForFunction(() => document.querySelectorAll("#pmVals .pm-val").length > 0,
    { timeout: 20000 });
  await page.evaluate(() => {
    document.querySelector('#pmNull input[data-empty]').click();
    applyColFilter();
  });
  await sleep(1600);
  const empt = await page.evaluate(() => state.total);
  chk("只勾空值 → 行数 = 空值行数", empt === api.empty, empt + " vs " + api.empty);

  log("=== 10. 导出跟随筛选 ===");
  const csvRows = await page.evaluate(async () => {
    const p = new URLSearchParams({ page: 1, size: 1 });
    p.append("filters", JSON.stringify(state.filters));
    const r = await fetch(`/api/tables/fct_po/export?` + p);
    const t = await r.text();
    return t.replace(/^\ufeff/, "").trim().split("\r\n").length - 1;
  });
  chk("导出 CSV 行数 = 筛选后行数", csvRows === api.empty, csvRows + " vs " + api.empty);


  log("=== 12. 数值列（金额原样 '¥…' 文本）也走同一套取值筛选 ===");
  await page.evaluate(() => { clearAllFilters(); });
  await sleep(1000);
  const numApi = await page.evaluate(async () => {
    const r = await fetch("/api/tables/fct_po/values?col=" +
      encodeURIComponent("总支付总额（人民币）") + "&limit=3");
    return await r.json();
  });
  chk("数值列取值仍是库里原文", /^[¥￥$]/.test((numApi.values[0] || {}).v || ""),
      (numApi.values[0] || {}).v);
  await page.evaluate(c => {
    document.querySelector(`thead .fn[data-filt="${c}"]`).click();
  }, "总支付总额（人民币）");
  await page.waitForFunction(() => document.querySelectorAll("#pmVals .pm-valv").length > 0,
    { timeout: 20000 });
  await page.evaluate(() => {
    document.querySelector("#pmVals .pm-valv input[data-v]").click();
    applyColFilter();
  });
  await sleep(1500);
  const numAfter = await page.evaluate(() => state.total);
  chk("按金额取值筛选命中行数一致", numAfter === numApi.values[0].n,
      numAfter + " vs " + numApi.values[0].n);

  log("=== 13. 暗色主题 ===");
  await page.evaluate(() => { clearAllFilters(); toggleTheme(); });
  await sleep(1200);
  await page.evaluate(() => {
    document.querySelector('thead .fn[data-filt="提交者"]').click();
  });
  await page.waitForFunction(() => document.querySelectorAll("#pmVals .pm-valv").length > 0,
    { timeout: 20000 });
  const dark = await page.evaluate(() => {
    const m = document.getElementById("filterMenu");
    const cs = getComputedStyle(m);
    const item = m.querySelector(".pm-valv .pv");
    return { bg: cs.backgroundColor, color: getComputedStyle(item).color,
             rows: m.querySelectorAll(".pm-valv").length };
  });
  chk("暗色下面板有背景色（非透明）", dark.bg !== "rgba(0, 0, 0, 0)" && dark.bg !== "transparent", dark.bg);
  chk("暗色下取值文字可见", dark.rows > 0 && dark.color !== "", dark.color);
  await (await page.$("#filterMenu")).screenshot({
    path: "D:/code/采购数据库建设/.shot/cf_dark.png" });
  await page.evaluate(() => { closePm(); toggleTheme(); });
  await sleep(600);

  log("=== 14. 运行期错误 ===");
  chk("全程 0 JS 报错", errs.length === 0, errs.slice(0, 3).join(" | "));

  clearTimeout(watchdog);
  log(`\n结果：${pass} 通过 / ${fail} 失败`);
  save();
  await browser.close();
  process.exit(fail ? 1 : 0);
})().catch(e => { log("!! 异常：" + e.message); save(); process.exit(2); });
