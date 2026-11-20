// 改类型报错提示验证：失败要红框给文案，且不能再弹「已改成功」的假提示
const puppeteer = require("C:/Users/CONG ZHU/.workbuddy/binaries/node/workspace/node_modules/puppeteer-core");
const fs = require("fs");
const OUT = "D:/code/采购数据库建设/tf_type_err_result.txt";
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

  const settle = async (ms) => {
    await sleep(ms === undefined ? 700 : ms);
    await page.waitForFunction(() => {
      const t = document.querySelector("#tfPrevInfo");
      return t && !t.textContent.includes("计算中");
    }, { timeout: 25000 });
    await sleep(150);
  };
  const errText = () => page.$eval("#tfPrevWrap", e => {
    const x = e.querySelector(".tf-err");
    return x ? x.textContent : "";
  });
  const toastText = () => page.$eval("#toast", e => e.textContent);
  const chipOf = col => page.evaluate(c => {
    const th = document.querySelector(`#tfPrevWrap th[data-col="${c}"]`);
    const t = th && th.querySelector(".tf-tchip");
    return t ? t.textContent.trim() : "";
  }, col);
  const clickTchip = col => page.evaluate(c => {
    const th = document.querySelector(`#tfPrevWrap th[data-col="${c}"]`);
    const el = th && th.querySelector(".tf-tchip");
    if (!el) return false;
    el.click(); return true;
  }, col);

  log("=== 1. 准备：进入转换页并选表 ===");
  await page.goto("http://127.0.0.1:5000/#transform", { waitUntil: "networkidle2" });
  await page.waitForSelector("#tfSource", { timeout: 20000 });
  await page.select("#tfSource", "fct_prpo");
  await settle(1200);
  chk("预览已出表", (await page.$$("#tfPrevWrap th[data-col]")).length > 20,
      String((await page.$$("#tfPrevWrap th[data-col]")).length));

  log("=== 2. 失败场景：列不存在 → 红框给后端原文 ===");
  await page.evaluate(async () => {
    tf.steps = [{ op: "change_type", column: "不存在的列", type: "date" }];
    tf.sel = 0; tf.err = "";
    document.getElementById("toast").textContent = "";
    tfRenderSteps(); tfRenderCfg();
    await tfPreview();
  });
  await settle(600);
  let e1 = await errText();
  chk("出现红框 .tf-err", e1.length > 0, "(无)");
  chk("文案含「列「不存在的列」在当前结果中不存在」",
      e1.includes("「不存在的列」在当前结果中不存在"), e1);
  chk("文案含步骤前缀「第 1 步（更改类型）」", e1.includes("第 1 步（更改类型）"), e1);
  chk("预览信息显示「有错误」", (await page.$eval("#tfPrevInfo", e => e.textContent)) === "有错误");
  chk("未弹假成功提示", !(await toastText()).includes("已将"), await toastText());

  log("=== 3. 失败场景：非法日期顺序（配置面板直传）===");
  await page.evaluate(async () => {
    tf.steps = [{ op: "change_type", column: "公司", type: "date", date_order: "ydm" }];
    tf.sel = 0; tf.err = "";
    tfRenderSteps(); tfRenderCfg();
    await tfPreview();
  });
  await settle(600);
  let e2 = await errText();
  chk("文案含「不支持的日期顺序：ydm」", e2.includes("不支持的日期顺序：ydm"), e2);

  log("=== 4. 成功场景：点表头改日期仍有 toast ===");
  await page.evaluate(async () => {
    tf.steps = []; tf.sel = -1; tf.err = "";
    tfRenderSteps(); tfRenderCfg();
    await tfPreview();
  });
  await settle(1200);
  chk("步骤已清空、红框消失", (await errText()) === "", await errText());
  chk("公司列初始标记 ABC", (await chipOf("公司")) === "ABC", await chipOf("公司"));

  await clickTchip("公司");
  await page.waitForSelector('.tf-pop .it[data-type="date"]', { timeout: 5000 });
  await page.click('.tf-pop .it[data-type="date"]');
  await sleep(1600);
  await settle(600);
  const t1 = await toastText();
  chk("成功 toast 出现「改为日期」", t1.includes("改为日期"), t1);
  chk("表头标记变 YMD", (await chipOf("公司")) === "YMD", await chipOf("公司"));

  log("=== 5. 还原为文本 ===");
  await clickTchip("公司");
  await page.waitForSelector('.tf-pop .it[data-type="text"]', { timeout: 5000 });
  await page.click('.tf-pop .it[data-type="text"]');
  await sleep(1600);
  await settle(600);
  chk("toast 含「改为文本」", (await toastText()).includes("改为文本"), await toastText());
  chk("表头标记回 ABC", (await chipOf("公司")) === "ABC", await chipOf("公司"));

  log("=== 6. 无 JS 报错 ===");
  // 故意制造的 400 会在控制台留下 "Failed to load resource" 噪音，属预期
  const realErrs = errs.filter(e => !/Failed to load resource/.test(e));
  chk("无真实 JS 报错（400 资源噪音已排除）", realErrs.length === 0,
      realErrs.join(" | ").slice(0, 300));

  log(`\n===== ${pass} 通过 / ${fail} 失败 =====`);
  save();
  clearTimeout(watchdog);
  await browser.close();
  process.exit(fail ? 1 : 0);
})().catch(e => { log("!! 异常: " + e.message); save(); process.exit(3); });
