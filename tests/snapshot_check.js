/**
 * 单文件快照校验：打开导出的 HTML（file://），确认数据全内嵌、交互可用、
 * 且前端复算的聚合结果与在线服务端一致。
 *
 * 跑法：
 *   NODE_PATH="<puppeteer-core 所在 node_modules>" node tests/snapshot_check.js
 *   可用 SNAP_FILE 指定文件，默认取 exports/ 下最新的 采购看板_单文件_*.html
 */
const fs = require('fs');
const path = require('path');
const http = require('http');
const puppeteer = require('puppeteer-core');

const ROOT = path.resolve(__dirname, '..');
const CHROME = process.env.CHROME_PATH || 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const LIVE = process.env.BOARD_BASE || 'http://127.0.0.1:5000';
const sleep = ms => new Promise(r => setTimeout(r, ms));

function latestSnap() {
  if (process.env.SNAP_FILE) return process.env.SNAP_FILE;
  const dir = path.join(ROOT, 'exports');
  const f = fs.readdirSync(dir)
    .filter(n => /^采购看板_单文件_.*\.html$/.test(n))
    .map(n => ({ n, t: fs.statSync(path.join(dir, n)).mtimeMs }))
    .sort((a, b) => b.t - a.t)[0];
  if (!f) throw new Error('exports/ 里没有单文件快照，先跑 board_export.py');
  return path.join(dir, f.n);
}

function liveJson(p) {
  return new Promise((resolve, reject) => {
    http.get(LIVE + p, r => {
      let s = '';
      r.on('data', c => { s += c; });
      r.on('end', () => { try { resolve(JSON.parse(s)); } catch (e) { reject(e); } });
    }).on('error', reject);
  });
}

let pass = 0, fail = 0;
function chk(name, ok, detail) {
  if (ok) { pass++; console.log('  OK  ' + name); }
  else { fail++; console.log('  XX  ' + name + (detail !== undefined ? '  ' + JSON.stringify(detail) : '')); }
}
async function waitFor(pg, fn, timeout, label) {
  const t0 = Date.now();
  while (Date.now() - t0 < (timeout || 8000)) {
    const v = await pg.evaluate(fn);
    if (v) return v;
    await sleep(90);
  }
  throw new Error('waitFor timeout: ' + (label || ''));
}

