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

  var FILTER_KEYS = ['brand', 'category', 'expense', 'level', 'region',
                     'company', 'method', 'supplier'];

  function readFilters(sp) {
    var o = {};
    FILTER_KEYS.forEach(function (k) {
      var v = sp.getAll(k).filter(function (x) { return x !== '' && x != null; });
      if (v.length) o[k] = v;
    });
    return o;
  }
  function pass(r, f) {
    for (var k in f) if (f[k].indexOf(r[k]) < 0) return false;
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
        return { k: k, vals: series[k].map(function (v) { return r2(v); }) };
      }),
      total: tot,
      frame: frame.map(function (v) { return r2(v); }),
      cur: tot
    };
  }

  function paretoOf(list) {
    var m = {}, total = 0, sups = {};
    list.forEach(function (r) {
      var k = r.category == null || r.category === '' ? 'Unassigned' : r.category;
      if (!m[k]) { m[k] = { k: k, v: 0, n: 0 }; sups[k] = {}; }
      m[k].v += (r.amount || 0);
      if (r.no != null) m[k].n++;
      if (r.supplier != null) sups[k][r.supplier] = 1;
      total += (r.amount || 0);
    });
    var out = Object.keys(m).map(function (k) { return m[k]; })
      .sort(function (a, b) { return b.v - a.v || (a.k < b.k ? -1 : 1); });
    var acc = 0;
    out.forEach(function (r) {
      acc += r.v;
      r.sup = Object.keys(sups[r.k] || {}).length;
      r.cum = total ? Math.round((acc / total) * 10000) / 10000 : null;
      r.share = total ? Math.round((r.v / total) * 10000) / 10000 : null;
      r.v = r2(r.v);
    });
    return { rows: out, total: r2(total) };
  }

  function donutOf(list) {
    var m = {}, total = 0;
    list.forEach(function (r) {
      var k = r.expense == null || r.expense === '' ? 'Unclassified' : r.expense;
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
      ['txn', 'PR Count', 'int', k0.txn, kp.txn, null],
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
    var top = topOf(list, 10);
    var cats = pareto.rows.slice(0, 8).map(function (r) { return r.k; });
    var heat = heatOf(list, top.map(function (r) { return r.k; }), cats);
    var rg = dateRange(list);

    return {
      fy: fy, prevFy: prevFy, cur: cur, rate: rate,
      range: rg,
      status: { txn: k0.txn, suppliers: k0.suppliers, stores: k0.stores },
      kpi: kpi, monthly: monthly, pareto: pareto, donut: donut,
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
  function rowsWhere(sp, fy) {
    var f = readFilters(sp);
    var q = (sp.get('q') || '').trim().toLowerCase();
    var out = rows.filter(function (r) {
      if (r.fy !== fy) return false;
      if (!pass(r, f)) return false;
      if (!q) return true;
      var hay = [r.no, r.supplier, r.company, r.category, r.dept, r.brand];
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
    var list = rowsWhere(sp, fy).slice();
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
        days: r.days, budget: r.budget, level: r.level
      };
    });
    return { total: list.length, page: page, size: size, amount: r2(amount), rows: slice };
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
    else out = {};
    return Promise.resolve(new Response(JSON.stringify(out), {
      status: 200, headers: { 'Content-Type': 'application/json' }
    }));
  };
})();
