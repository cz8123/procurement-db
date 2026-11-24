/**
 * 视觉对象头部工具 + 交互模式检查（服务先起）
 *   NODE_PATH="<puppeteer-core 所在 node_modules>" node tests/visual_tools_check.js
 *
 * 盯住三组 Power BI 式能力：
 *   1) 上钻 / 转至下一级 / 展开所有下一级（v-back / v-down / v-exp）
 *   2) 影响此视觉对象的筛选器与切片器（v-flt → #vtPop）
 *   3) 突出显示开关（v-hl）：highlight 淡化其余 vs filter 只留当前
 * 期望值一律从 API 现场取，不写死行数与金额。
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
const count = (page, s) => page.evaluate(sel => document.querySelectorAll(sel).length, s);

(async () => {
  const browser = await puppeteer.launch({
    executablePath: CHROME, headless: 'new', args: ['--no-sandbox']
  });
  const page = await browser.newPage();
  const errs = [];
  page.on('pageerror', e => errs.push('PAGEERROR ' + e.message));
  page.on('response', r => {
    if (r.status() >= 400) errs.push('HTTP ' + r.status() + ' ' +
      decodeURIComponent(r.url().replace(BASE, '')));
  });
  page.on('console', m => { if (m.type() === 'error') errs.push('CONSOLE ' + m.text().slice(0, 140)); });
  try {
    await page.setViewport({ width: 1600, height: 1000 });
    await page.goto(BASE + '/dash?t=' + Date.now(), { waitUntil: 'networkidle0' });
    await waitFor(page, () => document.querySelectorAll('#chHeat .hcell').length > 0, 15000, 'charts');
    /* 默认突出显示 = 开 */
    await page.evaluate(() => {
      const b = document.querySelector('.v-hl');
      if (b && b.classList.contains('off')) b.click();
    });
    await sleep(300);

    console.log('=== 1. 头部图标组按能力出现 ===');
    const icons = await page.evaluate(() => ({
      flt: [...document.querySelectorAll('.v-flt')].map(e => e.dataset.flt),
      hl: document.querySelectorAll('.v-hl').length,
      back: document.querySelectorAll('.v-back').length,
      backVisible: [...document.querySelectorAll('.v-back')].filter(e => !e.hidden).length,
      down: [...document.querySelectorAll('.v-down')].map(e => ({
        name: (e.closest('.card').querySelector('.v-flt') || {}).dataset.flt,
        hidden: e.hidden
      })),
      exp: (() => {
        const e = document.querySelector('.v-exp');
        return e ? { hidden: e.hidden, disabled: e.disabled } : null;
      })(),
      drillSvg: document.querySelectorAll('.v-back svg').length
    }));
    chk('8 个视觉对象都有筛选浮层入口',
      icons.flt.length === 8 && icons.flt.indexOf('contracts') >= 0, icons.flt);
    chk('可以点选的图有突出显示开关（5 个）', icons.hl === 5, icons.hl);
    chk('无筛选时上钻按钮全部隐藏',
      icons.back >= 5 && icons.backVisible === 0, icons);
    chk('下钻按钮只出现在有层级的图（方式饼图 / 帕累托）',
      icons.down.length === 2 &&
      icons.down.every(d => d.name === 'methodDonut' || d.name === 'pareto') &&
      icons.down.every(d => d.hidden === false), icons.down);
    chk('类目明细有「展开所有下一级」且可用',
      !!icons.exp && icons.exp.hidden === false && icons.exp.disabled === false, icons.exp);
    chk('上钻是图标按钮（无文字，单个 svg）',
      icons.drillSvg === icons.back, { svg: icons.drillSvg, btns: icons.back });

    console.log('=== 2. 帕累托：点柱 → 高亮（淡化其余，不隐藏） ===');
    const pbase = await page.evaluate(() => {
      const r = document.querySelectorAll('#chPareto rect.pbar');
      return { n: r.length, first: r[0] ? r[0].dataset.cat : null };
    });
    chk('类别层有柱子', pbase.n > 1, pbase);
    await page.evaluate(() => document.querySelector('#chPareto rect.pbar')
      .dispatchEvent(new MouseEvent('click', { bubbles: true })));
    await waitFor(page, () => document.querySelectorAll('.chip').length === 1 &&
      document.querySelectorAll('#chPareto rect.pbar.dv').length > 0, 10000, 'pareto chip + dim');
    const phl = await page.evaluate(() => ({
      bars: document.querySelectorAll('#chPareto rect.pbar').length,
      dim: document.querySelectorAll('#chPareto rect.pbar.dv').length,
      chips: [...document.querySelectorAll('.chip')].map(e => e.textContent.trim()),
      up: !!([...document.querySelectorAll('.v-back')]
        .find(e => e.dataset.clear === 'category' && !e.hidden))
    }));
    chk('高亮：柱子数量不变，其余全部淡化',
      phl.bars === pbase.n && phl.dim === pbase.n - 1, phl);
    chk('点柱生成 Category chip', /Category/.test(phl.chips.join('|')), phl.chips);
    chk('上钻按钮出现', phl.up === true, phl.up);

    console.log('=== 3. 影响此视觉对象的筛选器与切片器 ===');
    await page.evaluate(() => document.querySelector('.v-flt[data-flt="pareto"]').click());
    await waitFor(page, () => !document.getElementById('vtPop').hidden, 3000, 'vtpop open');
    const pop = await page.evaluate(() => {
      const p = document.getElementById('vtPop');
      return {
        head: p.querySelector('.vt-h').textContent.replace(/\s+/g, ' ').trim(),
        groups: [...p.querySelectorAll('.vt-g')].map(e => e.textContent.trim()),
        rows: [...p.querySelectorAll('.vt-row')].map(e =>
          e.querySelector('b').textContent + '=' + e.querySelector('span').textContent),
        mine: p.querySelectorAll('.vt-row.mine').length,
        box: (() => { const r = p.getBoundingClientRect(); return { w: Math.round(r.width), h: Math.round(r.height) }; })()
      };
    });
    chk('浮层标题写明作用范围', /Filters affecting this visual/.test(pop.head), pop.head);
    chk('列出切片器（财年 / 币种）', pop.groups.indexOf('Slicers') >= 0 &&
      pop.rows.some(r => /^Fiscal year=FY/.test(r)) && pop.rows.some(r => /^Currency=/.test(r)), pop.rows);
    chk('列出页面筛选（Category，且标为本图造成）',
      pop.groups.indexOf('Filters') >= 0 && pop.rows.some(r => /^Category=/.test(r)) && pop.mine === 1,
      { rows: pop.rows, mine: pop.mine });
    chk('浮层有实际尺寸（不是空壳）', pop.box.w > 200 && pop.box.h > 80, pop.box);
    await page.evaluate(() => document.body.click());
    await waitFor(page, () => document.getElementById('vtPop').hidden, 3000, 'vtpop close');
    chk('点外部关闭浮层', await page.evaluate(() => document.getElementById('vtPop').hidden));

    console.log('=== 4. 转至下一级 → 子类别 → 上钻回类别 ===');
    await page.evaluate(() => document.getElementById('chPareto')
      .closest('.card').querySelector('.v-down').click());
    await waitFor(page, () => /Sub-category/.test(document.getElementById('pTitle').textContent),
      6000, 'sub level');
    const sub = await page.evaluate(() => {
      const rows = document.querySelectorAll('#chPareto rect.pbar');
      return {
        title: document.getElementById('pTitle').textContent,
        bars: rows.length,
        cats: [...rows].map(e => e.dataset.cat),
        other: rows.length ? !!rows[rows.length - 1].dataset.other : false,
        downHidden: document.getElementById('chPareto').closest('.card').querySelector('.v-down').hidden
      };
    });
    chk('标题切到子类别层', /Sub-category Spend/.test(sub.title), sub.title);
    chk('子类别层柱数 ≤ 15（Top14 + Other）', sub.bars > 1 && sub.bars <= 15, sub.bars);
    chk('超出部分合并成 Other', sub.other === true || sub.bars < 15, sub);
    chk('子类别层隐藏「下一级」按钮', sub.downHidden === true);

    await page.evaluate(() => document.getElementById('chPareto')
      .closest('.card').querySelector('.v-back').click());
    await waitFor(page, () => /^Category Spend/.test(document.getElementById('pTitle').textContent),
      8000, 'back to category');
    chk('上钻回类别层', /^Category Spend/.test(
      await page.evaluate(() => document.getElementById('pTitle').textContent)));

    /* 第 4 步留下的 category 筛选只命中 1 行，展开测试要先清掉 */
    await page.click('#btnReset');
    await waitFor(page, () => document.querySelectorAll('.chip').length === 0, 8000, 'reset before expand');
    await sleep(600);

    console.log('=== 5. 类目明细：展开所有下一级 ===');
    const anyOpen = () => page.evaluate(() =>
      document.querySelectorAll('#chCat .cd-subrow').length);
    chk('默认折叠（没有子类别行）', (await anyOpen()) === 0, await anyOpen());
    await page.evaluate(() => document.querySelector('.v-exp').click());
    await waitFor(page, () => document.querySelectorAll('#chCat .cd-subrow').length > 0,
      5000, 'expand all');
    const expanded = await page.evaluate(() => ({
      subrows: document.querySelectorAll('#chCat .cd-subrow').length,
      disabled: document.querySelector('.v-exp').disabled
    }));
    chk('一次展开所有有子类别的行', expanded.subrows > 1, expanded);
    chk('全展开后按钮置灰（无可展开项）', expanded.disabled === true, expanded.disabled);

    /* 展开态必须有对应的「上钻」把它们收回去（否则只能一个个点 −） */
    const upAfter = await page.evaluate(() => {
      const c = document.getElementById('chCat').closest('.card');
      const u = c.querySelector('.v-back');
      return { hidden: u.hidden, title: u.title };
    });
    chk('展开后出现上钻按钮', upAfter.hidden === false && /collapse/.test(upAfter.title), upAfter);
    await page.evaluate(() => document.getElementById('chCat')
      .closest('.card').querySelector('.v-back').click());
    await waitFor(page, () => document.querySelectorAll('#chCat .cd-subrow').length === 0,
      5000, 'collapse all');
    const collapsed = await page.evaluate(() => {
      const c = document.getElementById('chCat').closest('.card');
      return {
        subrows: document.querySelectorAll('#chCat .cd-subrow').length,
        upHidden: c.querySelector('.v-back').hidden,
        expDisabled: c.querySelector('.v-exp').disabled
      };
    });
    chk('上钻一次收起全部子类别', collapsed.subrows === 0, collapsed);
    chk('收起后上钻隐藏、展开按钮恢复可用',
      collapsed.upHidden === true && collapsed.expDisabled === false, collapsed);

    console.log('=== 6. 突出显示开关：filter 模式只留当前 ===');
    await page.evaluate(() => document.querySelector('.v-hl').click());
    await waitFor(page, () => document.querySelector('.v-hl').classList.contains('off'),
      3000, 'hl off');
    const offState = await page.evaluate(() => ({
      store: localStorage.getItem('board-hl'),
      icon: document.querySelector('.v-hl').querySelectorAll('circle').length,
      title: document.querySelector('.v-hl').title
    }));
    chk('关掉后写 localStorage 且图标变「斜杠圈」',
      offState.store === '0' && offState.icon === 1, offState);
    chk('悬浮提示改成 Filter 语义', /Filter/.test(offState.title), offState.title);

    await page.evaluate(() => document.querySelector('#chPareto rect.pbar')
      .dispatchEvent(new MouseEvent('click', { bubbles: true })));
    await waitFor(page, () => document.querySelectorAll('.chip').length === 1, 10000, 'filter chip');
    await waitFor(page, () => document.querySelectorAll('#chPareto rect.pbar').length === 1,
      8000, 'filter collapse');
    const filterMode = await page.evaluate(() => ({
      bars: document.querySelectorAll('#chPareto rect.pbar').length,
      dim: document.querySelectorAll('#chPareto rect.pbar.dv').length
    }));
    chk('filter 模式：只剩当前柱，没有淡化项',
      filterMode.bars === 1 && filterMode.dim === 0, filterMode);

    /* 收尾：恢复突出显示 + 清筛选 */
    await page.evaluate(() => document.querySelector('.v-hl').click());
    await page.click('#btnReset');
    await waitFor(page, () => document.querySelectorAll('.chip').length === 0, 8000, 'reset');
    chk('重置后无残留 chip 与淡化',
      await page.evaluate(() => document.querySelectorAll('.chip').length === 0 &&
        document.querySelectorAll('#chPareto rect.pbar.dv').length === 0));

    console.log('=== 7. 无 JS / 请求错误 ===');
    chk('无 pageerror / 4xx / console.error', errs.length === 0, errs.slice(0, 4));
  } catch (e) {
    fail++;
    console.log('  XX  测试中断：' + e.message);
  } finally {
    await browser.close();
    console.log('\n===== ' + pass + ' 通过 / ' + fail + ' 失败 =====');
    process.exit(fail ? 1 : 0);
  }
})();
