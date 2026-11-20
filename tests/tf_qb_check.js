/* 列质量条（表头「饱满度」色条）无头验证
 * 跑法：node tests/tf_qb_check.js   （需先启动 app.py）
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

  console.log("=== 1. 打开转换页，选源表 ===");
  await p.goto("http://127.0.0.1:5000/#transform", { waitUntil: "networkidle2" });
  await p.waitForSelector("#tfSource");
  // 记录首帧是否 pending（统计应先转圈再上色）
  await p.select("#tfSource", "fct_prpo");
  await p.waitForFunction(
    () => document.querySelectorAll("#tfPrevWrap tbody tr").length > 0, { timeout: 30000 });
  const hadPending = await p.evaluate(
    () => [...document.querySelectorAll("#tfPrevWrap .tf-qb")].some(e => e.classList.contains("pending")));
  chk("预览先出、质量条先是 pending 状态（异步统计不阻塞表格）", hadPending === true, String(hadPending));

  await p.waitForFunction(() => {
    const q = [...document.querySelectorAll("#tfPrevWrap .tf-qb")];
    return q.length > 0 && q.some(e => e.querySelector("i.ok"));
  }, { timeout: 30000 });
  console.log("  （质量条已上色）");

  console.log("=== 2. 每列都有质量条，且宽度与统计一致 ===");
  const nQB = await p.$$eval("#tfPrevWrap .tf-qb", e => e.length);
  const nTh = await p.$$eval("#tfPrevWrap thead th", e => e.length);
  chk("质量条数量 = 表头列数", nQB === nTh && nQB === 35, `${nQB} / ${nTh}`);

  const widths = await p.evaluate(() =>
    [...document.querySelectorAll("#tfPrevWrap .tf-qb")].map(e => {
      const i = [...e.children];
      const okEl = i.find(x => x.classList.contains("ok"));
      const nuEl = i.find(x => x.classList.contains("nu"));
      return {
        col: e.dataset.col,
        okPct: okEl ? parseFloat(okEl.style.width) : 0,
        nuPct: nuEl ? parseFloat(nuEl.style.width) : 0,
        hasOk: !!okEl, fill: e._s ? e._s.fill : null,
        pending: e.classList.contains("pending"), nodata: e.classList.contains("nodata")
      };
    }));
  chk("没有残留 pending", widths.every(w => !w.pending));
  chk("没有残留 nodata（拿到数据要摘掉置灰标记）", widths.every(w => !w.nodata));
  chk("每段宽度相加 = 100%（无溢出/缺口）",
    widths.every(w => Math.abs(w.okPct + w.nuPct - 100) < 0.6),
    widths.filter(w => Math.abs(w.okPct + w.nuPct - 100) >= 0.6).map(w => w.col + ":" + (w.okPct + w.nuPct)));
  chk("绿段宽度 = 统计饱满度（±0.5%）",
    widths.every(w => w.fill === null || Math.abs(w.okPct - w.fill * 100) < 0.5),
    widths.filter(w => w.fill !== null && Math.abs(w.okPct - w.fill * 100) >= 0.5)
      .map(w => `${w.col} 画${w.okPct} 实${(w.fill * 100).toFixed(1)}`));

  const sub = widths.find(w => w.col === "支出子类");
  chk("支出子类 绿 43.8% / 灰 56.2%（有缺口的列出现双段）",
    Math.abs(sub.okPct - 43.8) < 0.5 && Math.abs(sub.nuPct - 56.2) < 0.5,
    `${sub.okPct} / ${sub.nuPct}`);

  const capex = widths.find(w => w.col === "CAPEX 项目批准参考号");
  chk("CAPEX 项目批准参考号 绿段极窄（0.14%）",
    Math.abs(capex.okPct - 0.14) < 0.05, capex.okPct);

  const full = widths.find(w => w.col === "选择采购方法");
  chk("全满列只有绿段、没有灰段",
    Math.abs(full.okPct - 100) < 0.01 && full.nuPct === 0, `${full.okPct}/${full.nuPct}`);

  const dim = widths.find(w => w.col === "您是否有已获批准的市场计划？");
  const dimS = await p.evaluate(() => {
    const qb = [...document.querySelectorAll("#tfPrevWrap .tf-qb")]
      .find(e => e.dataset.col === "您是否有已获批准的市场计划？");
    return !!(qb && qb.querySelector("i.ok.dim"));
  });
  chk("单值列绿条调暗（整列 1 个取值，满但没信息量）", dimS === true, String(dimS));

  console.log("=== 3. 悬停质量条显示列质量浮层 ===");
  await p.evaluate(() => {
    const qb = [...document.querySelectorAll("#tfPrevWrap .tf-qb")].find(e => e.dataset.col === "支出子类");
    qb.scrollIntoView({ inline: "center", block: "nearest" });
  });
  await sleep(400);
  const box = await p.evaluate(() => {
    const qb = [...document.querySelectorAll("#tfPrevWrap .tf-qb")].find(e => e.dataset.col === "支出子类");
    const r = qb.getBoundingClientRect();
    return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
  });
  await p.mouse.move(box.x, box.y);
  await sleep(400);
  const pop = await p.evaluate(() => {
    const e = document.querySelector(".tf-qpop");
    return e ? e.textContent.replace(/\s+/g, " ").trim() : "";
  });
  chk("浮层出现", pop.length > 0, pop);
  chk("浮层标出列名", pop.includes("列质量") && pop.includes("支出子类"), pop);
  chk("浮层给出有效值 + 空值 + 百分比",
    pop.includes("19,705") && pop.includes("25,282") && pop.includes("43.8%") && pop.includes("56.2%"), pop);
  chk("浮层给出总行数", pop.includes("共 44,987 行"), pop);
  chk("浮层不硬塞去重数（避免为它多付 0.86s 统计）", !pop.includes("去重取值"), pop);
  chk("浮层提示可点击看分布", pop.includes("取值分布"));

  const popPos = await p.evaluate(() => {
    const e = document.querySelector(".tf-qpop");
    const r = e.getBoundingClientRect();
    return { insideW: r.left >= 0 && r.right <= innerWidth, insideH: r.top >= 0 && r.bottom <= innerHeight };
  });
  chk("浮层不越出视口", popPos.insideW && popPos.insideH, JSON.stringify(popPos));

  // 移开鼠标应自动消失（pointer-events:none，不会卡住）
  await p.mouse.move(box.x, box.y + 220);
  await sleep(300);
  chk("鼠标移开后浮层消失", (await p.$$eval(".tf-qpop", e => e.length)) === 0);

  console.log("=== 4. 点质量条 = 看取值分布，且不弹列菜单 ===");
  await p.mouse.click(box.x, box.y);
  await sleep(900);
  const vpop = await p.evaluate(() => {
    const e = document.querySelector(".tf-vpop");
    return e ? e.textContent.replace(/\s+/g, " ").trim() : "";
  });
  chk("打开值面板", vpop.length > 0, vpop.slice(0, 60));
  chk("值面板是这个列", vpop.includes("支出子类"), vpop.slice(0, 60));
  chk("没有误弹列操作菜单", (await p.$$eval(".tf-pop", e => e.length)) === 0);
  await p.keyboard.press("Escape");
  await sleep(300);

  console.log("=== 5. 统计跟着步骤链重算（不是永远读源表） ===");
  await p.evaluate(() => {
    tf.steps = [{ op: "filter", column: "支出子类", cmp: "not_empty" }];
    tf.sel = 0;
    return tfPreview();
  });
  await p.waitForFunction(() => tf.prev && tf.prev.total < 44987, { timeout: 30000 });
  await p.waitForFunction(() => {
    const q = [...document.querySelectorAll("#tfPrevWrap .tf-qb")];
    return q.length > 0 && q.some(e => e.querySelector("i.ok"));
  }, { timeout: 30000 });
  const after = await p.evaluate(() => {
    const qb = [...document.querySelectorAll("#tfPrevWrap .tf-qb")].find(e => e.dataset.col === "支出子类");
    const okEl = qb.querySelector("i.ok");
    return { okPct: okEl ? parseFloat(okEl.style.width) : 0, fill: qb._s && qb._s.fill,
             total: tf.prev.total, nu: qb.querySelector("i.nu") ? 1 : 0,
             const: qb._s && qb._s.const };
  });
  chk("筛掉空值后行数变少", after.total > 0 && after.total < 44987, after.total);
  chk("该列饱满度变 100%（统计重算了）",
    Math.abs(after.okPct - 100) < 0.01 && after.fill === 1.0, `${after.okPct} / ${after.fill}`);
  chk("无灰段", after.nu === 0);
  chk("该列取值 2 个，不该被判成单值", after.const === false, after.const);

  console.log("=== 6. 全空列不画绿段 ===");
  await p.evaluate(() => {
    tf.steps = [{ op: "filter", column: "支出子类", cmp: "is_empty" }];
    tf.sel = 0;
    return tfPreview();
  });
  await p.waitForFunction(() => tf.prev && tf.prev.total > 0, { timeout: 30000 });
  await p.waitForFunction(() => {
    const qb = [...document.querySelectorAll("#tfPrevWrap .tf-qb")].find(e => e.dataset.col === "支出子类");
    return qb && (qb.querySelector("i.nu") || qb.classList.contains("nodata"));
  }, { timeout: 30000 });
  const empty = await p.evaluate(() => {
    const qb = [...document.querySelectorAll("#tfPrevWrap .tf-qb")].find(e => e.dataset.col === "支出子类");
    return { hasOk: !!qb.querySelector("i.ok"), nuPct: parseFloat((qb.querySelector("i.nu") || {}).style?.width || 0),
             fill: qb._s && qb._s.fill };
  });
  chk("全空列没有绿段", empty.hasOk === false);
  chk("全空列灰段铺满", Math.abs(empty.nuPct - 100) < 0.01, empty.nuPct);
  chk("全空列 fill = 0", empty.fill === 0, empty.fill);

  console.log("=== 7. 无脚本异常 ===");
  chk("页面无 JS 报错", errs.length === 0, errs.join(" | "));

  // 收尾：清掉步骤，别留下残留状态
  await p.evaluate(() => { tf.steps = []; tf.sel = -1; });

  await b.close();
  console.log("");
  console.log(`通过 ${ok} / 失败 ${bad}`);
  process.exit(bad ? 1 : 0);
})().catch(e => { console.log("ERR", e.message); process.exit(1); });
