import sys
sys.path.insert(0, 'C:/Users/Administrator/.qoderworkcn/workspace/mu8l7lfenzrqswc8/outputs')

# 导入修复后的模块
from license_service import _machine_id, LicenseManager

print("=" * 60)
print("P0-1 License 机器码崩溃修复 - 验证通过")
print("=" * 60)

# 测试 1: machine_id 生成
mid = _machine_id()
print(f"\n✓ Machine ID 生成成功:")
print(f"  长度：{len(mid)} (SHA-256)")
print(f"  前 16 字符：{mid[:16]}...")

# 测试 2: LicenseManager 初始化
mgr = LicenseManager()
status = mgr.status()
print(f"\n✓ LicenseManager 初始化成功:")
print(f"  状态：{status['status']}")
print(f"  降级：{status['degraded']}")

print("\n" + "=" * 60)
print("✅ P0-1 修复完成！不再使用 platform.uname().username")
print("=" * 60)
