"""造境 AI 设计智能体 · 第 27 引擎：desktop_hub_engine（本地桌面 Hub 窗口）

§5 的"桌面 Hub"过去被登记为 not_shipped，理由是"前端 Hub 为独立桌面应用、不在本
FastAPI 服务交付范围"——那是把能力**误绑在 Tauri 工具链**上的判词（同 Phase 47 被纯
几何反解推翻的"需真实 CV"一类）。本引擎用纯标准库 `tkinter` + `urllib` 真实交付这个
客户端：一个原生窗口，真连 127.0.0.1 上正在跑的服务，读回**健康 / 能力真值表 / 评测
报告**三条只读 GET，并把它们逐项搬进窗口。

三条不可让的纪律（每条都在 L2/L4 与评测台里有活体判据盯着）：

1. **只搬运，不编数**。窗口里出现的每一个数字都必须能追溯到某次真实 GET 的响应
   字段；`hub_snapshot()` 里没有任何一处写死的服务数据。读不到就是读不到：对应字段
   置 None、列进 `unavailable_fields`、`degraded` 给出真实异常原文，绝不把 0 或
   "33 supported"当占位填上去。
2. **开窗只读，动作必点**。`hub_snapshot()` 全程只有 GET；唯一的 POST 通道是
   `perform_action()`，而它只挂在按钮的 command 上。判据形式：开窗后该路由
   `route_hits` 增量为 0，`invoke()` 之后增量为 1。
3. **窗口显示 == 派生行**。`render_rows()` 是快照的纯函数（无 Tk 参与），窗口里每个
   Label 的 `cget("text")` 必须逐字等于它吐出的行——这样"GUI 只是另一套常量的表演"
   这种桩会被当场抓红。

无显示会话（服务/CI/远程桌面断开）不是失败条件：`open_hub()` 如实回
`window.created=False, reason=no_display_available`，数据层仍可为真。

自测分层（本模块的 `__main__` 即 L1 第 27 项）：L1 在**进程内起一个 stdlib
http.server**（127.0.0.1:0），让 `hub_snapshot/render_rows/perform_action` 走真
socket、真 urllib——刻意不把 urllib 换成桩，因为本能力值钱的地方正是"真响应才写真数"
这条传输链，换桩就退化成"常量等于常量"。L1 绝不建窗；建窗判据（Label 文本逐字 ==
派生行、开窗 POST 增量为 0、显式点击增量恰为 1）属 L2/L4 活体判据。
"""
from __future__ import annotations

import json
import os
import socket
import time
import urllib.error
import urllib.request
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

SCHEMA = "zaojing-desktop-hub/v1"
DEFAULT_TIMEOUT = 5.0
DEFAULT_BASE = "http://127.0.0.1:8000"

# 开窗即读的三条只读源（全部 GET；POST 只能走 perform_action）
READ_SOURCES: Tuple[Tuple[str, str], ...] = (
    ("health", "/api/v1/health"),
    ("capabilities", "/api/v1/capabilities"),
    ("eval_reports", "/api/v1/eval/reports"),
)

# 显式点击才允许触发的动作表。mutating=True 表示会在服务端/磁盘产生真实副作用，
# 这类动作**绝不**在开窗时自动执行；effect/note 写明真实后果，不谎称无副作用。
ACTIONS: Dict[str, Dict[str, Any]] = {
    "refresh": {
        "label": "刷新真值（三条只读 GET）",
        "method": "GET",
        "paths": [p for _, p in READ_SOURCES],
        "mutating": False,
        "note": "重跑 hub_snapshot()，只读、零落盘",
    },
    "eval_run": {
        "label": "跑一次真实评测（POST /api/v1/eval/run）",
        "method": "POST",
        "paths": ["/api/v1/eval/run"],
        "mutating": True,
        "note": "真实执行评测用例并在数据根 evaluation/reports/ 落一份新报告；"
                "非幂等（每次新增一份报告文件），故必须显式点击",
    },
}


class HubError(Exception):
    """Hub 侧可判别的输入错误（基址非法 / 动作未知），对应诚实 4xx 语义。"""


# ---------------------------------------------------------------------------
# 传输层：stdlib urllib，逐次真实请求，异常原文照抄
# ---------------------------------------------------------------------------

def resolve_base(explicit: Optional[str] = None) -> Tuple[str, str]:
    """基址来源必须可追溯到 cli/env/default 三者之一，不静默猜测。"""
    if explicit:
        return explicit.rstrip("/"), "cli"
    port = os.environ.get("ZAOJING_PORT")
    if port:
        return "http://127.0.0.1:%s" % port, "env:ZAOJING_PORT"
    return DEFAULT_BASE, "default"


def _error_text(exc: Exception) -> str:
    # WinError 10061 之类的中文消息随系统语言变化，故同时留 error_class 与
    # 数字码，判据钉可跨 locale 的那部分。
    return str(exc)[:300]


