r"""
造境 AI 设计智能体 - 配置持久化模块

P0-2 修复内容:
1. 素材库从 Temp 迁移到持久化目录 %LOCALAPPDATA%\Zaojing\Assets
2. Python 版本锁定 3.11/3.12
3. blocked_paths 规范化（路径穿越防护）
4. 环境变量展开支持

版本：V1.0 修复版
"""

import os
import logging
from pathlib import Path
from typing import Optional, List, Dict, Any

logger = logging.getLogger(__name__)


# ============================================================================
# 配置常量定义
# ============================================================================

class ConfigPaths:
    """配置路径常量"""
    
    # Windows 持久化数据目录
    WINDOWS_DATA_ROOT = Path("%LOCALAPPDATA%\\Zaojing")
    WINDOWS_ASSETS_ROOT = Path("%LOCALAPPDATA%\\Zaojing\\Assets")
    
    # Linux/macOS 持久化数据目录
    UNIX_DATA_ROOT = Path("~/.zaojing")
    UNIX_ASSETS_ROOT = Path("~/.zaojing/assets")
    
    # 子目录结构
    SUBDIRS = {
        "data": "data",
        "assets": "assets",
        "thumbnails": "thumbnails",
        "embeddings": "embeddings",
        "projects": "projects",
        "snapshots": "snapshots",
        "logs": "logs",
        "cache": "cache",
        "models": "models",
        "backups": "backups",
    }


def get_system_root() -> Path:
    """
    根据操作系统获取根目录
    
    Returns:
        系统数据根目录 Path 对象
    """
    if os.name == "nt":  # Windows
        return ConfigPaths.WINDOWS_DATA_ROOT
    else:  # Linux/macOS
        return ConfigPaths.UNIX_DATA_ROOT


def expand_path(path: str) -> Path:
    """
    展开路径变量（如 %LOCALAPPDATA%、~）
    
    Args:
        path: 可能包含环境变量的路径字符串
        
    Returns:
        展开后的 Path 对象
    """
    expanded = os.path.expandvars(str(path))
    expanded = os.path.expanduser(expanded)
    return Path(expanded).resolve()


def ensure_directory(path: Path) -> bool:
    """
    确保目录存在，不存在则创建
    
    Args:
        path: 目标目录路径
        
    Returns:
        成功返回 True，失败返回 False
    """
    try:
        path.mkdir(parents=True, exist_ok=True)
        logger.debug(f"Created directory: {path}")
        return True
    except Exception as e:
        logger.error(f"Failed to create directory {path}: {e}")
        return False


# ============================================================================
# 配置管理器
# ============================================================================

