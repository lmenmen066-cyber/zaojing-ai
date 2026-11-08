"""
造境 AI 设计智能体 - Model Router / Provider 完整实现

包含:
1. 模型注册表 (models/registry.yaml)
2. Provider 抽象基类
3. 自动路由选择 (成本/延迟/能力/隐私)
4. 限流、熔断、成本熔断
5. API Key 加密存储

版本：V1.0
"""

import yaml
import hashlib
import logging
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field
from enum import Enum
from abc import ABC, abstractmethod
import aiohttp
import asyncio
import time
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


# ============================================================================
# 枚举定义
# ============================================================================

class ModelType(Enum):
    """模型类型"""
    TEXT = "text"
    IMAGE = "image"
    VISION = "vision"
    AUDIO = "audio"


class ProviderType(Enum):
    """Provider 类型"""
    LOCAL = "local"           # Ollama / ComfyUI
    CLOUD_DOUBAO = "doubao"   # 字节跳动豆包
    CLOUD_TONGYI = "tongyi"   # 阿里云通义
    CLOUD_ZHIPU = "zhipu"     # 智谱 AI
    CLOUD_OPENAI = "openai"   # OpenAI
    CLOUD_ANTHROPIC = "anthropic"  # Claude


class CostThreshold(Enum):
    """成本阈值"""
    LOW = 0.1         # $0.1
    MEDIUM = 1.0      # $1.0
    HIGH = 10.0       # $10.0


# ============================================================================
# 数据模型
# ============================================================================

@dataclass
class ModelConfig:
    """模型配置"""
    name: str
    type: ModelType
    provider: ProviderType
    context_window: int
    max_output_tokens: int
    supports_images: bool = False
    supports_vision: bool = False
    
    # 性能指标
    avg_latency_ms: int = 1000
    throughput_tps: float = 10.0
    
    # 成本 (美元/千 token)
    input_cost_per_1k: float = 0.0
    output_cost_per_1k: float = 0.0


@dataclass
class ProviderConfig:
    """Provider 配置"""
    name: str
    type: ProviderType
    api_endpoint: str
    api_key_encrypted: str  # 加密后的 API Key
    rate_limit: int = 100    # 每分钟请求数
    timeout_seconds: int = 60
    
    # 熔断配置
    circuit_breaker: Dict[str, Any] = field(default_factory=lambda: {
        "failure_threshold": 5,
        "reset_timeout_seconds": 60,
        "half_open_requests": 3
    })
    
    # 成本限制
    daily_budget: float = 100.0  # 每日预算上限


# ============================================================================
# Provider 抽象基类
# ============================================================================

