#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
造境 AI 设计智能体 — Adobe UXP 桥接引擎（第 19 引擎）
============================================================================
任务书权威依据（唯一来源，非臆造）：
  §4  前端技术架构：插件用 UXP + TypeScript + React，只做 UI / Adobe 文档交互 /
      Tool 调用；不堆数据库 / LLM / 大文件 / 长任务。
  §8  插件连接协议：Plugin → localhost handshake → Agent Service → 认证 →
      Capability negotiation → Connected。"不能默认相信本地端口"，建议
      随机 Session Token + 本地 Origin 校验 + 端口动态发现。
  §9  统一请求格式：{request_id, project_id, document_id, intent, context,
      permissions, options}；统一响应：{request_id, status, task_id/result}。
  §10 WebSocket 事件协议（task.*）：由 agent_service 的 _EVENT_HUB 另行承载，
      本引擎的命令通道与之解耦。
  §23 Tool Registry 命名规范：ps.document.get / ps.layer.* / ps.image.* /
      ai.document.get / ai.artboard.list / ai.object.* / ai.path.create ...
  §24 Adobe Adapter（独立包）：PhotoshopAdapter / IllustratorAdapter →
      CapabilityDetector / ActionExecutor / DocumentReader / ObjectMapper /
      ErrorMapper / VersionAdapter。
  §25 Adobe Capability Map（插件启动上报）：{document_read, layer_write,
      selection, batch_operation, ...}；Agent 据当前能力自动决定工具。
  §26 版本适配：建立 Compatibility Matrix（Host / Version / Capability /
      Known Issue / Workaround），"禁止" if(version==="xx") 式硬编码门控。
  §74 插件崩溃隔离：Adobe 插件 / Agent 服务 / Asset 服务相互隔离，一方挂掉
      其他仍可运行。

--------------------------------------------------------------------------
【诚实边界（红线，§2/§97）】
  本引擎交付并端到端验证的是"桥接机制"——握手 / 随机 Session Token 认证 /
  Origin 校验 / 端口动态发现 / §9 命令下发 / 真实结果对账 / 超时 / 宿主掉线
  诚实降级。它把命令"搬运"给已连接的宿主并"如实对账"宿主回传的真实结果，
  绝不在服务端自行执行或伪造任何 Adobe 文档变更。
  真正的 ps.* / ai.* 文档写入必须运行在真实带许可证的 Photoshop / Illustrator
  的 UXP 运行时里（宿主侧面板完成）。因此：
    - adobe_uxp_bridge      = supported（管道为真，可验证）
    - ps_document_write     = needs_adobe_host（真实文档变更仍须宿主）
    - ai_document_write     = needs_adobe_host
    - cdr_document_read     = supported（同一管道 + CorelDRAW COM 宿主脚本 attach-only；
                              真实文档数据仍须本机已运行的 CorelDRAW，未运行则无宿主降级）
  未连接真实宿主时命令下发一律诚实返回 no_host_connected（fabricated=false），
  绝不凭空产出"图层已创建""对象已移动"之类假回执。
--------------------------------------------------------------------------
传输选型：纯 stdlib HTTP 长轮询（宿主作为客户端发起所有请求，服务端无需反向
连进沙箱宿主），与 Phase 10 Provider 客户端同构，不引入任何第三方 Python 包，
故 PyInstaller 无需新增 hidden-import。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("zaojing.adobe_bridge")

# ---------------------------------------------------------------------------
# §23 Tool Registry：可经桥接下发的工具 → (适配器 host, intent, 所需能力)
# ---------------------------------------------------------------------------
# 所需能力对应 §25 Capability Map 的键；宿主未上报该能力则拒绝下发（capability_not_negotiated）。
BRIDGEABLE_TOOLS: Dict[str, Dict[str, Any]] = {
    # —— Photoshop ——
    "ps.document.get":   {"host": "photoshop", "intent": "document.get",   "capability": "document_read",  "risk": "L0"},
    "ps.layer.list":     {"host": "photoshop", "intent": "layer.list",     "capability": "document_read",  "risk": "L0"},
    "ps.layer.create":   {"host": "photoshop", "intent": "layer.create",   "capability": "layer_write",    "risk": "L1"},
    "ps.layer.rename":   {"host": "photoshop", "intent": "layer.rename",   "capability": "layer_write",    "risk": "L1"},
    "ps.layer.move":     {"host": "photoshop", "intent": "layer.move",     "capability": "layer_write",    "risk": "L1"},
    "ps.layer.delete":   {"host": "photoshop", "intent": "layer.delete",   "capability": "layer_write",    "risk": "L3"},
    "ps.image.import":   {"host": "photoshop", "intent": "image.import",   "capability": "image_place",    "risk": "L2"},
    "ps.image.replace":  {"host": "photoshop", "intent": "image.replace",  "capability": "image_place",    "risk": "L3"},
    "ps.image.resize":   {"host": "photoshop", "intent": "image.resize",   "capability": "layer_write",    "risk": "L1"},
    # —— Illustrator ——
    "ai.document.get":   {"host": "illustrator", "intent": "document.get",   "capability": "document_read", "risk": "L0"},
    "ai.artboard.list":  {"host": "illustrator", "intent": "artboard.list",  "capability": "document_read", "risk": "L0"},
    "ai.object.create":  {"host": "illustrator", "intent": "object.create",  "capability": "object_write",  "risk": "L2"},
    "ai.object.move":    {"host": "illustrator", "intent": "object.move",    "capability": "object_write",  "risk": "L1"},
    "ai.path.create":    {"host": "illustrator", "intent": "path.create",    "capability": "object_write",  "risk": "L2"},
    "ai.text.create":    {"host": "illustrator", "intent": "text.create",    "capability": "object_write",  "risk": "L2"},
    "ai.vectorize":      {"host": "illustrator", "intent": "vectorize",      "capability": "vectorize",     "risk": "L2"},
    # —— CorelDRAW（Phase 77 只读三工具 + Phase 78 最小写集 create/move）——
    # 能力键 document_read 与 ps/ai 的读取工具同键；写工具单列 object_write，风险 L1，
    # 服务端不 import 任何 COM 库，真正的读取/写入发生在宿主端 outputs/cdr_host.py。
    "cdr.document.get":  {"host": "coreldraw", "intent": "document.get", "capability": "document_read", "risk": "L0"},
    "cdr.page.list":     {"host": "coreldraw", "intent": "page.list",    "capability": "document_read", "risk": "L0"},
    "cdr.object.list":   {"host": "coreldraw", "intent": "object.list",  "capability": "document_read", "risk": "L0"},
    # Phase 78：最小写集只有 create/move；delete/rename/save/export 一律不注册（unknown_tool）。
    # 写操作必须 args.confirm is True（服务端确认门 + 宿主端二次校验），永不 Save。
    "cdr.object.create": {"host": "coreldraw", "intent": "object.create", "capability": "object_write", "risk": "L1", "write": True},
    "cdr.object.move":   {"host": "coreldraw", "intent": "object.move",   "capability": "object_write", "risk": "L1", "write": True},
}


