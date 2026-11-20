/* 映射聚合页（简化版）无头验证：加载映射 / 手动改 / 搜索 / 导出 / 导入 / 保存
 * 跑法：
 *   1) python tests/_agg_server.py        # 5001 + 库副本，不碰真实库
 *   2) node tests/agg_check.js
 */
const puppeteer = require("C:/Users/CONG ZHU/.workbuddy/binaries/node/workspace/node_modules/puppeteer-core");
const fs = require("fs");
const os = require("os");
const path = require("path");
const sleep = ms => new Promise(r => setTimeout(r, ms));
const BASE = process.env.AGG_BASE || "http://127.0.0.1:5001";

const TABLE = "fct_prpo";
const COL = "Table - 采购子类别";
const RAW_A = "Insurance services - external-保险服务-外部";
const RAW_B = "Logistics - 物流服务";          // 若取值名不同，导入会被忽略（断言会体现）
const IMPORT_VAL = "UI导入聚合值";

let ok = 0, bad = 0;
function chk(name, cond, extra) {
  if (cond) { ok++; console.log("  [OK] " + name); }
  else { bad++; console.log("  [XX] " + name + (extra !== undefined ? " -> " + extra : "")); }
}

(async () => {
  const b = await puppeteer.launch({
    executablePath: "C:/Program Files/Google/Chrome/Application/chrome.exe",
    headless: "new", args: ["--no-sandbox"], defaultViewport: { width: 1680, height: 1100 }
  });
  const p = await b.newPage();
  const errs = [];
  p.on("pageerror", e => errs.push(e.message));
  p.on("console", m => { if (m.type() === "error") errs.push("console:" + m.text()); });
  p.on("dialog", async d => { await d.accept(); });

  const txt = sel => p.$eval(sel, e => e.innerText.replace(/\s+/g, " ")).catch(() => "");
  const waitFor = async (fn, ms = 20000, step = 120) => {
    const t0 = Date.now();
    while (Date.now() - t0 < ms) { if (await fn()) return true; await sleep(step); }
    return false;
  };
  const loadMapping = async () => {
    await p.evaluate(() => {
      [...document.querySelectorAll(".ag-bar .btn")].find(x => x.textContent.includes("加载映射")).click();
    });
  };

  console.log("=== 1. 页面已简化 ===");
  await p.goto(BASE + "/#agg", { waitUntil: "networkidle2" });
  await p.waitForSelector("#agTable", { timeout: 20000 });
  chk("没有标签栏", (await p.$$eval(".ag-tab", e => e.length)) === 0);
  chk("没有任务列表", (await p.$("#agTasks")) === null);
  chk("没有模式/阈值/重新整合/批量选择",
      (await txt("#agBody")).indexOf("重新整合") < 0 &&
      (await txt("#agBody")).indexOf("全选本页") < 0 &&
      (await p.$("#agBatch")) === null);
  chk("没有质量检查标签", (await txt(".q-page")).indexOf("质量检查") < 0);
  chk("保留表/字段下拉与加载按钮",
      (await p.$("#agTable")) !== null && (await p.$("#agCol")) !== null &&
      (await txt(".ag-bar")).includes("加载映射"));
  const nOpt = await p.$$eval("#agTable option", e => e.length);
  chk("表下拉有候选", nOpt >= 8, String(nOpt));

  console.log("=== 2. 加载字段映射 ===");
  await p.select("#agTable", TABLE);
  const cols = await p.$$eval("#agCol option", e => e.map(x => x.value));
  chk("字段下拉联动", cols.includes(COL), cols.slice(0, 4).join(","));
  await p.select("#agCol", COL);
  await loadMapping();
  const loaded = await waitFor(async () => (await p.$$eval(".ag-table tbody tr", e => e.length)) > 0);
  chk("出现映射表", loaded);
  const sum1 = await txt("#agSum");
  chk("摘要：原始取值 244 个", sum1.includes("244"), sum1);
  chk("摘要：聚合后 144 个值", sum1.includes("144"), sum1);
  const rowsShown = await p.$$eval(".ag-table tbody tr", e => e.length);
  chk("每页 150 行", rowsShown === 150, String(rowsShown));
  chk("分页信息", (await txt(".ag-pager")).includes("第 1 / 2 页"), await txt(".ag-pager"));
  chk("表格只有 3 列（原始值/行数/聚合值）",
      (await p.$$eval(".ag-table thead th", e => e.length)) === 3);
  chk("提示尚未保存映射（新建态）",
      (await txt("#agSaveHint")).includes("新建"), await txt("#agSaveHint"));

  console.log("=== 3. 手动改聚合值 ===");
  await p.evaluate(() => {
    const inp = document.querySelector(".ag-table tbody tr input[type=text]");
    inp.value = "UI手工聚合值";
    inp.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await sleep(150);
  chk("摘要提示已改、尚未保存", (await txt("#agSum")).includes("尚未保存"), await txt("#agSum"));
  chk("行标记为已合并",
      await p.$eval(".ag-table tbody tr", e => e.classList.contains("merged")));
  chk("首行输入值已更新",
      (await p.$eval(".ag-table tbody tr input[type=text]", e => e.value)) === "UI手工聚合值");

  console.log("=== 4. 搜索过滤 ===");
  await p.type("#agSearch", "Insurance");
  await sleep(200);
  const filtered = await p.$$eval(".ag-table tbody tr", e => e.length);
  chk("搜索后行数减少", filtered > 0 && filtered < 150, String(filtered));
  chk("搜索结果包含关键词",
      (await txt(".ag-table tbody")).includes("Insurance"));
  await p.evaluate(() => {
    const s = document.getElementById("agSearch");
    s.value = ""; s.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await sleep(150);

  console.log("=== 5. 导出 CSV（接口拿到的是屏幕上这份映射）===");
  const exp = await p.evaluate(async () => {
    const r = await fetch("/api/agg/export", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ table: agg.table, col: agg.col, fmt: "csv",
        rows: agg.rows.map(x => ({ raw: x.raw, agg: x.agg, rows: +x.rows || 0 })) })
    });
    const t = await r.text();
    return { status: r.status, head: t.replace(/^\uFEFF/, "").slice(0, 60),
             hasEdit: t.includes("UI手工聚合值") };
  });
  chk("导出 200", exp.status === 200, String(exp.status));
  chk("表头只有 原始值/聚合值（不含行数）", exp.head.startsWith("原始值,聚合值"), exp.head);
  chk("导出内容含刚才手改的值", exp.hasEdit);

  console.log("=== 6. 导入映射文件（两列，即导出的格式）===");
  const file = path.join(os.tmpdir(), "agg_import_ui.csv");
  fs.writeFileSync(file, "\ufeff" + "原始值,聚合值\n"
    + `"${RAW_A}",${IMPORT_VAL}\n`, "utf8");
  const input = await p.$("#agFile");
  await input.uploadFile(file);
  const imported = await waitFor(async () => (await txt("#agEdit")).includes("已从"), 15000);
  chk("出现导入提示条", imported, (await txt("#agEdit")).slice(0, 160));
  chk("提示还没写进数据库", (await txt("#agEdit")).includes("还没写进数据库"));
  const rowA = await p.evaluate(raw => {
    const i = agg.rows.findIndex(r => r.raw === raw);
    return i < 0 ? null : agg.rows[i].agg;
  }, RAW_A);
  chk("文件里的聚合值已生效", rowA === IMPORT_VAL, String(rowA));

  console.log("=== 6.5 自定义表头导入：第一列原始值 + 后面按表头建附加字段 ===");
  const twoRaws = await p.evaluate(rawA => agg.rows
    .filter(r => r.raw !== rawA).slice(0, 2).map(r => r.raw), RAW_A);
  const beforeAgg = await p.evaluate(rows => rows.map(r => {
    const hit = agg.rows.find(x => x.raw === r);
    return hit ? hit.agg : null;
  }), twoRaws);
  const file2 = path.join(os.tmpdir(), "agg_import_extra.csv");
  fs.writeFileSync(file2, "\ufeff" + "子类别cn,子类别en\n"
    + `"${twoRaws[0]}",SubA\n"${twoRaws[1]}",SubB\n`, "utf8");
  await (await p.$("#agFile")).uploadFile(file2);
  const imported2 = await waitFor(async () => (await txt("#agEdit")).includes("附加字段"), 15000);
  chk("导入提示里说明建了附加字段", imported2, (await txt("#agEdit")).slice(0, 200));
  chk("附加字段名取自表头",
      JSON.stringify(await p.evaluate(() => agg.extraCols)) === JSON.stringify(["子类别en"]),
      JSON.stringify(await p.evaluate(() => agg.extraCols)));
  chk("表格多出 1 列（原始值/行数/聚合值/子类别en）",
      (await p.$$eval(".ag-table thead th", e => e.length)) === 4,
      String(await p.$$eval(".ag-table thead th", e => e.length)));
  chk("表头显示附加字段名", (await txt(".ag-table thead")).includes("子类别en"),
      await txt(".ag-table thead"));
  const exVal = await p.evaluate(rows => {
    const hit = agg.rows.find(r => r.raw === rows[0]);
    return hit && hit.extra ? hit.extra["子类别en"] : null;
  }, twoRaws);
  chk("附加字段值随行载入", exVal === "SubA", String(exVal));
  chk("没有「聚合值」列时不动现有聚合值",
      await p.evaluate((rows, before) => rows.every((r, i) => {
        const hit = agg.rows.find(x => x.raw === r);
        return !!hit && hit.agg === before[i];
      }), twoRaws, beforeAgg),
      JSON.stringify(await p.evaluate(rows => rows.map(r => {
        const hit = agg.rows.find(x => x.raw === r);
        return hit ? hit.agg : null;
      }), twoRaws)));

  console.log("=== 7. 保存映射（新建维度表）===");
  await p.click("#agSaveBtn");
  const saved = await waitFor(async () => (await txt("#agHint")).includes("已加载"), 30000);
  chk("保存后转为已保存态", saved, await txt("#agHint"));
  const task = await p.evaluate(async () => {
    const d = await fetch("/api/agg/tasks").then(r => r.json());
    return (d.tasks || []).find(t => t.source_table === "fct_prpo" && t.source_col === "Table - 采购子类别") || null;
  });
  chk("后端有该字段的聚合任务", !!task, JSON.stringify(task && task.dim_table));
  const dimCols = task ? await p.evaluate(async t => {
    const d = await fetch("/api/tables/" + encodeURIComponent(t) + "/schema").then(r => r.json());
    return (Array.isArray(d) ? d : []).map(c => c.name);
  }, task.dim_table) : [];
  chk("维度表 = 原始值 + 聚合值 + 导入的附加字段（无源行数/写法数）",
      dimCols.length === 4 && dimCols[1] === "原始值"
      && dimCols.includes("子类别en")
      && !dimCols.includes("源行数") && !dimCols.includes("写法数"),
      JSON.stringify(dimCols));
  chk("提示文案切到「整体替换」",
      (await txt("#agSaveHint")).includes("整体替换"), await txt("#agSaveHint"));
  const banner = await waitFor(
    async () => (await txt("#agEdit .ag-banner.ok")).includes("已生成"), 15000);
  chk("保存成功横幅保留", banner, (await txt("#agEdit")).slice(0, 120));

  console.log("=== 8. 重新打开页面：映射来自库，不是重算 ===");
  await p.goto(BASE + "/#agg", { waitUntil: "networkidle2" });
  await p.waitForSelector("#agTable", { timeout: 20000 });
  await p.select("#agTable", TABLE);
  await p.select("#agCol", COL);
  await loadMapping();
  await waitFor(async () => (await txt("#agHint")).includes("已加载"), 20000);
  const keep = await p.evaluate(raw => {
    const i = agg.rows.findIndex(r => r.raw === raw);
    return i < 0 ? null : agg.rows[i].agg;
  }, RAW_A);
  chk("刷新后仍是保存的映射值", keep === IMPORT_VAL, String(keep));
  chk("提示已加载维度表", (await txt("#agHint")).includes("已加载"), await txt("#agHint"));
  const keepEx = await p.evaluate(rows => {
    const hit = agg.rows.find(r => r.raw === rows[0]);
    return { cols: agg.extraCols, v: hit && hit.extra ? hit.extra["子类别en"] : null };
  }, twoRaws);
  chk("刷新后附加字段列仍在", JSON.stringify(keepEx.cols) === JSON.stringify(["子类别en"]),
      JSON.stringify(keepEx.cols));
  chk("刷新后附加字段值仍在", keepEx.v === "SubA", String(keepEx.v));
  chk("刷新后表格列数不变", (await p.$$eval(".ag-table thead th", e => e.length)) === 4);

  console.log("=== 9. 维度表被删后保存能自愈（不是报错卡死）===");
  // 模拟用户在表格页把维度表删掉，聚合页还缓存着旧 task
  const delSt = await p.evaluate(async t => {
    const r = await fetch("/api/tables/" + encodeURIComponent(t), {
      method: "DELETE", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ confirm: t, backup: false }) });
    return r.status;
  }, task.dim_table);
  chk("维度表已删除（模拟）", delSt === 200, String(delSt));
  await p.click("#agSaveBtn");
  // 自愈成功 = 出现了新的任务 id（旧任务随表一起没了）
  const healed = await waitFor(async () => p.evaluate(async oldId => {
    const d = await fetch("/api/agg/tasks").then(r => r.json());
    const t = (d.tasks || []).find(x => x.source_col === "Table - 采购子类别");
    return !!t && t.id !== oldId;
  }, task.id), 40000);
  chk("删表后保存自动重建成功", healed, await txt("#agHint"));
  const rebuilt = await p.evaluate(async col => {
    const d = await fetch("/api/agg/tasks").then(r => r.json());
    const t = (d.tasks || []).find(x => x.source_col === col);
    if (!t) return null;
    const cols = await fetch("/api/tables/" + encodeURIComponent(t.dim_table) + "/schema")
      .then(r => r.json());
    return { dim: t.dim_table, cols: (Array.isArray(cols) ? cols : []).map(c => c.name) };
  }, COL);
  chk("重建后的维度表结构一致（含附加字段、无统计列）",
      !!rebuilt && rebuilt.cols.length === 4 && rebuilt.cols.includes("子类别en")
      && !rebuilt.cols.includes("源行数"),
      JSON.stringify(rebuilt));

  console.log("=== 10. 清理 + 无脚本错误 ===");
  await p.evaluate(async () => {
    const d = await fetch("/api/agg/tasks").then(r => r.json());
    const t = (d.tasks || []).find(x => x.source_col === "Table - 采购子类别");
    if (t) await fetch("/api/agg/task/" + t.id + "?backup=0", { method: "DELETE" });
  });
  const gone = await p.evaluate(async () => {
    const d = await fetch("/api/agg/tasks").then(r => r.json());
    return (d.tasks || []).some(t => t.dim_table === "dim_agg_ui_test");
  });
  chk("测试用的聚合任务已删除", gone === false);
  // 自愈时先发一次 PUT（旧任务已随表删除 → 404），这条 404 是预期响应，不算脚本异常
  const realErrs = errs.filter(e => !/\b404\b/.test(e));
  chk("页面无 JS 异常（自愈路径的 404 属预期）", realErrs.length === 0,
      realErrs.slice(0, 3).join(" | "));

  await p.screenshot({ path: path.join(os.tmpdir(), "dbadmin-agg.png"), fullPage: false });
  await b.close();
  console.log(`\n结果：${ok} 通过 / ${bad} 失败`);
  process.exit(bad ? 1 : 0);
})();
