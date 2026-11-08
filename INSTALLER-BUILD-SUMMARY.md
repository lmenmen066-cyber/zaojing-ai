# 造境 AI 设计智能体 V1.0 - 安装程序打包总结

**版本**: V1.0 Final  
**生成时间**: 2026-09-20  
**状态**: ✅ 打包脚本已创建，等待执行

---

## 📦 打包方案概述

### 技术栈

1. **PyInstaller** - Python 应用打包工具
   - 将 Python 代码和依赖打包成独立可执行文件
   - 支持 Windows/macOS/Linux 跨平台
   - 一键打包（onefile）模式

2. **Inno Setup** - Windows 安装包制作工具
   - 免费开源的 Windows 安装程序制作工具
   - 支持 Unicode、压缩、数字签名
   - 可定制安装向导、注册表、快捷方式

---

## 🛠️ 打包流程

### 步骤 1: 检查依赖

```bash
python build-installer.py
```

脚本会自动检查以下依赖：
- ✅ Python (3.11/3.12)
- ✅ pip
- ✅ PyInstaller (>=6.0.0)
- ⚠️ Inno Setup (需手动安装)

### 步骤 2: 安装缺失依赖

如果缺少 PyInstaller，脚本会自动安装：

```bash
pip install pyinstaller>=6.0.0
```

**注意**: Inno Setup 需要手动下载安装并添加到 PATH

### 步骤 3: PyInstaller 打包

脚本会执行以下操作：

1. 创建 `.spec` 配置文件
2. 扫描所有依赖包
3. 打包成独立可执行文件 `ZaojingAI.exe`
4. 包含所有必要的 DLL 和库文件

**预计输出**:
- `dist/ZaojingAI.exe` - 主程序 (~50-100MB)
- `build/` - 临时构建目录

### 步骤 4: Inno Setup 打包

脚本会执行以下操作：

1. 生成 `setup.iss` Inno Setup 脚本
2. 编译 Windows 安装包
3. 创建安装向导和卸载程序

**预计输出**:
- `zaojing-ai-1.0.0-setup.exe` - Windows 安装包 (~50-100MB)

### 步骤 5: 生成文档

自动生成安装说明文档 `安装说明.md`

---

## 📁 生成的文件

### 主要输出

| 文件名 | 大小 | 说明 |
|-------|------|------|
| `dist/ZaojingAI.exe` | ~50-100MB | 独立可执行文件 |
| `zaojing-ai-1.0.0-setup.exe` | ~50-100MB | Windows 安装包 |
| `setup.iss` | N/A | Inno Setup 脚本 |
| `安装说明.md` | ~10KB | 安装说明文档 |

### 辅助文件

| 文件名 | 说明 |
|-------|------|
| `build-installer.py` | 打包脚本 |
| `logs/agent.log` | 应用日志 |
| `outputs/outputs/*.json` | 测试结果数据 |

---

## 🔧 配置说明

### PyInstaller 配置

在 `build-installer.py` 中配置：

```python
APP_NAME = "ZaojingAI"
VERSION = "1.0.0"
COMPANY_NAME = "造境科技"
MAIN_SCRIPT = SCRIPT_DIR / "agent_service.py"
```

隐藏导入模块：

```python
hiddenimports=[
    'platform', 'getpass', 'hashlib', 'os', 'sys',
    'fastapi', 'uvicorn', 'pydantic',
    'PIL', 'numpy', 'cv2',
    'sentence_transformers', 'hnswlib',
    'PyYAML', 'cryptography',
]
```

### Inno Setup 配置

自动配置的目录结构：

```ini
[Dirs]
Name: "{appdata}\造境科技\ZaojingAI"          ; 应用数据
Name: "{localappdata}\造境科技\ZaojingAI\Assets" ; 素材库
Name: "{localappdata}\造境科技\ZaojingAI\Cache"  ; 缓存
Name: "{localappdata}\造境科技\ZaojingAI\Logs"   ; 日志
```

注册表配置：

```ini
Root: HKCU; Subkey: "Software\造境科技\ZaojingAI"; \
    ValueType: string; ValueName: "Version"; ValueData: "1.0.0"
```

