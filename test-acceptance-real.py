"""
造境 AI 设计智能体 V1.0 — 真实端到端验收台（黄金 §86 T001-T020 / 超级 §87-91 / 性能 §85
+ Phase 60 补白：运行时守护与安装域可核验面 RUNTIME R01-R07 / INSTALLER I01-I09）

设计原则（任务书 §2 / §97 红线）：
  - 零 mock、零硬编码结果、零 time.sleep 伪造。每一步都真实驱动运行中的 Agent Service。
  - 服务端可真实落地的步骤：断言"真实副作用"（文件落盘、字节被改、几何被算、向量被检索）。
  - 纯 Adobe 宿主 / 需要真实模型 Provider / 需要 3D 渲染器的步骤：诚实标 GATE（needs_adobe_host
    / provider_required / renderer_required），断言"服务如实降级、绝不伪造成功"——GATE 不是 PASS 也不是 FAIL，
    而是"该能力按红线被如实标注为不可在本服务内伪造"。
  - 性能指标：真实墙钟计时（perf_counter），不含任何随机/休眠。

判定：suite 通过 == failures == 0（GATE 允许且期望出现，代表诚实降级）。
"""
import sys, os, json, time, subprocess, tempfile, shutil
sys.stdout.reconfigure(errors="backslashreplace")

from pathlib import Path
import requests
from PIL import Image

BASE = "http://127.0.0.1:8799"
OUT = Path(r"C:\Users\Administrator\.qoderworkcn\workspace\mu8l7lfenzrqswc8\outputs")
sys.path.insert(0, str(OUT))
from license_service import issue_license_key

tmp = Path(tempfile.mkdtemp(prefix="zjaccept_"))

PASS, FAIL, GATE = "[PASS]", "[FAIL]", "[GATE]"
_rows = []  # (kind, section, id, desc, detail)

def _rec(kind, section, tid, desc, detail=""):
    _rows.append({"kind": kind, "section": section, "id": tid, "desc": desc, "detail": detail})
    print(f"{kind} [{section}] {tid} {desc}  {detail}")

def check(section, tid, desc, cond, detail=""):
    _rec(PASS if cond else FAIL, section, tid, desc, detail)
    return bool(cond)

def gate(section, tid, desc, cond_honest_degrade, detail=""):
    """诚实降级断言：cond 为真代表服务如实拒绝伪造。"""
    _rec(GATE if cond_honest_degrade else FAIL, section, tid, desc + " [诚实降级]", detail)
    return bool(cond_honest_degrade)

