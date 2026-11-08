#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
造境 AI 设计智能体 — CorelDRAW 宿主脚本（Phase 77 只读 + Phase 78 最小写集）
============================================================================
定位：这是**宿主端**进程，不是引擎、不随 EXE 分发、不被服务端 import。
它扮演与 UXP 面板同构的客户端角色（§8/§9/§24/§25/§26）：

    discovery 文件（端口 + 随机 Session Token）
        -> POST /api/v1/uxp/handshake  {host:"coreldraw",
           capabilities:{document_read:true, object_write:true}}
        -> 循环 POST /api/v1/uxp/poll  {host_id, token}        （长轮询取 §9 命令）
        -> 本进程在**本机 CorelDRAW 进程内**经 COM 真实读取/执行
        -> POST /api/v1/uxp/result     {request_id, status, payload|error}

【诚实边界（§2/§97）】
  * 只读：document.get / page.list / object.list。
  * 最小写集（Phase 78）：object.create（rectangle/ellipse/text）与 object.move。
    **没有** delete / rename / save / export；未知 intent 如实 not_supported。
  * 写操作必须 args.confirm is True（服务端确认门之外，宿主再校验一次）；
    缺 confirm 一律 confirmation_required，零文档变更。
  * 每次写入后回读对象并把 verified / undo_group / source 如实回传；
    撤销组是**尽力**（BeginCommandGroup/EndCommandGroup 失败则 undo_group=unsupported）。
    **任何情况下永不 Save**（不调用 document.Save / app.Save）。
  * COM 仅用 GetActiveObject **attach 到已在运行的实例**；绝不 Dispatch、
    绝不启动 CorelDRAW GUI（启动 GUI 属明令禁止）。未运行 -> coreldraw_not_running。
  * 服务端不 import 任何 COM 库，也不执行任何文档操作；结果全部由本进程回传后对账。
  * --fake-backend 是**确定性夹具**（source=fixture），仅供自测/协议联调；
    它明确标注来源，绝不冒充真实文档数据。

用法：
  python cdr_host.py --probe [--report FILE]     只读探测（模块/注册表/进程/attach）
  python cdr_host.py --self-test                 无网络无 COM 的确定性自测
  python cdr_host.py                             默认：attach 真宿主并长轮询
  python cdr_host.py --fake-backend --once       夹具后端跑一轮（协议联调）

退出码：0 正常；2 用法/探测失败；3 coreldraw_not_running；4 桥接服务不可达；
        5 握手被拒（auth/origin/host）；6 COM attach 其它失败。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

HOST_NAME = "coreldraw"
CAPABILITIES = {"document_read": True, "object_write": True}
READ_INTENTS = ("document.get", "page.list", "object.list")
WRITE_INTENTS = ("object.create", "object.move")
WRITABLE_KINDS = ("rectangle", "ellipse", "text")
DEFAULT_LIMIT = 100
MAX_LIMIT = 500

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_NOT_RUNNING = 3
EXIT_BRIDGE_DOWN = 4
EXIT_HANDSHAKE_REJECTED = 5
EXIT_ATTACH_FAILED = 6


class InvalidArgs(Exception):
    """入参不可执行：如实 invalid_args，绝不在缺参时猜默认值。"""


class ObjectNotFound(Exception):
    """按 index/name 找不到目标对象：如实 object_not_found。"""


class AmbiguousTarget(Exception):
    """按 name 命中多个对象：如实 target_ambiguous，绝不挑第一个。"""


class ComApiUnavailable(Exception):
    """COM 版本缺少所需成员：如实 com_api_unavailable，不伪造成功。"""


# ---------------------------------------------------------------------------
# 夹具后端（确定性；明确标注 source=fixture，绝不冒充真实文档）
# ---------------------------------------------------------------------------
FIXTURE_DOC = {
    "document_id": "cdr-fixture-doc",
    "name": "fixture.cdr",
    "pages": 2,
    "active_page": 1,
}
FIXTURE_PAGES = [
    {"index": 1, "name": "Page 1", "width": 210.0, "height": 297.0},
    {"index": 2, "name": "Page 2", "width": 210.0, "height": 297.0},
]
FIXTURE_SHAPES = [
    {"index": 1, "name": "Rectangle", "type": "rectangle",
     "x": 20, "y": 30, "width": 120, "height": 80},
    {"index": 2, "name": "Ellipse", "type": "ellipse",
     "x": 60, "y": 50, "width": 90, "height": 90},
    {"index": 3, "name": "Text", "type": "text",
     "x": 40, "y": 180, "width": 80, "height": 20, "text": "Zaojing"},
]


