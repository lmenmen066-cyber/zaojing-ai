"""
造境 AI 设计智能体 - 安装清单校验引擎 installer_manifest_engine（Phase 49 真实交付）

它做什么（全部为**只读、可逐项复算**的静态校验，绝不执行任何安装动作）：
- 解析 Inno Setup 脚本（setup.iss）的 [Setup]/[Files]/[Icons]/[Dirs]/[Registry]/[Run]
  声明，逐条与**磁盘实况**对照，输出结构化 finding（id / severity / 证据 / 判据）。
- 校验规则全部由脚本文本与文件系统真值决定：未替换的模板常量、Source 字面路径是否
  存在、通配 Source 是否有命中、卸载图标目标与 DefaultDirName 派生的卸载器实际位置
  是否一致、载荷 DestDir 与安装根是否分裂、AppId 是否为合法 GUID 形制且字面花括号是否
  双写、[Dirs] 是否覆盖服务运行时真实使用的数据根、[Registry] 值里是否残留占位字面量、
  Permissions 项形态、per-user 区域与 PrivilegesRequired 的配对（R11）、Architectures*
  里的弃用架构标识（R12）、[Setup] 是否给出 AppVersion/AppVerName（R13，编译级致命）。
  其中 R6 的转义臂、R11、R12、R13 四条**不是推测**：每条都对应本机真 iscc 的一次实测退出码
  或一行告警/错误原文（Phase 53，见 installer/build-evidence.json 与 p53_iscc_warning_probe.py），
  判词里以 compiler_proven / iscc_observed 字段回指该原话。
- 只读安装态探测：winreg 枚举 HKCU/HKLM 的 Uninstall 子键并据 AppId 派生
  `{AppId}_is1` 精确查找，辅以 DisplayName 兜底匹配、开始菜单组目录、应用数据根、
  便携标记存在性；非 Windows 如实 environment_unavailable。

诚实边界（§2 红线）：
- 本引擎**校验**安装清单，不**执行**安装/卸载。静默安装、写注册表、建快捷方式、跑
  卸载器自 Phase 58 起由 installer_runtime_engine 的执行器承担（入口只有 CLI + 显式
  同意令牌，HTTP 面结构上够不着）；本引擎另立
  installer_manifest_verification = supported（可观测/可校验部分），performed 恒 False。
- 未安装状态下绝不伪造"已安装"。install_state 的 installed 完全由逐项真实观察派生，
  证据为空即 False，并逐项回显观察值。
- 编译器是否存在由 locate_compiler() **三路寻径**实测（固定目录 → 只读注册表 → PATH），
  不写死；iscc 缺席时"脚本能否编译"不属可证范围：相关项一律判为 info 并声明需编译器定论，
  绝不说"已通过编译校验"。
- 纯标准库（re/glob/os/pathlib + 只读 winreg），零落盘、零子进程、零网络。

历史更正（Phase 53）：本引擎上线时（Phase 49）声称"本机没有 iscc、安装版从未真正编译"，
并据此把 R9 类项目一律判 info —— 该前提是**错的**：ISCC.exe 自 2026-07-19 起就装在
%ProgramFiles(x86)% 下的 Inno Setup 6 目录里，旧探测只问 shutil.which（PATH 单路），
PATH 未命中被当成"未安装"这一环境事实，一路写进了报告、发货文档与服务端文案。
寻径修复后 setup.iss 于 Phase 53 首次真正编译（见审计报告 Phase 53 段与 L2 断言）。
"""

from __future__ import annotations

import glob
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SCHEMA = "zaojing-installer-manifest/v1"
WINDOWS = os.name == "nt"

# Inno Setup 自身认识的常量名（小写）。脚本里出现的 {xxx} 若不在此集合、也不是
# {code:..}/{cm:..}/{param|..}/{#..} 形态，即为"未被替换、留在文本里的字面量"。
INNO_CONSTANTS = frozenset({
    "app", "autopf", "autoprograms", "autodesktop", "autofonts", "autogames",
    "autotemplates", "autofavorites", "commonpf", "commonprograms", "commondesktop",
    "commondocs", "commonappdata", "commonlocalappdata", "commontemplates",
    "userpf", "userprograms", "userdesktop", "userdocs", "userappdata",
    "userlocalappdata", "usertemplates", "userfavorites",
    # 裸形 {localappdata}/{appdata} 亦为 Inno 合法常量（按安装模式解析为 user 或 common）
    "localappdata", "appdata",
    "group", "src", "tmp", "temp", "win", "winver", "sys", "syswow64", "sd",
    "fonts", "uninstallexe", "log", "logfile", "uuid", "userprofile",
    "commonprofile", "database", "language", "addon",
})

# 安装/卸载执行域（校验 ≠ 执行）。Phase 58 起这枚字段的意思**变了**：它不再说"没人能做到"，
# 而只说"我这台只读仪器没做"。执行器确实随产品交付了，但入口只有 CLI（install-exec，且必须
# 逐字交出显式同意令牌），HTTP 面在结构上够不着它——所以本引擎的每一份报告仍然如实标
# performed=False。被退役的旧状态原文留在 retired_boundary.was 里：退役 ≠ 改写。
EXECUTION_BOUNDARY = {
    "status": "not_executed_by_this_engine",
    "performed": False,
    "owned_by": "installer_runtime_engine CLI executor (install-exec), not this read-only engine",
    "reason": "本引擎只做清单静态校验与只读安装态探测，零子进程、零注册表写入、零快捷方式；"
              "静默安装与卸载自 Phase 58 起由同一产品的执行器承担，入口只有 CLI 且必须先逐字交出显式同意令牌",
    "fabricated": False,
    "retired_boundary": {
        "was": "needs_installer_environment",
        "since": "Phase 26",
        "retired_in": "Phase 58",
        "why": "iscc 与 setup.iss 均已真实在场（Phase 53 三路固定目录寻径实证，历轮『本机没有 Inno Setup』属仪器伪影），"
               "执行器随之交付；『需要安装器环境』不再是本服务无能为力的借口，只是 HTTP 面刻意够不着的副作用域",
    },
}

SECTION_RE = re.compile(r"^\s*\[([A-Za-z][A-Za-z0-9 ]*)\]\s*$")
TOKEN_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_|]*)\}")
FORM_RE = re.compile(r"\{(?:code|cm|param|emit|emitfail|root|has)", re.I)
# 严格 8-4-4-4-12 十六进制 GUID（不带花括号）。R6 的"未转义"判据只在**确属 GUID 形制**
# 时开火：非十六进制串另有 appid_not_uuid 告警覆盖，两臂各自可独立证伪、互不遮蔽。
GUID_RE = re.compile(r"\A[0-9A-Fa-f]{8}(?:-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}\Z")

# ---- Phase 53 由本机真编译器（Inno Setup 6.7.3 / ISCC.exe）定论的三条形制 ----
# 下面三段引文不是转述：由 outputs/p53_iscc_warning_probe.py 在系统临时目录用最小假载荷
# 真编译取得，逐字符照抄（探针三段：both_warnings exit=0 / no_version_directive exit=2 /
# fixed exit=0 零告警）。把它们写进 finding 证据，是为了让每条静态判词都能回指编译器原话，
# 而不是"引擎作者认为 Inno 会这么判"。
ISCC_ARCH_WARNING = (
    'Warning: Architecture identifier "x64" is deprecated. Substituting "x64os", but '
    'note that "x64compatible" is preferred in most cases. See the "Architecture '
    'Identifiers" topic in help file for more information.')
ISCC_PRIVILEGES_WARNING = (
    'Warning: The [Setup] section directive "PrivilegesRequired" is set to "admin" but '
    'per-user areas (HKCU,localappdata,userdocs) are used by the script. Regardless of '
    'the version of Windows, if the installation is running in administrative install '
    'mode then you should be careful about making any per-user area changes: such '
    'changes may not achieve what you are intending. See the "UsedUserAreasWarning" '
    'topic in help file for more information.')
# 编译器原话（去掉 "Error in <脚本路径>: " 前缀与随后的 "Compile aborted." 两行套话）
ISCC_MISSING_VERSION_ERROR = ("The [Setup] section must include an AppVersion or "
                              "AppVerName directive.")

# Inno 的 per-user 形制常量（均已在 INNO_CONSTANTS 内、即 Inno 认识的写法）。{group}、
# {autopf}、{autoprograms} 一类 auto 常量按安装模式解析，本身不构成 per-user 断言，故不入集合。
PER_USER_AREA_CONSTANTS = frozenset({
    "localappdata", "appdata", "userdocs", "userappdata", "userlocalappdata",
    "userpf", "userprograms", "userdesktop", "usertemplates", "userfavorites",
    "userprofile",
})
# PrivilegesRequired 的 admin 生效形制：显式写法与缺省同义（iscc 缺省告警原文即 set to "admin"）。
# 只列本机编译器点名过的 admin，外加其同义复数写法；lowest/none/poweruser 一类非 admin 形制
# 一律落在"不报"的一侧——把未实测的值写进判据就是无据外推，宁可漏报不可冤枉。
DEFAULT_PRIVILEGES_REQUIRED = "admin"
ADMIN_PRIVILEGES = frozenset({"admin", "administrators"})
# 弃用架构标识 → 编译器推荐的替代标识（整词比较，绝不做 "x64" 子串匹配）。
# 集合里只有本机编译器**亲自点名过**的 x64：x86 等其它标识未经实测，写进判据就是无据的外推。
DEPRECATED_ARCH_IDENTIFIERS = {"x64": "x64compatible"}


class ManifestError(ValueError):
    """入参非法（非文本、空路径、越界路径等）。"""


# ---------------------------------------------------------------------------
# 真实文件系统/注册表接缝：生产路径即标准库调用；自测与评测在模块级替换以构造反证。
# ---------------------------------------------------------------------------

def _path_exists(path: str) -> bool:
    return os.path.exists(path)


def _dir_exists(path: str) -> bool:
    return os.path.isdir(path)


def _glob_matches(pattern: str) -> List[str]:
    return sorted(glob.glob(pattern))


def _which_iscc() -> Optional[str]:
    """PATH 单路寻径（保留为交叉核对用，不再单独充当"装没装"的结论）。"""
    return shutil.which("iscc")


# --- 编译器寻径（Phase 53）---------------------------------------------------
# 起因：本机 %ProgramFiles(x86)%\Inno Setup 6\ISCC.exe 一直存在，而旧实现只问
# shutil.which("iscc")（只查 PATH），于是连续三十余轮把"PATH 未命中"当成"本机未安装
# Inno Setup"这一环境事实写进报告、发货文档与服务端文案。三路寻径的判据就是：
# 任何一路命中都要如实回显来源，三路全空也只报"未命中"、绝不出具"未安装"的证据。
COMPILER_EXE = "ISCC.exe"
COMPILER_VERSION_DIRS = ("Inno Setup 6", "Inno Setup 7")
COMPILER_ABSENT_NOTE = ("三路寻径均未命中；此结论仅描述本仪器的寻径结果，"
                        "不构成'本机不存在编译器'的环境事实断言")


def _compiler_fixed_dirs() -> List[str]:
    """Inno 默认安装位置下的编译器候选（不查 PATH）。"""
    out: List[str] = []
    for env_name in ("ProgramFiles(x86)", "ProgramFiles", "ProgramW6432", "LOCALAPPDATA"):
        root = _env_dir(env_name)
        if not root:
            continue
        for ver in COMPILER_VERSION_DIRS:
            out.append(os.path.join(root, ver, COMPILER_EXE))
    return out


def _compiler_from_registry() -> Optional[str]:
    """只读注册表寻径：Inno 自装的卸载项里带 InstallLocation（32/64 视图都查）。

    64 位 Python 打开 HKLM\\...\\Uninstall 拿到的是 64 位视图，而 Inno Setup 6 是 32 位
    程序、其键落在 WOW6432Node——只查一个视图就会漏，同 PATH-only 是同一类仪器缺陷。
    """
    for root in ("HKLM", "HKCU"):
        for view in ("64", "32"):
            for sub in _enum_uninstall_keys(root, view):
                if "inno setup" not in sub.lower():
                    continue
                loc = _read_uninstall_value(root, sub, "InstallLocation", view)
                if not loc:
                    continue
                cand = os.path.join(loc.rstrip("\\/"), COMPILER_EXE)
                if _path_exists(cand):
                    return cand
    return None


def locate_compiler(observe_binary: bool = True) -> Dict[str, Any]:
    """三路寻径安装脚本编译器：固定目录 → 只读注册表 → PATH。

    纯标准库、只读、零子进程。返回逐路观测值，命中时附绝对路径与字节/sha（读不到则为
    None，不编造）。proves_absence 恒为 False：本函数只能证明"找到了"，永远不能证明
    "机器上没有"。observe_binary=False 供逐条 finding 使用，避免为大文件重复读取。
    """
    candidates = _compiler_fixed_dirs()
    fixed_hits = [p for p in candidates if _path_exists(p)]
    reg_hit = _compiler_from_registry()
    path_hit = _which_iscc()
    if fixed_hits:
        mode, hit = "fixed_dir", fixed_hits[0]
    elif reg_hit:
        mode, hit = "registry", reg_hit
    elif path_hit:
        mode, hit = "path", path_hit
    else:
        mode, hit = "none", None
    size = digest = None
    if hit is not None and observe_binary:
        try:
            raw = Path(hit).read_bytes()
            size, digest = len(raw), hashlib.sha256(raw).hexdigest()
        except OSError:
            pass
    return {
        "schema": SCHEMA,
        "capability": "installer_compiler_lookup",
        "exe": COMPILER_EXE,
        "found": hit is not None,
        "state": "located" if hit is not None else "not_located",
        "lookup_mode": mode,
        "path": hit,
        "bytes": size,
        "sha256": digest,
        "proves_absence": False,
        "sources": {
            "fixed_dirs": {"probed": len(candidates), "hits": fixed_hits},
            "registry": {"hit": reg_hit},
            "path": {"hit": path_hit},
        },
        "conclusion": ("已定位（来源=%s）" % mode) if hit else COMPILER_ABSENT_NOTE,
    }


def iscc_available() -> bool:
    """本机是否装有 Inno Setup 编译器（公开接缝；Phase 53 起由三路寻径派生）。

    校验器对"只有 iscc 才能定论"的项一律只出 info、不出结论；把这个前提显式化，
    调用方（含服务端点）就能如实回显"本次校验是在无编译器环境下做的"，
    而不会让读者把 manifest_ok 误读成"编译已通过"。
    旧实现只问 shutil.which，PATH 未命中即答 False——本机实测编译器一直在盘上，
    故该接缝现由 locate_compiler() 的三路结果派生。
    """
    return bool(locate_compiler()["found"])



# Inno 文件系统常量 → 本机环境变量。用于把脚本里的声明路径解析成真实路径后再与
# 运行时数据根比对；不解析就会把写法正确的 {localappdata}\Zaojing 误判为"无交集"。
_INNO_ENV_MAP = {
    "localappdata": "LOCALAPPDATA",
    "userlocalappdata": "LOCALAPPDATA",
    "appdata": "APPDATA",
    "userappdata": "APPDATA",
    "commonappdata": "PROGRAMDATA",
    "userprofile": "USERPROFILE",
    "win": "WINDIR",
    # 注意：{temp} 是 Windows 临时目录（≈GetTempPath），而 {tmp} 是安装器自建的
    # 解压临时目录，环境里并无对应项，故 {tmp} 不参与解析、原样保留。
    "temp": "TEMP",
}


def _env_dir(name: str) -> str:
    """环境目录接缝：生产即读 os.environ；自测/评测在模块级替换以脱离本机依赖。"""
    return os.environ.get(name, "") or ""


def _resolve_inno_path(path: str) -> str:
    """`{localappdata}\\Zaojing\\Assets` → 本机真实路径；未知常量原样返回。"""
    if not isinstance(path, str) or not path.startswith("{"):
        return path
    m = re.match(r"\{([A-Za-z_][A-Za-z0-9]*)\}(.*)\Z", path, re.S)
    if not m:
        return path
    env_name = _INNO_ENV_MAP.get(m.group(1).lower())
    if not env_name:
        return path
    base = _env_dir(env_name)
    if not base:
        return path
    rest = [seg for seg in re.split(r"[\\/]", m.group(2)) if seg]
    return os.path.join(base, *rest) if rest else base


def _same_place(a: str, b: str) -> bool:
    """两个声明路径是否指向同一处（先解析 Inno 常量，再按规范路径比对）。"""
    if not a or not b:
        return False
    na = os.path.normcase(os.path.abspath(_resolve_inno_path(a.replace("\\", os.sep))))
    nb = os.path.normcase(os.path.abspath(_resolve_inno_path(b.replace("\\", os.sep))))
    return na == nb or na.startswith(nb + os.sep) or nb.startswith(na + os.sep)


def _enum_uninstall_keys(root: str, view: str = "64") -> List[str]:
    """只读枚举 HKCU/HKLM 的 Uninstall 子键名；非 Windows 或键缺失返回空列表。

    view="32" 走 KEY_WOW64_32KEY（64 位 Python 默认只看见 64 位视图，32 位程序如
    Inno Setup 6 自身的卸载键落在 WOW6432Node，不显式请求视图就永远枚举不到）。
    """
    if not WINDOWS:
        return []
    import winreg
    hive = winreg.HKEY_CURRENT_USER if root == "HKCU" else winreg.HKEY_LOCAL_MACHINE
    flag = winreg.KEY_WOW64_32KEY if view == "32" else winreg.KEY_WOW64_64KEY
    try:
        h = winreg.OpenKey(hive, r"Software\Microsoft\Windows\CurrentVersion\Uninstall",
                           0, winreg.KEY_READ | flag)
    except OSError:
        return []
    count = winreg.QueryInfoKey(h)[0]
    return [winreg.EnumKey(h, i) for i in range(count)]


def _read_uninstall_value(root: str, subkey: str, name: str, view: str = "64") -> Optional[str]:
    if not WINDOWS:
        return None
    import winreg
    hive = winreg.HKEY_CURRENT_USER if root == "HKCU" else winreg.HKEY_LOCAL_MACHINE
    flag = winreg.KEY_WOW64_32KEY if view == "32" else winreg.KEY_WOW64_64KEY
    try:
        h = winreg.OpenKey(hive, "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\" + subkey,
                           0, winreg.KEY_READ | flag)
        val = winreg.QueryValueEx(h, name)[0]
    except OSError:
        return None
    return val if isinstance(val, str) else None


def display_name_matches(display_name: str, app_name: str) -> bool:
    """卸载项显示名的接受规则：以 `app_name` 起始（Phase 58 本机真装实测后定）。

    与 `installer_runtime_engine.display_name_matches` 同一条规则、同一批实测依据；两枚引擎在
    冻结包里各自独立成模块（互不 import），故此处按字面重复而非跨引擎引用——跨引擎的
    一致性由两套 L1 自测各自钉住"实测那枚显示名必须收、无关显示名必拒"来保证。

    旧规则是 `dn.strip().lower() == app_name.strip().lower()`（逐字相等）。本机真装当轮
    （Phase 58）在场核对：HKCU 的卸载项 `DisplayName` 实写 **"ZaojingAI 1.0.0"**——Inno 在
    `[Setup]` 未声明 `UninstallDisplayName` 时把显示名写成 `AppName + " " + AppVersion`。
    于是逐字相等的兜底扫描对**真实世界**永不命中：只读探测面在"已知 AppId"时靠精确子键
    还能命中，一旦 app_id 传空（文档承诺"空则只走 DisplayName 兜底"）就会把**已安装**
    判成未安装。这条缝隙此前无从暴露，因为两套自测的桩都按旧的逐字相等形状造数据。
    """
    dn, an = str(display_name or "").strip(), str(app_name or "").strip()
    return bool(an) and dn.startswith(an)


# ---------------------------------------------------------------------------
# 解析
# ---------------------------------------------------------------------------

def parse_script(text: Any) -> Dict[str, List[Tuple[int, str]]]:
    """把 .iss 文本解析为 {section: [(物理行号, 续行合并后的语句)]}。

    Inno 用行尾反斜杠续行；空行与 ; 注释行剔除。行号指向语句起始物理行，便于回指原文。
    """
    if not isinstance(text, str):
        raise ManifestError("脚本内容必须是文本，收到 %s" % type(text).__name__)
    lines = text.splitlines()
    sections: Dict[str, List[Tuple[int, str]]] = {}
    cur = "unnamed"
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        start = i + 1
        i += 1
        if not line or line.startswith(";"):
            continue
        m = SECTION_RE.match(line)
        if m:
            cur = m.group(1).strip()
            sections.setdefault(cur, [])
            continue
        while line.endswith("\\") and i < len(lines):
            line = line[:-1].rstrip() + " " + lines[i].strip()
            i += 1
        if line.startswith(";"):
            continue
        sections.setdefault(cur, []).append((start, line))
    return sections


def setup_map(entries: List[Tuple[int, str]]) -> Dict[str, Tuple[str, int]]:
    out: Dict[str, Tuple[str, int]] = {}
    for ln, stmt in entries:
        if "=" not in stmt:
            continue
        k, v = stmt.split("=", 1)
        out[k.strip().lower()] = (v.strip(), ln)
    return out


def split_params(stmt: str) -> Dict[str, str]:
    """把 `Name: "x"; Filename: "y"; Flags: a b` 拆成 {name: value}（分号在引号内不切）。"""
    parts = re.split(r';\s*(?=(?:[^"]*"[^"]*")*[^"]*$)', stmt)
    out: Dict[str, str] = {}
    for p in parts:
        p = p.strip()
        if not p:
            continue
        if ":" in p:
            k, v = p.split(":", 1)
            key, val = k.strip().lower(), v.strip().strip('"')
        else:
            key, val = "_raw", p.strip('"')
        out[key] = (out[key] + " " + val) if key in out else val
    return out


# ---------------------------------------------------------------------------
# 规则
# ---------------------------------------------------------------------------

def _finding(fid: str, severity: str, subject: str, evidence: Dict[str, Any], basis: str) -> Dict[str, Any]:
    return {"id": fid, "severity": severity, "subject": subject,
            "evidence": evidence, "basis": basis}


def _unknown_token(tok: str) -> bool:
    return tok.strip("{}").lower() not in INNO_CONSTANTS


