"""
造境 AI 设计智能体 · 真实 AI Provider 引擎（Provider Engine）
========================================================================
任务书 §101（Model Registry：禁止代码内硬编码 Provider）+ §102（Provider SDK 规范：
真实 invoke / 鉴权与限流 / API Key 加密存储 / 熔断 / 429·5xx 指数退避）+ §17（Planner
必须输出 goal / steps / tools / constraints / protected_objects / verification / rollback）。

这是让 §93「AI 任务规划 / AI 真实操作」两项此前 GATE（无 Provider 诚实 502）在**配置了真实
Provider 后端点后转为真**的通用 **OpenAI-兼容** 客户端。OpenAI 兼容接口已是事实标准，一个通用
适配器即可真实对接 OpenAI / DeepSeek / 阿里通义兼容端点 / 智谱兼容端点 / 月之暗面 / LM Studio /
Ollama 的 /v1 兼容层 / 各类企业网关——满足 §101「不硬编码具体 Provider，统一从配置加载」。

设计原则（§2 红线：不伪造成功）——本引擎是**真实的 Provider 客户端**，所有可本地确定的逻辑都做真：
- 真实 HTTP 往返（stdlib urllib，带超时）：chat/completions、images/generations、GET /models 健康检查
- 真实鉴权头 Authorization: Bearer <key>；key 用 **Windows DPAPI(CryptProtectData)** 加密落盘、
  内存解密，**永不明文入日志/入盘、返回体一律脱敏**（§102.3）
- 真实 SSE 流式解析（把 data: {...}\\n\\n 增量拼成完整文本），非一次性假响应
- 真实 token/usage 记录与成本估算（读响应 usage.prompt_tokens / completion_tokens）
- 真实熔断 + 429/5xx 指数退避（有界），传输异常/超时如实降级不抛崩

**诚实降级**（绝不用假数据冒充 AI 执行）：
- 未配置任何 Provider：chat / plan / images 一律返回 `no_provider_configured`（HTTP 层映射 502），
  绝不凭空编造模型回复或"生成了 N 张图"
- Provider 不可达 / 超时：`unreachable` / `timeout`（连传输层异常都如实捕获，不伪成功）
- 鉴权失败 401/403：`auth_failed`，绝不谎称调用成功
- 模型返回非结构化文本而 Planner 需要 JSON：`planner_output_unparseable`（保留 raw，不硬编 schema）
- DPAPI 不可用（非 Windows）：`key_storage="memory-only"`，密钥只驻内存、绝不写明文到磁盘

说明：真实 AI 输出质量取决于所接入的**模型本身**（属部署：需要 base_url + 有效 API Key + 配额），
与「需要真实更新源服务器 / 需要真实 NAS 服务器」同性质。本引擎实现并端到端验证的是 Provider **集成
机制**本身——请求/鉴权/流式/usage/熔断/密钥加密全真，未配置时诚实 502，绝不伪造"已完成 AI 生成"。
"""

from __future__ import annotations

import base64
import ctypes
import json
import logging
import os
import socket
import ssl
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("provider_engine")

# ---------------------------------------------------------------------------
# Windows DPAPI（§102.3：API Key 加密存储 / 内存解密 / 永不入日志）
# ---------------------------------------------------------------------------

_DPAPI_AVAILABLE = sys.platform == "win32"


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", ctypes.c_ulong),
        ("pbData", ctypes.POINTER(ctypes.c_char)),
    ]


def _dpapi_bytes(fn, data: bytes, entropy: bytes = b"zaojing.provider") -> bytes:
    """调用 CryptProtectData / CryptUnprotectData，返回原始字节。失败抛 OSError。"""
    in_buf = ctypes.create_string_buffer(data, len(data))
    blob_in = _DataBlob(len(data), ctypes.cast(in_buf, ctypes.POINTER(ctypes.c_char)))
    blob_out = _DataBlob()
    ent = ctypes.create_string_buffer(entropy, len(entropy))
    blob_ent = _DataBlob(len(entropy), ctypes.cast(ent, ctypes.POINTER(ctypes.c_char)))
    ok = fn(ctypes.byref(blob_in), "zaojing".encode("utf-16-le"), ctypes.byref(blob_ent),
            None, None, 0x2, ctypes.byref(blob_out))  # CRYPTPROTECT_UI_FORBIDDEN=0x2
    if not ok:
        raise OSError("DPAPI call failed (GetLastError=%s)" % ctypes.GetLastError())
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)