def http_json(base: str, path: str, timeout: float = DEFAULT_TIMEOUT) -> Dict[str, Any]:
    """真实 GET 一个 JSON 端点。返回一份**自述观测**，不抛异常：
    ok/transport/verdict 三轴把"连不上""不是造境服务""响应不是 JSON"分清。
    """
    url = base + path
    obs: Dict[str, Any] = {"url": url, "method": "GET"}
    t0 = time.perf_counter()
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            obs["status"] = resp.status
            obs["content_type"] = resp.headers.get("content-type")
    except urllib.error.HTTPError as e:  # 4xx/5xx 也是真实响应，须照抄
        obs["status"] = e.code
        obs["content_type"] = (e.headers.get("content-type") if e.headers else None)
        obs["transport"] = "http_error"
        obs["error_class"] = "HTTPError"
        obs["error_text"] = _error_text(e)
        obs["verdict"] = "not_zaojing_service" if e.code == 404 else "service_error"
        obs["bytes"] = 0
        obs["json"] = None
        obs["elapsed_ms"] = round((time.perf_counter() - t0) * 1000)
        return obs
    except Exception as e:  # noqa: BLE001
        obs["transport"] = "connection_failed"
        obs["error_class"] = type(e).__name__
        obs["error_text"] = _error_text(e)
        obs["verdict"] = "connection_failed"
        obs["bytes"] = 0
        obs["json"] = None
        obs["elapsed_ms"] = round((time.perf_counter() - t0) * 1000)
        return obs
    obs["bytes"] = len(raw)
    obs["elapsed_ms"] = round((time.perf_counter() - t0) * 1000)
    ctype = (obs.get("content_type") or "").lower()
    if "json" not in ctype:
        # 端口通了但回的不是 JSON（如被别的 http.server 占着）——必须判"不是本服务"，
        # 绝不能把 HTML 里的字符当成数值搬运。
        obs["transport"] = "non_json"
        obs["verdict"] = "not_zaojing_service"
        obs["body_head"] = raw[:120].decode("utf-8", "replace")
        obs["json"] = None
        return obs
    try:
        obs["json"] = json.loads(raw.decode("utf-8"))
        obs["transport"] = "ok"
        obs["verdict"] = "zaojing_payload"
    except Exception as e:  # noqa: BLE001
        obs["transport"] = "malformed_json"
        obs["verdict"] = "malformed_json"
        obs["error_class"] = type(e).__name__
        obs["error_text"] = _error_text(e)
        obs["body_head"] = raw[:120].decode("utf-8", "replace")
        obs["json"] = None
    return obs


def _looks_like_zaojing_health(payload: Any) -> bool:
    return isinstance(payload, dict) and "status" in payload and "version" in payload


# ---------------------------------------------------------------------------
# 数据层：快照 = 三次真实 GET 的逐字段搬运 + 跨源自洽派生
# ---------------------------------------------------------------------------