def unresolved_tokens(sections: Dict[str, List[Tuple[int, str]]]) -> List[Dict[str, Any]]:
    """规则 R1：残留的、Inno 不认识的 {token}（模板单遍替换未展开的痕迹）。"""
    found: Dict[str, Dict[str, Any]] = {}
    for sec in sorted(sections):
        for ln, stmt in sections[sec]:
            for m in TOKEN_RE.finditer(stmt):
                tok = m.group(1).lower()
                if tok in INNO_CONSTANTS:
                    continue
                if FORM_RE.search(stmt[m.start():m.start() + 14]):
                    continue
                rec = found.setdefault(tok, {"count": 0, "occurrences": [], "sections": set()})
                rec["count"] += 1
                rec["occurrences"].append({"line": ln, "section": sec})
                rec["sections"].add(sec)
    findings = []
    for tok in sorted(found):
        rec = found[tok]
        findings.append(_finding(
            "unresolved_template_constant", "error", "{" + tok + "}",
            {"count": rec["count"],
             "occurrences": sorted(rec["occurrences"], key=lambda r: (r["section"], r["line"])),
             "sections": sorted(rec["sections"])},
            "该花括号名不在 Inno 已知常量集合内，作为字面量留在路径/键名里即无法解析"))
    return findings


def check_files(sections: Dict[str, List[Tuple[int, str]]], payload_root: str) -> List[Dict[str, Any]]:
    """规则 R2/R3：[Files] 每条 Source 与磁盘实况对照（字面存在性 / 通配命中数）。"""
    findings = []
    for ln, stmt in sections.get("Files", []):
        p = split_params(stmt)
        src = p.get("source")
        if not src:
            continue
        flags = p.get("flags", "")
        resolved = src.replace("\\", os.sep)
        full = resolved if os.path.isabs(resolved) else os.path.join(payload_root, resolved)
        wildcard = bool(re.search(r"[*?]", resolved))
        if wildcard:
            hits = _glob_matches(full)
            if not hits and "skipifsourcedoesntexist" not in flags.lower():
                findings.append(_finding(
                    "files_source_wildcard_no_match", "error", src,
                    {"line": ln, "resolved_glob": full, "matches": 0,
                     "parent_dir_exists": _dir_exists(os.path.dirname(full)),
                     "flags": flags},
                    "Inno 对无命中且未加 skipifsourcedoesntexist 的 Source 通配在编译期报错；此处按磁盘实测命中数判定"))
        else:
            if not _path_exists(full):
                findings.append(_finding(
                    "files_source_literal_missing", "error", src,
                    {"line": ln, "resolved_path": full, "exists": False,
                     "payload_root": payload_root},
                    "Source 指向的实际路径不存在（相对脚本目录逐段解析）"))
    return findings


def check_uninstall(sections: Dict[str, List[Tuple[int, str]]]) -> List[Dict[str, Any]]:
    """规则 R4：卸载入口与 Inno 实际生成位置是否一致。

    Inno 把卸载器写在应用目录（DefaultDirName 的解析结果）下、名为 unins000.exe，
    {uninstallexe} 常量即其真实路径；硬写的其它路径必指不到文件。
    """
    sm = setup_map(sections.get("Setup", []))
    ddn = sm.get("defaultdirname", ("", 0))[0]
    findings = []
    for sec in ("Icons", "Run"):
        for ln, stmt in sections.get(sec, []):
            p = split_params(stmt)
            fn = p.get("filename", "")
            name = p.get("name", "")
            low = fn.lower().replace("\\", "/")
            if "uninstall" not in low and "卸载" not in name and "{uninstallexe}" not in fn:
                continue
            if "{uninstallexe}" in fn:
                continue  # 用 Inno 自身常量，恒正确
            if not ddn:
                findings.append(_finding(
                    "uninstall_target_undecidable", "warn", name or fn,
                    {"line": ln, "section": sec, "declared_filename": fn},
                    "无 DefaultDirName 可据以派生卸载器位置，故只示警不判错"))
                continue
            expected = os.path.join(ddn.replace("\\", os.sep), "unins000.exe")
            got = fn.replace("\\", os.sep)
            if got != expected:
                findings.append(_finding(
                    "uninstall_target_mismatch", "error", name or fn,
                    {"line": ln, "section": sec, "declared_filename": fn,
                     "expected_from_defaultdirname": expected},
                    "卸载器实际位于 %s；声明目标与之不符，快捷方式将指向不存在的文件" % expected))
    return findings


def check_payload_dir(sections: Dict[str, List[Tuple[int, str]]]) -> List[Dict[str, Any]]:
    """规则 R5：可执行载荷的 DestDir 是否落在安装根（DefaultDirName）之外。"""
    sm = setup_map(sections.get("Setup", []))
    ddn = sm.get("defaultdirname", ("", 0))[0]
    findings = []
    for ln, stmt in sections.get("Files", []):
        p = split_params(stmt)
        src = p.get("source", "")
        if not src.lower().endswith(".exe"):
            continue
        dest = p.get("destdir", "")
        if not dest or not ddn:
            continue
        if dest.strip("{}").lower() == "app":
            continue          # {app} 就是 DefaultDirName 解析出的安装根，标准写法
        if not _same_place(dest, ddn):
            findings.append(_finding(
                "payload_dir_split", "warn", src,
                {"line": ln, "payload_destdir": dest, "defaultdirname": ddn},
                "可执行载荷与安装根不同处，按 DefaultDirName 定位安装内容的升级/卸载会漏掉它"))
    return findings


def check_appid(sections: Dict[str, List[Tuple[int, str]]]) -> List[Dict[str, Any]]:
    """规则 R6：AppId 形制——两臂独立、各自可证伪。

    臂一（转义，Phase 53 由真 iscc 定论，severity=error）：Inno 在值里把 `{` 起始当常量
    展开，字面 GUID 必须把首花括号双写成 `{{`。发货脚本曾写单括号形制，本机真编译器
    实测 exit=2 并报 "Unknown constant"；双写后同一脚本 exit=0。这是**编译级致命**，
    不是风格问题，故出 error 而非 info。
    臂二（十六进制形制，severity=warn）：Inno 允许任意串作 AppId，非 GUID 形制只影响
    按 GUID 解析的外部工具，不阻断编译，故仍是 warn。
    """
    sm = setup_map(sections.get("Setup", []))
    raw, ln = sm.get("appid", ("", 0))
    if not raw:
        return [_finding("appid_missing", "error", "[Setup] AppId",
                         {"line": ln}, "无 AppId 时 Inno 以安装路径为卸载标识，升级/卸载识别不稳定")]
    findings: List[Dict[str, Any]] = []
    inner = raw.strip("{}")
    is_guid = bool(GUID_RE.match(inner))
    if raw.startswith("{") and not raw.startswith("{{") and is_guid:
        findings.append(_finding(
            "appid_guid_unescaped", "error", raw,
            {"line": ln, "raw_value": raw, "expected_form": "{" + raw,
             "compiler_proven": True,
             "iscc_observed": "单写形制 exit=2 Unknown constant；双写形制 exit=0（Phase 53 本机实测）"},
             "Inno 把开头的单个 { 当常量展开，字面 GUID 必须双写为 {{；此形制编译器直接拒绝，"
             "安装版从未产出（Phase 49 发货脚本即为此缺陷，Phase 53 真编译当场抓出）"))
    if is_guid:
        return findings
    bad = sorted({c for c in inner if c.isalpha() and c.upper() not in "ABCDEF"})
    findings.append(_finding(
        "appid_not_uuid", "warn", raw,
        {"line": ln, "non_hex_letters": bad, "length": len(inner),
         "escaped": raw.startswith("{{")},
        "Inno 允许任意 AppId 串，但非 GUID 形制会让按 GUID 解析的卸载/清点工具失配"))
    return findings


def check_dirs(sections: Dict[str, List[Tuple[int, str]]], data_root: str) -> List[Dict[str, Any]]:
    """规则 R7：声明了 [Dirs] 时，预建目录是否与运行时真实数据根有交集。"""
    declared = []
    for ln, stmt in sections.get("Dirs", []):
        p = split_params(stmt)
        n = p.get("name", "")
        if n:
            declared.append({"line": ln, "name": n})
    if not declared or not data_root:
        return []
    norm_root = os.path.normcase(os.path.abspath(data_root))
    covering = []
    for d in declared:
        resolved = os.path.normcase(os.path.abspath(_resolve_inno_path(d["name"])))
        if (norm_root == resolved
                or resolved.startswith(norm_root + os.sep)
                or norm_root.startswith(resolved + os.sep)):
            covering.append(dict(d, resolved=resolved))
    if covering:
        return []
    return [_finding("dirs_miss_runtime_data_root", "warn", "[Dirs]",
                    {"declared": declared,
                     "declared_resolved": [_resolve_inno_path(d["name"]) for d in declared],
                     "runtime_data_root": data_root,
                     "covering_entries": covering},
                    "安装包预建目录与运行时数据根无交集，首写将由服务自建，预建项形同虚设")]


def check_registry_values(sections: Dict[str, List[Tuple[int, str]]]) -> List[Dict[str, Any]]:
    """规则 R8：[Registry] 的 ValueData 是否残留占位字面量（装完即写入垃圾值）。"""
    findings = []
    for ln, stmt in sections.get("Registry", []):
        p = split_params(stmt)
        vd = p.get("valuedata", "")
        unknown = [m.group(0) for m in TOKEN_RE.finditer(vd) if _unknown_token(m.group(0))
                   and not FORM_RE.search(vd[m.start():m.start() + 14])]
        if unknown:
            findings.append(_finding(
                "registry_valuedata_literal_placeholder", "error", p.get("subkey", "?"),
                {"line": ln, "value_name": p.get("valuename", ""), "value_data": vd,
                 "unknown_tokens": sorted(set(unknown))},
                "注册表值里留下的花括号字面量会原样写入，属可见的数据污染"))
    return findings


def _reg_view(root: str) -> str:
    """把 Inno 的 Root 写法归一成"同一个真实键视图"的比较用形态。

    去掉 `32`/`64` 后缀、大小写与空格无关：`HKCU` 与 `HKCU64` 是同一处键的两种拼写，
    若因拼写不同就判"无人撤销"，规则会对合规脚本开火（假红）。而跨 base root 的声明
    （写在 HKCU、撤销挂在 HKLM）确实是两把不同的钥匙，必须照报——臂六钉的就是这一条。
    原始写法仍随证据回显，归一绝不能把"拼写不一致"这件事本身抹掉。
    """
    return re.sub(r"(32|64)$", "", (root or "").strip().lower())


def _reg_key(subkey: str) -> str:
    """键路径归一：分隔符与大小写不参与比较（注册表键名大小写不敏感），首尾斜杠剥掉。"""
    return (subkey or "").replace("/", "\\").strip().strip("\\").lower()


# 卸载期"撤销写入"只有三种真语义：整键删（连同子键）、仅空时删、删单枚值。
# 其中 uninsdeletekeyifempty **不能**撤销一枚值——值仍在即键永不为空，故不入 accepted。
REG_CLEANUP_FLAGS = ("uninsdeletekey", "uninsdeletekeyifempty", "uninsdeletevalue",
                     "uninsneveruninstall")
REG_ACCEPTED_CLEANUP_FLAGS = ("uninsdeletekey", "uninsdeletevalue")
# ValueType 以 delete/none 开头的条目本身就是"删除声明"，不是写入，不能当被检查的写入项。
REG_NON_WRITE_VALUE_TYPES = ("none", "deletekey", "deletekeyifempty", "deletetable",
                             "deletevalue")


def check_registry_uninstall_cleanup(sections: Dict[str, List[Tuple[int, str]]]) -> List[Dict[str, Any]]:
    """规则 R14：写入型 [Registry] 条目必须在卸载时显式撤销，否则值原样留守（Phase 58 实测）。

    立项依据不是文档而是本机真卸载：unins000.exe 以 0 退出、安装根与卸载子键双双消失、
    四棵目录树归零、既有数据逐字节未动，**而应用键的三枚值原样留守**，执行器据此判
    uninstall_incomplete 并轮询满 180 s。Inno 的 [Registry] 默认只写不删，撤销须由旗标
    显式声明——"装得干净"与"卸得干净"是两条互不通用的判据，此前十条静态规则全盲于后者。

    判定面（每个 Root+键路径 一组，只查写入项）：
    - 同键上出现 uninsdeletekey 或 uninsdeletevalue → 覆盖；
    - **祖先**键上出现 uninsdeletekey（Inno 连同子键一并删）→ 覆盖；
    - 祖先键只有 uninsdeletekeyifempty → 不覆盖（子键带着值，父键永不为空，撤销永不发生）；
    - 同键只有 uninsdeletekeyifempty → 同样不覆盖（值从未被声明删除，键非空）；
    - 条目声明 uninsneveruninstall → 有意的归属让渡（共享键），不报；
    - 撤销声明挂在另一个 base root → 不覆盖。
    """
    entries = []
    for ln, stmt in sections.get("Registry", []):
        p = split_params(stmt)
        flags = [f.strip().lower() for f in re.split(r"[\s,]+", p.get("flags", "")) if f.strip()]
        vtype = p.get("valuetype", "").strip().lower()
        has_value = bool(p.get("valuename", "").strip())
        writes = (has_value and vtype not in REG_NON_WRITE_VALUE_TYPES
                  and (bool(p.get("valuedata")) or bool(p.get("valuebitmap"))
                       or bool(vtype) or "createvalueifdoesntexist" in flags))
        entries.append({"line": ln, "view": _reg_view(p.get("root", "")),
                        "root_raw": p.get("root", "").strip(),
                        "key": _reg_key(p.get("subkey", "")),
                        "subkey_raw": p.get("subkey", "").strip(),
                        "flags": flags, "writes": writes,
                        "value_name": p.get("valuename", "").strip()})
    findings = []
    groups: Dict[Tuple[str, str], Dict[str, Any]] = {}
    order: List[Tuple[str, str]] = []
    for w in entries:
        if not w["writes"] or "uninsneveruninstall" in w["flags"]:
            continue
        gk = (w["view"], w["key"])
        if gk not in groups:
            groups[gk] = {"root_raw": w["root_raw"], "subkey_raw": w["subkey_raw"],
                          "lines": [], "value_names": [], "observed": [], "covered": False}
            order.append(gk)
        g = groups[gk]
        g["lines"].append(w["line"])
        if w["value_name"]:
            g["value_names"].append(w["value_name"])
        for e in entries:
            if e["view"] != w["view"]:
                continue
            same = e["key"] == w["key"]
            child = bool(e["key"]) and w["key"].startswith(e["key"] + "\\")
            if not (same or child):
                continue
            seen = [f for f in e["flags"] if f in REG_CLEANUP_FLAGS
                    and f != "uninsneveruninstall" and f not in g["observed"]]
            g["observed"] += seen
            if ("uninsdeletekey" in e["flags"] and (same or child)) or \
               (same and "uninsdeletevalue" in e["flags"]):
                g["covered"] = True
    for gk in order:
        g = groups[gk]
        if g["covered"]:
            continue
        findings.append(_finding(
            "registry_key_left_after_uninstall", "error",
            "%s\\%s" % ((g["root_raw"] or "?").upper(), g["subkey_raw"]),
            {"root": gk[0], "subkey": g["subkey_raw"], "line": g["lines"][0],
             "write_lines": sorted(g["lines"]),
             "value_names": sorted(set(g["value_names"])),
             "observed_cleanup_flags": sorted(g["observed"]),
             "accepted_flags": sorted(REG_ACCEPTED_CLEANUP_FLAGS),
             "live_measurement": "Phase 58 本机真卸载：三枚值留守、卸载器 exit 0、其余全树归零"},
            "写入型 [Registry] 条目没有配套的卸载撤销声明（同键 uninsdeletekey/uninsdeletevalue，"
            "或祖先键 uninsdeletekey），Inno 默认只写不删，卸载后该键值原样留在注册表里"))
    return findings


def check_permission_entries(sections: Dict[str, List[Tuple[int, str]]]) -> List[Dict[str, Any]]:
    """规则 R10：Permissions 项须为 Inno 的 `<组>-<级别>` 形态（或独立关键字）。

    这里只校验**形态**不校验词表——合法组名/级别名的最终定论权在 iscc，本引擎若无
    离线词表权威却去逐词判定，只会制造误报。缺连字符的写法（如 usersfull）必不合规，
    据此抓得住真错、不冤枉合规写法。
    """
    findings = []
    for sec in ("Dirs", "Files", "Registry", "Icons"):
        for ln, stmt in sections.get(sec, []):
            p = split_params(stmt)
            perm = p.get("permissions", "")
            if not perm:
                continue
            for tok in perm.split():
                low = tok.lower()
                if "-" in low or low in ("inherit", "ignoreerror"):
                    continue
                findings.append(_finding(
                    "permissions_entry_malformed", "error", "%s: %s" % (sec, tok),
                    {"line": ln, "section": sec, "entry": tok,
                     "full_value": perm,
                     "expected_shape": "<group>-<level>，例如 users-modify"},
                     "Permissions 项缺 <组>-<级别> 的连字符形态，Inno 无法识别该权限入口"))
    return findings


def per_user_areas(sections: Dict[str, List[Tuple[int, str]]]) -> Tuple[List[str], Dict[str, List[Dict[str, Any]]]]:
    """脚本真实触及的 per-user 区域：返回（区名有序表, 区名→出处行）二元组，供 R11 判配对。

    两类证据：[Registry] 的 `Root: HKCU`（含 HKCU64 之类带后缀写法），以及任何段里出现的
    per-user 形制常量。`{group}`/`{autopf}`/`{autoprograms}` 一类 auto 常量按安装模式
    解析、本身不构成 per-user 断言，故**不**入集合——否则每一条快捷方式都会被误报。
    注释行已在 parse_script 阶段剔除，评论里提到 {localappdata} 不会污染判定。
    """
    hits: Dict[str, List[Dict[str, Any]]] = {}
    for sec in sorted(sections):
        for ln, stmt in sections[sec]:
            for m in TOKEN_RE.finditer(stmt):
                name = m.group(1).lower()
                if name in PER_USER_AREA_CONSTANTS:
                    hits.setdefault(name, []).append({"line": ln, "section": sec})
            if sec == "Registry":
                root = split_params(stmt).get("root", "")
                if root.lower().startswith("hkcu"):
                    hits.setdefault("HKCU", []).append({"line": ln, "section": sec})
    return sorted(hits, key=str.lower), hits


def check_privileges_areas(sections: Dict[str, List[Tuple[int, str]]]) -> List[Dict[str, Any]]:
    """规则 R11：per-user 区域与 PrivilegesRequired 的配对（Phase 53 由真 iscc 定论）。

    本机首次真编译 exit=0，但 iscc 在 stdout 里另报一行 Warning：PrivilegesRequired 缺省
    即 admin，而脚本触及 (HKCU,localappdata,userdocs)。这不是噪音——以管理员安装模式运行
    时，写 HKCU/per-user 目录落到的是**提权那个账户**的配置区，普通用户登录后看不到，
    表现为"装完了却找不到数据根"。因此判据是配对：只有 per-user 区域**且**生效装模式为
    admin（显式写 admin 或缺省）时才出 warn；补齐 PrivilegesRequired=lowest 即消解。
    severity 取 warn 与编译器一致（iscc 只告警、不拒绝编译）。
    """
    areas, sites = per_user_areas(sections)
    if not areas:
        return []
    priv = effective_privileges_required(sections)
    if not priv["requires_elevation"]:
        return []
    return [_finding(
        "per_user_area_privileges_mismatch", "warn", "[Setup] PrivilegesRequired",
        {"line": priv["declared_line"],
         "privileges_required_declared": priv["declared"],
         "privileges_required_effective": priv["effective"],
         "default_when_absent": priv["default_when_absent"],
         "per_user_areas_observed": areas,
         "per_user_area_sites": [{"area": a, "sites": sites[a]} for a in areas],
         "compiler_proven": True,
         "iscc_observed": ISCC_PRIVILEGES_WARNING},
        "生效安装模式为 admin 却触及 per-user 区域，提权运行时这些改动落在提权账户而非目标用户；"
        "本机真编译器原样告警（见 iscc_observed）")]


def check_arch_identifiers(sections: Dict[str, List[Tuple[int, str]]]) -> List[Dict[str, Any]]:
    """规则 R12：Architectures* 里的弃用架构标识（Phase 53 由真 iscc 定论）。

    同一次真编译的另一行 Warning：`x64` 已弃用，iscc 自行替换为 `x64os`，并说明多数场景
    应写 `x64compatible`。语义差别是真实的：`x64` 只匹配纯 64 位 Windows，`x64compatible`
    还含 onecore/ARM64 仿真等"兼容 x64"的形态。判据按**整词**比较（逗号拆分后逐 token），
    绝不用子串——否则 `x64os`、`x64compatible` 会被一并冤枉成弃用写法。
    """
    entries = sections.get("Setup", [])
    sm = setup_map(entries)
    findings = []
    for key in ("architecturesallowed", "architecturesinstallin64bitmode"):
        if key not in sm:
            continue
        raw, ln = sm[key]
        tokens = [t.strip() for t in raw.split(",") if t.strip()]
        offending = [t for t in tokens if t.lower() in DEPRECATED_ARCH_IDENTIFIERS]
        if not offending:
            continue
        # 回显脚本里的原文拼写（setup_map 只把键归一为小写，原文另从语句行取）
        subject = next((s.split("=", 1)[0].strip() for _, s in entries
                        if "=" in s and s.split("=", 1)[0].strip().lower() == key), key)
        findings.append(_finding(
            "arch_identifier_deprecated", "warn", subject,
            {"line": ln, "directive": key, "declared_value": raw,
             "tokens": tokens, "offending_tokens": offending,
             "preferred": DEPRECATED_ARCH_IDENTIFIERS[offending[0].lower()],
             "compiler_proven": True,
             "iscc_observed": ISCC_ARCH_WARNING},
            "弃用的架构标识由编译器自行替换，替换结果并非脚本作者的意图表达；"
            "本机真编译器原样告警（见 iscc_observed）"))
    return findings


