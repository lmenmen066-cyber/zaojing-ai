"""
造境 AI 设计智能体 V1.0 - 主服务程序（全功能真实集成版）
========================================================

所有端点均接入真实功能引擎，无 mock 数据:
    - License        → license_service.LicenseManager (P0-1 修复版，支持降级启动)
    - 配置/路径/安全  → config_persistence (P0-2) + security_engine (P1 安全加固)
    - 素材库         → asset_engine.AssetEngine (持久化索引 + 向量搜索 + License 追踪)
    - 印前检查       → print_qa_engine.PreflightService (9 项检查规则 + 报告 + 自动修复)
    - 工作流/批处理  → workflow_engine.WorkflowEngine (节点图执行引擎)
    - AI 生成路由    → model_router.ModelRouter (成本/延迟/隐私优先路由 + 熔断 + 预算)
    - 去背景         → PIL 真实像素处理 (边缘泛洪算法)
    - 能力真值表     → Registry 2.0: 逐项 engine/health_ready/测试计数/verified
      由 _MODULES 与磁盘最新评估报告实时派生，清单不写死任何证据（§2 红线机制化）

运行方式:
    python agent_service.py
    或打包后直接运行 ZaojingAI.exe（自动检测便携版 portable.flag）
"""

from __future__ import annotations

import sys
import os

# Windows GBK 控制台防崩溃（打包后任何打印都不能导致进程退出）
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="backslashreplace")
    except Exception:
        pass

import asyncio
import inspect
import json
import logging
import math
import mimetypes
import shutil
import threading
import time
import uuid
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

APP_VERSION = "1.0.8"
START_TIME = time.time()

# 安装版数据根目录名（%LOCALAPPDATA%\Zaojing）：与 resolve_data_root 的第三分支同源，
# 单一真相源，避免"清单校验用的数据根"与"服务真实数据根"各写一份字面量而漂移。
INSTALLED_DATA_DIR_NAME = "Zaojing"
# 安装包 AppName：镜像 build-installer.py 的 APP_NAME。服务本体不依赖它，只有
# /installer/state 在**发现不到 setup.iss** 时用它做只读探测的定位名；能发现脚本时
# 一律以脚本里的 AppName/AppId 为准，并把二者是否一致如实回显（app_name_matches_script）。
DEFAULT_INSTALLER_APP_NAME = "ZaojingAI"
# 安装根尾名的**镜像常量**：发现得到 setup.iss 时一律以脚本的 DefaultDirName 尾段为准
# （见 _execution_registry_keys 的 vendor_dir/vendor_dir_source），发现不到时才退回这里，
# 与 INSTALLED_DATA_DIR_NAME 同理——镜像允许，但镜像必须标明自己是镜像并在使用处回显来历。
DEFAULT_INSTALLER_VENDOR_DIR = "造境科技"
# 显式同意令牌的镜像：CLI 的 --help 文案要在引擎加载前就能打印，故此处留一份字面量；
# _install_exec_cli() 每次运行都拿 eng.CONFIRM_TOKEN 对一次，不等就大声报 token_mirror_drift，
# 免得文案里的令牌与门里认的那枚各写一份而悄悄分家（同一类"随包文档落后于实况"的坑）。
INSTALLER_CONFIRM_TOKEN = "ZAOJING-CONFIRM-EXECUTE-INSTALLER"

# ============================================================================
# 路径解析（支持 PyInstaller frozen / 便携版 / 开发模式三种形态）
# ============================================================================

def app_base_dir() -> Path:
    """EXE 或脚本所在目录"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def is_portable() -> bool:
    """便携版检测：EXE 同目录存在 portable.flag 即数据全部落在本地 Data\\ 内"""
    return (app_base_dir() / "portable.flag").exists()


def installed_data_root() -> Path:
    """安装版（非便携、无环境变量覆盖）时的数据根：%LOCALAPPDATA%\\Zaojing。
    安装清单校验拿它对照 setup.iss 的 [Dirs]，与当前进程 DATA_ROOT 区分开——
    便携版/测试沙箱下 DATA_ROOT 是别处，那不代表安装器该建的目录变了。"""
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    return Path(base) / INSTALLED_DATA_DIR_NAME


def resolve_data_root() -> Path:
    """数据根目录: 环境变量覆盖 > 便携版本地目录 > %LOCALAPPDATA%\\Zaojing"""
    env = os.environ.get("ZAOJING_DATA_DIR")
    if env:
        root = Path(env)
    elif is_portable():
        root = app_base_dir() / "Data"
    else:
        root = installed_data_root()
    root.mkdir(parents=True, exist_ok=True)
    return root


DATA_ROOT = resolve_data_root()
LOGS_DIR = DATA_ROOT / "logs"
LOGS_DIR.mkdir(parents=True, exist_ok=True)  # 必须先于 FileHandler


def resolve_webui_dir():
    """WebUI 静态资源目录（Phase 35）：PyInstaller 内嵌(_MEIPASS) > EXE/脚本同目录。"""
    cands = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        cands.append(Path(meipass) / "webui")
    cands.append(app_base_dir() / "webui")
    for c in cands:
        if (c / "index.html").is_file():
            return c
    return None


def resolve_installer_script():
    """安装清单脚本 setup.iss 的定位（Phase 49）：_MEIPASS > EXE/脚本同目录。

    与 resolve_webui_dir 同一解析次序，但**返回 None 是常态而非缺陷**：setup.iss 是
    build-installer.py 在构建目录里生成的安装器脚本，PyInstaller 从未把它打进包内
    （包内没有 dist/ 与两份 .md，打进去只会让校验器报出与真实构建无关的缺失），
    因此冻结 EXE 上本函数返回 None，/installer/manifest/verify 如实 404。
    """
    cands = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        cands.append(Path(meipass) / "setup.iss")
    cands.append(app_base_dir() / "setup.iss")
    for c in cands:
        if c.is_file():
            return c
    return None

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler(LOGS_DIR / "agent.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("zaojing.service")

# ============================================================================
# 引擎模块接入（静态导入保证 PyInstaller 依赖分析可收集；失败时优雅降级）
# ============================================================================

_MODULES: Dict[str, Any] = {}
_IMPORT_ERRORS: Dict[str, str] = {}

try:
    import license_service as _license_mod
    _MODULES["license"] = _license_mod
except Exception as _e:  # pragma: no cover
    _IMPORT_ERRORS["license"] = f"{type(_e).__name__}: {_e}"

try:
    import config_persistence as _config_mod
    _MODULES["config"] = _config_mod
except Exception as _e:  # pragma: no cover
    _IMPORT_ERRORS["config"] = f"{type(_e).__name__}: {_e}"

try:
    import asset_engine as _asset_mod
    _MODULES["asset"] = _asset_mod
except Exception as _e:  # pragma: no cover
    _IMPORT_ERRORS["asset"] = f"{type(_e).__name__}: {_e}"

try:
    import print_qa_engine as _print_qa_mod
    _MODULES["print_qa"] = _print_qa_mod
except Exception as _e:  # pragma: no cover
    _IMPORT_ERRORS["print_qa"] = f"{type(_e).__name__}: {_e}"

try:
    import workflow_engine as _workflow_mod
    _MODULES["workflow"] = _workflow_mod
except Exception as _e:  # pragma: no cover
    _IMPORT_ERRORS["workflow"] = f"{type(_e).__name__}: {_e}"

try:
    import model_router as _model_router_mod
    _MODULES["model_router"] = _model_router_mod
except Exception as _e:  # pragma: no cover
    _IMPORT_ERRORS["model_router"] = f"{type(_e).__name__}: {_e}"

try:
    import security_engine as _security_mod
    _MODULES["security"] = _security_mod
except Exception as _e:  # pragma: no cover
    _IMPORT_ERRORS["security"] = f"{type(_e).__name__}: {_e}"

try:
    import recovery_engine as _recovery_mod
    _MODULES["recovery"] = _recovery_mod
except Exception as _e:  # pragma: no cover
    _IMPORT_ERRORS["recovery"] = f"{type(_e).__name__}: {_e}"

try:
    import knowledge_rag_engine as _knowledge_mod
    _MODULES["knowledge"] = _knowledge_mod
except Exception as _e:  # pragma: no cover
    _IMPORT_ERRORS["knowledge"] = f"{type(_e).__name__}: {_e}"

try:
    import typography_engine as _typography_mod
    _MODULES["typography"] = _typography_mod
except Exception as _e:  # pragma: no cover
    _IMPORT_ERRORS["typography"] = f"{type(_e).__name__}: {_e}"

try:
    import design_review_engine as _review_mod
    _MODULES["review"] = _review_mod
except Exception as _e:  # pragma: no cover
    _IMPORT_ERRORS["review"] = f"{type(_e).__name__}: {_e}"

try:
    import revision_engine as _revision_mod
    _MODULES["revision"] = _revision_mod
except Exception as _e:  # pragma: no cover
    _IMPORT_ERRORS["revision"] = f"{type(_e).__name__}: {_e}"

try:
    import color_engine as _color_mod
    _MODULES["color"] = _color_mod
except Exception as _e:  # pragma: no cover
    _IMPORT_ERRORS["color"] = f"{type(_e).__name__}: {_e}"

try:
    import layout_engine as _layout_mod
    _MODULES["layout"] = _layout_mod
except Exception as _e:  # pragma: no cover
    _IMPORT_ERRORS["layout"] = f"{type(_e).__name__}: {_e}"

try:
    import packaging_engine as _packaging_mod
    _MODULES["packaging"] = _packaging_mod
except Exception as _e:  # pragma: no cover
    _IMPORT_ERRORS["packaging"] = f"{type(_e).__name__}: {_e}"

try:
    import update_engine as _update_mod
    _MODULES["update"] = _update_mod
except Exception as _e:  # pragma: no cover
    _IMPORT_ERRORS["update"] = f"{type(_e).__name__}: {_e}"

try:
    import nas_engine as _nas_mod
    _MODULES["nas"] = _nas_mod
except Exception as _e:  # pragma: no cover
    _IMPORT_ERRORS["nas"] = f"{type(_e).__name__}: {_e}"

try:
    import ai_provider_engine as _provider_mod
    _MODULES["provider"] = _provider_mod
except Exception as _e:  # pragma: no cover
    _IMPORT_ERRORS["provider"] = f"{type(_e).__name__}: {_e}"

try:
    import adobe_bridge_engine as _adobe_bridge_mod
    _MODULES["adobe_bridge"] = _adobe_bridge_mod
except Exception as _e:  # pragma: no cover
    _IMPORT_ERRORS["adobe_bridge"] = f"{type(_e).__name__}: {_e}"

try:
    import evaluation_engine as _evaluation_mod
    _MODULES["evaluation"] = _evaluation_mod
except Exception as _e:  # pragma: no cover
    _IMPORT_ERRORS["evaluation"] = f"{type(_e).__name__}: {_e}"

try:
    import brand_engine as _brand_mod
    _MODULES["brand"] = _brand_mod
except Exception as _e:  # pragma: no cover
    _IMPORT_ERRORS["brand"] = f"{type(_e).__name__}: {_e}"

try:
    import installer_runtime_engine as _runtime_mod
    _MODULES["runtime"] = _runtime_mod
except Exception as _e:  # pragma: no cover
    _IMPORT_ERRORS["runtime"] = f"{type(_e).__name__}: {_e}"

try:
    import design_compare_engine as _compare_mod
    _MODULES["compare"] = _compare_mod
except Exception as _e:  # pragma: no cover
    _IMPORT_ERRORS["compare"] = f"{type(_e).__name__}: {_e}"

try:
    import background_removal_engine as _bg_removal_mod
    _MODULES["bg_removal"] = _bg_removal_mod
except Exception as _e:  # pragma: no cover
    _IMPORT_ERRORS["bg_removal"] = f"{type(_e).__name__}: {_e}"

try:
    import panel_recognition_engine as _panel_recog_mod
    _MODULES["panel_recognition"] = _panel_recog_mod
except Exception as _e:  # pragma: no cover
    _IMPORT_ERRORS["panel_recognition"] = f"{type(_e).__name__}: {_e}"

try:
    import installer_manifest_engine as _manifest_mod
    _MODULES["installer_manifest"] = _manifest_mod
except Exception as _e:  # pragma: no cover
    _IMPORT_ERRORS["installer_manifest"] = f"{type(_e).__name__}: {_e}"

try:
    # Phase 50 第 27 引擎：桌面 Hub 是**客户端**模块，服务端进程只静态导入以便
    # /health 与 Registry 如实自报"该能力已交付"，绝不在服务进程里建窗（tkinter 在
    # open_hub() 内延迟导入，故服务启动不付 tcl/tk 的加载代价，也不依赖显示会话）。
    import desktop_hub_engine as _desktop_hub_mod
    _MODULES["desktop_hub"] = _desktop_hub_mod
except Exception as _e:  # pragma: no cover
    _IMPORT_ERRORS["desktop_hub"] = f"{type(_e).__name__}: {_e}"

try:
    # Phase 80 第 28 引擎：服务侧持久化配置（feature_flags / tool_registry / skill_runtime）
    # 逐字下沉为可导入模块，本服务改为委托它（单一真相源，无平行副本），评测台再以三枚
    # 黄金用例真实驱动本引擎实例——三能力由此从 no_golden_cases_yet 转 verified。
    import service_registry_engine as _service_registry_mod
    _MODULES["service_registry"] = _service_registry_mod
except Exception as _e:  # pragma: no cover
    _IMPORT_ERRORS["service_registry"] = f"{type(_e).__name__}: {_e}"

try:
    # Phase 81 第 29 引擎：WebSocket 实时事件流（EventHub + 环形历史 + 运行时台账 +
    # 死连接摘除）逐字下沉，本服务改为委托 event_engine.hub() / event_engine.emit()——
    # 单一真相源，评测台以 websocket_events 黄金域真驱动本引擎实例；
    # websocket_events 由此从 no_golden_cases_yet 转 verified（与下一枚同批）。
    import event_engine as _event_mod
    _MODULES["event"] = _event_mod
except Exception as _e:  # pragma: no cover
    _IMPORT_ERRORS["event"] = f"{type(_e).__name__}: {_e}"

try:
    # Phase 81 第 30 引擎：一键交付包磁盘工作（复制 + SHA256 + manifest 原子落盘 +
    # README + ZIP + 拒收三形制台账）逐字下沉，本服务 delivery_package 端点
    # 只保留 classify_input_path 的路径合法性判定（依赖 security_engine），把磁盘
    # 工作整体委托 delivery_engine.DeliveryEngine 实例（服务进程绑 DATA_ROOT、评测实例
    # 绑私有沙箱）；评测台以 delivery_package 黄金域真驱动本引擎，
    # delivery_package 由此从 no_golden_cases_yet 转 verified。
    import delivery_engine as _delivery_mod
    _MODULES["delivery"] = _delivery_mod
except Exception as _e:  # pragma: no cover
    _IMPORT_ERRORS["delivery"] = f"{type(_e).__name__}: {_e}"

# /health 与 diagnostics 的引擎自报口径：单一真相源，新增引擎必须同步登记，
# 防"health 自述遗漏已注册引擎"的自报失真（Phase 26 修复 evaluation/brand 漏报）
_ENGINE_NAMES = [
    "license", "config", "asset", "print_qa", "workflow", "model_router", "security",
    "recovery", "knowledge", "typography", "review", "revision", "color", "layout",
    "packaging", "update", "nas", "provider", "adobe_bridge", "evaluation", "brand", "runtime",
    "compare", "bg_removal", "panel_recognition", "installer_manifest", "desktop_hub",
    "service_registry", "event", "delivery",
]

try:
    import psutil
except Exception:  # pragma: no cover
    psutil = None


def _require(name: str):
    mod = _MODULES.get(name)
    if mod is None:
        raise HTTPException(
            status_code=503,
            detail=f"{name} 引擎不可用: {_IMPORT_ERRORS.get(name, '未加载')}",
        )
    return mod


# ============================================================================
# 单例：配置 / 安全 / 各引擎（懒加载，初始化错误转 503 而不是崩溃）
# ============================================================================

_state: Dict[str, Any] = {}


def get_settings():
    if "settings" not in _state:
        cfg_mod = _require("config")
        if is_portable() or os.environ.get("ZAOJING_DATA_DIR"):
            cfg = cfg_mod.UserSettingsManager(
                config_path=DATA_ROOT / "config" / "user-settings.json"
            )
        else:
            cfg = cfg_mod.UserSettingsManager()
        _state["settings"] = cfg
    return _state["settings"]


def get_path_checker():
    """P0-2 + P1 路径安全：系统目录禁入 / 路径穿越 / 符号链接 / 双扩展名"""
    if "path_checker" not in _state:
        sec_mod = _require("security")
        blocked = [str(p) for p in get_settings().get_blocked_paths()]
        _state["path_checker"] = sec_mod.EnhancedPathSecurityChecker(blocked)
    return _state["path_checker"]


def validate_file_path(file_path: str) -> Path:
    """所有涉及用户提供的路径的端点都必须经过此校验"""
    try:
        checker = get_path_checker()
        blocked = checker.is_blocked(file_path)
    except HTTPException:
        blocked = False  # 安全引擎缺失时不阻断（在 diagnostics 中如实报告）
    if blocked:
        raise HTTPException(status_code=403, detail=f"路径被安全策略阻止: {file_path}")
    p = Path(file_path)
    if not p.exists():
        raise HTTPException(status_code=404, detail=f"文件不存在: {file_path}")
    return p


# ----------------------------------------------------------------------------
# 输入可达性（Phase 55 · §2 红线：路径消费面必须先过门，再谈"检查/修复/归档"）
#
# 修前实测（p55_gate_shapes.json）：同一串输入，已接门的 /assets 与
# /generate/remove-background 分别回 403/404/400，而 /preflight 与
# /preflight/auto-fix 一律回 HTTP 200——其中绝对穿越 C:\Windows\win.ini 甚至被
# 解析成一份言之凿凿的印前报告（PRINT-001"文档使用 RGB…"等 7 条发现）。安全策略
# 明令禁入的文件被读进去、还被判成了设计稿，这是本仓最靠前的输入面在替用户"猜"。
#
# 口径：门在引擎之前 ⇒ 传输层状态码即诚实信号（403 禁入 / 404 缺件 / 400 非文件），
# 而 200 报告必须自带机器可读的可达性判定，令批处理等下游消费者不必再模式匹配
# 引擎的中文诊断串（print_qa 把缺件吞成 PRINT-999 的旧依赖保留为第二道防线）。
#
# 留下的缝隙（本轮不越权改）：validate_file_path 只查 exists()，不查 is_file()，
# 故其余 12 处调用点喂目录仍会 500（remove-background 实测 500）。把 is_file() 加进
# 共用门需先审计 14 处是否有合法目录输入，超出本轮批准范围，故此处只在本族内收口，
# 并把该缝隙如实写进报告作为下一轮候选。
# ----------------------------------------------------------------------------

UNREADABLE_PATH_DETAIL = "路径不是可读文件（目录、驱动器根或特殊节点）"


def validate_readable_file(file_path: str) -> Path:
    """共用安全门 + "必须是文件"两道校验，供印前族（check / auto-fix）使用。"""
    p = validate_file_path(file_path)          # 403 被策略阻止 / 404 不存在
    if not p.is_file():                        # 空串在此变成 Path('.')，目录在此现形
        raise HTTPException(status_code=400, detail=f"{UNREADABLE_PATH_DETAIL}: {file_path}")
    return p


def input_reachability(p: Path) -> Dict[str, Any]:
    """过门之后的实物判定——每个字段都从磁盘读出，不是请求参数的复述。"""
    st = p.stat()
    return {
        "path": str(p),
        "reachable": True,
        "is_file": True,
        "verdict": "checked",
        "size_bytes": st.st_size,
        "modified_at": datetime.fromtimestamp(st.st_mtime).isoformat(),
    }


def classify_input_path(file_path: str):
    """把"这条路走不走得通"拆成三种责任不同的形制，供交付面逐文件落账。

    返回 (Path | None, reason | None, detail | None)，reason ∈
    not_found（文件不在了，用户侧）/ blocked_by_policy（安全引擎禁入，策略侧）/
    not_a_file（目录、空串等非普通文件，输入形制侧）。修前只有一枚
    missing_or_blocked，把前两种压成一个词，目录与空串更直接把整个请求打到 500。
    """
    try:
        p = validate_file_path(file_path)
    except HTTPException as e:
        if e.status_code == 403:
            return None, "blocked_by_policy", str(e.detail)
        if e.status_code == 404:
            return None, "not_found", str(e.detail)
        return None, "not_a_file", str(e.detail)
    if not p.is_file():
        return None, "not_a_file", f"{UNREADABLE_PATH_DETAIL}: {file_path}"
    return p, None, None


def _inside_data_root(p: Path) -> bool:
    """源文件是否落在服务自报的数据根之内（以 /health.data_root 同一口径计算）。"""
    try:
        return p.resolve().is_relative_to(Path(str(DATA_ROOT)).resolve())
    except Exception:
        return False


def get_asset_engine():
    if "asset" not in _state:
        mod = _require("asset")
        if is_portable() or os.environ.get("ZAOJING_DATA_DIR"):
            root = DATA_ROOT / "Assets"
        else:
            root = get_settings().resolve_local_root()
        _state["asset"] = mod.AssetEngine(root)
    return _state["asset"]


def get_preflight_service():
    if "preflight" not in _state:
        mod = _require("print_qa")
        _state["preflight"] = mod.PreflightService()
    return _state["preflight"]


# ----------------------------------------------------------------------------
# 工作流跨引擎工具面（Phase 51 · 血缘层配套）
#
# 背景：Phase 51 尖峰实证——27 跳跨引擎值血缘链在 HTTP 上全通，但**工作流图**只能
# 触到 asset 引擎的三个工具（embed_text / cosine / asset_count），其余 26 个引擎
# 一个都编排不了，"编排层"名不副实。
#
# 修法（不重实现任何算法）：每个工具都**转调本模块已有的端点协程**，于是工作流里的
# 一跳与 HTTP 上的一跳共用同一份参数校验（pydantic 模型）、同一份安全策略
# （validate_file_path）、同一份副作用与同一份事件发射；唯一差别是 route_hits 不
# 计数（没有真实 HTTP 请求）。工具**绝不**新造算法、绝不伪造 AI 结果：引擎如实抛错
# 就让节点如实失败（RuntimeError/HTTPException 原样冒出，由工作流执行器记账）。
#
# 有意不纳入的端点及理由（不臆造能力）：
#   · /uxp/*                     协程签名带 starlette Request，工具语境无请求对象可给
#   · /assets/{id}/thumbnail     返回 FileResponse 二进制流，不是可入变量的值
#   · /workflow/define、/workflow/{id}/execute   避免编排层自递归
#   · /nas/*、/update/*、/provider/*、/license/*、/config/*
#                                外部系统与凭据写侧，按 P45 写侧接线五条件交由控制台
#                                显式点击，不进无人值守的图执行
# 清单端点 GET /api/v1/workflow/tools 由 tool_registry **实时派生**（逐工具参数契约
# 取自 pydantic 模型字段与协程签名），绝无手写清单可漂移。
# ----------------------------------------------------------------------------

# (工具名, 归属引擎域, 端点协程名, 请求模型类名 或 None)
_WORKFLOW_TOOL_SPECS = (
    # —— 素材 / 去背 / 印前 ——
    ("assets.add", "asset", "add_asset", "AssetAddRequest"),
    ("assets.search", "asset", "search_assets", "AssetSearchRequest"),
    ("assets.get", "asset", "get_asset", None),
    ("assets.list", "asset", "list_assets", None),
    ("assets.delete", "asset", "delete_asset", None),
    ("assets.set_license", "asset", "set_asset_license", "AssetLicenseRequest"),
    ("generate.remove_background", "bg_removal", "remove_background", "RemoveBackgroundRequest"),
    ("preflight.run", "print_qa", "run_preflight", "PreflightRequest"),
    ("preflight.auto_fix", "print_qa", "preflight_auto_fix", "PreflightRequest"),
    ("preflight.history", "print_qa", "preflight_history", None),
    # —— 对比 / 交付 / 批量 / 恢复 ——
    ("design.compare", "compare", "design_compare", "DesignCompareRequest"),
    ("delivery.package", "delivery", "delivery_package", "DeliveryPackageRequest"),
    ("batch.process", "batch", "batch_process", "BatchProcessRequest"),
    ("recovery.snapshot", "recovery", "recovery_create_snapshot", "SnapshotCreateRequest"),
    ("recovery.restore", "recovery", "recovery_restore_snapshot", None),
    ("recovery.snapshots", "recovery", "recovery_list_snapshots", None),
    # —— 品牌 / 知识 ——
    ("brand.palette", "brand", "brand_palette", "BrandPaletteRequest"),
    ("brand.color_consistency", "brand", "brand_color_consistency", "BrandConsistencyRequest"),
    ("brand.logo_geometry", "brand", "brand_logo_geometry", "BrandLogoGeometryRequest"),
    ("brand.semantics", "brand", "brand_semantics", None),
    ("knowledge.ingest", "knowledge", "knowledge_ingest", "KnowledgeIngestRequest"),
    ("knowledge.retrieve", "knowledge", "knowledge_retrieve", "KnowledgeRetrieveRequest"),
    ("knowledge.list", "knowledge", "knowledge_list", None),
    # —— 排版 / 评审 / 改稿 ——
    ("typography.scan", "typography", "typography_scan", None),
    ("typography.fonts", "typography", "typography_fonts", None),
    ("typography.metrics", "typography", "typography_metrics", "FontMetricsRequest"),
    ("typography.match", "typography", "typography_match", "FontMatchRequest"),
    ("typography.resolve", "typography", "typography_resolve", "ResolveFontRequest"),
    ("typography.fallback_rules", "typography", "typography_fallback_rules", None),
    ("typography.alias_map", "typography", "typography_alias_map", None),
    ("review.run", "review", "design_review_run", "DesignReviewRequest"),
    ("revision.parse", "revision", "revision_parse", "RevisionParseRequest"),
    ("revision.apply", "revision", "revision_apply", "RevisionApplyRequest"),
    # —— 色彩 / 布局 ——
    ("color.palette", "color", "color_palette", "ColorPaletteRequest"),
    ("color.harmony", "color", "color_harmony", "ColorHarmonyRequest"),
    ("color.contrast", "color", "color_contrast", "ColorContrastRequest"),
    ("color.convert", "color", "color_convert", "ColorConvertRequest"),
    ("color.delta_e", "color", "color_delta_e", "DeltaERequest"),
    ("color.nearest", "color", "color_nearest", "NearestPaletteRequest"),
    ("color.pantone", "color", "color_pantone", "PantoneRequest"),
    ("layout.collide", "layout", "layout_collide", "LayoutCollideRequest"),
    ("layout.align", "layout", "layout_align", "LayoutAlignRequest"),
    ("layout.distribute", "layout", "layout_distribute", "LayoutDistributeRequest"),
    ("layout.safe_area", "layout", "layout_safe_area", "LayoutSafeAreaRequest"),
    ("layout.grid_snap", "layout", "layout_grid_snap", "LayoutGridSnapRequest"),
    ("layout.analyze", "layout", "layout_analyze", "LayoutAnalyzeRequest"),
    # —— 包装（刀版/拼版/VDP/面板识别/面板拓扑/条码）——
    ("packaging.dieline", "packaging", "packaging_dieline", "DielineRequest"),
    ("packaging.imposition", "packaging", "packaging_imposition", "ImpositionRequest"),
    ("packaging.vdp_merge", "packaging", "packaging_vdp_merge", "VdpMergeRequest"),
    ("packaging.panel_recognition", "panel_recognition", "packaging_panel_recognition", None),
    ("packaging.panel_topology", "packaging", "packaging_panel_topology", "PanelTopologyRequest"),
    ("packaging.barcode", "packaging", "packaging_barcode", "BarcodeRequest"),
    # —— 评测中心（只读 + 真跑）——
    ("eval.run", "evaluation", "eval_run", None),
    ("eval.suite", "evaluation", "eval_suite", None),
    ("eval.reports", "evaluation", "eval_reports", None),
    ("eval.report", "evaluation", "eval_report", None),
    ("eval.recommendations", "evaluation", "eval_recommendations", None),
    # —— 文档态 / 运行时观测 / 安装态 / 工作流只读自检 ——
    ("document.get", "document_store", "get_current_document", None),
    ("document.rename", "document_store", "rename_document", None),
    ("layers.list", "document_store", "get_layers", None),
    ("layers.create", "document_store", "create_layer", "LayerCreateRequest"),
    ("layers.delete", "document_store", "delete_layer", None),
    ("metrics.get", "runtime", "get_metrics", None),
    ("events.recent", "runtime", "events_recent", None),
    ("installer.manifest_verify", "installer_manifest", "installer_manifest_verify", None),
    ("installer.state", "installer_runtime", "installer_state", None),
    ("workflow.validate", "workflow", "workflow_validate", None),
    ("workflow.visualize", "workflow", "workflow_visualize", None),
)


def _wf_jsonable(obj):
    """把端点协程的返回值规整成可入工作流变量、可 JSON 序列化的值。
    不吞信息：未知类型退化为 str()，绝不静默丢弃或编造字段。"""
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, BaseModel):
        return _wf_jsonable(obj.model_dump())
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, dict):
        return {str(k): _wf_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [_wf_jsonable(v) for v in obj]
    if hasattr(obj, "__dict__"):
        return _wf_jsonable(vars(obj))
    return str(obj)


# 工作流变量入库的收敛规则；Phase 81 起事件账本入库由 event_engine.json_safe 独立实现
# （语义与本函数逐字对齐，两引擎解耦不相互引用），本别名仅供 workflow 侧继续复用。
_json_safe = _wf_jsonable


def _wf_type_str(ann) -> str:
    """类型标注 → 无损可读串。
    - 真类型（str/float/int/bool）取 __name__，不把内部 repr `<class 'str'>` 当契约暴露；
    - 参数化泛型（List[DielinePanel] / str | None）走 str() 保留完整参数化——
      typing.List[X].__name__ 会退化成 'List' 丢掉元素类型，那是抹平契约；
    - 剥掉 typing. / __main__. 前缀，使清单在源码进程与冻结 EXE 里读出同一串。"""
    if ann is inspect.Parameter.empty or ann is None:
        return "any"
    if isinstance(ann, type):
        return ann.__name__
    s = str(ann)
    return s.replace("typing.", "").replace("__main__.", "")


def _wf_default(v, required: bool):
    """默认值 → JSON 字面量。pydantic 的 PydanticUndefined / inspect.empty 是"无默认"
    的内部哨兵，绝不能 str() 成 'PydanticUndefined' 混进对外契约（那是把内部实现
    当数据暴露）；必填位如实记 None + required=True。"""
    if required:
        return None
    if v.__class__.__name__ == "PydanticUndefinedType" or repr(v) == "PydanticUndefined":
        return None
    return _wf_jsonable(v)


def _wf_param_list(scalar_params, model):
    """派生工具参数契约：协程标量形参（路径/查询位）+ 请求模型字段（body 位）。"""
    out = []
    for pn, p in scalar_params:
        required = p.default is inspect.Parameter.empty
        out.append({
            "name": pn,
            "in": "arg",
            "required": required,
            "default": None if required else _wf_jsonable(p.default),
            "type": _wf_type_str(p.annotation),
        })
    if model is not None:
        for fn_name, fi in model.model_fields.items():
            required = bool(fi.is_required())
            out.append({
                "name": fn_name,
                "in": "body",
                "required": required,
                "default": _wf_default(fi.default, required),
                "type": _wf_type_str(fi.annotation),
            })
    return out


def _wf_tag(fn, tool, domain, handler, model_name, params):
    """把派生用的元信息挂在工具函数自身上——清单端点只读 tool_registry，
    元信息随注册同时产生，不存在第二份可漂移的手写清单。"""
    fn._wf_meta = {
        "tool": tool,
        "domain": domain,
        "handler": handler,
        "request_model": model_name,
        "params": params,
        "coroutine": asyncio.iscoroutinefunction(fn),
    }
    return fn


def _make_workflow_tool(tool_name, domain, handler_name, model_name):
    """按 (协程名, 模型名) 生成一个转调端点协程的真实工具。
    绑定期即解析名字：写错处理器/模型名会在装配时立刻炸，而不是等到调用时才 500。"""
    handler = globals().get(handler_name)
    if not callable(handler):
        raise RuntimeError(f"工作流工具 {tool_name} 绑定的端点协程不存在: {handler_name}")
    sig = inspect.signature(handler)
    if "request" in sig.parameters:
        raise RuntimeError(f"工作流工具 {tool_name} 绑定的协程需要 starlette Request，不可作工具: {handler_name}")
    model = None
    if model_name:
        model = globals().get(model_name)
        if model is None or not hasattr(model, "model_fields"):
            raise RuntimeError(f"工作流工具 {tool_name} 绑定的请求模型不存在: {model_name}")
    scalar = [(pn, p) for pn, p in sig.parameters.items() if not (model_name and pn == "req")]

    async def _tool(**kw):
        args = {pn: kw[pn] for pn, _p in scalar if pn in kw}
        missing = [pn for pn, p in scalar
                   if pn not in args and p.default is inspect.Parameter.empty]
        if missing:
            raise RuntimeError(f"工具 {tool_name} 缺少参数: {missing}")
        rest = {k: v for k, v in kw.items() if k not in args}
        if model is not None:
            allowed = set(model.model_fields)
            unknown = sorted(k for k in rest if k not in allowed)
            if unknown:
                raise RuntimeError(
                    f"工具 {tool_name} 不接受参数 {unknown}（契约字段: {sorted(allowed)}）")
            args["req"] = model(**rest)
        elif rest:
            raise RuntimeError(f"工具 {tool_name} 不接受参数 {sorted(rest)}")
        res = handler(**args)
        if asyncio.iscoroutine(res):
            res = await res
        return _wf_jsonable(res)

    _tool.__name__ = "wf_tool_" + tool_name.replace(".", "_")
    return _wf_tag(_tool, tool_name, domain, handler_name, model_name,
                   _wf_param_list(scalar, model))


def get_workflow_engine():
    if "workflow" not in _state:
        mod = _require("workflow")
        engine = mod.WorkflowEngine()

        # 真实 AI 后端：经 ModelRouter 选路并调用（失败如实抛出，不伪造内容）
        async def _ai_invoker(prompt: str, model: Optional[str] = None,
                              provider: Optional[str] = None, **kwargs):
            r = get_model_router()
            model_name = model or next(iter(r.registry.models), None)
            if not model_name:
                raise RuntimeError("模型注册表为空，无法执行 AI 节点")
            pname = provider or r.select_provider(model_name, {"priority": "cost"})
            if not pname or pname not in r.registry.providers:
                raise RuntimeError(f"没有可用 Provider（模型 {model_name} 全部熔断/超预算）")
            resp = await r.registry.providers[pname].invoke(model_name, prompt, options=kwargs)
            return resp.get("content") if isinstance(resp, dict) else resp

        engine.register_ai_invoker(_ai_invoker)

        # 真实工具：文本向量 / 余弦 / 索引统计（复用素材引擎同一实现）
        asset_mod = _MODULES.get("asset")
        if asset_mod and hasattr(asset_mod, "embed_text"):
            _et = lambda text="", dims=512: asset_mod.embed_text(str(text), dims)
            engine.register_tool("embed_text", _wf_tag(
                _et, "embed_text", "asset", "asset_engine.embed_text", None,
                [{"name": "text", "in": "arg", "required": False, "default": "", "type": "str"},
                 {"name": "dims", "in": "arg", "required": False, "default": 512, "type": "int"}]))

        def _cosine(a: Optional[List[float]] = None, b: Optional[List[float]] = None):
            eng = get_asset_engine()
            return eng._cosine_similarity(a or [], b or [])

        def _asset_count():
            eng = get_asset_engine()
            return {"assets": len(eng.assets), "embeddings": len(eng.embeddings)}

        engine.register_tool("cosine", _wf_tag(
            _cosine, "cosine", "asset", "asset_engine.AssetEngine._cosine_similarity", None,
            _wf_param_list(list(inspect.signature(_cosine).parameters.items()), None)))
        engine.register_tool("asset_count", _wf_tag(
            _asset_count, "asset_count", "asset", "asset_engine.AssetEngine(assets/embeddings)",
            None, []))

        # Phase 51：跨引擎工具面 —— 逐条转调既有端点协程（同一份校验/副作用/事件）
        for _spec in _WORKFLOW_TOOL_SPECS:
            engine.register_tool(_spec[0], _make_workflow_tool(*_spec))

        # 桌面 Hub 只读快照：desktop_hub 是 CLI 引擎、无 HTTP 端点，直接转调其函数；
        # 阻塞 HTTP 放进线程，绝不卡住事件循环。
        hub_mod = _MODULES.get("desktop_hub")
        if hub_mod and hasattr(hub_mod, "hub_snapshot"):
            async def _hub_tool(base_url: Optional[str] = None, timeout: float = 5.0):
                return _wf_jsonable(await asyncio.to_thread(
                    hub_mod.hub_snapshot, base_url, timeout))

            _hub_tool.__name__ = "wf_tool_hub_snapshot"
            engine.register_tool("hub.snapshot", _wf_tag(
                _hub_tool, "hub.snapshot", "desktop_hub", "desktop_hub_engine.hub_snapshot", None,
                [{"name": "base_url", "in": "arg", "required": False, "default": None, "type": "Optional[str]"},
                 {"name": "timeout", "in": "arg", "required": False, "default": 5.0, "type": "float"}]))

        _state["workflow"] = engine
    return _state["workflow"]


def get_model_router():
    if "router" not in _state:
        mod = _require("model_router")
        registry_dir = DATA_ROOT / "models"
        registry_dir.mkdir(parents=True, exist_ok=True)
        registry = mod.ModelRegistry(str(registry_dir / "registry.yaml"))
        _state["router"] = mod.ModelRouter(registry)
    return _state["router"]


def get_license_manager():
    if "license" not in _state:
        mod = _require("license")
        try:
            mod.configure_store(str(DATA_ROOT / "state" / "license.json"))
        except Exception as e:
            logger.warning(f"license store 配置失败(不影响内存激活): {e}")
        _state["license"] = mod.LicenseManager.get_instance()
    return _state["license"]


def get_recovery_engine():
    """快照 / 事务 / 任务状态恢复引擎（§51/§52/§73 · 核心能力#7 版本回滚）"""
    if "recovery" not in _state:
        mod = _require("recovery")
        rec_root = DATA_ROOT / "recovery"
        snapshot_dir = rec_root / "snapshots"
        state_dir = rec_root / "states"
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        state_dir.mkdir(parents=True, exist_ok=True)
        _state["recovery"] = mod.CrashRecoveryEngine(snapshot_dir, state_dir)
    return _state["recovery"]


def get_knowledge_engine():
    """知识库 / RAG 检索引擎（§65/§66 · 真实文档抽取 + 分块 + 向量检索）"""
    if "knowledge" not in _state:
        mod = _require("knowledge")
        store = DATA_ROOT / "knowledge"
        store.mkdir(parents=True, exist_ok=True)
        _state["knowledge"] = mod.KnowledgeEngine(store)
    return _state["knowledge"]


def get_typography_engine():
    """字体排版引擎（§43/§111 · fontTools 真实扫描/度量/缺失字体匹配）"""
    if "typography" not in _state:
        mod = _require("typography")
        store = DATA_ROOT / "typography"
        store.mkdir(parents=True, exist_ok=True)
        _state["typography"] = mod.TypographyEngine(store)
    return _state["typography"]


def get_review_engine():
    """多角色设计评审引擎（§49 · 真实 WCAG/越界/字号/重叠 规则）"""
    if "review" not in _state:
        mod = _require("review")
        store = DATA_ROOT / "reviews"
        store.mkdir(parents=True, exist_ok=True)
        _state["review"] = mod.DesignReviewEngine(store)
    return _state["review"]


def get_revision_engine():
    """客户改稿引擎（§48/§114 · 确定性指令→操作→定位→冲突→应用）"""
    if "revision" not in _state:
        mod = _require("revision")
        _state["revision"] = mod.RevisionEngine()
    return _state["revision"]


def get_color_engine():
    """色彩引擎（§44 · 真实调色板/和谐/对比/CMYK/Lab-ΔE；Pantone 诚实拒绝伪造）"""
    if "color" not in _state:
        mod = _require("color")
        store = DATA_ROOT / "color"
        store.mkdir(parents=True, exist_ok=True)
        _state["color"] = mod.ColorEngine(store)
    return _state["color"]


def get_layout_engine():
    """布局排版引擎（§45 · 碰撞/对齐/分布/安全区/网格/宽高比 真实几何）"""
    if "layout" not in _state:
        mod = _require("layout")
        _state["layout"] = mod.LayoutEngine()
    return _state["layout"]


def get_packaging_engine():
    """包装与变量数据引擎（§46/§47 · 刀版/拼版/变量数据合并/条码 EAN-13·EAN-8/矢量面板拓扑 真实；
    栅格图→面板的 Panel Recognition 自 Phase 47 起由独立第 25 引擎 panel_recognition_engine 真实交付）"""
    if "packaging" not in _state:
        mod = _require("packaging")
        store = DATA_ROOT / "packaging"
        store.mkdir(parents=True, exist_ok=True)
        _state["packaging"] = mod.PackagingEngine(store)
    return _state["packaging"]


def get_panel_recognition_engine():
    """面板识别引擎（Phase 47 · 第 25 引擎：栅格刀版图 → 面板/cut-fold 线型/邻接拓扑的真实几何反解。
    Otsu 自算阈值二值化 + 轴对齐游程线抽取 + 共线间隙合并 + 虚线支撑屏障 + 游程并查集连通域，
    全程零 CV/ML 模型、零落盘副作用，阈值/覆盖率/面积/轮廓长全部随响应回显可逐项复算。
    能力边界如实写入 caveats：覆盖轴对齐矩形刀版，斜角/曲线不在能力内。"""
    if "panel_recognition" not in _state:
        mod = _require("panel_recognition")
        _state["panel_recognition"] = mod.PanelRecognitionEngine()
    return _state["panel_recognition"]


def get_update_engine():
    """自动更新引擎（§75-§77 · 真实更新客户端：拉清单+HMAC 验签+SHA 校验下载+原子替换/回滚；未配置 feed 诚实降级）"""
    if "update" not in _state:
        mod = _require("update")
        store = DATA_ROOT / "update"
        store.mkdir(parents=True, exist_ok=True)
        _state["update"] = mod.UpdateEngine(store)
    return _state["update"]


def get_nas_engine():
    """NAS 引擎（§81 · 真实客户端：文件系统/UNC/映射盘 经 OS 原生访问 + WebDAV PROPFIND；枚举/文件锁/断线重连/缓存 纯 stdlib 真做；未配置/不可达诚实降级）"""
    if "nas" not in _state:
        mod = _require("nas")
        store = DATA_ROOT / "nas"
        store.mkdir(parents=True, exist_ok=True)
        _state["nas"] = mod.NasEngine(store)
    return _state["nas"]


def get_provider_engine():
    """真实 AI Provider 引擎（§101/§102 · 通用 OpenAI-兼容客户端：真实 HTTP 往返 / DPAPI 密钥加密 /
    SSE 流式 / usage 记录 / 429·5xx 退避 + 熔断；§17 Planner 结构化任务规划；未配置 Provider 诚实 502，绝不伪造 AI 输出）"""
    if "provider" not in _state:
        mod = _require("provider")
        store = DATA_ROOT / "providers"
        store.mkdir(parents=True, exist_ok=True)
        _state["provider"] = mod.ProviderEngine(store)
    return _state["provider"]


def get_bridge_engine():
    """真实 Adobe UXP 桥接引擎（§8/§9/§24/§25/§26 · 交付并端到端验证的是"桥接机制"：
    握手 + 随机 Session Token 认证 + Origin/loopback 校验 + 端口动态发现 + §9 命令下发 +
    真实结果对账 + 超时/掉线诚实降级。服务端绝不伪造任何 Adobe 文档变更；ps.*/ai.* 文档写
    仍 needs_adobe_host——须真实带许可证的 Photoshop/Illustrator UXP 宿主执行）。"""
    if "adobe_bridge" not in _state:
        mod = _require("adobe_bridge")
        store = DATA_ROOT / "uxp"
        store.mkdir(parents=True, exist_ok=True)
        _state["adobe_bridge"] = mod.AdobeBridgeEngine(store)
    return _state["adobe_bridge"]


def get_evaluation_engine():
    """评估中心引擎（Phase 24 · 真实黄金用例评测：真实调用 color/layout/packaging 判分，
    负控校准用例保证判分器可证伪，报告真实 JSON 落盘可对比；agent/vision 类别如实 not_evaluated。
    Phase 27 起 brand 引擎亦注入，品牌真值用例（真实像素调色板/一致性/几何）纳入评测。
    Phase 28 起 print_qa/knowledge_rag 引擎亦注入，印前色彩·分辨率·出血真值与检索排序·作用域隔离真值纳入评测。
    Phase 30 起 revision/review/security 引擎亦注入，改稿指令矩阵·真实换算、评审缺陷/合规双向真值、
    路径·权限·高危不虚报三态真值纳入评测。
    Phase 31 起工作流真值纳入评测——workflow 引擎由评测台内部新构造而非注入本服务单例：
    零注册工具使"未注册即诚实失败"的错误文案精确可核，且引擎纯内存、评测零落盘副作用。
    Phase 32 起素材（asset）与崩溃恢复（recovery）真值纳入评测——两引擎同样由评测台逐用例
    在评测私有沙箱（DATA_ROOT/evaluation/_asset|_recovery，先清后建）全新构造而非注入单例：
    素材引擎的 _load_databases 会从磁盘复活旧状态，复用根目录将破坏跨用例确定性；恢复引擎
    逐用例固定字节夹具使 sha256/还原判分独立可复算。
    Phase 33 起排版（typography）真值纳入评测——本机字体在装状态是外因，评测台以自供夹具
    断链：fontTools FontBuilder 现场编译两枚真 TTF（Plane-15 专码位经真机字体反查排除撞车），
    溯源解析（引擎 A include_os=False 只扫夹具目录，同一真实解析路径、秒级完成不触本机字体，
    专码位不撞车性由 Phase 33 探针真机反查钉死）产出真 FontRecord，逐用例引擎 B 重放仅含夹具记录的索引
    （DATA_ROOT/evaluation/_typo，先清后建），度量/门槛/规则判分全走真实代码路径与真实字节，
    零桩且零本机字体干扰。
    Phase 34 起条码（barcode）与面板拓扑（topology）升格为独立黄金域纳入评测——二者与刀版/拼版
    同属 packaging 引擎（服务单例注入即可：纯函数、判分输入输出全显式），此前 6 条 production
    用例混编一类且白名单域为 None 无法逐能力挂证；本轮位串期望在评测源码内以 GS1 公开编码例
    逐字符手抄独立核对、像素真值直接采样渲染 PNG（采样后 close 释放句柄）、拓扑几何全字段精确。
    Phase 36 起授权（license）、配置持久化（config）与模型路由（model_routing）纳入评测——三域
    引擎由评测台自构而非注入本服务单例：签发/验签是模块级 HMAC 纯函数无状态可注入；配置管理器
    会从磁盘复活旧状态，逐用例在评测私有沙箱（DATA_ROOT/evaluation/_config|_router，先清后建）
    全新构造方可跨用例确定；路由注册表为评测自供 YAML 夹具走真实解析路径，熔断 create_task
    的无运行循环怪癖在独立线程语境钉死、判分与调用语境无关
    （Phase 60 就地改核：上面这句写于 Phase 36，当时把"无运行循环时 create_task 当场
    RuntimeError"当成被测真值钉进用例。该怪癖经取证判为**真缺陷**——同步方法里排异步复位任务，
    短命环下排好的任务随循环死掉 ⇒ 熔断永久开环；本轮已根治（create_task 退役，开环只写一枚
    单调时间戳、复口由读侧 can_execute() 惰性判定），同一枚用例现钉"无环也能开环与到点复口"。
    原句逐字留档不改写，判卷侧的期望值改由实测收据 p60_probe12_evalpayload.txt 派生）
    Phase 37 起自动更新（update）、NAS 挂载（nas）与 AI Provider 接入（provider）纳入评测——
    三域引擎同由评测台自构（模块即引擎，逐用例在评测私有沙箱 DATA_ROOT/evaluation/_update|
    _nas|_provider 先清后建全新构造）；网络面一律由评测自起的 127.0.0.1:0 临时端口进程内
    mock HTTP 服务器驱动（routes/seen 逐用例隔离、用毕必 shutdown，与本机在线服务零端口
    冲突），下载字节、缓存命中四元组、Bearer 头与熔断零 HTTP 均以真实文件与请求计数落证
    Phase 38 起设计版本对比（compare）与去背景（bg_removal）纳入评测——两能力原为本服务
    内联逻辑，本轮逐字下沉为第 23/24 引擎（design_compare_engine 纯函数零状态；
    background_removal_engine 真像素 BFS 泛洪，仅 PIL 参与），端点改薄包装、契约字段逐字
    保留（既有 L2/L4 端点断言即回归网）；compare 判分零 I/O 无沙箱，bg_removal 逐用例在评测
    私有 _bg 沙箱先清后建，夹具为评测自造 100×100 真像素图（被包围孔洞 alpha 必 255，
    证泛洪≠全局色键）"""
    if "evaluation" not in _state:
        mod = _require("evaluation")
        store = DATA_ROOT / "evaluation"
        store.mkdir(parents=True, exist_ok=True)
        _state["evaluation"] = mod.EvaluationEngine(
            store_dir=store,
            color=get_color_engine(),
            layout=get_layout_engine(),
            packaging=get_packaging_engine(),
            brand=get_brand_engine(),
            print_qa=get_preflight_service().doctor,
            knowledge_rag=get_knowledge_engine(),
            review=get_review_engine(),
            revision=get_revision_engine(),
            security=get_path_checker(),
        )
    return _state["evaluation"]


def get_brand_engine():
    """品牌智能引擎（Phase 25 · 真实像素分析：中位切分调色板、CIEDE2000 品牌色一致性、
    alpha 包围盒 Logo 几何；Phase 56 · 语义识别经真实 Provider 多模态透传：有 Provider 后端点时
    把 Logo 图逐字交给所配置模型、原样透传其输出，无 Provider 或未配置时如实 needs_external，
    绝不臆造调性/关键词/置信度）"""
    if "brand" not in _state:
        mod = _require("brand")
        # 注入真实 Provider 引擎以支持语义透传；Provider 不可用时退化为 None（引擎据实 needs_external）。
        try:
            provider = get_provider_engine()
        except Exception:  # pragma: no cover - Provider 模块缺失/初始化失败时的诚实降级
            provider = None
        _state["brand"] = mod.BrandEngine(color=get_color_engine(), provider=provider)
    return _state["brand"]


def get_runtime_guard_engine():
    """安装/运行时守护引擎（Phase 26 · 真实写入探针/磁盘余量/端口 bind/内核单实例锁，
    诊断状态由逐项真实检查派生；安装/卸载**执行器**自 Phase 58 起交付在本引擎里，只经
    CLI 显式同意令牌触发，HTTP 面走不到它——旧的 needs_installer_environment 原文留在
    install_actions()["retired_boundary"]["was"] 可对账）"""
    if "runtime" not in _state:
        mod = _require("runtime")
        _state["runtime"] = mod.RuntimeGuardEngine(data_root=DATA_ROOT)
    return _state["runtime"]


def get_installer_manifest_engine():
    """安装清单静态校验引擎（Phase 49 · 第 26 引擎）：模块即引擎（纯函数、零实例状态、
    零落盘），与 update/nas/provider 同一口径，故不做单例缓存——缓存一个模块对象无意义。"""
    return _require("installer_manifest")


# 只读校验脚本的体积上限：setup.iss 量级为数 KB，设 512 KB 足够宽松，
# 目的是拒绝把任意大文件喂进逐行正则解析（DoS 面），而不是提供"任意文件读取"。
_MAX_MANIFEST_BYTES = 512 * 1024


def _sha256_text(text: str) -> str:
    """清单文本的 SHA256。纯 CPU，但仍在 async 体内会被 L6-A1 判为占用事件循环，
    故与工作线程配对（Phase 52：把"循环上零未下沉哈希/磁盘调用"保持为无例外的机械规则）。"""
    import hashlib
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _read_manifest_script(script_path: Optional[str]):
    """定位并只读读取安装清单脚本，返回 (绝对路径, 文本)。

    安全边界（§2 责任红线：这是本服务唯一会被喂任意路径的只读解析入口）：
      1. 未给参数 → 走 resolve_installer_script()，发现不到即 404，绝不"凭空造一份脚本"；
      2. 给了参数 → 先过 validate_file_path（系统目录/穿越/符号链接 → 403，不存在 → 404），
         再要求真实后缀 .iss、真实是普通文件、体积 <= _MAX_MANIFEST_BYTES、
         且规范化后落在允许根（app_base_dir 或 _MEIPASS）之内 —— 越界一律 403，
         使"只校验本产品的安装清单"成为可断言的事实而非承诺。
    """
    mod = get_installer_manifest_engine()
    if not script_path or not str(script_path).strip():
        found = resolve_installer_script()
        if found is None:
            raise HTTPException(status_code=404, detail={
                "reason": "installer_script_not_found",
                "looked_in": [str(p) for p in _manifest_search_dirs()],
                "note": "setup.iss 由 build-installer.py 在构建目录生成，PyInstaller 未随包装载；"
                        "本端点如实报未找到，不伪造校验通过",
                "fabricated": False,
            })
        path = found
    else:
        path = validate_file_path(str(script_path))
        path = path.resolve()
        if path.suffix.lower() != ".iss":
            raise HTTPException(status_code=400, detail={
                "reason": "manifest_suffix_invalid", "path": str(path), "suffix": path.suffix,
                "fabricated": False})
        if not path.is_file():
            raise HTTPException(status_code=400, detail={
                "reason": "manifest_not_a_file", "path": str(path), "fabricated": False})
        if not _within_manifest_roots(path):
            raise HTTPException(status_code=403, detail={
                "reason": "manifest_path_outside_allowed_roots", "path": str(path),
                "allowed_roots": [str(r) for r in _manifest_allowed_roots()],
                "fabricated": False})
    size = path.stat().st_size
    if size > _MAX_MANIFEST_BYTES:
        raise HTTPException(status_code=400, detail={
            "reason": "manifest_too_large", "path": str(path), "size": size,
            "limit": _MAX_MANIFEST_BYTES, "fabricated": False})
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as e:
        raise HTTPException(status_code=400, detail={
            "reason": "manifest_not_utf8", "path": str(path), "error": str(e),
            "fabricated": False})
    try:
        mod.parse_script(text)  # 先探一次解析，坏输入由引擎原文抛 ManifestError
    except mod.ManifestError as e:
        raise HTTPException(status_code=400, detail={
            "reason": "manifest_unparseable", "path": str(path), "error": str(e),
            "fabricated": False})
    return path, text


def _manifest_allowed_roots() -> List[Path]:
    roots = [app_base_dir()]
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        roots.append(Path(meipass))
    return roots


def _within_manifest_roots(path: Path) -> bool:
    for root in _manifest_allowed_roots():
        try:
            path.relative_to(root.resolve())
            return True
        except Exception:
            continue
    return False


def _manifest_search_dirs() -> List[Path]:
    return [r / "setup.iss" for r in _manifest_allowed_roots()]



# ============================================================================
# 实时事件总线（§7/§10 · WebSocket task.* 事件流；真实广播，非模拟）
# ============================================================================

# ============================================================================
# WebSocket 实时事件流 — Phase 81 起 EventHub 类逐字下沉为第 29 引擎 event_engine
# （单一真相源：环形缓冲 / 台账累计 / 死连接摘除 / json_safe 归一化）。
# 本服务不再各写一份，_EVENT_HUB 指向 event_engine 的进程级单例；_emit 委托之。
# ws.accept() 属 transport 关注点，由 @app.websocket("/ws") 端点在 connect 前显式调用。
# ============================================================================

_EVENT_HUB = _event_mod.hub()


async def _emit(event: str, data: Dict[str, Any]) -> None:
    """向事件总线发布任务事件；无连接时仅入历史缓冲，绝不报错阻断业务。
    Phase 81 起逐字委托 event_engine.emit（异常吞并与原实现同口径，仅日志前缀换）。"""
    await _event_mod.emit(event, data)


# ============================================================================
# 文档状态存储（真实 JSON 持久化，图层增删改落盘）
# ============================================================================

class DocumentStore:
    """文档/图层状态持久化存储 — DATA_ROOT/state/document.json"""

    def __init__(self, path: Path, seed_loader=None, seed_path: Optional[str] = None):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            with open(self.path, "r", encoding="utf-8") as f:
                self.data = json.load(f)
        else:
            self.data = self._seed(seed_loader, seed_path)
            self.save()

    @staticmethod
    def _seed(seed_loader=None, seed_path: Optional[str] = None) -> Dict[str, Any]:
        # 诚实的"新建未命名文档"默认值 — 这是设计工作态的合法初值(空白画布默认 1 个背景层),
        # 不是对任何文件的伪造解析。真实导入某文档时由 seed_loader(seed_path) 覆盖。
        info: Dict[str, Any] = {
            "name": "未命名设计.psd",
            "color_mode": "RGB",
            "bit_depth": 8,
            "resolution": 72,
            "artboards": 1,
            "layers": [
                {
                    "id": str(uuid.uuid4())[:8],
                    "name": "Background",
                    "type": "image",
                    "visible": True,
                    "locked": False,
                    "opacity": 100.0,
                    "blend_mode": "normal",
                }
            ],
            "updated_at": datetime.now().isoformat(),
        }
        if seed_loader and seed_path:
            try:
                doc = seed_loader(seed_path)
                info["file_size"] = doc.get("file_size")
                dims = doc.get("dimensions") or (0, 0)
                info["width_mm"], info["height_mm"] = dims[0], dims[1]
                info["resolution"] = doc.get("resolution", 72)
                info["color_mode"] = doc.get("color_mode", "RGB")
                info["bit_depth"] = doc.get("bit_depth", 8)
                info["fonts"] = doc.get("fonts", [])
                real_layers = [
                    {
                        "id": str(uuid.uuid4())[:8],
                        "name": ly.get("name", f"Layer {i + 1}"),
                        "type": ly.get("type", "normal"),
                        "visible": True,
                        "locked": False,
                        "opacity": 100.0,
                        "blend_mode": "normal",
                    }
                    for i, ly in enumerate(doc.get("layers", []))
                ]
                if real_layers:
                    info["layers"] = real_layers
            except Exception as e:
                # 真实文件解析失败时保留诚实的新建文档默认值，不写入伪造层级
                logger.warning(f"文档种子加载失败，使用新建文档默认: {e}")
        return info

    def save(self):
        self.data["updated_at"] = datetime.now().isoformat()
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2, ensure_ascii=False)

    def layers(self) -> List[Dict[str, Any]]:
        return self.data.get("layers", [])

    def add_layer(self, name: str, layer_type: str = "normal") -> Dict[str, Any]:
        layer = {
            "id": str(uuid.uuid4())[:8],
            "name": name,
            "type": layer_type,
            "visible": True,
            "locked": False,
            "opacity": 100.0,
            "blend_mode": "normal",
        }
        self.data["layers"].append(layer)
        self.save()
        return layer

    def update_layer(self, layer_id: str, patch: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        for ly in self.data["layers"]:
            if ly["id"] == layer_id:
                ly.update({k: v for k, v in patch.items() if k != "id"})
                self.save()
                return ly
        return None

    def delete_layer(self, layer_id: str) -> bool:
        before = len(self.data["layers"])
        self.data["layers"] = [ly for ly in self.data["layers"] if ly["id"] != layer_id]
        if len(self.data["layers"]) != before:
            self.save()
            return True
        return False


def get_document_store() -> DocumentStore:
    if "doc_store" not in _state:
        # 无真实文件可导入时使用诚实的新建文档默认种子；
        # 不再以伪路径调用 print-QA 检查器（该检查器已正确拒绝不存在的路径）。
        _state["doc_store"] = DocumentStore(DATA_ROOT / "state" / "document.json")
    return _state["doc_store"]


# ============================================================================
# 真实运行指标采集中间件
# ============================================================================

class RequestMetrics:
    def __init__(self, window: int = 2000):
        self.total = 0
        self.errors_5xx = 0
        self.latencies: deque = deque(maxlen=window)
        self.by_endpoint: Dict[str, int] = {}

    def record(self, path: str, seconds: float, status_code: int):
        self.total += 1
        if status_code >= 500:
            self.errors_5xx += 1
        self.latencies.append(seconds)
        key = path.split("?")[0]
        self.by_endpoint[key] = self.by_endpoint.get(key, 0) + 1

    def percentile(self, p: float) -> Optional[float]:
        if not self.latencies:
            return None
        s = sorted(self.latencies)
        idx = min(int(math.ceil(p / 100 * len(s))) - 1, len(s) - 1)
        return round(s[max(idx, 0)], 4)


METRICS = RequestMetrics()
# P44 验收回填仪器：按 FastAPI 路由模板（非具体路径）逐条计命中数。
# 路由匹配后端点自报 4xx/5xx 仍计数——"被该端点处理过"正是验收要的证据。
_ROUTE_HITS: Dict[str, int] = {}

# ============================================================================
# 数据模型
# ============================================================================

class DocumentInfo(BaseModel):
    name: str
    width: Any
    height: Any
    resolution: Optional[int] = None
    color_mode: Optional[str] = None
    bit_depth: Optional[int] = None
    artboards: int = 1
    layers_count: int = 0
    updated_at: Optional[str] = None


class LayerInfo(BaseModel):
    id: str
    name: str
    type: str
    visible: bool
    locked: bool
    opacity: float = 100.0
    blend_mode: str = "normal"


class LayerCreateRequest(BaseModel):
    name: str = "新图层"
    type: str = "normal"


class LayerUpdateRequest(BaseModel):
    name: Optional[str] = None
    visible: Optional[bool] = None
    locked: Optional[bool] = None
    opacity: Optional[float] = None
    blend_mode: Optional[str] = None


class AssetAddRequest(BaseModel):
    file_path: str
    mime_type: Optional[str] = None


class AssetSearchRequest(BaseModel):
    query: str
    top_k: int = 10


class AssetLicenseRequest(BaseModel):
    license_status: str          # unknown / free / commercial / restricted / proprietary
    license_type: Optional[str] = None
    commercial_use: bool = False
    verified: bool = False
    warnings: List[str] = Field(default_factory=list)


class GenerationRequest(BaseModel):
    prompt: str
    negative_prompt: Optional[str] = None
    model: Optional[str] = None      # 不传则按路由策略自动选择
    resolution: str = "1024x1024"
    steps: int = 30
    guidance: float = 7.5
    seed: Optional[int] = None
    priority: str = "cost"           # cost / latency / privacy


class RemoveBackgroundRequest(BaseModel):
    image_path: str
    threshold: int = 32              # 背景色距离容差 0-255
    output_path: Optional[str] = None


class PreflightRequest(BaseModel):
    document_path: str


class WorkflowNodeRequest(BaseModel):
    id: str
    type: str                        # input/output/condition/ai/tool/skill/loop/parallel/approval
    name: str
    config: Dict[str, Any] = Field(default_factory=dict)


class WorkflowEdgeRequest(BaseModel):
    source_node_id: str
    target_node_id: str
    condition: Optional[str] = None


class WorkflowDefineRequest(BaseModel):
    id: Optional[str] = None
    name: str
    description: str = ""
    version: str = "1.0"
    nodes: List[WorkflowNodeRequest]
    edges: List[WorkflowEdgeRequest] = Field(default_factory=list)
    variables: Dict[str, Any] = Field(default_factory=dict)


class WorkflowExecuteRequest(BaseModel):
    input_data: Dict[str, Any] = Field(default_factory=dict)


class BatchProcessRequest(BaseModel):
    file_paths: List[str]
    operation: str                   # preflight / import_asset / remove_background
    workers: int = 4


class LicenseActivateRequest(BaseModel):
    license_key: str


class ConfigSetRequest(BaseModel):
    value: Any


class SnapshotCreateRequest(BaseModel):
    document_path: str
    snapshot_type: str = "full"          # full / incremental
    metadata: Dict[str, Any] = Field(default_factory=dict)


class TransactionBeginRequest(BaseModel):
    task_id: str
    user_id: Optional[str] = None
    document_path: str = ""


class TransactionRollbackRequest(BaseModel):
    transaction_id: str


class TaskRegisterRequest(BaseModel):
    task_id: str
    total_steps: int
    context: Dict[str, Any] = Field(default_factory=dict)


class TaskProgressRequest(BaseModel):
    current_step: int


class TaskFailRequest(BaseModel):
    error_message: str
    recoverable: bool = True


class KnowledgeIngestRequest(BaseModel):
    file_path: str
    title: Optional[str] = None
    scope: str = "project"                # global/workspace/project/brand/private
    tags: List[str] = Field(default_factory=list)
    project_id: Optional[str] = None
    brand: Optional[str] = None
    workspace_id: Optional[str] = None


class KnowledgeRetrieveRequest(BaseModel):
    query: str
    top_k: int = 5
    allowed_scopes: Optional[List[str]] = None
    context: Dict[str, Any] = Field(default_factory=dict)


class FeatureFlagRequest(BaseModel):
    value: bool


class ToolRegisterRequest(BaseModel):
    name: str
    description: str = ""
    risk_level: str = "L1"                # §21 L0-L4
    requires_adobe_host: bool = False


class CapabilityReportRequest(BaseModel):
    host: str                             # photoshop / illustrator
    version: Optional[str] = None
    capabilities: Dict[str, bool] = Field(default_factory=dict)


class SkillManifestRequest(BaseModel):
    id: str
    version: str = "1.0.0"
    permissions: List[str] = Field(default_factory=list)
    tools: List[str] = Field(default_factory=list)
    risk: str = "medium"


class DesignCompareRequest(BaseModel):
    before: List[Dict[str, Any]]
    after: List[Dict[str, Any]]
    compare_keys: Optional[List[str]] = None


class DeliveryPackageRequest(BaseModel):
    project: str
    files: Dict[str, List[str]] = Field(default_factory=dict)   # category(Source/Preview/Report/Print) -> [file_path]
    notes: Optional[str] = None


class FontMetricsRequest(BaseModel):
    font: str                              # 字体 id / family / PostScript 名
    text: str
    size_pt: float = 12.0


class FontMatchRequest(BaseModel):
    missing: str                           # 缺失/待替换的字体名
    weight: Optional[int] = None
    text: Optional[str] = None             # 提供则作为覆盖度硬门槛
    require_cjk: bool = False
    top_k: int = 5


class FallbackRuleRequest(BaseModel):
    from_font: str
    to_fonts: List[str]
    weight: Optional[int] = None


class ResolveFontRequest(BaseModel):
    missing: str
    text: Optional[str] = None


class DesignReviewRequest(BaseModel):
    objects: List[Dict[str, Any]]
    canvas: Optional[Dict[str, Any]] = None
    brand: Optional[Dict[str, Any]] = None
    roles: Optional[List[str]] = None       # 缺省评审全部四角色


class RevisionParseRequest(BaseModel):
    text: str
    objects: List[Dict[str, Any]]
    protected: Optional[List[str]] = None


class RevisionApplyRequest(BaseModel):
    objects: List[Dict[str, Any]]
    changes: List[Dict[str, Any]]
    only_ids: Optional[List[str]] = None    # 用户批量审核后勾选的 change id


# —— 色彩引擎（§44）——
class ColorPaletteRequest(BaseModel):
    path: str                               # 真实图像文件路径（经 validate_file_path）
    k: int = 6

class ColorHarmonyRequest(BaseModel):
    base: str
    scheme: str = "complementary"

class ColorContrastRequest(BaseModel):
    fg: str
    bg: str

class ColorConvertRequest(BaseModel):
    hex: str

class DeltaERequest(BaseModel):
    a: str
    b: str
    method: str = "ciede2000"

class PantoneRequest(BaseModel):
    hex: str

class NearestPaletteRequest(BaseModel):
    color: str
    palette: List[str]


# —— 布局排版引擎（§45）——
class LayoutAlignRequest(BaseModel):
    objects: List[Dict[str, Any]]
    mode: str = "left"

class LayoutDistributeRequest(BaseModel):
    objects: List[Dict[str, Any]]
    axis: str = "x"
    spacing: Optional[float] = None

class LayoutCollideRequest(BaseModel):
    objects: List[Dict[str, Any]]
    min_overlap_ratio: float = 0.0

class LayoutSafeAreaRequest(BaseModel):
    objects: List[Dict[str, Any]]
    canvas: Dict[str, Any]
    margin: Optional[float] = None

class LayoutGridSnapRequest(BaseModel):
    objects: List[Dict[str, Any]]
    canvas: Dict[str, Any]
    cols: int = 12
    rows: int = 12
    mode: str = "cell_center"

class LayoutAnalyzeRequest(BaseModel):
    objects: List[Dict[str, Any]]
    canvas: Optional[Dict[str, Any]] = None


# —— 包装与变量数据引擎（§46/§47）——
class DielineRequest(BaseModel):
    width: float
    height: float
    bleed: float = 0.0
    safe: float = 0.0
    units: str = "mm"

class ImpositionRequest(BaseModel):
    die_width: float
    die_height: float
    count: int
    sheet_width: float
    sheet_height: float
    gap: float = 0.0

class VdpMergeRequest(BaseModel):
    source: str                             # CSV/XLSX/JSON 数据源路径（经 validate_file_path）
    field_map: Optional[Dict[str, str]] = None   # 输出字段 -> 源列名
    limit: Optional[int] = None

class BarcodeRequest(BaseModel):
    code: str                               # EAN：12/13 位或 7/8 位数字（12/7 位自动补校验位）；QR：任意载荷
    symbology: str = "EAN13"                # 当前真实支持 EAN13 / EAN8 / QR(v1..v10)；其它码制如实 needs_external
    scale: int = 2                          # 每个模块的像素宽度
    height: int = 60                        # 条纹高度（像素，仅一维码）
    quiet: Optional[int] = None             # 左右静区（模块数）；省略则按码制各取其标准值（一维 11 / QR 4）
    draw_text: bool = True
    ec_level: str = "M"                     # 仅 QR 生效：L/M/Q/H（非法值 → 400，绝不自作主张降级）
    output_path: Optional[str] = None       # 省略则写入 DATA_ROOT/packaging

class DielinePanel(BaseModel):
    id: Optional[str] = None
    x: float
    y: float
    width: float
    height: float

class PanelTopologyRequest(BaseModel):
    panels: List[DielinePanel]              # 结构化矢量刀版面板（轴对齐矩形）
    tol: float = 1e-6                        # 共边判定容差
    min_fold: float = 1e-6                   # 计入折线的最小共享边长
    units: str = "mm"

class UpdateConfigureRequest(BaseModel):
    channel: str                            # stable / beta / dev
    feed_url: Optional[str] = None          # 更新清单 URL（真实 HTTP GET）
    secret: Optional[str] = None            # HMAC-SHA256 验签密钥（不回显）
    current_version: Optional[str] = None

class UpdateCheckRequest(BaseModel):
    channel: str = "stable"
    current_version: Optional[str] = None
    timeout: float = 5.0

class UpdateDownloadRequest(BaseModel):
    channel: str = "stable"
    staged_dir: Optional[str] = None
    timeout: float = 30.0

class UpdateApplyRequest(BaseModel):
    target_path: str                         # 被替换的目标（如便携 EXE 路径）
    staged_path: str                         # 已下载并校验的暂存文件
    expected_sha256: Optional[str] = None    # 传入则替换前再校验暂存


class NasConfigureRequest(BaseModel):
    target_id: str                           # 目标标识
    target: str                              # filesystem: 路径/映射盘/UNC；webdav: http(s) URL
    kind: str                                # filesystem / webdav
    share_root: Optional[str] = None         # filesystem 沙箱根（枚举/锁/下载受限于此，防穿越）
    username: Optional[str] = None           # WebDAV 凭据（不回显）
    password: Optional[str] = None           # WebDAV 凭据（不回显）


class NasConnectRequest(BaseModel):
    target_id: str
    timeout: float = 5.0


class NasListRequest(BaseModel):
    target_id: str
    subpath: str = ""
    timeout: float = 8.0
    use_cache: bool = False
    max_age: float = 30.0


class NasLockRequest(BaseModel):
    target_id: str
    subpath: str                             # 相对沙箱根的文件路径


class NasReconnectRequest(BaseModel):
    target_id: str
    attempts: int = 3
    base_delay: float = 0.2
    timeout: float = 5.0


class NasDownloadRequest(BaseModel):
    target_id: str
    subpath: str
    dest_dir: Optional[str] = None
    timeout: float = 30.0


# ============================================================================
# FastAPI 应用
# ============================================================================

app = FastAPI(
    title="造境 AI 设计智能体",
    description="全功能真实集成版 — License / 配置持久化 / 素材引擎 / 印前检查 / 工作流 / 模型路由 / 安全加固",
    version=APP_VERSION,
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def _close_boot_splash():
    """关掉 PyInstaller 引导器在解包阶段显示的原生启动画面。

    Phase 76 实测：onefile 每次启动都要把 1799 个文件 / 100.4 MB 解包到全新的
    _MEI 目录，解包约占 40 s、随后导入再占约 41 s，整段窗口里产品原本没有任何
    可见反馈。--splash 由 bootloader 在解包开始前就显示窗口，关窗的责任在程序
    一侧；源码直跑（未冻结）时 pyi_splash 不存在，静默跳过。
    """
    try:
        import pyi_splash  # type: ignore
        pyi_splash.close()
        logger.info("启动画面已关闭（冻结版）")
    except Exception:
        pass


@app.on_event("startup")
async def _publish_uxp_discovery():
    """§8 端口动态发现：把本地端口 + 随机 Session Token 落 discovery 文件供 UXP 面板读取。
    桥接引擎缺失时降级不阻断启动。"""
    try:
        port = int(os.environ.get("ZAOJING_PORT", "8000"))
        get_bridge_engine().publish_discovery(port)
        logger.info("UXP 桥接 discovery 已发布 (port=%s)", port)
    except Exception as e:  # pragma: no cover
        logger.warning("UXP 桥接未就绪(降级运行): %s", e)


@app.on_event("startup")
async def _warm_openapi_schema():
    """把 OpenAPI schema 的构建成本从请求路径移到启动期（Phase 52 L6-D4 实证修复）。

    实测：/openapi.json **首次**调用约 414 ms，之后稳定约 7 ms——FastAPI 懒构建
    schema，而这 414 ms 是同步 CPU 工作，整段压在事件循环上（任何并发请求在这
    半秒里都拿不到响应）。控制台的面板与验收层都要读这个端点，第一次读被当成
    性能缺陷是不诚实的：schema 只依赖 app 定义、与数据根无关，构建一次即缓存，
    所以启动期预热是纯收益。预热本身仍是同步工作，故照 L6-A1 走工作线程。
    """
    try:
        await asyncio.to_thread(app.openapi)
    except Exception as e:  # pragma: no cover — 预热失败不阻断启动，请求路径会自行懒构建
        logger.warning("OpenAPI schema 预热失败(降级为首次调用时构建): %s", e)


# ----------------------------------------------------------------------------
# 事件循环延迟看门狗（Phase 52 F6）
# ----------------------------------------------------------------------------
# 为什么产品要自己量这件事：L6 的前四版都在客户端量"控制台被饿了多少"，实测全部不可信——
# 客户端量到的是它自己的线程池与传输地板（p52_l6_run11.out vs p52_mut_m3 变异：clean 节奏
# 超额 p95=175.7 ms，把 cosine 打分放回事件循环的缺陷 p95=175.1 ms，两个数一样，分离度全靠
# 分母 75.2/46.1 ms，即机器在说话而不是产品在说话）。看门狗把观测点搬进被观测物内部：一个
# 50 ms 的 sleep 在事件循环上排队，循环若被任何协程里的同步工作占住，这次醒来就正好迟到那段
# 时长；它不经过网络、不受客户端线程数影响，和 worker_cap / peak_concurrency 同属"计数器家族"。
# 写读都在事件循环线程上（任务本身 + async def get_metrics），因此无需锁，也不会撕裂窗口。
_LOOP_LAG_INTERVAL_S = 0.05   # 看门狗心跳：50 ms 足以看见一次 20 ms 以上的循环占用
_LOOP_LAG_WINDOW = 200        # 保留最近 200 次醒来（=10 s），够覆盖本层任何一个测量窗口
_loop_lag_recent: List[float] = []
_loop_lag_stats: Dict[str, Any] = {"samples": 0, "max_ms": 0.0}
_LOOP_LAG_TASK: Optional["asyncio.Task"] = None


async def _loop_lag_watchdog() -> None:
    """在事件循环上睡觉并记录每次醒来的迟到量（ms）。

    重新锚定用"现在 + 间隔"而不是"上次目标 + 间隔"：一次被占住 500 ms 的循环应当被记成一次
    500 ms 的迟到，而不是把欠账累加进后续每一次心跳，否则一段长阻塞会伪装成整轮的持续饥饿。
    """
    loop = asyncio.get_running_loop()
    target = loop.time() + _LOOP_LAG_INTERVAL_S
    while True:
        try:
            delay = target - loop.time()
            if delay > 0:
                await asyncio.sleep(delay)
            now = loop.time()
            lag_ms = max(0.0, (now - target) * 1000.0)
            target = now + _LOOP_LAG_INTERVAL_S
            _loop_lag_recent.append(lag_ms)
            if len(_loop_lag_recent) > _LOOP_LAG_WINDOW:
                del _loop_lag_recent[:len(_loop_lag_recent) - _LOOP_LAG_WINDOW]
            _loop_lag_stats["samples"] += 1
            if lag_ms > _loop_lag_stats["max_ms"]:
                _loop_lag_stats["max_ms"] = lag_ms
        except asyncio.CancelledError:
            raise
        except Exception as e:  # pragma: no cover — 仪表坏了不能拖走服务
            logger.warning("事件循环延迟看门狗退出(指标降级): %s", e)
            return


def _loop_lag_counters() -> Dict[str, Any]:
    """给 /api/v1/metrics 自报的循环延迟窗口（单一真相源）。

    recent_first_index 是窗口里第一个样本在全局计数中的下标，L6 据此把"某个请求窗口内的迟到"
    从两次 /metrics 快照里筛出来，无需两个进程对表。
    """
    _n = len(_loop_lag_recent)
    return {"interval_ms": round(_LOOP_LAG_INTERVAL_S * 1000.0, 1),
            "samples": _loop_lag_stats["samples"],
            "max_ms": round(float(_loop_lag_stats["max_ms"]), 1),
            "window": _LOOP_LAG_WINDOW,
            "running": 1 if (_LOOP_LAG_TASK is not None and not _LOOP_LAG_TASK.done()) else 0,
            "recent_first_index": _loop_lag_stats["samples"] - _n,
            "recent_ms": [round(float(v), 1) for v in _loop_lag_recent]}


@app.on_event("startup")
async def _start_loop_lag_watchdog():
    """启动期在运行循环上挂看门狗（缺失或失败只降级指标，不阻断启动）。"""
    global _LOOP_LAG_TASK
    try:
        _LOOP_LAG_TASK = asyncio.get_running_loop().create_task(_loop_lag_watchdog())
    except Exception as e:  # pragma: no cover
        logger.warning("事件循环延迟看门狗未启动(降级): %s", e)


# ----------------------------------------------------------------------------
# 进程 / 磁盘快照工作线程（Phase 58 L6-D4 实证修复）
# ----------------------------------------------------------------------------
# 为什么这两个读数必须搬出事件循环——实测，不是推断（p58_l6d4_parts.json / p58_l6d4_report.json）：
# psutil.Process(os.getpid()).num_threads() 在机器安静时单次 11.8 ms（max 14.5），本层批处理
# 负载期间 51.5/189.4 ms，收尾轮 85.9/144.3 ms；而同一次请求里 handler 总耗时 24.0/47.2 ms，
# 其余分段全部 <= 0.1 ms（latencies_n=23、route_hits_n=2、resp_bytes=683 都读 0.00）。也就是
# 说这一个调用就是 /api/v1/metrics 的全部成本。它贵得不怪：Toolhelp32 要给全机每一个线程做
# 一次快照，价格由**别人的**线程数决定（本机 11,668 线程），所以同一请求内第二次调用也要
# 11.99/28.36 ms，且逐样本跟着机器线程数走。shutil.disk_usage 是 GetDiskFreeSpaceExW，安静时
# 0.15 ms，但数据卷上有持续写入时它会排队：在 4 MB/fsync 的持续写盘窗口里 /metrics 的中位数
# 被抬到 2.0x、最大值 4.7x，而同期 /health、/capabilities、/openapi.json 稳在
# 1.2-1.3 / 3.8-3.9 / 2.3-2.5 ms。控制台每几秒轮一次 /metrics，等于把"别人正在往这块盘写"
# 的延迟继承成自己的响应时间。
# 修法：把读数交给固定节奏的工作线程快照，请求路径只合并缓存——一次请求零次系统调用。
# 代价如实记在这里：cpu_percent 的窗口是"距上一次快照"（2 s 节奏），threads/disk_free_gb
# 至多旧一个快照周期，旧多少由 metrics_snapshot.age_ms 自报，验收层 L6-D4 第 4 腿据此把
# "缓存回答"与"坏掉的缓存一直在回答"分开。
# 就地更正（Phase 58 第 3 枚真缺陷，改注释之前这里写的是错的）：本字段诞生以来 cpu_percent
# 恒为 0.0。psutil 的 cpu_percent(interval=None) 是**同一实例上对上一次调用的差值**，而旧写法
# 每次构建都 psutil.Process(os.getpid()) 新建实例，于是永远没有"上一次"可减。实测对照
# （p58_cpu_semantics.json，psutil 7.2.2，同进程，采样间跑忙循环）：逐次新建
# [0.0, 0.0, 0.0, 0.0] vs 持久句柄 [0.0, 31.2, 34.7, 34.7]。所以窗口不是"变了"，是此前根本
# 不存在。修法是持有一个进程句柄复用；第一次构建仍是 0.0（那一次确实没有前一次），这一点
# 由 L6-D4 第 4b 腿的"至少一次 > 0"判据承担，不靠绝对阈值。
_METRICS_SNAPSHOT_INTERVAL_S = 2.0
_metrics_snapshot: Dict[str, Any] = {}
_METRICS_PROC: Optional["psutil.Process"] = None
_METRICS_PROC_LOCK = threading.Lock()
_metrics_snapshot_meta: Dict[str, Any] = {
    "status": "pending", "refreshes": 0, "errors": 0, "built_at": None,
    "last_build_ms": None, "last_error": None,
    "interval_s": _METRICS_SNAPSHOT_INTERVAL_S,
}
_METRICS_SNAPSHOT_TASK: Optional["asyncio.Task"] = None


def _metrics_proc() -> "psutil.Process":
    """复用的进程句柄：cpu_percent 需要同一实例上的上一次调用才有差值。

    只创建一次（惰性 + 锁，构建工作线程与启动预热线程可能先后进入）。若某次构建换了新实例，
    那一轮读数会退回 0.0，所以这里绝不在构建里重建。
    """
    global _METRICS_PROC
    if _METRICS_PROC is None:
        with _METRICS_PROC_LOCK:
            if _METRICS_PROC is None:
                _METRICS_PROC = psutil.Process(os.getpid())
    return _METRICS_PROC


def _metrics_snapshot_build() -> Dict[str, Any]:
    """同步取进程/磁盘读数。只允许在工作线程里跑，绝不被事件循环直接调用。"""
    snap: Dict[str, Any] = {}
    if psutil:
        proc = _metrics_proc()
        snap["cpu_percent"] = proc.cpu_percent(interval=None)
        snap["memory_mb"] = round(proc.memory_info().rss / (1024 * 1024), 1)
        snap["threads"] = proc.num_threads()
    du = shutil.disk_usage(DATA_ROOT)
    snap["disk_free_gb"] = round(du.free / (1024 ** 3), 2)
    return snap


async def _metrics_snapshot_worker() -> None:
    """固定节奏在工作线程重建快照；失败如实进 meta.errors/status，不静默把旧值当新值。"""
    global _metrics_snapshot
    while True:
        try:
            t0 = time.perf_counter()
            snap = await asyncio.to_thread(_metrics_snapshot_build)
            _metrics_snapshot = snap
            _metrics_snapshot_meta["status"] = "ok"
            _metrics_snapshot_meta["last_error"] = None
            _metrics_snapshot_meta["refreshes"] += 1
            _metrics_snapshot_meta["built_at"] = time.time()
            _metrics_snapshot_meta["last_build_ms"] = round(
                (time.perf_counter() - t0) * 1000.0, 1)
            await asyncio.sleep(_METRICS_SNAPSHOT_INTERVAL_S)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # pragma: no cover — 仪表坏了不能拖走服务
            _metrics_snapshot_meta["status"] = "error"
            _metrics_snapshot_meta["errors"] += 1
            _metrics_snapshot_meta["last_error"] = str(e)[:200]
            logger.warning("进程/磁盘快照工作线程退出(指标降级): %s", e)
            return


def _metrics_snapshot_view() -> Dict[str, Any]:
    """请求路径用的快照自报元数据：只读 dict + 一次 time.time()，零系统调用。"""
    meta = dict(_metrics_snapshot_meta)
    _built = meta.get("built_at")
    meta["age_ms"] = round((time.time() - _built) * 1000.0, 1) if _built else None
    meta["running"] = 1 if (_METRICS_SNAPSHOT_TASK is not None
                            and not _METRICS_SNAPSHOT_TASK.done()) else 0
    return meta


@app.on_event("startup")
async def _start_metrics_snapshot():
    """启动期先在工作线程建一次快照，使第一个 /metrics 就带真实读数而不是空值，再挂节奏。"""
    global _metrics_snapshot, _METRICS_SNAPSHOT_TASK
    try:
        t0 = time.perf_counter()
        _metrics_snapshot = await asyncio.to_thread(_metrics_snapshot_build)
        _metrics_snapshot_meta["status"] = "ok"
        _metrics_snapshot_meta["refreshes"] = 1
        _metrics_snapshot_meta["built_at"] = time.time()
        _metrics_snapshot_meta["last_build_ms"] = round((time.perf_counter() - t0) * 1000.0, 1)
    except Exception as e:  # pragma: no cover — 预热失败只降级指标，不阻断启动
        _metrics_snapshot_meta["status"] = "error"
        _metrics_snapshot_meta["errors"] = 1
        _metrics_snapshot_meta["last_error"] = str(e)[:200]
        logger.warning("进程/磁盘快照预热失败(降级): %s", e)
    try:
        _METRICS_SNAPSHOT_TASK = asyncio.get_running_loop().create_task(
            _metrics_snapshot_worker())
    except Exception as e:  # pragma: no cover
        logger.warning("进程/磁盘快照工作线程未启动(降级): %s", e)


@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    t0 = time.perf_counter()
    response = await call_next(request)
    dt = time.perf_counter() - t0
    METRICS.record(request.url.path, dt, response.status_code)
    _rt = request.scope.get("route")
    _rp = getattr(_rt, "path", None)
    if _rp and _rp.startswith("/api/v1"):
        _ROUTE_HITS[_rp] = _ROUTE_HITS.get(_rp, 0) + 1
    response.headers["X-Process-Time"] = f"{dt:.4f}"
    return response


# ----------------------------------------------------------------------------
# 系统
# ----------------------------------------------------------------------------

@app.get("/api/v1/health", tags=["系统"])
async def health_check():
    """健康检查 — 如实报告每个引擎的加载状态"""
    engines = {}
    for name in _ENGINE_NAMES:
        engines[name] = "ready" if name in _MODULES else _IMPORT_ERRORS.get(name, "not loaded")
    return {
        "status": "healthy" if len(_MODULES) >= 5 else "degraded",
        "version": APP_VERSION,
        "timestamp": datetime.now().isoformat(),
        "uptime_seconds": round(time.time() - START_TIME, 1),
        "mode": "portable" if is_portable() else "installed",
        "data_root": str(DATA_ROOT),
        "frozen": bool(getattr(sys, "frozen", False)),
        "python": sys.version.split()[0],
        "engines": engines,
    }


@app.get("/api/v1/metrics", tags=["系统"])
async def get_metrics():
    """真实运行时指标（进程 + 请求统计 + 逐路由模板命中计数）

    Phase 58：进程与磁盘读数改由工作线程快照合并而来，本 handler 自身零系统调用
    （原因与代价记在 _METRICS_SNAPSHOT_INTERVAL_S 上方的注释里），缓存的新鲜度由
    metrics_snapshot 如实自报。
    """
    result: Dict[str, Any] = {
        "uptime_seconds": round(time.time() - START_TIME, 1),
        "requests_total": METRICS.total,
        "errors_5xx": METRICS.errors_5xx,
        "p50_latency": METRICS.percentile(50),
        "p95_latency": METRICS.percentile(95),
        "p99_latency": METRICS.percentile(99),
        "top_endpoints": sorted(METRICS.by_endpoint.items(), key=lambda x: -x[1])[:10],
        "route_hits": dict(_ROUTE_HITS),
        # Phase 52：批处理的服务级工作者预算 + 真实占用/峰值（L6 的判据来源，单一真相源，
        # 计数由 _batch_slot_enter/_batch_slot_exit 在实际执行文件任务的那一刻维护）
        "batch": _batch_counters(),
        # Phase 52 F6：事件循环自身的延迟窗口（L6 循环饥饿判据的判据来源）
        "loop_lag": _loop_lag_counters(),
    }
    # Phase 58：off-loop 快照的合并点。psutil 缺失时快照本就不含这些键，此处不补假值。
    result.update(_metrics_snapshot)
    result["metrics_snapshot"] = _metrics_snapshot_view()
    return result


def _probe_data_root_writable(probe: Path) -> None:
    """写探针：真写一个文件再删掉。同步磁盘调用，Phase 52 起由工作线程执行。"""
    probe.write_text("ok", encoding="utf-8")
    probe.unlink()


@app.post("/api/v1/diagnostics/run", tags=["系统"])
async def run_diagnostics():
    """真实系统诊断 — 逐项检查引擎/目录/磁盘/版本/License"""
    checks: List[Dict[str, Any]] = []

    def add(name: str, ok: bool, detail: str):
        checks.append({"check": name, "status": "pass" if ok else "fail", "detail": detail})

    # 1. 引擎加载
    missing = [k for k in _ENGINE_NAMES if k not in _MODULES]
    add("engines", not missing, f"已加载 {len(_MODULES)}/{len(_ENGINE_NAMES)}" + (f"; 缺失: {missing}" if missing else ""))

    # 2. Python 版本
    try:
        ok, msg = _MODULES["config"].PythonVersionChecker.check()
        add("python_version", ok, msg)
    except Exception as e:
        add("python_version", False, str(e))

    # 3. 数据目录可写
    try:
        probe = DATA_ROOT / ".diag_write"
        await asyncio.to_thread(_probe_data_root_writable, probe)
        add("data_writable", True, str(DATA_ROOT))
    except Exception as e:
        add("data_writable", False, str(e))

    # 4. 配置文件
    try:
        st = get_settings()
        add("config_file", st.config_path.exists(), str(st.config_path))
    except Exception as e:
        add("config_file", False, str(e))

    # 5. 素材引擎初始化
    try:
        eng = get_asset_engine()
        add("asset_engine", True, f"{len(eng.assets)} assets indexed at {eng.root_path}")
    except Exception as e:
        add("asset_engine", False, str(e))

    # 6. 磁盘空间（Phase 58：现场量而非读快照——诊断是用户点了才跑的，要给实时值；
    #    但 GetDiskFreeSpaceExW 会在卷写入后面排队，所以照 L6-A1 走工作线程）
    try:
        du = await asyncio.to_thread(shutil.disk_usage, DATA_ROOT)
        free_gb = du.free / (1024 ** 3)
        add("disk_space", free_gb > 1.0, f"{free_gb:.1f} GB free")
    except Exception as e:
        add("disk_space", False, str(e))

    # 7. License
    try:
        lm = get_license_manager()
        stt = lm.status()
        add("license", not stt.get("degraded"), json.dumps(stt, ensure_ascii=False))
    except Exception as e:
        add("license", False, str(e))

    # 8. 模型注册表
    try:
        r = get_model_router()
        add("model_registry", bool(r.registry.providers),
            f"{len(r.registry.models)} models / {list(r.registry.providers)}")
    except Exception as e:
        add("model_registry", False, str(e))

    passed = sum(1 for c in checks if c["status"] == "pass")
    return {
        "checks": checks,
        "total": len(checks),
        "passed": passed,
        "failed": len(checks) - passed,
        "overall_status": "healthy" if passed == len(checks) else "attention_needed",
    }


# ----------------------------------------------------------------------------
# License (P0-1)
# ----------------------------------------------------------------------------

@app.get("/api/v1/license", tags=["License"])
async def check_license():
    """License 状态 — 降级模式返回 degraded 而非 500"""
    lm = get_license_manager()
    stt = lm.status()
    return {
        "valid": lm.validate(),
        "status": stt.get("status"),
        "machine_id": stt.get("machine_id"),
        "edition": stt.get("edition"),
        "degraded": stt.get("degraded", False),
        "degraded_reason": stt.get("degraded_reason"),
        "expiry_date": lm.expiry_date(),
    }


@app.post("/api/v1/license/activate", tags=["License"])
async def activate_license(req: LicenseActivateRequest):
    lm = get_license_manager()
    ok = lm.activate(req.license_key)
    if not ok:
        raise HTTPException(
            status_code=400,
            detail={
                "message": "激活失败：密钥无效、机器绑定不符、已过期或服务处于降级模式",
                "reason": lm.last_error,
                "machine_id": lm.machine_id,
            },
        )
    return {"activated": True, "status": lm.status()}


# ----------------------------------------------------------------------------
# 配置与安全 (P0-2 / P1)
# ----------------------------------------------------------------------------

@app.get("/api/v1/config", tags=["配置"])
async def get_config():
    return get_settings().config


@app.put("/api/v1/config/{key}", tags=["配置"])
async def set_config(key: str, req: ConfigSetRequest):
    st = get_settings()
    st.set(key, req.value)
    await asyncio.to_thread(st.save)
    return {"updated": key, "config": st.config}


@app.get("/api/v1/security/status", tags=["安全"])
async def security_status():
    """P1 安全加固状态：路径黑名单 / 扩展名白名单 / 版本约束"""
    st = get_settings()
    result: Dict[str, Any] = {
        "blocked_paths": [str(p) for p in st.get_blocked_paths()],
        "allowed_extensions": st.get_allowed_extensions(),
        "data_root": str(DATA_ROOT),
        "mode": "portable" if is_portable() else "installed",
    }
    if "security" in _MODULES:
        checker = get_path_checker()
        probes = {
            "system_dir": "C:\\Windows\\System32\\cmd.exe",
            "path_traversal": str(DATA_ROOT / "..\\..\\windows\\win.ini"),
            "double_extension": "C:\\Users\\public\\photo.jpg.exe",
            "normal_file": "C:\\Users\\public\\design.psd",
        }
        result["self_test"] = {k: checker.is_blocked(v) for k, v in probes.items()}
        result["permission_system"] = "PermissionEngine (L0-L4 risk levels) loaded"
    else:
        result["permission_system"] = _IMPORT_ERRORS.get("security", "not loaded")
    return result


# ----------------------------------------------------------------------------
# 文档 / 图层
# ----------------------------------------------------------------------------

@app.get("/api/v1/document", response_model=DocumentInfo, tags=["文档"])
async def get_current_document():
    doc = get_document_store().data
    return DocumentInfo(
        name=doc.get("name", "未命名设计.psd"),
        width=doc.get("width_mm"),
        height=doc.get("height_mm"),
        resolution=doc.get("resolution"),
        color_mode=doc.get("color_mode"),
        bit_depth=doc.get("bit_depth"),
        artboards=doc.get("artboards", 1),
        layers_count=len(doc.get("layers", [])),
        updated_at=doc.get("updated_at"),
    )


@app.put("/api/v1/document", response_model=DocumentInfo, tags=["文档"])
async def rename_document(name: str):
    store = get_document_store()
    old_name = store.data.get("name")
    store.data["name"] = name
    await asyncio.to_thread(store.save)
    doc = await get_current_document()
    await _emit("document.renamed", {
        "old_name": old_name, "new_name": name,
        "document_path": str(store.path), "layers_count": len(store.data.get("layers", [])),
    })
    return doc


@app.get("/api/v1/layers", response_model=List[LayerInfo], tags=["图层"])
async def get_layers():
    return [LayerInfo(**ly) for ly in get_document_store().layers()]


@app.post("/api/v1/layers", response_model=LayerInfo, tags=["图层"])
async def create_layer(req: LayerCreateRequest):
    store = get_document_store()
    layer = store.add_layer(req.name, req.type)
    logger.info(f"Created layer: {req.name}")
    await _emit("layer.created", {
        "layer_id": layer.get("id"), "name": layer.get("name"),
        "type": layer.get("type"), "visible": layer.get("visible"),
        "total_layers": len(store.data.get("layers", [])),
    })
    return LayerInfo(**layer)


@app.patch("/api/v1/layers/{layer_id}", response_model=LayerInfo, tags=["图层"])
async def update_layer(layer_id: str, req: LayerUpdateRequest):
    patch = {k: v for k, v in req.model_dump().items() if v is not None}
    layer = get_document_store().update_layer(layer_id, patch)
    if layer is None:
        raise HTTPException(status_code=404, detail=f"图层不存在: {layer_id}")
    return LayerInfo(**layer)


def _require_l3_confirm(action: str, confirm: Optional[bool]) -> None:
    """L3 高风险操作的显式确认门（Phase 65 缺陷 P65-D1 修复）。

    口径对齐安装执行面的 confirmation_required 三形（缺省 / 空 / 近似值皆拒）：
    只有字面 True 才放行；其余一律 400 且**绝不执行任何副作用**。
    HTTP 协程与工作流工具转调的是同一个函数对象，所以这道门同时覆盖两个面——
    工作流节点里调用 layers.delete 而不带 confirm 也只会得到失败节点，不会静默删层。"""
    if confirm is not True:
        raise HTTPException(
            status_code=400,
            detail=("confirmation_required — " + action + " 为 L3 不可逆操作，"
                    "须显式携带 confirm=true（缺省 / 空 / false / 近似值一律拒绝，未执行任何删除）"),
        )


@app.delete("/api/v1/layers/{layer_id}", tags=["图层"])
async def delete_layer(layer_id: str, confirm: Optional[bool] = None):
    """L3 高风险操作 — 便携/本地单用户环境下需显式 confirm 参数（confirm=true 才真删）"""
    if not layer_id:
        raise HTTPException(status_code=400, detail="缺少 layer_id")
    _require_l3_confirm("删除图层 " + layer_id, confirm)
    store = get_document_store()
    doomed = next((ly for ly in store.data.get("layers", []) if ly.get("id") == layer_id), None)
    if not store.delete_layer(layer_id):
        raise HTTPException(status_code=404, detail=f"图层不存在: {layer_id}")
    logger.warning(f"[L3] Deleted layer: {layer_id}")
    await _emit("layer.deleted", {
        "layer_id": layer_id, "name": (doomed or {}).get("name"),
        "type": (doomed or {}).get("type"), "risk_level": "L3",
        "total_layers": len(store.data.get("layers", [])),
    })
    return {"deleted": True, "layer_id": layer_id, "risk_level": "L3"}


# ----------------------------------------------------------------------------
# 素材库 (P2 Asset Engine — 持久化 + 向量搜索 + License 追踪)
# ----------------------------------------------------------------------------

def _asset_to_dict(a) -> Dict[str, Any]:
    return {
        "id": a.asset_id,
        "name": a.filename,
        "path": a.file_path,
        "size_bytes": a.file_size,
        "size": {"width": a.width, "height": a.height},
        "format": (a.mime_type or "").split("/")[-1],
        "type": a.asset_type.value,
        "thumbnail": getattr(a, "thumbnail_path", None),
        "created_at": a.created_at.isoformat() if a.created_at else None,
    }


@app.get("/api/v1/assets", tags=["素材"])
async def list_assets(category: Optional[str] = None):
    eng = get_asset_engine()
    assets = [_asset_to_dict(a) for a in eng.list_assets()]
    if category:
        assets = [a for a in assets if a["type"] == category]
    return {"total": len(assets), "storage": str(eng.root_path), "assets": assets}


@app.get("/api/v1/assets/{asset_id}", tags=["素材"])
async def get_asset(asset_id: str):
    eng = get_asset_engine()
    a = eng.get_asset(asset_id)
    if not a:
        raise HTTPException(status_code=404, detail=f"素材不存在: {asset_id}")
    lic = eng.get_license(asset_id)
    emb = eng.get_embedding(asset_id)
    return {
        **_asset_to_dict(a),
        "license": {
            "status": lic.license_status.value if lic else "unknown",
            "type": lic.license_type if lic else None,
            "commercial_use": lic.commercial_use if lic else False,
            "verified": lic.verified if lic else False,
            "attribution_required": lic.attribution_required if lic else False,
        } if lic else {"status": "unknown"},
        "embedding": {
            "model": emb.model_name,
            "index_status": emb.index_status.value,
            "dimensions": len(emb.vector),
        } if emb else None,
    }


@app.get("/api/v1/assets/{asset_id}/thumbnail", tags=["素材"])
async def get_asset_thumbnail(asset_id: str):
    """返回真实生成的缩略图 PNG（非图像素材无缩略图则 409）"""
    eng = get_asset_engine()
    a = eng.get_asset(asset_id)
    if not a:
        raise HTTPException(status_code=404, detail=f"素材不存在: {asset_id}")
    thumb = getattr(a, "thumbnail_path", None)
    if not thumb or not Path(thumb).exists():
        raise HTTPException(status_code=409, detail="该素材无可用缩略图（非图像或缩略图未生成）")
    # 安全：确保缩略图落在引擎 thumbnails 目录内，防路径穿越
    tdir = Path(str(eng.thumbnails_dir)).resolve()
    tp = Path(thumb).resolve()
    if tdir not in tp.parents and tp != tdir:
        raise HTTPException(status_code=403, detail="缩略图路径越界")
    return FileResponse(str(tp), media_type="image/png")


def _probe_image_size(path: Path):
    """读取图像尺寸（只解析头部，不载入像素）。失败如实返回 (None, None)。

    同步文件 I/O，因此只能活在工作线程里 —— 见 L6-A1：本服务 async 体内不留未下沉的
    阻塞调用（Phase 52 实测：一次 add_asset 的 PIL 打开 + 全库重写把 /health 按住 996 ms）。
    """
    try:
        from PIL import Image
        with Image.open(path) as im:
            return im.size
    except Exception:
        return (None, None)


@app.post("/api/v1/assets", tags=["素材"])
async def add_asset(req: AssetAddRequest):
    """导入真实素材文件 → 元数据入库 + 缩略图 + Embedding 索引"""
    path = validate_file_path(req.file_path)

    allowed = get_settings().get_allowed_extensions()
    if allowed and path.suffix.lower() not in allowed:
        raise HTTPException(status_code=400, detail=f"扩展名 {path.suffix} 不在白名单 {allowed}")

    mime = req.mime_type or mimetypes.guess_type(str(path))[0] or "application/octet-stream"

    width = height = None
    if mime.startswith("image/"):
        # PIL 头部解析 = 同步磁盘 + 同步解码准备，Phase 52 起在工作线程里做
        width, height = await asyncio.to_thread(_probe_image_size, path)

    eng = get_asset_engine()
    metadata = await eng.add_asset(str(path), mime, width=width, height=height)
    d = _asset_to_dict(metadata)
    emb = eng.get_embedding(d.get("id")) if d.get("id") else None
    await _emit("asset.ingested", {
        "asset_id": d.get("id"), "name": d.get("name"), "path": d.get("path"),
        "format": d.get("format"), "size_bytes": d.get("size_bytes"),
        "width": (d.get("size") or {}).get("width"),
        "height": (d.get("size") or {}).get("height"),
        "embedding_model": getattr(emb, "model_name", None),
        "embedding_dims": len(getattr(emb, "vector", []) or []) if emb is not None else None,
    })
    return d


@app.delete("/api/v1/assets/{asset_id}", tags=["素材"])
async def delete_asset(asset_id: str):
    """仅从索引删除（不触碰磁盘源文件）"""
    eng = get_asset_engine()
    # delete_asset 内部会 save_databases()（全量快照重写）—— Phase 52 起在工作线程执行
    if not await asyncio.to_thread(eng.delete_asset, asset_id):
        raise HTTPException(status_code=404, detail=f"素材不存在: {asset_id}")
    await _emit("asset.deleted", {"asset_id": asset_id, "removed_from_index": True})
    return {"removed_from_index": True, "asset_id": asset_id}


# 检索能力天花板的如实标注（Phase 51）：Phase 51 尖峰实测——`tokenhash-sha256-512`
# 是把词元 SHA-256 哈希投到 512 维的**稀疏投影**，一段短文本只点亮极少几维
# （实测 6 字中文查询仅 2 维非零、和为 -2.0），中文自然语言查询对真实素材的相似度
# 只有 0.1826 量级。它不是语义嵌入模型：同义/近义改写不会因此更近。为避免把哈希
# 投影冒充语义检索，凡返回相似度的端点都随响应自带这段口径（模型名/维度/是否语义）。
_RETRIEVAL_MODEL = "tokenhash-sha256-512"
_RETRIEVAL_NOTE = (
    "检索向量为词元 SHA-256 哈希的稀疏投影，非语义嵌入模型；相似度只反映字面词元重合，"
    "同义/近义改写不会更近。接入真实嵌入模型前如实标注该天花板，绝不冒称语义检索。"
)


def _retrieval_descriptor(dims: Optional[int] = None) -> Dict[str, Any]:
    """检索口径描述符 — 模型名取自 asset_engine 落库时写入的同名常量，维度按实测量。"""
    model = _RETRIEVAL_MODEL
    try:
        eng = get_asset_engine()
        for emb in list(getattr(eng, "embeddings", {}).values())[:1]:
            model = getattr(emb, "model_name", model) or model
            if dims is None:
                dims = len(getattr(emb, "vector", []) or [])
    except Exception:  # 引擎未就绪时退回常量口径，不编造维度
        pass
    return {"model": model, "dims": dims, "semantic": False,
            "kind": "sparse-token-hash-projection", "note": _RETRIEVAL_NOTE}


def _embed_text(text: str, dims: int = 512) -> List[float]:
    """查询向量 — 复用 AssetEngine.embed_text，保证与入库素材同一向量空间。"""
    mod = _MODULES.get("asset")
    fn = getattr(mod, "embed_text", None)
    if callable(fn):
        return fn(text, dims)
    # 兜底：模块缺失时退回确定性投影
    import hashlib
    vec = []
    for i in range(dims):
        h = hashlib.sha256(f"{text}|{i}".encode("utf-8")).digest()
        vec.append(int.from_bytes(h[:8], "big") / (2 ** 64))
    return vec


@app.post("/api/v1/assets/search", tags=["素材"])
async def search_assets(req: AssetSearchRequest):
    """真实向量余弦相似度搜索（走 AssetEngine.search_assets）"""
    eng = get_asset_engine()
    t0 = time.perf_counter()
    # 素材向量生成时未归一化，查询向量使用未归一化同构形式保证 cosine 计算一致
    # 512 维哈希投影是纯 CPU 循环（L6-A1 的静态面看不见一跳调用，故此处按实测主动下沉）
    query_vec = await asyncio.to_thread(_embed_text, req.query)
    hits = await eng.search_assets(query_vec, top_k=req.top_k)
    results = []
    for asset_id, score in hits:
        a = eng.get_asset(asset_id)
        if a:
            results.append({**_asset_to_dict(a), "similarity": round(score, 4)})
    return {
        "query": req.query,
        "results": results,
        "total": len(results),
        "indexed_embeddings": len(eng.embeddings),
        "search_time": round(time.perf_counter() - t0, 4),
        "retrieval": _retrieval_descriptor(len(query_vec)),
    }


def _persist_license_and_read_back(eng, asset_id: str, status, req):
    """授权写入 + 落盘 + 磁盘回读判定，返回 (persisted, persisted_status)。

    事实核对（Phase 52）：AssetEngine.set_license_status 内部**已经**调用
    save_databases()，旧注释"引擎只改内存态、必须由端点显式落盘"与代码不符，已纠正为
    实情。此处再显式 save 一次不是因为"引擎不落盘"，而是因为 save_databases 吞异常，
    所以"是否真落盘"只能回读磁盘判定，绝不自报 persisted=True。
    整块是同步磁盘工作（全量 assets.db.json 重写 + 解析），Phase 52 起在工作线程执行。
    """
    eng.set_license_status(
        asset_id, status,
        license_type=req.license_type,
        commercial_use=req.commercial_use,
        verified=req.verified,
        warnings=req.warnings,
    )
    eng.save_databases()
    try:
        with open(eng.assets_db_path, "r", encoding="utf-8") as _f:
            _disk = json.load(_f)
        _rec = (_disk.get("licenses") or {}).get(asset_id)
        if not _rec:
            return (False, None)
        persisted_status = _rec.get("license_status")
        if hasattr(persisted_status, "value"):
            persisted_status = persisted_status.value
        return (persisted_status == status.value, persisted_status)
    except Exception as _e:  # 回读失败如实记为未落盘，不掩盖
        logger.warning(f"license persist read-back failed ({asset_id}): {_e}")
        return (False, None)


@app.post("/api/v1/assets/{asset_id}/license", tags=["素材"])
async def set_asset_license(asset_id: str, req: AssetLicenseRequest):
    """授权状态人工确认写入 — 系统不自动认定第三方素材可商用"""
    eng = get_asset_engine()
    if not eng.get_asset(asset_id):
        raise HTTPException(status_code=404, detail=f"素材不存在: {asset_id}")
    try:
        status = _MODULES["asset"].LicenseStatus(req.license_status)
    except ValueError:
        valid = [s.value for s in _MODULES["asset"].LicenseStatus]
        raise HTTPException(status_code=400, detail=f"无效状态，可选: {valid}")
    # 授权写入 + 落盘 + 磁盘回读判定，整块在工作线程执行（判据见 helper）
    persisted, persisted_status = await asyncio.to_thread(
        _persist_license_and_read_back, eng, asset_id, status, req)
    lic = eng.get_license(asset_id)
    await _emit("asset.license_set", {
        "asset_id": asset_id, "license_status": status.value,
        "license_type": req.license_type, "commercial_use": req.commercial_use,
        "verified": req.verified,
        "warnings": list(lic.warnings) if lic else [],
        "persisted": persisted,
        "persisted_status_on_disk": persisted_status,
        "persist_evidence": "assets_db_path read-back (engine swallows save errors)",
    })
    return {"asset_id": asset_id, "license_status": status.value, "commercial_use": req.commercial_use,
            "persisted": persisted}


# ----------------------------------------------------------------------------
# AI 生成 (P2 Model Router — 路由/熔断/预算 + 生成元数据记录)
# ----------------------------------------------------------------------------

@app.get("/api/v1/models", tags=["AI 生成"])
async def list_models():
    r = get_model_router()
    return {
        "models": [
            {"name": m.name, "type": m.type.value if hasattr(m.type, "value") else str(m.type),
             "provider": m.provider, "cost_per_1k": m.input_cost_per_1k + m.output_cost_per_1k}
            for m in r.registry.models.values()
        ],
        "providers": [
            {"name": name, "available": p.can_execute(), "endpoint": p.config.api_endpoint}
            for name, p in r.registry.providers.items()
        ],
    }


@app.post("/api/v1/generate", tags=["AI 生成"])
async def generate(req: GenerationRequest):
    """
    真实生成管线: ModelRouter 选路 → Provider 调用(熔断/限流生效) → GenerationMetadata 落库。
    本地 Ollama / 云端豆包未运行时如实返回 502 并给出指引，不伪造成功。
    """
    r = get_model_router()

    model_name = req.model
    if model_name and model_name not in r.registry.models:
        raise HTTPException(
            status_code=400,
            detail=f"模型未注册: {model_name}，可用: {list(r.registry.models)}（GET /api/v1/models）",
        )
    if not model_name:
        model_name = next(iter(r.registry.models), None)
    if not model_name:
        raise HTTPException(status_code=503, detail="注册表中没有可用模型")

    provider_name = r.select_provider(model_name, {"priority": req.priority})
    if not provider_name:
        raise HTTPException(status_code=503, detail="所有 Provider 均不可用（熔断/预算/限流）")

    provider = r.registry.providers[provider_name]
    try:
        w, h = (int(x) for x in req.resolution.lower().split("x"))
    except Exception:
        w, h = 1024, 1024

    t0 = time.perf_counter()
    try:
        resp = await asyncio.wait_for(
            provider.invoke(model_name, req.prompt, options={"steps": req.steps, "guidance": req.guidance, "seed": req.seed, "negative_prompt": req.negative_prompt}),
            timeout=min(120, provider.config.timeout_seconds + 5),
        )
    except asyncio.TimeoutError:
        provider.record_failure()
        raise HTTPException(status_code=504, detail=f"Provider [{provider_name}] 超时，已计入熔断")
    except Exception as e:
        raise HTTPException(
            status_code=502,
            detail=f"Provider [{provider_name}] 调用失败: {e} — 本地模型请确认 Ollama (localhost:11434) 已启动；云端请配置 API Key",
        )

    duration = time.perf_counter() - t0
    content = resp.get("content", "") if isinstance(resp, dict) else str(resp)
    cost = provider.estimate_cost(max(1, len(req.prompt) // 4), max(1, len(content) // 4))
    r.record_cost(provider_name, cost)

    generation_id = None
    if "asset" in _MODULES:
        try:
            eng = get_asset_engine()
            # record_generation 内部会 save_databases()（全量快照重写）—— 交给工作线程
            generation_id = await asyncio.to_thread(
                eng.record_generation,
                source_asset_id=None, prompt=req.prompt, model_name=model_name,
                provider=provider_name, width=w, height=h, steps=req.steps,
                guidance_scale=req.guidance, seed=req.seed, cost_usd=cost,
                duration_seconds=round(duration, 3), negative_prompt=req.negative_prompt,
            )
        except Exception as e:
            logger.warning(f"GenerationMetadata 记录失败: {e}")

    return {
        "status": "success",
        "generation_id": generation_id,
        "provider": provider_name,
        "model": model_name,
        "output_preview": content[:500],
        "output_length": len(content),
        "images_generated": 0 if not content.startswith("data:image") else 1,
        "cost_usd": round(cost, 6),
        "duration_seconds": round(duration, 3),
        "params": {"resolution": f"{w}x{h}", "steps": req.steps, "guidance": req.guidance, "seed": req.seed},
    }


@app.post("/api/v1/generate/remove-background", tags=["AI 生成"])
async def remove_background(req: RemoveBackgroundRequest):
    """
    真实像素级去背景 — 从图像四边泛洪填充，移除与边缘主色相近的区域，输出透明 PNG。
    适合纯色/弱纹理背景的产品图；复杂背景请接入 rembg/segment-anything 模型。
    Phase 38：泛洪算法逐字下沉 background_removal 引擎（第 24 引擎），本端点为薄包装，
    响应契约字段逐字保持（既有 L2/L4 断言作回归安全网）。
    """
    path = validate_file_path(req.image_path)
    eng = _require("bg_removal")
    out = Path(req.output_path) if req.output_path else DATA_ROOT / "outputs" / f"{path.stem}_no_bg.png"
    t0 = time.perf_counter()
    try:
        result = await asyncio.to_thread(eng.remove_background, path, out, req.threshold)
    except ImportError:
        raise HTTPException(status_code=503, detail="Pillow 未安装，无法执行去背景")
    duration = time.perf_counter() - t0
    logger.info(f"Remove background: {path} -> {result['output']} "
                f"({result['background_cleared_ratio']:.1%} pixels cleared)")
    payload = {
        "input": result["input"],
        "output": result["output"],
        "algorithm": result["algorithm"],
        "background_cleared_ratio": round(result["background_cleared_ratio"], 4),
        "has_alpha": True,
        "processing_time": round(duration, 3),
    }
    await _emit("bg_removed", {
        "input": payload["input"], "output": payload["output"],
        "algorithm": payload["algorithm"],
        "background_cleared_ratio": payload["background_cleared_ratio"],
        "processing_time": payload["processing_time"],
    })
    return payload


# ----------------------------------------------------------------------------
# 印前检查 (P2 Print QA Engine — 9 项真实检查规则)
# ----------------------------------------------------------------------------

@app.post("/api/v1/preflight", tags=["印前检查"])
async def run_preflight(req: PreflightRequest):
    # Phase 55：先过全仓共用的路径门，再跑引擎。门拦下的输入不进引擎、不发
    # preflight.completed——账本不为没发生过的检查记 completed。
    doc = validate_readable_file(req.document_path)
    service = get_preflight_service()
    result = await asyncio.to_thread(service.check, req.document_path)
    report = service.doctor.generate_report(result)
    report["print_ready"] = result.overall_status == "pass"
    report["input_reachability"] = input_reachability(doc)
    await _emit("preflight.completed", {
        "document_path": req.document_path,
        "print_ready": report["print_ready"],
        "overall_status": getattr(result, "overall_status", None),
        "findings": len(report.get("findings") or []),
        "reachability": report["input_reachability"]["verdict"],
        "size_bytes": report["input_reachability"]["size_bytes"],
    })
    return report


@app.get("/api/v1/preflight/history", tags=["印前检查"])
async def preflight_history(limit: int = 10):
    service = get_preflight_service()
    return {"history": service.get_history(limit)}


@app.post("/api/v1/preflight/auto-fix", tags=["印前检查"])
async def preflight_auto_fix(req: PreflightRequest):
    # Phase 55：与 check 同一把门。"对不存在的文档执行自动修复"不该以 200 +
    # skipped 列表收场，而该在传输层就被拒绝（404/403/400）。
    doc = validate_readable_file(req.document_path)
    service = get_preflight_service()
    result = await asyncio.to_thread(service.check, req.document_path)
    fixable = [f for f in result.findings if f.auto_fixable]
    outcome = await asyncio.to_thread(service.auto_fix, result)
    payload = {
        "document": req.document_path,
        "input_reachability": input_reachability(doc),
        "fixable_issues": len(fixable),
        "fix_executed": outcome["fix_executed"],
        "applied": outcome["applied"],
        "skipped": outcome["skipped"],
        "output": outcome["output"],
        "remaining": [
            {"code": f.code, "severity": f.severity.value, "suggestion": f.suggestion}
            for f in result.findings if not f.auto_fixable
        ],
    }
    await _emit("preflight.autofixed", {
        "document_path": req.document_path,
        "fix_executed": payload["fix_executed"],
        "fixable_issues": payload["fixable_issues"],
        "applied": payload["applied"],
        "skipped": payload["skipped"],
        "output": payload["output"],
        "remaining_count": len(payload["remaining"]),
    })
    return payload


# ----------------------------------------------------------------------------
# 工作流引擎 (P2 Workflow Engine — 真实节点图执行)
# ----------------------------------------------------------------------------

@app.post("/api/v1/workflow/define", tags=["工作流"])
async def workflow_define(req: WorkflowDefineRequest):
    wf_mod = _require("workflow")
    engine = get_workflow_engine()
    try:
        nodes = [
            wf_mod.NodeConfig(id=n.id, type=wf_mod.NodeType[n.type.upper()], name=n.name, config=n.config)
            for n in req.nodes
        ]
    except KeyError as e:
        valid = [t.name.lower() for t in wf_mod.NodeType]
        raise HTTPException(status_code=400, detail=f"未知节点类型 {e}，可选: {valid}")
    edges = [wf_mod.Edge(source_node_id=e.source_node_id, target_node_id=e.target_node_id, condition=e.condition) for e in req.edges]
    definition = wf_mod.WorkflowDefinition(
        id=req.id or str(uuid.uuid4())[:8],
        name=req.name, description=req.description, version=req.version,
        nodes=nodes, edges=edges, variables=req.variables,
    )
    errors = wf_mod.WorkflowEditorSupport.validate_workflow(definition)
    wf_id = engine.define_workflow(definition)
    return {"workflow_id": wf_id, "validation_errors": errors, "nodes": len(nodes), "edges": len(edges)}


@app.get("/api/v1/workflow/tools", tags=["工作流"])
async def workflow_tools():
    """工作流 Tool 节点可调用的真实工具清单（Phase 51）。

    **全部字段实时派生**：工具集取自 WorkflowEngine.tool_registry 本体，逐工具的参数
    契约取自其绑定的 pydantic 请求模型字段与端点协程签名——没有第二份手写清单可漂移。
    每个工具都转调既有端点协程（同一份校验/安全策略/副作用/事件），故"能被图编排"
    与"能被 HTTP 调用"在语义上同源；coroutine 字段如实标出异步工具。"""
    engine = get_workflow_engine()
    reg = engine.tool_registry
    tools = []
    untagged = []
    for name in sorted(reg):
        meta = getattr(reg[name], "_wf_meta", None)
        if meta is None:
            # 未挂元信息（例如评测台/外部代码临时 register_tool 的桩）→ 如实标出，
            # 绝不假装它也是端点转调工具。
            untagged.append(name)
            tools.append({"tool": name, "domain": "unregistered", "handler": None,
                          "request_model": None, "params": [], "coroutine": False,
                          "signature": str(inspect.signature(reg[name]))})
            continue
        tools.append(dict(meta))
    domains = sorted({t["domain"] for t in tools})
    per_domain: Dict[str, int] = {}
    for t in tools:
        per_domain[t["domain"]] = per_domain.get(t["domain"], 0) + 1
    api_routes = [r for r in app.routes if str(getattr(r, "path", "")).startswith("/api/v1/")]
    wf_mod = _MODULES.get("workflow")
    _cls = getattr(wf_mod, "WorkflowEngine", None)
    _doc = getattr(_cls, "_resolve_value", None)
    _doc = (_doc.__doc__ or "") if callable(_doc) else ""
    # 语义句与引用语法都从引擎本体取：语义取 docstring 的"语义："行，语法取引擎自己
    # 编译的 _REF_RE.pattern。任何一处改引擎而不改这里，公开契约都会跟着走——
    # 手抄一份口径正是 Phase 54 之前那行"根变量不存在时返回 None"漂移成假话的原因。
    _sem = next((ln.strip() for ln in _doc.splitlines() if ln.strip().startswith("语义：")), "")
    _ref_re = getattr(_cls, "_REF_RE", None)
    return {
        "total": len(tools),
        "domains": domains,
        "domain_count": len(domains),
        "tools_per_domain": {k: per_domain[k] for k in sorted(per_domain)},
        "tools": tools,
        "untagged": untagged,
        "ai_invoker_registered": engine.ai_invoker is not None,
        "derived_from": "workflow_engine.tool_registry",
        "api_route_total": len(api_routes),
        # 引用语法说明派生自引擎本体（WorkflowEngine._resolve_value 的 docstring），
        # 而非另写一份容易漂移的手抄文档；语义句以 docstring 的"语义："行为唯一真相源
        "reference_syntax": {
            "variable": "$name",
            "path": "$name.field.subfield（Phase 51 新增）",
            "index": "$name.list.0.field（负下标可用）",
            "semantics": (_sem or "（引擎 docstring 未声明语义，见 implemented_by）"),
            "grammar": _ref_re.pattern if _ref_re else None,
            "implemented_by": "workflow_engine.WorkflowEngine._resolve_value",
            "supports_path": "点路径" in _doc,
            "doc_evidence": _doc.strip().splitlines()[0] if _doc.strip() else None,
        },
        "fabricated": False,
    }


@app.get("/api/v1/workflow/{workflow_id}/validate", tags=["工作流"])
async def workflow_validate(workflow_id: str):
    wf_mod = _require("workflow")
    engine = get_workflow_engine()
    wf = engine.workflows.get(workflow_id)
    if not wf:
        raise HTTPException(status_code=404, detail=f"工作流不存在: {workflow_id}")
    return {"workflow_id": workflow_id, "errors": wf_mod.WorkflowEditorSupport.validate_workflow(wf)}


@app.get("/api/v1/workflow/{workflow_id}/visualize", tags=["工作流"])
async def workflow_visualize(workflow_id: str):
    wf_mod = _require("workflow")
    engine = get_workflow_engine()
    wf = engine.workflows.get(workflow_id)
    if not wf:
        raise HTTPException(status_code=404, detail=f"工作流不存在: {workflow_id}")
    return wf_mod.WorkflowEditorSupport.generate_visualization(wf)


@app.post("/api/v1/workflow/{workflow_id}/execute", tags=["工作流"])
async def workflow_execute(workflow_id: str, req: WorkflowExecuteRequest):
    engine = get_workflow_engine()
    if workflow_id not in engine.workflows:
        raise HTTPException(status_code=404, detail=f"工作流不存在: {workflow_id}")
    instance_id = await engine.execute(workflow_id, req.input_data)
    inst = engine.get_instance_status(instance_id) or {}
    _node_status = inst.get("node_status") or {}
    _outputs = inst.get("node_outputs") or {}
    await _emit("workflow.executed", {
        "workflow_id": workflow_id,
        "instance_id": instance_id,
        "status": inst.get("status"),
        "current_node": inst.get("current_node"),
        "error_message": inst.get("error_message"),
        "node_count": len(_node_status),
        "nodes_completed": sum(1 for v in _node_status.values() if str(v) in ("completed", "success")),
        # 只记结果的键名与工具名，不把大载荷（如 512 维向量）灌进事件环形缓冲
        "result_keys": sorted(inst["result"].keys()) if isinstance(inst.get("result"), dict) else None,
        "tools_used": sorted({str(v.get("tool")) for v in _outputs.values()
                              if isinstance(v, dict) and v.get("tool")}),
    })
    return {"instance_id": instance_id, **inst}


@app.get("/api/v1/workflow/instance/{instance_id}", tags=["工作流"])
async def workflow_instance_status(instance_id: str):
    engine = get_workflow_engine()
    inst = engine.get_instance_status(instance_id)
    if not inst:
        raise HTTPException(status_code=404, detail=f"实例不存在: {instance_id}")
    return inst


# ----------------------------------------------------------------------------
# 批量处理 (工作者预算 + 逐文件计时 + 实测并发效率自报)
# ----------------------------------------------------------------------------

# 服务级工作者预算：onefile 单机服务，超过逻辑核数只会加剧 GIL 争抢与线程切换。
_BATCH_WORKER_CAP = max(1, min(16, os.cpu_count() or 4))
# Phase 52 实测（L6-C3a/C3b/B5）：三种批处理操作的每一步都以纯 Python CPU 核为主
# （印前分析、PIL 泛洪去背、缩略图 + 向量），在 GIL 下加工作者买不到吞吐——
# workers=1 墙钟 27.51 s、workers=4 反而 31.62 s，单件耗时升到 5.95x，
# 同服务 /health 的 p95 饥饿同步放大。因此对这三类操作只授予 1 个工作者，
# 并把"请求了多少 / 实际给了多少 / 实测效率"如实写进响应，而不是假装并行。
_CPU_GIL_BOUND_OPERATIONS = frozenset({"preflight", "import_asset", "remove_background"})
# Phase 52 F5（实测于 outputs/p52_f5_probe.out）：上面那条"只授予 1 个工作者"一度只是
# *每个请求* 的事实——semaphore 建在 batch_process 内部，于是 3 个并发客户端各拿 1 个工作者，
# 服务却同时在跑 3 个像素核：/metrics 的 peak_concurrency=3，而三份响应同时自报
# workers_applied=1 / worker_policy=cpu_gil_bound_serialized / parallel_efficiency=1.0，
# 单件耗时从 1.5 s 涨到 3.76 s（2.5x），/health 探针 p50 142.8 ms、p95 566.3 ms。
# 声称"串行"而实测并发 3，是契约与实况的分叉，不只是性能问题，故串行必须做在服务级：
# 一把全进程共享的闸门，让"已串行"成为可被 peak_concurrency 计数器证伪的事实。
_gil_gate: Optional[asyncio.Semaphore] = None


def _gil_batch_gate() -> asyncio.Semaphore:
    """CPU/GIL 绑定批处理的服务级串行门（跨请求，进程内唯一）。

    惰性创建：Python 3.10+ 的 Semaphore 不在构造时绑定事件循环，首个请求内在运行循环上
    创建即可，uvicorn 单循环下等价于模块级常量。"""
    global _gil_gate
    if _gil_gate is None:
        _gil_gate = asyncio.Semaphore(1)
    return _gil_gate


_batch_lock = threading.Lock()
_batch_in_flight = 0            # 当前正在执行的文件任务数（跨请求求和）
_batch_peak = 0                 # 本进程生命周期内出现过的最大并发文件任务数


def _batch_grant(operation: str, requested: int) -> int:
    """实际授予的并发数 = 客户端请求 ∩ 服务级剩余预算 ∩ 该操作可测量的收益（>=1）。"""
    with _batch_lock:
        spare = max(1, _BATCH_WORKER_CAP - _batch_in_flight)
    allowed = min(max(1, int(requested)), _BATCH_WORKER_CAP, spare)
    if operation in _CPU_GIL_BOUND_OPERATIONS:
        allowed = 1
    return allowed


def _batch_slot_enter() -> None:
    global _batch_in_flight, _batch_peak
    with _batch_lock:
        _batch_in_flight += 1
        if _batch_in_flight > _batch_peak:
            _batch_peak = _batch_in_flight


def _batch_slot_exit() -> None:
    global _batch_in_flight
    with _batch_lock:
        _batch_in_flight -= 1


def _batch_counters() -> Dict[str, int]:
    """给 /api/v1/metrics 自报的批处理并发计数（单一真相源，绝不在别处另算一套）。"""
    with _batch_lock:
        return {"worker_cap": _BATCH_WORKER_CAP,
                "peak_concurrency": _batch_peak,
                "in_flight": _batch_in_flight}


@app.post("/api/v1/batch/process", tags=["批量处理"])
async def batch_process(req: BatchProcessRequest):
    """对真实文件列表并发执行 preflight / import_asset / remove_background"""
    if req.operation not in ("preflight", "import_asset", "remove_background"):
        raise HTTPException(status_code=400, detail="operation 可选: preflight / import_asset / remove_background")
    if not req.file_paths:
        raise HTTPException(status_code=400, detail="file_paths 为空")

    workers_requested = max(1, int(req.workers))
    workers = _batch_grant(req.operation, workers_requested)
    # F5：CPU/GIL 绑定的操作用服务级共享闸门（跨请求串行），其余操作仍按本请求授予的
    # 预算建 semaphore。workers_applied 说的就是这把闸门的许可数，两者不得各说各话。
    sem = (_gil_batch_gate() if req.operation in _CPU_GIL_BOUND_OPERATIONS
           else asyncio.Semaphore(workers))
    results: List[Dict[str, Any]] = []

    async def one(fp: str):
        # Phase 51：批处理不再自带第二套实现，而是逐字复用单件端点协程。
        # 原实现有三处真实缺陷：import_asset 绕过扩展名白名单与 asset.ingested 事件；
        # preflight 绕过 preflight.completed 事件；remove_background 只做 convert("RGBA")
        # 却回报 success=True（并未真正去背）。复用端点后校验/副作用/事件三者自动一致。
        async with sem:
            _batch_slot_enter()
            t0 = time.perf_counter()
            try:
                if req.operation == "preflight":
                    r = await run_preflight(PreflightRequest(document_path=fp))
                    s = r.get("summary") or {}
                    # Phase 55 起，缺件/禁入/非文件三类输入在 run_preflight 入口的全仓
                    # 共用门前就被 HTTPException 拦下（404/403/400），逐文件错误串因此是
                    # **门的判定**而不是引擎的中文诊断消息；旧判词只能指向
                    # "文档不存在或不可读"，因为修前根本没有门。
                    # 引擎把"文档不存在或不可读"吞成一条 PRINT-999 system 发现（HTTP 层仍 200），
                    # 该码正是引擎自认"检查失败"的标记（print_qa 的 L1 自检亦以它判定），
                    # 此处保留为第二道防线：门后仍被引擎判失败的输入（如无法解析的文件）
                    # 依旧不得混进 success。
                    # 注意不能用 overall_status=="error" 作判据：那是"检出严重问题"的判级
                    # （RGB 色彩模式即 error），文件确实被检查成了，必须记 success=True。
                    _pf_sys = next((_f for _f in (r.get("findings") or [])
                                    if isinstance(_f, dict) and _f.get("code") == "PRINT-999"), None)
                    if _pf_sys:
                        raise RuntimeError(str(_pf_sys.get("message") or f"印前检查未成: {fp}"))
                    detail = {"status": s.get("overall_status"), "findings": s.get("total_findings"),
                              "print_ready": r.get("print_ready"),
                              "reachability": r.get("input_reachability")}
                elif req.operation == "import_asset":
                    mime = mimetypes.guess_type(str(fp))[0]
                    d = await add_asset(AssetAddRequest(file_path=fp, mime_type=mime))
                    detail = {"asset_id": d.get("id")}
                else:  # remove_background — 走真实泛洪算法（background_removal 引擎）
                    d = await remove_background(RemoveBackgroundRequest(image_path=fp))
                    detail = {"output": d.get("output"),
                              "background_cleared_ratio": d.get("background_cleared_ratio"),
                              "algorithm": d.get("algorithm")}
                return {"file": fp, "success": True, "time": round(time.perf_counter() - t0, 3), **detail}
            except HTTPException as e:
                return {"file": fp, "success": False, "error": e.detail, "time": round(time.perf_counter() - t0, 3)}
            except Exception as e:
                return {"file": fp, "success": False, "error": str(e), "time": round(time.perf_counter() - t0, 3)}
            finally:
                _batch_slot_exit()

    t0 = time.perf_counter()
    results = list(await asyncio.gather(*(one(fp) for fp in req.file_paths)))
    total_time = time.perf_counter() - t0
    success = sum(1 for r in results if r["success"])
    # 实测并发效率 = sum(逐文件耗时) / 墙钟：串行时≈1.0，真并行才会 >1。
    # 这是自报数字而不是宣传语——它会让"workers: 8"这类声称在生产环境里当场失真。
    per_file_sum = sum(float(r.get("time") or 0.0) for r in results)
    efficiency = round(per_file_sum / total_time, 3) if total_time > 0 else 0.0
    payload = {
        "total_files": len(req.file_paths),
        "operation": req.operation,
        "workers": workers,
        "workers_requested": workers_requested,
        "workers_applied": workers,
        "worker_cap": _BATCH_WORKER_CAP,
        "worker_policy": ("cpu_gil_bound_serialized"
                          if req.operation in _CPU_GIL_BOUND_OPERATIONS
                          else "within_service_worker_budget"),
        "parallel_efficiency": efficiency,
        "success": success,
        "failed": len(results) - success,
        "total_time": round(total_time, 3),
        "avg_per_file": round(total_time / len(req.file_paths), 4),
        "results": results,
    }
    await _emit("batch.completed", {
        "operation": req.operation, "workers": workers,
        "workers_requested": workers_requested, "workers_applied": workers,
        "worker_policy": payload["worker_policy"],
        "parallel_efficiency": efficiency,
        "total_files": payload["total_files"], "success": success,
        "failed": payload["failed"], "total_time": payload["total_time"],
        # 事件账本只留摘要与产物指针，不复制逐文件大载荷
        "outputs": [r.get("output") for r in results if r.get("output")],
        "asset_ids": [r.get("asset_id") for r in results if r.get("asset_id")],
    })
    return payload


# ----------------------------------------------------------------------------
# 快照 / 事务 / 任务恢复引擎 (Recovery Engine — §51/§52/§73 · 核心能力#7 版本回滚)
# ----------------------------------------------------------------------------

def _recovery_snapshot_to_dict(s) -> Dict[str, Any]:
    return {
        "id": s.id,
        "created_at": s.created_at.isoformat() if s.created_at else None,
        "snapshot_type": s.snapshot_type,
        "document_path": s.document_path,
        "checksum": s.checksum,
        "size_bytes": s.size_bytes,
        "backup_path": s.backup_path,
        "recoverable": s.metadata.get("recoverable"),
        "recovery_reason": s.metadata.get("recovery_reason"),
    }


def _assert_path_not_blocked(file_path: str) -> None:
    """快照路径安全检查：仅拦截黑名单/穿越，不要求文件已存在（Adobe 文档可能尚未导出到本地）。"""
    try:
        checker = get_path_checker()
        if checker.is_blocked(file_path):
            raise HTTPException(status_code=403, detail=f"路径被安全策略阻止: {file_path}")
    except HTTPException:
        raise
    except Exception:
        pass  # 安全引擎缺失时不阻断，在 diagnostics/capabilities 中如实报告


@app.post("/api/v1/recovery/snapshot", tags=["快照与恢复"])
async def recovery_create_snapshot(req: SnapshotCreateRequest):
    """为本地文档创建快照。文件真实存在→字节级备份+SHA256，可恢复；否则诚实标记不可恢复。"""
    if req.snapshot_type not in ("full", "incremental"):
        raise HTTPException(status_code=400, detail="snapshot_type 可选: full / incremental")
    _assert_path_not_blocked(req.document_path)
    eng = get_recovery_engine()
    snap = await eng.snapshot_manager.create_snapshot(
        req.document_path, snapshot_type=req.snapshot_type, metadata=req.metadata
    )
    d = _recovery_snapshot_to_dict(snap)
    await _emit("snapshot.created", {
        "snapshot_id": d.get("id"), "document_path": d.get("document_path"),
        "snapshot_type": d.get("snapshot_type"), "checksum": d.get("checksum"),
        "size_bytes": d.get("size_bytes"), "recoverable": d.get("recoverable"),
        "backup_path": d.get("backup_path"),
    })
    return d


@app.get("/api/v1/recovery/snapshots", tags=["快照与恢复"])
async def recovery_list_snapshots(document_path: Optional[str] = None):
    eng = get_recovery_engine()
    snaps = eng.snapshot_manager.list_snapshots(document_path)
    return {"count": len(snaps), "snapshots": [_recovery_snapshot_to_dict(s) for s in snaps]}


@app.post("/api/v1/recovery/snapshot/{snapshot_id}/restore", tags=["快照与恢复"])
async def recovery_restore_snapshot(snapshot_id: str):
    """真实恢复：把备份字节写回原路径并做完整性校验；无本地备份则诚实返回 restored=false。"""
    eng = get_recovery_engine()
    sm = eng.snapshot_manager
    snap = sm.get_snapshot(snapshot_id)
    if not snap:
        raise HTTPException(status_code=404, detail=f"快照不存在: {snapshot_id}")
    restored = await sm.restore_snapshot(snapshot_id)
    await _emit("snapshot.restored", {
        "snapshot_id": snapshot_id, "document_path": snap.document_path,
        "restored": restored, "checksum": getattr(snap, "checksum", None),
        "reason": None if restored else snap.metadata.get(
            "recovery_reason", "no_local_backup_or_integrity_mismatch"),
    })
    return {
        "snapshot_id": snapshot_id,
        "document_path": snap.document_path,
        "restored": restored,
        "reason": None if restored else snap.metadata.get("recovery_reason", "no_local_backup_or_integrity_mismatch"),
    }


@app.post("/api/v1/recovery/transaction/begin", tags=["快照与恢复"])
async def recovery_transaction_begin(req: TransactionBeginRequest):
    eng = get_recovery_engine()
    tx_id = await eng.transaction_manager.begin_transaction(
        req.task_id, user_id=req.user_id, document_path=req.document_path
    )
    return {"transaction_id": tx_id, "task_id": req.task_id}


@app.post("/api/v1/recovery/transaction/{transaction_id}/commit", tags=["快照与恢复"])
async def recovery_transaction_commit(transaction_id: str):
    eng = get_recovery_engine()
    ok = await eng.transaction_manager.commit_transaction(transaction_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"事务不存在或已结束: {transaction_id}")
    return {"transaction_id": transaction_id, "committed": True}


@app.post("/api/v1/recovery/transaction/rollback", tags=["快照与恢复"])
async def recovery_transaction_rollback(req: TransactionRollbackRequest):
    """回滚事务：若初始快照含本地备份则真实还原文件，否则如实返回 rolled_back=false。"""
    eng = get_recovery_engine()
    snap_id = None
    tx = eng.transaction_manager.active_transactions.get(req.transaction_id)
    if tx:
        snap_id = tx.get("snapshot_id")
    rolled_back = await eng.transaction_manager.rollback_transaction(req.transaction_id)
    return {
        "transaction_id": req.transaction_id,
        "rolled_back": rolled_back,
        "restored_from_snapshot": snap_id,
        "note": None if rolled_back else "初始快照无本地可恢复副本（Adobe 文档需宿主导出）或事务不存在",
    }


@app.post("/api/v1/recovery/task/register", tags=["快照与恢复"])
async def recovery_task_register(req: TaskRegisterRequest):
    if req.total_steps < 1:
        raise HTTPException(status_code=400, detail="total_steps 必须 >= 1")
    eng = get_recovery_engine()
    st = await eng.register_task(req.task_id, total_steps=req.total_steps, context=req.context)
    await _emit("task.registered", {"task_id": st.task_id, "total_steps": st.total_steps, "status": st.status})
    return {"task_id": st.task_id, "status": st.status, "total_steps": st.total_steps}


@app.post("/api/v1/recovery/task/{task_id}/progress", tags=["快照与恢复"])
async def recovery_task_progress(task_id: str, req: TaskProgressRequest):
    eng = get_recovery_engine()
    try:
        await eng.update_task_progress(task_id, current_step=req.current_step)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    st = eng.task_states[task_id]
    await _emit("task.progress", {"task_id": task_id, "current_step": st.current_step,
                                  "total_steps": st.total_steps,
                                  "percent": round(st.current_step / st.total_steps * 100, 1) if st.total_steps else 0.0})
    return {"task_id": task_id, "current_step": st.current_step, "total_steps": st.total_steps}


@app.post("/api/v1/recovery/task/{task_id}/complete", tags=["快照与恢复"])
async def recovery_task_complete(task_id: str):
    eng = get_recovery_engine()
    try:
        await eng.complete_task(task_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    await _emit("task.completed", {"task_id": task_id, "status": "completed"})
    return {"task_id": task_id, "status": "completed"}


@app.post("/api/v1/recovery/task/{task_id}/fail", tags=["快照与恢复"])
async def recovery_task_fail(task_id: str, req: TaskFailRequest):
    eng = get_recovery_engine()
    try:
        await eng.fail_task(task_id, req.error_message, recoverable=req.recoverable)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    await _emit("task.failed", {"task_id": task_id, "error": req.error_message, "recoverable": req.recoverable})
    return {"task_id": task_id, "status": eng.task_states[task_id].status}


@app.get("/api/v1/recovery/task/{task_id}/report", tags=["快照与恢复"])
async def recovery_task_report(task_id: str):
    eng = get_recovery_engine()
    report = eng.generate_recovery_report(task_id)
    if "error" in report:
        raise HTTPException(status_code=404, detail=report["error"])
    return report


@app.get("/api/v1/recovery/tasks/orphaned", tags=["快照与恢复"])
async def recovery_orphaned_tasks():
    """崩溃恢复：列出超过心跳阈值仍处 running 的悬空任务（§73）。"""
    eng = get_recovery_engine()
    orphans = await eng.check_orphaned_tasks()
    return {
        "count": len(orphans),
        "tasks": [
            {"task_id": t.task_id, "status": t.status, "current_step": t.current_step,
             "total_steps": t.total_steps,
             "last_heartbeat": t.last_heartbeat.isoformat() if t.last_heartbeat else None}
            for t in orphans
        ],
    }


# ----------------------------------------------------------------------------
# 能力真值表 (Capability Truth Table — §25 / §99.3；落实 §2「禁止伪造成功」红线)
# ----------------------------------------------------------------------------

_CAPABILITY_MANIFEST: List[Dict[str, Any]] = [
    # status ∈ {supported, needs_adobe_host, prototype_not_shipped, not_shipped}
    {"id": "license", "name": "离线签名 License", "status": "supported",
     "note": "HMAC-SHA256 签发 + 机器码绑定 + 降级启动，真实端点已上线"},
    {"id": "config_persistence", "name": "配置/路径持久化", "status": "supported",
     "note": "读写落盘 + 版本诊断"},
    {"id": "asset_engine", "name": "素材库/去重/语义搜索", "status": "supported",
     "note": "持久化索引 + SHA256 词元向量 + 缩略图 + License 追踪，真实处理"},
    {"id": "print_qa", "name": "印前检查与自动修复", "status": "supported",
     "note": "出血/DPI/色彩/条码/叠印等规则真实判定 + PIL 真实扩边修复；Phase 55 起 check/auto-fix 先过全仓共用路径门（403 禁入 / 404 缺件 / 400 非文件），200 报告另带机器可读的 input_reachability（path/reachable/is_file/verdict/size_bytes 逐字段从磁盘读出），下游不再靠模式匹配引擎的中文诊断串判断检没检成"},
    {"id": "workflow_engine", "name": "工作流节点图执行", "status": "supported",
     "note": "DFS 环检测 + 安全 AST 条件求值($var) + 真实分支路由 + 结果回传"},
    {"id": "model_router", "name": "模型路由/Provider 调用", "status": "supported",
     "note": "成本/延迟/隐私路由 + 熔断 + 真实 token 统计；实际生成依赖已配置的 Provider"},
    {"id": "ai_provider_integration", "name": "真实 AI Provider 接入 (§101/§102 + §17 Planner)", "status": "supported",
     "note": "第 18 个引擎 provider_engine：通用 **OpenAI-兼容** 客户端(一个适配器真实对接 OpenAI/DeepSeek/通义兼容/智谱兼容/月之暗面/LM Studio/Ollama /v1/企业网关，满足 §101 禁硬编码)。真实 urllib HTTP 往返(/chat/completions、/images/generations、GET /models 健康检查) + Authorization Bearer 鉴权 + 真实 SSE 流式增量拼装 + 真实 usage/cost 记录 + 429/5xx 指数退避 + 熔断。**§102.3 API Key 用 Windows DPAPI(CryptProtectData)加密落盘、内存解密、返回体脱敏、明文永不出日志/入盘**(DPAPI 不可用则密钥仅驻内存)。/plan 落实 §17 Planner(goal/steps/tools/constraints/protected_objects/verification/rollback)，模型输出非 JSON 时诚实 planner_output_unparseable 保留 raw，绝不硬编造。**诚实边界(§2 红线)**：未配置 Provider→/chat /images /plan 一律 502 `no_provider_configured` `fabricated=false`，绝不凭空伪造回复或 images_generated；401/403→auth_failed、不可达→unreachable、超时→如实降级。真实 AI 输出质量取决于所接入模型本身(base_url+有效 key+配额属**部署配置**，同'需真实更新源/需真实 NAS 服务器')——本条目交付并端到端验证的是 Provider **集成机制**本身。Phase 37 起以独立 provider 黄金域纳入评测台：未配置三态不虚报 + 脱敏字面真值与 DPAPI 落盘明文绝不在盘 + 真实 HTTP 对话 content/usage/Bearer 头/单请求逐字段精确 + 401 不重试与熔断开路零 HTTP。"},
    {"id": "security", "name": "权限/风险/高危操作", "status": "supported",
     "note": "最小权限交集 + 风险分级 + 已批准操作经真实 executor 派发，无 executor 不虚报"},
    {"id": "recovery_snapshot", "name": "快照/事务/回滚/崩溃恢复", "status": "supported",
     "note": "本地文档字节级备份 + SHA256 完整性校验恢复 + 任务状态持久化 + 悬空任务检测"},
    {"id": "image_remove_background", "name": "去背景", "status": "supported",
     "note": "PIL 边缘泛洪算法真实像素处理；Phase 38 起逐字下沉为第 24 引擎 background_removal_engine，端点改薄包装契约不变，并以独立 bg_removal 黄金域纳入评测台：清 8400px/84% 逐数精确、被包围孔洞 alpha 必 255（证泛洪≠全局色键）、阈值边界与幽灵输入绝不落盘、两次跑幂等确定性反查"},
    {"id": "knowledge_rag", "name": "知识库 RAG", "status": "supported",
     "note": "真实抽取 txt/md/csv/html/docx/pdf → 分块 → SHA256 词元向量 → 余弦检索 + 作用域隔离；PDF 缺解析库时诚实标 extraction_failed"},
    {"id": "tool_registry", "name": "工具注册表/能力协商", "status": "supported",
     "note": "真实工具契约(风险 L0-L4 + requires_adobe_host)持久化 + 宿主能力矩阵上报；ps.*/ai.* 未获宿主上报则 runnable_now=false，不伪造可执行。Phase 80 起逐字下沉为第 28 引擎 service_registry_engine（端点委托其单一真相源，服务侧不再持平行副本），并以独立 tool_registry 黄金域纳入评测台：种子 10 枚逐数、file.delete 判 L4 高风险、ps.add_layer 需宿主、无宿主上报时 runnable_now 真值 [6 true/4 false] 逐项精确、宿主能力上报后 add_layer 翻 true/export 仍 false、register 后回读落盘"},
    {"id": "skill_runtime", "name": "技能清单运行时", "status": "supported",
     "note": "从 DATA_ROOT/skills/*/manifest.json 真实加载 + 权限白名单/工具引用静态校验，越权清单拒绝落盘。Phase 80 起逐字下沉为第 28 引擎 service_registry_engine（validate_skill/skills_dir/load_skill_manifests 端点委托其单一真相源），并以独立 skill_runtime 黄金域纳入评测台：干净清单零错、越权/未知工具/坏风险各点名且 err_count 逐数精确、缺 id 的清单如实跳过、加载后 loaded_ids 逐项真值"},
    {"id": "feature_flags", "name": "功能开关", "status": "supported",
     "note": "JSON 持久化开关读写：默认四枚（webp_preview_enabled/auto_preflight_on_import=true，beta_design_review/telemetry_enabled=false）种子落盘、逐枚 set 回读、损坏文件回退默认不崩。Phase 80 起逐字下沉为第 28 引擎 service_registry_engine（get_flags/set_flag 端点委托其单一真相源），并以独立 feature_flags 黄金域纳入评测台：seeded_keys 与 defaults 逐项精确、写盘字节回读全等、跨实例持久化、merge 保留自定义不覆写既有且补默认、损坏回退默认"},
    {"id": "design_compare", "name": "设计版本对比", "status": "supported",
     "note": "§50 对 before/after 对象列表做真实 added/removed/changed/moved diff，并归类为 Geometry/Text/Color/Asset/Layer/Visual 六类结构化差异；Phase 38 起逐字下沉为第 23 引擎 design_compare_engine（纯函数零状态），端点改薄包装契约不变，并以独立 compare 黄金域纳入评测台：六类逐数归类、moved 逐字段、结构增删按 type 归类与未知兜底、compare_keys 限字段集等逐项真值"},
    {"id": "delivery_package", "name": "一键交付包", "status": "supported",
     "note": "§69 真实归档已存在文件(Source/Preview/Report/Print)+SHA256 清单+README+ZIP；Phase 55 起逐文件台账分三形制 not_found/blocked_by_policy/not_a_file（修前一枚 missing_or_blocked 把缺件与策略禁入压成同一个词，目录/空串更把整请求崩成 500），收录侧另带 included_files[].inside_data_root 与 outside_root_count——数据根之外的真实文件如实标注、不拦"},
    {"id": "websocket_events", "name": "WebSocket 实时事件流", "status": "supported",
     "note": "§7/§10 /ws 真实任务事件广播(连接补发历史 + task.* 实时推送) + /events/recent 轮询降级"},
    {"id": "typography_engine", "name": "字体匹配/排版", "status": "supported",
     "note": "§43/§111 fontTools 真实扫描系统字体+families/weight/Unicode覆盖+hmtx真实文本度量(缺字如实计数)+覆盖度硬门槛的缺失字体匹配+可配置替换规则；缺 fontTools 时端点诚实返回 503 不伪造度量；Phase 33 起纳入评测台黄金域：自供夹具真 TTF（溯源解析→重放索引，本机字体零干扰）6 条真值用例+2 条校准"},
    {"id": "design_review", "name": "多角色设计评审", "status": "supported",
     "note": "§49 Visual/Brand/Production/Commercial 四角色真实规则引擎：WCAG相对亮度对比度、最小可读字号、出血、安全边距、印刷DPI、品牌主色一致性、文字重叠面积；多数问题给确定性自动修复建议，非AI打分伪装"},
    {"id": "customer_revision", "name": "客户改稿引擎", "status": "supported",
     "note": "§48/§114 自然语言→操作映射→命名对象定位→冲突检查→ChangeRequest：命中唯一proposed/多候选needs_clarification/受保护conflict/无法识别unparsed绝不编造；apply真实缩放改色改字号调亮旋转删除；视觉相似度定位诚实标 needs_model 不谎称具备"},
    {"id": "color_engine", "name": "色彩引擎", "status": "supported",
     "note": "§44 真实色彩计算：Pillow 中位切分对真实图像取调色板(逐像素占比)+HSL 色轮和谐(互补/类似/三角/分裂/矩形)+WCAG2.1 对比度判级+sRGB↔HSL↔HSV↔CMYK↔Lab↔ΔE76/94/2000 标准公式；Pantone/专色精确匹配需授权色库，端点如实返回 needs_licensed_pantone_data 仅给标注'非 Pantone 标准'的 CMYK 等价值，绝不编造 Pantone 编号"},
    {"id": "layout_engine", "name": "布局排版引擎", "status": "supported",
     "note": "§45 真实计算几何(纯数学无外部依赖)：AABB 碰撞(重叠面积/IoU/轴向深度)+六向对齐+等距分布+安全边距越界检测+四边留白+网格吸附+宽高比校验+综合体检，结论可复算非启发猜测"},
    {"id": "packaging_dieline_vdp", "name": "包装刀版/拼版/变量数据", "status": "supported",
     "note": "§46/§47 真实几何与文件 IO：由成品+出血+安全边距推导裁切/出血/安全三框与最小 artwork；up-to-N 拼版(旋转取优/单张满版数/用纸数/利用率)；变量数据合并真实读取 CSV/XLSX(openpyxl)/JSON 按映射逐行生成变体，缺列如实记 missing_columns 不臆造填充"},
    {"id": "packaging_barcode", "name": "条码/二维码渲染 (EAN-13/EAN-8/QR)", "status": "supported",
     "note": "§46 真实标准算法(纯 Python+Pillow，无外部条码库)：EAN-13/EAN-8 校验位计算 + L/G/R 编码表 + 首字 parity 模式 + 起止/中间 guard，逐模块位串渲染成真实 PNG(含静区 11 模块)。Phase 57 起 QR 亦**真实实现**(ISO/IEC 18004，纯标准库，零三方条码库)：v1..v10 × L/M/Q/H 四级、Numeric/Alphanumeric/Byte(UTF-8，含中文) 三种模式（Kanji/ECI 不投，如实抛错）、GF(256) Reed-Solomon 分块纠错、格式 BCH(15,5)⊕0x5412 与版本 BCH(18,6)、8 掩码罚分全评优选优、导入期 Σ块数×(数据+纠错)==总码字数 算术自检；静区按码制各取标准值(一维 11 / QR 4)，缺省不再串用一维口径。非法校验位/位数/纠错等级、载荷超出 v1..v10 → 400 原话拒，未覆盖码制(Code-128/DataMatrix/PDF417)如实 needs_external 不伪造图像——Code-128 留守的理由是 107 项模式表不可几何推导，且本机 cv2.barcode 只出解码器不出编码器、本轮 144 次第三方夹具试读未证得该码制（同一套光栅画出的 EAN-13 可逐字读回，作通道正对照）。Phase 34 起以独立 barcode 黄金域纳入评测台：95/67 模块位串逐字符对 GS1 公开例全等 + 真像素逐点采样 + 错误原文逐字精确；跨实现逐字读回实证在 L2/L4 以第三方解码器完成（QR 自 Phase 57、一维 EAN-13/EAN-8/UPC_A 自 Phase 62 在实测边距梯上逐字读回，且产品申报位串与尺子手抄 GS1 编码逐模块相等；旧口径「本机无可运行的一维解码 oracle 可证伪」已于 2026-10-11 被上述实测反驳；产品与评测台源码绝不 import 该解码器）"},
    {"id": "packaging_panel_topology", "name": "面板关系/刀版拓扑 (Panel Relation)", "status": "supported",
     "note": "§46 Panel Relation 真实交付：输入结构化矢量刀版(轴对齐面板矩形列表)，纯几何(矩形共边求交 + BFS 连通分量)真实算邻接关系、折线(共享边)、折叠连通性、邻接度、外轮廓裁切线长；断开刀版诚实标 disconnected、非法面板入 rejected 不崩溃、空输入 400。role_hint 为几何启发式(role_basis=geometric-heuristic)不谎称语义鉴定。与栅格图→面板的 packaging_panel_recognition（Phase 47 起为独立第 25 引擎，输入位图）严格区分：本项输入是结构化矢量矩形，不做任何图像解码。Phase 34 起以独立 topology 黄金域纳入评测台：直插盒 9 面板度序列/折长/裁切线/面积全字段精确 + 三态如实 + 启发式标注诚实核验"},
    {"id": "auto_update", "name": "自动更新客户端", "status": "supported",
     "note": "§75-§77 真实更新客户端机制(纯 stdlib)：urllib 拉清单 + HMAC-SHA256 验签 + semver 版本比较 + SHA-256 逐字节完整性校验下载 + 原子替换(备份→覆盖→校验→失败自动回滚)；运行中 EXE 被占用则如实登记 pending_restart 而非谎称已更新。未配置 feed_url→no_update_configured、网络不可达→feed_unreachable、验签失败→integrity_failed，均如实降级不伪造'发现新版本'。真实更新源(HTTPS 服务器+签名私钥+已发布版本)属部署配置。Phase 37 起以独立 update 黄金域纳入评测台：无更新源诚实拒伪造 + HMAC 规范化字节独立复算 + 分支矩阵逐项真值（版本门先于验签门次序如实钉死）+ 真实 HTTP 下载 sha256 相符与原子应用字节回归全链"},

    {"id": "adobe_uxp_bridge", "name": "Adobe UXP 桥接（连接协议/命令下发/结果对账）", "status": "supported",
     "note": "§8/§9/§24/§25/§26 桥接**机制**真实交付并端到端验证：随机 Session Token 认证 + loopback/Origin 校验 + 端口动态发现(discovery 文件) + §9 统一请求帧下发 + 宿主真实结果对账 + 超时/宿主掉线诚实降级；§24 PhotoshopAdapter/IllustratorAdapter(CapabilityDetector/ActionExecutor/DocumentReader/ObjectMapper/ErrorMapper/VersionAdapter) + §26 兼容矩阵以**能力**而非版本字符串门控。传输为纯 stdlib HTTP 长轮询(宿主作客户端发起，服务端不反连沙箱)，与 Phase10 Provider 客户端同构、不引入第三方包。关键诚实边界：本项 supported 指的是**管道**为真(已用协议合规 mock host 验证握手/认证/下发/对账/超时全链路)，绝非伪造 Adobe 文档变更——真正的 ps.*/ai.* 文档写入仍须真实带许可证的 Photoshop/Illustrator UXP 宿主(见 ps_document_write/ai_document_write=needs_adobe_host)；未连接真实宿主时 /uxp/dispatch 一律 502 no_host_connected(fabricated=false)。"},

    # —— CorelDRAW 只读桥接（Phase 77；无 UXP 运行时，宿主经 COM attach-only）——
    {"id": "cdr_document_read", "name": "CorelDRAW 只读桥接（文档/页面/对象）", "status": "supported",
     "note": "Phase 77 真实交付：CorelDRAW 没有 UXP 运行时，故复用同一条 §8/§9/§25 桥接管道，宿主端由 outputs/cdr_host.py 以 COM **attach-only** 方式接入（GetActiveObject 只查询已运行实例的 ROT，绝不 Dispatch、绝不启动 GUI）。三个工具 cdr.document.get / cdr.page.list / cdr.object.list 能力键同为 document_read、风险 L0，真实读取 Documents/Pages/Shapes；未知 intent 如实 not_supported（宿主侧映射 capability_unsupported_at_host），受保护对象与写操作一律不在本能力内。**服务端零 COM**：agent_service 与桥接引擎不 import win32com/pythoncom，文档读取只发生在宿主进程，结果经 /uxp/result 回传后由 DocumentReader.reconcile 对账（source=host）。本机只读探针实测（2026-11-02，收据 p77_3b_cdr_host_probe）：pywin32 五模块均在、HKCR\\CorelDRAW.Application 存在（CLSID {F3150002-2C6D-4630-B100-8F6EE502AF96}，CurVer CorelDRAW.Application.22；.24 亦注册 {CBA90002-0F8D-4A20-B926-5E850738E4BD}，.25/.26 缺席），探测时 CorelDRAW 进程未运行，故 attach 被如实跳过（不启动 GUI）。诚实边界：未运行 CorelDRAW 或未启动宿主脚本时 /uxp/dispatch 一律 502 no_host_connected（fabricated=false）；本项 supported 指**只读桥接机制**，不声称任何写侧能力，也不声称真实文档数据可在无宿主时取得。**交付边界**：宿主端 `cdr_host.py` 与 `outputs/adobe-plugins/` 属源码树资产，不随 onefile EXE、安装包或便携 ZIP 出货；运行宿主要求本机 Python 与 pywin32，且 CorelDRAW 须已在运行（attach-only，绝不 Dispatch/启 GUI）。尚无黄金用例域（Registry 2.0 如实报 no_golden_cases_yet）。写侧最小集另立 cdr_document_write（Phase 78），本项只读边界不变。"},

    # —— CorelDRAW 最小写集（Phase 78：create/move 两工具；无 delete/rename/save/export）——
    {"id": "cdr_document_write", "name": "CorelDRAW 最小写集（新建/移动对象）", "status": "supported",
     "note": "Phase 78 真实交付：在 Phase 77 只读桥之上只加两个受控写工具——cdr.object.create（kind ∈ rectangle/ellipse/text）与 cdr.object.move（按 index 或 name 平移 dx/dy），能力键 object_write、风险 L1，宿主端仍由 outputs/cdr_host.py 以 COM **attach-only** 执行（GetActiveObject 只查询已运行实例，绝不 Dispatch、绝不启动 GUI）。两条纪律线：① **双层确认门**——服务端 /uxp/dispatch 在 args.confirm is not True 时直接返回 status=confirmation_required + plan（含 tool/intent/risk=L1/目标字段/effects/reversible=true/**save=false**）且**零入队零变更**；宿主 execute_intent 对写 intent 二次校验 confirm is True，缺省一律拒绝，绝不在宿主侧补默认值。② **写后回读**——宿主创建/移动后立即回读对象几何与位置，payload 带 verified（回读成功才 true）、undo_group（best-effort BeginCommandGroup/EndCommandGroup，成功 grouped、COM 不支持则如实 unsupported）、source（com/fixture，夹具仅供自测且显式标注），服务端 DocumentReader.reconcile 逐字段搬运宿主的 object_count/verified/undo_group，绝不本地合成成功。**绝不 Save**：不注册 object.delete/object.rename/object.save/document.save/export 等任何工具，收到即 unknown_tool；写作路径中不调用任何 Save/SaveAs 成员。错误码保持原义：invalid_args / object_not_found / target_ambiguous（映射 needs_clarification，绝不挑第一个）/ com_api_unavailable，服务端 ErrorMapper 只做稳定码映射不吞成成功。**服务端零 COM**：agent_service 与 adobe_bridge_engine 不 import win32com/pythoncom，真正写入只发生在宿主进程，结果经 /uxp/result 回传后对账（source=host）。诚实边界：未运行 CorelDRAW 或未启动宿主脚本 → 502 no_host_connected（fabricated=false）；只连了只读能力宿主 → capability_not_negotiated（能力门控优先于同名宿主匹配）；本项 supported 指**最小写集机制与回读对账**为真，不声称 delete/rename/save/export，也不声称无宿主时能写入。**交付边界**：宿主端 `cdr_host.py` 与 `outputs/adobe-plugins/` 属源码树资产，不随 onefile EXE、安装包或便携 ZIP 出货；运行宿主要求本机 Python 与 pywin32，且 CorelDRAW 须已在运行。尚无黄金用例域（Registry 2.0 如实报 no_golden_cases_yet），其端到端证据在 L2/L4 以夹具宿主（source=fixture）实证确认门/回读/无 Save 边界。"},

    # —— 需 Adobe 宿主（服务端不能凭空伪造）——
    {"id": "ps_document_write", "name": "Photoshop 真实写入", "status": "needs_adobe_host",
     "note": "ps.* 工具需运行在 Photoshop UXP 运行时；§8 桥接现已能把命令下发给已连接宿主并对账其真实回执，但服务端自身仍仅管理 Design IR/上下文，宿主未连接则 no_host_connected，绝不伪造执行结果"},
    {"id": "ai_document_write", "name": "Illustrator 真实写入", "status": "needs_adobe_host",
     "note": "ai.* 工具需运行在 Illustrator UXP 运行时；桥接管道已就绪(见 adobe_uxp_bridge)，真实矢量文档变更仍须宿主执行，未连接诚实降级"},
    {"id": "adobe_capability_report", "name": "插件能力上报", "status": "needs_adobe_host",
     "note": "Capability Map 端点已上线，但真实能力矩阵须由宿主插件上报"},
    {"id": "evaluation_center", "name": "能力评估中心", "status": "supported",
     "note": "Phase 24 真实交付（旧原型硬编码 0.85 模拟分已整文件剔除）：评估引擎真实调用 color/layout/packaging（Phase 27 起另含 brand，Phase 28 起另含 print_qa/knowledge_rag，Phase 30 起另含 revision/review/security，Phase 31 起另含 workflow（评测台内新构造的零注册工具引擎，不注入服务单例），Phase 32 起另含 asset/recovery（评测台内逐用例于评测私有沙箱先清后建全新构造，素材引擎磁盘态复活风险与恢复引擎字节夹具独立性均为此设计），Phase 33 起另含 typography（本机字体属外因，评测台以 fontTools 现场编译自供夹具真 TTF 走溯源解析→夹具重放索引断链，零桩且本机字体零干扰），Phase 34 起另含 barcode/topology（packaging 引擎升格独立黄金域：位串期望在评测源码内以 GS1 公开例逐字符手抄独立核对、像素真值直接采样渲染 PNG、拓扑几何全字段精确），Phase 36 起另含 license 授权 4 条/config 配置 4 条/model_routing 路由 4 条（三域各 4 条，HMAC 纯函数零状态；配置/路由逐用例于评测私有 _config|_router 沙箱先清后建，注册表为自供 YAML 夹具走真实解析路径），Phase 37 起另含 update 更新 4 条/nas NAS 4 条/provider Provider 4 条（三域模块即引擎、逐用例私有沙箱先清后建，网络面由评测自起 127.0.0.1:0 进程内 mock HTTP 驱动），Phase 38 起另含 compare 对比 4 条/bg_removal 去背景 4 条（design_compare 与 image_remove_background 逐字下沉为第 23/24 引擎后的独立黄金域；bg_removal 夹具为评测自造真像素图，被包围孔洞保留证泛洪≠全局色键），Phase 47 起另含 panel_recognition（第 25 引擎栅格刀版→面板几何反解：评测自绘 220×100 刀版夹具且与引擎自测夹具在画幅/划分/虚线常数上相互独立、字节 sha256 双向钉死，6 面板×2535px 与邻接 7/轮廓 1248 手算可核；同图在 max_line_width 3 与 12 下如实两态、降级分支字段集与正常分支全等；无墨/无封闭域/被面积门限全拒三态均 0 面板，并以同沙箱同法出 6 面板作反零向量），Phase 49 起另含 installer_manifest（第 26 引擎安装清单静态校验 + 只读安装态探测：夹具为评测自造 .iss 文本与真实载荷文件，逐用例落评测私有 _manifest 沙箱先清后建，机器相关项一律经模块级接缝桩反证而非依赖本机环境；Phase 53 起同域另并两枚只读仪器——编译面把 installer\\build-evidence.json 的每个数值与磁盘实读重算对账（自报 compiled/fabricated 一律 ignored、只回显），预检面按七路三态判本机安装条件（未探测进 unknowns，绝不把“没探测”写成“不可写”），Phase 50 起另含 desktop_hub（第 27 引擎桌面 Hub 客户端：评测自起 127.0.0.1:0 进程内真 HTTP 夹具驱动真实 urllib 请求，routes/seen 逐实例隔离、判分字段把端口归一成 \"BASE\" 以保证顶层/嵌套两次逐字段全等；GUI 窗口本体刻意不入评测台——无显示会话会让评测脆断，其活体实证在 L2/L4），Phase 56 起另含 brand_semantics（品牌语义透传集成面：评测自起进程内 mock 视觉 HTTP，绝不合成交付），Phase 57 起 barcode 黄金域并入纯标准库 QR 真值五条（跨实现解码实证在 L2/L4 由第三方 OpenCV 完成，评测台本体零三方依赖）、Phase 58 起另含 installer_execution 安装执行 6 条（第 22 引擎 runtime 的执行器：假 spawn／假注册表／假落点根三枚接缝注入同一份执行器源码，temp 沙箱里真起子进程真建目录树——plan 只算不跑且 sha 与字节由用例独立读盘复算、十七道拒绝臂各按 reason 码拒绝且拒绝的代价为零（零落盘零注册表写入）、installed 只由安装前后目录树＋HKCU 卸载项差集派生且同一物理文件被两个标签各扫一遍不许数两遍、同一份自报在盘上有物与盘上空手两趟判分相反、一装两卸三形制配只读对账九项重算逐字段（含『装好未卸而现场仍在位』的诚实中间态 install_verified_awaiting_uninstall，与同一份台账配上痕迹已不在场时点名『被台账之外的手段抹掉』）、证据件与用户数据分账——安装台账与缺省安装日志本就落在被观察的数据根内，第二轮执行必然被自家重写，故逐份另账回显而绝不进既有数据基线，轮 2 仍判 uninstall_verified 且既有数据零偏离））引擎跑 151 条确定性黄金用例（期望逐字段手工可核，其中 4 条品牌用例以 Pillow 真像素夹具驱动调色板/CIEDE2000 一致性/alpha 几何判分，5 条品牌语义用例（Phase 56 起独立 brand_semantics 黄金域）以评测自起 127.0.0.1:0 进程内 mock 视觉 HTTP 驱动真实 urllib 往返，核验模型原文逐字段全等透传＋usage=133 真值＋data URL 从真 PNG 字节独立复算＋Bearer 头在场＋明文密钥缺席＋非 JSON 保留 raw 原文不硬编 schema＋未注入/未配置两形制诚实 needs_external，2 条印前用例以 RGB-72DPI/CMYK-300DPI 真像素位图夹具核验色彩·分辨率·出血判定，2 条知识检索用例核验 SHA-256 确定性向量下检索排序相对真值与 project 作用域按 project_id 的可见/隔离真值，3 条改稿用例以零时钟·零随机指令流核验 proposed/conflict/needs_clarification 矩阵与 240×96/#8B0000/#939393/32pt 真实换算及闲聊 0 编造，3 条评审用例双向核验缺陷夹具 score=68 逐问题精确与合规夹具满分零问题反证，3 条安全用例核验路径三阻一放四向哨兵、User∩Skill∩Tool 交集三态与高危操作无 executor 不虚报，6 条工作流用例以零时钟·纯内存节点图核验环检测唯一报错原文、三段链 9 条日志消息精确贯通、失败传播下下游 n3 键不虚设于 node_status、条件分叉 else 支如实 skipped、循环体聚合 lp_iterations=3 真值，以及剥去实例 id 与时间戳后同引擎二次执行逐字段幂等，6 条素材用例核验 token-hash 稀疏向量非零位与哈希桶独立复算精确、真实 PNG 入库元数据/缩略图落盘/向量与描述符同源、检索排序自匹配 1.0 与异图 0.402015126104 双钉、CC-BY 署名规则双向分支逐项回读、生成元数据回读全字段且 None 不虚设、冷读回计数 (2,2,1,1) 与删除级联清空后 (1,1,0,1)，6 条恢复用例核验快照校验和==固定字节独立 sha256 复算、损坏后还原字节真回归且幽灵快照判 False、事务剥除 id/时间戳后历史恰 {active,2 ops} 且回滚真实还原、任务注册字段真值并真实落盘、检查点恢复字节回归且幽灵任务如实抛 ValueError 原文、无本地文件快照诚实记 checksum=none/不可恢复绝不谎报，6 条排版用例以自供夹具真 TTF 核验 serif 度量 3989 units→95.736pt/行高 28.8 逐数精确、sans 缺字如实计 1 不谎称全覆盖、双专码位覆盖硬门槛唯一候选、字重罚分降序 0.9861>0.6542、替换规则真实落盘回读与来源标注、幽灵字体判 None+KeyError 原文，14 条条码用例（Phase 34 一维真值 5 条 + Phase 57 纯标准库 QR 真值 5 条 + Phase 63 一维解码独立复算 oracle 真值 4 条）核验 EAN-13 95 模块与 EAN-8 67 模块位串逐字符对 GS1 公开例全等、渲染 PNG 真像素逐点采样护条/静区/留白黑白精确、双码制非法校验位 ValueError 原文逐字精确且绝不落盘、Code-128/DataMatrix 留守不虚报不产图与 13 位合法直传与参数化几何真值；QR 五条为手抄 ISO/IEC 18004 Table 7/8/9 常量独立核对——40 格容量边界「满容留本版/+1 升版」、data==cap+(2|3)、Σ块数×(data+ec)==total、校正图形按 n=2+v//7 与首 6/末 side-7/间距偶且极差≤2 推导，21×21 栅格与申报 matrix 逐格同源＋静区整带全白＋固定黑模块 (4v+9,8) 真采样，评测侧独立 BCH 尺子（格式 gen 0x537⊕掩码 0x5412、版本 gen 0x1F25）先复现 ISO 三串公布例再逐字符对账格式/版本位并核掩码=罚分最小，四条诚实边界 ValueError 原文逐字精确且绝不落盘，5 条拓扑用例核验直插盒 9 面板度序列/折长 640/裁切线 870/面积 28700 全字段、折线坐标逐数精确、共边部分重叠 shared=5.0、断开+非法混合三态如实与空输入抛原文、role_hint 几何启发式如实标注），4 条面板识别用例核验 3×2 网格 Otsu 阈值 0/线像素 817/6 面板 bbox 逐数与面积 2535×6、cut 4 段 552px 全占与 fold 3 段覆盖 267/347、邻接 7 与度序列 [3,3,2,2,2,2] 及逐边 separating_line=fold、轮廓 2×234+2×390=1248 与背景余量 22000-909-15210=5881，参数门效应（线宽 3↔12 两态、px_per_mm=2 → 65/39px 即 32.5/19.5mm 而像素面积不变），三态诚实空报（含 rejected 逐条含门限原文）与三次调用逐字段幂等（含 params 默认门限回显 64/12/8/12/0.95/0.98），6 条安装清单用例（Phase 49 静态四条 + Phase 53 静态开火臂一枚 + Phase 58 R14 卸载撤销七臂一条：写入型 [Registry] 缺撤销声明必 error 且逐行回指写入行号与值名，同键 uninsdeletevalue／同键 uninsdeletekey／祖先键 uninsdeletekey 三臂零检出，uninsdeletekeyifempty 挂本键或祖先键照旧开火（清不掉仍有值的键，故绝不进 accepted_flags），HKCU 写入而 HKLM 撤销跨根照旧开火，每臂 counts.total 恒等于 findings 长度；R14 的判据出处不是 Inno 文档转述而是 Phase 58 本机真卸载实测——卸载器 exit 0、安装根与卸载子键消失、四棵数据树归零而应用键三值原样留守）核验评测自造合规 .iss 的零检出（并以分段计数 Setup7/Files2/Icons2/Registry3 与「注释空行不计数、行尾续行并入上一条且行号取起始物理行」钉死解析器真值）、评测自造破损 .iss 的 12 项检出逐 id 逐项序精确（8 error·3 warn·1 info，含 Inno 合法常量 tmp 永不解析的机器无关触发与 install_date 一条字面量双落 R1/R8 两条检出，另含 R14 那条 registry_key_left_after_uninstall），并以生产合规写法零误报、去掉 runtime_data_root 后的 11 项差分（8 error·2 warn·1 info）双向排除误报与漏报，四路接缝反证（磁盘存在桩判假→合规清单必报缺件、glob 桩判真→零命中通配 error 消失、环境目录桩双向→预建目录结论翻转、注册表桩→installed 随卸载项翻转而本机真实便携标记不翻 installed），入参拒绝原文逐字 + 两次调用 JSON sha256 全等；Phase 53 的编译面与预检面五枚用例在 Phase 59 起各归各账——编译证据 3 条（编译面正控：三独立信号同立、证据字节与 sha256 与本用例独立重算一致、自报 compiled/fabricated 翻转不改判、校验前后沙箱逐条全等即零落盘；编译面四臂：等长替换唯 sha256 可查、编译后改脚本判陈旧而静态复校照旧 ok、产物缺席优先于编译未证成、退出码非零无完成行判 compile_not_proven；告警与完备性：告警四臂全文等式与截断下界两口径、缺字段判 evidence_incomplete 且 provenance 不替编译器说话）、预检 2 条（预检面正控：七项全 True 且 blocked/unknown 皆空、令牌走显式桩而真令牌只以关系式判、写探针真落盘自清、{autopf} 两条候选、不给 override 时真实安装根零探针；预检面十臂：同名文件／载荷缺失先阻断后未知／证据缺席与篡改／余量读不到与不足／目标 exe 占用三态／令牌未提权与探测不到／写探针三种归因，四路接缝还原后回基线）；Phase 59 另为第 22 引擎 installer_runtime_engine 的运行时面新建 runtime_guard 黄金域，运行时守护 5 条（写探针三臂：可写臂 writable/bytes_verified/cleaned 三真且 method=real_write_readback_delete、目标与父路径为普通文件两臂三假且 error 起手 FileExistsError、探针文件零残留且 target 归一 <SB>/probe-ok；磁盘阈值两侧：min=0 必 meets_min=True、min=2^62 必 False、负阈值抛点名错误，free/used 连续量不入判分；端口真 bind 两侧：自起监听桩端口必 available=False 且 detail 带 OSError 原文、OS 指派后自关端口必 available=True 且 detail=None、越界入参抛点名错误而端口号只作结构核对；单实例锁全链：首取 True/False→同实例重取 False/True→release True→释放后同实例 True/False→另一实例 False/True 且它的 released=False，backend=kernel32-named-mutex、非法锁名抛点名错误、重复 release False；run_diagnostic：三项全 pass 且计数 [3,3,0] 与 status=healthy、timestamp 刻意为 None、带端口臂四项含 port_available、降级臂 data_root_writable=fail 且 status=degraded 且 checks_failed 恒为 performed-passed 派生式、引擎构造不建根而探针真建根 [False,True]、残留只有 .runtime-guard 一枚目录），4 条桌面 Hub 用例核验三条只读 GET 的逐字段搬运（seen 计数仪证零 POST、八行窗口文本逐字符全等、最新报告按 max(finished_at) 挑中）、跨源自报引擎清单不一致须显 agree=False 而非静默且 200+JSON 但缺 version 的形状不合须判该源降级（绝不再落 \"vNone\" 占位行，该缺陷由 P50 探针实抓并根治）、端口关闭/非造境服务（HTML 与 404 两种传输形制逐源独立不连坐）/JSON 残缺三形制各有真值且缺失字段一律 None、POST 通道唯一性（只读路径恒零 POST、显式 eval_run 恰一次体 {} 的 POST、500 原文照抄不吞、未知动作抛 HubError 且零新增请求）、无显示会话下 open_hub 如实回 created=False/reason=headless_requested，通过率=passed/executed 真实派生，Phase 79 起另含 1 条桥接握手用例核验进程内 mock UXP 宿主驱动 adobe_bridge 真实异步握手（auth_reject／origin_reject／unknown_host_reject 三拒、connected 回连、能力矩阵回显、令牌脱敏且两次握手随机、剥实例戳后同沙箱二次往返逐字段幂等）、1 条桥接读取用例核验无宿主诚实 no_host_connected 与有宿主单条 cdr.object.list 命令下发 intent=object.list、轮询取回 submit_result 后 reconcile 派生 source=host／object_count=3／document_id=doc-7／result_source=cdr 且 fabricated=False、1 条桥接写入用例核验缺省 confirmation_required 零入队、未协商 capability_not_negotiated 拒绝、带 confirm 的真实写往返 reconcile verified=True／object_count=4／undo_group 在场（此三枚 verified=True 仅证协议机制对 mock 宿主的异步握手→下发→轮询→回填→对账往返成立，绝不等于已对真实 Adobe／CorelDRAW 文档做改写；ps/ai 文档写入与插件能力上报仍如实留 needs_adobe_host 诚实边界）、Phase 80 起另含服务侧持久化配置三枚黄金用例——1 条功能开关用例核验空沙箱种子 4 默认键精确且落盘 JSON=默认、set_flag 全新实例读回证落盘非内存、已有文件 setdefault 合并不覆盖自定义键与既存值只补缺、损坏文件如实回退默认（不半写不伪造）；1 条工具注册表用例核验种子 10 工具、无宿主上报时 6 枚非宿主 runnable=true／4 枚宿主=false（不伪造可执行）、宿主仅上报 ps.add_layer→只这一枚翻 True 其余仍 False（逐项派生非一刀切）、register 落盘后新实例读回；1 条技能运行时用例核验干净清单零错、越权＋未注册工具＋非法风险三错逐条命中、技能校验读同一工具注册表（先缺后 register 校验即转通过，跨能力联动非各写各）、带 id 清单加载与缺 id 如实跳过——三域逐字下沉为第 28 引擎 service_registry（端点全部委托其单一真相源），评测台于私有沙箱直接构造 ServiceRegistryEngine 驱动同一份出货代码而非另造桩，Phase 81 起另含服务侧最后一批两枚黄金用例——websocket_events 1 条：于私有 EventHub 实例（进程级单例不受扰）核验 json_safe 把 Path→str、Enum→name、set→list、tuple→list、None 保留、自定义对象转 dict 的归一化，空连接下 delivered_to=0 而台账 published_total/kind_counts 仍完整累计（不伪造交付量），连发截断后 history 长度恒=capacity 而 published_total 继续到 8（截断与未截断两形制区分），一枚坏 ws 发送抛错后死连接被从簿记摘除、delivered_to 只数活连接，disconnect 幂等（同一 ws 再摘不炸），recent(limit) 观测面的 published_total/kind_counts/history_capacity/kinds_seen/truncated 逐字段与内部真值一致（端点侧派生不各写一份）；delivery_package 1 条：于私有沙箱全新构造 DeliveryEngine 核验真实磁盘复制 + 逐文件 SHA256 由磁盘实物独立重算全等、数据根之外真实文件如实标注（inside_data_root=False、outside_root_count=1）而不拦，拒收三形制 not_found/blocked_by_policy/not_a_file 分账且 tally 三键恒在（缺席与 0 两回事），ZIP namelist 与 manifest 交叉——3 收录 + README + manifest 恰 5 且拒收路径绝不入 ZIP，manifest.json 原子落盘复读全等非半写，空 plan+全拒收仍出 README+manifest 且 included=0。此两枚与前述三域同为 scored 真值用例，全局负控校准恒 34 不动，另含 34 条负控校准用例（期望故意写错，Phase 28 新增印前假期望与检索假排序各一条，Phase 30 新增改稿判未变/评审判满分/安全判误阻断各一条，Phase 31 新增工作流假期望「未注册工具判成功」一条，Phase 32 新增素材假期望「锅图冒充顶位」与恢复假期望「幽灵快照判可还原」各一条，Phase 33 新增排版假期望「serif 宽写成 sans 值」与「缺字字体冒充过门槛」各一条，Phase 34 新增条码假期望「校验位 7 写成 0」与拓扑假期望「断开刀版判连通」各一条，Phase 36 新增授权/配置/路由假期望各一条，Phase 37 新增更新/NAS/Provider 假期望各一条，Phase 38 新增对比假期望「fill_color 改动归 layer」与去背景假期望「色键冒充泛洪清孔洞」各一条，Phase 47 新增面板识别假期望「coverage 0.77 的虚线折线判成 cut」一条，Phase 49 新增安装清单假期望「把合规预建目录与运行时数据根无交集判成 error」一条，Phase 50 新增 Hub 假期望「端口关闭仍当成连上且有 Registry 计数」一条，Phase 53 新增编译面假期望「把证据自报 compiled=False 当成编译未证成的判据」一条（case id calib-manifest-build-wrong-expect），Phase 56 新增品牌语义假期望「无 Provider 时凭空合成调性关键词」一条（case id calib-brand-semantics-wrong-expect），Phase 57 新增 QR 假期望「手抄满容 17 字符判需升版」一条（case id calib-barcode-qr-wrong-expect），Phase 58 新增安装执行假期望「把安装器自报退出码 0＋完成行当成已装成的判据」一条（case id calib-exec-trust-self-report），Phase 59 新增预检面假期望「把调用方关闭写探针当成可写已证、preflight_unknown 判成 preflight_ready」一条（case id calib-manifest-preflight-wrong-expect）与运行时守护假期望「把数据根写不进去的降级臂 status 当成 healthy」一条（case id calib-guard-wrong-expect））保证判分器可证伪——校准用例若被判 pass 即判分失真，报告如实标 calibration_error 且 honest=False；报告 JSON 真实落盘于数据根，compare 对两份真实历史报告逐用例求翻转与差值；agent 意图/vision OCR 类别因无确定性本地真值（需 LLM/CV）如实列入 not_evaluated，桌面 Hub 的 GUI 窗口本体（真建窗/逐 Label 文本/真实 invoke）亦如实列入 not_evaluated（无显示会话会让评测脆断，其活体实证在 L2/L4 完成），绝不为未交付能力编造分数。端点：POST /api/v1/eval/run、GET /api/v1/eval/suite|reports|reports/{id}|recommendations、POST /api/v1/eval/compare"},

    # —— 品牌智能红线分裂（Phase 25：可真实计算部分 supported，语义部分 not_shipped）+ 其余占位实现 ——
    {"id": "brand_color_analysis", "name": "品牌要素分析（调色板/品牌色一致性/Logo几何）", "status": "supported",
     "note": "Phase 25 真实交付（旧原型硬编码 Logo/色彩/字体示例数据的实现已整文件剔除）：调色板复用 color_engine 中位切分逐像素统计；品牌色一致性对真实中位切分调色板逐品牌色求 CIEDE2000 最近邻，matched 由真实 ΔE<=阈值判定（负控：纯绿品牌色遇纯红图必 matched=False、ΔE>50；黑白中性轴 ΔE2000 真值=100.0）；Logo 几何为真实 alpha 通道逐像素包围盒（bbox/四边留白/宽高比/填充率，alpha_threshold 真实参与过滤）。NaN/Inf/非数值阈值一律 400 拒绝，不静默伪成功。端点：POST /api/v1/brand/palette|color-consistency|logo-geometry"},
    {"id": "brand_semantic_recognition", "name": "品牌语义识别（Logo含义/调性/字体识别）", "status": "supported",
     "note": "Phase 56 真实交付（旧 not_shipped 占位判词已整段剔除）：把「需外部 CV/LLM」误判为不可交付，与 Phase 47『需真实 CV』被纯几何反解、Phase 50『需 Tauri 工具链』被纯标准库 tkinter 推翻同族——真实视觉模型并非缺失，本服务早已内置真实 OpenAI-兼容 Provider 客户端（ai_provider_integration=supported），缺的只是把 Logo 图像接进去的这条通路。现由 recognize_semantics 真实读取图像字节→构造 data:image/…;base64 多模态 URL→委派已注入 Provider 的 chat() 走真实 /chat/completions 往返→**逐字透传**模型返回的结构化语义（tone_keywords/logo_semantics/typography_guess 等全为所配置模型的真实输出，本引擎不合成任何一个字符、不臆造置信度）。诚实降级三形制各有真值且绝不由本引擎代劳伪造：① 未注入/未配置 Provider→status=needs_external + fabricated=False（HTTP 面映射 502，拒绝凭空编造回复，同 Provider 客户端未配置 502 口径）；② Provider 可达但模型返回非 JSON→provider_output_unparseable，保留 raw（截断至 2000）不硬编 schema；③ 传输失败（unreachable/timeout/auth_failed/429 熔断等）原样透传其真实状态。密钥经 DPAPI 加密、返回值与日志一律脱敏，明文密钥绝不出现在响应里。真实语义质量取决于所接入模型本身（属部署：需 base_url+有效 Key+配额），与『需真实更新源/NAS 服务器』同性质——本轮端到端验证的是**透传集成机制**本身（真读图/真构造 data URL/真鉴权头/真 usage/逐字透传/不伪造），未配置时诚实 502。Phase 56 起以独立 brand_semantics 黄金域纳入评测台（评测自起 127.0.0.1:0 进程内 mock 视觉 HTTP 驱动真实 urllib，逐字透传全等 + data URL 从真 PNG 字节独立复算 + Bearer 头在场 + 明文密钥缺席 + 非 JSON 保留 raw 四路判分）。GET /api/v1/brand/semantics 仍回边界声明并另带 provider_configured 实时真值，POST /api/v1/brand/semantic-insight 为真实透传入口（图像路径先过 P55 共用路径门）。"},
    # —— 安装/运行时守护红线分裂（Phase 26：真实可观测的守护 supported，安装执行域当时
    #    如实 not_shipped；Phase 58 执行器交付后该域退役边界、转 supported）——
    {"id": "installer_runtime_guard", "name": "运行时守护（写入探针/磁盘/端口/单实例/诊断）", "status": "supported",
     "note": "Phase 26 真实交付（旧原型无条件 'running'/example.com 更新/恒过 '数据库检查' 已整文件剔除）：数据根写入探针真实写→读回字节比对→清理（负控：父路径为普通文件必如实报 writable=False 而非抛崩）；磁盘余量 shutil.disk_usage 真值 + meets_min 真实阈值比较；端口可用性真 bind socket（被占用端口必报 available=False）；单实例锁 Windows 命名内核对象 CreateMutexW+ERROR_ALREADY_EXISTS 真检测、非 Windows 退化为 O_EXCL 锁文件；诊断状态由逐项真实检查派生且 passed+failed==performed。原型虚构的'四服务启停'表已删除（本产品仅单一服务进程，无可启停集群）。Phase 59 起本能力并入评测台 runtime_guard 黄金域 5 条真值用例 + 1 条校准（假期望把数据根写不进去的降级臂 status 当成 healthy，诚实判分必红），期望逐项来自只读探针的现场实测而非文档转记；它与 installer_execution 域各自独立成域——前者判运行时守护面（写／盘／端口／锁／诊断），后者判执行面（十道门与真装真卸差集）。端点：GET /api/v1/runtime/diagnostic、POST /runtime/port-check|single-instance、GET /runtime/install-actions"},
    {"id": "installer_execution", "name": "安装/卸载执行（静默安装/注册表/快捷方式/卸载器）", "status": "supported",
     "note": "Phase 58 真实交付（本家族最后一次漂移：not_shipped 清零，留守的诚实边界只剩三枚需 Adobe 宿主的写侧能力）。旧判词「属 Inno Setup 编译产物与系统权限域，非服务端运行时能力：GET /api/v1/runtime/install-actions 如实返回 needs_installer_environment」里，前半句到 Phase 53 仍成立（那时 iscc 从未被找到、setup.iss 从未真编译），后半句在 Phase 53 被三路固定目录寻径推翻（详见 Phase 53 更正段，那句『iscc 缺』属仪器伪影而非环境事实），而**写侧执行**这一块——跑安装器进程、写/删卸载注册项、建快捷方式、静默卸载——自 Phase 26 红线分裂起一直如实记 not_shipped。本轮退役它：退役 ≠ 改写，被退役边界的原文逐字留在 install_actions() 返回的 retired_boundary.was 里（since=Phase 26 / retired_in=Phase 58 / why 随附），而不是把历史判词抹平。交付形态是**执行器 + 只读对账面**两件套，纪律四条：① HTTP 面零执行——安装域在 /openapi.json 里共 6 条且全为 GET（4 条 installer 只读 + runtime/install-actions 契约 + 本条只读对账），结构上够不着执行器，这与『预检面绝不安装』同一条被断言的结构性事实；真正的执行入口只有同一可执行文件的 CLI 子命令 install-exec install|uninstall|plan|state，且必须逐字交出显式同意令牌 ZAOJING-CONFIRM-EXECUTE-INSTALLER（缺省/空串/近似值一律 confirmation_required 拒绝）。② 十道门前置于任何副作用：确认令牌、制品在允许根内且是真文件、构建台账同轮（脚本/产物字节+sha256 双路对账，等长替换即此形）、安装根绝对/非源码树内/在允许根内/**必须为空**（静默安装不接管别人的目录）、卸载器路径同源、落点清单可派生——门开在写盘之前，不是失败之后的解释。③ 回执只由磁盘与注册表派生：installed 由安装前后两棵目录树 + HKCU 卸载项的**差集**算得（basis 恒为 derived_from_disk_and_registry），完成与否由轮询现场判，退出码/自报 installed/performed/status 一律进 self_report_ignored 与 recorded_only 只回显不判定（exit_code 0 而盘上零产物 ⇒ 如实 install_unverified、installed=False，绝不被洗成回执）。④ 只读对账 GET /api/v1/installer/execution 把 CLI 留在 DATA_ROOT 下的 execution-evidence.json 拿现场重算逐字段比（制品 sha 三方相等、日志仍可重读且 sha 不变、安装根/卸载项/应用键存在性与台账说法吻合、既有数据逐字节完好），比不中点名到字段；形制 ledger_absent（本机从未跑过，不伪造）| executed_verified | install_verified_awaiting_uninstall（台账只到 install_verified、本机仍为完整安装态的诚实中间态，CLI 退出码 7；同一份台账而现场已不在安装态 ⇒ 改判 ledger_inconsistent，抓的是『痕迹被台账之外的手段抹掉』；第三支如实沉默——台账那一笔本身就不是 install_verified 时，空现场配空台账不构成本案，判 ledger_unverified 且点名清单为空，绝不把从未成立过的记录指控成被抹掉（此支由评测台校准臂实抓，是第 9 枚修复自己引入的第 11 枚缺陷））| ledger_unverified | ledger_inconsistent；台账字节/sha 在响应里回显且可被独立读盘复核；调用方交 ?ledger_path= 不改判（唯一真相 = 服务自己的数据根）。现场读不动（构建台账坏掉、日志被安装进程占用）在引擎内降级成点名的不一致而不是异常——500 不是裁决。既有用户数据（安装前就存在的 %LOCALAPPDATA%\\Zaojing 内容）先登记基线、装完卸完逐字节读回比对，绝不拿真实安装根当测试沙箱；基线里**不含**执行器自己本轮要重写的证据件（台账 + 缺省安装日志，第 7 枚缺陷的修法把它们收进数据根，于是第二轮重写必然让旧指纹对不上——本机实测那枚『被改坏』的文件就是本轮刚写的 execution-evidence.json，而六份真用户数据逐字节相等），它们逐份另账回显在 preexisting.executor_owned（文件名 + 动手前指纹 + 此刻指纹 + 是否变了），逐出不等于藏起来；卸载只清理**本次安装创建**的落点（removed_scope=created_by_install_only），且回执里每一枚路径逐字回显台账自己记下的拼写（比对走 normcase+realpath 归一，回显走原文——把归一形当凭据会让同一件事在 install 面叫 D:\…、uninstall 面叫 d:\…，任何逐字交叉核对都会撞出一堵假墙；此枚由评测台第二轮用例实抓，引擎自测同轮补臂）。验收责任分工：引擎自测以真子进程 + 真临时根 + 假安装器覆盖门、差集派生、轮询与篡改反证；评测台以 installer_execution 黄金域 6 条 + 1 条校准（注入假 spawn 接缝）覆盖；本机一次真装真卸（真 setup.exe → 产品 EXE 起跑 → 自带卸载器 → 全树归零）由冻结 EXE 的 CLI 承担并在 L2/L5 留下运行时命中与台账证据。端点：GET /api/v1/installer/execution（零参数、零写入）、GET /api/v1/runtime/install-actions（契约与退役边界）；CLI：ZaojingAI.exe install-exec install|uninstall|plan|state --confirm ZAOJING-CONFIRM-EXECUTE-INSTALLER"},
    # —— 安装清单可核验性红线分裂（Phase 49：静态校验 + 只读安装态探测 supported；写侧执行
    #    当时仍 not_shipped，Phase 58 执行器交付后退役）——
    {"id": "installer_manifest_verification", "name": "安装清单静态校验 + 只读安装态探测", "status": "supported",
     "note": "Phase 49 真实交付（第 26 引擎 installer_manifest_engine，纯 stdlib、零落盘、模块即引擎）：把 build-installer.py 生成的 setup.iss 逐条与磁盘实况对账，十四条规则（R1~R14）全部只报**可复算的观测事实**并各带判据 basis 与行号——R1 未知/残留花括号常量（抓出单遍 str.format 漏替换留下的 {app_name}×14 与 {install_date}；旧模板从未被检查的真实原因是寻径只用 PATH 问 iscc——Phase 53 实测本机自 2026-07-19 起就装有 Inno Setup 6 且 setup.iss 已真编译，历轮那句『本机没有 iscc、安装版从未真正编译过』属仪器伪影而非环境事实）、R2 [Files] Source 相对 payload_root 真实存在性、R3 通配 Source 零命中（Inno 编译期即报错，除非显式 skipifsourcedoesntexist，故按实测 glob 命中数判定）、R4 卸载入口必须指向 {uninstallexe}（卸载器真实落位 {app}\\unins000.exe，硬写他路径必指不到文件）且 [Setup] 不得关闭 Uninstallable、R5 载荷 DestDir 与 DefaultDirName 解析后的安装根同处（{app} 为标准写法直接放行，其余按 os.path 规范路径判同处/包含）、R6 AppId 合法 GUID 形制（决定卸载键 {<AppId>}_is1 的稳定性，非十六进制字母会让按 GUID 解析的清点工具失配）、R7 [Dirs] 是否覆盖安装版真实数据根（%LOCALAPPDATA%\\Zaojing，先解析 Inno 常量再比对，避免把正确写法误判为无交集）、R8 [Registry] 值引用与 [Icons] Filename 指向的项是否确有对应 [Files] 条目、R9 只有 iscc 能定论的项（LicenseFile=/SetupIconFile= 空值语义、compiler:/{code|cm|param|emit|root|has} 展开）**只出 info 并回显 iscc_available**，绝不出'编译已通过'的结论、R10 Permissions 须为 Inno 的 <组>-<级别> 形态（只校验形态不逐词判定，词表最终定论权在编译器，据此可抓 usersfull 而不冤合法写法）、R11 per-user 区域与 PrivilegesRequired 的配对、R12 Architectures* 里的弃用架构标识、R13 [Setup] 缺 AppVersion/AppVerName——R11/R12/R13 三条不是推测，每条都对应本机真 iscc 的一次实测退出码或一行告警原文（R13 系 iscc 实测 exit=2 的编译级致命而非告警），判词以 compiler_proven / iscc_observed 字段回指该原话，且各带双向臂（缺指令必报、补齐必不报）。verdict 由 counts 真实派生（有 error→manifest_invalid，仅 warn→manifest_warning，否则 manifest_ok），counts.total 恒等于 findings 长度；输入拒绝为非文本/空文本/空 payload_root 抛 ManifestError→端点 400。只读安装态探测 install_state 逐项观察 HKCU/HKLM 卸载子键（RegOpenKeyEx+RegEnumKeyEx 只读枚举，含真实子键计数）、按 AppName 的 DisplayName 兜底、开始菜单组与 %LOCALAPPDATA% 数据根存在性、便携标记，installed **只由注册表卸载项真实命中派生**，目录存在只作辅助观察，未见即不声称已安装（本机实测 installed=False，且旧 AppId 换为合法 GUID 前已核 HKCU(37)/HKLM(201) 子键零匹配，不存在被孤立的既有安装）。反证已内置：桩掉注册表接缝使命中为真则 installed 必翻真、桩掉 glob 使通配命中非零则该条 finding 必消失、桩掉环境解析使数据根不可解析则 dirs 告警必回归。校验对象是构建期脚本：PyInstaller 从未把 setup.iss 打进包（包内无 dist/ 与随包 .md，打进去只会产出与真实构建无关的缺失），故冻结 EXE 上 GET /api/v1/installer/manifest/verify 如实 404 reason=installer_script_not_found，绝不伪造'已通过'。Phase 59 起本域黄金用例收为 installer_manifest 6 条（Phase 49 静态四条 + 静态开火臂 + R14 卸载撤销七臂），此前并记在同一域下的编译面三枚与预检面两枚各归 installer_artifact_build / installer_preflight 自有黄金域（见两能力各自 note），本域那 1 条校准（假期望把合规预建目录与运行时数据根无交集判成 error）留守不变。端点：GET /api/v1/installer/manifest/verify?script_path=…（可选，须 .iss 且落在允许根内，越界 403/非文本超大 400）、GET /api/v1/installer/state?app_id=…"},
    # —— 安装域可核验性红线分裂第二刀（Phase 53：编译面 + 预检面 supported，写侧执行当时
    #    仍 not_shipped，Phase 58 执行器交付后退役）——
    {"id": "installer_artifact_build", "name": "安装产物编译证据核验（iscc 真编译 → 证据与磁盘逐字段对账）", "status": "supported",
     "note": "Phase 53 真实交付（第 26 引擎 installer_manifest_engine 的编译面 verify_build_evidence，纯 stdlib、零落盘、**未新增引擎**——installer_manifest 仍只计一枚；当前引擎总数 28，Phase 80 起新增第 28 引擎 service_registry）：把 build-installer.py 落在 installer/build-evidence.json 的构建证据逐字段与磁盘实况**重算**对账。本轮根除的缺陷类正是把自报字段写成事实——证据里的 compiled / fabricated 两个布尔**不参与判定**，只在 evidence_self_report 里回显并标 ignored=True；判据 compile_succeeded = 退出码 0 ∧ 输出尾部真见过完成行 ∧ 产物在盘，三条独立信号同立（完成行优先在尾部原文里找，无痕才退回派生字段，并回显 compile_line_source ∈ tail/derived_field/absent）。verdict 优先级先窄后宽：缺字段→evidence_incomplete、产物不在盘→artifact_missing、三信号不同立→compile_not_proven、对账有不符→evidence_mismatch、否则 artifact_verified，blocking 逐条带 code/detail/basis。脚本与产物各比**字节数 + sha256 两路**（等长替换只有 sha 能发现），产物与证据彼此相符而脚本已被改→artifact_not_from_current_script（专抓拿旧产物冒充本轮编译）。告警按行前缀 Warning/Error/Deprecated 从尾部重算：仅当生产者如实记录了输出总长且尾部即全文时才要求相等，尾部为节选时只设下界 recorded>=tail_recount——把截断判成不一致是另一种假红。旧证据以 iscc_stderr_tail 命名而 iscc 把告警与完成行一并写在 stdout，故 OUTPUT_TAIL_KEYS 双名容错并回显 tail_key/schema/schema_match（字段名不如实会诱导读者以为 stderr 空即无告警）；iscc_output_chars / iscc_warnings / iscc_compile_line 属后加字段，缺席只标 field_absent 并降低该臂断言强度，绝不据此判 evidence_incomplete。本机实测（非估算）：三路寻径在固定目录命中 ISCC，setup.iss 已真编译多次，留档的一次探针编译为 setup.iss 4,771 字节 sha256=b7dc240f3b5ad1fc…、产物 installer\\zaojing-ai-1.0.0-setup.exe 60,089,058 字节 sha256=be6270bdffd43e07…、iscc_exit_code=0、完成行 Successful compile (10.406 sec)、告警 0 行、尾部 1600 字符且未记总长（走下界臂）。本判词刻意**不钉当前产物字节数**：本 note 随 agent_service.py 打进 ZaojingAI.exe，而该 exe 又是 setup.iss 的一条 [Files] 载荷 ⇒ 任何写死的产物字节都会在**下一次构建之后**落后于实况（与『ZIP 内的审计报告永远描述把它打进去的那次构建之前的状态』同属自指工件的固有次序）。当前实况一律以盘上 installer/build-evidence.json 与本端点的重算结果为准，判据是关系而非快照：iscc_exit_code==0 ∧ 输出尾部真见过完成行 ∧ 产物在盘 三信号同立、脚本与产物各比字节数+sha256、编译后静态校验与记录的 manifest_verdict 一致 ⇒ verdict=artifact_verified、blocking_codes=[]。反证内置于引擎自测：改一个字节必报 sha 不符、删产物必报 artifact_missing、把退出码桩成 2 必报 compile_not_proven。界限声明：产物在盘 ≠ 本机已安装——not_install.installed_claimed 恒 False、零子进程零写入，安装态只由 installer_manifest_verification 的只读注册表探测判；写侧执行自 Phase 58 起由 installer_execution 承担，但**本端点仍零子进程零写入**，既不安装也不卸载。PyInstaller 从未把 build-evidence.json 打进包（同 setup.iss，打进去只会得到与真实构建无关的缺失），故冻结 EXE 上该端点如实 404 reason=build_evidence_not_found，绝不伪造『已证成』。Phase 59 起本面自有 installer_artifact_build 黄金域 3 条（编译面正控、编译面四臂、告警与完备性）+ 1 条校准（case id calib-manifest-build-wrong-expect），此前这五枚用例并记在 installer_manifest 同一个黄金域下，Phase 59 起各归各账。端点：GET /api/v1/installer/artifact?evidence_path=…&script_path=…&artifact_path=…（三参皆可选；显式路径须落在允许根内否则 403，非 JSON/超大 400）"},
    {"id": "installer_preflight", "name": "安装预检（权限/安装根候选/目标占用/可写性/磁盘/载荷，只读仪器）", "status": "supported",
     "note": "Phase 53 真实交付（同引擎预检面 preflight_install，纯 stdlib、模块即引擎）：按脚本声明 + 本机实况判『若要安装，条件够不够』，**绝不执行安装**。三态原则：每项 result ∈ {True, False, None}，None 表示未探测并进 unknowns 而非 blocking——把『没探测』写成『不可写/无权限』正是本轮根除的假信号。verdict：有阻断项→preflight_blocked，否则有未探测项→preflight_unknown，否则 preflight_ready。七路实测：① 权限——Win32 令牌真取（OpenProcessToken + GetTokenInformation(20/18)，须显式声明 ctypes restype/argtypes，不声明时同一路以 err=24 ERROR_BAD_LENGTH 失败，属仪器伪影非环境事实；IsUserAnAdmin 只作旁证），PrivilegesRequired 缺省按 Inno 语义判 admin（本机 iscc 缺省告警原文即 set to admin），故 declared/effective/requires_elevation 三项分列；② 安装根——按 Inno auto 常量形制**成对给候选而非猜一个真值**（发货脚本写 PrivilegesRequired=lowest，管理员/用户两种装模式都合法）：{autopf} 展开为 commonpf 形 C:\\Program Files\\造境科技（env ProgramFiles 与 CSIDL 0x26 实测）与 userpf 形 %LOCALAPPDATA%\\Programs\\造境科技（FOLDERID_UserProgramFiles 本机实测 hr=0x80070002 不可解析，按文档回退并显式标 resolved_via=fallback），引用官方形制原文，未实测过的 auto 常量一律 unresolved 不外推；③ 目标冲突——目标路径若已是同名文件即 target_exists_as_file，目录内每个 exe 用 CreateFileW share=0 真句柄试探（本机实测空闲=(True,0)、被占=(False,32 ERROR_SHARING_VIOLATION)、不存在=(False,2) 且视为未被占用不阻断）；④ 可写性——**写探针纪律：只有调用方显式给出 target_override 时才真落盘**（必要时建目录→写→读回字节比对→删除→必要时 rmdir，探针文件用完即删并回显 probe_removed），默认绝不拿解析出的真实安装根当探针目标（预检是只读仪器），HTTP 面更把 target_override 关在允许根内，结构上无法指向 Program Files 或 %LOCALAPPDATA%；probe_writable=False→write_probe_unprobed，mkdir 失败与写入失败分开归因（前者 cannot_create_target、后者才是 target_not_writable）；⑤ 产物——复用编译面，未证成即 artifact_not_verified/artifact_missing 阻断（预检的前提是有可装的东西）；⑥ 磁盘——需求字节 = 实测产物字节 + 实测声明载荷字节（本机实测 60,089,058 + 58,784,220 = 118,873,278，对比 shutil.disk_usage 真实余量），**不发明安全系数**（本轮根除的正是把常数写进判据）；⑦ 载荷——直接复用静态校验 R2/R3 同一判据，不另起口径。本机实况：真实 setup.iss 不传 target_override 时 verdict=preflight_unknown、unknown_codes=[write_probe_unprobed]、blocking_codes=[]（权限实测 elevated=true 而脚本不要求提权、根候选两条存在=false 属全新安装形制、载荷 3/3 在盘、余量充足）；传临时目录为 target_override 时 verdict=preflight_ready、探针真写 23 字节并读回比对后删除、目录零残留。反证内置：桩掉令牌接缝→必落 elevation_state_unresolved、桩掉写探针返回 (False,32/183)→必出阻断码且 result=False、桩成 (False,2)→不得阻断、父路径为普通文件→如实归因 mkdir_failed 而不是抛崩（Windows 此处抛 FileExistsError WinError 183 而非 3/21/123，故按分类判定并把错误号当观察值记录）。界限：结论只描述『若执行安装则本机条件如何』，既不是安装结果也不是安装态（not_install.preflight_is_not_install / compile_is_not_install 恒 True）；Phase 53 起随附的那句「execution 恒 needs_installer_environment」在 Phase 58 随执行器交付而**退役**——退役 ≠ 改写，原文逐字留在 GET /api/v1/runtime/install-actions 返回的 retired_boundary.was（since=Phase 26 / retired_in=Phase 58），该面现为 status=executable_via_cli_only，而本引擎自家报告里的 execution 字段现为 status=not_executed_by_this_engine 且 performed=False：只读仪器仍然没装也没卸，只是理由从『做不到』换成『刻意不做』。变更只发生在『谁来装』，预检面自身的纪律一字未动——验收全程绝不以预检面安装、绝不以预检面卸载本机。Phase 59 起本面自有 installer_preflight 黄金域 2 条（预检面正控、预检面十臂）+ 1 条校准（case id calib-manifest-preflight-wrong-expect：假期望把调用方关闭写探针当成可写已证、preflight_unknown 判成 preflight_ready），此前两枚真值用例并记在 installer_manifest 同一个黄金域下，Phase 59 起各归各账。端点：GET /api/v1/installer/preflight?probe=true|false&target_override=…&evidence_path=…（默认 probe=false 即纯只读；override 越出允许根 403；发现不到 setup.iss 时 404）"},

    # —— V1.0 规划但本服务未实现 ——
    {"id": "nas_mount", "name": "NAS 挂载/客户端", "status": "supported",
     "note": "§81 真实 NAS 客户端机制(纯 stdlib)：filesystem 覆盖 映射盘/UNC/本地路径，Windows 经系统网络重定向器原生访问 SMB(正确工程分工，无需重造 SMB 二进制协议)；webdav 用 urllib PROPFIND/OPTIONS/GET + xml.etree 解析 multistatus。真实实现 带超时可达性探测 + 目录逐条目枚举(name/type/size/mtime) + msvcrt 独占区域锁文件锁检测 + 指数退避断线重连 + 本地清单缓存(TTL+sha256 键) + 文件下载校验 + 沙箱根路径穿越/符号链接防护(§99)。诚实边界：真实远端服务器/已映射盘属部署环境(同 DB 连接串/更新源)，未配置→not_configured、不可达→unreachable、需凭据→auth_required、WebDAV 无本地句柄锁→not_supported_for_webdav，绝不凭'配了地址'伪造已连接/枚举成功。Phase 37 起以独立 nas 黄金域纳入评测台：未配置/非法类型/空目标/不可达四态报错原文逐字 + 目录清单排序·类型·大小逐项真值 + 路径穿越与绝对路径双向拦截且越界零落盘 + 冷实例缓存命中四元组全等"},
    {"id": "packaging_panel_recognition", "name": "包装面板识别(栅格图→面板)", "status": "supported",
     "note": "Phase 47 真实交付（第 25 引擎 panel_recognition_engine，旧 needs_external 占位已整方法剔除）：输入位图刀版，Otsu 直方图类间方差最大化自算阈值二值化（阈值随响应回显，不写死）→ 按水平/垂直游程长度与垂直厚度双门限分离线与块状墨迹 → 共线短段按间隙合并、以实测沿线覆盖率判 cut(实线)/fold(虚线) → 线像素∪合并段支撑建屏障后取反做游程并查集连通域（触边者判背景排除），逐面板输出 bbox/面积/矩形度，共边求交得邻接、度序列与折叠连通分量，外轮廓裁切线长由占用格游程数精确算得。全程零 CV/ML 模型、零落盘，阈值/覆盖率/像素计数/面积/轮廓长全部可逐项复算；线型与角色标 line_basis=geometric-heuristic 只声称几何事实、不谎称语义鉴定；无墨→no_lines_detected、有段无线未闭合→no_panels_detected、面积不足入 rejected 并给中文原因，绝不臆造型；缺参 400/阻止 403/不存在 404/非位图 400。能力边界写入 caveats 而非沉默：覆盖轴对齐矩形刀版，斜角与曲线不在能力内，共线首尾相接的 cut+fold 会按合并后整体覆盖率判型（逐邻边 separating_line 才是该边线型权威口径）。Phase 47 起以独立 panel_recognition 黄金域纳入评测台：2x2 网格 4 面板 6141px/轮廓 1264/度序列 [2,2,2,2]、直插盒 9 面板 8 邻边折线分隔、断开双盒 2 组 0 邻边、空白图与开放虚线反零向量，全部逐数精确。与矢量刀版拓扑 packaging_panel_topology 严格区分（前者位图输入、后者结构化矩形输入）。端点：POST /api/v1/packaging/panel-recognition?image_path=…（可选 min_area/max_line_width/px_per_mm）"},
    {"id": "desktop_hub", "name": "桌面 Hub（本地原生窗口，真连本机服务）", "status": "supported",
     "note": "Phase 50 真实交付（第 27 引擎 desktop_hub_engine，纯标准库 tkinter + urllib，零第三方依赖、零构建步骤）。旧判词\"§5 前端 Hub 为独立桌面应用、不在本 FastAPI 服务交付范围\"**已整段剔除**——那条把能力误绑在 Tauri 工具链上的推理与 Phase 47 被纯几何反解推翻的\"需真实 CV\"同类：桌面原生窗口并不需要 Rust 前端框架，Python 标准库自带的 Tk 就是本机 GUI。本机实测：单文件 EXE 内 tcl/tk 随包解析（`_MEIPASS\\_tcl_data` / `_tk_data`）、真建可见窗口（hwnd 非零、`winfo_ismapped()==1`、几何 480x320 问即所得），tkinter 入包的一文件体积代价实测为 +3,064,949 字节（同法基线对比，非估算）。三条纪律：① **只搬运不编数**——hub_snapshot() 只做三条只读 GET（/api/v1/health、/api/v1/capabilities、/api/v1/eval/reports），窗口里每个数字都是该次响应的字段搬运，跨源还要 /health 与 /capabilities 两处自报引擎清单逐项相等才报 agree=True，读不到即字段置 None 并列入 unavailable_fields，绝不用 0 或上一次的数字占位；② **开窗只读、动作必点**——唯一的 POST 通道是 perform_action()，它只挂在按钮 command 上，验收判据取服务端 route_hits 增量：开窗后 /api/v1/eval/run 增量恒 0、invoke 后增量恰 1；③ **窗口显示 == 派生行**——render_rows() 是快照的纯函数（不参与 Tk），窗口每个 Label 的 cget(\"text\") 必须逐字等于它吐出的行，防\"GUI 另演一套常量\"。诚实降级三形制各有真值：端口关闭→三源 connection_failed 且异常原文照抄（WinError 10061，同时留 error_class 供跨 locale 判读）、端口被别的服务占着→not_zaojing_service（HTTP 404 或 content-type 非 JSON 都算，绝不把 HTML 当数值搬）、JSON 残缺→malformed_json；无显示会话不是失败条件，open_hub() 如实回 window.created=False / reason=no_display_available（或 headless_requested），数据层仍可为真。入口为同一可执行文件的 CLI 子命令 `hub`（--base-url / --window-probe / --json），默认无参数仍是原服务行为，服务端**绝不**因请求弹窗口。能力边界如实申报：Hub 只读这三条 GET 与一个显式评测 POST，不替代 §5 规划的完整 Hub 工作流，控制台 webui 与桌面 Hub 是同一服务的两个客户端。Phase 50 起以独立 desktop_hub 黄金域纳入评测台（评测自起 127.0.0.1:0 进程内真 HTTP 夹具驱动，routes/seen 逐实例隔离，零 POST 与恰一次 POST 均以真实请求计数落证）；GUI 本身不在评测台内（无显示会话会让评测脆断），其活体实证在 L2/L4：真建窗、逐 Label 读回文本、真 invoke 按钮并核对服务端计数仪增量。端点：本能力不新增任何 /api/v1 路由（客户端消费既有三条 GET 与 POST /api/v1/eval/run）"},
]


# ----------------------------------------------------------------------------
# Capability Registry 2.0（Final 2.0 §5）：§2 红线"supported 须由代码实现 + 测试
# 证据 + 动态状态共同证明"的机制化。每项能力的 engine/health_ready/status_2_0/
# test_count/passed_count/verified/last_verified_at 全部在响应时从 _MODULES 与
# 磁盘真实评估报告动态派生，清单本身不写死任何证据字段；无报告可核时诚实报
# verified=false / evidence_kind=no_eval_report，引擎或报告缺位时派生 degraded/blocked。
# ----------------------------------------------------------------------------

# 能力 → (引擎注册名, 评测黄金域)。引擎名必须是 _MODULES/_ENGINE_NAMES 中的真实
# 注册名（与 /health 同源）；None 表示该能力为服务侧实现、无独立引擎可核。
# 黄金域对应评估报告 results[].category；"_report" 为评估中心自证据保留域
# （报告本身即其执行产物，不可能与任何用例 category 撞名）。
_CAPABILITY_ENGINE_GOLDEN: Dict[str, tuple] = {
    "license": ("license", "license"),
    "config_persistence": ("config", "config"),
    "asset_engine": ("asset", "asset"),
    "print_qa": ("print_qa", "print"),
    "workflow_engine": ("workflow", "workflow"),
    "model_router": ("model_router", "model_routing"),
    "ai_provider_integration": ("provider", "provider"),
    "security": ("security", "security"),
    "recovery_snapshot": ("recovery", "recovery"),
    "image_remove_background": ("bg_removal", "bg_removal"),
    "knowledge_rag": ("knowledge", "knowledge"),
    "tool_registry": ("service_registry", "tool_registry"),
    "skill_runtime": ("service_registry", "skill_runtime"),
    "feature_flags": ("service_registry", "feature_flags"),
    "design_compare": ("compare", "compare"),
    # Phase 81（轨道 A 服务侧最后一刀）：delivery_package / websocket_events 由
    # (None, None) 翻转为专属引擎 + 专属黄金域——磁盘工作逐字下沉 delivery_engine
    # （第 30 引擎）、事件流逐字下沉 event_engine（第 29 引擎），agent_service 端点
    # 委托引擎实例；评测台真驱动引擎（私有沙箱、期望值手算），verified 由磁盘最新
    # 报告如实派生。服务侧 no_golden_cases_yet 由 Phase 80 的两枚收缩为 0 枚。
    "delivery_package": ("delivery", "delivery_package"),
    "websocket_events": ("event", "websocket_events"),
    "typography_engine": ("typography", "typography"),
    "design_review": ("review", "review"),
    "customer_revision": ("revision", "revision"),
    "color_engine": ("color", "design"),
    "layout_engine": ("layout", "design"),
    "packaging_dieline_vdp": ("packaging", "production"),
    "packaging_barcode": ("packaging", "barcode"),
    "packaging_panel_topology": ("packaging", "topology"),
    "auto_update": ("update", "update"),
    # Phase 79（轨道 A 第一刀）：三枚 Adobe 桥能力补上黄金用例域。三枚用例在
    # evaluation_engine 内真实驱动 handshake→dispatch→poll→submit_result→reconcile
    # 协议往返（对协议合规 mock 宿主，非真实 Adobe/CAD 文档改写），故 Registry 2.0
    # 如实派生 golden_cases / verified=True；诚实边界保持：verified 只代表协议机制
    # 往返可证，真实文档读写仍需宿主（ps/ai 写与 adobe_capability_report 仍 None）。
    "adobe_uxp_bridge": ("adobe_bridge", "bridge_handshake"),
    # Phase 77：CorelDRAW 只读三工具复用 adobe_bridge 引擎（同一个 BRIDGEABLE_TOOLS 门控表），
    # Phase 79 起挂 bridge_cdr_read 黄金域（读回 + reconcile 逐字段真值）。
    "cdr_document_read": ("adobe_bridge", "bridge_cdr_read"),
    # Phase 78：最小写集（create/move）复用同一门控表；Phase 79 起挂 bridge_cdr_write
    # 黄金域（确认门前置拒 + 只读工具 capability_not_negotiated 拒 + 写往返 verified）。
    "cdr_document_write": ("adobe_bridge", "bridge_cdr_write"),
    "ps_document_write": ("adobe_bridge", None),
    "ai_document_write": ("adobe_bridge", None),
    "adobe_capability_report": (None, None),
    "evaluation_center": ("evaluation", "_report"),
    "brand_color_analysis": ("brand", "brand"),
    # Phase 56：语义识别经真实 Provider 视觉透传落地，引擎仍归 brand（recognize_semantics 在 brand 引擎内），
    # 黄金域为评测台自起进程内 mock 视觉 HTTP 的 brand_semantics。
    "brand_semantic_recognition": ("brand", "brand_semantics"),
    # Phase 59 路线 B：第 22 引擎（installer_runtime_engine）的守护面升为独立黄金域
    # runtime_guard（5 条真值 + 1 条校准，期望逐字段来自只读探针 p59_det_probe.py v2 的
    # 三轮归一实测）；此前该能力在 Registry 2.0 里恒为 evidence_kind=no_golden_cases_yet
    # ——引擎进程内已加载、真实可观测，但无黄金用例可核，故登记即令 verified 由磁盘报告派生。
    "installer_runtime_guard": ("runtime", "runtime_guard"),
    # Phase 58：执行器本体交付在 runtime 引擎（installer_runtime_engine）里，评测台以
    # installer_execution 黄金域 6 真值 + 1 校准覆盖；登记此项即令 Registry 2.0 的
    # verified 由磁盘最新报告实时派生，而不是清单里手写证据字段。
    "installer_execution": ("runtime", "installer_execution"),
    "installer_manifest_verification": ("installer_manifest", "installer_manifest"),
    # Phase 53 两刀分裂出的编译面与预检面：同属第 26 引擎 installer_manifest。
    # 就地更正（Phase 59）：此处原写"golden=None ⇒ Registry 2.0 如实报
    # evidence_kind=no_eval_report"，那是**看错了分支**——报告在场而 domain 为 None 时
    # 走的是 no_golden_cases_yet（3499 行），no_eval_report 只在磁盘无报告时出现。判据
    # 本身（三件编译面 + 两件预检面用例）自 Phase 53 起就活在评测台里，缺的只是把它们
    # 挂到本能力名下的**域名**；Phase 59 路线 B 正是把域名拆出来（判据原文一字未改），
    # 于是这两枚不再需要"借别的域凑证据"，也不必继续报无黄金用例。
    "installer_artifact_build": ("installer_manifest", "installer_artifact_build"),
    "installer_preflight": ("installer_manifest", "installer_preflight"),
    "nas_mount": ("nas", "nas"),
    "packaging_panel_recognition": ("panel_recognition", "panel_recognition"),
    "desktop_hub": ("desktop_hub", "desktop_hub"),
}


def _read_latest_eval_evidence() -> Dict[str, Any]:
    """从磁盘最新评估报告聚合逐域真实执行计数。聚合直接重算 results[]，
    不经由 evaluation 引擎运行时状态——报告文件本身是唯一证据源。"""
    out: Dict[str, Any] = {"report": None, "by_domain": {}, "corrupted_files": 0,
                           "report_files": 0, "error": None}
    rdir = DATA_ROOT / "evaluation" / "reports"
    if not rdir.is_dir():
        out["error"] = "eval_report_dir_missing"
        return out
    files = sorted(rdir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    out["report_files"] = len(files)
    for p in files:
        try:
            r = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            out["corrupted_files"] += 1
            continue
        if not isinstance(r, dict) or not isinstance(r.get("results"), list):
            out["corrupted_files"] += 1
            continue
        out["report"] = r
        for res in r["results"]:
            if res.get("must_fail"):
                continue
            d = str(res.get("category"))
            st = out["by_domain"].setdefault(d, {"total": 0, "passed": 0})
            st["total"] += 1
            if res.get("passed"):
                st["passed"] += 1
        return out
    if files:
        out["error"] = "all_reports_unparseable"
    return out


def _build_capability_registry() -> Dict[str, Any]:
    ev = _read_latest_eval_evidence()
    rep = ev["report"]
    now_iso = datetime.now().isoformat(timespec="seconds")
    caps: List[Dict[str, Any]] = []
    for c in _CAPABILITY_MANIFEST:
        engine, domain = _CAPABILITY_ENGINE_GOLDEN.get(c["id"], (None, None))
        supported = c["status"] == "supported"
        health_ready = (engine in _MODULES) if engine else None
        if supported and engine and health_ready is False:
            status_2_0 = "degraded"
        else:
            status_2_0 = ("supported" if supported
                          else "needs_host" if c["status"] == "needs_adobe_host"
                          else "not_shipped")
        note = None
        if status_2_0 == "degraded":
            note = f"引擎 {engine} 未加载: {_IMPORT_ERRORS.get(engine, '未记录导入错误')}"
        entry = {
            "id": c["id"], "name": c["name"], "status": c["status"],
            "status_2_0": status_2_0, "engine": engine,
            "golden_domain": domain, "health_ready": health_ready,
            "implemented": supported, "test_count": None, "passed_count": None,
            "failed_count": None, "last_verified_at": None, "verified": False,
            "evidence_kind": None, "evidence_note": note,
        }
        if not supported:
            entry["evidence_kind"] = "honest_boundary"
            caps.append(entry)
            continue
        if rep is None:
            entry["evidence_kind"] = "no_eval_report"
            entry["evidence_note"] = (entry["evidence_note"] or
                                      "磁盘无有效评估报告，真值未经测试证据核验（不虚报 verified）")
            caps.append(entry)
            continue
        entry["last_verified_at"] = rep.get("finished_at")
        if rep.get("calibration_error") or rep.get("honest") is not True:
            entry["evidence_kind"] = "report_calibration_error"
            entry["evidence_note"] = "最新报告判分校准失真（honest=false），其计数不可作真值证据"
            caps.append(entry)
            continue
        if domain is None:
            entry["evidence_kind"] = "no_golden_cases_yet"
            entry["evidence_note"] = (entry["evidence_note"] or
                                      ("引擎进程内已加载，但尚无该能力的黄金用例真值"
                                       if engine else "服务侧能力，尚未纳入黄金用例评测"))
            caps.append(entry)
            continue
        if domain == "_report":
            # 评估中心自证据：其交付物就是这份逐用例判分并落盘的报告本身；
            # 计数按全部 scored 用例真实聚合（非任何写死数字）
            scored = [r for r in rep["results"] if not r.get("must_fail")]
            p = sum(1 for r in scored if r.get("passed"))
            entry["evidence_kind"] = "golden_cases"
            entry["test_count"] = len(scored)
            entry["passed_count"] = p
            entry["failed_count"] = len(scored) - p
            entry["verified"] = (len(scored) > 0 and p == len(scored))
            caps.append(entry)
            continue
        st = None if domain == "_report" else ev["by_domain"].get(domain)
        if domain != "_report" and st is None:
            entry["evidence_kind"] = "report_missing_domain"
            entry["evidence_note"] = f"最新报告中无 category={domain} 的用例，域覆盖漂移须追查"
            caps.append(entry)
            continue
        entry["evidence_kind"] = "golden_cases"
        entry["test_count"] = st["total"]
        entry["passed_count"] = st["passed"]
        entry["failed_count"] = st["total"] - st["passed"]
        entry["verified"] = (st["total"] > 0 and st["passed"] == st["total"]
                             and status_2_0 == "supported")
        caps.append(entry)
    return {
        "schema_version": "capability-registry/2.0",
        "generated_at": now_iso,
        "evaluation_evidence": {
            "report_id": (rep or {}).get("report_id"),
            "report_finished_at": (rep or {}).get("finished_at"),
            "report_files_on_disk": ev["report_files"],
            "corrupted_report_files": ev["corrupted_files"],
            "source": str(DATA_ROOT / "evaluation" / "reports"),
            "error": ev["error"],
        },
        "capabilities": caps,
    }


@app.get("/api/v1/capabilities", tags=["能力真值表"])
async def capabilities():
    """如实披露各子系统实现状态。绝不把占位/未实现能力谎报为已支持（§2 红线 / §99.3）。
    Registry 2.0：逐项证据字段由 _MODULES 与磁盘最新评估报告实时派生。"""
    by = {}
    for c in _CAPABILITY_MANIFEST:
        by[c["status"]] = by.get(c["status"], 0) + 1
    registry = await asyncio.to_thread(_build_capability_registry)
    return {
        "version": APP_VERSION,
        "policy": "no_fabrication: 无法可靠实现的能力明确标注为 needs_adobe_host / prototype_not_shipped / not_shipped，不伪造成功",
        "summary": {
            "total": len(_CAPABILITY_MANIFEST),
            "supported": by.get("supported", 0),
            "needs_adobe_host": by.get("needs_adobe_host", 0),
            "prototype_not_shipped": by.get("prototype_not_shipped", 0),
            "not_shipped": by.get("not_shipped", 0),
        },
        "engines_loaded": sorted(_MODULES.keys()),
        "engines_missing": _IMPORT_ERRORS,
        "registry": registry,
        "capabilities": _CAPABILITY_MANIFEST,
    }


# ----------------------------------------------------------------------------
# 能力评估中心（Phase 24 · 真实黄金用例评测 + 负控校准 + 报告落盘/对比）
# ----------------------------------------------------------------------------

class EvalCompareRequest(BaseModel):
    a: str
    b: str


@app.post("/api/v1/eval/run", tags=["评估中心"])
async def eval_run():
    """运行全量真实评测：逐用例真实调用被测引擎并按手工可核期望判分，报告落盘后返回。"""
    rep = get_evaluation_engine().run_full()
    s = rep.get("summary") or {}
    await _emit("eval.completed", {
        "report_id": rep.get("report_id"),
        "executed_cases": s.get("executed_cases", rep.get("total")),
        "passed": s.get("passed", rep.get("passed")),
        "failed": s.get("failed", rep.get("failed")),
        "honest": rep.get("honest"),
        "calibration_error": rep.get("calibration_error"),
        "note": "逐用例真实调用被测引擎；honest=False 表示校准用例失真，全卷 verified 作废",
    })
    return rep


@app.get("/api/v1/eval/suite", tags=["评估中心"])
async def eval_suite():
    """评测用例清单（含负控校准标注与如实 not_evaluated 边界），不执行。"""
    return get_evaluation_engine().suite_manifest()


@app.get("/api/v1/eval/reports", tags=["评估中心"])
async def eval_reports():
    """列出已落盘的真实评测报告摘要（数据根 evaluation/reports/*.json）。"""
    return {"reports": get_evaluation_engine().list_reports()}


@app.get("/api/v1/eval/reports/{report_id}", tags=["评估中心"])
async def eval_report(report_id: str):
    mod = _require("evaluation")
    try:
        return get_evaluation_engine().get_report(report_id)
    except mod.EvaluationError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.post("/api/v1/eval/compare", tags=["评估中心"])
async def eval_compare(req: EvalCompareRequest):
    mod = _require("evaluation")
    try:
        return get_evaluation_engine().compare(req.a, req.b)
    except mod.EvaluationError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.get("/api/v1/eval/recommendations", tags=["评估中心"])
async def eval_recommendations(report_id: Optional[str] = None):
    """基于真实报告指标生成建议（ASCII 前缀，GBK 控制台安全）；缺省取最新一份。"""
    eng = get_evaluation_engine()
    try:
        if report_id:
            rep = eng.get_report(report_id)
        else:
            lst = eng.list_reports()
            if not lst:
                raise HTTPException(status_code=404, detail="尚无任何评测报告，请先 POST /api/v1/eval/run")
            rep = eng.get_report(lst[0]["report_id"])
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"report_id": rep["report_id"], "honest": rep.get("honest"),
            "recommendations": eng.recommendations(rep)}


# ----------------------------------------------------------------------------
# 品牌智能（Phase 25 · 真实像素调色板/CIEDE2000 一致性/alpha 包围盒几何；语义如实边界）
# ----------------------------------------------------------------------------

class BrandPaletteRequest(BaseModel):
    image_path: str
    k: Any = 6


class BrandConsistencyRequest(BaseModel):
    brand_hexes: List[Any]
    image_path: str
    max_delta_e: Any = 3.0
    k: Any = 8


class BrandLogoGeometryRequest(BaseModel):
    image_path: str
    alpha_threshold: Any = 8


class BrandSemanticInsightRequest(BaseModel):
    image_path: str
    provider_id: Optional[str] = None
    prompt: Optional[str] = None


@app.post("/api/v1/brand/palette", tags=["品牌智能"])
async def brand_palette(req: BrandPaletteRequest):
    """真实像素中位切分取调色板（复用色彩引擎，逐像素占比）。"""
    path = validate_file_path(req.image_path)
    try:
        return get_brand_engine().analyze_palette(str(path), k=req.k)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/v1/brand/color-consistency", tags=["品牌智能"])
async def brand_color_consistency(req: BrandConsistencyRequest):
    """品牌色 vs 图像真实调色板的 CIEDE2000 最近邻一致性判定，coverage 真实派生。"""
    path = validate_file_path(req.image_path)
    try:
        return get_brand_engine().check_brand_color_consistency(
            req.brand_hexes, str(path), max_delta_e=req.max_delta_e, k=req.k)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/v1/brand/logo-geometry", tags=["品牌智能"])
async def brand_logo_geometry(req: BrandLogoGeometryRequest):
    """真实 alpha 通道逐像素包围盒：内容框/四边留白/宽高比/填充率。"""
    path = validate_file_path(req.image_path)
    try:
        return get_brand_engine().logo_geometry(str(path), alpha_threshold=req.alpha_threshold)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/v1/brand/semantics", tags=["品牌智能"])
async def brand_semantics():
    """语义类品牌能力的如实边界声明（needs_external，绝不返回臆造调性/置信度）。
    Phase 56 起响应另带 provider_configured（从注入的 Provider 引擎实时读得），指示真实透传面是否就绪。"""
    return get_brand_engine().semantic_boundaries()


@app.post("/api/v1/brand/semantic-insight", tags=["品牌智能"])
async def brand_semantic_insight(req: BrandSemanticInsightRequest):
    """真实品牌语义识别（Phase 56）：读取 Logo 图像字节 → 构造 OpenAI 兼容多模态 data URL →
    委派已配置的真实 Provider 视觉模型 → **逐字透传**其结构化输出。本端点绝不自行合成调性关键词/置信度：
    未注入或未配置 Provider→诚实 needs_external(502)；模型输出非 JSON→provider_output_unparseable 保留 raw；
    Provider 传输失败→原样透传其状态。§2 红线：拒绝凭空伪造 AI 语义。"""
    path = validate_file_path(req.image_path)   # 403 禁入 / 404 缺件（P55 共用路径门）
    if not path.is_file():
        raise HTTPException(status_code=400, detail=f"{UNREADABLE_PATH_DETAIL}: {req.image_path}")
    eng = get_brand_engine()
    # 真实 HTTP 往返属阻塞 IO → 下沉线程池（P52 事件循环纪律）。
    r = await asyncio.to_thread(eng.recognize_semantics, str(path), req.provider_id, req.prompt)
    status = r.get("status")
    if status in ("needs_external", "no_provider_configured"):
        # 未配置 Provider：诚实降级，映射 502 且保留 fabricated=false（绝不伪成功）。
        raise _provider_502({"status": "needs_external",
                             "error": r.get("reason"),
                             "detail": "未配置真实 AI Provider，语义识别须经真实视觉模型，拒绝凭空伪造（POST /api/v1/provider/configure 接入）"})
    if status in ("unreachable", "timeout", "auth_failed", "provider_error", "response_invalid",
                  "circuit_open", "error", "key_unavailable_on_platform", "key_unreadable"):
        raise _provider_502({"status": status, "error": r.get("provider_status") or status,
                             "detail": r.get("reason")})
    return r   # ok（逐字透传模型真实输出）/ provider_output_unparseable（保留 raw，不硬编 schema）


# ----------------------------------------------------------------------------
# 安装/运行时守护（Phase 26 · 第 22 引擎：真实探针/磁盘/端口/单实例/诊断派生；
# 安装执行域自 Phase 58 起有真执行器，但只经 CLI 显式同意令牌触发；HTTP 面到此为止，
# 既不安装也不卸载，更不伪造"已安装/已卸载"）
# ----------------------------------------------------------------------------

class RuntimePortCheckRequest(BaseModel):
    port: Any
    host: Any = "127.0.0.1"


class RuntimeInstanceRequest(BaseModel):
    name: Any
    action: Any = "acquire"  # acquire | release


@app.get("/api/v1/runtime/diagnostic", tags=["运行时守护"])
async def runtime_diagnostic(service_port: Any = None):
    """真实运行时诊断：数据根写入探针 + 磁盘余量 + Python 版本（可选端口 bind 探测），
    状态与计数由逐项真实检查派生。service_port 传本机已监听端口时该项必如实 fail。"""
    try:
        return get_runtime_guard_engine().run_diagnostic(service_port=service_port)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/v1/runtime/port-check", tags=["运行时守护"])
async def runtime_port_check(req: RuntimePortCheckRequest):
    try:
        return get_runtime_guard_engine().check_port(req.port, req.host)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/v1/runtime/single-instance", tags=["运行时守护"])
async def runtime_single_instance(req: RuntimeInstanceRequest):
    eng = get_runtime_guard_engine()
    try:
        if req.action == "acquire":
            return eng.acquire_single_instance(req.name)
        if req.action == "release":
            return eng.release_single_instance(req.name)
        raise HTTPException(400, f"action 需为 acquire/release，收到 {req.action!r}")
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/v1/runtime/install-actions", tags=["运行时守护"])
async def runtime_install_actions():
    """安装/卸载执行域（静默安装/注册表/快捷方式/卸载器）的**契约面**：回显全部门禁（门数只活在载荷 gates 里，由引擎现场派生，文案不写死数字）、
    显式同意令牌、判定口径与十项观察名单，全部由引擎现场派生，不是静态承诺。
    本端点零子进程、零写入——真正的执行入口只有同一可执行文件的 CLI 子命令
    install-exec。返回里的 retired_boundary.was 逐字保留了 Phase 26~57 那句
    needs_installer_environment（退役 ≠ 改写）。"""
    return get_runtime_guard_engine().install_actions()


# ----------------------------------------------------------------------------
# 安装清单静态校验 + 只读安装态探测（Phase 49 · 第 26 引擎 installer_manifest）
# 两者都只做**只读**事：读脚本字节→内存解析→报 findings；读注册表卸载项/目录存在性→
# 报观察值。写注册表、跑安装器、生成卸载器属 Phase 58 交付的 installer_execution
# **执行器**，只经 CLI 显式同意令牌触发；本节两条端点与其互不越界（HTTP 面结构上到不了执行器）。
# ----------------------------------------------------------------------------

def _script_declared_identity():
    """从可发现的 setup.iss 里取 AppId/AppName（取不到就如实返回空，不猜）。"""
    script = resolve_installer_script()
    if script is None:
        return {"script_path": None, "app_id": "", "app_name": ""}
    try:
        mod = get_installer_manifest_engine()
        sections = mod.parse_script(script.read_bytes().decode("utf-8"))
        sm = mod.setup_map(sections.get("Setup", []))
        return {"script_path": str(script),
                "app_id": sm.get("appid", ("", 0))[0],
                "app_name": sm.get("appname", ("", 0))[0]}
    except Exception as e:  # 读不动就是读不动，如实带原因，不降级成空字符串冒充干净
        return {"script_path": str(script), "app_id": "", "app_name": "",
                "error": f"{type(e).__name__}: {e}"}


@app.get("/api/v1/installer/manifest/verify", tags=["安装清单"])
async def installer_manifest_verify(script_path: Optional[str] = None):
    """安装清单静态校验（§47 打包可核验性 · Phase 49 真实交付，第 26 引擎）：
    只读读取 build-installer.py 生成的 setup.iss（或允许根内显式指定的 .iss），
    逐条比对**脚本声明 × 磁盘实况**：[Files] 每条 Source 在脚本同目录下是否真有载荷、
    通配 Source 是否零命中（零命中会让 iscc 编译期直接报错）、Python 模板占位符是否
    残留未替换（{app_name} 这类单遍 str.format 漏替换的产物）、DestDir 是否落在
    DefaultDirName 解析出的安装根、卸载入口是否指向 {uninstallexe} 的真实位置、
    AppId 是否合法 GUID 形制、[Dirs] 是否覆盖安装版真实数据根、Permissions 是否是
    Inno 的 <组>-<级别> 形态、注册表值与编译器专属判断能否离线定论。
    返回 verdict ∈ {manifest_ok, manifest_warning, manifest_invalid} 与逐项 findings
    （含行号与判据 basis），counts 与 findings 长度恒等；全程零落盘、零写入。
    发现不到脚本→404 reason=installer_script_not_found（绝不伪造"已通过"）；
    路径被安全策略阻止或越出允许根→403；非 .iss/超大/非 UTF-8/无 Section→400。"""
    mod = get_installer_manifest_engine()
    path, text = await asyncio.to_thread(_read_manifest_script, script_path)
    data_root = installed_data_root()
    try:
        result = mod.verify_script(text, str(path.parent), str(data_root))
    except mod.ManifestError as e:
        raise HTTPException(status_code=400, detail={
            "reason": "manifest_rejected", "path": str(path), "error": str(e),
            "fabricated": False})
    stat = path.stat()
    result["script_path"] = str(path)
    result["script_bytes"] = stat.st_size
    result["script_sha256"] = await asyncio.to_thread(_sha256_text, text)
    # 判分时用的数据根与"当前进程的数据根"可以不同（便携版/测试沙箱下 DATA_ROOT 在别处），
    # 这里把口径写进响应而不是留给读者猜。
    result["data_root_basis"] = (
        "校验 [Dirs] 时对照的是安装版数据根（%LOCALAPPDATA%\\" + INSTALLED_DATA_DIR_NAME +
        "，由 installed_data_root() 派生）；当前进程 DATA_ROOT=" + str(DATA_ROOT) +
        "，便携/沙箱形态下另在他处，属如实差异而非缺陷")
    result["service_data_root"] = str(DATA_ROOT)
    result["app_name_matches_service_default"] = (result.get("app_name")
                                                 == DEFAULT_INSTALLER_APP_NAME)
    result["iscc_available"] = bool(mod.iscc_available())
    return result


@app.get("/api/v1/installer/state", tags=["安装清单"])
async def installer_state(app_id: Optional[str] = None):
    """只读安装态探测（Phase 49）：查 HKCU/HKLM 卸载子键里是否存在 Inno 自动登记的
    {<AppId>}_is1、按 AppName 的 DisplayName 兜底命中、开始菜单组与 %LOCALAPPDATA%
    应用数据根是否存在、便携标记是否存在。installed **只由注册表卸载项真实命中派生**，
    目录存在只作辅助观察，绝不凭"文件在"就说"已安装"。全程 RegOpenKeyEx 只读、
    零写入、零卸载动作；执行域自 Phase 58 起由同一可执行文件的 CLI 子命令 install-exec
    承担（须逐字交出显式同意令牌），本端点与之互不越界，绝不代它声称装了什么。
    app_id 优先级：显式参数 > 可发现的 setup.iss 里的 AppId > 空（空则只走 DisplayName
    兜底，并在 app_id_source 里写明 none）。"""
    mod = get_installer_manifest_engine()
    declared = await asyncio.to_thread(_script_declared_identity)
    explicit = (app_id or "").strip()
    eff_app_id = explicit or declared.get("app_id", "")
    eff_app_name = declared.get("app_name") or DEFAULT_INSTALLER_APP_NAME
    try:
        result = mod.install_state(eff_app_id, eff_app_name, [str(app_base_dir())])
    except mod.ManifestError as e:
        raise HTTPException(status_code=400, detail={
            "reason": "state_probe_rejected", "error": str(e), "fabricated": False})
    result["app_id_source"] = "query_param" if explicit else (
        "setup_iss" if declared.get("app_id") else "none")
    result["app_name_source"] = "setup_iss" if declared.get("app_name") else "service_default"
    result["script_path"] = declared.get("script_path")
    if declared.get("error"):
        result["script_read_error"] = declared["error"]
    result["app_name_matches_service_default"] = eff_app_name == DEFAULT_INSTALLER_APP_NAME
    result["probes_seen"] = len(result.get("probes", []))
    return result


# ----------------------------------------------------------------------------
# 安装域可核验性第二刀（Phase 53 · 同引擎的编译面与预检面）
#   GET /api/v1/installer/artifact   —— 构建证据 vs 磁盘实况逐字段对账（产物是否被证成）
#   GET /api/v1/installer/preflight  —— 若要安装，本机条件够不够（只读仪器）
# 两者都零子进程、零注册表写入、零安装/卸载动作；写探针只在调用方显式交出沙箱目标时才跑，
# 且该目标被允许根关住——真实安装根（Program Files\… / %LOCALAPPDATA%\…）不在根内，
# 因此"HTTP 面无法让写探针落在真实安装根"是可断言的结构事实，不是承诺。
# ----------------------------------------------------------------------------

_MAX_EVIDENCE_BYTES = 256 * 1024
_TRUE_WORDS = ("true", "1", "yes", "on")
_FALSE_WORDS = ("false", "0", "no", "off")


def _under_allowed_roots(raw: str, suffixes: tuple, kind: str) -> Path:
    """把调用方显式交出的路径关进允许根：存在性→后缀→普通文件→规范化落在根内。
    与 _read_manifest_script 同一口径，使"只核验本产品的构建物"成为可断言的事实。"""
    path = validate_file_path(str(raw)).resolve()
    if suffixes and path.suffix.lower() not in suffixes:
        raise HTTPException(status_code=400, detail={
            "reason": kind + "_suffix_invalid", "path": str(path), "suffix": path.suffix,
            "expected": list(suffixes), "fabricated": False})
    if not path.is_file():
        raise HTTPException(status_code=400, detail={
            "reason": kind + "_not_a_file", "path": str(path), "fabricated": False})
    if not _within_manifest_roots(path):
        raise HTTPException(status_code=403, detail={
            "reason": kind + "_path_outside_allowed_roots", "path": str(path),
            "allowed_roots": [str(r) for r in _manifest_allowed_roots()],
            "fabricated": False})
    return path


def _evidence_search_dirs() -> List[Path]:
    """build-evidence.json 的搜索次序（与 resolve_build_evidence 同一份清单，
    404 里回显 looked_in 必须等于真实查过的路径，不能另编一套。"""
    cands = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        cands.append(Path(meipass) / "installer" / "build-evidence.json")
        cands.append(Path(meipass) / "build-evidence.json")
    base = app_base_dir()
    cands.append(base / "installer" / "build-evidence.json")
    cands.append(base / "build-evidence.json")
    return cands


def resolve_build_evidence(explicit: Optional[str] = None):
    """构建证据 build-evidence.json 的定位（Phase 53 编译面）。

    次序与 resolve_installer_script 同一口径：_MEIPASS > EXE/脚本同目录，各试
    installer/ 子目录与根目录。**返回 None 是冻结 EXE 上的常态而非缺陷**：PyInstaller
    从未把构建证据打进包（打进去只会产出一堆与真实构建无关的缺失），故该端点如实 404，
    绝不凭空造一份证据来"核验通过"。显式参数一律过 _under_allowed_roots 四道门。
    """
    if explicit and str(explicit).strip():
        path = _under_allowed_roots(str(explicit).strip(), (".json",), "evidence")
        if path.stat().st_size > _MAX_EVIDENCE_BYTES:
            raise HTTPException(status_code=400, detail={
                "reason": "evidence_too_large", "path": str(path),
                "limit": _MAX_EVIDENCE_BYTES, "fabricated": False})
        return path
    for c in _evidence_search_dirs():
        if c.is_file():
            return c
    return None


def _preflight_probe_target(raw: Optional[str]):
    """写探针目标的门：允许根之外一律 403。真实安装根正因此在结构上不可达。

    路径不存在不预先拒——引擎要按"建不出目录"(cannot_create_target) 与"建得出但写不进"
    (target_not_writable) 分开归因，吞掉这种形态就等于替引擎编结论。
    """
    if not raw or not str(raw).strip():
        return None
    p = Path(str(raw).strip())
    try:
        rp = p.resolve()
    except OSError as e:
        raise HTTPException(status_code=400, detail={
            "reason": "preflight_override_unresolved", "path": str(p), "error": str(e),
            "fabricated": False})
    if not _within_manifest_roots(rp):
        raise HTTPException(status_code=403, detail={
            "reason": "preflight_override_outside_allowed_roots", "path": str(rp),
            "allowed_roots": [str(r) for r in _manifest_allowed_roots()],
            "note": "真实安装根（Program Files / %LOCALAPPDATA%）不作为 HTTP 面探针目标",
            "fabricated": False})
    return rp


@app.get("/api/v1/installer/artifact", tags=["安装清单"])
async def installer_artifact_evidence(evidence_path: Optional[str] = None,
                                      script_path: Optional[str] = None,
                                      artifact_path: Optional[str] = None):
    """安装产物编译证据核验（Phase 53 · 同引擎编译面，只读）：把 build-installer.py 落的
    installer/build-evidence.json 逐字段与磁盘实况**重算**对账——脚本与产物各比字节数 +
    sha256、iscc 退出码与输出尾部的 "Successful compile" 行、告警行按前缀重算、证据的
    manifest_verdict 与当前静态校验重跑结果是否一致。证据自报的 compiled/fabricated 布尔
    不参与判定（只在 evidence_self_report 里回显并标 ignored=True）：本轮根除的正是把
    自报字段与探针伪影写成环境事实。verdict ∈ {artifact_verified, evidence_mismatch,
    artifact_missing, compile_not_proven, evidence_incomplete}，blocking 逐条带判据 basis。
    产物在盘只证成"由构建链产出"，**不等于本机已安装**（not_install.installed_claimed 恒
    False，安装态由 /installer/state 判）。发现不到证据→404 reason=build_evidence_not_found
    （冻结 EXE 上属如实常态，绝不伪造"已证成"）；显式路径越出允许根→403；非 JSON/非文本/
    超大→400。60 MB 级产物哈希与目录观察全部走工作线程，不占事件循环（§L6 反阻塞口径）。"""
    mod = get_installer_manifest_engine()
    ev = await asyncio.to_thread(resolve_build_evidence, evidence_path)
    if ev is None:
        raise HTTPException(status_code=404, detail={
            "reason": "build_evidence_not_found",
            "looked_in": [str(p) for p in _evidence_search_dirs()],
            "note": "build-evidence.json 由 build-installer.py 写在构建工作区的 installer/ 目录，"
                    "PyInstaller 未随包打包；本端点如实报未找到，不伪造核验通过",
            "fabricated": False})
    sp = (await asyncio.to_thread(_under_allowed_roots, str(script_path), (".iss",), "script")
          if script_path and str(script_path).strip() else None)
    ap = (await asyncio.to_thread(_under_allowed_roots, str(artifact_path), (".exe",), "artifact")
          if artifact_path and str(artifact_path).strip() else None)
    try:
        result = await asyncio.to_thread(mod.verify_build_evidence, str(ev),
                                         str(sp) if sp else None,
                                         str(ap) if ap else None,
                                         str(installed_data_root()))
    except mod.ManifestError as e:
        raise HTTPException(status_code=400, detail={
            "reason": "artifact_evidence_rejected", "path": str(ev), "error": str(e),
            "fabricated": False})
    result["evidence_requested"] = str(ev)
    result["overrides"] = {"script_path": str(sp) if sp else None,
                           "artifact_path": str(ap) if ap else None}
    result["service_data_root"] = str(DATA_ROOT)
    result["installed_data_root"] = str(installed_data_root())
    return result


@app.get("/api/v1/installer/preflight", tags=["安装清单"])
async def installer_preflight_check(probe: str = "false",
                                    target_override: Optional[str] = None,
                                    script_path: Optional[str] = None,
                                    evidence_path: Optional[str] = None):
    """安装预检（Phase 53 · 同引擎预检面，只读仪器，**绝不执行安装**）：按 setup.iss 声明 +
    本机实况判"若要安装，条件够不够"。七路实测各带判据：权限（Win32 令牌真取 + PrivilegesRequired
    有效值/缺省语义）、安装根（{autopf} 按 Inno auto 常量形制成对给候选，不猜单一真值，
    判定口径回显 judged_on）、目标冲突（同名文件占位 + 目录内 exe 用 share=0 真句柄试探占用）、
    可写性（写探针，见下）、产物（复用编译面，未证成即阻断——预检的前提是有可装的东西）、
    磁盘（需求字节 = 实测产物 + 实测载荷，不发明安全系数）、载荷（与静态校验 R2/R3 同一判据）。
    verdict ∈ {preflight_ready, preflight_blocked, preflight_unknown}；每项 result ∈
    {true,false,null}，null 表示**未探测**，进 unknowns 而不是 blocking（"没探测"写成"不可写"
    正是本轮根除的假信号）。
    写探针纪律：默认 probe=false 即纯只读；只有显式 probe=true **且** 交出落在允许根内的
    target_override 才真落盘（写→读回字节比对→删，探针文件用完即删），真实安装根因在允许根
    之外而结构上不可达（越界 403）。结论只描述"若执行安装则本机条件如何"，既不是安装结果
    也不是安装态（not_install / execution 恒实）。发现不到脚本→404；证据不在→如实
    blocking_code=artifact_missing 并回显 evidence_provided=false，绝不补齐成放行。"""
    mod = get_installer_manifest_engine()
    word = str(probe or "false").strip().lower()
    if word not in _TRUE_WORDS + _FALSE_WORDS:
        raise HTTPException(status_code=400, detail={
            "reason": "preflight_probe_param_invalid", "probe": probe,
            "accepted": list(_TRUE_WORDS) + list(_FALSE_WORDS), "fabricated": False})
    probe_wanted = word in _TRUE_WORDS
    override = await asyncio.to_thread(_preflight_probe_target, target_override)
    path, text = await asyncio.to_thread(_read_manifest_script, script_path)
    ev = await asyncio.to_thread(resolve_build_evidence, evidence_path)
    try:
        result = await asyncio.to_thread(
            mod.preflight_install, text, str(path.parent), str(installed_data_root()),
            (str(ev) if ev else None), bool(probe_wanted and override),
            str(override) if override else "")
    except mod.ManifestError as e:
        raise HTTPException(status_code=400, detail={
            "reason": "preflight_rejected", "path": str(path), "error": str(e),
            "fabricated": False})
    result["script_path"] = str(path)
    result["payload_root"] = str(path.parent)
    result["evidence_provided"] = bool(ev)
    result["evidence_path"] = str(ev) if ev else None
    result["service_data_root"] = str(DATA_ROOT)
    result["installed_data_root"] = str(installed_data_root())
    result["probe_request"] = {
        "asked": probe_wanted, "override": str(override) if override else None,
        "ran": bool(probe_wanted and override),
        "basis": "probe=true 且允许根内有 target_override 才真写探针；其余形制一律不落盘，"
                 "并以 write_probe_unprobed 如实进 unknowns",
    }
    return result


# ----------------------------------------------------------------------------
# 安装执行面（Phase 58 · 真值表第 6 次、也是本家族最后一次漂移）
#   GET /api/v1/installer/execution —— **只读对账**。执行器本体只在 CLI 上（install-exec），
#   HTTP 面一个字节都不写、一个子进程都不起：它只把 CLI 留下的台账拿**现场磁盘/注册表**
#   逐字段重算比对，比不中就点名到字段。于是沙箱化验收（数据根在 temp）恒得 ledger_absent
#   —— 这是"本机从未按这条链跑过"的如实结论，不是断言失败。P53 的结构性纪律照旧：
#   安装域在 openapi 里六条 HTTP 路由全为 GET，HTTP 面在结构上够不着执行器。
#   Phase 60 就地改核（原句逐字保留于上，此处只补点名）：裸计数"6 条"不写域名会被读成
#   "/api/v1/installer/ 前缀有 6 条"，而按该前缀实数是 **5 条**——manifest/verify、state、
#   artifact、preflight、execution；第 6 条属 runtime 域的 GET /api/v1/runtime/install-actions
#   （安装执行的契约面）。"六条恒全 GET、HTTP 走不到执行器"这一结论不变，变的只是六条的
#   归属划分。本轮 L3 的 I09 判据正是被这个歧义按前缀派生当场判红的，故改为逐条点名、
#   判据侧同步改为由 /openapi.json 现场派生路径集合与验收台本段真驱动过的路径集合互证。
# ----------------------------------------------------------------------------

EXECUTION_LEDGER_RELPATH = ("installer", "execution-evidence.json")


def _execution_ledger_path() -> Path:
    """执行台账的唯一位置：本服务**自己的** DATA_ROOT 下。

    它不是端点入参——能被调用方指到别处就不叫对账而叫投毒。真实运行时（安装版）
    落在 %LOCALAPPDATA%\\Zaojing\\installer\\，与 CLI 默认写的路径同一条。
    """
    return Path(DATA_ROOT).joinpath(*EXECUTION_LEDGER_RELPATH)


def _execution_registry_keys() -> Dict[str, Any]:
    """两枚被对账键名 + 安装根尾名的派生（与 /installer/state 同一条链：可发现的 setup.iss）。

    - uninstall_key：AppId 经 uninstall_registry_key() 得 {GUID}_is1
    - app_reg_key：[Registry] 段里 Root: HKCU 的那枚 Subkey
    - vendor_dir：[Setup] DefaultDirName 的尾段（{autopf}\\造境科技 → 造境科技）——执行器
      缺省安装根据此拼出，避免"发货脚本改了发行方目录名而服务里的镜像常量没跟着改"
    找不到脚本 / 取不到 AppId 时**如实留空并回显 source**，绝不猜——猜出来的键名会让
    "键已消失"这类结论落在压根不存在的键上，而那正是本轮要根除的假信号。
    """
    declared = _script_declared_identity()
    mod = get_installer_manifest_engine()
    out = {"source": "script_not_found", "app_id": declared.get("app_id", ""),
           "app_name": declared.get("app_name", ""), "uninstall_key": "", "app_reg_key": "",
           "vendor_dir": "", "vendor_dir_source": "mirrored_constant"}
    if out["app_id"]:
        out["uninstall_key"] = mod.uninstall_registry_key(out["app_id"])
    script = resolve_installer_script()
    if script is not None:
        out["source"] = "setup_iss" if out["uninstall_key"] else "setup_iss_without_appid"
        try:
            sections = mod.parse_script(script.read_bytes().decode("utf-8"))
            for _ln, stmt in sections.get("Registry", []):
                params = mod.split_params(stmt)
                if str(params.get("root", "")).strip().upper() == "HKCU" and params.get("subkey"):
                    out["app_reg_key"] = str(params["subkey"])
                    break
            sm = mod.setup_map(sections.get("Setup", []))
            ddn = str(sm.get("defaultdirname", ("", 0))[0]).strip()
            tail = ddn.replace("/", "\\").rstrip("\\").split("\\")[-1] if ddn else ""
            if tail and not mod.TOKEN_RE.fullmatch(tail):
                out["vendor_dir"] = tail
                out["vendor_dir_source"] = "setup_iss_defaultdirname"
        except Exception as e:  # 读不动就带原因，不降级成"键不存在"
            out["script_read_error"] = f"{type(e).__name__}: {e}"
    return out


@app.get("/api/v1/installer/execution", tags=["安装清单"])
async def installer_execution_state():
    """安装/卸载执行台账的只读对账（Phase 58）：CLI 执行器（install-exec）跑完真装真卸后
    会把每一笔写进 DATA_ROOT 下的 execution-evidence.json；本端点把那本台账拿**现场**
    重算逐字段比——制品 sha 三方相等（台账 × 构建台账 × 盘上现读）、日志仍在盘上且 sha 不变、
    安装根/卸载项/应用键的存在性与台账"之后"的说法是否吻合、既有数据是否逐字节完好。
    台账自报的 installed/performed/status 这类布尔一律回显但不作判定依据（见返回里的
    self_report_ignored 与 recorded_only）：判定 basis 恒为 derived_from_disk_and_registry。
    形制：ledger_absent（本机从未跑过，不伪造回执）| executed_verified |
    install_verified_awaiting_uninstall（台账只到"装成了"这一笔、本机现场仍是完整安装态——
    这是诚实的中间态，既不算核验完成也不算对不上账；一旦现场不再吻合而台账仍无卸载记录，
    那是安装痕迹被台账之外的手段抹掉，改判 ledger_inconsistent 并逐字段点名）|
    ledger_unverified（自报成功而现场不认）| ledger_inconsistent（现场与台账矛盾，逐条点名）。
    既有用户数据的基线里**不含**执行器自己重写的证据件（台账与安装日志），它们逐份另账回显在
    preexisting.executor_owned 里（只读对账不重写任何东西，故该栏 this_run_rewrites_it=False）；
    旧格式台账把那几枚记成了用户数据时，逐出条件按落点路径兜住并在回显里标
    recorded_as_user_data_in_older_ledger=True。
    全程零写入、零子进程、零注册表写；现场读不动（构建台账坏掉 / 日志被占用）在引擎内
    降级成点名的不一致而不是异常——一次 500 不是裁决。"""
    mod = _require("runtime")
    keys = await asyncio.to_thread(_execution_registry_keys)
    led = _execution_ledger_path()
    result = await asyncio.to_thread(
        mod.execution_state, ledger_path=str(led),
        uninstall_key=keys["uninstall_key"], app_reg_key=keys["app_reg_key"])
    result["service_data_root"] = str(DATA_ROOT)
    result["registry_keys"] = keys
    return result


# ----------------------------------------------------------------------------
# 知识库 / RAG (Knowledge Engine — §65/§66 · 真实文档抽取 + 分块 + 向量检索)
# ----------------------------------------------------------------------------

@app.post("/api/v1/knowledge/ingest", tags=["知识库"])
async def knowledge_ingest(req: KnowledgeIngestRequest):
    """摄入本地文档（txt/md/csv/html/docx/pdf）→ 抽取文本 + 分块 + 词元向量索引。"""
    validate_file_path(req.file_path)  # 安全黑名单 + 存在性校验
    if req.scope not in ("global", "workspace", "project", "brand", "private"):
        raise HTTPException(status_code=400, detail="非法 scope")
    eng = get_knowledge_engine()
    # ingest_file = 读文档 + 分块 + 逐块向量 + 落盘（同步 I/O 与 CPU），整块交给工作线程
    item = await asyncio.to_thread(
        eng.ingest_file,
        req.file_path, title=req.title, scope=req.scope, tags=req.tags,
        project_id=req.project_id, brand=req.brand, workspace_id=req.workspace_id,
    )
    await _emit("knowledge.ingested", {"knowledge_id": item.id, "status": item.status,
                                       "chunks": len(item.chunks)})
    return item.to_public_dict()


@app.post("/api/v1/knowledge/retrieve", tags=["知识库"])
async def knowledge_retrieve(req: KnowledgeRetrieveRequest):
    """向量余弦检索，返回排序命中的知识分块（受作用域/上下文隔离约束）。
    Phase 51：随响应自带检索口径（模型名/维度/是否语义），如实标注稀疏哈希投影天花板。"""
    eng = get_knowledge_engine()
    results = eng.retrieve(req.query, top_k=req.top_k,
                           allowed_scopes=req.allowed_scopes, context=req.context)
    dims = None
    kmod = _MODULES.get("knowledge")
    if kmod is not None and hasattr(kmod, "embed_text"):
        try:
            dims = len(kmod.embed_text(req.query))
        except Exception:  # 量不到就不报数，绝不编造维度
            dims = None
    return {"query": req.query, "total": len(results), "results": results,
            "retrieval": _retrieval_descriptor(dims)}


@app.get("/api/v1/knowledge", tags=["知识库"])
async def knowledge_list(allowed_scopes: Optional[str] = None):
    eng = get_knowledge_engine()
    scopes = allowed_scopes.split(",") if allowed_scopes else None
    items = eng.list_items(scopes)
    return {"total": len(items), "items": items}


@app.get("/api/v1/knowledge/{item_id}", tags=["知识库"])
async def knowledge_get(item_id: str):
    eng = get_knowledge_engine()
    item = eng.get_item(item_id)
    if not item:
        raise HTTPException(status_code=404, detail=f"知识条目不存在: {item_id}")
    return item.to_public_dict()


@app.delete("/api/v1/knowledge/{item_id}", tags=["知识库"])
async def knowledge_delete(item_id: str):
    eng = get_knowledge_engine()
    ok = eng.delete_item(item_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"知识条目不存在: {item_id}")
    return {"deleted": True, "id": item_id}


# ----------------------------------------------------------------------------
# 工具注册表 + 能力协商 (Tool Registry — §23 工具定义 / §25 Capability Map)
# ----------------------------------------------------------------------------

# 工具契约 / 功能开关 / 技能校验的默认常量与实现，Phase 80 起逐字下沉为第 28 引擎
# service_registry_engine（单一真相源）。本服务不再各写一份，一律经 _service_reg() 委托，
# 评测黄金域驱动的本引擎实例即服务实际跑的那份代码，杜绝"平行副本"造成的自报失真。

_SERVICE_SINGLETON: Dict[str, Any] = {}


def _service_reg():
    """惰性构造绑定 DATA_ROOT 的 ServiceRegistryEngine 单例（进程内唯一实例）。"""
    if "engine" not in _SERVICE_SINGLETON:
        eng = _require("service_registry")
        _SERVICE_SINGLETON["engine"] = eng.ServiceRegistryEngine(DATA_ROOT)
    return _SERVICE_SINGLETON["engine"]


def _read_json(path: Path, default):
    if path.is_file():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            logger.warning(f"损坏的持久化文件，回退默认: {path}")
    return default


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def get_tool_registry() -> Dict[str, Any]:
    return _service_reg().get_tool_registry()


def _save_tool_registry():
    _service_reg().save_tool_registry()


def get_capability_map() -> Dict[str, Any]:
    """宿主插件启动时上报的能力矩阵（ps/ai 版本 + 支持的工具）。"""
    return _service_reg().get_capability_map()


@app.get("/api/v1/tools", tags=["工具注册表"])
async def tools_list():
    """列出全部已注册工具及其风险等级 / 宿主依赖 / 当前可执行性。
    runnable_now 由第 28 引擎 derive_tool_view 派生（无宿主上报的 ps.*/ai.* 如实为 False）。"""
    reg = await asyncio.to_thread(get_tool_registry)
    cmap = await asyncio.to_thread(get_capability_map)
    out = await asyncio.to_thread(_service_reg().derive_tool_view, reg, cmap)
    return {"total": len(out), "capability_reported": bool(cmap.get("hosts")), "tools": out}


@app.post("/api/v1/tools", tags=["工具注册表"])
async def tools_register(req: ToolRegisterRequest):
    if not req.name:
        raise HTTPException(status_code=400, detail="工具 name 不能为空")
    if req.risk_level not in ("L0", "L1", "L2", "L3", "L4"):
        raise HTTPException(status_code=400, detail="risk_level 必须为 L0-L4")
    reg = await asyncio.to_thread(get_tool_registry)
    reg[req.name] = {"description": req.description, "risk_level": req.risk_level,
                     "requires_adobe_host": req.requires_adobe_host}
    await asyncio.to_thread(_save_tool_registry)
    return {"registered": req.name, "total": len(reg)}


@app.post("/api/v1/tools/capability-report", tags=["工具注册表"])
async def tools_capability_report(req: CapabilityReportRequest):
    """Adobe 插件上报宿主能力（§25）。真实记录 host/version/capabilities，不伪造。"""
    cmap = await asyncio.to_thread(get_capability_map)
    hosts = cmap.setdefault("hosts", {})
    hosts[req.host] = {"version": req.version, "capabilities": req.capabilities,
                       "reported_at": datetime.now().isoformat()}
    cmap["updated_at"] = datetime.now().isoformat()
    await asyncio.to_thread(_write_json, DATA_ROOT / "tools" / "capability_map.json", cmap)
    return {"host": req.host, "hosts_reported": len(hosts),
            "supported_count": sum(1 for v in req.capabilities.values() if v)}


@app.get("/api/v1/tools/capability-map", tags=["工具注册表"])
async def tools_capability_map():
    return await asyncio.to_thread(get_capability_map) or {"hosts": {}, "note": "尚无宿主上报能力矩阵；ps.*/ai.* 工具暂不可执行"}


# ----------------------------------------------------------------------------
# 技能运行时 (Skill Runtime — §27/§28 · 清单加载 + 权限/工具校验)
# 实现下沉第 28 引擎；本处为委托包装，端点契约不变。
# ----------------------------------------------------------------------------

def _validate_skill(manifest: Dict[str, Any]) -> List[str]:
    return _service_reg().validate_skill(manifest)


def _skills_dir() -> Path:
    return _service_reg().skills_dir()


def _load_skill_manifests() -> Dict[str, Any]:
    return _service_reg().load_skill_manifests()


@app.get("/api/v1/skills", tags=["技能运行时"])
async def skills_list():
    skills = await asyncio.to_thread(_load_skill_manifests)
    return {"total": len(skills),
            "skills": [{"id": sid, "version": s.get("version"), "risk": s.get("risk"),
                        "permissions": s.get("permissions", []), "tools": s.get("tools", [])}
                       for sid, s in skills.items()]}


@app.post("/api/v1/skills", tags=["技能运行时"])
async def skills_register(req: SkillManifestRequest):
    """登记技能清单：先做权限/工具静态校验，校验不过则拒绝落盘（最小权限原则 §28）。"""
    manifest = req.model_dump()
    errors = await asyncio.to_thread(_validate_skill, manifest)
    if errors:
        raise HTTPException(status_code=400, detail={"validation_errors": errors})
    sdir = _skills_dir() / req.id
    sdir.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(_write_json, sdir / "manifest.json", manifest)
    return {"registered": req.id, "path": str(sdir / "manifest.json")}


@app.get("/api/v1/skills/{skill_id}", tags=["技能运行时"])
async def skills_get(skill_id: str):
    skills = await asyncio.to_thread(_load_skill_manifests)
    if skill_id not in skills:
        raise HTTPException(status_code=404, detail=f"技能不存在: {skill_id}")
    return {**skills[skill_id], "validation_errors": await asyncio.to_thread(_validate_skill, skills[skill_id])}


@app.post("/api/v1/skills/{skill_id}/validate", tags=["技能运行时"])
async def skills_validate(skill_id: str):
    skills = await asyncio.to_thread(_load_skill_manifests)
    if skill_id not in skills:
        raise HTTPException(status_code=404, detail=f"技能不存在: {skill_id}")
    errors = await asyncio.to_thread(_validate_skill, skills[skill_id])
    return {"skill_id": skill_id, "valid": not errors, "validation_errors": errors}


# ----------------------------------------------------------------------------
# 功能开关 (Feature Flags — §78 · JSON 持久化)
# Phase 80：种子默认 / setdefault 合并 / 落盘逐字下沉第 28 引擎，本处为委托包装。
# ----------------------------------------------------------------------------

def get_flags() -> Dict[str, bool]:
    return _service_reg().get_flags()


@app.get("/api/v1/flags", tags=["功能开关"])
async def flags_list():
    return await asyncio.to_thread(get_flags)


@app.get("/api/v1/flags/{key}", tags=["功能开关"])
async def flags_get(key: str):
    flags = await asyncio.to_thread(get_flags)
    if key not in flags:
        raise HTTPException(status_code=404, detail=f"开关不存在: {key}")
    return {"key": key, "value": flags[key]}


@app.put("/api/v1/flags/{key}", tags=["功能开关"])
async def flags_set(key: str, req: FeatureFlagRequest):
    val = await asyncio.to_thread(_service_reg().set_flag, key, req.value)
    return {"key": key, "value": val}


# ----------------------------------------------------------------------------
# 设计版本对比 (Design Compare — §50 · 真实对象列表 Diff + 六类结构化差异)
# Phase 38：diff 算法与类别映射逐字下沉 design_compare 引擎（第 23 引擎，
# 纯函数模块即引擎），本端点为薄包装；既有 L2/L4 契约断言作回归安全网。
# ----------------------------------------------------------------------------

@app.post("/api/v1/design/compare", tags=["设计对比"])
async def design_compare(req: DesignCompareRequest):
    """对 before/after 对象列表做真实差异分析：新增/删除/改动/移动，
    并按 §50 归类为 Geometry / Text / Color / Asset / Layer / Visual 六类结构化 diff。"""
    eng = _require("compare")
    return eng.compare_objects(req.before, req.after, req.compare_keys)


# ----------------------------------------------------------------------------
# 一键交付包 (Delivery Package — §69 · 真实文件归档 + SHA256 清单)
# ----------------------------------------------------------------------------

def _delivery() -> "_delivery_mod.DeliveryEngine":
    """交付磁盘工作的进程级单例（绑 DATA_ROOT），Phase 81 起本服务的
    delivery_package 端点整体委托它——评测台以 delivery_package 黄金域真驱动同份代码。"""
    eng = _require("delivery")
    return eng.DeliveryEngine(Path(str(DATA_ROOT)))


@app.post("/api/v1/delivery/package", tags=["一键交付"])
async def delivery_package(req: DeliveryPackageRequest):
    """把已存在的项目文件按 Source/Preview/Report/Print 分类归档，
    生成真实目录树 + SHA256 校验清单 + README + ZIP。

    Phase 55 起，逐文件可达性以三种责任不同的形制落账：not_found（文件不在了）、
    blocked_by_policy（安全引擎禁入）、not_a_file（目录、空串等非普通文件）。
    修前只有一枚 missing_or_blocked 把前两种压成同一个词，而目录/空串两类会让
    整个请求在 copy2 上抛 PermissionError 崩成 500（p55_delivery_500.json）。
    收录侧同样逐文件记账：数据根之外的真实文件仍然收录（不拦），但必须
    在 included_files[].inside_data_root 与 outside_root_count 里看得见。

    Phase 81 起磁盘工作（copy2 / sha256 / manifest 原子落盘 / README / ZIP / 三形制
    台账）逐字委托 delivery_engine.DeliveryEngine.build；本端点只保留路径合法性判定
    （依赖 security_engine 的 validate_file_path，经 classify_input_path 分形制）
    与事件广播（依赖 event_engine 的 emit）。单一真相源，无平行副本。"""
    if not req.files:
        raise HTTPException(status_code=400, detail="files 不能为空（至少提供一类交付文件）")

    pkg_id = "deliv_" + uuid.uuid4().hex[:12]

    # 路径校验留在循环上（纯逻辑 + 极轻 stat）；一旦验完就把整批磁盘工作交给工作线程，
    # 于是 async 体内不再有任何 copy/hash/write/zip。
    categories: List[str] = list(req.files.keys())
    plan: Dict[str, List[Path]] = {c: [] for c in categories}
    skipped: List[Dict[str, Any]] = []
    for category, paths in req.files.items():
        for fp in paths:
            src, reason, detail = classify_input_path(fp)
            if reason is not None:
                skipped.append({"category": category, "path": fp,
                                "reason": reason, "detail": detail})
            else:
                plan[category].append(src)

    manifest, zip_path = await asyncio.to_thread(
        _delivery().build, pkg_id, req.project, req.notes or "",
        categories, plan, skipped)

    # 台账先按三种形制各自计数（没发生的形制也要以 0 出现，缺席与为 0 是两回事），
    # 再与响应里的同一份账互相对表——由 L2/L5 拿 ZIP namelist 做第三方核对。
    _tally = _delivery().tally_skipped(manifest)
    await _emit("delivery.created", {
        "package_id": pkg_id,
        "files": manifest["included"],
        "outside_root_count": manifest["outside_root_count"],
        **_tally,
    })
    return {
        "package_id": pkg_id,
        "directory": str(_delivery().package_dir(pkg_id)),
        "archive": str(zip_path),
        "archive_size_bytes": zip_path.stat().st_size,
        "included": manifest["included"],
        "included_files": manifest["included_files"],
        "outside_root_count": manifest["outside_root_count"],
        "skipped": manifest["skipped"],
        "skipped_by_reason": manifest["skipped_by_reason"],
        "data_root": str(DATA_ROOT),
        "sha256_manifest": {c: [e["sha256"] for e in es] for c, es in manifest["categories"].items()},
    }


# ----------------------------------------------------------------------------
# WebSocket 实时事件流 (task.* — §7/§10)
# ----------------------------------------------------------------------------

@app.websocket("/ws")
async def ws_events(websocket: WebSocket):
    """真实任务事件流：连接后补发最近历史，随后推送实时事件；接收 ping 保活。

    Phase 81 起 EventHub 类逐字下沉为 event_engine（transport 无关），
    故 ws.accept() 由本端点显式发起，connect/disconnect 只负责簿记。"""
    await websocket.accept()
    await _EVENT_HUB.connect(websocket)
    try:
        await websocket.send_json({"event": "hello",
                                   "data": {"history": len(_EVENT_HUB.history),
                                            "clients": _EVENT_HUB.client_count}})
        for payload in list(_EVENT_HUB.history)[-50:]:
            await websocket.send_json({"event": "history." + payload["event"], "data": payload})
        while True:
            msg = await websocket.receive_text()
            if msg == "ping":
                await websocket.send_json({"event": "pong", "data": {"clients": _EVENT_HUB.client_count}})
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.warning(f"ws error: {e}")
    finally:
        await _EVENT_HUB.disconnect(websocket)


@app.get("/api/v1/events/recent", tags=["实时事件"])
async def events_recent(limit: int = 50):
    """拉取最近事件（供无法建立 WebSocket 的客户端轮询）。
    Phase 51：附带运行时事件台账（种类计数/累计发布量/缓冲容量），使"事件覆盖面"
    可由观测面实测，而不是靠文档声称；history 是环形缓冲，被截断时 published_total
    与 kind_counts 仍然如实累计。Phase 81 起逐字段由 event_engine.EventHub.recent 派生
    （单一真相源；端点不再各写一份字典）。"""
    return _EVENT_HUB.recent(limit=limit)


# ----------------------------------------------------------------------------
# 字体排版引擎 (Typography — §43/§111 · fontTools 真实扫描/度量/缺失替换)
# ----------------------------------------------------------------------------

@app.post("/api/v1/typography/scan", tags=["字体排版"])
async def typography_scan(limit: Optional[int] = None):
    """遍历系统字体目录真实重建字体索引（fontTools 解析）。缺 fontTools 诚实 503。"""
    eng = get_typography_engine()
    try:
        res = eng.rescan(limit=limit)
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    await _emit("fonts.scanned", {
        "scanned": res.get("scanned"), "error_count": len(res.get("errors") or []),
        "total_indexed": res.get("total_indexed"),
        "dirs_scanned": len(res.get("dirs") or []),
        "limit": limit,
        "note": "全量扫描为真实 I/O + fontTools 解析（全新数据根约 36-40s）",
    })
    return res


@app.get("/api/v1/typography/fonts", tags=["字体排版"])
async def typography_fonts(family: Optional[str] = None, cjk_only: bool = False):
    eng = get_typography_engine()
    items = eng.list_fonts(family=family, cjk_only=cjk_only)
    return {"total": len(items), "fonts": items}


@app.get("/api/v1/typography/fonts/{ref}", tags=["字体排版"])
async def typography_font(ref: str):
    eng = get_typography_engine()
    rec = eng.get_font(ref)
    if not rec:
        raise HTTPException(status_code=404, detail=f"未找到字体: {ref}")
    from dataclasses import asdict as _asdict
    return _asdict(rec)


@app.post("/api/v1/typography/metrics", tags=["字体排版"])
async def typography_metrics(req: FontMetricsRequest):
    """按字形 hmtx 真实累加排版宽度/行高，缺字如实计数（不假装能排）。"""
    eng = get_typography_engine()
    try:
        return eng.measure_text(req.font, req.text, req.size_pt)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))


@app.post("/api/v1/typography/match", tags=["字体排版"])
async def typography_match(req: FontMatchRequest):
    """为缺失字体挑已安装候选，覆盖度是硬门槛——绝不返回会出豆腐块的字体。"""
    eng = get_typography_engine()
    cands = eng.match_font(req.missing, weight=req.weight, text=req.text,
                           require_cjk=req.require_cjk, top_k=req.top_k)
    return {"missing": req.missing, "total": len(cands), "candidates": cands}


@app.get("/api/v1/typography/fallback-rules", tags=["字体排版"])
async def typography_fallback_rules():
    eng = get_typography_engine()
    rules = eng._load_fallbacks()
    return {"total": len(rules), "rules": rules}


@app.post("/api/v1/typography/fallback-rules", tags=["字体排版"])
async def typography_add_fallback(req: FallbackRuleRequest):
    eng = get_typography_engine()
    return eng.add_fallback(req.from_font, req.to_fonts, weight=req.weight)


@app.post("/api/v1/typography/resolve", tags=["字体排版"])
async def typography_resolve(req: ResolveFontRequest):
    """缺失字体处理（§111.3）：先查替换规则再真实匹配，无规则退回启发式匹配。"""
    eng = get_typography_engine()
    return eng.resolve_missing(req.missing, text=req.text)


@app.get("/api/v1/typography/alias-map", tags=["字体排版"])
async def typography_alias_map():
    """§111.4 跨平台字体名对照（内置真实映射表）。"""
    eng = get_typography_engine()
    return {"alias_map": eng.alias_map()}


# ----------------------------------------------------------------------------
# 多角色设计评审引擎 (Design Review — §49 · Visual/Brand/Production/Commercial)
# ----------------------------------------------------------------------------

@app.post("/api/v1/review/run", tags=["设计评审"])
async def design_review_run(req: DesignReviewRequest):
    """对设计对象列表做真实规则评审：WCAG 对比度、最小字号、出血、安全边距、
    印刷 DPI、品牌主色一致性、文字重叠。可复算，非 AI 打分伪装。"""
    eng = get_review_engine()
    result = eng.review(req.objects, canvas=req.canvas, brand=req.brand, roles=req.roles)
    await _emit("review.completed", {"score": result["score"],
                                     "issues": result["summary"]["total_issues"]})
    return result


# ----------------------------------------------------------------------------
# 客户改稿引擎 (Customer Revision — §48/§114 · 语义→操作→定位→冲突→应用)
# ----------------------------------------------------------------------------

@app.post("/api/v1/revision/parse", tags=["客户改稿"])
async def revision_parse(req: RevisionParseRequest):
    """把客户自然语言改稿意见解析为 ChangeRequest 列表：命中唯一→proposed，
    多候选→needs_clarification，受保护对象→conflict，无法识别→unparsed（绝不编造）。"""
    eng = get_revision_engine()
    return eng.parse(req.text, req.objects, protected=req.protected)


@app.post("/api/v1/revision/apply", tags=["客户改稿"])
async def revision_apply(req: RevisionApplyRequest):
    """把用户批量审核后勾选（only_ids）的 proposed 改动真实施加到对象副本：
    缩放/改字色/改字号/调亮度/旋转/删除，回传逐条结果与前后 diff。"""
    eng = get_revision_engine()
    result = eng.apply(req.objects, req.changes, only_ids=req.only_ids)
    await _emit("revision.applied", {"applied": result["applied"]})
    return result


# ----------------------------------------------------------------------------
# 色彩引擎 (Color — §44 · 调色板/和谐/对比/CMYK/Lab-ΔE，Pantone 诚实拒绝伪造)
# ----------------------------------------------------------------------------

@app.post("/api/v1/color/palette", tags=["色彩"])
async def color_palette(req: ColorPaletteRequest):
    """从真实图像文件量化取色（Pillow 中位切分 + 逐像素占比）。缺 Pillow→503，文件不存在→404。"""
    eng = get_color_engine()
    path = validate_file_path(req.path)
    try:
        return eng.palette_from_image(str(path), k=req.k)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))


@app.post("/api/v1/color/harmony", tags=["色彩"])
async def color_harmony(req: ColorHarmonyRequest):
    eng = get_color_engine()
    try:
        return eng.harmony(req.base, req.scheme)
    except KeyError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/v1/color/contrast", tags=["色彩"])
async def color_contrast(req: ColorContrastRequest):
    """WCAG 2.1 真实对比度 + AA/AAA 判级。"""
    eng = get_color_engine()
    try:
        return eng.contrast(req.fg, req.bg)
    except KeyError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/v1/color/convert", tags=["色彩"])
async def color_convert(req: ColorConvertRequest):
    eng = get_color_engine()
    try:
        return eng.convert(req.hex)
    except KeyError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/v1/color/delta-e", tags=["色彩"])
async def color_delta_e(req: DeltaERequest):
    eng = get_color_engine()
    try:
        return eng.delta_e(req.a, req.b, req.method)
    except KeyError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/v1/color/nearest", tags=["色彩"])
async def color_nearest(req: NearestPaletteRequest):
    eng = get_color_engine()
    try:
        return eng.nearest_in_palette(req.color, req.palette)
    except KeyError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/v1/color/pantone", tags=["色彩"])
async def color_pantone(req: PantoneRequest):
    """诚实端点：精确 Pantone/专色匹配需授权色库，服务不伪造编号，只给真实可算的 CMYK 等价值。"""
    eng = get_color_engine()
    try:
        return eng.pantone_match(req.hex)
    except KeyError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ----------------------------------------------------------------------------
# 布局排版引擎 (Layout — §45 · 碰撞/对齐/分布/安全区/网格/宽高比 真实几何)
# ----------------------------------------------------------------------------

@app.post("/api/v1/layout/collide", tags=["布局排版"])
async def layout_collide(req: LayoutCollideRequest):
    eng = get_layout_engine()
    try:
        return eng.collide(req.objects, min_overlap_ratio=req.min_overlap_ratio)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/v1/layout/align", tags=["布局排版"])
async def layout_align(req: LayoutAlignRequest):
    eng = get_layout_engine()
    try:
        return eng.align(req.objects, req.mode)
    except KeyError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/v1/layout/distribute", tags=["布局排版"])
async def layout_distribute(req: LayoutDistributeRequest):
    eng = get_layout_engine()
    try:
        return eng.distribute(req.objects, axis=req.axis, spacing=req.spacing)
    except KeyError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/v1/layout/safe-area", tags=["布局排版"])
async def layout_safe_area(req: LayoutSafeAreaRequest):
    eng = get_layout_engine()
    try:
        return eng.safe_area(req.objects, req.canvas, margin=req.margin)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/v1/layout/grid-snap", tags=["布局排版"])
async def layout_grid_snap(req: LayoutGridSnapRequest):
    eng = get_layout_engine()
    try:
        return eng.grid_snap(req.objects, req.canvas, cols=req.cols, rows=req.rows, mode=req.mode)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/v1/layout/analyze", tags=["布局排版"])
async def layout_analyze(req: LayoutAnalyzeRequest):
    eng = get_layout_engine()
    try:
        return eng.analyze(req.objects, canvas=req.canvas)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ----------------------------------------------------------------------------
# 包装与变量数据引擎 (Packaging / VDP — §46/§47 · 刀版/拼版/变量数据合并真实)
# ----------------------------------------------------------------------------

@app.post("/api/v1/packaging/dieline", tags=["包装"])
async def packaging_dieline(req: DielineRequest):
    """刀版几何：真实推导裁切框/出血框/安全框与最小 artwork 尺寸。
    Phase 51：响应自带结构化矢量面板 panels[]（仅成品面，panel_basis 如实标注），
    可直接作为 panel-topology 的入参——两端由孤岛变为真实血缘边。"""
    eng = get_packaging_engine()
    try:
        r = eng.dieline(req.width, req.height, bleed=req.bleed, safe=req.safe, units=req.units)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    # 纯计算事件（无落盘）：事件名以 .computed 明示，供血缘桥在事件账本里可观测
    await _emit("dieline.computed", {
        "finished": r.get("finished"), "trim_box": r.get("trim_box"),
        "bleed_box": r.get("bleed_box"), "safety_box": r.get("safety_box"),
        "panel_count": r.get("panel_count"), "panel_basis": r.get("panel_basis"),
        "units": r.get("units"), "warnings": r.get("warnings"),
    })
    return r


@app.post("/api/v1/packaging/imposition", tags=["包装"])
async def packaging_imposition(req: ImpositionRequest):
    """拼版 up-to-N：真实计算每行列数(含旋转取优)、单张满版数、用纸数与利用率。"""
    eng = get_packaging_engine()
    try:
        return eng.imposition(req.die_width, req.die_height, req.count,
                              req.sheet_width, req.sheet_height, gap=req.gap)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/v1/packaging/vdp/merge", tags=["包装"])
async def packaging_vdp_merge(req: VdpMergeRequest):
    """变量数据合并（§47）：真实读取 CSV/XLSX/JSON 按字段映射生成变体；缺列如实标注不臆造。"""
    eng = get_packaging_engine()
    path = validate_file_path(req.source)
    try:
        return eng.variable_data_merge(path, field_map=req.field_map, limit=req.limit)
    except (FileNotFoundError,) as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))


@app.post("/api/v1/packaging/panel-recognition", tags=["包装"])
async def packaging_panel_recognition(image_path: Optional[str] = None,
                                      min_area: int = 64,
                                      max_line_width: int = 12,
                                      px_per_mm: Optional[float] = None):
    """栅格刀版图 → 面板识别（§46 Panel Recognition · Phase 47 起真实交付，第 25 引擎）：
    Otsu 自算阈值二值化 + 轴对齐游程线抽取 + 共线间隙合并判 cut/fold + 虚线支撑屏障连通域，
    输出面板矩形/面积/矩形度、cut-fold 线段统计、邻接与度序列、折叠连通分量、外轮廓裁切线长。
    零 CV/ML 模型、零落盘副作用，阈值/覆盖率/像素数全部随响应回显可逐项复算；
    无墨或线未闭合如实降级 no_lines_detected / no_panels_detected（空面板列表，绝不臆造型）。
    缺 image_path→400；路径被安全策略阻止→403；文件不存在→404；非位图/尺寸非法/参数敌意→400。"""
    if not image_path or not str(image_path).strip():
        raise HTTPException(status_code=400,
                            detail="缺少 image_path 参数（须为位图刀版文件的本地路径）")
    mod = _require("panel_recognition")
    eng = get_panel_recognition_engine()
    path = validate_file_path(image_path)
    try:
        return eng.recognize(path, min_panel_area=min_area, max_line_width=max_line_width,
                             px_per_mm=px_per_mm)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except mod.PanelRecognitionError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/v1/packaging/panel-topology", tags=["包装"])
async def packaging_panel_topology(req: PanelTopologyRequest):
    """面板关系/拓扑（§46 Panel Relation）：从结构化矢量刀版（轴对齐面板矩形）真实推断邻接、
    折线、折叠连通性与外轮廓裁切线长——纯几何，不用 CV。与栅格图识别（panel-recognition）严格区分。"""
    eng = get_packaging_engine()
    try:
        r = eng.analyze_panel_topology([p.model_dump() for p in req.panels],
                                       tol=req.tol, min_fold=req.min_fold, units=req.units)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    # 纯计算事件（无落盘）：记录入参面板数与几何结论，使 dieline→topology 血缘边可观测
    await _emit("panel_topology.analyzed", {
        "input_panel_count": len(req.panels),
        "status": r.get("status"), "panel_count": r.get("panel_count"),
        "relation_count": r.get("relation_count"), "is_connected": r.get("is_connected"),
        "cut_boundary_length": r.get("cut_boundary_length"),
        "fold_length_total": r.get("fold_length_total"),
        "extent": r.get("extent"), "rejected": len(r.get("rejected") or []),
    })
    return r


@app.post("/api/v1/packaging/barcode", tags=["包装"])
async def packaging_barcode(req: BarcodeRequest):
    """条码/二维码渲染（§46 + Phase 57）：EAN-13 / EAN-8 真实校验位 + L/G/R 编码 + guard 条纹；
    QR 按 ISO/IEC 18004 纯标准库真实编码（v1..v10，L/M/Q/H 四级，掩码罚分选优，RS+BCH 纠错），
    两者均由 Pillow 渲染真 PNG。非法校验位/位数/纠错等级/超出已实现版本范围 → 400；
    未覆盖码制（Code-128 等）如实返回 needs_external，绝不伪造图像。
    quiet 省略时按码制各取其标准静区（一维 11 模块、QR 4 模块）。"""
    eng = get_packaging_engine()
    try:
        r = eng.render_barcode(req.code, symbology=req.symbology, scale=req.scale,
                               height=req.height, quiet=req.quiet, draw_text=req.draw_text,
                               ec_level=req.ec_level, output_path=req.output_path)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    await _emit("barcode.rendered", {
        "code": r.get("code"), "symbology": r.get("symbology"),
        "check_digit": r.get("check_digit"), "module_count": r.get("module_count"),
        "output_path": r.get("output_path"), "bytes": r.get("bytes"),
        "status": r.get("status"), "fabricated": r.get("fabricated"),
    })
    return r


# ============================================================================
# 自动更新端点（§75-§77 · 真实更新客户端机制；未配置 feed 诚实降级，绝不伪造发现新版本）
# ============================================================================

@app.get("/api/v1/update/channels", tags=["更新"])
async def update_channels():
    """列出 stable/beta/dev 渠道配置（脱敏，绝不回显验签密钥）。"""
    return get_update_engine().get_channels()


@app.post("/api/v1/update/configure", tags=["更新"])
async def update_configure(req: UpdateConfigureRequest):
    """配置某渠道的 feed_url / 验签密钥 / 当前版本。返回脱敏视图。"""
    eng = get_update_engine()
    try:
        view = eng.configure(req.channel, feed_url=req.feed_url, secret=req.secret,
                             current_version=req.current_version)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return view


@app.post("/api/v1/update/check", tags=["更新"])
async def update_check(req: UpdateCheckRequest):
    """真实拉取清单 + HMAC-SHA256 验签 + semver 比较。未配置→no_update_configured；
    网络失败→feed_unreachable；验签失败→integrity_failed；均如实返回，不伪造'有更新'。"""
    eng = get_update_engine()
    try:
        return eng.check(req.channel, current_version=req.current_version, timeout=req.timeout)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/v1/update/download", tags=["更新"])
async def update_download(req: UpdateDownloadRequest):
    """真实下载产物 + SHA-256 完整性校验；校验不过丢弃并返回 integrity_failed。"""
    eng = get_update_engine()
    try:
        return eng.download(req.channel, staged_dir=req.staged_dir, timeout=req.timeout)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/v1/update/apply", tags=["更新"])
async def update_apply(req: UpdateApplyRequest):
    """真实原子替换（备份→覆盖→校验→失败回滚）。目标被占用→pending_restart（下次启动应用），不谎称已更新。"""
    eng = get_update_engine()
    target = validate_file_path(req.target_path)  # 目标须存在且非禁入路径
    return eng.apply(str(target), req.staged_path, expected_sha256=req.expected_sha256)


@app.get("/api/v1/update/pending", tags=["更新"])
async def update_pending():
    """查询是否有一次因目标占用而待重启应用的更新。"""
    return get_update_engine().pending()


# ============================================================================
# NAS 网络存储端点（§81 · 真实客户端机制；未配置/不可达诚实降级，绝不伪造"已连接/枚举成功"）
# ============================================================================

@app.get("/api/v1/nas/targets", tags=["NAS"])
async def nas_targets():
    """列出已配置的 NAS 目标（脱敏，绝不回显口令）。"""
    return get_nas_engine().get_targets()


@app.post("/api/v1/nas/configure", tags=["NAS"])
async def nas_configure(req: NasConfigureRequest):
    """配置一个 NAS 目标：filesystem（映射盘/UNC/本地路径，经 OS 原生访问）或 webdav（PROPFIND 协议）。
    filesystem 建议给 share_root 作为沙箱根，枚举/锁/下载均受限于此以对齐 §99 路径穿越防护。返回脱敏视图。"""
    eng = get_nas_engine()
    try:
        return eng.configure(req.target_id, req.target, req.kind,
                             share_root=req.share_root, username=req.username, password=req.password)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/v1/nas/forget", tags=["NAS"])
async def nas_forget(req: NasConnectRequest):
    """删除一个已配置的 NAS 目标。"""
    return get_nas_engine().forget(req.target_id)


@app.post("/api/v1/nas/connect", tags=["NAS"])
async def nas_connect(req: NasConnectRequest):
    """真实可达性探测。filesystem：带超时原生探测；webdav：PROPFIND Depth:0。
    未配置→not_configured；不可达→unreachable；需凭据→auth_required。绝不凭'配了地址'谎报 connected。"""
    return get_nas_engine().connect(req.target_id, timeout=req.timeout)


@app.post("/api/v1/nas/list", tags=["NAS"])
async def nas_list(req: NasListRequest):
    """真实枚举共享目录：filesystem 逐条目取 name/type/size/mtime；webdav 解析 PROPFIND Depth:1 multistatus。
    越权子路径→forbidden；use_cache 且未过期→命中本地快照（cache:hit）。"""
    return get_nas_engine().list_dir(req.target_id, subpath=req.subpath, timeout=req.timeout,
                                     use_cache=req.use_cache, max_age=req.max_age)


@app.post("/api/v1/nas/lock", tags=["NAS"])
async def nas_lock(req: NasLockRequest):
    """真实文件锁检测（§81）：filesystem 用 msvcrt 独占区域锁探测被占用与否；
    webdav 无本地句柄，诚实返回 not_supported_for_webdav，绝不谎称锁态。"""
    return get_nas_engine().lock_check(req.target_id, req.subpath)


@app.post("/api/v1/nas/reconnect", tags=["NAS"])
async def nas_reconnect(req: NasReconnectRequest):
    """断线重连（§81）：对 connect 做真实指数退避重试，返回尝试次数与 recovered。"""
    return get_nas_engine().reconnect(req.target_id, attempts=req.attempts,
                                      base_delay=req.base_delay, timeout=req.timeout)


@app.post("/api/v1/nas/download", tags=["NAS"])
async def nas_download(req: NasDownloadRequest):
    """真实取回共享文件（filesystem copy / webdav GET）+ SHA-256。沙箱越权→forbidden；不存在→not_found。"""
    return get_nas_engine().download(req.target_id, req.subpath, dest_dir=req.dest_dir, timeout=req.timeout)


@app.get("/api/v1/nas/cache", tags=["NAS"])
async def nas_cache_stats():
    """目录清单缓存快照统计（真实落盘 JSON）。"""
    return get_nas_engine().cache_stats()


@app.post("/api/v1/nas/cache/clear", tags=["NAS"])
async def nas_cache_clear():
    """清空本地清单缓存。"""
    return get_nas_engine().cache_clear()


# ============================================================================
# 真实 AI Provider 端点（§101/§102 · 通用 OpenAI-兼容客户端；§17 Planner）
# 未配置 Provider 诚实 502，绝不伪造模型回复 / images_generated；密钥 DPAPI 加密、脱敏返回。
# ============================================================================

class ProviderConfigureRequest(BaseModel):
    provider_id: str
    base_url: str
    api_key: str = ""
    model: str = ""
    image_model: str = ""
    temperature: float = 0.7
    timeout: float = 60.0


class ProviderChatRequest(BaseModel):
    prompt: str = ""
    provider_id: Optional[str] = None
    messages: Optional[List[Dict[str, str]]] = None
    model: Optional[str] = None
    temperature: Optional[float] = None
    stream: bool = False
    timeout: float = 60.0


class ProviderImageRequest(BaseModel):
    prompt: str
    provider_id: Optional[str] = None
    n: int = 1
    size: str = "1024x1024"
    model: Optional[str] = None
    timeout: float = 120.0


class ProviderPlanRequest(BaseModel):
    goal: str
    provider_id: Optional[str] = None
    context: Optional[str] = None
    timeout: float = 60.0


class ProviderHealthRequest(BaseModel):
    provider_id: Optional[str] = None
    timeout: float = 15.0


class UxpHandshakeRequest(BaseModel):
    host: str                                   # photoshop / illustrator
    version: str = ""                           # §26：仅作诊断信息，不参与执行门控
    capabilities: Dict[str, bool] = Field(default_factory=dict)  # §25 Capability Map
    token: Optional[str] = None                 # §8 随机 Session Token


class UxpPollRequest(BaseModel):
    host_id: str
    token: Optional[str] = None
    timeout: float = 25.0


class UxpResultRequest(BaseModel):
    request_id: str
    token: Optional[str] = None
    status: str                                 # ok / error / failed（宿主如实回报）
    payload: Optional[Dict[str, Any]] = None    # 宿主真实执行结果
    error: Optional[str] = None                 # 宿主错误码（protected_object 等）
    detail: Optional[str] = None


class UxpDispatchRequest(BaseModel):
    tool: str                                   # §23 ps.*/ai.* 工具名
    args: Dict[str, Any] = Field(default_factory=dict)
    project_id: str = "project_default"
    document_id: str = "doc_default"
    protected_objects: List[str] = Field(default_factory=list)
    timeout: float = 30.0


def _provider_502(r: Dict[str, Any]) -> HTTPException:
    """把引擎的诚实降级结果映射为 502，detail 保留 fabricated=false 供测试断言。"""
    return HTTPException(status_code=502, detail={
        "status": r.get("status"), "fabricated": False,
        "error": r.get("error"), "detail": r.get("detail") or r.get("reason"),
    })


@app.get("/api/v1/provider", tags=["AI Provider"])
async def provider_list():
    """列出已配置 Provider（脱敏视图，绝不回显明文密钥）。"""
    return get_provider_engine().view()


@app.post("/api/v1/provider/configure", tags=["AI Provider"])
async def provider_configure(req: ProviderConfigureRequest):
    """接入一个真实 OpenAI-兼容 Provider（base_url + 有效 api_key + 配额属部署配置）。
    api_key 用 DPAPI 加密落盘、内存解密，返回脱敏视图，绝不回显密钥（§102.3）。"""
    eng = get_provider_engine()
    try:
        return await asyncio.to_thread(
            eng.configure, req.provider_id, req.base_url, req.api_key,
            req.model, req.image_model, req.temperature, req.timeout)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/v1/provider/health", tags=["AI Provider"])
async def provider_health(req: ProviderHealthRequest):
    """真实健康检查：向 {base_url}/models 发带鉴权的 GET，200=reachable。未配置诚实 no_provider_configured。"""
    eng = get_provider_engine()
    return await asyncio.to_thread(eng.health, req.provider_id, req.timeout)


@app.post("/api/v1/provider/chat", tags=["AI Provider"])
async def provider_chat(req: ProviderChatRequest):
    """真实文本生成：向配置的 Provider 发 /chat/completions，真解析 choices+usage。
    未配置→502 no_provider_configured；鉴权失败→502 auth_failed；不可达→502 unreachable。绝不伪造回复。"""
    eng = get_provider_engine()
    r = await asyncio.to_thread(
        eng.chat, req.prompt, req.provider_id, req.messages, req.model,
        req.temperature, req.stream, req.timeout)
    if r.get("status") != "ok":
        raise _provider_502(r)
    return r


@app.post("/api/v1/provider/images", tags=["AI Provider"])
async def provider_images(req: ProviderImageRequest):
    """真实图像生成：向配置的 Provider 发 /images/generations，真落盘 b64_json 为 PNG 并回真实张数/路径。
    未配置或失败一律 502 诚实降级，images_generated 绝不虚报（§2 红线）。"""
    eng = get_provider_engine()
    r = await asyncio.to_thread(
        eng.generate_image, req.prompt, req.provider_id, req.n, req.size, req.model, req.timeout)
    if r.get("status") != "ok":
        raise _provider_502(r)
    return r


@app.post("/api/v1/plan", tags=["AI Provider"])
async def ai_plan(req: ProviderPlanRequest):
    """§17 Planner：调用真实 LLM 产出结构化任务计划（goal/steps/tools/constraints/
    protected_objects/verification/rollback）。未配置→502；模型输出非 JSON→planner_output_unparseable，
    保留 raw 绝不硬编造 schema。"""
    eng = get_provider_engine()
    r = await asyncio.to_thread(eng.plan, req.goal, req.provider_id, req.context, req.timeout)
    if r.get("status") == "no_provider_configured":
        raise _provider_502(r)
    if r.get("status") == "error":
        raise _provider_502(r)
    return r


@app.get("/api/v1/uxp/bridge", tags=["Adobe UXP 桥接"])
async def uxp_bridge_view():
    """桥接机制视图（脱敏：Session Token 绝不回显明文；列出已连接宿主与可桥接工具）。"""
    return get_bridge_engine().view()


@app.get("/api/v1/uxp/discovery", tags=["Adobe UXP 桥接"])
async def uxp_bridge_discovery():
    """§8 端口动态发现（诊断视图）：回显端口 + 脱敏 token + 路径；明文 token 仅经本地 discovery 文件传递。"""
    eng = get_bridge_engine()
    info = eng.read_discovery() or {}
    return {
        "scheme": info.get("scheme"), "host": info.get("host"), "port": info.get("port"),
        "session_token": eng._mask(eng.session_token),
        "handshake_path": "/api/v1/uxp/handshake", "poll_path": "/api/v1/uxp/poll",
        "result_path": "/api/v1/uxp/result", "fabricated": False,
    }


def _bridge_http(e) -> HTTPException:
    """把桥接引擎的诚实错误映射为 502，detail 保留 fabricated=false（供测试断言无伪造）。"""
    return HTTPException(status_code=502, detail={
        "status": getattr(e, "kind", "bridge_error"), "fabricated": False,
        "error": getattr(e, "kind", "bridge_error"), "detail": getattr(e, "detail", str(e)),
    })


@app.post("/api/v1/uxp/handshake", tags=["Adobe UXP 桥接"])
async def uxp_bridge_handshake(req: UxpHandshakeRequest, request: Request):
    """§8 握手 + 认证 + §25 能力协商。成功后把"协商可执行的 §23 工具"并入 capability_map，
    使 /api/v1/tools 的 runnable_now 随宿主能力真实翻转（不伪造宿主）。"""
    eng = get_bridge_engine()
    ip = request.client.host if request.client else None
    try:
        res = await eng.handshake(req.host, req.version, req.capabilities, req.token, ip)
    except Exception as e:
        # BridgeError.kind 即稳定契约码（auth_failed / origin_rejected / unknown_host），经 502 如实降级
        raise _bridge_http(e)
    # §25 → §24 联动：按能力算出该宿主当前可执行的 §23 工具名，写入持久 capability_map
    try:
        mod = _require("adobe_bridge")
        exec_tools = {t: True for t in mod.BRIDGEABLE_TOOLS
                      if mod.CompatibilityMatrix.is_executable(req.host.lower(), t, req.capabilities)}
        cmap = await asyncio.to_thread(get_capability_map)
        hosts = cmap.setdefault("hosts", {})
        entry = hosts.setdefault(req.host.lower(), {"version": req.version, "capabilities": {}})
        entry["version"] = req.version
        entry.setdefault("capabilities", {}).update(exec_tools)
        entry["reported_at"] = datetime.now().isoformat()
        entry["source"] = "uxp_bridge_handshake"
        cmap["updated_at"] = datetime.now().isoformat()
        await asyncio.to_thread(_write_json, DATA_ROOT / "tools" / "capability_map.json", cmap)
        res["runnable_now_tools"] = sorted(exec_tools.keys())
    except Exception as e:  # pragma: no cover
        logger.warning("capability_map 联动更新失败(握手仍成功): %s", e)
    return res


@app.post("/api/v1/uxp/poll", tags=["Adobe UXP 桥接"])
async def uxp_bridge_poll(req: UxpPollRequest, request: Request):
    """宿主（UXP 面板）长轮询取待执行 §9 命令。认证失败/未注册 → 502 诚实降级。"""
    eng = get_bridge_engine()
    ip = request.client.host if request.client else None
    try:
        cmds = await eng.poll(req.host_id, req.token, ip, timeout=req.timeout)
    except Exception as e:
        raise _bridge_http(e)
    return {"status": "ok", "commands": cmds, "count": len(cmds), "fabricated": False}


@app.post("/api/v1/uxp/result", tags=["Adobe UXP 桥接"])
async def uxp_bridge_result(req: UxpResultRequest, request: Request):
    """宿主回传真实执行结果 → 引擎对账并唤醒等待中的 dispatch。未知请求如实 unknown_request，绝不静默伪造。
    §8 认证缺失/错误 → 502 诚实降级（detail 保留 fabricated=false），与 handshake/poll/dispatch 同形制。"""
    # P71 活体真缺陷 #2：本枚曾是该域四端点中唯一未包 try/except 的——submit_result 抛 BridgeError 后
    # 异常穿透 metrics_middleware ⇒ 裸 HTTP 500 + ASGI traceback，既丢 fabricated=false 申报，也对
    # route_hits / by_endpoint / requests_total / errors_5xx 四处计数完全隐身（中间件拿不到 response）。
    eng = get_bridge_engine()
    ip = request.client.host if request.client else None
    result = {"status": req.status, "payload": req.payload, "error": req.error, "detail": req.detail}
    try:
        return await eng.submit_result(req.request_id, req.token, ip, result)
    except Exception as e:
        # BridgeError.kind 即稳定契约码（auth_failed / origin_rejected）；其余异常同样如实降级，不返回假成功
        raise _bridge_http(e)


@app.post("/api/v1/uxp/dispatch", tags=["Adobe UXP 桥接"])
async def uxp_bridge_dispatch(req: UxpDispatchRequest, request: Request):
    """服务端把 §9 命令下发给已连接的 Adobe UXP 宿主并等待其真实回执（回流对账）。
    无宿主 / 超时 / 未协商能力 / 认证失败 → 一律 502 诚实降级，绝不伪造文档操作（§97 红线）。"""
    eng = get_bridge_engine()
    ip = request.client.host if request.client else None
    try:
        return await eng.dispatch(req.tool, req.args, req.project_id, req.document_id,
                                  req.protected_objects, req.timeout, client_ip=ip)
    except Exception as e:
        # BridgeError 的 kind 即稳定契约码；其余异常同样如实降级（不返回假成功）
        raise _bridge_http(e)


# ============================================================================
# Web 控制台（Phase 35）：零构建静态前端随 API 同端口提供。
# 注册在全部 API 路由之后，不遮蔽 /api、/docs、/redoc、/openapi.json。
# webui 目录缺失时 GET / 如实返回 webui_not_installed，绝不返回假页面。
# ============================================================================

WEBUI_DIR = resolve_webui_dir()


class _WebUIStatic(StaticFiles):
    """控制台资产一律带 cache-control: no-cache（Phase 69c 修的产品缺陷）。

    修前盘上实况（三条独立通道复量，收据 p69_r11b_r10_readback.txt 与
    p69_r12b_silent_proxy_probe.json 在场）：StaticFiles 只交 etag + last-modified、
    不写 cache-control ⇒ 按 RFC 9111 §4.2.2 走启发式新鲜度 =（Date − Last-Modified）×10%；
    本机实测两值差 11.4 小时 ⇒ 浏览器可约 68.5 分钟不回源。活体复量另见「文档 resp=200
    而服务端日志增量 0、transferSize=0、encodedBodySize=66195」。
    no-cache 不是 no-store：两把校验器照旧在场，未变时仍走 304 空体，省下的是「陈旧一页」
    而不是带宽。前端三枚字节一字未动 ⇒ P66/P67/P69 的 served==disk 逐字节判据照旧成立。"""

    def file_response(self, full_path, stat_result, scope, status_code=200):
        # super() 已完成 200/304 的裁决（is_not_modified），这里只在返回件上补一枚显式指令：
        # 形制 200（FileResponse）与形制 304（NotModifiedResponse）都经由这一处，故不存在
        # 「只给 200 写、304 漏写」的分叉。NotModifiedResponse 的透传白名单含 cache-control，
        # RFC 9111 §7.1 也要求 304 携带它。
        response = super().file_response(full_path, stat_result, scope, status_code)
        response.headers["cache-control"] = "no-cache"
        return response


WEBUI_FILES = _WebUIStatic(directory=str(WEBUI_DIR)) if WEBUI_DIR else None
if WEBUI_FILES:
    app.mount("/ui", WEBUI_FILES, name="webui")


@app.get("/", include_in_schema=False)
async def webui_index(request: Request):
    if WEBUI_FILES:
        # 与 /ui/* 同源裁决：复用同一实例的 get_response（内含 is_not_modified），
        # 不在此重写第二份 304 逻辑。牙齿台 p69_c1_cache_teeth.py 的 A0_live_pre 臂实测：
        # 修前 GET / 带匹配的 If-None-Match 仍回 66,195 B 全文 200、If-Modified-Since 亦 200
        # ——文档连回源复核都没兑现，而 /ui/* 却正确 304。「同一件事写两份」正是这次分叉的根因。
        return await WEBUI_FILES.get_response("index.html", request.scope)
    return JSONResponse(status_code=503, content={
        "status": "webui_not_installed",
        "reason": "未检测到 webui/ 静态资源目录（未随包分发或路径不可达）。API 服务不受影响，/docs 仍可用。",
    })


# ============================================================================
# 启动入口
# ============================================================================

HUB_FLAGS = ("--base-url", "--window-probe", "--selftest", "--json", "--text", "--help")
DEFAULT_HUB_BASE = "http://127.0.0.1:8000"
HUB_USAGE = (
    "用法: ZaojingAI hub [--base-url URL] [--window-probe] [--text]\n"
    "  （无参数即打开原生 Hub 窗口；只读三条 GET，动作须显式点击）\n"
    "  --base-url URL    指定服务地址；缺省依次取 ZAOJING_PORT 环境变量、%s\n"
    "  --selftest        不建窗，只打印数据层快照 JSON 后退出\n"
    "  --window-probe    自检并真建一次窗口（打印 hwnd/mapped/label_texts 后销毁）\n"
    "  --json | --text   输出形态（默认 json，ASCII 转义，GBK 控制台安全）\n"
    "退出码: 0 全源真实 / 3 降级（连不上·非本服务·JSON 残缺）/ 4 请求了窗口但建窗失败"
    "（无显示会话等）/ 2 用法错误" % DEFAULT_HUB_BASE)


def _hub_cli(argv: List[str]) -> int:
    """桌面 Hub 的 CLI 分支（Phase 50 第 27 引擎的入口面）。

    只有**用户显式**以 `hub` 子命令启动可执行文件才会走到这里；服务端任何 HTTP
    请求都不会触发建窗（那条路径根本不调用本函数）。
    """
    base: Optional[str] = None
    mode = "open"          # open | selftest | window-probe
    as_json = True
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--help" or a == "-h":
            sys.stdout.buffer.write((HUB_USAGE + "\n").encode("ascii", "backslashreplace"))
            return 0
        elif a in ("--base-url", "--url"):
            i += 1
            if i >= len(argv):
                sys.stdout.buffer.write(b"ERR missing value for --base-url\n")
                return 2
            base = argv[i]
        elif a.startswith("--base-url="):
            base = a.split("=", 1)[1]
        elif a == "--selftest":
            mode = "selftest"
        elif a == "--window-probe":
            mode = "window-probe"
        elif a == "--json":
            as_json = True
        elif a == "--text":
            as_json = False
        else:
            sys.stdout.buffer.write(
                ("ERR unknown_flag %r  合法旗标: %s\n" % (a, ", ".join(HUB_FLAGS)))
                .encode("ascii", "backslashreplace"))
            return 2
        i += 1

    try:
        hub_mod = _require("desktop_hub")
    except HTTPException as e:  # 引擎未加载：如实报，不假装窗口开成功
        sys.stdout.buffer.write(
            ("ERR engine_unavailable %s\n" % json.dumps({"detail": e.detail})).encode("ascii",
                                                                                       "backslashreplace"))
        return 3

    if mode == "selftest":
        res = hub_mod.hub_selftest(base, with_window=False)
        _emit_hub_output(res, as_json)
        return 0 if res["all_sources_live"] else 3

    if mode == "window-probe":
        res = hub_mod.hub_selftest(base, with_window=True)
        _emit_hub_output(res, as_json)
        if not res["all_sources_live"]:
            return 3
        return 0 if res["window"].get("created") else 4

    rep = hub_mod.open_hub(base)  # 进 mainloop，关窗后返回
    _emit_hub_output({k: v for k, v in rep.items() if not k.startswith("_")}, as_json)
    return 0 if rep["snapshot"]["all_sources_live"] else 3


def _emit_hub_output(payload: Any, as_json: bool) -> None:
    if as_json:
        blob = json.dumps(payload, ensure_ascii=True, sort_keys=True, default=str)
    else:
        rows = (payload.get("rows") or []) if isinstance(payload, dict) else []
        blob = "\n".join(rows) or json.dumps(payload, ensure_ascii=True, sort_keys=True,
                                             default=str)
    # 控制台可能是 GBK：JSON 走 ASCII 转义；--text 走 UTF-8 字节（供能解码的查看器）
    data = (blob + "\n").encode("ascii" if as_json else "utf-8", "backslashreplace")
    sys.stdout.buffer.write(data)
    sys.stdout.buffer.flush()


def _emit_cli_json(payload: Any) -> None:
    """CLI 输出只有一条路：ASCII 转义 JSON。控制台可能是 GBK，中文路径直写会炸在打印侧，
    而"结论没打出来"和"结论是错的"必须能区分——所以宁可回显 \\uXXXX 也不冒丢失的风险。"""
    blob = json.dumps(payload, ensure_ascii=True, sort_keys=True, default=str)
    sys.stdout.buffer.write((blob + "\n").encode("ascii", "backslashreplace"))
    sys.stdout.buffer.flush()


INSTALL_EXEC_FLAGS = ("--artifact", "--build-ledger", "--install-dir", "--ledger-out",
                      "--uninstall-key", "--app-reg-key", "--confirm", "--poll-timeout")
INSTALL_EXEC_USAGE = (
    "用法: ZaojingAI.exe install-exec <install|uninstall|plan|state> [旗标]\n"
    "  install     真跑一次静默安装（本产品的安装器），回执只由磁盘+注册表差集派生\n"
    "  uninstall   真跑自带卸载器，轮询到安装根与两枚 HKCU 键消失才算完\n"
    "  plan        只算不改：回显制品/构建台账/落点清单与门列表\n"
    "  state       只读对账执行台账（等价于 GET /api/v1/installer/execution）\n"
    "旗标: --artifact PATH --build-ledger PATH --install-dir PATH --ledger-out PATH\n"
    "      --uninstall-key KEY --app-reg-key KEY --confirm TOKEN --poll-timeout SEC\n"
    "  install/uninstall **必须**逐字交出 --confirm ZAOJING-CONFIRM-EXECUTE-INSTALLER；\n"
    "  缺省、空串、近似值一律 confirmation_required 拒绝\n"
    "  --artifact / --build-ledger 缺省按 <EXE 同目录>/installer、<上级>/installer、\n"
    "  <当前目录>/installer 三路寻径 setup.exe 与 build-evidence.json；三路皆无 ⇒ 如实\n"
    "  artifact_missing 并回显 looked_in\n"
    "  --uninstall-key / --app-reg-key 缺省从可发现的 setup.iss 派生；冻结 EXE 里没有 .iss 时\n"
    "  必须由操作者显式交出。键名只是**观察对象**而非证据：交错了只会让现场查不中、\n"
    "  结论朝『未装成』一侧偏，绝不会把『没查到』洗成『已安装』\n"
    "退出码: 0 已核验（install_verified/uninstall_verified/executed_verified/nothing_to_uninstall；\n"
    "  plan 只算不写，能算完即 0）\n"
    "  3 门前拒绝（未产生任何副作用）/ 4 跑了但未核验 / 5 state 无台账（本机从未跑过）\n"
    "  7 state 中间态（台账有 install_verified、尚无卸载记录，且本机现场确实是完整安装态）\n"
    "  2 用法错误 / 6 引擎不可用或执行器内部异常\n"
    "本 CLI 是唯一执行入口：安装域 HTTP 面 6 条全为 GET，结构上走不到这里。")


def _install_exec_find_artifact(rel_name: str) -> Tuple[Optional[str], List[str]]:
    """制品寻径（P53 三路纪律的执行面版本）：_MEIPASS > EXE/脚本同目录 > 上级 > 当前目录，
    各取其下 installer/<rel_name>。找不到时把找过的地方原样回显——"没找到"必须是可复核的
    事实，不是一句断言。"""
    bases = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        bases.append(Path(meipass))
    bases += [app_base_dir(), app_base_dir().parent, Path.cwd()]
    cands, looked = [], []
    for b in bases:
        q = b / "installer" / rel_name
        looked.append(str(q))
        if q.is_file() and str(q) not in cands:
            cands.append(str(q))
    return (cands[0] if cands else None), looked


def _install_exec_cli(argv: List[str]) -> int:
    """安装/卸载执行器 CLI（Phase 58）。

    与 hub 子命令同一条纪律：只有**用户显式**把子命令敲进命令行才会走到这里；
    服务端任何 HTTP 请求都不可能触发（那条路径根本没有这个分支，且安装域六条 HTTP 路由
    全为 GET：/api/v1/installer/{manifest/verify, state, artifact, preflight, execution}
    加 runtime 域的 /api/v1/runtime/install-actions——Phase 60 就地改核，把裸计数改成点名，
    结论未变）。
    判定不认自报：本函数只负责把参数交给引擎，引擎把结论交给磁盘与注册表。
    """
    if not argv or str(argv[0]).lower() in ("--help", "-h", "help"):
        sys.stdout.buffer.write((INSTALL_EXEC_USAGE + "\n").encode("ascii", "backslashreplace"))
        return 0 if argv else 2
    sub = str(argv[0]).lower()
    if sub not in ("install", "uninstall", "plan", "state"):
        _emit_cli_json({"capability": "installer_execution", "status": "usage_error",
                        "subcommand": sub, "legal": ["install", "uninstall", "plan", "state"],
                        "fabricated": False})
        return 2

    opts: Dict[str, str] = {}
    i = 1
    while i < len(argv):
        a = str(argv[i])
        if a.startswith("--") and "=" in a:
            k, v = a.split("=", 1)
            opts[k] = v
        elif a.startswith("--"):
            if i + 1 >= len(argv):
                _emit_cli_json({"capability": "installer_execution", "status": "usage_error",
                                "reason": "missing_value", "flag": a, "fabricated": False})
                return 2
            opts[a] = argv[i + 1]
            i += 1
        else:
            _emit_cli_json({"capability": "installer_execution", "status": "usage_error",
                            "reason": "unexpected_argument", "argument": a,
                            "legal_flags": list(INSTALL_EXEC_FLAGS), "fabricated": False})
            return 2
        i += 1
    bad = sorted(k for k in opts if k not in INSTALL_EXEC_FLAGS)
    if bad:
        _emit_cli_json({"capability": "installer_execution", "status": "usage_error",
                        "reason": "unknown_flag", "flags": bad,
                        "legal_flags": list(INSTALL_EXEC_FLAGS), "fabricated": False})
        return 2

    try:
        eng = _require("runtime")
    except HTTPException as e:  # 引擎没加载：如实报，绝不把"没引擎"说成"安装失败"
        _emit_cli_json({"capability": "installer_execution", "status": "engine_unavailable",
                        "detail": e.detail, "fabricated": False})
        return 6
    # 令牌镜像自检：usage 文本里的串与引擎实际判定的串必须同源，否则拒绝原因与提示会分家
    token_drift = str(getattr(eng, "CONFIRM_TOKEN", "")) != "ZAOJING-CONFIRM-EXECUTE-INSTALLER"

    keys = _execution_registry_keys()
    uninstall_key = opts.get("--uninstall-key") or keys.get("uninstall_key", "")
    app_reg_key = opts.get("--app-reg-key") or keys.get("app_reg_key", "")
    app_name = keys.get("app_name") or DEFAULT_INSTALLER_APP_NAME
    vendor_dir = keys.get("vendor_dir") or DEFAULT_INSTALLER_VENDOR_DIR
    common = {"app_name": app_name, "vendor_dir": vendor_dir,
              "data_dir_name": INSTALLED_DATA_DIR_NAME,
              "uninstall_key": uninstall_key, "app_reg_key": app_reg_key,
              "ledger_path": opts.get("--ledger-out") or str(_execution_ledger_path())}
    out: Dict[str, Any] = {"capability": "installer_execution", "subcommand": sub,
                           "registry_keys": dict(keys, overridden={
                               "uninstall_key": bool(opts.get("--uninstall-key")),
                               "app_reg_key": bool(opts.get("--app-reg-key"))}),
                           "token_mirror_drift": token_drift, "fabricated": False}

    if sub == "state":
        st = eng.execution_state(ledger_path=common["ledger_path"],
                                 uninstall_key=uninstall_key, app_reg_key=app_reg_key,
                                 app_name=app_name, vendor_dir=vendor_dir,
                                 data_dir_name=INSTALLED_DATA_DIR_NAME)
        out.update(st)
        out["service_data_root"] = str(DATA_ROOT)
        _emit_cli_json(out)
        # 退出码表与用法串同源：中间态既不是"跑了没核验"（4）也不是"已核验"（0），
        # 它是"装成了、还差卸载这一笔"，必须自成一档，否则操作者只能从 4 里猜。
        return {"executed_verified": 0, "ledger_absent": 5,
                "install_verified_awaiting_uninstall": 7}.get(st.get("status"), 4)

    art, looked = None, []
    if opts.get("--artifact"):
        art = opts["--artifact"]
    else:
        art, looked = _install_exec_find_artifact("zaojing-ai-%s-setup.exe" % APP_VERSION)
        if art is None:
            art = str(Path(looked[0]))  # 交给门去判缺，reason 与 looked_in 一起回显
    build_ledger = opts.get("--build-ledger") or ""
    if not build_ledger and art:
        build_ledger = str(Path(art).parent / "build-evidence.json")
    install_dir = opts.get("--install-dir") or str(
        Path(os.environ.get("LOCALAPPDATA") or str(Path.home())) / "Programs" / vendor_dir)

    try:
        if sub == "plan":
            res = eng.install_plan(artifact=art, install_dir=install_dir,
                                   build_ledger=build_ledger or None,
                                   app_name=app_name, vendor_dir=vendor_dir,
                                   data_dir_name=INSTALLED_DATA_DIR_NAME,
                                   app_id_braced=keys.get("app_id", ""))
        elif sub == "install":
            res = eng.execute_install(artifact=art, install_dir=install_dir,
                                      confirm=opts.get("--confirm"), app_version=APP_VERSION,
                                      build_ledger=build_ledger or None, **common)
        else:
            res = eng.execute_uninstall(install_dir=install_dir,
                                        confirm=opts.get("--confirm"),
                                        poll_timeout_s=float(opts.get("--poll-timeout", 120.0)),
                                        app_id_braced=keys.get("app_id", ""), **common)
    except eng.ExecutionError as e:  # 门开在写盘之前：走到这里本机没有被改动过
        _emit_cli_json(dict(out, status="refused", reason=e.reason,
                            detail=getattr(e, "detail", {}), message=str(e),
                            side_effects="none", looked_in=looked, fabricated=False))
        return 3
    except (OSError, ValueError, TypeError) as e:
        _emit_cli_json(dict(out, status="executor_error",
                            exception=type(e).__name__, message=str(e)[:400],
                            looked_in=looked, fabricated=False))
        return 6

    out.update(res)
    out["looked_in"] = looked
    _emit_cli_json(out)
    # plan 没有"status"字段可比：它只算不写，能算完就是成功；执行类子命令只认引擎派生的核验态
    if sub == "plan":
        return 0
    return 0 if str(res.get("status", "")) in ("install_verified", "uninstall_verified",
                                               "nothing_to_uninstall") else 4


def main():
    _args = sys.argv[1:]
    if _args and str(_args[0]).lower().lstrip("-") == "hub":
        raise SystemExit(_hub_cli(_args[1:]))
    if _args and str(_args[0]).lower().lstrip("-") == "install-exec":
        raise SystemExit(_install_exec_cli(_args[1:]))

    import uvicorn

    logger.info("=" * 70)
    logger.info(f"造境 AI 设计智能体 V{APP_VERSION} 启动")
    logger.info(f"模式: {'便携版' if is_portable() else '安装版'} | 数据目录: {DATA_ROOT}")
    logger.info(f"引擎: {len(_MODULES)}/{len(_ENGINE_NAMES)} 已加载" + (f" | 缺失: {_IMPORT_ERRORS}" if _IMPORT_ERRORS else ""))
    logger.info("=" * 70)

    # 预初始化核心组件（失败不阻断启动，端点降级返回 503）
    for name, fn in [("settings", get_settings), ("license", get_license_manager), ("document", get_document_store)]:
        try:
            fn()
        except Exception as e:
            logger.error(f"{name} 初始化失败(降级运行): {e}")

    host = os.environ.get("ZAOJING_HOST", "127.0.0.1")
    port = int(os.environ.get("ZAOJING_PORT", "8000"))
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
