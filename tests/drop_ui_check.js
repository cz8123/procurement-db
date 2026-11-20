/* 删除表 无头验证：按钮可用性 / 影响评估弹窗 / 表名回填守卫 / 删除后回欢迎页
 * 跑法：
 *   1) python tests/_agg_server.py        # 5001 + 库副本，不碰真实库
 *   2) 先建一张临时表：POST /api/import/new-table → dim_ui_drop
 *   3) node tests/drop_ui_check.js
 */
const puppeteer = require("C:/Users/CONG ZHU/.workbuddy/binaries/node/workspace/node_modules/puppeteer-core");
const os = require("os");
const path = require("path");
const sleep = ms => new Promise(r => setTimeout(r, ms));
const BASE = process.env.DROP_BASE || "http://127.0.0.1:5001";
const TMP = process.env.DROP_TABLE || "dim_ui_drop";
const VIEW = "vw_prpo";

let ok = 0, bad = 0;
function chk(name, cond, extra) {
  if (cond) { ok++; console.log("  [OK] " + name); }
  else { bad++; console.log("  [XX] " + name + (extra !== undefined ? " -> " + extra : "")); }
}

(async () => {
  const b = await puppeteer.launch({
    executablePath: "C:/Program Files/Google/Chrome/Application/chrome.exe",
    headless: "new", args: ["--no-sandbox"], defaultViewport: { width: 1600, height: 1000 }
  });
  const p = await b.newPage();
  const errs = [];
  p.on("pageerror", e => errs.push(e.message));
  p.on("console", m => { if (m.type() === "error") errs.push("console:" + m.text()); });
  p.on("dialog", async d => { await d.accept(); });

  const txt = sel => p.$eval(sel, e => e.innerText.replace(/\s+/g, " ")).catch(() => "");
  const waitFor = async (fn, ms = 20000, step = 120) => {
    const t0 = Date.now();
    while (Date.now() - t0 < ms) { if (await fn()) return true; await sleep(step); }
    return false;
  };
  const openTable = async t => {
    // 只改 hash 不会重载页面（state 不跟随），加查询参数强制重新加载
    await p.goto(BASE + "/?t=" + Date.now() + "#table/" + encodeURIComponent(t),
                 { waitUntil: "networkidle2" });
    await p.waitForSelector("#curTable", { timeout: 20000 });
    await waitFor(async () => (await txt("#curTable")).includes(t), 15000);
  };

  console.log("=== 1. 按钮可用性 ===");
  await openTable(VIEW);
  chk("视图上「删除表」禁用",
      await p.$eval("#dropBtn", e => e.disabled),
      await p.$eval("#dropBtn", e => e.title));
  chk("视图的提示指向数据转换",
      (await p.$eval("#dropBtn", e => e.title)).includes("数据转换"),
      await p.$eval("#dropBtn", e => e.title));

  await openTable(TMP);
  chk("物理表上「删除表」可用", !(await p.$eval("#dropBtn", e => e.disabled)));

  console.log("=== 2. 打开影响评估弹窗 ===");
  await p.click("#dropBtn");
  const shown = await waitFor(async () => (await txt("#dropBody")).includes(TMP), 15000);
  chk("弹窗显示表名", shown, (await txt("#dropBody")).slice(0, 120));
  const body = await txt("#dropBody");
  chk("弹窗给出中文名与行数", body.includes("UI删表测试") && body.includes("2 行"), body.slice(0, 160));
  chk("弹窗有备份勾选", !!(await p.$("#dropBackup")));
  chk("初始确认按钮禁用", await p.$eval("#dropOk", e => e.disabled));
  await p.screenshot({ path: path.join(os.tmpdir(), "drop_modal_light.png") });

  console.log("=== 3. 表名回填守卫 ===");
  await p.type("#dropConfirm", "dim_ui_drop_wrong");
  await sleep(120);
  chk("表名不一致时仍禁用", await p.$eval("#dropOk", e => e.disabled));
  await p.$eval("#dropConfirm", e => { e.value = ""; e.dispatchEvent(new Event("input", { bubbles: true })); });
  await p.type("#dropConfirm", TMP);
  await sleep(120);
  chk("表名一致后可点确认", !(await p.$eval("#dropOk", e => e.disabled)));

  console.log("=== 4. 执行删除 ===");
  await p.click("#dropOk");
  const closed = await waitFor(
    async () => !(await p.$eval("#dropMask", e => e.classList.contains("show"))), 20000);
  chk("弹窗已关闭", closed);
  chk("提示已删除", (await txt("#toast")).includes("已删除"), await txt("#toast"));
  const welcomed = await waitFor(
    async () => !!(await p.$("#grid .welcome, #grid .welcome")), 15000);
  chk("回到欢迎页", welcomed, (await txt("#grid")).slice(0, 80));
  chk("工具栏已隐藏",
      await p.$eval("#toolbar", e => getComputedStyle(e).display === "none"));
  chk("侧栏不再有该表",
      !(await p.$$eval(".titem .tname", els => els.map(e => e.title))).includes(TMP));

  const gone = await p.evaluate(async t => {
    const list = await fetch("/api/tables").then(r => r.json());
    return !list.some(x => x.table === t);
  }, TMP);
  chk("后端表清单里也已消失", gone);

  const info = await p.evaluate(async t => {
    const r = await fetch("/api/tables/" + encodeURIComponent(t) + "/drop-info");
    return await r.json();
  }, TMP);
  chk("再查影响评估提示表不存在",
      (info.blockers || []).some(x => x.includes("没有这张表")), JSON.stringify(info.blockers));

  console.log("=== 5. 刷新与暗色主题 ===");
  await p.reload({ waitUntil: "networkidle2" });
  await p.waitForSelector("#tableList", { timeout: 20000 });
  chk("刷新后侧栏正常渲染", (await p.$$eval(".titem", e => e.length)) > 3);
  await p.click("#themeToggle");
  await sleep(400);
  await openTable("fct_po");
  await p.click("#dropBtn");
  await waitFor(async () => (await txt("#dropBody")).includes("fct_po"), 15000);
  await p.screenshot({ path: path.join(os.tmpdir(), "drop_modal_dark.png") });
  const darkBg = await p.$eval("#dropBody .drop-hd", e => getComputedStyle(e).backgroundColor);
  chk("暗色下弹窗头部不是白底", darkBg !== "rgb(255, 255, 255)", darkBg);
  await p.evaluate(() => document.getElementById("dropMask").classList.remove("show"));
  chk("页面无 JS 异常", errs.length === 0, errs.slice(0, 3).join(" | "));

  await b.close();
  console.log(`\n结果：${ok} 通过 / ${bad} 失败`);
  process.exit(bad ? 1 : 0);
})();
