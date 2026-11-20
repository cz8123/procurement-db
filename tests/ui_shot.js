/* 视觉快照：欢迎页 / 数据表 / 模型视图 / 映射聚合 / 数据转换
 * 跑法：node tests/ui_shot.js   （需 5000 已启动）
 */
const puppeteer = require("C:/Users/CONG ZHU/.workbuddy/binaries/node/workspace/node_modules/puppeteer-core");
const os = require("os");
const path = require("path");
const sleep = ms => new Promise(r => setTimeout(r, ms));
const BASE = process.env.BASE || "http://127.0.0.1:5000";
const OUT = process.env.OUT || os.tmpdir();

(async () => {
  const b = await puppeteer.launch({
    executablePath: "C:/Program Files/Google/Chrome/Application/chrome.exe",
    headless: "new", args: ["--no-sandbox"], defaultViewport: { width: 1680, height: 1000 }
  });
  const p = await b.newPage();

  await p.goto(`${BASE}/?t=${Date.now()}`, { waitUntil: "networkidle2" });
  await sleep(600);
  await p.screenshot({ path: path.join(OUT, "ui_1_welcome.png") });

  await p.goto(`${BASE}/?t=${Date.now()}#table/fct_prpo`, { waitUntil: "networkidle2" });
  await sleep(1600);
  await p.screenshot({ path: path.join(OUT, "ui_2_table.png") });

  await p.goto(`${BASE}/?t=${Date.now()}#model`, { waitUntil: "networkidle2" });
  await sleep(2000);
  await p.screenshot({ path: path.join(OUT, "ui_3_model.png") });

  await p.goto(`${BASE}/?t=${Date.now()}#agg`, { waitUntil: "networkidle2" });
  await sleep(1200);
  await p.screenshot({ path: path.join(OUT, "ui_4_agg.png") });

  await p.goto(`${BASE}/?t=${Date.now()}#transform`, { waitUntil: "networkidle2" });
  await sleep(1600);
  await p.screenshot({ path: path.join(OUT, "ui_5_transform.png") });

  // 弹窗：导入
  await p.evaluate(() => openImport());
  await sleep(500);
  await p.screenshot({ path: path.join(OUT, "ui_6_import.png") });

  console.log("shots ->", OUT);
  await b.close();
})();
