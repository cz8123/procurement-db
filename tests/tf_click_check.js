// 「点表头直接改造」无头验证：改类型 / 排序 / 删列 / 筛选选值 / 重命名 / 分组
// 关键点：验证「智能合并」——连续同类操作不会刷出一堆步骤
const puppeteer = require("C:/Users/CONG ZHU/.workbuddy/binaries/node/workspace/node_modules/puppeteer-core");
const fs = require("fs");
const OUT = "D:/code/采购数据库建设/tf_click_result.txt";
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

  // 等预览算完（tfPreview 会先写「计算中…」）
  async function settle(ms) {
    await sleep(ms === undefined ? 650 : ms);
    await page.waitForFunction(() => {
      const t = document.querySelector("#tfPrevInfo");
      return t && !t.textContent.includes("计算中");
    }, { timeout: 25000 });
    await sleep(120);
  }
  const steps = () => page.evaluate(() => JSON.parse(JSON.stringify(tf.steps)));
  const stepN = () => page.evaluate(() => tf.steps.length);
  const colN = () => page.evaluate(() => tf.prev ? tf.prev.colnames.length : -1);
  const info = () => page.$eval("#tfPrevInfo", e => e.textContent);
  const rowN = async () => {
    const t = await info();
    const m = t.match(/([\d,]+) 行/);
    return m ? parseInt(m[1].replace(/,/g, ""), 10) : -1;
  };
  const clickMenu = async act => {
    await page.waitForSelector(".tf-pop", { timeout: 5000 });
    await page.click(`.tf-pop .it[data-act="${act}"]`);
  };
  // 35 列的表横向很宽，目标列常在视口外；脚本触发点击避免坐标问题
  const clickCol = async (col, part) => {
    const ok = await page.evaluate((c, pt) => {
    const th = document.querySelector(`#tfPrevWrap th[data-col="${c}"]`);
    if (!th) return false;
    const el = (pt && String(pt).indexOf("tchip") >= 0)
      ? th.querySelector(".tf-tchip") : th.querySelector(".nm");
    if (!el) return false;
      el.click();
      return true;
    }, col, part || "nm");
    if (!ok) throw new Error("表头找不到列：" + col);
  };

  log("=== 1. 加载并选源表 ===");
  await page.goto("http://127.0.0.1:5000/#transform", { waitUntil: "networkidle2" });
  await page.waitForSelector("#tfSource", { timeout: 15000 });
  await page.select("#tfSource", "fct_prpo");
  await page.waitForFunction(() => {
    const t = document.querySelector("#tfPrevWrap table");
    return t && t.querySelectorAll("tbody tr").length > 0;
  }, { timeout: 25000 });
  chk("源表载入 44,987 行", (await info()).includes("44,987"), await info());

  log("=== 2. 表头已变成操作入口 ===");
  const thN = await page.$$eval("#tfPrevWrap thead th", e => e.length);
  chk("表头 35 列", thN === 35, String(thN));
  const chipN = await page.$$eval("#tfPrevWrap thead th .tf-tchip", e => e.length);
  chk("每列都有类型标记", chipN === thN, `${chipN}/${thN}`);
  const moneyChip = await page.$eval('#tfPrevWrap th[data-col="含增值税总价"] .tf-tchip',
    e => ({ cls: e.className, txt: e.textContent }));
  chk("金额列标黄提示「实存文本」", moneyChip.cls.includes("warn") && moneyChip.txt === "ABC",
    JSON.stringify(moneyChip));
  // 说明性提示条已从界面移除（2026-11-18 视觉精简），不再断言其内容

  log("=== 3. 字段列表 + 真实鼠标点击表头 ===");
  const fN = await page.$$eval("#tfCfg .tf-frow", e => e.length);
  chk("字段列表 35 个", fN === 35, String(fN));
  await page.type("#tfFieldQ", "总价");
  const visN = await page.$$eval("#tfCfg .tf-frow",
    els => els.filter(e => e.style.display !== "none").length);
  chk("搜「总价」命中 1 个字段", visN === 1, String(visN));
  await page.evaluate(() => {
    const i = document.querySelector("#tfFieldQ");
    i.value = ""; i.dispatchEvent(new Event("input", { bubbles: true }));
  });
  // 第一列在视口内，用真实鼠标点击验证命中（不是只有脚本能点开）
  await clickCol("表单编号", "nm");
  await page.waitForSelector(".tf-pop", { timeout: 5000 });
  chk("真实鼠标点表头弹出列菜单", (await page.$$eval(".tf-pop .it[data-act]", e => e.length)) >= 6);
  await page.keyboard.press("Escape");
  await sleep(200);
  chk("Esc 关掉菜单", (await page.$$eval(".tf-pop", e => e.length)) === 0);

  log("=== 4. 点类型标记改数据类型 ===");
  await clickCol("含增值税总价", "tchip");
  await page.waitForSelector(".tf-pop", { timeout: 5000 });
  const typeItems = await page.$$eval(".tf-pop .it[data-type]", e => e.length);
  chk("类型菜单 4 项", typeItems === 4, String(typeItems));
  const curOn = await page.$eval(".tf-pop .it[data-type].cur", e => e.dataset.type);
  chk("当前类型标为「文本」（因实存文本）", curOn === "text", curOn);
  await page.click('.tf-pop .it[data-type="number"]');
  await settle();
  let st = await steps();
  chk("生成 1 个步骤", st.length === 1, JSON.stringify(st.map(s => s.op)));
  chk("步骤 = 更改类型 含增值税总价 → number",
    st[0].op === "change_type" && st[0].column === "含增值税总价" && st[0].type === "number",
    JSON.stringify(st[0]));
  const chip2 = await page.$eval('#tfPrevWrap th[data-col="含增值税总价"] .tf-tchip',
    e => ({ cls: e.className, txt: e.textContent }));
  chk("标记变成 1.2 且不再标黄", chip2.txt === "1.2" && !chip2.cls.includes("warn"),
    JSON.stringify(chip2));

  log("=== 5. 智能合并：同列再改类型不新增步骤 ===");
  await clickCol("含增值税总价", "tf-tchip");
  await page.click('.tf-pop .it[data-type="text"]');
  await settle();
  st = await steps();
  chk("步骤数仍为 1（合并）", st.length === 1, String(st.length));
  chk("类型被覆盖为 text", st[0].type === "text", st[0].type);

  log("=== 6. 重命名（插入到当前步骤之后） ===");
  await clickCol("表单编号", "nm");
  await clickMenu("rename");
  await page.waitForSelector(".tf-ppop input", { timeout: 5000 });
  await page.evaluate(() => {
    const i = document.querySelector(".tf-ppop input");
    i.value = "单据号"; i.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await page.click('.tf-ppop .btn[data-x="1"]');
  await settle();
  st = await steps();
  const heads1 = await page.$$eval("#tfPrevWrap thead th", e => e.map(x => x.dataset.col));
  chk("重命名步骤插在第 2 步", st.length === 2 && st[1].op === "rename_column",
    JSON.stringify(st.map(s => s.op)));
  chk("表头已显示新列名 单据号", heads1.includes("单据号") && !heads1.includes("表单编号"));

  log("=== 7. 删列 + 合并删列 ===");
  await clickCol("支出类型", "nm");
  await clickMenu("remove");
  await settle();
  const c1 = await colN();
  chk("删列后剩 34 列", c1 === 34, String(c1));
  chk("步骤数 3", (await stepN()) === 3, String(await stepN()));

  await clickCol("说明", "nm");
  await clickMenu("remove");
  await settle();
  st = await steps();
  chk("连续删列合并为同一步（步骤数仍 3）", st.length === 3, String(st.length));
  chk("该步记录了 2 个列", (st[2].columns || []).length === 2, JSON.stringify(st[2].columns));
  chk("删列后剩 33 列", (await colN()) === 33, String(await colN()));

  log("=== 8. 点表头筛选：值面板 ===");
  await clickCol("供应商属性", "nm");
  await clickMenu("filter");
  await page.waitForSelector(".tf-vpop", { timeout: 15000 });
  const vrows = await page.$$eval(".tf-vpop .lst .vrow", e => e.length);
  chk("值面板列出真实取值", vrows > 5, String(vrows));
  const vhd = await page.$eval(".tf-vpop .hd", e => e.textContent);
  chk("面板显示 36 种取值", vhd.includes("36"), vhd);
  const lastV = await page.$$eval(".tf-vpop .lst .vrow", els => {
    const r = els[els.length - 1];
    return r ? r.dataset.null : "";
  });
  chk("空值排在最后", lastV === "1", lastV);

  await page.click(".tf-vpop .lst .vrow:nth-child(1)");
  await page.click(".tf-vpop .lst .vrow:nth-child(2)");
  const selN = await page.$$eval(".tf-vpop .lst .vrow input:checked", e => e.length);
  chk("勾选 2 个值", selN === 2, String(selN));
  await page.click('.tf-vpop .ft .btn[data-x="keep"]');
  await settle(900);
  st = await steps();
  chk("生成筛选步骤", st.length === 4 && st[3].op === "filter",
    JSON.stringify(st.map(s => s.op)));
  chk("筛选方式 = 包含于(in)", st[3].cmp === "in", st[3].cmp);
  chk("携带 2 个所选值", (st[3].value || []).length === 2, JSON.stringify(st[3].value));
  const rowsAfter = await rowN();
  chk("筛选后行数显著减少", rowsAfter > 0 && rowsAfter < 38000, String(rowsAfter));

  log("=== 9. 点表头排序 + 合并 ===");
  await clickCol("公司", "nm");
  await clickMenu("sort_asc");
  await settle();
  st = await steps();
  chk("生成排序步骤（升序）", st.length === 5 && st[4].op === "sort" && st[4].items[0].desc === false,
    JSON.stringify(st[4] || {}));
  await clickCol("公司", "nm");
  await clickMenu("sort_desc");
  await settle();
  st = await steps();
  chk("再次排序合并，不新增步骤", st.length === 5, String(st.length));
  chk("排序方向被覆盖为降序", st[4].items[0].desc === true, JSON.stringify(st[4].items));

  log("=== 10. 点表头分组计数 ===");
  await clickCol("公司", "nm");
  await clickMenu("group_count");
  await settle(900);
  st = await steps();
  chk("生成分组步骤", st.length === 6 && st[5].op === "group_by",
    JSON.stringify(st.map(s => s.op)));
  chk("分组键 = 公司", JSON.stringify(st[5].keys) === '["公司"]', JSON.stringify(st[5].keys));
  chk("汇总方式 = 计数(行数)", st[5].aggs[0].func === "count_all", JSON.stringify(st[5].aggs));
  chk("分组后只剩 2 列", (await colN()) === 2, String(await colN()));

  log("=== 11. 清理与错误检查 ===");
  await page.screenshot({ path: require("os").tmpdir() + "/dbadmin-tf_click.png" });
  await page.evaluate(() => tfNew());
  await sleep(600);
  chk("重置后步骤清空", (await stepN()) === 0, String(await stepN()));
  chk("无 JS 运行时错误", errs.length === 0, errs.slice(0, 4).join(" ; "));

  log(`\n===== ${pass} 通过 / ${fail} 失败 =====`);
  await browser.close();
  clearTimeout(watchdog);
  save();
  process.exit(fail ? 1 : 0);
})().catch(e => { log("!! 异常: " + e.message + "\n" + (e.stack || ""));
  save(); clearTimeout(watchdog); process.exit(1); });
