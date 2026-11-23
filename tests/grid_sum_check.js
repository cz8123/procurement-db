// 列表视图（数据浏览页）验证：金额列声明 REAL 实存 '¥30,000.00'，服务端汇总不能再算成 0。
// 前端合计行已按用户要求移除；本测试改为直读后端 API 的 aggregates，守住数据层正确性，
// 同时验证表格视图布局（无合计行/无汇总按钮）、数值排序、金额筛选仍正常。
const puppeteer = require("C:/Users/CONG ZHU/.workbuddy/binaries/node/workspace/node_modules/puppeteer-core");
const fs = require("fs");
const OUT = "D:/code/采购数据库建设/grid_sum_result.txt";
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
  page.on("dialog", d => d.accept());

  const Q = "含增值税总价";

  log("=== 1. 打开 fct_prpo 列表 ===");
  await page.goto("http://127.0.0.1:5000/", { waitUntil: "networkidle2" });
  await page.waitForSelector("#grid", { timeout: 20000 });
  await page.evaluate(t => selectTable(t), "fct_prpo");
  await page.waitForFunction(() => {
    const t = document.querySelector("#grid table");
    return t && t.querySelectorAll("tbody tr").length > 0;
  }, { timeout: 30000 });
  chk("表格已渲染", (await page.$$("#grid tbody tr")).length > 0);

  log("=== 2. 表格视图下方无合计行 / 无汇总按钮（新需求）===");
  chk("无 tfoot 合计行", (await page.$$("#grid tfoot")).length === 0);
  chk("无 Σ 汇总按钮", (await page.$("#sumBtn")) === null);
  chk("导出/列/新增按钮仍在", await page.evaluate(() =>
    ["colBtn", "exportBtn", "addBtn"].every(id => !!document.getElementById(id))));

  log("=== 3. 后端合计不再是 0（金额列 REAL 实存 ¥ 文本，别被 CAST 成 0）===");
  const base = await page.evaluate(async c => {
    const r = await fetch("/api/tables/fct_prpo/rows?page=1&size=1");
    const d = await r.json();
    const a = d.aggregates[c] || {};
    return { sum: a.sum, count: a.count, avg: a.avg };
  }, Q);
  chk("后端合计非空", base.sum != null, String(base.sum));
  chk("后端合计不等于 0", Math.abs(base.sum) > 0.01, String(base.sum));
  /* 不写死金额（每次导入都会变）：用「合计 ≈ 平均值 × 非空行数」校验口径自洽 */
  chk("后端合计 = 平均值 × 行数", base.avg != null &&
      Math.abs(base.sum - base.avg * base.count) < Math.max(1, base.sum * 1e-9),
      JSON.stringify([base.sum, base.avg, base.count]));

  log("=== 4. 金额列右对齐 ===");
  chk("单元格带 num 类", await page.evaluate(c => {
    const hs = [...document.querySelectorAll("#grid thead th")].map(t => t.textContent.trim());
    const idx = hs.findIndex(t => t.startsWith(c));
    const td = document.querySelector("#grid tbody tr").children[idx];
    return td.classList.contains("num");
  }, Q));

  log("=== 5. 点列头按数值排序（不是字典序）===");
  await page.evaluate(c => {
    const el = [...document.querySelectorAll("#grid .th-name")].find(e => e.textContent.trim().startsWith(c));
    el.click();
  }, Q);
  await sleep(1500);
  const ascHead = await page.evaluate(c => {
    const hs = [...document.querySelectorAll("#grid thead th")].map(t => t.textContent.trim());
    const idx = hs.findIndex(t => t.startsWith(c));
    return [...document.querySelectorAll("#grid tbody tr")].slice(0, 4)
      .map(tr => tr.children[idx].textContent.trim());
  }, Q);
  await page.evaluate(c => {
    const el = [...document.querySelectorAll("#grid .th-name")].find(e => e.textContent.trim().startsWith(c));
    el.click();
  }, Q);
  await sleep(1500);
  const descFirst = await page.evaluate(c => {
    const hs = [...document.querySelectorAll("#grid thead th")].map(t => t.textContent.trim());
    const idx = hs.findIndex(t => t.startsWith(c));
    return document.querySelector("#grid tbody tr").children[idx].textContent.trim();
  }, Q);
  chk("升序开头是空值/¥0.00（按数值排，不是字典序）",
      (ascHead[0] === "" || ascHead[0] === "¥0.00") && ascHead.includes("¥0.00"),
      JSON.stringify(ascHead));
  chk("降序首行是 ¥7,938,000.00", descFirst === "¥7,938,000.00", descFirst);

  log("=== 6. 按金额筛选（>100万）===");
  /* 期望值不写死金额：数据每次导入都会变，这里校验「合计口径 == 逐行金额之和」，
     再顺带确认筛选确实命中（这正是 grID 底部合计容易出错的地方） */
  const n = await page.evaluate(async c => {
    const f = encodeURIComponent(JSON.stringify([{ col: c, op: "gt", val: "1000000" }]));
    const head = await (await fetch(`/api/tables/fct_prpo/rows?page=1&size=1&filters=` + f)).json();
    const all = await (await fetch(`/api/tables/fct_prpo/rows?page=1&size=200&filters=` + f)).json();
    const num = v => {
      if (v == null || v === "") return 0;
      const x = Number(String(v).replace(/[^0-9.\-]/g, ""));
      return isNaN(x) ? 0 : x;
    };
    const rows = all.rows || [];
    return {
      total: head.total,
      sum: head.aggregates[c].sum,
      nRows: rows.length,
      rowsSum: rows.reduce((a, r) => a + num(r[c]), 0),
      covered: rows.length === head.total
    };
  }, Q);
  chk("筛选有结果", n.total > 0, String(n.total));
  chk("合计 = 逐行金额之和（口径一致）", Math.abs(n.sum - n.rowsSum) < 0.01,
      JSON.stringify([n.sum, n.rowsSum]));
  chk("命中行数可在单页内逐行核对（≤200）", n.covered, String(n.total));

  log("=== 7. 无 JS 报错 ===");
  chk("无 pageerror / console error", errs.length === 0, errs.join(" | ").slice(0, 300));

  log(`\n===== ${pass} 通过 / ${fail} 失败 =====`);
  save();
  clearTimeout(watchdog);
  await browser.close();
  process.exit(fail ? 1 : 0);
})().catch(e => { log("!! 异常: " + e.message); save(); process.exit(3); });
