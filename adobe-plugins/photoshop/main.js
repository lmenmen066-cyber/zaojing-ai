/**
 * 造境 AI · Photoshop UXP 桥接面板 — 宿主侧真实逻辑（§8/§9/§24/§25/§74）
 *
 * 职责（§4）：只做「连 Agent Service + 在 Photoshop 内真实执行文档操作 + 回传真实结果」。
 *   不在此堆数据库/LLM/长任务。
 *
 * ⚠️ 诚实边界：本文件遵循 Adobe 官方 UXP API 写法，但开发环境未安装 Photoshop，
 *   "宿主内真实执行" 未在该环境运行验证。打包为 .ccx 并在真实 PS 里加载后方可生效。
 *   服务端无论本面板是否连上，都绝不伪造文档变更；未连接时命令下发一律 no_host_connected。
 */
const { app, core } = require("photoshop");
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

// ---- §25 能力上报（本宿主真实具备的能力集合；据 Photoshop DOM 支持度如实声明）----
function negotiateCapabilities() {
  return {
    document_read: true,
    layer_write: true,
    selection: true,
    batch_operation: true,
    // image_place: 若所用 PS 版本无稳定 place 能力则诚实 false；此处保守上报 true 仅在确认支持时
    image_place: false,
  };
}

// ---- §8 握手 + 认证 ----
async function handshake() {
  const caps = negotiateCapabilities();
  const { status, json } = await jpost("/api/v1/uxp/handshake", {
    host: "photoshop",
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

// ---- 真实文档执行：把 §9 命令在 Photoshop 内跑出真实结果（§74 崩溃隔离）----
async function executeIntent(cmd) {
  const intent = cmd.intent;
  const args = (cmd.context && cmd.context.args) || {};
  const protectedSet = ((cmd.permissions || {}).protected_objects) || [];

  const findLayer = (doc, name) => {
    const layers = [];
    const walk = (arr) => arr.forEach((l) => { layers.push(l); if (l.layers) walk(l.layers); });
    walk(doc.layers);
    return layers.find((l) => l.name === name);
  };

  // 受保护对象（§17/§88.2）：命中则如实拒绝，绝不改动画像
  const targetName = args.name || args.target || args.layer || args.to;
  if (intent !== "document.get" && intent !== "layer.list" && targetName && protectedSet.includes(targetName)) {
    const out = { status: "error", error: "protected_object", detail: `受保护对象不可改: ${targetName}` };
    await jpost("/api/v1/uxp/result", { request_id: cmd.request_id, token: state.token, ...out });
    log(`拒绝受保护对象命令 ${intent} -> ${targetName}`, "err");
    return;
  }

  try {
    let payload;
    await core.executeAsModal(async () => {
      const doc = app.documents[0];
      if (!doc) throw new Error("无打开的文档");
      if (intent === "document.get") {
        payload = {
          document_id: cmd.document_id, name: doc.name,
          width: doc.width, height: doc.height, resolution: doc.resolution,
          layer_count: doc.layers.length,
        };
      } else if (intent === "layer.list") {
        payload = {
          document_id: cmd.document_id,
          objects: doc.layers.map((l, i) => ({ id: `layer_${i}`, name: l.name, visible: l.visible, opacity: l.opacity })),
        };
      } else if (intent === "layer.create") {
        const L = doc.artLayers.add();
        L.name = args.name || ("造境图层 " + Date.now());
        payload = { document_id: cmd.document_id, objects: [{ id: `layer_${L.id}`, name: L.name, created: true }] };
      } else if (intent === "layer.rename") {
        const L = findLayer(doc, args.from || args.name);
        if (!L) throw Object.assign(new Error("目标图层不存在"), { code: "target_ambiguous" });
        L.name = args.to || args.newName;
        payload = { document_id: cmd.document_id, objects: [{ name: L.name, renamed: true }] };
      } else if (intent === "layer.move") {
        const L = findLayer(doc, args.name);
        if (!L) throw new Error("目标图层不存在");
        const T = findLayer(doc, args.to);
        L.move(T || doc, core.ElementPlacement.PLACEATBEGINNING);
        payload = { document_id: cmd.document_id, moved: true, name: L.name };
      } else if (intent === "layer.delete") {
        const L = findLayer(doc, args.name || args.target);
        if (!L) throw new Error("目标图层不存在");
        L.remove();
        payload = { document_id: cmd.document_id, deleted: true };
      } else {
        // 未在本参考实现覆盖的意图：如实上报不支持，绝不假成功
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