(async () => {
  const file = latestSnap();
  const size = fs.statSync(file).size / 1048576;
  console.log('快照：' + path.basename(file) + '  ' + size.toFixed(2) + ' MB');

  const browser = await puppeteer.launch({
    executablePath: CHROME, headless: 'new', args: ['--no-sandbox', '--allow-file-access-from-files']
  });
  const page = await browser.newPage();
  await page.setViewport({ width: 1600, height: 1100, deviceScaleFactor: 1 });
  const errs = [], specWarns = [];
  page.on('pageerror', e => errs.push('PAGEERROR ' + e.message));
  page.on('console', m => {
    if (m.type() === 'error') errs.push('CONSOLE ' + m.text());
    if (m.text().indexOf('[spec] missing definition') >= 0) specWarns.push(m.text());
  });
  /* 记录对外网络请求：单文件不允许连服务器 */
  const net = [];
  page.on('request', r => { if (/^https?:/.test(r.url())) net.push(r.url()); });

  const num = sel => page.$eval(sel, e => e.textContent.trim()).catch(() => null);
  const count = sel => page.$$eval(sel, a => a.length).catch(() => 0);

  try {
    console.log('=== 1. 打开文件（不连服务器） ===');
    chk('文件体积合理', size > 1 && size < 20, size);
    await page.goto('file:///' + file.replace(/\\/g, '/'), { waitUntil: 'load' });
    await waitFor(page, () => document.querySelectorAll('#fcTbl tbody tr').length > 0, 15000, '首屏');
    chk('7 张 KPI 卡', await count('.kpi') === 7, await count('.kpi'));
    chk('首屏无对外网络请求', net.length === 0, net.slice(0, 3));
    chk('数据已内嵌', await page.evaluate(() =>
      !!(window.__SNAP__ && window.__SNAP__.rows && window.__SNAP__.rows.data.length > 1000)));
    /* 期望值现场取在线接口；服务没起时退回快照自己的 API 垫片。
       不再写死金额/行数——新数据导入后这些数字本来就会变。 */
    const LD = await liveJson('/api/board/data?fy=FY2627&cur=RMB')
      .catch(() => page.evaluate(() => fetch('/api/board/data?fy=FY2627&cur=RMB').then(r => r.json())));
    const LO = await liveJson('/api/board/options')
      .catch(() => page.evaluate(() => fetch('/api/board/options').then(r => r.json())));
    const LC = await liveJson('/api/board/contracts?limit=5')
      .catch(() => page.evaluate(() => fetch('/api/board/contracts?limit=5').then(r => r.json())));
    const kv = k => (LD.kpi || []).find(x => x.k === k) || {};
    const fmtM = v => v == null ? '—' : '¥' + Number(v / 1e6).toFixed(1) + 'M';
    const fmtI = v => v == null ? '—' : Number(v).toLocaleString('en-US');
    const AMT0 = fmtM(kv('amount').v);
    await page.evaluate(v => { window.__AMT0 = v; }, AMT0);
    chk('内嵌行数与库一致', await page.evaluate(() =>
      window.__SNAP__.rows.data.length) === LO.rows,
      [await page.evaluate(() => window.__SNAP__.rows.data.length), LO.rows]);

    console.log('=== 2. 默认视图数字 ===');
    const k0 = await page.evaluate(() => {
      const o = {};
      document.querySelectorAll('.kpi').forEach(e => { o[e.dataset.k] = e.querySelector('.k-val').textContent; });
      return o;
    });
    chk('采购总额与库一致', k0.amount === AMT0, k0.amount + ' / ' + AMT0);
    chk('集采金额与库一致', k0.cp === fmtM(kv('cp').v), k0.cp);
    chk('PO 实付与库一致', k0.poSpend === fmtM(kv('poSpend').v), k0.poSpend);
    chk('单据数与库一致', k0.txn === fmtI(kv('txn').v), k0.txn);
    chk('供应商数与库一致', k0.suppliers === fmtI(kv('suppliers').v), k0.suppliers);
    chk('门店 52', k0.stores === '52', k0.stores);
    chk('中位周期按天', /\bd$/.test(k0.days || ''), k0.days);
    chk('帕累托 11 类', await count('#chPareto rect.pbar') === 11, await count('#chPareto rect.pbar'));
    chk('环形 4 类', await count('#chDonut [data-exp]') === 4, await count('#chDonut [data-exp]'));
    const mdApi = await page.evaluate(async () => {
      const r = await fetch('/api/board/data?cur=RMB'); return (await r.json()).methodDonut.length;
    });
    chk('离线采购方式环形 = 在线条数', await count('#chMethodDonut [data-exp]') === mdApi,
      { offline: await count('#chMethodDonut [data-exp]'), api: mdApi });
    /* 售后下钻：离线也应能切到豁免理由层（扇区数与在线接口一致） */
    const drillOffline = await page.evaluate(async () => {
      const el = [...document.querySelectorAll('#chMethodDonut [data-exp]')]
        .find(e => e.dataset.exp === 'SS-Exempt');
      if (!el) return null;
      el.dispatchEvent(new MouseEvent('click', { bubbles: true }));
      const r = await fetch('/api/board/method-drill?method=SS-Exempt&cur=RMB');
      const j = await r.json();
      const t0 = Date.now();
      while (Date.now() - t0 < 5000 &&
        document.querySelectorAll('#chMethodDonut [data-exp]').length < 2) {
        await new Promise(x => setTimeout(x, 100));
      }
      return {
        title: document.getElementById('mDonutTitle').textContent,
        segs: document.querySelectorAll('#chMethodDonut [data-exp]').length,
        apiRows: (j.rows || []).length,
        first: ((j.rows || [])[0] || {}).k
      };
    });
    console.log('    ' + JSON.stringify(drillOffline));
    chk('离线也能下钻到豁免理由层', !!drillOffline && /Exemption Reason/.test(drillOffline.title) &&
      drillOffline.segs === drillOffline.apiRows && drillOffline.apiRows >= 5, drillOffline);
    chk('热力 80 格', await count('#chHeat .hcell') === 80, await count('#chHeat .hcell'));
    chk('Top10 10 条', await count('#chTop .trow') === 10, await count('#chTop .trow'));
    chk('协议表默认 50 行', await count('#fcTbl tbody tr') === 50, await count('#fcTbl tbody tr'));
    const fs2 = await page.evaluate(() =>
      [...document.querySelectorAll('.fc-summary .fs')].map(e => e.textContent.trim()));
    chk('协议数与库一致', fs2[0] === 'Agreements' + LC.total, fs2[0] + ' / ' + LC.total);
    chk('可加预估与库一致',
      fs2[1].indexOf('¥' + Number(LC.summary.cv / 1e6).toFixed(1) + ' M') >= 0, fs2[1]);
    chk('整体执行率与库一致',
      fs2[3].indexOf((LC.summary.usage * 100).toFixed(1) + '%') >= 0, fs2[3]);

    console.log('=== 3. 前端复算 vs 服务端（同参数比对） ===');
    const cases = [
      '/api/board/data?fy=FY2627&cur=RMB',
      '/api/board/data?fy=FY2627&cur=RMB&category=Accessory',
      '/api/board/data?fy=FY2627&cur=RMB&region=%E5%8D%97%E5%8C%BA&expense=OPEX',
      '/api/board/data?fy=FY2526&cur=RMB',
      '/api/board/data?fy=FY2627&cur=RMB&level=C-Not%20Exempt&brand=BMW'
    ];
    for (const p of cases) {
      const snap = await page.evaluate(u => fetch(u).then(r => r.json()), p);
      let live = null;
      try { live = await liveJson(p); } catch (e) { /* 服务没起就跳过比对 */ }
      if (!live || live.error) { chk('（服务未启动，跳过）' + p, true); continue; }
      const a = snap.kpi.map(x => [x.k, x.v]);
      const b = live.kpi.map(x => [x.k, x.v]);
      const same = JSON.stringify(a) === JSON.stringify(b);
      chk('KPI 一致 ' + decodeURIComponent(p.replace('/api/board/data?', '')),
        same, { snap: a, live: b });
      const tv = snap.monthly.series.reduce((s, x) => s + x.vals.reduce((p2, v) => p2 + v, 0), 0);
      const tl = live.monthly.series.reduce((s, x) => s + x.vals.reduce((p2, v) => p2 + v, 0), 0);
      chk('月度合计一致', Math.abs(tv - tl) < 1, [tv, tl]);
      /* 类别明细表要吃 sup（去重供应商数）与 share，一并比对 */
      chk('帕累托一致（含供应商数/占比）',
        JSON.stringify(snap.pareto.rows.map(r => [r.k, r.v, r.n, r.sup, r.share])) ===
        JSON.stringify(live.pareto.rows.map(r => [r.k, r.v, r.n, r.sup, r.share])),
        snap.pareto.rows.slice(0, 2));
      chk('Top10 一致',
        JSON.stringify(snap.top.map(r => [r.k, r.v])) ===
        JSON.stringify(live.top.map(r => [r.k, r.v])),
        snap.top.slice(0, 2));
    }

    /* 供应商英文名（含被源系统吞掉空格的那种）：离线必须与在线一字不差 */
    let liveC = null;
    try { liveC = await liveJson('/api/board/contracts?cur=RMB&size=50&sort=start&dir=desc'); }
    catch (e) { /* 服务没起就跳过 */ }
    if (liveC && liveC.rows) {
      const cells = await page.$$eval('#fcTbl tbody tr td.supplier',
        a => a.map(e => e.textContent.trim()));
      const pairs = liveC.rows.map((r, i) => ({ en: r.supplier_en, cell: cells[i] }))
        .filter(x => x.en);
      chk('协议表供应商英文名与在线一致',
        pairs.length > 0 && pairs.every(x => x.cell === x.en),
        pairs.filter(x => x.cell !== x.en).slice(0, 2));
      /* 具体英文后缀随源数据版本变，只验证「连写被还原成带空格」 */
      chk('离线也补回了被吞掉的空格',
        pairs.some(x => /Dongguan Zhongfan /i.test(x.cell)) &&
        !pairs.some(x => /DongguanZhongfan/i.test(x.cell)),
        pairs.slice(0, 2).map(x => x.cell));
    } else {
      chk('（服务未启动，跳过供应商英文名比对）', true);
    }

    console.log('=== 3.5 类别明细表（离线渲染） ===');
    const cdSnap = await page.evaluate(() =>
      fetch('/api/board/data?fy=FY2627&cur=RMB').then(r => r.json()));
    const cdTop = cdSnap.pareto.rows[0];
    const cd = await page.evaluate(() => {
      const rows = Array.from(document.querySelectorAll('#chCat .cd-row'));
      const total = document.querySelector('#chCat .cd-total');
      return {
        n: rows.length,
        /* 类别单元格里现在有行首 +/− 按钮，取 .cd-cat span 才是类别名 */
        first: rows[0] ? [
          (rows[0].querySelector('.cd-cat span') || {}).textContent || '',
          ...[...rows[0].children].slice(2).map(td => td.textContent.trim())
        ].join(' ') : '',
        bars: rows.map(r => parseFloat((r.querySelector('.cd-share i') || {}).style ?
          r.querySelector('.cd-share i').style.width : '0')),
        total: total ? total.textContent.replace(/\s+/g, ' ').trim() : ''
      };
    });
    chk('类别明细 11 行', cd.n === 11, cd.n);
    chk('类别明细有合计行', /Total/.test(cd.total), cd.total.slice(0, 40));
    chk('合计占比 100%', /100\.0%/.test(cd.total), cd.total.slice(0, 60));
    chk('首行与在线数据一致（类别/供应商数/单据数）',
      cd.first.indexOf(cdTop.k) === 0 &&
      cd.first.indexOf(String(cdTop.sup)) > 0 &&
      cd.first.indexOf(String(cdTop.n)) > 0,
      [cd.first, cdTop.k, cdTop.sup, cdTop.n]);
    chk('条形宽度随金额递减',
      cd.bars.every((w, i) => i === 0 || w <= cd.bars[i - 1] + 0.01), cd.bars.slice(0, 5));

    console.log('=== 4. 交互：筛选 / 财年 / 抽屉 ===');
    await page.evaluate(() => document.querySelector('.spec-btn[data-spec="filter_brand"]').click());
    await sleep(150);
    await page.evaluate(() => document.querySelector('#spClose').click());
    await sleep(120);
    await page.click('.fdrop[data-k=category] .fd-btn');
    await sleep(200);
    await page.click('.fdrop[data-k=category] .fpop-b label');
    await waitFor(page, () => document.querySelectorAll('.chip').length === 1 &&
      document.querySelector('.kpi[data-k=amount] .k-val').textContent !== window.__AMT0,
      9000, '筛选生效');
    chk('勾选类别 → chip 出现', /Category/.test(await num('.chip') || ''));
    const a1 = await num('.kpi[data-k=amount] .k-val');
    chk('勾选类别 → 金额变化', a1 !== AMT0, a1);
    await page.keyboard.press('Escape');
    await page.click('#btnReset');
    await waitFor(page, () => document.querySelectorAll('.chip').length === 0, 8000, '重置');
    chk('重置回到全量', (await num('.kpi[data-k=amount] .k-val')) === AMT0);

    await page.select('#fySel', 'FY2526');
    await waitFor(page, () => /698/.test(document.querySelector('.kpi[data-k=amount] .k-val').textContent),
      9000, '切财年');
    chk('切 FY2526 金额变化', /698/.test(await num('.kpi[data-k=amount] .k-val')));
    await page.select('#fySel', 'FY2627');
    await waitFor(page, () => document.querySelector('.kpi[data-k=amount] .k-val').textContent === window.__AMT0,
      9000, '切回');

    await page.evaluate(() => document.querySelector('#chTop .trow').dispatchEvent(
      new MouseEvent('click', { bubbles: true })));
    await waitFor(page, () => document.querySelectorAll('#drawerTbl tbody tr').length > 0, 9000, '抽屉');
    chk('抽屉能下钻', await page.$eval('#drawer', e => e.classList.contains('on')));
    chk('抽屉有合计', /total/.test(await num('#drawerSub') || ''));
    await page.keyboard.press('Escape');
    await sleep(250);

    console.log('=== 5. 两个 CSV 导出 ===');
    await page.evaluate(() => {
      window.__csv = null; window.__csvBlob = null;
      const orig = URL.createObjectURL;
      URL.createObjectURL = function (blob) {
        window.__csvBlob = blob;
        blob.text().then(t => { window.__csv = t; });
        return orig.call(URL, blob);
      };
    });
    await page.click('#btnExport');
    await waitFor(page, () => !!window.__csv, 9000, '明细 CSV');
    const csv1 = await page.evaluate(() => window.__csv);
    /* Blob.text() 会按编码规范吞掉 BOM，所以单独看前 3 个字节是不是 EF BB BF */
    const head3 = await page.evaluate(() => window.__csvBlob.arrayBuffer()
      .then(b => Array.from(new Uint8Array(b).slice(0, 3))));
    chk('明细 CSV 带 UTF-8 BOM', JSON.stringify(head3) === '[239,187,191]', head3);
    chk('明细 CSV 表头英文', /^No\.,Date,Company/.test(csv1), csv1.slice(0, 40));
    /* 导出的是当前财年视图，不是全库行数 */
    chk('明细 CSV 行数 + 表头与库一致',
      csv1.trim().split('\r\n').length === Number(kv('txn').v) + 1,
      [csv1.trim().split('\r\n').length, Number(kv('txn').v) + 1]);
    await page.evaluate(() => { window.__csv = null; });
    /* 协议卡已移到 Framework 页，导出按钮要先切到该页才可点击 */
    await page.click('.pg-tab[data-page=framework]');
    await sleep(400);
    await page.click('#fcExport');
    await waitFor(page, () => !!window.__csv, 9000, '协议 CSV');
    const csv2 = await page.evaluate(() => window.__csv);
    chk('协议 CSV 行数 + 表头与库一致',
      csv2.trim().split('\r\n').length === LC.total + 1,
      [csv2.trim().split('\r\n').length, LC.total + 1]);
    chk('协议 CSV 表头英文', /Contract,Supplier/.test(csv2), csv2.slice(0, 40));

    console.log('=== 5b. 离线 Detail：说明列 ===');
    await page.click('.pg-tab[data-page=detail]');
    await waitFor(page, () => document.querySelectorAll('#dtTbl tbody tr.dt-group').length > 0,
      12000, 'offline detail');
    const dtOff = await page.evaluate(() =>
      [...document.querySelectorAll('#dtTbl thead th')].map(t => t.textContent.trim()));
    await page.evaluate(() => document.querySelector('#dtTbl tr.dt-group .dt-toggle').click());
    await waitFor(page, () => document.querySelectorAll('#dtTbl tr.dt-city').length > 0, 8000, 'offline city');
    await page.evaluate(() => document.querySelector('#dtTbl tr.dt-city .dt-toggle').click());
    await waitFor(page, () => document.querySelectorAll('#dtTbl tr.dt-company').length > 0, 8000, 'offline company');
    await page.evaluate(() => document.querySelector('#dtTbl tr.dt-company .dt-toggle').click());
    await waitFor(page, () => document.querySelectorAll('#dtTbl tr.dt-catgroup').length > 0, 8000, 'offline cat');
    await page.evaluate(() => document.querySelector('#dtTbl tr.dt-catgroup .dt-toggle').click());
    await waitFor(page, () => document.querySelectorAll('#dtTbl tr.dt-child').length > 0, 8000, 'offline child');
    const noteOff = await page.evaluate(() => {
      const td = document.querySelector('#dtTbl tr.dt-child td.dt-note');
      return td ? { text: td.textContent.trim(), tip: td.getAttribute('data-tip') || '',
                    ellipsis: getComputedStyle(td).textOverflow,
                    hasTipBox: !!document.querySelector('#dtTip') } : null;
    });
    chk('离线 Detail 13 列，区域后是 City、理由后是 Fiscal Year、财年后是 Note',
      dtOff.length === 13 && dtOff[1].indexOf('City') === 0 &&
      dtOff[4].indexOf('Sub-Category') === 0 &&
      dtOff[7].indexOf('Single Source Reason') === 0 &&
      dtOff[8].indexOf('Fiscal Year') === 0 &&
      dtOff[9].indexOf('Note') === 0 && dtOff[10].indexOf('Date') === 0, dtOff);
    chk('离线说明也是单行 + 省略号 + data-tip（悬停浮窗同构）',
      !!noteOff && noteOff.ellipsis === 'ellipsis' && noteOff.hasTipBox &&
      noteOff.text.length > 0 && noteOff.tip.length > 0, noteOff);
    const offCity = await page.evaluate(() => {
      const cityRow = document.querySelector('#dtTbl tr.dt-city');
      const cells = cityRow ? [...cityRow.children].map(t => t.textContent.trim()) : [];
      const child = document.querySelector('#dtTbl tr.dt-child');
      const cc = child ? [...child.children].map(t => t.textContent.trim()) : [];
      return { city: cells.slice(0, 3), child: cc.slice(0, 5),
               ok: !!cells[1] && cells[1] !== '—' && !!cc[1] && cc[1] === cells[1].replace(/^[+−]\s*/, '') };
    });
    chk('离线 City：城市分组行与明细行城市一致（第 2 格）', offCity.ok, offCity);
    const offFy = await page.evaluate(() => {
      const tr = document.querySelector('#dtTbl tr.dt-child');
      const cell = tr ? tr.children[8] : null;
      return { v: cell ? cell.textContent.trim() : '', n: document.querySelectorAll('#dtTbl tr.dt-child').length };
    });
    chk('离线明细行 Fiscal Year 列有 FY 值（第 9 格）', /^FY\d{4}$/.test(offFy.v), offFy);

    console.log('=== 5c. 离线：理由收在 Method 下拉的第二段（同 Region→Company） ===');
    await page.click('.pg-tab[data-page=overview]');
    await waitFor(page, () => !document.getElementById('pgOverview').hidden, 8000, 'back overview');
    await page.click('.fdrop[data-k=method] .fd-btn');
    await page.evaluate(() => {
      const i = [...document.querySelectorAll('.fdrop[data-k=method] .fsec[data-k=method] .fpop-b input')]
        .find(x => x.value === 'SS-Exempt');
      i.click();
    });
    await waitFor(page, () => !!document.querySelector('.fdrop[data-k=method] .fsec[data-k=exempt_reason]') &&
      !document.querySelector('.fdrop[data-k=method] .fsec[data-k=single_reason]'),
      9000, 'offline exempt section');
    chk('离线：理由不单独占格（恒 6 个），Method 下拉里出现 Exemption Reason 段',
      await page.evaluate(() => document.querySelectorAll('#fbar .fdrop').length === 6 &&
        [...document.querySelectorAll('.fdrop[data-k=method] .fsec')]
          .map(e => e.dataset.k).join(',') === 'method,exempt_reason'));
    await waitFor(page, () =>
      document.querySelectorAll('.fdrop[data-k=method] .fsec[data-k=exempt_reason] .fpop-b input').length >= 5,
      9000, 'offline reason opts');
    const oVals = await page.evaluate(() =>
      [...document.querySelectorAll('.fdrop[data-k=method] .fsec[data-k=exempt_reason] .fpop-b input')]
        .map(i => i.value));
    chk('离线：理由选项由前端复算（含 Frame Contract）',
      oVals.indexOf('Frame Contract') >= 0 && oVals.length >= 5, oVals);
    const oAmt0 = await page.$eval('.kpi[data-k=amount] .k-val', e => e.textContent.trim());
    await page.evaluate(v => { window.__OA0 = v; }, oAmt0);
    await page.evaluate(() => {
      const i = [...document.querySelectorAll('.fdrop[data-k=method] .fsec[data-k=exempt_reason] .fpop-b input')]
        .find(x => x.value === 'Frame Contract');
      i.click();
    });
    await waitFor(page, () => document.querySelectorAll('.chip').length === 2 &&
      document.querySelector('.kpi[data-k=amount] .k-val').textContent.trim() !== window.__OA0,
      10000, 'offline reason filter');
    const oChips = await page.evaluate(() =>
      [...document.querySelectorAll('.chip')].map(e => e.textContent.trim()));
    chk('离线：勾选理由 → chip 用聚合名 + 金额联动（原始值由垫片本地复算）',
      oChips.some(c => /Exemption Reason/.test(c) && /Frame Contract/.test(c)), oChips);
    await page.click('#btnReset');
    await waitFor(page, () => document.querySelectorAll('.chip').length === 0 &&
      !document.querySelector('.fdrop[data-k=method] .fsec[data-k=exempt_reason]') &&
      document.querySelectorAll('.fdrop').length === 6, 9000, 'offline reason reset');
    chk('离线：Reset 后理由段消失、筛选条仍是 6 个切片器', true);

    console.log('=== 5d. 离线：「?」口径说明按钮 ===');
    await page.click('.fbar-r > .spec-btn[data-spec="global"]');
    await waitFor(page, () => document.getElementById('specPop').classList.contains('on'),
      8000, 'offline global spec');
    chk('离线：右上角圆形 ? 弹出「看板整体口径」',
      (await num('#spTitle')) === '看板整体口径');
    await page.evaluate(() => document.getElementById('specPop').classList.remove('on'));
    await page.hover('.fdrop[data-k=method]');
    await page.click('.fdrop[data-k=method] .spec-btn');
    await waitFor(page, () => document.getElementById('specPop').classList.contains('on'),
      8000, 'offline method spec');
    chk('离线：Method 切片器右上角 ? 弹出「筛选 · 采购方式」',
      (await num('#spTitle')) === '筛选 · 采购方式');
    const offSweep = await page.evaluate(() => {
      const pop = document.getElementById('specPop');
      const bad = [];
      [...document.querySelectorAll('.spec-btn')].forEach(b => {
        pop.classList.remove('on');
        b.click();
        if (!pop.classList.contains('on') || !document.getElementById('spTitle').textContent)
          bad.push(b.dataset.spec);
      });
      pop.classList.remove('on');
      return bad;
    });
    await sleep(250);
    chk('离线：全部 ? 都能弹出说明', offSweep.length === 0, offSweep);
    chk('离线：无 missing definition 兜底警告', specWarns.length === 0, specWarns);

    console.log('=== 6. 运行期错误 ===');
    chk('全程 0 JS 报错', errs.length === 0, errs.slice(0, 3));
    chk('全程 0 对外请求', net.length === 0, net.slice(0, 3));

    /* .shot/ 是过程目录（gitignore），清理后可能不存在；写截图前先建，避免测试尾部抛 ENOENT */
    fs.mkdirSync(path.join(ROOT, '.shot'), { recursive: true });
    await page.screenshot({ path: path.join(ROOT, '.shot', 'snap_full.png'), fullPage: false });
    console.log('\n结果：' + pass + ' 通过 / ' + fail + ' 失败');
  } catch (e) {
    console.log('EXCEPTION: ' + e.message);
    fail++;
  } finally {
    await browser.close();
  }
  process.exit(fail ? 1 : 0);
})();
