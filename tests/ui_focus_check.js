/**
 * 专项验证（一次性的视觉细节核对，可重复跑）：
 *  1) Remain Days 圆形状态徽标（✓ / ! / ✕）
 *  2) 表头底纹加深后的实际颜色
 *  3) 月度图 region / pair 切换时按钮组与图表位置完全不动
 *
 * 跑法（服务先起）：
 *   NODE_PATH="<puppeteer-core 所在 node_modules>" node tests/ui_focus_check.js
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
    if (await pg.evaluate(fn)) return;
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
  try {
    await page.goto(BASE + '/dash?t=' + Date.now(), { waitUntil: 'networkidle0' });
    await waitFor(page, () => document.querySelectorAll('#fcTbl tbody tr').length > 0, 9000, 'contract rows');
    await waitFor(page, () => document.querySelectorAll('#chMonthly svg rect').length > 0, 9000, 'monthly bars');

    console.log('=== 1. Remain Days 徽标 ===');
    const rk = await page.evaluate(() => {
      const rows = [...document.querySelectorAll('#fcTbl tbody tr')];
      const seen = {};
      rows.forEach(tr => {
        const s = tr.querySelector('.remain');
        if (!s) return;
        const cls = [...s.classList].find(c => c !== 'remain');
        if (!cls || seen[cls]) return;
        const i = s.querySelector('i');
        const cs = getComputedStyle(i);
        seen[cls] = {
          glyph: i.textContent.trim(),
          bg: cs.backgroundColor,
          radius: cs.borderRadius,
          w: cs.width, h: cs.height,
          color: cs.color,
          txt: s.querySelector('b') ? s.querySelector('b').textContent.trim() : null
        };
      });
      return seen;
    });
    console.log('    ' + JSON.stringify(rk));
    chk('数值在徽标左侧', Object.values(rk).every(v => v.txt != null && v.txt !== ''));
    chk('徽标为圆形 (radius 50%)', Object.values(rk).every(v => /50%|8\.5px|9px/.test(v.radius)));
    chk('徽标白字', Object.values(rk).every(v => /255, 255, 255/.test(v.color)));
    chk('ok 用绿色 ✓', rk.ok && rk.ok.glyph === '✓' && !/0, 0, 0/.test(rk.ok.bg), rk.ok);
    chk('warn 用琥珀 !', rk.warn && rk.warn.glyph === '!', rk.warn);
    chk('bad 用红色 ✕', rk.bad && rk.bad.glyph === '✕', rk.bad);
    chk('三态都在页面上出现', !!(rk.ok && rk.warn && rk.bad), Object.keys(rk));

    console.log('=== 2. 表头底纹 ===');
    const hd = await page.evaluate(() => {
      const a = document.querySelector('#fcTbl thead th');
      const b = document.querySelector('#chCat thead th');
      const lum = rgb => {
        const m = rgb.match(/\d+/g).map(Number);
        return (0.2126 * m[0] + 0.7152 * m[1] + 0.0722 * m[2]) / 255;
      };
      return {
        fc: getComputedStyle(a).backgroundColor,
        cd: b ? getComputedStyle(b).backgroundColor : null,
        fcLum: lum(getComputedStyle(a).backgroundColor),
        color: getComputedStyle(a).color,
        /* 深底 + 深字也要够读：算 WCAG 对比度，别把表头调深后字看不清 */
        contrast: (() => {
          const ch = s => s.match(/\d+/g).map(Number);
          const rl = c => {
            const v = c.map(x => {
              x /= 255;
              return x <= 0.03928 ? x / 12.92 : Math.pow((x + 0.055) / 1.055, 2.4);
            });
            return 0.2126 * v[0] + 0.7152 * v[1] + 0.0722 * v[2];
          };
          const l1 = rl(ch(getComputedStyle(a).color));
          const l2 = rl(ch(getComputedStyle(a).backgroundColor));
          return +(((Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05)).toFixed(2));
        })()
      };
    });
    console.log('    ' + JSON.stringify(hd));
    chk('协议表表头明显加深 (亮度 < 0.72)', hd.fcLum < 0.72, hd.fcLum);
    chk('类别表表头同色系', hd.cd === hd.fc, [hd.cd, hd.fc]);
    chk('表头深字对比度 ≥ 4.5', hd.contrast >= 4.5, hd.contrast);

    console.log('=== 3. 切换时按钮/图表不动 ===');
    const snap = () => page.evaluate(() => {
      const q = s => document.querySelector(s);
      const r = e => { const b = e.getBoundingClientRect(); return [Math.round(b.left), Math.round(b.top), Math.round(b.width), Math.round(b.height)]; };
      const svg = q('#chMonthly svg');
      return {
        sw: r(q('#mSw')),
        btn0: r(q('#mSw button[data-m=region]')),
        btn1: r(q('#mSw button[data-m=pair]')),
        title: r(q('#mTitle')),
        chart: r(q('#chMonthly')),
        svg: r(svg),
        card: r(q('#chMonthly').closest('.card')),
        bars: [...svg.querySelectorAll('rect')].map(r0 => { const b = r0.getBoundingClientRect(); return [Math.round(b.left), Math.round(b.width)]; })
      };
    });
    const a = await snap();
    await page.evaluate(() => document.querySelector('#mSw button[data-m=pair]').click());
    await waitFor(page, () => !!document.querySelector('#mSw button[data-m=pair].on'), 4000, 'pair on');
    await sleep(200);
    const b = await snap();
    await page.evaluate(() => document.querySelector('#mSw button[data-m=region]').click());
    await waitFor(page, () => !!document.querySelector('#mSw button[data-m=region].on'), 4000, 'region on');
    await sleep(200);
    const c = await snap();
    console.log('    region ' + JSON.stringify(a.sw) + ' -> pair ' + JSON.stringify(b.sw) + ' -> ' + JSON.stringify(c.sw));
    chk('按钮组位置不变 (region↔pair)', JSON.stringify(a.sw) === JSON.stringify(b.sw), [a.sw, b.sw]);
    chk('按钮组位置不变 (回切)', JSON.stringify(a.sw) === JSON.stringify(c.sw), [a.sw, c.sw]);
    chk('两个按钮各自位置不变', JSON.stringify(a.btn0) === JSON.stringify(b.btn0) && JSON.stringify(a.btn1) === JSON.stringify(b.btn1));
    chk('图表区位置不变', JSON.stringify(a.chart) === JSON.stringify(b.chart), [a.chart, b.chart]);
    chk('SVG 位置尺寸不变', JSON.stringify(a.svg) === JSON.stringify(b.svg), [a.svg, b.svg]);
    /* 柱子的横向位置与宽度必须一致（纵向高度/每段数量随系列不同本就该变） */
    const xw = arr => {
      const xs = [...new Set(arr.map(r => r[0]))].sort((p, q) => p - q);
      const ws = [...new Set(arr.map(r => r[2]))];
      return { xs: xs, ws: ws };
    };
    chk('柱位/柱宽不变', JSON.stringify(xw(a.bars)) === JSON.stringify(xw(b.bars)), [xw(a.bars), xw(b.bars)]);
    chk('卡片总高不变', a.card[3] === b.card[3], [a.card[3], b.card[3]]);

    console.log('=== 4a. 钻取时整页不位移（chips 在筛选条内） ===');
    const pos = () => page.evaluate(() => {
      const t = s => Math.round(document.querySelector(s).getBoundingClientRect().top);
      const fb = document.querySelector('.fbar').getBoundingClientRect();
      const chips = document.getElementById('chips');
      const cr = chips.getBoundingClientRect();
      return {
        inBar: !!chips.closest('.fbar'),
        chipsDisplay: getComputedStyle(chips).display,
        chipsTop: Math.round(cr.top),
        barRight: Math.round(document.querySelector('.fbar-r').getBoundingClientRect().right),
        fbarH: Math.round(fb.height),
        stickyH: Math.round(document.querySelector('.sticky-head').getBoundingClientRect().height),
        monthly: t('#chMonthly'), donut: t('#chDonut'),
        heat: t('#chHeat'), fc: t('#fcCard')
      };
    });
    const p0 = await pos();
    chk('chips 在筛选条内（右侧）', p0.inBar === true);
    chk('无筛选时 chips 不占位', p0.chipsDisplay === 'none', p0.chipsDisplay);

    await page.evaluate(() => document.querySelector('#donutLegend .li').click());
    await waitFor(page, () => document.querySelectorAll('#chips .chip').length > 0, 8000, 'chip');
    await sleep(700);
    const p1 = await pos();
    console.log('    ' + JSON.stringify(p0) + ' -> ' + JSON.stringify(p1));
    chk('筛选条高度不变', p0.fbarH === p1.fbarH, [p0.fbarH, p1.fbarH]);
    chk('固定区高度不变', p0.stickyH === p1.stickyH, [p0.stickyH, p1.stickyH]);
    chk('各图表 top 不变', p0.monthly === p1.monthly && p0.donut === p1.donut &&
      p0.heat === p1.heat && p0.fc === p1.fc, [p0, p1]);
    const align = await page.evaluate(() => {
      const mid = s => {
        const r = document.querySelector(s).getBoundingClientRect();
        return r.top + r.height / 2;
      };
      return Math.round(Math.abs(mid('#chips') - mid('#btnReset')));
    });
    chk('chips 与 Reset 垂直居中对齐（差 ≤3px）', align <= 3, align);
    await page.evaluate(() => document.querySelector('#chips .chip button').click());
    await sleep(900);

    console.log('=== 4. 视觉对象「返回」 ===');
    await page.evaluate(() => document.querySelector('#donutLegend .li').click());
    await waitFor(page, () => {
      const c = document.querySelector('.v-back[data-clear=expense]');
      return c && !c.hidden;
    }, 8000, 'donut back chip');
    /* 筛选后要等数据回来、画面收成一个满环，再断言满环本身可点 */
    await waitFor(page, () => document.querySelectorAll('#chDonut .dseg').length === 1,
      8000, 'donut collapse');
    const dsel = await page.evaluate(() => {
      const b = document.querySelector('.v-back[data-clear=expense]');
      const r = b.getBoundingClientRect();
      const cs = getComputedStyle(b);
      return {
        chips: document.getElementById('chips').textContent.replace(/\s+/g, ' ').trim(),
        title: b.title,
        text: b.textContent.trim(),
        svg: b.querySelectorAll('svg').length,
        w: Math.round(r.width), h: Math.round(r.height),
        border: cs.borderTopWidth,
        bg: cs.backgroundColor,
        ringed: document.querySelectorAll('#chDonut circle.dseg[data-exp]').length,
        segs: document.querySelectorAll('#chDonut .dseg').length
      };
    });
    /* Power BI 视觉对象头部的返回：一个纯图标小按钮，不带文字胶囊 */
    chk('返回控件是图标按钮（无文字）', dsel.text === '' && dsel.svg === 1, dsel);
    chk('图标按钮尺寸 22×22 且无边框', dsel.w === 22 && dsel.h === 22 && dsel.border === '0px', dsel);
    chk('悬浮前无底色', /rgba\(0, 0, 0, 0\)|transparent/.test(dsel.bg), dsel.bg);
    chk('悬浮提示写明维度与取值', /Expense type/.test(dsel.title) && /OPEX|TRADE|CAPEX|A&P/.test(dsel.title), dsel.title);
    chk('只剩一个扇区时满环也可点（带 data-exp）', dsel.segs === 1 && dsel.ringed === 1, dsel);

    await page.evaluate(() => document.querySelector('.v-back[data-clear=expense]').click());
    await waitFor(page, () => document.querySelectorAll('#chDonut .dseg').length > 1, 8000, 'donut restored');
    const dback = await page.evaluate(() => ({
      chips: document.getElementById('chips').textContent.trim(),
      hidden: document.querySelector('.v-back[data-clear=expense]').hidden,
      income: document.querySelectorAll('.v-back:not([hidden])').length
    }));
    chk('点返回后筛选清空（chips 空）', dback.chips === '', dback.chips);
    chk('点返回后按钮隐藏', dback.hidden === true);
    chk('返回后没有残留可见按钮', dback.income === 0, dback.income);

    console.log('=== 4b. 月度图返回图标不挤动按钮组 ===');
    const before = await page.evaluate(() => {
      const r = document.querySelector('#mSw').getBoundingClientRect();
      return [Math.round(r.left), Math.round(r.width)];
    });
    await page.evaluate(() => document.querySelectorAll('#chMonthly rect.seg')[0].dispatchEvent(
      new MouseEvent('click', { bubbles: true })));
    await waitFor(page, () => {
      const b = document.querySelector('.v-back[data-clear=region]');
      return b && !b.hidden;
    }, 8000, 'monthly back icon');
    const after = await page.evaluate(() => {
      const r = document.querySelector('#mSw').getBoundingClientRect();
      const b = document.querySelector('.v-back[data-clear=region]').getBoundingClientRect();
      return { sw: [Math.round(r.left), Math.round(r.width)],
               gap: Math.round(document.querySelector('#mSw').getBoundingClientRect().right),
               iconLeft: Math.round(b.left) };
    });
    chk('Region 筛选生效后按钮组仍不动', JSON.stringify(before) === JSON.stringify(after.sw), [before, after.sw]);
    chk('返回图标在工具栏最右', after.iconLeft > after.gap, after);
    await page.evaluate(() => document.querySelector('.v-back[data-clear=region]').click());
    await sleep(600);

    console.log('=== 5. 排版（留白 / 列宽 / KPI 不截断） ===');
    const lay = await page.evaluate(() => {
      const pad = getComputedStyle(document.documentElement).getPropertyValue('--pad').trim();
      const card = document.querySelector('.kpi').getBoundingClientRect();
      const gridLeft = document.querySelector('main.grid > .card').getBoundingClientRect().left;
      const heads = [...document.querySelectorAll('#fcTbl thead th')].map(e => e.getBoundingClientRect().width);
      const tbl = document.querySelector('#fcTbl').getBoundingClientRect().width;
      return {
        pad: parseInt(pad, 10),
        leftGap: Math.round(card.left),
        aligned: Math.abs(card.left - gridLeft) < 1,
        leftPct: (heads[0] + heads[1]) / tbl,
        numMin: Math.min.apply(null, heads.slice(2, 8)),
        clipped: [...document.querySelectorAll('.kpi .k-val')]
          .filter(e => e.scrollWidth > e.clientWidth + 1).length
      };
    });
    console.log('    ' + JSON.stringify(lay));
    chk('左右留白 ≥ 24px', lay.pad >= 24, lay.pad);
    chk('KPI 与卡片左边缘对齐', lay.aligned, [lay.leftGap, lay.pad]);
    chk('协议表左侧两列不再吃满（<45%）', lay.leftPct < 0.45, lay.leftPct.toFixed(3));
    chk('协议表右侧数值列 ≥130px', lay.numMin >= 130, Math.round(lay.numMin));
    chk('KPI 大数值不被截断', lay.clipped === 0, lay.clipped);

    console.log('\n' + (fail ? 'FAILED ' + fail + ' / ' : 'ALL PASS ') + pass);
  } catch (e) {
    fail++;
    console.log('  XX  ' + e.message);
  } finally {
    await browser.close();
    process.exit(fail ? 1 : 0);
  }
})();
