# -*- coding: utf-8 -*-
"""
event_engine.py — 造境 AI 设计智能体 第 29 引擎
============================================================
WebSocket 实时事件流的单一真相源：连接簿记 + 事件广播 + 环形历史缓冲 + 运行时台账。

Phase 81（轨道 A 服务侧最后一刀）之前，EventHub 类内联在 agent_service 里，
故在 Registry 2.0 中恒为 evidence_kind=no_golden_cases_yet——逻辑真实可观测，
却因无独立引擎模块而无法被评测台黄金域核验。本引擎把 EventHub 与 _emit 逐字下沉为
可导入模块，agent_service 的 /ws WebSocket / /events/recent 观测端点 / 各处 _emit 调用
全部委托本引擎（单一真相源，无平行副本），评测台再以一枚黄金用例真实驱动本引擎实例
（私有沙箱、期望值手算），从而 verified 由磁盘最新报告如实派生。

诚实边界：本引擎只交付「广播 + 环形缓冲 + 运行时台账」这一确定性机制。
事件是否真被浏览器/客户端接收取决于 WS 连接的存活，本引擎不伪造交付量。

设计纪律（与既有引擎同源）：
  - 纯标准库（asyncio/collections/datetime/json/pathlib/logging/typing）。
  - 事件 payload 入库前统一收敛为 JSON 安全值（Path / 枚举 / Pydantic 模型 → 字面量），
    绝不静默丢弃，未知类型退化为 str()。
  - 环形缓冲 maxlen=200；published_total / kind_counts 累计不因截断而丢失。
"""

import asyncio
import json
import logging
import sys
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

logger = logging.getLogger("zaojing.events")


def json_safe(obj: Any) -> Any:
    """把任意 Python 对象收敛为可 JSON 序列化的值。未知类型退化 str，绝不静默丢弃。"""
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    # Pydantic BaseModel 兼容判定：duck-typing 而非 import pydantic（避免拉起可选依赖）
    if hasattr(obj, "model_dump") and callable(getattr(obj, "model_dump")):
        try:
            return json_safe(obj.model_dump())
        except Exception:
            return str(obj)
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, dict):
        return {str(k): json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set, frozenset)):
        return [json_safe(v) for v in obj]
    # Enum → name 而不是 repr
    if hasattr(obj, "name") and hasattr(obj, "value") and type(obj).__class__.__name__ == "EnumMeta":
        return str(obj.name)
    import enum as _enum
    if isinstance(obj, _enum.Enum):
        return obj.name
    if hasattr(obj, "__dict__"):
        try:
            return json_safe(vars(obj))
        except Exception:
            return str(obj)
    return str(obj)


