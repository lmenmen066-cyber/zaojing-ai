"""
License Service 单元测试

测试覆盖:
- machine_id 生成不依赖 uname().username
- getuser/uuid/node 失败时的 fallback 机制
- LicenseManager 正常与降级初始化
- 单例模式
- status/validate/activate 方法

运行方式:
    pytest tests/unit/test_license_service.py -v
"""

import pytest
import platform
import getpass
import uuid
import os
from unittest.mock import patch, MagicMock

# 导入被测试模块
import sys
sys.path.insert(0, '../outputs')

from license_service import (
    _machine_id,
    _safe_getuser,
    _safe_node,
    _safe_mac,
    LicenseManager,
    LicenseState
)


# ============================================================================
# _safe_getuser 测试
# ============================================================================

def test_safe_getuser_success(monkeypatch):
    """测试 getuser 成功情况"""
    monkeypatch.setattr(getpass, "getuser", lambda: "testuser")
    
    result = _safe_getuser()
    
    assert result == "testuser"


def test_safe_getuser_fallback_to_env(monkeypatch):
    """测试 getuser 失败时回退到环境变量"""
    def failing_getuser():
        raise OSError("getuser failed")
    
    monkeypatch.setattr(getpass, "getuser", failing_getuser)
    monkeypatch.setenv("USERNAME", "envuser")
    
    result = _safe_getuser()
    
    assert result == "envuser"


def test_safe_getuser_all_fail_returns_default(monkeypatch):
    """测试所有源都失败时返回默认值"""
    def failing_getuser():
        raise OSError("getuser failed")
    
    monkeypatch.setattr(getpass, "getuser", failing_getuser)
    monkeypatch.delenv("USERNAME", raising=False)
    monkeypatch.delenv("USER", raising=False)
    monkeypatch.delenv("LOGNAME", raising=False)
    
    result = _safe_getuser()
    
    assert result == "user"


# ============================================================================
# _safe_node 测试
# ============================================================================

def test_safe_node_success(monkeypatch):
    """测试 node 成功情况"""
    monkeypatch.setattr(platform, "node", lambda: "test-hostname")
    
    result = _safe_node()
    
    assert result == "test-hostname"


def test_safe_node_failure_returns_unknown(monkeypatch):
    """测试 node 失败时返回 unknown-node"""
    monkeypatch.setattr(platform, "node", lambda: (_ for _ in ()).throw(Exception("Failed")))
    
    result = _safe_node()
    
    assert result == "unknown-node"


# ============================================================================
# _safe_mac 测试
# ============================================================================

def test_safe_mac_success(monkeypatch):
    """测试 mac 成功情况"""
    monkeypatch.setattr(uuid, "getnode", lambda: 123456789012)
    
    result = _safe_mac()
    
    assert result == "123456789012"


def test_safe_mac_failure_returns_zero(monkeypatch):
    """测试 mac 失败时返回 0"""
    monkeypatch.setattr(uuid, "getnode", lambda: (_ for _ in ()).throw(Exception("Failed")))
    
    result = _safe_mac()
    
    assert result == "0"


# ============================================================================
# _machine_id 测试
# ============================================================================

def test_machine_id_no_uname_username(monkeypatch):
    """
    【核心测试】验证 machine_id 不依赖 uname().username
    
    旧代码错误:
        user = platform.uname().username  # AttributeError!
    
    修复后应该:
        - 使用 getpass.getuser() + 环境变量 fallback
        - 任何子项失败都不能抛异常
    """
    # 模拟 uname 返回没有 username 属性的对象（真实情况）
    class FakeUnameResult:
        system = "Windows"
        node = "localhost"
        release = "10"
        version = "1"
        machine = "AMD64"
        # 故意不包含 username
    
    monkeypatch.setattr(platform, "uname", lambda: FakeUnameResult())
    
    # 确保其他函数正常
    monkeypatch.setattr(getpass, "getuser", lambda: "testuser")
    
    from license_service import _machine_id
    result = _machine_id()
    
    # 断言
    assert isinstance(result, str), "结果必须是字符串"
    assert len(result) == 64, "SHA-256 输出应为 64 字符"
    assert result.isalnum(), "结果应只包含字母和数字"


