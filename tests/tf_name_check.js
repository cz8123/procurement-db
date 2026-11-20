// 输出名称「自动派生」无头验证（跑在 5001 隔离库副本上，不碰真实库）
const puppeteer = require("C:/Users/CONG ZHU/.workbuddy/binaries/node/workspace/node_modules/puppeteer-core");
const fs = require("fs");
const OUT = "D:/code/采购数据库建设/tf_name_result.txt";
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
const clickSave = (pg) => pg.evaluate(() => {
  const b = [...document.querySelectorAll(".tf-bar button")].find(x => x.textContent.trim() === "保存");
  if (!b) return false;
  b.click(); return true;
});
const typeName = v => `(() => {
  const el = document.getElementById("tfName");
  el.value = ${JSON.stringify(v)};
  el.dispatchEvent(new Event("input", {bubbles:true}));
  return el.value;
})()`;
let PAGE = null;
const nameVal = () => PAGE.$eval("#tfName", e => e.value);
const placeholder = () => PAGE.$eval("#tfName", e => e.placeholder);

(async () => {
  const browser = await puppeteer.launch({
    executablePath: "C:/Program Files/Google/Chrome/Application/chrome.exe",
    headless: "new",
    args: ["--no-sandbox", "--disable-dev-shm-usage"],
    defaultViewport: { width: 1680, height: 1050 },
  });
  const page = await browser.newPage();
  PAGE = page;
  const errs = [];
  page.on("pageerror", e => errs.push("pageerror: " + e.message));
  page.on("console", m => { if (m.type() === "error") errs.push("console: " + m.text()); });
  page.on("dialog", d => d.accept());

  log("=== 1. 初始状态 ===");
  await page.goto("http://127.0.0.1:5001/#transform", { waitUntil: "networkidle2" });
  await page.waitForSelector("#tfName", { timeout: 20000 });
  chk("未选源表时名字为空", (await nameVal()) === "", await nameVal());
  chk("占位符提示留空自动命名", (await placeholder()).includes("自动"), await placeholder());

  log("=== 2. 选源表自动给名（vw_ 前缀） ===");
  await page.select("#tfSource", "fct_po");
  await sleep(1200);
  chk("fct_po → vw_po", (await nameVal()) === "vw_po", await nameVal());

  log("=== 3. 切输出模式换前缀 ===");
  await page.select("#tfMode", "table");
  await sleep(300);
  chk("切数据表 → tbl_po", (await nameVal()) === "tbl_po", await nameVal());
  await page.select("#tfMode", "view");
  await sleep(300);
  chk("切回视图 → vw_po", (await nameVal()) === "vw_po", await nameVal());

  log("=== 4. 手填名字不被自动覆盖 ===");
  await page.evaluate(typeName("my_custom_out"));
  await page.select("#tfMode", "table");
  await sleep(300);
  chk("手填后切模式保持 my_custom_out", (await nameVal()) === "my_custom_out", await nameVal());

  log("=== 5. 清空后重新自动 ===");
  await page.evaluate(typeName(""));
  await sleep(100);
  chk("清空后输入框为空", (await nameVal()) === "", await nameVal());
  await page.select("#tfMode", "view");
  await sleep(200);
  await page.select("#tfMode", "table");   // 触发 sync
  await sleep(300);
  chk("空名再切模式自动填回 tbl_po", (await nameVal()) === "tbl_po", await nameVal());

  log("=== 6. 留空直接保存（核心：不用起名） ===");
  await page.evaluate(typeName(""));
  await clickSave(page);
  await sleep(2500);
  const nm1 = await nameVal();
  chk("保存后自动名 tbl_po", nm1 === "tbl_po", nm1);
  chk("名字框已回填", nm1 !== "", nm1);
  const toastTxt = await page.$eval("#toast", e => e.textContent);
  chk("提示含「已保存并生成数据表」", toastTxt.includes("已保存") && toastTxt.includes("tbl_po"), toastTxt);
  const saved = await page.$$eval("#tfSaved .sv", e => e.map(x => x.textContent).join("|"));
  chk("已保存列表出现 tbl_po", saved.includes("tbl_po"), saved.slice(0, 120));

  log("=== 7. 同名再保存 = 覆盖（不新建） ===");
  await clickSave(page);
  await sleep(2500);
  chk("再次保存仍为 tbl_po", (await nameVal()) === "tbl_po", await nameVal());
  const cnt = await page.evaluate(() => (tf.list || []).filter(q => q.name === "tbl_po").length);
  chk("记录仍只有 1 条", cnt === 1, String(cnt));

  log("=== 8. 载入已保存转换 ===");
  await page.evaluate(() => tfOpen(tf.list.find(q => q.name === "tbl_po").id));
  await sleep(1200);
  chk("载入后名字 = tbl_po", (await nameVal()) === "tbl_po", await nameVal());
  chk("载入后模式 = table", (await page.$eval("#tfMode", e => e.value)) === "table");

  log("=== 9. 控制台无报错 ===");
  chk("无 pageerror / console error", errs.length === 0, errs.join(" | ").slice(0, 300));

  log("=== 10. 清理 ===");
  const okDel = await page.evaluate(async () => {
    const q = (tf.list || []).find(x => x.name === "tbl_po");
    if (!q) return "no-record";
    const r = await fetch(`/api/transform/queries/${q.id}?drop=1`, { method: "DELETE" });
    return r.status;
  });
  chk("删除测试产出", okDel === 200 || okDel === "no-record", String(okDel));

  log(`\n===== ${pass} 通过 / ${fail} 失败 =====`);
  save();
  clearTimeout(watchdog);
  await browser.close();
  process.exit(fail ? 1 : 0);
})().catch(e => { log("!! 异常: " + e.message); save(); process.exit(3); });