def check_version_directive(sections: Dict[str, List[Tuple[int, str]]]) -> List[Dict[str, Any]]:
    """规则 R13：[Setup] 缺 AppVersion 与 AppVerName（Phase 53 由真 iscc 定论，severity=error）。

    同一枚探针的第三段实证：把 AppVersion 行删掉后 iscc 直接 **exit=2** 并报
    "The [Setup] section must include an AppVersion or AppVerName directive." —— 这是编译级
    致命，不是告警。R9 只能对"编译器才会说话的项"出 info，而这一项编译器给出了确定性判词，
    所以它属 error：脚本一旦缺这条形制，安装版**永远编译不出来**，而静态面此前会判 manifest_ok。
    两臂合一：AppVersion 或 AppVerName 任一在场即合规（编译器用的是"or"）。
    """
    sm = setup_map(sections.get("Setup", []))
    present = {k: sm[k] for k in ("appversion", "appvername") if k in sm}
    if present:
        return []
    return [_finding(
        "setup_missing_version_directive", "error", "[Setup] AppVersion/AppVerName",
        {"appversion_present": False, "appvername_present": False,
         "either_suffices": True,
         "compiler_proven": True, "iscc_exit_code_observed": 2,
         "iscc_observed": ISCC_MISSING_VERSION_ERROR},
        "Inno 要求 [Setup] 至少给出 AppVersion 或 AppVerName 之一，缺两者即编译中止；"
        "本机真编译器实测 exit=2（见 iscc_observed）")]


def check_compiler_dependent(sections: Dict[str, List[Tuple[int, str]]]) -> List[Dict[str, Any]]:
    """规则 R9：只能由 iscc 定论的项如实降级为 info，并回显编译器实测寻径结果。

    iscc_on_path 保留其字面语义（PATH 单路命中与否，Phase 53 前它曾被误当成"装没装"），
    另附 compiler_located / compiler_mode / compiler_path 三项三路寻径实测结果。
    """
    advisories = []
    sm = setup_map(sections.get("Setup", []))
    loc = locate_compiler()
    for key in ("licensefile", "setupiconfile"):
        if key not in sm:
            continue          # 未声明 = 无该项，不存在需要编译器定论的东西
        val, ln = sm[key]
        if val == "":
            advisories.append(_finding(
                "needs_compiler_adjudication", "info", "[Setup] %s=" % key,
                {"line": ln, "value": "",
                 "iscc_on_path": bool(loc["sources"]["path"]["hit"]),
                 "compiler_located": loc["found"],
                 "compiler_mode": loc["lookup_mode"],
                 "compiler_path": loc["path"]},
                "空值语义需 iscc 才能定论，本引擎不据此声称已通过编译校验"))
    return advisories


def _unescape_inno_value(value: str) -> str:
    """Inno 值层的字面花括号转义解码：开头的 `{{` 表示一个字面 `{`。

    只解首处的成对转义——Inno 的展开器从值的起始判定是否常量化，Phase 53 的两条真编译
    （单写 exit=2 / 双写 exit=0）就是这条映射的实测依据。原样返回未转义写法，
    使"键名派生"与"脚本写没写对转义"解耦：写错的脚本由 R6 报 error，键名照样能算。
    """
    v = str(value).strip()
    return "{" + v[2:] if v.startswith("{{") else v


def uninstall_registry_key(app_id: str) -> str:
    """Inno 自动登记的卸载键名（只读探测据此查找，绝不改写）。

    规则：键名 = `<生效 AppId>_is1`，其中生效 AppId 由 _unescape_inno_value 解出。括号
    只有在 AppId 本身以 `{` 起始时才保留——本机 HKLM 32 位视图里实测存在的 `Inno Setup 6_is1`
    （Inno 自家安装项，AppId 是无括号的 "Inno Setup 6"）就是这条规则的反证：旧实现无条件
    补括号，遇到无括号 AppId 永远查不中。

    入参可以是脚本里的原样写法（`{{GUID}`）或已解码的生效值（`{GUID}`），两者必须派生出
    同一个 `{GUID}_is1`：Phase 53 把 AppId 改成双写形制时，若键名派生随转义状态漂移，
    只读安装态探测就会在真机上永远查不中——故此处显式解码而非依赖 strip 的巧合。
    """
    eff = _unescape_inno_value(app_id)
    return "%s_is1" % eff if eff.startswith("{") else "%s_is1" % eff


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

def verify_script(text: str, payload_root: str, data_root: str = "") -> Dict[str, Any]:
    """静态校验主入口：纯只读、逐项复算、绝不落盘。"""
    if not isinstance(text, str) or not text.strip():
        raise ManifestError("脚本内容为空或非文本")
    if not isinstance(payload_root, str) or not payload_root.strip():
        raise ManifestError("payload_root 必须是非空路径")
    sections = parse_script(text)
    findings: List[Dict[str, Any]] = []
    findings += unresolved_tokens(sections)
    findings += check_files(sections, payload_root)
    findings += check_uninstall(sections)
    findings += check_payload_dir(sections)
    findings += check_appid(sections)
    findings += check_dirs(sections, data_root)
    findings += check_registry_values(sections)
    findings += check_registry_uninstall_cleanup(sections)
    findings += check_permission_entries(sections)
    findings += check_privileges_areas(sections)
    findings += check_arch_identifiers(sections)
    findings += check_version_directive(sections)
    findings += check_compiler_dependent(sections)
    findings.sort(key=lambda f: (f["id"], str(f["subject"]), str(f["evidence"].get("line", 0))))
    sev = {"error": 0, "warn": 0, "info": 0}
    for f in findings:
        sev[f["severity"]] += 1
    sm = setup_map(sections.get("Setup", []))
    appid = sm.get("appid", ("", 0))[0]
    return {
        "schema": SCHEMA,
        "sections_parsed": sorted(sections),
        "entries_by_section": {k: len(v) for k, v in sorted(sections.items())},
        "app_id": appid,
        "app_id_effective": _unescape_inno_value(appid) if appid else "",
        "uninstall_registry_key": uninstall_registry_key(appid) if appid else "",
        "default_dir_name": sm.get("defaultdirname", ("", 0))[0],
        "app_name": sm.get("appname", ("", 0))[0],
        "payload_root": payload_root,
        "runtime_data_root": data_root,
        "findings": findings,
        "counts": {"errors": sev["error"], "warnings": sev["warn"], "infos": sev["info"],
                   "total": len(findings)},
        "verdict": ("manifest_invalid" if sev["error"]
                    else ("manifest_warning" if sev["warn"] else "manifest_ok")),
        "execution": dict(EXECUTION_BOUNDARY),
        "fabricated": False,
    }


def install_state(app_id: str, app_name: str, extra_roots: Optional[List[str]] = None) -> Dict[str, Any]:
    """只读探测本机是否已安装：注册表卸载项 / DisplayName 兜底 / 开始菜单组 /
    应用数据根 / 便携标记。绝不写入任何东西；installed 完全由逐项真实观察派生。"""
    if not isinstance(app_id, str) or not isinstance(app_name, str):
        raise ManifestError("app_id/app_name 必须是字符串")
    probes: List[Dict[str, Any]] = []
    derived = uninstall_registry_key(app_id) if app_id else ""
    registry_hit = False
    # root × 视图四路枚举：64 位 Python 默认只看 64 位视图，而 Inno 默认把 32 位安装项
    # 落在 WOW6432Node。只查 64 位视图时"已安装"会被判成"未安装"——与 PATH 单路判编译器
    # 未安装同一类仪器缺陷，故此处把视图显式化并逐路回显（Phase 53）。
    #
    # 精确查找那一趟**不许被任何短路打断**：它回显的四枚 probe 是"四路都真去看过了"的凭证，
    # 一旦命中就 break，凭证数量就随本机安装态漂移（Phase 58 本机真装当轮正是这样把
    # `len(reg_probes) == 4` 打成 1 的）。兜底扫描改到精确趟跑完之后再单独进行。
    routes = (("HKCU", "64"), ("HKCU", "32"), ("HKLM", "64"), ("HKLM", "32"))
    seen: Dict[Any, List[str]] = {}
    for root, view in routes:
        keys = _enum_uninstall_keys(root, view)
        seen[(root, view)] = keys
        present = bool(derived) and derived in keys
        obs = {"probe": "registry_uninstall", "root": root, "view": view,
               "subkey_count": len(keys), "looking_for": derived, "present": present}
        if present:
            obs["display_name"] = _read_uninstall_value(root, derived, "DisplayName", view)
            obs["display_version"] = _read_uninstall_value(root, derived, "DisplayVersion", view)
            obs["uninstall_string"] = _read_uninstall_value(root, derived, "UninstallString", view)
            registry_hit = True
        probes.append(obs)
    if not registry_hit:
        # DisplayName 兜底：只在精确查找四路全落空后才扫（本机未声明 UninstallDisplayName 时
        # 显示名是 AppName+" "+AppVersion，见 display_name_matches 的实测依据）。命中即停，
        # 因为这里要的是"是否存在这样一枚卸载项"，多枚同名不改变结论。
        for root, view in routes:
            for k in seen[(root, view)]:
                if k == derived:
                    continue
                dn = _read_uninstall_value(root, k, "DisplayName", view)
                if display_name_matches(dn or "", app_name):
                    probes.append({"probe": "registry_display_name", "root": root, "view": view,
                                   "subkey": k, "display_name": dn,
                                   "expected_rule": "display_name_startswith_app_name",
                                   "install_location": _read_uninstall_value(
                                       root, k, "InstallLocation", view),
                                   "uninstall_string": _read_uninstall_value(
                                       root, k, "UninstallString", view)})
                    registry_hit = True
                    break
            if registry_hit:
                break
    appdata = os.environ.get("APPDATA", "")
    if appdata:
        grp = os.path.join(appdata, "Microsoft", "Windows", "Start Menu", "Programs", app_name)
        probes.append({"probe": "start_menu_group", "path": grp, "exists": _dir_exists(grp)})
    la = os.environ.get("LOCALAPPDATA", "")
    if la:
        dr = os.path.join(la, app_name)
        probes.append({"probe": "app_data_root", "path": dr, "exists": _dir_exists(dr)})
    for extra in (extra_roots or []):
        if not isinstance(extra, str) or not extra.strip():
            raise ManifestError("extra_roots 每项必须是非空字符串")
        flag = os.path.join(extra, "portable.flag")
        probes.append({"probe": "portable_flag", "path": flag, "exists": _path_exists(flag)})
    return {
        "schema": SCHEMA,
        "environment": "windows" if WINDOWS else "non_windows",
        "app_id": app_id,
        "app_name": app_name,
        "derived_uninstall_key": derived,
        "probes": probes,
        "installed": bool(registry_hit),
        "installed_basis": "installed 仅由注册表卸载项真实命中派生；目录/标记存在只作辅助观察，未见即不声称已安装",
        "execution": dict(EXECUTION_BOUNDARY),
        "fabricated": False,
    }


# ---------------------------------------------------------------------------
# 编译面（Phase 53 红线分裂之 installer_artifact_build）
# ---------------------------------------------------------------------------
# 这一面对的回答只有一个问题：**"这个安装包是不是由这条构建链、从当前这份脚本真编译出来的"**。
# 它不跑安装器、不写注册表、不建快捷方式（那属 installer_execution 的 CLI 执行器，
# Phase 58 起随包交付但入口只有 install-exec），
# 而是把构建侧留下的证据（installer/build-evidence.json）逐字段拿**磁盘重算值**对账：
# 脚本与产物的字节数、sha256、退出码、输出尾部里"Successful compile"那行原文、告警计数。
#
# 为什么必须是"对账"而不是"读证据"：本项目连续约三十轮把"iscc 缺、编译不可能"当成环境事实
# 写进报告与发货文档，根因是**自报字段/探针伪影被当成了事实**（PATH 单路 which 未命中即答
# False）。编译面因此刻意无视证据里的 compiled / fabricated 两个布尔——它们照样回显，
# 只是不作数；判定只认三件互相独立的信号同立（退出码 0、输出里真见过完成行、产物在盘）。
BUILD_EVIDENCE_SCHEMA = "zaojing-installer-build/v1"
# 旧证据（首次编译那轮）用的字段名是 iscc_stderr_tail，而 iscc 的告警走的是 stdout——
# 字段名写错一次，"零告警"就被当成"编译器没说话"。两键都容忍，并把实际取用的键名回显。
OUTPUT_TAIL_KEYS = ("iscc_output_tail", "iscc_stderr_tail")
COMPILE_LINE_PREFIX = "Successful compile"
# 与 build-installer.py 收集告警的口径一致：按行首词计，不做子串匹配（否则产物路径里
# 出现 "error" 之类字样就会把计数抬高，把合规证据判成矛盾）。
WARNING_LINE_PREFIXES = ("Warning", "Error", "Deprecated")
# 缺任何一项都无法完成对账（不是"少记了就宽容"——少记 sha 就等于让等长替换蒙混过关）。
BUILD_REQUIRED_FIELDS = ("script_path", "script_bytes", "script_sha256",
                         "artifact_path", "artifact_bytes", "artifact_sha256",
                         "iscc_exit_code", "compiler_path")

# 安装版数据根名（与 agent_service.INSTALLED_DATA_DIR_NAME 同形制，独立声明以免互相引用成环）
INSTALLED_DATA_DIR_NAME = "Zaojing"


def _installed_data_root() -> str:
    """编译面复校 [Dirs] 用的数据根接缝：由 LOCALAPPDATA 派生安装版数据根。

    环境接缝不可解析时返回空串，verify_script 据此如实**跳过** R7（跳过 ≠ 通过），
    调用方也可显式传入 data_root 覆盖（评测台/便携形态下口径不同）。
    """
    local = _env_dir("LOCALAPPDATA")
    return os.path.join(local, INSTALLED_DATA_DIR_NAME) if local else ""


def _stat_and_sha256(path: str) -> Tuple[int, str]:
    """读磁盘真值：字节数 + sha256。二者都要，因为只比长度会漏掉等长原地替换。"""
    data = Path(path).read_bytes()
    return len(data), hashlib.sha256(data).hexdigest()


def _sha(value: Any) -> str:
    return str(value or "").strip().lower()


def _warning_lines_in_tail(tail: str) -> List[str]:
    return [ln for ln in str(tail).splitlines()
            if ln.strip().startswith(WARNING_LINE_PREFIXES)]


def _read_build_evidence(evidence: Any) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """接受 dict（内联）或路径字符串/Path（文件）。返回 (证据, 证据来源说明)。"""
    if isinstance(evidence, dict):
        return evidence, {"from": "inline", "path": None,
                         "basis": "调用方内联传入的证据字典（构建进程内存态）"}
    if isinstance(evidence, (str, Path)):
        p = Path(evidence)
        try:
            raw = p.read_bytes().decode("utf-8")
        except OSError as e:
            raise ManifestError("构建证据不可读：%s（%s: %s）" % (p, type(e).__name__, e))
        except UnicodeDecodeError as e:
            raise ManifestError("构建证据非 UTF-8：%s" % p)
        try:
            data = json.loads(raw)
        except ValueError as e:
            raise ManifestError("构建证据非合法 JSON：%s（%s）" % (p, e))
        if not isinstance(data, dict):
            raise ManifestError("构建证据顶层须为对象：%s（实为 %s）" % (p, type(data).__name__))
        return data, {"from": "file", "path": str(p),
                      "bytes": p.stat().st_size,
                      "basis": "证据取自磁盘文件，其内部路径字段仍逐项与磁盘重算值对账"}
    raise ManifestError("构建证据须为 dict 或路径，实为 %s" % type(evidence).__name__)


def verify_build_evidence(evidence: Any, script_path: Optional[str] = None,
                          artifact_path: Optional[str] = None,
                          data_root: Optional[str] = None) -> Dict[str, Any]:
    """编译面：把构建证据逐字段与磁盘实况对账，判"产物是否被证成"。

    verdict ∈ {artifact_verified, evidence_mismatch, artifact_missing,
               compile_not_proven, evidence_incomplete}，优先级（先窄后宽，越靠前越具体）：
    缺字段 → evidence_incomplete；产物不在盘 → artifact_missing；三独立信号不同立 →
    compile_not_proven；对账有任一不符 → evidence_mismatch；否则 artifact_verified。

    要点：
    - 证据自报的 compiled / fabricated **不参与判定**，只在 evidence_self_report 里回显
      并标 ignored=True（自报字段作数是本轮要根除的缺陷类）。
    - 产物与脚本各自都比**字节数 + sha256** 两路：等长替换只有 sha 能发现；
      只比"证据 vs 产物"抓不到"编译后又改了脚本"的陈旧态，故脚本侧单独比一遍，
      不符时报 artifact_not_from_current_script（产物与证据彼此仍相符也算不符）。
    - 告警计数只在生产者如实记录了输出总长（尾部即全文）时要求相等；尾部是节选时
      按下界判（记的不少于尾部实见不算矛盾，把截断当不一致是另一种假红）。
    - iscc_output_chars / iscc_warnings / iscc_compile_line 属**后加字段**，缺席不判
      evidence_incomplete，只如实标 field_absent 并降低该臂的断言强度。
    - 全程只读：不执行安装器、不写注册表、不建快捷方式；产物在盘 ≠ 已安装，
      这一点由 not_install 字段显式说给下游（执行域自 Phase 58 起另有 CLI 执行器，但那是
      另一条链留下的台账，不是本函数的结论）。
    """
    ev, source = _read_build_evidence(evidence)
    spath = Path(script_path) if script_path else Path(ev.get("script_path") or "")
    apath = Path(artifact_path) if artifact_path else Path(ev.get("artifact_path") or "")
    root = _installed_data_root() if data_root is None else str(data_root)

    missing_fields = [k for k in BUILD_REQUIRED_FIELDS
                      if ev.get(k) in (None, "")] + \
                     ([] if any(ev.get(k) not in (None, "") for k in OUTPUT_TAIL_KEYS)
                      else list(OUTPUT_TAIL_KEYS))
    tail_key = next((k for k in OUTPUT_TAIL_KEYS if ev.get(k) not in (None, "")), None)
    tail = str(ev.get(tail_key)) if tail_key else ""

    exit_code_raw = ev.get("iscc_exit_code")
    exit_zero = (exit_code_raw == 0) if isinstance(exit_code_raw, int) else False

    # 完成行：优先认尾部原文（最强的独立信号），尾部无痕才退回派生字段。
    tail_hit = next((ln for ln in tail.splitlines()
                     if ln.strip().startswith(COMPILE_LINE_PREFIX)), None)
    derived = str(ev.get("iscc_compile_line") or "").strip()
    if tail_hit:
        compile_line_source, compile_line_seen = "tail", True
    elif derived.startswith(COMPILE_LINE_PREFIX):
        compile_line_source, compile_line_seen = "derived_field", True
    else:
        compile_line_source, compile_line_seen = "absent", False

    def _side(path: Path, bkey: str, skey: str) -> Dict[str, Any]:
        present = path.is_file()
        observed: Optional[Dict[str, Any]] = None
        if present:
            try:
                size, digest = _stat_and_sha256(str(path))
                observed = {"bytes": size, "sha256": digest}
            except OSError as e:  # 在盘却读不动：如实报不可观察，不降级成"相符"
                present = False
                observed = None
                path_note = "%s: %s" % (type(e).__name__, e)
            else:
                path_note = None
        else:
            path_note = None
        exp_b, exp_s = ev.get(bkey), _sha(ev.get(skey))
        return {
            "path": str(path) if str(path) else None, "present": present,
            "expected": {"bytes": ev.get(bkey), "sha256": exp_s or None},
            "observed": observed,
            "bytes_match": bool(observed) and observed["bytes"] == exp_b,
            "sha256_match": bool(observed) and observed["sha256"] == exp_s,
            "unreadable_note": path_note,
        }

    script = _side(spath, "script_bytes", "script_sha256")
    artifact = _side(apath, "artifact_bytes", "artifact_sha256")
    artifact_present = bool(artifact["observed"])

    compile_succeeded = bool(exit_zero and compile_line_seen and artifact_present)

    # ---- 告警计数一致性（尾部全文→相等；节选→下界）----
    tail_warnings = _warning_lines_in_tail(tail)
    tail_recount = len(tail_warnings)
    recorded_raw = ev.get("iscc_warnings")
    output_chars = ev.get("iscc_output_chars")
    tail_is_full_output = (output_chars == len(tail)) if isinstance(output_chars, int) else False
    if recorded_raw is None:
        recorded_count, consistent = None, True
        warnings_basis = ("field_absent：证据未记录 iscc_warnings（旧形制），本臂只回显尾部"
                          "实见 %d 行，不作相符/矛盾断言" % tail_recount)
    else:
        recorded_count = len(recorded_raw) if isinstance(recorded_raw, (list, tuple)) else None
        if recorded_count is None:
            consistent, warnings_basis = True, "iscc_warnings 非序列，降级为不参与判定"
        elif tail_is_full_output:
            consistent = recorded_count == tail_recount
            warnings_basis = ("尾部即全文（iscc_output_chars=%s==len(tail)）：要求记录数与"
                              "尾部实见数相等" % output_chars)
        else:
            consistent = recorded_count >= tail_recount
            warnings_basis = ("尾部为节选或未记录总长：按下界判（记录 %s ≥ 尾部实见 %s 即不矛盾），"
                              "把截断当不一致同样是假红" % (recorded_count, tail_recount))

    # ---- 编译后当前脚本的清单静态复校（信息面，不进 blocking）----
    manifest_now: Dict[str, Any]
    if script["present"]:
        try:
            now = verify_script(spath.read_bytes().decode("utf-8"),
                                str(spath.parent), data_root=root)
            manifest_now = {"verdict": now["verdict"], "counts": now["counts"],
                            "finding_ids": [f["id"] for f in now["findings"]],
                            "data_root": root,
                            "same_as_recorded": now["verdict"] == ev.get("manifest_verdict"),
                            "recorded_verdict": ev.get("manifest_verdict")}
        except (ManifestError, UnicodeDecodeError, OSError) as e:
            manifest_now = {"verdict": None, "same_as_recorded": False,
                            "recorded_verdict": ev.get("manifest_verdict"),
                            "basis": "当前脚本无法静态复校：%s: %s" % (type(e).__name__, e)}
    else:
        manifest_now = {"verdict": None, "same_as_recorded": False,
                        "recorded_verdict": ev.get("manifest_verdict"),
                        "basis": "脚本不在盘，无法对当前脚本重跑静态校验"}

    blocking: List[Dict[str, Any]] = []

    def _block(code: str, detail: Dict[str, Any], basis: str) -> None:
        blocking.append({"code": code, "detail": detail, "basis": basis})

    if script["present"] is False:
        _block("script_file_missing", {"path": script["path"],
                                       "unreadable_note": script["unreadable_note"]},
               "证据指向的脚本不在盘：产物无从与当前脚本对上")
    else:
        if not script["sha256_match"] or not script["bytes_match"]:
            _block("artifact_not_from_current_script",
                   {"script_expected": script["expected"], "script_observed": script["observed"],
                    "artifact_sha256_match": artifact["sha256_match"],
                    "note": "产物与证据彼此仍相符，脚本却已变——这正是只比证据/产物会漏掉的陈旧态"},
                   "编译之后脚本又被改动：证据记录的产物不是由当前脚本产出的")
    if artifact_present:
        if not artifact["sha256_match"]:
            _block("artifact_sha256_mismatch",
                   {"expected": artifact["expected"]["sha256"],
                    "observed": artifact["observed"]["sha256"],
                    "bytes_match": artifact["bytes_match"]},
                   "磁盘产物重算 sha256 与证据不符（等长原地替换只有 sha 能发现）")
        if not artifact["bytes_match"]:
            _block("artifact_bytes_mismatch",
                   {"expected": artifact["expected"]["bytes"],
                    "observed": artifact["observed"]["bytes"]},
                   "磁盘产物字节数与证据不符")
    if recorded_count is not None and not consistent:
        _block("warnings_count_mismatch",
               {"recorded_count": recorded_count, "tail_recount": tail_recount,
                "tail_is_full_output": tail_is_full_output,
                "tail_warning_lines": tail_warnings[:5]},
               "编译器在（可判定的）输出里说了话而证据记成更少的告警数")

    if missing_fields:
        verdict = "evidence_incomplete"
    elif not artifact_present:
        verdict = "artifact_missing"
    elif not compile_succeeded:
        verdict = "compile_not_proven"
    elif blocking:
        verdict = "evidence_mismatch"
    else:
        verdict = "artifact_verified"

    compiler_path = str(ev.get("compiler_path") or "")
    compiler_now = locate_compiler()
    return {
        "schema": SCHEMA,
        "face": "installer_artifact_build",
        "verdict": verdict,
        "compile_succeeded": compile_succeeded,
        "signals": {
            "exit_zero": exit_zero, "compile_line_seen": compile_line_seen,
            "artifact_present": artifact_present,
            "compile_line_source": compile_line_source,
            "iscc_exit_code": exit_code_raw,
            "compile_line_text": tail_hit or (derived or None),
            "basis": "编译成功由三独立信号同立：退出码 0 + 输出尾部真见过 %r 行 + 产物在盘；"
                     "证据自报布尔不参与" % COMPILE_LINE_PREFIX,
        },
        "script": script,
        "artifact": artifact,
        "warnings": {
            "recorded_count": recorded_count, "tail_recount": tail_recount,
            "consistent": consistent, "tail_is_full_output": tail_is_full_output,
            "recorded": list(recorded_raw) if isinstance(recorded_raw, (list, tuple)) else None,
            "tail_warning_lines": tail_warnings, "basis": warnings_basis,
        },
        "manifest_now": manifest_now,
        "evidence_self_report": {
            "compiled": ev.get("compiled"), "fabricated": ev.get("fabricated"),
            "ignored": True,
            "reason": "自报字段只回显不参与判定：本轮根除的缺陷正是"
                      "把探针伪影/自报布尔写成环境事实（iscc 在盘三十轮被答'缺'）",
        },
        "evidence_source": dict(source, tail_key=tail_key,
                                schema=ev.get("schema"),
                                schema_match=ev.get("schema") == BUILD_EVIDENCE_SCHEMA,
                                built_at=ev.get("built_at")),
        "output_tail": {"key": tail_key, "chars": len(tail),
                        "recorded_total_chars": output_chars,
                        "provenance": ("iscc_output_tail（stdout 全文尾部）"
                                       if tail_key == "iscc_output_tail" else
                                       "iscc_stderr_tail（旧形制，告警其实走 stdout，"
                                       "该尾部不能当作'编译器没说话'的证据）"
                                       if tail_key == "iscc_stderr_tail" else
                                       "无输出尾部字段（field_absent）：既不能说编译器说过话，"
                                       "也不能说它没说话，编译完成行退回派生字段口径")},
        "compiler": {"recorded_path": compiler_path,
                     "recorded_on_disk": bool(compiler_path) and Path(compiler_path).is_file(),
                     "now_located": {"found": compiler_now["found"],
                                     "mode": compiler_now["lookup_mode"],
                                     "path": compiler_now.get("path")}},
        "blocking": blocking,
        "blocking_codes": [b["code"] for b in blocking],
        "missing_fields": missing_fields,
        "not_install": {
            "installed_claimed": False,
            "artifact_present": artifact_present,
            "basis": "编译产物在盘只证成「由构建链产出」，不证成「本机已安装」：安装/卸载执行域"
                     "（静默安装、写注册表、建快捷方式、跑卸载器）自 Phase 58 起由 CLI 执行器"
                     "（install-exec，显式同意令牌）承担，其回执是另一条链上的 execution-evidence 台账；"
                     "本函数零子进程、零写入，故 installed_claimed 恒 False",
        },
        "fabricated": False,
        "fabricated_basis": "所有数值均为本次对磁盘实读重算所得；证据缺失即标 field_absent/"
                            "missing_fields，绝不用占位数字补齐",
    }


