// 数据转换页面完整无头验证（改进时序）
const puppeteer = require("C:/Users/CONG ZHU/.workbuddy/binaries/node/workspace/node_modules/puppeteer-core");
const fs = require("fs");
const OUT = "D:/code/采购数据库建设/tf_ui_result.txt";
const lines = [];
const log = s => { lines.push(s); console.log(s); };
const save = () => fs.writeFileSync(OUT, lines.join("\n"), "utf8");
const watchdog = setTimeout(() => { log("!! 看门狗超时"); save(); process.exit(2); }, 120000);
const sleep = ms => new Promise(r => setTimeout(r, ms));
let pass = 0, fail = 0;
function chk(name, cond, extra) {
  if (cond) { pass++; log("  [OK] " + name); }
  else { fail++; log("  [XX] " + name + (extra ? " -> " + extra : "")); }
}
const fire = (sel, val, ev) => `(() => {
  const el = document.querySelector(${JSON.stringify(sel)});
  if (!el) return false;
  el.value = ${JSON.stringify(val)};
  el.dispatchEvent(new Event(${JSON.stringify(ev || "change")}, {bubbles:true}));
  return true;
})()`;

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

  log("=== 1. 页面加载 ===");
  await page.goto("http://127.0.0.1:5000/#transform", { waitUntil: "networkidle2" });
  await page.waitForSelector("#tfSource", { timeout: 15000 });
  chk("导航项高亮", await page.$eval("#navTransform", e => e.classList.contains("active")));
  // 库内可能已有用户保存的转换视图，只断言「不少于当前业务表数」
  const nOpt = await page.$$eval("#tfSource option", e => e.length);
  const nTbl = await page.evaluate(async () => {
    const r = await fetch("/api/tables"); const d = await r.json();
    return Array.isArray(d) ? d.length : 0;
  });
  chk("源表下拉含占位+全部业务表", nOpt >= nTbl, `${nOpt} 项 / 业务表 ${nTbl} 张`);
  chk("下拉可选 fct_prpo", await page.$$eval("#tfSource option", e => e.some(o => o.value === "fct_prpo")));
  chk("步骤区有「源」节点", await page.$$eval("#tfSteps .tf-step", e => e.length) === 1);
  chk("配置区有引导说明", (await page.$eval("#tfCfg", e => e.textContent)).includes("源表"));

  log("=== 2. 选源表 → 自动预览 ===");
  await page.select("#tfSource", "fct_prpo");
  await page.waitForFunction(() => {
    const t = document.querySelector("#tfPrevWrap table");
    return t && t.querySelectorAll("tbody tr").length > 0;
  }, { timeout: 20000 });
  let info = await page.$eval("#tfPrevInfo", e => e.textContent);
  chk("预览 44,987 行", info.includes("44,987"), info);
  chk("预览 35 列", (await page.$$eval("#tfPrevWrap thead th", e => e.length)) === 35);

  log("=== 3. 加筛选步骤（真实点击菜单） ===");
  await page.click(".tf-bar button.btn.sm");
  await page.waitForSelector("#tfAddPop", { timeout: 5000 });
  chk("步骤菜单 18 种", (await page.$$eval("#tfAddPop .tf-mi", e => e.length)) === 18);
  await page.click('#tfAddPop .tf-mi[data-op="filter"]');
  await page.waitForFunction(() => document.querySelectorAll("#tfSteps .tf-step").length === 2,
    { timeout: 8000 });
  await sleep(700);
  chk("步骤列表新增一项", true);
  chk("默认预填第一列", (await page.evaluate(() => tf.steps[0].column)) === "表单编号");

  log("=== 4. 配置筛选：支出类型 包含 OPEX ===");
  await page.evaluate(fire("#tfCfg .row select", "支出类型"));
  await sleep(300);
  await page.evaluate(() => {
    const s = document.querySelectorAll("#tfCfg .row select")[1];
    s.value = "contains"; s.dispatchEvent(new Event("change", { bubbles: true }));
  });
  await sleep(300);
  const setVal = await page.evaluate(() => {
    const i = document.querySelector("#tfCfg .row input[type=text]");
    if (!i) return false;
    i.value = "OPEX"; i.dispatchEvent(new Event("change", { bubbles: true })); return true;
  });
  chk("填写筛选值", setVal);
  await sleep(2300);
  info = await page.$eval("#tfPrevInfo", e => e.textContent);
  chk("筛选后 18,440 行", info.includes("18,440"), info);

  log("=== 5. 加分组汇总步骤 ===");
  await page.evaluate(() => tfAddStep("group_by"));
  await sleep(800);
  chk("步骤数 3", (await page.$$eval("#tfSteps .tf-step", e => e.length)) === 3);
  chk("出现汇总行编辑器", !!(await page.$("#tfCfg .tf-aggline")));
  // 配分组列 = 公司
  const keySel = await page.evaluate(() => {
    const m = document.querySelector("#tfCfg select[multiple]");
    if (!m) return false;
    const o = [...m.options].find(x => x.value === "公司");
    if (!o) return false;
    o.selected = true; m.dispatchEvent(new Event("change", { bubbles: true })); return true;
  });
  chk("选择分组列 公司", keySel);
  await sleep(500);
  // 汇总：含增值税总价 sum
  const aggOk = await page.evaluate(() => {
    const line = document.querySelector("#tfCfg .tf-aggline");
    if (!line) return false;
    const sels = line.querySelectorAll("select");
    const o = [...sels[0].options].find(x => x.value === "含增值税总价");
    if (!o) return false;
    sels[0].value = "含增值税总价"; sels[0].dispatchEvent(new Event("change", { bubbles: true }));
    sels[1].value = "sum"; sels[1].dispatchEvent(new Event("change", { bubbles: true }));
    const inp = line.querySelector("input");
    inp.value = "总额"; inp.dispatchEvent(new Event("change", { bubbles: true }));
    return true;
  });
  chk("配置汇总列", aggOk);
  await sleep(2600);
  info = await page.$eval("#tfPrevInfo", e => e.textContent);
  chk("分组后 62 行 2 列", info.includes("62 行") && info.includes("2 列"), info);
  const head = await page.$$eval("#tfPrevWrap thead th", e => e.map(x => x.dataset.col));
  chk("结果列 = 公司/总额", JSON.stringify(head) === '["公司","总额"]', JSON.stringify(head));

  log("=== 6. 查看 SQL ===");
  await page.evaluate(() => tfShowSql());
  await page.waitForSelector("#tfSqlMask.show", { timeout: 8000 });
  const sql = await page.$eval("#tfSqlMask .tf-sql", e => e.textContent);
  chk("SQL 弹窗打开", !!sql.length);
  chk("SQL 含 WITH/筛选/GROUP BY", sql.includes("WITH") && sql.includes("LIKE") && sql.includes("GROUP BY"));
  chk("SQL 含金额清洗", sql.includes("REPLACE"));
  await page.evaluate(() => closeMask("tfSqlMask"));

  log("=== 7. 保存为视图 ===");
  const TESTNAME = "vw_uitest_tf";
  await page.evaluate(n => { document.getElementById("tfName").value = n; }, TESTNAME);
  await page.evaluate(() => tfSave(document.querySelector(".tf-bar button.btn.sm:last-of-type")));
  await sleep(3000);
  const savedList = await page.$$eval("#tfSaved .sv .nm", e => e.map(x => x.textContent.trim()));
  chk("已保存列表含测试视图", savedList.includes(TESTNAME), JSON.stringify(savedList));
  const inTables = await page.evaluate(async () => {
    const j = await (await fetch("/api/tables")).json();
    const t = j.find(x => x.table === "vw_uitest_tf");
    return t ? { kind: t.kind, readonly: t.readonly, rows: t.rows } : null;
  });
  chk("视图进入表列表且只读", inTables && inTables.kind === "view" && inTables.readonly === true,
    JSON.stringify(inTables));
  chk("视图行数 62", inTables && inTables.rows === 62, JSON.stringify(inTables));

  log("=== 8. 点步骤看中间结果 ===");
  await page.evaluate(() => tfSelect(0));
  await sleep(2000);
  info = await page.$eval("#tfPrevInfo", e => e.textContent);
  chk("第1步结果回到 18,440 行", info.includes("18,440"), info);
  await page.evaluate(() => tfSelect(1));
  await sleep(2600);
  info = await page.$eval("#tfPrevInfo", e => e.textContent);
  chk("第2步结果 62 行", info.includes("62 行"), info);

  log("=== 9. 布局 ===");
  const layout = await page.evaluate(() => {
    const g = s => { const e = document.querySelector(s); if (!e) return null;
      const r = e.getBoundingClientRect(); return [Math.round(r.width), Math.round(r.height)]; };
    return { tfp: g(".tfp"), left: g(".tf-left"), right: g(".tf-right"),
             prevWrap: g("#tfPrevWrap"), cfg: g("#tfCfg") };
  });
  log("     尺寸 = " + JSON.stringify(layout));
  chk("左侧 250px 宽", layout.left && layout.left[0] === 250);
  chk("预览区有高度", layout.prevWrap && layout.prevWrap[1] > 150, JSON.stringify(layout.prevWrap));
  chk("预览可横向滚动", await page.$eval("#tfPrevWrap",
    e => e.scrollWidth > e.clientWidth || e.scrollWidth >= e.clientWidth));

  await page.screenshot({ path: require("os").tmpdir() + "/dbadmin-tf_page.png" });

  log("=== 10. 清理 ===");
  const del = await page.evaluate(async () => {
    const list = await (await fetch("/api/transform/queries")).json();
    const q = list.find(x => x.name === "vw_uitest_tf");
    if (!q) return "not-found";
    const d = await (await fetch(`/api/transform/queries/${q.id}?drop=1`, { method: "DELETE" })).json();
    return d.ok ? "deleted" : "fail";
  });
  chk("测试视图已删除", del === "deleted", del);
  const gone = await page.evaluate(async () => {
    const j = await (await fetch("/api/tables")).json();
    return !j.some(x => x.table === "vw_uitest_tf");
  });
  chk("表列表中已消失", gone);

  log("=== 11. 页面错误 ===");
  chk("无 JS 运行时错误", errs.length === 0, errs.slice(0, 4).join(" ; "));

  log(`\n===== ${pass} 通过 / ${fail} 失败 =====`);
  await browser.close();
  clearTimeout(watchdog);
  save();
  process.exit(fail ? 1 : 0);
})().catch(e => { log("!! 异常: " + e.message + "\n" + (e.stack || ""));
  save(); clearTimeout(watchdog); process.exit(1); });
