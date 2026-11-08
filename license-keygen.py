"""
造境 AI 设计智能体 - License 密钥签发工具 (Keygen)

用途：授权方（或本地自测）签发合法的签名授权密钥。
产品端 license_service.verify_license_key 使用同一 HMAC 密钥校验，
因此本工具生成的密钥可被 /api/v1/license/activate 真实接受。

密钥格式：ZAOJING.<b64url(payload)>.<b64url(hmac_sha256)>

用法示例：
    # 永久专业版
    python license-keygen.py --edition pro

    # 绑定机器码前缀 + 一年期
    python license-keygen.py --edition pro --machine 2aa53495 --days 365

    # 7 天试用
    python license-keygen.py --edition trial --days 7

    # 自定义密钥（务必与产品端 ZAOJING_LICENSE_SECRET 一致）
    set ZAOJING_LICENSE_SECRET=your-shared-secret   (Windows)
    $env:ZAOJING_LICENSE_SECRET="your-shared-secret" (PowerShell)

注意：本工具与产品需共享同一 SECRET（默认内置一致）。生产分发时应
通过环境变量注入私有 SECRET，避免默认值被外部伪造。
"""

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone

# 直接复用产品端实现，保证签发/校验逻辑永远一致
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from license_service import issue_license_key  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="造境 AI 授权密钥签发工具")
    parser.add_argument("--edition", default="pro",
                        choices=["pro", "trial", "basic"],
                        help="授权版本 (默认 pro)")
    parser.add_argument("--days", type=int, default=None,
                        help="有效天数；缺省表示永久授权")
    parser.add_argument("--machine", default=None,
                        help="绑定的机器码前缀（如 /license 返回的 machine_id 前 8 位）；缺省不限机器")
    parser.add_argument("--seats", type=int, default=1, help="授权席位数")
    parser.add_argument("--customer", default="", help="客户标识（写入载荷备查）")
    args = parser.parse_args()

    payload = {
        "edition": args.edition,
        "seats": args.seats,
        "issued_at": datetime.now(timezone.utc).isoformat(),
    }
    if args.days is not None:
        payload["exp"] = (datetime.now(timezone.utc) + timedelta(days=args.days)).isoformat()
    if args.customer:
        payload["customer"] = args.customer
    if args.machine:
        payload["mid"] = args.machine

    key = issue_license_key(payload)

    print("=" * 70)
    print("签发授权密钥（可直接粘贴到产品激活框 / POST /api/v1/license/activate）")
    print("=" * 70)
    print(key)
    print("-" * 70)
    print("载荷详情：")
    for k, v in payload.items():
        print(f"  {k:12} = {v}")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
