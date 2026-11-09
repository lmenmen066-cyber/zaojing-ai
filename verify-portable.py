#!/usr/bin/env python3
"""verify-portable.py -- 便携包完整性校验器（纯标准库，无第三方依赖）。

用途：证明你手上这份 zaojing-ai-*-portable.zip 与构建产物逐字节一致。

两种真值来源（按优先级）：
  1) 显式传入的 sidecar .sha256（sha256sum 兼容文本，形如 "<64hex>  <文件名>"）；
  2) 未给 sidecar 时，回退读 installer/build-evidence.json 的 portable.sha256
     （路径可 --evidence 指定；默认在 ZIP 同级及其父目录/ installer/ 下就近找）。

判据（全部满足才 exit 0）：
  - 目标 ZIP 存在、可读；
  - 现场重算的 SHA-256 == 真值来源里的哈希（大小写不敏感比较，64 位十六进制）；
  - sidecar 声明的文件名（若给了 sidecar）与实际校验的 ZIP 文件名一致——防止"拿 A 的
    校验值去验 B"这种张冠李戴。
任一不满足 → 打印人类可读原因并 exit 1。缺席/解析失败一律判失败，绝不静默放过。

退出码：0=通过，1=不符或缺席，2=用法错误。
（ASCII 标记，无 emoji，兼容 Windows GBK 控制台。）
"""
import sys
import json
import hashlib
import argparse
from pathlib import Path

# Windows GBK 控制台：无法编码字符转义而非抛错
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors="backslashreplace")
    except Exception:
        pass

_HEX64_LEN = 64


def _sha256_of(path: Path) -> str:
    """分块读盘重算 SHA-256（便携包上百 MB，不一次性读进内存）。"""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _parse_sha256sum_line(text: str):
    """从 sha256sum 兼容文本取 (hash, name)。
    兼容 GNU 两种记法：文本模式 '<hash>  <name>'（两空格）与二进制 '<hash> *<name>'。
    取首个非空行里前导的 64 位十六进制段；取不到即抛 ValueError（调用方据此判失败）。
    返回 (hash_lower, name_or_None)。"""
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        # 前 64 字符必须是十六进制，之后是分隔（空格或 '*'）+ 可选文件名
        head, rest = line[:_HEX64_LEN], line[_HEX64_LEN:]
        if len(head) != _HEX64_LEN or any(c not in "0123456789abcdefABCDEF" for c in head):
            raise ValueError("首行前 64 个字符不是合法十六进制哈希串：%r" % line[:80])
        # rest 形如 "  name" / " name" / " *name" / " *name"；剥掉前导空白与 '*'
        name = rest.lstrip()
        if name.startswith("*"):
            name = name[1:]
        name = name.strip()
        return head.lower(), (name or None)
    raise ValueError("校验件为空，未找到含哈希的行")


def _locate_evidence(zip_path: Path, explicit: str):
    """找 build-evidence.json：--evidence 优先；否则就近在 ZIP 同级 installer/、
    ZIP 父目录 installer/、ZIP 父目录 下探测。找不到返回 None（回退失败由调用方处理）。"""
    if explicit:
        p = Path(explicit)
        return p if p.is_file() else None
    candidates = [
        zip_path.parent / "installer" / "build-evidence.json",
        zip_path.parent.parent / "installer" / "build-evidence.json",
        zip_path.parent / "build-evidence.json",
    ]
    for c in candidates:
        if c.is_file():
            return c
    return None


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="verify-portable.py",
        description="校验便携 ZIP 的 SHA-256 是否与 sidecar 或 build-evidence.json 一致。")
    ap.add_argument("zip", help="待校验的便携 ZIP 路径")
    ap.add_argument("sidecar", nargs="?",
                    help="sha256sum 兼容 .sha256 文件（省略则回退读 build-evidence.json）")
    ap.add_argument("--evidence", default="",
                    help="显式指定 installer/build-evidence.json（默认就近探测）")
    args = ap.parse_args(argv)

    zip_path = Path(args.zip)
    print("[INFO] 目标便携包：%s" % zip_path)

    if not zip_path.is_file():
        print("[ERROR] ZIP 不存在或不是普通文件：%s" % zip_path)
        return 1

    # 现场重算
    try:
        computed = _sha256_of(zip_path)
    except Exception as exc:
        print("[ERROR] 读取/计算 SHA-256 失败：%s" % exc)
        return 1
    print("[INFO] 现场重算 SHA256 = %s" % computed)

    want = None
    want_src = None
    want_name = None

    if args.sidecar:
        sc = Path(args.sidecar)
        if not sc.is_file():
            print("[ERROR] 指定的校验件不存在：%s" % sc)
            return 1
        try:
            want, want_name = _parse_sha256sum_line(sc.read_text(encoding="utf-8", errors="replace"))
        except Exception as exc:
            print("[ERROR] 校验件解析失败（应为 sha256sum 兼容 '<64hex>  <文件名>'）：%s" % exc)
            return 1
        want_src = "sidecar %s" % sc.name
        # 张冠李戴防护：sidecar 里点名的文件必须是眼前这枚 ZIP
        if want_name is not None and Path(want_name).name != zip_path.name:
            print("[ERROR] 校验件点名的文件是 '%s'，与待验 ZIP '%s' 不一致——拒绝混验。"
                  % (want_name, zip_path.name))
            return 1
    else:
        ev_path = _locate_evidence(zip_path, args.evidence)
        if ev_path is None:
            print("[ERROR] 既未给 sidecar，也未能就近找到 build-evidence.json；"
                  "无法确定真值哈希（缺席即判失败，绝不放行）。")
            return 1
        try:
            ev = json.loads(ev_path.read_text(encoding="utf-8"))
            want = str(ev["portable"]["sha256"]).lower()
        except Exception as exc:
            print("[ERROR] 从 %s 读取 portable.sha256 失败：%s" % (ev_path, exc))
            return 1
        want_src = "build-evidence.json (%s)" % ev_path
        # 证据里的 zip_name 也要对上，防跨包混验
        ev_zip = (ev.get("portable") or {}).get("zip_name")
        if ev_zip and Path(str(ev_zip)).name != zip_path.name:
            print("[ERROR] build-evidence 记录的文件名 '%s' 与待验 ZIP '%s' 不一致。"
                  % (ev_zip, zip_path.name))
            return 1

    if len(want) != _HEX64_LEN or any(c not in "0123456789abcdef" for c in want):
        print("[ERROR] 真值哈希形制不合法（应为 64 位十六进制小写）：%r" % want)
        return 1

    print("[INFO] 期望 SHA256   = %s   (来源：%s)" % (want, want_src))

    if computed == want:
        print("[OK] 校验通过：下载件与构建产物逐字节一致。")
        return 0
    print("[FAIL] 校验不符：现场 %s != 期望 %s。请勿使用该压缩包，回到 Actions 重新下载。"
          % (computed, want))
    return 1


if __name__ == "__main__":
    sys.exit(main())
