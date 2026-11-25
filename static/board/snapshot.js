/* ============================================================
   静态快照垫片（只在导出的单文件 HTML 里加载）
   - 把 /api/board/* 的请求就地算出来，不发网络请求
   - 导出按钮改由前端拼 CSV
   数据来自 window.__SNAP__（board_export.py 生成）
   ============================================================ */
(function () {
  'use strict';
  var S = window.__SNAP__;
  if (!S || !S.rows) return;

  var r2 = function (x) { return x == null ? null : Math.round(x * 100) / 100; };
  var num = function (x) { return x == null ? null : (Math.round(x * 100) / 100); };

  /* ---------- 行数据还原 ---------- */
  var R = S.rows, COLS = R.cols, DICT = R.dict || {};
  var rows = R.data.map(function (a) {
    var o = {};
    for (var i = 0; i < COLS.length; i++) {
      var c = COLS[i], v = a[i], d = DICT[c];
      o[c] = d ? (v < 0 ? null : d[v]) : v;
    }
    return o;
  });

  var FILTER_KEYS = ['brand', 'category', 'sub', 'expense', 'level', 'region',
                     'company', 'method', 'supplier'];

  function readFilters(sp) {
    var o = {};
    FILTER_KEYS.forEach(function (k) {
      var v = sp.getAll(k).filter(function (x) { return x !== '' && x != null; });
      if (v.length) o[k] = v;
    });
    /* 单一来源理由：在线在后端用 fct_prpo 子查询，离线用行内 exc / ssr 两列 */
    [['exempt_reason', 'exc'], ['single_reason', 'ssr']].forEach(function (p) {
      var v = sp.getAll(p[0]).filter(function (x) { return x !== '' && x != null; });
      if (v.length) o[p[0]] = v;
    });
    return o;
  }
  function pass(r, f) {
    for (var k in f) {
      var v = k === 'exempt_reason' ? r.exc : (k === 'single_reason' ? r.ssr : r[k]);
      if (f[k].indexOf(v) < 0) return false;
    }
    return true;
  }
  function pick(fy, f) {
    return rows.filter(function (r) { return r.fy === fy && pass(r, f); });
  }
  function prevOf(fy) {
    if (!fy || fy.length !== 6) return null;
    var a = parseInt(fy.slice(2, 4), 10) - 1, b = parseInt(fy.slice(4, 6), 10) - 1;
    if (isNaN(a) || isNaN(b)) return null;
    return 'FY' + ('0' + a).slice(-2) + ('0' + b).slice(-2);
  }
  var MON = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
  var MON_LONG = ['January','February','March','April','May','June',
                  'July','August','September','October','November','December'];
  function monthLabel(fm) { return MON[((fm - 1 + 6) % 12)]; }
  var CP = S.cpMethods || ['SS-Exempt', 'SS-Non Exempt', 'Tender'];

  function medianArr(v) {
    if (!v.length) return null;
    v.sort(function (a, b) { return a - b; });
    var n = v.length;
    return (v[Math.floor((n - 1) / 2)] + v[Math.floor(n / 2)]) / 2;
  }
  function rateOf(cur) { return cur === 'RM' ? 1.5 : 1; }

  /* ---------- 聚合 ---------- */
  function kpiOf(list) {
    /* 与服务端一致：SUM 在「没有可加的行」时返回 null，不是 0 */
    var amount = null, cp = null, poPaid = null, txn = 0;
    var sup = {}, store = {}, cat = {}, days = [];
    for (var i = 0; i < list.length; i++) {
      var r = list[i];
      if (r.no != null) txn++;
      if (r.amount != null) amount = (amount || 0) + r.amount;
      if (r.amount != null && CP.indexOf(r.method) >= 0) cp = (cp || 0) + r.amount;
      if (r.poPaid != null) poPaid = (poPaid || 0) + r.poPaid;
      if (r.supplier != null) sup[r.supplier] = 1;
      if (r.company != null) store[r.company] = 1;
      if (r.category != null) cat[r.category] = 1;
      if (r.days != null) days.push(r.days);
    }
    return {
      amount: amount, cp: cp, poPaid: poPaid, txn: txn,
      suppliers: Object.keys(sup).length,
      stores: Object.keys(store).length,
      days: medianArr(days)
    };
  }

  function monthlyOf(list) {
    var series = {}, total = new Array(12).fill(0);
    var frame = new Array(12).fill(0);
    list.forEach(function (r) {
      var fm = r.month;
      if (!fm || fm < 1 || fm > 12) return;
      var k = r.region == null || r.region === '' ? 'Unassigned' : r.region;
      if (!series[k]) series[k] = new Array(12).fill(0);
      var v = r.amount || 0;
      series[k][fm - 1] += v;
      total[fm - 1] += v;
      /* 与后端同口径：豁免理由原始值 'Ongoing Frame Contract'，不做映射 */
      if (r.exc === 'Ongoing Frame Contract') frame[fm - 1] += v;
    });
    var keys = Object.keys(series).sort(function (a, b) {
      return sum(b) - sum(a);
    });
    function sum(k) { return series[k].reduce(function (a, b) { return a + b; }, 0); }
    var tot = total.map(function (v) { return r2(v); });
    return {
      labels: Array.from({ length: 12 }, function (_, i) { return monthLabel(i + 1); }),
      labelsLong: Array.from({ length: 12 }, function (_, i) { return MON_LONG[(i + 6) % 12]; }),
      series: keys.map(function (k) {
        var en = (S.regionEn || {})[k] || k;
        return { k: k, l: en, vals: series[k].map(function (v) { return r2(v); }) };
      }),
      total: tot,
      frame: frame.map(function (v) { return r2(v); }),
      cur: tot
    };
  }

  function paretoOf(list) {
    var m = {}, total = 0, sups = {}, subs = {};
    list.forEach(function (r) {
      var k = r.category == null || r.category === '' ? 'Unassigned' : r.category;
      if (!m[k]) { m[k] = { k: k, v: 0, n: 0, po_paid: 0 }; sups[k] = {}; subs[k] = {}; }
      m[k].v += (r.amount || 0);
      m[k].po_paid += (r.poPaid || 0);
      if (r.no != null) m[k].n++;
      if (r.supplier != null) sups[k][r.supplier] = 1;
      var sk = r.sub == null || r.sub === '' ? 'Unassigned' : r.sub;
      var s = subs[k][sk] || (subs[k][sk] = { k: sk, v: 0, n: 0, po: 0 });
      s.v += (r.amount || 0); s.po += (r.poPaid || 0);
      if (r.no != null) s.n++;
      total += (r.amount || 0);
    });
    var out = Object.keys(m).map(function (k) { return m[k]; })
      .sort(function (a, b) { return b.v - a.v || (a.k < b.k ? -1 : 1); });
    var acc = 0, subMap = {}, subsMap = {};
    out.forEach(function (r) {
      acc += r.v;
      r.sup = Object.keys(sups[r.k] || {}).length;
      r.cum = total ? Math.round((acc / total) * 10000) / 10000 : null;
      r.share = total ? Math.round((r.v / total) * 10000) / 10000 : null;
      var sl = Object.keys(subs[r.k] || {}).map(function (s) { return subs[r.k][s]; })
        .sort(function (a, b) { return b.v - a.v || (a.k < b.k ? -1 : 1); })
        .map(function (s) { return { k: s.k, v: r2(s.v), n: s.n, po: r2(s.po) }; });
      subsMap[r.k] = sl;
      if (sl.length && sl[0].k !== 'Unassigned') subMap[r.k] = sl[0].k;
      r.v = r2(r.v);
      r.po_paid = r2(m[r.k].po_paid);
    });
    return { rows: out, total: r2(total), subMap: subMap, subsMap: subsMap };
  }

  function donutOf(list, field, blank) {
    field = field || 'expense'; blank = blank || 'Unclassified';
    var m = {}, total = 0;
    list.forEach(function (r) {
      var k = r[field] == null || r[field] === '' ? blank : r[field];
      if (!m[k]) m[k] = { k: k, v: 0, n: 0 };
      m[k].v += (r.amount || 0);
      if (r.no != null) m[k].n++;
      total += (r.amount || 0);
    });
    return Object.keys(m).map(function (k) { return m[k]; })
      .sort(function (a, b) { return b.v - a.v || (a.k < b.k ? -1 : 1); })
      .map(function (r) {
        return {
          k: r.k, v: r2(r.v), n: r.n,
          share: total ? Math.round((r.v / total) * 10000) / 10000 : null
        };
      });
  }

  /* 采购方式第二层：单一来源两种理由的聚合（与在线 methodDrill 同口径） */
  function methodDrillOf(list) {
    var RM = S.reasonMap || { exempt: {}, single: {} };
    var defs = {
      'SS-Exempt': { map: RM.exempt || {}, col: 'exc', reason: '豁免单一来源的理由' },
      'SS-Non Exempt': { map: RM.single || {}, col: 'ssr', reason: '单一来源采购理由' }
    };
    var out = {};
    Object.keys(defs).forEach(function (mk) {
      var d = defs[mk], m = {}, rawByAgg = {}, total = 0;
      list.forEach(function (r) {
        if (r.method !== mk) return;
        var raw = r[d.col];
        var agg = (raw === null || raw === undefined || raw === '') ? 'Unassigned' : (d.map[raw] || raw);
        if (!m[agg]) m[agg] = { k: agg, v: 0, n: 0 };
        m[agg].v += (r.amount || 0);
        if (r.no != null) m[agg].n++;
        total += (r.amount || 0);
        var arr = rawByAgg[agg] || (rawByAgg[agg] = []);
        if (raw && arr.indexOf(raw) < 0) arr.push(raw);
      });
      var rs = Object.keys(m).map(function (k) { return m[k]; })
        .sort(function (a, b) { return b.v - a.v; })
        .map(function (x) {
          return { k: x.k, v: r2(x.v), n: x.n,
                   share: total ? Math.round((x.v / total) * 10000) / 10000 : null,
                   raws: rawByAgg[x.k] || [] };
        });
      out[mk] = { rows: rs, reason: d.reason, total: r2(total) };
    });
    return out;
  }

  function topOf(list, limit) {
    var m = {};
    list.forEach(function (r) {
      var k = r.supplier == null || r.supplier === '' ? 'Unassigned' : r.supplier;
      if (!m[k]) m[k] = { k: k, v: 0, n: 0, cats: {} };
      m[k].v += (r.amount || 0);
      if (r.no != null) m[k].n++;
      if (r.category != null) m[k].cats[r.category] = 1;
    });
    return Object.keys(m).map(function (k) {
      return { k: k, v: r2(m[k].v), n: m[k].n, cats: Object.keys(m[k].cats).length };
    }).sort(function (a, b) { return b.v - a.v || (a.k < b.k ? -1 : 1); })
      .slice(0, limit || 10);
  }

  function heatOf(list, top, cats) {
    var cell = {};
    list.forEach(function (r) {
      var s = r.supplier == null || r.supplier === '' ? 'Unassigned' : r.supplier;
      var c = r.category == null || r.category === '' ? 'Unassigned' : r.category;
      if (top.indexOf(s) < 0 || cats.indexOf(c) < 0) return;
      cell[s + '\u0001' + c] = (cell[s + '\u0001' + c] || 0) + (r.amount || 0);
    });
    var mx = 0;
    Object.keys(cell).forEach(function (k) { if (cell[k] > mx) mx = cell[k]; });
    return {
      cols: cats,
      rows: top.map(function (nm) {
        return {
          k: nm,
          vals: cats.map(function (ct) { return r2(cell[nm + '\u0001' + ct] || 0); })
        };
      }),
      max: r2(mx)
    };
  }

  function dateRange(list) {
    var mn = null, mx = null;
    list.forEach(function (r) {
      if (!r.date) return;
      if (mn == null || r.date < mn) mn = r.date;
      if (mx == null || r.date > mx) mx = r.date;
    });
    return { from: mn, to: mx };
  }

  /* ---------- /api/board/data ---------- */
  function dataResp(sp) {
    var fy = sp.get('fy') || S.options.latestFy;
    var prevFy = prevOf(fy);
    var cur = (sp.get('cur') || 'RMB').toUpperCase();
    if (cur !== 'RM') cur = 'RMB';
    var rate = rateOf(cur);
    var f = readFilters(sp);

    var list = pick(fy, f);
    var k0 = kpiOf(list);
    var kp = prevFy ? kpiOf(pick(prevFy, f)) : {};
    var daysList = list.filter(function (r) { return r.days != null; }).map(function (r) { return r.days; });
    var pDays = prevFy ? medianArr(pick(prevFy, f)
      .filter(function (r) { return r.days != null; })
      .map(function (r) { return r.days; })) : null;

    function yoy(a, b) { return (b == null || a == null) ? null : (a - b) / b; }
    var defs = [
      ['amount', 'Total Spend', 'money', k0.amount, kp.amount, null],
      ['cp', 'Centralised Spend', 'money', k0.cp, kp.cp, 'SS / Tender'],
      ['poSpend', 'PO Paid', 'money', k0.poPaid, kp.poPaid, 'Actually paid'],
      ['txn', 'Orders', 'int', k0.txn, kp.txn, null],
      ['suppliers', 'Suppliers', 'int', k0.suppliers, kp.suppliers, 'unique'],
      ['stores', 'Stores Covered', 'int', k0.stores, kp.stores, null],
      ['days', 'Median Cycle Time', 'days', medianArr(daysList), pDays, 'submit → approve']
    ];
    var kpi = defs.map(function (d) {
      return {
        k: d[0], label: d[1], fmt: d[2],
        v: num(d[3]), pv: d[4] == null ? null : num(d[4]),
        yoy: yoy(num(d[3]), num(d[4])), sub: d[5] || undefined
      };
    });

    var monthly = monthlyOf(list);
    var prevLine = new Array(12).fill(0);
    if (prevFy) {
      pick(prevFy, f).forEach(function (r) {
        if (r.month >= 1 && r.month <= 12) prevLine[r.month - 1] += (r.amount || 0);
      });
    }
    monthly.prev = prevLine.map(function (v) { return r2(v); });

    var pareto = paretoOf(list);
    var donut = donutOf(list);
    var methodDonut = donutOf(list, 'method', 'Unassigned');
    var top = topOf(list, 10);
    var cats = pareto.rows.slice(0, 8).map(function (r) { return r.k; });
    var heat = heatOf(list, top.map(function (r) { return r.k; }), cats);
    var rg = dateRange(list);

    return {
      fy: fy, prevFy: prevFy, cur: cur, rate: rate,
      range: rg,
      status: { txn: k0.txn, suppliers: k0.suppliers, stores: k0.stores },
      kpi: kpi, monthly: monthly, pareto: pareto, donut: donut, methodDonut: methodDonut,
      top: top, heat: heat, palette: S.palette,
      params: { fy: fy, prevFy: prevFy, cur: cur, rate: rate, cpMethods: CP },
      spec: {}
    };
  }

  /* ---------- /api/board/top ---------- */
  function topResp(sp) {
    var fy = sp.get('fy') || S.options.latestFy;
    var f = readFilters(sp);
    return { fy: fy, rows: topOf(pick(fy, f), 10), spec: {} };
  }

  /* ---------- /api/board/rows ---------- */
  var SORT_COL = {
    date: 'date', amount: 'amount', days: 'days', supplier: 'supplier',
    company: 'company', category: 'category', no: 'no', expense: 'expense'
  };
  function rowsWhere(sp, fy, agg) {
    var f = readFilters(sp);
    var q = (sp.get('q') || '').trim().toLowerCase();
    var out = rows.filter(function (r) {
      if (r.fy !== fy) return false;
      if (!pass(r, f)) return false;
      if (!q) return true;
      /* 公司聚合时只按公司/区域搜，不能拿单号筛行 */
      var hay = agg ? [r.company, r.region]
        : [r.no, r.supplier, r.company, r.category, r.dept, r.brand];
      for (var i = 0; i < hay.length; i++) {
        if (hay[i] && String(hay[i]).toLowerCase().indexOf(q) >= 0) return true;
      }
      return false;
    });
    return out;
  }
  function rowsResp(sp) {
    var fy = sp.get('fy') || S.options.latestFy;
    var page = Math.max(parseInt(sp.get('page') || '1', 10) || 1, 1);
    var size = Math.min(Math.max(parseInt(sp.get('size') || '50', 10) || 50, 10), 200);
    var sk = SORT_COL[sp.get('sort') || 'date'] || 'date';
    var dir = sp.get('dir') === 'asc' ? 1 : -1;
    var byCompany = (sp.get('by') || '') === 'company';
    var list = rowsWhere(sp, fy, byCompany).slice();

    /* by=company：与后端 /api/board/rows 一致，按「公司 + 区域」聚合 */
    if (byCompany) {
      var m = {}, seqK = [], totA = 0, totP = 0;
      list.forEach(function (r) {
        var cn = r.company == null || r.company === '' ? '—' : r.company;
        var rg = r.region == null || r.region === '' ? '—' : r.region;
        var key = cn + '\u0001' + rg, g = m[key];
        if (!g) {
          g = m[key] = { company: cn, region: rg, n: 0, sup: {},
                         amount: 0, poPaid: 0, dsum: 0, dn: 0 };
          seqK.push(key);
        }
        g.n++;
        if (r.supplier) g.sup[r.supplier] = 1;
        g.amount += r.amount || 0;
        g.poPaid += r.poPaid || 0;
        if (r.days != null) { g.dsum += r.days; g.dn++; }
        totA += r.amount || 0;
        totP += r.poPaid || 0;
      });
      var ag = seqK.map(function (k) {
        var g = m[k];
        return {
          company: g.company, region: g.region, n: g.n,
          sup: Object.keys(g.sup).length,
          amount: r2(g.amount), poPaid: r2(g.poPaid),
          days: g.dn ? Math.round((g.dsum / g.dn) * 10) / 10 : null
        };
      }).sort(function (a, b) {
        return (b.amount - a.amount) || (a.company < b.company ? -1 : 1);
      });
      return {
        total: ag.length, page: page, size: size, by: 'company',
        amount: r2(totA), poPaid: r2(totP),
        rows: ag.slice((page - 1) * size, page * size)
      };
    }

    list.sort(function (a, b) {
      var x = a[sk], y = b[sk];
      if (x == null && y == null) return cmpNo(a, b);
      if (x == null) return 1;
      if (y == null) return -1;
      if (x === y) return cmpNo(a, b);
      return (x < y ? -1 : 1) * dir;
    });
    function cmpNo(a, b) {
      var p = a.no == null ? '' : String(a.no), q2 = b.no == null ? '' : String(b.no);
      return p < q2 ? -1 : (p > q2 ? 1 : 0);
    }
    var amount = 0;
    list.forEach(function (r) { amount += (r.amount || 0); });
    var slice = list.slice((page - 1) * size, page * size).map(function (r) {
      return {
        no: r.no, date: r.date, company: r.company, region: r.region,
        brand: r.brand, dept: r.dept, category: r.category, expense: r.expense,
        method: r.method, supplier: r.supplier, amount: r.amount,
        days: r.days, budget: r.budget, level: r.level, poPaid: r.poPaid
      };
    });
    return { total: list.length, page: page, size: size, amount: r2(amount),
             poPaid: r2(list.reduce(function (s, r) { return s + (r.poPaid || 0); }, 0)),
             rows: slice };
  }

  /* ---------- /api/board/contracts ---------- */
  var C = S.contracts || [];
  var C_SORT = {
    contract: 0, supplier: 1, cv: 2, pa: 4, usage: 5,
    start: 6, expiry: 7, remain: 8, type: 9
  };
  function contractWhere(sp) {
    var type = sp.get('type') || '';
    var df = sp.get('df') || '', dt = sp.get('dt') || '';
    var q = (sp.get('q') || '').trim().toLowerCase();
    var sup = (sp.get('sup') || '').trim().toLowerCase();
    var st = sp.get('status') || '';
    return C.filter(function (r) {
      if (type && r[9] !== type) return false;
      if (df && (!r[6] || r[6] < df)) return false;
      if (dt && (!r[6] || r[6] > dt)) return false;
      if (q && String(r[0] || '').toLowerCase().indexOf(q) < 0) return false;
      if (sup && String(r[1] || '').toLowerCase().indexOf(sup) < 0) return false;
      if (st === 'expired' && !(r[8] < 0)) return false;
      if (st === 'expiring' && !(r[8] >= 0 && r[8] <= 90)) return false;
      if (st === 'active' && !(r[8] > 90)) return false;
      return true;
    });
  }
  function contractsResp(sp) {
    var page = Math.max(parseInt(sp.get('page') || '1', 10) || 1, 1);
    var size = Math.min(Math.max(parseInt(sp.get('size') || '50', 10) || 50, 10), 200);
    var sk = C_SORT[sp.get('sort') || 'start'];
    if (sk == null) sk = 6;
    var dir = sp.get('dir') === 'asc' ? 1 : -1;
    var list = contractWhere(sp).slice();
    list.sort(function (a, b) {
      var x = a[sk], y = b[sk];
      if (x == null && y == null) return 0;
      if (x == null) return 1;
      if (y == null) return -1;
      if (x === y) return 0;
      return (x < y ? -1 : 1) * dir;
    });
    var cv = 0, pa = 0, expired = 0, expiring = 0, overrun = 0;
    list.forEach(function (r) {
      cv += (r[3] || 0);
      pa += (r[4] || 0);
      if (r[8] < 0) expired++;
      else if (r[8] <= 90) expiring++;
      if (r[5] != null && r[5] > 1) overrun++;
    });
    var types = {}, boundsMn = null, boundsMx = null;
    C.forEach(function (r) {
      var t = r[9] == null ? '' : r[9];
      types[t] = (types[t] || 0) + 1;
      if (r[6]) {
        if (boundsMn == null || r[6] < boundsMn) boundsMn = r[6];
        if (boundsMx == null || r[6] > boundsMx) boundsMx = r[6];
      }
    });
    var typeArr = Object.keys(types).map(function (k) { return { k: k, n: types[k] }; })
      .sort(function (a, b) { return b.n - a.n || (a.k < b.k ? -1 : 1); });
    var rowsOut = list.slice((page - 1) * size, page * size).map(function (r) {
      return {
        contract: r[0], supplier: r[1], cv: r[2], pa: r[4], usage: r[5],
        start: r[6], expiry: r[7], remain: r[8], type: r[9],
        supplier_en: r[10] || null
      };
    });
    return {
      total: list.length, page: page, size: size,
      summary: {
        n: list.length, cv: r2(cv), pa: r2(pa),
        usage: cv ? Math.round((pa / cv) * 10000) / 10000 : null,
        expired: expired, expiring: expiring, overrun: overrun
      },
      types: typeArr, bounds: { mn: boundsMn, mx: boundsMx },
      rows: rowsOut, spec: {}
    };
  }

  /* ---------- CSV ---------- */
  function csvCell(v) {
    var s = v == null ? '' : String(v);
    return /[",\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
  }
  function toCsv(header, body) {
    var out = [header.map(csvCell).join(',')];
    body.forEach(function (r) { out.push(r.map(csvCell).join(',')); });
    return out.join('\r\n');
  }
  function saveCsv(text, name) {
    var blob = new Blob(['\ufeff' + text], { type: 'text/csv;charset=utf-8' });
    var a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = name;
    document.body.appendChild(a);
    a.click();
    setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 1500);
  }
  function rowsCsv(sp) {
    var fy = sp.get('fy') || S.options.latestFy;
    var cur = (sp.get('cur') || 'RMB').toUpperCase() === 'RM' ? 'RM' : 'RMB';
    var rate = rateOf(cur);
    var sk = SORT_COL[sp.get('sort') || 'date'] || 'date';
    var dir = sp.get('dir') === 'asc' ? 1 : -1;
    var list = rowsWhere(sp, fy).slice();
    list.sort(function (a, b) {
      var x = a[sk], y = b[sk];
      if (x == null) return 1;
      if (y == null) return -1;
      if (x === y) return 0;
      return (x < y ? -1 : 1) * dir;
    });
    var body = list.map(function (r) {
      return [r.no, r.date, r.company, r.region, r.brand, r.dept, r.category,
        r.expense, r.method, r.supplier,
        r.amount == null ? '' : Math.round((r.amount / rate) * 100) / 100,
        r.days, r.budget, r.level];
    });
    return {
      text: toCsv(['No.', 'Date', 'Company', 'Region', 'Brand', 'Department',
        'Category', 'Expense Type', 'Procurement Method', 'Supplier',
        'Amount (' + cur + ')', 'Days', 'Budget Type', 'Supplier Category'], body),
      name: 'Procurement_Detail_' + fy + '_' + cur + '.csv'
    };
  }
  function contractsCsv(sp) {
    var cur = (sp.get('cur') || 'RMB').toUpperCase() === 'RM' ? 'RM' : 'RMB';
    var rate = rateOf(cur);
    var sk = C_SORT[sp.get('sort') || 'start'];
    if (sk == null) sk = 6;
    var dir = sp.get('dir') === 'asc' ? 1 : -1;
    var list = contractWhere(sp).slice();
    list.sort(function (a, b) {
      var x = a[sk], y = b[sk];
      if (x == null) return 1;
      if (y == null) return -1;
      if (x === y) return 0;
      return (x < y ? -1 : 1) * dir;
    });
    var body = list.map(function (r) {
      return [r[0], r[1],
        r[2] == null ? '' : Math.round((r[2] / rate) * 100) / 100,
        r[4] == null ? '' : Math.round((r[4] / rate) * 100) / 100,
        r[5] == null ? '' : Math.round(r[5] * 10000) / 100,
        r[6], r[7], r[8], r[9]];
    });
    return {
      text: toCsv(['Contract', 'Supplier', 'CV (' + cur + ')',
        'PA (' + cur + ')', 'Usage', 'Start Date', 'Expiry Date',
        'Remain Days', 'Type'], body),
      name: 'Framework_Agreements_' + cur + '.csv'
    };
  }

  window.__SNAP_DOWNLOAD__ = function (url) {
    var i = url.indexOf('?');
    var path = i < 0 ? url : url.slice(0, i);
    var sp = new URLSearchParams(i < 0 ? '' : url.slice(i + 1));
    var out = path.indexOf('contracts.csv') >= 0 ? contractsCsv(sp) : rowsCsv(sp);
    saveCsv(out.text, out.name);
  };

  /* ---------- /api/board/detail + detail/values（Detail 大表格） ----------
     与在线接口同口径：财年 + 全局筛选 + 每列搜索/取值/区间，三级树全量行 + 全集合计。
     离线已内嵌 46k 行（ROW_COLS 里有 region/company/category/supplier/method/
     amount/poPaid），所以这里只是本地复算，不需要额外数据。 */
  var DETAIL_KEYS = ['region', 'company', 'category', 'supplier', 'method', 'reason', 'note'];

  function detailPass(r, sp, fy, f, skip) {
    if (r.fy !== fy) return false;
    for (var k in f) if (f[k].indexOf(r[k]) < 0) return false;
    for (var i = 0; i < DETAIL_KEYS.length; i++) {
      var key = DETAIL_KEYS[i];
      if (key === skip) continue;
      var q = (sp.get('q_' + key) || '').toLowerCase();
      if (q && String(r[key] == null ? '' : r[key]).toLowerCase().indexOf(q) < 0) return false;
      var raw = sp.get('f_' + key);
      if (raw) {
        var arr = null;
        try { arr = JSON.parse(raw); } catch (e) { arr = null; }
        if (arr && arr.length && arr.indexOf(String(r[key] == null ? '' : r[key])) < 0) return false;
      }
    }
    var mn = sp.get('min_amount'), mx = sp.get('max_amount');
    if (mn && Number(r.amount || 0) < Number(mn)) return false;
    if (mx && Number(r.amount || 0) > Number(mx)) return false;
    mn = sp.get('min_paid'); mx = sp.get('max_paid');
    var paid = r.poPaid || 0;
    if (mn && Number(paid) < Number(mn)) return false;
    if (mx && Number(paid) > Number(mx)) return false;
    return true;
  }

  function detailResp(sp) {
    var fy = sp.get('fy') || S.options.latestFy;
    var f = readFilters(sp);
    var cur = (sp.get('cur') || 'RMB').toUpperCase();
    var rate = cur === 'RM' ? 1.5 : 1;
    var list = rows.filter(function (r) { return detailPass(r, sp, fy, f, null); });
    var sa = 0, sp_ = 0;
    list.forEach(function (r) { sa += r.amount || 0; sp_ += r.poPaid || 0; });
    var field = { region: 'region', company: 'company', category: 'category',
                  supplier: 'supplier', method: 'method', note: 'note',
                  amount: 'amount', paid: 'poPaid' };
    var sk = field[sp.get('sort') || 'amount'] || 'date';
    var dir = sp.get('dir') === 'asc' ? 1 : -1;
    list.sort(function (a, b) {
      var x = a[sk], y = b[sk];
      if (x == null) x = ''; if (y == null) y = '';
      if (typeof x === 'number' && typeof y === 'number') return (x - y) * dir;
      return String(x).localeCompare(String(y), 'zh') * dir;
    });
    var out = list.map(function (r) {
      return {
        region: r.region || '', company: r.company || '', category: r.category || '',
        supplier: r.supplier || '', method: r.method || '', note: r.note || '',
        amount: r2((r.amount || 0) / rate), paid: r2((r.poPaid || 0) / rate)
      };
    });
    var rmap = {}, cmap = {};
    list.forEach(function (r) {
      var rk = r.region || '', k = r.company || '', ck = rk + '\u0001' + k, g = rmap[rk], h = cmap[ck];
      if (!g) g = rmap[rk] = { region: rk, n: 0, amount: 0, paid: 0 };
      if (!h) h = cmap[ck] = { region: rk, company: k, n: 0, amount: 0, paid: 0 };
      g.n++; g.amount += r.amount || 0; g.paid += r.poPaid || 0;
      h.n++; h.amount += r.amount || 0; h.paid += r.poPaid || 0;
    });
    var rt = Object.keys(rmap).map(function (k) {
      var g = rmap[k];
      return { region: g.region, n: g.n, amount: r2(g.amount / rate), paid: r2(g.paid / rate) };
    }).sort(function (a, b) { return b.n - a.n; });
    var ct = Object.keys(cmap).map(function (k) {
      var h = cmap[k];
      return { region: h.region, company: h.company, n: h.n,
               amount: r2(h.amount / rate), paid: r2(h.paid / rate) };
    }).sort(function (a, b) { return b.n - a.n; });
    return { rows: out, total: list.length,
             sums: { amount: r2(sa / rate), paid: r2(sp_ / rate) },
             regionTotals: rt, companyTotals: ct,
             fy: fy, cur: cur, rate: rate,
             columns: DETAIL_KEYS.concat(['amount', 'paid']) };
  }

  /* GET /api/board/method-drill?method=SS-Exempt（采购方式饼图第二层） */
  function methodDrillResp(sp) {
    var mk = sp.get('method') || '';
    var fy = sp.get('fy') || S.options.latestFy;
    var f = readFilters(sp);
    var cur = (sp.get('cur') || 'RMB').toUpperCase();
    var rate = cur === 'RM' ? 1.5 : 1;
    var list = rows.filter(function (r) { return r.fy === fy && pass(r, f); });
    var all = methodDrillOf(list);
    if (!all[mk]) return { error: 'unknown method' };
    var o = all[mk];
    return { method: mk, reason: o.reason, rows: o.rows, total: o.total,
             fy: fy, cur: cur, rate: rate };
  }

  function detailValuesResp(sp) {
    var col = sp.get('col');
    if (DETAIL_KEYS.indexOf(col) < 0) return [];
    var fy = sp.get('fy') || S.options.latestFy;
    var f = readFilters(sp);
    var q = (sp.get('q') || '').toLowerCase();
    var cnt = {};
    rows.forEach(function (r) {
      if (!detailPass(r, sp, fy, f, col)) return;
      var v = r[col] == null ? '' : String(r[col]);
      if (q && v.toLowerCase().indexOf(q) < 0) return;
      cnt[v] = (cnt[v] || 0) + 1;
    });
    return Object.keys(cnt).map(function (v) { return { v: v, n: cnt[v] }; })
      .sort(function (a, b) { return b.n - a.n || a.v.localeCompare(b.v, 'zh'); })
      .slice(0, 200);
  }

  /* GET /api/board/geo：地图气泡 + Category 同比（离线按行复算） */
  var GEO_LABELS = { prpo: 'PRPO Spend', paid: 'PO Paid',
                     micro: 'Micro Purchase', inadequate: 'SS - Inadequate Competition' };
  function geoVal(r, metric) {
    if (metric === 'paid') return r.poPaid || 0;
    if (metric === 'micro') return r.method === 'Micro' ? (r.amount || 0) : 0;
    if (metric === 'inadequate') return r.ssr === 'Inadequate Competition' ? (r.amount || 0) : 0;
    return r.amount || 0;
  }
  function geoAgg(list, metric, keyName) {
    var m = {}, meta = {}, loc = S.geoLoc || {};
    list.forEach(function (r) {
      var k = r[keyName] || 'Others';
      m[k] = (m[k] || 0) + geoVal(r, metric);
      if (keyName === 'company' && !meta[k]) meta[k] = loc[k] || {};
    });
    return { sums: m, meta: meta };
  }
  function geoRows(a, b, key, meta) {
    var keys = Object.keys(a).concat(Object.keys(b).filter(function (k) { return !(k in a); }));
    keys.sort(function (x, y) { return (a[y] || 0) - (a[x] || 0); });
    return keys.map(function (k) {
      var v = a[k] || 0, pv = b[k] || 0, o = {};
      o[key] = k; o.v = r2(v); o.pv = r2(pv);
      o.yoy = pv ? r2((v - pv) / pv) : null;
      if (meta) {
        var m = meta[k] || {};
        o.lng = m.lng; o.lat = m.lat; o.color = m.color;
        o.region = m.region; o.city = m.city;
      }
      return o;
    });
  }
  function geoResp(sp) {
    var metric = sp.get('metric') || 'prpo';
    if (!GEO_LABELS[metric]) metric = 'prpo';
    var fy = sp.get('fy') || S.options.latestFy, pfy = prevOf(fy);
    var cur = (sp.get('cur') || 'RMB').toUpperCase(), f = readFilters(sp);
    var list = pick(fy, f), plist = pfy ? pick(pfy, f) : [];
    var ca = geoAgg(list, metric, 'category').sums,
        cp = geoAgg(plist, metric, 'category').sums;
    var co = geoAgg(list, metric, 'company'), po = geoAgg(plist, metric, 'company').sums;
    var categories = geoRows(ca, cp, 'cat');
    var companies = geoRows(co.sums, po, 'company', co.meta);
    var cityV = {}, cityPV = {}, cityMeta = {};
    companies.forEach(function (r) {
      if (!r.city) return;
      if (r.v >= (cityV[r.city] == null ? -1 : cityV[r.city])) {
        cityV[r.city] = r.v; cityPV[r.city] = r.pv || 0;
        cityMeta[r.city] = { lng: r.lng, lat: r.lat, color: r.color, region: r.region };
      }
    });
    var bubbles = Object.keys(cityV).sort(function (a, b) {
      return cityV[b] - cityV[a];
    }).map(function (k) {
      var o = { city: k, v: cityV[k], pv: cityPV[k] || 0,
                yoy: cityPV[k] ? r2((cityV[k] - cityPV[k]) / cityPV[k]) : null };
      var m = cityMeta[k] || {};
      o.lng = m.lng; o.lat = m.lat; o.color = m.color; o.region = m.region;
      return o;
    });
    var tv = 0, pv = 0;
    Object.keys(ca).forEach(function (k) { tv += ca[k]; });
    Object.keys(cp).forEach(function (k) { pv += cp[k]; });
    return { metric: metric, label: GEO_LABELS[metric], fy: fy, pyFy: pfy,
             cur: cur, rate: rateOf(cur), fys: S.options.fy || [],
             total: { v: r2(tv), pv: r2(pv), yoy: pv ? r2((tv - pv) / pv) : null },
             categories: categories, companies: companies, bubbles: bubbles };
  }

  /* ---------- fetch 垫片 ---------- */
  var _fetch = window.fetch ? window.fetch.bind(window) : null;
  var PREFIX = '/api/board/';

  window.fetch = function (url, opt) {
    var u = String(url || '');
    var at = u.indexOf(PREFIX);
    if (at < 0) return _fetch ? _fetch(url, opt) : Promise.reject(new Error('offline'));
    var rest = u.slice(at + PREFIX.length);
    var qi = rest.indexOf('?');
    var path = qi < 0 ? rest : rest.slice(0, qi);
    var sp = new URLSearchParams(qi < 0 ? '' : rest.slice(qi + 1));
    var out;
    if (path === 'options') out = S.options;
    else if (path === 'data') out = dataResp(sp);
    else if (path === 'top') out = topResp(sp);
    else if (path === 'rows') out = rowsResp(sp);
    else if (path === 'contracts') out = contractsResp(sp);
    else if (path === 'detail') out = detailResp(sp);
    else if (path === 'detail/values') out = detailValuesResp(sp);
    else if (path === 'method-drill') out = methodDrillResp(sp);
    else if (path === 'geo') out = geoResp(sp);
    else out = {};
    return Promise.resolve(new Response(JSON.stringify(out), {
      status: 200, headers: { 'Content-Type': 'application/json' }
    }));
  };
})();
