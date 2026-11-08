# -*- coding: utf-8 -*-
"""
service_registry_engine.py — 造境 AI 设计智能体 第 28 引擎
============================================================
服务侧「持久化配置」三类能力的单一真相源：

  1. feature_flags   —— JSON 持久化功能开关（种子默认 / setdefault 合并新默认 / 落盘）
  2. tool_registry   —— 工具契约注册表（L0-L4 风险 + requires_adobe_host）
                        + 宿主能力矩阵（capability map）
                        + 逐工具 runnable_now 可执行性派生（不伪造宿主）
  3. skill_runtime   —— 技能清单加载 + 权限白名单 / 工具引用 / 风险 静态校验

Phase 80（轨道 A 第二刀）之前，这三类的实现内联在 agent_service 的端点包装函数里，
在 Registry 2.0 中恒为 evidence_kind=no_golden_cases_yet——逻辑真实可观测，却因
「无独立引擎模块」而无法被评测台黄金域核验。本引擎把它们逐字下沉为可导入模块，
agent_service 改为委托本引擎（单一真相源，无平行副本），评测台再以三枚黄金用例
真实驱动本引擎实例（私有沙箱、期望值手算），从而 verified 由磁盘最新报告如实派生。

诚实边界：本引擎只交付「持久化配置 + 静态校验 + 可执行性派生」这一确定性机制。
ps.*/ai.* 工具是否真能执行，仍取决于真实 Adobe/CAD 宿主上报的 capability_map——
无宿主上报时 runnable_now 如实为 False，本引擎绝不代劳伪造「可执行」。

设计纪律（与既有引擎同源）：
  - 纯标准库（json/os/pathlib/logging），零第三方依赖，零网络。
  - 每个实例绑定一个 data_root，自带进程内缓存，实例之间零串扰（评测逐用例私有沙箱）。
  - 原子落盘：写 .tmp 再 os.replace，坏输入不半写。
  - 损坏的持久化 JSON 回退默认并 WARN，不静默、不伪造。
"""

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger("zaojing.service_registry")

# ---------------------------------------------------------------------------
# 真实内置契约常量（单一真相源；agent_service 不再各写一份，全部委托本引擎）
# ---------------------------------------------------------------------------

# 工具契约：元数据 + 风险分级 L0-L4 + 是否需 Adobe 宿主。
# 注册表本身是真实的工具契约存储；ps.*/ai.* 的实际执行仍受宿主能力门控（见 capability map）。
DEFAULT_TOOLS: Dict[str, Dict[str, Any]] = {
    "asset.search":        {"description": "素材语义检索", "risk_level": "L0", "requires_adobe_host": False},
    "preflight.check":     {"description": "印前检查", "risk_level": "L0", "requires_adobe_host": False},
    "workflow.execute":    {"description": "工作流执行", "risk_level": "L2", "requires_adobe_host": False},
    "recovery.snapshot":   {"description": "创建快照", "risk_level": "L1", "requires_adobe_host": False},
    "generate.image":      {"description": "AI 生成图像", "risk_level": "L2", "requires_adobe_host": False},
    "file.delete":         {"description": "删除文件（高危）", "risk_level": "L4", "requires_adobe_host": False},
    "ps.open_document":    {"description": "打开 Photoshop 文档", "risk_level": "L1", "requires_adobe_host": True},
    "ps.add_layer":        {"description": "新增图层", "risk_level": "L2", "requires_adobe_host": True},
    "ps.export":           {"description": "导出文档", "risk_level": "L1", "requires_adobe_host": True},
    "ai.create_object":    {"description": "Illustrator 创建矢量对象", "risk_level": "L2", "requires_adobe_host": True},
}

# 功能开关默认值（首次加载无持久化文件时种子落盘；已有文件时 setdefault 补齐新键）。
DEFAULT_FLAGS: Dict[str, bool] = {
    "webp_preview_enabled": True,
    "auto_preflight_on_import": True,
    "beta_design_review": False,
    "telemetry_enabled": False,
}

# 技能可申请的权限白名单（最小权限原则 §28）：越权权限静态拒绝落盘。
ALLOWED_PERMISSIONS = {
    "asset.read", "asset.write", "preflight.run", "workflow.run",
    "recovery.snapshot", "generate.text", "generate.image", "file.read",
}

# 技能风险合法取值（清单校验用，与工具风险 L0-L4 是两套口径）。
_VALID_SKILL_RISKS = ("low", "medium", "high")


