"""Post-build verification: artifacts integrity + EXE smoke test against real endpoints."""
import sys, os, json, time, zipfile, subprocess, signal, tempfile
sys.stdout.reconfigure(errors="backslashreplace")
from pathlib import Path
import requests
from PIL import Image

# 仓库根目录 = 本脚本所在目录。早先写死本机绝对路径会让任何其它检出（含 CI runner）
# 在 dist/、便携 ZIP、portable/ 上必然找不到文件，此处改为相对脚本自身定位。
OUT = Path(__file__).resolve().parent
EXE = OUT / "dist" / "ZaojingAI.exe"
ZIP = OUT / "zaojing-ai-1.0.8-portable.zip"
PORT_DIR = OUT / "portable"

results = []
def check(name, cond, detail=""):
    tag = "[PASS]" if cond else "[FAIL]"
    results.append((tag, name, detail))
    print(f"{tag} {name} {detail}")

# ---- 1. artifact basics ----
check("exe exists", EXE.exists(), f"{EXE.stat().st_size/1024/1024:.2f} MB" if EXE.exists() else "")
check("zip exists", ZIP.exists(), f"{ZIP.stat().st_size/1024/1024:.2f} MB" if ZIP.exists() else "")

# ---- 2. portable zip structure ----
names = set()
with zipfile.ZipFile(ZIP) as z:
    bad = z.testzip()
    names = set(z.namelist())
check("zip integrity", bad is None)
check("zip has exe", f"{EXE.name}" in names)
check("zip has portable.flag", "portable.flag" in names)
check("zip has README.txt", "README.txt" in names)
check("zip has docs/", any(n.startswith("docs/") for n in names), f"{sum(1 for n in names if n.startswith('docs/'))} entries")
check("zip has Data dirs", any(n.startswith("Data/") for n in names), f"{sum(1 for n in names if n.startswith('Data/'))} entries")
check("zip 安装说明 latest", "docs/安装说明.md" in names or "docs/\u5b89\u88c5\u8bf4\u660e.md" in names)

# ---- 3. bundled modules inside EXE (via runtime probe below) ----
# run the portable EXE from the portable/ directory (has portable.flag)
port_exe = PORT_DIR / "ZaojingAI.exe"
check("portable dir exe", port_exe.exists())

# ---- 红线条款前置条件复位：确保冻结 EXE 启动时"零 Provider 配置" ----
# provider 配置会随便携版持久化到 Data/providers/providers.json（真实可移植特性：跨重启记忆配置）。
# 引擎在 EXE 启动时一次性把 providers.json 载入内存，故必须在 Popen 之前删除该文件，
# 否则上一轮 run 遗留的 provider 会让 /provider/chat(无 provider_id) 默认路由到已失效端口、
# 诚实返回 unreachable，而非本用例要验证的 no_provider_configured。此系测试前置条件修复，非产品改动。
_prov_state = PORT_DIR / "Data" / "providers" / "providers.json"
if _prov_state.exists():
    try:
        _prov_state.unlink()
    except Exception as _e:
        print(f"[WARN] 无法清理 provider 持久化状态（红线条款可能因上轮遗留配置而失败）: {_e}")

# ---- Phase 45 扩展同款红线前置条件复位：update/nas 配置同样随便携版跨重启持久化，
# 而本套红线断言（no_update_configured / not_configured）测的就是零配置诚实态。
# P45 写面板活体检查会在本轮真配置二者，故必须在每次 Popen 前复位，非产品改动。
for _rst45 in (PORT_DIR / "Data" / "update" / "update-config.json",
               PORT_DIR / "Data" / "nas" / "nas-config.json"):
    if _rst45.exists():
        try:
            _rst45.unlink()
        except Exception as _e:
            print(f"[WARN] 无法清理 {_rst45.name} 持久化状态（红线诚实态可能受上轮遗留影响）: {_e}")

# ---- 清理上一轮（可能被中断）遗留的 ZaojingAI.exe：它会占住固定端口 8877 并保留旧内存配置 ----
try:
    import psutil as _ps, socket as _sk
    for _p in _ps.process_iter(["name"]):
        try:
            if _p.info["name"] and "zaojing" in _p.info["name"].lower():
                _p.terminate()
        except Exception:
            pass
    for _ in range(50):  # 有界轮询直到 8877 释放（最多约 5s），避免连到陈旧实例
        time.sleep(0.1)
        _s = _sk.socket()
        try:
            _busy = _s.connect_ex(("127.0.0.1", 8877)) == 0
        finally:
            _s.close()
        if not _busy:
            break
except ImportError:
    pass  # 无 psutil 时退化为仅依赖 providers.json 复位（端口占用则本轮如实失败）

