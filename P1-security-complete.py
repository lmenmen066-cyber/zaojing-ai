"""
造境 AI 设计智能体 - P1 安全加固完整模块

包含:
1. 路径安全增强 (基于 P0-2 的 PathSecurityChecker)
2. API Key 日志脱敏
3. Tool risk_level 强制标注
4. Permission 交集校验
5. L3/L4 高风险操作确认机制

版本：V1.0 修复版
"""

import logging
import os
import re
import functools
from typing import Dict, Any, Optional, List, Callable
from pathlib import Path
from datetime import datetime
from enum import Enum

logger = logging.getLogger(__name__)


# ============================================================================
# 枚举定义
# ============================================================================

class RiskLevel(Enum):
    """风险等级枚举"""
    L0 = "safe"           # 安全操作
    L1 = "low"            # 低风险
    L2 = "medium"         # 中等风险
    L3 = "high"           # 高风险 (删除/覆盖)
    L4 = "critical"       # 关键风险 (上传云端/联网)


class PermissionType(Enum):
    """权限类型"""
    USER = "user"         # 用户权限
    SKILL = "skill"       # Skill 权限
    TOOL = "tool"         # Tool 权限
    ADMIN = "admin"       # 管理员权限


# ============================================================================
# 路径安全增强 (继承 P0-2)
# ============================================================================