# ---------------------------------------------------------------------------
# 原子 JSON 读写（损坏回退默认并 WARN，绝不静默伪造）
# ---------------------------------------------------------------------------

def _read_json(path: Path, default: Any) -> Any:
    if path.is_file():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            logger.warning("损坏的持久化文件，回退默认: %s", path)
    return default


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


class ServiceRegistryEngine:
    """绑定单一 data_root 的服务侧持久化配置引擎（flags / tools / skills）。"""

    def __init__(self, data_root: Path):
        self.data_root = Path(data_root)
        self._cache: Dict[str, Any] = {}

    # ---------------------------------------------------------------- 路径
    def _flags_path(self) -> Path:
        return self.data_root / "config" / "feature-flags.json"

    def _tools_path(self) -> Path:
        return self.data_root / "tools" / "registry.json"

    def _capability_map_path(self) -> Path:
        return self.data_root / "tools" / "capability_map.json"

    def skills_dir(self) -> Path:
        d = self.data_root / "skills"
        d.mkdir(parents=True, exist_ok=True)
        return d

    # ============================================================ 功能开关
    def get_flags(self) -> Dict[str, bool]:
        if "flags" not in self._cache:
            p = self._flags_path()
            flags = _read_json(p, None)
            if not isinstance(flags, dict):
                flags = dict(DEFAULT_FLAGS)
                _write_json(p, flags)
            else:
                for k, v in DEFAULT_FLAGS.items():
                    flags.setdefault(k, v)
            self._cache["flags"] = flags
        return self._cache["flags"]

    def set_flag(self, key: str, value: Any) -> bool:
        flags = self.get_flags()
        flags[key] = bool(value)
        _write_json(self._flags_path(), flags)
        return flags[key]

    # ============================================================ 工具注册表
    def get_tool_registry(self) -> Dict[str, Any]:
        if "tool_registry" not in self._cache:
            p = self._tools_path()
            reg = _read_json(p, None)
            if not isinstance(reg, dict) or not reg:
                reg = {name: dict(meta) for name, meta in DEFAULT_TOOLS.items()}
                _write_json(p, reg)
            self._cache["tool_registry"] = reg
        return self._cache["tool_registry"]

    def save_tool_registry(self) -> None:
        _write_json(self._tools_path(), self.get_tool_registry())

    def register_tool(self, name: str, description: str, risk_level: str,
                      requires_adobe_host: bool) -> int:
        """登记/覆盖一枚工具契约并落盘，返回注册表总量。风险合法性由调用方（端点）判定。"""
        reg = self.get_tool_registry()
        reg[name] = {"description": description, "risk_level": risk_level,
                     "requires_adobe_host": requires_adobe_host}
        self.save_tool_registry()
        return len(reg)

    # ============================================================ 能力矩阵
    def get_capability_map(self) -> Dict[str, Any]:
        if "capability_map" not in self._cache:
            self._cache["capability_map"] = _read_json(self._capability_map_path(), {})
        return self._cache["capability_map"]

    def save_capability_map(self, cmap: Dict[str, Any]) -> None:
        _write_json(self._capability_map_path(), cmap)

    @staticmethod
    def derive_tool_view(reg: Dict[str, Any],
                         cmap: Dict[str, Any]) -> List[Dict[str, Any]]:
        """逐工具派生可执行性视图。runnable_now 由「是否需要宿主」与「宿主是否已上报
        该工具能力」共同决定——需要宿主但无上报 → False（不伪造可执行）。"""
        out: List[Dict[str, Any]] = []
        for name, meta in sorted(reg.items()):
            runnable = (not meta.get("requires_adobe_host")) or bool(
                cmap.get("hosts") and any(
                    cmap["hosts"].get(h, {}).get("capabilities", {}).get(name)
                    for h in cmap["hosts"]))
            out.append({"name": name, **meta, "runnable_now": runnable})
        return out

    def tool_view(self) -> List[Dict[str, Any]]:
        return self.derive_tool_view(self.get_tool_registry(), self.get_capability_map())

    # ============================================================ 技能运行时
    def validate_skill(self, manifest: Dict[str, Any]) -> List[str]:
        errors: List[str] = []
        reg = self.get_tool_registry()
        for p in manifest.get("permissions", []):
            if p not in ALLOWED_PERMISSIONS:
                errors.append("未知/越权权限: %s" % p)
        for t in manifest.get("tools", []):
            if t not in reg:
                errors.append("引用未注册工具: %s" % t)
        if manifest.get("risk") not in _VALID_SKILL_RISKS:
            errors.append("risk 必须为 low/medium/high")
        return errors

    def load_skill_manifests(self) -> Dict[str, Any]:
        skills: Dict[str, Any] = {}
        for mf in self.skills_dir().glob("*/manifest.json"):
            data = _read_json(mf, None)
            if isinstance(data, dict) and data.get("id"):
                skills[data["id"]] = data
        return skills

    # ============================================================ 健康自报
    def health(self) -> Dict[str, Any]:
        return {
            "status": "ready",
            "engine": "service_registry",
            "default_tools": len(DEFAULT_TOOLS),
            "default_flags": len(DEFAULT_FLAGS),
            "allowed_permissions": len(ALLOWED_PERMISSIONS),
        }


