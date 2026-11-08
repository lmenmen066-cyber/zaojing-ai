/**
 * 造境 AI · Illustrator UXP 桥接面板 — 宿主侧真实逻辑（§8/§9/§24/§25/§26/§74）
 *
 * 职责（§4）：只做「连 Agent Service + 在 Illustrator 内真实执行文档操作 + 回传真实结果」。
 *   不在此堆数据库/LLM/长任务。
 *
 * ⚠️ 诚实边界：本文件遵循 Adobe 官方 UXP API 写法，但开发环境未安装 Illustrator，
 *   "宿主内真实执行" 未在该环境运行验证。打包为 .ccx/.zxp 并在真实 Ai 里加载后方可生效。
 *   服务端无论本面板是否连上，都绝不伪造文档变更；未连接时命令下发一律 no_host_connected。
 *
 * 诚实实现取舍（§2/§26）：只真实实现「确有稳定 Illustrator UXP DOM 路径」的意图
 *   （document.get / artboard.list / path.create / text.create），其余意图（object.move、
 *   vectorize 等）若无可靠 DOM 路径则如实上报 not_supported —— 绝不用假动作冒充成功。
 *   上报的能力集合据此保守声明（vectorize:false）。
 */
const { app, core } = require("illustrator");
const uxp = require("uxp");
const localFileSystem = uxp.storage.localFileSystem;

const state = {
  connected: false,
  hostId: null,
  token: null,
  base: null,           // http://127.0.0.1:<port>
  discoveryToken: null, // 明文 token，仅存内存
  running: false,
};

// ---- 日志 ----
const $log = () => document.getElementById("log");
function log(msg, cls) {
  const el = $log();
  const line = document.createElement("div");
  if (cls) line.className = cls;
  line.textContent = `[${new Date().toLocaleTimeString()}] ${msg}`;
  el.appendChild(line);
  el.scrollTop = el.scrollHeight;
}
function setStatus(text, cls) {
  const s = document.getElementById("status");
  s.textContent = text;
  s.className = cls || "idle";
}

// ---- §8 端口动态发现：读本地 bridge-endpoint.json 拿 port + session_token ----
async function readDiscoveryViaPicker() {
  // UXP 需用户授权访问任意路径；面板引导选择一次发现文件（服务端半侧把它落在本地数据目录）。
  const file = await localFileSystem.getFileForOpening({ allowTypes: ["json"], names: ["bridge-endpoint"] });
  if (!file) { log("未选择发现文件，无法连接", "err"); return; }
  const text = await file.read();
  const info = JSON.parse(text);
  state.base = `${info.scheme || "http"}://${info.host || "127.0.0.1"}:${info.port}`;
  state.discoveryToken = info.session_token;
  log(`发现 Agent Service：${state.base}`, "ok");
  return info;
}