class BridgeError(Exception):
    """桥接层诚实错误：kind 为稳定契约码，绝不携带伪造结果。"""

    def __init__(self, kind: str, detail: str = ""):
        super().__init__(detail or kind)
        self.kind = kind
        self.detail = detail


# ---------------------------------------------------------------------------
# §26 版本适配：Compatibility Matrix —— 以能力为键门控，禁止版本号硬编码
# ---------------------------------------------------------------------------
class CompatibilityMatrix:
    """
    任务书 §26 明确"禁止 if(photoshopVersion==='xx')"，要求建立
    Host / Version / Capability / Known Issue / Workaround 矩阵。

    本实现以 **能力（capability）** 而非版本字符串作为执行门控：
    某工具能否下发，取决于宿主 §25 上报的能力集合是否覆盖它；版本仅作为
    诊断信息与已知问题（known_issue）提示，绝不参与 if version==... 分支。
    """

    # 静态知识表：host -> 已知问题/规避提示（纯信息，不作门控分支条件）
    _KNOWN = {
        "photoshop": [
            {"capability": "image_place", "known_issue": "部分版本 place 需先激活文档",
             "workaround": "宿主侧面板在 executeAsModal 前确保目标文档 active"},
        ],
        "illustrator": [
            {"capability": "vectorize", "known_issue": "图像描摹参数随版本差异较大",
             "workaround": "面板侧以能力探测而非版本判断选择默认参数"},
        ],
        "coreldraw": [
            {"capability": "document_read", "known_issue": "CorelDRAW 无 UXP 运行时；宿主脚本只能 attach 到已运行实例",
             "workaround": "attach-only：GetActiveObject 不 Dispatch、不启动 GUI；未运行则如实 coreldraw_not_running"},
            {"capability": "object_write", "known_issue": "最小写集只有 create/move，须显式 confirm；服务端与宿主双层校验",
             "workaround": "宿主尽力放进单个撤销组；失败如实 undo_group=unsupported；任何情况下永不 Save"},
        ],
    }

    @classmethod
    def known_issues(cls, host: str) -> List[dict]:
        return list(cls._KNOWN.get(host, []))

    @classmethod
    def is_executable(cls, host: str, tool: str, negotiated_caps: Dict[str, bool]) -> bool:
        """唯一门控依据 = 协商到的能力集合；不读取 version。"""
        meta = BRIDGEABLE_TOOLS.get(tool)
        if not meta or meta["host"] != host:
            return False
        return bool(negotiated_caps.get(meta["capability"]))


# ---------------------------------------------------------------------------
# §24 Adobe Adapter 组件（服务端侧：编排协议 + 对账真实回执，绝不执行文档操作）
# ---------------------------------------------------------------------------
class CapabilityDetector:
    """检测宿主 §25 上报的能力集合是否覆盖某工具。"""

    @staticmethod
    def covers(capabilities: Dict[str, bool], tool: str) -> bool:
        meta = BRIDGEABLE_TOOLS.get(tool)
        if not meta:
            return False
        return bool(capabilities.get(meta["capability"]))


class DocumentReader:
    """把宿主回传的真实文档快照（§9 result.payload）规范化为对账结构（§11 Design IR 片段）。"""

    @staticmethod
    def reconcile(result: dict) -> dict:
        payload = result.get("payload") if isinstance(result, dict) else None
        payload = payload if isinstance(payload, dict) else {}
        # 宿主显式上报的 object_count 优先（写侧回读的是"整篇文档对象数"，不是本次返回条数）；
        # 只在宿主没报时退回本次 objects 长度，绝不反推或补造。
        host_count = payload.get("object_count")
        if isinstance(host_count, int) and not isinstance(host_count, bool):
            count = host_count
        else:
            count = len(payload.get("objects", []) or [])
        return {
            "reconciled": True,
            "document_id": payload.get("document_id"),
            "object_count": count,
            # Phase 78：写侧回读必须如实透传宿主自报的 verified / undo_group / 数据来源，
            # 服务端不参与判断（宿主没报 verified=true 就是 false，绝不补造）。
            "verified": bool(payload.get("verified")),
            "undo_group": payload.get("undo_group"),
            "result_source": payload.get("source"),
            "source": "host",  # 结果来自宿主真实执行，非服务端合成
        }