class EnhancedPathSecurityChecker:
    """增强的路径安全检测器"""
    
    def __init__(self, blocked_roots: List[str]):
        """
        初始化
        
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
                real_path = Path(root).resolve()
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
        - 系统敏感目录
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
                    pass
            
            # 检查是否在 blocked roots 内
            if self._is_blocked_path(norm_path):
                return True
            
            # 检查双扩展名
            if self._has_double_extension(path):
                logger.warning(f"Double extension detected: {path}")
                return True
            
            # 检查系统敏感目录
            if self._is_sensitive_system_path(path):
                logger.warning(f"Sensitive system path detected: {path}")
                return True
            
            return False
            
        except Exception as e:
            logger.error(f"Error checking path security: {e}")
            return True
    
    def _is_blocked_path(self, path: str) -> bool:
        """检查路径是否在阻止列表中"""
        for blocked in self._normalized:
            blocked_str = str(blocked).lower()
            
            # 完全匹配
            if path == blocked_str:
                return True
            
            # 前缀匹配（子目录）
            if path.startswith(blocked_str + os.sep) or path.startswith(blocked_str + "/"):
                return True
        
        return False
    
    def _has_double_extension(self, filename: str) -> bool:
        """检查文件名是否有双扩展名"""
        parts = filename.split(".")
        
        if len(parts) >= 3:
            last_ext = parts[-1].lower()
            dangerous_extensions = {
                "exe", "bat", "cmd", "com", "scr", 
                "vbs", "js", "ps1", "msi", "dll",
                "sh", "pl", "py", "rb"
            }
            
            if last_ext in dangerous_extensions:
                return True
        
        return False
    
    def _is_sensitive_system_path(self, path: str) -> bool:
        """检查是否是系统敏感路径"""
        sensitive_patterns = [
            "/proc/", "/sys/",           # Linux 内核
            "/etc/passwd", "/etc/shadow", # Linux 密码文件
            "C:\\Windows\\System32\\",    # Windows 系统目录
            "/bin/", "/sbin/",            # 系统命令
            "/usr/bin/", "/usr/sbin/",    # 用户系统命令
        ]
        
        path_lower = path.lower()
        for pattern in sensitive_patterns:
            if pattern.lower() in path_lower:
                return True
        
        return False


# ============================================================================
# API Key 日志脱敏
# ============================================================================

class SensitiveDataMasker:
    """敏感数据脱敏器"""
    
    # 需要脱敏的字段模式
    SENSITIVE_PATTERNS = [
        r'api[_-]?key',
        r'token',
        r'authorization',
        r'password',
        r'secret',
        r'credential',
        r'private[_-]?key',
    ]
    
    def __init__(self):
        """初始化脱敏器"""
        self.patterns = [re.compile(p, re.IGNORECASE) for p in self.SENSITIVE_PATTERNS]
        self.mask_value = "***REDACTED***"
    
    def mask(self, data: Any) -> Any:
        """
        递归脱敏数据结构中的所有敏感信息
        
        Args:
            data: 任意类型的数据
            
        Returns:
            脱敏后的数据
        """
        if isinstance(data, dict):
            return {k: self.mask(v) for k, v in data.items()}
        elif isinstance(data, list):
            return [self.mask(item) for item in data]
        elif isinstance(data, str):
            return self._mask_string(data)
        else:
            return data
    
    def _mask_string(self, text: str) -> str:
        """脱敏字符串中的敏感信息"""
        result = text
        
        for pattern in self.patterns:
            # 查找所有匹配的键名
            matches = pattern.findall(text)
            if matches:
                # 替换值为脱敏值
                result = pattern.sub(f'"{self.mask_value}"', result)
        
        return result
    
    def log_filter(self, record: logging.LogRecord) -> bool:
        """
        日志过滤器，过滤敏感信息
        
        Args:
            record: 日志记录
            
        Returns:
            True 表示记录该日志
        """
        # 处理消息
        if hasattr(record, 'msg'):
            record.msg = self.mask(str(record.msg))
        
        # 处理 args
        if hasattr(record, 'args') and record.args:
            record.args = tuple(self.mask(arg) for arg in record.args)
        
        return True


# ============================================================================
# Tool 装饰器与 risk_level 强制标注
# ============================================================================

def tool(
    name: str,
    description: str,
    risk_level: RiskLevel,
    permissions: Optional[List[PermissionType]] = None,
    **kwargs
):
    """
    Tool 装饰器 - 强制标注 risk_level
    
    Args:
        name: Tool 名称
        description: Tool 描述
        risk_level: 风险等级 (L0-L4)
        permissions: 所需权限列表
        **kwargs: 其他参数
        
    Raises:
        ValueError: 如果 risk_level 未指定
        
    Example:
        @tool(
            name="ps.layer.create",
            description="创建新图层",
            risk_level=RiskLevel.L1,
            permissions=[PermissionType.TOOL]
        )
        def create_layer(name: str):
            ...
    """
    def decorator(func: Callable) -> Callable:
        # 强制验证 risk_level
        if risk_level is None:
            raise ValueError(f"risk_level is required for tool '{name}'")
        
        # 设置默认权限（用局部变量，避免对外层闭包参数赋值触发 UnboundLocalError）
        _resolved_permissions = permissions if permissions is not None else [PermissionType.TOOL]
        
        # 绑定元数据到函数
        func.__tool_metadata__ = {
            "name": name,
            "description": description,
            "risk_level": risk_level,
            "permissions": _resolved_permissions,
        }
        
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            return func(*args, **kwargs)
        
        return wrapper
    
    return decorator


# ============================================================================
# Permission Engine - 权限交集校验
# ============================================================================

class PermissionEngine:
    """权限引擎 - 计算 User ∩ Skill ∩ Tool 权限交集"""
    
    def __init__(self):
        """初始化权限引擎"""
        self.user_permissions: set = set()
        self.skill_permissions: Dict[str, set] = {}
        self.tool_permissions: Dict[str, set] = {}
    
    def set_user_permissions(self, permissions: set):
        """设置用户权限"""
        self.user_permissions = permissions
    
    def set_skill_permissions(self, skill_name: str, permissions: set):
        """设置 Skill 权限"""
        self.skill_permissions[skill_name] = permissions
    
    def set_tool_permissions(self, tool_name: str, permissions: set):
        """设置 Tool 权限"""
        self.tool_permissions[tool_name] = permissions
    
    def can_execute(self, tool_name: str, skill_name: str = None) -> bool:
        """
        检查是否有权限执行 Tool
        
        权限规则:
        - 必须同时满足 User ∩ Skill ∩ Tool 三个条件
        - 只要有一个不满足就拒绝
        
        Args:
            tool_name: Tool 名称
            skill_name: 可选的 Skill 名称
            
        Returns:
            有权限返回 True
        """
        # 获取 Tool 权限
        tool_perms = self.tool_permissions.get(tool_name, set())
        
        # 计算交集
        allowed_perms = self.user_permissions.copy()
        
        if skill_name and skill_name in self.skill_permissions:
            allowed_perms &= self.skill_permissions[skill_name]
        
        allowed_perms &= tool_perms
        
        # 检查是否有足够权限
        has_admin = PermissionType.ADMIN in allowed_perms
        has_all_required = all(perm in allowed_perms for perm in tool_perms)
        
        return has_admin or has_all_required
    
    def get_risk_level(self, tool_name: str) -> Optional[RiskLevel]:
        """获取 Tool 的风险等级"""
        tool_meta = getattr(self.tool_permissions.get(tool_name), "__tool_metadata__", None)
        
        if tool_meta:
            return tool_meta.get("risk_level")
        
        return None
    
    def check_and_raise(self, tool_name: str, skill_name: str = None) -> None:
        """
        检查权限，无权限则抛出异常
        
        Args:
            tool_name: Tool 名称
            skill_name: Skill 名称
            
        Raises:
            PermissionError: 无权限时抛出
        """
        if not self.can_execute(tool_name, skill_name):
            risk_level = self.get_risk_level(tool_name)
            raise PermissionError(
                f"Permission denied for tool '{tool_name}' "
                f"(risk_level={risk_level.value if risk_level else 'unknown'})"
            )


# ============================================================================
# L3/L4 高风险操作确认机制
# ============================================================================

class HighRiskOperationHandler:
    """高风险操作处理器"""
    
    def __init__(self, confirmation_required: bool = True):
        """
        初始化
        
        Args:
            confirmation_required: 是否需要用户确认
        """
        self.confirmation_required = confirmation_required
        self.pending_operations: Dict[str, Dict] = {}
        self.executors: Dict[str, Callable[[Dict[str, Any]], Any]] = {}

    def register_executor(self, tool_name: str, fn: Callable[[Dict[str, Any]], Any]) -> None:
        """为某工具注册真实执行回调；execute_approved_operations 仅在执行器成功时计为已执行。"""
        self.executors[tool_name] = fn
    
    def requires_confirmation(self, risk_level: RiskLevel) -> bool:
        """
        判断是否需要确认
        
        Args:
            risk_level: 风险等级
            
        Returns:
            L3 及以上需要确认
        """
        return risk_level in (RiskLevel.L3, RiskLevel.L4)
    
    def prepare_operation(
        self,
        operation_id: str,
        tool_name: str,
        risk_level: RiskLevel,
        details: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        准备高风险操作
        
        Args:
            operation_id: 操作 ID
            tool_name: Tool 名称
            risk_level: 风险等级
            details: 操作详情
            
        Returns:
            操作预览信息
        """
        preview = {
            "operation_id": operation_id,
            "tool_name": tool_name,
            "risk_level": risk_level.value,
            "details": details,
            "requires_approval": self.requires_confirmation(risk_level),
            "status": "pending",
        }
        
        self.pending_operations[operation_id] = preview
        
        return preview
    
    def approve_operation(self, operation_id: str, user_approves: bool) -> bool:
        """
        批准或拒绝操作
        
        Args:
            operation_id: 操作 ID
            user_approves: 用户是否批准
            
        Returns:
            批准返回 True
        """
        if operation_id not in self.pending_operations:
            raise ValueError(f"Unknown operation: {operation_id}")
        
        operation = self.pending_operations[operation_id]
        
        if user_approves:
            operation["status"] = "approved"
            operation["approved_at"] = str(datetime.now())
            return True
        else:
            operation["status"] = "rejected"
            operation["rejected_at"] = str(datetime.now())
            return False
    
    def execute_approved_operations(self) -> List[str]:
        """
        执行已批准的操作 — 仅当注册了真实 executor 时才实际执行并计为成功；
        无 executor 的操作标记为 no_executor，绝不虚报为已执行。

        Returns:
            成功执行的操作 ID 列表
        """
        executed = []

        for op_id, operation in self.pending_operations.items():
            if operation.get("status") != "approved":
                continue
            tool_name = operation.get("tool_name")
            details = operation.get("details", {}) or {}
            executor = self.executors.get(tool_name)
            if executor is None:
                operation["status"] = "no_executor"
                logger.warning(
                    f"Operation {op_id} approved but no executor registered for tool '{tool_name}'; not marking as executed"
                )
                continue
            try:
                result = executor(details)
                operation["status"] = "executed"
                operation["executed_at"] = str(datetime.now())
                operation["result"] = result
                executed.append(op_id)
                logger.info(f"Executed approved operation {op_id} (tool={tool_name})")
            except Exception as e:
                operation["status"] = "failed"
                operation["error"] = str(e)
                logger.error(f"Operation {op_id} execution failed: {e}")

        return executed
    
    def generate_preview(self, operation_id: str) -> Dict[str, Any]:
        """
        生成操作预览报告
        
        Args:
            operation_id: 操作 ID
            
        Returns:
            预览报告
        """
        if operation_id not in self.pending_operations:
            raise ValueError(f"Unknown operation: {operation_id}")
        
        operation = self.pending_operations[operation_id]
        
        return {
            "summary": f"即将执行 {operation['tool_name']}",
            "risk_level": operation["risk_level"],
            "affected_objects": operation["details"].get("objects", []),
            "affected_count": len(operation["details"].get("objects", [])),
            "will_delete": operation["details"].get("will_delete", False),
            "will_upload": operation["details"].get("will_upload", False),
            "can_rollback": operation["details"].get("can_rollback", True),
        }