# ---------------------------------------------------------------------------
# 预检面（Phase 53 红线分裂之 installer_preflight）
# ---------------------------------------------------------------------------
# 预检回答"若现在按这份脚本装，本机条件够不够"。它**不是**安装的 miniature：
# 零注册表写入、零子进程、对真实安装根零落盘（写探针只在调用方显式交出的
# target_override 目录里跑，且探针文件用完即删）。
# 三条形制直接来自本轮实测，不是转述：
#   · 令牌读取必须显式声明 ctypes 原型，否则 GetTokenInformation 以 err=24
#     (ERROR_BAD_LENGTH) 失败——那是仪器伪影，把它写成"未提权"就是造假象（p53_token_probe.py）；
#   · 独占打开（CreateFileW share=0）：空闲→err0、被持有→err32 (ERROR_SHARING_VIOLATION)、
#     不存在→err2 (ERROR_FILE_NOT_FOUND)（p53_preflight_probe.py V3 四臂）；
#   · {autopf} 的落点取决于运行时装模式，本机 FOLDERID_UserProgramFiles 实测 hr=0x80070002
#     不可解析，故候选必须报两条并标各自的取得方式（p53_folder_probe.py）。
CSIDL_PROGRAM_FILES = 0x26
CSIDL_PROGRAM_FILESX86 = 0x2A
CSIDL_LOCAL_APPDATA = 0x1C
USER_PROGRAM_FILES_FOLDERID = "d34a6e12-eb69-4855-8e9b-2b0515467c7b"
# 官方文本照抄（jrsoftware.org/ishelp/topic_consts.htm，2026-09-29 抓取），
# 用来解释"为什么 auto 常量必须报两个候选而不是一个"。
AUTO_CONSTANTS_DOC = ("Auto Constants ... automatically map to their 'common' form unless the "
                      "installation is running in non administrative install mode, in which case "
                      "they map to their 'user' form  [表行 autopf | commonpf | userpf]")
# auto 常量 → (common 形式, user 形式)。只有 autopf 的两条落点在本机实测过；
# 其余 auto 常量如实落到 unresolved，不做未实测的外推。
AUTO_CONSTANT_FORMS = {
    "autopf": ("commonpf", "userpf"),
    "autoprograms": ("commonprograms", "userprograms"),
    "autodesktop": ("commondesktop", "userdesktop"),
    "autodocs": ("commondocs", "userdocs"),
}
# 写探针用的探针文件名（用完即删，绝不留在目标目录里）
PROBE_FILE_NAME = "zj-preflight-probe.bin"


def _token_elevation() -> Dict[str, Any]:
    """提权状态接缝：返回 available / elevated / elevation_type / is_admin_account / reason。

    读不到一律 available=None（"未探测"），绝不写成 False（"未提权"）——预检里
    False 会直接阻断，把仪器故障渲染成权限不足是双重错误。
    """
    out: Dict[str, Any] = {"available": None, "elevated": None, "elevation_type": None,
                           "is_admin_account": None, "reason": None}
    if not WINDOWS:
        out["reason"] = "非 Windows：令牌形制不适用"
        return out
    try:
        import ctypes
        import ctypes.wintypes as wt
        k32 = ctypes.windll.kernel32
        a32 = ctypes.windll.advapi32
        k32.GetCurrentProcess.restype = wt.HANDLE
        k32.GetCurrentProcess.argtypes = []
        k32.GetLastError.restype = wt.DWORD
        k32.SetLastError.argtypes = [wt.DWORD]
        k32.CloseHandle.restype = wt.BOOL
        a32.OpenProcessToken.restype = wt.BOOL
        a32.OpenProcessToken.argtypes = [wt.HANDLE, wt.DWORD, ctypes.POINTER(wt.HANDLE)]
        a32.GetTokenInformation.restype = wt.BOOL
        a32.GetTokenInformation.argtypes = [wt.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                            wt.DWORD, ctypes.POINTER(wt.DWORD)]
        tok = wt.HANDLE()
        if not a32.OpenProcessToken(k32.GetCurrentProcess(), 0x0008, ctypes.byref(tok)):
            out["reason"] = "OpenProcessToken 失败 err=%d" % k32.GetLastError()
            return out

        def _read(cls: int):
            k32.SetLastError(0)
            size = wt.DWORD(0)
            a32.GetTokenInformation(tok, cls, None, 0, ctypes.byref(size))  # 先问长度
            buf = ctypes.create_string_buffer(size.value or 8)
            if not a32.GetTokenInformation(tok, cls, buf, len(buf), ctypes.byref(size)):
                return None, k32.GetLastError()
            return int(ctypes.cast(buf, ctypes.POINTER(wt.DWORD)).contents.value), 0

        elev, e1 = _read(20)      # TokenElevation（TOKEN_ELEVATION.TokenIsElevated）
        etype, e2 = _read(18)     # TokenElevationType（1 Default / 2 ElevatedFilter / 3 Full）
        k32.CloseHandle(tok)
        if elev is None:
            out["reason"] = "GetTokenInformation(20) 失败 err=%d" % e1
            return out
        out.update({"available": True, "elevated": bool(elev),
                    "elevation_type": etype, "token_elevation_err": e1,
                    "elevation_type_err": e2,
                    "basis": "ctypes 显式原型下实测：不声明 restype/argtypes 时同一路读数以 "
                             "err=24(ERROR_BAD_LENGTH) 失败，属仪器伪影而非环境事实"})
        try:
            ctypes.windll.shell32.IsUserAnAdmin.restype = wt.BOOL
            ctypes.windll.shell32.IsUserAnAdmin.argtypes = []
            out["is_admin_account"] = bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception as e:  # 这一路只是旁证，缺席不影响主判
            out["is_admin_account"] = None
            out["is_admin_account_note"] = "%s: %s" % (type(e).__name__, e)
        return out
    except Exception as e:
        out["reason"] = "%s: %s" % (type(e).__name__, e)
        return out


def _exclusive_open(path: str) -> Tuple[bool, int]:
    """以 share=0 独占打开，判断"是否正被别的进程占用"；返回 (成功, GetLastError)。

    本机实测四臂（p53_preflight_probe.py V3）：空闲→(True, 0)；他人持有→(False, 32)；
    文件不存在→(False, 2)；share=ALL→(True, 0)。调用方须把 2 理解为"未被占用"。
    """
    if not WINDOWS:
        return (False, 2)
    try:
        import ctypes
        import ctypes.wintypes as wt
        k32 = ctypes.windll.kernel32
        k32.CreateFileW.restype = wt.HANDLE
        k32.CreateFileW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD, ctypes.c_void_p,
                                    wt.DWORD, wt.DWORD, ctypes.c_void_p]
        k32.GetLastError.restype = wt.DWORD
        k32.CloseHandle.restype = wt.BOOL
        h = k32.CreateFileW(path, 0x80000000 | 0x40000000, 0, None, 3, 0x80, None)
        invalid = wt.HANDLE(-1).value
        if h is None or h == -1 or (invalid is not None and h == invalid):
            return False, k32.GetLastError()
        k32.CloseHandle(h)
        return True, 0
    except Exception:
        return False, -1


def _write_probe(directory: str) -> Tuple[Optional[bool], Dict[str, Any]]:
    """目录可写探针：必要时建目录 → 写探针文件 → 读回校验 → 删除。

    只在调用方显式交出的目录上跑（真实安装根永不作为探针目标，见 preflight_install 的
    target_override 规则）；归因区分"目录建不出来"(mkdir_failed=True) 与"建得出但写不了"，
    因为前者是 cannot_create_target、后者才是 target_not_writable。
    """
    if not directory:
        return None, {"reason": "empty_path"}
    d = Path(directory)
    made = False
    try:
        if not d.exists():
            d.mkdir(parents=True, exist_ok=True)
            made = True
    except OSError as e:
        return False, {"os_error": getattr(e, "winerror", None) or getattr(e, "errno", None),
                       "exception": type(e).__name__, "mkdir_failed": True,
                       "detail": str(e)}
    probe = d / PROBE_FILE_NAME
    payload = b"zaojing-preflight-probe"
    try:
        probe.write_bytes(payload)
    except OSError as e:
        if made:
            try:
                d.rmdir()
            except OSError:
                pass
        return False, {"os_error": getattr(e, "winerror", None) or getattr(e, "errno", None),
                      "exception": type(e).__name__, "mkdir_failed": False, "detail": str(e)}
    written_ok = probe.read_bytes() == payload
    try:
        probe.unlink()
    except OSError as e:
        return None, {"reason": "probe_cleanup_failed", "detail": str(e),
                      "written_ok": written_ok}
    if made:
        try:
            d.rmdir()
        except OSError:
            pass
    return bool(written_ok), {"via": "real_probe_file", "bytes": len(payload),
                              "created_directory": made, "probe_removed": not probe.exists()}


def _disk_free(path: str) -> Optional[int]:
    """磁盘余量接缝：shutil.disk_usage 实测；路径不可解析时返回 None（未探测 ≠ 没有空间）。"""
    if not path:
        return None
    try:
        return int(shutil.disk_usage(path).free)
    except OSError:
        try:
            return int(shutil.disk_usage(os.path.dirname(path) or path).free)
        except OSError:
            return None


def _shell_folder_path(csidl: int) -> Optional[str]:
    """SHGetFolderPathW 接缝（只读）。失败/非 Windows 返回 None，不参与判定只作候选来源。"""
    if not WINDOWS:
        return None
    try:
        import ctypes
        import ctypes.wintypes as wt
        shell32 = ctypes.windll.shell32
        shell32.SHGetFolderPathW.restype = ctypes.c_int
        shell32.SHGetFolderPathW.argtypes = [wt.HWND, ctypes.c_int, wt.HANDLE, ctypes.c_uint,
                                             wt.LPWSTR]
        buf = ctypes.create_unicode_buffer(wt.MAX_PATH + 1)
        if shell32.SHGetFolderPathW(None, csidl, None, 0, buf) == 0 and buf.value:
            return buf.value
    except Exception:
        return None
    return None


def _known_folder_path(folder_id: str) -> Optional[str]:
    """SHGetKnownFolderPath 接缝（只读）。本机对 FOLDERID_UserProgramFiles 实测 hr=0x80070002，
    返回 None 属正常观察值，故 {userpf} 候选必须有 {localappdata}\\Programs 这条兜底路径。"""
    if not WINDOWS:
        return None
    try:
        import ctypes

        class _GUID(ctypes.Structure):
            _fields_ = [("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort),
                        ("Data3", ctypes.c_ushort), ("Data4", ctypes.c_ubyte * 8)]

        hexed = folder_id.replace("-", "")
        g = _GUID(int(hexed[0:8], 16), int(hexed[8:12], 16), int(hexed[12:16], 16),
                  (ctypes.c_ubyte * 8).from_buffer_copy(bytes.fromhex(hexed[16:32])))
        shell32 = ctypes.windll.shell32
        shell32.SHGetKnownFolderPath.restype = ctypes.c_int
        shell32.SHGetKnownFolderPath.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p,
                                                 ctypes.POINTER(ctypes.c_wchar_p)]
        out = ctypes.c_wchar_p()
        if shell32.SHGetKnownFolderPath(ctypes.byref(g), 0, None, ctypes.byref(out)) == 0:
            value = out.value
            try:
                ctypes.windll.ole32.CoTaskMemFree(ctypes.cast(out, ctypes.c_void_p))
            except Exception:
                pass
            return value or None
    except Exception:
        return None
    return None


def effective_privileges_required(sections: Dict[str, List[Tuple[int, str]]]) -> Dict[str, Any]:
    """生效装模式（PrivilegesRequired）派生：显式写法归一小写，缺省即 Inno 的 admin。

    R11 与预检面共用本函数——两处若各写一套"什么算需要提权"，迟早一处翻另一处不翻。
    """
    sm = setup_map(sections.get("Setup", []))
    declared, line = sm.get("privilegesrequired", ("", 0))
    declared = (declared or "").strip()
    eff = declared.lower() if declared else DEFAULT_PRIVILEGES_REQUIRED
    return {"declared": declared or None, "declared_line": line, "effective": eff,
            "default_when_absent": DEFAULT_PRIVILEGES_REQUIRED,
            "requires_elevation": eff in ADMIN_PRIVILEGES,
            "basis": "Inno 的 PrivilegesRequired 缺省即 admin（本机 iscc 缺省告警原文即 "
                     "set to \"admin\"）；非 admin 形制（lowest/none/poweruser）不要求提权"}


def default_dir_candidates(sections: Dict[str, List[Tuple[int, str]]]) -> List[Dict[str, Any]]:
    """把 DefaultDirName 解析成**候选安装根**：auto 常量给 common/user 两条，各标取得方式。

    {autopf} 不是"一个路径"：官方文本（AUTO_CONSTANTS_DOC）说 auto 常量在非管理员安装模式下
    映射为 user 形式，否则映射为 common 形式；而 {commonpf} 又取决于是否 64 位安装模式。
    发货脚本写的是 PrivilegesRequired=lowest，运行时究竟落哪条由令牌决定，
    因此预检报两条候选并回显每条来源，绝不替读者挑一个"真相"。
    """
    sm = setup_map(sections.get("Setup", []))
    raw = sm.get("defaultdirname", ("", 0))[0]
    if not raw:
        return []
    m = re.match(r"\A\{([A-Za-z_][A-Za-z0-9]*)\}(.*)\Z", raw, re.S)
    if not m:
        literal = os.path.abspath(os.path.normpath(raw.replace("\\", os.sep)))
        return [{"declared": raw, "constant": None, "inno_form": "literal", "path": literal,
                 "exists": _dir_exists(literal), "resolved_via": "literal-path",
                 "unresolved": False,
                 "basis": "DefaultDirName 为字面路径，无需常量解析"}]
    const, rest = m.group(1), m.group(2)
    rest_parts = [seg for seg in re.split(r"[\\/]", rest) if seg]

    def _join(base: Optional[str], via: str, form: str, basis: str) -> Dict[str, Any]:
        path = os.path.abspath(os.path.normpath(os.path.join(base, *rest_parts))) if base else None
        return {"declared": raw, "constant": "{" + const + "}", "inno_form": form,
                "path": path, "exists": bool(path) and _dir_exists(path),
                "resolved_via": via, "unresolved": path is None, "basis": basis}

    if const.lower() in AUTO_CONSTANT_FORMS:
        common_form, user_form = AUTO_CONSTANT_FORMS[const.lower()]
        if const.lower() == "autopf":
            pf = _env_dir("PROGRAMFILES") or _shell_folder_path(CSIDL_PROGRAM_FILES)
            via = "env:ProgramFiles" if _env_dir("PROGRAMFILES") else "SHGetFolderPathW(CSIDL_PROGRAM_FILES)"
            common = _join(pf or None, via, common_form,
                           "本机实测：64 位进程视图下 CSIDL_PROGRAM_FILES=%s；{commonpf} 在"
                           "非 64 位安装模式下等于 {commonpf32}（本机另见 Program Files (x86)=%s），"
                           "故这一条代表管理员安装模式的落点" %
                           (_shell_folder_path(CSIDL_PROGRAM_FILES),
                            _shell_folder_path(CSIDL_PROGRAM_FILESX86)))
            upf = _known_folder_path(USER_PROGRAM_FILES_FOLDERID)
            lad = _resolve_inno_path("{localappdata}") or _shell_folder_path(CSIDL_LOCAL_APPDATA)
            fallback = os.path.join(lad, "Programs") if lad else None
            if upf:
                user = _join(upf, "SHGetKnownFolderPath(FOLDERID_UserProgramFiles)", user_form,
                             "known folder 实测可解析")
            else:
                user = _join(fallback, "fallback:{localappdata}\\Programs", user_form,
                             "FOLDERID_UserProgramFiles 本机实测 hr=0x80070002 不可解析，"
                             "按 Inno 文档 {userpf}=当前用户 Program Files 目录回落到 "
                             "{localappdata}\\Programs（来源已标 fallback）")
            user["basis"] += "；auto 常量整体形制见 AUTO_CONSTANTS_DOC"
            return [common, user]
        return [_join(None, "unmeasured", common_form,
                      "该 auto 常量的本机位置未经实测（本轮只实测过 {autopf} 两条落点），"
                      "如实标 unresolved，不做外推"),
                _join(None, "unmeasured", user_form,
                      "同上：非管理员安装模式的落点未实测，不猜")]
    env_name = _INNO_ENV_MAP.get(const.lower())
    if env_name:
        base = _env_dir(env_name)
        return [_join(base or None, "env:" + env_name, const.lower(),
                      "非 auto 常量：单一落点，由环境变量解析" if base
                      else "环境变量 %s 不可解析，如实标 unresolved" % env_name)]
    return [_join(None, "unknown-constant", const.lower(),
                  "未识别的 Inno 常量，无法解析为磁盘路径")]


def _payload_total_bytes(sections: Dict[str, List[Tuple[int, str]]],
                         payload_root: str) -> Tuple[int, int, int]:
    """量出声明载荷在脚本目录下的真实字节总量：返回 (总字节, 命中条数, 缺失条数)。

    需求量必须量出来而不是估个系数——本轮根除的正是"把想当然的常数写进判据"。
    """
    total, hits, misses = 0, 0, 0
    for _, stmt in sections.get("Files", []):
        src = split_params(stmt).get("source", "")
        if not src:
            continue
        full = src if os.path.isabs(src) else os.path.join(payload_root,
                                                           src.replace("\\", os.sep))
        if any(ch in src for ch in "*?["):
            matched = _glob_matches(full)
            if matched:
                hits += 1
            else:
                misses += 1
            for p in matched:
                try:
                    total += Path(p).stat().st_size
                except OSError:
                    pass
        elif _path_exists(full):
            hits += 1
            try:
                total += Path(full).stat().st_size
            except OSError:
                pass
        else:
            misses += 1
    return total, hits, misses