class ObjectMapper:
    """把 §23 工具名 + 入参映射为 §9 统一请求帧。"""

    @staticmethod
    def build_request(tool: str, args: dict, project_id: str, document_id: str,
                      permissions: Optional[dict] = None) -> dict:
        meta = BRIDGEABLE_TOOLS[tool]
        return {
            "request_id": "req_" + secrets.token_hex(8),
            "project_id": project_id,
            "document_id": document_id,
            "intent": meta["intent"],
            "tool": tool,
            "context": {"args": args or {}},
            "permissions": permissions or {},
            "options": {"risk": meta["risk"]},
            "issued_at": time.time(),
        }


class ErrorMapper:
    """把宿主回传的错误映射为稳定契约码；绝不吞成"成功"。"""

    _MAP = {
        "protected_object": "protected_object_conflict",
        "target_ambiguous": "needs_clarification",
        "host_error": "host_execution_failed",
        "not_supported": "capability_unsupported_at_host",
        # Phase 78：写侧拒绝码保持原义，绝不吞成泛化 host_error
        "invalid_args": "invalid_args",
        "confirmation_required": "confirmation_required",
        "object_not_found": "object_not_found",
        "com_api_unavailable": "com_api_unavailable",
    }

    @classmethod
    def map(cls, host_error: str) -> str:
        return cls._MAP.get(host_error, "host_execution_failed")


class ActionExecutor:
    """
    服务端执行器：仅负责"把已协商可执行的命令入队给宿主并等待其真实回执"。
    真正的文档操作发生在宿主侧（本进程之外），本类不产生任何文档变更。
    """

    def __init__(self, engine: "AdobeBridgeEngine"):
        self._engine = engine

    async def run(self, tool: str, args: dict, project_id: str, document_id: str,
                  protected_objects: Optional[List[str]] = None,
                  timeout: float = 30.0) -> dict:
        return await self._engine.dispatch(
            tool=tool, args=args, project_id=project_id, document_id=document_id,
            protected_objects=protected_objects, timeout=timeout,
        )


class VersionAdapter:
    """版本适配入口（能力门控 + 已知问题提示），拒绝版本字符串分支。"""

    @staticmethod
    def advisory(host: str, version: str) -> List[dict]:
        # version 仅用于展示，不参与任何分支判断
        return CompatibilityMatrix.known_issues(host)


class _Adapter:
    def __init__(self, engine: "AdobeBridgeEngine"):
        self.engine = engine
        self.capability_detector = CapabilityDetector()
        self.object_mapper = ObjectMapper()
        self.document_reader = DocumentReader()
        self.error_mapper = ErrorMapper()
        self.version_adapter = VersionAdapter()
        self.action_executor = ActionExecutor(engine)


class PhotoshopAdapter(_Adapter):
    host = "photoshop"


class IllustratorAdapter(_Adapter):
    host = "illustrator"