def _is_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _require_number(args: Dict[str, Any], key: str):
    v = args.get(key)
    if not _is_number(v):
        raise InvalidArgs(f"缺少或非数值参数 {key}")
    return v


def _normalize_create(args: Dict[str, Any]) -> Dict[str, Any]:
    kind = args.get("kind")
    if kind not in WRITABLE_KINDS:
        raise InvalidArgs(f"kind 只支持 {WRITABLE_KINDS}")
    name = args.get("name")
    if name is not None and not isinstance(name, str):
        raise InvalidArgs("name 必须是字符串")
    if kind in ("rectangle", "ellipse"):
        x1 = _require_number(args, "x1")
        y1 = _require_number(args, "y1")
        x2 = _require_number(args, "x2")
        y2 = _require_number(args, "y2")
        if x2 <= x1 or y2 <= y1:
            raise InvalidArgs("x2/y2 必须大于 x1/y1")
        return {"kind": kind, "name": name, "x1": x1, "y1": y1, "x2": x2, "y2": y2,
                "x": x1, "y": y1, "width": x2 - x1, "height": y2 - y1}
    x = _require_number(args, "x")
    y = _require_number(args, "y")
    text = args.get("text")
    if not isinstance(text, str) or not text.strip():
        raise InvalidArgs("text 必须是非空字符串")
    # 夹具/估算几何：文本宽度按字符数估算并明确属于夹具回读，不冒充真实排版度量
    return {"kind": "text", "name": name, "x": x, "y": y,
            "width": max(1, 8 * len(text)), "height": 20, "text": text}


def _normalize_move(args: Dict[str, Any]) -> Dict[str, Any]:
    dx = _require_number(args, "dx")
    dy = _require_number(args, "dy")
    index = args.get("index")
    name = args.get("name")
    if index is None and not name:
        raise InvalidArgs("move 需要 index 或 name 之一")
    if index is not None and (not isinstance(index, int) or isinstance(index, bool) or index < 1):
        raise InvalidArgs("index 必须是 >=1 的整数")
    if name is not None and not isinstance(name, str):
        raise InvalidArgs("name 必须是字符串")
    return {"index": index, "name": name, "dx": dx, "dy": dy}


def _find_shape(shapes: List[dict], index, name) -> dict:
    if index is not None:
        for s in shapes:
            if s.get("index") == index:
                return s
        raise ObjectNotFound(f"index={index}")
    hits = [s for s in shapes if s.get("name") == name]
    if len(hits) > 1:
        raise AmbiguousTarget(f"name={name!r} 命中 {len(hits)} 个对象")
    if not hits:
        raise ObjectNotFound(f"name={name!r}")
    return hits[0]


class FakeBackend:
    """确定性夹具：无 COM、无 GUI、无网络。source 字段永远写 fixture。"""

    kind = "fixture"

    def __init__(self) -> None:
        self._shapes: List[dict] = [dict(s) for s in FIXTURE_SHAPES]

    def document_get(self, args: Dict[str, Any]) -> Dict[str, Any]:
        return dict(FIXTURE_DOC, source="fixture", note="夹具数据，非真实 CorelDRAW 文档")

    def page_list(self, args: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "document_id": FIXTURE_DOC["document_id"],
            "pages": [dict(p) for p in FIXTURE_PAGES],
            "page_count": len(FIXTURE_PAGES),
            "truncated": False,
            "source": "fixture",
        }

    def object_list(self, args: Dict[str, Any]) -> Dict[str, Any]:
        limit = _clamp_limit(args.get("limit", DEFAULT_LIMIT))
        shapes = self._shapes[:limit]
        return {
            "document_id": FIXTURE_DOC["document_id"],
            "objects": [dict(s) for s in shapes],
            "object_count": len(shapes),
            "truncated": len(self._shapes) > limit,
            "limit": limit,
            "source": "fixture",
        }

    def object_create(self, args: Dict[str, Any]) -> Dict[str, Any]:
        p = _normalize_create(args)
        nxt = max((s.get("index", 0) for s in self._shapes), default=0) + 1
        obj = {
            "index": nxt,
            "name": p["name"] or f"{p['kind'].capitalize()} {nxt}",
            "type": p["kind"],
            "x": p["x"], "y": p["y"],
            "width": p["width"], "height": p["height"],
        }
        if p["kind"] == "text":
            obj["text"] = p["text"]
        self._shapes.append(obj)
        return {
            "document_id": FIXTURE_DOC["document_id"],
            "objects": [dict(obj)],
            "object_count": len(self._shapes),
            "verified": True,
            "undo_group": "unsupported",  # 夹具没有真实撤销栈，如实上报
            "source": "fixture",
            "note": "夹具写入：仅内存状态，不触碰真实文档、永不 Save",
        }

    def object_move(self, args: Dict[str, Any]) -> Dict[str, Any]:
        p = _normalize_move(args)
        obj = _find_shape(self._shapes, p["index"], p["name"])
        obj["x"] += p["dx"]
        obj["y"] += p["dy"]
        return {
            "document_id": FIXTURE_DOC["document_id"],
            "objects": [dict(obj)],
            "object_count": len(self._shapes),
            "verified": True,
            "undo_group": "unsupported",
            "source": "fixture",
            "note": "夹具移动：仅内存状态，不触碰真实文档、永不 Save",
        }