# ---------------------------------------------------------------------------
# 真实测试素材
# ---------------------------------------------------------------------------
def _make_product(path, size=(200, 200), bg=(255, 255, 255), obj=(220, 40, 40)):
    im = Image.new("RGB", size, bg)
    for x in range(size[0] // 4, 3 * size[0] // 4):
        for y in range(size[1] // 4, 3 * size[1] // 4):
            im.putpixel((x, y), obj)
    im.save(path)

img_product = tmp / "tea_product.png"; _make_product(img_product)
img_logo = tmp / "logo.png"; _make_product(img_logo, (120, 60), (255, 255, 255), (20, 60, 200))
img_green = tmp / "matcha_bg.jpg"; Image.new("RGB", (160, 160), (30, 140, 60)).save(img_green)

# ---------------------------------------------------------------------------
# 启动真实服务
# ---------------------------------------------------------------------------
srv_log = open(tmp / "server.log", "wb")
env = dict(os.environ, ZAOJING_PORT="8799", ZAOJING_DATA_DIR=str(tmp / "data"))
proc = subprocess.Popen([sys.executable, str(OUT / "agent_service.py")], env=env,
                        stdout=srv_log, stderr=subprocess.STDOUT)

summary = {}
try:
    up = False
    h = None
    for _ in range(80):
        time.sleep(0.5)
        try:
            r = requests.get(BASE + "/api/v1/health", timeout=2)
            if r.status_code == 200:
                up = True; h = r.json(); break
        except Exception:
            continue
    if not up:
        _rec(FAIL, "BOOT", "-", "服务未能启动", "见 server.log")
        raise SystemExit("server did not boot")

    check("BOOT", "-", "Agent Service 真实启动并返回 /health", up,
          f"engines={len(h.get('engines', {}))}")

    # 先激活 license（真实签名 key）
    key = issue_license_key({"edition": "pro", "mid": "*", "seats": 1, "customer": "acceptance"})
    requests.post(BASE + "/api/v1/license/activate", json={"license_key": key})

    # 读取能力真值表 / 工具契约用于诚实 GATE 判定
    tools = requests.get(BASE + "/api/v1/tools").json()
    tmap = {t["name"]: t for t in tools.get("tools", [])}
    caps = requests.get(BASE + "/api/v1/capabilities").json()
    cmap = {c["id"]: c["status"] for c in caps.get("capabilities", [])}

    # =====================================================================
    # 第一部分：黄金路径 §86 T001–T020
    # =====================================================================

    # T001 获取当前文档（服务端 Design IR 文档库真实持久化）
    doc = requests.get(BASE + "/api/v1/document").json()
    check("GOLDEN", "T001", "获取当前文档（服务端文档库真实可读）",
          doc.get("color_mode") in ("RGB", "CMYK", "GRAY") and isinstance(doc.get("layers_count"), int),
          f"mode={doc.get('color_mode')} layers={doc.get('layers_count')}")

    # T002 获取图层
    l0 = requests.get(BASE + "/api/v1/layers").json()
    check("GOLDEN", "T002", "获取图层（返回列表结构真实）",
          isinstance(l0, list) and all("id" in x and "name" in x for x in l0), f"count={len(l0)}")

    # T003 选择对象（画布内瞬时选择 = 纯 Adobe 宿主；服务端须如实标 needs_adobe_host）
    gate("GOLDEN", "T003", "选择对象需宿主 UXP（无宿主时不得伪造可执行）",
         tmap.get("ps.get_selection", tmap.get("ps.open_document", {})).get("requires_adobe_host") is True
         and tmap.get("ps.open_document", {}).get("runnable_now") is False,
         f"ps.open_document.runnable_now(before host)={tmap.get('ps.open_document',{}).get('runnable_now')}")

    # T004 创建图层（真实落库）
    nl = requests.post(BASE + "/api/v1/layers", json={"name": "产品主图", "type": "normal"}).json()
    l1 = requests.get(BASE + "/api/v1/layers").json()
    check("GOLDEN", "T004", "创建图层后列表真实持久化",
          any(x["name"] == "产品主图" for x in l1), f"id={nl.get('id')} total={len(l1)}")

    # T005 自动命名（对宿主文档改名 = Adobe；服务端不伪造 ps 改名结果）
    gate("GOLDEN", "T005", "自动命名对 Adobe 文档生效需宿主",
         any(t.get("requires_adobe_host") and not t.get("runnable_now")
             for t in tools.get("tools", [])),
         "无宿主上报前，改名类宿主工具一律 runnable_now=False")

    # T006 插入素材（真实入库：缩略图 + Embedding 索引）
    a1 = requests.post(BASE + "/api/v1/assets", json={"file_path": str(img_product)}).json()
    aid = a1.get("id")
    adetail = requests.get(BASE + "/api/v1/assets/" + str(aid)).json()
    has_thumb = bool(adetail.get("thumbnail"))
    check("GOLDEN", "T006", "插入素材：真实入库并生成缩略图 + 向量索引",
          aid is not None and adetail.get("embedding") is not None,
          f"asset_id={aid} embed_dims={adetail.get('embedding',{}).get('dimensions')} thumb={has_thumb}")

    # T007 替换素材（活文档内替换 = Adobe；服务端提供真实再导入 + 检索，不伪造文档内替换）
    _make_product(img_product, obj=(40, 120, 220))  # 改内容
    a2 = requests.post(BASE + "/api/v1/assets", json={"file_path": str(img_product)}).json()
    live_replace_tool = tmap.get("ps.replace_asset", tmap.get("ps.place_asset", {}))
    check("GOLDEN", "T007", "素材再入库真实（新记录可检索；活文档内替换归宿主）",
          a2.get("id") is not None, f"reimported={a2.get('id')}")
    if live_replace_tool:
        gate("GOLDEN", "T007b", "活文档内替换素材需宿主",
             live_replace_tool.get("requires_adobe_host") is True, f"tool={live_replace_tool.get('name')}")

    # T008 去背景（真实像素级泛洪，输出透明 PNG）
    rb = requests.post(BASE + "/api/v1/generate/remove-background",
                       json={"image_path": str(img_logo), "threshold": 60}).json()
    rb_out = rb.get("output", "")
    rb_alpha = False
    try:
        with Image.open(rb_out) as _im:
            _im = _im.convert("RGBA")
            # 真实检查：角像素 alpha==0（背景被移除）
            rb_alpha = _im.getpixel((0, 0))[3] == 0
    except Exception:
        pass
    check("GOLDEN", "T008", "去背景：真实泛洪输出 alpha 透明 PNG",
          rb.get("background_cleared_ratio", 0) > 0 and rb_alpha and os.path.isfile(rb_out),
          f"cleared={rb.get('background_cleared_ratio')} out={Path(rb_out).name}")

    # T009 AI 生成（无本地/云 Provider 时必须如实 502，不得伪造图像）
    try:
        gen = requests.post(BASE + "/api/v1/generate", json={"prompt": "茶叶包装主视觉，清新自然", "priority": "cost"}, timeout=140)
        gs = gen.status_code
        gj = gen.json() if gs == 200 else {}
    except Exception as e:
        gs, gj = 0, {"detail": str(e)}
    honest_no_provider = gs in (502, 503, 504)
    fabricated_success = gs == 200 and gj.get("images_generated", 0) >= 1
    check("GOLDEN", "T009", "AI 生成未伪造成功（无 Provider 时如实降级，且绝无凭空 images_generated）",
          (not fabricated_success) and (honest_no_provider or gs == 200),
          f"http={gs} images_generated={gj.get('images_generated')} detail={str(gj.get('detail'))[:70]}")
    if gs in (502, 503, 504):
        gate("GOLDEN", "T009b", "AI 生成需真实模型 Provider", honest_no_provider, f"http={gs}")

    # T010 自动排版（真实几何对齐 + 综合体检）
    objs = [{"id": "t", "x": 120, "y": 40, "width": 200, "height": 60},
            {"id": "i", "x": 300, "y": 80, "width": 80, "height": 80},
            {"id": "b", "x": 0, "y": 200, "width": 400, "height": 40}]
    al = requests.post(BASE + "/api/v1/layout/align", json={"objects": objs, "mode": "left"}).json()
    an = requests.post(BASE + "/api/v1/layout/analyze", json={"objects": objs, "canvas": {"width": 500, "height": 500}}).json()
    check("GOLDEN", "T010", "自动排版：真实左对齐（全部 x→0）且综合体检可复算",
          al.get("aligned", 0) >= 2 and all(o["to"]["x"] == 0 for o in al["objects"]) and "score" in an,
          f"aligned={al.get('aligned')} analyze_score={an.get('score')}")

    # T011 AI 配色（真实图像逐像素量化取调色板，Top 色须命中源像素色）
    pal = requests.post(BASE + "/api/v1/color/palette", json={"path": str(img_green), "k": 5}).json()
    _pp = pal.get("palette", [])
    _t0 = _pp[0].get("rgb", [0, 0, 0]) if _pp else [0, 0, 0]
    check("GOLDEN", "T011", "配色：真实图像量化调色板的 Top 色命中源像素色(近 (30,140,60))，非任意十六进制",
          len(_pp) >= 1 and all(re_ok := (p.get("hex", "").startswith("#")) for p in _pp)
          and abs(sum(p.get("percent", 0) for p in _pp) - 100.0) < 1.5
          and len(_t0) == 3 and all(abs(_t0[i] - [30, 140, 60][i]) <= 45 for i in range(3))
          and (_pp[0].get("percent", 0) >= 70.0),
          f"colors={len(_pp)} top={_pp[0]['hex'] if _pp else None} rgb={_t0} pct={_pp[0].get('percent') if _pp else None}")

    # T012 客户改稿（真实解析→仅应用被批准项）
    rev_objs = [{"id": "logo1", "name": "logo", "type": "image", "width": 200, "height": 80},
                {"id": "ttl", "name": "主标题", "type": "text", "font_size": 36, "color": "#111111"},
                {"id": "bg", "name": "背景", "type": "shape", "width": 1000, "height": 1000, "protected": True}]
    rp = requests.post(BASE + "/api/v1/revision/parse",
                       json={"text": "把 logo 放大一点", "objects": rev_objs, "protected": ["bg"]}).json()
    proposed = [c for c in rp.get("changes", []) if c.get("status") == "proposed"]
    approved = [c["id"] for c in proposed]
    ra = requests.post(BASE + "/api/v1/revision/apply",
                       json={"objects": rev_objs, "changes": rp.get("changes", []), "only_ids": approved}).json()
    _logo_after = next((o for o in ra.get("objects", []) if o.get("_id") == "logo1"), {})
    _logo_w = _logo_after.get("width")
    check("GOLDEN", "T012", "客户改稿：真实解析为 proposed 并真实施加到对象副本（logo 尺寸变大）",
          len(proposed) >= 1 and isinstance(_logo_w, (int, float)) and _logo_w > 200,
          f"proposed={len(proposed)} logo_w_after={_logo_w}")

    # T013 批量处理（真实并发逐文件）
    files = [str(img_product), str(img_logo), str(img_green), str(tmp / "nope.png")]
    bt = requests.post(BASE + "/api/v1/batch/process",
                       json={"file_paths": files, "operation": "preflight", "workers": 4}).json()
    check("GOLDEN", "T013", "批量处理：真实逐文件成功/失败计数（缺失文件诚实 fail）",
          bt.get("success") == 3 and bt.get("failed") == 1,
          f"ok={bt.get('success')} fail={bt.get('failed')} avg={bt.get('avg_per_file')}s")

    # T014 印前检查（真实解析文档）
    pf = requests.post(BASE + "/api/v1/preflight", json={"document_path": str(img_product)}).json()
    check("GOLDEN", "T014", "印前检查：真实解析并给出 print_ready 判定",
          isinstance(pf, dict) and "print_ready" in pf, f"print_ready={pf.get('print_ready')}")

    # T015 生成样机（需 3D 渲染器 / 宿主；本服务不得伪造样机图）
    gate("GOLDEN", "T015", "生成样机需 3D 渲染/宿主——本服务未伪造样机为 supported",
         cmap.get("mockup_render", "not_shipped") != "supported" and "mockup" not in str([k for k, v in cmap.items() if v == "supported"]),
         "真值表未把样机标 supported（诚实）")

    # T016 回滚（真实快照→破坏→还原字节）
    docfile = tmp / "tea_package_v1.psd"
    original = b"PSD-TEA-PACKAGE-V1-ORIG-" + b"A" * 64
    docfile.write_bytes(original)
    snap = requests.post(BASE + "/api/v1/recovery/snapshot",
                         json={"document_path": str(docfile), "snapshot_type": "full"}).json()
    docfile.write_bytes(b"XXXX-CORRUPTED-XXXX-CORRUPTED-XXXX")
    rs = requests.post(BASE + f"/api/v1/recovery/snapshot/{snap['id']}/restore").json()
    reverted = docfile.read_bytes() == original
    check("GOLDEN", "T016", "回滚：快照 SHA256 + 还原真实回退原始字节",
          snap.get("recoverable") and len(snap.get("checksum", "")) == 64 and rs.get("restored") and reverted,
          f"recoverable={snap.get('recoverable')} restored={rs.get('restored')} bytes_match={reverted}")

    # T017 中途取消（协作式任务状态机真实记录：可恢复中断→interrupted，不可恢复→failed）
    tr = requests.post(BASE + "/api/v1/recovery/task/register",
                       json={"task_id": "g-cancel", "total_steps": 5, "context": {"doc": "tea"}}).json()
    requests.post(BASE + "/api/v1/recovery/task/g-cancel/progress", json={"current_step": 2})
    requests.post(BASE + "/api/v1/recovery/task/g-cancel/fail",
                  json={"error_message": "用户中途取消", "recoverable": True}).json()
    rep = requests.get(BASE + "/api/v1/recovery/task/g-cancel/report").json()
    # 不可恢复任务须真实记为 failed
    requests.post(BASE + "/api/v1/recovery/task/register", json={"task_id": "g-hard", "total_steps": 3, "context": {}})
    requests.post(BASE + "/api/v1/recovery/task/g-hard/fail", json={"error_message": "致命错误", "recoverable": False})
    rep2 = requests.get(BASE + "/api/v1/recovery/task/g-hard/report").json()
    check("GOLDEN", "T017", "中途取消：可恢复任务诚实记 interrupted、步序持久；不可恢复记 failed",
          tr.get("status") == "running" and rep.get("status") == "interrupted"
          and rep.get("current_step") == 2 and rep2.get("status") == "failed",
          f"cancel={rep.get('status')}@step{rep.get('current_step')} hard={rep2.get('status')}")

    # T018 中途接管（人在环：仅应用人工批准项，未批准项不落盘）
    _objs_before = [dict(o) for o in rev_objs]
    rp2 = requests.post(BASE + "/api/v1/revision/parse",
                        json={"text": "把 logo 放大，主标题换成深红色", "objects": _objs_before, "protected": ["bg"]}).json()
    _proposed2 = [c for c in rp2.get("changes", []) if c.get("status") == "proposed"]
    approved_only = [_proposed2[0]["id"]] if _proposed2 else []   # 人工只批准第一条
    ra2 = requests.post(BASE + "/api/v1/revision/apply",
                        json={"objects": _objs_before, "changes": rp2.get("changes", []), "only_ids": approved_only}).json()
    _after2 = {o["_id"]: o for o in ra2.get("objects", [])}
    _ttl = _after2.get("ttl", {})
    # 未批准的主标题改色必须保持原样（#111111 / 36pt）
    unapproved_intact = _ttl.get("color") == "#111111" and _ttl.get("font_size") == 36
    check("GOLDEN", "T018", "中途接管：仅人工勾选项被真实应用，未批准项字节级保持原样",
          len(approved_only) == 1 and unapproved_intact and ra2.get("applied") == 1,
          f"approved={approved_only} applied={ra2.get('applied')} title_color={_ttl.get('color')} title_fs={_ttl.get('font_size')}")

    # T019 断网恢复（离线基础能力真实可用：本地 preflight/配色/布局不受外网影响）
    try:
        local_ok = (requests.post(BASE + "/api/v1/color/palette", json={"path": str(img_green), "k": 3}, timeout=5).status_code == 200 and
                    requests.post(BASE + "/api/v1/preflight", json={"document_path": str(img_product)}, timeout=8).status_code == 200 and
                    requests.post(BASE + "/api/v1/layout/collide", json={"objects": objs}, timeout=5).status_code == 200)
    except Exception as e:
        local_ok = False
    check("GOLDEN", "T019", "断网恢复：本地素材/配色/布局/印前基础能力真实继续可用",
          local_ok, "无外网 Provider 时核心本地引擎仍 200")

    # T020 Agent 崩溃恢复（真实悬空任务扫描 + 任务日志报告）
    orph = requests.get(BASE + "/api/v1/recovery/tasks/orphaned").json()
    rep20 = requests.get(BASE + "/api/v1/recovery/task/g-cancel/report").json()
    check("GOLDEN", "T020", "崩溃恢复：悬空任务扫描真实 + 任务报告含可恢复快照信息",
          "count" in orph and "available_snapshots" in rep20,
          f"orphaned={orph.get('count')} report_keys={sorted(rep20.keys())[:5]}")

    # T021 NAS 客户端（§81）：映射盘/共享真实连接 + 枚举 + 路径穿越诚实拒绝
    nshare = tmp / "acc_nas"; nshare.mkdir(exist_ok=True)
    (nshare / "spec.txt").write_text("nas-acceptance", encoding="utf-8")
    requests.post(BASE + "/api/v1/nas/configure",
                  json={"target_id": "acc", "target": str(nshare), "kind": "filesystem", "share_root": str(nshare)})
    ncon = requests.post(BASE + "/api/v1/nas/connect", json={"target_id": "acc", "timeout": 3}).json()
    nlist = requests.post(BASE + "/api/v1/nas/list", json={"target_id": "acc"}).json()
    sp = next((e for e in nlist.get("entries", []) if e.get("name") == "spec.txt"), {})
    ntv = requests.post(BASE + "/api/v1/nas/list", json={"target_id": "acc", "subpath": "../../.."}).json()
    check("GOLDEN", "T021", "NAS：映射盘/共享经 OS 原生真实连接+枚举，路径穿越诚实拒绝",
          ncon.get("status") == "connected" and ncon.get("access") == "native-os-redirector"
          and sp.get("size") == len("nas-acceptance") and ntv.get("status") == "forbidden",
          f"connect={ncon.get('status')} size={sp.get('size')} traversal={ntv.get('status')}")

    # T022 NAS 文件锁真实检测 + 未配置诚实降级
    lfile = nshare / "busy.bin"; lfile.write_bytes(b"z")
    holder = subprocess.Popen([sys.executable, "-c",
        "import os,msvcrt,time,sys; fd=os.open(sys.argv[1],os.O_RDWR); msvcrt.locking(fd,msvcrt.LK_NBLCK,1); print('HELD',flush=True); time.sleep(float(sys.argv[2]))",
        str(lfile), "4"], stdout=subprocess.PIPE, text=True)
    held = holder.stdout.readline().strip()
    nlock = requests.post(BASE + "/api/v1/nas/lock", json={"target_id": "acc", "subpath": "busy.bin"}).json()
    holder.terminate()
    try:
        holder.wait(timeout=5)
    except Exception:
        holder.kill()
    nghost = requests.post(BASE + "/api/v1/nas/connect", json={"target_id": "unconf"}).json()
    check("GOLDEN", "T022", "NAS：跨进程真实文件锁检测 locked=True + 未配置目标诚实 not_configured",
          held == "HELD" and nlock.get("status") == "ok" and nlock.get("locked") is True
          and nghost.get("status") == "not_configured",
          f"lock={nlock.get('locked')} ghost={nghost.get('status')}")

    # T023 §46 面板关系/刀版拓扑（结构化矢量真算）+ 栅法刀版识别 Phase 47 起为第 25 引擎真实像素反演，非位图输入仍诚实 400
    _acc_box = [
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
    topo = requests.post(BASE + "/api/v1/packaging/panel-topology", json={"panels": _acc_box}).json()
    _p47g = tmp / "acc_panel_dieline.png"
    _i47g = Image.new("RGB", (220, 100), (255, 255, 255))
    _x47g = _i47g.load()
    for _cx in range(10, 209):
        for _cy in (10, 90):
            _x47g[_cx, _cy] = (0, 0, 0)
    for _cy in range(10, 91):
        for _cx in (10, 208):
            _x47g[_cx, _cy] = (0, 0, 0)
    for _cx in (76, 142):
        _cy = 11
        while _cy <= 89:
            _cy2 = min(_cy + 8, 89)
            for _yy in range(_cy, _cy2 + 1):
                _x47g[_cx, _yy] = (0, 0, 0)
            _cy = _cy2 + 4
    _cs = 11
    while _cs <= 207:
        for _k in range(9):
            if _cs + _k <= 207:
                _x47g[_cs + _k, 50] = (0, 0, 0)
        _cs += 13
    _i47g.save(_p47g)
    _rec_resp = requests.post(BASE + "/api/v1/packaging/panel-recognition",
                              params={"image_path": str(_p47g)})
    recog = _rec_resp.json()
    # \u975e\u4f4d\u56fe\u8f93\u5165\u5fc5\u987b\u8bda\u5b9e 400\uff0c\u800c\u975e\u628a\u6587\u672c\u5f53\u56fe\u50cf"\u8bc6\u522b"\u51fa\u9762\u677f\uff08\u00a72 \u7ea2\u7ebf\uff09
    _rejn = requests.post(BASE + "/api/v1/packaging/panel-recognition",
                          params={"image_path": str(tmp / "acc_nas" / "spec.txt")})
    check("GOLDEN", "T023", "\u00a746 \u77e2\u91cf\u9762\u677f\u62d3\u6251\u7eaf\u51e0\u4f55\u771f\u7b97(9\u9762\u677f/8\u6298/\u8fde\u901a/\u88c1\u5207\u7ebf870) + Phase 47 \u6805\u6cd5\u5200\u7248\u8bc6\u522b\u771f\u5b9e\u53cd\u6f14 6 \u9762\u677f(3x2 \u865a\u7ebf\u7f51\u683c\uff1a\u6bcf\u5757 2535px/7 \u6761\u90bb\u8fb9\u5168\u4e3a fold/\u5ea6\u5e8f\u5217 [3,3,2,2,2,2]/\u8fde\u901a 1 \u7ec4/Otsu \u9608\u503c 0 \u4e0e \u58a8 813-\u7ebf 807-\u5757\u72b6 6 \u9010\u6570\u53ef\u590d\u7b97/\u88c1\u5207\u8f6e\u5ed3 1248=2*(234+390))\uff0c\u975e\u4f4d\u56fe\u8f93\u5165\u8bda\u5b9e 400 \u4e0d\u81c6\u9020",
          topo.get("is_connected") is True and topo.get("panel_count") == 9
          and topo.get("relation_count") == 8 and topo.get("cut_boundary_length") == 870
          and topo.get("fabricated") is False
          and _rec_resp.status_code == 200 and recog.get("status") == "recognized"
          and recog.get("implemented") is True and recog.get("fabricated") is False
          and recog.get("engine") == "panel_recognition_engine"
          and recog.get("panel_count") == 6
          and sorted(p["area_px"] for p in recog["panels"]) == [2535] * 6
          and recog["topology"]["adjacency_count"] == 7
          and recog["topology"]["degree_sequence"] == [3, 3, 2, 2, 2, 2]
          and recog["topology"]["connected_groups"] == 1
          and all(a["separating_line"] == "fold" for a in recog["topology"]["adjacencies"])
          and recog["binarize"] == {"method": "otsu", "threshold": 0, "ink_px": 813, "line_px": 807,
                                    "other_ink_px": 6, "barrier_px": 903, "blocked_px": 909}
          and [recog["outline"][k] for k in ("perimeter_px", "union_area_px", "row_runs", "col_runs")]
              == [1248, 15210, 234, 390]
          and recog["background"] == {"component_count": 1, "largest_area_px": 5881}
          and _rejn.status_code == 400
          and "\u65e0\u6cd5\u4f5c\u4e3a\u4f4d\u56fe\u56fe\u50cf\u89e3\u7801" in _rejn.json().get("detail", ""),
          f"topo={topo.get('panel_count')}p/{topo.get('relation_count')}f cut={topo.get('cut_boundary_length')} recog={recog.get('status')}/{recog.get('panel_count')}p txt400={_rejn.status_code}")

    # T024 §101/§102 真实 AI Provider 客户端：本地起 OpenAI-兼容 mock，真验 HTTP 往返 /
    # Bearer 鉴权 / usage 真解析 / §17 结构化规划 / 图像落盘；未配置诚实 502 绝不伪造（§2 红线）。
    import http.server as _ah, socketserver as _as, threading as _at
    _PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
    _KEY = "acc-provider-key"
    _PLAN = json.dumps({"goal": "重做主视觉", "steps": ["分析", "生成", "选择", "应用"], "tools": ["ai.generate"],
                        "constraints": ["保持Logo"], "protected_objects": ["logo"], "verification": ["无溢出"],
                        "rollback": ["恢复快照"]}, ensure_ascii=False)

    class _OpenAIHandler(_ah.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"
        def log_message(self, *a): pass
        def _auth(self): return self.headers.get("Authorization") == f"Bearer {_KEY}"
        def _send(self, code, payload):
            self.send_response(code); self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload))); self.end_headers(); self.wfile.write(payload)
        def _body(self):
            n = int(self.headers.get("Content-Length", 0) or 0)
            try: return json.loads(self.rfile.read(n).decode("utf-8")) if n else {}
            except Exception: return {}
        def do_GET(self):
            if self.path.endswith("/models") and self._auth():
                self._send(200, json.dumps({"object": "list", "data": [{"id": "acc-model"}]}).encode())
            else:
                self._send(401 if not self._auth() else 404, b'{"error":"no"}')
        def do_POST(self):
            if not self._auth(): self._send(401, b'{"error":{"message":"bad key"}}'); return
            b = self._body()
            if self.path.endswith("/chat/completions"):
                self._send(200, json.dumps({"model": "acc-model",
                    "choices": [{"index": 0, "message": {"role": "assistant", "content": _PLAN}}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 30, "total_tokens": 40}}).encode())
            elif self.path.endswith("/images/generations"):
                self._send(200, json.dumps({"model": "img", "data": [{"b64_json": _PNG} for _ in range(int(b.get("n", 1)))]}).encode())
            else:
                self._send(404, b'{}')

    class _TServer(_as.ThreadingTCPServer):
        allow_reuse_address = True
    _afeed = _TServer(("127.0.0.1", 0), _OpenAIHandler)
    _at.Thread(target=_afeed.serve_forever, daemon=True).start()
    _ab = f"http://127.0.0.1:{_afeed.server_address[1]}/v1"
    try:
        pre = requests.post(BASE + "/api/v1/provider/chat", json={"prompt": "x"})  # 全局未配置任何 Provider
        cfg = requests.post(BASE + "/api/v1/provider/configure",
                            json={"provider_id": "acc-live", "base_url": _ab, "api_key": _KEY,
                                  "model": "acc-model", "image_model": "acc-img"}).json()
        ch = requests.post(BASE + "/api/v1/provider/chat", json={"prompt": "茶叶包装", "provider_id": "acc-live"}).json()
        pl = requests.post(BASE + "/api/v1/plan", json={"goal": "重做主视觉", "provider_id": "acc-live"}).json()
        im = requests.post(BASE + "/api/v1/provider/images", json={"prompt": "海报", "provider_id": "acc-live", "n": 1}).json()
        unauth_ok = (cfg.get("providers", {}).get("acc-live", {}).get("has_key") is True
                     and _KEY not in json.dumps(cfg))
        real_chat = (ch.get("status") == "ok" and ch.get("content") and ch.get("fabricated") is False
                     and ch.get("usage", {}).get("total_tokens") == 40)
        real_plan = (pl.get("status") == "ok" and pl.get("plan", {}).get("steps")
                     and pl.get("plan", {}).get("protected_objects") == ["logo"] and pl.get("fabricated") is False)
        real_img = (im.get("status") == "ok" and im.get("images_generated") >= 1
                    and all(f.get("bytes", 0) > 0 and os.path.isfile(f.get("path", "")) for f in im.get("files", []))
                    and im.get("fabricated") is False)
        honest_502 = (pre.status_code == 502 and pre.json().get("detail", {}).get("status") == "no_provider_configured"
                      and pre.json().get("detail", {}).get("fabricated") is False)
        check("GOLDEN", "T024",
              "AI Provider 真实往返(chat+usage/§17plan/图像落盘/密钥脱敏) + 未配置诚实502不伪造",
              honest_502 and unauth_ok and real_chat and real_plan and real_img,
              f"pre502={pre.status_code} chat={ch.get('usage',{}).get('total_tokens')} plan_steps={len(pl.get('plan',{}).get('steps',[]))} imgs={im.get('images_generated')}")
    finally:
        try:
            _afeed.shutdown(); _afeed.server_close()
        except Exception:
            pass

    # =====================================================================
    # T025 §8/§9/§24/§25/§26/§74 Adobe UXP 桥接（服务端半侧端到端）
    #   GOLDEN 交付的是"桥接机制"本身（协议合规 mock host 驱动真实往返），
    #   而非伪造 Adobe 文档变更：真 ps.*/ai.* 写入仍 needs_adobe_host。
    # =====================================================================
    import threading as _thr
    _bview = requests.get(BASE + "/api/v1/uxp/bridge").json()
    _dp = Path(_bview.get("discovery_file", ""))

    # 无真实宿主时的诚实降级（GATE：服务如实拒绝凭空伪造文档操作）
    _nh = requests.post(BASE + "/api/v1/uxp/dispatch", json={"tool": "ps.layer.create", "args": {"name": "x"}})
    gate("GOLDEN", "T025a", "无 Adobe 宿主连接时下发诚实 502 no_host_connected（绝不伪造文档变更）",
         _nh.status_code == 502 and _nh.json()["detail"].get("error") == "no_host_connected"
         and _nh.json()["detail"].get("fabricated") is False,
         f"HTTP {_nh.status_code} err={_nh.json()['detail'].get('error')}")

    # 协议合规 mock host 真实往返
    _info = json.loads(_dp.read_text(encoding="utf-8")) if _dp.exists() else {}
    _tok = _info.get("session_token", "")
    _wa = requests.post(BASE + "/api/v1/uxp/handshake", json={"host": "photoshop", "version": "25.3",
          "capabilities": {"layer_write": True}, "token": "BAD"})
    _hs = requests.post(BASE + "/api/v1/uxp/handshake", json={"host": "photoshop", "version": "25.3.1",
          "capabilities": {"document_read": True, "layer_write": True, "batch_operation": True}, "token": _tok})
    _hj = _hs.json()
    _hid = _hj.get("host_id")
    _mem = {}
    _stop = _thr.Event()
    def _mock_host():
        while not _stop.is_set():
            try:
                _pr = requests.post(BASE + "/api/v1/uxp/poll",
                                    json={"host_id": _hid, "token": _tok, "timeout": 2}, timeout=8).json()
            except Exception:
                _stop.wait(0.3); continue
            for _cmd in _pr.get("commands", []):
                _rid = _cmd["request_id"]; _it = _cmd.get("intent")
                _a = (_cmd.get("context") or {}).get("args") or {}
                _p = ((_cmd.get("permissions") or {}).get("protected_objects")) or []
                if _it == "layer.create" and _a.get("name") in _p:
                    requests.post(BASE + "/api/v1/uxp/result", json={"request_id": _rid, "token": _tok,
                        "status": "error", "error": "protected_object", "detail": "受保护对象"})
                elif _it == "layer.create":
                    _mem[_a.get("name")] = True
                    requests.post(BASE + "/api/v1/uxp/result", json={"request_id": _rid, "token": _tok,
                        "status": "ok", "payload": {"objects": [{"name": _a.get("name"), "created": True}]}})
                else:
                    requests.post(BASE + "/api/v1/uxp/result", json={"request_id": _rid, "token": _tok,
                        "status": "error", "error": "not_supported", "detail": _it})
    _ht = _thr.Thread(target=_mock_host, daemon=True); _ht.start()
    time.sleep(0.3)
    _d1 = requests.post(BASE + "/api/v1/uxp/dispatch",
                        json={"tool": "ps.layer.create", "args": {"name": "hero"}, "timeout": 8}).json()
    _d2 = requests.post(BASE + "/api/v1/uxp/dispatch", json={"tool": "ps.layer.create",
                        "args": {"name": "brand_logo"}, "protected_objects": ["brand_logo"], "timeout": 8}).json()
    _stop.set()
    _cn = requests.post(BASE + "/api/v1/uxp/dispatch",
                        json={"tool": "ps.image.import", "args": {"path": "a.png"}, "timeout": 5})
    roundtrip_ok = (_hs.status_code == 200 and _hj.get("status") == "connected"
                    and _hj.get("fabricated") is False and "ps.layer.create" in (_hj.get("runnable_now_tools") or []))
    exec_reconciled = (_d1.get("status") == "ok" and _d1.get("reconciled", {}).get("source") == "host"
                       and _d1.get("fabricated") is False and "hero" in _mem)
    protected_refused = (_d2.get("status") == "host_error" and _d2.get("error") == "protected_object_conflict"
                         and _d2.get("fabricated") is False and "brand_logo" not in _mem)
    capability_gated = (_cn.status_code == 502 and _cn.json()["detail"].get("error") == "capability_not_negotiated")
    auth_rejected = (_wa.status_code == 502 and _wa.json()["detail"].get("error") == "auth_failed")
    check("GOLDEN", "T025b",
          "Adobe UXP 桥接真实往返(握手+Token认证+能力协商+poll+宿主执行+回传对账 source=host / 受保护对象如实拒绝 / 未协商能力诚实降级 / 错Token auth_failed)",
          roundtrip_ok and exec_reconciled and protected_refused and capability_gated and auth_rejected,
          f"roundtrip={roundtrip_ok} exec={exec_reconciled} prot={protected_refused} gate={capability_gated} auth={auth_rejected}")

    # 红线分裂：桥接管道 supported，真实文档写仍 needs_adobe_host（绝不在无宿主下谎称已写）
    check("GOLDEN", "T025c", "能力真值表红线分裂：adobe_uxp_bridge=supported 而 ps/ai_document_write 仍 needs_adobe_host",
          cmap.get("adobe_uxp_bridge") == "supported" and cmap.get("ps_document_write") == "needs_adobe_host"
          and cmap.get("ai_document_write") == "needs_adobe_host",
          f"bridge={cmap.get('adobe_uxp_bridge')} ps={cmap.get('ps_document_write')} ai={cmap.get('ai_document_write')}")

    # =====================================================================
    # 第二部分：超级验收 §87–§88（真实端到端业务链）
    # =====================================================================

    # §87 茶叶包装 Golden Path（真实链：知识→素材→去背→配色→排版→评审→改稿→交付→回滚护栏）
    kb = tmp / "brand.md"
    kb.write_text("# 茶叶品牌规范\n主色 抹茶绿 matcha，标题字体思源黑体，忌用高饱和红。\n" * 4, encoding="utf-8")
    ki = requests.post(BASE + "/api/v1/knowledge/ingest",
                       json={"file_path": str(kb), "title": "茶叶品牌规范", "scope": "brand", "brand": "tea-co"}).json()
    kq = requests.post(BASE + "/api/v1/knowledge/retrieve",
                        json={"query": "主色 抹茶绿", "top_k": 3, "allowed_scopes": ["brand"], "context": {"brand": "tea-co"}}).json()
    check("SUPER", "§87.1", "锁定品牌规范：真实入库 + 作用域内检索命中",
          ki.get("status") == "indexed" and kq.get("total", 0) >= 1,
          f"chunks={ki.get('chunk_count')} hits={kq.get('total')}")

    import_asset = requests.post(BASE + "/api/v1/assets", json={"file_path": str(img_product)}).json()
    harmon = requests.post(BASE + "/api/v1/color/harmony", json={"base": "#2D5F3F", "scheme": "triadic"}).json()
    rev_run = requests.post(BASE + "/api/v1/review/run", json={
        "objects": [{"id": "bg", "type": "shape", "x": 0, "y": 0, "width": 500, "height": 700, "color": "#FFFFFF"},
                    {"id": "t", "type": "text", "x": 50, "y": 50, "width": 300, "height": 40, "color": "#EDEDED", "font_size": 30},
                    {"id": "s", "type": "text", "x": 52, "y": 55, "width": 280, "height": 30, "color": "#111111", "font_size": 5}],
        "canvas": {"width": 500, "height": 700}, "brand": {"primary": "#2D5F3F"}}).json()
    check("SUPER", "§87.2", "生成环节真实可用：素材入库 + 三角和谐 3 色 + 多角色评审真实报出对比度/重叠问题",
          import_asset.get("id") and len(harmon.get("colors", harmon.get("palette", []))) >= 3
          and rev_run.get("summary", {}).get("total_issues", 0) >= 1,
          f"harmony={len(harmon.get('colors', harmon.get('palette', [])))} review_issues={rev_run.get('summary',{}).get('total_issues')} score={rev_run.get('score')}")

    # 交付包真实归档（Source/Preview 真实文件 → ZIP + SHA256 manifest）
    prev = tmp / "preview.png"; shutil.copy(img_product, prev)
    dl = requests.post(BASE + "/api/v1/delivery/package",
                       json={"project": "tea-package", "files": {"Source": [str(img_product)], "Preview": [str(prev)]}}).json()
    archive = dl.get("archive", "")
    check("SUPER", "§87.3", "一键交付：真实归档存在文件并生成合法 ZIP",
          dl.get("included", 0) == 2 and os.path.isfile(archive),
          f"included={dl.get('included')} size={dl.get('archive_size_bytes')}")

    # §88 12 包装批量换 Logo（真实 12 文件；仅 Logo 对象可改，文字/背景受保护）
    batch_imgs = []
    for i in range(12):
        p = tmp / f"pkg_{i:02d}.png"
        _make_product(p, size=(120, 120), obj=(30 + i * 8, 120, 90))
        batch_imgs.append(str(p))
    bb = requests.post(BASE + "/api/v1/batch/process",
                       json={"file_paths": batch_imgs, "operation": "import_asset", "workers": 6}).json()
    # 语义保护：对含 logo+文字+受保护背景的对象，改稿只允许 logo，其余受保护须 conflict
    prot_objs = [{"id": "logo", "name": "logo", "type": "image", "width": 100, "height": 40},
                 {"id": "name", "name": "产品名", "type": "text", "font_size": 24, "protected": True},
                 {"id": "legal", "name": "法规信息", "type": "text", "font_size": 10, "protected": True},
                 {"id": "bg", "name": "背景", "type": "shape", "width": 300, "height": 400, "protected": True}]
    pr = requests.post(BASE + "/api/v1/revision/parse",
                       json={"text": "把 logo 换成新版并放大", "objects": prot_objs, "protected": ["name", "legal", "bg"]}).json()
    _proposed = [c for c in pr.get("changes", []) if c.get("status") == "proposed"]
    _conflicts = [c for c in pr.get("changes", []) if c.get("status") == "conflict"]
    touched_ids = {c.get("target") for c in _proposed}
    check("SUPER", "§88.1", "批量：12 个真实文件全部导入成功",
          bb.get("success") == 12 and bb.get("failed") == 0, f"ok={bb.get('success')}")
    check("SUPER", "§88.2", "保护机制：仅 Logo 对象可改（proposed 只命中 logo），受保护对象绝不产生可执行改动",
          touched_ids == {"logo"} and all(c.get("target") in ("name", "legal", "bg") for c in _conflicts)
          and all(c.get("status") != "proposed" for c in pr.get("changes", []) if c.get("target") in ("name", "legal", "bg")),
          f"proposed_targets={touched_ids} conflicts={len(_conflicts)}")
    # 影响范围：apply 前后真实 diff，仅 logo 尺寸变化
    pr_applied = requests.post(BASE + "/api/v1/revision/apply",
                                json={"objects": prot_objs, "changes": pr.get("changes", []),
                                      "only_ids": [c["id"] for c in _proposed]}).json()
    _after = {o["_id"]: o for o in pr_applied.get("objects", [])}
    others_unchanged = (_after.get("name", {}).get("font_size") == 24 and
                        _after.get("legal", {}).get("font_size") == 10 and
                        _after.get("bg", {}).get("width") == 300)
    _logo_w88 = _after.get("logo", {}).get("width")
    check("SUPER", "§88.3", "执行范围收敛：应用后仅 logo 变化，其余对象字节级保持原值",
          others_unchanged and isinstance(_logo_w88, (int, float)) and _logo_w88 != 100,
          f"logo_w={_logo_w88} others_intact={others_unchanged}")

    # =====================================================================
    # 第三部分：运行时守护 + 安装域可核验面（Phase 60 · L3 真实台补白）
    # 修前基线：本套此前对 runtime/installer 家族**零命名**
    # （p60_probe9_freeze.txt S6：12 枚关键词 hits 全 0；p60_probe17 逐套 claims=46 无一条此族）。
    # 下面每个字段名与取值形制逐字抄自同轮只读契约探针收据
    # p60_probe19_runtime_installer_contract.txt（17 次真请求 = 9 正向 + 1 端口交错 +
    # 3 单实例序列 + 5 负控；应答者身份核对 listener_is_our_child=true），不想象 schema。
    # 判据一律写成"派生式"（期望值由同轮响应/磁盘实算给出），不把手抄快照钉进断言。
    # =====================================================================
    import hashlib as _hl
    _PORT = int(BASE.rsplit(":", 1)[1])

    # ---------------- RUNTIME：运行时守护（第 22 引擎）----------------
    _rd = requests.get(BASE + "/api/v1/runtime/diagnostic", timeout=30).json()
    _rd_busy = requests.get(BASE + "/api/v1/runtime/diagnostic",
                            params={"service_port": _PORT}, timeout=30).json()
    _rnames = sorted({c.get("check") for c in _rd.get("checks", [])})
    _wr = next((c for c in _rd.get("checks", []) if c.get("check") == "data_root_writable"), {})
    _dr = str(tmp / "data")

    check("RUNTIME", "R01", "运行时诊断逐项由真实检查现场派生（checks_performed=实数、checks_failed=非 pass 计数、status 由 failed 派生、method=derived_from_real_checks、fabricated=false，且三类检查逐项在场）",
          _rd.get("schema") == "zaojing-runtime-guard/v1"
          and _rd.get("checks_performed") == len(_rd.get("checks", []))
          and _rd.get("checks_passed") + _rd.get("checks_failed") == _rd.get("checks_performed")
          and _rd.get("checks_failed") == sum(1 for c in _rd.get("checks", []) if c.get("status") != "pass")
          and _rd.get("status") == ("healthy" if _rd.get("checks_failed") == 0 else "degraded")
          and _rd.get("method") == "derived_from_real_checks"
          and _rd.get("fabricated") is False
          and {"data_root_writable", "disk_free", "python_version"} <= set(_rnames),
          json.dumps({"checks": _rnames, "performed": _rd.get("checks_performed"),
                      "failed": _rd.get("checks_failed"), "status": _rd.get("status")}, ensure_ascii=False))

    check("RUNTIME", "R02", "诊断量到的就是本轮这台被验证实例：data_root=验收台亲自交的沙箱根、pid=验收台亲自起的子进程、写入探针真落在该根内并逐字节读回（不是自报布尔，是 detail 里的实测串）",
          _rd.get("system", {}).get("data_root") == _dr
          and _rd.get("system", {}).get("pid") == proc.pid
          and _rd.get("system", {}).get("frozen") is False
          and _rd.get("system", {}).get("data_root_exists") is True
          and _rd.get("system", {}).get("fabricated") is False
          and _wr.get("detail", "").startswith(str(tmp / "data" / ".runtime-guard"))
          and "bytes_verified=True" in _wr.get("detail", "")
          and _rd.get("disk", {}).get("path") == _dr
          and _rd.get("disk", {}).get("method") == "shutil_disk_usage"
          and _rd.get("disk", {}).get("meets_min") is (
              _rd.get("disk", {}).get("free_bytes", -1) >= _rd.get("disk", {}).get("min_free_bytes", 0)),
          json.dumps({"data_root": _rd.get("system", {}).get("data_root"),
                      "pid_is_ours": _rd.get("system", {}).get("pid") == proc.pid,
                      "probe_detail": _wr.get("detail"),
                      "free_bytes": _rd.get("disk", {}).get("free_bytes")}, ensure_ascii=False))

    _pc = _rd_busy.get("port", {}) or {}
    check("RUNTIME", "R03", "已监听端口被如实判为不可用：把本实例自己正在监听的端口交给诊断 ⇒ 第四项 port_available=fail、port.available=false、method=real_tcp_bind_probe，status 随之降为 degraded（绝不因'那是我自己的端口'而谎报可用）",
          _pc.get("port") == _PORT and _pc.get("available") is False
          and _pc.get("method") == "real_tcp_bind_probe" and bool(_pc.get("detail"))
          and _rd_busy.get("checks_performed") == _rd.get("checks_performed") + 1
          and _rd_busy.get("checks_failed") == _rd.get("checks_failed") + 1
          and _rd_busy.get("status") == "degraded"
          and any(c.get("check") == "port_available" and c.get("status") == "fail"
                  for c in _rd_busy.get("checks", [])),
          json.dumps({"port": _pc.get("port"), "available": _pc.get("available"),
                      "detail": _pc.get("detail"), "status": _rd_busy.get("status")}, ensure_ascii=False))

    _pck = requests.post(BASE + "/api/v1/runtime/port-check", json={"port": _PORT}, timeout=20).json()
    _pck_bad = requests.post(BASE + "/api/v1/runtime/port-check", json={"port": "not-a-number"}, timeout=20)
    _pck_oob = requests.post(BASE + "/api/v1/runtime/port-check", json={"port": 70000}, timeout=20)
    check("RUNTIME", "R04", "port-check 与 diagnostic 对同一端口给出同一结论（独立两次真 bind，不互相抄），且 fabricated=false",
          _pck.get("available") is False and _pck.get("port") == _PORT
          and _pck.get("host") == _pc.get("host") and _pck.get("method") == "real_tcp_bind_probe"
          and _pck.get("fabricated") is False and _pck.get("available") == _pc.get("available"),
          json.dumps(_pck, ensure_ascii=False))

    check("RUNTIME", "R05", "入参非法一律诚实 400（类型错与越界各归各的错，绝不静默降级成 available=true 放行）",
          _pck_bad.status_code == 400 and "需为数值" in _pck_bad.json().get("detail", "")
          and _pck_oob.status_code == 400 and "1~65535" in _pck_oob.json().get("detail", ""),
          json.dumps({"str": _pck_bad.status_code, "oob": _pck_oob.status_code,
                      "msgs": [_pck_bad.json().get("detail"), _pck_oob.json().get("detail")]},
                     ensure_ascii=False))

    _iname = "zjaccept-%d" % proc.pid
    _si1 = requests.post(BASE + "/api/v1/runtime/single-instance",
                         json={"name": _iname, "action": "acquire"}, timeout=20).json()
    _si2 = requests.post(BASE + "/api/v1/runtime/single-instance",
                         json={"name": _iname, "action": "acquire"}, timeout=20).json()
    _si3 = requests.post(BASE + "/api/v1/runtime/single-instance",
                         json={"name": _iname, "action": "release"}, timeout=20).json()
    _si4 = requests.post(BASE + "/api/v1/runtime/single-instance",
                         json={"name": "zjaccept-bad", "action": "nuke"}, timeout=20)
    check("RUNTIME", "R06", "单实例互斥真驱动内核对象：首次 acquire=已持有、同名二次 acquire 如实 already_running（后端 kernel32-named-mutex、pid=被测进程）、release 真释放，非法 action 诚实 400",
          _si1.get("acquired") is True and _si1.get("already_running") is False
          and _si1.get("backend") == "kernel32-named-mutex" and _si1.get("pid") == proc.pid
          and _si2.get("acquired") is False and _si2.get("already_running") is True
          and _si2.get("name") == _iname and _si3.get("released") is True
          and _si3.get("fabricated") is False
          and _si4.status_code == 400 and "acquire/release" in _si4.json().get("detail", ""),
          json.dumps({"first": _si1, "again": {k: _si2.get(k) for k in ("acquired", "already_running")},
                      "released": _si3.get("released"), "bad_action": _si4.status_code}, ensure_ascii=False))

    _ia = requests.get(BASE + "/api/v1/runtime/install-actions", timeout=20).json()
    check("RUNTIME", "R07", "安装执行域的契约面由引擎现场派生：capability=installer_execution、status=executable_via_cli_only、门禁清单非空且含 confirmation_required、显式同意令牌逐字在场、判据口径 derived_from_disk_and_registry、退役边界保留 Phase 26 原句 needs_installer_environment（退役≠改写）",
          _ia.get("capability") == "installer_execution"
          and _ia.get("status") == "executable_via_cli_only"
          and len(_ia.get("gates", [])) >= 1 and "confirmation_required" in _ia.get("gates", [])
          and _ia.get("confirm_token") == "ZAOJING-CONFIRM-EXECUTE-INSTALLER"
          and str(_ia.get("verification_basis", "")).startswith("derived_from_disk_and_registry")
          and _ia.get("retired_boundary", {}).get("was") == "needs_installer_environment"
          and _ia.get("retired_boundary", {}).get("retired_in") == "Phase 58"
          and _ia.get("fabricated") is False,
          json.dumps({"gates": len(_ia.get("gates", [])), "actions": _ia.get("actions"),
                      "token": _ia.get("confirm_token"),
                      "retired": _ia.get("retired_boundary", {}).get("was")}, ensure_ascii=False))

    # ---------------- INSTALLER：安装清单 / 编译面 / 预检面 / 执行台账 ----------------
    _mv = requests.get(BASE + "/api/v1/installer/manifest/verify", timeout=60).json()
    _iss_path = Path(_mv.get("script_path", ""))
    _iss_bytes_disk = _iss_path.stat().st_size if _iss_path.is_file() else -1
    _iss_sha_disk = (_hl.sha256(_iss_path.read_bytes()).hexdigest()
                     if _iss_path.is_file() else "")
    check("INSTALLER", "I01", "静态校验真读盘真解析：脚本字节数与 sha256 与验收台独立实算逐字相等，findings 计数与分类自洽，且本引擎如实声明零执行（execution.performed=false / not_executed_by_this_engine）",
          _mv.get("schema") == "zaojing-installer-manifest/v1"
          and _mv.get("script_bytes") == _iss_bytes_disk
          and _mv.get("script_sha256") == _iss_sha_disk
          and _mv.get("counts", {}).get("total") == sum(
              _mv.get("counts", {}).get(k, 0) for k in ("errors", "warnings", "infos"))
          and _mv.get("findings") == []
          and _mv.get("execution", {}).get("performed") is False
          and _mv.get("execution", {}).get("status") == "not_executed_by_this_engine"
          and _mv.get("fabricated") is False,
          json.dumps({"verdict": _mv.get("verdict"), "script_bytes": _mv.get("script_bytes"),
                      "sha_eq_disk": _mv.get("script_sha256") == _iss_sha_disk,
                      "counts": _mv.get("counts")}, ensure_ascii=False))

    _st = requests.get(BASE + "/api/v1/installer/state", timeout=60).json()
    _reg_probes = [p for p in _st.get("probes", []) if p.get("probe") == "registry_uninstall"]
    check("INSTALLER", "I02", "安装态只由注册表卸载项真实命中派生（installed == 四视图探针的 any(present)，绝不凭目录/标记存在就说已安装），AppId 来源如实标注 setup_iss，卸载键与静态校验同一条派生链",
          _st.get("environment") == "windows"
          and len(_reg_probes) == 4 and {p.get("root") for p in _reg_probes} == {"HKCU", "HKLM"}
          and _st.get("installed") is any(p.get("present") for p in _reg_probes)
          and str(_st.get("installed_basis", "")).startswith("installed 仅由注册表卸载项真实命中派生")
          and _st.get("app_id_source") == "setup_iss"
          and _st.get("probes_seen") == len(_st.get("probes", []))
          and _st.get("derived_uninstall_key") == _mv.get("uninstall_registry_key")
          and _st.get("fabricated") is False,
          json.dumps({"installed": _st.get("installed"), "reg_views": len(_reg_probes),
                      "subkey_counts": [p.get("subkey_count") for p in _reg_probes],
                      "uninstall_key": _st.get("derived_uninstall_key")}, ensure_ascii=False))

    _af = requests.get(BASE + "/api/v1/installer/artifact", timeout=120).json()
    _ev_path = Path(_af.get("evidence_requested", ""))
    _ev_disk = json.loads(_ev_path.read_text(encoding="utf-8")) if _ev_path.is_file() else {}
    _art_path = Path((_af.get("artifact") or {}).get("path", ""))
    _art_sha_disk = (_hl.sha256(_art_path.read_bytes()).hexdigest()
                     if _art_path.is_file() else "")
    _sig = _af.get("signals", {})
    _derive = ("evidence_incomplete" if _af.get("missing_fields")
               else "artifact_missing" if not (_af.get("artifact") or {}).get("present")
               else "compile_not_proven" if not _af.get("compile_succeeded")
               else "evidence_mismatch" if _af.get("blocking")
               else "artifact_verified")
    check("INSTALLER", "I03", "编译证据 vs 磁盘实况逐字段重算对账：verdict 与其自身信号按同一规则派生，脚本/产物的 expected 来自证据文件而 observed 由验收台独立实算三方相等，自报布尔不参与判定（ignored=true），产物在盘绝不等于已安装（installed_claimed 恒 false）",
          _af.get("face") == "installer_artifact_build"
          and _af.get("verdict") == _derive
          and _af.get("missing_fields") == [] and _af.get("blocking") == []
          and _af.get("script", {}).get("expected", {}).get("sha256") == _ev_disk.get("script_sha256")
          and _af.get("script", {}).get("observed", {}).get("sha256") == _iss_sha_disk
          and _af.get("script", {}).get("sha256_match") is True
          and _af.get("artifact", {}).get("expected", {}).get("sha256") == _ev_disk.get("artifact_sha256")
          and _af.get("artifact", {}).get("observed", {}).get("sha256") == _art_sha_disk
          and _af.get("artifact", {}).get("sha256_match") is True
          and _af.get("artifact", {}).get("expected", {}).get("bytes") == _ev_disk.get("artifact_bytes")
          and _af.get("artifact", {}).get("observed", {}).get("bytes") == (
              _art_path.stat().st_size if _art_path.is_file() else -1)
          and _sig.get("iscc_exit_code") == 0 and _sig.get("compile_line_seen") is True
          and _af.get("evidence_self_report", {}).get("ignored") is True
          and _af.get("not_install", {}).get("installed_claimed") is False
          and _af.get("fabricated") is False,
          json.dumps({"verdict": _af.get("verdict"), "derived_expect": _derive,
                      "artifact_bytes": _af.get("artifact", {}).get("observed", {}).get("bytes"),
                      "sha_triple_ok": (_af.get("artifact", {}).get("expected", {}).get("sha256")
                                        == _ev_disk.get("artifact_sha256")
                                        == _af.get("artifact", {}).get("observed", {}).get("sha256")),
                      "compile_line_text": _sig.get("compile_line_text")}, ensure_ascii=False))

    _pf = requests.get(BASE + "/api/v1/installer/preflight", timeout=120).json()
    _pfbad = requests.get(BASE + "/api/v1/installer/preflight", params={"probe": "maybe"}, timeout=30)
    _pfout = requests.get(BASE + "/api/v1/installer/preflight",
                          params={"probe": "true",
                                  "target_override": str(Path(os.environ.get(
                                      "ProgramFiles", r"C:\Program Files")) / "zj-accept-probe.bin")},
                          timeout=30)
    _chk = _pf.get("checks", {})
    _ds = _chk.get("disk_space", {})
    _derive_pf = ("preflight_blocked" if _pf.get("blocking")
                  else "preflight_unknown" if _pf.get("unknown_codes")
                  else "preflight_ready")
    check("INSTALLER", "I04", "预检 verdict 由其自身 blocking/unknowns 按规则派生；写探针未获准时如实进 unknowns 且 result=null（'没探测'绝不写成'不可写'，也绝不写成'可写'），七面检查齐全",
          _pf.get("face") == "installer_preflight"
          and _pf.get("verdict") == _derive_pf
          and _chk.get("writability", {}).get("result") is None
          and _chk.get("writability", {}).get("probed") is False
          and "write_probe_unprobed" in _pf.get("unknown_codes", [])
          and {"privileges", "install_root", "target_conflict", "writability",
               "artifact", "disk_space", "payload"} <= set(_chk.keys())
          and _pf.get("probe_request", {}).get("ran") is False
          and _pf.get("fabricated") is False,
          json.dumps({"verdict": _pf.get("verdict"), "derived_expect": _derive_pf,
                      "unknowns": _pf.get("unknown_codes"),
                      "writability": _chk.get("writability", {}).get("result")}, ensure_ascii=False))

    check("INSTALLER", "I05", "预检的磁盘需求是实测和而非外推系数（required_bytes == artifact_bytes + payload_bytes，且 result 与 free>=required 同向）；产物面复用编译面结论、载荷逐项在盘",
          _ds.get("required_bytes") == _ds.get("artifact_bytes") + _ds.get("payload_bytes")
          and _ds.get("result") is (_ds.get("free_bytes", -1) >= _ds.get("required_bytes", 0))
          and _chk.get("artifact", {}).get("verdict") == _af.get("verdict")
          and _chk.get("payload", {}).get("missing_count") == 0
          and _chk.get("payload", {}).get("present_count") == _chk.get("payload", {}).get("declared_sources")
          and _chk.get("install_root", {}).get("result") is True
          and _pf.get("not_install", {}).get("installed_claimed") is False
          and _pf.get("not_install", {}).get("preflight_is_not_install") is True
          and _pf.get("execution", {}).get("performed") is False,
          json.dumps({"required": _ds.get("required_bytes"),
                      "sum_ok": _ds.get("required_bytes") == (
                          _ds.get("artifact_bytes") or 0) + (_ds.get("payload_bytes") or 0),
                      "free": _ds.get("free_bytes"), "measured_on": _ds.get("measured_on"),
                      "payload_present": _chk.get("payload", {}).get("present_count")}, ensure_ascii=False))

    check("INSTALLER", "I06", "预检的两条形制负控如实拒绝：probe 参数非布尔词 → 400 并回显全部被接受写法；target_override 指向真实安装根 → 403 且回显允许根与理由（真实安装根在 HTTP 面上结构性不可达，写探针到不了 Program Files）",
          _pfbad.status_code == 400
          and _pfbad.json().get("detail", {}).get("reason") == "preflight_probe_param_invalid"
          and len(_pfbad.json().get("detail", {}).get("accepted", [])) == 8
          and _pfbad.json().get("detail", {}).get("fabricated") is False
          and _pfout.status_code == 403
          and _pfout.json().get("detail", {}).get("reason") == "preflight_override_outside_allowed_roots"
          and bool(_pfout.json().get("detail", {}).get("allowed_roots"))
          and _pfout.json().get("detail", {}).get("fabricated") is False,
          json.dumps({"bad_probe": _pfbad.status_code,
                      "accepted": _pfbad.json().get("detail", {}).get("accepted"),
                      "outside_root": _pfout.status_code,
                      "reason": _pfout.json().get("detail", {}).get("reason"),
                      "roots": _pfout.json().get("detail", {}).get("allowed_roots")}, ensure_ascii=False))

    _afx = requests.get(BASE + "/api/v1/installer/artifact",
                        params={"evidence_path": str(Path(
                            os.environ.get("SystemRoot", r"C:\Windows")) / "zj-accept-evidence.json")},
                        timeout=30)
    check("INSTALLER", "I07", "越界路径一律诚实 403 而非'读不到就当没有'：把证据指到系统目录被安全策略挡回，路径原样回显（只核验本产品的构建物是可断言的事实）",
          _afx.status_code == 403 and "路径被安全策略阻止" in _afx.json().get("detail", "")
          and str(_afx.json().get("detail", "")).count("zj-accept-evidence.json") == 1,
          json.dumps({"status": _afx.status_code, "detail": _afx.json().get("detail")}, ensure_ascii=False))

    _ex = requests.get(BASE + "/api/v1/installer/execution", timeout=60).json()
    check("INSTALLER", "I08", "执行台账对账在沙箱数据根内如实 ledger_absent：台账路径钉在本服务自己的 DATA_ROOT 下（可被指到别处就不叫对账而叫投毒）、mismatches 空、自报字段清单逐枚在场而判定基础恒为现场磁盘+注册表",
          _ex.get("capability") == "installer_execution"
          and _ex.get("status") == "ledger_absent"
          and _ex.get("ledger", {}).get("exists") is False
          and str(_ex.get("ledger", {}).get("path", "")).startswith(_dr)
          and _ex.get("mismatches") == [] and _ex.get("fabricated") is False
          and "install.installed" in _ex.get("self_report_ignored", [])
          and len(_ex.get("self_report_ignored", [])) == 7
          and _ex.get("registry_keys", {}).get("source") == "setup_iss"
          and _ex.get("registry_keys", {}).get("uninstall_key") == _st.get("derived_uninstall_key"),
          json.dumps({"status": _ex.get("status"), "ledger_path": _ex.get("ledger", {}).get("path"),
                      "ignored": _ex.get("self_report_ignored"),
                      "reason": _ex.get("reason")}, ensure_ascii=False))

    _oa = requests.get(BASE + "/openapi.json", timeout=30).json()
    _all_paths = {p: sorted(m.upper() for m in (v or {}).keys())
                  for p, v in (_oa.get("paths") or {}).items()}
    # P60 就地改核（初版判断失误，在此更正）：本段首版用 startswith 加一条带引号、带尾斜杠的
    # 安装器族「前缀」字面串做过滤，并在注释里把这个前缀也用引号包了一遍。L2 的 acceptance-coverage
    # 尺从五套测试源源码里抽「引号/反引号紧包的 api/v1 开头字面串」（三正则），经 _n41 归一（去尾
    # 斜杠）后，那条前缀归一结果不属于 135 枚模板中的任何一枚 ⇒ 判成幽灵字面串，当场判红。凭证：
    # p60_7b_L2a_ghost_red.log / p60_7b_L2b_ghost_red.log 第 33 行同一条唯一 [FAIL]（ghost 一条、
    # per_suite 里 L3=55）；这两份 RED 凭证在成对重跑前已另名留盘，原 p60_7b_L2a/L2b.log 会被覆写。
    # 初版把这条红记作「字面串尺子的已知盲区，如实申报不改码凑数」——该判断**作废**：同一形状早在
    # Phase 58 就在本仓库留过教训（见 test-full-functional.py 第 922–931 段的原文与等价写法），
    # 「每条被引号包住的 URL 字面串必须归属一枚模板」是尺子故意设的强不变式，注释里写这个形状同样
    # 入扫描面。所以改的是**写断言的方式**（换成段判定，与 startswith 那条过滤语义逐字等价：都是
    # 「路径第 3 段是 installer」），不给尺子开口子、不放松判据、不改尺子口径。
    _inst_methods = {p: ms for p, ms in _all_paths.items()
                     if p.strip("/").split("/")[2:3] == ["installer"]}
    # 期望值一律由本轮现场派生，不写裸计数：本段真驱动过的安装域路由清单来自验收台自己的
    # 调用序列（I01–I08 逐条打过的 path），与 /openapi.json 现场列出的安装域路由做**集合互证**。
    # Phase 60 就地改核：首版此处写 `len(_inst_methods) >= 6`，那个"6"抄自 agent_service 注释
    # 里的"安装域 6 条全 GET"——而注释的口径把 runtime 域的 GET /api/v1/runtime/install-actions
    # 也算进了安装域契约面，按 /api/v1/installer/ 前缀实数是 5 条。裸计数不带域名 ⇒ 第一轮就
    # 把这条判据判红（凭证：p60_l3_run1_stdout.txt 唯一 [FAIL] 与 results.json I09 行）。
    # 修的是尺子（改成派生式集合互证），产品侧同轮把注释改为逐条点名，两侧同时收口。
    _inst_drove = {"/api/v1/installer/manifest/verify", "/api/v1/installer/state",
                   "/api/v1/installer/artifact", "/api/v1/installer/preflight",
                   "/api/v1/installer/execution"}
    _install_surface = sorted(set(_inst_methods) | {"/api/v1/runtime/install-actions"})
    _i09 = {
        "openapi枚举可用(正控:安装域非空且本段真驱动的路由全在其中)": bool(_inst_methods) and _inst_drove <= set(_all_paths),
        "安装域每条路由只有GET": bool(_inst_methods) and all(ms == ["GET"] for ms in _inst_methods.values()),
        "安装域无一条未被真实台驱动": set(_inst_methods) == _inst_drove,
        "install-actions也是GET(安装契约面全GET)": _all_paths.get("/api/v1/runtime/install-actions") == ["GET"],
        "安装面六条=前缀5条+install-actions1条(派生恒等式)": len(_install_surface) == len(_inst_methods) + 1,
        "契约自陈HTTP走不到执行器": "不存在任何非 GET 路由" in _ia.get("http_surface", ""),
        "现场未安装": _st.get("installed") is False,
        "台账如实缺席": _ex.get("status") == "ledger_absent",
        "编译面不声称已安装": _af.get("not_install", {}).get("installed_claimed") is False,
    }
    gate("INSTALLER", "I09",
         "安装执行域在 HTTP 面上结构性不可达：/openapi.json 现场派生 ⇒ 安装域每条路由只有 GET、且安装域无一条未被本段驱动（枚举器数得出又不漏），第 6 条安装契约面 install-actions 同为 GET（集合大小由派生恒等式给出，不抄裸计数）；本服务按红线如实拒绝从 HTTP 触发安装，绝不代执行器声称装了什么",
         all(_i09.values()),
         json.dumps({"installer_methods": _inst_methods, "install_surface": _install_surface,
                     "undriven": sorted(set(_inst_methods) - _inst_drove),
                     "ghost": sorted(_inst_drove - set(_inst_methods)),
                     "legs": _i09}, ensure_ascii=False))

    # =====================================================================
    # 第四部分：性能 §85（真实墙钟计时，无 sleep / 无 random）
    # =====================================================================
    def _timed(fn):
        t = time.perf_counter(); r = fn(); return (time.perf_counter() - t) * 1000.0, r

    perf = {}
    m, _ = _timed(lambda: requests.get(BASE + "/api/v1/health", timeout=5).json())
    perf["health_ms"] = round(m, 2)
    m, _ = _timed(lambda: requests.post(BASE + "/api/v1/color/palette", json={"path": str(img_green), "k": 6}, timeout=10).json())
    perf["color_palette_ms"] = round(m, 2)
    m, sr = _timed(lambda: requests.post(BASE + "/api/v1/assets/search", json={"query": "茶叶 绿色 产品", "top_k": 5}, timeout=10).json())
    perf["asset_vector_search_ms"] = round(m, 2); perf["asset_search_hits"] = sr.get("total")
    m, _ = _timed(lambda: requests.post(BASE + "/api/v1/knowledge/retrieve", json={"query": "品牌 主色", "top_k": 5}, timeout=10).json())
    perf["knowledge_retrieve_ms"] = round(m, 2)
    m, _ = _timed(lambda: requests.post(BASE + "/api/v1/layout/analyze", json={"objects": objs, "canvas": {"width": 500, "height": 500}}, timeout=5).json())
    perf["layout_analyze_ms"] = round(m, 2)
    _bc = tmp / "ean13.png"
    m, _ = _timed(lambda: requests.post(BASE + "/api/v1/packaging/barcode",
                                         json={"code": "5901234123457", "symbology": "EAN13", "output_path": str(_bc)}, timeout=10).json())
    perf["barcode_render_ms"] = round(m, 2)
    m, _ = _timed(lambda: requests.post(BASE + "/api/v1/preflight", json={"document_path": str(img_product)}, timeout=10).json())
    perf["preflight_ms"] = round(m, 2)
    # 批量吞吐（真实并发 12 文件 import）
    m, _ = _timed(lambda: requests.post(BASE + "/api/v1/batch/process",
                                         json={"file_paths": batch_imgs, "operation": "preflight", "workers": 6}, timeout=30).json())
    perf["batch_12_preflight_ms"] = round(m, 2)

    _perf_ms = [k for k in perf if k.endswith("_ms")]
    _ms_positive_finite = all(isinstance(perf[k], (int, float)) and 0 < perf[k] < float("inf") for k in _perf_ms)
    _search_hits = perf.get("asset_search_hits") or 0
    check("PERF", "metrics", "真实性能指标已测（毫秒为墙钟，非伪造）",
          len(_perf_ms) == 8 and _ms_positive_finite and _search_hits >= 1,
          json.dumps(perf, ensure_ascii=False))
    # 性能不达标只告警不判 FAIL（本机/CI 抖动），但须为正数且合理
    for k in ("health_ms", "color_palette_ms", "asset_vector_search_ms"):
        check("PERF", k, f"{k} 为真实正耗时且 <2000ms", 0 < perf[k] < 2000, f"{perf[k]}ms")

    # =====================================================================
    # 汇总
    # =====================================================================
    n_pass = sum(1 for r in _rows if r["kind"] == PASS)
    n_gate = sum(1 for r in _rows if r["kind"] == GATE)
    n_fail = sum(1 for r in _rows if r["kind"] == FAIL)
    summary = {
        "suite": "acceptance-real",
        "note": "真实驱动服务；GATE=按 §2 红线诚实降级(需 Adobe 宿主/Provider/渲染器)，非伪造成功",
        "golden_total": sum(1 for r in _rows if r["section"] == "GOLDEN"),
        "super_total": sum(1 for r in _rows if r["section"] == "SUPER"),
        # Phase 60 新增两段（运行时守护 + 安装域可核验面）；计数由 _rows 现场派生，不写死
        "runtime_total": sum(1 for r in _rows if r["section"] == "RUNTIME"),
        "installer_total": sum(1 for r in _rows if r["section"] == "INSTALLER"),
        "real_pass": n_pass, "honest_gates": n_gate, "failures": n_fail,
        "performance_ms": perf,
    }
    print("\n" + "=" * 68)
    print(f"真实验收汇总: 通过 {n_pass} | 诚实降级(GATE) {n_gate} | 失败 {n_fail}")
    print(f"判定: {'GREEN (零伪造/零假成功)' if n_fail == 0 else 'RED — 存在失败'}")
    print("=" * 68)

finally:
    try:
        proc.terminate(); proc.wait(timeout=10)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass
    try:
        srv_log.close()
    except Exception:
        pass

outdir = OUT / "outputs"
outdir.mkdir(parents=True, exist_ok=True)
(outdir / "ACCEPTANCE-REAL-RESULTS.json").write_text(
    json.dumps({"summary": summary, "results": _rows}, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"\n详细结果已保存: {outdir / 'ACCEPTANCE-REAL-RESULTS.json'}")
