# 造境 AI 设计智能体 · Zaojing AI Design Agent

> 面向平面设计 / 印前 / 品牌交付场景的本地 AI 设计智能体服务端。
> 当前版本 **V1.0.8**，30 个可插拔引擎，四层自动化验证（L1/L2/L3/L4）全绿。

造境以单一 FastAPI 服务（`agent_service.py`）为核心，把色彩、排版、包装刀版、条码、印前质检、素材检索、知识 RAG、客户改稿、多角色评审、崩溃恢复、自动更新、NAS、AI Provider 集成、Adobe / CorelDRAW 桥接等能力，实现为一组可独立加载、可真实调用的引擎模块，并打包为 Windows 桌面程序（PyInstaller onefile EXE + Inno Setup 安装包 / 便携 ZIP）。

本项目最重要的工程纪律是**诚实**：每一项能力都在 `GET /api/v1/capabilities` 能力真值表中按 `supported / needs_adobe_host / prototype_not_shipped / not_shipped` 四态如实披露；凡依赖真实外部宿主、真实模型后端点、真实更新源或真实网络盘的能力，在未配置时一律返回诚实的降级码（如 `502 no_provider_configured`、`502 no_host_connected`、`not_configured`），绝不伪造成功结果或编造分数。

---

## 架构概览

```
webui/ (原生 HTML/CSS/JS 面板)          adobe-plugins/ (Photoshop / Illustrator UXP 参考面板)
        │                                        │
        └──────────────┐        ┌────────────────┘
                       ▼        ▼
              agent_service.py  (FastAPI · 统一 HTTP / WebSocket 服务)
                       │
        ┌──────────────┴───────────────────────────────┐
        │            30 个可插拔引擎（模块即引擎）        │
        │  color · layout · typography · packaging(条码/  │
        │  刀版拓扑) · panel_recognition · print_qa ·     │
        │  brand · design_compare · background_removal ·  │
        │  knowledge_rag · asset · revision · review ·    │
        │  evaluation · workflow · recovery · update ·    │
        │  nas · ai_provider · adobe_bridge · service_    │
        │  registry · event · delivery · desktop_hub ·    │
        │  installer_manifest · installer_runtime ·       │
        │  security · license · model_router · config …   │
        └────────────────────┬──────────────────────────┘
                             ▼
              四层验证：L1 源码级 · L2 功能验收 · L3 端到端 · L4 冻结 EXE
```

## 能力边界（截至 V1.0.8）

能力真值表当前聚合为 **43 项**，其中 **40 项 supported、3 项 needs_adobe_host、0 占位、0 not_shipped**。3 项 `needs_adobe_host` 指的是 Photoshop / Illustrator / CorelDRAW 的**真实文档写入**——桥接管道本身已端到端验证为真，但文档变更必须由带许可证的真实宿主执行。

其余依赖外部资源的能力，其"机制"（客户端 / 协议 / 校验 / 降级）均已在服务端真实实现，需要真实环境才出结果属**部署配置**而非能力缺失，典型如：

- AI 生成与任务规划：通用 OpenAI-兼容 HTTP 客户端为真，未配置可达后端点时诚实 `502 no_provider_configured`。
- 自动更新：清单拉取 / HMAC 验签 / 逐字节完整性 / 原子替换为真，未配置 feed 时 `no_update_configured`。
- NAS：映射盘 / UNC 原生访问 + WebDAV 为真，未配置目标时 `not_configured`。
- Adobe UXP 桥接：握手 / 认证 / 下发 / 对账 / 超时降级为真（协议合规 mock 宿主已验证），无真实宿主时 `502 no_host_connected`。

完整逐项判词见随包审计报告 `造境-补充完善方案-开源对照-v1.md`。

## 技术栈

- **语言 / 框架**：Python 3 + FastAPI + Uvicorn；服务与多数引擎以纯标准库实现。
- **可选第三方库**（仅特定引擎使用）：Pillow（像素/条码/品牌配色）、fontTools（字体度量）、NumPy、PyYAML、aiohttp/aiofiles、PyPDF2/pypdf。
- **桌面 / 桥接**：Adobe UXP（面板源码）、CorelDRAW 宿主脚本（COM attach-only）、原生 tkinter 桌面 Hub。
- **打包 / 分发**：PyInstaller（onefile EXE）、Inno Setup（`setup.iss` 安装版）+ 便携 ZIP，构建脚本 `build-installer.py`。

## 目录结构

```
agent_service.py          统一服务入口（FastAPI 应用 + 全部 HTTP/WebSocket 端点）
*_engine.py / P2-*.py     可插拔引擎与构建期模块（色彩、排版、包装、评审、恢复…）
evaluation_engine.py      评测引擎（185 条确定性黄金用例 + 34 条负控校准）
test-*.py                 四层验证脚手架（源码级 / 功能 / 端到端 / 冻结 EXE）
build-installer.py        打包管线（PyInstaller + Inno Setup + 便携 ZIP）
setup.iss / ZaojingAI.spec 安装器脚本与打包配置
webui/                    原生前端面板
adobe-plugins/            Photoshop / Illustrator UXP 面板参考实现
license_*.py              授权 / 机器码 / 密钥签发
*.md                      审计报告与各轮交付记录
```

## 快速开始

### 源码运行

```bash
pip install fastapi uvicorn pillow fonttools numpy pyyaml pypdf
python agent_service.py
```

服务默认监听 `127.0.0.1:8000`，可用环境变量覆盖：

```bash
set ZAOJING_HOST=127.0.0.1
set ZAOJING_PORT=8000
```

启动后：

- 交互文档：http://127.0.0.1:8000/docs
- 健康检查：`GET /api/v1/health`
- 能力真值表：`GET /api/v1/capabilities`

### 构建桌面产物

```bash
python build-installer.py
```

产出安装版（Inno Setup，需本机 `iscc`）与便携 ZIP；缺 `iscc` 时安装版优雅跳过、便携包照常生成。

### 运行验证

```bash
python test-full-functional.py     # L2 功能验收
python test-packaged-exe.py        # L4 冻结 EXE 验收
```

## 版本

采用语义化版本，当前 **1.0.8**（见 `agent_service.py` 中的 `APP_VERSION`）。历轮真实开发与能力转真记录见随包审计报告与各 `P0/P1` 交付文档。

## 许可与免责声明

本项目为"造境 AI 设计智能体"源码交付物。涉及 Adobe Photoshop / Illustrator / CorelDRAW 的文档写入能力，须在获得相应软件合法许可证的真实宿主环境中执行；本项目不附带、也不授权任何第三方软件的分发。使用本项目即表示你理解并接受：真实外部依赖（模型后端点、更新源、NAS、宿主）需自行配置，未配置时相应能力会诚实降级而非伪造结果。
