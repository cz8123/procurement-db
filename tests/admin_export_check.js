/**
 * 管理台「导出离线看板」功能检查（服务先起）
 *   NODE_PATH="<puppeteer-core 所在 node_modules>" node tests/admin_export_check.js
 *
 * 覆盖：顶部入口按钮 → 弹层 → 上次导出/历史文件 → 点「开始导出」→
 *       轮询到完成 → 下载按钮可用 / 状态文案 / 0 JS 报错。
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
async function waitFor(pg, fn, timeout, label) {
  const t0 = Date.now();
  while (Date.now() - t0 < (timeout || 8000)) {
    if (await pg.evaluate(fn)) return true;
    await sleep(120);
  }
  throw new Error('waitFor timeout: ' + (label || ''));
}

(async () => {
  const browser = await puppeteer.launch({
    executablePath: CHROME, headless: 'new', args: ['--no-sandbox']
  });
  const page = await browser.newPage();
  await page.setViewport({ width: 1600, height: 1000, deviceScaleFactor: 1 });
  const errs = [];
  page.on('pageerror', e => errs.push('PAGEERROR ' + e.message));
  page.on('console', m => { if (m.type() === 'error') errs.push('CONSOLE ' + m.text()); });
  const num = sel => page.$eval(sel, e => e.textContent.trim()).catch(() => null);

  try {
    console.log('=== 1. 入口与弹层 ===');
    await page.goto(BASE + '/?t=' + Date.now(), { waitUntil: 'networkidle0' });
    const btn = await page.evaluate(() => {
      const b = [...document.querySelectorAll('header .btn')]
        .find(x => /导出看板/.test(x.textContent));
      return b ? b.textContent.trim() : null;
    });
    chk('顶部有「导出看板」按钮', !!btn, btn);

    await page.evaluate(() => {
      [...document.querySelectorAll('header .btn')].find(x => /导出看板/.test(x.textContent)).click();
    });
    chk('弹层已打开', await page.evaluate(() =>
      document.getElementById('bemMask').classList.contains('show')));

    await waitFor(page, () => /\.html/.test(document.getElementById('bemLast').textContent), 6000, 'last export');
    const last = await num('#bemLast');
    chk('显示上次导出文件', /\.html/.test(last) && /MB/.test(last), last);

    const nFiles = await page.$$eval('#bemFiles .bem-file', a => a.length);
    chk('历史文件列表非空', nFiles > 0, nFiles);
    chk('历史行含大小与时间', await page.$eval('#bemFiles .bem-file',
      e => /MB/.test(e.textContent) && /\d{4}-\d{2}-\d{2}/.test(e.textContent)));

    console.log('=== 2. 触发导出 ===');
    await page.click('#bemRun');
    /* 立刻应有「正在启动/正在导出」反馈（不能停在上一次的完成文案上） */
    await waitFor(page, () => {
      const s = document.getElementById('bemStatus');
      return s.classList.contains('run') && /正在/.test(s.textContent);
    }, 5000, 'running state');
    chk('点击后立即进入导出中', true);

    /* 完成判定：按钮恢复可点（只有拿到 done/error 的渲染才会恢复），
       避免读到上一轮遗留的 done 文案就提前通过 */
    await waitFor(page, () => {
      const s = document.getElementById('bemStatus');
      return /ok|err/.test(s.className) && !document.getElementById('bemRun').disabled;
    }, 120000, 'export done');

    const st = await page.evaluate(() => ({
      cls: document.getElementById('bemStatus').className,
      txt: document.getElementById('bemStatus').textContent.trim(),
      dl: document.getElementById('bemDl').disabled,
      run: document.getElementById('bemRun').disabled,
      runTxt: document.getElementById('bemRun').textContent.trim()
    }));
    chk('导出成功（非报错）', /bem-status ok/.test(st.cls), st.txt);
    chk('文案含文件名/大小/行数', /\.html/.test(st.txt) && /MB/.test(st.txt) && /行/.test(st.txt), st.txt);
    chk('完成后「下载」可用', st.dl === false);
    chk('完成后「开始导出」恢复可点', st.run === false && /开始导出/.test(st.runTxt), st.runTxt);
    chk('历史列表已刷新', await page.$$eval('#bemFiles .bem-file', a => a.length) > 0);

    console.log('=== 3. 产物与接口 ===');
    const meta = await page.evaluate(async () => {
      const r = await fetch('/api/board/export/status').then(r => r.json());
      return { state: r.job.state, size: r.job.size, rows: r.job.rows,
               ag: r.job.agreements, name: r.job.name };
    });
    chk('接口状态为 done', meta.state === 'done', meta.state);
    chk('产出文件 > 3MB', meta.size > 3 * 1048576, meta.size);
    chk('行数与库一致（>40000）', meta.rows > 40000, meta.rows);
    chk('协议数 > 150', meta.ag > 150, meta.ag);

    chk('全程 0 JS 报错', errs.length === 0, errs.slice(0, 3));

    console.log('\n' + (fail ? 'FAILED ' + fail + ' / ' : 'ALL PASS ') + pass);
  } catch (e) {
    fail++;
    console.log('  XX  ' + e.message);
  } finally {
    await browser.close();
    process.exit(fail ? 1 : 0);
  }
})();
