"""测试打包脚本"""
import subprocess
import sys
import os

os.chdir(r'C:\Users\Administrator\.qoderworkcn\workspace\mu8l7lfenzrqswc8\outputs')
result = subprocess.run([sys.executable, 'build-installer.py'], 
                       capture_output=True, 
                       encoding='gbk',
                       errors='ignore')
print(result.stdout)
if result.stderr:
    print("ERROR:", result.stderr)
print("Return code:", result.returncode)