class EventHub:
    """WebSocket 连接簿记 + 事件广播。维护最近事件环形缓冲供新连接补发。

    Phase 51 起以运行时台账（kind_counts / published_total）如实回答
    "本进程实际发出过什么"，绝不靠手写清单自报；history 被 maxlen 截断时
    台账仍完整累计。
    """

    def __init__(self, history: int = 200):
        self._conns: set = set()
        self._lock = asyncio.Lock()
        self.history: deque = deque(maxlen=history)
        self.history_capacity = history
        self.kind_counts: Dict[str, int] = {}
        self.published_total = 0

    async def connect(self, ws) -> None:
        # ws.accept 由调用方（FastAPI websocket 端点）负责；本引擎只做簿记，
        # 避免与具体 transport 耦合。
        async with self._lock:
            self._conns.add(ws)

    async def disconnect(self, ws) -> None:
        async with self._lock:
            self._conns.discard(ws)

    @property
    def client_count(self) -> int:
        return len(self._conns)

    async def publish(self, event: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """入历史缓冲 + 累计台账 + 逐连接推送；死连接从簿记摘除。

        台账先算再推送，即使 send_json 全部失败也不影响"本进程实际发出过什么"
        这一事实。返回值 delivered_to 为**推送成功**数（真实网络观测面）。
        """
        payload = {"event": event, "data": json_safe(data),
                   "ts": datetime.now().isoformat()}
        self.history.append(payload)
        self.kind_counts[event] = self.kind_counts.get(event, 0) + 1
        self.published_total += 1
        async with self._lock:
            targets = list(self._conns)
        dead: List[Any] = []
        for ws in targets:
            try:
                await ws.send_json(payload)
            except Exception as e:
                logger.debug(f"ws send failed ({event}): {e}")
                dead.append(ws)
        if dead:
            async with self._lock:
                for ws in dead:
                    self._conns.discard(ws)
        return {"event": event, "delivered_to": len(targets) - len(dead)}

    def recent(self, limit: int = 50) -> Dict[str, Any]:
        """只读观测面：/api/v1/events/recent 委托本方法。"""
        evts = list(self.history)[-limit:] if limit > 0 else []
        return {
            "clients": self.client_count,
            "total": len(evts),
            "events": evts,
            "kinds_seen": sorted(self.kind_counts),
            "kind_counts": dict(sorted(self.kind_counts.items())),
            "kind_count": len(self.kind_counts),
            "published_total": self.published_total,
            "history_capacity": self.history_capacity,
            "truncated": self.published_total > len(self.history),
        }


# 进程级单例；agent_service 里的 _EVENT_HUB 与各处 _emit 调用全部指向此实例。
_EVENT_HUB = EventHub()


def hub() -> EventHub:
    return _EVENT_HUB


async def emit(event: str, data: Dict[str, Any]) -> None:
    """向事件总线发布任务事件；无连接时仅入历史缓冲，绝不报错阻断业务。"""
    try:
        await _EVENT_HUB.publish(event, data)
    except Exception as e:
        logger.warning(f"event publish failed ({event}): {e}")


# ---------------------------------------------------------------------------
# 自测（Phase 81：assert-gated，__main__ 走真断言，非恒真 PASS）
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import asyncio as _a

    async def _selftest():
        hub_ = EventHub(history=5)
        # 1) json_safe：Path / dict / list / set / Enum 皆收敛为 JSON 安全值
        class _Enum:
            pass
        import enum as _e

        class _Color(_e.Enum):
            RED = 1
            GREEN = 2

        js = json_safe({"a": Path("C:/tmp/x"), "b": [1, 2, _e.auto],
                        "c": _Color.RED, "d": {1, 2, 3}})
        assert isinstance(js["a"], str) and js["a"].endswith("x"), js["a"]
        assert js["c"] == "RED", js["c"]
        assert js["b"][2] is not None
        assert set(js["d"]) == {"1", "2", "3"} or set(js["d"]) == {1, 2, 3}, js["d"]
        # 2) 空连接发布 → 仍入缓冲 + 累计，delivered_to=0（不伪造交付量）
        r0 = await hub_.publish("task.registered", {"task_id": "t0", "total_steps": 3})
        assert r0["delivered_to"] == 0, r0
        assert hub_.published_total == 1 and hub_.kind_counts["task.registered"] == 1
        # 3) 环形截断：连发 8 条，history 长度=capacity 而 published_total=8、
        #    truncated=True（"没发生的形制也要以 0 出现，截断与未截断是两回事"）
        for i in range(7):
            await hub_.publish("task.progress", {"task_id": "t0", "current_step": i + 1})
        snap = hub_.recent(limit=100)
        assert snap["published_total"] == 8, snap
        assert len(snap["events"]) == 5, snap  # 环形 5 位
        assert snap["truncated"] is True, snap
        assert snap["kind_counts"]["task.registered"] == 1, snap
        assert snap["kind_counts"]["task.progress"] == 7, snap
        # 4) 假 ws 连接：send_json 计数 + 死连接摘除（不静默保留）
        class _FakeWS:
            def __init__(self, fail=False):
                self.sent = []
                self.fail = fail

            async def send_json(self, payload):
                if self.fail:
                    raise RuntimeError("simulated disconnect")
                self.sent.append(payload)

        ok = _FakeWS()
        bad = _FakeWS(fail=True)
        await hub_.connect(ok)
        await hub_.connect(bad)
        assert hub_.client_count == 2, hub_.client_count
        r = await hub_.publish("task.completed", {"task_id": "t0"})
        # 台账累计 +1；ok 收到、bad 失败并从簿记摘除
        assert hub_.published_total == 9
        assert r["delivered_to"] == 1, r
        assert hub_.client_count == 1, hub_.client_count
        assert len(ok.sent) == 1
        # 5) disconnect 幂等（同一 ws 再次摘除不炸）
        await hub_.disconnect(ok)
        await hub_.disconnect(ok)
        assert hub_.client_count == 0
        # 6) 单例：模块级 emit/hub 指向同一实例（供 agent_service 委托）
        assert hub() is _EVENT_HUB
        await emit("eval.completed", {"case": "x"})
        snap2 = hub().recent(limit=1)
        assert snap2["kinds_seen"], snap2

    _a.run(_selftest())
    print("[PASS] event_engine self-test: json_safe/history-ring/ledger/dead-conn/disconnect-idempotent")
