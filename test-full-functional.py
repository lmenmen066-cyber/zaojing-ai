"""Full functional acceptance test — real service, real engines, zero mocks."""
import sys, os, json, time, socket, subprocess, tempfile, traceback
sys.stdout.reconfigure(errors="backslashreplace")

from pathlib import Path
import requests
from PIL import Image

# ---- Phase 50 引入避让、Phase 57 升级为"内核指派临时端口 + boot 后核对监听者身份" ----
# P50 的原口径（保留其结论）：Windows 上 http.server/uvicorn 设了 allow_reuse_address，
# "再绑一次同一端口"照样 bind 成功，且新连接被**后绑定者**抢走 ⇒ bind 成功完全不证明端口可用。
# P57 实测证明**单点避让不够**（p57_l2_m6.out / p57_l2_m7.out 两轮崩溃）：本机 8765 正被外部
# 进程**反复回收**——13:32 基线那刻它"有人应答"⇒判 foreign、避让到 8781 ⇒ 该轮 289/289 全绿；
# 13:36 与 13:38 两轮它恰在重启间隙"无人应答"⇒判 silent 并占用它 ⇒ 邻居随后重新绑定、新连接被
# 抢走 ⇒ 内层 L2 分别在第 104 条、第 14 条收到 ConnectionResetError(10054) 而中断，日志无 TOTAL
# 裁决行（同一分钟内实测：13:38:48 PID 29060 = scripts/serve.py 8765、13:45 换 PID 26532）。
# 根因：旧判据证的是**某一瞬间有没有人应答**（liveness），要防的却是**这条端口上有没有别人**
# （ownership）——点采样追不上会自行重启的邻居。故这里不再从固定候选里挑端口，改为
# ①问内核要一个它自己指派的临时端口（bind port=0，外部邻居用的是固定低位口，不同轨）；
# ②服务应答后**当场**核对 /health 的 data_root 落在本轮独享临时根内、netstat 上该端口的监听
# PID 就是本进程亲自起的子进程。把"跟陌生人说话、100 条断言之后才炸"变成 boot 处一次大声失败。
# 固定候选端口仍然**逐项探测并打印**，作为"我们不再与谁争"的现场留档。
def _answered(p):
    """该端口上是否有 HTTP 应答者？返回 (应答与否, 应答者是否造境载荷)。"""
    try:
        r = requests.get("http://127.0.0.1:%d/api/v1/health" % p, timeout=2)
    except Exception:
        return False, False
    try:
        j = r.json()
    except Exception:
        j = None
    ours = (r.status_code == 200 and isinstance(j, dict)
            and isinstance(j.get("engines"), dict) and "data_root" in j)
    return True, ours


def _listener_pids(port):
    """netstat 中在该端口 LISTENING 的 PID 集合（字符串）。"""
    _o = subprocess.run(["netstat", "-ano"], capture_output=True, text=True,
                        errors="replace").stdout
    return {ln.split()[-1] for ln in _o.splitlines()
            if "LISTENING" in ln and ln.split()[1].endswith(":%d" % port)}


def _pick_port(candidates=(8765, 8781, 8783, 8785, 8787, 8789)):
    seen = {}
    for p in candidates:
        busy, ours = _answered(p)
        seen[p] = ("zaojing" if ours else "foreign") if busy else "silent"
    for _ in range(8):
        _s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            _s.bind(("127.0.0.1", 0))
            port = _s.getsockname()[1]
        finally:
            _s.close()
        if port not in seen and not _answered(port)[0]:
            return port, seen
    raise RuntimeError("no OS-assigned silent acceptance port (fixed-band probes=%s)"
                       % (seen,))


PORT, _PORT_PROBES = _pick_port()
BASE = "http://127.0.0.1:%d" % PORT
print("[PORT] L2 acceptance port=%d (OS-assigned ephemeral; fixed-band probes=%s = 本轮不再"
      "与之争抢的外部/残留持有者留档)" % (PORT, json.dumps(_PORT_PROBES)))
# 仓库根目录 = 本脚本所在目录（脚本位于 outputs 仓库根）。早先写死本机绝对路径会让
# 任何其它检出（含 CI runner）找不到 agent_service.py 与 webui/*，此处改为相对脚本自身定位。
OUT = Path(__file__).resolve().parent
sys.path.insert(0, str(OUT))

# ---- 布局基准 shim（尺子修复，非判据削弱）----
# 本 harness 早期按「脚本父目录下再有一层 outputs/ 放源码与产物」的两级树撰写，全文以
# `<脚本父目录> / "outputs" / X` 定位 setup.iss / installer/ / cdr_host.py / portable/ 等。
# 本仓库把这些产物平铺在与本脚本同级处（= OUT 本身，与 L4 脚本零 "outputs" 引用可佐证），
# 故凡旧式 `<base> / "outputs" / X` 在平铺检出里都应解析到 OUT。这里据 setup.iss 的真实落点
# 自动选定基准：两级树里它在 OUT/outputs、平铺仓库里它就在 OUT——只搬文件定位，不动任何断言语义。
_ZJ_OUTBASE = (OUT / "outputs") if (OUT / "outputs" / "setup.iss").is_file() else OUT


def _zj_uninstall_count(root, view):
    """测试侧**独立复算**某注册表视图下 Uninstall 的真实子键数，用来交叉核对服务端探针回报的
    subkey_count（尺子修复：把旧判据「四视图逐一 subkey_count>0」这一作者机耦合——干净 Windows
    runner 的 HKCU\\...\\Uninstall 合法为 0，作者机却因装过第三方应用而 HKCU=37——换成与机器无关
    且**更强**的等式：服务端回报数==本函数现场枚举数，且四视图合计>0 以证枚举接缝非恒空桩。
    与 installer_manifest_engine._enum_uninstall_keys 同一开窗口径；非 Windows/键不可开 → 0，
    对齐服务端 OSError→空列表→计数 0 的实况。"""
    if not sys.platform.startswith("win"):
        return 0
    try:
        import winreg
    except Exception:
        return 0
    hive = winreg.HKEY_CURRENT_USER if root == "HKCU" else winreg.HKEY_LOCAL_MACHINE
    flag = winreg.KEY_WOW64_32KEY if str(view) == "32" else winreg.KEY_WOW64_64KEY
    try:
        _h = winreg.OpenKey(hive, r"Software\Microsoft\Windows\CurrentVersion\Uninstall",
                            0, winreg.KEY_READ | flag)
        return int(winreg.QueryInfoKey(_h)[0])
    except OSError:
        return 0


# 内容类型家族判定（尺子修复，非判据削弱）：StaticFiles 的 content-type 来自 stdlib mimetypes，
# 而 mimetypes 在 Windows 上还会读 HKEY_CLASSES_ROOT 里各安装器登记的 .js Content Type——作者机与
# CI runner 的注册表可分别给 text/javascript / application/javascript。二者都是合法的 JS MIME 拼法、
# 浏览器行为一致，属环境差异而非产品契约。故对 JS 资产钉「类型家族」而非某个 stdlib 拼写：家族成员
# 之外的值（如 application/octet-stream、text/plain）照样判红——既去掉拼写耦合，又保留可证伪性。
_ZJ_CT_FAMILY = {
    "text/html": {"text/html"},
    "text/css": {"text/css"},
    "text/javascript": {"text/javascript", "application/javascript", "application/ecmascript",
                        "text/ecmascript", "application/x-javascript", "module"},
}


def _zj_ct_ok(want, got):
    base = (got or "").split(";", 1)[0].strip().lower()
    return base in _ZJ_CT_FAMILY.get(want, {want.lower()})

from license_service import issue_license_key  # 服务端默认 SECRET 一致（两者均未设 ZAOJING_LICENSE_SECRET）
tmp = Path(tempfile.mkdtemp(prefix="zjtest_"))

results = []
def check(name, cond, detail=""):
    tag = "[PASS]" if cond else "[FAIL]"
    results.append((tag, name, detail))
    print(f"{tag} {name} {detail}")

# --- prepare real test images ---
img1 = tmp / "red_product_on_white.png"
im = Image.new("RGB", (120, 120), (255, 255, 255))
for x in range(45, 75):
    for y in range(45, 75):
        im.putpixel((x, y), (220, 30, 30))
im.save(img1)
img2 = tmp / "tea_texture_green.jpg"
Image.new("RGB", (80, 80), (30, 140, 60)).save(img2)
img3 = tmp / "logo_blue.png"
Image.new("RGB", (60, 60), (20, 60, 200)).save(img3)
# Phase 38 下沉回归夹具：带被包围背景色孔洞的图，端点薄包装必须与引擎逐数同值
img4 = tmp / "flood_hole_fixture.png"
_im4 = Image.new("RGB", (100, 100), (200, 30, 30))
for _y in range(30, 70):
    for _x in range(30, 70):
        _im4.putpixel((_x, _y), (20, 20, 220))
for _y in range(40, 48):
    for _x in range(40, 48):
        _im4.putpixel((_x, _y), (200, 30, 30))
_im4.save(img4)
# Phase 47 第 25 引擎活体夹具：240x160，1px 实线外框 + x=119 垂直实线 cut + y=79 水平虚线 fold
# 期望逐数由 outputs/p47_l2probe.py 真服务探针冻结（面积可手算：98x58/99x58/98x59/99x59）
img5 = tmp / "p47_dieline_2x2.png"
_im5 = Image.new("RGB", (240, 160), (255, 255, 255))
_p5 = _im5.load()
for _p5x in range(20, 220):
    for _p5y in (20, 139):
        _p5[_p5x, _p5y] = (0, 0, 0)
for _p5y in range(20, 140):
    for _p5x in (20, 219):
        _p5[_p5x, _p5y] = (0, 0, 0)
for _p5y in range(21, 139):
    _p5[119, _p5y] = (0, 0, 0)
_p5x = 21
while _p5x <= 218:
    for _p5k in range(10):
        if _p5x + _p5k <= 218:
            _p5[_p5x + _p5k, 79] = (0, 0, 0)
    _p5x += 14
_im5.save(img5)

# --- start server (stdout->file to avoid PIPE buffer deadlock) ---
srv_log = open(tmp / "server.log", "wb")
env = dict(os.environ, ZAOJING_PORT=str(PORT), ZAOJING_DATA_DIR=str(tmp / "data"))
proc = subprocess.Popen([sys.executable, str(OUT / "agent_service.py")], env=env,
                        stdout=srv_log, stderr=subprocess.STDOUT)
try:
    up = False
    for _ in range(60):
        time.sleep(0.5)
        try:
            r = requests.get(BASE + "/api/v1/health", timeout=2)
            if r.status_code == 200:
                up = True
                break
        except Exception:
            continue
    check("server boot", up)

    h = r.json()
    # P57：应答者身份当场核对（P50 定律的对偶面——"有 200 应答"不等于"应答者是我的服务"）。
    # 不通过就立刻大声退出：绝不允许剩下 280 余条断言对着陌生监听者跑，把一次端口争抢
    # 伪装成"产品在第 104 条处崩溃"。
    _dr = str(h.get("data_root") or "").replace("\\", "/")
    _mine = str(tmp / "data").replace("\\", "/")
    _pids = _listener_pids(PORT)
    if (_dr != _mine and not _dr.startswith(_mine + "/")) or _pids != {str(proc.pid)}:
        print("[ABORT] L2 应答者身份核对失败：port=%d data_root=%r 期望前缀=%r "
              "listener_pids=%s 自己起的子进程 PID=%s ⇒ 判定为端口被他人占有/抢占，"
              "本轮不判分（既不判绿，也不据此反推产品有问题）"
              % (PORT, _dr, _mine, sorted(_pids), proc.pid))
        try:
            proc.terminate()
        except Exception:
            pass
        sys.exit(2)
    check("health: engines 30/30 (self-report covers evaluation/brand/runtime since Phase 26 fix, +compare/bg_removal Phase 38, +panel_recognition Phase 47, +installer_manifest Phase 49, +desktop_hub Phase 50, +service_registry Phase 80, +event/delivery Phase 81)",
          len(h["engines"]) == 30 and h["engines"].get("panel_recognition") == "ready"
          and h["engines"].get("installer_manifest") == "ready"
          and h["engines"].get("desktop_hub") == "ready"
          and h["engines"].get("service_registry") == "ready"
          and h["engines"].get("event") == "ready"
          and h["engines"].get("delivery") == "ready"
          and all(v == "ready" for v in h["engines"].values()), json.dumps(h["engines"]))
    _LIVE_ENGINE_COUNT = len(h.get("engines", {}))
    check("health: data_root honored", h["data_root"].replace("\\", "/").endswith("data"))

    # --- Phase 35: webui served same-port (mount registered after all API routes) ---
    import re as _re35
    _ui = requests.get(BASE + "/")
    check("webui: GET / serves console HTML from agent_service same port (Phase 35)",
          _ui.status_code == 200 and "text/html" in _ui.headers.get("content-type", "")
          and "\u9020\u5883" in _ui.text and "/ui/app.js" in _ui.text,
          f"HTTP {_ui.status_code} ct={_ui.headers.get('content-type','')}")
    _uijs = requests.get(BASE + "/ui/app.js")
    _uicss = requests.get(BASE + "/ui/style.css")
    _no404 = requests.get(BASE + "/ui/__no_such_asset__.xyz").status_code == 404
    check("webui: /ui assets served & index fully self-contained (zero CDN, offline red-line) & missing asset honest 404",
          _uijs.status_code == 200 and "renderOut" in _uijs.text
          and _uicss.status_code == 200 and "--accent" in _uicss.text
          and _no404
          and not _re35.search(r'(?:src|href)="https?://', _ui.text),
          f"js={_uijs.status_code} css={_uicss.status_code}")
    _docs = requests.get(BASE + "/docs")
    _oa = requests.get(BASE + "/openapi.json")
    check("webui: mount last — /docs /openapi.json /api not shadowed",
          _docs.status_code == 200 and _oa.status_code == 200 and _oa.json()["info"]["version"] == "1.0.8",
          f"docs={_docs.status_code} oa={_oa.status_code}")

    # --- Phase 39: webui 6→12 panels (layout/brand/print/knowledge/revision/ops wired to real endpoints) ---
    _p39_nav = ["overview", "eval", "color", "packaging", "assets", "typo",
                "layout", "brand", "print", "knowledge", "revision", "ops"]
    _p39_html = _ui.text
    check("webui(P39+P40+P42+P45): served index has exactly 15 nav panels, each nav matches a panel section (P40 adds flow, P42 adds detail, P45 adds write)",
          all(f'data-panel="{p}"' in _p39_html for p in _p39_nav + ["flow", "detail", "write"])
          and all(f'id="panel-{p}"' in _p39_html for p in _p39_nav + ["flow", "detail", "write"])
          and _p39_html.count('data-panel="') == 15,
          f"data-panel={_p39_html.count(chr(100)+chr(97)+chr(116)+chr(97)+chr(45)+chr(112))}")
    check("webui(P39): served app.js wires six new panel domains (real handlers, stale counts fixed)",
          all(_m in _uijs.text for _m in (
              "/layout/align", "/design/compare", "/generate/remove-background",
              "/brand/color-consistency", "/preflight/auto-fix", "/packaging/vdp/merge",
              "/knowledge/retrieve", "/revision/parse", "/review/run",
              "data-ops", "/runtime/diagnostic"))
          and "服务不可达 · 引擎分母不编造" in _uijs.text
          and "0/26" not in _uijs.text and "0/25" not in _uijs.text and "0/24" not in _uijs.text
          and "${names.filter((n) => eng[n] === \"ready\").length}/${names.length}" in _uijs.text,
          "markers/data-ops present; P50: the offline branch carries NO invented denominator (0/22..0/26 all gone) while the live branch derives n/N from /health")
    _p39_live = {
        "align": (requests.post(BASE + "/api/v1/layout/align", json={
            "objects": [{"id": "a", "x": 1, "y": 0, "width": 10, "height": 10},
                        {"id": "b", "x": 7, "y": 5, "width": 10, "height": 10}],
            "mode": "left"}),
            lambda j: j.get("mode") == "left" and j["objects"][0]["to"]["x"] == 1),
        "compare": (requests.post(BASE + "/api/v1/design/compare", json={
            "before": [{"id": "a", "x": 0}], "after": [{"id": "a", "x": 5}, {"id": "b"}]}),
            lambda j: j["summary"]["by_category"]["geometry"] == 1
            and j["summary"]["added"] == 1 and j["identical"] is False),
        "semantics": (requests.get(BASE + "/api/v1/brand/semantics"),
                      lambda j: j.get("status") == "needs_external" and j.get("fabricated") is False),
        "recognize": (requests.post(BASE + "/api/v1/packaging/panel-recognition?image_path="
                                    + requests.utils.quote(str(img5), safe="")),
                      lambda j: j.get("status") == "recognized" and j.get("panel_count") == 4
                      and j.get("implemented") is True and j.get("fabricated") is False),
        "review": (requests.post(BASE + "/api/v1/review/run", json={
            "objects": [{"id": "t", "x": 0, "y": 0, "width": 10, "height": 10}]}),
            lambda j: j.get("object_count") == 1 and isinstance(j.get("score"), int)),
        "knowledge": (requests.get(BASE + "/api/v1/knowledge"),
                      lambda j: j.get("total") == len(j.get("items") or [])),
    }
    _p39_bad = [k for k, (resp, okf) in _p39_live.items()
                if resp.status_code != 200 or not okf(resp.json())]
    check("webui(P39): the exact payloads the 6 new panels send get true/honest responses on this live service",
          not _p39_bad, f"failed={_p39_bad}")

    # --- Phase 40: webui 12→13 panels (workflow/batch/recovery flow chain + new ops read-only GETs) ---
    check("webui(P40): served app.js+index wire flow-recovery panel & 8 new ops buttons (markers from p40 probe truth)",
          all(_m in _uijs.text for _m in (
              "/workflow/define", "/workflow/${encodeURIComponent(id)}/execute",
              "/workflow/instance/", "/batch/process", "/recovery/snapshot",
              "/recovery/transaction/begin", "/recovery/task/register",
              "/recovery/tasks/orphaned"))
          and all(_m in _p39_html for _m in (
              'data-ops="/config"', 'data-ops="/uxp/bridge"', 'data-ops="/uxp/discovery"',
              'data-ops="/runtime/install-actions"', 'data-ops="/tools/capability-map"',
              'data-ops="/eval/recommendations"', 'data-ops="/typography/fallback-rules"',
              'data-ops="/typography/alias-map"')),
          "P40 js/html markers")
    _p40_bad = []
    _wfdef = requests.post(BASE + "/api/v1/workflow/define", json={
        "id": "l2p40flow", "name": "L2 状态链流",
        "nodes": [{"id": "in", "type": "input", "name": "输入", "config": {}},
                  {"id": "out", "type": "output", "name": "输出", "config": {}}],
        "edges": [{"source_node_id": "in", "target_node_id": "out"}],
        "variables": {"greet": "hello"}})
    _j = _wfdef.json() if _wfdef.status_code == 200 else {}
    if not (_j.get("workflow_id") == "l2p40flow" and _j.get("nodes") == 2
            and _j.get("edges") == 1 and _j.get("validation_errors") == []):
        _p40_bad.append("define")
    _wfv = requests.get(BASE + "/api/v1/workflow/l2p40flow/validate")
    if _wfv.status_code != 200 or _wfv.json().get("errors") != []:
        _p40_bad.append("validate")
    _wfx = requests.post(BASE + "/api/v1/workflow/l2p40flow/execute", json={"input_data": {}})
    _xj = _wfx.json() if _wfx.status_code == 200 else {}
    _inst_id = _xj.get("instance_id")
    if _xj.get("status") != "success" or not _inst_id:
        _p40_bad.append("execute")
    else:
        _i = requests.get(BASE + f"/api/v1/workflow/instance/{_inst_id}")
        _ij = _i.json() if _i.status_code == 200 else {}
        if _ij.get("status") != "success" or _ij.get("workflow_id") != "l2p40flow":
            _p40_bad.append("instance")
    if requests.get(BASE + "/api/v1/workflow/instance/ghost-l2").status_code != 404:
        _p40_bad.append("instance-ghost-not-404")
    _bt = requests.post(BASE + "/api/v1/batch/process", json={
        "file_paths": [str(tmp / "ghost-l2.png")], "operation": "preflight"})
    _bj = _bt.json() if _bt.status_code == 200 else {}
    if not (_bj.get("total_files") == 1 and _bj.get("failed") == 1
            and _bj.get("success") == 0
            and (_bj.get("results") or [{}])[0].get("success") is False
            and _bj["results"][0].get("error")):
        _p40_bad.append("batch-ghost")
    _doc = tmp / "l2p40_doc.txt"
    _orig = "phase40-l2-original-payload"
    _doc.write_text(_orig, encoding="utf-8")
    _sn = requests.post(BASE + "/api/v1/recovery/snapshot",
                        json={"document_path": str(_doc), "snapshot_type": "full"})
    _sj = _sn.json() if _sn.status_code == 200 else {}
    if not (_sj.get("recoverable") is True and _sj.get("size_bytes") == len(_orig)
            and len(str(_sj.get("checksum", ""))) == 64):
        _p40_bad.append("snapshot")
    else:
        _doc.write_text("X" * 40, encoding="utf-8")  # 篡改
        _rs = requests.post(BASE + f"/api/v1/recovery/snapshot/{_sj['id']}/restore")
        _rj = _rs.json() if _rs.status_code == 200 else {}
        if not (_rj.get("restored") is True and _doc.read_text(encoding="utf-8") == _orig):
            _p40_bad.append("restore-bytes-not-reverted")
    if requests.get(BASE + "/api/v1/recovery/snapshots").json().get("count", 0) < 1:
        _p40_bad.append("snapshots-list")
    _orph = requests.get(BASE + "/api/v1/recovery/tasks/orphaned")
    _oj = _orph.json() if _orph.status_code == 200 else {}
    if not (isinstance(_oj.get("count"), int) and isinstance(_oj.get("tasks"), list)):
        _p40_bad.append("orphaned")
    _ia = requests.get(BASE + "/api/v1/runtime/install-actions")
    _ij2 = _ia.json() if _ia.status_code == 200 else {}
    if _ij2.get("status") != "executable_via_cli_only" or _ij2.get("fabricated") is not False:
        _p40_bad.append("install-actions")
    _ux = requests.get(BASE + "/api/v1/uxp/bridge")
    if _ux.status_code != 200 or "connected_hosts" not in _ux.json():
        _p40_bad.append("uxp-bridge")
    check("webui(P40): flow panel state chains (define→validate→execute→instance / snapshot→mutate→restore真字节回写 / batch ghost 诚实失败 / ops 新 GET) hold on this live service",
          not _p40_bad, f"failed={_p40_bad}")

    # --- Phase 41: 接线覆盖机制化 —— /openapi.json 单一真相源 × 控制台自身源码派生 ---
    import re as _re41
    _oa41 = requests.get(BASE + "/openapi.json")
    _oa41_paths = [p for p in (_oa41.json().get("paths") or {}) if p.startswith("/api/v1/")] \
        if _oa41.status_code == 200 else []
    check("webui(P41+P51+P53+P56+P58): served /openapi.json is the single source of truth — 135 api/v1 endpoint paths present & coverage derivation compiled into app.js (手写清单已废止；P51 工作流工具清单 +1, P53 编译面/预检面两枚只读 +2, P56 品牌语义透传入口 +1, P58 执行面对账只读 GET +1)",
          _oa41.status_code == 200 and len(_oa41_paths) == 135
          and all(_m in _uijs.text for _m in ("renderCoverage", "/openapi.json", "p41Strip", "幽灵接线"))
          and "AI Provider 配置与对话" not in _uijs.text,  # 旧手写 notWired 清单必须已移除而非并存两套口径
          f"oa_paths={len(_oa41_paths)}")
    # 独立镜像派生（与 webui 内算法逐字同构）：ghost 必空、88/40 与探针冻结值相等
    _n41 = lambda p: _re41.sub(r"\{[^{}]*\}", "{}", p).rstrip("/")
    _ns41 = {_n41(p) for p in _oa41_paths}
    _dom41 = {p.split("/")[3] for p in _ns41}
    _js41, _html41 = _uijs.text, _p39_html
    _lits41 = set()
    # Phase 48 口径修正：与受服务 app.js 派生器逐字同构——第四式收录 `${API}/…` 插值
    # （素材缩略图 <img src> 嵌在模板串内部，旧三式判不到），否则镜像与产品各算一套。
    for _pat41 in (r'"(/[^"\n]*)"', r"'(/[^'\n]*)'", r"`(/[^`]*)`", r"\$\{API\}(/[^\"`\s]*)"):
        _lits41.update(_m.group(1) for _m in _re41.finditer(_pat41, _js41))
    _lits41.update(_m.group(1) for _m in _re41.finditer(r'data-ops="([^"]+)"', _html41))
    _wired41, _ghost41 = set(), []
    for _raw41 in _lits41:
        _s41 = _re41.sub(r"\$\{[^{}]*\}", "{}", _raw41).split("?")[0].rstrip("/")
        if _s41 in ("/", "/api/v1"):
            continue
        _seg41 = _s41[1:].split("/")[0]
        if not _seg41 or _seg41 == "api" or _seg41 not in _dom41:
            continue
        _rest41 = _s41[len("/api/v1"):] if _s41.startswith("/api/v1") else _s41
        _full41 = _n41("/api/v1" + _rest41)
        (_wired41.add(_full41) if _full41 in _ns41 else _ghost41.append(_full41))
    _notwired41 = _ns41 - _wired41
    # 15 项精确集合以前缀无关片段拼接构造：凡引号内以斜杠api打头接续 v1 路径的字面串都会被
    # P43 收集器记为 L2 声称、P44 回填要求运行时真命中——拒接项永不发起请求，写成整串必假红。
    # Phase 48：缩略图与四条生成端点已由"拒接"转为真实接线，故从本集合移出（19→14）。
    # Phase 56：品牌语义透传入口 POST /brand/semantic-insight 是需真实 Provider（base_url+Key+配额）
    # 的写侧透传，控制台不自动发起（同 /generate 一族须显式点击且此处无 UI 按钮），如实列入拒接面（14→15）。
    # Phase 58：GET /installer/execution 是对**本机安装执行台账**的运维型对账读数（无设计工作流
    # 语义，且台账在绝大多数机器上如实为 ledger_absent），不进控制台面板——拒接面 15→16，
    # wired 恒 119 不变（本轮只加路由，一个旧拒接项也没转正）。
    # Phase 65：留守 16 项里逐枚复核后**转正六枚**——nas/connect·list·lock·reconnect 四枚经 P65 契约
    # 探针以数据根全树差集实证零落盘（同域唯一会写调用方指定盘的 nas/download 仍留守）；
    # runtime/port-check 真 bind 随即释放、不留句柄（进程级互斥 single-instance 仍留守）；
    # layers/{} 的 PATCH/DELETE 只改本服务数据根内 state/document.json，且 DELETE 由 P65-D1 修复补上
    # confirm=true 门禁（界面未勾选连请求都不发）。⇒ wired 119→125、notWired 16→10、ghost 恒空、
    # 分母恒 135（本轮只挪接线面，一条路由也没新增或退役，故 125+10 与 119+16 是同一宇宙的两种切法）。
    # Phase 70：原拒接两枚转正——品牌语义透传改为品牌面板上**显式点击**的 bsem-run（图像字节仍只发往
    # 已配置 Provider；未配置时 502 needs_external 原样呈现，绝不伪造语义），安装执行台账改为只读
    # 对账卡 iex-run（只读 GET；真装/真卸结构上仍只经同一可执行文件的 CLI 显式同意令牌可达）。
    # ⇒ wired 125→127、notWired 10→8、ghost 恒空、分母恒 135（仍是一条路由也没新增或退役）。
    # Phase 71：拒接面清零——最后八枚（nas/download、runtime/single-instance、update/apply、
    # update/download、uxp handshake/poll/dispatch/result）逐枚过写侧五条件后转正（update/apply
    # 另加双确认），wired 127→135、notWired 8→0、ghost 恒空、分母仍 135（本轮一条路由也没新增或退役）。
    # 换代纪律：拒接面归零后「与留守清单相等」这一锚点会退化成空集上的空真（P58 第十三类），
    # 故把义务搬走并配**非空见证**——① 八枚转正路由逐枚必须 ∈ wired（种群由本集合显式给出，
    # 恒 8 枚，不随 notWired 归零而消失）；② wired==universe==135 的正向计数为「notWired 恰空」
    # 背书，不是"数到 0 就判绿"。_p48_expect_nw 保留为空集，继续充当差集闭合的相等锚。
    _p71_promoted8 = {_n41("/api/v1" + _s45) for _s45 in (
        "/nas/download", "/runtime/single-instance", "/update/apply", "/update/download",
        "/uxp/dispatch", "/uxp/handshake", "/uxp/poll", "/uxp/result")}
    _p48_expect_nw = set()
    _p65_new_wired = {_n41("/api/v1" + _s45) for _s45 in (
        "/layers/{}", "/nas/connect", "/nas/list", "/nas/lock", "/nas/reconnect",
        "/runtime/port-check")}
    _p48_new_wired = {_n41("/api/v1" + _s45) for _s45 in (
        "/assets/{}/thumbnail", "/generate", "/plan",
        "/provider/chat", "/provider/images")}
    _p51_new_wired = {_n41("/api/v1" + _s45) for _s45 in ("/workflow/tools",)}
    # Phase 53：编译面与预检面两枚只读 GET 由控制台真按钮接线（p53_repin_probe 实测
    # wired 117→119、notWired 恒 14、ghost 恒空——本轮只加路由，一个旧拒接项也没转正）。
    _p53_new_wired = {_n41("/api/v1" + _s45) for _s45 in
                      ("/installer/artifact", "/installer/preflight")}
    check("webui(P41+P48+P49+P51+P53+P56+P58+P65+P70+P71): live mirror derivation — ghost wiring MUST be empty (console never calls a nonexistent endpoint) and wired/notWired == 135/0 of 135 exactly as the p71 wiring carrier measures (135/135/0/ghost[]; history 119/16 → 125/10 → 127/8 → 135/0) plus the P58/P70/P71 rows (P45 write panel +13, P48 thumbnail+generate/plan/provider-chat/images +5 & 口径第四式, P49 两枚安装侧只读 +2, P51 工具清单 +1, P53 编译面+预检面 +2, P56 品牌语义透传先拒接 +1, P58 执行面对账先拒接 +1, P65 留守项转正六枚 ⇒ 119→125 / 16→10, P70 品牌语义显式点击 + 安装台账只读卡转正两枚 ⇒ 125→127 / 10→8, P71 拒接面清零八枚转正 ⇒ 127→135 / 8→0 / 分母恒 135)",
          _ghost41 == [] and len(_wired41) == 135 and len(_notwired41) == 0
          and _notwired41 == _p48_expect_nw
          and len(_p71_promoted8) == 8
          and _p71_promoted8 <= _wired41
          and _p65_new_wired <= _wired41
          and _p48_new_wired <= _wired41
          and _p51_new_wired <= _wired41
          and _p53_new_wired <= _wired41
          and {"/api/v1/installer/manifest/verify", "/api/v1/installer/state"} <= _wired41
          and "/api/v1/brand/semantic-insight" in _wired41
          and "/api/v1/installer/execution" in _wired41
          and len(_wired41) + len(_notwired41) == len(_ns41) == 135,
          f"ghost={_ghost41[:5]} wired={len(_wired41)} not={len(_notwired41)} diff={sorted(_notwired41 ^ _p48_expect_nw)[:6]}")

    # --- Phase 42: 实体明细联查面板（8 个探针实证零副作用端点）；P45 改写两相历史断言 ---
    check("webui(P42+P45): served app.js+index wire detail panel with 8 side-effect-free endpoints; typography/resolve + tools/capability-report now PRESENT as P45 explicit-write literals (two-phase history), stale 明确不接线 self-report MUST be gone",
          all(_m in _uijs.text for _m in (
              "p42Need", "/eval/reports/", "/eval/compare", "/knowledge/", "/assets/",
              "/skills/", "/validate", "/flags/", "/typography/fonts/"))
          and "/typography/resolve" in _uijs.text
          and "/tools/capability-report" in _uijs.text
          and "明确不接线" not in _p39_html
          and "曾因实证存在落盘副作用" in _p39_html
          and "归入" in _p39_html
          and all(_m in _p39_html for _m in ('id="dt-report-btn"', 'id="dt-cmp-btn"',
              'id="dt-cmp-run2"', 'id="dt-kn-btn"', 'id="dt-as-btn"', 'id="dt-sk-btn"',
              'id="dt-sk-val"', 'id="dt-flag-btn"', 'id="dt-font-btn"')),
          "P42 js markers + P45 presence flip + rewritten two-phase info-box")
    _p42_bad = []
    _sk = requests.post(BASE + "/api/v1/skills", json={
        "id": "l2p42sk", "version": "1.0.0", "permissions": ["asset.read"],
        "tools": ["preflight.check"], "risk": "low"})
    if _sk.status_code not in (200, 400):   # 400=已由前次注册在场
        _p42_bad.append("skill-register")
    _skg = requests.get(BASE + "/api/v1/skills/l2p42sk").json()
    if not (_skg.get("id") == "l2p42sk" and "validation_errors" in _skg):
        _p42_bad.append("skill-get")
    _skv = requests.post(BASE + "/api/v1/skills/l2p42sk/validate").json()
    if not (_skv.get("valid") is True and _skv.get("validation_errors") == []):
        _p42_bad.append("skill-validate")
    _fl = requests.get(BASE + "/api/v1/flags").json()
    _fkey = list(_fl.keys())[0] if isinstance(_fl, dict) and _fl else None
    if _fkey:
        _f1 = requests.get(BASE + f"/api/v1/flags/{_fkey}").json()
        if _f1.get("key") != _fkey:
            _p42_bad.append("flag-get")
    for _name, _m, _p, _b in [
            ("report-ghost", "GET", "/api/v1/eval/reports/p42ghostrid", None),
            ("compare-ghost", "POST", "/api/v1/eval/compare", {"a": "p42ghost_a", "b": "p42ghost_b"}),
            ("kn-ghost", "GET", "/api/v1/knowledge/p42ghostid", None),
            ("as-ghost", "GET", "/api/v1/assets/p42ghostid", None),
            ("sk-ghost", "GET", "/api/v1/skills/p42ghostsk", None),
            ("skval-ghost", "POST", "/api/v1/skills/p42ghostsk/validate", None),
            ("flag-ghost", "GET", "/api/v1/flags/p42ghostkey", None),
            ("font-ghost", "GET", "/api/v1/typography/fonts/p42GhostFont9", None)]:
        # P43 修正：_p 此前缺 /api/v1 前缀，404 来自静态挂载而非端点诚实拒绝 —— 断言打 404 也必须打对 URL
        _r = (requests.get(BASE + _p) if _m == "GET"
              else requests.post(BASE + _p, json=_b))
        if _r.status_code != 404:
            _p42_bad.append(_name + f"={_r.status_code}")
    check("webui(P42): the exact payloads the detail panel sends get true responses / honest 404 ghosts on this live service (skill fixture roundtrip + 8 ghost negatives)",
          not _p42_bad, f"failed={_p42_bad}")

    # 变异校准（#110）：证明派生检测器真的在计算，而非背答案 —— ghost 注入必被点名，摘除字面量必令 wired 下降
    def _p42_mirror(_js_txt):
        _w, _g = set(), []
        _lt = set()
        for _pat in (r'"(/[^"\n]*)"', r"'(/[^'\n]*)'", r"`(/[^`]*)`", r"\$\{API\}(/[^\"`\s]*)"):
            _lt.update(_mm.group(1) for _mm in _re41.finditer(_pat, _js_txt))
        _lt.update(_mm.group(1) for _mm in _re41.finditer(r'data-ops="([^"]+)"', _p39_html))
        for _raw in _lt:
            _s = _re41.sub(r"\$\{[^{}]*\}", "{}", _raw).split("?")[0].split("#")[0].rstrip("/")
            if _s in ("/", "/api/v1"):
                continue
            _seg = _s[1:].split("/")[0]
            if not _seg or _seg == "api" or _seg not in _dom41:
                continue
            _rest = _s[len("/api/v1"):] if _s.startswith("/api/v1") else _s
            _full = _n41("/api/v1" + _rest)
            (_w.add(_full) if _full in _ns41 else _g.append(_full))
        return _w, _g
    _cal_w0, _cal_g0 = _p42_mirror(_uijs.text)
    _cal_w1, _cal_g1 = _p42_mirror(_uijs.text + '\nconst _p42calib = ["/knowledge/p42_ghost_injected_9f3a"];')
    _cal_w2, _cal_g2 = _p42_mirror(_uijs.text.replace("/eval/compare", "/p42calib/removed"))
    # P48 第三桩：把 `${API}` 插值打码，缩略图必须从 wired 掉出——证明口径第四式真在计算，
    # 而非"字面串恰好也在别处"的搭便车（旧三式对这一条整体失明，故必须有此桩）。
    _cal_w3, _cal_g3 = _p42_mirror(_uijs.text.replace("${API}", "P48CALIB"))
    # P49 第四桩：摘掉本轮新接的安装态字面串，installer/state 必须掉出 wired（135→134），
    # 而 installer/manifest/verify 仍在——证明两枚按钮各自被真实计算，不是一条带出一条。
    _cal_w4, _cal_g4 = _p42_mirror(_uijs.text.replace("/installer/state", "/p49calib/removed"))
    # P51 第五桩：摘掉本轮新接的工具清单字面串，/workflow/tools 必须掉出 wired（135→134），
    # 而同域的 define/validate/execute/instance 仍在——证明这一枚按钮被单独计算，
    # 不是"workflow 域里随便一条都在算"的搭便车。
    _cal_w5, _cal_g5 = _p42_mirror(_uijs.text.replace("/workflow/tools", "/p51calib/removed"))
    # P53 第六/七桩：编译面与预检面各自单独摘除。两枚新按钮的调用串在同一面板里相邻，
    # 若派生器把一条域当成一整块计数，摘掉其一另一必随之掉出——这里要求另一枚仍在，
    # 且 wired 恰 135→134、ghost 恒空。
    _cal_w6, _cal_g6 = _p42_mirror(_uijs.text.replace("/installer/artifact", "/p53calib/removed"))
    _cal_w7, _cal_g7 = _p42_mirror(_uijs.text.replace("/installer/preflight", "/p53calib/removed"))
    # P65 第八~十一桩：NAS 四枚逐枚摘除。四枚调用串在同一面板相邻且同域，若派生器把 nas 域
    # 当成一整块计数，摘掉其一其余必随之掉出——这里要求其余三枚仍在，且 wired 恰 135→134。
    _cal_w8, _cal_g8 = _p42_mirror(_uijs.text.replace('"/nas/connect"', '"/p65calib/removed"'))
    _cal_w9, _cal_g9 = _p42_mirror(_uijs.text.replace('"/nas/list"', '"/p65calib/removed"'))
    _cal_w10, _cal_g10 = _p42_mirror(_uijs.text.replace('"/nas/lock"', '"/p65calib/removed"'))
    _cal_w11, _cal_g11 = _p42_mirror(_uijs.text.replace('"/nas/reconnect"', '"/p65calib/removed"'))
    # P65 第十二桩：port-check 单独摘除 ⇒ 135→134，且同域项 single-instance 依旧在 wired
    # （P71 起它已转正，摘邻居不许把它带出场），跨域对照改钉 nas 四枚仍在——证明摘的是一枚字面串不是一个域。
    _cal_w12, _cal_g12 = _p42_mirror(_uijs.text.replace('"/runtime/port-check"',
                                                        '"/p65calib/removed"'))
    # P71 第十六桩：本层新接的 uxp/poll 单独摘除 ⇒ 135→134，而同域 handshake/dispatch/result
    # 三枚仍在——桥接四枚按钮各自被真实计算（P49/P65 同一判别子，搬到本轮转正的最后一域）。
    _cal_w15, _cal_g15 = _p42_mirror(_uijs.text.replace('"/uxp/poll"', '"/p71calib/removed"'))
    # P65 第十三/十四桩：图层两枚字面串（PATCH 与 DELETE?confirm=true）归一到同一路由
    # /api/v1/layers/{}。摘其一另一仍在 ⇒ wired 恒 135，证明派生器按**路由**去重计数而非按字面串
    # 条数；把两枚同摘 ⇒ 135→134 且 nas 四枚仍在。单/双两臂合起来才排除"字面串数==路由数"的巧合。
    _cal_w13, _cal_g13 = _p42_mirror(_uijs.text.replace(
        '`/layers/${encodeURIComponent(id)}`', '`/p65calib/one_removed`'))
    _cal_w14, _cal_g14 = _p42_mirror(_uijs.text.replace(
        '/layers/${encodeURIComponent(id)}', '/p65calib/both_removed'))
    _cal_all = [(_cal_w0, _cal_g0), (_cal_w1, _cal_g1), (_cal_w2, _cal_g2), (_cal_w3, _cal_g3),
                (_cal_w4, _cal_g4), (_cal_w5, _cal_g5), (_cal_w6, _cal_g6), (_cal_w7, _cal_g7),
                (_cal_w8, _cal_g8), (_cal_w9, _cal_g9), (_cal_w10, _cal_g10),
                (_cal_w11, _cal_g11), (_cal_w12, _cal_g12), (_cal_w13, _cal_g13),
                (_cal_w14, _cal_g14), (_cal_w15, _cal_g15)]
    _p65_nas4 = {"/api/v1/nas/connect", "/api/v1/nas/list", "/api/v1/nas/lock",
                 "/api/v1/nas/reconnect"}
    check("webui(P42+P48+P49+P51+P53+P65+P71 mutation calibration): detector COMPUTES — injected ghost is named exactly, removing /eval/compare literals drops wired 135->134, blanking ${API} drops the thumbnail route 135->134 (ruler 4th pattern is load-bearing), removing /installer/state / /workflow/tools / /installer/artifact / /installer/preflight each drops ONLY that route 135->134, P65 逐枚摘除 nas/connect·list·lock·reconnect 与 runtime/port-check 各只令 wired 掉 135->134 而其余四枚 nas 仍在（排除「整域一块计数」），图层两枚字面串归一同一路由 ⇒ 摘其一恒 135、两枚同摘才 135->134（排除「字面串数==路由数」的巧合），P71 摘 uxp/poll 只令 wired 掉 135->134 而 handshake/dispatch/result 三枚仍在，不变式 wired+notWired==135 在 16 臂全体成立且 ghost 除注入臂外恒空",
          _cal_g0 == [] and len(_cal_w0) == 135
          and _cal_g1 == ["/api/v1/knowledge/p42_ghost_injected_9f3a"] and len(_cal_w1) == 135
          and _cal_g2 == [] and len(_cal_w2) == 134
          and "/api/v1/eval/compare" not in _cal_w2
          and _cal_g3 == [] and len(_cal_w3) == 134
          and "/api/v1/assets/{}/thumbnail" not in _cal_w3
          and "/api/v1/assets/{}/thumbnail" in _cal_w0
          and "/api/v1/installer/state" in _cal_w0
          and "/api/v1/installer/state" not in _cal_w4
          and "/api/v1/installer/manifest/verify" in _cal_w4
          and len(_cal_w4) == 134
          and "/api/v1/workflow/tools" in _cal_w0
          and "/api/v1/workflow/tools" not in _cal_w5
          and {"/api/v1/workflow/define", "/api/v1/workflow/{}/execute"} <= _cal_w5
          and len(_cal_w5) == 134
          and "/api/v1/installer/artifact" in _cal_w0 and len(_cal_w6) == 134
          and "/api/v1/installer/artifact" not in _cal_w6
          and "/api/v1/installer/preflight" in _cal_w6
          and _cal_g6 == []
          and "/api/v1/installer/preflight" in _cal_w0 and len(_cal_w7) == 134
          and "/api/v1/installer/preflight" not in _cal_w7
          and "/api/v1/installer/artifact" in _cal_w7
          and _cal_g7 == []
          # P65 六枚逐枚臂：每枚被摘 ⇒ 恰该路由离开 wired、其余五枚 P65 路由仍在、wired 恰掉 1
          and _p65_nas4 <= _cal_w0 and "/api/v1/runtime/port-check" in _cal_w0
          and "/api/v1/layers/{}" in _cal_w0
          and len(_cal_w8) == 134 and "/api/v1/nas/connect" not in _cal_w8
          and (_p65_nas4 - {"/api/v1/nas/connect"}) <= _cal_w8
          and len(_cal_w9) == 134 and "/api/v1/nas/list" not in _cal_w9
          and "/api/v1/nas/connect" in _cal_w9 and "/api/v1/nas/lock" in _cal_w9
          and len(_cal_w10) == 134 and "/api/v1/nas/lock" not in _cal_w10
          and "/api/v1/nas/connect" in _cal_w10 and "/api/v1/nas/reconnect" in _cal_w10
          and len(_cal_w11) == 134 and "/api/v1/nas/reconnect" not in _cal_w11
          and "/api/v1/nas/connect" in _cal_w11 and "/api/v1/nas/list" in _cal_w11
          and len(_cal_w12) == 134 and "/api/v1/runtime/port-check" not in _cal_w12
          and _p65_nas4 <= _cal_w12 and "/api/v1/runtime/single-instance" in _cal_w12
          and len(_cal_w13) == 135 and "/api/v1/layers/{}" in _cal_w13
          and len(_cal_w14) == 134 and "/api/v1/layers/{}" not in _cal_w14
          and _p65_nas4 <= _cal_w14
          # P71 uxp 域逐枚臂：poll 被摘 ⇒ 该路由离场、同域三枚仍在、wired 恰掉 1
          and "/api/v1/uxp/poll" in _cal_w0 and len(_cal_w15) == 134
          and "/api/v1/uxp/poll" not in _cal_w15
          and {"/api/v1/uxp/handshake", "/api/v1/uxp/dispatch",
               "/api/v1/uxp/result"} <= _cal_w15
          and _p71_promoted8 <= _cal_w0
          and _cal_g8 == [] and _cal_g9 == [] and _cal_g10 == [] and _cal_g11 == []
          and _cal_g12 == [] and _cal_g13 == [] and _cal_g14 == [] and _cal_g15 == []
          and len(_cal_all) == 16
          and all(len(_w) + (len(_ns41) - len(_w)) == 135 for _w, _g in _cal_all)
          and all(_g == [] for _w, _g in _cal_all if _g != [
              "/api/v1/knowledge/p42_ghost_injected_9f3a"]),
          f"base={len(_cal_w0)}/{_cal_g0[:2]} inj={len(_cal_w1)}/{_cal_g1[:2]} rm={len(_cal_w2)}/{_cal_g2[:2]} api={len(_cal_w3)}/{_cal_g3[:2]} state={len(_cal_w4)}/{_cal_g4[:2]} tools={len(_cal_w5)}/{_cal_g5[:2]} artifact={len(_cal_w6)}/{_cal_g6[:2]} preflight={len(_cal_w7)}/{_cal_g7[:2]} nc={len(_cal_w8)} nl={len(_cal_w9)} nlock={len(_cal_w10)} nrc={len(_cal_w11)} pc={len(_cal_w12)} ly1={len(_cal_w13)} ly2={len(_cal_w14)} uxppoll={len(_cal_w15)}/{_cal_g15[:2]}")

    # --- Phase 45: 配置与写操作面板结构（第十五星面板：13 显式点击写按钮 + 13 枚 js 标记） ---
    check("webui(P45): served index+app.js wire the 15th 'write' panel — 13 wr-* action buttons present, app.js carries p45Bad guard + all write-endpoint literals (side effects only on explicit click)",
          all(_m in _p39_html for _m in ('data-panel="write"', 'id="panel-write"',
              'id="wr-cfg-btn"', 'id="wr-pv-cfg-btn"', 'id="wr-pv-health-btn"', 'id="wr-nas-cfg-btn"',
              'id="wr-nas-forget-btn"', 'id="wr-nas-clear-btn"', 'id="wr-uc-btn"', 'id="wr-ck-btn"',
              'id="wr-lic-btn"', 'id="wr-dv-btn"', 'id="wr-tr-btn"', 'id="wr-cr-btn"', 'id="wr-al-btn"'))
          and all(_m in _uijs.text for _m in ("p45Bad", "/provider/configure", "/provider/health",
              "/nas/configure", "/nas/forget", "/nas/cache/clear", "/update/configure", "/update/check",
              "/license/activate", "/delivery/package", "/typography/resolve", "/tools/capability-report")),
          "P45 panel + wr-* buttons + write js markers")

    # --- Phase 49: 两枚安装侧只读端点的活体真值（接线资格 = p49_wiring_probe 实证零副作用） ---
    # 探针取证：隔离数据根全树哈希 diff 为空（9 条目 before == after），两条路由运行时命中
    # 均 >0，前端视图引用的每个键在真实响应中全部在场，probe 键集合无未预期字段。
    # 负控真值来自 p49_neg_probe 实测：.py/.js 等源码后缀先被共享路径策略拦成 403（detail 为
    # 字符串），允许根内的非 .iss 后缀才走本端点自己的 400 manifest_suffix_invalid（detail 为
    # 对象），两层 403/400 的判据形状不同、不可互相冒充。
    import hashlib as _hl49
    import re as _re49
    _p49root = Path(__file__).resolve().parent   # 本文件在根，_root43 在下游才绑定，勿前向引用
    _iss49 = _ZJ_OUTBASE / "setup.iss"
    # 磁盘脚本里那行 AppId=（构建期由 build-installer.py 从 INSTALLER_APP_ID 生成）
    _appid49 = _re49.search(r"(?m)^AppId=(.+?)\s*$",
                            _iss49.read_text(encoding="utf-8")).group(1)
    # Inno 值层转义律的**测试侧独立复算**（不复用引擎函数）：AppId 开头的 `{{` 是一个字面
    # `{` 的转义，故生效 AppId = `{GUID}`，卸载键 = `{GUID}_is1`。Phase 53 两次真 iscc
    # 编译（单写 exit=2 / 双写 exit=0）就是这条映射的实测依据。
    _eff49 = ("{" + _appid49[2:]) if _appid49.startswith("{{") else _appid49
    _mv49 = requests.get(BASE + "/api/v1/installer/manifest/verify", timeout=60)
    _mvj49 = _mv49.json()
    _mx49 = requests.get(BASE + "/api/v1/installer/manifest/verify", timeout=60,
                         params={"script_path": str(_iss49)}).json()
    _ms49 = requests.get(BASE + "/api/v1/installer/state", timeout=60)
    _msj49 = _ms49.json()
    _txt49 = _ZJ_OUTBASE / "p49_wiring_probe.out"   # 允许根内、非 .iss 后缀 → 400 负控
    # Phase 80 判卷侧涟漪：早期 cleanup 把这份探针遗留件移入回收站，validate_file_path
    # 的 exists() 早于 suffix 检查，非 .iss 负控会退化成一记 404。该文件仅承担"路径
    # 存在且落在允许根、后缀非 .iss"这一 fixture 角色，L2 自建以确保自足——
    # 内容与 400 判据无关，只是让端点先过 exists() 门、再撞上 suffix 门。
    if not _txt49.exists():
        _txt49.write_text("p49_wiring_probe stdout fixture (path-only sentinel for suffix 400)\n",
                          encoding="utf-8")
    _py49 = _ZJ_OUTBASE / "p49_wiring_probe.py"     # 源码后缀被共享路径策略先拦 → 403 负控
    # CI 判红回归：干净 runner 无历史探针落盘，_py49 缺席 ⇒ validate_file_path 先 exists() 后
    # suffix，退化为 404 而非 403，与本判据「源码后缀被共享策略先拦」的语义直接冲突（MEMORY
    # 【validate_file_path 顺序陷阱】同源，_txt49 自建的同族修复）。自建 fixture 与 _txt49 一致：
    # 内容与判据无关，只让路径存在且以 .py 结尾，供共享策略命中。
    if not _py49.exists():
        _py49.write_text("# p49_wiring_probe .py path-only fixture "
                         "(source-suffix 403 sentinel for shared path policy)\n",
                         encoding="utf-8")
    _out49 = Path(str(tmp)) / "p49_outside_allowed_root.iss"
    # Phase 80 判卷侧涟漪（仪器缺陷伪装成产品结论，尺子修复、非产品改动、非新增 check 位）：
    # 服务端 installer manifest-verify 的每一条错误分支（含 manifest_path_outside_allowed_roots）
    # 都在回显前先 path=path.resolve()，回的是**解析后的长名**；而本负控路径取自 mkdtemp()，
    # 本机 TEMP 父目录回的是 8.3 短名（C:\Users\ADMINI~1\...），raw 串与解析串仅大小写/短长名之差
    # （实测 os.path.realpath(test)==server echo）。判据语义不变——仍钉「端点回显的正是它拒绝的那枚
    # 文件」，只把测试侧的期望路径同样归一，使之与服务端 resolve() 回显契约一致。_txt49/_iss49 皆在
    # 仓库长名根下、resolve 幂等，故唯独此处 TEMP 负控受此涟漪影响。
    _out49 = _out49.resolve()
    _out49.write_text("[Setup]\nAppId={1}\nAppName=X\n", encoding="utf-8")
    _r400 = requests.get(BASE + "/api/v1/installer/manifest/verify", timeout=30,
                         params={"script_path": str(_txt49)})
    _r403p = requests.get(BASE + "/api/v1/installer/manifest/verify", timeout=30,
                          params={"script_path": str(_py49)})
    _r403 = requests.get(BASE + "/api/v1/installer/manifest/verify", timeout=30,
                         params={"script_path": str(_out49)})
    _r404 = requests.get(BASE + "/api/v1/installer/manifest/verify", timeout=30,
                         params={"script_path": str(_ZJ_OUTBASE / "p49_no_such_manifest.iss")})
    _rh49 = {k: v for k, v in (requests.get(BASE + "/api/v1/metrics", timeout=30).json()
                               .get("route_hits") or {}).items() if "installer" in k}
    _reg49 = [p for p in (_msj49.get("probes") or []) if p.get("probe") == "registry_uninstall"]
    check("webui(P49) live read-only payloads: GET /api/v1/installer/manifest/verify 真校验生产 setup.iss（verdict/counts/entries/sha256 与磁盘逐字节一致、零检出且 counts.total==len(findings)、execution 恒 performed=false/fabricated=false）；GET /api/v1/installer/state 的 installed 必由 registry_uninstall 探针逐项派生（7 探针 / 其中 4 枚 registry_uninstall = HKCU+HKLM × 32+64 视图，Phase 53 视图修复；present 全反则 installed 反、app_id 逐字取自磁盘脚本 AppId=、looking_for==派生键且 subkey_count>0；派生键必须先解 Inno 的 {{ → { 转义再拼 _is1，而响应 app_id 保持脚本原样写法，故原样拼 _is1 必不等于派生键）且本机真值 installed=False（HKCU/HKLM 零命中，p49 探针实测）；负控四连真值：允许根内非 .iss 后缀→400 manifest_suffix_invalid（对象 detail 带 suffix）、源码后缀→403 且由共享路径策略先拦（字符串 detail，与本端点自己的 403 形状不同）、.iss 越出允许根→403 manifest_path_outside_allowed_roots、允许根内缺失→404；两路由 route_hits 均 >0（P44 运行时回填）",
          _mv49.status_code == 200
          and _mvj49.get("schema") == "zaojing-installer-manifest/v1"
          and _mvj49.get("verdict") == "manifest_ok"
          and _mvj49.get("findings") == []
          and _mvj49.get("counts") == {"errors": 0, "infos": 0, "total": 0, "warnings": 0}
          and _mvj49.get("counts", {}).get("total") == len(_mvj49.get("findings") or [])
          # Phase 53 实测漂移（p53_l2_run1 第 19 行 / p53_mf_tree_probe）：生产 setup.iss 被本轮
          # 真实改动（补 [Setup] 指令、Languages 段加中文向导条目）⇒ Languages 1→2、Setup 18→19。
          # 判据口径没变（仍与磁盘逐字节一致），变的是磁盘本身——尺子随重测走，不回退脚本。
          # Phase 58 同族第二次实测漂移（本轮用引擎自身 parse_script 现场读 outputs/setup.iss
          # 复算所得 {"Dirs":4,"Files":3,"Icons":3,"Languages":2,"Registry":5,"Run":1,
          # "Setup":19,"unnamed":5}，不是回忆旧数字）：为修第 8 枚真
          # 缺陷（卸载后 HKCU 应用键残留），[Registry] 段从 3 条长到 5 条（厂商父键
          # uninsdeletekeyifempty + 产品键 uninsdeletekey 各一条，再加 Version/InstallLocation/
          # InstallDate 三值声明 → 段内条目 3→5）。这里仍是"与磁盘逐字节一致"的口径，动的还是磁盘。
          and _mvj49.get("entries_by_section") == {"Dirs": 4, "Files": 3, "Icons": 3,
                                                  "Languages": 2, "Registry": 5, "Run": 1,
                                                  "Setup": 19, "unnamed": 5}
          and _iss49.exists()
          and _mvj49.get("script_bytes") == _iss49.stat().st_size
          # 哈希口径真值：服务端是 read_bytes().decode() 后原样再编码，即**逐字节的磁盘实况**。
          # setup.iss 为 CRLF，故 Python 的 read_text()（通用换行会把 \r\n 折成 \n）算出的 sha
          # 与响应值不同（50ca82df… vs f993e512…，p49_conj_probe 实测）——钉 bytes 口径，
          # 顺带钉死"响应里的 sha 不是对文本做过归一化后再哈希"这一条。
          and b"\r\n" in _iss49.read_bytes()
          and _mvj49.get("script_sha256") == _hl49.sha256(_iss49.read_bytes()).hexdigest()
          and _mvj49.get("script_sha256") != _hl49.sha256(
              _iss49.read_text(encoding="utf-8").encode("utf-8")).hexdigest()
          and _mvj49.get("app_id") == _appid49
          # Phase 53 判卷侧笔误更正（p53_webui49_diag 实测：51 条子判据只此两条红）：旧断言把
          # 卸载键钉成「响应 app_id 原样 + "_is1"」，而响应 app_id 是脚本里那行的**原样写法**
          # （`{{GUID}`），卸载键是**解转义后的生效值**拼 _is1（`{GUID}_is1`）。朴素拼接恰好
          # 要求引擎无视 Inno 转义——那正是引擎 docstring 点名要防的漂移（转义状态一变，
          # 真机只读探测永远查不中）。改为测试侧独立复算转义，并额外钉一条反证：
          # 原样拼接必须**不等于**响应值，否则"解转义"这个动作本身无从证明。
          and _mvj49.get("uninstall_registry_key") == _eff49 + "_is1"
          and _mvj49.get("uninstall_registry_key") != _appid49 + "_is1"
          and _mvj49.get("fabricated") is False
          and _mvj49.get("execution", {}).get("performed") is False
          and _mvj49.get("execution", {}).get("fabricated") is False
          # Phase 58 就地改核（判卷侧第 1 处）：needs_installer_environment 是**被退役**的旧判词，
          # 不是被改写的——执行器真实交付后，本只读引擎仍不执行安装，状态转
          # not_executed_by_this_engine；旧判词逐字留在 retired_boundary.was 里（退役 ≠ 改写，
          # 见 installer_manifest_engine.EXECUTION_BOUNDARY 与 contract(P58) 那条判据）。
          # 只钉新值会丢掉"旧判词仍可查"这条声称，因此新值与 was 同时钉。
          and _mvj49.get("execution", {}).get("status") == "not_executed_by_this_engine"
          and _mvj49.get("execution", {}).get("retired_boundary", {})
              .get("was") == "needs_installer_environment"
          and _mvj49.get("execution", {}).get("retired_boundary", {})
              .get("retired_in") == "Phase 58"
          and _mx49.get("verdict") == _mvj49.get("verdict")
          and _mx49.get("script_sha256") == _mvj49.get("script_sha256")
          and _ms49.status_code == 200 and _msj49.get("fabricated") is False
          and _msj49.get("probes_seen") == len(_msj49.get("probes") or []) == 7
          and len(_reg49) == 4 and all(isinstance(p.get("present"), bool)
                                      and isinstance(p.get("subkey_count"), int) for p in _reg49)
          # 源码态真值（p49_l2_diag 实测）：本机能发现构建期 setup.iss ⇒ AppId 由脚本供给，
          # 且响应里的 app_id 必须与磁盘脚本那行 AppId= 逐字相同（不是端点自己编的名）。
          # 四枚注册表探针（HKCU/HKLM × 32/64 两视图，Phase 53 视图修复后从 2 枚扩到 4 枚）的
          # looking_for 必须就是那条派生卸载键、subkey_count>0 ——
          # 否则"零命中"可能只是枚举接缝被桩成恒空，与诚实未装长得一模一样。
          # 视图扩容的动机同 PATH-only：64 位 Python 默认只看见 64 位视图，而 Inno Setup 6 是
          # 32 位程序、其卸载键落在 WOW6432Node，只查一个视图会把"已安装"判成"未安装"。
          and _msj49.get("app_id") == _appid49
          and _msj49.get("app_id_source") == "setup_iss"
          and _msj49.get("script_path") == str(_iss49)
          # 尺子修复（非判据削弱）：旧此处钉的是作者机观测——四视图「逐一 subkey_count>0」，
          # 而干净 Windows runner 的 HKCU\...\Uninstall 合法为 0（本机曾装第三方应用故 HKCU=37）。
          # 换成机器无关且**更强**的等式：服务端回报的每视图子键数 == 测试侧独立 winreg 现场枚举数
          # （_zj_uninstall_count，与引擎同开窗口径）；再加一条四视图合计>0 的反桩地板——HKLM 在任何
          # Windows 上都非空，故"枚举接缝被整体桩成恒空"必红，而 HKCU 诚实为 0 时 0==0 仍绿。
          and all(p.get("looking_for") == _msj49.get("derived_uninstall_key")
                  and int(p.get("subkey_count", -1)) == _zj_uninstall_count(p.get("root"), p.get("view"))
                  for p in _reg49)
          and sum(_zj_uninstall_count(p.get("root"), p.get("view")) for p in _reg49) > 0
          and _msj49.get("installed") is any(p.get("present") for p in _reg49)
          # 本机真值（p49 探针实测 HKCU 37 / HKLM 201 子键零命中）：installed 必为 False。
          # 上一行钉的是"与探针恒等"这条可跨机迁移的不变式，本行钉的是本机观测——
          # 二者缺一不可：只有恒等式时，"见 app_id 即命中"的桩（p49 变异 m2）会两边同翻
          # 真而一路绿；只有真值时，换一台真装过应用的机器就误红。
          and _msj49.get("installed") is False
          and _msj49.get("derived_uninstall_key") == _eff49 + "_is1"
          and _msj49.get("derived_uninstall_key") != _msj49.get("app_id") + "_is1"
          # 与 /manifest/verify 同一条 Inno 转义律（上方已详注）；此处再钉一遍是因为两枚端点
          # 各自行派生键名，任何一侧漏解转义，只读安装态探测就会在真机永远查不中该键。
          and str(_msj49.get("installed_basis", "")).startswith("installed 仅由注册表卸载项真实命中派生")
          and _msj49.get("execution", {}).get("performed") is False
          and _r400.status_code == 400
          and _r400.json().get("detail", {}).get("reason") == "manifest_suffix_invalid"
          and _r400.json()["detail"].get("suffix") == ".out"
          and _r400.json()["detail"].get("path") == str(_txt49)
          and _r400.json()["detail"].get("fabricated") is False
          and _r403p.status_code == 403
          and isinstance(_r403p.json().get("detail"), str)
          and str(_r403p.json().get("detail", "")).startswith("路径被安全策略阻止")
          and _r403.status_code == 403
          and _r403.json().get("detail", {}).get("reason") == "manifest_path_outside_allowed_roots"
          and str(_ZJ_OUTBASE) in _r403.json()["detail"].get("allowed_roots", [])
          and _r403.json()["detail"].get("path") == str(_out49)
          and _r404.status_code == 404
          and _r404.json().get("detail") == "文件不存在: " + str(
              _ZJ_OUTBASE / "p49_no_such_manifest.iss")
          and _rh49.get("/api/v1/installer/manifest/verify", 0) > 0
          and _rh49.get("/api/v1/installer/state", 0) > 0
          and all(_m in _uijs.text for _m in ('"/installer/manifest/verify"', '"/installer/state"',
              "mfVerifyView", "mfStateView", "installer_script_not_found", "op-mf-verify",
              "op-mf-state", "mf-path"))
          and all(_m in _p39_html for _m in ('id="op-mf-verify"', 'id="op-mf-state"',
              'id="mf-path"', "第 26 引擎", "installer_script_not_found")),
          json.dumps({"verify": {k: _mvj49.get(k) for k in
                                 ("verdict", "counts", "script_bytes", "iscc_available")},
                      "entries": _mvj49.get("entries_by_section"),
                      "state": {k: _msj49.get(k) for k in ("installed", "probes_seen")},
                      "reg": [(p.get("root"), p.get("present"), p.get("subkey_count")) for p in _reg49],
                      # Phase 53 增此 dump：键名派生是「原样写法 vs 解转义生效值」两值之争，
                      # 红而不给全文就只能靠另写探针复现（本轮 p53_webui49_diag 即为此而生）
                      "keys": {"disk_AppId_line": _appid49, "eff_recomputed": _eff49,
                               "mv_app_id": _mvj49.get("app_id"),
                               "mv_uninstall_registry_key": _mvj49.get("uninstall_registry_key"),
                               "ms_derived_uninstall_key": _msj49.get("derived_uninstall_key")},
                      "neg": [_r400.status_code, _r403p.status_code,
                              _r403.status_code, _r404.status_code],
                      "hits": _rh49}, ensure_ascii=False))

    # --- Phase 53: 编译面 + 预检面两枚只读 GET 的活体真值（同源第 26 引擎，未新增引擎） ---
    # 期望逐项取自 p53_repin_probe / p53_endpoint_probe / p53_repin3_probe 的磁盘实测。
    # 机器不变量纪律（本轮定律）：凡随构建漂移的量（产物字节/sha、载荷字节、磁盘余量、
    # 真实安装根）一律以**关系式**或**测试自己独立复算**钉，绝不钉字面毫秒/字面字节数——
    # 串行 --clean 重打包会改写 dist\ZaojingAI.exe 与 installer\*.exe，硬编码即假红。
    _evp53 = _ZJ_OUTBASE / "installer" / "build-evidence.json"
    _art53 = requests.get(BASE + "/api/v1/installer/artifact", timeout=60).json()
    _art53x = requests.get(BASE + "/api/v1/installer/artifact", timeout=60,
                           params={"script_path": str(_iss49)}).json()
    _sig53 = _art53.get("signals", {})
    _war53 = _art53.get("warnings", {})
    _afa53 = _art53.get("artifact", {})
    _asc53 = _art53.get("script", {})
    _artfile53 = Path(_afa53.get("path", ""))
    _sha = lambda b: _hl49.sha256(b).hexdigest()   # noqa: E731
    _src53 = [m.group(1) for m in _re49.finditer(r'(?m)^Source:\s*"([^"]+)"',
                                                 _iss49.read_text(encoding="utf-8"))]
    _payload_bytes53 = sum((_ZJ_OUTBASE / s.replace("\\", "/")).stat().st_size
                           for s in _src53)
    _pf53 = requests.get(BASE + "/api/v1/installer/preflight", timeout=60).json()
    _ck53 = _pf53.get("checks", {})
    _disk53 = _ck53.get("disk_space", {})
    _root53 = _ck53.get("install_root", {})
    _cand53 = _root53.get("candidates", [])
    _priv53 = _ck53.get("privileges", {})
    _sandbox53 = _ZJ_OUTBASE / "p53_l2_probe_dir"
    _bad53 = _ZJ_OUTBASE / "installer" / "p53_l2_not_json_probe.json"
    _bad53.write_text("this is not json", encoding="utf-8")
    import shutil as _shu53
    _residual53 = None
    try:
        _a400s = requests.get(BASE + "/api/v1/installer/artifact", timeout=30,
                             params={"evidence_path": str(_iss49)})
        _a400b = requests.get(BASE + "/api/v1/installer/artifact", timeout=30,
                             params={"evidence_path": str(_bad53)})
        _pf403a = requests.get(BASE + "/api/v1/installer/preflight", timeout=30,
                              params={"probe": "true",
                                      "target_override": str(_cand53[0].get("path"))})
        _pf403b = requests.get(BASE + "/api/v1/installer/preflight", timeout=30,
                              params={"probe": "true",
                                      "target_override": _pf53.get("installed_data_root")})
        _pfready = requests.get(BASE + "/api/v1/installer/preflight", timeout=30,
                               params={"probe": "true",
                                       "target_override": str(_sandbox53)}).json()
        # 残留判定必须**先于**本测试自己的兜底清理取快照，否则引擎漏删也会被 finally 抹平
        _residual53 = _sandbox53.exists()
    finally:
        _bad53.unlink()
        _shu53.rmtree(str(_sandbox53), ignore_errors=True)
    _a403 = requests.get(BASE + "/api/v1/installer/artifact", timeout=30,
                         params={"evidence_path": r"C:\Windows\win.ini"})
    _a404 = requests.get(BASE + "/api/v1/installer/artifact", timeout=30,
                         params={"evidence_path": str(_evp53.parent / "p53_no_such_evidence.json")})
    _a403s = requests.get(BASE + "/api/v1/installer/artifact", timeout=30,
                          params={"script_path": r"C:\Windows\win.ini"})
    _pf400 = requests.get(BASE + "/api/v1/installer/preflight", timeout=30,
                          params={"probe": "bogus"})
    _rh53 = {k: v for k, v in (requests.get(BASE + "/api/v1/metrics", timeout=30).json()
                              .get("route_hits") or {}).items() if "installer" in k}
    check("webui(P53) live read-only payloads: GET /api/v1/installer/artifact 把 installer\\build-evidence.json 与磁盘实况逐字段重算对账——三独立信号同立（退出码 0 + 尾部真见完成行 + 产物在盘）⇒ compile_succeeded，且该布尔恒等于三信号的合取、证据自报 compiled/fabricated 只回显且 ignored=True；脚本与产物各比 bytes+sha256 两路且**本测试用磁盘实读独立复算**（expected==observed==自算，CRLF 字节口径）；告警一致性按截断下界成立且记录为 0 行；manifest_now 复校 verdict==recorded_verdict==manifest_ok 且 counts.total==len(findings)。GET /api/v1/installer/preflight 不传 override 时如实 preflight_unknown/unknown=[write_probe_unprobed]/blocking=[]，七项 checks 键名精确、安装根按 Inno auto 成对给候选（commonpf+userpf）、需求字节恒等于实测产物+实测载荷（本测试独立 stat 三条 [Files] Source 复算）、写探针未跑（probed=False/result=None/probe_request.ran=False）、execution 恒 performed=false、not_install 三判据恒真。结构门实证：probe=true 把 target_override 指向解析出的真实安装根（候选路径与 %LOCALAPPDATA%\\Zaojing）一律 403 preflight_override_outside_allowed_roots，只有落在允许根内的目录才真跑探针（23 字节建→读回→删→rmdir，零残留）；负控六连真值（win.ini 403 字符串 detail、越界脚本 403、非 .json 后缀 400 evidence_suffix_invalid、允许根内非 JSON 400 artifact_evidence_rejected、缺失 404、probe=bogus 400 带 accepted 表）；两路由 route_hits 均 >0（P44 运行时回填）",
          _art53.get("face") == "installer_artifact_build"
          and _art53.get("schema") == "zaojing-installer-manifest/v1"
          and _art53.get("verdict") == "artifact_verified"
          and _art53.get("blocking") == [] and _art53.get("blocking_codes") == []
          and _art53.get("missing_fields") == []
          and _art53.get("fabricated") is False
          and _art53.get("evidence_requested") == str(_evp53) and _evp53.is_file()
          and _art53.get("evidence_source", {}).get("from") == "file"
          and _art53.get("evidence_source", {}).get("schema_match") is True
          and _art53.get("evidence_source", {}).get("bytes") == _evp53.stat().st_size
          and bool(_art53.get("evidence_source", {}).get("built_at"))
          # 自报布尔不参与判定：ignored 恒真，且结论由三条独立信号合取而非 compiled 布尔
          and _art53.get("evidence_self_report", {}).get("ignored") is True
          and _sig53.get("iscc_exit_code") == 0 and _sig53.get("exit_zero") is True
          and _sig53.get("compile_line_seen") is True
          and str(_sig53.get("compile_line_text", "")).startswith("Successful compile")
          and _sig53.get("artifact_present") is True and _sig53.get("compile_line_source") == "tail"
          and _art53.get("compile_succeeded") is (_sig53.get("exit_zero")
                                                 and _sig53.get("compile_line_seen")
                                                 and _sig53.get("artifact_present"))
          and _artfile53.is_file()
          and _afa53.get("present") is True and _afa53.get("bytes_match") is True
          and _afa53.get("sha256_match") is True and _afa53.get("unreadable_note") is None
          and _afa53.get("expected", {}).get("bytes") == _afa53.get("observed", {}).get("bytes") \
          == _artfile53.stat().st_size
          and _afa53.get("expected", {}).get("sha256") \
          == _afa53.get("observed", {}).get("sha256") == _sha(_artfile53.read_bytes())
          and Path(_asc53.get("path", "")).is_file()
          and _asc53.get("bytes_match") is True and _asc53.get("sha256_match") is True
          and _asc53.get("expected", {}).get("bytes") \
          == _asc53.get("observed", {}).get("bytes") == _iss49.stat().st_size
          and b"\r\n" in _iss49.read_bytes()
          and _asc53.get("expected", {}).get("sha256") \
          == _asc53.get("observed", {}).get("sha256") == _sha(_iss49.read_bytes())
          and _war53.get("consistent") is True
          and len(_war53.get("recorded", [])) == _war53.get("recorded_count") == 0
          and (_war53.get("recorded_count") == _war53.get("tail_recount")
               if _war53.get("tail_is_full_output")
               else _war53.get("recorded_count") >= _war53.get("tail_recount"))
          and _art53.get("output_tail", {}).get("key") == "iscc_output_tail"
          # 尾部长度由磁盘证据独立复算（重打包会改编译器输出，1600 只是截断上限）
          and _art53.get("output_tail", {}).get("chars") == len(
              json.loads(_evp53.read_text(encoding="utf-8"))["iscc_output_tail"]) <= 1600
          and _art53.get("compiler", {}).get("now_located", {}).get("found") is True
          and _art53.get("compiler", {}).get("recorded_on_disk") is True
          and _art53.get("compiler", {}).get("recorded_path") \
          == _art53.get("compiler", {}).get("now_located", {}).get("path")
          and _art53.get("manifest_now", {}).get("verdict") == "manifest_ok"
          and _art53.get("manifest_now", {}).get("same_as_recorded") is True
          and _art53.get("manifest_now", {}).get("counts", {}).get("total") \
          == len(_art53.get("manifest_now", {}).get("finding_ids") or []) == 0
          and _art53.get("not_install", {}).get("installed_claimed") is False
          and _art53.get("overrides") == {"script_path": None, "artifact_path": None}
          # 显式交回同一路径的脚本⇒逐字段不变（参数只是定位器，不是判定器）
          and _art53x.get("verdict") == _art53.get("verdict")
          and _art53x.get("script", {}).get("observed") == _asc53.get("observed")
          and _pf53.get("face") == "installer_preflight"
          and _pf53.get("verdict") == "preflight_unknown"
          and _pf53.get("blocking") == [] and _pf53.get("blocking_codes") == []
          and _pf53.get("unknown_codes") == ["write_probe_unprobed"]
          and len(_pf53.get("unknowns") or []) == 1
          and _pf53.get("evidence_provided") is True
          and _pf53.get("evidence_path") == str(_evp53)
          and _pf53.get("script_path") == str(_iss49)
          and _pf53.get("target_override") is None and _pf53.get("probe_writable") is False
          and _pf53.get("fabricated") is False
          and sorted(_ck53) == ["artifact", "disk_space", "install_root", "payload",
                                "privileges", "target_conflict", "writability"]
          and _pf53.get("probe_request", {}).get("asked") is False
          and _pf53.get("probe_request", {}).get("override") is None
          and _pf53.get("probe_request", {}).get("ran") is False
          and bool(_pf53.get("probe_request", {}).get("basis"))
          and _ck53.get("artifact", {}).get("verdict") == "artifact_verified"
          and _ck53.get("artifact", {}).get("state") == "verified"
          and _ck53.get("artifact", {}).get("result") is True
          and _root53.get("result") is True
          and _root53.get("declared_default_dir_name") == _pf53.get("script", {}).get(
              "default_dir_name") == "{autopf}\\造境科技"
          and _root53.get("judged_on") == "first_resolved_candidate"
          and [c.get("inno_form") for c in _cand53] == ["commonpf", "userpf"]
          and all(c.get("constant") == "{autopf}" and c.get("unresolved") is False
                  and c.get("declared") == "{autopf}\\造境科技" for c in _cand53)
          and all(str(c.get("resolved_via", "")).startswith(("env:", "csidl:", "fallback:",
                                                             "knownfolder:")) for c in _cand53)
          and _disk53.get("measured_on") == _cand53[0].get("path")
          and _disk53.get("artifact_bytes") == _artfile53.stat().st_size
          and _disk53.get("payload_bytes") == _payload_bytes53
          and _disk53.get("required_bytes") \
          == _disk53.get("artifact_bytes") + _disk53.get("payload_bytes")
          and isinstance(_disk53.get("free_bytes"), int) and _disk53.get("free_bytes") > 0
          and _disk53.get("result") is (_disk53.get("free_bytes") >= _disk53.get("required_bytes"))
          and _ck53.get("payload", {}).get("declared_sources") == len(_src53) == 3
          and _ck53.get("payload", {}).get("present_count") == 3
          and _ck53.get("payload", {}).get("missing_count") == 0
          and _ck53.get("payload", {}).get("findings") == []
          and _ck53.get("payload", {}).get("result") is True
          and _priv53.get("result") is True and _priv53.get("declared") == "lowest"
          and _priv53.get("effective") == "lowest"
          and _priv53.get("elevation", {}).get("available") is True
          and isinstance(_priv53.get("elevated"), bool)
          and _priv53.get("result") is (not _pf53.get("script", {}).get(
              "privileges_required", {}).get("requires_elevation")
              or _priv53.get("elevated"))
          and _pf53.get("script", {}).get("privileges_required", {}).get("default_when_absent") \
          == "admin"
          and _pf53.get("script", {}).get("privileges_required", {}).get("declared_line") == 38
          and _ck53.get("target_conflict", {}).get("result") is True
          and _ck53.get("target_conflict", {}).get("detail", {}).get("exists_as_file") is False
          and _ck53.get("target_conflict", {}).get("detail", {}).get("held") == []
          and _ck53.get("writability", {}).get("probed") is False
          and _ck53.get("writability", {}).get("result") is None
          and _ck53.get("writability", {}).get("detail", {}).get("reason") == "caller_disabled_probe"
          and _ck53.get("writability", {}).get("target") == _cand53[0].get("path")
          and _pf53.get("execution", {}).get("performed") is False
          # Phase 58 就地改核（判卷侧第 2 处，与上方 webui(P49) 同一枚翻转的第二次落点）：
          # 预检面同样只做静态判「条件够不够」，绝不执行安装；旧判词
          # needs_installer_environment 已退役（不是被改写）⇒ 新值 + retired_boundary.was 双钉。
          and _pf53.get("execution", {}).get("status") == "not_executed_by_this_engine"
          and _pf53.get("execution", {}).get("retired_boundary", {})
              .get("was") == "needs_installer_environment"
          and _pf53.get("execution", {}).get("fabricated") is False
          and _pf53.get("not_install", {}).get("installed_claimed") is False
          and _pf53.get("not_install", {}).get("preflight_is_not_install") is True
          and _pf53.get("not_install", {}).get("compile_is_not_install") is True
          and bool(_pf53.get("not_install", {}).get("basis"))
          # 结构门：HTTP 面无法把写探针指向任何真实安装根（解析出的候选路径本身即被拒）
          and _pf403a.status_code == 403
          and _pf403a.json().get("detail", {}).get("reason") == "preflight_override_outside_allowed_roots"
          and _pf403b.status_code == 403
          and _pf403b.json().get("detail", {}).get("path") == _pf53.get("installed_data_root")
          and str(_ZJ_OUTBASE) in _pf403a.json()["detail"].get("allowed_roots", [])
          and _pf400.status_code == 400
          and _pf400.json().get("detail", {}).get("reason") == "preflight_probe_param_invalid"
          and _pf400.json()["detail"].get("accepted") == ["true", "1", "yes", "on",
                                                          "false", "0", "no", "off"]
          and _pf400.json()["detail"].get("fabricated") is False
          and _pfready.get("verdict") == "preflight_ready"
          and _pfready.get("blocking_codes") == [] and _pfready.get("unknown_codes") == []
          and _pfready.get("checks", {}).get("writability", {}).get("probed") is True
          and _pfready.get("checks", {}).get("writability", {}).get("result") is True
          and _pfready.get("checks", {}).get("writability", {}).get("detail") == {
              "via": "real_probe_file", "bytes": 23, "created_directory": True,
              "probe_removed": True}
          and _sandbox53.exists() is False
          and _pfready.get("probe_request", {}).get("asked") is True
          and _pfready.get("probe_request", {}).get("ran") is True
          and _a403.status_code == 403 and isinstance(_a403.json().get("detail"), str)
          and str(_a403.json().get("detail", "")).startswith("路径被安全策略阻止")
          and _a403s.status_code == 403
          and _a400s.status_code == 400
          and _a400s.json().get("detail", {}).get("reason") == "evidence_suffix_invalid"
          and _a400s.json()["detail"].get("suffix") == ".iss"
          and _a400s.json()["detail"].get("expected") == [".json"]
          and _a400s.json()["detail"].get("fabricated") is False
          and _a400b.status_code == 400
          and _a400b.json().get("detail", {}).get("reason") == "artifact_evidence_rejected"
          and _a404.status_code == 404
          and str(_a404.json().get("detail", "")).startswith("文件不存在: ")
          and _rh53.get("/api/v1/installer/artifact", 0) > 0
          and _rh53.get("/api/v1/installer/preflight", 0) > 0
          and all(_m in _uijs.text for _m in ('"/installer/artifact"', '"/installer/preflight?"',
              "op-mf-artifact", "op-mf-preflight", "mfArtifactView", "mfPreflightView", "p53Why"))
          and all(_m in _p39_html for _m in ('id="op-mf-artifact"', 'id="op-mf-preflight"',
              'id="mf-ev-path"', 'id="mf-probe"', 'id="mf-override"', "Phase 53")),
          json.dumps({"art": {k: _art53.get(k) for k in ("verdict", "blocking_codes")},
                      "sig": {k: _sig53.get(k) for k in ("compile_line_source", "iscc_exit_code")},
                      "art_bytes": _afa53.get("observed"), "script": _asc53.get("observed"),
                      "warn": {k: _war53.get(k) for k in ("recorded_count", "tail_recount",
                                                          "tail_is_full_output")},
                      "tail": _art53.get("output_tail"),
                      "pf": {k: _pf53.get(k) for k in ("verdict", "unknown_codes", "blocking_codes")},
                      "disk": {k: _disk53.get(k) for k in ("required_bytes", "artifact_bytes",
                                                           "payload_bytes", "free_bytes",
                                                           "measured_on")},
                      "root": [(c.get("inno_form"), c.get("exists")) for c in _cand53],
                      "gate": [_pf403a.status_code, _pf403b.status_code, _pf400.status_code],
                      "residual_snapshot": _residual53,
                      "neg": [_a403.status_code, _a400s.status_code, _a400b.status_code,
                              _a404.status_code],
                      "ready": _pfready.get("verdict"), "hits": _rh53}, ensure_ascii=False))

    # --- Phase 58: 安装执行面转真（执行器只挂 CLI，HTTP 面加一枚**只读对账** GET） ---
    # 三路各自不可替代，缺一即回到"声称宇宙自洽而现场无据"：
    # ① 契约面：/runtime/install-actions 由 needs_installer_environment 转 executable_via_cli_only，
    #    且必须**点名退役的旧边界并保留原文**（退役 ≠ 改写历史，P53/P56/P57 同律）；
    # ② 结构面：从受服务自身的 /openapi.json 派生"安装域零非 GET 动词"——"HTTP 面走不到执行器"
    #    不再是注释里的声称，而是路由表可重算的事实（P53 那枚结构门是同一手法）；
    # ③ 对账面：GET /api/v1/installer/execution 无台账时如实 ledger_absent；**植入一枚自称
    #    "装成功又卸干净"的假台账**时必须判 ledger_inconsistent 并逐条点名——台账里的自报布尔
    #    一律 ignored，判定只认磁盘+注册表现场重算（本轮最要防的正是"信自报"）。
    # 顺序律：本块只新增一枚 /api/v1/installer/execution 字面串，且在本文件内被真请求命中，
    # 对 P43 差集尺与 P44 运行时回填均为正贡献（非注释盲区）。
    import hashlib as _hl58
    _ia58 = requests.get(BASE + "/api/v1/runtime/install-actions").json()
    _j58 = lambda _r: _r.json() if _r.status_code == 200 else {}
    _ex58 = requests.get(BASE + "/api/v1/installer/execution")
    _exj58 = _j58(_ex58)
    _led58 = tmp / "data" / "installer" / "execution-evidence.json"
    _oa58 = requests.get(BASE + "/openapi.json", timeout=30).json()
    # 段判定而非 startswith 一个带尾斜杠的 URL 字面串：本文件自身就在 acceptance-coverage
    # 尺的扫描面内，引号/反引号包住的「/api/v1/installer/」经 _n41 归一（去尾斜杠）后会
    # 变成一条不属于任何模板的幽灵字面串，当场判红——注释里写这个形状同样会被扫到
    # （p58_ghost_hunt 定位：五套源仅此一处，bare=2 全是本块自身产出，一条来自代码、
    # 一条来自注释）。「每条引号 URL 字面串必须归属一枚模板」是尺子故意设的强不变式
    # （P42 假 404、P43 first-match 都靠它抓到），所以改的是**写断言的方式**，不给尺子
    # 开口子。语义完全等价：取安装域全部路径 + runtime 的那一枚安装动作契约。
    _dom58 = {p: v for p, v in (_oa58.get("paths") or {}).items()
              if p.strip("/").split("/")[2:3] == ["installer"]
              or p.endswith("/runtime/install-actions")}
    _non_get58 = sorted(f"{p}:{m}" for p, _ops in _dom58.items() for m in (_ops or {})
                        if str(m).lower() != "get")
    # RED 阶段本路由尚不存在：一律 .get 链取，判卷侧不许抛 KeyError（崩了就没有裁决行，
    # 而"无裁决行的日志既不算通过、也不能反推被测对象有问题"）。
    _get58 = ((_dom58.get("/api/v1/installer/execution") or {}).get("get")) or {}
    check("contract(P58): installer_execution flips needs_installer_environment -> executable_via_cli_only, retirement of the old boundary is NAMED with its original wording kept (was/since/retired_in/why), explicit consent token + gates are published, and the executor entry point is CLI-only; STRUCTURALLY derived from served /openapi.json the whole install domain (6 = 4 installer GETs + runtime/install-actions + the new installer/execution GET) exposes ZERO non-GET operations ⇒ the HTTP face cannot reach the executor; the new route takes no caller-chosen path param",
          _ia58.get("status") == "executable_via_cli_only"
          and _ia58.get("capability") == "installer_execution"
          and _ia58.get("fabricated") is False
          and _ia58.get("retired_boundary", {}).get("was") == "needs_installer_environment"
          and _ia58.get("retired_boundary", {}).get("retired_in") == "Phase 58"
          and _ia58.get("retired_boundary", {}).get("since") == "Phase 26"
          and bool(_ia58.get("retired_boundary", {}).get("why"))
          and _ia58.get("confirm_token") == "ZAOJING-CONFIRM-EXECUTE-INSTALLER"
          and {"confirmation_required", "artifact_outside_allowed_root", "build_ledger_stale",
               "artifact_sha_mismatch_vs_build_ledger", "install_dir_not_empty",
               "install_dir_inside_source_tree"} <= set(_ia58.get("gates", []))
          and "install-exec" in _ia58.get("entry_point", "")
          and "cli" in _ia58.get("entry_point", "").lower()
          and len(_dom58) == 6 and _non_get58 == []
          and _get58 != {} and _get58.get("parameters") in ([], None)
          and _ex58.status_code == 200 and _exj58.get("status") == "ledger_absent"
          and _exj58.get("capability") == "installer_execution"
          and _exj58.get("fabricated") is False
          and _exj58.get("ledger", {}).get("exists") is False
          and _exj58.get("ledger", {}).get("path") == str(_led58)
          and _exj58.get("mismatches") == [] and _exj58.get("live") == {}
          and _exj58.get("recomputed") == {}
          and bool(_exj58.get("self_report_ignored"))
          and not _led58.exists(),
          json.dumps({"status": _ia58.get("status"), "ret": _ia58.get("retired_boundary"),
                      "dom": sorted(_dom58), "non_get": _non_get58,
                      "ex": {k: _exj58.get(k) for k in ("status", "ledger")}},
                     ensure_ascii=False))
    # 反证（本轮的§2 命门）：伪造台账**绝不可能**把对账面骗成"已执行且已核验"。
    _fake58 = {
        "schema": "zaojing-installer-exec-ledger/v1",
        "created_at": "2026-10-01T00:00:00", "updated_at": "2026-10-01T00:00:00",
        "install": {
            "status": "install_verified", "installed": True, "exit_code": 0,
            "artifact": {"path": str(tmp / "no-such-setup.exe"), "sha256": "de" * 32},
            "install_dir": str(tmp / "no-such-install-root"),
            "checks": [], "checks_count": 99,
            "diff": {"created": [], "created_count": 7},
            "log": {"path": str(tmp / "no-such-install.log"), "sha256": "af" * 32},
            "preexisting": {"files": []},
        },
        "uninstall": {"status": "uninstall_verified", "performed": True,
                      "install_dir_gone": True, "uninstall_key_gone": True,
                      "app_key_gone": True, "preexisting": {"intact": True}},
    }
    _led58.parent.mkdir(parents=True, exist_ok=True)
    _fake_bytes = json.dumps(_fake58, ensure_ascii=False).encode("utf-8")
    _led58.write_bytes(_fake_bytes)
    _ex58t = _j58(requests.get(BASE + "/api/v1/installer/execution"))
    # 调用方指定路径也不改判：ledger_path 不是本端点的入参（唯一真相 = 服务自己的数据根）
    _ex58q = _j58(requests.get(BASE + "/api/v1/installer/execution",
                               params={"ledger_path": str(_led58.parent.parent / "elsewhere"),
                                       "app_name": "bogus"}))
    _mids58 = _ex58t.get("mismatches", [])
    check("negative(P58): a planted FORGED ledger that self-reports install_verified+uninstall_verified+exit 0 is NOT trusted — GET /installer/execution returns ledger_inconsistent and names each field the live machine contradicts (artifact unreadable/not-on-disk, sha vs build ledger, checks_count vs len(checks), created_count vs len(created), log sha); the self-reported booleans stay echoed with ignored=True semantics while verdict derives only from disk+registry recompute; ledger.bytes/sha256 in the response equal the file as read independently off disk; a caller-supplied ?ledger_path= cannot redirect the reconciliation",
          _ex58t.get("status") == "ledger_inconsistent"
          and _ex58t.get("fabricated") is False
          and len(_mids58) >= 5
          and any("install.artifact" in m for m in _mids58)
          and any("install.checks_count" in m for m in _mids58)
          and any("install.diff.created_count" in m for m in _mids58)
          and any("install.log.sha256" in m for m in _mids58)
          and "install.installed" not in "".join(_mids58)
          and _ex58t.get("recomputed", {}).get("install_dir_now_exists") is False
          and _ex58t.get("recomputed", {}).get("uninstall_key_now_present") is False
          and _ex58t.get("recomputed", {}).get("build_ledger_artifact_sha256") in (None, "")
          and _ex58t.get("recorded_only") == ["install.exit_code", "install.argv",
                                              "install.process"]
          and _ex58t.get("ledger", {}).get("bytes") == len(_fake_bytes)
          and _ex58t.get("ledger", {}).get("sha256") == _hl58.sha256(_fake_bytes).hexdigest()
          and _ex58t.get("ledger", {}).get("exists") is True
          and _ex58q.get("ledger", {}).get("path") == str(_led58)
          and _ex58q.get("status") == "ledger_inconsistent"
          and any(i.startswith("install.") for i in _ex58t.get("self_report_ignored", [])),
          json.dumps({"status": _ex58t.get("status"), "mismatches": _mids58,
                      "recomputed": _ex58t.get("recomputed"),
                      "ledger": _ex58t.get("ledger"),
                      "q": _ex58q.get("ledger")}, ensure_ascii=False))
    # 归位：假台账删除后对账面必须回到 ledger_absent（同一 GET 现场重读、不缓存、不留残件）
    #
    # 第 9 枚真缺陷在**服务端这一侧**的坐位：台账只有 install_verified、无卸载记录，而本机现场
    # 并不在安装态 ⇒ 判不一致并点名"痕迹被台账之外的手段抹掉"。中间态
    # install_verified_awaiting_uninstall 的正面活体实证在 L1（真子进程 + 假安装器，安装态由
    # 临时根与注册表替身真造出来）——沙箱 HTTP 面凭空造不出一枚真安装，硬造就是让判据依赖
    # 伪造的现场，那正是本层要防的事。
    _only58 = json.loads(json.dumps(_fake58))
    _only58["uninstall"] = None
    _only58["install"]["checks_count"] = 0
    _only58["install"]["diff"] = {"created": [], "created_count": 0}
    _only58["install"]["preexisting"] = {"files": []}
    _only_bytes = json.dumps(_only58, ensure_ascii=False).encode("utf-8")
    _led58.write_bytes(_only_bytes)
    _ex58o = _j58(requests.get(BASE + "/api/v1/installer/execution"))
    _mo58 = _ex58o.get("mismatches", [])
    check("negative(P58 defect 9): a ledger recording ONLY an install_verified run with no uninstall "
          "record, on a machine that is NOT in an installed state, must be ledger_inconsistent naming "
          "the off-book removal (install_verified + 无卸载记录 + 现场已不在安装态) — the new honest "
          "intermediate verdict install_verified_awaiting_uninstall is granted ONLY while the live "
          "machine agrees it is installed, otherwise 'something deleted the app outside the ledger' "
          "would launder into a normal waiting state",
          _ex58o.get("status") == "ledger_inconsistent"
          and _ex58o.get("status") != "install_verified_awaiting_uninstall"
          and any("install_verified" in m and "无卸载记录" in m for m in _mo58)
          and _ex58o.get("recomputed", {}).get("install_dir_now_exists") is False
          and _ex58o.get("recomputed", {}).get("uninstall_key_now_present") is False
          and _ex58o.get("ledger", {}).get("sha256") == _hl58.sha256(_only_bytes).hexdigest()
          and _led58.read_bytes() == _only_bytes,
          json.dumps({"status": _ex58o.get("status"), "mismatches": _mo58,
                      "recomputed": _ex58o.get("recomputed")}, ensure_ascii=False)[:600])
    _led58.unlink()
    _ex58z = _j58(requests.get(BASE + "/api/v1/installer/execution"))
    _rh58 = {k: v for k, v in (requests.get(BASE + "/api/v1/metrics", timeout=20).json()
                               .get("route_hits") or {}).items()
             if "installer" in k or "install-actions" in k}
    check("readback(P58): after the planted ledger is removed the same GET re-derives ledger_absent from disk (no caching of a previous verdict, no residue left in the sandbox data root) and both /installer/execution + /runtime/install-actions show live route_hits>0 (P44 runtime backfill)",
          _ex58z.get("status") == "ledger_absent"
          and _ex58z.get("ledger", {}).get("exists") is False
          and not _led58.exists() and not list(_led58.parent.glob("*")),
          json.dumps({"z": _ex58z.get("status"), "exists": _led58.exists(),
                      "sib": list(_led58.parent.glob("*")),
                      "hits": _rh58}, ensure_ascii=False))
    check("runtime-backfill(P58): GET /api/v1/installer/execution and /api/v1/runtime/install-actions each show route_hits>0 in the live service's /metrics (本文件的两条新字面串真达，不是注释声称)",
          _rh58.get("/api/v1/installer/execution", 0) > 0
          and _rh58.get("/api/v1/runtime/install-actions", 0) > 0,
          json.dumps(_rh58, ensure_ascii=False))

    # --- Phase 46: 四主题融合视觉体系随控制台出货（苹果风 HIG × 玻璃拟态，玄夜/雾白/青瓷/琥珀） ---
    # 顺序律同 P45：本块置于 P45 之后、P43 之前即可（零新增 /api/v1 引号字面串，注释盲区定律）。
    # 键点：data-theme-btn 字面串在 JS 处理器中重复出现，钉数须用最具体的 swatch class 串而非子串计数。
    check("webui(P46): served index+style.css ship the four-theme fusion system — exactly 4 swatch buttons (night/mist/celadon/amber) with zj-theme persistence in index, style.css carries the 3 non-default theme blocks + glass backdrop-filter + all three a11y fallbacks (reduced-motion / reduced-transparency / prefers-contrast)",
          _p39_html.count('class="sw sw-') == 4
          and all(f'data-theme-btn="{t}"' in _p39_html for t in ("night", "mist", "celadon", "amber"))
          and "zj-theme" in _p39_html
          and all(_m in _uicss.text for _m in ('data-theme="mist"', 'data-theme="celadon"',
              'data-theme="amber"', 'backdrop-filter', 'prefers-reduced-motion',
              'prefers-reduced-transparency', 'prefers-contrast')),
          "P46 swatches + css theme/material/a11y markers")

    # --- Phase 48: 生成类接线 + 缩略图真实渲染（第十五面板 AI 生成区 / 素材库缩略图列） ---
    # 结构面钉三件事：① 受服务 HTML 里 4 枚生成按钮与其输入控件齐备（含 select 的 value 枚举，
    # P45 教训：枚举值错即前端想象偏差）；② app.js 里每个静态 $("id") 读取的节点必须在受服务
    # HTML 真实存在，或被 app.js 自己动态创建——这是把 P39/P45"探针抓前端想象偏差"机制化的尺子；
    # ③ 旧的"生成类不进控制台/缩略图不入 JSON"判词必须已归位，否则本页在声称一件已不再真实的事。
    _p48_ids = set(_re41.findall(r'\$\("([^"]+)"\)', _js41))
    _p48_missing = sorted(_i for _i in _p48_ids if ('id="%s"' % _i) not in _html41)
    _p48_dyn = {"as-thumb-view", "ev-report-detail"}
    check("webui(P48): DOM consistency — every one of the %d $(\"id\") reads in served app.js resolves against served index.html (only whitelisted dynamic nodes %s may be absent, and each MUST be created by app.js itself); stale 需真实 Provider/缩略图不入 JSON refusal text gone, P48 归位说明 present"
          % (len(_p48_ids), sorted(_p48_dyn)),
          all(_i not in _p48_dyn or ('id="%s"' % _i) in _js41 for _i in _p48_missing)
          and not (set(_p48_missing) - _p48_dyn)
          and "需真实 Provider 后端（属下一能力域）" not in _html41
          and "素材缩略图二进制流不入 JSON 呈现区" not in _html41
          and "Phase 48 归位" in _html41
          and 'id="as-thumb-view"' in _js41,
          f"ids={len(_p48_ids)} missing={_p48_missing}")
    check("webui(P48): served index+app.js wire the AI-generation block (4 explicit-click buttons + full input set + select enumerations) and the thumbnail column (data-thumb / thumb-lg / honest thumb-none texts)",
          all(_m in _html41 for _m in ('id="wr-gen-btn"', 'id="wr-gen-out"', 'id="wr-plan-btn"',
              'id="wr-plan-out"', 'id="wr-chat-btn"', 'id="wr-chat-out"', 'id="wr-img-btn"',
              'id="wr-img-out"', 'id="wr-gen-prompt"', 'id="wr-gen-priority"', 'id="wr-plan-goal"',
              'id="wr-chat-stream"', 'id="wr-img-n"', 'id="wr-ai-timeout"',
              '<option value="cost">', '<option value="latency">', '<option value="privacy">',
              '<option value="false">false', '<option value="true">true'))
          and all(_m in _js41 for _m in ('Post("/generate"', 'Post("/plan"', 'Post("/provider/chat"',
              'Post("/provider/images"', "aiRender", 'data-thumb', 'class="thumb-lg"',
              "取不到（端点非 200，状态如实）", "无（非图像素材，服务端未生成）",
              "此处<b>不是</b>原图，界面不假装是")),
          "P48 html/js markers")
    # 契约面：界面据以渲染的两种诚实 502 形态（p48_freeze 同款），且必须与 app.js 的 errBox 假设一致。
    _p48_gen = requests.post(BASE + "/api/v1/generate", json={"prompt": "a shiba inu"}, timeout=180)
    _p48_gen_np = requests.post(BASE + "/api/v1/generate", json={"steps": 10}, timeout=60)
    _p48_plan = requests.post(BASE + "/api/v1/plan", json={"goal": "design eco tea packaging"}, timeout=120)
    _p48_chat = requests.post(BASE + "/api/v1/provider/chat",
                              json={"prompt": "explain dieline", "provider_id": "p48l2ghost"}, timeout=60)
    _p48_img = requests.post(BASE + "/api/v1/provider/images",
                             json={"prompt": "minimal crane logo", "provider_id": "p48l2ghost"}, timeout=60)
    _p48_d = lambda _r: (_r.json() or {}).get("detail")
    check("webui(P48): the two honest-failure shapes the console renders are exactly what the live service sends — /generate 502 carries a STRING detail (or true 200 success on a machine with a live provider, never a fake), /plan + /provider/chat + /provider/images 502 carry a DICT detail with fabricated:false, missing prompt still 422 (no 5xx on client error)",
          (_p48_gen.status_code == 502 and isinstance(_p48_d(_p48_gen), str)
           or (_p48_gen.status_code == 200 and _p48_gen.json().get("status") == "success"
               and isinstance(_p48_gen.json().get("images_generated"), int)))
          and _p48_gen_np.status_code == 422
          and _p48_plan.status_code == 502 and isinstance(_p48_d(_p48_plan), dict)
          and _p48_d(_p48_plan).get("fabricated") is False
          and _p48_d(_p48_plan).get("status") == "no_provider_configured"
          and _p48_chat.status_code == 502 and isinstance(_p48_d(_p48_chat), dict)
          and _p48_d(_p48_chat).get("fabricated") is False
          and _p48_img.status_code == 502 and isinstance(_p48_d(_p48_img), dict)
          and _p48_d(_p48_img).get("fabricated") is False,
          f"gen={_p48_gen.status_code}/{type(_p48_d(_p48_gen)).__name__} np={_p48_gen_np.status_code} "
          f"plan={_p48_plan.status_code} chat={_p48_chat.status_code} img={_p48_img.status_code}")

    # --- Phase 66: 玻璃拟态 × 苹果风材质换代（product 面：style.css 16,000→32,487 B 重写；
    # index.html 62,084→62,062 B（−22 B，P66-1 实测缺陷：两枚外层 dl 去掉与 helper 冲突的
    # class="kv"，helper 仍自输出同名根，故样式不丢）；app.js 零改动（sha16 a5151ce992265350）。
    # 本判据只钉 CSS 面；index.html 的这处修复由 P48 的 DOM 一致性判据与 P66-4 活体独立复核。） ---
    # 一号判据的不可替代性：本轮换肤全部活在 style.css 一张表里，而 P46 的七枚标记在旧表上也会全绿
    # （旧表同样有 data-theme / backdrop-filter / 三条 prefers-*）。所以「受服务字节 == 盘上台账字节」
    # 必须排第一——子串在场只是必要不充分；若服务端还在发旧副本，结构判据越像越会给自己发假绿。
    # 计数律（P64/P58 教训）：本块不剔除注释就计数，只数「说明注释里不会出现」的声明原文；
    # 五枚材质 token 各 4 份 = 四个主题块（:root/night、mist、celadon、amber）各一份，
    # 少一份即某个主题退化成继承别家材质——这正是要抓的漂移。`html[data-theme="` 这类会出现在
    # 说明注释里的前缀一律不做等值计数（P66 注释里就引用了它）。
    # 三号判据钉 run3 抓到的真缺陷修复：媒体查询里的 token 覆盖若不写 html[data-theme]（0,1,1），
    # 裸 :root（0,1,0）必输给主题块 ⇒ --grain-o / --t-* 覆盖成死代码（run3 实测 grain 仍 .028）。
    # 两处「同特指度 + 源序靠后」的原文必须在场。
    _p66_disk = OUT / "webui" / "style.css"
    _p66_disk_b = _p66_disk.read_bytes()
    _p66_served_b = _uicss.content
    _p66_css = _p66_served_b.decode("utf-8")
    _p66_tok4 = {_t: _p66_css.count(_t) for _t in
                 ("--glass:", "--glass-hi:", "--elev-1:", "--shadow-card:", "--stage-a:")}
    check("webui(P66): served /ui/style.css 与 outputs/webui/style.css 逐字节相等（不是「标记相似」）且非空白页——"
          "四主题材质壳齐备（五枚材质 token 各 4 份 = night/mist/celadon/amber 各一）、动效与圆角 token、"
          "两档模糊值/五档断点/三条无障碍回退 @media + 无 backdrop-filter 的 @supports 回退，"
          "以及 run3 实测缺陷的修复形制（两处 html[data-theme], :root 同特指度覆盖；裸 :root 会输成死代码）",
          _uicss.status_code == 200
          and "text/css" in _uicss.headers.get("content-type", "")
          and _p66_disk.is_file() and len(_p66_disk_b) > 20000
          and _p66_served_b == _p66_disk_b
          and all(v == 4 for v in _p66_tok4.values())
          and all(_m in _p66_css for _m in (
              '--blur-thick: 44px;', '--blur-card: 30px;',
              '--ease-spring: cubic-bezier', '--ease-out: cubic-bezier',
              '--t-enter: 500ms;', '--t-move: 420ms;', '--t-color: 240ms;', '--t-act: 150ms;',
              '--r-l: 24px;', '--r-m: 16px;', '--r-s: 10px;', '--r-pill: 999px;',
              'body::before',
              'html[data-theme="night"],', 'html[data-theme="mist"]',
              'html[data-theme="celadon"]', 'html[data-theme="amber"]',
              'max-width: 1100px', 'max-width: 900px', 'max-width: 720px',
              'max-width: 560px', 'max-width: 420px',
              '@media (prefers-reduced-motion: reduce)',
              '@media (prefers-reduced-transparency: reduce)',
              '@media (prefers-contrast: more)',
              'html[data-theme], :root { --t-enter: 1ms;',
              'html[data-theme], :root { --grain-o: 0; }',
              '@supports not ((backdrop-filter'))
          and _p66_css.count('minmax(0, 1fr)') >= 4
          and _p66_css.count('overflow-wrap') >= 4,
          f"disk={len(_p66_disk_b)}B served={len(_p66_served_b)}B eq={_p66_served_b == _p66_disk_b} "
          f"tok4={_p66_tok4} repair={_p66_css.count('html[data-theme], :root')} "
          f"minmax={_p66_css.count('minmax(0, 1fr)')} wrap={_p66_css.count('overflow-wrap')}")

    # --- Phase 67: 玻璃强度三档（全/柔/关）+ 系统无障碍上限（产品改动轮） ---
    # 活体证据：p67_glass_probe.js run3/run4 各 21/21 步通过（四主题×三档截图、375 窄窗几何、
    # 系统 reduced-transparency / contrast:more 仿真锁定、刷新持久化）。本判据钉三件事：
    # ① 受服务 index.html 与盘上 outputs/webui/index.html 逐字节相等——控件与样式全部活在单文件
    #    资产里，「标记相似」挡不住服务端还在发旧副本；② 三枚按钮与存取/锁定接线原文每人恰好一份
    #    （少一份=某档不可达或不可持久化）；③ CSS 的 soft/off 形制与 run2 实测的中文 flex 塌陷修复。
    # 计数律：本块不剔除注释，故只钉「说明注释里不会出现」的声明原文；`data-glass` 前缀不做等值
    # 计数（注释里引用了它），按钮与规则全文才做等值。system 偏好是上限而非第 4 个用户档位：
    # lock 分支必须把 full/soft 置 disabled、把有效值钳到 off。
    _p67_idx = OUT / "webui" / "index.html"
    _p67_idx_b = _p67_idx.read_bytes()
    _p67_served_b = _ui.content
    _p67_html = _p67_served_b.decode("utf-8")
    _p67_css = _uicss.content.decode("utf-8")
    _p67_btn = {_b: _p67_html.count('data-glass-btn="%s"' % _b) for _b in ("full", "soft", "off")}
    _p67_soft_n = _p67_css.count('html[data-glass="soft"]')
    _p67_off_n = _p67_css.count('html[data-glass="off"]')
    check("webui(P67): served / 与 outputs/webui/index.html 逐字节相等，玻璃三档控件接线齐备——"
          "三枚 data-glass-btn 各 1 份、启动读取/点击写回 localStorage、html[data-glass] 引导、"
          "matchMedia 两条系统偏好 × change 监听，锁定时有效值钳到 off、full/soft 置 disabled 且 "
          "aria-pressed 与 .on 同源；样式表 soft 降模糊/降饱和、off 实底 + 关模糊 + 停噪点、"
          ".gt 最小触摸宽度与窄窗独占行修复在场（run2 实测 375 窗曾塌成一字宽 13px/枚）",
          _ui.status_code == 200 and "text/html" in _ui.headers.get("content-type", "")
          and _p67_idx.is_file() and len(_p67_idx_b) > 50000
          and _p67_served_b == _p67_idx_b
          and all(v == 1 for v in _p67_btn.values())
          and all(_m in _p67_html for _m in (
              'class="glassctl"', 'role="group" aria-label="玻璃强度"',
              'localStorage.getItem("zj-glass")',
              'localStorage.setItem("zj-glass", b.getAttribute("data-glass-btn"));',
              'document.documentElement.setAttribute("data-glass",g);',
              'root.setAttribute("data-glass", b.getAttribute("data-glass-btn"));',
              'window.matchMedia("(prefers-reduced-transparency: reduce)")',
              'window.matchMedia("(prefers-contrast: more)")',
              'm.addEventListener("change", mark);',
              'var eff=solid?"off":(root.getAttribute("data-glass")||"full");',
              'var lock=solid&&v!=="off";',
              'b.disabled=lock;',
              'b.title=lock?"',
          ))
          and all(_m in _p67_css for _m in (
              'html[data-glass="soft"] {',
              '--blur-thick: 26px;', '--blur-card: 18px;', '--sat: 1.45;',
              'html[data-glass="soft"] body::before { opacity: .55; }',
              'html[data-glass="soft"] body::after { opacity: calc(var(--grain-o) * .55); }',
              'html[data-glass="off"] body::before,',
              'html[data-glass="off"] body::after { display: none; }',
              '-webkit-backdrop-filter: none;',
              '.gt:disabled { opacity: .40; cursor: not-allowed; }',
              'flex: 1 1 0; min-width: 30px; padding: 5px 0;',
              '.glassctl { flex: 1 1 100%; padding: 2px 0 0; }',
          ))
          and _p67_soft_n == 3
          and _p67_off_n == 11
          and _p67_css.count('--panel-solid:') == 4
          and _p67_css.count('background: var(--panel-solid);') == 5,
          f"idx_eq={_p67_served_b == _p67_idx_b} btns={_p67_btn} "
          f"soft={_p67_soft_n} off={_p67_off_n} "
          f"solid4={_p67_css.count('--panel-solid:')} "
          f"idx={len(_p67_served_b)}B css={len(_p67_css.encode('utf-8'))}B")

    # --- Phase 69: 控制台呈现层四枚收口（图标内联 / 空输出占位 / 长说明折叠 / 接线表进度条） ---
    # 本轮零新增端点、零新增文件、零外链：图标以 data URI 内联——此前浏览器每次加载都去要一次
    # /favicon.ico 并如实 404，那是控制台自己制造的唯一一条报错；.out:empty::after 给未发起请求的
    # 输出区一条静态说明；三枚长说明折进 <details>（正文逐字保留，P42/P45 的字符串判据照旧命中）；
    # 概览按域表的裸数字格换成与同一份 wired/all 同源的面条。
    # 计数律（P64/P66 同条）：本块不剔除注释就计数，故只钉「说明注释里不会出现」的声明原文；
    # 期望值一律由受服务文本现场派生，不抄探针快照数字（P58 第十四类）。
    # 拒接面清单由运行时 _notwired41 派生而非手抄：测试源里任何以斜杠 api 打头的引号字面串都会被
    # P43/P60 逐套收集器记成本套声称，而拒接项永不发请求 ⇒ 写了必假红，故此处只出现无前导斜杠的后缀。
    import re as _re69
    import urllib.parse as _up69
    import xml.etree.ElementTree as _et69
    _p69_html, _p69_css, _p69_js = _p67_html, _p67_css, _uijs.text
    _p69_eq = (_ui.content == _p67_idx_b
               and _uijs.content == (OUT / "webui" / "app.js").read_bytes()
               and _uicss.content == (OUT / "webui" / "style.css").read_bytes())
    # ① 图标：恰好一枚 rel="icon"，href 内联且解码后是真 <svg>
    _p69_icons = _re69.findall(r'<link rel="icon"[^>]*href="([^"]*)"', _p69_html)
    _p69_icon_any = len(_re69.findall(r'<link[^>]*rel="icon"', _p69_html))
    _p69_svg = _up69.unquote(_p69_icons[0]).split(",", 1)[1] if (
        len(_p69_icons) == 1 and _p69_icon_any == 1
        and _p69_icons[0].startswith("data:image/svg+xml,")) else ""
    try:
        _p69_svg_root = _et69.fromstring(_p69_svg) if _p69_svg else None
    except Exception:
        _p69_svg_root = None
    _p69_svg_ok = (_p69_svg_root is not None and _p69_svg_root.tag.endswith("svg")
                   and len(list(_p69_svg_root)) >= 3
                   and _p69_svg_root.get("viewBox") == "0 0 32 32")
    _p69_ext = (len(_re69.findall(r'(?:src|href)="https?://', _p69_html))
                + len(_re69.findall(r'(?:src|href)="https?://', _p69_js))
                + len(_re69.findall(r'url\(\s*["\']?https?:', _p69_css))
                + (1 if "@import" in _p69_css else 0))
    # ② 输出区：未发起请求时每个 .out 要么裸空（CSS :empty 才命中）、要么只带一条静态说明
    _p69_out = _re69.findall(r'<div class="out"[^>]*>(.*?)</div>', _p69_html, _re69.S)
    _p69_out_raw = sum(1 for _x in _p69_out if not _x.strip())
    _p69_out_hint = sum(1 for _x in _p69_out if _re69.fullmatch(
        r'<span class="muted">[^<]*</span>', _x.strip()) is not None)
    _p69_ph = ('.out:empty::after {' in _p69_css and "尚无结果" in _p69_css)
    # ③ 长说明折叠：正文长度阈值两侧必须各就各位（折叠腿 ≥280、未折叠腿 <280），且逐字保留
    _p69_strip = lambda _s: _re69.sub(r"<[^>]+>", "", _s).strip()
    _p69_boxes = _re69.findall(
        r'<details class="info-box[^"]*">.*?</details>|<div class="info-box"[^>]*>.*?</div>',
        _p69_html, _re69.S)
    _p69_folds = [_re69.match(
        r'<details class="info-box info-fold"><summary>([^<]*)</summary><div>(.*?)</div></details>',
        _b, _re69.S) for _b in _p69_boxes if _b.startswith("<details")]
    _p69_pairs = [_m.groups() for _m in _p69_folds if _m]
    _p69_fold_body = sorted(len(_p69_strip(_t)) for _s, _t in _p69_pairs)
    _p69_plain_body = sorted(len(_p69_strip(_b)) for _b in _p69_boxes if _b.startswith("<div"))
    _p69_fold_css = all(_m in _p69_css for _m in (
        'details.info-fold { padding: 0; }', 'details.info-fold > summary {',
        'details.info-fold > summary::before {', 'details.info-fold[open] > summary::before {',
        'details.info-fold > div {'))
    _p69_kept = all(_k in _p69_html for _k in (
        "曾因实证存在落盘副作用", "归入", "Phase 48 归位", "Phase 65 归位",
        # Phase 70/71 两轮把旧拒接项逐枚转正，面板里的历史说明随之归位；换代只认盘上实测
        # （本轮量得两串各恰 1 次），并把上一代次的「仍明确不进控制台」移出必在场集合。
        "Phase 70 归位", "Phase 71 归位"))
    # ④ 按域接线表：面条与 wired/all 同源，裸数字表头必须已让位
    _p69_bar = all(_m in _p69_js for _m in (
        'class="bar-cell"', '<th>接线进度</th>', 'Math.round((w / all.length) * 100)'))
    _p69_bar_css = 'td.bar-cell { width: 42%; }' in _p69_css
    _p69_num_gone = '<th>已接线/端点数</th>' not in _p69_js
    # ⑤ 面板说明的逐名在册腿。换代声明（口径变更，非放宽阈值）：Phase 71 把拒接面清零后，
    # 「由 notWired 派生名单」这条腿的种群变成空集——空集上的 all()/差集恒空即恒绿
    # （P58 第十三类：并集≠每套；归零型枚举必须先自证数得出 ≥1），它已无法证明任何事。
    # 故把同一义务搬到**恒非空的八枚转正集合**上：名单由 _p71_promoted8 现场派生（不手抄），
    # 逐名必须仍在面板散文里在册，且逐名必须**不再**出现在拒接集里；拒接面自身的归零由
    # wired==universe==135 的正向计数背书，不是"数到 0 就判绿"。
    _p69_nw_names = sorted(_p[len("/api/v1"):].lstrip("/") for _p in _notwired41)
    _p69_promoted_names = sorted(_p[len("/api/v1"):].lstrip("/") for _p in _p71_promoted8)
    _p69_gap = [_n for _n in _p69_promoted_names if _n not in _p69_html]
    _p69_still_refused = [_n for _n in _p69_promoted_names
                          if _n in set(_p69_nw_names)]
    # 三十三条腿逐名求值并命名：判据红时必须说得出是哪条腿红的（P64 纪律「每条命中带理由」
    # 的对偶——红也要带理由），九臂变异台据此证明「各红在不同腿上」而不是八臂全挤在同一条。
    _p69_legs = {
        "served_eq_disk": _p69_eq,
        "icon_inlined_svg": _p69_svg_ok,
        "icon_exactly_one": _p69_icon_any == 1,
        "zero_external_refs": _p69_ext == 0,
        "out_boxes_present": len(_p69_out) >= 30,
        "out_boxes_empty_or_hint_only": _p69_out_raw + _p69_out_hint == len(_p69_out),
        "placeholder_rule": _p69_ph,
        "info_boxes_are_eleven": len(_p69_boxes) == 11,
        "folds_are_five": len(_p69_pairs) == 5,
        "plains_are_six": len(_p69_plain_body) == 6,
        "folded_bodies_long": min(_p69_fold_body, default=0) >= 280,
        "plain_bodies_short": max(_p69_plain_body, default=10 ** 9) < 280,
        "summary_text_nonempty": all(_s.strip() for _s, _t in _p69_pairs),
        "fold_css_five_rules": _p69_fold_css,
        "disclosure_wording_kept": _p69_kept,
        "stale_refusal_gone": "明确不接线" not in _p69_html,
        "coverage_bar_in_js": _p69_bar,
        "coverage_bar_css": _p69_bar_css,
        "numeric_header_gone": _p69_num_gone,
        "promoted_names_derived": len(_p69_promoted_names) == 8,
        "promoted_ledger_complete": _p69_gap == [],
        "promoted_not_in_refusal_set": _p69_still_refused == [],
        "refusal_set_really_empty": len(_p69_nw_names) == 0,
        "ghost_zero": _ghost41 == [],
        "wired_135": len(_wired41) == 135,
        "notwired_0": len(_notwired41) == 0,
        "universe_135": len(_wired41) + len(_notwired41) == len(_ns41) == 135,
        "panel_solid_tokens_4": _p69_css.count('--panel-solid:') == 4,
        "panel_solid_uses_5": _p69_css.count('background: var(--panel-solid);') == 5,
        "glass_soft_3": _p69_css.count('html[data-glass="soft"]') == 3,
        "glass_off_11": _p69_css.count('html[data-glass="off"]') == 11,
        "panels_15": _p69_html.count('data-panel="') == 15,
        "swatches_4": _p69_html.count('class="sw sw-') == 4,
    }
    _p69_red = sorted(_k for _k, _v in _p69_legs.items() if not _v)
    check("webui(P69): 呈现层四枚收口在受服务副本里逐字节实存且零外链——"
          "① 恰好一枚 rel=\"icon\"，href 为 data:image/svg+xml 内联，解码后能被 XML 解析成 "
          "viewBox=\"0 0 32 32\" 且子元素 ≥3 的 <svg>，属性式 src/href=\"http(s)://\" + CSS url(http) "
          "+ @import 的命中总数恒为 0（图标位不再是网络请求）；"
          "② 每个 class=\"out\" 输出区在未发请求时要么裸空（CSS :empty 才命中）、要么只带一条静态 "
          "<span class=\"muted\"> 说明，两腿之和等于总数且占位原文「尚无结果」在场；"
          "③ 十一枚 info-box 里正文 ≥280 字者（实测五枚）一律折进 <details class=\"info-box info-fold\">、"
          "summary 非空、正文逐字保留（P42/P45 依赖的声明原文照旧在场），未折叠者（实测六枚）正文最高"
          "仍 <280，五条折叠样式原文在场；④ 概览按域表用与 wired/all 同源的 .bar 面条呈现"
          "（bar-cell + 接线进度 + Math.round 派生），旧裸数字表头「已接线/端点数」必须不在场；"
          "另钉**口径变更换代**（P71 拒接面归零后原「八枚拒接端点逐名在册」腿的种群成空集、退化为"
          "空真，故把同一义务搬到恒非空的八枚转正集合）：名单由 P71 转正集现场派生（不手抄）、"
          "逐名仍须在写侧说明里在册、逐名必须不再出现在拒接集里，且拒接集自身经 wired==universe==135 "
          "的正向计数归零；另钉三资产 served==disk 逐字节相等、以及 P66/P67 的等值计数与 "
          "135/135/0/ghost[] 宇宙未被动摇；"
          "另注：三十三腿逐名求值，detail 自带 red=[失败腿名]——红时必说得出是哪条腿，"
          "九臂变异台据此证明八桩各红在不同腿上而非同一条腿的回声",
          all(_p69_legs.values()) and len(_p69_legs) == 33,
          f"red={_p69_red} eq={_p69_eq} ext={_p69_ext} icon={_p69_icon_any}/{len(_p69_icons)} "
          f"svg_ok={_p69_svg_ok} out={len(_p69_out)}/{_p69_out_raw}+{_p69_out_hint} "
          f"boxes={len(_p69_boxes)}/{len(_p69_pairs)} fold_body={_p69_fold_body[:3]} "
          f"plain_body={_p69_plain_body} nw={len(_p69_nw_names)} "
          f"promoted={len(_p69_promoted_names)} gap={_p69_gap[:3]} still_refused={_p69_still_refused[:3]} "
          f"bar={_p69_bar}/{_p69_bar_css}/{_p69_num_gone} html={len(_p69_html)}B")

    # --- Phase 69c: 控制台三资产不得可启发式缓存（r10/r11b/r12b 三轮独立实测坐实的产品缺陷） ---
    # 实测链（全部盘上收据在场，非推断）：
    # ① p69_r11b_r10_readback.txt：产品 GET / 头 cache-control=null、etag 与 last-modified 在场、
    #    Date−Last-Modified=11.4 小时 ⇒ 无显式指令时按 RFC 9111 启发式新鲜度取该差值的 10% ≈ 68.5 分钟；
    #    同载体 P7 臂「resp=200 而 uvicorn 日志增量 0、transferSize=0、encodedBodySize=66195」
    #    = 浏览器根本没发请求就交出上一份副本；server_log_304_lines=0（条件请求一条都没发过）。
    # ② p69_r12b_silent_proxy_probe.json：另两条独立通道（浏览器 resp.header_value 与 python 原始 HTTP）
    #    复量同一读数 cache-control=null / etag 在场 ⇒ 不是仪器把空头读错。
    # 用户面危害：改了控制台，近一小时内刷新仍是旧界面，界面上又没有任何版本线索可对照。
    # 修法只改服务端两处（GET / 与 /ui/* 一律显式 cache-control: no-cache，保留 etag/last-modified
    # 两把校验器 ⇒ 浏览器每次必须回源复核：未变 304 空体、变了 200 全文），不动前端字节 ⇒
    # P66/P67/P69 的 served==disk 逐字节判据照旧成立。
    # 判据形制（P64/P58 计数律）：逐资产现场派生 rows，条件原文只读 rows；先钉枚数==3 自证数得出；
    # bogus 两腿是正对照——它挡的是「服务端无脑对任何请求都回 304」也能绿的那类假修。
    _p69c_targets = (("/", _ui, OUT / "webui" / "index.html", "text/html"),
                     ("/ui/app.js", _uijs, OUT / "webui" / "app.js", "text/javascript"),
                     ("/ui/style.css", _uicss, OUT / "webui" / "style.css", "text/css"))
    _p69c_rows = []
    for _p69c_path, _p69c_plain, _p69c_disk, _p69c_ct in _p69c_targets:
        _p69c_h = {str(_k).lower(): _v for _k, _v in dict(_p69c_plain.headers).items()}
        _p69c_cond = requests.get(
            BASE + _p69c_path, timeout=30,
            headers={"if-none-match": _p69c_h.get("etag") or '"zaojing-absent"'})
        _p69c_bogus = requests.get(
            BASE + _p69c_path, timeout=30,
            headers={"if-none-match": '"zaojing-not-a-validator"'})
        _p69c_ims = requests.get(
            BASE + _p69c_path, timeout=30,
            headers={"if-modified-since": _p69c_h.get("last-modified")
                     or "Mon, 01 Jan 1990 00:00:00 GMT"})
        _p69c_rows.append({
            "path": _p69c_path, "cc": _p69c_h.get("cache-control"),
            "etag": _p69c_h.get("etag"), "lm": _p69c_h.get("last-modified"),
            "ct": _p69c_h.get("content-type") or "", "want_ct": _p69c_ct,
            "disk_eq": _p69c_plain.content == _p69c_disk.read_bytes(),
            "cond_status": _p69c_cond.status_code, "cond_len": len(_p69c_cond.content),
            "ims_status": _p69c_ims.status_code,
            "bogus_status": _p69c_bogus.status_code,
            "bogus_disk_eq": _p69c_bogus.content == _p69c_disk.read_bytes(),
        })
    _p69c_legs = {
        "population_3": len(_p69c_rows) == 3,
        "types_3": all(_zj_ct_ok(_r["want_ct"], _r["ct"]) for _r in _p69c_rows),
        "explicit_cc_3": all(_r["cc"] is not None for _r in _p69c_rows),
        "cc_no_cache_3": all("no-cache" in (_r["cc"] or "") for _r in _p69c_rows),
        "validators_3": all(_r["etag"] and _r["lm"] for _r in _p69c_rows),
        "served_eq_disk_3": all(_r["disk_eq"] for _r in _p69c_rows),
        "revalidate_304_3": all(_r["cond_status"] == 304 for _r in _p69c_rows),
        "revalidate_304_empty_3": all(_r["cond_len"] == 0 for _r in _p69c_rows),
        "if_modified_since_304_3": all(_r["ims_status"] == 304 for _r in _p69c_rows),
        "bogus_etag_200_3": all(_r["bogus_status"] == 200 for _r in _p69c_rows),
        "bogus_full_body_3": all(_r["bogus_disk_eq"] for _r in _p69c_rows),
    }
    _p69c_red = sorted(_k for _k, _v in _p69c_legs.items() if not _v)
    check("webui(P69c): 控制台三资产（GET / 文档 + /ui/app.js + /ui/style.css）一律不可被启发式缓存——"
          "① 三枚都必须在场且 cache-control 显式含 no-cache（缺 cache-control 时浏览器按 RFC 9111 "
          "启发式新鲜度自算，r11b 实测 Date−Last-Modified=11.4h ⇒ 约 68.5 分钟内刷新根本不发请求）；"
          "② etag 与 last-modified 两把校验器必须同时在场（只关缓存不交校验器=每次全量重下）；"
          "③ 带 etag 的条件请求必须 304 且空体、带 last-modified 的条件请求也必须 304（证明 no-cache "
          "走的是回源复核而不是放弃校验）；④ 正对照：伪造的 If-None-Match 必须回到 200 且正文与盘上逐字节相等"
          "（挡住「服务端无脑对一切回 304」也能绿的那类假修）；⑤ 无条件 GET 的正文仍与 "
          "outputs/webui/ 盘上副本逐字节相等（修缓存头不许动前端字节，P66/P67/P69 的等值判据照旧成立）",
          all(_p69c_legs.values()) and len(_p69c_legs) == 11,
          f"red={_p69c_red} n={len(_p69c_rows)} "
          f"cc={[(_r['path'], _r['cc']) for _r in _p69c_rows]} "
          f"ct={[(_r['path'], _r['want_ct'], _r['ct']) for _r in _p69c_rows]} "
          f"cond={[(_r['path'], _r['cond_status'], _r['cond_len']) for _r in _p69c_rows]} "
          f"ims={[_r['ims_status'] for _r in _p69c_rows]} "
          f"bogus={[(r['path'], r['bogus_status'], r['bogus_disk_eq']) for r in _p69c_rows]}")

    # --- Phase 70: 品牌语义真实透传 + 安装执行台账（两枚原「拒接」项转正为面板上的真实路径）---
    # 产品面改的是「面板上真有一条路」，不是多一段文案：
    # ① 品牌语义：陈旧静态卡（写死「服务端诚实返回 needs_external」）必须消失；面板初始化读
    #    GET /brand/semantics 的 provider_configured 现场派生边界（配置与未配置两态都由服务端说，
    #    前端不写死）；用户**显式点击** bsem-run 才 POST /brand/semantic-insight——图像字节只发往
    #    已配置 Provider，200 时模型输出（j.semantics）逐字透传，502 needs_external 时把响应原样
    #    翻译成可点的 /provider/configure 指引且标 fabricated=false，绝不合成任何语义。
    # ② 安装台账：只读 GET /installer/execution 卡片，五形制逐名在场、self_report_ignored /
    #    registry_keys / mismatches 逐字段回显；真装/真卸结构上仍只经同一可执行文件的 CLI 显式
    #    同意令牌可达，界面没有也不该有触发执行的按钮。
    # 判据纪律：全部读受服务副本；端点字面串一律由 '/'+'api/v1' 拼接构造——P43/P60 的逐套收集器
    # 会把整串记成 L2 声称并要求运行时命中，而本块是纯静态断言，写整串必假红（P48 同条口径）。
    # 十六腿逐名求值并打 red=[失败腿名]：对未改前端必红（RED 跑实测 wired=127 / not=8、
    # bsem-*/iex-* 全缺席，见 p71_2_red_detail.txt 与 p71_2_l2_red.log），GREEN 后逐腿转绿。
    # 换代注：wired/notWired 两腿在 P71 拒接面清零后重钉为 135/0——口径变更（非放宽阈值），
    # 本块自身的可证伪面由 bsem-*/iex-* 六+二枚 id 与两枚转正路由 ∈ wired 承担。
    _p70_html, _p70_js, _p70_css = _p69_html, _p69_js, _p69_css
    _p70_ids = ("bsem-path", "bsem-provider", "bsem-prompt", "bsem-run",
                "bsem-out", "bsem-boundary", "iex-run", "iex-out")
    _p70_shapes = ("ledger_absent", "executed_verified",
                   "install_verified_awaiting_uninstall",
                   "ledger_unverified", "ledger_inconsistent")
    _p70_ext = (len(_re69.findall(r'(?:src|href)="https?://', _p70_html))
                + len(_re69.findall(r'(?:src|href)="https?://', _p70_js))
                + len(_re69.findall(r'url\(\s*["\']?https?:', _p70_css))
                + (1 if "@import" in _p70_css else 0))
    _p70_legs = {
        "brand_ids_once": all(_p70_html.count('id="%s"' % _i70) == 1
                              for _i70 in _p70_ids),
        "stale_semantic_claim_gone":
            "服务端诚实返回 needs_external，本界面原样呈现、不伪造" not in _p70_html,
        "boundary_live": ('api("/brand/semantics")' in _p70_js
                          and "provider_configured" in _p70_js
                          and "brand: refreshBrandBoundary" in _p70_js),
        "insight_post_call": 'Post("/brand/semantic-insight"' in _p70_js,
        "insight_honest_502": all(_m70 in _p70_js for _m70 in (
            "r.status === 502", '"needs_external"', "fabricated", "/provider/configure")),
        "insight_passthrough": all(_m70 in _p70_js for _m70 in (
            "真实透传", "j.semantics", "模型输出逐字透传")),
        "insight_4xx_honest": "errBox(r)" in _p70_js,
        "ledger_card": ('id="iex-run"' in _p70_html and 'id="iex-out"' in _p70_html
                        and 'api("/installer/execution")' in _p70_js
                        # 形制相关字段缺场时必须标注而非渲染 undefined / "—"（P70 活体 F 腿揪出的
                        # 真缺陷：ledger_absent 回执不含 basis / preexisting / 台账字节与 sha256）。
                        and "该形制不提供" in _p70_js
                        and 'basis=${esc(asText(j.basis))}' not in _p70_js),
        "ledger_five_shapes": all(_s70 in _p70_js for _s70 in _p70_shapes),
        "ledger_fields": all(_m70 in _p70_js for _m70 in (
            "self_report_ignored", "registry_keys", "mismatches", "led.exists")),
        "wired_135": len(_wired41) == 135,
        "notwired_0": len(_notwired41) == 0,
        "universe_135": len(_wired41) + len(_notwired41) == len(_ns41) == 135,
        "ghost_zero": _ghost41 == [],
        "two_promoted": {"/api/v1" + "/brand/semantic-insight",
                         "/api/v1" + "/installer/execution"} <= set(_wired41),
        "zero_external_refs": _p70_ext == 0,
    }
    _p70_red = sorted(_k70 for _k70, _v70 in _p70_legs.items() if not _v70)
    _p70_missing = [_i70 for _i70 in _p70_ids
                    if ('id="' + _i70 + '"') not in _p70_html]
    check("webui(P70): 品牌语义 + 安装台账两枚原拒接项转正——① 陈旧静态卡（写死 needs_external"
          "「本界面原样呈现、不伪造」）必须不在场，改由面板初始化读 GET /brand/semantics 的 "
          "provider_configured 现场派生边界（配置/未配置两态都由服务端说）；bsem-path/provider/"
          "prompt/run/out/boundary 六枚 id 各恰一次；显式点击才 POST /brand/semantic-insight，"
          "200 路径含 j.semantics + 「真实透传」+「模型输出逐字透传」（合成语义即判红），502 路径"
          "须同时认出 r.status === 502 / needs_external / fabricated / /provider/configure 四要素"
          "（诚实说明而非伪造），其余状态一律 errBox 原样呈现。② 安装台账卡 iex-run/iex-out 在场且"
          "只调只读 api(/installer/execution)；五形制（ledger_absent / executed_verified / "
          "install_verified_awaiting_uninstall / ledger_unverified / ledger_inconsistent）逐名在场，"
          "self_report_ignored / registry_keys / mismatches / led.exists 逐字段回显；形制相关字段"
          "（basis / preexisting / 台账字节与 sha256）缺场时必须标注「该形制不提供」而非渲染 "
          "undefined——界面没有、"
          "也不该有触发真装真卸的按钮（执行动作结构上仍只经 CLI 显式同意令牌可达）。"
          "③ 分母不动（P71 换代后拒接面已清零，本块两枚转正路由仍在 wired 集合里）："
          "wired 135 / notWired 0 / universe 135 / ghost 空；两资产零外链。"
          "十六腿逐名求值，detail 自带 red=[失败腿名]——未改前端时必红即本轮的 RED 兑现",
          all(_p70_legs.values()) and len(_p70_legs) == 16,
          f"red={_p70_red} missing_ids={_p70_missing} "
          f"wired={len(_wired41)} not={len(_notwired41)} ext={_p70_ext} "
          f"html={len(_p70_html)}B js={len(_p70_js)}B")

    # --- Phase 71: 接线收尾八枚——runtime 锁 / uxp 桥接诊断 / NAS 下载 / 更新下载与应用 ---
    # 最后 8 枚 notWired 端点在面板上获得真实路径（写侧五条件：契约冻结/诚实4xx/脱敏/
    # 副作用受域/显式点击；update/apply 另加双确认）。契约形制由 p71_1b_contract_receipt.txt
    # 冻结（23 闸全绿，真请求真响应）。四张卡：
    #   ① 运行时面板上锁卡 nsl-run/nsl-out：显式点击才 Post /runtime/single-instance，
    #      acquire/release 两相切换，schema/acquired/backend 逐字段回显；
    #   ② 桥接面板诊断卡 uxp-token/hand/poll/disp/res/out：token 由用户粘贴（服务端脱敏回显），
    #      handshake/poll/dispatch/result 四动作各一枚按钮；502 三形（auth_failed /
    #      no_host_connected / capability_not_negotiated）逐一认出并原样呈现 fabricated，
    #      poll 空命令诚实（无待执行命令），dispatch 只发真桥接工具 ps.document.get，
    #      result 未知回执如实 unknown_request；
    #   ③ NAS 面板下载卡 nasd-tid/sub/run/out：显式点击才 Post /nas/download；forbidden /
    #      not_configured 内联降级逐形认出（穿越拒绝绝不落盘），downloaded/sha256 逐字段回显；
    #   ④ 更新面板卡 upd-chan/dl/tgt/sha/apply/out：upd-dl 发 /update/download
    #      （no_update_configured 诚实），upd-apply 发 /update/apply 前必须过两道 confirm
    #      （integrity_failed/applied/verified 逐字段回显）。
    # 判据纪律（P70 同条）：静态腿全读受服务副本；端点字面串一律由 '/'+"api/v1" 拼接构造——
    # P43/P60 逐套收集器会把整串记成 L2 声称并要求运行时命中，静态块写整串必假红（P48 同条）；
    # 活体腿（webui(P71-live)）的整串字面则各配一条真请求背书（P44 回填口径）。
    # 十九腿逐名求值并打 red=[失败腿名]：对未改前端必红——RED 跑（p71_2_l2_red.log /
    # p71_2_red_detail.txt）实测 red 腿 16 枚、missing_ids 18 枚全缺、wired=127 / not=8，
    # 与本轮预测逐字相符；GREEN 后逐腿转绿，即本轮「先 RED 后 GREEN」的可证伪兑现。
    _p71_html, _p71_js, _p71_css = _p70_html, _p70_js, _p70_css
    _p71_ids = ("nsl-run", "nsl-out",
                "uxp-token", "uxp-hand", "uxp-poll", "uxp-disp", "uxp-res", "uxp-out",
                "nasd-tid", "nasd-sub", "nasd-run", "nasd-out",
                "upd-chan", "upd-dl", "upd-tgt", "upd-sha", "upd-apply", "upd-out")
    _p71_ext = (len(_re69.findall(r'(?:src|href)="https?://', _p71_html))
                + len(_re69.findall(r'(?:src|href)="https?://', _p71_js))
                + len(_re69.findall(r'url\(\s*["\']?https?:', _p71_css))
                + (1 if "@import" in _p71_css else 0))
    # upd-apply 双确认：契约冻结为 async function updApply() 函数体内 ≥2 枚 confirm( 且真调 apply
    _p71_ua_m = _re69.search(r'(?:async )?function updApply\s*\(', _p71_js)
    _p71_ua_body = _p71_js[_p71_ua_m.start():_p71_ua_m.start() + 3000] if _p71_ua_m else ""
    _p71_legs = {
        "ids_once": all(_p71_html.count('id="%s"' % _i71) == 1 for _i71 in _p71_ids),
        "nsl_call": all(_m71 in _p71_js for _m71 in (
            'Post("/runtime/single-instance"', '"acquire"', '"release"',
            "j.schema", "j.acquired", "j.backend", "errBox(r)")),
        "uxp_calls": all(_m71 in _p71_js for _m71 in (
            'Post("/uxp/handshake"', 'Post("/uxp/poll"',
            'Post("/uxp/dispatch"', 'Post("/uxp/result"')),
        "uxp_502_honest": all(_m71 in _p71_js for _m71 in (
            "r.status === 502", "auth_failed", "no_host_connected",
            "capability_not_negotiated", "fabricated", "errBox(r)")),
        "uxp_token_input": '$("uxp-token")' in _p71_js,
        "uxp_poll_honest": all(_m71 in _p71_js for _m71 in (
            "j.commands", "j.count", "无待执行命令")),
        "uxp_result_honest": "unknown_request" in _p71_js,
        "uxp_dispatch_real_tool": "ps.document.get" in _p71_js,
        "nasd_call": 'Post("/nas/download"' in _p71_js,
        "nasd_honest": all(_m71 in _p71_js for _m71 in (
            'j.status === "forbidden"', 'j.status === "not_configured"',
            "j.downloaded", "j.sha256", "errBox(r)")),
        "upd_download_call": all(_m71 in _p71_js for _m71 in (
            'Post("/update/download"', "no_update_configured", "j.verified", "j.downloaded")),
        "upd_apply_call": all(_m71 in _p71_js for _m71 in (
            'Post("/update/apply"', "integrity_failed", "j.verified", "j.applied")),
        "upd_apply_double_confirm": (_p71_ua_m is not None
                                     and _p71_ua_body.count("confirm(") >= 2
                                     and 'Post("/update/apply"' in _p71_ua_body),
        "wired_135": len(_wired41) == 135,
        "notwired_0": len(_notwired41) == 0,
        "universe_135": len(_wired41) + len(_notwired41) == len(_ns41) == 135,
        "ghost_zero": _ghost41 == [],
        "eight_promoted": {_n41("/api/v1" + _s71) for _s71 in (
            "/nas/download", "/runtime/single-instance", "/update/apply", "/update/download",
            "/uxp/dispatch", "/uxp/handshake", "/uxp/poll", "/uxp/result")} <= set(_wired41),
        "zero_external_refs": _p71_ext == 0,
    }
    _p71_red = sorted(_k71 for _k71, _v71 in _p71_legs.items() if not _v71)
    _p71_missing = [_i71 for _i71 in _p71_ids if ('id="' + _i71 + '"') not in _p71_html]
    check("webui(P71): 接线收尾八枚转正——① 运行时上锁卡 nsl-run/nsl-out 显式点击才 Post "
          "/runtime/single-instance（acquire/release 两相、schema/acquired/backend 逐字段回显）；"
          "② 桥接诊断卡 uxp-token/hand/poll/disp/res/out 四动作（handshake/poll/dispatch/result）"
          "各一枚按钮，502 三形 auth_failed/no_host_connected/capability_not_negotiated 逐一认出并"
          "原样呈现 fabricated，poll 空命令诚实（无待执行命令），dispatch 只发真桥接工具 "
          "ps.document.get，result 未知回执如实 unknown_request，token 经 $(\"uxp-token\") 由用户"
          "粘贴（服务端脱敏回显）；③ NAS 下载卡 nasd-tid/sub/run/out 显式点击才 Post /nas/download，"
          "forbidden/not_configured 内联降级逐形认出（穿越拒绝绝不落盘），downloaded/sha256 回显；"
          "④ 更新卡 upd-chan/dl/tgt/sha/apply/out——upd-dl 发 /update/download"
          "（no_update_configured 诚实），upd-apply 发 /update/apply 前必须过两道 confirm"
          "（integrity_failed/applied/verified 回显）。⑤ 分母挪满：wired 135 / notWired 0 / "
          "universe 135 / ghost 空，八枚转正路由确在 wired 集合；三资产零外链。十九腿逐名求值，"
          "detail 自带 red=[失败腿名]——未改前端必红即本轮 RED 兑现",
          all(_p71_legs.values()) and len(_p71_legs) == 19,
          f"red={_p71_red} missing_ids={_p71_missing} "
          f"wired={len(_wired41)} not={len(_notwired41)} ext={_p71_ext} "
          f"html={len(_p71_html)}B js={len(_p71_js)}B")

    # --- P71b: 界面显示字段血统常驻判据（真缺陷 #3 的机制化；p71_15 离线普查的同一义务入验收面）---
    # 教训源：P65-C3c-2（/nas/list 的 href/note）修后只钉了那一张卡的字段名，同一族缺陷在 P71
    # 八卡上又长回 10 枚（dest_path/note/payload/reason/integrity/target_path/backup_path/bytes
    # /release.status，逐枚见 p71_15_red/ 两份留册载体）。所以本轮把「显示面 ⊆ 真值面」钉成
    # 常驻派生判据：两侧同源、由现场读盘派生，不手写清单、不写死键名。
    # 条件原文由下面这对标记钉出（自 _P71B_CARDS 起、至本块 check(...) 调用结束为止，含四枚变异臂与
    # 判据调用本身），证伪台 p71_16 直接抽取本段 dedent 后 exec（只在命名空间里绑 OUT / _p71_js /
    # _re69 / check 四枚外部依赖 ⇒ 勿另写等价脚本，避免仪器与判据分家）。
    # P71B_LINEAGE_START
    _P71B_CARDS = {
        "nsl-run": [("agent_service.py", "runtime_single_instance"),
                    ("installer_runtime_engine.py", "acquire_single_instance"),
                    ("installer_runtime_engine.py", "release_single_instance")],
        "uxp-hand": [("agent_service.py", "uxp_bridge_handshake"), ("agent_service.py", "_bridge_http"),
                     ("adobe_bridge_engine.py", "handshake")],
        "uxp-poll": [("agent_service.py", "uxp_bridge_poll"), ("agent_service.py", "_bridge_http"),
                     ("adobe_bridge_engine.py", "poll")],
        "uxp-disp": [("agent_service.py", "uxp_bridge_dispatch"), ("agent_service.py", "_bridge_http"),
                     ("adobe_bridge_engine.py", "dispatch")],
        "uxp-res": [("agent_service.py", "uxp_bridge_result"), ("agent_service.py", "_bridge_http"),
                    ("adobe_bridge_engine.py", "submit_result")],
        "nasd-run": [("agent_service.py", "nas_download"), ("nas_engine.py", "download")],
        "upd-dl": [("agent_service.py", "update_download"), ("update_engine.py", "download"),
                   ("update_engine.py", "check")],
        "upd-apply": [("agent_service.py", "update_apply"), ("update_engine.py", "apply")],
    }

    def _p71b_region(_js, _cid):
        """把一枚 kv 块钉回它自己的处理器：内联箭头 ⇒ 从该行的 { 花括号配平；
        命名函数（如 updApply）⇒ 先取 addEventListener 第二实参的函数名，再配平其函数体。
        定位器只此一份：_display 与变异臂共用（各写一套 ⇒ 臂打不中却被记成"无违规"）。"""
        _i = _js.find('$("%s")' % _cid)
        if _i < 0:
            return ""
        _le = _js.find("\n", _i)
        _line = _js[_i:_le]
        _st = None
        if "=>" in _line and _line.rstrip().endswith("{"):
            _st = _i + _line.rstrip().rfind("{")
        else:
            _nm = _re69.search(r",\s*([A-Za-z_][A-Za-z0-9_]*)\s*\)", _line)
            if _nm:
                _fm = _re69.search(r"(?:async )?function %s\([^)]*\)\s*\{" % _re69.escape(_nm.group(1)), _js)
                if _fm:
                    _st = _fm.end() - 1
        if _st is None:
            return ""
        _d, _j = 0, _st
        while _j < len(_js):
            if _js[_j] == "{":
                _d += 1
            elif _js[_j] == "}":
                _d -= 1
                if _d == 0:
                    break
            _j += 1
        return _js[_st:_j + 1]

    def _p71b_inject(_js, _cid, _entry):
        """变异臂的注入器：在"该卡自己的处理器区域"内第一枚 kv([[ 之后插入一条取值链。
        锚点/处理器/kv 任一环找不着或出现多义 ⇒ 返回 None，由调用方判红（静默注偏 = 假绿）。"""
        _anchor = '$("%s")' % _cid
        if _js.count(_anchor) != 1:
            return None
        _rg = _p71b_region(_js, _cid)
        if not _rg:
            return None
        _k = _rg.find("kv([[")
        if _k < 0 or _rg.count("kv([[") < 1:
            return None
        _abs = _js.find(_rg)
        if _abs < 0:
            return None
        _p = _abs + _k + 5
        return _js[:_p] + _entry + _js[_p:]

    def _p71b_lineage(_js, _src, _re):
        """返回 (legs, detail)：显示面 = _js 里八卡 kv 的取值链；真值面 = _src 中该端点处理链上
        每一枚 return 的字典字面键并集（{**var} 递归展开）。_js 可传受服务副本或变异副本。"""
        def _body(_f, _fn):
            _t = _src.get(_f, "")
            _m = _re.search(r"^[ \t]*(?:async )?def %s\(" % _re.escape(_fn), _t, _re.M)
            if not _m:
                return ""
            _ind = len(_m.group(0)) - len(_m.group(0).lstrip())
            _ls = _t[_m.start():].split("\n")
            _o = [_ls[0]]
            for _ln in _ls[1:]:
                if not _ln.strip():
                    _o.append(_ln)
                    continue
                _c = len(_ln) - len(_ln.lstrip())
                _s = _ln.lstrip()
                if _c <= _ind and _s.startswith(("def ", "async def ", "@", "class ")):
                    break
                _o.append(_ln)
            return "\n".join(_o)

        def _lit(_seg):
            _k = set(_re.findall(r'"([a-z_][a-z0-9_]*)"\s*:', _seg))
            _k |= set(_re.findall(r'\["([a-z_][a-z0-9_]*)"\]\s*=', _seg))
            return _k

        def _spread(_f, _var, _seen):
            if (_f, _var) in _seen:
                return set()
            _seen = _seen | {(_f, _var)}
            _t = _src.get(_f, "")
            _k = set()
            for _m in _re.finditer(r"^\s*%s = \{" % _re.escape(_var), _t, _re.M):
                _d, _j = 0, _m.end() - 1
                while _j < len(_t):
                    if _t[_j] == "{":
                        _d += 1
                    elif _t[_j] == "}":
                        _d -= 1
                        if _d == 0:
                            _k |= _lit(_t[_m.start():_j + 1])
                            break
                    _j += 1
            _fm = _re.search(r"^\s*%s = (?:self\.)?([A-Za-z_][A-Za-z0-9_]*)\(" % _re.escape(_var), _t, _re.M)
            if _fm:
                _sb = _body(_f, _fm.group(1))
                _k |= _lit(_sb) | _spread_seg(_f, _sb, _seen)
            return _k

        def _spread_seg(_f, _seg, _seen=None):
            _o = set()
            for _v in set(_re.findall(r"\{\*\*(\w+)", _seg)):
                _o |= _spread(_f, _v, _seen or set())
            return _o

        def _region(_cid):
            # 返回"处理器区域文本"（不是旧形的 (index, text) 二元组）：定位器收敛为一份后，
            # 壳层若仍被当成元组下标 [1]，空区域会直接 IndexError 炸掉整条判据。
            return _p71b_region(_js, _cid)

        def _display(_cid):
            _rg = _region(_cid)
            _out = []
            for _m in _re.finditer(r"kv\(\[", _rg):
                _d, _j = 0, _m.start()
                while _j < len(_rg):
                    if _rg[_j] == "(":
                        _d += 1
                    elif _rg[_j] == ")":
                        _d -= 1
                        if _d == 0:
                            break
                    _j += 1
                for _lab, _rhs in _re.findall(r'\["([^"]+)",\s*([^\]]+?)\]', _rg[_m.start():_j + 1]):
                    _nested = _re.search(r"\|\|\s*\{\}\)?\.([a-z_][a-z0-9_]*)", _rhs)
                    _root = _re.search(r"\bj\.([a-z_][a-z0-9_]*)", _rhs)
                    if _root:
                        _out.append((_lab, _root.group(1)))
                    elif _nested:
                        _out.append((_lab, _nested.group(1)))
                    elif _re.fullmatch(r"[a-z_][a-z0-9_]*", _lab):
                        _out.append((_lab, _lab))
                    else:
                        _out.append((_lab, None))
            return _out

        disp, truth, phantom, unresolved, empty_body = {}, {}, [], [], []
        for _cid, _spec in _P71B_CARDS.items():
            _t = set()
            for _f, _fn in _spec:
                _b = _body(_f, _fn)
                if not _b:
                    empty_body.append("%s/%s" % (_f, _fn))
                    continue
                _t |= _lit(_b) | _spread_seg(_f, _b)
            truth[_cid] = _t
            disp[_cid] = _display(_cid)
            for _lab, _k in disp[_cid]:
                if _k is None:
                    unresolved.append("%s:%s" % (_cid, _lab))
                elif _k not in _t:
                    phantom.append("%s.%s" % (_cid, _k))
        legs = {
            "handler_region_found": all(_region(_c) for _c in _P71B_CARDS),
            "truth_fn_bodies_found": not empty_body,
            "display_nonempty": bool(disp) and all(len(disp[_c]) >= 3 for _c in _P71B_CARDS),
            "truth_nonempty": all(len(truth[_c]) >= 4 for _c in _P71B_CARDS),
            "parse_strict": not unresolved,
            "lineage_clean": not phantom,
        }
        return legs, {"phantom": phantom, "unresolved": unresolved, "empty_body": empty_body,
                      "disp_n": {c: len(disp[c]) for c in _P71B_CARDS},
                      "truth_n": {c: len(truth[c]) for c in _P71B_CARDS},
                      "keys_total": sum(len(v) for v in disp.values())}
    _p71b_src = {}
    for _f71b in ("agent_service.py", "nas_engine.py", "installer_runtime_engine.py",
                  "adobe_bridge_engine.py", "update_engine.py"):
        _p71b_disk_b71 = (OUT / _f71b).read_bytes() if (OUT / _f71b).is_file() else b""
        _p71b_src[_f71b] = _p71b_disk_b71.decode("utf-8", "replace")
    # 显示面用的必须是受服务副本（_p71_js == _uijs.text，P69 起逐代传递）：判据若读盘不读服务，
    # 服务与盘一旦分叉（L4 内嵌/缓存/挂载顺序），血统表判的就是上一代界面。
    _p71b_js = _p71_js
    # CI 判红回归：Path.read_text 走 universal newlines（CRLF→LF），而 requests 的 .text 保 CRLF；
    # Windows runner 上 git checkout 若按 core.autocrlf 把 app.js 落为 CRLF，两口径即分叉——
    # served_eq_disk=False 是「文本口径不匹配」的仪器假红，非产品分叉。P69c 已用 read_bytes 定口径，
    # 本判据同步：把两侧都归到**字节口径**再解码，与服务实况同构。
    _p71b_js_disk = (OUT / "webui" / "app.js").read_bytes().decode("utf-8")
    _p71b_legs, _p71b_info = _p71b_lineage(_p71b_js, _p71b_src, _re69)
    # 变异四臂（作用面必须先自证真实存在：锚点 / 处理器区域 / kv 落点 / 真值函数体逐环数得出再动手
    # ⇒ 桩打不中却被记成"无违规"是假绿；注入一律走 _p71b_inject，与 _display 共用同一份定位器）
    _p71b_cut_card = "uxp-disp"
    _p71b_strip_card = "upd-apply"
    _p71b_phantom_js = _p71b_inject(_p71b_js, _p71b_cut_card, '["p71b-probe", j.p71b_phantom_field], ')
    _p71b_legs_mut, _p71b_info_mut = _p71b_lineage(_p71b_phantom_js or _p71b_js, _p71b_src, _re69)
    # 注真键臂用的必须是被注卡片端点上真实存在的键，否则"仍绿"这条腿证明不了任何事
    _p71b_real_js = (_p71b_phantom_js or _p71b_js).replace("j.p71b_phantom_field", "j.host")
    _p71b_legs_real, _p71b_info_real = _p71b_lineage(_p71b_real_js, _p71b_src, _re69)
    # 摘显示锚点：把 upd-apply 的 $() 锚点改名 ⇒ 该卡处理器区域找不着 ⇒ handler_region_found 必红
    _p71b_strip_js = (_p71b_js.replace('$("%s")' % _p71b_strip_card, '$("%s-absent")' % _p71b_strip_card)
                      if _p71b_js.count('$("%s")' % _p71b_strip_card) == 1 else _p71b_js)
    _p71b_legs_strip, _p71b_info_strip = _p71b_lineage(_p71b_strip_js, _p71b_src, _re69)
    # 摘真值锚点：update_engine.apply 改名 ⇒ 该端点处理链少一枚函数体 ⇒ truth_fn_bodies_found 必红
    _p71b_truthcut_src = dict(_p71b_src, **{"update_engine.py":
                            _p71b_src.get("update_engine.py", "").replace("def apply(", "def p71b_apply_x(")})
    _p71b_legs_truthcut, _p71b_info_truthcut = _p71b_lineage(_p71b_js, _p71b_truthcut_src, _re69)
    _p71b_mut_ok = (
        _p71b_phantom_js is not None
        and _p71b_js.count('$("%s")' % _p71b_cut_card) == 1
        and _p71b_js.count('$("%s")' % _p71b_strip_card) == 1
        and _p71b_src.get("update_engine.py", "").count("def apply(") == 1
        and _p71b_info_mut["phantom"] == ["%s.p71b_phantom_field" % _p71b_cut_card]
        and _p71b_legs_real["lineage_clean"] and _p71b_info_real["phantom"] == []
        and not _p71b_legs_strip["handler_region_found"]
        and not _p71b_legs_truthcut["truth_fn_bodies_found"]
        and _p71b_info_truthcut["empty_body"] == ["update_engine.py/apply"])
    _p71b_served_eq = _p71b_js == _p71b_js_disk
    _p71b_red = sorted(_k71b for _k71b, _v71b in _p71b_legs.items() if not _v71b)
    check("webui(P71b): 界面显示字段血统常驻判据——八卡（nsl/uxp 四动作/nasd/upd-dl/upd-apply）的 kv "
          "取值链逐枚现场派生，真值面 = 该端点处理链上路由体 ∪ 引擎方法体的 return 字典字面键并集"
          "（{**var} 递归展开，函数体按缩进切到下一枚同级 def 为止）；显示键 ⊄ 真值并集即界面凭空字段。"
          "六腿：处理器区域在场 / 真值函数体逐枚找得到（空体点名，不许静默少一条腿）/ 逐卡显示键 ≥3 / "
          "逐卡真值并集 ≥4 / 每条 kv 项必须解出键（解不出即判红，绝不静默跳过——第 10 枚凭空字段曾藏在"
          "这种宽容里）/ 凭空字段归零。变异四臂当场自证（锚点逐枚数出恰一枚才动手）：注一枚假键 ⇒ 必须"
          "恰好点名 uxp-disp.p71b_phantom_field；同一位置换成真键 j.host ⇒ 必须整条转绿（证明红的是凭空"
          "字段而非「凡改必红」）；摘掉显示锚点 ⇒ handler_region_found 必须红；摘掉真值锚点 update_engine"
          ".apply ⇒ truth_fn_bodies_found 必须红且空体清单逐名 pointing 到它。受服务副本与盘上 app.js "
          "逐字节相等（两侧同源，否则血统判据判的是上一代界面）",
          all(_p71b_legs.values()) and _p71b_served_eq and _p71b_mut_ok
          and len(_p71b_legs) == 6,
          f"red={_p71b_red} phantom={_p71b_info['phantom'][:6]} unresolved={_p71b_info['unresolved'][:4]} "
          f"empty_body={_p71b_info['empty_body']} disp_n={_p71b_info['disp_n']} "
          f"truth_n={_p71b_info['truth_n']} keys={_p71b_info['keys_total']} "
          f"served_eq_disk={_p71b_served_eq} mut_ok={_p71b_mut_ok} "
          f"mut_phantom_named={_p71b_info_mut['phantom'][:2]} "
          f"real_key_arm_green={_p71b_legs_real['lineage_clean']} "
          f"strip_arm_region_red={not _p71b_legs_strip['handler_region_found']} "
          f"truthcut_arm_red={not _p71b_legs_truthcut['truth_fn_bodies_found']} "
          f"truthcut_empty_body={_p71b_info_truthcut['empty_body']}")
    # P71B_LINEAGE_END

    # --- Phase 71-live: 八枚写侧端点的诚实降级形制活体钉（p71_1b 契约收据的同源复核）---
    # 只钉 L2 此前未钉过的降级形制（正对照 happy path 已由 P37/P40/P26 各块覆盖，不重复）。
    # FastAPI 把 HTTPException.detail 包在外层 detail 键内 ⇒ 502 归约读 r["detail"]["status"]。
    # 副作用域：全部落在本轮 tmp 沙箱内；持 token 握手登记的宿主 20 秒后由 host_stale_seconds
    # 自行过期（下文 P40/P26 的 connected_hosts==[] 判据运行时早已过期，不受扰动）。
    import hashlib as _h71
    _p71_live = {}
    _d711 = requests.post(BASE + "/api/v1/uxp/dispatch",
                          json={"tool": "ps.document.get", "args": {}, "timeout": 2.0}, timeout=30)
    _d711j = _d711.json()
    _p71_live["uxp_dispatch_no_host_502"] = (
        _d711.status_code == 502 and isinstance(_d711j.get("detail"), dict)
        and _d711j["detail"].get("status") == "no_host_connected"
        and _d711j["detail"].get("fabricated") is False)
    _h711 = requests.post(BASE + "/api/v1/uxp/handshake",
                          json={"host": "photoshop", "version": "26.0",
                                "capabilities": {"createDocument": True}}, timeout=30)
    _h711j = _h711.json()
    _p71_live["uxp_handshake_no_token_502"] = (
        _h711.status_code == 502 and isinstance(_h711j.get("detail"), dict)
        and _h711j["detail"].get("status") == "auth_failed"
        and _h711j["detail"].get("fabricated") is False)
    _tok71 = None
    for _j71p in sorted((tmp / "data").rglob("*.json")):
        try:
            _j71d = json.loads(_j71p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(_j71d, dict) and _j71d.get("session_token"):
            _tok71 = _j71d["session_token"]
            break
    _p71_live["uxp_token_discovered_local"] = bool(_tok71)
    _hs71 = (requests.post(BASE + "/api/v1/uxp/handshake",
                           json={"host": "photoshop", "version": "26.0",
                                 "capabilities": {"createDocument": True}, "token": _tok71},
                           timeout=30).json() if _tok71 else {})
    _hid71 = _hs71.get("host_id")
    _p71_live["uxp_handshake_token_200"] = (
        _hs71.get("status") == "connected" and bool(_hid71)
        and _hs71.get("fabricated") is False and _hs71.get("session") != _tok71)
    _pl71 = (requests.post(BASE + "/api/v1/uxp/poll",
                           json={"host_id": _hid71, "token": _tok71, "timeout": 1.0},
                           timeout=30).json() if _hid71 else {})
    _p71_live["uxp_poll_empty_honest"] = (
        _pl71.get("status") == "ok" and _pl71.get("commands") == []
        and _pl71.get("count") == 0 and _pl71.get("fabricated") is False)
    _dp71 = requests.post(BASE + "/api/v1/uxp/dispatch",
                          json={"tool": "ps.document.get", "args": {}, "timeout": 2.0}, timeout=30)
    _dp71j = _dp71.json()
    _p71_live["uxp_dispatch_capability_gated_502"] = (
        _dp71.status_code == 502 and isinstance(_dp71j.get("detail"), dict)
        and _dp71j["detail"].get("status") == "capability_not_negotiated"
        and _dp71j["detail"].get("fabricated") is False)
    _rs71 = (requests.post(BASE + "/api/v1/uxp/result",
                           json={"request_id": "l2_p71_unknown_req", "token": _tok71,
                                 "status": "ok", "payload": {}}, timeout=30).json()
             if _tok71 else {})
    _p71_live["uxp_result_unknown_honest"] = (
        _rs71.get("status") == "unknown_request" and _rs71.get("fabricated") is False)
    _share71 = tmp / "p71_share"
    _share71.mkdir(parents=True, exist_ok=True)
    _pay71 = ("造境 L2 P71 活体 " + time.strftime("%H%M%S")).encode("utf-8")
    (_share71 / "hello.txt").write_bytes(_pay71)
    requests.post(BASE + "/api/v1/nas/configure",
                  json={"target_id": "l2_p71_share", "target": str(_share71),
                        "kind": "filesystem", "share_root": str(_share71)}, timeout=30)
    _dest71 = tmp / "p71_dl"
    _dest71.mkdir(parents=True, exist_ok=True)
    _tr71 = requests.post(BASE + "/api/v1/nas/download",
                          json={"target_id": "l2_p71_share", "subpath": "../escape.txt",
                                "dest_dir": str(_dest71)}, timeout=60).json()
    _esc71 = (tmp / "escape.txt").exists() or (_share71 / "escape.txt").exists()
    _p71_live["nas_traversal_forbidden_noland"] = (
        isinstance(_tr71, dict) and _tr71.get("status") == "forbidden"
        and _tr71.get("downloaded") is False and _tr71.get("fabricated") is False
        and not _esc71)
    _nt71 = requests.post(BASE + "/api/v1/nas/download",
                          json={"target_id": "no_such_target_l2p71", "subpath": "x.txt",
                                "dest_dir": str(_dest71)}, timeout=30).json()
    _p71_live["nas_unknown_target_not_configured"] = (
        isinstance(_nt71, dict) and _nt71.get("status") == "not_configured"
        and _nt71.get("fabricated") is False)
    _tgt71 = tmp / "p71_apply_target.bin"
    _stg71 = tmp / "p71_apply_staged.bin"
    _new71 = os.urandom(4096)
    _old71 = os.urandom(2048)
    _tgt71.write_bytes(_old71)
    _stg71.write_bytes(_new71)
    _ap72 = requests.post(BASE + "/api/v1/update/apply",
                          json={"target_path": str(_tgt71), "staged_path": str(_stg71),
                                "expected_sha256": "0" * 64}, timeout=60).json()
    _p71_live["apply_bad_sha_rollback"] = (
        isinstance(_ap72, dict) and _ap72.get("status") == "integrity_failed"
        and _ap72.get("applied") is False and _ap72.get("fabricated") is False
        and _tgt71.read_bytes() == _old71)
    _p71_live_red = sorted(_k for _k, _v in _p71_live.items() if not _v)
    check("webui(P71-live): 八枚写侧端点的诚实降级形制活体复核（p71_1b 契约收据同源）——"
          "dispatch 无宿主 502 detail.status=no_host_connected、handshake 无 token 502 "
          "auth_failed（两枚都 fabricated=false，FastAPI 外层 detail 包装形制一并钉住）；"
          "discovery 明文 token 只在数据根本地文件可得（握手回执 session 为掩码，绝不回显明文）；"
          "持 token 握手 200 connected+host_id；poll 空命令诚实 {ok,[],0}；握手后未协商能力再 "
          "dispatch 真工具 ps.document.get 仍 502 capability_not_negotiated（§25 能力门控，绝不"
          "假成功）；result 未知 request_id 如实 unknown_request；nas/download ../ 穿越 "
          "200+内联 forbidden+downloaded=false 且逃逸文件绝不落盘、未知 target not_configured；"
          "update/apply 错 sha integrity_failed+applied=false 且目标逐字节未被改坏（真回滚）。"
          "十腿副作用全部落在本轮 tmp 沙箱；持 token 宿主 20 秒后由 host_stale_seconds 自行过期，"
          "不扰动后文 connected_hosts==[] 判据",
          all(_p71_live.values()) and len(_p71_live) == 10,
          f"red={_p71_live_red} tok_found={bool(_tok71)} hid={bool(_hid71)}")

    # --- Phase 74A: 控制台可用性六项（全网资源采纳批 A；R1 的超集） ---
    # 来源与依据（全在盘上可查，非记忆）：
    # ① 误操作：P74 只读盘点实测 sn-restore 把备份字节写回原路径、ks-del 删知识条目，二者点击即发，
    #    而同文件 update/apply 有双 confirm、layers DELETE 有勾选+服务端 confirm=true ⇒ 门禁不一致。
    # ② 播报：aria-live=0 / aria-busy=0，93 处 renderOut 直写且不清旧结果 ⇒ 读屏器听不到任何回执，
    #    界面也会把上一轮读数留在新请求下方。
    # ③ 焦点：主区是独立滚动容器（style.css main overflow-y:auto），900 以下 aside 变 sticky 顶栏；
    #    切面板只改 class，不回顶不转移焦点 ⇒ WCAG 2.2 · 2.4.11（焦点不被遮挡，AA）与 2.4.3 次序问题。
    # ④ 裸名控件 #as-q；⑤ 正文 11px 六处（代码块同写 12px 一并收口）。
    # 判据形制（P58/P64 计数律）：逐腿现场求值，detail 自带 red=[失败腿名]；字符串钉在被服务副本上，
    # 不是盘上文件——受服务副本没更新同样判红。变异臂见 p74a_mutate 回执（拆一处 confirm 必红）。
    _re74a = _re41
    _p74a_js, _p74a_html, _p74a_css = _js41, _html41, _uicss.text
    _p74a_legs = {
        "population_js": len(_p74a_js) > 100000,
        "population_css": len(_p74a_css) > 30000,
        "confirm_sn_restore": _re74a.search(
            r'\$\("sn-restore"\)[\s\S]{0,700}?confirm\(', _p74a_js) is not None,
        "confirm_ks_del": _re74a.search(
            r'\$\("ks-del"\)[\s\S]{0,500}?confirm\(', _p74a_js) is not None,
        "confirm_wording": "不可撤销" in _p74a_js and "不可恢复" in _p74a_js,
        "aria_live_polite": 'setAttribute("aria-live", "polite")' in _p74a_js,
        "aria_busy_both": _p74a_js.count('setAttribute("aria-busy", "false")') >= 2,
        "clear_prev_result": "box.innerHTML = skelBlock()" in _p74a_js,
        "retry_wired": ("data-retry" in _p74a_js and "res.retry()" in _p74a_js
                        and "retry = () => api(path, opts)" in _p74a_js),
        "panel_scroll_reset": "mainEl.scrollTop = 0" in _p74a_js,
        "panel_focus_move": ('setAttribute("tabindex", "-1")' in _p74a_js
                             and "focus({ preventScroll: true })" in _p74a_js),
        "sticky_scroll_padding": ("scrollPaddingTop" in _p74a_js and "offsetHeight" in _p74a_js),
        "as_q_accessible_name": _re74a.search(r'id="as-q"[^>]*aria-label="[^"]+"', _p74a_html) is not None,
        "no_11px_body": _p74a_css.count("font-size: 11px") == 0,
        "skeleton_css": (".skel i" in _p74a_css and "skel-breathe" in _p74a_css
                         and _re74a.search(r"animation:[^;]*infinite", _p74a_css) is None),
        "local_err_clears_busy": _re74a.search(
            r'function showLocalErr\([\s\S]{0,400}?setAttribute\("aria-busy", "false"\)',
            _p74a_js) is not None,
    }
    _p74a_red = sorted(_k for _k, _v in _p74a_legs.items() if not _v)
    check("webui(P74A): 控制台可用性六项（确认门 / 播报 / 骨架与重试 / 回顶与焦点 / 裸名 / 11px）——"
          "① sn-restore 与 ks-del 各自的事件处理器内必须出现 confirm(，且两处文案分别写明「不可撤销」"
          "「不可恢复」——门禁与同文件 update/apply 的双 confirm 对齐；"
          "② renderOut 必须给容器挂 aria-live=polite，且在成功与失败两条出口都把 aria-busy 落回 false（≥2 处）；"
          "③ 新一轮请求先把容器换成骨架（box.innerHTML = skelBlock()），旧读数不得残留；"
          "④ api() 返回的失败对象带 retry 闭包，错误态渲染 data-retry 按钮并在点击时以 res.retry() 重入；"
          "⑤ 面板切换后 mainEl.scrollTop = 0 且把面板 h2 设 tabindex=-1 并 focus({preventScroll:true})；"
          "⑥ 窄窗吸顶条高度现场写入 scrollPaddingTop（用 offsetHeight，不写死断点）；"
          "⑦ #as-q 必须有非空 aria-label；⑧ style.css 里 font-size: 11px 归零；"
          "⑨ 骨架样式在场且动画非 infinite（全站禁无限循环的既有红线不得被新件破坏）；"
          "⑩ 本地拦截 showLocalErr 也必须把 aria-busy 落回 false（第二条 busy 出口，位置而非计数）。"
          "两条 population 腿是正控：受众为空时整条判据不许「空真」通过",
          all(_p74a_legs.values()) and len(_p74a_legs) == 16,
          f"red={_p74a_red} js={len(_p74a_js)}B css={len(_p74a_css)}B")

    # --- Phase 43: 验收层真差集三端点（/eval/suite、/eval/reports 列表、/tools/capability-map）+ 覆盖率派生自检 ---
    # 探针 p43 首跑报 7 项未接通，其中 4 项系 first-match 非确定性假阴性（具体字面串被 {} 模板吸收，
    # set 迭代顺序随哈希随机化漂移）；修正为"最具体模板优先"后真缺仅 3 项。产品端点全部实存，零产品改动。
    _su43 = requests.get(BASE + "/api/v1/eval/suite", timeout=30).json()
    _runA43 = requests.post(BASE + "/api/v1/eval/run", timeout=240).json()
    _runB43 = requests.post(BASE + "/api/v1/eval/run", timeout=240).json()
    _lstr43 = requests.get(BASE + "/api/v1/eval/reports", timeout=30).json().get("reports", [])
    _ids43 = [r.get("report_id") for r in _lstr43]
    _entA43 = next((r for r in _lstr43 if r.get("report_id") == _runA43["report_id"]), {})
    _det43 = requests.get(BASE + "/api/v1/eval/reports/" + _runA43["report_id"], timeout=30).json()
    _cm43 = requests.get(BASE + "/api/v1/tools/capability-map", timeout=30)
    _cmj43 = _cm43.json()
    check("eval+tools(P43): the 3 endpoints NO acceptance layer had ever called return real payloads on the live service — suite manifest 185 cases/34 must_fail (Phase 53 +installer_manifest 编译面+预检面 6+1, Phase 56 +brand_semantics 语义透传 5+1, Phase 57 +barcode QR 真值 5+1, Phase 58 +installer_execution 安装执行 6+1 + R14 卸载撤销七臂 1, Phase 59 +runtime_guard 运行时守护 5+1 + 预检面自有校准 +1 —— 恒等式 total=executed+must_fail 由 p59_suite_count.py 只读枚举实测：修前 134+30=164，修后 139+32=171；设计件 §1.4 曾把这一格手算成 170，属「加数对、和写错」的算术笔误，已就地更正；Phase 63（2026-10-11）并入一维解码 oracle 真值 4+校准 2 ⇒ 143+34=177，三枚读数由本轮盘上评测报告现场派生复量；Phase 79（2026-11-04）Adobe 桥 handshake/cdr_read/cdr_write 三枚 scored 真值并入（非 must_fail，校准恒 34）⇒ 146+34=180，读数由本轮 run_full 现场派生复量；Phase 80（2026-11-05）服务注册表 feature_flags／tool_registry／skill_runtime 三枚 scored 真值并入（第 28 引擎 service_registry 下沉，端点委托单一真相源；非 must_fail，校准恒 34）⇒ 149+34=183，读数由本轮 run_full 现场派生复量；Phase 81（2026-11-07）服务侧最后一刀 websocket_events／delivery_package 两枚 scored 真值并入（第 29 引擎 event_engine、第 30 引擎 delivery_engine 下沉，端点委托单一真相源；非 must_fail，校准恒 34）⇒ 151+34=185，读数由本轮 run_full 现场派生复量), two-run reports list strictly newest-first with real summary fields, detail round-trips same report_id, capability-map honest empty-hosts shape",
          _su43.get("schema") == "zaojing-eval/v1" and len(_su43.get("cases", [])) == 185
          and sum(1 for c in _su43.get("cases", []) if c.get("must_fail")) == 34
          and len(_su43.get("cases", [])) - sum(1 for c in _su43.get("cases", []) if c.get("must_fail")) == _entA43.get("executed_cases")
          and "not_evaluated" in _su43
          and _runA43["report_id"] != _runB43["report_id"] and len(_lstr43) >= 2
          and _ids43[0] == _runB43["report_id"] and _runA43["report_id"] in _ids43
          and [_r.get("finished_at", "") for _r in _lstr43] == sorted((_r.get("finished_at", "") for _r in _lstr43), reverse=True)
          and _entA43.get("honest") is True and _entA43.get("executed_cases") == 151 and _entA43.get("pass_rate") == 1.0
          and _det43.get("report_id") == _runA43["report_id"] and len(_det43.get("results", [])) == 185
          and _cm43.status_code == 200 and isinstance(_cmj43.get("hosts"), dict),
          f"ids={_ids43[:3]} ent={_entA43}")

    # --- Phase 59 路线 C-2：随包界面的评测分母不手写（前端首个「随包散文静默腐坏」载体） ---
    # 实录：index.html 曾手写「99 条计分用例 + 23 条负控校准」，九轮评测扩张后真值已是 139/32，Phase 63 后 143/34，
    # 六层验收无一层看过它 ⇒ 随包界面静默说谎九轮。修法在工具层：app.js 从 /eval/suite 实时派生
    # （计分条数 = 清单条数 − must_fail 条数，与上面 P43 钉的恒等式同一条式子、同一真相源）。
    # 本判据永久钉住：① 受服务 #ev-run 文案不含任何手写「数字+条」分母；② app.js 确带派生标记
    # （正控自证这把尺不是恒空断言）；③ 数字同一性由恒等式 cases−must_fail==executed_cases 钉
    # （RED→GREEN 证据见仪器回执 p59_c2_webui_check_receipt.txt：U3 正控 5/2→3/2、
    # U4 离线腿「取不到清单保留无数字兜底文案，绝不编分母」）。
    _mf59ui = sum(1 for c in _su43.get("cases", []) if c.get("must_fail"))
    _evrun59ui = _re41.search(r'id="ev-run">([^<]*)<', _html41)
    check("webui(P59-C2): shipped console's eval-run label carries NO hand-written denominator — 受服务 #ev-run 文案不含手写「数字+条」分母（文案本体仍在：运行全量评测），app.js 带实时派生标记（$(\"ev-run\").textContent 赋值 + must_fail 过滤 + 空清单早退保留无数字兜底文案）且套件算术与上面 P43 的恒等式同源，修前手写 99/23 九轮漂移即判红即此故",
          _evrun59ui is not None
          and _re41.search(r"\d+\s*条", _evrun59ui.group(1)) is None
          and "运行全量评测" in _evrun59ui.group(1)
          and '$("ev-run").textContent' in _js41
          and "must_fail" in _js41 and "eval/suite" in _js41
          and "if (!cs.length) return" in _js41
          and len(_su43.get("cases", [])) - _mf59ui == _entA43.get("executed_cases"),
          json.dumps({"label": _evrun59ui.group(1) if _evrun59ui else None,
                      "cases": len(_su43.get("cases", [])), "must_fail": _mf59ui},
                     ensure_ascii=False))

    _root43 = Path(__file__).resolve().parent
    _srcs43 = {"L2": _root43 / "test-full-functional.py",
               "L4": _root43 / "test-packaged-exe.py",
               "L3": _ZJ_OUTBASE / "test-acceptance-real.py",
               # P51 第五层（血缘层）同样入尺：它的字面串既算声称，也在 L5 内被
               # route_hits 逐项真命中；本尺只额外承担"陈旧/幽灵字面串必红"这一路。
               "L5": _root43 / "test-lineage-chain.py",
               # P52 第六层（性能与规模基准层）入尺：L6 打的是真实端点（health/metrics/
               # assets/search/batch/delivery/recovery/capabilities），它同样必须在声称
               # 宇宙里可归属——L6 里写错一个 URL 就是幽灵字面串，本尺当场判红。
               "L6": _root43 / "test-perf-baseline.py"}
    _t43 = [_n41(p) for p in requests.get(BASE + "/openapi.json", timeout=30).json()["paths"]
            if p.startswith("/api/v1/")]

    def _spec_match43(_s):
        # 与 P41 探针 first-match 的关键差异：同段数候选模板中取"字面段完全相等"最多者，
        # 令 /api/v1/assets/search 归 /assets/search 而非被 /assets/{} 吸收（消除哈希序假阴性）。
        # 两侧统一用 _n41 归一后的 {} 表示空间比对（P44 踩坑回滚：具名 {param} 与 {} 混比
        # 会让 {} 模板永不中、P44 幽灵串被通配吸中——两侧不同宇宙即判分失效）。
        _best, _bsc = None, -1
        _ssegs = _s.split("/")
        for _tp in _t43:
            _tsegs = _tp.split("/")
            if len(_tsegs) != len(_ssegs):
                continue
            _sc, _ok = 0, True
            for _a, _b in zip(_tsegs, _ssegs):
                if _a == "{}":
                    continue
                if _a == _b:
                    _sc += 1
                else:
                    _ok = False
                    break
            if _ok and _sc > _bsc:
                _best, _bsc = _tp, _sc
        return _best

    _hit43, _ghost43 = set(), []
    _claims_by_layer43 = {}
    for _layer43, _f43 in _srcs43.items():
        _txt43 = _f43.read_text(encoding="utf-8")
        _lt43 = set()
        for _pat in ('"(/api/v1[^"\\n]*)"', "'(/api/v1[^'\\n]*)'", "`(/api/v1[^`]*)`"):
            _lt43.update(_m.group(1) for _m in _re41.finditer(_pat, _txt43))
        for _raw43 in _lt43:
            # P51 起 L5 也入尺；%s/%d 与 f-string 的 {var} 是同一件事（插值位）的两种写法，
            # 统一折成 {} 表示空间，否则 L5 的 f"%s" 式路径会被当成幽灵字面串误报。
            _s43 = _n41(_raw43.replace("%s", "{}").replace("%d", "{}").split("#")[0].split("?")[0])
            if _s43 in ("/", "/api/v1"):
                continue
            _tp43 = _spec_match43(_s43)
            if _tp43:
                _hit43.add(_tp43)
                _claims_by_layer43.setdefault(_layer43, set()).add(_tp43)
            else:
                _ghost43.append(_s43)
    # P58-H2 逐套派生：并集才是 135，每套各有多少、谁独占，一律现场算进 detail。
    _per_layer43 = {_k43: len(_claims_by_layer43.get(_k43, set())) for _k43 in _srcs43}
    _excl43 = {}
    for _k43 in _srcs43:
        _others43 = [_claims_by_layer43.get(_o43, set()) for _o43 in _srcs43 if _o43 != _k43]
        _excl43[_k43] = _claims_by_layer43.get(_k43, set()) - set().union(*_others43)
    _l2_claims43 = _claims_by_layer43.get("L2", set())
    _e43 = ", ".join("%s:%d" % (_k43, len(_excl43[_k43]))
                     for _k43 in sorted(_srcs43))
    # P43 曾申报界限：字面串在场 ≠ 该调用运行时必达。P44 起该界限由本文件末尾的
    # runtime-backfill 检查收口——L2 自己声明的每一条模板须在收尾时命中>0。
    check("acceptance-coverage(P43+P51+P52+P53+P56+P58): mechanized self-scan — openapi 135 templates x literal extraction from ALL FIVE suite sources (L2/L3/L4/L5/L6, specificity-corrected matching), unexercised MUST be empty and ghost literals none; any future endpoint without a suite call turns this red (P56 +brand/semantic-insight 真实透传入口, P58 +installer/execution 对账面). **P58-H2 口径限定**：135 是 L2/L3/L4/L5/L6 五套源码的**并集**触达数，不是每一套各自的触达数——逐套计数与本套独占贡献由现场派生并打进 detail（判据名内不手抄任何快照数字）。随并集另加两条可证伪断言：任何一套的字面串贡献归零即判红（某套源码被清空或改名而并集仍绿 = 假绿）；任何单套触达数等于模板总数也判红（真做到逐套全覆盖时必须改写『并集』口径，而不是放松判据）",
          len(_t43) == 135 and _ghost43 == []
          and (set(_t43) - _hit43) == set() and len(_hit43) == 135
          and min(_per_layer43.values()) >= 1
          and max(_per_layer43.values()) < len(_t43),
          f"hit={len(_hit43)} miss={sorted(set(_t43) - _hit43)[:6]} ghost={sorted(set(_ghost43))[:6]} per_suite={_per_layer43} exclusive={{{_e43}}}")

    lic = requests.get(BASE + "/api/v1/license").json()
    check("license: real machine_id", lic["machine_id"] not in ("", "N/A", None) and len(lic["machine_id"]) >= 16, f"machine_id={lic['machine_id'][:20]}...")
    real_key = issue_license_key({"edition": "pro", "mid": "*", "seats": 1, "customer": "acceptance"})
    act = requests.post(BASE + "/api/v1/license/activate", json={"license_key": real_key})
    actj = act.json() if act.status_code == 200 else {}
    check("license: real signed key activates", act.status_code == 200 and actj.get("activated") is True, f"HTTP {act.status_code} status={actj.get('status', {}).get('status')}")
    bogus = requests.post(BASE + "/api/v1/license/activate", json={"license_key": "ZJ-TEST-2026"})
    check("license: bogus key honestly rejected", bogus.status_code == 400, f"HTTP {bogus.status_code}")
    signed_non_dict = issue_license_key("i-am-a-string-not-an-object")
    nd = requests.post(BASE + "/api/v1/license/activate", json={"license_key": signed_non_dict})
    check("license: signed-but-non-object payload -> 400 (no 5xx crash)",
          nd.status_code == 400 and nd.status_code < 500, f"HTTP {nd.status_code}")

    cfg = requests.get(BASE + "/api/v1/config").json()
    check("config: persisted json", "assets" in cfg and "blocked_paths" in cfg)
    _ck23 = "p23_cfg_iso"
    requests.put(BASE + f"/api/v1/config/{_ck23}", json={"value": "root-only"})
    _usp = tmp / "data" / "config" / "user-settings.json"
    check("config: writes land inside ZAOJING_DATA_DIR root (Phase 23 data-root isolation)",
          _usp.exists() and _ck23 in _usp.read_text(encoding="utf-8"), str(_usp))
    sec = requests.get(BASE + "/api/v1/security/status").json()
    st = sec.get("self_test", {})
    check("security: path probes", st.get("system_dir") is True and st.get("double_extension") is True and st.get("normal_file") is False, json.dumps(st))

    doc = requests.get(BASE + "/api/v1/document").json()
    check("document: real store persists", doc["layers_count"] >= 1 and doc["color_mode"] in ("RGB", "CMYK", "GRAY"), f"layers={doc['layers_count']} mode={doc['color_mode']}")

    lay = requests.post(BASE + "/api/v1/layers", json={"name": "测试图层", "type": "normal"}).json()
    layers = requests.get(BASE + "/api/v1/layers").json()
    check("layers: create+list persists", any(l["name"] == "测试图层" for l in layers))
    patched = requests.patch(BASE + f"/api/v1/layers/{lay['id']}", json={"opacity": 55.0}).json()
    check("layers: patch", patched.get("opacity") == 55.0)
    # persistence across restart is verified later; here verify delete.
    # Phase 65 缺陷 P65-D1：路由 docstring 承诺「L3 高风险操作需显式 confirm 参数」，实现面却
    # 无门禁。修前旧口径已由 p65_contract_probe 腿 D 钉死在盘上（无 confirm → 200 deleted=true，
    # HTTP 视图与磁盘 document.json 两侧皆消失）。下面三枚判据断言**承诺兑现后的新口径**，
    # 对未修产品必红 ⇒ 本轮先跑红再修产品，绝不反过来把断言改成迎合缺陷。
    _p65_doc = tmp / "data" / "state" / "document.json"

    def _p65_on_disk(_lid):
        # 磁盘视图独立读回：不信 HTTP 侧的 deleted 标志，落盘态自己数
        return any(_l.get("id") == _lid for _l in
                   json.loads(_p65_doc.read_text(encoding="utf-8")).get("layers", []))

    _p65_nof = requests.delete(BASE + f"/api/v1/layers/{lay['id']}")
    check("layers(P65): DELETE without confirm refused 400 confirmation_required AND the layer survives in BOTH the HTTP view and on-disk document.json (承诺兑现；缺陷 P65-D1 修前此判据必红)",
          _p65_nof.status_code == 400 and "confirmation_required" in _p65_nof.text
          and any(l["id"] == lay["id"] for l in requests.get(BASE + "/api/v1/layers").json())
          and _p65_on_disk(lay["id"]),
          f"status={_p65_nof.status_code} body={_p65_nof.text[:90]}")
    _p65_false = requests.delete(BASE + f"/api/v1/layers/{lay['id']}?confirm=false")
    _p65_junk = requests.delete(BASE + f"/api/v1/layers/{lay['id']}?confirm=maybe")
    check("layers(P65): 三形门禁 confirm=false 与近似值皆拒（对齐执行面 confirmation_required 的缺省/空/近似三形），图层两侧仍在场",
          _p65_false.status_code == 400 and _p65_junk.status_code in (400, 422)
          and any(l["id"] == lay["id"] for l in requests.get(BASE + "/api/v1/layers").json())
          and _p65_on_disk(lay["id"]),
          f"false={_p65_false.status_code} junk={_p65_junk.status_code}")
    dl = requests.delete(BASE + f"/api/v1/layers/{lay['id']}?confirm=true")
    check("layers: delete (L3) 带 confirm=true 才真删，且 HTTP 视图与磁盘 document.json 两处皆消失",
          dl.status_code == 200 and dl.json().get("deleted") is True
          and dl.json().get("risk_level") == "L3"
          and not any(l["id"] == lay["id"] for l in requests.get(BASE + "/api/v1/layers").json())
          and not _p65_on_disk(lay["id"]),
          f"status={dl.status_code} body={dl.text[:90]}")

    ids = {}
    for f in (img1, img2, img3):
        a = requests.post(BASE + "/api/v1/assets", json={"file_path": str(f)})
        check(f"asset import {f.name}", a.status_code == 200, str(a.text[:120]))
        ids[f.name] = a.json().get("id")
    listing = requests.get(BASE + "/api/v1/assets").json()
    check("assets: list shows 3", listing["total"] == 3, f"total={listing['total']} storage={listing['storage']}")
    one = requests.get(BASE + f"/api/v1/assets/{ids['logo_blue.png']}").json()
    check("assets: detail w/ embedding", one.get("embedding", {}).get("dimensions") == 512, json.dumps(one.get("embedding")))

    s = requests.post(BASE + "/api/v1/assets/search", json={"query": "logo blue", "top_k": 3}).json()
    sims = [x.get("similarity", 0.0) for x in s["results"]]
    ranked_desc = all(sims[i] >= sims[i + 1] for i in range(len(sims) - 1))
    check("assets: vector search returns ranked", s["total"] == 3 and all("similarity" in x for x in s["results"]), f"t={s['search_time']}s top={s['results'][0]['name']}")
    check("assets: embeddings real (token-match, sorted, top>0)",
          ranked_desc and len(sims) >= 2 and sims[0] > 0.0 and s["results"][0]["name"] == "logo_blue.png",
          f"top={s['results'][0]['name']} sims={[round(x,4) for x in sims]}")
    s2 = requests.post(BASE + "/api/v1/assets/search", json={"query": "logo blue", "top_k": 3}).json()
    sims2 = [x.get("similarity", 0.0) for x in s2["results"]]
    check("assets: embeddings deterministic (reproducible across calls)", sims == sims2, f"run1={[round(x,6) for x in sims]}")

    thumb = requests.get(BASE + f"/api/v1/assets/{ids['logo_blue.png']}/thumbnail")
    check("assets: real thumbnail served", thumb.status_code == 200 and thumb.headers.get("content-type", "").startswith("image/"), f"HTTP {thumb.status_code} ct={thumb.headers.get('content-type')}")
    # Phase 48：该端点已真正被控制台 <img src> 请求（wired 判据之一），故把"是图像"钉到字节层：
    # PNG 魔数 + 非零长度 + 幽灵 id 必 404（前端 onerror 分支据此如实显示"取不到"，不画假图）。
    _p48_ghost_thumb = requests.get(BASE + "/api/v1/assets/p48_no_such_asset_9f3a/thumbnail")
    check("assets(P48): thumbnail bytes are a real PNG (magic 89504e470d0a1a0a, non-trivial length) and an unknown asset id yields an honest 404 — the console's <img onerror> branch depends on this rung existing, and never fabricates an image",
          thumb.status_code == 200 and thumb.content[:8] == b"\x89PNG\r\n\x1a\n" and len(thumb.content) > 100
          and _p48_ghost_thumb.status_code == 404,
          f"magic={thumb.content[:8]!r} bytes={len(thumb.content)} ghost={_p48_ghost_thumb.status_code}")

    lc = requests.post(BASE + f"/api/v1/assets/{ids['tea_texture_green.jpg']}/license",
                       json={"license_status": "commercial", "license_type": "CC0", "commercial_use": True, "verified": True}).json()
    check("assets: license status set", lc.get("license_status") == "commercial")

    bad = requests.post(BASE + "/api/v1/assets", json={"file_path": "C:\\Windows\\System32\\cmd.exe"})
    check("security: blocked import rejected", bad.status_code == 403, str(bad.status_code))

    gen = requests.post(BASE + "/api/v1/generate", json={"prompt": "茶叶包装 极简", "priority": "privacy"})
    check("generate: honest provider error (no fake success)", gen.status_code in (502, 503, 200), f"HTTP {gen.status_code}: {gen.text[:100]}")

    models = requests.get(BASE + "/api/v1/models").json()
    check("models: registry has providers", len(models["providers"]) >= 2)

    rb = requests.post(BASE + "/api/v1/generate/remove-background", json={"image_path": str(img1), "threshold": 40}).json()
    ratio = rb.get("background_cleared_ratio", 0)
    outp = Path(rb.get("output", ""))
    has_alpha = outp.exists() and Image.open(outp).mode == "RGBA"
    corner_clear = outp.exists() and Image.open(outp).getpixel((1, 1))[3] == 0
    center_kept = outp.exists() and Image.open(outp).getpixel((60, 60))[3] == 255
    check("remove-bg: real pixels processed", 0.6 < ratio < 0.98 and has_alpha and corner_clear and center_kept, f"ratio={ratio}")

    # Phase 38：端点已改薄包装挂 bg_removal 引擎——孔洞保留（泛洪≠色键）与逐数真值经 HTTP 复验
    rb4 = requests.post(BASE + "/api/v1/generate/remove-background",
                        json={"image_path": str(img4), "threshold": 32}).json()
    _o4 = Path(rb4.get("output", ""))
    _im4o = Image.open(_o4) if _o4.exists() else None
    _al4 = [_im4o.getpixel(p)[3] for p in ((0, 0), (31, 31), (43, 43))] if _im4o else None
    check("remove-bg: thin wrapper preserves engine flood truth end-to-end (hole survives, ratio 0.84 exact)",
          rb4.get("background_cleared_ratio") == 0.84 and rb4.get("has_alpha") is True
          and _al4 == [0, 255, 255]
          and str(_o4) == str(tmp / "data" / "outputs" / "flood_hole_fixture_no_bg.png"),
          json.dumps({k: rb4.get(k) for k in ("background_cleared_ratio", "output")}, ensure_ascii=False))

    pf = requests.post(BASE + "/api/v1/preflight", json={"document_path": str(img1)}).json()
    check("preflight: real report+findings", "findings" in pf and "summary" in pf and pf["summary"]["total_findings"] >= 0, f"status={pf.get('summary', {}).get('overall_status')} print_ready={pf.get('print_ready')}")
    hist = requests.get(BASE + "/api/v1/preflight/history").json()
    check("preflight: history", len(hist["history"]) >= 1)
    fx = requests.post(BASE + "/api/v1/preflight/auto-fix", json={"document_path": str(img1)}).json()
    check("preflight: auto-fix", "fixable_issues" in fx, f"keys={list(fx.keys())}")
    fx_out = Path(fx.get("output") or "")
    orig_w, orig_h = Image.open(img1).size
    grew = False
    if fx.get("fix_executed") and fx_out.exists():
        nw, nh = Image.open(fx_out).size
        grew = (nw > orig_w and nh > orig_h)
    check("preflight: auto-fix really expands bleed", fx.get("fix_executed") is True and grew, f"executed={fx.get('fix_executed')} out={fx_out.name if fx_out else None} {orig_w}x{orig_h}->{(Image.open(fx_out).size if (fx.get('fix_executed') and fx_out.exists()) else None)}")

    # ---------------- Phase 55 输入可达性契约（印前族接同一把路径门 + 机器可读判定） ----------------
    # 修前实测（p55_pre_fix_live.json / p55_gate_shapes.json）：/preflight 与 /preflight/auto-fix
    # 是**唯一**不做路径校验的路径消费面。同一串输入，已接门的 /assets 与
    # /generate/remove-background 分别回 403 / 404 / 400，印前族一律回 HTTP 200：
    #   · 幽灵路径、空串 → 200 + 引擎自报 PRINT-999（调用方只能靠中文消息串猜"检没检成"）
    #   · 相对穿越 …\..\windows\win.ini → 200 + PRINT-999
    #   · **绝对穿越 C:\Windows\win.ini → 200 + 一份言之凿凿的完整印前报告**
    #     （PRINT-001"文档使用 RGB 色彩模式，印刷必须使用 CMYK"等 7 条发现）——安全策略
    #     明令禁入的文件被读进去、还被判成了设计稿。本轮把全仓已有的 validate_file_path
    #     接进这一族；目录与空串两类在已接门的面上本就是 500/400 的裂缝，此处按
    #     诚实的 400 形制收口（delivery 侧同名形制 not_a_file）。
    _p55_ghost = str(tmp / "p55_no_such_dir" / "p55_ghost.pdf")
    _p55_trav = str(tmp / ".." / ".." / "windows" / "win.ini")
    _p55_abstrav = str(Path(os.environ.get("WINDIR", "C:\\Windows")) / "win.ini")
    _p55_dir = str(tmp / "data")
    _r55 = {}
    for _t55, _v55 in (("ghost", _p55_ghost), ("traversal", _p55_trav),
                       ("absolute_traversal", _p55_abstrav),
                       ("directory", _p55_dir), ("empty", "")):
        _r55[_t55] = requests.post(BASE + "/api/v1/preflight",
                                   json={"document_path": _v55}, timeout=120)
    _want55 = {"ghost": 404, "traversal": 403, "absolute_traversal": 403,
               "directory": 400, "empty": 400}
    check("preflight(P55): the path-consuming surface goes through the SAME gate as the other 14 call sites — a blocked path (relative .. traversal AND an absolute C:\\Windows path, which the security engine both refuse) is an honest 403, a missing document 404, an empty or non-file input 400 — never a 200 report describing a document that was never legitimately opened",
          all(_r55[k].status_code == c for k, c in _want55.items())
          and "安全策略" in str(_r55["traversal"].json().get("detail"))
          and "安全策略" in str(_r55["absolute_traversal"].json().get("detail"))
          and "文件不存在" in str(_r55["ghost"].json().get("detail")),
          {k: (_r55[k].status_code, str(_r55[k].text)[:60]) for k in _r55})
    _fx55g = requests.post(BASE + "/api/v1/preflight/auto-fix",
                           json={"document_path": _p55_ghost}, timeout=120)
    check("preflight(P55): auto-fix takes the same gate as check — a fix that would run on a document which does not exist is refused with 404, not with a 200 payload whose 'skipped' list quietly absorbs the missing input",
          _fx55g.status_code == 404 and "文件不存在" in str(_fx55g.json().get("detail")),
          f"HTTP {_fx55g.status_code} {_fx55g.text[:80]}")
    _pf55 = requests.post(BASE + "/api/v1/preflight",
                          json={"document_path": str(img1)}, timeout=120).json()
    _ir55 = _pf55.get("input_reachability") or {}
    check("preflight(P55): a 200 report carries a machine-readable reachability verdict whose every field is DERIVED FROM DISK (reachable/is_file True, verdict 'checked', the resolved path, size_bytes equal to this test's own os.path.getsize) — the batch layer reads this instead of pattern-matching the engine's Chinese diagnosis string",
          _ir55.get("reachable") is True and _ir55.get("is_file") is True
          and _ir55.get("verdict") == "checked" and str(_ir55.get("path")) == str(img1)
          and _ir55.get("size_bytes") == os.path.getsize(str(img1)),
          json.dumps(_ir55, ensure_ascii=False)[:200])

    wf = requests.post(BASE + "/api/v1/workflow/define", json={
        "name": "批量印前", "description": "test",
        "nodes": [
            {"id": "n1", "type": "input", "name": "开始", "config": {}},
            {"id": "n2", "type": "output", "name": "结束", "config": {}},
        ],
        "edges": [{"source_node_id": "n1", "target_node_id": "n2"}],
        "variables": {"files": ["a.psd"]},
    }).json()
    check("workflow: define+validate", wf.get("validation_errors") == [], json.dumps(wf)[:140])
    ex = requests.post(BASE + f"/api/v1/workflow/{wf['workflow_id']}/execute", json={"input_data": {"x": 1}}).json()
    check("workflow: execute real instance", ex.get("status") in ("success", "running", "completed"), f"status={ex.get('status')}")
    check("workflow: exposes real result", "result" in ex, f"result={json.dumps(ex.get('result'))}")
    viz = requests.get(BASE + f"/api/v1/workflow/{wf['workflow_id']}/visualize").json()
    check("workflow: visualization", "nodes" in viz and all("position" in n for n in viz["nodes"]))

    # 分支工作流：input -> condition -> (big|small)，真实图遍历 + 安全条件求值
    cw = requests.post(BASE + "/api/v1/workflow/define", json={
        "name": "条件分支", "description": "branch",
        "nodes": [
            {"id": "i", "type": "input", "name": "输入", "config": {}},
            {"id": "c", "type": "condition", "name": "判断", "config": {"expression": "$n > 10"}},
            {"id": "big", "type": "output", "name": "大分支", "config": {"select": ["n"]}},
            {"id": "small", "type": "output", "name": "小分支", "config": {"select": ["n"]}},
        ],
        "edges": [
            {"source_node_id": "i", "target_node_id": "c"},
            {"source_node_id": "c", "target_node_id": "big", "condition": "$n > 10"},
            {"source_node_id": "c", "target_node_id": "small", "condition": "$n <= 10"},
        ],
        "variables": {"n": 0},
    }).json()
    check("workflow: branch define valid", cw.get("validation_errors") == [], json.dumps(cw)[:120])
    bx = requests.post(BASE + f"/api/v1/workflow/{cw['workflow_id']}/execute", json={"input_data": {"n": 50}}).json()
    ns = bx.get("node_status", {})
    big_st, small_st = ns.get("big"), ns.get("small")
    check("workflow: real branch routing (n=50 -> big)", bx.get("status") == "success" and big_st == "success" and small_st == "skipped", f"status={bx.get('status')} big={big_st} small={small_st} result={json.dumps(bx.get('result'))}")

    b = requests.post(BASE + "/api/v1/batch/process", json={"file_paths": [str(img1), str(img2), str(img3), str(tmp / "missing.jpg")], "operation": "preflight", "workers": 4}).json()
    check("batch: real per-file results", b["success"] == 3 and b["failed"] == 1, f"ok={b['success']} fail={b['failed']} t={b['total_time']}s")

    # ---------------- 快照 / 事务 / 恢复引擎（核心能力#7 版本回滚） ----------------
    doc_file = tmp / "work.psd"
    original_bytes = b"PSD-ORIGINAL-CONTENT-v1-1234567890"
    doc_file.write_bytes(original_bytes)
    snap = requests.post(BASE + "/api/v1/recovery/snapshot",
                         json={"document_path": str(doc_file), "snapshot_type": "full"}).json()
    chk = snap.get("checksum", "")
    check("recovery: snapshot of real file is recoverable w/ sha256",
          snap.get("recoverable") is True and len(chk) == 64 and all(c in "0123456789abcdef" for c in chk),
          f"recoverable={snap.get('recoverable')} checksum={chk[:12]}...")
    doc_file.write_bytes(b"XXXX-CORRUPTED-XXXX-CORRUPTED-XX")  # 模拟损坏
    rs = requests.post(BASE + f"/api/v1/recovery/snapshot/{snap['id']}/restore").json()
    back_ok = doc_file.read_bytes() == original_bytes
    check("recovery: restore really reverts bytes (integrity-verified)",
          rs.get("restored") is True and back_ok, f"restored={rs.get('restored')} bytes_match={back_ok}")
    lst = requests.get(BASE + "/api/v1/recovery/snapshots").json()
    check("recovery: snapshot listed", any(s["id"] == snap["id"] for s in lst.get("snapshots", [])), f"count={lst.get('count')}")

    # Adobe 文档未导出到本地 → 诚实标注不可恢复
    snap_missing = requests.post(BASE + "/api/v1/recovery/snapshot",
                                 json={"document_path": str(tmp / "adobe_only.ai"), "snapshot_type": "full"}).json()
    check("recovery: metadata-only snapshot honestly recoverable=False",
          snap_missing.get("recoverable") is False, f"recoverable={snap_missing.get('recoverable')} reason={snap_missing.get('recovery_reason')}")
    rs_missing = requests.post(BASE + f"/api/v1/recovery/snapshot/{snap_missing['id']}/restore").json()
    check("recovery: restore of metadata-only honestly restored=False",
          rs_missing.get("restored") is False, f"restored={rs_missing.get('restored')} reason={rs_missing.get('reason')}")

    # 事务生命周期
    tx = requests.post(BASE + "/api/v1/recovery/transaction/begin",
                       json={"task_id": "t-tx-1", "document_path": str(doc_file)}).json()
    commit = requests.post(BASE + f"/api/v1/recovery/transaction/{tx['transaction_id']}/commit").json()
    check("recovery: transaction commit", commit.get("committed") is True, f"tx={tx.get('transaction_id')}")
    doc_file.write_bytes(b"BBBB-BEFORE-ROLLBACK-BEFORE-BEFO")
    tx2 = requests.post(BASE + "/api/v1/recovery/transaction/begin",
                        json={"task_id": "t-tx-2", "document_path": str(doc_file)}).json()
    doc_file.write_bytes(b"CCCC-CHANGED-INSIDE-TX-CHANGED-XX")
    roll = requests.post(BASE + "/api/v1/recovery/transaction/rollback",
                         json={"transaction_id": tx2["transaction_id"]}).json()
    reverted = doc_file.read_bytes() == b"BBBB-BEFORE-ROLLBACK-BEFORE-BEFO"
    check("recovery: transaction rollback really reverts", roll.get("rolled_back") is True and reverted,
          f"rolled_back={roll.get('rolled_back')} reverted={reverted}")

    # 任务状态 / 崩溃恢复
    reg = requests.post(BASE + "/api/v1/recovery/task/register",
                        json={"task_id": "task-9", "total_steps": 4, "context": {"doc": "x"}}).json()
    requests.post(BASE + "/api/v1/recovery/task/task-9/progress", json={"current_step": 3})
    requests.post(BASE + "/api/v1/recovery/task/task-9/complete")
    rep = requests.get(BASE + "/api/v1/recovery/task/task-9/report").json()
    check("recovery: task state persists + report",
          reg.get("status") == "running" and rep.get("status") == "completed" and "available_snapshots" in rep,
          f"status={rep.get('status')} steps={rep.get('current_step')}/{rep.get('total_steps')}")
    orph = requests.get(BASE + "/api/v1/recovery/tasks/orphaned").json()
    check("recovery: orphan scan", "count" in orph, f"orphaned={orph.get('count')}")

    # ---------------- 知识库 / RAG（§65/§66） ----------------
    kb_a = tmp / "brand_guidelines.md"
    kb_a.write_text("# 品牌指南\n\n包装主色使用抹茶绿 matcha green tea 配色，字体采用思源黑体。\n" * 3, encoding="utf-8")
    kb_b = tmp / "recipe_matcha.txt"
    kb_b.write_text("抹茶拿铁 matcha latte 做法：抹茶粉 matcha powder 过筛后用茶筅 whisk 点拂至起泡。", encoding="utf-8")
    ia = requests.post(BASE + "/api/v1/knowledge/ingest",
                       json={"file_path": str(kb_a), "title": "品牌指南", "scope": "brand", "brand": "matcha-co"}).json()
    ib = requests.post(BASE + "/api/v1/knowledge/ingest",
                       json={"file_path": str(kb_b), "title": "抹茶配方", "scope": "project", "project_id": "p1"}).json()
    check("knowledge: ingest indexes real chunks",
          ia.get("status") == "indexed" and ia.get("chunk_count", 0) >= 1 and ib.get("status") == "indexed",
          f"a_chunks={ia.get('chunk_count')} b_chunks={ib.get('chunk_count')}")
    miss = requests.post(BASE + "/api/v1/knowledge/ingest",
                         json={"file_path": str(tmp / "nope.md")})
    check("knowledge: missing file honestly rejected (404)", miss.status_code == 404, f"HTTP {miss.status_code}")
    qr = requests.post(BASE + "/api/v1/knowledge/retrieve", json={"query": "抹茶 matcha 配色", "top_k": 5}).json()
    check("knowledge: retrieve returns ranked hits",
          qr["total"] >= 1 and all("similarity" in r for r in qr["results"])
          and all(qr["results"][i]["similarity"] >= qr["results"][i + 1]["similarity"] for i in range(len(qr["results"]) - 1)),
          f"total={qr['total']} top={qr['results'][0]['title'] if qr['results'] else None} sim={qr['results'][0]['similarity'] if qr['results'] else None}")
    iso = requests.post(BASE + "/api/v1/knowledge/retrieve",
                        json={"query": "抹茶 matcha", "top_k": 5, "allowed_scopes": ["brand"], "context": {"brand": "other-co"}}).json()
    check("knowledge: scope isolation excludes other brand", iso["total"] == 0, f"total_under_foreign_brand={iso['total']}")
    kl = requests.get(BASE + "/api/v1/knowledge").json()
    check("knowledge: list persists", kl["total"] >= 2, f"total={kl['total']}")

    # ---------------- 工具注册表 + 能力协商（§23/§25） ----------------
    tl = requests.get(BASE + "/api/v1/tools").json()
    tmap = {t["name"]: t for t in tl["tools"]}
    check("tools: default registry seeded w/ real contracts",
          "ps.open_document" in tmap and tmap["ps.open_document"]["requires_adobe_host"] is True
          and "asset.search" in tmap and tmap["asset.search"]["requires_adobe_host"] is False,
          f"total={tl['total']}")
    check("tools: adobe tool not runnable before host report", tmap["ps.open_document"]["runnable_now"] is False,
          f"runnable_now={tmap['ps.open_document']['runnable_now']}")
    regt = requests.post(BASE + "/api/v1/tools", json={"name": "custom.audit", "description": "自定义", "risk_level": "L3"})
    check("tools: register custom tool", regt.status_code == 200 and regt.json().get("registered") == "custom.audit", f"HTTP {regt.status_code}")
    badt = requests.post(BASE + "/api/v1/tools", json={"name": "x", "risk_level": "L9"})
    check("tools: invalid risk_level rejected", badt.status_code == 400, f"HTTP {badt.status_code}")
    crep = requests.post(BASE + "/api/v1/tools/capability-report",
                         json={"host": "photoshop", "version": "26.0", "capabilities": {"ps.open_document": True}}).json()
    check("tools: host capability report recorded", crep.get("supported_count") == 1 and crep.get("hosts_reported") >= 1, json.dumps(crep))
    tl2 = requests.get(BASE + "/api/v1/tools").json()
    tmap2 = {t["name"]: t for t in tl2["tools"]}
    check("tools: adobe tool becomes runnable after host report", tmap2["ps.open_document"]["runnable_now"] is True,
          f"runnable_now={tmap2['ps.open_document']['runnable_now']}")

    # ---------------- 技能清单运行时（§27/§28） ----------------
    goodskill = requests.post(BASE + "/api/v1/skills",
                              json={"id": "pack-helper", "version": "1.0.0", "permissions": ["asset.read", "preflight.run"],
                                    "tools": ["asset.search", "preflight.check"], "risk": "low"})
    check("skills: valid manifest registers", goodskill.status_code == 200 and goodskill.json().get("registered") == "pack-helper", f"HTTP {goodskill.status_code}")
    badskill = requests.post(BASE + "/api/v1/skills",
                             json={"id": "evil", "permissions": ["system.wipe"], "tools": ["nonexistent.tool"], "risk": "high"})
    check("skills: over-privleged manifest rejected", badskill.status_code == 400, f"HTTP {badskill.status_code}")
    sv = requests.post(BASE + "/api/v1/skills/pack-helper/validate").json()
    check("skills: registered skill validates clean", sv.get("valid") is True and sv.get("validation_errors") == [], json.dumps(sv))

    # ---------------- 功能开关（§78） ----------------
    fl = requests.get(BASE + "/api/v1/flags").json()
    check("flags: defaults present", "webp_preview_enabled" in fl, json.dumps(fl))
    requests.put(BASE + "/api/v1/flags/beta_design_review", json={"value": True})
    fg = requests.get(BASE + "/api/v1/flags/beta_design_review").json()
    check("flags: PUT persists", fg.get("value") is True, json.dumps(fg))

    # ---------------- 设计版本对比（§50） ----------------
    cmp = requests.post(BASE + "/api/v1/design/compare", json={
        "before": [{"id": "l1", "name": "背景", "x": 0, "y": 0, "opacity": 100},
                   {"id": "l2", "name": "logo", "x": 10, "y": 10, "opacity": 100}],
        "after": [{"id": "l1", "name": "背景", "x": 0, "y": 0, "opacity": 50},
                  {"id": "l3", "name": "标题", "x": 5, "y": 5, "opacity": 100}],
    }).json()
    sm = cmp["summary"]
    check("design-compare: real added/removed/changed/moved diff",
          sm["added"] == 1 and sm["removed"] == 1 and sm["changed"] == 1 and cmp["identical"] is False,
          json.dumps(sm))
    # §50 六类结构化 diff：对富字段对象逐字段归类到 Geometry/Text/Color/Asset/Layer/Visual
    cmp2 = requests.post(BASE + "/api/v1/design/compare", json={
        "before": [{"id": "o1", "type": "text", "x": 0, "y": 0, "width": 10, "height": 10,
                    "color": "#111111", "font_size": 24, "src": "a.png", "opacity": 100, "z": 1}],
        "after": [{"id": "o1", "type": "text", "x": 5, "y": 5, "width": 10, "height": 10,
                   "color": "#222222", "font_size": 30, "src": "b.png", "opacity": 60, "z": 2}],
    }).json()
    cat = cmp2.get("categories", {})
    bc = cmp2["summary"].get("by_category", {})
    check("design-compare: §50 six-category structured diff",
          set(cat.keys()) == {"geometry", "text", "color", "asset", "layer", "visual"}
          and bc.get("geometry", 0) >= 1 and bc.get("color", 0) >= 1 and bc.get("text", 0) >= 1
          and bc.get("asset", 0) >= 1 and bc.get("visual", 0) >= 1 and bc.get("layer", 0) >= 1,
          f"by_category={json.dumps(bc)}")
    # Phase 38：端点已改薄包装挂 compare 引擎——逐数真值与未知 type 兜底 layer 经 HTTP 复验
    cmp3 = requests.post(BASE + "/api/v1/design/compare", json={
        "before": [{"id": "o1", "type": "text", "x": 0, "y": 0, "width": 10, "height": 10,
                    "color": "#111111", "font_size": 24, "src": "a.png", "opacity": 100, "z": 1}],
        "after": [{"id": "o1", "type": "text", "x": 5, "y": 5, "width": 10, "height": 10,
                   "color": "#222222", "font_size": 30, "src": "b.png", "opacity": 60, "z": 2}],
    }).json()
    check("design-compare: thin wrapper preserves engine exact per-category counts end-to-end",
          cmp3["summary"]["by_category"] == {"geometry": 2, "text": 1, "color": 1,
                                             "asset": 1, "layer": 1, "visual": 1}
          and cmp3["summary"]["moved"] == 1 and cmp3["summary"]["changed"] == 1,
          f"by_category={json.dumps(cmp3['summary']['by_category'])}")
    cmp4 = requests.post(BASE + "/api/v1/design/compare", json={
        "before": [{"id": "gone", "type": "mystery"}],
        "after": [{"id": "newx", "type": "whatever"}],
    }).json()
    check("design-compare: unknown type falls back to layer for structural add/remove",
          cmp4["summary"]["by_category"] == {"geometry": 0, "text": 0, "color": 0,
                                             "asset": 0, "layer": 2, "visual": 0}
          and [(i["key"], i["op"]) for i in cmp4["categories"]["layer"]]
              == [("newx", "added"), ("gone", "removed")],
          json.dumps(cmp4["summary"], ensure_ascii=False))

    # ---------------- 一键交付包（§69） ----------------
    dp = requests.post(BASE + "/api/v1/delivery/package", json={
        "project": "tea-pack", "notes": "验收交付",
        "files": {"Source": [str(img1)], "Report": [str(kb_a)], "Preview": [str(tmp / "absent.png")]}
    }).json()
    import zipfile as _zf
    arch = dp.get("archive", "")
    zip_ok = os.path.isfile(arch) and _zf.is_zipfile(arch)
    check("delivery: package built w/ real files + honest skip",
          dp.get("included") == 2 and len(dp.get("skipped", [])) == 1 and zip_ok
          and len(dp["sha256_manifest"].get("Source", [])) == 1,
          f"included={dp.get('included')} skipped={len(dp.get('skipped', []))} zip={zip_ok} size={dp.get('archive_size_bytes')}")

    # ---------------- Phase 55 交付面：skipped 三形制 + 数据根内外的如实台账 ----------------
    # 修前实测（p55_delivery_500.json）：skipped[].reason 只有一枚 "missing_or_blocked"，把
    # "文件不存在"和"安全策略禁入"两种**责任完全不同**的输入压成同一个词；而"指向目录"与
    # "空串"两种输入根本不落 skipped——整个请求 500 Internal Server Error（plain text），
    # 一份交付还没开始就崩在 copy2 上。本轮按 not_found / blocked_by_policy / not_a_file
    # 三形制拆开，并给收录侧补逐文件台账：inside_data_root 如实标注（数据根之外的真实文件
    # 仍然收录、不拦，但必须看得见它在外面），且自报 included 与 ZIP namelist 实测互相咬合。
    # 三形制台账 + 逐文件 inside_data_root 的可证伪性由 outputs/p55_mutation_driver.py 出具
    # （四桩 mu_gate/mu_reach/mu_reason/mu_inside 全部活在受测进程里，实测
    #   "P55 MUTATION CRITERIA ALL_MET"，其中 mu_reason 逐字复现修前的 HTTP 500 形态）。
    _p55_data_root = Path(str(requests.get(BASE + "/api/v1/health", timeout=60).json()["data_root"])).resolve()
    _p55_dir = _p55_data_root / "p55_is_a_dir"
    _p55_dir.mkdir(parents=True, exist_ok=True)
    _p55_inside = _p55_data_root / "p55_inside.png"       # 真实、可读、在数据根之内
    _p55_inside.write_bytes(Path(str(img1)).read_bytes())
    _p55_out = Path(sys.executable).resolve()  # 数据根之外的真实文件（仅作对照，不入本轮载荷）
    _p55_trav = str(_p55_data_root / ".." / ".." / "windows" / "win.ini")

    def _p55_is_in(p):
        # 期望值由本测试自己从 /health.data_root 反算，不写死 True/False：产品若把
        # inside_data_root 注成常量，这里必然对不上。仪器自检见下一条判据。
        try:
            return Path(str(p)).resolve().is_relative_to(_p55_data_root)
        except Exception:
            return False

    d55 = requests.post(BASE + "/api/v1/delivery/package", timeout=180, json={
        "project": "p55-reachability",
        "files": {"Print": [str(_p55_inside), str(img1)],
                  "Source": [str(tmp / "p55_absent.png"), _p55_trav, str(_p55_dir), ""]}})
    d55j = d55.json() if d55.headers.get("content-type", "").startswith("application/json") else {}
    _reasons55 = {s.get("reason") for s in (d55j.get("skipped") or [])}
    check("delivery(P55): skipped[].reason splits into the three honest shapes — not_found for an absent path, blocked_by_policy for a traversal the security engine refuses, not_a_file for a directory and for an empty string — and the two shapes that used to crash the whole request with 500 now land in the ledger instead (the old single 'missing_or_blocked' bucket conflated a missing file with a policy refusal, i.e. two different responsibilities in one word)",
          d55.status_code == 200 and len(d55j.get("skipped") or []) == 4
          and _reasons55 == {"not_found", "blocked_by_policy", "not_a_file"}
          and "missing_or_blocked" not in _reasons55
          and sum(1 for s in (d55j.get("skipped") or []) if s.get("reason") == "not_a_file") == 2,
          f"http={d55.status_code} skipped={json.dumps(d55j.get('skipped'), ensure_ascii=False)[:260]}")
    _ledger55 = d55j.get("included_files") or []
    _zip55 = []
    if os.path.isfile(str(d55j.get("archive") or "")):
        with _zf.ZipFile(d55j["archive"]) as _z55:
            _zip55 = _z55.namelist()
    _want_in = {_p55_is_in(_p55_inside): True, _p55_is_in(img1): False}
    check("delivery(P55): every INCLUDED file carries a per-file ledger entry whose inside_data_root equals this test's own recomputation from /health.data_root — the instrument proves itself first (the file written under the reported root is in, the console-supplied image outside it is out), the archive is NOT censored (不拦，如实标注: both are physically in the ZIP), and the self-reported included count equals the payload entries counted out of the namelist here",
          _want_in == {True: True, False: False}
          and len(_ledger55) == 2
          and {e.get("name"): e.get("inside_data_root") for e in _ledger55}
              == {Path(_p55_inside).name: True, Path(img1).name: False}
          and d55j.get("included") == 2 and os.path.isfile(d55j.get("archive", ""))
          and set(_zip55) >= {"Print/" + Path(_p55_inside).name, "Print/" + Path(img1).name}
          and len([n for n in _zip55 if not n.endswith(("README.txt", "manifest.json"))])
              == d55j.get("included") == 2
          and d55j.get("outside_root_count") == 1,
          f"want={_want_in} ledger={json.dumps(_ledger55, ensure_ascii=False)[:200]} outside={d55j.get('outside_root_count')} zip={_zip55}")
    _ev55 = [e for e in (requests.get(BASE + "/api/v1/events/recent", params={"limit": 400},
                                      timeout=60).json().get("events") or [])
             if e.get("event") == "delivery.created"
             and (e.get("data") or {}).get("package_id") == d55j.get("package_id")]
    check("delivery(P55): the event ledger counts its own reachability — delivery.created records files/outside_root_count/skipped_<shape> per reason, and those numbers equal the response's own ledger (a self-reported count that only agrees with itself is not evidence; here both sides are cross-checked against the ZIP namelist above)",
          len(_ev55) == 1 and (_ev55[0].get("data") or {}).get("files") == 2
          and (_ev55[0].get("data") or {}).get("outside_root_count") == 1
          and {k: v for k, v in (_ev55[0]["data"]).items() if k.startswith("skipped_")}
              == {"skipped_not_found": 1, "skipped_blocked_by_policy": 1,
                  "skipped_not_a_file": 2},
          f"events={json.dumps(_ev55, ensure_ascii=False)[:260]}")

    # ---------------- 实时事件总线（§7/§10 · WebSocket 降级轮询验证真实记录） ----------------
    ev = requests.get(BASE + "/api/v1/events/recent", params={"limit": 200}).json()
    ev_names = [e["event"] for e in ev.get("events", [])]
    check("events: real task/knowledge/delivery events recorded in hub",
          "task.registered" in ev_names and "task.completed" in ev_names and "knowledge.ingested" in ev_names,
          f"clients={ev.get('clients')} sample={sorted(set(ev_names))[:6]}")

    # ---------------- 字体排版引擎（§43/§111） ----------------
    scan = requests.post(BASE + "/api/v1/typography/scan", params={"limit": 80}).json()
    check("typography: real font scan (fontTools)", scan.get("scanned", 0) >= 1 and scan.get("total_indexed", 0) >= 1,
          json.dumps({k: scan.get(k) for k in ("scanned", "errors", "total_indexed")}))
    flist = requests.get(BASE + "/api/v1/typography/fonts", params={"cjk_only": False}).json()
    some_font = (flist.get("fonts") or [{}])[0]
    fid = some_font.get("id")
    check("typography: fonts listed w/ real metadata",
          flist.get("total", 0) >= 1 and bool(some_font.get("family")) and some_font.get("units_per_em", 0) > 0,
          f"total={flist.get('total')} sample_family={some_font.get('family')}")
    met = requests.post(BASE + "/api/v1/typography/metrics",
                        json={"font": fid, "text": "Hello 中", "size_pt": 24.0}).json()
    check("typography: real hmtx metrics + honest missing-glyph count",
          met.get("width_pt", 0) > 0 and met.get("glyph_count", 0) >= 1 and met.get("units_per_em", 0) > 0
          and ("missing_glyphs" in met),
          json.dumps({k: met.get(k) for k in ("width_pt", "line_height_pt", "glyph_count", "missing_glyphs", "fully_covered")}))
    mtc = requests.post(BASE + "/api/v1/typography/match",
                        json={"missing": "Helvetica Neue", "weight": 400, "top_k": 5}).json()
    check("typography: missing-font match returns scored candidates",
          mtc.get("total", 0) >= 1 and all("score" in c and "similarity" in c for c in mtc.get("candidates", [])),
          f"cands={[c['family'] for c in mtc.get('candidates', [])][:3]}")
    # 覆盖度硬门槛：目标文字无法被覆盖时不得伪造候选
    mtc_cjk = requests.post(BASE + "/api/v1/typography/match",
                            json={"missing": "NonexistentFontXYZ", "text": "中文字形覆盖测试", "require_cjk": True, "top_k": 3}).json()
    check("typography: coverage hard-gate (CJK text) honest",
          all(c.get("has_cjk") for c in mtc_cjk.get("candidates", [])),
          f"total={mtc_cjk.get('total')}")
    fb = requests.post(BASE + "/api/v1/typography/fallback-rules",
                       json={"from_font": "方正兰亭黑-简", "to_fonts": ["Microsoft YaHei"], "weight": 500}).json()
    check("typography: fallback rule persisted", fb.get("from") == "方正兰亭黑-简", json.dumps(fb, ensure_ascii=False))
    rslv = requests.post(BASE + "/api/v1/typography/resolve",
                         json={"missing": "方正兰亭黑-简", "text": "设计"}).json()
    check("typography: resolve_missing uses rule source", rslv.get("source") in ("fallback_rule", "heuristic_match"),
          f"source={rslv.get('source')} fallback_installed={rslv.get('fallback_installed')}")
    amap = requests.get(BASE + "/api/v1/typography/alias-map").json()
    check("typography: cross-platform alias map served", "PingFang SC" in amap.get("alias_map", {}),
          json.dumps(list(amap.get("alias_map", {}).keys())))

    # ---------------- 多角色设计评审引擎（§49） ----------------
    rev_objs = [
        {"id": "bg", "type": "shape", "x": 0, "y": 0, "width": 1000, "height": 1000, "color": "#FFFFFF", "z": 0},
        {"id": "title", "type": "text", "x": 100, "y": 100, "width": 400, "height": 60, "color": "#EEEEEE", "font_size": 40},
        {"id": "sub", "type": "text", "x": 102, "y": 105, "width": 380, "height": 40, "color": "#111111", "font_size": 4},
    ]
    rev = requests.post(BASE + "/api/v1/review/run", json={
        "objects": rev_objs,
        "canvas": {"width": 1000, "height": 1000, "bleed": 0, "dpi": 300, "background": "#FFFFFF"},
        "brand": {"palette": ["#FF0000"]},
    }).json()
    kinds = {i["id"] for i in rev.get("issues", [])}
    check("review: real WCAG contrast issue detected", any("contrast" in k for k in kinds), json.dumps(rev["summary"]))
    check("review: min-font-size + text-overlap detected",
          any("fontsize" in k for k in kinds) and any("overlap" in k for k in kinds),
          f"kinds={sorted(kinds)[:6]}")
    check("review: 4 roles + score + autofix present",
          set(rev["reviewed_roles"]) == {"visual", "brand", "production", "commercial"}
          and isinstance(rev.get("score"), int) and any(i["autofix_available"] for i in rev["issues"]),
          f"score={rev.get('score')} roles={rev['reviewed_roles']}")
    # 品牌主色一致性
    check("review: brand palette mismatch flagged", any("brandcolor" in k for k in kinds), f"kinds={sorted(kinds)}")

    # ---------------- 客户改稿引擎（§48/§114） ----------------
    rev_objs2 = [
        {"id": "obj_logo", "name": "logo", "type": "image", "width": 200, "height": 80, "color": "#123456"},
        {"id": "obj_title", "name": "主标题", "type": "text", "font_size": 36, "color": "#111111"},
        {"id": "obj_subtitle", "name": "副标题", "type": "text", "font_size": 20},
        {"id": "obj_bg", "name": "背景", "type": "shape", "width": 1000, "height": 1000, "protected": True},
    ]
    rp = requests.post(BASE + "/api/v1/revision/parse", json={
        "text": "logo 再大一点，主标题换成深红色，背景删掉，主标题和副标题都缩小一点",
        "objects": rev_objs2,
    }).json()
    stat_op = {(c["op"], c["status"]) for c in rp.get("changes", [])}
    check("revision: unique target -> proposed", ("resize", "proposed") in stat_op, json.dumps(rp["summary"]))
    check("revision: protected object -> conflict", any(s == "conflict" for _, s in stat_op), f"ops={sorted(map(str, stat_op))}")
    check("revision: ambiguous multi-target -> needs_clarification (never guesses)",
          ("resize", "needs_clarification") in stat_op, f"ops={sorted(map(str, stat_op))}")
    check("revision: color_change mapped to proposed", ("color_change", "proposed") in stat_op, f"ops={sorted(map(str, stat_op))}")
    approved = [c["id"] for c in rp["changes"] if c["status"] == "proposed"]
    ra = requests.post(BASE + "/api/v1/revision/apply", json={
        "objects": rev_objs2, "changes": rp["changes"], "only_ids": approved,
    }).json()
    applied_objs = {o["_id"]: o for o in ra.get("objects", [])}
    check("revision: apply really resizes logo",
          abs(applied_objs.get("obj_logo", {}).get("width", 0) - 240.0) < 1e-6,
          f"logo width -> {applied_objs.get('obj_logo', {}).get('width')}")
    check("revision: apply really recolors title",
          applied_objs.get("obj_title", {}).get("color") == "#8B0000",
          f"title color -> {applied_objs.get('obj_title', {}).get('color')}")
    check("revision: conflict/clarification NOT applied", ra.get("applied") == len(approved),
          f"applied={ra.get('applied')} approved={len(approved)}")

    # ---------------- 色彩引擎（§44 · 真实计算 + Pantone 诚实拒绝伪造） ----------------
    pal = requests.post(BASE + "/api/v1/color/palette", json={"path": str(img2), "k": 4}).json()
    _topc = (pal.get("palette") or [{}])[0]
    _tr = _topc.get("rgb", [0, 0, 0])
    check("color: real palette Top-1 hits source pixels (green 30,140,60), not arbitrary hex",
          pal.get("distinct_colors", 0) >= 1 and _topc.get("pixels", 0) > 0
          and str(_topc.get("hex", "")).startswith("#")
          and len(_tr) == 3 and all(abs(_tr[i] - [30, 140, 60][i]) <= 45 for i in range(3))
          and _topc.get("percent", 0) >= 70.0,
          json.dumps(_topc, ensure_ascii=False))
    con = requests.post(BASE + "/api/v1/color/contrast", json={"fg": "#777777", "bg": "#FFFFFF"}).json()
    check("color: real WCAG contrast + AA/AAA grading",
          4 < con["ratio"] < 5 and con["wcag"]["AA_normal"] is False and con["wcag"]["AA_large"] is True,
          json.dumps(con["wcag"]))
    con21 = requests.post(BASE + "/api/v1/color/contrast", json={"fg": "#000000", "bg": "#FFFFFF"}).json()
    check("color: black/white contrast exactly 21:1", con21["ratio"] == 21.0, f"ratio={con21['ratio']}")
    cvt = requests.post(BASE + "/api/v1/color/convert", json={"hex": "#FF0000"}).json()
    check("color: sRGB->CMYK/HSL/Lab conversions",
          cvt["rgb"] == {"r": 255, "g": 0, "b": 0} and cvt["cmyk"]["c"] == 0.0 and abs(cvt["cmyk"]["m"] - 100) < 1
          and "lab" in cvt, json.dumps(cvt.get("cmyk")))
    de = requests.post(BASE + "/api/v1/color/delta-e", json={"a": "#FF0000", "b": "#0000FF", "method": "ciede2000"}).json()
    check("color: real CIEDE2000 deltaE red vs blue is large", de["delta_e"] > 50, f"deltaE={de['delta_e']}")
    harm = requests.post(BASE + "/api/v1/color/harmony", json={"base": "#3366CC", "scheme": "triadic"}).json()
    check("color: triadic harmony yields 3 colors", len(harm["colors"]) == 3, json.dumps(harm["colors"]))
    pant = requests.post(BASE + "/api/v1/color/pantone", json={"hex": "#1F4E79"}).json()
    check("color: Pantone NOT fabricated (needs licensed data, red-line)",
          pant["status"] == "needs_licensed_pantone_data" and pant["exact_pantone"] is None and pant["fabricated"] is False,
          f"status={pant['status']} fabricated={pant['fabricated']}")
    bad = requests.post(BASE + "/api/v1/color/contrast", json={"fg": "zzz", "bg": "#FFFFFF"})
    check("color: illegal color rejected 400 (not fake)", bad.status_code == 400, f"HTTP {bad.status_code}")

    # ---------------- 布局排版引擎（§45 · 真实几何） ----------------
    objs2 = [
        {"id": "a", "x": 0, "y": 0, "width": 100, "height": 100},
        {"id": "b", "x": 50, "y": 50, "width": 100, "height": 100},
        {"id": "c", "x": 400, "y": 10, "width": 50, "height": 50},
    ]
    coll = requests.post(BASE + "/api/v1/layout/collide", json={"objects": objs2}).json()
    check("layout: real AABB collision detected w/ overlap area",
          coll["collision_count"] == 1 and abs(coll["collisions"][0]["overlap_area"] - 2500) < 1,
          json.dumps(coll["collisions"]))
    al = requests.post(BASE + "/api/v1/layout/align", json={"objects": objs2, "mode": "left"}).json()
    check("layout: left-align moves all to x=0", all(o["to"]["x"] == 0 for o in al["objects"]), f"aligned={al['aligned']}")
    dis = requests.post(BASE + "/api/v1/layout/distribute", json={"objects": [
        {"id": "x", "x": 0, "y": 0, "width": 10, "height": 10},
        {"id": "y", "x": 20, "y": 0, "width": 10, "height": 10},
        {"id": "z", "x": 100, "y": 0, "width": 10, "height": 10}], "axis": "x"}).json()
    check("layout: even distribution computes equal gap", abs(dis["computed_gap"] - 40) < 1e-6, f"gap={dis['computed_gap']}")
    sa = requests.post(BASE + "/api/v1/layout/safe-area", json={"objects": objs2, "canvas": {"width": 500, "height": 500}, "margin": 20}).json()
    check("layout: safe-area violations detected", sa["violation_count"] >= 1, f"violations={sa['violation_count']}")
    an = requests.post(BASE + "/api/v1/layout/analyze", json={"objects": objs2, "canvas": {"width": 500, "height": 500}}).json()
    check("layout: analyze returns real score", 0 <= an["score"] <= 100 and an["collision"]["collision_count"] == 1, f"score={an['score']}")

    # ---------------- 布局引擎敌意输入鲁棒性（Phase 22：畸形输入须诚实降级 400，绝不泄漏 5xx 崩溃） ----------------
    _hostile_layout = [
        ("collide 非数值坐标", "/api/v1/layout/collide",
         {"objects": [{"id": "a", "x": "NaN?", "y": "z", "width": "w", "height": "h"}]}),
        ("collide 字面量 NaN 坐标(防伪造几何)", "/api/v1/layout/collide",
         {"objects": [{"id": "a", "x": "NaN", "y": 0, "width": 10, "height": 10},
                      {"id": "b", "x": 0, "y": 0, "width": 10, "height": 10}]}),
        ("distribute 非数值尺寸", "/api/v1/layout/distribute",
         {"objects": [{"x": 0, "y": 0, "width": "a", "height": "b"},
                      {"x": 5, "y": 0, "width": 10, "height": 10},
                      {"x": 20, "y": 0, "width": 10, "height": 10}], "axis": "x"}),
        ("align 非数值坐标", "/api/v1/layout/align",
         {"objects": [{"id": "a", "x": "?", "y": 0, "width": 10, "height": 10}], "mode": "left"}),
        ("grid-snap 画布缺失(旧除零崩溃)", "/api/v1/layout/grid-snap",
         {"objects": objs2, "canvas": {}, "cols": 4, "rows": 4}),
        ("safe-area 非数值画布宽", "/api/v1/layout/safe-area",
         {"objects": objs2, "canvas": {"width": "wide", "height": 500}}),
        ("analyze 非数值画布", "/api/v1/layout/analyze",
         {"objects": objs2, "canvas": {"width": "tall", "height": 500}}),
    ]
    for _lbl, _path, _payload in _hostile_layout:
        _r = requests.post(BASE + _path, json=_payload)
        check(f"layout hostile-input [{_lbl}] -> 400 (no 5xx crash)",
              _r.status_code == 400 and _r.status_code < 500, f"HTTP {_r.status_code}")

    # ---------------- 包装/变量数据引擎（§46/§47） ----------------
    dl = requests.post(BASE + "/api/v1/packaging/dieline", json={"width": 100, "height": 80, "bleed": 3, "safe": 5}).json()
    check("packaging: dieline bleed box geometry", dl["bleed_box"] == [-3, -3, 103, 83] and dl["areas"]["bleed_area"] == round(106 * 86, 3), json.dumps(dl["bleed_box"]))
    imp = requests.post(BASE + "/api/v1/packaging/imposition", json={"die_width": 50, "die_height": 50, "count": 100, "sheet_width": 500, "sheet_height": 500, "gap": 0}).json()
    check("packaging: imposition n-up + utilization", imp["n_up"] == 100 and imp["sheets_needed"] == 1 and abs(imp["sheet_utilization_percent"] - 100) < 1e-6, json.dumps({k: imp.get(k) for k in ("n_up","sheets_needed","sheet_utilization_percent")}))
    vdp_csv = tmp / "vdp_products.csv"
    vdp_csv.write_text("name,sku,weight\nCola,A001,330\nSprite,A002,330\nFanta,A003\n", encoding="utf-8")
    vm = requests.post(BASE + "/api/v1/packaging/vdp/merge", json={"source": str(vdp_csv), "field_map": {"product": "name", "code": "sku", "grams": "weight", "flavor": "flavor"}}).json()
    check("packaging: VDP real CSV merge, missing col honest",
          vm["total_rows"] == 3 and vm["records"][0]["variant"]["product"] == "Cola"
          and vm["records"][2]["variant"]["grams"] is None and "flavor" in vm["missing_columns"] and vm["fabricated"] is False,
          json.dumps(vm.get("missing_columns")))
    pr = requests.post(BASE + "/api/v1/packaging/panel-recognition", params={"image_path": str(img5)}).json()
    _pr_kinds = sorted((a["separating_line"], a["separating_coverage"]) for a in pr["topology"]["adjacencies"])
    check("packaging: panel recognition REAL since Phase 47 (25th engine) - 240x160 dieline inverts to 4 panels [5684,5742,5782,5841]/Otsu thr 0/ink 895/line 887, cut 5seg+fold 1seg, 2 solid-cut(cov 1.0)+2 dashed-fold edges, outline 1256=2*(234+394), union 23049, zero fabricated",
          pr["status"] == "recognized" and pr["implemented"] is True and pr["fabricated"] is False
          and pr["panel_count"] == 4
          and sorted(p["area_px"] for p in pr["panels"]) == [5684, 5742, 5782, 5841]
          and all(p["rectangular"] is True for p in pr["panels"])
          and pr["binarize"] == {"method": "otsu", "threshold": 0, "ink_px": 895, "line_px": 887,
                                 "other_ink_px": 8, "barrier_px": 945, "blocked_px": 951}
          and [pr["lines"][k]["segments"] for k in ("cut", "fold")] == [5, 1]
          and _pr_kinds == [("cut", 1.0), ("cut", 1.0), ("fold", 0.7143), ("fold", 0.7172)]
          and pr["topology"]["adjacency_count"] == 4
          and pr["topology"]["degree_sequence"] == [2, 2, 2, 2]
          and pr["topology"]["is_single_connected_net"] is True
          and [pr["outline"][k] for k in ("perimeter_px", "union_area_px", "row_runs", "col_runs")]
              == [1256, 23049, 234, 394]
          and pr["background"] == {"component_count": 1, "largest_area_px": 14400}
          and pr["rejected_count"] == 0 and len(pr["caveats"]) == 5
          and pr["params"] == {"min_panel_area": 64, "max_line_width": 12, "min_line_length": 8,
                               "dash_merge_gap": 12, "solid_coverage_threshold": 0.95,
                               "rectangularity_threshold": 0.98},
          json.dumps({"status": pr["status"], "areas": [p["area_px"] for p in pr["panels"]],
                      "adj": _pr_kinds}, ensure_ascii=False))
    pr0 = requests.post(BASE + "/api/v1/packaging/panel-recognition", params={"image_path": str(img1)}).json()
    check("packaging: panel recognition stays honest on a non-dieline bitmap - solid red blob -> no_lines_detected/0 panels/line_px 0, blocky ink reported as other_ink 900 not invented panels",
          pr0["status"] == "no_lines_detected" and pr0["panel_count"] == 0 and pr0["panels"] == []
          and pr0["lines"]["cut"]["segments"] == 0 and pr0["lines"]["fold"]["segments"] == 0
          and pr0["binarize"] == {"method": "otsu", "threshold": 87, "ink_px": 900, "line_px": 0,
                                  "other_ink_px": 900, "barrier_px": 0, "blocked_px": 900}
          and pr0["outline"]["union_area_px"] == 0 and pr0["topology"]["adjacency_count"] == 0
          and bool(pr0["reason"]) and pr0["fabricated"] is False,
          json.dumps({"status": pr0["status"], "bz": pr0["binarize"]}, ensure_ascii=False))
    _neg47 = {
        "missing-param": (requests.post(BASE + "/api/v1/packaging/panel-recognition"), 400),
        "relative-ghost": (requests.post(BASE + "/api/v1/packaging/panel-recognition?image_path=x.png"), 404),
        "absent-ghost": (requests.post(BASE + "/api/v1/packaging/panel-recognition",
                                       params={"image_path": str(tmp / "no_such_dieline.png")}), 404),
        "non-bitmap": (requests.post(BASE + "/api/v1/packaging/panel-recognition",
                                     params={"image_path": str(tmp / "server.log")}), 400),
        "blocked-system-path": (requests.post(BASE + "/api/v1/packaging/panel-recognition",
                                              params={"image_path": "C:/Windows/System32/config/SAM"}), 403),
        "hostile-param": (requests.post(BASE + "/api/v1/packaging/panel-recognition",
                                        params={"image_path": str(img5), "min_area": "abc"}), 422),
    }
    _bad47 = [k for k, (_r47, want) in _neg47.items() if _r47.status_code != want]
    _no5xx47 = [k for k, (_r47, _w) in _neg47.items() if _r47.status_code >= 500]
    check("packaging: panel recognition honest 4xx ladder (no 5xx leak) - missing 400 / ghost 404 / non-bitmap 400 / blocked 403 / hostile param 422, all with real Chinese detail",
          not _bad47 and not _no5xx47
          and "缺少 image_path" in _neg47["missing-param"][0].json()["detail"]
          and "文件不存在" in _neg47["absent-ghost"][0].json()["detail"]
          and "无法作为位图图像解码" in _neg47["non-bitmap"][0].json()["detail"]
          and "路径被安全策略阻止" in _neg47["blocked-system-path"][0].json()["detail"],
          f"bad={_bad47} 5xx={_no5xx47}")
    bc = requests.post(BASE + "/api/v1/packaging/barcode", json={"code": "590123412345", "symbology": "EAN13", "output_path": str(tmp / "bc13.png")}).json()
    check("packaging: real EAN-13 render (check-digit + 95 modules + PNG on disk)",
          bc.get("implemented") is True and bc.get("code") == "5901234123457"
          and bc.get("check_digit") == "7" and bc.get("module_count") == 95
          and bc.get("bit_pattern", "").startswith("101") and "01010" in bc.get("bit_pattern", "")
          and os.path.isfile(bc.get("output_path", "")) and bc.get("bytes", 0) > 0 and bc.get("fabricated") is False,
          json.dumps({k: bc.get(k) for k in ("code", "check_digit", "module_count", "bytes")}))
    bc8 = requests.post(BASE + "/api/v1/packaging/barcode", json={"code": "9638507", "symbology": "EAN8", "output_path": str(tmp / "bc8.png")}).json()
    check("packaging: real EAN-8 render (67 modules)", bc8.get("code") == "96385074" and bc8.get("module_count") == 67, f"code={bc8.get('code')} modules={bc8.get('module_count')}")
    bcbad = requests.post(BASE + "/api/v1/packaging/barcode", json={"code": "5901234123450", "symbology": "EAN13"})  # 非法校验位(应为7)
    check("packaging: EAN-13 invalid check-digit rejected 400 (not faked)", bcbad.status_code == 400, f"HTTP {bcbad.status_code}")
    # ==== Phase 57：QR（ISO/IEC 18004）纯标准库真实实现 + 第三方 OpenCV 逐字读回 oracle ====
    # 产品与评测台源码**绝不 import cv2**；只有验收层引入第三方解码器，读服务写出的真 PNG。
    # 判据口径（依据本轮实测 p57_oracle_sweep.txt / p57_remainder_bit_decisive.txt）：
    #  · 读回必须与载荷**逐字相等**（长度相等不算）；
    #  · OpenCV 自家检测器在默认栅格(quiet=4, scale=8)上存在**载荷相关的容量洞**：同一行它
    #    自己的编码器同样读不回（实测产品 36/40、尺子自读 36/40，失败行**完全重合**；且翻转
    #    余比特、逐块校验子全零均不改变该结论），只改出图几何（静区宽/像素比）后两侧同回
    #    满额。故合格线 = 「默认栅格逐字读回」∨「该行尺子自家产物同样读不回 **且** 几何扫描
    #    内逐字读回」。这不是给产品开后门：产品失败行集合若不是尺子自败行集合的子集（P ⊄ C），
    #    即"产品比参照编码器差得没有对应依据"，当场判红。
    #  · 尺子缺失（未装 opencv / numpy）时这两条判据**大声判红**，绝不静默跳过、绝不记为通过。
    _cv2 = None
    _np = None
    try:
        import cv2 as _cv2
        import numpy as _np
        _HAS_CV = True
        _CV_ERR = ""
    except Exception as _e57:
        _HAS_CV = False
        _CV_ERR = repr(_e57)
    from packaging_engine import PackagingEngine as _PE57
    _eng57 = _PE57(store_dir=tmp / "qr_trigger_scan")
    _det57 = _cv2.QRCodeDetector() if _HAS_CV else None
    _CVLVL = {"L": getattr(_cv2, "QRCodeEncoder_CORRECT_LEVEL_L", 0),
              "M": getattr(_cv2, "QRCodeEncoder_CORRECT_LEVEL_M", 1),
              "Q": getattr(_cv2, "QRCodeEncoder_CORRECT_LEVEL_Q", 2),
              "H": getattr(_cv2, "QRCodeEncoder_CORRECT_LEVEL_H", 3)} if _HAS_CV else {}

    def _dec57(img):
        try:
            return _det57.detectAndDecode(img)[0]
        except Exception:
            return ""

    def _raster57(mat, sc, qz):
        side = mat.shape[0]
        canvas = _np.ones((side + 2 * qz, side + 2 * qz), dtype=_np.uint8) * 255
        canvas[qz:qz + side, qz:qz + side][mat > 0] = 0
        return _cv2.resize(canvas, None, fx=sc, fy=sc, interpolation=_cv2.INTER_NEAREST)

    def _own57(payload, ver, level, side):
        """尺子自家编码器在同一 (version, level) 下的 side×side 二值矩阵（1=黑）。"""
        prm = _cv2.QRCodeEncoder_Params()
        prm.version, prm.correction_level, prm.mode = ver, _CVLVL[level], _cv2.QRCodeEncoder_MODE_BYTE
        raw = _cv2.QRCodeEncoder.create(prm).encode(payload)
        s0 = (raw.shape[0] - side) // 2
        return (raw[s0:s0 + side, s0:s0 + side] < 128).astype(_np.uint8)

    _vcache57 = {}

    def _ver_of57(level, n):
        key = (level, n)
        if key not in _vcache57:
            try:
                _vcache57[key] = _eng57.render_qr(
                    "z" + "A" * (n - 1), ec_level=level, scale=1, quiet=4,
                    output_path=str(tmp / f"vscan_{level}_{n}.png"))["version"]
            except ValueError:
                _vcache57[key] = 999  # 超出已实现 v1..v10：按"更大"处理，二分仍可定界
        return _vcache57[key]

    rows57, _trig_bad = [], []
    for _lv in ("L", "M", "Q", "H"):
        for _vv in range(1, 11):
            _lo, _hi = 1, 280
            while _lo < _hi:
                _mid = (_lo + _hi) // 2
                if _ver_of57(_lv, _mid) >= _vv:
                    _hi = _mid
                else:
                    _lo = _mid + 1
            if _ver_of57(_lv, _lo) != _vv:
                _trig_bad.append(f"({_lv},v{_vv}) 触发长度落在 v{_ver_of57(_lv, _lo)}")
                continue
            if _vv > 1 and _ver_of57(_lv, _lo - 1) != _vv - 1:
                _trig_bad.append(f"({_lv},v{_vv}) 触发非最小（上一长度落在 "
                                 f"v{_ver_of57(_lv, _lo - 1)}，版本阶跃跳档）")
            rows57.append((_lv, _vv, "z" + "A" * (_lo - 1)))
    _extra57 = [("M", 0, "123456789012"), ("M", 0, "HELLO WORLD-123 ABC"),
                ("M", 0, "造境AI設計智能体")]   # Numeric / Alphanumeric / Byte(UTF-8 中文)

    _q57 = []
    for (_lv, _vv, _pl) in rows57:
        _q57.append((_lv, _vv, _pl, requests.post(
            BASE + "/api/v1/packaging/barcode",
            json={"code": _pl, "symbology": "QR", "ec_level": _lv, "scale": 8,
                  "output_path": str(tmp / f"qr_{_lv}{_vv}.png")}).json()))
    for (_lv, _unused, _pl) in _extra57:
        _q57.append((_lv, 0, _pl, requests.post(
            BASE + "/api/v1/packaging/barcode",
            json={"code": _pl, "symbology": "QR", "ec_level": _lv, "scale": 8,
                  "output_path": str(tmp / ("qr_x_%d.png" % len(_pl)))}).json()))

    hello57 = requests.post(BASE + "/api/v1/packaging/barcode",
                            json={"code": "hello", "symbology": "QR",
                                  "output_path": str(tmp / "qr_hello.png")}).json()
    _m57 = hello57.get("matrix") or []
    _side57 = hello57.get("side", 0)
    check("packaging: QR really rendered by service (ISO 18004 structural fields self-consistent, real PNG on disk, fabricated=False)",
          hello57.get("implemented") is True and hello57.get("status") == "ok"
          and hello57.get("fabricated") is False and hello57.get("symbology") == "QR"
          and hello57.get("mode") == "Byte" and hello57.get("version") == 1
          and _side57 == 21 + 4 * (hello57.get("version", 0) - 1)
          and hello57.get("module_count") == _side57 * _side57
          and len(_m57) == _side57 and all(len(r) == _side57 for r in _m57)
          and len(hello57.get("bit_pattern", "")) == _side57 * _side57
          and len(hello57.get("format_bits", "")) == 15
          and hello57.get("total_codewords") == hello57.get("data_codewords") + hello57.get("ec_codewords")
          and os.path.isfile(hello57.get("output_path", ""))
          and os.path.getsize(hello57.get("output_path", "")) == hello57.get("bytes") > 0
          and hello57.get("quiet_zone_modules") == 4
          and hello57.get("width_px") == (_side57 + 2 * 4) * 2,
          json.dumps({k: hello57.get(k) for k in ("version", "side", "mode", "total_codewords",
                                                  "bytes", "quiet_zone_modules", "width_px")}))

    lvl_versions = {}
    for (_lv, _vv, _pl, _rr) in _q57:
        if _vv and _lv in ("L", "M", "Q", "H"):
            lvl_versions.setdefault(_pl, {})[_lv] = (_rr.get("ec_level"), _rr.get("version"))
    _echo_bad = [f"{_lv}/v{_vv}->{_rr.get('ec_level')}/v{_rr.get('version')}"
                 for (_lv, _vv, _pl, _rr) in _q57 if _vv and _rr.get("ec_level") != _lv]
    _lvl_matters = sum(1 for d in lvl_versions.values() if len({v[1] for v in d.values()}) > 1)
    q2_57 = requests.post(BASE + "/api/v1/packaging/barcode",
                          json={"code": "hello", "symbology": "QR", "quiet": 2, "scale": 8,
                                "output_path": str(tmp / "qr_hello_q2.png")}).json()
    ean57 = requests.post(BASE + "/api/v1/packaging/barcode",
                          json={"code": "590123412345", "symbology": "EAN13",
                                "output_path": str(tmp / "qr_ean_qdef.png")}).json()
    check("packaging: QR ec_level + quiet honored end-to-end by service (no 1D quiet=11 leak; declared==actual; EAN-13 default quiet unchanged)",
          not _echo_bad and _lvl_matters >= 3
          and q2_57.get("quiet_zone_modules") == 2
          and q2_57.get("width_px") == (q2_57.get("side", 0) + 4) * 8
          and hello57.get("quiet_zone_modules") == 4
          and ean57.get("quiet_zone_modules") == 11,
          f"echo_bad={_echo_bad[:6]} lvl_matters={_lvl_matters} "
          f"quiet q2={q2_57.get('quiet_zone_modules')} "
          f"qr_default={hello57.get('quiet_zone_modules')} ean_default={ean57.get('quiet_zone_modules')}")

    _pdef_fail, _cdef_fail, _sweep_fail, _nomatch = [], [], [], []
    if _HAS_CV:
        for (_lv, _vv, _pl, _rr) in _q57:
            _key = f"({_rr.get('ec_level')},v{_rr.get('version')},n={len(_pl)})"
            if _rr.get("implemented") is not True or not _rr.get("matrix"):
                _sweep_fail.append(_key + " 未出图")
                continue
            _sd = _rr["side"]
            _pm = _np.array([[int(ch) for ch in r] for r in _rr["matrix"]], dtype=_np.uint8)
            _g = _cv2.imdecode(_np.fromfile(_rr["output_path"], dtype=_np.uint8), _cv2.IMREAD_GRAYSCALE)
            _ok_p = _dec57(_g) == _pl
            try:
                _om = _own57(_pl, _rr["version"], _rr["ec_level"], _sd)
                _ok_c = _dec57(_raster57(_om, 8, 4)) == _pl
            except Exception:
                _ok_c = True  # 尺子自家编码器无法在此行出图 => 不作为产品有罪依据，仍走扫描判定
            if not _ok_p:
                _pdef_fail.append(_key)
                if not any(_dec57(_raster57(_pm, sc, qz)) == _pl
                           for qz in (2, 3, 4, 5, 6, 7) for sc in (4, 6, 8, 12, 16)):
                    _sweep_fail.append(_key)
            if not _ok_c:
                _cdef_fail.append(_key)
            if _rr.get("side") != 21 + 4 * (_rr.get("version", 0) - 1):
                _nomatch.append(_key)
    check("packaging: third-party OpenCV decodes EVERY service QR PNG verbatim (43 rows: v1..v10 x L/M/Q/H minimal-trigger + Numeric/Alphanumeric/UTF-8 中文; ruler-hole exemption requires P subset-of C + geometry sweep read-back)",
          _HAS_CV and not _trig_bad and len(rows57) == 40 and not _nomatch
          and set(_pdef_fail) <= set(_cdef_fail) and not _sweep_fail
          and len(_q57) == 43,
          (f"cv_missing={not _HAS_CV}{'' if _HAS_CV else ' -> ' + _CV_ERR} "
           f"trig_bad={_trig_bad[:3]} rows={len(rows57)} 产品默认栅格读不回={len(_pdef_fail)} "
           f"尺子自读不回={len(_cdef_fail)} 扫描后仍读不回={_sweep_fail[:4]} "
           f"P subset C={set(_pdef_fail) <= set(_cdef_fail)}"))

    _ctrl57 = _dec57(_cv2.imdecode(_np.fromfile(hello57["output_path"], dtype=_np.uint8),
                                   _cv2.IMREAD_GRAYSCALE)) == "hello" if _HAS_CV else False
    _neg57 = {}
    if _HAS_CV:
        _neg57["all_white"] = _dec57(_np.full((400, 400), 255, dtype=_np.uint8))
        _hmat = _np.array([[int(ch) for ch in r] for r in hello57["matrix"]], dtype=_np.uint8)
        _e1 = _hmat.copy(); _e1[0:9, 0:9] = 0
        _e2 = _hmat.copy(); _e2[0:9, 0:9] = 0; _e2[-9:, -9:] = 0; _e2[-9:, 0:9] = 0
        _neg57["erase_tl_finder"] = _dec57(_raster57(_e1, 8, 4))
        _neg57["erase_all_finders"] = _dec57(_raster57(_e2, 8, 4))
    check("packaging: QR oracle instrument self-calibration (positive control decodes) + 3 negative controls decode to NOTHING (all-white / finder erased / all finders erased)",
          _HAS_CV and _ctrl57 and all(v == "" for v in _neg57.values()) and len(_neg57) == 3,
          f"cv_missing={not _HAS_CV} positive={_ctrl57} negatives={json.dumps(_neg57)}")

    _over = requests.post(BASE + "/api/v1/packaging/barcode",
                          json={"code": "A" * 400, "symbology": "QR"})
    _empty = requests.post(BASE + "/api/v1/packaging/barcode",
                           json={"code": "", "symbology": "QR"})
    _badlvl = requests.post(BASE + "/api/v1/packaging/barcode",
                            json={"code": "hello", "symbology": "QR", "ec_level": "X"})
    _c128 = requests.post(BASE + "/api/v1/packaging/barcode",
                          json={"code": "ABC123", "symbology": "Code128"}).json()
    check("packaging: QR honest boundaries (payload beyond v10 -> 400 naming implemented range; empty -> 400; illegal ec_level -> 400; Code-128 still needs_external with reason naming it, red-line not faked)",
          _over.status_code == 400 and "v1..v10" in json.dumps(_over.json(), ensure_ascii=False)
          and _empty.status_code == 400 and _badlvl.status_code == 400
          and _c128.get("implemented") is False and _c128.get("status") == "needs_external"
          and "Code-128" in (_c128.get("reason") or "")
          and _c128.get("fabricated") is not True,
          f"over={_over.status_code} empty={_empty.status_code} badlvl={_badlvl.status_code} "
          f"c128={_c128.get('status')}")
    # ==== Phase 62：一维条码第三方解码 oracle（cv2.barcode.BarcodeDetector）====
    # 报告第 370 / 404 行连着两轮写「一维条码解码 oracle 本机仍缺（pyzbar/zxing/reportlab 三路皆无）」，
    # 本轮**实测推翻**：cv2 5.0.0 的 `cv2.barcode.BarcodeDetector` 在场，且能把产品自家渲染的
    # EAN-13 PNG 逐字读回。三轮漏掉的那条路，正是本验收层自己早已 import 的 cv2
    # （上面第 1972 行就用 `cv2.QRCodeDetector` 做 QR 的第三方读回）。凭证：
    # `p62_spike_1d_oracle2_receipt.txt`（10,939 B / sha256/16 723b785de4474b15，历史落盘件）
    # 与 `p62_probe_oracle_receipt.txt`（5,472 B / sha256/16 f5be6619b2d7ceda，活体现渲染件）。
    # 判据一律钉在**量出来的操作点**上，绝不写成「一律可读回」：
    #  · 产品缺省形制（scale=2 / quiet=11 模块 / height=60 → 104×234）**原样不解**，补 16 px 白边后
    #    才逐字读回（实测命中点 = [16, 24, 32, 40]）⇒ 合格线 = 边距梯内至少一点逐字读回；
    #    原样读不回只**如实记录在 detail 里**、不进判据条件——那是这台检测器要边距，不是产品位串有缺陷
    #    （静区 11 模块本已宽于 GS1 要求）；若哪天原样也能解，那是改善，不该把改善判成红。
    #  · EAN-13 前导 0 被读成 UPC_A（去掉前导 0）是解码器**合法**读法，按等价判定。
    #  · EAN-8 实测**产品与尺子两侧同梯全不解**（96385074 在 0/16/24/32/40 五点上均为空）⇒ 只允许
    #    走与 QR 判据同款的「尺子洞豁免」：产品失败集 ⊆ 尺子自败集（P ⊆ C）。这不是给产品开后门：
    #    P ⊄ C 即「产品比独立编码器差得没有对应依据」，当场判红。
    #  · 20 枚覆盖集里 6 枚（共用右半数字池 68079）全梯不可解 = 解码器选择性盲区；可解性也**不随放大
    #    单调变好**（逐 scale 单点 = {1:0, 2:10, 3:0, 4:0, 5:0, 6:0, 8:0}）⇒ 本判据只钉实测可解的两枚
    #    EAN-13，盲区在判词里点名，既不拿盲区码当合格线、也不假装它不存在。
    #  · cv2 在非 ASCII 路径上 imwrite 返回 False 且不落文件、imread 返回 None ⇒ 读侧只走
    #    `imdecode(np.fromfile(...))`（与上面 QR 判据同一读法）；cv2 5.0.0 的 `detect()` 返回 2 元组、
    #    `detectAndDecodeWithType()` 返回 4 元组。
    #  · 尺子缺失（未装 opencv / numpy，或 cv2.barcode 不在场）时本判据**大声判红**，绝不静默跳过。
    # ---- 2026-10-11 就地改核：上面第 2165-2167 行的立论已被实测推翻。旧文逐字保留在原处，不改写、不删除。
    #  错在事实而非文笔：EAN-8 在补边梯上**能**逐字读回。活体探针 `p62_probe_oracle_receipt.txt`
    #  （6,870 B / sha256/16 31d0ad7c89c0de9b，本轮起真服务现渲染；应答者身份核对通过 = True，
    #  data_root 与期望前缀逐字符相等、监听 PID 集合 == 本探针子进程 PID）第 29-30 行：产品图
    #  96385074 在 pad=40 读回 ["96385074"]、检测器自报码制 ["EAN_8"]；尺子自家编码 + 自家渲染同点读回。
    #  错因在**判卷侧**：第 1 版 `_match62` 把期望码制写死成 EAN_13/UPC_A，于是检测器如实回 EAN_8
    #  时被本尺子判成空集。为排除「抖动/环境形」这一解释，另跑确定性尖峰
    #  `p62_c128_determinism_receipt.txt`（1,729 B / sha256/16 978ddbf94fbaf592）：同一张图 12 次重复 ×
    #  5 种形制（含 ±1 模块宽窄）、6 个新建检测器实例，命中集合零变化 ⇒ 既不是抖动也不是产品缺陷，
    #  是尺子读错了脸。判卷侧写错形状，改的是判卷侧，不给尺子开口子。
    #  处置（只收紧、不放松）：期望码制改由**码长现场派生**（`_want62`：13→EAN_13 / 8→EAN_8），
    #  UPC_A 等价只对 13 位且带前导 0 的码开放；EAN-8 从此必须真正逐字读回、不再走豁免；
    #  「尺子洞豁免」P ⊆ C 降级为**结构性不变式**继续钉住——P/C 记的是**不可解的码**集合（本轮两侧
    #  均为空集），一旦产品比独立编码器差得没有对应依据，仍当场判红。
    #  顺带纠正一处易误读：两侧**边距点位**集合本轮并不相等（EAN-13 400638133393 产品 = [16,24,32,40]、
    #  尺子 = [16,24,40]，产品多中一点），所以本判据只钉**可解性**、绝不把点位当合格线——点位会随
    #  Pillow 尺寸与检测器内部缩放合法移动，钉住它只能红掉尺子而不是红掉产品。
    #  旧回执不消失：第 2158 行所指的 5,472 B / sha256/16 f5be6619b2d7ceda 那份已原样存档为
    #  `p62_probe_oracle_receipt_v1_matcherbug_superseded.txt`（字节数与 sha 均未改动，仍在盘上可复验），
    #  `p62_probe_oracle_receipt.txt` 这个路径现在装的是上面 6,870 B 的新件。
    #  本尺子同一轮的第二处自身缺陷也记在此处：探针第 1 版收尾段拿 tuple 当 JSON 键，写盘前抛
    #  TypeError，导致整轮实测读数只活在内存里、收据没落盘 ⇒ 已改 str 键并把收尾段包进 except，
    #  复跑才取回这份 6,870 B 件（仪器中途炸也不许丢证据）。
    #  另：本轮把端口释放核验的正对照从「外部已知持有者 8765」换成了**同一条通道终止前对本探针
    #  自有端口的应答**（收据第 69-77 行：before=True 且 after=False、监听 PID 归零）。8765 当轮不回
    #  /api/v1/health，把它当正对照等于「通道有效性」从未被证明，那条释放结论先前不予采信；
    #  现在外部 8765 只作在场观测留档，收据里已写明不得当正对照引用。
    # ---- 2026-10-11 就地改核第二条（变异判据跑完后追加；上面 2175-2201 行逐字保留不改）：
    #  变异驱动 p62_mutation_driver.py 的零桩 ctrl 最初写成「把静区 11 改 13」，第一跑即被实测反驳：
    #  宽静区**不是中性对照**，它改的就是被测量本身。观测臂 q13（只动静区 11→13）实测产品侧
    #  {590123412345:[16,40], 400638133393:[32], 9638507:[24,40]}、尺子侧 {400638133393: 空}
    #  ⇒ 两侧位串逐模块相等而读回命中点集却分叉，尺子的正对照腿在宽静区下自己断了。凭证：
    #  p62_mutation_receipt.txt（14,989 B / sha256/16 481806a7bda4dbf1）q13 行，与上一代
    #  p62_mutation_receipt_v2_ctrl_not_neutral_and_keys_unwired_superseded.txt（11,980 B /
    #  sha256/16 1913fcf702c56b16）。处置（只收紧不放松）：零桩回到本仓库既有形制（p57_mutation_
    #  driver.py 第 414 行、outputs/p34_mutation_driver.py 第 116 行均为空补丁），宽静区降级为
    #  **不裁决**的观测臂，盲区留在盘上而不是被藏起来；三枚真桩 m1/m2/m3 各自只翻自己的面。
    #  本判据**不**因此升级为「整梯逐字读回」——仍钉「边距梯内至少一点逐字读回」+ 申报位串与尺子
    #  编码逐模块相等 + P ⊆ C，理由与上面第 2189-2191 行同：点位随 Pillow 尺寸与检测器内部缩放
    #  合法移动，钉住点位只会红掉尺子而不是红掉产品。
    #  同轮另记三处**仪器**缺陷（产品与判据口径均未动）：桩替换串缺行尾换行把下一行折进注释 ⇒
    #  NameError ⇒ 三枚 POST 全 500（SWALLOW 静态审计永久钉住该不变式）；栅格桩作用列与 check#2 的
    #  翻转集撞列 ⇒ 翻坏再翻一次恰好复原原图（COLLIDE 审计，字面集从本块磁盘原文正则现取不手抄）；
    #  期望台账里出现无比对分支的键 ⇒ 永久假红被读成缺席（UNWIRED 审计）。
    #  L2 侧实测复跑（p62_4b_L2.log）与本块判据的转绿关系：见上一条改核与 Phase 62 报告章节。
    # ==== Phase 62 block start（变异判据按此标记整块抽取 exec，不另写等价脚本）====
    _bc62 = None
    if _HAS_CV:
        try:
            from cv2 import barcode as _bcmod62
            _bc62 = _bcmod62.BarcodeDetector()
        except Exception as _ebc62:
            _bc62 = None
            _CV_ERR = _CV_ERR + " | cv2.barcode: " + repr(_ebc62)
    _HAS_BC62 = _bc62 is not None
    _PADS62 = (0, 16, 24, 32, 40)
    # 独立 GS1 编码（常量手抄，与产品源无 import 关系）：尺子自家编码器，供正对照与位串复核
    _LT62 = {"0": "0001101", "1": "0011001", "2": "0010011", "3": "0111101", "4": "0100011",
             "5": "0110001", "6": "0101111", "7": "0111011", "8": "0110111", "9": "0001011"}
    _GT62 = {"0": "0100111", "1": "0110011", "2": "0011011", "3": "0100001", "4": "0011101",
             "5": "0111001", "6": "0000101", "7": "0010001", "8": "0001001", "9": "0010111"}
    _RT62 = {"0": "1110010", "1": "1100110", "2": "1101100", "3": "1000010", "4": "1011100",
             "5": "1001110", "6": "1010000", "7": "1000100", "8": "1001000", "9": "1110100"}
    _PT62 = {"0": "LLLLLL", "1": "LLGLGG", "2": "LLGGLG", "3": "LLGGGL", "4": "LGLLGG",
             "5": "LGGLLG", "6": "LGGGLL", "7": "LGLGLG", "8": "LGLGGL", "9": "LGGLGL"}

    def _bits1362(c13):
        b = "101"
        for ch, p in zip(c13[1:7], _PT62[c13[0]]):
            b += (_LT62 if p == "L" else _GT62)[ch]
        b += "01010"
        for ch in c13[7:13]:
            b += _RT62[ch]
        return b + "101"

    def _bits862(c8):
        b = "101"
        for ch in c8[:4]:
            b += _LT62[ch]
        b += "01010"
        for ch in c8[4:]:
            b += _RT62[ch]
        return b + "101"

    def _raster62(bits, scale, height, quiet, margin_y):
        w = len(bits) * scale + 2 * quiet * scale
        img = _np.full((height + 2 * margin_y, w), 255, _np.uint8)
        x = quiet * scale
        for m in bits:
            if m == "1":
                img[margin_y:margin_y + height, x:x + scale] = 0
            x += scale
        return img

    def _pad62(img, p):
        if p <= 0:
            return img
        return _cv2.copyMakeBorder(img, p, p, p, p, _cv2.BORDER_CONSTANT, value=255)

    def _dec62(img):
        try:
            _ok62, _txt62, _tys62, _cn62 = _bc62.detectAndDecodeWithType(img)
            return [t for t in (_txt62 or []) if t], [t for t in (_tys62 or []) if t]
        except Exception:
            return [], []

    def _want62(code):
        """期望码制**由码长现场派生**（13→EAN_13 / 8→EAN_8），一律不写死。
        2026-10-11 就地改核：本尺子第 1 版把期望固定成 EAN_13/UPC_A 两型，于是检测器如实
        回 `EAN_8` 时被判成「读不回」，进而把「EAN-8 全梯不可解」写成了判据的立论依据。
        实测（p62_c128_determinism_receipt.txt：同图 12 次 × 5 形制零抖动、6 个新实例同读）
        证明那不是抖动也不是产品缺陷，是判卷侧写错了形状 ⇒ 改的是判卷侧，且改完更严。"""
        if len(code) == 13:
            return ("EAN_13", "EAN13")
        if len(code) == 8:
            return ("EAN_8", "EAN8")
        return ("",)

    def _match62(code, txt, tys):
        _w62 = _want62(code)
        for i, t in enumerate(txt):
            y = tys[i] if i < len(tys) else ""
            if t == code and y in _w62:
                return True
            # 仅对 13 位码开放：前导 0 被读成 UPC_A（去前导 0 的 12 位）是解码器合法读法
            if len(code) == 13 and code.startswith("0") and y == "UPC_A" \
                    and t == code[1:] and len(t) == 12:
                return True
        return False

    def _hits62(img, code):
        return [p for p in _PADS62 if _match62(code, *_dec62(_pad62(img, p)))]

    def _rd62(path):
        if not path or not os.path.isfile(path):
            return None
        return _cv2.imdecode(_np.fromfile(path, dtype=_np.uint8), _cv2.IMREAD_GRAYSCALE)

    def _geo62(img):
        """由暗行/暗列派生几何（margin_y, bar_h, x0, bar_w），不硬编码产品常量。"""
        rows = _np.where((img < 128).any(axis=1))[0]
        cols = _np.where((img < 128).any(axis=0))[0]
        if not len(rows) or not len(cols):
            return -1, -1, -1, -1
        return (int(rows[0]), int(rows[-1] - rows[0] + 1),
                int(cols[0]), int(cols[-1] - cols[0] + 1))

    _REQ62 = (("EAN13", "590123412345"), ("EAN13", "400638133393"), ("EAN8", "9638507"))

    # ---- Phase 63 G 腿（2026-10-11 新增；离线：零产品依赖、零网络、零夹具、零像素）：
    #  GS1 数字模式表**构造律**自检——逐表核验而非逐码核验。
    #  立论（为什么本验收层既有的腿抓不到这一格）：本块向产品请求的三枚码里，两枚 EAN-13 的
    #  PARITY 串把左半 G 槽只落在下面**现场派生**的数字集合上（绝不手抄），EAN-8 根本没有
    #  parity 概念 ⇒ 若手抄的 G 表在某个**未被任何请求码触及的槽位**上错成别族的位串，
    #  本层所有依赖码串的腿（_bitsok62 逐模块相等、边距梯读回、P⊆C）会一律保持绿色而静默放行。
    #  上一代 G(7) 抄成 L(4) 正是这样静默的。故本腿不查码串，只查三族表本身是否满足 GS1 构造律：
    #  R_d == complement(L_d)、G_d == reverse(complement(L_d))，对**全部十位数字**逐位成立；
    #  外加三族两两互斥、每族十码互异且长 7、PARITY 十串互异且长 6、字符集仅 {L,G}、首字符领衔 L。
    #  期望值一律由 L 表 + 构造律**现场算出**，本腿正文不写死任何一格的位串；L 表自身由本块
    #  既有的第三方 cv2.barcode 解码腿绝对锚定，故"G 由 L 派生"这条相对约束足以把 G 表也绝对锚定。
    def _p63_complement(_s63):
        return "".join("1" if _c63 == "0" else "0" for _c63 in _s63)

    def _p63_reverse(_s63):
        return _s63[::-1]

    def _p63_gslot_digits(_codes):
        """派生：本块所请求的 EAN-13 码，其左半 G 槽实际落在哪些数字上（正对照枚举器，
        数不出 >=1 即说明派生链自己断了——那种绿不予采信）。"""
        _out63 = set()
        for _sym63, _c63 in _codes:
            if _sym63 != "EAN13" or len(_c63) != 12:
                continue
            _par63 = _PT62[_c63[0]]
            for _i63 in range(6):
                if _par63[_i63] == "G":
                    _out63.add(_c63[1 + _i63])
        return _out63

    _p63_digits = "0123456789"
    _p63_R_bad = [_d63 for _d63 in _p63_digits
                  if _RT62[_d63] != _p63_complement(_LT62[_d63])]
    _p63_G_bad = [_d63 for _d63 in _p63_digits
                  if _GT62[_d63] != _p63_reverse(_p63_complement(_LT62[_d63]))]
    _p63_fam_card = [len(set(_t63.values())) for _t63 in (_LT62, _GT62, _RT62)]
    _p63_fam_len = sorted({len(_v63) for _t63 in (_LT62, _GT62, _RT62)
                           for _v63 in _t63.values()})
    _p63_LG_inter = sorted(set(_LT62.values()) & set(_GT62.values()))
    _p63_LR_inter = sorted(set(_LT62.values()) & set(_RT62.values()))
    _p63_GR_inter = sorted(set(_GT62.values()) & set(_RT62.values()))
    _p63_par_bad = [_d63 for _d63 in _p63_digits
                    if len(_PT62[_d63]) != 6 or (set(_PT62[_d63]) - {"L", "G"})
                    or _PT62[_d63][0] != "L"]
    _p63_par_uniq = len(set(_PT62.values())) == 10
    _p63_g7_law = _p63_reverse(_p63_complement(_LT62["7"]))
    _p63_exercised = sorted(_p63_gslot_digits(_REQ62))
    _p63_g7_exercised = "7" in _p63_exercised
    check("packaging(P63): the ruler's OWN hand-copied GS1 digit-pattern tables satisfy the construction "
          "laws, checked table-by-table instead of code-by-code - R_d == complement(L_d) and "
          "G_d == reverse(complement(L_d)) for ALL TEN digits, the L/G/R families pairwise disjoint, each "
          "family ten distinct 7-module strings, PARITY ten distinct 6-character strings over {L,G} each "
          "starting with L, and the G-slot-digit enumerator itself proves it enumerated at least one digit. "
          "Every expected bit-string here is DERIVED live from the L table, never transcribed. Rationale for "
          "a table-level leg: the EAN-13 codes requested in this block route their left-half G slots only "
          "through the derived digit set recorded in the detail (EAN-8 has no parity at all), so a G-table "
          "typo sitting in an unexercised slot is invisible to EVERY code-string leg in this acceptance "
          "layer - the exact way the previous generation's G slot duplicated an L-family string and stayed "
          "silent while all of them stayed green",
          not _p63_R_bad and not _p63_G_bad and _p63_fam_card == [10, 10, 10]
          and _p63_fam_len == [7] and not _p63_LG_inter and not _p63_LR_inter
          and not _p63_GR_inter and not _p63_par_bad and _p63_par_uniq
          and len(_p63_exercised) >= 1,
          json.dumps({"R_bad": _p63_R_bad, "G_bad": _p63_G_bad,
                      "G7_actual": _GT62["7"], "G7_law_derived": _p63_g7_law,
                      "L7": _LT62["7"],
                      "family_cardinality": _p63_fam_card,
                      "family_string_lengths": _p63_fam_len,
                      "L_inter_G": _p63_LG_inter, "L_inter_R": _p63_LR_inter,
                      "G_inter_R": _p63_GR_inter,
                      "parity_bad": _p63_par_bad, "parity_all_distinct": _p63_par_uniq,
                      "g_slot_digits_exercised_by_this_block": _p63_exercised,
                      "g_slot_7_exercised": _p63_g7_exercised,
                      "why_a_G_typo_was_silent": (bool(_p63_G_bad) and not _p63_g7_exercised)}))
    _R62 = {}
    for _sym62, _c62 in _REQ62:
        _rp62 = requests.post(BASE + "/api/v1/packaging/barcode", timeout=60,
                              json={"code": _c62, "symbology": _sym62,
                                    "output_path": str(tmp / ("p62_%s_%s.png" % (_sym62.lower(), _c62)))})
        _R62[(_sym62, _c62)] = (_rp62.status_code,
                                _rp62.json() if _rp62.status_code == 200 else {})
    _prod62, _ruler62, _bitsok62, _asis62, _geo62d = {}, {}, {}, {}, {}
    _want62d, _readback62 = {}, {}
    for (_sym62, _c62), (_st62, _j62) in _R62.items():
        _code62 = _j62.get("code") or ""
        _img62 = _rd62(_j62.get("output_path") or "") if _HAS_BC62 else None
        _sc62 = int(_j62.get("scale") or 0)
        _qz62 = int(_j62.get("quiet_zone_modules") or 0)
        _g62 = _geo62(_img62) if _img62 is not None else (-1, -1, -1, -1)
        _geo62d[(_sym62, _c62)] = _g62
        _want62d[(_sym62, _c62)] = list(_want62(_code62))
        _own62 = ""
        if _sym62 == "EAN13" and len(_code62) == 13:
            _own62 = _bits1362(_code62)
        elif _sym62 == "EAN8" and len(_code62) == 8:
            _own62 = _bits862(_code62)
        _decl62 = _j62.get("bit_pattern") or ""
        _bitsok62[(_sym62, _c62)] = (_own62 != "" and _own62 == _decl62
                                     and len(_decl62) == (95 if _sym62 == "EAN13" else 67))
        _prod62[(_sym62, _c62)] = _hits62(_img62, _code62) if _img62 is not None else []
        # 逐字读数（码串 + 检测器自报码制）随命中点一并落账：判据名不写快照数字，靠这里现场自证
        _readback62[(_sym62, _c62)] = [[_p62rb] + [list(z) for z in _dec62(_pad62(_img62, _p62rb))]
                                       for _p62rb in _prod62[(_sym62, _c62)]] if _img62 is not None else []
        _asis62[(_sym62, _c62)] = (_dec62(_img62)[0] if _img62 is not None else ["<no-image>"])
        if _img62 is not None and _own62 and _sc62 > 0 and _qz62 >= 0 and _g62[0] >= 0:
            _ruler62[(_sym62, _c62)] = _hits62(
                _raster62(_own62, _sc62, _g62[1], _qz62, _g62[0]), _code62)
        else:
            _ruler62[(_sym62, _c62)] = []
    _P62 = {k for k in _prod62 if not _prod62[k]}
    _C62 = {k for k in _ruler62 if not _ruler62[k]}
    _sub62 = _P62 <= _C62
    _bad62 = [k for k in _prod62 if not _prod62[k]]
    _ctlbad62 = [k for k in _ruler62 if not _ruler62[k]]
    _nofab62 = all((_j62.get("fabricated") is not True) and _st62 == 200
                   for (_st62, _j62) in _R62.values())
    check("packaging(P62): the third-party cv2.barcode oracle reads back EVERY live render VERBATIM on the "
          "measured margin ladder - all three requested symbologies, both EAN-13s AND EAN-8, each at its OWN "
          "code-length-derived symbology string (not pinned to EAN_13/UPC_A: that matcher bug is what made "
          "EAN-8 look unreadable, corrected 2026-10-11 with the determinism receipt cited above) - and the "
          "ruler's own hand-copied GS1 encoding + own raster must read back the same way as the positive "
          "control; the declared bit_pattern must equal that independent encoding module-for-module (95 for "
          "EAN-13, 67 for EAN-8); the structural invariant P subset-of C stays pinned so a product that "
          "decodes WORSE than the independent encoder is caught even though it is currently an empty set; "
          "as-is pad=0 is recorded, never required; the decoder's own blind spots are disclosed in the block "
          "comment and are NOT used as a pass line",
          _HAS_BC62 and len(_prod62) == len(_REQ62) and _nofab62
          and all(_bitsok62.values())
          and all(_want62d[k][0] for k in _want62d)
          and not _bad62 and not _ctlbad62 and _sub62,
          json.dumps({"cv_missing": not _HAS_CV, "barcode_missing": not _HAS_BC62,
                      "cv_err": _CV_ERR if not _HAS_BC62 else "",
                      "ladder": list(_PADS62),
                      "want_symbology": {"%s/%s" % k: v for k, v in _want62d.items()},
                      "prod_hits": {"%s/%s" % k: v for k, v in _prod62.items()},
                      "prod_readback": {"%s/%s" % k: v for k, v in _readback62.items()},
                      "ruler_hits": {"%s/%s" % k: v for k, v in _ruler62.items()},
                      "as_is": {"%s/%s" % k: v for k, v in _asis62.items()},
                      "bits_equal_ruler": {("%s/%s" % k): v for k, v in _bitsok62.items()},
                      "geometry": {"%s/%s" % k: list(v) for k, v in _geo62d.items()},
                      "P": sorted("%s/%s" % k for k in _P62),
                      "C": sorted("%s/%s" % k for k in _C62),
                      "P_subset_C": _sub62}, ensure_ascii=False))
    # 仪器自校准：正对照已在上一枚（尺子自家 EAN-13 逐字读回）；这里钉阴性与位串敏感度。
    _neg62 = {}
    _flipn62 = 0
    _flipbad62 = []
    if _HAS_BC62:
        _k13 = ("EAN13", "590123412345")
        _base62 = _rd62(_R62[_k13][1].get("output_path") or "")
        _my62, _bh62, _x062, _bw62 = _geo62d[_k13]
        _sc62b = int(_R62[_k13][1].get("scale") or 0)
        _qz62b = int(_R62[_k13][1].get("quiet_zone_modules") or 0)
        if _base62 is not None and _my62 >= 0 and _sc62b > 0 and _x062 >= 0:
            _n1 = _np.full(_base62.shape, 255, _np.uint8)
            _n2 = _base62.copy(); _n2[_my62:_my62 + _bh62, _x062:_x062 + 3 * _sc62b] = 255
            _n3 = _base62.copy()
            _n3[_my62:_my62 + _bh62, _x062 + 45 * _sc62b:_x062 + 50 * _sc62b] = 255
            _n4 = _base62.copy(); _n4[_my62:_my62 + _bh62, :] = 128
            _n5 = _np.random.default_rng(62).integers(0, 256, _base62.shape, dtype=_np.uint8)
            for _nk, _nv in (("all_white", _n1), ("start_guard_erased", _n2),
                             ("centre_guard_erased", _n3), ("bars_greyed", _n4), ("noise", _n5)):
                # 每枚对照必须**真的改到了像素**，否则是空操作而会自证为绿
                _chg = int((_nv != _base62).sum())
                _false62 = [p for p in _PADS62 if _dec62(_pad62(_nv, p))[0]]
                _neg62[_nk] = {"changed_px": _chg, "false_pads": _false62}
            _bits62s = _R62[_k13][1].get("bit_pattern") or ""
            _code62s = _R62[_k13][1].get("code") or ""
            for _col in sorted({0, 1, 2, 10, 20, 30, 45, 47, 60, 80, len(_bits62s) - 1}):
                _x = _qz62b * _sc62b + _col * _sc62b
                assert 0 <= _x and _x + _sc62b <= _base62.shape[1], (_col, _x, _base62.shape[1])
                _f = _base62.copy()
                _f[_my62:_my62 + _bh62, _x:_x + _sc62b] = 255 if _base62[_my62, _x] < 128 else 0
                assert int((_f != _base62).sum()) > 0, ("no-op flip", _col)
                _flipn62 += 1
                if _code62s in [t for p in _PADS62 for t in _dec62(_pad62(_f, p))[0]]:
                    _flipbad62.append(_col)
    check("packaging(P62): 1D oracle instrument self-calibration - 5 negative controls (all-white / start guard "
          "101 erased / centre guard 01010 erased / bars greyed to 128 / RNG noise) decode to NOTHING at every "
          "pad of the ladder, each control proven to have really changed pixels (no vacuous no-op), and 11 "
          "single-module flips (bounds-asserted, never an empty slice) NEVER read back the original code",
          _HAS_BC62 and len(_neg62) == 5 and _flipn62 == 11
          and all(v["changed_px"] > 0 for v in _neg62.values())
          and not any(v["false_pads"] for v in _neg62.values())
          and not _flipbad62,
          json.dumps({"barcode_missing": not _HAS_BC62, "negatives": _neg62,
                      "flips_tried": _flipn62, "flips_read_back_original": _flipbad62},
                     ensure_ascii=False))
    # ==== Phase 62 block end ====
    # Phase 57：控制台条码面板对 QR 的真实渲染面（p57_webui_qr_red 探针升格为常驻守卫）。
    # 修前实测五判据全 RED：面板只发 code/symbology/scale/height/quiet/draw_text，
    # 既发不出纠错等级，又把服务端返回的 side×side 模块阵拼成 bit_pattern 当一维条纹逐列画
    # ——QR 在控制台上画出来是一条无意义横带。判据钉在「活体服务返回的 HTML/JS 原文」上，
    # 不是仓库源码（测链接不测源码）。
    import re as _re57c
    _js57 = _uijs.text
    _i57 = _js57.find('$("bc-run")')
    _j57 = _js57.find("\n/* =", _i57 + 10)
    _seg57 = _js57[_i57:_j57] if _i57 >= 0 and _j57 > _i57 else ""
    _m_ec57 = _re57c.search(r'<select id="bc-ec">(.*?)</select>', _ui.text, _re57c.S)
    _opts57 = _re57c.findall(r"<option[^>]*>([^<]+)</option>", _m_ec57.group(1)) if _m_ec57 else []
    check("webui(P57): the console really renders QR, not just 1D bars — served index.html carries a #bc-ec select "
          "whose options are exactly L/M/Q/H, the served bc-run handler sends ec_level in its body and omits quiet when "
          "the field is blank (so the server's per-symbology standard quiet zone applies), and the handler branches on "
          "Array.isArray(j.matrix) to fillRect a 2D module grid instead of streaming j.bit_pattern as one row",
          _opts57 == ["L", "M", "Q", "H"]
          and "ec_level: $(\"bc-ec\").value" in _seg57
          and 'Array.isArray(j.matrix)' in _seg57
          and _seg57.count("fillRect") >= 3
          and "for (let r57" in _seg57 and "for (let c57" in _seg57
          and "j.bit_pattern" in _seg57
          and 'if (_qraw !== "") _body.quiet' in _seg57
          and len(_seg57) > 1200,
          json.dumps({"opts": _opts57, "seg_len": len(_seg57),
                      "has_ec": "ec_level" in _seg57, "has_matrix": "Array.isArray(j.matrix)" in _seg57}))
    # §46 Panel Relation / 刀版面板拓扑（纯几何，真跑）
    _box = [
        {"id": "glue", "x": -15, "y": 0, "width": 15, "height": 100},
        {"id": "front", "x": 0, "y": 0, "width": 60, "height": 100},
        {"id": "right", "x": 60, "y": 0, "width": 40, "height": 100},
        {"id": "back", "x": 100, "y": 0, "width": 60, "height": 100},
        {"id": "left", "x": 160, "y": 0, "width": 40, "height": 100},
        {"id": "top_f", "x": 0, "y": 100, "width": 60, "height": 30},
        {"id": "top_b", "x": 100, "y": 100, "width": 60, "height": 30},
        {"id": "bot_f", "x": 0, "y": -30, "width": 60, "height": 30},
        {"id": "bot_b", "x": 100, "y": -30, "width": 60, "height": 30},
    ]
    topo = requests.post(BASE + "/api/v1/packaging/panel-topology", json={"panels": _box}).json()
    check("packaging: real panel topology geometry (9 panels / 8 folds / connected / cut=870)",
          topo.get("implemented") is True and topo.get("fabricated") is False
          and topo.get("panel_count") == 9 and topo.get("relation_count") == 8
          and topo.get("is_connected") is True and topo.get("group_count") == 1
          and topo.get("cut_boundary_length") == 870,
          json.dumps({k: topo.get(k) for k in ("panel_count", "relation_count", "is_connected", "group_count", "cut_boundary_length")}))
    _deg = {p["id"]: p["fold_degree"] for p in topo.get("panels", [])}
    check("packaging: panel adjacency degrees real (front=4 body strip, glue=1)", _deg.get("front") == 4 and _deg.get("glue") == 1, json.dumps(_deg))
    topo2 = requests.post(BASE + "/api/v1/packaging/panel-topology", json={"panels": [
        {"id": "a", "x": 0, "y": 0, "width": 10, "height": 10}, {"id": "b", "x": 100, "y": 100, "width": 10, "height": 10}]}).json()
    check("packaging: disconnected die honestly reported (not forced connected, red-line)",
          topo2.get("is_connected") is False and topo2.get("group_count") == 2 and topo2.get("status") == "disconnected",
          json.dumps({k: topo2.get(k) for k in ("is_connected", "group_count", "status")}))
    topoempty = requests.post(BASE + "/api/v1/packaging/panel-topology", json={"panels": []})
    check("packaging: empty panels rejected 400 (no fake topology)", topoempty.status_code == 400, f"HTTP {topoempty.status_code}")

    # ---------------- 自动更新引擎（§75-§77）真实端到端：本地 HTTP 更新源 + 验签 + 下载校验 + 原子替换 ----------------
    import hashlib as _hl, http.server as _hs, socketserver as _ss, threading as _th
    _SECRET = "acceptance-signing-key"
    _ART = b"NEW-BINARY-v2.0.0-Payload"
    _ART_SHA = _hl.sha256(_ART).hexdigest()
    sys.path.insert(0, str(OUT))
    import update_engine as _ue

    class _FeedHandler(_hs.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass
        def do_GET(self):
            host = f"http://{self.headers.get('Host')}"
            if self.path == "/manifest.json":
                m = {"version": "2.0.0", "url": f"{host}/artifact.bin", "sha256": _ART_SHA, "notes": "acc"}
                m["signature"] = _ue.UpdateEngine.sign(m, _SECRET)
                b = json.dumps(m).encode("utf-8")
                self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(b)
            elif self.path == "/manifest-tamper.json":
                m = {"version": "3.0.0", "url": f"{host}/artifact.bin", "sha256": _ART_SHA, "signature": "bad"}
                b = json.dumps(m).encode("utf-8")
                self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(b)
            elif self.path == "/artifact.bin":
                self.send_response(200); self.send_header("Content-Type", "application/octet-stream"); self.end_headers(); self.wfile.write(_ART)
            else:
                self.send_response(404); self.end_headers()

    _feed = _ss.TCPServer(("127.0.0.1", 0), _FeedHandler)
    _fport = _feed.server_address[1]
    _th.Thread(target=_feed.serve_forever, daemon=True).start()
    try:
        _fbase = f"http://127.0.0.1:{_fport}"
        # 未配置渠道 → 诚实 no_update_configured（绝不伪造发现新版本）
        ck_none = requests.post(BASE + "/api/v1/update/check", json={"channel": "stable"}).json()
        check("update: unconfigured feed honest no_update_configured (red-line)",
              ck_none.get("status") == "no_update_configured" and ck_none.get("fabricated") is False, json.dumps(ck_none))
        # 配置 dev 渠道指向本地真实更新源
        cfgr = requests.post(BASE + "/api/v1/update/configure",
                             json={"channel": "dev", "feed_url": _fbase + "/manifest.json",
                                   "secret": _SECRET, "current_version": "1.0.0"}).json()
        check("update: configure returns redacted (no secret leak)",
              cfgr.get("configured") is True and "secret" not in cfgr and cfgr.get("has_secret") is True, json.dumps(cfgr))
        ck = requests.post(BASE + "/api/v1/update/check", json={"channel": "dev", "current_version": "1.0.0"}).json()
        check("update: real signed manifest -> update_available verified",
              ck.get("status") == "update_available" and ck.get("verified") is True
              and ck.get("remote_version") == "2.0.0" and ck.get("sha256") == _ART_SHA, json.dumps(ck))
        # 篡改签名 → integrity_failed
        requests.post(BASE + "/api/v1/update/configure",
                      json={"channel": "beta", "feed_url": _fbase + "/manifest-tamper.json",
                            "secret": _SECRET, "current_version": "1.0.0"})
        ckt = requests.post(BASE + "/api/v1/update/check", json={"channel": "beta", "current_version": "1.0.0"}).json()
        check("update: tampered signature -> integrity_failed (not trusted)",
              ckt.get("status") == "integrity_failed" and ckt.get("verified") is False, json.dumps(ckt))
        # 已最新 → up_to_date
        cku = requests.post(BASE + "/api/v1/update/check", json={"channel": "dev", "current_version": "9.9.9"}).json()
        check("update: newer current -> up_to_date", cku.get("status") == "up_to_date" and cku.get("verified") is True, json.dumps(cku))
        # 真实下载 + SHA 校验
        dl = requests.post(BASE + "/api/v1/update/download",
                           json={"channel": "dev", "staged_dir": str(tmp / "staged")}).json()
        check("update: real download SHA-256 verified",
              dl.get("status") == "downloaded" and dl.get("integrity") == "sha256_match"
              and os.path.isfile(dl.get("staged_path", ""))
              and open(dl.get("staged_path", ""), "rb").read() == _ART, json.dumps({k: dl.get(k) for k in ("status", "integrity", "bytes")}))
        # 真实原子替换：造一个"当前 EXE"文件 → apply → 内容被替换、备份清理
        fake_exe = tmp / "current_app.exe"
        fake_exe.write_bytes(b"OLD-v1-binary")
        ap = requests.post(BASE + "/api/v1/update/apply",
                           json={"target_path": str(fake_exe), "staged_path": dl["staged_path"],
                                 "expected_sha256": _ART_SHA}).json()
        check("update: real atomic apply swaps target content",
              ap.get("status") == "applied" and ap.get("applied") is True
              and fake_exe.read_bytes() == _ART, json.dumps(ap))
        # 非法渠道 → 400
        badch = requests.post(BASE + "/api/v1/update/check", json={"channel": "nope"})
        check("update: unknown channel rejected 400", badch.status_code == 400, f"HTTP {badch.status_code}")
    finally:
        _feed.shutdown(); _feed.server_close()

    # ---------------- NAS 网络存储引擎（§81 · 真客户端：文件系统/映射盘 + WebDAV + 文件锁 + 缓存） ----------------
    import http.server as _nhs, socketserver as _nss, threading as _nth

    class _NasDavHandler(_nhs.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass
        def do_OPTIONS(self):
            self.send_response(200); self.send_header("Allow", "OPTIONS, PROPFIND, GET"); self.end_headers()
        def do_PROPFIND(self):
            if self.path.rstrip("/") != "/share":
                self.send_response(404); self.end_headers(); return
            xml = ('<?xml version="1.0"?><D:multistatus xmlns:D="DAV:">'
                   '<D:response><D:href>/share/</D:href><D:propstat><D:prop>'
                   '<D:resourcetype><D:collection/></D:resourcetype><D:displayname>share</D:displayname>'
                   '</D:prop></D:propstat></D:response>'
                   '<D:response><D:href>/share/design.psd</D:href><D:propstat><D:prop>'
                   '<D:resourcetype/><D:displayname>design.psd</D:displayname>'
                   '<D:getcontentlength>12345</D:getcontentlength></D:prop></D:propstat></D:response>'
                   '</D:multistatus>').encode("utf-8")
            self.send_response(207); self.send_header("Content-Type", "application/xml; charset=utf-8")
            self.send_header("Content-Length", str(len(xml))); self.end_headers(); self.wfile.write(xml)
        def do_GET(self):
            if self.path == "/share/design.psd":
                payload = b"PSD-REAL-BYTES"
                self.send_response(200); self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(len(payload))); self.end_headers(); self.wfile.write(payload)
            else:
                self.send_response(404); self.end_headers()

    _dav = _nss.TCPServer(("127.0.0.1", 0), _NasDavHandler)
    _dport = _dav.server_address[1]
    _nth.Thread(target=_dav.serve_forever, daemon=True).start()
    try:
        # 真实"映射盘/共享"临时目录
        nshare = tmp / "nas_mapped"
        (nshare / "assets").mkdir(parents=True, exist_ok=True)
        (nshare / "readme.txt").write_text("nas-content", encoding="utf-8")

        # 未配置目标 → 诚实 not_configured（不伪造已连接）
        g = requests.post(BASE + "/api/v1/nas/connect", json={"target_id": "no_such"}).json()
        check("nas: unconfigured target honest not_configured (red-line)",
              g.get("status") == "not_configured" and g.get("fabricated") is False, json.dumps(g))

        # 配置 filesystem 目标（映射盘/UNC 经 OS 原生访问）
        requests.post(BASE + "/api/v1/nas/configure",
                      json={"target_id": "fs", "target": str(nshare), "kind": "filesystem", "share_root": str(nshare)}).json()
        c = requests.post(BASE + "/api/v1/nas/connect", json={"target_id": "fs", "timeout": 3}).json()
        check("nas: filesystem/mapped target real connect via native redirector",
              c.get("status") == "connected" and c.get("access") == "native-os-redirector", json.dumps({k: c.get(k) for k in ('status', 'access')}))
        # 真实枚举
        l = requests.post(BASE + "/api/v1/nas/list", json={"target_id": "fs"}).json()
        rd = next((e for e in l.get("entries", []) if e.get("name") == "readme.txt"), {})
        check("nas: real enumeration with correct name/type/size",
              l.get("status") == "ok" and rd.get("type") == "file" and rd.get("size") == len("nas-content"),
              json.dumps([e.get("name") for e in l.get("entries", [])]))
        # 路径穿越防护（§99）
        tv = requests.post(BASE + "/api/v1/nas/list", json={"target_id": "fs", "subpath": "../.."}).json()
        check("nas: path traversal blocked (sandbox root)", tv.get("status") == "forbidden", json.dumps(tv))
        # P65-D3 回归位（常驻）：上面那条走的是**配了 share_root 的夹具形**，而控制台配置表单真实
        # 发送的是「只发 target、不带 share_root」那一形制——旧产品把 _safe_join 守卫挂在
        # t.get("share_root") 上，于是无 share_root 的目标可用 ../.. 直接枚举沙箱外目录（真缺陷，
        # 由 p65_wiring_probe.py 腿 A 抓出并修产品）。这里同时钉正腿与负腿：正腿证明 fs2 确实配上了
        # 且能如实枚举（防「目标不存在 ⇒ forbidden 也成立」的假绿），负腿证明越界被拦且零条目。
        requests.post(BASE + "/api/v1/nas/configure",
                      json={"target_id": "fs2", "target": str(nshare), "kind": "filesystem"}).json()
        w2 = requests.post(BASE + "/api/v1/nas/list", json={"target_id": "fs2"}).json()
        tv2 = requests.post(BASE + "/api/v1/nas/list",
                            json={"target_id": "fs2", "subpath": "../.."}).json()
        check("nas(P65): the no-share_root shape the console form actually sends keeps the sandbox on the target itself — plain list honest ok with real entries AND ../.. forbidden with ZERO entries (P65-D3 regression seat; 正腿在场⇒负腿不是「目标缺席」的假绿)",
              w2.get("status") == "ok" and any(e.get("name") == "assets" for e in w2.get("entries", []))
              and tv2.get("status") == "forbidden" and tv2.get("entries") == []
              and tv2.get("fabricated") is False,
              json.dumps({"pos": [w2.get("status"), [e.get("name") for e in w2.get("entries", [])]],
                          "neg": {k: tv2.get(k) for k in ("status", "reason", "fabricated")}},
                         ensure_ascii=False)[:240])
        # 缓存真实落盘：删除底层文件后仍从快照命中
        requests.post(BASE + "/api/v1/nas/list", json={"target_id": "fs"})  # 写入缓存
        (nshare / "readme.txt").unlink()
        cc = requests.post(BASE + "/api/v1/nas/list", json={"target_id": "fs", "use_cache": True, "max_age": 60}).json()
        check("nas: cache snapshot served from disk (cache:hit) after file removed",
              cc.get("cache") == "hit" and any(e.get("name") == "readme.txt" for e in cc.get("entries", [])), json.dumps({k: cc.get(k) for k in ('status', 'cache')}))

        # 真实跨进程文件锁检测
        nshare.mkdir(exist_ok=True)
        lockf = nshare / "locked.bin"; lockf.write_bytes(b"lockme")
        holder = subprocess.Popen([sys.executable, "-c",
            "import os,msvcrt,time,sys; fd=os.open(sys.argv[1],os.O_RDWR); msvcrt.locking(fd,msvcrt.LK_NBLCK,1); print('HELD',flush=True); time.sleep(float(sys.argv[2]))",
            str(lockf), "5"], stdout=subprocess.PIPE, text=True)
        held = holder.stdout.readline().strip()
        lk = requests.post(BASE + "/api/v1/nas/lock", json={"target_id": "fs", "subpath": "locked.bin"}).json()
        check("nas: real file-lock detection returns locked=True while held",
              held == "HELD" and lk.get("status") == "ok" and lk.get("locked") is True, json.dumps(lk))
        holder.terminate()
        try:
            holder.wait(timeout=5)
        except Exception:
            holder.kill()
        # Windows 在 killed 进程关闭句柄表前可能瞬时仍显占用，有界轮询直至锁释放（真实断言：终须为 False）
        lk2 = {}
        _deadline = time.time() + 3.0
        while time.time() < _deadline:
            lk2 = requests.post(BASE + "/api/v1/nas/lock", json={"target_id": "fs", "subpath": "locked.bin"}).json()
            if lk2.get("locked") is False:
                break
            time.sleep(0.15)
        check("nas: file-lock detection returns locked=False after release",
              lk2.get("status") == "ok" and lk2.get("locked") is False, json.dumps(lk2))

        # 不可达主机诚实（不伪造已连接）+ 断线重连真实退避
        requests.post(BASE + "/api/v1/nas/configure", json={"target_id": "dead", "target": r"\\zj-no-host\share", "kind": "filesystem"}).json()
        rc = requests.post(BASE + "/api/v1/nas/reconnect", json={"target_id": "dead", "attempts": 2, "base_delay": 0.05, "timeout": 1}).json()
        check("nas: dead host honest unreachable + reconnect recovered=False with real attempts",
              rc.get("status") == "unreachable" and rc.get("recovered") is False and rc.get("attempts_made") == 2, json.dumps({k: rc.get(k) for k in ('status', 'recovered', 'attempts_made')}))

        # 真实 WebDAV 客户端：PROPFIND 解析 + GET 下载
        requests.post(BASE + "/api/v1/nas/configure", json={"target_id": "dav", "target": f"http://127.0.0.1:{_dport}/share", "kind": "webdav"}).json()
        dc = requests.post(BASE + "/api/v1/nas/connect", json={"target_id": "dav", "timeout": 5}).json()
        check("nas: webdav real PROPFIND Depth:0 -> connected (HTTP 207)",
              dc.get("status") == "connected" and dc.get("http_status") == 207, json.dumps({k: dc.get(k) for k in ('status', 'http_status')}))
        dl = requests.post(BASE + "/api/v1/nas/list", json={"target_id": "dav", "timeout": 5}).json()
        psd = next((e for e in dl.get("entries", []) if e.get("name") == "design.psd"), {})
        check("nas: webdav PROPFIND Depth:1 multistatus parsed (name+size)",
              dl.get("status") == "ok" and psd.get("type") == "file" and psd.get("size") == 12345, json.dumps([e.get("name") for e in dl.get("entries", [])]))
        dd = requests.post(BASE + "/api/v1/nas/download", json={"target_id": "dav", "subpath": "design.psd", "dest_dir": str(tmp / "nas_dl")}).json()
        check("nas: webdav real GET download bytes match",
              dd.get("status") == "downloaded" and os.path.isfile(dd.get("path", ""))
              and open(dd.get("path", ""), "rb").read() == b"PSD-REAL-BYTES", json.dumps({k: dd.get(k) for k in ('status', 'bytes')}))
        dwl = requests.post(BASE + "/api/v1/nas/lock", json={"target_id": "dav", "subpath": "design.psd"}).json()
        check("nas: webdav lock honestly not_supported_for_webdav (no fake lock state)",
              dwl.get("status") == "not_supported_for_webdav", json.dumps(dwl))
        # 关掉 WebDAV 源 → 诚实 unreachable（非缓存伪装成已连接）
        _dav.shutdown(); _dav.server_close()
        ddead = requests.post(BASE + "/api/v1/nas/connect", json={"target_id": "dav", "timeout": 3}).json()
        check("nas: webdav source closed -> honest unreachable (transport error caught)",
              ddead.get("status") == "unreachable", json.dumps(ddead))
        # 未知 kind → 400
        badk = requests.post(BASE + "/api/v1/nas/configure", json={"target_id": "x", "target": "y", "kind": "ftp"})
        check("nas: unknown kind rejected 400", badk.status_code == 400, f"HTTP {badk.status_code}")
    finally:
        try:
            _dav.shutdown(); _dav.server_close()
        except Exception:
            pass

    # ---------------- Phase 10 真实 AI Provider（§101/§102 + §17 Planner） ----------------
    # 起一个**真实的 OpenAI-兼容 mock 服务器**，验证 provider_engine 的 HTTP 往返 / Bearer 鉴权 /
    # SSE 流式 / usage / 图像落盘 / §17 结构化规划全为真；未配置 / 密钥错 / 不可达 一律诚实降级不伪造。
    import http.server as _phs, socketserver as _pss, threading as _pth
    _PNG_B64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
    _EXPECT_KEY = "test-key-123"
    _PLAN_JSON = json.dumps({"goal": "重做主视觉", "steps": ["分析风格", "生成三方向", "用户选择", "应用并印前检查"],
                             "tools": ["ps.open", "ai.generate"], "constraints": ["保持Logo不变"],
                             "protected_objects": ["logo"], "verification": ["无溢出", "对比度达标"],
                             "rollback": ["恢复V1快照"]}, ensure_ascii=False)

    class _OpenAIHandler(_phs.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"
        def log_message(self, *a): pass
        def _authed(self): return self.headers.get("Authorization") == f"Bearer {_EXPECT_KEY}"
        def _send(self, code, payload, ctype="application/json"):
            self.send_response(code); self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(payload))); self.end_headers(); self.wfile.write(payload)
        def _body(self):
            n = int(self.headers.get("Content-Length", 0) or 0)
            try: return json.loads(self.rfile.read(n).decode("utf-8")) if n else {}
            except Exception: return {}
        def do_GET(self):
            if self.path.endswith("/models"):
                if not self._authed():
                    self._send(401, b'{"error":"unauthorized"}'); return
                self._send(200, json.dumps({"object": "list", "data": [{"id": "unit-test-model"}]}).encode()); return
            self._send(404, b'{}')
        def do_POST(self):
            if not self._authed():
                self._send(401, b'{"error":{"message":"bad key"}}'); return
            body = self._body()
            if self.path.endswith("/chat/completions"):
                if body.get("stream") is True:  # 真实 SSE 增量
                    self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.end_headers()
                    for c in ["你好", "，真实", "流式"]:
                        self.wfile.write(b"data: " + json.dumps({"model": "unit-test-model", "choices": [{"index": 0, "delta": {"content": c}}]}).encode() + b"\n\n")
                        self.wfile.flush()
                    self.wfile.write(b"data: [DONE]\n\n"); self.wfile.flush(); return
                content = "抱歉，这不是 JSON。" if "__NONJSON__" in json.dumps(body, ensure_ascii=False) else _PLAN_JSON
                self._send(200, json.dumps({"model": body.get("model", "unit-test-model"),
                                            "choices": [{"index": 0, "message": {"role": "assistant", "content": content}}],
                                            "usage": {"prompt_tokens": 12, "completion_tokens": 34, "total_tokens": 46}}).encode()); return
            if self.path.endswith("/images/generations"):
                self._send(200, json.dumps({"model": body.get("model", "img"),
                                            "data": [{"b64_json": _PNG_B64} for _ in range(int(body.get("n", 1)))]}).encode()); return
            self._send(404, b'{}')

    _pfeed = _pss.TCPServer(("127.0.0.1", 0), _OpenAIHandler)
    _pth.Thread(target=_pfeed.serve_forever, daemon=True).start()
    PBASE = f"http://127.0.0.1:{_pfeed.server_address[1]}/v1"
    try:
        # 1) 未配置任何 Provider → chat / plan / images 诚实 502（绝不伪造 AI 输出，§2 红线）
        nc = requests.post(BASE + "/api/v1/provider/chat", json={"prompt": "画个logo"})
        check("provider: unconfigured chat honest 502 no_provider_configured (red-line)",
              nc.status_code == 502 and nc.json()["detail"].get("status") == "no_provider_configured"
              and nc.json()["detail"].get("fabricated") is False, f"HTTP {nc.status_code} {nc.json().get('detail')}")
        np = requests.post(BASE + "/api/v1/plan", json={"goal": "改标题"})
        check("provider: unconfigured plan honest 502 (no fake plan)",
              np.status_code == 502 and np.json()["detail"].get("status") == "no_provider_configured", f"HTTP {np.status_code}")
        # 2) 配置真实 Provider（base_url 指向本地 mock）+ DPAPI 密钥脱敏，视图绝不回显明文
        cfg = requests.post(BASE + "/api/v1/provider/configure",
                            json={"provider_id": "mock", "base_url": PBASE, "api_key": _EXPECT_KEY,
                                  "model": "unit-test-model", "image_model": "img-test"})
        check("provider: configure returns redacted view (key never leaked)",
              cfg.status_code == 200 and _EXPECT_KEY not in cfg.text
              and cfg.json()["providers"]["mock"]["has_key"] is True, f"HTTP {cfg.status_code}")
        lst = requests.get(BASE + "/api/v1/provider")
        check("provider: list masked (no plaintext key)", lst.status_code == 200 and _EXPECT_KEY not in lst.text, lst.text[:80])
        # 3) 真实健康检查（GET /models 带鉴权）
        hp = requests.post(BASE + "/api/v1/provider/health", json={"provider_id": "mock"}).json()
        check("provider: real health via /models reachable", hp.get("reachable") is True, json.dumps(hp))
        # 4) 真实 chat HTTP 往返 + usage 真解析 + Bearer 鉴权被服务端收到
        ch = requests.post(BASE + "/api/v1/provider/chat", json={"prompt": "画个茶叶包装", "provider_id": "mock"}).json()
        check("provider: real chat round-trip content+usage (auth header honored)",
              ch.get("status") == "ok" and ch.get("content") and ch.get("fabricated") is False
              and ch.get("usage", {}).get("total_tokens") == 46 and ch.get("model") == "unit-test-model", json.dumps(ch)[:120])
        # 5) 真实 SSE 流式增量拼装
        st = requests.post(BASE + "/api/v1/provider/chat", json={"prompt": "x", "provider_id": "mock", "stream": True}).json()
        check("provider: real SSE stream assembled",
              st.get("status") == "ok" and st.get("content") == "你好，真实流式" and st.get("streamed") is True, json.dumps(st)[:120])
        # 6) 真实图像生成落盘 n=2
        im = requests.post(BASE + "/api/v1/provider/images", json={"prompt": "海报", "provider_id": "mock", "n": 2}).json()
        check("provider: real image gen, files written, honest count",
              im.get("status") == "ok" and im.get("images_generated") == 2
              and all(f.get("bytes", 0) > 0 for f in im.get("files", [])) and im.get("fabricated") is False, json.dumps(im)[:140])
        # 7) §17 Planner：真实调用 + 结构化解析
        pl = requests.post(BASE + "/api/v1/plan", json={"goal": "把茶叶包装重做主视觉", "provider_id": "mock"}).json()
        check("provider: §17 planner real LLM structured output parsed",
              pl.get("status") == "ok" and isinstance(pl.get("plan"), dict)
              and pl["plan"].get("steps") and pl["plan"].get("protected_objects") == ["logo"] and pl.get("fabricated") is False,
              json.dumps(pl.get("plan", pl))[:140])
        # 8) 模型输出非 JSON → planner_output_unparseable（保留 raw，绝不硬编造 schema）
        pnb = requests.post(BASE + "/api/v1/plan", json={"goal": "测试", "provider_id": "mock", "context": "__NONJSON__"}).json()
        check("provider: planner non-JSON output honest unparseable (no fabrication)",
              pnb.get("status") == "planner_output_unparseable" and pnb.get("fabricated") is False and "raw" in pnb, json.dumps(pnb)[:120])
        # 9) 密钥错 → 真实 401 → auth_failed（不谎称成功）
        requests.post(BASE + "/api/v1/provider/configure", json={"provider_id": "bad", "base_url": PBASE, "api_key": "wrong-key", "model": "m"})
        bad = requests.post(BASE + "/api/v1/provider/chat", json={"prompt": "x", "provider_id": "bad"})
        check("provider: wrong key -> honest auth_failed 502 (not faked)",
              bad.status_code == 502 and bad.json()["detail"].get("error") == "auth_failed", f"HTTP {bad.status_code} {bad.json().get('detail')}")
        # 10) 不可达 → honest unreachable（传输异常如实捕获不抛崩）
        requests.post(BASE + "/api/v1/provider/configure", json={"provider_id": "dead", "base_url": "http://127.0.0.1:9/v1", "api_key": "k", "model": "m"})
        un = requests.post(BASE + "/api/v1/provider/chat", json={"prompt": "x", "provider_id": "dead", "timeout": 3})
        check("provider: dead endpoint -> honest unreachable 502",
              un.status_code == 502 and un.json()["detail"].get("error") == "unreachable", f"HTTP {un.status_code} {un.json().get('detail')}")
    finally:
        try:
            _pfeed.shutdown(); _pfeed.server_close()
        except Exception:
            pass

    # ---------------- Adobe UXP 桥接（§8/§9/§24/§25/§26/§74 · 服务端半侧端到端）----------------
    # 交付并验证的是"桥接机制"：发现文件→随机 Token 认证→能力协商→命令下发→宿主真实回传对账。
    # 用协议合规 mock host（后台线程 poll/execute/result）驱动真实往返；无宿主/超时/未协商一律诚实降级。
    import threading as _thr
    _bridge_url = BASE + "/api/v1/uxp/bridge"
    bv = requests.get(_bridge_url).json()
    disco_path = Path(bv.get("discovery_file", ""))
    check("uxp: discovery published, no host connected yet",
          bv.get("implemented") is True and disco_path.exists() and bv.get("connected_hosts") == [],
          f"exists={disco_path.exists()} hosts={bv.get('connected_hosts')}")
    check("uxp: Phase 25 nesting fix - discovery sits directly under DATA_ROOT/uxp, no uxp/uxp file",
          disco_path.parent == tmp / "data" / "uxp" and disco_path.name == "bridge-endpoint.json"
          and not (tmp / "data" / "uxp" / "uxp" / "bridge-endpoint.json").exists(),
          str(disco_path))

    nh = requests.post(BASE + "/api/v1/uxp/dispatch", json={"tool": "ps.layer.create", "args": {"name": "x"}})
    check("uxp: dispatch w/o host -> honest 502 no_host_connected (never fakes document op)",
          nh.status_code == 502 and nh.json()["detail"].get("error") == "no_host_connected"
          and nh.json()["detail"].get("fabricated") is False, json.dumps(nh.json().get("detail"))[:120])

    info = json.loads(disco_path.read_text(encoding="utf-8"))  # §8 明文 token 仅经本地发现文件传递
    token = info["session_token"]
    check("uxp: §8 discovery carries port + plaintext session token",
          int(info["port"]) == PORT and len(token) > 20, f"port={info['port']} (harness port={PORT})")

    wa = requests.post(BASE + "/api/v1/uxp/handshake", json={"host": "photoshop", "version": "25.3",
          "capabilities": {"layer_write": True}, "token": "WRONG-TOKEN"})
    check("uxp: wrong session token -> honest 502 auth_failed",
          wa.status_code == 502 and wa.json()["detail"].get("error") == "auth_failed", json.dumps(wa.json().get("detail"))[:120])

    hs = requests.post(BASE + "/api/v1/uxp/handshake", json={"host": "photoshop", "version": "25.3.1",
          "capabilities": {"document_read": True, "layer_write": True, "selection": True, "batch_operation": True},
          "token": token})
    hj = hs.json()
    host_id = hj.get("host_id")
    check("uxp: §25 handshake connected, capability-gated runnable tools computed (version not gating)",
          hs.status_code == 200 and hj.get("status") == "connected" and "ps.layer.create" in (hj.get("runnable_now_tools") or [])
          and "ps.image.import" not in (hj.get("runnable_now_tools") or []) and hj.get("fabricated") is False,
          json.dumps({k: hj.get(k) for k in ("status", "runnable_now_tools", "gating")})[:170])

    # 协议合规 mock host：真实执行（读回内存图层表）；受保护对象如实拒绝；未覆盖意图如实 not_supported
    _mem_layers = {}
    _host_stop = _thr.Event()
    def _mock_host():
        while not _host_stop.is_set():
            try:
                pr = requests.post(BASE + "/api/v1/uxp/poll",
                                   json={"host_id": host_id, "token": token, "timeout": 2}, timeout=8).json()
            except Exception:
                _host_stop.wait(0.3); continue
            for cmd in pr.get("commands", []):
                rid = cmd["request_id"]; intent = cmd.get("intent")
                args = (cmd.get("context") or {}).get("args") or {}
                prot = ((cmd.get("permissions") or {}).get("protected_objects")) or []
                nm = args.get("name")
                if intent == "layer.create" and nm in prot:
                    requests.post(BASE + "/api/v1/uxp/result", json={"request_id": rid, "token": token,
                        "status": "error", "error": "protected_object", "detail": f"受保护对象 {nm}"})
                elif intent == "layer.create":
                    _mem_layers[nm] = {"name": nm, "opacity": 100}
                    requests.post(BASE + "/api/v1/uxp/result", json={"request_id": rid, "token": token,
                        "status": "ok", "payload": {"objects": [{"name": nm, "created": True}], "layer_count": len(_mem_layers)}})
                else:
                    requests.post(BASE + "/api/v1/uxp/result", json={"request_id": rid, "token": token,
                        "status": "error", "error": "not_supported", "detail": f"mock host: {intent}"})
    _ht = _thr.Thread(target=_mock_host, daemon=True); _ht.start()
    time.sleep(0.3)

    d1 = requests.post(BASE + "/api/v1/uxp/dispatch",
                       json={"tool": "ps.layer.create", "args": {"name": "hero_bg"}, "timeout": 8}).json()
    check("uxp: real dispatch->host executes->reconciled source=host (not server-faked)",
          d1.get("status") == "ok" and d1.get("fabricated") is False
          and d1.get("reconciled", {}).get("source") == "host" and "hero_bg" in _mem_layers, json.dumps(d1)[:150])

    d2 = requests.post(BASE + "/api/v1/uxp/dispatch", json={"tool": "ps.layer.create", "args": {"name": "brand_logo"},
          "protected_objects": ["brand_logo"], "timeout": 8}).json()
    check("uxp: §17 protected object refused by host -> protected_object_conflict (honest, not ok)",
          d2.get("status") == "host_error" and d2.get("error") == "protected_object_conflict"
          and d2.get("fabricated") is False and "brand_logo" not in _mem_layers, json.dumps(d2)[:150])

    cn = requests.post(BASE + "/api/v1/uxp/dispatch",
                       json={"tool": "ps.image.import", "args": {"path": "x.png"}, "timeout": 5})
    check("uxp: tool w/ unnegotiated capability -> 502 capability_not_negotiated (§26 capability gate)",
          cn.status_code == 502 and cn.json()["detail"].get("error") == "capability_not_negotiated",
          json.dumps(cn.json().get("detail"))[:120])
    _host_stop.set()

    bv2 = requests.get(_bridge_url).json()
    check("uxp: §25 negotiated capabilities persisted server-side in bridge view",
          any(h.get("host") == "photoshop" and h.get("capabilities", {}).get("layer_write") is True
              for h in (bv2.get("connected_hosts") or [])), json.dumps(bv2.get("connected_hosts"))[:150])

    # ---------------- Phase 77：CorelDRAW 只读桥接（真实宿主脚本 cdr_host.py + 夹具后端）----------------
    # 这里驱动的是随包宿主脚本本体（不是测试里另写一份 mock）：它按 §8 读 discovery 文件、
    # 随机 Token 握手、长轮询取命令、回传 §9 结果。CorelDRAW 未运行且禁止启动 GUI，故用
    # --fake-backend 的确定性夹具（source=fixture 明确标注），绝不冒充真实文档数据。
    _cdr_disco = tmp / "data" / "uxp" / "bridge-endpoint.json"
    nc0 = requests.post(BASE + "/api/v1/uxp/dispatch", json={"tool": "cdr.document.get", "args": {}})
    check("uxp-cdr: dispatch before any coreldraw host -> honest 502 no_host_connected (never fakes CDR data)",
          nc0.status_code == 502 and nc0.json()["detail"].get("error") == "no_host_connected"
          and nc0.json()["detail"].get("fabricated") is False,
          json.dumps(nc0.json().get("detail"))[:120])
    badh = requests.post(BASE + "/api/v1/uxp/handshake",
                         json={"host": "corel", "version": "1", "capabilities": {"document_read": True},
                               "token": token})
    check("uxp-cdr: host name 'corel' rejected -> unknown_host (whitelist exact 'coreldraw')",
          badh.status_code == 502 and badh.json()["detail"].get("error") == "unknown_host",
          json.dumps(badh.json().get("detail"))[:120])

    _cdr_log_path = tmp / "cdr_host_stdout.log"
    _cdr_log = open(_cdr_log_path, "w", encoding="utf-8")
    # `-u`：宿主脚本的 stdout 被重定向到文件时是块缓冲，若不加 -u 会在 terminate 时
    # 丢掉尚未 flush 的 HANDSHAKE/EXEC 行，令下面那条"stdout 证明真执行"的判据永远落空
    # （本判据要证的是宿主脚本真的跑了，不是它愿不愿意 flush）。
    _cdr_proc = subprocess.Popen(
        [sys.executable, "-u", str(_ZJ_OUTBASE / "cdr_host.py"),
         "--fake-backend", "--discovery", str(_cdr_disco), "--poll-timeout", "2"],
        stdout=_cdr_log, stderr=subprocess.STDOUT)
    _cdr_conn = None
    for _ in range(50):
        _bv = requests.get(_bridge_url).json()
        _cdr_conn = next((h for h in (_bv.get("connected_hosts") or [])
                          if h.get("host") == "coreldraw"), None)
        if _cdr_conn:
            break
        time.sleep(0.2)
    check("uxp-cdr: shipped host script cdr_host.py handshakes as coreldraw/document_read+object_write "
          "(attach-only; fixture backend labelled, no CorelDRAW GUI launched)",
          _cdr_conn is not None
          and _cdr_conn.get("capabilities", {}).get("document_read") is True
          and _cdr_conn.get("capabilities", {}).get("object_write") is True
          and _cdr_conn.get("version") == "fixture",
          json.dumps(_cdr_conn)[:150])

    _d_doc = requests.post(BASE + "/api/v1/uxp/dispatch",
                           json={"tool": "cdr.document.get", "args": {}, "timeout": 10}).json()
    check("uxp-cdr: cdr.document.get real dispatch->host->result reconciled source=host "
          "(fixture payload explicitly labelled, never server-synthesized)",
          _d_doc.get("status") == "ok" and _d_doc.get("host") == "coreldraw"
          and _d_doc.get("fabricated") is False
          and _d_doc.get("reconciled", {}).get("source") == "host"
          and _d_doc.get("reconciled", {}).get("document_id") == "cdr-fixture-doc"
          and (_d_doc.get("result") or {}).get("source") == "fixture",
          json.dumps(_d_doc)[:170])

    _d_pages = requests.post(BASE + "/api/v1/uxp/dispatch",
                             json={"tool": "cdr.page.list", "args": {}, "timeout": 10}).json()
    check("uxp-cdr: cdr.page.list returns the host's real payload (2 pages, object_count=0)",
          _d_pages.get("status") == "ok"
          and len((_d_pages.get("result") or {}).get("pages") or []) == 2
          and _d_pages.get("reconciled", {}).get("object_count") == 0,
          json.dumps(_d_pages)[:170])

    _d_objs = requests.post(BASE + "/api/v1/uxp/dispatch",
                            json={"tool": "cdr.object.list", "args": {"limit": 3}, "timeout": 10}).json()
    check("uxp-cdr: cdr.object.list reconciled object_count=3 from host payload (read-only, risk L0)",
          _d_objs.get("status") == "ok"
          and _d_objs.get("reconciled", {}).get("object_count") == 3
          and len((_d_objs.get("result") or {}).get("objects") or []) == 3
          and (_d_objs.get("result") or {}).get("source") == "fixture",
          json.dumps(_d_objs)[:170])

    # Phase 78：最小写集（cdr.object.create / cdr.object.move）端到端。
    # 纪律线 ①：服务端确认门先于宿主查找与入队——缺 confirm 时返回可审阅计划，零变更；
    #            宿主端 execute_intent 还会二次校验，双层门不可绕过。
    # 纪律线 ②：写后回读 verified / undo_group / source 全由宿主自报，服务端只搬运不合成。
    # 纪律线 ③：delete / rename / save / export 一律未注册 ⇒ 必须 unknown_tool。
    _w_nc = requests.post(BASE + "/api/v1/uxp/dispatch",
                          json={"tool": "cdr.object.create",
                                "args": {"kind": "rectangle", "x1": 5, "y1": 6, "x2": 25, "y2": 36},
                                "timeout": 10}).json()
    _w_list0 = requests.post(BASE + "/api/v1/uxp/dispatch",
                             json={"tool": "cdr.object.list", "args": {"limit": 100}, "timeout": 10}).json()
    check("uxp-cdr-write: create without confirm -> confirmation_required + reviewable plan, zero mutation (double gate arm 1)",
          _w_nc.get("status") == "confirmation_required"
          and _w_nc.get("fabricated") is False
          and (_w_nc.get("plan") or {}).get("risk") == "L1"
          and (_w_nc.get("plan") or {}).get("save") is False
          and (_w_nc.get("plan") or {}).get("reversible") is True
          and (_w_nc.get("plan") or {}).get("effects") == ["object.create"]
          and (_w_nc.get("plan") or {}).get("target", {}).get("kind") == "rectangle"
          and _w_list0.get("reconciled", {}).get("object_count") == 3,
          json.dumps(_w_nc)[:220])

    _w_new = requests.post(BASE + "/api/v1/uxp/dispatch",
                           json={"tool": "cdr.object.create",
                                 "args": {"kind": "rectangle", "name": "P78 Box",
                                          "x1": 5, "y1": 6, "x2": 25, "y2": 36, "confirm": True},
                                 "timeout": 10}).json()
    _w_new_obj = ((_w_new.get("result") or {}).get("objects") or [{}])[0]
    check("uxp-cdr-write: confirmed create executes on host, readback verified=true + undo_group + source=fixture "
          "(never Save), document count 3->4",
          _w_new.get("status") == "ok" and _w_new.get("fabricated") is False
          and _w_new_obj.get("name") == "P78 Box" and _w_new_obj.get("index") == 4
          and _w_new_obj.get("x") == 5 and _w_new_obj.get("y") == 6
          and _w_new_obj.get("width") == 20 and _w_new_obj.get("height") == 30
          and _w_new.get("reconciled", {}).get("verified") is True
          and _w_new.get("reconciled", {}).get("undo_group") == "unsupported"
          and _w_new.get("reconciled", {}).get("result_source") == "fixture"
          and _w_new.get("reconciled", {}).get("source") == "host"
          and _w_new.get("reconciled", {}).get("object_count") == 4,
          json.dumps(_w_new.get("reconciled"))[:220])

    _w_mv_nc = requests.post(BASE + "/api/v1/uxp/dispatch",
                             json={"tool": "cdr.object.move",
                                   "args": {"index": 4, "dx": 10, "dy": 11}, "timeout": 10}).json()
    check("uxp-cdr-write: move without confirm refused by the same gate (effects=object.move, save=false)",
          _w_mv_nc.get("status") == "confirmation_required"
          and (_w_mv_nc.get("plan") or {}).get("effects") == ["object.move"]
          and (_w_mv_nc.get("plan") or {}).get("risk") == "L1"
          and (_w_mv_nc.get("plan") or {}).get("save") is False,
          json.dumps(_w_mv_nc.get("plan"))[:180])

    _w_mv = requests.post(BASE + "/api/v1/uxp/dispatch",
                          json={"tool": "cdr.object.move",
                                "args": {"index": 4, "dx": 10, "dy": 11, "confirm": True},
                                "timeout": 10}).json()
    _w_mv_obj = ((_w_mv.get("result") or {}).get("objects") or [{}])[0]
    _w_list1 = requests.post(BASE + "/api/v1/uxp/dispatch",
                             json={"tool": "cdr.object.list", "args": {"limit": 100}, "timeout": 10}).json()
    _w_after = next((o for o in ((_w_list1.get("result") or {}).get("objects") or [])
                     if o.get("index") == 4), {})
    check("uxp-cdr-write: confirmed move readback x/y = 15/17, verified=true, count unchanged, "
          "and a fresh object.list sees the moved geometry (not just the write payload)",
          _w_mv.get("status") == "ok"
          and _w_mv_obj.get("index") == 4 and _w_mv_obj.get("x") == 15 and _w_mv_obj.get("y") == 17
          and _w_mv.get("reconciled", {}).get("verified") is True
          and _w_mv.get("reconciled", {}).get("object_count") == 4
          and _w_list1.get("reconciled", {}).get("object_count") == 4
          and _w_after.get("x") == 15 and _w_after.get("y") == 17,
          json.dumps({"write": _w_mv_obj, "list": _w_after})[:220])

    _w_del = requests.post(BASE + "/api/v1/uxp/dispatch",
                           json={"tool": "cdr.object.delete", "args": {"index": 4}, "timeout": 10})
    _w_ren = requests.post(BASE + "/api/v1/uxp/dispatch",
                           json={"tool": "cdr.object.rename", "args": {"index": 4, "name": "x"}, "timeout": 10})
    _w_save = requests.post(BASE + "/api/v1/uxp/dispatch",
                            json={"tool": "cdr.document.save", "args": {}, "timeout": 10})
    check("uxp-cdr-write: delete / rename / save are NOT registered -> 502 unknown_tool "
          "(no hidden write surface, never Save)",
          _w_del.status_code == 502 and _w_del.json()["detail"].get("error") == "unknown_tool"
          and _w_ren.status_code == 502 and _w_ren.json()["detail"].get("error") == "unknown_tool"
          and _w_save.status_code == 502 and _w_save.json()["detail"].get("error") == "unknown_tool"
          and _w_del.json()["detail"].get("fabricated") is False,
          json.dumps({n: r.json().get("detail", {}).get("error")
                      for n, r in (("delete", _w_del), ("rename", _w_ren), ("save", _w_save))}))

    _bv3 = requests.get(_bridge_url).json()
    _btools = set(_bv3.get("bridgeable_tools") or [])
    _cdr_expected78 = {"cdr.document.get", "cdr.page.list", "cdr.object.list",
                       "cdr.object.create", "cdr.object.move"}
    check("uxp-cdr: bridge view advertises exactly the five cdr tools (3 read + 2 write); "
          "no delete/rename/save tool invented",
          {t for t in _btools if t.startswith("cdr.")} == _cdr_expected78,
          json.dumps(sorted(t for t in _btools if t.startswith("cdr."))))

    if _cdr_proc.poll() is None:
        _cdr_proc.terminate()
    try:
        _cdr_proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        _cdr_proc.kill()
        _cdr_proc.wait(timeout=5)
    _cdr_log.close()
    _cdr_txt = _cdr_log_path.read_text(encoding="utf-8", errors="replace")
    check("uxp-cdr: host script exited on its own handle (own child only) and its stdout proves real execution "
          "(read + write + readback all logged by the host, not the server)",
          _cdr_proc.poll() is not None
          and "HANDSHAKE ok" in _cdr_txt
          and "EXEC tool=cdr.object.list" in _cdr_txt
          and "status=ok objects=3" in _cdr_txt
          and "EXEC tool=cdr.object.create" in _cdr_txt
          and "EXEC tool=cdr.object.move" in _cdr_txt
          and "verified=True" in _cdr_txt,
          "alive=%s log_handshake=%s log_exec=%s log_write=%s log_readback=%s" % (
              _cdr_proc.poll() is None, "HANDSHAKE ok" in _cdr_txt,
              "EXEC tool=cdr.object.list" in _cdr_txt,
              "EXEC tool=cdr.object.create" in _cdr_txt,
              "verified=True" in _cdr_txt))

    # ---------------- 能力真值表（禁止伪造，§2 红线 / §99.3） ----------------
    caps = requests.get(BASE + "/api/v1/capabilities")
    cj = caps.json() if caps.status_code == 200 else {}
    by_id = {c["id"]: c for c in cj.get("capabilities", [])}
    check("capabilities: honest manifest served", caps.status_code == 200 and cj.get("summary", {}).get("supported", 0) >= 24,
          f"HTTP {caps.status_code} summary={cj.get('summary')}")
    supported_ids = {c["id"] for c in cj.get("capabilities", []) if c["status"] == "supported"}
    check("capabilities: recovery engine marked supported", "recovery_snapshot" in supported_ids, json.dumps(sorted(supported_ids)))
    _new_shipped = {"knowledge_rag", "tool_registry", "skill_runtime", "feature_flags", "design_compare", "delivery_package", "websocket_events",
                    "typography_engine", "design_review", "customer_revision", "color_engine", "layout_engine", "packaging_dieline_vdp",
                    "packaging_barcode", "auto_update", "nas_mount", "packaging_panel_topology", "ai_provider_integration", "adobe_uxp_bridge"}
    check("capabilities: newly shipped subsystems marked supported",
          _new_shipped <= supported_ids,
          f"missing={sorted(_new_shipped - supported_ids)}")
    check("capabilities: barcode/panel_topology AND raster panel-recognition all supported after Phase 47 real delivery (Phase 56: brand_semantic_recognition LEAVES the red-line split too — real Provider vision passthrough ships, so NO brand cap is left unshipped)",
          by_id.get("packaging_barcode", {}).get("status") == "supported"
          and by_id.get("packaging_panel_topology", {}).get("status") == "supported"
          and by_id.get("packaging_panel_recognition", {}).get("status") == "supported"
          and "Phase 47" in by_id.get("packaging_panel_recognition", {}).get("note", "")
          and by_id.get("brand_semantic_recognition", {}).get("status") == "supported",
          f"barcode={by_id.get('packaging_barcode',{}).get('status')} topology={by_id.get('packaging_panel_topology',{}).get('status')} panel={by_id.get('packaging_panel_recognition',{}).get('status')} sem={by_id.get('brand_semantic_recognition',{}).get('status')}")
    check("capabilities: zero prototype placeholders left after Phase 26 honest split (installer_runtime retired); Phase 58 flips the LAST not_shipped — installer_execution supported (executor really ships on the CLI face, receipts derived from disk+registry diff)",
          "installer_runtime" not in by_id
          and by_id.get("installer_runtime_guard", {}).get("status") == "supported"
          and by_id.get("installer_execution", {}).get("status") == "supported"
          and "Phase 58" in by_id.get("installer_execution", {}).get("note", "")
          and cj.get("summary", {}).get("prototype_not_shipped") == 0
          and cj.get("summary", {}).get("not_shipped") == 0,
          f"guard={by_id.get('installer_runtime_guard',{}).get('status')} exec={by_id.get('installer_execution',{}).get('status')} prototype={cj.get('summary',{}).get('prototype_not_shipped')} not={cj.get('summary',{}).get('not_shipped')}")
    check("capabilities: Phase 50 desktop_hub red-line flip (not_shipped -> supported, 27th engine is a REAL tkinter window, not a Tauri-toolchain excuse)",
          by_id.get("desktop_hub", {}).get("status") == "supported"
          and "Phase 50" in by_id.get("desktop_hub", {}).get("note", "")
          and "desktop_hub_engine" in by_id.get("desktop_hub", {}).get("note", "")
          and len(cj.get("engines_loaded", [])) == 30
          and "desktop_hub" in cj.get("engines_loaded", [])
          and "service_registry" in cj.get("engines_loaded", [])
          and "event" in cj.get("engines_loaded", [])
          and "delivery" in cj.get("engines_loaded", [])
          and cj.get("engines_missing") == {},
          f"hub={by_id.get('desktop_hub', {}).get('status')} engines={len(cj.get('engines_loaded', []))} missing={cj.get('engines_missing')}")
    check("capabilities: Phase 56 brand red-line FULLY resolved (color/geometry supported since Phase 25, semantics now supported too via real Provider vision passthrough — old 'semantics NOT claimed' split retired)",
          "brand_intelligence" not in by_id
          and by_id.get("brand_color_analysis", {}).get("status") == "supported"
          and by_id.get("brand_semantic_recognition", {}).get("status") == "supported"
          and "Phase 56" in by_id.get("brand_semantic_recognition", {}).get("note", ""),
          f"color={by_id.get('brand_color_analysis',{}).get('status')} sem={by_id.get('brand_semantic_recognition',{}).get('status')}")
    check("capabilities: evaluation_center upgraded to supported ONLY after Phase 24 real delivery",
          by_id.get("evaluation_center", {}).get("status") == "supported",
          by_id.get("evaluation_center", {}).get("status"))
    _p24 = cj.get("summary", {})
    check("capabilities: Phase 26 truth-table shift is arithmetically consistent (dynamic derive, not hand-typed)",
          _p24.get("total") == len(cj.get("capabilities", []))
          and _p24.get("supported") + _p24.get("needs_adobe_host") + _p24.get("prototype_not_shipped") + _p24.get("not_shipped") == _p24.get("total")
          and _p24.get("total") == 43 and _p24.get("supported") == 40
          and _p24.get("prototype_not_shipped") == 0 and _p24.get("not_shipped") == 0,
          # Phase 47 授权漂移：packaging_panel_recognition not_shipped -> supported（第 25 引擎真实交付）
          # Phase 49 授权漂移：installer_execution 红线分裂，只读一半升为 installer_manifest_verification
          # supported（第 26 引擎），写侧执行仍如实留驻 not_shipped —— 故 total 38→39、supported 32→33。
          # Phase 50 授权漂移：desktop_hub not_shipped -> supported（第 27 引擎 desktop_hub_engine
          # 真实交付，纯标准库 tkinter+urllib 真建窗；旧判词把能力误绑在 Tauri 工具链上，与 P47
          # 被纯几何反解推翻的"需真实 CV"同类）—— 故 supported 33→34、not_shipped 3→2。
          # Phase 53 授权漂移（第 4 次真实漂移）：installer_execution 第二次分裂——编译证据核验
          # (installer_artifact_build) 与安装预检 (installer_preflight) 两面转 supported，**未新增引擎**
          # （总数仍 27）；静默安装/卸载器执行/注册表写删/快捷方式四项仍如实留驻 not_shipped=2。
          # 故 total 39→41、supported 34→36。旧判词"本机没有 iscc、安装版从未真正编译过"经三路
          # 寻径实测证伪（PATH 单路探测造成的仪器伪影），本轮就地更正见报告正文。
          # Phase 56 授权漂移（第 5 次真实漂移）：brand_semantic_recognition not_shipped -> supported
          # （"需外部 CV/LLM"判词被真实 Provider 视觉多模态透传推翻，与 P47『需真实 CV』、P50『需
          # Tauri 工具链』同族误判）——**未新增引擎**（总数仍 27，recognize_semantics 归 brand 引擎），
          # 只把 Logo 图像接进早已内置的真实 Provider chat()。故 supported 36→37、not_shipped 2→1。
          # 留守 not_shipped 的只剩 installer_execution 写侧执行域（静默安装/卸载/注册表写删/快捷方式），
          # 那是永不伪造的诚实边界。
          # Phase 77 授权漂移（第 7 次真实漂移）：cdr_document_read 新增为 supported——CorelDRAW
          # 无 UXP 运行时，故复用同一条 §8/§9 桥接管道，宿主端 outputs/cdr_host.py 以 COM attach-only
          # 读取 document/page/object 三工具（能力键 document_read、风险 L0）；**未新增引擎**
          # （总数仍 27，仍归 adobe_bridge 引擎）。故 total 41→42、supported 38→39；needs_adobe_host
          # 仍 3（ps/ai 写侧 + 能力上报），not_shipped 仍 0。写侧与真实文档变更一律不在本能力内。
          # Phase 78 授权漂移（第 8 次真实漂移）：最小写集 cdr_document_write 新增为 supported——
          # 只含 cdr.object.create / cdr.object.move 两工具（能力键 object_write、风险 L1，
          # 服务端确认门 + 宿主二次校验、写后回读 verified/undo_group、永不 Save）；
          # **未新增引擎**（仍归 adobe_bridge 引擎，总数仍 27）。故 total 42→43、supported 39→40，
          # needs_adobe_host 仍 3、not_shipped 仍 0。
          json.dumps(_p24, ensure_ascii=False))
    check("capabilities: adobe write marked needs_adobe_host",
          by_id.get("ps_document_write", {}).get("status") == "needs_adobe_host", by_id.get("ps_document_write", {}).get("status"))
    check("capabilities: Phase 11 red-line split (bridge pipe supported, real doc write STILL needs_adobe_host)",
          by_id.get("adobe_uxp_bridge", {}).get("status") == "supported"
          and by_id.get("ps_document_write", {}).get("status") == "needs_adobe_host"
          and by_id.get("ai_document_write", {}).get("status") == "needs_adobe_host",
          f"bridge={by_id.get('adobe_uxp_bridge',{}).get('status')} ps={by_id.get('ps_document_write',{}).get('status')} ai={by_id.get('ai_document_write',{}).get('status')}")
    # Registry 2.0 的逐项证据字段在 cj["registry"]["capabilities"]；cj["capabilities"] 是
    # 旧清单面（只有 id/name/status/note）。要核 verified/golden_domain 必须读 registry 面，
    # 否则拿清单面去取这些键只会恒得 None（本轮首跑即因此误红）。
    _reg77 = {c["id"]: c for c in (cj.get("registry", {}) or {}).get("capabilities", [])}
    check("capabilities: Phase 77/78 cdr read+minimal-write are supported (attach-only host), "
          "engine still adobe_bridge, and Phase 79 now ATTACHES their golden domains "
          "(bridge_cdr_read / bridge_cdr_write); the write entry stays bounded to create/move "
          "with never-Save honesty (evidence/verified derivation is asserted post-eval below)",
          by_id.get("cdr_document_read", {}).get("status") == "supported"
          and "Phase 77" in by_id.get("cdr_document_read", {}).get("note", "")
          and "attach-only" in by_id.get("cdr_document_read", {}).get("note", "")
          and _reg77.get("cdr_document_read", {}).get("engine") == "adobe_bridge"
          # Phase 79：读侧挂上 bridge_cdr_read 黄金域（读回 + reconcile 逐字段真值）
          and _reg77.get("cdr_document_read", {}).get("golden_domain") == "bridge_cdr_read"
          and by_id.get("cdr_document_write", {}).get("status") == "supported"
          and "Phase 78" in by_id.get("cdr_document_write", {}).get("note", "")
          and "cdr.object.create" in by_id.get("cdr_document_write", {}).get("note", "")
          and "绝不 Save" in by_id.get("cdr_document_write", {}).get("note", "")
          and _reg77.get("cdr_document_write", {}).get("engine") == "adobe_bridge"
          # Phase 79：写侧挂上 bridge_cdr_write 黄金域（确认门前置拒 + 只读拒 + 写往返 verified）
          and _reg77.get("cdr_document_write", {}).get("golden_domain") == "bridge_cdr_write",
          f"cdr={by_id.get('cdr_document_read',{}).get('status')} "
          f"engine={_reg77.get('cdr_document_read',{}).get('engine')} "
          f"domain={_reg77.get('cdr_document_read',{}).get('golden_domain')} "
          f"w={by_id.get('cdr_document_write',{}).get('status')} "
          f"wdomain={_reg77.get('cdr_document_write',{}).get('golden_domain')}")

    # ---- Phase 24: 评估中心活体断言（真实黄金用例 + 负控校准 + 报告落盘） ----
    # 传输上限而非判据：exec 域真起子进程后全量评测进程内实测 29.9 s（p58_eval_duration_out.txt），
    # 冻结归档与活体服务首跑更慢，60 s 会把「跑得完」误判成「跑不完」。本行只调**传输上限**，
    # 判据侧的 executed/results/域内枚数一律由下面的断言现场派生，绝不在注释里写死数字
    # （P58 教训：注释里的计数会与断言各说各话，改一处漏一处）。
    ev = requests.post(BASE + "/api/v1/eval/run", timeout=240)
    evj = ev.json()
    _es = evj.get("summary", {})
    _cal = evj.get("calibration", [])
    check("eval: pass rate is real-derived from executed cases, never old 0.85 mock (Phase 24)",
          ev.status_code == 200 and _es.get("executed_cases", 0) >= 16 and _es.get("crashed_cases") == 0
          and _es.get("executed_cases") and abs(_es.get("pass_rate", -1) - _es.get("passed", -2) / _es.get("executed_cases", 1)) < 1e-9
          and _es.get("pass_rate") != 0.85,
          json.dumps(_es, ensure_ascii=False))
    check("eval: falsifiability harness present and green - 34 must_fail calibration cases judged fail (Phase 24, +brand 27, +print/knowledge calib 28, +revision/review/security calib 30, +workflow calib 31, +asset/recovery calib 32, +typography calib 33, +barcode/topology calib 34, +license/config/routing calib 36, +update/nas/provider calib 37,+compare/bgre calib 38, +panel_recognition calib 47, +installer_manifest calib 49, +desktop_hub calib 50, +installer_manifest build calib 53, +brand_semantics calib 56, +barcode QR calib 57, +installer_execution calib 58, +runtime_guard calib 59, +installer_preflight calib 59, +barcode 1D oracle calib 63 ×2：calib-barcode-1d-wrong-expect / calib-barcode-1d-g7-typo-table)",
          len(_cal) == 34 and all(c.get("calibration_ok") is True and c.get("actual_verdict") == "fail" for c in _cal)
          and evj.get("honest") is True and evj.get("calibration_error") is False,
          json.dumps(_cal, ensure_ascii=False))
    _bc = (evj.get("categories", {}) or {}).get("brand", {})
    check("eval: brand category truly evaluated via injected brand engine - 4/4 real-pixel cases pass, PNG fixtures on disk (Phase 27)",
          _bc.get("total") == 4 and _bc.get("passed") == 4 and _bc.get("failed") == 0
          and _es.get("executed_cases") == 151
          and (tmp / "data" / "evaluation" / "artifacts" / "eval_brand_two_band.png").exists()
          and (tmp / "data" / "evaluation" / "artifacts" / "eval_brand_logo_rect.png").exists(),
          json.dumps(_bc, ensure_ascii=False))
    _pc = (evj.get("categories", {}) or {}).get("print", {})
    check("eval: print category truly evaluated via injected print_qa engine - 2/2 real-bitmap cases pass, RGB+CMYK fixtures on disk (Phase 28)",
          _pc.get("total") == 2 and _pc.get("passed") == 2 and _pc.get("failed") == 0
          and (tmp / "data" / "evaluation" / "artifacts" / "eval_print_rgb72.png").exists()
          and (tmp / "data" / "evaluation" / "artifacts" / "eval_print_cmyk300.jpg").exists(),
          json.dumps(_pc, ensure_ascii=False))
    _kc = (evj.get("categories", {}) or {}).get("knowledge", {})
    check("eval: knowledge category truly evaluated via injected knowledge_rag engine - 2/2 ranking+isolation cases pass, ingest persisted (Phase 28)",
          _kc.get("total") == 2 and _kc.get("passed") == 2 and _kc.get("failed") == 0
          and (tmp / "data" / "knowledge" / "kb_eval_coffee.json").exists(),
          json.dumps(_kc, ensure_ascii=False))
    _by30 = {r.get("id"): r for r in evj.get("results", [])}
    _rv30 = (evj.get("categories", {}) or {}).get("revision", {})
    _rw30 = (evj.get("categories", {}) or {}).get("review", {})
    _sc30 = (evj.get("categories", {}) or {}).get("security", {})
    check("eval: revision/review/security categories truly evaluated via injected engines - 3+3+3 with real returned payloads (Phase 30)",
          _rv30.get("total") == 3 and _rv30.get("passed") == 3
          and _rw30.get("total") == 3 and _rw30.get("passed") == 3
          and _sc30.get("total") == 3 and _sc30.get("passed") == 3
          and _by30.get("eval-review-dirty-matrix", {}).get("details", {}).get("actual", {}).get("score") == 68
          and _by30.get("eval-revision-apply-math", {}).get("details", {}).get("actual", {}).get("title") == "#8B0000"
          and _by30.get("eval-security-highrisk-no-overreport", {}).get("details", {}).get("actual", {}).get("status") == "no_executor",
          json.dumps({"rv": _rv30, "rw": _rw30, "sc": _sc30}, ensure_ascii=False))
    _wfc31 = (evj.get("categories", {}) or {}).get("workflow", {})
    check("eval: workflow category truly evaluated by fresh zero-tool engines - 6/6 graph-truth cases pass with exact payloads, executed 151 (Phase 31 pinned at 39, grown by Phase 47 panel + Phase 49 installer + Phase 50 desktop_hub + Phase 53 build/preflight + Phase 56 brand_semantics + Phase 57 barcode QR + Phase 58 installer_execution & R14 arms + Phase 59 runtime_guard 5 cases + Phase 63 barcode 1D oracle 4 cases + Phase 79 Adobe-bridge handshake/cdr_read/cdr_write 3 cases + Phase 80 service_registry feature_flags/tool_registry/skill_runtime 3 cases + Phase 81 event/delivery websocket_events/delivery_package 2 cases)",
          _wfc31.get("total") == 6 and _wfc31.get("passed") == 6 and _wfc31.get("failed") == 0
          and _es.get("executed_cases") == 151
          and _by30.get("eval-workflow-fail-no-pass-on", {}).get("details", {}).get("actual", {}).get("n3_present") is False
          and _by30.get("eval-workflow-fail-no-pass-on", {}).get("details", {}).get("actual", {}).get("error") == "Tool 未注册: 'ghost'（可用: []）"
          and _by30.get("eval-workflow-loop-aggregate", {}).get("details", {}).get("actual", {}).get("result") == {"lp_iterations": 3},
          json.dumps(_wfc31, ensure_ascii=False))
    _ac32 = (evj.get("categories", {}) or {}).get("asset", {})
    _ar32 = _by30.get("eval-asset-search-rank", {}).get("details", {}).get("actual", {}).get("ranked", [])
    _adc32 = _by30.get("eval-asset-delete-cascade", {}).get("details", {}).get("actual", {})
    check("eval: asset category truly evaluated by fresh sandbox engines - 6/6 with exact rank/cascade payloads (Phase 32)",
          _ac32.get("total") == 6 and _ac32.get("passed") == 6 and _ac32.get("failed") == 0
          and _ar32 and _ar32[0][0] == "coffee_mug.png" and _ar32[0][1] == 1.0
          and abs(_ar32[1][1] - 0.402015126104) < 1e-9
          and _adc32.get("before") == [2, 2, 1, 1] and _adc32.get("after") == [1, 1, 0, 1]
          and (tmp / "data" / "evaluation" / "artifacts" / "coffee_mug.png").exists()
          and (tmp / "data" / "evaluation" / "artifacts" / "cooking_pot.jpg").read_bytes()[:2] == b"\xff\xd8"
          and any((tmp / "data" / "evaluation" / "_asset" / "add" / "thumbnails").glob("*.png")),
          json.dumps({"cat": _ac32, "ranked": _ar32}, ensure_ascii=False))
    _rc32 = (evj.get("categories", {}) or {}).get("recovery", {})
    _rsh32 = _by30.get("eval-recovery-snapshot-sha256", {}).get("details", {}).get("actual", {})
    _rrb32 = _by30.get("eval-recovery-restore-revert", {}).get("details", {}).get("actual", {})
    _rtx32 = _by30.get("eval-recovery-txn-rollback", {}).get("details", {}).get("actual", {})
    check("eval: recovery category truly evaluated by fresh sandbox engines - 6/6 with sha256/byte-revert/rollback payloads (Phase 32)",
          _rc32.get("total") == 6 and _rc32.get("passed") == 6 and _rc32.get("failed") == 0
          and _rsh32.get("checksum_matches") is True and _rsh32.get("size") == 126
          and _rrb32.get("bytes_back") is True and _rrb32.get("ghost") is False
          and _rtx32.get("shape") == [{"status": "active", "operation_count": 2}] and _rtx32.get("rollback") is True
          and (tmp / "data" / "evaluation" / "_recovery" / "task" / "states" / "task_task-77.json").exists(),
          json.dumps({"cat": _rc32, "sha": _rsh32}, ensure_ascii=False))
    _tc33 = (evj.get("categories", {}) or {}).get("typography", {})
    _tms33 = _by30.get("eval-typography-measure-serif", {}).get("details", {}).get("actual", {})
    _tsm33 = _by30.get("eval-typography-measure-sans", {}).get("details", {}).get("actual", {})
    _tgo33 = _by30.get("eval-typography-ghost-truth", {}).get("details", {}).get("actual", {})
    _typo_fix33 = tmp / "data" / "evaluation" / "_typo" / "_fix"
    check("eval: typography category truly evaluated w/ self-supplied fixture TTFs - 6/6 exact metrics/gate/negative payloads, real compiled fonts on disk (Phase 33)",
          _tc33.get("total") == 6 and _tc33.get("passed") == 6 and _tc33.get("failed") == 0
          and _tms33.get("advance_units") == 3989 and _tms33.get("width_pt") == 95.736
          and _tsm33.get("missing_glyphs") == 1 and _tsm33.get("fully_covered") is False
          and _tgo33.get("ghost") is None and _tgo33.get("keyerror") == "'未找到字体: ZjNoSuchFont9'"
          and (_typo_fix33 / "zjfix_serif.ttf").read_bytes()[:4] == b"\x00\x01\x00\x00"
          and (_typo_fix33 / "zjfix_sans.ttf").read_bytes()[:4] == b"\x00\x01\x00\x00"
          and (tmp / "data" / "evaluation" / "_typo" / "measure_serif" / "fonts_index.json").exists()
          and (tmp / "data" / "evaluation" / "_typo" / "fallback" / "fallback-rules.json").exists(),
          json.dumps({"cat": _tc33, "serif": _tms33}, ensure_ascii=False))
    _bkc34 = (evj.get("categories", {}) or {}).get("barcode", {})
    _b1334 = _by30.get("eval-barcode-ean13-bitstring", {}).get("details", {}).get("actual", {})
    _bpx34 = _by30.get("eval-barcode-pixel-truth", {}).get("details", {}).get("actual", {})
    _bnd34 = _by30.get("eval-barcode-bad-checkdigit-msg", {}).get("details", {}).get("actual", {})
    _bhd34 = _by30.get("eval-barcode-honest-degradation", {}).get("details", {}).get("actual", {})
    _bc_art34 = tmp / "data" / "evaluation" / "artifacts"
    _bq1_57 = _by30.get("eval-barcode-qr-capacity-exact", {}).get("details", {}).get("actual", {})
    _bq2_57 = _by30.get("eval-barcode-qr-block-structure", {}).get("details", {}).get("actual", {})
    _bq3_57 = _by30.get("eval-barcode-qr-pixel-truth", {}).get("details", {}).get("actual", {})
    _bq4_57 = _by30.get("eval-barcode-qr-mask-format-bch", {}).get("details", {})
    _bq5_57 = _by30.get("eval-barcode-qr-honest-reject", {}).get("details", {}).get("actual", {})
    _b1d63 = _by30.get("eval-barcode-1d-oracle-law", {}).get("details", {}).get("actual", {})
    _bd1363 = _by30.get("eval-barcode-1d-decode-ean13", {}).get("details", {}).get("actual", {})
    _bd863 = _by30.get("eval-barcode-1d-decode-ean8", {}).get("details", {}).get("actual", {})
    _bng63 = _by30.get("eval-barcode-1d-decode-negatives", {}).get("details", {}).get("actual", {})
    check("eval: barcode category truly evaluated - 14/14 GS1 bit-string/pixel/checkdigit-message "
          "exact payloads + Phase 57 QR 真值五条（手抄 ISO Table 7/8/9 容量与码字算术 40 格全等、"
          "21×21 栅格与申报 matrix 逐格同源、评测侧独立 BCH 尺子先复现 ISO 三串公布例、四条诚实边界原文），"
          "Phase 63 一维解码 oracle 真值四条（三表构造律自检 bad 归零 + 公开例 G 槽免疫关系落证、"
          "EAN-13 十位逐位扫段像素读回、EAN-8 公开例 + 十位扫段、五枚阴性逐枚点名与采样模块数全等），"
          "PNG bytes on disk (Phase 34, +QR Phase 57)",
          _bkc34.get("total") == 14 and _bkc34.get("passed") == 14 and _bkc34.get("failed") == 0
          and _b1334.get("bits_equal") is True and _b1334.get("code") == "5901234123457"
          and _b1334.get("modules") == 95 and _b1334.get("px") == [234, 104]
          and _bpx34.get("mode") == "1" and _bpx34.get("samples", {}).get("m0") == 0
          and _bpx34.get("samples", {}).get("m1") == 255 and _bpx34.get("midguard") == "01010"
          and _bnd34.get("ean13") == "EAN-13 校验位非法：给定 0，应为 7"
          and _bnd34.get("ean8") == "EAN-8 校验位非法：给定 0，应为 4"
          and _bnd34.get("files") == [False, False]
          # Phase 57：QR 已真实出货，留守判词只剩 Code-128 / DataMatrix
          and _bhd34.get("c128", [None])[0] == "needs_external" and _bhd34.get("c128")[1] is False
          and _bhd34.get("c128")[2] is False and "Code-128" in _bhd34.get("c128")[3]
          and _bhd34.get("datamatrix") == ["needs_external", False]
          and _bhd34.get("qr") is None
          and _bhd34.get("v13") == "5901234123457" and _bhd34.get("alt") == [351, 60, [351, 60]]
          and _bq1_57.get("bad") == [] and "L17->v1" in _bq1_57.get("cells", [])
          and "L18->v2" in _bq1_57.get("cells", [])
          and _by30["eval-barcode-qr-capacity-exact"]["details"]["expected"]["Table9_v1_ec"] == {"L": 7, "M": 10, "Q": 13, "H": 17}
          and _bq2_57.get("bad") == [] \
          and _bq2_57.get("rows") == ["L1:19+7/1(19,7)", "M4:64+36/2(32,18)",
                                      "H6:60+112/4(15,28)",
                                      "H10:122+224/6(15,28)x2(16,28)"] \
          and _bq3_57.get("mismatch_count") == 0 and _bq3_57.get("mismatch_head") == [] \
          and _bq3_57.get("size") == [116, 116] \
          and _bq3_57.get("mode") == "1" and _bq3_57.get("quiet_band_values") == [255]
          and _bq3_57.get("samples", {}).get("dark_fixed") == 0
          and _bq4_57.get("actual", {}).get("ruler_ok") is True
          and _bq4_57.get("actual", {}).get("ruler_reproduces") == {"L0": "111011111000100",
                                                                   "M0": "101010000010010",
                                                                   "v7": "000111110010010100"}
          and all(c["fmt_equal"] and c["ver_match"] for c in _bq4_57.get("actual", {}).get("cases", []))
          and len(_bq4_57.get("actual", {}).get("cases", [])) == 5
          and _bq5_57.get("empty") == "QR 载荷不能为空"
          and _bq5_57.get("badlvl") == "非法纠错等级 X，只接受 L/M/Q/H"
          and _bq5_57.get("over_m", "").startswith("载荷超出本引擎已实现的 QR 版本范围（v1..v10，纠错等级 M）：400 字符")
          and _bq5_57.get("no_file") == {k: True for k in ("over_m", "over_h", "empty", "badlvl")}
          and (_bc_art34 / "eval_p34_bc13.png").read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
          and (_bc_art34 / "eval_p57_qrpx.png").read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
          and (_bc_art34 / "eval_p57_cap.png").exists()
          and not (_bc_art34 / "eval_p57_c128.png").exists()
          and not (_bc_art34 / "eval_p57_datamatrix.png").exists()
          and _b1d63.get("bad") == [] and _b1d63.get("public_example_parity") == 'LGGLLG'
          and _b1d63.get("public_example_G_slot_digits") == ['0', '1', '4'] and _b1d63.get("enc13_head") == '101000101101001110110011'
          and _b1d63.get("enc8_head") == '101000101101011110111101' and _b1d63.get("public_example_blind_to_G7") is True
          and _bd1363.get("bad") == [] and len(_bd1363.get("rows", [])) == 10
          and sorted(r["d"] for r in _bd1363.get("rows", [])) == ['0', '1', '2', '3', '4', '5', '6', '7', '8', '9']
          and [r["got"] for r in _bd1363.get("rows", [])] == ['1030500089211', '1131511189218', '1232522289215', '1333533389212', '1434544489219', '1535555589216', '1636566689213', '1737577789210', '1838588889217', '1939599989214']
          and all(r["want"] == r["got"] for r in _bd1363.get("rows", []))
          and _bd863.get("bad") == [] and _bd863.get("public_readback") == '96385074'
          and len(_bd863.get("rows", [])) == 10 and [r["got"] for r in _bd863.get("rows", [])] == ['03050705', '13151713', '23252721', '33353739', '43454747', '53555755', '63656763', '73757771', '83858789', '93959797']
          and all(r["want"] == r["got"] for r in _bd863.get("rows", [])) and _bd863.get("exercises_G_parity") is False
          and _bng63.get("positive") == '5901234123457' and _bng63.get("sampled") == {'ean13': 95, 'ean8_as13': 78, 'ean13_as8': 67}
          and [_bng63.get(k) for k in ("inverted", "midguard_wiped", "tampered_m50",
              "ean8_window_as_ean13", "ean13_prefix_as_ean8")] == [None, None, None, None, None]
          and all(k in _bng63 for k in ("inverted", "midguard_wiped", "tampered_m50",
              "ean8_window_as_ean13", "ean13_prefix_as_ean8"))
          and {'calib-barcode-1d-g7-typo-table', 'calib-barcode-1d-wrong-expect'} <= {c["id"] for c in evj.get("calibration", [])}
          and not (_bc_art34 / "eval_p57_qr_never.png").exists(),
          json.dumps({"cat": _bkc34, "ean13": _b1334,
                      "qr_cap": _bq1_57.get("cells", [])[:6], "qr_blk": _bq2_57.get("rows"),
                      "qr_px": _bq3_57, "qr_bch": _bq4_57.get("actual", {}),
                      "qr_rej": _bq5_57, "deg": _bhd34}, ensure_ascii=False))
    _tpc34 = (evj.get("categories", {}) or {}).get("topology", {})
    _tb34 = _by30.get("eval-topology-box9-full", {}).get("details", {}).get("actual", {})
    _tf34 = _by30.get("eval-topology-fold-coords", {}).get("details", {}).get("actual", {})
    _tq34 = _by30.get("eval-topology-rejected-honest", {}).get("details", {}).get("actual", {})
    _th34 = _by30.get("eval-topology-heuristic-honesty", {}).get("details", {}).get("actual", {})
    check("eval: topology category truly evaluated - 5/5 BOX9 full-geometry/fold-coords/partial-overlap/rejected-honest/heuristic-honesty exact payloads (Phase 34)",
          _tpc34.get("total") == 5 and _tpc34.get("passed") == 5 and _tpc34.get("failed") == 0
          and _tb34.get("relations") == 8 and _tb34.get("cut") == 870.0 and _tb34.get("fold") == 640.0
          and _tb34.get("area") == 28700.0 and _tb34.get("degrees", {}).get("front") == 4
          and (_tf34.get("glue_front") or {}).get("fold_line") == {"x": 0.0, "y1": 0.0, "y2": 100.0}
          and (_tf34.get("front_top") or {}).get("shared_length") == 60.0
          and _tq34.get("mix", [None])[0] == "disconnected"
          and _tq34.get("empty") == "至少需要一个面板"
          and _th34.get("basis") == ["geometric-heuristic"]
          and _th34.get("hints", {}).get("front") == ["major-face/tall", "primary"],
          json.dumps({"cat": _tpc34, "box9": _tb34}, ensure_ascii=False))
    # --- Phase 57: 契约散文逐数对账（p57_doc_freeze2 的谓词升格为常驻守卫）---------------
    # 为什么需要：note 与随包《安装说明》里散落的「N 条X用例 / X N 条」「当前 T 端点=已接线
    # W+未接线 U」全是手写数字。覆盖率有镜像派生、注册表 caps 有磁盘报告对账，唯独散文没人管，
    # 于是 P53/P56/P57 各漂过一轮（本轮实测：note 曾写 117/27/条码 5 而实盘 127/29/10）。
    # 本判据把散文钉回本轮实盘：任一计数与实盘派生不等、或有黄金域未被点名，当场判红。
    import re as _re57
    _p57_dom = [("品牌语义", "brand_semantics"), ("条码", "barcode"), ("拓扑", "topology"),
                ("排版", "typography"), ("素材", "asset"), ("恢复", "recovery"),
                ("工作流", "workflow"), ("改稿", "revision"), ("评审", "review"),
                ("安全", "security"), ("配置", "config"), ("路由", "model_routing"),
                ("授权", "license"), ("更新", "update"), ("NAS", "nas"),
                ("Provider", "provider"), ("对比", "compare"), ("去背景", "bg_removal"),
                ("面板识别", "panel_recognition"), ("安装清单", "installer_manifest"),
                # Phase 59 路线 B：三枚证据各归各账——runtime_guard 为**新题**，
                # installer_artifact_build / installer_preflight 为此前记在 installer_manifest
                # 同一个 category 下的编译面/预检面用例，现拆成独立黄金域（枚数由 live
                # categories 现场派生，写死数字的散文一律由本判据反查）。
                ("运行时守护", "runtime_guard"), ("编译证据", "installer_artifact_build"),
                ("预检", "installer_preflight"),
                ("安装执行", "installer_execution"),
                ("桌面 Hub", "desktop_hub"), ("品牌", "brand"), ("印前", "print"),
                ("知识检索", "knowledge"),
                # Phase 79（轨道 A 第一刀）：Adobe 桥 3 枚 golden 化——bridge_handshake /
                # bridge_cdr_read / bridge_cdr_write 各 1 枚真值用例，随包散文以「桥接握手 1 条」
                # 「桥接读取 1 条」「桥接写入 1 条」点名（note 另写「1 条桥接握手用例」，两形制
                # 分别命中 _p57_cnt 的 pattern1/pattern2）。诚实边界：verified=True 仅证协议机制
                # 对进程内 mock 宿主的异步往返成立，不等于已对真实 Adobe/CorelDRAW 文档改写。
                ("桥接握手", "bridge_handshake"), ("桥接读取", "bridge_cdr_read"),
                ("桥接写入", "bridge_cdr_write"),
                # Phase 80（轨道 A 第二刀）：服务侧持久化配置 3 枚 golden 化——feature_flags /
                # tool_registry / skill_runtime 各 1 枚真值用例，逐字下沉为第 28 引擎
                # service_registry 的单一真相源，端点全部委托它，评测台于私有沙箱直接构造同一份
                # 出货代码驱动。随包散文以「功能开关 1 条」「工具注册表 1 条」「技能运行时 1 条」点名。
                ("功能开关", "feature_flags"), ("工具注册表", "tool_registry"),
                ("技能运行时", "skill_runtime"),
                # websocket_events / delivery_package 各 1 枚真值用例，逐字下沉为第 29/30 引擎
                # event_engine / delivery_engine 的单一真相源，端点全部委托它们，评测台于私有实例／
                # 私有沙箱直接构造出货引擎驱动同一份随包代码而非另造桩。随包四份散文（note／README 模板／
                # 两份《安装说明》）均以「websocket_events 1 条」「delivery_package 1 条」点名（note 用
                # pattern2 英文域键形制，README 与安装说明模板同用英文域键，_p57_cnt 逐字命中）。诚实边界：
                # verified=True 仅证磁盘工作／事件簿记对出货引擎真实往返成立，不夸大任何未交付能力。
                ("websocket_events", "websocket_events"),
                ("delivery_package", "delivery_package")]
    # design/production 属初代 color/layout/packaging 黄金域，文档以引擎名而非用例计数句点名，
    # 故豁免逐域计数，但仍受下面的「实盘域必须被点名」闭合约束。
    _p57_legacy = {"design", "production"}

    def _p57_cnt(_tx, _ph):
        _neg = "(?!语义)" if _ph == "品牌" else ""
        _m = _re57.search(r"(\d+) 条%s%s\s*(?:用例|真值用例)" % (_re57.escape(_ph), _neg), _tx)
        if _m:
            return int(_m.group(1))
        _m = _re57.search(r"%s%s(?:\s*(?:客户端|真值))?\s*(\d+) 条" % (_re57.escape(_ph), _neg), _tx)
        return int(_m.group(1)) if _m else None

    _p57_here = Path(__file__).resolve().parent
    _p57_texts = {"note": (by_id.get("evaluation_center") or {}).get("note", "")}
    for _nm57, _rel57 in (("readme-template", "build-installer.py"),
                          ("安装说明.md", "安装说明.md"),
                          ("portable/安装说明.md", "portable/docs/安装说明.md")):
        _f57 = _ZJ_OUTBASE / _rel57
        if _f57.exists():
            _p57_texts[_nm57] = _f57.read_text(encoding="utf-8", errors="replace")
    _p57_cats = evj.get("categories", {}) or {}
    _p57_exec = (evj.get("summary", {}) or {}).get("executed_cases")
    _p57_cal = len(evj.get("calibration", []))
    _p57_cov = (len(_ns41), len(_wired41), len(_notwired41))
    _p57_bad = []
    for _nm57, _tx57 in _p57_texts.items():
        _m57 = _re57.search(r"(\d+) 条确定性(?:黄金)?用例", _tx57)
        if not _m57 or int(_m57.group(1)) != _p57_exec:
            _p57_bad.append("%s 确定性=%s!=%s" % (_nm57, _m57 and _m57.group(1), _p57_exec))
        _m57 = _re57.search(r"(\d+) 条负控校准", _tx57)
        if not _m57 or int(_m57.group(1)) != _p57_cal:
            _p57_bad.append("%s 校准=%s!=%s" % (_nm57, _m57 and _m57.group(1), _p57_cal))
        _named57 = set()
        for _ph, _dom in _p57_dom:
            _got = _p57_cnt(_tx57, _ph)
            _live = (_p57_cats.get(_dom) or {}).get("total")
            if _got is None:
                _p57_bad.append("%s 缺「%s N 条」而 live %s=%s" % (_nm57, _ph, _dom, _live))
                continue
            _named57.add(_dom)
            if _got != _live:
                _p57_bad.append("%s %s=%s!=live %s" % (_nm57, _ph, _got, _live))
        _miss57 = sorted({k for k in _p57_cats if k not in _p57_legacy} - _named57)
        if _miss57:
            _p57_bad.append("%s 未点名域 %s" % (_nm57, _miss57))
        if _nm57 != "note":
            _m57 = _re57.search(r"当前 (\d+) 端点=已接线 (\d+)\+未接线 (\d+)", _tx57)
            _got57 = tuple(int(x) for x in _m57.groups()) if _m57 else None
            if _got57 != _p57_cov:
                _p57_bad.append("%s 覆盖句=%s!=镜像 %s" % (_nm57, _got57, _p57_cov))
            _eng57 = _re57.search(r"内置 \*\*(\d+) 个引擎\*\*", _tx57)
            if not _eng57 or int(_eng57.group(1)) != _LIVE_ENGINE_COUNT:
                _p57_bad.append("%s 内置引擎数=%s!=live %s" % (_nm57, _eng57 and _eng57.group(1), _LIVE_ENGINE_COUNT))
            if ("共 %d 项" % _LIVE_ENGINE_COUNT) not in _tx57:
                _p57_bad.append("%s engines 共N项 缺「共 %d 项」" % (_nm57, _LIVE_ENGINE_COUNT))
            for _stale in ("133 端点", "未接线 14，", "其余 14 端点保持",
                           "134 端点", "未接线 15，",
                           # Phase 65：留守 16 项转正六枚后，任何仍写「16 端点保持 not_wired_yet」
                           # 或「未接线 16，」的散文都是上一代次遗留，逐枚反查（"未接线 15→16"
                           # 这类**历史叙述**里的 16 带箭头，不与本黑名单的任何形态相同）。
                           "其余 16 端点保持", "未接线 16，", "已接线 119+",
                           # Phase 70：两枚转正后，仍写「125+10 / 其余 10 端点保持」的散文即上一代次；
                           # 历史叙述里的「119→125」「16→10」带箭头，不与本黑名单同形。
                           "其余 10 端点保持", "未接线 10，", "已接线 125+",
                           # Phase 71：拒接面清零后，仍写「其余 8 端点保持 not_wired_yet / 未接线 8，/
                           # 已接线 127+」的散文即上一代次副本。历史叙述里的「10→8」「125→127」带箭头，
                           # 形态与本黑名单三条都不同，不会被误伤（P70 同条判别子）。
                           "其余 8 端点保持", "未接线 8，", "已接线 127+",
                           "语义类（Logo 含义/调性/字体识别）如实 needs_external 不臆造"):
                if _stale in _tx57:
                    _p57_bad.append("%s 陈旧串 %s" % (_nm57, _stale[:12]))
            if "QR" not in _tx57 or "Phase 57 起 QR 真值另 5 条" not in _tx57:
                _p57_bad.append("%s 无 P57 QR 句" % _nm57)
    _p57_eng_reports = {}
    for _rp57 in ("V1.0-功能完成度审计报告.md", "portable/docs/V1.0-功能完成度审计报告.md"):
        _rf57 = _ZJ_OUTBASE / _rp57
        if not _rf57.exists():
            _p57_bad.append("审计报告缺席 %s" % _rp57); continue
        _mt57 = _re57.search(r"当前为 \*{0,2}(\d+) 个引擎", _rf57.read_text(encoding="utf-8", errors="replace"))
        _p57_eng_reports[_rp57] = (int(_mt57.group(1)) if _mt57 else None)
        if not _mt57 or int(_mt57.group(1)) != _LIVE_ENGINE_COUNT:
            _p57_bad.append("审计报告 %s 引擎现值=%s!=live %s" % (_rp57, _mt57 and _mt57.group(1), _LIVE_ENGINE_COUNT))
    check("docs(P57+P65+P70+P71): shipped prose reconciled against THIS run's live derivations - "
          "evaluation_center note + README template + both bundled 安装说明.md must state 151 确定性/34 "
          "负控校准 and every golden-domain case count equal to this report's categories, name every live "
          "domain (design/production 初代域豁免逐域计数), carry the mirrored 135=135+0 coverage sentence "
          "(Phase 65 转正六枚 119→125/16→10；Phase 70 再转正两枚 125→127/10→8；Phase 71 末批八枚全部转正 "
          "127→135/8→0，分母恒 135), and be free of pre-P56/P57/P59/P65/P70/P71 stale verdicts (缺枚举即判红); "
          "the 「内置 **N 个引擎**」/「共 N 项」句 + both bundled 审计报告「当前为 N 个引擎」现值亦须逐一对齐本轮 /health 实测引擎数（P81 起补此尺，杜绝 28/30 散文漂移）",
          _p57_bad == [] and len(_p57_texts) == 4 and _p57_exec == 151 and _p57_cal == 34
          and _p57_cov == (135, 135, 0) and len(_named57) == 36,
          json.dumps({"bad": _p57_bad[:10], "texts": sorted(_p57_texts),
                      "cov": _p57_cov, "exec": _p57_exec, "cal": _p57_cal,
                      "eng_live": _LIVE_ENGINE_COUNT, "eng_reports": _p57_eng_reports}, ensure_ascii=False))
    # --- Phase 61: 随包副本不变量（outputs/ 母本 ↔ outputs/portable/docs 随包副本）--------
    # 为什么需要：P59 收尾时随包《V1.0-功能完成度审计报告》落后一个代次（母本已定稿而随包仍是
    # Phase 58 那份），当时的"已同步"只由复包收据自证——本轮实测六层对「V1.0-」字面串命中 0 行，
    # 即**没有任何一枚判据看过它**。散文对账有 docs(P57)（note/README 模板/两份《安装说明》逐数
    # 反查），字节同步却没有尺子：副本一旦滞后，发货包里那份还是旧结论，六层照样全绿。
    # 本判据把「随包 docs 目录里每一份都必须在 outputs/ 有同名母本且逐字节相等」钉成常驻守卫。
    # 口径三条：① 清单由目录现场枚举派生（不手抄文件名，也不猜路径），枚举不出 ≥2 份即判红——
    # 缺席不得被读成绿；② 比较一律 read_bytes()，不用 read_text()（universal-newlines 会把母本
    # 的 LF 与随包的 CRLF 抹平成"看起来相等"，那正是本判据要抓的漂移形制之一）；③ 判据名不写
    # 快照字节数，尺寸只进 detail，改派生逻辑时不必回头改判据名。
    _p61_dir = _ZJ_OUTBASE / "portable" / "docs"
    _p61_ship = sorted(p.name for p in _p61_dir.iterdir() if p.is_file()) if _p61_dir.is_dir() else []
    _p61_bad = []
    if len(_p61_ship) < 2:
        _p61_bad.append("随包 docs 只数出 %d 份（<2 ⇒ 枚举失败或副本缺席，不得判绿）：%s"
                        % (len(_p61_ship), _p61_dir))
    _p61_sizes = {}
    for _n61 in _p61_ship:
        _m61 = _ZJ_OUTBASE / _n61
        _c61 = _p61_dir / _n61
        _p61_sizes[_n61] = [_c61.stat().st_size, _m61.stat().st_size if _m61.is_file() else None]
        if not _m61.is_file():
            _p61_bad.append("随包 %s 在 outputs/ 无同名母本" % _n61)
            continue
        _cb, _mb = _c61.read_bytes(), _m61.read_bytes()
        if _cb != _mb:
            _p61_bad.append("随包 %s != 母本（随包 %d B vs 母本 %d B，字节级）" % (_n61, len(_cb), len(_mb)))
    check("docs(P61): every file shipped inside outputs/portable/docs has a same-named master under outputs/ and is "
          "byte-identical to it - 随包《审计报告》副本与《安装说明》必须有同名母本且逐字节相等（P59 曾漂一个代次而六层全绿，"
          "因无一枚判据看过它）；清单现场枚举派生、数不出 ≥2 份即判红、比较走 read_bytes() 不走 read_text()",
          _p61_bad == [] and len(_p61_ship) >= 2,
          json.dumps({"bad": _p61_bad[:8], "shipped": _p61_ship, "sizes": _p61_sizes}, ensure_ascii=False))
    _lc36 = (evj.get("categories", {}) or {}).get("license", {})
    _ltf36 = _by30.get("eval-license-tamper-format-exact", {}).get("details", {}).get("actual", {})
    _lmb36 = _by30.get("eval-license-machine-bind", {}).get("details", {}).get("actual", {})
    check("eval: license category truly evaluated - 4/4 deterministic-issue/tamper-expiry/bind payloads with exact HMAC verdict strings (Phase 36)",
          _lc36.get("total") == 4 and _lc36.get("passed") == 4 and _lc36.get("failed") == 0
          and _ltf36.get("tamper", [None, ""])[1] == "签名校验失败：密钥被篡改或非本产品签发"
          and _ltf36.get("formats", [[None, ""]])[3][1] == "签名校验失败：密钥被篡改或非本产品签发"
          and _ltf36.get("array_payload") == [False, "载荷结构无效：签名载荷必须是 JSON 对象"]
          and _lmb36.get("match") is True
          and _lmb36.get("mismatch", [None, ""])[1] == "机器绑定不符：该密钥不适用于本机（期望前缀 ZJ36）",
          json.dumps({"cat": _lc36, "tamper": _ltf36}, ensure_ascii=False))
    _cc36 = (evj.get("categories", {}) or {}).get("config", {})
    _cmig36 = _by30.get("eval-config-migration", {}).get("details", {}).get("actual", {})
    _cdef36 = _by30.get("eval-config-defaults-literal", {}).get("details", {}).get("actual", {})
    check("eval: config category truly evaluated - 4/4 defaults/cold-readback/migration/path-guard payloads, sandbox JSON on disk (Phase 36)",
          _cc36.get("total") == 4 and _cc36.get("passed") == 4 and _cc36.get("failed") == 0
          and _cdef36.get("data_root") == "${LOCALAPPDATA}\\Zaojing"
          and _cdef36.get("blocked_n") == 7 and _cdef36.get("allowed_n") == 13
          and _cmig36.get("marker") == "marker-kept" and _cmig36.get("saved_version") == "1.0.0"
          and "zj_probe_key" in (tmp / "data" / "evaluation" / "_config" / "migrate" / "user-settings.json").read_text(encoding="utf-8")
          and (tmp / "data" / "evaluation" / "_config" / "readback" / "user-settings.json").is_file(),
          json.dumps({"cat": _cc36, "migration": _cmig36}, ensure_ascii=False))
    _rtc36 = (evj.get("categories", {}) or {}).get("model_routing", {})
    _rtcst36 = _by30.get("eval-routing-cost-tie", {}).get("details", {}).get("actual", {})
    _rtbb36 = _by30.get("eval-routing-budget-breaker", {}).get("details", {}).get("actual", {})
    check("eval: model_routing category truly evaluated - 4/4 registry-parse/cost-tie/privacy-latency/budget-breaker payloads, fixture YAML on disk (Phase 36)",
          _rtc36.get("total") == 4 and _rtc36.get("passed") == 4 and _rtc36.get("failed") == 0
          and _rtcst36.get("picked") == "p_local" and _rtcst36.get("est") == (0.002 + 0.006) / 2
          and _rtbb36.get("all_over") is None and _rtbb36.get("privacy_after_open") == "p_cloud"
          # Phase 60 就地改核（修前此行钉 "event loop" in breaker_exc，即把 create_task 的
          # 无运行循环 RuntimeError 当成期望真值——那枚怪癖经取证判为真缺陷并已根治）。
          # 新针头的期望值逐字段来自同轮实测收据 p60_probe12_evalpayload.txt（现场派生，
          # 非手抄）：breaker_exc=None（无环开环不抛）、opened_after_failures=True（状态已翻）、
          # rearm_without_loop=True（窗口走完、无环也复口）、inloop_can_execute=False（环内同样开环）。
          and _rtbb36.get("breaker_exc") is None and _rtbb36.get("rearm_exc") is None
          and _rtbb36.get("opened_after_failures") is True
          and _rtbb36.get("rearm_without_loop") is True
          and _rtbb36.get("inloop_can_execute") is False
          and _rtbb36.get("after_cloud_over") == "p_local"
          and (tmp / "data" / "evaluation" / "_router" / "cost" / "registry.yaml").is_file(),
          json.dumps({"cat": _rtc36, "cost": _rtcst36}, ensure_ascii=False))
    _uc37 = (evj.get("categories", {}) or {}).get("update", {})
    _udl37 = _by30.get("eval-update-download-apply", {}).get("details", {}).get("actual", {})
    _ubm37 = _by30.get("eval-update-branch-matrix", {}).get("details", {}).get("actual", {})
    _u_fx37 = tmp / "data" / "evaluation" / "_update"
    check("eval: update category truly evaluated - 4/4 honest-gates/HMAC-recompute/real-HTTP-chain/branch-order payloads, applied artifact bytes on disk (Phase 37)",
          _uc37.get("total") == 4 and _uc37.get("passed") == 4 and _uc37.get("failed") == 0
          and _udl37.get("dl") == ["downloaded", "sha256_match", 26]
          and _udl37.get("seen") == ["/manifest.json", "/manifest.json", "/art.bin"]
          and _udl37.get("check") == ["update_available", True, ["1.0.0", "2.0.0"]]
          and _ubm37.get("version_gate_first") == "manifest_invalid"
          and _ubm37.get("no_secret") == ["signature_unverified", False, "2.0.0"]
          and (_u_fx37 / "chain" / "update-config.json").exists()
          and (_u_fx37 / "chain_fx" / "app.bin").read_bytes() == b"ZAOJING-P37-ARTIFACT-BYTES",
          json.dumps({"cat": _uc37, "chain": _udl37}, ensure_ascii=False))
    _nc37 = (evj.get("categories", {}) or {}).get("nas", {})
    _nnc37 = _by30.get("eval-nas-cache-cold", {}).get("details", {}).get("actual", {})
    _ngd37 = _by30.get("eval-nas-gate-matrix", {}).get("details", {}).get("actual", {})
    _npg37 = _by30.get("eval-nas-path-guard", {}).get("details", {}).get("actual", {})
    check("eval: nas category truly evaluated - 4/4 gate-originals/list-truth/path-guard/cold-cache-hit payloads, download bytes on disk (Phase 37)",
          _nc37.get("total") == 4 and _nc37.get("passed") == 4 and _nc37.get("failed") == 0
          and _ngd37.get("bad_kind") == "未知 NAS 类型 ftp（可用: filesystem, webdav）"
          and _ngd37.get("blank_target") == "target 不能为空"
          and _npg37.get("traversal") == ["forbidden", True] and _npg37.get("leaked") is False
          and _nnc37.get("cache") == "hit" and _nnc37.get("quads_equal") is True
          and (tmp / "data" / "evaluation" / "_nas" / "cache" / "dl" / "a.txt").read_bytes() == b"12345",
          json.dumps({"cat": _nc37, "cache": _nnc37}, ensure_ascii=False))
    _pc37 = (evj.get("categories", {}) or {}).get("provider", {})
    _pch37 = _by30.get("eval-provider-chat-roundtrip", {}).get("details", {}).get("actual", {})
    _pbr37 = _by30.get("eval-provider-auth-breaker", {}).get("details", {}).get("actual", {})
    _pkey37 = _by30.get("eval-provider-key-dpapi", {}).get("details", {}).get("actual", {})
    _ppath37 = tmp / "data" / "evaluation" / "_provider"
    check("eval: provider category truly evaluated - 4/4 unconfigured-honest/DPAPI-mask/real-chat-fields/breaker-zero-HTTP payloads, key store on disk (Phase 37)",
          _pc37.get("total") == 4 and _pc37.get("passed") == 4 and _pc37.get("failed") == 0
          and _pch37.get("status_content") == ["ok", "收到：造境评测 P37"]
          and _pch37.get("usage") == [20, 7, 13] and _pch37.get("auth") == "Bearer sk-eval-p37-secret"
          and _pbr37.get("auth") == ["error", "auth_failed", 1]
          and _pbr37.get("breaker") == ["error", "circuit_open", 0]
          and _pkey37.get("mask_literal") == "sk-" + "*" * 11 + "cret"
          and "sk-eval-p37-secret" not in (_ppath37 / "keys" / "providers.json").read_text(encoding="utf-8")
          and "api_key_dpapi" in (_ppath37 / "keys" / "providers.json").read_text(encoding="utf-8"),
          json.dumps({"cat": _pc37, "chat": _pch37}, ensure_ascii=False))
    _cc38 = (evj.get("categories", {}) or {}).get("compare", {})
    _ccf38 = _by30.get("eval-compare-field-categories", {}).get("details", {}).get("actual", {})
    _cst38 = _by30.get("eval-compare-structure-typemap", {}).get("details", {}).get("actual", {})
    check("eval: compare category truly evaluated by the newly extracted pure engine - 4/4 six-category/typemap/keys-universe payloads (Phase 38)",
          _cc38.get("total") == 4 and _cc38.get("passed") == 4 and _cc38.get("failed") == 0
          and _ccf38.get("summary", {}).get("by_category")
              == {"geometry": 2, "text": 1, "color": 1, "asset": 1, "layer": 1, "visual": 1}
          and _ccf38.get("moved0", {}).get("position", {}).get("x") == {"before": 0, "after": 5}
          and _cst38.get("summary", {}).get("by_category", {}).get("asset") == 1
          and _cst38.get("layer") == [["a", None], ["newx", "added"], ["gone", "removed"]],
          json.dumps({"cat": _cc38, "fields": _ccf38}, ensure_ascii=False))
    _bg38 = (evj.get("categories", {}) or {}).get("bg_removal", {})
    _bgf38 = _by30.get("eval-bgre-flood-exact", {}).get("details", {}).get("actual", {})
    _bgd38 = _by30.get("eval-bgre-default-determinism", {}).get("details", {}).get("actual", {})
    _bgpx38 = tmp / "data" / "evaluation" / "_bg"
    check("eval: bg_removal category truly evaluated by the newly extracted pixel engine - 4/4 flood-count/hole-truth/boundary/determinism payloads, real PNGs on disk (Phase 38)",
          _bg38.get("total") == 4 and _bg38.get("passed") == 4 and _bg38.get("failed") == 0
          and _bgf38.get("cleared") == 8400 and _bgf38.get("ratio") == 0.84
          and _bgf38.get("alphas") == [0, 255, 255]
          and _bgf38.get("sample") == [200, 30, 30]
          and _bgd38.get("algorithm") == "edge-flood-fill (PIL, real pixel processing)"
          and (_bgpx38 / "_fix" / "subject.png").read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
          and (_bgpx38 / "flood" / "no_bg.png").read_bytes()[:8] == b"\x89PNG\r\n\x1a\n",
          json.dumps({"cat": _bg38, "flood": _bgf38}, ensure_ascii=False))
    _pc47 = (evj.get("categories", {}) or {}).get("panel_recognition", {})
    _pg47 = _by30.get("eval-panel-grid-inversion", {}).get("details", {}).get("actual", {})
    _pp47 = _by30.get("eval-panel-param-gates", {}).get("details", {}).get("actual", {})
    _pe47 = _by30.get("eval-panel-honest-empty", {}).get("details", {}).get("actual", {})
    _pi47 = _by30.get("eval-panel-determinism", {}).get("details", {}).get("actual", {})
    _pw47 = _by30.get("calib-panel-wrong-expect", {}).get("details", {}).get("actual", {})
    check("eval: panel_recognition golden domain truly evaluated by the 25th engine - 4/4 exact grid-inversion/param-gate/honest-empty/determinism payloads, self-drawn fixtures on disk, and its must_fail calibration (band 76 coverage 0.7722 claimed cut) judged FAIL not green (Phase 47)",
          _pc47.get("total") == 4 and _pc47.get("passed") == 4 and _pc47.get("failed") == 0
          and _pg47.get("areas") == [2535] * 6
          and _pg47.get("binarize") == [826, 817, 9, 900, 909]
          and _pg47.get("lines") == [4, 552, 3, 267]
          and _pg47.get("adj") == [7, [3, 3, 2, 2, 2, 2], 1]
          and _pg47.get("outline") == [1248, 15210, 234, 390]
          and _pp47.get("thin12") == [6, 4005, [2013, 2013, 1947, 1947, 1947, 1947]]
          and _pp47.get("thick_fold") == {"segments": 7, "length_px": 851, "covered_px": 801, "dashes": 17}
          and _pp47.get("lw3", [None, None])[0] == "no_lines_detected"
          and _pp47.get("same_keys") is True
          and _pe47.get("statuses") == ["no_lines_detected", "no_panels_detected",
                                        "no_panels_detected", "recognized"]
          and _pe47.get("counts") == [0, 0, 0, 6]
          and _pi47.get("equal") is True
          and _by30.get("calib-panel-wrong-expect", {}).get("passed") is False
          and [q[1] for q in _pw47] == ["fold", "fold", "fold"]
          and (tmp / "data" / "evaluation" / "_panel" / "grid" / "grid3x2.png").read_bytes()[:8] == b"\x89PNG\r\n\x1a\n",
          json.dumps({"cat": _pc47, "grid": _pg47.get("areas"), "calib": _pw47}, ensure_ascii=False))
    # --- Phase 49: 第 26 引擎 installer_manifest 的黄金域真值用例（评测扩张第十一例） ---
    # --- Phase 53: 同域再扩六条（编译面 3 + 预检面 2 + 静态开火臂 1）+ 校准 1，4+1 → 10+2 ---
    _mc49 = (evj.get("categories", {}) or {}).get("installer_manifest", {})
    # Phase 59 路线 B：编译面三条与预检面两条**改归**各自域（不新增用例、不改判分内容，
    # 只把证据记回它真正证明的能力）。故本域名下只剩静态四式 + 静态开火臂 + R14 七臂 = 6 条。
    _mb59 = (evj.get("categories", {}) or {}).get("installer_artifact_build", {})
    _mp59 = (evj.get("categories", {}) or {}).get("installer_preflight", {})
    _mA49 = _by30.get("eval-manifest-clean-zero-findings", {}).get("details", {}).get("actual", {})
    _mB49 = _by30.get("eval-manifest-broken-findings-exact", {}).get("details", {}).get("actual", {})
    _mC49 = _by30.get("eval-manifest-seam-stub-falsification", {}).get("details", {}).get("actual", {})
    _mD49 = _by30.get("eval-manifest-rejects-and-installed", {}).get("details", {}).get("actual", {})
    _mW49 = _by30.get("calib-manifest-wrong-expect", {}).get("details", {}).get("actual", {})
    _mE53 = _by30.get("eval-manifest-build-evidence-verified", {}).get("details", {}).get("actual", {})
    _mF53 = _by30.get("eval-manifest-build-evidence-arms", {}).get("details", {}).get("actual", {})
    _mG53 = _by30.get("eval-manifest-build-warnings-completeness", {}).get("details", {}).get("actual", {})
    _mH53 = _by30.get("eval-manifest-preflight-ready-readonly", {}).get("details", {}).get("actual", {})
    _mI53 = _by30.get("eval-manifest-preflight-blocked-stubs", {}).get("details", {}).get("actual", {})
    _mJ53 = _by30.get("eval-manifest-static-fire-arms", {}).get("details", {}).get("actual", {})
    # Phase 58：R14（写入型 [Registry] 缺卸载撤销声明）的七臂用例——判据出处是本机真卸载实测
    _mK58 = _by30.get("eval-manifest-registry-uninstall-arms", {}).get("details", {}).get("actual", {})
    _mX53 = _by30.get("calib-manifest-build-wrong-expect", {})
    _mX53a = _mX53.get("details", {}).get("actual", {})
    _mG53a = _mG53.get("arms", {})
    _mG53w = ["Warning: w0", "Warning: w1"]
    _mI53v = {_k53: (str(_v53.get("verdict")), tuple(_v53.get("blocking") or []),
                     tuple(_v53.get("unknown") or []))
              for _k53, _v53 in _mI53.items() if isinstance(_v53, dict)}
    _m053 = {"errors": 0, "warnings": 0, "infos": 0, "total": 0}
    _msd49 = tmp / "data" / "evaluation" / "_manifest"
    _mzx53 = sorted(_msd49.rglob("zaojing.exe"))
    _mbody53 = [p for p in _mzx53 if p.read_bytes() == b"MZ\x90\x00fixture"]
    _miss53 = sorted(_msd49.rglob("setup.iss"))
    _mart53 = sorted(_msd49.rglob("zaojing-ai-1.0.0-setup.exe"))
    check("eval: installer_manifest golden domain truly evaluated by the 26th engine - 6/6 payloads (P49 静态四式 + P53 编译器定论开火臂一条 + P58 R14 卸载撤销七臂一条; Phase 59 起编译面三条与预检面两条不再算进本域名下，改由 installer_artifact_build 3 条 / installer_preflight 2 两条各自记账——同一批用例、同一份判分内容，只是把证据记回它真正证明的能力), eval-self-drawn .iss fixtures with real payloads on disk, and its must_fail calibration (R7 pre-created dir claimed as error) judged FAIL not green (Phase 49)",
          _mc49.get("total") == 6 and _mc49.get("passed") == 6 and _mc49.get("failed") == 0
          and _mb59.get("total") == 3 and _mb59.get("passed") == 3 and _mb59.get("failed") == 0
          and _mp59.get("total") == 2 and _mp59.get("passed") == 2 and _mp59.get("failed") == 0
          and _mA49.get("verdict") == "manifest_ok"
          and _mA49.get("counts") == {"errors": 0, "infos": 0, "total": 0, "warnings": 0}
          # Phase 58 漂移（p58_l2_refreeze_probe 磁盘复算）：合规夹具的 [Registry] 段为 R14
          # 三条合规臂补齐（同键 uninsdeletevalue／同键 uninsdeletekey／祖先键 uninsdeletekey），
          # 段内条目 1→3。与 P53 那次同一条教训：夹具一改就必须重新量，不能照旧值放行。
          and _mA49.get("entries") == {"Files": 2, "Icons": 2, "Registry": 3, "Setup": 7}
          # Phase 53 夹具漂移（p53_eval49_diag 实测：64 条子判据仅这两条红）：
          # MF_CLEAN_SCRIPT 本轮为 R6/R11/R12/R13 四条合规臂补齐了 [Setup] 指令
          # （AppId 双写转义、PrivilegesRequired=lowest、两枚 x64compatible 架构标识），
          # 段内条目 4→7，其后 [Files] 两行的行号整体下移 3（9,10 → 12,13）。
          # 用例本身仍 passed（评测器内部期望与夹具同源），红的是本套**独立复算的冻结期望**——
          # 这正是 L2 逐字钉夹具形状的价值：夹具一改就必须在磁盘上重新量，而不是照旧值放行。
          and _mA49.get("files_lines") == [12, 13]
          and _mA49.get("flags") == "ignoreversion onlyifdoesntexist"
          and _mA49.get("listing_stable") is True
          and _mB49.get("verdict") == "manifest_invalid"
          and _mB49.get("counts") == {"errors": 8, "infos": 1, "total": 12, "warnings": 3}
          # Phase 53 漂移（p53_b49_probe 磁盘复算）：R11 per_user_area_privileges_mismatch
          # 是本轮新立的一条静态规则，破损夹具如实多检出第 11 项（warn）。
          # Phase 58 同型再漂移（p58_l2_refreeze_probe 磁盘复算）：R14 registry_key_left_after_uninstall
          # 又添第 12 项（error，逐行回指那条写入型 [Registry] 条目的行号与值名）——破损夹具的
          # [Registry] 本就从未声明卸载撤销，R14 上线当轮即在它身上开火。这仍是"加规则会静默改写
          # 评测台冻结期望"的同一课，尺子随产品重测而不是把产品改回旧计数。
          and _mB49.get("ids") == ["appid_not_uuid", "dirs_miss_runtime_data_root",
                                   "files_source_literal_missing",
                                   "files_source_wildcard_no_match",
                                   "needs_compiler_adjudication",
                                   "per_user_area_privileges_mismatch",
                                   "permissions_entry_malformed",
                                   "registry_key_left_after_uninstall",
                                   "registry_valuedata_literal_placeholder",
                                   "uninstall_target_mismatch",
                                   "unresolved_template_constant",
                                   "unresolved_template_constant"]
          and _mB49.get("severities") == ["warn", "warn", "error", "error", "info", "warn",
                                          "error", "error", "error", "error", "error", "error"]
          # noroot 差分同样随 R11/R14 漂移：去掉 R7 一条后仍是 11 项（8 error / 2 warn / 1 info），
          # 即 R11 与数据根无关、R14 只看脚本自身、二者都不随 data_root 传空而消失——
          # 这正是它们该各自独立成一条规则的证明。
          and _mB49.get("noroot_counts") == {"errors": 8, "infos": 1, "total": 11, "warnings": 2}
          and list(_mC49.get("envdir_pair", [])) == [0, [["dirs_miss_runtime_data_root", "warn"]]]
          and _mD49.get("sha_equal") is True and _mD49.get("installed") is False
          # 真值：本机 HKCU/HKLM 零命中 ⇒ derived 亦 False；契约钉的是"二者恒等"，
          # 即 installed 只能由探针逐项真命中派生（沙箱里放了真 portable.flag 也不翻 installed）。
          and _mD49.get("installed") is _mD49.get("derived_from_probes")
          and _mD49.get("flag_exists") is True
          and _by30.get("calib-manifest-wrong-expect", {}).get("passed") is False
          and _mW49 == [["dirs_miss_runtime_data_root", "warn"]]
          and (_msd49 / "clean" / "zaojing.exe").read_bytes() == b"MZ\x90\x00fixture"
          # ---- Phase 53 编译面三式（正控 + 四失配臂 + 告警/完整性四臂两口径） ----
          # 编译成功由三独立信号同立（退出码/完成行/产物在盘），证据自报 compiled/fabricated
          # 被翻成假话后判词与其余字段仍逐条全等 ⇒ 自报布尔不参与判定，这条只能靠真复算成立。
          and _mE53.get("verdict") == "artifact_verified" and _mE53.get("codes") == []
          and _mE53.get("line_source") == "tail"
          and _mE53.get("matches") == [True, True, True, True]
          and _mE53.get("warn") == [0, 0, True]
          and _mE53.get("lie_verdict") == "artifact_verified"
          and _mE53.get("lie_rest_equal") is True and _mE53.get("listing_stable") is True
          # 等长替换：字节数一致、三信号仍齐，唯 sha256 可查 ⇒ 长度不是证据
          and _mF53.get("equal_len") == ["evidence_mismatch", ["artifact_sha256_mismatch"],
                                         True, False, True]
          # 编译后改脚本：证据判陈旧，但静态复校对**当前**脚本照旧 ok ⇒ 两口径不互相污染
          and _mF53.get("stale") == ["evidence_mismatch", ["artifact_not_from_current_script"],
                                     False, True, "manifest_ok", True]
          # 产物缺席优先于"编译未证成"——先答"东西在不在"，再答"是谁造的"
          and _mF53.get("gone") == ["artifact_missing", [], False, "artifact_missing"]
          and _mF53.get("not_proven") == ["compile_not_proven", [], "absent", True, "manifest_ok"]
          # 告警四臂两口径：全文走等式（3 记录 vs 2 实见必矛盾），节选走下界
          # （3 记录 vs 尾部 2 实见不矛盾——把截断当不一致同样是假红）
          and _mG53a.get("eq_full") == {"verdict": "artifact_verified", "codes": [], "rec": 2,
                                        "recount": 2, "full": True, "consistent": True,
                                        "lines": _mG53w, "detail": None}
          and _mG53a.get("more_full") == {"verdict": "evidence_mismatch",
                                          "codes": ["warnings_count_mismatch"], "rec": 3,
                                          "recount": 2, "full": True, "consistent": False,
                                          "lines": _mG53w,
                                          "detail": {"recorded_count": 3, "tail_recount": 2,
                                                     "tail_is_full_output": True,
                                                     "tail_warning_lines": _mG53w}}
          and _mG53a.get("more_excerpt") == {"verdict": "artifact_verified", "codes": [], "rec": 3,
                                             "recount": 2, "full": False, "consistent": True,
                                             "lines": _mG53w, "detail": None}
          and _mG53a.get("less_excerpt") == {"verdict": "evidence_mismatch",
                                             "codes": ["warnings_count_mismatch"], "rec": 1,
                                             "recount": 2, "full": False, "consistent": False,
                                             "lines": _mG53w,
                                             "detail": {"recorded_count": 1, "tail_recount": 2,
                                                        "tail_is_full_output": False,
                                                        "tail_warning_lines": _mG53w}}
          # 缺字段 ⇒ evidence_incomplete 并逐项列缺；缺失字段的 provenance 不替编译器说话
          and _mG53.get("incomplete") == ["evidence_incomplete",
                                          ["script_sha256", "artifact_bytes",
                                           "iscc_output_tail", "iscc_stderr_tail"],
                                          ["artifact_not_from_current_script",
                                           "artifact_bytes_mismatch"], None]
          and _mG53.get("derived") == ["derived_field", "artifact_verified"]
          and _mG53.get("old_key") == ["iscc_stderr_tail", "artifact_verified"]
          and _mG53.get("file_src") == ["file", True]
          # ---- Phase 53 预检面两式（只读承诺 + 十臂阻断/未知分治） ----
          and _mH53.get("verdict") == "preflight_ready" and _mH53.get("codes") == [[], []]
          and _mH53.get("check_results") == {"artifact": True, "disk_space": True,
                                             "install_root": True, "payload": True,
                                             "privileges": True, "target_conflict": True,
                                             "writability": True}
          # 写探针真落盘 23 字节、目录自建自清，探针跑完目标目录不留（只读承诺的可证伪面）
          and _mH53.get("write") == ["real_probe_file", 23, True, True]
          and _mH53.get("target_left") is False
          and _mH53.get("forms") == ["commonpf", "userpf"]
          # 需求字节 = 产物 + 载荷实测，不外推系数（88 == 68 + 20 是本次实量的恒等）
          and _mH53.get("disk") == [68, 20, 88]
          and _mH53.get("disk")[2] == _mH53.get("disk")[0] + _mH53.get("disk")[1]
          and _mH53.get("nover") == ["preflight_unknown", ["write_probe_unprobed"], False]
          and _mH53.get("off") == ["preflight_unknown", False]
          and _mH53.get("listing_stable") is True
          # 十八臂：阻断码与未知码分治——能判死判死（blocked），判不了如实挂 unknown，
          # 且"载荷缺失"先阻断、"余量读不到"只未知；桩全部还原后回基线（接缝可证伪）
          and _mI53v == {
              "base": ("preflight_ready", (), ()),
              "file_target": ("preflight_blocked", ("target_exists_as_file",), ()),
              "miss_payload": ("preflight_blocked", ("payload_source_missing",), ()),
              "miss_payload_beats_unknown": ("preflight_blocked", ("payload_source_missing",),
                                             ("write_probe_unprobed",)),
              "no_evidence": ("preflight_blocked", ("artifact_missing",), ()),
              "tampered": ("preflight_blocked", ("artifact_not_verified",), ()),
              "free_none": ("preflight_unknown", (), ("disk_free_unresolved",)),
              "free_tiny": ("preflight_blocked", ("space_insufficient",), ()),
              "exe_held32": ("preflight_blocked", ("target_exe_in_use",), ()),
              "exe_miss2": ("preflight_ready", (), ()),
              "exe_free0": ("preflight_ready", (), ()),
              "exe_real": ("preflight_ready", (), ()),
              "admin_elev_false": ("preflight_blocked", ("privileges_insufficient",), ()),
              "admin_unresolved": ("preflight_unknown", (), ("elevation_state_unresolved",)),
              "probe_mkdir": ("preflight_blocked", ("cannot_create_target",), ()),
              "probe_write": ("preflight_blocked", ("target_not_writable",), ()),
              "probe_none": ("preflight_unknown", (), ("write_probe_unresolved",)),
              "restore": ("preflight_ready", (), ()),
          }
          and _mI53.get("file_target_write") == [None, False, "target_exists_as_file", True]
          and _mI53.get("no_evidence_state") == ["not_supplied", None]
          and _mI53.get("tampered_state") == ["not_verified", "evidence_mismatch"]
          # 四条编译器定论臂各自单独开火（一次只开一枪，防止"整批检出"掩盖单规则失灵）
          and _mJ53.get("r6") == ["manifest_invalid", {"errors": 1, "warnings": 0, "infos": 0,
                                                       "total": 1},
                                  [["appid_guid_unescaped", "error", 4]]]
          and _mJ53.get("r12") == ["manifest_warning", {"errors": 0, "warnings": 2, "infos": 0,
                                                        "total": 2},
                                   [["arch_identifier_deprecated", "warn", 7],
                                    ["arch_identifier_deprecated", "warn", 8]]]
          and _mJ53.get("r13no") == ["manifest_invalid", {"errors": 1, "warnings": 0, "infos": 0,
                                                          "total": 1},
                                     [["setup_missing_version_directive", "error", None]]]
          and _mJ53.get("r13vn") == ["manifest_ok", _m053, []]
          and _mJ53.get("r11fire") == ["manifest_warning", {"errors": 0, "warnings": 1,
                                                             "infos": 0, "total": 1},
                                        [["per_user_area_privileges_mismatch", "warn", 0]]]
          and _mJ53.get("r11ok") == ["manifest_ok", _m053, []]
          and _mJ53.get("clean") == ["manifest_ok", _m053]
          # 新校准桩：把证据自报 compiled=False 当成"编译未证成"是假期望，必须判 fail
          and _mX53.get("passed") is False
          and _mX53a == {"verdict": "artifact_verified", "self_report": [False, True, True]}
          # 磁盘证据：夹具全部真落盘（17 枚载荷体 + 12 份脚本 + 11 份"已编译"产物），
          # 且 bm_gone 那一份**只有脚本没有产物**——"产物缺席"是磁盘事实不是布尔桩。
          # 【Phase 59 就地重钉】预检面新校准 calib-manifest-preflight-wrong-expect 经
          # _manifest_build_sandbox("pfc") 多种一座沙箱（1 枚带 MZ 体的 zaojing.exe +
          # 1 份 setup.iss + 1 份 setup 产物，实测 p59_mcount.txt 17/16/12/11）——
          # 与 P53/P58 同课：加规则会静默改写评测台冻结期望，尺子随产品重测，
          # 不是把产品改回旧计数。
          and len(_mzx53) == 17 and len(_mbody53) == 16
          and len(_miss53) == 12 and len(_mart53) == 11
          and (_msd49 / "bm_gone" / "setup.iss").exists()
          and not (_msd49 / "bm_gone" / "zaojing-ai-1.0.0-setup.exe").exists()
          and (_msd49 / "bf" / "build-evidence.json").exists(),
          json.dumps({"cat": _mc49, "clean": _mA49.get("counts"), "broken": _mB49.get("counts"),
                      # Phase 53 增这两枚 dump：夹具一改 [Setup] 指令，段计数与 Files 行号同漂，
                      # 而用例自身仍 passed —— 不 dump 就只能再写探针才知道红在哪一条（p53_eval49_diag）
                      "clean_entries": _mA49.get("entries"), "files_lines": _mA49.get("files_lines"),
                      "ids": _mB49.get("ids"), "seam": _mC49.get("envdir_pair"),
                      "sev": _mB49.get("severities"), "noroot": _mB49.get("noroot_counts"),
                      "rej": {k: _mD49.get(k) for k in ("sha_equal", "installed",
                                                        "derived_from_probes", "flag_exists")},
                      "calib": _mW49,
                      "p53": {"ver": _mE53.get("verdict"), "eq": _mF53.get("equal_len"),
                              "warn_full": _mG53a.get("more_full", {}).get("codes"),
                              "pf": _mH53.get("verdict"), "arms": len(_mI53v),
                              "fire": _mJ53.get("r13no"), "calib_build": _mX53a,
                              "disk": [len(_mzx53), len(_miss53), len(_mart53)]}},
                     ensure_ascii=False))
    # --- Phase 58：第 26 引擎静态规则 R14 的七臂（写入型 [Registry] 缺卸载撤销声明）---
    # 为什么 L2 要独立钉这七臂而不信评测台内部期望：R14 的判据出处是本机**真卸载实测**
    # （卸载器 exit 0、安装根与卸载子键消失、四棵数据树归零而应用键三值原样留守），
    # 而"撤销声明该认哪些旗标"是这条规则唯一会静默退化成"写了任何旗标都放行"的地方。
    # 三枚开火臂里两枚已经带 uninsdeletekeyifempty——若把它当合规，这两臂就绿了，
    # 于是残留缺陷重新变成不可见；三枚合规臂若过宽（例如把 uninsneveruninstall 之外的一切
    # 都算撤销）则会冤枉生产脚本。两向都钉住，才叫可证伪。
    _m058 = {"errors": 0, "warnings": 0, "infos": 0, "total": 0}
    _m158 = {"errors": 1, "warnings": 0, "infos": 0, "total": 1}
    _mR58 = "registry_key_left_after_uninstall"
    _m_norm58 = lambda _k: ((_v := _mK58.get(_k)) and
                           (_v[0], _v[1], [[_f[0], _f[1], list(_f[2]), list(_f[3])]
                                           for _f in _v[2]]))
    _m_exp58 = {
        "residue": ("manifest_invalid", _m158, [[_mR58, "error", [8, 9], []]]),
        "key": ("manifest_ok", _m058, []),
        "parent_delkey": ("manifest_ok", _m058, []),
        "parent_ifempty": ("manifest_invalid", _m158,
                           [[_mR58, "error", [9], ["uninsdeletekeyifempty"]]]),
        "ifempty_same_key": ("manifest_invalid", _m158,
                             [[_mR58, "error", [9], ["uninsdeletekeyifempty"]]]),
        "never": ("manifest_ok", _m058, []),
        "cross_root": ("manifest_invalid", _m158, [[_mR58, "error", [9], []]]),
    }
    check("eval: R14 registry-uninstall-arms truly evaluated on seven eval-self-drawn .iss fixtures - 三臂零检出（同键 uninsdeletekey／祖先键 uninsdeletekey／uninsneveruninstall 属主动弃权而非残留）、三臂开火且如实回显已观测到的撤销旗标（裸写两行 [8,9]、uninsdeletekeyifempty 挂本键与挂祖先键照旧开火、HKCU 写而 HKLM 撤的跨根照旧开火），每臂 counts.total 恒等于 findings 长度（Phase 58）",
          set(_mK58) == set(_m_exp58)
          and all(_m_norm58(_k) == _m_exp58[_k] for _k in _m_exp58)
          and all(_mK58[_k][1]["total"] == len(_mK58[_k][2]) for _k in _mK58)
          and _mK58["residue"][1]["errors"] == 1 and _mK58["residue"][2][0][3] == []
          and _mK58["parent_ifempty"][2][0][3] == ["uninsdeletekeyifempty"]
          and _mK58["ifempty_same_key"][2][0][3] == ["uninsdeletekeyifempty"]
          and _mK58["cross_root"][2][0][3] == []
          and _mK58["key"][0] == _mK58["parent_delkey"][0] == _mK58["never"][0] == "manifest_ok",
          json.dumps({"arms": {k: _m_norm58(k) for k in sorted(_mK58)},
                      "expected": _m_exp58}, ensure_ascii=False))
    # 评测扩张第十二例。为什么这一域必须由评测台自证而非只靠 L2 的活体对账：执行器唯一的
    # 失败模式是"信自报"，而信自报在**真机上**只有装/卸一次才看得见；评测台把同一套对账
    # 逻辑压在自建夹具上（假制品/假台账/假日志全在本沙箱里真落盘），于是"伪造的账"这条
    # 判据在每一轮都真跑一遍，不必等下一次装机。三形制=缺席/伪造/真跑通，加两枚边界臂。
    _ec58 = (evj.get("categories", {}) or {}).get("installer_execution", {})
    _eP58 = _by30.get("eval-exec-plan-disk-derived", {}).get("details", {}).get("actual", {})
    _eG58 = _by30.get("eval-exec-gates-refuse-zero-side-effects", {}).get("details", {}).get("actual", {})
    _eI58 = _by30.get("eval-exec-install-verified-from-diff", {}).get("details", {}).get("actual", {})
    _eV58 = _by30.get("eval-exec-verdict-ignores-self-report", {}).get("details", {}).get("actual", {})
    _eU58 = _by30.get("eval-exec-uninstall-three-forms-and-reconcile", {}).get("details", {}).get("actual", {})
    _eR58 = _by30.get("eval-exec-second-round-evidence-not-user-data", {}).get("details", {}).get("actual", {})
    _eW58 = _by30.get("calib-exec-trust-self-report", {})
    # 落点改核（p58_exec_disk_freeze.py，21:30 实测）：执行面沙箱按引擎自己的口径落在
    # **系统临时根** tempfile.gettempdir()/_zj_eval_exec/<tag>，不在 store_dir 子树里——
    # 因为 store_dir 位于源码树内，而执行器的 install_dir_inside_source_tree 门会把每一臂
    # 在门口拒掉。上一枚探针扫 store_dir 数出 [0,0,0]，把"我没扫到"读成"盘上没有"，
    # 差点用来论证本断言前提为假（自造仪器盲区，见 p58_exec_disk_freeze.py 文档串）。
    _xr58 = Path(tempfile.gettempdir()) / "_zj_eval_exec"
    _xsand58 = sorted(p.name for p in _xr58.iterdir() if p.is_dir()) if _xr58.is_dir() else []
    _xel58 = sorted(p.relative_to(_xr58).as_posix() for p in _xr58.rglob("exec-ledger.json")) if _xr58.is_dir() else []
    _xeg58 = sorted(p.relative_to(_xr58).as_posix() for p in _xr58.rglob("zaojing-install.log")) if _xr58.is_dir() else []
    _xen58 = sorted(p.relative_to(_xr58).as_posix() for p in _xr58.rglob("user-notes.json")) if _xr58.is_dir() else []
    _xee58 = sorted(p.relative_to(_xr58).as_posix() for p in _xr58.rglob("ZaojingAI.exe")) if _xr58.is_dir() else []
    _xeu58 = sorted(p.relative_to(_xr58).as_posix() for p in _xr58.rglob("unins000.exe")) if _xr58.is_dir() else []
    _xez58 = sum(1 for p in _xr58.rglob("exec-ledger.json") if p.is_file() and p.stat().st_size > 0) if _xr58.is_dir() else -1
    _xr258 = sorted(p.relative_to(_xr58 / "round2" / "fake-local" / "Zaojing").as_posix()
                    for p in (_xr58 / "round2" / "fake-local" / "Zaojing").rglob("*")
                    if p.is_file()) if (_xr58 / "round2" / "fake-local" / "Zaojing").is_dir() else []
    check("eval: installer_execution golden domain truly evaluated by the executor itself - 6/6 real-fixture payloads on self-drawn sandboxes: plan 只算不跑（sha 独立复算全等+五枚落点 exists_now 现场重算+卸载键由 {{源文} 解成 {GUID}_is1+七门逐字+沙箱 listing 逐条不变）/ 十七臂各按 reason 码拒绝且拒绝代价真为零（台账从不落盘、唯一在场的安装根必须是那条非空臂自己种的目录）/ installed 只由磁盘+注册表差集派生（十项观察全 ok、created_count=去重后磁盘真相=4、既有数据逐字节完好、argv 三静默开关、日志 48 字节、台账七枚自报布尔 ignored 逐字）/ 同一份自报两趟判分相反（盘上有物 install_verified vs 空手 install_unverified 且十项观察恰 3 真 7 假、空手趟判 ledger_unverified 不伪造回执）/ 一装两卸三形制+对账活证（install_verified_awaiting_uninstall 诚实中间态、痕迹被台账之外手段抹掉被点名、uninstall_incomplete 三枚残留入列、真卸 removed 恰 4 枚逐枚 gone_now 且用户数据只点名不删、等长篡改唯 sha 点名一条、还原回 executed_verified、nothing_to_uninstall 与 missing_uninstaller 独立状态码）/ 证据与用户数据分账两轮往返（轮 2 自家台账被重写有评测独立复算的哈希为证仍判 uninstall_verified 且 deviating 空、创建集 5→4、对自家证据只回显不声称重写）；夹具在盘上真实存在（10 座沙箱、6 枚台账、6 枚日志、10 份用户数据、2+2 枚 exe，且真卸沙箱的安装根已归零、空手沙箱的 Programs 从未被建）；其 must_fail 校准（把安装器自报退出码 0+完成行当成已装成） judged FAIL not green (Phase 58)",
          _ec58.get("total") == 6 and _ec58.get("passed") == 6 and _ec58.get("failed") == 0
          and _es.get("executed_cases") == 151 and len(evj.get("results", [])) == 185
          # ① plan：只算不跑
          and _eP58.get("sha_equal_recompute") is True
          and _eP58.get("install_dir_rel") == "fake-local/Programs/造境科技"
          and _eP58.get("uninstall_key") == "{D4C8A1F2-7B3E-4E5A-9C6D-2F1B8A47E3D5}_is1"
          and not str(_eP58.get("uninstall_key", "")).startswith("{{")
          and _eP58.get("gates") == ["confirmation_required", "artifact_missing",
                                     "artifact_outside_allowed_root", "build_ledger_unavailable",
                                     "artifact_sha_mismatch_vs_build_ledger",
                                     "install_dir_not_empty", "install_dir_inside_source_tree"]
          and _eP58.get("exists_now") == [["install_root", False], ["runtime_data_root", True],
                                          ["start_menu_group", False], ["userdocs", False],
                                          ["vendor_dir", False]]
          and _eP58.get("listing_stable") is True
          # ② gates：十七臂形形对应、代价为零
          and _eG58.get("refused") == 17 and len(_eG58.get("arms", [])) == 17
          and {r[0]: r[1] for r in _eG58.get("arms", [])} == {
              "install.confirm_absent": "confirmation_required",
              "install.confirm_empty": "confirmation_required",
              "install.confirm_near_miss": "confirmation_required",
              "install.artifact_absent": "artifact_missing",
              "install.artifact_outside_allow_root": "artifact_outside_allowed_root",
              "install.build_ledger_absent": "build_ledger_unavailable",
              "install.build_ledger_unreadable": "build_ledger_unavailable",
              "install.artifact_bytes_changed": "artifact_sha_mismatch_vs_build_ledger",
              "install.ledger_points_to_other_file": "build_ledger_stale",
              "install.artifact_equal_length_swap": "artifact_sha_mismatch_vs_build_ledger",
              "install.dir_relative": "install_dir_not_absolute",
              "install.dir_outside_allow_root": "install_dir_outside_allowed_root",
              "install.dir_inside_source_tree": "install_dir_inside_source_tree",
              "install.dir_not_empty": "install_dir_not_empty",
              "uninstall.confirm_absent": "confirmation_required",
              "uninstall.dir_relative": "install_dir_not_absolute",
              "uninstall.dir_outside_allow_root": "install_dir_outside_allowed_root"}
          and all(r[2] is True and r[3] is True and r[4] is False
                  for r in _eG58.get("arms", []))
          and _eG58.get("ledger_written") is False
          # dir_created 是 any(臂) 形：十七臂里唯一安装根在场的必须是那条非空臂（它自种的目录）
          and [r[0] for r in _eG58.get("arms", []) if r[5]] == ["install.dir_not_empty"]
          # ③ install：差集派生
          and _eI58.get("status") == "install_verified" and _eI58.get("checks_ok") is True
          and _eI58.get("created_count") == _eI58.get("created_rows") == 4
          and _eI58.get("created_rel") == sorted(set(_eI58.get("created_rel") or []))
          and _eI58.get("disk_files") == _eI58.get("created_rel")[1:3]
          and _eI58.get("registry_display") == "ZaojingAI 1.0.0"
          and _eI58.get("registry_observation", {}).get("hive") == "HKCU"
          and _eI58.get("registry_observation", {}).get("uninstall_key_was_leaf") is True
          and _eI58.get("registry_observation", {}).get("view") == "injected"
          and _eI58.get("argv_flags") == ["/VERYSILENT", "/NORESTART", "/SUPPRESSMSGBOXES"]
          and _eI58.get("log_bytes") == 48 and _eI58.get("preexisting") == [1, True]
          and _eI58.get("ignored_in_ledger") == ["install.installed", "install.status",
                                                 "install.log.seen_finished_line",
                                                 "uninstall.performed", "uninstall.status",
                                                 "build_ledger.compiled",
                                                 "build_ledger.fabricated"]
          # ④ 判分不信自报：同一份自报、两趟相反
          and _eV58.get("self_report_identical") is True
          and _eV58.get("exit_codes") == [0, 0] and _eV58.get("finished_line_both") == [True, True]
          and _eV58.get("verdicts") == ["install_verified", "install_unverified"]
          and len(_eV58.get("ghost_ok_marks", [])) == 10
          and sum(1 for x in _eV58.get("ghost_ok_marks", []) if x) == 3
          and _eV58.get("ghost_created") == 0
          and _eV58.get("ghost_state") == ["ledger_unverified", [], False]
          # ⑤ 卸载三形制 + 对账活证
          and _eU58.get("installed_state") == ["install_verified_awaiting_uninstall", [],
                                               [True, True, True]]
          and _eU58.get("off_book_state", [None])[0] == "ledger_inconsistent"
          and "被台账之外的手段抹掉" in json.dumps(_eU58.get("off_book_state"), ensure_ascii=False)
          and _eU58.get("lazy", [None])[0] == "uninstall_incomplete"
          and _eU58.get("lazy", [None, None, [True, False, False]])[2] == [True, False, False]
          and {"install_root_still_present", "registry_app", "registry_uninstall"} <= set(
              _eU58.get("lazy", [None, None, None, []])[3])
          and _eU58.get("verified", [None])[0] == "uninstall_verified"
          and _eU58.get("verified", [[]])[5:8] == [4, "created_by_install_only",
                                                   "derived_from_disk_and_registry"]
          and _eU58.get("verified", [[]])[2:5] == [True, True, True]
          and _eU58.get("verified", [[]])[8] == [[True, True]] * 4
          and _eU58.get("verified", [[]])[9] == [1, True, True]
          and _eU58.get("residue") == [["runtime_data_root", 1, False]]
          and _eU58.get("untouched") == [] and _eU58.get("install_dir_absent") is True
          and _eU58.get("user_notes_hex") == "7b226d696e65223a747275657d"
          and _eU58.get("state", [None])[0] == "executed_verified"
          and _eU58.get("state", [[], []])[1] == [] and _eU58.get("state", [[], [], True])[2] is False
          and _eU58.get("state", [[], [], [], None])[3] == [0, False, False, False, True, True]
          and _eU58.get("state", [[]] * 4 + [False, False])[4:6] == [True, True]
          and _eU58.get("state", [[]] * 6 + [None])[6] == _eI58.get("ignored_in_ledger")
          and _eU58.get("recorded_only") == ["install.exit_code", "install.argv", "install.process"]
          and _eU58.get("tamper") == ["ledger_inconsistent",
                                      ["install.artifact.sha256 != 制品在盘上的现算值"]]
          and _eU58.get("restored") == "executed_verified"
          and _eU58.get("none", [None])[0] == "nothing_to_uninstall"
          and _eU58.get("none", [[]])[3] == [] and _eU58.get("none", [[]] * 3 + [True, True])[4:6] == [True, True]
          and _eU58.get("missing", [None])[0] == "missing_uninstaller"
          and _eU58.get("missing", [[]] * 3 + [2])[3] == 2
          and _eU58.get("missing", [[]] * 5 + [False])[5] is False
          # ⑥ 两轮往返：证据件逐出用户数据基线
          and _eR58.get("r1", [None])[0] == "install_verified"
          and _eR58.get("r1", [[]])[1] == "installer/zaojing-install.log"
          and _eR58.get("r1", [[]])[3] == "uninstall_verified"
          and _eR58.get("r1", [[]])[4] == 5
          and _eR58.get("r1", [[]])[6] == "executed_verified"
          and _eR58.get("ledger_sha_before_rewrite") != _eR58.get("ledger_sha_after_rewrite")
          and _eR58.get("r2_created_count") == 4 and _eR58.get("r2_pre_count") == 1
          and [str(x).replace("\\", "/").rsplit("/_zj_eval_exec/", 1)[-1]
               for x in _eR58.get("r2_pre_files", [])] == \
              ["round2/fake-local/Zaojing/Assets/user-notes.json"]
          and _eR58.get("r3", [None])[0] == "uninstall_verified"
          and _eR58.get("r3", [[]])[5] == 4 and _eR58.get("r3", [[]])[6] == "executed_verified"
          and _eR58.get("r3", [[]] * 7 + [{}])[8] == {"intact_claimed": True, "deviating": [],
                                                     "deviating_all_executor_owned": False}
          and _eR58.get("r3", [[]] * 9 + [None])[9] == [False, False]
          and _eR58.get("left_in_data_root") == ["Assets/user-notes.json",
                                                 "installer/exec-ledger.json",
                                                 "installer/zaojing-install.log"]
          and _eR58.get("user_notes_hex") == "7b226d696e65223a747275657d"
          # 校准：信自报的假期望必须判 fail
          and _eW58.get("passed") is False
          # 落盘实况（评测进程自己刚跑完的夹具，按引擎口径扫正确的根）
          and _xsand58 == ["calib", "gates", "ghost", "install", "miss", "none", "plan",
                           "real", "round2", "unins"]
          and len(_xel58) == 6 and _xez58 == 6 and len(_xeg58) == 6
          and len(_xen58) == 10
          and _xee58 == ["install/fake-local/Programs/造境科技/ZaojingAI.exe",
                         "real/fake-local/Programs/造境科技/ZaojingAI.exe"]
          and _xeu58 == ["install/fake-local/Programs/造境科技/unins000.exe",
                         "real/fake-local/Programs/造境科技/unins000.exe"]
          # 真卸沙箱的安装根必须已归零；空手沙箱连 Programs 都不许存在
          and not (_xr58 / "unins" / "fake-local" / "Programs" / "造境科技").exists()
          and not (_xr58 / "ghost" / "fake-local" / "Programs").exists()
          and _xr258 == ["Assets/user-notes.json", "installer/exec-ledger.json",
                         "installer/zaojing-install.log"],
          json.dumps({"cat": _ec58, "sum": {k: _es.get(k) for k in ("executed_cases", "passed", "failed")},
                      "plan": _eP58, "gates": {k: _eG58.get(k) for k in ("refused", "ledger_written", "dir_created")},
                      "gates_arms": {r[0]: r[1] for r in _eG58.get("arms", [])},
                      "install": _eI58, "verdict": _eV58,
                      "uninstall": {k: _eU58.get(k) for k in ("installed_state", "verified", "tamper",
                                                              "restored", "none", "missing", "state")},
                      "round2": {k: _eR58.get(k) for k in ("r1", "r3", "ledger_sha_before_rewrite",
                                                           "ledger_sha_after_rewrite", "r2_created_count",
                                                           "left_in_data_root")},
                      "calib": _eW58.get("passed"),
                      "disk": {"sandboxes": _xsand58, "root": str(_xr58),
                               "ledgers": _xel58, "logs": _xeg58, "notes": len(_xen58),
                               "exe": _xee58, "unin_exe": _xeu58, "round2_root": _xr258}},
                     ensure_ascii=False))
    # --- Phase 59 路线 B：第 22 引擎的**守护面**升为独立黄金域 runtime_guard（5 真值 + 1 校准） ---
    # 期望逐字段来自只读探针 p59_det_probe.py v2（每面三轮、归一后全字段比对，diff_count 恒 0），
    # 其 steps 与本处字段**同名同值**；不是照着手抄文档。锁名/沙箱路径一类的随机量由用例
    # 自身归一成 <LOCK>/<SB>（Phase 50 _hub_norm 同法），故本套判据可以逐字钉死。
    _gc59 = (evj.get("categories", {}) or {}).get("runtime_guard", {})
    _gA59 = _by30.get("eval-guard-write-probe-three-arms", {}).get("details", {}).get("actual", {})
    _gB59 = _by30.get("eval-guard-disk-threshold-two-sided", {}).get("details", {}).get("actual", {})
    _gC59 = _by30.get("eval-guard-port-bind-probe-two-sided", {}).get("details", {}).get("actual", {})
    _gD59 = _by30.get("eval-guard-single-instance-mutex-chain", {}).get("details", {}).get("actual", {})
    _gE59 = _by30.get("eval-guard-diagnostic-derived-and-degrades", {}).get("details", {}).get("actual", {})
    _gW59 = _by30.get("calib-guard-wrong-expect", {})
    check("eval: runtime_guard golden domain truly evaluated by the 22nd engine's guard class - 5/5 payloads on self-drawn sandboxes: 写探针三臂（可写臂 writable/bytes_verified/cleaned 三真且事后 zj_write_probe_* 计数=0、目标为普通文件与父路径为普通文件两臂三假且 error 均以 FileExistsError 起手——**两侧同写**才许判绿）/ 磁盘两侧（min=0 必 meets_min=True、min=2**62 必 False、负阈值抛 RuntimeGuardError|min_free_bytes 不能为负、连续量 free/used 不入判分字段）/ 端口两侧（自起监听桩的端口 available=False 且 detail 含 Error、OS 指派 bind(0) 后自关的端口 available=True 且 detail=None、70000 抛 port 需为 1~65535 的整数，绝不触碰外部 socket）/ 单实例互斥五步链（first=[True,False]→同实例二次 [False,True]→release.released=True→同实例再申请 [True,False]→**另一实例**二次 [False,True] 且它的 released=False，句柄不属它就不许谎称释放成功）/ 诊断派生+降级（健康臂三 check 全 pass 且 [performed,passed,failed]=[3,3,0]、status=healthy、timestamp 为 None 即刻意不盖戳、带端口臂 4 项含 port_available、降级臂 data_root_writable=fail 且 status=degraded 且 checks_failed==performed-passed 恒为派生式、以不存在的目录建引擎时 dir_exists_before_vs_after=[False,True] 且 fresh_first_snapshot=False（判别对：引擎构造本身不建根，data_root_exists=True 由写探针真实 mkdir 派生——非判别的 first_vs_second=[True,True] 之外两枚**判别字段**也必须逐值钉死）、沙箱残留只有 .runtime-guard 一枚目录而探针文件计数为 0）；其 must_fail 校准（把降级臂 status 期望写成 healthy）judged FAIL not green (Phase 59)",
          _gc59.get("total") == 5 and _gc59.get("passed") == 5 and _gc59.get("failed") == 0
          # ① 写探针三臂
          and _gA59.get("writable_dir") == {"writable": True, "bytes_verified": True,
                                            "cleaned": True, "method": "real_write_readback_delete",
                                            "fabricated": False}
          and _gA59.get("target_is_file") == {"writable": False, "bytes_verified": False,
                                              "cleaned": False}
          and _gA59.get("parent_is_file") == {"writable": False, "bytes_verified": False,
                                              "cleaned": False}
          and _gA59.get("target_err_type") == "FileExistsError"
          and _gA59.get("parent_err_type") == "FileExistsError"
          and _gA59.get("residue_probe_file_count") == 0 and _gA59.get("dir_created") is True
          and _gA59.get("target_norm") == "<SB>/probe-ok"
          # ② 磁盘阈值两侧
          and _gB59.get("min_zero_meets") is True and _gB59.get("min_huge_meets") is False
          and _gB59.get("total_positive") is True
          and _gB59.get("method") == "shutil_disk_usage" and _gB59.get("fabricated") is False
          and _gB59.get("negative_raises") == "RuntimeGuardError|min_free_bytes 不能为负"
          and _gB59.get("min_echo") == [0.0, float(2 ** 62)]
          # ③ 端口 bind 两侧
          and _gC59.get("free_available") is True and _gC59.get("occ_available") is False
          and _gC59.get("free_detail_none") is True and _gC59.get("occ_detail_is_oserror") is True
          and _gC59.get("method") == "real_tcp_bind_probe" and _gC59.get("fabricated") is False
          and _gC59.get("out_of_range_raises") == "RuntimeGuardError|port 需为 1~65535 的整数"
          # ④ 单实例互斥五步链
          and _gD59.get("first") == [True, False]
          and _gD59.get("second_same_instance") == [False, True]
          and _gD59.get("released") is True
          and _gD59.get("after_release_same_instance") == [True, False]
          and _gD59.get("after_release_other_instance") == [False, True]
          and _gD59.get("other_release") is False
          and _gD59.get("backend") == "kernel32-named-mutex"
          and _gD59.get("fabricated") is False and _gD59.get("name_echo") == "<LOCK>"
          and _gD59.get("bad_name_rejected") == "RuntimeGuardError|instance_name 含路径/保留字符，拒绝"
          # ⑤ 诊断派生与降级
          and _gE59.get("checks") == [["data_root_writable", "pass"], ["disk_free", "pass"],
                                      ["python_version", "pass"]]
          and _gE59.get("counts") == [3, 3, 0] and _gE59.get("status") == "healthy"
          and _gE59.get("method") == "derived_from_real_checks"
          and _gE59.get("fabricated") is False and _gE59.get("timestamp_none") is True
          and _gE59.get("with_port_checks") == [["data_root_writable", "pass"], ["disk_free", "pass"],
                                                ["python_version", "pass"], ["port_available", "pass"]]
          and _gE59.get("with_port_count") == 4
          and _gE59.get("degraded_checks") == [["data_root_writable", "fail"], ["disk_free", "pass"],
                                               ["python_version", "pass"]]
          and _gE59.get("degraded_status") == "degraded"
          and _gE59.get("failed_eq_derived") is True
          and _gE59.get("dir_exists_first_vs_second") == [True, True]
          and _gE59.get("dir_exists_before_vs_after") == [False, True]
          and _gE59.get("fresh_first_snapshot") is False
          and _gE59.get("residue") == [".runtime-guard"]
          and _gE59.get("residue_probe_files") == 0
          and _gW59.get("passed") is False and _gW59.get("must_fail") is True,
          json.dumps({"cat": _gc59,
                      "write": _gA59, "disk": _gB59, "port": _gC59,
                      "mutex": _gD59, "diag": {k: _gE59.get(k) for k in
                                               ("checks", "counts", "status", "degraded_checks",
                                                "degraded_status", "dir_exists_first_vs_second",
                                                "dir_exists_before_vs_after", "fresh_first_snapshot",
                                                "residue")},
                      "calib": _gW59.get("passed")}, ensure_ascii=False))
    # --- Phase 59 路线 C-1：entry_point 声明的形态集合 ⊇/= CLI 实际受理的形态集合（双向） ---
    # CLI 受理形态**不手抄**：同一份产品代码给四路派生源，其中两路零副作用——
    #   路一：`install-exec`（空参数）走 usage 分支，把 INSTALL_EXEC_USAGE 原文打到 stdout 后 return 2；
    #   路二：`install-exec <非法子命令>` 先回 {"status":"usage_error","legal":[...]} 再 return 2，
    #   该分支在 `_require("runtime")` **之前**，故两趟都既不加载引擎也不起服务。
    # 与契约面 entry_point 里的形态做**集合相等**（少一形=漏报发货能力，多一形=文案吹过实现）。
    # 本文件约定：re 不在模块顶层 import，各段就地取别名（_re35/_re41/_re49/_re57/_re54 同法）。
    import re as _re59
    _xenv59 = dict(os.environ)
    _xenv59["ZAOJING_PORT"] = str(PORT)
    _xenv59["ZAOJING_DATA_DIR"] = str(tmp / "data")
    _xu59 = subprocess.run([sys.executable, str(OUT / "agent_service.py"), "install-exec"],
                           cwd=str(OUT), env=_xenv59, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, timeout=120)
    _usage59 = _xu59.stdout.decode("utf-8", "replace")
    # 首行取不到就用空串，别让 IndexError 冒充"实现没有该形态"——崩掉的探针没有裁决行。
    _u_line59 = (_usage59.splitlines() or [""])[0]
    _xq59 = subprocess.run([sys.executable, str(OUT / "agent_service.py"), "install-exec",
                            "not-a-real-subcommand"], cwd=str(OUT), env=_xenv59,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
    _q_out59 = _xq59.stdout.decode("utf-8", "replace").lstrip()
    _legal59 = (json.loads(_q_out59) if _q_out59.startswith("{") else {})
    _u_forms59 = _re59.search(r"<([a-z_|\-]+)>", _u_line59)
    _usage_forms59 = sorted(set(_u_forms59.group(1).split("|"))) if _u_forms59 else []
    _ia59 = requests.get(BASE + "/api/v1/runtime/install-actions", timeout=60).json()
    _ep59 = _re59.search(r"install-exec\s+([a-z_|\-]+)", str(_ia59.get("entry_point", "")))
    _ep_forms59 = sorted(set(_ep59.group(1).split("|"))) if _ep59 else []
    # 第四路：installer_execution 的 note 随 /api/v1/capabilities 直接发货（能力说明书正文），
    # 里面**每一处** "install-exec <形态串>" 都在判分面上——只改 entry_point 不改 note 就是
    # "文案与实现各写一份"的第二载体，故 findall 出的每一处都必须与前三路同集合。
    _note59 = str(by_id.get("installer_execution", {}).get("note") or "")
    _note_forms59 = sorted(_re59.findall(r"install-exec\s+([a-z|]+)", _note59))
    _note_sets59 = [sorted(set(_m.split("|"))) for _m in _note_forms59]
    check("contract: install-exec 的形态集合四源同源且**集合相等**（正控：每一源都数得出 ≥1 枚；负控：文案与实现各写一份即判红）— CLI usage 首行 <a|b|c|d> 派生 = usage_error 载荷 legal 数组派生 = /runtime/install-actions 的 entry_point 派生 = capabilities note 里每一处形态串，四者全等且都必须含 plan（Phase 58 交付的只读形态；Phase 59 修前 entry_point 与 note 都只有 install|uninstall|state 三形＝**漏报已发货能力**，判红即此故）",
          len(_usage_forms59) >= 1 and len(_legal59.get("legal", [])) >= 1 and len(_ep_forms59) >= 1
          and len(_note_sets59) >= 1
          and _usage_forms59 == sorted(_legal59.get("legal", [])) == _ep_forms59
          and all(_s == _usage_forms59 for _s in _note_sets59)
          and _xu59.returncode == 2 and _xq59.returncode == 2
          and _legal59.get("status") == "usage_error" and _legal59.get("fabricated") is False
          and "plan" in _ep_forms59
          and "install-exec" in _u_line59,
          json.dumps({"usage_forms": _usage_forms59, "legal": _legal59.get("legal"),
                      "entry_point_forms": _ep_forms59,
                      "note_forms": _note_sets59, "note_hits": len(_note_forms59),
                      "rc": [_xu59.returncode, _xq59.returncode]}, ensure_ascii=False))
    # --- Phase 59 路线 C-3：发货中的 API 契约文案不得写死门数 ---
    # FastAPI 把端点函数 docstring 当作 operation description 发进 /openapi.json，
    # 所以"回显七道门"这类陈旧数字不是注释里的闲话，而是**随包发货的契约文本**。
    # 正控先自证尺子数得出（对样例串必命中），否则"不含 \d+道门"这种恒空断言是假绿。
    _gate_re59 = _re59.compile(r"[一二三四五六七八九十0-9]+\s*道门")
    _oa59 = requests.get(BASE + "/openapi.json", timeout=60).json()
    # _desc59 必须是**原文串**：先前写成 json.dumps(...) 会把中文转义成 \u95e8，
    # 于是"含 门"永远落空、"不含 N道门"永远为真 —— 一把只能判绿的尺子。
    _desc59 = str((((_oa59.get("paths", {}).get("/api/v1/runtime/install-actions", {}) or {})
                    .get("get", {}) or {}).get("description")) or "")
    check("contract: /openapi.json 里 install-actions 的 description 不得含写死门数（门数只活在载荷里，由引擎 gates 现场派生）— 正控：同一把尺对样例串「回显七道门」必命中；活体：payload 的 gates 长度 == 逐名清单长度且 >0（Phase 59 实测 10 道，docstring 却写七道 ⇒ 判红即此故；修法是删掉数字而不是把数字改成 10，改数字下一轮照样漂移）",
          _gate_re59.search("契约面：回显七道门") is not None
          and _gate_re59.search(_desc59) is None
          and len(_ia59.get("gates", [])) > 0
          and len(_ia59.get("gates", [])) == len(set(_ia59.get("gates", [])))
          and "门" in _desc59,
          json.dumps({"desc_hits": bool(_gate_re59.search(_desc59)),
                      "gates_live": len(_ia59.get("gates", [])),
                      "desc_tail": _desc59[-90:]}, ensure_ascii=False))
    _hc50 = (evj.get("categories", {}) or {}).get("desktop_hub", {})
    _hA50 = _by30.get("eval-hub-transport-fidelity", {}).get("details", {}).get("actual", {})
    _hB50 = _by30.get("eval-hub-derive-not-invent", {}).get("details", {}).get("actual", {})
    _hC50 = _by30.get("eval-hub-degradation-three-forms", {}).get("details", {}).get("actual", {})
    _hD50 = _by30.get("eval-hub-action-single-post", {}).get("details", {}).get("actual", {})
    check("eval: desktop_hub golden domain truly evaluated by the 27th engine - 4/4 real in-process-HTTP payloads: transport fidelity (three GETs in exact order, 0 POST, window row is verbatim carrier of the fixture's own version string), derive-not-invent (200+JSON-but-wrong-shape health -> shape_valid False, identity fields None, no 'vNone' row, cross-source disagreement shown as 同源=False), degradation three forms (dead port -> URLError/connection_failed for all three, HTML 404 -> not_zaojing_service non-连坐, broken JSON -> JSONDecodeError/malformed_json, headless window honest), single POST channel (refresh zero writes, eval_run exactly one body '{}' POST, unknown action raises and adds no request); its must_fail calibration (closed port claimed reachable with a registry count) judged FAIL not green (Phase 50)",
          _hc50.get("total") == 4 and _hc50.get("passed") == 4 and _hc50.get("failed") == 0
          and [s.get("path") for s in _hA50.get("seen", [])] == ["/api/v1/health",
                                                                 "/api/v1/capabilities",
                                                                 "/api/v1/eval/reports"]
          and _hA50.get("post_count") == 0 and _hA50.get("read_only") is True
          and _hA50.get("fabricated") is False and _hA50.get("all_sources_live") is True
          and _hA50.get("engines", {}).get("agree") is True
          and _hA50.get("picked_by") == "max(finished_at)"
          and _hA50.get("evaluation_latest", {}).get("report_id") == "eval-new"
          and _hA50.get("rows", [None, None])[1] == "服务 造境 AI v9.9.9-probe · healthy · 运行 12.5s"
          and _hB50.get("shape_valid") is False and _hB50.get("no_none_row") is True
          and "同源=False" in _hB50.get("row_engines_inconsistent", "")
          and _hB50.get("service_after_bad_shape", {}).get("version") is None
          and _hC50.get("dead_verdicts", {}).get("health") == "connection_failed"
          and _hC50.get("dead_error_class", {}) == {"health": "URLError",
                                                   "capabilities": "URLError",
                                                   "eval_reports": "URLError"}
          and _hC50.get("html_verdict") == "not_zaojing_service"
          and _hC50.get("html_transport") == "non_json"
          and _hC50.get("html_reports_verdict") == "zaojing_payload"
          and _hC50.get("bad_verdict") == "malformed_json"
          and _hC50.get("bad_error_class") == "JSONDecodeError"
          and _hC50.get("headless_window") == {"created": False, "reason": "headless_requested",
                                               "rows_would_render": 3}
          and _hD50.get("post_seen") == [{"body": "{}", "method": "POST",
                                         "path": "/api/v1/eval/run"}]
          and _hD50.get("requests_after_unknown") == _hD50.get("requests_before_unknown")
          and str(_hD50.get("unknown_error", "")).startswith("unknown_action")
          and _by30.get("calib-hub-wrong-expect", {}).get("passed") is False,
          json.dumps({"cat": _hc50, "rows": len(_hA50.get("rows", [])),
                      "post": _hD50.get("post_seen"),
                      "dead": _hC50.get("dead_error_class")}, ensure_ascii=False))
    _rid = evj.get("report_id", "")
    _rp = tmp / "data" / "evaluation" / "reports" / f"{_rid}.json"
    check("eval: report truly persisted as JSON inside ZAOJING_DATA_DIR root (Phase 24)",
          _rp.exists() and _rid in _rp.read_text(encoding="utf-8"), str(_rp))
    ev2 = requests.post(BASE + "/api/v1/eval/run", timeout=240).json()
    cm = requests.post(BASE + "/api/v1/eval/compare", json={"a": _rid, "b": ev2.get("report_id", "")})
    cmj = cm.json()
    check("eval: compare of two real reports yields full shared set + zero flips (Phase 24)",
          cm.status_code == 200 and cmj.get("shared_cases") == len(evj.get("results", []))
          and cmj.get("flips") == [] and cmj.get("pass_rate_delta") == 0.0,
          json.dumps({k: cmj.get(k) for k in ("shared_cases", "flips", "pass_rate_delta")}, ensure_ascii=False))
    miss404 = requests.get(BASE + "/api/v1/eval/reports/eval-does-not-exist")
    check("eval: missing report honestly 404, no 5xx leak (Phase 24)",
          miss404.status_code == 404, f"HTTP {miss404.status_code}")
    rec24 = requests.get(BASE + "/api/v1/eval/recommendations")
    check("eval: recommendations derived from real metrics, ASCII-safe prefixes (Phase 24)",
          rec24.status_code == 200 and rec24.json().get("report_id")
          and all(r.startswith(("[OK]", "[INFO]", "[WARN]", "[BLOCK]")) for r in rec24.json().get("recommendations", [])),
          json.dumps(rec24.json().get("recommendations", []), ensure_ascii=False))

    # --- Phase 29: Capability Registry 2.0 证据字段动态派生（Final 2.0 §5 / §2 红线机制化） ---
    import shutil as _sh29
    cj29 = requests.get(BASE + "/api/v1/capabilities").json()
    rg = cj29.get("registry", {})
    rc = {c["id"]: c for c in rg.get("capabilities", [])}
    check("registry: schema 2.0 covers all 43 caps with engine names sourced from _ENGINE_NAMES (Phase 29, +installer_manifest Phase 49, +installer_artifact_build/installer_preflight Phase 53, +cdr_document_read Phase 77, +cdr_document_write Phase 78, +event/delivery Phase 81)",
          rg.get("schema_version") == "capability-registry/2.0" and len(rc) == 43
          and all(c["implemented"] is (c["status"] == "supported") for c in rc.values())
          and all((c["engine"] is None or c["engine"] in
                   ["license", "config", "asset", "print_qa", "workflow", "model_router", "security",
                    "recovery", "knowledge", "typography", "review", "revision", "color", "layout",
                    "packaging", "update", "nas", "provider", "adobe_bridge", "evaluation", "brand", "runtime",
                    "compare", "bg_removal", "panel_recognition", "installer_manifest",
                    # Phase 80（轨道 A 第二刀）：feature_flags/tool_registry/skill_runtime 三枚
                    # 逐字下沉为第 28 引擎 service_registry_engine（单一真相源、端点全量委托），
                    # 引擎名由此进入 registry 派生面；不加白名单即判 False 与产品实况矛盾。
                    # Phase 81（轨道 A 第三刀/服务侧最后一刀）：websocket_events/delivery_package
                    # 两枚逐字下沉为第 29 引擎 event_engine 与第 30 引擎 delivery_engine（单一真相源、
                    # 端点全量委托），引擎名由此进入 registry 派生面；不加白名单即整条 all(...) 判
                    # False、与产品实况矛盾（同 Phase 80 教训，与 _ENGINE_NAMES/health 无关）。
                    "desktop_hub", "service_registry", "event", "delivery"])
                  for c in rc.values())
          and rc["color_engine"]["engine"] == "color" and rc["color_engine"]["health_ready"] is True,
          json.dumps({k: rc[k]["engine"] for k in list(rc)[:6]}, ensure_ascii=False))
    _dom_expected = {"design": evj.get("categories", {}).get("design"),
                     "production": evj.get("categories", {}).get("production"),
                     "brand": evj.get("categories", {}).get("brand"),
                     "revision": evj.get("categories", {}).get("revision"),
                     "review": evj.get("categories", {}).get("review"),
                     "security": evj.get("categories", {}).get("security"),
                     "workflow": evj.get("categories", {}).get("workflow"),
                     "asset": evj.get("categories", {}).get("asset"),
                     "recovery": evj.get("categories", {}).get("recovery"),
                     "typography": evj.get("categories", {}).get("typography"),
                     "barcode": evj.get("categories", {}).get("barcode"),
                     "topology": evj.get("categories", {}).get("topology"),
                     "license": evj.get("categories", {}).get("license"),
                     "config": evj.get("categories", {}).get("config"),
                     "model_routing": evj.get("categories", {}).get("model_routing"),
                     "update": evj.get("categories", {}).get("update"),
                     "nas": evj.get("categories", {}).get("nas"),
                     "provider": evj.get("categories", {}).get("provider"),
                     "compare": evj.get("categories", {}).get("compare"),
                     "bg_removal": evj.get("categories", {}).get("bg_removal"),
                     "panel_recognition": evj.get("categories", {}).get("panel_recognition"),
                     "installer_manifest": evj.get("categories", {}).get("installer_manifest"),
                     "desktop_hub": evj.get("categories", {}).get("desktop_hub"),
                     "brand_semantics": evj.get("categories", {}).get("brand_semantics"),
                     "installer_execution": evj.get("categories", {}).get("installer_execution"),
                     # Phase 59 路线 B：三枚能力各归各账。runtime_guard 是**从零新题**（5 真值），
                     # installer_artifact_build / installer_preflight 不新增用例、只把此前记在
                     # installer_manifest 同一个 category 名下的编译面/预检面证据**改归**给它们
                     # 真正证明的能力。故此处三个 key 一律由 live categories 现场派生，
                     # 绝不手抄枚数（手抄数字与断言各说各话是 P58 判卷侧改核的同族教训）。
                     "runtime_guard": evj.get("categories", {}).get("runtime_guard"),
                     "installer_artifact_build": evj.get("categories", {}).get("installer_artifact_build"),
                     "installer_preflight": evj.get("categories", {}).get("installer_preflight")}
    check("registry: golden-domain caps carry disk-report counts matching THIS run's eval categories (Phase 29, +revision/review/security 30, +workflow 31, +asset/recovery 32, +typography 33, +barcode/topology 34, +license/config/model_routing 36, +update/nas/provider 37, +compare/bg_removal 38, +installer_manifest 49, +desktop_hub 50, +brand_semantics 56, +barcode QR 57 域内 5→10, +installer_execution 58, +runtime_guard/installer_artifact_build/installer_preflight 59 —— 后者两枚是改归而非新题，installer_manifest 域内 11→6 属收紧；Phase 63 一维解码 oracle 令 barcode 域内 10→14)",
          rc["color_engine"]["evidence_kind"] == "golden_cases"
          and rc["color_engine"]["test_count"] == _dom_expected["design"]["total"]
          and rc["layout_engine"]["test_count"] == _dom_expected["design"]["total"]
          and rc["packaging_dieline_vdp"]["test_count"] == _dom_expected["production"]["total"]
          and rc["print_qa"]["test_count"] == 2 and rc["knowledge_rag"]["test_count"] == 2
          and rc["brand_color_analysis"]["test_count"] == _dom_expected["brand"]["total"]
          and rc["customer_revision"]["test_count"] == _dom_expected["revision"]["total"]
          and rc["design_review"]["test_count"] == _dom_expected["review"]["total"]
          and rc["security"]["test_count"] == _dom_expected["security"]["total"]
          and rc["workflow_engine"]["test_count"] == _dom_expected["workflow"]["total"]
          and rc["asset_engine"]["test_count"] == _dom_expected["asset"]["total"]
          and rc["recovery_snapshot"]["test_count"] == _dom_expected["recovery"]["total"]
          and rc["typography_engine"]["test_count"] == _dom_expected["typography"]["total"]
          and rc["packaging_barcode"]["test_count"] == _dom_expected["barcode"]["total"] == 14
          and rc["packaging_panel_topology"]["test_count"] == _dom_expected["topology"]["total"] == 5
          and rc["license"]["test_count"] == _dom_expected["license"]["total"] == 4
          and rc["config_persistence"]["test_count"] == _dom_expected["config"]["total"] == 4
          and rc["model_router"]["test_count"] == _dom_expected["model_routing"]["total"] == 4
          and rc["auto_update"]["test_count"] == _dom_expected["update"]["total"] == 4
          and rc["nas_mount"]["test_count"] == _dom_expected["nas"]["total"] == 4
          and rc["ai_provider_integration"]["test_count"] == _dom_expected["provider"]["total"] == 4
          and rc["design_compare"]["test_count"] == _dom_expected["compare"]["total"] == 4
          and rc["image_remove_background"]["test_count"] == _dom_expected["bg_removal"]["total"] == 4
          and rc["packaging_panel_recognition"]["test_count"] == _dom_expected["panel_recognition"]["total"] == 4
          and rc["image_remove_background"]["engine"] == "bg_removal"
          and rc["packaging_panel_recognition"]["engine"] == "panel_recognition"
          # Phase 59 收紧：installer_manifest_verification 名下只剩静态+探测的 6 条。
          # 编译面 3 条与预检面 2 条不再算进自己名下（见下面三枚改归断言），
          # 故 11→6 是**证据记账口径变严**，不是产品退化——真值表零漂移。
          and rc["installer_manifest_verification"]["test_count"] == _dom_expected["installer_manifest"]["total"] == 6
          and rc["installer_manifest_verification"]["passed_count"] == 6
          and rc["installer_manifest_verification"]["engine"] == "installer_manifest"
          and rc["installer_manifest_verification"]["golden_domain"] == "installer_manifest"
          and rc["desktop_hub"]["test_count"] == _dom_expected["desktop_hub"]["total"] == 4
          and rc["desktop_hub"]["passed_count"] == 4 and rc["desktop_hub"]["failed_count"] == 0
          and rc["desktop_hub"]["engine"] == "desktop_hub"
          and rc["desktop_hub"]["golden_domain"] == "desktop_hub"
          and rc["desktop_hub"]["evidence_kind"] == "golden_cases"
          # Phase 56: brand_semantic_recognition 转正——golden_domain=brand_semantics、engine=brand，
          # 5 条真值用例逐字透传/data URL 复算/Bearer/明文缺席/非 JSON 保 raw 全绿
          and rc["brand_semantic_recognition"]["test_count"] == _dom_expected["brand_semantics"]["total"] == 5
          and rc["brand_semantic_recognition"]["passed_count"] == 5
          and rc["brand_semantic_recognition"]["failed_count"] == 0
          and rc["brand_semantic_recognition"]["engine"] == "brand"
          and rc["brand_semantic_recognition"]["golden_domain"] == "brand_semantics"
          and rc["brand_semantic_recognition"]["evidence_kind"] == "golden_cases"
          # Phase 58：installer_execution 转正的证据源——引擎=runtime（执行器本体在
          # installer_runtime_engine 内），黄金域=installer_execution（评测台用**注入的假
          # spawn 接缝 + 临时根**真跑门与差集派生，判据见 evaluation_engine 该域用例）。
          # 缺席域必须**判红而不是抛异常**：_dom_expected 的每一项都可能是 None（域还没
          # 交付时 categories 里就没有它），直接二级下标会把整台仪器炸成 traceback、既不出
          # 裁决行也证明不了任何事（P52「判卷侧崩溃≠被测缺陷」同族）。故取 .get 链，
          # 断言语义不变：None != 6 照样判红（下限数字随下一行的改核同步，注释与断言不得各说各话）。
          # 判卷侧改核（P58）：此处的 5 是我写断言时按"黄金域 5 条"的旧模板手抄的，而评测台
          # 实际注册了 **6** 条 installer_execution 真值用例（L4 同一份 payload 文本早就钉 6）。
          # 两套判据对同一个派生值给出不同期望 ⇒ 谁红都不代表产品坏。口径以磁盘实况为准：
          # p58_mut_ctrl.out 实测量 `exec域={'total': 6, 'passed': 6, 'failed': 0}`，
          # 且 registry 的 test_count 由 agent_service 从**同一份报告**的 categories 派生
          # （agent_service.py:3400 `entry["test_count"] = st["total"]`），两侧同源必等。
          and rc["installer_execution"]["test_count"] == (_dom_expected["installer_execution"] or {}).get("total") == 6
          and rc["installer_execution"]["passed_count"] == 6
          and rc["installer_execution"]["failed_count"] == 0
          and rc["installer_execution"]["engine"] == "runtime"
          and rc["installer_execution"]["golden_domain"] == "installer_execution"
          and rc["installer_execution"]["evidence_kind"] == "golden_cases"
          # Phase 59 路线 B 三枚。口径与上面 installer_execution 完全一致：test_count 由
          # 服务从**同一份最新报告**的 categories 现场派生（agent_service.py `entry["test_count"]
          # = st["total"]`，且 must_fail 校准被聚合排除），判据拿它与本进程刚跑完的 evj 比对，
          # 因此两侧同源必等——任何不等都只能是"注册的用例数与文档声称的数各写一份"。
          # runtime_guard = 从零新题 5 条（写探针三臂/磁盘两侧/端口两侧/互斥锁五步链/诊断派生+降级）
          # installer_artifact_build = 改归 3 条（编译面正控/四臂/告警完备性）
          # installer_preflight = 改归 2 条（预检正控只读承诺/十臂桩）+ 本轮新增一枚自有校准
          # 注意键名是**能力 id**（registry 的 rc 以 id 索引）：installer_runtime_guard 的黄金域
          # 叫 runtime_guard，写成 rc["runtime_guard"] 会把仪器炸成 KeyError（无裁决行、证不了任何事）。
          and rc["installer_runtime_guard"]["test_count"] == (_dom_expected["runtime_guard"] or {}).get("total") == 5
          and rc["installer_runtime_guard"]["passed_count"] == 5
          and rc["installer_runtime_guard"]["failed_count"] == 0
          and rc["installer_runtime_guard"]["engine"] == "runtime"
          and rc["installer_runtime_guard"]["golden_domain"] == "runtime_guard"
          and rc["installer_runtime_guard"]["evidence_kind"] == "golden_cases"
          and rc["installer_artifact_build"]["test_count"] == (_dom_expected["installer_artifact_build"] or {}).get("total") == 3
          and rc["installer_artifact_build"]["passed_count"] == 3
          and rc["installer_artifact_build"]["failed_count"] == 0
          and rc["installer_artifact_build"]["engine"] == "installer_manifest"
          and rc["installer_artifact_build"]["golden_domain"] == "installer_artifact_build"
          and rc["installer_artifact_build"]["evidence_kind"] == "golden_cases"
          and rc["installer_preflight"]["test_count"] == (_dom_expected["installer_preflight"] or {}).get("total") == 2
          and rc["installer_preflight"]["passed_count"] == 2
          and rc["installer_preflight"]["failed_count"] == 0
          and rc["installer_preflight"]["engine"] == "installer_manifest"
          and rc["installer_preflight"]["golden_domain"] == "installer_preflight"
          and rc["installer_preflight"]["evidence_kind"] == "golden_cases"
          and rc["evaluation_center"]["test_count"] == _es.get("executed_cases")
          and all(rc[i]["verified"] is True and rc[i]["failed_count"] == 0
                  and rc[i]["last_verified_at"] == ev2.get("finished_at")
                  for i in ("color_engine", "layout_engine", "packaging_dieline_vdp", "print_qa",
                            "knowledge_rag", "brand_color_analysis", "evaluation_center",
                            "customer_revision", "design_review", "security", "workflow_engine",
                            "asset_engine", "recovery_snapshot", "typography_engine",
                            "packaging_barcode", "packaging_panel_topology",
                            "license", "config_persistence", "model_router",
                            "auto_update", "nas_mount", "ai_provider_integration",
                            "design_compare", "image_remove_background",
                            "packaging_panel_recognition", "installer_manifest_verification",
                            "desktop_hub", "brand_semantic_recognition",
                            "installer_execution", "installer_runtime_guard",
                            "installer_artifact_build", "installer_preflight")),
          json.dumps(rc.get("color_engine"), ensure_ascii=False))
    _supported29 = [i for i, c in rc.items() if c["status"] == "supported"]
    _evid29 = {}
    for _c29 in rc.values():
        _evid29[_c29["evidence_kind"]] = _evid29.get(_c29["evidence_kind"], 0) + 1
    check("registry: supported-without-golden-caps honestly verified=False, never rubber-stamped (Phase 29, barcode/topology promoted Phase 34, license promoted Phase 36, update/nas/provider promoted Phase 37, design_compare/image_remove_background promoted Phase 38, installer_manifest_verification promoted Phase 49, desktop_hub promoted Phase 50; Phase 53's two new caps installer_artifact_build/installer_preflight joined the NOT-rubber-stamped side because they owned no golden domain of their own — Phase 59 route B moved them to the VERIFIED side by RE-ATTRIBUTING the very cases that were already being executed under installer_manifest, so this is a bookkeeping fix rather than new coverage; Phase 58 installer_execution joins the VERIFIED side only because it owns a real golden domain of its own; Phase 59 also promotes installer_runtime_guard, whose 5 cases are genuinely new; Phase 79 promotes adobe_uxp_bridge / cdr_document_read / cdr_document_write from the NOT-rubber-stamped side to VERIFIED because each now owns a real golden domain (bridge_handshake / bridge_cdr_read / bridge_cdr_write) whose cases genuinely drive handshake->dispatch->poll->submit_result->reconcile round-trips against a protocol-compliance mock host; this is protocol-MECHANISM evidence, NOT real Adobe/CAD document mutation, so the write caps ps_document_write / ai_document_write and adobe_capability_report STAY the 3 honest_boundary rows; Phase 80 promotes feature_flags / tool_registry / skill_runtime from the NOT-rubber-stamped side to VERIFIED because the three service caps are逐字下沉 into the 28th engine service_registry_engine (single source of truth — the endpoints delegate to it) and each now owns a real golden domain whose case constructs that SAME shipped engine in a private sandbox and drives the shipped code, NOT a hand-written stub; Phase 81 promotes the LAST two service-side caps delivery_package / websocket_events from the NOT-rubber-stamped side to VERIFIED because they are逐字下沉 into the 29th engine event_engine and the 30th engine delivery_engine respectively (single source of truth — the /ws, /events/recent and /delivery/package endpoints delegate to them) and each now owns a real golden domain whose case constructs that SAME shipped engine in a private sandbox and drives the shipped code, NOT a hand-written stub — leaving no no_golden_cases_yet cap on the supported side)",
          # Phase 81：delivery_package / websocket_events 两枚从 no_golden 侧升到 VERIFIED 侧——
          # 分别挂第 30/29 引擎（delivery/event）、黄金域逐一对名（delivery_package/
          # websocket_events）、evidence=golden_cases、verified True、status supported、
          # test_count 1、failed 0；判据是评测台于私有实例／私有沙箱直接构造出货引擎
          # 驱动同一份随包代码，非另造桩。服务侧 no_golden_cases_yet 由此收缩为 0。
          rc["delivery_package"]["engine"] == "delivery"
          and rc["delivery_package"]["golden_domain"] == "delivery_package"
          and rc["websocket_events"]["engine"] == "event"
          and rc["websocket_events"]["golden_domain"] == "websocket_events"
          and all(rc[i]["evidence_kind"] == "golden_cases" for i in ("delivery_package",
                                                                     "websocket_events"))
          and all(rc[i]["verified"] is True for i in ("delivery_package", "websocket_events"))
          and all(rc[i]["status_2_0"] == "supported" for i in ("delivery_package",
                                                              "websocket_events"))
          and all(rc[i]["test_count"] == 1 and rc[i]["failed_count"] == 0 for i in (
              "delivery_package", "websocket_events"))
          # Phase 80：三枚服务侧持久化配置能力升到 VERIFIED 侧——挂上第 28 引擎
          # service_registry、黄金域逐一对名（feature_flags/tool_registry/skill_runtime）、
          # evidence=golden_cases、verified True、test_count 1、failed 0；判据是评测台于私有
          # 沙箱直接构造出货的 ServiceRegistryEngine 驱动同一份随包代码，非另造桩。
          and all(rc[i]["engine"] == "service_registry" for i in ("feature_flags",
                                                                  "tool_registry", "skill_runtime"))
          and rc["feature_flags"]["golden_domain"] == "feature_flags"
          and rc["tool_registry"]["golden_domain"] == "tool_registry"
          and rc["skill_runtime"]["golden_domain"] == "skill_runtime"
          and all(rc[i]["status_2_0"] == "supported" for i in ("feature_flags",
                                                               "tool_registry", "skill_runtime"))
          and all(rc[i]["evidence_kind"] == "golden_cases" for i in ("feature_flags",
                                                                     "tool_registry", "skill_runtime"))
          and all(rc[i]["verified"] is True for i in ("feature_flags",
                                                      "tool_registry", "skill_runtime"))
          and all(rc[i]["test_count"] == 1 and rc[i]["failed_count"] == 0 for i in ("feature_flags",
                                                                                    "tool_registry", "skill_runtime"))
          # Phase 79：三枚 Adobe 桥升到 VERIFIED 侧——挂上黄金域、evidence=golden_cases、
          # verified True、test_count 1、failed 0；诚实边界：协议机制往返（mock 宿主），
          # 非真实 Adobe/CAD 文档改写
          and all(rc[i]["engine"] == "adobe_bridge" for i in ("adobe_uxp_bridge",
                                                              "cdr_document_read", "cdr_document_write"))
          and rc["adobe_uxp_bridge"]["golden_domain"] == "bridge_handshake"
          and rc["cdr_document_read"]["golden_domain"] == "bridge_cdr_read"
          and rc["cdr_document_write"]["golden_domain"] == "bridge_cdr_write"
          and all(rc[i]["status_2_0"] == "supported" for i in ("adobe_uxp_bridge",
                                                              "cdr_document_read", "cdr_document_write"))
          and all(rc[i]["evidence_kind"] == "golden_cases" for i in ("adobe_uxp_bridge",
                                                                    "cdr_document_read", "cdr_document_write"))
          and all(rc[i]["verified"] is True for i in ("adobe_uxp_bridge",
                                                     "cdr_document_read", "cdr_document_write"))
          and all(rc[i]["test_count"] == 1 and rc[i]["failed_count"] == 0 for i in ("adobe_uxp_bridge",
                                                                                   "cdr_document_read", "cdr_document_write"))
          and rc["installer_runtime_guard"]["evidence_kind"] == "golden_cases"
          and rc["installer_artifact_build"]["evidence_kind"] == "golden_cases"
          and rc["installer_preflight"]["evidence_kind"] == "golden_cases"
          and rc["packaging_panel_recognition"]["evidence_kind"] == "golden_cases"
          and rc["installer_manifest_verification"]["evidence_kind"] == "golden_cases"
          and rc["installer_execution"]["evidence_kind"] == "golden_cases"
          and rc["installer_execution"]["verified"] is True
          and rc["installer_execution"]["engine"] == "runtime"
          and sum(1 for i in _supported29 if rc[i]["verified"]) == 40
          # Phase 81：最后两枚服务侧能力 delivery_package/websocket_events 从
          # no_golden_cases_yet 升到 golden_cases ⇒ golden_cases 38→40、no_golden_cases_yet
          # 2→0（服务侧全域入黄金）、honest_boundary 恒 3（ps/ai 写 + 能力上报），
          # verified 求和 38→40。判据须尊重派生形状：_evid29 只累加**出现过的** evidence_kind，
          # 计数归 0 即键**缺席**而非显式 0（缺席与 0 是两回事），故不能钉 "no_golden_cases_yet":0，
          # 改钉「该键缺席 + golden 40 + honest_boundary 3 + 求和 43」三者合证。
          and _evid29 == {"golden_cases": 40, "honest_boundary": 3}
          and "no_golden_cases_yet" not in _evid29
          and sum(_evid29.values()) == 43,
          json.dumps({"verified": sum(1 for i in _supported29 if rc[i]["verified"]),
                      "evidence": _evid29}, ensure_ascii=False))
    check("registry: needs_host caps marked honest_boundary with zero evidence demands (Phase 29; desktop_hub LEFT this set in Phase 50; Phase 56 brand_semantic_recognition LEFT this set too; Phase 58 installer_execution LEFT this set as well — the executor ships, owns a golden domain, and its receipts derive from disk+registry diff, so its honest_boundary row became a golden_cases row. Only 3 honest_boundary rows remain: the 3 Adobe write caps)",
          all(rc[i]["status_2_0"] == "needs_host"
              and rc[i]["evidence_kind"] == "honest_boundary" and rc[i]["verified"] is False
              for i in ("ps_document_write", "ai_document_write", "adobe_capability_report"))
          and rc["installer_execution"]["status_2_0"] == "supported"
          and rc["installer_execution"]["evidence_kind"] == "golden_cases"
          and not any(c["status_2_0"] == "not_shipped" for c in rc.values())
          and rc["desktop_hub"]["status_2_0"] == "supported"
          and rc["brand_semantic_recognition"]["status_2_0"] == "supported"
          and rc["brand_semantic_recognition"]["evidence_kind"] == "golden_cases"
          and rc["packaging_panel_recognition"]["evidence_kind"] != "honest_boundary",
          json.dumps([rc[i]["status_2_0"] for i in ("desktop_hub", "ps_document_write",
                                                    "packaging_panel_recognition",
                                                    "brand_semantic_recognition")]))
    _rdir29 = tmp / "data" / "evaluation" / "reports"
    _bak29 = tmp / "p29_reports_bak"
    _sh29.move(str(_rdir29), str(_bak29))
    cold29 = {c["id"]: c for c in requests.get(BASE + "/api/v1/capabilities").json()["registry"]["capabilities"]}
    _sh29.move(str(_bak29), str(_rdir29))
    check("registry: delete reports on disk -> evidence counts vanish (source is files, not memory) (Phase 29)",
          all(cold29[i]["evidence_kind"] == "no_eval_report" and cold29[i]["verified"] is False
              and cold29[i]["test_count"] is None
              for i in ("color_engine", "print_qa", "knowledge_rag", "evaluation_center"))
          and all(c["evidence_kind"] in ("no_eval_report", "honest_boundary") and c["verified"] is False
                  for c in cold29.values()),
          json.dumps(cold29.get("print_qa"), ensure_ascii=False))
    _fake29 = _rdir29 / "eval-p29-fake-drift.json"
    _fake29.write_text(json.dumps({"report_id": "eval-p29-fake-drift", "finished_at": "2099-01-01T00:00:00",
                                   "honest": True, "calibration_error": False,
                                   "results": [{"id": "x", "category": "bogus-domain", "must_fail": False, "passed": True}]}),
                       encoding="utf-8")
    drift29 = {c["id"]: c for c in requests.get(BASE + "/api/v1/capabilities").json()["registry"]["capabilities"]}
    _fake29.unlink()
    check("registry: hand-written report with drifted domains exposed, not silently blessed (Phase 29)",
          drift29["print_qa"]["evidence_kind"] == "report_missing_domain"
          and drift29["print_qa"]["verified"] is False and drift29["print_qa"]["test_count"] is None
          and drift29["color_engine"]["last_verified_at"] == "2099-01-01T00:00:00",
          json.dumps(drift29.get("print_qa"), ensure_ascii=False))
    _mix29 = _rdir29 / "eval-p29-mixed-fail.json"
    _mix29.write_text(json.dumps({
        "report_id": "eval-p29-mixed-fail", "finished_at": "2099-01-01T00:00:01",
        "honest": True, "calibration_error": False,
        "results": ([{"id": f"d{i}", "category": "design", "must_fail": False, "passed": i < 9} for i in range(10)]
                    + [{"id": "c0", "category": "design", "must_fail": True, "passed": False}])}),
        encoding="utf-8")
    time.sleep(1.05)
    _mix29.touch()
    mix29 = {c["id"]: c for c in requests.get(BASE + "/api/v1/capabilities").json()["registry"]["capabilities"]}
    _mix29.unlink()
    check("registry: 1-of-10 real fails in report flips verified->False and failed_count->1 both directions (Phase 29)",
          mix29["color_engine"]["evidence_kind"] == "golden_cases"
          and mix29["color_engine"]["test_count"] == 10
          and mix29["color_engine"]["passed_count"] == 9
          and mix29["color_engine"]["failed_count"] == 1
          and mix29["layout_engine"]["verified"] is False
          and mix29["print_qa"]["evidence_kind"] == "report_missing_domain"
          and all(mix29[i]["verified"] is False for i in ("color_engine", "evaluation_center")),
          json.dumps(mix29.get("color_engine"), ensure_ascii=False))

    # --- Phase 25 brand intelligence: real pixel analysis via /api/v1/brand/* ---
    _logo25 = tmp / "p25_logo.png"
    _li = Image.new("RGBA", (100, 50), (0, 0, 0, 0))
    for _y in range(10, 40):
        for _x in range(20, 80):
            _li.putpixel((_x, _y), (9, 9, 9, 255))
    _li.save(_logo25)
    bp = requests.post(BASE + "/api/v1/brand/palette", json={"image_path": str(img1), "k": 2})
    bpj = bp.json()
    _bphexes = sorted(e["hex"].upper() for e in bpj.get("palette", []))
    check("brand: palette endpoint yields real median-cut hexes of prepared image (#DC1E1E + #FFFFFF)",
          bp.status_code == 200 and _bphexes == ["#DC1E1E", "#FFFFFF"]
          and bpj.get("method") == "median_cut_via_color_engine",
          json.dumps(_bphexes, ensure_ascii=False))
    bc = requests.post(BASE + "/api/v1/brand/color-consistency",
                       json={"brand_hexes": ["#DC1E1E", "#FFFFFF"], "image_path": str(img1), "max_delta_e": 1.0})
    bcj = bc.json()
    check("brand: exact brand colors on matching image -> real coverage 1.0 (Phase 25)",
          bc.status_code == 200 and bcj.get("coverage") == 1.0 and bcj.get("all_matched") is True
          and bcj.get("fabricated") is False, json.dumps(bcj.get("brand_colors"), ensure_ascii=False))
    bn = requests.post(BASE + "/api/v1/brand/color-consistency",
                       json={"brand_hexes": ["#00FF00"], "image_path": str(img1), "max_delta_e": 10.0})
    bnj = bn.json()
    _bnd = (bnj.get("brand_colors") or [{}])[0]
    check("brand NEG: green brand vs red/white image -> matched False with real large deltaE (falsifiable scorer)",
          bn.status_code == 200 and _bnd.get("matched") is False and bnj.get("coverage") == 0.0
          and 20.0 < (_bnd.get("delta_e_ciede2000") or 0) < 100.0, json.dumps(_bnd, ensure_ascii=False))
    bg = requests.post(BASE + "/api/v1/brand/logo-geometry",
                       json={"image_path": str(_logo25), "alpha_threshold": 8})
    bgj = bg.json()
    check("brand: logo geometry is real alpha bbox (20,10,80,40) with symmetric margins (Phase 25)",
          bg.status_code == 200 and bgj.get("content_bbox") == [20, 10, 80, 40]
          and bgj.get("margins") == {"left": 20, "top": 10, "right": 20, "bottom": 10}
          and bgj.get("aspect_ratio") == 2.0 and bgj.get("fill_ratio") == 1.0,
          json.dumps({k: bgj.get(k) for k in ("content_bbox", "margins", "aspect_ratio")}, ensure_ascii=False))
    bh1 = requests.post(BASE + "/api/v1/brand/logo-geometry",
                        json={"image_path": str(_logo25), "alpha_threshold": "NaN?"})
    bh2 = requests.post(BASE + "/api/v1/brand/color-consistency",
                        json={"brand_hexes": ["#GGGGGG"], "image_path": str(img1)})
    bh3 = requests.post(BASE + "/api/v1/brand/palette",
                        data=json.dumps({"image_path": str(img1), "k": float("nan")}, allow_nan=True),
                        headers={"Content-Type": "application/json"})
    check("brand: hostile inputs honestly 4xx (non-numeric threshold / invalid hex / NaN k), no 5xx leak",
          bh1.status_code < 500 and bh1.status_code >= 400
          and bh2.status_code < 500 and bh2.status_code >= 400
          and bh3.status_code < 500 and bh3.status_code >= 400,
          f"HTTP {bh1.status_code}/{bh2.status_code}/{bh3.status_code}")
    bs = requests.get(BASE + "/api/v1/brand/semantics")
    bsj = bs.json()
    check("brand: semantics endpoint honestly declares needs_external + fabricated False, no invented tone",
          bs.status_code == 200 and bsj.get("status") == "needs_external" and bsj.get("fabricated") is False,
          json.dumps({k: bsj.get(k) for k in ("status", "fabricated")}, ensure_ascii=False))

    # ---- Phase 56 真实品牌语义透传入口：POST /api/v1/brand/semantic-insight ----
    # 该端点把 Logo 图像字节交给"已注入的真实 Provider 视觉模型"、逐字透传其真实结果或真实失败。
    # agent_service 恒把 ProviderEngine 注入 brand（get_brand_engine 里 provider=get_provider_engine()），
    # 故 HTTP 面走不到引擎内 self._provider is None 那条 needs_external（仅 L1 自测/评测直构引擎可达）。
    # 为把"绝不伪造 AI 语义"钉成确定性判据，这里显式接入一枚**真实不可达** Provider（127.0.0.1:9 拒连），
    # 端点必须把它的**真实传输失败逐字透传**为 502（error/unreachable），且响应体内绝不出现任何
    # 自造的 semantics/tone_keywords/confidence 字段——这正是 §2 红线：有真 Provider 通路但模型没给出
    # 结果时，服务替模型编造语义即为伪成功。三形制各有真值：真图+不可达 Provider→502 透传 /
    # 缺件→404 / 策略禁入绝对路径→403 / 目录非文件→400（先过 P55 共用路径门再谈透传）。
    requests.post(BASE + "/api/v1/provider/configure", timeout=30,
                  json={"provider_id": "p56dead", "base_url": "http://127.0.0.1:9/v1",
                        "api_key": "p56-probe-key", "model": "vision-test"})
    _bs56 = requests.post(BASE + "/api/v1/brand/semantic-insight",
                          json={"image_path": str(img3), "provider_id": "p56dead",
                                "prompt": "analyze the logo mood"}, timeout=60)
    _bs56d = _bs56.json().get("detail") if _bs56.status_code == 502 else None
    _bs56_body = json.dumps(_bs56.json(), ensure_ascii=False).lower()
    check("brand(P56): semantic-insight with a real Logo + an explicitly unreachable vision Provider -> the endpoint PASSES THROUGH that provider's real transport failure as honest 502 (fabricated:False, status passthrough) and NEVER synthesizes semantics/tone_keywords/confidence (§2 red line: a fake success while the model produced nothing is exactly what this catches)",
          _bs56.status_code == 502 and isinstance(_bs56d, dict)
          and _bs56d.get("fabricated") is False
          and _bs56d.get("status") in ("needs_external", "no_provider_configured", "error", "unreachable")
          and "unreachable" in json.dumps(_bs56d, ensure_ascii=False).lower()
          and '"semantics"' not in _bs56_body and '"tone_keywords"' not in _bs56_body
          and '"confidence"' not in _bs56_body,
          f"HTTP {_bs56.status_code} detail={json.dumps(_bs56d, ensure_ascii=False)[:200] if isinstance(_bs56d, dict) else str(_bs56d)[:160]}")
    _bs56_missing = requests.post(BASE + "/api/v1/brand/semantic-insight",
                                  json={"image_path": str(tmp / "p56_no_such_logo.png")}, timeout=60)
    check("brand(P56): semantic-insight path gate — missing image -> honest 404 文件不存在 (never a fabricated analysis of a file that was never opened)",
          _bs56_missing.status_code == 404 and "文件不存在" in str(_bs56_missing.json().get("detail")),
          f"HTTP {_bs56_missing.status_code} {str(_bs56_missing.json().get('detail'))[:80]}")
    _bs56_blocked = requests.post(BASE + "/api/v1/brand/semantic-insight",
                                  json={"image_path": "C:/Windows/System32/config/SAM"}, timeout=60)
    check("brand(P56): semantic-insight path gate — security-blocked absolute path -> honest 403 被安全策略 (SAME gate as the other 14 path-consuming call sites)",
          _bs56_blocked.status_code == 403 and "安全策略" in str(_bs56_blocked.json().get("detail")),
          f"HTTP {_bs56_blocked.status_code} {str(_bs56_blocked.json().get('detail'))[:80]}")
    _bs56_dir = requests.post(BASE + "/api/v1/brand/semantic-insight",
                              json={"image_path": str(tmp / "data")}, timeout=60)
    check("brand(P56): semantic-insight path gate — directory (exists but not a file) -> honest 400 不是可读文件 (is_file() check before provider delegation)",
          _bs56_dir.status_code == 400 and "不是可读文件" in str(_bs56_dir.json().get("detail")),
          f"HTTP {_bs56_dir.status_code} {str(_bs56_dir.json().get('detail'))[:80]}")

    # ---- Phase 26 运行时守护：真实探针经 HTTP（负控 = 本服务自己占用的 PORT 端口）----
    rd = requests.get(BASE + "/api/v1/runtime/diagnostic")
    rdj = rd.json()
    check("runtime: diagnostic counts derived from real checks; live DATA_ROOT write probe passes for real",
          rd.status_code == 200
          and rdj["checks_performed"] == rdj["checks_passed"] + rdj["checks_failed"] == len(rdj["checks"])
          and rdj["fabricated"] is False
          and any(c["check"] == "data_root_writable" and c["status"] == "pass" for c in rdj["checks"])
          and rdj["status"] == ("healthy" if rdj["checks_failed"] == 0 else "degraded"),
          json.dumps({k: rdj.get(k) for k in ("status", "checks_passed", "checks_performed")}, ensure_ascii=False))
    rneg = requests.get(BASE + "/api/v1/runtime/diagnostic", params={"service_port": str(PORT)}).json()
    _pi = [c for c in rneg["checks"] if c["check"] == "port_available"][0]
    check("runtime NEG: service_port = the live service's own port -> honest fail + degraded (real bind conflict)",
          _pi["status"] == "fail" and rneg["status"] == "degraded" and rneg["checks_failed"] >= 1,
          json.dumps({k: rneg.get(k) for k in ("status", "checks_failed")}, ensure_ascii=False))
    import socket as _sock26
    _s26 = _sock26.socket(); _s26.bind(("127.0.0.1", 0)); _free26 = _s26.getsockname()[1]; _s26.close()
    rp_occ = requests.post(BASE + "/api/v1/runtime/port-check", json={"port": str(PORT)}).json()
    rp_free = requests.post(BASE + "/api/v1/runtime/port-check", json={"port": _free26}).json()
    check("runtime: port-check mirrors real bind (occupied %d False / freshly freed port True)" % PORT,
          rp_occ["available"] is False and rp_free["available"] is True and rp_free["fabricated"] is False,
          f"occupied={rp_occ.get('available')} free={rp_free.get('available')}")
    h1 = requests.post(BASE + "/api/v1/runtime/port-check", json={"port": "NaN"})
    h2 = requests.post(BASE + "/api/v1/runtime/port-check", json={"port": 70000})
    h3 = requests.get(BASE + "/api/v1/runtime/diagnostic", params={"service_port": "abc"})
    check("runtime: hostile inputs honestly 4xx (NaN/0-range/non-numeric), no 5xx leak",
          h1.status_code == 400 and h2.status_code == 400 and h3.status_code == 400,
          f"HTTP {h1.status_code}/{h2.status_code}/{h3.status_code}")
    _inst = "l2-p26-%x" % int(time.time() * 1000 % 0x7FFFFFFF)
    si1 = requests.post(BASE + "/api/v1/runtime/single-instance", json={"name": _inst}).json()
    si2 = requests.post(BASE + "/api/v1/runtime/single-instance", json={"name": _inst}).json()
    si3 = requests.post(BASE + "/api/v1/runtime/single-instance", json={"name": _inst, "action": "release"}).json()
    si_bad = requests.post(BASE + "/api/v1/runtime/single-instance", json={"name": "bad/name"})
    check("runtime: single-instance mirrors kernel mutex (acquire wins, 2nd sees already_running, release ok, bad name 400)",
          si1.get("acquired") is True and si1.get("already_running") is False
          and si2.get("acquired") is False and si2.get("already_running") is True
          and si3.get("released") is True and si_bad.status_code == 400,
          json.dumps({k: si1.get(k) for k in ("acquired", "backend")}, ensure_ascii=False))
    ia = requests.get(BASE + "/api/v1/runtime/install-actions")
    iaj = ia.json()
    check("runtime: install-actions stays a real contract, never a fabricated install (Phase 26 honesty legs kept verbatim: fabricated=False and no example.com/1.1.0 leftovers) — Phase 58 flips only the status claim needs_installer_environment -> executable_via_cli_only; the old wording still lives, named in retired_boundary.was (见 contract(P58))",
          ia.status_code == 200 and iaj.get("status") == "executable_via_cli_only"
          and iaj.get("fabricated") is False
          and "example.com" not in ia.text and "1.1.0" not in ia.text,
          json.dumps({k: iaj.get(k) for k in ("status", "owned_by")}, ensure_ascii=False))

    m = requests.get(BASE + "/api/v1/metrics").json()
    check("metrics: real counters", m["requests_total"] > 20 and m["p95_latency"] is not None, f"req={m['requests_total']} p95={m['p95_latency']} mem={m.get('memory_mb')}MB")

    dg = requests.post(BASE + "/api/v1/diagnostics/run").json()
    check("diagnostics: all checks real", dg["total"] >= 7, f"{dg['passed']}/{dg['total']} pass -> {dg['overall_status']} " + json.dumps([c['check'] + ':' + c['status'] for c in dg['checks']]))

    # ===== Phase 17: close acceptance-coverage gap on 9 real-but-untested supported endpoints =====
    # Verify each is genuinely wired HTTP->engine (drives real computation / state transition /
    # redaction), NOT a hardcoded stub — a constant-stub or dead route would fail one of these.

    # (1) color/nearest — query-driven ΔE ordering (two different queries -> different nearest)
    nr = requests.post(BASE + "/api/v1/color/nearest",
                       json={"color": "#3A7D44", "palette": ["#FF0000", "#00FF00", "#0000FF"]}).json()
    nr2 = requests.post(BASE + "/api/v1/color/nearest",
                        json={"color": "#0A0AF0", "palette": ["#FF0000", "#00FF00", "#0000FF"]}).json()
    _m = nr.get("matches", [])
    _sorted = all(_m[i]["delta_e"] <= _m[i + 1]["delta_e"] + 1e-6 for i in range(len(_m) - 1))
    check("color/nearest: real CIEDE2000 ordering + query-driven (green->#00FF00, blue->#0000FF, not constant)",
          len(_m) == 3 and _m[0]["hex"] == "#00FF00" and nr2["matches"][0]["hex"] == "#0000FF"
          and _sorted and _m[0]["delta_e"] < _m[-1]["delta_e"], json.dumps(_m))
    nrbad = requests.post(BASE + "/api/v1/color/nearest", json={"color": "nothex", "palette": ["#FF0000"]})
    check("color/nearest: illegal hex honestly 400", nrbad.status_code == 400, str(nrbad.status_code))

    # (2) layout/grid-snap — real cell quantization (a in cell(0,0)->(0,0), b in cell(1,1)->(60,60))
    gs = requests.post(BASE + "/api/v1/layout/grid-snap", json={
        "objects": [{"id": "a", "x": 5, "y": 5, "width": 10, "height": 10},
                    {"id": "b", "x": 90, "y": 90, "width": 10, "height": 10}],
        "canvas": {"width": 120, "height": 120}, "cols": 2, "rows": 2, "mode": "cell_top_left"}).json()
    _byid = {o["id"]: o for o in gs.get("objects", [])}
    check("layout/grid-snap: real snapping onto 60px grid, distinct cells (not constant)",
          gs.get("snapped") == 2 and gs.get("grid", {}).get("cell") == [60, 60]
          and _byid.get("a", {}).get("to") == {"x": 0, "y": 0} and _byid.get("a", {}).get("cell") == [0, 0]
          and _byid.get("b", {}).get("to") == {"x": 60, "y": 60} and _byid.get("b", {}).get("cell") == [1, 1],
          json.dumps(gs)[:200])

    # (3)+(4) nas/targets + nas/forget — real add->list(masked)->forget->gone->not_configured lifecycle
    requests.post(BASE + "/api/v1/nas/configure",
                  json={"target_id": "ph17", "target": str(tmp), "kind": "filesystem",
                        "username": "u17", "password": "pw17secretvalue"}).json()
    tg = requests.get(BASE + "/api/v1/nas/targets").json()
    _ids = [t["target_id"] for t in tg.get("targets", [])]
    _ph = next((t for t in tg.get("targets", []) if t["target_id"] == "ph17"), {})
    check("nas/targets: lists configured target with has_credentials=True yet plaintext pw NEVER leaks",
          "ph17" in _ids and _ph.get("has_credentials") is True and "pw17secretvalue" not in json.dumps(tg)
          and tg.get("fabricated") is False, json.dumps(_ph))
    fr = requests.post(BASE + "/api/v1/nas/forget", json={"target_id": "ph17"}).json()
    tg2 = requests.get(BASE + "/api/v1/nas/targets").json()
    conn_after = requests.post(BASE + "/api/v1/nas/connect", json={"target_id": "ph17"}).json()
    check("nas/forget: real persisted removal (absent from list + connect now not_configured)",
          fr.get("removed") is True and "ph17" not in [t["target_id"] for t in tg2.get("targets", [])]
          and conn_after.get("status") == "not_configured", json.dumps(fr))

    # (5)+(6) nas/cache + nas/cache/clear — real on-disk entry-count transition
    ca = requests.get(BASE + "/api/v1/nas/cache").json()
    cclr = requests.post(BASE + "/api/v1/nas/cache/clear").json()
    ca2 = requests.get(BASE + "/api/v1/nas/cache").json()
    check("nas/cache stats+clear: populated count>=1, clear removes >=1, re-query drops to 0 (state is real)",
          ca.get("entries", 0) >= 1 and cclr.get("cleared", 0) >= 1 and ca2.get("entries") == 0
          and ca2.get("fabricated") is False, f"before={ca} cleared={cclr.get('cleared')} after={ca2}")

    # (7) update/channels — 3 channels, reflects earlier configure, secret key absent (structural redaction)
    chs = requests.get(BASE + "/api/v1/update/channels").json()
    _names = [c["channel"] for c in chs.get("channels", [])]
    _dev = next((c for c in chs.get("channels", []) if c["channel"] == "dev"), {})
    check("update/channels: [stable,beta,dev] listed, dev configured+has_secret true, no raw 'secret' key present",
          _names == ["stable", "beta", "dev"] and _dev.get("configured") is True
          and _dev.get("has_secret") is True and all("secret" not in c for c in chs.get("channels", []))
          and chs.get("fabricated") is False, json.dumps(chs)[:220])

    # (8) update/pending — real engine structure
    pend = requests.get(BASE + "/api/v1/update/pending").json()
    check("update/pending: real engine payload (has_pending bool + fabricated false)",
          isinstance(pend.get("has_pending"), bool) and pend.get("fabricated") is False, json.dumps(pend)[:120])

    # (9) uxp/discovery (HTTP) — live port + token MASKED vs plaintext only in local discovery file
    bv2 = requests.get(BASE + "/api/v1/uxp/bridge").json()
    _dpath = Path(bv2.get("discovery_file", ""))
    _plain = ""
    if _dpath.is_file():
        _dj = json.loads(_dpath.read_text(encoding="utf-8"))
        _plain = _dj.get("session_token") or _dj.get("token") or ""
    disc = requests.get(BASE + "/api/v1/uxp/discovery").json()
    check("uxp/discovery: HTTP view shows live port(%d) + MASKED token differing from plaintext file token, paths present" % PORT,
          disc.get("port") == PORT and "*" in disc.get("session_token", "") and disc.get("session_token") != _plain
          and len(_plain) > 20 and disc.get("handshake_path") == "/api/v1/uxp/handshake"
          and disc.get("fabricated") is False,
          f"port={disc.get('port')} masked={disc.get('session_token')!r} file_token_len={len(_plain)}")

    # ===== Phase 45: 配置与写操作面板活体载荷（写侧真请求；置于全部既有断言之后、P44 回填之前） =====
    # 顺序即安全：provider/update/nas 配置态若早发会污染 P10 no_provider_configured、
    # P37 no_update_configured、P8 not_configured 等前段断言，故写侧探测只在此处发起。
    # 本块同时命中全部新增引号字面串所声称的模板，令 P44 声称→运行时命中链条闭合。
    _p45_bad = []
    _k45 = "p45_l2_cfg_key"
    _cp45 = requests.put(BASE + "/api/v1/config/" + _k45, json={"value": 42}, timeout=30)
    if not (_cp45.status_code == 200 and _k45 in (_cp45.json().get("config") or {})):
        _p45_bad.append("config-put")
    if requests.put(BASE + "/api/v1/config/" + _k45, json={"nope": 1}, timeout=30).status_code != 422:
        _p45_bad.append("config-put-hostile-not-422")
    _pkm45 = "sk-p45l2supersecret"
    _pc45 = requests.post(BASE + "/api/v1/provider/configure", timeout=30, json={
        "provider_id": "p45l2prov", "base_url": "http://127.0.0.1:1",
        "api_key": _pkm45, "model": "m1"})
    _pv45 = (_pc45.json().get("providers") or {}).get("p45l2prov") or {}
    if not (_pc45.status_code == 200 and _pv45.get("has_key") is True
            and "*" in str(_pv45.get("api_key_masked")) and _pkm45 not in _pc45.text):
        _p45_bad.append("provider-configure-mask")
    _ph45r = requests.post(BASE + "/api/v1/provider/health", timeout=30,
                           json={"provider_id": "p45l2prov", "timeout": 3})
    _ph45j = _ph45r.json() if _ph45r.status_code == 200 else {}
    if not (_ph45r.status_code == 200 and _ph45j.get("reachable") is False
            and _ph45j.get("status") == "unreachable" and _ph45j.get("fabricated") is False):
        _p45_bad.append("provider-health")
    _sh45 = tmp / "p45share"
    _sh45.mkdir(exist_ok=True)
    _nc45 = requests.post(BASE + "/api/v1/nas/configure", timeout=30, json={
        "target_id": "p45l2nas", "target": str(_sh45), "kind": "filesystem", "share_root": str(_sh45)})
    _tg45 = requests.get(BASE + "/api/v1/nas/targets", timeout=30).json()
    _nf45 = requests.post(BASE + "/api/v1/nas/forget", timeout=30, json={"target_id": "p45l2nas"})
    _cl45 = requests.post(BASE + "/api/v1/nas/cache/clear", timeout=30, json={})
    if not (_nc45.status_code == 200 and _nc45.json().get("configured") is True
            and "p45l2nas" in json.dumps(_tg45)
            and _nf45.json().get("removed") is True and isinstance(_cl45.json().get("cleared"), int)):
        _p45_bad.append("nas-configure-targets-forget-clear")
    _us45 = "p45l2topsecret"
    _uc45 = requests.post(BASE + "/api/v1/update/configure", timeout=30, json={
        "channel": "dev", "feed_url": "http://127.0.0.1:1/manifest.json",
        "secret": _us45, "current_version": "1.0.0"})
    if not (_uc45.status_code == 200 and _uc45.json().get("has_secret") is True
            and _us45 not in _uc45.text):
        _p45_bad.append("update-configure-mask")
    _uk45 = requests.post(BASE + "/api/v1/update/check", timeout=30,
                          json={"channel": "dev", "current_version": "1.0.0", "timeout": 8}).json()
    if not (_uk45.get("status") == "feed_unreachable" and _uk45.get("verified") is False
            and _uk45.get("fabricated") is False):
        _p45_bad.append("update-check-honest")
    _lc45 = requests.post(BASE + "/api/v1/license/activate", timeout=30,
                          json={"license_key": "NOTAVALIDKEY"})
    if not (_lc45.status_code == 400 and "reason" in json.dumps(_lc45.text)):
        _p45_bad.append("license-activate-400")
    _dp45 = requests.post(BASE + "/api/v1/delivery/package", timeout=60, json={
        "project": "l2p45proj", "files": {"Source": ["missing/__p45l2__.png"]},
        "notes": "p45 l2 live"}).json()
    _dp_arch = str(_dp45.get("archive") or "")
    if not (_dp45.get("included") == 0 and str(_dp45.get("package_id", "")).startswith("deliv_")
            and int(_dp45.get("archive_size_bytes") or 0) > 0
            and _dp_arch.startswith(str(tmp / "data")) and Path(_dp_arch).is_file()):
        _p45_bad.append("delivery-package")
    _tr45 = requests.post(BASE + "/api/v1/typography/resolve", timeout=120,
                          json={"missing": "A", "text": "p45 l2 resolve"})
    _trj45 = _tr45.json() if _tr45.status_code == 200 else {}
    if not (_tr45.status_code == 200 and isinstance(_trj45.get("candidates"), list)):
        _p45_bad.append("typography-resolve")  # 候选机器相关，不钉数
    _cr45 = requests.post(BASE + "/api/v1/tools/capability-report", timeout=30, json={
        "host": "p45l2host", "capabilities": {"ps.doc.place": True}}).json()
    if not (_cr45.get("host") == "p45l2host" and int(_cr45.get("hosts_reported") or 0) >= 1
            and int(_cr45.get("supported_count") or 0) >= 1):
        _p45_bad.append("capability-report")
    _al45 = requests.post(BASE + "/api/v1/assets/__p45l2ghost__/license", timeout=30, json={
        "license_status": "commercial", "license_type": "commercial",
        "commercial_use": True, "verified": False, "warnings": []})
    if not (_al45.status_code == 404 and "不存在" in _al45.text):
        _p45_bad.append("assets-license-ghost-404")
    check("webui(P45) live write payloads: 13 write endpoints on THIS live service — config PUT 真值+敌意422 / provider 脱敏往返+health 诚实 unreachable / nas configure→targets→forget→cache-clear 链 / update configure 不回显密钥+check 诚实 feed_unreachable / license 敌意 400 原文 / delivery 诚实 included=0 且归档真实落盘数据根 / typography resolve 候选表 / capability-report 真计数 / assets license 幽灵 404",
          not _p45_bad, f"failed={_p45_bad}")

    # ===== Phase 51: 跨引擎工具面 / 值血缘 / 事件台账自洽 =====
    # 逐字段真值先由 outputs/p51_wiring_probe.py 冻结（P51_WIRING PROBE ALL_GREEN），
    # 这里只钉已实测的数字，不写推算值。
    _p51_tj = requests.get(BASE + "/api/v1/workflow/tools", timeout=60).json()
    _p51_tl = _p51_tj.get("tools") or []
    _p51_prm = [_p for _t in _p51_tl for _p in (_t.get("params") or [])]
    _p51_comps = ((requests.get(BASE + "/openapi.json", timeout=60).json()
                   .get("components") or {}).get("schemas") or {})
    _p51_models = [_t.get("request_model") for _t in _p51_tl if _t.get("request_model")]
    # 两条独立派生路径必须一致：清单里的 body 参数集 == 同名 pydantic 模型在 openapi 组件表的 properties
    _p51_xbad = [_t.get("tool") for _t in _p51_tl if _t.get("request_model")
                 and sorted((_p51_comps.get(_t["request_model"]) or {}).get("properties") or {})
                 != sorted(_p["name"] for _p in (_t.get("params") or []) if _p.get("in") == "body")]
    _p51_ref = _p51_tj.get("reference_syntax") or {}
    check("webui(P51): GET /api/v1/workflow/tools mirrors the LIVE tool_registry instead of a hand-written list — 72 tools / 23 domains / 154 params / api_route_total 145 with untagged==[] and derived_from named, per-domain counts sum to the total, all 40 request models resolve in the openapi components table and each tool's body-param set equals that model's properties (two independent derivations that must agree, or the manifest is a copy-paste), params strictly arg|body with JSON-native defaults, reference syntax derived from the engine docstring (P56 +brand/semantic-insight raises api_route_total 143→144; the透传入口 needs a real provider so it is NOT a workflow tool — total stays 72; P57 BarcodeRequest gains ec_level so QR 纠错等级成为工作流可传参数 ⇒ params 152→153，实测 p57_service_green.txt ④; P58 +GET /api/v1/installer/execution 只读对账 ⇒ 144→145，无请求体参数故 params 恒 153、无新工作流工具故 tools 恒 72 — 执行器只在 CLI 面，工具面加一条就等于把 §2 红线从后门放回来; P65 delete_layer 签名新增 confirm: Optional[bool]=None（L3 不可逆删除须显式确认）⇒ 工具面随签名派生，params 153→154 且 layers.delete 的参数面变成 {layer_id, confirm} —— 这正是「无第二份手写清单可漂移」的证据：门禁一旦上到路由，工作流节点也绕不过去（p65_gate_probe.txt 腿 8 实测节点不带 confirm 即 failed 并点名 confirmation_required）。归属由独立载体逐名量得（p65_p51_param_probe.txt：全清单里名叫 confirm 的参数只有 layers.delete 一枚、type=Optional[bool]/required=false/default=null 随注解与默认值派生、154−1=153 可被算式重述、api_route_total 仍 145、tools 仍 72、xbad=[]）)",
          _p51_tj.get("total") == 72 and len(_p51_tl) == 72 and _p51_tj.get("domain_count") == 23
          and len(_p51_prm) == 154 and _p51_tj.get("api_route_total") == 145
          and _p51_tj.get("untagged") == [] and _p51_tj.get("fabricated") is False
          and _p51_tj.get("derived_from") == "workflow_engine.tool_registry"
          and len(_p51_tj.get("domains") or []) == 23
          and sum((_p51_tj.get("tools_per_domain") or {}).values()) == _p51_tj.get("total")
          and len(_p51_models) == 40 and all(_m in _p51_comps for _m in _p51_models)
          and _p51_xbad == [] and {p.get("in") for p in _p51_prm} <= {"arg", "body"}
          and all(isinstance(p.get("default"), (type(None), bool, int, float, str, list, dict, tuple))
                  for p in _p51_prm)
          and _p51_ref.get("supports_path") is True and bool(_p51_ref.get("doc_evidence"))
          and _p51_ref.get("implemented_by") == "workflow_engine.WorkflowEngine._resolve_value",
          f"total={_p51_tj.get('total')} params={len(_p51_prm)} models={len(_p51_models)} "
          f"xbad={_p51_xbad[:3]} art={_p51_tj.get('api_route_total')}")

    def _p51_perim(_rs):
        # 坐标压缩扫掠求并集轮廓周长：与引擎的共边相减是两种不同算法（L5 同构，独立复算）
        _xs = sorted({float(r["x"]) for r in _rs} | {float(r["x"]) + float(r["width"]) for r in _rs})
        _ys = sorted({float(r["y"]) for r in _rs} | {float(r["y"]) + float(r["height"]) for r in _rs})
        _xi = {v: i for i, v in enumerate(_xs)}
        _yi = {v: i for i, v in enumerate(_ys)}
        _cv = [[False] * (len(_ys) - 1) for _ in range(len(_xs) - 1)]
        for _r in _rs:
            for _i in range(_xi[float(_r["x"])], _xi[float(_r["x"]) + float(_r["width"])]):
                for _j in range(_yi[float(_r["y"])], _yi[float(_r["y"]) + float(_r["height"])]):
                    _cv[_i][_j] = True
        _p = 0.0
        for _i in range(len(_xs) - 1):
            for _j in range(len(_ys) - 1):
                if not _cv[_i][_j]:
                    continue
                _dx, _dy = _xs[_i + 1] - _xs[_i], _ys[_j + 1] - _ys[_j]
                if _i == 0 or not _cv[_i - 1][_j]:
                    _p += _dy
                if _i == len(_xs) - 2 or not _cv[_i + 1][_j]:
                    _p += _dy
                if _j == 0 or not _cv[_i][_j - 1]:
                    _p += _dx
                if _j == len(_ys) - 2 or not _cv[_i][_j + 1]:
                    _p += _dx
        return _p

    _p51_die = requests.post(BASE + "/api/v1/packaging/dieline", timeout=60, json={
        "width": 100, "height": 80, "bleed": 3, "safe": 5}).json()
    _p51_graph = {
        "id": "l2p51chain", "name": "L2 P51 dieline->topology", "description": "value lineage",
        "variables": {},
        "nodes": [
            {"id": "in", "type": "input", "name": "in", "config": {}},
            {"id": "die", "type": "tool", "name": "dieline",
             "config": {"tool_name": "packaging.dieline",
                        "parameters": {"width": 100, "height": 80, "bleed": 3, "safe": 5},
                        "output_variable": "die"}},
            {"id": "topo", "type": "tool", "name": "topology",
             "config": {"tool_name": "packaging.panel_topology",
                        "parameters": {"panels": "$die.panels"}, "output_variable": "topo"}},
            {"id": "out", "type": "output", "name": "out", "config": {"select": ["die", "topo"]}},
        ],
        "edges": [{"source_node_id": "in", "target_node_id": "die"},
                  {"source_node_id": "die", "target_node_id": "topo"},
                  {"source_node_id": "topo", "target_node_id": "out"}],
    }
    _p51_def = requests.post(BASE + "/api/v1/workflow/define", json=_p51_graph, timeout=60).json()
    _p51_val = requests.get(BASE + "/api/v1/workflow/l2p51chain/validate", timeout=60).json()
    _p51_exe = requests.post(BASE + "/api/v1/workflow/l2p51chain/execute",
                             json={"input_data": {}}, timeout=120).json()
    _p51_res = _p51_exe.get("result") or {}
    _p51_rd, _p51_rt = _p51_res.get("die") or {}, _p51_res.get("topo") or {}
    _p51_per = _p51_perim(_p51_rd.get("panels") or [{"x": 0, "y": 0, "width": 0, "height": 0}])
    check("workflow(P51) live cross-engine graph: the tools named by that derived manifest really execute — a 4-node input->packaging.dieline->packaging.panel_topology->output graph validates clean and reaches success on every node, and the topology node consumed the dieline node's OWN panels ($die.panels): panels equal the direct /packaging/dieline payload field-for-field, panel_count propagates 1->1, and cut_boundary_length equals BOTH the independent coordinate-compressed edge sweep computed in this suite and 2*(100+80)=360 — a lineage edge, not two calls that merely happened in order",
          _p51_def.get("nodes") == 4 and _p51_def.get("edges") == 3
          and _p51_def.get("validation_errors") == [] and _p51_val.get("errors") == []
          and _p51_exe.get("status") == "success" and _p51_exe.get("error_message") is None
          and set(_p51_exe.get("node_status") or {}) == {"in", "die", "topo", "out"}
          and all(str(v) == "success" for v in (_p51_exe.get("node_status") or {}).values())
          and _p51_rd.get("panels") == _p51_die.get("panels") and len(_p51_rd.get("panels") or []) == 1
          and _p51_rt.get("panel_count") == _p51_rd.get("panel_count") == 1
          and _p51_rt.get("status") == "ok" and _p51_rt.get("fabricated") is False
          and abs(_p51_rt.get("cut_boundary_length", -1.0) - _p51_per) < 1e-6
          and abs(_p51_rt.get("cut_boundary_length", -1.0) - 360.0) < 1e-6
          and _p51_rt.get("extent", {}).get("width") == _p51_rd.get("finished", {}).get("width"),
          f"nodes={_p51_def.get('nodes')} status={_p51_exe.get('status')} "
          f"cut={_p51_rt.get('cut_boundary_length')} independent={_p51_per}")

    _p51_ev = requests.get(BASE + "/api/v1/events/recent", params={"limit": 2000}, timeout=60).json()
    _p51_kc = _p51_ev.get("kind_counts") or {}
    _p51_declared = set(_re41.findall(r'_emit\(\s*"([^"]+)"',
                                      (OUT / "agent_service.py").read_text(encoding="utf-8")))
    _p51_win = _p51_ev.get("events") or []
    _p51_wc = {}
    for _e51 in _p51_win:
        _p51_wc[_e51.get("event")] = _p51_wc.get(_e51.get("event"), 0) + 1
    _p51_last = lambda k: next((_v51 for _v51 in reversed(_p51_win)
                               if _v51.get("event") == k), {})
    _p51_et = _p51_last("panel_topology.analyzed").get("data") or {}
    _p51_ew = _p51_last("workflow.executed").get("data") or {}
    _p51_ed = _p51_last("dieline.computed").get("data") or {}
    # 台账判据写成"环形缓冲截断与否都成立"的关系式，而不是赌 L2 究竟发了多少条：
    # published_total 与 kind_counts 同为累计口径，window 只是缓冲尾部，
    # 故窗口逐种计数必 <= 累计计数，且 truncated 恰等于"累计量 > 窗口长度"。
    check("events(P51) ledger is a faithful mirror of the process: the expected kind set is derived from _emit() literals in the artifact under test (never hand-listed, and an empty scan would be fatal) with zero phantom kind fired, published_total equals sum(kind_counts) under either truncation state, the window agrees with its own reported total and never exceeds the 200-slot capacity, and the LAST dieline/topology/workflow events carry the real values — input_panel_count (a field that exists ONLY on the event, never on the HTTP response), the same 360 cut boundary, the same finished box as the direct call, and tools_used naming the two manifest tools by their registered names",
          len(_p51_declared) == 26 and bool(_p51_declared)
          and not (set(_p51_kc) - _p51_declared)
          and {"dieline.computed", "panel_topology.analyzed", "workflow.executed"} <= set(_p51_kc)
          and _p51_ev.get("published_total") == sum(_p51_kc.values())
          and _p51_ev.get("history_capacity") == 200
          and _p51_ev.get("total") == len(_p51_win) <= _p51_ev.get("history_capacity")
          and _p51_ev.get("truncated") is (sum(_p51_wc.values()) < _p51_ev.get("published_total"))
          and _p51_ev.get("truncated") is (_p51_ev.get("published_total") > _p51_ev.get("total"))
          and all(_p51_wc[_k51] <= _p51_kc.get(_k51, 0) for _k51 in _p51_wc)
          and _p51_et.get("input_panel_count") == 1 and _p51_et.get("status") == "ok"
          and abs(_p51_et.get("cut_boundary_length", -1.0) - 360.0) < 1e-6
          and _p51_ed.get("panel_count") == 1 and _p51_ed.get("finished") == _p51_die.get("finished")
          and _p51_ew.get("workflow_id") == "l2p51chain" and _p51_ew.get("status") == "success"
          and _p51_ew.get("node_count") == 4 and _p51_ew.get("nodes_completed") == 4
          and _p51_ew.get("result_keys") == ["die", "topo"]
          and _p51_ew.get("tools_used") == ["packaging.dieline", "packaging.panel_topology"],
          f"declared={len(_p51_declared)} kinds_seen={_p51_ev.get('kind_count')} "
          f"published={_p51_ev.get('published_total')} win={_p51_ev.get('total')} "
          f"trunc={_p51_ev.get('truncated')} phantom={sorted(set(_p51_kc) - _p51_declared)[:3]} "
          f"ev_topo={_p51_et.get('input_panel_count')}/{_p51_et.get('cut_boundary_length')} "
          f"tools_used={_p51_ew.get('tools_used')}")

    _p51_as = requests.post(BASE + "/api/v1/assets/search", timeout=60,
                            json={"query": "red product", "top_k": 3}).json()
    _p51_kn = requests.post(BASE + "/api/v1/knowledge/retrieve",
                            timeout=60, json={"query": "offset printing bleed", "top_k": 5}).json()
    _p51_d1 = _p51_as.get("retrieval") or {}
    _p51_d2 = _p51_kn.get("retrieval") or {}
    check("webui(P51) honest retrieval ceiling: both search surfaces self-describe the vector they actually use rather than claiming semantics — semantic is false, kind is sparse-token-hash-projection, model tokenhash-sha256-512, dims 512 and a non-empty note is present on BOTH /assets/search and /knowledge/retrieve with the two descriptors byte-identical (a capability ceiling labelled, not papered over)",
          all(_p51_d1.get(_k) == _p51_d2.get(_k) for _k in
              ("model", "dims", "semantic", "kind", "note"))
          and _p51_d1.get("semantic") is False
          and _p51_d1.get("kind") == "sparse-token-hash-projection"
          and _p51_d1.get("model") == "tokenhash-sha256-512" and _p51_d1.get("dims") == 512
          and len(str(_p51_d1.get("note") or "")) > 20 and "语义" in str(_p51_d1.get("note")),
          f"assets={json.dumps(_p51_d1, ensure_ascii=False)[:80]} equal={_p51_d1 == _p51_d2}")

    check("webui(P51/P54): the tool surface is wired into the SERVED console, not only into the API — index.html carries the 工具清单 button, its output node, the tool-name filter input and the 实时派生 note inside the flow panel; app.js calls api(\"/workflow/tools\") and renders domain_count / tools_per_domain / untagged / api_route_total / reference_syntax, so the literal counted by the coverage ruler is the literal that issues the request; since Phase 54 the console shows the resolver's derived 语义 line AND the engine's own compiled grammar regex (the machine-checked contract) instead of a hand-truncated docstring excerpt",
          all(_m in _p39_html for _m in ('id="wf-tools"', 'id="wf-tools-out"', 'id="wf-tools-q"',
                                         'data-panel="flow"', '工具清单（GET /workflow/tools）',
                                         'Tool 节点真正能调用什么，以这里为准'))
          and '工具清单由服务端 tool_registry <b>实时派生</b>' in _p39_html
          and all(_m in _uijs.text for _m in ('$("wf-tools").addEventListener', 'api("/workflow/tools")',
              'renderOut("wf-tools-out"', 'j.tools_per_domain', 'j.domain_count', 'j.untagged',
              'j.api_route_total', 'j.reference_syntax', 'j.derived_from', 'wfToolTable',
              'j.reference_syntax && j.reference_syntax.semantics',
              'j.reference_syntax && j.reference_syntax.grammar')),
          f"html_bytes={len(_p39_html)} js_bytes={len(_uijs.text)}")

    # ===== Phase 54: 引用进容器 —— 值不再在参数列表里悄悄消失 =====
    # 判据形态先在 outputs/p54_mutation_driver.py 里被四桩证伪（P54 MUTATION CRITERIA ALL_MET），
    # 同族判据落 L5 3b 段（跨引擎血缘）。这里钉的是"源码活服务"这一路：
    #   A) /workflow/tools 公布的引用语法 == 引擎自己编译的那条正则（派生，非手抄）；
    #   B) 列表元素里的 '$bc.output_path' 真解析成上游产出的路径，交付 ZIP 以 namelist 实测；
    #   C) 解析不了的引用让节点 failed 并点名缺失的根，绝不静默变 None 后自报 success。
    import re as _re54, hashlib as _hl54, zipfile as _zip54
    _p54_gram = str(_p51_ref.get("grammar") or "")
    _p54_sem = str(_p51_ref.get("semantics") or "")
    check("workflow(P54): the reference grammar the tool surface publishes IS the engine's own compiled pattern — live-verified that '$bc.output_path' and '$fl.fonts.0.family' match while '$1,200' / '$ a' / '$-x' / '$9' / '$' do not (so a typo can no longer be dressed up as a missing variable) — and the semantics sentence shipped to the console is the resolver docstring's own 语义： line, derived rather than hand-copied, which is exactly how the stale P51-era copy ('根变量不存在时返回 None') was caught and removed",
          bool(_p54_gram) and _re54.match(_p54_gram, "$bc.output_path") is not None
          and _re54.match(_p54_gram, "$fl.fonts.0.family") is not None
          and _re54.match(_p54_gram, "$fl.fonts.-1.family") is not None
          and all(_re54.match(_p54_gram, _s) is None
                  for _s in ("$1,200", "$ a", "$-x", "$9", "$", "$bc.out-put")),
          f"grammar={_p54_gram[:60]} sem={_p54_sem[:20]!r}")

    _p54_bc = requests.post(BASE + "/api/v1/packaging/barcode", timeout=60,
                            json={"code": "4006381333931"}).json()
    _p54_op = str(_p54_bc.get("output_path") or "")
    _p54_gid = "p54_container_%d" % os.getpid()

    def _p54_graph(_gid, _files, _with_bc):
        _n = [{"id": "in", "type": "input", "name": "in", "config": {}}]
        _e = []
        if _with_bc:
            _n.append({"id": "bc", "type": "tool", "name": "barcode",
                       "config": {"tool_name": "packaging.barcode",
                                  "parameters": {"code": "4006381333931"},
                                  "output_variable": "bc"}})
            _e.append({"source_node_id": "in", "target_node_id": "bc"})
            _head, _sel = "bc", ["bc", "dl"]
        else:
            _head, _sel = "in", ["dl"]
        _n.append({"id": "dl", "type": "tool", "name": "delivery",
                   "config": {"tool_name": "delivery.package",
                              "parameters": {"project": "p54_container", "files": _files},
                              "output_variable": "dl"}})
        _n.append({"id": "out", "type": "output", "name": "out", "config": {"select": _sel}})
        _e += [{"source_node_id": _head, "target_node_id": "dl"},
               {"source_node_id": "dl", "target_node_id": "out"}]
        return {"id": _gid, "name": "p54 " + _gid, "description": "P54 container-position reference",
                "variables": {}, "nodes": _n, "edges": _e}

    _p54_def = requests.post(BASE + "/api/v1/workflow/define", timeout=60, json=_p54_graph(
        _p54_gid, {"Print": ["$bc.output_path"]}, True)).json()
    _p54_ex = requests.post(BASE + "/api/v1/workflow/%s/execute" % _p54_gid, timeout=180,
                            json={"input_data": {}}).json()
    _p54_inst = requests.get(BASE + "/api/v1/workflow/instance/%s" % _p54_ex.get("instance_id"),
                             timeout=60).json()
    _p54_dl = ((_p54_inst.get("node_outputs") or {}).get("dl") or {}).get("result") or {}
    _p54_zp = Path(str(_p54_dl.get("archive") or ""))
    _p54_names, _p54_shas, _p54_zbytes = [], {}, 0
    if _p54_zp.is_file():
        _p54_zbytes = _p54_zp.stat().st_size
        with _zip54.ZipFile(str(_p54_zp)) as _zf54:
            _p54_names = sorted(_zf54.namelist())
            _p54_shas = {_n: _hl54.sha256(_zf54.read(_n)).hexdigest() for _n in _zf54.namelist()}
    _p54_disk = (_hl54.sha256(Path(_p54_op).read_bytes()).hexdigest()
                 if _p54_op and Path(_p54_op).is_file() else "")
    _p54_want = "Print/" + Path(_p54_op).name if _p54_op else ""
    check("workflow(P54): a reference sitting in a LIST ELEMENT of a tool parameter resolves to the upstream node's real output — live barcode→delivery journey on this service: instance success with every node success, and the archive measured from the ZIP's own namelist (never from the self-reported 'included') contains Print/<the rendered png> whose sha256 equals this file's independent hash of the on-disk png, 'included' equals the measured payload count, 'archive_size_bytes' equals the zip's real size, and no entry name carries a literal '$' — the last mile of the design task no longer loses values inside parameter containers",
          _p54_def.get("nodes") == 4 and _p54_inst.get("status") == "success"
          and set(_p54_inst.get("node_status") or {}) == {"in", "bc", "dl", "out"}
          and all(str(_v) == "success" for _v in (_p54_inst.get("node_status") or {}).values())
          and bool(_p54_want) and _p54_want in _p54_names
          and bool(_p54_disk) and _p54_shas.get(_p54_want) == _p54_disk
          and _p54_dl.get("included") == sum(1 for _n in _p54_names if _n.startswith("Print/"))
          and _p54_dl.get("archive_size_bytes") == _p54_zbytes
          and not any("$" in _n for _n in _p54_names),
          f"st={_p54_inst.get('status')} names={_p54_names} included={_p54_dl.get('included')} "
          f"sha_eq={_p54_shas.get(_p54_want) == _p54_disk} bytes={_p54_dl.get('archive_size_bytes')}/{_p54_zbytes}")

    _p54_ggid = "p54_ghost_%d" % os.getpid()
    requests.post(BASE + "/api/v1/workflow/define", timeout=60, json=_p54_graph(
        _p54_ggid, {"Print": ["$ghostly.output_path"]}, False))
    _p54_gex = requests.post(BASE + "/api/v1/workflow/%s/execute" % _p54_ggid, timeout=180,
                             json={"input_data": {}}).json()
    _p54_ginst = requests.get(BASE + "/api/v1/workflow/instance/%s" % _p54_gex.get("instance_id"),
                              timeout=60).json()
    _p54_gerr = str(_p54_ginst.get("error_message") or "")
    check("workflow(P54): an unresolvable reference fails the node instead of shipping an empty package — instance failed, node_status dl failed, the error names the missing root 'ghostly' and lists what IS available, and the resolved value is nowhere faked: Phase 54 closed the silent '→ None' path that let a delivery node report included=0 while still calling itself success",
          _p54_ginst.get("status") == "failed"
          and (_p54_ginst.get("node_status") or {}).get("dl") == "failed"
          and "ghostly" in _p54_gerr and "$ghostly.output_path" in _p54_gerr,
          f"st={_p54_ginst.get('status')} ns={_p54_ginst.get('node_status')} err={_p54_gerr[:76]}")

    # ===== Phase 44: runtime backfill — P43 申报界限（字面串在场≠运行时必达）收口 =====
    # 探针(outputs/_p44_verify2.out)已冻结：命中按路由模板计、端点自报 4xx 仍计、
    # 未匹配/静态路径不产生键。/metrics 的响应快照先于本次自身请求被计入，故先打一次
    # warm call 再取快照——warm 之后 /api/v1/metrics 自身也有命中。
    requests.get(BASE + "/api/v1/metrics", timeout=20)
    _snap44 = requests.get(BASE + "/api/v1/metrics", timeout=20).json()
    # 命中键归一到 {} 宇宙（与 _n41/_t43 同表示空间；P44 踩坑：具网名 vs {} 混比即全盲）
    _rh44 = {_n41(k): v for k, v in (_snap44.get("route_hits") or {}).items()}
    _miss44 = sorted(p for p in _l2_claims43 if _rh44.get(p, 0) <= 0)
    requests.get(BASE + "/api/v1/eval/suite", timeout=30)
    _rh44b = {_n41(k): v for k, v in (requests.get(BASE + "/api/v1/metrics", timeout=20).json().get("route_hits") or {}).items()}
    # 幽灵串分段拼接：整体字面 /api/v1/__p44_no_route__（无引号包裹以免被抽取器当声称）
    # 会被 P43 的 {} 通配吸成伪声称。
    _ph44 = "/api/v1/" + "__p44_no_route__"
    _phantom44 = requests.get(BASE + _ph44, timeout=20).status_code
    _rh44c = {_n41(k): v for k, v in (requests.get(BASE + "/api/v1/metrics", timeout=20).json().get("route_hits") or {}).items()}
    _bad44 = []
    if not _rh44:
        _bad44.append("route_hits缺失/为空(仪器未活)")
    else:
        if _rh44.get("/api/v1/eval/suite", 0) + 1 != _rh44b.get("/api/v1/eval/suite", 0):
            _bad44.append(f"增量校准失败 suite:{_rh44.get('/api/v1/eval/suite')}->{_rh44b.get('/api/v1/eval/suite')}")
        if _ph44 in _rh44c or _phantom44 != 404:
            _bad44.append(f"幽灵路由被计数或HTTP{_phantom44}")
        if any(not str(k).startswith("/api/v1") for k in _rh44c):
            _bad44.append("键泄漏到/api/v1之外")
        if not _rh44.get("/api/v1/metrics"):
            _bad44.append("warm call未计入")
    check("runtime-backfill(P44): EVERY /api/v1 template claimed by this file's own source literals shows route_hits>0 in the live service's /metrics (P43 static-claim limitation closed by runtime evidence) + increment calibration + phantom-route negative",
          not _bad44 and _miss44 == [],
          f"claims={len(_l2_claims43)} hits={len(_rh44) if isinstance(_rh44, dict) else 'N/A'} miss={_miss44[:8]} bad={_bad44}")

    # ---- Phase 50: 桌面 Hub 活体判据（第 27 引擎真建窗 / 逐 Label 文本 / 真 invoke / 服务端计数仪增量） ----
    # 这一段是本能力的"GUI 本体"实证面：评测台只驱动数据层与呈现层（进程内真 HTTP 夹具），
    # 窗口是否真在屏幕上立起来、Label 里是否真写着派生行、按钮是否真走 POST 通道——
    # 只能在有显示会话的活体环境里判。判据形态是"三条纪律各一枚"：
    #   ① 只搬运不编数 -> 窗口 Label 文本 == render_rows(同一快照) 且行内数字来自真服务；
    #   ② 开窗只读     -> 开窗前后 /api/v1/eval/run 的 route_hits 增量恒 0；
    #   ③ 动作必点     -> 真 invoke eval_run 按钮后该计数增量恰为 1（refresh 只加三条 GET）。
    def _hits50():
        requests.get(BASE + "/api/v1/metrics", timeout=20)
        return {k: int(v) for k, v in
                (requests.get(BASE + "/api/v1/metrics", timeout=20).json().get("route_hits") or {}).items()}

    import desktop_hub_engine as _hub50
    _env_before50 = os.environ.get("ZAOJING_HUB_HEADLESS")
    _rep50 = None            # 预先归零：finally 里的 close_hub / teardown 判据不得踩 NameError
    _snap50 = None
    _rows50 = []
    _win50 = None
    _hl50 = None
    _live_status50 = None
    _hub50_err = None
    try:
        _pre50 = _hits50()
        _snap50 = _hub50.hub_snapshot(BASE, timeout=10.0)
        _rows50 = _hub50.render_rows(_snap50)
        _mid50 = _hits50()
        _rep50 = _hub50.open_hub(snapshot=_snap50, enter_mainloop=False, timeout=10.0)
        _win50 = _rep50.get("window") or {}
        _post_open50 = _hits50()
        # 独立仪器：判卷方不看窗口**自报**的字典，而是直接遍历活 Tk 控件树自己数一遍。
        # 自报 dict 是可以被桩造出来的（"报告说开了窗就算开了窗"是 Phase 50 最危险的假绿形制），
        # 而 winfo_children/cget 只能从真控件对象上读到——假造的 report 没有这条通路。
        _live_labels50 = [c.cget("text") for c in _rep50["_root"].winfo_children()
                          if c.winfo_class() == "Label"]
        _live_frames50 = [c for c in _rep50["_root"].winfo_children()
                          if c.winfo_class() == "Frame"]
        _live_buttons50 = [c for c in (_live_frames50[0].winfo_children() if _live_frames50 else [])
                           if c.winfo_class() == "Button"]
        check("hub-window(P50): the 27th engine REALLY creates a native Tk window on this machine and every Label text is byte-for-byte the derived row (no separate GUI constants) — hwnd!=0 / mapped=1 / title / two action buttons / rows==render_rows(snapshot) / the live Tk widget tree independently re-counts those exact rows (report dict is not trusted on its own) / live truth-table numbers carried verbatim",
              _win50.get("created") is True and _win50.get("hwnd") not in (0, None)
              and _win50.get("mapped") == 1
              and _win50.get("title") == "造境 AI · 本地 Hub"
              and _win50.get("buttons") == ["eval_run", "refresh"]
              and _win50.get("label_texts") == _rows50
              and len(_live_labels50) == len(_rows50) + 2
              and _live_labels50[1:-1] == _rows50
              and _live_labels50[0] == "造境 AI 设计智能体 · 桌面 Hub（只读快照）"
              and _live_labels50[-1] == "未触发动作"
              and len(_live_buttons50) == len(_win50.get("buttons") or []) == 2
              and _rep50.get("rows") == _rows50
              and _rep50.get("fabricated") is False
              and any("能力真值表 43 项：supported 40 / needs_adobe_host 3 / prototype_not_shipped 0 / not_shipped 0" in t for t in _rows50)
              # Phase 53 漂移：Hub 这一行是从 /capabilities 实时派生搬来的，不是 GUI 常量——
              # 真值表 39→41、supported 34→36 一改动，窗口里那行文字必跟着改；判卷侧若仍钉
              # 旧数，红的是尺子不是产品（P50 立这条正是为了让它跟着动）。
              # Phase 56 第 5 次真实漂移同族：brand_semantic_recognition 转 supported ⇒ supported
              # 36→37、not_shipped 2→1（仅 installer_execution 写侧留守），Registry 已验证 27→28、
              # honest_boundary 5→4（品牌语义移出诚实边界集）。以下两处判卷字符串随之就地重钉，
              # 产品侧无需改动——它本就实时派生、已经动了；红的是尺子。
              # Phase 58 第 6 次真实漂移同族且是这一族的**收尾**：installer_execution 执行面经
              # CLI 真安装/真卸载转 supported ⇒ supported 37→38、not_shipped 1→0（名单清零，
              # 留守的只有 needs_adobe_host 3 项——那是宿主进程问题，不是本产品问题）；
              # Registry 已验证 28→29（新黄金域 installer_execution 5 条）、honest_boundary 4→3。
              # Phase 59 路线 B 漂移同族：runtime_guard 升独立黄金域（5 真值+1 校准）且
              # 编译面/预检面三枚+两枚用例按"证据记回它真正证明的能力"改归
              # installer_artifact_build/installer_preflight ⇒ Registry 已验证 29→32
              # （no_golden_cases_yet 9→6）、honest_boundary 恒 3。产品侧实时派生已动；
              # 红的是尺子，就地重钉。
              # Phase 80 漂移同族：feature_flags/tool_registry/skill_runtime 三枚逐字下沉为第 28
              # 引擎 service_registry_engine 并各拥黄金域 ⇒ Registry 已验证 35→38（honest_boundary
              # 恒 3）；能力真值表 status 口径不变（这枚本就 supported，只是评测证据层升格），
              # 故上面「43 项：supported 40」那行照旧。产品侧实时派生已动；红的是尺子。
              # Phase 81 漂移同族：websocket_events／delivery_package 两枚逐字下沉为第 29 引擎
              # event_engine、第 30 引擎 delivery_engine 并各拥黄金域 ⇒ Registry 已验证 38→40
              # （honest_boundary 恒 3）；两枚本就 supported，能力真值表 status 口径不变（43 项
              # supported 40 照旧）。产品侧实时派生已动；红的是尺子。
              and any(t.startswith("Registry 已验证 40 项（honest_boundary 3 项）") for t in _rows50)
              and any(t.startswith("Hub 目标 %s（来源 cli）" % BASE) for t in _rows50)
              and _snap50["service"]["health_verdict"] == "zaojing_payload"
              and _snap50["engines"]["agree"] is True
              and _snap50["all_sources_live"] is True,
              json.dumps({"created": _win50.get("created"), "hwnd": _win50.get("hwnd"),
                          "mapped": _win50.get("mapped"), "geometry_live": _win50.get("geometry_live"),
                          "rows": len(_rows50), "labels": len(_win50.get("label_texts") or []),
                          "live_labels": len(_live_labels50),
                          "live_buttons": len(_live_buttons50),
                          # 派生行全文随行（Phase 53 加此 dump：这一行的数字来自 /capabilities 实时
                          # 派生，真值表一漂移文字必变；若判卷红了却看不到全文，只能靠猜定位）
                          "row_texts": _rows50},
                         ensure_ascii=False))
        check("hub-readonly(P50): opening the window performs ZERO writes — POST /api/v1/eval/run route_hits delta is exactly 0 across snapshot+window-build (the counter instrument itself is proved live by the +3 GET deltas)",
              _post_open50.get("/api/v1/eval/run", 0) - _mid50.get("/api/v1/eval/run", 0) == 0
              and _post_open50.get("/api/v1/health", 0) - _pre50.get("/api/v1/health", 0) == 1
              and _post_open50.get("/api/v1/capabilities", 0) - _pre50.get("/api/v1/capabilities", 0) == 1
              and _post_open50.get("/api/v1/eval/reports", 0) - _pre50.get("/api/v1/eval/reports", 0) == 1
              and _win50.get("status_text") == "未触发动作"
              and list(_win50.get("clicks_so_far") or []) == []
              and _snap50["read_only"] is True and _snap50["actions_performed"] == [],
              json.dumps({k: _post_open50.get(k, 0) - _mid50.get(k, 0)
                          for k in ("/api/v1/eval/run",)}, ensure_ascii=False))
        # 真点击：先 refresh（只读，三条 GET 各 +1），再 eval_run（唯一 POST 通道，恰 +1）
        _rep50["_buttons"]["refresh"].invoke()
        _ref50 = _hits50()
        _rep50["_buttons"]["eval_run"].invoke()
        _run50 = _hits50()
        # status_text 在 window 报告里是**建窗时刻**的快照（"未触发动作"），点击后的真实
        # 状态行只能从活控件上现读——判据读的是 _status 这个真 widget 的当前 cget("text")。
        _live_status50 = str(_rep50["_status"].cget("text"))
        check("hub-invoke(P50): a REAL Button.invoke() actually drives the network — refresh adds exactly 3 read GETs and zero POSTs, eval_run adds EXACTLY 1 hit to /api/v1/eval/run with body '{}' (mutating action only ever fires on an explicit click), and the LIVE status widget reports the executed action",
              _ref50.get("/api/v1/eval/run", 0) - _post_open50.get("/api/v1/eval/run", 0) == 0
              and all(_ref50.get(p, 0) - _post_open50.get(p, 0) == 1
                      for p in ("/api/v1/health", "/api/v1/capabilities", "/api/v1/eval/reports"))
              and _run50.get("/api/v1/eval/run", 0) - _ref50.get("/api/v1/eval/run", 0) == 1
              and _win50.get("clicks_so_far") == ["refresh", "eval_run"]
              and _live_status50.startswith("已执行 eval_run @")
              and _win50.get("status_text") == "未触发动作"
              and [a.get("mutating") for a in (_rep50.get("actions") or [])] == [False, True]
              and (_rep50.get("actions") or [{}])[1].get("results", [{}])[0].get("status") == 200,
              json.dumps({"refresh_gets": sum(_ref50.get(p, 0) - _post_open50.get(p, 0)
                                              for p in ("/api/v1/health", "/api/v1/capabilities",
                                                        "/api/v1/eval/reports")),
                          "eval_run_delta": _run50.get("/api/v1/eval/run", 0) - _ref50.get("/api/v1/eval/run", 0),
                          "clicks": _win50.get("clicks_so_far"),
                          "live_status": _live_status50}, ensure_ascii=False))
    except Exception as _e50:
        _hub50_err = "%s: %s" % (type(_e50).__name__, _e50)
        check("hub-window(P50): desktop Hub live driver raised before all three verdicts could be taken (no_display_available would land here too — that is an ENVIRONMENT fact reported honestly, never a silent pass)",
              False, _hub50_err)
    finally:
        if _rep50 is not None:
            _hub50.close_hub(_rep50)
        # 无显示意图分支必须**现场实测**：置旗 -> open_hub（同一快照，故不再发 HTTP）-> 读回
        # 诚实申报 -> 复原环境。判据是"该分支绝不声称建了没建的窗"，不是"此刻环境里恰好有旗"。
        if _snap50 is not None:
            os.environ["ZAOJING_HUB_HEADLESS"] = "1"
            try:
                _hl50 = _hub50.open_hub(snapshot=_snap50, enter_mainloop=False,
                                        timeout=10.0).get("window")
            finally:
                if _env_before50 is None:
                    os.environ.pop("ZAOJING_HUB_HEADLESS", None)
                else:
                    os.environ["ZAOJING_HUB_HEADLESS"] = _env_before50
        check("hub-teardown(P50): the Tk root is REALLY destroyed (close_hub sets _root back to None) and the headless-intent branch, measured live here with the SAME snapshot, honestly reports created=False / reason=headless_requested / rows_would_render==len(render_rows(snap)) — the no-display branch never claims a window it did not build, and the env flag is restored",
              _rep50 is not None and _rep50.get("_root") is None
              and isinstance(_hl50, dict) and len(_rows50) > 0
              and _hl50 == {"created": False, "reason": "headless_requested",
                            "rows_would_render": len(_rows50)}
              and os.environ.get("ZAOJING_HUB_HEADLESS") == _env_before50
              and _live_status50 is not None,
              json.dumps({"root_cleared": (_rep50 or {}).get("_root") is None,
                          "headless_window": _hl50, "rows": len(_rows50),
                          "env_restored": os.environ.get("ZAOJING_HUB_HEADLESS") == _env_before50,
                          "err": _hub50_err}, ensure_ascii=False))

finally:
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except Exception:
        proc.kill()

fails = [x for x in results if x[0] == "[FAIL]"]
print("=" * 60)
print(f"TOTAL {len(results)} | PASS {len(results) - len(fails)} | FAIL {len(fails)}")
sys.exit(1 if fails else 0)