def preflight_install(script_text: Any, payload_root: str, data_root: str = "",
                      evidence: Any = None, probe_writable: bool = True,
                      target_override: str = "") -> Dict[str, Any]:
    """预检面：按脚本声明 + 本机实况判"若要安装，条件够不够"。绝不执行安装。

    三态原则：每一项检查的 result ∈ {True, False, None}，None 表示**未探测**，
    进 unknowns 而不是 blocking（"没探测"写成"不可写/无权限"就是本轮根除的假信号）。
    verdict：有阻断项 → preflight_blocked；否则有未探测项 → preflight_unknown；
    否则 preflight_ready。

    写探针规则：只有调用方显式给出 target_override 时才真落盘（且探针文件用完即删）；
    默认不拿解析出的真实安装根当探针目标——预检是只读仪器，不该为了回答而制造副作用。
    """
    if not isinstance(script_text, str) or not script_text.strip():
        raise ManifestError("脚本内容须为非空文本，实为 %r" % (type(script_text).__name__,))
    if not isinstance(payload_root, str) or not payload_root.strip():
        raise ManifestError("payload_root 须为非空路径")
    sections = parse_script(script_text)
    sm = setup_map(sections.get("Setup", []))
    priv = effective_privileges_required(sections)
    candidates = default_dir_candidates(sections)
    resolved = [c for c in candidates if c["path"]]

    checks: Dict[str, Any] = {}
    blocking: List[Dict[str, Any]] = []
    unknowns: List[Dict[str, Any]] = []

    def _block(code, basis, detail):
        blocking.append({"code": code, "basis": basis, "detail": detail})

    def _unknown(code, basis, detail=None):
        unknowns.append({"code": code, "basis": basis, "detail": detail})

    # 1) 权限：需要提权的脚本必须实测到已提权；探测不到即 unknown，不替用户猜
    elev = _token_elevation()
    if not priv["requires_elevation"]:
        checks["privileges"] = {"result": True if elev["available"] is not None else None,
                                "declared": priv["declared"], "effective": priv["effective"],
                                "elevated": elev["elevated"], "elevation": elev,
                                "basis": "PrivilegesRequired=%s 不要求提权" % priv["effective"]}
        if elev["available"] is None:
            _unknown("elevation_state_unresolved",
                     "令牌状态读不到（%s）：本脚本不要求提权，故不阻断，但也不声称已确认"
                     % elev["reason"])
    elif elev["available"] is None:
        checks["privileges"] = {"result": None, "declared": priv["declared"],
                                "effective": priv["effective"], "elevated": None,
                                "elevation": elev,
                                "basis": "需要提权而令牌状态未探测：不阻断也不放行"}
        _unknown("elevation_state_unresolved",
                 "PrivilegesRequired=%s 要求提权，但 GetTokenInformation 未能给出结果"
                 % priv["effective"], elev)
    elif elev["elevated"] is True:
        checks["privileges"] = {"result": True, "declared": priv["declared"],
                                "effective": priv["effective"], "elevated": True,
                                "elevation": elev, "basis": "已提权，满足脚本要求"}
    else:
        checks["privileges"] = {"result": False, "declared": priv["declared"],
                                "effective": priv["effective"], "elevated": False,
                                "elevation": elev,
                                "basis": "脚本要求 admin 装模式而实测令牌未提权"}
        _block("privileges_insufficient",
               "PrivilegesRequired=%s 需提权，实测 TokenElevation=0（is_admin_account=%r）"
               % (priv["effective"], elev.get("is_admin_account")), elev)

    # 2) 安装根：报候选（auto 常量两条），并说明本次判定用的是哪一条
    target = str(target_override) if target_override else (
        resolved[0]["path"] if resolved else None)
    checks["install_root"] = {
        "result": True if target else None,
        "declared_default_dir_name": sm.get("defaultdirname", ("", 0))[0],
        "candidates": candidates,
        "judged_on": "target_override" if target_override else
                     ("first_resolved_candidate" if target else "none"),
        "basis": "候选按 Inno auto 常量形制成对给出；判定口径见 judged_on，"
                 "未探测到可解析候选时不猜路径"}
    if not target:
        _unknown("install_root_unresolved",
                 "DefaultDirName 解析不出磁盘路径（候选全为 unresolved），"
                 "预检无法就安装根下结论", candidates)

    # 3) 目标冲突：同名文件占位 / 目标 exe 正被占用
    conflict_detail: Dict[str, Any] = {"target": target, "exists_as_file": False,
                                       "exe_checked": [], "held": []}
    conflict_result: Optional[bool] = None
    if target:
        if Path(target).is_file():
            conflict_detail["exists_as_file"] = True
            conflict_result = False
            _block("target_exists_as_file",
                   "安装根路径上已存在同名**文件**，目录建不出来（与权限无关）", conflict_detail)
        elif _dir_exists(target):
            exes = sorted(str(p) for p in Path(target).glob("*.exe"))
            for exe in exes:
                ok, err = _exclusive_open(exe)
                conflict_detail["exe_checked"].append({"path": exe, "exclusive": ok,
                                                       "os_error": err})
                if ok is False and err not in (2,):
                    conflict_detail["held"].append(exe)
                    conflict_result = False
                    _block("target_exe_in_use",
                           "CreateFileW share=0 实测 error=%d（32=ERROR_SHARING_VIOLATION）："
                           "目标 exe 正被占用，安装器无法覆盖" % err,
                           conflict_detail)
            if conflict_result is None:
                conflict_result = True
        else:
            conflict_result = True
            conflict_detail["note"] = "目标目录尚不存在（全新安装形制），无冲突可查"
    else:
        conflict_detail["note"] = "无可判定的目标路径"
    checks["target_conflict"] = {"result": conflict_result, "detail": conflict_detail,
                                 "basis": "只读观察 + share=0 试探；不存在(err=2) 视为未被占用"}

    # 4) 可写性：仅当调用方显式给出沙箱目标时才真写探针
    if not probe_writable:
        checks["writability"] = {"result": None, "probed": False, "target": target,
                                 "detail": {"reason": "caller_disabled_probe"},
                                 "basis": "调用方关闭了写探针（probe_writable=False）"}
        _unknown("write_probe_unprobed",
                 "写探针未运行：不声称可写，也不声称不可写")
    elif conflict_detail["exists_as_file"]:
        checks["writability"] = {"result": None, "probed": False, "target": target,
                                 "detail": {"reason": "target_exists_as_file"},
                                 "basis": "目标是文件，写探针不适用（已由 target_exists_as_file 阻断）"}
    elif not target_override:
        checks["writability"] = {"result": None, "probed": False, "target": target,
                                 "detail": {"reason": "no_probe_target"},
                                 "basis": "预检默认不对解析出的真实安装根落盘：需要实测可写性时"
                                          "由调用方显式给出 target_override"}
        _unknown("write_probe_unprobed",
                 "未给出 target_override：真实安装根不作为探针目标（只读承诺）")
    else:
        ok, detail = _write_probe(target_override)
        detail = dict(detail or {})
        checks["writability"] = {"result": ok, "probed": True, "target": target_override,
                                 "detail": detail,
                                 "basis": "真写探针（建→读回→删），归因区分 mkdir 失败与写入失败"}
        if ok is False:
            if detail.get("mkdir_failed"):
                _block("cannot_create_target",
                       "目标目录建不出来（os_error=%s, %s）" % (detail.get("os_error"),
                                                     detail.get("exception")), detail)
            else:
                _block("target_not_writable",
                       "目录存在但写不进（os_error=%s, %s）" % (detail.get("os_error"),
                                                   detail.get("exception")), detail)
        elif ok is None:
            _unknown("write_probe_unresolved",
                     "写探针未能归因：%s" % detail.get("reason"), detail)

    # 5) 产物是否已被证成（预检的前提是"有可装的东西"）
    art_state = "not_supplied"
    art_report: Optional[Dict[str, Any]] = None
    if evidence is None:
        _block("artifact_missing", "未提供构建证据：无被证成的产物即谈不上可安装", None)
    else:
        art_report = verify_build_evidence(evidence, data_root=(data_root or None))
        v = art_report["verdict"]
        if v == "artifact_verified":
            art_state = "verified"
        elif v == "artifact_missing":
            art_state = "missing"
            _block("artifact_missing", "证据指向的产物不在盘（verdict=artifact_missing）",
                   {"artifact": art_report["artifact"], "signals": art_report["signals"]})
        else:
            art_state = "not_verified"
            _block("artifact_not_verified",
                   "编译面判定为 %s：产物未获证成，预检不放行" % v,
                   {"verdict": v, "blocking_codes": art_report["blocking_codes"],
                    "missing_fields": art_report["missing_fields"]})
    checks["artifact"] = {"result": art_state == "verified", "state": art_state,
                          "verdict": art_report["verdict"] if art_report else None,
                          "detail": {"artifact": art_report["artifact"]} if art_report else None,
                          "basis": "预检复用编译面：产物先被证成，才谈安装条件"}

    # 6) 磁盘余量：需求 = 产物字节 + 声明载荷字节（两项都是量出来的）
    artifact_bytes = (art_report["artifact"]["observed"]["bytes"]
                      if art_report and art_report["artifact"]["observed"] else 0)
    payload_bytes, payload_hits, payload_misses = _payload_total_bytes(sections, payload_root)
    required = int(artifact_bytes) + int(payload_bytes)
    probe_at = target or payload_root
    free = _disk_free(probe_at) if probe_at else None
    if free is None:
        checks["disk_space"] = {"result": None, "free_bytes": None, "required_bytes": required,
                                "measured_on": probe_at,
                                "basis": "磁盘余量未能实测（路径不可解析或 API 失败）"}
        _unknown("disk_free_unresolved", "shutil.disk_usage 未给出结果，不声称空间充足",
                 {"path": probe_at})
    else:
        enough = free >= required
        checks["disk_space"] = {"result": enough, "free_bytes": free, "required_bytes": required,
                                "artifact_bytes": artifact_bytes,
                                "payload_bytes": payload_bytes, "measured_on": probe_at,
                                "basis": "需求量 = 产物字节 + 声明载荷字节实测和；"
                                         "系数不外推（本轮根除的正是把常数写进判据）"}
        if not enough:
            _block("space_insufficient",
                   "实测剩余 %d 字节 < 需求 %d 字节（%s）" % (free, required, probe_at),
                   checks["disk_space"])

    # 7) 载荷可得性：直接复用 R2/R3 的判据，不另起一套口径
    file_findings = check_files(sections, payload_root)
    file_errors = [f for f in file_findings if f["severity"] == "error"]
    checks["payload"] = {"result": not file_errors if sections.get("Files") else True,
                         "declared_sources": len(sections.get("Files", [])),
                         "payload_root": payload_root,
                         "present_count": payload_hits, "missing_count": payload_misses,
                         "findings": [f["id"] for f in file_findings],
                         "basis": "与静态校验 R2/R3 同一判据（Literal 缺失 / 通配零命中）"}
    if file_errors:
        _block("payload_source_missing",
               "声明载荷在脚本目录下缺失：%s" % sorted({f["id"] for f in file_errors}),
               {"errors": [f["evidence"] for f in file_errors][:5]})

    if blocking:
        verdict = "preflight_blocked"
    elif unknowns:
        verdict = "preflight_unknown"
    else:
        verdict = "preflight_ready"

    return {
        "schema": SCHEMA,
        "face": "installer_preflight",
        "verdict": verdict,
        "blocking": blocking,
        "blocking_codes": [b["code"] for b in blocking],
        "unknowns": unknowns,
        "unknown_codes": [u["code"] for u in unknowns],
        "checks": checks,
        "script": {"app_name": sm.get("appname", ("", 0))[0],
                   "default_dir_name": sm.get("defaultdirname", ("", 0))[0],
                   "privileges_required": priv,
                   "files_entries": len(sections.get("Files", [])),
                   "dirs_entries": len(sections.get("Dirs", []))},
        "probe_writable": bool(probe_writable),
        "target_override": target_override or None,
        "execution": dict(EXECUTION_BOUNDARY),
        "not_install": {
            "installed_claimed": False,
            "preflight_is_not_install": True,
            "compile_is_not_install": True,
            "basis": "预检结论只描述「若执行安装则本机条件如何」，既不是安装结果也不是安装态；"
                     "产物已证成同样不等于已安装（执行器自 Phase 58 起在 CLI 上真装真卸并留台账，"
                     "本函数仍零子进程、零写入）",
        },
        "fabricated": False,
        "fabricated_basis": "权限/占用/可写/余量/载荷/产物六面均取自本次实测或显式桩；"
                            "探测不到的项落 unknowns 并以 result=None 回显，绝不补齐成结论",
    }


# ---------------------------------------------------------------------------
# 自测（硬断言，任何不符即 AssertionError；夹具建于系统临时目录并即时删除）
# ---------------------------------------------------------------------------

CLEAN_SCRIPT = "\n".join([
    "[Setup]",
    "AppName=ZaojingAI",
    "AppVersion=1.0.0",
    "AppId={{11112222-3333-4444-5555-666677778888}",
    "PrivilegesRequired=lowest",
    "DefaultDirName={autopf}\\Zaojing",
    "[Files]",
    'Source: "clean_exe.exe"; DestDir: "{autopf}\\Zaojing"; Flags: ignoreversion',
    "[Icons]",
    'Name: "{group}\\卸载"; Filename: "{autopf}\\Zaojing\\unins000.exe"',
    "[Registry]",
    'Root: HKCU; Subkey: "Software\\Zaojing"; Flags: uninsdeletekeyifempty',
    'Root: HKCU; Subkey: "Software\\Zaojing\\ZaojingAI"; Flags: uninsdeletekey',
    'Root: HKCU; Subkey: "Software\\Zaojing\\ZaojingAI"; ValueType: string; ValueName: "Version"; ValueData: "1.0.0"',
])

BROKEN_SCRIPT = "\n".join([
    "[Setup]",
    "AppName=ZaojingAI",
    "AppVersion=1.0.0",
    "AppId={{9A8B7C6D-5E4F-3G2H-1I0J-K9L8M7N6O5P4}",
    "DefaultDirName={autopf}\\company",
    "LicenseFile=",
    "[Files]",
    'Source: "nope\\missing.exe"; DestDir: "{autoprograms}\\{app_name}"; Flags: ignoreversion',
    'Source: "nodir\\*.dll"; DestDir: "{autoprograms}"; Flags: ignoreversion',
    "[Icons]",
    'Name: "{group}\\{app_name}\\ZaojingAI"; Filename: "{autoprograms}\\{app_name}.exe"',
    'Name: "{group}\\卸载"; Filename: "{autopf}\\ZaojingAI\\Uninstall.exe"',
    "[Dirs]",
    'Name: "{userlocalappdata}\\{app_name}\\Assets"; Permissions: usersfull',
    "[Registry]",
    'Root: HKCU; Subkey: "Software\\company\\ZaojingAI"; ValueType: string; ValueName: "InstallDate"; ValueData: "{install_date}"',
])

BROKEN_APPID = "{9A8B7C6D-5E4F-3G2H-1I0J-K9L8M7N6O5P4}"

# Phase 53 由真 iscc 定论的发货缺陷：Inno 中值里的字面 `{` 必须双写成 `{{`，否则编译器把
# `{GUID}` 当常量展开、以 "Unknown constant" 退出（本机实测单写 exit=2、双写 exit=0，
# 见审计报告 Phase 53 段）。而 Inno 自动登记的卸载键名是单花括号的 `{GUID}_is1`，
# 因此"转义状态"与"键名派生"必须彼此无关地各自成立——只钉一条，另一条就能写反。
UNESCAPED_APPID_SCRIPT = "\n".join([
    "[Setup]",
    "AppName=ZaojingAI",
    "AppVersion=1.0.0",
    "AppId={11112222-3333-4444-5555-666677778888}",
    "DefaultDirName={autopf}\\Zaojing",
    "[Files]",
    'Source: "clean_exe.exe"; DestDir: "{app}"; Flags: ignoreversion',
])
GUID_INNER = "11112222-3333-4444-5555-666677778888"

# 生产修正后的写法（DestDir 用 {app}、[Dirs] 用 {localappdata}、卸载用 {uninstallexe}）。
# 这条夹具专钉 R5/R7 的两处解析归一：写法则、语义正确时绝不误报，否则"改到零告警"
# 会把脚本推向错误的写法。_env_dir 被桩替换以脱离本机 LOCALAPPDATA 依赖。
PRODUCTION_SHAPE_SCRIPT = "\n".join([
    "[Setup]",
    "AppName=ZaojingAI",
    "AppVersion=1.0.0",
    "AppId={{11112222-3333-4444-5555-666677778888}",
    "PrivilegesRequired=lowest",
    "DefaultDirName={autopf}\\Zaojing",
    "[Files]",
    'Source: "prod_exe.exe"; DestDir: "{app}"; Flags: ignoreversion',
    "[Icons]",
    'Name: "{group}\\ZaojingAI"; Filename: "{app}\\prod_exe.exe"',
    'Name: "{group}\\卸载"; Filename: "{uninstallexe}"',
    "[Dirs]",
    'Name: "{localappdata}\\Zaojing\\Assets"; Permissions: users-modify',
    'Name: "{localappdata}\\Zaojing\\Logs"; Permissions: users-modify',
])


def _with_setup(script: str, *directives: str) -> str:
    """把 [Setup] 指令插进已有夹具（AppName 行之后）。

    R11/R12 的夹具之间只差一条指令，整段复制会让"差异"淹没在重复里、改一处漏一处；
    用本助手构造后，夹具的每一个字节差都就是被测的那条指令。
    """
    lines = script.splitlines()
    try:
        at = next(i for i, ln in enumerate(lines) if ln.startswith("AppName=")) + 1
    except StopIteration:
        raise AssertionError("夹具缺少 AppName= 行，无法定位 [Setup] 插入点")
    return "\n".join(lines[:at] + list(directives) + lines[at:])


# Phase 53 首次真编译（exit=0）之外，iscc 还在输出里报了两行**告警**，静态面此前全盲：
#   Warning: ... directive "ArchitecturesAllowed" value "x64" is deprecated. Substituting
#            "x64os", but note that "x64compatible" is preferred in most cases.
#   Warning: ... "PrivilegesRequired" is set to "admin" but per-user areas
#            (HKCU,localappdata,userdocs) are used by the script. ...
# 这两条形制就是 R11/R12 的实测依据；PER_USER_AREAS_SCRIPT 复刻本机发货脚本被点名到的
# 那三个 per-user 区域，逐区各来一处（注册表 / 文档 / 预建目录）。
PER_USER_AREAS_SCRIPT = "\n".join([
    "[Setup]",
    "AppName=ZaojingAI",
    "AppVersion=1.0.0",
    "AppId={{11112222-3333-4444-5555-666677778888}",
    "DefaultDirName={autopf}\\Zaojing",
    "[Files]",
    'Source: "clean_exe.exe"; DestDir: "{app}"; Flags: ignoreversion',
    'Source: "readme.md"; DestDir: "{userdocs}\\ZaojingAI"; Flags: isreadme',
    "[Dirs]",
    'Name: "{localappdata}\\Zaojing\\Logs"; Permissions: users-modify',
    "[Registry]",
    'Root: HKCU; Subkey: "Software\\Zaojing"; Flags: uninsdeletekeyifempty',
    'Root: HKCU; Subkey: "Software\\Zaojing\\ZaojingAI"; Flags: uninsdeletekey',
    'Root: HKCU; Subkey: "Software\\Zaojing\\ZaojingAI"; ValueType: string; ValueName: "Version"; ValueData: "1.0.0"',
])
PRIV_LOWEST_AREAS_SCRIPT = _with_setup(PER_USER_AREAS_SCRIPT, "PrivilegesRequired=lowest")
PRIV_ADMIN_AREAS_SCRIPT = _with_setup(PER_USER_AREAS_SCRIPT, "PrivilegesRequired=admin")

# 只碰公共区域（HKLM + {commonappdata}）：PrivilegesRequired 缺省即 admin，此处配对成立，
# 故不得报——R11 判的是"per-user 区域 × admin 装模式"这一**组合**，不是任一单项。
PRIV_ADMIN_COMMON_SCRIPT = "\n".join([
    "[Setup]",
    "AppName=ZaojingAI",
    "AppVersion=1.0.0",
    "AppId={{11112222-3333-4444-5555-666677778888}",
    "DefaultDirName={autopf}\\Zaojing",
    "[Files]",
    'Source: "clean_exe.exe"; DestDir: "{app}"; Flags: ignoreversion',
    "[Dirs]",
    'Name: "{commonappdata}\\Zaojing\\Logs"; Permissions: admins-modify',
    "[Registry]",
    'Root: HKLM; Subkey: "Software\\Zaojing"; Flags: uninsdeletekeyifempty',
    'Root: HKLM; Subkey: "Software\\Zaojing\\ZaojingAI"; Flags: uninsdeletekey',
    'Root: HKLM; Subkey: "Software\\Zaojing\\ZaojingAI"; ValueType: string; ValueName: "Version"; ValueData: "1.0.0"',
])
DEPRECATED_ARCH_SCRIPT = _with_setup(
    PRIV_LOWEST_AREAS_SCRIPT,
    "ArchitecturesAllowed=x64",
    "ArchitecturesInstallIn64BitMode=x64",
)
ARCH_COMPATIBLE_SCRIPT = _with_setup(
    PRIV_LOWEST_AREAS_SCRIPT,
    "ArchitecturesAllowed=x64compatible",
    "ArchitecturesInstallIn64BitMode=x64compatible",
)
# 整词比较的反证：x64os / x64compatible 都以 "x64" 开头，用子串判据就会把合规写法一并冤枉。
ARCH_OK_VARIANTS_SCRIPT = _with_setup(
    PRIV_LOWEST_AREAS_SCRIPT,
    "ArchitecturesAllowed=x64os,x64compatible",
    "ArchitecturesInstallIn64BitMode=x64compatible",
)

# R13 的夹具：从 CLEAN 里只抽掉 AppVersion 一行。CLEAN 本身不写 AppVerName，于是两条版本
# 指令同时缺席——正是探针里 iscc 以 exit=2 拒绝的那种形制。
NO_VERSION_SCRIPT = "\n".join([ln for ln in CLEAN_SCRIPT.splitlines()
                               if not ln.startswith("AppVersion")])

