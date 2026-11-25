/**
 * Geo 页专项检查：地图气泡容器、四指标下拉、Zebra BI 式 Category 同比、
 * 公司勾选联动、刷新保层级。在线与离线（SNAP_FILE）同一套断言。
 *   NODE_PATH="<puppeteer-core 所在 node_modules>" node tests/geo_check.js
 *   SNAP_FILE=/path/to/snapshot.html node tests/geo_check.js   # 离线模式
 */
const puppeteer = require('puppeteer-core');
const fs = require('fs');
const path = require('path');
const BASE = process.env.BOARD_BASE || 'http://127.0.0.1:5000';
const CHROME = process.env.CHROME_PATH || 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const SNAP = process.env.SNAP_FILE || null;
const sleep = ms => new Promise(r => setTimeout(r, ms));

let pass = 0, fail = 0;
function chk(name, ok, detail) {
  if (ok) { pass++; console.log('  OK  ' + name); }
  else { fail++; console.log('  XX  ' + name + (detail !== undefined ? '  ' + JSON.stringify(detail) : '')); }
}
async function waitFor(page, fn, timeout, label) {
  const t0 = Date.now();
  while (Date.now() - t0 < timeout) {
    if (await page.evaluate(fn)) return true;
    await sleep(150);
  }
  throw new Error('timeout: ' + label);
}
function latestSnap() {
  const dir = path.join(__dirname, '..', 'exports');
  const files = fs.readdirSync(dir).filter(f => /^采购看板_单文件_.*\.html$/.test(f))
    .map(f => path.join(dir, f)).sort((a, b) => fs.statSync(b).mtimeMs - fs.statSync(a).mtimeMs);
  return files[0];
}