def test_machine_id_getuser_fail_with_fallback(monkeypatch):
    """测试 getuser 失败时使用环境变量 fallback"""
    def failing_getuser():
        raise OSError("getuser failed")
    
    monkeypatch.setattr(getpass, "getuser", failing_getuser)
    monkeypatch.setenv("USERNAME", "fallbackuser")
    
    # 固定其他变量以确保一致性
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    monkeypatch.setattr(platform, "machine", lambda: "AMD64")
    monkeypatch.setattr(platform, "node", lambda: "test-host")
    monkeypatch.setattr(uuid, "getnode", lambda: "123456789012")
    
    from license_service import _machine_id
    result = _machine_id()
    
    assert isinstance(result, str)
    assert len(result) == 64


def test_machine_id_uuid_fail(monkeypatch):
    """测试 uuid.getnode 失败时的容错"""
    def failing_getnode():
        raise OSError("uuid failed")
    
    monkeypatch.setattr(uuid, "getnode", failing_getnode)
    
    # 固定其他变量
    monkeypatch.setattr(getpass, "getuser", lambda: "testuser")
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    monkeypatch.setattr(platform, "machine", lambda: "AMD64")
    monkeypatch.setattr(platform, "node", lambda: "test-host")
    
    from license_service import _machine_id
    result = _machine_id()
    
    assert isinstance(result, str)
    assert len(result) == 64


def test_machine_id_consistency(monkeypatch):
    """测试同一环境下 machine_id 的一致性"""
    # 固定所有变量
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    monkeypatch.setattr(platform, "machine", lambda: "AMD64")
    monkeypatch.setattr(platform, "node", lambda: "test-host")
    monkeypatch.setattr(uuid, "getnode", lambda: "123456789012")
    monkeypatch.setattr(getpass, "getuser", lambda: "testuser")
    
    from license_service import _machine_id
    id1 = _machine_id()
    id2 = _machine_id()
    
    assert id1 == id2, "同一环境下的 machine_id 必须一致"


def test_machine_id_different_users_produce_different_ids(monkeypatch):
    """测试不同用户产生不同的 machine_id"""
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    monkeypatch.setattr(platform, "machine", lambda: "AMD64")
    monkeypatch.setattr(platform, "node", lambda: "test-host")
    monkeypatch.setattr(uuid, "getnode", lambda: "123456789012")
    
    id1 = None
    id2 = None
    
    with monkeypatch.context() as m:
        m.setattr(getpass, "getuser", lambda: "user1")
        from license_service import _machine_id as mid1
        id1 = mid1()
    
    with monkeypatch.context() as m:
        m.setattr(getpass, "getuser", lambda: "user2")
        from license_service import _machine_id as mid2
        id2 = mid2()
    
    assert id1 != id2, "不同用户应产生不同的 machine_id"


# ============================================================================
# LicenseManager 测试
# ============================================================================

def test_license_manager_init_success(monkeypatch):
    """测试 LicenseManager 正常初始化"""
    monkeypatch.setattr(
        "license_service._machine_id",
        lambda: "test-machine-id-1234567890abcdef1234567890abcdef1234567890abcdef12345678"
    )
    
    manager = LicenseManager()
    
    assert manager.degraded == False, "正常初始化不应降级"
    assert manager.machine_id == "test-machine-id-1234567890abcdef1234567890abcdef1234567890abcdef12345678"
    assert manager.degraded_reason is None


def test_license_manager_init_failure_degrades_gracefully(monkeypatch):
    """
    【核心测试】测试 LicenseManager 初始化失败时降级而不是崩溃
    
    这是 P0-1 的关键修复点:
    - 旧行为：_machine_id 异常导致整个 /api/v1/license 返回 500
    - 新行为：degraded=True，应用继续运行但限制功能
    """
    monkeypatch.setattr(
        "license_service._machine_id",
        lambda: (_ for _ in ()).throw(Exception("Machine ID generation failed"))
    )
    
    manager = LicenseManager()
    
    assert manager.degraded == True, "失败时应进入降级模式"
    assert manager.machine_id == "unknown-machine", "机器码应为未知"
    assert manager.degraded_reason == "machine_id_unavailable"


