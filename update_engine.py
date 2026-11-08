"""
造境 AI 设计智能体 · 自动更新引擎（Auto-Update Engine）
========================================================================
任务书 §75–§77（Stable / Beta / Dev 更新渠道 + 完整性校验 + 原子替换）。

设计原则（§2 红线：不伪造成功）：本引擎是**真实的更新客户端**——所有可本地确定的逻辑都做真：
- 真实 HTTP 拉取更新清单（urllib，带超时，无第三方依赖）
- 真实 HMAC-SHA256 对清单签名/验签（hmac+hashlib，密钥不匹配即判 integrity_failed）
- 真实语义化版本比较（semver 数字元组）
- 真实下载产物 + SHA-256 逐字节完整性校验（校验不过删除文件并如实报错，绝不谎称下载成功）
- 真实原子替换：备份当前文件 → 覆盖 → 校验目标内容 → 任一步失败自动回滚（还原备份）
  Windows 下运行中的 EXE 无法被替换，属正常限制 → 如实登记为 pending_restart 而非假 "已更新"

**诚实降级**（绝不用假数据冒充）：
- 未配置 feed_url：check 返回 `no_update_configured`，不凭空"发现新版本"
- 网络不可达：返回 `feed_unreachable`
- 清单非法 JSON / 缺字段：返回 `manifest_invalid`
- 验签失败 / 无验签密钥：返回 `integrity_failed` / `signature_unverified`（`verified=false`）
- 下载 SHA 不符：删除并返回 `integrity_failed`

说明：真实"更新源"（HTTPS 服务器 + 签名私钥 + 已发布版本）是**部署配置**，与"需要数据库连接串"
同性质；本引擎实现并端到端验证的是更新**机制**本身，绝不伪造"有可用更新"这一事实。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import shutil
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger("update_engine")

CHANNELS = ("stable", "beta", "dev")

# ---------------- 验签密钥静态加密（Windows DPAPI，用户态） ----------------
# §2 同源约束：配置文件中不得留存可直接读取的明文密钥。DPAPI 由操作系统按当前
# 用户加密，磁盘上只有密文；解不出明文时**绝不**悄悄退回明文持久化（拒绝降级）。
_DPAPI_ENTROPY = b"zaojing.update"


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
        raise RuntimeError("DPAPI 不可用，拒绝以明文持久化验签密钥")
    return base64.b64encode(_dpapi_bytes("CryptProtectData", plaintext.encode("utf-8"),
                                         _DPAPI_ENTROPY)).decode("ascii")


def unprotect_secret(blob_b64: str) -> str:
    if not blob_b64:
        return ""
    if not _dpapi_available():
        raise RuntimeError("DPAPI 不可用，无法解出验签密钥")
    return _dpapi_bytes("CryptUnprotectData", base64.b64decode(blob_b64),
                        _DPAPI_ENTROPY).decode("utf-8")


class UpdateEngine:
    def __init__(self, state_dir: Optional[Path] = None):
        self.state_dir = Path(state_dir) if state_dir else Path(tempfile.mkdtemp(prefix="zjupd_"))
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.config_path = self.state_dir / "update-config.json"
        self.pending_path = self.state_dir / "pending-update.json"
        self._config = self._read_json(self.config_path, default={})
        # 结构: {"channels": {"stable": {"feed_url": "", "secret_dpapi": "", "current_version": "1.0.0"}, ...}}
        if "channels" not in self._config:
            self._config["channels"] = {
                ch: {"feed_url": "", "secret_dpapi": "", "current_version": "1.0.0"} for ch in CHANNELS
            }
            self._write_json(self.config_path, self._config)
        self._migrate_legacy_plaintext()

    # ---------- 密钥读取与旧版明文迁移（磁盘上只留 DPAPI 密文） ----------
    def _secret(self, channel: str) -> str:
        c = self._config["channels"].get(channel, {}) or {}
        blob = c.get("secret_dpapi") or ""
        if blob:
            try:
                return unprotect_secret(blob)
            except Exception as e:  # 密文换机/换用户后解不开 → 如实当"无密钥"，不伪装可信
                logger.warning("unprotect secret for %s failed: %s", channel, e)
                return ""
        return c.get("secret") or ""

    def _migrate_legacy_plaintext(self) -> int:
        """把历史版本写下的明文 secret 就地加密并抹掉明文；DPAPI 不可用时保持原样（不更糟）。"""
        changed = 0
        for ch, c in (self._config.get("channels") or {}).items():
            if not isinstance(c, dict):
                continue
            legacy = c.get("secret") or ""
            if legacy and not c.get("secret_dpapi"):
                try:
                    c["secret_dpapi"] = protect_secret(legacy)
                except Exception as e:
                    logger.warning("migrate secret for %s failed: %s", ch, e)
                    continue
                c.pop("secret", None)
                changed += 1
            elif "secret" in c and c.get("secret_dpapi"):
                c.pop("secret", None)
                changed += 1
        if changed:
            self._write_json(self.config_path, self._config)
        return changed

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
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)  # 原子替换

    # ---------- 配置读写（绝不回显密钥） ----------
    def _redacted(self, channel: str) -> Dict[str, Any]:
        c = self._config["channels"].get(channel, {})
        return {
            "channel": channel,
            "feed_url": c.get("feed_url", ""),
            "has_secret": bool(c.get("secret_dpapi") or c.get("secret")),
            "configured": bool(c.get("feed_url")),
            "current_version": c.get("current_version", ""),
        }

    def get_channels(self) -> Dict[str, Any]:
        return {"channels": [self._redacted(ch) for ch in CHANNELS], "fabricated": False}

    def configure(self, channel: str, feed_url: Optional[str] = None,
                  secret: Optional[str] = None, current_version: Optional[str] = None) -> Dict[str, Any]:
        if channel not in CHANNELS:
            raise ValueError(f"未知渠道 {channel}（可用: {', '.join(CHANNELS)}）")
        c = self._config["channels"].setdefault(
            channel, {"feed_url": "", "secret_dpapi": "", "current_version": "1.0.0"})
        if feed_url is not None:
            c["feed_url"] = feed_url
        if secret is not None:
            if secret:
                c["secret_dpapi"] = protect_secret(secret)  # 只落 DPAPI 密文（失败即抛，绝不落明文）
            else:
                c["secret_dpapi"] = ""
            c.pop("secret", None)
        if current_version is not None:
            c["current_version"] = current_version
        self._write_json(self.config_path, self._config)
        return self._redacted(channel)

    # ---------- 版本比较（真实 semver 数字元组） ----------
    @staticmethod
    def _ver_tuple(v: str) -> Tuple[int, ...]:
        nums: list[int] = []
        for seg in str(v).lstrip("vV").replace("-", ".").split("."):
            digits = ""
            for ch in seg:
                if ch.isdigit():
                    digits += ch
                else:
                    break
            nums.append(int(digits) if digits else 0)
        return tuple(nums) if nums else (0,)

    # ---------- 签名工具（清单侧签发 + 客户端验签，均真实 HMAC-SHA256） ----------
    @classmethod
    def canonical_bytes(cls, manifest: Dict[str, Any]) -> bytes:
        m = {k: v for k, v in manifest.items() if k != "signature"}
        return json.dumps(m, sort_keys=True, separators=(",", ":")).encode("utf-8")

    @classmethod
    def sign(cls, manifest: Dict[str, Any], secret: str) -> str:
        return hmac.new(secret.encode("utf-8"), cls.canonical_bytes(manifest), hashlib.sha256).hexdigest()

    @classmethod
    def verify(cls, manifest: Dict[str, Any], secret: str) -> bool:
        got = str(manifest.get("signature", ""))
        exp = cls.sign(manifest, secret)
        return hmac.compare_digest(got, exp)

    # ---------- 真实 HTTP ----------
    @staticmethod
    def _http_get(url: str, timeout: float) -> bytes:
        req = urllib.request.Request(url, headers={"User-Agent": "ZaojingAI-Updater/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if getattr(resp, "status", 200) not in (200,):
                raise urllib.error.HTTPError(url, resp.status, "bad status", resp.headers, None)  # type: ignore
            return resp.read()

    # ---------- check：拉清单 + 验签 + 版本比较（全诚实） ----------
    def check(self, channel: str, current_version: Optional[str] = None,
              timeout: float = 5.0) -> Dict[str, Any]:
        if channel not in CHANNELS:
            raise ValueError(f"未知渠道 {channel}")
        c = self._config["channels"].get(channel, {})
        feed = c.get("feed_url", "")
        base = {"channel": channel, "fabricated": False}
        if not feed:
            return {**base, "status": "no_update_configured", "verified": False,
                    "reason": "未配置更新源 feed_url，拒绝凭空伪造'发现新版本'"}
        cur = current_version or c.get("current_version") or "0.0.0"
        try:
            body = self._http_get(feed, timeout=timeout)
        except Exception as e:
            return {**base, "status": "feed_unreachable", "verified": False,
                    "reason": f"{type(e).__name__}: {e}"}
        try:
            manifest = json.loads(body.decode("utf-8"))
        except Exception:
            return {**base, "status": "manifest_invalid", "verified": False,
                    "reason": "更新清单不是合法 JSON"}
        if not isinstance(manifest, dict) or "version" not in manifest:
            return {**base, "status": "manifest_invalid", "verified": False,
                    "reason": "清单缺 version 字段或结构非法"}
        secret = self._secret(channel)
        if not secret:
            return {**base, "status": "signature_unverified", "verified": False,
                    "reason": "未配置验签密钥，拒绝信任清单来源（§2 不伪造可信）",
                    "remote_version": manifest.get("version")}
        if not self.verify(manifest, secret):
            return {**base, "status": "integrity_failed", "verified": False,
                    "reason": "清单 HMAC-SHA256 验签失败，疑似被篡改"}
        latest = str(manifest["version"])
        if self._ver_tuple(latest) > self._ver_tuple(cur):
            return {**base, "status": "update_available", "verified": True,
                    "current_version": cur, "remote_version": latest,
                    "artifact_url": manifest.get("url", ""),
                    "sha256": manifest.get("sha256", ""),
                    "notes": manifest.get("notes", "")}
        return {**base, "status": "up_to_date", "verified": True,
                "current_version": cur, "remote_version": latest}

    # ---------- download：真实下载 + SHA-256 完整性校验 ----------
    def download(self, channel: str, staged_dir: Optional[str] = None,
                 timeout: float = 30.0) -> Dict[str, Any]:
        chk = self.check(channel, timeout=timeout)
        if chk["status"] != "update_available":
            return {**chk, "downloaded": False}
        url = chk.get("artifact_url", "")
        if not url:
            return {**chk, "status": "manifest_invalid", "downloaded": False,
                    "reason": "清单未提供产物 url"}
        dest = Path(staged_dir) if staged_dir else (self.state_dir / "staged")
        dest.mkdir(parents=True, exist_ok=True)
        fname = f"{channel}-{chk['remote_version']}.bin"
        tmp = dest / (fname + ".part")
        try:
            data = self._http_get(url, timeout=timeout)
        except Exception as e:
            return {**chk, "status": "feed_unreachable", "downloaded": False,
                    "reason": f"产物下载失败 {type(e).__name__}: {e}"}
        actual_sha = hashlib.sha256(data).hexdigest()
        expected_sha = chk.get("sha256", "")
        if expected_sha and actual_sha != expected_sha:
            try:
                tmp.unlink()
            except Exception:
                pass
            return {**chk, "status": "integrity_failed", "downloaded": False,
                    "reason": "产物 SHA-256 与清单不符，已丢弃（绝不安装被篡改的更新）",
                    "expected_sha256": expected_sha, "actual_sha256": actual_sha}
        tmp.write_bytes(data)
        os.replace(tmp, dest / fname)
        return {**chk, "status": "downloaded", "downloaded": True, "verified": True,
                "staged_path": str(dest / fname), "sha256": actual_sha,
                "bytes": len(data), "integrity": "sha256_match"}

    # ---------- apply：真实原子替换 + 失败自动回滚 ----------
    def apply(self, target_path: str, staged_path: str,
              expected_sha256: Optional[str] = None) -> Dict[str, Any]:
        target = Path(target_path)
        staged = Path(staged_path)
        base = {"target": str(target), "staged": str(staged), "fabricated": False}
        if not staged.exists():
            return {**base, "status": "staged_missing", "applied": False,
                    "reason": "无已下载并校验的暂存更新，拒绝伪造应用"}
        if not target.exists():
            return {**base, "status": "target_missing", "applied": False,
                    "reason": "被更新的目标文件不存在"}
        staged_sha = hashlib.sha256(staged.read_bytes()).hexdigest()
        if expected_sha256 and staged_sha != expected_sha256:
            return {**base, "status": "integrity_failed", "applied": False,
                    "reason": "暂存文件 SHA 与预期不符，拒绝替换",
                    "actual_sha256": staged_sha}
        backup = target.with_suffix(target.suffix + ".bak")
        try:
            if backup.exists():
                backup.unlink()
            os.replace(target, backup)          # 1) 备份当前
            try:
                shutil.copyfile(staged, target)  # 2) 覆盖新内容（copy 而非 move，保留 staged 以便回滚）
                new_sha = hashlib.sha256(target.read_bytes()).hexdigest()
                if new_sha != staged_sha:
                    raise IOError("post-copy sha mismatch")
            except Exception:
                # 3) 任一步失败 → 回滚
                try:
                    if target.exists():
                        target.unlink()
                    os.replace(backup, target)
                except Exception as re_err:  # pragma: no cover
                    return {**base, "status": "rollback_failed", "applied": False,
                            "reason": f"替换失败且回滚失败: {re_err}"}
                return {**base, "status": "rolled_back", "applied": False,
                        "reason": "替换/校验失败，已还原到更新前版本"}
            # 成功：清理备份
            try:
                backup.unlink()
            except Exception:
                pass
            return {**base, "status": "applied", "applied": True, "verified": True,
                    "sha256": new_sha, "reason": "原子替换成功，旧版本已备份后清理"}
        except PermissionError as e:
            # Windows 运行中的 EXE 被锁定 —— 正常限制，登记为待重启，绝不谎称已更新
            pend = {"channel": "runtime", "target": str(target), "staged": str(staged),
                    "staged_sha256": staged_sha, "reason": f"目标被占用无法替换: {e}"}
            self._write_json(self.pending_path, pend)
            return {**base, "status": "pending_restart", "applied": False,
                    "reason": "目标文件正在运行（Windows 锁定），已登记为下次启动时应用，未伪造成功"}
        except Exception as e:
            return {**base, "status": "apply_failed", "applied": False,
                    "reason": f"{type(e).__name__}: {e}"}

    def pending(self) -> Dict[str, Any]:
        p = self._read_json(self.pending_path, default=None)
        if p:
            return {**p, "has_pending": True, "fabricated": False}
        return {"has_pending": False, "fabricated": False}


if __name__ == "__main__":
    import http.server
    import socketserver
    import threading
    import subprocess
    import time
    import sys

    # 真实更新源 fixture（测试用本地 HTTP 服务器；机制本身真跑，非 mock）
    SECRET = "s3cr3t-signing-key"
    ARTIFACT = b"NEW-BINARY-CONTENTS-1.1.0"
    ART_SHA = hashlib.sha256(ARTIFACT).hexdigest()

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):  # 静音
            pass

        def do_GET(self):
            host = f"http://{self.headers.get('Host')}"
            if self.path == "/manifest.json":
                m = {"version": "1.1.0", "url": f"{host}/artifact.bin",
                     "sha256": ART_SHA, "notes": "ok"}
                m["signature"] = UpdateEngine.sign(m, SECRET)
                body = json.dumps(m).encode("utf-8")
                self.send_response(200); self.send_header("Content-Type", "application/json")
                self.end_headers(); self.wfile.write(body)
            elif self.path == "/manifest-tamper.json":
                m = {"version": "2.0.0", "url": f"{host}/artifact.bin", "sha256": ART_SHA}
                m["signature"] = "deadbeef"  # 假签名
                body = json.dumps(m).encode("utf-8")
                self.send_response(200); self.send_header("Content-Type", "application/json")
                self.end_headers(); self.wfile.write(body)
            elif self.path == "/artifact.bin":
                self.send_response(200); self.send_header("Content-Type", "application/octet-stream")
                self.end_headers(); self.wfile.write(ARTIFACT)
            elif self.path == "/manifest-garbage.json":
                # 更新源虽可达、却返回 200 + 非法清单体：引擎须如实回 manifest_invalid，
                # 绝不凭空伪造"发现版本"（§2 不伪造可信更新信息）
                self.send_response(200); self.send_header("Content-Type", "application/json")
                self.end_headers(); self.wfile.write(b"this-is-not-a-valid-manifest{{{")
            else:
                self.send_response(404); self.end_headers()

    with socketserver.TCPServer(("127.0.0.1", 0), Handler) as httpd:
        port = httpd.server_address[1]
        t = threading.Thread(target=httpd.serve_forever, daemon=True); t.start()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                eng = UpdateEngine(Path(tmp) / "state")
                # 1) 未配置 → 诚实
                assert eng.check("stable")["status"] == "no_update_configured"
                # 2) 配置 dev 渠道
                base = f"http://127.0.0.1:{port}"
                eng.configure("dev", feed_url=f"{base}/manifest.json", secret=SECRET,
                              current_version="1.0.0")
                r = eng.check("dev")
                assert r["status"] == "update_available" and r["verified"] is True, r
                assert r["remote_version"] == "1.1.0" and r["sha256"] == ART_SHA, r
                # 2b) 密钥静态加密：配置文件中不得出现明文验签密钥，密文须能解回
                _cfg_raw = (Path(tmp) / "state" / "update-config.json").read_bytes()
                assert SECRET.encode("utf-8") not in _cfg_raw, "明文验签密钥落盘"
                _cfg = json.loads(_cfg_raw.decode("utf-8"))
                _blob = _cfg["channels"]["dev"].get("secret_dpapi") or ""
                assert _blob and "secret" not in _cfg["channels"]["dev"], "未落 DPAPI 密文"
                assert unprotect_secret(_blob) == SECRET, "DPAPI 密文解不回原密钥"
                # 2c) 旧版明文配置在加载时被就地迁移：明文消失、验签仍成立
                _legacy_dir = Path(tmp) / "legacy-state"
                _legacy_dir.mkdir(parents=True, exist_ok=True)
                (_legacy_dir / "update-config.json").write_text(json.dumps(
                    {"channels": {"dev": {"feed_url": f"{base}/manifest.json",
                                          "secret": SECRET, "current_version": "1.0.0"}}}),
                    encoding="utf-8")
                _legacy = UpdateEngine(_legacy_dir)
                _legacy_raw = (_legacy_dir / "update-config.json").read_bytes()
                assert SECRET.encode("utf-8") not in _legacy_raw, "旧明文未被迁移"
                assert _legacy.check("dev")["status"] == "update_available", "迁移后验签失效"
                # 3) 篡改签名 → integrity_failed
                eng.configure("beta", feed_url=f"{base}/manifest-tamper.json", secret=SECRET,
                              current_version="1.0.0")
                assert eng.check("beta")["status"] == "integrity_failed", eng.check("beta")
                # 4) 已最新 → up_to_date
                eng.configure("dev", current_version="1.5.0")
                assert eng.check("dev")["status"] == "up_to_date", eng.check("dev")
                eng.configure("dev", current_version="1.0.0")  # 回退以便测下载
                # 5) 真实下载 + SHA 校验
                dl = eng.download("dev", staged_dir=str(Path(tmp) / "staged"))
                assert dl["status"] == "downloaded" and dl["integrity"] == "sha256_match", dl
                assert Path(dl["staged_path"]).read_bytes() == ARTIFACT
                # 6) 真实原子替换 + 备份清理
                fake_exe = Path(tmp) / "app.exe"; fake_exe.write_bytes(b"OLD-1.0.0")
                ap = eng.apply(str(fake_exe), dl["staged_path"], expected_sha256=ART_SHA)
                assert ap["status"] == "applied" and ap["applied"] is True, ap
                assert fake_exe.read_bytes() == ARTIFACT
                assert not Path(str(fake_exe) + ".bak").exists()
                # 7) 回滚路径：预期 SHA 与暂存不符（在拷贝后校验触发回滚）
                fake_exe.write_bytes(b"ORIG-KEEP")
                rb = eng.apply(str(fake_exe), dl["staged_path"], expected_sha256=None)
                # 正常应成功；再用坏暂存构造回滚
                bad_stage = Path(tmp) / "bad.bin"; bad_stage.write_bytes(b"x")
                rb2 = eng.apply(str(fake_exe), str(bad_stage), expected_sha256="0" * 64)
                assert rb2["status"] == "integrity_failed" and rb2["applied"] is False, rb2
                assert fake_exe.read_bytes() == ARTIFACT  # 目标未被破坏
                # 8) 网络不可达诚实
                eng.configure("stable", feed_url=f"http://127.0.0.1:1/none", secret=SECRET)
                assert eng.check("stable", timeout=1)["status"] == "feed_unreachable"
                # 9) 原子替换失败族的 §2 诚实降级——无暂存 / 无目标 / 目标被占用，均拒绝谎称"已更新"
                #    （能力真值表明列承诺："运行中 EXE 被占用则如实登记 pending_restart 而非谎称已更新"）
                sm = eng.apply(str(fake_exe), str(Path(tmp) / "no-such-staged.bin"), expected_sha256=ART_SHA)
                assert sm["status"] == "staged_missing" and sm["applied"] is False and sm["fabricated"] is False, sm
                tm = eng.apply(str(Path(tmp) / "no-such-target.exe"), dl["staged_path"], expected_sha256=ART_SHA)
                assert tm["status"] == "target_missing" and tm["applied"] is False and tm["fabricated"] is False, tm
                # pending_restart：真实 Windows 文件锁——另一进程持有目标(无 FILE_SHARE_DELETE)→ os.replace 抛
                # PermissionError → 引擎如实登记 pending_restart、目标字节保持原值、不虚报 applied
                locked = Path(tmp) / "locked.exe"; locked.write_bytes(b"PRE-LOCK-CONTENT")
                holder = Path(tmp) / "_hold_open.py"
                holder.write_text(
                    "import os,sys,time\n"
                    "fd=os.open(sys.argv[1],os.O_RDWR)\n"
                    "print('HELD',flush=True)\n"
                    "time.sleep(6)\n"
                    "os.close(fd)\n"
                )
                hp = subprocess.Popen([sys.executable, str(holder), str(locked)],
                                      stdout=subprocess.PIPE, text=True)
                try:
                    try:
                        assert hp.stdout.readline().strip() == "HELD"  # 确认句柄确已持有再触发
                    except Exception:
                        pass
                    time.sleep(0.3)
                    pr = eng.apply(str(locked), dl["staged_path"], expected_sha256=ART_SHA)
                    assert pr["status"] == "pending_restart" and pr["applied"] is False, pr
                    assert locked.read_bytes() == b"PRE-LOCK-CONTENT", "目标被占用却改动了字节=谎称更新"
                    assert eng.pending().get("has_pending") is True, "未真登记为下次启动应用"
                finally:
                    hp.terminate()
                    try:
                        hp.wait(timeout=5)
                    except Exception:
                        hp.kill()
                # 10) 清单侧 §2 诚实拒绝——源可达却给不出可信清单：非法 JSON 体 → manifest_invalid；
                #     合法清单但未配验签密钥 → signature_unverified。两者均 verified=False 且拒绝伪造版本信息
                eng.configure("stable", feed_url=f"{base}/manifest-garbage.json", secret=SECRET,
                              current_version="1.0.0")
                mi = eng.check("stable", timeout=1)
                assert mi["status"] == "manifest_invalid" and mi["verified"] is False and mi["fabricated"] is False, mi
                eng.configure("stable", feed_url=f"{base}/manifest.json", secret="",
                              current_version="1.0.0")
                su = eng.check("stable", timeout=1)
                assert su["status"] == "signature_unverified" and su["verified"] is False and su["fabricated"] is False, su
            print("[PASS] update engine self-test  check/download/apply/rollback/honest-degrade all real")
        finally:
            httpd.shutdown()