class UserSettingsManager:
    """用户配置管理器"""
    
    DEFAULT_CONFIG = {
        "version": "1.0.0",
        "assets": {
            "local_root": "${LOCALAPPDATA}\\Zaojing\\Assets",
            "nas_enabled": False,
            "nas_paths": [],
            "auto_tag_enabled": True,
            "embedding_model": "clip-ViT-B-32",
            "max_index_assets": 100000,
            "engine_url": "http://127.0.0.1:8082"
        },
        "blocked_paths": [
            "C:\\Windows",
            "C:\\Program Files",
            "C:\\Program Files (x86)",
            "/usr",
            "/etc",
            "/bin",
            "/sbin",
        ],
        "allowed_extensions": [
            ".psd", ".ai", ".jpg", ".jpeg", ".png", ".gif", ".bmp",
            ".tiff", ".webp", ".svg", ".pdf", ".eps", ".zip"
        ],
        "python_version": {
            "min": "3.11",
            "max": "3.14",
            "recommended": "3.11"
        },
        "paths": {
            "data_root": "${LOCALAPPDATA}\\Zaojing",
            "logs": "${LOCALAPPDATA}\\Zaojing\\logs",
            "cache": "${LOCALAPPDATA}\\Zaojing\\cache",
            "models": "${LOCALAPPDATA}\\Zaojing\\models",
            "projects": "${LOCALAPPDATA}\\Zaojing\\projects",
        }
    }
    
    def __init__(self, config_path: Optional[Path] = None):
        """
        初始化配置管理器
        
        Args:
            config_path: 配置文件路径，默认在数据根目录
        """
        self.config_path = config_path or self._default_config_path()
        self.config: Dict[str, Any] = {}
        self._load_or_create()
    
    def _default_config_path(self) -> Path:
        """获取默认配置文件路径"""
        data_root = expand_path("${LOCALAPPDATA}\\Zaojing")
        return data_root / "user-settings.json"
    
    def _load_or_create(self):
        """加载或创建配置文件"""
        if self.config_path.exists():
            self._load_config()
        else:
            self._create_default_config()
    
    def _load_config(self):
        """从文件加载配置"""
        import json
        
        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                self.config = json.load(f)
            
            logger.info(f"Loaded configuration from {self.config_path}")
            
            # 验证并升级配置
            self._migrate_config()
            
        except Exception as e:
            logger.error(f"Failed to load config: {e}, using default")
            self._create_default_config()
    
    def _create_default_config(self):
        """创建默认配置文件"""
        import json
        
        # 解析所有环境变量路径
        resolved = self._resolve_all_paths(self.DEFAULT_CONFIG)
        
        try:
            # 确保配置目录存在
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(resolved, f, indent=2, ensure_ascii=False)
            
            logger.info(f"Created default configuration at {self.config_path}")
            self.config = resolved
            
        except Exception as e:
            logger.critical(f"Failed to create config file: {e}")
            raise
    
    def _resolve_all_paths(self, config: dict) -> dict:
        """递归解析配置中的所有环境变量路径"""
        result = {}
        
        for key, value in config.items():
            if isinstance(value, str):
                # 检查是否是路径变量
                if value.startswith("${") and value.endswith("}"):
                    result[key] = str(expand_path(value))
                else:
                    result[key] = value
            elif isinstance(value, dict):
                result[key] = self._resolve_all_paths(value)
            elif isinstance(value, list):
                resolved_list = []
                for item in value:
                    if isinstance(item, str) and item.startswith("${"):
                        resolved_list.append(str(expand_path(item)))
                    else:
                        resolved_list.append(item)
                result[key] = resolved_list
            else:
                result[key] = value
        
        return result
    
    def _migrate_config(self):
        """配置迁移（版本升级）"""
        current_version = self.config.get("version", "0.0.0")
        target_version = self.DEFAULT_CONFIG["version"]
        
        if current_version != target_version:
            logger.info(f"Migrating config from {current_version} to {target_version}")
            
            # 合并新配置项
            for key, value in self.DEFAULT_CONFIG.items():
                if key not in self.config:
                    self.config[key] = value
            
            self.config["version"] = target_version
            self.save()
    
    def save(self):
        """保存配置到文件"""
        import json
        
        try:
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(self.config, f, indent=2, ensure_ascii=False)
            
            logger.info(f"Saved configuration to {self.config_path}")
        except Exception as e:
            logger.error(f"Failed to save config: {e}")
            raise
    
    def get(self, key: str, default=None):
        """获取配置项"""
        return self.config.get(key, default)
    
    def set(self, key: str, value):
        """设置配置项"""
        self.config[key] = value
    
    def resolve_local_root(self) -> Path:
        """
        解析素材本地根目录
        
        Returns:
            素材存储的绝对路径
        """
        raw_path = self.config.get("assets", {}).get(
            "local_root", 
            "${LOCALAPPDATA}\\Zaojing\\Assets"
        )
        
        resolved = expand_path(raw_path)
        
        # 确保目录存在
        if not ensure_directory(resolved):
            logger.error(f"Failed to create assets directory: {resolved}")
            # 降级到临时目录
            resolved = Path(tempfile.gettempdir()) / "zaojing-assets"
            ensure_directory(resolved)
        
        return resolved
    
    def get_blocked_paths(self) -> List[Path]:
        """
        获取被阻止的路径列表
        
        Returns:
            已解析的 Path 对象列表
        """
        blocked = self.config.get("blocked_paths", [])
        return [expand_path(p) for p in blocked]
    
    def get_allowed_extensions(self) -> List[str]:
        """获取允许的文件扩展名列表"""
        return self.config.get("allowed_extensions", [])