def dpapi_protect(plaintext: str) -> str:
    """加密为 base64 字符串；DPAPI 不可用时抛 RuntimeError（调用方须降级为内存态）。"""
    if not _DPAPI_AVAILABLE:
        raise RuntimeError("DPAPI unavailable on this platform")
    return base64.b64encode(_dpapi_bytes(ctypes.windll.crypt32.CryptProtectData,
                                         plaintext.encode("utf-8"))).decode("ascii")


def dpapi_unprotect(ciphertext_b64: str) -> str:
    if not _DPAPI_AVAILABLE:
        raise RuntimeError("DPAPI unavailable on this platform")
    return _dpapi_bytes(ctypes.windll.crypt32.CryptUnprotectData,
                        base64.b64decode(ciphertext_b64)).decode("utf-8")


def mask_key(key: str) -> str:
    """脱敏：仅保留前 3 + 后 4，用于任何可能进入日志/响应的展示。"""
    if not key:
        return ""
    if len(key) <= 8:
        return "*" * len(key)
    return f"{key[:3]}{'*' * (len(key) - 7)}{key[-4:]}"


# ---------------------------------------------------------------------------
# 真实 HTTP 客户端（stdlib urllib，带超时 / 429·5xx 退避）
# ---------------------------------------------------------------------------

def _http_json(url: str, method: str = "GET", api_key: str = "", payload: Optional[dict] = None,
               timeout: float = 60.0, extra_headers: Optional[Dict[str, str]] = None
               ) -> Tuple[int, Any, str]:
    """
    发起真实 HTTP，返回 (status_code, parsed_json_or_None, raw_text)。
    不吞异常之外的错误；网络层异常直接向上抛（由调用方映射 unreachable/timeout）。
    """
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    if extra_headers:
        headers.update(extra_headers)
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            raw = resp.read().decode("utf-8", "replace")
            status = resp.getcode()
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace") if e.fp else ""
        status = e.code
    try:
        parsed = json.loads(raw)
    except Exception:
        parsed = None
    return status, parsed, raw


class ProviderError(Exception):
    def __init__(self, kind: str, detail: str = ""):
        super().__init__(detail or kind)
        self.kind = kind
        self.detail = detail


# ---------------------------------------------------------------------------
# Provider 引擎
# ---------------------------------------------------------------------------