env = dict(os.environ, ZAOJING_PORT="8877")
proc = subprocess.Popen([str(port_exe)], cwd=str(PORT_DIR), env=env,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
BASE = "http://127.0.0.1:8877"
try:
    up = False
    for _ in range(90):
        time.sleep(1)
        try:
            r = requests.get(BASE + "/api/v1/health", timeout=2)
            if r.status_code == 200:
                up = True
                break
        except Exception:
            if proc.poll() is not None:
                break
    check("EXE boots + serves /health", up, f"exit={proc.poll()}" if not up else "")

    h = r.json()
    check("EXE: mode=portable", h.get("mode") == "portable", h.get("data_root", ""))
    check("EXE: frozen=true", h.get("frozen") is True)
    ready = [k for k, v in h.get("engines", {}).items() if v == "ready"]
    check("EXE: all 30 engines bundled (health self-report covers evaluation/brand/runtime, +compare/bg_removal Phase 38, +panel_recognition Phase 47, +installer_manifest Phase 49, +desktop_hub Phase 50, +service_registry Phase 80, +event/delivery Phase 81)",
          len(ready) == 30 and h.get("engines", {}).get("panel_recognition") == "ready"
          and h.get("engines", {}).get("installer_manifest") == "ready"
          and h.get("engines", {}).get("desktop_hub") == "ready"
          and h.get("engines", {}).get("service_registry") == "ready"
          and h.get("engines", {}).get("event") == "ready"
          and h.get("engines", {}).get("delivery") == "ready", json.dumps(h.get("engines", {})))
    _ui35 = requests.get(BASE + "/")
    _uij35 = requests.get(BASE + "/ui/app.js")
    check("EXE: webui console bundled via --add-data — GET / serves HTML, /ui/app.js present (Phase 35)",
          _ui35.status_code == 200 and "text/html" in _ui35.headers.get("content-type", "")
          and "\u9020\u5883" in _ui35.text and _uij35.status_code == 200 and "renderOut" in _uij35.text,
          f"root={_ui35.status_code} js={_uij35.status_code}")
    _p39_nav = ["overview", "eval", "color", "packaging", "assets", "typo",
                "layout", "brand", "print", "knowledge", "revision", "ops", "flow",
                "detail", "write"]
    check("EXE(P39+P40+P42+P45): 15-panel console compiled into onefile — every nav has its panel section (P40 adds flow, P42 adds detail, P45 adds write)",
          _ui35.status_code == 200 and all(f'data-panel="{p}"' in _ui35.text for p in _p39_nav)
          and all(f'id="panel-{p}"' in _ui35.text for p in _p39_nav)
          and _ui35.text.count('data-panel="') == 15,
          f"panels={_ui35.text.count('data-panel=')}")
    _j39 = requests.get(BASE + "/ui/app.js").text
    check("EXE(P39+P49+P50): six new panel handlers shipped — layout/brand/print/knowledge/revision/ops markers compiled into onefile, and the offline engine line invents NO denominator (stale 0/22..0/26 all gone in the frozen archive)",
          all(_m in _j39 for _m in (
              "/layout/align", "/design/compare", "/generate/remove-background",
              "/brand/color-consistency", "/preflight/auto-fix", "/packaging/vdp/merge",
              "/knowledge/retrieve", "/revision/parse", "/review/run",
              "data-ops", "/runtime/diagnostic", "\u670d\u52a1\u4e0d\u53ef\u8fbe \u00b7 \u5f15\u64ce\u5206\u6bcd\u4e0d\u7f16\u9020"))
          and "0/26" not in _j39
          and "0/25" not in _j39
          and "0/24" not in _j39
          and "0/22" not in _j39,
          f"js_bytes={len(_j39)}")
    check("EXE(P40): flow-recovery panel + 8 new ops buttons shipped — workflow/batch/recovery handlers & read-only GETs compiled into onefile",
          all(_m in _j39 for _m in (
              "/workflow/define", "/workflow/instance/", "/batch/process",
              "/recovery/snapshot", "/recovery/transaction/begin",
              "/recovery/task/register", "/recovery/tasks/orphaned"))
          and all(_m in _ui35.text for _m in (
              'data-ops="/config"', 'data-ops="/uxp/bridge"',
              'data-ops="/runtime/install-actions"', 'data-ops="/eval/recommendations"',
              'data-ops="/typography/fallback-rules"')),
          f"js_bytes={len(_j39)}")

    import re as _re41x
    _oa41 = requests.get(BASE + "/openapi.json")
    _oap41 = [p for p in (_oa41.json().get("paths") or {}) if p.startswith("/api/v1/")] if _oa41.status_code == 200 else []
    _ns41 = {_re41x.sub(r"\{[^{}]*\}", "{}", p).rstrip("/") for p in _oap41}
    _dom41 = {p.split("/")[3] for p in _ns41}
    _l41 = set()
    # Phase 48 口径第四式：${API}/… 插值同样会发真 HTTP 请求（素材缩略图 <img src> 即此类），
    # 与受服务 app.js 派生器逐字同构；p48_freeze 实证此式独立贡献 1 条（113→114）。
    for _pat in (r'"(/[^"\n]*)"', r"'(/[^'\n]*)'", r"`(/[^`]*)`", r"\$\{API\}(/[^\"`\s]*)"):
        _l41.update(_m.group(1) for _m in _re41x.finditer(_pat, _j39))
    _l41.update(_m.group(1) for _m in _re41x.finditer(r'data-ops="([^"]+)"', _ui35.text))
    _w41, _g41 = set(), []
    for _raw in _l41:
        _s = _re41x.sub(r"\$\{[^{}]*\}", "{}", _raw).split("?")[0].rstrip("/")
        if _s in ("/", "/api/v1"):
            continue
        _seg = _s[1:].split("/")[0]
        if not _seg or _seg == "api" or _seg not in _dom41:
            continue
        _rest = _s[len("/api/v1"):] if _s.startswith("/api/v1") else _s
        _full = _re41x.sub(r"\{[^{}]*\}", "{}", "/api/v1" + _rest).rstrip("/")
        (_w41.add(_full) if _full in _ns41 else _g41.append(_full))
    # Phase 48：拒接清单 19→14（缩略图 + generate/plan/provider.chat/provider.images 转为真实接线）。
    # 片段不带 /api/v1 前缀，故 P43 声称收集器看不见它们——拒接项永不发起请求，写成整串必假红。
    # Phase 56：品牌语义透传入口 /brand/semantic-insight 需真实视觉 Provider 后端、控制台绝不自动发起
    # （同 provider.chat/images 的写侧拒接口径），故入拒接清单 ⇒ 14→15，notWired 由 _ns41 - _w41 派生。
    # Phase 58：/installer/execution 是**只读对账面**，控制台不自动发起（它读的是安装那台机器的账，
    # 而装机由 CLI 显式令牌触发；面板里去自动读别人的装机台账既无意义也越界）⇒ 15→16。
    # Phase 65：留守拒接项逐枚复核后**转正六枚**（nas/connect·list·lock·reconnect 以数据根全树哈希
    # 差集实证零落盘；runtime/port-check 真 bind 随即释放、不留进程句柄；layers/{} 的 PATCH/DELETE
    # 只改本服务数据根内文档，且 DELETE 由服务端 confirm=true 门禁把关、界面未勾选连请求都不发）。
    # ⇒ 分子 119→125、拒接 16→10、分母恒 135（本轮一条路由也没新增或退役，125+10 与 119+16 是同一
    # 端点宇宙的两种切法）。本判据读的是** THIS 冻结归档**里编译进去的那份 app.js，故只有串行
    # --clean 重打包之后才可能转绿——重打包前的红是对的方向：冻结 EXE 仍携带上一代次的控制台。
    # Phase 70：最后两枚转正（品牌语义显式点击 bsem-run + 安装台账只读卡 iex-run）⇒ 125→127、
    # 10→8、分母恒 135；只有携带 P70 控制台的新归档才可能同时满足 127/8。
    # Phase 71：拒接面清零——最后八枚（nas/download、runtime/single-instance、update/apply、
    # update/download、uxp handshake/poll/dispatch/result）逐枚过写侧五条件后转正（update/apply
    # 另加双确认）⇒ 127→135、8→0、分母恒 135；只有携带 P71 控制台的新归档才可能同时满足 135/0。
    # 换代纪律（与 L2 同源）：差集锚点 _p48_nw41 归零后退化为空集上的空真（P58 第十三类），故把
    # 义务搬到一个**种群恒为 8** 的显式集合上——八枚转正路由逐枚必须 ∈ wired，归零本身由
    # len(_w41)==135==len(_ns41) 的正向计数背书，不是"数到 0 就判绿"。
    _p71_promoted41 = {"/api/v1" + _f for _f in (
        "/nas/download", "/runtime/single-instance", "/update/apply", "/update/download",
        "/uxp/dispatch", "/uxp/handshake", "/uxp/poll", "/uxp/result")}
    _p48_nw41 = set()
    _p65_wired41 = {"/api/v1" + _f for _f in (
        "/layers/{}", "/nas/connect", "/nas/list", "/nas/lock", "/nas/reconnect",
        "/runtime/port-check")}
    check("EXE(P41+P48+P49+P51+P53+P56+P58+P65+P70+P71): coverage mechanization shipped — GET /openapi.json 135 api/v1 paths in onefile & derived wiring = 135/0 with zero ghost (P45 write panel +13, P48 thumbnail/generate/plan/provider-chat/provider-images +5 and ruler 4th pattern, P49 manifest/verify + installer/state +2, P51 workflow tool manifest +1, P53 installer/artifact + installer/preflight +2, P56 brand/semantic-insight 先拒接 +1, P58 installer/execution 先拒接 +1 ⇒ 134→135; P65 转正六枚 ⇒ 119→125/16→10; P70 再转正两枚 ⇒ 125→127/10→8; P71 末批八枚转正 ⇒ 127→135/8→0/分母不变; re-pinned on THIS archive after the P71 rebuild)",
          _oa41.status_code == 200 and len(_oap41) == 135
          and all(_m in _j39 for _m in ("renderCoverage", "p41Strip"))
          and _g41 == [] and len(_w41) == 135 and (_ns41 - _w41) == _p48_nw41
          and len(_p71_promoted41) == 8 and _p71_promoted41 <= _w41
          and _p65_wired41 <= _w41
          and "/api/v1/assets/{}/thumbnail" in _w41 and "/api/v1/generate" in _w41
          and "/api/v1/brand/semantic-insight" in _w41
          and "/api/v1/installer/execution" in _w41
          and {"/api/v1/installer/manifest/verify", "/api/v1/installer/state",
               "/api/v1/installer/artifact", "/api/v1/installer/preflight",
               "/api/v1/workflow/tools"} <= _w41,
          f"oa_paths={len(_oap41)} wired={len(_w41)} not={len(_ns41 - _w41)} ghost={_g41[:3]} diff={sorted((_ns41 - _w41) ^ _p48_nw41)[:4]}")
    check("EXE(P48): the AI-generation block and the real thumbnail column shipped inside onefile — 4 explicit-click buttons + inputs in served HTML, handlers + honest thumb fallback texts in served JS, stale refusal wording replaced by the P48 归位 note",
          all(_m in _ui35.text for _m in ('id="wr-gen-btn"', 'id="wr-plan-btn"', 'id="wr-chat-btn"',
              'id="wr-img-btn"', 'id="wr-gen-priority"', 'id="wr-chat-stream"', 'id="wr-ai-timeout"',
              "Phase 48 归位"))
          and all(_m in _j39 for _m in ('Post("/generate"', 'Post("/plan"', 'Post("/provider/chat"',
              'Post("/provider/images"', 'data-thumb', 'class="thumb-lg"',
              "取不到（端点非 200，状态如实）", "此处<b>不是</b>原图，界面不假装是"))
          and "素材缩略图二进制流不入 JSON 呈现区" not in _ui35.text
          and "需真实 Provider 后端（属下一能力域）" not in _ui35.text,
          f"js_bytes={len(_j39)} html_bytes={len(_ui35.text)}")
    check("EXE(P42+P45): detail panel shipped — 8 side-effect-free endpoint handlers compiled into onefile; resolve/capability-report now PRESENT as P45 explicit-write handlers (two-phase history, stale 明确不接线 self-report gone), skill fixture + honest 404 ghosts live on frozen exe",
          all(_m in _j39 for _m in ("p42Need", "/eval/reports/", "/eval/compare",
                                    "/skills/", "/validate", "/flags/",
                                    "/typography/fonts/"))
          and "/typography/resolve" in _j39 and "/tools/capability-report" in _j39
          and "明确不接线" not in _ui35.text
          and all(_m in _ui35.text for _m in ('id="dt-report-btn"', 'id="dt-cmp-run2"',
                                              'id="dt-font-btn"'))
          and requests.post(BASE + "/api/v1/skills", json={
              "id": "l4p42sk", "version": "1.0.0", "permissions": ["asset.read"],
              "tools": ["preflight.check"], "risk": "low"}).status_code in (200, 400)
          and requests.get(BASE + "/api/v1/skills/l4p42sk").json().get("id") == "l4p42sk"
          and requests.post(BASE + "/api/v1/skills/l4p42sk/validate").json().get("valid") is True
          and requests.get(BASE + "/api/v1/eval/reports/l4p42ghost").status_code == 404
          and requests.post(BASE + "/api/v1/eval/compare",
                            json={"a": "l4p42g1", "b": "l4p42g2"}).status_code == 404
          and requests.get(BASE + "/api/v1/flags/l4p42ghostkey").status_code == 404,
          "P42 markers + refusal + live skill/ghost on EXE")

    # ===== Phase 51: 跨引擎工具面 / 值血缘 / 事件台账 在冻结 EXE 内部自洽 =====
    # onefile 只随包 .pyc，源码侧的"幽灵 kind 反查"在冻结产物上无从下手，
    # 故这里只钉产物内部的不变式与真实执行结果（覆盖面判据在 L2/L5 的源码侧出具）。
    _p51_wid = "l4p51chain"
    _p51_tj = requests.get(BASE + "/api/v1/workflow/tools", timeout=60).json()
    _p51_tl = _p51_tj.get("tools") or []
    _p51_prm = [_p for _t in _p51_tl for _p in (_t.get("params") or [])]
    _p51_comps = ((requests.get(BASE + "/openapi.json", timeout=60).json()
                   .get("components") or {}).get("schemas") or {})
    _p51_models = [_t.get("request_model") for _t in _p51_tl if _t.get("request_model")]
    _p51_xbad = [_t.get("tool") for _t in _p51_tl if _t.get("request_model")
                 and sorted((_p51_comps.get(_t["request_model"]) or {}).get("properties") or {})
                 != sorted(_p["name"] for _p in (_t.get("params") or []) if _p.get("in") == "body")]
    check("EXE(P51+P53+P56+P57+P58+P65): the tool manifest is derived INSIDE the frozen one-file build rather than shipped as a text table — 72 tools / 23 domains / 154 params / api_route_total 145 counted from the compiled app's own tool_registry with untagged==[] (P53 installer/artifact + installer/preflight 两条新路由入表 ⇒ 141→143；P56 brand/semantic-insight 透传入口 +1 ⇒ 143→144；P57 BarcodeRequest 增 ec_level ⇒ params 152→153；P58 installer/execution 只读对账 GET +1 ⇒ 144→145，工具面 72/域 23 恒等——执行面走 CLI 显式令牌，绝不进工具表；P65 delete_layer 签名新增 confirm: Optional[bool]=None ⇒ 工具面随签名派生 params 153→154，门禁上到路由后工作流节点同样绕不过去。本条对旧归档必红，正是可证伪的兑现，须 P65 重打包后在冻结归档上实量重钉), all 40 request models present in THIS artifact's openapi components with every body-param set equal to its model properties, and the served console carries the tool-surface button/filter/output plus the 实时派生 wording that issues the request",
          _p51_tj.get("total") == 72 and len(_p51_tl) == 72 and _p51_tj.get("domain_count") == 23
          and len(_p51_prm) == 154 and _p51_tj.get("api_route_total") == 145
          and _p51_tj.get("untagged") == [] and _p51_tj.get("fabricated") is False
          and _p51_tj.get("derived_from") == "workflow_engine.tool_registry"
          and sum((_p51_tj.get("tools_per_domain") or {}).values()) == 72
          and len(_p51_models) == 40 and all(_m in _p51_comps for _m in _p51_models)
          and _p51_xbad == []
          and all(_m in _ui35.text for _m in ('id="wf-tools"', 'id="wf-tools-q"', 'id="wf-tools-out"'))
          and all(_m in _j39 for _m in ('$("wf-tools").addEventListener', 'api("/workflow/tools")',
                                        'j.tools_per_domain', 'j.api_route_total')),
          f"total={_p51_tj.get('total')} params={len(_p51_prm)} art={_p51_tj.get('api_route_total')} "
          f"xbad={_p51_xbad[:3]}")

    _p51_die = requests.post(BASE + "/api/v1/packaging/dieline", timeout=60, json={
        "width": 100, "height": 80, "bleed": 3, "safe": 5}).json()
    _p51_graph = {
        "id": _p51_wid, "name": "L4 P51 lineage", "description": "value lineage in onefile",
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
    _p51_exe = requests.post(BASE + f"/api/v1/workflow/{_p51_wid}/execute",
                             json={"input_data": {}}, timeout=120).json()
    _p51_res = _p51_exe.get("result") or {}
    _p51_rd, _p51_rt = _p51_res.get("die") or {}, _p51_res.get("topo") or {}
    check("EXE(P51) live value lineage: the manifest's tools really execute inside the frozen artifact — the 4-node input->packaging.dieline->packaging.panel_topology->output graph defines clean, reaches success on every node, and the topology hop consumed the dieline hop's OWN panels ($die.panels): panels identical to the direct /packaging/dieline payload, panel_count propagating 1->1, cut_boundary_length equal to the closed form 2*(100+80)=360 for a single 100x80 blank and to the extent of the finished box",
          _p51_def.get("nodes") == 4 and _p51_def.get("validation_errors") == []
          and _p51_exe.get("status") == "success"
          and all(str(v) == "success" for v in (_p51_exe.get("node_status") or {}).values())
          and _p51_rd.get("panels") == _p51_die.get("panels")
          and _p51_rt.get("panel_count") == _p51_rd.get("panel_count") == 1
          and _p51_rt.get("status") == "ok" and _p51_rt.get("fabricated") is False
          and abs(_p51_rt.get("cut_boundary_length", -1.0) - 360.0) < 1e-6
          and _p51_rt.get("extent", {}).get("width") == _p51_rd.get("finished", {}).get("width"),
          f"status={_p51_exe.get('status')} cut={_p51_rt.get('cut_boundary_length')} "
          f"panels={len(_p51_rd.get('panels') or [])}")

    _p51_ev = requests.get(BASE + "/api/v1/events/recent", params={"limit": 2000}, timeout=60).json()
    _p51_kc = _p51_ev.get("kind_counts") or {}
    _p51_win = _p51_ev.get("events") or []
    _p51_wc = {}
    for _e51 in _p51_win:
        _p51_wc[_e51.get("event")] = _p51_wc.get(_e51.get("event"), 0) + 1
    _p51_et = next((_v51 for _v51 in reversed(_p51_win)
                    if _v51.get("event") == "panel_topology.analyzed"), {}).get("data") or {}
    _p51_ew = next((_v51 for _v51 in reversed(_p51_win)
                    if _v51.get("event") == "workflow.executed"), {}).get("data") or {}
    check("EXE(P51) ledger invariants hold on the compiled artifact: published_total equals sum(kind_counts) under either truncation state, the window never exceeds the 200-slot capacity and its per-kind counts stay under the cumulative ones, and the lineage left its mark — the last topology event carries input_panel_count (a field present ONLY on the event, never on the HTTP response) with the same 360 boundary, while the workflow event names both manifest tools it executed",
          _p51_ev.get("published_total") == sum(_p51_kc.values())
          and _p51_ev.get("history_capacity") == 200
          and _p51_ev.get("total") == len(_p51_win) <= 200
          and _p51_ev.get("truncated") is (_p51_ev.get("published_total") > _p51_ev.get("total"))
          and all(_p51_wc[_k51] <= _p51_kc.get(_k51, 0) for _k51 in _p51_wc)
          and {"dieline.computed", "panel_topology.analyzed", "workflow.executed"} <= set(_p51_kc)
          and _p51_et.get("input_panel_count") == 1
          and abs(_p51_et.get("cut_boundary_length", -1.0) - 360.0) < 1e-6
          and _p51_ew.get("workflow_id") == _p51_wid and _p51_ew.get("status") == "success"
          and _p51_ew.get("tools_used") == ["packaging.dieline", "packaging.panel_topology"],
          f"published={_p51_ev.get('published_total')} kinds={_p51_ev.get('kind_count')} "
          f"win={_p51_ev.get('total')} trunc={_p51_ev.get('truncated')} "
          f"used={_p51_ew.get('tools_used')}")
    # 幽灵串分段拼接（P44 惯例）：整体字面串会被 L2 的 P43 声称收集器误判为"调用了不存在的端点"
    check("EXE(P51) tool-surface route is real while a one-character-off ghost stays an honest 404 on the frozen artifact",
          "/api/v1/workflow/tools" in _ns41
          and requests.get(BASE + "/api/v1/" + "workflow/toolz", timeout=30).status_code == 404
          and requests.get(BASE + "/api/v1/workflow/tools", timeout=60).status_code == 200,
          "ghost=%d" % requests.get(BASE + "/api/v1/" + "workflow/toolz", timeout=30).status_code)

    lic = requests.get(BASE + "/api/v1/license").json()
    check("EXE: license machine_id real", len(str(lic.get("machine_id", ""))) >= 16, lic.get("machine_id", "")[:24])

    tmp = Path(tempfile.mkdtemp())
    img = tmp / "packaged_probe.png"
    im = Image.new("RGB", (64, 64), (255, 255, 255))
    for x in range(24, 40):
        for y in range(24, 40):
            im.putpixel((x, y), (10, 90, 200))
    im.save(img)

    a = requests.post(BASE + "/api/v1/assets", json={"file_path": str(img)})
    check("EXE: asset import works", a.status_code == 200, a.text[:100] if a.status_code != 200 else a.json()["id"][:8])
    if a.status_code == 200:
        aid = a.json()["id"]
        s = requests.post(BASE + "/api/v1/assets/search", json={"query": "packaged", "top_k": 3}).json()
        check("EXE: vector search works", s.get("total", 0) >= 1, f"t={s.get('search_time')}s")
        # Phase 48：控制台素材列的 <img src="${API}/assets/{id}/thumbnail"> 是真 HTTP 请求，
        # 已在 wired 判据内（口径第四式）。故在冻结 EXE 上钉到字节层：PNG 魔数 + 幽灵 id 诚实 404
        # （前端 onerror 分支据此显示"取不到"，绝不画假图）。
        _th48 = requests.get(BASE + f"/api/v1/assets/{aid}/thumbnail", timeout=60)
        _th48g = requests.get(BASE + "/api/v1/assets/p48_no_such_asset_9f3a/thumbnail", timeout=60)
        check("EXE(P48): wired thumbnail endpoint returns a real PNG (magic bytes, non-trivial length) from the frozen exe and an unknown id an honest 404 — the console's <img onerror> rung",
              _th48.status_code == 200 and _th48.content[:8] == b"\x89PNG\r\n\x1a\n" and len(_th48.content) > 100
              and _th48g.status_code == 404,
              f"HTTP {_th48.status_code} magic={_th48.content[:8]!r} bytes={len(_th48.content)} ghost={_th48g.status_code}")
        # Phase 48：生成类按钮接线后的诚实失败面（在配置任何 Provider 之前发起，故应为真 502/503）。
        _ge48 = requests.post(BASE + "/api/v1/generate", json={"prompt": "minimal crane logo"}, timeout=180)
        _gd48 = (_ge48.json() or {}).get("detail") if _ge48.headers.get("content-type", "").startswith("application/json") else None
        check("EXE(P48): /api/v1/generate on the frozen exe is honest — 502/503 with a STRING detail when no provider is reachable, or a true 200 with integer images_generated; never a fabricated success",
              (_ge48.status_code in (502, 503) and isinstance(_gd48, str))
              or (_ge48.status_code == 200 and _ge48.json().get("status") == "success"
                  and isinstance(_ge48.json().get("images_generated"), int)),
              f"HTTP {_ge48.status_code} detail_type={type(_gd48).__name__}")
        _ge48np = requests.post(BASE + "/api/v1/generate", json={"steps": 10}, timeout=60)
        check("EXE(P48): /api/v1/generate without the required prompt answers 422 (client error never dressed as 5xx)",
              _ge48np.status_code == 422, f"HTTP {_ge48np.status_code}")
        requests.delete(BASE + f"/api/v1/assets/{aid}")  # cleanup index

    pf = requests.post(BASE + "/api/v1/preflight", json={"document_path": str(img)}).json()
    check("EXE: preflight real parse", pf.get("document_info", {}).get("resolution") in (72, None) and "findings" in pf,
          f"dims={pf.get('document_info', {}).get('dimensions')} status={pf.get('summary', {}).get('overall_status')}")

    rb = requests.post(BASE + "/api/v1/generate/remove-background", json={"image_path": str(img), "threshold": 40}).json()
    check("EXE: remove-bg works", 0.7 < rb.get("background_cleared_ratio", 0) < 1.0, f"ratio={rb.get('background_cleared_ratio')}")

    wf = requests.post(BASE + "/api/v1/workflow/define", json={
        "name": "smoke", "nodes": [{"id": "a", "type": "input", "name": "in"}, {"id": "b", "type": "output", "name": "out"}],
        "edges": [{"source_node_id": "a", "target_node_id": "b"}]}).json()
    ex = requests.post(BASE + f"/api/v1/workflow/{wf['workflow_id']}/execute", json={"input_data": {}}).json()
    check("EXE: workflow define+execute", ex.get("status") in ("success", "completed", "running"), f"wf={wf['workflow_id']} st={ex.get('status')}")

    # ===== Phase 54（冻结件这一路）：引用解析收口的三处，必须在 EXE 里而不是只在源码里 =====
    # 同一判据族：L2 打源码活服务、L5 打跨引擎血缘、本段打随包冻结的 workflow_engine。
    import re as _re54x, hashlib as _hl54x
    _p54_rs = ((requests.get(BASE + "/api/v1/workflow/tools", timeout=60).json()
                or {}).get("reference_syntax")) or {}
    _p54_gram = str(_p54_rs.get("grammar") or "")
    check("EXE(P54): the frozen artifact ships the reference grammar the resolver actually compiled — '$bc.output_path'/'$fl.fonts.0.family' match, '$1,200'/'$ a'/'$-x'/'$9'/'$' do not, and the semantics line is the docstring's own 语义： line (implemented_by still names workflow_engine.WorkflowEngine._resolve_value)",
          bool(_p54_gram) and _p54_rs.get("supports_path") is True
          and _p54_rs.get("implemented_by") == "workflow_engine.WorkflowEngine._resolve_value"
          and _re54x.match(_p54_gram, "$bc.output_path") is not None
          and _re54x.match(_p54_gram, "$fl.fonts.0.family") is not None
          and all(_re54x.match(_p54_gram, _s) is None
                  for _s in ("$1,200", "$ a", "$-x", "$9", "$")),
          f"grammar={_p54_gram[:52]} sem={str(_p54_rs.get('semantics') or '')[:16]!r}")

    _p54_bc = requests.post(BASE + "/api/v1/packaging/barcode", timeout=60,
                            json={"code": "4006381333931"}).json()
    _p54_op = str(_p54_bc.get("output_path") or "")
    _p54_gid = "p54exe_%d" % os.getpid()
    _p54_def = requests.post(BASE + "/api/v1/workflow/define", timeout=60, json={
        "id": _p54_gid, "name": _p54_gid, "description": "P54 container ref on frozen exe",
        "variables": {},
        "nodes": [
            {"id": "in", "type": "input", "name": "in", "config": {}},
            {"id": "bc", "type": "tool", "name": "barcode",
             "config": {"tool_name": "packaging.barcode",
                        "parameters": {"code": "4006381333931"}, "output_variable": "bc"}},
            {"id": "dl", "type": "tool", "name": "delivery",
             "config": {"tool_name": "delivery.package",
                        "parameters": {"project": "p54exe",
                                       "files": {"Print": ["$bc.output_path"]}},
                        "output_variable": "dl"}},
            {"id": "out", "type": "output", "name": "out", "config": {"select": ["bc", "dl"]}}],
        "edges": [{"source_node_id": "in", "target_node_id": "bc"},
                  {"source_node_id": "bc", "target_node_id": "dl"},
                  {"source_node_id": "dl", "target_node_id": "out"}]}).json()
    _p54_ex = requests.post(BASE + "/api/v1/workflow/%s/execute" % _p54_gid, timeout=180,
                            json={"input_data": {}}).json()
    _p54_inst = requests.get(BASE + "/api/v1/workflow/instance/%s" % _p54_ex.get("instance_id"),
                             timeout=60).json()
    _p54_dl = ((_p54_inst.get("node_outputs") or {}).get("dl") or {}).get("result") or {}
    _p54_zp = Path(str(_p54_dl.get("archive") or ""))
    _p54_names, _p54_shas, _p54_zbytes = [], {}, 0
    if _p54_zp.is_file():
        _p54_zbytes = _p54_zp.stat().st_size
        with zipfile.ZipFile(str(_p54_zp)) as _zf54:
            _p54_names = sorted(_zf54.namelist())
            _p54_shas = {_n: _hl54x.sha256(_zf54.read(_n)).hexdigest() for _n in _zf54.namelist()}
    _p54_disk = (_hl54x.sha256(Path(_p54_op).read_bytes()).hexdigest()
                 if _p54_op and Path(_p54_op).is_file() else "")
    _p54_want = "Print/" + Path(_p54_op).name if _p54_op else ""
    check("EXE(P54): the container-position fix is inside the frozen exe, proven by measured bytes — a reference in a LIST ELEMENT of delivery.files resolves through the bundled engine to the barcode png the same process just rendered, and the ZIP's own namelist (not the self-reported 'included') contains Print/<that png> with sha256 equal to this file's independent hash of the on-disk png",
          _p54_def.get("nodes") == 4 and _p54_inst.get("status") == "success"
          and all(str(_v) == "success" for _v in (_p54_inst.get("node_status") or {}).values())
          and bool(_p54_want) and _p54_want in _p54_names
          and bool(_p54_disk) and _p54_shas.get(_p54_want) == _p54_disk
          and _p54_dl.get("included") == sum(1 for _n in _p54_names if _n.startswith("Print/"))
          and _p54_dl.get("archive_size_bytes") == _p54_zbytes
          and not any("$" in _n for _n in _p54_names),
          f"st={_p54_inst.get('status')} names={_p54_names} sha_eq={_p54_shas.get(_p54_want) == _p54_disk}")

    _p54_ggid = "p54exe_ghost_%d" % os.getpid()
    requests.post(BASE + "/api/v1/workflow/define", timeout=60, json={
        "id": _p54_ggid, "name": _p54_ggid, "variables": {},
        "nodes": [
            {"id": "in", "type": "input", "name": "in", "config": {}},
            {"id": "dl", "type": "tool", "name": "delivery",
             "config": {"tool_name": "delivery.package",
                        "parameters": {"project": "p54exe_ghost",
                                       "files": {"Print": ["$ghostly.output_path"]}},
                        "output_variable": "dl"}},
            {"id": "out", "type": "output", "name": "out", "config": {"select": ["dl"]}}],
        "edges": [{"source_node_id": "in", "target_node_id": "dl"},
                  {"source_node_id": "dl", "target_node_id": "out"}]})
    _p54_gex = requests.post(BASE + "/api/v1/workflow/%s/execute" % _p54_ggid, timeout=180,
                             json={"input_data": {}}).json()
    _p54_ginst = requests.get(BASE + "/api/v1/workflow/instance/%s" % _p54_gex.get("instance_id"),
                              timeout=60).json()
    _p54_gerr = str(_p54_ginst.get("error_message") or "")
    check("EXE(P54): on the frozen exe an unresolvable reference in a parameter container fails the node loudly (root 'ghostly' named, available variables listed) instead of silently becoming None and producing a self-reported successful empty delivery",
          _p54_ginst.get("status") == "failed"
          and (_p54_ginst.get("node_status") or {}).get("dl") == "failed"
          and "ghostly" in _p54_gerr and "$ghostly.output_path" in _p54_gerr,
          f"st={_p54_ginst.get('status')} ns={_p54_ginst.get('node_status')} err={_p54_gerr[:70]}")

    # EXE 级：真实快照 + 完整性恢复
    rdoc = tmp / "probe.psd"
    rbytes = b"PACKAGED-SNAPSHOT-PROBE-0123456789"
    rdoc.write_bytes(rbytes)
    rsnap = requests.post(BASE + "/api/v1/recovery/snapshot",
                          json={"document_path": str(rdoc), "snapshot_type": "full"}).json()
    rdoc.write_bytes(b"ZZZZ-CORRUPTED-ZZZZ-CORRUPTED-ZZZZ")
    rr = requests.post(BASE + f"/api/v1/recovery/snapshot/{rsnap.get('id')}/restore").json()
    check("EXE: recovery snapshot+real restore", rsnap.get("recoverable") is True and rr.get("restored") is True and rdoc.read_bytes() == rbytes,
          f"recoverable={rsnap.get('recoverable')} restored={rr.get('restored')}")

    # EXE 级：知识库 RAG 真实抽取+检索
    kdoc = tmp / "spec_matcha.md"
    kdoc.write_text("抹茶 matcha 包装规范：主色抹茶绿 matcha green，禁用荧光色。", encoding="utf-8")
    ki = requests.post(BASE + "/api/v1/knowledge/ingest",
                       json={"file_path": str(kdoc), "scope": "project", "project_id": "px"}).json()
    kr = requests.post(BASE + "/api/v1/knowledge/retrieve", json={"query": "抹茶 matcha 主色", "top_k": 3}).json()
    check("EXE: knowledge RAG ingest+retrieve", ki.get("status") == "indexed" and kr.get("total", 0) >= 1,
          f"chunks={ki.get('chunk_count')} hits={kr.get('total')}")

    # EXE 级：工具注册表 + 能力协商
    tl = requests.get(BASE + "/api/v1/tools").json()
    tnames = {t["name"]: t for t in tl.get("tools", [])}
    check("EXE: tool registry seeded + adobe gated",
          "ps.open_document" in tnames and tnames["ps.open_document"]["runnable_now"] is False,
          f"total={tl.get('total')}")

    # EXE 级：技能校验（越权拒绝）
    sk_bad = requests.post(BASE + "/api/v1/skills", json={"id": "evil", "permissions": ["system.wipe"], "risk": "high"})
    check("EXE: skill over-privilege rejected", sk_bad.status_code == 400, f"HTTP {sk_bad.status_code}")

    # EXE 级：功能开关持久化
    requests.put(BASE + "/api/v1/flags/telemetry_enabled", json={"value": True})
    flg = requests.get(BASE + "/api/v1/flags/telemetry_enabled").json()
    check("EXE: feature flag persists", flg.get("value") is True, json.dumps(flg))

    # EXE 级：设计对比真实 diff（含 §50 六类结构化归类）
    dc = requests.post(BASE + "/api/v1/design/compare", json={
        "before": [{"id": "a", "x": 0, "color": "#111111", "font_size": 20, "opacity": 100}],
        "after": [{"id": "a", "x": 9, "color": "#222222", "font_size": 28, "opacity": 50}, {"id": "b", "x": 1}]}).json()
    dbc = dc.get("summary", {}).get("by_category", {})
    check("EXE: design compare real diff + §50 categories",
          dc.get("summary", {}).get("added") == 1 and dbc.get("geometry", 0) >= 1
          and dbc.get("color", 0) >= 1 and dbc.get("text", 0) >= 1 and dbc.get("visual", 0) >= 1,
          json.dumps(dc.get("summary")))

    # EXE 级：字体排版引擎（fontTools 在冻结 EXE 内真实解析）
    tscan = requests.post(BASE + "/api/v1/typography/scan", params={"limit": 50}).json()
    check("EXE: typography scan works (fontTools bundled)",
          tscan.get("scanned", 0) >= 1, json.dumps({k: tscan.get(k) for k in ("scanned", "errors", "total_indexed")}))
    tfonts = requests.get(BASE + "/api/v1/typography/fonts").json()
    tfid = (tfonts.get("fonts") or [{}])[0].get("id")
    tmet = requests.post(BASE + "/api/v1/typography/metrics", json={"font": tfid, "text": "Abc", "size_pt": 20}).json()
    check("EXE: typography real metrics in frozen exe",
          tmet.get("width_pt", 0) > 0 and tmet.get("glyph_count", 0) >= 1,
          f"w={tmet.get('width_pt')} glyphs={tmet.get('glyph_count')}")
    tmt = requests.post(BASE + "/api/v1/typography/match", json={"missing": "Helvetica Neue", "top_k": 3}).json()
    check("EXE: typography match returns candidates", tmt.get("total", 0) >= 1, f"total={tmt.get('total')}")

    # EXE 级：多角色设计评审引擎
    rv = requests.post(BASE + "/api/v1/review/run", json={
        "objects": [{"id": "bg", "type": "shape", "x": 0, "y": 0, "width": 800, "height": 600, "color": "#FFFFFF"},
                    {"id": "t", "type": "text", "x": 40, "y": 40, "width": 300, "height": 40, "color": "#F0F0F0", "font_size": 30}],
        "canvas": {"width": 800, "height": 600, "dpi": 300, "background": "#FFFFFF"}}).json()
    check("EXE: design review detects WCAG contrast",
          rv.get("summary", {}).get("total_issues", 0) >= 1 and any("contrast" in i["id"] for i in rv.get("issues", [])),
          f"issues={rv.get('summary',{}).get('total_issues')} score={rv.get('score')}")

    # EXE 级：客户改稿引擎
    _logo_objs = [{"id": "logo", "name": "logo", "type": "image", "width": 100, "height": 50}]
    rpo = requests.post(BASE + "/api/v1/revision/parse", json={"text": "logo 放大一点", "objects": _logo_objs}).json()
    proposed = [c for c in rpo.get("changes", []) if c.get("status") == "proposed"]
    rpa = requests.post(BASE + "/api/v1/revision/apply", json={
        "objects": _logo_objs, "changes": rpo.get("changes", []),
        "only_ids": [c["id"] for c in proposed]}).json()
    logo_w = next((o.get("width") for o in rpa.get("objects", []) if o.get("_id") == "logo"), 0)
    check("EXE: revision parse+apply really resizes",
          len(proposed) >= 1 and abs(logo_w - 120.0) < 1e-6, f"proposed={len(proposed)} logo_w={logo_w}")

    # EXE 级：色彩引擎（冻结 EXE 内真实计算 + Pillow 取色 + Pantone 诚实拒绝）
    c_pal = requests.post(BASE + "/api/v1/color/palette", json={"path": str(img), "k": 4}).json()
    _cp = c_pal.get("palette", [])
    _ctop = _cp[0].get("rgb", [0, 0, 0]) if _cp else [0, 0, 0]
    _blue_hit = any(len(e.get("rgb", [])) == 3 and all(abs(e["rgb"][i] - [10, 90, 200][i]) <= 8 for i in range(3)) for e in _cp)
    check("EXE: color palette real (Pillow in frozen exe) — 白底主导且中心蓝(10,90,200)自真像素还原",
          c_pal.get("distinct_colors", 0) >= 2 and _cp and _cp[0].get("pixels", 0) > 0
          and all(abs(_ctop[i] - [255, 255, 255][i]) <= 8 for i in range(3)) and _cp[0].get("percent", 0) >= 80.0
          and _blue_hit,
          json.dumps(_cp[:2], ensure_ascii=False))
    c_con = requests.post(BASE + "/api/v1/color/contrast", json={"fg": "#000000", "bg": "#FFFFFF"}).json()
    check("EXE: color WCAG contrast 21:1", c_con.get("ratio") == 21.0, f"ratio={c_con.get('ratio')}")
    c_de = requests.post(BASE + "/api/v1/color/delta-e", json={"a": "#FF0000", "b": "#0000FF"}).json()
    check("EXE: color CIEDE2000 deltaE real", c_de.get("delta_e", 0) > 50, f"deltaE={c_de.get('delta_e')}")
    c_pan = requests.post(BASE + "/api/v1/color/pantone", json={"hex": "#1F4E79"}).json()
    check("EXE: color Pantone NOT fabricated (red-line)",
          c_pan.get("status") == "needs_licensed_pantone_data" and c_pan.get("fabricated") is False, json.dumps(c_pan))

    # EXE 级：布局排版引擎（真实几何）
    _l_objs = [{"id": "a", "x": 0, "y": 0, "width": 100, "height": 100},
               {"id": "b", "x": 50, "y": 50, "width": 100, "height": 100}]
    l_col = requests.post(BASE + "/api/v1/layout/collide", json={"objects": _l_objs}).json()
    check("EXE: layout collision real geometry",
          l_col.get("collision_count") == 1 and abs(l_col["collisions"][0]["overlap_area"] - 2500) < 1, json.dumps(l_col.get("collisions")))
    l_al = requests.post(BASE + "/api/v1/layout/align", json={"objects": [
        {"id": "p", "x": 10, "y": 0, "width": 20, "height": 10}, {"id": "q", "x": 0, "y": 5, "width": 20, "height": 10}], "mode": "left"}).json()
    check("EXE: layout left-align", all(o["to"]["x"] == 0 for o in l_al.get("objects", [])), f"aligned={l_al.get('aligned')}")

    # EXE 级：敌意输入鲁棒性（Phase 22：证明有限数值/除零画布/结构非法载荷的诚实降级守卫确已编入冻结 onefile 归档——
    #   曾在源码级泄漏裸 500 的畸形输入，在发布二进制里必须诚实返回 400，绝不再是任何 5xx）
    _hostile = [
        ("collide 非数值坐标", "/api/v1/layout/collide",
         {"objects": [{"id": "a", "x": "NaN?", "y": "z", "width": "w", "height": "h"}]}),
        ("grid-snap 空画布(旧除零)", "/api/v1/layout/grid-snap",
         {"objects": _l_objs, "canvas": {}, "cols": 4, "rows": 4}),
        ("distribute 非数值尺寸", "/api/v1/layout/distribute",
         {"objects": [{"x": 0, "y": 0, "width": "a", "height": "b"},
                      {"x": 5, "y": 0, "width": 10, "height": 10},
                      {"x": 20, "y": 0, "width": 10, "height": 10}], "axis": "x"}),
        ("license 结构非法密钥", "/api/v1/license/activate", {"license_key": "ZAOJING.###.###"}),
    ]
    for _lbl, _path, _payload in _hostile:
        _r = requests.post(BASE + _path, json=_payload)
        check(f"EXE hostile [{_lbl}] -> 400 (no 5xx in shipped binary)",
              _r.status_code == 400 and _r.status_code < 500, f"HTTP {_r.status_code}")

    # EXE 级：包装/变量数据引擎（真实几何+文件IO + 面板/条码诚实未实现）
    p_dl = requests.post(BASE + "/api/v1/packaging/dieline", json={"width": 100, "height": 80, "bleed": 3, "safe": 5}).json()
    check("EXE: packaging dieline geometry", p_dl.get("bleed_box") == [-3.0, -3.0, 103.0, 83.0], json.dumps(p_dl.get("bleed_box")))
    p_imp = requests.post(BASE + "/api/v1/packaging/imposition", json={"die_width": 50, "die_height": 50, "count": 100, "sheet_width": 500, "sheet_height": 500}).json()
    check("EXE: packaging imposition n-up", p_imp.get("n_up") == 100 and p_imp.get("sheets_needed") == 1, f"n_up={p_imp.get('n_up')}")
    _vdp = tmp / "vdp.csv"
    _vdp.write_text("name,sku\nCola,A001\nSprite,A002\n", encoding="utf-8")
    p_vm = requests.post(BASE + "/api/v1/packaging/vdp/merge", json={"source": str(_vdp), "field_map": {"product": "name"}}).json()
    check("EXE: packaging VDP real CSV merge", p_vm.get("total_rows") == 2 and p_vm["records"][0]["variant"]["product"] == "Cola", f"rows={p_vm.get('total_rows')}")
    _p47b = tmp / "l4_box3_dieline.png"
    _i47b = Image.new("RGB", (200, 120), (255, 255, 255))
    _p47px = _i47b.load()
    for _x47 in range(10, 190):
        for _y47 in (10, 109):
            _p47px[_x47, _y47] = (0, 0, 0)
    for _y47 in range(10, 110):
        for _x47 in (10, 189):
            _p47px[_x47, _y47] = (0, 0, 0)
    for _y47 in range(11, 109):
        for _x47 in (69, 130):
            _p47px[_x47, _y47] = (0, 0, 0)
    _i47b.save(_p47b)
    _EP47 = BASE + "/api/v1/packaging/panel-recognition"
    p_pr_resp = requests.post(_EP47, params={"image_path": str(_p47b)})
    p_pr = p_pr_resp.json()
    check("EXE: packaging panel recognition REAL in frozen exe (25th engine) - 200x120 three-panel dieline inverts to areas [5880,5684,5684]/Otsu thr 0/ink 752/line 744, 2 solid-cut edges cov 1.0, deg [2,1,1], outline 940=2*(294+176), union 17248, fabricated False",
          p_pr_resp.status_code == 200 and p_pr.get("status") == "recognized"
          and p_pr.get("implemented") is True and p_pr.get("fabricated") is False
          and p_pr.get("panel_count") == 3
          and sorted(pp["area_px"] for pp in p_pr["panels"]) == [5684, 5684, 5880]
          and p_pr["binarize"] == {"method": "otsu", "threshold": 0, "ink_px": 752, "line_px": 744,
                                   "other_ink_px": 8, "barrier_px": 748, "blocked_px": 752}
          and p_pr["lines"]["cut"]["segments"] == 6 and p_pr["lines"]["fold"]["segments"] == 0
          and [(a["separating_line"], a["separating_coverage"], a["line_gap_px"])
               for a in p_pr["topology"]["adjacencies"]] == [("cut", 1.0, 1), ("cut", 1.0, 1)]
          and p_pr["topology"]["adjacency_count"] == 2
          and p_pr["topology"]["degree_sequence"] == [2, 1, 1]
          and p_pr["topology"]["is_single_connected_net"] is True
          and [p_pr["outline"][k] for k in ("perimeter_px", "union_area_px", "row_runs", "col_runs")]
              == [940, 17248, 294, 176]
          and p_pr["background"] == {"component_count": 1, "largest_area_px": 6000}
          and len(p_pr["caveats"]) == 5,
          json.dumps({"status": p_pr.get("status"), "engine": p_pr.get("engine"),
                      "areas": [pp["area_px"] for pp in p_pr.get("panels", [])]}, ensure_ascii=False))
    (tmp / "p47_dieline_spec.txt").write_text("not a bitmap at all", encoding="utf-8")
    _n47 = (("missing-param", requests.post(_EP47), 400),
            ("ghost-path", requests.post(_EP47, params={"image_path": str(tmp / "nope_p47.png")}), 404),
            ("non-bitmap", requests.post(_EP47, params={"image_path": str(tmp / "p47_dieline_spec.txt")}), 400),
            ("blocked-system", requests.post(_EP47, params={"image_path": "C:/Windows/System32/config/SAM"}), 403))
    check("EXE: panel recognition honest 4xx ladder inside frozen exe - missing 400 / ghost 404 / non-bitmap 400 / blocked 403, zero 5xx leak",
          all(_r.status_code == _w for _l, _r, _w in _n47)
          and all(_r.status_code < 500 for _l, _r, _w in _n47)
          and "缺少 image_path" in _n47[0][1].json()["detail"]
          and "文件不存在" in _n47[1][1].json()["detail"]
          and "无法作为位图图像解码" in _n47[2][1].json()["detail"]
          and "路径被安全策略阻止" in _n47[3][1].json()["detail"],
          json.dumps([(_l, _r.status_code) for _l, _r, _w in _n47], ensure_ascii=False))
    p_bc = requests.post(BASE + "/api/v1/packaging/barcode",
                         json={"code": "590123412345", "symbology": "EAN13", "output_path": str(tmp / "exe_bc.png")}).json()
    check("EXE: real EAN-13 render in frozen exe (Pillow + 95 modules + PNG)",
          p_bc.get("implemented") is True and p_bc.get("code") == "5901234123457"
          and p_bc.get("check_digit") == "7" and p_bc.get("module_count") == 95
          and os.path.isfile(p_bc.get("output_path", "")) and p_bc.get("bytes", 0) > 0,
          f"code={p_bc.get('code')} modules={p_bc.get('module_count')} bytes={p_bc.get('bytes')}")
    # ==== Phase 57：冻结 EXE 内的 QR（ISO/IEC 18004）+ 第三方 OpenCV 逐字读回 oracle ====
    # 与 L2 同款判据，但**一切都走 HTTP**：触发长度表由本 EXE 自己申报，不由源码引擎派生，
    # 这样"QR 在冻结归档里就是这一版实现"才是被测对象本身（测链接而非测源码）。
    # 尺子缺失即大声判红，绝不静默跳过。合格线同 L2：默认栅格逐字读回 ∨
    # （该行尺子自家产物同样读不回 且 几何扫描内逐字读回），且产品失败行集合 ⊆ 尺子自败行集合。
    _cv2x = None
    _npx = None
    try:
        import cv2 as _cv2x
        import numpy as _npx
        _HAS_CVX = True
        _CVX_ERR = ""
    except Exception as _ex57:
        _HAS_CVX = False
        _CVX_ERR = repr(_ex57)
    _detx = _cv2x.QRCodeDetector() if _HAS_CVX else None
    _CVXL = {"L": getattr(_cv2x, "QRCodeEncoder_CORRECT_LEVEL_L", 0),
             "M": getattr(_cv2x, "QRCodeEncoder_CORRECT_LEVEL_M", 1),
             "Q": getattr(_cv2x, "QRCodeEncoder_CORRECT_LEVEL_Q", 2),
             "H": getattr(_cv2x, "QRCodeEncoder_CORRECT_LEVEL_H", 3)} if _HAS_CVX else {}

    def _post_bc(payload, **kw):
        body = {"code": payload, "symbology": kw.pop("symbology", "QR"),
                "output_path": str(tmp / ("qrX_%s_%s_%d.png" % (
                    kw.get("ec_level", "M"), kw.get("scale", 8), len(payload))))}
        body.update(kw)
        return requests.post(BASE + "/api/v1/packaging/barcode", json=body, timeout=60)

    def _decx(img):
        try:
            return _detx.detectAndDecode(img)[0]
        except Exception:
            return ""

    def _rasterx(mat, sc, qz):
        side = mat.shape[0]
        canvas = _npx.ones((side + 2 * qz, side + 2 * qz), dtype=_npx.uint8) * 255
        canvas[qz:qz + side, qz:qz + side][mat > 0] = 0
        return _cv2x.resize(canvas, None, fx=sc, fy=sc, interpolation=_cv2x.INTER_NEAREST)

    def _ownx(payload, ver, level, side):
        prm = _cv2x.QRCodeEncoder_Params()
        prm.version, prm.correction_level, prm.mode = ver, _CVXL[level], _cv2x.QRCodeEncoder_MODE_BYTE
        raw = _cv2x.QRCodeEncoder.create(prm).encode(payload)
        s0 = (raw.shape[0] - side) // 2
        return (raw[s0:s0 + side, s0:s0 + side] < 128).astype(_npx.uint8)

    _vx = {}

    def _ver_ofx(level, n):
        key = (level, n)
        if key not in _vx:
            _r = _post_bc("z" + "A" * (n - 1), ec_level=level, scale=1)
            _vx[key] = _r.json().get("version", 999) if _r.status_code == 200 else 999
        return _vx[key]

    rowsX, _trigX_bad = [], []
    for _lv in ("L", "M", "Q", "H"):
        for _vv in range(1, 11):
            _lo, _hi = 1, 280
            while _lo < _hi:
                _mid = (_lo + _hi) // 2
                if _ver_ofx(_lv, _mid) >= _vv:
                    _hi = _mid
                else:
                    _lo = _mid + 1
            if _ver_ofx(_lv, _lo) != _vv:
                _trigX_bad.append("(%s,v%d) 触发落在 v%s" % (_lv, _vv, _ver_ofx(_lv, _lo)))
                continue
            if _vv > 1 and _ver_ofx(_lv, _lo - 1) != _vv - 1:
                _trigX_bad.append("(%s,v%d) 触发非最小（上一长度 v%s）"
                                  % (_lv, _vv, _ver_ofx(_lv, _lo - 1)))
            rowsX.append((_lv, _vv, "z" + "A" * (_lo - 1)))
    _extraX = [("M", "123456789012"), ("M", "HELLO WORLD-123 ABC"), ("M", "造境AI設計智能体")]
    _qX = [(_lv, _vv, _pl, _post_bc(_pl, ec_level=_lv, scale=8).json())
           for (_lv, _vv, _pl) in rowsX]
    _qX += [("M", 0, _pl, _post_bc(_pl, ec_level="M", scale=8).json()) for (_lv, _pl) in _extraX]

    helloX = _post_bc("hello", scale=2).json()
    _mX = helloX.get("matrix") or []
    _sdX = helloX.get("side", 0)
    check("EXE(P57): QR really rendered inside the frozen exe (ISO 18004 structural fields self-consistent, real PNG on disk, fabricated=False)",
          helloX.get("implemented") is True and helloX.get("status") == "ok"
          and helloX.get("fabricated") is False and helloX.get("symbology") == "QR"
          and helloX.get("mode") == "Byte" and helloX.get("version") == 1
          and _sdX == 21 + 4 * (helloX.get("version", 0) - 1)
          and helloX.get("module_count") == _sdX * _sdX
          and len(_mX) == _sdX and all(len(r) == _sdX for r in _mX)
          and len(helloX.get("bit_pattern", "")) == _sdX * _sdX
          and len(helloX.get("format_bits", "")) == 15
          and helloX.get("total_codewords") == helloX.get("data_codewords") + helloX.get("ec_codewords")
          and os.path.isfile(helloX.get("output_path", ""))
          and os.path.getsize(helloX.get("output_path", "")) == helloX.get("bytes") > 0
          and helloX.get("quiet_zone_modules") == 4
          and helloX.get("width_px") == (_sdX + 2 * 4) * 2,
          json.dumps({k: helloX.get(k) for k in ("version", "side", "mode", "total_codewords",
                                                  "bytes", "quiet_zone_modules", "width_px")}))

    _echoX = ["%s/v%s->%s/v%s" % (_lv, _vv, _rr.get("ec_level"), _rr.get("version"))
              for (_lv, _vv, _pl, _rr) in _qX if _vv and _rr.get("ec_level") != _lv]
    _lvlX = {}
    for (_lv, _vv, _pl, _rr) in _qX:
        if _vv:
            _lvlX.setdefault(_pl, {})[_lv] = _rr.get("version")
    _lmX = sum(1 for d in _lvlX.values() if len(set(d.values())) > 1)
    q2X = _post_bc("hello", quiet=2, scale=8).json()
    eanX = requests.post(BASE + "/api/v1/packaging/barcode", timeout=60, json={
        "code": "590123412345", "symbology": "EAN13",
        "output_path": str(tmp / "qrX_ean.png")}).json()
    check("EXE(P57): QR ec_level + quiet honored end-to-end by the frozen exe (no 1D quiet=11 leak; declared==actual; EAN-13 default quiet unchanged)",
          not _echoX and _lmX >= 3 and q2X.get("quiet_zone_modules") == 2
          and q2X.get("width_px") == (q2X.get("side", 0) + 4) * 8
          and helloX.get("quiet_zone_modules") == 4
          and eanX.get("quiet_zone_modules") == 11,
          f"echo_bad={_echoX[:6]} lvl_matters={_lmX} q2={q2X.get('quiet_zone_modules')} "
          f"qr_default={helloX.get('quiet_zone_modules')} ean_default={eanX.get('quiet_zone_modules')}")

    _pX, _cX, _sX = [], [], []
    if _HAS_CVX:
        for (_lv, _vv, _pl, _rr) in _qX:
            _kX = "(%s,v%s,n=%d)" % (_rr.get("ec_level"), _rr.get("version"), len(_pl))
            if _rr.get("implemented") is not True or not _rr.get("matrix"):
                _sX.append(_kX + " 未出图")
                continue
            _pm = _npx.array([[int(ch) for ch in r] for r in _rr["matrix"]], dtype=_npx.uint8)
            _gx = _cv2x.imdecode(_npx.fromfile(_rr["output_path"], dtype=_npx.uint8),
                                 _cv2x.IMREAD_GRAYSCALE)
            _okp = _decx(_gx) == _pl
            try:
                _okc = _decx(_rasterx(_ownx(_pl, _rr["version"], _rr["ec_level"], _rr["side"]), 8, 4)) == _pl
            except Exception:
                _okc = True
            if not _okp:
                _pX.append(_kX)
                if not any(_decx(_rasterx(_pm, sc, qz)) == _pl
                           for qz in (2, 3, 4, 5, 6, 7) for sc in (4, 6, 8, 12, 16)):
                    _sX.append(_kX)
            if not _okc:
                _cX.append(_kX)
    check("EXE(P57): third-party OpenCV decodes EVERY frozen-exe QR PNG verbatim (43 rows: v1..v10 x L/M/Q/H minimal-trigger + Numeric/Alphanumeric/UTF-8 中文; ruler-hole exemption requires P subset-of C + geometry sweep read-back)",
          _HAS_CVX and not _trigX_bad and len(rowsX) == 40 and not _sX
          and set(_pX) <= set(_cX) and len(_qX) == 43,
          f"cv_missing={not _HAS_CVX}{'' if _HAS_CVX else ' -> ' + _CVX_ERR} "
          f"trig_bad={_trigX_bad[:3]} rows={len(rowsX)} 产品默认栅格读不回={len(_pX)} "
          f"尺子自读不回={len(_cX)} 扫描后仍读不回={_sX[:4]}")

    _ctrlX = _decx(_cv2x.imdecode(_npx.fromfile(helloX["output_path"], dtype=_npx.uint8),
                                  _cv2x.IMREAD_GRAYSCALE)) == "hello" if _HAS_CVX else False
    _negX = {}
    if _HAS_CVX:
        _negX["all_white"] = _decx(_npx.full((400, 400), 255, dtype=_npx.uint8))
        _hm = _npx.array([[int(ch) for ch in r] for r in helloX["matrix"]], dtype=_npx.uint8)
        _e1 = _hm.copy(); _e1[0:9, 0:9] = 0
        _e2 = _hm.copy(); _e2[0:9, 0:9] = 0; _e2[-9:, -9:] = 0; _e2[-9:, 0:9] = 0
        _negX["erase_tl_finder"] = _decx(_rasterx(_e1, 8, 4))
        _negX["erase_all_finders"] = _decx(_rasterx(_e2, 8, 4))
    check("EXE(P57): QR oracle self-calibration inside frozen-exe run (positive control decodes) + 3 negative controls decode to NOTHING (all-white / finder erased / all finders erased)",
          _HAS_CVX and _ctrlX and len(_negX) == 3 and all(v == "" for v in _negX.values()),
          f"cv_missing={not _HAS_CVX} positive={_ctrlX} negatives={json.dumps(_negX)}")
    # ==== Phase 62 block start（L4 侧一维 cv2.barcode oracle；变异判据按此标记整块抽取 exec）====
    # 与 L2 同款、但一切走 HTTP：图由**冻结 EXE** 现渲染，位串/静区/模块宽一律取该 EXE 自己的申报，
    # 不从仓库源码派生（测链接而非测源码）。尺子=手抄 GS1 常量 + 本件自家光栅，与产品源无 import 关系。
    # 2026-10-11 就地改核（与 L2 第 2184-2201 行同源同据）：第 1 版把期望码制钉死成 EAN_13/UPC_A，
    # 于是检测器如实回 `EAN_8` 被判成「读不回」，进而把「EAN-8 两侧同梯全不解 ⇒ 尺子洞豁免」写成立论
    # 依据。实测 p62_c128_determinism_receipt.txt（6,870 B 件 = p62_probe_oracle_receipt.txt，同图 12 次
    # ×5 形制零抖动、6 个新实例同读，sha256/16 978ddbf94fbaf592）证明那是**判卷侧写错了形状**，
    # 不是产品缺陷也不是抖动。⇒ 期望码制改由码长现场派生，EAN-8 从此必须真正逐字读回（判据变严），
    # P ⊆ C 降级为结构性不变式继续钉住（本轮两侧均为空集）。点位集合只记录不作合格线。
    # 2026-10-11 就地改核第二条（与 L2 Phase 62 块注的同名段落源同据，此处只镜像本层所需）：宽静区
    # 不是中性对照——变异驱动观测臂 q13（静区 11→13）实测产品与尺子两侧命中点集分叉而位串逐模块
    # 相等，尺子正对照腿自断 ⇒ 零桩 ctrl 回到本仓库既有形制（空补丁），宽静区降级为不裁决的观测臂。
    # 凭证 p62_mutation_receipt.txt（14,989 B / sha256/16 481806a7bda4dbf1）。本层判据同样只钉
    # 「梯内至少一点逐字读回 + 申报位串与尺子编码逐模块相等 + P ⊆ C」，不钉点位。
    # 顺带更正上面第 806 行的一处笔误（旧行逐字保留不删）：1,729 B / sha256/16 978ddbf94fbaf592 是
    # p62_c128_determinism_receipt.txt；6,870 B 那份是 p62_probe_oracle_receipt.txt，两者不是同一件。
    # 本层实跑（2026-10-11 01:14，_p62_l4_run2.log 63,822 B / sha256/16 8abba17336512bb1，
    # 裁决行 TOTAL 154 | PASS 154 | FAIL 0）：冻结 EXE 现渲染 EAN-13 两枚 = [16,24,32,40]、
    # EAN-8 = [40]，五枚阴性真改像素且零假解码，11 枚单模块翻转零复原 ⇒ 两枚 EXE(P62) 判据 GREEN。
    _bc62x = None
    if _HAS_CVX:
        try:
            from cv2 import barcode as _bcmod62x
            _bc62x = _bcmod62x.BarcodeDetector()
        except Exception as _ebc62x:
            _bc62x = None
            _CVX_ERR = _CVX_ERR + " | cv2.barcode: " + repr(_ebc62x)
    _HAS_BC62X = _bc62x is not None
    _PADS62X = (0, 16, 24, 32, 40)
    _LT62x = {"0": "0001101", "1": "0011001", "2": "0010011", "3": "0111101", "4": "0100011",
              "5": "0110001", "6": "0101111", "7": "0111011", "8": "0110111", "9": "0001011"}
    _GT62x = {"0": "0100111", "1": "0110011", "2": "0011011", "3": "0100001", "4": "0011101",
              "5": "0111001", "6": "0000101", "7": "0010001", "8": "0001001", "9": "0010111"}
    _RT62x = {"0": "1110010", "1": "1100110", "2": "1101100", "3": "1000010", "4": "1011100",
              "5": "1001110", "6": "1010000", "7": "1000100", "8": "1001000", "9": "1110100"}
    _PT62x = {"0": "LLLLLL", "1": "LLGLGG", "2": "LLGGLG", "3": "LLGGGL", "4": "LGLLGG",
              "5": "LGGLLG", "6": "LGGGLL", "7": "LGLGLG", "8": "LGLGGL", "9": "LGGLGL"}

    def _bits1362x(c13):
        b = "101"
        for ch, p in zip(c13[1:7], _PT62x[c13[0]]):
            b += (_LT62x if p == "L" else _GT62x)[ch]
        b += "01010"
        for ch in c13[7:13]:
            b += _RT62x[ch]
        return b + "101"

    def _bits862x(c8):
        b = "101"
        for ch in c8[:4]:
            b += _LT62x[ch]
        b += "01010"
        for ch in c8[4:]:
            b += _RT62x[ch]
        return b + "101"

    def _raster62x(bits, scale, height, quiet, margin_y):
        w = len(bits) * scale + 2 * quiet * scale
        img = _npx.full((height + 2 * margin_y, w), 255, _npx.uint8)
        x = quiet * scale
        for m in bits:
            if m == "1":
                img[margin_y:margin_y + height, x:x + scale] = 0
            x += scale
        return img

    def _pad62x(img, p):
        if p <= 0:
            return img
        return _cv2x.copyMakeBorder(img, p, p, p, p, _cv2x.BORDER_CONSTANT, value=255)

    def _dec62x(img):
        try:
            _ok62x, _txt62x, _tys62x, _cn62x = _bc62x.detectAndDecodeWithType(img)
            return [t for t in (_txt62x or []) if t], [t for t in (_tys62x or []) if t]
        except Exception:
            return [], []

    def _want62x(code):
        """期望码制**由码长现场派生**（13→EAN_13 / 8→EAN_8），一律不写死（见上方就地改核）。"""
        if len(code) == 13:
            return ("EAN_13", "EAN13")
        if len(code) == 8:
            return ("EAN_8", "EAN8")
        return ("",)

    def _match62x(code, txt, tys):
        _w62x = _want62x(code)
        for i, t in enumerate(txt):
            y = tys[i] if i < len(tys) else ""
            if t == code and y in _w62x:
                return True
            if len(code) == 13 and code.startswith("0") and y == "UPC_A" \
                    and t == code[1:] and len(t) == 12:
                return True
        return False

    def _hits62x(img, code):
        return [p for p in _PADS62X if _match62x(code, *_dec62x(_pad62x(img, p)))]

    def _rd62x(path):
        if not path or not os.path.isfile(path):
            return None
        return _cv2x.imdecode(_npx.fromfile(path, dtype=_npx.uint8), _cv2x.IMREAD_GRAYSCALE)

    def _geo62x(img):
        rows = _npx.where((img < 128).any(axis=1))[0]
        cols = _npx.where((img < 128).any(axis=0))[0]
        if not len(rows) or not len(cols):
            return -1, -1, -1, -1
        return (int(rows[0]), int(rows[-1] - rows[0] + 1),
                int(cols[0]), int(cols[-1] - cols[0] + 1))

    _REQ62X = (("EAN13", "590123412345"), ("EAN13", "400638133393"), ("EAN8", "9638507"))

    # ---- Phase 63 G 腿 L4 镜像（2026-10-11 新增；离线：零 EXE 依赖、零网络、零像素）：
    #  GS1 数字模式表**构造律**自检——与 L2 侧 packaging(P63) 同口径的逐表核验，落在此处
    #  是因为冻结 EXE 验收层带的是**另一份手抄表**（_LT62x/_GT62x/_RT62x/_PT62x），两份抄本
    #  各错各的：L2 侧抓到缺陷不代表 L4 侧这份也干净。判卷侧抄错形状（P57/P62 反复实证）修的
    #  是判卷侧，本腿即判卷侧的构造律防线。
    #  立论同 L2：本块向 EXE 请求的两枚 EAN-13，其 PARITY 串把左半 G 槽只落在下面**现场派生**
    #  的数字集合上；EAN-8 根本没有 parity 概念 ⇒ 若手抄 G 表在某个未被任何请求码触及的槽位
    #  上错成别族位串，本层所有依赖码串的腿（_bitsok62x 逐模块相等、边距梯读回、P⊆C）会一律
    #  保持绿色而静默放行——上一代 G(7) 抄成 L(4) 正是这样静默的。期望值一律由 L 表 + 构造律
    #  现场算出，本腿正文不写死任何一格的位串；L 表自身由本块既有的 cv2.barcode 解码腿绝对锚定。
    def _p63x_complement(_s63x):
        return "".join("1" if _c63x == "0" else "0" for _c63x in _s63x)

    def _p63x_reverse(_s63x):
        return _s63x[::-1]

    def _p63x_gslot_digits(_codes):
        """派生：本块所请求的 EAN-13 码，其左半 G 槽实际落在哪些数字上（正对照枚举器，
        数不出 >=1 即说明派生链自己断了——那种绿不予采信）。"""
        _out63x = set()
        for _sym63x, _c63x in _codes:
            if _sym63x != "EAN13" or len(_c63x) != 12:
                continue
            _par63x = _PT62x[_c63x[0]]
            for _i63x in range(6):
                if _par63x[_i63x] == "G":
                    _out63x.add(_c63x[1 + _i63x])
        return _out63x

    _p63x_digits = "0123456789"
    _p63x_R_bad = [_d63x for _d63x in _p63x_digits
                   if _RT62x[_d63x] != _p63x_complement(_LT62x[_d63x])]
    _p63x_G_bad = [_d63x for _d63x in _p63x_digits
                   if _GT62x[_d63x] != _p63x_reverse(_p63x_complement(_LT62x[_d63x]))]
    _p63x_fam_card = [len(set(_t63x.values())) for _t63x in (_LT62x, _GT62x, _RT62x)]
    _p63x_fam_len = sorted({len(_v63x) for _t63x in (_LT62x, _GT62x, _RT62x)
                            for _v63x in _t63x.values()})
    _p63x_LG_inter = sorted(set(_LT62x.values()) & set(_GT62x.values()))
    _p63x_LR_inter = sorted(set(_LT62x.values()) & set(_RT62x.values()))
    _p63x_GR_inter = sorted(set(_GT62x.values()) & set(_RT62x.values()))
    _p63x_par_bad = [_d63x for _d63x in _p63x_digits
                     if len(_PT62x[_d63x]) != 6 or (set(_PT62x[_d63x]) - {"L", "G"})
                     or _PT62x[_d63x][0] != "L"]
    _p63x_par_uniq = len(set(_PT62x.values())) == 10
    _p63x_g7_law = _p63x_reverse(_p63x_complement(_LT62x["7"]))
    _p63x_exercised = sorted(_p63x_gslot_digits(_REQ62X))
    _p63x_g7_exercised = "7" in _p63x_exercised
    check("EXE(P63): the frozen-exe ruler's OWN second hand-copied GS1 digit-pattern tables satisfy the "
          "construction laws, checked table-by-table instead of code-by-code - R_d == complement(L_d) and "
          "G_d == reverse(complement(L_d)) for ALL TEN digits, the L/G/R families pairwise disjoint, each "
          "family ten distinct 7-module strings, PARITY ten distinct 6-character strings over {L,G} each "
          "starting with L, and the G-slot-digit enumerator itself proves it enumerated at least one digit. "
          "Every expected bit-string is DERIVED live from the L table, never transcribed; this is the L4 "
          "mirror of the L2 leg and guards a SEPARATE transcription, because the two files each carry their "
          "own hand-copied tables and a typo in one says nothing about the other. Rationale: the EAN-13 "
          "codes requested here route their left-half G slots only through the derived digit set in the "
          "detail (EAN-8 has no parity), so a G-table typo in an unexercised slot is invisible to every "
          "code-string leg in this layer - exactly how the previous generation's G slot duplicated an "
          "L-family string while all of them stayed green",
          not _p63x_R_bad and not _p63x_G_bad and _p63x_fam_card == [10, 10, 10]
          and _p63x_fam_len == [7] and not _p63x_LG_inter and not _p63x_LR_inter
          and not _p63x_GR_inter and not _p63x_par_bad and _p63x_par_uniq
          and len(_p63x_exercised) >= 1,
          json.dumps({"R_bad": _p63x_R_bad, "G_bad": _p63x_G_bad,
                      "G7_actual": _GT62x["7"], "G7_law_derived": _p63x_g7_law,
                      "L7": _LT62x["7"],
                      "family_cardinality": _p63x_fam_card,
                      "family_string_lengths": _p63x_fam_len,
                      "L_inter_G": _p63x_LG_inter, "L_inter_R": _p63x_LR_inter,
                      "G_inter_R": _p63x_GR_inter,
                      "parity_bad": _p63x_par_bad, "parity_all_distinct": _p63x_par_uniq,
                      "g_slot_digits_exercised_by_this_block": _p63x_exercised,
                      "g_slot_7_exercised": _p63x_g7_exercised,
                      "why_a_G_typo_was_silent": (bool(_p63x_G_bad) and not _p63x_g7_exercised)}))
    _R62X = {}
    for _sym62x, _c62x in _REQ62X:
        _rp62x = requests.post(BASE + "/api/v1/packaging/barcode", timeout=60,
                               json={"code": _c62x, "symbology": _sym62x,
                                     "output_path": str(tmp / ("p62X_%s_%s.png" % (_sym62x.lower(), _c62x)))})
        _R62X[(_sym62x, _c62x)] = (_rp62x.status_code,
                                   _rp62x.json() if _rp62x.status_code == 200 else {})
    _prod62x, _ruler62x, _bitsok62x, _asis62x, _geo62xd = {}, {}, {}, {}, {}
    _want62xd, _readback62x = {}, {}
    for (_sym62x, _c62x), (_st62x, _j62x) in _R62X.items():
        _code62x = _j62x.get("code") or ""
        _img62x = _rd62x(_j62x.get("output_path") or "") if _HAS_BC62X else None
        _sc62x = int(_j62x.get("scale") or 0)
        _qz62x = int(_j62x.get("quiet_zone_modules") or 0)
        _g62x = _geo62x(_img62x) if _img62x is not None else (-1, -1, -1, -1)
        _geo62xd[(_sym62x, _c62x)] = _g62x
        _want62xd[(_sym62x, _c62x)] = list(_want62x(_code62x))
        _own62x = ""
        if _sym62x == "EAN13" and len(_code62x) == 13:
            _own62x = _bits1362x(_code62x)
        elif _sym62x == "EAN8" and len(_code62x) == 8:
            _own62x = _bits862x(_code62x)
        _decl62x = _j62x.get("bit_pattern") or ""
        _bitsok62x[(_sym62x, _c62x)] = (_own62x != "" and _own62x == _decl62x
                                        and len(_decl62x) == (95 if _sym62x == "EAN13" else 67))
        _prod62x[(_sym62x, _c62x)] = _hits62x(_img62x, _code62x) if _img62x is not None else []
        _readback62x[(_sym62x, _c62x)] = [[_p62xrb] + [list(z) for z in _dec62x(_pad62x(_img62x, _p62xrb))]
                                          for _p62xrb in _prod62x[(_sym62x, _c62x)]] if _img62x is not None else []
        _asis62x[(_sym62x, _c62x)] = (_dec62x(_img62x)[0] if _img62x is not None else ["<no-image>"])
        if _img62x is not None and _own62x and _sc62x > 0 and _qz62x >= 0 and _g62x[0] >= 0:
            _ruler62x[(_sym62x, _c62x)] = _hits62x(
                _raster62x(_own62x, _sc62x, _g62x[1], _qz62x, _g62x[0]), _code62x)
        else:
            _ruler62x[(_sym62x, _c62x)] = []
    _P62X = {k for k in _prod62x if not _prod62x[k]}
    _C62X = {k for k in _ruler62x if not _ruler62x[k]}
    _sub62X = _P62X <= _C62X
    _bad62x = [k for k in _prod62x if not _prod62x[k]]
    _ctlbad62x = [k for k in _ruler62x if not _ruler62x[k]]
    _nofab62x = all((_j62x.get("fabricated") is not True) and _st62x == 200
                    for (_st62x, _j62x) in _R62X.values())
    check("EXE(P62): the cv2.barcode oracle reads back EVERY live render of the FROZEN EXE VERBATIM on the "
          "measured margin ladder - all three requested symbologies, both EAN-13s AND EAN-8, each at its OWN "
          "code-length-derived symbology string (the EAN_13/UPC_A-only matcher was this ruler's own defect, "
          "corrected 2026-10-11; EAN-8 now carries a real read-back obligation instead of an exemption) - and "
          "the ruler's hand-copied GS1 encoding + own raster must read back as positive control; the EXE's "
          "declared bit_pattern must equal that independent encoding module-for-module (95 EAN-13 / 67 EAN-8); "
          "the structural invariant P subset-of C stays pinned so an exe decoding WORSE than the independent "
          "encoder is caught even while it is an empty set; pad=0 as-is recorded never required; the decoder's "
          "blind spots stay in this comment and are not a pass line",
          _HAS_BC62X and len(_prod62x) == len(_REQ62X) and _nofab62x
          and all(_bitsok62x.values())
          and all(_want62xd[k][0] for k in _want62xd)
          and not _bad62x and not _ctlbad62x and _sub62X,
          json.dumps({"cv_missing": not _HAS_CVX, "barcode_missing": not _HAS_BC62X,
                      "cv_err": _CVX_ERR if not _HAS_BC62X else "",
                      "ladder": list(_PADS62X),
                      "want_symbology": {"%s/%s" % k: v for k, v in _want62xd.items()},
                      "prod_hits": {"%s/%s" % k: v for k, v in _prod62x.items()},
                      "prod_readback": {"%s/%s" % k: v for k, v in _readback62x.items()},
                      "ruler_hits": {"%s/%s" % k: v for k, v in _ruler62x.items()},
                      "as_is": {"%s/%s" % k: v for k, v in _asis62x.items()},
                      "bits_equal_ruler": {("%s/%s" % k): v for k, v in _bitsok62x.items()},
                      "geometry": {"%s/%s" % k: list(v) for k, v in _geo62xd.items()},
                      "P": sorted("%s/%s" % k for k in _P62X),
                      "C": sorted("%s/%s" % k for k in _C62X),
                      "P_subset_C": _sub62X}, ensure_ascii=False))
    _neg62x = {}
    _flipn62x = 0
    _flipbad62x = []
    if _HAS_BC62X:
        _k13x = ("EAN13", "590123412345")
        _base62x = _rd62x(_R62X[_k13x][1].get("output_path") or "")
        _my62x, _bh62x, _x062x, _bw62x = _geo62xd[_k13x]
        _sc62xb = int(_R62X[_k13x][1].get("scale") or 0)
        _qz62xb = int(_R62X[_k13x][1].get("quiet_zone_modules") or 0)
        if _base62x is not None and _my62x >= 0 and _sc62xb > 0 and _x062x >= 0:
            _n1x = _npx.full(_base62x.shape, 255, _npx.uint8)
            _n2x = _base62x.copy(); _n2x[_my62x:_my62x + _bh62x, _x062x:_x062x + 3 * _sc62xb] = 255
            _n3x = _base62x.copy()
            _n3x[_my62x:_my62x + _bh62x, _x062x + 45 * _sc62xb:_x062x + 50 * _sc62xb] = 255
            _n4x = _base62x.copy(); _n4x[_my62x:_my62x + _bh62x, :] = 128
            _n5x = _npx.random.default_rng(62).integers(0, 256, _base62x.shape, dtype=_npx.uint8)
            for _nkx, _nvx in (("all_white", _n1x), ("start_guard_erased", _n2x),
                               ("centre_guard_erased", _n3x), ("bars_greyed", _n4x), ("noise", _n5x)):
                _chgx = int((_nvx != _base62x).sum())
                _false62x = [p for p in _PADS62X if _dec62x(_pad62x(_nvx, p))[0]]
                _neg62x[_nkx] = {"changed_px": _chgx, "false_pads": _false62x}
            _bits62xs = _R62X[_k13x][1].get("bit_pattern") or ""
            _code62xs = _R62X[_k13x][1].get("code") or ""
            for _colx in sorted({0, 1, 2, 10, 20, 30, 45, 47, 60, 80, len(_bits62xs) - 1}):
                _xx = _qz62xb * _sc62xb + _colx * _sc62xb
                assert 0 <= _xx and _xx + _sc62xb <= _base62x.shape[1], (_colx, _xx, _base62x.shape[1])
                _fx = _base62x.copy()
                _fx[_my62x:_my62x + _bh62x, _xx:_xx + _sc62xb] = 255 if _base62x[_my62x, _xx] < 128 else 0
                assert int((_fx != _base62x).sum()) > 0, ("no-op flip", _colx)
                _flipn62x += 1
                if _code62xs in [t for p in _PADS62X for t in _dec62x(_pad62x(_fx, p))[0]]:
                    _flipbad62x.append(_colx)
    check("EXE(P62): 1D oracle instrument self-calibration inside the frozen-exe run - 5 negative controls "
          "(all-white / start guard 101 erased / centre guard 01010 erased / bars greyed to 128 / RNG noise) "
          "decode to NOTHING at every pad, each proven to have really changed pixels (no vacuous no-op), and 11 "
          "bounds-asserted single-module flips NEVER read back the original code",
          _HAS_BC62X and len(_neg62x) == 5 and _flipn62x == 11
          and all(v["changed_px"] > 0 for v in _neg62x.values())
          and not any(v["false_pads"] for v in _neg62x.values())
          and not _flipbad62x,
          json.dumps({"barcode_missing": not _HAS_BC62X, "negatives": _neg62x,
                      "flips_tried": _flipn62x, "flips_read_back_original": _flipbad62x},
                     ensure_ascii=False))
    # ==== Phase 62 block end ====


    _overX = requests.post(BASE + "/api/v1/packaging/barcode", timeout=60,
                           json={"code": "A" * 400, "symbology": "QR"})
    _emptyX = requests.post(BASE + "/api/v1/packaging/barcode", timeout=60,
                            json={"code": "", "symbology": "QR"})
    _badX = requests.post(BASE + "/api/v1/packaging/barcode", timeout=60,
                          json={"code": "hello", "symbology": "QR", "ec_level": "X"})
    _c128X = requests.post(BASE + "/api/v1/packaging/barcode", timeout=60,
                           json={"code": "ABC123", "symbology": "Code128"}).json()
    check("EXE(P57): QR honest boundaries in frozen exe (payload beyond v10 -> 400 naming implemented range; empty -> 400; illegal ec_level -> 400; Code-128 still needs_external with reason naming it, red-line not faked)",
          _overX.status_code == 400 and "v1..v10" in json.dumps(_overX.json(), ensure_ascii=False)
          and _emptyX.status_code == 400 and _badX.status_code == 400
          and _c128X.get("implemented") is False and _c128X.get("status") == "needs_external"
          and "Code-128" in (_c128X.get("reason") or ""),
          f"over={_overX.status_code} empty={_emptyX.status_code} badlvl={_badX.status_code} "
          f"c128={_c128X.get('status')}")
    _ebox = [
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
    p_topo = requests.post(BASE + "/api/v1/packaging/panel-topology", json={"panels": _ebox}).json()
    check("EXE: real panel-topology geometry in frozen exe (9/8/connected/cut=870)",
          p_topo.get("implemented") is True and p_topo.get("fabricated") is False
          and p_topo.get("panel_count") == 9 and p_topo.get("relation_count") == 8
          and p_topo.get("is_connected") is True and p_topo.get("cut_boundary_length") == 870,
          json.dumps({k: p_topo.get(k) for k in ("panel_count", "relation_count", "is_connected", "cut_boundary_length")}))

    # EXE 级：自动更新引擎（§75-§77）真实 HTTP 拉取 + HMAC 验签在冻结归档内可用
    import hashlib as _hl, http.server as _hs, socketserver as _ss, threading as _th
    _SECRET = "exe-acc-key"
    _ART = b"EXE-UPDATED-PAYLOAD-v2"
    _ART_SHA = _hl.sha256(_ART).hexdigest()

    class _FeedH(_hs.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass
        def do_GET(self):
            host = f"http://{self.headers.get('Host')}"
            if self.path == "/manifest.json":
                m = {"version": "2.0.0", "url": f"{host}/artifact.bin", "sha256": _ART_SHA}
                m["signature"] = _hmac_sign(m, _SECRET)
                b = json.dumps(m).encode("utf-8")
                self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(b)
            elif self.path == "/artifact.bin":
                self.send_response(200); self.send_header("Content-Type", "application/octet-stream"); self.end_headers(); self.wfile.write(_ART)
            else:
                self.send_response(404); self.end_headers()

    def _hmac_sign(manifest, secret):
        import hmac as _h
        m = {k: v for k, v in manifest.items() if k != "signature"}
        body = json.dumps(m, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return _h.new(secret.encode("utf-8"), body, _hl.sha256).hexdigest()

    _feed = _ss.TCPServer(("127.0.0.1", 0), _FeedH)
    _fport = _feed.server_address[1]
    _th.Thread(target=_feed.serve_forever, daemon=True).start()
    try:
        _fb = f"http://127.0.0.1:{_fport}"
        ck_none = requests.post(BASE + "/api/v1/update/check", json={"channel": "stable"}).json()
        check("EXE: update honest no_update_configured (red-line)",
              ck_none.get("status") == "no_update_configured" and ck_none.get("fabricated") is False, json.dumps(ck_none))
        requests.post(BASE + "/api/v1/update/configure",
                      json={"channel": "dev", "feed_url": _fb + "/manifest.json", "secret": _SECRET, "current_version": "1.0.0"})
        ck = requests.post(BASE + "/api/v1/update/check", json={"channel": "dev", "current_version": "1.0.0"}).json()
        check("EXE: update real HTTP+HMAC verify works in frozen exe",
              ck.get("status") == "update_available" and ck.get("verified") is True
              and ck.get("remote_version") == "2.0.0", json.dumps(ck))
    finally:
        _feed.shutdown(); _feed.server_close()

    # EXE 级：NAS 引擎真实可用（nas_engine + msvcrt 随归档打入）
    gn = requests.post(BASE + "/api/v1/nas/connect", json={"target_id": "ghost"}).json()
    check("EXE: nas honest not_configured (red-line)",
          gn.get("status") == "not_configured" and gn.get("fabricated") is False, json.dumps({k: gn.get(k) for k in ('status', 'fabricated')}))
    nshare = tmp / "exe_nas"
    nshare.mkdir(parents=True, exist_ok=True)
    (nshare / "doc.txt").write_text("nas-in-exe", encoding="utf-8")
    requests.post(BASE + "/api/v1/nas/configure",
                  json={"target_id": "fs", "target": str(nshare), "kind": "filesystem", "share_root": str(nshare)}).json()
    cn = requests.post(BASE + "/api/v1/nas/connect", json={"target_id": "fs", "timeout": 3}).json()
    check("EXE: nas filesystem real connect via native redirector",
          cn.get("status") == "connected" and cn.get("access") == "native-os-redirector", json.dumps({k: cn.get(k) for k in ('status', 'access')}))
    ln = requests.post(BASE + "/api/v1/nas/list", json={"target_id": "fs"}).json()
    doc = next((e for e in ln.get("entries", []) if e.get("name") == "doc.txt"), {})
    check("EXE: nas real enumeration in frozen exe",
          ln.get("status") == "ok" and doc.get("size") == len("nas-in-exe"), json.dumps([e.get("name") for e in ln.get("entries", [])]))
    lockf = nshare / "l.bin"; lockf.write_bytes(b"x")
    holder = subprocess.Popen([sys.executable, "-c",
        "import os,msvcrt,time,sys; fd=os.open(sys.argv[1],os.O_RDWR); msvcrt.locking(fd,msvcrt.LK_NBLCK,1); print('HELD',flush=True); time.sleep(float(sys.argv[2]))",
        str(lockf), "4"], stdout=subprocess.PIPE, text=True)
    held = holder.stdout.readline().strip()
    lkn = requests.post(BASE + "/api/v1/nas/lock", json={"target_id": "fs", "subpath": "l.bin"}).json()
    check("EXE: nas real file-lock detection (msvcrt bundled)",
          held == "HELD" and lkn.get("status") == "ok" and lkn.get("locked") is True, json.dumps({k: lkn.get(k) for k in ('status', 'locked')}))
    holder.terminate()
    try:
        holder.wait(timeout=5)
    except Exception:
        holder.kill()

    # EXE 级：真实 AI Provider 引擎（ai_provider_engine + urllib HTTP + ctypes DPAPI 均随冻结归档打入）
    import http.server as _xh, socketserver as _xs, threading as _xt
    _XKEY = "exe-provider-key"
    _XPNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
    _XPLAN = json.dumps({"goal": "g", "steps": ["s1", "s2"], "tools": ["ai.generate"], "constraints": ["c"],
                         "protected_objects": ["logo"], "verification": ["v"], "rollback": ["r"]}, ensure_ascii=False)

    class _XOpenAI(_xh.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"
        def log_message(self, *a): pass
        def _a(self): return self.headers.get("Authorization") == f"Bearer {_XKEY}"
        def _s(self, code, p):
            self.send_response(code); self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(p))); self.end_headers(); self.wfile.write(p)
        def _b(self):
            n = int(self.headers.get("Content-Length", 0) or 0)
            try: return json.loads(self.rfile.read(n).decode("utf-8")) if n else {}
            except Exception: return {}
        def do_GET(self):
            self._s(200, json.dumps({"data": [{"id": "m"}]}).encode()) if self.path.endswith("/models") and self._a() else self._s(401, b'{}')
        def do_POST(self):
            if not self._a(): self._s(401, b'{"error":{"message":"bad key"}}'); return
            b = self._b()
            if self.path.endswith("/chat/completions"):
                self._s(200, json.dumps({"model": "m", "choices": [{"index": 0, "message": {"role": "assistant", "content": _XPLAN}}],
                                         "usage": {"prompt_tokens": 5, "completion_tokens": 15, "total_tokens": 20}}).encode())
            elif self.path.endswith("/images/generations"):
                self._s(200, json.dumps({"model": "i", "data": [{"b64_json": _XPNG} for _ in range(int(b.get("n", 1)))]}).encode())
            else: self._s(404, b'{}')

    class _XTsrv(_xs.ThreadingTCPServer):
        allow_reuse_address = True
    _xfe = _XTsrv(("127.0.0.1", 0), _XOpenAI)
    _xt.Thread(target=_xfe.serve_forever, daemon=True).start()
    _xb = f"http://127.0.0.1:{_xfe.server_address[1]}/v1"
    try:
        pn = requests.post(BASE + "/api/v1/provider/chat", json={"prompt": "x"})
        check("EXE: provider honest 502 no_provider_configured (red-line)",
              pn.status_code == 502 and pn.json().get("detail", {}).get("status") == "no_provider_configured"
              and pn.json().get("detail", {}).get("fabricated") is False, f"HTTP {pn.status_code}")
        cx = requests.post(BASE + "/api/v1/provider/configure",
                           json={"provider_id": "exe", "base_url": _xb, "api_key": _XKEY, "model": "m", "image_model": "i"}).json()
        check("EXE: provider configure DPAPI-encrypted + key masked in frozen exe",
              cx.get("providers", {}).get("exe", {}).get("has_key") is True and _XKEY not in json.dumps(cx), json.dumps(cx)[:100])
        chx = requests.post(BASE + "/api/v1/provider/chat", json={"prompt": "hi", "provider_id": "exe"}).json()
        check("EXE: provider real chat HTTP round-trip + usage in frozen exe",
              chx.get("status") == "ok" and chx.get("content") and chx.get("fabricated") is False
              and chx.get("usage", {}).get("total_tokens") == 20, json.dumps({k: chx.get(k) for k in ('status',)}))
        plx = requests.post(BASE + "/api/v1/plan", json={"goal": "g", "provider_id": "exe"}).json()
        check("EXE: §17 planner real structured output in frozen exe",
              plx.get("status") == "ok" and plx.get("plan", {}).get("protected_objects") == ["logo"]
              and plx.get("fabricated") is False, f"steps={len(plx.get('plan',{}).get('steps',[]))}")
        # Phase 48：控制台"真实生图"按钮已在 wired 判据内。此处用同一本地 OpenAI 兼容桩证明该端点
        # 真落盘 PNG（非虚报张数、非假成功）；失败路径的诚实 502 已由 P37 与上方 P48 检查钉住。
        _im48 = requests.post(BASE + "/api/v1/provider/images",
                              json={"prompt": "minimal crane logo", "provider_id": "exe", "n": 2},
                              timeout=120).json()
        _f48 = [Path(p["path"]) for p in (_im48.get("files") or [])]
        check("EXE(P48): wired /api/v1/provider/images returns REAL bytes on a configured provider — images_generated equals files listed equals PNGs on disk (fabricated:false, never a虚报 count)",
              _im48.get("status") == "ok" and _im48.get("fabricated") is False
              and _im48.get("images_generated") == len(_f48) == 2
              and all(p.is_file() and p.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n" and p.stat().st_size > 0 for p in _f48),
              f"gen={_im48.get('images_generated')} files={len(_f48)} st={_im48.get('status')}")
        # Phase 56：品牌语义透传入口 /brand/semantic-insight 在冻结 EXE 内的活体实证——把真 PNG
        # 交给已配置的真实 OpenAI-兼容 Provider 桩（复用上方 _xb，非另造假象），端点必须真读图像字节、
        # 委派 chat()、把模型输出逐字透传（此处桩回 _XPLAN JSON），method 为真值、fabricated False、
        # 明文凭据 _XKEY 绝不落进响应；这同时为该 notWired 模板回填 route_hits>0（P43 申报界限收口）。
        _si56 = requests.post(BASE + "/api/v1/brand/semantic-insight",
                              json={"image_path": str(img), "provider_id": "exe",
                                    "prompt": "describe this logo's mood"}, timeout=60)
        _si56j = _si56.json()
        check("EXE(P56): wired /api/v1/brand/semantic-insight with a real Logo + configured vision Provider PASSES THROUGH the model's actual output verbatim (status ok, method real_provider_vision_passthrough, fabricated:False, image_bytes>0) and NEVER leaks the plaintext key into the response — the endpoint delegates to the provider, it does not synthesize semantics (§2 red line)",
              _si56.status_code == 200 and _si56j.get("status") == "ok"
              and _si56j.get("fabricated") is False
              and _si56j.get("method") == "real_provider_vision_passthrough"
              and _si56j.get("image_bytes", 0) > 0
              and isinstance(_si56j.get("semantics"), dict) and _si56j.get("semantics")
              and _XKEY not in json.dumps(_si56j, ensure_ascii=False),
              f"HTTP {_si56.status_code} st={_si56j.get('status')} m={_si56j.get('method')} bytes={_si56j.get('image_bytes')}")
        _si56_bad = requests.post(BASE + "/api/v1/brand/semantic-insight",
                                  json={"image_path": str(img), "provider_id": "no_such_p56_exe"},
                                  timeout=60)
        _si56_bj = _si56_bad.json().get("detail") if _si56_bad.status_code == 502 else None
        _si56_bbody = json.dumps(_si56_bad.json(), ensure_ascii=False).lower()
        check("EXE(P56): semantic-insight delegating to a NON-resolvable provider honestly degrades to 502 whose detail carries fabricated:False and passes the provider's real no_provider_configured status through verbatim (never a fabricated analysis when delegation fails — §2 red line)",
              _si56_bad.status_code == 502 and isinstance(_si56_bj, dict)
              and _si56_bj.get("fabricated") is False
              and _si56_bj.get("status") in ("needs_external", "no_provider_configured", "error")
              and "no_provider_configured" in _si56_bbody
              and '"semantics"' not in _si56_bbody,
              f"HTTP {_si56_bad.status_code} detail={json.dumps(_si56_bj, ensure_ascii=False)[:120]}")
    finally:
        try:
            _xfe.shutdown(); _xfe.server_close()
        except Exception:
            pass

    # EXE 级：一键交付包真实归档
    dp = requests.post(BASE + "/api/v1/delivery/package",
                       json={"project": "smoke", "files": {"Source": [str(img)]}}).json()
    check("EXE: delivery package built", dp.get("included") == 1 and os.path.isfile(dp.get("archive", "")),
          f"included={dp.get('included')} size={dp.get('archive_size_bytes')}")

    # ---------------- Phase 55 EXE 内可达性契约：门与台账随冻结归档一起出厂 ----------------
    # 源码层成立、冻结层不成立，是这个项目反复出现过的第三种假绿（P47/P50/P52 均重跑 EXE
    # 级实证）。故此处把 ①②③ 三处收口在真 EXE 内再量一遍：五形制的传输层判定、auto-fix
    # 同门、交付三形制 + 逐文件 inside_data_root + 事件台账，全部与 ZIP namelist 交叉对表。
    _h55e = Path(str(requests.get(BASE + "/api/v1/health", timeout=60).json()["data_root"])).resolve()
    _tr55e = str(Path(tmp) / ".." / ".." / "windows" / "win.ini")
    _ab55e = str(Path(os.environ.get("WINDIR", "C:\\Windows")) / "win.ini")
    _p55e = {}
    for _n55e, _v55e in (("ghost", str(tmp / "p55_exe_ghost.png")), ("traversal", _tr55e),
                         ("absolute", _ab55e), ("directory", str(tmp)), ("empty", "")):
        _p55e[_n55e] = requests.post(BASE + "/api/v1/preflight",
                                     json={"document_path": _v55e}, timeout=120)
    _want55e = {"ghost": 404, "traversal": 403, "absolute": 403, "directory": 400, "empty": 400}
    check("EXE(P55): the path gate ships inside the frozen onefile — /preflight answers 404 for a missing document, 403 for a relative .. traversal AND for an absolute C:\\Windows path, 400 for a directory and for an empty string; pre-fix the very same archive answered HTTP 200 for all five, and the absolute one came back as a full 7-finding print report for a system file the security engine names as blocked",
          all(_p55e[k].status_code == c for k, c in _want55e.items())
          and all("findings" not in _p55e[k].text for k in _want55e),
          {k: (_p55e[k].status_code, _p55e[k].text[:50]) for k in _p55e})
    _ir55e = requests.post(BASE + "/api/v1/preflight", json={"document_path": str(img)},
                           timeout=120).json().get("input_reachability") or {}
    _fx55e = requests.post(BASE + "/api/v1/preflight/auto-fix",
                           json={"document_path": str(tmp / "p55_exe_ghost.png")}, timeout=120)
    check("EXE(P55): the machine-readable verdict ships too — a 200 report carries input_reachability whose size_bytes equals this test reading the same file off disk, and auto-fix goes through the SAME gate (404 on a missing document) instead of returning 200 with the missing input absorbed into its skipped list",
          _ir55e.get("reachable") is True and _ir55e.get("verdict") == "checked"
          and _ir55e.get("size_bytes") == os.path.getsize(str(img))
          and _fx55e.status_code == 404 and "文件不存在" in str(_fx55e.text),
          f"reach={json.dumps(_ir55e, ensure_ascii=False)[:150]} autofix={_fx55e.status_code}")
    # 仪器总量性（P53 `health = {}` 一族 / 本轮 L5 同一处复发）：修前的冻结 EXE 在目录与
    # 空串这两种形制上回的是 **HTTP 500 + 纯文本**，`.json()` 直接把整层 traceback 打断，
    # 于是这一条判据从来没有机会说话。此处把响应体安全取回：非 JSON 就当空台账，并把传输层
    # 状态码写进判词证据里——缺了门时证据是"500"，而不是"这一层没跑完"。
    _r55dl = requests.post(BASE + "/api/v1/delivery/package", timeout=180, json={
        "project": "p55-exe",
        "files": {"Print": [str(img)],
                  "Source": [str(tmp / "p55_exe_absent.png"), _tr55e, str(tmp), ""]}})
    try:
        _d55e = _r55dl.json()
    except Exception:
        _d55e = {}
    if not isinstance(_d55e, dict):
        _d55e = {}
    _sk55e = [s.get("reason") for s in (_d55e.get("skipped") or [])]
    _led55e = {e.get("name"): e.get("inside_data_root") for e in (_d55e.get("included_files") or [])}
    _in55e = Path(str(img)).resolve().is_relative_to(_h55e)
    _zip55e = []
    if os.path.isfile(str(_d55e.get("archive") or "")):
        with zipfile.ZipFile(_d55e["archive"]) as _z55e:
            _zip55e = _z55e.namelist()
    _ev55e = [e for e in (requests.get(BASE + "/api/v1/events/recent", params={"limit": 400},
                                       timeout=60).json().get("events") or [])
              if e.get("event") == "delivery.created"
              and (e.get("data") or {}).get("package_id") == _d55e.get("package_id")]
    check("EXE(P55): the delivery ledger ships too — skipped splits into not_found/blocked_by_policy/not_a_file (the directory and empty-string shapes that used to crash the whole request with 500 now land in the ledger), the included file carries inside_data_root recomputed here from the EXE's own /health.data_root, the self-reported count equals the ZIP namelist, and delivery.created's own skipped_<shape> tally equals the response it was emitted from",
          sorted(set(_sk55e)) == sorted(["not_found", "blocked_by_policy", "not_a_file"])
          and len(_sk55e) == 4 and _d55e.get("included") == 1
          and _led55e == {Path(img).name: _in55e}
          and _d55e.get("outside_root_count") == (0 if _in55e else 1)
          and "Print/" + Path(img).name in _zip55e
          and len(_ev55e) == 1
          and {k: v for k, v in (_ev55e[0].get("data") or {}).items() if k.startswith("skipped_")}
              == {"skipped_not_found": 1, "skipped_blocked_by_policy": 1, "skipped_not_a_file": 2},
          f"http={_r55dl.status_code} skipped={_sk55e} ledger={_led55e} in={_in55e} zip={_zip55e} ev={json.dumps(_ev55e, ensure_ascii=False)[:180]}")

    # EXE 级：事件总线真实记录（recovery/knowledge/delivery 端点已触发 task/delivery 事件）
    evr = requests.get(BASE + "/api/v1/events/recent", params={"limit": 200}).json()
    evnames = {e["event"] for e in evr.get("events", [])}
    check("EXE: event hub recorded real events", bool({"knowledge.ingested", "delivery.created"} & evnames),
          f"sample={sorted(evnames)[:5]}")

    # EXE 级：Adobe UXP 桥接（第 19 引擎 adobe_bridge 随冻结归档打入，端到端机制在真 EXE 内可用）
    import threading as _bth
    _bv = requests.get(BASE + "/api/v1/uxp/bridge").json()
    _bdisco = Path(_bv.get("discovery_file", ""))
    _bnh = requests.post(BASE + "/api/v1/uxp/dispatch", json={"tool": "ps.layer.create", "args": {"name": "x"}})
    check("EXE: uxp bridge honest 502 no_host_connected w/o real Adobe host (red-line, no fake doc op)",
          _bnh.status_code == 502 and _bnh.json()["detail"].get("error") == "no_host_connected"
          and _bnh.json()["detail"].get("fabricated") is False, f"HTTP {_bnh.status_code}")
    _btok = json.loads(_bdisco.read_text(encoding="utf-8")).get("session_token", "") if _bdisco.exists() else ""
    _bhs = requests.post(BASE + "/api/v1/uxp/handshake", json={"host": "photoshop", "version": "25.3.1",
          "capabilities": {"document_read": True, "layer_write": True}, "token": _btok})
    _bhj = _bhs.json()
    _bhid = _bhj.get("host_id")
    _bmem = {}
    _bstop = _bth.Event()
    def _bhost():
        while not _bstop.is_set():
            try:
                _pp = requests.post(BASE + "/api/v1/uxp/poll", json={"host_id": _bhid, "token": _btok, "timeout": 2}, timeout=8).json()
            except Exception:
                _bstop.wait(0.3); continue
            for _c in _pp.get("commands", []):
                _a = (_c.get("context") or {}).get("args") or {}
                _pr = ((_c.get("permissions") or {}).get("protected_objects")) or []
                if _c.get("intent") == "layer.create" and _a.get("name") in _pr:
                    requests.post(BASE + "/api/v1/uxp/result", json={"request_id": _c["request_id"], "token": _btok, "status": "error", "error": "protected_object"})
                elif _c.get("intent") == "layer.create":
                    _bmem[_a.get("name")] = True
                    requests.post(BASE + "/api/v1/uxp/result", json={"request_id": _c["request_id"], "token": _btok, "status": "ok", "payload": {"objects": [{"name": _a.get("name"), "created": True}]}})
                else:
                    requests.post(BASE + "/api/v1/uxp/result", json={"request_id": _c["request_id"], "token": _btok, "status": "error", "error": "not_supported"})
    _bth.Thread(target=_bhost, daemon=True).start()
    time.sleep(0.3)
    _bd1 = requests.post(BASE + "/api/v1/uxp/dispatch", json={"tool": "ps.layer.create", "args": {"name": "hero_exe"}, "timeout": 8}).json()
    _bd2 = requests.post(BASE + "/api/v1/uxp/dispatch", json={"tool": "ps.layer.create", "args": {"name": "lock"}, "protected_objects": ["lock"], "timeout": 8}).json()
    _bstop.set()
    check("EXE: uxp bridge real dispatch->host round-trip reconciled source=host in frozen exe",
          _bhj.get("status") == "connected" and _bd1.get("status") == "ok"
          and _bd1.get("reconciled", {}).get("source") == "host" and _bd1.get("fabricated") is False
          and "hero_exe" in _bmem, json.dumps(_bd1)[:120])
    check("EXE: uxp bridge protected object honestly refused -> protected_object_conflict",
          _bd2.get("status") == "host_error" and _bd2.get("error") == "protected_object_conflict"
          and _bd2.get("fabricated") is False and "lock" not in _bmem, json.dumps(_bd2)[:120])

    # EXE 级：能力真值表诚实
    capsr = requests.get(BASE + "/api/v1/capabilities")
    capj = capsr.json() if capsr.status_code == 200 else {}
    cid = {c["id"]: c["status"] for c in capj.get("capabilities", [])}
    _cn47 = {c["id"]: c for c in capj.get("capabilities", [])}
    check("EXE: capabilities honest manifest",
          capsr.status_code == 200 and capj.get("summary", {}).get("supported") == 40
          and capj.get("summary", {}).get("not_shipped") == 0
          and "brand_intelligence" not in cid and cid.get("brand_color_analysis") == "supported"
          # Phase 56 授权漂移：品牌语义识别 not_shipped -> supported。第 5 次真实漂移
          # （supported 36→37、not_shipped 2→1）——把早已内置的真实 OpenAI-兼容 Provider 视觉通路
          # 接进 Logo 图像（recognize_semantics 真读字节→构 data URL→委派 Provider.chat→逐字透传），
          # 未注入/不可达 Provider 诚实 needs_external/透传其真实失败，绝不合成调性/置信度（§2 红线）。
          # 留守的 not_shipped 仅 installer_execution 写侧执行（真装/卸/注册表写删，属外部主机域）。
          and cid.get("brand_semantic_recognition") == "supported"
          and "Phase 56" in _cn47["brand_semantic_recognition"].get("note", "")
          and cid.get("recovery_snapshot") == "supported"
          and cid.get("knowledge_rag") == "supported" and cid.get("delivery_package") == "supported"
          and cid.get("typography_engine") == "supported" and cid.get("design_review") == "supported"
          and cid.get("customer_revision") == "supported" and cid.get("color_engine") == "supported"
          and cid.get("layout_engine") == "supported" and cid.get("packaging_dieline_vdp") == "supported"
          and cid.get("packaging_barcode") == "supported" and cid.get("auto_update") == "supported"
          and cid.get("packaging_panel_topology") == "supported"
          and cid.get("nas_mount") == "supported"
          and cid.get("ai_provider_integration") == "supported"
          and cid.get("adobe_uxp_bridge") == "supported"
          and cid.get("ps_document_write") == "needs_adobe_host" and cid.get("ai_document_write") == "needs_adobe_host"
          # Phase 47 授权漂移：第 25 引擎真实交付，not_shipped -> supported
          and cid.get("packaging_panel_recognition") == "supported"
          and "Phase 47" in _cn47["packaging_panel_recognition"].get("note", "")
          # Phase 49 授权漂移：installer_execution 红线分裂——只读一半升为 installer_manifest_verification
          # supported（第 26 引擎），写侧执行仍如实留驻 not_shipped。total 38→39、supported 32→33。
          and cid.get("installer_manifest_verification") == "supported"
          and "Phase 49" in _cn47["installer_manifest_verification"].get("note", "")
          # Phase 50 授权漂移：desktop_hub not_shipped -> supported（第 27 引擎 desktop_hub_engine
          # 在冻结 EXE 内真建原生 Tk 窗口，见下方 hub-cli 判据；旧"需 Tauri 工具链"判词作废）。
          and cid.get("desktop_hub") == "supported"
          and "Phase 50" in _cn47["desktop_hub"].get("note", "")
          and "desktop_hub_engine" in _cn47["desktop_hub"].get("note", "")
          and capj.get("summary", {}).get("total") == len(capj.get("capabilities", [])) == 43
          # Phase 78 授权漂移：最小写集 cdr_document_write 转 supported ⇒ total 42→43、supported 39→40
          # （无新引擎，仍归 adobe_bridge；delete/rename/save/export 一律未注册）。
          and cid.get("cdr_document_write") == "supported"
          and "Phase 78" in _cn47["cdr_document_write"].get("note", "")
          and "cdr.object.create" in _cn47["cdr_document_write"].get("note", "")
          # Phase 53 授权漂移：installer 域红线再分裂——编译面（构建期工件核验）与预检面（只读安装
          # 预检）各升为一条 supported 能力（同一第 26 引擎 installer_manifest 承载），静默安装/卸载
          # 执行/注册表写删仍如实留驻 installer_execution=not_shipped。total 39→41、supported 34→36。
          and cid.get("installer_artifact_build") == "supported"
          and "Phase 53" in _cn47["installer_artifact_build"].get("note", "")
          and cid.get("installer_preflight") == "supported"
          and "Phase 53" in _cn47["installer_preflight"].get("note", "")
          and "installer_runtime" not in cid and cid.get("installer_runtime_guard") == "supported"
          # Phase 58 授权漂移（第 6 次真实漂移，也是这一族的收尾）：installer_execution 由
          # not_shipped 转 supported——执行器真的随包交付（CLI 显式令牌 install/uninstall/state），
          # 回执从磁盘+注册表差集派生，本机已实跑过一次真安装与真卸载（判据见下方 execution 腿）。
          # supported 37→38、not_shipped 1→0；本名单自此为空，剩下 3 项是 needs_adobe_host
          # （宿主进程列，不是 not_shipped）。旧判词以 retired_boundary.was 原文留在契约面，
          # 退役≠改写。
          and cid.get("installer_execution") == "supported"
          and "Phase 58" in _cn47["installer_execution"].get("note", "")
          and capj.get("summary", {}).get("prototype_not_shipped") == 0,
          f"summary={capj.get('summary')}")

    # Phase 78：写集工具面（EXE 级）。宿主脚本与 adobe-plugins 不随包，故这里只核三件纯服务端事实：
    # ① /uxp/bridge 的工具面恰为 3 读 + 2 写；② 无宿主时"缺 confirm"仍先回可审阅计划
    # （确认门先于宿主查找，零入队零变更）；③ delete/rename/save 一律未注册 ⇒ unknown_tool。
    _bv78 = requests.get(BASE + "/api/v1/uxp/bridge").json()
    _cdr_tools78 = {t for t in (_bv78.get("bridgeable_tools") or []) if t.startswith("cdr.")}
    _nc78 = requests.post(BASE + "/api/v1/uxp/dispatch",
                          json={"tool": "cdr.object.create",
                                "args": {"kind": "rectangle", "x1": 1, "y1": 2, "x2": 11, "y2": 22},
                                "timeout": 10})
    _ncj78 = _nc78.json()
    _del78 = requests.post(BASE + "/api/v1/uxp/dispatch",
                           json={"tool": "cdr.object.delete", "args": {"index": 1}, "timeout": 10})
    _save78 = requests.post(BASE + "/api/v1/uxp/dispatch",
                            json={"tool": "cdr.document.save", "args": {}, "timeout": 10})
    check("EXE: Phase 78 CDR minimal-write surface is exactly 3 read + 2 write tools; "
          "no-host confirm gate still returns a reviewable plan (zero enqueue) and delete/save stay unregistered",
          _cdr_tools78 == {"cdr.document.get", "cdr.page.list", "cdr.object.list",
                           "cdr.object.create", "cdr.object.move"}
          and _nc78.status_code == 200
          and _ncj78.get("status") == "confirmation_required"
          and (_ncj78.get("plan") or {}).get("save") is False
          and (_ncj78.get("plan") or {}).get("risk") == "L1"
          and _ncj78.get("fabricated") is False
          and _del78.status_code == 502 and _del78.json()["detail"].get("error") == "unknown_tool"
          and _save78.status_code == 502 and _save78.json()["detail"].get("error") == "unknown_tool",
          json.dumps({"tools": sorted(_cdr_tools78), "gate": _ncj78.get("status"),
                      "delete": _del78.status_code, "save": _save78.status_code}))

    dg = requests.post(BASE + "/api/v1/diagnostics/run").json()
    engine_pass = [c for c in dg["checks"] if c["check"] == "engines"][0]["status"]
    check("EXE: diagnostics engines pass", engine_pass == "pass", f"{dg['passed']}/{dg['total']}")

    # portable data stays local
    data_dir = PORT_DIR / "Data"
    check("EXE: portable Data\\ created locally", data_dir.exists() and (data_dir / "logs").exists(),
          str(sorted(p.name for p in data_dir.iterdir())) if data_dir.exists() else "missing")
    _ck23 = "p23_cfg_iso_exe"
    requests.put(BASE + f"/api/v1/config/{_ck23}", json={"value": "local-only"}, timeout=15)
    _usp = data_dir / "config" / "user-settings.json"
    check("EXE: config PUT lands inside local Data\\config (Phase 23 portable isolation)",
          _usp.exists() and _ck23 in _usp.read_text(encoding="utf-8"), str(_usp))

    # Phase 24: 评估中心确已编入冻结归档，且在本机真实落盘（非模拟 0.85）
    _ev = requests.post(BASE + "/api/v1/eval/run", timeout=240)
    _evj = _ev.json()
    _es = _evj.get("summary", {})
    check("EXE: eval run real-derived pass rate baked into frozen archive (Phase 24, 34 calib since Phase 59 +runtime_guard/+preflight own calibration + Phase 63 barcode-1D ×2)",
          _ev.status_code == 200 and _es.get("executed_cases", 0) >= 151 and _es.get("crashed_cases") == 0
          and _es.get("pass_rate") != 0.85 and _evj.get("honest") is True
          and len(_evj.get("calibration", [])) == 34
          and len(_evj.get("results", [])) == 185
          and all(c.get("actual_verdict") == "fail" for c in _evj.get("calibration", [])),
          json.dumps(_es, ensure_ascii=False))
    _ebc = (_evj.get("categories", {}) or {}).get("brand", {})
    check("EXE: eval brand category 4/4 with real PNG fixtures written inside local Data (Phase 27 frozen archive)",
          _ebc.get("total") == 4 and _ebc.get("passed") == 4 and _es.get("executed_cases") == 151
          and (data_dir / "evaluation" / "artifacts" / "eval_brand_two_band.png").exists()
          and (data_dir / "evaluation" / "artifacts" / "eval_brand_logo_rect.png").exists()
          and (data_dir / "evaluation" / "artifacts" / "eval_brand_logo_rect.png").read_bytes()[:8] == b"\x89PNG\r\n\x1a\n",
          json.dumps(_ebc, ensure_ascii=False))
    _epc = (_evj.get("categories", {}) or {}).get("print", {})
    check("EXE: eval print category 2/2 with real RGB+CMYK bitmap fixtures inside local Data (Phase 28 frozen archive)",
          _epc.get("total") == 2 and _epc.get("passed") == 2
          and (data_dir / "evaluation" / "artifacts" / "eval_print_rgb72.png").exists()
          and (data_dir / "evaluation" / "artifacts" / "eval_print_cmyk300.jpg").exists()
          and (data_dir / "evaluation" / "artifacts" / "eval_print_cmyk300.jpg").read_bytes()[:2] == b"\xff\xd8",
          json.dumps(_epc, ensure_ascii=False))
    _ekc = (_evj.get("categories", {}) or {}).get("knowledge", {})
    check("EXE: eval knowledge category 2/2 with real ingest persisted inside local Data (Phase 28 frozen archive)",
          _ekc.get("total") == 2 and _ekc.get("passed") == 2
          and (data_dir / "knowledge" / "kb_eval_coffee.json").exists(),
          json.dumps(_ekc, ensure_ascii=False))
    _eby30 = {r.get("id"): r for r in _evj.get("results", [])}
    _erv30 = (_evj.get("categories", {}) or {}).get("revision", {})
    _erw30 = (_evj.get("categories", {}) or {}).get("review", {})
    _esc30 = (_evj.get("categories", {}) or {}).get("security", {})
    check("EXE: eval revision/review/security categories 3+3+3 with real returned payloads in frozen archive (Phase 30)",
          _erv30.get("total") == 3 and _erv30.get("passed") == 3
          and _erw30.get("total") == 3 and _erw30.get("passed") == 3
          and _esc30.get("total") == 3 and _esc30.get("passed") == 3
          and _eby30.get("eval-review-dirty-matrix", {}).get("details", {}).get("actual", {}).get("score") == 68
          and _eby30.get("eval-revision-apply-math", {}).get("details", {}).get("actual", {}).get("title") == "#8B0000"
          and _eby30.get("eval-security-highrisk-no-overreport", {}).get("details", {}).get("actual", {}).get("status") == "no_executor",
          json.dumps({"rv": _erv30, "rw": _erw30, "sc": _esc30}, ensure_ascii=False))
    _ewf31 = (_evj.get("categories", {}) or {}).get("workflow", {})
    # Phase 58 再钉：installer_execution 黄金域 6 条（p58_eval_exec_payload.json 逐枚点名）
    # + installer_manifest 的 R14 卸载撤销七臂 1 条，合计把 executed 从 132 推到 134
    # （以 p58_l2_freeze_out.json 磁盘实量为据）。
    # Phase 59 再推：runtime_guard 黄金域 5 条真值（installer_manifest 的编译面/预检面只**改归**
    # 域名、不增减用例），executed 134 → 139；枚数由 p59_suite_count.py 只读枚举实测，非手抄。Phase 63（2026-10-11）一维解码 oracle 真值 4 条并入 barcode 域 ⇒ executed 143，读数取自本轮盘上评测报告。
    check("EXE: eval workflow category 6/6 graph-truth cases with exact payloads in frozen archive, executed 151 (Phase 31, grown by Phase 47 panel + Phase 49/53 installer + Phase 50 desktop_hub + Phase 56 brand_semantics + Phase 57 barcode QR + Phase 58 installer_execution & R14 arms + Phase 59 runtime_guard cases + Phase 63 barcode 1D oracle 4 cases + Phase 79 Adobe-bridge 3 golden cases + Phase 80 service-registry 3 golden cases + Phase 81 event/delivery 2 golden cases)",
          _ewf31.get("total") == 6 and _ewf31.get("passed") == 6 and _ewf31.get("failed") == 0
          and _es.get("executed_cases") == 151
          and _eby30.get("eval-workflow-fail-no-pass-on", {}).get("details", {}).get("actual", {}).get("n3_present") is False
          and _eby30.get("eval-workflow-loop-aggregate", {}).get("details", {}).get("actual", {}).get("result") == {"lp_iterations": 3},
          json.dumps(_ewf31, ensure_ascii=False))
    # Phase 58 判卷侧改核（exec 域）：id 与字段名一律取自 p58_eval_exec_payload.json 的盘上实况，
    # 落点根取自 p58_exec_disk_freeze.py 的实测（tempfile.gettempdir()/_zj_eval_exec/<tag>，不在
    # store_dir 子树内——store_dir 在源码树里，而 install_dir_inside_source_tree 门会把每一臂在门口
    # 拒掉）。以下整块由 p58_l4_repin_exec.py 从 L2 已验 PASS 的同名判据逐字改名提取，两侧不各自维护。
    _ec58y = (_evj.get("categories", {}) or {}).get("installer_execution", {})
    _ePy58 = _eby30.get("eval-exec-plan-disk-derived", {}).get("details", {}).get("actual", {})
    _eGy58 = _eby30.get("eval-exec-gates-refuse-zero-side-effects", {}).get("details", {}).get("actual", {})
    _eIy58 = _eby30.get("eval-exec-install-verified-from-diff", {}).get("details", {}).get("actual", {})
    _eVy58 = _eby30.get("eval-exec-verdict-ignores-self-report", {}).get("details", {}).get("actual", {})
    _eUy58 = _eby30.get("eval-exec-uninstall-three-forms-and-reconcile", {}).get("details", {}).get("actual", {})
    _eRy58 = _eby30.get("eval-exec-second-round-evidence-not-user-data", {}).get("details", {}).get("actual", {})
    _eWy58 = _eby30.get("calib-exec-trust-self-report", {})
    # 落点改核（p58_exec_disk_freeze.py，21:30 实测）：执行面沙箱按引擎自己的口径落在
    # **系统临时根** tempfile.gettempdir()/_zj_eval_exec/<tag>，不在 store_dir 子树里——
    # 因为 store_dir 位于源码树内，而执行器的 install_dir_inside_source_tree 门会把每一臂
    # 在门口拒掉。上一枚探针扫 store_dir 数出 [0,0,0]，把"我没扫到"读成"盘上没有"，
    # 差点用来论证本断言前提为假（自造仪器盲区，见 p58_exec_disk_freeze.py 文档串）。
    _xry58 = Path(tempfile.gettempdir()) / "_zj_eval_exec"
    _xsandy58 = sorted(p.name for p in _xry58.iterdir() if p.is_dir()) if _xry58.is_dir() else []
    _xely58 = sorted(p.relative_to(_xry58).as_posix() for p in _xry58.rglob("exec-ledger.json")) if _xry58.is_dir() else []
    _xegy58 = sorted(p.relative_to(_xry58).as_posix() for p in _xry58.rglob("zaojing-install.log")) if _xry58.is_dir() else []
    _xeny58 = sorted(p.relative_to(_xry58).as_posix() for p in _xry58.rglob("user-notes.json")) if _xry58.is_dir() else []
    _xeey58 = sorted(p.relative_to(_xry58).as_posix() for p in _xry58.rglob("ZaojingAI.exe")) if _xry58.is_dir() else []
    _xeuy58 = sorted(p.relative_to(_xry58).as_posix() for p in _xry58.rglob("unins000.exe")) if _xry58.is_dir() else []
    _xezy58 = sum(1 for p in _xry58.rglob("exec-ledger.json") if p.is_file() and p.stat().st_size > 0) if _xry58.is_dir() else -1
    _xr2y58 = sorted(p.relative_to(_xry58 / "round2" / "fake-local" / "Zaojing").as_posix()
                    for p in (_xry58 / "round2" / "fake-local" / "Zaojing").rglob("*")
                    if p.is_file()) if (_xry58 / "round2" / "fake-local" / "Zaojing").is_dir() else []
    check("EXE: installer_execution golden domain truly evaluated INSIDE the frozen archive - 6/6 real-fixture payloads on self-drawn sandboxes: plan 只算不跑（sha 独立复算全等+五枚落点 exists_now 现场重算+卸载键由 {{源文} 解成 {GUID}_is1+七门逐字+沙箱 listing 逐条不变）/ 十七臂各按 reason 码拒绝且拒绝代价真为零（台账从不落盘、唯一在场的安装根必须是那条非空臂自己种的目录）/ installed 只由磁盘+注册表差集派生（十项观察全 ok、created_count=去重后磁盘真相=4、既有数据逐字节完好、argv 三静默开关、日志 48 字节、台账七枚自报布尔 ignored 逐字）/ 同一份自报两趟判分相反（盘上有物 install_verified vs 空手 install_unverified 且十项观察恰 3 真 7 假、空手趟判 ledger_unverified 不伪造回执）/ 一装两卸三形制+对账活证（install_verified_awaiting_uninstall 诚实中间态、痕迹被台账之外手段抹掉被点名、uninstall_incomplete 三枚残留入列、真卸 removed 恰 4 枚逐枚 gone_now 且用户数据只点名不删、等长篡改唯 sha 点名一条、还原回 executed_verified、nothing_to_uninstall 与 missing_uninstaller 独立状态码）/ 证据与用户数据分账两轮往返（轮 2 自家台账被重写有评测独立复算的哈希为证仍判 uninstall_verified 且 deviating 空、创建集 5→4、对自家证据只回显不声称重写）；夹具在盘上真实存在（10 座沙箱、6 枚台账、6 枚日志、10 份用户数据、2+2 枚 exe，且真卸沙箱的安装根已归零、空手沙箱的 Programs 从未被建）；其 must_fail 校准（把安装器自报退出码 0+完成行当成已装成） judged FAIL not green (Phase 58, same payload text the executor produces; the L2 sibling check is extracted verbatim into this file so the two cannot drift apart)",
          _ec58y.get("total") == 6 and _ec58y.get("passed") == 6 and _ec58y.get("failed") == 0
          and _es.get("executed_cases") == 151 and len(_evj.get("results", [])) == 185
          # ① plan：只算不跑
          and _ePy58.get("sha_equal_recompute") is True
          and _ePy58.get("install_dir_rel") == "fake-local/Programs/造境科技"
          and _ePy58.get("uninstall_key") == "{D4C8A1F2-7B3E-4E5A-9C6D-2F1B8A47E3D5}_is1"
          and not str(_ePy58.get("uninstall_key", "")).startswith("{{")
          and _ePy58.get("gates") == ["confirmation_required", "artifact_missing",
                                     "artifact_outside_allowed_root", "build_ledger_unavailable",
                                     "artifact_sha_mismatch_vs_build_ledger",
                                     "install_dir_not_empty", "install_dir_inside_source_tree"]
          and _ePy58.get("exists_now") == [["install_root", False], ["runtime_data_root", True],
                                          ["start_menu_group", False], ["userdocs", False],
                                          ["vendor_dir", False]]
          and _ePy58.get("listing_stable") is True
          # ② gates：十七臂形形对应、代价为零
          and _eGy58.get("refused") == 17 and len(_eGy58.get("arms", [])) == 17
          and {r[0]: r[1] for r in _eGy58.get("arms", [])} == {
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
                  for r in _eGy58.get("arms", []))
          and _eGy58.get("ledger_written") is False
          # dir_created 是 any(臂) 形：十七臂里唯一安装根在场的必须是那条非空臂（它自种的目录）
          and [r[0] for r in _eGy58.get("arms", []) if r[5]] == ["install.dir_not_empty"]
          # ③ install：差集派生
          and _eIy58.get("status") == "install_verified" and _eIy58.get("checks_ok") is True
          and _eIy58.get("created_count") == _eIy58.get("created_rows") == 4
          and _eIy58.get("created_rel") == sorted(set(_eIy58.get("created_rel") or []))
          and _eIy58.get("disk_files") == _eIy58.get("created_rel")[1:3]
          and _eIy58.get("registry_display") == "ZaojingAI 1.0.0"
          and _eIy58.get("registry_observation", {}).get("hive") == "HKCU"
          and _eIy58.get("registry_observation", {}).get("uninstall_key_was_leaf") is True
          and _eIy58.get("registry_observation", {}).get("view") == "injected"
          and _eIy58.get("argv_flags") == ["/VERYSILENT", "/NORESTART", "/SUPPRESSMSGBOXES"]
          and _eIy58.get("log_bytes") == 48 and _eIy58.get("preexisting") == [1, True]
          and _eIy58.get("ignored_in_ledger") == ["install.installed", "install.status",
                                                 "install.log.seen_finished_line",
                                                 "uninstall.performed", "uninstall.status",
                                                 "build_ledger.compiled",
                                                 "build_ledger.fabricated"]
          # ④ 判分不信自报：同一份自报、两趟相反
          and _eVy58.get("self_report_identical") is True
          and _eVy58.get("exit_codes") == [0, 0] and _eVy58.get("finished_line_both") == [True, True]
          and _eVy58.get("verdicts") == ["install_verified", "install_unverified"]
          and len(_eVy58.get("ghost_ok_marks", [])) == 10
          and sum(1 for x in _eVy58.get("ghost_ok_marks", []) if x) == 3
          and _eVy58.get("ghost_created") == 0
          and _eVy58.get("ghost_state") == ["ledger_unverified", [], False]
          # ⑤ 卸载三形制 + 对账活证
          and _eUy58.get("installed_state") == ["install_verified_awaiting_uninstall", [],
                                               [True, True, True]]
          and _eUy58.get("off_book_state", [None])[0] == "ledger_inconsistent"
          and "被台账之外的手段抹掉" in json.dumps(_eUy58.get("off_book_state"), ensure_ascii=False)
          and _eUy58.get("lazy", [None])[0] == "uninstall_incomplete"
          and _eUy58.get("lazy", [None, None, [True, False, False]])[2] == [True, False, False]
          and {"install_root_still_present", "registry_app", "registry_uninstall"} <= set(
              _eUy58.get("lazy", [None, None, None, []])[3])
          and _eUy58.get("verified", [None])[0] == "uninstall_verified"
          and _eUy58.get("verified", [[]])[5:8] == [4, "created_by_install_only",
                                                   "derived_from_disk_and_registry"]
          and _eUy58.get("verified", [[]])[2:5] == [True, True, True]
          and _eUy58.get("verified", [[]])[8] == [[True, True]] * 4
          and _eUy58.get("verified", [[]])[9] == [1, True, True]
          and _eUy58.get("residue") == [["runtime_data_root", 1, False]]
          and _eUy58.get("untouched") == [] and _eUy58.get("install_dir_absent") is True
          and _eUy58.get("user_notes_hex") == "7b226d696e65223a747275657d"
          and _eUy58.get("state", [None])[0] == "executed_verified"
          and _eUy58.get("state", [[], []])[1] == [] and _eUy58.get("state", [[], [], True])[2] is False
          and _eUy58.get("state", [[], [], [], None])[3] == [0, False, False, False, True, True]
          and _eUy58.get("state", [[]] * 4 + [False, False])[4:6] == [True, True]
          and _eUy58.get("state", [[]] * 6 + [None])[6] == _eIy58.get("ignored_in_ledger")
          and _eUy58.get("recorded_only") == ["install.exit_code", "install.argv", "install.process"]
          and _eUy58.get("tamper") == ["ledger_inconsistent",
                                      ["install.artifact.sha256 != 制品在盘上的现算值"]]
          and _eUy58.get("restored") == "executed_verified"
          and _eUy58.get("none", [None])[0] == "nothing_to_uninstall"
          and _eUy58.get("none", [[]])[3] == [] and _eUy58.get("none", [[]] * 3 + [True, True])[4:6] == [True, True]
          and _eUy58.get("missing", [None])[0] == "missing_uninstaller"
          and _eUy58.get("missing", [[]] * 3 + [2])[3] == 2
          and _eUy58.get("missing", [[]] * 5 + [False])[5] is False
          # ⑥ 两轮往返：证据件逐出用户数据基线
          and _eRy58.get("r1", [None])[0] == "install_verified"
          and _eRy58.get("r1", [[]])[1] == "installer/zaojing-install.log"
          and _eRy58.get("r1", [[]])[3] == "uninstall_verified"
          and _eRy58.get("r1", [[]])[4] == 5
          and _eRy58.get("r1", [[]])[6] == "executed_verified"
          and _eRy58.get("ledger_sha_before_rewrite") != _eRy58.get("ledger_sha_after_rewrite")
          and _eRy58.get("r2_created_count") == 4 and _eRy58.get("r2_pre_count") == 1
          and [str(x).replace("\\", "/").rsplit("/_zj_eval_exec/", 1)[-1]
               for x in _eRy58.get("r2_pre_files", [])] == \
              ["round2/fake-local/Zaojing/Assets/user-notes.json"]
          and _eRy58.get("r3", [None])[0] == "uninstall_verified"
          and _eRy58.get("r3", [[]])[5] == 4 and _eRy58.get("r3", [[]])[6] == "executed_verified"
          and _eRy58.get("r3", [[]] * 7 + [{}])[8] == {"intact_claimed": True, "deviating": [],
                                                     "deviating_all_executor_owned": False}
          and _eRy58.get("r3", [[]] * 9 + [None])[9] == [False, False]
          and _eRy58.get("left_in_data_root") == ["Assets/user-notes.json",
                                                 "installer/exec-ledger.json",
                                                 "installer/zaojing-install.log"]
          and _eRy58.get("user_notes_hex") == "7b226d696e65223a747275657d"
          # 校准：信自报的假期望必须判 fail
          and _eWy58.get("passed") is False
          # 落盘实况（评测进程自己刚跑完的夹具，按引擎口径扫正确的根）
          and _xsandy58 == ["calib", "gates", "ghost", "install", "miss", "none", "plan",
                           "real", "round2", "unins"]
          and len(_xely58) == 6 and _xezy58 == 6 and len(_xegy58) == 6
          and len(_xeny58) == 10
          and _xeey58 == ["install/fake-local/Programs/造境科技/ZaojingAI.exe",
                         "real/fake-local/Programs/造境科技/ZaojingAI.exe"]
          and _xeuy58 == ["install/fake-local/Programs/造境科技/unins000.exe",
                         "real/fake-local/Programs/造境科技/unins000.exe"]
          # 真卸沙箱的安装根必须已归零；空手沙箱连 Programs 都不许存在
          and not (_xry58 / "unins" / "fake-local" / "Programs" / "造境科技").exists()
          and not (_xry58 / "ghost" / "fake-local" / "Programs").exists()
          and _xr2y58 == ["Assets/user-notes.json", "installer/exec-ledger.json",
                         "installer/zaojing-install.log"],
          json.dumps({"cat": _ec58y, "sum": {k: _es.get(k) for k in ("executed_cases", "passed", "failed")},
                      "plan": _ePy58, "gates": {k: _eGy58.get(k) for k in ("refused", "ledger_written", "dir_created")},
                      "gates_arms": {r[0]: r[1] for r in _eGy58.get("arms", [])},
                      "install": _eIy58, "verdict": _eVy58,
                      "uninstall": {k: _eUy58.get(k) for k in ("installed_state", "verified", "tamper",
                                                              "restored", "none", "missing", "state")},
                      "round2": {k: _eRy58.get(k) for k in ("r1", "r3", "ledger_sha_before_rewrite",
                                                           "ledger_sha_after_rewrite", "r2_created_count",
                                                           "left_in_data_root")},
                      "calib": _eWy58.get("passed"),
                      "disk": {"sandboxes": _xsandy58, "root": str(_xry58),
                               "ledgers": _xely58, "logs": _xegy58, "notes": len(_xeny58),
                               "exe": _xeey58, "unin_exe": _xeuy58, "round2_root": _xr2y58}},
                     ensure_ascii=False))
    _eas32 = (_evj.get("categories", {}) or {}).get("asset", {})
    _erc32 = (_evj.get("categories", {}) or {}).get("recovery", {})
    _ars32 = _eby30.get("eval-asset-search-rank", {}).get("details", {}).get("actual", {}).get("ranked", [])
    _rrs32 = _eby30.get("eval-recovery-snapshot-sha256", {}).get("details", {}).get("actual", {})
    check("EXE: eval asset/recovery categories 6+6 with exact rank/sha256 payloads and sandbox files in local Data (Phase 32)",
          _eas32.get("total") == 6 and _eas32.get("passed") == 6
          and _erc32.get("total") == 6 and _erc32.get("passed") == 6
          and _ars32 and _ars32[0][0] == "coffee_mug.png" and _ars32[0][1] == 1.0
          and _rrs32.get("checksum_matches") is True and _rrs32.get("size") == 126
          and (data_dir / "evaluation" / "artifacts" / "coffee_mug.png").read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
          and any((data_dir / "evaluation" / "_asset" / "add" / "thumbnails").glob("*.png"))
          and (data_dir / "evaluation" / "_recovery" / "task" / "states" / "task_task-77.json").exists(),
          json.dumps({"as": _eas32, "rc": _erc32}, ensure_ascii=False))
    _etc33 = (_evj.get("categories", {}) or {}).get("typography", {})
    _etm33 = _eby30.get("eval-typography-measure-serif", {}).get("details", {}).get("actual", {})
    _etg33 = _eby30.get("eval-typography-ghost-truth", {}).get("details", {}).get("actual", {})
    _etfix33 = data_dir / "evaluation" / "_typo" / "_fix"
    check("EXE: eval typography category 6/6 w/ self-supplied fixture TTFs (compiled in frozen exe) exact payloads + sandbox files in local Data (Phase 33)",
          _etc33.get("total") == 6 and _etc33.get("passed") == 6 and _etc33.get("failed") == 0
          and _etm33.get("advance_units") == 3989 and _etm33.get("width_pt") == 95.736
          and _etg33.get("ghost") is None and _etg33.get("keyerror") == "'未找到字体: ZjNoSuchFont9'"
          and (_etfix33 / "zjfix_serif.ttf").read_bytes()[:4] == b"\x00\x01\x00\x00"
          and (_etfix33 / "zjfix_sans.ttf").read_bytes()[:4] == b"\x00\x01\x00\x00"
          and (data_dir / "evaluation" / "_typo" / "measure_serif" / "fonts_index.json").exists(),
          json.dumps({"cat": _etc33, "serif": _etm33}, ensure_ascii=False))
    _ebk34 = (_evj.get("categories", {}) or {}).get("barcode", {})
    _etp34 = (_evj.get("categories", {}) or {}).get("topology", {})
    _eb13 = _eby30.get("eval-barcode-ean13-bitstring", {}).get("details", {}).get("actual", {})
    _ebnd = _eby30.get("eval-barcode-bad-checkdigit-msg", {}).get("details", {}).get("actual", {})
    _etb34 = _eby30.get("eval-topology-box9-full", {}).get("details", {}).get("actual", {})
    _etj34 = _eby30.get("eval-topology-rejected-honest", {}).get("details", {}).get("actual", {})
    _ebq2_57 = _eby30.get("eval-barcode-qr-block-structure", {}).get("details", {}).get("actual", {})
    _ebq3_57 = _eby30.get("eval-barcode-qr-pixel-truth", {}).get("details", {}).get("actual", {})
    _ebq4_57 = _eby30.get("eval-barcode-qr-mask-format-bch", {}).get("details", {}).get("actual", {})
    _ebdg_57 = _eby30.get("eval-barcode-honest-degradation", {}).get("details", {}).get("actual", {})
    _eb1d63 = _eby30.get("eval-barcode-1d-oracle-law", {}).get("details", {}).get("actual", {})
    _ebd1363 = _eby30.get("eval-barcode-1d-decode-ean13", {}).get("details", {}).get("actual", {})
    _ebd863 = _eby30.get("eval-barcode-1d-decode-ean8", {}).get("details", {}).get("actual", {})
    _ebng63 = _eby30.get("eval-barcode-1d-decode-negatives", {}).get("details", {}).get("actual", {})
    check("EXE: eval barcode category 14/14 GS1 bit-string/pixel/checkdigit exact payloads + Phase 57 QR 真值五条"
          "（手抄 ISO Table 7/8/9 容量与 40 格码字算术全等、21×21 栅格与申报 matrix 逐格同源、"
          "评测侧独立 BCH 尺子先复现 ISO 三串公布例、四条诚实边界原文），real PNG bytes in local Data "
          "（Phase 63 一维解码 oracle 真值四条：三表构造律自检 bad 归零 + 公开例 G 槽免疫关系落证、"
          "EAN-13 十位逐位扫段像素读回、EAN-8 公开例 + 十位扫段、五枚阴性逐枚点名与采样模块数全等），"
          "(Phase 34 frozen archive, +QR Phase 57)",
          _ebk34.get("total") == 14 and _ebk34.get("passed") == 14 and _ebk34.get("failed") == 0
          and _eb13.get("bits_equal") is True and _eb13.get("code") == "5901234123457"
          and _eb13.get("modules") == 95 and _eb13.get("px") == [234, 104]
          and _ebnd.get("ean13") == "EAN-13 校验位非法：给定 0，应为 7"
          and _ebnd.get("files") == [False, False]
          and _eby30.get("eval-barcode-qr-capacity-exact", {}).get("details", {}).get("actual", {}).get("bad") == []
          and _eby30.get("eval-barcode-qr-capacity-exact", {}).get("details", {}).get("expected", {})
              .get("Table8_L_cap") == [17, 32, 53, 78, 106, 134, 154, 192, 230, 271]
          and _ebq2_57.get("bad") == [] \
          and _ebq2_57.get("rows") == ["L1:19+7/1(19,7)", "M4:64+36/2(32,18)",
                                       "H6:60+112/4(15,28)",
                                       "H10:122+224/6(15,28)x2(16,28)"] \
          and _ebq3_57.get("mismatch_count") == 0 and _ebq3_57.get("quiet_band_values") == [255] \
          and _ebq3_57.get("size") == [116, 116] \
          and _ebq4_57.get("ruler_ok") is True \
          and _ebq4_57.get("ruler_reproduces") == {"L0": "111011111000100",
                                                   "M0": "101010000010010",
                                                   "v7": "000111110010010100"} \
          and len(_ebq4_57.get("cases", [])) == 5 \
          and all(c["fmt_equal"] and c["ver_match"] for c in _ebq4_57.get("cases", [])) \
          and _eby30.get("eval-barcode-qr-honest-reject", {}).get("details", {}).get("actual", {})
              .get("no_file") == {"over_m": True, "over_h": True, "empty": True, "badlvl": True}
          and _ebdg_57.get("c128", [None])[0] == "needs_external" and "Code-128" in _ebdg_57.get("c128", [None, None, None, ""])[3]
          and _ebdg_57.get("datamatrix") == ["needs_external", False] and _ebdg_57.get("qr") is None
          and (data_dir / "evaluation" / "artifacts" / "eval_p34_bc13.png").read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
          and (data_dir / "evaluation" / "artifacts" / "eval_p34_px.png").read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
          and (data_dir / "evaluation" / "artifacts" / "eval_p57_qrpx.png").read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
          and (data_dir / "evaluation" / "artifacts" / "eval_p57_cap.png").exists()
          and not (data_dir / "evaluation" / "artifacts" / "eval_p57_c128.png").exists()
          and _eb1d63.get("bad") == [] and _eb1d63.get("public_example_parity") == 'LGGLLG'
          and _eb1d63.get("public_example_G_slot_digits") == ['0', '1', '4'] and _eb1d63.get("enc13_head") == '101000101101001110110011'
          and _eb1d63.get("enc8_head") == '101000101101011110111101' and _eb1d63.get("public_example_blind_to_G7") is True
          and _ebd1363.get("bad") == [] and len(_ebd1363.get("rows", [])) == 10
          and sorted(r["d"] for r in _ebd1363.get("rows", [])) == ['0', '1', '2', '3', '4', '5', '6', '7', '8', '9']
          and [r["got"] for r in _ebd1363.get("rows", [])] == ['1030500089211', '1131511189218', '1232522289215', '1333533389212', '1434544489219', '1535555589216', '1636566689213', '1737577789210', '1838588889217', '1939599989214']
          and all(r["want"] == r["got"] for r in _ebd1363.get("rows", []))
          and _ebd863.get("bad") == [] and _ebd863.get("public_readback") == '96385074'
          and len(_ebd863.get("rows", [])) == 10 and [r["got"] for r in _ebd863.get("rows", [])] == ['03050705', '13151713', '23252721', '33353739', '43454747', '53555755', '63656763', '73757771', '83858789', '93959797']
          and all(r["want"] == r["got"] for r in _ebd863.get("rows", [])) and _ebd863.get("exercises_G_parity") is False
          and _ebng63.get("positive") == '5901234123457' and _ebng63.get("sampled") == {'ean13': 95, 'ean8_as13': 78, 'ean13_as8': 67}
          and [_ebng63.get(k) for k in ("inverted", "midguard_wiped", "tampered_m50",
              "ean8_window_as_ean13", "ean13_prefix_as_ean8")] == [None, None, None, None, None]
          and all(k in _ebng63 for k in ("inverted", "midguard_wiped", "tampered_m50",
              "ean8_window_as_ean13", "ean13_prefix_as_ean8"))
          and {'calib-barcode-1d-g7-typo-table', 'calib-barcode-1d-wrong-expect'} <= {c["id"] for c in _evj.get("calibration", [])}
          and not (data_dir / "evaluation" / "artifacts" / "eval_p57_qr_never.png").exists(),
          json.dumps({"cat": _ebk34, "ean13": _eb13, "qr_px": _ebq3_57,
                      "qr_bch": _ebq4_57.get("ruler_reproduces")}, ensure_ascii=False))
    check("EXE: eval topology category 5/5 BOX9 full-geometry/rejected-honest exact payloads in frozen archive (Phase 34)",
          _etp34.get("total") == 5 and _etp34.get("passed") == 5 and _etp34.get("failed") == 0
          and _etb34.get("relations") == 8 and _etb34.get("cut") == 870.0 and _etb34.get("fold") == 640.0
          and _etb34.get("degrees", {}).get("front") == 4
          and _etj34.get("mix", [None])[0] == "disconnected"
          and _etj34.get("empty") == "至少需要一个面板",
          json.dumps({"cat": _etp34, "box9": _etb34}, ensure_ascii=False))
    # --- Phase 57: 随包散文逐数对账（冻结归档侧，与 L2 docs(P57) 同谓词）-------------
    # 为什么 L4 也要来一遍：L2 只能证到开发态文件，用户拿到的是 ZIP 里那一份。本判据读
    # 的是「随包实物」（ZIP 条目 + portable 目录）与「本 EXE 自报」（capabilities note +
    # 自家 eval 报告 + 本归档镜像覆盖三元组），两侧逐字相等才算数对上了。
    import re as _re57x
    _p57_dom = [("品牌语义", "brand_semantics"), ("条码", "barcode"),
                ("拓扑", "topology"), ("排版", "typography"),
                ("素材", "asset"), ("恢复", "recovery"),
                ("工作流", "workflow"), ("改稿", "revision"),
                ("评审", "review"), ("安全", "security"),
                ("配置", "config"), ("路由", "model_routing"),
                ("授权", "license"), ("更新", "update"),
                ("NAS", "nas"), ("Provider", "provider"),
                ("对比", "compare"), ("去背景", "bg_removal"),
                ("面板识别", "panel_recognition"), ("安装清单", "installer_manifest"),
                # Phase 59 路线 B：三枚证据各归各账——runtime_guard 为**新题**，
                # installer_artifact_build / installer_preflight 为此前记在 installer_manifest
                # 同一个 category 下的编译面/预检面用例，现拆成独立黄金域（枚数由本归档自己的
                # live categories 现场派生，写死数字的散文一律由本判据反查）。
                ("运行时守护", "runtime_guard"), ("编译证据", "installer_artifact_build"),
                ("预检", "installer_preflight"),
                ("桌面 Hub", "desktop_hub"), ("品牌", "brand"),
                ("印前", "print"), ("知识检索", "knowledge"),
                # Phase 58：执行面转正带来第 12 例评测扩张（installer_execution 黄金域 6 条）。
                # 本清单是「live 域必须被随包散文点名」的期望集，缺一行 ⇒ 下一句 _miss57 当场判红，
                # 故新域入表必须在同一轮把《安装说明》模板里的对应句一起写下（修产品不修断言）。
                ("安装执行", "installer_execution"),
                # Phase 79（轨道 A 第一刀）：Adobe 桥三枚 golden 化（各 1 枚真值用例）。诚实边界：
                # verified=True 仅证协议机制对进程内 mock 宿主的异步握手→下发→轮询→回填→对账往返成立，
                # 绝不等于已对真实 Adobe／CorelDRAW 文档做改写。随包散文以「桥接握手 1 条」等点名。
                ("桥接握手", "bridge_handshake"), ("桥接读取", "bridge_cdr_read"),
                ("桥接写入", "bridge_cdr_write"),
                # Phase 80（轨道 A 第二刀）：服务侧持久化配置三枚 golden 化（各 1 枚真值用例），
                # 逐字下沉为第 28 引擎 service_registry 的单一真相源，端点全部委托它；随包散文
                # 以「功能开关 1 条」「工具注册表 1 条」「技能运行时 1 条」点名。
                ("功能开关", "feature_flags"), ("工具注册表", "tool_registry"),
                ("技能运行时", "skill_runtime"),
                # Phase 81（轨道 A 服务侧最后一刀）：websocket_events / delivery_package 各 1 枚
                # golden 真值用例，逐字下沉为第 29/30 引擎 event_engine / delivery_engine 的单一真相源，
                # 端点全部委托它们，评测台于私有实例／私有沙箱直接构造出货引擎驱动同一份随包代码而非另造桩。
                # 随包四份散文（本 exe 的 note／ZIP 内《安装说明》／portable/docs）均以英文域键「websocket_events 1 条」
                # 「delivery_package 1 条」点名。诚实边界：verified=True 仅证事件簿记／交付磁盘工作对出货引擎往返成立。
                ("websocket_events", "websocket_events"),
                ("delivery_package", "delivery_package")]
    _p57_legacy = {"design", "production"}

    def _p57_cnt(_tx, _ph):
        _neg = "(?!语义)" if _ph == "品牌" else ""
        _m = _re57x.search(r"(\d+) 条%s%s\s*(?:用例|真值用例)" % (_re57x.escape(_ph), _neg), _tx)
        if _m:
            return int(_m.group(1))
        _m = _re57x.search(r"%s%s(?:\s*(?:客户端|真值))?\s*(\d+) 条" % (_re57x.escape(_ph), _neg), _tx)
        return int(_m.group(1)) if _m else None

    _p57_texts = {"note(exe)": _cn47.get("evaluation_center", {}).get("note", "")}
    _zipdoc = None
    _zipdoc_bytes = None
    with zipfile.ZipFile(ZIP) as _z57:
        for _n57 in _z57.namelist():
            if _n57.startswith("docs/") and "安装说明" in _n57:
                _zipdoc_bytes = _z57.read(_n57)
                _zipdoc = _zipdoc_bytes.decode("utf-8", "replace")
    if _zipdoc:
        _p57_texts["zip:docs/安装说明.md"] = _zipdoc
    _pd57 = PORT_DIR / "docs" / "安装说明.md"
    # P57 实测教训（本机第一轮 L4 唯一红）：随包《安装说明》是 build-installer 以文本模式
    # 写下的 **CRLF** 文件（304 处），而 Path.read_text() 走 universal-newlines，会把磁盘上的
    # \r\n 静默翻译成 \n；ZIP 条目只能 bytes.decode()，\r\n 原样保留。于是「逐字节相等」这一
    # 句用 read_text 去比 decode 出来的 ZIP 文本，对任何 CRLF 产物都**恒假**——红的是仪器，
    # 不是产物（实测两侧 46,866 B 全等）。判据本身说的是 byte-identical，那就按字节比。
    _pf57_bytes = _pd57.read_bytes() if _pd57.exists() else None
    if _pd57.exists():
        _p57_texts["portable/docs"] = _pd57.read_text(encoding="utf-8", errors="replace")
    _p57_cats = _evj.get("categories", {}) or {}
    _p57_exec = _es.get("executed_cases")
    _p57_cal = len(_evj.get("calibration", []))
    _p57_cov = (len(_ns41), len(_w41), len(_ns41 - _w41))
    _p57_bad = []
    _named57 = set()
    for _nm57, _tx57 in _p57_texts.items():
        _m57 = _re57x.search(r"(\d+) 条确定性(?:黄金)?用例", _tx57)
        if not _m57 or int(_m57.group(1)) != _p57_exec:
            _p57_bad.append("%s 确定性=%s!=%s" % (_nm57, _m57 and _m57.group(1), _p57_exec))
        _m57 = _re57x.search(r"(\d+) 条负控校准", _tx57)
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
        if _nm57 != "note(exe)":
            _m57 = _re57x.search(r"当前 (\d+) 端点=已接线 (\d+)\+未接线 (\d+)", _tx57)
            _got57 = tuple(int(x) for x in _m57.groups()) if _m57 else None
            if _got57 != _p57_cov:
                _p57_bad.append("%s 覆盖句=%s!=冻结镜像 %s" % (_nm57, _got57, _p57_cov))
            for _stale in ("133 端点", "未接线 14，", "其余 14 端点保持",
                           "134 端点", "未接线 15，",
                           # Phase 65：随包散文若仍写「16 端点保持 not_wired_yet / 未接线 16，/
                           # 已接线 119+」即为上一代次副本（历史叙述里的 16 一律带箭头，形态不同）。
                           "其余 16 端点保持", "未接线 16，", "已接线 119+",
                           # Phase 70：两枚转正后，仍写「其余 10 端点保持 / 未接线 10，/ 已接线 125+」
                           # 即为上一代次副本（历史叙述「119→125」「16→10」带箭头，形态不同）。
                           "其余 10 端点保持", "未接线 10，", "已接线 125+",
                           # Phase 71：拒接面清零后，仍写「其余 8 端点保持 not_wired_yet / 未接线 8，/
                           # 已接线 127+」的散文即上一代次副本。历史叙述里的「10→8」「125→127」带箭头，
                           # 形态与本黑名单三条都不同，不会被误伤（与 L2 同条判别子）。
                           "其余 8 端点保持", "未接线 8，", "已接线 127+",
                           "语义类（Logo 含义/调性/字体识别）如实 needs_external 不臆造"):
                if _stale in _tx57:
                    _p57_bad.append("%s 陈旧串 %s" % (_nm57, _stale[:12]))
            if "QR" not in _tx57 or "Phase 57 起 QR 真值另 5 条" not in _tx57:
                _p57_bad.append("%s 无 P57 QR 句" % _nm57)
    check("EXE(P57+P58+P59+P65+P70+P71+P81): the SHIPPED prose reconciles against THIS frozen archive's own self-reports - ZIP docs/安装说明.md "
          "byte-identical to portable/docs, and both plus this exe's evaluation_center note state 151 确定性/34 负控校准 "
          "and all 36 golden-domain case counts equal to this archive's own eval categories (design/production 初代域豁免逐域计数 "
          "but every live domain must be named — Phase 58 adds 安装执行, Phase 59 adds 运行时守护/编译证据/预检 alongside "
          "their golden domains, Phase 79 adds 桥接握手/桥接读取/桥接写入, Phase 80 adds 功能开关/工具注册表/技能运行时, "
          "Phase 81 adds websocket_events/delivery_package), "
          "carry the mirrored 135=135+0 coverage sentence read from THIS onefile's openapi (Phase 65 转正六枚 119→125/16→10；"
          "Phase 70 再转正两枚 125→127/10→8；Phase 71 末批八枚转正 127→135/8→0，分母恒 135), "
          "and be free of pre-P56/P57/P58/P65/P70/P71 stale verdicts",
          _p57_bad == [] and len(_p57_texts) == 3 and _zipdoc is not None
          and _zipdoc_bytes is not None and _pf57_bytes == _zipdoc_bytes
          and _p57_exec == 151 and _p57_cal == 34 and _p57_cov == (135, 135, 0)
          and len(_named57) == 36,
          json.dumps({"bad": _p57_bad[:10], "texts": sorted(_p57_texts), "cov": _p57_cov,
                      "named": len(_named57), "bytes": [len(_zipdoc_bytes or b""),
                                                        len(_pf57_bytes or b"")],
                      "exec": _p57_exec, "cal": _p57_cal}, ensure_ascii=False))
    # --- Phase 61: 随包副本不变量（冻结归档侧：ZIP docs/ 成员 ↔ portable ↔ outputs 母本）---
    # 与 L2 同族的判据但捕面不同：既有 EXE(P57+P58+P59) 只把 **ZIP 里的《安装说明》** 与
    # portable 侧比字节，随包《审计报告》三处（母本→portable→ZIP）从未同时上过判分面。
    # 本枚把整条链钉住：ZIP docs/ 每一个成员都必须在 portable/docs 有逐字节相等的对应件，
    # 且必须在 outputs/ 有同名逐字节相等的母本。任一段漂移即判红，包括「ZIP 与 portable 相等
    # 而母本已被改过、随包没赶上」这一旧判据结构性看不见的形制（arm B3 现场证伪）。
    # 清单同样由 ZIP namelist 派生，不手抄文件名；数不出 ≥2 枚即判红。
    _p61x_bad = []
    _p61x_rows = []
    with zipfile.ZipFile(ZIP) as _z61:
        _p61x_members = sorted(n for n in _z61.namelist()
                               if n.startswith("docs/") and not n.endswith("/"))
        if len(_p61x_members) < 2:
            _p61x_bad.append("ZIP docs/ 只数出 %d 枚（<2 ⇒ 缺席不得读成绿）" % len(_p61x_members))
        for _n61 in _p61x_members:
            _b61 = _n61.rsplit("/", 1)[-1]
            _zb = _z61.read(_n61)
            _pf = PORT_DIR / "docs" / _b61
            _mf = OUT / _b61
            _pb = _pf.read_bytes() if _pf.is_file() else None
            _mb = _mf.read_bytes() if _mf.is_file() else None
            if _pb is None:
                _p61x_bad.append("ZIP %s 在 portable/docs 无对应件" % _b61)
            elif _zb != _pb:
                _p61x_bad.append("ZIP %s != portable（%d vs %d B）" % (_b61, len(_zb), len(_pb)))
            if _mb is None:
                _p61x_bad.append("ZIP %s 在 outputs/ 无同名母本" % _b61)
            elif _zb != _mb:
                _p61x_bad.append("ZIP %s != 母本（%d vs %d B）" % (_b61, len(_zb), len(_mb)))
            _p61x_rows.append([_b61, len(_zb), len(_pb) if _pb is not None else None,
                               len(_mb) if _mb is not None else None])
    check("EXE(P61): every docs/ member of the frozen ZIP is byte-identical to BOTH the on-disk portable copy and the "
          "outputs/ master - 随包《审计报告》与《安装说明》三处同名同字节，母本→portable→ZIP 任一段漂移即判红（含旧判据看不见的"
          "「ZIP 与 portable 一致而母本已漂」形制）；成员清单由 ZIP namelist 派生，数不出 ≥2 枚即判红",
          _p61x_bad == [] and len(_p61x_members) >= 2,
          json.dumps({"bad": _p61x_bad[:8], "members": _p61x_members, "sizes": _p61x_rows},
                     ensure_ascii=False))
    _elc36 = (_evj.get("categories", {}) or {}).get("license", {})
    _elf36 = _eby30.get("eval-license-tamper-format-exact", {}).get("details", {}).get("actual", {})
    check("EXE: eval license category 4/4 exact HMAC tamper/format/expiry/bind verdict strings in frozen archive (Phase 36)",
          _elc36.get("total") == 4 and _elc36.get("passed") == 4 and _elc36.get("failed") == 0
          and _elf36.get("tamper", [None, ""])[1] == "签名校验失败：密钥被篡改或非本产品签发"
          and _elf36.get("array_payload") == [False, "载荷结构无效：签名载荷必须是 JSON 对象"],
          json.dumps({"cat": _elc36, "tamper": _elf36}, ensure_ascii=False))
    _ecc36 = (_evj.get("categories", {}) or {}).get("config", {})
    _ecm36 = _eby30.get("eval-config-migration", {}).get("details", {}).get("actual", {})
    check("EXE: eval config category 4/4 defaults/migration payloads with sandbox JSON written inside local Data (Phase 36 frozen archive)",
          _ecc36.get("total") == 4 and _ecc36.get("passed") == 4 and _ecc36.get("failed") == 0
          and _ecm36.get("marker") == "marker-kept" and _ecm36.get("saved_version") == "1.0.0"
          and "zj_probe_key" in (data_dir / "evaluation" / "_config" / "migrate" / "user-settings.json").read_text(encoding="utf-8"),
          json.dumps({"cat": _ecc36, "mig": _ecm36}, ensure_ascii=False))
    _ert36 = (_evj.get("categories", {}) or {}).get("model_routing", {})
    _ertc36 = _eby30.get("eval-routing-cost-tie", {}).get("details", {}).get("actual", {})
    _ertb36 = _eby30.get("eval-routing-budget-breaker", {}).get("details", {}).get("actual", {})
    check("EXE: eval model_routing category 4/4 cost-tie/budget/breaker payloads with fixture YAML in local Data (Phase 36 frozen archive)",
          _ert36.get("total") == 4 and _ert36.get("passed") == 4 and _ert36.get("failed") == 0
          and _ertc36.get("picked") == "p_local" and _ertc36.get("est") == (0.002 + 0.006) / 2
          and _ertb36.get("all_over") is None and _ertb36.get("privacy_after_open") == "p_cloud"
          # Phase 60：本枚修前只读 all_over/privacy_after_open，从不读 breaker_exc——于是
          # "熔断开环依赖事件循环"这枚真缺陷在冻结 EXE 面上没有判卷腿（该非对称已在
          # p60_probe8_rootcause.txt 登记）。今日产品侧根治后，同一份载荷的四个新字段
          # 在冻结归档内同样落证，判卷腿补齐（期望值来自同轮源码实测收据
          # p60_probe12_evalpayload.txt；本枚在重打包前必红，正是"冻结件里还是旧引擎"的证据）。
          and _ertb36.get("breaker_exc") is None and _ertb36.get("rearm_exc") is None
          and _ertb36.get("opened_after_failures") is True
          and _ertb36.get("rearm_without_loop") is True
          and _ertb36.get("inloop_can_execute") is False
          and (data_dir / "evaluation" / "_router" / "cost" / "registry.yaml").is_file(),
          json.dumps({"cat": _ert36, "cost": _ertc36}, ensure_ascii=False))
    _uc37 = (_evj.get("categories", {}) or {}).get("update", {})
    _udl37 = _eby30.get("eval-update-download-apply", {}).get("details", {}).get("actual", {})
    _ubm37 = _eby30.get("eval-update-branch-matrix", {}).get("details", {}).get("actual", {})
    _u_fx37 = data_dir / "evaluation" / "_update"
    check("EXE: eval update category 4/4 honest-gates/HMAC-recompute/real-HTTP-chain/branch-order payloads, applied artifact bytes in local Data (Phase 37 frozen archive)",
          _uc37.get("total") == 4 and _uc37.get("passed") == 4 and _uc37.get("failed") == 0
          and _udl37.get("dl") == ["downloaded", "sha256_match", 26]
          and _udl37.get("seen") == ["/manifest.json", "/manifest.json", "/art.bin"]
          and _udl37.get("check") == ["update_available", True, ["1.0.0", "2.0.0"]]
          and _ubm37.get("version_gate_first") == "manifest_invalid"
          and _ubm37.get("no_secret") == ["signature_unverified", False, "2.0.0"]
          and (_u_fx37 / "chain" / "update-config.json").exists()
          and (_u_fx37 / "chain_fx" / "app.bin").read_bytes() == b"ZAOJING-P37-ARTIFACT-BYTES",
          json.dumps({"cat": _uc37, "chain": _udl37}, ensure_ascii=False))
    _nc37 = (_evj.get("categories", {}) or {}).get("nas", {})
    _nnc37 = _eby30.get("eval-nas-cache-cold", {}).get("details", {}).get("actual", {})
    _ngd37 = _eby30.get("eval-nas-gate-matrix", {}).get("details", {}).get("actual", {})
    _npg37 = _eby30.get("eval-nas-path-guard", {}).get("details", {}).get("actual", {})
    check("EXE: eval nas category 4/4 gate-originals/list-truth/path-guard/cold-cache-hit payloads, download bytes in local Data (Phase 37 frozen archive)",
          _nc37.get("total") == 4 and _nc37.get("passed") == 4 and _nc37.get("failed") == 0
          and _ngd37.get("bad_kind") == "未知 NAS 类型 ftp（可用: filesystem, webdav）"
          and _ngd37.get("blank_target") == "target 不能为空"
          and _npg37.get("traversal") == ["forbidden", True] and _npg37.get("leaked") is False
          and _nnc37.get("cache") == "hit" and _nnc37.get("quads_equal") is True
          and (data_dir / "evaluation" / "_nas" / "cache" / "dl" / "a.txt").read_bytes() == b"12345",
          json.dumps({"cat": _nc37, "cache": _nnc37}, ensure_ascii=False))
    _pc37 = (_evj.get("categories", {}) or {}).get("provider", {})
    _pch37 = _eby30.get("eval-provider-chat-roundtrip", {}).get("details", {}).get("actual", {})
    _pbr37 = _eby30.get("eval-provider-auth-breaker", {}).get("details", {}).get("actual", {})
    _pkey37 = _eby30.get("eval-provider-key-dpapi", {}).get("details", {}).get("actual", {})
    _ppath37 = data_dir / "evaluation" / "_provider"
    check("EXE: eval provider category 4/4 unconfigured-honest/DPAPI-mask/real-chat-fields/breaker-zero-HTTP payloads, key store in local Data (Phase 37 frozen archive)",
          _pc37.get("total") == 4 and _pc37.get("passed") == 4 and _pc37.get("failed") == 0
          and _pch37.get("status_content") == ["ok", "收到：造境评测 P37"]
          and _pch37.get("usage") == [20, 7, 13] and _pch37.get("auth") == "Bearer sk-eval-p37-secret"
          and _pbr37.get("auth") == ["error", "auth_failed", 1]
          and _pbr37.get("breaker") == ["error", "circuit_open", 0]
          and _pkey37.get("mask_literal") == "sk-" + "*" * 11 + "cret"
          and "sk-eval-p37-secret" not in (_ppath37 / "keys" / "providers.json").read_text(encoding="utf-8")
          and "api_key_dpapi" in (_ppath37 / "keys" / "providers.json").read_text(encoding="utf-8"),
          json.dumps({"cat": _pc37, "chat": _pch37}, ensure_ascii=False))
    _cc38 = (_evj.get("categories", {}) or {}).get("compare", {})
    _ccf38 = _eby30.get("eval-compare-field-categories", {}).get("details", {}).get("actual", {})
    _bgc38 = (_evj.get("categories", {}) or {}).get("bg_removal", {})
    _bgf38 = _eby30.get("eval-bgre-flood-exact", {}).get("details", {}).get("actual", {})
    check("EXE: eval compare category 4/4 six-category typemap payloads from newly extracted pure engine in frozen archive (Phase 38)",
          _cc38.get("total") == 4 and _cc38.get("passed") == 4 and _cc38.get("failed") == 0
          and _ccf38.get("summary", {}).get("by_category")
              == {"geometry": 2, "text": 1, "color": 1, "asset": 1, "layer": 1, "visual": 1},
          json.dumps({"cat": _cc38, "fields": _ccf38}, ensure_ascii=False))
    check("EXE: eval bg_removal category 4/4 flood-count/hole-truth payloads, real PNG fixture+output in local Data (Phase 38 frozen archive)",
          _bgc38.get("total") == 4 and _bgc38.get("passed") == 4 and _bgc38.get("failed") == 0
          and _bgf38.get("cleared") == 8400 and _bgf38.get("ratio") == 0.84
          and _bgf38.get("alphas") == [0, 255, 255]
          and (data_dir / "evaluation" / "_bg" / "_fix" / "subject.png").read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
          and (data_dir / "evaluation" / "_bg" / "flood" / "no_bg.png").read_bytes()[:8] == b"\x89PNG\r\n\x1a\n",
          json.dumps({"cat": _bgc38, "flood": _bgf38}, ensure_ascii=False))
    _pc47 = (_evj.get("categories", {}) or {}).get("panel_recognition", {})
    _pg47 = _eby30.get("eval-panel-grid-inversion", {}).get("details", {}).get("actual", {})
    _pi47 = _eby30.get("eval-panel-determinism", {}).get("details", {}).get("actual", {})
    _pw47 = _eby30.get("calib-panel-wrong-expect", {}).get("details", {}).get("actual", {})
    check("EXE: eval panel_recognition category 4/4 in frozen archive - 3x2 dashed grid inverts to 6 panels of 2535px/7 adjacencies/degree seq [3,3,2,2,2,2]/perimeter 1248, determinism equal, its must_fail calibration judged FAIL not green, self-drawn fixture PNG persisted under local Data (Phase 47)",
          _pc47.get("total") == 4 and _pc47.get("passed") == 4 and _pc47.get("failed") == 0
          and _pg47.get("areas") == [2535] * 6
          and _pg47.get("adj") == [7, [3, 3, 2, 2, 2, 2], 1]
          and _pg47.get("outline") == [1248, 15210, 234, 390]
          and _pi47.get("equal") is True
          and _eby30.get("calib-panel-wrong-expect", {}).get("passed") is False
          and [q[1] for q in _pw47] == ["fold", "fold", "fold"]
          and (data_dir / "evaluation" / "_panel" / "grid" / "grid3x2.png").read_bytes()[:8] == b"\x89PNG\r\n\x1a\n",
          json.dumps({"cat": _pc47, "areas": _pg47.get("areas")}, ensure_ascii=False))
    # Phase 49: 第 26 引擎的 installer_manifest 黄金域确已编入冻结归档
    _mc49x = (_evj.get("categories", {}) or {}).get("installer_manifest", {})
    _mA49x = _eby30.get("eval-manifest-clean-zero-findings", {}).get("details", {}).get("actual", {})
    _mB49x = _eby30.get("eval-manifest-broken-findings-exact", {}).get("details", {}).get("actual", {})
    _mC49x = _eby30.get("eval-manifest-seam-stub-falsification", {}).get("details", {}).get("actual", {})
    # Phase 59 路线 B：编译面三条与预检面两条**改归**各自域（不新增用例、不改判分内容，
    # 只把证据记回它真正证明的能力）。故 installer_manifest 域名下只剩 6 条。
    _mb59x = (_evj.get("categories", {}) or {}).get("installer_artifact_build", {})
    _mp59x = (_evj.get("categories", {}) or {}).get("installer_preflight", {})
    _mD49x = _eby30.get("eval-manifest-rejects-and-installed", {}).get("details", {}).get("actual", {})
    _mW49x = _eby30.get("calib-manifest-wrong-expect", {}).get("details", {}).get("actual", {})
    _msd49x = data_dir / "evaluation" / "_manifest"
    check("EXE: eval installer_manifest golden domain 6/6 shipped in the frozen archive - clean zero-findings, broken 12-findings exact id/severity list, seam-stub falsification, rejects + installed-derivation invariant, eval-self-drawn .iss fixtures with real payloads under local Data\\evaluation\\_manifest, must_fail calibration judged FAIL not green (Phase 49 4 cases, grown to 10 by Phase 53 编译面/预检面 arms and to 11 by Phase 58 R14 卸载撤销七臂; **Phase 59 起**编译面三条与预检面两条不再算进本域名下，改由 installer_artifact_build 3 条 / installer_preflight 2 条各自记账——同一批用例、同一份判分内容，只是把证据记回它真正证明的能力; counts and fixture shapes re-pinned by p58_fixture_count_probe on THIS payload family)",
          _mc49x.get("total") == 6 and _mc49x.get("passed") == 6 and _mc49x.get("failed") == 0
          and _mb59x.get("total") == 3 and _mb59x.get("passed") == 3 and _mb59x.get("failed") == 0
          and _mp59x.get("total") == 2 and _mp59x.get("passed") == 2 and _mp59x.get("failed") == 0
          and _mA49x.get("verdict") == "manifest_ok"
          and _mA49x.get("counts") == {"errors": 0, "infos": 0, "total": 0, "warnings": 0}
          # Phase 58 漂移（与 L2 同源同量：p58_l2_freeze_out + p58_fixture_count_out 两次独立
          # 进程内/HTTP 复算一致）：合规夹具 [Registry] 段为 R14 三条合规臂补到 3 条。
          and _mA49x.get("entries") == {"Files": 2, "Icons": 2, "Registry": 3, "Setup": 7}
          and _mA49x.get("files_lines") == [12, 13]
          and _mA49x.get("flags") == "ignoreversion onlyifdoesntexist"
          and _mA49x.get("listing_stable") is True
          and _mB49x.get("verdict") == "manifest_invalid"
          and _mB49x.get("counts") == {"errors": 8, "infos": 1, "total": 12, "warnings": 3}
          and _mB49x.get("noroot_counts") == {"errors": 8, "infos": 1, "total": 11, "warnings": 2}
          and _mB49x.get("ids") == ["appid_not_uuid", "dirs_miss_runtime_data_root",
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
          and _mB49x.get("severities") == ["warn", "warn", "error", "error", "info", "warn",
                                           "error", "error", "error", "error", "error", "error"]
          and list(_mC49x.get("envdir_pair", [])) == [0, [["dirs_miss_runtime_data_root", "warn"]]]
          # installed 只能由注册表探针真命中派生：沙箱里放了真 portable.flag 也不翻 installed
          and _mD49x.get("installed") is _mD49x.get("derived_from_probes") is False
          and _mD49x.get("flag_exists") is True and _mD49x.get("sha_equal") is True
          and _eby30.get("calib-manifest-wrong-expect", {}).get("passed") is False
          and _mW49x == [["dirs_miss_runtime_data_root", "warn"]]
          and (_msd49x / "clean" / "zaojing.exe").read_bytes() == b"MZ\x90\x00fixture"
          # Phase 59 路线 B 漂移同族（与 L2 同源同量）：新校准 calib-manifest-preflight-wrong-expect
          # 经 _manifest_build_sandbox("pfc") 多种一座沙箱（1 枚带 MZ 体的 zaojing.exe），
          # 冻结 EXE 内 eval 实测 outputs/portable/Data/evaluation/_manifest 下 17/16——
          # 与 P53/P58 同课：尺子随产品重测，不是把产品改回旧计数。
          and len(list(_msd49x.rglob("zaojing.exe"))) == 17,
          json.dumps({"cat": _mc49x, "clean": _mA49x.get("counts"), "broken": _mB49x.get("counts"),
                      "rej": {k: _mD49x.get(k) for k in ("sha_equal", "installed",
                                                        "derived_from_probes", "flag_exists")}},
                     ensure_ascii=False))
    # --- Phase 58：R14 卸载撤销七臂确已随包冻结（与 L2 同判据、不同进程与不同数据根）---
    # 判据出处是本机**真卸载实测**（卸载器 exit 0、安装根与卸载子键消失、四棵数据树归零而
    # 应用键三值原样留守）。这条规则唯一会静默退化的地方是"写了任何旗标都算撤销"，故七臂里
    # 两枚带 uninsdeletekeyifempty 的开火臂与三枚合规臂必须同时成立，缺任何一枚都判红。
    _mK58x = _eby30.get("eval-manifest-registry-uninstall-arms", {}).get("details", {}).get("actual", {})
    _m058x = {"errors": 0, "warnings": 0, "infos": 0, "total": 0}
    _m158x = {"errors": 1, "warnings": 0, "infos": 0, "total": 1}
    _mR58x = "registry_key_left_after_uninstall"
    _m_norm58x = lambda _k: ((_v := _mK58x.get(_k)) and
                             (_v[0], _v[1], [[_f[0], _f[1], list(_f[2]), list(_f[3])]
                                             for _f in _v[2]]))
    _m_exp58x = {
        "residue": ("manifest_invalid", _m158x, [[_mR58x, "error", [8, 9], []]]),
        "key": ("manifest_ok", _m058x, []),
        "parent_delkey": ("manifest_ok", _m058x, []),
        "parent_ifempty": ("manifest_invalid", _m158x,
                           [[_mR58x, "error", [9], ["uninsdeletekeyifempty"]]]),
        "ifempty_same_key": ("manifest_invalid", _m158x,
                             [[_mR58x, "error", [9], ["uninsdeletekeyifempty"]]]),
        "never": ("manifest_ok", _m058x, []),
        "cross_root": ("manifest_invalid", _m158x, [[_mR58x, "error", [9], []]]),
    }
    check("EXE: R14 registry-uninstall-arms truly evaluated on seven eval-self-drawn .iss fixtures in the frozen archive - 三臂零检出（同键 uninsdeletekey／祖先键 uninsdeletekey／uninsneveruninstall 属主动弃权而非残留）、三臂开火且如实回显已观测到的撤销旗标（裸写两行 [8,9]、uninsdeletekeyifempty 挂本键与挂祖先键照旧开火、HKCU 写而 HKLM 撤的跨根照旧开火），每臂 counts.total 恒等于 findings 长度（Phase 58）",
          set(_mK58x) == set(_m_exp58x)
          and all(_m_norm58x(_k) == _m_exp58x[_k] for _k in _m_exp58x)
          and all(_mK58x[_k][1]["total"] == len(_mK58x[_k][2]) for _k in _mK58x)
          and _mK58x["residue"][1]["errors"] == 1 and _mK58x["residue"][2][0][3] == []
          and _mK58x["parent_ifempty"][2][0][3] == ["uninsdeletekeyifempty"]
          and _mK58x["ifempty_same_key"][2][0][3] == ["uninsdeletekeyifempty"]
          and _mK58x["cross_root"][2][0][3] == []
          and _mK58x["key"][0] == _mK58x["parent_delkey"][0] == _mK58x["never"][0] == "manifest_ok",
          json.dumps({"arms": {k: _m_norm58x(k) for k in sorted(_mK58x)},
                      "expected": _m_exp58x}, ensure_ascii=False))
    # Phase 53：编译面（verify_build_evidence）与预检面（preflight_install）的真值臂确已随包冻结。
    # 逐字段值取自 p53_l4_freeze3 在本冻结归档上的实测（不是 L2 源码跑的转抄），故本轮判据与
    # 源码跑互为独立两路：评测台在 EXE 内自建 _manifest 沙箱、真算真判。
    _mBv53 = _eby30.get("eval-manifest-build-evidence-verified", {}).get("details", {}).get("actual", {})
    _mBa53 = _eby30.get("eval-manifest-build-evidence-arms", {}).get("details", {}).get("actual", {})
    _mWc53 = _eby30.get("eval-manifest-build-warnings-completeness", {}).get("details", {}).get("actual", {})
    _mPr53 = _eby30.get("eval-manifest-preflight-ready-readonly", {}).get("details", {}).get("actual", {})
    _mPb53 = _eby30.get("eval-manifest-preflight-blocked-stubs", {}).get("details", {}).get("actual", {})
    _mSf53 = _eby30.get("eval-manifest-static-fire-arms", {}).get("details", {}).get("actual", {})
    _mCw53 = _eby30.get("calib-manifest-build-wrong-expect", {})
    check("EXE(P53): eval installer_manifest 编译面/预检面 arms shipped in the frozen archive with the measured payloads - "
          "真证据全字段对账 artifact_verified（line_source=tail、四字段 matches 全真、复算等值），四种伪造各被指名拦下 "
          "(equal_len→artifact_sha256_mismatch / stale→artifact_not_from_current_script / gone→artifact_missing / "
          "not_proven→compile_not_proven)，iscc 告警完备性四臂只有『记录数=尾部真数』才一致（不足与超出都报 "
          "warnings_count_mismatch，尾部只是摘录时超额不判红），预检三态在只读仪器下 ready/blocked/unknown 各自成立"
          "（16 臂逐臂点名），R6 转义律静态开火臂（单花括号 appid_guid_unescaped 必 error、{{}} 合规不报），"
          "且 must_fail 校准 calib-manifest-build-wrong-expect 判 FAIL 不判绿（self_report=[假,真,真]）",
          _mBv53.get("verdict") == "artifact_verified" and _mBv53.get("line_source") == "tail"
          and _mBv53.get("matches") == [True, True, True, True]
          and _mBv53.get("listing_stable") is True and _mBv53.get("codes") == []
          and _mBv53.get("lie_verdict") == "artifact_verified" and _mBv53.get("lie_rest_equal") is True
          and list(_mBv53.get("warn", [])) == [0, 0, True]
          and _mBa53.get("equal_len", [None])[0] == "evidence_mismatch"
          and _mBa53["equal_len"][1] == ["artifact_sha256_mismatch"]
          and _mBa53.get("stale", [None])[0] == "evidence_mismatch"
          and _mBa53["stale"][1] == ["artifact_not_from_current_script"]
          and _mBa53.get("gone", [None])[0] == "artifact_missing"
          and _mBa53["gone"][2] is False and _mBa53["gone"][3] == "artifact_missing"
          and _mBa53.get("not_proven", [None])[0] == "compile_not_proven"
          and _mBa53["not_proven"][2] == "absent" and _mBa53["not_proven"][4] == "manifest_ok"
          and _mWc53.get("arms", {}).get("eq_full", {}).get("consistent") is True
          and _mWc53["arms"]["eq_full"].get("verdict") == "artifact_verified"
          and _mWc53["arms"].get("more_full", {}).get("consistent") is False
          and _mWc53["arms"]["more_full"].get("codes") == ["warnings_count_mismatch"]
          and _mWc53["arms"].get("less_excerpt", {}).get("codes") == ["warnings_count_mismatch"]
          and _mWc53["arms"].get("more_excerpt", {}).get("consistent") is True
          and _mWc53["arms"]["more_excerpt"].get("verdict") == "artifact_verified"
          and _mWc53.get("incomplete", [None])[0] == "evidence_incomplete"
          and _mWc53["incomplete"][1] == ["script_sha256", "artifact_bytes",
                                          "iscc_output_tail", "iscc_stderr_tail"]
          and list(_mWc53.get("derived", [])) == ["derived_field", "artifact_verified"]
          and _mPr53.get("verdict") == "preflight_ready"
          and sorted(_mPr53.get("check_results", {})) == ["artifact", "disk_space", "install_root",
                                                          "payload", "privileges", "target_conflict",
                                                          "writability"]
          and all(_mPr53.get("check_results", {}).values())
          and list(_mPr53.get("write", [])) == ["real_probe_file", 23, True, True]
          and _mPr53.get("target_left") is False and _mPr53.get("listing_stable") is True
          and list(_mPr53.get("forms", [])) == ["commonpf", "userpf"]
          and list(_mPr53.get("nover", [])) == ["preflight_unknown", ["write_probe_unprobed"], False]
          and _mPb53.get("base", {}).get("verdict") == "preflight_ready"
          and _mPb53.get("restore", {}).get("verdict") == "preflight_ready"
          and _mPb53.get("exe_held32", {}).get("blocking") == ["target_exe_in_use"]
          and _mPb53.get("file_target", {}).get("blocking") == ["target_exists_as_file"]
          and _mPb53.get("free_tiny", {}).get("blocking") == ["space_insufficient"]
          and _mPb53.get("miss_payload", {}).get("blocking") == ["payload_source_missing"]
          and _mPb53.get("no_evidence", {}).get("blocking") == ["artifact_missing"]
          and _mPb53.get("tampered", {}).get("blocking") == ["artifact_not_verified"]
          and _mPb53.get("probe_write", {}).get("blocking") == ["target_not_writable"]
          and _mPb53.get("probe_mkdir", {}).get("blocking") == ["cannot_create_target"]
          and _mPb53.get("admin_elev_false", {}).get("blocking") == ["privileges_insufficient"]
          and _mPb53.get("miss_payload_beats_unknown", {}).get("verdict") == "preflight_blocked"
          and _mPb53["miss_payload_beats_unknown"].get("unknown") == ["write_probe_unprobed"]
          and _mPb53.get("admin_unresolved", {}).get("verdict") == "preflight_unknown"
          and _mPb53["admin_unresolved"].get("unknown") == ["elevation_state_unresolved"]
          and _mPb53.get("free_none", {}).get("unknown") == ["disk_free_unresolved"]
          and _mPb53.get("probe_none", {}).get("unknown") == ["write_probe_unresolved"]
          and _mSf53.get("r6", [None])[0] == "manifest_invalid"
          and _mSf53["r6"][2] == [["appid_guid_unescaped", "error", 4]]
          and _mSf53.get("r12", [None])[0] == "manifest_warning"
          and [i[0] for i in _mSf53["r12"][2]] == ["arch_identifier_deprecated"] * 2
          and _mSf53.get("r11fire", [None])[2] == [["per_user_area_privileges_mismatch", "warn", 0]]
          and _mSf53.get("r13no", [None])[2] == [["setup_missing_version_directive", "error", None]]
          and _mSf53.get("r13vn", [None])[0] == "manifest_ok"
          and _mSf53.get("clean", [None])[0] == "manifest_ok"
          and _mCw53.get("passed") is False
          and _mCw53.get("details", {}).get("actual", {}).get("verdict") == "artifact_verified"
          and list(_mCw53.get("details", {}).get("actual", {}).get("self_report", []))
              == [False, True, True],
          json.dumps({"bv": _mBv53.get("verdict"), "arms": {k: v[0] for k, v in _mBa53.items()
                                                            if isinstance(v, list)},
                    "pr": _mPr53.get("verdict"), "calib": _mCw53.get("passed")},
                     ensure_ascii=False))
    # Phase 59 路线 B：第 22 引擎的**守护面**作为独立黄金域 runtime_guard 随包冻结，评测台在这台
    # EXE 内自起沙箱/自起监听桩/自起同实例与另一实例的句柄表，真算真判。期望逐字段与 L2 同名同值
    # 不是"转抄源码跑的结论"——引擎字节确已随包（上面 EXE/PYZ 两枚 toc 判据背书），归档内若跑出
    # 不同值，红的是「随包引擎与源码引擎不一致」这一件真事，正是本层独有的捕面。
    _gc59x = (_evj.get("categories", {}) or {}).get("runtime_guard", {})
    _gA59x = _eby30.get("eval-guard-write-probe-three-arms", {}).get("details", {}).get("actual", {})
    _gB59x = _eby30.get("eval-guard-disk-threshold-two-sided", {}).get("details", {}).get("actual", {})
    _gC59x = _eby30.get("eval-guard-port-bind-probe-two-sided", {}).get("details", {}).get("actual", {})
    _gD59x = _eby30.get("eval-guard-single-instance-mutex-chain", {}).get("details", {}).get("actual", {})
    _gE59x = _eby30.get("eval-guard-diagnostic-derived-and-degrades", {}).get("details", {}).get("actual", {})
    _gW59x = _eby30.get("calib-guard-wrong-expect", {})
    check("EXE(P59): runtime_guard golden domain 5/5 shipped in the frozen archive - 写探针三臂（可写臂三真且事后探针文件计数=0、目标为普通文件与父路径为普通文件两臂三假且 error 起手 FileExistsError，两侧同写）、磁盘阈值两侧（min=0 必 meets、2**62 必不 meets、负阈值抛 RuntimeGuardError，连续量不入判分）、端口 bind 两侧（自起监听桩的端口不可用且 detail 为 OS 错误、bind(0) 后自关的端口可用且 detail=None、越界端口抛点名错误，绝不触碰外部 socket）、单实例互斥五步链（同实例 [True,False]→[False,True]→release→再 [True,False]，**另一实例**二次 [False,True] 且它的 released=False）、诊断派生与降级（[performed,passed,failed]=[3,3,0]、status=healthy、timestamp 刻意为 None、带端口臂含 port_available、降级臂 data_root_writable=fail 且 status=degraded 且 failed==performed-passed 恒为派生式、以不存在的目录建引擎时 dir_exists_before_vs_after=[False,True] 且 fresh_first_snapshot=False（判别对：引擎构造不建根、由写探针真实 mkdir 派生）、沙箱残留只有 .runtime-guard 目录）；其 must_fail 校准 judged FAIL not green",
          _gc59x.get("total") == 5 and _gc59x.get("passed") == 5 and _gc59x.get("failed") == 0
          and _gA59x.get("writable_dir") == {"writable": True, "bytes_verified": True,
                                             "cleaned": True, "method": "real_write_readback_delete",
                                             "fabricated": False}
          and _gA59x.get("target_is_file") == {"writable": False, "bytes_verified": False,
                                               "cleaned": False}
          and _gA59x.get("parent_is_file") == {"writable": False, "bytes_verified": False,
                                               "cleaned": False}
          and _gA59x.get("target_err_type") == "FileExistsError"
          and _gA59x.get("parent_err_type") == "FileExistsError"
          and _gA59x.get("residue_probe_file_count") == 0 and _gA59x.get("dir_created") is True
          and _gA59x.get("target_norm") == "<SB>/probe-ok"
          and _gB59x.get("min_zero_meets") is True and _gB59x.get("min_huge_meets") is False
          and _gB59x.get("total_positive") is True
          and _gB59x.get("method") == "shutil_disk_usage" and _gB59x.get("fabricated") is False
          and _gB59x.get("negative_raises") == "RuntimeGuardError|min_free_bytes 不能为负"
          and _gB59x.get("min_echo") == [0.0, float(2 ** 62)]
          and _gC59x.get("free_available") is True and _gC59x.get("occ_available") is False
          and _gC59x.get("free_detail_none") is True and _gC59x.get("occ_detail_is_oserror") is True
          and _gC59x.get("method") == "real_tcp_bind_probe" and _gC59x.get("fabricated") is False
          and _gC59x.get("out_of_range_raises") == "RuntimeGuardError|port 需为 1~65535 的整数"
          and _gD59x.get("first") == [True, False]
          and _gD59x.get("second_same_instance") == [False, True]
          and _gD59x.get("released") is True
          and _gD59x.get("after_release_same_instance") == [True, False]
          and _gD59x.get("after_release_other_instance") == [False, True]
          and _gD59x.get("other_release") is False
          and _gD59x.get("backend") == "kernel32-named-mutex"
          and _gD59x.get("fabricated") is False and _gD59x.get("name_echo") == "<LOCK>"
          and _gD59x.get("bad_name_rejected") == "RuntimeGuardError|instance_name 含路径/保留字符，拒绝"
          and _gE59x.get("checks") == [["data_root_writable", "pass"], ["disk_free", "pass"],
                                       ["python_version", "pass"]]
          and _gE59x.get("counts") == [3, 3, 0] and _gE59x.get("status") == "healthy"
          and _gE59x.get("method") == "derived_from_real_checks"
          and _gE59x.get("fabricated") is False and _gE59x.get("timestamp_none") is True
          and _gE59x.get("with_port_checks") == [["data_root_writable", "pass"], ["disk_free", "pass"],
                                                 ["python_version", "pass"], ["port_available", "pass"]]
          and _gE59x.get("with_port_count") == 4
          and _gE59x.get("degraded_checks") == [["data_root_writable", "fail"], ["disk_free", "pass"],
                                                ["python_version", "pass"]]
          and _gE59x.get("degraded_status") == "degraded"
          and _gE59x.get("failed_eq_derived") is True
          and _gE59x.get("dir_exists_first_vs_second") == [True, True]
          and _gE59x.get("dir_exists_before_vs_after") == [False, True]
          and _gE59x.get("fresh_first_snapshot") is False
          and _gE59x.get("residue") == [".runtime-guard"]
          and _gE59x.get("residue_probe_files") == 0
          and _gW59x.get("passed") is False and _gW59x.get("must_fail") is True,
          json.dumps({"cat": _gc59x, "write": _gA59x, "disk": _gB59x, "port": _gC59x,
                      "mutex": _gD59x,
                      "diag": {k: _gE59x.get(k) for k in ("checks", "counts", "status",
                                                          "degraded_status",
                                                          "dir_exists_before_vs_after",
                                                          "fresh_first_snapshot", "residue")},
                      "calib": _gW59x.get("passed")}, ensure_ascii=False))
    # Phase 50: 第 27 引擎 desktop_hub 的黄金域确已编入冻结归档（数据层+呈现层，进程内真 HTTP 夹具；
    # 窗口本体的活体判据在下面 hub-cli 分支，评测台从不弹窗）。
    _hc50x = (_evj.get("categories", {}) or {}).get("desktop_hub", {})
    _hA50x = _eby30.get("eval-hub-transport-fidelity", {}).get("details", {}).get("actual", {})
    _hB50x = _eby30.get("eval-hub-derive-not-invent", {}).get("details", {}).get("actual", {})
    _hC50x = _eby30.get("eval-hub-degradation-three-forms", {}).get("details", {}).get("actual", {})
    _hD50x = _eby30.get("eval-hub-action-single-post", {}).get("details", {}).get("actual", {})
    check("EXE: eval desktop_hub golden domain 4/4 shipped in the frozen archive - three read GETs in exact order with 0 POST and the fixture's own version string carried verbatim into the window row, wrong-shape health leaves identity fields None instead of writing a 'vNone' row, degradation splits into URLError/not_zaojing_service/JSONDecodeError three forms, and exactly one body-'{}' POST channel exists (unknown action raises, adds no request); its must_fail calibration judged FAIL not green (Phase 50)",
          _hc50x.get("total") == 4 and _hc50x.get("passed") == 4 and _hc50x.get("failed") == 0
          and [s.get("path") for s in _hA50x.get("seen", [])] == ["/api/v1/health",
                                                                  "/api/v1/capabilities",
                                                                  "/api/v1/eval/reports"]
          and _hA50x.get("post_count") == 0 and _hA50x.get("read_only") is True
          and _hA50x.get("fabricated") is False and _hA50x.get("all_sources_live") is True
          and _hA50x.get("picked_by") == "max(finished_at)"
          and _hA50x.get("evaluation_latest", {}).get("report_id") == "eval-new"
          and _hA50x.get("rows", [None, None])[1] == "\u670d\u52a1 \u9020\u5883 AI v9.9.9-probe \u00b7 healthy \u00b7 \u8fd0\u884c 12.5s"
          and _hB50x.get("shape_valid") is False and _hB50x.get("no_none_row") is True
          and "\u540c\u6e90=False" in _hB50x.get("row_engines_inconsistent", "")
          and _hB50x.get("service_after_bad_shape", {}).get("version") is None
          and _hC50x.get("dead_error_class", {}) == {"health": "URLError",
                                                     "capabilities": "URLError",
                                                     "eval_reports": "URLError"}
          and _hC50x.get("html_verdict") == "not_zaojing_service"
          and _hC50x.get("bad_verdict") == "malformed_json"
          and _hC50x.get("bad_error_class") == "JSONDecodeError"
          and _hC50x.get("headless_window") == {"created": False,
                                                "reason": "headless_requested",
                                                "rows_would_render": 3}
          and _hD50x.get("post_seen") == [{"body": "{}", "method": "POST",
                                           "path": "/api/v1/eval/run"}]
          and str(_hD50x.get("unknown_error", "")).startswith("unknown_action")
          and _eby30.get("calib-hub-wrong-expect", {}).get("passed") is False,
          json.dumps({"cat": _hc50x, "rows": len(_hA50x.get("rows", [])),
                      "post": _hD50x.get("post_seen")}, ensure_ascii=False))
    # Phase 56: brand_semantic_recognition 转正——第 21 brand 引擎的 recognize_semantics + 已内置的
    # 真实 OpenAI-兼容 Provider 客户端承载语义透传（零新引擎）；评测台自起进程内 mock HTTP 钉五形制，
    # 冻结归档里这一黄金域必为 5/5 全绿，其 must_fail 校准判 fail。判据核的是"逐字透传/绝不合成"，
    # 明文密钥不落响应、Bearer 头在场、独立复算 data URL 逐字符对账——若产品回退成自造语义即红。
    _bs56x = (_evj.get("categories", {}) or {}).get("brand_semantics", {})
    # Phase 58：执行器黄金域——评测台用**注入的假 spawn 接缝 + 临时根**真跑十道门与磁盘差集派生，
    # 冻结归档里 6/6 必全绿；这里核的不是"引擎自说已安装"，而是台账逐字段对账与拒做副作用。
    _ec58x = (_evj.get("categories", {}) or {}).get("installer_execution", {})
    _bsV56 = _eby30.get("eval-brand-semantics-verbatim-passthrough", {}).get("details", {})
    _bsD56 = _eby30.get("eval-brand-semantics-dataurl-bearer-independent", {}).get("details", {})
    _bsU56 = _eby30.get("eval-brand-semantics-unconfigured-honest", {}).get("details", {}).get("actual", {})
    _bsN56 = _eby30.get("eval-brand-semantics-no-provider-injected", {}).get("details", {}).get("actual", {})
    _bsR56 = _eby30.get("eval-brand-semantics-unparseable-raw", {}).get("details", {}).get("actual", {})
    check("EXE: eval brand_semantics golden domain 5/5 shipped in the frozen archive - unconfigured+uninjected both honest needs_external with fabricated=False and NO semantics key, the model's real tone/confidence JSON passed through byte-for-byte with usage 133 and method real_provider_vision_passthrough (engine synthesizes nothing), data URL independently recomputed from on-disk PNG bytes and matched into the model request with a live Bearer header while the plaintext key never appears in the response, non-JSON kept as raw with no fabricated schema; its must_fail calibration (expect inventing tone_keywords) judged FAIL not green (Phase 56)",
          _bs56x.get("total") == 5 and _bs56x.get("passed") == 5 and _bs56x.get("failed") == 0
          and _bsU56.get("status") == "needs_external" and _bsU56.get("fabricated") is False
          and _bsU56.get("provider_status") == "no_provider_configured"
          and _bsN56.get("status") == "needs_external" and _bsN56.get("reason") == "no_provider_injected"
          and _bsV56.get("fabricated") is False and _bsV56.get("usage", {}).get("total_tokens") == 133
          and _bsV56.get("actual_semantics") == _bsV56.get("expected")
          and _bsD56.get("auth") == "Bearer sk-eval-p56-secretkey9"
          and _bsD56.get("url_matches_recompute") is True and _bsD56.get("requests") == 1
          and _bsR56.get("status") == "provider_output_unparseable" and _bsR56.get("raw_kept") is True
          and _bsR56.get("semantics_present") is False
          and _eby30.get("calib-brand-semantics-wrong-expect", {}).get("passed") is False,
          json.dumps({"cat": _bs56x, "verbatim": _bsV56.get("usage"),
                      "auth_ok": _bsD56.get("auth") == "Bearer sk-eval-p56-secretkey9"},
                     ensure_ascii=False))
    # 从未把它打进包（包内无 dist/ 与随包 .md，打进去只会产出与真实构建无关的缺失），
    # 因此无参 verify 在冻结归档上必须如实 404 reason=installer_script_not_found 并回显
    # looked_in，绝不伪造"已通过"；可观测的只读安装态探测必须在本机给出真实探针结果。
    _mvx49 = requests.get(BASE + "/api/v1/installer/manifest/verify", timeout=60)
    _mvj49x = _mvx49.json()
    _mxs49 = requests.get(BASE + "/api/v1/installer/state", timeout=60).json()
    _rhx49 = {k: v for k, v in (requests.get(BASE + "/api/v1/metrics", timeout=60)
               .json().get("route_hits", {}) or {}).items() if "installer" in k}
    # 键形成规则按引擎口径就地复算（空 AppId ⇒ 空键；非空 ⇒ {去括号AppId}_is1），
    # 而不是把某一种部署形态的具体值当成万能真值。
    _dk49rule = ("{%s}_is1" % _mxs49["app_id"].strip("{}")) if _mxs49.get("app_id") else ""
    _reg49x = [p for p in (_mxs49.get("probes") or []) if p.get("probe") == "registry_uninstall"]
    check("EXE(P49+P53): manifest/verify honestly 404 installer_script_not_found on the frozen archive (build-time script is not bundled; looked_in echoed, fabricated=False) while read-only install_state derives installed ONLY from real registry probes (Phase 53 grew the probe set 5→7: four registry views + start_menu_group + app_data_root + portable_flag, subkey enumeration non-empty, execution.performed=false, this machine's truth installed=False) and honestly degrades to app_id_source=none / empty derived key (setup.iss lives outside the package), both routes showing route_hits>0 (P44 runtime backfill)",
          _mvx49.status_code == 404
          and _mvj49x.get("detail", {}).get("reason") == "installer_script_not_found"
          and _mvj49x.get("detail", {}).get("fabricated") is False
          and isinstance(_mvj49x.get("detail", {}).get("looked_in"), list)
          and len(_mvj49x["detail"]["looked_in"]) >= 1
          and _mxs49.get("fabricated") is False
          and _mxs49.get("probes_seen") == len(_mxs49.get("probes") or []) == 7
          # 冻结归档真值（p53_l4_freeze3 在本归档实测重钉：Phase 53 把探针集从 5 扩到 7——
          # 四个注册表视图 + start_menu_group + app_data_root + portable_flag）。
          # 包内无 setup.iss ⇒ AppId 发现不到 ⇒ app_id_source=none、script_path=None、
          # 派生卸载键为空。这是诚实退化，不是"探测到未安装"以外的任何伪造结论；
          # 键规则本身仍按引擎口径钉死。
          and _mxs49.get("app_id") == "" and _mxs49.get("app_id_source") == "none"
          and _mxs49.get("script_path") is None
          and _mxs49.get("app_name") == "ZaojingAI"
          and _mxs49.get("app_name_source") == "service_default"
          and _mxs49.get("app_name_matches_service_default") is True
          and _mxs49.get("derived_uninstall_key") == _dk49rule == ""
          and _mxs49.get("execution", {}).get("performed") is False
          # Phase 58 第 3 处就地改核（判卷侧）：`needs_installer_environment` 是那句被**退役**
          # 的边界判词，不是被改写的——执行器真实交付后，只读面仍不执行，状态改为
          # not_executed_by_this_engine，而旧判词逐字保留在 retired_boundary.was 里
          # （退役 ≠ 改写，见 installer_manifest_engine.EXECUTION_BOUNDARY）。
          # 只钉新值会把"旧判词仍然可查"这条声称丢掉，故两侧同时钉。
          and _mxs49.get("execution", {}).get("status") == "not_executed_by_this_engine"
          and _mxs49.get("execution", {}).get("retired_boundary", {})
              .get("was") == "needs_installer_environment"
          and _mxs49.get("execution", {}).get("retired_boundary", {})
              .get("retired_in") == "Phase 58"
          and _mxs49.get("execution", {}).get("fabricated") is False
          and len(_reg49x) == 4
          and {(_p.get("root"), _p.get("view")) for _p in _reg49x} == {
              ("HKCU", "64"), ("HKCU", "32"), ("HKLM", "64"), ("HKLM", "32")}
          and sorted({p.get("probe") for p in (_mxs49.get("probes") or [])}) == [
              "app_data_root", "portable_flag", "registry_uninstall", "start_menu_group"]
          # 枚举必须真有数据：subkey_count>0 才说明"零命中"是真的零命中，
          # 而不是接缝被桩成恒空列表（那种假绿与诚实未装长得一模一样）。
          and all(isinstance(p.get("subkey_count"), int) and p["subkey_count"] > 0
                  for p in _reg49x)
          and _mxs49.get("installed") is any(
              p.get("present") for p in _reg49x)
          # 本机真值：HKCU/HKLM 卸载子键零命中 ⇒ installed 必为 False（不变式之外再钉观测面，
          # 否则"见 app_id 即命中"的桩会两边同翻真而判卷无感——p49 变异 m2 的落点）
          and _mxs49.get("installed") is False
          and all(k in _rhx49 and _rhx49[k] > 0 for k in (
              "/api/v1/installer/manifest/verify", "/api/v1/installer/state"))
          and all(_m in _j39 for _m in ('"/installer/manifest/verify"', '"/installer/state"',
                                        "mfVerifyView", "mfStateView",
                                        "installer_script_not_found", "op-mf-verify",
                                        "op-mf-state", "mf-path"))
          and all(_m in _ui35.text for _m in ('id="op-mf-verify"', 'id="op-mf-state"',
                                              'id="mf-path"', "第 26 引擎",
                                              "installer_script_not_found")),
          json.dumps({"verify_status": _mvx49.status_code, "detail": _mvj49x.get("detail"),
                      "state": {k: _mxs49.get(k) for k in ("installed", "probes_seen", "app_id",
                                                            "app_id_source",
                                                            "derived_uninstall_key")},
                      "registry": _reg49x,
                      "hits": _rhx49}, ensure_ascii=False))

    # Phase 53：编译面 / 预检面两路由随包冻结后的诚实边界。构建期工件（setup.iss、
    # installer/build-evidence.json）不随 onefile 分发 ⇒ 两路由必 404 并如实报 looked_in；
    # 显式指定包外路径必 403（越出允许根）；非法规格/参数必 400。全部 fabricated=False。
    _ar53 = requests.get(BASE + "/api/v1/installer/artifact", timeout=60)
    _arj53 = _ar53.json()
    _pf53 = requests.get(BASE + "/api/v1/installer/preflight", timeout=60)
    _pfj53 = _pf53.json()
    _ext_ev = OUT / "installer" / "build-evidence.json"
    _ext_iss = OUT / "setup.iss"
    _arx53 = requests.get(BASE + "/api/v1/installer/artifact",
                          params={"evidence_path": str(_ext_ev)}, timeout=60)
    _pfx53 = requests.get(BASE + "/api/v1/installer/preflight",
                          params={"script_path": str(_ext_iss)}, timeout=60)
    _pfb53 = requests.get(BASE + "/api/v1/installer/preflight",
                          params={"probe": "maybe"}, timeout=60)
    _ars53 = requests.get(BASE + "/api/v1/installer/artifact",
                          params={"evidence_path": str(_ext_iss)}, timeout=60)
    _rh53 = {k: int(v) for k, v in (requests.get(BASE + "/api/v1/metrics", timeout=60)
                .json().get("route_hits", {}) or {}).items() if "installer" in k}
    check("EXE(P53): the two new installer faces are honest AT THE FROZEN BOUNDARY — GET /installer/artifact 404 build_evidence_not_found (looked_in 4 entries: _MEIPASS installer/ + bare, portable dir installer/ + bare; the build-time evidence is NOT bundled and the endpoint never fabricates 已证成), GET /installer/preflight 404 installer_script_not_found (looked_in 2), an explicit path OUTSIDE the allowed roots is refused 403 (evidence_path_outside_allowed_roots / manifest_path_outside_allowed_roots — the read-only sandbox structurally cannot point at the system install dir), probe=maybe is refused 400 preflight_probe_param_invalid with the 8 accepted spellings echoed, a .iss passed as evidence_path is refused 400 evidence_suffix_invalid, both routes carry route_hits>0, and the console wires them (HTML ids op-mf-artifact/op-mf-preflight/mf-ev-path/mf-override/mf-probe present, JS carries the call strings + mfArtifactView/mfPreflightView + p53Why) — app.js deliberately does NOT hardcode build_evidence_not_found: it echoes the server's reason, so the token must stay absent there (measured by p53_l4_freeze3, not inferred from L2)",
          _ar53.status_code == 404
          and _arj53.get("detail", {}).get("reason") == "build_evidence_not_found"
          and _arj53.get("detail", {}).get("fabricated") is False
          and len(_arj53.get("detail", {}).get("looked_in") or []) == 4
          and _pf53.status_code == 404
          and _pfj53.get("detail", {}).get("reason") == "installer_script_not_found"
          and len(_pfj53.get("detail", {}).get("looked_in") or []) == 2
          and _ext_ev.is_file() and _ext_iss.is_file()
          and _arx53.status_code == 403
          and _arx53.json().get("detail", {}).get("reason") == "evidence_path_outside_allowed_roots"
          and len(_arx53.json().get("detail", {}).get("allowed_roots") or []) == 2
          and _arx53.json().get("detail", {}).get("fabricated") is False
          and _pfx53.status_code == 403
          and _pfx53.json().get("detail", {}).get("reason") == "manifest_path_outside_allowed_roots"
          and _pfb53.status_code == 400
          and _pfb53.json().get("detail", {}).get("reason") == "preflight_probe_param_invalid"
          and [str(x).lower() for x in
               (_pfb53.json().get("detail", {}).get("accepted") or [])] \
              == ["true", "1", "yes", "on", "false", "0", "no", "off"]
          and _ars53.status_code == 400
          and _ars53.json().get("detail", {}).get("reason") == "evidence_suffix_invalid"
          and _ars53.json().get("detail", {}).get("suffix") == ".iss"
          and _ars53.json().get("detail", {}).get("expected") == [".json"]
          and all(_rh53.get(k, 0) > 0 for k in ("/api/v1/installer/artifact",
                                                 "/api/v1/installer/preflight"))
          and all(_m in _ui35.text for _m in ('id="op-mf-artifact"', 'id="op-mf-preflight"',
                                              'id="mf-ev-path"', 'id="mf-override"',
                                              'id="mf-probe"', "编译证据核验", "安装预检"))
          # P53 实测（app.js:1024/1044）：artifact 拼成 "/installer/artifact" + 可选查询，
          # preflight 恒带查询 ⇒ 字面串是 "/installer/preflight?"，末引号在 ? 之后。
          # 判卷侧曾臆造 '"/installer/preflight"'（闭合引号紧跟 e）⇒ 判卷笔误，非产品缺陷。
          and all(_m in _j39 for _m in ('"/installer/artifact"', '"/installer/preflight?"',
                                        "mfArtifactView", "mfPreflightView", "p53Why",
                                        "op-mf-artifact", "op-mf-preflight", "mf-ev-path",
                                        "mf-override", "mf-probe"))
          and "build_evidence_not_found" not in _j39,
          json.dumps({"artifact": [_ar53.status_code, _arj53.get("detail", {}).get("reason"),
                                   len(_arj53.get("detail", {}).get("looked_in") or [])],
                      "preflight": [_pf53.status_code, _pfj53.get("detail", {}).get("reason")],
                      "outside": [_arx53.status_code, _pfx53.status_code],
                      "bad": [_pfb53.status_code, _ars53.status_code],
                      "hits": _rh53}, ensure_ascii=False))

    _erp = data_dir / "evaluation" / "reports" / f"{_evj.get('report_id', '')}.json"
    check("EXE: eval report lands inside local Data\\evaluation (Phase 24 portable persistence)",
          bool(_evj.get("report_id")) and _erp.exists()
          and _evj.get("report_id") in _erp.read_text(encoding="utf-8"), str(_erp))

    # Phase 29: Capability Registry 2.0 证据派生确已编入冻结归档
    _rgj = requests.get(BASE + "/api/v1/capabilities").json().get("registry", {})
    _rc = {c["id"]: c for c in _rgj.get("capabilities", [])}
    # Phase 59 三枚黄金域的 live category 现场派生（must_fail 校准已被聚合排除）。
    # 与 L2 的 _dom_expected 同口径：期望值来自**本归档自己刚跑完的那份报告**，不手抄枚数。
    _gr59x = (_evj.get("categories", {}) or {}).get("runtime_guard", {})
    _ab59x = (_evj.get("categories", {}) or {}).get("installer_artifact_build", {})
    _pf59x = (_evj.get("categories", {}) or {}).get("installer_preflight", {})
    check("EXE: registry 2.0 schema with 43 caps and engine names sourced from _ENGINE_NAMES (Phase 29, +installer_manifest Phase 49, +desktop_hub Phase 50, +installer_artifact_build/installer_preflight Phase 53, +cdr_document_read Phase 77, +cdr_document_write Phase 78)",
          _rgj.get("schema_version") == "capability-registry/2.0" and len(_rc) == 43
          and all(c["implemented"] is (c["status"] == "supported") for c in _rc.values())
          and _rc["color_engine"]["engine"] == "color" and _rc["color_engine"]["health_ready"] is True
          and _rc["image_remove_background"]["engine"] == "bg_removal" \
          and _rc["design_compare"]["engine"] == "compare" \
          and _rc["packaging_panel_recognition"]["engine"] == "panel_recognition" \
          and _rc["installer_manifest_verification"]["engine"] == "installer_manifest" \
          and _rc["desktop_hub"]["engine"] == "desktop_hub" \
          and _rc["desktop_hub"]["health_ready"] is True \
          and _rc["desktop_hub"]["golden_domain"] == "desktop_hub" \
          and _rc["installer_manifest_verification"]["golden_domain"] == "installer_manifest" \
          # Phase 53 分裂出的两条能力同属第 26 引擎，当时 _CAPABILITY_ENGINE_GOLDEN 里
          # golden=None ⇒ 不借别的域凑证据，Registry 2.0 如实报 no_golden_cases_yet /
          # verified=False（保守欠称，不是伪证）。
          # Phase 59 路线 B：这组 None 期望作废——编译面 3 条与预检面 2 条本来就在被评测台
          # 逐条执行，只是把证据记在 installer_manifest 一个域名下；现在把 category 改归给
          # 它们真正证明的能力，golden_domain 由 None 变为自有域名、verified 由 False 变为 True。
          # 这是**记账口径变严**而非产品退化：用例内容一字未改，真值表零漂移。
          and _rc["installer_artifact_build"]["engine"] == "installer_manifest" \
          and _rc["installer_artifact_build"]["golden_domain"] == "installer_artifact_build" \
          and _rc["installer_artifact_build"]["evidence_kind"] == "golden_cases" \
          and _rc["installer_artifact_build"]["verified"] is True \
          and _rc["installer_artifact_build"]["health_ready"] is True \
          and _rc["installer_preflight"]["engine"] == "installer_manifest" \
          and _rc["installer_preflight"]["golden_domain"] == "installer_preflight" \
          and _rc["installer_preflight"]["evidence_kind"] == "golden_cases" \
          and _rc["installer_preflight"]["verified"] is True \
          and _rc["installer_preflight"]["health_ready"] is True \
          # Phase 59 路线 B 的另一枚：runtime_guard 是**从零新题**（第 22 引擎的守护面
          # 首次拥有自己的黄金域，5 条真值 + 1 枚自有校准），键名是能力 id
          # installer_runtime_guard，黄金域名才是 runtime_guard。
          and _rc["installer_runtime_guard"]["engine"] == "runtime" \
          and _rc["installer_runtime_guard"]["golden_domain"] == "runtime_guard" \
          and _rc["installer_runtime_guard"]["evidence_kind"] == "golden_cases" \
          and _rc["installer_runtime_guard"]["verified"] is True \
          and _rc["installer_execution"]["engine"] == "runtime" \
          and _rc["installer_execution"]["evidence_kind"] == "golden_cases" \
          and _rc["installer_execution"]["golden_domain"] == "installer_execution" \
          and _rc["installer_execution"]["test_count"] == _ec58x.get("total") == 6 \
          and _rc["installer_execution"]["passed_count"] == _ec58x.get("passed") == 6 \
          and _rc["installer_execution"]["failed_count"] == 0 \
          and _rc["installer_execution"]["verified"] is True,
          json.dumps(len(_rc)))
    check("EXE: golden caps read THIS frozen exe's own on-disk eval report for evidence counts (Phase 29, +revision/review/security 30, +workflow 31, +asset/recovery 32, +typography 33, +barcode/topology 34, +license/config/model_routing 36, +update/nas/provider 37, +compare/bg_removal 38, +installer_manifest 49, +desktop_hub 50, +brand_semantics 56, +barcode QR 57 域内 5→10, +installer_execution 58, +runtime_guard 59 新题 5 条 / installer_artifact_build+installer_preflight 59 改归 —— 同轮 installer_manifest 域内 11→6 属证据记账收紧而非产品退化；Phase 63 一维解码 oracle 令 barcode 域内 10→14)",
          _rc["color_engine"]["evidence_kind"] == "golden_cases"
          and _rc["color_engine"]["test_count"] == _evj.get("categories", {}).get("design", {}).get("total")
          and _rc["print_qa"]["test_count"] == 2 and _rc["knowledge_rag"]["test_count"] == 2
          and _rc["brand_color_analysis"]["test_count"] == 4
          and _rc["customer_revision"]["test_count"] == _erv30.get("total")
          and _rc["design_review"]["test_count"] == _erw30.get("total")
          and _rc["security"]["test_count"] == _esc30.get("total")
          and _rc["workflow_engine"]["test_count"] == _ewf31.get("total")
          and _rc["asset_engine"]["test_count"] == _eas32.get("total")
          and _rc["recovery_snapshot"]["test_count"] == _erc32.get("total")
          and _rc["typography_engine"]["test_count"] == _etc33.get("total")
          and _rc["packaging_barcode"]["test_count"] == _ebk34.get("total") == 14
          and _rc["packaging_panel_topology"]["test_count"] == _etp34.get("total") == 5
          and _rc["license"]["test_count"] == _elc36.get("total") == 4
          and _rc["config_persistence"]["test_count"] == _ecc36.get("total") == 4
          and _rc["model_router"]["test_count"] == _ert36.get("total") == 4
          and _rc["auto_update"]["test_count"] == _uc37.get("total") == 4
          and _rc["nas_mount"]["test_count"] == _nc37.get("total") == 4
          and _rc["ai_provider_integration"]["test_count"] == _pc37.get("total") == 4
          and _rc["design_compare"]["test_count"] == _cc38.get("total") == 4
          and _rc["image_remove_background"]["test_count"] == _bgc38.get("total") == 4
          and _rc["packaging_panel_recognition"]["test_count"] == _pc47.get("total") == 4
          and _rc["installer_manifest_verification"]["test_count"] == _mc49x.get("total") == 6
          and _rc["installer_manifest_verification"]["passed_count"] == _mc49x.get("passed") == 6
          and _rc["desktop_hub"]["test_count"] == _hc50x.get("total") == 4
          and _rc["desktop_hub"]["passed_count"] == _hc50x.get("passed") == 4
          and _rc["desktop_hub"]["failed_count"] == 0
          and _rc["brand_semantic_recognition"]["test_count"] == _bs56x.get("total") == 5
          and _rc["brand_semantic_recognition"]["passed_count"] == _bs56x.get("passed") == 5
          and _rc["brand_semantic_recognition"]["engine"] == "brand"
          and _rc["brand_semantic_recognition"]["golden_domain"] == "brand_semantics"
          and _rc["brand_semantic_recognition"]["evidence_kind"] == "golden_cases"
          and _rc["installer_execution"]["test_count"] == _ec58x.get("total") == 6
          and _rc["installer_execution"]["passed_count"] == _ec58x.get("passed") == 6
          and _rc["installer_execution"]["failed_count"] == 0
          and _rc["installer_execution"]["engine"] == "runtime"
          and _rc["installer_execution"]["golden_domain"] == "installer_execution"
          and _rc["installer_execution"]["evidence_kind"] == "golden_cases"
          # Phase 59 路线 B 三枚：runtime_guard 从零新题 5 条；编译面/预检面各 3/2 条为**改归**。
          # 缺席域必须判红而不是抛异常——_gr59x 之类在域还没交付时就是 {}，
          # .get("total") 回 None，None != 5 照样红（P52「判卷侧崩溃≠被测缺陷」同族教训）。
          and _rc["installer_runtime_guard"]["test_count"] == _gr59x.get("total") == 5
          and _rc["installer_runtime_guard"]["passed_count"] == _gr59x.get("passed") == 5
          and _rc["installer_runtime_guard"]["failed_count"] == 0
          and _rc["installer_runtime_guard"]["engine"] == "runtime"
          and _rc["installer_runtime_guard"]["golden_domain"] == "runtime_guard"
          and _rc["installer_runtime_guard"]["evidence_kind"] == "golden_cases"
          and _rc["installer_artifact_build"]["test_count"] == _ab59x.get("total") == 3
          and _rc["installer_artifact_build"]["passed_count"] == _ab59x.get("passed") == 3
          and _rc["installer_artifact_build"]["failed_count"] == 0
          and _rc["installer_artifact_build"]["evidence_kind"] == "golden_cases"
          and _rc["installer_preflight"]["test_count"] == _pf59x.get("total") == 2
          and _rc["installer_preflight"]["passed_count"] == _pf59x.get("passed") == 2
          and _rc["installer_preflight"]["failed_count"] == 0
          and _rc["installer_preflight"]["evidence_kind"] == "golden_cases"
          # Phase 79（轨道 A 第一刀）：Adobe 桥三枚 golden 化。诚实边界——verified=True 仅证
          # 协议机制对进程内 mock 宿主的异步握手→下发→轮询→回填→对账往返成立，绝不等于已对
          # 真实 Adobe／CorelDRAW 文档做改写；ps/ai 写入与插件能力上报仍留 needs_host 诚实边界。
          and _rc["adobe_uxp_bridge"]["engine"] == "adobe_bridge"
          and _rc["adobe_uxp_bridge"]["golden_domain"] == "bridge_handshake"
          and _rc["adobe_uxp_bridge"]["status_2_0"] == "supported"
          and _rc["adobe_uxp_bridge"]["evidence_kind"] == "golden_cases"
          and _rc["adobe_uxp_bridge"]["test_count"] == 1 and _rc["adobe_uxp_bridge"]["passed_count"] == 1
          and _rc["adobe_uxp_bridge"]["failed_count"] == 0 and _rc["adobe_uxp_bridge"]["verified"] is True
          and _rc["cdr_document_read"]["engine"] == "adobe_bridge"
          and _rc["cdr_document_read"]["golden_domain"] == "bridge_cdr_read"
          and _rc["cdr_document_read"]["status_2_0"] == "supported"
          and _rc["cdr_document_read"]["evidence_kind"] == "golden_cases"
          and _rc["cdr_document_read"]["test_count"] == 1 and _rc["cdr_document_read"]["passed_count"] == 1
          and _rc["cdr_document_read"]["failed_count"] == 0 and _rc["cdr_document_read"]["verified"] is True
          and _rc["cdr_document_write"]["engine"] == "adobe_bridge"
          and _rc["cdr_document_write"]["golden_domain"] == "bridge_cdr_write"
          and _rc["cdr_document_write"]["status_2_0"] == "supported"
          and _rc["cdr_document_write"]["evidence_kind"] == "golden_cases"
          and _rc["cdr_document_write"]["test_count"] == 1 and _rc["cdr_document_write"]["passed_count"] == 1
          and _rc["cdr_document_write"]["failed_count"] == 0 and _rc["cdr_document_write"]["verified"] is True
          # Phase 80（轨道 A 第二刀）：服务侧持久化配置三枚 golden 化。三域逐字下沉为第 28 引擎
          # service_registry（单一真相源，端点全部委托它），评测台于私有沙箱直接构造出货的
          # ServiceRegistryEngine 驱动同一份随包代码——黄金用例驱动的就是端点委托的那份代码，非另造桩。
          and _rc["feature_flags"]["engine"] == "service_registry"
          and _rc["feature_flags"]["golden_domain"] == "feature_flags"
          and _rc["feature_flags"]["status_2_0"] == "supported"
          and _rc["feature_flags"]["evidence_kind"] == "golden_cases"
          and _rc["feature_flags"]["test_count"] == 1 and _rc["feature_flags"]["passed_count"] == 1
          and _rc["feature_flags"]["failed_count"] == 0 and _rc["feature_flags"]["verified"] is True
          and _rc["tool_registry"]["engine"] == "service_registry"
          and _rc["tool_registry"]["golden_domain"] == "tool_registry"
          and _rc["tool_registry"]["status_2_0"] == "supported"
          and _rc["tool_registry"]["evidence_kind"] == "golden_cases"
          and _rc["tool_registry"]["test_count"] == 1 and _rc["tool_registry"]["passed_count"] == 1
          and _rc["tool_registry"]["failed_count"] == 0 and _rc["tool_registry"]["verified"] is True
          and _rc["skill_runtime"]["engine"] == "service_registry"
          and _rc["skill_runtime"]["golden_domain"] == "skill_runtime"
          and _rc["skill_runtime"]["status_2_0"] == "supported"
          and _rc["skill_runtime"]["evidence_kind"] == "golden_cases"
          and _rc["skill_runtime"]["test_count"] == 1 and _rc["skill_runtime"]["passed_count"] == 1
          and _rc["skill_runtime"]["failed_count"] == 0 and _rc["skill_runtime"]["verified"] is True
          # Phase 81（轨道 A 服务侧最后一刀）：delivery_package / websocket_events 两枚升到 VERIFIED
          # 侧——逐字下沉为第 29/30 引擎 event_engine / delivery_engine 的单一真相源、端点全量委托，
          # 黄金域逐一对名、evidence=golden_cases、verified True、test_count 1、failed 0；判据是评测台
          # 于私有实例／私有沙箱直接构造出货引擎驱动同一份随包代码，非另造桩。诚实边界：verified=True
          # 仅证事件簿记／交付磁盘工作对出货引擎真实往返成立，不夸大任何未交付能力。
          and _rc["delivery_package"]["engine"] == "delivery"
          and _rc["delivery_package"]["golden_domain"] == "delivery_package"
          and _rc["delivery_package"]["status_2_0"] == "supported"
          and _rc["delivery_package"]["evidence_kind"] == "golden_cases"
          and _rc["delivery_package"]["test_count"] == 1 and _rc["delivery_package"]["passed_count"] == 1
          and _rc["delivery_package"]["failed_count"] == 0 and _rc["delivery_package"]["verified"] is True
          and _rc["websocket_events"]["engine"] == "event"
          and _rc["websocket_events"]["golden_domain"] == "websocket_events"
          and _rc["websocket_events"]["status_2_0"] == "supported"
          and _rc["websocket_events"]["evidence_kind"] == "golden_cases"
          and _rc["websocket_events"]["test_count"] == 1 and _rc["websocket_events"]["passed_count"] == 1
          and _rc["websocket_events"]["failed_count"] == 0 and _rc["websocket_events"]["verified"] is True
          and _rc["evaluation_center"]["test_count"] == _es.get("executed_cases")
          and all(_rc[i]["verified"] is True and _rc[i]["failed_count"] == 0
                  and _rc[i]["last_verified_at"] == _evj.get("finished_at")
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
                            "installer_artifact_build", "installer_preflight",
                            "adobe_uxp_bridge", "cdr_document_read", "cdr_document_write",
                            "feature_flags", "tool_registry", "skill_runtime",
                            "delivery_package", "websocket_events"))
          and sum(1 for c in _rc.values()
                  if c["status"] == "supported" and c["verified"]) == 40,
          json.dumps(_rc.get("evaluation_center"), ensure_ascii=False))
    _evid59x = {}
    for _c59x in _rc.values():
        _evid59x[_c59x["evidence_kind"]] = _evid59x.get(_c59x["evidence_kind"], 0) + 1
    check("EXE: honest boundaries intact in registry (no-golden verified=False, needs_host honest_boundary) (barcode promoted to golden Phase 34, update/nas promoted Phase 37, design_compare/image_remove_background promoted Phase 38, installer_manifest promoted Phase 49, desktop_hub LEFT this set in Phase 50, brand_semantic_recognition LEFT this set in Phase 56, installer_execution LEFT this set in Phase 58; Phase 53's installer_artifact_build/installer_preflight joined the NOT-rubber-stamped side because they owned no golden domain of their own — Phase 59 route B moved them to the VERIFIED side by RE-ATTRIBUTING the very cases already executed under installer_manifest (a bookkeeping fix, not new coverage), and promoted installer_runtime_guard with 5 genuinely new cases. Phase 79 (track A first cut) promoted the three Adobe-bridge caps adobe_uxp_bridge/cdr_document_read/cdr_document_write out of this set onto the VERIFIED side: each now owns exactly one golden case that drives the real async handshake→dispatch→poll→submit_result→reconcile round-trip against an in-process protocol-compliance mock host, so verified=True here proves ONLY the protocol mechanism against a mock host — it does NOT claim a real Adobe/CorelDRAW document was mutated; ps_document_write/ai_document_write/adobe_capability_report remain the 3 honest_boundary needs_host caps. Phase 80 (track A second cut) then promoted feature_flags/tool_registry/skill_runtime out of this set onto the VERIFIED side: each now owns exactly one golden case that constructs the shipped 28th engine ServiceRegistryEngine in a private sandbox and drives the SAME code the delegating endpoints run (not a hand-written stub). What remains in the no-golden set after Phase 80 was exactly the 2 service-side caps (delivery_package/websocket_events, engine=None, no golden domain); Phase 81 (track A final cut) promoted BOTH of them onto the VERIFIED side — each now owns exactly one golden case that constructs the shipped 29th engine EventHub / 30th engine DeliveryEngine in a private instance/sandbox and drives the SAME code the delegating endpoints run (not a hand-written stub). The no-golden set is therefore now EMPTY: every one of the 40 supported caps carries golden evidence, and the only non-golden rows are the 3 needs_host honest_boundary Adobe caps (ps/ai document write + capability report)",
          all(_rc[i]["evidence_kind"] == "golden_cases" and _rc[i]["verified"] is True
              for i in ("delivery_package", "websocket_events"))
          # Phase 81 把留守的最后两枚服务侧能力逐字下沉为第 29/30 引擎并 golden 化后，no_golden
          # 桶首次为空。空集必须靠"计数"钉死而非逐名枚举——列举零个名字恒真，那正是本判据要防的假绿。
          # 若日后有人把已 golden 的能力偷偷留回 no-golden（或把新能力漏进 no-golden），
          # 下面的 ==0 与逐名 golden_cases 两路都会当场红，绝不认"少列几条也算对"。
          and sum(1 for c in _rc.values()
                  if c["evidence_kind"] == "no_golden_cases_yet") == 0
          and _rc["delivery_package"]["evidence_kind"] != "no_golden_cases_yet"
          and _rc["websocket_events"]["evidence_kind"] != "no_golden_cases_yet"
          and all(_rc[i]["status_2_0"] == "needs_host"
                  and _rc[i]["evidence_kind"] == "honest_boundary"
                  for i in ("ps_document_write", "ai_document_write",
                            "adobe_capability_report"))
          and sum(1 for c in _rc.values() if c["evidence_kind"] == "honest_boundary") == 3
          # 形态迁移逐项核对，不认"少了一条就算对"：desktop_hub 的行在 Phase 50、brand_semantic_
          # recognition 的行在 Phase 56、installer_execution 的行在 Phase 58 各自从 honest_boundary
          # 迁到 golden_cases。第三枚迁走之后本集合首次为空 ⇒ 判据同时钉"没有任何 not_shipped 残留"，
          # 这一条若日后有人把已退役边界偷偷删行而非改状态，就会在这里红。
          and not any(c["status_2_0"] == "not_shipped" for c in _rc.values())
          and _rc["installer_execution"]["status_2_0"] == "supported"
          and _rc["installer_execution"]["evidence_kind"] == "golden_cases"
          and _rc["installer_execution"]["verified"] is True
          and _rc["desktop_hub"]["status_2_0"] == "supported"
          and _rc["desktop_hub"]["evidence_kind"] == "golden_cases"
          and _rc["desktop_hub"]["verified"] is True
          and _rc["brand_semantic_recognition"]["status_2_0"] == "supported"
          and _rc["brand_semantic_recognition"]["evidence_kind"] == "golden_cases"
          and _rc["brand_semantic_recognition"]["verified"] is True
          and _rc["installer_runtime_guard"]["evidence_kind"] == "golden_cases"
          and _rc["installer_runtime_guard"]["verified"] is True
          and _rc["installer_artifact_build"]["evidence_kind"] == "golden_cases"
          and _rc["installer_preflight"]["evidence_kind"] == "golden_cases"
          and _rc["packaging_panel_recognition"]["evidence_kind"] == "golden_cases"
          and _rc["installer_manifest_verification"]["evidence_kind"] == "golden_cases"
          and _rc["packaging_panel_recognition"]["verified"] is True
          # 三种证据形态的全量分布：一枚都不许缺位、也不许多出来（改归只搬家不改判分，
          # 故 honest_boundary 恒 3；golden_cases 由 29 涨到 32 = 新题 1 + 改归 2；
          # Phase 77 的 cdr_document_read 属新题但无黄金域 ⇒ no_golden_cases_yet 6→7；
          # Phase 78 的 cdr_document_write 同族新题（无黄金域）⇒ 7→8；
          # Phase 79 轨道 A 第一刀：adobe_uxp_bridge/cdr_document_read/cdr_document_write 三枚
          # 各获 1 枚 golden 真值用例 ⇒ golden_cases 32→35、no_golden_cases_yet 8→5，
          # honest_boundary 恒 3。此三枚 verified=True 仅证协议机制对 mock 宿主往返成立）。
          # Phase 80 轨道 A 第二刀：feature_flags/tool_registry/skill_runtime 三枚各获 1 枚 golden
          # 真值用例（下沉第 28 引擎 service_registry）⇒ golden_cases 35→38、no_golden_cases_yet 5→2
          # （仅余 delivery_package/websocket_events），honest_boundary 恒 3。
          # Phase 81 轨道 A 服务侧最后一刀：delivery_package/websocket_events 两枚各获 1 枚 golden
          # 真值用例（下沉第 29/30 引擎 event_engine/delivery_engine）⇒ golden_cases 38→40、
          # no_golden_cases_yet 2→0（服务侧全域入黄金），honest_boundary 恒 3（ps/ai 写 + 能力上报）。
          # 判据须尊重派生形状：_evid59x 只累加**出现过的** evidence_kind，计数归 0 即键**缺席**
          # 而非显式 0（缺席与 0 是两回事），故不能钉 "no_golden_cases_yet":0，改钉「该键缺席
          # + golden 40 + honest_boundary 3 + 求和 43」三者合证。
          and _evid59x == {"golden_cases": 40, "honest_boundary": 3}
          and "no_golden_cases_yet" not in _evid59x
          and sum(_evid59x.values()) == 43,
          json.dumps({i: _rc[i]["evidence_kind"] for i in ("feature_flags", "ps_document_write",
                                                           "desktop_hub")} | {"evidence": _evid59x}))

    # Phase 25: 品牌引擎真实像素分析确已编入冻结归档；uxp discovery 不再 uxp/uxp 嵌套
    _bp = requests.post(BASE + "/api/v1/brand/palette", json={"image_path": str(img), "k": 2})
    _bpj = _bp.json()
    _bph = sorted(e["hex"].upper() for e in _bpj.get("palette", []))
    check("EXE: brand palette real median-cut hexes of prepared probe image (Phase 25)",
          _bp.status_code == 200 and _bph == ["#0A5AC8", "#FFFFFF"]
          and _bpj.get("method") == "median_cut_via_color_engine", json.dumps(_bph))
    _bn = requests.post(BASE + "/api/v1/brand/color-consistency",
                        json={"brand_hexes": ["#00FF00"], "image_path": str(img), "max_delta_e": 10.0}).json()
    _bne = (_bn.get("brand_colors") or [{}])[0]
    check("EXE: brand consistency negative control - green brand on blue/white image matched=False (falsifiable)",
          _bne.get("matched") is False and _bn.get("coverage") == 0.0 and _bn.get("fabricated") is False,
          json.dumps(_bne))
    _lg = tmp / "p25_logo_exe.png"
    _lim = Image.new("RGBA", (80, 40), (0, 0, 0, 0))
    for _y in range(8, 32):
        for _x in range(16, 64):
            _lim.putpixel((_x, _y), (3, 3, 3, 255))
    _lim.save(_lg)
    _bg = requests.post(BASE + "/api/v1/brand/logo-geometry",
                        json={"image_path": str(_lg), "alpha_threshold": 8}).json()
    check("EXE: brand logo geometry real alpha bbox [16,8,64,32] margins 16/8 (Phase 25)",
          _bg.get("content_bbox") == [16, 8, 64, 32]
          and _bg.get("margins") == {"left": 16, "top": 8, "right": 16, "bottom": 8}
          and _bg.get("aspect_ratio") == 2.0, json.dumps({k: _bg.get(k) for k in ("content_bbox", "margins")}))
    _bse = requests.get(BASE + "/api/v1/brand/semantics").json()
    check("EXE: brand semantics honest needs_external in frozen archive (no invented tone)",
          _bse.get("status") == "needs_external" and _bse.get("fabricated") is False, str(_bse.get("status")))
    _bv25 = requests.get(BASE + "/api/v1/uxp/bridge").json()
    _dp25 = Path(_bv25.get("discovery_file", ""))
    check("EXE: uxp discovery flat under local Data\\uxp, no uxp\\uxp nesting (Phase 25)",
          _dp25.parent == data_dir / "uxp" and _dp25.name == "bridge-endpoint.json"
          and not (data_dir / "uxp" / "uxp" / "bridge-endpoint.json").exists(), str(_dp25))

    # ---- Phase 26 运行时守护在冻结 EXE 内真跑（负控 = EXE 自己监听的 8877）----
    _rd = requests.get(BASE + "/api/v1/runtime/diagnostic").json()
    check("EXE: runtime diagnostic derived from real checks; write probe passes on local Data (frozen archive)",
          _rd.get("fabricated") is False
          and _rd.get("checks_performed") == _rd.get("checks_passed") + _rd.get("checks_failed") == len(_rd.get("checks", []))
          and any(c["check"] == "data_root_writable" and c["status"] == "pass" for c in _rd["checks"])
          and Path(_rd["system"]["data_root"]).resolve() == data_dir.resolve(),
          json.dumps({k: _rd.get(k) for k in ("status", "checks_passed", "checks_performed")}))
    _rn = requests.get(BASE + "/api/v1/runtime/diagnostic", params={"service_port": "8877"}).json()
    _rp = [c for c in _rn["checks"] if c["check"] == "port_available"][0]
    check("EXE NEG: occupied own port 8877 -> honest fail + degraded inside frozen exe (real bind conflict)",
          _rp["status"] == "fail" and _rn["status"] == "degraded", str(_rn.get("status")))
    _nm26 = "exe-p26-%d" % os.getpid()
    _si1 = requests.post(BASE + "/api/v1/runtime/single-instance", json={"name": _nm26}).json()
    _si2 = requests.post(BASE + "/api/v1/runtime/single-instance", json={"name": _nm26}).json()
    _si3 = requests.post(BASE + "/api/v1/runtime/single-instance", json={"name": _nm26, "action": "release"}).json()
    check("EXE: kernel named-mutex single-instance real in frozen archive (win, already_running on 2nd, release ok)",
          _si1.get("acquired") is True and _si2.get("already_running") is True and _si3.get("released") is True,
          json.dumps({k: _si1.get(k) for k in ("acquired", "backend")}))
    _ia26 = requests.get(BASE + "/api/v1/runtime/install-actions")
    _iaj26 = _ia26.json() if _ia26.status_code == 200 else {}
    # Phase 58：这一句从 needs_installer_environment 翻成 executable_via_cli_only。P26 那句
    # 判词当时是诚实的（引擎里连一个 spawn 接缝都没有），P58 交付了执行器本体，故退休它——
    # 但旧原文以 retired_boundary.was 逐字留在同一响应里（退役≠改写），且翻转只改"能力声明"，
    # HTTP 面依旧走不到执行器：安装域没有任何非 GET 路由。
    check("EXE(P26+P58): install-actions declares the executor shipped-but-CLI-only in the frozen archive — status flipped needs_installer_environment -> executable_via_cli_only with the old wording kept verbatim in retired_boundary.was, the confirm token and the ten gate names echoed, and NO example.com/1.1.0 mock residue",
          _ia26.status_code == 200 and _iaj26.get("status") == "executable_via_cli_only"
          and _iaj26.get("capability") == "installer_execution"
          and _iaj26.get("fabricated") is False
          and _iaj26.get("retired_boundary", {}).get("was") == "needs_installer_environment"
          and _iaj26.get("retired_boundary", {}).get("retired_in") == "Phase 58"
          and len(_iaj26.get("gates") or []) == 10
          and _iaj26.get("confirm_token") == "ZAOJING-CONFIRM-EXECUTE-INSTALLER"
          and "example.com" not in _ia26.text and "1.1.0" not in _ia26.text,
          str(_iaj26.get("status")))

    # Phase 58 冻结归档腿：执行器的**只读对账面**必须真的编进 onefile，并在一台从未按这条
    # 链跑过安装的沙箱数据根上诚实回 ledger_absent（不是 404、不是伪造回执）；同时证明
    # HTTP 面对安装域没有任何写路由——执行只能由 CLI 显式令牌抵达。
    _ex58 = requests.get(BASE + "/api/v1/installer/execution", timeout=60)
    _exj58 = _ex58.json() if _ex58.status_code == 200 else {}
    _oa58 = requests.get(BASE + "/openapi.json", timeout=60).json()
    _ins58 = {p: sorted(m.upper() for m in (v or {}))
              for p, v in (_oa58.get("paths") or {}).items() if "/installer/" in p}
    _rh58 = {k: int(v) for k, v in (requests.get(BASE + "/api/v1/metrics", timeout=60)
                .json().get("route_hits", {}) or {}).items() if "installer" in k}
    _led58p = Path(_exj58.get("ledger", {}).get("path", "")) if _exj58.get("ledger") else None
    check("EXE(P58): GET /api/v1/installer/execution is compiled into the one-file build and honestly reports ledger_absent on THIS archive's own data root (no execution ever ran here ⇒ reason named, mismatches empty, fabricated=False, the seven self-reported booleans echoed-but-ignored, and the ledger path it looked at is really absent on disk), while the install domain carries ZERO non-GET routes in this artifact's openapi — the executor is unreachable over HTTP and ships only behind the CLI confirm token; route_hits proves the reconciliation face was actually served",
          _ex58.status_code == 200 and _exj58.get("status") == "ledger_absent"
          and _exj58.get("capability") == "installer_execution"
          and _exj58.get("fabricated") is False
          and bool(_exj58.get("reason")) and _exj58.get("mismatches") == []
          and len(_exj58.get("self_report_ignored") or []) == 7
          and _led58p is not None and not _led58p.is_file()
          and all(set(v) == {"GET"} for v in _ins58.values())
          and "/api/v1/installer/execution" in _ins58
          and _rh58.get("/api/v1/installer/execution", 0) > 0,
          json.dumps({"status": _exj58.get("status"),
                      "ledger": (_exj58.get("ledger") or {}).get("path"),
                      "install_routes": _ins58, "hits": _rh58}, ensure_ascii=False))

    # ===== Phase 45: 写面板随冻结 EXE 出货 —— 15 面板结构标记 + 13 个写端点在 EXE 上活体真请求 =====
    # 顺序律同 L2：写侧探测置于全部既有断言之后、P44 回填之前，本块命中全部新增字面串模板。
    _p45e_bad = []
    if not (all(_m in _ui35.text for _m in ('data-panel="write"', 'id="panel-write"',
               'id="wr-cfg-btn"', 'id="wr-pv-cfg-btn"', 'id="wr-pv-health-btn"', 'id="wr-nas-cfg-btn"',
               'id="wr-nas-forget-btn"', 'id="wr-nas-clear-btn"', 'id="wr-uc-btn"', 'id="wr-ck-btn"',
               'id="wr-lic-btn"', 'id="wr-dv-btn"', 'id="wr-tr-btn"', 'id="wr-cr-btn"', 'id="wr-al-btn"'))
           and all(_m in _j39 for _m in ("p45Bad", "/provider/configure", "/provider/health",
               "/nas/configure", "/nas/forget", "/nas/cache/clear", "/update/configure", "/update/check",
               "/license/activate", "/delivery/package", "/typography/resolve", "/tools/capability-report"))):
        _p45e_bad.append("markers")
    if requests.get(BASE + "/api/v1/config", timeout=30).status_code != 200:
        _p45e_bad.append("config-get")
    _k45e = "p45_l4_cfg_key"
    _cpe = requests.put(BASE + "/api/v1/config/" + _k45e, json={"value": 7}, timeout=30)
    if not (_cpe.status_code == 200 and _k45e in (_cpe.json().get("config") or {})):
        _p45e_bad.append("config-put")
    if requests.put(BASE + "/api/v1/config/" + _k45e, json={"nope": 1}, timeout=30).status_code != 422:
        _p45e_bad.append("config-hostile")
    _pke = "sk-p45l4supersecret"
    _pce = requests.post(BASE + "/api/v1/provider/configure", timeout=30, json={
        "provider_id": "p45l4prov", "base_url": "http://127.0.0.1:1", "api_key": _pke, "model": "m1"})
    _pve = (_pce.json().get("providers") or {}).get("p45l4prov") or {}
    if not (_pce.status_code == 200 and _pve.get("has_key") is True
            and "*" in str(_pve.get("api_key_masked")) and _pke not in _pce.text):
        _p45e_bad.append("provider-mask")
    _phe = requests.post(BASE + "/api/v1/provider/health", timeout=30,
                         json={"provider_id": "p45l4prov", "timeout": 3}).json()
    if not (_phe.get("status") == "unreachable" and _phe.get("fabricated") is False):
        _p45e_bad.append("provider-health")
    _she = tmp / "p45share"
    _she.mkdir(exist_ok=True)
    _nce = requests.post(BASE + "/api/v1/nas/configure", timeout=30, json={
        "target_id": "p45l4nas", "target": str(_she), "kind": "filesystem", "share_root": str(_she)})
    _tge = requests.get(BASE + "/api/v1/nas/targets", timeout=30).json()
    _nfe = requests.post(BASE + "/api/v1/nas/forget", timeout=30, json={"target_id": "p45l4nas"})
    _cle = requests.post(BASE + "/api/v1/nas/cache/clear", timeout=30, json={})
    if not (_nce.json().get("configured") is True and "p45l4nas" in json.dumps(_tge)
            and _nfe.json().get("removed") is True and isinstance(_cle.json().get("cleared"), int)):
        _p45e_bad.append("nas-chain")
    _use = "p45l4topsecret"
    _uce = requests.post(BASE + "/api/v1/update/configure", timeout=30, json={
        "channel": "dev", "feed_url": "http://127.0.0.1:1/manifest.json",
        "secret": _use, "current_version": "1.0.0"})
    if not (_uce.json().get("has_secret") is True and _use not in _uce.text):
        _p45e_bad.append("update-mask")
    _uke = requests.post(BASE + "/api/v1/update/check", timeout=30,
                         json={"channel": "dev", "current_version": "1.0.0", "timeout": 8}).json()
    if not (_uke.get("status") == "feed_unreachable" and _uke.get("fabricated") is False):
        _p45e_bad.append("update-check")
    _lce = requests.post(BASE + "/api/v1/license/activate", timeout=30,
                         json={"license_key": "NOTAVALIDKEY"})
    if not (_lce.status_code == 400 and "reason" in _lce.text):
        _p45e_bad.append("license-400")
    _dpe = requests.post(BASE + "/api/v1/delivery/package", timeout=60, json={
        "project": "l4p45proj", "files": {"Source": ["missing/__p45l4__.png"]},
        "notes": "p45 l4 live"}).json()
    _dre = str(_dpe.get("archive") or "")
    if not (_dpe.get("included") == 0 and str(_dpe.get("package_id", "")).startswith("deliv_")
            and int(_dpe.get("archive_size_bytes") or 0) > 0 and Path(_dre).is_file()):
        _p45e_bad.append("delivery")
    _tre = requests.post(BASE + "/api/v1/typography/resolve", timeout=180,
                         json={"missing": "A", "text": "p45 l4 resolve"})
    if not (_tre.status_code == 200 and isinstance(_tre.json().get("candidates"), list)):
        _p45e_bad.append("typo-resolve")  # 候选机器相关，不钉数
    _cre = requests.post(BASE + "/api/v1/tools/capability-report", timeout=30, json={
        "host": "p45l4host", "capabilities": {"ps.doc.place": True}}).json()
    if not (_cre.get("hosts_reported", 0) >= 1 and _cre.get("supported_count", 0) >= 1):
        _p45e_bad.append("cap-report")
    _ale = requests.post(BASE + "/api/v1/assets/__p45l4ghost__/license", timeout=30, json={
        "license_status": "commercial", "license_type": "commercial",
        "commercial_use": True, "verified": False, "warnings": []})
    if not (_ale.status_code == 404 and "不存在" in _ale.text):
        _p45e_bad.append("asset-license-404")
    check("EXE(P45): write panel shipped & live — 15th panel markers compiled into onefile + 13 write endpoints exercised on the frozen exe (config PUT+422 / provider masked configure+health 诚实 unreachable / nas configure→targets→forget→clear / update 不回显密钥+诚实 feed_unreachable / license 敌意 400 / delivery 诚实 included=0 真归档落盘 / typography resolve 候选表 / capability-report 真计数 / assets license 幽灵 404)",
          not _p45e_bad, f"failed={_p45e_bad}")

    # ===== Phase 46: 四主题融合视觉体系随冻结 EXE 出货 —— index 4 色板 + style.css 主题/材质/无障碍标记 =====
    # 纯静态资源断言（零 /api/v1 调用、零新增声称字面串），不进 P44 运行时回填宇宙。
    # 对旧归档必红——P44 模式的可证伪兑现：重打包前跑一次应为诚实 FAIL，重建后转绿。
    _sw46 = _ui35.text.count('class="sw sw-')
    _css46 = requests.get(BASE + "/ui/style.css", timeout=30)
    check("EXE(P46): four-theme fusion system shipped in onefile — GET /ui/style.css 200 carrying mist/celadon/amber theme blocks + glass backdrop-filter + three a11y fallbacks (reduced-motion / reduced-transparency / prefers-contrast), index carrying exactly 4 swatch buttons + zj-theme persistence",
          _css46.status_code == 200 and _sw46 == 4
          and all(f'data-theme-btn="{t}"' in _ui35.text for t in ("night", "mist", "celadon", "amber"))
          and "zj-theme" in _ui35.text
          and all(_m in _css46.text for _m in ('data-theme="mist"', 'data-theme="celadon"',
              'data-theme="amber"', 'backdrop-filter', 'prefers-reduced-motion',
              'prefers-reduced-transparency', 'prefers-contrast')),
          f"css={_css46.status_code} swatches={_sw46}")

    # ===== Phase 67: 玻璃强度三档随冻结 EXE 出货 —— index 控件接线 + style.css soft/off 形制 =====
    # 静态资源断言（零 /api/v1 调用）。L2 另有 served==disk 逐字节判据；此处只证「包内那一份」
    # 真的带着三档控件与降级形制——旧归档必红，故这条判据在重打包前跑一次即是可证伪兑现。
    # 计数律同 L2：只钉声明原文；data-glass 前缀不计数（注释里引用它）。
    _p67e_html = _ui35.text
    _p67e_css = _css46.text
    _p67e_btn = {_b: _p67e_html.count('data-glass-btn="%s"' % _b) for _b in ("full", "soft", "off")}
    _p67e_soft_n = _p67e_css.count('html[data-glass="soft"]')
    _p67e_off_n = _p67e_css.count('html[data-glass="off"]')
    check("EXE(P67): glass intensity control compiled into onefile — index carries exactly one button each for full/soft/off, boot reads zj-glass into html[data-glass], click writes back, two system media queries (reduced-transparency / contrast:more) clamp effective value to off and disable full/soft; style.css carries soft blur/saturation downgrade, off solid+no-backdrop+no-grain, run2 touch-width and narrow-row repairs",
          _ui35.status_code == 200 and all(v == 1 for v in _p67e_btn.values())
          and all(_m in _p67e_html for _m in (
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
          and all(_m in _p67e_css for _m in (
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
          and _p67e_soft_n == 3
          and _p67e_off_n == 11
          and _p67e_css.count('--panel-solid:') == 4,
          f"idx={_ui35.status_code} css={_css46.status_code} btns={_p67e_btn} "
          f"soft={_p67e_soft_n} off={_p67e_off_n}")

    # ===== Phase 69: 呈现层四枚收口随冻结 EXE 出货（图标内联 / 空输出占位 / 长说明折叠 / 接线表面条）=====
    # 纯静态资源断言（零 /api/v1 调用、零新增声称字面串），不进 P44 运行时回填宇宙。
    # 与 L2 的判据同源同腿，只换掉一条：冻结归档里没有可对照的磁盘母本，故「served==disk」换成
    # 「三资产真由本归档服务且非空」。对旧归档必红——这是 P46/P67 同条的可证伪兑现：
    # 串行 --clean 重打包之前，EXE 里编译进去的还是上一代次控制台，红得正是方向。
    # 拒接/转正名单一律由运行时 (_ns41 - _w41) 与 _p71_promoted41 现场派生再剥前缀：P43/P60 逐套
    # 收集器会把测试源里任何以斜杠 api 打头的引号字面串记成本套声称，而拒接项永不发请求 ⇒ 写了必
    # 假红（P48 同条口径）。Phase 71 拒接面清零后，名单人口由 _p71_promoted41（恒 8 枚）承担。
    import urllib.parse as _up69e
    import xml.etree.ElementTree as _et69e
    _p69e_html, _p69e_css, _p69e_js = _ui35.text, _css46.text, _j39
    _p69e_icons = _re41x.findall(r'<link rel="icon"[^>]*href="([^"]*)"', _p69e_html)
    _p69e_icon_any = len(_re41x.findall(r'<link[^>]*rel="icon"', _p69e_html))
    _p69e_svg = _up69e.unquote(_p69e_icons[0]).split(",", 1)[1] if (
        len(_p69e_icons) == 1 and _p69e_icon_any == 1
        and _p69e_icons[0].startswith("data:image/svg+xml,")) else ""
    try:
        _p69e_root = _et69e.fromstring(_p69e_svg) if _p69e_svg else None
    except Exception:
        _p69e_root = None
    _p69e_svg_ok = (_p69e_root is not None and _p69e_root.tag.endswith("svg")
                    and len(list(_p69e_root)) >= 3
                    and _p69e_root.get("viewBox") == "0 0 32 32")
    _p69e_ext = (len(_re41x.findall(r'(?:src|href)="https?://', _p69e_html))
                 + len(_re41x.findall(r'(?:src|href)="https?://', _p69e_js))
                 + len(_re41x.findall(r'url\(\s*["\']?https?:', _p69e_css))
                 + (1 if "@import" in _p69e_css else 0))
    _p69e_out = _re41x.findall(r'<div class="out"[^>]*>(.*?)</div>', _p69e_html, _re41x.S)
    _p69e_out_raw = sum(1 for _x in _p69e_out if not _x.strip())
    _p69e_out_hint = sum(1 for _x in _p69e_out if _re41x.fullmatch(
        r'<span class="muted">[^<]*</span>', _x.strip()) is not None)
    _p69e_ph = ('.out:empty::after {' in _p69e_css and "尚无结果" in _p69e_css)
    _p69e_strip = lambda _s: _re41x.sub(r"<[^>]+>", "", _s).strip()
    _p69e_boxes = _re41x.findall(
        r'<details class="info-box[^"]*">.*?</details>|<div class="info-box"[^>]*>.*?</div>',
        _p69e_html, _re41x.S)
    _p69e_folds = [_re41x.match(
        r'<details class="info-box info-fold"><summary>([^<]*)</summary><div>(.*?)</div></details>',
        _b, _re41x.S) for _b in _p69e_boxes if _b.startswith("<details")]
    _p69e_pairs = [_m.groups() for _m in _p69e_folds if _m]
    _p69e_fold_body = sorted(len(_p69e_strip(_t)) for _s, _t in _p69e_pairs)
    _p69e_plain_body = sorted(
        len(_p69e_strip(_b)) for _b in _p69e_boxes if _b.startswith("<div"))
    _p69e_fold_css = all(_m in _p69e_css for _m in (
        'details.info-fold { padding: 0; }', 'details.info-fold > summary {',
        'details.info-fold > summary::before {', 'details.info-fold[open] > summary::before {',
        'details.info-fold > div {'))
    _p69e_kept = all(_k in _p69e_html for _k in (
        "曾因实证存在落盘副作用", "归入", "Phase 48 归位", "Phase 65 归位",
        # Phase 70/71 两轮把旧拒接项逐枚转正，面板里的历史说明随之归位；换代只认盘上实测
        # （母本量得两串各恰 1 次），并把上一代次的「仍明确不进控制台」移出必在场集合。
        "Phase 70 归位", "Phase 71 归位"))
    _p69e_bar = all(_m in _p69e_js for _m in (
        'class="bar-cell"', '<th>接线进度</th>', 'Math.round((w / all.length) * 100)'))
    _p69e_bar_css = 'td.bar-cell { width: 42%; }' in _p69e_css
    _p69e_num_gone = '<th>已接线/端点数</th>' not in _p69e_js
    _p69e_nw_names = sorted(_p[len("/api/v1"):].lstrip("/") for _p in (_ns41 - _w41))
    # 换代声明（与 L2 同口径变更，非放宽阈值）：Phase 71 把拒接面清零后，「由 notWired 派生名单」
    # 这条腿的种群变成空集——空集上的 all()/差集恒空即恒绿（P58 第十三类），它已无法证明任何事。
    # 故把同一义务搬到**恒非空的八枚转正集合**上：名单由 _p71_promoted41 现场派生（不手抄），
    # 逐名必须仍在面板散文里在册，且逐名必须**不再**出现在拒接集里；拒接面自身的归零由
    # wired==universe==135 的正向计数背书，不是"数到 0 就判绿"。
    _p69e_promoted_names = sorted(_p[len("/api/v1"):].lstrip("/") for _p in _p71_promoted41)
    _p69e_gap = [_n for _n in _p69e_promoted_names if _n not in _p69e_html]
    _p69e_still_refused = [_n for _n in _p69e_promoted_names
                           if _n in set(_p69e_nw_names)]
    _p69e_legs = {
        "assets_served_by_this_archive": (_ui35.status_code == 200 and _css46.status_code == 200
                                          and bool(_p69e_html.strip()) and bool(_p69e_css.strip())
                                          and bool(_p69e_js.strip())),
        "icon_exactly_one": _p69e_icon_any == 1,
        "icon_inlined_svg": _p69e_svg_ok,
        "zero_external_refs": _p69e_ext == 0,
        "out_boxes_present": len(_p69e_out) >= 30,
        "out_boxes_empty_or_hint_only": _p69e_out_raw + _p69e_out_hint == len(_p69e_out),
        "placeholder_rule": _p69e_ph,
        "info_boxes_are_eleven": len(_p69e_boxes) == 11,
        "folds_are_five": len(_p69e_pairs) == 5,
        "plains_are_six": len(_p69e_plain_body) == 6,
        "folded_bodies_long": min(_p69e_fold_body, default=0) >= 280,
        "plain_bodies_short": max(_p69e_plain_body, default=10 ** 9) < 280,
        "summary_text_nonempty": all(_s.strip() for _s, _t in _p69e_pairs),
        "fold_css_five_rules": _p69e_fold_css,
        "disclosure_wording_kept": _p69e_kept,
        "stale_refusal_gone": "明确不接线" not in _p69e_html,
        "coverage_bar_in_js": _p69e_bar,
        "coverage_bar_css": _p69e_bar_css,
        "numeric_header_gone": _p69e_num_gone,
        "promoted_names_derived": len(_p69e_promoted_names) == 8,
        "promoted_ledger_complete": _p69e_gap == [],
        "promoted_not_in_refusal_set": _p69e_still_refused == [],
        "refusal_set_really_empty": len(_p69e_nw_names) == 0,
        "ghost_zero": _g41 == [],
        "wired_135": len(_w41) == 135,
        "notwired_0": len(_ns41 - _w41) == 0,
        "universe_135": len(_w41) + len(_ns41 - _w41) == len(_ns41) == 135,
        "panel_solid_tokens_4": _p69e_css.count('--panel-solid:') == 4,
        "panel_solid_uses_5": _p69e_css.count('background: var(--panel-solid);') == 5,
        "glass_soft_3": _p69e_css.count('html[data-glass="soft"]') == 3,
        "glass_off_11": _p69e_css.count('html[data-glass="off"]') == 11,
        "panels_15": _p69e_html.count('data-panel="') == 15,
        "swatches_4": _p69e_html.count('class="sw sw-') == 4,
    }
    _p69e_red = sorted(_k for _k, _v in _p69e_legs.items() if not _v)
    check("EXE(P69): 呈现层四枚收口编译进冻结归档——① 包内 index 恰好一枚 rel=\"icon\" 且 href 为 "
          "data:image/svg+xml 内联，解码后 XML 可解析为 viewBox=\"0 0 32 32\" 且子元素 ≥3 的 <svg>，"
          "属性式 http(s) 外链 + CSS url(http) + @import 命中总数恒 0（打包版也零网络字体/零外链）；"
          "② 包内每个 class=\"out\" 输出区未发请求时要么裸空、要么只带一条静态 muted 说明，两腿之和等于"
          "总数且「尚无结果」占位原文在 style.css 里在场；③ 十一枚 info-box 里正文 ≥280 字者（实测五枚）"
          "一律折进 <details class=\"info-box info-fold\">（summary 非空、正文逐字保留、五条折叠样式原文在场），"
          "未折叠者（实测六枚）正文最高仍 <280；④ 概览按域表以与 wired/all 同源的 .bar 面条呈现，旧裸数字表头"
          "「已接线/端点数」必须不在场；另钉**口径变更换代**（P71 拒接面归零后原「八枚拒接端点逐名在册」腿的"
          "种群成空集、退化为空真，故把同一义务搬到恒非空的八枚转正集合）：名单由 P71 转正集现场派生（不手抄）、"
          "逐名仍须在写侧说明里在册、逐名必须不再出现在拒接集里，且拒接集自身经 wired==universe==135 的正向"
          "计数归零；另钉 135/135/0/ghost[] 宇宙与 P66/P67 的等值计数未被动摇；"
          "三十三腿逐名求值，detail 自带 red=[失败腿名]——对旧归档必红即本轮的可证伪兑现",
          all(_p69e_legs.values()) and len(_p69e_legs) == 33,
          f"red={_p69e_red} ext={_p69e_ext} icon={_p69e_icon_any}/{len(_p69e_icons)} "
          f"svg_ok={_p69e_svg_ok} out={len(_p69e_out)}/{_p69e_out_raw}+{_p69e_out_hint} "
          f"boxes={len(_p69e_boxes)}/{len(_p69e_pairs)} fold_body={_p69e_fold_body[:3]} "
          f"plain_body={_p69e_plain_body} nw={len(_p69e_nw_names)} gap={_p69e_gap[:3]} "
          f"bar={_p69e_bar}/{_p69e_bar_css}/{_p69e_num_gone} "
          f"html={len(_p69e_html)}B css={len(_p69e_css)}B js={len(_p69e_js)}B")

    # ===== Phase 69c: 控制台三资产不可被启发式缓存 —— 冻结归档必须自带这条服务端修法 =====
    # 与 L2 的 webui(P69c) 同源同腿名（11 枚，一一对得上），只换一处含义：这里的
    # served_eq_disk_3 比的是「包内字节 == 仓库 outputs/webui/ 母本字节」，即 onefile 里编译进去的
    # 那份确实出自当前母本（P35 起 EXE 从 _MEIPASS 自服务，比较有意义）。
    # 对旧归档必红：P67 封版归档没有 cache-control，红在 explicit_cc_3/cc_no_cache_3——那正是
    # 串行 --clean 重打包前的可证伪兑现。实测依据（盘上收据在场）：p69_r11b_r10_readback.txt 量到
    # cache-control=null + etag/last-modified 在场 + Date−Last-Modified=11.4 小时 ⇒ 启发式新鲜度
    # ≈68.5 分钟、全程零 304；p69_r12b_silent_proxy_probe.json 另两条独立通道复量 cc=null。
    _p69ce_targets = (("/", _ui35, OUT / "webui" / "index.html", "text/html"),
                      ("/ui/app.js", _uij35, OUT / "webui" / "app.js", "text/javascript"),
                      ("/ui/style.css", _css46, OUT / "webui" / "style.css", "text/css"))
    _p69ce_rows = []
    for _p69ce_path, _p69ce_plain, _p69ce_disk, _p69ce_ct in _p69ce_targets:
        _p69ce_h = {str(_k).lower(): _v for _k, _v in dict(_p69ce_plain.headers).items()}
        _p69ce_cond = requests.get(
            BASE + _p69ce_path, timeout=30,
            headers={"if-none-match": _p69ce_h.get("etag") or '"zaojing-absent"'})
        _p69ce_bogus = requests.get(
            BASE + _p69ce_path, timeout=30,
            headers={"if-none-match": '"zaojing-not-a-validator"'})
        _p69ce_ims = requests.get(
            BASE + _p69ce_path, timeout=30,
            headers={"if-modified-since": _p69ce_h.get("last-modified") or "Mon, 01 Jan 1990 00:00:00 GMT"})
        _p69ce_rows.append({"path": _p69ce_path, "cc": _p69ce_h.get("cache-control"),
            "etag": _p69ce_h.get("etag"), "lm": _p69ce_h.get("last-modified"),
            "ct": _p69ce_h.get("content-type") or "", "want_ct": _p69ce_ct,
            "disk_eq": _p69ce_plain.content == _p69ce_disk.read_bytes(),
            "cond_status": _p69ce_cond.status_code, "cond_len": len(_p69ce_cond.content),
            "ims_status": _p69ce_ims.status_code, "bogus_status": _p69ce_bogus.status_code,
            "bogus_disk_eq": _p69ce_bogus.content == _p69ce_disk.read_bytes()})
    _p69ce_legs = {
        "population_3": len(_p69ce_rows) == 3,
        "types_3": all(_r["want_ct"] in _r["ct"] for _r in _p69ce_rows),
        "explicit_cc_3": all(_r["cc"] is not None for _r in _p69ce_rows),
        "cc_no_cache_3": all("no-cache" in (_r["cc"] or "") for _r in _p69ce_rows),
        "validators_3": all(_r["etag"] and _r["lm"] for _r in _p69ce_rows),
        "served_eq_disk_3": all(_r["disk_eq"] for _r in _p69ce_rows),
        "revalidate_304_3": all(_r["cond_status"] == 304 for _r in _p69ce_rows),
        "revalidate_304_empty_3": all(_r["cond_len"] == 0 for _r in _p69ce_rows),
        "if_modified_since_304_3": all(_r["ims_status"] == 304 for _r in _p69ce_rows),
        "bogus_etag_200_3": all(_r["bogus_status"] == 200 for _r in _p69ce_rows),
        "bogus_full_body_3": all(_r["bogus_disk_eq"] for _r in _p69ce_rows)}
    _p69ce_red = sorted(_k for _k, _v in _p69ce_legs.items() if not _v)
    check("EXE(P69c): 冻结归档里的控制台三资产（GET / 文档 + /ui/app.js + /ui/style.css）一律不可被"
          "启发式缓存——① 种群恰三枚且 content-type 各就各位（拿到的就是那三枚资产）；② 三枚全部显式"
          "携带 cache-control 且含 no-cache（缺席即交回 RFC 9111 启发式新鲜度，本机实测 Date−"
          "Last-Modified=11.4 小时 ⇒ 浏览器可约 68.5 分钟不回源，那是修前归档的实况）；③ etag 与 "
          "last-modified 两把校验器同时在场（no-cache 不是 no-store，省的是陈旧一页不是带宽）；"
          "④ 带匹配 If-None-Match 必须回 304 且空体，带 If-Modified-Since 同样回 304（修前文档这条"
          "腿连回源复核都没兑现，带匹配校验器仍回 66,195 B 全文 200）；⑤ 带一枚不匹配的假校验器必须"
          "回 200 全文——「一律 304」的假修由此腿拒接；⑥ 无条件与假校验器两条正文均与仓库 "
          "outputs/webui/ 母本逐字节相等（修缓存头不许动前端字节，且包内那份确出现行母本）。"
          "十一腿逐名求值，detail 自带 red=[失败腿名]；腿名与 L2 的 webui(P69c) 一一对得上",
          all(_p69ce_legs.values()) and len(_p69ce_legs) == 11,
          f"red={_p69ce_red} n={len(_p69ce_rows)} cc={[(_r['path'], _r['cc']) for _r in _p69ce_rows]} "
          f"cond={[(_r['path'], _r['cond_status'], _r['cond_len']) for _r in _p69ce_rows]} "
          f"ims={[_r['ims_status'] for _r in _p69ce_rows]} "
          f"bogus={[(_r['path'], _r['bogus_status'], _r['bogus_disk_eq']) for _r in _p69ce_rows]} "
          f"disk_eq={[_r['disk_eq'] for _r in _p69ce_rows]}")

    # ===== Phase 70: 品牌语义 + 安装台账两枚原拒接项，编译进冻结归档 =====
    # 与 L2 的 webui(P70) 同源同腿（十六腿一一对得上），只多一条：冻结归档没有可对照的磁盘母本，
    # 故把「资产由本归档真服务且非空」作为第十七腿。对旧归档必红——串行 --clean 重打包之前，
    # onefile 里编译进去的还是上一代次控制台（陈旧静态卡在场、bsem-*/iex-* 全缺席、wired 仍 127/8
    # 的另一侧 125/10），红得正是方向。Phase 71 把拒接面清零后，本块的两条宇宙腿随之改钉
    # wired==135 / notWired==0（口径变更：分母恒 135，只挪接线面），归零由正向计数背书。
    # 端点字面串一律由 '/'+'api/v1' 拼接构造：P43/P60 逐套收集器会把整串记成本套声称并要求运行时
    # 命中，而本块只做静态断言（P48 同条口径）。
    _p70e_html, _p70e_js, _p70e_css = _ui35.text, _j39, _css46.text
    _p70e_ids = ("bsem-path", "bsem-provider", "bsem-prompt", "bsem-run",
                 "bsem-out", "bsem-boundary", "iex-run", "iex-out")
    _p70e_shapes = ("ledger_absent", "executed_verified",
                    "install_verified_awaiting_uninstall",
                    "ledger_unverified", "ledger_inconsistent")
    _p70e_ext = (len(_re41x.findall(r'(?:src|href)="https?://', _p70e_html))
                 + len(_re41x.findall(r'(?:src|href)="https?://', _p70e_js))
                 + len(_re41x.findall(r'url\(\s*["\']?https?:', _p70e_css))
                 + (1 if "@import" in _p70e_css else 0))
    _p70e_missing = [_i70e for _i70e in _p70e_ids
                     if ('id="' + _i70e + '"') not in _p70e_html]
    _p70e_legs = {
        "assets_served_by_this_archive": (_ui35.status_code == 200
                                          and _css46.status_code == 200
                                          and bool(_p70e_html.strip())
                                          and bool(_p70e_js.strip())
                                          and bool(_p70e_css.strip())),
        "brand_ids_once": all(_p70e_html.count('id="%s"' % _i70e) == 1
                              for _i70e in _p70e_ids),
        "stale_semantic_claim_gone":
            "服务端诚实返回 needs_external，本界面原样呈现、不伪造" not in _p70e_html,
        "boundary_live": ('api("/brand/semantics")' in _p70e_js
                          and "provider_configured" in _p70e_js
                          and "brand: refreshBrandBoundary" in _p70e_js),
        "insight_post_call": 'Post("/brand/semantic-insight"' in _p70e_js,
        "insight_honest_502": all(_m70e in _p70e_js for _m70e in (
            "r.status === 502", '"needs_external"', "fabricated", "/provider/configure")),
        "insight_passthrough": all(_m70e in _p70e_js for _m70e in (
            "真实透传", "j.semantics", "模型输出逐字透传")),
        "insight_4xx_honest": "errBox(r)" in _p70e_js,
        "ledger_card": ('id="iex-run"' in _p70e_html and 'id="iex-out"' in _p70e_html
                        and 'api("/installer/execution")' in _p70e_js),
        "ledger_five_shapes": all(_s70e in _p70e_js for _s70e in _p70e_shapes),
        "ledger_fields": all(_m70e in _p70e_js for _m70e in (
            "self_report_ignored", "registry_keys", "mismatches", "led.exists")),
        "wired_135": len(_w41) == 135,
        # 判据文本与 len(...) == 17 都要求 here 有十七条腿；早期此块以 assets 腿替换了
        # L2 的 notwired_8 却漏补回，十六条腿全绿仍恒红（p70_9_L4a/b 留册的仪器缺陷）。
        # 修法是把这条腿补回来（加严到十七），不是把期望从 17 降到 16。
        # Phase 71 换代（口径变更，非放宽阈值）：拒接面清零后此腿由 ==8 改钉 ==0；归零由
        # wired_135 与 universe_135 的正向计数背书，不是"数到 0 就判绿"，且八枚转正路由另在
        # EXE(P69e) 的 promoted_* 四腿上以恒非空种群作见证。
        "notwired_0": len(_ns41 - _w41) == 0,
        "universe_135": len(_w41) + len(_ns41 - _w41) == len(_ns41) == 135,
        "ghost_zero": _g41 == [],
        "two_promoted": {"/api/v1" + "/brand/semantic-insight",
                         "/api/v1" + "/installer/execution"} <= _w41,
        "zero_external_refs": _p70e_ext == 0,
    }
    _p70e_red = sorted(_k70e for _k70e, _v70e in _p70e_legs.items() if not _v70e)
    check("EXE(P70): 品牌语义 + 安装台账两枚原拒接项随冻结归档出货——包内 index 的陈旧静态卡"
          "（写死 needs_external「本界面原样呈现、不伪造」）必须不在场，bsem-* 六枚 id 各恰一次，"
          "包内 app.js 带 provider_configured 现场派生边界（brand: refreshBrandBoundary）与显式点击"
          "才发的 Post(/brand/semantic-insight)；200 路径含 j.semantics +「真实透传」+「模型输出逐字"
          "透传」，502 路径四要素（r.status === 502 / needs_external / fabricated / /provider/configure）"
          "齐备，其余状态 errBox 原样呈现；包内 iex-run/iex-out 只调只读 api(/installer/execution)，"
          "五形制逐名在场、self_report_ignored/registry_keys/mismatches/led.exists 逐字段回显，且没有"
          "触发真装真卸的按钮。另钉 135/0/135/ghost[] 宇宙与两枚转正路由确在 wired、包内三资产真由"
          "本归档服务且非空、零外链。十七腿逐名求值，detail 自带 red=[失败腿名]——旧归档必红即可证伪兑现",
          all(_p70e_legs.values()) and len(_p70e_legs) == 17,
          f"red={_p70e_red} missing_ids={_p70e_missing} ext={_p70e_ext} "
          f"wired={len(_w41)} not={len(_ns41 - _w41)} "
          f"html={len(_p70e_html)}B js={len(_p70e_js)}B css={len(_p70e_css)}B")

    # ===== Phase 71: 接线收尾八枚（runtime 锁 / uxp 桥接诊断 / NAS 下载 / 更新下载与应用）
    # 编译进冻结归档 =====
    # 与 L2 的 webui(P71) 同源同腿（十九腿一一对得上），只多一条：冻结归档没有可对照的磁盘母本，
    # 故把「资产由本归档真服务且非空」作为第二十腿。对旧归档必红——串行 --clean 重打包之前，
    # onefile 里编译进去的还是上一代次控制台（nsl-*/uxp-*/nasd-*/upd-* 全缺席、wired 仍 127/8），
    # 红得正是方向。契约形制由 p71_1b_contract_receipt.txt 冻结；L2 侧另有 webui(P71-live)
    # 活体降级形制腿，本层打的是源码服务（P37/P40/P26 各块已活体覆盖八枚 happy path），
    # 归档侧只钉静态接线面与覆盖分母。
    # 端点字面串一律由 '/'+'api/v1' 拼接构造：P43/P60 逐套收集器会把整串记成本套声称并要求
    # 运行时命中，而本块只做静态断言（P48 同条口径）。
    _p71e_html, _p71e_js, _p71e_css = _p70e_html, _p70e_js, _p70e_css
    _p71e_ids = ("nsl-run", "nsl-out",
                 "uxp-token", "uxp-hand", "uxp-poll", "uxp-disp", "uxp-res", "uxp-out",
                 "nasd-tid", "nasd-sub", "nasd-run", "nasd-out",
                 "upd-chan", "upd-dl", "upd-tgt", "upd-sha", "upd-apply", "upd-out")
    _p71e_ext = (len(_re41x.findall(r'(?:src|href)="https?://', _p71e_html))
                 + len(_re41x.findall(r'(?:src|href)="https?://', _p71e_js))
                 + len(_re41x.findall(r'url\(\s*["\']?https?:', _p71e_css))
                 + (1 if "@import" in _p71e_css else 0))
    _p71e_ua_m = _re41x.search(r'(?:async )?function updApply\s*\(', _p71e_js)
    _p71e_ua_body = _p71e_js[_p71e_ua_m.start():_p71e_ua_m.start() + 3000] if _p71e_ua_m else ""
    _p71e_missing = [_i71e for _i71e in _p71e_ids
                     if ('id="' + _i71e + '"') not in _p71e_html]
    _p71e_legs = {
        "assets_served_by_this_archive": (_ui35.status_code == 200
                                          and _css46.status_code == 200
                                          and bool(_p71e_html.strip())
                                          and bool(_p71e_js.strip())
                                          and bool(_p71e_css.strip())),
        "ids_once": all(_p71e_html.count('id="%s"' % _i71e) == 1 for _i71e in _p71e_ids),
        "nsl_call": all(_m71e in _p71e_js for _m71e in (
            'Post("/runtime/single-instance"', '"acquire"', '"release"',
            "j.schema", "j.acquired", "j.backend", "errBox(r)")),
        "uxp_calls": all(_m71e in _p71e_js for _m71e in (
            'Post("/uxp/handshake"', 'Post("/uxp/poll"',
            'Post("/uxp/dispatch"', 'Post("/uxp/result"')),
        "uxp_502_honest": all(_m71e in _p71e_js for _m71e in (
            "r.status === 502", "auth_failed", "no_host_connected",
            "capability_not_negotiated", "fabricated", "errBox(r)")),
        "uxp_token_input": '$("uxp-token")' in _p71e_js,
        "uxp_poll_honest": all(_m71e in _p71e_js for _m71e in (
            "j.commands", "j.count", "无待执行命令")),
        "uxp_result_honest": "unknown_request" in _p71e_js,
        "uxp_dispatch_real_tool": "ps.document.get" in _p71e_js,
        "nasd_call": 'Post("/nas/download"' in _p71e_js,
        "nasd_honest": all(_m71e in _p71e_js for _m71e in (
            'j.status === "forbidden"', 'j.status === "not_configured"',
            "j.downloaded", "j.sha256", "errBox(r)")),
        "upd_download_call": all(_m71e in _p71e_js for _m71e in (
            'Post("/update/download"', "no_update_configured", "j.verified", "j.downloaded")),
        "upd_apply_call": all(_m71e in _p71e_js for _m71e in (
            'Post("/update/apply"', "integrity_failed", "j.verified", "j.applied")),
        "upd_apply_double_confirm": (_p71e_ua_m is not None
                                     and _p71e_ua_body.count("confirm(") >= 2
                                     and 'Post("/update/apply"' in _p71e_ua_body),
        "wired_135": len(_w41) == 135,
        "notwired_0": len(_ns41 - _w41) == 0,
        "universe_135": len(_w41) + len(_ns41 - _w41) == len(_ns41) == 135,
        "ghost_zero": _g41 == [],
        "eight_promoted": {"/api/v1" + "/nas/download",
                           "/api/v1" + "/runtime/single-instance",
                           "/api/v1" + "/update/apply",
                           "/api/v1" + "/update/download",
                           "/api/v1" + "/uxp/dispatch",
                           "/api/v1" + "/uxp/handshake",
                           "/api/v1" + "/uxp/poll",
                           "/api/v1" + "/uxp/result"} <= _w41,
        "zero_external_refs": _p71e_ext == 0,
    }
    _p71e_red = sorted(_k71e for _k71e, _v71e in _p71e_legs.items() if not _v71e)
    check("EXE(P71): 接线收尾八枚随冻结归档出货——包内 index 的 nsl-run/nsl-out（运行时上锁卡）、"
          "uxp-token/hand/poll/disp/res/out（桥接诊断卡，token 由用户粘贴、502 三形 "
          "auth_failed/no_host_connected/capability_not_negotiated 逐一认出并原样呈现 fabricated、"
          "poll 空命令诚实「无待执行命令」、dispatch 只发真工具 ps.document.get、result 如实 "
          "unknown_request）、nasd-tid/sub/run/out（NAS 下载卡，forbidden/not_configured 内联降级"
          "逐形认出、downloaded/sha256 回显）、upd-chan/dl/tgt/sha/apply/out（更新卡，upd-dl 发 "
          "/update/download 的 no_update_configured 诚实、upd-apply 过两道 confirm 才发 "
          "/update/apply）十八枚 id 各恰一次，包内 app.js 四组调用串与逐字段回显/errBox 齐备。"
          "另钉 135/0/135/ghost[] 宇宙与八枚转正路由确在 wired、包内三资产真由本归档服务且非空、"
          "零外链。二十腿逐名求值，detail 自带 red=[失败腿名]——旧归档必红即可证伪兑现",
          all(_p71e_legs.values()) and len(_p71e_legs) == 20,
          f"red={_p71e_red} missing_ids={_p71e_missing} ext={_p71e_ext} "
          f"wired={len(_w41)} not={len(_ns41 - _w41)} "
          f"html={len(_p71e_html)}B js={len(_p71e_js)}B css={len(_p71e_css)}B")

    # ---- Phase 50: Hub 的"最新评测"行取自列表端点 —— 冻结 EXE 必须真的服务它 ----
    # P44 表示一致性：本文件新钉的 "/api/v1/eval/reports" 字面串须由**自己的请求**背书，
    # 不能只在断言里引用 Hub 评测台内嵌夹具发出的 GET（那是另一个进程的计数器）。
    _rpt50 = requests.get(BASE + "/api/v1/eval/reports", timeout=60).json()
    _rpt50l = _rpt50.get("reports", [])
    check("EXE(P50): GET /api/v1/eval/reports served by the frozen exe returns exactly the summary envelope the Hub reads (report_id/finished_at/honest/pass_rate/executed_cases, newest first) — this file's own new literal backed by its own runtime request, not by the eval fixture's in-process GETs",
          isinstance(_rpt50l, list) and len(_rpt50l) >= 1
          and all({"report_id", "finished_at", "honest", "pass_rate",
                   "executed_cases"} <= set(r) for r in _rpt50l)
          and [r.get("finished_at") or "" for r in _rpt50l] == sorted(
              (r.get("finished_at") or "" for r in _rpt50l), reverse=True),
          json.dumps({"n": len(_rpt50l),
                      "keys": sorted(_rpt50l[0]) if _rpt50l else []}, ensure_ascii=False))

    # ===== Phase 52: 反阻塞仪表与并发诚实契约 —— 交付物（冻结 EXE）里也真在跑 =====
    # L6 打的是源码服务；本层把同一套声称钉到 onefile 上，因为"源码里改了"不等于"归档里带上了"。
    # 三枚可证伪点：
    #  ① loop_lag 看门狗在冻结态是活的：samples 在 0.6 s 内必须增长（常量桩与未启动的任务都涨不动），
    #     running==1，interval/window 与产品常量同值，recent_ms 长度不得超过 window；
    #  ② batch 的 worker_cap 由本文件**独立复算** max(1,min(16,cpu_count)) 再与服务自报对照——
    #     服务把上限写成 999 在它自己的响应里依然自洽，只有第二路派生能证伪；
    #  ③ 批处理诚实契约在冻结态成立：请求 8 个工作者、实得 1，worker_policy 逐字
    #     cpu_gil_bound_serialized，parallel_efficiency 可由响应自带的逐件耗时与墙钟独立复算，
    #     3 张真 PNG 逐件 success 且产物真落盘（串行≠漏件），服务级闸门下 peak_concurrency 恒 1、
    #     收尾 in_flight 归 0。
    _cap52 = max(1, min(16, os.cpu_count() or 4))
    _p52files = []
    for _i52 in range(3):
        _f52 = tmp / ("p52_batch_%d.png" % _i52)
        Image.new("RGB", (48, 48), (0, 128, 255) if _i52 == 0 else (255, 255, 255)).save(_f52)
        _p52files.append(str(_f52))
    _m52a = requests.get(BASE + "/api/v1/metrics", timeout=60).json()
    _la52, _ba52 = _m52a.get("loop_lag") or {}, _m52a.get("batch") or {}
    time.sleep(0.6)
    _m52b = requests.get(BASE + "/api/v1/metrics", timeout=60).json()
    _lb52 = _m52b.get("loop_lag") or {}
    _r52 = requests.post(BASE + "/api/v1/batch/process",
                         json={"file_paths": list(_p52files),
                               "operation": "remove_background", "workers": 8},
                         timeout=300).json()
    _bd52 = requests.get(BASE + "/api/v1/metrics", timeout=60).json().get("batch") or {}
    _res52 = _r52.get("results") or []
    _outs52 = [r.get("output") for r in _res52]
    _eff52 = (round(sum(float(r.get("time") or 0.0) for r in _res52)
                    / float(_r52.get("total_time")), 3)
              if _r52.get("total_time") else None)
    check("EXE(P52): the two anti-blocking instruments are LIVE inside the frozen archive — loop_lag watchdog reports running=1 / interval_ms 50.0 / window 200 and its sample counter STRICTLY increases across a 0.6 s gap (a constant stub or an un-started task cannot grow), recent_ms never exceeds the ring window; batch.worker_cap equals THIS machine's max(1,min(16,cpu_count)) re-derived by the test, not trusted from the service",
          _la52.get("running") == 1 and _la52.get("interval_ms") == 50.0
          and _la52.get("window") == 200
          and isinstance(_la52.get("samples"), int) and isinstance(_lb52.get("samples"), int)
          and _lb52["samples"] > _la52["samples"]
          and len(_lb52.get("recent_ms") or []) <= (_lb52.get("window") or 200)
          and _ba52.get("worker_cap") == _cap52 and _ba52.get("in_flight") == 0,
          f"lag {json.dumps({k: _la52.get(k) for k in ('running', 'interval_ms', 'window', 'samples')}, ensure_ascii=False)} -> samples={_lb52.get('samples')} ring={len(_lb52.get('recent_ms') or [])} | batch(cap_self={_ba52.get('worker_cap')} rederived={_cap52} in_flight={_ba52.get('in_flight')})")
    check("EXE(P52): the batch honesty contract ships in the onefile — requesting 8 workers for a CPU/GIL-bound operation GRANTS 1 (workers_requested=8 / workers_applied=workers=1 / worker_policy=='cpu_gil_bound_serialized' / worker_cap re-derived), parallel_efficiency is recomputable from the response's own per-file times over wall clock, all 3 real PNGs succeeded with transparent outputs actually on disk (serialized never means dropped), and the service-wide gate kept batch.peak_concurrency at EXACTLY 1 with in_flight back to 0",
          _r52.get("workers_requested") == 8 and _r52.get("workers_applied") == 1
          and _r52.get("workers") == 1 and _r52.get("worker_cap") == _cap52
          and _r52.get("worker_policy") == "cpu_gil_bound_serialized"
          and isinstance(_r52.get("parallel_efficiency"), float) and _eff52 is not None
          and abs(_r52["parallel_efficiency"] - _eff52) <= 0.05
          and 0.5 <= _r52["parallel_efficiency"] <= 1.5
          and _r52.get("success") == 3 and _r52.get("failed") == 0 and len(_res52) == 3
          and all(p and Path(p).is_file() and Path(p).read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
                  for p in _outs52)
          and _bd52.get("peak_concurrency") == 1 and _bd52.get("in_flight") == 0,
          f"{json.dumps({k: _r52.get(k) for k in ('workers', 'workers_requested', 'workers_applied', 'worker_cap', 'worker_policy', 'parallel_efficiency', 'success', 'failed', 'total_time')}, ensure_ascii=False)} eff_rederived={_eff52} outs={[Path(p).name if p else None for p in _outs52]} batch_after={json.dumps(_bd52, ensure_ascii=False)}")

    # ===== Phase 44: runtime backfill on FROZEN EXE —— L4 自身源码声称的每条 {} 归一
    # 模板须在 EXE 的 /metrics route_hits 命中>0（P43 申报界限的打包面收口）+ 增量校准
    # + 幽灵负控。P44 前旧归档无 route_hits → 该检查必红（可证伪实证，见 _p44_l4_pre.out）。
    # 幽灵串不写成引号包裹的 /api/v1 字面串——抽取器无注释盲区，整体字面会被吸成伪声称。
    import re as _re44
    _n44 = lambda p: _re44.sub(r"\{[^{}]*\}", "{}", p).rstrip("/") or "/"
    _tp44 = [_n44(p) for p in requests.get(BASE + "/openapi.json", timeout=60).json()["paths"]
             if p.startswith("/api/v1/")]

    def _spec44(_s):
        _best, _bsc = None, -1
        _ssegs = _s.split("/")
        for _tp in _tp44:
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

    _cl44 = set()
    _lt44 = set()
    _txt44 = Path(__file__).resolve().read_text(encoding="utf-8")
    for _pat in ('"(/api/v1[^"\\n]*)"', "'(/api/v1[^'\\n]*)'", "`(/api/v1[^`]*)`"):
        _lt44.update(_m.group(1) for _m in _re44.finditer(_pat, _txt44))
    for _raw in _lt44:
        _s = _n44(_raw.split("#")[0].split("?")[0])
        if _s in ("/", "/api/v1"):
            continue
        _m = _spec44(_s)
        if _m:
            _cl44.add(_m)
    requests.get(BASE + "/api/v1/metrics", timeout=60)
    requests.get(BASE + "/api/v1/eval/suite", timeout=120)
    _mh44 = requests.get(BASE + "/api/v1/metrics", timeout=60).json().get("route_hits")
    _rh44 = {_n44(k): v for k, v in (_mh44 or {}).items()} if isinstance(_mh44, dict) else None
    _miss44 = sorted(p for p in _cl44 if not _rh44 or _rh44.get(p, 0) <= 0)
    # 增量校准：再调一次 suite，新快照须恰 +1（杀"硬编码计数字典"假仪器）
    _su44a = _rh44.get("/api/v1/eval/suite", 0) if _rh44 else 0
    requests.get(BASE + "/api/v1/eval/suite", timeout=120)
    _rh44b = {_n44(k): v for k, v in (requests.get(BASE + "/api/v1/metrics", timeout=60).json().get("route_hits") or {}).items()}
    _ph44 = "/api/v1/" + "__p44_no_route__"
    _php44 = requests.get(BASE + _ph44, timeout=60).status_code
    _rh44c = {_n44(k): v for k, v in (requests.get(BASE + "/api/v1/metrics", timeout=60).json().get("route_hits") or {}).items()}
    _bad44 = []
    if _rh44 is None:
        _bad44.append("route_hits 字段缺失(旧归档=可证伪预期)")
    else:
        if _su44a + 1 != _rh44b.get("/api/v1/eval/suite", 0):
            _bad44.append(f"增量校准失败(suite {_su44a}->{_rh44b.get('/api/v1/eval/suite')})")
        if _ph44 in _rh44c or _php44 != 404:
            _bad44.append("幽灵路由被计数或 HTTP 非 404")
        if not _rh44.get("/api/v1/metrics"):
            _bad44.append("warm call 未计入 /api/v1/metrics")
    check("EXE runtime-backfill(P44): every normalized /api/v1 template claimed by THIS file's own literals shows route_hits>0 in frozen exe /metrics + increment calibration + phantom negative",
          not _bad44 and _miss44 == [],
          f"claims={len(_cl44)} hits={len(_rh44) if _rh44 else 'N/A'} miss={_miss44[:8]} bad={_bad44}")

    # ---- Phase 50: 冻结 EXE 的 hub 子命令 —— 第 27 引擎的 GUI 本体在 onefile 里真立起来 ----
    # 三层证据：① 构建期 TOC 字面（tkinter/_tkinter/tcl/tk 确被打进包）；
    # ② 进程级：另起 ZaojingAI.exe hub --window-probe 子进程，连的是**本轮正在服务**的冻结
    #    EXE(8877)，真建窗真销毁，退出码与 stdout 都由归档自身的 CLI 分支产出（非本 harness 代打）；
    # ③ 服务端计数仪：hub 全程只发只读 GET，POST /eval/run 增量恒 0 —— "开窗只读"在冻结态同样成立。
    # 关窗动作绝不自动化：window-probe 只建窗读 Label，不 invoke 任何按钮（clicks 恒空即此证）。
    # 两类条目**分属两份 TOC**（实测本轮构建目录）：DLL 与 tcl/tk 脚本树这类二进制记在
    # EXE-00.toc，纯 Python 模块记在 PYZ-00.toc（onefile 把它们打进 .pyz 归档）。
    # 只在 EXE-00.toc 里找 desktop_hub_engine 会把"模块确实在包内"误判成缺——P50 L4 首跑
    # 的红就是这么来的（仪器用错了账本，不是产品没打包）。tkinter 两处都查：EXE 侧是
    # tcl86t/tk86t 运行库，PYZ 侧是 tkinter 纯 Python 包本体。
    _exe_toc50 = OUT / "build" / "ZaojingAI" / "EXE-00.toc"
    _pyz_toc50 = OUT / "build" / "ZaojingAI" / "PYZ-00.toc"
    _exe50text = _exe_toc50.read_text(encoding="utf-8", errors="replace") if _exe_toc50.exists() else ""
    _pyz50text = _pyz_toc50.read_text(encoding="utf-8", errors="replace") if _pyz_toc50.exists() else ""
    _tk50 = {("EXE:" + t): (t in _exe50text) for t in ("tkinter", "_tkinter", "tcl86t", "tk86t",
                                                       "_tcl_data", "_tk_data")}
    _tk50.update({("PYZ:" + t): (t in _pyz50text)
                  for t in ("desktop_hub_engine", "tkinter")})
    check("EXE(P50): the 27th engine and its GUI runtime are really inside the frozen archive — EXE-00.toc carries tkinter/_tkinter/tcl86t/tk86t/_tcl_data/_tk_data (binary+script tree) AND PYZ-00.toc carries the desktop_hub_engine module itself (function-level import still collected by modulegraph; no hidden-import flag used)",
          _exe_toc50.exists() and _pyz_toc50.exists() and all(_tk50.values()),
          json.dumps(_tk50, ensure_ascii=False))

    def _hub50run(args, env_extra=None, dec="ascii"):
        # 子进程必须**有界**：旧归档没有 hub 分支时，`hub --selftest` 会被 main() 当成
        # 普通启动参数（起第二个服务、抢 8877 失败），可能长时间不返回。这里 120s 封顶并
        # 把超时如实记成 rc=None —— 判据照样红，但 harness 不会整棵死掉（P26 教训：onefile
        # 树要 /T 强杀，超时后子进程树同样要清）。
        # stdout / stderr 必须**分开收**：契约是"--json 模式的 stdout 是一份可直接解析的
        # JSON"，而 import 期的 DeprecationWarning（on_event 弃用）走 stderr。早先把
        # stderr 并进 stdout，警告第一行就吃掉了 `{`，把一条本来成立的契约判成 FAIL——
        # 判卷侧的合并流是假红源，分开收才是对的仪器（同 P43/P48 的判卷侧自纠）。
        _e = dict(os.environ)
        _e.pop("ZAOJING_HUB_HEADLESS", None)
        _e.update(env_extra or {})
        try:
            _cp = subprocess.run([str(port_exe)] + list(args), cwd=str(PORT_DIR), env=_e,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
        except Exception as _eh50:  # noqa: BLE001
            try:
                subprocess.run(["taskkill", "/PID", str(_eh50.__dict__.get("process", None)
                                                     and _eh50.process.pid), "/T", "/F"],
                               capture_output=True)
            except Exception:
                pass
            return None, "%s: %s" % (type(_eh50).__name__, _eh50), ""
        return (_cp.returncode, _cp.stdout.decode(dec, "replace"),
                _cp.stderr.decode("utf-8", "replace"))

    _pre50h = {k: int(v) for k, v in (requests.get(BASE + "/api/v1/metrics", timeout=60)
                   .json().get("route_hits", {}) or {}).items()}
    _rc50a, _so50a, _se50a = _hub50run(["hub", "--selftest"], env_extra={"ZAOJING_PORT": "8877"})
    _j50a = json.loads(_so50a) if _so50a.lstrip().startswith("{") else {}
    check("EXE(P50+P53+P56+P58): `ZaojingAI.exe hub --selftest` on the frozen archive runs the data layer WITHOUT ever touching tkinter — exit 0, base url resolved from ZAOJING_PORT (source env:ZAOJING_PORT, not invented), all three sources live, degraded [], fabricated False, window block is exactly {requested: False}, the rows carry THIS exe's own 30/30 engines + 43/40/3/0/0 truth table (Phase 53 moved 39/34→41/36 by转真 the two installer faces; Phase 56 moved supported 36→37 / not_shipped 2→1 by转真 brand_semantic_recognition through the real Provider vision passthrough, leaving only installer_execution not_shipped; Phase 58 moved supported 37→38 / not_shipped 1→0 by转真 the executor itself — one real silent install + one real uninstall on this machine, verdict derived from disk+registry diff rather than any self-report, so this not_shipped name list is now EMPTY and the only remaining boundaries are the 3 Adobe write caps; Phase 80 sank feature_flags/tool_registry/skill_runtime into the new engine #28 service_registry and Phase 81 sank the WebSocket EventHub + one-click delivery into engines #29 event / #30 delivery — the two service-side caps were already supported, so the truth table moved 41→43 / supported 38→40 and Registry verified 38→40 purely by engine下沉+golden化 while status 口径 stays 43 项 supported 40 — all re-pinned on THIS archive), and the JSON lands ENTIRELY on stdout (import-time DeprecationWarning may go to stderr but never splits the payload)",
          _rc50a == 0 and _j50a.get("schema") == "zaojing-desktop-hub/v1"
          and _j50a.get("all_sources_live") is True and _j50a.get("degraded") == []
          and _j50a.get("fabricated") is False
          and '"schema"' not in _se50a
          and _j50a.get("window") == {"requested": False}
          and _j50a.get("base_url") == "http://127.0.0.1:8877"
          and _j50a.get("base_url_source") == "env:ZAOJING_PORT"
          and _j50a.get("rows", [""])[0] == "Hub 目标 http://127.0.0.1:8877（来源 env:ZAOJING_PORT）"
          and any("能力真值表 43 项：supported 40 / needs_adobe_host 3 / prototype_not_shipped 0 / not_shipped 0" in r for r in _j50a.get("rows", []))
          and any("引擎 30/30 ready（/health 与 /capabilities 同源=True）" in r for r in _j50a.get("rows", []))
          # Phase 59 路线 B 漂移同族：runtime_guard 新黄金域 + 编译面/预检面改归
          # installer_artifact_build/installer_preflight ⇒ Registry 已验证 29→32。
          # 产品侧实时派生已动；红的是尺子，就地重钉（同 P53/P56/P58 先例）。
          # Phase 80 漂移同族：feature_flags/tool_registry/skill_runtime 三枚下沉第 28 引擎
          # service_registry 并各 golden 化 ⇒ Registry 已验证 35→38、引擎 27/27→28/28；能力真值
          # status 口径不变（43 项 supported 40 照旧），产品侧实时派生已动；红的是尺子。
          # Phase 81 漂移同族：websocket_events／delivery_package 两枚下沉第 29 引擎 event、
          # 第 30 引擎 delivery 并各 golden 化 ⇒ Registry 已验证 38→40、引擎 28/28→30/30；能力
          # 真值 status 口径不变（43 项 supported 40 照旧），产品侧实时派生已动；红的是尺子。
          and any(r.startswith("Registry 已验证 40 项（honest_boundary 3 项）") for r in _j50a.get("rows", [])),
          json.dumps({"rc": _rc50a, "rows": len(_j50a.get("rows", [])),
                      "src": _j50a.get("base_url_source"), "out": _so50a[:160],
                      "stderr": _se50a[:160]},
                     ensure_ascii=False))
    _rc50b, _so50b, _se50b = _hub50run(["hub", "--window-probe", "--base-url", BASE])
    _j50b = json.loads(_so50b) if _so50b.lstrip().startswith("{") else {}
    _w50b = _j50b.get("window") or {}
    check("EXE(P50): `hub --window-probe` on the frozen archive REALLY builds a native Tk window from inside the onefile — exit 0 / created True / hwnd!=0 / mapped 1 / title '造境 AI · 本地 Hub' / buttons [eval_run, refresh] / label_texts == rows (the window shows only derived lines, no separate GUI constants) / rows_equal_render True / clicks_so_far [] and status still 未触发动作 (probe never auto-clicks)",
          _rc50b == 0 and _j50b.get("base_url_source") == "cli"
          and _w50b.get("created") is True and _w50b.get("hwnd") not in (0, None)
          and _w50b.get("mapped") == 1
          and _w50b.get("title") == "造境 AI · 本地 Hub"
          and _w50b.get("buttons") == ["eval_run", "refresh"]
          and _w50b.get("label_texts") == _j50b.get("rows")
          and _w50b.get("rows_equal_render") is True
          and _w50b.get("clicks_so_far") == []
          and _w50b.get("status_text") == "未触发动作"
          and _j50b.get("fabricated") is False,
          json.dumps({"rc": _rc50b, "created": _w50b.get("created"),
                      "hwnd": _w50b.get("hwnd"), "mapped": _w50b.get("mapped"),
                      "geom": _w50b.get("geometry_live"), "rows": len(_j50b.get("rows", [])),
                      "stdout_is_json": _so50b.lstrip().startswith("{"),
                      "stderr": _se50b[:120],
                      "err": (_w50b.get("reason"), _w50b.get("error_text"))},
                     ensure_ascii=False))
    _rc50c, _so50c, _se50c = _hub50run(["hub", "--selftest", "--text"],
                                       env_extra={"ZAOJING_PORT": "8877"}, dec="utf-8")
    _rc50d, _so50d, _se50d = _hub50run(["hub", "--no-such-flag"])
    _post50h = {k: int(v) for k, v in (requests.get(BASE + "/api/v1/metrics", timeout=60)
                    .json().get("route_hits", {}) or {}).items()}
    _d50 = {k: _post50h.get(k, 0) - _pre50h.get(k, 0)
            for k in ("/api/v1/eval/run", "/api/v1/health", "/api/v1/capabilities",
                      "/api/v1/eval/reports")}
    check("EXE(P50): the hub CLI is honest at its edges too — --text emits the SAME derived rows as UTF-8 (GBK-console-safe ASCII default), an unknown flag answers exit 2 with ERR unknown_flag instead of silently opening something, and the WHOLE hub session (2 selftests + 1 real window) left POST /eval/run route_hits at EXACTLY +0 while the three read routes each moved (the subprocess really talked to this frozen exe)",
          _rc50c == 0 and "服务 造境 AI v" in _so50c
          and "能力真值表 43 项" in _so50c and _so50c.count("\n") >= 6
          and _rc50d == 2 and "ERR unknown_flag" in _so50d
          and _d50["/api/v1/eval/run"] == 0
          and _d50["/api/v1/health"] >= 2 and _d50["/api/v1/capabilities"] >= 2
          and _d50["/api/v1/eval/reports"] >= 2,
          json.dumps({"rc_text": _rc50c, "rc_bad": _rc50d, "text_lines": _so50c.count("\n"),
                      "deltas": _d50}, ensure_ascii=False))

    # ===== Phase 59 路线 C：冻结归档自己的「文案 vs 实现」同源判据 =====
    # C-1 受理形态集合。四路派生、一律**不手抄**：
    #   路一：`ZaojingAI.exe install-exec`（空参数）走 usage 分支，把 INSTALL_EXEC_USAGE 原文以
    #         ASCII 转义写到 stdout 后 return 2（首行形如 <install|uninstall|plan|state>，形态段
    #         本身纯 ASCII，正则照打）。
    #   路二：`ZaojingAI.exe install-exec <非法子命令>` 先回 usage_error 载荷的 legal 数组再 return 2。
    #   两分支都在 `_require(...)` **之前**，故既不加载引擎也不起服务——零副作用，可反复跑。
    #   路三：HTTP 契约面 entry_point。路四：/api/v1/capabilities 里 installer_execution 的 note
    #   （note 里 install-exec 后跟形态串的地方**每一处**都在判分面上，一处漏改即判红）。
    # 判据取**集合相等**：少一形=漏报已发货能力（plan 自 Phase 58 就在，修前 entry_point 与 note
    # 只写三形），多一形=文案吹过实现。本文件约定 re 不在顶层 import，就地取别名（_re41x/_re54x 同法）。
    import re as _re59x
    _rc59u, _so59u, _se59u = _hub50run(["install-exec"], dec="utf-8")
    _u_line59x = ((_so59u or "").splitlines() or [""])[0]
    _rc59q, _so59q, _se59q = _hub50run(["install-exec", "not-a-real-subcommand"], dec="utf-8")
    _q_out59x = (_so59q or "").lstrip()
    _legal59x = (json.loads(_q_out59x) if _q_out59x.startswith("{") else {})
    _u_forms59x = _re59x.search(r"<([a-z_|\-]+)>", _u_line59x)
    _usage_forms59x = sorted(set(_u_forms59x.group(1).split("|"))) if _u_forms59x else []
    _ia59x = requests.get(BASE + "/api/v1/runtime/install-actions", timeout=60).json()
    _ep59x = _re59x.search(r"install-exec\s+([a-z_|\-]+)", str(_ia59x.get("entry_point", "")))
    _ep_forms59x = sorted(set(_ep59x.group(1).split("|"))) if _ep59x else []
    _note59x = str(_cn47.get("installer_execution", {}).get("note") or "")
    _note_forms59x = sorted(_re59x.findall(r"install-exec\s+([a-z|]+)", _note59x))
    _note_sets59x = [sorted(set(m.split("|"))) for m in _note_forms59x]
    check("EXE(P59): install-exec 的受理形态在冻结归档上是**四源同源且集合相等**（正控：每一源都数得出 ≥1 枚，否则尺子自己先红；负控：文案与实现各写一份即判红）— EXE CLI usage 首行派生 = EXE CLI usage_error 载荷 legal 派生 = GET /api/v1/runtime/install-actions 的 entry_point 派生 = capabilities note 里每一处形态串，四者全等且都含 plan；两趟 CLI 各自 rc=2 且不加载引擎、不起服务（Phase 59 修前 entry_point 与 note 只有 install|uninstall|state 三形＝漏报 Phase 58 已发货的只读形态 plan，判红即此故）",
          len(_usage_forms59x) >= 1 and len(_legal59x.get("legal", [])) >= 1
          and len(_ep_forms59x) >= 1 and len(_note_sets59x) >= 1
          and _usage_forms59x == sorted(_legal59x.get("legal", [])) == _ep_forms59x
          and all(_s == _usage_forms59x for _s in _note_sets59x)
          and _rc59u == 2 and _rc59q == 2 and "install-exec" in _u_line59x
          and _legal59x.get("status") == "usage_error" and _legal59x.get("fabricated") is False
          and "plan" in _ep_forms59x,
          json.dumps({"usage_forms": _usage_forms59x, "legal": _legal59x.get("legal"),
                      "entry_point_forms": _ep_forms59x,
                      "note_forms": _note_sets59x, "note_hits": len(_note_forms59x),
                      "rc": [_rc59u, _rc59q], "usage_head": _u_line59x[:90]},
                     ensure_ascii=False))
    # C-3 随包发货的 OpenAPI 文案不得写死门数。FastAPI 把端点 docstring 当作 operation description
    # 发进 /openapi.json，所以「回显七道门」不是注释里的闲话而是**随包发货的契约文本**；修法是删掉
    # 数字而不是改成 10（改数字下一轮加一道门照样漂移）。正控先自证这把尺数得出，否则"不含 N道门"
    # 是恒真的假绿。_desc 必须取**原文串**：json.dumps 会把中文转义成 \uXXXX，"含 门"就永远落空。
    _gate_re59x = _re59x.compile(r"[一二三四五六七八九十0-9]+\s*道门")
    _oa59x = requests.get(BASE + "/openapi.json", timeout=60).json()
    _desc59x = str((((_oa59x.get("paths", {}).get("/api/v1/runtime/install-actions", {}) or {})
                     .get("get", {}) or {}).get("description")) or "")
    check("EXE(P59): 冻结归档随包发货的 /openapi.json 里 install-actions 的 description 不得含写死门数（门数只活在载荷里，由引擎 gates 现场派生）— 正控：同一把尺对样例串「回显七道门」必命中；活体：payload 的 gates 长度 >0 且逐名不重复（Phase 58 归档实测 10 道，docstring 却写七道 ⇒ 判红即此故）",
          _gate_re59x.search("契约面：回显七道门") is not None
          and _gate_re59x.search(_desc59x) is None
          and len(_ia59x.get("gates", [])) > 0
          and len(_ia59x.get("gates", [])) == len(set(_ia59x.get("gates", [])))
          and "门" in _desc59x,
          json.dumps({"desc_hits": bool(_gate_re59x.search(_desc59x)),
                      "gates_live": len(_ia59x.get("gates", [])),
                      "desc_tail": _desc59x[-90:]}, ensure_ascii=False))
    # C-2 随包界面的评测分母不手写（前端首个「随包散文静默腐坏」载体，与生成件 README 同族）。
    # 实录：index.html 曾手写「99 条计分用例 + 23 条负控校准」，九轮评测扩张后真值已是 139/32，Phase 63 后 143/34，
    # 六层验收无一层看过它 ⇒ 随包界面静默说谎九轮。修法在工具层：app.js 从 /eval/suite 实时派生
    # （计分条数 = 清单条数 − must_fail 条数）。本判据永久钉住：冻结归档受服务的 #ev-run 文案
    # 不含任何手写「数字+条」分母；app.js 确带派生标记（正控自证尺子非恒空）；数字同一性由
    # 恒等式 cases−must_fail==executed_cases 钉（RED→GREEN 证据见仪器回执
    # p59_c2_webui_check_receipt.txt：U3 正控 5/2→3/2、U4 离线腿「取不到清单保留无数字兜底
    # 文案，绝不编分母」）。
    _ui59x = requests.get(BASE + "/", timeout=60).text
    _js59x = requests.get(BASE + "/ui/app.js", timeout=60).text
    _su59x = requests.get(BASE + "/api/v1/eval/suite", timeout=120).json()
    _evrun59x = _re59x.search(r'id="ev-run">([^<]*)<', _ui59x)
    _mf59x = sum(1 for c in _su59x.get("cases", []) if c.get("must_fail"))
    check("EXE(P59): shipped console's eval-run label carries NO hand-written denominator — 冻结归档受服务的 #ev-run 文案不含手写「数字+条」分母（文案本体仍在：运行全量评测），app.js 带实时派生标记（$(\"ev-run\").textContent 赋值 + must_fail 过滤 + 空清单早退保留无数字兜底文案）且套件算术与评测恒等式 cases−must_fail==executed_cases 同源，修前手写 99/23 九轮漂移即判红即此故",
          _evrun59x is not None
          and _re59x.search(r"\d+\s*条", _evrun59x.group(1)) is None
          and "运行全量评测" in _evrun59x.group(1)
          and '$("ev-run").textContent' in _js59x
          and "must_fail" in _js59x and "eval/suite" in _js59x
          and "if (!cs.length) return" in _js59x
          and len(_su59x.get("cases", [])) - _mf59x > 0,
          json.dumps({"label": _evrun59x.group(1) if _evrun59x else None,
                      "cases": len(_su59x.get("cases", [])), "must_fail": _mf59x},
                     ensure_ascii=False))
finally:
    # Phase 26 收尾实证：仅 terminate() 会遗留 onefile 子进程树（继续占 8877、持冻结 EXE
    # 文件句柄——即 Phase 25 重打包 PermissionError 的同根因），改用 taskkill /T 强杀整树。
    subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
    try:
        proc.wait(timeout=15)
    except Exception:
        proc.kill()

import socket as _sock_fin
for _ in range(20):  # 端口释放确认（最多 10s）：harness 必须把环境原样交还给下一次构建
    _s = _sock_fin.socket()
    try:
        _s.bind(("127.0.0.1", 8877))
        _s.close()
        print("[OK] port 8877 released after cleanup")
        break
    except OSError:
        _s.close()
        time.sleep(0.5)
else:
    print("[WARN] port 8877 still occupied after cleanup (next build may hit file lock)")

fails = [x for x in results if x[0] == "[FAIL]"]
print("=" * 60)
print(f"TOTAL {len(results)} | PASS {len(results)-len(fails)} | FAIL {len(fails)}")
for t, n, d in fails:
    print(f"  FAILED: {n} {d}")
sys.exit(1 if fails else 0)
