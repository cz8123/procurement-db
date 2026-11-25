/**
 * Detail 大表格检查（服务先起）
 *   NODE_PATH="<puppeteer-core 所在 node_modules>" node tests/detail_table_check.js
 *
 * 盯住：8 列（含 PRPO 说明）与每列排序/搜索/筛选、合计与筛选全集一致、数值列区间、
 *       三级树（区域→公司→明细）默认全折叠、无分页、币种换算、切页后全局筛选仍能刷新表格。
 *       期望值一律从 API 现场取。
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
async function waitFor(page, fn, timeout, label, arg) {
  const t0 = Date.now();
  while (Date.now() - t0 < timeout) {
    if (await page.evaluate(fn, arg)) return true;
    await sleep(150);
  }
  throw new Error('timeout: ' + label);
}
const num = s => Number(String(s).replace(/[^\d.-]/g, ''));
const metaNum = s => Number(String(s).split(' ')[0].replace(/,/g, ''));

(async () => {
  const browser = await puppeteer.launch({
    executablePath: CHROME, headless: 'new', args: ['--no-sandbox']
  });
  const page = await browser.newPage();
  const errs = [];
  page.on('pageerror', e => errs.push('PAGEERROR ' + e.message));
  page.on('response', r => { if (r.status() >= 400) errs.push('HTTP ' + r.status() + ' ' +
    decodeURIComponent(r.url().replace(BASE, ''))); });
  page.on('console', m => { if (m.type() === 'error') errs.push('CONSOLE ' + m.text().slice(0, 140)); });
  try {
    await page.setViewport({ width: 1920, height: 1100 });
    await page.goto(BASE + '/dash?t=' + Date.now(), { waitUntil: 'networkidle0' });
    await waitFor(page, () => document.querySelectorAll('#chHeat .hcell').length > 0, 15000, 'charts');
    await page.click('.pg-tab[data-page="detail"]');
    await waitFor(page, () => document.querySelectorAll('#dtTbl tbody tr').length > 0, 12000, 'detail rows');

    console.log('=== 1. 表头与合计 ===');
    const base = await page.evaluate(async () => {
      const r = await fetch('/api/board/detail?sort=amount&dir=desc');
      return r.json();
    });
    const head = await page.evaluate(() => [...document.querySelectorAll('#dtTbl thead th')].map(th => ({
      text: th.textContent.trim(),
      sort: !!th.querySelector('.dt-sort'), funnel: !!th.querySelector('.dt-funnel'),
      width: Math.round(th.getBoundingClientRect().width)
    })));
    console.log('    ' + JSON.stringify(head.map(h => [h.text, h.width])));
    chk('九列且名称正确（理由、说明都在方法与本单金额之间）', head.length === 9 &&
      ['Region', 'Company', 'Category', 'Supplier', 'Procurement Method',
       'Single Source Reason', 'Note', 'PRPO Amount (RMB)', 'PO Paid (RMB)']
        .every((t, i) => head[i].text.indexOf(t) === 0), head);
    chk('每个表头都有排序与筛选入口', head.every(h => h.sort && h.funnel), head);
    chk('无分页条', await page.evaluate(() => !document.getElementById('dtPager')));
    const shown = await page.evaluate(() => ({
      meta: document.getElementById('dtMeta').textContent,
      footCells: [...document.querySelectorAll('#dtTbl tfoot td')].map(e => e.textContent.trim())
    }));
    console.log('    ' + JSON.stringify(shown) + ' api total=' + base.total);
    chk('行数与 API 一致', metaNum(shown.meta) === base.total, shown);
    chk('合计 = 筛选全集（不是当前页）',
      Math.abs(num(shown.footCells[7]) - base.sums.amount) < 0.05 &&
      Math.abs(num(shown.footCells[8]) - base.sums.paid) < 0.05,
      { foot: shown.footCells, sums: base.sums });

    console.log('=== 1b. 三级树默认全折叠 ===');
    const grp = await page.evaluate(() => ({
      regions: document.querySelectorAll('#dtTbl tbody tr.dt-group').length,
      companies: document.querySelectorAll('#dtTbl tbody tr.dt-company').length,
      children: document.querySelectorAll('#dtTbl tbody tr.dt-child').length,
      glyph: document.querySelector('#dtTbl .dt-toggle').textContent.trim(),
      subTotal: document.querySelector('#dtTbl tr.dt-group td:nth-child(3)').textContent.trim()
    }));
    console.log('    ' + JSON.stringify(grp));
    chk('区域行数 = API regionTotals 且默认 + 号全折叠',
      grp.regions === base.regionTotals.length && grp.glyph === '+' &&
      grp.companies === 0 && grp.children === 0 && /¥/.test(grp.subTotal), grp);
    const dtRegions = await page.evaluate(() => [...document.querySelectorAll('#dtTbl tr.dt-group')]
      .map(e => e.dataset.region));
    const expectRegions = base.regionTotals.slice().sort((a, b) => b.amount - a.amount).map(r => r.region);
    chk('区域按采购额降序（大的在上）', JSON.stringify(dtRegions) === JSON.stringify(expectRegions),
      { actual: dtRegions, expect: expectRegions });

    /* 找一个有多个公司的小区域展开，避免渲染上千行 */
    const target = base.regionTotals.slice().sort((a, b) => a.n - b.n)[0].region;
    const coCount = base.companyTotals.filter(r => r.region === target).length;
    await page.evaluate(r => document.querySelector('#dtTbl tr.dt-group[data-region="' +
      CSS.escape(r) + '"] .dt-toggle').click(), target);
    await waitFor(page, r => document.querySelectorAll('#dtTbl tbody tr.dt-company').length > 0,
      8000, 'expand region', target);
    await page.evaluate(r => window.__targetRegion = r, target);
    const lvl2 = await page.evaluate(() => ({
      glyph: document.querySelector('#dtTbl tr.dt-group[data-region="' + CSS.escape(window.__targetRegion) +
        '"] .dt-toggle').textContent.trim(),
      companies: document.querySelectorAll('#dtTbl tbody tr.dt-company').length,
      children: document.querySelectorAll('#dtTbl tbody tr.dt-child').length,
      subTotal: document.querySelector('#dtTbl tr.dt-company td:nth-child(4)').textContent.trim()
    }));
    console.log('    ' + JSON.stringify(lvl2) + ' expect companies=' + coCount);
    chk('展开区域后出现公司行（公司仍折叠）', lvl2.glyph === '−' &&
      lvl2.companies === coCount && lvl2.children === 0 && /¥/.test(lvl2.subTotal), lvl2);

    const coKey = await page.evaluate(() => document.querySelector('#dtTbl tr.dt-company .dt-toggle').dataset.toggleCompany);
    const coRow = base.companyTotals.find(r => (r.region || '') + '\u0001' + (r.company || '') === coKey);
    await page.click('#dtTbl tr.dt-company .dt-toggle');
    await waitFor(page, () => document.querySelectorAll('#dtTbl tbody tr.dt-child').length > 0,
      8000, 'expand company');
    const lvl3 = await page.evaluate(() => ({
      glyph: document.querySelector('#dtTbl tr.dt-company .dt-toggle').textContent.trim(),
      children: document.querySelectorAll('#dtTbl tbody tr.dt-child').length,
      first: document.querySelector('#dtTbl tr.dt-child td:nth-child(2)').textContent.trim()
    }));
    console.log('    ' + JSON.stringify(lvl3) + ' expect children=' + (coRow && coRow.n));
    chk('展开公司后出现明细行且条数一致', lvl3.glyph === '−' &&
      lvl3.children === (coRow ? coRow.n : -1), lvl3);
    await page.click('#dtTbl tr.dt-company .dt-toggle');
    await sleep(200);
    chk('再点公司 + 收起明细', await page.evaluate(() =>
      document.querySelectorAll('#dtTbl tbody tr.dt-child').length === 0));
    await page.evaluate(r => document.querySelector('#dtTbl tr.dt-group[data-region="' +
      CSS.escape(r) + '"] .dt-toggle').click(), target);
    await sleep(200);
    chk('再点区域 + 收起公司行', await page.evaluate(() =>
      document.querySelectorAll('#dtTbl tbody tr.dt-company').length === 0));

    console.log('=== 1c. 说明列（单行省略 + 悬停浮窗给全文 + 搜索） ===');
    /* 重新展开一个区域 + 一个公司，拿到明细行 */
    await page.evaluate(r => document.querySelector('#dtTbl tr.dt-group[data-region="' +
      CSS.escape(r) + '"] .dt-toggle').click(), target);
    await waitFor(page, () => document.querySelectorAll('#dtTbl tbody tr.dt-company').length > 0,
      8000, 're-expand region');
    await page.click('#dtTbl tr.dt-company .dt-toggle');
    await waitFor(page, () => document.querySelectorAll('#dtTbl tbody tr.dt-child').length > 0,
      8000, 're-expand company');

    const noteCell = await page.evaluate(() => {
      const td = document.querySelector('#dtTbl tr.dt-child td.dt-note');
      if (!td) return null;
      const cs = getComputedStyle(td);
      return {
        text: td.textContent.trim(), tip: td.getAttribute('data-tip') || '',
        ellipsis: cs.textOverflow, nowrap: cs.whiteSpace,
        clipped: td.scrollWidth > td.clientWidth + 1,
        idx: [...td.parentElement.children].indexOf(td)
      };
    });
    console.log('    ' + JSON.stringify(noteCell));
    chk('明细行有说明列（第 7 列，理由与本单金额之间）', !!noteCell && noteCell.idx === 6, noteCell);
    chk('说明单行 + 省略号（不会被长文本撑开）', !!noteCell &&
      noteCell.ellipsis === 'ellipsis' && noteCell.nowrap === 'nowrap', noteCell);
    chk('data-tip 给全文（长于可见文字）', !!noteCell &&
      noteCell.tip.length > 0 && noteCell.tip.length >= noteCell.text.replace(/…/g, '').length, noteCell);

    /* 悬停浮窗：说明格鼠标移入 → #dtTip 显示全文、落在视口内、移出即收起 */
    const tip1 = await page.evaluate(() => {
      const td = document.querySelector('#dtTbl tr.dt-child td.dt-note');
      td.dispatchEvent(new MouseEvent('mouseover', { bubbles: true }));
      return td.getAttribute('data-tip');
    });
    await sleep(260);
    const tipShown = await page.evaluate(() => {
      const el = document.querySelector('#dtTip');
      if (!el || el.hidden) return null;
      const r = el.getBoundingClientRect();
      return { text: el.textContent, w: Math.round(r.width), h: Math.round(r.height),
               inView: r.left >= 0 && r.top >= 0 && r.right <= innerWidth + 1 && r.bottom <= innerHeight + 1,
               panel: getComputedStyle(el).position };
    });
    chk('悬停说明格弹出浮窗（fixed，落在视口内）', !!tipShown &&
      tipShown.panel === 'fixed' && tipShown.w > 0 && tipShown.inView, tipShown);
    chk('浮窗给的是整段说明（与 data-tip 一致）', !!tipShown && tipShown.text === tip1,
      tipShown && { got: (tipShown.text || '').length, want: (tip1 || '').length });
    await page.evaluate(() => {
      document.querySelector('#dtTbl').dispatchEvent(new MouseEvent('mouseleave', { bubbles: false }));
    });
    await sleep(120);
    const tipHidden = await page.evaluate(() => {
      const el = document.querySelector('#dtTip');
      return !el || el.hidden;
    });
    chk('鼠标移出表格后浮窗收起', tipHidden);

    /* 搜索：关键词现场挑一个「确实能收窄」的（说明里「采购」几乎条条都有） */
    const probe = await page.evaluate(async (cands) => {
      const out = [];
      for (const c of cands) {
        const r = await fetch('/api/board/detail?q_note=' + encodeURIComponent(c));
        out.push({ k: c, n: (await r.json()).total });
      }
      return out;
    }, ['抖音', '报废', '续约', '保险', '脚垫', '展厅']);
    const hit = probe.find(x => x.n > 0 && x.n < base.total) || { k: '抖音', n: 0 };
    const kw = hit.k, expectNote = hit.n;
    console.log('    keyword probe=' + JSON.stringify(probe));
    chk('说明搜索在 API 上确实收窄', expectNote > 0 && expectNote < base.total,
      { kw, expectNote, total: base.total });
    await page.click('#dtTbl thead .dt-funnel[data-col="note"]');
    await waitFor(page, () => !!document.querySelector('#dtPop [data-f=search]'), 5000, 'note pop');
    chk('说明列浮层只有搜索框（不拉 4 万种取值）', await page.evaluate(() =>
      !!document.querySelector('#dtPop [data-f=search]') &&
      !document.querySelector('#dtPop .dt-list')));
    await page.type('#dtPop [data-f=search]', kw);
    await page.click('#dtPop [data-act=apply]');
    await waitFor(page, t => Number(document.getElementById('dtMeta').textContent
      .split(' ')[0].replace(/,/g, '')) === t, 12000, 'note filtered', expectNote);
    chk('说明搜索后行数 = API 口径', await page.evaluate(t => Number(
      document.getElementById('dtMeta').textContent.split(' ')[0].replace(/,/g, '')) === t, expectNote));
    await page.click('#dtReset');
    await waitFor(page, t => Number(document.getElementById('dtMeta').textContent
      .split(' ')[0].replace(/,/g, '')) === t, 12000, 'note reset', base.total);
    /* 收起 1c 里手工展开的公司/区域，恢复默认全折叠（后面要断言这一点） */
    await page.evaluate(() => {
      const c = document.querySelector('#dtTbl tr.dt-company .dt-toggle');
      if (c) c.click();
    });
    await sleep(200);
    await page.evaluate(r => {
      const g = document.querySelector('#dtTbl tr.dt-group[data-region="' +
        CSS.escape(r) + '"] .dt-toggle');
      if (g && g.textContent.trim() === '−') g.click();
    }, target);
    await sleep(300);

    await sleep(350);
    console.log('=== 2. 排序 ===');
    await page.evaluate(() => document.querySelector('#dtTbl thead [data-sort="company"]').click());
    await waitFor(page, () => document.querySelector('[data-sort="company"] .dt-arrow').textContent === '↑', 8000, 'sort asc');
    const sorted = await page.evaluate(() => ({
      arrow: document.querySelector('[data-sort="company"] .dt-arrow').textContent,
      first: document.querySelector('#dtTbl tr.dt-group td').textContent.trim()
    }));
    console.log('    ' + JSON.stringify(sorted));
    chk('点表头切换排序方向（组仍全折叠）', sorted.arrow === '↑' && await page.evaluate(() =>
      document.querySelectorAll('#dtTbl tbody tr.dt-child').length === 0), sorted);

    console.log('=== 3. 文本列筛选（取值 + 搜索）===');
    await page.click('#dtTbl thead .dt-funnel[data-col="region"]');
    await waitFor(page, () => document.querySelectorAll('#dtPop .dt-list label').length > 0, 8000, 'region values');
    const picked = await page.evaluate(() => {
      const l = document.querySelector('#dtPop .dt-list label');
      return { v: l.querySelector('input').value, n: Number(l.querySelector('i').textContent.replace(/,/g, '')) };
    });
    await page.click('#dtPop .dt-list label input');
    await page.click('#dtPop [data-act="apply"]');
    await waitFor(page, t => Number(document.getElementById('dtMeta').textContent.split(' ')[0].replace(/,/g, '')) < t,
      8000, 'filter applied', base.total);
    const filtered = await page.evaluate(() => ({
      meta: document.getElementById('dtMeta').textContent,
      regions: [...new Set([...document.querySelectorAll('#dtTbl tbody tr.dt-group [data-toggle-region]')].map(e => e.dataset.toggleRegion))],
      on: document.querySelectorAll('.dt-funnel.on').length
    }));
    console.log('    ' + JSON.stringify(picked) + ' -> ' + JSON.stringify(filtered));
    chk('取值筛选生效且列头高亮', filtered.regions.length === 1 &&
      filtered.regions[0] === picked.v && metaNum(filtered.meta) === picked.n && filtered.on === 1, filtered);

    await page.click('#dtTbl thead .dt-funnel[data-col="region"]');
    await page.type('#dtPop [data-f="search"]', '西区');
    await waitFor(page, () => {
      const ls = [...document.querySelectorAll('#dtPop .dt-list label')];
      return ls.length > 0 && ls.every(l => l.querySelector('input').value.indexOf('西区') >= 0);
    }, 8000, 'search 西区');
    const searchList = await page.evaluate(() => [...document.querySelectorAll('#dtPop .dt-list label')]
      .map(l => l.querySelector('input').value));
    chk('表头搜索缩小取值列表', searchList.length >= 1 && searchList.every(v => v.indexOf('西区') >= 0), searchList);
    await page.click('#dtPop .dt-list label input');
    await page.click('#dtPop [data-act="apply"]');
    await waitFor(page, () => {
      const rs = [...new Set([...document.querySelectorAll('#dtTbl tbody tr.dt-group [data-toggle-region]')]
        .map(e => e.dataset.toggleRegion))];
      return rs.length === 1 && rs[0] === '西区';
    }, 8000, 'filter 西区 applied');
    chk('搜索后筛选命中西区', true);

    console.log('=== 3b. 表头取值全选 ===');
    await page.click('#dtReset');
    await sleep(700);
    await page.click('#dtTbl thead .dt-funnel[data-col="region"]');
    await waitFor(page, () => document.querySelectorAll('#dtPop .dt-list label').length > 0, 8000, 'dt values');
    const dtAll = await page.evaluate(() => ({
      btn: !!document.querySelector('#dtPop [data-act="all"]'),
      total: document.querySelectorAll('#dtPop .dt-list label').length
    }));
    await page.click('#dtPop [data-act="all"]');
    const dtSel = await page.evaluate(() => document.querySelectorAll('#dtPop .dt-list input:checked').length);
    console.log('    ' + JSON.stringify(dtAll) + ' checked=' + dtSel);
    chk('Detail 表头 All 全选当前取值', dtAll.btn && dtSel === dtAll.total, { dtAll, dtSel });
    await page.click('#dtPop [data-act="clear"]');
    await sleep(400);

    console.log('=== 4. 数值列区间筛选 ===');
    await page.click('#dtReset');
    await sleep(700);
    await page.click('#dtTbl thead .dt-funnel[data-col="amount"]');
    await page.type('#dtPop [data-f="min"]', '1000000');
    await page.click('#dtPop [data-act="apply"]');
    await waitFor(page, t => Number(document.getElementById('dtMeta').textContent.split(' ')[0].replace(/,/g, '')) < t,
      8000, 'range applied', base.total);
    const rangedMeta = await page.evaluate(() => ({
      meta: document.getElementById('dtMeta').textContent,
      on: document.querySelectorAll('.dt-funnel.on').length
    }));
    console.log('    ' + JSON.stringify(rangedMeta));
    chk('金额下限生效', rangedMeta.on === 1 && metaNum(rangedMeta.meta) > 0, rangedMeta);
    /* 展开一个区域 + 公司验证筛选结果金额 */
    const rr = await page.evaluate(async () => {
      const r = await fetch('/api/board/detail?min_amount=1000000'); return (await r.json()).regionTotals;
    });
    const small = rr.slice().sort((a, b) => a.n - b.n)[0].region;
    await page.evaluate(v => document.querySelector('#dtTbl tr.dt-group[data-region="' + CSS.escape(v) +
      '"] .dt-toggle').click(), small);
    await sleep(200);
    await page.click('#dtTbl tr.dt-company .dt-toggle');
    await waitFor(page, () => document.querySelectorAll('#dtTbl tr.dt-child').length > 0, 8000, 'range rows');
    chk('筛选后明细金额均 ≥ 下限', await page.evaluate(() =>
      [...document.querySelectorAll('#dtTbl tr.dt-child td:nth-child(8)')]
        .every(e => Number(String(e.textContent).replace(/[^\d.-]/g, '')) >= 1000000)));
    await page.click('#dtReset');
    await waitFor(page, t => Number(document.getElementById('dtMeta').textContent.split(' ')[0].replace(/,/g, '')) === t,
      8000, 'reset filters', base.total);
    chk('Clear filters 复原', metaNum(await page.evaluate(() =>
      document.getElementById('dtMeta').textContent)) === base.total);

    console.log('=== 4b. 日期范围切片器 ===');
    chk('Detail 有日期范围切片器', await page.evaluate(() =>
      !!document.getElementById('dtFrom') && !!document.getElementById('dtTo')));
    const expect = await page.evaluate(async () => {
      const r = await fetch('/api/board/detail?dateFrom=2026-11-01&dateTo=2026-11-15');
      return (await r.json()).total;
    });
    await page.evaluate(() => {
      const f = document.getElementById('dtFrom'), t = document.getElementById('dtTo');
      f.value = '2026-11-01'; f.dispatchEvent(new Event('change', { bubbles: true }));
      t.value = '2026-11-15'; t.dispatchEvent(new Event('change', { bubbles: true }));
    });
    await waitFor(page, n => Number(document.getElementById('dtMeta')
      .textContent.split(' ')[0].replace(/,/g, '')) === n,
      12000, 'date range', expect).catch(() => {});
    const dateMeta = metaNum(await page.evaluate(() =>
      document.getElementById('dtMeta').textContent));
    chk('日期范围生效（行数=接口同区间）', dateMeta === expect && dateMeta < base.total,
      { dateMeta, expect, all: base.total });

    const xyr = await page.evaluate(async () => {
      const a = await (await fetch('/api/board/detail?dateFrom=2026-03-01&dateTo=2026-09-30')).json();
      const b = await (await fetch('/api/board/detail?fy=FY2627&dateFrom=2026-07-01&dateTo=2026-09-30')).json();
      return { all: a.total, inFy: b.total };
    });
    chk('日期范围可跨财年（指定日期时不再叠加财年）', xyr.all > xyr.inFy * 1.5, xyr);
    const fy0 = await page.$eval('#fySel', e => e.value);
    const fyOther = await page.$$eval('#fySel option', o => o.map(x => x.value))
      .then(a => a.find(v => v !== fy0));
    if (fyOther) {
      await page.select('#fySel', fyOther);
      await sleep(1500);
      chk('换财年不清空日期范围', await page.evaluate(() =>
        document.getElementById('dtFrom').value === '2026-11-01' &&
        document.getElementById('dtTo').value === '2026-11-15' &&
        /~/.test(document.getElementById('dtMeta').textContent)));
      await page.select('#fySel', fy0);
      await sleep(1500);
    }
    await page.click('#dtReset');
    await waitFor(page, t => Number(document.getElementById('dtMeta')
      .textContent.split(' ')[0].replace(/,/g, '')) === t,
      8000, 'reset date', base.total).catch(() => {});
    chk('Clear filters 同时清空日期', await page.evaluate(() =>
      !document.getElementById('dtFrom').value && !document.getElementById('dtTo').value) &&
      metaNum(await page.evaluate(() => document.getElementById('dtMeta').textContent)) === base.total);

    console.log('=== 5. 币种 ===');
    const rmbFirst = await page.evaluate(async () => {
      const r = await fetch('/api/board/detail?sort=region&dir=asc'); return (await r.json()).regionTotals[0].amount;
    });
    await page.click('#curSeg button[data-cur="RM"]');
    await waitFor(page, () => document.querySelector('#dtTbl thead th:nth-child(8)').textContent.indexOf('(RM)') >= 0,
      8000, 'currency RM');
    const rmFirst = await page.evaluate(async () => {
      const r = await fetch('/api/board/detail?sort=region&dir=asc&cur=RM'); return (await r.json()).regionTotals[0].amount;
    });
    console.log('    RMB ' + rmbFirst + ' -> RM ' + rmFirst);
    chk('币种切换同步刷新 Detail（RMB/1.5）', Math.abs(rmFirst - rmbFirst / 1.5) < 0.05, { rmbFirst, rmFirst });

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