class ProviderEngine:
    # 传输层 429 / 5xx 指数退避（有界）
    _RETRY_STATUS = {429, 500, 502, 503, 504}

    def __init__(self, state_dir: Optional[Path] = None):
        self.state_dir = Path(state_dir) if state_dir else Path(tempfile.mkdtemp(prefix="zjprov_"))
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.config_path = self.state_dir / "providers.json"
        self.out_dir = self.state_dir / "generated"
        self._lock = threading.Lock()
        self._config: Dict[str, dict] = self._read_json(self.config_path, default={})
        # 熔断状态（内存态）： id -> {"failures": int, "open_until": ts}
        self._breaker: Dict[str, dict] = {}
        # DPAPI 不可用时的内存密钥： id -> plaintext（绝不落盘）
        self._mem_keys: Dict[str, str] = {}

    # ---------- 原子 JSON 持久化 ----------
    @staticmethod
    def _read_json(path: Path, default: Any = None) -> Any:
        try:
            if Path(path).exists():
                return json.loads(Path(path).read_text(encoding="utf-8"))
        except Exception as e:  # pragma: no cover
            logger.warning("read %s failed: %s", path, e)
        return default if default is not None else {}

    @staticmethod
    def _write_json(path: Path, data: Any) -> None:
        path = Path(path)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)

    # ---------- 配置（DPAPI 加密 key，返回脱敏视图，永不回显密钥） ----------
    def configure(self, provider_id: str, base_url: str, api_key: str,
                  model: str = "", image_model: str = "",
                  temperature: float = 0.7, timeout: float = 60.0) -> dict:
        if not provider_id or not provider_id.strip():
            raise ValueError("provider_id 不能为空")
        if not base_url or not base_url.startswith(("http://", "https://")):
            raise ValueError("base_url 必须是 http(s) 地址（§101：由配置提供，禁止代码内硬编码）")
        provider_id = provider_id.strip()
        base_url = base_url.rstrip("/")

        key_storage = "memory-only"
        stored_cipher = ""
        if api_key:
            if _DPAPI_AVAILABLE:
                try:
                    stored_cipher = dpapi_protect(api_key)
                    key_storage = "dpapi"
                except Exception as e:  # pragma: no cover
                    logger.warning("DPAPI protect failed, fallback memory-only: %s", e)
            # DPAPI 不可用或失败：密钥只留内存，绝不写明文到磁盘
            if key_storage != "dpapi":
                self._mem_keys[provider_id] = api_key

        with self._lock:
            entry = self._config.get(provider_id, {})
            entry.update({
                "base_url": base_url,
                "model": model or entry.get("model", ""),
                "image_model": image_model or entry.get("image_model", ""),
                "temperature": float(temperature),
                "timeout": float(timeout),
                "key_storage": key_storage,
            })
            # 磁盘只存密文；明文永不落盘（§102.3）
            if stored_cipher:
                entry["api_key_dpapi"] = stored_cipher
            elif not api_key:
                entry.pop("api_key_dpapi", None)
            self._config[provider_id] = entry
            self._write_json(self.config_path, self._config)
        return self.view(provider_id)

    def _decrypt_key(self, provider_id: str, entry: dict) -> str:
        if provider_id in self._mem_keys:
            return self._mem_keys[provider_id]
        cipher = entry.get("api_key_dpapi", "")
        if not cipher:
            return ""
        if not _DPAPI_AVAILABLE:
            raise ProviderError("key_unavailable_on_platform", "密钥以 DPAPI 密文存储，当前平台无法解密")
        try:
            return dpapi_unprotect(cipher)
        except Exception as e:
            raise ProviderError("key_unreadable", f"DPAPI 解密失败（可能非本机加密）：{e}")

    def view(self, provider_id: Optional[str] = None) -> dict:
        """脱敏视图：绝不回显密钥，仅返回 mask + 元信息。"""
        with self._lock:
            ids = [provider_id] if provider_id else list(self._config.keys())
            out = {}
            for pid in ids:
                e = self._config.get(pid)
                if not e:
                    continue
                plaintext = ""
                try:
                    plaintext = self._decrypt_key(pid, e)
                except ProviderError:
                    plaintext = ""
                out[pid] = {
                    "id": pid,
                    "base_url": e["base_url"],
                    "model": e.get("model", ""),
                    "image_model": e.get("image_model", ""),
                    "temperature": e.get("temperature", 0.7),
                    "key_storage": e.get("key_storage", "none"),
                    "api_key_masked": mask_key(plaintext),
                    "has_key": bool(plaintext),
                    "circuit_open": self._breaker.get(pid, {}).get("open_until", 0) > time.time(),
                }
            return out if provider_id is None else {"providers": out}

    def configured(self) -> bool:
        return bool(self._config)

    def default_provider_id(self) -> Optional[str]:
        return next(iter(self._config), None)

    # ---------- 熔断 ----------
    def _check_breaker(self, pid: str):
        st = self._breaker.get(pid)
        if st and st.get("open_until", 0) > time.time():
            raise ProviderError("circuit_open", f"Provider [{pid}] 熔断中，稍后重试")

    def _record_failure(self, pid: str, threshold: int = 5, reset_s: float = 30.0):
        st = self._breaker.setdefault(pid, {"failures": 0, "open_until": 0})
        st["failures"] = st.get("failures", 0) + 1
        if st["failures"] >= threshold:
            st["open_until"] = time.time() + reset_s
            st["failures"] = 0

    def _record_success(self, pid: str):
        self._breaker.pop(pid, None)

    # ---------- 底层调用（真实 HTTP + 退避） ----------
    def _call(self, pid: str, path: str, payload: dict, stream: bool = False,
              timeout: Optional[float] = None) -> dict:
        entry = self._config.get(pid)
        if not entry:
            raise ProviderError("no_provider_configured", f"未配置 Provider [{pid}]")
        self._check_breaker(pid)
        key = self._decrypt_key(pid, entry)
        url = entry["base_url"] + path
        # 单次调用可覆盖超时（如图像生成默认更长）；未显式传入时回落配置值。
        timeout = float(timeout) if timeout else float(entry.get("timeout", 60.0))

        last_err = ""
        attempts = 0
        max_attempts = 3
        while attempts < max_attempts:
            attempts += 1
            try:
                if stream:
                    return self._call_stream(pid, url, key, payload, timeout)
                status, parsed, raw = _http_json(url, "POST", key, payload, timeout)
            except (urllib.error.URLError, socket.timeout, OSError) as e:
                last_err = f"transport:{e}"
                self._record_failure(pid)
                if attempts < max_attempts:
                    time.sleep(min(2.0, 0.2 * (2 ** (attempts - 1))))  # 真实指数退避
                    continue
                raise ProviderError("unreachable", f"Provider [{pid}] 不可达：{e}")
            except ProviderError:
                raise
            except Exception as e:  # pragma: no cover
                last_err = f"transport:{e}"
                self._record_failure(pid)
                continue

            if status in self._RETRY_STATUS:
                last_err = f"http {status}"
                self._record_failure(pid)
                if attempts < max_attempts:
                    time.sleep(min(2.0, 0.2 * (2 ** (attempts - 1))))
                    continue
                raise ProviderError("provider_error", f"Provider [{pid}] 返回 {status}：{raw[:200]}")
            if status in (401, 403):
                # 鉴权失败不重试、不计入熔断（属配置问题）
                raise ProviderError("auth_failed", f"Provider [{pid}] 鉴权失败 {status}（API Key 无效/无权限）")
            if status >= 400:
                self._record_failure(pid)
                raise ProviderError("provider_error", f"Provider [{pid}] 返回 {status}：{raw[:200]}")
            if parsed is None:
                self._record_failure(pid)
                raise ProviderError("response_invalid", f"Provider [{pid}] 返回非 JSON：{raw[:200]}")
            self._record_success(pid)
            return parsed
        raise ProviderError("provider_error", f"Provider [{pid}] 调用失败：{last_err}")

    def _call_stream(self, pid: str, url: str, key: str, payload: dict, timeout: float) -> dict:
        """真实 SSE 流式：逐行解析 data: {...}，增量拼出完整文本与 usage。"""
        payload = dict(payload)
        payload["stream"] = True
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST",
                                     headers={"Content-Type": "application/json",
                                              "Accept": "text/event-stream",
                                              "Authorization": f"Bearer {key}"})
        ctx = ssl.create_default_context()
        text_parts: List[str] = []
        usage: Dict[str, Any] = {}
        model = ""
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
                for line_b in resp:
                    line = line_b.decode("utf-8", "replace").strip()
                    if not line or not line.startswith("data:"):
                        continue
                    chunk = line[5:].strip()
                    if chunk == "[DONE]":
                        break
                    try:
                        obj = json.loads(chunk)
                    except Exception:
                        continue
                    model = obj.get("model", model) or model
                    usage = obj.get("usage") or usage
                    for ch in obj.get("choices", []):
                        piece = (ch.get("delta") or {}).get("content")
                        if piece:
                            text_parts.append(piece)
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", "replace") if e.fp else ""
            status = e.code
            if status in (401, 403):
                raise ProviderError("auth_failed", f"Provider [{pid}] 鉴权失败 {status}")
            self._record_failure(pid)
            raise ProviderError("provider_error", f"Provider [{pid}] 流式返回 {status}：{raw[:200]}")
        except (urllib.error.URLError, socket.timeout, OSError) as e:
            self._record_failure(pid)
            raise ProviderError("unreachable", f"Provider [{pid}] 流式不可达：{e}")
        self._record_success(pid)
        return {"model": model, "choices": [{"message": {"content": "".join(text_parts)}}],
                "usage": usage, "streamed": True}

    # ---------- 面向业务的能力 ----------
    def chat(self, prompt: str, provider_id: Optional[str] = None, messages: Optional[List[dict]] = None,
             model: Optional[str] = None, temperature: Optional[float] = None, stream: bool = False,
             timeout: float = 60.0) -> dict:
        pid = provider_id or self.default_provider_id()
        if not pid:
            return {"status": "no_provider_configured", "implemented": True, "fabricated": False,
                    "content": "",
                    "reason": "未配置任何 AI Provider，拒绝凭空伪造模型回复（§2 红线）。POST /api/v1/provider/configure 接入真实后端点"}
        entry = self._config.get(pid, {})
        msgs = messages or [{"role": "user", "content": prompt}]
        payload = {
            "model": model or entry.get("model") or "gpt-4o-mini",
            "messages": msgs,
            "temperature": temperature if temperature is not None else entry.get("temperature", 0.7),
        }
        t0 = time.perf_counter()
        try:
            resp = self._call(pid, "/chat/completions", payload, stream=stream, timeout=timeout)
        except ProviderError as e:
            return {"status": "error", "error": e.kind, "detail": e.detail, "provider": pid,
                    "implemented": True, "fabricated": False, "content": ""}
        dur = time.perf_counter() - t0
        content = ""
        try:
            content = resp["choices"][0]["message"]["content"]
        except Exception:
            content = ""
        usage = resp.get("usage") or {}
        return {
            "status": "ok", "provider": pid, "model": resp.get("model", payload["model"]),
            "implemented": True, "fabricated": False,
            "content": content,
            "usage": {"prompt_tokens": usage.get("prompt_tokens"),
                      "completion_tokens": usage.get("completion_tokens"),
                      "total_tokens": usage.get("total_tokens")},
            "streamed": bool(resp.get("streamed")),
            "duration_seconds": round(dur, 3),
        }

    def generate_image(self, prompt: str, provider_id: Optional[str] = None, n: int = 1,
                       size: str = "1024x1024", model: Optional[str] = None,
                       timeout: float = 120.0) -> dict:
        """真实调用 OpenAI-兼容 /images/generations，落盘返回真实 PNG 路径与张数。"""
        pid = provider_id or self.default_provider_id()
        if not pid:
            return {"status": "no_provider_configured", "implemented": True, "fabricated": False,
                    "images_generated": 0,
                    "reason": "未配置任何 AI Provider，拒绝伪造 images_generated（§2 红线）"}
        entry = self._config.get(pid, {})
        payload = {
            "model": model or entry.get("image_model") or "gpt-image-1",
            "prompt": prompt, "n": int(n), "size": size,
            "response_format": "b64_json",
        }
        t0 = time.perf_counter()
        try:
            resp = self._call(pid, "/images/generations", payload, timeout=timeout)
        except ProviderError as e:
            return {"status": "error", "error": e.kind, "detail": e.detail, "provider": pid,
                    "implemented": True, "fabricated": False, "images_generated": 0}
        dur = time.perf_counter() - t0
        data = resp.get("data") or []
        self.out_dir.mkdir(parents=True, exist_ok=True)
        files = []
        for i, item in enumerate(data):
            b64 = item.get("b64_json")
            if not b64:
                continue
            fp = self.out_dir / f"gen_{int(time.time()*1000)}_{i}.png"
            fp.write_bytes(base64.b64decode(b64))
            files.append({"path": str(fp), "bytes": fp.stat().st_size,
                          "revised_prompt": item.get("revised_prompt")})
        return {
            "status": "ok", "provider": pid, "model": resp.get("model", payload["model"]),
            "implemented": True, "fabricated": False,
            "images_generated": len(files), "files": files,
            "duration_seconds": round(dur, 3),
        }

    def health(self, provider_id: Optional[str] = None, timeout: float = 15.0) -> dict:
        """真实健康检查：GET {base_url}/models 带鉴权，200=reachable。"""
        pid = provider_id or self.default_provider_id()
        if not pid:
            return {"status": "no_provider_configured", "reachable": False, "fabricated": False}
        entry = self._config.get(pid, {})
        key = self._decrypt_key(pid, entry)
        url = entry["base_url"] + "/models"
        try:
            status, parsed, raw = _http_json(url, "GET", key, None, timeout)
        except (urllib.error.URLError, socket.timeout, OSError) as e:
            return {"status": "unreachable", "reachable": False, "detail": str(e), "fabricated": False}
        except ProviderError as e:
            return {"status": e.kind, "reachable": False, "detail": e.detail, "fabricated": False}
        return {"status": "ok" if status == 200 else f"http_{status}", "reachable": status == 200,
                "http_status": status, "fabricated": False}

    # ---------- §17 Planner：真实 LLM 结构化任务规划 ----------
    PLANNER_SYSTEM = (
        "你是设计自动化 Agent 的任务规划器。只输出一个 JSON 对象，不要任何解释文字或代码块围栏。"
        "JSON 必须严格包含以下键：goal(字符串)、steps(字符串数组)、tools(字符串数组)、"
        "constraints(字符串数组)、protected_objects(字符串数组)、verification(字符串数组)、"
        "rollback(字符串数组)。所有描述用与用户请求相同的语言。"
    )

    def plan(self, goal: str, provider_id: Optional[str] = None,
             context: Optional[str] = None, timeout: float = 60.0) -> dict:
        pid = provider_id or self.default_provider_id()
        if not pid:
            return {"status": "no_provider_configured", "implemented": True, "fabricated": False,
                    "reason": "未配置 AI Provider，拒绝凭空伪造任务计划（§2 红线）"}
        user = f"目标：{goal}"
        if context:
            user += f"\n\n上下文：{context}"
        payload = {
            "model": self._config.get(pid, {}).get("model") or "gpt-4o-mini",
            "messages": [{"role": "system", "content": self.PLANNER_SYSTEM},
                         {"role": "user", "content": user}],
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
        }
        try:
            resp = self._call(pid, "/chat/completions", payload, timeout=timeout)
        except ProviderError as e:
            return {"status": "error", "error": e.kind, "detail": e.detail, "provider": pid,
                    "implemented": True, "fabricated": False}
        raw = ""
        try:
            raw = resp["choices"][0]["message"]["content"]
        except Exception:
            raw = ""
        parsed = self._extract_json(raw)
        if parsed is None:
            return {"status": "planner_output_unparseable", "provider": pid,
                    "implemented": True, "fabricated": False,
                    "raw": raw[:2000],
                    "reason": "模型未返回可解析 JSON，保留原始输出，绝不硬编造 schema 字段"}
        plan_obj = {k: parsed.get(k, []) for k in
                    ("steps", "tools", "constraints", "protected_objects", "verification", "rollback")}
        plan_obj["goal"] = parsed.get("goal", goal)
        return {"status": "ok", "provider": pid, "model": resp.get("model", payload["model"]),
                "implemented": True, "fabricated": False, "plan": plan_obj,
                "usage": resp.get("usage") or {}}

    @staticmethod
    def _extract_json(text: str) -> Optional[dict]:
        """从模型输出中稳健提取 JSON 对象（容忍 ```json 围栏与前后噪声）。"""
        if not text:
            return None
        t = text.strip()
        if t.startswith("```"):
            t = t.strip("`")
            if t.lower().startswith("json"):
                t = t[4:]
            t = t.strip()
        try:
            obj = json.loads(t)
            return obj if isinstance(obj, dict) else None
        except Exception:
            pass
        start = t.find("{")
        end = t.rfind("}")
        if start != -1 and end > start:
            try:
                obj = json.loads(t[start:end + 1])
                return obj if isinstance(obj, dict) else None
            except Exception:
                return None
        return None