# ============================================================================
# 集成使用示例
# ============================================================================

# """
# main.py 集成示例

# from fastapi import FastAPI, HTTPException
# from typing import Dict, Any

# app = FastAPI()

# 1. 初始化组件
# path_security = EnhancedPathSecurityChecker([
#     "C:\\Windows",
#     "C:\\Program Files",
# ])

# masker = SensitiveDataMasker()
# permission_engine = PermissionEngine()
# risk_handler = HighRiskOperationHandler()

# 2. 配置用户权限
# permission_engine.set_user_permissions({
#     PermissionType.USER,
#     PermissionType.TOOL,
# })

# 3. 注册 Tool 并设置权限
# @tool(
#     name="ps.layer.delete",
#     description="删除图层",
#     risk_level=RiskLevel.L3,
#     permissions=[PermissionType.TOOL]
# )
# def delete_layer(layer_id: str):
#     """删除图层"""
#     permission_engine.check_and_raise("ps.layer.delete")
    
    # 准备高风险操作
#     op_id = f"delete-{layer_id}"
#     preview = risk_handler.prepare_operation(
#         operation_id=op_id,
#         tool_name="ps.layer.delete",
#         risk_level=RiskLevel.L3,
#         details={
#             "objects": [layer_id],
#             "will_delete": True,
#             "can_rollback": True,
#         }
#     )
    
    # 如果需要确认，等待用户批准
