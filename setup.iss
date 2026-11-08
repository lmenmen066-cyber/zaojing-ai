
; 造境 AI 设计智能体 V1.0 安装包
; ================================
; 本文件由 build-installer.py 生成，请勿手改；生成后可经
; GET /api/v1/installer/manifest/verify 做只读静态校验。

#define MyAppName "造境 AI 设计智能体"
#define MyAppVersion "1.0.8"
#define MyAppPublisher "造境科技"
#define MyAppURL "https://example.com/zaojing"
#define MyAppExeName "ZaojingAI.exe"

[Setup]
; AppId 决定卸载注册项 <AppId>_is1 的标识。此处必须双写首花括号（GUID 前两个左括号）：
; 单个 {{ 解为一个字面 {，写单括号会被当常量展开、编译器直接拒绝（本机真编译实测 exit=2）。
AppId={{D4C8A1F2-7B3E-4E5A-9C6D-2F1B8A47E3D5}
AppName=ZaojingAI
AppVersion=1.0.8
AppVerName=ZaojingAI 1.0.8
AppPublisher=造境科技
AppPublisherURL=https://example.com/zaojing
AppSupportURL=https://forum.qoder.com/
AppUpdatesURL=https://example.com/zaojing/updates
DefaultDirName={autopf}\造境科技
DefaultGroupName=ZaojingAI
AllowNoIcons=yes
OutputDir=installer
OutputBaseFilename=zaojing-ai-1.0.8-setup
Compression=lzma
SolidCompression=yes
WizardStyle=modern
; 生效装模式与 per-user 区域的配对由本机真编译器定论（Phase 53）。脚本触及 HKCU、
; localappdata、userdocs 三类 per-user 区域，而 PrivilegesRequired 缺省即 admin，iscc 首次
; 真编译当场告警：「the directive "PrivilegesRequired" is set to "admin" but per-user areas
; (HKCU,localappdata,userdocs) are used by the script」。提权运行时这些写入落在提权账户而非
; 目标用户，表现为「装完却看不见数据根」。本产品的数据根就在当前用户的 LOCALAPPDATA 下，
; 因此显式取 lowest：装模式与运行时同账户，补齐后二次真编译为零告警（编译器实测）。
PrivilegesRequired=lowest
; 架构标识同样由编译器定论：x64 已弃用，iscc 原话是「Architecture identifier "x64" is
; deprecated. Substituting "x64os", but note that "x64compatible" is preferred」，故按推荐
; 写法取 x64compatible（语义差别真实存在：x64 只匹配纯 64 位 Windows，兼容形态不含）。
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
; 语言条目由本机构建环境的真编译器定论（Phase 53）：compiler: 前缀相对 Inno 安装目录解析，
; ChineseSimplified.isl 实测存在于 Inno 安装目录的 Languages 子目录下，iscc 接受该条目并 exit=0；缺该文件的机器
; 会在编译期直接报错，因此这里不做"无从核验"的回避声明，也不做无据的可用性承诺。
; 中文在前=向导默认语言，english 保留为回退项。
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
; 载荷一律落在 {app}（即 DefaultDirName 解析出的安装根），升级/卸载按同一定位；
; 只声明磁盘上确实存在的条目，零命中的通配 Source 会让 Inno 编译期直接报错。
Source: "dist\ZaojingAI.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "安装说明.md"; DestName: "安装说明.md"; DestDir: "{userdocs}\ZaojingAI"; Flags: isreadme
Source: "V1.0-功能完成度审计报告.md"; DestName: "功能完成度审计报告.md"; DestDir: "{userdocs}\ZaojingAI"; Flags: isreadme

[Icons]
Name: "{group}\ZaojingAI"; Filename: "{app}\ZaojingAI.exe"; WorkingDir: "{app}"
Name: "{group}\文档"; Filename: "{userdocs}\ZaojingAI\安装说明.md"
; {uninstallexe} 是 Inno 对卸载器真实位置（{app}\unins000.exe）的内置常量，
; 硬写别的路径必然指不到文件。
Name: "{group}\卸载"; Filename: "{uninstallexe}"

[Dirs]
; 预建目录与服务运行时真实数据根 %LOCALAPPDATA%\Zaojing 对齐（见 config_persistence
; 的 WINDOWS_DATA_ROOT），Permissions 用 Inno 的 <组>-<级别> 形态。
Name: "{localappdata}\Zaojing\Assets"; Permissions: users-modify
Name: "{localappdata}\Zaojing\Cache"; Permissions: users-modify
Name: "{localappdata}\Zaojing\Logs"; Permissions: users-modify
Name: "{localappdata}\Zaojing\config"; Permissions: users-modify

[Registry]
; 卸载撤销声明：Inno 的 [Registry] 默认**只写不删**。Phase 58 本机真卸载实测（缺陷 #8）：
; unins000.exe 以 0 退出、安装根与 HKCU 卸载子键双双归零、四棵目录树清空，而下面三枚值
; 原样留在 HKCU\Software\造境科技\ZaojingAI 里，执行器据此判 uninstall_incomplete
; 并轮询满 180 s。键级 uninsdeletekey 连同其值与子键一并撤销；厂商父键只用
; uninsdeletekeyifempty（仅当为空才删），绝不抹掉非本产品写的东西——与 Inno 自带
; Examples\Example3.iss 的写法同构。本产品运行时全程只读注册表（零 SetValue，本轮逐字
; 检索确认），故此处撤销结构上只能删到自己写入的三枚值。
Root: HKCU; Subkey: "Software\造境科技"; Flags: uninsdeletekeyifempty
Root: HKCU; Subkey: "Software\造境科技\ZaojingAI"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\造境科技\ZaojingAI"; \
    ValueType: string; ValueName: "Version"; ValueData: "1.0.8"
Root: HKCU; Subkey: "Software\造境科技\ZaojingAI"; \
    ValueType: string; ValueName: "InstallLocation"; ValueData: "{app}"
Root: HKCU; Subkey: "Software\造境科技\ZaojingAI"; \
    ValueType: string; ValueName: "InstallDate"; ValueData: "11-08-2026"

[Run]
Filename: "{app}\ZaojingAI.exe"; Description: "{cm:LaunchProgram,ZaojingAI}"; Flags: nowait postinstall skipifsilent