def test_license_manager_singleton():
    """测试单例模式"""
    LicenseManager.reset_instance()
    
    mgr1 = LicenseManager.get_instance()
    mgr2 = LicenseManager.get_instance()
    
    assert mgr1 is mgr2, "应返回同一实例"
    
    LicenseManager.reset_instance()


def test_license_manager_status():
    """测试 status 方法"""
    LicenseManager.reset_instance()
    manager = LicenseManager.get_instance()
    
    status = manager.status()
    
    assert "status" in status
    assert "machine_id" in status
    assert "degraded" in status
    
    if manager.degraded:
        assert status["status"] == "degraded"
        assert "degraded_reason" in status


def test_license_manager_validate_degraded():
    """测试降级模式下 validate 返回 False"""
    LicenseManager.reset_instance()
    manager = LicenseManager.get_instance()
    
    # 强制设置为 degraded
    manager.degraded = True
    
    assert manager.validate() == False, "降级模式下 validate 应返回 False"


def test_license_manager_validate_active():
    """测试激活状态下 validate 返回 True"""
    LicenseManager.reset_instance()
    manager = LicenseManager.get_instance()
    
    manager.license_state = LicenseState.ACTIVE
    manager.degraded = False
    
    assert manager.validate() == True


def test_license_manager_activate_in_degraded_mode():
    """测试降级模式下无法激活"""
    LicenseManager.reset_instance()
    manager = LicenseManager.get_instance()
    
    manager.degraded = True
    
    result = manager.activate("test-key")
    
    assert result == False, "降级模式下 activate 应返回 False"


# ============================================================================
# 集成测试场景
# ============================================================================

def test_full_workflow_normal_scenario(monkeypatch):
    """
    完整工作流测试 - 正常场景
    """
    # 模拟正常环境
    monkeypatch.setattr(getpass, "getuser", lambda: "admin")
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    monkeypatch.setattr(platform, "machine", lambda: "AMD64")
    monkeypatch.setattr(platform, "node", lambda: "workstation-01")
    monkeypatch.setattr(uuid, "getnode", lambda: "aabbccdd112233")
    
    LicenseManager.reset_instance()
    manager = LicenseManager.get_instance()
    
    # 验证状态
    assert manager.degraded == False
    assert len(manager.machine_id) == 64
    
    # 验证状态信息
    status = manager.status()
    assert status["status"] == "inactive"
    assert status["degraded"] == False
    
    # 尝试激活（mock 实现）
    with patch.object(manager, '_validate_license_key', return_value=True):
        result = manager.activate("TEST-LICENSE-KEY")
        # 注意：实际 activate 需要完整实现，这里仅测试流程


def test_full_workflow_degraded_scenario(monkeypatch):
    """
    完整工作流测试 - 降级场景（P0-1 关键场景）
    """
    # 模拟所有机器信息采集都失败
    monkeypatch.setattr(getpass, "getuser", lambda: (_ for _ in ()).throw(OSError()))
    monkeypatch.setattr(platform, "node", lambda: (_ for _ in ()).throw(Exception()))
    monkeypatch.setattr(uuid, "getnode", lambda: (_ for _ in ()).throw(OSError()))
    monkeypatch.setenv("USERNAME", "")
    monkeypatch.setenv("USER", "")
    monkeypatch.setenv("LOGNAME", "")
    
    LicenseManager.reset_instance()
    manager = LicenseManager.get_instance()
    
    # 验证降级
    assert manager.degraded == True
    assert manager.machine_id == "unknown-machine"
    assert manager.degraded_reason == "machine_id_unavailable"
    
    # 验证应用仍可运行（不会崩溃）
    status = manager.status()
    assert status["status"] == "degraded"
    
    # 验证 validate 失败
    assert manager.validate() == False


# ============================================================================
# 运行测试
# ============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
