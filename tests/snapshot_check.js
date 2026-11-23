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
  const errs = [];
  page.on('pageerror', e => errs.push('PAGEERROR ' + e.message));
  page.on('console', m => { if (m.type() === 'error') errs.push('CONSOLE ' + m.text()); });
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
      chk('离线也补回了被吞掉的空格',
        pairs.some(x => /Dongguan Zhongfan auto supplies/i.test(x.cell)),
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
        first: (rows[0] || {}).textContent ? rows[0].textContent.replace(/\s+/g, ' ').trim() : '',
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
    await page.click('#fcExport');
    await waitFor(page, () => !!window.__csv, 9000, '协议 CSV');
    const csv2 = await page.evaluate(() => window.__csv);
    chk('协议 CSV 行数 + 表头与库一致',
      csv2.trim().split('\r\n').length === LC.total + 1,
      [csv2.trim().split('\r\n').length, LC.total + 1]);
    chk('协议 CSV 表头英文', /Contract,Supplier/.test(csv2), csv2.slice(0, 40));

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
