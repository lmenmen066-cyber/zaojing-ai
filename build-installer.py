"""
造境 AI 设计智能体 V1.0 - 安装程序打包脚本（支持标准版 + 便携版）
===============================================================

功能:
1. PyInstaller 打包 Python 应用为可执行文件
2. Inno Setup 制作 Windows 安装包（标准版）
3. 生成便携版本（无需安装，解压即用）
4. 生成完整文档和说明

运行方式:
    python build-installer.py
    
输出产物:
- dist/ZaojingAI.exe - 独立可执行文件
- installer/zaojing-ai-1.0.8-setup.exe - Windows 安装包（标准版）
- portable/zaojing-ai-1.0.8-portable.zip - 便携版本压缩包
- 安装说明.md - 详细安装指南
"""

import os
import sys
import subprocess
import json
import hashlib
from pathlib import Path
from string import Template
from datetime import datetime

# ----------------------------------------------------------------------------
# 输出编码防护：Windows GBK 控制台下，替换符 (\ufffd) 等字符会导致 print 崩溃。
# 保持控制台原生编码，但将无法编码的字符转为转义序列而非抛错。
# ----------------------------------------------------------------------------
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors='backslashreplace')
    except Exception:
        pass

# ============================================================================
# 配置信息
# ============================================================================

PROJECT_NAME = "造境 AI 设计智能体"
APP_NAME = "ZaojingAI"
VERSION = "1.0.8"
COMPANY_NAME = "造境科技"
AUTHOR = "QoderWork AI Agent"

# 安装器 AppId（Phase 53 更正，Phase 49 修正的续）：写在 .iss 里的**字面形制**。
# Inno 把值起始的单个 `{` 当常量展开，字面 GUID 必须把首花括号双写成 `{{`——本机真编译器
# 实测：单写 exit=2 并报 "Unknown constant"，双写 exit=0。此前 30 轮声称"本机无 iscc、
# 安装版从未编译"，因此这条致命形制从未被任何工具看见过（该前提本身是错的，见 P53 探针）。
# 生效值仍是 {D4C8A1F2-…}，故卸载键 {D4C8A1F2-…}_is1 未变，历史探测结论不受影响。
# 旧值 {9A8B7C6D-5E4F-3G2H-1I0J-K9L8M7N6O5P4} 含 G/H/I/J/K/L/M/N/O/P 十个非十六进制字母，
# 按 GUID 解析的清点工具会失配；换为合法 GUID 前已实测本机 HKCU/HKLM 卸载子键零匹配旧值。
INSTALLER_APP_ID = "{{D4C8A1F2-7B3E-4E5A-9C6D-2F1B8A47E3D5}"

# 源目录和目标目录
SCRIPT_DIR = Path(__file__).parent
OUTPUT_DIR = SCRIPT_DIR / "dist"
BUILD_DIR = SCRIPT_DIR / "build"
INSTALLER_DIR = SCRIPT_DIR / "installer"
PORTABLE_DIR = SCRIPT_DIR / "portable"

# 主程序入口
MAIN_SCRIPT = SCRIPT_DIR / "agent_service.py"

# 引擎源码 -> 可导入模块名（连字符文件名不能被 Python 导入，PyInstaller 也无法收集；
# 打包前由 prepare_core_modules() 同步为扁平模块名，agent_service.py 直接静态导入）
CORE_MODULES = {
    "P2-asset-engine.py": "asset_engine.py",
    "P2-print-qa-engine.py": "print_qa_engine.py",
    "P2-workflow-engine.py": "workflow_engine.py",
    "P2-model-router.py": "model_router.py",
    "P1-security-complete.py": "security_engine.py",
    "P2-crash-recovery.py": "recovery_engine.py",
    "P2-knowledge-rag.py": "knowledge_rag_engine.py",
    "P2-typography.py": "typography_engine.py",
    "P2-design-review.py": "design_review_engine.py",
    "P2-customer-revision.py": "revision_engine.py",
    "P2-color.py": "color_engine.py",
    "P2-layout.py": "layout_engine.py",
    "P2-packaging.py": "packaging_engine.py",
    "P2-auto-update.py": "update_engine.py",
    "P2-nas.py": "nas_engine.py",
    "P2-ai-provider.py": "ai_provider_engine.py",
    "P2-adobe-bridge.py": "adobe_bridge_engine.py",
    "P2-evaluation-center.py": "evaluation_engine.py",
    "P2-brand-intelligence.py": "brand_engine.py",
    "P2-installer-runtime.py": "installer_runtime_engine.py",
    "P2-installer-manifest.py": "installer_manifest_engine.py",
    # 已是合法模块名，无需重命名但纳入完整性校验:
    # license_service.py / config_persistence.py
    # design_compare_engine.py / background_removal_engine.py（Phase 38 自服务层逐字下沉，
    # 无 P2 源文件，PyInstaller 经 agent_service 静态导入分析收集）
    # panel_recognition_engine.py（Phase 47 第 25 引擎，全新独立模块，同上经静态导入收集）
    # desktop_hub_engine.py（Phase 50 第 27 引擎，全新独立模块，同上经静态导入收集；
    # 它 import tkinter 只发生在 open_hub() 函数体内——PyInstaller 的模块图扫的是全部
    # 字节码里的 IMPORT_NAME，函数级导入同样收得到，且 hook-_tkinter 会把 tcl/tk 脚本
    # 与 tk8.6.dll/_tkinter.pyd 一并带进包。此声称的实证在 TOC：见 _tkinter_toc_audit）
    # service_registry_engine.py（Phase 80 第 28 引擎：feature_flags/tool_registry/skill_runtime
    # 三能力逐字下沉的持久化配置模块，纯 stdlib、无 P2 源文件，agent_service 顶层
    # `import service_registry_engine` 静态导入，PyInstaller 经模块图收集，与上两枚同法）
}

# Inno Setup 脚本模板
# ---------------------------------------------------------------------------
# Phase 49 起改用 string.Template（$name 占位）。原先的 str.format 与 Inno 自身的
# 花括号常量语法相撞：模板里每个 {autopf} 都得写成 {{autopf}}，而"值里再嵌一个
# {python_key}"的写法在单遍替换后会把 {app_name} 原样留在产物里。这类残留自项目起
# 从未暴露——直到第 26 引擎 installer_manifest_engine 上线当轮才抓到（旧模板产出的
# setup.iss 报了 14 项，
# 其中 {app_name}/{install_date} 残留 15 处、4 条零命中通配 Source、卸载入口指向
# 根本不存在的 {autopf}\{app_name}\Uninstall.exe、载荷落在安装根之外、
# Permissions: usersfull 不合 Inno 形态、AppId 含非十六进制字母）。
# 更正（Phase 53）：此处历轮写的"本机一直没有 iscc、安装版从未真正编译过"是**错的**，
# 起因是寻径只用 shutil.which（PATH 单路）；三路寻径（固定目录→只读注册表→PATH）
# 实测 ISCC.exe 自 2026-07-19 起就装在 %ProgramFiles(x86)%\Inno Setup 6，setup.iss
# 已于 Phase 53 首次真编译（证据见 installer\build-evidence.json，编译面由
# GET /api/v1/installer/artifact 逐字段与磁盘重算对账）。
# 本模板已由该校验器复核为零 error / 零 warning；仅剩的编译器专属判断不在此列。
# ---------------------------------------------------------------------------
INNO_SETUP_SCRIPT = Template("""
; 造境 AI 设计智能体 V1.0 安装包
; ================================
; 本文件由 build-installer.py 生成，请勿手改；生成后可经
; GET /api/v1/installer/manifest/verify 做只读静态校验。

#define MyAppName "$project_name"
#define MyAppVersion "$version"
#define MyAppPublisher "$publisher"
#define MyAppURL "$url"
#define MyAppExeName "$app_name.exe"

[Setup]
; AppId 决定卸载注册项 <AppId>_is1 的标识。此处必须双写首花括号（GUID 前两个左括号）：
; 单个 {{ 解为一个字面 {，写单括号会被当常量展开、编译器直接拒绝（本机真编译实测 exit=2）。
AppId=$appid
AppName=$app_name
AppVersion=$version
AppVerName=$app_name $version
AppPublisher=$publisher
AppPublisherURL=$url
AppSupportURL=https://forum.qoder.com/
AppUpdatesURL=https://example.com/zaojing/updates
DefaultDirName={autopf}\\$company_name
DefaultGroupName=$app_name
AllowNoIcons=yes
OutputDir=installer
OutputBaseFilename=zaojing-ai-$version-setup
Compression=lzma
SolidCompression=yes
WizardStyle=modern
; 生效装模式与 per-user 区域的配对由本机真编译器定论（Phase 53）。脚本触及 HKCU、
; localappdata、userdocs 三类 per-user 区域，而 PrivilegesRequired 缺省即 admin，iscc 首次
; 真编译当场告警：「the directive "PrivilegesRequired" is set to "admin" but per-user areas
; (HKCU,localappdata,userdocs) are used by the script」。提权运行时这些写入落在提权账户而非
; 目标用户，表现为「装完却看不见数据根」。本产品的数据根就在当前用户的 LOCALAPPDATA 下，
; 因此显式取 lowest：装模式与运行时同账户，补齐后二次真编译为零告警（编译器实测）。
PrivilegesRequired=lowest
; 架构标识同样由编译器定论：x64 已弃用，iscc 原话是「Architecture identifier "x64" is
; deprecated. Substituting "x64os", but note that "x64compatible" is preferred」，故按推荐
; 写法取 x64compatible（语义差别真实存在：x64 只匹配纯 64 位 Windows，兼容形态不含）。
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
; 语言条目由本机构建环境的真编译器定论（Phase 53）：compiler: 前缀相对 Inno 安装目录解析，
; ChineseSimplified.isl 实测存在于 Inno 安装目录的 Languages 子目录下，iscc 接受该条目并 exit=0；缺该文件的机器
; 会在编译期直接报错，因此这里不做"无从核验"的回避声明，也不做无据的可用性承诺。
; 中文在前=向导默认语言，english 保留为回退项。
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
; 载荷一律落在 {app}（即 DefaultDirName 解析出的安装根），升级/卸载按同一定位；
; 只声明磁盘上确实存在的条目，零命中的通配 Source 会让 Inno 编译期直接报错。
Source: "dist\\$app_name.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "安装说明.md"; DestName: "安装说明.md"; DestDir: "{userdocs}\\$app_name"; Flags: isreadme
Source: "V1.0-功能完成度审计报告.md"; DestName: "功能完成度审计报告.md"; DestDir: "{userdocs}\\$app_name"; Flags: isreadme

[Icons]
Name: "{group}\\$app_name"; Filename: "{app}\\$app_name.exe"; WorkingDir: "{app}"
Name: "{group}\\文档"; Filename: "{userdocs}\\$app_name\\安装说明.md"
; {uninstallexe} 是 Inno 对卸载器真实位置（{app}\\unins000.exe）的内置常量，
; 硬写别的路径必然指不到文件。
Name: "{group}\\卸载"; Filename: "{uninstallexe}"

[Dirs]
; 预建目录与服务运行时真实数据根 %LOCALAPPDATA%\\Zaojing 对齐（见 config_persistence
; 的 WINDOWS_DATA_ROOT），Permissions 用 Inno 的 <组>-<级别> 形态。
Name: "{localappdata}\\Zaojing\\Assets"; Permissions: users-modify
Name: "{localappdata}\\Zaojing\\Cache"; Permissions: users-modify
Name: "{localappdata}\\Zaojing\\Logs"; Permissions: users-modify
Name: "{localappdata}\\Zaojing\\config"; Permissions: users-modify

[Registry]
; 卸载撤销声明：Inno 的 [Registry] 默认**只写不删**。Phase 58 本机真卸载实测（缺陷 #8）：
; unins000.exe 以 0 退出、安装根与 HKCU 卸载子键双双归零、四棵目录树清空，而下面三枚值
; 原样留在 HKCU\\Software\\$company_name\\$app_name 里，执行器据此判 uninstall_incomplete
; 并轮询满 180 s。键级 uninsdeletekey 连同其值与子键一并撤销；厂商父键只用
; uninsdeletekeyifempty（仅当为空才删），绝不抹掉非本产品写的东西——与 Inno 自带
; Examples\\Example3.iss 的写法同构。本产品运行时全程只读注册表（零 SetValue，本轮逐字
; 检索确认），故此处撤销结构上只能删到自己写入的三枚值。
Root: HKCU; Subkey: "Software\\$company_name"; Flags: uninsdeletekeyifempty
Root: HKCU; Subkey: "Software\\$company_name\\$app_name"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\\$company_name\\$app_name"; \\
    ValueType: string; ValueName: "Version"; ValueData: "$version"
Root: HKCU; Subkey: "Software\\$company_name\\$app_name"; \\
    ValueType: string; ValueName: "InstallLocation"; ValueData: "{app}"
Root: HKCU; Subkey: "Software\\$company_name\\$app_name"; \\
    ValueType: string; ValueName: "InstallDate"; ValueData: "$install_date"

[Run]
Filename: "{app}\\$app_name.exe"; Description: "{cm:LaunchProgram,$app_name}"; Flags: nowait postinstall skipifsilent
""")

# ============================================================================
# 工具函数
# ============================================================================

def print_section(title: str):
    """打印章节标题"""
    print("\n" + "=" * 70)
    print(f"  {title}")
    print("=" * 70 + "\n")

def run_command(command: list, description: str = ""):
    """运行命令并显示进度"""
    if description:
        print(f"\n[INFO] {description}")
    
    print(f"[CMD] 执行：{' '.join(command)}")
    print("-" * 70)
    
    result = subprocess.run(command, shell=True, text=True, 
                          capture_output=True, encoding='utf-8', errors='replace')
    
    if result.returncode != 0:
        print(f"[FAIL] 失败！错误代码：{result.returncode}")
        if result.stderr:
            print("错误输出:")
            print(result.stderr)
        return False
    
    print("[PASS] 成功完成\n")
    return True

def _pkg_version(name: str):
    """进程内读取已安装包版本（避免调用 pip.exe 启动器——它在 3.14 偶发死锁挂起）。"""
    try:
        from importlib.metadata import version
        return version(name)
    except Exception:
        return None


def _tool_on_path(exe: str):
    import shutil
    return shutil.which(exe)