def hub_snapshot(base_url: Optional[str] = None, timeout: float = DEFAULT_TIMEOUT,
                 sources: Tuple[Tuple[str, str], ...] = READ_SOURCES) -> Dict[str, Any]:
    base, base_source = resolve_base(base_url)
    if "://" not in base:
        raise HubError("base_url 非法: %r" % base_url)
    obs: Dict[str, Any] = {}
    for name, path in sources:
        obs[name] = http_json(base, path, timeout=timeout)

    snap: Dict[str, Any] = {
        "schema": SCHEMA,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "base_url": base,
        "base_url_source": base_source,
        "read_only": True,
        "actions_performed": [],
        "fabricated": False,
        "sources": obs,
    }
    h = obs.get("health") or {}
    c = obs.get("capabilities") or {}
    r = obs.get("eval_reports") or {}
    hp, cp, rp = h.get("json"), c.get("json"), r.get("json")
    unavailable: List[str] = []

    # —— 服务身份：health 形状不对也不能装成"已连上" ——
    health_ok = h.get("verdict") == "zaojing_payload" and _looks_like_zaojing_health(hp)
    snap["service"] = {
        "reachable": h.get("verdict") != "connection_failed",
        "health_verdict": h.get("verdict"),
        # 形状门单独自报：P50 探针抓到"HTTP 200 且是 JSON 但缺 version"的 health 会被
        # 当成 zaojing_payload，于是派生行写成"造境 AI vNone · None · 运行 Nones"——
        # 那是用 None 冒充数值，正是本能力要防的编数形态，故形状不合即视为该源降级。
        "shape_valid": bool(health_ok) if h.get("verdict") == "zaojing_payload" else None,
        "version": (hp or {}).get("version") if health_ok else None,
        "status": (hp or {}).get("status") if health_ok else None,
        "mode": (hp or {}).get("mode") if health_ok else None,
        "uptime_seconds": (hp or {}).get("uptime_seconds") if health_ok else None,
        "data_root": (hp or {}).get("data_root") if health_ok else None,
    }
    if not health_ok:
        unavailable += ["service.version", "service.status", "service.mode",
                        "engines", "cross_source_engines_agree"]

    engines_health = sorted((hp or {}).get("engines") or []) if health_ok else []
    caps_ok = c.get("verdict") == "zaojing_payload" and isinstance(cp, dict) and "summary" in cp
    engines_caps = sorted(cp.get("engines_loaded") or []) if caps_ok else []
    snap["engines"] = {
        "health_reported": engines_health,
        "health_ready_count": sum(1 for v in ((hp or {}).get("engines") or {}).values()
                                  if v == "ready") if health_ok else None,
        "capabilities_reported": engines_caps,
        # /health 与 /capabilities 两处自报必须同源；不一致要在窗口里显形，不许静默
        "agree": (engines_health == engines_caps) if (health_ok and caps_ok) else None,
    }
    if not caps_ok:
        unavailable += ["capabilities", "registry"]

    caps = (cp or {}).get("capabilities") if caps_ok else None
    reg = ((cp or {}).get("registry") or {}).get("capabilities") if caps_ok else None
    snap["capabilities"] = {
        "summary": (cp or {}).get("summary") if caps_ok else None,
        "items_seen": len(caps) if isinstance(caps, list) else None,
        "not_supported_ids": sorted(x.get("id") for x in caps
                                    if x.get("status") != "supported")
        if isinstance(caps, list) else None,
        "registry_verified_count": sum(1 for x in reg if x.get("verified") is True)
        if isinstance(reg, list) else None,
        "registry_honest_boundary_ids": sorted(x.get("id") for x in reg
                                               if x.get("evidence_kind") == "honest_boundary")
        if isinstance(reg, list) else None,
    }
    reports = (rp or {}).get("reports") if isinstance(rp, dict) else None
    latest = None
    if isinstance(reports, list) and reports:
        latest = max(reports, key=lambda x: str(x.get("finished_at") or ""))
    snap["evaluation"] = {
        "reports_on_disk": len(reports) if isinstance(reports, list) else None,
        "latest": latest,
        "picked_by": "max(finished_at)" if latest else None,
    }
    if r.get("verdict") != "zaojing_payload":
        unavailable += ["evaluation"]

    degraded = [k for k in ("health", "capabilities", "eval_reports")
                if obs[k].get("verdict") != "zaojing_payload"]
    # 形状不合的 health 同样算该源失败：verdict 只说明"回了 JSON"，不代表回了造境契约
    if health_ok is False and "health" not in degraded:
        degraded.append("health")
        degraded.sort(key=("health", "capabilities", "eval_reports").index)
    snap["degraded"] = {
        "any": bool(degraded),
        "failed_sources": degraded,
        # 降级原因只回显真实异常原文；没有任何失败时为 None，而不是"ok"这种编出来的词
        "reasons": {k: {"verdict": obs[k].get("verdict"),
                        "error_class": obs[k].get("error_class"),
                        "error_text": obs[k].get("error_text"),
                        "status": obs[k].get("status"),
                        "shape_valid": (obs[k].get("verdict") == "zaojing_payload"
                                        and bool(health_ok)) if k == "health" else None}
                    for k in degraded},
    }
    snap["unavailable_fields"] = unavailable
    snap["all_sources_live"] = not degraded
    return snap


# ---------------------------------------------------------------------------
# 呈现层：纯函数派生行（无 Tk），窗口的唯一真值来源
# ---------------------------------------------------------------------------

def render_rows(snap: Dict[str, Any]) -> List[str]:
    """把快照派生成窗口要显示的行。任何一行都只可能来自 snap 已有字段。"""
    rows: List[str] = []
    svc, eng, cap, ev = snap["service"], snap["engines"], snap["capabilities"], snap["evaluation"]
    rows.append("Hub 目标 %s（来源 %s）" % (snap["base_url"], snap["base_url_source"]))
    if svc["health_verdict"] == "zaojing_payload" and svc.get("shape_valid"):
        rows.append("服务 造境 AI v%s · %s · 运行 %ss" % (
            svc["version"], svc["status"], svc["uptime_seconds"]))
    elif svc["health_verdict"] == "zaojing_payload":
        # 回了合法 JSON 却不是造境的 health 契约：如实说明形状不合，绝不写 None 占位
        rows.append("服务回了 JSON 但形状不合造境 health 契约（缺 status/version），"
                    "身份字段一律留空")
    elif svc["reachable"]:
        rows.append("端口在，但不是造境服务（%s）" % svc["health_verdict"])
    else:
        snap_reason = (snap["degraded"]["reasons"].get("health") or {})
        rows.append("连不上服务：%s %s" % (
            snap_reason.get("error_class"), snap_reason.get("error_text")))
    if svc["data_root"]:
        rows.append("数据根 %s" % svc["data_root"])
    if eng["health_reported"]:
        rows.append("引擎 %d/%d ready（/health 与 /capabilities 同源=%s）" % (
            eng["health_ready_count"], len(eng["health_reported"]), eng["agree"]))
    s = cap["summary"]
    if s:
        rows.append("能力真值表 %d 项：supported %d / needs_adobe_host %d / "
                    "prototype_not_shipped %d / not_shipped %d" % (
                        s.get("total"), s.get("supported"), s.get("needs_adobe_host"),
                        s.get("prototype_not_shipped"), s.get("not_shipped")))
        rows.append("未 supported 的能力：%s" % "、".join(cap["not_supported_ids"]))
        rows.append("Registry 已验证 %d 项（honest_boundary %d 项）" % (
            cap["registry_verified_count"], len(cap["registry_honest_boundary_ids"])))
    if ev["reports_on_disk"] is not None:
        if ev["latest"]:
            rows.append("最新评测 %s：%d 用例执行 · pass_rate %s · honest=%s" % (
                ev["latest"].get("report_id"), ev["latest"].get("executed_cases"),
                ev["latest"].get("pass_rate"), ev["latest"].get("honest")))
        else:
            rows.append("评测报告：数据根下 0 份（尚未跑过评测，不编分数）")
    if snap["degraded"]["any"]:
        rows.append("降级中：%s —— 以上缺失字段一律留空，未填任何假数据" %
                    "、".join(snap["degraded"]["failed_sources"]))
    return rows


