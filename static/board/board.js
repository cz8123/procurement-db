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
  /* Card 视觉对象的顶部色条：主题里所有卡片同一个 ColorId，故不按指标区分 */
  var KPI_COLORS = ['#3B5E96'];
  var FDEFS = [
    { k: 'brand', name: 'Brand' },
    { k: 'category', name: 'Category' },
    { k: 'expense', name: 'Expense Type' },
    { k: 'level', name: 'Supplier Category' },
    { k: 'region', name: 'Region' }
  ];
  var COLS = [
    { k: 'date', t: 'Date', w: 82 },
    { k: 'no', t: 'PR No.' },
    { k: 'company', t: 'Company' },
    { k: 'region', t: 'Region', w: 66 },
    { k: 'category', t: 'Category' },
    { k: 'expense', t: 'Expense Type', w: 74 },
    { k: 'supplier', t: 'Supplier' },
    { k: 'amount', t: 'Amount', num: 1 },
    { k: 'days', t: 'Days', num: 1, w: 78 }
  ];

  var S = {
    fy: null, cur: 'RMB', rate: 1,
    sel: {},
    options: {},
    data: null,
    topLocal: { category: '', method: '' },
    mMode: 'region',          // 月度图：region=区域堆叠 / pair=Total vs Framework
    // 框架协议统计（独立数据源 vw_fa_analysis，不吃 PRPO 全局筛选）
    fc: { page: 1, size: 50, sort: 'start', dir: 'desc',
          q: '', sup: '', type: '', df: '', dt: '', status: '' },
    drawer: { page: 1, size: 50, q: '', filters: null },
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
  function qs(extra) {
    var p = new URLSearchParams();
    if (S.fy) p.set('fy', S.fy);
    p.set('cur', S.cur);
    Object.keys(S.sel).forEach(function (k) {
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
    if (Math.abs(x) >= 1e9) return nf(x / 1e9, 1) + 'B';
    if (Math.abs(x) >= 1e6) {
      var m = x / 1e6; return nf(m, Math.abs(m % 1) < 0.05 ? 0 : 1) + 'M';
    }
    if (Math.abs(x) >= 1e3) {
      var k = x / 1e3; return nf(k, Math.abs(k % 1) < 0.05 ? 0 : 1) + 'K';
    }
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
  function chartW(id, fallback) {
    var el = $('#' + id);
    return Math.max(320, (el ? el.clientWidth : 0) || fallback);
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

  /** 差额带正负号：-¥627.7 M / +12 PRs */
  function kpiSigned(k, x) {
    var sgn = x < 0 ? '-' : (x > 0 ? '+' : '');
    return sgn + kpiVal(k, Math.abs(x));
  }

  function renderKpis() {
    var d = S.data, box = $('#kpis');
    var fy = d.fy || '';
    var pFy = (d.params && d.params.prevFy) || 'prev FY';
    box.innerHTML = (d.kpi || []).map(function (k) {
      var hasPrev = k.pv != null && k.v != null;
      var diff = hasPrev ? (k.v - k.pv) : null;
      var dir = diff == null ? '' : (diff < 0 ? 'down' : (diff > 0 ? 'up' : 'flat'));

      /* 两枚胶囊：左侧差额、右侧百分比（同参考样式），没上期就不显示 */
      var pills = '';
      if (hasPrev) {
        pills = '<span class="k-pill ' + dir + '">' + esc(kpiSigned(k, diff)) + '</span>' +
          '<span class="k-pill ' + dir + '">' +
          (k.yoy == null ? '—'
            : (k.yoy < 0 ? '-' : '+') + Math.abs(k.yoy * 100).toFixed(1) + '%') +
          '</span>';
      }
      var sub = k.sub ? '<span class="k-sub">· ' + esc(k.sub) + '</span>' : '';
      var prev = hasPrev
        ? '<div class="k-prev">vs ' + esc(pFy) + ' ' + esc(kpiVal(k, k.pv)) + sub + '</div>'
        : (sub ? '<div class="k-prev">' + sub + '</div>' : '');

      return '<div class="kpi" data-k="' + k.k + '" style="--c:' +
        KPI_COLORS[0] + '">' +
        '<button class="spec-btn kpi-spec" data-spec="kpi_' + k.k + '" ' +
        'title="How ' + esc(k.label) + ' is calculated">?</button>' +
        '<div class="k-top">' +
        '<span class="k-ico"><svg viewBox="0 0 24 24" aria-hidden="true">' +
        (KPI_ICONS[k.k] || KPI_ICONS.amount) + '</svg></span>' +
        '<span class="k-label">' + esc(k.label) + '</span>' +
        '<span class="k-fy">(' + esc(fy) + ')</span></div>' +
        '<div class="k-val">' + kpiVal(k, k.v) + '</div>' +
        (pills ? '<div class="k-bot">' + pills + '</div>' : '') +
        prev +
        '</div>';
    }).join('');

    var r = d.range || {};
    $('#hdSub').textContent = 'Procurement spend analysis · ' + (d.fy || '') +
      ' · ' + (r.from || '—') + ' ~ ' + (r.to || '—') +
      ' · ' + nf(d.status.txn, 0) + ' PRs / ' + nf(d.status.suppliers, 0) + ' suppliers';
  }

  /* ================================================ 筛选 */
  function renderFilters() {
    var bar = $('#fbar');
    bar.innerHTML = FDEFS.map(function (f) {
      var opts = (S.options[f.k] || []);
      var sel = S.sel[f.k] || [];
      var txt = sel.length === 0 ? 'All'
        : sel.length === 1 ? sel[0] : sel.length + ' selected';
      var body = opts.map(function (o) {
        return '<label><input type="checkbox" value="' + esc(o.v) + '"' +
          (sel.indexOf(o.v) >= 0 ? ' checked' : '') + '><span>' + esc(o.v) +
          '</span><span class="cnt">' + nf(o.n, 0) + '</span></label>';
      }).join('');
      return '<div class="fdrop' + (sel.length ? ' has' : '') + '" data-k="' + f.k + '">' +
        '<div class="fd-row">' +
        '<button type="button" class="fd-btn"><span class="fd-name">' + f.name + '</span>' +
        '<span class="fd-val">' + esc(txt) + '</span><span class="fd-caret">▾</span></button>' +
        '<button class="spec-btn fdrop-spec" data-spec="filter_' + f.k + '" ' +
        'title="Where ' + esc(f.name) + ' values come from">?</button>' +
        '</div>' +
        '<div class="fpop"><div class="fpop-h"><span>' + f.name + '</span>' +
        '<span><a data-act="none">Clear</a></span></div>' +
        '<div class="fpop-b">' + (body || '<div style="padding:8px 11px;color:#8695A8">No options</div>') +
        '</div></div></div>';
    }).join('');
    renderChips();
  }

  /** 只刷新按钮文字与 chips，避免勾选后整个下拉被重建（面板会闪一下关闭） */
  function updateFilterLabels() {
    FDEFS.forEach(function (f) {
      var drop = $('.fdrop[data-k="' + f.k + '"]');
      if (!drop) return;
      var sel = S.sel[f.k] || [];
      var txt = sel.length === 0 ? 'All' : (sel.length === 1 ? sel[0] : sel.length + ' selected');
      var el = $('.fd-val', drop);
      if (el) el.textContent = txt;
      drop.classList.toggle('has', sel.length > 0);
    });
    renderChips();
  }

  function renderChips() {
    var box = $('#chips'), out = [];
    FDEFS.forEach(function (f) {
      (S.sel[f.k] || []).forEach(function (v) {
        out.push('<span class="chip"><b>' + f.name + '</b>' + esc(v) +
          '<button data-k="' + f.k + '" data-v="' + esc(v) + '" title="Remove">×</button></span>');
      });
    });
    box.innerHTML = out.join('');
  }

  function setSel(k, vals) {
    if (!vals.length) delete S.sel[k]; else S.sel[k] = vals;
    S.fc.page = 1;
  }

  /* ================================================ Chart 1 月度堆叠 */
  /* ---------------------------------------------------------------- 月度图
     两种图示，切换保留：
       region = 按区域堆叠 + 上期虚线（原样式，点柱按区域交叉筛选）
       pair   = 每月 Total / Framework Contract 两根柱 + 柱顶金额胶囊（参考样式，
                点柱下钻该月明细）
     ------------------------------------------------------------------ */
  var M_TOT = '#1E2F4B';      // Total：主题藏青
  var M_FC  = '#E66C37';      // Framework Contract：对比橙

  function mTitle(t) { var h = $('#mTitle'); if (h) h.textContent = t; }

  /* 柱顶金额胶囊（深藏青底白字，宽按字数估） */
  function mCap(cx, bottomY, text) {
    var w = text.length * 5.0 + 11, h = 15;
    var x = cx - w / 2, y = bottomY - 4 - h;
    return '<rect x="' + x.toFixed(1) + '" y="' + y.toFixed(1) + '" width="' + w.toFixed(1) +
      '" height="' + h + '" rx="' + (h / 2) + '" fill="#1E2F4B"/>' +
      '<text x="' + cx.toFixed(1) + '" y="' + (y + h / 2 + 3.4).toFixed(1) +
      '" text-anchor="middle" font-size="9" font-weight="600" fill="#fff">' + esc(text) + '</text>';
  }

  function renderMonthly() {
    if (S.mMode === 'pair') return renderMonthlyPair();
    return renderMonthlyRegion();
  }

  /* ---------------- 原样式：按区域堆叠 ---------------- */
  function renderMonthlyRegion() {
    var d = S.data.monthly, box = $('#chMonthly');
    mTitle('Monthly Spend by Region');
    if (!d || !d.series || !d.series.length) {
      emptyBox('chMonthly', 'No data for the current filters'); return;
    }
    var W = chartW('chMonthly', 760), H = 300;
    var L = 58, R = 14, T = 14, B = 42;
    var iw = W - L - R, ih = H - T - B;

    // 画满 12 个财月（未到的月份留空，体现财年进度），但柱身不跟着月份数变宽
    var idx = d.labels.map(function (_, i) { return i; });
    var totals = idx.map(function (i) {
      return d.series.reduce(function (s, x) { return s + (x.vals[i] || 0); }, 0);
    });
    if (!totals.some(function (v) { return v > 0; })) {
      emptyBox('chMonthly', 'No data for the current filters'); return;
    }
    var pmax = Math.max.apply(null, totals.concat(d.prev || []));
    var ymax = niceMax(pmax * 1.12);

    var n = idx.length, slot = iw / n, bw = Math.min(52, slot * 0.62);

    var g = '';
    for (var t = 0; t <= 4; t++) {
      var y = T + ih - ih * t / 4, v = ymax * t / 4;
      g += '<line x1="' + L + '" y1="' + y.toFixed(1) + '" x2="' + (W - R) + '" y2="' + y.toFixed(1) +
        '" stroke="#E4E4E4" stroke-width="1"/>' +
        '<text x="' + (L - 8) + '" y="' + (y + 3.5).toFixed(1) +
        '" text-anchor="end" font-size="9" font-weight="600" fill="#3B3A39">' + axisMoney(v) + '</text>';
    }

    var bars = '', prevPts = [];
    idx.forEach(function (mi, j) {
      var x = L + j * slot + (slot - bw) / 2, acc = 0;
      var segs = d.series.map(function (s, si) {
        return { v: s.vals[mi] || 0, c: PALETTE[si % PALETTE.length], k: s.k, si: si };
      }).filter(function (z) { return z.v > 0; });
      segs.forEach(function (z, zi) {
        var h = z.v / ymax * ih;
        var y = T + ih - acc - h;
        var hh = Math.max(0, h - (zi < segs.length - 1 ? 1.2 : 0));
        bars += '<rect class="seg" data-reg="' + esc(z.k) + '" data-m="' + mi +
          '" x="' + x.toFixed(1) + '" y="' + y.toFixed(1) + '" width="' + bw.toFixed(1) +
          '" height="' + hh.toFixed(1) + '" fill="' + z.c + '" rx="0"/>';
        acc += h;
      });
      var pv = (d.prev || [])[mi] || 0;
      if (pv > 0) prevPts.push([x + bw / 2, T + ih - pv / ymax * ih]);
      bars += '<text x="' + (x + bw / 2).toFixed(1) + '" y="' + (H - B + 16) +
        '" text-anchor="middle" font-size="9" font-weight="600" fill="#3B3A39">' +
        esc(d.labels[mi]) + '</text>';
    });

    var prevLine = '';
    if (prevPts.length > 1) {
      prevLine = '<polyline points="' + prevPts.map(function (p) {
        return p[0].toFixed(1) + ',' + p[1].toFixed(1);
      }).join(' ') + '" fill="none" stroke="#A9B7CB" stroke-width="1.4" stroke-dasharray="4 3"/>';
    }

    $('#mLegend').innerHTML =
      d.series.map(function (s, i) {
        return '<span><i style="background:' + PALETTE[i % PALETTE.length] + '"></i>' +
          esc(s.k) + '</span>';
      }).join('') +
      '<span><i style="background:#A9B7CB;height:2px;border-radius:0"></i>Prior year</span>';

    box.innerHTML = svg(W, H, g + bars + prevLine +
      '<text x="' + (L + iw / 2) + '" y="' + (H - 4) +
      '" text-anchor="middle" font-size="9" fill="#605E5C">Fiscal month</text>');
  }

  /* ---------------- 参考样式：Total vs Framework Contract 双柱 ---------------- */
  function renderMonthlyPair() {
    var d = S.data.monthly, box = $('#chMonthly');
    mTitle('Total vs Framework Contract');
    if (!d || !d.total) { emptyBox('chMonthly', 'No monthly data'); return; }
    var tot = d.total, frm = d.frame || [];
    var lbl = d.labelsLong || d.labels || [];
    if (!tot.some(function (v) { return v > 0; })) {
      emptyBox('chMonthly', 'No data for the current filters'); return;
    }

    var W = chartW('chMonthly', 760), H = 300;
    var L = 14, R = 12, T = 42, B = 40;       // 顶部留白给胶囊
    var iw = W - L - R, ih = H - T - B;
    var peak = Math.max.apply(null, tot.concat(frm));
    /* 这张图没有 y 轴刻度，不必凑整刻度；只留 22% 给柱顶胶囊，柱子才够高 */
    var ymax = peak * 1.22 || 1;

    /* 只画到「最后一个有数的月份」：财年进行中时右侧不留一大片空白
       （区域堆叠视图仍画满 12 个月体现实进度，两种图示各自成立） */
    var last = 0;
    for (var i0 = 0; i0 < 12; i0++) {
      if ((tot[i0] || 0) > 0 || (frm[i0] || 0) > 0) last = i0;
    }
    var n = Math.max(last + 1, 3);
    var slot = iw / n;
    var gap = Math.max(4, Math.min(9, slot * 0.10));
    var bw = Math.max(10, Math.min(30, (slot * 0.52 - gap) / 2));

    var g = '';
    for (var t = 1; t <= 4; t++) {
      var y = T + ih - ih * t / 4;
      g += '<line x1="' + L + '" y1="' + y.toFixed(1) + '" x2="' + (W - R) + '" y2="' + y.toFixed(1) +
        '" stroke="#F0F0F0" stroke-width="1"/>';
    }
    g += '<line x1="' + L + '" y1="' + (T + ih).toFixed(1) + '" x2="' + (W - R) + '" y2="' +
      (T + ih).toFixed(1) + '" stroke="#D9D9D9" stroke-width="1"/>';

    /* 胶囊防重叠：按月先 Total 后 Frame，撞上就跳过（参考图靠柱高差错开） */
    var placed = [];
    function capTry(cx, topY, text) {
      var w = text.length * 5.0 + 11, h = 15;
      var bx = cx - w / 2, by = topY - 4 - h;
      for (var i = 0; i < placed.length; i++) {
        var p = placed[i];
        if (bx < p.x + p.w && bx + w > p.x && by < p.y + p.h && by + h > p.y) return '';
      }
      placed.push({ x: bx, y: by, w: w, h: h });
      return mCap(cx, topY, text);
    }

    var bars = '', caps = '';
    for (var i = 0; i < n; i++) {
      var cx = L + i * slot + slot / 2;
      var x0 = cx - bw - gap / 2, x1 = cx + gap / 2;
      var pair = [
        { v: tot[i] || 0, x: x0, c: M_TOT, s: 'tot' },
        { v: frm[i] || 0, x: x1, c: M_FC, s: 'frm' }
      ];
      for (var q = 0; q < 2; q++) {
        var z = pair[q];
        if (!(z.v > 0)) continue;
        var h = z.v / ymax * ih, topY = T + ih - h;
        bars += '<rect class="pseg" data-m="' + i + '" data-s="' + z.s +
          '" data-v="' + z.v + '" x="' + z.x.toFixed(1) + '" y="' + topY.toFixed(1) +
          '" width="' + bw.toFixed(1) + '" height="' + h.toFixed(1) +
          '" fill="' + z.c + '" rx="0"/>';
        caps += capTry(z.x + bw / 2, topY, money(z.v, 1).replace(/ ([KMB])$/, '$1'));
      }
      bars += '<text x="' + cx.toFixed(1) + '" y="' + (H - B + 18) +
        '" text-anchor="middle" font-size="9" font-weight="600" fill="#3B3A39">' +
        esc(lbl[i] || '') + '</text>';
    }

    $('#mLegend').innerHTML =
      '<span><i style="background:' + M_TOT + '"></i>Total Amount</span>' +
      '<span><i style="background:' + M_FC + '"></i>Framework Contract</span>';

    box.innerHTML = svg(W, H, g + bars + caps);
  }

  /* ================================================ Chart 2 帕累托 */
  function renderPareto() {
    var rows = (S.data.pareto && S.data.pareto.rows) || [];
    var box = $('#chPareto');
    if (!rows.length) { emptyBox('chPareto', 'No categories for the current filters'); return; }
    var W = chartW('chPareto', 660), H = 320;
    var L = 62, R = 52, T = 22, B = 76;
    var iw = W - L - R, ih = H - T - B;
    var ymax = niceMax(Math.max.apply(null, rows.map(function (r) { return r.v || 0; })) * 1.08);

    var n = rows.length, slot = iw / n, bw = Math.min(58, slot * 0.56);

    var g = '';
    for (var t = 0; t <= 4; t++) {
      var y = T + ih - ih * t / 4;
      g += '<line x1="' + L + '" y1="' + y.toFixed(1) + '" x2="' + (W - R) + '" y2="' + y.toFixed(1) +
        '" stroke="#E4E4E4"/>' +
        '<text x="' + (L - 8) + '" y="' + (y + 3.5).toFixed(1) +
        '" text-anchor="end" font-size="9" font-weight="600" fill="#3B3A39">' + axisMoney(ymax * t / 4) + '</text>' +
        '<text x="' + (W - R + 8) + '" y="' + (y + 3.5).toFixed(1) +
        '" font-size="9" font-weight="600" fill="#3B3A39">' + Math.round(t * 25) + '%</text>';
    }
    // 80% 参考线
    var y80 = T + ih - ih * 0.8;
    g += '<line x1="' + L + '" y1="' + y80.toFixed(1) + '" x2="' + (W - R) + '" y2="' + y80.toFixed(1) +
      '" stroke="#EA1313" stroke-width="1.2" stroke-dasharray="6 4"/>' +
      '<text x="' + (L + 6) + '" y="' + (y80 - 5).toFixed(1) +
      '" font-size="9" fill="#EA1313">80%</text>';

    var mx = Math.max.apply(null, rows.map(function (r) { return r.v || 0; })) || 1;
    var bars = '', pts = [];
    rows.forEach(function (r, i) {
      var h = (r.v || 0) / ymax * ih;
      var x = L + i * slot + (slot - bw) / 2, y = T + ih - h;
      // PBI 单系列列图用统一主题色，不做深浅渐变
      var col = PALETTE[1];
      bars += '<rect class="pbar" data-cat="' + esc(r.k) + '" x="' + x.toFixed(1) + '" y="' + y.toFixed(1) +
        '" width="' + bw.toFixed(1) + '" height="' + Math.max(0, h).toFixed(1) + '" fill="' + col + '" rx="0"/>';
      pts.push([x + bw / 2, T + ih - (r.cum || 0) * ih]);

      var nm = truncPx(r.k, Math.max(46, slot * 2.1), 8.5);
      bars += '<text transform="translate(' + (x + bw / 2).toFixed(1) + ',' + (T + ih + 8) +
        ') rotate(-40)" text-anchor="end" font-size="8.5" font-weight="600" fill="#3B3A39">' + esc(nm) + '</text>';
    });

    var line = '<polyline points="' + pts.map(function (p) {
      return p[0].toFixed(1) + ',' + p[1].toFixed(1);
    }).join(' ') + '" fill="none" stroke="#1E2F4B" stroke-width="1.8"/>';
    var dots = pts.map(function (p) {
      return '<circle cx="' + p[0].toFixed(1) + '" cy="' + p[1].toFixed(1) +
        '" r="2.6" fill="#1E2F4B"/>';
    }).join('');

    box.innerHTML = svg(W, H, g + bars + line + dots +
      '<text x="' + (L + iw / 2) + '" y="' + (H - 12) +
      '" text-anchor="middle" font-size="9" fill="#605E5C">Category</text>');
  }

  /* ================================================ 类别明细表
     参考 AdventureWorks Sales 里「Product details by profitability」那个视觉对象：
     行 = 维度成员，列 = 数量 / 金额 / 占比，占比那列用条形表达。
     这里行 = 采购类别，多给两列（供应商数、单据数），点一行等同勾选该类别。 */
  function renderCatDetails() {
    var d = S.data.pareto, box = $('#chCat');
    if (!d || !d.rows || !d.rows.length) {
      box.innerHTML = '<div class="no-data">No categories for the current filters</div>';
      return;
    }
    var tot = d.total || 0, sel = S.sel.category || [], MAXB = 0.55;
    /* 条形按「占最大类别的比例」缩放（PBI 数据条默认就是比最大值），
       直接按 100% 缩放的话 11 个类别最长也只有 20%，会全缩成小方块。 */
    var maxShare = tot ? (d.rows[0].v || 0) / tot : 1;
    var body = d.rows.map(function (r) {
      var share = tot ? (r.v || 0) / tot : 0;
      var on = sel.indexOf(r.k) >= 0;
      return '<tr class="cd-row' + (on ? ' on' : '') + '" data-cat="' + esc(r.k) + '">' +
        '<td class="cd-cat" title="' + esc(r.k) + '"><span>' + esc(r.k) + '</span></td>' +
        '<td class="num">' + nf(r.sup, 0) + '</td>' +
        '<td class="num">' + nf(r.n, 0) + '</td>' +
        '<td class="num cd-money">' + money(r.v, 1) + '</td>' +
        '<td class="cd-share"><i style="width:' +
          ((maxShare ? share / maxShare : 0) * MAXB * 100).toFixed(1) + '%"></i>' +
        '<b>' + pct(share) + '</b></td></tr>';
    }).join('');

    var st = S.data.status || {};
    var total = '<tr class="cd-total">' +
      '<td class="cd-cat"><span>Total</span></td>' +
      '<td class="num">' + nf(st.suppliers, 0) + '</td>' +
      '<td class="num">' + nf(st.txn, 0) + '</td>' +
      '<td class="num cd-money">' + money(tot, 1) + '</td>' +
      '<td class="cd-share"><i style="width:' + (MAXB * 100) + '%"></i>' +
      '<b>' + pct(1) + '</b></td></tr>';

    box.innerHTML = '<table class="cd-tbl"><thead><tr>' +
      '<th class="cd-cat">Category</th><th class="num">Suppliers</th>' +
      '<th class="num">PRs</th><th class="num">Spend</th><th>Share</th></tr></thead>' +
      '<tbody>' + body + total + '</tbody></table>';
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
    var p = hex(h);
    return (0.299 * p[0] + 0.587 * p[1] + 0.114 * p[2]) / 255 > 0.62;
  }

  /* ================================================ Chart 3 环形 */
  function renderDonut() {
    var rows = S.data.donut || [];
    if (!rows.length) { emptyBox('chDonut', 'No data for the current filters'); $('#donutLegend').innerHTML = ''; return; }
    var W = chartW('chDonut', 340), H = 250;
    var cx = W / 2, cy = H / 2 + 2, R = Math.min(W, H) * 0.38, r0 = R * 0.58;
    var tot = rows.reduce(function (s, x) { return s + (x.v || 0); }, 0) || 1;

    var a0 = -Math.PI / 2, paths = '', labels = '';
    rows.forEach(function (x, i) {
      var frac = (x.v || 0) / tot;
      var a1 = a0 + frac * Math.PI * 2;
      var col = PALETTE[i % PALETTE.length];
      if (frac > 0.9999) {
        paths += '<circle cx="' + cx + '" cy="' + cy + '" r="' + ((R + r0) / 2) +
          '" fill="none" stroke="' + col + '" stroke-width="' + (R - r0) + '"/>';
      } else {
        paths += '<path class="dseg" data-exp="' + esc(x.k) + '" d="' + arc(cx, cy, R, r0, a0, a1) +
          '" fill="' + col + '" stroke="#fff" stroke-width="2"/>';
      }
      var am = (a0 + a1) / 2, rr = (R + r0) / 2;
      if (frac >= 0.045) {
        labels += '<text x="' + (cx + rr * Math.cos(am)).toFixed(1) + '" y="' +
          (cy + rr * Math.sin(am) + 3.5).toFixed(1) + '" text-anchor="middle" font-size="10" ' +
          'font-weight="600" fill="' + (isLight(col) ? '#1E2F4B' : '#fff') +
          '" pointer-events="none">' + (frac * 100).toFixed(1) + '%</text>';
      }
      a0 = a1;
    });

    var center = '<text x="' + cx + '" y="' + (cy - 4) + '" text-anchor="middle" font-size="9" ' +
      'fill="#3B3A39">Total</text>' +
      '<text x="' + cx + '" y="' + (cy + 17) + '" text-anchor="middle" font-size="15" ' +
      'font-weight="600" fill="#000000">' + money(tot, 1) + '</text>';

    $('#chDonut').innerHTML = svg(W, H, paths + labels + center);
    $('#donutLegend').innerHTML = rows.map(function (x, i) {
      return '<span class="li" data-exp="' + esc(x.k) + '"><i style="background:' +
        PALETTE[i % PALETTE.length] + '"></i>' + esc(x.k) +
        ' <b>' + money(x.v, 1) + '</b><span class="pc">' + pct(x.share) + '</span></span>';
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
    var L = Math.min(200, Math.max(120, W * 0.36)), R = 64, T = 8, B = 26;
    var iw = W - L - R;
    var mx = Math.max.apply(null, rows.map(function (r) { return r.v || 0; })) || 1;

    // PBI 横向条形图：不画纵向网格线，只留底部轴线与刻度标签
    var g = '<line x1="' + L + '" y1="' + (H - B) + '" x2="' + (W - R) + '" y2="' +
      (H - B) + '" stroke="#E4E4E4"/>';
    for (var t = 0; t <= 4; t++) {
      var x = L + iw * t / 4;
      g += '<text x="' + x.toFixed(1) + '" y="' + (H - B + 14) +
        '" text-anchor="middle" font-size="9" font-weight="600" fill="#3B3A39">' + axisMoney(mx * t / 4) + '</text>';
    }

    var rowsHtml = '';
    rows.forEach(function (r, i) {
      var y = T + i * 27, bh = 17;
      var w = Math.max(2, (r.v || 0) / mx * iw);
      var col = PALETTE[1];
      rowsHtml += '<g class="trow" data-sup="' + esc(r.k) + '">' +
        '<rect x="0" y="' + y + '" width="' + W + '" height="' + 27 + '" fill="transparent"/>' +
        '<text x="' + (L - 8) + '" y="' + (y + 13) + '" text-anchor="end" font-size="9.5" ' +
        'fill="#3B3A39">' + esc(truncPx(r.en || supEn(r.k), L - 18, 9.5)) + '</text>' +
        '<rect x="' + L + '" y="' + (y + 2) + '" width="' + w.toFixed(1) + '" height="' + bh +
        '" fill="' + col + '" rx="1"/>' +
        '<text x="' + (L + w + 6).toFixed(1) + '" y="' + (y + 13) + '" font-size="9.5" ' +
        'fill="#333333">' + money(r.v, 1) + '</text></g>';
    });

    $('#chTop').innerHTML = svg(W, H, g + rowsHtml);
  }

  /* ================================================ Chart 4 热力 */
  function renderHeat() {
    var h = S.data.heat;
    if (!h || !h.rows.length) { emptyBox('chHeat', 'No cross data for the current filters'); return; }
    var W = chartW('chHeat', 1100);
    var L = Math.min(250, Math.max(160, W * 0.21)), R = 76, T = 108, B = 34;
    var cols = h.cols, rows = h.rows, mx = h.max || 1;
    var cw = (W - L - R) / cols.length, ch = 30;
    var H = T + rows.length * ch + B;

    var g = '';
    cols.forEach(function (c, i) {
      var x = L + i * cw + cw / 2;
      g += '<text transform="translate(' + x.toFixed(1) + ',' + (T - 8) + ') rotate(-38)" ' +
        'text-anchor="start" font-size="9" font-weight="600" fill="#3B3A39">' + esc(truncPx(c, 92, 9)) + '</text>';
    });

    rows.forEach(function (r, ri) {
      var y = T + ri * ch;
      g += '<text x="' + (L - 12) + '" y="' + (y + ch / 2 + 3.5) + '" text-anchor="end" ' +
        'font-size="9.5" font-weight="600" fill="#3B3A39">' + esc(truncPx(r.en || supEn(r.k), L - 28, 9.5)) + '</text>';
      r.vals.forEach(function (v, ci) {
        var x = L + ci * cw;
        var k = v > 0 ? 0.08 + 0.92 * Math.pow(v / mx, 0.55) : 0;
        var fill = v > 0 ? mix('#B1BFD5', '#1E2F4B', k) : '#FFFFFF';
        g += '<rect class="hcell" data-sup="' + esc(r.k) + '" data-cat="' + esc(cols[ci]) +
          '" x="' + (x + 1).toFixed(1) + '" y="' + (y + 1) + '" width="' + (cw - 2).toFixed(1) +
          '" height="' + (ch - 2) + '" fill="' + fill + '" stroke="#E4E4E4" rx="0"/>';
        if (v > 0 && cw > 52) {
          g += '<text x="' + (x + cw / 2).toFixed(1) + '" y="' + (y + ch / 2 + 3.5) +
            '" text-anchor="middle" font-size="9" pointer-events="none" fill="' +
            (k > 0.45 ? '#fff' : '#000000') + '">' + cellMoney(v) + '</text>';
        }
      });
    });

    // 右侧色阶
    var bx = W - R + 18, by = T, bh = Math.min(140, rows.length * ch - 10), bw = 11;
    var grad = '<defs><linearGradient id="hg" x1="0" y1="1" x2="0" y2="0">' +
      '<stop offset="0" stop-color="#B1BFD5"/><stop offset="1" stop-color="#1E2F4B"/>' +
      '</linearGradient></defs>';
    g += grad + '<rect x="' + bx + '" y="' + by + '" width="' + bw + '" height="' + bh +
      '" fill="url(#hg)" stroke="#E4E4E4"/>';
    [0, 0.5, 1].forEach(function (kk) {
      var y = by + bh - bh * kk;
      g += '<text x="' + (bx + bw + 5) + '" y="' + (y + 3.5) +
        '" font-size="9" fill="#3B3A39">' + axisMoney(mx * kk) + '</text>';
    });
    g += '<text x="' + bx + '" y="' + (by - 8) + '" font-size="9" fill="#3B3A39">Amount</text>';
    g += '<text x="' + (L + (W - L - R) / 2) + '" y="' + (H - 4) +
      '" text-anchor="middle" font-size="9" fill="#605E5C">Category</text>';

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
    if (!rows.length) return '<tr><td class="empty" colspan="' + COLS.length + '">No matching records</td></tr>';
    return rows.map(function (r) {
      return '<tr>' + COLS.map(function (c) {
        if (c.k === 'amount') return '<td class="num">' + money(r.amount, 1) + '</td>';
        if (c.k === 'days') return '<td class="num">' + (r.days == null ? '—' : Number(r.days).toFixed(1)) + '</td>';
        if (c.k === 'no') return '<td class="strong">' + esc(r.no) + '</td>';
        if (c.k === 'date') return '<td>' + esc((r.date || '').slice(5)) + '</td>';
        return '<td title="' + esc(r[c.k]) + '">' + esc(r[c.k] || '—') + '</td>';
      }).join('') + '</tr>';
    }).join('');
  }
  function pagerHtml(total, page, size) {
    var pages = Math.max(1, Math.ceil(total / size));
    var from = total ? (page - 1) * size + 1 : 0, to = Math.min(total, page * size);
    return '<span><b>' + nf(total, 0) + '</b> rows · ' + from + '–' + to +
      ' · page ' + page + '/' + pages + '</span>' +
      '<span class="pg-btns">' +
      '<button data-pg="1"' + (page <= 1 ? ' disabled' : '') + '>First</button>' +
      '<button data-pg="' + (page - 1) + '"' + (page <= 1 ? ' disabled' : '') + '>Prev</button>' +
      '<button data-pg="' + (page + 1) + '"' + (page >= pages ? ' disabled' : '') + '>Next</button>' +
      '<button data-pg="' + pages + '"' + (page >= pages ? ' disabled' : '') + '>Last</button>' +
      '</span>';
  }

  /* ================================================ 框架协议统计表 */
  var FC_COLS = [
    { k: 'contract', t: 'Contract' },
    { k: 'supplier', t: 'Supplier' },
    { k: 'cv', t: 'CV', num: 1 },
    { k: 'pa', t: 'PA', num: 1 },
    { k: 'usage', t: 'Usage', num: 1, w: 96, tip: 'PA counts framework-contract POs only' },
    { k: 'start', t: 'Start Date', w: 92 },
    { k: 'expiry', t: 'Expiry Date', w: 92 },
    { k: 'remain', t: 'Remain Days', num: 1, w: 96 },
    { k: 'type', t: 'Type', w: 54 }
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
  function fcHeadHtml() {
    return '<tr>' + FC_COLS.map(function (c) {
      var arr = S.fc.sort === c.k ? '<span class="arr">' + (S.fc.dir === 'asc' ? '▲' : '▼') + '</span>' : '';
      return '<th data-sort="' + c.k + '"' + (c.w ? ' style="width:' + c.w + 'px"' : '') +
        (c.num ? ' class="ta-r"' : '') + (c.tip ? ' title="' + c.tip + '"' : '') +
        '>' + c.t + arr + '</th>';
    }).join('') + '</tr>';
  }
  function fcRowsHtml(rows) {
    if (!rows.length) {
      return '<tr><td class="empty" colspan="' + FC_COLS.length + '">No matching agreements</td></tr>';
    }
    return rows.map(function (r) {
      var u = r.usage;
      var uc = u == null ? '' : (u > 1 ? 'hi' : (u >= 0.5 ? 'mid' : 'low'));
      var ut = u == null ? '—' : (u * 100).toFixed(1) + '%';
      var rd = r.remain, rc = rd < 0 ? 'bad' : (rd <= 90 ? 'warn' : 'ok');
      var rt = (rd < 0 ? '✕ ' : (rd <= 90 ? '! ' : '✓ ')) + rd;
      return '<tr>' +
        '<td class="contract strong" title="' + esc(r.contract) + '">' + esc(r.contract) + '</td>' +
        '<td class="supplier" title="' + esc(r.supplier) + '">' +
          esc(r.supplier_en || supEn(r.supplier)) + '</td>' +
        '<td class="num">' + money(r.cv, 1) + '</td>' +
        '<td class="num">' + money(r.pa, 1) + '</td>' +
        '<td class="num"><span class="usage ' + uc + '">' + ut + '</span></td>' +
        '<td>' + esc(r.start || '—') + '</td>' +
        '<td>' + esc(r.expiry || '—') + '</td>' +
        '<td class="num"><span class="remain ' + rc + '">' + rt + '</span></td>' +
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
      $('#fcTbl tbody').innerHTML = fcRowsHtml(d.rows || []);
      $('#fcPager').innerHTML = pagerHtml(d.total, d.page, d.size);
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
    $('#drawer').setAttribute('aria-hidden', 'false');
    loadDrawer();
  }
  function closeDrawer() {
    $('#drawer').classList.remove('on');
    $('#mask').classList.remove('on');
    $('#drawer').setAttribute('aria-hidden', 'true');
  }
  function loadDrawer() {
    var seq = ++S.drawerSeq;
    var p = qs(Object.assign({
      page: S.drawer.page, size: S.drawer.size, q: S.drawer.q,
      sort: 'amount', dir: 'desc'
    }, S.drawer.filters || {}));
    api('/api/board/rows', p).then(function (d) {
      if (seq !== S.drawerSeq) return;
      $('#drawerTbl thead').innerHTML = headHtml(COLS, 'amount', 'desc');
      $('#drawerTbl tbody').innerHTML = rowsHtml(d.rows || []);
      $('#drawerPager').innerHTML = pagerHtml(d.total, d.page, d.size) +
        (d.amount != null ? '<span>Total ' + money(d.amount, 0) + '</span>' : '');
      $('#drawerSub').textContent = (S.drawer.label || '') +
        nf(d.total, 0) + ' PRs · total ' + money(d.amount, 0);
    }).catch(function (e) { toast('Failed to load details: ' + e.message); });
  }

  /* ================================================ 加载 */
  function loadAll() {
    var seq = ++S.seq;
    document.body.classList.add('loading');
    return api('/api/board/data', qs()).then(function (d) {
      if (seq !== S.seq) return;
      S.data = d; S.rate = d.rate || 1;
      renderKpis(); renderMonthly(); renderPareto(); renderCatDetails();
      renderDonut(); renderHeat();
      loadTop();
      loadContracts();
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
      var sel = $('#fySel');
      sel.innerHTML = (d.fy || []).map(function (f) {
        return '<option value="' + esc(f) + '"' + (f === d.latestFy ? ' selected' : '') + '>' +
          esc(f) + '</option>';
      }).join('');
      S.fy = d.latestFy;

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

      // 框架协议表的局部选项（Type / 日期边界），独立于 PRPO 全局筛选
      return api('/api/board/contracts', fcqs({ size: 1 })).then(function (cd) {
        $('#fcType').innerHTML = '<option value="">All types</option>' +
          (cd.types || []).map(function (o) {
            return '<option value="' + esc(o.k) + '">' + esc(o.k) + ' (' + o.n + ')</option>';
          }).join('');
        var b = cd.bounds || {};
        if (b.mn) {
          ['#fcFrom', '#fcTo'].forEach(function (sel) {
            $(sel).min = b.mn;
            if (b.mx) $(sel).max = b.mx;
          });
        }
      });
    });
  }

  /* ================================================ 事件 */
  function bind() {
    // 筛选下拉
    $('#fbar').addEventListener('click', function (e) {
      var a = e.target.closest('a[data-act]');
      if (a) {
        var k = a.closest('.fdrop').dataset.k;
        setSel(k, []);
        renderFilters(); loadAll();
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
      var drop = inp.closest('.fdrop');
      var list = $$('.fpop-b input', drop).filter(function (i) { return i.checked; })
        .map(function (i) { return i.value; });
      setSel(drop.dataset.k, list);
      updateFilterLabels();
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
      setSel(k, (S.sel[k] || []).filter(function (x) { return x !== v; }));
      renderFilters(); loadAll();
    });

    // 重置
    $('#btnReset').addEventListener('click', function () {
      S.sel = {}; S.topLocal = { category: '', method: '' };
      $('#topCat').value = ''; $('#topMth').value = '';
      renderFilters(); loadAll();
      toast('Filters reset');
    });

    // 财年 / 货币
    $('#fySel').addEventListener('change', function (e) {
      S.fy = e.target.value; S.fc.page = 1; loadAll();
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
      $$('#mSw button').forEach(function (x) { x.classList.toggle('on', x === btn); });
      if (S.data && S.data.monthly) renderMonthly();
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
      setSel('region', list); renderFilters(); loadAll();
    });
    $('#chCat').addEventListener('click', function (e) {
      var tr = e.target.closest('.cd-row');
      if (!tr) return;
      var cat = tr.dataset.cat;
      var list = (S.sel.category || []).slice();
      var i = list.indexOf(cat);
      if (i >= 0) list.splice(i, 1); else list.push(cat);
      setSel('category', list); renderFilters(); loadAll();
    });
    $('#chPareto').addEventListener('click', function (e) {
      var r = e.target.closest('rect.pbar');
      if (!r) return;
      var cat = r.dataset.cat;
      var list = (S.sel.category || []).slice();
      var i = list.indexOf(cat);
      if (i >= 0) list.splice(i, 1); else list.push(cat);
      setSel('category', list); renderFilters(); loadAll();
    });
    $('#chDonut').addEventListener('click', function (e) {
      var p = e.target.closest('[data-exp]');
      if (!p) return;
      var v = p.dataset.exp;
      var list = (S.sel.expense || []).slice();
      var i = list.indexOf(v);
      if (i >= 0) list.splice(i, 1); else list.push(v);
      setSel('expense', list); renderFilters(); loadAll();
    });
    $('#donutLegend').addEventListener('click', function (e) {
      var p = e.target.closest('[data-exp]');
      if (!p) return;
      var v = p.dataset.exp;
      var list = (S.sel.expense || []).slice();
      var i = list.indexOf(v);
      if (i >= 0) list.splice(i, 1); else list.push(v);
      setSel('expense', list); renderFilters(); loadAll();
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
      showTip('<b>' + esc(S.data.monthly.labels[mi]) + ' · ' + esc(reg) + '</b>' +
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
        '<div class="tip-row"><span>PRs</span><span>' + nf(row.n, 0) + '</span></div>',
        e.clientX, e.clientY);
    });
    // 悬浮提示（环形）
    $('#chDonut').addEventListener('mousemove', function (e) {
      var p = e.target.closest('[data-exp]');
      if (!p || !S.data) { hideTip(); return; }
      var row = (S.data.donut || []).find(function (x) { return x.k === p.dataset.exp; });
      if (!row) { hideTip(); return; }
      showTip('<b>' + esc(row.k) + '</b>' +
        '<div class="tip-row"><span>Amount</span><span>' + money(row.v, 1) + '</span></div>' +
        '<div class="tip-row"><span>Share</span><span>' + pct(row.share) + '</span></div>' +
        '<div class="tip-row"><span>PRs</span><span>' + nf(row.n, 0) + '</span></div>',
        e.clientX, e.clientY);
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
        '<div class="tip-row"><span>PRs</span><span>' + nf(row.n, 0) + '</span></div>' +
        '<div class="tip-row"><span>Categories</span><span>' + nf(row.cats, 0) + '</span></div>' +
        '<div style="color:#A9C0D6;margin-top:3px">Click for details</div>',
        e.clientX, e.clientY);
    });
    ['#chMonthly', '#chPareto', '#chDonut', '#chTop', '#chHeat'].forEach(function (sel) {
      $(sel).addEventListener('mouseleave', hideTip);
    });

    // 框架协议表：排序 / 翻页 / 局部筛选
    $('#fcTbl thead').addEventListener('click', function (e) {
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
        S.fc.page = 1; loadContracts();
      });
    });
    $('#fcReset').addEventListener('click', function () {
      S.fc = { page: 1, size: 50, sort: 'start', dir: 'desc',
               q: '', sup: '', type: '', df: '', dt: '', status: '' };
      ['#fcQ', '#fcSup', '#fcType', '#fcStatus', '#fcFrom', '#fcTo'].forEach(function (sel) {
        $(sel).value = '';
      });
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
    $('#drawerClose').addEventListener('click', closeDrawer);
    $('#mask').addEventListener('click', closeDrawer);
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

    // 自适应重绘
    var rt = null;
    window.addEventListener('resize', function () {
      clearTimeout(rt);
      rt = setTimeout(function () {
        if (!S.data) return;
        renderMonthly(); renderPareto(); renderCatDetails();
        renderDonut(); renderHeat(); renderTop();
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
    filter_level: [],
    filter_region: [],
    monthly: ['fiscal', 'yoy'],
    donut: [],
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
        '中间那排筛选（品牌、类别、支出类型、供应商属性、区域）同时作用于 KPI 和所有图表，多选取「或」的关系。',
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
      title: '筛选 · 区域',
      source: '数据来源：' + SRC.comp,
      notes: [
        '区域同样是由' + SRC.prpo + '的「公司」关联' + SRC.comp + '带出来的（南区、北区等）。',
        '可以多选，选中之间是「或」的关系。'
      ],
      fields: [['区域', '区域（RegionAbbreviationCN）', SRC.comp]]
    },

    /* ---------------- 图表 ---------------- */
    monthly: {
      title: '月度支出（两种图示）',
      source: '数据来源：' + SRC.prpo + ' + ' + SRC.cal + ' + ' + SRC.comp,
      notes: [
        '右上角可切换两种图示，数据口径相同。',
        'Region（默认）：柱子按财月堆叠，一根柱子里的不同颜色是不同区域；虚线是上一个财年同月的总额；点柱子按区域筛选。',
        'Total vs Framework：每月两根柱，深色是当月全部采购，橙色是其中「通过框架协议方式申请」的部分；柱顶胶囊就是该柱金额，点柱子看当月明细。',
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
    catdetail: {
      title: '类别明细',
      source: '数据来源：' + SRC.prpo + ' + ' + SRC.catmap + '（与帕累托图同一份结果）',
      notes: [
        '这张表和左边的帕累托图是同一份汇总结果，只是换了看法：一行一个采购类别。',
        '「供应商数」是这一类别下去重后的供应商家数；「单据数」是采购申请单的条数。',
        '「金额」= 该类别所有采购申请单的含增值税总价之和；「占比」= 该类别金额 ÷ 当前筛选下的总金额，条形长度就是它。',
        '合计行的供应商数是整页去重后的家数，不是各类别相加 —— 同一家供应商可能跨类别，相加会重复计算。',
        '点任意一行等于勾选这个类别（再点一次取消），和点帕累托柱子是同一个动作。'
      ],
      fields: [
        ['类别', '采购类别（归并后 11 类）', SRC.prpo + ' → ' + SRC.catmap],
        ['供应商数', '供应商名称（去重计数）', SRC.prpo],
        ['单据数', '表单编号（计数）', SRC.prpo],
        ['金额', '含增值税总价（求和）', SRC.prpo],
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
        '供应商原始名是「中文全称-英文名」，列表里只显示中文部分，鼠标悬停给全名。'
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
        '只算这 10 × 8 的交叉部分，不是全量交叉。'
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

  /* ================================================ 启动 */
  function init() {
    bind();
    bindSpec();
    loadOptions().then(loadAll).catch(function (e) {
      toast('Initialization failed: ' + e.message, 5000);
      console.error(e);
    });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