def _iscc_lookup() -> dict:
    """Inno 编译器寻径：复用第 26 引擎的三路结果（固定目录 → 只读注册表 → PATH）。

    Phase 53 前的实现只问 shutil.which，PATH 未命中即被写成"未安装"这一环境事实，
    并据此跳过安装版、报告缺失、发货文档道歉——而编译器一直在盘上。此处按引擎的三路
    结果判定；导入失败（如换目录调用）时降级为 PATH 单路并**如实标注降级与证据面**，
    降级结论只说"未定位"，绝不再冒充"未安装"。
    """
    try:
        if str(SCRIPT_DIR) not in sys.path:
            sys.path.insert(0, str(SCRIPT_DIR))
        import installer_manifest_engine as _ime
        info = dict(_ime.locate_compiler(observe_binary=False))
        info["lookup_source"] = "installer_manifest_engine"
        return info
    except Exception as exc:
        hit = _tool_on_path("iscc")
        return {"found": bool(hit), "path": hit,
                "lookup_mode": "path" if hit else "none",
                "state": "located" if hit else "not_located",
                "proves_absence": False,
                "lookup_source": "path_only_fallback",
                "fallback_error": "%s: %s" % (type(exc).__name__, exc)}


def check_dependencies():
    """检查必要依赖是否已安装（全程进程内/带超时，绝不调用会挂起的 pip.exe）"""
    print_section("检查依赖")

    dependencies = {
        "Python": sys.version.split()[0],
        "PyInstaller": None,
        "Inno Setup": None,
    }
    print(f"[OK] Python {sys.version.split()[0]}")

    # PyInstaller：进程内查版本
    pyi = _pkg_version("pyinstaller")
    dependencies["PyInstaller"] = pyi
    if pyi:
        print(f"[OK] PyInstaller {pyi}")
    else:
        print("[WARN] PyInstaller 未检测到（将尝试安装）")

    # Inno Setup：三路寻径定位编译器（固定目录 → 只读注册表 → PATH），再用实测绝对路径问版本
    loc = _iscc_lookup()
    if loc["found"]:
        try:
            subprocess.run([loc["path"], '/ver'], capture_output=True, text=True, timeout=5)
            dependencies["Inno Setup"] = "已安装"
            print("[OK] Inno Setup 已定位：%(path)s（来源=%(mode)s，寻径=%(src)s）" % {
                "path": loc["path"], "mode": loc["lookup_mode"], "src": loc["lookup_source"]})
        except Exception:
            print("[WARN] Inno Setup 探测超时，标准安装版步骤将优雅跳过")
    else:
        print("[WARN] Inno Setup 编译器未定位（三路寻径均未命中，仅描述寻径结果，"
              "不等于本机未安装）（便携版照常产出）")
        if loc.get("lookup_source") == "path_only_fallback":
            print("       寻径降级为 PATH 单路：%s" % loc.get("fallback_error"))

    return True

def install_dependencies():
    """安装缺失的依赖（用 python -m pip + 超时，绕开 pip.exe 启动器死锁）"""
    print_section("安装依赖")

    deps_to_install = []
    if not _pkg_version("pyinstaller"):
        deps_to_install.append('pyinstaller>=6.0.0')

    # 安装缺失的依赖
    if deps_to_install:
        print(f"正在安装：{', '.join(deps_to_install)}")
        success = run_command([sys.executable, '-m', 'pip', 'install', '-U'] + deps_to_install)
        if not success:
            print("[ERROR] 依赖安装失败!")
            return False
    else:
        print("[OK] 所有依赖已安装")

    # 提示 Inno Setup：只报寻径结果，不报"未安装"的环境事实
    if not _iscc_lookup()["found"]:
        print("\n[WARN] 未定位到 Inno Setup 编译器 ISCC.exe（固定目录/注册表/PATH 三路均未命中）!")
        print("请从 https://www.jrsoftware.org/isdl.php 下载并安装")
        print("标准安装版步骤将优雅跳过，仅产出便携版 ZIP\n")

    return True

def prepare_core_modules():
    """同步引擎源码为可导入模块名（PyInstaller 依赖分析前提）"""
    print_section("同步核心功能模块")
    
    import shutil
    
    ok = True
    for src_name, dst_name in CORE_MODULES.items():
        src = SCRIPT_DIR / src_name
        dst = SCRIPT_DIR / dst_name
        if not src.exists():
            print(f"[ERROR] 缺少引擎源码：{src_name}")
            ok = False
            continue
        # 内容一致则跳过（避免无意义重写）
        if dst.exists() and src.read_bytes() == dst.read_bytes():
            print(f"[OK] {dst_name} 已是最新")
            continue
        shutil.copyfile(src, dst)
        print(f"[OK] {src_name} -> {dst_name}")
    
    # 主程序直接静态导入的既有模块（Phase 38 起另含两枚下沉引擎；Phase 50 起另含
    # desktop_hub_engine；Phase 80 起另含第 28 引擎 service_registry_engine——三者的持久化
    # 配置逻辑逐字下沉为单一真相源、由 agent_service 顶层静态导入；Phase 81 起另含第 29
    # 引擎 event_engine 与第 30 引擎 delivery_engine——WebSocket 事件总线与一键交付归档
    # 各自逐字下沉为单一真相源、同样由 agent_service 顶层静态导入，PyInstaller 经模块图收集；
    # 第 26 引擎 installer_manifest_engine 走 CORE_MODULES 映射，不在这里重复登记）
    for required in ("license_service.py", "config_persistence.py",
                     "design_compare_engine.py", "background_removal_engine.py",
                     "panel_recognition_engine.py", "desktop_hub_engine.py",
                     "service_registry_engine.py", "event_engine.py",
                     "delivery_engine.py"):
        if not (SCRIPT_DIR / required).exists():
            print(f"[ERROR] 缺少必需模块：{required}")
            ok = False
    
    if not ok:
        return False
    
    # 编译校验：所有将被打包的模块必须可通过语法检查
    import py_compile
    for mod_file in [MAIN_SCRIPT.name] + list(CORE_MODULES.values()) + [
            "license_service.py", "config_persistence.py",
            "design_compare_engine.py", "background_removal_engine.py",
            "panel_recognition_engine.py", "desktop_hub_engine.py",
            "service_registry_engine.py", "event_engine.py",
            "delivery_engine.py"]:
        try:
            py_compile.compile(str(SCRIPT_DIR / mod_file), doraise=True)
        except Exception as e:
            print(f"[ERROR] {mod_file} 语法检查失败：{e}")
            return False
    print("[OK] 全部模块编译校验通过")
    return True

def _fonttools_hidden_import_flags():
    """本地枚举 fontTools.ttLib.tables 全部字形表子模块，转成 --hidden-import 参数。
    TTFont 按表名惰性 importlib 加载这些模块，PyInstaller 静态分析看不到；而这些表
    模块本身无顶层副作用，可安全逐个作为 hidden-import 交给 modulegraph 解析。
    刻意不使用 --collect-submodules/--collect-data：它们经 PyInstaller.isolated 子进程
    执行，在 Python 3.14 上会死锁挂起。此处用 pkgutil 本地枚举（毫秒级，无子进程）。"""
    flags = []
    try:
        import pkgutil, importlib
        pkg = importlib.import_module("fontTools.ttLib.tables")
        subs = ["fontTools.ttLib.tables"]
        subs += [f"fontTools.ttLib.tables.{m.name}"
                 for m in pkgutil.iter_modules(pkg.__path__) if not m.name.startswith("__")]
        # 表解析还会惰性用到 misc / ttLib 核心子模块，一并显式声明以防漏收
        subs += ["fontTools.ttLib", "fontTools.misc.arrayTools", "fontTools.misc.textTools",
                 "fontTools.misc.psOperators", "fontTools.misc.py2urllib"]
        for name in subs:
            flags += ["--hidden-import", name]
    except Exception as e:
        print(f"[WARN] fontTools 表枚举失败，退回仅静态导入（EXE 内字体度量可能不可用）：{e}")
    return flags


def _tkinter_toc_audit():
    """第 27 引擎的窗口依赖**收集实证**：读 PyInstaller 自己写下的 EXE-00.toc，
    核对 tkinter / _tkinter / tcl / tk 四类条目真的进了包。

    为什么不能"反正 hook 会处理"就略过：desktop_hub_engine 的 `import tkinter` 躲在
    open_hub() 函数体内，而冻结态若缺 tcl/tk 脚本，开窗会**静默**走
    `window.created=False, reason=tkinter_unavailable` 这条诚实降级分支——数据层仍然
    全绿，看起来"跑通了"，可桌面窗口这个已记为 supported 的能力其实没交付。所以这一
    项缺即判构建失败，而不是留到 L4 才发现。
    """
    toc = BUILD_DIR / APP_NAME / "EXE-00.toc"
    if not toc.is_file():
        print(f"[ERROR] 找不到 {toc}，无法核验 tkinter/tcl/tk 是否随包收集"
              f"（第 27 引擎窗口层的依赖声称不可证，判失败）")
        return False
    text = toc.read_text(encoding="utf-8", errors="replace").lower()
    # 令牌取自本机 P50-1 尖峰产物的 EXE-00.toc 实测条目名（不是猜的）：
    # _tkinter（扩展模块）、tcl86t.dll / tk86t.dll（运行时 DLL）、
    # _tcl_data / _tk_data（tcl/tk 脚本树；缺任一则冻结态 tk.Tk() 直接抛错）
    probes = ("tkinter", "_tkinter", "tcl86t", "tk86t", "_tcl_data", "_tk_data")
    hits = {k: text.count(k) for k in probes}
    print(f"[INFO] EXE-00.toc 窗口层依赖命中数：{hits}")
    missing = [k for k in probes[1:] if hits.get(k, 0) == 0]
    if missing:
        print(f"[ERROR] 冻结包缺窗口层依赖：{missing} —— "
              f"desktop_hub 的窗口层将不可用，真值表 supported 声称不成立")
        return False
    print("[OK] 第 27 引擎窗口依赖已实证随包收集："
          "_tkinter / tcl86t / tk86t / _tcl_data / _tk_data 均在 EXE-00.toc")
    return True


def build_with_pyinstaller():
    """使用 PyInstaller 打包"""
    print_section("PyInstaller 打包")
    
    # 确保输出目录存在
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    
    # 直接打包主程序，不生成 spec 文件
    command = [
        sys.executable, '-m', 'PyInstaller',
        '--clean',
        '--onefile',
        '--name', APP_NAME,
    ] + _fonttools_hidden_import_flags()

    # Phase 76：启动画面。onefile 每次启动都要把 ~100 MB / 1799 个文件解包到临时目录，
    # 本机实测 Popen→health 200 需 83–141 s（CPU 仅 8.3%，瓶颈在逐文件 IO 与实时扫描）。
    # --splash 由引导器在解包阶段就画窗口，使这段"什么都不显示"的时间有可见反馈；
    # 服务就绪后由 agent_service 调 pyi_splash.close() 关闭。
    splash_file = SCRIPT_DIR / "assets" / "splash.png"
    if splash_file.is_file():
        command += ['--splash', str(splash_file)]
        print("[OK] 启动画面将随包内嵌（--splash assets/splash.png，%d B）"
              % splash_file.stat().st_size)
    else:
        print("[WARN] 缺少 assets/splash.png：EXE 启动期无启动画面")

    # Phase 76 载荷收敛（随包但运行时从未导入的组件，逐项见 P76 归因收据）：
    # cryptography 不在启动期 sys.modules、产品源码零引用（签名走标准库 hmac）；
    # PIL AvifImagePlugin/_avif 在产品源码里零引用，且单个 _avif.pyd 占 7.9 MB。
    command += [
        '--exclude-module', 'cryptography',
        '--exclude-module', 'PIL.AvifImagePlugin',
        '--exclude-module', 'PIL._avif',
    ]
    # Web 控制台（Phase 35）：静态资源随 EXE 内嵌（运行时经 sys._MEIPASS/webui 解析）
    webui_dir = SCRIPT_DIR / "webui"
    if (webui_dir / "index.html").is_file():
        command += ["--add-data", str(webui_dir) + os.pathsep + "webui"]
        print("[OK] webui 控制台资源将随包内嵌（--add-data webui）")
    else:
        print("[WARN] 缺少 webui/index.html：EXE 将不含 Web 控制台，GET / 如实返回 webui_not_installed")
    command += [
        '--workpath', str(BUILD_DIR),
        '--distpath', str(OUTPUT_DIR),
        str(MAIN_SCRIPT)
    ]
    print(f"[INFO] fontTools 字形表 hidden-import 数量：{(len(_fonttools_hidden_import_flags())//2)}")
    
    success = run_command(command, "开始 PyInstaller 打包...")
    if not success:
        return False
    
    # 验证输出
    exe_file = OUTPUT_DIR / f"{APP_NAME}.exe"
    if exe_file.exists():
        file_size = exe_file.stat().st_size / (1024 * 1024)
        print(f"[OK] 打包成功!")
        print(f"   可执行文件：{exe_file}")
        print(f"   文件大小：{file_size:.2f} MB")
        if not _tkinter_toc_audit():
            return False
        return True
    else:
        print("[ERROR] 打包失败：未找到生成的可执行文件")
        return False