# ---------------------------------------------------------------------------
# 桥接引擎
# ---------------------------------------------------------------------------
class AdobeBridgeEngine:
    """
    宿主侧 UXP 面板作为 **HTTP 客户端**：
      1) 读 discovery 文件（端口 + 随机 Session Token）→ 端口动态发现
      2) POST /uxp/handshake  {host, version, capabilities, token}  → 认证 + 能力协商
      3) 循环 POST /uxp/poll   {host_id, token}                     → 取待执行 §9 命令（长轮询）
      4) 真执行后 POST /uxp/result {request_id, token, status, payload|error} → 回传真实结果
    服务端 /uxp/dispatch：把命令入队对应宿主并 await 其真实回执（超时/掉线诚实降级）。
    """

    def __init__(self, state_dir: Optional[Path] = None,
                 loopback_only: bool = True,
                 host_stale_seconds: float = 20.0):
        self.state_dir = Path(state_dir) if state_dir else Path(os.environ.get(
            "ZAOJING_DATA", os.path.join(os.path.expanduser("~"), ".zaojing", "uxp")))
        self.state_dir.mkdir(parents=True, exist_ok=True)
        # Phase 25：discovery 文件与 state_dir 平级（历史上曾在 state_dir 下再套一层
        # "uxp/"，因调用方已传 DATA_ROOT/"uxp"，形成 uxp/uxp/bridge-endpoint.json 冗余嵌套）。
        # 读取保留旧位置回退以兼容升级瞬间仍指向旧文件的宿主；写入永远只落新位置。
        self.discovery_path = self.state_dir / "bridge-endpoint.json"
        self.legacy_discovery_path = self.state_dir / "uxp" / "bridge-endpoint.json"
        self.session_token = secrets.token_urlsafe(32)
        self.loopback_only = loopback_only
        self.host_stale_seconds = float(host_stale_seconds)
        self._lock = threading.Lock()
        # host_id -> {host, version, capabilities, last_seen, queue(asyncio.Queue), loop}
        self._hosts: Dict[str, dict] = {}
        # request_id -> {"future": asyncio.Future, "host_id": str, "tool": str}
        self._pending: Dict[str, dict] = {}
        self._advisory_issued = 0

    # ---------- 端口动态发现（§8）：把本地服务信息落 discovery 文件供面板读取 ----------
    def publish_discovery(self, port: int, scheme: str = "http") -> dict:
        info = {
            "scheme": scheme, "host": "127.0.0.1", "port": int(port),
            "handshake_path": "/api/v1/uxp/handshake",
            "poll_path": "/api/v1/uxp/poll", "result_path": "/api/v1/uxp/result",
            "session_token": self.session_token,
            "issued_at": time.time(),
            "note": "UXP 面板读取本文件发现端口与 Session Token（§8 端口动态发现 + 随机 Token）。请勿置于共享盘。",
        }
        tmp = self.discovery_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.discovery_path)
        try:  # Phase 25 迁移卫生：旧嵌套位若存有此前会话的明文 token，发布成功后一并清除
            if self.legacy_discovery_path.exists():
                self.legacy_discovery_path.unlink()
        except OSError:  # pragma: no cover
            logger.warning("legacy discovery cleanup failed: %s", self.legacy_discovery_path)
        return info

    def read_discovery(self) -> Optional[dict]:
        try:
            for p in (self.discovery_path, self.legacy_discovery_path):
                if p.exists():
                    return json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:  # pragma: no cover
            logger.warning("read discovery failed: %s", e)
        return None

    @staticmethod
    def _mask(token: str) -> str:
        if not token:
            return ""
        if len(token) <= 8:
            return "*" * len(token)
        return token[:3] + "*" * (len(token) - 7) + token[-4:]

    def _check_loopback(self, client_ip: Optional[str]) -> None:
        if self.loopback_only:
            loop = {"127.0.0.1", "::1", "localhost"}
            if (client_ip or "") not in loop:
                raise BridgeError("origin_rejected",
                                  f"§8：非本地来源 {client_ip!r} 被拒绝，桥接只信任 loopback 且需随机 Session Token")

    def _check_token(self, token: Optional[str]) -> None:
        if not token or not secrets.compare_digest(token, self.session_token):
            raise BridgeError("auth_failed", "§8：缺少或错误的 Session Token，拒绝握手/取命令")

    # ---------- §8 握手 + §25 能力协商 ----------
    async def handshake(self, host: str, version: str, capabilities: Dict[str, bool],
                        token: Optional[str], client_ip: Optional[str]) -> dict:
        self._check_loopback(client_ip)
        self._check_token(token)
        host = (host or "").strip().lower()
        if host not in ("photoshop", "illustrator", "coreldraw"):
            raise BridgeError("unknown_host", f"§24：未知宿主 {host!r}（仅 photoshop / illustrator / coreldraw）")
        if not isinstance(capabilities, dict) or not capabilities:
            # 允许空能力集合登记连接，但任何工具下发都会因 capability_not_negotiated 被拒
            capabilities = {}
        loop = asyncio.get_running_loop()
        host_id = f"{host}-{secrets.token_hex(4)}"
        with self._lock:
            self._hosts[host_id] = {
                "host": host, "version": version, "capabilities": dict(capabilities),
                "last_seen": time.time(), "queue": asyncio.Queue(), "loop": loop,
            }
        self._advisory_issued += 1
        logger.info("UXP 宿主握手成功 host=%s id=%s caps=%d", host, host_id, len(capabilities))
        # §26：返回已知问题/规避建议（信息，不门控）；version 仅展示
        return {
            "status": "connected", "host_id": host_id, "session": self._mask(self.session_token),
            "capabilities": capabilities,
            "advisory": VersionAdapter.advisory(host, version),
            "gating": "capability-based (§26：按能力门控，禁止版本号 if 分支)",
            "fabricated": False,
        }

    def _prune_stale_locked(self) -> List[str]:
        now = time.time()
        dead = [hid for hid, h in self._hosts.items()
                if now - h["last_seen"] > self.host_stale_seconds]
        for hid in dead:
            self._hosts.pop(hid, None)
        return dead

    def connected_hosts(self) -> List[dict]:
        with self._lock:
            self._prune_stale_locked()
            return [{"host_id": hid, "host": h["host"], "version": h["version"],
                     "capabilities": h["capabilities"],
                     "idle_seconds": round(time.time() - h["last_seen"], 1)}
                    for hid, h in self._hosts.items()]

    # ---------- 宿主拉取待执行命令（长轮询）----------
    async def poll(self, host_id: str, token: Optional[str],
                   client_ip: Optional[str], timeout: float = 25.0) -> List[dict]:
        self._check_loopback(client_ip)
        self._check_token(token)
        with self._lock:
            h = self._hosts.get(host_id)
            if h is None:
                raise BridgeError("not_registered", "未握手的 host_id，请先完成 §8 handshake")
            h["last_seen"] = time.time()
            queue = h["queue"]
        cmds: List[dict] = []
        try:
            first = await asyncio.wait_for(queue.get(), timeout=timeout)
            cmds.append(first)
            # 一次尽量取空队列里其它命令，减少往返
            while not queue.empty():
                cmds.append(queue.get_nowait())
        except asyncio.TimeoutError:
            pass
        return cmds

    # ---------- 宿主回传真实结果 → 对账并唤醒等待中的 dispatch ----------
    async def submit_result(self, request_id: str, token: Optional[str],
                            client_ip: Optional[str], result: dict) -> dict:
        self._check_loopback(client_ip)
        self._check_token(token)
        with self._lock:
            pend = self._pending.pop(request_id, None)
        if pend is None:
            # 未知/已超时请求：如实上报，绝不静默丢弃或伪造
            return {"status": "unknown_request", "request_id": request_id, "fabricated": False}
        fut: asyncio.Future = pend["future"]
        if not fut.done():
            fut.set_result({"result": result, "tool": pend["tool"], "host": pend["host"]})
        return {"status": "ack", "request_id": request_id, "fabricated": False}

    # ---------- 服务端下发命令到已连接宿主并等待真实回执 ----------
    async def dispatch(self, tool: str, args: dict, project_id: str, document_id: str,
                       protected_objects: Optional[List[str]] = None,
                       timeout: float = 30.0, client_ip: Optional[str] = None) -> dict:
        self._check_loopback(client_ip)
        meta = BRIDGEABLE_TOOLS.get(tool)
        if not meta:
            raise BridgeError("unknown_tool", f"§23：不可桥接的工具 {tool!r}")
        want_host = meta["host"]
        # Phase 78：写集确认门先于宿主查找与入队——缺 confirm 时返回可审阅的计划，
        # 零入队、零文档变更；宿主端还会二次校验 confirm，双层门不可绕过。
        if meta.get("write") and (args or {}).get("confirm") is not True:
            plan_args = args or {}
            target = {k: plan_args[k] for k in (
                "kind", "name", "index", "x1", "y1", "x2", "y2", "x", "y", "text", "dx", "dy"
            ) if k in plan_args}
            return {
                "status": "confirmation_required",
                "tool": tool, "host": want_host, "intent": meta["intent"],
                "plan": {
                    "tool": tool, "intent": meta["intent"], "risk": meta["risk"],
                    "host": want_host, "target": target,
                    "effects": ["object.create" if meta["intent"] == "object.create" else "object.move"],
                    "reversible": True, "save": False,
                },
                "fabricated": False,
                "note": "写操作需显式 confirm=true；本次未入队、未执行任何文档变更，且永不 Save",
            }
        with self._lock:
            self._prune_stale_locked()
            same_host = [h for h in self._hosts.values() if h["host"] == want_host]
            if not same_host:
                raise BridgeError("no_host_connected",
                                  f"§24/§97：无已连接的 {want_host} UXP 宿主，拒绝凭空伪造文档操作")
            # §25/§26：同名宿主可能不止一个——只挑真正协商过所需能力的那个；
            # 全都未协商则如实 capability_not_negotiated（绝不拿只读宿主硬跑写工具）。
            cands = [h for h in same_host if CapabilityDetector.covers(h["capabilities"], tool)]
            if not cands:
                raise BridgeError("capability_not_negotiated",
                                  f"§25：已连接的 {want_host} 宿主都未上报所需能力 "
                                  f"[{meta['capability']}]，工具 {tool} 暂不可执行")
            host_entry = cands[0]
            caps = host_entry["capabilities"]
        # §25/§26：以能力协商结果门控（非版本）
        if not CapabilityDetector.covers(caps, tool):
            raise BridgeError("capability_not_negotiated",
                              f"§25：宿主未上报所需能力 [{meta['capability']}]，工具 {tool} 暂不可执行")
        request = ObjectMapper.build_request(
            tool, args, project_id, document_id,
            permissions={"protected_objects": protected_objects or []},
        )
        rid = request["request_id"]
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        with self._lock:
            self._pending[rid] = {"future": fut, "host_id": next(
                hid for hid, h in self._hosts.items() if h is host_entry),
                "tool": tool, "host": want_host}
            host_entry["queue"].put_nowait(request)
        try:
            pack = await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            with self._lock:
                self._pending.pop(rid, None)
            raise BridgeError("host_timeout",
                              f"§8：宿主 {want_host} 在 {timeout}s 内未回传结果，如实报超时（不伪造成功）")
        result = pack["result"]
        status = result.get("status") if isinstance(result, dict) else None
        # 宿主如实报错 → ErrorMapper 映射稳定契约码，绝不吞成成功
        if status in ("error", "failed"):
            mapped = ErrorMapper.map(result.get("error", "host_error"))
            return {"status": "host_error", "error": mapped, "host": want_host,
                    "detail": result.get("detail", ""), "request_id": rid, "fabricated": False}
        recon = DocumentReader.reconcile(result)
        return {"status": "ok", "host": want_host, "tool": tool, "request_id": rid,
                "result": result.get("payload", result), "reconciled": recon,
                "fabricated": False,
                "note": "结果由宿主真实回传并对账；服务端未执行任何 Adobe 文档操作（§97）"}

    def view(self) -> dict:
        with self._lock:
            self._prune_stale_locked()
            pend = list(self._pending.keys())
        return {
            "implemented": True, "fabricated": False,
            "session_token": self._mask(self.session_token),
            "discovery_file": str(self.discovery_path),
            "discovery_exists": self.discovery_path.exists(),
            "connected_hosts": self.connected_hosts(),
            "pending_requests": len(pend),
            "bridgeable_tools": sorted(BRIDGEABLE_TOOLS.keys()),
            "honest_boundary": ("adobe_uxp_bridge=supported（管道）；ps.*/ai.* 文档写=needs_adobe_host（须真实宿主）；"
                                "cdr.* 须本机已运行的 CorelDRAW（宿主脚本 attach-only，未运行→no_host_connected）；"
                                "cdr 写集仅 create/move（confirm 双层门、永不 Save、回读 verified 由宿主自报）"),
        }