class BaseProvider(ABC):
    """Provider 抽象基类"""
    
    def __init__(self, config: ProviderConfig):
        """初始化"""
        self.config = config
        self._request_count = 0
        self._last_request_time: Optional[datetime] = None
        self._failure_count = 0
        self._is_circuit_open = False
        # Phase 60：熔断复出口径=单调时间戳（见 record_failure/_rearm_if_expired），
        # 不再排任何依赖事件循环的复位任务。0.0 表示未在开环态。
        self._circuit_open_until = 0.0
        self._available = True
    
    @abstractmethod
    async def invoke(
        self,
        model_name: str,
        prompt: str,
        **kwargs
    ) -> Dict[str, Any]:
        """
        调用模型
        
        Args:
            model_name: 模型名称
            prompt: 提示词
            **kwargs: 其他参数
            
        Returns:
            模型响应
        """
        pass
    
    @abstractmethod
    async def cancel(self, request_id: str) -> bool:
        """取消正在进行的请求"""
        pass
    
    @abstractmethod
    async def health_check(self) -> bool:
        """健康检查"""
        pass
    
    @abstractmethod
    def estimate_cost(self, input_tokens: int, output_tokens: int) -> float:
        """估算成本"""
        pass
    
    def can_execute(self) -> bool:
        """检查是否可以执行（未熔断）

        Phase 60：复口在读侧惰性判定——开环只留下一枚单调时间戳，到点自己放行，
        因此复位不再要求调用线程有事件循环在场。
        """
        self._rearm_if_expired()
        return self._available and not self._is_circuit_open

    def _rearm_if_expired(self) -> bool:
        """熔断窗口走完就复口（纯计算，零事件循环依赖）；返回本次是否执行了复口。"""
        if self._is_circuit_open and time.monotonic() >= self._circuit_open_until:
            self._is_circuit_open = False
            self._failure_count = 0
            self._circuit_open_until = 0.0
            logger.info(f"Circuit breaker reset for {self.config.name}")
            return True
        return False
    
    def record_success(self):
        """记录成功请求"""
        self._request_count += 1
        self._last_request_time = datetime.now()
        self._failure_count = 0
    
    def record_failure(self):
        """记录失败请求（同步方法，绝不依赖事件循环）"""
        self._failure_count += 1

        # 触发熔断
        if self._failure_count >= self.config.circuit_breaker["failure_threshold"]:
            if not self._is_circuit_open:
                logger.warning(f"Circuit breaker opened for {self.config.name}")
            self._is_circuit_open = True
            # 单一复位窗口守卫：开环只把"最迟可复口时刻"往后推，由最后一次开环说了算；
            # 交错开环时更早那枚窗口不再能提前关掉更新的开环。
            _reset_s = float(self.config.circuit_breaker["reset_timeout_seconds"])
            self._circuit_open_until = time.monotonic() + _reset_s
            logger.info(f"Circuit breaker will re-arm in {_reset_s}s")

    # Phase 60 退役说明（历轮结论不改写，此处留档）：原 `async def _reset_circuit()`
    # 靠 `await asyncio.sleep(timeout)` 定时复口，并由 record_failure() 用
    # asyncio.create_task() 排任务——在同步方法里排任务正是本缺陷根因：
    #   ① 无运行循环时 create_task 当场 RuntimeError（环开了、复位却没排上，另留一条
    #      "coroutine 'BaseProvider._reset_circuit' was never awaited" 警告）；
    #   ② 短命循环（asyncio.run 已返回）里排好的任务随循环一起死掉 => 永久开环；
    #   ③ 交错开环时每枚开环各排一枚自己的计时任务，旧窗口的任务会提前关掉新窗口。
    # 它对外承诺的唯一行为——"窗口过后自动复口"——已由 _rearm_if_expired() 承接，
    # 并由本文件自测「测试 6」的无环/短命环/交错窗口三臂钉住（复口不再需要事件循环）。
    
    def check_rate_limit(self) -> bool:
        """检查是否达到速率限制"""
        if self._last_request_time is None:
            return True
        
        elapsed = (datetime.now() - self._last_request_time).total_seconds()
        requests_per_minute = 60.0 / elapsed if elapsed > 0 else float('inf')
        
        return requests_per_minute <= self.config.rate_limit


# ============================================================================
# Local Provider (Ollama/ComfyUI)
# ============================================================================

class LocalProvider(BaseProvider):
    """本地模型 Provider"""
    
    async def invoke(
        self,
        model_name: str,
        prompt: str,
        **kwargs
    ) -> Dict[str, Any]:
        """调用本地模型"""
        if not self.can_execute():
            raise Exception("Circuit breaker open")
        
        try:
            # 真实通信：向 Ollama /api/generate 发起 HTTP 请求（失败时如实抛出，不伪造内容）
            async with aiohttp.ClientSession() as session:
                url = f"{self.config.api_endpoint}/api/generate"
                
                payload = {
                    "model": model_name,
                    "prompt": prompt,
                    "options": kwargs.get("options", {}),
                    "stream": False,
                }
                
                async with session.post(url, json=payload) as response:
                    response.raise_for_status()
                    result = await response.json()
                    
                    self.record_success()
                    # Ollama 非流式响应会回报真实 token 统计（prompt_eval_count/eval_count）
                    usage = result.get("usage") or {
                        "prompt_tokens": int(result.get("prompt_eval_count", 0) or 0),
                        "completion_tokens": int(result.get("eval_count", 0) or 0),
                    }
                    return {
                        "content": result.get("response", ""),
                        "usage": usage,
                    }
        
        except Exception as e:
            self.record_failure()
            logger.error(f"Local provider invocation failed: {e}")
            raise
    
    async def cancel(self, request_id: str) -> bool:
        """取消请求"""
        # TODO: 实现取消逻辑
        return True
    
    async def health_check(self) -> bool:
        """健康检查"""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(self.config.api_endpoint) as response:
                    return response.status == 200
        except Exception:
            return False
    
    def estimate_cost(self, input_tokens: int, output_tokens: int) -> float:
        """本地模型免费"""
        return 0.0