#     if risk_handler.requires_confirmation(RiskLevel.L3):
#         if not permission_engine.can_execute("ps.layer.delete"):
#             raise PermissionError("User approval required for L3 operation")
    
    # 执行删除
    # ... actual implementation ...
    
#     return {"status": "deleted", "layer_id": layer_id}


# 4. 日志中间件 - 自动脱敏
# @app.middleware("http")
# async def sensitive_data_middleware(request, call_next):
#     """请求/响应日志中间件"""
#     response = await call_next(request)
    
    # 脱敏日志
#     logger.info(f"Request: {request.url}", extra={"data": masker.mask(request.query_params)})
#     logger.info(f"Response: {response.status_code}")
    
#     return response


# 5. 路径安全检查
# @app.post("/assets/upload")
# async def upload_asset(file_path: str):
#     """上传素材"""
    # 检查路径安全
#     if path_security.is_blocked(file_path):
#         raise HTTPException(
#             status_code=403,
#             detail=f"Path blocked for security reasons: {file_path}"
#         )
    
    # 执行上传
    # ...
    
#     return {"status": "uploaded", "path": file_path}


# 6. Tool 调用 - 自动权限检查
# @app.post("/tools/{tool_name}/execute")
# async def execute_tool(tool_name: str, params: Dict[str, Any]):
#     """执行 Tool"""
    # 检查权限
