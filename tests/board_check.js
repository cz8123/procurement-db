/**
 * 采购支出分析看板回归测试
 *
 * 跑法（服务先起）：
 *   NODE_PATH="<puppeteer-core 所在 node_modules>" node tests/board_check.js
 *
 * 覆盖：首屏渲染、KPI 口径、支出类型修复、5 张图、交叉筛选、
 *       下拉/chips/重置、货币、财年、抽屉钻取、明细排序搜索翻页、导出、旧页下线。
 */
const puppeteer = require('puppeteer-core');
const http = require('http');
/** Node 侧直连（不跟随重定向），避免在页面里 fetch 制造 404 console error */
function httpStatus(path) {
  return new Promise(resolve => {
    http.get(BASE + path, r => { r.resume(); resolve(r.statusCode); })
      .on('error', () => resolve(0));
  });
}

const BASE = process.env.BOARD_BASE || 'http://127.0.0.1:5000';
const CHROME = process.env.CHROME_PATH || 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const sleep = ms => new Promise(r => setTimeout(r, ms));

let pass = 0, fail = 0;
function chk(name, ok, detail) {
  if (ok) { pass++; console.log('  OK  ' + name); }
  else { fail++; console.log('  XX  ' + name + (detail !== undefined ? '  ' + JSON.stringify(detail) : '')); }
}
async function waitFor(pg, fn, timeout, label) {
  const t0 = Date.now();
  while (Date.now() - t0 < (timeout || 6000)) {
    const v = await pg.evaluate(fn);
    if (v) return v;
    await sleep(90);
  }
  throw new Error('waitFor timeout: ' + (label || ''));
}