---

## 🚀 使用指南

### 方法 1: 直接运行打包脚本

```bash
cd outputs
python build-installer.py
```

### 方法 2: 分步执行

#### 步骤 A: PyInstaller 打包

```bash
cd outputs
pyinstaller --clean --onefile --name ZaojingAI agent_service.py
```

#### 步骤 B: Inno Setup 打包

```bash
iscc setup.iss
```

### 方法 3: 手动打包

#### PyInstaller 命令

```bash
pyinstaller ^
  --clean ^
  --onefile ^
  --name ZaojingAI ^
  --workpath build ^
  --distpath dist ^
  --hidden-import platform ^
  --hidden-import getpass ^
  --hidden-import hashlib ^
  --hidden-import fastapi ^
  --hidden-import uvicorn ^
  --hidden-import pydantic ^
  --hidden-import PIL ^
  --hidden-import numpy ^
  --hidden-import cv2 ^
  --hidden-import sentence_transformers ^
  --hidden-import hnswlib ^
  --hidden-import PyYAML ^
  --hidden-import cryptography ^
  agent_service.py
```

#### Inno Setup 脚本

编辑 `setup.iss` 后运行：

```bash
iscc setup.iss
```

---

## 📋 测试清单

### 打包前测试

- [ ] 确认所有核心模块已开发完成
- [ ] 确认 E2E 测试全部通过
- [ ] 确认超级验收全部通过
- [ ] 确认性能基准全部达标
- [ ] 确认文档完整

### 打包后测试

- [ ] 测试独立可执行文件能否正常运行
- [ ] 测试安装包能否正常安装
- [ ] 测试安装后能否启动服务
- [ ] 测试健康检查端点是否响应
- [ ] 测试 License 验证是否正常
- [ ] 测试卸载程序是否正常工作

### 功能测试

- [ ] Photoshop/Illustrator 集成
- [ ] 素材管理功能
- [ ] AI 生成功能
- [ ] 印前检查功能
- [ ] 批量处理功能
- [ ] 崩溃恢复功能

---

## 🐛 常见问题

### Q1: PyInstaller 找不到某些模块？

**A**: 在 `hiddenimports` 列表中显式添加该模块：

```python
hiddenimports=[
    # ... 其他模块
    'module_name_here',
]
```

### Q2: Inno Setup 未找到？

**A**: 
1. 从 https://www.jrsoftware.org/isdl.php 下载
2. 安装到默认路径
3. 确保添加到系统 PATH
4. 重新运行打包脚本

### Q3: 杀毒软件误报？

**A**: 这是正常现象。由于使用了 PyInstaller 打包，某些杀毒软件可能会误报。请将安装目录添加到白名单。

### Q4: 打包后文件太大？

**A**: 可以尝试：
1. 使用 UPX 压缩（已启用）
2. 排除不必要的依赖
3. 使用 `--exclude-module` 排除大模块

### Q5: 如何添加图标？

**A**: 在 PyInstaller 配置中添加：

```python
icon="assets/icon.ico"
```

在 Inno Setup 脚本中添加：

```ini
SetupIconFile=assets/icon.ico
```

---

## 🎯 下一步行动

### 立即可执行

1. ✅ 打包脚本已创建
2. ⏳ 等待后台任务完成
3. ⏳ 验证生成的安装包
4. ⏳ 进行实际部署测试

### 短期计划

1. Adobe 开发者证书签名
2. 官网发布
3. 应用商店上架
4. Beta 用户邀请

### 中期计划

1. macOS 版本打包
2. Linux 版本打包
3. 自动化 CI/CD 流程

---

## 📞 技术支持

**问题反馈**: forum.qoder.com  
**Bug 报告**: 使用内置反馈按钮  
**紧急问题**: 联系技术支持团队

---

**打包脚本创建时间**: 2026-09-20  
**版本**: V1.0 Final  
**状态**: ✅ 脚本已就绪，正在执行打包

---

**备注**: 打包过程可能需要 5-15 分钟，请耐心等待。完成后会生成完整的 Windows 安装包。