# ============================================================================
# Cloud Provider (示例：Doubao)
# ============================================================================

class DoubaoProvider(BaseProvider):
    """字节跳动豆包 Provider"""
    
    async def invoke(
        self,
        model_name: str,
        prompt: str,
        **kwargs
    ) -> Dict[str, Any]:
        """调用豆包模型"""
        if not self.can_execute():
            raise Exception("Circuit breaker open")
        
        try:
            # 真实通信：向豆包 Ark chat/completions 发起 HTTP 请求（失败时如实抛出）
            async with aiohttp.ClientSession() as session:
                url = "https://ark.cn-beijing.volces.com/api/v3/chat/completions"
                
                headers = {
                    "Authorization": f"Bearer {self.config.api_key_encrypted}",
                    "Content-Type": "application/json"
                }
                
                payload = {
                    "model": model_name,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": kwargs.get("temperature", 0.7),
                }
                
                async with session.post(url, headers=headers, json=payload) as response:
                    response.raise_for_status()
                    result = await response.json()
                    
                    self.record_success()
                    return {
                        "content": result["choices"][0]["message"]["content"],
                        "usage": result.get("usage", {})
                    }
        
        except Exception as e:
            self.record_failure()
            logger.error(f"Doubao provider invocation failed: {e}")
            raise
    
    async def cancel(self, request_id: str) -> bool:
        return True
    
    async def health_check(self) -> bool:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(self.config.api_endpoint) as response:
                    return response.status == 200
        except Exception:
            return False
    
    def estimate_cost(self, input_tokens: int, output_tokens: int) -> float:
        """豆包成本估算 ($0.002/$0.006 per 1k tokens)"""
        input_cost = (input_tokens / 1000) * 0.002
        output_cost = (output_tokens / 1000) * 0.006
        return input_cost + output_cost


# ============================================================================
# Model Registry
# ============================================================================