def create_portable_version():
    """创建便携版本"""
    print_section("创建便携版本")
    
    # 确保 portable 目录存在
    PORTABLE_DIR.mkdir(parents=True, exist_ok=True)
    
    # 复制所有必要文件到便携目录
    import shutil
    
    portable_exe = PORTABLE_DIR / f"{APP_NAME}.exe"
    shutil.copy(OUTPUT_DIR / f"{APP_NAME}.exe", portable_exe)
    
    # 便携模式标记：agent_service.exe 检测到该文件后数据全部落在本地 Data\ 目录
    (PORTABLE_DIR / "portable.flag").write_text(
        f"zaojing-ai portable mode\nversion={VERSION}\ncreated={datetime.now().isoformat()}\n",
        encoding="utf-8",
    )
    print("[OK] portable.flag 已生成（EXE 将以便携模式运行）")
    
    # 复制文档（仅随包分发当前准确、可核实的文档；遗留的完成度/验收类文档含已作废的模拟数据，不分发）
    docs_dir = PORTABLE_DIR / "docs"
    docs_dir.mkdir(exist_ok=True)
    
    SHIPPED_DOCS = [
        "安装说明.md",
        "V1.0-功能完成度审计报告.md",
        # 便携包完整性校验的静态指南（如何下载、如何 certutil/sha256sum/verify-portable.py 校验）。
        # 刻意只写方法与"SHA 每轮变化"的说明，绝不在此写死某个具体 sha256——该文件本身在 ZIP 内，
        # 若嵌自身校验值即自指悖论；真实校验值只活在 ZIP 外的 sidecar .sha256 与 build-evidence.json。
        "下载与校验说明.md",
    ]
    # 清理便携包 docs 目录中上次构建遗留、且不在允许清单内的文档（防止作废的模拟数据随包分发）
    for stale in docs_dir.glob("*.md"):
        if stale.name not in SHIPPED_DOCS:
            stale.unlink()
    for doc_name in SHIPPED_DOCS:
        doc_file = SCRIPT_DIR / doc_name
        if doc_file.exists():
            shutil.copy(doc_file, docs_dir)
    
    # 创建 README.txt
    readme_content = f"""===================================================
造境 AI 设计智能体 V{VERSION} - 便携版本
===================================================

【特点】
- 无需安装，解压即用
- 不修改系统注册表
- 不写入系统目录
- 可随时删除不留痕迹

【使用方法】
1. 双击运行 {APP_NAME}.exe 即可启动程序
2. 程序数据将保存在当前目录下的 Data 文件夹中
3. 关闭程序后，所有数据和配置自动保存

【数据目录】
- Data\\Assets\\    - 素材库（随包预建，zip 内条目名记作 Data/Assets/，Windows 归一化后同一目录）
- Data\\Cache\\     - 缓存目录（随包预建）
- Data\\logs\\      - 日志目录（运行时真实写入名是 logs，日志文件只有 agent.log；包内预建的
                    Data\\Logs 与之同处——Windows 路径大小写不敏感，不是两个目录）
- Data\\config\\user-settings.json - 用户设置（**没有 config.yaml 这个文件**，全产品零处读取）
- 其余子目录（outputs / recovery / knowledge / providers / state / uxp …）按需创建，不预铺

【系统要求】
- Windows 10/11 (64 位)
- 便携版**不需要管理员权限**：不写注册表、不装系统目录，普通用户账户即可运行与删除
- 无 GPU 要求：全链路为 CPU 原生实现，代码里不存在 GPU / CUDA / 推理框架分支

【卸载方法】
直接删除整个便携版本文件夹即可

【技术支持】
- 论坛：https://forum.qoder.com/
- 文档：详见 docs 文件夹

===================================================
版本号：V{VERSION}
发布日期：{datetime.now().strftime('%Y-%m-%d')}
版权所有：© {datetime.now().year} {COMPANY_NAME}
===================================================
"""
    
    with open(PORTABLE_DIR / "README.txt", 'w', encoding='utf-8') as f:
        f.write(readme_content)
    
    # 预建便携版数据目录结构（运行时使用，随包分发）
    # P44 打包卫生修复：portable\Data\ 同时是 L4 活体测试的运行时数据根——
    # 不先复位就会把往轮评测报告/交付物/快照等测试残留逐轮滚存打进交付 ZIP
    # （实证：条目 834→871、内含 2026-09-23 起 42 份 eval 报告）。先删后建。
    stale_data = PORTABLE_DIR / "Data"
    if stale_data.exists():
        import shutil
        shutil.rmtree(stale_data, ignore_errors=False)
        print(f"[OK] 已复位便携版运行时数据目录（清除测试残留）: {stale_data}")
    for sub in ("Assets", "Cache", "Logs"):
        (PORTABLE_DIR / "Data" / sub).mkdir(parents=True, exist_ok=True)
    
    # 压缩为 ZIP（递归包含 docs/ 与 Data/ 子目录，含空目录）
    import zipfile
    
    portable_zip = PORTABLE_DIR.parent / f"zaojing-ai-{VERSION}-portable.zip"
    file_count = 0
    with zipfile.ZipFile(portable_zip, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for root, dirs, files in os.walk(PORTABLE_DIR):
            root_path = Path(root)
            rel_root = root_path.relative_to(PORTABLE_DIR)
            # 写入目录项（保证空目录如 Data/Logs 也在包内，统一用正斜杠保证跨平台兼容）
            if not files and not dirs and rel_root != Path('.'):
                zipf.writestr(zipfile.ZipInfo(str(rel_root).replace(os.sep, '/') + '/'), '')
            for fname in files:
                fpath = root_path / fname
                arcname = str(fpath.relative_to(PORTABLE_DIR)).replace(os.sep, '/')
                zipf.write(fpath, arcname)
                file_count += 1
    
    file_size = portable_zip.stat().st_size / (1024 * 1024)
    print(f"[OK] 便携版本生成成功!")
    print(f"   压缩包：{portable_zip}")
    print(f"   文件数：{file_count}")
    print(f"   文件大小：{file_size:.2f} MB")

    # 便携包完整性校验闭环：从磁盘实物重算 sha256（不是内存里拼的），产 sidecar .sha256 与
    # build-evidence.json 的 portable 块。校验值只在 ZIP 之外留存——该 ZIP 无法自证自身哈希。
    zip_bytes = portable_zip.read_bytes()
    zip_sha256 = hashlib.sha256(zip_bytes).hexdigest()
    zip_size = portable_zip.stat().st_size
    with zipfile.ZipFile(portable_zip) as _zf_cnt:
        zip_entries = len(_zf_cnt.namelist())
    sidecar = portable_zip.parent / (portable_zip.name + ".sha256")
    # sha256sum -c 兼容格式："<64位hash><两空格><纯文件名><LF>"。写 bytes 而非 text，
    # 避开 Windows 文本模式把 \n 折成 \r\n 令 `sha256sum -c` 与 certutil 逐字节对不上。
    sidecar.write_bytes(("%s  %s\n" % (zip_sha256, portable_zip.name)).encode("utf-8"))
    print("[OK] 便携包校验件已写出：%s" % sidecar.name)
    print("     sha256=%s…" % zip_sha256[:16])
    _merge_portable_into_evidence({
        "zip_name": portable_zip.name,
        "sha256": zip_sha256,
        "bytes": zip_size,
        "entries": zip_entries,
        "file_count": file_count,
        "sidecar_name": sidecar.name,
    })

    return True


def _merge_portable_into_evidence(portable_info: dict) -> None:
    """把便携 ZIP 的 sha256/字节数/条目数并入 installer/build-evidence.json。

    该文件由 step5 的 Inno 编译步骤先写出（编译器未定位时也会写一份只含
    iscc_output_tail=compiler_not_located 的最小件），便携包却在 step7 才生成，
    故此处「读回既有 JSON、追加/覆盖 portable 块、原子重写」，绝不整份重建——
    否则会抹掉 iscc/artifact/manifest 等既有编译证据字段。读回失败按空 dict 兜底，
    保证便携校验块总能落盘，即便本轮没编出安装版。"""
    try:
        INSTALLER_DIR.mkdir(parents=True, exist_ok=True)
        dest = INSTALLER_DIR / "build-evidence.json"
        ev = {}
        if dest.is_file():
            try:
                loaded = json.loads(dest.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    ev = loaded
            except Exception:
                ev = {}
        ev["portable"] = {
            "zip_name": portable_info["zip_name"],
            "sha256": portable_info["sha256"],
            "bytes": portable_info["bytes"],
            "entries": portable_info["entries"],
            "file_count": portable_info["file_count"],
            "sidecar_name": portable_info["sidecar_name"],
            # 如实声明 sidecar 的行文形制，供校验器与人都能据此复算，不让人猜两空格还是制表符
            "sidecar_format": "sha256sum-compatible: '<sha256>  <zip_name>\\n' (两个空格分隔, LF 换行, 文件名不含路径)",
            "note": "该 sha256 针对本次构建产出的 ZIP 实物；ZIP 内含目录时间戳故字节逐轮微变，"
                    "校验值一律以本 sidecar 与 build-evidence.json 现值为准，文档不写死历史值。",
        }
        ev.setdefault("schema", "zaojing-installer-build/v1")
        tmp = dest.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(ev, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, dest)
        print("[OK] 便携包校验信息已并入 build-evidence.json：%s" % dest)
    except Exception as exc:
        print("[WARN] 便携包校验信息未能并入 build-evidence.json：%s（不谎报已并入）" % exc)

def prepare_inno_setup_script():
    """生成 Inno Setup 脚本 setup.iss（模板见 INNO_SETUP_SCRIPT）。

    用 string.Template.substitute 而非 str.format：$name 占位与 Inno 的 {const} 语法
    不再冲突，"值里再带花括号"的写法不可能在产物里留下未展开的残留；且 substitute
    遇到缺失占位符直接 KeyError，绝不静默漏替换（.format 的漏替换正是旧缺陷的成因）。
    产物由第 26 引擎 installer_manifest_engine 静态校验，见 /api/v1/installer/manifest。
    """
    script_content = INNO_SETUP_SCRIPT.substitute(
        project_name=PROJECT_NAME,
        version=VERSION,
        publisher=COMPANY_NAME,
        url="https://example.com/zaojing",
        app_name=APP_NAME,
        company_name=COMPANY_NAME.replace(' ', ''),
        appid=INSTALLER_APP_ID,
        install_date=datetime.now().strftime("%m-%d-%Y")
    )
    
    script_file = SCRIPT_DIR / "setup.iss"
    with open(script_file, 'w', encoding='utf-8') as f:
        f.write(script_content)
    
    print(f"[OK] 已创建 Inno Setup 脚本：{script_file}")
    return True

def build_with_innosetup():
    """用真编译器产出安装版，并把可复核的构建证据落进 installer/build-evidence.json。

    证据只由实测派生：编译器绝对路径与寻径来源、iscc 真实退出码、产物字节数与 sha256、
    编译后再跑一遍静态校验的 counts。任何一环读不到就如实记 None/失败，绝不写"已生成"。
    """
    print_section("Inno Setup 打包")

    # 准备脚本
    prepare_inno_setup_script()

    script_file = SCRIPT_DIR / "setup.iss"

    if not script_file.exists():
        print("[ERROR] Inno Setup 脚本不存在")
        return False

    loc = _iscc_lookup()
    evidence = {
        "schema": "zaojing-installer-build/v1",
        "script_path": str(script_file),
        "script_bytes": script_file.stat().st_size,
        "script_sha256": hashlib.sha256(script_file.read_bytes()).hexdigest(),
        "compiler_path": loc.get("path"),
        "compiler_lookup_mode": loc.get("lookup_mode"),
        "compiler_lookup_source": loc.get("lookup_source"),
        "iscc_exit_code": None,
        # 字段名如实反映内容：这是编译器的整段输出尾部，不是 stderr。iscc 把告警与
        # "Successful compile" 行一并写在 stdout，若仍命名为 stderr_tail，读证据的人
        # 会以为 stderr 为空即无告警——那正是本轮要根除的"探针形制写成环境事实"。
        "iscc_output_tail": None,
        "iscc_output_chars": None,
        "iscc_warnings": None,
        "iscc_compile_line": None,
        "artifact_path": None,
        "artifact_bytes": None,
        "artifact_sha256": None,
        "manifest_verify_after_compile": None,
        "compiled": False,
        "fabricated": False,
    }

    if not loc["found"]:
        # 寻径三路均未命中：只说"未定位"，给出可执行的下一步，不冒充环境事实
        print("[WARN] 未定位到 ISCC.exe（固定目录/注册表/PATH 三路），安装版步骤跳过")
        print("       该结论仅描述本次寻径结果，不等于本机未安装 Inno Setup")
        evidence["iscc_output_tail"] = "compiler_not_located"
        _write_build_evidence(evidence)
        return False

    # 运行 Inno Setup Compiler（用实测绝对路径，不依赖 PATH）
    print("编译器（绝对路径）：%s" % loc["path"])
    proc = subprocess.run([loc["path"], str(script_file)],
                          capture_output=True, timeout=900)
    out = (proc.stdout or b"").decode("utf-8", errors="replace")
    err = (proc.stderr or b"").decode("utf-8", errors="replace")
    evidence["iscc_exit_code"] = proc.returncode
    full = out + ("\n" + err if err.strip() else "")
    evidence["iscc_output_tail"] = full[-1600:]
    # 生产者如实记录**输出总长**：校验器据此区分"尾部即全文"与"尾部是节选"，
    # 前者要求告警计数相等、后者只设下界。缺这一格时校验器只能按 field_absent 降强度，
    # 记了才谈得上可证伪——本轮根除的正是"记录者知道自己截了断却不写下来"。
    evidence["iscc_output_chars"] = len(full)
    # 编译器自己的话逐条留证：它报的每一行 Warning/Error 都是静态校验看不见的判词，
    # 本引擎的 R11/R12 两条规则正是由这类行反推出来的，故原文落盘而非只留退出码。
    evidence["iscc_warnings"] = [ln.strip() for ln in full.splitlines()
                                 if ln.strip().startswith(("Warning", "Error", "Deprecated"))]
    evidence["iscc_compile_line"] = next((ln.strip() for ln in full.splitlines()
                                          if "compile (" in ln), None)
    print("iscc 退出码 = %s   编译器告警 %d 行" % (proc.returncode, len(evidence["iscc_warnings"])))
    for _w in evidence["iscc_warnings"]:
        print("   [iscc] %s" % _w[:220])
    if proc.returncode != 0:
        print("[ERROR] 编译失败，输出尾部：\n%s" % evidence["iscc_output_tail"])
        _write_build_evidence(evidence)
        return False

    # 查找生成的安装包（以脚本 OutputDir/OutputBaseFilename 的真实落点为准）
    setup_exe = INSTALLER_DIR / f"zaojing-ai-{VERSION}-setup.exe"
    if setup_exe.exists():
        raw = setup_exe.read_bytes()
        evidence["compiled"] = True
        evidence["artifact_path"] = str(setup_exe)
        evidence["artifact_bytes"] = len(raw)
        evidence["artifact_sha256"] = hashlib.sha256(raw).hexdigest()
        print(f"[OK] 安装包生成成功!")
        print(f"   安装包：{setup_exe}")
        print(f"   文件大小：{len(raw) / (1024 * 1024):.2f} MB  sha256={evidence['artifact_sha256'][:16]}…")
        # 编译通过 ≠ 清单正确：产出后再跑一遍静态校验，把 counts 一并留证
        try:
            import installer_manifest_engine as _ime
            # [Files] 的 Source 一律相对 .iss 所在目录解析，故 payload_root = SCRIPT_DIR；
            # 运行时数据根取本机真实 %LOCALAPPDATA%\Zaojing（读不到就传空，让 R7 如实跳过）。
            _la = os.environ.get("LOCALAPPDATA", "")
            rep = _ime.verify_script(script_file.read_text(encoding="utf-8"),
                                     str(SCRIPT_DIR),
                                     data_root=(str(Path(_la) / "Zaojing") if _la else ""))
            evidence["manifest_verify_after_compile"] = dict(rep["counts"])
            evidence["manifest_verdict"] = rep["verdict"]
            print("   编译后静态校验：verdict=%s counts=%s" % (rep["verdict"], rep["counts"]))
        except Exception as exc:
            evidence["manifest_verify_after_compile"] = None
            print("   [WARN] 编译后静态校验未能执行：%s（不谎报已通过）" % exc)
        _write_build_evidence(evidence)
        return True
    else:
        print("[ERROR] 安装包生成失败：编译器返回 0 但产物不在预期路径")
        _write_build_evidence(evidence)
        return False


def _write_build_evidence(evidence: dict) -> None:
    """把构建证据写进 installer/ 目录（构建工作区内，不进 PyInstaller 包）。"""
    try:
        INSTALLER_DIR.mkdir(parents=True, exist_ok=True)
        evidence["built_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        dest = INSTALLER_DIR / "build-evidence.json"
        dest.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[OK] 构建证据：{dest}")
    except Exception as exc:
        print("[WARN] 构建证据未能写入：%s" % exc)

def generate_readme():
    """生成安装说明文档"""
    readme_content = f"""# {PROJECT_NAME} V{VERSION} 安装说明

**发布日期**: {datetime.now().strftime('%Y-%m-%d')}  
**版本**: V{VERSION}  
**平台**: Windows 10/11 (64 位)

---

## 📦 安装包内容

### 主程序
- `ZaojingAI.exe` - 造境 AI 设计智能体主程序

### 分发格式（本次构建产出两种）
- `zaojing-ai-{VERSION}-setup.exe` - 标准安装包（由本机 Inno Setup 6 的 ISCC **真编译**产出：构建侧按『固定安装目录 → 只读注册表 → PATH』三路寻径，找到即每次构建都编译一次；退出码、输出尾部、脚本与产物的字节数+sha256 记入 `installer/build-evidence.json`，运行时可用 `GET /api/v1/installer/artifact` 逐字段与磁盘实况重算对账、`GET /api/v1/installer/preflight` 只读预检本机安装条件；三路皆未找到时如实 WARN 并只出便携版）
- `zaojing-ai-{VERSION}-portable.zip` - 便携版压缩包（免安装，解压即用）

### 文档
- `安装说明.md` - 本说明（安装、配置、诊断、缓存清理等）
- `功能完成度审计报告.md` - V1.0 全量能力真实交付状态与诚实边界（能力真值表、引擎清单、验收结论）

### 数据目录（自动创建）
- `%LOCALAPPDATA%\\Zaojing\\` - 应用数据根目录（配置 user-settings.json / 状态 / 模型注册表）
- `%LOCALAPPDATA%\\Zaojing\\Assets\\` - 素材库索引（assets.db.json / embeddings / 缩略图）
- `%LOCALAPPDATA%\\Zaojing\\logs\\` - 运行日志 (agent.log)
- 便携版则全部位于程序目录 `Data\\` 内，不写系统目录

---

## ⚡ 核心功能（本次构建为真实引擎集成版）

V{VERSION} 打包产物内置 **30 个引擎**。凡在任务书 V1.0 范围内、可在服务端真实落地的能力均已真做（无占位数据、无假成功）；依赖外部条件的能力（真实 Adobe UXP 宿主、已配置且可达的模型后端点、3D 渲染器、授权数据）在冻结 EXE 与真实环境中一律以 GATE / 诚实 502 如实降级，**绝不伪造结果**。逐项交付状态与诚实边界见随包的 `功能完成度审计报告.md` 能力真值表。主要端点：

- **授权管理** — 多源机器码（getpass + 环境变量 + SHA-256），支持降级启动不崩溃；`GET /api/v1/license`、`POST /api/v1/license/activate`
- **配置持久化** — 路径展开、跨版本迁移、扩展名白名单；`GET/PUT /api/v1/config`
- **安全防护 (P1)** — 路径穿越/符号链接逃逸/双扩展名/系统目录拦截 + L0-L4 工具风险分级；`GET /api/v1/security/status`
- **素材引擎** — 真实文件导入入库、512 维向量余弦搜索、缩略图生成、商用授权状态追踪；Phase 52 起素材的导入／删除／授权写入与磁盘审计探针一律经 `asyncio.to_thread` 离开事件循环执行（重活儿不在协程里独占循环，故同期其它请求的响应延迟不被它拖走，该项由 `/api/v1/metrics` 的 `loop_lag` 仪表而非主观断言核验）；`/api/v1/assets` 系列端点
- **印前检查** — PIL 真实解析尺寸/DPI/色彩模式，9 项规则检查 + 报告 + 自动修复建议；`POST /api/v1/preflight`
- **工作流/批处理** — 节点图定义/校验/执行/可视化，多文件并发批处理（真实逐文件计时）；Phase 51 起 Tool 节点可跨引擎转调既有端点协程（同一份校验、副作用与事件，不另起第二份实现），工具面 **72 个 / 23 个域**由 `WorkflowEngine.tool_registry` **实时派生**并经 `GET /api/v1/workflow/tools` 出清单（逐工具的参数契约取自其绑定的请求模型与协程签名，未挂元信息的工具如实标 untagged），节点间传值为 `$变量.字段` 点路径（Phase 51 起支持整数下标），故上游 `packaging.dieline` 产出的 panels 被下游 `packaging.panel_topology` 逐字消费——**值血缘**（下一节点的输入可被独立复算证明就是上一节点产出的值），而非"先后调用过"；批处理的逐文件成败以**引擎自身诊断**为判据（文档不存在时印前引擎自报 PRINT-999 系统发现，批处理层如实记为失败并携带其原文，绝不因"协程有返回"就记成功）；Phase 52 起批处理的"并发"口径如实申报：CPU 与 GIL 绑定类操作（preflight／import_asset／remove_background）在**服务级闸门**下串行，工作线程预算 `workers_applied` 取请求值与 `max(1, min(16, cpu_count))` 的较小者，且每次响应自报 `workers_requested`／`workers_applied`／`worker_cap`／`worker_policy`／`parallel_efficiency`（由真实逐件计时算出，不是配置值），因此"配了 8 个线程"绝不等于"8 件同时在算"；`/api/v1/workflow` 与 `POST /api/v1/batch/process`
- **模型路由** — registry.yaml 注册表、成本/延迟/隐私三种路由策略、熔断限流、每日预算；`POST /api/v1/generate`
- **图像处理** — 真实像素级去背景（边缘泛洪算法，输出透明 PNG）；`POST /api/v1/generate/remove-background`
- **知识库 RAG** — 真实分块/嵌入/检索与跨品牌作用域隔离，缺失文件诚实 404；`/api/v1/knowledge` 系列
- **字体排版** — 真实字体扫描/度量（fontTools）/匹配候选/fallback/resolve，缺库诚实降级；`/api/v1/typography` 系列
- **多角色设计评审** — 四角色规则真实命中品牌色冲突/重叠/对比度并聚合评分；`POST /api/v1/review/run`
- **客户改稿** — 唯一目标 proposed、受保护对象 conflict、目标歧义 needs_clarification 从不猜测，apply 真实改写几何与颜色；`/api/v1/revision` 系列
- **设计对比 / 一键交付** — §50 六类结构化真实 diff；真实归档打包为合法 ZIP + 诚实 skip；`POST /api/v1/design/compare`、`POST /api/v1/delivery/package`
- **能力评估中心** — 真实黄金用例评测（151 条确定性用例 + 34 条负控校准保证判分器可证伪，Phase 27 起品牌真值 4 条以真实像素夹具驱动 brand 引擎判分，Phase 28 起印前 2 条以 RGB-72DPI/CMYK-300DPI 真像素位图核验色彩·分辨率·出血判定、知识检索 2 条核验确定性向量下检索排序与作用域隔离真值，Phase 30 起改稿 3 条（零时钟·零随机指令流的 proposed/conflict/needs_clarification 矩阵与真实换算）、评审 3 条（缺陷夹具 score=68 逐问题精确 + 合规夹具满分零问题反证）、安全 3 条（路径三阻一放四向哨兵 + User∩Skill∩Tool 权限交集 + 高危无执行器不虚报），Phase 31 起工作流 6 条（环检测唯一报错原文、三段链 9 条日志消息精确贯通、失败传播下下游键不虚设、条件分叉 else 支如实 skipped、循环体聚合迭代计数真值、剥去实例戳后二次执行逐字段幂等），Phase 32 起素材 6 条（token-hash 向量非零位与哈希桶独立复算、真实 PNG 入库与缩略图落盘、检索排序 1.0/0.402015 双钉、CC-BY 署名双向、生成元数据回读、冷读回与删除级联清空）、恢复 6 条（快照 sha256 与固定字节复算相符、损坏还原字节回归、事务计数与回滚真实还原、任务注册字段落盘、检查点恢复与幽灵任务如实抛错、无本地文件诚实不可恢复）、Phase 33 起排版 6 条（fontTools 现场编译自供夹具真 TTF 走溯源解析→重放索引断掉本机字体依赖：serif/sans 度量逐数精确与缺字如实计数、覆盖硬门槛唯一候选、字重罚分降序、替换规则落盘回读、幽灵字体 None+KeyError 原文）、Phase 34 起条码 14 条（一维 5 条：EAN-13/EAN-8 位串逐字符对 GS1 公开例全等、渲染 PNG 真像素逐点采样、非法校验位错误原文逐字且不落盘、Code-128/DataMatrix 留守不虚报与参数化几何真值；Phase 57 起 QR 真值另 5 条：纯标准库 ISO/IEC 18004 编码，手抄 Table 7/8/9 常量独立核对 40 格容量边界与码字算术全等、21×21 栅格与申报 matrix 逐格同源、评测侧独立 BCH 尺子先复现 ISO 三串公布例再逐字符对账格式/版本位与掩码罚分最小、四条诚实边界 ValueError 原文逐字且绝不落盘；Phase 63 起一维解码 oracle 真值另 4 条：三表构造律自检（oracle 自身受验收）、EAN-13 十位扫段像素读回、EAN-8 公开例+扫段、三阴性防空集假绿）与拓扑 5 条（直插盒度序列/折长 640/裁切线 870 全字段、折线坐标逐数、部分重叠 shared=5.0、断开+非法三态如实、启发式标注诚实核验）与 Phase 36 起授权 4 条（两次签发票据字节全等、篡改与非法格式报错原文逐字含 base64url 静默丢字符实际行为、过期三态、机器绑定两态）、配置 4 条（默认版本/URL/计数真值与变量拼接串不展开如实钉死、冷读回、旧版本迁移四字段、路径拦截四阻两放）、路由 4 条（自供 YAML 注册表真实解析、cost 模型均价平票插入序裁决、privacy/latency 分向择路、预算严格小于排除与熔断开环三态）、Phase 37 起更新 4 条（无源诚实拒伪造与非法渠道原文、HMAC 规范化字节独立复算、分支矩阵钉死版本门先于验签门、真实 HTTP 下载 sha256 相符与原子应用字节回归全链）、NAS 4 条（未配置/非法类型/空目标/不可达四态原文逐字、目录清单逐项真值、穿越与绝对路径双拦零落盘、冷实例缓存命中四元组全等）、Provider 4 条（未配置三态不虚报、脱敏字面真值与 DPAPI 明文绝不在盘、真实 HTTP 对话 content/usage/Bearer/单请求逐字段、401 不重试与熔断开路零 HTTP）、Phase 38 起对比 4 条（空 diff 六类计数全 0、富字段六类逐数归类与 moved 逐字段、结构增删按 type 归类与未知兜底、compare_keys 限字段集与 name 索引）、去背景 4 条（真像素泛洪清 8400px/84% 逐数精确与被包围孔洞保留证泛洪≠全局色键、阈值边界 0/1 与钳 255、幽灵输入中文原文抛错绝不落盘、缺省输出与两次跑幂等确定性反查）、Phase 47 起面板识别 4 条（评测自绘 220×100 刀版夹具、与引擎自测夹具在画幅/划分/虚线常数上相互独立且字节 sha256 双向钉死：3×2 网格 6 面板各 65×39=2535px 与邻接 7/度序列 [3,3,2,2,2,2]/轮廓 2×234+2×390=1248/背景余量 22000-909-15210=5881 逐字段精确、同图在 max_line_width 3 与 12 下如实两态且降级分支字段集与正常分支全等与 px_per_mm=2 换算 32.5×19.5mm 不改像素面积、无墨/无封闭域/被面积门限全拒三态均 0 面板并以同沙箱同法出 6 面板作反零向量、三次调用逐字段幂等含 params 默认门限回显）、Phase 49/53/58/59 起安装清单 6 条（评测自造合规 .iss 零检出，并以分段计数 Setup7/Files2/Icons2/Registry3 与"注释空行不计数、行尾续行并入上一条且行号取起始物理行"的解析器真值钉死；自造破损 .iss 的 12 项检出逐 id 逐项序精确——8 error·3 warn·1 info，含 Inno 合法常量 tmp 永不解析的机器无关触发与 install_date 一条字面量双落 R1/R8 两条检出，另以生产合规写法零误报和去掉 runtime_data_root 后的 11 项差分（8 error·2 warn·1 info）双向排除误报与漏报；四路接缝反证——磁盘存在桩判假则合规清单必报缺件、glob 桩判真则零命中通配 error 消失、环境目录桩双向则预建目录结论翻转、注册表桩则 installed 随真实卸载项翻转而本机真实 portable 标记不翻 installed；入参拒绝原文逐字 + 两次调用 JSON sha256 全等、静态开火臂（R6 单写首括号 error 且键名派生与转义状态无关／R12 两条 directive 各一条 warn 且整词比较对 x64compatible 零误报／R13 缺 AppVersion 与 AppVerName 各判 error、AppVerName 单独在场即合规／R11 缺省 admin 与 HKCU 区配对 warn、补 lowest 归零，四条夹具各只改一处形制互不掩盖，其合规形制照抄本机真 iscc 零告警编译通过的生产 setup.iss 而非凭空想象）、Phase 58 补 R14 卸载撤销七臂（写入型 [Registry] 条目缺卸载撤销声明必 error，且逐行回指写入行号与值名；同键 uninsdeletevalue／同键 uninsdeletekey／祖先键 uninsdeletekey 三臂零检出；uninsdeletekeyifempty 无论挂在本键还是祖先键照旧开火——它清不掉仍有值的键，故其形制绝不进 accepted_flags；HKCU 写入而 HKLM 撤销跨根照旧开火；每臂 counts.total 恒等于 findings 长度；R14 的判据出处不是 Inno 文档转述而是 Phase 58 本机**真卸载实测**：卸载器 exit 0、安装根与 HKCU 卸载子键消失、四棵数据树归零而应用键三值原样留守））、编译证据 3 条（编译面正控（退出码 0 ∧ 尾部真见完成行 ∧ 产物在盘三条独立信号同立，证据字节与 sha256 由用例用 hashlib 独立重算而非抄引擎返回值，自报 compiled/fabricated 翻转后除回显字段外整份报告全等，以此证明自报不参与判定）、编译面四臂（等长原地替换只有 sha256 能发现而 compile_succeeded 仍 True／编译后改脚本判 artifact_not_from_current_script 而静态复校照旧 manifest_ok／产物缺席判 artifact_missing 且优先于 compile_not_proven／退出码非零且无完成行判 compile_not_proven 而静态面仍 manifest_ok，即静态合规不等于编译通过）、告警与完备性臂（全文尾部走等式口径、截断尾部走下界口径，缺字段判 evidence_incomplete 且 provenance 不替编译器说话）；自带 1 条域校准（case id calib-manifest-build-wrong-expect——编译面假期望：把证据自报 compiled=False 当成编译未证成的判据必须判 fail；Phase 53 该枚入册时全局负控校准 26→27））、预检 2 条（预检面正控（七项全 True 且 blocked/unknown 皆空、写探针真落盘并自清、{{autopf}} 成对给出 commonpf/userpf 两条候选、需求字节=产物字节+声明载荷字节实测和、不给 target_override 时真实安装根零探针）、预检面十臂（同名文件占位、载荷缺失、证据缺席与篡改、余量读不到或不足、目标 exe 占用三态、令牌未提权与探测不到、写探针三种归因，四路接缝桩 _token_elevation/_disk_free/_exclusive_open/_write_probe 桩后必还原并复验回基线）；自带 1 条域校准（case id calib-manifest-preflight-wrong-expect——预检面假期望：调用方显式关闭写探针时诚实形制是 verdict=preflight_unknown 且 unknown_codes=[write_probe_unprobed]，把它当成 preflight_ready 的判据必须判 fail））、运行时守护 5 条（写探针三臂：可写臂 writable/bytes_verified/cleaned 三真且 method=real_write_readback_delete、目标与父路径为普通文件两臂三假且 error 起手 FileExistsError、探针文件零残留且 target 归一 <SB>/probe-ok；磁盘阈值两侧：min=0 必 meets_min=True、min=2^62 必 False、负阈值抛点名错误，free/used 连续量不入判分；端口真 bind 两侧：自起监听桩端口必 available=False 且 detail 带 OSError 原文、OS 指派后自关端口必 available=True 且 detail=None、越界入参抛点名错误而端口号只作结构核对；单实例锁全链：首取 True/False→同实例重取 False/True→release True→释放后同实例 True/False→另一实例 False/True 且它的 released=False，backend=kernel32-named-mutex、非法锁名抛点名错误、重复 release False；run_diagnostic：三项全 pass 且计数 [3,3,0] 与 status=healthy、timestamp 刻意为 None、带端口臂四项含 port_available、降级臂 data_root_writable=fail 且 status=degraded 且 checks_failed 恒为 performed-passed 派生式、引擎构造不建根而探针真建根 [False,True]、残留只有 .runtime-guard 一枚目录；自带 1 条域校准（case id calib-guard-wrong-expect——守护面假期望：数据根落在普通文件之下时写探针必失败、status 由逐项检查派生成 degraded，把降级臂 status 当成 healthy 的判据必须判 fail）；本枚与新添的预检面校准同轮入册，全局负控校准随之 30→32）、Phase 50 起桌面 Hub 客户端 4 条（评测自起 127.0.0.1:0 的进程内**真** HTTP 夹具驱动真实 urllib 请求，routes/seen 逐实例隔离：三条只读 GET 的调用序列与 READ_SOURCES 逐字全等且 POST 计数恒 0、八行窗口文本逐字复算全等、最新报告按 max(finished_at) 挑中而非取末位；跨源自报引擎清单不一致须显式 agree=False 且不静默降级，HTTP 200 且合法 JSON 但缺 version 的形状不合须判该源降级、身份字段一律留空——绝不再落 "vNone" 占位行，该缺陷由 P50 契约探针实抓并同轮根治；端口关闭／非造境服务（HTML 与 404 两种传输形制逐源独立不连坐）／JSON 残缺三形制各有真值且缺失字段一律 None；POST 通道唯一性——只读路径恒零 POST、显式 eval_run 恰一次体为空对象的 POST、500 原文照抄不吞、未知动作抛 HubError 且零新增请求，判分字段把随机端口归一成 "BASE" 以保证顶层与嵌套两次运行逐字段全等，Phase 56 起品牌语义 5 条（评测自起 127.0.0.1:0 进程内 mock 视觉 HTTP 驱动真实 urllib 往返：模型原文逐字段全等透传＋usage=133 真值＋data URL 从真 PNG 字节独立复算＋Bearer 头在场＋明文密钥缺席＋非 JSON 保留 raw 原文不硬编 schema＋未注入/未配置两形制诚实 needs_external）、Phase 58 起安装执行 6 条＋1 校准（评测把假 spawn／假注册表／假落点根三枚接缝注入同一份执行器源码，在 temp 沙箱里真起子进程、真建目录树：plan 只算不跑——sha 与字节由用例独立读盘复算、五枚落点 exists_now 现场重算、卸载键解 {{ 括号派生、七门四开关逐字；十七道拒绝臂各按 reason 码拒绝且**拒绝的代价为零**（零落盘零注册表写入）；installed 只由安装前后目录树＋HKCU 卸载项的差集派生，且同一枚物理文件被 install_root 与 vendor_dir 两个标签各扫一遍时**不许数两遍**（该缺陷由本轮判据实抓并同轮根治：created_count 曾虚报 6 而磁盘真相 4）；同一份自报——退出码 0＋完成行＋stdout 原文一字不差——在盘上有物与盘上空手两趟判分相反，空手趟十项观察恰好 3 真 7 假且台账与现场自洽只判 ledger_unverified 不伪造回执；一装两卸三形制＋对账活证，退出 0 而未清判 incomplete、真卸则目录与两枚键归零且作用域锁死本次创建集、用户原有数据只点名不删、等长篡改唯 sha256 可查且还原即回绿、装好未卸而现场仍在位判诚实中间态 install_verified_awaiting_uninstall（同一份 install-only 台账配上痕迹已不在场则点名『被台账之外的手段抹掉』，而台账那一笔本就未成立时如实沉默为零点名）；证据件与用户数据分账——安装台账与缺省安装日志本就落在被观察的数据根内，第二轮执行必然被自家重写，故逐份另账回显而绝不进既有数据基线，轮 2 仍判 uninstall_verified 且既有数据零偏离、创建集 5→4、卸载回执逐字回显台账自己的拼写（比对归一、凭据原文，两件事分开；此枚由评测台实抓，同轮补进引擎自测臂）；校准那条把"安装器自报退出码 0＋完成行当成已装成"的假期望必须判 fail）、Phase 79 起桥接握手 1 条＋桥接读取 1 条＋桥接写入 1 条（进程内 mock UXP/CDR 宿主驱动 adobe_bridge 真实异步握手→单条 cdr.object.list 下发→轮询取回→submit_result→reconcile 对账往返，含 auth/origin/unknown_host 三拒、no_host_connected 诚实、写门 confirmation_required 零入队、未协商 capability_not_negotiated 拒绝；此三枚 verified=True 仅证协议机制对 mock 宿主的往返成立，绝不等于已对真实 Adobe/CorelDRAW 文档做改写，ps/ai 写入与插件能力上报仍留 needs_adobe_host 边界）、Phase 80 起功能开关 1 条＋工具注册表 1 条＋技能运行时 1 条（三域逐字下沉为第 28 引擎 service_registry_engine 的单一真相源、`/api/v1/flags`／`/api/v1/tools`／`/api/v1/skills` 端点逐字委托它，黄金用例于私有沙箱直接构造出货的 ServiceRegistryEngine 驱动同一份随包代码而非另造桩——功能开关核空沙箱种子 4 默认键精确与落盘 JSON 等于默认、setdefault 合并不覆盖自定义键、损坏文件如实回退默认，工具注册表核无宿主时 6 枚可执行 4 枚需宿主逐项派生不伪造且宿主仅上报 ps.add_layer 只这一枚翻值，技能运行时核越权＋未注册工具＋非法风险三错逐条命中、跨能力读同一工具注册表、缺 id 如实跳过）、Phase 81 起 websocket_events 1 条＋delivery_package 1 条（两域逐字下沉为第 29 引擎 `event_engine` 与第 30 引擎 `delivery_engine` 的单一真相源、`/ws`／`/api/v1/events/recent`／`/api/v1/delivery/package` 端点逐字委托它们，黄金用例于私有实例／私有沙箱直接构造出货引擎驱动同一份随包代码而非另造桩——事件流核 json_safe 把 Path/Enum/set/tuple/None/自定义对象归一化、空连接下 delivered_to=0 而台账 published_total/kind_counts 仍完整累计、连发截断后 history 长度恒=capacity 而 published_total 续增、坏连接发送抛错后被摘除且 delivered_to 只数活连接、disconnect 幂等、recent 观测面逐字段与内部真值一致；交付核真实磁盘复制＋逐文件 SHA256 由磁盘实物独立重算全等、数据根之外真实文件如实标注不拦、拒收三形制分账且 tally 三键恒在、ZIP namelist 与 manifest 交叉且拒收路径绝不入包、manifest 原子落盘复读全等、空 plan 仍出 README+manifest 且 included=0）核验确定性引擎真值，通过率真实派生、报告 JSON 落盘可对比；未交付的 agent/vision 类别如实 not_evaluated，桌面 Hub 的 GUI 窗口本体（真建窗／逐 Label 文本／真实 invoke）亦如实列入 not_evaluated——无显示会话会让评测脆断，其活体实证在 L2/L4 完成）；`POST /api/v1/eval/run`、`GET /api/v1/eval/suite|reports`、`POST /api/v1/eval/compare`
- **品牌要素分析** — 真实像素中位切分调色板、CIEDE2000 品牌色一致性（matched 由真实 ΔE 判定）、alpha 通道逐像素 Logo 包围盒/留白/宽高比；语义类（Logo 含义/调性/字体识别）自 Phase 56 起为真实透传：真读图像字节→构造 data:image/…;base64 多模态 URL→委派已注入的真实 OpenAI-兼容 Provider 走 /chat/completions 往返→**逐字透传**模型原文（本引擎不合成任何一个字符、不臆造置信度）；未注入/未配置 Provider 诚实 needs_external（HTTP 502 且 fabricated=false）、模型返回非 JSON 如实 provider_output_unparseable 保留 raw、传输失败原样透传其真实状态；真实语义质量取决于所接入模型本身（base_url＋有效 Key＋配额属部署配置）；`POST /api/v1/brand/palette|color-consistency|logo-geometry|semantic-insight`、`GET /api/v1/brand/semantics`（仍回边界声明并另带 provider_configured 实时真值）
- **运行时守护** — 数据根写入探针（真实写→读回比对→清理，父路径为文件如实报不可写）、磁盘余量真实阈值、端口真 bind 探测（占用必报 False）、命名内核对象单实例锁、诊断状态由逐项真实检查派生；静默安装/注册表/快捷方式/卸载的执行器自 Phase 58 起真实交付（十道门先于任何副作用、回执只由安装前后目录树与 HKCU 卸载项的差集派生、安装器退出码与自报一律进 self_report_ignored 不作数、卸载只清理本次创建的落点），入口**只有**同一可执行文件的 CLI 子命令 `ZaojingAI.exe install-exec install|uninstall|state|plan [--confirm ZAOJING-CONFIRM-EXECUTE-INSTALLER]`——四形由 CLI 自己的 legal 清单派生（非法子命令当场回 legal 集合而非静默兜底），其中 install/uninstall 两形必须逐字带上显式同意令牌、缺省值绝不触发执行，plan/state 两形只算不跑不需要令牌（plan 的落点、参数、门序列全部从磁盘与台账派生，结构上不产生任何副作用），HTTP 面结构上走不到执行器；`GET /api/v1/runtime/diagnostic`、`POST /api/v1/runtime/port-check|single-instance`、`GET /api/v1/runtime/install-actions`（契约面与退役边界原文）、`GET /api/v1/installer/execution`（只读对账）
- **实时事件流** — 任务事件真实记录（WebSocket + 事件总线）；`/api/v1/events/recent`。Phase 51 起账本可机械复核：端点同时给出**累计口径**（`kind_counts` / `published_total`）与**尾部缓冲**（`history`，容量 200 的环形队列），故"事件是否丢失/注水"由 `published_total == sum(kind_counts)` 这类与截断无关的关系式判定，而不是靠数条目；服务侧 26 类事件名在验收时被逐类要求**真实发生过**（期望集由源码 `_emit()` 字面串实时派生，非手写名单），未声明却出现的事件名一律判为虚假
- **工具注册表 / 技能 / 功能开关** — 默认契约、越权拒绝、持久化开关；Phase 80 起三域逐字下沉为第 28 引擎 `service_registry_engine`（单一真相源，`/api/v1/tools`、`/api/v1/skills`、`/api/v1/flags` 端点一律委托该引擎，服务不再各写一份），并以 `feature_flags`／`tool_registry`／`skill_runtime` 三枚独立黄金域纳入评测台——黄金用例驱动的就是端点委托的那份出货代码
- **色彩引擎** — 对真实图像取调色板、WCAG 对比度、CIEDE2000 ΔE、三角和谐配色；`/api/v1/color` 系列
- **布局引擎** — AABB 碰撞、对齐/分布/安全区/综合体检真实几何；`/api/v1/layout` 系列
- **包装引擎** — 刀版/拼版/变量数据/面板拓扑几何，真实 EAN-13/EAN-8 条码渲染；`/api/v1/packaging` 系列
- **面板识别引擎** — 第 25 引擎（Phase 47）：位图刀版 → 面板/线型/拓扑的真实几何反解，Otsu 直方图自算阈值二值化（阈值随响应回显）+ 轴对齐游程抽线 + 共线间隙合并 + 沿线覆盖率判 cut/fold + 游程并查集连通域（触边判背景）+ 共边邻接/度序列/折叠连通分组/外轮廓长；全程零 CV/ML 模型，无墨与未闭合如实空报且响应面字段集恒定，能力边界（仅轴对齐矩形刀版，斜角曲线不在内）写入 caveats 而非沉默；`POST /api/v1/packaging/panel-recognition`
- **安装清单校验引擎** — 第 26 引擎（Phase 49 交付清单面与安装态面，Phase 53 分裂出编译面与预检面，**未新增引擎**（installer_manifest 自身四面仍只计一枚引擎），引擎总数自 Phase 80 起为 28）：Inno Setup 脚本（setup.iss）分段解析 + R1~R14 十四条规则，逐条与**磁盘实况**对照并输出带行号/证据/判据的结构化 finding（未替换的模板常量残留、Source 载荷实存与通配零命中、卸载图标目标与 DefaultDirName 派生的卸载器实际位置一致性、载荷目录与安装根分裂、AppId GUID 形制与花括号转义、预建目录是否覆盖服务运行时真实数据根、注册表 ValueData 占位字面量、权限条目形态、per-user 区域与 PrivilegesRequired 的配对、Architectures 弃用标识、AppVersion/AppVerName 缺失、**写入型注册表条目缺卸载撤销声明**）；其中转义臂与后三条**不是推测**，各对应本机真 iscc 的一次实测退出码或一行告警原文并在判词里回指（缺 AppVersion/AppVerName 实测 exit=2 属编译级致命），R14 的判据出处另属一路：Phase 58 本机**真卸载实测**——卸载器 exit 0、安装根与卸载子键双双消失、四棵目录树归零，而 `HKCU\\Software\\Zaojing\\ZaojingAI` 的三枚值原样留守，故 Inno 的 [Registry] "只写不删"不是文档转述而是本机量出来的；只有编译器才定论得了的项如实判 info 并回显寻径结果，绝不出"编译已通过"的结论。另附安装态**只读**探测（HKCU/HKLM 卸载子键按 AppId 派生精确查找 + DisplayName 兜底 + 开始菜单组 + 应用数据根 + 便携标记逐项回显观察值，installed 仅由注册表真实命中派生，未安装绝不伪造）。Phase 53 的两个新面同为只读仪器：**编译证据核验**把 installer\\build-evidence.json 的每个数值与磁盘实读**重算**对账（脚本与产物各比字节数 + sha256 两路；证据自报的 compiled / fabricated 布尔一律不参与判定，只回显并标 ignored；compile_succeeded 由退出码 0、尾部真见完成行、产物在盘三条独立信号同立），判的是"产物是否真由当前脚本编译产出"，产物在盘 ≠ 本机已安装；**安装预检**按令牌权限 / 安装根候选 / 目标占用 / 可写性 / 产物 / 磁盘 / 载荷七路判"若要安装本机条件够不够"，每项三态（真 / 假 / 未探测），未探测进 unknowns 而非阻断，绝不把"没探测"写成"不可写"，写探针只在调用方显式交出的目标上跑且该目标必须落在本产品允许根内（越界一律 403，真实安装根结构上无法被指向）。编译器是否存在由固定目录→只读注册表→PATH **三路寻径**实测，不写死：本机实测命中 %ProgramFiles(x86)% 下的 Inno Setup 6，setup.iss 已真编译并留证（此前历轮正文里"本机没有 iscc、安装版从未真正编译过"是 PATH 单路探测造成的仪器伪影，非环境事实，已于 Phase 53 更正）。全程零写入、零落盘、零子进程、零网络；静默安装 / 写注册表 / 跑卸载器**自 Phase 58 起真实交付**，但交付在另一路仪器上（`installer_execution`，只走同一可执行文件的 CLI 子命令 `install-exec`，HTTP 面结构上到不了执行器），本清单引擎仍保持只读——R14 正是拿那一路的真卸载实测结果反过来校验这份脚本的；`GET /api/v1/installer/manifest/verify`、`GET /api/v1/installer/state`、`GET /api/v1/installer/artifact`、`GET /api/v1/installer/preflight`
- **桌面 Hub 引擎** — 第 27 引擎（Phase 50）：纯标准库 tkinter + urllib 的**原生桌面窗口客户端**，真连本机正在跑的服务，把健康／能力真值表／评测报告三条只读 GET 逐字段搬进窗口；窗口每一行都由 `render_rows()` 这一纯函数从真实快照派生，读不到就留空并写进 `unavailable_fields` 与降级原文，绝不把 0 或编出来的分数填上去（`/health` 与 `/capabilities` 自报引擎清单不一致时窗口显式写"同源=False"，HTTP 200 但形状不合造境契约时身份字段一律留空）；开窗**只读**——POST 通道唯一挂在显式点击的 `perform_action()` 上，唯一的写侧动作"跑一次真实评测"会在数据根落一份新报告且非幂等，故绝不自动触发；无显示会话时如实回 `created=False/reason=no_display_available`，数据层仍可为真。入口是同一可执行文件的子命令 `ZaojingAI hub [--base-url URL] [--window-probe] [--text]`（`hub --selftest` 走 CLI 自检）；本引擎是**客户端**，零新增 `/api/v1` 路由，故接线覆盖率分母不变；GUI 本体不进评测台（无显示会话会让评测脆断），真建窗、逐 Label 文本比对、真实 invoke 的活体判据在 L2/L4
- **自动更新** — 拉清单 + HMAC-SHA256 验签 + SHA-256 完整性 + 原子替换，未配置 feed 诚实 `no_update_configured`；`/api/v1/update` 系列
- **NAS 挂载** — 经操作系统原生 UNC/映射盘 + 纯标准库 WebDAV 客户端 + `msvcrt` 文件锁探测 + 断线重连，未配置诚实 `not_configured`；`/api/v1/nas` 系列
- **AI Provider 集成** — 通用 OpenAI-兼容 HTTP 客户端（chat/图像/流式/usage/退避/熔断）+ DPAPI 密钥加密 + §17 结构化任务规划，未配置后端点诚实 502；`/api/v1/provider` 系列
- **Adobe UXP 桥接** — §8 连接协议 / §9 统一帧 / §24 Adapter / §25 能力协商 / §26 能力门控的桥接管道真实端到端；文档写本体需真实宿主，无宿主诚实 502；`/api/v1/uxp` 系列
- **运维观测** — 真实请求计数/延迟分位 (p50/p95/p99)、进程 CPU/内存、系统诊断 8 项检查；Phase 52 起另报两组**反阻塞**仪表：`loop_lag`（事件循环看门狗每 0.05 s 的应然/实然差值，回显 mean 与 p95 的滞后毫秒及样本数，用于证明"某个请求把循环独占了一段"——长尾会推高 p95 而不动中位数，故任何请求都拿不到"墙钟很快所以没阻塞"的免罪符）与 `batch`（`in_flight` 在途文件任务数、`peak_concurrency` 本进程出现过的最大并发、`worker_cap` 服务级工作者上限），全部为**计数器口径**，即仪表测量的是服务自身的行为，不去测量服务无法左右的外部墙钟；能力真值表 Registry 2.0：逐项 engine/health_ready/测试计数/verified 由引擎装载状态与磁盘最新评估报告实时派生，无报告可核时诚实报 verified=false；`GET /api/v1/metrics`、`POST /api/v1/diagnostics/run`、`GET /api/v1/capabilities`
- **Web 控制台** — 自包含静态前端（十五面板：总览真值表/评估中心/色彩/包装/素材库/字体排版/设计工具/品牌智能/印前检查/知识库/改稿评审/运维运行时/流程与恢复/实体明细联查/配置与写操作），与 API 同端口挂载（Phase 35 设计：挂载最后注册不遮蔽 /docs 与 /api），零构建零外链、离线完全可用；Phase 46 视觉体系——Apple HIG × 玻璃拟态融合四主题（玄夜/雾白/青瓷/琥珀），html[data-theme] CSS 自定义属性令牌一键切换 + localStorage 持久化、首访跟随系统深浅偏好；玻璃材质（backdrop-filter）仅用于侧栏/卡片等 chrome 层，表格、代码块与输入域保持近实心以保数据密集可读性；三条无障碍降级路径（prefers-reduced-motion / prefers-reduced-transparency / prefers-contrast）齐备；无装饰性无限循环动画。只调用本服务真实端点并原样呈现响应与错误，needs_external 类能力（品牌语义识别）如实展示不伪造；栅格刀版面板识别自 Phase 47 起为第 25 引擎的真实几何反解（零 CV/ML），面板表与线型/拓扑摘要原样呈自响应，流程与恢复面板真实驱动工作流状态链（定义→校验→可视化→执行→实例查询，id 服务端返回并回填）、Phase 51 起该面板另以"工具清单"按钮实时呈现服务端派生的跨引擎工具面（72 工具 / 23 域 / 逐工具参数契约 / 未挂元信息即如实标 untagged，不存在第二份手写清单）、批量并发处理（逐文件真墙钟计时）与快照恢复（对真实文件做字节级备份+SHA-256、还原真把字节写回磁盘，不存在的文档如实标记不可恢复绝不伪造），实体明细联查面板按 id 只读深挖评测报告/报告对比/知识/素材/技能/开关/字体共 8 端点（Phase 42 探针以数据根全树哈希快照实测零磁盘副作用后方接线；曾因实证存在落盘副作用而拒接的只读候选 typography/resolve 与 tools/capability-report，Phase 45 起改归入配置与写操作面板，按写侧标准显式点击驱动），配置与写操作面板收录 13 个写侧端点（PUT /config/{{key}}、provider/configure·health、nas/configure·forget·cache/clear、update/configure·check、license/activate、delivery/package、typography/resolve、tools/capability-report、assets/{{id}}/license，Phase 45 契约探针实证：合法载荷真实响应、敌意/缺失载荷一律 4xx、密钥只回脱敏视图、副作用仅发生于本服务数据根，且只在用户点击按钮时发起、面板加载绝不自动写），未接线面自 Phase 71 起清零——原留守的 uxp 机器协议×4、需真实共享会话才能取数的 nas/download×1、风险动作 update/download·apply×2、进程级互斥 runtime/single-instance×1 逐枚过写侧五条件后转正，面板信息框里的拒接理由逐名改写为边界逐名在册（原「需真实 Provider 后端×4」与「素材缩略图二进制流」自 Phase 45/48 起已实证接线并归入 wired，原「需真实 NAS/共享会话×5」中的 connect/list/lock/reconnect 四枚、「进程级 runtime×2」中的 port-check 一枚、「layers 写需文档语境×1」的 PATCH 与 DELETE 两枚字面串（同一路由）自 Phase 65 起逐枚复核后转正，brand/semantic-insight 与 installer/execution 两枚自 Phase 70 起转正），端点接线覆盖报告实时派生自 GET /openapi.json 与控制台自身请求代码串的逐一匹配（当前 135 端点=已接线 135+未接线 0，非手写清单，幽灵接线即虚假声称会当场示警；Phase 50 桌面 Hub 客户端是本机服务的消费者而非新路由提供者，第 27 引擎零新增 /api/v1 端点，覆盖率分母不随之改变；Phase 51 新增的唯一路由 GET /api/v1/workflow/tools 已同步接线到流程与恢复面板的"工具清单"按钮，分母 130→131、分子 116→117；Phase 53 新增的唯一两条路由 GET /api/v1/installer/artifact 与 GET /api/v1/installer/preflight 已同步接线到总览真值表与控制台的安装域信息面，分母 131→133、分子 117→119，14 项未接线名单未变；Phase 56 新增的唯一路由 POST /api/v1/brand/semantic-insight 需真实 Provider 凭证（base_url＋Key＋配额）且属写侧透传，控制台无对应按钮故绝不自动发起，如实列入未接线面——分母 133→134、分子 119 不变、未接线 14→15；Phase 58 新增的唯一路由 GET /api/v1/installer/execution 是**只读对账面**（把安装执行台账与目录树/HKCU 现场复核后回显，绝不触发执行），因面板无对应按钮且执行器结构上只经 CLI 显式同意令牌可达，亦如实列入未接线——分母 134→135、分子 119 不变、未接线 15→16；Phase 65 既未新增也未退役任何路由，只是把留守的 16 项未接线逐枚复核后转正六枚：nas 四枚 connect/list/lock/reconnect 以「数据根全树哈希差集」实证零落盘（同域唯一会把字节写到调用方指定盘的 nas/download 仍留守），runtime/port-check 真 bind 后立刻释放、不留进程句柄（进程级互斥 single-instance 仍留守），layers 的 PATCH/DELETE 只改本服务数据根内的文档记录，且 P65 先补上服务端 confirm=true 门禁再谈接线——界面未勾选时连请求都不发，勾选后服务端对缺省/空/false/近似值一律 400 且零副作用。于是分母 135 不变、分子 119→125、未接线 16→10，125+10 与 119+16 是同一端点宇宙的两种切法；Phase 70 再把两枚只读/显式点击面转正——品牌语义 POST /api/v1/brand/semantic-insight 接在品牌面板（显式点击才经所配置 Provider 发真实外网请求，未配置时如实 502 needs_external，边界由 GET /brand/semantics 的 provider_configured 现场派生而非写死）与安装台账 GET /api/v1/installer/execution 接在运维面板（只读取数、现场重算五形制，零执行按钮：真装真卸仍只经同一可执行文件的 CLI 显式同意令牌）——分母 135 不变、分子 125→127、未接线 10→8，127+8 即上一代切法；Phase 71 把最后八枚逐枚过写侧五条件后转正——nas/download 的落盘目录 dest_dir 由用户显式给定，越出沙箱根的 subpath 被服务端拒绝并如实回 forbidden 且零落盘，update/download 真外网下载并校验 SHA-256（未配渠道诚实 no_update_configured），update/apply 发出请求前连过两道 confirm、sha 不符时服务端回滚且目标逐字节不被改坏，runtime/single-instance acquire 后随即 release 不滞留锁，uxp 四枚 handshake/poll/dispatch/result 无宿主时不靠假成功表态，502 的 detail 里 auth_failed/no_host_connected/capability_not_negotiated 逐形认出、fabricated:false 原文呈现且回扤 token 恒脱敏——分母 135 不变、分子 127→135、未接线 8→0，135+0 即当前切法），资源缺失时 GET / 诚实返回 webui_not_installed；启动后浏览器打开 `http://127.0.0.1:8000/` 即可

完整交互式 API 文档：启动后访问 `http://127.0.0.1:8000/docs`

---

## 🔧 系统要求

### 操作系统
- Windows 10/11 (64 位)

### Python 版本
- 无需单独安装。打包产物为自带 Python 运行时的独立可执行文件（构建环境为 Python 3.14），终端用户直接运行 EXE 即可。

### 可选宿主（仅真实文档操作需要）
- 应用本体可独立运行；只有需要在 Photoshop / Illustrator 内对真实文档做写入操作时，才需一台带许可证的 Adobe UXP 宿主（安装随包提供的 UXP 面板参考实现并连接）。未连接宿主时，相关文档写操作会诚实返回 502 `no_host_connected`，不会伪造结果；素材、印前、排版、色彩、包装、知识库、批量、审批改稿等服务端能力不依赖 Adobe。

### 硬件要求
- 内存：本轮未做内存压力基准，因此不设实测门槛——"8 GB / 推荐 16 GB"没有判据支撑，只能算经验值。真实占用以 `GET /api/v1/metrics` 回显为准（psutil 在场时给出 `memory_mb` / `cpu_percent` / `threads`，另给数据根所在盘的 `disk_free_gb`）。
- 磁盘：安装版三条 `[Files]` 载荷实测合计约 56 MB（主程序 EXE 约 55.8 MB + 两份随包 .md），便携 ZIP 约 55 MB；旧文案的"2GB 可用空间"从未被任何测量支撑过，按实况改为起步预留 1 GB 足够，数据根会随使用增长。**字节数每次构建都会变**，实况一律以 `installer\\build-evidence.json` 与 `GET /api/v1/installer/preflight`（需求字节 = 实测产物字节 + 实测载荷字节）的重算为准，本说明刻意不写死当前产物字节。
- 显卡：无要求。全链路是 CPU 原生实现（Pillow / numpy / fontTools 一类纯计算路径），服务与 30 个引擎里不存在任何 GPU、CUDA 或推理框架分支，因此"支持 GPU 加速"这句旧文案不成立，已删除。

---

## 🚀 快速开始

### 步骤 1: 下载安装包

双击运行 `zaojing-ai-{VERSION}-setup.exe`

### 步骤 2: 安装向导

1. 选择向导语言：默认「中文简体」，另有 English 回退项（`[Languages]` 以 `compiler:Languages\\ChineseSimplified.isl` 声明，中文条目在前即为默认语言）。这不是"无从核验"的回避：本机真编译器接受该条目并 exit=0；缺该 .isl 的构建机会在编译期直接报错，所以装出来的包必然带中文向导。
2. 选择安装位置：默认 `{{autopf}}\\造境科技`，而 `{{autopf}}` **不是单一真值**。本包声明 `PrivilegesRequired=lowest`，交互安装解析到当前用户的 `%LOCALAPPDATA%\\Programs\\造境科技`，并且**其下没有 ZaojingAI 子目录**——三条载荷直接落在安装根。旧文案写的 `C:\\Program Files\\造境科技\\ZaojingAI` 两头都不对。
3. 选择开始菜单文件夹：默认 `ZaojingAI`（取自 `DefaultGroupName`，不是"造境科技"）；`AllowNoIcons=yes`，取消勾选即不创建任何快捷方式。
4. 点击"安装"。向导**不含许可协议页**，也没有自定义任务页：setup.iss 未声明 `LicenseFile`，无 `[Tasks]` 段、无 `[Code]` 段，所以不会凭空多出一页要你勾选。
5. 结束页见下方「步骤 3: 启动程序」。

### 步骤 3: 启动程序

结束页有一个默认勾选的启动项，文字取自 `[Run]` 的 `{{cm:LaunchProgram,ZaojingAI}}`（中文向导下即"运行 ZaojingAI"），flags 为 `nowait postinstall skipifsilent`——也就是说交互装完会拉起程序，而静默安装（`/SILENT`）按 `skipifsilent` 不启动。未自动启动时：

- **开始菜单**：`ZaojingAI` 文件夹下共三条快捷方式，与 `[Icons]` 的三条声明逐字对应——`ZaojingAI` → `{{app}}\\ZaojingAI.exe`、`文档` → `{{userdocs}}\\ZaojingAI\\安装说明.md`、`卸载` → `{{uninstallexe}}`。
- **本包不创建桌面快捷方式**：`[Icons]` 三条 Name 全是 `{{group}}\\…` 形制，没有任何 `{{autodesktop}}` 条目；旧文案"桌面上会出现图标"没有依据，已删除。随包两份文档也不在安装根内，而是落在 `{{userdocs}}\\ZaojingAI`（即"文档\\ZaojingAI"）。
- **手动启动**：默认安装根下的 `%LOCALAPPDATA%\\Programs\\造境科技\\ZaojingAI.exe`；便携版则直接双击包内 `ZaojingAI.exe`。若在向导里取消了快捷方式，就只能走这条。

### 步骤 4: 验证安装

启动程序后，可以通过以下方式验证安装：

```bash
# 健康检查端点
curl http://localhost:8000/api/v1/health

# License 检查
curl http://localhost:8000/api/v1/license
```

预期响应逐字段照 `GET /api/v1/health` 的实现列出，不做"挑两个好看的字段"式省略：

```json
{{
  "status": "healthy",
  "version": "{VERSION}",
  "timestamp": "<datetime.now().isoformat()>",
  "uptime_seconds": 12.3,
  "mode": "installed",
  "data_root": "C:\\Users\\<用户名>\\AppData\\Local\\Zaojing",
  "frozen": true,
  "python": "3.14.x",
  "engines": {{ "<模块名>": "ready" }}
}}
```

- `status`：30 个引擎里已加载数 < 5 时如实回 `degraded`，不是永远 healthy。
- `mode`：便携版为 `portable`，安装版为 `installed`；`data_root` 直接回显真实数据根，便于核对是否被 `ZAOJING_DATA_DIR` 改道。
- `frozen`：冻结 EXE 为 true，源码运行为 false。
- `engines`：共 30 项，逐项 `ready` 或异常原文（加载失败不伪装成 ready）。

`GET /api/v1/license` 的真实字段是 `valid` / `status` / `machine_id` / `edition` / `degraded` / `degraded_reason` / `expiry_date`——未激活时按降级模式返回，绝不伪造为已激活。旧文案只回显 `status` 与 `version` 两个字段，会让人以为响应面就这么大，已按实现补全。

---

## 💼 便携版本（免安装）

除标准安装版外，本项目同时提供便携版本，适合不想安装或需要在多台机器 / U 盘上使用的场景。

### 获取方式

解压 `zaojing-ai-{VERSION}-portable.zip` 到任意目录（无需管理员权限）。

### 便携版包内结构

- `ZaojingAI.exe` - 主程序，双击即可运行
- `README.txt` - 便携版快速说明
- `docs\\` - 随包文档（安装说明、功能完成度审计报告、下载与校验说明）
- `Data\\Assets\\` - 素材库（数据随程序保存在本地）
- `Data\\Cache\\` - 缓存目录
- `Data\\Logs\\` - 日志目录

### 完整性校验（下载后请先做）

每次构建都会在 ZIP **旁边**另存一份 `zaojing-ai-{VERSION}-portable.zip.sha256`（sha256sum 兼容文本：`<64 位十六进制><两个空格><纯文件名>`）。校验的是"你手上这份 zip 与构建产物逐字节一致"，用下面任一方式即可：

```powershell
# 方式 A：Windows 自带 certutil（与 sidecar 里的哈希串对比）
certutil -hashfile zaojing-ai-{VERSION}-portable.zip SHA256

# 方式 B：Git Bash / WSL 的 sha256sum，-c 直接吃 sidecar 文件
sha256sum -c zaojing-ai-{VERSION}-portable.zip.sha256

# 方式 C：随仓库提供的独立校验器（纯标准库，退出码 0=通过 / 1=不符或缺席）
python verify-portable.py zaojing-ai-{VERSION}-portable.zip zaojing-ai-{VERSION}-portable.zip.sha256
```

诚实边界：本说明**不写死**当前 SHA-256。ZIP 里带目录时间戳，字节数与哈希每次构建都会微变，逐轮钉死一个具体值必然过期即误报（与 `installer\\build-evidence.json` 的既有纪律一致）。当前构建的真值只存在于两处实物：ZIP 外的 `.sha256` sidecar，与 `installer\\build-evidence.json` 的 `portable.sha256`。校验方法、下载位置与逐命令细节见随包 `下载与校验说明.md`。

#### verify-portable.py 使用细则

方式 C 里的 `verify-portable.py` 位于**仓库根目录**（`git clone` 后可见），刻意**未打进便携 ZIP**——把校验器塞进被验对象等于让它自证，判据失去独立性。三种典型调用：

```bash
# 1) 显式喂 sidecar（最常用）：sidecar 首行点名的文件必须与眼前 ZIP 同名
python verify-portable.py <zip 路径> <sidecar .sha256 路径>

# 2) 省略 sidecar：自动就近探测 installer/build-evidence.json 里的 portable.sha256
#    探测顺序：<zip 同级>/installer → <zip 上级>/installer → <zip 同级>/
python verify-portable.py zaojing-ai-{VERSION}-portable.zip

# 3) ZIP 被搬到别处、目录结构不规整时，用 --evidence 显式指定证据文件
python verify-portable.py <zip 路径> --evidence <build-evidence.json 路径>
```

判据与退出码（三值，任一不满足即非零，缺席绝不静默放过）：

- `0` 通过——现场重算的 SHA-256 与真值来源逐字符等（64 位十六进制，大小写归一）；且当 sidecar 或 `build-evidence` 记录了文件名时，那个名字必须与被验 ZIP 的**文件名**一致（张冠李戴防护：拿 A 的校验值去混验 B 一律 exit 1）。
- `1` 不符或缺席——ZIP 不存在/不是普通文件、sidecar 空文件或首行非法、真值哈希形制不合法（不是 64 位十六进制）、被验件哈希与期望不等、evidence 里 `portable.zip_name` 与被验件不同名……都归这一路。人类可读的 `[ERROR]`/`[FAIL]` 行会点名具体是哪一条不成立。
- `2` 用法错误——argparse 层拦下的缺参/多参/未知开关，不会真去读盘。

实现要点：SHA-256 用 **1 MB 分块**流式读入重算，不会为一份上百 MB 的 ZIP 撑爆内存；输出使用 `[OK]/[INFO]/[ERROR]/[FAIL]` 纯 ASCII 标记，`sys.stdout/stderr` 已用 `errors="backslashreplace"` 兜底，Windows GBK 控制台不会因中文段落抛 `UnicodeEncodeError`。想要看构建侧的具体哈希、字节数和文件条目数，直接读 `installer/build-evidence.json` 的 `portable` 块即可（含 `zip_name / sha256 / bytes / entries / file_count / sidecar_name`），这份证据由 `build-installer.py` 与 sidecar 同轮写出、由 L4 的 `P-zip-verify` 判据逐字段核验过。

### 安装版 vs 便携版对比

- **安装版**：载荷落在 `{{autopf}}\\造境科技`（`{{autopf}}` 是 Inno 的 auto 常量，**不是单一真值**：per-user 缺省即 `%LOCALAPPDATA%\\Programs\\造境科技`，管理员模式才是 `%ProgramFiles%\\造境科技`），创建开始菜单项与卸载注册项，另按 [Registry] 写 `HKCU\\Software\\造境科技\\ZaojingAI` 的 Version / InstallLocation / InstallDate，并按 [Dirs] 预建 `%LOCALAPPDATA%\\Zaojing` 下的 Assets / Cache / Logs / config；运行时数据根就是 `%LOCALAPPDATA%\\Zaojing`（见 `config_persistence.WINDOWS_DATA_ROOT`）。
- **便携版**：不写注册表、不装系统目录，数据随包保存在 Data\\ 内，删除文件夹即彻底卸载，适合移动办公与临时机器。

### 便携版验证安装

与安装版相同，启动后访问健康检查端点：

```bash
curl http://localhost:8000/api/v1/health
```

---

## ⚙️ 配置说明

### 环境变量

**安装程序不创建任何环境变量。** setup.iss 只有 `[Setup] / [Languages] / [Files] / [Icons] / [Dirs] / [Registry] / [Run]` 七段（本轮按段逐一数过），没有 `[Code]` 段，也不写 `HKCU\\Environment`；`[Registry]` 仅在当前用户区 `Software\\造境科技\\ZaojingAI` 下写入 Version / InstallLocation / InstallDate 三个字面值。下列变量全部是**运行时可选开关**，你按需自己设置，程序不设也能跑：

| 变量 | 真实读取位置 | 缺省行为 |
| --- | --- | --- |
| `ZAOJING_DATA_DIR` | `agent_service.resolve_data_root()` | 未设时：便携版用程序目录下 `Data\\`，安装版用 `%LOCALAPPDATA%\\Zaojing`；设置后一律以该目录为数据根 |
| `ZAOJING_HOST` | 服务启动入口 | `127.0.0.1` |
| `ZAOJING_PORT` | 服务启动入口 + 桌面 Hub 解析 base URL | `8000` |
| `ZAOJING_HUB_HEADLESS` | `desktop_hub_engine`（值为 `1` 时生效） | 强制 Hub 不建窗口，如实回 `created=False`，数据层仍可为真 |
| `ZAOJING_LICENSE_SECRET` | `license_service` | 未设时回落到内置默认密钥（这一点在能力判词里如实标注，不装作已配置） |
| `ZAOJING_DATA` | `adobe_bridge_engine` 的状态目录入参缺省值 | 仅当调用方未显式传目录时生效；服务侧显式传数据根下的 `uxp\\`，因此对本服务不构成逃逸 |

三条**不存在**的旧声明已删除：`ZAOJING_HOME` / `ZAOJING_ASSETS` / `ZAOJING_CACHE` 不被任何产品代码读取（本轮对服务主文件与 30 个引擎逐字串检索，只有随包文档自己的历史版本命中）；`DOUBAO_API_KEY` / `OPENAI_API_KEY` 也不从环境读取——`model_router` 自供注册表里的 `api_key_encrypted: "${{DOUBAO_API_KEY}}"` 只是一段字面占位串，评测台已把"变量拼接串不展开"钉为真值用例。Provider 密钥的真实入口是 `POST /api/v1/provider/configure`，落盘为 DPAPI 加密后的 `api_key_dpapi`，明文绝不写盘。

### 配置文件

**没有 config.yaml**：全产品零处读取该文件名（`import yaml` 只出现在 `model_router` 解析其自供注册表处，与"用户配置文件"无关）。用户设置由 `config_persistence.UserSettingsManager` 落为 JSON，位置由源码 `agent_service.get_settings()` 的分支决定，不是猜的：

- 安装版（默认）：`%LOCALAPPDATA%\\Zaojing\\user-settings.json`（本机实测该文件已在场）
- 便携版或设置了 `ZAOJING_DATA_DIR`：`<数据根>\\config\\user-settings.json`
- 读写走 `GET /api/v1/config` 与 `PUT /api/v1/config/{{key}}`（后者已在控制台「配置与写操作」面板接线，只在显式点击时发起，面板加载绝不自动写）

---

## 🛠️ 常见问题

### Q1: 安装时提示权限不足？

**A**: 本包按 per-user 模式设计（`PrivilegesRequired=lowest`），默认装到 `%LOCALAPPDATA%\\Programs\\造境科技`，**不需要管理员权限**，也不该用"以管理员身份运行"。这条设计有编译器背书而非自我声明：脚本同时触及 HKCU、`{{localappdata}}`、`{{userdocs}}` 三类 per-user 区域，而 Inno 的 `PrivilegesRequired` 缺省值就是 admin——本机首次真编译时 iscc 当场给出该告警原文，显式补 lowest 后二次真编译为零告警。若你真用管理员身份运行安装包，这些写入会落在提权账户而不是目标用户，典型表现是"装完却看不见数据根"。只有主动把安装位置改到 `C:\\Program Files\\...` 时才需要提权，而那已不是本包声明的路径。

### Q2: 杀毒软件误报？

**A**: 这是正常现象。由于使用了 PyInstaller 打包，某些杀毒软件可能会误报。请将安装目录添加到白名单即可。

### Q3: 启动时提示找不到 Photoshop/Illustrator？

**A**: 应用本体不依赖 Adobe，也不会因为没装 Adobe 而报错。只有当你确实要在宿主里对当前文档做写入操作时，才需要一台带许可证的 UXP 宿主；未连接宿主时相关端点诚实返回 502 `no_host_connected`，绝不伪造结果。旧文案"请确保已安装 Photoshop CC 2020+ 或 Illustrator 2020+"里的**版本门槛是杜撰**：代码里不存在任何宿主版本探测逻辑，因此不作为要求保留，只保留"需要宿主"这一真实条件。

### Q4: 如何查看日志？

**A**: 日志只有一个文件：`<数据根>\\logs\\agent.log`。实现是 `logging.FileHandler(LOGS_DIR / "agent.log")` 挂一个文件句柄，`LOGS_DIR = 数据根 / "logs"`，没有轮转处理器。

- 安装版：`%LOCALAPPDATA%\\Zaojing\\logs\\agent.log`（本机实测已在场）
- 便携版：`<程序目录>\\Data\\logs\\agent.log`
- 旧文案列出的 `error.log`、`access.log` 与"日志轮转"**在产品里不存在**（本轮逐字串检索：两个文件名只在随包文档自己的历史版本里命中），属文档想象，已删除。
- 安装版还会在 `%LOCALAPPDATA%\\Zaojing\\` 下预建 `Assets / Cache / Logs / config` 四个目录（`[Dirs]` 声明并带 `Permissions: users-modify`）；因 Windows 路径大小写不敏感，`[Dirs]` 建的 `Logs` 与运行时写入的 `logs` 是同一个目录，不是两份。

### Q5: 如何清理缓存？

**A**: 应用运行时的缓存与状态都保存在「数据目录」下，清理缓存即删除该目录内对应的子目录（如 `Cache`、`knowledge`、`recovery` 等），清理前请先退出应用。数据目录位置：

- 便携版：程序目录下的 `Data\\`
- 安装版：`%LOCALAPPDATA%\\Zaojing\\`
- 若设置了环境变量 `ZAOJING_DATA_DIR`，则以该变量指向的目录为准

例如（安装版，PowerShell 清理 Cache 子目录）：
```powershell
Remove-Item "$env:LOCALAPPDATA\\Zaojing\\Cache\\*" -Recurse -Force
```

### Q6: 如何升级版本？

**A**: 直接运行新版本安装包。真实行为是：Inno 依据同一枚 `AppId`（GUID `D4C8A1F2-7B3E-4E5A-9C6D-2F1B8A47E3D5`，脚本里首花括号双写）定位上次的安装目录并**就地覆盖**——`UsePreviousAppDir` 未在 `[Setup]` 声明即取缺省 yes；三条 `[Files]` 载荷一律带 `ignoreversion`；卸载器随之重写。**安装程序不会备份旧版本**：旧文案"安装程序会自动备份旧版本并升级"没有任何依据，setup.iss 既无 `[Code]` 段，也没有任何复制/备份指令。需要字节级回滚请使用应用自身的快照机制（`/api/v1/recovery` 系列，还原会把真实字节写回磁盘并按 SHA-256 复核），那是文档级别的能力，与安装器无关。数据根 `%LOCALAPPDATA%\\Zaojing` 不在 `[Files]` 里，升级不动它。

---

## 🔄 卸载程序

### 方法 1: 控制面板

1. 打开"控制面板" → "程序和功能"
2. 在列表里按 `ZaojingAI` 字样查找该条目。条目名由脚本决定：`[Setup]` 写的是 `AppName=ZaojingAI`（配当前 `AppVersion={VERSION}`，版本沿革见本节末），且**未声明 `UninstallDisplayName`**（本轮逐字检索 setup.iss：零命中），所以条目取 Inno 的缺省派生形制 `AppName + " " + AppVersion`，而不是 `#define MyAppName` 那串中文产品名——该宏及 `MyAppVersion`/`MyAppPublisher`/`MyAppURL`/`MyAppExeName` 四枚在整个脚本里都只出现在自己的定义行（逐字统计均为 1 次），属遗留声明，不影响产物。**显示名已由本机真装实测钉死为 `ZaojingAI 1.0.0`**（Phase 58：静默安装后只读枚举 `HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\D4C8A1F2-7B3E-4E5A-9C6D-2F1B8A47E3D5_is1` 读到 DisplayName 逐字为 `ZaojingAI 1.0.0`）。旧文案在此处写的是"缺省显示名的最终呈现仍以控制面板实况为准：验收纪律是绝不安装本机"——那句话在 Phase 58 之前属实，此后作废：本轮真装、真卸各一次，故此处直接给实测值，不再以"两种缺省形制都成立"的口径回避。同一枚实测还纠正了一处真实缺陷：`installer_manifest_engine.install_state` 的 DisplayName 兜底原先按**逐字相等**比较 `ZaojingAI`，对真实世界的 `ZaojingAI 1.0.0` 永不命中（app_id 为空时把"已安装"判成"未安装"），现改为按 `app_name` 起始匹配，与 `installer_runtime_engine` 同一条规则、同一批实测依据。**版本现状（Phase 76）**：上段实测取自 1.0.0；Phase 73 起 `[Setup]` 写的是 `AppVersion=1.0.1`，Phase 74-UI-A 起写的是 `AppVersion=1.0.2`，Phase 76 起写的是 `AppVersion=1.0.3`，按同一条 Inno 缺省规则（`AppName + " " + AppVersion`）派生的预期显示名为 `ZaojingAI 1.0.3`。三轮均未重装实测，故不把预期名写成实测值。**版本现状（Phase 77）**：Phase 77 起 `[Setup]` 写的是 `AppVersion=1.0.4`，按同一条 Inno 缺省规则（`AppName + " " + AppVersion`）派生的预期显示名为 `ZaojingAI 1.0.4`。本轮未重装实测，故仍不把预期名写成实测值。**版本现状（Phase 78）**：Phase 78 起 `[Setup]` 写的是 `AppVersion=1.0.5`，按同一条 Inno 缺省规则（`AppName + " " + AppVersion`）派生的预期显示名为 `ZaojingAI 1.0.5`。本轮仍未重装实测，故同样不把预期名写成实测值。**版本现状（Phase 79）**：Phase 79 起 `[Setup]` 写的是 `AppVersion=1.0.6`（评测引擎新增 Adobe 桥三枚黄金用例、executed 143→146、Registry 2.0 verified 32→35，属首次随包出货的评测覆盖扩张，按历轮产品改动轮 semver 递增的约定由 1.0.5 patch 到 1.0.6），按同一条 Inno 缺省规则（`AppName + " " + AppVersion`）派生的预期显示名为 `ZaojingAI 1.0.6`。本轮仍未重装实测，故同样不把预期名写成实测值。**版本现状（Phase 80）**：Phase 80 起 `[Setup]` 写的是 `AppVersion=1.0.7`（第 28 引擎 service_registry_engine 逐字下沉功能开关／工具注册表／技能运行时三域为单一真相源、评测台新增三枚独立黄金域、executed 146→149、Registry 2.0 verified 35→38 且 no_golden 5→2，属真实产品改动轮，按历轮 semver 递增约定由 1.0.6 patch 到 1.0.7），按同一条 Inno 缺省规则（`AppName + " " + AppVersion`）派生的预期显示名为 `ZaojingAI 1.0.7`。本轮仍未重装实测，故同样不把预期名写成实测值。**版本现状（Phase 81）**：Phase 81 起 `[Setup]` 写的是 `AppVersion=1.0.8`（第 29 引擎 event_engine 与第 30 引擎 delivery_engine 把 WebSocket 事件总线与一键交付归档逐字下沉为单一真相源、评测台新增 websocket_events 1 条、delivery_package 1 条 两枚独立黄金域、executed 149→151、Registry 2.0 verified 38→40 且服务侧 no_golden 2→0，属真实产品改动轮，按历轮 semver 递增约定由 1.0.7 patch 到 1.0.8），按同一条 Inno 缺省规则（`AppName + " " + AppVersion`）派生的预期显示名为 `ZaojingAI 1.0.8`。本轮仍未重装实测，故同样不把预期名写成实测值。
3. 右键选择"卸载"

### 方法 2: 开始菜单

开始菜单 `ZaojingAI` 文件夹 → 「卸载」快捷方式。组名取自 `DefaultGroupName=ZaojingAI`（不是"造境科技"），三条快捷方式的标签与 `[Icons]` 的 Name 逐字对应：`ZaojingAI`、`文档`、`卸载`。

### 方法 3: 手动卸载

运行卸载程序（默认安装根下）：

```
%LOCALAPPDATA%\\Programs\\造境科技\\unins000.exe
```

**卸载器文件名是 `unins000.exe`，不是 `Uninstall.exe`。** 旧文案的 `C:\\Program Files\\造境科技\\ZaojingAI\\Uninstall.exe` 两头都不错得不冤：既不是本包的默认安装根（per-user 模式落 `%LOCALAPPDATA%\\Programs\\造境科技`，且无 ZaojingAI 子目录），也不是 Inno 真实生成的文件名——这正是静态校验规则 R4 的判据，卸载图标必须指向 `{{uninstallexe}}` 常量，硬写别的路径必然指不到文件。

**注意**: 卸载器只撤销它自己按安装日志（`unins000.dat`）记录的动作；setup.iss **未声明 `[UninstallDelete]`**，即没有任何一条指令指向用户数据，所以数据根默认保留。`[Registry]` 三条也都是 `ValueType: string` 且不带任何 `Flags`（既无 `uninsdeletevalue` 也无 `uninsneveruninstall`），是否随卸载清理由卸载器按日志决定，本说明不作超出脚本声明的承诺。如需彻底清理，请手动检查并删除：

- 安装根：`%LOCALAPPDATA%\\Programs\\造境科技\\`（含 `ZaojingAI.exe`、`unins000.exe`、`unins000.dat`）
- 运行时数据根：`%LOCALAPPDATA%\\Zaojing\\`（`user-settings.json`、`logs\\`、`Assets\\`、`state\\`、`uxp\\` 等）
- 随包文档：`%USERPROFILE%\\Documents\\ZaojingAI\\`（`[Files]` 用 `{{userdocs}}\\ZaojingAI` 落两份 .md）
- 安装信息键：`HKCU\\Software\\造境科技\\ZaojingAI`（若仍在）
- 开始菜单组：`%APPDATA%\\Microsoft\\Windows\\Start Menu\\Programs\\ZaojingAI\\`

想在不碰注册表的前提下确认本机安装状态，可用只读探测 `GET /api/v1/installer/state`：它按 AppId 派生卸载键名（脚本里的转义写法与生效值派生同一枚键名，这一点有评测用例钉死），并在 HKCU / HKLM × 64/32 两视图枚举；`installed` 只由注册表真实命中派生，未命中绝不伪造。

---

## 📞 技术支持

### 常见问题 FAQ

常见安装与运维问题见本文档上方「常见问题 FAQ」；功能交付状态与已知边界详见随包的 `功能完成度审计报告.md`。

### 反馈渠道

- **Bug 报告**: 直接在 forum.qoder.com 发帖（**应用内没有“内置反馈按钮”**——本轮对服务与 Web 控制台逐字串检索：`agent_service.py` 当前 144 条路由（Phase 56 起，第 53 轮为 143）里含 feedback 的零条，`feedback`/`反馈` 在服务与 webui 三件套里仅在 style.css 注释里命中一次（按下视觉反馈，与故障反馈无关），既无端点也无控件，旧文案属杜撰，已删除）
- **功能建议**: forum.qoder.com
- **紧急问题**: 联系技术支持团队

---

## 📄 许可证

本软件遵循商业许可证。详细条款请参阅 LICENSE 文件。

---

**版本号**: V{VERSION}  
**发布日期**: {datetime.now().strftime('%Y-%m-%d')}  
**版权所有**: © {datetime.now().year} {COMPANY_NAME}

"""
    
    readme_file = SCRIPT_DIR / "安装说明.md"
    with open(readme_file, 'w', encoding='utf-8') as f:
        f.write(readme_content)
    
    print(f"[OK] 已生成安装说明：{readme_file}")
    return True

def main():
    """主函数"""
    print_section(f"{PROJECT_NAME} V{VERSION} 安装程序打包")
    print(f"生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    # 步骤 1: 检查依赖
    if not check_dependencies():
        print("[ERROR] 依赖检查失败，无法继续打包")
        return False
    
    # 步骤 2: 安装缺失的依赖
    if not install_dependencies():
        print("[ERROR] 依赖安装失败，无法继续打包")
        return False
    
    # 步骤 3: 同步引擎模块为可导入名（PyInstaller 依赖收集前提）
    if not prepare_core_modules():
        print("[ERROR] 核心模块同步失败")
        return False
    
    # 步骤 4: PyInstaller 打包
    if not build_with_pyinstaller():
        print("[ERROR] PyInstaller 打包失败")
        return False
    
    # 步骤 5: Inno Setup 打包（标准版）- 可选
    if not build_with_innosetup():
        print("[WARN] Inno Setup 打包失败，跳过标准版安装包生成")
    
    # 步骤 6: 生成安装说明文档（先生成，确保便携包内的 docs 为最新版）
    generate_readme()
    
    # 步骤 7: 创建便携版本 - 必需（此时会复制最新的安装说明.md）
    if not create_portable_version():
        print("[ERROR] 便携版本生成失败")
        return False
    
    # 总结
    print_section("打包完成")
    
    # 列出本轮真实产出的文件（只列磁盘上实际存在者，未生成者如实说明，绝不指向不存在文件）
    exe_path = OUTPUT_DIR / f'{APP_NAME}.exe'
    zip_path = PORTABLE_DIR.parent / f'zaojing-ai-{VERSION}-portable.zip'
    sidecar_path = zip_path.with_name(zip_path.name + '.sha256')
    setup_path = INSTALLER_DIR / f'zaojing-ai-{VERSION}-setup.exe'
    readme_path = SCRIPT_DIR / '安装说明.md'
    produced = []
    if exe_path.exists():
        produced.append((exe_path, "独立可执行文件"))
    if zip_path.exists():
        produced.append((zip_path, "便携版本压缩包"))
    if sidecar_path.exists():
        produced.append((sidecar_path, "便携包 SHA-256 校验件（sha256sum 兼容，供下载后逐字节核对）"))
    if setup_path.exists():
        produced.append((setup_path, "Windows 安装包（标准版 · Inno Setup）"))
    if readme_path.exists():
        produced.append((readme_path, "安装说明文档"))
    print("[FILES] 本轮真实生成的文件:")
    for _i, (_pth, _desc) in enumerate(produced, 1):
        print(f"   {_i}. {_pth} - {_desc}")
    if not setup_path.exists():
        loc = _iscc_lookup()
        if loc["found"]:
            print("   [INFO] 标准安装包未生成：编译器已定位（%s）但本轮未产出安装版，"
                  "   详见 installer/build-evidence.json 的 iscc_exit_code 与 iscc_warnings。" % loc["path"])
        else:
            print("   [INFO] 标准安装包未生成：ISCC.exe 三路寻径（固定目录/注册表/PATH）均未命中，")
            print("          该结论只描述本次寻径结果，不等同于本机未安装 Inno Setup；便携版 ZIP 已完整可用。")

    print("\n[NEXT] 下一步:")
    _n = 1
    if setup_path.exists():
        print(f"   {_n}. 测试安装包：运行 {setup_path.name}"); _n += 1
    if zip_path.exists():
        print(f"   {_n}. 解压测试便携版：解压 {zip_path.name} 到任意目录"); _n += 1
    print(f"   {_n}. 发布前代码签名（可选）：使用你自己的代码签名证书对 EXE / 安装包签名"); _n += 1
    print(f"   {_n}. 分发：官网下载 / 应用商店"); _n += 1
    
    print_section("感谢使用!")
    return True

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