# ---------------------------------------------------------------------------
# COM 后端（attach-only：GetActiveObject，绝不 Dispatch / 绝不启动 GUI）
# ---------------------------------------------------------------------------
class CorelNotRunning(Exception):
    pass


class ComBackend:
    kind = "com"

    def __init__(self, app) -> None:
        self.app = app

    @classmethod
    def attach(cls) -> "ComBackend":
        try:
            import pythoncom  # type: ignore
            import win32com.client  # type: ignore
        except Exception as e:  # pragma: no cover - 依赖缺失时如实上报
            raise RuntimeError(f"pywin32_unavailable: {type(e).__name__}")
        pythoncom.CoInitialize()
        try:
            # attach-only：只查询 ROT 中已运行实例，不 Dispatch（Dispatch 会启动 GUI）
            app = win32com.client.GetActiveObject("CorelDRAW.Application")
        except Exception as e:  # com_error 等
            code = getattr(e, "hresult", None)
            text = str(e)
            if code in (-2147221021, 0x800401E3) or "operation unavailable" in text.lower():
                raise CorelNotRunning("coreldraw_not_running")
            raise
        return cls(app)

    # ---- 以下全部只读；任何属性读取失败都返回 null，不自造值 ----
    @staticmethod
    def _get(obj, name: str, default=None):
        try:
            return getattr(obj, name)
        except Exception:
            return default

    def _active_document(self):
        try:
            return self.app.ActiveDocument
        except Exception:
            return None

    def document_get(self, args: Dict[str, Any]) -> Dict[str, Any]:
        doc = self._active_document()
        if doc is None:
            return {"document_id": None, "name": None, "pages": 0,
                    "active_page": None, "source": "com", "note": "no_active_document"}
        pages = self._get(self._get(doc, "Pages"), "Count", 0)
        active = self._get(doc, "ActivePage", None)
        return {
            "document_id": self._get(doc, "FullFileName", None) or self._get(doc, "Name", None),
            "name": self._get(doc, "Name", None),
            "pages": int(pages or 0),
            "active_page": self._get(active, "Index", None) if active is not None else None,
            "source": "com",
        }

    def page_list(self, args: Dict[str, Any]) -> Dict[str, Any]:
        doc = self._active_document()
        if doc is None:
            return {"document_id": None, "pages": [], "page_count": 0,
                    "truncated": False, "source": "com", "note": "no_active_document"}
        pages_obj = self._get(doc, "Pages", None)
        count = int(self._get(pages_obj, "Count", 0) or 0)
        limit = _clamp_limit(args.get("limit", DEFAULT_LIMIT))
        out: List[dict] = []
        for i in range(1, min(count, limit) + 1):
            try:
                page = pages_obj.Item(i)
            except Exception:
                continue
            out.append({
                "index": i,
                "name": self._get(page, "Name", None),
                "width": _num(self._get(page, "SizeWidth", None)),
                "height": _num(self._get(page, "SizeHeight", None)),
            })
        return {
            "document_id": self._get(doc, "FullFileName", None) or self._get(doc, "Name", None),
            "pages": out,
            "page_count": len(out),
            "truncated": count > limit,
            "limit": limit,
            "source": "com",
        }

    def object_list(self, args: Dict[str, Any]) -> Dict[str, Any]:
        doc = self._active_document()
        if doc is None:
            return {"document_id": None, "objects": [], "object_count": 0,
                    "truncated": False, "source": "com", "note": "no_active_document"}
        page = self._get(doc, "ActivePage", None)
        shapes = self._get(page, "Shapes", None)
        count = int(self._get(shapes, "Count", 0) or 0)
        limit = _clamp_limit(args.get("limit", DEFAULT_LIMIT))
        out: List[dict] = []
        for i in range(1, min(count, limit) + 1):
            try:
                shape = shapes.Item(i)
            except Exception:
                continue
            out.append({
                "index": i,
                "name": self._get(shape, "Name", None),
                "type": _num(self._get(shape, "Type", None)),
            })
        return {
            "document_id": self._get(doc, "FullFileName", None) or self._get(doc, "Name", None),
            "objects": out,
            "object_count": len(out),
            "truncated": count > limit,
            "limit": limit,
            "source": "com",
        }

    # ---- Phase 78 最小写集：create / move（attach-only 实例内执行，永不 Save）----
    def _active_shapes(self):
        doc = self._active_document()
        if doc is None:
            return None, None
        page = self._get(doc, "ActivePage", None)
        return doc, self._get(page, "Shapes", None)

    def _call_first(self, obj, names, *args):
        for name in names:
            fn = self._get(obj, name, None)
            if callable(fn):
                return fn(*args)
        raise ComApiUnavailable(f"COM 成员缺失: {names}")

    def _begin_group(self) -> bool:
        fn = self._get(self.app, "BeginCommandGroup", None)
        if not callable(fn):
            return False
        try:
            fn()
            return True
        except Exception:
            return False

    def _end_group(self, began: bool) -> None:
        if not began:
            return
        fn = self._get(self.app, "EndCommandGroup", None)
        if callable(fn):
            try:
                fn()
            except Exception:
                pass

    def _shape_info(self, shape, index) -> Dict[str, Any]:
        info = {
            "index": index,
            "name": self._get(shape, "Name", None),
            "type": _num(self._get(shape, "Type", None)),
            "x": _num(self._get(shape, "PositionX", None)),
            "y": _num(self._get(shape, "PositionY", None)),
            "width": _num(self._get(shape, "SizeWidth", None)),
            "height": _num(self._get(shape, "SizeHeight", None)),
        }
        text = self._get(shape, "Text", None)
        if isinstance(text, str):
            info["text"] = text
        return info

    def _find_index_by_name(self, shapes, name: str) -> int:
        count = int(self._get(shapes, "Count", 0) or 0)
        hits: List[int] = []
        for i in range(1, count + 1):
            try:
                shape = shapes.Item(i)
            except Exception:
                continue
            if self._get(shape, "Name", None) == name:
                hits.append(i)
        if len(hits) > 1:
            raise AmbiguousTarget(f"name={name!r} 命中 {len(hits)} 个对象")
        if not hits:
            raise ObjectNotFound(f"name={name!r}")
        return hits[0]

    def object_create(self, args: Dict[str, Any]) -> Dict[str, Any]:
        p = _normalize_create(args)
        doc, shapes = self._active_shapes()
        if shapes is None:
            return {"document_id": None, "objects": [], "object_count": 0,
                    "verified": False, "undo_group": "unsupported", "source": "com",
                    "note": "no_active_document"}
        began = self._begin_group()
        try:
            if p["kind"] == "rectangle":
                shape = self._call_first(shapes, ("CreateRectangle",),
                                         p["x1"], p["y1"], p["x2"], p["y2"])
            elif p["kind"] == "ellipse":
                shape = self._call_first(shapes, ("CreateEllipse",),
                                         p["x1"], p["y1"], p["x2"], p["y2"])
            else:
                shape = self._call_first(shapes, ("CreateArtisticText",),
                                         p["x"], p["y"], p["text"])
            if p["name"]:
                try:
                    shape.Name = p["name"]
                except Exception:
                    pass  # 命名失败不影响"对象已创建"这一事实；回读会如实显示真实名称
        finally:
            self._end_group(began)
        count = int(self._get(shapes, "Count", 0) or 0)
        info = self._shape_info(shape, count)
        return {
            "document_id": self._get(doc, "FullFileName", None) or self._get(doc, "Name", None),
            "objects": [info],
            "object_count": count,
            "verified": bool(info.get("index")),
            "undo_group": "grouped" if began else "unsupported",
            "source": "com",
        }

    def object_move(self, args: Dict[str, Any]) -> Dict[str, Any]:
        p = _normalize_move(args)
        doc, shapes = self._active_shapes()
        if shapes is None:
            return {"document_id": None, "objects": [], "object_count": 0,
                    "verified": False, "undo_group": "unsupported", "source": "com",
                    "note": "no_active_document"}
        index = p["index"] if p["index"] is not None else self._find_index_by_name(shapes, p["name"])
        try:
            shape = shapes.Item(index)
        except Exception as e:
            raise ObjectNotFound(f"index={index}: {type(e).__name__}")
        began = self._begin_group()
        try:
            move_fn = self._get(shape, "Move", None)
            if callable(move_fn):
                move_fn(p["dx"], p["dy"])
            else:
                posx = _num(self._get(shape, "PositionX", None))
                posy = _num(self._get(shape, "PositionY", None))
                if posx is None or posy is None:
                    raise ComApiUnavailable("COM shape 既无 Move 也无 PositionX/Y")
                shape.PositionX = posx + p["dx"]
                shape.PositionY = posy + p["dy"]
        finally:
            self._end_group(began)
        count = int(self._get(shapes, "Count", 0) or 0)
        info = self._shape_info(shape, index)
        return {
            "document_id": self._get(doc, "FullFileName", None) or self._get(doc, "Name", None),
            "objects": [info],
            "object_count": count,
            "verified": bool(info.get("x") is not None and info.get("y") is not None),
            "undo_group": "grouped" if began else "unsupported",
            "source": "com",
        }