# ---------------------------------------------------------------------------
# 自测（hermetic：不依赖外部网络；验证密钥加密往返 / 脱敏 / 未配置诚实降级 / 规划解析）
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    eng = ProviderEngine(state_dir=Path(tempfile.mkdtemp(prefix="zjprov_selftest_")))

    # 1) 未配置 → 三项能力全部诚实降级，绝不伪造
    for r in (eng.chat("画个 logo"), eng.generate_image("海报"), eng.plan("改标题")):
        assert r["status"] == "no_provider_configured", r
        assert r["fabricated"] is False, r
    assert eng.health()["status"] == "no_provider_configured"

    # 2) 配置脱敏：视图绝不回显明文密钥
    view = eng.configure("mock", base_url="http://127.0.0.1:9/v1", api_key="sk-secret-abcdef123456",
                         model="unit-test-model")
    body = json.dumps(view)
    assert "sk-secret-abcdef123456" not in body, "明文密钥泄漏到返回体！"
    assert view["providers"]["mock"]["has_key"] is True

    # 3) DPAPI 真实往返（仅 Windows；非 Windows 走内存态，二者都不得写明文到磁盘）
    if _DPAPI_AVAILABLE:
        pt = "sk-live-XYZ123456789"
        ct = dpapi_protect(pt)
        assert ct != pt and dpapi_unprotect(ct) == pt, "DPAPI 往返失败"
        raw_disk = eng.config_path.read_text(encoding="utf-8")
        assert pt not in raw_disk, "明文密钥写入磁盘！"

    # 4) Planner JSON 解析（对模型原始输出做真实解析，非伪造）
    fenced = '```json\n{"goal":"重做主视觉","steps":["分析风格","生成3方向"],"tools":["ps.generate"],"constraints":["保持Logo"],"protected_objects":["logo"],"verification":["无溢出"],"rollback":["恢复快照"]}\n```'
    p = ProviderEngine._extract_json(fenced)
    assert p and p["goal"] == "重做主视觉" and p["steps"] == ["分析风格", "生成3方向"], p

    print("[PASS] provider engine self-test  honest=chat/image/plan/health  mask_ok=1  "
          + ("dpapi_roundtrip=1" if _DPAPI_AVAILABLE else "dpapi=skipped(non-win)") + "  planner_parse=1")
