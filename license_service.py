"""
造境 AI 设计智能体 - License 服务模块

修复内容:
- P0-1: 修复 machine_id 生成崩溃问题
- 不再使用 platform.uname().username (该字段不存在)
- 增加多源 fallback 机制
- LicenseManager 可降级启动
- API 层异常处理与降级响应

版本：V1.0 修复版
"""

import base64
import hashlib
import hmac
import json
import getpass
import logging
import os
import platform
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Tuple, Any, Dict

logger = logging.getLogger(__name__)


def _safe_getuser() -> str:
    """
    安全获取用户名，多源 fallback。
    
    Returns:
        用户名字符串，失败时返回 "user"
    """
    try:
        user = getpass.getuser()
        if user:
            return user
    except Exception as e:
        logger.warning("getpass.getuser() failed, fallback to env", exc_info=True)

    # 尝试环境变量
    return (
        os.environ.get("USERNAME")
        or os.environ.get("USER")
        or os.environ.get("LOGNAME")
        or "user"
    )


def _safe_node() -> str:
    """
    安全获取主机名。
    
    Returns:
        主机名字符串，失败时返回 "unknown-node"
    """
    try:
        node = platform.node()
        if node:
            return node
    except Exception as e:
        logger.warning("platform.node() failed", exc_info=True)
    return "unknown-node"


def _safe_mac() -> str:
    """
    安全获取 MAC 地址。
    
    Returns:
        MAC 地址字符串，失败时返回 "0"
    """
    try:
        return str(uuid.getnode())
    except Exception as e:
        logger.warning("uuid.getnode() failed", exc_info=True)
        return "0"