async function jpost(path, body) {
  const res = await fetch(state.base + path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const json = await res.json().catch(() => ({}));
  return { status: res.status, json };
}

// ---- §25 能力上报（本宿主真实具备的能力集合；据 Illustrator DOM 支持度如实声明）----
function negotiateCapabilities() {
  return {
    document_read: true,   // document.get / artboard.list 有稳定 DOM 路径
    object_write: true,    // path.create / text.create 有稳定 DOM 路径
    // vectorize: Illustrator 的「图像描摹」在此参考实现无稳定纯 DOM 调用 → 诚实 false
    vectorize: false,
    // selection: 参考面板不消费选择语义 → 诚实不声明为 true
    selection: false,
  };
}

// ---- §8 握手 + 认证 ----
async function handshake() {
  const caps = negotiateCapabilities();
  const { status, json } = await jpost("/api/v1/uxp/handshake", {
    host: "illustrator",
    version: app.version,          // §26：仅诊断，服务端不以版本字符串门控
    capabilities: caps,
    token: state.discoveryToken,
  });
  if (status !== 200 || json.status !== "connected") {
    throw new Error(`握手失败 HTTP ${status}: ${JSON.stringify(json)}`);
  }
  state.hostId = json.host_id;
  state.token = state.discoveryToken; // 之后每请求都带
  state.connected = true;
  setStatus(`已连接 (${json.host_id})`, "ok");
  log(`握手成功，服务端可执行工具: ${(json.runnable_now_tools || []).join(", ")}`, "ok");
}

// 在文档顶层图层的 pageItems 里按名查找（Illustrator UXP 常见遍历方式）
function findItem(doc, name) {
  if (!name) return null;
  try {
    const items = doc.pageItems;
    for (let i = 0; i < items.length; i++) {
      if (items[i].name === name) return items[i];
    }
  } catch (e) { /* §74：遍历异常不外溢，交由上层如实判定未找到 */ }
  return null;
}

// ---- 真实文档执行：把 §9 命令在 Illustrator 内跑出真实结果（§74 崩溃隔离）----
async function executeIntent(cmd) {
  const intent = cmd.intent;
  const args = (cmd.context && cmd.context.args) || {};
  const protectedSet = ((cmd.permissions || {}).protected_objects) || [];

  // 受保护对象（§17/§88.2）：命中则如实拒绝，绝不改动画像/矢量
  const targetName = args.name || args.target || args.from || args.to;
  const isReadonly = (intent === "document.get" || intent === "artboard.list");
  if (!isReadonly && targetName && protectedSet.includes(targetName)) {
    const out = { status: "error", error: "protected_object", detail: `受保护对象不可改: ${targetName}` };
    await jpost("/api/v1/uxp/result", { request_id: cmd.request_id, token: state.token, ...out });
    log(`拒绝受保护对象命令 ${intent} -> ${targetName}`, "err");
    return;
  }

  try {
    let payload;
    await core.executeAsModal(async () => {
      const doc = app.activeDocument;
      if (!doc) throw new Error("无打开的文档");

      if (intent === "document.get") {
        payload = {
          document_id: cmd.document_id, name: doc.name,
          layer_count: doc.layers.length,
          pageitem_count: doc.pageItems.length,
          artboard_count: doc.artboards.length,
        };
      } else if (intent === "artboard.list") {
        const abs = [];
        for (let i = 0; i < doc.artboards.length; i++) {
          const ab = doc.artboards[i];
          const r = ab.artboard; // [left, top, right, bottom]
          abs.push({
            id: `artboard_${i}`, name: ab.name,
            bounds: { left: r[0], top: r[1], right: r[2], bottom: r[3] },
          });
        }
        payload = { document_id: cmd.document_id, objects: abs };
      } else if (intent === "path.create") {
        // 真实矢量矩形（Illustrator UXP：pathItems.rectangle(top, left, width, height)）
        const w = Number(args.width) || 100;
        const h = Number(args.height) || 100;
        const l = Number(args.left) || 0;
        const t = Number(args.top) || 0;
        const p = doc.pathItems.rectangle(t, l, w, h);
        p.name = args.name || ("造境路径 " + Date.now());
        payload = { document_id: cmd.document_id, objects: [{ id: `path_${p.toString()}`, name: p.name, created: true, kind: "rectangle" }] };
      } else if (intent === "text.create") {
        const tf = doc.textFrames.add();
        tf.contents = args.text || args.contents || "造境文字";
        tf.name = args.name || ("造境文本 " + Date.now());
        try { tf.position = [Number(args.left) || 0, Number(args.top) || 0]; } catch (e) { /* 位置设置非必需 */ }
        payload = { document_id: cmd.document_id, objects: [{ name: tf.name, created: true, kind: "text", text: tf.contents }] };
      } else if (intent === "object.create") {
        // 通用对象创建：此参考实现仅覆盖矩形一种，其余形状如实不支持
        if ((args.shape || "rect") === "rect") {
          const w = Number(args.width) || 100, h = Number(args.height) || 100;
          const p = doc.pathItems.rectangle(Number(args.top) || 0, Number(args.left) || 0, w, h);
          p.name = args.name || ("造境对象 " + Date.now());
          payload = { document_id: cmd.document_id, objects: [{ name: p.name, created: true, kind: "rectangle" }] };
        } else {
          throw Object.assign(new Error(`未支持的形状: ${args.shape}`), { code: "not_supported" });
        }
      } else {
        // object.move / vectorize 等在 Illustrator UXP 无稳定纯 DOM 路径 → 如实上报不支持，绝不假成功
        throw Object.assign(new Error(`宿主未实现该意图: ${intent}`), { code: "not_supported" });
      }
    }, { commandName: "造境 " + intent });

    await jpost("/api/v1/uxp/result", { request_id: cmd.request_id, token: state.token, status: "ok", payload });
    if (document.getElementById("verbose").checked) log(`执行 ${intent} -> ok`, "ok");
  } catch (e) {
    const errCode = e && e.code ? e.code : "host_error";
    await jpost("/api/v1/uxp/result", {
      request_id: cmd.request_id, token: state.token, status: "error", error: errCode,
      detail: String(e && e.message ? e.message : e),
    });
    log(`执行 ${intent} 失败: ${e && e.message ? e.message : e}`, "err"); // §74 崩溃隔离：捕获不外溢
  }
}

// ---- 命令循环（§9 长轮询）----
async function pollLoop() {
  state.running = true;
  while (state.running && state.connected) {
    let cmds = [];
    try {
      const { status, json } = await jpost("/api/v1/uxp/poll", { host_id: state.hostId, token: state.token, timeout: 25 });
      if (status === 502) { // 掉线/未注册/认证失效：如实停止，不空转
        log(`轮询被拒(${json && json.detail && json.detail.error})，需重新握手`, "err");
        break;
      }
      cmds = json.commands || [];
    } catch (e) {
      await new Promise((r) => setTimeout(r, 800)); // 服务端暂不可达：退避重试（不崩溃）
      continue;
    }
    for (const c of cmds) {
      try { await executeIntent(c); }
      catch (fatal) { log(`命令处理异常(隔离): ${fatal}`, "err"); } // §74
    }
  }
  state.running = false;
}

// ---- UI 绑定 ----
document.getElementById("pickDiscovery").onclick = async () => {
  try { await readDiscoveryViaPicker(); } catch (e) { log("读取发现文件失败: " + e, "err"); }
};
document.getElementById("connect").onclick = async () => {
  try {
    if (!state.base) await readDiscoveryViaPicker();
    if (!state.base) { log("请先选择 bridge-endpoint.json", "err"); return; }
    setStatus("握手中…", "idle");
    await handshake();
    pollLoop();
  } catch (e) {
    setStatus("连接失败", "err");
    log("连接失败: " + (e && e.message ? e.message : e), "err");
  }
};
log("面板就绪。请：1) 选择 bridge-endpoint.json；2) 点击连接。", "idle");