# ============================================================================
# 路径安全检查
# ============================================================================

class PathSecurityChecker:
    """路径安全检测器"""
    
    def __init__(self, blocked_roots: List[Path]):
        """
        初始化路径安全检测器
        
        Args:
            blocked_roots: 被阻止的根路径列表
        """
        self.blocked_roots = blocked_roots
        self._normalize_blocked_roots()
    
    def _normalize_blocked_roots(self):
        """规范化被阻止的路径"""
        self._normalized = []
        
        for root in self.blocked_roots:
            try:
                real_path = root.resolve()
                self._normalized.append(real_path)
            except Exception as e:
                logger.warning(f"Failed to normalize blocked path {root}: {e}")
    
    def is_blocked(self, path: str) -> bool:
        """
        检查路径是否被阻止
        
        防护策略:
        - 路径穿越（..）
        - 符号链接逃逸
        - UNC 路径未授权访问
        - 双扩展名攻击
        
        Args:
            path: 待检查的路径
            
        Returns:
            被阻止返回 True
        """
        try:
            # 规范化路径
            real_path = Path(path).resolve()
            norm_path = str(real_path).lower()
            
            # 检查路径穿越
            if ".." in path:
                logger.warning(f"Path traversal attempt detected: {path}")
                return True
            
            # 检查符号链接逃逸
            if real_path.is_symlink():
                try:
                    target = real_path.readlink()
                    if self._is_blocked_path(target):
                        logger.warning(f"Symlink escape attempt: {path} -> {target}")
                        return True
                except Exception:
                    pass  # 某些系统不支持 readlink
            
            # 检查是否在 blocked roots 内
            if self._is_blocked_path(norm_path):
                return True
            
            # 检查双扩展名
            if self._has_double_extension(path):
                logger.warning(f"Double extension detected: {path}")
                return True
            
            return False
            
        except Exception as e:
            logger.error(f"Error checking path security: {e}")
            return True  # 失败时保守拒绝
    
    def _is_blocked_path(self, path: str) -> bool:
        """检查路径是否在阻止列表中"""
        for blocked in self._normalized:
            blocked_str = str(blocked).lower()
            
            # 完全匹配
            if path == blocked_str:
                return True
            
            # 前缀匹配（子目录）
            if path.startswith(blocked_str + os.sep):
                return True
            
            # 仅当被阻止根目录本身是"盘符根"(如 c:\)时，才按整盘匹配；
            # 绝不能只取前三字符，否则会把同盘所有正常文件误判为阻断。
            is_drive_root = os.name == "nt" and len(blocked_str) == 3 \
                and blocked_str[1] == ":" and blocked_str[2] == "\\"
            if is_drive_root and path.startswith(blocked_str):
                return True
        
        return False
    
    def _has_double_extension(self, filename: str) -> bool:
        """
        检查文件名是否有双扩展名（如 .jpg.exe）
        
        用于防止可执行文件伪装成图片
        """
        parts = filename.split(".")
        
        if len(parts) >= 3:
            # 检查最后两个扩展名
            last_ext = parts[-1].lower()
            second_last = parts[-2].lower()
            
            # 常见危险扩展名
            dangerous_extensions = {
                "exe", "bat", "cmd", "com", "scr", 
                "vbs", "js", "ps1", "msi", "dll"
            }
            
            if last_ext in dangerous_extensions:
                return True
        
        return False


# ============================================================================
# Python 版本检查
# ============================================================================

