/* ============================================================
   采购支出分析看板（单页）— 零依赖前端
   数据：/api/board/{options,data,top,rows,export.csv}
   ============================================================ */
(function () {
  'use strict';

  /* Regional Sales Sample.pbip 的 Fidex 主题：图表用同一族蓝灰阶（深→浅），
     页头/表头用 maximum #1E2F4B，热力色阶走 minimum #B1BFD5 → maximum #1E2F4B */
  var PALETTE = ['#1E2F4B', '#3B5E96', '#627EAB', '#A0D1FF', '#B1BFD5',
                 '#4D6172', '#8FA3C0', '#2C4771'];
  /* 分类色板：同一系列在所有月份/所有柱子上保持固定颜色，
     不再按数值做深浅色阶（用户明确：图例靠颜色区分，不要用色阶表达大小） */
  var CAT_COLORS = ['#1E2F4B', '#3B5E96', '#5E9BB8', '#2F7D6B',
                    '#C08A2E', '#8A5A8F', '#6B7A8F', '#B5544A'];
  var PAIR_FRM = '#1E3A63', PAIR_OTHER = '#8FB3D9';
  /* Card 视觉对象的顶部色条：主题里所有卡片同一个 ColorId，故不按指标区分 */
  var KPI_COLORS = ['#3B5E96'];
  var FDEFS = [
    { k: 'brand', name: 'Brand' },
    { k: 'category', name: 'Category' },
    { k: 'expense', name: 'Expense Type' },
    { k: 'method', name: 'Procurement Method' },
    { k: 'level', name: 'Supplier Category' },
    { k: 'region', name: 'Region' },
    /* Company 是 Region 的下级：不再单独占一格，收在 Region 下拉的第二段 */
    { k: 'company', name: 'Company', parent: 'region' }
  ];
  /* 抽屉 = 按公司聚合（用户要求）：一行一家公司，不列 PRPO 单号、不逐单展开 */
  var AG_COLS = [
    { k: 'company', t: 'Company', w: 230 },
    { k: 'region', t: 'Region', w: 84 },
    { k: 'n', t: 'Orders', num: 1, w: 68 },
    { k: 'amount', t: 'Spend', num: 1, w: 108 },
    { k: 'poPaid', t: 'PO Paid', num: 1, w: 108 },
    { k: 'days', t: 'Avg Days', num: 1, w: 82 }
  ];

  var S = {
    fy: null, cur: 'RMB', rate: 1, page: 'overview',
    fyRange: {},              // 每个财年的实际日期区间（选财年 → 自动填日期切片器）
    range: null,              // 全库日期范围（日期框 min/max 用，不能取当前财年的）
    latestFy: '',
    sel: {},
    options: {},
    data: null,
    topLocal: { category: '', method: '' },
    catSub: {},               // 类别明细里已展开子类别的类别名（默认全部折叠）
    mMode: 'region',          // 月度图：region=区域堆叠 / pair=Total vs Framework
    methodDrill: null,        // 采购方式饼图下钻：null | SS-Exempt | SS-Non Exempt
    subLevel: null,           // 帕累托下钻：null=类别层 | 'sub'=子类别层
    hlOn: (localStorage.getItem('board-hl') !== '0'),   // 突出显示（PBI highlight）默认开
    hl: null,                 // {src, key, vals, base}：当前高亮的来源视觉对象 + 点击前数据
    hlFresh: false,
    drill: {},                // 下钻数据缓存 {method: {rows,...}}（筛选变化时清空）
    // 框架协议统计（独立数据源 vw_fa_analysis，不吃 PRPO 全局筛选）
    fc: { page: 1, size: 50, sort: 'start', dir: 'desc',
          q: '', sup: '', type: '', df: '', dt: '', status: '' },
    drawer: { page: 1, size: 50, q: '', filters: null },
    geo: { metric: 'prpo', data: null, selected: [], q: '',
           loaded: false, stale: true, seq: 0, map: null, overlay: null },
    detail: { sort: 'region', dir: 'asc',
              q: {}, f: {}, min: {}, max: {}, expR: {}, expC: {},
              from: '', to: '',
              loaded: false, stale: true, seq: 0, pop: null, tmp: null },
    seq: 0, rowSeq: 0, drawerSeq: 0
  };

  /* ------------------------------------------------ 工具 */
  function $(s, r) { return (r || document).querySelector(s); }
  function $$(s, r) { return [].slice.call((r || document).querySelectorAll(s)); }
  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  function trunc(s, n) {
    s = String(s == null ? '' : s);
    return s.length > n ? s.slice(0, n - 1) + '…' : s;
  }
  /** 供应商名在库里是「中文全称-English name」。看板显示英文段（英文界面更好认），
      hover / 抽屉副标题给全称。顺序不保证（个别是 English-中文），两个方向都认。 */
  function supEn(s) {
    s = String(s == null ? '' : s);
    var out = s, i = s.indexOf('-');
    if (i > 1 && i < s.length - 2) {
      var head = s.slice(0, i).trim(), tail = s.slice(i + 1).trim();
      var zhH = /[\u4e00-\u9fa5]/.test(head), enH = /[A-Za-z]/.test(head);
      var zhT = /[\u4e00-\u9fa5]/.test(tail), enT = /[A-Za-z]/.test(tail);
      if (zhH && enT && !zhT) out = tail;      // 中文-English
      else if (enH && zhT && !zhH) out = head; // English-中文
    }
    /* 源系统常把英文名里的空格吞掉（ShenzhenXinchimediaCo.,LTD）。
       离线快照里没有后端清洗，用内嵌的 enFix（20 万字符级）就地还原。 */
    var fix = (window.__SNAP__ && window.__SNAP__.enFix) || null;
    return (fix && fix[out]) || out;
  }
  /** 按像素宽度截断：中文按 1 个字号宽，英文按 0.56。中英混排的名字
      （供应商名都是「中文-english」）用字符数截断会把左边裁掉。 */
  function truncPx(s, maxPx, fs) {
    s = String(s == null ? '' : s);
    var w = 0, out = '';
    for (var i = 0; i < s.length; i++) {
      var cw = s.charCodeAt(i) > 255 ? fs : fs * 0.56;
      if (w + cw > maxPx) return out + '…';
      w += cw; out += s[i];
    }
    return s;
  }
  function nf(v, d) {
    if (v == null || isNaN(v)) return '—';
    return Number(v).toLocaleString('en-US', {
      minimumFractionDigits: d || 0, maximumFractionDigits: d == null ? 0 : d
    });
  }
  function money(v, d) {
    if (v == null) return '—';
    var x = v / S.rate, a = Math.abs(x);
    if (a >= 1e9) return sym() + nf(x / 1e9, d == null ? 2 : d) + ' B';
    if (a >= 1e6) return sym() + nf(x / 1e6, d == null ? 1 : d) + ' M';
    if (a >= 1e3) return sym() + nf(x / 1e3, 0) + ' K';
    return sym() + nf(x, 0);
  }
  function sym() { return S.cur === 'RM' ? 'RM ' : '¥'; }
  function pct(v, d) {
    if (v == null || isNaN(v)) return '—';
    return (v * 100).toFixed(d == null ? 1 : d) + '%';
  }
  function days(v) { return v == null ? '—' : Number(v).toFixed(1) + ' d'; }
  function qs(extra, skip) {
    var p = new URLSearchParams();
    if (S.fy) p.set('fy', S.fy);
    p.set('cur', S.cur);
    Object.keys(S.sel).forEach(function (k) {
      if (skip && skip.indexOf(k) >= 0) return;
      (S.sel[k] || []).forEach(function (v) { p.append(k, v); });
    });
    if (extra) Object.keys(extra).forEach(function (k) {
      if (extra[k] !== '' && extra[k] != null) p.set(k, extra[k]);
    });
    return p;
  }
  /** 下载：在线时直接跳转由后端出 CSV；静态快照里由垫片接管，前端自己拼 */
  function download(url) {
    if (window.__SNAP_DOWNLOAD__) { window.__SNAP_DOWNLOAD__(url); return; }
    window.location.href = url;
  }
  function api(path, params) {
    var q = params ? params.toString() : '';
    return fetch(path + (q ? '?' + q : ''), { headers: { 'X-Requested-With': 'fetch' } })
      .then(function (r) {
        if (!r.ok) throw new Error(path + ' ' + r.status);
        return r.json();
      });
  }
  var toastT = null;
  function toast(msg, ms) {
    var t = $('#toast');
    t.textContent = msg; t.classList.add('on');
    clearTimeout(toastT);
    toastT = setTimeout(function () { t.classList.remove('on'); }, ms || 1800);
  }

  /* ------------------------------------------------ 提示框 */
  function showTip(html, x, y) {
    var t = $('#tip');
    t.innerHTML = html; t.classList.add('on');
    placeTip(x, y);
  }
  function placeTip(x, y) {
    var t = $('#tip'), r = t.getBoundingClientRect();
    var left = x + 14, top = y + 16;
    if (left + r.width > window.innerWidth - 8) left = x - r.width - 14;
    if (top + r.height > window.innerHeight - 8) top = y - r.height - 12;
    t.style.left = Math.max(6, left) + 'px';
    t.style.top = Math.max(6, top) + 'px';
  }
  function hideTip() { $('#tip').classList.remove('on'); }

  /* ------------------------------------------------ 图表基元 */
  function svg(W, H, inner, cls) {
    return '<svg viewBox="0 0 ' + W + ' ' + H + '" width="' + W + '" height="' + H +
      '" class="' + (cls || '') + '" style="max-width:100%;height:auto" ' +
      'preserveAspectRatio="xMidYMid meet">' + inner + '</svg>';
  }
  function axisMoney(v) {
    var x = v / S.rate;
    /* 坐标轴只显示整数：带小数会让刻度看起来参差 */
    if (Math.abs(x) >= 1e9) return nf(x / 1e9, 0) + 'B';
    if (Math.abs(x) >= 1e6) return nf(x / 1e6, 0) + 'M';
    if (Math.abs(x) >= 1e3) return nf(x / 1e3, 0) + 'K';
    return nf(x, 0);
  }
  /** 矩阵单元格里的数字：比坐标轴更紧凑（K 不带小数，避免 264.1K 这类噪声） */
  function cellMoney(v) {
    var x = v / S.rate, a = Math.abs(x);
    if (a >= 1e9) return nf(x / 1e9, 1) + 'B';
    if (a >= 1e6) return nf(x / 1e6, a >= 1e8 ? 0 : 1) + 'M';
    if (a >= 1e3) return nf(x / 1e3, 0) + 'K';
    return nf(x, 0);
  }
  function niceMax(v) {
    if (!v || v <= 0) return 1;
    var e = Math.pow(10, Math.floor(Math.log10(v))), m = v / e;
    var s = m <= 1 ? 1 : m <= 2 ? 2 : m <= 2.5 ? 2.5 : m <= 5 ? 5 : 10;
    return s * e;
  }
  /* 图表内宽：必须扣掉容器左右 padding，否则 svg 的 width 属性会比实际可绘制区宽
     16px，CSS 的 max-width:100% 再把它压回来 → 整张图被缩小 2~5%，线条与文字全部
     重采样发糊（锯齿感的根源）。取整到整数 px 才能 1:1 落像素。 */
  function chartW(id, fallback) {
    var el = $('#' + id);
    if (!el) return fallback;
    var cs = window.getComputedStyle(el);
    /* 用 getBoundingClientRect 的**小数**宽度：clientWidth 会四舍五入，
       取整后可能比真实内容宽多 1px，反而被 max-width:100% 压回去产生 0.1% 缩放 */
    var w = el.getBoundingClientRect().width -
      (parseFloat(cs.paddingLeft) || 0) - (parseFloat(cs.paddingRight) || 0);
    return Math.max(320, Math.floor(w) || fallback);
  }
  function emptyBox(id, msg) {
    var el = $('#' + id);
    if (el) el.innerHTML = '<div class="no-data">' + esc(msg) + '</div>';
  }

  /* ================================================ KPI */
  /* KPI 卡的图标（统一 24×24 视框、fill 风格，颜色由 CSS 的 currentColor 给） */
  var KPI_ICONS = {
    amount: '<rect x="3.6" y="12.2" width="4.1" height="8.2" rx="1.3"/>' +
            '<rect x="9.95" y="6.4" width="4.1" height="14" rx="1.3"/>' +
            '<rect x="16.3" y="9.4" width="4.1" height="11" rx="1.3"/>',
    cp: '<rect x="8.4" y="3.4" width="7.2" height="6.6" rx="1.6"/>' +
        '<rect x="3.4" y="12.4" width="7.2" height="7.4" rx="1.6"/>' +
        '<rect x="13.4" y="12.4" width="7.2" height="7.4" rx="1.6"/>',
    poSpend: '<rect x="2.6" y="6.4" width="18.8" height="11.2" rx="2.2"/>' +
             '<circle cx="12" cy="12" r="2.7" fill="#fff"/>',
    txn: '<path d="M6.2 3.4h7.3l4.3 4.3v12.9H6.2z"/>' +
         '<rect x="8.8" y="11.2" width="6.4" height="1.6" rx=".8" fill="#fff"/>' +
         '<rect x="8.8" y="14.6" width="6.4" height="1.6" rx=".8" fill="#fff"/>',
    suppliers: '<circle cx="12" cy="8" r="3.5"/>' +
               '<path d="M4.4 20.6c0-3.7 3.4-6.1 7.6-6.1s7.6 2.4 7.6 6.1z"/>',
    stores: '<path d="M3.6 9.4h16.8l-1.3 10.8H4.9z"/>' +
            '<path d="M6.6 9.4V5.2h10.8v4.2"/>',
    days: '<circle cx="12" cy="12" r="8.4" fill-opacity=".2"/>' +
          '<path d="M12 6.6v5.9l3.7 2.3" stroke="currentColor" stroke-width="2.1" ' +
          'fill="none" stroke-linecap="round" stroke-linejoin="round"/>'
  };

  function kpiVal(k, x) {
    if (x == null) return '—';
    if (k.fmt === 'money') return money(x, 1).replace(/ ([KMB])$/, '$1');   /* ¥138.7M，贴参考写法 */
    if (k.fmt === 'days') return days(x);
    if (k.fmt === 'pct') return (x * 100).toFixed(1) + '%';
    return nf(x, 0);
  }

  /** 差额带正负号：-¥627.7 M / +12 orders */
  function kpiSigned(k, x) {
    var sgn = x < 0 ? '-' : (x > 0 ? '+' : '');
    return sgn + kpiVal(k, Math.abs(x));
  }
  /** KPI 右侧的一行差额：ΔPY +4.2M ↑ / -1.9M ↓
      箭头用 1:1 像素的描边箭头（12×12 视框、不缩放），细线带杆，
      关于圆心对称绘制，避免缩放后亚像素模糊看起来"偏了"。 */
  function kDelta(label, txt, dir) {
    var d = dir === 'up'
      ? 'M6 8.8V3.3M3.3 5.9 6 3.3l2.7 2.6'
      : 'M6 3.2v5.5M3.3 6.1 6 8.7l2.7-2.6';
    return '<span class="k-delta ' + dir + '"><b>' + esc(label) + '</b>' +
      '<em>' + esc(txt) + '</em><i class="k-bd"><svg viewBox="0 0 12 12" ' +
      'width="12" height="12" aria-hidden="true" fill="none" stroke="#fff" ' +
      'stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round">' +
      '<path d="' + d + '"/></svg></i></span>';
  }

  function renderKpis() {
    var d = S.data, box = $('#kpis');
    var fy = d.fy || '';
    var pFy = (d.params && d.params.prevFy) || 'prev FY';
    box.innerHTML = (d.kpi || []).map(function (k) {
      var hasPrev = k.pv != null && k.v != null;
      var diff = hasPrev ? (k.v - k.pv) : null;
      var dir = diff == null ? '' : (diff < 0 ? 'down' : (diff > 0 ? 'up' : 'flat'));

      /* 参考图样式：右侧两行 ΔPY / Δ%，每行「灰色标签 + 彩色数值 + 彩色圆形箭头」。
         颜色按国内习惯：增加红、减少绿。 */
      var pills = '';
      if (hasPrev) {
        var yoyTxt = k.yoy == null ? '—'
          : (k.yoy < 0 ? '-' : '+') + Math.abs(k.yoy * 100).toFixed(1) + '%';
        pills = kDelta('ΔPY', kpiSigned(k, diff), dir) +
          kDelta('Δ%', yoyTxt, dir);
      }
      return '<div class="kpi" data-k="' + k.k + '" style="--c:' +
        KPI_COLORS[0] + '">' +
        '<button class="spec-btn kpi-spec" data-spec="kpi_' + k.k + '" ' +
        'title="How ' + esc(k.label) + ' is calculated">?</button>' +
        '<div class="k-top">' +
        '<span class="k-label">' + esc(k.label) + '</span>' +
        '<span class="k-fy">(' + esc(fy) + ')</span></div>' +
        '<div class="k-mid">' +
        '<div class="k-val">' + kpiVal(k, k.v) + '</div>' +
        (pills ? '<div class="k-bot">' + pills + '</div>' : '') +
        '</div>' +
        '</div>';
    }).join('');

    var r = d.range || {};
    $('#hdSub').textContent = 'Procurement spend analysis · ' + (d.fy || '') +
      ' · ' + (r.from || '—') + ' ~ ' + (r.to || '—') +
      ' · ' + nf(d.status.txn, 0) + ' orders / ' + nf(d.status.suppliers, 0) + ' suppliers';
  }

  /* ================================================ 筛选 */
  /** 某个筛选（或它的下级筛选）是否有选中值 —— 有下级时按钮也要高亮 */
  function hasAny(k) {
    if ((S.sel[k] || []).length) return true;
    return FDEFS.some(function (x) {
      return x.parent === k && (S.sel[x.k] || []).length;
    });
  }
  /** 按钮上的取值文字；Region 有下级 Company 时附上公司计数 */
  function fdText(f) {
    var sel = S.sel[f.k] || [];
    var txt = sel.length === 0 ? 'All' : (sel.length === 1 ? sel[0] : sel.length + ' selected');
    var kids = FDEFS.filter(function (x) { return x.parent === f.k; });
    if (kids.length) {
      var n = kids.reduce(function (a, x) { return a + (S.sel[x.k] || []).length; }, 0);
      if (n) txt += ' · ' + n + ' ' + kids[0].name.toLowerCase() + (n > 1 ? 's' : '');
    }
    return txt;
  }
  function optBody(k) {
    var opts = S.options[k] || [], sel = S.sel[k] || [];
    var body = opts.map(function (o) {
      return '<label><input type="checkbox" value="' + esc(o.v) + '"' +
        (sel.indexOf(o.v) >= 0 ? ' checked' : '') + '><span>' + esc(o.v) +
        '</span><span class="cnt">' + nf(o.n, 0) + '</span></label>';
    }).join('');
    return body || '<div class="fempty">No options</div>';
  }
  /** 下拉里的一段：表头 + 取值列表，各自带 data-k 供勾选/清空定位 */
  function fsec(f, note) {
    return '<div class="fsec" data-k="' + f.k + '">' +
      '<div class="fpop-h"><span>' + f.name +
      (note ? '<i class="fh-note">' + note + '</i>' : '') + '</span>' +
      '<span class="fh-acts"><a data-act="all">All</a><a data-act="none">Clear</a></span></div>' +
      '<div class="fpop-b">' + optBody(f.k) + '</div></div>';
  }

  function renderFilters() {
    var bar = $('#fbar'), tops = [], kids = {};
    FDEFS.forEach(function (f) {
      if (f.parent) (kids[f.parent] = kids[f.parent] || []).push(f);
      else tops.push(f);
    });
    bar.innerHTML = tops.map(function (f) {
      var sub = kids[f.k] || [];
      return '<div class="fdrop' + (hasAny(f.k) ? ' has' : '') +
        '" data-k="' + f.k + '">' +
        '<div class="fd-row">' +
        '<button type="button" class="fd-btn"><span class="fd-name">' + f.name + '</span>' +
        '<span class="fd-val">' + esc(fdText(f)) + '</span>' +
        '<span class="fd-caret" aria-hidden="true"></span></button>' +
        '<button class="spec-btn fdrop-spec" data-spec="filter_' + f.k + '" ' +
        'title="Where ' + esc(f.name) + ' values come from">?</button>' +
        '</div>' +
        '<div class="fpop">' + fsec(f) +
        sub.map(function (c) { return fsec(c, 'within ' + f.name.toLowerCase()); }).join('') +
        '</div></div>';
    }).join('');
    renderChips();
  }

  /** 只刷新按钮文字与 chips，避免勾选后整个下拉被重建（面板会闪一下关闭） */
  function updateFilterLabels() {
    FDEFS.forEach(function (f) {
      var drop = $('.fdrop[data-k="' + f.k + '"]');
      if (!drop) return;
      var el = $('.fd-val', drop);
      if (el) el.textContent = fdText(f);
      drop.classList.toggle('has', hasAny(f.k));
    });
    renderChips();
  }

  /* ---------------- 视觉对象头部工具（Power BI 模式） ----------------
     v-back  = Drill up：层级上退一级；已在顶层就清掉这张图造成的筛选（原「返回」）。
     v-down  = Go to next level：进入下一层级（方式→理由 / 类别→子类别）。
     v-exp   = Expand all down one level：一次展开所有下一级（类目明细的子类别）。
     v-flt   = Filters affecting this visual：列出切片器 / 全局筛选 / 本图局部筛选。
     v-hl    = Highlight 开关：点图元时淡化其他（开）或只留当前（关）。
     每个按钮只在该图「有这个能力」时出现，避免把标题挤没。 */
  var BACK_LABEL = { region: 'Region', expense: 'Expense type', category: 'Category',
    method: 'Procurement method', supplier: 'Supplier', company: 'Company', sub: 'Sub-category' };
  var HL_ICON_ON = '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor"' +
    ' stroke-width="2.1" stroke-linecap="round" aria-hidden="true">' +
    '<path d="M4 20v-6M9 20V6M14 20v-9M19 20V9"/></svg>';
  var HL_ICON_OFF = '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor"' +
    ' stroke-width="2" aria-hidden="true"><circle cx="12" cy="12" r="8.2"/>' +
    '<path d="M6.4 17.6 17.6 6.4" stroke-linecap="round"/></svg>';

  /** 卡片上的能力名（v-flt 的 data-flt）→ 该卡的 card 元素 */
  function cardOf(name) {
    var b = document.querySelector('.v-flt[data-flt="' + name + '"]');
    return b ? b.closest('.card') : null;
  }
  function setBtn(el, show, title) {
    if (!el) return;
    el.hidden = !show;
    if (title) { el.title = title; el.setAttribute('aria-label', title); }
  }
  function updateVisualTools() {
    $$('.v-back').forEach(function (el) {
      var k = el.dataset.clear, vals = (S.sel && S.sel[k]) || [];
      var card = el.closest('.card');
      var isCat = !!(card && card.querySelector('#chCat'));
      var isPareto = !!(card && card.querySelector('#chPareto'));
      /* 类目明细：展开过子类别 → 上钻 = 收起全部（优先于清类别筛选） */
      var nOpen = Object.keys(S.catSub || {}).length;
      if (isCat && nOpen) {
        el.hidden = false;
        var ct = 'Drill up — collapse ' + nOpen +
          (nOpen > 1 ? ' expanded sub-categories' : ' expanded sub-category');
        el.title = ct; el.setAttribute('aria-label', ct);
        return;
      }
      /* 采购方式饼图下钻态：先退出下钻（回到方式层） */
      if (k === 'method' && S.methodDrill) {
        el.hidden = false;
        var dt = 'Drill up — exit ' + S.methodDrill + ' drill-down';
        el.title = dt; el.setAttribute('aria-label', dt);
        return;
      }
      /* 帕累托子类别层：先回到类别层 */
      if (isPareto && S.subLevel === 'sub') {
        el.hidden = false;
        var st = 'Drill up — back to categories';
        el.title = st; el.setAttribute('aria-label', st);
        return;
      }
      if (!vals.length) {
        el.hidden = true;
        el.title = 'Drill up';
        return;
      }
      el.hidden = false;
      var txt = 'Drill up — clear ' + (BACK_LABEL[k] || k) + ': ' + vals.join(', ');
      el.title = txt;
      el.setAttribute('aria-label', txt);
    });

    /* 下一级：方式饼图（方式层 → 理由层）、帕累托（类别 → 子类别） */
    $$('.v-down').forEach(function (dn) {
      var cn = dn.closest('.card');
      var f = cn && cn.querySelector('.v-flt');
      var nm = f ? f.dataset.flt : '';
      var showDn = nm === 'methodDonut' ? !S.methodDrill : (nm === 'pareto' ? S.subLevel !== 'sub' : false);
      setBtn(dn, showDn, nm === 'methodDonut'
        ? 'Go to next level — reason (pick SS-Exempt / SS-Non Exempt first)'
        : 'Go to next level — sub-categories (pick a category bar first)');
    });

    /* 展开所有下一级：类目明细表一次性展开所有子类别 */
    $$('.v-exp').forEach(function (ex) {
      var pack = S.data && S.data.pareto;
      var subs = (pack && pack.subsMap) || {};
      var multi = Object.keys(subs).filter(function (k) { return (subs[k] || []).length > 1; });
      var closed = multi.some(function (k) { return !(S.catSub || {})[k]; });
      setBtn(ex, multi.length > 0,
        closed ? 'Expand all down one level' : 'All sub-categories already expanded');
      ex.disabled = !closed;
    });

    /* 突出显示开关 */
    $$('.v-hl').forEach(function (b) {
      b.classList.toggle('off', !S.hlOn);
      var t = S.hlOn
        ? 'Highlight: click a bar to dim the rest'
        : 'Filter: click a bar to show only it';
      b.title = t; b.setAttribute('aria-label', t);
      b.innerHTML = S.hlOn ? HL_ICON_ON : HL_ICON_OFF;
    });
  }
  function updateBacks() { updateVisualTools(); }

  var EXTRA_CHIP_LABEL = {
    exempt_reason: 'Exemption Reason', single_reason: 'Single Source Reason',
    sub: 'Sub-category'
  };
  function renderChips() {
    var box = $('#chips'), out = [];
    FDEFS.forEach(function (f) {
      (S.sel[f.k] || []).forEach(function (v) {
        out.push('<span class="chip"><b>' + f.name + '</b>' + esc(v) +
          '<button data-k="' + f.k + '" data-v="' + esc(v) + '" title="Remove">×</button></span>');
      });
    });
    /* 单一来源理由不在 FDEFS（不占筛选下拉），但下钻后要能看见并移除 */
    Object.keys(EXTRA_CHIP_LABEL).forEach(function (k) {
      (S.sel[k] || []).forEach(function (v) {
        out.push('<span class="chip"><b>' + EXTRA_CHIP_LABEL[k] + '</b>' + esc(v) +
          '<button data-k="' + k + '" data-v="' + esc(v) + '" title="Remove">×</button></span>');
      });
    });
    box.innerHTML = out.join('');
    updateBacks();
  }

  function setSel(k, vals) {
    if (!vals.length) delete S.sel[k]; else S.sel[k] = vals;
    S.fc.page = 1;
  }

  /* ---------------- 突出显示（Power BI highlight） ----------------
     点图元时两种模式：
       highlight（默认）→ 本图用「点击前那份数据」重绘，未命中的项淡化但位置不变，
                          其他视觉对象照常按新筛选联动（PBI 的交叉筛选高亮）。
       filter          → 本图直接用筛选后的数据，只剩当前项（其他隐藏）。
     来源图一旦变了（别的图点击 / 筛选条改动 / 重置），高亮自动失效。 */
  function hlPack(src, key) {
    if (!S.hlOn || !S.hl || S.hl.src !== src) return null;
    if (key && S.hl.key !== key) return null;
    return S.hl;
  }
  function hlDim(src, key, val) {
    var h = hlPack(src, key);
    if (!h || !h.vals || !h.vals.length) return false;
    return h.vals.indexOf(val) < 0;
  }
  /** 本图要画的数据：高亮态用点击前的全量那份，否则用当前筛选结果 */
  function hlBase(src, pick) {
    var h = hlPack(src), d = h && h.base ? pick(h.base) : null;
    return d || pick(S.data);
  }
  function beginHl(src, key, vals) {
    if (!S.hlOn || !vals.length) { S.hl = null; S.hlFresh = false; return; }
    if (S.hl && S.hl.src === src && S.hl.key === key) {
      S.hl.vals = vals.slice();
    } else {
      S.hl = { src: src, key: key, vals: vals.slice(), base: S.data };
    }
    S.hlFresh = true;
  }
  function clearHl() { S.hl = null; S.hlFresh = false; }
  function dv(on) { return on ? ' dv' : ''; }

  /* ================================================ Chart 1 月度堆叠 */
  /* ---------------------------------------------------------------- 月度图
     两种图示，切换保留：
       region = 按区域堆叠 + 上期虚线（原样式，点柱按区域交叉筛选）
       pair   = 每月 Total / Framework Contract 两根柱 + 柱顶金额胶囊（参考样式，
                点柱下钻该月明细）
     ------------------------------------------------------------------ */
  var M_TOT = '#1E2F4B';      // Total：主题藏青
  var M_FC  = '#3B5E96';      // Framework Contract：同色系中蓝（不用橙色）

  function mTitle(t) { var h = $('#mTitle'); if (h) h.textContent = t; }

  /* 柱顶金额胶囊（深藏青底白字，宽按字数估） */
  /* 柱宽：两种月度视图共用 —— 切换时柱子的位置、宽度、基线完全一致 */
  function barW(slot) { return Math.min(52, slot * 0.62); }

  /* 段内数字：段太矮或柱太窄就不显示；文字颜色按底色明暗自适应。
     窄图（g6，两饼图并排时）用整数紧凑格式，保证金额仍能落在段内。 */
  function segLabel(cx, yTop, h, w, v, fill) {
    if (!(v > 0) || h < 15) return '';
    var txt = money(v, w < 44 ? 0 : 1).replace(/ ([KMB])$/, '$1');
    if (txt.length * 6 > w - 4) return '';
    return '<text x="' + cx.toFixed(1) + '" y="' + (yTop + h / 2 + 4).toFixed(1) +
      '" text-anchor="middle" font-size="10" font-weight="600" pointer-events="none" fill="' +
      (isLight(fill) ? '#1E2F4B' : '#FFFFFF') + '">' + esc(txt) + '</text>';
  }

  /* 两种图示共用同一套坐标与 scale：切换时刻度、基线不移动 */
  function monthlyGeom(d) {
    var W = chartW('chMonthly', 760), H = 290;
    var L = 58, R = 14, T = 42, B = 42;
    var vals = [0].concat(d.prev || [], d.total || [], d.frame || []);
    (d.series || []).forEach(function (s) { vals = vals.concat(s.vals || []); });
    return { W: W, H: H, L: L, R: R, T: T, B: B,
      iw: W - L - R, ih: H - T - B,
      ymax: niceMax(Math.max.apply(null, vals) * 1.12) };
  }
  /* X 轴用 3 字母月份：12 个刻度挤在 ~690px 里，全称会互相压字（AuguSemptOcto…）。
     悬停 tooltip 仍直接取 labelsLong 给全称，所以缩写只影响坐标轴。 */
  function monthLabels(d) {
    return d.labels && d.labels.length ? d.labels : (d.labelsLong || []);
  }
  function yAxisText(L, T, ih, W, R, ymax) {
    var g = '';
    for (var t = 0; t <= 4; t++) {
      var y = T + ih - ih * t / 4;
      g += '<text x="' + (L - 8) + '" y="' + (y + 3.5).toFixed(1) +
        '" text-anchor="end" font-size="10" font-weight="600" fill="#1E2F4B">' +
        axisMoney(ymax * t / 4) + '</text>';
    }
    return g;
  }

  function renderMonthly() {
    if (S.mMode === 'pair') return renderMonthlyPair();
    return renderMonthlyRegion();
  }

  /* ---------------- 原样式：按区域堆叠 ---------------- */
  function renderMonthlyRegion() {
    var d = hlBase('monthly', function (x) { return x.monthly; }), box = $('#chMonthly');
    mTitle('Monthly Spend by Region');
    if (!d || !d.series || !d.series.length) {
      emptyBox('chMonthly', 'No data for the current filters'); return;
    }
    var G = monthlyGeom(d), W = G.W, H = G.H, L = G.L, R = G.R,
      T = G.T, B = G.B, iw = G.iw, ih = G.ih;
    var mLbl = monthLabels(d);

    // 画满 12 个财月（未到的月份留空，体现财年进度），但柱身不跟着月份数变宽
    var idx = d.labels.map(function (_, i) { return i; });
    var totals = idx.map(function (i) {
      return d.series.reduce(function (s, x) { return s + (x.vals[i] || 0); }, 0);
    });
    if (!totals.some(function (v) { return v > 0; })) {
      emptyBox('chMonthly', 'No data for the current filters'); return;
    }
    var ymax = G.ymax;

    var n = idx.length, slot = iw / n, bw = barW(slot);

    /* 无背景横向网格线：只保留刻度文字 + 零轴，位置与堆叠视图完全一致 */
    var g = yAxisText(L, T, ih, W, R, ymax) +
      '<line x1="' + L + '" y1="' + (T + ih).toFixed(1) + '" x2="' + (W - R) + '" y2="' +
      (T + ih).toFixed(1) + '" stroke="#D9D9D9" stroke-width="1"/>';

    var segMax = Math.max.apply(null,
      d.series.reduce(function (a, s) { return a.concat(s.vals || []); }, [0])) || 1;
    var bars = '', prevPts = [];
    idx.forEach(function (mi, j) {
      var x = L + j * slot + (slot - bw) / 2, acc = 0;
      /* 区域颜色固定（按系列索引），不随金额深浅变化 */
      var segs = d.series.map(function (s, si) {
        return { v: s.vals[mi] || 0,
          c: CAT_COLORS[si % CAT_COLORS.length],
          k: s.k, si: si };
      }).filter(function (z) { return z.v > 0; });
      segs.forEach(function (z, zi) {
        var h = z.v / ymax * ih;
        var y = T + ih - acc - h;
        var hh = Math.max(0, h - (zi < segs.length - 1 ? 1.2 : 0));
        var faded = hlDim('monthly', 'region', z.k);
        bars += '<rect class="seg' + dv(faded) + '" data-reg="' + esc(z.k) + '" data-m="' + mi +
          '" x="' + x.toFixed(1) + '" y="' + y.toFixed(1) + '" width="' + bw.toFixed(1) +
          '" height="' + hh.toFixed(1) + '" fill="' + z.c + '" rx="0"/>';
        if (!faded) bars += segLabel(x + bw / 2, y, hh, bw, z.v, z.c);
        acc += h;
      });
      var pv = (d.prev || [])[mi] || 0;
      if (pv > 0) prevPts.push([x + bw / 2, T + ih - pv / ymax * ih]);
      bars += '<text x="' + (x + bw / 2).toFixed(1) + '" y="' + (H - B + 16) +
        '" text-anchor="middle" font-size="10" font-weight="600" fill="#1E2F4B">' +
        esc(truncPx(mLbl[mi] || '', slot - 4, 13)) + '</text>';
    });

    var prevLine = '';
    if (prevPts.length > 1) {
      prevLine = '<polyline points="' + prevPts.map(function (p) {
        return p[0].toFixed(1) + ',' + p[1].toFixed(1);
      }).join(' ') + '" fill="none" stroke="#A9B7CB" stroke-width="1.4" stroke-dasharray="4 3"/>';
    }

    $('#mLegend').innerHTML =
      d.series.map(function (s, i) {
        return '<span' + dv(hlDim('monthly', 'region', s.k)) + '><i style="background:' +
          CAT_COLORS[i % CAT_COLORS.length] + '"></i>' + esc(s.l || s.k) + '</span>';
      }).join('') +
      '<span><i style="background:#7B8FA8;height:2px;border-radius:0"></i>Prior year</span>';

    box.innerHTML = svg(W, H, g + bars + prevLine +
      '<text x="' + (L + iw / 2) + '" y="' + (H - 4) +
      '" class="t-note" text-anchor="middle" font-size="10.5" fill="#1E2F4B">Fiscal month</text>');
  }

  /* ---------------- 参考样式：Total 由 Framework + Non-framework 堆叠而成 ---------------- */
  function renderMonthlyPair() {
    var d = S.data.monthly, box = $('#chMonthly');
    mTitle('Total vs Framework Contract');
    if (!d || !d.total) { emptyBox('chMonthly', 'No monthly data'); return; }
    var tot = d.total, frm = d.frame || [];
    var lbl = monthLabels(d);
    if (!tot.some(function (v) { return v > 0; })) {
      emptyBox('chMonthly', 'No data for the current filters'); return;
    }

    var G = monthlyGeom(d), W = G.W, H = G.H, L = G.L, R = G.R,
      T = G.T, B = G.B, iw = G.iw, ih = G.ih;
    var ymax = G.ymax;

    /* 与区域视图共用同一套坐标、柱宽、基线：切换时整图完全不动 */
    var n = Math.max(lbl.length || 0, 12);
    var slot = iw / n, bw = barW(slot);

    var g = yAxisText(L, T, ih, W, R, ymax) +
      '<line x1="' + L + '" y1="' + (T + ih).toFixed(1) + '" x2="' + (W - R) + '" y2="' +
      (T + ih).toFixed(1) + '" stroke="#D9D9D9" stroke-width="1"/>';

    /* 一根柱堆叠：底段 = 框架协议，上段 = 其余采购，柱顶 = 当月总额。
       两段用固定图例色，不按金额做色阶 */
    var bars = '';
    for (var i = 0; i < n; i++) {
      var t = tot[i] || 0, f = Math.min(frm[i] || 0, t);
      var cx2 = L + i * slot + slot / 2;
      var x = L + i * slot + (slot - bw) / 2;
      if (t > 0) {
        var hAll = t / ymax * ih, hF = f / ymax * ih;
        var yTop = T + ih - hAll, other = t - f, hO = hAll - hF;
        if (hO > 0.4) {
          bars += '<rect class="pseg" data-m="' + i + '" data-s="other" data-v="' + other +
            '" x="' + x.toFixed(1) + '" y="' + yTop.toFixed(1) + '" width="' + bw.toFixed(1) +
            '" height="' + hO.toFixed(1) + '" fill="' + PAIR_OTHER + '" rx="0"/>';
          bars += segLabel(cx2, yTop, hO, bw, other, PAIR_OTHER);
        }
        if (hF > 0.4) {
          bars += '<rect class="pseg" data-m="' + i + '" data-s="frm" data-v="' + f +
            '" x="' + x.toFixed(1) + '" y="' + (yTop + hO).toFixed(1) + '" width="' + bw.toFixed(1) +
            '" height="' + hF.toFixed(1) + '" fill="' + PAIR_FRM + '" rx="0"/>';
          bars += segLabel(cx2, yTop + hO, hF, bw, f, PAIR_FRM);
        }
      }
      bars += '<text x="' + cx2.toFixed(1) + '" y="' + (H - B + 16) +
        '" text-anchor="middle" font-size="10" font-weight="600" fill="#1E2F4B">' +
        esc(truncPx(lbl[i] || '', slot - 2, Math.min(13, slot * 0.16))) + '</text>';
    }

    $('#mLegend').innerHTML =
      '<span><i style="background:' + PAIR_FRM + '"></i>Framework Contract</span>' +
      '<span><i style="background:' + PAIR_OTHER + '"></i>Non-framework</span>';

    box.innerHTML = svg(W, H, g + bars +
      '<text x="' + (L + iw / 2) + '" y="' + (H - 4) +
      '" class="t-note" text-anchor="middle" font-size="10.5" fill="#1E2F4B">Fiscal month</text>');
  }

  /* ================================================ Chart 2 帕累托 */
  /* 帕累托两层的行集：
       category 层 → /data 的 pareto.rows（按类别）
       sub 层      → 当前选中类别下的子类别（/data 的 pareto.subsMap，前端合并重算累计）
     高亮态一律用「点击前」那份，保证柱子数量与位置不跳。 */
  function paretoPack() {
    var d = hlBase('pareto', function (x) { return x.pareto; });
    if (S.subLevel !== 'sub') {
      return { rows: (d && d.rows) || [], total: (d && d.total) || 0, level: 'category' };
    }
    var cats = (S.sel.category || []).slice();
    var src = (d && d.subsMap) || {};
    if (!cats.length) cats = Object.keys(src);
    var map = {}, order = [];
    cats.forEach(function (c) {
      (src[c] || []).forEach(function (it) {
        var g = map[it.k];
        if (!g) { g = map[it.k] = { k: it.k, v: 0, n: 0, po_paid: 0 }; order.push(it.k); }
        g.v += it.v || 0; g.n += it.n || 0; g.po_paid += it.po || 0;
      });
    });
    var rows = order.map(function (k) { return map[k]; })
      .sort(function (a, b) { return b.v - a.v; });
    var total = rows.reduce(function (s, r) { return s + r.v; }, 0);
    /* 子类别可能有几十个：留 Top 14 + 一根 Other，柱宽和标签才看得清 */
    if (rows.length > 15) {
      var rest = rows.slice(14);
      var o = { k: 'Other · ' + rest.length, v: 0, n: 0, po_paid: 0, other: 1 };
      rest.forEach(function (r) { o.v += r.v; o.n += r.n; o.po_paid += r.po_paid; });
      rows = rows.slice(0, 14).concat([o]);
    }
    var acc = 0;
    rows.forEach(function (r) {
      acc += r.v;
      r.cum = total ? acc / total : null;
      r.share = total ? r.v / total : null;
    });
    return { rows: rows, total: total, level: 'sub' };
  }

  function renderPareto() {
    var pack = paretoPack(), rows = pack.rows;
    var box = $('#chPareto');
    var pt = $('#pTitle');
    if (pt) {
      pt.textContent = S.subLevel === 'sub' ? 'Sub-category Spend (Pareto)' : 'Category Spend (Pareto)';
    }
    if (!rows.length) {
      emptyBox('chPareto', S.subLevel === 'sub'
        ? 'Pick a category bar first, then drill down' : 'No categories for the current filters');
      return;
    }
    var hlKey = S.subLevel === 'sub' ? 'sub' : 'category';
    var W = chartW('chPareto', 660), H = 310;
    var L = 62, R = 52, T = 22, B = 76;
    var iw = W - L - R, ih = H - T - B;
    var ymax = niceMax(Math.max.apply(null, rows.map(function (r) { return r.v || 0; })) * 1.08);

    var n = rows.length, slot = iw / n, bw = Math.min(58, slot * 0.56);

    /* 只留左右刻度文字；80% 参考线是业务判据，不是背景网格，予以保留 */
    var g = '';
    for (var t = 0; t <= 4; t++) {
      var y = T + ih - ih * t / 4;
      g += '<text x="' + (L - 8) + '" y="' + (y + 3.5).toFixed(1) +
        '" text-anchor="end" font-size="10" font-weight="600" fill="#1E2F4B">' + axisMoney(ymax * t / 4) + '</text>' +
        '<text x="' + (W - R + 8) + '" y="' + (y + 3.5).toFixed(1) +
        '" font-size="10" font-weight="600" fill="#1E2F4B">' + Math.round(t * 25) + '%</text>';
    }
    // 80% 参考线
    var y80 = T + ih - ih * 0.8;
    g += '<line x1="' + L + '" y1="' + y80.toFixed(1) + '" x2="' + (W - R) + '" y2="' + y80.toFixed(1) +
      '" stroke="#EA1313" stroke-width="1.2" stroke-dasharray="6 4"/>' +
      '<text x="' + (L + 6) + '" y="' + (y80 - 5).toFixed(1) +
      '" font-size="10" fill="#EA1313">80%</text>';

    var bars = '', pts = [];
    rows.forEach(function (r, i) {
      var h = (r.v || 0) / ymax * ih;
      var x = L + i * slot + (slot - bw) / 2, y = T + ih - h;
      /* 单一主色：金额大小由柱高表达，不再叠加色阶 */
      var faded = hlDim('pareto', hlKey, r.k);
      bars += '<rect class="pbar' + dv(faded) + '" data-cat="' + esc(r.k) + '"' +
        (r.other ? ' data-other="1"' : '') + ' x="' + x.toFixed(1) +
        '" y="' + y.toFixed(1) +
        '" width="' + bw.toFixed(1) + '" height="' + Math.max(0, h).toFixed(1) +
        '" fill="' + (faded ? '#AFC0D6' : '#3B5E96') + '" rx="0"/>';
      pts.push([x + bw / 2, T + ih - (r.cum || 0) * ih]);

      var nm = truncPx(r.k, Math.max(46, slot * 2.1), 13);
      bars += '<text transform="translate(' + (x + bw / 2).toFixed(1) + ',' + (T + ih + 8) +
        ') rotate(-40)" text-anchor="end" font-size="10" font-weight="600" fill="' +
        (faded ? '#98A6B8' : '#1E2F4B') + '">' + esc(nm) + '</text>';
    });

    var line = '<polyline points="' + pts.map(function (p) {
      return p[0].toFixed(1) + ',' + p[1].toFixed(1);
    }).join(' ') + '" fill="none" stroke="#E66C37" stroke-width="1.8"/>';
    var dots = pts.map(function (p) {
      return '<circle cx="' + p[0].toFixed(1) + '" cy="' + p[1].toFixed(1) +
        '" r="2.6" fill="#E66C37"/>';
    }).join('');

    /* 类别名本身已经说明维度，X 轴标题省掉——字号放大后它会和旋转标签压在一起 */
    box.innerHTML = svg(W, H, g + bars + line + dots);
  }

  /* ================================================ 类别明细表
     参考 AdventureWorks Sales 里「Product details by profitability」那个视觉对象：
     行 = 维度成员，列 = 数量 / 金额 / 占比，占比那列用条形表达。
     这里行 = 采购类别，多给两列（供应商数、单据数），点一行等同勾选该类别。 */
  function renderCatDetails() {
    var d = hlBase('catdetail', function (x) { return x.pareto; }), box = $('#chCat');
    if (!d || !d.rows || !d.rows.length) {
      box.innerHTML = '<div class="no-data">No categories for the current filters</div>';
      return;
    }
    var q = ($('#catQ') ? $('#catQ').value : '').trim().toLowerCase();
    var subsMap = d.subsMap || {};
    var rows = q ? d.rows.filter(function (r) {
      return (r.k || '').toLowerCase().indexOf(q) >= 0 ||
        ((subsMap[r.k] || []).some(function (s) {
          return (s.k || '').toLowerCase().indexOf(q) >= 0;
        }));
    }) : d.rows;
    var tot = d.total || 0, sel = S.sel.category || [], MAXB = 0.68;
    var paidTot = d.rows.reduce(function (s, r) { return s + (r.po_paid || 0); }, 0);
    /* 条形按「占最大类别的比例」缩放（PBI 数据条默认就是比最大值） */
    var maxShare = tot ? (d.rows[0].v || 0) / tot : 1;
    var subOpen = S.catSub || {};
    var body = rows.map(function (r) {
      var share = tot ? (r.v || 0) / tot : 0;
      var on = sel.indexOf(r.k) >= 0;
      var sl = subsMap[r.k] || [];
      var sub = (d.subMap || {})[r.k] || '—';
      /* 子类别一多就折起来：默认只显示金额最高的一条，点 +N 展开全部 */
      var more = Math.max(0, sl.length - 1);
      var open = !!subOpen[r.k];
      var cell = '<td class="cd-sub" title="' + esc(sub) + '"><div class="cs-line">' +
        '<span class="cs-main">' + esc(sub) + '</span>' +
        (more ? '<button type="button" class="cs-tog' + (open ? ' on' : '') +
          '" data-sub="' + esc(r.k) + '" title="' +
          (open ? 'Collapse' : more + ' more sub-categories') + '">' +
          (open ? '−' : '+' + more) + '</button>' : '') +
        '</div></td>';
      var tr = '<tr class="cd-row' + (on ? ' on' : '') +
        dv(hlDim('catdetail', 'category', r.k)) + '" data-cat="' + esc(r.k) + '">' +
        '<td class="cd-cat" title="' + esc(r.k) + '"><span>' + esc(r.k) + '</span></td>' +
        cell +
        '<td class="num">' + nf(r.sup, 0) + '</td>' +
        '<td class="num">' + nf(r.n, 0) + '</td>' +
        '<td class="num cd-money">' + money(r.v, 1) + '</td>' +
        '<td class="num">' + money(r.po_paid, 1) + '</td>' +
        '<td class="cd-share"><i style="width:' +
          ((maxShare ? share / maxShare : 0) * MAXB * 100).toFixed(1) + '%;background:#3B5E96"></i>' +
        '<b>' + pct(share) + '</b></td></tr>';
      if (!open || !sl.length) return tr;
      var smax = sl[0].v || 1;
      var items = sl.map(function (s) {
        var ss = r.v ? (s.v || 0) / r.v : 0;
        var sOn = (S.sel.sub || []).indexOf(s.k) >= 0;
        return '<li class="cs-item' + (sOn ? ' on' : '') + '" data-subcat="' +
          esc(s.k) + '" title="Click to filter this sub-category">' +
          '<span class="cs-k" title="' + esc(s.k) + '">' + esc(s.k) + '</span>' +
          '<i class="cs-bar"><b style="width:' + Math.max(2, (s.v / smax) * 100).toFixed(1) +
          '%;background:#8FB3D9"></b></i>' +
          '<span class="cs-v">' + money(s.v, 1) + '</span>' +
          '<span class="cs-p">' + money(s.po, 1) + '</span>' +
          '<span class="cs-n">' + nf(s.n, 0) + ' orders · ' + pct(ss) + '</span></li>';
      }).join('');
      return tr + '<tr class="cd-subrow" data-cat="' + esc(r.k) + '"><td colspan="7">' +
        '<div class="cs-wrap"><div class="cs-h">' + esc(r.k) + ' · Sub-categories' +
        '<span>' + sl.length + ' items</span></div><ul class="cs-list">' +
        '<li class="cs-head"><span>Sub-category</span><span></span>' +
        '<span>Spend</span><span>PO Paid</span><span>Orders</span></li>' + items +
        '</ul></div></td></tr>';
    }).join('');
    if (!body) body = '<tr><td class="empty" colspan="7">No matching categories</td></tr>';

    var st = S.data.status || {};
    var total = '<tr class="cd-total">' +
      '<td class="cd-cat"><span>Total</span></td><td class="cd-sub"></td>' +
      '<td class="num">' + nf(st.suppliers, 0) + '</td>' +
      '<td class="num">' + nf(st.txn, 0) + '</td>' +
      '<td class="num cd-money">' + money(tot, 1) + '</td>' +
      '<td class="num">' + money(paidTot, 1) + '</td>' +
      '<td class="cd-share"><i style="width:' + (MAXB * 100) + '%;background:var(--navy)"></i>' +
      '<b>' + pct(1) + '</b></td></tr>';

    box.innerHTML = '<table class="cd-tbl"><thead><tr>' +
      '<th class="cd-cat">Category</th><th class="cd-sub">Sub-Category</th>' +
      '<th class="num">Suppliers</th><th class="num">Orders</th>' +
      '<th class="num">Spend</th><th class="num">PO Paid</th><th>Share</th></tr></thead>' +
      '<tbody>' + body + (q ? '' : total) + '</tbody></table>';
    /* 铺满：等高之后再补齐高度，让合计行贴着卡片底边（否则和右侧热力卡错位） */
    requestAnimationFrame(function () { requestAnimationFrame(fitCatTable); });
    saveView();               // 展开/收起子类别不走 loadAll，单独落一次
  }

  /** 类别表比卡片矮时，把富余高度摊给各行（table 高度撑满），合计行才会落在卡片底部。
      内容更高（展开子类别）时不设高度，交给 .cd-wrap 滚动。 */
  function fitCatTable() {
    var wrap = $('#chCat'), tbl = wrap && wrap.querySelector('.cd-tbl');
    if (!tbl) return;
    tbl.style.height = '';
    /* 展开子类别：把表格锁成和右侧热力卡一样高、内部滚动，
       否则类别卡被撑高，两张卡的下沿又错开了 */
    if (Object.keys(S.catSub || {}).length) {
      var heat = $('#chHeat');
      wrap.style.height = (heat && heat.offsetHeight > 160 ? heat.offsetHeight : '') + 'px';
      return;
    }
    wrap.style.height = '';
    var avail = wrap.clientHeight - 12;      /* .cd-wrap 上 2 + 下 10 的内边距 */
    if (avail > 0 && tbl.offsetHeight < avail - 1) tbl.style.height = avail + 'px';
  }

  /** 数值色阶：柱形按 v/max 从浅色渐变到主题深色，小值也保留最低可见强度 */
  function ramp(v, max, light, dark, minK) {
    var k = max > 0 ? Math.max(minK || 0.3, Math.min(1, v / max)) : (minK || 0.3);
    return mix(light, dark, k);
  }
  function mix(a, b, k) {
    var pa = hex(a), pb = hex(b);
    var r = Math.round(pa[0] + (pb[0] - pa[0]) * k);
    var g = Math.round(pa[1] + (pb[1] - pa[1]) * k);
    var bl = Math.round(pa[2] + (pb[2] - pa[2]) * k);
    return 'rgb(' + r + ',' + g + ',' + bl + ')';
  }
  function hex(h) {
    return [parseInt(h.slice(1, 3), 16), parseInt(h.slice(3, 5), 16), parseInt(h.slice(5, 7), 16)];
  }
  /** 浅色块上用深字（浅天蓝扇区里的百分比标签不能是白的） */
  function isLight(h) {
    var p = h.charAt(0) === '#' ? hex(h)
      : (h.match(/\d+/g) || [255, 255, 255]).map(Number);
    return (0.299 * p[0] + 0.587 * p[1] + 0.114 * p[2]) / 255 > 0.62;
  }

  /* ================================================ Chart 3 环形 */
  function renderExpenseDonut() {
    drawDonut(hlBase('chDonut', function (x) { return x.donut; }) || [],
      'chDonut', 'donutLegend', 'chDonut', 'expense');
  }
  /* 采购方式环形：默认第一层（方式）；点 SS-Exempt / SS-Non Exempt 下钻到各自的理由层。
     下钻数据走单独接口按需取（塞在 /data 里会让整页每次刷新多跑两次全表 JOIN）。 */
  function loadMethodDrill(mk, cb) {
    if (S.drill[mk]) return cb(S.drill[mk]);
    api('/api/board/method-drill', qs({ method: mk }, ['method', 'exempt_reason', 'single_reason']))
      .then(function (d) {
        S.drill[mk] = d;
        if (S.methodDrill === mk) cb(d);
      })
      .catch(function (e) { toast('Failed to load reasons: ' + e.message, 3500); });
  }
  function renderMethodDonut() {
    var t = $('#mDonutTitle'), drill = S.methodDrill;
    var pack = drill ? S.drill[drill] : null;
    if (drill) {
      var rkey = drill === 'SS-Exempt' ? 'exempt_reason' : 'single_reason';
      t.textContent = (drill === 'SS-Exempt' ? 'Exemption Reason' : 'Single Source Reason') + ' · ' + drill;
      /* 理由层数据本来就不吃理由筛选（接口 skip 了 reason），天然保留全量，
         这里再按已选理由淡化其余扇区，和第二层下钻的视觉一致 */
      if (pack) drawDonut(pack.rows || [], 'chMethodDonut', 'methodDonutLegend', 'chMethodDonut', rkey);
      else emptyBox('chMethodDonut', 'Loading…');
      return;
    }
    t.textContent = 'Spend by Method';
    drawDonut(hlBase('chMethodDonut', function (x) { return x.methodDonut; }) || [],
      'chMethodDonut', 'methodDonutLegend', 'chMethodDonut', 'method');
  }
  function drawDonut(rows, chartId, legendId, src, key) {
    if (!rows.length) { emptyBox(chartId, 'No data for the current filters'); $('#' + legendId).innerHTML = ''; return; }
    var W = chartW(chartId, 340), H = 230;
    var cx = W / 2, cy = H / 2 + 2, R = Math.min(W, H) * 0.44, r0 = R * 0.58;
    var tot = rows.reduce(function (s, x) { return s + (x.v || 0); }, 0) || 1;

    var a0 = -Math.PI / 2, paths = '', labels = '';
    rows.forEach(function (x, i) {
      var frac = (x.v || 0) / tot;
      var a1 = a0 + frac * Math.PI * 2;
      var col = CAT_COLORS[i % CAT_COLORS.length];
      var faded = src ? hlDim(src, key, x.k) : false;
      if (frac > 0.9999) {
        /* 单值满环：也要带 class/data-exp —— 否则筛到一个类型后这一圈点不动，
           用户会卡在「只剩一个扇区、点哪都回不去」的状态 */
        paths += '<circle class="dseg" data-exp="' + esc(x.k) + '" cx="' + cx + '" cy="' + cy +
          '" r="' + ((R + r0) / 2) + '" fill="none" stroke="' + col +
          '" stroke-width="' + (R - r0) + '"/>';
      } else {
        paths += '<path class="dseg' + dv(faded) + '" data-exp="' + esc(x.k) + '" d="' + arc(cx, cy, R, r0, a0, a1) +
          '" fill="' + col + '" stroke="#fff" stroke-width="2"/>';
      }
      var am = (a0 + a1) / 2, rr = (R + r0) / 2;
      if (!faded && frac >= 0.045) {
        labels += '<text x="' + (cx + rr * Math.cos(am)).toFixed(1) + '" y="' +
          (cy + rr * Math.sin(am) + 3.5).toFixed(1) + '" text-anchor="middle" font-size="10" ' +
          'font-weight="600" fill="' + (isLight(col) ? '#1E2F4B' : '#fff') +
          '" pointer-events="none">' + (frac * 100).toFixed(1) + '%</text>';
      }
      a0 = a1;
    });

    var center = '<text x="' + cx + '" y="' + (cy - 4) + '" text-anchor="middle" font-size="10" ' +
      'fill="#1E2F4B">Total</text>' +
      '<text x="' + cx + '" y="' + (cy + 17) + '" class="t-big" text-anchor="middle" font-size="17" ' +
      'font-weight="600" fill="#0E1720">' + money(tot, 1) + '</text>';

    $('#' + chartId).innerHTML = svg(W, H, paths + labels + center);
    /* 项数多（下钻到理由层 7~8 项）时图例只留名称：
       金额与占比在扇区和悬浮提示里都有，省掉才能两列排开、名字少截断 */
    var compact = rows.length > 6;
    $('#' + legendId).innerHTML = rows.map(function (x, i) {
      return '<span class="li' + dv(src ? hlDim(src, key, x.k) : false) + '" data-exp="' + esc(x.k) +
        '"><i style="background:' + CAT_COLORS[i % CAT_COLORS.length] + '"></i>' +
        '<span class="lb" title="' + esc(x.k) + ' · ' + money(x.v, 1) + ' · ' + pct(x.share) + '">' +
        esc(x.k) + '</span>' +
        (compact ? '' : '<b>' + money(x.v, 1) + '</b><span class="pc">' + pct(x.share) + '</span>') +
        '</span>';
    }).join('');
  }

  function arc(cx, cy, R, r0, a0, a1) {
    var p = function (r, a) { return [(cx + r * Math.cos(a)).toFixed(2), (cy + r * Math.sin(a)).toFixed(2)]; };
    var large = (a1 - a0) > Math.PI ? 1 : 0;
    var o0 = p(R, a0), o1 = p(R, a1), i1 = p(r0, a1), i0 = p(r0, a0);
    return 'M' + o0 + 'A' + R + ',' + R + ' 0 ' + large + ' 1 ' + o1 +
      'L' + i1 + 'A' + r0 + ',' + r0 + ' 0 ' + large + ' 0 ' + i0 + 'Z';
  }

  /* ================================================ Chart 5 Top10 横条 */
  function renderTop() {
    var rows = S.data.top || [];
    if (!rows.length) { emptyBox('chTop', 'No suppliers for the current filters'); return; }
    var W = chartW('chTop', 480), H = Math.max(240, rows.length * 27 + 34);
    var L = Math.min(236, Math.max(130, W * 0.38)), R = 64, T = 8, B = 26;
    var iw = W - L - R;
    var mx = Math.max.apply(null, rows.map(function (r) { return r.v || 0; })) || 1;

    // PBI 横向条形图：不画纵向网格线，只留底部轴线与刻度标签
    var g = '<line x1="' + L + '" y1="' + (H - B) + '" x2="' + (W - R) + '" y2="' +
      (H - B) + '" stroke="#E4E4E4"/>';
    for (var t = 0; t <= 4; t++) {
      var x = L + iw * t / 4;
      g += '<text x="' + x.toFixed(1) + '" y="' + (H - B + 14) +
        '" text-anchor="middle" font-size="10" font-weight="600" fill="#1E2F4B">' + axisMoney(mx * t / 4) + '</text>';
    }

    var rowsHtml = '';
    rows.forEach(function (r, i) {
      var y = T + i * 27, bh = 18;
      var w = Math.max(2, (r.v || 0) / mx * iw);
      /* 单一主色：长度表达金额，不做色阶 */
      var col = '#3B5E96';
      var label = money(r.v, 1);
      /* 条够长就把金额写进条内右端（浅条用深字），不够才放到条外 */
      var inside = w > label.length * 7.4 + 14;
      rowsHtml += '<g class="trow" data-sup="' + esc(r.k) + '">' +
        '<rect x="0" y="' + y + '" width="' + W + '" height="' + 27 + '" fill="transparent"/>' +
        '<text x="' + (L - 8) + '" y="' + (y + 14) + '" text-anchor="end" font-size="10" ' +
        'fill="#1E2F4B">' + esc(truncPx(r.en || supEn(r.k), L - 18, 13)) + '</text>' +
        '<rect class="tbar" x="' + L + '" y="' + (y + 2) + '" width="' + w.toFixed(1) + '" height="' + bh +
        '" fill="' + col + '" rx="0"/>' +
        (inside
          ? '<text x="' + (L + w - 7).toFixed(1) + '" y="' + (y + 14) + '" text-anchor="end" ' +
            'font-size="10" font-weight="600" fill="#FFFFFF">' +
            label + '</text>'
          : '<text x="' + (L + w + 6).toFixed(1) + '" y="' + (y + 14) + '" font-size="10" ' +
            'fill="#1F2A36">' + label + '</text>') +
        '</g>';
    });

    $('#chTop').innerHTML = svg(W, H, g + rowsHtml);
  }

  /* ================================================ Chart 4 热力 */
  function renderHeat() {
    var h = S.data.heat;
    if (!h || !h.rows.length) { emptyBox('chHeat', 'No cross data for the current filters'); return; }
    var W = chartW('chHeat', 1100);
    var L = Math.min(260, Math.max(170, W * 0.22)), R = 96, T = 88, B = 30;
    var cols = h.cols, rows = h.rows, mx = h.max || 1;
    var cw = (W - L - R) / cols.length, ch = 40;
    var H = T + rows.length * ch + B;

    var g = '';
    cols.forEach(function (c, i) {
      var x = L + i * cw + cw / 2;
      g += '<text transform="translate(' + x.toFixed(1) + ',' + (T - 8) + ') rotate(-38)" ' +
        'text-anchor="start" font-size="10" font-weight="600" fill="#1E2F4B">' + esc(truncPx(c, 92, 13)) + '</text>';
    });

    rows.forEach(function (r, ri) {
      var y = T + ri * ch;
      g += '<text x="' + (L - 12) + '" y="' + (y + ch / 2 + 3.5) + '" text-anchor="end" ' +
        'font-size="10" font-weight="600" fill="#1E2F4B">' + esc(truncPx(r.en || supEn(r.k), L - 28, 13)) + '</text>';
      r.vals.forEach(function (v, ci) {
        var x = L + ci * cw;
        var k = v > 0 ? 0.08 + 0.92 * Math.pow(v / mx, 0.55) : 0;
        var fill = v > 0 ? mix('#E8F1FE', '#1E2F4B', k) : '#F7FAFF';
        g += '<rect class="hcell" data-sup="' + esc(r.k) + '" data-cat="' + esc(cols[ci]) +
          '" x="' + (x + 1).toFixed(1) + '" y="' + (y + 1) + '" width="' + (cw - 2).toFixed(1) +
          '" height="' + (ch - 2) + '" fill="' + fill + '" stroke="none" rx="0"/>';
        if (v > 0 && cw > 52) {
          g += '<text x="' + (x + cw / 2).toFixed(1) + '" y="' + (y + ch / 2 + 3.5) +
            '" text-anchor="middle" font-size="10" pointer-events="none" fill="' +
            (k > 0.45 ? '#fff' : '#000000') + '">' + cellMoney(v) + '</text>';
        }
      });
    });

    // 右侧色阶
    var bx = W - R + 18, by = T, bh = Math.min(140, rows.length * ch - 10), bw = 11;
    var grad = '<defs><linearGradient id="hg" x1="0" y1="1" x2="0" y2="0">' +
      '<stop offset="0" stop-color="#E8F1FE"/><stop offset="1" stop-color="#1E2F4B"/>' +
      '</linearGradient></defs>';
    g += grad + '<rect x="' + bx + '" y="' + by + '" width="' + bw + '" height="' + bh +
      '" fill="url(#hg)" stroke="none" rx="0"/>';
    [0, 0.5, 1].forEach(function (kk) {
      var y = by + bh - bh * kk;
      g += '<text x="' + (bx + bw + 5) + '" y="' + (y + 3.5) +
        '" font-size="10" fill="#1E2F4B">' + axisMoney(mx * kk) + '</text>';
    });
    g += '<text x="' + bx + '" y="' + (by - 8) + '" class="t-note" font-size="10.5" fill="#1E2F4B">Amount</text>';
    g += '<text x="' + (L + (W - L - R) / 2) + '" y="' + (H - 4) +
      '" class="t-note" text-anchor="middle" font-size="10.5" fill="#1E2F4B">Category</text>';

    $('#chHeat').innerHTML = svg(W, H, g);
  }

  /* ================================================ 明细表 */
  function headHtml(cols, sort, dir) {
    return '<tr>' + cols.map(function (c) {
      var arr = sort === c.k ? '<span class="arr">' + (dir === 'asc' ? '▲' : '▼') + '</span>' : '';
      return '<th data-sort="' + c.k + '"' + (c.w ? ' style="width:' + c.w + 'px"' : '') +
        (c.num ? ' class="ta-r"' : '') + '>' + c.t + arr + '</th>';
    }).join('') + '</tr>';
  }
  function rowsHtml(rows) {
    if (!rows.length) {
      return '<tr><td class="empty" colspan="' + AG_COLS.length + '">No companies for this selection</td></tr>';
    }
    return rows.map(function (r) {
      return '<tr>' +
        '<td class="strong" title="' + esc(r.company) + '">' + esc(r.company || '—') + '</td>' +
        '<td>' + esc(r.region || '—') + '</td>' +
        '<td class="num">' + nf(r.n, 0) + '</td>' +
        '<td class="num">' + money(r.amount, 1) + '</td>' +
        '<td class="num">' + money(r.poPaid, 1) + '</td>' +
        '<td class="num">' + (r.days == null ? '—' : Number(r.days).toFixed(1)) + '</td>' +
        '</tr>';
    }).join('');
  }
  function pagerHtml(total, page, size, unit) {
    var pages = Math.max(1, Math.ceil(total / size));
    var from = total ? (page - 1) * size + 1 : 0, to = Math.min(total, page * size);
    return '<span><b>' + nf(total, 0) + '</b> ' + (unit || 'rows') + ' · ' + from + '–' + to +
      ' · page ' + page + '/' + pages + '</span>' +
      '<span class="pg-btns">' +
      '<button data-pg="1"' + (page <= 1 ? ' disabled' : '') + '>First</button>' +
      '<button data-pg="' + (page - 1) + '"' + (page <= 1 ? ' disabled' : '') + '>Prev</button>' +
      '<button data-pg="' + (page + 1) + '"' + (page >= pages ? ' disabled' : '') + '>Next</button>' +
      '<button data-pg="' + pages + '"' + (page >= pages ? ' disabled' : '') + '>Last</button>' +
      '</span>';
  }

  /* ================================================ 框架协议统计表 */
  /* 列宽不再写死 px：卡片是全宽，写死会让 Contract/Supplier 吃掉整张表、
     右侧金额与日期挤成一团。改为 CSS 里的百分比列宽（table-layout:fixed），
     见 board.css 的 .fc-tbl th:nth-child(n)。 */
  var FC_COLS = [
    { k: 'contract', t: 'Contract', hf: 'contract' },
    { k: 'supplier', t: 'Supplier', hf: 'supplier' },
    { k: 'cv', t: 'CV', num: 1 },
    { k: 'pa', t: 'PA', num: 1 },
    { k: 'usage', t: 'Usage', num: 1, tip: 'PA counts framework-contract POs only' },
    { k: 'start', t: 'Start Date' },
    { k: 'expiry', t: 'Expiry Date' },
    { k: 'remain', t: 'Remain Days', num: 1, hf: 'status' },
    { k: 'type', t: 'Type', hf: 'type' }
  ];
  function fcqs(extra) {
    var p = new URLSearchParams();
    p.set('cur', S.cur);
    ['type', 'df', 'dt', 'q', 'sup', 'status', 'page', 'size', 'sort', 'dir'].forEach(function (k) {
      if (S.fc[k] !== '' && S.fc[k] != null) p.set(k, S.fc[k]);
    });
    if (extra) Object.keys(extra).forEach(function (k) {
      if (extra[k] !== '' && extra[k] != null) p.set(k, extra[k]);
    });
    return p;
  }
  var FUNNEL_SVG = '<svg viewBox="0 0 24 24" width="11" height="11" fill="none" ' +
    'stroke="currentColor" stroke-width="2.4" stroke-linejoin="round" aria-hidden="true">' +
    '<path d="M3 5h18l-7.2 8.4V20l-3.6-2v-4.6z"/></svg>';
  /* Type / Status 两个下拉的实际取值仍存在隐藏池里的 <select>（事件、选项来源不变），
     表头只放一枚漏斗按钮，点开就是这两个下拉的选项 —— 与 Detail 的列头筛选同一套交互 */
  var FC_HF = {
    contract: { kind: 'text', inp: '#fcQ', t: 'Contract contains' },
    supplier: { kind: 'text', inp: '#fcSup', t: 'Supplier contains' },
    type:     { kind: 'list', sel: '#fcType', t: 'Agreement type' },
    status:   { kind: 'list', sel: '#fcStatus', t: 'Status' }
  };
  /** 浮层里的控件都要先收回隐藏池：直接 innerHTML 会把节点（和事件）一起销毁 */
  function fcParkCtl() {
    var pool = $('#fcHdrPool');
    if (!pool) return;
    Object.keys(FC_HF).forEach(function (k) {
      var sp = FC_HF[k];
      if (sp.kind !== 'text') return;
      var inp = $(sp.inp);
      if (inp && !pool.contains(inp)) pool.appendChild(inp);
    });
  }
  function fcHeadHtml() {
    return '<tr>' + FC_COLS.map(function (c) {
      var arr = S.fc.sort === c.k ? '<span class="arr">' + (S.fc.dir === 'asc' ? '▲' : '▼') + '</span>' : '';
      var cls = 'fc-hth' + (c.num ? ' ta-r' : '');
      var flt = c.hf ? '<button type="button" class="fc-hflt" data-hf="' + c.hf + '" title="' +
        esc(FC_HF[c.hf].t) + ' — filter this column">' + FUNNEL_SVG + '</button>' : '';
      return '<th data-sort="' + c.k + '"' + (c.w ? ' style="width:' + c.w + 'px"' : '') +
        ' class="' + cls + '"' + (c.tip ? ' title="' + c.tip + '"' : '') + '>' +
        '<span class="fc-ht">' + c.t + arr + '</span>' + flt + '</th>';
    }).join('') + '</tr>';
  }
  /** 表头漏斗的高亮态：这一列正在筛（下拉看 value，搜索框看输入值） */
  function markFcHdrSel() {
    Object.keys(FC_HF).forEach(function (k) {
      var sp = FC_HF[k];
      var th = $('#fcTbl thead th[data-sort="' + k + '"]');
      var ctl = $(sp.kind === 'text' ? sp.inp : sp.sel);
      if (th) th.classList.toggle('has', !!(ctl && String(ctl.value || '').trim()));
    });
  }
  /* -------- 表头筛选浮层：选项直接来自隐藏的 <select> -------- */
  var fcPopEl = null;
  function closeFcPop() {
    if (!fcPopEl || fcPopEl.hidden) return;
    fcParkCtl();
    fcPopEl.hidden = true;
    fcPopEl.dataset.k = '';
  }
  function openFcPop(btn, key) {
    var sp = FC_HF[key];
    var ctl = $(sp.kind === 'text' ? sp.inp : sp.sel);
    if (!ctl) return;
    if (!fcPopEl) {
      fcPopEl = document.createElement('div');
      fcPopEl.className = 'fc-pop';
      fcPopEl.hidden = true;
      document.body.appendChild(fcPopEl);
    }
    fcParkCtl();                                  // 先收回控件，再重建浮层内容
    fcPopEl.dataset.k = key;
    fcPopEl.classList.toggle('wide', sp.kind === 'text');
    if (sp.kind === 'text') {
      fcPopEl.innerHTML = '<div class="fc-pop-h">' + esc(sp.t) +
        '<a class="fc-pop-clr">Clear</a></div><div class="fc-pop-s"></div>';
      fcPopEl.querySelector('.fc-pop-s').appendChild(ctl);
      /* 等这一轮 click 的默认焦点处理完再抢焦点，否则焦点落在漏斗按钮上 */
      setTimeout(function () { ctl.focus(); if (ctl.select) ctl.select(); }, 0);
    } else {
      fcPopEl.innerHTML = '<div class="fc-pop-h">' + esc(sp.t) + '</div>' +
        '<div class="fc-pop-b">' + Array.prototype.map.call(ctl.options, function (o) {
          return '<button type="button" data-v="' + esc(o.value) + '"' +
            (o.value === ctl.value ? ' class="on"' : '') + '>' + esc(o.textContent) + '</button>';
        }).join('') + '</div>';
    }
    fcPopEl.hidden = false;
    var r = btn.getBoundingClientRect(), w = 196;
    /* 贴右端时改成右边对齐按钮，浮层不会顶到视口边缘 */
    var left = (r.left + w > innerWidth - 8) ? (r.right - w) : r.left;
    var top = r.bottom + 4;
    if (top + fcPopEl.offsetHeight > innerHeight - 8) {
      top = Math.max(8, r.top - fcPopEl.offsetHeight - 4);
    }
    fcPopEl.style.left = Math.max(8, left) + 'px';
    fcPopEl.style.top = top + 'px';
  }
  function fcRowsHtml(rows) {
    if (!rows.length) {
      return '<tr><td class="empty" colspan="' + FC_COLS.length + '">No matching agreements</td></tr>';
    }
    /* 数据条按「本页最大 Usage」归一（参考样例的做法），低值也保底 4% 可见；
       分母不低于 100%，避免整页用量都低时条被放大成满格 */
    var uMax = 1;
    rows.forEach(function (r) { if (r.usage != null && r.usage > uMax) uMax = r.usage; });
    return rows.map(function (r) {
      var u = r.usage;
      var ut = u == null ? '—' : (u * 100).toFixed(1) + '%';
      /* 开方归一：usage 分布极扁（多数 <10%、少数 >100%），线性会让低值几乎看不见 */
      var uFill = u == null ? ''
        : '<i class="u-bar' + (u > 1 ? ' hi' : '') + '" style="width:' +
          Math.max(4, Math.min(100, Math.sqrt(u / uMax) * 100)).toFixed(1) + '%"></i>';
      var rd = r.remain;
      var rc = rd == null ? 'none' : (rd < 0 ? 'bad' : (rd <= 90 ? 'warn' : 'ok'));
      var rk = { ok: ['✓', 'Active — more than 90 days remaining'],
        warn: ['!', 'Expiring within 90 days'],
        bad: ['✕', 'Expired'],
        none: ['–', 'No expiry date'] }[rc];
      var rt = rd == null ? '—' : rd;
      return '<tr>' +
        '<td class="contract strong" title="' + esc(r.contract) + '">' + esc(r.contract) + '</td>' +
        '<td class="supplier" title="' + esc(r.supplier) + '">' +
          esc(r.supplier_en || supEn(r.supplier)) + '</td>' +
        '<td class="num">' + money(r.cv, 1) + '</td>' +
        '<td class="num">' + money(r.pa, 1) + '</td>' +
        '<td class="num u-cell">' + uFill +
          '<span class="u-txt' + (u != null && u > 1 ? ' hi' : '') + '">' + ut + '</span></td>' +
        '<td>' + esc(r.start || '—') + '</td>' +
        '<td>' + esc(r.expiry || '—') + '</td>' +
        '<td class="num"><span class="remain ' + rc + '" title="' + esc(rk[1]) + '">' +
          '<b>' + rt + '</b><i aria-hidden="true">' + rk[0] + '</i></span></td>' +
        '<td>' + esc(r.type || '—') + '</td></tr>';
    }).join('');
  }
  function loadContracts() {
    var seq = ++S.rowSeq;
    return api('/api/board/contracts', fcqs()).then(function (d) {
      if (seq !== S.rowSeq) return;
      S.fcSpec = d.spec || {};      // 后端仍返回实际 SQL（便于排查），前端口径说明不再展示
      var s = d.summary || {};
      var items = [
        ['Agreements', nf(s.n, 0), ''],
        ['Contract Value (CV)', money(s.cv, 1), ''],
        ['Actual Spend (PA)', money(s.pa, 1), ''],
        ['Overall Usage', pct(s.usage), (s.usage != null && s.usage > 1) ? 'warn' : ''],
        ['Usage > 100%', nf(s.overrun, 0), s.overrun ? 'warn' : ''],
        ['Expired', nf(s.expired, 0), s.expired ? 'bad' : '']
      ];
      $('#fcSummary').innerHTML = items.map(function (x) {
        return '<div class="fs"><div class="fs-l">' + x[0] + '</div>' +
          '<div class="fs-v ' + x[2] + '">' + x[1] + '</div></div>';
      }).join('');
      $('#fcTbl thead').innerHTML = fcHeadHtml();
      markFcHdrSel();
      $('#fcTbl tbody').innerHTML = fcRowsHtml(d.rows || []);
      $('#fcPager').innerHTML = pagerHtml(d.total, d.page, d.size);
      fillContractMeta(d.types, d.bounds);
    }).catch(function (e) { toast('Failed to load agreements: ' + e.message); });
  }

  /* ================================================ 抽屉 */
  function openDrawer(title, sub, filters) {
    S.drawer.filters = filters || {};
    S.drawer.page = 1; S.drawer.q = '';
    $('#drawerTitle').textContent = title;
    $('#drawerSub').textContent = sub || '';
    $('#drawerQ').value = '';
    $('#drawer').classList.add('on');
    $('#mask').classList.add('on');
    /* html 才是滚动容器，body 单独设 overflow 锁不住（见 board.css 注释） */
    _scrollY = window.pageYOffset || document.documentElement.scrollTop || 0;
    document.documentElement.classList.add('drawer-open');
    document.body.classList.add('drawer-open');
    $('#drawer').setAttribute('aria-hidden', 'false');
    loadDrawer();
  }
  var _scrollY = null;
  function closeDrawer() {
    $('#drawer').classList.remove('on');
    $('#mask').classList.remove('on');
    document.documentElement.classList.remove('drawer-open');
    document.body.classList.remove('drawer-open');
    $('#drawer').setAttribute('aria-hidden', 'true');
    if (_scrollY != null) { window.scrollTo(0, _scrollY); _scrollY = null; }
  }
  function loadDrawer() {
    var seq = ++S.drawerSeq;
    var p = qs(Object.assign({
      page: S.drawer.page, size: S.drawer.size, q: S.drawer.q,
      sort: 'amount', dir: 'desc', by: 'company'
    }, S.drawer.filters || {}));
    api('/api/board/rows', p).then(function (d) {
      if (seq !== S.drawerSeq) return;
      $('#drawerTbl thead').innerHTML = headHtml(AG_COLS, 'amount', 'desc');
      $('#drawerTbl tbody').innerHTML = rowsHtml(d.rows || []);
      $('#drawerPager').innerHTML = pagerHtml(d.total, d.page, d.size, 'companies') +
        (d.amount != null ? '<span>Total ' + money(d.amount, 0) + '</span>' : '') +
        (d.poPaid != null ? '<span>PO Paid ' + money(d.poPaid, 0) + '</span>' : '');
      $('#drawerSub').textContent = (S.drawer.label || '') + nf(d.total, 0) +
        ' companies · total ' + money(d.amount, 0) + ' · PO Paid ' + money(d.poPaid, 0);
    }).catch(function (e) { toast('Failed to load details: ' + e.message); });
  }

  /* ================================================ 加载 */
  /* 概览区的全部可视化。隐藏状态下 chartW() 只能拿 fallback 宽度，
     所以从 Detail/Framework 切回来时要重画一次（见 PAGES.overview.enter）。 */
  function renderOverviewVisuals() {
    if (!S.data) return;
    renderKpis(); renderMonthly(); renderPareto(); renderCatDetails();
    renderExpenseDonut(); renderMethodDonut(); renderHeat();
    /* Top 10 也依赖容器宽度：在别页隐藏时渲染只会拿到 fallback 480，
       切回概览必须按真实宽度重画，否则 SVG 会被 width:100% 放大（字和图变大）。 */
    if ((S.data.top || []).length) renderTop();
    updateVisualTools();
  }
  function loadAll() {
    var seq = ++S.seq;
    /* 这次刷新是不是图内点击（高亮）造成的：不是就把高亮清掉，
       避免筛选条 / chip / 重置之后还留着上一张图的淡化形态 */
    var keepHl = S.hlFresh;
    S.hlFresh = false;
    S.detail.stale = true;          // 全局筛选变了：下次进 Detail 页重新取数
    S.geo.stale = true;             // 地图 / 同比同样依赖全局筛选
    S.drill = {};                   // 下钻数据依赖筛选，一起失效
    saveView();                     // 筛选 / 下钻 / 财年 / 币种的任何变化都在这里落盘
    if (S.page === 'detail' && !$('#pgDetail').hidden) loadDetail();
    document.body.classList.add('loading');
    return api('/api/board/data', qs()).then(function (d) {
      if (seq !== S.seq) return;
      if (!keepHl) S.hl = null;
      S.data = d; S.rate = d.rate || 1;
      /* 下钻态且缓存已清：重新拉一次理由层，保证与当前筛选一致 */
      if (S.methodDrill) loadMethodDrill(S.methodDrill, function () { renderMethodDonut(); });
      renderOverviewVisuals();
      /* 首帧落在 Detail/Framework 时概览是隐藏的：这里量到的宽度是 fallback，
         回切到 Overview 时再按真实尺寸重画一次 */
      if (S.page !== 'overview') S.overviewHiddenRender = true;
      loadTop();
      loadContracts();
      updateVisualTools();
    }).catch(function (e) {
      toast('Failed to load data: ' + e.message, 4000);
      console.error(e);
    }).then(function () {
      if (seq === S.seq) document.body.classList.remove('loading');
    });
  }

  function loadTop() {
    var p = qs({ category: S.topLocal.category, method: S.topLocal.method });
    return api('/api/board/top', p).then(function (d) {
      S.data.top = d.rows || [];
      S.topSpec = d.spec || {};
      renderTop();
    }).catch(function () { renderTop(); });
  }

  function loadOptions() {
    return api('/api/board/options').then(function (d) {
      S.options = d.options || {};
      S.fyRange = d.fyRange || {};
      /* d.range 是**全库**日期范围；S.options 里装的是筛选值列表，没有 range，
         以前回退到 S.data.range（当前财年）会让日期框的 min/max 卡在本财年内 */
      if (d.range && d.range.from) S.range = d.range;
      S.latestFy = d.latestFy || '';
      var sel = $('#fySel');
      /* 第一项是「空财年」：设了日期范围时财年会被置空，两者不叠加 */
      sel.innerHTML = '<option value="">—</option>' + (d.fy || []).map(function (f) {
        return '<option value="' + esc(f) + '">' + esc(f) + '</option>';
      }).join('');
      /* 已选财年可能是刷新前还原的：还在列表里就保留，被删了就回落最新。
         空值只在「已经设了日期范围」时才合法（此时财年不参与筛选）。 */
      var fys = d.fy || [];
      var blankOk = S.fy === '' && (S.detail.from || S.detail.to);
      if (!blankOk && (!S.fy || fys.indexOf(S.fy) < 0)) S.fy = d.latestFy;
      sel.value = S.fy || '';

      // Chart5 局部筛选下拉
      $('#topCat').innerHTML = '<option value="">All categories</option>' +
        (S.options.category || []).map(function (o) {
          return '<option value="' + esc(o.v) + '">' + esc(o.v) + '</option>';
        }).join('');
      $('#topMth').innerHTML = '<option value="">All methods</option>' +
        (S.options.method || []).map(function (o) {
          return '<option value="' + esc(o.v) + '">' + esc(o.v) + '</option>';
        }).join('');

      renderFilters();

      /* 框架协议表的 Type 选项 / 日期边界曾在这里单独打一次 /contracts?size=1
         （多一次 80ms 的视图扫描 + 一个串行往返）。现在 loadContracts 的响应
         本来就带 types/bounds，改由它顺手填，首屏少一个请求。 */
    });
  }

  /** 框架协议的局部下拉与日期边界（types/bounds 由 /contracts 捎带返回）
      只在还没填过时构建，避免每次翻页/筛选都把下拉重建一遍（会闪、会丢焦点） */
  function fillContractMeta(types, bounds) {
    var sel = $('#fcType');
    if (types && types.length && sel && sel.options.length <= 1) {
      sel.innerHTML = '<option value="">All types</option>' +
        types.map(function (o) {
          return '<option value="' + esc(o.k) + '">' + esc(o.k) + ' (' + o.n + ')</option>';
        }).join('');
      if (S.fc.type) sel.value = S.fc.type;
    }
    var b = bounds || {};
    if (b.mn && !$('#fcFrom').min) {
      ['#fcFrom', '#fcTo'].forEach(function (s) {
        $(s).min = b.mn;
        if (b.mx) $(s).max = b.mx;
      });
    }
  }

  /** Region 变化后刷新 Company 下级选项（只更新这一个下拉，不动其它状态） */
  /** 公司选项随 Region 联动；只重建公司那一段，面板保持打开 */
  function refreshCompanyOptions() {
    return api('/api/board/options', qs(null, ['company'])).then(function (d) {
      S.options.company = (d.options && d.options.company) || [];
      var valid = (S.options.company || []).map(function (o) { return o.v; });
      var kept = (S.sel.company || []).filter(function (v) { return valid.indexOf(v) >= 0; });
      if (kept.length) S.sel.company = kept; else delete S.sel.company;
      var sec = $('.fdrop[data-k="region"] .fsec[data-k="company"] .fpop-b');
      if (sec) sec.innerHTML = optBody('company');
      updateFilterLabels();
    });
  }

  /* ================================================ 事件 */
  function bind() {
    // 页签切换
    var tabs = $('#pgTabs');
    if (tabs) tabs.addEventListener('click', function (e) {
      var b = e.target.closest('.pg-tab');
      if (b) goPage(b.dataset.page);
    });
    // 筛选下拉
    $('#fbar').addEventListener('click', function (e) {
      var a = e.target.closest('a[data-act]');
      if (a) {
        /* All / Clear 只作用于自己那一段（Region 的按钮不清 Company） */
        var scope = a.closest('[data-k]');
        var k = scope.dataset.k;
        var all = a.dataset.act === 'all';
        var list = all ? $$('input', scope).map(function (i) { return i.value; }) : [];
        setSel(k, list);
        $$('input', scope).forEach(function (i) { i.checked = all; });
        updateFilterLabels();
        if (k === 'region') refreshCompanyOptions();
        loadAll();
        return;
      }
      var b = e.target.closest('.fdrop .fd-btn');
      if (b) {
        var drop = b.closest('.fdrop');
        var was = drop.classList.contains('open');
        $$('.fdrop').forEach(function (d) { d.classList.remove('open'); });
        if (!was) drop.classList.add('open');
        return;
      }
    });
    // 勾选走 change：label 的 click 会联动 input，用 click 判断会读到切换前的状态
    $('#fbar').addEventListener('change', function (e) {
      var inp = e.target.closest('.fpop-b input');
      if (!inp) return;
      var scope = inp.closest('[data-k]');
      var k = scope.dataset.k;
      var list = $$('input', scope).filter(function (i) { return i.checked; })
        .map(function (i) { return i.value; });
      setSel(k, list);
      updateFilterLabels();
      if (k === 'region') refreshCompanyOptions();
      loadAll();
    });
    document.addEventListener('click', function (e) {
      if (!e.target.closest('.fdrop')) $$('.fdrop').forEach(function (d) { d.classList.remove('open'); });
    });

    // chips 移除
    $('#chips').addEventListener('click', function (e) {
      var b = e.target.closest('button[data-k]');
      if (!b) return;
      var k = b.dataset.k, v = b.dataset.v;
      /* 下钻态移除 method 或理由 → 整个下钻退出，回采购方式层 */
      if (S.methodDrill && (k === 'method' || k === 'exempt_reason' || k === 'single_reason')) {
        S.methodDrill = null;
        delete S.sel.method; delete S.sel.exempt_reason; delete S.sel.single_reason;
        clearHl(); renderFilters(); loadAll();
        return;
      }
      /* 移掉子类别筛选 → 回到类别层（留在子类别层会把所有类别的子类别都摊出来） */
      if (k === 'sub') S.subLevel = null;
      clearHl();
      setSel(k, (S.sel[k] || []).filter(function (x) { return x !== v; }));
      renderFilters();
      if (k === 'region') refreshCompanyOptions();
      loadAll();
    });

    // 视觉对象「向上钻取」：层级退一级；已在顶层就清掉这张图自己造成的那个维度
    document.addEventListener('click', function (e) {
      var b = e.target.closest('.v-back');
      if (!b || b.hidden) return;
      var k = b.dataset.clear;
      if (!k) return;
      e.preventDefault();
      /* 类目明细：展开态的上钻 = 收起全部子类别（回到类别层，不动类别筛选） */
      var bcard = b.closest('.card');
      if (bcard && bcard.querySelector('#chCat') && Object.keys(S.catSub || {}).length) {
        S.catSub = {};
        renderCatDetails(); updateVisualTools();
        toast('Collapsed sub-categories');
        return;
      }
      /* 下钻态：先退出下钻层，回到采购方式饼图 */
      if (k === 'method' && S.methodDrill) {
        var mk = S.methodDrill;
        S.methodDrill = null;
        delete S.sel.method; delete S.sel.exempt_reason; delete S.sel.single_reason;
        clearHl(); renderFilters(); loadAll();
        toast('Back to procurement method');
        return;
      }
      /* 帕累托子类别层：先回类别层（保留类别筛选，PBI 退一级不动父级选择） */
      if (k === 'category' && S.subLevel === 'sub' &&
          bcard && bcard.querySelector('#chPareto')) {
        S.subLevel = null;
        clearHl();
        setSel('sub', []);
        renderFilters(); loadAll();
        toast('Back to categories');
        return;
      }
      clearHl();
      if (k === 'category') { S.subLevel = null; setSel('sub', []); }
      setSel(k, []);
      renderFilters();
      if (k === 'region') refreshCompanyOptions();
      loadAll();
      toast('Cleared ' + (BACK_LABEL[k] || k) + ' filter');
    });

    /* ------------------- 视觉对象头部：下钻 / 展开 / 筛选浮层 / 突出显示 ------------------- */
    function cardName(el) {
      var f = el.closest('.card') && el.closest('.card').querySelector('.v-flt');
      return f ? f.dataset.flt : '';
    }
    // 转至下一级
    document.addEventListener('click', function (e) {
      var b = e.target.closest('.v-down');
      if (!b || b.hidden) return;
      e.preventDefault();
      var nm = cardName(b);
      if (nm === 'methodDonut') {
        var ms = (S.sel.method || []).filter(function (v) {
          return v === 'SS-Exempt' || v === 'SS-Non Exempt';
        });
        if (!ms.length) { toast('Pick SS-Exempt or SS-Non Exempt first, then drill down'); return; }
        S.methodDrill = ms[0];
        clearHl(); renderMethodDonut(); updateVisualTools(); saveView();
        loadMethodDrill(S.methodDrill, function () { renderMethodDonut(); });
        return;
      }
      if (nm === 'pareto') {
        if (!(S.sel.category || []).length) {
          toast('Pick a category bar first, then drill down'); return;
        }
        S.subLevel = 'sub';
        clearHl(); renderPareto(); updateVisualTools(); saveView();
        toast('Drilled down to sub-categories');
      }
    });
    // 展开层次中的所有下一级别（类目明细：一次展开全部子类别）
    document.addEventListener('click', function (e) {
      var b = e.target.closest('.v-exp');
      if (!b || b.hidden || b.disabled) return;
      e.preventDefault();
      var pack = (S.data && S.data.pareto) || {};
      var subs = pack.subsMap || {};
      Object.keys(subs).forEach(function (k) {
        if ((subs[k] || []).length > 1) S.catSub[k] = 1;
      });
      renderCatDetails(); updateVisualTools();
      toast('Expanded all sub-categories');
    });
    // 突出显示 / 筛选 模式开关
    document.addEventListener('click', function (e) {
      var b = e.target.closest('.v-hl');
      if (!b) return;
      e.preventDefault();
      S.hlOn = !S.hlOn;
      try { localStorage.setItem('board-hl', S.hlOn ? '1' : '0'); } catch (err) { }
      if (!S.hlOn) clearHl();
      if (S.data) {
        renderMonthly(); renderPareto(); renderCatDetails();
        renderExpenseDonut(); renderMethodDonut();
      }
      updateVisualTools();
      toast(S.hlOn ? 'Highlight on — click dims the others'
        : 'Filter on — click hides the others');
    });

    /* 影响此视觉对象的筛选器与切片器（PBI 头部漏斗） */
    var VT_LABEL = {
      region: 'Region', company: 'Company', category: 'Category', sub: 'Sub-category',
      expense: 'Expense Type', level: 'Supplier Category', method: 'Procurement Method',
      brand: 'Brand', supplier: 'Supplier',
      exempt_reason: 'Exemption Reason', single_reason: 'Single Source Reason'
    };
    var VT_OWN = { monthly: 'region', donut: 'expense', methodDonut: 'method',
      pareto: 'category', catdetail: 'category' };
    var VT_FUNNEL = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"' +
      ' stroke-linejoin="round" aria-hidden="true"><path d="M3 5h18l-7.2 8.4V20l-3.6-2v-4.6z"/></svg>';

    function vtRow(k, v, mine) {
      return '<div class="vt-row' + (mine ? ' mine' : '') + '"><b>' + esc(k) + '</b><span>' +
        esc(v) + '</span></div>';
    }
    function vtLocal(name) {
      var out = [];
      if (name === 'top') {
        if (S.topLocal.category) out.push(['Category', S.topLocal.category]);
        if (S.topLocal.method) out.push(['Procurement Method', S.topLocal.method]);
      } else if (name === 'contracts') {
        if (S.fc.type) out.push(['Agreement type', S.fc.type]);
        if (S.fc.status) out.push(['Status', S.fc.status]);
        if (S.fc.df || S.fc.dt) out.push(['Start date', (S.fc.df || '…') + ' → ' + (S.fc.dt || '…')]);
        if (S.fc.q) out.push(['Search contract', S.fc.q]);
        if (S.fc.sup) out.push(['Search supplier', S.fc.sup]);
      } else if (name === 'catdetail') {
        var q = ($('#catQ') ? $('#catQ').value : '').trim();
        if (q) out.push(['Search category', q]);
        var open = Object.keys(S.catSub || {}).length;
        if (open) out.push(['Sub-categories shown', nf(open, 0) + ' categories expanded']);
      } else if (name === 'methodDonut') {
        if (S.methodDrill) out.push(['Drill level', S.methodDrill + ' → reason']);
      } else if (name === 'pareto') {
        if (S.subLevel === 'sub') out.push(['Drill level', 'Category → Sub-category']);
      }
      return out;
    }
    function openVtPop(btn) {
      var pop = $('#vtPop'), name = btn.dataset.flt;
      if (!pop) return;
      if (!pop.hidden && pop.dataset.for === name) { closeVtPop(); return; }
      var h = '<div class="vt-h">' + VT_FUNNEL + 'Filters affecting this visual</div>';
      h += '<div class="vt-g">Slicers</div>' +
        vtRow('Fiscal year', S.fy || '—') +
        vtRow('Currency', S.cur === 'RM' ? 'RM (÷ 1.5)' : 'RMB');

      var keys = Object.keys(S.sel).filter(function (k) { return (S.sel[k] || []).length; });
      h += '<div class="vt-g">Filters</div>';
      if (name === 'contracts') {
        /* 协议表是独立数据源（vw_fa_analysis），页面筛选确实不作用于它 */
        h += '<div class="vt-none">Separate source — page filters do not apply</div>';
      } else if (!keys.length) {
        h += '<div class="vt-none">No page filters — showing everything</div>';
      } else {
        keys.forEach(function (k) {
          h += vtRow(VT_LABEL[k] || k, S.sel[k].join(', '), VT_OWN[name] === k);
        });
      }
      var loc = vtLocal(name);
      if (loc.length) {
        h += '<div class="vt-g">This visual</div>';
        loc.forEach(function (r) { h += vtRow(r[0], r[1], true); });
      }
      pop.innerHTML = h;
      pop.dataset.for = name;
      pop.hidden = false;
      var r = btn.getBoundingClientRect(), w = pop.offsetWidth || 268;
      pop.style.top = Math.round(r.bottom + 6) + 'px';
      pop.style.left = Math.round(Math.max(8, Math.min(r.right - w,
        window.innerWidth - w - 8))) + 'px';
    }
    function closeVtPop() {
      var pop = $('#vtPop');
      if (pop && !pop.hidden) { pop.hidden = true; pop.dataset.for = ''; }
    }
    document.addEventListener('click', function (e) {
      var b = e.target.closest('.v-flt');
      if (b) { e.preventDefault(); openVtPop(b); return; }
      if (!e.target.closest('#vtPop')) closeVtPop();
    });
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') closeVtPop();
    });
    window.addEventListener('scroll', function () {
      if ($('#vtPop') && !$('#vtPop').hidden) closeVtPop();
    }, true);

    // 重置
    $('#btnReset').addEventListener('click', function () {
      S.sel = {}; S.topLocal = { category: '', method: '' };
      S.methodDrill = null; S.subLevel = null; clearHl();
      $('#topCat').value = ''; $('#topMth').value = '';
      renderFilters(); refreshCompanyOptions(); loadAll();
      toast('Filters reset');
    });

    // 财年 / 货币
    $('#fySel').addEventListener('change', function (e) {
      var v = e.target.value;
      S.fy = v; S.fc.page = 1;
      /* 选财年 → 日期切片器自动落成该财年的实际区间（两者表达同一个范围） */
      var rg = v && S.fyRange && S.fyRange[v];
      if (rg) {
        S.detail.from = rg.from || '';
        S.detail.to = rg.to || '';
        S.detail.page = 1;
        if ($('#dtFrom')) $('#dtFrom').value = S.detail.from;
        if ($('#dtTo')) $('#dtTo').value = S.detail.to;
      }
      saveView();
      loadAll();
    });
    $('#curSeg').addEventListener('click', function (e) {
      var b = e.target.closest('button[data-cur]');
      if (!b) return;
      S.cur = b.dataset.cur; S.rate = S.cur === 'RM' ? 1.5 : 1;
      $$('#curSeg button').forEach(function (x) { x.classList.toggle('on', x === b); });
      loadAll();
    });

    // Chart5 局部筛选
    $('#topCat').addEventListener('change', function (e) {
      S.topLocal.category = e.target.value; loadTop();
    });
    $('#topMth').addEventListener('change', function (e) {
      S.topLocal.method = e.target.value; loadTop();
    });

    // 月度图图示切换（原区域堆叠 / 双柱对比）
    $('#mSw').addEventListener('click', function (e) {
      var btn = e.target.closest('button[data-m]');
      if (!btn) return;
      var m = btn.dataset.m;
      if (m === S.mMode) return;
      S.mMode = m;
      clearHl();       // 切到双柱视图后 region 维度不再参与绘制，高亮态作废
      $$('#mSw button').forEach(function (x) { x.classList.toggle('on', x === btn); });
      if (S.data && S.data.monthly) renderMonthly();
      saveView();
    });

    // 图表交互
    $('#chMonthly').addEventListener('click', function (e) {
      /* 双柱视图：点柱下钻该月明细（区域维度在这张图里没有） */
      var p = e.target.closest('rect.pseg');
      if (p) {
        var mi = +p.dataset.m;
        var mm = S.data.monthly;
        var lb = (mm.labelsLong || mm.labels)[mi];
        S.drawer.label = lb + ' · ';
        openDrawer(lb, '', { fm: mi + 1 });
        return;
      }
      var r = e.target.closest('rect.seg');
      if (!r) return;
      var reg = r.dataset.reg;
      var list = (S.sel.region || []).slice();
      var i = list.indexOf(reg);
      if (i >= 0) list.splice(i, 1); else list.push(reg);
      beginHl('monthly', 'region', list);
      setSel('region', list); renderFilters(); refreshCompanyOptions(); loadAll();
    });
    $('#chCat').addEventListener('click', function (e) {
      /* 点 +N / − 只展开收起子类别，不触发类别筛选 */
      var tog = e.target.closest('.cs-tog');
      if (tog) {
        e.stopPropagation();
        var c = tog.dataset.sub;
        if (S.catSub[c]) delete S.catSub[c]; else S.catSub[c] = 1;
        renderCatDetails(); updateVisualTools();
        return;
      }
      /* 展开后的子类别行也可点：按 Sub-category 交叉筛选 */
      var li = e.target.closest('li[data-subcat]');
      if (li) {
        var sv = li.dataset.subcat;
        var slist = (S.sel.sub || []).slice();
        var si = slist.indexOf(sv);
        if (si >= 0) slist.splice(si, 1); else slist.push(sv);
        beginHl('catdetail', 'sub', slist);
        setSel('sub', slist); renderFilters(); loadAll();
        return;
      }
      var tr = e.target.closest('.cd-row');
      if (!tr) return;
      var cat = tr.dataset.cat;
      var list = (S.sel.category || []).slice();
      var i = list.indexOf(cat);
      if (i >= 0) list.splice(i, 1); else list.push(cat);
      beginHl('catdetail', 'category', list);
      setSel('category', list); renderFilters(); loadAll();
    });
    $('#chPareto').addEventListener('click', function (e) {
      var r = e.target.closest('rect.pbar');
      if (!r || r.dataset.other) return;
      var key = S.subLevel === 'sub' ? 'sub' : 'category';
      var cat = r.dataset.cat;
      var list = (S.sel[key] || []).slice();
      var i = list.indexOf(cat);
      if (i >= 0) list.splice(i, 1); else list.push(cat);
      beginHl('pareto', key, list);
      setSel(key, list); renderFilters(); loadAll();
    });
    function toggleSel(key, vals, src) {
      var list = (S.sel[key] || []).slice(), hit = true;
      vals.forEach(function (v) {
        var i = list.indexOf(v);
        if (i >= 0) list.splice(i, 1); else { list.push(v); hit = false; }
      });
      if (hit && !vals.length) { /* no-op */ }
      beginHl(src, key, list);
      setSel(key, list); renderFilters(); loadAll();
    }
    function bindDonut(chartId, legendId, key) {
      [chartId, legendId].forEach(function (id) {
        $('#' + id).addEventListener('click', function (e) {
          var p = e.target.closest('[data-exp]');
          if (!p) return;
          toggleSel(key, [p.dataset.exp], chartId);
        });
      });
    }
    bindDonut('chDonut', 'donutLegend', 'expense');

    /* 采购方式环形：第一层点 SS-Exempt / SS-Non Exempt 下钻到理由层；
       理由层点扇区按该理由的原始值交叉筛选（并锁住对应采购方式）。 */
    var MD_REASON_KEY = { 'SS-Exempt': 'exempt_reason', 'SS-Non Exempt': 'single_reason' };
    function onMethodPick(v) {
      if (!S.methodDrill) {
        if (MD_REASON_KEY[v]) {
          S.methodDrill = v;
          renderMethodDonut(); updateBacks(); saveView();
          loadMethodDrill(v, function () { renderMethodDonut(); });
          return;
        }
        toggleSel('method', [v], 'chMethodDonut');
        return;
      }
      var pack = S.drill[S.methodDrill];
      var row = ((pack && pack.rows) || []).filter(function (x) { return x.k === v; })[0];
      var raws = (row && row.raws && row.raws.length) ? row.raws : [v];
      var key = MD_REASON_KEY[S.methodDrill];
      /* 锁方式 + 理由：全页口径与下钻图一致（一次 loadAll，避免重复请求） */
      S.sel.method = [S.methodDrill];
      saveView();
      toggleSel(key, raws, 'chMethodDonut');
    }
    ['chMethodDonut', 'methodDonutLegend'].forEach(function (id) {
      $('#' + id).addEventListener('click', function (e) {
        var p = e.target.closest('[data-exp]');
        if (p) onMethodPick(p.dataset.exp);
      });
    });
    $('#chTop').addEventListener('click', function (e) {
      var g = e.target.closest('.trow');
      if (!g) return;
      var sup = g.dataset.sup;
      S.drawer.label = sup + ' · ';      // 副标题里保留全称
      openDrawer(supEn(sup), '', { supplier: sup });
    });
    $('#chHeat').addEventListener('click', function (e) {
      var c = e.target.closest('.hcell');
      if (!c) return;
      var sup = c.dataset.sup, cat = c.dataset.cat;
      S.drawer.label = sup + ' · ';
      openDrawer(supEn(sup), '', { supplier: sup, category: cat });
    });

    // 悬浮提示（月度）
    $('#chMonthly').addEventListener('mousemove', function (e) {
      var pb = e.target.closest('rect.pseg');
      if (pb && S.data) {
        var mi2 = +pb.dataset.m, isFc = pb.dataset.s === 'frm';
        var mm2 = S.data.monthly;
        var vv = +pb.dataset.v, base2 = mm2.total[mi2] || 0;
        showTip('<b>' + esc((mm2.labelsLong || mm2.labels)[mi2]) + ' · ' +
          (isFc ? 'Framework Contract' : 'Total') + '</b>' +
          '<div class="tip-row"><span>Amount</span><span>' + money(vv, 1) + '</span></div>' +
          '<div class="tip-row"><span>Share of month</span><span>' +
          pct(base2 ? vv / base2 : 0) + '</span></div>' +
          '<div class="tip-row"><span>Click</span><span>details</span></div>',
          e.clientX, e.clientY);
        return;
      }
      var r = e.target.closest('rect.seg');
      if (!r || !S.data) { hideTip(); return; }
      var mi = +r.dataset.m, reg = r.dataset.reg;
      var s = S.data.monthly.series.find(function (x) { return x.k === reg; });
      var v = s ? (s.vals[mi] || 0) : 0;
      var tot = S.data.monthly.cur[mi] || 1;
      showTip('<b>' + esc(S.data.monthly.labels[mi]) + ' · ' +
        esc(s && s.l ? s.l : reg) + '</b>' +
        '<div class="tip-row"><span>Amount</span><span>' + money(v, 1) + '</span></div>' +
        '<div class="tip-row"><span>Share</span><span>' + pct(v / tot) + '</span></div>' +
        '<div class="tip-row"><span>Month total</span><span>' + money(tot, 1) + '</span></div>',
        e.clientX, e.clientY);
    });
    // 悬浮提示（帕累托）
    $('#chPareto').addEventListener('mousemove', function (e) {
      var r = e.target.closest('rect.pbar');
      if (!r || !S.data) { hideTip(); return; }
      var row = (S.data.pareto.rows || []).find(function (x) { return x.k === r.dataset.cat; });
      if (!row) { hideTip(); return; }
      showTip('<b>' + esc(row.k) + '</b>' +
        '<div class="tip-row"><span>Amount</span><span>' + money(row.v, 1) + '</span></div>' +
        '<div class="tip-row"><span>Share</span><span>' +
        pct(row.v / (S.data.pareto.total || 1)) + '</span></div>' +
        '<div class="tip-row"><span>Cumulative</span><span>' + pct(row.cum) + '</span></div>' +
        '<div class="tip-row"><span>Orders</span><span>' + nf(row.n, 0) + '</span></div>',
        e.clientX, e.clientY);
    });
    // 悬浮提示（两个环形图）
    [['chDonut', 'donut'], ['chMethodDonut', 'methodDonut']].forEach(function (pair) {
      $('#' + pair[0]).addEventListener('mousemove', function (e) {
        var p = e.target.closest('[data-exp]');
        if (!p || !S.data) { hideTip(); return; }
        var row = (S.data[pair[1]] || []).find(function (x) { return x.k === p.dataset.exp; });
        if (!row) { hideTip(); return; }
        showTip('<b>' + esc(row.k) + '</b>' +
          '<div class="tip-row"><span>Amount</span><span>' + money(row.v, 1) + '</span></div>' +
          '<div class="tip-row"><span>Share</span><span>' + pct(row.share) + '</span></div>' +
          '<div class="tip-row"><span>Orders</span><span>' + nf(row.n, 0) + '</span></div>',
          e.clientX, e.clientY);
      });
    });
    // 悬浮提示（热力 / 横条）
    $('#chHeat').addEventListener('mousemove', function (e) {
      var c = e.target.closest('.hcell');
      if (!c || !S.data) { hideTip(); return; }
      var row = S.data.heat.rows.find(function (x) { return x.k === c.dataset.sup; });
      var v = 0;
      if (row) v = row.vals[S.data.heat.cols.indexOf(c.dataset.cat)] || 0;
      showTip('<b>' + esc(c.dataset.sup) + '</b>' +
        '<div class="tip-row"><span>Category</span><span>' + esc(c.dataset.cat) + '</span></div>' +
        '<div class="tip-row"><span>Amount</span><span>' + money(v, 1) + '</span></div>' +
        '<div style="color:#A9C0D6;margin-top:3px">Click for details</div>',
        e.clientX, e.clientY);
    });
    $('#chTop').addEventListener('mousemove', function (e) {
      var g = e.target.closest('.trow');
      if (!g || !S.data) { hideTip(); return; }
      var row = (S.data.top || []).find(function (x) { return x.k === g.dataset.sup; });
      if (!row) { hideTip(); return; }
      showTip('<b>' + esc(row.k) + '</b>' +
        '<div class="tip-row"><span>Amount</span><span>' + money(row.v, 1) + '</span></div>' +
        '<div class="tip-row"><span>Orders</span><span>' + nf(row.n, 0) + '</span></div>' +
        '<div class="tip-row"><span>Categories</span><span>' + nf(row.cats, 0) + '</span></div>' +
        '<div style="color:#A9C0D6;margin-top:3px">Click for details</div>',
        e.clientX, e.clientY);
    });
    ['#chMonthly', '#chPareto', '#chDonut', '#chMethodDonut', '#chTop', '#chHeat'].forEach(function (sel) {
      $(sel).addEventListener('mouseleave', hideTip);
    });

    // 框架协议表：排序 / 翻页 / 局部筛选
    $('#fcTbl thead').addEventListener('click', function (e) {
      var flt = e.target.closest('.fc-hflt');
      if (flt) {                            // 表头漏斗：开/关这一列的选项浮层
        var key = flt.dataset.hf;
        var sameOpen = fcPopEl && !fcPopEl.hidden && fcPopEl.dataset.k === key;
        if (sameOpen) closeFcPop(); else openFcPop(flt, key);
        return;
      }
      var th = e.target.closest('th[data-sort]');
      if (!th) return;
      var k = th.dataset.sort;
      if (S.fc.sort === k) S.fc.dir = S.fc.dir === 'asc' ? 'desc' : 'asc';
      else { S.fc.sort = k; S.fc.dir = 'desc'; }
      S.fc.page = 1;
      loadContracts();
    });
    $('#fcPager').addEventListener('click', function (e) {
      var b = e.target.closest('button[data-pg]');
      if (!b) return;
      S.fc.page = +b.dataset.pg; loadContracts();
    });
    function bindFc(id, key) {
      var t = null;
      $(id).addEventListener('input', function (e) {
        clearTimeout(t);
        t = setTimeout(function () {
          S.fc[key] = e.target.value.trim(); S.fc.page = 1; loadContracts();
        }, 300);
      });
    }
    bindFc('#fcQ', 'q');
    bindFc('#fcSup', 'sup');
    ['#fcType', '#fcStatus', '#fcFrom', '#fcTo'].forEach(function (sel) {
      $(sel).addEventListener('change', function (e) {
        S.fc[{ '#fcType': 'type', '#fcStatus': 'status',
               '#fcFrom': 'df', '#fcTo': 'dt' }[sel]] = e.target.value;
        markFcHdrSel();
        S.fc.page = 1; loadContracts();
      });
    });
    /* 表头漏斗浮层：点选项 = 改隐藏 <select> 的值并触发它的 change（口径只有一处） */
    document.addEventListener('click', function (e) {
      if (!fcPopEl || fcPopEl.hidden) return;
      var op = e.target.closest('.fc-pop button[data-v]');
      if (op) {
        var sel = $(FC_HF[fcPopEl.dataset.k].sel);
        if (sel) {
          sel.value = op.dataset.v;
          sel.dispatchEvent(new Event('change', { bubbles: true }));
        }
        closeFcPop();
        return;
      }
      if (e.target.closest('.fc-pop-clr')) {          // 搜索浮层里的 Clear
        var inp = $(FC_HF[fcPopEl.dataset.k].inp);
        if (inp) {
          inp.value = '';
          inp.dispatchEvent(new Event('input', { bubbles: true }));
          inp.focus();
        }
        return;
      }
      if (!e.target.closest('.fc-pop') && !e.target.closest('.fc-hflt')) closeFcPop();
    });
    var fcw = document.querySelector('#pgFramework .tbl-wrap');
    if (fcw) fcw.addEventListener('scroll', closeFcPop, { passive: true });
    window.addEventListener('resize', closeFcPop);
    $('#fcReset').addEventListener('click', function () {
      S.fc = { page: 1, size: 50, sort: 'start', dir: 'desc',
               q: '', sup: '', type: '', df: '', dt: '', status: '' };
      ['#fcQ', '#fcSup', '#fcType', '#fcStatus', '#fcFrom', '#fcTo'].forEach(function (sel) {
        $(sel).value = '';
      });
      markFcHdrSel();
      loadContracts();
      toast('Agreement filters reset');
    });

    /* 顶栏「导出当前视图」= 当前财年+筛选下的采购申请明细；
       框架协议表头「导出」= 协议清单（独立数据源）。
       静态快照里 window.__SNAP_DOWNLOAD__ 会接管，改由前端拼 CSV。 */
    function exportView() {
      download('/api/board/export.csv?' +
        qs({ sort: 'date', dir: 'desc' }).toString());
      toast('Exporting current view…');
    }
    function exportContracts() {
      download('/api/board/contracts.csv?' + fcqs().toString());
      toast('Exporting agreements…');
    }
    $('#btnExport').addEventListener('click', exportView);
    $('#fcExport').addEventListener('click', exportContracts);

    // 抽屉
    var catT = null;
    $('#catQ').addEventListener('input', function (e) {
      clearTimeout(catT);
      catT = setTimeout(renderCatDetails, 150);
    });

    $('#drawerClose').addEventListener('click', closeDrawer);
    $('#mask').addEventListener('click', closeDrawer);
    /* 滚轮兜底：分页条/抽屉头部在表格滚动容器之外，滚轮会穿透到背景页 —— 直接吞掉 */
    $('#drawer').addEventListener('wheel', function (e) {
      if (!e.target.closest('.tbl-wrap')) e.preventDefault();
    }, { passive: false });
    $('#mask').addEventListener('wheel', function (e) { e.preventDefault(); }, { passive: false });
    $('#drawerPager').addEventListener('click', function (e) {
      var b = e.target.closest('button[data-pg]');
      if (!b) return;
      S.drawer.page = +b.dataset.pg;
      loadDrawer();
      $('.drawer-b').scrollTop = 0;
    });
    $('#drawerSize').addEventListener('change', function (e) {
      S.drawer.size = +e.target.value; S.drawer.page = 1; loadDrawer();
    });
    var dqT = null;
    $('#drawerQ').addEventListener('input', function (e) {
      clearTimeout(dqT);
      dqT = setTimeout(function () {
        S.drawer.q = e.target.value.trim(); S.drawer.page = 1; loadDrawer();
      }, 300);
    });

    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') { closeDrawer(); $$('.fdrop').forEach(function (d) { d.classList.remove('open'); }); }
    });

    /* 同一排的卡片高度会互相牵动（右侧热力卡更高时左卡被拉高）：
       卡片尺寸一变就重铺一次类别表，合计行才始终贴着卡片底边 */
    if (window.ResizeObserver) {
      var catCard = $('#catCard');
      if (catCard) new ResizeObserver(function () {
        requestAnimationFrame(fitCatTable);
      }).observe(catCard);
    }

    // 自适应重绘
    var rt = null;
    window.addEventListener('resize', function () {
      clearTimeout(rt);
      rt = setTimeout(function () {
        if (!S.data) return;
        renderMonthly(); renderPareto(); renderCatDetails();
        renderExpenseDonut(); renderMethodDonut(); renderHeat(); renderTop();
      }, 180);
    });
  }

  /* ================================================ 取值计算逻辑说明
     每个视觉对象配一个「?」按钮，点开说明四件事：
       ① 这个数是怎么来的（大白话）
       ② 用了数据库里哪些中文字段
       ③ 这一次查询实际生效的条件（人话版 WHERE）
       ④ 名词解释
     不展示 SQL 语句 —— 用户看的是口径，不是语句。 */
  var SRC = {
    prpo: '采购申请单',
    po: '采购订单',
    cal: '日期表',
    comp: '公司维度表',
    catmap: '类别映射表',
    expmap: '支出类型映射表',
    methodmap: '采购方法映射表',
    fa: '框架协议统计视图'
  };

  /* 名词解释：按对象取用，只解释这一块真用到的词 */
  var TERMS = {
    where: ['WHERE（筛选条件）', '数据库语句里「先按条件把不符合的记录剔掉」的那一段。' +
      '这里不用看语句，上面「当前生效的条件」就是它的中文版。'],
    dedupe: ['去重', '同一个值出现多次只算一次。比如同一家供应商开了 10 张单，供应商数仍然只算 1 家。'],
    median: ['中位数', '把所有数从小到大排，取正中间那个（偶数个时取中间两个的平均）。比平均数更不容易被个别极端值带偏。'],
    yoy: ['同比', '跟上一个财年同期相比的增减幅度。'],
    usage: ['执行率', '框架协议的实际采购额 ÷ 合同金额。'],
    cv: ['可加预估金额', '一张前置审批被多份协议共用时，预估金额只算一次，避免同一笔预算被重复计入。'],
    pareto: ['帕累托图', '按金额从高到低排柱子，再叠一条累计占比曲线，用来看「少数类别贡献了大部分金额」。'],
    fiscal: ['财年', '公司财年从 7 月算到次年 6 月，FY2627 就是 2026-07-01 至 2027-06-30。']
  };

  /* 每个对象额外解释哪些词（WHERE 由 openSpec 统一补） */
  var TERMS_BY_KEY = {
    global: ['fiscal', 'yoy'],
    kpi_amount: ['yoy'],
    kpi_cp: [],
    kpi_poSpend: [],
    kpi_txn: [],
    kpi_suppliers: ['dedupe'],
    kpi_stores: ['dedupe'],
    kpi_days: ['median', 'yoy'],
    filter_brand: [],
    filter_category: [],
    filter_expense: [],
    filter_method: [],
    filter_level: [],
    filter_region: [],
    filter_company: [],
    monthly: ['fiscal', 'yoy'],
    donut: [],
    methodDonut: [],
    pareto: ['pareto'],
    top: [],
    heat: [],
    contracts: ['cv', 'usage']
  };

  var SPEC_DEF = {
    /* ---------------- 整页口径 ---------------- */
    global: {
      title: '看板整体口径',
      source: '数据来源：' + SRC.prpo + '（PRPO）+ ' + SRC.po + ' + ' +
        SRC.cal + ' + ' + SRC.comp,
      notes: [
        '整页数字都从采购申请单一行一行算出来，再按当前筛选条件汇总，没有额外的中间表参与。',
        '顶部「财年」决定看哪一年；「RM / RMB」只改变显示单位，底层金额始终是人民币原值。',
        '中间那排筛选（品牌、类别、支出类型、供应商属性、区域）同时作用于 KPI 和所有图表，多选取「或」的关系；公司收在区域下拉的第二段，是区域的下级。',
        '点图表上的柱子、扇区、热力格会再加一层筛选，顶部的小标签可以单独去掉。',
        '最下面的「框架协议统计」是另一套数据，不跟着上面的筛选走。'
      ],
      fields: [
        ['金额', '含增值税总价', SRC.prpo],
        ['日期 / 财年', '提交日期 → 财年、财月', SRC.prpo + ' + ' + SRC.cal],
        ['公司 / 区域 / 品牌', '公司 → 公司简称、区域、品牌', SRC.comp],
        ['类别', '采购类别（归并后 11 类）', SRC.prpo + ' → ' + SRC.catmap],
        ['支出类型', '支出类型（4 类）', SRC.prpo + ' → ' + SRC.expmap],
        ['采购方式', '选择采购方法', SRC.prpo + ' → ' + SRC.methodmap]
      ]
    },

    /* ---------------- 地图 / 同比页 ---------------- */
    geo: {
      title: '地图与类别同比口径',
      source: '数据来源：dash_po（PRPO 打底）+ fct_prpo（理由）+ fct_company_location（坐标）',
      notes: [
        '地图上一个气泡＝一家公司所在的城市；同一城市有多家公司时合并成一个气泡，坐标与颜色取该城金额最大那家店。',
        '下拉四个口径：PRPO 采购额＝Total Price；PO Paid＝已关联 PO 的实付合计；小额采购＝采购方式为 Micro；竞争力不足＝单一来源采购理由为 Inadequate Competition（只在 fct_prpo 里，按单号关联，不会放大行数）。',
        '下方 Category 同比拿当前财年与上一财年各算一次，ΔPY＝本期－上期，ΔPY%＝ΔPY÷上期；绿色代表增加、红色代表减少。',
        '当前财年还在进行中，和完整上一财年比，整体降幅会偏大，这是数据进度差异、不是采购骤降。',
        '顶栏的财年、币种和全局筛选同样作用于这一页；公司列表里勾选会同时筛选地图和同比表。'
      ],
      fields: [
        ['地图气泡金额', '含增值税总价 / 实付合计', SRC.prpo],
        ['竞争力不足', '单一来源采购理由', SRC.prpo + ' → fct_prpo'],
        ['城市 / 坐标', '经度、纬度、市', SRC.comp],
        ['类别', '采购类别（归并后 11 类）', SRC.prpo + ' → ' + SRC.catmap]
      ]
    },

    /* ---------------- KPI 卡片（每张卡单独说明） ---------------- */
    kpi_amount: {
      title: 'KPI · 采购总额',
      source: '数据来源：' + SRC.prpo + '（PRPO）',
      notes: [
        '把当前筛选后剩下的所有' + SRC.prpo + '的「含增值税总价」加起来。',
        '库里的原单位是人民币元；顶部切到 RM 时，只是显示时按 ÷1.5 换算，数据本身没变。',
        '同比是跟上一个财年、用同一套筛选条件算出来的金额比较。'
      ],
      fields: [['采购总额', '含增值税总价', SRC.prpo]]
    },
    kpi_cp: {
      title: 'KPI · 集中采购金额',
      source: '数据来源：' + SRC.prpo + '（PRPO）',
      notes: [
        '只挑「采购方式」属于集中采购范围的单子，把它们的「含增值税总价」加起来。',
        '集中采购范围＝单一来源豁免（SS-Exempt）、单一来源非豁免（SS-Non Exempt）、招标（Tender）。',
        '询价、议价等其他方式不计入。'
      ],
      fields: [
        ['集中采购金额', '含增值税总价', SRC.prpo],
        ['（限定）采购方式', '选择采购方法 ＝ SS-Exempt / SS-Non Exempt / Tender', SRC.prpo]
      ]
    },
    kpi_poSpend: {
      title: 'KPI · PO 实付金额',
      source: '数据来源：' + SRC.po + '，按单号关联到' + SRC.prpo,
      notes: [
        SRC.prpo + '里没有「已付金额」这一项，所以这里是按单号回到' + SRC.po + '取「总支付总额（人民币）」汇总过来的。',
        '采购状态为「已取消（Cancelled）」的订单不计入。',
        '一张' + SRC.prpo + '对应多张 PO 时，金额相加。',
        '所以这个数天然小于「采购总额」，差额是还没开出 PO 或还没付款的部分。'
      ],
      fields: [
        ['PO 实付金额', '总支付总额（人民币）', SRC.po],
        ['（关联）采购申请单', '已批准的采购申请参考 ＝ 表单编号', SRC.po + ' ↔ ' + SRC.prpo]
      ]
    },
    kpi_txn: {
      title: 'KPI · 单据数',
      source: '数据来源：' + SRC.prpo + '（PRPO）',
      notes: ['当前筛选后还剩多少张采购申请单。'],
      fields: [['单据数', '表单编号', SRC.prpo]]
    },
    kpi_suppliers: {
      title: 'KPI · 供应商数',
      source: '数据来源：' + SRC.prpo + '（PRPO）',
      notes: [
        '统计出现了多少家不同的供应商，同一家供应商开多张单只算一家（去重）。',
        '供应商名取的是「供应商名称」里的中文部分。'
      ],
      fields: [['供应商数', '供应商名称', SRC.prpo]]
    },
    kpi_stores: {
      title: 'KPI · 覆盖门店',
      source: '数据来源：' + SRC.comp,
      notes: [
        '采购申请单上记的是公司全称，这里统一换成「江门悦之宝」这样的简称后再去重统计。',
        '简称取自' + SRC.comp + '的「公司简称」。'
      ],
      fields: [['覆盖门店', '公司简称', SRC.comp + '（由' + SRC.prpo + '的「公司」关联）']]
    },
    kpi_days: {
      title: 'KPI · 中位采购周期',
      source: '数据来源：' + SRC.prpo + '（PRPO）',
      notes: [
        '每张单的周期＝「审批时间」减「提交日期」，单位是天。',
        '把所有单的周期排序后取正中间那个值（中位数），不是平均数 —— 少数超长单不会把整体拉高。',
        '还没审批的单不参与计算。'
      ],
      fields: [['采购周期', '审批时间 − 提交日期（天）', SRC.prpo]]
    },

    /* ---------------- 筛选下拉 ---------------- */
    filter_brand: {
      title: '筛选 · 品牌',
      source: '数据来源：' + SRC.comp,
      notes: [
        '品牌是按' + SRC.prpo + '上的「公司」关联' + SRC.comp + '带出来的。',
        '可以多选：选中的品牌之间是「或」的关系，命中任意一个就算。',
        '下拉里右边的数字是这个品牌下还剩多少张单。'
      ],
      fields: [['品牌', '品牌（Brand）', SRC.comp]]
    },
    filter_category: {
      title: '筛选 · 类别',
      source: '数据来源：' + SRC.prpo + '（PRPO）',
      notes: [
        '原始的「采购类别」有 94 种写法，先经过' + SRC.catmap + '归并成 11 个标准类别。',
        '可以多选，选中之间是「或」的关系。'
      ],
      fields: [['类别', '采购类别（归并后 11 类）', SRC.prpo + ' → ' + SRC.catmap]]
    },
    filter_expense: {
      title: '筛选 · 支出类型',
      source: '数据来源：' + SRC.prpo + '（PRPO）',
      notes: [
        '原始的「支出类型」经' + SRC.expmap + '整理成四类：OPEX 运营支出、TRADE 贸易、A&P 市场推广、CAPEX 资本开支。',
        '可以多选，选中之间是「或」的关系。'
      ],
      fields: [['支出类型', '支出类型（OPEX / TRADE / A&P / CAPEX）', SRC.prpo + ' → ' + SRC.expmap]]
    },
    filter_level: {
      title: '筛选 · 供应商属性',
      source: '数据来源：' + SRC.prpo + '（PRPO）',
      notes: [
        '原值就是「供应商属性」这一列：字母 A 全部豁免、B 部分豁免、C 不豁免。',
        '看板上显示的是对应的英文名（A-Fully Exempt 等）。',
        '可以多选，选中之间是「或」的关系。'
      ],
      fields: [['供应商属性', '供应商属性', SRC.prpo]]
    },
    filter_region: {
      title: '筛选 · 区域 / 公司',
      source: '数据来源：' + SRC.comp,
      notes: [
        '区域由' + SRC.prpo + '的「公司」关联' + SRC.comp + '带出来的（南区、北区等）。',
        '下拉里分两段：上面是区域，下面是公司。公司是区域的下级 —— 先勾区域，公司列表只保留这些区域下的公司；不勾区域时是全部公司。',
        '两段可以各自多选，选中之间是「或」的关系；两段之间是「且」（区域选中项 ∩ 公司选中项）。',
        '公司值同样由' + SRC.prpo + '的「公司」关联' + SRC.comp + '带出。'
      ],
      fields: [
        ['区域', '区域（RegionAbbreviationCN）', SRC.comp],
        ['公司', '公司（Company_cn）', SRC.comp]
      ]
    },
    filter_company: {
      title: '筛选 · 公司',
      source: '数据来源：' + SRC.comp,
      notes: [
        '公司收在 Region 下拉的第二段，不再单独占一个筛选格。',
        '公司值由' + SRC.prpo + '的「公司」关联' + SRC.comp + '带出。',
        '可以多选，选中之间是「或」的关系。'
      ],
      fields: [['公司', '公司（Company_cn）', SRC.comp]]
    },

    /* ---------------- 图表 ---------------- */
    monthly: {
      title: '月度支出（两种图示）',
      source: '数据来源：' + SRC.prpo + ' + ' + SRC.cal + ' + ' + SRC.comp,
      notes: [
        '右上角可切换两种图示，数据口径相同。',
        'Region（默认）：柱子按财月堆叠，一根柱子里的不同颜色是不同区域；虚线是上一个财年同月的总额；点柱子按区域筛选。',
        'Total vs Framework：每月两根柱，深色是当月全部采购，中蓝是其中「通过框架协议方式申请」的部分；柱顶胶囊就是该柱金额，点柱子看当月按公司汇总。',
        '两种视图都用同一个「豁免单一来源的理由 = Ongoing Frame Contract」判据识别框架协议，不做映射。',
        '公司的财年从 7 月开始，所以图上第 1 个月对应日历的 7 月；还没到的月份不补 0。'
      ],
      fields: [
        ['月度金额', '含增值税总价', SRC.prpo],
        ['框架协议金额', '含增值税总价（豁免理由 = Ongoing Frame Contract）', SRC.prpo],
        ['月份', '财月（财年从 7 月起算）', SRC.cal],
        ['区域', '区域', SRC.comp]
      ]
    },
    donut: {
      title: '支出类型分布',
      source: '数据来源：' + SRC.prpo + ' + ' + SRC.expmap,
      notes: [
        '把金额按支出类型分成四块：OPEX 运营支出、TRADE 贸易、A&P 市场推广、CAPEX 资本开支。',
        '每块的百分比＝该类金额 ÷ 当前筛选下的总金额。'
      ],
      fields: [
        ['支出类型', '支出类型', SRC.prpo + ' → ' + SRC.expmap],
        ['金额', '含增值税总价', SRC.prpo]
      ]
    },
    methodDonut: {
      title: '采购方式分布',
      source: '数据来源：' + SRC.prpo,
      notes: [
        '把金额按采购方式（Procurement Method）分成若干块，空值归为 Unassigned。',
        '每块百分比＝该方式金额 ÷ 当前筛选下的总金额。',
        '点 SS-Exempt / SS-Non Exempt 会下钻到第二层：前者按「豁免单一来源的理由」、后者按「单一来源采购理由」，都经聚合映射表归一；再点理由扇区即按该理由交叉筛选。'
      ],
      fields: [
        ['采购方式', 'Procurement Method', SRC.prpo],
        ['金额', '含增值税总价', SRC.prpo]
      ]
    },
    catdetail: {
      title: '类别明细',
      source: '数据来源：' + SRC.prpo + ' + ' + SRC.catmap + '（与帕累托图同一份结果）',
      notes: [
        '这张表和左边的帕累托图是同一份汇总结果，只是换了看法：一行一个采购类别。',
        '「供应商数」是这一类别下去重后的供应商家数；「单据数」是采购申请单的条数。',
        '「金额」= 该类别所有采购申请单的含增值税总价之和；「PO Paid」= 这些申请关联采购订单的实际支付总额（Cancelled 不计）。',
        '「占比」= 该类别金额 ÷ 当前筛选下的总金额，条形长度就是它；「Sub-Category」显示该类别下金额最高的采购子类别。',
        '一个类别下子类别往往有十几条，所以默认只显示金额最高的一条；点右侧「+N」展开该类别全部子类别（各自金额、PO Paid、单数、占本类别比例），再点「−」收起。',
        '类别搜索框只过滤这张表的行（按类别名或子类别名匹配），不影响 KPI 和上面的图表。',
        '合计行的供应商数是整页去重后的家数，不是各类别相加 —— 同一家供应商可能跨类别，相加会重复计算。',
        '点任意一行等于勾选这个类别（再点一次取消），和点帕累托柱子是同一个动作。'
      ],
      fields: [
        ['类别', '采购类别（归并后 11 类）', SRC.prpo + ' → ' + SRC.catmap],
        ['Sub-Category', 'Table - 采购子类别 → Sub-Category（默认折叠，显示金额最高项）', SRC.prpo],
        ['供应商数', '供应商名称（去重计数）', SRC.prpo],
        ['单据数', '表单编号（计数）', SRC.prpo],
        ['金额', '含增值税总价（求和）', SRC.prpo],
        ['PO Paid', 'PO Paid（求和）', SRC.prpo],
        ['占比', '金额 ÷ 当前筛选下的总金额', '由上面两列算出']
      ]
    },
    pareto: {
      title: '类别支出帕累托',
      source: '数据来源：' + SRC.prpo + ' + ' + SRC.catmap,
      notes: [
        '柱子按金额从高到低排；折线是累计占比（从第一根柱累加到当前柱的金额 ÷ 总金额）。',
        '虚线是 80% 参考线，线以上的类别就是主要贡献类别。',
        '原始类别有 94 种写法，经' + SRC.catmap + '归并成 11 类，实在归不进去的进「Others」。'
      ],
      fields: [
        ['类别', '采购类别（归并后 11 类）', SRC.prpo + ' → ' + SRC.catmap],
        ['金额', '含增值税总价', SRC.prpo]
      ]
    },
    top: {
      title: 'Top 10 供应商',
      source: '数据来源：' + SRC.prpo + '（PRPO）',
      notes: [
        '按供应商把金额汇总起来后取前 10 名。',
        '图里那两个下拉（类别、采购方法）是「只管这张图」的筛选，不影响 KPI 和其它图表。',
        '供应商原始名是「中文全称-英文名」，列表里只显示中文部分，鼠标悬停给全名。',
        '点任意一行打开右侧抽屉，按公司汇总（单数 / 供应商数 / 采购额 / PO Paid / 平均周期）。'
      ],
      fields: [
        ['供应商', '供应商名称', SRC.prpo],
        ['金额', '含增值税总价', SRC.prpo]
      ]
    },
    heat: {
      title: 'Top 10 供应商 × 类别',
      source: '数据来源：' + SRC.prpo + '（PRPO）',
      notes: [
        '行是供应商前 10 名，列是金额前 8 个类别。',
        '每个格子＝这家供应商在这个类别下的金额合计；颜色越深金额越大，按当前筛选下的最大值归一化。',
        '只算这 10 × 8 的交叉部分，不是全量交叉。',
        '点任意格子打开右侧抽屉，按公司汇总这一格（供应商 × 类别）的数据。'
      ],
      fields: [
        ['行（供应商）', '供应商名称', SRC.prpo],
        ['列（类别）', '采购类别', SRC.prpo],
        ['格子金额', '含增值税总价', SRC.prpo]
      ]
    },
    contracts: {
      title: '框架协议统计',
      source: '数据来源：' + SRC.fa + '（框架协议表 + 前置审批表 + ' + SRC.prpo + '）',
      notes: [
        '这张表是独立数据源，不跟着上面的品牌、区域等筛选走，只受本表自己的筛选影响。',
        'CV（合同金额）来自前置审批单的「预估项目金额」；一张前置审批被多份协议共用时，合计里只算一次，所以汇总用的是「预估金额_可加」。',
        'PA（实际采购额）只统计「通过框架协议方式申请」的采购单，即「豁免单一来源的理由」＝ Ongoing Frame Contract。',
        '执行率＝PA ÷ CV；整表执行率＝全部 PA 之和 ÷ 全部 CV 之和。',
        '剩余天数＝结束日期 − 今天：小于 0 已过期，0～90 天即将到期，大于 90 天有效。'
      ],
      fields: [
        ['合同名称', '合同名称CN', '框架协议表'],
        ['供应商', '供应商名称', '框架协议表'],
        ['合同金额 CV', '预估项目金额', '前置审批表'],
        ['实际采购额 PA', '含增值税总价', SRC.prpo],
        ['执行率', '实际采购额 ÷ 预估金额', '算出来的'],
        ['开始 / 结束日期', '开始日期、结束日期', '框架协议表'],
        ['剩余天数', '结束日期 − 今天', '算出来的'],
        ['适用领域', '适用领域（中文）', '框架协议表']
      ]
    }
  };

  /* 当前生效的条件 —— 中文版的 WHERE */
  function specCond(key) {
    var out = [];
    if (key === 'contracts') {
      var fc = S.fc || {};
      out.push(['协议类型', fc.type || '全部']);
      var st = { active: '有效（剩 90 天以上）', expiring: '90 天内到期', expired: '已过期' }[fc.status];
      out.push(['到期状态', st || '全部']);
      out.push(['开始日期区间', (fc.df || '不限') + ' ~ ' + (fc.dt || '不限')]);
      out.push(['合同名包含', fc.q || '（无）']);
      out.push(['供应商包含', fc.sup || '（无）']);
      out.push(['说明', '本表不吃上方筛选，只按上面这些条件筛']);
      return out;
    }
    var d = S.data || {}, p = d.params || {};
    out.push(['财年', (p.fy || '—') + (p.prevFy ? '（同比对照 ' + p.prevFy + '）' : '')]);
    out.push(['货币', S.cur === 'RM' ? 'RM（人民币 ÷ 1.5 换算显示）' : 'RMB（人民币原值）']);
    var r = d.range || {};
    if (r.from) out.push(['数据范围', r.from + ' ~ ' + r.to]);
    var names = { brand: '品牌', category: '类别', expense: '支出类型',
                  level: '供应商属性', region: '区域' };
    var parts = [];
    Object.keys(names).forEach(function (k) {
      var v = (S.sel || {})[k] || [];
      if (v.length) parts.push(names[k] + '：' + v.join('、'));
    });
    out.push(['筛选条件', parts.length ? parts.join('；') : '未设置（全量数据）']);
    if (key === 'top') {
      var tl = S.topLocal || {};
      out.push(['本图额外筛选', '类别＝' + (tl.category || '全部') + '，采购方法＝' +
        (tl.method || '全部') + '（只影响这张图）']);
    }
    if (d.status) out.push(['命中单据', nf(d.status.txn, 0) + ' 笔']);
    return out;
  }

  function openSpec(key) {
    var def = SPEC_DEF[key];
    if (!def) return;
    var pop = $('#specPop');
    $('#spTitle').textContent = def.title;
    $('#spSource').textContent = def.source || '';
    $('#spNotes').innerHTML = (def.notes || []).map(function (n) {
      return '<li>' + n + '</li>';
    }).join('');
    /* 字段对照 */
    var fh = '<div class="sp-frow sp-fhead"><span>看板显示</span>' +
      '<span>数据库字段</span><span>来自哪张表</span></div>';
    fh += (def.fields || []).map(function (f) {
      return '<div class="sp-frow"><span>' + esc(f[0]) + '</span>' +
        '<span class="fc">' + esc(f[1]) + '</span><span>' + esc(f[2]) + '</span></div>';
    }).join('');
    $('#spFields').innerHTML = fh;
    /* 当前生效条件 */
    $('#spParams').innerHTML = specCond(key).map(function (kv) {
      return '<div class="k">' + esc(kv[0]) + '</div><div class="v">' + esc(kv[1]) + '</div>';
    }).join('');
    $('#spHint').textContent = key === 'contracts'
      ? '以上条件在数据库里就是筛选条件（WHERE）：只统计同时满足它们的协议。'
      : '以上条件在数据库里就是筛选条件（WHERE）：只统计同时满足它们的采购申请单。';
    /* 名词解释：WHERE 一律说明，其余按对象取 */
    var keys = (TERMS_BY_KEY[key] || []);
    var list = [TERMS.where].concat(keys.map(function (k) { return TERMS[k]; }).filter(Boolean));
    var seen = {}, tl = [];
    list.forEach(function (t) {
      if (seen[t[0]]) return;
      seen[t[0]] = 1;
      tl.push('<div class="sp-term"><b>' + esc(t[0]) + '</b>' + esc(t[1]) + '</div>');
    });
    $('#spTerms').innerHTML = tl.join('');
    pop.classList.add('on');
    pop.setAttribute('aria-hidden', 'false');
  }

  function closeSpec() {
    var pop = $('#specPop');
    pop.classList.remove('on');
    pop.setAttribute('aria-hidden', 'true');
  }

  function bindSpec() {
    document.addEventListener('click', function (e) {
      var b = e.target.closest ? e.target.closest('.spec-btn') : null;
      if (b) { e.preventDefault(); openSpec(b.dataset.spec); return; }
      if (e.target.closest && e.target.closest('#spClose')) { closeSpec(); return; }
      if (e.target.closest && e.target.closest('#specPop .sp-body')) return;
      if ($('#specPop').classList.contains('on')) closeSpec();
    });
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && $('#specPop').classList.contains('on')) closeSpec();
    });
  }

  /* ================================================ Geo 页：地图气泡 + Category 同比
     地图底图用腾讯地图 GL JS；Key 由部署方 TMAP_KEY 注入，未配置只显示数据。
     气泡 = 公司所在城市（同一城市合并），颜色 = 该城金额最大公司的区域色。 */
  var GEO_LABELS = { prpo: 'PRPO Spend', paid: 'PO Paid',
                     micro: 'Micro Purchase', inadequate: 'SS - Inadequate Competition' };
  function geoParams() {
    var p = qs({ metric: S.geo.metric });
    (S.geo.selected || []).forEach(function (v) { p.append('company', v); });
    return p;
  }
  function enterGeo() {
    $('#pgGeo').hidden = false;
    showChrome(false);
    if (!S.geo.loaded || S.geo.stale) loadGeo();
  }
  function leaveGeo() { $('#pgGeo').hidden = true; }
  function loadGeo() {
    var seq = ++S.geo.seq;
    S.geo.stale = false;
    saveView();
    return api('/api/board/geo', geoParams()).then(function (d) {
      if (seq !== S.geo.seq) return;
      S.geo.data = d; S.geo.loaded = true;
      renderGeo(d);
    }).catch(function (e) {
      toast('Failed to load map data: ' + e.message, 4000);
    });
  }
  /* Zebra BI 贴的是纯数值 + M/K，不带币种符号 */
  function geoNum(v, d) {
    if (v == null || isNaN(v)) return '—';
    var x = Math.abs(v) / S.rate;
    if (x >= 1e6) return nf(x / 1e6, d == null ? 1 : d) + ' M';
    if (x >= 1e3) return nf(x / 1e3, 0) + ' K';
    return nf(x, 0);
  }
  function geoSigned(v, d) {
    if (v == null || isNaN(v)) return '—';
    return (v < 0 ? '-' : (v > 0 ? '+' : '')) + geoNum(v, d);
  }
  /* Zebra 参考图的数值只有纯数字（统一百万单位），不写 M / 币种 */
  function zbNum(v) {
    if (v == null || isNaN(v)) return '';
    return nf(Math.abs(v) / S.rate / 1e6, 1);
  }
  function zbSigned(v) {
    if (v == null || isNaN(v)) return '';
    return (v < 0 ? '-' : (v > 0 ? '+' : '')) + zbNum(v);
  }
  function renderGeo(d) {
    $('#geoSub').textContent = '· ' + d.fy + ' vs ' + (d.pyFy || '—') +
      ' · bubble = company city · FY in progress, so YoY looks low';
    $('#geoMetric').value = d.metric;
    renderGeoCompanies(d);
    renderGeoTable(d);
    renderGeoMap(d);
  }
  function renderGeoCompanies(d) {
    /* 记住未筛选时的完整公司清单：别的筛选（类别等）把已勾选公司暂时筛掉时，
       仍要在列表里保留它，否则勾上之后取消不掉。 */
    if (!(S.geo.selected || []).length) S.geo.baseCompanies = (d.companies || []).slice();
    var rows = (d.companies || []).slice();
    var have = {};
    rows.forEach(function (r) { have[r.company] = 1; });
    (S.geo.selected || []).forEach(function (name) {
      if (have[name]) return;
      var b = (S.geo.baseCompanies || []).filter(function (x) { return x.company === name; })[0];
      rows.push({ company: name, abbr: b ? b.abbr : name, v: null, miss: true });
    });
    var q = (S.geo.q || '').trim().toLowerCase();
    rows = rows.filter(function (r) {
      return !q || (r.company || '').toLowerCase().indexOf(q) >= 0 ||
        (r.abbr || '').toLowerCase().indexOf(q) >= 0;
    });
    $('#geoCompCount').textContent = nf(rows.length, 0) + ' / ' + nf((d.companies || []).length, 0);
    $('#geoCompList').innerHTML = rows.length ? rows.map(function (r) {
      var on = (S.geo.selected || []).indexOf(r.company) >= 0;
      return '<label' + (r.miss ? ' class="miss"' : '') +
        '><input type="checkbox" data-company="' + esc(r.company) + '"' +
        (on ? ' checked' : '') + '><span title="' + esc(r.company) + ' · ' +
        esc(r.abbr || '') + '">' + esc(r.abbr || r.company) + '</span><i>' +
        (r.miss ? '—' : esc(geoNum(r.v, 1))) + '</i></label>';
    }).join('') : '<div class="geo-comp-none">No matching company</div>';
  }
  /* Zebra BI 1:1：PY 右对齐数字 / AC 深灰条数值贴尾 / ΔPY 中心零线左右红绿条 /
     ΔPY% 细线 + 黑心圆点。数值统一深色，不用彩色字。 */
  function zebraSide(kind, diff, ratio, txt, zero) {
    var bar = kind === 'pct'
      ? '<i class="zb-line" style="width:' + ratio.toFixed(1) + '%"></i>'
      : '<i class="zb-bar" style="width:' + ratio.toFixed(1) + '%"></i>';
    return '<div class="z-side ' + (diff >= 0 ? 'pos' : 'neg') + (zero ? ' zero' : '') + '">' +
      bar + (txt ? '<span class="zv">' + esc(txt) + '</span>' : '') + '</div>';
  }
  function zebra(v, pv, kind, max) {
    if (kind === 'py') {
      return '<td class="z-py">' + (pv ? esc(zbNum(pv)) : '') + '</td>';
    }
    if (kind === 'amount') {
      var w = Math.min(86, Math.abs(v) / (max || 1) * 86);
      return '<td><div class="z-amt"><i class="zb-gray" style="width:' +
        w.toFixed(1) + '%"></i><span class="zv">' + esc(zbNum(v)) +
        '</span></div></td>';
    }
    var diff = v - pv;
    var val = kind === 'pct' ? (pv ? diff / pv : 0) : diff;
    var cap = kind === 'pct' ? 34 : 42;
    var ratio = Math.min(cap, Math.abs(val) / (max || 1) * cap);
    var zero = kind === 'pct' && Math.abs(val) < 0.0005;
    /* 参考图：ΔPY% 为 0 时只留零线上的点，不写 +0.0% */
    var txt = kind === 'pct'
      ? (pv && !zero ? (diff >= 0 ? '+' : '-') + Math.abs(diff / pv * 100).toFixed(1) + '%' : '')
      : zbSigned(diff);
    return '<td><div class="z-br' + (kind === 'pct' ? ' z-pct' : '') + '">' +
      zebraSide(kind, diff, ratio, txt, zero) + '</div></td>';
  }
  function renderGeoTable(d) {
    /* 表格点击类别时保留整张表（未选中行淡化），否则行被筛掉就没法再点一次取消 */
    var picked = S.sel.category || [];
    var src = (picked.length && d.categoriesAll) ? d.categoriesAll : (d.categories || []);
    var cats = src.slice().sort(function (a, b) { return b.v - a.v; });
    var maxV = 0, maxD = 0, maxP = 0;
    cats.forEach(function (r) {
      maxV = Math.max(maxV, Math.abs(r.v));
      maxD = Math.max(maxD, Math.abs(r.v - r.pv));
      if (r.pv) maxP = Math.max(maxP, Math.abs((r.v - r.pv) / r.pv));
    });
    $('#geoYoyNote').textContent = (d.pyFy || 'PY') + ' → ' + d.fy +
      ' · unit M · green = increase, red = decrease · FY in progress';
    $('#geoTbl thead').innerHTML = '<tr><th>Category</th><th>PY</th><th>AC ↓</th>' +
      '<th>ΔPY</th><th>ΔPY%</th></tr>';
    $('#geoTbl tbody').innerHTML = cats.map(function (r, i) {
      var on = picked.indexOf(r.cat) >= 0;
      var dim = picked.length > 0 && !on;
      return '<tr class="z-row' + (on ? ' on' : '') + (dim ? ' dim' : '') +
        (i === 5 ? ' z-group' : '') +
        '" data-cat="' + esc(r.cat) + '" title="Click to filter this category">' +
        '<td title="' + esc(r.cat) + '">' + esc(r.cat) + '</td>' +
        zebra(r.v, r.pv, 'py', 1) +
        zebra(r.v, r.pv, 'amount', maxV) +
        zebra(r.v, r.pv, 'delta', maxD) +
        zebra(r.v, r.pv, 'pct', maxP) + '</tr>';
    }).join('');
  }
  /* 表格按参考图固定行高，不再拉伸铺满容器 */
  function fitGeoTbl() { /* 保留空函数：resize 监听仍调用，无需重算 */ }
  function ensureMapSDK(cb) {
    var key = window.TMAP_KEY || '';
    var msg = $('#geoMapMsg');
    if (!key || key.indexOf('请到') === 0 || key.indexOf('Please apply') === 0) {
      msg.textContent = '地图底图需要腾讯位置服务 Key。请在服务端设置 TMAP_KEY 环境变量后重启；' +
        '下方的公司列表与 Category 同比不受影响。';
      msg.hidden = false;
      return;
    }
    if (window.TMap) { msg.hidden = true; cb(); return; }
    if (window.__tmapQueue) { window.__tmapQueue.push(cb); return; }
    window.__tmapQueue = [cb];
    var s = document.createElement('script');
    s.src = 'https://map.qq.com/api/gljs?v=1.exp&key=' + encodeURIComponent(key);
    s.onload = function () {
      msg.hidden = true;
      var q = window.__tmapQueue.slice(); window.__tmapQueue = null;
      q.forEach(function (f) { f(); });
    };
    s.onerror = function () {
      msg.textContent = '腾讯地图 SDK 加载失败，请检查网络连通性与 Key 的域名白名单设置。';
      msg.hidden = false;
      window.__tmapQueue = null;
    };
    document.head.appendChild(s);
  }
  var GEO_CENTER = [113.5, 22.9], GEO_ZOOM = 5.6;   // 默认聚焦珠三角（门店最密）
  function initGeoMap() {
    S.geo.map = new window.TMap.Map('geoMap', {
      center: new window.TMap.LatLng(GEO_CENTER[1], GEO_CENTER[0]),
      zoom: GEO_ZOOM, pitch: 0
    });
    /* 记录初始视角，便于回归断言默认落在华南 */
    $('#geoMap').dataset.zoom = String(GEO_ZOOM);
    $('#geoMap').dataset.center = GEO_CENTER.join(',');
    window.__geoMap = S.geo.map;   /* 便于回归测试调整视角验证气泡随缩放变化 */
    S.geo.overlay = new window.TMap.MultiCircle({
      map: S.geo.map, styles: {}, geometries: []
    });
    S.geo.overlay.on('click', function (e) {
      var g = e.geometry;
      if (!g || !g.id) return;
      var name = g.id, i = S.geo.selected.indexOf(name);
      if (i >= 0) S.geo.selected.splice(i, 1);
      else S.geo.selected.push(name);
      loadGeo();
    });
    /* 气泡按屏幕像素定尺寸（参考图：各缩放级别下视觉大小基本一致、仅缓慢变小），
       缩放时按当前米/像素重算地理半径。 */
    S.geo.map.on('zoom', function () {
      if (S.geo.data) drawGeoBubbles(S.geo.data);
    });
  }
  function drawGeoBubbles(d) {
    var bubbles = (d.bubbles || []).filter(function (b) {
      return b.lat != null && b.lng != null;
    });
    var max = 0;
    bubbles.forEach(function (b) { max = Math.max(max, Math.abs(b.v)); });
    var zoom = S.geo.map.getZoom() || GEO_ZOOM;
    $('#geoMap').dataset.zoom = String(zoom);
    /* 按参考图：气泡在屏幕上像素大小基本恒定（每放大一级只缩 8%），
       所以先算目标像素半径，再按 Web Mercator 米/像素反算出地理半径。 */
    var shrink = Math.pow(0.92, zoom - GEO_ZOOM);
    var styles = {}, geoms = {}, legend = {};
    bubbles.forEach(function (b) {
      var c = b.color || '#3B5E96';
      var key = 'g_' + c.replace('#', '');
      if (!styles[key]) {
        var m = /^#?([0-9a-f]{6})$/i.exec(c || '');
        var rgb = m ? parseInt(m[1], 16) : 0x3B5E96;
        var fill = 'rgba(' + ((rgb >> 16) & 255) + ',' + ((rgb >> 8) & 255) +
          ',' + (rgb & 255) + ',.70)';
        styles[key] = new window.TMap.CircleStyle({
          color: fill, showBorder: true, borderColor: c, borderWidth: 2
        });
        legend[c] = b.region || '';
      }
      /* 屏幕半径 5~20px，再乘缩放衰减；mpp = 156543×cos(lat)/2^zoom */
      var mpp = 156543.03392 * Math.cos(b.lat * Math.PI / 180) / Math.pow(2, zoom);
      var pxR = (5 + Math.sqrt(Math.abs(b.v) / (max || 1)) * 15) * shrink;
      var radius = pxR * mpp;
      $('#geoMap').dataset.sampleRadius = String(radius);
      $('#geoMap').dataset.samplePx = String(pxR);
      (geoms[key] || (geoms[key] = [])).push({
        id: b.company, styleId: key,
        center: new window.TMap.LatLng(b.lat, b.lng), radius: radius
      });
    });
    S.geo.overlay.setStyles(styles);
    S.geo.overlay.setGeometries([].concat.apply([], Object.keys(geoms).map(function (k) {
      return geoms[k];
    })));
    $('#geoLegend').innerHTML = Object.keys(legend).map(function (c) {
      return '<span><i style="background:' + esc(c) + '"></i>' +
        esc(legend[c] || 'Region') + '</span>';
    }).join('');
  }
  function renderGeoMap(d) {
    ensureMapSDK(function () {
      try {
        if (!S.geo.map) initGeoMap();
        drawGeoBubbles(d);
      } catch (e) {
        console.error(e);
        $('#geoMapMsg').textContent = '地图渲染失败：' + e.message +
          '（下方公司列表与同比表仍可使用）';
        $('#geoMapMsg').hidden = false;
      }
    });
  }
  function bindGeo() {
    $('#geoMetric').addEventListener('change', function (e) {
      S.geo.metric = e.target.value;
      loadGeo();
    });
    $('#geoSearch').addEventListener('input', function (e) {
      S.geo.q = e.target.value;
      if (S.geo.data) renderGeoCompanies(S.geo.data);
      saveView();
    });
    $('#geoCompList').addEventListener('change', function (e) {
      var el = e.target;
      if (!el.dataset || !el.dataset.company) return;
      var name = el.dataset.company, i = S.geo.selected.indexOf(name);
      if (el.checked && i < 0) S.geo.selected.push(name);
      if (!el.checked && i >= 0) S.geo.selected.splice(i, 1);
      loadGeo();
    });
    /* Zebra 行点击 = 全页类别交叉筛选（再点取消），与其它视觉对象一致 */
    $('#geoTbl tbody').addEventListener('click', function (e) {
      var tr = e.target.closest('tr.z-row');
      if (!tr || !tr.dataset.cat) return;
      var list = (S.sel.category || []).slice();
      var i = list.indexOf(tr.dataset.cat);
      if (i >= 0) list.splice(i, 1); else list.push(tr.dataset.cat);
      setSel('category', list);
      renderFilters();
      loadAll();
      loadGeo();
    });
    window.addEventListener('resize', function () {
      if (S.page !== 'geo') return;
      if (S.geo.map) S.geo.map.resize();
      fitGeoTbl();
    });
  }

  /* ================================================ 页签 ↔ 页面切换
     三页：Overview（原看板）/ Detail（大表格）/ Framework（协议卡独立成页）。
     新页面：BoardPages.register(key, {label, enter, leave})。
     页签高度固定 50px、下沿齐顶栏，且只改 class → 切页不顶动顶栏控件。 */
  /* ---------------- 视图状态持久化（刷新后停在同一个层级） ----------------
     存的东西：页签、财年、币种、全局筛选、方法饼图下钻层、帕累托子类别层、
     类别明细已展开的类别、Detail 三级树展开态。
     不存高亮（S.hl 依赖「点击前那份数据」，刷新后已无从还原）与方法下钻缓存（重拉一次）。
     sessionStorage：同一标签页刷新/前进后退有效，新开标签页回到默认视图。 */
  var VIEW_KEY = 'board-view-v1';
  /* 刷新 / 关标签页前再存一次：饼图下钻、类目展开这些不经过 loadAll，
     靠 pagehide 兜底，才不至于「改了层级一刷新就丢」 */
  window.addEventListener('pagehide', function () { saveView(); });
  window.addEventListener('beforeunload', function () { saveView(); });
  function saveView() {
    try {
      sessionStorage.setItem(VIEW_KEY, JSON.stringify({
        fy: S.fy || '', cur: S.cur, page: S.page,
        sel: S.sel || {},
        methodDrill: S.methodDrill || '',
        subLevel: S.subLevel || '',
        catSub: Object.keys(S.catSub || {}),
        mMode: S.mMode,
        geoMetric: S.geo.metric,
        geoSelected: S.geo.selected || [],
        geoQ: S.geo.q || '',
        expR: S.detail.expR || {},
        expC: S.detail.expC || {},
        dFrom: S.detail.from || '',
        dTo: S.detail.to || ''
      }));
    } catch (e) { /* 隐私模式 / 配额满：存不了也不影响使用 */ }
  }
  function readView() {
    try {
      var v = JSON.parse(sessionStorage.getItem(VIEW_KEY) || 'null');
      return v && typeof v === 'object' ? v : null;
    } catch (e) { return null; }
  }
  function restoreView(v) {
    if (!v) return;
    if (v.sel && typeof v.sel === 'object') {
      Object.keys(v.sel).forEach(function (k) {
        var vals = v.sel[k];
        if (Object.prototype.toString.call(vals) === '[object Array]' && vals.length) S.sel[k] = vals;
      });
    }
    if (v.methodDrill) S.methodDrill = v.methodDrill;
    if (v.subLevel === 'sub') S.subLevel = 'sub';
    (v.catSub || []).forEach(function (k) { S.catSub[k] = 1; });
    if (v.mMode === 'pair') S.mMode = 'pair';
    if (GEO_LABELS[v.geoMetric]) S.geo.metric = v.geoMetric;
    if (Object.prototype.toString.call(v.geoSelected) === '[object Array]') {
      S.geo.selected = v.geoSelected.filter(function (x) { return typeof x === 'string'; });
    }
    if (typeof v.geoQ === 'string') S.geo.q = v.geoQ;
    if (v.cur === 'RM' || v.cur === 'RMB') S.cur = v.cur;
    if (typeof v.fy === 'string') S.fy = v.fy;
    if (v.expR && typeof v.expR === 'object') S.detail.expR = v.expR;
    if (v.expC && typeof v.expC === 'object') S.detail.expC = v.expC;
    if (typeof v.dFrom === 'string') S.detail.from = v.dFrom;
    if (typeof v.dTo === 'string') S.detail.to = v.dTo;
  }
  /* 顶栏 / 图内的分段控件要跟着还原后的状态点亮（否则按钮和实际视图对不上） */
  function syncControls() {
    if ($('#fySel')) $('#fySel').value = S.fy || '';
    var cs = $('#curSeg');
    if (cs) $$('button[data-cur]', cs).forEach(function (b) {
      b.classList.toggle('on', b.dataset.cur === S.cur);
    });
    var ms = $('#mSw');
    if (ms) $$('button[data-m]', ms).forEach(function (b) {
      b.classList.toggle('on', b.dataset.m === S.mMode);
    });
  }

  var PAGES = {
    overview:  { label: 'Overview',
                 enter: function () {
                   $('#pgOverview').hidden = false; showChrome(true);
                   if (S.overviewHiddenRender) {
                     S.overviewHiddenRender = false;
                     /* 双 rAF：等 hidden 取消后的布局真正生效再量宽重画 */
                     requestAnimationFrame(function () {
                       requestAnimationFrame(renderOverviewVisuals);
                     });
                   }
                 },
                 leave: function () { $('#pgOverview').hidden = true; } },
    detail:    { label: 'Detail',    enter: enterDetail,   leave: leaveDetail },
    framework: { label: 'Framework', enter: enterFramework, leave: leaveFramework },
    geo:       { label: 'Geo Spend', enter: enterGeo,      leave: leaveGeo }
  };
  function renderTabs() {
    var box = $('#pgTabs');
    if (!box) return;
    box.innerHTML = Object.keys(PAGES).map(function (k) {
      var p = PAGES[k], on = (k === S.page);
      return '<button type="button" class="pg-tab' + (on ? ' on' : '') + '" data-page="' +
        esc(k) + '"' + (on ? ' aria-current="page"' : '') + ' title="' +
        esc(p.title || p.label) + '">' +
        '<span class="pg-tab-top">' + esc(p.label) + '</span></button>';
    }).join('');
  }

  /* 当前页签写进 URL hash（#detail / #framework）：刷新、收藏、离线单文件都停在原页面 */
  function pageFromHash() {
    var h = (location.hash || '').replace(/^#\/?/, '');
    return PAGES[h] ? h : null;
  }
  function rememberPage(key) {
    try {
      if (window.history && history.replaceState) history.replaceState(null, '', '#' + key);
      else location.hash = key;
    } catch (e) { location.hash = key; }
  }
  function goPage(key, fromHash) {
    if (!PAGES[key] || key === S.page) return false;
    var old = PAGES[S.page];
    if (old && old.leave) old.leave();
    S.page = key;
    renderTabs();
    var next = PAGES[key];
    if (next && next.enter) next.enter();
    if (!fromHash) rememberPage(key);
    saveView();
    return true;
  }

  /* Overview 才显示固定区里的 KPI 行 + 全局筛选条；Detail/Framework 各有自己的表与局部筛选 */
  function showChrome(show) {
    var bar = $('.fbar'), kpis = $('#kpis');
    if (bar) bar.hidden = !show;
    if (kpis) kpis.hidden = !show;
  }
  function enterFramework() {
    $('#pgFramework').hidden = false;
    showChrome(false);
  }
  function leaveFramework() {
    $('#pgFramework').hidden = true;
    showChrome(true);
  }
  function enterDetail() {
    $('#pgDetail').hidden = false;
    showChrome(false);
    if (!S.detail.loaded || S.detail.stale) loadDetail();
  }
  function leaveDetail() {
    closeDtPop();
    $('#pgDetail').hidden = true;
    showChrome(true);
  }

  window.addEventListener('hashchange', function () {
    var p = pageFromHash();
    if (p && p !== S.page) goPage(p, true);
  });

  /* 给后续页面模块用：register 之后页签自动出现，go 可程序化切页 */
  window.BoardPages = {
    go: goPage,
    register: function (key, def) { PAGES[key] = def || {}; renderTabs(); },
    current: function () { return S.page; }
  };

  /* ================================================ Detail 大表格
     后端 /api/board/detail 分页取数，合计按筛选全集算；每列：
       · 点标题排序（数值默认降序、文本默认升序，再点反向）
       · 点漏斗开浮层：文本列 = 搜索 + 取值勾选，数值列 = 最小/最大区间
       · Clear filters 一次清掉所有列条件 */
  /* 列宽按实测内容定（1600 视口 ≈1472px 表宽）：
       短列只要放下表头 + 漏斗 + 一个分组行，余量全给 Supplier / Note；
       Company / Category 再压一档（分组行仍放得下），多出来的宽度都给说明；
       金额两列要容得下表头「PRPO Amount (RMB)」，否则表头被省略号截掉。 */
  var DT_COLS = [
    { k: 'region',   t: 'Region',              w: '6.25%' },
    { k: 'company',  t: 'Company',             w: '8.43%' },
    { k: 'category', t: 'Category',            w: '8.84%' },
    { k: 'supplier', t: 'Supplier',            w: '18.7%' },
    { k: 'method',   t: 'Procurement Method',  w: '10.94%' },
    /* 豁免单一来源的理由 / 单一来源采购理由 不会同时出现，合并成一列 */
    { k: 'reason',   t: 'Single Source Reason', w: '11.08%' },
    /* 说明：一行显示 + 省略号（全文在悬停浮窗里），夹在理由与金额之间 */
    { k: 'note',     t: 'Note',                w: '16.59%', long: 1 },
    { k: 'amount',   t: 'PRPO Amount',         w: '10.81%', num: 1 },
    { k: 'paid',     t: 'PO Paid',             w: '8.36%', num: 1 }
  ];
  var DT_TEXT = DT_COLS.filter(function (c) { return !c.num; });

  function dtCol(key) {
    for (var i = 0; i < DT_COLS.length; i++) if (DT_COLS[i].k === key) return DT_COLS[i];
    return null;
  }
  function dtMoney(v) {
    if (v == null || isNaN(v)) return '—';
    return sym() + nf(v, 2);
  }
  function dtActive(k) {
    var d = S.detail;
    return !!(d.q[k] || (d.f[k] && d.f[k].length) || d.min[k] !== undefined || d.max[k] !== undefined);
  }
  function dtParams(extra) {
    var p = qs(extra), d = S.detail;
    p.set('sort', d.sort); p.set('dir', d.dir);
    if (d.from) p.set('dateFrom', d.from);
    if (d.to) p.set('dateTo', d.to);
    Object.keys(d.q).forEach(function (k) { if (d.q[k]) p.set('q_' + k, d.q[k]); });
    Object.keys(d.f).forEach(function (k) {
      if (d.f[k] && d.f[k].length) p.set('f_' + k, JSON.stringify(d.f[k]));
    });
    Object.keys(d.min).forEach(function (k) { if (d.min[k] !== '' && d.min[k] != null) p.set('min_' + k, d.min[k]); });
    Object.keys(d.max).forEach(function (k) { if (d.max[k] !== '' && d.max[k] != null) p.set('max_' + k, d.max[k]); });
    return p;
  }
  function loadDetail() {
    /* 请求在飞时又来一次（连续改日期/筛选）：只记一个待办，等当前回来再补一次，
       不能直接丢弃，否则最后一次改动不生效。 */
    if (S.detail.pending) { S.detail.queued = true; return; }
    S.detail.pending = true;
    var seq = ++S.detail.seq;
    api('/api/board/detail', dtParams()).then(function (d) {
      if (seq !== S.detail.seq) return;
      S.detail.loaded = true; S.detail.stale = false; S.detail.data = d;
      renderDetail(d);
    }).catch(function (e) {
      toast('Failed to load detail: ' + e.message, 4000);
    }).then(function () {
      S.detail.pending = false;
      if (S.detail.queued) { S.detail.queued = false; loadDetail(); }
    });
  }
  function renderDtHead() {
    var d = S.detail;
    $('#dtTbl thead').innerHTML = '<tr>' + DT_COLS.map(function (c) {
      var label = c.t + (c.num ? ' (' + S.cur + ')' : '');
      var arrow = d.sort === c.k ? (d.dir === 'asc' ? '↑' : '↓') : '';
      return '<th style="width:' + c.w + '"><div class="dt-th">' +
        '<span class="dt-sort" data-sort="' + c.k + '" title="Sort by ' + esc(c.t) + '">' +
        esc(label) + '<i class="dt-arrow">' + arrow + '</i></span>' +
        '<button type="button" class="dt-funnel' + (dtActive(c.k) ? ' on' : '') +
        '" data-col="' + c.k + '" title="Search / filter ' + esc(c.t) + '">' +
        '<svg viewBox="0 0 24 24" width="11" height="11" fill="none" stroke="currentColor"' +
        ' stroke-width="2.4" stroke-linejoin="round" aria-hidden="true">' +
        '<path d="M3 5h18l-7.2 8.4V20l-3.6-2v-4.6z"/></svg></button>' +
        '</div></th>';
    }).join('') + '</tr>';
  }
  function dtCoKey(region, company) { return (region || '') + '\u0001' + (company || ''); }
  /* -------- 单元格悬停浮窗 --------
     只对被裁掉的内容浮窗：说明列永远单行，一定有浮窗；其余列按 scrollWidth 判断。
     定位贴单元格下沿、越界翻到上方并夹在视口内；滚动 / 移出表格 / 重渲染都收起。 */
  var dtTipEl = null, dtTipTd = null, dtTipTimer = null;
  function hideDtTip() {
    clearTimeout(dtTipTimer); dtTipTimer = null; dtTipTd = null;
    if (dtTipEl) { dtTipEl.hidden = true; dtTipEl.textContent = ''; }
  }
  function placeDtTip(td) {
    var txt = td.getAttribute('data-tip') || '';
    if (!dtTipEl || !txt ||
        (!td.classList.contains('dt-note') && td.scrollWidth <= td.clientWidth + 1)) {
      hideDtTip(); return;
    }
    dtTipEl.textContent = txt;
    dtTipEl.hidden = false;
    var r = td.getBoundingClientRect(), pad = 8;
    var w = dtTipEl.offsetWidth, h = dtTipEl.offsetHeight;
    var vw = window.innerWidth, vh = window.innerHeight;
    var x = Math.min(Math.max(pad, r.left), vw - w - pad);
    var y = r.bottom + 6;
    if (y + h > vh - pad) y = r.top - h - 6;
    dtTipEl.style.left = Math.round(x) + 'px';
    dtTipEl.style.top = Math.round(Math.max(pad, y)) + 'px';
  }
  function renderDetail(d) {
    hideDtTip();
    renderDtHead();
    S.detail.data = d;
    /* 日期切片器回填 + 用数据整体范围限制可选区间 */
    var dF = $('#dtFrom'), dT = $('#dtTo'),
      rg = S.range || (S.data && S.data.range) || {};
    if (dF && dT) {
      dF.value = S.detail.from || '';
      dT.value = S.detail.to || '';
      if (rg.from) { dF.min = rg.from; dT.min = rg.from; }
      if (rg.to) { dF.max = rg.to; dT.max = rg.to; }
    }
    var max = { amount: 0, paid: 0 }, reg = {}, co = {}, tree = [];
    (d.rows || []).forEach(function (r) {
      ['amount', 'paid'].forEach(function (k) {
        if (r[k] > max[k]) max[k] = r[k];
      });
    });
    /* 分组小计：后端按筛选全集算；老快照没带就按全集兜底 */
    (d.regionTotals || []).forEach(function (r) { reg[r.region || ''] = r; });
    (d.companyTotals || []).forEach(function (r) { co[dtCoKey(r.region, r.company)] = r; });
    if (!(d.regionTotals || []).length || !(d.companyTotals || []).length) {
      (d.rows || []).forEach(function (r) {
        var rk = r.region || '', ck = dtCoKey(rk, r.company);
        var g = reg[rk] || (reg[rk] = { region: rk, n: 0, amount: 0, paid: 0 });
        var h = co[ck] || (co[ck] = { region: rk, company: r.company || '', n: 0, amount: 0, paid: 0 });
        g.n++; g.amount += r.amount || 0; g.paid += r.paid || 0;
        h.n++; h.amount += r.amount || 0; h.paid += r.paid || 0;
      });
    }
    /* 建区域→公司树；区域按采购额降序、公司按采购额降序（小计来自筛选全集），
       明细行在组内保持后端排序。 */
    var rmap = {};
    (d.rows || []).forEach(function (r) {
      var rk = r.region || '', ck = r.company || '';
      var g = rmap[rk] || (rmap[rk] = { region: rk, cos: {}, order: [] });
      if (!g.cos[ck]) { g.cos[ck] = []; g.order.push(ck); }
      g.cos[ck].push(r);
    });
    Object.keys(rmap).forEach(function (rk) { tree.push(rmap[rk]); });
    tree.sort(function (a, b) {
      return (reg[b.region] ? reg[b.region].amount : 0) - (reg[a.region] ? reg[a.region].amount : 0);
    });
    tree.forEach(function (g) {
      g.order.sort(function (x, y) {
        var cx = co[dtCoKey(g.region, x)], cy = co[dtCoKey(g.region, y)];
        return (cy ? cy.amount : 0) - (cx ? cx.amount : 0);
      });
    });
    function numCell(v, key) {
      var w = max[key] > 0 && v > 0 ? Math.min(100, Math.max(3, Math.round(v / max[key] * 100))) : 0;
      return '<td class="num"><i class="dt-bar' + (key === 'paid' ? ' paid' : '') +
        '" style="width:' + w + '%"></i><span>' + dtMoney(v) + '</span></td>';
    }
    var body = [];
    tree.forEach(function (g) {
      var rk = g.region, rg = reg[rk] || { n: 0, amount: 0, paid: 0 };
      var rOpen = !!S.detail.expR[rk];
      body.push('<tr class="dt-group" data-region="' + esc(rk) + '">' +
        '<td data-tip="' + esc(rk) + '"><button type="button" class="dt-toggle" data-toggle-region="' + esc(rk) +
        '" title="' + (rOpen ? 'Collapse' : 'Expand') + ' ' + esc(rk) + '">' +
        (rOpen ? '−' : '+') + '</button>' + esc(rk || '(blank)') +
        '<i class="dt-gn">' + nf(rg.n, 0) + '</i></td>' +
        '<td colspan="6" class="dt-gmeta">region subtotal</td>' +
        numCell(rg.amount, 'amount') + numCell(rg.paid, 'paid') + '</tr>');
      if (!rOpen) return;
      g.order.forEach(function (ckName) {
        var key = dtCoKey(rk, ckName), cg = co[key] || { n: 0, amount: 0, paid: 0 };
        var cOpen = !!S.detail.expC[key];
        body.push('<tr class="dt-company" data-region="' + esc(rk) + '" data-company="' + esc(ckName) + '">' +
          '<td class="dt-tree">└</td><td data-tip="' + esc(ckName) + '"><button type="button" class="dt-toggle" data-toggle-company="' +
          esc(key) + '" title="' + (cOpen ? 'Collapse' : 'Expand') + ' ' + esc(ckName) + '">' +
          (cOpen ? '−' : '+') + '</button>' + esc(ckName || '(blank)') +
          '<i class="dt-gn">' + nf(cg.n, 0) + '</i></td>' +
          '<td colspan="5" class="dt-gmeta">company subtotal</td>' +
          numCell(cg.amount, 'amount') + numCell(cg.paid, 'paid') + '</tr>');
        if (!cOpen) return;
        g.cos[ckName].forEach(function (r) {
          body.push('<tr class="dt-child">' + DT_COLS.map(function (c, i) {
            var v = r[c.k];
            if (c.num) return numCell(v, c.k);
            if (i === 0) return '<td class="dt-tree">└</td>';
            var tip = v == null ? '' : String(v);
            /* 文本列把原值放进 data-tip，悬停时由浮窗给全文（说明保留换行、
               供应商给中英文全称，宽度不够时不再靠浏览器原生 title） */
            if (c.long) {
              return '<td class="dt-note" data-tip="' + esc(tip) + '">' + esc(v || '—') + '</td>';
            }
            var disp = c.k === 'supplier' ? supEn(v) : (v || '—');
            return '<td data-tip="' + esc(tip) + '">' + esc(disp) + '</td>';
          }).join('') + '</tr>');
        });
      });
    });
    $('#dtTbl tbody').innerHTML = body.join('') || '<tr><td colspan="' + DT_COLS.length +
      '" class="dt-empty">No rows match the current filters</td></tr>';
    $('#dtTbl tfoot').innerHTML = '<tr>' + DT_COLS.map(function (c, i) {
      if (c.num) return '<td class="num">' + dtMoney(d.sums[c.k]) + '</td>';
      return '<td>' + (i === 0 ? 'Total' : '') + '</td>';
    }).join('') + '</tr>';
    $('#dtMeta').textContent = nf(d.total, 0) + ' rows · ' +
      ((S.detail.from || S.detail.to)
        ? ((S.detail.from || '…') + ' ~ ' + (S.detail.to || '…'))
        : d.fy) + ' · ' + d.cur;
  }

  /* -------- 表头浮层 -------- */
  function closeDtPop() {
    var pop = $('#dtPop');
    if (!pop) return;
    pop.hidden = true; pop.innerHTML = '';
    S.detail.pop = null; S.detail.tmp = null;
  }
  function openDtPop(key, btn) {
    closeDtPop();
    var c = dtCol(key);
    if (!c) return;
    var d = S.detail, pop = $('#dtPop');
    d.pop = key;
    if (c.num) {
      d.tmp = { min: d.min[key] == null ? '' : d.min[key], max: d.max[key] == null ? '' : d.max[key] };
      pop.innerHTML = '<div class="dt-pt">' + esc(c.t) + ' · range</div>' +
        '<div class="dt-range"><span>Min</span><input type="number" data-f="min" value="' + esc(d.tmp.min) + '">' +
        '<span>Max</span><input type="number" data-f="max" value="' + esc(d.tmp.max) + '"></div>' +
        '<div class="dt-acts" style="margin-top:9px">' +
        '<button type="button" class="btn ghost" data-act="clear">Clear</button>' +
        '<button type="button" class="btn" data-act="apply">Apply</button></div>';
    } else if (c.long) {
      /* 长文本列（说明）：4 万多种取值，勾选没有意义，只给全文搜索 */
      d.tmp = { search: d.q[key] || '' };
      pop.innerHTML = '<div class="dt-pt">' + esc(c.t) + ' · search</div>' +
        '<input type="search" data-f="search" placeholder="Search within note…" value="' + esc(d.tmp.search) + '">' +
        '<div class="dt-none" style="margin:7px 0 0">Matches the whole PRPO note text (case-insensitive, contains).</div>' +
        '<div class="dt-acts" style="margin-top:9px">' +
        '<button type="button" class="btn ghost" data-act="clear">Clear</button>' +
        '<button type="button" class="btn" data-act="apply">Apply</button></div>';
    } else {
      d.tmp = { search: d.q[key] || '', sel: (d.f[key] || []).slice(), list: [] };
      pop.innerHTML = '<div class="dt-pt">' + esc(c.t) + ' · filter</div>' +
        '<input type="search" data-f="search" placeholder="Search values…" value="' + esc(d.tmp.search) + '">' +
        '<div class="dt-list"><div class="dt-none">Loading…</div></div>' +
        '<div class="dt-acts">' +
        '<button type="button" class="btn ghost sm" data-act="all">All</button>' +
        '<button type="button" class="btn ghost" data-act="clear">Clear</button>' +
        '<button type="button" class="btn" data-act="apply">Apply</button></div>';
      fetchDtValues(key, d.tmp.search);
    }
    pop.hidden = false;
    var r = btn.getBoundingClientRect();
    pop.style.left = Math.max(8, Math.min(r.left - 8, innerWidth - 278)) + 'px';
    pop.style.top = Math.max(8, Math.min(r.bottom + 4, innerHeight - 330)) + 'px';
    var inp = pop.querySelector('input');
    if (inp) inp.focus();
  }
  function fetchDtValues(key, search) {
    var c0 = dtCol(key);
    if (!key || !c0 || c0.long) return;      // 长文本列没有取值列表
    var p = dtParams();
    p.delete('q_' + key); p.delete('f_' + key);
    p.delete('page'); p.delete('size'); p.delete('sort'); p.delete('dir');
    if (search) p.set('q', search);
    p.set('col', key);
    api('/api/board/detail/values', p).then(function (list) {
      if (S.detail.pop !== key) return;
      var pop = $('#dtPop'), box = pop.querySelector('.dt-list');
      if (!box) return;
      var sel = S.detail.tmp.sel;
      S.detail.tmp.list = (list || []).map(function (o) { return String(o.v); });
      box.innerHTML = (list || []).length ? list.map(function (o) {
        return '<label><input type="checkbox" value="' + esc(o.v) + '"' +
          (sel.indexOf(String(o.v)) >= 0 ? ' checked' : '') + '><span>' + esc(o.v || '(blank)') +
          '</span><i>' + nf(o.n, 0) + '</i></label>';
      }).join('') : '<div class="dt-none">No matching value</div>';
    }).catch(function () {
      var box = $('#dtPop .dt-list');
      if (box) box.innerHTML = '<div class="dt-none">Failed to load values</div>';
    });
  }
  function applyDtPop() {
    var d = S.detail, key = d.pop, pop = $('#dtPop'), c = dtCol(key);
    if (!key || !c || !pop) return;
    if (c.num) {
      var mn = pop.querySelector('[data-f=min]').value, mx = pop.querySelector('[data-f=max]').value;
      if (mn === '') delete d.min[key]; else d.min[key] = mn;
      if (mx === '') delete d.max[key]; else d.max[key] = mx;
    } else {
      var q = pop.querySelector('[data-f=search]').value.trim();
      var vals = [].slice.call(pop.querySelectorAll('.dt-list input:checked')).map(function (i) { return i.value; });
      if (q) d.q[key] = q; else delete d.q[key];
      if (vals.length) d.f[key] = vals; else delete d.f[key];
    }
    d.page = 1;
    closeDtPop();
    renderDtHead();
    loadDetail();
  }
  function clearDtCol(key) {
    var d = S.detail;
    delete d.q[key]; delete d.f[key]; delete d.min[key]; delete d.max[key];
    d.page = 1; closeDtPop(); renderDtHead(); loadDetail();
  }
  function bindDetail() {
    var tbl = $('#dtTbl'), pop = $('#dtPop');
    if (!tbl || !pop) return;
    dtTipEl = $('#dtTip');
    /* 悬停浮窗：同一格只在首次进入时启动，移动鼠标不重复定位 */
    tbl.addEventListener('mouseover', function (e) {
      /* 折叠按钮自己有 title，别和浮窗打架 */
      if (e.target.closest('.dt-toggle')) { hideDtTip(); return; }
      var td = e.target.closest('td[data-tip]');
      if (!td) { hideDtTip(); return; }
      if (td === dtTipTd) return;
      dtTipTd = td;
      clearTimeout(dtTipTimer);
      dtTipTimer = setTimeout(function () { placeDtTip(td); }, 80);
    });
    tbl.addEventListener('mouseleave', hideDtTip);
    /* 表格横竖滚动、页面滚动都会让浮窗位置失效，直接收起 */
    document.addEventListener('scroll', hideDtTip, true);
    tbl.addEventListener('click', function (e) {
      var c = e.target.closest('[data-toggle-company]');
      if (c) {
        var key = c.dataset.toggleCompany;
        S.detail.expC[key] = !S.detail.expC[key];
        if (S.detail.data) renderDetail(S.detail.data);
        saveView();
        return;
      }
      var t = e.target.closest('[data-toggle-region]');
      if (t) {
        var rk = t.dataset.toggleRegion;
        S.detail.expR[rk] = !S.detail.expR[rk];
        if (S.detail.data) renderDetail(S.detail.data);
        saveView();
        return;
      }
      var s = e.target.closest('[data-sort]');
      if (s) {
        var k = s.dataset.sort, d = S.detail;
        if (d.sort === k) d.dir = d.dir === 'asc' ? 'desc' : 'asc';
        else { d.sort = k; d.dir = dtCol(k) && dtCol(k).num ? 'desc' : 'asc'; }
        d.page = 1; loadDetail(); return;
      }
      var f = e.target.closest('.dt-funnel');
      if (f) openDtPop(f.dataset.col, f);
    });
    ['dtFrom', 'dtTo'].forEach(function (id) {
      var el = $('#' + id);
      if (!el) return;
      el.addEventListener('change', function () {
        S.detail.from = ($('#dtFrom').value || '').trim();
        S.detail.to = ($('#dtTo').value || '').trim();
        S.detail.page = 1;
        /* 设了日期范围就把财年置空：日期范围自己就是范围，两者不再叠加。
           财年一变，全局（KPI/图表）也要回落到「未指定 → 最新财年」。 */
        var cleared = false;
        if ((S.detail.from || S.detail.to) && S.fy) {
          S.fy = ''; cleared = true;
          if ($('#fySel')) $('#fySel').value = '';
        }
        saveView();
        if (cleared) loadAll(); else loadDetail();
      });
    });
    $('#dtReset').addEventListener('click', function () {
      var d = S.detail;
      d.q = {}; d.f = {}; d.min = {}; d.max = {}; d.page = 1;
      d.from = ''; d.to = '';
      if ($('#dtFrom')) $('#dtFrom').value = '';
      if ($('#dtTo')) $('#dtTo').value = '';
      /* 日期清空后财年不再有「空白」的理由：回到最新财年 */
      var back = false;
      if (!S.fy && S.latestFy) {
        S.fy = S.latestFy; back = true;
        if ($('#fySel')) $('#fySel').value = S.fy;
      }
      saveView();
      closeDtPop(); renderDtHead();
      if (back) loadAll(); else loadDetail();
    });
    pop.addEventListener('click', function (e) {
      var a = e.target.closest('[data-act]');
      if (!a) return;
      if (a.dataset.act === 'apply') applyDtPop();
      else if (a.dataset.act === 'clear') clearDtCol(S.detail.pop);
      else if (a.dataset.act === 'all') {
        var list = (S.detail.tmp && S.detail.tmp.list) || [];
        $$('.dt-list input', pop).forEach(function (i) { i.checked = true; });
        if (S.detail.tmp) S.detail.tmp.sel = list.slice();
      }
    });
    var timer = null;
    pop.addEventListener('input', function (e) {
      var f = e.target.dataset.f;
      if (f === 'min' || f === 'max') { S.detail.tmp[f] = e.target.value; return; }
      if (f !== 'search') return;
      S.detail.tmp.search = e.target.value;
      clearTimeout(timer);
      var cur = S.detail.pop, c2 = dtCol(cur);
      /* 长文本列只有搜索、没有取值列表，不用去拉候选 */
      if (!cur || !c2 || c2.long) return;
      timer = setTimeout(function () {
        if (S.detail.pop !== cur) return;   // 期间已 Apply/关闭，丢弃这次请求
        fetchDtValues(cur, e.target.value);
      }, 220);
    });
    document.addEventListener('click', function (e) {
      if (!pop.hidden && !e.target.closest('.dt-pop') && !e.target.closest('.dt-funnel')) closeDtPop();
    });
  }

  /* ================================================ 启动 */
  function init() {
    /* 服务端已给最新财年：先落一个默认值，首个 /data /top 就能带 fy（命中预热缓存）；
       随后 restoreView 若还原了具体财年会覆盖它 */
    if (!S.fy && window.__FY_DEFAULT) S.fy = window.__FY_DEFAULT;
    /* 先还原层级再发首个请求：筛选 / 下钻本来就要进 query，晚一步就是白跑一次全量 */
    restoreView(readView());
    renderTabs();
    bind();
    bindDetail();
    bindSpec();
    bindGeo();
    /* 首帧就落在 hash 指定的页：旧逻辑要等 data 回来才切，刷新时会先闪一下 Overview。
       这里只切可见性与页签，加载仍由下面的 loadAll / enterDetail 负责。 */
    var bootPage = pageFromHash();
    if (bootPage && bootPage !== S.page) {
      S.page = bootPage;
      renderTabs();
      var bootEnter = PAGES[bootPage];
      if (bootEnter && bootEnter.enter) bootEnter.enter();
      $('#pgOverview').hidden = true;
      /* 交给 hidden 属性接管，撤掉 <head> 的首帧防闪标记 */
      document.documentElement.classList.remove('boot-other');
    }
    /* options 与 data 互不依赖：data 不带 fy 时后端取最新财年（与 options.latestFy
       同一个口径），所以两个请求可以并行发，首屏少一个 300ms 的往返。 */
    Promise.all([
      loadOptions().catch(function (e) { console.error(e); }),
      loadAll()
    ]).then(function () {
      syncControls();
      /* 首屏渲染完再切页：Overview 已经在可见状态下量过尺寸，切过去不会出现 0 宽度图表 */
      var p = pageFromHash();
      if (p && p !== S.page) goPage(p, true);
    }).catch(function (e) {
      toast('Initialization failed: ' + e.message, 5000);
      console.error(e);
    });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