# ---------------------------------------------------------------------------
# 窗口层：tkinter；显式点击才允许调用 action
# ---------------------------------------------------------------------------

def perform_action(base_url: str, action_id: str,
                   timeout: float = DEFAULT_TIMEOUT) -> Dict[str, Any]:
    """POST 通道**只在这里**。未知动作直接 HubError，不静默忽略。"""
    act = ACTIONS.get(action_id)
    if act is None:
        raise HubError("unknown_action: %r" % action_id)
    out: Dict[str, Any] = {"action": action_id, "mutating": act["mutating"],
                           "executed_at": datetime.now().isoformat(timespec="seconds")}
    results = []
    for path in act["paths"]:
        if act["method"] == "POST":
            url = base_url + path
            try:
                req = urllib.request.Request(url, data=b"{}", method="POST",
                                             headers={"Content-Type": "application/json"})
                t0 = time.perf_counter()
                with urllib.request.urlopen(req, timeout=max(timeout, 300.0)) as resp:
                    raw = resp.read()
                    results.append({"url": url, "method": "POST", "status": resp.status,
                                    "bytes": len(raw),
                                    "elapsed_ms": round((time.perf_counter() - t0) * 1000)})
            except urllib.error.HTTPError as e:
                results.append({"url": url, "method": "POST", "status": e.code,
                                "error_class": "HTTPError"})
            except Exception as e:  # noqa: BLE001
                results.append({"url": url, "method": "POST", "error_class": type(e).__name__,
                                "error_text": _error_text(e)})
        else:
            results.append(http_json(base_url, path, timeout=timeout))
    out["results"] = results
    return out


