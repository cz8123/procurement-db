/**
 * 标题栏页签（多页面切换基础）检查，服务先起
 *   NODE_PATH="<puppeteer-core 所在 node_modules>" node tests/page_tabs_check.js
 *
 * 盯住：示例中的「白色选中页 + 灰色未选中页 + 下方小标题/下划线」样式、
 *       页签切换时其他控件零位移、未实现页不能伪装成当前页、
 *       BoardPages.register/go 的接口可用、三档宽度不溢出。
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
async function waitFor(page, fn, timeout, label) {
  const t0 = Date.now();
  while (Date.now() - t0 < timeout) {
    if (await page.evaluate(fn)) return true;
    await sleep(150);
  }
  throw new Error('timeout: ' + label);
}

(async () => {
  const browser = await puppeteer.launch({
    executablePath: CHROME, headless: 'new', args: ['--no-sandbox']
  });
  const page = await browser.newPage();
  const errs = [];
  page.on('pageerror', e => errs.push('PAGEERROR ' + e.message));
  page.on('console', m => { if (m.type() === 'error') errs.push('CONSOLE ' + m.text().slice(0, 140)); });
  try {
    await page.setViewport({ width: 1920, height: 1050 });
    await page.goto(BASE + '/dash?t=' + Date.now(), { waitUntil: 'networkidle0' });
    await waitFor(page, () => document.querySelectorAll('#chHeat .hcell').length > 0, 15000, 'charts');
    await sleep(400);

    console.log('=== 1. 页签结构 ===');
    const tabs = await page.evaluate(() => [...document.querySelectorAll('.pg-tab')].map(e => {
      const top = e.querySelector('.pg-tab-top');
      const b = e.getBoundingClientRect();
      return {
        page: e.dataset.page, label: top.textContent.trim(),
        on: e.classList.contains('on'), current: e.getAttribute('aria-current'),
        topBg: getComputedStyle(top).backgroundColor,
        topColor: getComputedStyle(top).color,
        radius: getComputedStyle(top).borderTopLeftRadius,
        h: Math.round(b.height), bottom: Math.round(b.bottom)
      };
    }));
    const subCount = await page.evaluate(() => document.querySelectorAll('.pg-tab-sub').length);
    const headerBottom = await page.evaluate(() =>
      Math.round(document.querySelector('.hd').getBoundingClientRect().bottom));
    console.log('    ' + JSON.stringify(tabs.map(t => [t.page, t.label, t.on])));
    chk('四个页签且首签为 Overview',
      tabs.length === 4 && tabs[0].page === 'overview' && tabs[0].label === 'Overview', tabs);
    chk('只保留大标题：无小标题行', subCount === 0 &&
      tabs[1].label === 'Detail' && tabs[2].label === 'Framework' &&
      tabs[3].label === 'Geo Spend', { subCount, tabs });
    chk('选中页白底深字 + 圆角上沿',
      tabs[0].topBg === 'rgb(255, 255, 255)' && tabs[0].topColor === 'rgb(14, 23, 32)' &&
      tabs[0].radius !== '0px', tabs[0]);
    chk('未选中页灰底', tabs[1].topBg === 'rgb(169, 177, 188)', tabs[1]);
    chk('选中页有 aria-current', tabs[0].current === 'page' && !tabs[1].current, tabs);
    chk('页签高度 40px（比原先紧凑）', tabs.every(t => t.h === 40), tabs.map(t => t.h));
    chk('页签下沿与顶栏下沿对齐', tabs.every(t => Math.abs(t.bottom - headerBottom) <= 1),
      { headerBottom, bottoms: tabs.map(t => t.bottom) });

    console.log('=== 2. 页签都能真正切换 ===');
    const before = await page.evaluate(() => {
      const p = s => { const b = document.querySelector(s).getBoundingClientRect(); return [Math.round(b.x), Math.round(b.y)]; };
      return { hd: Math.round(document.querySelector('.hd').getBoundingClientRect().height),
               fy: p('#fySel'), cur: p('#curSeg'),
               kpiHidden: document.querySelector('.kpis').hidden };
    });
    await page.click('.pg-tab[data-page="detail"]');
    await waitFor(page, () => document.querySelectorAll('#dtTbl tbody tr').length > 0, 12000, 'detail rows');
    const after = await page.evaluate(() => {
      const p = s => { const b = document.querySelector(s).getBoundingClientRect(); return [Math.round(b.x), Math.round(b.y)]; };
      return { hd: Math.round(document.querySelector('.hd').getBoundingClientRect().height),
               fy: p('#fySel'), cur: p('#curSeg'),
               kpiHidden: document.querySelector('.kpis').hidden,
               on: [...document.querySelectorAll('.pg-tab.on')].map(e => e.dataset.page),
               detailVisible: !document.getElementById('pgDetail').hidden,
               overviewHidden: document.getElementById('pgOverview').hidden,
               current: window.BoardPages.current() };
    });
    console.log('    ' + JSON.stringify(after));
    chk('Detail 页签切换到第二页', after.on.length === 1 && after.on[0] === 'detail' &&
      after.current === 'detail' && after.detailVisible && after.overviewHidden, after);
    chk('切页不移动顶栏控件', JSON.stringify(before) === JSON.stringify({
      hd: after.hd, fy: after.fy, cur: after.cur, exp: after.exp, kpiHidden: before.kpiHidden }), [before, after]);
    chk('Detail 页隐藏 KPI 与全局筛选条', after.kpiHidden &&
      await page.evaluate(() => document.querySelector('.fbar').hidden), after);
    await page.click('.pg-tab[data-page="framework"]');
    await sleep(400);
    chk('Framework 页签切到独立协议页，Overview 不再有协议表', await page.evaluate(() =>
      window.BoardPages.current() === 'framework' &&
      !!document.querySelector('#pgFramework #fcCard') &&
      !document.querySelector('#pgOverview #fcCard') &&
      document.querySelector('.fbar').hidden &&
      document.querySelector('.kpis').hidden &&
      document.getElementById('pgDetail').hidden));
    await page.click('.pg-tab[data-page="overview"]');
    await sleep(400);
    chk('返回 Overview：协议卡留在 Framework，全局筛选条恢复', await page.evaluate(() =>
      window.BoardPages.current() === 'overview' &&
      !!document.querySelector('#pgFramework #fcCard') &&
      document.getElementById('pgOverview').hidden === false &&
      document.querySelector('.fbar').hidden === false &&
      document.querySelector('.kpis').hidden === false));

    console.log('=== 3. 注册新页后可真正切换 ===');
    const reg = await page.evaluate(async () => {
      let hits = 0;
      window.BoardPages.register('demo', { label: 'Demo Page', enter: () => { hits++; } });
      const added = [...document.querySelectorAll('.pg-tab')].map(e => e.dataset.page);
      window.BoardPages.go('demo');
      const on = [...document.querySelectorAll('.pg-tab.on')].map(e => e.dataset.page);
      const label = document.querySelector('.pg-tab.on .pg-tab-top').textContent.trim();
      window.BoardPages.go('overview');
      return { added, on, label, hits, back: window.BoardPages.current() };
    });
    console.log('    ' + JSON.stringify(reg));
    chk('register 后自动多一个页签', reg.added.length === 5 && reg.added[4] === 'demo', reg.added);
    chk('go 切换选中态并调用 enter', reg.on.length === 1 && reg.on[0] === 'demo' && reg.label === 'Demo Page' && reg.hits === 1, reg);
    chk('可切回 Overview', reg.back === 'overview', reg.back);

    console.log('=== 4. 多宽度不溢出、顶栏高度稳定 ===');
    /* 上面注册了一个测试页（5 个页签），重载回到正式的四个页签再量版式 */
    await page.goto(BASE + '/dash?t=' + Date.now(), { waitUntil: 'networkidle0' });
    await waitFor(page, () => document.querySelectorAll('#chHeat .hcell').length > 0, 15000, 'reload');
    for (const w of [1440, 1280]) {
      await page.setViewport({ width: w, height: 1050 });
      await sleep(350);
      const g = await page.evaluate(() => {
        const hd = document.querySelector('.hd');
        const right = s => document.querySelector(s).getBoundingClientRect().right;
        const left = s => document.querySelector(s).getBoundingClientRect().left;
        const pad = parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--pad'));
        return { h: Math.round(hd.getBoundingClientRect().height),
                 overflow: hd.scrollWidth > hd.clientWidth + 1,
                 cut: [...document.querySelectorAll('.pg-tab-top')]
                   .filter(e => e.scrollWidth > e.clientWidth + 1).map(e => e.textContent),
                 tabsRight: Math.round(right('#pgTabs')),
                 edgeGap: Math.round(innerWidth - 15 - right('#pgTabs')), // 15px 为滚动条
                 pad: pad,
                 controlsBeforeTabs: right('.hd-r') <= left('#pgTabs') + 1,
                 controlsMiddle: Math.round((left('.hd-r') + right('.hd-r')) / 2) };
      });
      console.log('    ' + w + ' ' + JSON.stringify(g));
      chk(w + 'px 顶栏仍 68px 且无溢出/截断',
        g.h === 68 && !g.overflow && g.cut.length === 0, g);
      chk(w + 'px 页签贴最右、控件在中间', g.controlsBeforeTabs &&
        Math.abs(g.edgeGap - g.pad) <= 2 &&
        g.controlsMiddle > w * 0.35 && g.controlsMiddle < w * 0.7, g);
    }

    console.log('\n=== 6b. 切回概览时 Top10 不被放大 ===');
    await page.setViewport({ width: 1600, height: 1000 });
    await sleep(350);
    const fy0 = await page.$eval('#fySel', s => s.value);
    const fyList = await page.$$eval('#fySel option', o => o.map(x => x.value).filter(Boolean));
    const fy1 = fyList.find(f => f !== fy0);
    await page.click('.pg-tab[data-page="detail"]');
    await sleep(500);
    if (fy1) { await page.select('#fySel', fy1); await sleep(1600); }
    await page.click('.pg-tab[data-page="overview"]');
    await sleep(1600);
    const top = await page.evaluate(() => {
      const svg = document.querySelector('#chTop svg');
      if (!svg) return null;
      const box = document.getElementById('chTop');
      const cs = getComputedStyle(box);
      const inner = box.clientWidth -
        parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight);
      return { w: +svg.getAttribute('width'), box: Math.round(inner),
               rendered: +svg.getBoundingClientRect().width.toFixed(2) };
    });
    chk('Top10 SVG 宽度=容器内宽（1:1 不缩放）',
      !!top && Math.abs(top.w - top.box) <= 1 &&
      Math.abs(top.rendered - top.w) <= 1, top);
    if (fy1) { await page.select('#fySel', fy0); await sleep(1200); }

    console.log('\n=== 7. 刷新首帧不闪回 Overview ===');
    for (const hash of ['detail', 'framework']) {
      const p = await browser.newPage();
      p.on('pageerror', e => errs.push('PAGEERROR ' + hash + ' ' + e.message));
      p.on('console', m => { if (m.type() === 'error') errs.push('CONSOLE ' + hash + ' ' + m.text().slice(0, 140)); });
      await p.setViewport({ width: 1440, height: 900 });
      await p.evaluateOnNewDocument((h) => {
        window.__vis = [];
        const t0 = Date.now();
        function snap() {
          const ov = document.getElementById('pgOverview');
          const target = document.getElementById(h === 'detail' ? 'pgDetail' : 'pgFramework');
          if (ov) window.__vis.push({
            t: Date.now() - t0,
            ov: getComputedStyle(ov).display !== 'none',
            target: !!target && getComputedStyle(target).display !== 'none'
          });
          if (Date.now() - t0 < 5000) setTimeout(snap, 16);
        }
        document.addEventListener('DOMContentLoaded', snap);
      }, hash);
      await p.goto(BASE + '/dash?t=' + Date.now() + '#' + hash,
        { waitUntil: 'networkidle0', timeout: 30000 });
      await sleep(250);
      const samples = await p.evaluate(() => window.__vis || []);
      await p.close();
      const flashes = samples.filter(s => s.ov);
      chk(hash + ' 刷新全程未见 Overview', flashes.length === 0,
        { samples: samples.length, flashes: flashes.slice(0, 4) });
      chk(hash + ' 最终停在目标页',
        samples.length > 0 && samples[samples.length - 1].target,
        samples[samples.length - 1]);
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