(async () => {
  const browser = await puppeteer.launch({
    executablePath: CHROME, headless: 'new', args: ['--no-sandbox']
  });
  const page = await browser.newPage();
  await page.setViewport({ width: 1600, height: 1100, deviceScaleFactor: 1 });
  const errs = [];
  page.on('pageerror', e => errs.push('PAGEERROR ' + e.message));
  page.on('console', m => { if (m.type() === 'error') errs.push('CONSOLE ' + m.text()); });

  const num = sel => page.$eval(sel, e => e.textContent.trim()).catch(() => null);
  const count = sel => page.$$eval(sel, a => a.length).catch(() => 0);

  try {
    console.log('=== 1. 首屏 ===');
    await page.goto(BASE + '/dash?t=' + Date.now(), { waitUntil: 'networkidle0' });
    await waitFor(page, () => document.querySelectorAll('#fcTbl tbody tr').length > 0, 9000, 'contract rows');

    /* 期望值从 API 现场取，不写死金额/单据数：新数据导入后这些数字本来就会变。
       仍校验页面显示与后端口径严格一致（格式化规则同 board.js 的 money/nf）。 */
    const BD = await page.evaluate(() => fetch('/api/board/data?fy=FY2627').then(r => r.json()));
    const kv = k => (BD.kpi || []).find(x => x.k === k) || {};
    const fmtM = v => v == null ? '—' : '¥' + Number(v / 1e6).toFixed(1) + 'M';
    const fmtI = v => v == null ? '—' : Number(v).toLocaleString('en-US');
    const AMT0 = fmtM(kv('amount').v);
    await page.evaluate(v => { window.__AMT0 = v; }, AMT0);  // waitFor 回调在浏览器上下文执行，取不到 Node 变量
    const CT = await page.evaluate(() => fetch('/api/board/contracts?limit=5').then(r => r.json()));
    const CTQ = await page.evaluate(() => fetch('/api/board/contracts?q=' +
      encodeURIComponent('代驾') + '&limit=5').then(r => r.json()));
    await page.evaluate(o => {
      window.__FCT = o.t; window.__FCQ = o.q; window.__FCCP = o.cp; window.__FCE = o.e;
    }, { t: CT.total, q: CTQ.total, cp: (CT.types.find(x => x.k === 'CP') || {}).n,
         e: CT.summary.expired });

    chk('7 张 KPI 卡', await count('.kpi') === 7, await count('.kpi'));
    chk('含 PO 实付金额卡', await count('.kpi[data-k=poSpend]') === 1);
    const poTxt = await num('.kpi[data-k=poSpend] .k-val');
    /* PO 实付金额 = PO 表「总支付总额」按 PRPO 单号汇总（Cancelled 不计），
       与 PO Paid 同源；期望值现场从 API 取，避免每次导入新数据都要改测试 */
    chk('PO 实付金额显示为金额', /^[¥R]/.test(poTxt || ''), poTxt);
    chk('PO 实付金额与库一致', poTxt === fmtM(kv('poSpend').v), poTxt + ' / ' + fmtM(kv('poSpend').v));
    chk('6 组筛选下拉（公司收在 Region 内，含采购方式）', await count('.fdrop') === 6, await count('.fdrop'));
    chk('Region 下拉含公司一段',
      await count('.fdrop[data-k=region] .fsec') === 2,
      await count('.fdrop[data-k=region] .fsec'));
    chk('月度堆叠柱 > 0', await count('#chMonthly rect.seg') > 0);
    chk('月度画满 12 个财月', (await page.$eval('#chMonthly svg',
      e => (e.textContent.match(/Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec/g) || []).length)) >= 12);
    chk('帕累托 11 类', await count('#chPareto rect.pbar') === 11, await count('#chPareto rect.pbar'));
    chk('环形 4 个支出类型', await count('#chDonut [data-exp]') === 4, await count('#chDonut [data-exp]'));
    chk('采购方式环形 = API methodDonut 条数', await count('#chMethodDonut [data-exp]') ===
      (await page.evaluate(async () => (await (await fetch('/api/board/data?cur=RMB')).json()).methodDonut.length)),
      await count('#chMethodDonut [data-exp]'));
    chk('热力 10×8 = 80 格', await count('#chHeat .hcell') === 80, await count('#chHeat .hcell'));
    chk('Top10 供应商 10 条', await count('#chTop .trow') === 10, await count('#chTop .trow'));
    chk('协议表默认 50 行', await count('#fcTbl tbody tr') === 50, await count('#fcTbl tbody tr'));
    chk('默认财年 FY2627', await page.$eval('#fySel', e => e.value) === 'FY2627');
    chk('副标题含当前单据数（Orders）', new RegExp(fmtI(kv('txn').v) + '\\s*orders').test(await num('.hd-sub') || ''));
    chk('标题用原版 Procurement Data Dashboard',
      await num('h1') === 'Procurement Data Dashboard', await num('h1'));
    chk('logo 用原素材 logo-sime.png 且已加载',
      await page.$eval('.hd-logo', e => /logo-sime\.png/.test(e.getAttribute('src') || '') &&
        e.complete && e.naturalWidth > 0));

    console.log('=== 2. KPI 口径 ===');
    const k0 = await page.evaluate(() => {
      const o = {};
      document.querySelectorAll('.kpi').forEach(e => { o[e.dataset.k] = e.querySelector('.k-val').textContent; });
      return o;
    });
    chk('采购总额与库一致', k0.amount === AMT0, k0);
    chk('集采金额与库一致', k0.cp === fmtM(kv('cp').v), k0.cp + ' / ' + fmtM(kv('cp').v));
    chk('单据数与库一致', k0.txn === fmtI(kv('txn').v), k0.txn + ' / ' + fmtI(kv('txn').v));
    chk('供应商数与库一致', k0.suppliers === fmtI(kv('suppliers').v), k0.suppliers + ' / ' + fmtI(kv('suppliers').v));
    chk('中位周期按天展示', /\bd$/.test(k0.days || ''), k0.days);

    console.log('=== 3. 支出类型（dash_build 字段错位修复） ===');
    const exps = await page.evaluate(() =>
      fetch('/api/board/data?fy=FY2627').then(r => r.json()).then(d => d.donut.map(x => x.k)));
    chk('含 OPEX / TRADE / A&P / CAPEX',
      ['OPEX', 'TRADE', 'A&P', 'CAPEX'].every(k => exps.indexOf(k) >= 0), exps);
    const expSum = await page.evaluate(() =>
      fetch('/api/board/data?fy=FY2627').then(r => r.json()).then(d =>
        d.donut.reduce((s, x) => s + x.v, 0)));
    chk('4 类合计 = 总额',
      Math.abs(expSum - kv('amount').v) < 10, expSum + ' / ' + kv('amount').v);

    console.log('=== 4. 交叉筛选 ===');
    await page.evaluate(() => document.querySelector('#chMonthly rect.seg').dispatchEvent(
      new MouseEvent('click', { bubbles: true })));
    await waitFor(page, () => document.querySelectorAll('.chip').length === 1 &&
      document.querySelector('.kpi[data-k=amount] .k-val').textContent !== window.__AMT0,
      8000, 'region chip + amount');
    const a1 = await num('.kpi[data-k=amount] .k-val');
    chk('点区域柱 → 1 个 chip', await count('.chip') === 1);
    chk('点区域柱 → 金额跟着变', a1 !== k0.amount, a1);
    chk('chip 标注 Region', /Region/.test(await num('.chip') || ''));

    await page.evaluate(() => document.querySelector('#chPareto rect.pbar').dispatchEvent(
      new MouseEvent('click', { bubbles: true })));
    await waitFor(page, () => document.querySelectorAll('.chip').length === 2, 6000, 'cat chip');
    chk('点帕累托柱 → 叠加筛选', await count('.chip') === 2);

    await page.click('#btnReset');
    await waitFor(page, () => document.querySelectorAll('.chip').length === 0 &&
      document.querySelector('.kpi[data-k=amount] .k-val').textContent === window.__AMT0,
      8000, 'reset');
    chk('重置 → chip 清空', await count('.chip') === 0);
    chk('重置 → 金额复原', await num('.kpi[data-k=amount] .k-val') === k0.amount);

    console.log('=== 5. 环形筛选 ===');
    const pt = await page.evaluate(() => {
      const el = document.querySelector('#chDonut .dseg');
      if (!el) return null;
      const bb = el.getBBox(), svg = el.ownerSVGElement, r = svg.getBoundingClientRect();
      const vb = svg.viewBox.baseVal, sx = r.width / vb.width, sy = r.height / vb.height;
      for (let fy = 0.1; fy <= 0.9; fy += 0.08) {
        for (let fx = 0.1; fx <= 0.9; fx += 0.04) {
          const x = bb.x + bb.width * fx, y = bb.y + bb.height * fy;
          if (el.isPointInFill(new DOMPoint(x, y)))
            return { x: r.left + (x - vb.x) * sx, y: r.top + (y - vb.y) * sy };
        }
      }
      return null;
    });
    chk('找到环形扇区可点坐标', pt !== null);
    if (pt) {
      await page.mouse.click(pt.x, pt.y);
      await waitFor(page, () => document.querySelectorAll('.chip').length === 1 &&
        document.querySelector('.kpi[data-k=amount] .k-val').textContent !== window.__AMT0,
        8000, 'exp chip + amount');
      chk('点扇区 → Expense Type chip', /Expense Type/.test(await num('.chip') || ''));
      chk('点扇区 → 金额变化', (await num('.kpi[data-k=amount] .k-val')) !== k0.amount);
    }
    await page.click('#btnReset');

    console.log('=== 5b. 采购方式环形：两层下钻 ===');
    const pickSeg = v => page.evaluate(val => {
      const el = [...document.querySelectorAll('#chMethodDonut [data-exp]')]
        .find(e => e.dataset.exp === val);
      if (!el) return false;
      el.dispatchEvent(new MouseEvent('click', { bubbles: true }));
      return true;
    }, v);

    /* 第一层点 SS-Exempt → 下钻到豁免理由层（不产生 chip） */
    chk('点 SS-Exempt 进入下钻', await pickSeg('SS-Exempt'));
    await waitFor(page, () => /Exemption Reason/.test(document.getElementById('mDonutTitle').textContent) &&
      document.querySelectorAll('#chMethodDonut [data-exp]').length > 1, 8000, 'exempt drill');
    const drill1 = await page.evaluate(() => ({
      title: document.getElementById('mDonutTitle').textContent,
      keys: [...document.querySelectorAll('#chMethodDonut [data-exp]')].map(e => e.dataset.exp),
      chips: document.querySelectorAll('.chip').length,
      back: !document.querySelector('.v-back[data-clear=method]').hidden
    }));
    console.log('    ' + JSON.stringify(drill1));
    chk('下钻标题/理由列表/返回按钮正确', /SS-Exempt/.test(drill1.title) &&
      drill1.keys.indexOf('Frame Contract') >= 0 && drill1.keys.length >= 5 &&
      drill1.chips === 0 && drill1.back, drill1);

    /* 第二层点一个理由 → 交叉筛选（方式 + 理由两个 chip） */
    const mdBefore = await page.$eval('.kpi[data-k=amount] .k-val', e => e.textContent.trim());
    await pickSeg('Frame Contract');
    await waitFor(page, t => document.querySelectorAll('.chip').length === 2 &&
      document.querySelector('.kpi[data-k=amount] .k-val').textContent.trim() !== t,
      10000, 'reason filter', mdBefore);
    const reasonChips = await page.evaluate(() =>
      [...document.querySelectorAll('.chip')].map(e => e.textContent.trim()));
    const mdAfter = await num('.kpi[data-k=amount] .k-val');
    chk('点理由 → Method + Exemption Reason 两个 chip',
      reasonChips.some(c => /Procurement Method/.test(c) && /SS-Exempt/.test(c)) &&
      reasonChips.some(c => /Exemption Reason/.test(c) && /Frame Contract/.test(c)), reasonChips);
    chk('点理由 → 金额收窄到该理由', mdAfter !== Number(mdBefore.replace(/[^\d.]/g, '')),
      { before: mdBefore, after: mdAfter });

    /* 返回：退出下钻并清掉方式/理由筛选，回到第一层 */
    await page.evaluate(() => document.querySelector('.v-back[data-clear=method]').click());
    await waitFor(page, () => /Spend by Method/.test(
      document.getElementById('mDonutTitle').textContent) &&
      document.querySelectorAll('.chip').length === 0, 10000, 'exit drill');
    chk('返回退出下钻并清空筛选', await page.evaluate(() =>
      document.querySelector('.v-back[data-clear=method]').hidden));

    /* SS-Non Exempt 走另一张理由表 */
    await pickSeg('SS-Non Exempt');
    await waitFor(page, () => /Single Source Reason/.test(
      document.getElementById('mDonutTitle').textContent) &&
      document.querySelectorAll('#chMethodDonut [data-exp]').length > 1, 8000, 'single drill');
    const drill2 = await page.evaluate(() => [...document.querySelectorAll('#chMethodDonut [data-exp]')]
      .map(e => e.dataset.exp));
    console.log('    ' + JSON.stringify(drill2));
    chk('SS-Non Exempt 下钻到单一来源理由', drill2.indexOf('Inadequate Competition') >= 0, drill2);
    await page.evaluate(() => document.querySelector('.v-back[data-clear=method]').click());
    await waitFor(page, () => /Spend by Method/.test(
      document.getElementById('mDonutTitle').textContent), 8000, 'back method');

    /* 非单一来源方式（Quotation）仍走普通交叉筛选 */
    await pickSeg('Quotation');
    await waitFor(page, () => document.querySelectorAll('.chip').length === 1 &&
      /Procurement Method/.test(document.querySelector('.chip').textContent), 8000, 'quotation chip');
    chk('其它采购方式仍为普通筛选', true);
    await page.click('#btnReset');
    await waitFor(page, () => document.querySelectorAll('.chip').length === 0, 6000, 'reset2');

    console.log('=== 6. 下拉与 chips ===');
    await page.click('.fdrop[data-k=category] .fd-btn');
    await sleep(200);
    chk('下拉已展开', await page.$eval('.fdrop[data-k=category]', e => e.classList.contains('open')));
    await page.click('.fdrop[data-k=category] .fpop-b label');
    await waitFor(page, () => document.querySelectorAll('.chip').length === 1, 6000, 'cat dropdown');
    chk('勾选 → chip 出现', /Category/.test(await num('.chip') || ''));
    chk('勾选 → 按钮显示取值', (await num('.fdrop[data-k=category] .fd-val')) === 'Accessory');
    chk('勾选 → 面板保持展开',
      await page.$eval('.fdrop[data-k=category]', e => e.classList.contains('open')));
    await page.keyboard.press('Escape');   // 收起浮层，否则 .fpop 盖住 chip 的 ×
    await sleep(150);
    await page.click('.chip button');
    await waitFor(page, () => document.querySelectorAll('.chip').length === 0 &&
      document.querySelector('.kpi[data-k=amount] .k-val').textContent === window.__AMT0,
      8000, 'chip remove');
    chk('点 chip × → 撤销筛选', await num('.kpi[data-k=amount] .k-val') === k0.amount);
    await page.keyboard.press('Escape');

    /* 公司是 Region 的下级（收在同一个下拉的第二段）：选区域后 Company 选项收窄 */
    const coSel = '.fdrop[data-k=region] .fsec[data-k=company] .fpop-b label';
    await page.click('.fdrop[data-k=region] .fd-btn');
    await waitFor(page, () => document.querySelectorAll('.fdrop[data-k=region] .fsec[data-k=company] .fpop-b label').length > 20,
      8000, 'company list');
    const coAll = await count(coSel);
    await page.evaluate(n => { window.__COALL = n; }, coAll);
    await page.click('.fdrop[data-k=region] .fsec[data-k=region] .fpop-b label');
    await waitFor(page, () => document.querySelectorAll('.fdrop[data-k=region] .fsec[data-k=company] .fpop-b label').length < window.__COALL,
      8000, 'company cascade');
    const coFew = await count(coSel);
    chk('Region → Company 级联收窄', coAll > 20 && coFew > 0 && coFew < coAll, [coAll, coFew]);
    await page.keyboard.press('Escape');
    await page.click('#btnReset');
    await waitFor(page, () => document.querySelectorAll('.fdrop[data-k=region] .fsec[data-k=company] .fpop-b label').length === window.__COALL,
      8000, 'company reset');

    console.log('=== 7. 货币 / 财年 ===');
    await page.click('#curSeg button[data-cur=RM]');
    await waitFor(page, () => /^RM /.test(document.querySelector('.kpi[data-k=amount] .k-val').textContent), 6000, 'RM');
    const rmAmt = await num('.kpi[data-k=amount] .k-val');
    chk('切 RM 符号正确', /^RM /.test(rmAmt), rmAmt);
    chk('RM = RMB / 1.5',
      Math.abs(parseFloat(rmAmt.replace(/[^\d.]/g, '')) - Number(kv('amount').v) / 1e6 / 1.5) < 0.6,
      rmAmt + ' / ' + Number(kv('amount').v) / 1e6 / 1.5);
    await page.click('#curSeg button[data-cur=RMB]');
    await waitFor(page, () => /^¥/.test(document.querySelector('.kpi[data-k=amount] .k-val').textContent), 6000, 'RMB');
    chk('切回 RMB', await num('.kpi[data-k=amount] .k-val') === k0.amount);

    await page.select('#fySel', 'FY2526');
    await waitFor(page, () => document.querySelector('.kpi[data-k=amount] .k-val').textContent !== window.__AMT0, 8000, 'FY2526');
    chk('切财年 → FY2526 金额', (await num('.kpi[data-k=amount] .k-val')).indexOf('698') >= 0,
      await num('.kpi[data-k=amount] .k-val'));
    await page.select('#fySel', 'FY2627');
    await waitFor(page, () => document.querySelector('.kpi[data-k=amount] .k-val').textContent === window.__AMT0, 8000, 'FY2627');

    console.log('=== 8. 明细钻取抽屉 ===');
    await waitFor(page, () => document.querySelectorAll('#chTop .trow').length === 10, 8000, 'top10');
    await page.evaluate(() => document.querySelector('#chTop .trow').dispatchEvent(
      new MouseEvent('click', { bubbles: true })));
    await waitFor(page, () => document.querySelector('#drawer').classList.contains('on'), 3000, 'drawer');
    await waitFor(page, () => document.querySelectorAll('#drawerTbl tbody tr').length > 0, 8000, 'drawer rows');
    chk('Top10 点开抽屉', await page.$eval('#drawer', e => e.classList.contains('on')));
    chk('抽屉有明细行', await count('#drawerTbl tbody tr') > 0);
    chk('抽屉标题是供应商', (await num('#drawerTitle') || '').length > 2 &&
      (await num('#drawerTitle')) !== 'Details', await num('#drawerTitle'));
    chk('抽屉有合计', /total/.test(await num('#drawerSub') || ''));
    chk('抽屉补充 PO Paid 列',
      (await page.$$eval('#drawerTbl thead th', a => a.map(e => e.textContent.trim())))
        .indexOf('PO Paid') >= 0);
    const dHeads = await page.$$eval('#drawerTbl thead th', a => a.map(e => e.textContent.trim()));
    chk('抽屉按公司聚合（表头）', dHeads[0] === 'Company' && dHeads.indexOf('Avg Days') >= 0, dHeads);
    chk('抽屉不显示 PRPO 单号', dHeads.indexOf('PR No.') < 0 &&
      dHeads.indexOf('No.') < 0 && dHeads.indexOf('Date') < 0, dHeads);
    chk('抽屉表头 7 列', dHeads.length === 7, dHeads);
    const firstRow = await page.$$eval('#drawerTbl tbody tr:first-child td',
      a => a.map(e => e.textContent.trim()));
    chk('抽屉一行一家公司', firstRow.length === 7 && firstRow[0].length > 1, firstRow);
    chk('公司行有单数与金额', /^\d+$/.test(firstRow[2] || '') && /[¥R]/.test(firstRow[4] || ''), firstRow);
    chk('抽屉小标题写 companies',
      /companies/.test(await num('#drawerSub') || ''), await num('#drawerSub'));
    chk('抽屉打开时背景不滚动',
      await page.evaluate(() => getComputedStyle(document.body).overflow === 'hidden'));
    chk('抽屉无横向滚动条', await page.evaluate(() => {
      const e = document.querySelector('.drawer .tbl-wrap');
      return e.scrollWidth - e.clientWidth <= 1;
    }));
    const dp0 = await num('#drawerPager');
    await page.evaluate(s => { window.__DP0 = s; }, dp0);
    const canNext = await page.evaluate(() => Array.from(
      document.querySelectorAll('#drawerPager button[data-pg]'))
      .some(b => /Next/.test(b.textContent) && !b.disabled));
    if (canNext) {
      await page.evaluate(() => Array.from(document.querySelectorAll('#drawerPager button[data-pg]'))
        .find(b => /Next/.test(b.textContent)).click());
      await waitFor(page, () => document.querySelector('#drawerPager').textContent !== window.__DP0,
        8000, 'drawer next');
      chk('抽屉 Next 翻页生效', (await num('#drawerPager')) !== dp0);
    } else {
      /* 按公司聚合后，单个供应商下的公司数常常不足一页，此时 Next 应当被禁用 */
      chk('公司数不足一页时 Next 禁用', await page.evaluate(() => {
        const b = Array.from(document.querySelectorAll('#drawerPager button[data-pg]'))
          .find(x => /Next/.test(x.textContent));
        return !b || b.disabled;
      }));
    }
    await page.keyboard.press('Escape');
    await sleep(300);
    chk('Esc 关闭抽屉', !(await page.$eval('#drawer', e => e.classList.contains('on'))));
    chk('关闭后背景滚动恢复',
      await page.evaluate(() => getComputedStyle(document.body).overflow !== 'hidden'));

    await page.evaluate(() => document.querySelector('#chHeat .hcell').dispatchEvent(
      new MouseEvent('click', { bubbles: true })));
    await waitFor(page, () => document.querySelector('#drawer').classList.contains('on'), 3000, 'heat drawer');
    await waitFor(page, () => document.querySelectorAll('#drawerTbl tbody tr').length > 0, 8000, 'heat drawer rows');
    chk('热力格点开抽屉', await page.$eval('#drawer', e => e.classList.contains('on')));
    await page.evaluate(() => document.querySelector('#drawerClose').click());
    await sleep(350);

    console.log('=== 8.5 供应商英文名 + Top10 局部筛选改采购方法 ===');
    /* 库里供应商名是「中文全称-English name」：图上显示英文段，hover 给全称 */
    const supInfo = await page.evaluate(() => {
      const tr = document.querySelector('#chTop .trow');
      return { full: tr.dataset.sup, label: tr.querySelector('text').textContent.trim() };
    });
    chk('Top10 数据源仍是全称（中文-英文）',
      /[\u4e00-\u9fa5]/.test(supInfo.full) && supInfo.full.indexOf('-') > 0, supInfo.full);
    chk('Top10 轴上显示英文段', !/[\u4e00-\u9fa5]/.test(supInfo.label), supInfo.label);
    await page.evaluate(() => {
      const tr = document.querySelector('#chTop .trow');
      const r = tr.getBoundingClientRect();
      tr.dispatchEvent(new MouseEvent('mousemove',
        { bubbles: true, clientX: r.left + 10, clientY: r.top + 6 }));
    });
    await sleep(200);
    const topTip = await num('#tip');
    chk('Top10 悬停提示给全称', /[\u4e00-\u9fa5]/.test(topTip || ''), (topTip || '').slice(0, 40));
    await page.evaluate(() => document.querySelector('#chTop').dispatchEvent(
      new MouseEvent('mouseleave', { bubbles: true })));

    const heatRows = await page.$$eval('#chHeat text[text-anchor=end]', a =>
      a.map(e => e.textContent.trim()));
    chk('热力图行标签也是英文', heatRows.length === 10 &&
      heatRows.every(t => !/[\u4e00-\u9fa5]/.test(t)), heatRows.slice(0, 3));

    /* 协议表的供应商名是单语种（纯中文/纯英文），英文来自 fct_supplier 反查 */
    const fcSup = await page.$$eval('#fcTbl tbody tr td.supplier', a =>
      a.map(e => ({ t: e.textContent.trim(), title: e.getAttribute('title') || '' })));
    const cnRows = fcSup.filter(x => /[\u4e00-\u9fa5]/.test(x.title));
    const enOfCn = cnRows.filter(x => !/[\u4e00-\u9fa5]/.test(x.t));
    chk('协议表中文供应商多数显示英文名',
      cnRows.length > 0 && enOfCn.length / cnRows.length >= 0.7,
      enOfCn.length + '/' + cnRows.length);
    chk('协议表供应商 hover 仍是原名', cnRows.every(x => x.title !== x.t),
      (cnRows[0] || {}).title);

    /* 源系统常吞掉英文名里的空格：Backend 会用「同名的有空格写法」补回来 */
    const longTok = fcSup.filter(x => x.t.replace(/[,]/g, ' ').split(/\s+/)
      .some(w => w.length > 24));
    chk('协议表英文名已补空格（无超长连写）', longTok.length === 0,
      longTok.slice(0, 2).map(x => x.t));
    /* 具体英文后缀会随源数据版本变（auto supplies / Car Accessories Supplies），
       只断言连写被还原成带空格写法 */
    const fcBody = await page.$eval('#fcTbl tbody', e => e.textContent);
    chk('DongguanZhongfan… 已还原成 Dongguan Zhongfan …',
      /Dongguan Zhongfan /i.test(fcBody) && !/DongguanZhongfan/i.test(fcBody));

    const topLong = await page.$$eval('#chTop .trow text', a => a
      .map(e => e.textContent.trim())
      .filter(t => !/[¥0-9]/.test(t))
      .filter(t => t.split(/\s+/).some(w => w.length > 24)));
    chk('Top10 英文名无超长连写', topLong.length === 0, topLong.slice(0, 2));

    /* 第二个下拉原先是支出类型，改成采购方法 */
    const mthOpts = await page.$$eval('#topMth option', a => a.map(e => e.textContent.trim()));
    chk('第二个筛选是采购方法（取值来自 Procurement Method）',
      mthOpts[0] === 'All methods' && mthOpts.indexOf('Tender') > 0, mthOpts);
    chk('不再有支出类型下拉', (await page.$('#topExp')) === null);

    await page.evaluate(() => {
      window.__prevTop = document.querySelector('#chTop .trow text').textContent;
    });
    await page.select('#topMth', 'Tender');
    await waitFor(page, () => {
      const t = document.querySelector('#chTop .trow text');
      return t && t.textContent !== window.__prevTop;
    }, 9000, 'top by method');
    const apiTop = await page.evaluate(() =>
      fetch('/api/board/top?fy=FY2627&cur=RMB&method=Tender')
        .then(r => r.json()).then(d => d.rows.length));
    chk('按采购方法筛 Top10 → 行数 = 接口返回', (await count('#chTop .trow')) === apiTop, apiTop);
    chk('局部筛选不影响 KPI', (await num('.kpi[data-k=amount] .k-val')) === k0.amount);

    await page.select('#topMth', '');
    await waitFor(page, () => document.querySelectorAll('#chTop .trow').length === 10, 9000, 'top reset');
    chk('清回 All methods → 恢复 10 条', (await count('#chTop .trow')) === 10);

    console.log('=== 8.6 类别明细表（表格 + 数据条） ===');
    chk('类别明细 11 行', await count('#chCat .cd-row') === 11,
      await count('#chCat .cd-row'));
    const cdTotal = await num('#chCat .cd-total');
    chk('合计行含 Total 与 100.0%',
      /Total/.test(cdTotal) && /100\.0%/.test(cdTotal), cdTotal);
    const cdBars = await page.$$eval('#chCat .cd-share i',
      a => a.map(e => parseFloat(e.style.width)));
    /* 11 行 + 合计行；合计条最长，只校验前 11 行单调不增 */
    const cdRowBars = cdBars.slice(0, 11);
    chk('数据条随金额递减',
      cdBars.length === 12 &&
      cdRowBars.every((w, i) => i === 0 || w <= cdRowBars[i - 1] + 0.01),
      [cdBars.length, cdRowBars]);
    chk('数据条与百分比不溢出单元格',
      await page.$$eval('#chCat .cd-share',
        a => a.every(e => e.scrollWidth <= e.clientWidth + 1)));
    const cdFirst = await num('#chCat .cd-row');
    chk('首行是金额最高的类别', /Administration/.test(cdFirst), cdFirst);
    chk('明细表含 Sub-Category / PO Paid 列',
      (await page.$$eval('#chCat thead th', a => a.map(e => e.textContent.trim())))
        .filter(t => t === 'Sub-Category' || t === 'PO Paid').length === 2);
    await page.type('#catQ', 'Administration');
    await waitFor(page, () => document.querySelectorAll('#chCat .cd-row').length === 1,
      5000, 'category search');
    chk('类别搜索框只筛表格行', (await count('#chCat .cd-row')) === 1);
    await page.$eval('#catQ', e => { e.value = ''; e.dispatchEvent(new Event('input', { bubbles: true })); });
    await waitFor(page, () => document.querySelectorAll('#chCat .cd-row').length === 11,
      5000, 'category search clear');

    await page.evaluate(() => document.querySelector('#chCat .cd-row').click());
    await waitFor(page, () => document.querySelectorAll('.chip').length === 1 &&
      document.querySelectorAll('#chCat .cd-row.dv').length > 0,
      10000, 'cat table chip + dim');
    chk('点明细行 → 类别 chip', /Category/.test(await num('.chip') || ''),
      await num('.chip'));
    /* 突出显示（默认开）：其余类别淡化保留，不隐藏 —— 行数仍是 11 */
    chk('突出显示：其余行淡化而非隐藏',
      await count('#chCat .cd-row') === 11 && await count('#chCat .cd-row.dv') === 10,
      { rows: await count('#chCat .cd-row'), dim: await count('#chCat .cd-row.dv') });
    await page.evaluate(() => document.querySelector('#chCat .cd-row').click());
    await waitFor(page, () => document.querySelectorAll('.chip').length === 0 &&
      document.querySelectorAll('#chCat .cd-row.dv').length === 0,
      8000, 'cat table reset');
    chk('再点一次取消筛选', await count('#chCat .cd-row') === 11,
      await count('#chCat .cd-row'));

    console.log('=== 8.7 月度图两种图示切换 ===');
    chk('有图示切换控件', await count('#mSw button') === 2, await count('#mSw button'));
    chk('默认是区域堆叠', (await num('#mTitle')) === 'Monthly Spend by Region' &&
      (await page.evaluate(() => !!document.querySelector('#mSw button[data-m=region].on'))),
      await num('#mTitle'));
    const regionSegs = await count('#chMonthly rect.seg');
    chk('区域堆叠有柱子', regionSegs > 10, regionSegs);
    /* 切换前后「柱宽 / 基线 / 图高 / 图在卡片里的纵向位置」必须完全一致 */
    const geoOf = sel => page.evaluate(s => {
      const svg = document.querySelector('#chMonthly svg');
      const rs = [...svg.querySelectorAll(s)];
      const line = svg.querySelector('line');
      const card = svg.closest('.card');
      return {
        barW: rs.length ? +rs[0].getAttribute('width') : 0,
        baseY: line ? +line.getAttribute('y1') : 0,
        relTop: Math.round(svg.getBoundingClientRect().top - card.getBoundingClientRect().top),
        h: Math.round(svg.getBoundingClientRect().height),
        money: [...svg.querySelectorAll('text')].filter(t => /^¥/.test(t.textContent)).length
      };
    }, sel);
    const geoR = await geoOf('rect.seg');
    chk('区域柱内也有金额', geoR.money > 0, geoR.money);

    const mData = await page.evaluate(() => fetch('/api/board/data?fy=FY2627&cur=RMB')
      .then(r => r.json()).then(d => d.monthly));
    const activeM = mData.total.filter(v => v > 0).length;

    await page.evaluate(() => document.querySelector('#mSw button[data-m=pair]').click());
    await sleep(450);
    const pb = await page.evaluate(() => ({
      title: document.querySelector('#mTitle').textContent.trim(),
      bars: document.querySelectorAll('#chMonthly rect.pseg').length,
      months: new Set([...document.querySelectorAll('#chMonthly rect.pseg')]
        .map(r => r.dataset.m)).size,
      caps: [...document.querySelectorAll('#chMonthly text')]
        .map(t => t.textContent).filter(t => /^¥/.test(t)),
      legend: document.querySelector('#mLegend').textContent,
      /* X 轴改 3 字母缩写后，12 个刻度不再互相压字 */
      longMonths: [...document.querySelectorAll('#chMonthly text')].map(t => t.textContent)
        .filter(t => /^(Jul|Aug|Sep|Oct|Nov|Dec|Jan|Feb|Mar|Apr|May|Jun)$/.test(t))
    }));
    chk('切到堆叠改标题', /Total vs Framework/.test(pb.title), pb.title);
    chk('每月一根柱、按框架/非框架堆叠',
      pb.months >= activeM && pb.bars >= activeM && pb.bars <= activeM * 2,
      [pb.bars, pb.months, activeM]);
    chk('金额写在柱内（不再有柱顶胶囊）', pb.caps.length >= activeM, pb.caps.length);
    /* 窄图（g6）段内标签用整数紧凑格式，宽图用一位小数；两种都认 */
    chk('首段金额与接口一致',
      pb.caps.some(c => {
        const n = parseFloat(c.replace(/[^\d.]/g, ''));
        return Math.abs(n - mData.frame[0] / 1e6) < 0.6 ||
          Math.abs(n - (mData.total[0] - mData.frame[0]) / 1e6) < 0.6;
      }),
      [pb.caps.slice(0, 3), mData.total[0], mData.frame[0]]);
    chk('图例换成 Framework / Non-framework',
      /Framework Contract/.test(pb.legend) && /Non-framework/.test(pb.legend), pb.legend);
    const geoP = await geoOf('rect.pseg');
    chk('切换前后柱宽一致', geoP.barW === geoR.barW && geoP.barW > 0, [geoR.barW, geoP.barW]);
    chk('切换前后基线/图高/位置一致',
      geoP.baseY === geoR.baseY && geoP.h === geoR.h && geoP.relTop === geoR.relTop,
      [geoR, geoP]);
    /* 双柱与区域视图一致，横轴固定 12 个财月 */
    chk('月份用 3 字母缩写并显示 12 个月', pb.longMonths.length === 12, pb.longMonths);

    await page.evaluate(() => document.querySelector('#chMonthly rect.pseg').dispatchEvent(
      new MouseEvent('click', { bubbles: true })));
    await waitFor(page, () => document.querySelector('#drawer').classList.contains('on'),
      8000, 'pair drill');
    await sleep(800);
    chk('点柱下钻到该月', (await num('#drawerTitle')) === mData.labelsLong[0],
      [await num('#drawerTitle'), mData.labelsLong[0]]);
    chk('下钻抽屉有明细', await count('#drawerTbl tbody tr') > 0,
      await count('#drawerTbl tbody tr'));
    await page.evaluate(() => document.querySelector('#drawerClose').click());
    await sleep(400);

    await page.evaluate(() => document.querySelector('#mSw button[data-m=region]').click());
    await sleep(450);
    chk('切回区域堆叠标题', (await num('#mTitle')) === 'Monthly Spend by Region',
      await num('#mTitle'));
    chk('切回后堆叠柱数量不变', await count('#chMonthly rect.seg') === regionSegs,
      await count('#chMonthly rect.seg'));
    chk('切回后图例含 Prior year',
      /Prior year/.test(await num('#mLegend') || ''), await num('#mLegend'));

    console.log('=== 9. 框架协议统计表 ===');
    /* 协议卡已移到 Framework 页：先切过去，否则输入框不可见 */
    await page.click('.pg-tab[data-page=framework]');
    await sleep(350);
    chk('摘要 6 项', await count('.fc-summary .fs') === 6, await count('.fc-summary .fs'));
    const fs = await page.evaluate(() =>
      [...document.querySelectorAll('.fc-summary .fs')].map(e => e.textContent.trim()));
    const usagePct = (CT.summary.usage * 100).toFixed(1) + '%';
    chk('协议数与库一致', fs[0] === 'Agreements' + CT.total, fs[0] + ' / ' + CT.total);
    chk('可加预估与库一致',
      fs[1].indexOf('¥' + Number(CT.summary.cv / 1e6).toFixed(1) + ' M') >= 0, fs[1]);
    chk('整体执行率与库一致', fs[3].indexOf(usagePct) >= 0, fs[3] + ' / ' + usagePct);
    chk('执行率>100% / 已过期与库一致',
      fs[4].indexOf(String(CT.summary.overrun)) >= 0 && fs[5].indexOf(String(CT.summary.expired)) >= 0,
      [fs[4], fs[5], CT.summary.overrun, CT.summary.expired]);
    // 口径：PA 只统计「框架协议方式申请」的采购单（视图 agg 里限定 Exception Reason）
    const faSql = await new Promise(resolve => {
      const { execFileSync } = require('child_process');
      /* 用当前环境可用的 Python（sqlite3 是标准库，任意 Python 都行）；
         不再写死某台机器的 venv 路径 */
      const py = process.env.PYTHON || (process.platform === 'win32' ? 'python' : 'python3');
      try {
        resolve(execFileSync(py,
          ['-c', "import sqlite3;c=sqlite3.connect('procurement.db');print(c.execute(\"select sql from sqlite_master where name='vw_fa_analysis'\").fetchone()[0])"],
          { encoding: 'utf-8' }));
      } catch (e) { resolve(''); }
    });
    chk('视图执行率口径限定框架协议方式申请',
      /豁免单一来源的理由/.test(faSql) && /Ongoing Frame Contract/.test(faSql));
    chk('卡片标注 PA 口径', /PA counts framework-contract POs only/.test(
      await page.$eval('#fcCard .card-note', e => e.textContent)));
    chk('Usage 列有口径提示', /framework-contract POs only/.test(
      await page.$eval('#fcTbl th[data-sort=usage]', e => e.getAttribute('title') || '')));
    const typeOpts = ['All types'].concat(CT.types.map(t => t.k + ' (' + t.n + ')'));
    chk('类型下拉 CP/AAP/DT 与库一致',
      JSON.stringify(await page.$$eval('#fcType option', a => a.map(e => e.textContent))) ===
      JSON.stringify(typeOpts), typeOpts);
    /* 超 100% 的协议未必落在默认第一页 50 行里，改为库级断言 + 页面已渲染状态列 */
    chk('Usage 有超 100% 的记录（库级）', CT.summary.overrun > 0, CT.summary.overrun);
    chk('Usage 列已渲染数据条', await count('#fcTbl td.u-cell .u-bar') > 0);
    chk('Usage 数据条宽度有差异', await page.evaluate(() => {
      const w = [...document.querySelectorAll('#fcTbl td.u-cell .u-bar')]
        .map(e => parseFloat(e.style.width) || 0);
      return new Set(w).size > 1;
    }));
    chk('Remain Days 有红/黄/绿状态',
      await count('#fcTbl .remain.bad') > 0 && await count('#fcTbl .remain.ok') > 0);
    chk('Remain Days 带状态圆点', await count('#fcTbl .remain i') > 0);

    // 合同名搜索：'代驾' 命中 3 份
    await page.type('#fcQ', '代驾');
    await waitFor(page, () => new RegExp('\\b' + window.__FCQ + '\\s*rows').test(document.querySelector('#fcPager').textContent), 8000, 'fc search');
    chk('搜索「代驾」命中数与库一致',
      new RegExp('\\b' + CTQ.total + '\\s*rows').test(await num('#fcPager') || ''));
    await page.$eval('#fcQ', e => { e.value = ''; e.dispatchEvent(new Event('input', { bubbles: true })); });
    await waitFor(page, () => new RegExp('\\b' + window.__FCT + '\\s*rows').test(document.querySelector('#fcPager').textContent), 8000, 'fc clear');

    // 类型筛选（CP=103）
    await page.select('#fcType', 'CP');
    await waitFor(page, () => new RegExp('\\b' + window.__FCCP + '\\s*rows').test(document.querySelector('#fcPager').textContent), 8000, 'fc type');
    chk('筛 CP 与库一致',
      new RegExp('\\b' + (CT.types.find(x => x.k === 'CP') || {}).n + '\\b').test(await num('#fcPager') || ''));
    await page.select('#fcType', '');
    await waitFor(page, () => new RegExp('\\b' + window.__FCT + '\\s*rows').test(document.querySelector('#fcPager').textContent), 8000, 'fc type clear');

    // 状态筛选（已过期=71）
    await page.select('#fcStatus', 'expired');
    await waitFor(page, () => new RegExp('\\b' + window.__FCE + '\\s*rows').test(document.querySelector('#fcPager').textContent), 8000, 'fc status');
    chk('筛已过期与库一致',
      new RegExp('\\b' + CT.summary.expired + '\\b').test(await num('#fcPager') || ''));
    await page.click('#fcReset');
    await waitFor(page, () => new RegExp('\\b' + window.__FCT + '\\s*rows').test(document.querySelector('#fcPager').textContent), 8000, 'fc reset');

    // 按 CV 降序 + 翻页
    await page.click('#fcTbl thead th[data-sort=cv]');
    await waitFor(page, () => /▼/.test(document.querySelector('#fcTbl thead th[data-sort=cv]').textContent), 5000, 'fc sort');
    chk('按 CV 排序有降序标', /▼/.test(await num('#fcTbl thead th[data-sort=cv]') || ''));
    await page.click('#fcPager button[data-pg="2"]');
    await waitFor(page, () => /51–100/.test(document.querySelector('#fcPager').textContent), 8000, 'fc page2');
    chk('协议表翻到第 2 页', /51–100/.test(await num('#fcPager') || ''));

    console.log('=== 10. 导出 / 旧版下线 ===');
    chk('顶栏按钮改为「Export View」', (await num('#btnExport')) === 'Export View',
      await num('#btnExport'));

    /* 导出改走 Node 直连：页面里 fetch 流式中止会刷 ERR_INCOMPLETE_CHUNKED_ENCODING，
       把「0 JS 报错」这条断言污染成假阳性 */
    function csvOf(p) {
      return new Promise(resolve => {
        http.get(BASE + p, r => {
          let s = '', n = 0;
          r.setEncoding('utf8');
          r.on('data', c => { n += c.length; s += c; });
          r.on('end', () => resolve({
            s: r.statusCode, ct: r.headers['content-type'], n: n,
            lines: s.replace(/^\ufeff/, '').replace(/\r?\n$/, '').split('\r\n'),
            head: s.replace(/^\ufeff/, '').split('\r\n').slice(0, 2).join(' | ')
          }));
        }).on('error', () => resolve({ s: 0, lines: [] }));
      });
    }

    const exp = await csvOf('/api/board/contracts.csv?cur=RMB');
    chk('协议导出 CSV 200', exp.s === 200, exp);
    chk('导出类型 text/csv', /text\/csv/.test(exp.ct || ''), exp.ct);
    chk('协议 CSV 行数 + 表头与库一致',
      exp.lines.length === CT.total + 1, exp.lines.length + ' / ' + (CT.total + 1));

    /* 顶栏「导出当前视图」= 当前财年 + 全局筛选下的采购申请明细 */
    const viewAll = await csvOf('/api/board/export.csv?fy=FY2627&cur=RMB&sort=date&dir=desc');
    chk('明细 CSV 200', viewAll.s === 200, viewAll.s);
    chk('明细 CSV 行数 + 表头与库一致',
      viewAll.lines.length === Number(kv('txn').v) + 1, viewAll.lines.length + ' / ' + (Number(kv('txn').v) + 1));
    chk('明细 CSV 表头含 No./Supplier/Amount', /No\./.test(viewAll.head) &&
      /Supplier/.test(viewAll.head) && /Amount/.test(viewAll.head), viewAll.head);

    const viewCat = await csvOf('/api/board/export.csv?fy=FY2627&cur=RMB&category=Accessory');
    chk('明细 CSV 跟随筛选（类别）', viewCat.lines.length < viewAll.lines.length &&
      viewCat.lines.length > 1, [viewCat.lines.length, viewAll.lines.length]);
    const viewRm = await csvOf('/api/board/export.csv?fy=FY2627&cur=RM');
    chk('明细 CSV 跟随货币（表头标 RM）', /Amount \(RM\)/.test(viewRm.head), viewRm.head);

    const legacy = await Promise.all([
      httpStatus('/dash/general'), httpStatus('/dash/analysis'),
      httpStatus('/api/dash/home?fy=FY2627')
    ]);
    chk('旧页 /dash/general 已重定向', legacy[0] === 302, legacy);
    chk('旧页 /dash/analysis 已重定向', legacy[1] === 302, legacy);
    chk('旧 API /api/dash/* 已下线', legacy[2] === 404, legacy);

    console.log('=== 11. 取值计算逻辑说明 ===');
    await page.click('.pg-tab[data-page=overview]');
    await sleep(350);
    const specBtns = await page.$$eval('.spec-btn', a => a.map(e => e.dataset.spec));
    /* 视觉对象 9 个（含 methodDonut + geo）+ 整页 1 + KPI 卡 7 + 筛选下拉 6 = 23 */
    chk('每个视觉对象都有说明按钮', specBtns.length === 23, specBtns);
    chk('按钮覆盖 10 个对象',
      ['global', 'monthly', 'donut', 'methodDonut', 'pareto', 'catdetail',
       'top', 'heat', 'contracts', 'geo']
        .every(k => specBtns.indexOf(k) >= 0), specBtns);
    chk('7 张 KPI 卡各有说明按钮',
      ['amount', 'cp', 'poSpend', 'txn', 'suppliers', 'stores', 'days']
        .every(k => specBtns.indexOf('kpi_' + k) >= 0), specBtns);
    chk('5 个筛选下拉各有说明按钮',
      ['brand', 'category', 'expense', 'level', 'region']
        .every(k => specBtns.indexOf('filter_' + k) >= 0), specBtns);

    const ALL_KEYS = ['global', 'kpi_amount', 'kpi_cp', 'kpi_poSpend', 'kpi_txn',
      'kpi_suppliers', 'kpi_stores', 'kpi_days',
      'filter_brand', 'filter_category', 'filter_expense', 'filter_level',
      'filter_region', 'monthly', 'donut', 'pareto', 'catdetail', 'top', 'heat',
      'contracts'];
    for (const key of ALL_KEYS) {
      await page.evaluate(k => document.querySelector('.spec-btn[data-spec="' + k + '"]').click(), key);
      await waitFor(page, () => document.querySelector('#specPop').classList.contains('on'),
        3000, 'spec open ' + key);
      const sp = await page.evaluate(() => {
        const t = document.querySelector('#specPop');
        const r = t.getBoundingClientRect();
        const body = document.querySelector('#specPop .sp-body').textContent || '';
        return {
          title: (document.querySelector('#spTitle').textContent || '').trim(),
          notes: document.querySelectorAll('#spNotes li').length,
          params: document.querySelectorAll('#spParams .k').length,
          fields: document.querySelectorAll('#spFields .sp-frow').length,
          terms: document.querySelectorAll('#spTerms .sp-term').length,
          body,
          /* 视口高度随测试配置变，别写死 720 */
          inView: r.left >= -1 && r.top >= -1 &&
                  r.right <= innerWidth + 1 && r.bottom <= innerHeight + 1
        };
      });
      chk(key + ' 说明有口径条目', sp.notes > 0, sp.notes);
      chk(key + ' 说明有当前生效条件', sp.params > 0, sp.params);
      chk(key + ' 说明有字段对照', sp.fields > 1, sp.fields);
      chk(key + ' 说明有名词解释', sp.terms > 0, sp.terms);
      /* 不该再出现 SQL 语法 */
      chk(key + ' 说明不含 SQL 语句',
        !/\bSELECT\b|\bFROM\b|\bJOIN\b|\bGROUP BY\b|\bSUM\(|COUNT\(/i.test(sp.body),
        sp.body.slice(0, 120));
      chk(key + ' 浮层在视口内', sp.inView);
      await page.evaluate(() => document.querySelector('#spClose').click());
      await sleep(180);
      chk(key + ' 关闭后隐藏', !(await page.evaluate(() =>
        document.querySelector('#specPop').classList.contains('on'))));
    }

    /* 口径说明里应解释 WHERE 是什么意思 */
    await page.evaluate(() => document.querySelector('.spec-btn[data-spec=global]').click());
    await sleep(300);
    const termsTxt = await page.evaluate(() =>
      document.querySelector('#spTerms').textContent || '');
    chk('名词解释里讲清 WHERE', /WHERE/.test(termsTxt) && /筛选条件/.test(termsTxt),
      termsTxt.slice(0, 120));
    await page.evaluate(() => document.querySelector('#spClose').click());
    await sleep(180);

    /* 数据库字段用中文名 */
    await page.evaluate(() => document.querySelector('.spec-btn[data-spec=kpi_amount]').click());
    await sleep(300);
    const fldTxt = await page.evaluate(() =>
      document.querySelector('#spFields').textContent || '');
    chk('字段对照用数据库中文列名', /含增值税总价/.test(fldTxt) && /采购申请单/.test(fldTxt),
      fldTxt.slice(0, 120));
    await page.evaluate(() => document.querySelector('#spClose').click());
    await sleep(180);

    /* 筛选后「当前生效的条件」应随之变化 */
    await page.evaluate(() => document.querySelector('.fdrop[data-k=category] .fd-btn').click());
    await sleep(220);
    await page.evaluate(() => document.querySelector('.fdrop[data-k=category] .fpop-b label').click());
    await waitFor(page, () => document.querySelectorAll('.chip').length === 1 &&
      document.querySelector('.kpi[data-k=amount] .k-val').textContent !== window.__AMT0,
      8000, 'filter applied');
    await page.evaluate(() => document.querySelector('.spec-btn[data-spec=global]').click());
    await sleep(400);
    const spFiltered = await page.evaluate(() =>
      document.querySelector('#spParams').textContent || '');
    chk('筛选后条件区显示所选类别',
      /类别/.test(spFiltered) && /Accessory/.test(spFiltered),
      spFiltered.slice(0, 120));
    await page.evaluate(() => document.querySelector('#spClose').click());
    await sleep(200);
    await page.evaluate(() => document.querySelector('#btnReset').click());
    await sleep(900);

    console.log('=== 11. 吸顶（滚动时标题栏 / KPI / 筛选不动） ===');
    await page.evaluate(() => window.scrollTo(0, 0));
    await sleep(200);
    const top0 = await page.evaluate(() => {
      const t = s => +document.querySelector(s).getBoundingClientRect().top.toFixed(1);
      return { hd: t('.hd'), kpi: t('.kpis'), fbar: t('.fbar'), grid: t('.grid') };
    });
    await page.evaluate(() => window.scrollTo(0, 700));
    await sleep(350);
    const top1 = await page.evaluate(() => {
      const t = s => +document.querySelector(s).getBoundingClientRect().top.toFixed(1);
      return { hd: t('.hd'), kpi: t('.kpis'), fbar: t('.fbar'), grid: t('.grid'),
        y: window.scrollY, pos: getComputedStyle(document.querySelector('.sticky-head')).position };
    });
    chk('固定区 position:sticky', top1.pos === 'sticky', top1.pos);
    chk('页面确实滚动了', top1.y >= 600, top1.y);
    chk('标题栏滚动后不动', Math.abs(top1.hd - top0.hd) < 1, [top0.hd, top1.hd]);
    chk('KPI 滚动后不动', Math.abs(top1.kpi - top0.kpi) < 1, [top0.kpi, top1.kpi]);
    chk('筛选框滚动后不动', Math.abs(top1.fbar - top0.fbar) < 1, [top0.fbar, top1.fbar]);
    chk('图表区正常滚过', top1.grid < top0.grid - 600, [top0.grid, top1.grid]);
    await page.evaluate(() => window.scrollTo(0, 0));

    console.log('=== 12. 运行期错误 ===');
    chk('全程 0 JS 报错', errs.length === 0, errs);

  } catch (e) {
    fail++;
    console.log('  XX  测试中断：' + e.message);
    console.log(e.stack);
  } finally {
    await browser.close();
  }

  console.log('\n结果：' + pass + ' 通过 / ' + fail + ' 失败');
  process.exit(fail ? 1 : 0);
})();