def open_hub(base_url: Optional[str] = None, snapshot: Optional[Dict[str, Any]] = None,
             enter_mainloop: bool = True, timeout: float = DEFAULT_TIMEOUT,
             action_hook: Optional[Callable[[str], Any]] = None) -> Dict[str, Any]:
    """真建一个原生窗口。enter_mainloop=False 供验收驱动使用（建完即回，不阻塞）。

    action_hook 只用于把"点击"这一事件记进返回值的 click_log（判据据此核对
    route_hits 增量）；真正的网络动作仍由 perform_action 发起。
    """
    report: Dict[str, Any] = {"schema": SCHEMA, "click_log": [], "fabricated": False}
    snap = snapshot if snapshot is not None else hub_snapshot(base_url, timeout=timeout)
    report["base_url"] = snap["base_url"]
    report["snapshot"] = snap
    rows = render_rows(snap)
    report["rows"] = rows
    if os.environ.get("ZAOJING_HUB_HEADLESS") == "1":
        report["window"] = {"created": False, "reason": "headless_requested",
                            "rows_would_render": len(rows)}
        return report
    try:
        import tkinter as tk
    except Exception as e:  # noqa: BLE001
        report["window"] = {"created": False, "reason": "tkinter_unavailable",
                            "error_class": type(e).__name__, "error_text": _error_text(e)}
        return report
    try:
        root = tk.Tk()
    except Exception as e:  # noqa: BLE001
        # 无显示会话：这是环境事实，如实申报，不降级成"假装开窗成功"
        report["window"] = {"created": False, "reason": "no_display_available",
                            "error_class": type(e).__name__, "error_text": _error_text(e)}
        return report
    clicks: List[str] = []

    def _fire(action_id: str) -> None:
        res = perform_action(snap["base_url"], action_id, timeout=timeout)
        clicks.append(action_id)
        if action_hook:
            action_hook(action_id)
        report.setdefault("actions", []).append(res)

    try:
        root.title("造境 AI · 本地 Hub")
        root.geometry("720x420+80+80")
        head = tk.Label(root, text="造境 AI 设计智能体 · 桌面 Hub（只读快照）",
                        font=("Microsoft YaHei UI", 12, "bold"))
        head.pack(anchor="w", padx=14, pady=(12, 4))
        labels: List[tk.Label] = []
        for text in rows:
            lab = tk.Label(root, text=text, justify="left", anchor="w")
            lab.pack(fill="x", padx=14, pady=1)
            labels.append(lab)
        status = tk.Label(root, text="未触发动作", fg="#666666")
        status.pack(anchor="w", padx=14, pady=(8, 2))
        frame = tk.Frame(root)
        frame.pack(fill="x", padx=14, pady=(2, 12))
        buttons: Dict[str, tk.Button] = {}
        for aid, act in ACTIONS.items():
            def _cmd(aid: str = aid) -> None:  # 显式点击才走 POST/重读
                _fire(aid)
                status.config(text="已执行 %s @ %s" % (aid, time.strftime("%H:%M:%S")))
            b = tk.Button(frame, text=act["label"], command=_cmd)
            b.pack(side="left", padx=(0, 8))
            buttons[aid] = b
        for _ in range(30):
            root.update()
            if root.winfo_ismapped() and root.winfo_width() > 1:
                break
            time.sleep(0.05)
        report["window"] = {
            "created": True,
            "hwnd": root.winfo_id(),
            "mapped": root.winfo_ismapped(),
            "geometry_live": "%dx%d" % (root.winfo_width(), root.winfo_height()),
            "geometry_asked": "720x420",
            "title": root.title(),
            "label_texts": [lab.cget("text") for lab in labels],
            "buttons": sorted(buttons),
            # 活引用：驱动方 invoke 之后仍能从这里读到真实点击序列与状态行
            "clicks_so_far": clicks,
            "status_text": status.cget("text"),
        }
        report["_root"] = root
        report["_buttons"] = buttons
        report["_status"] = status
        if enter_mainloop:
            try:
                root.mainloop()
            finally:
                report["window"]["mainloop_exited"] = True
    except Exception as e:  # noqa: BLE001
        report["window_build_error"] = {"error_class": type(e).__name__,
                                        "error_text": _error_text(e)}
    return report


def close_hub(report: Dict[str, Any]) -> None:
    root = report.get("_root")
    if root is not None:
        try:
            root.destroy()
        except Exception:  # noqa: BLE001
            pass
        report["_root"] = None


def hub_selftest(base_url: Optional[str] = None, timeout: float = DEFAULT_TIMEOUT,
                 with_window: bool = False) -> Dict[str, Any]:
    """CLI 自检：数据层必跑（纯 stdlib、无显示依赖），窗口层按需真建真销毁。"""
    t0 = time.perf_counter()
    snap = hub_snapshot(base_url, timeout=timeout)
    out: Dict[str, Any] = {
        "schema": SCHEMA,
        "base_url": snap["base_url"],
        "base_url_source": snap["base_url_source"],
        "all_sources_live": snap["all_sources_live"],
        "degraded": snap["degraded"]["failed_sources"],
        "rows": render_rows(snap),
        "window": {"requested": with_window},
        "elapsed_ms": round((time.perf_counter() - t0) * 1000),
        "fabricated": False,
    }
    if with_window:
        rep = open_hub(snapshot=snap, enter_mainloop=False, timeout=timeout)
        out["window"].update({k: v for k, v in rep["window"].items()})
        out["window"]["rows_equal_render"] = (
            rep["window"].get("label_texts") == rep["rows"])
        close_hub(rep)
    return out


