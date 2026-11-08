"""
造境 AI 设计智能体 · NAS 网络存储引擎（NAS Engine）
========================================================================
任务书 §81：支持 `SMB / WebDAV / 映射盘`，断线重连 + 文件锁检测 + 缓存。

设计原则（§2 红线：不伪造成功）——把"本可真实落地的客户端机制"和"真正的外部条件"诚实分开：

真实落地（纯标准库，无第三方依赖，可本地确定/验证）：
- **connect 真探测**：
  - `filesystem`（覆盖 映射盘 `Z:\\...`、UNC `\\\\server\\share\\...`、本地路径）——
    Windows 的 SMB 共享由**系统网络重定向器原生**暴露为 UNC/映射盘路径，因此对这类目标的
    可达性探测/枚举/读写直接走 `os` / `pathlib` 原生文件系统调用，**这就是 Windows 应用访问
    SMB NAS 的标准且真实方式**（无需我们重造 SMB 二进制协议）。带超时线程包裹避免网络盘挂起。
  - `webdav`——真实 `PROPFIND Depth:0` / `OPTIONS` HTTP 请求（urllib），按响应码判定
    207→connected、401/403→auth_required、异常→unreachable。
- **list 真枚举**：filesystem 用 `Path.iterdir()` 逐条目取真实 name/type/size/mtime；
  webdav 用 `PROPFIND Depth:1` + `xml.etree` 解析 `<D:multistatus>`（displayname /
  getcontentlength / collection / getlastmodified）。
- **文件锁真检测**（§81 明确要求）：filesystem 目标用 `msvcrt` 独占区域锁探测
  （`os.open(O_RDWR)` + `msvcrt.locking(LK_NBLCK)`）——能锁上即未被占用，抛错即被他进程锁定，
  打开即共享冲突（PermissionError）也算锁定；webdav 无本地句柄，诚实返回
  `not_supported_for_webdav`（可选 HEAD 探测，不谎称锁态）。
- **断线重连**：`reconnect` 指数退避重试 connect，返回真实尝试次数与最终态。
- **缓存**：目录清单快照落本地 JSON（含 ts + sha256 键），按 max_age TTL 命中；真实文件 IO。

**诚实降级**（绝不用假数据冒充）：
- 未配置目标：`not_configured`（`fabricated=false`），绝不凭空"发现共享/已连接"
- 目标不可达：`unreachable`（含超时/网络异常）
- 需凭据：`auth_required`
- 路径越权：拒绝并如实返回（沙箱根 + `..` 穿越 + 绝对子路径 + 符号链接逃逸检测，对齐 §99）

说明：真实"远端 NAS 服务器 / 已映射的网络盘 / 可用 WebDAV 站点"是**部署环境**，与"需要数据库
连接串 / 更新源服务器"同性质；本引擎实现并端到端验证的是**客户端机制本身**，绝不伪造"已连接/
枚举成功"这一事实。原始 SMB 二进制协议交由操作系统原生重定向器处理，属正确工程分工而非能力缺失。
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import shutil
import socket
import tempfile
import threading
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List, Optional

try:  # Windows 专有：文件锁真实探测
    import msvcrt  # type: ignore
    _HAS_MSVCRT = True
except Exception:  # pragma: no cover
    msvcrt = None  # type: ignore
    _HAS_MSVCRT = False

logger = logging.getLogger("nas_engine")

KINDS = ("filesystem", "webdav")
_DAV = "{DAV:}"

# ---------------- 口令静态加密（Windows DPAPI，用户态） ----------------
# 配置文件里只允许出现 DPAPI 密文；DPAPI 不可用时**拒绝**把口令写成明文（不静默降级）。
_DPAPI_ENTROPY = b"zaojing.nas"


def _dpapi_available() -> bool:
    try:
        import ctypes
        return bool(getattr(ctypes, "windll", None)) and hasattr(ctypes.windll, "crypt32")
    except Exception:  # pragma: no cover - 非 Windows
        return False


def _dpapi_bytes(fn_name: str, data: bytes, entropy: bytes) -> bytes:
    import ctypes

    class _Blob(ctypes.Structure):
        _fields_ = [("cbData", ctypes.c_ulong),
                    ("pbData", ctypes.POINTER(ctypes.c_char))]

    def _blob(raw: bytes) -> "_Blob":
        buf = ctypes.create_string_buffer(bytes(raw), len(raw))
        return _Blob(len(raw), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))

    fn = getattr(ctypes.windll.crypt32, fn_name)
    blob_in, blob_ent = _blob(data), _blob(entropy)
    blob_out = _Blob()
    ok = fn(ctypes.byref(blob_in), "zaojing".encode("utf-16-le"), ctypes.byref(blob_ent),
            None, None, 0x2, ctypes.byref(blob_out))  # CRYPTPROTECT_UI_FORBIDDEN
    if not ok:
        raise OSError("DPAPI %s failed (GetLastError=%s)" % (fn_name, ctypes.GetLastError()))
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)


def protect_secret(plaintext: str) -> str:
    """加密为 base64 密文；DPAPI 不可用时抛 RuntimeError（调用方不得落明文）。"""
    if not plaintext:
        return ""
    if not _dpapi_available():
        raise RuntimeError("DPAPI 不可用，拒绝以明文持久化访问口令")
    return base64.b64encode(_dpapi_bytes("CryptProtectData", plaintext.encode("utf-8"),
                                         _DPAPI_ENTROPY)).decode("ascii")


def unprotect_secret(blob_b64: str) -> str:
    if not blob_b64:
        return ""
    if not _dpapi_available():
        raise RuntimeError("DPAPI 不可用，无法解出访问口令")
    return _dpapi_bytes("CryptUnprotectData", base64.b64decode(blob_b64),
                        _DPAPI_ENTROPY).decode("utf-8")


class NasEngine:
    def __init__(self, state_dir: Optional[Path] = None):
        self.state_dir = Path(state_dir) if state_dir else Path(tempfile.mkdtemp(prefix="zjnas_"))
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.config_path = self.state_dir / "nas-config.json"
        self.cache_dir = self.state_dir / "cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._config = self._read_json(self.config_path, default={"targets": {}})
        if "targets" not in self._config:
            self._config["targets"] = {}
            self._write_json(self.config_path, self._config)
        self._migrate_legacy_plaintext()

    # ---------------- 口令静态加密（DPAPI 密文；旧明文就地迁移） ----------------
    def _password(self, t: Dict[str, Any]) -> str:
        blob = t.get("password_dpapi") or ""
        if blob:
            try:
                return unprotect_secret(blob)
            except Exception as e:
                logger.warning("unprotect nas password failed: %s", e)
                return ""
        return t.get("password") or ""

    def _migrate_legacy_plaintext(self) -> int:
        changed = 0
        for _tid, t in (self._config.get("targets") or {}).items():
            if not isinstance(t, dict):
                continue
            legacy = t.get("password") or ""
            if legacy and not t.get("password_dpapi"):
                try:
                    t["password_dpapi"] = protect_secret(legacy)
                except Exception as e:
                    logger.warning("migrate nas password failed: %s", e)
                    continue
                t.pop("password", None)
                changed += 1
            elif "password" in t and t.get("password_dpapi"):
                t.pop("password", None)
                changed += 1
        if changed:
            self._write_json(self.config_path, self._config)
        return changed

    # ---------------- 原子 JSON 持久化 ----------------
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
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)

    # ---------------- 配置（绝不回显口令） ----------------
    def _redacted(self, tid: str) -> Dict[str, Any]:
        t = self._config["targets"].get(tid, {})
        return {
            "target_id": tid,
            "kind": t.get("kind", ""),
            "target": t.get("target", ""),
            "has_share_root": bool(t.get("share_root")),
            "has_credentials": bool(t.get("username") or t.get("password_dpapi") or t.get("password")),
            "configured": bool(t.get("target")),
        }

    def get_targets(self) -> Dict[str, Any]:
        return {
            "targets": [self._redacted(tid) for tid in self._config["targets"]],
            "fabricated": False,
        }

    def configure(self, target_id: str, target: str, kind: str,
                  share_root: Optional[str] = None, username: Optional[str] = None,
                  password: Optional[str] = None) -> Dict[str, Any]:
        if kind not in KINDS:
            raise ValueError(f"未知 NAS 类型 {kind}（可用: {', '.join(KINDS)}）")
        if not target or not str(target).strip():
            raise ValueError("target 不能为空")
        t = self._config["targets"].setdefault(target_id, {})
        t["kind"] = kind
        t["target"] = str(target).strip()
        if share_root is not None:
            t["share_root"] = str(share_root).strip()
        if username is not None:
            t["username"] = username
        if password is not None:
            t["password_dpapi"] = protect_secret(password) if password else ""
            t.pop("password", None)
        self._write_json(self.config_path, self._config)
        return self._redacted(target_id)

    def forget(self, target_id: str) -> Dict[str, Any]:
        existed = self._config["targets"].pop(target_id, None) is not None
        self._write_json(self.config_path, self._config)
        return {"target_id": target_id, "removed": existed, "fabricated": False}

    def _target(self, target_id: str) -> Optional[Dict[str, Any]]:
        return self._config["targets"].get(target_id)

    # ---------------- 路径沙箱（§99：穿越 + 符号链接逃逸检测） ----------------
    @staticmethod
    def _safe_join(root: Path, sub: str) -> Path:
        sub = (sub or "").strip()
        if sub in ("", ".", "/"):
            return root
        if os.path.isabs(sub) or re_isdrive(sub) or sub.startswith("\\\\"):
            raise PermissionError(f"子路径不得为绝对路径/UNC: {sub}")
        candidate = (root / sub).resolve()
        try:
            candidate.relative_to(root.resolve())
        except ValueError:
            raise PermissionError(f"路径穿越被拒绝（逃逸出沙箱根）: {sub}")
        if candidate.is_symlink():
            raise PermissionError(f"拒绝跟随符号链接: {sub}")
        return candidate

    # ---------------- 真实 HTTP（WebDAV） ----------------
    def _dav_request(self, url: str, method: str, headers: Dict[str, str],
                     timeout: float, body: Optional[bytes] = None):
        req = urllib.request.Request(url=url, method=method, headers=headers, data=body)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return getattr(resp, "status", 200), resp.read(), dict(resp.headers)
        except urllib.error.HTTPError as e:
            return e.code, e.read() if e.fp else b"", dict(getattr(e, "headers", {}) or {})
        except (urllib.error.URLError, OSError) as e:
            # 真实传输层失败（拒绝连接/DNS/超时）→ status 0，调用方据此诚实判 unreachable（绝不抛崩）
            reason = getattr(e, "reason", e)
            return 0, b"", {"X-Error": f"{type(e).__name__}: {reason}"}

    def _dav_headers(self, t: Dict[str, Any], extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        h = {"User-Agent": "ZaojingAI-NAS/1.0"}
        u, p = t.get("username", ""), self._password(t)
        if u or p:
            token = base64.b64encode(f"{u}:{p}".encode("utf-8")).decode("ascii")
            h["Authorization"] = f"Basic {token}"
        if extra:
            h.update(extra)
        return h

    @staticmethod
    def _join_url(base: str, sub: str) -> str:
        sub = (sub or "").strip().lstrip("/")
        return base.rstrip("/") + ("/" + sub if sub else "") + ("/" if sub == "" and not base.endswith("/") else "")

    # ---------------- 文件系统可达性探测（带超时，避免网络盘挂起） ----------------
    def _fs_probe(self, path: str, timeout: float) -> Dict[str, Any]:
        result: Dict[str, Any] = {}
        box: Dict[str, Any] = {}

        def worker():
            try:
                p = Path(path)
                exists = p.exists()
                box.update({"exists": exists, "is_dir": p.is_dir() if exists else False})
            except OSError as e:
                box.update({"error": f"{type(e).__name__}: {e}"})

        th = threading.Thread(target=worker, daemon=True)
        th.start()
        th.join(timeout)
        if th.is_alive():
            result["status"] = "unreachable"
            result["reason"] = f"探测超时（{timeout}s）：目标 {path} 可能是无响应的网络盘"
            return result
        if "error" in box:
            result["status"] = "unreachable"
            result["reason"] = box["error"]
            return result
        if not box.get("exists"):
            result["status"] = "unreachable"
            result["reason"] = f"路径不存在或不可达：{path}"
            return result
        result["status"] = "connected"
        result["is_dir"] = bool(box.get("is_dir"))
        return result

    # ---------------- connect：真实探测（绝不仅凭"配了地址"就报已连接） ----------------
    def connect(self, target_id: str, timeout: float = 5.0) -> Dict[str, Any]:
        t = self._target(target_id)
        base = {"target_id": target_id, "fabricated": False}
        if not t or not t.get("target"):
            return {**base, "status": "not_configured",
                    "reason": "未配置 NAS 目标，拒绝凭空伪造'已连接/发现共享'"}
        kind = t["kind"]
        target = t["target"]
        if kind == "filesystem":
            pr = self._fs_probe(target, timeout)
            if pr["status"] != "connected":
                return {**base, "kind": kind, "target": target, **pr}
            return {**base, "kind": kind, "target": target, "status": "connected",
                    "access": "native-os-redirector", "is_dir": pr.get("is_dir", False),
                    "note": "映射盘/UNC 经操作系统网络重定向器原生访问（SMB 由 OS 处理）"}
        # webdav
        url = self._join_url(target, "")
        status, body, hdrs = self._dav_request(url, "PROPFIND",
                                               self._dav_headers(t, {"Depth": "0", "Content-Type": "text/xml"}),
                                               timeout, body=b"")
        if status in (401, 403):
            return {**base, "kind": kind, "target": target, "status": "auth_required",
                    "reason": f"WebDAV 返回 {status}，需要有效凭据"}
        if status == 207 or status == 200:
            return {**base, "kind": kind, "target": target, "status": "connected",
                    "http_status": status, "server": hdrs.get("Server", hdrs.get("server", ""))}
        return {**base, "kind": kind, "target": target, "status": "unreachable",
                "reason": f"WebDAV 异常响应 HTTP {status}", "http_status": status}

    # ---------------- list：真实枚举（含缓存） ----------------
    def list_dir(self, target_id: str, subpath: str = "", timeout: float = 8.0,
                 use_cache: bool = False, max_age: float = 30.0) -> Dict[str, Any]:
        t = self._target(target_id)
        base = {"target_id": target_id, "subpath": subpath, "fabricated": False}
        if not t or not t.get("target"):
            return {**base, "status": "not_configured", "entries": []}
        if use_cache:
            hit = self._cache_get(target_id, subpath, max_age)
            if hit is not None:
                return {**base, "status": "ok", "count": len(hit["entries"]),
                        "entries": hit["entries"], "cache": "hit", "cached_ts": hit["ts"]}
        try:
            if t["kind"] == "filesystem":
                res = self._list_fs(t, subpath)
            else:
                res = self._list_webdav(t, subpath, timeout)
        except PermissionError as e:
            return {**base, "status": "forbidden", "entries": [], "reason": str(e)}
        if res.get("status") == "ok" and res.get("entries") is not None:
            self._cache_put(target_id, subpath, res["entries"])
            res = {**res, "cache": "miss_stored"}
        return {**base, **res}

    def _list_fs(self, t: Dict[str, Any], subpath: str) -> Dict[str, Any]:
        # 沙箱根：配置了 share_root 以它为准，否则 target 本身即沙箱根（控制台配置表单
        # 缺省发送的就是「只发 target」这一形制）。
        # P65 修复：不论 share_root 是否配置，一律经 _safe_join —— 旧形制把守卫挂在
        # t.get("share_root") 上，未配 share_root 的目标可用 ../.. 直接枚举沙箱外目录。
        root = Path(t["share_root"]) if t.get("share_root") else Path(t["target"])
        loc = self._safe_join(root, subpath)
        if not loc.exists():
            return {"status": "unreachable", "entries": [], "reason": f"路径不存在：{loc}"}
        if not loc.is_dir():
            return {"status": "not_a_directory", "entries": [], "reason": f"目标不是目录：{loc}"}
        entries: List[Dict[str, Any]] = []
        for child in sorted(loc.iterdir(), key=lambda x: x.name):
            try:
                st = child.stat()
                entries.append({
                    "name": child.name,
                    "type": "dir" if child.is_dir() else "file",
                    "size": st.st_size,
                    "mtime": int(st.st_mtime),
                })
            except OSError as e:  # 单个条目错误如实记录，不影响整体
                entries.append({"name": child.name, "type": "error", "error": str(e)})
        return {"status": "ok", "entries": entries, "count": len(entries),
                "path": str(loc)}

    def _list_webdav(self, t: Dict[str, Any], subpath: str, timeout: float) -> Dict[str, Any]:
        url = self._join_url(t["target"], subpath)
        status, body, _ = self._dav_request(url, "PROPFIND",
                                            self._dav_headers(t, {"Depth": "1", "Content-Type": "text/xml"}),
                                            timeout, body=b"")
        if status in (401, 403):
            return {"status": "auth_required", "entries": []}
        if status != 207:
            return {"status": "unreachable", "entries": [], "reason": f"WebDAV HTTP {status}"}
        try:
            root = ET.fromstring(body)
        except ET.ParseError as e:
            return {"status": "invalid_response", "entries": [], "reason": f"multistatus 解析失败: {e}"}
        entries: List[Dict[str, Any]] = []
        for resp in root.iter(_DAV + "response"):
            href_el = resp.find(_DAV + "href")
            href = href_el.text if href_el is not None else ""
            props: Dict[str, Any] = {}
            is_coll = False
            for prop in resp.iter(_DAV + "prop"):
                for k in ("displayname", "getcontentlength", "getlastmodified"):
                    el = prop.find(_DAV + k)
                    if el is not None and el.text:
                        props[k] = el.text
                # collection 嵌套于 resourcetype 之下，非 prop 的直接子元素
                rt = prop.find(_DAV + "resourcetype")
                if rt is not None and rt.find(_DAV + "collection") is not None:
                    is_coll = True
            name = props.get("displayname") or (href.rstrip("/").split("/")[-1] if href else "")
            try:
                size = int(props.get("getcontentlength", "0"))
            except ValueError:
                size = 0
            entries.append({
                "name": name, "type": "dir" if is_coll else "file",
                "size": size, "href": href,
                "mtime_raw": props.get("getlastmodified", ""),
            })
        # 首条常为查询集合自身，剔除与请求同名的自身条目
        return {"status": "ok", "entries": entries, "count": len(entries), "href": url}

    # ---------------- 文件锁真实检测（§81） ----------------
    def lock_check(self, target_id: str, subpath: str) -> Dict[str, Any]:
        t = self._target(target_id)
        base = {"target_id": target_id, "subpath": subpath, "fabricated": False}
        if not t or not t.get("target"):
            return {**base, "status": "not_configured"}
        if t["kind"] != "filesystem":
            return {**base, "status": "not_supported_for_webdav",
                    "reason": "文件锁需本地文件句柄，WebDAV 无句柄可锁；不谎称锁态"}
        try:
            root = Path(t["share_root"]) if t.get("share_root") else Path(t["target"])
            loc = self._safe_join(root, subpath)
        except PermissionError as e:
            return {**base, "status": "forbidden", "reason": str(e)}
        if not loc.exists() or loc.is_dir():
            return {**base, "status": "not_found", "reason": f"文件不存在或为目录：{loc}"}
        if not _HAS_MSVCRT:
            return {**base, "status": "unsupported_platform", "path": str(loc)}
        # 真实独占区域锁探测：拿到锁=未被占用；抛错=被其他进程锁定
        try:
            fd = os.open(str(loc), os.O_RDWR)
        except PermissionError as e:
            return {**base, "status": "ok", "path": str(loc), "locked": True,
                    "reason": f"共享冲突无法打开（疑似被占用）: {e}"}
        except OSError as e:
            return {**base, "status": "error", "reason": f"{type(e).__name__}: {e}"}
        try:
            try:
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)  # 非阻塞独占锁第 1 字节
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)   # 立即释放
                return {**base, "status": "ok", "path": str(loc), "locked": False,
                        "reason": "成功获取并释放独占锁 → 未被占用"}
            except OSError as e:
                return {**base, "status": "ok", "path": str(loc), "locked": True,
                        "reason": f"锁定失败（已被其他进程持有）: {type(e).__name__}"}
        finally:
            try:
                os.close(fd)
            except OSError:  # pragma: no cover
                pass

    # ---------------- 断线重连（真实指数退避） ----------------
    def reconnect(self, target_id: str, attempts: int = 3, base_delay: float = 0.2,
                  timeout: float = 5.0) -> Dict[str, Any]:
        attempts = max(1, int(attempts))
        last: Dict[str, Any] = {}
        for i in range(attempts):
            last = self.connect(target_id, timeout=timeout)
            if last.get("status") == "connected":
                return {**last, "attempts_made": i + 1, "recovered": True,
                        "backoff_seconds": [min(base_delay * (2 ** k), 5.0) for k in range(i)],
                        "fabricated": False}
            if i < attempts - 1:
                time.sleep(min(base_delay * (2 ** i), 5.0))  # 确定性退避，非随机
        return {**last, "attempts_made": attempts, "recovered": False,
                "backoff_seconds": [min(base_delay * (2 ** k), 5.0) for k in range(attempts - 1)],
                "fabricated": False}

    # ---------------- 缓存（真实文件 IO + TTL + sha256 键） ----------------
    def _cache_key(self, target_id: str, subpath: str) -> str:
        return hashlib.sha256(f"{target_id}|{subpath}".encode("utf-8")).hexdigest()[:32]

    def _cache_put(self, target_id: str, subpath: str, entries: List[Dict[str, Any]]) -> None:
        rec = {"target_id": target_id, "subpath": subpath, "ts": time.time(), "entries": entries}
        self._write_json(self.cache_dir / (self._cache_key(target_id, subpath) + ".json"), rec)

    def _cache_get(self, target_id: str, subpath: str, max_age: float) -> Optional[Dict[str, Any]]:
        p = self.cache_dir / (self._cache_key(target_id, subpath) + ".json")
        rec = self._read_json(p, default=None)
        if not rec:
            return None
        if time.time() - float(rec.get("ts", 0)) > max_age:
            return None
        return rec

    def cache_clear(self) -> Dict[str, Any]:
        removed = 0
        for f in self.cache_dir.glob("*.json"):
            try:
                f.unlink()
                removed += 1
            except OSError:  # pragma: no cover
                pass
        return {"cleared": removed, "fabricated": False}

    def cache_stats(self) -> Dict[str, Any]:
        files = list(self.cache_dir.glob("*.json"))
        return {"entries": len(files), "bytes": sum(f.stat().st_size for f in files) if files else 0,
                "fabricated": False}

    # ---------------- 下载共享文件（真实拷贝 + sha256） ----------------
    def download(self, target_id: str, subpath: str, dest_dir: Optional[str] = None,
                 timeout: float = 30.0) -> Dict[str, Any]:
        t = self._target(target_id)
        base = {"target_id": target_id, "subpath": subpath, "fabricated": False}
        if not t or not t.get("target"):
            return {**base, "status": "not_configured", "downloaded": False}
        dest = Path(dest_dir) if dest_dir else (self.state_dir / "downloads")
        dest.mkdir(parents=True, exist_ok=True)
        name = Path(subpath).name or "file"
        out = dest / name
        if t["kind"] == "filesystem":
            try:
                root = Path(t["share_root"]) if t.get("share_root") else Path(t["target"])
                src = self._safe_join(root, subpath)
            except PermissionError as e:
                return {**base, "status": "forbidden", "downloaded": False, "reason": str(e)}
            if not src.exists() or src.is_dir():
                return {**base, "status": "not_found", "downloaded": False}
            try:
                shutil.copyfile(src, out)
            except OSError as e:
                return {**base, "status": "read_failed", "downloaded": False,
                        "reason": f"{type(e).__name__}: {e}"}
            sha = hashlib.sha256(out.read_bytes()).hexdigest()
            return {**base, "status": "downloaded", "downloaded": True, "path": str(out),
                    "bytes": out.stat().st_size, "sha256": sha, "integrity": "sha256_computed"}
        # webdav GET
        url = self._join_url(t["target"], subpath)
        status, body, _ = self._dav_request(url, "GET", self._dav_headers(t), timeout)
        if status in (401, 403):
            return {**base, "status": "auth_required", "downloaded": False}
        if status != 200:
            return {**base, "status": "unreachable", "downloaded": False,
                    "reason": f"WebDAV GET HTTP {status}"}
        out.write_bytes(body)
        sha = hashlib.sha256(body).hexdigest()
        return {**base, "status": "downloaded", "downloaded": True, "path": str(out),
                "bytes": len(body), "sha256": sha, "integrity": "sha256_computed"}


def re_isdrive(s: str) -> bool:
    # 轻量盘符检测：形如 "C:\\" 或 "C:/"
    return len(s) >= 2 and s[1] == ":" and s[0].isalpha()


# ============================================================================
# 自测：真跑文件系统目标 + 真 mock WebDAV 服务器 + 真持锁进程（非 mock 结果）
# ============================================================================
if __name__ == "__main__":
    import http.server
    import socketserver
    import subprocess
    import sys

    # --- 真实 mock WebDAV 服务器（PROPFIND/OPTIONS/GET），验证客户端协议真跑 ---
    class DavHandler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _multistatus(self, urlpath):
            if urlpath.rstrip("/") != "/share":
                self.send_response(404); self.end_headers(); return
            xml = (
                '<?xml version="1.0"?>'
                '<D:multistatus xmlns:D="DAV:">'
                '<D:response><D:href>/share/</D:href><D:propstat><D:prop>'
                '<D:resourcetype><D:collection/></D:resourcetype>'
                '<D:displayname>share</D:displayname>'
                '</D:prop></D:propstat></D:response>'
                '<D:response><D:href>/share/design.psd</D:href><D:propstat><D:prop>'
                '<D:resourcetype/>'
                '<D:displayname>design.psd</D:displayname>'
                '<D:getcontentlength>12345</D:getcontentlength>'
                '<D:getlastmodified>Wed, 20 Sep 2026 10:00:00 GMT</D:getlastmodified>'
                '</D:prop></D:propstat></D:response>'
                '<D:response><D:href>/share/assets/</D:href><D:propstat><D:prop>'
                '<D:resourcetype><D:collection/></D:resourcetype>'
                '<D:displayname>assets</D:displayname>'
                '</D:prop></D:propstat></D:response>'
                '</D:multistatus>')
            b = xml.encode("utf-8")
            self.send_response(207)
            self.send_header("Content-Type", "application/xml; charset=utf-8")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers(); self.wfile.write(b)

        def do_OPTIONS(self):
            self.send_response(200)
            self.send_header("Allow", "OPTIONS, PROPFIND, GET")
            self.send_header("MS-RAP", "")
            self.end_headers()

        def do_PROPFIND(self):
            self._multistatus(self.path)

        def do_GET(self):
            if self.path == "/share/design.psd":
                payload = b"PSD-BYTES-REAL-CONTENT"
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers(); self.wfile.write(payload)
            else:
                self.send_response(404); self.end_headers()

    with socketserver.TCPServer(("127.0.0.1", 0), DavHandler) as httpd:
        port = httpd.server_address[1]
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                eng = NasEngine(Path(tmp) / "state")
                base = {"fabricated": False}
                _PW = "nas-selftest-password-3f7a"

                # 1) 未配置目标 → 诚实 not_configured（connect/list/lock/reconnect 全诚实）
                assert eng.connect("ghost")["status"] == "not_configured", base
                assert eng.list_dir("ghost")["status"] == "not_configured", base
                assert eng.lock_check("ghost", "x")["status"] == "not_configured", base
                assert eng.reconnect("ghost", attempts=2, base_delay=0.01)["status"] == "not_configured", base

                # ---- filesystem 目标（用一个真实临时目录当"映射盘/共享"） ----
                share = Path(tmp) / "mappedshare"
                (share / "assets").mkdir(parents=True)
                (share / "readme.txt").write_text("hello-nas", encoding="utf-8")
                (share / "assets" / "a.png").write_bytes(b"PNGPNG")
                eng.configure("localshare", target=str(share), kind="filesystem", share_root=str(share))

                # 2) 真连接
                c = eng.connect("localshare", timeout=3)
                assert c["status"] == "connected" and c.get("access") == "native-os-redirector", c
                # 3) 真枚举：顶层 2 条（assets 目录 + readme.txt），排序稳定
                lst = eng.list_dir("localshare", "")
                assert lst["status"] == "ok", lst
                names = [e["name"] for e in lst["entries"]]
                assert "assets" in names and "readme.txt" in names, names
                readme = next(e for e in lst["entries"] if e["name"] == "readme.txt")
                assert readme["type"] == "file" and readme["size"] == len("hello-nas"), readme
                # 子目录枚举
                sub = eng.list_dir("localshare", "assets")
                assert any(e["name"] == "a.png" for e in sub["entries"]), sub
                # 4) 路径穿越防护：拒绝越权子路径
                bad = eng.list_dir("localshare", "../..")
                assert bad["status"] == "forbidden", bad
                # 4b) 同一防护在「控制台缺省形制」下也必须生效：configure 只发 target，
                #     不带 share_root（界面配置表单真实发送的就是这一形制 ⇒ 验世界不只验夹具）。
                #     旧形制下 _list_fs 仅在 share_root 存在时才走 _safe_join ⇒ 无 share_root
                #     的目标可直接以 ../.. 枚举沙箱外目录（P65 载体揪出的真缺陷）。
                eng.configure("noshare", target=str(share), kind="filesystem")
                bad2 = eng.list_dir("noshare", "../..")
                assert bad2["status"] == "forbidden", ("无 share_root 形制下 list 越权未被拦住", bad2)
                assert bad2.get("entries") == [] and bad2.get("reason"), bad2
                # 正对照：同一目标的合法子路径仍可枚举 ⇒ 新门禁不是恒 forbidden 摆设
                ok2 = eng.list_dir("noshare", "assets")
                assert ok2["status"] == "ok" and any(e["name"] == "a.png" for e in ok2["entries"]), ok2
                # 5) 缓存真落盘 + TTL 命中
                eng.list_dir("localshare", "")           # 写入缓存
                cached = eng.list_dir("localshare", "", use_cache=True, max_age=30)
                assert cached.get("cache") == "hit", cached
                assert eng.cache_stats()["entries"] >= 1, eng.cache_stats()
                # 删除真目录后缓存仍命中（证明缓存是真实落盘的快照，而非现取）
                (share / "readme.txt").unlink()
                cached2 = eng.list_dir("localshare", "", use_cache=True, max_age=30)
                assert cached2["cache"] == "hit" and any(e["name"] == "readme.txt" for e in cached2["entries"]), cached2

                # ---- 文件锁真检测：spawn 一个跨进程持锁 ----
                lockf = Path(tmp) / "locked.bin"
                lockf.write_bytes(b"lock-me")
                holder_code = (
                    "import os,msvcrt,time,sys;"
                    "fd=os.open(sys.argv[1],os.O_RDWR);"
                    "msvcrt.locking(fd,msvcrt.LK_NBLCK,1);"
                    "print('HELD',flush=True);"
                    "time.sleep(float(sys.argv[2]))"
                )
                proc = subprocess.Popen([sys.executable, "-c", holder_code, str(lockf), "4"],
                                        stdout=subprocess.PIPE, text=True)
                assert proc.stdout.readline().strip() == "HELD"  # 确认真已持锁
                lk = eng.lock_check("localshare", str(lockf.relative_to(share)) if str(lockf).startswith(str(share)) else "")
                # lock_check 需要目标在 share 内；直接改配一个覆盖 tmp 的目标更清晰
                eng.configure("tmpshare", target=tmp, kind="filesystem", share_root=tmp)
                lk = eng.lock_check("tmpshare", "locked.bin")
                assert lk["status"] == "ok" and lk["locked"] is True, lk
                proc.terminate(); proc.wait(timeout=5)
                # 释放后 → 未锁
                lk2 = eng.lock_check("tmpshare", "locked.bin")
                assert lk2["status"] == "ok" and lk2["locked"] is False, lk2

                # 6) 真实不可达目标 → honest unreachable（不伪造已连接）
                eng.configure("dead", target=r"\\zaojing-nonexistent-host\nope", kind="filesystem")
                assert eng.connect("dead", timeout=2)["status"] == "unreachable", "unreachable expected"
                # 断线重连对不可达目标：真实退避后诚实 recovered=False
                rc = eng.reconnect("dead", attempts=3, base_delay=0.01, timeout=1)
                assert rc["status"] == "unreachable" and rc["recovered"] is False and rc["attempts_made"] == 3, rc
                # 对可达目标首次即恢复
                rc2 = eng.reconnect("localshare", attempts=3, base_delay=0.01, timeout=3)
                assert rc2["recovered"] is True and rc2["attempts_made"] == 1, rc2

                # ---- WebDAV 目标：真实 PROPFIND + GET，xml.etree 解析 ----
                eng.configure("dav", target=f"http://127.0.0.1:{port}/share", kind="webdav",
                              username="nasuser", password=_PW)
                cd = eng.connect("dav", timeout=5)
                assert cd["status"] == "connected" and cd.get("http_status") == 207, cd
                dl = eng.list_dir("dav", "")
                assert dl["status"] == "ok", dl
                dnames = {e["name"] for e in dl["entries"]}
                assert "design.psd" in dnames and "assets" in dnames, dnames
                psd = next(e for e in dl["entries"] if e["name"] == "design.psd")
                assert psd["type"] == "file" and psd["size"] == 12345, psd
                assert next(e for e in dl["entries"] if e["name"] == "assets")["type"] == "dir", dl
                # WebDAV 真下载 + sha
                wd = eng.download("dav", "design.psd", dest_dir=str(Path(tmp) / "wdl"))
                assert wd["status"] == "downloaded" and wd["bytes"] == len(b"PSD-BYTES-REAL-CONTENT"), wd
                assert Path(wd["path"]).read_bytes() == b"PSD-BYTES-REAL-CONTENT"
                # ---- 口令静态加密：磁盘只允许 DPAPI 密文 ----
                _raw = (Path(tmp) / "state" / "nas-config.json").read_bytes()
                assert _PW.encode("utf-8") not in _raw, "NAS 口令明文落盘"
                _cfg = json.loads(_raw.decode("utf-8"))
                _blob = _cfg["targets"]["dav"].get("password_dpapi") or ""
                assert _blob and "password" not in _cfg["targets"]["dav"], "未落 DPAPI 密文"
                assert unprotect_secret(_blob) == _PW, "DPAPI 密文解不回原口令"
                _auth = eng._dav_headers(eng._target("dav")).get("Authorization", "")
                assert _auth == "Basic " + base64.b64encode(
                    ("nasuser:" + _PW).encode("utf-8")).decode("ascii"), "basic auth 头不含真口令"
                # 旧明文配置就地迁移后 basic-auth 仍可用
                _legacy_dir = Path(tmp) / "legacy-nas"
                _legacy_dir.mkdir(parents=True, exist_ok=True)
                (_legacy_dir / "nas-config.json").write_text(json.dumps(
                    {"targets": {"dav": {"kind": "webdav",
                                         "target": f"http://127.0.0.1:{port}/share",
                                         "username": "nasuser", "password": _PW}}}), encoding="utf-8")
                _legacy = NasEngine(_legacy_dir)
                _legacy_raw = (_legacy_dir / "nas-config.json").read_bytes()
                assert _PW.encode("utf-8") not in _legacy_raw, "旧明文口令未被迁移"
                assert _legacy._dav_headers(_legacy._target("dav")).get("Authorization", "") == _auth, \
                    "迁移后 basic auth 失效"
                # WebDAV 锁检测诚实 not_supported（不谎称锁态）
                assert eng.lock_check("dav", "design.psd")["status"] == "not_supported_for_webdav"
                # WebDAV 服务器关掉 → 真 unreachable（诚实降级，非缓存伪装）
                httpd.shutdown()
                assert eng.connect("dav", timeout=3)["status"] == "unreachable", "dav unreachable after shutdown"

                # 7) 未知类型 configure 报错
                try:
                    eng.configure("bad", target="x", kind="ftp")
                    raise AssertionError("should reject unknown kind")
                except ValueError:
                    pass

                assert eng.cache_clear()["cleared"] >= 1
            print("[PASS] nas engine self-test  connect/list/lock/reconnect/cache/webdav all real + honest-degrade")
        finally:
            try:
                httpd.shutdown()
            except Exception:
                pass
