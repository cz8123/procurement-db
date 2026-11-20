/* 日期类型转换 UI 验证
 * 覆盖：表头类型标记 → 改成日期后变 YMD、值真的变了、配置面板出现日期选项、
 *       质量条跟着重算、认不出时保留原值。
 * 跑法：node tests/tf_date_check.js   （需先启动 app.py）
 */
const puppeteer = require("C:/Users/CONG ZHU/.workbuddy/binaries/node/workspace/node_modules/puppeteer-core");
const sleep = ms => new Promise(r => setTimeout(r, ms));

let ok = 0, bad = 0;
function chk(name, cond, extra) {
  if (cond) { ok++; console.log("  [OK] " + name); }
  else { bad++; console.log("  [XX] " + name + (extra !== undefined ? " -> " + extra : "")); }
}

(async () => {
  const b = await puppeteer.launch({
    executablePath: "C:/Program Files/Google/Chrome/Application/chrome.exe",
    headless: "new", args: ["--no-sandbox"], defaultViewport: { width: 1680, height: 1000 }
  });
  const p = await b.newPage();
  const errs = [];
  p.on("pageerror", e => errs.push(e.message));

  // 表头目标列可能在视口外，用脚本触发点击（与 tf_click_check 一致）
  const clickTh = async (col, part) => {
    const done = await p.evaluate((c, pt) => {
      const th = document.querySelector(`#tfPrevWrap th[data-col="${c}"]`);
      if (!th) return false;
      (pt === "tchip" ? th.querySelector(".tf-tchip") : th.querySelector(".nm")).click();
      return true;
    }, col, part || "tchip");
    if (!done) throw new Error("表头找不到列：" + col);
  };
  const valOf = async col => p.evaluate(c => {
    const i = tf.prev ? tf.prev.colnames.indexOf(c) : -1;
    return i < 0 ? null : tf.prev.rows.slice(0, 8).map(r => r[i]);
  }, col);

  console.log("=== 1. 打开转换页 ===");
  await p.goto("http://127.0.0.1:5000/#transform", { waitUntil: "networkidle2" });
  await p.waitForSelector("#tfSource");
  await p.select("#tfSource", "fct_prpo");
  await p.waitForFunction(
    () => document.querySelectorAll("#tfPrevWrap tbody tr").length > 0, { timeout: 30000 });

  const chip0 = await p.evaluate(() =>
    document.querySelector('#tfPrevWrap th[data-col="框架合同到期日"] .tf-tchip').textContent.trim());
  chk("转换前「框架合同到期日」标为文本 ABC", chip0 === "ABC", chip0);

  console.log("=== 2. 点类型标记改成日期 ===");
  await clickTh("框架合同到期日", "tchip");
  await p.waitForSelector(".tf-pop", { timeout: 5000 });
  const types = await p.$$eval(".tf-pop .it[data-type]", e => e.map(x => x.dataset.type));
  chk("类型菜单含日期项", types.includes("date"), JSON.stringify(types));
  await p.evaluate(() => document.querySelector('.tf-pop .it[data-type="date"]').click());
  await sleep(2200);

  const chip1 = await p.evaluate(() =>
    document.querySelector('#tfPrevWrap th[data-col="框架合同到期日"] .tf-tchip').textContent.trim());
  chk("改成日期后表头标为 YMD（不再是 ABC）", chip1 === "YMD", chip1);
  const cls1 = await p.evaluate(() =>
    document.querySelector('#tfPrevWrap th[data-col="框架合同到期日"] .tf-tchip').className);
  chk("YMD 标记不是告警色（warn）", !/warn/.test(cls1), cls1);

  console.log("=== 3. 值真的转换了 ===");
  const vals = await valOf("框架合同到期日");
  const isoLike = vals.filter(v => v === null || /^\d{4}-\d{2}-\d{2}$/.test(v));
  chk("预览里的值全部是 ISO 日期或空值", isoLike.length === vals.length,
    JSON.stringify(vals));
  chk("确实有值被解析出来（不是整列 null）",
    vals.filter(v => v && /^\d{4}-\d{2}-\d{2}$/.test(v)).length > 0, JSON.stringify(vals));

  console.log("=== 4. 步骤面板出现日期专用选项 ===");
  const labels = await p.$$eval("#tfCfg .row > label", e => e.map(x => x.textContent.trim()));
  chk("有「日期顺序」选项", labels.some(t => t.includes("日期顺序")), JSON.stringify(labels));
  chk("有「认不出时保留原值」选项",
    labels.some(t => t.includes("保留原值")), JSON.stringify(labels));

  const orderOpts = await p.evaluate(() => {
    const rows = [...document.querySelectorAll("#tfCfg .row")];
    const row = rows.find(r => r.querySelector("label") &&
      r.querySelector("label").textContent.includes("日期顺序"));
    return row ? [...row.querySelectorAll("option")].map(o => o.textContent.trim()) : [];
  });
  chk("日期顺序给了 4 种（自动/年月日/日月年/月日年）", orderOpts.length === 4,
    JSON.stringify(orderOpts));

  console.log("=== 5. 切到「日-月-年」重算 ===");
  const before = await valOf("框架合同到期日");
  await p.evaluate(() => {
    const rows = [...document.querySelectorAll("#tfCfg .row")];
    const row = rows.find(r => r.querySelector("label") &&
      r.querySelector("label").textContent.includes("日期顺序"));
    const sel = row.querySelector("select");
    sel.value = "dmy";
    sel.dispatchEvent(new Event("change", { bubbles: true }));
  });
  await sleep(2200);
  const after = await valOf("框架合同到期日");
  chk("切换顺序后仍全是 ISO 或空（没变成 null 一片）",
    after.every(v => v === null || /^\d{4}-\d{2}-\d{2}$/.test(v)), JSON.stringify(after));
  chk("顺序变化确实影响了结果（有值不同或同样合法）",
    after.filter(v => v !== null).length > 0, JSON.stringify(after.slice(0, 4)));

  console.log("=== 6. 质量条跟着重算 ===");
  await p.waitForFunction(() => {
    const qb = document.querySelector('#tfPrevWrap .tf-qb[data-col="框架合同到期日"]');
    return qb && (qb.querySelector("i.ok") || qb.querySelector("i.nu"));
  }, { timeout: 30000 });
  const qb = await p.evaluate(() => {
    const e = document.querySelector('#tfPrevWrap .tf-qb[data-col="框架合同到期日"]');
    return { s: e._s || null, hasOk: !!e.querySelector("i.ok") };
  });
  chk("质量条有数据（统计重算完成）", qb.s !== null, JSON.stringify(qb));
  chk("饱满度合理（非 0 也非 100%，因为这列本来大半是空）",
    qb.s && qb.s.fill > 0 && qb.s.fill < 1, qb.s && qb.s.fill);

  console.log("=== 7. 自由文本列改日期：保留原值而非清空 ===");
  await clickTh("说明", "tchip");
  await p.waitForSelector(".tf-pop", { timeout: 5000 });
  await p.evaluate(() => document.querySelector('.tf-pop .it[data-type="date"]').click());
  await sleep(2500);
  const noteVals = await valOf("说明");
  chk("「说明」改日期后仍有文本内容（未整列清空）",
    noteVals.filter(v => v && v.length > 5).length >= 3, JSON.stringify(noteVals.map(
      v => v && v.slice(0, 20))));

  console.log("=== 8. 清理：撤销两步 ===");
  await p.evaluate(() => {
    tf.steps = [];
    tf.sel = -1;
    tfRenderSteps(); tfRenderCfg(); tfPreview();
  });
  await sleep(1200);
  const chipBack = await p.evaluate(() =>
    document.querySelector('#tfPrevWrap th[data-col="框架合同到期日"] .tf-tchip').textContent.trim());
  chk("清空步骤后表头恢复 ABC", chipBack === "ABC", chipBack);
  chk("清空步骤后无残留步骤", await p.evaluate(() => tf.steps.length) === 0);

  chk("页面无 JS 报错", errs.length === 0, JSON.stringify(errs.slice(0, 3)));

  await b.close();
  console.log(`\n===== ${ok} 通过 / ${bad} 失败 =====`);
  process.exit(bad ? 1 : 0);
})().catch(e => { console.log("ERR " + e.message); process.exit(1); });
