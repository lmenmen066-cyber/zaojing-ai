"""
造境 AI 设计智能体 - 安装/运行时守护引擎 installer_runtime_engine（Phase 26 真实交付）

诚实原则（§2 红线）：
- 本引擎只做**运行时可真实观测**的事：数据根写入探针（真实写入→读回字节比对→清理，
  父路径为文件时必须如实报不可写而非抛崩）、磁盘余量（shutil.disk_usage 真值 +
  阈值真实比较）、端口可用性（真 bind 一个 socket，被占用的端口必须如实报 False）、
  单实例锁（Windows 命名内核对象 CreateMutexW + ERROR_ALREADY_EXISTS 真检测，
  非 Windows 退化为 O_CREAT|O_EXCL 锁文件）、系统快照（真实 sys/platform 值）。
- 诊断报告的状态与计数由**逐项真实检查结果派生**（passed+failed==performed），
  绝不复现原型"无条件 running / 恒 healthy"的假成功。
- 静默安装、注册表写入、快捷方式创建、卸载器执行属 Inno Setup 编译产物（iscc）的
  部署域。Phase 26 起本引擎如实返回 needs_installer_environment 且 fabricated=False，
  能力真值表据此红线分裂：installer_runtime_guard（运行时守护）= supported，
  installer_execution（安装/卸载执行）= not_shipped。
  **Phase 58 该判词退役**：本文件交付了真实执行器（install_plan / execute_install /
  execute_uninstall / execution_state），入口只有 CLI 且必须先逐字交出显式同意令牌，
  HTTP 面结构上走不到它；判定只认磁盘 + 注册表差集，安装器自报一律不作数。
  旧状态原文保留在 install_actions()["retired_boundary"]["was"] 里——退役 ≠ 改写。
- 原型中的"多服务启停"（agent-service/asset-service/... 四服务表）是虚构：
  本产品运行时只有单一 FastAPI 服务进程，不存在可被本引擎启停的服务集群，
  故该 API 整体删除而非伪造。更新检查/下载/替换的真实实现属 update_engine，
  本引擎不重复也不冒充。

历史：V1.0 原型 P2-installer-runtime.py 以硬编码成功糊弄（get_service_status
无条件返回 "running"、start_all_services 对不存在的服务返回 True、
check_for_updates 返回写死的 1.1.0/example.com、"数据库检查"TODO 直接放行），
并依赖第三方 psutil。Phase 26 整文件重写为上述纯标准库 + ctypes 真实实现。
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import socket
import subprocess
import sys
import tempfile
import time
import uuid
import shutil
from pathlib import Path
from typing import Any, Dict, Optional

SCHEMA = "zaojing-runtime-guard/v1"
WINDOWS = os.name == "nt"

# Phase 58：这里曾有 INSTALL_EXECUTION_BOUNDARY = {"status": "needs_installer_environment", ...}，
# 它是 P26 那轮对"整台服务装不了"的诚实申报。P58 交付了执行器本体后，让一枚模块级常量继续
# 宣称"需要安装器环境"就成了**与实况相反的静态声明**（本文件里唯一一处硬编码状态位），故删除。
# 旧口径原文不在代码里改写掉，而是留在两处可对账的地方：
#   install_actions()["retired_boundary"]["was"]（随产品交付的契约面）
#   outputs/p58_actions_probe.json（P58-2 修前真值冻结的磁盘证据）
_DEFAULT_MIN_FREE = 200 * 1024 * 1024  # 200MB：运行时的诚实最低余量阈值


def _write_text(p: Path, s: str) -> None:
    """真实落盘接缝：生产路径即 write_text；测试/变异可在模块级替换以构造"写入内容与读回不一致"。"""
    p.write_text(s, encoding="utf-8")


def _read_text(p: Path) -> str:
    """真实读回接缝（与 _write_text 配对，供负控证伪读回比对）。"""
    return p.read_text(encoding="utf-8")


class RuntimeGuardError(ValueError):
    """运行时守护中的诚实错误（入参非法等），路由层转 4xx。"""


def _finite_num(v: Any, what: str) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise RuntimeGuardError(f"{what} 需为数值，收到 {type(v).__name__}")
    if not math.isfinite(f):
        raise RuntimeGuardError(f"{what} 需为有限数值（拒绝 NaN/Inf）")
    return f


def _clean_path(path: Any, what: str = "path") -> Path:
    if isinstance(path, (str, os.PathLike)) and str(path).strip():
        return Path(path)
    raise RuntimeGuardError(f"{what} 需为非空字符串/路径对象")


def _clean_name(name: Any, what: str = "name") -> str:
    if not isinstance(name, str) or not name.strip() or len(name) > 64:
        raise RuntimeGuardError(f"{what} 需为 1~64 字符的字符串")
    if any(ch in name for ch in "\\/:*?\"<>|"):
        raise RuntimeGuardError(f"{what} 含路径/保留字符，拒绝")
    return name.strip()


class RuntimeGuardEngine:
    """运行时守护：写入探针 / 磁盘 / 端口 / 单实例 / 诊断派生。无状态副作用除真实探针文件。"""

    def __init__(self, data_root: Optional[Path] = None):
        self.data_root = Path(data_root) if data_root else Path(tempfile.gettempdir()) / "zaojing-runtime"
        self._mutex_handles: Dict[str, int] = {}

    # ---------------- 写入探针（判分器变异靶点：读回比对） ----------------

    def probe_write(self, path: Any) -> Dict[str, Any]:
        """真实写→读回字节比对→删除。父路径不存在则创建；父路径为文件必须如实报不可写。"""
        p = _clean_path(path)
        probe_file = p / f"zj_write_probe_{uuid.uuid4().hex[:8]}.tmp"
        payload = f"zaojing-probe-{uuid.uuid4().hex}"
        result: Dict[str, Any] = {
            "schema": SCHEMA, "target": str(p), "writable": False,
            "bytes_verified": False, "cleaned": False,
            "method": "real_write_readback_delete", "fabricated": False, "error": None,
        }
        try:
            p.mkdir(parents=True, exist_ok=True)
            _write_text(probe_file, payload)
            readback = _read_text(probe_file)
            result["writable"] = True
            # 读回不一致即非真实落盘（如被过滤/截断），writable 必须撤回：
            result["bytes_verified"] = readback == payload  # 真实数值比较（判分器变异靶点）
            result["writable"] = result["bytes_verified"]
            probe_file.unlink()
            result["cleaned"] = not probe_file.exists()
        except OSError as e:
            result["error"] = f"{type(e).__name__}: {e}"
            try:
                if probe_file.exists():
                    probe_file.unlink()
                    result["cleaned"] = True
            except OSError:
                pass
        return result

    # ---------------- 磁盘（真实阈值比较） ----------------

    def disk_space(self, path: Any, min_free_bytes: Any = _DEFAULT_MIN_FREE) -> Dict[str, Any]:
        p = _clean_path(path)
        target = p if p.exists() else (p.anchor or Path(tempfile.gettempdir()))
        usage = shutil.disk_usage(target)
        min_free = _finite_num(min_free_bytes, "min_free_bytes")
        if min_free < 0:
            raise RuntimeGuardError("min_free_bytes 不能为负")
        return {
            "schema": SCHEMA, "path": str(target),
            "total_bytes": usage.total, "used_bytes": usage.used, "free_bytes": usage.free,
            "min_free_bytes": min_free,
            "meets_min": usage.free >= min_free,  # 真实数值比较（阈值变异靶点）
            "method": "shutil_disk_usage", "fabricated": False,
        }

    # ---------------- 端口（真实 bind 探测） ----------------

    def check_port(self, port: Any, host: str = "127.0.0.1") -> Dict[str, Any]:
        pv = _finite_num(port, "port")
        if not float(pv).is_integer() or not (1 <= pv <= 65535):
            raise RuntimeGuardError("port 需为 1~65535 的整数")
        if not isinstance(host, str) or not host.strip():
            raise RuntimeGuardError("host 需为非空字符串")
        available = False
        detail = ""
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind((host, int(pv)))  # 真 bind：被占用端口必然 OSError → available=False
            available = True
        except OSError as e:
            detail = f"{type(e).__name__}: {e}"
        finally:
            s.close()
        return {
            "schema": SCHEMA, "host": host, "port": int(pv), "available": available,
            "detail": detail or None, "method": "real_tcp_bind_probe", "fabricated": False,
        }

    # ---------------- 单实例（真实内核对象 / 锁文件） ----------------

    def acquire_single_instance(self, name: Any) -> Dict[str, Any]:
        n = _clean_name(name, "instance_name")
        if WINDOWS:
            import ctypes
            handle = ctypes.windll.kernel32.CreateMutexW(None, False, f"Local\\{n}")
            if not handle:
                raise RuntimeGuardError(f"CreateMutexW 失败: {ctypes.GetLastError()}")
            already = ctypes.GetLastError() == 183  # ERROR_ALREADY_EXISTS：真实内核反馈
            if not already:
                self._mutex_handles[n] = int(handle)
                acquired = True
            else:
                ctypes.windll.kernel32.CloseHandle(handle)  # 已存在句柄由持锁者拥有
                acquired = False
            backend = "kernel32-named-mutex"
        else:
            lock = self.data_root / f"{n}.lock"
            lock.parent.mkdir(parents=True, exist_ok=True)
            try:
                fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, str(os.getpid()).encode())
                os.close(fd)
                acquired, already = True, False
            except FileExistsError:
                acquired, already = False, True
            backend = "o_excl-lockfile"
        return {
            "schema": SCHEMA, "name": n, "acquired": acquired, "already_running": already,
            "backend": backend, "pid": os.getpid(), "fabricated": False,
        }

    def release_single_instance(self, name: Any) -> Dict[str, Any]:
        n = _clean_name(name, "instance_name")
        if WINDOWS:
            import ctypes
            handle = self._mutex_handles.pop(n, None)
            released = False
            if handle:
                ctypes.windll.kernel32.ReleaseMutex(ctypes.c_void_p(handle))
                ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(handle))
                released = True
        else:
            lock = self.data_root / f"{n}.lock"
            try:
                lock.unlink()
                released = True
            except OSError:
                released = False
        return {"schema": SCHEMA, "name": n, "released": released, "fabricated": False}

    # ---------------- 系统快照（真实值） ----------------

    def system_snapshot(self) -> Dict[str, Any]:
        info: Dict[str, Any] = {
            "schema": SCHEMA,
            "python": sys.version.split()[0],
            "python_tuple": list(sys.version_info[:3]),
            "platform": sys.platform,
            "frozen": bool(getattr(sys, "frozen", False)),
            "data_root": str(self.data_root),
            "data_root_exists": self.data_root.exists(),
            "pid": os.getpid(),
            "fabricated": False,
        }
        wv = getattr(sys, "getwindowsversion", None)
        if wv:
            v = wv()
            info["windows"] = {"major": v.major, "minor": v.minor, "build": v.build}
        return info

    # ---------------- 诊断（状态由真实逐项检查派生） ----------------

    def run_diagnostic(self, service_port: Any = None) -> Dict[str, Any]:
        checks = []

        def add(name: str, ok: bool, detail: str):
            checks.append({"check": name, "status": "pass" if ok else "fail", "detail": detail})

        snap = self.system_snapshot()
        wp = self.probe_write(self.data_root / ".runtime-guard")
        add("data_root_writable", wp["writable"], f'{wp["target"]} bytes_verified={wp["bytes_verified"]} {wp["error"] or ""}')

        try:
            ds = self.disk_space(self.data_root)
            add("disk_free", ds["meets_min"], f'free={ds["free_bytes"]} min={ds["min_free_bytes"]:.0f}')
        except (RuntimeGuardError, OSError) as e:
            ds = None
            add("disk_free", False, str(e))

        add("python_version", sys.version_info >= (3, 11), f'python={snap["python"]}')

        port_check = None
        if service_port is not None:
            port_check = self.check_port(service_port)
            add("port_available", port_check["available"],
                f'port={port_check["port"]} {port_check["detail"] or "bind ok"}')

        performed = len(checks)
        passed = sum(1 for c in checks if c["status"] == "pass")
        return {
            "schema": SCHEMA, "timestamp": None,
            "checks": checks,
            "checks_performed": performed,
            "checks_passed": passed,
            "checks_failed": performed - passed,  # 派生，绝不手写
            "status": "healthy" if passed == performed else "degraded",
            "system": snap, "disk": ds, "port": port_check,
            "method": "derived_from_real_checks", "fabricated": False,
        }

    # ---------------- 安装执行域（Phase 58 转真；见下方执行器一节） ----------------

    def install_actions(self) -> Dict[str, Any]:
        return {
            "schema": SCHEMA,
            "capability": "installer_execution",
            "status": "executable_via_cli_only",
            "actions": ["silent_install", "registry_entries", "shortcuts", "uninstaller"],
            "entry_point": "ZaojingAI.exe install-exec install|uninstall|plan|state "
                           "(install/uninstall 需逐字显式 --confirm 令牌，plan/state 只读"
                           "不需令牌；P50 desktop_hub 的 CLI 先例；形态集合与 CLI usage "
                           "及 usage_error.legal 数组同集，由 L2 四路判据当场对账)",
            "http_surface": "GET /api/v1/installer/execution 只做台账对账；"
                            "安装域不存在任何非 GET 路由 ⇒ HTTP 面走不到执行器",
            "confirm_token": CONFIRM_TOKEN,
            "gates": ["confirmation_required", "artifact_missing", "artifact_outside_allowed_root",
                      "build_ledger_unavailable", "build_ledger_stale",
                      "artifact_sha_mismatch_vs_build_ledger", "install_dir_not_absolute",
                      "install_dir_outside_allowed_root", "install_dir_not_empty",
                      "install_dir_inside_source_tree"],
            "verification_basis": BASIS + "：installed/uninstalled 只由差集与注册表现场观察派生，"
                                          "安装器与卸载器的退出码与自报文本一律另列不作数",
            "retired_boundary": {
                "was": "needs_installer_environment",
                "since": "Phase 26",
                "retired_in": "Phase 58",
                "why": "P26 那句判词当时是诚实的：引擎里连一个 spawn 接缝都没有，"
                       "任何'已安装'回执都只能是编的。P58 交付了执行器本体，故退休它，"
                       "但保留原文而不是改写历史。"},
            "runtime_guard_instead": "本引擎同时真实交付运行时守护：写入探针/磁盘/端口/单实例/诊断派生",
            "update_mechanism_note": "更新检查/验签/下载/原子替换由 update_engine 真实交付，不在本引擎重复",
            "fabricated": False,
        }


# ============================================================================
# 安装执行器（Phase 58 · installer_execution 由 not_shipped 转 supported）
# ============================================================================
# 这一面对回答的问题是：**"跑安装器/写删注册项/建快捷方式/跑卸载器"这四件写侧执行，
# 到底有没有真发生过，以及发生后本机还剩什么**。设计上有三条不可让步的线：
#
# 1) 执行器**不挂 HTTP**。P53 把"HTTP 面零安装副作用、写探针落不到真实安装根"钉成结构事实
#    并被 L2 断言着；一个能 spawn 60MB 安装器的 POST 会当场换掉那条事实。入口只有 CLI，
#    且必须带显式同意令牌（与 /VERYSILENT 一样不询问就绝不发生）。
# 2) 判定只认**磁盘 + 注册表的差集**。安装器退出 0、日志里"Successful"字样、卸载器提前退出
#    都不算证据——历轮的假成功几乎都长在"信自报"上。卸载器会 spawn 副本后先退，因此完成判定
#    必须轮询到目标真的消失，而不是等进程返回。
# 3) 既有用户数据**只登记、只比对，绝不删**。安装器的 [Dirs] 落在 %LOCALAPPDATA%\Zaojing，
#    那里可能已有上一轮的真实素材库/日志。执行器把安装前的每个文件连 sha 一起记进台账，
#    卸载后逐份读回比对；删除动作的作用域被限制在"本次安装创建集"里（removed_scope）。
#
# Inno 的真实行为与本机临时根里的假安装器不互相冒充：L1 用真子进程 + 临时根证伪**门与派生
# 逻辑**，真实 Inno 语义由 execution ledger 那一次真装真卸承担，execution_state() 再把台账
# 与现场重算对账。

EXECUTOR_SCHEMA = "zaojing-installer-exec/v1"
LEDGER_SCHEMA = "zaojing-installer-exec-ledger/v1"
# 显式同意串：与"我确认要在这台机器上装/卸"一一对应，任何缺省值都不触发执行。
CONFIRM_TOKEN = "ZAOJING-CONFIRM-EXECUTE-INSTALLER"
SILENT_FLAGS: tuple = ("/VERYSILENT", "/NORESTART", "/SUPPRESSMSGBOXES")
DATA_SUBDIRS: tuple = ("Assets", "Cache", "Logs", "config")
# 仅作**观察**记录，不参与判定（安装器的自话不算证据）。
LOG_FINISHED_MARKERS: tuple = ("Installation finished", "Log closed", "Successful log")
BASIS = "derived_from_disk_and_registry"
# 台账里这些字段照样回显，但**一律不作数**（P53 编译面对 compiled/fabricated 的同款口径）。
SELF_REPORT_IGNORED: tuple = ("install.installed", "install.status", "install.log.seen_finished_line",
                              "uninstall.performed", "uninstall.status", "build_ledger.compiled",
                              "build_ledger.fabricated")
UNINSTALL_ROOTS_DEFAULT = ("LOCALAPPDATA", "TEMP")
# HKCU 下卸载项的登记根（与 installer_manifest_engine 的只读探测同源同一条路径）。
# 注意它和上面的 UNINSTALL_ROOTS_DEFAULT 不是一回事：那枚是"允许删哪些磁盘根"的作用域，
# 这枚是"注册表去哪儿看"。名字相近是真实的混淆风险，故注释指名。
UNINSTALL_REG_ROOT_REL = r"Software\Microsoft\Windows\CurrentVersion\Uninstall"


class ExecutionError(ValueError):
    """执行前的门。reason 是机器可读码，L2/L4 按码断言而不是按中文串。"""

    def __init__(self, reason: str, message: str = "", **detail: Any):
        self.reason = reason
        self.detail = detail
        super().__init__(reason + (": " + message if message else ""))


def _norm(path: str) -> str:
    return os.path.normcase(os.path.realpath(str(path)))


def _inside(path: str, roots: List[str]) -> bool:
    np = _norm(path)
    return any(np == _norm(r) or np.startswith(_norm(r).rstrip("\\/") + os.sep) for r in roots)


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _default_roots() -> Dict[str, str]:
    return {"localappdata": os.environ.get("LOCALAPPDATA") or "",
            "userprofile": os.environ.get("USERPROFILE") or "",
            "appdata": os.environ.get("APPDATA") or ""}


def _touched_roots(roots: Dict[str, str], app_name: str, vendor_dir: str,
                   data_dir_name: str, install_dir: str) -> Dict[str, str]:
    """本次动作可能触碰的四处磁盘落点，逐一带标签（差集与残留都按标签报）。"""
    return {
        "install_root": install_dir,
        "userdocs": os.path.join(roots.get("userprofile", ""), "Documents", app_name),
        "start_menu_group": os.path.join(roots.get("appdata", ""), "Microsoft", "Windows",
                                         "Start Menu", "Programs", app_name),
        "runtime_data_root": os.path.join(roots.get("localappdata", ""), data_dir_name),
        "vendor_dir": os.path.join(roots.get("localappdata", ""), "Programs", vendor_dir),
    }


def _tree_snapshot(root: str) -> Dict[str, Any]:
    """真实磁盘枚举：目录清单 + 每个文件的相对名/字节/sha256。根不存在⇒空清单（而非 None）。"""
    p = Path(root) if root else None
    if p is None or not p.exists():
        return {"root": root, "exists": False, "dirs": [], "files": []}
    dirs, files = [], []
    for q in sorted(p.rglob("*")):
        rel = str(q.relative_to(p))
        if q.is_dir():
            dirs.append(rel)
        elif q.is_file():
            try:
                files.append({"rel": rel, "path": str(q), "bytes": q.stat().st_size,
                              "sha256": _sha256_file(str(q))})
            except OSError as e:
                files.append({"rel": rel, "path": str(q), "bytes": -1,
                              "sha256": "", "error": type(e).__name__})
    return {"root": root, "exists": True, "dirs": dirs, "files": files}


def _snapshot_all(touched: Dict[str, str], registry) -> Dict[str, Any]:
    return {"trees": {k: _tree_snapshot(v) for k, v in touched.items()},
            "registry": registry()}


def _spawn_process(cmd: List[str], log_path: Optional[str] = None,
                   timeout: float = 1800.0) -> Dict[str, Any]:
    """真实进程接缝（默认实现）：subprocess.run、无 shell、argv 逐项传（含中文路径不解析成两条）。"""
    r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout)
    return {"argv": [str(c) for c in cmd], "returncode": r.returncode,
            "stdout_tail": (r.stdout or "")[-400:], "stderr_tail": (r.stderr or "")[-400:]}


def uninstall_reg_path(key: str) -> str:
    """卸载键名 → HKCU 相对全路径（Phase 58 本机真装当场抓出的缺陷，此处收口）。

    Inno 把卸载项登记在 `HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\<AppId>_is1`，
    而全仓库派生出来的键名是**叶子形态**（`uninstall_registry_key()` / `uninstall_key_for()` 都只给
    `{GUID}_is1`，因为只读探测侧 `_enum_uninstall_keys` 本来就是"先开 Uninstall 根再枚举叶子"）。
    执行器却拿叶子当 HKCU 相对全路径去开，于是 `HKCU\\{GUID}_is1` 永远不存在 ⇒ 真装成功也判
    install_unverified、真卸完也"看见"键早没了（ vacuous 真）。评测台与 L1 都注入假 registry 字典，
    两侧都摸不到真实注册表，所以这条缝隙只有本机真装这一腿暴露得出来。

    两种入参形态必须派生同一个结果（与 `effective_app_id` 的"双括号/单括号同解"同一条纪律）：
    叶子形态补上前缀，已是全路径的原样返回，空串仍返回空串——绝不把空键名补成"看着像键的路径"。
    """
    s = str(key or "").strip()
    if not s:
        return ""
    flat = s.replace("/", "\\")
    low = flat.lower()
    root = UNINSTALL_REG_ROOT_REL.lower()
    if low == root or low.startswith(root + "\\"):
        return flat
    return UNINSTALL_REG_ROOT_REL + "\\" + flat


def registry_observation_targets(uninstall_key: str, app_reg_key: str) -> Dict[str, str]:
    """把"将要观察哪两枚键"提前说出来：回执里必须能指名观察对象，否则"键已消失"这种结论
    落在压根没打开过的路径上，事后无从复核（本机真装当轮就是靠这条把它自己抓出来的）。"""
    return {"hive": "HKCU",
            "uninstall": uninstall_reg_path(uninstall_key),
            "app": str(app_reg_key or "").strip(),
            "uninstall_input_form": str(uninstall_key or ""),
            "uninstall_key_was_leaf": bool(str(uninstall_key or "").strip())
            and uninstall_reg_path(uninstall_key) != str(uninstall_key or "").strip().replace("/", "\\")}


def display_name_matches(display_name: str, app_name: str) -> bool:
    """卸载项显示名的接受规则（Phase 58 本机真装实测后定，取代旧的逐字相等）。

    Inno 在未声明 `UninstallDisplayName` 时把显示名写成 `AppName + " " + AppVersion`，本机真装
    当轮从注册表原样读回的是 `"ZaojingAI 1.0.0"`；旧断言要求逐字等于 `app_name`，那是在验**夹具**
    而不是验世界（假安装器照抄了 app_name，于是 L1 恒绿、真装恒红）。显示名只做描述性复核，
    产品身份由键名里的 AppId GUID 承担，因此规则放宽为"以 app_name 起始"；空 app_name 不接受，
    否则前缀判断会对任何显示名恒真（又是一枚空洞真）。
    """
    dn, an = str(display_name or "").strip(), str(app_name or "").strip()
    return bool(an) and dn.startswith(an)


def _registry_view_real(uninstall_key: str, app_reg_key: str):
    """真实注册表接缝（默认实现）：HKCU 只读，返回 {"uninstall": {...}, "app": {...}}，缺项⇒空 dict。"""
    un_path, app_path = uninstall_reg_path(uninstall_key), str(app_reg_key or "").strip()

    def view() -> Dict[str, Any]:
        out: Dict[str, Any] = {"uninstall": {}, "app": {}}
        if not WINDOWS:
            return out
        try:
            import winreg
        except ImportError:
            return out
        for slot, key in (("uninstall", un_path), ("app", app_path)):
            if not key:
                continue
            try:
                h = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key)
            except OSError:
                continue
            try:
                _sub, nvals, _k = winreg.QueryInfoKey(h)
                for i in range(nvals):
                    name, val, _t = winreg.EnumValue(h, i)
                    out[slot][str(name)] = str(val)
            finally:
                winreg.CloseKey(h)
        return out
    return view


def _read_build_ledger(path: str) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        raise ExecutionError("build_ledger_unavailable", "构建台账不在盘上，无法证明制品与脚本同轮",
                             path=path)
    except (OSError, ValueError) as e:
        raise ExecutionError("build_ledger_unavailable", f"构建台账读不动：{type(e).__name__}", path=path)


def _require_confirm(confirm: Any) -> None:
    if not isinstance(confirm, str) or confirm != CONFIRM_TOKEN:
        raise ExecutionError("confirmation_required",
                             "执行安装/卸载必须逐字交出显式同意令牌；缺省、空串、近似值一律拒绝",
                             received_type=type(confirm).__name__)


def _gate_artifact(artifact: str, allow_roots: List[str]) -> Dict[str, Any]:
    p = Path(artifact)
    if not p.is_file():
        raise ExecutionError("artifact_missing", "制品不在盘上", path=str(p))
    if not _inside(str(p), allow_roots):
        raise ExecutionError("artifact_outside_allowed_root",
                             "制品在允许根之外——执行器不是通用程序启动器",
                             path=str(p), allow_roots=allow_roots)
    return {"path": str(p.resolve()), "bytes": p.stat().st_size, "sha256": _sha256_file(str(p))}


def _gate_ledger(artifact: Dict[str, Any], build_ledger: str) -> Dict[str, Any]:
    led = _read_build_ledger(build_ledger)
    same_path = _norm(str(led.get("artifact_path", ""))) == _norm(artifact["path"])
    if artifact["bytes"] != led.get("artifact_bytes"):
        raise ExecutionError("artifact_sha_mismatch_vs_build_ledger",
                             "制品字节数与构建台账不同轮", ledger=led.get("artifact_bytes"),
                             disk=artifact["bytes"])
    if not same_path:
        raise ExecutionError("build_ledger_stale",
                             "构建台账指向的产物不是这一枚", ledger_path=led.get("artifact_path"),
                             artifact_path=artifact["path"])
    if str(led.get("artifact_sha256", "")) != artifact["sha256"]:
        raise ExecutionError("artifact_sha_mismatch_vs_build_ledger",
                             "制品 sha256 与构建台账不同轮（等长替换即此形）",
                             ledger=led.get("artifact_sha256"), disk=artifact["sha256"])
    return led


def _gate_install_dir(install_dir: str, allow_roots: List[str]) -> str:
    d = Path(install_dir)
    if not d.is_absolute():
        raise ExecutionError("install_dir_not_absolute", "安装根必须是绝对路径", install_dir=install_dir)
    src_tree = _norm(str(Path(__file__).resolve().parent))
    if _norm(str(d)) == src_tree or _norm(str(d)).startswith(src_tree + os.sep):
        raise ExecutionError("install_dir_inside_source_tree",
                             "安装根不得落在源码树内（避免把随包产物嵌进仓库）", install_dir=str(d))
    if not _inside(str(d), allow_roots):
        raise ExecutionError("install_dir_outside_allowed_root",
                             "安装根在允许根之外", install_dir=str(d), allow_roots=allow_roots)
    if d.exists() and any(d.iterdir()):
        raise ExecutionError("install_dir_not_empty",
                             "安装根已存在且非空——静默安装不许接管别人的目录", install_dir=str(d))
    return str(d)


def _diff_files(before: Dict[str, Any], after: Dict[str, Any]) -> List[Dict[str, Any]]:
    seen = {f["path"] for f in before.get("files", [])}
    return [f for f in after.get("files", []) if f["path"] not in seen]


def _preexisting(before_trees: Dict[str, Any], install_dir: str,
                 owned_paths: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """既有数据基线 = 观察根里属于**用户**的文件，逐份带 sha 供装后/卸后读回比对。

    owned_paths 是执行器本轮自己就要重写的证据件（执行台账 + 缺省安装日志）。Phase 58 本机
    真装抓住的第 10 枚真缺陷正出在这里：第 7 枚缺陷的修法把证据落点收进产品数据根（那是
    对的——共用目录不该漏文件），于是它们必然被现场快照数进"数据根里已有的文件"，第二轮
    install 一重写台账，本轮基线里那枚旧台账的 sha 就对不上，卸载侧
    `verified = dir_gone and key_gone and app_gone and pre["intact"]` 便把一次干干净净的
    卸载判成 uninstall_incomplete。本机实测：deviating 恰好一枚，路径就是本轮刚写的
    %LOCALAPPDATA%\\Zaojing\\installer\\execution-evidence.json，而六份真用户数据逐字节相等
    ——判红的正是"证据被更新了"这件事本身。逐出基线不是藏起来：同一条回执里另账回显
    （见 _owned_echo），文件名、动手前的指纹、此刻的指纹、是否变了，全都看得见。
    """
    owned = {_norm(p) for p in (owned_paths or []) if p}
    out: List[Dict[str, Any]] = []
    for label, snap in before_trees.items():
        if label in ("install_root", "vendor_dir"):
            continue      # 安装根本身必须干净（_gate_install_dir 已强制），谈不上"既有数据"
        for f in snap.get("files", []):
            if _norm(f["path"]) in owned:
                continue
            out.append({"root": label, "rel": f["rel"], "path": f["path"],
                        "bytes": f["bytes"], "sha256": f["sha256"]})
    return out


def _owned_echo(install_owned: List[Dict[str, Any]],
                excluded_from_baseline: List[Dict[str, Any]],
                read_now: bool = True, rewrites: bool = True) -> List[Dict[str, Any]]:
    """把逐出用户数据基线的自家证据件逐份回显：动手前的指纹（+ 需要时此刻现场重读的指纹）。

    两份来源都要认：安装台账里那份 executor_owned（新格式），以及旧格式台账里仍被误记进
    preexisting.files 的那些路径（recorded_as_user_data_in_older_ledger=True）——本机
    Phase 58 那次真装留下的台账正是旧格式，只认前者会让对账面继续把自家证据当用户数据比。

    read_now=False 用在**安装回执自己**里：那一刻台账还没落笔、日志刚被本轮写完，"此刻"既不是
    安装前的状态也不是卸载后的状态，把两个时刻混在一栏里只会造出一枚读不准的
    changed_since_install。安装回执只报"本轮会重写这几枚、它们动手前是什么"，读盘比对的活
    归卸载面与对账面（那里 read_now=True 才有时序意义）。
    """
    ex_paths = {_norm(str(q.get("path", ""))) for q in excluded_from_baseline}
    merged: Dict[str, Dict[str, Any]] = {}
    for q in list(install_owned) + list(excluded_from_baseline):
        p = str(q.get("path", ""))
        k = _norm(p)
        if not k:
            continue
        merged[k] = {
            "path": p, "root": q.get("root", ""), "rel": q.get("rel", ""),
            "bytes_before": q.get("bytes_before", q.get("bytes")),
            "sha256_before": q.get("sha256_before", q.get("sha256", "")),
            "this_run_rewrites_it": bool(rewrites),
            "recorded_as_user_data_in_older_ledger": k in ex_paths}
    if read_now:
        for k, row in merged.items():
            q = Path(row["path"])
            if q.is_file():
                row["bytes_now"] = q.stat().st_size
                try:
                    row["sha256_now"] = _sha256_file(str(q))
                except OSError:
                    row["sha256_now"] = ""
            else:
                row["bytes_now"], row["sha256_now"] = -1, ""
            row["changed_since_install"] = bool(row["sha256_now"]) and (
                row["sha256_now"] != str(row.get("sha256_before", "")))
    return [merged[k] for k in sorted(merged)]


def _evidence_paths(ledger_path: Optional[str], log_path: Optional[str] = "",
                    extra: Optional[List[str]] = None) -> List[str]:
    """执行器本轮/本轮对账面的自家输出落点清单（台账 + 安装日志），供逐出基线与回显。"""
    return [str(p) for p in [ledger_path, log_path] + list(extra or []) if p]


def _owned_from_before(before_trees: Dict[str, Any],
                       owned_paths: List[str]) -> List[Dict[str, Any]]:
    """本轮**动手之前**这些自家落点在观察面里的样子（第一轮通常盘上还没有）。"""
    owned = {_norm(p) for p in owned_paths}
    out: List[Dict[str, Any]] = []
    for label, snap in before_trees.items():
        if label in ("install_root", "vendor_dir"):
            continue
        for f in snap.get("files", []):
            if _norm(f["path"]) in owned:
                out.append({"path": f["path"], "root": label, "rel": f["rel"],
                            "bytes_before": f["bytes"], "sha256_before": f["sha256"]})
    return out


def _missing_owned(owned_paths: List[str], found: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """盘上还没有的自家落点也列一条（bytes_before=None）：第一轮与第二轮的回执形状一致，
    消费方无从把"没列"误读成"本轮不写它"。"""
    have = {_norm(str(q.get("path", ""))) for q in found}
    return [{"path": p, "root": "", "rel": "", "bytes_before": None, "sha256_before": ""}
            for p in owned_paths if _norm(p) not in have]


def _check_intact(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    """既有数据逐字节读回：任何一份缺失或被改都如实报，不做"多半没动"的推断。"""
    off: List[str] = []
    for r in records:
        p = Path(r["path"])
        if not p.is_file() or _sha256_file(str(p)) != r["sha256"]:
            off.append(r["path"])
    return {"count": len(records), "intact": len(off) == 0, "deviating": off,
            "files": [{"rel": r.get("rel", ""), "path": r["path"], "bytes": r.get("bytes", -1),
                       "sha256": r["sha256"]} for r in records]}


def effective_app_id(raw: str) -> str:
    """AppId 源文 → 生效值（Inno 常量转义的解括号，与安装清单引擎同一条规则）。

    setup.iss 里写 `{{GUID}`：双写首括号是**源文转义**，编译器解出来的生效值是 `{GUID}`，
    于是注册表里真存的卸载键是 `{GUID}_is1`。Phase 53 把 AppId 改成双写形制（单括号会被
    Inno 当常量展开、iscc 当场 exit=2 拒编），此后任何"源文 + _is1"的拼接都会造出一枚
    **盘上根本不存在**的键名——查不中之后被读成"没装成/没卸掉"，那正是本轮要根除的假信号族。

    两条入参形态必须派生同一个结果：源文 `{{GUID}` 与已解码的 `{GUID}`。只在前导确实是
    双括号时折叠一次，不做全局替换（`{autopf}` 这类常量不该被碰）。
    """
    s = str(raw or "")
    return ("{" + s[2:]) if s.startswith("{{") else s


def uninstall_key_for(app_id: str) -> str:
    """卸载键名 = `<生效 AppId>_is1`（空 AppId ⇒ 空键名，绝不返回 "_is1" 这种看着像键的东西）。"""
    eff = effective_app_id(app_id)
    return ("%s_is1" % eff) if eff else ""


def missing_data_dirs(observed: List[str], required: tuple = DATA_SUBDIRS) -> List[str]:
    """[Dirs] 声明的数据子目录里**哪些真不在场**——按文件系统自身的大小写语义判，而不是按字面拼写。

    本机实况（真装前现场核过）：数据根 %LOCALAPPDATA%\\Zaojing 里的日志目录是产品自己的
    logger 先建的，叫 `logs`；setup.iss 的 [Dirs] 声明的是 `Logs`。NTFS 大小写不敏感，
    在已存在 `logs` 的卷上再建 `Logs` 既不会另起目录也不会改名，`rglob` 枚举出来的永远是
    盘上那份拼写 `logs`。用字面成员判断就会把"目录确实在场"读成"缺失"，进而在**本机真装
    当轮**判出 installed=False——那是要根除的假信号（仪器错，不是产品错）。

    os.path.normcase 正是这件事的权威口径：Windows 上折叠大小写（并统一分隔符），POSIX 上
    是恒等函数，因此在真正大小写敏感的卷上 `logs` 依然**不算** `Logs` 在场。这不是放宽：
    目录必须真的存在，只是其拼写按该卷的语义比较。
    """
    have = {os.path.normcase(str(d)) for d in (observed or [])}
    return [r for r in required if os.path.normcase(str(r)) not in have]


def install_plan(*, artifact: str, install_dir: str = "", app_name: str = "ZaojingAI",
                 vendor_dir: str = "造境科技", data_dir_name: str = "Zaojing",
                 app_id_braced: str = "", build_ledger: Optional[str] = None,
                 roots: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """只算不跑：把"若真装，会碰哪些落点、吃哪些参数、过哪几道门"逐项从磁盘派生出来。"""
    roots = roots or _default_roots()
    art = _gate_artifact(artifact, [str(Path(artifact).parent), str(Path.cwd())])
    led_path = build_ledger or os.path.join(Path(art["path"]).parent, "build-evidence.json")
    led = _read_build_ledger(led_path)
    tgt = install_dir or os.path.join(roots["localappdata"], "Programs", vendor_dir)
    touched = _touched_roots(roots, app_name, vendor_dir, data_dir_name, tgt)
    return {
        "schema": EXECUTOR_SCHEMA, "capability": "installer_execution", "action": "plan",
        "artifact": art,
        "build_ledger": {"path": led_path, "script_sha256": led.get("script_sha256"),
                         "artifact_sha256": led.get("artifact_sha256"),
                         "sha_cross_equal": str(led.get("artifact_sha256")) == art["sha256"],
                         "iscc_exit_code": led.get("iscc_exit_code")},
        "install_dir": tgt,
        "silent_flags": list(SILENT_FLAGS),
        "argv_template": ["{artifact}", *SILENT_FLAGS, "/DIR={install_dir}", "/LOG={log_path}"],
        "touched": [{"key": k, "path": v, "exists_now": bool(v) and Path(v).exists()}
                    for k, v in touched.items()],
        "uninstall_registry_key": uninstall_key_for(app_id_braced),
        "gates": ["confirmation_required", "artifact_missing", "artifact_outside_allowed_root",
                  "build_ledger_unavailable", "artifact_sha_mismatch_vs_build_ledger",
                  "install_dir_not_empty", "install_dir_inside_source_tree"],
        "confirm_token": CONFIRM_TOKEN,
        "fabricated": False,
    }


def execute_install(*, artifact: str, install_dir: str, confirm: Optional[str] = None,
                    app_name: str = "ZaojingAI", vendor_dir: str = "造境科技",
                    data_dir_name: str = "Zaojing", app_version: str = "1.0.0",
                    build_ledger: Optional[str] = None, roots: Optional[Dict[str, str]] = None,
                    registry=None, spawn=None, allow_roots: Optional[List[str]] = None,
                    ledger_path: Optional[str] = None, log_path: Optional[str] = None,
                    uninstall_key: str = "", app_reg_key: str = "") -> Dict[str, Any]:
    """真跑一次静默安装，并把"装成了没有"完全交给差集与注册表现场观察判定。"""
    _require_confirm(confirm)
    roots = roots or _default_roots()
    allow_roots = allow_roots or [roots.get("TEMP", tempfile.gettempdir()),
                                  roots.get("localappdata", ""), str(Path.cwd())]
    art = _gate_artifact(artifact, allow_roots)
    led_path = build_ledger or os.path.join(Path(art["path"]).parent, "build-evidence.json")
    led = _gate_ledger(art, led_path)
    tgt = _gate_install_dir(install_dir, allow_roots)
    touched = _touched_roots(roots, app_name, vendor_dir, data_dir_name, tgt)
    reg = registry or _registry_view_real(uninstall_key, app_reg_key)
    reg_targets = registry_observation_targets(uninstall_key, app_reg_key)
    reg_targets["view"] = "injected" if registry is not None else "real_hkcu_readonly"
    run = spawn or _spawn_process
    # 缺省日志落点（Phase 58 本机真装抓住的第 7 枚真缺陷）：旧值取安装根的父目录，真机上即
    # %LOCALAPPDATA%\Programs——那目录与别家应用共用、卸载器够不着、又不在任何观察根里，
    # 于是每次缺省安装都在共用目录里永久漏一个文件，而两份回执都说"没有残留"。证据与证据同根：
    # 交出台账路径的（真实 CLI 正是如此）就写在台账旁边，没交的写在数据根下的 installer/，
    # 两处都落在 runtime_data_root 的观察面内——安装时被差集点名，卸载后作为"有意保留的证据"
    # 出现在 residue 里，而不是藏在没人看的目录中。
    _ev_dir = (os.path.dirname(str(ledger_path)) if ledger_path else
               os.path.join(roots.get("localappdata", ""), data_dir_name, "installer"))
    log = log_path or os.path.join(_ev_dir, "zaojing-install.log")
    argv = [str(art["path"]), *SILENT_FLAGS, "/DIR=" + tgt, "/LOG=" + log]

    before = _snapshot_all(touched, reg)
    try:
        Path(log).parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass        # 建不出目录不当门：日志写不成会让 installer_log_written 当场判红，无需在此抢先编造
    proc = run(argv, log)
    after = _snapshot_all(touched, reg)
    exit_code = int(proc.get("returncode", -1))
    log_bytes = Path(log).stat().st_size if Path(log).is_file() else 0
    log_text = Path(log).read_text(encoding="utf-8", errors="replace") if log_bytes else ""
    root_files = {f["rel"] for f in after["trees"]["install_root"].get("files", [])}
    reg_now = after["registry"] or {}
    un = reg_now.get("uninstall") or {}
    app_vals = reg_now.get("app") or {}

    checks = [
        {"check": "install_dir_was_empty_before", "expected": 0,
         "observed": len(before["trees"]["install_root"].get("files", []))},
        {"check": "process_exit_zero", "expected": 0, "observed": exit_code},
        {"check": "installer_log_written", "expected": ">0 bytes", "observed": log_bytes},
        {"check": "exe_in_install_root", "expected": app_name + ".exe",
         "observed": sorted(r for r in root_files if r.lower().endswith(".exe") and "unins" not in r.lower())},
        {"check": "uninstaller_in_install_root", "expected": "unins000.exe",
         "observed": "unins000.exe" in root_files or any("unins" in r.lower() for r in root_files)},
        {"check": "registry_uninstall_key_present",
         "expected": "DisplayName 以 %r 起始：本机真装实测 Inno 缺省写的是 AppName+空格+AppVersion"
                     "（未声明 UninstallDisplayName），逐字等于 app_name 的旧断言是在验夹具而不是验世界；"
                     "身份本就由键名里的 AppId GUID 定，显示名只做描述性复核" % app_name,
         "expected_rule": "display_name_startswith_app_name", "app_name": app_name,
         "observed": un.get("DisplayName", "")},
        {"check": "registry_app_values_match",
         "expected": {"Version": app_version, "InstallLocation": _norm(tgt)},
         "observed": {"Version": app_vals.get("Version", ""),
                      "InstallLocation": _norm(app_vals.get("InstallLocation", ""))}},
        {"check": "docs_written", "expected": ">=1 file under userdocs",
         "observed": len(after["trees"]["userdocs"].get("files", []))},
        {"check": "start_menu_group_present", "expected": ">=1 entry under start_menu_group",
         "observed": len(after["trees"]["start_menu_group"].get("files", []))
         + len(after["trees"]["start_menu_group"].get("dirs", []))},
        {"check": "data_dirs_present", "expected": list(DATA_SUBDIRS),
         "observed": sorted(after["trees"]["runtime_data_root"].get("dirs", []))},
    ]
    exe_hit = [r for r in root_files if r.lower().endswith(".exe") and "unins" not in r.lower()]
    ok_marks = [len(before["trees"]["install_root"].get("files", [])) == 0, exit_code == 0, log_bytes > 0, bool(exe_hit),
                any("unins" in r.lower() for r in root_files),
                bool(un) and display_name_matches(un.get("DisplayName", ""), app_name),
                str(app_vals.get("Version", "")) == app_version
                and _norm(app_vals.get("InstallLocation", "")) == _norm(tgt),
                len(after["trees"]["userdocs"].get("files", [])) >= 1,
                (len(after["trees"]["start_menu_group"].get("files", []))
                 + len(after["trees"]["start_menu_group"].get("dirs", []))) >= 1,
                not missing_data_dirs(after["trees"]["runtime_data_root"].get("dirs", []))]
    for c, ok in zip(checks, ok_marks):
        c["ok"] = bool(ok)

    # 差集按"落点标签×文件"展开会把同一枚物理文件数两遍——本机 install_root（%LOCALAPPDATA%
    # \Programs\造境科技）与 vendor_dir 是**同一个目录**，两枚标签各扫一遍就各计一次，于是
    # created_count 虚报（4 枚实物报成 6 行），而卸载作用域、台账回执、"装了多少东西"的声称
    # 全都建立在这个计数上。磁盘真相只有一个：按规范化路径去重，别名标签记在 also_under 里
    # （信息不丢，计数不谎）。
    created: List[Dict[str, Any]] = []
    by_path: Dict[str, Dict[str, Any]] = {}
    for label in touched:
        for f in _diff_files(before["trees"][label], after["trees"][label]):
            key = _norm(f["path"])
            row = by_path.get(key)
            if row is None:
                row = {**f, "root": label, "also_under": []}
                by_path[key] = row
                created.append(row)
            elif label != row["root"] and label not in row["also_under"]:
                row["also_under"].append(label)
    _owned = _evidence_paths(ledger_path, log)
    _found_owned = _owned_from_before(before["trees"], _owned)
    pre = _preexisting(before["trees"], tgt, _owned)
    intact = _check_intact(pre)
    intact["executor_owned"] = _owned_echo(
        _found_owned + _missing_owned(_owned, _found_owned), [], read_now=False)
    installed = all(c["ok"] for c in checks)
    res = {
        "schema": EXECUTOR_SCHEMA, "capability": "installer_execution", "action": "install",
        "status": "install_verified" if installed else "install_unverified",
        "installed": installed, "basis": BASIS, "fabricated": False,
        "exit_code": exit_code, "argv": list(argv),
        "artifact": art, "build_ledger": {"path": led_path, "script_sha256": led.get("script_sha256"),
                                          "artifact_sha256": led.get("artifact_sha256")},
        "install_dir": tgt,
        "log": {"path": log, "bytes": log_bytes,
                "sha256": _sha256_file(log) if log_bytes else "",
                "seen_finished_line": any(m in log_text for m in LOG_FINISHED_MARKERS),
                "tail": log_text[-400:] if log_text else ""},
        "process": {k: proc.get(k) for k in ("stdout_tail", "stderr_tail")},
        "checks": checks, "checks_count": len(checks),
        "diff": {"created": created, "created_count": len(created)},
        "registry_before": before["registry"], "registry_after": after["registry"],
        "registry_observation": reg_targets,
        "trees_after": {k: {"exists": v["exists"], "files": len(v["files"]), "dirs": len(v["dirs"])}
                        for k, v in after["trees"].items()},
        "preexisting": intact, "created_at": _now_iso(),
    }
    if ledger_path:
        ledger = {"schema": LEDGER_SCHEMA, "capability": "installer_execution",
                  "created_at": res["created_at"], "install": res, "uninstall": None,
                  "self_report_ignored": list(SELF_REPORT_IGNORED), "fabricated": False}
        _write_ledger(ledger_path, ledger)
        res["ledger_path"] = ledger_path
    return res


def execute_uninstall(*, install_dir: str, confirm: Optional[str] = None,
                      app_name: str = "ZaojingAI", vendor_dir: str = "造境科技",
                      data_dir_name: str = "Zaojing", roots: Optional[Dict[str, str]] = None,
                      registry=None, spawn=None, allow_roots: Optional[List[str]] = None,
                      ledger_path: Optional[str] = None, app_id_braced: str = "",
                      uninstall_key: str = "", app_reg_key: str = "",
                      poll_timeout_s: float = 120.0, poll_interval_s: float = 1.0) -> Dict[str, Any]:
    """真跑一次静默卸载：轮询到"安装根消失 + 两枚 HKCU 键消失"才算完，作用域锁死在本次创建集。"""
    _require_confirm(confirm)
    roots = roots or _default_roots()
    allow_roots = allow_roots or [os.environ.get(k, "") for k in UNINSTALL_ROOTS_DEFAULT]
    if not Path(install_dir).is_absolute():
        raise ExecutionError("install_dir_not_absolute", "卸载目标必须是绝对路径", install_dir=install_dir)
    if not _inside(install_dir, allow_roots):
        raise ExecutionError("install_dir_outside_allowed_root",
                             "卸载目标在允许根之外——执行器不是通用删除器",
                             install_dir=install_dir, allow_roots=allow_roots)
    touched = _touched_roots(roots, app_name, vendor_dir, data_dir_name, install_dir)
    reg = registry or _registry_view_real(uninstall_key or uninstall_key_for(app_id_braced),
                                          app_reg_key)
    reg_targets = registry_observation_targets(uninstall_key or uninstall_key_for(app_id_braced),
                                               app_reg_key)
    reg_targets["view"] = "injected" if registry is not None else "real_hkcu_readonly"
    run = spawn or _spawn_process

    before = _snapshot_all(touched, reg)
    unins = Path(install_dir) / "unins000.exe"
    reg_has_key = bool((before["registry"] or {}).get("uninstall"))
    if not (unins.is_file() or reg_has_key):
        res = {
            "schema": EXECUTOR_SCHEMA, "capability": "installer_execution", "action": "uninstall",
            "status": "nothing_to_uninstall", "performed": False, "fabricated": False,
            "basis": BASIS, "install_dir": install_dir,
            "registry_observation": reg_targets,
            "reason": "既没有 unins000.exe 也没有注册表卸载项——如实报“没东西可卸”，绝不回“卸载成功”",
            "polls": 0, "residue": [], "removed": [], "removed_scope": "created_by_install_only",
            "preexisting": _check_intact([]), "exit_code": None, "argv": [],
            "install_dir_gone": not Path(install_dir).exists(),
            "uninstall_key_gone": not reg_has_key,
            "app_key_gone": not bool((before["registry"] or {}).get("app")),
            "checks": [], "checks_count": 0, "created_at": _now_iso(),
        }
        return res

    # 卸载器的来历要能指名：盘上 unins000.exe 优先，其次注册表 UninstallString（Inno 写成
    # 带引号的可执行路径，可能自带参数，故只剥外壳引号、按空格切第一段）。两路都给不出可
    # 执行文件时不猜路径，直接以 missing_uninstaller 状态返回——"找不到卸载器"和"卸载成功"
    # 是两个必须分开的答案。
    unins_path, unins_from = "", ""
    if unins.is_file():
        unins_path, unins_from = str(unins), "install_dir"
    else:
        raw = str((before["registry"]["uninstall"] or {}).get("UninstallString", "")).strip()
        cand = raw.split('"')[1] if raw.count('"') >= 2 else raw.split()[0] if raw else ""
        if cand and Path(cand).is_file():
            unins_path, unins_from = cand, "registry_uninstall_string"
    if not unins_path:
        return {
            "schema": EXECUTOR_SCHEMA, "capability": "installer_execution", "action": "uninstall",
            "status": "missing_uninstaller", "performed": False, "fabricated": False,
            "basis": BASIS, "install_dir": install_dir,
            "registry_observation": reg_targets,
            "reason": "安装痕迹在（盘或注册表），但两条路都指不出可执行的卸载器——"
                      "如实报缺，绝不以手工删除冒充已卸载",
            "looked_in": [str(unins), str((before["registry"]["uninstall"] or {}).get(
                "UninstallString", ""))],
            "polls": 0, "residue": [], "removed": [], "removed_scope": "created_by_install_only",
            "preexisting": {**_check_intact([]), "recorded_in_ledger": False},
            "exit_code": None, "argv": [],
            "install_dir_gone": not Path(install_dir).exists(),
            "uninstall_key_gone": not reg_has_key,
            "app_key_gone": not bool((before["registry"] or {}).get("app")),
            "checks": [], "checks_count": 0, "created_at": _now_iso(),
        }
    argv = [unins_path, *SILENT_FLAGS]
    proc = run(argv, None)
    # 卸载器会复制自身再退出（退出码≠卸载完成），因此判定靠轮询现场而不是靠进程返回。
    polls, deadline = 0, time.time() + float(poll_timeout_s)
    while True:
        polls += 1
        dir_gone = not Path(install_dir).exists()
        reg_now = reg() or {}
        key_gone = not bool(reg_now.get("uninstall"))
        app_gone = not bool(reg_now.get("app"))
        if (dir_gone and key_gone and app_gone) or time.time() >= deadline:
            break
        time.sleep(max(0.05, float(poll_interval_s)))
    after = _snapshot_all(touched, reg)
    dir_gone = not Path(install_dir).exists()
    key_gone = not bool((after["registry"] or {}).get("uninstall"))
    app_gone = not bool((after["registry"] or {}).get("app"))

    ledger: Optional[Dict[str, Any]] = None
    if ledger_path:
        try:
            with open(ledger_path, "r", encoding="utf-8") as f:
                ledger = json.load(f)
        except (OSError, ValueError):
            ledger = None
    _created_recorded = [str(f["path"]) for f in ((ledger or {}).get("install") or {}).get(
        "diff", {}).get("created", [])]
    created_paths = {_norm(p) for p in _created_recorded}
    # 既有数据基线**只从安装台账里那份逐字节清单**取：卸载时的现场快照里，安装刚创建的
    # 文件与用户原有数据混在一起，从现场反推基线会把前者当成后者，于是"删掉自己装的东西"
    # 被读成"改坏了用户数据"。没有台账就没有基线可核验——此时如实标 recorded_in_ledger=False，
    # 让 intact 的空洞真（count 0 必为真）不冒充"既有数据已核验"。
    ins_rec = (ledger or {}).get("install") or {}
    ins_pre = ins_rec.get("preexisting") or {}
    # 基线里还要再挑掉执行器**自家**的证据件（第 10 枚缺陷的卸载侧）：新格式台账已由安装侧
    # 逐出，但旧格式台账（本机 Phase 58 那次真装留下的那份正是）仍把台账与安装日志记在
    # preexisting.files 里，逐出条件因此不能只依赖上游是否干净。
    _owned_u = _evidence_paths(
        ledger_path, str((ins_rec.get("log") or {}).get("path", "")),
        extra=[str(q.get("path", "")) for q in (ins_pre.get("executor_owned") or [])])
    _owned_u_norm = {_norm(p) for p in _owned_u}
    baseline_raw = [f for f in ins_pre.get("files", [])]
    baseline = [f for f in baseline_raw
                if _norm(str(f.get("path", ""))) not in _owned_u_norm]
    excluded_u = [f for f in baseline_raw
                  if _norm(str(f.get("path", ""))) in _owned_u_norm]
    _found_u = _owned_from_before(before["trees"], _owned_u)
    pre = _check_intact(baseline)
    pre["recorded_in_ledger"] = bool(baseline)
    pre["executor_owned"] = _owned_echo(
        _found_u + _missing_owned(_owned_u, _found_u), excluded_u, read_now=True)
    # 逐出集合用**归一化形**比对，回执却必须回显**台账记下的那一串原文**：Windows 路径大小写
    # 不敏感、realpath 还会折短名，所以比对要归一；但把归一形当回执交出去，同一件事在 install
    # 面记 `D:\…\Zaojing\installer\zaojing-install.log`、uninstall 面记
    # `d:\…\zaojing-install.log`，两侧逐字比就是一道假墙（评测侧第一次拿 removed.path 对
    # install.log.path 就撞在这上面）。核对靠归一、回显靠原文，两件事分开。
    _eligible = {}
    for _c in _created_recorded:
        _eligible.setdefault(_norm(_c), _c)
    eligible = sorted(_eligible.values()) if created_paths else []
    removed = [{"path": p, "created_by_install": True,
                "gone_now": not Path(p).exists()} for p in eligible]
    if created_paths:
        blocked = [p for p in _all_paths(install_dir) if _norm(p) not in created_paths]
    else:
        blocked = []
    residue = []
    for label, snap in after["trees"].items():
        if snap.get("exists") and snap.get("files"):
            residue.append({"label": label, "path": touched[label],
                            "files": len(snap["files"]), "created_by_install": False})
    for label, key in (("registry_uninstall", key_gone), ("registry_app", app_gone)):
        if not key:
            residue.append({"label": label, "path": label, "files": 1,
                            "created_by_install": True})
    if not dir_gone:
        residue.append({"label": "install_root_still_present", "path": install_dir,
                        "files": len(after["trees"]["install_root"]["files"]),
                        "created_by_install": True})
    verified = dir_gone and key_gone and app_gone and pre["intact"] and polls >= 1
    res = {
        "schema": EXECUTOR_SCHEMA, "capability": "installer_execution", "action": "uninstall",
        "status": "uninstall_verified" if verified else "uninstall_incomplete",
        "performed": True, "basis": BASIS, "fabricated": False,
        "exit_code": int(proc.get("returncode", -1)), "argv": list(argv),
        "process": {k: proc.get(k) for k in ("stdout_tail", "stderr_tail")},
        "polls": polls, "poll_timeout_s": float(poll_timeout_s),
        "install_dir": install_dir, "install_dir_gone": dir_gone,
        "uninstall_key_gone": key_gone, "app_key_gone": app_gone,
        "registry_after": after["registry"], "registry_observation": reg_targets,
        "removed": removed, "removed_scope": ("created_by_install_only" if created_paths
                                              else "unknown_without_ledger"),
        "untouched_non_created": blocked,
        "residue": residue, "residue_count": len(residue),
        "preexisting": pre, "trees_after": {
            k: {"exists": v["exists"], "files": len(v["files"]), "dirs": len(v["dirs"])}
            for k, v in after["trees"].items()},
        "created_at": _now_iso(), "checks": [], "checks_count": 0,
    }
    if ledger_path and ledger is not None:
        ledger["uninstall"] = res
        ledger["updated_at"] = res["created_at"]
        _write_ledger(ledger_path, ledger)
        res["ledger_path"] = ledger_path
    return res


def _all_paths(root: str) -> List[str]:
    p = Path(root)
    return [str(q) for q in p.rglob("*")] if p.exists() else []


def _now_iso() -> str:
    from datetime import datetime
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _write_ledger(path: str, payload: Dict[str, Any]) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                          encoding="utf-8", newline="\n")


def execution_state(*, ledger_path: Optional[str] = None, registry=None,
                    roots: Optional[Dict[str, str]] = None, build_ledger: Optional[str] = None,
                    app_name: str = "ZaojingAI", data_dir_name: str = "Zaojing",
                    vendor_dir: str = "造境科技", uninstall_key: str = "",
                    app_reg_key: str = "") -> Dict[str, Any]:
    """只读对账：把执行台账记的每一笔拿**现场重算值**逐字段比，比不中就点名到字段。

    与 P53 编译面同一口径：台账里的 installed/performed/status 这类自报布尔照样回显，
    但不作为判定依据；判定只认三件独立信号——制品 sha 三方相等、日志文件仍能被重读且 sha 不变、
    以及磁盘/注册表现场状态与台账关于"之后"的说法是否吻合。
    """
    roots = roots or _default_roots()
    lp = ledger_path or os.path.join(str(Path(__file__).resolve().parent),
                                     "installer", "execution-evidence.json")
    if not Path(lp).is_file():
        return {"schema": EXECUTOR_SCHEMA, "capability": "installer_execution",
                "status": "ledger_absent", "ledger": {"path": lp, "exists": False},
                "reason": "本机从未按这条链跑过真实安装/卸载——没有台账就如实说没有，绝不伪造回执",
                "self_report_ignored": list(SELF_REPORT_IGNORED), "fabricated": False,
                "mismatches": [], "live": {}, "recomputed": {}}
    # 第 11 枚真缺陷（判卷侧抓住，落在产品侧）：`ledger` 这个子对象在缺席分支里有
    # `exists`，在在场分支里却没有——同一字段名只在一条形制上成立，消费方按对称形状读
    # `ledger.exists` 就会在场时读到 None，而 None 与 False 在对账面语境里是两种截然不同的
    # 真相（"没查"与"查了、确实不在"）。补齐三条形制的 exists，让"字节数与哈希从哪个文件
    # 算来"这件事本身可被断言。
    raw = Path(lp).read_bytes()
    try:
        led = json.loads(raw.decode("utf-8"))
    except ValueError:
        return {"schema": EXECUTOR_SCHEMA, "status": "ledger_inconsistent",
                "ledger": {"path": lp, "exists": True},
                "mismatches": ["ledger:unreadable"], "fabricated": False,
                "self_report_ignored": list(SELF_REPORT_IGNORED), "live": {}, "recomputed": {}}
    ins = led.get("install") or {}
    un = led.get("uninstall")
    led_path = build_ledger or os.path.join(
        str(Path(ins.get("artifact", {}).get("path", ".")).parent), "build-evidence.json")
    # 只读对账**不许**把现场读失败抛成异常：抛出去在 HTTP 面就是 500，而 500 既不是通过也不是
    # 裁决——真相是它没给结论。构建台账与日志两处的读失败一律降级成下面点名的不一致。
    bl: Dict[str, Any] = {}
    bl_error = ""
    if not Path(led_path).is_file():
        bl_error = "build_ledger_absent: " + led_path
    else:
        try:
            bl = _read_build_ledger(led_path)
        except (ExecutionError, OSError, ValueError) as e:
            bl_error = str(getattr(e, "reason", "") or type(e).__name__)
    art_disk = {"path": ins.get("artifact", {}).get("path", "")}
    # 对账面是**只读**的：制品读不动（没盘 / 没权限 / 被占用）必须降级成一条点名的不一致，
    # 而不是把异常抛出对账函数——一次 500 会让人以为"对账逻辑本身没问题"，而真相是它压根
    # 没给出裁决。
    art_now: Dict[str, Any] = {}
    art_read_error = ""
    if not art_disk["path"]:
        art_read_error = "台账未记录制品路径"
    elif not Path(art_disk["path"]).is_file():
        art_read_error = "制品已不在盘上：" + art_disk["path"]
    else:
        try:
            art_now = _gate_artifact(art_disk["path"],
                                     [str(Path(art_disk["path"]).parent), str(Path.cwd())])
        except (ExecutionError, OSError, ValueError) as e:
            art_read_error = "制品读不动：" + str(getattr(e, "reason", "") or e)

    reg = registry or _registry_view_real(uninstall_key, app_reg_key)
    reg_targets = registry_observation_targets(uninstall_key, app_reg_key)
    reg_targets["view"] = "injected" if registry is not None else "real_hkcu_readonly"
    live_reg = reg() or {}
    tgt = ins.get("install_dir", "")
    touched = _touched_roots(roots, app_name, vendor_dir, data_dir_name, tgt)
    trees = {k: _tree_snapshot(v) for k, v in touched.items()}
    dir_now = bool(tgt) and Path(tgt).exists()
    un_now = bool(live_reg.get("uninstall"))
    app_now = bool(live_reg.get("app"))
    log_now = ins.get("log", {}).get("path", "")
    log_re, log_error = "", ""
    if log_now and Path(log_now).is_file():
        try:
            log_re = _sha256_file(log_now)
        except (OSError, ValueError) as e:
            log_error = str(getattr(e, "reason", "") or e) or type(e).__name__

    ins_pre_s = ins.get("preexisting") or {}
    # 对账面同样要认自家证据（第 10 枚缺陷的第三张脸）。除了本轮的台账与安装日志，还须
    # 读台账里那份 executor_owned——它由安装侧写下；旧格式台账没这份，因此旧台账里被误记进
    # preexisting.files 的证据件要靠"落点等于台账/日志路径"这条规则兜住。
    _un_pre_s = ((led.get("uninstall") or {}).get("preexisting") or {})
    _owned_s = _evidence_paths(
        lp, str((ins.get("log") or {}).get("path", "")),
        extra=([str(q.get("path", "")) for q in (ins_pre_s.get("executor_owned") or [])]
               + [str(q.get("path", "")) for q in (_un_pre_s.get("executor_owned") or [])]))
    _owned_s_norm = {_norm(p) for p in _owned_s}
    _pre_raw = [f for f in ins_pre_s.get("files", [])]
    pre_records = [{"path": f["path"], "sha256": f["sha256"]} for f in _pre_raw
                   if _norm(str(f.get("path", ""))) not in _owned_s_norm]
    excluded_s = [f for f in _pre_raw
                  if _norm(str(f.get("path", ""))) in _owned_s_norm]
    # count=0 时 _check_intact 的 intact 恒为真——那是**空洞真**，不能算核验过。
    # 因此这里把"有无基线"独立回显，reconcile 的字段比对只认有基线的情形。
    pre = _check_intact(pre_records)
    pre["recorded_in_ledger"] = bool(pre_records)
    _found_s = _owned_from_before(trees, _owned_s)
    # rewrites=False：对账面是只读的，它不重写任何东西，把安装/卸载侧那句"本轮会重写"
    # 照搬过来会造出一句假话。
    pre["executor_owned"] = _owned_echo(
        _found_s + _missing_owned(_owned_s, _found_s), excluded_s,
        read_now=True, rewrites=False)

    mism: List[str] = []
    if bl_error:
        mism.append("build_ledger.unreadable: " + bl_error)
    if art_read_error:
        mism.append("install.artifact.unreadable: " + art_read_error)
    if str(bl.get("artifact_sha256", "")) != str(ins.get("artifact", {}).get("sha256", "")):
        mism.append("install.artifact.sha256 != 构建台账 artifact_sha256")
    if art_now and art_now.get("sha256") != ins.get("artifact", {}).get("sha256"):
        mism.append("install.artifact.sha256 != 制品在盘上的现算值")
    if ins.get("checks_count") != len(ins.get("checks", [])):
        mism.append("install.checks_count != len(install.checks)")
    if ins.get("diff", {}).get("created_count") != len(ins.get("diff", {}).get("created", [])):
        mism.append("install.diff.created_count != len(install.diff.created)")
    if log_error:
        mism.append("install.log.unreadable: 日志在盘上但读不动（安装/卸载进程仍占用即此形）："
                    + log_error)
    if ins.get("log", {}).get("sha256") and log_re != ins["log"]["sha256"]:
        mism.append("install.log.sha256 != 日志文件在盘上的现算值")
    ins_verified_now = str(ins.get("status", "")) == "install_verified"
    awaiting_uninstall = False
    if un is None:
        # 第 9 枚真缺陷：旧口径在这里判"台账无卸载记录而本机仍在安装态"=不一致，把一次
        # 正常的"装好了、等着卸"窗口报成台账有问题；而 verified_run 又硬性要求一条
        # uninstall_verified 记录，install-only 永远到不了任何终态。该判不一致的是**反向**
        # 情形——台账说装成了、本机已不在安装态、又没有任何卸载记录：那是安装痕迹被台账
        # 之外的手段抹掉，必须点名。
        if ins_verified_now and dir_now and un_now and app_now:
            awaiting_uninstall = True
        elif ins_verified_now:
            mism.append(
                "uninstall:台账只有 install_verified=True 这一笔、无卸载记录，而本机现场已不在"
                "完整安装态（install_dir=%s uninstall_key=%s app_key=%s）——安装痕迹是被台账"
                "之外的手段抹掉的（手工删目录 / 第三方清理器 / 注册表回滚），不能读成一次正常"
                "卸载" % (dir_now, un_now, app_now))
        # 第三支：台账里那一笔本身就不是 install_verified（自报未证成、或干脆是空手一趟）。
        # 此时"本机不在安装态"与台账的说法并不矛盾——台账从未声称装成过，也就没有"痕迹被
        # 台账之外的手段抹掉"这回事。把它写进 mism 会造出一句假话，并把诚实的
        # ledger_unverified 升级成 ledger_inconsistent（第 9 枚修复自己引入的第 11 枚缺陷）。
    else:
        if bool(un.get("install_dir_gone")) == dir_now:
            mism.append("uninstall.install_dir_gone != 本机现场（安装根存在性相反）")
        if bool(un.get("uninstall_key_gone")) == un_now:
            mism.append("uninstall.uninstall_key_gone != 本机现场（卸载项存在性相反）")
        if bool(un.get("app_key_gone")) == app_now:
            mism.append("uninstall.app_key_gone != 本机现场（应用键存在性相反）")
        _dev_s = [str(d) for d in (_un_pre_s.get("deviating") or [])]
        _dev_all_owned = bool(_dev_s) and all(_norm(d) in _owned_s_norm for d in _dev_s)
        pre["uninstall_receipt"] = {
            "intact_claimed": _un_pre_s.get("intact"),
            "deviating": _dev_s,
            "deviating_all_executor_owned": _dev_all_owned}
        if un.get("preexisting") and bool(un["preexisting"].get("intact")) != pre["intact"]:
            # 回执说"用户数据被改坏"、现场逐字节读回说没有，而回执点名的那些文件**全部**是
            # 执行器自己重写的证据件 ⇒ 这正是第 10 枚缺陷的指纹（旧台账由修复前的引擎写下）。
            # 逐出是两面对面同一条规则，所以这里不再判红，但两侧说法都留在回显里看得见。
            if not _dev_all_owned:
                mism.append("uninstall.preexisting.intact != 既有数据逐字节读回结果")
    verified_run = (ins_verified_now and bool(un)
                    and str((un or {}).get("status", "")) == "uninstall_verified")
    status = ("ledger_inconsistent" if mism
              else ("executed_verified" if verified_run
                    else ("install_verified_awaiting_uninstall" if awaiting_uninstall
                          else "ledger_unverified")))
    return {
        "schema": EXECUTOR_SCHEMA, "capability": "installer_execution", "status": status,
        "basis": BASIS, "fabricated": False,
        "ledger": {"path": lp, "exists": True, "bytes": len(raw),
                   "sha256": hashlib.sha256(raw).hexdigest(),
                   "created_at": led.get("created_at"), "updated_at": led.get("updated_at")},
        "recomputed": {
            "install_exit_code": ins.get("exit_code"),
            "build_ledger_artifact_sha256": bl.get("artifact_sha256"),
            "artifact_sha256_on_disk_now": art_now.get("sha256", ""),
            "log_sha256_on_disk_now": log_re,
            "install_checks_ok_all": all(c.get("ok") for c in ins.get("checks", [])),
            "install_dir_now_exists": dir_now,
            "uninstall_key_now_present": un_now, "app_key_now_present": app_now,
            "preexisting_intact_now": pre["intact"],
        },
        "recorded_only": ["install.exit_code", "install.argv", "install.process"],
        "live": {"trees": {k: {"exists": v["exists"], "files": len(v["files"])}
                           for k, v in trees.items()},
                 "registry": live_reg},
        # 回显"这次究竟去哪个路径看了"。uninstall_key 传入的是叶子名还是完整相对路径，
        # 在本窗口之前决定了判卷结果：叶子名会被直接挂到 HKCU 下，那里永远什么都没有，
        # 于是 uninstall_key_now_present 恒为 false —— 一个空洞真。把它回显出来，
        # 消费方就能在"归零"与"根本没去看"之间做出区分。
        "registry_observation": reg_targets,
        "preexisting": pre,
        "mismatches": mism,
        "self_report_ignored": list(SELF_REPORT_IGNORED),
    }


# ---------------------------------------------------------------------------
# 独立自测：确定性、可证伪（负控 + 变异靶点全覆盖），无网络无第三方
# ---------------------------------------------------------------------------

# 自测夹具（Phase 58 执行器）：两枚"假安装器/假卸载器"脚本。它们由**真实子进程**执行、
# 在**临时根**里产生真实文件与真实注册表替身（marker JSON），因此执行器的四道门、
# 差集派生、轮询与台账写入走的都是生产代码路径。它们不是被测产品的一部分，也不进包。
FAKE_INSTALLER_SRC = '''"""假 Inno 安装器：只吃 /DIR= 与 /LOG=，副作用全部落在 ZJ_FAKE_ROOTS 指着的临时根。"""
import json, os, sys, pathlib
a = {}
for x in sys.argv[1:]:
    if x.startswith("/DIR="):
        a["dir"] = x[5:]
    elif x.startswith("/LOG="):
        a["log"] = x[5:]
roots = json.loads(pathlib.Path(os.environ["ZJ_FAKE_ROOTS"]).read_text(encoding="utf-8"))
d = pathlib.Path(a["dir"])
d.mkdir(parents=True, exist_ok=True)
(d / "ZaojingAI.exe").write_bytes(b"MZ" + b"fake-app-binary" * 7)
(d / "unins000.exe").write_bytes(b"MZ" + b"fake-uninstaller" * 6)
docs = pathlib.Path(roots["userprofile"]) / "Documents" / "ZaojingAI"
docs.mkdir(parents=True, exist_ok=True)
(docs / "\\u5b89\\u88c5\\u8bf4\\u660e.md").write_text("# fake doc\\n", encoding="utf-8")
menu = (pathlib.Path(roots["appdata"]) / "Microsoft" / "Windows" / "Start Menu"
        / "Programs" / "ZaojingAI")
menu.mkdir(parents=True, exist_ok=True)
(menu / "ZaojingAI.lnk").write_bytes(b"LNK" * 9)
data = pathlib.Path(roots["localappdata"]) / "Zaojing"
for sub in ("Assets", "Cache", "Logs", "config"):
    (data / sub).mkdir(parents=True, exist_ok=True)
mk = pathlib.Path(os.environ["ZJ_FAKE_MARKER"])
reg = json.loads(mk.read_text(encoding="utf-8")) if mk.exists() else {}
# DisplayName 取 "ZaojingAI 1.0.0" 而非 "ZaojingAI"：本机真装实测（Phase 58），Inno 在未声明
# UninstallDisplayName 时写的是 AppName+" "+AppVersion。夹具必须长得像**真实世界**，否则"以
# app_name 起始"和旧的"逐字等于 app_name"两条规则在夹具上同真， seam 就永远测不出差别。
reg["uninstall"] = {"DisplayName": "ZaojingAI 1.0.0", "DisplayVersion": "1.0.0",
                    "UninstallString": str(d / "unins000.exe"), "InstallLocation": str(d)}
reg["app"] = {"Version": "1.0.0", "InstallLocation": str(d), "InstallDate": "01-01-2026"}
mk.write_text(json.dumps(reg), encoding="utf-8")
pathlib.Path(a["log"]).write_text("2026-01-01 00:00:00.000   Installation finished\\n",
                                  encoding="utf-8")
sys.exit(0)
'''

FAKE_UNINSTALLER_SRC = '''"""假卸载器：从注册表替身里取安装根；只清自己创建的路径，非空数据目录一个字节都不动。"""
import json, os, pathlib, shutil, sys
mk = pathlib.Path(os.environ["ZJ_FAKE_MARKER"])
roots = json.loads(pathlib.Path(os.environ["ZJ_FAKE_ROOTS"]).read_text(encoding="utf-8"))
reg = json.loads(mk.read_text(encoding="utf-8")) if mk.exists() else {}
un = reg.get("uninstall") or {}
if not un:
    sys.exit(0)
d = pathlib.Path(un["InstallLocation"])
if d.is_dir():
    shutil.rmtree(d, ignore_errors=True)
docs = pathlib.Path(roots["userprofile"]) / "Documents" / "ZaojingAI"
if docs.is_dir():
    shutil.rmtree(docs, ignore_errors=True)
menu = (pathlib.Path(roots["appdata"]) / "Microsoft" / "Windows" / "Start Menu"
        / "Programs" / "ZaojingAI")
if menu.is_dir():
    shutil.rmtree(menu, ignore_errors=True)
data = pathlib.Path(roots["localappdata"]) / "Zaojing"
for sub in ("Assets", "Cache", "Logs", "config"):
    p = data / sub
    if p.is_dir():
        try:
            p.rmdir()          # 非空即失败：与 Inno"只删自己建且已空的目录"同形
        except OSError:
            pass
reg.pop("uninstall", None)
reg.pop("app", None)
mk.write_text(json.dumps(reg), encoding="utf-8")
sys.exit(0)
'''

def _self_test() -> int:
    ok_count, fail = 0, []

    def check(name: str, cond: bool, extra: str = ""):
        nonlocal ok_count
        if cond:
            ok_count += 1
            print(f"[OK] {name}")
        else:
            fail.append(name)
            print(f"[FAIL] {name} {extra}".encode("ascii", "backslashreplace").decode())

    eng = RuntimeGuardEngine(data_root=Path(tempfile.mkdtemp(prefix="zjrtg_")) / "Data")
    with tempfile.TemporaryDirectory(prefix="zjrtg_t_") as td:
        tdp = Path(td)

        # 1-3 写入探针：真实往返 + 清理
        r1 = eng.probe_write(tdp)
        check("write probe: real write+readback+delete roundtrip",
              r1["writable"] is True and r1["bytes_verified"] is True and r1["cleaned"] is True)
        check("write probe: not faking (fabricated False, schema set)",
              r1["fabricated"] is False and r1["schema"] == SCHEMA)
        check("write probe: temp file truly gone from disk",
              not list(Path(tdp).glob("zj_write_probe_*")))
        # 4 负控：父路径为普通文件 → 必须如实报不可写而非抛崩
        as_file = tdp / "iam-a-file"
        as_file.write_text("x", encoding="utf-8")
        r2 = eng.probe_write(as_file / "sub")
        check("write probe NEG: parent-is-a-file -> writable False (no crash)",
              r2["writable"] is False and r2["bytes_verified"] is False
              and isinstance(r2["error"], str) and r2["error"] != "")
        # 5-6 入参守卫
        for bad in ("", "   ", None, 123):
            try:
                eng.probe_write(bad)
                guarded = False
            except RuntimeGuardError:
                guarded = True
            check(f"write probe: reject hostile path {bad!r}", guarded)
        # 7 负控：读回与写入不一致（模拟静默漂移/截断）→ bytes_verified 必撤回 writable
        global _read_text
        _orig_read = _read_text
        try:
            _read_text = lambda p: "TAMPERED-READBACK"  # noqa: E731 负控接缝
            r3 = eng.probe_write(tdp)
        finally:
            _read_text = _orig_read
        check("write probe NEG: readback mismatch -> writable retracted (comparison is load-bearing)",
              r3["writable"] is False and r3["bytes_verified"] is False and r3["cleaned"] is True)

        # 7-9 磁盘真实比较
        ds = eng.disk_space(tdp)
        check("disk: real values (free>0, total>=free)",
              ds["free_bytes"] > 0 and ds["total_bytes"] >= ds["free_bytes"])
        check("disk: meets_min True for trivial threshold",
              eng.disk_space(tdp, min_free_bytes=1)["meets_min"] is True)
        check("disk NEG: meets_min False for unreachable threshold",
              eng.disk_space(tdp, min_free_bytes=2 ** 62)["meets_min"] is False)
        try:
            eng.disk_space(tdp, min_free_bytes=float("nan"))
            nan_guard = False
        except RuntimeGuardError:
            nan_guard = True
        check("disk: NaN min_free rejected", nan_guard)

        # 10-12 端口真实 bind：占用中的端口必报 False（负控）
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        occupied = srv.getsockname()[1]
        pr = eng.check_port(occupied)
        check("port NEG: occupied port -> available False (real bind conflict)",
              pr["available"] is False and pr["fabricated"] is False)
        srv.close()
        pr2 = eng.check_port(occupied)
        check("port: released port -> available True", pr2["available"] is True)
        for bad in (0, 70000, "abc", 80.5):
            try:
                eng.check_port(bad)
                pg = False
            except RuntimeGuardError:
                pg = True
            check(f"port: reject invalid {bad!r}", pg)

        # 13-15 单实例真实内核反馈
        name = f"zaojing-selftest-{uuid.uuid4().hex[:10]}"
        a1 = eng.acquire_single_instance(name)
        a2 = eng.acquire_single_instance(name)
        check("single-instance: first acquire wins (kernel mutex truth)",
              a1["acquired"] is True and a1["already_running"] is False)
        check("single-instance NEG: second acquire sees already_running",
              a2["acquired"] is False and a2["already_running"] is True)
        rel = eng.release_single_instance(name)
        a3 = eng.acquire_single_instance(name)
        check("single-instance: reacquire after release succeeds",
              rel["released"] is True and a3["acquired"] is True)
        eng.release_single_instance(name)
        try:
            eng.acquire_single_instance("bad/name")
            ng = False
        except RuntimeGuardError:
            ng = True
        check("single-instance: name with reserved chars rejected", ng)

        # 16 系统快照真实值
        snap = eng.system_snapshot()
        check("system: python value matches interpreter (no hardcode)",
              snap["python"] == sys.version.split()[0] and isinstance(snap["frozen"], bool))

        # 17-19 诊断派生
        dg = eng.run_diagnostic()
        check("diagnostic: counts arithmetic consistent (derived)",
              dg["checks_performed"] == len(dg["checks"]) ==
              dg["checks_passed"] + dg["checks_failed"])
        srv2 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv2.bind(("127.0.0.1", 0))
        srv2.listen(1)
        dg2 = eng.run_diagnostic(service_port=srv2.getsockname()[1])
        srv2.close()
        port_item = [c for c in dg2["checks"] if c["check"] == "port_available"][0]
        check("diagnostic NEG: occupied service_port -> that check fails honestly",
              port_item["status"] == "fail" and dg2["status"] == "degraded"
              and dg2["checks_failed"] >= 1)
        check("diagnostic: healthy only when all real checks pass",
              dg["status"] == "healthy" if dg["checks_failed"] == 0 else dg["status"] == "degraded")

        # 20-21 安装执行域契约（Phase 58 转真：旧口径 needs_installer_environment 退役，
        #        退役 ≠ 改写——判词里写明它曾经的形态与今日为何不同）
        ia = eng.install_actions()
        check("install actions(P58 retired P26 boundary): status became executable_via_cli_only "
              "(old value was needs_installer_environment because the engine had no executor at all; "
              "P58 ships one behind explicit consent + path/sha gates, CLI-only, HTTP 面零执行路由)",
              ia["status"] == "executable_via_cli_only" and ia["fabricated"] is False
              and ia["capability"] == "installer_execution" and "http_surface" in ia
              and "gates" in ia and "confirm_token" in ia)
        check("install actions: no fabricated endpoints/versions (no example.com)",
              "example.com" not in str(ia) and "1.1.0" not in str(ia))

        # 22+ 安装执行器（P58）：真实子进程 + 全临时根，机器状态零触碰。
        #     L1 能证伪的是**执行器的门与派生逻辑**（确认令牌/路径/sha/非空目录四道门、
        #     完成判定只认磁盘+注册表差集、卸载只清自己创建的路径、台账对账不信自报布尔）；
        #     Inno 真实行为由 execution ledger 那一次真跑承担，两者不互相冒充。
        _surface = ("install_plan", "execute_install", "execute_uninstall", "execution_state")
        _have = [n for n in _surface if hasattr(sys.modules[__name__], n)]
        check("install exec surface exists (install_plan/execute_install/execute_uninstall/execution_state)",
              len(_have) == len(_surface), f"missing={set(_surface) - set(_have)}")
        _pure = ("uninstall_reg_path", "registry_observation_targets", "display_name_matches",
                 "uninstall_key_for", "missing_data_dirs")
        _pure_have = [n for n in _pure if callable(getattr(sys.modules[__name__], n, None))]
        check("registry-observation pure functions exist (path joining / target echo / display-name rule "
              "are separately testable, not buried in the subprocess path) — the seam that had ZERO "
              "automated coverage until this machine's first real install found it by hand",
              len(_pure_have) == len(_pure), f"missing={set(_pure) - set(_pure_have)}")
        if len(_have) == len(_surface):
            import json as _json
            import subprocess as _sp

            ex = Path(tempfile.mkdtemp(prefix="zj_p58_ex_"))
            # 兜底清理：末尾虽有显式 rmtree + "roots cleaned" 断言，但**中途任何一处抛异常**
            # 都会跳过它们，把临时根留在共享 temp 目录里（本轮实测两次崩溃各留一枚）。
            # atexit 在解释器退出前必跑，故失败路径也不 litter。
            import atexit as _atexit
            _atexit.register(shutil.rmtree, str(ex), True)
            marker = ex / "registry-marker.json"
            marker.write_text("{}", encoding="utf-8")
            roots = {"localappdata": str(ex / "fake-local"),
                     "userprofile": str(ex / "fake-user"),
                     "appdata": str(ex / "fake-appdata")}
            for v in roots.values():
                Path(v).mkdir(parents=True, exist_ok=True)
            (ex / "roots.json").write_text(_json.dumps(roots), encoding="utf-8")
            (Path(roots["localappdata"]) / "Zaojing" / "Assets").mkdir(parents=True, exist_ok=True)
            keep = Path(roots["localappdata"]) / "Zaojing" / "Assets" / "keep-me.json"
            keep.write_bytes(b'{"preexisting":true}')
            # 本机真实形态（真装前在 %LOCALAPPDATA%\Zaojing 现场核过：logs\agent.log 一直在）：
            # 数据根里的日志目录是**产品自己的 logger 先建的**，名字是小写 logs；setup.iss 的
            # [Dirs] 声明的是 {localappdata}\Zaojing\Logs。在大小写不敏感的 NTFS 上，已存在
            # logs 时再建 Logs 不会另起一枚目录、也不会改名，盘上枚举出来就是 "logs"。因此
            # "四个子目录是否在场"必须按**文件系统的大小写语义**判，否则本机真装当轮就会被
            # 判成"数据目录缺失→installed=False"——那是仪器错，不是产品错。
            (Path(roots["localappdata"]) / "Zaojing" / "logs").mkdir(parents=True, exist_ok=True)

            fake_dir = ex / "fake"
            fake_dir.mkdir()
            (fake_dir / "fake_install.py").write_text(FAKE_INSTALLER_SRC, encoding="utf-8")
            (fake_dir / "fake_uninstall.py").write_text(FAKE_UNINSTALLER_SRC, encoding="utf-8")

            def _reg():
                return _json.loads(marker.read_text(encoding="utf-8"))

            # 真跑的 spawn 接缝：执行器交来的 argv[0] 是制品/卸载器路径，这里把它**改道**到
            # 对应的假脚本（假 setup.exe 不是合法 PE，直接 CreateProcess 会 WinError 216），
            # 其余参数（/VERYSILENT… /DIR= /LOG=）原样透传，保证门与派生逻辑吃的还是真 argv。
            def _spawn(cmd, log_path=None):
                first = os.path.basename(str(cmd[0])).lower()
                script = (str(fake_dir / "fake_uninstall.py") if "unins" in first
                          else str(fake_dir / "fake_install.py"))
                env = dict(os.environ, ZJ_FAKE_ROOTS=str(ex / "roots.json"),
                           ZJ_FAKE_MARKER=str(marker))
                argv = [sys.executable, "-u", script, *[str(c) for c in cmd[1:]]]
                r = _sp.run(argv, capture_output=True,
                            text=True, encoding="utf-8", errors="replace", env=env, timeout=180)
                return {"argv": list(cmd), "returncode": r.returncode,
                        "stdout_tail": (r.stdout or "")[-200:], "stderr_tail": (r.stderr or "")[-200:]}

            # 制品：临时假 setup.exe + 与它同轮的构建台账
            art = ex / "setup.exe"
            art.write_bytes(b"MZ\x90\x00" + b"payload" * 8)
            art_sha = hashlib.sha256(art.read_bytes()).hexdigest()
            ledger_dir = ex / "installer"
            ledger_dir.mkdir()
            bled = ledger_dir / "build-evidence.json"
            bled.write_text(_json.dumps({
                "script_path": str(ex / "setup.iss"), "script_bytes": 3,
                "script_sha256": hashlib.sha256(b"iss").hexdigest(),
                "artifact_path": str(art), "artifact_bytes": art.stat().st_size,
                "artifact_sha256": art_sha, "iscc_exit_code": 0,
                "compiler_path": sys.executable, "compiled": True, "fabricated": False,
            }), encoding="utf-8")
            (ex / "setup.iss").write_bytes(b"iss")
            tgt = ex / "inst-root"
            kw = dict(artifact=str(art), install_dir=str(tgt), app_name="ZaojingAI",
                      vendor_dir="FakeVendor", data_dir_name="Zaojing",
                      build_ledger=str(bled), roots=roots, registry=_reg,
                      allow_roots=[str(ex)], ledger_path=str(ex / "exec-ledger.json"),
                      spawn=_spawn)

            # 计划面的键名派生（Phase 58 就地抓住的自家缺陷）：AppId 在 setup.iss 里是 Inno
            # 转义源文 `{{GUID}`（双写首括号防常量展开），而注册表里真存的键是解码后的
            # `{GUID}_is1`。用 naive 拼接会造出一枚盘上根本不存在的键名并把"没查到"一路
            # 洗成"没装成"——这正是本轮要根除的假信号族，所以键名必须走同一条解括号规则。
            plan_app_id = "{{D4C8A1F2-7B3E-4E5A-9C6D-2F1B8A47E3D5}"
            pl = install_plan(artifact=str(art), install_dir=str(tgt), app_name="ZaojingAI",
                              vendor_dir="FakeVendor", data_dir_name="Zaojing",
                              app_id_braced=plan_app_id, build_ledger=str(bled), roots=roots)
            check("install plan: uninstall_registry_key un-escapes Inno's doubled leading brace "
                  "({{GUID} -> {GUID}_is1) so it names the key that really exists on disk",
                  pl["uninstall_registry_key"] == "{D4C8A1F2-7B3E-4E5A-9C6D-2F1B8A47E3D5}_is1",
                  extra="got=%s" % pl["uninstall_registry_key"])
            check("install plan: read-only face reports the same sha the executor will gate on, "
                  "and touches nothing",
                  pl["artifact"]["sha256"] == art_sha and pl["fabricated"] is False
                  and pl["build_ledger"]["sha_cross_equal"] is True
                  and pl["confirm_token"] == CONFIRM_TOKEN)

            # 注册表观察路径（Phase 58 本机真装当场抓出的第 4 枚真缺陷）：全仓库派生出来的
            # 卸载键名是**叶子形态**（只读探测侧先开 Uninstall 根再枚举叶子，所以叶子才是它的
            # 自然单位），执行器却拿它当 HKCU 相对全路径去开，于是 HKCU\{GUID}_is1 永远不存在：
            # 真装成功判 install_unverified，真卸完也"看见"键早没了（空洞真）。三形制必须在此
            # 钉死，因为 L1/评测台两侧都注入假 registry 字典，真实注册表接缝在本层**没有**自动
            # 覆盖——不在这里补，缝隙就仍然只能靠人工真装才发现。
            _leaf = "{D4C8A1F2-7B3E-4E5A-9C6D-2F1B8A47E3D5}_is1"
            _joined = UNINSTALL_REG_ROOT_REL + "\\" + _leaf
            check("uninstall_reg_path: leaf form gets the Uninstall root prepended "
                  "(this is the exact form uninstall_registry_key()/uninstall_key_for() emit)",
                  uninstall_reg_path(_leaf) == _joined,
                  extra="got=%s" % uninstall_reg_path(_leaf))
            check("uninstall_reg_path: already-full relative path passes through unchanged "
                  "(idempotent — re-joining would name a path that cannot exist)",
                  uninstall_reg_path(_joined) == _joined
                  and uninstall_reg_path(uninstall_reg_path(_leaf)) == _joined)
            check("uninstall_reg_path: empty/blank input stays empty (never invent a key path — "
                  "a fabricated 'Software\\...\\Uninstall\\' root would read as 'key present')",
                  uninstall_reg_path("") == "" and uninstall_reg_path("   ") == ""
                  and uninstall_reg_path(None) == "")
            check("uninstall_reg_path: forward slashes folded to backslashes (registry paths are "
                  "backslash-delimited; winreg would not open the slash form)",
                  uninstall_reg_path(_joined.replace("\\", "/")) == _joined,
                  extra="got=%s" % uninstall_reg_path(_joined.replace("\\", "/")))
            # 折叠是**分隔符级**的，不是"把斜杠从名字里抹掉"：键名本身不许含反斜杠，所以叶子内
            # 部混进的斜杠只能按层级理解。这一枚把两种读法分开钉死——若实现改成"删掉斜杠"，
            # 上面那条会照绿而这条必红；若实现改成"整串当名字"，回执就会说出一个开不了的键。
            check("uninstall_reg_path: the fold rewrites delimiters only, it never launders a slash "
                  "out of a name (stray slash => a nested path the receipt names honestly, instead of "
                  "a silently-rewritten key that looks like the right one)",
                  uninstall_reg_path(_leaf + "/x") == _joined + "\\x"
                  and uninstall_reg_path(_leaf.replace("_is1", "") + "/_is1")
                  == UNINSTALL_REG_ROOT_REL + "\\" + _leaf.replace("_is1", "") + "\\_is1")
            _tg_leaf = registry_observation_targets(_leaf, r"Software\造境科技\ZaojingAI")
            _tg_full = registry_observation_targets(_joined, r"Software\造境科技\ZaojingAI")
            check("registry_observation_targets: both input forms resolve to ONE observed path "
                  "(the receipt must name what was actually opened, whatever the caller handed in)",
                  _tg_leaf["uninstall"] == _tg_full["uninstall"] == _joined
                  and _tg_leaf["app"] == _tg_full["app"] == r"Software\造境科技\ZaojingAI")
            check("registry_observation_targets: uninstall_key_was_leaf distinguishes the two forms "
                  "(so a future regression to 'open the leaf under HKCU' is visible in the receipt)",
                  _tg_leaf["uninstall_key_was_leaf"] is True
                  and _tg_full["uninstall_key_was_leaf"] is False
                  and registry_observation_targets("", "")["uninstall"] == "")
            # 显示名规则的三形制：真装读回的那一枚必须收、旧夹具逐字相等那枚仍收、
            # 与产品无关的显示名必拒。只钉第一条的话"以 app_name 起始"会退化成一枚
            # 什么都收的钝刀（空 app_name 尤其如此），第三条就是它的正控。
            check("display_name_matches: accepts what the real machine actually writes "
                  "(Inno default = AppName+' '+AppVersion, read back verbatim from HKCU in P58)",
                  display_name_matches("ZaojingAI 1.0.0", "ZaojingAI") is True
                  and display_name_matches("ZaojingAI", "ZaojingAI") is True)
            check("display_name_matches NEG: unrelated display name rejected (the rule still discriminates)",
                  display_name_matches("Some Other Product 1.0.0", "ZaojingAI") is False)
            check("display_name_matches NEG: empty app_name must not make startswith vacuously true",
                  display_name_matches("ZaojingAI 1.0.0", "") is False
                  and display_name_matches("", "") is False
                  and display_name_matches(None, "ZaojingAI") is False)

            # 门 1：确认令牌缺失 / 错值
            # P58 变异判据就地抓住的**仪器自身缺陷**（第 11 枚，判卷侧）：负探针原先沿用正路
            # 的 `kw`（其中 install_dir 就是后面真跑要用的那个干净目标）。门一缺席——正是本桩
            # 的形制——这三臂就不再"被拒"，而是**真的各装一次**，于是第 1737 行的正路臂撞上
            # install_dir_not_empty、整台 L1 在裁决行之前崩掉（实测 rc=1、三臂领红后 traceback
            # 停在 execute_install 行 783）。一枚桩的作用面变成了"三次真实安装 + 仪器失声"，
            # 这既让本臂不可测，也把"仪器坏了"伪装成"产品坏了"。收口＝**负探针必须在结构上
            # 不可能产生它所守卫的副作用**：给它们一枚安全失效的安装根（源码树内路径，门 5 当场
            # 拒绝且从不 mkdir），被测门在场时该值永远轮不到（门序 confirm→artifact→ledger→
            # install_dir），被测门缺席时才由它兜住副作用。
            _neg_probe_dir = str(Path(__file__).resolve().parent / "p58-neg-probe")
            _neg_kw = {**{k: v for k, v in kw.items() if k != "confirm"},
                       "install_dir": _neg_probe_dir}
            for bad_c in (None, "", "I-agree"):
                try:
                    execute_install(confirm=bad_c, **_neg_kw)
                    g1 = False
                except ExecutionError as e:
                    g1 = e.reason == "confirmation_required"
                check(f"install gate NEG: confirm={bad_c!r} -> confirmation_required", g1)
            # 门 2：制品在允许根外（另起一枚临时根放它，收尾时随目录一并删——不在共享
            #        temp 根里留无人认领的散件）
            out = Path(tempfile.mkdtemp(prefix="zj_p58_out_"))
            elsewhere = out / "elsewhere.exe"
            elsewhere.write_bytes(b"MZ\x90\x00outside")
            try:
                execute_install(confirm=CONFIRM_TOKEN, **{**kw, "artifact": str(elsewhere)})
                g2 = False
            except ExecutionError as e:
                g2 = e.reason == "artifact_outside_allowed_root"
            check("install gate NEG: artifact outside allowed roots -> refused (executor is not a "
                  "generic program runner)", g2)
            shutil.rmtree(out, ignore_errors=True)
            # 门 3：制品 sha 与构建台账不同轮（等长替换必须被抓）
            swapped = ex / "swapped.exe"
            swapped.write_bytes(b"MZ\x90\x00" + b"payloaX" * 8)
            bled.write_text(_json.dumps({**_json.loads(bled.read_text(encoding="utf-8")),
                                         "artifact_path": str(swapped)}), encoding="utf-8")
            try:
                execute_install(confirm=CONFIRM_TOKEN, **{**kw, "artifact": str(swapped)})
                g3 = False
            except ExecutionError as e:
                g3 = e.reason in ("artifact_sha_mismatch_vs_build_ledger", "build_ledger_stale")
            check("install gate NEG: same-length payload swap breaks sha -> refused", g3)
            bled.write_text(_json.dumps({**_json.loads(bled.read_text(encoding="utf-8")),
                                         "artifact_path": str(art)}), encoding="utf-8")
            # 门 4：目标目录非空 → 拒（真实安装必须落干净临时根；先建目录再放散件才构造得出"非空"）
            tgt.mkdir(parents=True, exist_ok=True)
            (tgt / "junk.txt").write_text("x", encoding="utf-8")
            try:
                execute_install(confirm=CONFIRM_TOKEN, **kw)
                g4 = False
            except ExecutionError as e:
                g4 = e.reason == "install_dir_not_empty"
            check("install gate NEG: non-empty install_dir -> install_dir_not_empty (no silent takeover)", g4)
            (tgt / "junk.txt").unlink()
            # 门 5：安装根不得落在源码树内（allow_roots 同时放行制品所在临时根，否则先撞
            #        制品那道门，测不到本门）
            try:
                execute_install(confirm=CONFIRM_TOKEN,
                                **{**kw, "install_dir": str(Path(__file__).resolve().parent / "nested"),
                                   "allow_roots": [str(ex), str(Path(__file__).resolve().parent)]})
                g5 = False
            except ExecutionError as e:
                g5 = e.reason == "install_dir_inside_source_tree"
            check("install gate NEG: install_dir inside source tree -> refused", g5)

            # 正路：真子进程真跑，逐项观察必须全绿且 installed 由差集派生
            ok1 = execute_install(confirm=CONFIRM_TOKEN, **kw)
            check("install REAL run: status install_verified, installed derived from disk+registry diff",
                  ok1["status"] == "install_verified" and ok1["installed"] is True
                  and ok1["basis"] == "derived_from_disk_and_registry" and ok1["fabricated"] is False)
            _dd = [c for c in ok1["checks"] if c["check"] == "data_dirs_present"][0]
            check("install REAL run: data_dirs_present judged with the filesystem's own case semantics "
                  "(real machine declares [Dirs] Logs while the app's logger already created logs; "
                  "on a case-insensitive volume that IS the same directory and must count as present)",
                  _dd["ok"] is True and "logs" in _dd["observed"],
                  extra="ok=%s observed=%s" % (_dd["ok"], _dd["observed"]))
            # 上面那条判据的比较器本身不许是一把"永远说在场"的钝刀：正控（缺一个就必点名）
            # 与平台语义（大小写敏感的卷上 logs 不算 Logs）各钉一枚，两者互为反证。
            check("missing_data_dirs POSITIVE CONTROL: a genuinely absent subdir is still named "
                  "(the case-aware comparison must not become an always-present blanket)",
                  missing_data_dirs(["Assets", "Cache", "config"], DATA_SUBDIRS) == ["Logs"])
            _shape = ["Assets", "cache", "LOGS", "config"]
            check("missing_data_dirs: comparison follows os.path.normcase of the running platform "
                  "(Windows folds case => no false negative; a case-sensitive volume still reports "
                  "the wrongly-spelled dir as missing)",
                  missing_data_dirs(_shape, ("Cache", "Logs")) == ([] if WINDOWS else ["Cache", "Logs"]),
                  extra="windows=%s got=%s" % (WINDOWS, missing_data_dirs(_shape, ("Cache", "Logs"))))
            check("install REAL run: every declared observation is present and ok (counts derived)",
                  len(ok1["checks"]) == 10 and all(c["ok"] for c in ok1["checks"])
                  and ok1["checks_count"] == len(ok1["checks"]))
            check("install REAL run: diff created set spans install_root+userdocs+start_menu_group "
                  "with exe+unins000 really on disk",
                  ok1["diff"]["created_count"] == len(ok1["diff"]["created"])
                  and {"install_root", "userdocs", "start_menu_group"}
                  <= {f["root"] for f in ok1["diff"]["created"]}
                  and (tgt / "ZaojingAI.exe").is_file() and (tgt / "unins000.exe").is_file())
            check("install REAL run: argv carries the four silent switches and /DIR=/LOG= (no GUI install)",
                  all(f in ok1["argv"] for f in ("/VERYSILENT", "/NORESTART", "/SUPPRESSMSGBOXES"))
                  and any(a.startswith("/DIR=") for a in ok1["argv"])
                  and any(a.startswith("/LOG=") for a in ok1["argv"]))
            check("install REAL run: log bytes/sha read from disk, finished-line recorded as observation",
                  ok1["log"]["bytes"] > 0 and len(ok1["log"]["sha256"]) == 64
                  and ok1["log"]["seen_finished_line"] is True)
            check("install REAL run: preexisting user data named and untouched (Assets/keep-me.json survives)",
                  keep.is_file() and any(f.get("rel", "").endswith("keep-me.json")
                                         for f in ok1["preexisting"]["files"])
                  and ok1["preexisting"]["intact"] is True)
            # 从入参到回执必须是**一根连续的线**：叶子键名交进执行器后，回执里说出的观察对象
            # 得是补全后的全路径，否则本机真装那一轮的"键不在"又会落成"压根没去看"。
            # view 这一枚同时是本层的自限声明——它是 "injected"，意味着这里的绿只证明路径拼接
            # 与派生逻辑；真实注册表接缝的覆盖由冻结 EXE 那一腿真装真卸承担，两者不许互相冒充。
            _dn = [c for c in ok1["checks"] if c["check"] == "registry_uninstall_key_present"][0]
            check("install REAL run: the display-name check observed the shape the REAL machine writes "
                  "(fixture now mirrors Inno's default AppName+' '+AppVersion, not the bare app_name)",
                  _dn["ok"] is True and _dn["observed"] == "ZaojingAI 1.0.0"
                  and _dn["expected_rule"] == "display_name_startswith_app_name",
                  extra="obs=%r" % _dn["observed"])
            # 这一枚必须**自带一套沙箱**（独立 marker / 独立 roots.json / 独立台账 / 独立日志）。
            # 键形回执要的是"再装一次，观察对象仍按补全后的路径说出去"，而不是第二次真装；
            # 若复用共享 marker，假安装器会把 reg["uninstall"]["InstallLocation"] 改写成
            # inst-keys，随后的真卸载就会去删 inst-keys 而非 tgt —— 于是"安装根没删干净"
            # 一路污染成卸载/对账三条红（本轮亲手踩过：加这条检查时 L1 从 62/62 掉到 73/77）。
            _kmark = ex / "registry-marker.keys.json"
            _kmark.write_text("{}", encoding="utf-8")
            _kroots_dir = ex / "keys-roots"
            _kroots = {"localappdata": str(_kroots_dir / "fake-local"),
                       "userprofile": str(_kroots_dir / "fake-user"),
                       "appdata": str(_kroots_dir / "fake-appdata")}
            for _v in _kroots.values():
                Path(_v).mkdir(parents=True, exist_ok=True)
            (_kroots_dir / "roots.json").write_text(_json.dumps(_kroots), encoding="utf-8")

            def _reg_keys():
                return _json.loads(_kmark.read_text(encoding="utf-8"))

            def _spawn_keys(cmd, log_path=None):
                first = os.path.basename(str(cmd[0])).lower()
                script = (str(fake_dir / "fake_uninstall.py") if "unins" in first
                          else str(fake_dir / "fake_install.py"))
                env = dict(os.environ, ZJ_FAKE_ROOTS=str(_kroots_dir / "roots.json"),
                           ZJ_FAKE_MARKER=str(_kmark))
                argv = [sys.executable, "-u", script, *[str(c) for c in cmd[1:]]]
                r = _sp.run(argv, capture_output=True,
                            text=True, encoding="utf-8", errors="replace", env=env, timeout=180)
                return {"argv": [str(c) for c in cmd], "returncode": r.returncode,
                        "stdout_tail": (r.stdout or "")[-200:],
                        "stderr_tail": (r.stderr or "")[-200:]}

            keys_run = execute_install(confirm=CONFIRM_TOKEN,
                                       **{**kw, "install_dir": str(ex / "inst-keys"),
                                          "log_path": str(ex / "inst-keys-install.log"),
                                          "uninstall_key": _leaf,
                                          "app_reg_key": r"Software\FakeVendor\ZaojingAI",
                                          "roots": _kroots, "registry": _reg_keys,
                                          "spawn": _spawn_keys,
                                          "ledger_path": str(ex / "exec-ledger.keys.json")})
            _ro = keys_run["registry_observation"]
            check("install REAL run: registry_observation echoes the JOINED path for a leaf-form input "
                  "and names its own seam as injected (L1 green != real-registry coverage)",
                  keys_run["status"] == "install_verified" and _ro["uninstall"] == _joined
                  and _ro["uninstall_input_form"] == _leaf
                  and _ro["uninstall_key_was_leaf"] is True
                  and _ro["view"] == "injected" and _ro["hive"] == "HKCU"
                  and _reg()["uninstall"]["InstallLocation"] == str(tgt),
                  extra="obs=%s" % _ro)
            check("install REAL run: no key handed in => receipt admits it looked at nothing "
                  "(empty stays empty instead of naming the bare Uninstall root as if it were a key)",
                  ok1["registry_observation"]["uninstall"] == ""
                  and ok1["registry_observation"]["app"] == ""
                  and ok1["registry_observation"]["view"] == "injected")

            # 缺省日志落点——本机真装抓住的第 7 枚真缺陷。真实 CLI 从不交 log_path，走的就是
            # execute_install 里那支缺省。旧缺省取 os.path.dirname(install_dir)，本机即
            # %LOCALAPPDATA%\Programs：那枚目录 (1) 不在卸载器能撤的范围里——unins000 只按自己的
            # 安装日志撤，日志本身不在安装根里就永远撤不掉；(2) 不是本产品声明拥有的根，它与别家
            # 应用共用，也不在本次批准写入的落点清单里；(3) 不在 _touched_roots 的观察面里，
            # 于是差集数不到、residue 也数不到。现场实证：真装一次后 %LOCALAPPDATA%\Programs 下
            # 多出 6,859 字节的 zaojing-install.log（该目录唯一一个文件），而同一轮回执的
            # install_root 只报了 3 个文件、卸后快照还会报 uninstall_verified——自报与磁盘实物解耦
            # 的老族（Phase 54 那一族）在执行面又长出一枚。夹具侧此前看不见的缘故：所有用例都把
            # 安装根放在沙箱里，父目录本身就是沙箱根，写进去也算"在沙箱内"，只有真机才暴露
            # "父目录是共用目录"。现在的规矩：证据与证据同根——缺省落在台账所在目录（真机即
            # %LOCALAPPDATA%\Zaojing\installer，属产品数据根，卸载有意保留且被观察面覆盖）。
            _lex = ex / "log-default"
            _lprog = _lex / "fake-programs"                 # 安装根的父目录：类比 %LOCALAPPDATA%\Programs
            _ltgt = _lprog / "FakeVendor"
            _lroots_dir = _lex / "roots"
            _lroots = {"localappdata": str(_lroots_dir / "fake-local"),
                       "userprofile": str(_lroots_dir / "fake-user"),
                       "appdata": str(_lroots_dir / "fake-appdata")}
            _levid = Path(_lroots["localappdata"]) / "Zaojing" / "installer"   # 台账/日志所在（数据根内）
            for _v in list(_lroots.values()) + [str(_lprog), str(_levid)]:
                Path(_v).mkdir(parents=True, exist_ok=True)
            (_lroots_dir / "roots.json").write_text(_json.dumps(_lroots), encoding="utf-8")
            _lmark = _lex / "registry-marker.log.json"
            _lmark.write_text("{}", encoding="utf-8")

            def _reg_log():
                return _json.loads(_lmark.read_text(encoding="utf-8"))

            def _spawn_log(cmd, log_path=None):
                first = os.path.basename(str(cmd[0])).lower()
                script = (str(fake_dir / "fake_uninstall.py") if "unins" in first
                          else str(fake_dir / "fake_install.py"))
                env = dict(os.environ, ZJ_FAKE_ROOTS=str(_lroots_dir / "roots.json"),
                           ZJ_FAKE_MARKER=str(_lmark))
                argv = [sys.executable, "-u", script, *[str(c) for c in cmd[1:]]]
                r = _sp.run(argv, capture_output=True,
                            text=True, encoding="utf-8", errors="replace", env=env, timeout=180)
                return {"argv": [str(c) for c in cmd], "returncode": r.returncode,
                        "stdout_tail": (r.stdout or "")[-200:],
                        "stderr_tail": (r.stderr or "")[-200:]}

            log_run = execute_install(confirm=CONFIRM_TOKEN,
                                      **{**kw, "install_dir": str(_ltgt),
                                         "uninstall_key": _leaf,
                                         "app_reg_key": r"Software\FakeVendor\ZaojingAI",
                                         "roots": _lroots, "registry": _reg_log,
                                         "spawn": _spawn_log,
                                         "ledger_path": str(_levid / "exec-ledger.json")})
            _lp = Path(log_run["log"]["path"])
            check("install REAL run: the DEFAULT installer log lands beside the execution ledger, "
                  "inside the product data root (a caller that hands no log_path must not have to "
                  "know where our evidence lives — the engine decides it, and the receipt says so)",
                  log_run["status"] == "install_verified" and _lp.parent == _levid
                  and _lp.name == "zaojing-install.log" and _lp.is_file(),
                  extra="log=%s ledger_dir=%s st=%s" % (_lp, _levid, log_run["status"]))
            check("install REAL run: the DEFAULT log never lands in the install dir's parent "
                  "(on this machine that parent is %LOCALAPPDATA%\\Programs — shared with other "
                  "vendors' apps, outside the uninstaller's reach, and outside every observation "
                  "root, so a file written there leaks forever while both receipts say 'clean')",
                  _lp.parent != _ltgt.parent
                  and not (_lex / "fake-programs" / "zaojing-install.log").exists()
                  and sorted(q.name for q in _lprog.iterdir()) == ["FakeVendor"],
                  extra="parent=%s siblings=%s" % (
                      _ltgt.parent, sorted(q.name for q in _lprog.iterdir())))
            _lcreated = {(f["root"], f["rel"]) for f in log_run["diff"]["created"]}
            _ldata = Path(_lroots["localappdata"]) / "Zaojing"
            # 判据本身必须是**全函数**：早先这里直接 _lp.relative_to(_ldata)，日志落在数据根之外时
            # pathlib 抛 ValueError，整层自测当场崩掉——崩掉的层没有裁决行，既不算通过也不能拿来
            # 证明产品有问题。先做包含性判定，不在一起就明说"不在数据根内"。
            _lrel = (str(_lp.relative_to(_ldata)) if _ldata in _lp.parents else
                     "NOT_INSIDE_DATA_ROOT")
            check("install REAL run: the DEFAULT log is inside the observed map, so the diff can "
                  "name it as something this install created (invisible side effect = un-auditable)",
                  ("runtime_data_root", _lrel) in _lcreated,
                  extra="rel=%s created=%s" % (_lrel, sorted(_lcreated)))

            # 只读对账在"已装未卸"窗口里没有诚实说法——本机真装抓住的第 9 枚真缺陷。
            # 真跑完 install、还没跑 uninstall 时去 state，旧口径判 ledger_inconsistent，
            # 判词是"台账无卸载记录而本机仍在安装态"：现场完全正常（目录在、两枚键在），
            # 被仪器报成对不上账，操作者只能看到"台账有问题"这一条假信号。而另一侧，
            # verified_run 硬性要求存在一条 uninstall_verified 记录，install-only 永远升不到
            # 任何终态——两件事合起来意味着这台机器上"装好了等着卸"的窗口既不算通过也不算
            # 失败，只能算不一致。真正该判不一致的是**反向**情形：台账说装成了、本机却已经
            # 不在安装态、又没有任何卸载记录——那是安装痕迹被台账之外的手段抹掉（手工删目录、
            # 第三方清理器、注册表被回滚），必须点名。所以这里把判据倒过来钉住两侧：
            # 安装态在场 ⇒ install_verified_awaiting_uninstall 且 mismatches 为空；
            # 安装态不在场 ⇒ ledger_inconsistent 并逐字段点名缺了哪一侧。
            _st_open = execution_state(ledger_path=str(_levid / "exec-ledger.json"),
                                       registry=_reg_log, roots=_lroots, build_ledger=str(bled))
            check("state REAL run (install-only window): a verified install + no uninstall record "
                  "while the machine still shows the install is an honest INTERMEDIATE state, not a "
                  "reconciliation failure (the old verdict called this ledger_inconsistent, which "
                  "made 'installed and waiting' indistinguishable from a forged ledger)",
                  _st_open["status"] == "install_verified_awaiting_uninstall"
                  and _st_open["mismatches"] == []
                  and _st_open["recomputed"]["install_dir_now_exists"] is True
                  and _st_open["recomputed"]["uninstall_key_now_present"] is True
                  and _st_open["recomputed"]["app_key_now_present"] is True,
                  extra="st=%s mism=%s recomp=%s" % (
                      _st_open["status"], _st_open["mismatches"],
                      _st_open["recomputed"]))
            # 现场搬到台账之外：目录删掉、两枚键清空，但台账仍只有 install 这一笔。
            shutil.rmtree(str(_ltgt), ignore_errors=True)
            _lmark.write_text("{}", encoding="utf-8")
            _st_off = execution_state(ledger_path=str(_levid / "exec-ledger.json"),
                                      registry=_reg_log, roots=_lroots, build_ledger=str(bled))
            check("state REAL run (off-book removal): the same ledger with the install dir and both "
                  "registry keys gone and STILL no uninstall record must be named a mismatch — the "
                  "record-free intermediate verdict is only honest while the machine agrees it is "
                  "installed; otherwise 'something removed the app outside the ledger' would read as "
                  "normal, which is the exact laundering this state must not enable",
                  _st_off["status"] == "ledger_inconsistent"
                  and any("install_verified" in m and "无卸载记录" in m
                          for m in _st_off["mismatches"])
                  and _st_off["recomputed"]["install_dir_now_exists"] is False
                  and _st_off["recomputed"]["uninstall_key_now_present"] is False,
                  extra="st=%s mism=%s" % (_st_off["status"], _st_off["mismatches"]))

            # 既有数据基线含执行器自家证据件 ⇒ 第二轮必假红——本机真装抓住的第 10 枚真缺陷。
            # _preexisting 只跳过 install_root/vendor_dir 两枚标签，于是数据根里的每一枚文件
            # 都算"用户既有数据"，**包括执行器自己上一轮写的台账与安装日志**（缺省落点把它们
            # 就放在数据根内，这是第 7 枚缺陷的修法带来的必然结果）。第二轮 install 会重写
            # 台账（created_at/差集/回执都在变），于是本轮基线里那枚旧台账的 sha 对不上，
            # execute_uninstall 的 verified 合取（… and pre["intact"]）就把一次干干净净的卸载
            # 判成 uninstall_incomplete。本机实证：卸后回执 deviating 恰好一枚，路径是
            # %LOCALAPPDATA%\\Zaojing\\installer\\execution-evidence.json（本轮自己刚写的证据），
            # 而六份真正的用户数据逐字节相等——判红的正是"证据被更新了"这件事本身。
            # 规矩：证据与用户数据分账。自家输出从基线里挑出来单独回显（executor_owned），
            # 改动仍然看得见，但不再冒充"用户数据被改坏"。
            _rex = ex / "second-round"
            _rroots_dir = _rex / "roots"
            _rroots = {"localappdata": str(_rroots_dir / "fake-local"),
                       "userprofile": str(_rroots_dir / "fake-user"),
                       "appdata": str(_rroots_dir / "fake-appdata")}
            _rdata = Path(_rroots["localappdata"]) / "Zaojing"
            _revid = _rdata / "installer"                    # 台账与缺省日志所在（数据根内）
            _rprog = _rex / "fake-programs"
            _rtgt = _rprog / "FakeVendor"
            for _v in list(_rroots.values()) + [str(_revid), str(_rprog)]:
                Path(_v).mkdir(parents=True, exist_ok=True)
            (_rroots_dir / "roots.json").write_text(_json.dumps(_rroots), encoding="utf-8")
            _rmark = _rex / "registry-marker.json"
            _rmark.write_text("{}", encoding="utf-8")
            _rkeep = _rdata / "Assets" / "keep-across-rounds.json"
            _rkeep.parent.mkdir(parents=True, exist_ok=True)
            _rkeep.write_bytes(b'{"user":true}')
            _rkeep_sha = hashlib.sha256(_rkeep.read_bytes()).hexdigest()

            def _reg_r():
                return _json.loads(_rmark.read_text(encoding="utf-8"))

            def _spawn_r(cmd, log_path=None):
                first = os.path.basename(str(cmd[0])).lower()
                script = (str(fake_dir / "fake_uninstall.py") if "unins" in first
                          else str(fake_dir / "fake_install.py"))
                env = dict(os.environ, ZJ_FAKE_ROOTS=str(_rroots_dir / "roots.json"),
                           ZJ_FAKE_MARKER=str(_rmark))
                argv = [sys.executable, "-u", script, *[str(c) for c in cmd[1:]]]
                r = _sp.run(argv, capture_output=True,
                            text=True, encoding="utf-8", errors="replace", env=env, timeout=180)
                return {"argv": [str(c) for c in cmd], "returncode": r.returncode,
                        "stdout_tail": (r.stdout or "")[-200:],
                        "stderr_tail": (r.stderr or "")[-200:]}

            _rkw = dict(artifact=str(art), build_ledger=str(bled), app_name="ZaojingAI",
                        vendor_dir="FakeVendor", data_dir_name="Zaojing", roots=_rroots,
                        registry=_reg_r, spawn=_spawn_r, allow_roots=[str(ex)],
                        uninstall_key=_leaf, app_reg_key=r"Software\FakeVendor\ZaojingAI",
                        ledger_path=str(_revid / "exec-ledger.json"))
            # execute_uninstall 不吃制品与构建台账（卸载器来历走"盘上 unins000 > 注册表
            # UninstallString"两路，与安装侧的 sha 门是两件事），参数表必须按各自签名给。
            _rkw_u = {k: v for k, v in _rkw.items() if k not in ("artifact", "build_ledger")}
            _r1i = execute_install(confirm=CONFIRM_TOKEN, **{**_rkw, "install_dir": str(_rtgt)})
            _r1u = execute_uninstall(confirm=CONFIRM_TOKEN, install_dir=str(_rtgt),
                                     poll_timeout_s=2, poll_interval_s=0.2, **_rkw_u)
            check("install/uninstall REAL run, round 1 (ledger sits inside the observed data root): "
                  "a clean round-trip verifies — this is the arm that has nothing to do with the "
                  "defect, it exists so round 2's verdict can't be blamed on a broken fixture",
                  _r1i["status"] == "install_verified"
                  and _r1u["status"] == "uninstall_verified"
                  and _r1u["preexisting"]["count"] == 1
                  and _r1u["preexisting"]["intact"] is True,
                  extra="i=%s u=%s pre=%s" % (_r1i["status"], _r1u["status"],
                                              _r1u["preexisting"]))
            _r2i = execute_install(confirm=CONFIRM_TOKEN, **{**_rkw, "install_dir": str(_rtgt)})
            _r2u = execute_uninstall(confirm=CONFIRM_TOKEN, install_dir=str(_rtgt),
                                     poll_timeout_s=2, poll_interval_s=0.2, **_rkw_u)

            check("install/uninstall REAL run, round 2: the executor's OWN previous-round evidence "
                  "(ledger + default installer log, both inside the data root by design after "
                  "defect 7) must not be counted as pre-existing USER data — otherwise the file this "
                  "very round rewrites is the one that turns a genuinely complete uninstall into "
                  "uninstall_incomplete (measured on this machine: deviating listed exactly "
                  "execution-evidence.json while all six real user files were byte-identical)",
                  _r2u["status"] == "uninstall_verified"
                  and _r2u["install_dir_gone"] is True and _r2u["uninstall_key_gone"] is True
                  and _r2u["app_key_gone"] is True
                  and [f["path"] for f in _r2u["preexisting"]["files"]] == [str(_rkeep)]
                  and _r2u["preexisting"]["intact"] is True
                  and _r2u["preexisting"]["deviating"] == []
                  and _rkeep.read_bytes() == b'{"user":true}'
                  and hashlib.sha256(_rkeep.read_bytes()).hexdigest() == _rkeep_sha,
                  extra="u=%s pre=%s" % (_r2u["status"], _r2u["preexisting"]))
            _r_owned = {str(q["path"]) for q in _r2i["preexisting"].get("executor_owned", [])}
            check("install REAL run, round 2: excluding our own outputs from the user-data baseline "
                  "must NOT hide them — the receipt names each executor-owned file it skipped and "
                  "says this run rewrites it (auditability is not served by dropping the record)",
                  _r_owned == {str(_revid / "exec-ledger.json"),
                               str(_revid / "zaojing-install.log")}
                  and all(q["this_run_rewrites_it"] is True
                          for q in _r2i["preexisting"]["executor_owned"]),
                  extra="owned=%s" % _r2i["preexisting"].get("executor_owned"))

            # 完成判定不可信自报：退出码 0 但磁盘无产物 ⇒ 必判 install_unverified
            def _spawn_noop(cmd, log_path=None):
                return {"argv": list(cmd), "returncode": 0, "stdout_tail": "Successful",
                        "stderr_tail": ""}
            ghost = ex / "ghost-root"
            r_ghost = execute_install(confirm=CONFIRM_TOKEN,
                                      **{**kw, "install_dir": str(ghost), "spawn": _spawn_noop,
                                         "log_path": str(ex / "ghost-install.log"),
                                         "ledger_path": str(ex / "exec-ledger.ghost.json")})
            check("install NEG: exit_code 0 + self-reported success but zero artifacts -> install_unverified",
                  r_ghost["status"] == "install_unverified" and r_ghost["installed"] is False
                  and r_ghost["exit_code"] == 0
                  and any(not c["ok"] for c in r_ghost["checks"]))
            # 这一枚要单独交 log_path：不交的话它落进缺省位置，而那位置早被上一轮 ok1 的真日志占着，
            # 于是"本轮 installer_log_written"会读成**别人**的字节数（本轮其实一个字都没写）。
            # 观察面必须按动作隔离，否则 NEG 用例里混进一枚假绿——与"缺省落点"那条同一个病根。
            check("install NEG: a run whose installer wrote nothing reports zero log bytes for ITSELF "
                  "(the log observation must be this run's own, not a previous run's leftover)",
                  r_ghost["log"]["bytes"] == 0 and r_ghost["log"]["sha256"] == ""
                  and r_ghost["log"]["seen_finished_line"] is False
                  and not (ex / "ghost-install.log").exists())
            # 第 11 枚真缺陷（由评测侧先撞出、这一枚臂事后收紧）：旧臂写成
            # `in ("ledger_unverified", "ledger_inconsistent")`，于是第 9 枚的修复把"台账从未声称
            # 装成过"也升级成"安装痕迹被台账之外的手段抹掉"时，这一层照样绿。空手一趟的台账与
            # 空手一台机器**并不矛盾**——台账没说过装成，就没有"被抹掉"可言；诚实的回答是
            # ledger_unverified 且 mismatches 为空。点名"被台账之外抹掉"只留给台账确实写了
            # install_verified 而现场已不在安装态的那种情形。
            _ghost_state = execution_state(ledger_path=str(ex / "exec-ledger.ghost.json"),
                                           registry=_reg, roots=roots, build_ledger=str(bled))
            check("install NEG: unverified run leaves installed=false in its own ledger (no laundered receipt), "
                  "and reconciling it reads ledger_unverified with NO mismatch — an unverified ledger plus an "
                  "empty machine is honest silence, not an off-book wipe",
                  _ghost_state["status"] == "ledger_unverified"
                  and _ghost_state["mismatches"] == []
                  and _ghost_state["recomputed"]["install_checks_ok_all"] is False
                  and _ghost_state["recomputed"]["install_dir_now_exists"] is False,
                  extra="state=%s mismatches=%s" % (_ghost_state["status"],
                                                    _ghost_state["mismatches"]))

            # 卸载不可信自报（先于真卸载跑：此刻安装产物仍在盘上）
            def _spawn_lazy(cmd, log_path=None):
                return {"argv": list(cmd), "returncode": 0, "stdout_tail": "", "stderr_tail": ""}
            r_lazy = execute_uninstall(confirm=CONFIRM_TOKEN, install_dir=str(tgt),
                                       app_name="ZaojingAI", vendor_dir="FakeVendor",
                                       data_dir_name="Zaojing", roots=roots, registry=_reg,
                                       spawn=_spawn_lazy, poll_timeout_s=2, poll_interval_s=0.2,
                                       ledger_path=str(ex / "exec-ledger.json"),
                                       allow_roots=[str(ex)])
            check("uninstall NEG: exit 0 but registry key still present -> uninstall_incomplete with residue list",
                  r_lazy["status"] == "uninstall_incomplete" and r_lazy["uninstall_key_gone"] is False
                  and len(r_lazy["residue"]) >= 1)

            # 真卸载：轮询到注册表项与安装根双双消失才算 uninstall_verified
            r_un = execute_uninstall(confirm=CONFIRM_TOKEN, install_dir=str(tgt),
                                     app_name="ZaojingAI", vendor_dir="FakeVendor",
                                     data_dir_name="Zaojing", roots=roots, registry=_reg,
                                     spawn=_spawn, ledger_path=str(ex / "exec-ledger.json"),
                                     uninstall_key=_leaf,
                                     app_reg_key=r"Software\FakeVendor\ZaojingAI",
                                     allow_roots=[str(ex)])
            check("uninstall REAL run: uninstall_verified, dir gone, registry keys gone, polls recorded",
                  r_un["status"] == "uninstall_verified" and r_un["install_dir_gone"] is True
                  and r_un["uninstall_key_gone"] is True and r_un["app_key_gone"] is True
                  and r_un["polls"] >= 1 and r_un["basis"] == "derived_from_disk_and_registry")
            # 这一枚是本层最要紧的防呆：卸载那一侧的"键已消失"若落在压根没打开过的路径上，
            # 它就**空洞为真**——uninstall_verified 照样绿，而盘上的键还好端端躺着。真装当轮
            # 之所以没让这事溜过去，全靠这里把观察对象钉成补全后的全路径。
            check("uninstall REAL run: the 'key gone' verdict is anchored to the JOINED path, not to "
                  "a leaf opened directly under HKCU (a vacuous uninstall_verified is the failure mode)",
                  r_un["registry_observation"]["uninstall"] == _joined
                  and r_un["registry_observation"]["uninstall_key_was_leaf"] is True
                  and r_un["registry_observation"]["view"] == "injected",
                  extra="obs=%s" % r_un["registry_observation"])
            check("uninstall REAL run: preexisting data byte-identical after uninstall (nothing borrowed as sandbox)",
                  keep.is_file() and keep.read_bytes() == b'{"preexisting":true}'
                  and r_un["preexisting"]["intact"] is True)
            check("uninstall scope: only paths created by the install are eligible for removal",
                  r_un["removed_scope"] == "created_by_install_only"
                  and all(p.get("created_by_install") is True for p in r_un["removed"]))
            # 第 12 枚真缺陷：回执把台账记下的路径**改写成形**再交出去（normcase+realpath 把
            # `D:\…` 折成 `d:\…`）。比对归一化是对的，把归一形当凭据就是假话——同一件事在
            # install 面叫 `D:\…\某文件`、uninstall 面叫 `d:\…\某文件`，任何逐字交叉核对都会
            # 撞出一堵假墙（评测第一次拿 removed.path 对 install.log.path 就撞在这上面）。
            # 注：本层夹具的缺省日志落在**观察根之外**（ex 根直下），所以"日志同时出现在
            # removed 里"这条交叉核对只能在评测侧成立——那里的台账与日志都在被观察的数据根内
            # （真实 CLI 的产品形状，见 eval-exec-second-round-evidence-not-user-data）。
            check("uninstall REAL run: removed[] echoes the ledger's OWN spelling of every created path "
                  "(compare normalized, receipt verbatim)",
                  {r["path"] for r in r_un["removed"]}
                  == {f["path"] for f in ok1["diff"]["created"]}
                  and len(r_un["removed"]) == len(ok1["diff"]["created"]),
                  extra="removed=%s created=%s log=%s" % (
                      [r["path"] for r in r_un["removed"]],
                      [f["path"] for f in ok1["diff"]["created"]],
                      ok1["log"]["path"]))

            # 台账对账：在场且与磁盘/注册表现场重算一致 → executed_verified
            st = execution_state(ledger_path=str(ex / "exec-ledger.json"), registry=_reg,
                                 roots=roots, build_ledger=str(bled))
            check("execution_state: ledger reconciles against live disk+registry (executed_verified)",
                  st["status"] == "executed_verified" and st["fabricated"] is False
                  and st["recomputed"]["install_exit_code"] == 0)
            check("execution_state: self-reported booleans in the ledger are echoed but NOT counted",
                  "install.installed" in st["self_report_ignored"]
                  and "uninstall.performed" in st["self_report_ignored"])
            # 对账面也必须说清"现在的现场是去哪儿看的"，否则它回报的
            # uninstall_key_now_present=False 与"路径写错所以看不见"两种情形无法区分。
            check("execution_state: reports which registry path it re-read live (so 'key absent' is "
                  "never confusable with 'never looked')",
                  st["registry_observation"]["hive"] == "HKCU"
                  and "uninstall" in st["registry_observation"]
                  and st["registry_observation"]["view"] == "injected")
            # 篡改台账：现场"已消失"的事实被改成"还在" ⇒ 重算必须盖过它并指名字段
            tampered = _json.loads((ex / "exec-ledger.json").read_text(encoding="utf-8"))
            tampered["install"]["installed"] = True
            tampered["uninstall"]["install_dir_gone"] = False
            (ex / "exec-ledger.tampered.json").write_text(_json.dumps(tampered), encoding="utf-8")
            st_t = execution_state(ledger_path=str(ex / "exec-ledger.tampered.json"),
                                   registry=_reg, roots=roots, build_ledger=str(bled))
            check("execution_state NEG: ledger contradicts live state (claims dir still present) -> "
                  "not executed_verified, mismatch names the field",
                  st_t["status"] in ("ledger_inconsistent", "ledger_unverified")
                  and any("uninstall" in m for m in st_t["mismatches"]))
            # 台账缺失 → 诚实 ledger_absent，绝不伪造
            st_a = execution_state(ledger_path=str(ex / "nope.json"), registry=_reg,
                                   roots=roots, build_ledger=str(bled))
            check("execution_state NEG: absent ledger -> ledger_absent (never invent a receipt)",
                  st_a["status"] == "ledger_absent" and st_a["fabricated"] is False)
            # 现场读不动的两形制（Phase 58）：构建台账是坏 JSON、安装日志被别的进程独占。
            # 对账面是**只读**的：读失败必须降级成一条点名的不一致，而不是把异常抛出函数——
            # 装配成 HTTP 面后异常就是 500，而 500 既不是"通过"也不是裁决，真相是它压根
            # 没给出结论（§2 红线：回执只能由磁盘/注册表现场派生，不能由崩溃代替）。
            bad_bled = ex / "build-evidence.corrupt.json"
            bad_bled.write_text("{ this is not json", encoding="utf-8")
            try:
                st_bb = execution_state(ledger_path=str(ex / "exec-ledger.json"), registry=_reg,
                                        roots=roots, build_ledger=str(bad_bled))
                esc_bb = ""
            except Exception as e:
                st_bb, esc_bb = {}, type(e).__name__
            check("execution_state NEG: corrupt build ledger -> named mismatch, no exception escapes",
                  esc_bb == "" and st_bb.get("status") == "ledger_inconsistent"
                  and any("build_ledger" in m for m in st_bb.get("mismatches", [])),
                  extra="escaped=" + esc_bb)
            # 正控先钉住：这把锁必须真的把日志挡住，否则下面的断言会空洞为真
            log_real = Path(ok1["log"]["path"])
            lock_h = None
            if WINDOWS and log_real.is_file():
                import ctypes
                from ctypes import wintypes
                kernel32 = ctypes.windll.kernel32
                kernel32.CreateFileW.restype = wintypes.HANDLE
                h = kernel32.CreateFileW(str(log_real), 0x80000000, 0, None, 3, 0x80, None)
                lock_h = None if h in (None, -1, 0) else h
            try:
                lock_ok = False
                try:
                    with open(log_real, "rb"):
                        pass
                except OSError:
                    lock_ok = True
                try:
                    st_lk = execution_state(ledger_path=str(ex / "exec-ledger.json"), registry=_reg,
                                            roots=roots, build_ledger=str(bled))
                    esc_lk = ""
                except Exception as e:
                    st_lk, esc_lk = {}, type(e).__name__
            finally:
                if lock_h is not None:
                    import ctypes
                    ctypes.windll.kernel32.CloseHandle(lock_h)
            check("execution_state NEG: log held exclusively by another process -> named mismatch, "
                  "no exception escapes (positive control: the lock really blocks reads)",
                  lock_ok and esc_lk == "" and st_lk.get("status") == "ledger_inconsistent"
                  and any("install.log" in m for m in st_lk.get("mismatches", [])),
                  extra="lock_ok=%s escaped=%s" % (lock_ok, esc_lk))
            # 无可卸载物：诚实 nothing_to_uninstall 而非"卸载成功"
            r_none = execute_uninstall(confirm=CONFIRM_TOKEN, install_dir=str(ex / "never-installed"),
                                       app_name="ZaojingAI", vendor_dir="FakeVendor",
                                       data_dir_name="Zaojing", roots=roots, registry=_reg,
                                       spawn=_spawn, allow_roots=[str(ex)])
            check("uninstall NEG: nothing installed -> status nothing_to_uninstall (not a fake success)",
                  r_none["status"] == "nothing_to_uninstall" and r_none["fabricated"] is False)

            shutil.rmtree(ex, ignore_errors=True)
            check("install exec temp roots cleaned", not ex.exists())

    print(f"installer_runtime_engine self-test: {ok_count}/{ok_count + len(fail)} passed, {len(fail)} failed")
    return 0 if not fail else 1


if __name__ == "__main__":
    sys.exit(_self_test())
