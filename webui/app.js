/* 造境 AI 设计智能体控制台 · 零构建原生 JS
 * §2 红线：所有面板只调用本服务真实端点并原样呈现结果与错误；不伪造、不兜底假数据。
 * 端点缺失/失败一律如实展示 HTTP 状态与服务端 detail。 */
"use strict";

const API = "/api/v1";
const $ = (id) => document.getElementById(id);

function esc(s) {
  return String(s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

async function api(path, opts = {}) {
  const url = API + path;
  /* 失败对象自带 retry 闭包：错误态渲染的「重试」按钮复跑同一次请求，
     不需要 93 个调用点各自记住参数（P74A）。 */
  const retry = () => api(path, opts);
  let resp;
  try {
    resp = await fetch(url, Object.assign({
      headers: { "Content-Type": "application/json" },
    }, opts));
  } catch (e) {
    return { ok: false, status: 0, detail: "网络错误：无法连接本服务 — " + e, retry };
  }
  const text = await resp.text();
  let json = null;
  try { json = text ? JSON.parse(text) : null; } catch (_) { /* 非 JSON 原样保留 */ }
  if (!resp.ok) {
    const detail = json && json.detail ? (typeof json.detail === "string" ? json.detail : JSON.stringify(json.detail, null, 2))
      : (text || resp.statusText);
    return { ok: false, status: resp.status, detail, json, retry };
  }
  return { ok: true, status: resp.status, json, text };
}

const Post = (path, body) => api(path, { method: "POST", body: JSON.stringify(body) });

function rawBlock(obj) {
  return `<details class="raw"><summary>原始响应 JSON</summary><pre>${esc(JSON.stringify(obj, null, 2))}</pre></details>`;
}
function errBox(r) {
  return `<div class="err-box">HTTP ${r.status} · ${esc(r.detail)}</div>`;
}
/* 加载骨架（P74A）：新一轮开始先清掉上一轮读数，避免旧结果被当成新结果读；
   aria-live 让读屏器听到「有回执了」，aria-busy 表达「还在途」。 */
function skelBlock() {
  return `<div class="skel" aria-hidden="true"><i></i><i></i><i></i></div>`;
}
async function renderOut(elId, r, viewer) {
  const box = $(elId);
  if (!box) return;
  if (!box.hasAttribute("aria-live")) box.setAttribute("aria-live", "polite");
  box.setAttribute("aria-busy", "true");
  box.classList.add("is-busy");
  box.innerHTML = skelBlock();
  let res;
  try {
    res = await r;   // 允许直接传 fetch Promise
  } catch (e) {
    res = { ok: false, status: 0, detail: "请求异常：" + e };
  }
  box.setAttribute("aria-busy", "false");
  box.classList.remove("is-busy");
  if (!res.ok) {
    const canRetry = typeof res.retry === "function";
    box.innerHTML = errBox(res) + (canRetry
      ? `<button class="ghost" type="button" data-retry>重试</button>` : "");
    const b = canRetry ? box.querySelector("[data-retry]") : null;
    if (b) b.addEventListener("click", () => { renderOut(elId, res.retry(), viewer); });
    return;
  }
  box.innerHTML = viewer(res.json) + rawBlock(res.json);
}
function kv(pairs) {
  return `<dl class="kv">` + pairs.filter(([, v]) => v !== undefined).map(([k, v]) =>
    `<dt>${esc(k)}</dt><dd>${esc(typeof v === "object" ? JSON.stringify(v) : String(v))}</dd>`).join("") + `</dl>`;
}
function badge(text, cls) { return `<span class="badge ${cls}">${esc(text)}</span>`; }
const HEX_RE = /#(?:[0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})\b/g;
function hexChips(obj) {
  const seen = new Set();
  (JSON.stringify(obj).match(HEX_RE) || []).forEach((h) => seen.add(h.toUpperCase()));
  const list = [...seen].slice(0, 24);
  if (!list.length) return "";
  return `<div class="chips">` + list.map((h) =>
    `<span class="chip"><i class="dot" style="background:${h}"></i>${h}</span>`).join("") + `</div>`;
}
function monoTable(rows, cols) {
  if (!rows || !rows.length) return `<div class="muted small">（空结果）</div>`;
  const head = cols.map((c) => `<th>${esc(c[0])}</th>`).join("");
  const body = rows.map((r) => `<tr>` + cols.map(([k, f]) =>
    `<td class="${typeof f(r[k]) === "number" ? "num" : ""}">${esc(f(r[k]))}</td>`).join("") + `</tr>`).join("");
  return `<table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
}
function asText(v) {
  if (v === null || v === undefined) return "—";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

/* ============================ 面板切换 ============================ */
const loaded = {};
document.querySelectorAll("#nav button").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll("#nav button").forEach((b) => b.classList.remove("active"));
    document.querySelectorAll("main .panel").forEach((p) => p.classList.remove("active"));
    btn.classList.add("active");
    const panel = $("panel-" + btn.dataset.panel);
    panel.classList.add("active");
    /* P74A：主区是独立滚动容器，切面板必须回顶（否则停在上一面板的半截处），
       并把焦点交给本面板标题，读屏器才会从新面板开头念起。 */
    const mainEl = document.querySelector("main");
    if (mainEl) mainEl.scrollTop = 0;
    const head = panel.querySelector("h2");
    if (head) { head.setAttribute("tabindex", "-1"); head.focus({ preventScroll: true }); }
    const key = btn.dataset.panel;
    if (!loaded[key]) { loaded[key] = true; (PANEL_INIT[key] || (() => {}))(); }
  });
});
/* 面板初次激活时的加载动作 */
const PANEL_INIT = {
  overview: initOverview,
  assets: refreshAssets,
  knowledge: refreshKnowledge,
  brand: refreshBrandBoundary,
};

/* P74A · WCAG 2.2 · 2.4.11（焦点不被遮挡）：≤900px 时 aside 收成可折行的吸顶条，
   高度随窗口宽度变化 ⇒ 用实测 offsetHeight 写 scroll-padding-top，不写死断点。 */
function syncStickyPad() {
  const bar = document.querySelector("aside");
  const stacked = getComputedStyle(document.body).flexDirection === "column";
  document.documentElement.style.scrollPaddingTop =
    (bar && stacked) ? (bar.offsetHeight + 10) + "px" : "0px";
}
window.addEventListener("resize", syncStickyPad);
syncStickyPad();

/* ============================ 总览 ============================ */
async function initOverview() {
  const [h, cap, lic] = await Promise.all([
    api("/health"), api("/capabilities"), api("/license"),
  ]);
  if (h.ok) {
    const j = h.json;
    $("ov-health").innerHTML = kv([
      ["状态", j.status || "ok"], ["版本", j.version],
      ["冻结 EXE", j.frozen ? "是" : "否（开发/脚本模式）"],
      ["数据根", j.data_root], ["运行时长", fmtUptime(j.uptime_seconds)],
    ]);
    const eng = j.engines || {};
    const names = Object.keys(eng).sort();
    $("ov-engines").innerHTML = names.map((n) => {
      const ready = eng[n] === "ready";
      return `<span class="chip"><i class="dot" style="background:${ready ? "var(--ok)" : "var(--err)"}"></i>${esc(n)} · ${esc(eng[n])}</span>`;
    }).join("") || `<span class="muted">未上报</span>`;
    $("runtime-status").innerHTML =
      `<b>${names.filter((n) => eng[n] === "ready").length}/${names.length}</b> 引擎 ready · ` +
      `${j.frozen ? "冻结 EXE" : "脚本模式"} · v${esc(j.version || "")}`;
  } else {
    $("ov-health").innerHTML = errBox(h);
    $("runtime-status").innerHTML = `<b>—</b> 服务不可达 · 引擎分母不编造`;
  }
  if (lic.ok) {
    const j = lic.json;
    $("ov-license").innerHTML = kv([
      ["状态", j.status], ["版本", j.edition], ["客户", j.customer],
      ["席位", j.seats], ["机器指纹", j.machine_id],
      ["到期", j.expires_at || "—"],
    ]) + rawBlock(j);
  } else { $("ov-license").innerHTML = errBox(lic); }

  if (cap.ok) {
    const raw = (cap.json.registry && cap.json.registry.capabilities) || [];
    const reg = Array.isArray(raw)
      ? Object.fromEntries(raw.map((c) => [c.id, c]))
      : raw;   // 真实响应为列表（逐项带 id），兼容字典形态
    const ids = Object.keys(reg).sort();
    const rows = ids.map((id) => {
      const c = reg[id];
      const vb = c.verified ? badge("verified", "ok") :
        (c.evidence_kind === "not_evaluated" ? badge("未评测", "mut") : badge("未验证", "warn"));
      return `<tr><td>${esc(id)}</td><td>${esc(c.engine || "—")}</td>
        <td class="num">${esc(asText(c.test_count))}</td>
        <td>${esc(c.evidence_kind || "—")}</td><td>${vb}</td>
        <td class="small muted">${esc(c.note || "")}</td></tr>`;
    });
    $("ov-registry").innerHTML =
      `<table><thead><tr><th>能力</th><th>引擎</th><th>黄金用例数</th><th>证据类型</th><th>验证</th><th>说明</th></tr></thead>` +
      `<tbody>${rows.join("")}</tbody></table>` +
      `<div class="small muted" style="margin-top:6px">共 ${ids.length} 项 · verified ${ids.filter((i) => reg[i].verified).length} 项（逐项证据实时派生自磁盘评测报告）</div>`;
    wiringReport(reg);
  } else { $("ov-registry").innerHTML = errBox(cap); }
}
function fmtUptime(s) {
  if (typeof s !== "number") return "—";
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
  return `${h} 时 ${m} 分 ${Math.floor(s % 60)} 秒`;
}
function wiringReport() {
  $("ov-wiring").innerHTML = `<div class="small">端点覆盖 · 正在从 /openapi.json 实时派生…</div>`;
  renderCoverage();
}
const p41Strip = (t) => {
  let s = t.replace(/\$\{[^{}]*\}/g, "{}");
  const q = s.indexOf("?"); if (q >= 0) s = s.slice(0, q);
  return s.replace(/\/+$/, "") || "/";
};
async function renderCoverage() {
  const box = $("ov-wiring");
  let oaR, jsR, htmlR;
  try {
    [oaR, jsR, htmlR] = await Promise.all([
      fetch("/openapi.json"), fetch("/ui/app.js"), fetch("/"),
    ]);
  } catch (e) {
    box.innerHTML = `<div class="err-box">端点覆盖派生失败（诚实处理，不回落手写清单）：网络错误 ${esc(String(e))}</div>`;
    return;
  }
  if (!oaR.ok || !jsR.ok || !htmlR.ok) {
    box.innerHTML = `<div class="err-box">端点覆盖派生失败（诚实处理，不回落手写清单）：` +
      `openapi=${oaR.status} app.js=${jsR.status} index=${htmlR.status}</div>`;
    return;
  }
  const norm = (p) => p.replace(/\{[^{}]*\}/g, "{}").replace(/\/+$/, "");
  const paths = Object.keys(((await oaR.json()).paths) || {}).filter((p) => p.startsWith(API + "/"));
  const normSet = new Set(paths.map(norm));
  const domains = new Set([...normSet].map((p) => p.split("/")[3]));
  const js = await jsR.text(), html = await htmlR.text();
  const lits = new Set();
  // Phase 48 口径修正：除"以 / 开头的字面串"外，`${API}/…` 形式的插值同样会真发 HTTP 请求
  // （素材缩略图 <img src> 即此类，且它嵌在模板串内部，旧口径连"以反引号开头"都判不到）——
  // p48_probe2 实测宽松口径 B 比口径 A 多 1 条、A 无多计、两侧 ghost 皆 0。
  // 尺子应当量浏览器真实行为，故修正派生器而非改写可用代码去迁就尺子。
  for (const re of [/"(\/[^"\n]*)"/g, /'(\/[^'\n]*)'/g, /`(\/[^`]*)`/g, /\$\{API\}(\/[^"`\s]*)/g])
    for (const m of js.matchAll(re)) lits.add(m[1]);

  for (const m of html.matchAll(/data-ops="([^"]+)"/g)) lits.add(m[1]);
  const wired = new Set(); const ghost = [];
  for (const raw of lits) {
    const s = p41Strip(raw);
    if (s === "/" || s === API) continue;
    const seg = s.replace(/^\//, "").split("/")[0];
    if (!seg || seg === "api" || !domains.has(seg)) continue;
    const full = norm(API + (s.startsWith(API) ? s.slice(API.length) : s));
    if (normSet.has(full)) wired.add(full); else ghost.push(full);
  }
  const notWired = [...normSet].filter((p) => !wired.has(p)).sort();
  const doms = [...domains].sort();
  const rows = doms.map((d) => {
    const all = [...normSet].filter((p) => p.split("/")[3] === d);
    const w = all.filter((p) => wired.has(p)).length;
    const b = w === all.length ? badge("全接线", "ok") : (w ? badge("部分", "warn") : badge("未接线", "mut"));
    // Phase 69：数字格换成与同一份 w/all 同源的进度条（.bar 是本控制台既有元件，评测面板在用）。
    // 判据口径一点没动——wired / notWired / ghost 仍由上面那段实时派生；这里只换呈现面，
    // 且刻意不引入任何新的端点字面串，接线宇宙（135 条）与镜像派生不受影响。
    const pct = all.length ? Math.round((w / all.length) * 100) : 0;
    return `<tr><td>${esc(d)}</td><td class="bar-cell"><div class="bar" title="${esc(d)} · 已接线 ${w}/${all.length}"><i style="width:${pct}%"></i><span>${w}/${all.length}</span></div></td><td>${b}</td></tr>`;
  });
  box.innerHTML =
    `<div class="small">端点覆盖 · <b>实时派生</b>：以 GET /openapi.json（本服务真实路由清单，单一真相源）` +
    `与本控制台自身请求代码中的端点串逐一匹配——<b>非手写清单</b>，后端新增/移除端点、控制台代码改动都会如实反映于此。</div>` +
    kv([["端点路径总数", normSet.size], ["控制台已接线", wired.size],
        ["未接线（not_wired_yet）", notWired.length],
        ["幽灵接线（引用了服务端不存在的端点）", ghost.length]]) +
    (ghost.length
      ? `<div class="err-box">检测到幽灵接线（控制台代码引用了 openapi 中不存在的端点，属虚假声称，必须修复）：${ghost.map(esc).join(" · ")}</div>`
      : `<div class="small">幽灵 0 · 本控制台代码引用的端点串全部真实存在于服务端路由中。</div>`) +
    `<details open><summary>按域接线状态（${doms.length} 域）</summary>` +
    `<table><thead><tr><th>域</th><th>接线进度</th><th>状态</th></tr></thead><tbody>${rows.join("")}</tbody></table></details>` +
    `<details><summary>未接线端点全清单（${notWired.length} 条 · 后端真实存在、控制台如实标注不假装）</summary>` +
    `<div class="chips" style="margin-top:6px">${notWired.map((p) => `<span class="chip"><code>${esc(p)}</code> · <i class="badge mut">not_wired_yet</i></span>`).join("")}</div></details>` +
    `<div class="small muted">判据：路径字符串出现在本页 fetch/POST/data-ops <b>或 &lt;img src&gt; 等真实请求代码</b>中即"已接线"；` +
    `Phase 48 起派生口径把 <code>\${API}/…</code> 形式的插值一并纳入（含嵌在模板串内部的 <code>&lt;img src&gt;</code>；` +
    `此前只认"以 <code>/</code> 开头的引号字面串"，实测低估 1 条真实缩略图请求）。原手写"已接线/尚未接线"清单自 Phase 41 起废止。</div>`;

}
$("ov-metrics-btn").addEventListener("click", async () => {
  renderOut("ov-metrics", await api("/metrics"), (j) => "");
});

/* ============================ 评估中心 ============================ */
$("ev-run").addEventListener("click", async () => {
  const btn = $("ev-run");
  btn.disabled = true;
  $("ev-load").textContent = "全量评测运行中（逐用例真实调用引擎，typography 域较慢，请稍候）…";
  const r = await api("/eval/run", { method: "POST" });
  btn.disabled = false;
  $("ev-load").textContent = "";
  if (!r.ok) { $("ev-summary").innerHTML = errBox(r); return; }
  const j = r.json;
  const sum = j.summary || {};
  const honest = j.honest === true;
  const failList = (j.results || []).filter((x) => !x.passed);
  const sumRows = Object.keys(sum).map((k) => [k, sum[k]]);
  sumRows.push(["判分器诚实", honest ? "honest = true" : "honest = false（判分链路可能失真）"]);
  let sumHtml = `<div class="card"><h3>本次执行</h3>` + kv(sumRows);
  sumHtml += honest ? badge("评测器自校验通过（负控校准全部如实 fail）", "ok") : badge("校准异常：全卷判分不可信", "err");
  if (failList.length) sumHtml += `<div class="err-box" style="margin-top:8px">失败用例：${failList.map((x) => esc(x.id)).join("、")}</div>`;
  $("ev-summary").innerHTML = `<div class="cards">` + sumHtml + `</div></div>`;
  const cats = j.categories || {};
  $("ev-cats").innerHTML = Object.keys(cats).sort().map((k) => {
    const c = cats[k]; const total = c.total || 0;
    const pct = total ? Math.round((c.passed || 0) / total * 100) : 0;
    return `<div class="form-row" style="margin:6px 0">
      <span style="width:110px">${esc(k)}</span>
      <div class="bar" style="flex:1"><i style="width:${pct}%"></i><span>${c.passed || 0}/${total}</span></div>
      <span class="muted small" style="width:60px;text-align:right">${c.failed ? c.failed + " 失败" : "全通过"}</span></div>`;
  }).join("");
  const cal = j.calibration || [];
  $("ev-calib").innerHTML = `<div class="small muted" style="margin-bottom:6px">校准用例期望为 fail：若判分器给它们绿灯，全卷 verified 作废（防伪造判分第四防线）。</div>` +
    (cal.length ? `<table><thead><tr><th>校准项</th><th>期望</th><th>实际</th><th>判定</th></tr></thead><tbody>` +
      cal.map((c) => `<tr><td>${esc(c.id)}</td><td>${esc(c.expected_verdict)}</td>
        <td>${esc(c.actual_verdict)}</td><td>${c.actual_verdict === "fail" ? badge("校准有效", "ok") : badge("校准失效", "err")}</td></tr>`).join("")
      + `</tbody></table>` : `<span class="muted">无校准数据</span>`);
});
$("ev-suite").addEventListener("click", async () => {
  const r = await api("/eval/suite");
  if (!r.ok) { $("ev-summary").innerHTML = errBox(r); return; }
  $("ev-summary").innerHTML = `<section class="block"><h3>用例清单（未执行）</h3>` + rawBlock(r.json) + `</section>`;
});

/* P59：本枚按钮上的用例数不手写，只从 GET /api/v1/eval/suite 实时派生。
 * 缘由是实录而非预防：index.html 这里曾手写 "99 条计分用例 + 23 条负控校准"，而九轮
 * 评测扩张后的真值已是 139/32，六层验收无一层看过它，于是随包界面静默说谎了九轮。
 * 计分条数 = 清单条数 − must_fail 条数，与 L2 钉的 identity 同一条式子（同一个真相源）。
 * 取不到清单时**保留 HTML 里的无数字文案**直接返回——绝不把猜来的分母印给用户。 */
(async () => {
  const r = await api("/eval/suite");
  const cs = (r.json && r.json.cases) || [];
  const mf = cs.filter((c) => c && c.must_fail).length;
  if (!cs.length) return;
  $("ev-run").textContent = `运行全量评测（${cs.length - mf} 条计分用例 + ${mf} 条负控校准，逐条真实调用引擎判分）`;
})();
$("ev-reports").addEventListener("click", async () => {
  const r = await api("/eval/reports");
  if (!r.ok) { $("ev-reportlist").innerHTML = errBox(r); return; }
  const list = r.json.reports || [];
  if (!list.length) { $("ev-reportlist").innerHTML = `<span class="muted">暂无落盘报告。</span>`; return; }
  $("ev-reportlist").innerHTML = `<table><thead><tr><th>报告</th><th>完成时间</th><th>执行用例</th><th>通过率</th><th>诚实</th><th></th></tr></thead><tbody>` +
    list.map((p) => `<tr><td class="num">${esc(p.report_id)}</td>
      <td>${esc(asText(p.finished_at))}</td>
      <td class="num">${esc(asText(p.executed_cases))}</td>
      <td class="num">${esc(asText(p.pass_rate))}</td>
      <td>${p.honest === false ? badge("不诚实", "err") : p.honest ? badge("honest", "ok") : esc(asText(p.error))}</td>
      <td>${p.error ? "" : `<button class="ghost" data-report="${esc(p.report_id)}">查看</button>`}</td></tr>`).join("") +
    `</tbody></table><div class="out" id="ev-report-detail"></div>`;
  document.querySelectorAll("#ev-reportlist button[data-report]").forEach((b) => {
    b.addEventListener("click", async () => {
      const rr = await api("/eval/reports/" + encodeURIComponent(b.dataset.report));
      $("ev-report-detail").innerHTML = rr.ok ? rawBlock(rr.json) : errBox(rr);
    });
  });
});

/* ============================ 色彩 ============================ */
const colorViewer = (j) => hexChips(j) + `<pre>${esc(JSON.stringify(j, null, 2))}</pre>`;
$("cc-convert").addEventListener("click", () => renderOut("cc-convert-out",
  Post("/color/convert", { hex: $("cc-hex").value }), colorViewer));
$("ct-run").addEventListener("click", () => renderOut("ct-out",
  Post("/color/contrast", { fg: $("ct-fg").value, bg: $("ct-bg").value }), colorViewer));
$("de-run").addEventListener("click", () => renderOut("de-out",
  Post("/color/delta-e", { a: $("de-a").value, b: $("de-b").value, method: $("de-m").value }), colorViewer));
$("pt-run").addEventListener("click", () => renderOut("pt-out",
  Post("/color/pantone", { hex: $("pt-hex").value }), colorViewer));
$("hm-run").addEventListener("click", () => renderOut("hm-out",
  Post("/color/harmony", { base: $("hm-base").value, scheme: $("hm-scheme").value }), colorViewer));
$("pl-run").addEventListener("click", () => renderOut("pl-out",
  Post("/color/palette", { path: $("pl-path").value, k: +$("pl-k").value || 6 }), colorViewer));
$("nr-run").addEventListener("click", () => renderOut("nr-out",
  Post("/color/nearest", {
    color: $("nr-color").value,
    palette: $("nr-pal").value.split(/[,，\s]+/).filter(Boolean),
  }), colorViewer));

/* ============================ 包装 · 条码 ============================ */
$("bc-run").addEventListener("click", async () => {
  const box = $("bc-out"), cv = $("bc-canvas");
  cv.hidden = true;
  const _qraw = $("bc-quiet").value;
  const _body = {
    code: $("bc-code").value,
    symbology: $("bc-sym").value,
    ec_level: $("bc-ec").value,
    scale: +$("bc-scale").value || 2,
    height: +$("bc-height").value || 60,
    draw_text: $("bc-text").value === "1",
  };
  // 静区留空＝不传，让服务端按码制各取其标准值（一维 11 模块、QR 4 模块）；
  // 若前端硬塞 11，QR 会被撑出一圈过宽白边，掩盖真实静区契约。
  if (_qraw !== "") _body.quiet = +_qraw;
  const res = await Post("/packaging/barcode", _body);
  if (!res.ok) { box.innerHTML = errBox(res); return; }
  const j = res.json;
  if (j.status === "needs_external") {
    box.innerHTML = `<div class="info-box">服务端如实返回 needs_external（未伪造图像）：${esc(j.reason || "")}</div>` + rawBlock(j);
    return;
  }
  const scale = j.scale || 2;
  const quiet = (j.quiet_zone_modules || 0) * scale;
  const w = j.width_px, h = j.height_px;
  cv.width = w; cv.height = h; cv.hidden = false;
  const g = cv.getContext("2d");
  g.fillStyle = "#fff"; g.fillRect(0, 0, w, h);
  g.fillStyle = "#000";
  // 服务端对 QR 返回二维模块阵 matrix（行字符串数组），对一维码返回单行 bit_pattern。
  // 两者形状不同，画法必须分支：把 QR 的 matrix 拼成的 bit_pattern 当一维条纹逐列画，
  // 会画出一条与真码毫无关系的横带——形状分岔是这里唯一的正确依据。
  const _mx = Array.isArray(j.matrix) ? j.matrix : null;
  let drawNote;
  if (_mx) {
    const side = _mx.length;
    for (let r57 = 0; r57 < side; r57++) {
      const row = _mx[r57];
      for (let c57 = 0; c57 < side; c57++) {
        if (row[c57] === "1") g.fillRect(quiet + c57 * scale, quiet + r57 * scale, scale, scale);
      }
    }
    drawNote = `画布按服务端返回的 ${side}×${side} 模块阵 matrix 逐格绘制（${j.module_count} 模块），与服务端渲染的 PNG 同源一致。`;
  } else {
    const bits = j.bit_pattern || "";
    const y0 = j.draw_text ? 22 : 10, bh = j.height_px - 2 * y0;
    bits.split("").forEach((ch, i) => {
      if (ch === "1") g.fillRect(quiet + i * scale, y0, scale, bh);
    });
    drawNote = "画布按服务端返回的 bit_pattern 逐模块绘制，与服务端渲染的 PNG 同源一致。";
  }
  const _rows = [["完整码", j.code], ["模块数", j.module_count],
                 ["像素", j.width_px + " × " + j.height_px], ["输出文件(服务端)", j.output_path]];
  if (_mx) {
    _rows.splice(1, 0, ["纠错等级", j.ec_level], ["版本", "v" + j.version],
                 ["侧边模块", j.side + " × " + j.side], ["编码模式", j.mode],
                 ["掩码", j.mask_pattern], ["格式位", j.format_bits],
                 ["码字(数据/纠错)", (j.data_codewords || 0) + " / " + (j.ec_codewords || 0)]);
  } else {
    _rows.splice(1, 0, ["校验位", j.check_digit]);
  }
  _rows.push(["伪造", j.fabricated === false ? "false（引擎真实渲染）" : asText(j.fabricated)]);
  box.innerHTML = kv(_rows) + `<div class="small muted" style="margin-top:6px">${esc(drawNote)}</div>` +
    (_mx ? `<pre class="small">${esc(_mx.join("\n"))}</pre>`
         : `<pre>bit_pattern (${(j.bit_pattern || "").length} 模块): ${esc(j.bit_pattern || "")}</pre>`) +
    rawBlock(j);
});

/* ============================ 包装 · 刀版拓扑 ============================ */
const TP_DEFAULT = `glue,-15,0,15,100
front,0,0,60,100
right,60,0,40,100
back,100,0,60,100
left,160,0,40,100
top_f,0,100,60,30
top_b,100,100,60,30
bot_f,0,-30,60,30
bot_b,100,-30,60,30`;
$("tp-reset").addEventListener("click", () => { $("tp-panels").value = TP_DEFAULT; $("tp-out").innerHTML = ""; $("tp-svg").innerHTML = ""; });
function parsePanels(text) {
  const panels = [];
  for (const line of text.split(/\n+/)) {
    const t = line.trim();
    if (!t) continue;
    const p = t.split(/[,，\s]+/);
    if (p.length !== 5) throw new Error(`行「${t}」需要 5 个字段：id,x,y,width,height`);
    panels.push({ id: p[0], x: +p[1], y: +p[2], width: +p[3], height: +p[4] });
  }
  if (!panels.length) throw new Error("至少需要一个面板");
  return panels;
}
$("tp-run").addEventListener("click", async () => {
  const box = $("tp-out"), svg = $("tp-svg");
  let panels;
  try { panels = parsePanels($("tp-panels").value); }
  catch (e) { box.innerHTML = errBox({ status: 0, detail: "前端输入校验（未发请求）：" + e.message }); return; }
  const r = await Post("/packaging/panel-topology", {
    panels, tol: parseFloat($("tp-tol").value) || 1e-6,
    min_fold: parseFloat($("tp-minfold").value) || 1e-6,
    units: $("tp-units").value || "mm",
  });
  if (!r.ok) { box.innerHTML = errBox(r); svg.innerHTML = ""; return; }
  const j = r.json;
  const okStatus = j.status === "ok";
  box.innerHTML = `<div class="grid2"><div>${kv([
    ["判定", okStatus ? badge("单连通刀版", "ok") : badge("disconnected（如实上报）", "warn")],
    ["面板数(有效)", asText(j.panel_count)],
    ["邻接关系数", asText(j.relation_count)],
    ["连通分量", asText(j.group_count) + " · " + asText(j.groups)],
    ["折线总长", asText(j.fold_length_total) + " " + asText(j.units)],
    ["外轮廓裁切线长", asText(j.cut_boundary_length) + " " + asText(j.units)],
    ["面板面积合计", asText(j.total_panel_area)],
    ["外接矩形", asText(j.extent && (j.extent.width + " × " + j.extent.height))],
    ["被拒面板", asText(j.rejected)],
    ["伪造", j.fabricated === false ? "false（纯几何推断）" : asText(j.fabricated)],
    ["方法", j.method],
  ])}</div><div>${rawBlock(j)}</div></div>`;
  drawTopology(j);
});
function drawTopology(j) {
  const panels = j.panels || [];
  if (!panels.length) { $("tp-svg").innerHTML = ""; return; }
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  panels.forEach((p) => {
    minX = Math.min(minX, p.x); minY = Math.min(minY, p.y);
    maxX = Math.max(maxX, p.x + p.width); maxY = Math.max(maxY, p.y + p.height);
  });
  const pad = Math.max(maxX - minX, maxY - minY) * 0.08 || 10;
  const vb = [minX - pad, minY - pad, (maxX - minX) + 2 * pad, (maxY - minY) + 2 * pad];
  const sw = vb[2] / 400;
  const pal = ["#4f8cff", "#3fbf72", "#e0a63c", "#c678dd", "#56b6c2", "#e2575b", "#d19a66", "#7f8fae", "#98c379"];
  const rects = panels.map((p, i) =>
    `<rect x="${p.x}" y="${p.y}" width="${p.width}" height="${p.height}" fill="${pal[i % pal.length]}22" stroke="${pal[i % pal.length]}" stroke-width="${sw}"/>
     <text x="${p.x + p.width / 2}" y="${p.y + p.height / 2}" fill="#d7dde8" font-size="${vb[2] / 55}" text-anchor="middle">${esc(p.id)}</text>
     <text x="${p.x + p.width / 2}" y="${p.y + p.height / 2 + vb[2] / 45}" fill="#8a94a6" font-size="${vb[2] / 75}" text-anchor="middle">度${esc(p.fold_degree)} ${esc(p.size_class)}</text>`).join("");
  const folds = (j.adjacency || []).map((rel) => {
    const fl = rel.fold_line || {};
    if (fl.x !== undefined)
      return `<line x1="${fl.x}" y1="${fl.y1}" x2="${fl.x}" y2="${fl.y2}" stroke="#e0a63c" stroke-width="${sw * 1.6}" stroke-dasharray="${vb[2] / 90} ${vb[2] / 140}"/>`;
    if (fl.y !== undefined)
      return `<line x1="${fl.x1}" y1="${fl.y}" x2="${fl.x2}" y2="${fl.y}" stroke="#e0a63c" stroke-width="${sw * 1.6}" stroke-dasharray="${vb[2] / 90} ${vb[2] / 140}"/>`;
    return "";
  }).join("");
  $("tp-svg").innerHTML =
    `<svg viewBox="${vb.join(" ")}" width="100%" style="min-height:340px" xmlns="http://www.w3.org/2000/svg">
      ${rects}${folds}
      <text x="${vb[0]}" y="${vb[1] + vb[3] - sw}" fill="#8a94a6" font-size="${vb[2] / 60}">虚线=折线（服务端 fold_line 坐标） · 矩形=面板（引擎归一化几何） · 单位 ${esc(asText(j.units))}</text>
    </svg>`;
}

/* ============================ 包装 · dieline / imposition ============================ */
$("dl-run").addEventListener("click", () => renderOut("dl-out",
  Post("/packaging/dieline", {
    width: +$("dl-w").value, height: +$("dl-h").value,
    bleed: +$("dl-bleed").value || 0, safe: +$("dl-safe").value || 0,
    units: $("dl-units").value || "mm",
  }), (j) => kv([
    ["成品", asText(j.finished.width) + " × " + asText(j.finished.height) + " " + asText(j.units)],
    ["裁切框 trim", asText(j.trim_box)], ["出血框 bleed", asText(j.bleed_box)],
    ["安全框 safe", asText(j.safety_box)], ["最小 artwork 尺寸", asText(j.min_artwork_size)],
    ["面积 trim/bleed/safe", asText(j.areas && [j.areas.trim_area, j.areas.bleed_area, j.areas.safe_area])],
  ] + ((j.warnings || []).length
    ? `<div class="info-box">引擎如实警告：${j.warnings.map(esc).join("；")}</div>` : ""))));
$("im-run").addEventListener("click", () => renderOut("im-out",
  Post("/packaging/imposition", {
    die_width: +$("im-dw").value, die_height: +$("im-dh").value,
    count: +$("im-count").value, sheet_width: +$("im-sw").value,
    sheet_height: +$("im-sh").value, gap: +$("im-gap").value || 0,
  }), (j) => j.feasible === false
    ? `<div class="info-box">如实返回不可行：${esc(j.reason)}</div>`
    : kv([
      ["可行", "true · 选用 " + asText(j.chosen.orientation)],
      ["每行列数", asText(j.chosen.per_row) + " × " + asText(j.chosen.per_col)],
      ["单张满版 n-up", asText(j.n_up)], ["用纸张数", asText(j.sheets_needed)],
      ["总槽位/浪费", asText(j.total_slots) + " / " + asText(j.wasted_slots)],
      ["用纸利用率", asText(j.sheet_utilization_percent) + " %"],
      ["直/旋备选", asText(j.alternatives.straight.n_up) + " / " + asText(j.alternatives.rotated.n_up)],
    ])));

/* ============================ 素材 ============================ */
async function refreshAssets() {
  $("as-load").textContent = "加载素材库…";
  const r = await api("/assets");
  $("as-load").textContent = "";
  if (!r.ok) { $("as-list").innerHTML = errBox(r); return; }
  const j = r.json;
  $("as-list").innerHTML = `<div class="small muted" style="margin-bottom:6px">共 ${j.total} 项 · 存储根（服务端）：${esc(j.storage)}</div>` +
    (j.assets.length ? `<table><thead><tr><th>缩略图</th><th>ID</th><th>名称</th><th>类型</th><th>尺寸</th><th>大小</th><th>格式</th></tr></thead><tbody>` +
      j.assets.map((a) => `<tr>
        <td>${a.thumbnail ? `<img class="thumb" loading="lazy" tabindex="0" role="button" title="点击在本面板放大（真实请求 GET /api/v1/assets/{id}/thumbnail）" data-thumb="${esc(a.id)}" alt="${esc(a.name)} 缩略图" src="${API}/assets/${encodeURIComponent(a.id)}/thumbnail" onerror="this.outerHTML='<span class=thumb-none>取不到（端点非 200，状态如实）</span>'">` : `<span class="thumb-none">无（非图像素材，服务端未生成）</span>`}</td>
        <td class="num">${esc(a.id)}</td><td>${esc(a.name)}</td><td>${esc(a.type)}</td>
        <td class="num">${esc(asText(a.size.width))}×${esc(asText(a.size.height))}</td>
        <td class="num">${esc(asText(a.size_bytes))} B</td><td>${esc(a.format)}</td></tr>`).join("") +
      `</tbody></table>` +
      `<div id="as-thumb-view" class="thumb-view" hidden></div>`
      : `<div class="muted">素材库为空（真实状态：尚未导入任何素材。通过 POST /api/v1/assets 传入服务端文件路径导入）。</div>`);
}
/* Phase 48：缩略图真实渲染与放大预览——图源就是本服务的二进制端点，非占位图、非 data-uri 伪造 */
document.addEventListener("click", (e) => {
  const t = e.target.closest && e.target.closest("img.thumb[data-thumb]");
  if (!t) return;
  const view = $("as-thumb-view");
  if (!view) return;
  const id = t.getAttribute("data-thumb");
  view.hidden = false;
  view.innerHTML = `<div class="thumb-view-inner">` +
    `<img class="thumb-lg" alt="素材 ${esc(id)} 缩略图放大" src="${API}/assets/${encodeURIComponent(id)}/thumbnail">` +
    `<div class="small muted">放大显示的是 <code>GET /api/v1/assets/{id}/thumbnail</code> 的真实返回（最长边 256px 的服务端缩略图）。` +
    `本服务未对外暴露原始 artwork 的全分辨率读取端点，故此处<b>不是</b>原图，界面不假装是。</div></div>`;
  view.scrollIntoView({ block: "nearest" });
});

$("as-refresh").addEventListener("click", refreshAssets);
$("as-search-btn").addEventListener("click", async () => {
  const q = $("as-q").value.trim();
  if (!q) { $("as-search").innerHTML = `<div class="info-box">请输入查询文本。</div>`; return; }
  const r = await Post("/assets/search", { query: q, top_k: 10 });
  renderOut("as-search", r, (j) => `<div class="small muted">查询：「${esc(q)}」命中 ${asText(j.total)} 项（向量语义检索，命中为真、未命中不伪造）</div>` +
    `<pre>${esc(JSON.stringify(j, null, 2))}</pre>`);
});

/* ============================ 字体排版 ============================ */
$("ty-list-btn").addEventListener("click", async () => {
  const q = new URLSearchParams();
  if ($("ty-filter").value.trim()) q.set("family", $("ty-filter").value.trim());
  if ($("ty-cjk").value === "1") q.set("cjk_only", "true");
  const r = await api("/typography/fonts?" + q.toString());
  if (!r.ok) { $("ty-list").innerHTML = errBox(r); return; }
  const j = r.json;
  $("ty-list").innerHTML = `<div class="small muted">共 ${j.total} 款（真实解析系统字体索引）</div>` +
    monoTable(j.fonts.slice(0, 300), [
      ["family", (v) => v], ["style", (v) => v], ["weight", (v) => v],
      ["upem", (v) => v], ["CJK", (v) => v],
    ]) + (j.fonts.length > 300 ? `<div class="small muted">（仅显示前 300 条）</div>` : "");
});
$("ty-scan-btn").addEventListener("click", async () => {
  const btn = $("ty-scan-btn");
  btn.disabled = true; btn.textContent = "扫描中（遍历系统字体目录，较慢）…";
  const r = await api("/typography/scan", { method: "POST" });
  btn.disabled = false; btn.textContent = "重建索引（扫描系统字体，较慢）";
  renderOut("ty-list", r, (j) => kv([
    ["本次扫描", asText(j.scanned)], ["解析失败(如实计数)", asText(j.errors)],
    ["索引总量", asText(j.total_indexed)],
  ]));
});
$("tm-run").addEventListener("click", () => renderOut("tm-out",
  Post("/typography/metrics", { font: $("tm-font").value, text: $("tm-text").value, size_pt: +$("tm-size").value || 12 }),
  (j) => kv([
    ["字体", j.font], ["advance 单位合计", asText(j.advance_units)],
    ["排版宽度", asText(j.width_pt) + " pt"], ["行高", asText(j.line_height_pt) + " pt"],
    ["字形数", asText(j.glyph_count)], ["缺字", asText(j.missing_glyphs)],
    ["全覆盖", asText(j.fully_covered)],
  ])));
$("tq-run").addEventListener("click", () => renderOut("tq-out",
  Post("/typography/match", {
    missing: $("tq-missing").value, text: $("tq-text").value.trim() || null,
    require_cjk: $("tq-cjk").value === "1", top_k: 5,
  }),
  (j) => `<div class="small muted">「${esc(j.missing)}」候选 ${asText(j.total)} 个（覆盖度为硬门槛，宁缺毋滥；0 个即如实为空）</div>` +
    monoTable(j.candidates, [["family", (v) => v], ["style", (v) => v], ["weight", (v) => v],
      ["similarity", (v) => v], ["score", (v) => v], ["CJK", (v) => v]])));

/* ============================ 通用：JSON 输入与动态表 ============================ */
function parseJsonField(id) {
  const t = $(id).value.trim();
  if (!t) return { ok: true, value: null };
  try { return { ok: true, value: JSON.parse(t) }; }
  catch (e) { return { ok: false, error: `#${id} 的 JSON 解析失败：${e.message}` }; }
}
function showLocalErr(elId, msg) {
  const box = $(elId);
  /* 本地拦截也是一次「落定」：必须把 busy 收回 false，否则同一结果区上一次
     没等到回执就被本地校验改写时，读屏器会一直以为还在途（P74A）。 */
  if (!box.hasAttribute("aria-live")) box.setAttribute("aria-live", "polite");
  box.setAttribute("aria-busy", "false");
  box.classList.remove("is-busy");
  box.innerHTML = `<div class="err-box">输入未通过本地 JSON 解析（未发起请求）：${esc(msg)}</div>`;
}
function dynTable(rows) {
  if (!rows || !rows.length) return `<div class="muted small">（空结果）</div>`;
  return monoTable(rows, Object.keys(rows[0]).map((k) => [k, (v) => asText(v)]));
}
const objTable = (list) => dynTable(list);
const adjTable = (list) => dynTable((list || []).map((o) => ({
  id: o.id, to_x: o.to && o.to.x, to_y: o.to && o.to.y, dx: o.dx, dy: o.dy,
  from_x: o.from && o.from.x, from_y: o.from && o.from.y, cell: o.cell,
})));

/* ============================ 设计工具 ============================ */
$("la-align").addEventListener("click", () => {
  const o = parseJsonField("la-objs");
  if (!o.ok) { showLocalErr("la-align-out", o.error); return; }
  renderOut("la-align-out", Post("/layout/align", { objects: o.value || [], mode: $("la-mode").value }),
    (j) => kv([["模式", j.mode], ["参考框", j.reference_box], ["已对齐", j.aligned], ["备注", j.note]]) + adjTable(j.objects));
});
$("lc-run").addEventListener("click", () => {
  const o = parseJsonField("la-objs");
  if (!o.ok) { showLocalErr("lc-out", o.error); return; }
  renderOut("lc-out", Post("/layout/collide", { objects: o.value || [], min_overlap_ratio: +$("lc-min").value || 0 }),
    (j) => kv([["对象数", j.object_count], ["碰撞数", j.collision_count]]) +
      dynTable((j.collisions || []).map((c) => ({ a: c.a, b: c.b, overlap_ratio: c.overlap_ratio }))));
});
$("ld-run").addEventListener("click", () => {
  const o = parseJsonField("la-objs");
  if (!o.ok) { showLocalErr("ld-out", o.error); return; }
  const body = { objects: o.value || [], axis: $("ld-axis").value };
  if ($("ld-gap").value !== "") body.spacing = +$("ld-gap").value;
  renderOut("ld-out", Post("/layout/distribute", body),
    (j) => kv([["轴", j.axis], ["已分布", j.distributed], ["计算间隙", j.computed_gap], ["间距来源", j.spacing_source], ["备注", j.note]]) + adjTable(j.objects));
});
$("ls-run").addEventListener("click", () => {
  const o = parseJsonField("la-objs");
  const c = parseJsonField("la-canvas");
  if (!o.ok) { showLocalErr("ls-out", o.error); return; }
  if (!c.ok) { showLocalErr("ls-out", c.error); return; }
  const body = { objects: o.value || [], canvas: c.value || {} };
  if ($("ls-margin").value !== "") body.margin = +$("ls-margin").value;
  renderOut("ls-out", Post("/layout/safe-area", body),
    (j) => kv([["margin", j.margin], ["安全框", j.safe_box], ["违规数", j.violation_count]]) + dynTable(j.violations));
});
$("lg-run").addEventListener("click", () => {
  const o = parseJsonField("la-objs");
  const c = parseJsonField("la-canvas");
  if (!o.ok) { showLocalErr("lg-out", o.error); return; }
  if (!c.ok) { showLocalErr("lg-out", c.error); return; }
  renderOut("lg-out", Post("/layout/grid-snap", {
    objects: o.value || [], canvas: c.value || {},
    cols: +$("lg-cols").value || 12, rows: +$("lg-rows").value || 12, mode: $("lg-mode").value,
  }), (j) => kv([["栅格", j.grid], ["模式", j.mode], ["已吸附", j.snapped]]) + adjTable(j.objects));
});
$("lz-run").addEventListener("click", () => {
  const o = parseJsonField("la-objs");
  const c = parseJsonField("la-canvas");
  if (!o.ok) { showLocalErr("lz-out", o.error); return; }
  if (!c.ok) { showLocalErr("lz-out", c.error); return; }
  renderOut("lz-out", Post("/layout/analyze", { objects: o.value || [], canvas: c.value || {} }),
    (j) => kv([["对象数", j.object_count], ["体检评分", j.score], ["碰撞", j.collision], ["安全区", j.safe_area], ["发现数", (j.findings || []).length]]));
});
$("cp-run").addEventListener("click", () => {
  const b = parseJsonField("cp-before");
  const a = parseJsonField("cp-after");
  if (!b.ok) { showLocalErr("cp-out", b.error); return; }
  if (!a.ok) { showLocalErr("cp-out", a.error); return; }
  const body = { before: b.value || [], after: a.value || [] };
  if ($("cp-keys").value.trim()) body.compare_keys = $("cp-keys").value.split(",").map((s) => s.trim()).filter(Boolean);
  renderOut("cp-out", Post("/design/compare", body), (j) => {
    const sm = j.summary || {};
    return kv([
      ["完全一致", j.identical], ["新增", sm.added], ["删除", sm.removed],
      ["变更", sm.changed], ["移动", sm.moved], ["六类分布", sm.by_category],
    ]) + hexChips(j);
  });
});
$("bgrm-run").addEventListener("click", () => {
  const body = { image_path: $("bgrm-path").value.trim(), threshold: +$("bgrm-thr").value || 0 };
  if ($("bgrm-out").value.trim()) body.output_path = $("bgrm-out").value.trim();
  renderOut("bgrm-out-box", Post("/generate/remove-background", body), (j) => kv([
    ["输入", j.input], ["输出", j.output], ["算法", j.algorithm],
    ["清除比例", j.background_cleared_ratio], ["含 alpha", j.has_alpha],
    ["耗时", j.processing_time],
  ]));
});

/* ============================ 品牌智能 ============================ */
$("bpal-run").addEventListener("click", () => {
  renderOut("bpal-out", Post("/brand/palette", { image_path: $("bpal-path").value.trim(), k: +$("bpal-k").value || 6 }),
    (j) => hexChips(j));
});
$("bcons-run").addEventListener("click", () => {
  renderOut("bcons-out", Post("/brand/color-consistency", {
    brand_hexes: $("bcons-hexes").value.split(",").map((s) => s.trim()).filter(Boolean),
    image_path: $("bcons-path").value.trim(),
    max_delta_e: +$("bcons-de").value || 3, k: +$("bcons-k").value || 8,
  }), (j) => kv([
    ["覆盖率", j.coverage], ["全部匹配", j.all_matched],
    ["伪造", asText(j.fabricated)], ["逐色", j.brand_colors],
  ]) + hexChips(j));
});
$("blogo-run").addEventListener("click", () => {
  renderOut("blogo-out", Post("/brand/logo-geometry", {
    image_path: $("blogo-path").value.trim(), alpha_threshold: +$("blogo-thr").value || 0,
  }), (j) => kv([
    ["内容包围盒", j.content_bbox], ["四边留白", j.margins],
    ["宽高比", j.aspect_ratio], ["填充比", j.fill_ratio],
  ]));
});
/* ---- Phase 70：品牌语义真实透传（显式点击才 POST；未配置 Provider 如实 502，不伪造） ---- */
/* 边界不写死：每次进入品牌面板都读 GET /brand/semantics 的 provider_configured 现场派生，
 * 配置态与未配置态都由服务端说，前端只负责如实呈现与给出接入路径。 */
async function refreshBrandBoundary() {
  const box = $("bsem-boundary");
  if (!box) return;
  const r = await api("/brand/semantics");
  if (!r.ok) { box.innerHTML = `边界声明读取失败（不做本地推断）：` + errBox(r); return; }
  const j = r.json || {};
  const on = j.provider_configured === true;
  box.innerHTML = (on ? badge("Provider 已配置", "ok") : badge("Provider 未配置", "warn")) +
    ` 服务端实时派生 <code>provider_configured=${esc(asText(j.provider_configured))}</code> — ` +
    (on
      ? `点击「真实识别」将经已配置的视觉模型做真实往返，输出逐字透传、不加工。`
      : `此时点击「真实识别」会收到诚实 502 <code>needs_external</code>，本界面不伪造任何语义；请先在配置面板接入真实 Provider。`);
}
$("bsem-run").addEventListener("click", async () => {
  const box = $("bsem-out");
  const path = $("bsem-path").value.trim();
  if (!path) { box.innerHTML = `<div class="err-box">图像路径未填写 — 未发起请求</div>`; return; }
  const body = { image_path: path };
  const pid = $("bsem-provider").value.trim();
  const prompt = $("bsem-prompt").value.trim();
  if (pid) body.provider_id = pid;
  if (prompt) body.prompt = prompt;
  box.innerHTML = `<div class="small muted">POST /brand/semantic-insight …（经真实视觉模型往返，可能耗时数秒）</div>`;
  const r = await Post("/brand/semantic-insight", body);
  if (r.ok) {
    const j = r.json || {};
    if (j.status === "provider_output_unparseable") {
      box.innerHTML = badge("模型输出非 JSON", "warn") +
        `<div class="small">服务端保留模型原始输出（raw），不硬编 schema、不代填字段。</div>` +
        kv([["状态", j.status], ["伪造", asText(j.fabricated)], ["Provider", j.provider_id || j.provider]]) +
        (j.raw ? `<pre>${esc(asText(j.raw))}</pre>` : "") + rawBlock(j);
      return;
    }
    box.innerHTML = badge("真实透传", "ok") +
      `<div class="small">模型输出逐字透传：以下语义字段全部来自所配置的视觉模型，本界面不合成、不加工、不改写置信度。</div>` +
      kv([["状态", j.status], ["Provider", j.provider_id || j.provider],
          ["伪造", asText(j.fabricated)], ["usage", j.usage]]) +
      `<pre>${esc(JSON.stringify(j.semantics === undefined ? j : j.semantics, null, 2))}</pre>` +
      rawBlock(j);
    return;
  }
  const d = (r.json && r.json.detail) || {};
  if (r.status === 502 && d.status === "needs_external") {
    box.innerHTML = `<div class="info-box">` + badge("诚实降级 · 502 needs_external", "warn") +
      `<div>服务端未配置真实 AI Provider，语义识别须经真实视觉模型，拒绝凭空伪造（<code>fabricated: ${esc(asText(d.fabricated))}</code>）。</div>` +
      `<div class="small">接入路径：配置面板的 <code>/provider/configure</code> 端点（完整路由前缀 /api/v1）。本界面不会绘制臆造关键词或置信度。</div></div>`;
    return;
  }
  box.innerHTML = errBox(r);
});

/* ============================ 印前检查 ============================ */
const preflightViewer = (j) => kv([
  ["print_ready", j.print_ready], ["summary", j.summary],
  ["问题概要", j.findings_summary], ["文档信息", j.document_info],
  ["建议数", (j.recommendations || []).length],
]);
$("pf-run").addEventListener("click", () => {
  renderOut("pf-out", Post("/preflight", { document_path: $("pf-path").value.trim() }), preflightViewer);
});
$("af-run").addEventListener("click", () => {
  renderOut("af-out", Post("/preflight/auto-fix", { document_path: $("af-path").value.trim() }), (j) => kv([
    ["可修复项", j.fixable_issues], ["已执行", j.fix_executed],
    ["应用", j.applied], ["跳过", j.skipped],
    ["输出", j.output], ["剩余", j.remaining],
  ]));
});
$("ph-run").addEventListener("click", () => {
  renderOut("ph-out", api("/preflight/history?limit=" + (+$("ph-limit").value || 10)),
    (j) => `<div class="small muted">历史 ${asText((j.history || []).length)} 条（真实落盘记录，空即未检查过）</div>` + dynTable(j.history));
});
$("vd-run").addEventListener("click", async () => {
  const body = { source: $("vd-source").value.trim() };
  if ($("vd-map").value.trim()) {
    try { body.field_map = JSON.parse($("vd-map").value); }
    catch (e) { showLocalErr("vd-out", "field_map JSON 解析失败：" + e.message); return; }
  }
  if ($("vd-limit").value !== "") body.limit = +$("vd-limit").value;
  renderOut("vd-out", Post("/packaging/vdp/merge", body), (j) => kv([
    ["总行数", j.total_rows], ["合并行数", j.merged_rows],
    ["可用列", j.available_columns], ["缺失列", j.missing_columns],
  ]) + dynTable((j.records || []).slice(0, 20)));
});
$("pr-run").addEventListener("click", () => {
  renderOut("pr-out", api("/packaging/panel-recognition?image_path=" + encodeURIComponent($("pr-path").value.trim()), { method: "POST" }),
    (j) => {
      const ln = j.lines || {}, bz = j.binarize || {}, tp = j.topology || {};
      const rej = (j.rejected || []).slice(0, 6).map((r) => `bbox ${JSON.stringify(r.bbox)}（${r.area_px}px）${r.reason}`).join("；");
      return kv([
        ["状态", j.status], ["面板数", j.panel_count],
        ["Otsu 阈值 / 墨像素", `${bz.threshold} / ${bz.ink_px}`],
        ["线像素 / 块状墨迹", `${bz.line_px} / ${bz.other_ink_px}`],
        ["cut 段·px", `${(ln.cut || {}).segments} · ${(ln.cut || {}).length_px}`],
        ["fold 段·px", `${(ln.fold || {}).segments} · ${(ln.fold || {}).length_px}`],
        ["邻接 / 度序列", `${tp.adjacency_count} · ${JSON.stringify(tp.degree_sequence || [])}`],
        ["连通组 / 单一展开", `${tp.connected_groups} · ${asText(tp.is_single_connected_net)}`],
        ["外轮廓 px", (j.outline || {}).perimeter_px],
        ["被拒区块", j.rejected_count ? `${j.rejected_count} 项：${rej}` : "无"],
        ["如实原因", j.reason || "—"],
      ]) + ((j.panels || []).length
        ? dynTable(j.panels.slice(0, 40))
        : `<div class="small muted">无封闭面板：服务端如实返回空列表，未臆造型。</div>`);
    });
});

/* ============================ 知识库 ============================ */
async function refreshKnowledge() {
  $("kl-out").innerHTML = `<span class="muted">加载知识库…</span>`;
  const r = await api("/knowledge");
  if (!r.ok) { $("kl-out").innerHTML = errBox(r); return; }
  const j = r.json;
  $("kl-out").innerHTML =
    `<div class="small muted">共 ${asText(j.total)} 项 · 按 allowed_scopes 服务端过滤</div>` +
    dynTable((j.items || []).slice(0, 100));
}
$("kl-run").addEventListener("click", refreshKnowledge);
$("ki-run").addEventListener("click", () => {
  const body = {
    file_path: $("ki-path").value.trim(),
    scope: $("ki-scope").value,
    tags: $("ki-tags").value.split(",").map((s) => s.trim()).filter(Boolean),
  };
  if ($("ki-title").value.trim()) body.title = $("ki-title").value.trim();
  renderOut("ki-out", Post("/knowledge/ingest", body), (j) => kv([
    ["knowledge id", j.id], ["状态", j.status],
    ["作用域", j.scope], ["分块数", j.chunk_count], ["标题", j.title],
  ]));
});
$("kr-run").addEventListener("click", () => {
  const body = { query: $("kr-query").value.trim(), top_k: +$("kr-k").value || 5 };
  if ($("kr-scopes").value.trim()) {
    body.allowed_scopes = $("kr-scopes").value.split(",").map((s) => s.trim()).filter(Boolean);
  }
  renderOut("kr-out", Post("/knowledge/retrieve", body), (j) =>
    `<div class="small muted">命中 ${asText(j.total)} 条（向量语义检索，未命中不伪造）</div>` +
    dynTable(j.results));
});
$("ks-get").addEventListener("click", () => {
  const id = $("ks-id").value.trim();
  if (!id) { showLocalErr("ks-out", "请先填写 item_id"); return; }
  renderOut("ks-out", api("/knowledge/" + encodeURIComponent(id)), (j) => kv([
    ["knowledge id", j.id], ["title", j.title], ["scope", j.scope], ["status", j.status],
  ]));
});
$("ks-del").addEventListener("click", () => {
  const id = $("ks-id").value.trim();
  if (!id) { showLocalErr("ks-out", "请先填写 item_id"); return; }
  if (!confirm("确认删除知识条目 " + id + "？删除后不可恢复。")) return;
  renderOut("ks-out", api("/knowledge/" + encodeURIComponent(id), { method: "DELETE" }),
    (j) => kv([["已删除", j.deleted], ["knowledge id", j.id]]));
});

/* ============================ 改稿评审 ============================ */
function rvObjs(outId) {
  const o = parseJsonField("rv-objs");
  if (!o.ok) { showLocalErr(outId, o.error); return null; }
  return o.value || [];
}
$("rp-run").addEventListener("click", () => {
  const objs = rvObjs("rp-out");
  if (!objs) return;
  const body = { text: $("rp-text").value, objects: objs };
  const p = parseJsonField("rv-protected");
  if (!p.ok) { showLocalErr("rp-out", p.error); return; }
  if (p.value && p.value.length) body.protected = p.value;
  renderOut("rp-out", Post("/revision/parse", body), (j) => kv([
    ["条款数", j.clause_count], ["摘要", j.summary],
    ["未解析（如实）", (j.unparsed || []).length],
  ]) + dynTable(j.changes));
});
$("ra-run").addEventListener("click", () => {
  const objs = rvObjs("ra-out");
  if (!objs) return;
  const ch = parseJsonField("ra-changes");
  if (!ch.ok) { showLocalErr("ra-out", ch.error); return; }
  const body = { objects: objs, changes: ch.value || [] };
  if ($("ra-only").value.trim()) body.only_ids = $("ra-only").value.split(",").map((s) => s.trim()).filter(Boolean);
  renderOut("ra-out", Post("/revision/apply", body), (j) => kv([
    ["已应用", j.applied], ["diff", j.diff],
  ]) + dynTable(j.results) + objTable(j.objects));
});
$("rr-run").addEventListener("click", () => {
  const objs = rvObjs("rr-out");
  if (!objs) return;
  const body = { objects: objs };
  if ($("rr-canvas").value.trim()) {
    const c = parseJsonField("rr-canvas");
    if (!c.ok) { showLocalErr("rr-out", c.error); return; }
    body.canvas = c.value;
  }
  if ($("rr-brand").value.trim()) body.brand = { palette: $("rr-brand").value.split(",").map((s) => s.trim()).filter(Boolean) };
  if ($("rr-roles").value.trim()) body.roles = $("rr-roles").value.split(",").map((s) => s.trim()).filter(Boolean);
  renderOut("rr-out", Post("/review/run", body), (j) => kv([
    ["评审角色", j.reviewed_roles], ["评分", j.score],
    ["问题总数", j.summary && j.summary.total_issues],
    ["按严重度", j.summary && j.summary.by_severity],
    ["按角色", j.summary && j.summary.by_role],
  ]) + dynTable(j.issues) + hexChips(j));
});

/* ============================ 运维运行时 ============================ */
const opsShow = (path, r) => renderOut("ops-out", r, (j) =>
  `<div class="small muted">${esc(path)} · HTTP ${r.status}（响应由服务端脱敏后原样呈现）</div>` +
  `<pre>${esc(JSON.stringify(j, null, 2))}</pre>`);
document.querySelectorAll("#ops-btns [data-ops]").forEach((b) => {
  b.addEventListener("click", async () => {
    const path = b.dataset.ops;
    $("ops-out").innerHTML = `<span class="muted">GET ${esc(path)} …</span>`;
    const r = await api(path);
    opsShow(path, r);
  });
});
$("op-diag").addEventListener("click", async () => {
  const path = "/runtime/diagnostic?service_port=" + (+$("op-port").value || 8765);
  const r = await api(path);
  renderOut("ops-out", r, (j) =>
    `<div class="small muted">${esc(path)} · HTTP ${r.status}</div>` +
    kv([["状态", j.status], ["检查数", j.checks_performed], ["通过", j.checks_passed], ["失败", j.checks_failed]]) +
    dynTable(j.checks) + rawBlock(j));
});
$("op-selftest").addEventListener("click", async () => {
  const r = await Post("/diagnostics/run", {});
  renderOut("ops-out", r, (j) =>
    kv([["总检查", j.total], ["通过", j.passed], ["失败", j.failed], ["总体", j.overall_status]]) +
    dynTable(j.checks) + rawBlock(j));
});

/* ---- Phase 70：安装执行台账只读对账（GET，零参数零写入；执行动作只在 CLI 上） ---- */
/* 五形制逐名如实呈现；self_report_ignored / registry_keys / mismatches 逐字段回显。
 * 字段集随形制而变：ledger_absent 的回执不含 basis / preexisting / 台账字节与 sha256。
 * 缺什么就写「该形制不提供」，绝不把 undefined 或 "—" 渲染成一条观测值。
 * 本卡没有任何触发真装真卸的按钮——执行器结构上只经同一可执行文件的 CLI 显式同意令牌可达。 */
const iexCls = (st) => (st === "executed_verified" ? "ok"
  : st === "ledger_inconsistent" ? "err"
    : st === "ledger_absent" || st === "install_verified_awaiting_uninstall" ? "warn" : "mut");
const IEX_ABSENT = "（该形制不提供）";
const iexHas = (o, k) => Object.prototype.hasOwnProperty.call(o, k) && o[k] !== undefined;
const iexVal = (o, k) => (iexHas(o, k) ? o[k] : IEX_ABSENT);
const iexEmpty = (v) => (!v || (typeof v === "object" && Object.keys(v).length === 0));
$("iex-run").addEventListener("click", async () => {
  const box = $("iex-out");
  box.innerHTML = `<div class="small muted">GET /installer/execution …（只读对账，不发任何写请求）</div>`;
  const r = await api("/installer/execution");
  if (!r.ok) { box.innerHTML = errBox(r); return; }
  const j = r.json || {};
  const led = j.ledger || {};
  const mism = j.mismatches || [];
  box.innerHTML = badge("形制 · " + asText(j.status), iexCls(j.status)) +
    `<div class="small">五形制：ledger_absent（从未跑过，不伪造）/ executed_verified（装与卸都经现场复核）/ install_verified_awaiting_uninstall（已装待卸的诚实中间态）/ ledger_unverified（自报成功而现场不认）/ ledger_inconsistent（现场与台账矛盾，逐项点名）。判据 basis=${esc(asText(iexVal(j, "basis")))}，fabricated=${esc(asText(j.fabricated))}；本卡只呈现服务端回执，缺字段标注「${IEX_ABSENT}」而不臆造。</div>` +
    kv([["台账路径", led.path], ["台账存在", led.exists],
        ["台账字节", iexVal(led, "bytes")], ["台账 sha256", iexVal(led, "sha256")],
        ["自报字段（只回显不判定）", iexVal(j, "self_report_ignored")],
        ["注册表观测键", iexVal(j, "registry_keys")],
        ["逐项不一致", mism.length ? mism : "（空）"],
        ["现场重算", iexEmpty(j.recomputed) ? "（空 · 该形制不重算）" : j.recomputed],
        ["既有数据基线", iexVal(j, "preexisting")],
        ["原因", iexVal(j, "reason")]]) +
    rawBlock(j);
});

/* ---- Phase 65：端口可占用性真探测（POST，真 bind socket 试探，非查表也非推断） ---- */
/* 本地只拦「port 空 / 非整数字面」两种必然 4xx 的形制；其余一律真发请求，
 * 服务端 400（越界/非数值/host 空串）与 422（缺必填）的 detail 原文照抄，不改写不降级。 */
const p65Bad = (elId, msg) => { $(elId).innerHTML = `<div class="err-box">${esc(msg)} — 未发起请求</div>`; };
const p65Cls = (st) => (st === "connected" || st === "ok" ? "ok"
  : st === "not_configured" || st === "unreachable" ? "warn"
    : st === "forbidden" || st === "auth_required" ? "err" : "mut");
const p65Num = (v) => (v === "" || v === null || v === undefined ? undefined : Number(v));
$("op-pchk-btn").addEventListener("click", () => {
  const raw = $("op-pchk-port").value.trim();
  if (!raw) return p65Bad("ops-out", "port 未填写（该字段为 schema 必填，空体服务端必回 422）");
  if (!/^\d+$/.test(raw)) return p65Bad("ops-out", "port 须为整数字面，当前输入：" + raw);
  const host = $("op-pchk-host").value.trim();
  renderOut("ops-out", api("/runtime/port-check", {
    method: "POST", body: JSON.stringify({ port: +raw, host: host || undefined }),
  }), (j) => kv([
    ["schema", j.schema], ["host", j.host], ["port", j.port],
    ["available（该端口此刻可被 bind）", j.available], ["探测方式", j.method], ["fabricated", j.fabricated],
  ]) + badge(j.available === true ? "可占用" : j.available === false ? "已被占用" : "服务端未回 available 布尔",
    j.available === true ? "ok" : j.available === false ? "warn" : "mut") +
    `<div class="small">${esc(j.detail === undefined ? "—" : j.detail)}</div>` +
    `<div class="small muted">本探测只做一次真实 bind 试探即释放，不监听、不写入任何文件。available:false 是「已被占用」的观测事实，不是失败。</div>`);
});

/* ---- Phase 49：安装清单静态校验 + 安装态只读探测（第 26 引擎，零写入） ---- */
const mfUnionKeys = (rows) => {
  const seen = [];
  (rows || []).forEach((r) => Object.keys(r).forEach((k) => { if (!seen.includes(k)) seen.push(k); }));
  return seen;
};
const mfVerifyView = (j) => {
  const c = j.counts || {};
  const ex = j.execution || {};
  return kv([
    ["结论 verdict", j.verdict], ["schema", j.schema],
    ["校验对象", j.script_path], ["脚本字节", j.script_bytes], ["脚本 sha256", j.script_sha256],
    ["AppId", j.app_id], ["卸载注册表键", j.uninstall_registry_key],
    ["AppName", j.app_name], ["与服务默认名一致", j.app_name_matches_service_default],
    ["缺省安装目录 default_dir_name", j.default_dir_name],
    ["检出总数", c.total], ["error", c.errors], ["warning", c.warnings], ["info", c.infos],
    ["已解析节", j.sections_parsed], ["逐节条目数", j.entries_by_section],
    ["载荷根 payload_root", j.payload_root], ["运行时数据根", j.runtime_data_root],
    ["本进程数据根", j.service_data_root], ["iscc 可用", j.iscc_available],
    ["执行侧（本引擎不执行安装）", ex.status], ["执行归属", ex.owned_by],
    ["已执行 performed", ex.performed], ["伪造 fabricated", j.fabricated],
  ]) +
    `<div class="small muted">数据根口径：${esc(j.data_root_basis || "—")}</div>` +
    `<div class="small muted">执行边界：${esc(ex.reason || "—")}</div>` +
    (j.verdict === "manifest_ok"
      ? `<div class="small">清单与磁盘实况对账零检出（${badge("manifest_ok", "ok")}）。这是静态校验结论，不代表安装已执行。</div>`
      : `<div class="small">${badge(String(j.verdict), "err")} 检出 ${asText(c.total)} 项，逐条见下表。</div>`) +
    `<h3>逐条检出（id · severity · subject · basis · evidence）</h3>` +
    monoTable(j.findings, [
      ["id", (v) => v], ["severity", (v) => v], ["subject", (v) => v],
      ["basis", (v) => v], ["evidence", (v) => asText(v)],
    ]);
};
const mfStateView = (j) => kv([
  ["installed（仅由注册表卸载项真实命中派生）", j.installed],
  ["app_id", j.app_id], ["app_id 来源", j.app_id_source],
  ["app_name", j.app_name], ["app_name 来源", j.app_name_source],
  ["与服务默认名一致", j.app_name_matches_service_default],
  ["推导卸载键 derived_uninstall_key", j.derived_uninstall_key],
  ["环境 environment", j.environment], ["脚本路径 script_path", j.script_path],
  ["探测数 probes_seen", j.probes_seen],
  ["执行侧状态", j.execution && j.execution.status],
  ["伪造 fabricated", j.fabricated],
]) +
  `<div class="small muted">派生依据：${esc(j.installed_basis || "—")}</div>` +
  `<div class="small">${j.installed ? badge("已安装", "ok") : badge("未安装", "mut")}（辅助观察只回显事实，不参与 installed 结论）</div>` +
  `<h3>逐项只读观察值</h3>` +
  monoTable(j.probes, mfUnionKeys(j.probes).map((k) => [k, (v) => asText(v)]));
$("op-mf-verify").addEventListener("click", async () => {
  const sp = $("mf-path").value.trim();
  const path = "/installer/manifest/verify" + (sp ? "?script_path=" + encodeURIComponent(sp) : "");
  $("ops-out").innerHTML = `<span class="muted">GET ${esc(path)} …</span>`;
  const r = await api(path);
  if (!r.ok) {
    const d = r.json && r.json.detail;
    const reason = (d && typeof d === "object") ? d.reason : null;
    const why = r.status === 404 && reason === "installer_script_not_found"
      ? "冻结 EXE / 便携包不随包分发构建期 setup.iss，清单不可得即如实报不可得（looked_in 见原始响应），绝不伪造「已通过」。可在上方 script_path 显式指向本机一份 .iss 后重试。"
      : r.status === 404 ? "服务端如实报该路径不存在，非本服务缺陷。"
        : r.status === 403 ? "服务端拒绝越出允许根或命中系统目录黑名单的路径（只读安全边界：只校验本产品的安装清单），非本服务缺陷。"
          : r.status === 400 ? "服务端按清单契约拒绝该入参（后缀/形态/体积/编码/无 Section），非本服务缺陷。"
            : "如实呈现服务端 detail，不做本地兜底。";
    $("ops-out").innerHTML = errBox(r) + `<div class="small">${esc(why)}</div>` +
      (r.json ? rawBlock(r.json) : "");
    return;
  }
  renderOut("ops-out", r, mfVerifyView);
});
$("op-mf-state").addEventListener("click", async () => {
  const path = "/installer/state";
  $("ops-out").innerHTML = `<span class="muted">GET ${esc(path)} …</span>`;
  renderOut("ops-out", api(path), mfStateView);
});

/* —— Phase 53 第二刀：编译证据核验 + 安装预检（两枚只读 GET，零写入、零安装、零卸载） ——
   视图里每一行都取自本次真值探针打出的响应键树，未出现在响应里的字段一律不渲染；
   三态（true / false / null）分列显示，"未探测"绝不显示成"不通过"。 */
const p53sha = (v) => String(v || "—").slice(0, 16);
const p53pair = (label, o) => {
  const e = (o && o.expected) || {}, b = (o && o.observed) || {};
  return `<div class="small">${esc(label)}：证据 期望 ${esc(asText(e.bytes))} B / ${esc(p53sha(e.sha256))}… ` +
    `磁盘 实测 ${esc(asText(b.bytes))} B / ${esc(p53sha(b.sha256))}… ` +
    `${badge(o.bytes_match ? "字节符" : "字节不符", o.bytes_match ? "ok" : "err")} ` +
    `${badge(o.sha256_match ? "SHA 符" : "SHA 不符", o.sha256_match ? "ok" : "err")}` +
    (o.present === false ? ` ${badge("不在盘", "err")}` : "") +
    (o.unreadable_note ? `<div class="small muted">${esc(o.unreadable_note)}</div>` : "") + `</div>`;
};
const mfArtifactView = (j) => {
  const s = j.signals || {}, sc = j.script || {}, ac = j.artifact || {},
        w = j.warnings || {}, self = j.evidence_self_report || {},
        esrc = j.evidence_source || {}, ot = j.output_tail || {},
        cp = j.compiler || {}, mn = j.manifest_now || {}, ni = j.not_install || {};
  return kv([
    ["verdict", asText(j.verdict)],
    ["compile_succeeded（三信号同立，与证据自报无关）", asText(j.compile_succeeded)],
    ["信号① iscc 退出码为 0", asText(s.exit_zero)],
    ["信号② 输出尾部真见完成行", asText(s.compile_line_seen)],
    ["信号③ 产物在盘", asText(s.artifact_present)],
    ["iscc_exit_code（证据记录值）", asText(s.iscc_exit_code)],
    ["完成行出处 compile_line_source", asText(s.compile_line_source)],
    ["完成行原文", asText(s.compile_line_text)],
    ["证据自报（回显但不参与判定）", `compiled=${asText(self.compiled)} · fabricated=${asText(self.fabricated)} · ignored=${asText(self.ignored)}`],
    ["证据来源", `${asText(esrc.from)} · ${asText(esrc.path)} · ${asText(esrc.bytes)} B`],
    ["字段名与模式", `tail_key=${asText(esrc.tail_key)} · schema=${asText(esrc.schema)} · schema_match=${asText(esrc.schema_match)} · built_at=${asText(esrc.built_at)}`],
    ["输出尾部", `键=${asText(ot.key)} · 实存 ${asText(ot.chars)} 字符 · 记录总长 ${asText(ot.recorded_total_chars)} · ${asText(ot.provenance)}`],
    ["告警计数", `记录 ${asText(w.recorded_count)} 行 · 尾部实见 ${asText(w.tail_recount)} 行 · 相符=${asText(w.consistent)} · 尾部即全文=${asText(w.tail_is_full_output)}`],
    ["编译器", `记录路径 ${asText(cp.recorded_path)}（在盘 ${asText(cp.recorded_on_disk)}）· 本次寻径 found=${asText(cp.now_located && cp.now_located.found)} path=${asText(cp.now_located && cp.now_located.path)}`],
    ["编译后清单静态复校", `verdict=${asText(mn.verdict)} · counts=${asText(mn.counts)} · 与记录一致=${asText(mn.same_as_recorded)}（记录 ${asText(mn.recorded_verdict)}）`],
    ["缺失字段（只降本臂强度，不判 evidence_incomplete）", asText(j.missing_fields)],
    ["数据根口径", `service=${asText(j.service_data_root)} · installed=${asText(j.installed_data_root)}`],
    ["显式覆盖", asText(j.overrides)],
    ["产物在盘 ≠ 本机已安装", `installed_claimed=${asText(ni.installed_claimed)} · artifact_present=${asText(ni.artifact_present)} · fabricated=${asText(j.fabricated)}`],
  ]) +
    `<div class="small muted">${esc(s.basis || "")}</div>` +
    `<h3>逐字节对账（脚本与产物各比 bytes + sha256 两路）</h3>` +
    p53pair("脚本", sc) + p53pair("安装包产物", ac) +
    `<h3>阻断项（code · detail · basis；空即无阻断）</h3>` +
    monoTable(j.blocking, [["code", asText], ["detail", asText], ["basis", asText]]) +
    `<div class="small muted">${esc(ni.basis || "")}</div>`;
};
const mfPreflightView = (j) => {
  const c = j.checks || {}, pr = j.probe_request || {}, ni = j.not_install || {},
        ex = j.execution || {}, dk = c.disk_space || {}, wt = c.writability || {},
        pl = c.payload || {}, pf = c.privileges || {}, er = pf.elevation || {};
  const order = [["privileges", "权限（令牌实测 + 脚本声明）"],
                 ["install_root", "安装根候选（auto 常量成对解析）"],
                 ["target_conflict", "目标冲突（文件同名 / exe 占用）"],
                 ["writability", "可写性（默认不写探针）"],
                 ["artifact", "安装包产物（复用编译面结论）"],
                 ["disk_space", "磁盘余量（实测需求 vs 实测余量）"],
                 ["payload", "载荷（与静态校验 R2/R3 同判据）"]];
  const rows = order.filter(([k]) => c[k]).map(([k, name]) => ({
    check: name, state: c[k].result === true ? "通过" : c[k].result === false ? "不通过" : "未探测",
    code: asText(c[k].state), basis: c[k].basis || "",
  }));
  return kv([
    ["verdict", asText(j.verdict)],
    ["blocking_codes", asText(j.blocking_codes)],
    ["unknown_codes（未探测 ≠ 不可用）", asText(j.unknown_codes)],
    ["写探针请求", `asked=${asText(pr.asked)} · ran=${asText(pr.ran)} · override=${asText(pr.override)}`],
    ["探针依据", asText(pr.basis)],
    ["writability 明细", `target=${asText(wt.target)} · probed=${asText(wt.probed)} · detail=${asText(wt.detail)}`],
    ["权限三项", `declared=${asText(pf.declared)} · effective=${asText(pf.effective)} · elevated=${asText(pf.elevated)}`],
    ["令牌接缝", `available=${asText(er.available)} · elevation_type=${asText(er.elevation_type)} · token_elevation_err=${asText(er.token_elevation_err)} · is_admin_account=${asText(er.is_admin_account)}`],
    ["磁盘口径", `余量 ${asText(dk.free_bytes)} B · 需求 ${asText(dk.required_bytes)} B（产物 ${asText(dk.artifact_bytes)} + 载荷 ${asText(dk.payload_bytes)}）· 测量点 ${asText(dk.measured_on)}`],
    ["载荷", `声明 ${asText(pl.declared_sources)} 条 · 在盘 ${asText(pl.present_count)} · 缺失 ${asText(pl.missing_count)} · root=${asText(pl.payload_root)}`],
    ["脚本/证据", `script_path=${asText(j.script_path)} · evidence_provided=${asText(j.evidence_provided)} · evidence_path=${asText(j.evidence_path)}`],
    ["执行边界（本面绝不执行安装）", `status=${asText(ex.status)} · performed=${asText(ex.performed)} · fabricated=${asText(ex.fabricated)}`],
    ["界限", `preflight_is_not_install=${asText(ni.preflight_is_not_install)} · compile_is_not_install=${asText(ni.compile_is_not_install)} · installed_claimed=${asText(ni.installed_claimed)}`],
  ]) +
    `<h3>七路实测</h3>` +
    monoTable(rows, [["check", asText], ["state", asText], ["code", asText], ["basis", asText]]) +
    `<h3>安装根候选（成对给出，不猜唯一真值）</h3>` +
    monoTable((c.install_root || {}).candidates, [
      ["path", asText], ["constant", asText], ["inno_form", asText],
      ["declared", asText], ["exists", asText], ["unresolved", asText],
      ["resolved_via", asText],
    ]) +
    `<h3>阻断项 / 未探测项</h3>` +
    monoTable(j.blocking, [["code", asText], ["detail", asText], ["basis", asText]]) +
    monoTable(j.unknowns, [["code", asText], ["detail", asText], ["basis", asText]]) +
    `<div class="small">${badge("只读仪器", "mut")} 结论只描述「若执行安装则本机条件如何」，既不是安装结果也不是安装态；本机验收全程不安装、不卸载。</div>` +
    `<div class="small muted">${esc(ni.basis || "")}</div>`;
};
const p53Why = (r, reason) => {
  if (r.status === 404) return "服务端如实报该证据/脚本不可得（looked_in 见原始响应）：构建期工件不随冻结包分发，缺失即报缺失，绝不伪造「已证成」。";
  if (r.status === 403) return "服务端拒绝越出允许根的路径（只读安全边界：写探针结构上无法指向系统安装目录），非本服务缺陷。";
  if (r.status === 400) return "服务端按契约拒绝该入参（后缀/形态/体积/编码/非 JSON/探针参数取值），原文见 detail。";
  return "如实呈现服务端 detail，不做本地兜底。" + (reason ? " reason=" + reason : "");
};
$("op-mf-artifact").addEventListener("click", async () => {
  const q = [];
  const ep = $("mf-ev-path").value.trim();
  const sp = $("mf-path").value.trim();
  if (ep) q.push("evidence_path=" + encodeURIComponent(ep));
  if (sp) q.push("script_path=" + encodeURIComponent(sp));
  const path = "/installer/artifact" + (q.length ? "?" + q.join("&") : "");
  $("ops-out").innerHTML = `<span class="muted">GET ${esc(path)} …</span>`;
  const r = await api(path);
  if (!r.ok) {
    const d = r.json && r.json.detail;
    const reason = (d && typeof d === "object") ? d.reason : null;
    $("ops-out").innerHTML = errBox(r) + `<div class="small">${esc(p53Why(r, reason))}</div>` +
      (r.json ? rawBlock(r.json) : "");
    return;
  }
  renderOut("ops-out", r, mfArtifactView);
});
$("op-mf-preflight").addEventListener("click", async () => {
  const q = ["probe=" + ($("mf-probe").checked ? "true" : "false")];
  const ov = $("mf-override").value.trim();
  const ep = $("mf-ev-path").value.trim();
  const sp = $("mf-path").value.trim();
  if (ov) q.push("target_override=" + encodeURIComponent(ov));
  if (ep) q.push("evidence_path=" + encodeURIComponent(ep));
  if (sp) q.push("script_path=" + encodeURIComponent(sp));
  const path = "/installer/preflight?" + q.join("&");
  $("ops-out").innerHTML = `<span class="muted">GET ${esc(path)} …</span>`;
  const r = await api(path);
  if (!r.ok) {
    const d = r.json && r.json.detail;
    const reason = (d && typeof d === "object") ? d.reason : null;
    $("ops-out").innerHTML = errBox(r) + `<div class="small">${esc(p53Why(r, reason))}</div>` +
      (r.json ? rawBlock(r.json) : "");
    return;
  }
  renderOut("ops-out", r, mfPreflightView);
});

/* ============================ 流程与恢复（Phase 40） ============================ */
const WF_SAMPLE = {
  id: "demo-flow", name: "示例工作流", description: "输入 → 输出",
  nodes: [{ id: "in", type: "input", name: "输入", config: {} },
          { id: "out", type: "output", name: "输出", config: {} }],
  edges: [{ source_node_id: "in", target_node_id: "out" }],
  variables: { greet: "hello" },
};
$("wf-sample").addEventListener("click", () => {
  $("wf-def").value = JSON.stringify(WF_SAMPLE, null, 2);
});
const wfInstView = (j) => kv([
  ["实例", j.id], ["工作流", j.workflow_id], ["状态", j.status],
  ["当前节点", j.current_node], ["错误", j.error_message || "—"],
  ["结果", j.result], ["节点状态", j.node_status], ["日志条数", (j.logs || []).length],
]) + `<details><summary>执行日志（真实逐节点）</summary><pre>${esc((j.logs || []).join("\n"))}</pre></details>`;
$("wf-define").addEventListener("click", async () => {
  const p = parseJsonField("wf-def");
  if (!p.ok) return showLocalErr("wf-out", p.error);
  const r = await Post("/workflow/define", p.value);
  if (r.ok && r.json && r.json.workflow_id) $("wf-id").value = r.json.workflow_id;
  renderOut("wf-out", r, (j) => kv([
    ["workflow_id（已回填）", j.workflow_id], ["节点数", j.nodes], ["边数", j.edges],
    ["定义期校验错误", j.validation_errors && j.validation_errors.length ? j.validation_errors : "无"],
  ]));
});
$("wf-validate").addEventListener("click", async () => {
  const id = $("wf-id").value.trim();
  if (!id) return showLocalErr("wf-out", "workflow_id 为空（先定义或手动填写）");
  renderOut("wf-out", api(`/workflow/${encodeURIComponent(id)}/validate`),
    (j) => kv([["workflow_id", j.workflow_id], ["错误", j.errors && j.errors.length ? j.errors : "无"]]));
});
$("wf-visualize").addEventListener("click", async () => {
  const id = $("wf-id").value.trim();
  if (!id) return showLocalErr("wf-out", "workflow_id 为空");
  renderOut("wf-out", api(`/workflow/${encodeURIComponent(id)}/visualize`), (j) => kv([
    ["节点", (j.nodes || []).length], ["边", (j.edges || []).length],
    ["元数据", j.metadata],
  ]) + dynTable(j.nodes) + dynTable(j.edges));
});
$("wf-execute").addEventListener("click", async () => {
  const id = $("wf-id").value.trim();
  if (!id) return showLocalErr("wf-out", "workflow_id 为空");
  const p = parseJsonField("wf-input");
  if (!p.ok) return showLocalErr("wf-out", p.error);
  const r = await Post(`/workflow/${encodeURIComponent(id)}/execute`, { input_data: p.value || {} });
  if (r.ok && r.json && r.json.instance_id) $("wf-inst").value = r.json.instance_id;
  renderOut("wf-out", r, wfInstView);
});
$("wf-instance").addEventListener("click", async () => {
  const id = $("wf-inst").value.trim();
  if (!id) return showLocalErr("wf-out", "instance_id 为空（先执行或手动填写）");
  renderOut("wf-out", api(`/workflow/instance/${encodeURIComponent(id)}`), wfInstView);
});
const wfToolTable = (tools) => monoTable(tools, [
  ["tool", (v) => v], ["domain", (v) => v], ["coroutine", (v) => v ? "async" : "sync"],
  ["参数（派生自请求模型/签名，*=必填）", (v) => (v.params || []).map((p) =>
    p.name + (p.required ? "*" : "")).join(", ") || "—"],
  ["绑定端点", (v) => v.handler || "—"], ["请求模型", (v) => v.request_model || "—"],
]);
$("wf-tools").addEventListener("click", async () => {
  const r = await api("/workflow/tools");
  renderOut("wf-tools-out", r, (j) => {
    const q = $("wf-tools-q").value.trim().toLowerCase();
    const all = j.tools || [];
    const hit = q ? all.filter((t) => (t.tool + " " + t.domain).toLowerCase().includes(q)) : all;
    return kv([
      ["工具总数", j.total], ["领域数", j.domain_count],
      ["未挂端点元信息（如实标出）", (j.untagged || []).length ? j.untagged : "无"],
      ["派生来源", j.derived_from], ["API 路由总数", j.api_route_total],
      ["AI 调用器已注册", j.ai_invoker_registered],
      ["引用语义（取自引擎 _resolve_value 文档的语义行，派生而非手抄）", j.reference_syntax && j.reference_syntax.semantics],
      ["引用语法（引擎自己编译的那条正则，构成引用与否以此为准）", j.reference_syntax && j.reference_syntax.grammar],
      ["过滤", q ? `"${q}" → ${hit.length}/${all.length}` : "无"],
    ]) + monoTable(Object.entries(j.tools_per_domain || {}).map(([domain, count]) => ({ domain, count })),
      [["domain", (v) => v], ["count", (v) => v]]) + wfToolTable(hit);
  });
});
$("bt-run").addEventListener("click", async () => {
  const files = $("bt-files").value.split(/\r?\n/).map((s) => s.trim()).filter(Boolean);
  if (!files.length) return showLocalErr("bt-out", "文件路径列表为空");
  const r = await Post("/batch/process", {
    file_paths: files, operation: $("bt-op").value, workers: +$("bt-workers").value || 4,
  });
  renderOut("bt-out", r, (j) => kv([
    ["operation", j.operation], ["workers", j.workers], ["总数", j.total_files],
    ["成功", j.success], ["失败", j.failed], ["总耗时(s)", j.total_time],
    ["单文件均值(s)", j.avg_per_file],
  ]) + dynTable(j.results));
});
$("sn-create").addEventListener("click", async () => {
  const r = await Post("/recovery/snapshot", {
    document_path: $("sn-path").value.trim(), snapshot_type: $("sn-type").value,
  });
  if (r.ok && r.json && r.json.id) $("sn-id").value = r.json.id;
  renderOut("sn-out", r, (j) => kv([
    ["快照 id（已回填）", j.id], ["类型", j.snapshot_type], ["文档", j.document_path],
    ["SHA-256", j.checksum], ["字节数", j.size_bytes],
    ["可恢复", j.recoverable], ["原因", j.recovery_reason || "—"], ["备份路径", j.backup_path],
  ]));
});
const snapView = (j) => kv([["数量", j.count]]) + monoTable(j.snapshots, [
  ["id", (v) => String(v || "").slice(0, 13)],
  ["type", (s) => s.snapshot_type], ["doc", (s) => s.document_path],
  ["size", (s) => s.size_bytes], ["recoverable", (s) => s.recoverable],
  ["checksum", (s) => String(s.checksum || "").slice(0, 16)],
]);
$("sn-list").addEventListener("click", async () => {
  const dp = $("sn-path").value.trim();
  renderOut("sn-out", api("/recovery/snapshots" + (dp ? "?document_path=" + encodeURIComponent(dp) : "")), snapView);
});
$("sn-restore").addEventListener("click", async () => {
  const id = $("sn-id").value.trim();
  if (!id) return showLocalErr("sn-out", "snapshot_id 为空（先创建或手动填写）");
  if (!confirm("确认把快照 " + id + " 的备份字节写回原路径？这会覆盖当前文件，且不可撤销。")) return;
  renderOut("sn-out", Post(`/recovery/snapshot/${encodeURIComponent(id)}/restore`, {}), (j) => kv([
    ["snapshot_id", j.snapshot_id], ["文档", j.document_path],
    ["已还原（真实写回磁盘）", j.restored], ["原因", j.reason || "—"],
  ]));
});
$("tx-begin").addEventListener("click", async () => {
  const r = await Post("/recovery/transaction/begin", {
    task_id: $("tx-task").value.trim(), document_path: $("tx-doc").value.trim(),
  });
  if (r.ok && r.json && r.json.transaction_id) $("tx-id").value = r.json.transaction_id;
  renderOut("tk-out", r, (j) => kv([["transaction_id（已回填）", j.transaction_id], ["task_id", j.task_id]]));
});
$("tx-commit").addEventListener("click", async () => {
  const id = $("tx-id").value.trim();
  if (!id) return showLocalErr("tk-out", "transaction_id 为空");
  renderOut("tk-out", Post(`/recovery/transaction/${encodeURIComponent(id)}/commit`, {}),
    (j) => kv([["transaction_id", j.transaction_id], ["已提交", j.committed]]));
});
$("tx-rollback").addEventListener("click", async () => {
  const id = $("tx-id").value.trim();
  if (!id) return showLocalErr("tk-out", "transaction_id 为空");
  renderOut("tk-out", Post("/recovery/transaction/rollback", { transaction_id: id }), (j) => kv([
    ["transaction_id", j.transaction_id], ["已回滚", j.rolled_back],
    ["还原自快照", j.restored_from_snapshot || "—"], ["说明", j.note || "—"],
  ]));
});
$("tk-register").addEventListener("click", async () => {
  const r = await Post("/recovery/task/register", {
    task_id: $("tk-id").value.trim(), total_steps: +$("tk-steps").value || 1,
  });
  if (r.ok && r.json && r.json.task_id) $("tx-task").value = r.json.task_id;
  renderOut("tk-out", r, (j) => kv([["task_id", j.task_id], ["状态", j.status], ["总步数", j.total_steps]]));
});
$("tk-progress").addEventListener("click", async () => {
  const id = $("tk-id").value.trim();
  if (!id) return showLocalErr("tk-out", "task_id 为空");
  renderOut("tk-out", Post(`/recovery/task/${encodeURIComponent(id)}/progress`,
    { current_step: +$("tk-step").value || 0 }),
  (j) => kv([["task_id", j.task_id], ["current_step", j.current_step], ["total_steps", j.total_steps]]));
});
$("tk-complete").addEventListener("click", async () => {
  const id = $("tk-id").value.trim();
  if (!id) return showLocalErr("tk-out", "task_id 为空");
  renderOut("tk-out", Post(`/recovery/task/${encodeURIComponent(id)}/complete`, {}),
    (j) => kv([["task_id", j.task_id], ["状态", j.status]]));
});
$("tk-fail").addEventListener("click", async () => {
  const id = $("tk-id").value.trim();
  if (!id) return showLocalErr("tk-out", "task_id 为空");
  renderOut("tk-out", Post(`/recovery/task/${encodeURIComponent(id)}/fail`,
    { error_message: "由 Web 控制台标记失败", recoverable: true }),
  (j) => kv([["task_id", j.task_id], ["状态", j.status]]));
});
$("tk-report").addEventListener("click", async () => {
  const id = $("tk-id").value.trim();
  if (!id) return showLocalErr("tk-out", "task_id 为空");
  renderOut("tk-out", api(`/recovery/task/${encodeURIComponent(id)}/report`), (j) => kv([
    ["task_id", j.task_id], ["状态", j.status],
    ["进度", `${j.current_step}/${j.total_steps}`], ["错误", j.error_message || "—"],
    ["心跳", j.last_heartbeat], ["快照数", j.available_snapshots],
    ["检查点快照", j.checkpoint_snapshot || "—"], ["建议", j.recommendations],
  ]));
});
$("tk-orphaned").addEventListener("click", async () => {
  renderOut("tk-out", api("/recovery/tasks/orphaned"), (j) =>
    kv([["悬空任务数", j.count]]) + dynTable(j.tasks));
});

/* ============================ 实体明细联查（Phase 42） ============================ */
/* 以下 8 端点经 p42_probe 数据根全树哈希快照实测零副作用；幽灵 id 诚实 404。 */
const p42Need = (elId, msg) => {
  $(elId).innerHTML = `<div class="err-box">${esc(msg)} — 未发起请求</div>`;
};
$("dt-report-btn").addEventListener("click", () => {
  const id = $("dt-report").value.trim();
  if (!id) return p42Need("dt-eval-out", "report_id 未填写");
  renderOut("dt-eval-out", api(`/eval/reports/${encodeURIComponent(id)}`), (j) =>
    kv([["report_id", j.report_id], ["honest", j.honest],
        ["started_at", j.started_at], ["finished_at", j.finished_at]]) +
    dynTable(Object.entries(j.categories || {}).map(([c, v]) => ({ 类别: c, ...v }))));
});
const p42CmpView = (j) =>
  kv([["报告 A", j.a], ["报告 B", j.b], ["pass_rate_delta", j.pass_rate_delta],
      ["regressed", asText(j.regressed)], ["improved", asText(j.improved)]]) +
  (Array.isArray(j.flips) ? dynTable(j.flips) : `<div class="small">${esc(asText(j.flips))}</div>`) +
  `<div class="small muted">shared_cases=${esc(asText(j.shared_cases))} · incomparable_only_in=${esc(asText(j.incomparable_cases_only_in))}</div>`;
$("dt-cmp-btn").addEventListener("click", () => {
  const a = $("dt-cmp-a").value.trim(), b = $("dt-cmp-b").value.trim();
  if (!a || !b) return p42Need("dt-eval-out", "两个报告 id 缺一不可");
  renderOut("dt-eval-out", Post("/eval/compare", { a, b }), p42CmpView);
});
$("dt-cmp-run2")?.addEventListener("click", async () => {
  const b = $("dt-cmp-run2");
  b.disabled = true;
  try {
    const ids = [];
    for (let i = 0; i < 2; i++) {
      $("dt-eval-out").innerHTML = `<div class="small">真实评测进行中（第 ${i + 1}/2 次，POST /eval/run）…</div>`;
      const r = await Post("/eval/run", {});
      if (!r.ok) { $("dt-eval-out").innerHTML = errBox(r); return; }
      ids.push(r.json.report_id);
    }
    $("dt-cmp-a").value = ids[0]; $("dt-cmp-b").value = ids[1];
    renderOut("dt-eval-out", Post("/eval/compare", { a: ids[0], b: ids[1] }), p42CmpView);
  } finally { b.disabled = false; }
});
$("dt-kn-btn").addEventListener("click", () => {
  const id = $("dt-kn").value.trim();
  if (!id) return p42Need("dt-lib-out", "知识条目 id 未填写");
  renderOut("dt-lib-out", api(`/knowledge/${encodeURIComponent(id)}`), (j) =>
    kv([["id", j.id], ["title", j.title], ["scope", j.scope], ["status", j.status],
        ["char_count", j.char_count], ["chunk_count", j.chunk_count], ["created_at", j.created_at]]));
});
$("dt-as-btn").addEventListener("click", () => {
  const id = $("dt-as").value.trim();
  if (!id) return p42Need("dt-lib-out", "素材 id 未填写");
  renderOut("dt-lib-out", api(`/assets/${encodeURIComponent(id)}`), (j) =>
    kv([["id", j.id], ["name", j.name], ["format", j.format], ["type", j.type],
        ["size_bytes", j.size_bytes], ["尺寸", asText(j.size)], ["license", asText(j.license)],
        ["embedding 索引", j.embedding && j.embedding.index_status], ["created_at", j.created_at]]));
});
const p42SkView = (j) =>
  kv([["skill_id", j.id || j.skill_id], ["version", j.version], ["risk", j.risk],
      ["permissions", asText(j.permissions)], ["tools", asText(j.tools)],
      ["valid", j.valid]] ) +
  `<div class="small">validation_errors：${(j.validation_errors || []).length === 0
    ? badge("无", "ok") : `<ul class="small">${(j.validation_errors || []).map((e) => `<li>${esc(e)}</li>`).join("")}</ul>`}</div>`;
$("dt-sk-btn").addEventListener("click", () => {
  const id = $("dt-sk").value.trim();
  if (!id) return p42Need("dt-ops-out", "skill_id 未填写");
  renderOut("dt-ops-out", api(`/skills/${encodeURIComponent(id)}`), p42SkView);
});
$("dt-sk-val").addEventListener("click", () => {
  const id = $("dt-sk").value.trim();
  if (!id) return p42Need("dt-ops-out", "skill_id 未填写");
  renderOut("dt-ops-out", Post(`/skills/${encodeURIComponent(id)}/validate`, {}), p42SkView);
});
$("dt-flag-btn").addEventListener("click", () => {
  const key = $("dt-flag").value.trim();
  if (!key) return p42Need("dt-ops-out", "flag key 未填写");
  renderOut("dt-ops-out", api(`/flags/${encodeURIComponent(key)}`), (j) =>
    kv([["key", j.key], ["value", asText(j.value)]]));
});
$("dt-font-btn").addEventListener("click", () => {
  const ref = $("dt-font").value.trim();
  if (!ref) return p42Need("dt-ops-out", "字体 ref 未填写");
  renderOut("dt-ops-out", api(`/typography/fonts/${encodeURIComponent(ref)}`), (j) =>
    kv([["id", j.id], ["family", j.family], ["style", j.style], ["weight", j.weight],
        ["units_per_em", j.units_per_em], ["ascent/descent", `${j.ascent}/${j.descent}`],
        ["has_cjk", j.has_cjk], ["has_latin", j.has_latin], ["file_size", j.file_size]]));
});

/* ============================ 配置与写操作（Phase 45） ============================ */
/* 13 个写侧端点经 p45_probe2 契约探针钉死：合法载荷真实响应、敌意载荷一律 4xx、
 * 落盘副作用只发生在本服务数据根内且快照 diff 可证；副作用即端点设计意图，仅显式点击触发。 */
const p45Bad = (elId, msg) => { $(elId).innerHTML = `<div class="err-box">${esc(msg)} — 未发起写入</div>`; };
$("wr-cfg-btn").addEventListener("click", () => {
  const key = $("wr-cfg-key").value.trim();
  if (!key) return p45Bad("wr-cfg-out", "配置键未填写");
  let v = $("wr-cfg-val").value.trim();
  try { v = JSON.parse(v); } catch (_) { /* 非 JSON 按原始字符串写入 */ }
  renderOut("wr-cfg-out", api(`/config/${encodeURIComponent(key)}`, { method: "PUT", body: JSON.stringify({ value: v }) }),
    (j) => kv([["已写入键", key], ["配置项总数", Object.keys(j.config || {}).length]]));
});
$("wr-pv-cfg-btn").addEventListener("click", () => {
  const id = $("wr-pv-id").value.trim(), url = $("wr-pv-url").value.trim();
  if (!id || !url) return p45Bad("wr-pv-out", "provider_id 与 base_url 缺一不可");
  renderOut("wr-pv-out", Post("/provider/configure", {
    provider_id: id, base_url: url, api_key: $("wr-pv-key").value, model: $("wr-pv-model").value.trim() || undefined,
  }), (j) => {
    const p = (j.providers || {})[id] || {};
    return kv([["provider", p.id], ["base_url", p.base_url], ["model", p.model],
      ["api_key_masked", p.api_key_masked], ["key_storage", p.key_storage], ["has_key", p.has_key]]) +
      `<div class="small muted">明文密钥经 DPAPI 加密落盘，视图仅回脱敏串。</div>`;
  });
});
$("wr-pv-health-btn").addEventListener("click", () => {
  const id = $("wr-pv-id").value.trim();
  if (!id) return p45Bad("wr-pv-out", "provider_id 未填写");
  renderOut("wr-pv-out", Post("/provider/health", { provider_id: id, timeout: 5 }), (j) =>
    kv([["provider_id", id], ["status", j.status], ["reachable", j.reachable], ["fabricated", j.fabricated]]) +
    (j.detail ? `<div class="small">${esc(j.detail)}</div>` : ""));
});
$("wr-nas-cfg-btn").addEventListener("click", () => {
  const id = $("wr-nas-id").value.trim(), target = $("wr-nas-target").value.trim();
  if (!id || !target) return p45Bad("wr-nas-out", "target_id 与 target 缺一不可");
  renderOut("wr-nas-out", Post("/nas/configure", {
    target_id: id, target, kind: $("wr-nas-kind").value, share_root: $("wr-nas-root").value.trim() || undefined,
  }), (j) => kv([["configured", j.configured], ["target_id", j.target_id], ["kind", j.kind],
    ["target", j.target], ["has_share_root", j.has_share_root], ["has_credentials", j.has_credentials]]));
});
$("wr-nas-forget-btn").addEventListener("click", () => {
  const id = $("wr-nas-id").value.trim();
  if (!id) return p45Bad("wr-nas-out", "target_id 未填写");
  renderOut("wr-nas-out", Post("/nas/forget", { target_id: id }), (j) =>
    kv([["removed", j.removed], ["target_id", j.target_id], ["fabricated", j.fabricated]]));
});
$("wr-nas-clear-btn").addEventListener("click", () => {
  renderOut("wr-nas-out", Post("/nas/cache/clear", {}), (j) =>
    kv([["cleared", j.cleared], ["fabricated", j.fabricated]]));
});

/* ============================ Phase 65 扩接线：NAS 实探四枚 + 图层改/删 ============================ */
/* 四条 NAS POST 经 p65_contract_probe 真请求真响应实证：HTTP 码恒为 200，结论只在 status
 * 字段里（connected / ok / not_configured / unreachable / forbidden / not_supported_for_webdav）。
 * 因此徽标语色一律取自 status 而非 HTTP 码——把 200 统统染绿就是「自报即事实」的假成功形制。 */
const p65Entries = (rows) => {
  if (!rows || !rows.length)
    return `<div class="small muted">（entries 为空——本次未列出任何条目，不虚报数量）</div>`;
  return monoTable(rows, mfUnionKeys(rows).map((k) => [k, asText]));
};
$("wr-nas-conn-btn").addEventListener("click", () => {
  const id = $("wr-nas-id").value.trim();
  if (!id) return p65Bad("wr-nas-out", "target_id 未填写（schema 必填，服务端必回 422）");
  const to = p65Num($("wr-nas-timeout").value.trim());
  renderOut("wr-nas-out", api("/nas/connect", {
    method: "POST", body: JSON.stringify({ target_id: id, timeout: to }),
  }), (j) => kv([
    ["target_id", j.target_id], ["kind", j.kind], ["target", j.target], ["status", j.status],
    ["access", j.access], ["is_dir", j.is_dir], ["http_status", j.http_status], ["server", j.server],
    ["fabricated", j.fabricated], ["reason", j.reason], ["note", j.note],
  ]) + badge(asText(j.status), p65Cls(j.status)) +
    `<div class="small muted">可达性由真实探测得出：映射盘/UNC 经操作系统网络重定向器，WebDAV 走 PROPFIND Depth:0。未配置/不可达/需凭据皆如实回原文，绝不凭「配了地址」谎报 connected。</div>`);
});
$("wr-nas-list-btn").addEventListener("click", () => {
  const id = $("wr-nas-id").value.trim();
  if (!id) return p65Bad("wr-nas-out", "target_id 未填写（schema 必填，服务端必回 422）");
  const to = p65Num($("wr-nas-timeout").value.trim());
  renderOut("wr-nas-out", api("/nas/list", {
    method: "POST", body: JSON.stringify({
      target_id: id, subpath: $("wr-nas-sub").value.trim(), timeout: to,
      use_cache: $("wr-nas-cache").value === "true",
    }),
  }), (j) => kv([
    ["target_id", j.target_id], ["status", j.status], ["subpath", j.subpath], ["path", j.path],
    ["count", j.count], ["cache", j.cache], ["cached_ts", j.cached_ts],
    ["fabricated", j.fabricated], ["reason", j.reason],
  ]) + badge(asText(j.status), p65Cls(j.status)) + p65Entries(j.entries) +
    `<div class="small muted">条目逐字来自本次真实枚举（filesystem 取 name/type/size/mtime，WebDAV 解析 PROPFIND multistatus）。越出沙箱根的子路径如实回 forbidden 且零条目。</div>`);
});
$("wr-nas-lock-btn").addEventListener("click", () => {
  const id = $("wr-nas-id").value.trim(), sub = $("wr-nas-sub").value.trim();
  if (!id || !sub) return p65Bad("wr-nas-out", "target_id 与 subpath 缺一不可（两者皆为必填，缺即 422）");
  renderOut("wr-nas-out", api("/nas/lock", {
    method: "POST", body: JSON.stringify({ target_id: id, subpath: sub }),
  }), (j) => kv([
    ["target_id", j.target_id], ["subpath", j.subpath], ["status", j.status], ["path", j.path],
    ["locked（已被其他进程持有）", j.locked], ["fabricated", j.fabricated], ["reason", j.reason],
  ]) + badge(asText(j.status), p65Cls(j.status)) +
    `<div class="small">${j.locked === true ? "真持锁探测：该文件当前被其他进程独占，请勿并发写入。"
      : j.locked === false ? "真持锁探测：已获取并立即释放独占区域锁 ⇒ 未被占用。"
        : "服务端未回 locked 布尔（如 WebDAV 无本地句柄可锁），锁态以 reason 原文为准，本界面不推断。"}</div>`);
});
$("wr-nas-reconn-btn").addEventListener("click", () => {
  const id = $("wr-nas-id").value.trim();
  if (!id) return p65Bad("wr-nas-out", "target_id 未填写（schema 必填，服务端必回 422）");
  const at = p65Num($("wr-nas-attempts").value.trim());
  const to = p65Num($("wr-nas-timeout").value.trim());
  renderOut("wr-nas-out", api("/nas/reconnect", {
    method: "POST", body: JSON.stringify({ target_id: id, attempts: at, timeout: to }),
  }), (j) => kv([
    ["target_id", j.target_id], ["status", j.status], ["kind", j.kind], ["target", j.target],
    ["recovered", j.recovered], ["attempts_made", j.attempts_made], ["backoff_seconds", j.backoff_seconds],
    ["access", j.access], ["is_dir", j.is_dir], ["fabricated", j.fabricated], ["reason", j.reason], ["note", j.note],
  ]) + badge(j.recovered === true ? "已恢复" : j.recovered === false ? "未恢复" : "服务端未回 recovered",
    j.recovered === true ? "ok" : j.recovered === false ? "warn" : "mut") +
    `<div class="small muted">attempts_made 与 backoff_seconds 均为真实重试次数的观测值；不可达时如实报未恢复，绝不自称重试成功。</div>`);
});

/* ---- Phase 65：图层 PATCH（只拼填了的字段）与 DELETE（L3 门禁，须显式勾选） ---- */
/* PATCH 纪律：服务端按「字段为 null 即不改」派生补丁，故本界面只把您填过的字段放进请求体，
 * 五格全空时本地即拒绝——那等于发一个空 PATCH，改不了任何东西却会让人以为改过了。 */
$("wr-ly-patch-btn").addEventListener("click", () => {
  const id = $("wr-ly-id").value.trim();
  if (!id) return p65Bad("wr-ly-out", "layer_id 未填写");
  const body = {};
  const nm = $("wr-ly-name").value.trim(); if (nm) body.name = nm;
  const opq = $("wr-ly-opacity").value.trim();
  if (opq !== "") {
    if (!/^-?\d+(\.\d+)?$/.test(opq)) return p65Bad("wr-ly-out", "opacity 须为数值，当前输入：" + opq);
    body.opacity = +opq;
  }
  const vis = $("wr-ly-visible").value; if (vis !== "") body.visible = vis === "true";
  const lck = $("wr-ly-locked").value; if (lck !== "") body.locked = lck === "true";
  const bl = $("wr-ly-blend").value; if (bl) body.blend_mode = bl;
  if (!Object.keys(body).length)
    return p65Bad("wr-ly-out", "name / opacity / visible / locked / blend_mode 五格皆未填 ⇒ 无字段可改，未发起请求");
  renderOut("wr-ly-out", api(`/layers/${encodeURIComponent(id)}`, { method: "PATCH", body: JSON.stringify(body) }), (j) =>
    kv([["id", j.id], ["name", j.name], ["type", j.type], ["visible", j.visible],
      ["locked", j.locked], ["opacity", j.opacity], ["blend_mode", j.blend_mode]]) +
      `<div class="small muted">视图为服务端回读的真实图层记录（只含本次提交的字段被改动）。图层 id 不存在时如实 404，不静默新建。</div>`);
});
/* DELETE 纪律：confirm 是服务端的 L3 门禁（缺省 / 空 / false / 近似值一律 400 且零副作用）。
 * 未勾选时本界面连请求都不发——把门禁推到界面上，是对「点了删除按钮就删了」这种误读的第二次保险。 */
$("wr-ly-del-btn").addEventListener("click", () => {
  const id = $("wr-ly-id").value.trim();
  if (!id) return p65Bad("wr-ly-out", "layer_id 未填写");
  if (!$("wr-ly-confirm").checked)
    return p65Bad("wr-ly-out", "未勾选「确认删除该图层」⇒ 删除为 L3 不可逆操作，本地即拒绝，请求未发出");
  renderOut("wr-ly-out", api(`/layers/${encodeURIComponent(id)}?confirm=true`, { method: "DELETE" }), (j) =>
    kv([["deleted", j.deleted], ["layer_id", j.layer_id], ["risk_level", j.risk_level]]) +
      `<div class="small muted">deleted:true 由服务端真实删除返回；图层在文档与磁盘快照两侧同时消失，可在运维面板「当前文档 / 图层清单」复核。</div>`);
});
$("wr-uc-btn").addEventListener("click", () => {
  const ch = $("wr-uc-chan").value, feed = $("wr-uc-feed").value.trim();
  if (!feed) return p45Bad("wr-uc-out", "feed_url 未填写");
  renderOut("wr-uc-out", Post("/update/configure", {
    channel: ch, feed_url: feed, secret: $("wr-uc-secret").value || undefined,
    current_version: $("wr-uc-ver").value.trim() || undefined,
  }), (j) => kv([["channel", j.channel], ["configured", j.configured], ["feed_url", j.feed_url],
    ["has_secret", j.has_secret], ["current_version", j.current_version]]));
});
$("wr-ck-btn").addEventListener("click", () => {
  renderOut("wr-uc-out", Post("/update/check", {
    channel: $("wr-uc-chan").value, current_version: $("wr-uc-ver").value.trim() || "1.0.0", timeout: 8,
  }), (j) => kv([["channel", j.channel], ["status", j.status], ["verified", j.verified],
    ["fabricated", j.fabricated], ["reason", j.reason], ["remote_version", j.remote_version]]));
});
/* ---- Phase 71：接线收尾八枚（runtime/single-instance · uxp 四枚 · nas/download · update/download+apply） ----
 * 写侧五条纪律逐枚兑现：契约形制按 p71_1b 探针真响应冻结（字段名不猜，缺字段不臆造）；敌意/降级
 * 载荷一律原样呈现（502 的 detail 对象逐形认出、fabricated 随原文回显，绝不画成成功）；副作用只在
 * 点击时发生；update/apply 为 L3 级替换动作，请求发出前必须连过两道 confirm；侧效应落点逐卡写明。 */
const p71D = (r) => (r.json && r.json.detail && typeof r.json.detail === "object" ? r.json.detail : null);
const P71_HONEST_502 = ["auth_failed", "no_host_connected", "capability_not_negotiated", "not_registered"];
const p71Fail = (r, elId) => {
  const d = p71D(r);
  if (r.status === 502 && d && P71_HONEST_502.includes(d.status)) {
    $(elId).innerHTML = `<div class="err-box">HTTP 502 · ${esc(d.status)} · fabricated=${esc(d.fabricated)} — 服务端诚实降级（无 Adobe 宿主/未握手时即此形制），结论只在 detail 里，原文见下方</div>` + rawBlock(d);
    return;
  }
  $(elId).innerHTML = errBox(r);
};
const p71St = (j) => badge("status · " + asText(j.status), j.status === "ok" || j.status === "connected" || j.status === "downloaded" || j.status === "applied" ? "ok"
  : j.status === "forbidden" || j.status === "integrity_failed" || j.status === "auth_required" ? "err" : "warn");
let p71HostId = null;

/* ① 运行时上锁（进程级语义）：acquire 后随即 release，探测即归还，不滞留一把锁 */
$("nsl-run").addEventListener("click", async () => {
  const name = $("nsl-name").value.trim() || "zaojing-console-lock";
  const box = $("nsl-out");
  box.innerHTML = `<div class="small muted">POST /runtime/single-instance acquire→release …（显式点击才发生）</div>`;
  const r = await Post("/runtime/single-instance", { name: name, action: "acquire" });
  if (!r.ok) { box.innerHTML = errBox(r); return; }
  const j = r.json || {};
  const rel = await Post("/runtime/single-instance", { name: name, action: "release" });
  box.innerHTML = badge(j.acquired === true ? "acquire 已获取（随即 release 归还）" : "acquire 未获取", j.acquired === true ? "ok" : "warn") +
    kv([["schema", j.schema], ["acquired", j.acquired], ["already_running", j.already_running],
        ["backend", j.backend], ["pid", j.pid], ["name", j.name],
        ["release.released", rel.ok ? (rel.json || {}).released : undefined], ["fabricated", j.fabricated]]) +
    (rel.ok ? "" : errBox(rel)) + rawBlock(j);
});

/* ② UXP 宿主桥四动作：token 由用户从本机发现文件粘贴；无宿主时 502 三形逐一认出 */
$("uxp-hand").addEventListener("click", async () => {
  const box = $("uxp-out");
  box.innerHTML = `<div class="small muted">POST /uxp/handshake …（登记本机的 UXP 宿主）</div>`;
  const r = await Post("/uxp/handshake", {
    host: "photoshop", version: "26.0", capabilities: { createDocument: true },
    token: $("uxp-token").value.trim() || undefined,
  });
  if (!r.ok) return p71Fail(r, "uxp-out");
  const j = r.json || {};
  p71HostId = j.host_id || null;
  box.innerHTML = badge("已连接 · host_id=" + asText(j.host_id), "ok") +
    kv([["status", j.status], ["host_id", j.host_id], ["session（服务端脱敏，非明文）", j.session],
        ["capabilities", j.capabilities], ["advisory", j.advisory], ["gating", j.gating], ["fabricated", j.fabricated]]) +
    `<div class="small muted">token 只经本机输入框传递，本界面不回显明文；握手后 poll/dispatch 可直接用。</div>` + rawBlock(j);
});
$("uxp-poll").addEventListener("click", async () => {
  /* P71 活体真缺陷 #1 修（修产品不修断言）：服务端 UxpPollRequest.host_id 是必填 str（契约 B5 冻结），
   * 旧形制把 host_id 写成 `p71HostId || undefined` ⇒ 未握手时该键被 JSON 整体丢弃 ⇒ 服务端回 422
   * 校验数组并裸呈进界面。改为与本页 nasd/upd 两卡同款的本地必填守卫：前提不在场就不发起请求；
   * 服务端模型不放宽（放宽即口径滑坡）。 */
  if (!p71HostId) return p65Bad("uxp-out", "host_id 为服务端必填（UxpPollRequest.host_id: str）⇒ 本会话尚未握手取得 host_id，不替您编一个 ⇒ 先点上方「握手」");
  const box = $("uxp-out");
  box.innerHTML = `<div class="small muted">POST /uxp/poll …（宿主队列读腿，短超时后如实返回）</div>`;
  const r = await Post("/uxp/poll", {
    host_id: p71HostId, token: $("uxp-token").value.trim() || undefined, timeout: 2.0,
  });
  if (!r.ok) return p71Fail(r, "uxp-out");
  const j = r.json || {};
  const n = (j.commands || []).length;
  box.innerHTML = badge(n ? "取到 " + n + " 条待执行命令" : "无待执行命令", n ? "ok" : "mut") +
    kv([["status", j.status], ["count", j.count], ["commands", j.commands], ["fabricated", j.fabricated]]) + rawBlock(j);
});
$("uxp-disp").addEventListener("click", async () => {
  const box = $("uxp-out");
  box.innerHTML = `<div class="small muted">POST /uxp/dispatch …（派发真桥接工具 ps.document.get，等 Adobe 宿主执行）</div>`;
  const r = await Post("/uxp/dispatch", { tool: "ps.document.get", args: {}, timeout: 3.0 });
  if (!r.ok) return p71Fail(r, "uxp-out");
  const j = r.json || {};
  box.innerHTML = p71St(j) +
    kv([["tool", j.tool], ["request_id", j.request_id], ["status", j.status], ["host", j.host],
        ["result", j.result], ["reconciled", j.reconciled], ["error", j.error], ["detail", j.detail],
        ["fabricated", j.fabricated]]) + rawBlock(j);
});
$("uxp-res").addEventListener("click", async () => {
  const box = $("uxp-out");
  const rid = "console-probe-" + Date.now();
  box.innerHTML = `<div class="small muted">POST /uxp/result …（上报 request_id=${esc(rid)}）</div>`;
  const r = await Post("/uxp/result", {
    request_id: rid, token: $("uxp-token").value.trim() || undefined, status: "ok", payload: {},
  });
  const j = (r.json && typeof r.json === "object") ? r.json : {};
  if (!r.ok) return p71Fail(r, "uxp-out");
  box.innerHTML = badge(j.status === "unknown_request" ? "未知 request_id 如实回报 unknown_request" : "回执已记录",
      j.status === "unknown_request" ? "warn" : "ok") +
    kv([["status", j.status], ["request_id（服务端回显）", j.request_id], ["fabricated", j.fabricated]]) + rawBlock(j);
});

/* ③ NAS 下载：dest_dir 由用户显式给定；越界 forbidden 零落盘、未知 target not_configured */
$("nasd-run").addEventListener("click", async () => {
  const tid = $("nasd-tid").value.trim(), sub = $("nasd-sub").value.trim(), dest = $("nasd-dest").value.trim();
  if (!tid || !sub || !dest) return p65Bad("nasd-out", "target_id / subpath / dest_dir 三项必填（落盘目录不替您挑）⇒ 未发起请求");
  const box = $("nasd-out");
  box.innerHTML = `<div class="small muted">POST /nas/download …（显式点击才发生）</div>`;
  const r = await Post("/nas/download", { target_id: tid, subpath: sub, dest_dir: dest });
  if (!r.ok) { box.innerHTML = errBox(r); return; }
  const j = r.json || {};
  const cls = j.status === "forbidden" || j.status === "not_configured" ? "err"
    : j.downloaded === true ? "ok" : "warn";
  box.innerHTML = badge("status · " + asText(j.status), cls) +
    kv([["downloaded", j.downloaded], ["sha256", j.sha256], ["bytes", j.bytes], ["path", j.path],
        ["integrity", j.integrity], ["target_id", j.target_id], ["subpath", j.subpath],
        ["reason", j.reason], ["fabricated", j.fabricated]]) +
    (j.status === "forbidden" ? `<div class="small muted">subpath 越出沙箱根被服务端拒绝，本机零落盘（downloaded=false 为服务端事实，非界面推断）。</div>` : "") +
    (j.status === "not_configured" ? `<div class="small muted">该 target_id 未注册 ⇒ 上方「注册目标」或运维面板「NAS 目标」可先核对。</div>` : "") + rawBlock(j);
});

/* ④ 更新暂存下载 + 原子应用（apply 为 L3 级，双 confirm 门禁） */
$("upd-dl").addEventListener("click", async () => {
  const box = $("upd-out");
  box.innerHTML = `<div class="small muted">POST /update/download …（真外网下载并做 SHA-256 校验）</div>`;
  const r = await Post("/update/download", { channel: $("upd-chan").value, timeout: 20 });
  if (!r.ok) { box.innerHTML = errBox(r); return; }
  const j = r.json || {};
  const cls = j.downloaded === true ? "ok" : j.status === "no_update_configured" ? "warn" : "err";
  box.innerHTML = badge("status · " + asText(j.status), cls) +
    kv([["verified", j.verified], ["downloaded", j.downloaded], ["integrity", j.integrity],
        ["remote_version", j.remote_version], ["bytes", j.bytes], ["staged_path", j.staged_path],
        ["sha256", j.sha256], ["reason", j.reason], ["fabricated", j.fabricated]]) +
    (j.status === "no_update_configured" ? `<div class="small muted">该渠道未配置 feed ⇒ 诚实降级，无文件落盘；先在上方「配置更新渠道」填 feed_url。</div>` : "") + rawBlock(j);
});
async function updApply() {
  const stg = $("upd-stg").value.trim(), tgt = $("upd-tgt").value.trim(), sha = $("upd-sha").value.trim();
  if (!stg || !tgt || !sha) return p65Bad("upd-out", "staged_path / target_path / expected_sha256 三项必填 ⇒ 未发起请求");
  if (!confirm("L3 级动作 · 第一道确认：/update/apply 将把暂存包原子替换写入下面这个目标文件\n" + tgt + "\n（目标被占用时服务端可能回 pending_restart。）确定继续？")) return;
  if (!confirm("第二道确认：替换不可由本界面撤销。expected_sha256 与暂存包不符时服务端会拒绝并回滚（integrity_failed，目标逐字节不被改坏）。仍要执行？")) return;
  const box = $("upd-out");
  box.innerHTML = `<div class="small muted">POST /update/apply …（两道 confirm 已过，请求发出）</div>`;
  const r = await Post("/update/apply", { staged_path: stg, target_path: tgt, expected_sha256: sha });
  if (!r.ok) {
    const d = p71D(r);
    box.innerHTML = (d && d.status === "integrity_failed")
      ? `<div class="err-box">HTTP ${r.status} · integrity_failed · SHA-256 不符 ⇒ 服务端拒绝并已回滚，目标未被改坏（applied=false）。原文见下方</div>` + rawBlock(d)
      : errBox(r);
    return;
  }
  const j = r.json || {};
  box.innerHTML = badge(j.applied === true ? "applied · 目标已原子替换" : "status · " + asText(j.status),
      j.applied === true ? "ok" : "warn") +
    kv([["applied", j.applied], ["verified", j.verified], ["status", j.status],
        ["target", j.target], ["staged", j.staged], ["sha256", j.sha256],
        ["staged_sha256", j.staged_sha256], ["actual_sha256", j.actual_sha256],
        ["reason", j.reason], ["fabricated", j.fabricated]]) + rawBlock(j);
}
$("upd-apply").addEventListener("click", updApply);
$("wr-lic-btn").addEventListener("click", () => {
  const k = $("wr-lic-key").value.trim();
  if (!k) return p45Bad("wr-lic-out", "license_key 未填写");
  renderOut("wr-lic-out", Post("/license/activate", { license_key: k }), (j) =>
    kv([["status", j.status], ["edition", j.edition], ["valid_until", j.valid_until], ["reason", j.reason]]));
});
$("wr-dv-btn").addEventListener("click", () => {
  const proj = $("wr-dv-proj").value.trim();
  if (!proj) return p45Bad("wr-dv-out", "project 未填写");
  const f = parseJsonField("wr-dv-files");
  if (!f.ok) return showLocalErr("wr-dv-out", f.error);
  renderOut("wr-dv-out", Post("/delivery/package", { project: proj, files: f.value || {} }), (j) =>
    kv([["package_id", j.package_id], ["included", j.included], ["skipped", asText(j.skipped)],
        ["archive", j.archive], ["archive_size_bytes", j.archive_size_bytes]]));
});
$("wr-tr-btn").addEventListener("click", () => {
  const m = $("wr-tr-miss").value;
  if (!m) return p45Bad("wr-tr-out", "missing 字符未填写");
  renderOut("wr-tr-out", Post("/typography/resolve", { missing: m, text: $("wr-tr-text").value || undefined }), (j) =>
    dynTable((j.candidates || []).slice(0, 8).map((c) => ({ family: c.family, style: c.style, score: c.score }))) +
    `<div class="small muted">候选为机器相关真实字体索引；本次解析可能已学习并落盘回退规则（设计意图）。</div>`);
});
$("wr-cr-btn").addEventListener("click", () => {
  const host = $("wr-cr-host").value.trim();
  if (!host) return p45Bad("wr-cr-out", "host 未填写");
  const c = parseJsonField("wr-cr-caps");
  if (!c.ok) return showLocalErr("wr-cr-out", c.error);
  renderOut("wr-cr-out", Post("/tools/capability-report", { host, capabilities: c.value || {} }), (j) =>
    kv([["host", j.host], ["hosts_reported", j.hosts_reported], ["supported_count", j.supported_count]]));
});
$("wr-al-btn").addEventListener("click", () => {
  const id = $("wr-al-id").value.trim();
  if (!id) return p45Bad("wr-al-out", "素材 id 未填写");
  renderOut("wr-al-out", Post(`/assets/${encodeURIComponent(id)}/license`, {
    license_status: $("wr-al-status").value, license_type: $("wr-al-type").value.trim(),
    commercial_use: $("wr-al-status").value === "commercial", verified: false, warnings: [],
  }), (j) => kv([["asset_id", asText(j.asset_id)], ["license_status", asText(j.license_status)], ["commercial_use", asText(j.commercial_use)]]));
});

/* ======================= Phase 48：AI 生成类接线 =======================
   四个 POST 端点仅在显式点击时发出；两种诚实失败形态由 p48_probe 钉死：
   /generate 的 502 detail 为字符串，/plan · /provider/chat · /provider/images
   的 502 detail 为对象 {status, fabricated:false, error, detail}（api() 原样序列化呈现）。
   未取回结果时本区不渲染任何图像、计划或回复文本。                              */
async function aiRender(elId, r, viewer) {
  const box = $(elId);
  const res = await r;
  if (!res.ok) {
    box.innerHTML = errBox(res) +
      `<div class="small muted">未取得真实结果，故不呈现结果：本界面无兜底假数据、不伪造图像/计划/回复。</div>`;
    return;
  }
  box.innerHTML = (viewer ? viewer(res.json) : "") + rawBlock(res.json);
}
const aiFabricated = (j) => `<div class="small muted">服务端字段原样呈现；<code>fabricated=${esc(asText(j.fabricated))}</code>、<code>status=${esc(asText(j.status))}</code>。</div>`;
const aiTimeout = () => {
  const t = Number($("wr-ai-timeout").value);
  return Number.isFinite(t) && t > 0 ? t : undefined;
};

$("wr-gen-btn").addEventListener("click", () => {
  const prompt = $("wr-gen-prompt").value.trim();
  if (!prompt) return p45Bad("wr-gen-out", "prompt 未填写（服务端必填，缺省将 422）— 未发起写入");
  const seedRaw = $("wr-gen-seed").value.trim();
  aiRender("wr-gen-out", Post("/generate", {
    prompt,
    model: $("wr-gen-model").value.trim() || undefined,
    resolution: $("wr-gen-res").value.trim() || "1024x1024",
    steps: Number($("wr-gen-steps").value) || 30,
    guidance: Number($("wr-gen-guidance").value) || 7.5,
    seed: seedRaw === "" ? undefined : Number(seedRaw),
    priority: $("wr-gen-priority").value,
  }), (j) =>
    kv([["status", asText(j.status)], ["generation_id", asText(j.generation_id)], ["provider", asText(j.provider)],
        ["model", asText(j.model)], ["images_generated", asText(j.images_generated)],
        ["output_length", asText(j.output_length)], ["cost_usd", asText(j.cost_usd)],
        ["duration_seconds", asText(j.duration_seconds)], ["params", asText(j.params)]]) +
    aiFabricated(j) +
    `<div class="small muted">output_preview 为服务端截断原文（本界面按 ${esc(asText(j.output_length))} 字之内的返回值呈现）：</div>` +
    `<pre class="ai-out">${esc(asText(j.output_preview))}</pre>` +
    (j.images_generated ? `<div class="info-box small">本次 images_generated&gt;0，但服务端未开放生成图的静态文件路由，故此处不绘制图像，仅见上方元数据与原始 JSON 中的真实路径。</div>` : ""));
});

$("wr-plan-btn").addEventListener("click", () => {
  const goal = $("wr-plan-goal").value.trim();
  if (!goal) return p45Bad("wr-plan-out", "goal 未填写（服务端必填，缺省将 422）— 未发起写入");
  aiRender("wr-plan-out", Post("/plan", {
    goal,
    provider_id: $("wr-plan-pid").value.trim() || undefined,
    context: $("wr-plan-ctx").value.trim() || undefined,
    timeout: aiTimeout(),
  }), (j) => {
    const p = j.plan || {};
    return kv([["status", asText(j.status)], ["provider", asText(j.provider)], ["model", asText(j.model)],
               ["implemented", asText(j.implemented)], ["fabricated", asText(j.fabricated)],
               ["plan.goal", asText(p.goal)], ["steps 数", Array.isArray(p.steps) ? p.steps.length : asText(p.steps)],
               ["tools", asText(p.tools)], ["constraints", asText(p.constraints)],
               ["protected_objects", asText(p.protected_objects)], ["verification", asText(p.verification)],
               ["rollback", asText(p.rollback)], ["usage", asText(j.usage)]]) +
      (Array.isArray(p.steps) && p.steps.length
        ? `<div class="small muted">计划步骤为服务端 LLM 原文（结构未通过 schema 校验时 status 如实为 error/unparseable）：</div>` +
          dynTable(p.steps.map((s) => (typeof s === "object" ? s : { step: s })))
        : "") + aiFabricated(j);
  });
});

$("wr-chat-btn").addEventListener("click", () => {
  const prompt = $("wr-chat-prompt").value.trim();
  if (!prompt) return p45Bad("wr-chat-out", "prompt 未填写（未填时服务端会发空 messages，仍属真实调用）— 未发起请求");
  aiRender("wr-chat-out", Post("/provider/chat", {
    prompt,
    provider_id: $("wr-chat-pid").value.trim() || undefined,
    model: $("wr-chat-model").value.trim() || undefined,
    temperature: Number.isFinite(Number($("wr-chat-temp").value)) ? Number($("wr-chat-temp").value) : undefined,
    stream: $("wr-chat-stream").value === "true",
    timeout: aiTimeout(),
  }), (j) =>
    kv([["status", asText(j.status)], ["provider", asText(j.provider)], ["model", asText(j.model)],
        ["implemented", asText(j.implemented)], ["fabricated", asText(j.fabricated)],
        ["streamed", asText(j.streamed)], ["usage", asText(j.usage)],
        ["duration_seconds", asText(j.duration_seconds)], ["content 长度", (j.content || "").length]]) +
    aiFabricated(j) +
    `<div class="small muted">content 为 Provider 返回原文：</div>` +
    `<pre class="ai-out">${esc(asText(j.content))}</pre>`);
});

$("wr-img-btn").addEventListener("click", () => {
  const prompt = $("wr-img-prompt").value.trim();
  if (!prompt) return p45Bad("wr-img-out", "prompt 未填写（服务端必填，缺省将 422）— 未发起写入");
  aiRender("wr-img-out", Post("/provider/images", {
    prompt,
    provider_id: $("wr-img-pid").value.trim() || undefined,
    n: Number($("wr-img-n").value) || 1,
    size: $("wr-img-size").value.trim() || "1024x1024",
    model: $("wr-img-model").value.trim() || undefined,
    timeout: aiTimeout(),
  }), (j) =>
    kv([["status", asText(j.status)], ["provider", asText(j.provider)], ["model", asText(j.model)],
        ["implemented", asText(j.implemented)], ["fabricated", asText(j.fabricated)],
        ["images_generated", asText(j.images_generated)], ["duration_seconds", asText(j.duration_seconds)]]) +
    aiFabricated(j) +
    ((j.files || []).length
      ? `<div class="small muted">真实落盘文件（服务端本机路径 + 实测字节数）：</div>` +
        dynTable(j.files.map((f) => ({ path: f.path, bytes: f.bytes, revised_prompt: asText(f.revised_prompt) }))) +
        `<div class="info-box small">本服务未开放生成图的静态文件路由，故此处<b>不</b>绘制图像预览——仅呈现真实路径与字节数。</div>`
      : `<div class="small muted">files 为空：本次服务端未落盘任何图像文件（不虚报张数）。</div>`));
});

/* ============================ 启动：服务徽章 ============================ */
(async () => {
  const r = await api("/health");
  const b = $("svc-badge");
  if (r.ok) { b.className = "badge ok"; b.textContent = "服务在线"; }
  else { b.className = "badge err"; b.textContent = "服务不可达"; }
})();
loaded.overview = true;
initOverview();