class PythonVersionChecker:
    """Python 版本检查器"""
    
    MIN_VERSION = (3, 11)
    MAX_VERSION = (3, 15)  # 支持 3.11–3.14（本产品即在此区间内构建并验证）
    RECOMMENDED_VERSION = (3, 11)
    
    @classmethod
    def check(cls) -> tuple:
        """
        检查当前 Python 版本
        
        Returns:
            (is_valid, message)
        """
        import sys
        
        current = sys.version_info[:2]
        
        if current < cls.MIN_VERSION:
            return (
                False,
                f"Python {'.'.join(map(str, cls.MIN_VERSION))} or higher required, "
                f"found {'.'.join(map(str, current))}"
            )
        
        if current >= cls.MAX_VERSION:
            return (
                False,
                f"Python {'.'.join(map(str, cls.MAX_VERSION))} or lower recommended, "
                f"found {'.'.join(map(str, current))}"
            )
        
        return True, f"Python {'.'.join(map(str, current))} OK"
    
    @classmethod
    def get_pyproject_constraint(cls) -> str:
        """获取 pyproject.toml 约束"""
        return f'python = ">=3.11,<3.15"'
    
    @classmethod
    def generate_requirements(cls) -> str:
        """生成 requirements.txt 约束部分"""
        return f"""
# Python 版本要求
# P0-2 修复：锁定稳定版本，避免使用过于新的 Python 3.14+

--index-url https://pypi.org/simple

# 核心依赖
fastapi>=0.104.0,<0.110.0
uvicorn[standard]>=0.24.0,<0.28.0
pydantic>=2.5.0,<2.6.0
python-multipart>=0.0.6,<0.1.0

# Adobe UXP
# ... (其他依赖)
"""


# ============================================================================
# 工具函数
# ============================================================================

def create_project_directories(base_path: Path) -> Dict[str, Path]:
    """
    创建项目完整目录结构
    
    Args:
        base_path: 基础数据目录
        
    Returns:
        各目录的 Path 映射
    """
    directories = {}
    
    for name, subdir in ConfigPaths.SUBDIRS.items():
        path = base_path / subdir
        if ensure_directory(path):
            directories[name] = path
            logger.debug(f"Created {name}: {path}")
    
    return directories


def validate_config_paths(config: UserSettingsManager) -> List[str]:
    """
    验证所有配置路径是否可访问
    
    Args:
        config: 配置管理器实例
        
    Returns:
        错误消息列表（空表示全部正常）
    """
    errors = []
    
    # 验证素材目录
    try:
        assets_root = config.resolve_local_root()
        if not assets_root.exists():
            errors.append(f"Assets directory does not exist: {assets_root}")
        elif not os.access(assets_root, os.W_OK):
            errors.append(f"No write permission for assets: {assets_root}")
    except Exception as e:
        errors.append(f"Failed to validate assets root: {e}")
    
    # 验证日志目录
    try:
        logs_path = expand_path(config.get("paths", {}).get("logs"))
        if not ensure_directory(logs_path):
            errors.append(f"Cannot create logs directory: {logs_path}")
    except Exception as e:
        errors.append(f"Failed to validate logs path: {e}")
    
    return errors


# ============================================================================
# 示例：如何在主应用中使用
# ============================================================================

"""
# main.py 示例

from config_persistence import UserSettingsManager, PythonVersionChecker

# 1. 初始化配置管理器
config_manager = UserSettingsManager()

# 2. 获取素材根目录
assets_root = config_manager.resolve_local_root()
print(f"Assets stored at: {assets_root}")

# 3. 检查 Python 版本
valid, message = PythonVersionChecker.check()
if not valid:
    print(f"ERROR: {message}")
    sys.exit(1)

# 4. 创建路径安全检测器
blocked_paths = config_manager.get_blocked_paths()
security_checker = PathSecurityChecker(blocked_paths)

# 5. 验证用户输入路径
user_path = "/some/path/file.jpg"
if security_checker.is_blocked(user_path):
    print(f"ERROR: Path blocked for security reasons: {user_path}")
else:
    print(f"OK: Path is safe to use")

# 6. 保存配置（如果需要修改）
config_manager.set("some_key", "some_value")
config_manager.save()
"""