if __name__ == "__main__":
    # 真实自测（非恒真：把被测函数换成常量桩这里必红）。用临时沙箱，零污染。
    import shutil
    import tempfile

    tmp = Path(tempfile.mkdtemp(prefix="zj_sr_selftest_"))
    try:
        e = ServiceRegistryEngine(tmp)
        # --- flags ---
        f0 = e.get_flags()
        assert f0 == {
            "webp_preview_enabled": True, "auto_preflight_on_import": True,
            "beta_design_review": False, "telemetry_enabled": False,
        }, f0
        assert e._flags_path().is_file() and json.loads(
            e._flags_path().read_text(encoding="utf-8")) == dict(DEFAULT_FLAGS)
        v = e.set_flag("beta_design_review", 1)
        assert v is True and e.get_flags()["beta_design_review"] is True
        # 磁盘持久化：换新实例读回，须仍为 True（证明落盘非内存）
        assert ServiceRegistryEngine(tmp).get_flags()["beta_design_review"] is True
        # 损坏文件回退默认并重新种子
        e._flags_path().write_text("{not json", encoding="utf-8")
        assert ServiceRegistryEngine(tmp).get_flags() == dict(DEFAULT_FLAGS)

        # --- tool registry ---
        reg = e.get_tool_registry()
        assert len(reg) == 10 and reg["file.delete"]["risk_level"] == "L4"
        assert reg["ps.add_layer"]["requires_adobe_host"] is True
        # 无宿主上报：6 枚非宿主工具 runnable_now=True，4 枚宿主工具=False（不伪造）
        view = e.tool_view()
        run_true = sorted(t["name"] for t in view if t["runnable_now"])
        run_false = sorted(t["name"] for t in view if not t["runnable_now"])
        assert run_true == ["asset.search", "file.delete", "generate.image",
                            "preflight.check", "recovery.snapshot", "workflow.execute"], run_true
        assert run_false == ["ai.create_object", "ps.add_layer", "ps.export",
                             "ps.open_document"], run_false
        # 宿主上报 ps.add_layer=True → 仅这一枚翻 True，其余宿主工具仍 False
        cmap = e.get_capability_map()
        cmap["hosts"] = {"photoshop": {"capabilities": {"ps.add_layer": True}}}
        view2 = e.tool_view()
        assert next(t for t in view2 if t["name"] == "ps.add_layer")["runnable_now"] is True
        assert next(t for t in view2 if t["name"] == "ps.export")["runnable_now"] is False

        # --- skill runtime ---
        assert e.validate_skill({"permissions": ["asset.read"],
                                 "tools": ["preflight.check"], "risk": "low"}) == []
        errs = e.validate_skill({"permissions": ["admin.everything"],
                                 "tools": ["ghost.tool"], "risk": "extreme"})
        assert "未知/越权权限: admin.everything" in errs
        assert "引用未注册工具: ghost.tool" in errs
        assert "risk 必须为 low/medium/high" in errs and len(errs) == 3
        # load：带 id 的清单被加载，缺 id 的被如实跳过
        good = e.skills_dir() / "s1"
        good.mkdir(parents=True, exist_ok=True)
        _write_json(good / "manifest.json", {"id": "s1", "version": "1.0"})
        e.skills_dir().mkdir(parents=True, exist_ok=True)
        bad = e.skills_dir() / "s2"
        bad.mkdir(parents=True, exist_ok=True)
        _write_json(bad / "manifest.json", {"version": "0.0"})
        loaded = e.load_skill_manifests()
        assert set(loaded.keys()) == {"s1"}, loaded
        print("[PASS] service_registry_engine self-test: flags/tools/skills 全真实断言通过")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
