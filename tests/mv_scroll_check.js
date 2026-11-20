/* 模型视图滚动条回归：.mv-page 必须固定高度，让内部 .mv-scroll 成为真正的滚动容器
 * 背景：.mv-page 曾用 min-height，flex column 下 .mv-scroll{flex:1} 跟着内容撑高，
 *       scrollHeight==clientHeight → 滚动条消失且底部卡片被裁。
 * 跑法：服务已起（python app.py）时 node tests/mv_scroll_check.js
 */
const puppeteer = require("C:/Users/CONG ZHU/.workbuddy/binaries/node/workspace/node_modules/puppeteer-core");
const sleep = ms => new Promise(r => setTimeout(r, ms));
const BASE = process.env.MV_BASE || "http://127.0.0.1:5000";

let ok = 0, bad = 0;
function chk(name, cond, extra) {
  if (cond) { ok++; console.log("  [OK] " + name); }
  else { bad++; console.log("  [XX] " + name + (extra !== undefined ? " -> " + JSON.stringify(extra) : "")); }
}

(async () => {
  const b = await puppeteer.launch({
    executablePath: "C:/Program Files/Google/Chrome/Application/chrome.exe",
    headless: "new", args: ["--no-sandbox"], defaultViewport: { width: 1600, height: 900 }
  });
  const p = await b.newPage();
  const errs = [];
  p.on("pageerror", e => errs.push(e.message));

  // 强制带查询参数加载，保证 hash=#model 触发首屏渲染
  await p.goto(BASE + "/?t=" + Date.now() + "#model", { waitUntil: "networkidle0" });
  await sleep(2200);   // 等 /api/model-view 回来 + 多次 remeasure

  const m = await p.evaluate(() => {
    const cs = s => getComputedStyle(document.querySelector(s));
    const pg = document.querySelector(".mv-page").getBoundingClientRect();
    const sc = document.querySelector(".mv-scroll");
    const st = document.querySelector(".mv-stage").getBoundingClientRect();
    return {
      boxes: document.querySelectorAll(".mv-box").length,
      vh: innerHeight,
      pageH: +pg.height.toFixed(0), pageBottom: +pg.bottom.toFixed(0),
      pagePos: cs(".mv-page").position,
      pageHStyle: cs(".mv-page").height,
      scrollTopStyle: cs(".mv-page").overflow,
      ch: sc.clientHeight, sh: sc.scrollHeight,
      scrollOverflow: cs(".mv-scroll").overflowY,
      stageH: +st.height.toFixed(0)
    };
  });
  console.log("  ", JSON.stringify(m));

  chk("模型卡片已渲染", m.boxes > 0, m.boxes);
  chk(".mv-page 固定高度（非 min-height 撑开）",
    m.pageHStyle !== "auto" && m.pageH <= m.vh - 58 + 2, [m.pageHStyle, m.pageH, m.vh]);
  chk(".mv-page 不超出视口底部", m.pageBottom <= m.vh + 1, [m.pageBottom, m.vh]);
  chk(".mv-scroll 是滚动容器", m.scrollOverflow === "auto" || m.scrollOverflow === "scroll", m.scrollOverflow);
  chk("内容高于可视区 → 有滚动条", m.sh > m.ch + 2, [m.ch, m.sh]);

  // 真正滚一次，确认能到底
  const max = await p.evaluate(() => {
    const s = document.querySelector(".mv-scroll");
    s.scrollTop = 99999;
    return s.scrollTop;
  });
  chk("可滚动到底部", max > 0, max);

  // 视口变矮时仍不溢出
  await p.setViewport({ width: 1600, height: 700 });
  await sleep(600);
  const m2 = await p.evaluate(() => {
    const pg = document.querySelector(".mv-page").getBoundingClientRect();
    const sc = document.querySelector(".mv-scroll");
    return { pageBottom: +pg.bottom.toFixed(0), vh: innerHeight,
             canScroll: sc.scrollHeight > sc.clientHeight + 2 };
  });
  chk("视口变矮后仍不溢出", m2.pageBottom <= m2.vh + 1, m2);
  chk("视口变矮后仍可滚动", m2.canScroll, m2);

  chk("无 JS 报错", errs.length === 0, errs);

  console.log("结果：" + ok + " 通过 / " + bad + " 失败");
  await b.close();
  process.exit(bad ? 1 : 0);
})();