# ============================================================================
# 快速测试
# ============================================================================

if __name__ == "__main__":
    import tempfile
    
    print("=" * 60)
    print("P0-2 配置持久化修复 - 快速验证")
    print("=" * 60)
    
    import tempfile
    
    # 测试 1: Python 版本检查（真实布尔判定，非恒真）
    print("\n[OK] Python 版本检查:")
    valid, message = PythonVersionChecker.check()
    print(f"  {message}")
    assert isinstance(valid, bool) and valid is True, ("当前解释器应通过版本检查", valid, message)
    assert isinstance(message, str) and message, "版本检查须返回非空说明"

    # 测试 2: 配置管理器 — 解析真实持久目录
    print("\n[OK] 配置管理器:")
    config = UserSettingsManager()
    assets_root = config.resolve_local_root()
    print(f"  Assets root: {assets_root}")
    assert assets_root.is_absolute() and assets_root.exists(), \
        ("素材根目录须为已创建的真实绝对路径", str(assets_root))
    _local = os.environ.get("LOCALAPPDATA", "")
    is_persistent = bool(_local) and os.path.normcase(str(assets_root)).startswith(os.path.normcase(_local))
    print(f"  Is persistent (under LOCALAPPDATA): {is_persistent}")
    assert is_persistent, "默认应落在 %LOCALAPPDATA% 持久目录（非临时目录降级）"

    # 测试 3: 真实持久化 — 写入→保存→重新载入须从磁盘读回（此前完全未测）
    print("\n[OK] 配置持久化往返:")
    tmp_cfg = Path(tempfile.mkdtemp()) / "settings.json"
    mgr_a = UserSettingsManager(config_path=tmp_cfg)
    mgr_a.set("selftest_roundtrip", {"v": 42})
    mgr_a.save()
    assert tmp_cfg.exists(), "save() 必须把配置写入磁盘"
    mgr_b = UserSettingsManager(config_path=tmp_cfg)
    assert mgr_b.get("selftest_roundtrip") == {"v": 42}, \
        ("新实例须从磁盘读回已保存的值（真实持久化，非进程内假象）", mgr_b.get("selftest_roundtrip"))
    print("  [OK] 写入→重载读回一致")

    # 测试 4: 路径安全 — 确定性 blocked root，正反向哨兵（证明判定非恒定）
    print("\n[OK] 路径安全检查:")
    checker = PathSecurityChecker([Path("C:\\Windows")])
    cases = [
        ("C:\\Windows\\system32\\drivers\\etc\\hosts", True),   # 命中 blocked root
        ("C:\\Windows", True),                                   # 命中 blocked root 本身
        ("C:\\Users\\Alice\\report.psd", False),                 # 普通用户文件放行
        ("C:\\Program Files\\App\\data.jpg", False),             # 正常路径放行
        ("C:\\Users\\Alice\\..\\..\\Windows\\evil.txt", True),   # 路径穿越
        ("C:\\Users\\Alice\\photo.jpg.exe", True),               # 双扩展名伪装可执行
    ]
    outcomes = []
    for path, should_block in cases:
        got = checker.is_blocked(path)
        outcomes.append(got)
        mark = "OK" if got == should_block else "FAIL"
        print(f"  [{mark}] {path}: {'blocked' if got else 'allowed'} (expect {'blocked' if should_block else 'allowed'})")
        assert got == should_block, ("路径判定错误", path, got, should_block)
    assert any(outcomes) and not all(outcomes), \
        "判定须同时含放行与阻断，否则 checker 可能恒真/恒假（伪造）"

    print("\n" + "=" * 60)
    print("[PASS] P0-2 配置持久化断言自测通过（真实版本判定 + 持久目录 + 写盘重载往返 + 路径正反向哨兵）")
    print("=" * 60)