(async () => {
  const file = SNAP || latestSnap();
  const url = SNAP ? 'file:///' + file.replace(/\\/g, '/') + '#geo'
    : BASE + '/dash?t=' + Date.now() + '#geo';
  const browser = await puppeteer.launch({
    executablePath: CHROME, headless: 'new', args: ['--no-sandbox']
  });
  const page = await browser.newPage();
  const errs = [];
  page.on('pageerror', e => errs.push('PAGEERROR ' + e.message));
  page.on('console', m => { if (m.type() === 'error') errs.push('CONSOLE ' + m.text().slice(0, 140)); });
  try {
    await page.setViewport({ width: 1600, height: 950 });
    await page.goto(url, { waitUntil: 'networkidle2', timeout: 60000 });
    await waitFor(page, () => document.querySelectorAll('#geoTbl tbody tr').length > 0, 20000, 'geo table');
    console.log('=== 1. 页签与首屏 ===');
    const tabs = await page.evaluate(() => [...document.querySelectorAll('.pg-tab')].map(e => e.dataset.page));
    chk('四个页签且 Geo 在末位', tabs.length === 4 && tabs[3] === 'geo', tabs);
    const on = await page.evaluate(() => (document.querySelector('.pg-tab.on') || {}).dataset.page);
    chk('当前页 = geo', on === 'geo', on);
    const head = await page.evaluate(() => ({
      sub: document.getElementById('geoSub').textContent,
      kpis: [...document.querySelectorAll('.geo-kpi')].map(e => e.textContent.trim()),
      metric: document.getElementById('geoMetric').value
    }));
    chk('同比两财年都在副标题', /FY\d{4} vs FY\d{4}/.test(head.sub), head.sub);
    chk('四张 KPI 卡', head.kpis.length === 4, head.kpis);
    chk('默认指标 prpo', head.metric === 'prpo', head.metric);

    console.log('=== 2. Category 同比表（Zebra BI 式）===');
    const tbl = await page.evaluate(() => {
      const rows = [...document.querySelectorAll('#geoTbl tbody tr')].map(tr => {
        const tds = [...tr.children];
        return {
          cat: tds[0].textContent.trim(),
          amount: tds[1].querySelector('.zb em')?.textContent.trim(),
          delta: tds[2].querySelector('.zb em')?.textContent.trim(),
          pct: tds[3].querySelector('.zb em')?.textContent.trim(),
          up: tds[2].querySelectorAll('.zb-pos').length,
          dn: tds[2].querySelectorAll('.zb-neg').length
        };
      });
      return { cols: document.querySelectorAll('#geoTbl thead th').length, rows };
    });
    chk('表头四列', tbl.cols === 4, tbl.cols);
    chk('11 个类别', tbl.rows.length === 11, tbl.rows.length);
    chk('每行金额/ΔPY/ΔPY% 都有值',
      tbl.rows.every(r => r.amount && r.delta && r.pct), tbl.rows.slice(0, 2));
    chk('ΔPY 条形方向与符号一致', tbl.rows.every(r =>
      (r.delta.startsWith('-') ? r.dn === 1 && r.up === 0 : r.up === 1 && r.dn === 0)),
      tbl.rows.filter(r => r.dn !== 1).slice(0, 3));

    console.log('=== 3. 四指标下拉 ===');
    const seen = [];
    for (const m of ['paid', 'micro', 'inadequate', 'prpo']) {
      await page.select('#geoMetric', m);
      await waitFor(page, () => !document.body.classList.contains('loading') &&
        document.querySelector('.geo-kpi .gk-v')?.textContent.length > 1, 20000, m);
      await sleep(250);
      const v = await page.evaluate(() => ({
        metric: document.getElementById('geoMetric').value,
        kpi: document.querySelector('.geo-kpi .gk-v').textContent.trim(),
        cats: document.querySelectorAll('#geoTbl tbody tr').length
      }));
      seen.push(v.metric + ':' + v.kpi);
      chk('切到 ' + m + ' 后 KPI 与类别表更新',
        v.metric === m && v.cats === 11, v);
    }
    chk('四个指标金额各不相同', new Set(seen).size === 4, seen);

    console.log('=== 4. 公司筛选联动 ===');
    const before = await page.evaluate(() => ({
      kpi: document.querySelector('.geo-kpi .gk-v').textContent.trim(),
      comps: document.querySelectorAll('#geoCompList label').length
    }));
    await page.evaluate(() => {
      const l = document.querySelector('#geoCompList label');
      if (l) l.querySelector('input').click();
    });
    await page.waitForFunction((old) =>
      document.querySelector('.geo-kpi .gk-v')?.textContent.trim() !== old,
      { timeout: 20000 }, before.kpi).catch(() => {});
    const after = await page.evaluate(() => ({
      kpi: document.querySelector('.geo-kpi .gk-v')?.textContent.trim(),
      checked: document.querySelectorAll('#geoCompList input:checked').length
    }));
    chk('公司列表非空', before.comps > 0, before);
    chk('勾选公司后 KPI 变化', after.checked === 1 && after.kpi !== before.kpi,
      { before, after });

    console.log('=== 5. 刷新保层级 / 不闪回 ===');
    await page.reload({ waitUntil: 'networkidle2', timeout: 60000 });
    await sleep(900);
    const restored = await page.evaluate(() => ({
      page: (document.querySelector('.pg-tab.on') || {}).dataset.page,
      metric: document.getElementById('geoMetric').value,
      checked: document.querySelectorAll('#geoCompList input:checked').length,
      rows: document.querySelectorAll('#geoTbl tbody tr').length
    }));
    chk('刷新后仍在 Geo 页', restored.page === 'geo', restored);
    chk('刷新后指标与勾选保持',
      restored.metric === 'prpo' && restored.checked === 1, restored);
    chk('刷新后同比表就绪', restored.rows === 11, restored);

    console.log('=== 6. 地图容器与 Key 提示 ===');
    const mapState = await page.evaluate(() => ({
      box: !!document.getElementById('geoMap'),
      h: Math.round(document.getElementById('geoMap').getBoundingClientRect().height),
      msgHidden: document.getElementById('geoMapMsg').hidden,
      msg: document.getElementById('geoMapMsg').textContent.slice(0, 60),
      key: window.TMAP_KEY || ''
    }));
    chk('地图容器有固定高度', mapState.box && mapState.h > 150, mapState);
    if (!mapState.key) {
      chk('未配 Key 时显示配置提示', !mapState.msgHidden && /TMAP_KEY/.test(mapState.msg), mapState);
    } else {
      chk('配了 Key 时不挡提示', mapState.msgHidden, mapState);
    }
    chk('全程无 JS 报错', errs.length === 0, errs);
    console.log('\n' + (fail ? 'FAILED ' + fail + ' / ' : 'ALL PASS ') + pass);
  } catch (e) {
    fail++;
    console.log('  XX  ' + e.message);
  } finally {
    await browser.close();
    process.exit(fail ? 1 : 0);
  }
})();
