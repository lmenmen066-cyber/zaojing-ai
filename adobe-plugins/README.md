# 造境 — Adobe UXP 插件（宿主侧参考实现）

对应任务书：§1（交付 `造境-Photoshop.ccx` / `造境-Illustrator.ccx`）、§4（UXP + TypeScript + React，
插件只做 UI / Adobe 文档交互 / Tool 调用，不堆数据库/LLM/长任务）、§8（插件连接协议）、§9（统一请求格式）、
§24（Adobe Adapter）、§25（Capability Map 上报）、§74（崩溃隔离）。

## 这套代码做什么

面板是**桥接的宿主半侧**，与 Agent Service 的 `adobe_uxp_bridge` 引擎（服务端半侧）配合：

1. **端口动态发现（§8）**：读取 Agent Service 落在本地数据目录的
   `uxp/bridge-endpoint.json`，得到 `port` 与随机 `session_token`。
2. **握手 + 能力协商（§8/§25）**：`POST /api/v1/uxp/handshake` 上报 `host/version/capabilities`。
3. **命令循环（§9）**：`POST /api/v1/uxp/poll` 长轮询取待执行命令；对每条命令在**本宿主内**
   用真实 Adobe API（Photoshop `executeAsModal` + `batchPlay` / Illustrator 脚本）执行，
   再 `POST /api/v1/uxp/result` 回传**真实结果或真实错误**。
4. **崩溃隔离（§74）**：单条命令执行异常一律 try/catch 并如实回 `error`，绝不吞成成功、绝不使面板崩溃。

## ⚠️ 诚实边界（务必读）

- 本目录是**可直接审阅的真实源码**，遵循 Adobe 官方 UXP API 写法。
- **宿主实况（2026-11-02 只读核对：路径、字节数、文件版本均现场读取）**：Photoshop 2025 26.8 在
  `C:\ps\Adobe Photoshop 2025\Photoshop.exe`（201,268,720 B）；Illustrator 2023 27.1.1 在
  `C:\Program Files\Adobe\Adobe Illustrator 2023\Support Files\Contents\Windows\Illustrator.exe`
  （37,725,680 B）；CorelDRAW 2022 24.0.0.301 / 2020 22.0.0.412 / 2018 20.1.0.708 分别在
  `C:\Program Files\Corel\CorelDRAW Graphics Suite 2022\Programs64\CorelDRW.exe`（377,608 B）、
  `C:\Program Files\Corel\CorelDRAW Graphics Suite 2020\Programs64\CorelDRW.exe`（368,848 B）、
  `C:\Program Files\Corel\CDR2018\Programs64\CorelDRW.exe`（356,536 B）。
  此前「本开发环境未安装 Photoshop / Illustrator」与磁盘实况不符，已就此处更正。
- 真实边界改写为：**宿主已安装，但本工程的验收流程从不启动宿主 GUI**（不打扰用户文档），
  所以面板的"宿主内真实文档执行"**仍未在本环境运行验证**。UXP Developer Tool 在五个常见
  路径与三条卸载注册表名录中均未找到（0 命中）。
- 把它变成 §1 要求的 `.ccx` 仍需真实 Adobe 环境（UXP Developer Tool 打包）：这属于
  **部署/构建配置**，不是能力缺失。
- Agent Service 侧无论面板是否连上，都**绝不伪造文档变更**：无宿主连接时命令下发一律
  `no_host_connected`。`ps_document_write` / `ai_document_write` 在能力真值表中**始终 `needs_adobe_host`**。
- 因此："桥接管道 supported（服务端半侧已端到端验证）" ≠ "Adobe 真实文档写已在无宿主下完成"。

## 文件

```
photoshop/  manifest.json  index.html  main.js
illustrator/ manifest.json index.html  main.js
```