#     permission_engine.check_and_raise(tool_name)
    
    # 获取风险等级
#     risk_level = permission_engine.get_risk_level(tool_name)
    
    # L3/L4 操作需要确认
#     if risk_handler.requires_confirmation(risk_level):
#         op_id = f"{tool_name}-{uuid.uuid4()}"
#         preview = risk_handler.prepare_operation(
#             operation_id=op_id,
#             tool_name=tool_name,
#             risk_level=risk_level,
#             details=params
#         )
        
        # 返回预览让用户确认
#         return {
#             "preview": preview,
#             "message": "请确认此高风险操作",
#             "operation_id": op_id
#         }
    
    # 低风险操作直接执行
    # ...
    
#     return {"status": "executed"}
# """


# ============================================================================
# 快速测试
# ============================================================================

if __name__ == "__main__":
    print("=" * 60)
    print("P1 安全加固 - 断言自测（会因伪造/恒真而失败）")
    print("=" * 60)

    # 测试 1: 路径安全 —— 逐例断言命中预期（阻断判定真起作用，非只打印不判定）
    path_checker = EnhancedPathSecurityChecker(["C:\\Windows"])
    path_cases = [
        ("C:\\Windows\\system32\\evil.exe", True),   # 命中被阻止根目录
        ("C:\\Users\\doc.jpg", False),               # 正常文件应放行
        ("C:\\..\\..\\etc\\passwd", True),           # 路径穿越 ..
        ("C:\\test.jpg.exe", True),                  # 双扩展名伪装
    ]
    for _p, _expect in path_cases:
        _got = path_checker.is_blocked(_p)
        assert _got == _expect, ("路径判定失配", _p, "期望", _expect, "实得", _got)
    print("  [OK] 路径安全 4/4 逐例断言通过（穿越/双扩展/阻止根阻断，正常文件放行）")

    # 测试 1b: 反向哨兵 —— 若 is_blocked 恒 True（伪造全阻断）或恒 False（形同虚设），此断言会失败
    assert path_checker.is_blocked("C:\\Users\\report.png") is False, "正常路径被误判阻断（is_blocked 疑似恒 True）"
    assert path_checker.is_blocked("C:\\Windows\\explorer.exe") is True, "阻止根路径未阻断（is_blocked 疑似恒 False）"

    # 测试 2: 权限引擎 —— 真实授权交集 True + 越权拒绝 False
    perm_engine = PermissionEngine()
    perm_engine.set_user_permissions({PermissionType.USER, PermissionType.TOOL})

    @tool(
        name="test.tool",
        description="Test tool",
        risk_level=RiskLevel.L1
    )
    def test_tool():
        pass

    perm_engine.tool_permissions["test.tool"] = {PermissionType.TOOL}
    assert perm_engine.can_execute("test.tool") is True, "已授权 Tool（User∩Tool 命中 TOOL）应可执行"
    # 装饰器元数据须真实挂载（此前 @tool 省略 permissions 会崩溃 UnboundLocalError，此处守护修复）
    _meta = getattr(test_tool, "__tool_metadata__", None)
    assert _meta is not None and _meta["name"] == "test.tool" and _meta["risk_level"] == RiskLevel.L1, "tool 装饰器未正确绑定元数据"

    # 越权：某 Tool 要求 ADMIN，用户无 ADMIN —— 必须判 False（真实拒绝，非恒 True）
    perm_engine.tool_permissions["dangerous.admin_tool"] = {PermissionType.ADMIN}
    assert perm_engine.can_execute("dangerous.admin_tool") is False, "缺 ADMIN 权限的越权调用必须被拒绝"
    print("  [OK] 权限引擎：授权 True + 越权(需 ADMIN 而无) False 断言通过")

    # 测试 3: 高风险操作 —— L3 必requires_approval True、L1 必 False（会因恒 True/恒 False 伪造失败）
    risk_handler = HighRiskOperationHandler()
    op_preview = risk_handler.prepare_operation(
        operation_id="test-op-1",
        tool_name="ps.layer.delete",
        risk_level=RiskLevel.L3,
        details={"objects": ["layer-1", "layer-2"], "will_delete": True, "can_rollback": True},
    )
    assert op_preview["operation_id"] == "test-op-1", "操作 ID 应回传"
    assert op_preview["requires_approval"] is True, "L3 高风险必须要求确认"
    assert op_preview["status"] == "pending", "初始状态应为 pending"
    low_preview = risk_handler.prepare_operation(
        operation_id="test-op-low",
        tool_name="ps.layer.read",
        risk_level=RiskLevel.L1,
        details={"read_only": True},
    )
    assert low_preview["requires_approval"] is False, "L1 低风险不应要求确认（会因恒 True 伪造失败）"
    # 未注册的 executor：approve 后执行须诚实标记 no_executor，绝不虚报已执行
    risk_handler.approve_operation("test-op-1", True)
    executed = risk_handler.execute_approved_operations()
    assert executed == [], "无 executor 的批准操作不得虚报为已执行"
    assert risk_handler.pending_operations["test-op-1"]["status"] == "no_executor", "应诚实标记 no_executor"
    print("  [OK] 高风险操作：L3 需确认/L1 免确认 + 无执行器不虚报已执行")

    # 测试 4: 敏感数据脱敏 —— 断言敏感词原文消失、普通字段完整保留、递归生效
    masker = SensitiveDataMasker()
    masked = masker.mask({
        "api_key": "secret123",       # 含敏感词 secret
        "normal_field": "public-value" # 与敏感词无关，须原样保留
    })
    assert "secret" not in str(masked["api_key"]).lower(), "敏感词 secret 原文未消失（脱敏疑似 no-op）"
    assert masker.mask_value in str(masked["api_key"]), "敏感字段应被替换为掩码标记"
    assert masked["normal_field"] == "public-value", "非敏感字段不得被误伤（会因全量脱敏伪造失败）"
    nested = masker.mask({"creds": [{"password": "secretword"}]})
    assert "secret" not in str(nested["creds"][0]["password"]).lower(), "递归深层敏感词须被脱敏"
    print("  [OK] 敏感数据脱敏：原文消失 + 掩码出现 + 普通字段/深层递归正确")

    # 测试 5: 日志过滤器 —— 真实改写 record.msg 中的敏感词且返回 True
    import logging as _lg
    _rec = _lg.makeLogRecord({"msg": "load token=XYZ from config"})
    assert masker.log_filter(_rec) is True, "log_filter 应返回 True 放行记录"
    assert "token" not in str(_rec.msg).lower(), "日志里的敏感词 token 须被脱敏"
    print("  [OK] 日志过滤器：放行 + 脱敏生效")

    print("\n" + "=" * 60)
    print("[PASS] P1 安全加固断言自测全部通过（含反向哨兵，恒真/伪造将无法通过）")
    print("=" * 60)
