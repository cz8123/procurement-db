// 新增行自增主键验证：维度表（INTEGER PRIMARY KEY AUTOINCREMENT）新增行不应要求手填 ID
const puppeteer = require("C:/Users/CONG ZHU/.workbuddy/binaries/node/workspace/node_modules/puppeteer-core");
const fs = require("fs");
const OUT = "D:/code/采购数据库建设/auto_id_result.txt";
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
const TESTVAL = "__UI_AUTOID__";

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

  log("=== 1. 打开公司映射维度表 ===");
  await page.goto("http://127.0.0.1:5000/", { waitUntil: "networkidle2" });
  await page.waitForSelector("#grid", { timeout: 20000 });
  await page.evaluate(t => selectTable(t), "dim_company_map");
  await page.waitForFunction(() => {
    const t = document.querySelector("#grid table");
    return t && t.querySelectorAll("tbody tr").length > 0;
  }, { timeout: 30000 });
  chk("表格已渲染", (await page.$$("#grid tbody tr")).length > 0);

  log("=== 2. 点新增一行 → 行内新行（不弹窗）===");
  await page.click("#addBtn");
  await page.waitForFunction(() => !!document.getElementById("rowNew"), { timeout: 5000 });
  chk("出现行内新增行", true);
  chk("未弹窗", !(await page.$eval("#editMask", e => e.classList.contains("show"))));

  const idState = await page.$eval('#rowNew input[data-col="id"]', e => ({
    disabled: e.disabled, value: e.value, ph: e.placeholder,
  }));
  chk("行内新行 id 已禁用（不可手填）", idState.disabled === true, JSON.stringify(idState));
  chk("id 初始为空", idState.value === "", idState.value);
  chk("占位提示为自动生成", /自动/.test(idState.ph), idState.ph);
  const cInp = await page.$eval('#rowNew input[data-col="Company"]', e => e.disabled);
  chk("普通字段仍可编辑", cInp === false);

  log("=== 3. 展开弹窗仍可用且不虚化 ===");
  await page.click("#rowNew .act-expand-new");
  await page.waitForFunction(() => document.getElementById("editMask").classList.contains("show"), { timeout: 5000 });
  chk("展开编辑弹窗已打开", true);
  chk("标题为新增一行", /新增一行/.test(await page.$eval("#editTitle", e => e.textContent)));
  const bf = await page.$eval("#editMask", e => {
    const cs = getComputedStyle(e); return cs.backdropFilter || cs.webkitBackdropFilter || "none";
  });
  chk("弹窗背景不虚化", bf === "none" || bf === "", bf);
  const idInp2 = await page.$eval('#editBody input[data-col="id"]', e => e.disabled);
  chk("弹窗内 id 同样禁用", idInp2 === true);
  const tag = await page.$eval('#editBody .field.auto label', e =>
    getComputedStyle(e, "::after").content);
  chk("标签角标显示「自增」", /自增/.test(tag), tag);
  const tip = await page.$eval("#editBody .form-tip", e => e.textContent.trim()).catch(() => "");
  chk("有自动分配说明条", /自动分配/.test(tip), tip);

  log("=== 4. 截图（亮色） ===");
  await page.screenshot({ path: require("os").tmpdir() + "/dbadmin-新增行-自增ID.png" });
  await page.evaluate(() => document.documentElement.setAttribute("data-theme", "dark"));
  await sleep(300);
  await page.screenshot({ path: require("os").tmpdir() + "/dbadmin-新增行-自增ID-暗色.png" });
  await page.evaluate(() => {
    const t = localStorage.getItem("dbadmin-theme");
    document.documentElement.setAttribute("data-theme", t === "dark" ? "dark" : "light");
  });

  log("=== 5. 填公司名并保存 ===");
  await page.type('#editBody input[data-col="Company"]', TESTVAL);
  const btns = await page.$$("#editMask .foot .btn");
  await btns[btns.length - 1].click();          // 最后一个 = 保存
  await page.waitForFunction(() => {
    const t = document.getElementById("toast");
    return t && t.classList.contains("show") && /新增/.test(t.textContent);
  }, { timeout: 10000 });
  const toastTxt = await page.$eval("#toast", e => e.textContent.trim());
  log("  toast: " + toastTxt);
  const toastId = (toastTxt.match(/ID\s*(\d+)/) || [])[1];
  chk("新增提示带出自动 ID", !!toastId && Number(toastId) > 62, toastTxt);
  chk("弹窗已关闭", !(await page.$eval("#editMask", e => e.classList.contains("show"))));

  log("=== 6. 数据落库校验 ===");
  const found = await page.evaluate(async v => {
    const r = await fetch("/api/tables/dim_company_map/rows?size=5&search=" + encodeURIComponent(v));
    const d = await r.json();
    return d.rows || [];
  }, TESTVAL);
  chk("新行已写入且 id 与 toast 一致", found.length === 1 && String(found[0].id) === String(toastId),
      JSON.stringify(found.map(r => ({ id: r.id, c: r.Company }))));

  log("=== 7. 清理测试数据 ===");
  const del = await page.evaluate(async id => {
    const r = await fetch("/api/tables/dim_company_map/rows/" + id, { method: "DELETE" });
    return r.json();
  }, toastId);
  chk("测试行已删除", del.ok === true, JSON.stringify(del));

  log("=== 7. 无 JS 报错 ===");
  chk("无脚本异常", errs.length === 0, errs.slice(0, 3).join(" | "));

  log(`\n结果：${pass} 通过 / ${fail} 失败`);
  save();
  clearTimeout(watchdog);
  await browser.close();
  process.exit(fail ? 1 : 0);
})().catch(e => { log("!! 异常: " + e.message + "\n" + e.stack); save(); process.exit(3); });