def _num(v):
    """把 COM 数值规范化为 int/float；不可转换则原样/None（不猜）。"""
    if v is None:
        return None
    try:
        f = float(v)
    except Exception:
        return None
    return int(f) if f.is_integer() else f


def _clamp_limit(v) -> int:
    try:
        n = int(v)
    except Exception:
        n = DEFAULT_LIMIT
    return max(1, min(MAX_LIMIT, n))


def execute_intent(backend, intent: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """执行一个 intent；未知 intent 如实 not_supported（§26 能力边界）。

    写侧（Phase 78）在宿主端再设一道 confirm 门：即使服务端确认门被绕过，
    缺 confirm 仍一律 confirmation_required 且零文档变更。
    """
    args = args or {}
    if intent in WRITE_INTENTS:
        if args.get("confirm") is not True:
            return {"status": "error", "error": "confirmation_required",
                    "detail": "宿主端二次校验：写操作需 args.confirm=true"}
        try:
            if intent == "object.create":
                payload = backend.object_create(args)
            else:
                payload = backend.object_move(args)
        except InvalidArgs as e:
            return {"status": "error", "error": "invalid_args", "detail": str(e)}
        except ObjectNotFound as e:
            return {"status": "error", "error": "object_not_found", "detail": str(e)}
        except AmbiguousTarget as e:
            return {"status": "error", "error": "target_ambiguous", "detail": str(e)}
        except ComApiUnavailable as e:
            return {"status": "error", "error": "com_api_unavailable", "detail": str(e)}
        return {"status": "ok", "payload": payload}
    if intent == "document.get":
        payload = backend.document_get(args)
    elif intent == "page.list":
        payload = backend.page_list(args)
    elif intent == "object.list":
        payload = backend.object_list(args)
    else:
        return {"status": "error", "error": "not_supported",
                "detail": f"cdr_host 只实现 {READ_INTENTS + WRITE_INTENTS}，收到 {intent!r}"}
    return {"status": "ok", "payload": payload}


# ---------------------------------------------------------------------------
# discovery / HTTP（纯标准库；宿主是客户端，服务端不反连）
# ---------------------------------------------------------------------------
def default_discovery_paths() -> List[Path]:
    paths: List[Path] = []
    env = os.environ.get("ZAOJING_DATA_DIR")
    if env:
        paths.append(Path(env) / "uxp" / "bridge-endpoint.json")
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    paths.append(Path(base) / "Zaojing" / "uxp" / "bridge-endpoint.json")
    # 历史嵌套位（Phase 25 之前）只读回退
    if env:
        paths.append(Path(env) / "uxp" / "uxp" / "bridge-endpoint.json")
    paths.append(Path(base) / "Zaojing" / "uxp" / "uxp" / "bridge-endpoint.json")
    seen, out = set(), []
    for p in paths:
        key = str(p).lower()
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out


def read_discovery(explicit: Optional[str]) -> Dict[str, Any]:
    candidates = [Path(explicit)] if explicit else default_discovery_paths()
    for p in candidates:
        try:
            if p.exists():
                info = json.loads(p.read_text(encoding="utf-8"))
                info["_path"] = str(p)
                return info
        except Exception:
            continue
    raise FileNotFoundError(f"discovery_not_found: {[str(p) for p in candidates]}")


def _post_json(url: str, body: dict, timeout: float) -> Tuple[int, dict]:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        try:
            payload = json.loads(e.read().decode("utf-8") or "{}")
        except Exception:
            payload = {}
        return e.code, payload


def _error_code(payload: dict) -> str:
    detail = payload.get("detail")
    if isinstance(detail, dict):
        return str(detail.get("error") or detail.get("status") or "unknown")
    return str(detail or payload.get("error") or "unknown")


def handshake(base_url: str, token: str, version: str, timeout: float = 10.0):
    status, payload = _post_json(base_url + "/api/v1/uxp/handshake", {
        "host": HOST_NAME, "version": version,
        "capabilities": dict(CAPABILITIES), "token": token,
    }, timeout)
    if status != 200:
        return None, status, _error_code(payload)
    return payload.get("host_id"), status, ""


# ---------------------------------------------------------------------------
# 自测（无网络 / 无 COM / 无 GUI）
# ---------------------------------------------------------------------------
def self_test() -> int:
    ok = True

    def check(name: str, cond: bool, detail: str = "") -> None:
        nonlocal ok
        print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))
        ok = ok and bool(cond)

    be = FakeBackend()
    doc = execute_intent(be, "document.get", {})
    check("self-test: document.get ok, source=fixture, document_id present",
          doc["status"] == "ok" and doc["payload"]["source"] == "fixture"
          and doc["payload"]["document_id"] == "cdr-fixture-doc", doc["status"])
    pages = execute_intent(be, "page.list", {})
    check("self-test: page.list returns 2 pages deterministically",
          pages["status"] == "ok" and pages["payload"]["page_count"] == 2
          and [p["index"] for p in pages["payload"]["pages"]] == [1, 2])
    objs = execute_intent(be, "object.list", {"limit": 2})
    check("self-test: object.list honours limit + truncated flag",
          objs["status"] == "ok" and objs["payload"]["object_count"] == 2
          and objs["payload"]["truncated"] is True and objs["payload"]["limit"] == 2)
    objs_all = execute_intent(be, "object.list", {})
    check("self-test: object.list default returns all 3 fixture shapes",
          objs_all["payload"]["object_count"] == 3 and objs_all["payload"]["truncated"] is False)
    unk = execute_intent(be, "layer.create", {"name": "x"})
    check("self-test: unknown write intent -> not_supported (never fabricated ok)",
          unk["status"] == "error" and unk["error"] == "not_supported")
    check("self-test: limit clamp (0 -> 1, 10**9 -> 500)",
          _clamp_limit(0) == 1 and _clamp_limit(10 ** 9) == MAX_LIMIT)
    check("self-test: binary never claims com (fixture only)", be.kind == "fixture")

    # ---- Phase 78：最小写集（create/move）确认门 / 回读 / 撤销组 / 无 delete-save ----
    check("self-test p78: host advertises object_write alongside document_read",
          CAPABILITIES.get("document_read") is True and CAPABILITIES.get("object_write") is True)
    _nc = execute_intent(be, "object.create", {
        "kind": "rectangle", "x1": 10, "y1": 20, "x2": 110, "y2": 70, "name": "P78Rect"})
    _after_nc = execute_intent(be, "object.list", {})
    check("self-test p78: create without confirm -> confirmation_required, zero mutation",
          _nc["status"] == "error" and _nc["error"] == "confirmation_required"
          and _after_nc["payload"]["object_count"] == 3, _nc.get("error", ""))
    _cr = execute_intent(be, "object.create", {
        "confirm": True, "kind": "rectangle", "x1": 10, "y1": 20, "x2": 110, "y2": 70,
        "name": "P78Rect"})
    _cr_obj = (_cr.get("payload") or {}).get("objects") or [{}]
    check("self-test p78: confirmed create -> count 3->4, readback verified, undo group reported",
          _cr["status"] == "ok" and _cr["payload"]["object_count"] == 4
          and _cr["payload"]["verified"] is True
          and _cr["payload"]["undo_group"] in ("grouped", "unsupported")
          and _cr_obj[0].get("name") == "P78Rect" and _cr_obj[0].get("width") == 100
          and _cr_obj[0].get("height") == 50, json.dumps(_cr.get("payload"))[:160])
    _mv = execute_intent(be, "object.move", {"confirm": True, "name": "P78Rect", "dx": 5, "dy": -3})
    _mv_obj = (_mv.get("payload") or {}).get("objects") or [{}]
    check("self-test p78: confirmed move -> position read back, count unchanged",
          _mv["status"] == "ok" and _mv["payload"]["object_count"] == 4
          and _mv["payload"]["verified"] is True
          and _mv_obj[0].get("x") == 15 and _mv_obj[0].get("y") == 17,
          json.dumps(_mv.get("payload"))[:160])
    _mvn = execute_intent(be, "object.move", {"name": "P78Rect", "dx": 1, "dy": 1})
    _after_mvn = execute_intent(be, "object.list", {})
    check("self-test p78: move without confirm -> confirmation_required, position unchanged",
          _mvn["status"] == "error" and _mvn["error"] == "confirmation_required"
          and (_after_mvn["payload"]["objects"] or [{}])[3].get("x") == 15,
          _mvn.get("error", ""))
    for _forbidden in ("object.delete", "object.save", "document.save", "layer.delete"):
        _fr = execute_intent(be, _forbidden, {"confirm": True})
        check("self-test p78: %s not shipped -> not_supported" % _forbidden,
              _fr["status"] == "error" and _fr["error"] == "not_supported")
    print("[PASS] cdr_host self-test" if ok else "[FAIL] cdr_host self-test")
    return EXIT_OK if ok else 1