# ---- R14 的夹具：写入型 [Registry] 条目与"卸载时是否撤销"必须成对出现 ----
# 立项依据不是文档而是 Phase 58 本机真卸载实测：unins000.exe 以 0 退出、安装根与 HKCU 卸载
# 子键双双消失、四棵目录树归零、6 枚既有数据逐字节未动，**而 HKCU\Software\造境科技\ZaojingAI
# 的三枚值原样留守**（现场只读枚举实测 app_key 仍有 Version/InstallLocation/InstallDate，
# 执行器据此判 uninstall_incomplete 并轮询满 180 s）。Inno 的 [Registry] 默认只写不删，
# 撤销须由旗标显式声明——"装得干净"与"卸得干净"是两条互不通用的判据。
# 夹具与 CLEAN 只差 [Registry] 段的形制，其余逐字相同，于是"差异即缺陷"可被读出来。
REG_HEADER = [
    "[Setup]",
    "AppName=ZaojingAI",
    "AppVersion=1.0.0",
    "AppId={{" + GUID_INNER + "}",
    "PrivilegesRequired=lowest",
    "DefaultDirName={autopf}\\Zaojing",
    "[Files]",
    'Source: "clean_exe.exe"; DestDir: "{app}"; Flags: ignoreversion',
]
REG_WRITES = [
    'Root: HKCU; Subkey: "Software\\Zaojing\\ZaojingAI"; ValueType: string;'
    ' ValueName: "Version"; ValueData: "1.0.0"',
    'Root: HKCU; Subkey: "Software\\Zaojing\\ZaojingAI"; ValueType: string;'
    ' ValueName: "InstallLocation"; ValueData: "{app}"',
]
REG_RESIDUE_SCRIPT = "\n".join(REG_HEADER + ["[Registry]"] + REG_WRITES)
# 发货级修正形制（Inno 自带 Examples\Example3.iss 的写法逐字同构：厂商键 ifempty + 应用键
# 整键撤销；两条写入本身不需要旗标）。
REG_CLEAN_SCRIPT = "\n".join(REG_HEADER + ["[Registry]",
    'Root: HKCU; Subkey: "Software\\Zaojing"; Flags: uninsdeletekeyifempty',
    'Root: HKCU; Subkey: "Software\\Zaojing\\ZaojingAI"; Flags: uninsdeletekey',
] + REG_WRITES)
# 臂三：只给每枚值各自挂 uninsdeletevalue，同样成立——键与值是两级撤销，不能互相顶替。
REG_CLEAN_PER_VALUE_SCRIPT = "\n".join(
    REG_HEADER + ["[Registry]"] + [w + "; Flags: uninsdeletevalue" for w in REG_WRITES])
# 臂四：显式声明"永不撤销"是**有意的**归属让渡（共享键），不属于残留缺陷，不得报。
REG_NEVER_SCRIPT = "\n".join(
    REG_HEADER + ["[Registry]"] + [w + "; Flags: uninsneveruninstall" for w in REG_WRITES])
# 臂五（反证的关键）：只在**父键**上声明 ifempty 而应用键自身带着值——父键永远不为空，
# 撤销永不发生。若规则被父键的旗标"洗白"，它就会把这枚真实残留判成合规。
REG_PARENT_IFEMPTY_ONLY_SCRIPT = "\n".join(
    REG_HEADER + ["[Registry]",
                  'Root: HKCU; Subkey: "Software\\Zaojing"; Flags: uninsdeletekeyifempty',
                  ] + REG_WRITES)
# 臂五之二：父键声明整键撤销时，Inno 连同子键一并删除，子键因此已被覆盖，不得报。
REG_PARENT_DELETEKEY_SCRIPT = "\n".join(
    REG_HEADER + ["[Registry]",
                  'Root: HKCU; Subkey: "Software\\Zaojing"; Flags: uninsdeletekey',
                  ] + REG_WRITES)
# 臂六：跨视图声明不算覆盖——写入落在 HKCU、撤销写在 HKCU64 之外另立一枚 HKLM，
# 现场查不中的那一路必须仍判残留（与安装态探测四路枚举同源的理由）。
REG_ROOT_MISMATCH_SCRIPT = "\n".join(
    REG_HEADER + ["[Registry]",
                  'Root: HKLM; Subkey: "Software\\Zaojing\\ZaojingAI"; Flags: uninsdeletekey',
                  ] + REG_WRITES)
# 臂八：把 ifempty 写在**应用键自身**上仍不成立——值从未被声明删除，键在卸载时非空，
# "仅当为空才删"于是永不触发。这一条把"接受集合"钉死为 uninsdeletekey / uninsdeletevalue
# 两枚，防止规则被一枚语义上顶不住的旗标洗白。
REG_IFEMPTY_SAME_KEY_SCRIPT = "\n".join(
    REG_HEADER + ["[Registry]",
                  'Root: HKCU; Subkey: "Software\\Zaojing\\ZaojingAI";'
                  ' Flags: uninsdeletekeyifempty',
                  ] + REG_WRITES)
# 臂八之二：撤销声明写成 HKCU64 而值写在 HKCU——同一处键的两种拼写，必须仍判已覆盖，
# 否则规则会用"拼写不同"制造假红（视图归一只改比较键，原始写法照旧回显在证据里）。
REG_SPELLING_VARIANT_SCRIPT = "\n".join(
    REG_HEADER + ["[Registry]",
                  'Root: HKCU; Subkey: "Software\\Zaojing"; Flags: uninsdeletekeyifempty',
                  'Root: HKCU64; Subkey: "Software\\Zaojing\\ZaojingAI";'
                  ' Flags: uninsdeletekey',
                  ] + REG_WRITES)