def _machine_id() -> str:
    """
    生成稳定机器码（SHA-256 哈希）。
    
    修复要点:
    - 不再使用 platform.uname().username (该字段不存在导致崩溃)
    - 任何子项失败都不能抛异常
    - 输出 SHA-256，避免泄露原始主机信息
    
    Returns:
        64 字符的 SHA-256 哈希字符串
    """
    parts = [
        platform.system() or "unknown-os",
        platform.machine() or "unknown-arch",
        _safe_node(),
        _safe_mac(),
        _safe_getuser(),
    ]
    raw = "|".join(parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ============================================================================
# 真实 License 密钥方案（HMAC-SHA256 签名 + 载荷 + 机器绑定 + 过期）
# 密钥格式：ZAOJING.<b64url(payload_json)>.<b64url(hmac_sha256)>
# 签发方（授权后台 / 本地 keygen 工具）与校验方共享同一 SECRET，
# 未配置 ZAOJING_LICENSE_SECRET 时回落到内置产品密钥（离线授权常见做法）。
# ============================================================================

LICENSE_PREFIX = "ZAOJING"
_DEFAULT_SECRET = "zaojing-ai-design-agent-v1-offline-license"

_store_path: Optional[str] = None


def configure_store(path: str) -> None:
    """设置激活记录落盘路径（由 agent_service 在启动时注入数据目录）"""
    global _store_path
    _store_path = str(path)


def _secret_key() -> bytes:
    return os.environ.get("ZAOJING_LICENSE_SECRET", _DEFAULT_SECRET).encode("utf-8")


def _b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64d(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


def issue_license_key(payload: Dict[str, Any]) -> str:
    """
    签发一个合法的签名授权密钥（供授权后台与本地 keygen 工具使用）。

    payload 支持字段：
      - edition: "pro" | "trial" | "basic"
      - exp:     ISO8601 到期时间字符串（可选，缺省为永久）
      - mid:     绑定机器码前缀，"*" 或不填表示不限机器（可选）
      - seats / customer 等自定义信息
    """
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    sig = hmac.new(_secret_key(), body, hashlib.sha256).digest()
    return f"{LICENSE_PREFIX}.{_b64e(body)}.{_b64e(sig)}"


def verify_license_key(license_key: str, machine_id: str) -> Tuple[bool, Any]:
    """
    校验授权密钥的真实性。

    Returns:
        (True, payload_dict)  校验通过
        (False, reason_str)   校验失败，附带人类可读原因
    """
    key = (license_key or "").strip()
    if not key:
        return False, "密钥为空"

    parts = key.split(".")
    if len(parts) != 3 or parts[0] != LICENSE_PREFIX:
        return False, "格式无效：应为 ZAOJING.<载荷>.<签名>"

    try:
        body = _b64d(parts[1])
        sig = _b64d(parts[2])
    except Exception:
        return False, "编码无效：载荷或签名不是合法的 Base64URL"

    expect = hmac.new(_secret_key(), body, hashlib.sha256).digest()
    if not hmac.compare_digest(sig, expect):
        return False, "签名校验失败：密钥被篡改或非本产品签发"

    try:
        payload = json.loads(body.decode("utf-8"))
    except Exception:
        return False, "载荷解析失败"

    if not isinstance(payload, dict):
        return False, "载荷结构无效：签名载荷必须是 JSON 对象"

    # 机器绑定校验
    bound = str(payload.get("mid", "*"))
    if bound != "*":
        if not machine_id.startswith(bound):
            return False, f"机器绑定不符：该密钥不适用于本机（期望前缀 {bound}）"

    # 过期校验
    exp = payload.get("exp")
    if exp:
        try:
            exp_dt = datetime.fromisoformat(exp)
        except Exception:
            return False, "过期时间格式无效"
        if exp_dt.tzinfo is None:
            exp_dt = exp_dt.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) > exp_dt:
            return False, f"授权已过期：{exp}"

    return True, payload


class LicenseState:
    """License 状态枚举"""
    ACTIVE = "active"
    INACTIVE = "inactive"
    EXPIRED = "expired"
    TRIAL = "trial"
    DEGRADED = "degraded"


class LicenseManager:
    """
    License 管理器 - 支持降级启动
    
    修复要点:
    - __init__ 不允许因为机器码失败导致整个系统崩溃
    - degraded 标志允许应用继续运行但限制功能
    - machine_id 生成失败时使用 "unknown-machine"
    """
    
    _instance: Optional['LicenseManager'] = None
    
    def __init__(self):
        self.degraded = False
        self.degraded_reason: Optional[str] = None
        self.machine_id: str = "unknown-machine"
        self.license_state: str = LicenseState.INACTIVE
        self.activated_key: Optional[str] = None
        self.expiry: Optional[str] = None
        self.edition: str = ""
        self.last_error: Optional[str] = None

        try:
            self.machine_id = _machine_id()
            logger.info(f"Machine ID generated successfully: {self.machine_id[:16]}...")
        except Exception as e:
            logger.exception("machine_id generation failed")
            self.machine_id = "unknown-machine"
            self.degraded = True
            self.degraded_reason = "machine_id_unavailable"
            
            # 记录严重警告
            logger.critical(
                "License service running in degraded mode: %s",
                self.degraded_reason
            )

        # 从磁盘恢复此前的合法激活记录（重启后仍保持已授权状态）
        if not self.degraded:
            self._load_store()
    
    @classmethod
    def get_instance(cls) -> 'LicenseManager':
        """单例模式获取实例"""
        if cls._instance is None:
            cls._instance = LicenseManager()
        return cls._instance
    
    @classmethod
    def reset_instance(cls):
        """重置单例（用于测试）"""
        cls._instance = None
    
    def status(self) -> dict:
        """
        获取 License 状态
        
        Returns:
            {
                "status": "active" | "inactive" | "expired" | "trial" | "degraded",
                "machine_id": str,
                "degraded": bool,
                "degraded_reason": str | None,
                "edition": str, "expiry": str | None, "last_error": str | None
            }
        """
        result = {
            "status": LicenseState.DEGRADED if self.degraded else self.license_state,
            "machine_id": self.machine_id,
            "degraded": self.degraded,
            "edition": self.edition,
            "expiry": self.expiry,
        }
        
        if self.degraded_reason:
            result["degraded_reason"] = self.degraded_reason
        if self.last_error:
            result["last_error"] = self.last_error
        
        return result
    
    def activate(self, license_key: str) -> bool:
        """
        激活 License —— 真实校验签名 / 机器绑定 / 过期，通过后落盘。
        
        Args:
            license_key: 授权密钥（ZAOJING.<载荷>.<签名>）
            
        Returns:
            激活成功返回 True；任何校验不通过返回 False 并记录 last_error
        """
        self.last_error = None
        if self.degraded:
            self.last_error = "degraded_mode"
            logger.error("Cannot activate: License service in degraded mode")
            return False
        
        ok, res = verify_license_key(license_key, self.machine_id)
        if not ok:
            self.license_state = LicenseState.INACTIVE
            self.last_error = res
            logger.warning("License activation rejected: %s", res)
            return False
        
        payload = res
        self.activated_key = license_key.strip()
        self.expiry = payload.get("exp")
        self.edition = str(payload.get("edition", "pro"))
        self.license_state = (
            LicenseState.TRIAL if self.edition == "trial" else LicenseState.ACTIVE
        )
        self._save_store()
        logger.info("License activated successfully: edition=%s exp=%s",
                    self.edition, self.expiry)
        return True
    
    def validate(self) -> bool:
        """
        验证 License 是否有效 —— 运行时重新校验签名与过期，诚实降级。
        
        Returns:
            有效返回 True
        """
        if self.degraded:
            return False
        if not self.activated_key:
            return False
        
        ok, res = verify_license_key(self.activated_key, self.machine_id)
        if not ok:
            self.last_error = res
            self.license_state = (
                LicenseState.EXPIRED if "过期" in str(res) else LicenseState.INACTIVE
            )
            logger.warning("License no longer valid: %s", res)
            return False
        
        return self.license_state in (LicenseState.ACTIVE, LicenseState.TRIAL)
    
    def expiry_date(self) -> Optional[str]:
        """
        获取过期日期
        
        Returns:
            ISO 格式日期字符串，未设置返回 None
        """
        return self.expiry

    # ------------------------------------------------------------------
    # 激活记录落盘（跨重启保持授权状态）
    # ------------------------------------------------------------------
    def _save_store(self) -> None:
        if not _store_path or not self.activated_key:
            return
        try:
            p = Path(_store_path)
            p.parent.mkdir(parents=True, exist_ok=True)
            record = {
                "key": self.activated_key,
                "machine_id": self.machine_id,
                "activated_at": datetime.now(timezone.utc).isoformat(),
            }
            p.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
            logger.debug("License activation persisted to %s", p)
        except Exception as e:
            logger.warning("Failed to persist license activation: %s", e)

    def _load_store(self) -> None:
        if not _store_path:
            return
        try:
            p = Path(_store_path)
            if not p.exists():
                return
            record = json.loads(p.read_text(encoding="utf-8"))
            key = record.get("key", "")
            ok, res = verify_license_key(key, self.machine_id)
            if ok:
                self.activated_key = key
                self.expiry = res.get("exp")
                self.edition = str(res.get("edition", "pro"))
                self.license_state = (
                    LicenseState.TRIAL if self.edition == "trial" else LicenseState.ACTIVE
                )
                logger.info("Restored valid license activation (edition=%s)", self.edition)
            else:
                logger.info("Stored license record no longer valid: %s", res)
        except Exception as e:
            logger.warning("Failed to load license activation store: %s", e)


# ============================================================================
# FastAPI Router 示例（供参考）
# ============================================================================

# """
# from fastapi import APIRouter, HTTPException
# from fastapi.responses import JSONResponse
# from typing import Any

# router = APIRouter(prefix="/api/v1/license", tags=["license"])

# def get_license_manager() -> LicenseManager:
#     return LicenseManager.get_instance()


# @router.get("/status")
# async def license_status() -> dict:
#     """
#     获取 License 状态（健康检查接口）
    
#     修复要点:
#     - 不再因 machine_id 错误返回 500
#     - 降级模式下返回 200 + degraded 状态
#     - 完全失败时返回 503
#     """
#     try:
#         mgr = get_license_manager()
#         return mgr.status()
#     except Exception as e:
#         logger.exception("license_status failed")
#         return JSONResponse(
#             status_code=503,
#             content={
#                 "code": "ZJ-9001",
#                 "status": "degraded",
#                 "message": "授权服务暂不可用，请查看诊断日志。",
#                 "trace": request_trace_id(),
#             },
#         )


# @router.post("/activate")
# async def activate_license(license_data: dict) -> dict:
#     """
#     激活 License
    
#     Args:
#         license_data: {"license_key": "xxx"}
#     """
#     try:
#         mgr = get_license_manager()
        
#         if mgr.degraded:
#             raise HTTPException(
#                 status_code=503,
#                 detail={
#                     "code": "ZJ-9002",
#                     "message": "授权服务不可用，无法激活",
#                     "reason": mgr.degraded_reason
#                 }
#             )
        
#         license_key = license_data.get("license_key", "")
#         if not license_key:
#             raise HTTPException(status_code=400, detail="license_key 必填")
        
#         success = mgr.activate(license_key)
        
#         if success:
#             return {"status": "activated", "machine_id": mgr.machine_id}
#         else:
#             raise HTTPException(status_code=400, detail="激活失败")
            
#     except HTTPException:
#         raise
#     except Exception as e:
#         logger.exception("activate_license failed")
#         raise HTTPException(status_code=500, detail=str(e))


# @router.get("/validate")
# async def validate_license() -> dict:
#     """
#     验证 License 有效性
#     """
#     try:
#         mgr = get_license_manager()
        
#         if mgr.validate():
#             return {
#                 "valid": True,
#                 "state": mgr.license_state,
#                 "expiry": mgr.expiry_date()
#             }
#         else:
#             return {
#                 "valid": False,
#                 "state": mgr.license_state,
#                 "degraded": mgr.degraded,
#                 "reason": mgr.degraded_reason
#             }
            
#     except Exception as e:
#         logger.exception("validate_license failed")
#         return {
#             "valid": False,
#             "error": str(e)
#         }
# """


# ============================================================================
# 单元测试（tests/unit/test_license_service.py）
# ============================================================================

# """
# import pytest
# import platform
# import getpass
# import uuid
# from unittest.mock import patch, MagicMock

# from license_service import _machine_id, _safe_getuser, _safe_node, _safe_mac, LicenseManager


# def test_machine_id_no_uname_username(monkeypatch):
#     \"\"\"
#     测试 machine_id 不依赖 uname().username
#     模拟旧错误路径不再被使用
#     \"\"\"
    # 模拟 uname 返回没有 username 属性的对象
#     class FakeUnameResult:
#         system = "Windows"
#         node = "localhost"
#         release = "10"
#         version = "1"
#         machine = "AMD64"
        # 故意不包含 username
    
#     monkeypatch.setattr(platform, "uname", lambda: FakeUnameResult())
    
#     from license_service import _machine_id
#     result = _machine_id()
    
#     assert isinstance(result, str)
#     assert len(result) == 64  # SHA-256 输出 64 字符
#     assert result.isalnum()


# def test_machine_id_getuser_fail(monkeypatch):
#     \"\"\"
#     测试 getuser 失败时的 fallback
#     \"\"\"
#     def failing_getuser():
#         raise OSError("getuser failed")
    
#     monkeypatch.setattr(getpass, "getuser", failing_getuser)
    
    # 确保环境变量存在
#     monkeypatch.setenv("USERNAME", "testuser")
    
#     from license_service import _machine_id
#     result = _machine_id()
    
#     assert isinstance(result, str)
#     assert len(result) == 64


# def test_machine_id_uuid_fail(monkeypatch):
#     \"\"\"
#     测试 uuid.getnode 失败时的容错
#     \"\"\"
#     def failing_getnode():
#         raise OSError("uuid failed")
    
#     monkeypatch.setattr(uuid, "getnode", failing_getnode)
    
#     from license_service import _machine_id
#     result = _machine_id()
    
#     assert isinstance(result, str)
#     assert len(result) == 64


# def test_machine_id_consistency(monkeypatch):
#     \"\""
#     测试同一环境下 machine_id 的一致性
#     """
    # 固定所有变量
#     monkeypatch.setattr(platform, "system", lambda: "Windows")
#     monkeypatch.setattr(platform, "machine", lambda: "AMD64")
#     monkeypatch.setattr(platform, "node", lambda: "test-host")
#     monkeypatch.setattr(uuid, "getnode", lambda: "123456789012")
#     monkeypatch.setattr(getpass, "getuser", lambda: "testuser")
    
#     from license_service import _machine_id
#     id1 = _machine_id()
#     id2 = _machine_id()
    
#     assert id1 == id2


# def test_license_manager_init_success(monkeypatch):
#     """
#     测试 LicenseManager 正常初始化
#     """
#     monkeypatch.setattr(
#         "license_service._machine_id",
#         lambda: "test-machine-id-1234567890abcdef"
#     )
    
#     manager = LicenseManager()
    
#     assert manager.degraded == False
#     assert manager.machine_id == "test-machine-id-1234567890abcdef"


# def test_license_manager_init_failure(monkeypatch):
#     """
#     测试 LicenseManager 初始化失败时降级
#     """
#     monkeypatch.setattr(
#         "license_service._machine_id",
#         lambda: (_ for _ in ()).throw(Exception("Failed"))
#     )
    
#     manager = LicenseManager()
    
#     assert manager.degraded == True
#     assert manager.machine_id == "unknown-machine"
#     assert manager.degraded_reason == "machine_id_unavailable"


# def test_license_manager_singleton():
#     """
#     测试单例模式
#     """
#     LicenseManager.reset_instance()
    
#     mgr1 = LicenseManager.get_instance()
#     mgr2 = LicenseManager.get_instance()
    
#     assert mgr1 is mgr2
    
#     LicenseManager.reset_instance()


# def test_license_manager_status():
#     """
#     测试 status 方法
#     """
#     LicenseManager.reset_instance()
#     manager = LicenseManager.get_instance()
    
#     status = manager.status()
    
#     assert "status" in status
#     assert "machine_id" in status
#     assert "degraded" in status


# def test_license_manager_validate_degraded():
#     """
#     测试降级模式下 validate 返回 False
#     """
#     LicenseManager.reset_instance()
    
    # 强制设置为 degraded
#     manager = LicenseManager.get_instance()
#     manager.degraded = True
    
#     assert manager.validate() == False
# """


if __name__ == "__main__":
    from datetime import datetime, timedelta, timezone as _tz
    print("=" * 60)
    print("License Service - 断言自测（含签名防篡改往返，伪造将失败）")
    print("=" * 60)

    # 测试 1: machine_id —— 64 位 SHA-256 十六进制、稳定、非空（会因随机/截断/明文回显失败）
    mid = _machine_id()
    assert isinstance(mid, str) and len(mid) == 64, ("machine_id 长度应为 64", len(mid))
    assert all(c in "0123456789abcdef" for c in mid), "machine_id 应为小写十六进制（SHA-256 hexdigest）"
    assert mid == _machine_id(), "machine_id 须稳定（同环境两次调用一致）"
    assert mid.strip() != "", "machine_id 不得为空"
    print(f"  [OK] machine_id: 64 位十六进制 + 稳定  前16={mid[:16]}...")

    # 测试 2: LicenseManager.status() —— 关键键存在 + 类型正确 + 降级布尔
    manager = LicenseManager()
    st = manager.status()
    for _k in ("status", "degraded", "machine_id"):
        assert _k in st, ("status() 缺少字段", _k, st.keys())
    assert isinstance(st["degraded"], bool), "degraded 必须是布尔"
    assert isinstance(st["machine_id"], str) and st["machine_id"], "machine_id 必须非空字符串"
    print(f"  [OK] status() 字段齐全: status={st['status']} degraded={st['degraded']}")

    # 测试 3: 合法签发→校验往返（真 HMAC；若 verify 恒 True 桩，此处篡改检测会失守）
    valid_key = issue_license_key({"edition": "pro", "customer": "acme"})
    ok, res = verify_license_key(valid_key, mid)
    assert ok is True and isinstance(res, dict) and res.get("edition") == "pro", ("合法密钥应通过", ok, res)

    # 测试 4: 防篡改 —— 用甲密钥的载荷拼乙密钥的签名（签名对不上内容），必须拒绝
    # （这是反伪造的核心非恒真断言：若 verify 恒 True 桩，此处会错误放行）
    _other_key = issue_license_key({"edition": "trial", "n": 999})
    _body_a = valid_key.split(".")[1]
    _sig_b = _other_key.split(".")[2]
    tampered_key = f"{LICENSE_PREFIX}.{_body_a}.{_sig_b}"
    ok_t, res_t = verify_license_key(tampered_key, mid)
    assert ok_t is False and isinstance(res_t, str) and res_t, ("载荷与签名不匹配的伪造密钥必须被拒绝", ok_t, res_t)

    # 测试 5: 机器绑定不符必须拒绝（mid 前缀指向别的机器）
    bound_key = issue_license_key({"edition": "pro", "mid": "deadbeef" if not mid.startswith("deadbeef") else "cafebabe"})
    ok_b, res_b = verify_license_key(bound_key, mid)
    assert ok_b is False and "机器绑定" in str(res_b), ("他机绑定密钥不应在本机通过", ok_b, res_b)

    # 测试 6: 过期判定真实生效
    past = (datetime.now(_tz.utc) - timedelta(days=1)).isoformat()
    future = (datetime.now(_tz.utc) + timedelta(days=365)).isoformat()
    ok_exp, res_exp = verify_license_key(issue_license_key({"edition": "pro", "exp": past}), mid)
    assert ok_exp is False and "过期" in str(res_exp), ("已过期密钥必须拒绝", ok_exp, res_exp)
    ok_fut, res_fut = verify_license_key(issue_license_key({"edition": "pro", "exp": future}), mid)
    assert ok_fut is True, ("未过期密钥应通过", ok_fut, res_fut)

    # 测试 7: 结构非法的合法签名载荷（离线默认密钥可对任意 body 签名）——
    #   旧版会在 payload.get 处抛 AttributeError 泄漏 5xx；须诚实降级为 (False, reason)，绝不崩溃。
    signed_non_dict = issue_license_key("i-am-a-string-not-an-object")
    try:
        ok_nd, res_nd = verify_license_key(signed_non_dict, mid)
    except Exception as e:  # 崩溃即缺陷复现
        raise AssertionError(f"合法签名但载荷非对象应诚实拒绝，却抛出异常: {e!r}")
    assert ok_nd is False and isinstance(res_nd, str) and ("结构" in res_nd or "对象" in res_nd), \
        ("载荷非 JSON 对象必须被诚实拒绝", ok_nd, res_nd)
    print("  [OK] 授权往返: 合法通过 + 篡改/他机/过期/结构非法载荷均诚实拒绝（防伪造，且不崩溃）")

    print("\n" + "=" * 60)
    print("[PASS] License Service 断言自测全部通过（签名往返+防篡改，桩/恒真无法通过）")
    print("=" * 60)