# ---------------------------------------------------------------------------
# 只读探测
# ---------------------------------------------------------------------------
def probe(report_path: Optional[str]) -> int:
    report: Dict[str, Any] = {
        "host": HOST_NAME, "attach_only": True, "gui_launched": False,
        "modules": {}, "registry": {}, "process": {}, "attach": {},
        "discovery": {}, "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    for mod in ("win32com", "win32com.client", "pythoncom", "pywintypes", "comtypes"):
        try:
            __import__(mod)
            report["modules"][mod] = True
        except Exception:
            report["modules"][mod] = False

    try:
        import winreg  # type: ignore
        for key in ("CorelDRAW.Application", "CorelDRAW.Application.24",
                    "CorelDRAW.Application.25", "CorelDRAW.Application.26"):
            try:
                with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, key) as k:
                    # 形制（p77_3_reg_shape 实测）：CLSID / CurVer 都是**子键**，
                    # 其默认值才是 GUID / 版本 ProgID；根键下没有同名值。
                    try:
                        with winreg.OpenKey(k, "CLSID") as ck:
                            clsid = winreg.QueryValue(ck, None)
                    except OSError:
                        clsid = None
                    try:
                        with winreg.OpenKey(k, "CurVer") as vk:
                            curver = winreg.QueryValue(vk, None)
                    except OSError:
                        curver = None
                    report["registry"][key] = {"exists": True, "clsid": clsid, "curver": curver}
            except OSError:
                report["registry"][key] = {"exists": False}
    except Exception as e:  # pragma: no cover
        report["registry"]["error"] = type(e).__name__

    try:
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq CorelDRAW.exe", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=10,
        ).stdout or ""
        report["process"] = {"running": "CorelDRAW.exe" in out,
                             "lines": [ln for ln in out.splitlines() if ln.strip()][:5]}
    except Exception as e:
        report["process"] = {"running": None, "error": type(e).__name__}

    try:
        info = read_discovery(None)
        report["discovery"] = {k: info.get(k) for k in ("_path", "scheme", "host", "port")}
        report["discovery"]["token_present"] = bool(info.get("session_token"))
    except Exception as e:
        report["discovery"] = {"found": False, "error": str(e).split(":")[0]}

    if report["modules"].get("win32com.client") and report["process"].get("running"):
        try:
            ComBackend.attach()
            report["attach"] = {"attempted": True, "ok": True}
        except CorelNotRunning:
            report["attach"] = {"attempted": True, "ok": False, "error": "coreldraw_not_running"}
        except Exception as e:
            report["attach"] = {"attempted": True, "ok": False,
                                "error": type(e).__name__, "code": getattr(e, "hresult", None)}
    else:
        # 未运行时不 attach：既避免噪声，也证明"绝不启动 GUI"
        report["attach"] = {"attempted": False, "ok": False, "error": "skipped_not_running"}

    text = json.dumps(report, ensure_ascii=True, indent=2)
    if report_path:
        p = Path(report_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        print(f"PROBE report={p}")
    else:
        print(text)
    print("PROBE attach_only=True gui_launched=False "
          f"modules_ok={all(report['modules'].values())} "
          f"process_running={report['process'].get('running')} "
          f"attach_ok={report['attach'].get('ok')} "
          f"attach_error={report['attach'].get('error')}")
    return EXIT_OK


# ---------------------------------------------------------------------------
# 长轮询主循环
# ---------------------------------------------------------------------------
def serve(args) -> int:
    if args.fake_backend:
        backend = FakeBackend()
        version = "fixture"
    else:
        try:
            backend = ComBackend.attach()
        except CorelNotRunning:
            print("ATTACH error=coreldraw_not_running (attach-only; no GUI launched)")
            return EXIT_NOT_RUNNING
        except Exception as e:
            print(f"ATTACH error={type(e).__name__} code={getattr(e, 'hresult', None)}")
            return EXIT_ATTACH_FAILED
        version = str(getattr(backend.app, "Version", "") or "")

    try:
        info = read_discovery(args.discovery)
    except FileNotFoundError as e:
        print(f"BRIDGE error=discovery_not_found detail={str(e).split(':')[0]}")
        return EXIT_BRIDGE_DOWN

    base_url = args.base_url or f"{info.get('scheme', 'http')}://{info.get('host', '127.0.0.1')}:{info.get('port')}"
    token = args.token or info.get("session_token")
    if not token:
        print("BRIDGE error=token_missing")
        return EXIT_BRIDGE_DOWN

    host_id, status, err = handshake(base_url, token, version)
    if not host_id:
        print(f"HANDSHAKE status={status} error={err}")
        return EXIT_HANDSHAKE_REJECTED
    print(f"HANDSHAKE ok host_id={host_id} backend={backend.kind} base={base_url}")

    cycles = 0
    try:
        while True:
            cycles += 1
            try:
                st, payload = _post_json(base_url + "/api/v1/uxp/poll", {
                    "host_id": host_id, "token": token, "timeout": args.poll_timeout,
                }, timeout=args.poll_timeout + 10)
            except Exception as e:
                print(f"POLL error={type(e).__name__}")
                return EXIT_BRIDGE_DOWN
            if st != 200:
                print(f"POLL status={st} error={_error_code(payload)}")
                return EXIT_BRIDGE_DOWN
            cmds = payload.get("commands") or []
            print(f"POLL commands={len(cmds)}")
            for cmd in cmds:
                rid = cmd.get("request_id")
                intent = cmd.get("intent")
                cargs = (cmd.get("context") or {}).get("args") or {}
                try:
                    res = execute_intent(backend, intent, cargs)
                except Exception as e:  # COM 执行期异常 -> 如实 host_error
                    res = {"status": "error", "error": "host_error", "detail": type(e).__name__}
                if res.get("status") == "ok":
                    _pl = res.get("payload") or {}
                    print(f"EXEC tool={cmd.get('tool')} intent={intent} status=ok "
                          f"objects={len(_pl.get('objects') or [])} "
                          f"verified={_pl.get('verified')} undo_group={_pl.get('undo_group')}")
                else:
                    print(f"EXEC tool={cmd.get('tool')} intent={intent} "
                          f"status=error error={res.get('error')}")
                body = {"request_id": rid, "token": token, "status": res.get("status")}
                if res.get("status") == "ok":
                    body["payload"] = res.get("payload")
                else:
                    body["error"] = res.get("error")
                    body["detail"] = res.get("detail", "")
                try:
                    rst, rpayload = _post_json(base_url + "/api/v1/uxp/result", body, 10)
                    print(f"RESULT status={rst} ack={rpayload.get('status')}")
                except Exception as e:
                    print(f"RESULT error={type(e).__name__}")
            if args.once:
                return EXIT_OK
    except KeyboardInterrupt:
        print("STOP keyboard_interrupt")
        return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="CorelDRAW attach-only bridge host (read-only + minimal write set).",
        epilog="Read: document.get / page.list / object.list. Write: object.create / object.move "
               "(both require confirm=true). Never launches CorelDRAW; never saves the document.",
    )
    ap.add_argument("--discovery", help="bridge-endpoint.json path (default: ZAOJING_DATA_DIR/LOCALAPPDATA)")
    ap.add_argument("--base-url", help="override base URL, e.g. http://127.0.0.1:8000")
    ap.add_argument("--token", help="override session token (default: from discovery file)")
    ap.add_argument("--fake-backend", action="store_true", help="deterministic fixture backend (no COM)")
    ap.add_argument("--once", action="store_true", help="handshake + one poll cycle + exit")
    ap.add_argument("--poll-timeout", type=float, default=20.0, help="long-poll seconds (default 20)")
    ap.add_argument("--probe", action="store_true", help="read-only probe (modules/registry/process/attach)")
    ap.add_argument("--report", help="probe JSON output path (UTF-8)")
    ap.add_argument("--self-test", action="store_true", help="offline deterministic self-test")
    return ap


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.self_test:
        return self_test()
    if args.probe:
        return probe(args.report)
    return serve(args)


if __name__ == "__main__":
    sys.exit(main())