def write_build_evidence(directory: str, script_text: str = CLEAN_SCRIPT,
                         artifact_body: bytes = b"INNO-SETUP-ARTIFACT-FIXTURE",
                         exit_code: int = 0, warnings=(), compile_line: bool = True,
                         tail_full: bool = True, drop_fields=(),
                         override: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """编译面夹具：在 directory 里真写脚本与产物，再按**磁盘真值**生成一份构建证据。

    夹具必须由磁盘派生而不是手抄常量：否则"证据与产物相符"的正控只是在读我自己写进去的
    数字，任何一条复核臂都可能被夹具本身糊过去（Phase 53 反复踩到的同一类假绿）。
    """
    d = Path(directory)
    d.mkdir(parents=True, exist_ok=True)
    script = d / "setup.iss"
    script.write_text(script_text, encoding="utf-8")
    art_dir = d / "installer"
    art_dir.mkdir(parents=True, exist_ok=True)
    artifact = art_dir / "zaojing-ai-1.0.0-setup.exe"
    artifact.write_bytes(artifact_body)
    s_size, s_sha = _stat_and_sha256(str(script))
    a_size, a_sha = _stat_and_sha256(str(artifact))
    tail_lines = ["   Parsing [Setup] section, line 3"] + ["   " + w for w in warnings]
    done_line = ("Successful compile (0.109 sec). Resulting Setup program filename is:"
                 if compile_line else None)
    if done_line:
        tail_lines += [done_line, str(artifact)]
    tail = "\n".join(tail_lines)
    ev: Dict[str, Any] = {
        "schema": BUILD_EVIDENCE_SCHEMA,
        "script_path": str(script), "script_bytes": s_size, "script_sha256": s_sha,
        "compiler_path": os.path.join(directory, "ISCC.exe"),
        "compiler_lookup_mode": "fixed_dir",
        "iscc_exit_code": exit_code,
        "iscc_output_tail": tail, "iscc_warnings": list(warnings),
        "iscc_compile_line": done_line,
        # 生产者如实记录输出总长：尾部是全文还是截断，决定"告警计数"能否当一致性问题判。
        "iscc_output_chars": len(tail) if tail_full else len(tail) + 900,
        "artifact_path": str(artifact), "artifact_bytes": a_size, "artifact_sha256": a_sha,
        "manifest_verify_after_compile": {"errors": 0, "warnings": 0, "infos": 0, "total": 0},
        "compiled": exit_code == 0, "fabricated": False,
        "manifest_verdict": "manifest_ok", "built_at": "2026-09-29 18:39:36",
    }
    for k in drop_fields:
        ev.pop(k, None)
    if override:
        ev.update(override)
    return ev


def self_check() -> Dict[str, Any]:
    """返回逐项实测结果；任何断言不符直接抛 AssertionError（不做静默通过）。"""
    tmp = tempfile.mkdtemp(prefix="zj_p49_selfcheck_")
    res: Dict[str, Any] = {}
    try:
        Path(tmp, "clean_exe.exe").write_bytes(b"MZ-fixture")
        rep = verify_script(CLEAN_SCRIPT, tmp, data_root=os.path.join(tmp, "Zaojing"))
        assert rep["verdict"] == "manifest_ok", rep["findings"]
        assert rep["counts"] == {"errors": 0, "warnings": 0, "infos": 0, "total": 0}, rep["counts"]
        assert rep["app_id"] == "{{" + GUID_INNER + "}", rep["app_id"]
        assert rep["app_id_effective"] == "{" + GUID_INNER + "}", rep
        assert rep["uninstall_registry_key"] == "{" + GUID_INNER + "}_is1", rep
        assert rep["fabricated"] is False and rep["execution"]["performed"] is False
        res["clean_verdict"] = rep["verdict"]

        # 生产修正写法：{app} 载荷目录 + {localappdata} 预建目录 + {uninstallexe} 卸载入口
        # 必须零告警（R5/R7 的解析归一），且该结论由真实路径解析派生、非硬编码——
        # 把环境接缝桩成不可解析后，同一脚本必须重新报出 dirs_miss_runtime_data_root。
        Path(tmp, "prod_exe.exe").write_bytes(b"MZ-fixture")
        real_env_dir = _env_dir
        data_root = os.path.join(tmp, "Zaojing")
        try:
            globals()["_env_dir"] = lambda name: tmp
            repP = verify_script(PRODUCTION_SHAPE_SCRIPT, tmp, data_root=data_root)
            assert repP["counts"] == {"errors": 0, "warnings": 0, "infos": 0, "total": 0}, repP["findings"]
            assert repP["verdict"] == "manifest_ok", repP["verdict"]
            globals()["_env_dir"] = lambda name: ""
            repP2 = verify_script(PRODUCTION_SHAPE_SCRIPT, tmp, data_root=data_root)
            ids = {f["id"] for f in repP2["findings"]}
            assert "dirs_miss_runtime_data_root" in ids, repP2["findings"]
        finally:
            globals()["_env_dir"] = real_env_dir
        res["production_shape_verdict"] = repP["verdict"]
        res["dirs_rule_falsifiable"] = True

        # ---- AppId 字面花括号转义（Phase 53：唯一由真编译器定论、且本轮真编译过）----
        # 正例：双写形制必须零告警；反例：发货脚本的单写形制必须 error 且 verdict 翻红。
        # 两条形制派生出的卸载键名必须完全相同（Inno 把开头的 {{ 解成一个字面 {）。
        repE = verify_script(CLEAN_SCRIPT, tmp, data_root=os.path.join(tmp, "Zaojing"))
        assert repE["counts"]["total"] == 0, repE["findings"]
        assert repE["app_id"] == "{{" + GUID_INNER + "}", repE["app_id"]
        assert repE["app_id_effective"] == "{" + GUID_INNER + "}", repE
        assert repE["uninstall_registry_key"] == "{" + GUID_INNER + "}_is1", repE
        repU = verify_script(UNESCAPED_APPID_SCRIPT, tmp,
                             data_root=os.path.join(tmp, "Zaojing"))
        idsU = {f["id"] for f in repU["findings"]}
        assert idsU == {"appid_guid_unescaped"}, repU["findings"]
        assert repU["verdict"] == "manifest_invalid", repU["verdict"]
        fu = repU["findings"][0]
        # 行号取自夹具自身（AppId 实际所在行），不写死字面量：夹具上方每加一条 [Setup] 指令，
        # 写死的行号就会假红——判据要钉的是"finding 指回 AppId 那一行"，不是"AppId 在第 3 行"。
        appid_line = [i for i, ln in enumerate(UNESCAPED_APPID_SCRIPT.splitlines(), 1)
                      if ln.startswith("AppId=")][0]
        assert fu["severity"] == "error" and fu["evidence"]["line"] == appid_line, fu
        assert fu["evidence"]["expected_form"] == "{{" + GUID_INNER + "}", fu["evidence"]
        assert repU["app_id_effective"] == repE["app_id_effective"], (repU, repE)
        assert repU["uninstall_registry_key"] == repE["uninstall_registry_key"], (repU, repE)
        # 只读安装态探测吃的是报告里的原样 app_id：转义与否不得改变派生键名
        st_esc = install_state(repE["app_id"], "ZaojingAI")
        st_plain = install_state(repU["app_id"], "ZaojingAI")
        assert st_esc["derived_uninstall_key"] == st_plain["derived_uninstall_key"] \
            == "{" + GUID_INNER + "}_is1", (st_esc["derived_uninstall_key"],
                                            st_plain["derived_uninstall_key"])
        res["appid_escape"] = {"escaped": repE["verdict"], "unescaped": repU["verdict"]}

        # 键名派生的第三种形制：不带花括号的 AppId。本机 HKLM 32 位视图里实测存在
        # `Inno Setup 6_is1`（Inno 自家安装项，AppId 即无括号的 "Inno Setup 6"），
        # 故 Inno 的键名规则是 `<AppId>_is1`——只在该值本身以 `{` 起始时才保留括号。
        # 旧实现无条件补括号，遇到无括号 AppId 会永远查不中（同一类仪器缺陷的第二例）。
        assert uninstall_registry_key("Inno Setup 6") == "Inno Setup 6_is1"
        assert uninstall_registry_key("  zaojing-portable ") == "zaojing-portable_is1"
        assert uninstall_registry_key("{%s}" % GUID_INNER) == "{%s}_is1" % GUID_INNER
        assert uninstall_registry_key("{{%s}" % GUID_INNER) == "{%s}_is1" % GUID_INNER

        # 反证（视图）：卸载键只落在 32 位视图（WOW6432Node）时探测必须仍能命中。
        # 本机即为此形态：HKLM 32 位视图里有 Inno 自家安装项，64 位视图没有。旧实现只查
        # 64 位视图，与"PATH 单路判编译器未安装"是同一类仪器缺陷的第三例。
        real_enum, real_read = _enum_uninstall_keys, _read_uninstall_value
        try:
            globals()["_enum_uninstall_keys"] = lambda root, view="64": (
                ["Inno Setup 6_is1"] if (root, view) == ("HKLM", "32") else [])
            globals()["_read_uninstall_value"] = lambda r, k, n, view="64": (
                "Inno Setup version 6.7.3" if n == "DisplayName" else None)
            st32 = install_state("Inno Setup 6", "Inno Setup version 6.7.3")
            assert st32["installed"] is True, "32 位视图命中却判未安装: %s" % st32["probes"]
            assert [p for p in st32["probes"] if p.get("view") == "32" and p.get("present")], st32["probes"]
        finally:
            globals()["_enum_uninstall_keys"] = real_enum
            globals()["_read_uninstall_value"] = real_read
        res["uninstall_key_forms"] = "braced/escaped/bare"
        # 本机自证（不变式，不硬编另一台机器的注册表状态）：只要 32 位视图里确实有该键、
        # 64 位视图里没有，探测就必须判 installed=True。
        if WINDOWS:
            _k64 = _enum_uninstall_keys("HKLM", "64")
            _k32 = _enum_uninstall_keys("HKLM", "32")
            if "Inno Setup 6_is1" in _k32 and "Inno Setup 6_is1" not in _k64:
                live_inno = install_state("Inno Setup 6", "Inno Setup version 6.7.3")
                assert live_inno["installed"] is True, live_inno["probes"]
                res["uninstall_state_live"] = "32-view-only key found"

        # ---- R11/R12：由首次真编译的两行 iscc 告警原文反推的静态规则（Phase 53）----
        # 首次真编译 exit=0，但编译器在 stdout 里另报了两行 Warning：`x64` 架构标识已弃用
        # （自动替换为 x64os，推荐 x64compatible）、PrivilegesRequired 缺省即 admin 却使用
        # per-user 区域。"能编译通过"曾被当成"编译器没有话要说"——这就是把编译面转真时
        # 必须把编译器的话反哺成静态规则的理由。两条规则各带双向臂：缺指令必报、补齐必不报、
        # 合规写法不误伤（x64os 不以子串命中）、单项不成立（admin 但只碰公共区域）。
        Path(tmp, "readme.md").write_text("ZaojingAI readme fixture", encoding="utf-8")
        repPR = verify_script(PER_USER_AREAS_SCRIPT, tmp)
        idsPR = {f["id"]: f for f in repPR["findings"]}
        assert set(idsPR) == {"per_user_area_privileges_mismatch"}, repPR["findings"]
        fPR = idsPR["per_user_area_privileges_mismatch"]
        assert fPR["severity"] == "warn", fPR
        assert fPR["evidence"]["privileges_required_declared"] is None, fPR["evidence"]
        assert fPR["evidence"]["privileges_required_effective"] == "admin", fPR["evidence"]
        assert fPR["evidence"]["per_user_areas_observed"] == [
            "HKCU", "localappdata", "userdocs"], fPR["evidence"]
        assert fPR["evidence"]["compiler_proven"] is True, fPR["evidence"]
        assert "per-user areas" in fPR["evidence"]["iscc_observed"], fPR["evidence"]
        assert repPR["verdict"] == "manifest_warning", repPR["verdict"]
        # 臂二：显式写 admin 不豁免（缺省与显式的生效形制同为 admin，配对同样成立）
        repPR4 = verify_script(PRIV_ADMIN_AREAS_SCRIPT, tmp)
        assert [f["id"] for f in repPR4["findings"]] == [
            "per_user_area_privileges_mismatch"], repPR4["findings"]
        assert repPR4["findings"][0]["evidence"]["privileges_required_declared"] == "admin"
        # 臂三（反证）：同一 per-user 脚本显式 lowest 后必须一条不报
        repPR2 = verify_script(PRIV_LOWEST_AREAS_SCRIPT, tmp)
        assert repPR2["counts"] == {"errors": 0, "warnings": 0, "infos": 0, "total": 0}, repPR2["findings"]
        # 臂四（反证）：只碰公共区域 + HKLM 时缺省 admin 也成立，不得报
        repPR3 = verify_script(PRIV_ADMIN_COMMON_SCRIPT, tmp)
        assert repPR3["counts"]["total"] == 0, repPR3["findings"]
        res["privileges_rule"] = {
            "admin_default_with_per_user": repPR["verdict"],
            "explicit_lowest": repPR2["verdict"],
            "common_areas_only": repPR3["verdict"],
        }

        repAR = verify_script(DEPRECATED_ARCH_SCRIPT, tmp)
        arch = [f for f in repAR["findings"] if f["id"] == "arch_identifier_deprecated"]
        assert len(arch) == 2, repAR["findings"]
        bydir = {f["evidence"]["directive"]: f for f in arch}
        assert sorted(bydir) == ["architecturesallowed",
                                 "architecturesinstallin64bitmode"], bydir
        assert all(f["severity"] == "warn" for f in arch), arch
        a1 = bydir["architecturesallowed"]
        assert a1["subject"] == "ArchitecturesAllowed", a1["subject"]   # 原文拼写须可回显
        assert a1["evidence"]["declared_value"] == "x64", a1["evidence"]
        assert a1["evidence"]["offending_tokens"] == ["x64"], a1["evidence"]
        assert a1["evidence"]["preferred"] == "x64compatible", a1["evidence"]
        assert a1["evidence"]["compiler_proven"] is True, a1["evidence"]
        assert "x64os" in a1["evidence"]["iscc_observed"], a1["evidence"]
        # 反证：改成 x64compatible 两条指令都不再报
        repAR2 = verify_script(ARCH_COMPATIBLE_SCRIPT, tmp)
        assert not any(f["id"] == "arch_identifier_deprecated"
                       for f in repAR2["findings"]), repAR2["findings"]
        # 反证：x64os / x64compatible 是合规标识，判据按整词比较，不得被 "x64" 子串误伤
        repAR3 = verify_script(ARCH_OK_VARIANTS_SCRIPT, tmp)
        assert repAR3["counts"]["total"] == 0, repAR3["findings"]
        res["arch_rule"] = {"deprecated_hits": len(arch),
                           "compatible_verdict": repAR2["verdict"],
                           "variants_verdict": repAR3["verdict"]}

        # ---- R13：[Setup] 缺 AppVersion 与 AppVerName —— 探针第三段实测 exit=2 的编译级致命 ----
        # 同一枚最小探针把 AppVersion 行删掉后，iscc 不再只是告警，而是 Error + Compile aborted
        # 并以 2 退出。这一条此前完全不在静态面里：缺它的脚本会被判成 manifest_ok，而它其实
        # 连安装版都编译不出来——"校验通过"与"能编译"之间 formerly 缺的正是这类硬形制。
        repNV = verify_script(NO_VERSION_SCRIPT, tmp)
        idsNV = {f["id"]: f for f in repNV["findings"]}
        assert set(idsNV) == {"setup_missing_version_directive"}, repNV["findings"]
        fNV = idsNV["setup_missing_version_directive"]
        assert fNV["severity"] == "error", fNV
        assert repNV["verdict"] == "manifest_invalid", repNV["verdict"]
        assert fNV["evidence"]["appversion_present"] is False, fNV["evidence"]
        assert fNV["evidence"]["appvername_present"] is False, fNV["evidence"]
        assert fNV["evidence"]["iscc_exit_code_observed"] == 2, fNV["evidence"]
        assert "AppVerName" in fNV["evidence"]["iscc_observed"], fNV["evidence"]
        # 反证：AppVerName 单独在场（把 AppVersion 换成 AppVerName）同样合规——编译器说的是 "or"
        repVN = verify_script(CLEAN_SCRIPT.replace("AppVersion=1.0.0",
                                                  "AppVerName=ZaojingAI 1.0.0"), tmp)
        assert repVN["counts"]["total"] == 0, repVN["findings"]
        res["version_directive_rule"] = {"missing": repNV["verdict"],
                                        "appvername_only": repVN["verdict"]}

        # ---- R14：写入型 [Registry] 条目必须带卸载撤销声明（Phase 58 本机真卸载实测立项）----
        # 六条臂各盯一个逃逸面：缺声明必报 / 发货形制不报 / 逐值撤销也成立 / 显式永不撤销
        # 属有意让渡不报 / 父键 ifempty 不得替子键洗白（父键永不为空）/ 跨 root 声明不算覆盖。
        repRG = verify_script(REG_RESIDUE_SCRIPT, tmp)
        idsRG = {f["id"]: f for f in repRG["findings"]}
        assert set(idsRG) == {"registry_key_left_after_uninstall"}, repRG["findings"]
        fRG = idsRG["registry_key_left_after_uninstall"]
        assert fRG["severity"] == "error", fRG
        assert repRG["verdict"] == "manifest_invalid", repRG["verdict"]
        evRG = fRG["evidence"]
        assert evRG["root"] == "hkcu" and evRG["subkey"] == "Software\\Zaojing\\ZaojingAI", evRG
        # 行号取自夹具自身（两条写入实际所在行），不写死字面量——与 AppId 臂同一条理由。
        _reg_lines = [i for i, ln in enumerate(REG_RESIDUE_SCRIPT.splitlines(), 1)
                      if ln.startswith("Root: HKCU; Subkey:")]
        assert evRG["write_lines"] == _reg_lines, (evRG, _reg_lines)
        assert evRG["value_names"] == ["InstallLocation", "Version"], evRG
        assert evRG["observed_cleanup_flags"] == [], evRG
        assert "uninsdeletekey" in evRG["accepted_flags"], evRG["accepted_flags"]
        assert fRG["subject"] == "HKCU\\Software\\Zaojing\\ZaojingAI", fRG["subject"]
        # 臂二：发货形制（父键 ifempty + 应用键整键撤销）必须一条不报
        repRC = verify_script(REG_CLEAN_SCRIPT, tmp)
        assert repRC["counts"]["total"] == 0, repRC["findings"]
        # 臂三：逐值 uninsdeletevalue 同样成立
        repRCV = verify_script(REG_CLEAN_PER_VALUE_SCRIPT, tmp)
        assert repRCV["counts"]["total"] == 0, repRCV["findings"]
        # 臂四：显式 uninsneveruninstall 是有意的归属让渡，不得判残留
        repRNV = verify_script(REG_NEVER_SCRIPT, tmp)
        assert repRNV["counts"]["total"] == 0, repRNV["findings"]
        # 臂五：父键 ifempty **不能**替带值的子键洗白（父键因该子键存在而永不为空）
        repRPE = verify_script(REG_PARENT_IFEMPTY_ONLY_SCRIPT, tmp)
        idsRPE = {f["id"]: f for f in repRPE["findings"]}
        assert set(idsRPE) == {"registry_key_left_after_uninstall"}, repRPE["findings"]
        assert idsRPE["registry_key_left_after_uninstall"]["evidence"]["subkey"] \
            == "Software\\Zaojing\\ZaojingAI", idsRPE
        # 臂五之二：父键整键撤销（连子键一并删）则子键已被覆盖，不得报
        repRPD = verify_script(REG_PARENT_DELETEKEY_SCRIPT, tmp)
        assert repRPD["counts"]["total"] == 0, repRPD["findings"]
        # 臂六：撤销声明写在另一个 root（HKLM）上，HKCU 那一路仍属无人撤销
        repRRM = verify_script(REG_ROOT_MISMATCH_SCRIPT, tmp)
        idsRRM = {f["id"] for f in repRRM["findings"]}
        assert "registry_key_left_after_uninstall" in idsRRM, repRRM["findings"]
        # 臂八：ifempty 写在应用键自身上也顶不住——值没被声明删除 ⇒ 键永不为空 ⇒ 永不删
        repRIE = verify_script(REG_IFEMPTY_SAME_KEY_SCRIPT, tmp)
        idsRIE = {f["id"]: f for f in repRIE["findings"]}
        assert set(idsRIE) == {"registry_key_left_after_uninstall"}, repRIE["findings"]
        evRIE = idsRIE["registry_key_left_after_uninstall"]["evidence"]
        assert evRIE["observed_cleanup_flags"] == ["uninsdeletekeyifempty"], evRIE
        assert "uninsdeletekeyifempty" not in evRIE["accepted_flags"], evRIE
        # 同一 base root 的两种拼写（HKCU vs HKCU64）视为同一处键，不得假红
        repRSP = verify_script(REG_SPELLING_VARIANT_SCRIPT, tmp)
        assert repRSP["counts"]["total"] == 0, repRSP["findings"]
        # 臂七（自证）：随包发货脚本若在场，必须零枚本规则命中——这一条在 Phase 58 真机
        # 实测当场判红（app 键三枚值留守），修脚本后才转绿；缺席（冻结 EXE 内无 .iss）
        # 时如实记 absent，绝不把"没查到"写成"合规"。
        _shipped = Path(__file__).resolve().parent / "setup.iss"
        if _shipped.is_file():
            repRS = verify_script(_shipped.read_text(encoding="utf-8"),
                                  str(_shipped.parent),
                                  data_root=os.path.join(tmp, "Zaojing"))
            _r14 = [f for f in repRS["findings"] if f["id"] == "registry_key_left_after_uninstall"]
            assert _r14 == [], {"shipped_setup_iss": str(_shipped), "r14": _r14}
            res["registry_uninstall_rule"] = {"residue": repRG["verdict"],
                                              "shipped": "clean",
                                              "shipped_path": str(_shipped)}
        else:
            res["registry_uninstall_rule"] = {"residue": repRG["verdict"],
                                              "shipped": "script_absent"}
        res["registry_uninstall_rule"]["arms"] = {
            "clean_key": repRC["counts"]["total"], "per_value": repRCV["counts"]["total"],
            "never_uninstall": repRNV["counts"]["total"],
            "parent_ifempty_only_reports": len(repRPE["findings"]),
            "parent_deletekey": repRPD["counts"]["total"],
            "root_mismatch_reports": len([f for f in repRRM["findings"]
                                          if f["id"] == "registry_key_left_after_uninstall"]),
            "ifempty_same_key_reports": len(repRIE["findings"]),
            "root_spelling_variant": repRSP["counts"]["total"],
        }

        rep2 = verify_script(BROKEN_SCRIPT, tmp, data_root=os.path.join(tmp, "Zaojing"))
        assert rep2["verdict"] == "manifest_invalid", rep2["verdict"]
        byid: Dict[str, List[Dict[str, Any]]] = {}
        for f in rep2["findings"]:
            byid.setdefault(f["id"], []).append(f)
        want = {"unresolved_template_constant", "files_source_literal_missing",
                "files_source_wildcard_no_match", "uninstall_target_mismatch",
                "payload_dir_split", "appid_not_uuid",
                "registry_valuedata_literal_placeholder", "needs_compiler_adjudication",
                "dirs_miss_runtime_data_root", "permissions_entry_malformed",
                "per_user_area_privileges_mismatch", "registry_key_left_after_uninstall"}
        missing = want - set(byid)
        assert not missing, "缺规则命中: %s" % sorted(missing)
        pe = byid["permissions_entry_malformed"][0]
        assert pe["evidence"]["entry"] == "usersfull" and pe["evidence"]["section"] == "Dirs", pe["evidence"]
        # BROKEN 里的 per-user 区域写成 user 前缀形制（{userlocalappdata}），与发货脚本的
        # 裸形 {localappdata} 都必须被认出，否则规则只对一种拼写开火、换种拼写即逃逸。
        prB = byid["per_user_area_privileges_mismatch"][0]
        assert prB["evidence"]["per_user_areas_observed"] == [
            "HKCU", "userlocalappdata"], prB["evidence"]
        assert all(f["evidence"]["entry"] == "usersfull"
                   for f in byid["permissions_entry_malformed"]), byid["permissions_entry_malformed"]
        u = [f for f in byid["unresolved_template_constant"] if f["subject"] == "{app_name}"][0]
        assert u["evidence"]["count"] == 4, u["evidence"]
        assert sorted(u["evidence"]["sections"]) == ["Dirs", "Files", "Icons"], u["evidence"]
        d = [f for f in byid["unresolved_template_constant"] if f["subject"] == "{install_date}"][0]
        assert d["evidence"]["count"] == 1, d["evidence"]
        um = byid["uninstall_target_mismatch"][0]
        assert um["evidence"]["expected_from_defaultdirname"] == os.path.join(
            "{autopf}" + os.sep + "company", "unins000.exe"), um["evidence"]
        assert um["evidence"]["declared_filename"] == "{autopf}\\ZaojingAI\\Uninstall.exe"
        assert byid["appid_not_uuid"][0]["evidence"]["non_hex_letters"] == [
            "G", "H", "I", "J", "K", "L", "M", "N", "O", "P"], byid["appid_not_uuid"][0]["evidence"]
        # R14 在破损夹具上同样开火：那枚 ValueData 带未展开 {install_date} 的写入既没
        # 撤销声明也没父键覆盖，一条检出同时留证两处独立缺陷（值污染 + 卸载残留）。
        rgB = byid["registry_key_left_after_uninstall"][0]
        assert rgB["evidence"]["subkey"] == "Software\\company\\ZaojingAI", rgB["evidence"]
        assert rgB["evidence"]["value_names"] == ["InstallDate"], rgB["evidence"]
        assert rgB["evidence"]["observed_cleanup_flags"] == [], rgB["evidence"]
        assert rep2["counts"]["errors"] == 8 and rep2["counts"]["warnings"] == 4, rep2["counts"]
        assert rep2["counts"]["infos"] == 1, rep2["counts"]
        res["broken_findings"] = rep2["counts"]

        # 只读安装态：真实机器上未安装即 installed=False（绝不伪造）
        st = install_state(BROKEN_APPID, "ZaojingAI")
        assert st["derived_uninstall_key"] == BROKEN_APPID + "_is1"
        # 探测面从 2 路（root）扩到 4 路（root × 视图）——Phase 53 的视图修复，
        # 少一路就会把 32 位安装项判成不存在，故把覆盖面本身钉进断言。
        reg_probes = [p for p in st["probes"] if p["probe"] == "registry_uninstall"]
        assert len(reg_probes) == 4, st["probes"]
        assert sorted((p["root"], p["view"]) for p in reg_probes) == [
            ("HKCU", "32"), ("HKCU", "64"), ("HKLM", "32"), ("HKLM", "64")], reg_probes
        assert st["fabricated"] is False
        res["state_installed_real"] = st["installed"]

        # 显示名规则的三形制（与 installer_runtime_engine 同一批实测依据）：真机实写那枚必收、
        # 旧的逐字相等形仍收、无关名与空 app_name 必拒。第三条是第一条的正控——少了它，
        # "以 app_name 起始"会退化成一枚什么都收的钝刀（空串对任何显示名都 startswith 为真）。
        assert display_name_matches("ZaojingAI 1.0.0", "ZaojingAI") is True
        assert display_name_matches("ZaojingAI", "ZaojingAI") is True
        assert display_name_matches("CCleaner", "ZaojingAI") is False
        assert display_name_matches("ZaojingAI 1.0.0", "") is False
        assert display_name_matches("", "") is False
        assert display_name_matches(None, "ZaojingAI") is False

        # 反证：桩构造"注册表命中"，installed 必须随之翻真（判分非硬编码）
        real_enum, real_read = _enum_uninstall_keys, _read_uninstall_value
        try:
            # 桩里的 DisplayName 用**本机真装实测**那枚（"ZaojingAI 1.0.0"）而不是旧的
            # "ZaojingAI"：夹具长得像真实世界，兜底规则才有的可测——两枚名字在逐字相等的
            # 旧规则下只有后者能命中，于是"兜底"这条路径在自测里从未真正跑通过。
            globals()["_enum_uninstall_keys"] = lambda root, view="64": (
                [BROKEN_APPID + "_is1"] if root == "HKCU" else [])
            globals()["_read_uninstall_value"] = lambda r, k, n, view="64": {
                "DisplayName": "ZaojingAI 1.0.0", "DisplayVersion": "1.0.0",
                "UninstallString": "unins000.exe /SILENT"}.get(n)
            st2 = install_state(BROKEN_APPID, "ZaojingAI")
            assert st2["installed"] is True, st2["probes"]
            hit = [p for p in st2["probes"] if p["probe"] == "registry_uninstall" and p["present"]]
            assert hit and hit[0]["display_version"] == "1.0.0", hit
            assert hit[0]["display_name"] == "ZaojingAI 1.0.0", hit

            # 兜底路径（Phase 58 本机真装当场抓出的第 6 枚真缺陷）：app_id 交空时文档承诺
            # "只走 DisplayName 兜底"，而真机上卸载项的显示名是 AppName+" "+AppVersion。
            # 旧规则逐字相等 ⇒ 本机明明装着却报 installed=False。这条路径此前零覆盖，
            # 所以桩必须用实测显示名、且必须覆盖"命中"与"不落空时不许乱认"两侧。
            st_fb = install_state("", "ZaojingAI")
            fb = [p for p in st_fb["probes"] if p["probe"] == "registry_display_name"]
            assert st_fb["installed"] is True and len(fb) == 1, st_fb["probes"]
            assert fb[0]["subkey"] == BROKEN_APPID + "_is1", fb
            assert fb[0]["display_name"] == "ZaojingAI 1.0.0", fb
            assert fb[0]["expected_rule"] == "display_name_startswith_app_name", fb
            # NEG：满盘是别的产品的卸载项时，兜底不许"认领"任何一枚（否则规则会退化
            # 成"看到显示名就算命中"，把别人的安装项写成本机已装造境）。
            foreign = {r"7966B2C3-45B1-414C-A6C6\CCleaner": {"DisplayName": "CCleaner"},
                       r"{1D186B97-1A58-4EAC-8D5F-710E74B9D40B}_is1":
                           {"DisplayName": "Everything 1.4.1993.1022"}}

            def _read_foreign(r, k, n, view="64"):
                return foreign[k].get(n)

            globals()["_enum_uninstall_keys"] = lambda root, view="64": (
                sorted(foreign) if root == "HKCU" else [])
            globals()["_read_uninstall_value"] = _read_foreign
            st_neg = install_state("", "ZaojingAI")
            assert st_neg["installed"] is False and not any(
                p["probe"] == "registry_display_name" for p in st_neg["probes"]), st_neg["probes"]
        finally:
            globals()["_enum_uninstall_keys"] = real_enum
            globals()["_read_uninstall_value"] = real_read

        # 反证：桩构造"通配有命中"，wildcard 规则必须随之不报
        real_glob = _glob_matches
        try:
            globals()["_glob_matches"] = lambda pat: [pat.replace("*", "x")]
            rep3 = verify_script(BROKEN_SCRIPT, tmp, data_root=os.path.join(tmp, "Zaojing"))
            assert not any(f["id"] == "files_source_wildcard_no_match" for f in rep3["findings"]), rep3["findings"]
        finally:
            globals()["_glob_matches"] = real_glob

        # 编译器寻径（Phase 53）：三路按固定顺序 固定目录 → 只读注册表 → PATH，命中者回显
        # 绝对路径与来源，落空者只报 not_located —— "未命中"与"未安装"是两件事，前者是
        # 本仪器的观测结果、后者是需要三路同空才能勉强支撑的环境断言。
        real_exists, real_which, real_reg, real_fixed = \
            _path_exists, _which_iscc, _compiler_from_registry, _compiler_fixed_dirs
        try:
            fake = os.path.join(tmp, "fixed", "ISCC.exe")
            globals()["_compiler_fixed_dirs"] = lambda: [fake]
            globals()["_path_exists"] = lambda p: p == fake
            globals()["_which_iscc"] = lambda: None
            globals()["_compiler_from_registry"] = lambda: None
            loc = locate_compiler()
            assert loc["found"] is True and loc["lookup_mode"] == "fixed_dir", loc
            assert loc["path"] == fake and os.path.isabs(loc["path"]), loc
            assert loc["proves_absence"] is False, loc

            fake_reg = os.path.join(tmp, "reg", "ISCC.exe")
            globals()["_path_exists"] = lambda p: False
            globals()["_compiler_from_registry"] = lambda: fake_reg
            loc2 = locate_compiler()
            assert loc2["lookup_mode"] == "registry" and loc2["path"] == fake_reg, loc2

            fake_path = os.path.join(tmp, "path", "iscc.exe")
            globals()["_compiler_from_registry"] = lambda: None
            globals()["_which_iscc"] = lambda: fake_path
            loc3 = locate_compiler()
            assert loc3["lookup_mode"] == "path" and loc3["path"] == fake_path, loc3

            globals()["_which_iscc"] = lambda: None
            loc4 = locate_compiler()
            assert loc4["found"] is False and loc4["state"] == "not_located", loc4
            assert loc4["proves_absence"] is False, loc4
            assert "未安装" not in loc4["conclusion"], loc4["conclusion"]
            # 交叉核对：PATH 与固定目录同时命中时，来源仍按固定顺序判定，另一路如实留痕
            globals()["_path_exists"] = lambda p: p == fake
            globals()["_which_iscc"] = lambda: fake_path
            loc5 = locate_compiler()
            assert loc5["lookup_mode"] == "fixed_dir", loc5
            assert loc5["sources"]["path"]["hit"] == fake_path, loc5["sources"]
        finally:
            globals()["_path_exists"], globals()["_which_iscc"] = real_exists, real_which
            globals()["_compiler_from_registry"] = real_reg
            globals()["_compiler_fixed_dirs"] = real_fixed

        # 本机自证（不变式而非硬编码期望）：只要磁盘上确实存在编译器，寻径就必须命中；
        # 这正是 Phase 53 前那台仪器答不出的东西（PATH-only 把已安装的编译器判成不存在）。
        on_disk = [p for p in _compiler_fixed_dirs() if _path_exists(p)]
        live = locate_compiler()
        if on_disk:
            assert live["found"] is True and live["lookup_mode"] == "fixed_dir", (on_disk, live)
            assert os.path.isfile(live["path"]), live
            assert live["bytes"] > 0 and live["sha256"], live
            assert iscc_available() is True, "磁盘有编译器却答 False"
        assert (live["found"] is True) or (live["state"] == "not_located"), live
        res["compiler_lookup"] = {"mode": live["lookup_mode"], "fixed_dir_hits": len(on_disk)}

        # ---- 编译面（Phase 53 红线分裂之 installer_artifact_build）----
        # "安装包由本构建链真实产出"这句声称必须建立在对账上：证据文件的每个字段都拿磁盘重算值
        # 比一遍；"编译成功"由三个互相独立的信号同立才成立（退出码 0、输出里真有
        # "Successful compile" 行、产物文件在盘），证据自报的 compiled/fabricated 两个布尔
        # **不参与判定**——自报字段作数是本轮要根除的缺陷类（"探针伪影当环境事实"的镜像面）。
        ok_dir = os.path.join(tmp, "ev_ok")
        ev = write_build_evidence(ok_dir)
        Path(ok_dir, "clean_exe.exe").write_bytes(b"MZ-fixture")   # CLEAN_SCRIPT 声明的载荷
        repA = verify_build_evidence(ev)
        assert repA["verdict"] == "artifact_verified", repA
        assert repA["compile_succeeded"] is True, repA["signals"]
        sig = repA["signals"]
        assert sig["exit_zero"] is True and sig["compile_line_seen"] is True \
            and sig["artifact_present"] is True, sig
        assert repA["script"]["sha256_match"] is True and repA["script"]["bytes_match"] is True
        assert repA["artifact"]["sha256_match"] is True and repA["artifact"]["bytes_match"] is True
        assert repA["warnings"]["consistent"] is True, repA["warnings"]
        assert repA["manifest_now"]["verdict"] == "manifest_ok", repA["manifest_now"]
        assert repA["manifest_now"]["same_as_recorded"] is True
        assert repA["not_install"]["installed_claimed"] is False
        assert repA["fabricated"] is False
        # 臂一：证据自报被反向篡改，判定不得跟着翻（磁盘真值说了算）
        repA2 = verify_build_evidence(dict(ev, compiled=False, fabricated=True))
        assert repA2["verdict"] == "artifact_verified", repA2
        assert repA2["evidence_self_report"]["ignored"] is True, repA2["evidence_self_report"]

        # 臂二：产物原地改一字节（长度不变）→ 只有 sha 能发现，bytes 仍相符。
        # 这条同时钉住"真的重算了 sha256"而不是只比大小（比大小的仪器会漏掉等长替换）。
        art = Path(ev["artifact_path"])
        body = art.read_bytes()
        art.write_bytes(body[:-1] + bytes([body[-1] ^ 0xFF]))
        repB = verify_build_evidence(ev)
        assert repB["verdict"] == "evidence_mismatch", repB
        assert repB["artifact"]["bytes_match"] is True, repB["artifact"]
        assert repB["artifact"]["sha256_match"] is False, repB["artifact"]
        assert "artifact_sha256_mismatch" in [b["code"] for b in repB["blocking"]], repB["blocking"]
        art.write_bytes(body)
        assert verify_build_evidence(ev)["verdict"] == "artifact_verified"
        # 臂三（对侧）：磁盘不动、只篡改证据里的 sha → 同样必须翻红，两向都不放过
        repC = verify_build_evidence(dict(ev, artifact_sha256="0" * 64))
        assert repC["verdict"] == "evidence_mismatch" and repC["artifact"]["sha256_match"] is False
        assert repC["artifact"]["bytes_match"] is True, repC["artifact"]

        # 臂四：退出码非 0 → 未证成编译；产物在盘也不能算成功
        repD = verify_build_evidence(dict(ev, iscc_exit_code=2))
        assert repD["verdict"] == "compile_not_proven", repD
        assert repD["signals"]["exit_zero"] is False and repD["signals"]["artifact_present"] is True
        # 臂五：退出码 0 却输不出 "Successful compile" 行 → 两信号不同立，仍判未证成
        repE2 = verify_build_evidence(write_build_evidence(
            os.path.join(tmp, "ev_noline"), compile_line=False))
        assert repE2["verdict"] == "compile_not_proven", repE2
        assert repE2["signals"]["compile_line_seen"] is False
        assert repE2["signals"]["compile_line_source"] == "absent", repE2["signals"]
        # 反证：只有派生字段、尾部无痕时，来源如实标 derived_field（弱证据但不谎称见过原文）
        ev_df = dict(ev)
        ev_df["iscc_output_tail"] = "Parsing [Setup] section, line 3\n"
        repE3 = verify_build_evidence(ev_df)
        assert repE3["signals"]["compile_line_seen"] is True
        assert repE3["signals"]["compile_line_source"] == "derived_field", repE3["signals"]

        # 臂六：告警计数——编译器说了话而证据记成 0，必须当场翻红；
        # 尾部为截断时按**下界**判（记的不少于尾部实见即不算矛盾），全文时要求相等。
        two = [ISCC_ARCH_WARNING, ISCC_PRIVILEGES_WARNING]
        repF = verify_build_evidence(write_build_evidence(
            os.path.join(tmp, "ev_warn"), warnings=two,
            override={"iscc_warnings": [], "iscc_output_chars": None}))
        assert repF["warnings"]["recorded_count"] == 0, repF["warnings"]
        assert repF["warnings"]["tail_recount"] == 2, repF["warnings"]
        assert repF["warnings"]["consistent"] is False, repF["warnings"]
        assert "warnings_count_mismatch" in [b["code"] for b in repF["blocking"]], repF["blocking"]
        assert repF["verdict"] == "evidence_mismatch", repF["verdict"]
        # 反证：尾部只存了全文的一段（生产者如实标了 iscc_output_chars > 尾部长度）时，
        # 记 2 条、尾部可见 1 条属正常，不得误判成矛盾——把截断当不一致也是假红。
        repG = verify_build_evidence(write_build_evidence(
            os.path.join(tmp, "ev_partial"), warnings=two, tail_full=False,
            override={"iscc_warnings": two}))
        assert repG["warnings"]["tail_is_full_output"] is False, repG["warnings"]
        assert repG["warnings"]["consistent"] is True and repG["verdict"] == "artifact_verified"

        # 臂七：产物文件被删 → artifact_missing（而不是"哈希不符"或"已通过"）
        os.remove(ev["artifact_path"])
        repH = verify_build_evidence(ev)
        assert repH["verdict"] == "artifact_missing", repH
        assert repH["artifact"]["observed"] is None and repH["artifact"]["present"] is False

        # 臂八：编译后又改脚本 → "产物不是由当前脚本编出来的"。这条抓的是最真实的陈旧态：
        # 证据与产物仍然彼此相符，脚本却已经变了；只比"证据 vs 产物"会一路绿灯。
        stale_dir = os.path.join(tmp, "ev_stale")
        ev_stale = write_build_evidence(stale_dir)
        Path(stale_dir, "clean_exe.exe").write_bytes(b"MZ-fixture")
        assert verify_build_evidence(ev_stale)["verdict"] == "artifact_verified"
        Path(ev_stale["script_path"]).write_text(
            _with_setup(CLEAN_SCRIPT, "AppCopyright=zaojing") + "\n", encoding="utf-8")
        repI = verify_build_evidence(ev_stale)
        assert repI["verdict"] == "evidence_mismatch", repI
        assert repI["script"]["sha256_match"] is False and repI["artifact"]["sha256_match"] is True
        assert "artifact_not_from_current_script" in [b["code"] for b in repI["blocking"]], repI["blocking"]

        # 臂九：证据从文件读（生产形态就是 installer/build-evidence.json）
        # 上一臂把产物删了，此处按臂二留存的原始字节复原——本臂要测的是"证据来源=文件"
        # 这一条路径，不是产物缺失；不复原就会把两臂的因由搅在一起（假红的常见来源）。
        Path(ev["artifact_path"]).write_bytes(body)
        ev_file = os.path.join(tmp, "evidence.json")
        Path(ev_file).write_text(json.dumps(ev, ensure_ascii=False), encoding="utf-8")
        repJ = verify_build_evidence(ev_file)
        assert repJ["verdict"] == "artifact_verified" and repJ["evidence_source"]["from"] == "file"
        # 臂十：字段缺失即证据不完整，判 evidence_incomplete 而不是硬抛或假绿
        repK = verify_build_evidence(write_build_evidence(
            os.path.join(tmp, "ev_incomplete"), drop_fields=("artifact_sha256",)))
        assert repK["verdict"] == "evidence_incomplete", repK
        assert "artifact_sha256" in repK["missing_fields"], repK["missing_fields"]

        # 本机自证（不变式，不写死另一台机器的构建状态）：仓库里真编译过的产物与证据若同时在盘，
        # 就必须判 artifact_verified；缺任何一件只说明这台机器没跑过构建，不作结论。
        repo_ev = Path(__file__).resolve().parent / "installer" / "build-evidence.json"
        if repo_ev.is_file():
            repL = verify_build_evidence(str(repo_ev))
            assert repL["verdict"] == "artifact_verified", {
                k: repL[k] for k in ("verdict", "blocking", "signals", "warnings",
                                     "script", "artifact")}
            assert repL["artifact"]["observed"]["bytes"] > 10 ** 7, repL["artifact"]
            res["artifact_face_live"] = {"verdict": repL["verdict"],
                                         "artifact_bytes": repL["artifact"]["observed"]["bytes"],
                                         "warnings_recorded": repL["warnings"]["recorded_count"]}
        res["artifact_face"] = {
            "verified": repA["verdict"],
            "self_report_ignored": repA2["verdict"],
            "byte_flip": repB["verdict"],
            "tampered_evidence": repC["verdict"],
            "exit_nonzero": repD["verdict"],
            "no_compile_line": repE2["verdict"],
            "warnings_undercounted": repF["verdict"],
            "partial_tail_ok": repG["verdict"],
            "artifact_removed": repH["verdict"],
            "script_stale": repI["verdict"],
            "incomplete": repK["verdict"],
            "derived_field_source": repE3["signals"]["compile_line_source"],
        }

        # ---- 预检面（Phase 53 红线分裂之 installer_preflight）----
        # 预检回答的是"若现在按这份脚本装，本机条件够不够"，它**不是**安装：零写入真实
        # 安装根、零注册表、零子进程。三条硬约束在夹具里逐条钉住：
        # (1) 探针未跑即 result=None（"没探测"绝不能写成"不可写"，那是另一种假信号）；
        # (2) 生效装模式由 PrivilegesRequired 派生，而 {autopf} 一类的落点**取决于运行时
        #     是否提权**，故必须同时报 common/user 两个候选，不许给单一真相；
        # (3) 预检通过 ≠ 已安装，产物已证成 ≠ 已安装（执行域自 Phase 58 起归 CLI 执行器，
        #     本面仍只读）。
        def _pf_codes(rep):
            return [b["code"] for b in rep["blocking"]]

        def _pf_unknowns(rep):
            return [u["code"] for u in rep["unknowns"]]

        _pf_real = {k: globals()[k] for k in
                    ("_env_dir", "_token_elevation", "_exclusive_open", "_write_probe",
                     "_disk_free", "_shell_folder_path", "_known_folder_path")}
        try:
            # 活体臂（本机自证，不写死别机状态）：不跑写探针 → 必须是 unknown 而非 ready/blocked
            globals()["_token_elevation"] = lambda: {
                "available": None, "elevated": None, "elevation_type": None,
                "is_admin_account": None,
                "reason": "活体臂不假设本机令牌状态"}
            live_pf = preflight_install(CLEAN_SCRIPT, ok_dir, evidence=ev,
                                       probe_writable=False)
            assert live_pf["verdict"] == "preflight_unknown", live_pf
            assert _pf_codes(live_pf) == [], live_pf["blocking"]
            assert live_pf["checks"]["writability"]["result"] is None, live_pf["checks"]
            assert live_pf["checks"]["writability"]["probed"] is False
            assert "write_probe_unprobed" in _pf_unknowns(live_pf), live_pf["unknowns"]
            assert live_pf["checks"]["privileges"]["effective"] == "lowest", live_pf["checks"]
            assert live_pf["checks"]["privileges"]["result"] is None
            assert live_pf["not_install"]["installed_claimed"] is False
            assert live_pf["not_install"]["preflight_is_not_install"] is True
            assert live_pf["fabricated"] is False
            res["preflight_live"] = {"verdict": live_pf["verdict"],
                                     "unknowns": _pf_unknowns(live_pf),
                                     "candidates": len(live_pf["checks"]["install_root"]["candidates"])}

            # 桩定臂：环境/令牌/写探针/占用/磁盘全桩，逐项验证判据本身
            globals()["_env_dir"] = lambda name: {
                "LOCALAPPDATA": tmp, "PROGRAMFILES": os.path.join(tmp, "PF64"),
                "APPDATA": tmp, "USERPROFILE": tmp, "TEMP": tmp}.get(name, "")
            globals()["_shell_folder_path"] = lambda csidl: None
            globals()["_known_folder_path"] = lambda guid: None
            globals()["_disk_free"] = lambda path: 10 ** 12
            globals()["_exclusive_open"] = lambda path: (True, 0)
            globals()["_write_probe"] = lambda path: (True, {"via": "stub"})
            globals()["_token_elevation"] = lambda: {
                "available": True, "elevated": False, "elevation_type": 3,
                "is_admin_account": False, "reason": None}
            # {autopf} 的两个候选：commonpf（环境 ProgramFiles）与 userpf（{localappdata}\Programs）
            # 夹具本身就用 {autopf}，不再造第二条 DefaultDirName（setup_map 后写覆盖前写，
            # 用 _with_setup 追加同名指令会造出"两条都在、谁生效靠顺序"的隐形夹具 bug）。
            cand_script = CLEAN_SCRIPT
            cands = default_dir_candidates(parse_script(cand_script))
            assert len(cands) == 2, cands
            forms = {c["inno_form"] for c in cands}
            assert forms == {"commonpf", "userpf"}, cands
            assert all(c["path"] for c in cands), cands
            assert all("basis" in c and c["basis"] for c in cands), cands
            # 非 auto 常量只有一个落点：把 {autopf} 换成 {localappdata} 后候选必须塌成一个
            single = default_dir_candidates(parse_script(
                cand_script.replace("DefaultDirName={autopf}\\Zaojing",
                                    "DefaultDirName={localappdata}\\Zaojing")))
            assert len(single) == 1 and single[0]["inno_form"] == "localappdata", single
            res["preflight_candidates"] = {"autopf": len(cands), "localappdata": len(single)}

            # 生效 PrivilegesRequired 派生（与 R11 同源，缺省即 admin、大小写与空格不敏感）
            assert effective_privileges_required(parse_script(CLEAN_SCRIPT))["effective"] == "lowest"
            assert effective_privileges_required(parse_script(PRIV_ADMIN_AREAS_SCRIPT))["effective"] == "admin"
            assert effective_privileges_required(parse_script(PER_USER_AREAS_SCRIPT))["effective"] \
                == DEFAULT_PRIVILEGES_REQUIRED, "缺省指令时必须按 Inno 的默认 admin 生效"
            spaced = effective_privileges_required(parse_script(CLEAN_SCRIPT.replace(
                "PrivilegesRequired=lowest", "PrivilegesRequired= Lowest ")))
            assert spaced["effective"] == "lowest", spaced

            pf_dir = os.path.join(tmp, "pf_ready")
            os.makedirs(pf_dir, exist_ok=True)
            ok = preflight_install(cand_script, ok_dir, evidence=ev,
                                   target_override=pf_dir)
            assert ok["verdict"] == "preflight_ready", ok
            assert _pf_codes(ok) == [], ok["blocking"]
            assert ok["checks"]["privileges"]["result"] is True, ok["checks"]
            assert ok["checks"]["writability"]["result"] is True
            assert ok["checks"]["writability"]["probed"] is True
            assert ok["checks"]["artifact"]["state"] == "verified"
            assert ok["checks"]["target_conflict"]["result"] is True
            assert ok["checks"]["disk_space"]["result"] is True
            assert ok["checks"]["payload"]["result"] is True
            assert ok["not_install"]["installed_claimed"] is False

            # 反证一：要 admin 而令牌显示未提权 → 阻断，且绝不下调成"大概可以"
            globals()["_token_elevation"] = lambda: {
                "available": True, "elevated": False, "elevation_type": 2,
                "is_admin_account": False, "reason": None}
            adm_script = cand_script.replace("PrivilegesRequired=lowest",
                                             "PrivilegesRequired=admin")
            assert adm_script != cand_script and "PrivilegesRequired=admin" in adm_script, \
                "夹具替换必须真发生，否则两臂测的是同一份脚本"
            no_priv = preflight_install(adm_script, ok_dir, evidence=ev,
                                        target_override=pf_dir)
            assert no_priv["verdict"] == "preflight_blocked", no_priv
            assert "privileges_insufficient" in _pf_codes(no_priv), no_priv["blocking"]
            assert no_priv["checks"]["privileges"]["result"] is False
            # 反证二：同一脚本，提权后即消解（说明那条阻断真是令牌派生的，不是写死的）
            globals()["_token_elevation"] = lambda: {
                "available": True, "elevated": True, "elevation_type": 1,
                "is_admin_account": True, "reason": None}
            assert preflight_install(adm_script, ok_dir, evidence=ev,
                                     target_override=pf_dir)["verdict"] == "preflight_ready"

            # 反证三：令牌状态探测不了 → unknown（"读不到"不等于"没有权限"，也 ≠ "有权限"）
            globals()["_token_elevation"] = lambda: {
                "available": None, "elevated": None, "elevation_type": None,
                "is_admin_account": None, "reason": "OpenProcessToken 不可用（非 Windows/接口异常）"}
            unp = preflight_install(adm_script, ok_dir, evidence=ev, target_override=pf_dir)
            assert unp["verdict"] == "preflight_unknown", unp
            assert _pf_codes(unp) == [], unp["blocking"]
            assert "elevation_state_unresolved" in _pf_unknowns(unp), unp["unknowns"]
            globals()["_token_elevation"] = lambda: {
                "available": True, "elevated": False, "elevation_type": 3,
                "is_admin_account": False, "reason": None}

            # 反证四：目标目录不可写 → 阻断；探针报错但无法归因 → unknown，不写"不可写"
            globals()["_write_probe"] = lambda path: (False, {"os_error": 5, "via": "stub"})
            nowrite = preflight_install(cand_script, ok_dir, evidence=ev,
                                        target_override=pf_dir)
            assert nowrite["verdict"] == "preflight_blocked"
            assert "target_not_writable" in _pf_codes(nowrite), nowrite["blocking"]
            globals()["_write_probe"] = lambda path: (None, {"reason": "探针无法归因", "via": "stub"})
            unpwrite = preflight_install(cand_script, ok_dir, evidence=ev,
                                         target_override=pf_dir)
            assert unpwrite["verdict"] == "preflight_unknown", unpwrite
            assert _pf_codes(unpwrite) == [], unpwrite["blocking"]
            assert unpwrite["checks"]["writability"]["result"] is None
            assert "write_probe_unresolved" in _pf_unknowns(unpwrite), unpwrite["unknowns"]
            globals()["_write_probe"] = lambda path: (True, {"via": "stub"})

            # 反证五：磁盘不够 → 阻断，且需求量是量出来的（产物字节 + 声明载荷字节）
            globals()["_disk_free"] = lambda path: 1
            nospace = preflight_install(cand_script, ok_dir, evidence=ev,
                                        target_override=pf_dir)
            assert "space_insufficient" in _pf_codes(nospace), nospace["blocking"]
            assert nospace["checks"]["disk_space"]["result"] is False
            assert nospace["checks"]["disk_space"]["required_bytes"] > 1
            globals()["_disk_free"] = lambda path: None
            unp_space = preflight_install(cand_script, ok_dir, evidence=ev,
                                          target_override=pf_dir)
            assert unp_space["verdict"] == "preflight_unknown"
            assert "disk_free_unresolved" in _pf_unknowns(unp_space), unp_space["unknowns"]
            globals()["_disk_free"] = lambda path: 10 ** 12

            # 反证六：安装根被同名**文件**占着 → 阻断（目录建不出来，不是"权限不够"）
            taken = os.path.join(tmp, "taken")
            Path(taken).write_text("not a directory", encoding="utf-8")
            asfile = preflight_install(cand_script, ok_dir, evidence=ev, target_override=taken)
            assert "target_exists_as_file" in _pf_codes(asfile), asfile["blocking"]
            assert asfile["verdict"] == "preflight_blocked"
            # 反证七：父级不是目录、目标建不出来 → cannot_create_target（与"不可写"分码）
            globals()["_write_probe"] = lambda path: (False, {"os_error": 3,
                                                              "mkdir_failed": True})
            nocreate = preflight_install(cand_script, ok_dir, evidence=ev,
                                         target_override=os.path.join(tmp, "no", "such"))
            assert "cannot_create_target" in _pf_codes(nocreate), nocreate["blocking"]
            globals()["_write_probe"] = lambda path: (True, {"via": "stub"})

            # 反证八：目标 exe 正被占用（本机实测 error 32 = ERROR_SHARING_VIOLATION）→ 阻断
            globals()["_exclusive_open"] = lambda path: (False, 32)
            Path(pf_dir, "Zaojing.exe").write_bytes(b"MZ-fixture")
            inuse = preflight_install(cand_script, ok_dir, evidence=ev,
                                      target_override=pf_dir)
            assert "target_exe_in_use" in _pf_codes(inuse), inuse["blocking"]
            assert inuse["checks"]["target_conflict"]["result"] is False
            _held_detail = inuse["checks"]["target_conflict"]["detail"]
            assert _held_detail["exe_checked"][0]["os_error"] == 32, _held_detail
            assert _held_detail["exe_checked"][0]["exclusive"] is False
            assert _held_detail["held"] == [str(Path(pf_dir, "Zaojing.exe"))], _held_detail
            # 对侧：本机实测"文件不存在"是 err=2（ERROR_FILE_NOT_FOUND），属"未被占用"而非占用。
            # 把 2 也当成阻断，就是把全新安装判成装不了——两向都要钉。
            globals()["_exclusive_open"] = lambda path: (False, 2)
            notheld = preflight_install(cand_script, ok_dir, evidence=ev,
                                        target_override=pf_dir)
            assert "target_exe_in_use" not in _pf_codes(notheld), notheld["blocking"]
            assert notheld["checks"]["target_conflict"]["result"] is True

            # 反证九：产物没被证成 → 预检不放行（预检的前提是"有可装的东西"）
            globals()["_exclusive_open"] = lambda path: (True, 0)
            noart = preflight_install(cand_script, ok_dir, evidence=dict(ev, iscc_exit_code=2),
                                      target_override=pf_dir)
            assert "artifact_not_verified" in _pf_codes(noart), noart["blocking"]
            assert noart["checks"]["artifact"]["state"] == "not_verified"
            missingart = preflight_install(cand_script, ok_dir,
                                           evidence=dict(ev, artifact_path=os.path.join(tmp, "gone.exe")),
                                           target_override=pf_dir)
            assert missingart["checks"]["artifact"]["state"] == "missing", missingart["checks"]
            assert "artifact_missing" in _pf_codes(missingart), missingart["blocking"]
            nosupply = preflight_install(cand_script, ok_dir, target_override=pf_dir)
            assert nosupply["checks"]["artifact"]["state"] == "not_supplied"
            assert "artifact_missing" in _pf_codes(nosupply), nosupply["blocking"]

            # 反证十：载荷缺失必须让 payload 翻红（复用 R2 的判据，不另起一套口径）
            empty_root = os.path.join(tmp, "no_payload")
            os.makedirs(empty_root, exist_ok=True)
            nopayload = preflight_install(cand_script, empty_root, evidence=ev,
                                          target_override=pf_dir)
            assert nopayload["checks"]["payload"]["result"] is False, nopayload["checks"]
            assert "payload_source_missing" in _pf_codes(nopayload), nopayload["blocking"]

            # 真接缝臂：写探针/磁盘余量/独占打开全用生产实现（只对本机临时目录，
            # 绝不拿真实安装根当探针目标），证明桩之外的代码本身也真能跑。
            for _k in ("_token_elevation", "_write_probe", "_disk_free", "_exclusive_open"):
                globals()[_k] = _pf_real[_k]
            real_dir = os.path.join(tmp, "pf_real_dir")
            real_pf = preflight_install(cand_script, ok_dir, evidence=ev,
                                        target_override=real_dir)
            assert real_pf["checks"]["writability"]["result"] is True, real_pf["checks"]
            assert real_pf["checks"]["writability"]["detail"]["via"] != "stub"
            assert real_pf["checks"]["disk_space"]["free_bytes"] > 0
            assert real_pf["checks"]["target_conflict"]["result"] is True
            assert real_pf["verdict"] == "preflight_ready", real_pf
            # 父级是文件 → 生产实现必须归因为 cannot_create_target（真 os_error，不是桩）
            real_bad = preflight_install(cand_script, ok_dir, evidence=ev,
                                         target_override=os.path.join(taken, "sub"))
            assert "cannot_create_target" in _pf_codes(real_bad), real_bad["blocking"]
            _wp = real_bad["checks"]["writability"]
            assert _wp["result"] is False and _wp["detail"]["mkdir_failed"] is True, _wp
            assert isinstance(_wp["detail"]["os_error"], int) and _wp["detail"]["os_error"], _wp
            # 本机实测：父级是文件时 CreateDirectory 给的是 WinError 183(ERROR_ALREADY_EXISTS)，
            # 而不是教科书式的 3/21/123——Windows 在这个分支上不统一，故判据钉「归类」
            # （mkdir 失败 → cannot_create_target）而不钉具体号，号只回显为观察值。
            res["preflight_real_os_error"] = {"code": _wp["detail"]["os_error"],
                                              "exception": _wp["detail"]["exception"]}
            res["preflight_face"] = {
                "live": live_pf["verdict"],
                "ready": ok["verdict"],
                "privileges_blocked": no_priv["verdict"],
                "privileges_unresolved": unp["verdict"],
                "not_writable": nowrite["verdict"],
                "write_unresolved": unpwrite["verdict"],
                "space_blocked": nospace["verdict"],
                "exists_as_file": asfile["verdict"],
                "cannot_create": nocreate["verdict"],
                "in_use": inuse["verdict"],
                "artifact_not_verified": noart["verdict"],
                "payload_missing": nopayload["verdict"],
                "real_seams": real_pf["verdict"],
            }
        finally:
            for _k, _v in _pf_real.items():
                globals()[_k] = _v

        for bad_text in (None, 123, "", "   "):
            try:
                verify_script(bad_text if isinstance(bad_text, str) else bad_text, tmp)
                raise AssertionError("非法脚本内容必须被拒绝: %r" % (bad_text,))
            except ManifestError:
                pass
        for bad_root in ("", "   ", 7):
            try:
                verify_script(CLEAN_SCRIPT, bad_root)
                raise AssertionError("非法 payload_root 必须被拒绝: %r" % (bad_root,))
            except ManifestError:
                pass
        try:
            parse_script(123)
            raise AssertionError("非文本脚本必须被拒绝")
        except ManifestError:
            pass
        try:
            install_state(None, "X")
            raise AssertionError("非字符串 app_id 必须被拒绝")
        except ManifestError:
            pass
        res["rejects"] = "ok"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return res


if __name__ == "__main__":
    info = self_check()
    print("[OK] installer_manifest_engine self_check: %s" % info)
    sys.exit(0)
