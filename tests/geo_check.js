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
    chk('公司筛选 / 地图 / 同比表横向并排',
      layout.comp.y === layout.map.y && layout.map.y === layout.yoy.y &&
      layout.comp.right <= layout.map.x && layout.map.right <= layout.yoy.x, layout);
    chk('三栏等高且不铺满整屏',
      [layout.map, layout.yoy, layout.comp].every(b =>
        Math.abs(b.h - 400) <= 2) && layout.card.h < 600, layout);
    const fit = await page.evaluate(() => {
      const w = document.querySelector('.geo-tbl-wrap').getBoundingClientRect();
      const last = document.querySelector('#geoTbl tbody tr:last-child').getBoundingClientRect();
      return { gap: Math.round(w.bottom - last.bottom), h: Math.round(w.height) };
    });
    chk('同比表固定行高、不拉伸', fit.h > 300 && fit.gap >= 0 && fit.gap < 80, fit);
    chk('同比表最宽、筛选栏最窄',
      layout.yoy.w > layout.map.w && layout.map.w > layout.comp.w, layout);

    console.log('=== 1c. 公司级气泡 / 英文简称 / 华南视角 ===');
    const gd = await page.evaluate(async () =>
      await (await fetch('/api/board/geo?metric=prpo')).json());
    chk('气泡按公司而非按城市合并',
      gd.bubbles.length > 40 &&
      new Set(gd.bubbles.map(b => b.company)).size === gd.bubbles.length,
      { bubbles: gd.bubbles.length, companies: gd.companies.length });
    chk('同城每家公司各有坐标',
      gd.bubbles.filter(b => b.city === '深圳市').length >= 10,
      gd.bubbles.filter(b => b.city === '深圳市').length);
    chk('气泡与列表都带英文简称',
      gd.bubbles.every(b => b.abbr && b.company), gd.bubbles.slice(0, 2));
    const labels = await page.evaluate(() =>
      [...document.querySelectorAll('#geoCompList label span')].slice(0, 6)
        .map(e => e.textContent.trim()));
    chk('公司列表显示英文简称',
      labels.length > 0 && labels.every(x => /^[A-Z]/.test(x)), labels);
    const view = await page.evaluate(() => {
      const el = document.getElementById('geoMap');
      return { zoom: +el.dataset.zoom, center: el.dataset.center };
    });
    if (!SNAP) {
      chk('默认聚焦华南', view.zoom >= 5.5 &&
        parseFloat(view.center.split(',')[1]) < 25, view);
      const zr = await page.evaluate(async () => {
        const el = document.getElementById('geoMap');
        const px1 = +el.dataset.samplePx || 0, m1 = +el.dataset.sampleRadius || 0;
        window.__geoMap.setZoom(window.__geoMap.getZoom() + 2);
        await new Promise(r => setTimeout(r, 600));
        const px2 = +el.dataset.samplePx || 0, m2 = +el.dataset.sampleRadius || 0;
        window.__geoMap.setZoom(5.6);
        await new Promise(r => setTimeout(r, 400));
        return { px1, px2, m1, m2 };
      });
      chk('放大时气泡屏幕尺寸近似恒定（仅轻微变小）',
        zr.px1 > 0 && zr.px2 / zr.px1 > 0.7 && zr.px2 / zr.px1 < 0.95, zr);
      chk('地理半径随缩放反向变化',
        zr.m2 > 0 && zr.m1 / zr.m2 > 2, zr);
    }
    const overflow = await page.evaluate(() => {
      const w = document.querySelector('.geo-tbl-wrap'),
            p = document.querySelector('.geo-comp-pane').getBoundingClientRect(),
            card = document.querySelector('#pgGeo .geo-card').getBoundingClientRect();
      return { xScroll: w.scrollWidth - w.clientWidth,
               compRight: Math.round(p.right - card.right) };
    });
    chk('同比表无横向滚动条', overflow.xScroll <= 1, overflow);
    chk('公司筛选框未跑出画布', overflow.compRight <= 1, overflow);

    console.log('=== 2. Category 同比表（Zebra 视觉 + 亮色主题）===');
    const tbl = await page.evaluate(() => {
      const rows = [...document.querySelectorAll('#geoTbl tbody tr')].map(tr => {
        const tds = [...tr.children];
        return {
          cat: tds[0].textContent.trim(),
          py: tds[1].textContent.trim(),
          amount: tds[2].querySelector('.z-amt .zv')?.textContent.trim(),
          gray: !!tds[2].querySelector('.zb-gray'),
          grayColor: tds[2].querySelector('.zb-gray')
            ? getComputedStyle(tds[2].querySelector('.zb-gray')).backgroundColor : '',
          delta: tds[3].querySelector('.z-side .zv')?.textContent.trim(),
          pct: tds[4].querySelector('.z-side .zv')?.textContent.trim(),
          up: tds[3].querySelectorAll('.z-side.pos').length,
          dn: tds[3].querySelectorAll('.z-side.neg').length,
          dot: !!tds[4].querySelector('.zb-line'),
          dotColor: tds[4].querySelector('.zb-line')
            ? getComputedStyle(tds[4].querySelector('.zb-line'), '::after').backgroundColor : ''
        };
      });
      return {
        cols: [...document.querySelectorAll('#geoTbl thead th')].map(e => e.textContent.trim()),
        rows,
        groups: document.querySelectorAll('#geoTbl tbody tr.z-group').length,
        dark: getComputedStyle(document.querySelector('.geo-tbl-wrap')).backgroundColor,
        zero: getComputedStyle(document.querySelector('.z-br'), '::after').width
      };
    });
    chk('表头严格复刻：Category / PY / AC ↓ / ΔPY / ΔPY%',
      tbl.cols.join('|') === 'Category|PY|AC ↓|ΔPY|ΔPY%', tbl.cols);
    chk('11 个类别', tbl.rows.length === 11, tbl.rows.length);
    chk('PY 列是纯数字（右对齐）',
      tbl.rows.filter(r => r.py).length >= 9 && tbl.rows.every(r => !r.py || /^\d+\.\d$/.test(r.py)),
      tbl.rows.slice(0, 2).map(r => r.py));
    chk('每行金额/ΔPY/ΔPY% 都有值',
      tbl.rows.every(r => r.amount && r.delta && r.pct), tbl.rows.slice(0, 2));
    chk('表格沿用看板亮色主题', tbl.dark === 'rgb(255, 255, 255)', tbl.dark);
    chk('AC 深灰条 + ΔPY% 黑心圆点',
      tbl.rows.every(r => r.gray && r.dot && r.grayColor === 'rgb(74, 74, 74)' &&
        r.dotColor === 'rgb(17, 17, 17)'), tbl.rows.slice(0, 2));
    chk('ΔPY 中心零线 2px', tbl.zero === '2px', tbl.zero);
    chk('参考图分组横线（第 6 行前）', tbl.groups === 1, tbl.groups);
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

    console.log('=== 4. 公司多选联动 ===');
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
    const mid = await page.evaluate(() => ({
      checked: document.querySelectorAll('#geoCompList input:checked').length,
      comps: document.querySelectorAll('#geoCompList label').length
    }));
    chk('勾选一家后公司列表仍完整', before.comps > 40 && mid.comps >= before.comps - 1,
      { before: before.comps, mid });
    await page.evaluate(() => {
      const ls = [...document.querySelectorAll('#geoCompList label')];
      const l = ls.find(x => !x.querySelector('input').checked);
      if (l) l.querySelector('input').click();
    });
    await page.waitForFunction(() =>
      document.querySelectorAll('#geoCompList input:checked').length === 2,
      { timeout: 20000 }).catch(() => {});
    await sleep(700);
    const after = await page.evaluate(() => ({
      checked: document.querySelectorAll('#geoCompList input:checked').length,
      rows: document.querySelectorAll('#geoTbl tbody tr').length
    }));
    chk('可以连续多选公司', after.checked === 2, after);
    chk('多选后同比表仍有 11 行', after.rows === 11, after);

    console.log('=== 4b. Zebra 行点击联动 ===');
    await page.evaluate(() => {
      const tr = document.querySelector('#geoTbl tbody tr.z-row');
      const cat = tr.dataset.cat;
      tr.click();
      return cat;
    });
    await sleep(1400);
    const z1 = await page.evaluate(() => ({
      on: document.querySelectorAll('#geoTbl tbody tr.z-row.on').length,
      dim: document.querySelectorAll('#geoTbl tbody tr.z-row.dim').length,
      rows: document.querySelectorAll('#geoTbl tbody tr').length,
      chips: document.querySelectorAll('#chips .chip').length
    }));
    chk('点击行后高亮该行、其余淡化',
      z1.on === 1 && z1.dim === z1.rows - 1, z1);
    chk('行点击联动全局筛选（出现 chip）', z1.chips > 0, z1);
    await page.evaluate(() => {
      const tr = [...document.querySelectorAll('#geoTbl tbody tr.z-row.on')][0];
      if (tr) tr.click();
    });
    await sleep(1400);
    const z2 = await page.evaluate(() => ({
      on: document.querySelectorAll('#geoTbl tbody tr.z-row.on').length,
      dim: document.querySelectorAll('#geoTbl tbody tr.z-row.dim').length,
      rows: document.querySelectorAll('#geoTbl tbody tr').length
    }));
    chk('再次点击取消筛选、恢复整表', z2.on === 0 && z2.dim === 0 && z2.rows === 11, z2);
    chk('点击期间无报错', errs.length === 0, errs.slice(0, 3));

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
      restored.metric === 'prpo' && restored.checked === 2, restored);
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
