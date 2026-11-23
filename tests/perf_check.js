/**
 * 首屏性能与缓存契约检查（服务先起）
 *   NODE_PATH="<puppeteer-core 所在 node_modules>" node tests/perf_check.js
 *
 * 盯住三件事，防止以后改回去：
 *   1) 首屏只打 4 个 /api/board/* 请求（框架协议别再多打一次 size=1 的元数据请求）
 *   2) 同参数重复请求走内存缓存（< 120ms；没缓存时是 300–500ms）
 *   3) 缓存返回的内容与直算一致（summary.n == total，行里不带窗口列的 _ 前缀字段）
 */
const puppeteer = require('puppeteer-core');
const BASE = process.env.BOARD_BASE || 'http://127.0.0.1:5000';
const CHROME = process.env.CHROME_PATH || 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const sleep = ms => new Promise(r => setTimeout(r, ms));

let pass = 0, fail = 0;
function chk(name, ok, detail) {
  if (ok) { pass++; console.log('  OK  ' + name); }
  else { fail++; console.log('  XX  ' + name + (detail !== undefined ? '  ' + JSON.stringify(detail) : '')); }
}

(async () => {
  const browser = await puppeteer.launch({
    executablePath: CHROME, headless: 'new', args: ['--no-sandbox']
  });
  const page = await browser.newPage();
  await page.setViewport({ width: 1600, height: 1000 });
  try {
    await page.goto(BASE + '/dash?t=' + Date.now(), { waitUntil: 'domcontentloaded' });
    await page.waitForFunction(
      () => document.querySelectorAll('#chHeat .hcell').length > 0, { timeout: 15000 });
    const r = await page.evaluate(() => {
      const rs = performance.getEntriesByType('resource')
        .filter(x => /\/api\/board\//.test(x.name));
      return {
        n: rs.length,
        urls: rs.map(x => x.name.split('/api/board')[1].split('?')[0]),
        firstReady: Math.round(performance.now())
      };
    });
    console.log('    requests=' + JSON.stringify(r.urls) + ' ready@' + r.firstReady + 'ms');
    chk('首屏只打 4 个看板接口', r.n === 4, r.urls);
    chk('不再为 Type 选项单打一次 contracts',
      r.urls.filter(u => u.indexOf('/contracts') === 0).length === 1, r.urls);

    console.log('=== 2. 缓存命中 ===');
    const warm = await page.evaluate(async () => {
      const t = async (url) => {
        const t0 = performance.now();
        const res = await fetch(url);
        const j = await res.json();
        return { ms: +(performance.now() - t0).toFixed(1), j: j };
      };
      await t('/api/board/data?cur=RMB');          // 预热（首次可能 300ms+）
      const a = await t('/api/board/data?cur=RMB');
      const b = await t('/api/board/contracts?cur=RMB&page=1&size=50');
      const c = await t('/api/board/contracts?cur=RMB&page=1&size=50');
      return { data: a.ms, contr1: b.ms, contr2: c.ms, j: a.j, k: c.j };
    });
    console.log('    data ' + warm.data + 'ms · contracts ' + warm.contr1 + '→' + warm.contr2 + 'ms');
    chk('重复 /data 走缓存 (< 120ms)', warm.data < 120, warm.data);
    chk('重复 /contracts 走缓存 (< 120ms)', warm.contr2 < 120, warm.contr2);

    console.log('=== 3. 缓存内容与直算一致 ===');
    chk('/data 仍带 6 个 KPI', (warm.j.kpi || []).length >= 6, (warm.j.kpi || []).length);
    chk('/data 月度 12 个财月', (warm.j.monthly.labels || []).length === 12);
    chk('/contracts summary.n == total', warm.k.summary.n === warm.k.total,
      [warm.k.summary.n, warm.k.total]);
    chk('/contracts 行内不泄漏窗口列', warm.k.rows.every(x => Object.keys(x).every(k => k[0] !== '_')));
    chk('/contracts 仍带 types / bounds / supplier_en',
      (warm.k.types || []).length > 0 && !!warm.k.bounds.mn && !!warm.k.rows[0].supplier_en);

    console.log('\n' + (fail ? 'FAILED ' + fail + ' / ' : 'ALL PASS ') + pass);
  } catch (e) {
    fail++;
    console.log('  XX  ' + e.message);
  } finally {
    await browser.close();
    process.exit(fail ? 1 : 0);
  }
})();
