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
      noKpi: !document.querySelector('.geo-kpi'),
      metric: document.getElementById('geoMetric').value
    }));
    chk('同比两财年都在副标题', /FY\d{4} vs FY\d{4}/.test(head.sub), head.sub);
    chk('顶部 KPI 卡已移除', head.noKpi, head);
    chk('默认指标 prpo', head.metric === 'prpo', head.metric);

    console.log('=== 1b. 三栏横向布局 ===');
    const layout = await page.evaluate(() => {
      const r = s => { const b = document.querySelector(s).getBoundingClientRect();
        return { x: Math.round(b.x), y: Math.round(b.y), w: Math.round(b.width),
                 h: Math.round(b.height), right: Math.round(b.right), bottom: Math.round(b.bottom) }; };
      return { map: r('.geo-map-pane'), yoy: r('.geo-yoy'), comp: r('.geo-comp-pane'),
               card: r('#pgGeo .geo-card') };
    });
    chk('地图 / 同比表 / 公司筛选横向并排',
      layout.map.y === layout.yoy.y && layout.yoy.y === layout.comp.y &&
      layout.map.right <= layout.yoy.x && layout.yoy.right <= layout.comp.x, layout);
    chk('三栏铺满卡片下方',
      [layout.map, layout.yoy, layout.comp].every(b =>
        Math.abs(b.bottom - layout.card.bottom) <= 12 && b.h > 400), layout);
    chk('地图占比最大、筛选栏最窄',
      layout.map.w > layout.yoy.w && layout.yoy.w > layout.comp.w, layout);

    console.log('=== 2. Category 同比表（Zebra 视觉 + 亮色主题）===');
    const tbl = await page.evaluate(() => {
      const rows = [...document.querySelectorAll('#geoTbl tbody tr')].map(tr => {
        const tds = [...tr.children];
        return {
          cat: tds[0].textContent.trim(),
          amount: tds[1].querySelector('.z-amt .zv')?.textContent.trim(),
          gray: !!tds[1].querySelector('.zb-gray'),
          delta: tds[2].querySelector('.z-side .zv')?.textContent.trim(),
          pct: tds[3].querySelector('.z-side .zv')?.textContent.trim(),
          up: tds[2].querySelectorAll('.z-side.pos').length,
          dn: tds[2].querySelectorAll('.z-side.neg').length,
          dot: !!tds[3].querySelector('.zb-line')
        };
      });
      return {
        cols: document.querySelectorAll('#geoTbl thead th').length, rows,
        dark: getComputedStyle(document.querySelector('.geo-tbl-wrap')).backgroundColor,
        zero: getComputedStyle(document.querySelector('.z-br'), '::after').width
      };
    });
    chk('表头四列', tbl.cols === 4, tbl.cols);
    chk('11 个类别', tbl.rows.length === 11, tbl.rows.length);
    chk('每行金额/ΔPY/ΔPY% 都有值',
      tbl.rows.every(r => r.amount && r.delta && r.pct), tbl.rows.slice(0, 2));
    chk('表格沿用看板亮色主题', tbl.dark === 'rgb(255, 255, 255)', tbl.dark);
    chk('金额列灰条 + ΔPY% 细线圆点',
      tbl.rows.every(r => r.gray && r.dot), tbl.rows.slice(0, 2));
    chk('ΔPY 中心零线', tbl.zero === '1px', tbl.zero);
    chk('ΔPY 条形方向与符号一致', tbl.rows.every(r =>
      (r.delta.startsWith('-') ? r.dn === 1 && r.up === 0 : r.up === 1 && r.dn === 0)),
      tbl.rows.filter(r => r.dn !== 1).slice(0, 3));

    console.log('=== 3. 四指标下拉 ===');
    const seen = [];
    let prev = await page.evaluate(() =>
      document.querySelector('#geoTbl tbody .z-amt .zv').textContent.trim());
    for (const m of ['paid', 'micro', 'inadequate', 'prpo']) {
      await page.select('#geoMetric', m);
      await page.waitForFunction((old) => {
        const el = document.querySelector('#geoTbl tbody .z-amt .zv');
        return el && el.textContent.trim() !== old;
      }, { timeout: 20000 }, prev).catch(() => {});
      await sleep(200);
      const v = await page.evaluate(() => ({
        metric: document.getElementById('geoMetric').value,
        first: document.querySelector('#geoTbl tbody .z-amt .zv').textContent.trim(),
        cats: document.querySelectorAll('#geoTbl tbody tr').length
      }));
      seen.push(v.metric + ':' + v.first);
      chk('切到 ' + m + ' 后金额与类别表更新',
        v.metric === m && v.cats === 11 && v.first !== prev, v);
      prev = v.first;
    }
    chk('四个指标金额各不相同', new Set(seen).size === 4, seen);

    console.log('=== 4. 公司筛选联动 ===');
    const before = await page.evaluate(() => ({
      first: document.querySelector('#geoTbl tbody .z-amt .zv').textContent.trim(),
      comps: document.querySelectorAll('#geoCompList label').length
    }));
    await page.evaluate(() => {
      const l = document.querySelector('#geoCompList label');
      if (l) l.querySelector('input').click();
    });
    await page.waitForFunction((old) => {
      const el = document.querySelector('#geoTbl tbody .z-amt .zv');
      return el && el.textContent.trim() !== old;
    }, { timeout: 20000 }, before.first).catch(() => {});
    const after = await page.evaluate(() => ({
      first: document.querySelector('#geoTbl tbody .z-amt .zv').textContent.trim(),
      checked: document.querySelectorAll('#geoCompList input:checked').length
    }));
    chk('公司列表非空', before.comps > 0, before);
    chk('勾选公司后同比表变化', after.checked === 1 && after.first !== before.first,
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