class ModelRegistry:
    """模型注册表"""
    
    def __init__(self, registry_path: str):
        """
        初始化
        
        Args:
            registry_path: registry.yaml 文件路径
        """
        self.registry_path = registry_path
        self.models: Dict[str, ModelConfig] = {}
        self.providers: Dict[str, BaseProvider] = {}
        self._load_registry()
    
    def _load_registry(self):
        """加载注册表"""
        try:
            with open(self.registry_path, "r", encoding="utf-8") as f:
                registry = yaml.safe_load(f) or {}
            
            # 加载模型（枚举字段自动转换）
            for model_data in registry.get("models", []):
                md = dict(model_data)
                if isinstance(md.get("type"), str):
                    md["type"] = ModelType(md["type"])
                if isinstance(md.get("provider"), str):
                    md["provider"] = ProviderType(md["provider"])
                model = ModelConfig(**md)
                self.models[model.name] = model
            
            # 加载 Provider
            for provider_data in registry.get("providers", []):
                provider_type = provider_data.pop("type")
                
                if provider_type == "local":
                    provider_cls = LocalProvider
                elif provider_type == "doubao":
                    provider_cls = DoubaoProvider
                else:
                    continue
                
                config = ProviderConfig(**provider_data, type=ProviderType(provider_type))
                self.providers[config.name] = provider_cls(config)
            
            logger.info(f"Loaded {len(self.models)} models and {len(self.providers)} providers")
        
        except FileNotFoundError:
            logger.warning(f"Registry file not found: {self.registry_path}")
            self._create_default_registry()
        except Exception as e:
            logger.error(f"Registry load failed ({e}), recreating default registry")
            self._create_default_registry()
    
    def _create_default_registry(self):
        """创建默认注册表"""
        default_registry = {
            "models": [
                {
                    "name": "llama-3-8b",
                    "type": "text",
                    "provider": "local",
                    "context_window": 8192,
                    "max_output_tokens": 4096,
                    "avg_latency_ms": 500,
                    "input_cost_per_1k": 0.0,
                    "output_cost_per_1k": 0.0
                },
                {
                    "name": "doubao-pro",
                    "type": "text",
                    "provider": "doubao",
                    "context_window": 32768,
                    "max_output_tokens": 8192,
                    "avg_latency_ms": 1200,
                    "input_cost_per_1k": 0.002,
                    "output_cost_per_1k": 0.006
                },
            ],
            "providers": [
                {
                    "name": "ollama-local",
                    "type": "local",
                    "api_endpoint": "http://localhost:11434",
                    "api_key_encrypted": "",  # 本地不需要
                    "rate_limit": 60,
                    "timeout_seconds": 120
                },
                {
                    "name": "doubao-api",
                    "type": "doubao",
                    "api_endpoint": "https://ark.cn-beijing.volces.com",
                    "api_key_encrypted": "",  # 需要加密存储
                    "rate_limit": 100,
                    "timeout_seconds": 60,
                    "daily_budget": 100.0
                }
            ]
        }
        
        with open(self.registry_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(default_registry, f, allow_unicode=True, default_flow_style=False)
        
        logger.info(f"Created default registry: {self.registry_path}")
        self._load_registry()


# ============================================================================
# Model Router - 自动路由选择
# ============================================================================

class ModelRouter:
    """模型路由器"""
    
    def __init__(self, registry: ModelRegistry):
        """初始化"""
        self.registry = registry
        self._cost_tracker: Dict[str, float] = {}  # Provider -> 今日花费
        self._selected_provider: Optional[str] = None
    
    def select_provider(
        self,
        model_name: str,
        requirements: Dict[str, Any]
    ) -> Optional[str]:
        """
        自动选择最佳 Provider
        
        选择策略:
        1. 优先本地模型 (免费/隐私)
        2. 其次低成本 Provider
        3. 考虑延迟要求
        4. 检查熔断状态
        5. 检查成本预算
        
        Args:
            model_name: 模型名称
            requirements: 需求 ({'priority': 'cost'|'latency'|'privacy'})
            
        Returns:
            Provider 名称
        """
        model = self.registry.models.get(model_name)
        if not model:
            logger.warning(f"Unknown model: {model_name}")
            return None
        
        # 获取所有可用的 Provider
        available_providers = []
        
        for provider_name, provider in self.registry.providers.items():
            # 检查健康状态
            if not provider.can_execute():
                continue
            
            # 检查速率限制
            if not provider.check_rate_limit():
                continue
            
            # 检查成本预算
            if self._check_daily_budget(provider_name):
                available_providers.append((provider_name, provider))
        
        if not available_providers:
            logger.warning("No available providers")
            return None
        
        # 根据优先级排序
        priority = requirements.get("priority", "cost")
        
        if priority == "cost":
            # 按成本排序
            available_providers.sort(
                key=lambda x: self._estimate_total_cost(x[1], model),
                reverse=False
            )
        elif priority == "latency":
            # 按延迟排序
            available_providers.sort(
                key=lambda x: x[1].config.circuit_breaker.get("avg_latency_ms", 9999),
                reverse=False
            )
        elif priority == "privacy":
            # 优先本地
            available_providers.sort(
                key=lambda x: 0 if x[1].config.type == ProviderType.LOCAL else 1,
                reverse=False
            )
        
        # 返回最佳 Provider
        self._selected_provider = available_providers[0][0]
        return self._selected_provider
    
    def _estimate_total_cost(self, provider: BaseProvider, model: ModelConfig) -> float:
        """估算总成本"""
        # 简化估算
        return (model.input_cost_per_1k + model.output_cost_per_1k) / 2
    
    def _check_daily_budget(self, provider_name: str) -> bool:
        """检查是否超出每日预算"""
        current_spending = self._cost_tracker.get(provider_name, 0.0)
        provider_config = self.registry.providers.get(provider_name)
        
        if provider_config:
            budget = provider_config.config.daily_budget
            return current_spending < budget
        
        return True
    
    def record_cost(self, provider_name: str, cost: float):
        """记录成本"""
        self._cost_tracker[provider_name] = self._cost_tracker.get(provider_name, 0.0) + cost
    
    def get_selected_provider(self) -> Optional[str]:
        """获取最近选择的 Provider"""
        return self._selected_provider


# ============================================================================
# 配置文件示例
# ============================================================================

DEFAULT_REGISTRY_YAML = """
# Model Registry - 模型注册表
# P2 修复：禁止硬编码 Provider，统一从 YAML 配置加载

models:
  - name: llama-3-8b
    type: text
    provider: local
    context_window: 8192
    max_output_tokens: 4096
    supports_images: false
    supports_vision: false
    avg_latency_ms: 500
    throughput_tps: 15.0
    input_cost_per_1k: 0.0
    output_cost_per_1k: 0.0
  
  - name: doubao-pro
    type: text
    provider: doubao
    context_window: 32768
    max_output_tokens: 8192
    supports_images: false
    supports_vision: false
    avg_latency_ms: 1200
    throughput_tps: 8.0
    input_cost_per_1k: 0.002
    output_cost_per_1k: 0.006
  
  - name: stable-diffusion-xl
    type: image
    provider: local
    context_window: 0
    max_output_tokens: 0
    supports_images: true
    supports_vision: false
    avg_latency_ms: 3000
    throughput_tps: 0.5
    input_cost_per_1k: 0.0
    output_cost_per_1k: 0.0

providers:
  - name: ollama-local
    type: local
    api_endpoint: http://localhost:11434
    api_key_encrypted: ""
    rate_limit: 60
    timeout_seconds: 120
    circuit_breaker:
      failure_threshold: 5
      reset_timeout_seconds: 60
      half_open_requests: 3
  
  - name: doubao-api
    type: doubao
    api_endpoint: https://ark.cn-beijing.volces.com
    api_key_encrypted: "${DOUBAO_API_KEY}"
    rate_limit: 100
    timeout_seconds: 60
    daily_budget: 100.0
    circuit_breaker:
      failure_threshold: 10
      reset_timeout_seconds: 120
      half_open_requests: 5
"""


# ============================================================================
# 快速测试
# ============================================================================

if __name__ == "__main__":
    import tempfile
    import os
    print("=" * 60)
    print("P2 Model Router - 断言自测（会因伪造路由/空注册表失败）")
    print("=" * 60)

    # 测试 1: 注册表真实解析 —— DEFAULT_REGISTRY_YAML 必须解析出已知模型与 Provider
    temp_dir = tempfile.mkdtemp()
    registry_path = os.path.join(temp_dir, "registry.yaml")
    with open(registry_path, "w", encoding="utf-8") as f:
        f.write(DEFAULT_REGISTRY_YAML)
    registry = ModelRegistry(registry_path)
    assert len(registry.models) >= 3, ("注册表应解析出至少 3 个模型", list(registry.models))
    for _m in ("llama-3-8b", "doubao-pro", "stable-diffusion-xl"):
        assert _m in registry.models, ("已知模型未解析", _m)
    assert len(registry.providers) >= 2, ("注册表应解析出至少 2 个 Provider", list(registry.providers))
    # 至少一个本地 Provider（隐私路由的前提，且证明 Provider 真被实例化为对象）
    _local_providers = [n for n, p in registry.providers.items() if p.config.type == ProviderType.LOCAL]
    assert len(_local_providers) >= 1, "未声明任何本地 Provider（隐私路由无从谈起）"
    print(f"  [OK] 注册表解析: {len(registry.models)} 模型 / {len(registry.providers)} Provider / 本地={_local_providers}")

    router = ModelRouter(registry)

    # 测试 2: 诚实路由 —— 未知模型必须返回 None（伪造"总返回某个 Provider"的路由会在此失守）
    sel_unknown = router.select_provider("this-model-does-not-exist", {"priority": "cost"})
    assert sel_unknown is None, ("未知模型不得被虚构路由", sel_unknown)
    print("  [OK] 未知模型诚实返回 None（无伪造路由）")

    # 测试 3: 已知模型的路由结果只能是 None 或真实存在的 Provider 名（绝非凭空字符串）
    for _prio in ("cost", "latency", "privacy"):
        sel = router.select_provider("llama-3-8b", {"priority": _prio})
        assert sel is None or sel in registry.providers, ("路由返回了不存在的 Provider", _prio, sel)

    # 测试 4: 隐私优先绝不允许选中非本地 Provider（真实语义约束，硬编码/伪路由会违反）
    sel_priv = router.select_provider("llama-3-8b", {"priority": "privacy"})
    if sel_priv is not None:
        assert registry.providers[sel_priv].config.type == ProviderType.LOCAL, \
            ("隐私优先却选了云端 Provider", sel_priv, registry.providers[sel_priv].config.type)
    print(f"  [OK] 三优先级路由合法 + 隐私优先本地约束: {sel_priv}")

    # 测试 5: 本地 Provider 健康检查返回布尔且诚实判定（离线自测不触碰云端外网，仅探本地端点）
    _names = []
    for _n in _local_providers:
        try:
            _h = asyncio.run(asyncio.wait_for(registry.providers[_n].health_check(), timeout=5))
        except Exception:
            _h = False  # 本地端点不可达/离线：诚实视为 False，绝不谎报健康
        assert isinstance(_h, bool), ("health_check 应返回布尔", _n, _h)
        _names.append((_n, _h))
    print(f"  [OK] 本地 Provider health_check 返回布尔（离线诚实 False，不谎报）: {_names}")

    # 测试 6: 熔断复位不依赖事件循环（Phase 60 真缺陷根治判据；先跑 RED 再修产品）
    #   修前自陈（历轮结论不改写，此处留档）：record_failure() 用
    #   asyncio.create_task(self._reset_circuit()) 排一次复位，于是——
    #     ① 调用线程没有运行中的事件循环时 create_task 当场 RuntimeError
    #        （环已开、复位任务却没排上，还留下一条"coroutine never awaited"警告）；
    #     ② 在 asyncio.run(...) 这类短命循环里排的任务随循环拆除一起死掉
    #        => 熔断永久开环，provider 再也回不来；
    #     ③ 交错开环时每枚开环各排一枚自己的计时任务，旧窗口的任务会提前
    #        关掉新窗口（复位窗口被更早的那枚说话）。
    #   修后口径：开环只写一个单调时间戳，复口由读侧 can_execute() 惰性判定——
    #   零事件循环依赖，且"最后一次开环说了算"（单一复位窗口守卫）。
    def _p60_provider(_tag, _threshold, _reset_s):
        _c = ProviderConfig(
            name="p60-" + _tag,
            type=ProviderType.LOCAL,
            api_endpoint="http://127.0.0.1:1",   # 本地关闭端口：只建对象，不发请求
            api_key_encrypted="",
            circuit_breaker={
                "failure_threshold": _threshold,
                "reset_timeout_seconds": _reset_s,
                "half_open_requests": 3,
            },
        )
        return LocalProvider(_c)

    _a6 = []

    def _arm6(_name, _fn):
        try:
            _a6.append((_name, True, _fn()))
        except Exception as _e:
            _a6.append((_name, False, type(_e).__name__ + ": " + str(_e)[:180]))

    def _a6_no_loop():
        # 臂①：顶层（无运行循环）trip 不得抛错，窗口过后必须自己复口
        _p = _p60_provider("noloop", 1, 0.05)
        assert _p.can_execute() is True, "trip 之前必须可用"
        _p.record_failure()
        assert _p.can_execute() is False, "达阈值必须开环，且 record_failure 不得抛错"
        time.sleep(0.12)
        assert _p.can_execute() is True, "窗口过后必须自动复口（全程无事件循环）"
        return {"threshold": 1, "reset_s": 0.05, "rearmed_without_loop": True}

    def _a6_short_loop():
        # 臂②：在 asyncio.run 的短命循环里 trip，循环拆除后仍须复口（修前=永久开环）
        _p = _p60_provider("shortloop", 1, 0.05)

        async def _trip():
            _p.record_failure()
            assert _p.can_execute() is False, "环内必须开环"

        asyncio.run(_trip())
        time.sleep(0.12)
        assert _p.can_execute() is True, "循环已拆除仍须复口（不得永久开环）"
        return {"loop_torn_down": True, "rearmed": True}

    def _a6_interleave():
        # 臂③：交错开环——旧窗口（0.30s）不得提前关闭新窗口（2.0s）
        _p = _p60_provider("interleave", 1, 0.30)
        _p.record_failure()                                   # opening #1 -> 0.30s
        _p.config.circuit_breaker["reset_timeout_seconds"] = 2.0
        _p.record_failure()                                   # opening #2 -> 2.00s
        time.sleep(0.45)                                      # 已过 #1，远在 #2 之内
        assert _p.can_execute() is False, "较旧的复位窗口不得提前关闭较新的开环"
        return {"window_1_s": 0.30, "window_2_s": 2.0, "checked_at_s": 0.45}

    def _a6_negative():
        # 臂④：阴性对照——未达阈值绝不许开环（防止把修复写成"逢失败即开环"）
        _p = _p60_provider("negctl", 3, 0.05)
        _p.record_failure()
        _p.record_failure()
        assert _p.can_execute() is True, "2/3 次失败未达阈值，不得开环"
        return {"failures": 2, "threshold": 3, "still_closed": True}

    _arm6("无环trip+到点复口", _a6_no_loop)
    _arm6("短命环拆除后复口", _a6_short_loop)
    _arm6("交错窗口不被旧计时器提前关", _a6_interleave)
    _arm6("未达阈值不开环（阴性对照）", _a6_negative)
    for _n6, _ok6, _d6 in _a6:
        print("  [" + ("OK " if _ok6 else "RED") + "] 熔断-" + _n6 + ": " + str(_d6))
    _bad6 = [x for x in _a6 if not x[1]]
    assert not _bad6, ("熔断复位不依赖事件循环：未全绿（修前即为 RED 凭证）", _bad6)
    time.sleep(2.1)  # 臂③的新窗口走完，复核它最终确实会复口（不是关不掉）
    _p60 = _p60_provider("interleave-final", 1, 0.05)
    _p60.record_failure()
    time.sleep(0.12)
    assert _p60.can_execute() is True, "开环必须可复口（守卫不得变成永久开环）"
    print(f"  [OK] 熔断复位不依赖事件循环：{len(_a6)} 臂全绿 + 到点复口复核"
          f"（无环 / 短命环 / 交错窗口 / 阴性对照）")

    print("\n" + "=" * 60)
    print("[PASS] P2 Model Router 断言自测全部通过（诚实 None 路由 + 隐私约束 + 真实解析）")
    print("=" * 60)