# ---------------------------------------------------------------------------
# 独立自测（确定性部分：兼容矩阵 / §9 帧 / 能力门控 / 认证 / 未知请求）
# 异步端到端（握手→poll→回传→对账）在服务层测试用协议合规 mock host 验证。
# ---------------------------------------------------------------------------
def _self_test() -> int:  # pragma: no cover
    ok = True

    def check(name, cond, detail=""):
        nonlocal ok
        print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))
        ok = ok and cond

    eng = AdobeBridgeEngine(state_dir=Path(os.environ.get("TMP", "/tmp")) / "zj_uxp_selftest")
    # 端口动态发现
    info = eng.publish_discovery(8799)
    check("discovery: port + token persisted", info["port"] == 8799 and len(info["session_token"]) > 20)
    check("discovery: token masked in view (never plaintext)",
          eng.session_token not in json.dumps(eng.view()), f"mask={eng.view()['session_token']}")
    # Phase 25：discovery 与 state_dir 平级（历史 uxp/uxp 冗余嵌套已修），读取保留旧位置回退
    check("Phase25 discovery: file sits directly in state_dir (no uxp/uxp nesting)",
          eng.discovery_path == eng.state_dir / "bridge-endpoint.json" and eng.discovery_path.exists())
    eng.discovery_path.unlink()
    eng.legacy_discovery_path.parent.mkdir(parents=True, exist_ok=True)
    eng.legacy_discovery_path.write_text(json.dumps({"port": 1234, "session_token": "legacy-token"}), encoding="utf-8")
    check("Phase25 discovery: legacy nested file still readable as migration fallback",
          (eng.read_discovery() or {}).get("port") == 1234)
    eng.publish_discovery(8801)
    check("Phase25 discovery: republish writes flat path and sweeps legacy nested file (no stale token left)",
          (eng.read_discovery() or {}).get("port") == 8801
          and eng.discovery_path.exists() and not eng.legacy_discovery_path.exists())
    eng.legacy_discovery_path.parent.rmdir()
    # §9 帧构造
    req = ObjectMapper.build_request("ps.layer.create", {"name": "bg"}, "p1", "d1",
                                     permissions={"protected_objects": ["logo"]})
    check("§9 request: unified fields",
          req["intent"] == "layer.create" and req["options"]["risk"] == "L1"
          and req["permissions"]["protected_objects"] == ["logo"] and req["request_id"].startswith("req_"))
    # §25/§26 能力门控（不读版本）
    check("§26 gate: capability-negotiated tool executable",
          CompatibilityMatrix.is_executable("photoshop", "ps.layer.create", {"layer_write": True}))
    check("§26 gate: missing required capability -> not executable (version ignored)",
          not CompatibilityMatrix.is_executable("photoshop", "ps.layer.create", {"document_read": True})
          and CompatibilityMatrix.is_executable("photoshop", "ps.layer.create", {"layer_write": True, "document_read": False}))
    check("§26 gate: cross-host tool rejected",
          not CompatibilityMatrix.is_executable("photoshop", "ai.object.create", {"object_write": True}))
    # Phase 77：CorelDRAW COM 只读三工具（host=coreldraw，能力键 document_read）
    check("Phase77 cdr: three read-only tools registered under coreldraw/document_read",
          all(BRIDGEABLE_TOOLS.get(t, {}).get("host") == "coreldraw"
              and BRIDGEABLE_TOOLS.get(t, {}).get("capability") == "document_read"
              and BRIDGEABLE_TOOLS.get(t, {}).get("risk") == "L0"
              for t in ("cdr.document.get", "cdr.page.list", "cdr.object.list")))
    check("Phase77 cdr: capability gate (coreldraw+document_read only; photoshop cross-host rejected)",
          CompatibilityMatrix.is_executable("coreldraw", "cdr.document.get", {"document_read": True})
          and not CompatibilityMatrix.is_executable("coreldraw", "cdr.document.get", {"layer_write": True})
          and not CompatibilityMatrix.is_executable("photoshop", "cdr.page.list", {"document_read": True}))
    # Phase 78：CorelDRAW 最小写集（create/move，capacity=object_write，risk=L1，confirm 门）
    check("Phase78 cdr write: two write tools registered under coreldraw/object_write at L1",
          all(BRIDGEABLE_TOOLS.get(t, {}).get("host") == "coreldraw"
              and BRIDGEABLE_TOOLS.get(t, {}).get("capability") == "object_write"
              and BRIDGEABLE_TOOLS.get(t, {}).get("risk") == "L1"
              and BRIDGEABLE_TOOLS.get(t, {}).get("write") is True
              for t in ("cdr.object.create", "cdr.object.move")))
    check("Phase78 cdr write: object_write gate (document_read alone insufficient; cross-host rejected)",
          CompatibilityMatrix.is_executable("coreldraw", "cdr.object.create", {"object_write": True})
          and not CompatibilityMatrix.is_executable("coreldraw", "cdr.object.create", {"document_read": True})
          and not CompatibilityMatrix.is_executable("photoshop", "cdr.object.move", {"object_write": True}))
    check("Phase78 cdr write: no delete/save/export tool invented",
          not any(t in BRIDGEABLE_TOOLS for t in (
              "cdr.object.delete", "cdr.object.save", "cdr.document.save", "cdr.document.export")))
    # ErrorMapper 诚实
    check("ErrorMapper: protected object -> conflict (never ok)",
          ErrorMapper.map("protected_object") == "protected_object_conflict")
    check("ErrorMapper: unknown host_error -> host_execution_failed (no swallow)",
          ErrorMapper.map("weird") == "host_execution_failed")
    check("Phase78 ErrorMapper: write refusal codes stay explicit (not generic host_error)",
          ErrorMapper.map("invalid_args") == "invalid_args"
          and ErrorMapper.map("confirmation_required") == "confirmation_required"
          and ErrorMapper.map("object_not_found") == "object_not_found"
          and ErrorMapper.map("com_api_unavailable") == "com_api_unavailable")
    # 认证：token 与 loopback 校验（同步检查器）
    try:
        eng._check_token("wrong"); auth_ok = False
    except BridgeError as e:
        auth_ok = e.kind == "auth_failed"
    check("§8 auth: wrong token -> auth_failed", auth_ok)
    try:
        eng._check_loopback("10.0.0.5"); origin_ok = False
    except BridgeError as e:
        origin_ok = e.kind == "origin_rejected"
    check("§8 origin: non-loopback -> origin_rejected", origin_ok)

    async def _async_checks():
        results = {}
        # 无宿主时 dispatch 必须诚实 no_host_connected，绝不伪造
        try:
            await eng.dispatch("ps.layer.create", {}, "p", "d", timeout=1, client_ip="127.0.0.1")
            results["no_host"] = False
        except BridgeError as e:
            results["no_host"] = (e.kind == "no_host_connected")
        # §2 负向诚实：宿主回传一个未知/已过期 request_id 时，须如实标 unknown_request，
        #   绝不静默丢弃、更绝不给本不存在的请求伪造一条"已对账 ack"
        res = await eng.submit_result("req-does-not-exist", eng.session_token, "127.0.0.1",
                                     {"ok": True, "invented": "payload"})
        results["unknown_req"] = (res["status"] == "unknown_request" and res["fabricated"] is False)
        # Phase 78：写集确认门先于宿主查找——无 confirm 时绝不入队，plan 里明写 save=false
        try:
            pre = await eng.dispatch("cdr.object.create", {"kind": "rectangle"}, "p", "d",
                                     timeout=2, client_ip="127.0.0.1")
        except BridgeError:
            pre = {}
        results["cdr_write_confirm_gate"] = (
            pre.get("status") == "confirmation_required" and pre.get("fabricated") is False
            and (pre.get("plan") or {}).get("save") is False
            and (pre.get("plan") or {}).get("risk") == "L1")
        try:
            await eng.dispatch("cdr.object.create", {"confirm": True, "kind": "rectangle"},
                               "p", "d", timeout=2, client_ip="127.0.0.1")
            results["cdr_write_no_host"] = False
        except BridgeError as e:
            results["cdr_write_no_host"] = (e.kind == "no_host_connected")
        # Phase 77：coreldraw 宿主握手 → dispatch→poll→result 全链（宿主端行为由本测试模拟，
        # 真实 COM 只发生在 cdr_host.py，服务端永不 import COM）
        hs = await eng.handshake("coreldraw", "24.0", {"document_read": True},
                                 eng.session_token, "127.0.0.1")
        results["cdr_handshake"] = (hs.get("status") == "connected"
                                    and hs.get("fabricated") is False
                                    and str(hs.get("host_id", "")).startswith("coreldraw-")
                                    and isinstance(hs.get("advisory"), list))
        disp = asyncio.ensure_future(eng.dispatch("cdr.object.list", {}, "p", "d",
                                                  timeout=5, client_ip="127.0.0.1"))
        await asyncio.sleep(0)
        cmds = await eng.poll(hs["host_id"], eng.session_token, "127.0.0.1", timeout=2)
        results["cdr_poll_shape"] = (bool(cmds) and cmds[0].get("tool") == "cdr.object.list"
                                     and cmds[0].get("intent") == "object.list"
                                     and cmds[0].get("options", {}).get("risk") == "L0")
        if cmds:
            await eng.submit_result(cmds[0]["request_id"], eng.session_token, "127.0.0.1",
                                    {"status": "ok", "payload": {
                                        "document_id": "cdr-doc-1",
                                        "objects": [{"index": 1, "name": "Rectangle",
                                                     "type": "rectangle"}]}})
        pack = await asyncio.wait_for(disp, timeout=5)
        results["cdr_roundtrip"] = (pack.get("status") == "ok" and pack.get("fabricated") is False
                                    and pack.get("host") == "coreldraw"
                                    and pack.get("reconciled", {}).get("source") == "host"
                                    and pack.get("reconciled", {}).get("object_count") == 1)
        # Phase 78：只读宿主（document_read only）不得执行写工具——能力门控优先于"有同名宿主"
        try:
            await eng.dispatch("cdr.object.create",
                               {"confirm": True, "kind": "rectangle",
                                "x1": 0, "y1": 0, "x2": 10, "y2": 10},
                               "p", "d", timeout=1, client_ip="127.0.0.1")
            results["cdr_write_readonly_host"] = False
        except BridgeError as e:
            results["cdr_write_readonly_host"] = (e.kind == "capability_not_negotiated")
        # 写能力宿主：确认门发生在宿主查找/入队之前；无 confirm 时两个宿主队列都不得出现该命令
        hw = await eng.handshake("coreldraw", "24.0",
                                 {"document_read": True, "object_write": True},
                                 eng.session_token, "127.0.0.1")
        results["cdr_write_handshake"] = (
            hw.get("status") == "connected"
            and (hw.get("capabilities") or {}).get("object_write") is True)
        try:
            pre2 = await eng.dispatch("cdr.object.create", {"kind": "rectangle"},
                                      "p", "d", timeout=2, client_ip="127.0.0.1")
        except BridgeError:
            pre2 = {}
        q_read = await eng.poll(hs["host_id"], eng.session_token, "127.0.0.1", timeout=0.1)
        q_write = await eng.poll(hw["host_id"], eng.session_token, "127.0.0.1", timeout=0.1)
        results["cdr_write_confirm_zero_enqueue"] = (
            pre2.get("status") == "confirmation_required"
            and not any(c.get("tool") == "cdr.object.create" for c in q_read + q_write))
        # 确认后的 create：§9 帧 L1 + 宿主回执对账（verified / object_count / undo_group）
        disp2 = asyncio.ensure_future(eng.dispatch(
            "cdr.object.create",
            {"confirm": True, "kind": "rectangle", "x1": 10, "y1": 20, "x2": 110, "y2": 70,
             "name": "P78Rect"},
            "p", "d", timeout=5, client_ip="127.0.0.1"))
        await asyncio.sleep(0)
        cmds2 = await eng.poll(hw["host_id"], eng.session_token, "127.0.0.1", timeout=2)
        results["cdr_write_poll_shape"] = (
            bool(cmds2) and cmds2[0].get("tool") == "cdr.object.create"
            and cmds2[0].get("intent") == "object.create"
            and cmds2[0].get("options", {}).get("risk") == "L1")
        if cmds2:
            await eng.submit_result(cmds2[0]["request_id"], eng.session_token, "127.0.0.1",
                                    {"status": "ok", "payload": {
                                        "document_id": "cdr-doc-1",
                                        "objects": [{"index": 4, "name": "P78Rect",
                                                     "type": "rectangle", "x": 10, "y": 20,
                                                     "width": 100, "height": 50}],
                                        "object_count": 4, "verified": True,
                                        "undo_group": "grouped", "source": "fixture"}})
        try:
            pack2 = await asyncio.wait_for(disp2, timeout=5)
        except BridgeError:
            pack2 = {}
        results["cdr_write_roundtrip"] = (
            pack2.get("status") == "ok" and pack2.get("fabricated") is False
            and pack2.get("host") == "coreldraw"
            and (pack2.get("reconciled") or {}).get("verified") is True
            and (pack2.get("reconciled") or {}).get("object_count") == 4
            and (pack2.get("reconciled") or {}).get("undo_group") == "grouped")
        # 确认后的 move：位置读回，数量不变
        disp3 = asyncio.ensure_future(eng.dispatch(
            "cdr.object.move", {"confirm": True, "name": "P78Rect", "dx": 5, "dy": -3},
            "p", "d", timeout=5, client_ip="127.0.0.1"))
        await asyncio.sleep(0)
        cmds3 = await eng.poll(hw["host_id"], eng.session_token, "127.0.0.1", timeout=2)
        results["cdr_move_poll_shape"] = (
            bool(cmds3) and cmds3[0].get("tool") == "cdr.object.move"
            and cmds3[0].get("intent") == "object.move"
            and cmds3[0].get("options", {}).get("risk") == "L1")
        if cmds3:
            await eng.submit_result(cmds3[0]["request_id"], eng.session_token, "127.0.0.1",
                                    {"status": "ok", "payload": {
                                        "document_id": "cdr-doc-1",
                                        "objects": [{"index": 4, "name": "P78Rect",
                                                     "type": "rectangle", "x": 15, "y": 17,
                                                     "width": 100, "height": 50}],
                                        "object_count": 4, "verified": True,
                                        "undo_group": "grouped", "source": "fixture"}})
        try:
            pack3 = await asyncio.wait_for(disp3, timeout=5)
        except BridgeError:
            pack3 = {}
        results["cdr_move_roundtrip"] = (
            pack3.get("status") == "ok"
            and (pack3.get("reconciled") or {}).get("verified") is True
            and (pack3.get("reconciled") or {}).get("object_count") == 4
            and ((pack3.get("result") or {}).get("objects") or [{}])[0].get("x") == 15)
        # 未交付的删除工具必须如实 unknown_tool
        try:
            await eng.dispatch("cdr.object.delete", {"confirm": True}, "p", "d",
                               timeout=1, client_ip="127.0.0.1")
            results["cdr_delete_unknown"] = False
        except BridgeError as e:
            results["cdr_delete_unknown"] = (e.kind == "unknown_tool")
        try:
            await eng.handshake("corel", "1", {"document_read": True},
                                eng.session_token, "127.0.0.1")
            results["cdr_unknown_host"] = False
        except BridgeError as e:
            results["cdr_unknown_host"] = (e.kind == "unknown_host")
        return results

    _ar = asyncio.new_event_loop().run_until_complete(_async_checks())
    check("§97: no host connected -> honest no_host_connected (no fake)", _ar["no_host"])
    check("§result: unknown/expired request_id -> honest unknown_request (never fabricate ack)",
          _ar["unknown_req"], f"res={_ar['unknown_req']}")
    check("Phase77 cdr: handshake coreldraw connected (capability-negotiated, fabricated=false)",
          _ar["cdr_handshake"])
    check("Phase77 cdr: dispatch->poll->result round-trip reconciled source=host (never server-faked)",
          _ar["cdr_poll_shape"] and _ar["cdr_roundtrip"],
          f"shape={_ar['cdr_poll_shape']} roundtrip={_ar['cdr_roundtrip']}")
    check("Phase77 cdr: unknown host 'corel' still rejected (whitelist extended by exact name)",
          _ar["cdr_unknown_host"])
    check("Phase78 cdr: write without confirm -> confirmation_required + plan (save=false), zero enqueue",
          _ar["cdr_write_confirm_gate"] and _ar["cdr_write_confirm_zero_enqueue"])
    check("Phase78 cdr: confirmed write w/o host -> honest no_host_connected (never server-faked)",
          _ar["cdr_write_no_host"])
    check("Phase78 cdr: read-only host refuses object_write (capability gate beats same-host match)",
          _ar["cdr_write_readonly_host"])
    check("Phase78 cdr: object_write host handshake negotiated",
          _ar["cdr_write_handshake"])
    check("Phase78 cdr: create -> §9 frame L1 + reconciled verified/object_count/undo_group",
          _ar["cdr_write_poll_shape"] and _ar["cdr_write_roundtrip"])
    check("Phase78 cdr: move -> §9 frame L1 + verified readback (count unchanged)",
          _ar["cdr_move_poll_shape"] and _ar["cdr_move_roundtrip"])
    check("Phase78 cdr: delete tool not shipped -> unknown_tool (never invented)",
          _ar["cdr_delete_unknown"])
    print("\n[PASS] adobe bridge engine self-test" if ok else "\n[FAIL] adobe bridge engine self-test")
    return 0 if ok else 1


if __name__ == "__main__":  # pragma: no cover
    import sys
    sys.exit(_self_test())