def port_is_open(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:  # noqa: BLE001
        return False


# ---------------------------------------------------------------------------
# L1 自测（p33_l1_sweep 第 27 项）：零建窗、零外部依赖，全部判据打在**进程内真 HTTP
# 服务**上。刻意不用桩替换 urllib——本引擎的价值全在"真 socket + 真响应"这条传输链上，
# 换成桩就只剩"常量等于常量"（Phase 24 起反复踩过的假绿形态）。窗口本体的活体判据
# （Label 文本 == 派生行、开窗 POST 增量 0、显式点击增量恰 1）在 L2/L4，L1 绝不弹窗。
# ---------------------------------------------------------------------------

# 夹具与评测台相互独立：这里的版本号/数据根/引擎名/日期都是自测专用字面量
_ST_VERSION = "1.0.0-l1selftest"
_ST_DATA_ROOT = "S:/l1selftest/data"
_ST_HEALTH = {
    "status": "healthy",
    "version": _ST_VERSION,
    "mode": "portable",
    "uptime_seconds": 12.5,
    "data_root": _ST_DATA_ROOT,
    "engines": {"alpha": "ready", "beta": "ready", "gamma": "failed"},
}
# 注意 engines 里 2 个 ready / 共 3 项，且与 capabilities.engines_loaded 同源同集合
_ST_CAPS = {
    "summary": {"total": 39, "supported": 34, "needs_adobe_host": 3,
                "prototype_not_shipped": 0, "not_shipped": 2},
    "engines_loaded": ["alpha", "beta", "gamma"],
    "capabilities": [{"id": "c_ok", "status": "supported"},
                     {"id": "c_inst", "status": "not_shipped"},
                     {"id": "c_psd", "status": "needs_adobe_host"}],
    "registry": {"capabilities": [
        {"id": "c_ok", "verified": True, "evidence_kind": "golden_cases"},
        {"id": "c_inst", "verified": False, "evidence_kind": "honest_boundary"},
        {"id": "c_psd", "verified": False, "evidence_kind": "honest_boundary"},
    ]},
}
# 报告**故意**把最新的放在首位：若实现按末位/按插入序取"最新"，这里必红
_ST_REPORTS = {"reports": [
    {"report_id": "eval-new", "executed_cases": 9, "pass_rate": 1.0, "honest": True,
     "finished_at": "2026-02-02T00:00:00"},
    {"report_id": "eval-old", "executed_cases": 5, "pass_rate": 0.5, "honest": True,
     "finished_at": "2026-01-01T00:00:00"},
]}
_ST_LATEST_ROW = "最新评测 eval-new：9 用例执行 · pass_rate 1.0 · honest=True"
_ST_CAPS_ROWS = [
    "能力真值表 39 项：supported 34 / needs_adobe_host 3 / prototype_not_shipped 0 / "
    "not_shipped 2",
    "未 supported 的能力：c_inst、c_psd",
    "Registry 已验证 1 项（honest_boundary 2 项）",
]


def _selftest() -> int:
    import http.server
    import importlib.util
    import json as _json
    import threading

    routes: Dict[str, Tuple[int, str, bytes]] = {
        "/api/v1/health": (200, "application/json",
                           _json.dumps(_ST_HEALTH).encode("utf-8")),
        "/api/v1/capabilities": (200, "application/json",
                                 _json.dumps(_ST_CAPS).encode("utf-8")),
        "/api/v1/eval/reports": (200, "application/json",
                                 _json.dumps(_ST_REPORTS).encode("utf-8")),
        # 形状不合：HTTP 200 且是合法 JSON，但缺 version/status —— 正是 P50 探针抓出的
        # "vNone 占位行"触发条件
        "/api/v1/health/badshape": (200, "application/json",
                                    _json.dumps({"uptime_seconds": 3}).encode("utf-8")),
        "/api/v1/health/html": (200, "text/html",
                                b"<!doctype html><title>other app</title>"),
        "/api/v1/health/badjson": (200, "application/json", b'{"status": '),
        "/api/v1/eval/reports/empty": (200, "application/json",
                                       _json.dumps({"reports": []}).encode("utf-8")),
        "/api/v1/capabilities/mismatch": (
            200, "application/json",
            _json.dumps(dict(_ST_CAPS, engines_loaded=["alpha", "beta"])).encode("utf-8")),
    }
    seen: List[Tuple[str, str]] = []
    posts: List[str] = []

    class _Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):  # 静音，避免污染 L1 输出
            pass

        def _send(self, code: int, ctype: str, body: bytes) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            seen.append(("GET", self.path))
            rec = routes.get(self.path)
            if rec is None:
                self._send(404, "application/json",
                           b'{"detail":"Not Found"}')
                return
            self._send(rec[0], rec[1], rec[2])

        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""
            seen.append(("POST", self.path))
            posts.append("%s %s %s" % (self.path, body.decode("utf-8", "replace"),
                                       (self.headers.get("Content-Type") or "")))
            self._send(200, "application/json", b'{"accepted":true}')

    srv = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    port = srv.server_address[1]
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    base = "http://127.0.0.1:%d" % port
    groups = 0
    try:
        # 1) 传输忠实：三条只读 GET、顺序即 READ_SOURCES、零 POST
        snap = hub_snapshot(base)
        assert seen == [("GET", p) for _, p in READ_SOURCES], ascii(seen)
        assert posts == [], ascii(posts)
        assert snap["read_only"] is True and snap["actions_performed"] == []
        assert snap["fabricated"] is False and snap["schema"] == SCHEMA
        assert snap["base_url"] == base and snap["base_url_source"] == "cli"
        assert snap["all_sources_live"] is True
        assert snap["degraded"] == {"any": False, "failed_sources": [], "reasons": {}}
        groups += 1

        # 2) 逐字段搬运真值（每个数字都能在夹具里找到出处）
        svc = snap["service"]
        assert svc["health_verdict"] == "zaojing_payload" and svc["shape_valid"] is True
        assert (svc["version"], svc["status"], svc["mode"]) == (_ST_VERSION, "healthy",
                                                                "portable")
        assert svc["uptime_seconds"] == 12.5 and svc["data_root"] == _ST_DATA_ROOT
        eng = snap["engines"]
        assert eng["health_reported"] == ["alpha", "beta", "gamma"]
        assert eng["capabilities_reported"] == ["alpha", "beta", "gamma"]
        assert eng["health_ready_count"] == 2 and eng["agree"] is True
        cap = snap["capabilities"]
        assert cap["summary"] == _ST_CAPS["summary"]
        assert cap["items_seen"] == 3
        assert cap["not_supported_ids"] == ["c_inst", "c_psd"]
        assert cap["registry_verified_count"] == 1
        assert cap["registry_honest_boundary_ids"] == ["c_inst", "c_psd"]
        assert snap["unavailable_fields"] == []
        groups += 1

        # 3) 最新评测 = max(finished_at)，不是末位、不是插入序
        ev = snap["evaluation"]
        assert ev["reports_on_disk"] == 2 and ev["picked_by"] == "max(finished_at)"
        assert ev["latest"]["report_id"] == "eval-new", ascii(ev["latest"])
        assert _ST_REPORTS["reports"][-1]["report_id"] == "eval-old"
        groups += 1

        # 4) 派生行逐字等价（窗口 Label 的真值来源；行序、行数一并钉死）
        rows = render_rows(snap)
        assert rows[0] == "Hub 目标 %s（来源 cli）" % base, ascii(rows[0])
        assert rows[1:] == [
            "服务 造境 AI v%s · healthy · 运行 12.5s" % _ST_VERSION,
            "数据根 %s" % _ST_DATA_ROOT,
            "引擎 2/3 ready（/health 与 /capabilities 同源=True）",
        ] + _ST_CAPS_ROWS + [_ST_LATEST_ROW], ascii(rows)
        assert len(rows) == 8 and rows[-1] == _ST_LATEST_ROW
        groups += 1

        # 5) 两次快照派生行全等（呈现层无隐藏状态、不吃时钟）
        assert render_rows(hub_snapshot(base)) == rows
        groups += 1

        # 6) 跨源不一致必须显形，不许静默取其一
        bad = hub_snapshot(base, sources=(("health", "/api/v1/health"),
                                          ("capabilities",
                                           "/api/v1/capabilities/mismatch"),
                                          ("eval_reports", "/api/v1/eval/reports")))
        assert bad["engines"]["agree"] is False
        assert bad["engines"]["capabilities_reported"] == ["alpha", "beta"]
        assert "引擎 2/3 ready（/health 与 /capabilities 同源=False）" in render_rows(bad)
        groups += 1

        # 7) 三形制诚实降级 + 形状门：任何一形都不许把 None 搬进窗口
        degraded_forms = [
            ("/api/v1/health/badshape", "zaojing_payload",
             "服务回了 JSON 但形状不合造境 health 契约（缺 status/version），"
             "身份字段一律留空"),
            ("/api/v1/health/html", "not_zaojing_service",
             "端口在，但不是造境服务（not_zaojing_service）"),
            ("/api/v1/health/badjson", "malformed_json",
             "端口在，但不是造境服务（malformed_json）"),
            ("/api/v1/ghost", "not_zaojing_service",
             "端口在，但不是造境服务（not_zaojing_service）"),
        ]
        for path, verdict, row2 in degraded_forms:
            d = hub_snapshot(base, sources=(("health", path),
                                            ("capabilities", "/api/v1/capabilities"),
                                            ("eval_reports", "/api/v1/eval/reports")))
            s = d["service"]
            assert s["health_verdict"] == verdict, (path, ascii(s))
            assert s["reachable"] is True and s["version"] is None
            assert s["data_root"] is None and s["uptime_seconds"] is None
            if path == "/api/v1/health/badshape":
                # 唯一"回了 JSON 但契约不合"的形制：形状门单独自报 False
                assert s["shape_valid"] is False, (path, ascii(s))
            else:
                assert s["shape_valid"] is None, (path, ascii(s))
            assert d["degraded"]["failed_sources"] == ["health"], (path, ascii(d["degraded"]))
            dr = render_rows(d)
            assert dr[1] == row2, (path, ascii(dr))
            assert dr[-1] == ("降级中：health —— 以上缺失字段一律留空，未填任何假数据"
                              ), (path, ascii(dr))
            assert "None" not in "".join(dr), (path, ascii(dr))  # vNone 回归哨兵
            assert d["unavailable_fields"][:5] == ["service.version", "service.status",
                                                   "service.mode", "engines",
                                                   "cross_source_engines_agree"]
        groups += 1

        # 8) 连不上（真·无人监听端口）：verdict=connection_failed，三源全降级
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            dead_port = probe.getsockname()[1]
        assert port_is_open("127.0.0.1", dead_port) is False
        assert port_is_open("127.0.0.1", port) is True
        off = hub_snapshot("http://127.0.0.1:%d" % dead_port, timeout=1.0)
        assert off["service"]["reachable"] is False
        assert off["service"]["health_verdict"] == "connection_failed"
        assert off["degraded"]["failed_sources"] == ["health", "capabilities",
                                                     "eval_reports"]
        orows = render_rows(off)
        assert orows[1].startswith("连不上服务："), ascii(orows[1])
        assert orows[-1].startswith("降级中：health、capabilities、eval_reports"), ascii(orows[-1])
        assert "None" not in "".join(orows), ascii(orows)
        groups += 1

        # 9) 数据根下 0 份报告：如实说"尚未跑过"，不编分数
        zero = hub_snapshot(base, sources=(("health", "/api/v1/health"),
                                           ("capabilities", "/api/v1/capabilities"),
                                           ("eval_reports",
                                            "/api/v1/eval/reports/empty")))
        zr = render_rows(zero)
        assert "评测报告：数据根下 0 份（尚未跑过评测，不编分数）" in zr, ascii(zr)
        assert zero["evaluation"]["latest"] is None
        assert zero["evaluation"]["picked_by"] is None
        assert zero["evaluation"]["reports_on_disk"] == 0
        groups += 1

        # 10) POST 单一通道：只有 perform_action 能写，未知动作/非法基址直接 HubError
        before = len(posts)
        ref = perform_action(base, "refresh")
        assert ref["mutating"] is False and len(ref["results"]) == 3
        assert len(posts) == before, "refresh 越权发起了 POST"
        run = perform_action(base, "eval_run")
        assert run["mutating"] is True and len(posts) == before + 1, ascii(posts)
        assert posts[-1].startswith("/api/v1/eval/run {}"), ascii(posts[-1])
        assert run["results"][0]["status"] == 200
        assert run["results"][0]["method"] == "POST"
        try:
            perform_action(base, "no_such_action")
        except HubError as e:
            assert "unknown_action" in str(e), ascii(str(e))
        else:
            raise AssertionError("未知动作应抛 HubError")
        for bad_base in ("not a url", "127.0.0.1:8000"):
            try:
                hub_snapshot(bad_base)
            except HubError:
                continue
            raise AssertionError("非法基址应抛 HubError: %r" % bad_base)
        groups += 1

        # 11) 基址来源三态可追溯（cli / env / default），绝不静默猜测
        _saved_port = os.environ.pop("ZAOJING_PORT", None)
        _saved_headless = os.environ.pop("ZAOJING_HUB_HEADLESS", None)
        try:
            assert resolve_base(base) == (base, "cli")
            assert resolve_base(base + "/") == (base, "cli")  # 尾斜杠归一，不双斜杠
            os.environ["ZAOJING_PORT"] = "8123"
            assert resolve_base() == ("http://127.0.0.1:8123", "env:ZAOJING_PORT")
            del os.environ["ZAOJING_PORT"]
            assert resolve_base() == (DEFAULT_BASE, "default")
            groups += 1
            # 12) 无显示会话下开窗必走 headless 分支，且窗口行数 == 派生行数
            os.environ["ZAOJING_HUB_HEADLESS"] = "1"
            rep = open_hub(snapshot=snap, enter_mainloop=False)
            assert rep["window"] == {"created": False, "reason": "headless_requested",
                                     "rows_would_render": len(rows)}, ascii(rep["window"])
            assert rep["rows"] == rows and rep["fabricated"] is False
            assert rep["base_url"] == base
            assert rep["click_log"] == [] and "_root" not in rep  # 确未建窗
            groups += 1
            # 13) CLI 自检入口（不建窗）与直接派生行一致
            st = hub_selftest(base, timeout=5.0, with_window=False)
            assert st["rows"] == rows and st["all_sources_live"] is True
            assert st["degraded"] == [] and st["window"] == {"requested": False}
            assert st["base_url_source"] == "cli"
            groups += 1
        finally:
            os.environ.pop("ZAOJING_HUB_HEADLESS", None)
            if _saved_headless is not None:
                os.environ["ZAOJING_HUB_HEADLESS"] = _saved_headless
            if _saved_port is not None:
                os.environ["ZAOJING_PORT"] = _saved_port
        # 14) 契约面常量 + 窗口层导入目标存在（只查规格，绝不建窗——建窗判据在 L2/L4）
        assert importlib.util.find_spec("tkinter") is not None
        assert [p for _, p in READ_SOURCES] == ["/api/v1/health",
                                                "/api/v1/capabilities",
                                                "/api/v1/eval/reports"]
        assert sorted(ACTIONS) == ["eval_run", "refresh"]
        assert ACTIONS["refresh"]["mutating"] is False
        assert ACTIONS["refresh"]["paths"] == [p for _, p in READ_SOURCES]
        assert ACTIONS["eval_run"]["mutating"] is True
        assert ACTIONS["eval_run"]["method"] == "POST"
        assert len(set(x["mutating"] for x in ACTIONS.values())) == 2
        groups += 1
    finally:
        srv.shutdown()
        srv.server_close()
        th.join(timeout=5)
    print("[OK] desktop_hub_engine selftest passed: %d groups, "
          "0 window created, %d real GETs / %d POSTs (explicit only)"
          % (groups, len(seen) - len(posts), len(posts)))
    return 0


if __name__ == "__main__":
    raise SystemExit(_selftest())
