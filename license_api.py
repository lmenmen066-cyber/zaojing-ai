"""
License API - FastAPI Router

修复内容:
- P0-1: /api/v1/license 不再因 machine_id 错误返回 500
- 降级模式下返回 200 + degraded 状态
- 完全失败时返回 503 但带友好提示

文件位置: packages/agent-service/src/zaojing/api/v1/license.py
"""

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
import logging
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)


# ============================================================================
# 依赖注入（实际项目中应从服务层导入）
# ============================================================================

def get_license_manager():
    """
    获取 LicenseManager 单例实例
    
    注意：实际项目中应该从服务容器获取
    这里为了演示，直接从 outputs 导入
    """
    import sys
    sys.path.insert(0, '../outputs')
    from license_service import LicenseManager
    return LicenseManager.get_instance()


def request_trace_id() -> str:
    """生成请求追踪 ID"""
    import uuid
    return str(uuid.uuid4())[:8]


# ============================================================================
# Router 定义
# ============================================================================

router = APIRouter(prefix="/api/v1/license", tags=["license"])


# ============================================================================
# API 端点
# ============================================================================

@router.get("/status")
async def license_status() -> Dict[str, Any]:
    """
    获取 License 状态（健康检查接口）
    
    【P0-1 关键修复】
    旧代码问题:
        - _machine_id() 抛异常 → LicenseManager.__init__ 崩溃
        - 整个 /api/v1/license 返回 500
        - 应用无法启动
    
    新代码行为:
        - LicenseManager 可降级启动 (degraded=True)
        - /status 始终返回 200 (除非完全不可用)
        - 返回 degraded 标志和原因供诊断
        
    Returns:
        {
            "status": "active" | "inactive" | "expired" | "trial" | "degraded",
            "machine_id": str,
            "degraded": bool,
            "degraded_reason": str | None
        }
        
    Errors:
        503: 授权服务完全不可用
    """
    try:
        mgr = get_license_manager()
        return mgr.status()
        
    except Exception as e:
        # 完全失败时的降级处理
        logger.exception("license_status failed unexpectedly")
        
        return JSONResponse(
            status_code=503,
            content={
                "code": "ZJ-9001",
                "status": "degraded",
                "message": "授权服务暂不可用，请查看诊断日志。",
                "trace": request_trace_id(),
            },
            media_type="application/json"
        )


@router.post("/activate")
async def activate_license(data: Dict[str, str]) -> Dict[str, Any]:
    """
    激活 License
    
    Args:
        data: {"license_key": "xxx"}
        
    Returns:
        {"status": "activated", "machine_id": "xxx"}
        
    Errors:
        400: license_key 必填或激活失败
        503: 服务在降级模式无法激活
    """
    try:
        mgr = get_license_manager()
        
        # 检查是否降级
        if mgr.degraded:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "ZJ-9002",
                    "message": "授权服务不可用，无法激活",
                    "reason": mgr.degraded_reason,
                    "suggestion": "请联系技术支持或重启服务"
                }
            )
        
        license_key = data.get("license_key", "").strip()
        
        if not license_key:
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "ZJ-9003",
                    "message": "license_key 必填"
                }
            )
        
        # 执行激活
        success = mgr.activate(license_key)
        
        if success:
            return {
                "status": "activated",
                "machine_id": mgr.machine_id,
                "message": "License 激活成功"
            }
        else:
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "ZJ-9004",
                    "message": "激活失败，请检查 license_key 是否正确"
                }
            )
            
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("activate_license failed")
        raise HTTPException(
            status_code=500,
            detail={
                "code": "ZJ-9005",
                "message": f"激活过程出错：{str(e)}"
            }
        )


@router.get("/validate")
async def validate_license() -> Dict[str, Any]:
    """
    验证 License 有效性
    
    用于前端显示 License 状态
    
    Returns:
        {
            "valid": bool,
            "state": str,
            "expiry": str | None,
            "degraded": bool
        }
    """
    try:
        mgr = get_license_manager()
        
        if mgr.validate():
            return {
                "valid": True,
                "state": mgr.license_state,
                "expiry": mgr.expiry_date(),
                "machine_id": mgr.machine_id
            }
        else:
            return {
                "valid": False,
                "state": mgr.license_state,
                "expiry": mgr.expiry_date(),
                "degraded": mgr.degraded,
                "reason": mgr.degraded_reason
            }
            
    except Exception as e:
        logger.exception("validate_license failed")
        return {
            "valid": False,
            "error": str(e),
            "degraded": True
        }


@router.get("/info")
async def license_info() -> Dict[str, Any]:
    """
    获取详细 License 信息（包含诊断数据）
    
    用于故障排查
    
    Returns:
        {
            "machine_id": str,
            "license_state": str,
            "degraded": bool,
            "degraded_reason": str | None,
            "capabilities": list,
            "restrictions": list
        }
    """
    try:
        mgr = get_license_manager()
        
        return {
            "machine_id": mgr.machine_id,
            "license_state": mgr.license_state,
            "degraded": mgr.degraded,
            "degraded_reason": mgr.degraded_reason,
            "capabilities": ["basic"] if mgr.degraded else ["full"],
            "restrictions": [] if not mgr.degraded else ["cloud_features_disabled"]
        }
        
    except Exception as e:
        logger.exception("license_info failed")
        return {
            "error": str(e),
            "degraded": True
        }


# ============================================================================
# 健康检查端点（额外提供）
# ============================================================================

@router.get("/health")
async def health_check() -> Dict[str, str]:
    """
    健康检查端点（K8s liveness/readiness probe）
    
    Returns:
        {"status": "ok" | "degraded" | "unavailable"}
    """
    try:
        mgr = get_license_manager()
        
        if mgr.degraded:
            return {"status": "degraded", "message": "License service running with limited functionality"}
        else:
            return {"status": "ok"}
            
    except Exception as e:
        logger.exception("health_check failed")
        return {"status": "unavailable", "message": str(e)}


# ============================================================================
# 示例：如何在主应用中集成
# ============================================================================

"""
# main.py 示例

from fastapi import FastAPI
from .api.v1.license import router as license_router

app = FastAPI(
    title="造境 AI 设计智能体 API",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc"
)

# 注册 License 路由
app.include_router(license_router)

# 启动
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
"""


# ============================================================================
# cURL 测试命令
# ============================================================================

"""
# 1. 获取状态（正常情况）
curl http://localhost:8080/api/v1/license/status

# 预期响应:
{
  "status": "inactive",
  "machine_id": "a1b2c3d4e5f6...",
  "degraded": false
}

# 2. 激活 License
curl -X POST http://localhost:8080/api/v1/license/activate \
  -H "Content-Type: application/json" \
  -d '{"license_key": "TEST-LICENSE-KEY-12345"}'

# 预期响应:
{
  "status": "activated",
  "machine_id": "a1b2c3d4e5f6...",
  "message": "License 激活成功"
}

# 3. 验证 License
curl http://localhost:8080/api/v1/license/validate

# 预期响应 (已激活):
{
  "valid": true,
  "state": "active",
  "expiry": "2027-09-20",
  "machine_id": "a1b2c3d4e5f6..."
}

# 4. 健康检查
curl http://localhost:8080/api/v1/license/health

# 预期响应:
{
  "status": "ok"
}
"""
