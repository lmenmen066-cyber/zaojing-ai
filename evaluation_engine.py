"""
造境 AI 设计智能体 - 评估中心引擎 evaluation_engine（Phase 24 真实交付）

诚实原则（§2 红线）：
- 本引擎**真实调用已交付引擎**（color / layout / packaging / brand / print_qa / knowledge_rag /
  revision / review / security / workflow / asset / recovery / typography / license / config /
  model_router / update / nas / provider / compare / bg_removal）
  上的确定性黄金用例，pass/fail 由真实返回值与事先写死的期望逐字段核对得出，绝不返回固定模拟分。
- 每条用例带**负控校准**（must_fail=True，期望故意写错）：若校准用例被判通过，
  说明判分器本身失真，整份报告 honest=False（calibration_error），不冒充成功。
- 未真实交付的能力类别（agent 意图识别需已配置 LLM、vision OCR/Logo 需 CV 模型）
  如实列在 not_evaluated，绝不为其编造分数。
- 评测报告为真实 JSON 持久化（reports/{report_id}.json），compare 对两份真实
  历史报告逐用例求翻转与差值；报告用例集不同则如实标 incomparable。

历史：V1.0 原型 run_test 返回硬编码 0.85 模拟分（假成功信号第 1 类），
Phase 24 整体重写为上述真实评测，随后由 prototype_not_shipped 升级为 supported。
Phase 27 扩容：品牌智能在 Phase 25 真实交付后，评测集新增 brand 类别 4 条真实像素
黄金用例 + 1 条品牌校准负控（20 计分 / 3 校准）；agent/vision 类别仍如实 not_evaluated。
Phase 28 扩容：印前（print_qa）与知识库检索（knowledge_rag）在早前轮次已真实交付且
本地确定性可核，评测集新增 print 类别 2 条（RGB/CMYK 真像素夹具的色彩·分辨率·出血真值）
+ knowledge 类别 2 条（检索排序相对真值 + 作用域隔离真值）+ 2 条校准负控（24 计分 / 5 校准）；
补考卷不翻转任何能力状态，agent/vision 仍如实 not_evaluated。
Phase 30 扩容：改稿（revision）、评审（review=design_review）、安全（security）三引擎为
纯本地确定性（零时钟/零随机/零网络参与判分），评测集新增 revision 3 条（指令矩阵/
真实换算 apply/无法解析绝不编造）+ review 3 条（缺陷夹具逐项真值 score=68/干净夹具
满分反证/WCAG 对比度与自动修复精确算式）+ security 3 条（路径四向哨兵/权限交集三态/
高危不虚报已执行）+ 3 条校准负控（33 计分 / 8 校准）。agent/vision 仍如实 not_evaluated。
Phase 31 扩容：工作流引擎（workflow）经确定性反查合格（uuid 仅实例 id、datetime 仅记录戳、
无 set 迭代序/挂钟参与判分），评测集新增 workflow 类别 6 条（环依赖检出精确报错 / 合法链
INPUT→TOOL→OUTPUT 逐数贯通含日志序列真值 / 未注册工具失败传播且下游节点绝不虚报执行 /
条件边分叉 yes 执行 no 如实 skipped / Loop 体聚合 iterations=3 / 同链两次执行剥除时间戳后
幂等全等）+ 1 条校准负控（39 计分 / 9 校准）。agent/vision 仍如实 not_evaluated。
Phase 32 扩容：素材（asset）与崩溃恢复（recovery）两引擎经**字段级**确定性反查合格
（uuid/datetime 仅渗入 id 与时间戳等非计分字段，剥除后判分字段全部可手工核），评测集新增
asset 6 条（token-hash 稀疏向量非零位精确真值 / 真实 PNG 入库元数据+缩略图+向量同源 /
检索排序 1.0 与 0.402015 精确余弦 / CC-BY 署名规则双向分支 / 生成元数据回读全字段 /
删除级联+落盘重启读回）+ recovery 6 条（快照 sha256 与真实字节精确相符 / 损坏后还原字节
回归 / 事务操作计数与回滚真实还原 / 任务注册字段真值 / 检查点恢复+幽灵任务如实抛错 /
无本地文件快照诚实标记不可恢复不谎报）+ 2 条校准负控（51 计分 / 11 校准）。
agent/vision 仍如实 not_evaluated。
Phase 33 扩容：字体排版（typography）引擎真值计算本身确定，但字体索引依赖本机字体属外因，
故评测以**自供夹具字体**断链——用 fontTools FontBuilder 现场编译两枚真 TTF（Plane-15 专码位
经真机字体反查排除撞车），走"溯源解析→夹具重放索引"：引擎 A 以 include_os=False 只扫夹具
目录（同一条真实 fontTools 解析路径，秒级完成，不触本机字体；专码位不撞车性由 Phase 33
探针以真机全量字体反查钉死），
逐用例引擎 B 索引仅含该两枚夹具记录（零本机字体干扰、零桩、全真实代码路径与真实字节），
评测集新增 typography 6 条（serif/sans 度量逐项精确+缺字如实计数 / 双门槛覆盖过滤唯一候选 /
单门槛+字重罚分降序精确 / 替换规则持久化回读 / 幽灵字体 None+KeyError 原文 / 启发式经门槛
真值）+ 2 条校准负控（57 计分 / 13 校准）。agent/vision 仍如实 not_evaluated。
Phase 34 扩容：条码（packaging_barcode）与面板拓扑（packaging_panel_topology）同属 packaging
引擎但早前 6 条 production 用例把二者与刀版/拼版混编一类、且白名单域为 None 无法挂逐能力证据；
本轮将其升格为**独立黄金域**并加深判分——barcode 5 条（EAN-13 95 模块位串逐字符对 GS1 公开编码
例全等 / EAN-8 67 模块位串+校验位 4 全等 / 渲染 PNG 真像素逐点采样护条·静区·留白黑白精确 /
双码制非法校验位 ValueError 原文逐字精确且绝不落盘 / QR 不虚报不产图+13 位合法直传+参数化几何
真值）+ topology 5 条（直插盒 9 面板度序列·折长 640·裁切线 870·面积 28700 全字段 / 折线坐标逐数
精确 / 共边部分重叠 shared=5.0 / 断开+非法混合三态如实与空输入原文抛错 / role_hint 几何启发式
如实标注宽扁高身分级精确）+ 2 条校准负控（67 计分 / 15 校准）。判分全部零桩：位串期望在评测
源码内以 GS1 发布例逐字符手抄，像素真值直接采样渲染产物。agent/vision 仍如实 not_evaluated。
Phase 36 扩容：授权（license_service）、用户配置持久化（config_persistence）与模型路由
（model_router）经确定性反查合格——签发/验签为 HMAC-SHA256 纯函数，配置判分路径零时钟零随机，
路由选路只在已发起请求的 provider 上读挂钟（评测夹具注册表逐用例全新构造、请求计数不动），
熔断开路 create_task 的无运行循环 RuntimeError 怪癖以纯线程/环内两语境钉死为真值（Phase 60
就地改核：该怪癖经取证判为真缺陷并已根治——create_task 退役、复口改由单调时间戳惰性判定，
同一枚用例现钉"无环也能开环与到点复口"，详见报告 Phase 60 章节）。评测集新增
license 4 条（两次签发票据字节全等+验签载荷全等回显 / 篡改签名与四种非法格式报错原文逐字，
含 base64url 静默丢非法字符落入签名失败分支这一实际行为钉死 / 过期·未来·非法日期三态精确 /
机器绑定前缀匹配与不符原文）+ config 4 条（默认真值：版本·引擎 URL·7 阻断·13 扩展名·
${LOCALAPPDATA}\\Zaojing 后缀拼接串不展开之实际行为如实钉死 / set+save 后全新实例冷读回+磁盘
文件核实 / 旧版本+缺键迁移四字段 / 路径拦截六向+双扩展名两态）+ model_routing 4 条（YAML
注册表真实解析枚举字段 / cost 模式按模型均价 0.004 与插入序平票裁决+未知模型 None /
privacy 择本地·latency 随熔断配置均值择云 / 预算严格小于排除与熔断开启排除三态）
+ 3 条校准负控（79 计分 / 18 校准）。三域引擎由评测台自构（模块即引擎、逐用例私有沙箱），
不注入服务单例，理由同 workflow/asset/recovery/typography。agent/vision 仍如实 not_evaluated。
Phase 37 扩容：自动更新（update_engine）、NAS 挂载（nas_engine）与 AI Provider 接入
（ai_provider_engine）经确定性反查合格——判分字段零时钟零随机，网络面由评测自起的
127.0.0.1:0 临时端口进程内 mock HTTP 服务器驱动（routes/seen 逐用例隔离、用毕必 shutdown，
与本机在线服务零端口冲突），沙箱先清后建。评测集新增 update 4 条（无更新源诚实拒伪造三字段+
三渠道未配+非法渠道报错原文 / 清单规范化字节与 HMAC-SHA256 独立复算全等，篡改与错钥必验签
失败 / 真实 HTTP 全链：验签通过→下载 sha256 相符→原子应用字节回归、.bak 清理、无幽灵 pending
/ 分支矩阵逐项真值：乱码·篡改·未设钥·版本门先于验签门·降级 up_to_date 次序如实钉死）+
nas 4 条（未配置·非法类型·空目标·不可达四态如实报错原文逐字 / 文件共享连接与目录清单排序·
类型·大小·首见缓存逐项真值 / 路径穿越与绝对路径双向拦截且越界下载绝不落盘 / 真实下载
sha256_computed+冷实例缓存命中四元组全等+清缓存与遗忘逐项回读）+ provider 4 条（未配置不
虚报：chat·生图·健康检查三态零伪造 / 密钥脱敏字面真值 sk-***cret+DPAPI 落盘明文绝不在
JSON+回解密相符 / 真实 HTTP 对话 content·usage·Bearer 头·单请求逐字段精确 / 401 不重试
+熔断开路零 HTTP+view circuit_open 真值）+ 3 条校准负控（91 计分 / 21 校准）。
三域引擎同由评测台自构（模块即引擎、逐用例私有沙箱），不注入服务单例。
agent/vision 仍如实 not_evaluated。
Phase 38 扩容：设计版本对比（design_compare）与去背景（image_remove_background）原为
agent_service 内联逻辑（可确定性反查合格但无引擎可挂证），本轮**逐字下沉**为第 23/24 引擎
design_compare_engine（纯函数，零状态零时钟零随机）与 background_removal_engine（真像素
BFS 泛洪，仅 PIL 参与），端点改薄包装、契约字段逐字保留（既有 L2/L4 端点断言即回归网）。
评测集新增 compare 4 条（空 diff identical=True 六类计数全 0 / 富字段对象六类逐数归类
{geometry:2,text:1,color:1,asset:1,layer:1,visual:1}+moved 逐字段+相同 type 不入 diff /
结构增删按 type 归类 image→asset·未知兜底 layer 且 layer 项序逐字段 / compare_keys 限字段集
+name 索引+未知字段兜底 visual）+ bg_removal 4 条（夹具图泛洪清 8400px·84% 精确、角 0/
方块 255/被包围孔洞 255 采样落证——泛洪≠全局色键的可证伪真值 / 阈值边界：0·1 仅等色、
999 如实钳 255 全幅清空 / 幽灵输入 FileNotFoundError 中文原文+绝不落盘 / 缺省输出落同级
_no_bg.png+两次跑逐数幂等确定性反查）+ 2 条校准负控（99 计分 / 23 校准）。compare 判分为
纯函数零状态；bg_removal 逐用例在评测私有 _bg 沙箱先清后建，夹具为评测自造真像素 PNG。
Phase 47 扩容：栅格面板识别（packaging_panel_recognition）原为 not_shipped（诚实占位），本轮
真实交付为第 25 引擎 panel_recognition_engine（Otsu 阈值自算 + 轴对齐游程抽线 + 共线合并判型 +
虚线支撑屏障 + 游程并查集连通域，全程无 CV/ML）。评测集新增 panel_recognition 4 条（3×2 网格
逐字段反解 6 面板×2535px·邻接 7·度序列 [3,3,2,2,2,2]·轮廓 1248 / 参数门效应：同一图线宽 3↔12
两态且降级分支响应面与正常分支全等、px_per_mm=2 换算 32.5×19.5mm 不改像素面积 / 三态诚实空报
含反零向量 / 三次调用逐字段幂等 + params 回显）+ 1 条校准负控（103 计分 / 24 校准）。夹具为评测
自绘刀版（画幅、划分、虚线常数均与引擎自测夹具不同，字节 sha256 双向钉死），逐用例落私有 _panel
沙箱先清后建；期望全部由 p47_eval_probe.py 从磁盘实测冻结。
Phase 49 扩容：安装清单校验（installer_execution）原为整项 not_shipped，本轮红线分裂——
只读可校验的一半（清单静态校验 + 安装态只读探测）真实交付为第 26 引擎
installer_manifest_engine（.iss 分段解析 + R1~R14 十四条规则 + 文件系统/注册表只读接缝），
写侧执行仍如实留驻 not_shipped。评测集新增 installer_manifest 4 条（自造合规清单零检出·
解析器续行/注释真值 / 自造破损清单 12 项检出逐 id 逐项序精确：8 error·3 warn·1 info，
含 {tmp} 合法常量永不解析的机器无关触发与 {install_date} 一条字面量双落 R1/R8 /
四路接缝反证：_path_exists 桩假→合规清单必报缺件、_glob_matches 桩真→零命中通配 error
消失、_env_dir 双向桩→R7 结论翻转、注册表桩→installed 真翻转且真 portable 标记不翻 installed /
入参拒绝五类原文 + 两次调用 JSON sha256 全等 + 未桩态 installed 必由 probes 逐项派生）
+ 1 条校准负控（107 计分 / 25 校准）。夹具为评测自造 .iss 文本与真实载荷文件，逐用例落
私有 _manifest 沙箱先清后建；机器相关项一律走模块级接缝桩（Phase 38 锚点隔离定律），
期望全部由 p49_eval_probe.py 从磁盘实测冻结。
Phase 50 扩容：桌面 Hub（desktop_hub）原为整项 not_shipped（旧判词把能力误绑在 Tauri
工具链上，称"前端 Hub 不在本服务交付范围"），本轮真实交付为第 27 引擎
desktop_hub_engine（纯标准库 tkinter + urllib 的本机原生窗口客户端，零新增 /api/v1 路由）。
评测台新增 desktop_hub 4 条（三源只读 GET 逐字段搬运 + 八行窗口文本逐字符全等 /
绝不编数反证：跨源引擎清单不一致须显 False、形状不合造境 health 契约的 200+JSON 须判该源
降级且绝不落 vNone 占位行 / 三形制诚实降级：端口关闭、非造境服务逐源独立不连坐、JSON
残缺，加无显示会话如实 created=False / 唯一 POST 通道：只读路径恒零 POST、显式动作恰一次
体 {} 的 POST、500 原文照抄、未知动作抛错且零新增请求）+ 1 条校准负控（111 计分 / 26 校准）。
GUI 本体刻意不进评测台（见 NOT_EVALUATED["gui"]），其活体实证在 L2/L4；网络夹具为评测自起
的 127.0.0.1:0 进程内真 HTTP，判分字段以 _hub_norm 把端口归一成 "BASE"（Phase 37 同源纪律，
保证顶层/嵌套两次逐字段全等），期望全部由 p50_hub_eval_probe.py 从磁盘实测冻结。
Phase 53 扩容：安装清单域的另外两面转真并并入评测台——编译面 verify_build_evidence（产物
是否被这份证据证成）与预检面 preflight_install（本机是否具备安装条件），另补四条"编译器
定论"静态开火臂。installer_manifest 由 4 条扩至 10 条：
① 编译面正控（exit=0 + 尾部完成行 + 产物在盘三独立信号同立；证据字节/sha256 由本用例用
hashlib 独立重算而非抄引擎返回值；自报 compiled/fabricated 翻转后除回显字段外整份报告全等，
证明自报不参与判定；校验前后沙箱逐条全等即零落盘）
② 编译面四臂（等长原地替换只有 sha256 能发现而 compile_succeeded 仍 True / 编译后改脚本判
artifact_not_from_current_script 而静态复校照旧 manifest_ok——陈旧态只有脚本侧对比能抓 /
产物缺席判 artifact_missing 且优先于 compile_not_proven / 退出码非零且无完成行判
compile_not_proven 而静态面仍 manifest_ok，即"静态合规≠编译通过"）
③ 告警与完备性（全文尾部走等式口径、截断尾部走下界口径，四臂各自钉死；缺字段判
evidence_incomplete 且 provenance 不替编译器说话；完成行可退回派生字段与旧 stderr 键；
build-evidence.json 文件形制证据）
④ 预检面正控（七项检查全 True 且 blocked/unknown 皆空；写探针真落盘并自清——探针文件与其
自建目录均不留；{autopf} 成对给出 commonpf/userpf 两条候选；需求字节=产物字节+声明载荷字节
实测和；execution 取单一真相源常量；不给 target_override 时真实安装根零探针）
⑤ 预检面十臂（同名文件占位、载荷缺失且先阻断后未知、证据缺席、证据篡改、余量读不到/不足、
目标 exe 占用三态、令牌未提权与探测不到、写探针三种归因；四路接缝 _token_elevation/
_disk_free/_exclusive_open/_write_probe 桩后必还原并复验回基线）
⑥ 静态开火臂（R6 单写首括号 error 且键名派生与转义状态无关 / R12 两条 directive 各出一条
warn 且整词比较对 x64compatible 零误报 / R13 缺 AppVersion 与 AppVerName 二者为 error、
AppVerName 单独在场即合规 / R11 缺省 admin 与 HKCU 区配对 warn、补 lowest 归零——四条夹具
各只改一处形制，互不掩盖）
+ 1 条校准负控（假期望把证据自报 compiled=False 当成编译未证成的判据，诚实判分必红），
合计 117 计分 / 27 校准。
Phase 58 扩容：安装执行面（installer_execution）原为整份名单唯一留守的 not_shipped（旧判词
"需真实安装环境"），本轮转真为 CLI 执行器（install-exec install|uninstall|plan|state）。评测台
并入 installer_execution 黄金域 6 条 + 1 条校准负控，另随第 26 引擎新增静态规则 R14 并入
installer_manifest 第 11 条（七臂开火/归零，本机实量合计 134 计分 / 30 校准）：
① plan 只算不跑（制品 sha/字节由本用例 hashlib 独立读盘复算；五枚落点标签 exists_now 逐枚与
现场重算一致；AppId 源文双写括号须解成 {GUID}_is1；七道门·四静默开关·argv 模板·确认令牌逐字；
plan 前后沙箱逐条全等即零落盘）
② 十七道拒绝臂（同意缺失三形制 / 制品缺席 / 制品越允许根 / 台账缺席与坏 JSON / 改长与等长替换
两形 sha 门 / 台账指错文件判 build_ledger_stale / 安装根相对·越根·落源码树·非空 / 卸载侧同意与
路径三门；每臂拒绝后现场逐条全等、台账从不落盘、十七臂里唯一"安装根在场上"的必须是那条自己
种了文件的非空臂——拒绝的代价必须真是零）
③ 真装一趟的差集派生（十条观察逐项 ok 且顺序逐字；install_root 差集与评测自己 rglob 的磁盘
实测逐条全等；新增物理文件恰 4 枚且 created_count 等于去重后的磁盘真相——install_root 与
vendor_dir 在本机是同一物理目录，按"落点标签×文件"计数就会把同一枚文件数两遍；注册表从空到
有；argv 六段；日志字节与 sha；既有数据 1 份逐字节完好；台账 self_report_ignored 七枚逐字）
④ 自报不作数（同一份 returncode=0 + "Installation finished" 自报文案喂给两枚沙箱：真装者判
install_verified、零副作用的幽灵安装判不通过；日志完成行自报 True 而 created_count=0、注册表
仍空、安装根从未被创建，execution_state 读该台账判 ledger_unverified 且 mismatches 为空——
即"没有可证伪的矛盾"不等于"已核验"，靠的是现场重算而非回执布尔）
⑤ 卸载三形制 + 台账对账（已装无卸载记录须恰一条 mismatch 且状态 ledger_inconsistent；轮询到
超时判 uninstall_incomplete 且 residue 含注册表两键与安装根；真卸后三键归零、removed 恰 4 枚
且每枚 created_by_install+gone_now、untouched_non_created 为空、另一枚沙箱的用户文件逐字节
不动；对账 executed_verified 的九项现场重算逐字段钉死；等长篡改制品须**恰一条**点名"与盘上现算值
不符"（两份台账彼此仍全等，只有 sha 能说话）且还原即复绿；nothing_to_uninstall / missing_uninstaller
两形制含 looked_in 与 recorded_in_ledger）
⑥ 第二轮证据同根（第 10 枚缺陷：台账与缺省日志落在**被观察的数据根内**＝真实 CLI 的产品形状）
两轮 install→uninstall 全跑：轮 1 对照臂钉"证据同根且缺省日志就在台账旁边、干净往返判
uninstall_verified、创建集 5 枚、自家两枚证据件以 bytes_before=None 列进另账"；轮 2 的基线里必然
出现上一轮那份台账，而评测**自己**在它被改写前后各算一次 sha256 证明它确实变了（本轮写后 ≠
本轮写前），但它属执行器自家证据 ⇒ 从用户既有数据基线逐出、另账回显 this_run_rewrites_it=True，
卸载仍判 uninstall_verified 且 deviating 为空、创建集回到 4 枚（日志早已在场）、数据根剩下的
三枚由评测 rglob 独立核对；对账面 executed_verified 且只回显不声称重写（三枚 rewrites 必为 False）
+ 1 条校准负控（假期望把"退出码 0 + 自报完成行 + installed=True"当成安装成立，诚实判分必红）。
分工纪律：本域**零子进程**，只经 roots=/registry=/spawn=/build_ledger=/ledger_path= 五枚显式接缝
在进程内复现安装器副作用，本机真实 %LOCALAPPDATA% 全程不被触碰；真子进程假安装器由 L1 实证，
真 Inno 真装真卸由冻结 EXE 的 install-exec CLI 实证。三侧共用同一套判定代码，任何一侧通过都不
证明另一侧。沙箱刻意落在**系统临时根**而非 store_dir：store_dir 在源码树内，而执行器
_gate_install_dir 明令"安装根不得落在源码树内"（那正是本阶段要守的门），放 store_dir 会让每条
真装臂一进门就被自己的门拒掉。夹具形制照本机真装实况而非 setup.iss 字面声明（安装根与 vendor_dir
同一物理目录、数据根日志目录实为小写 logs），两处差异都是历轮假信号的宿主。执行面期望逐项由
p58_probe_actions.py（冻结台账 p58_actions_probe.json）对同一份执行器源码的现场读回钉死。
机器无关纪律（Phase 53 立、Phase 58 沿用）：凡随本机而异的量——磁盘余量、真令牌是否
提权、Program Files 实际落点、write_text 落盘的 CRLF 字节数——一律写成关系式或走显式接缝桩，
随本机而异的值绝不写进 details.actual，故顶层/嵌套两次判分细节全等本身就是"判分不含机器实况"
的落证。合规夹具的形制照抄本机真编译通过（exit=0、0 告警）的生产 setup.iss；清单域期望逐项由
p53_face_probe3/4/5.py 对评测台原件夹具（MF_* 常量与 _mf_evidence/_manifest_build_sandbox）
的磁盘实测冻结。
Phase 59 扩容（路线 B：第 22 引擎的守护面升为独立黄金域 + 编译面/预检面从清单域拆出挂证）：
runtime_guard 5 条真值 + 1 条校准，另把 installer_manifest 的三件编译面用例改挂
installer_artifact_build、两件预检面用例改挂 installer_preflight（清单域由 11 条收为 6 条，
**收紧不是删除**——判据原文一字未改，只换挂证域名），并补 1 条预检面校准（假期望把"调用方
关闭写探针"当成"可写已证"）。本机实量合计 139 计分 / 32 校准（总 171）：
① 写探针三臂（可写臂 writable/bytes_verified/cleaned 三真且 method/fabricated 如实、事后
zj_write_probe_* 计数为 0；目标本身是普通文件与父路径是普通文件两臂三假且 error 均以
FileExistsError 起手——两侧同写才许判绿；target 归一为 <SB>/probe-ok）
② 磁盘阈值两侧（min=0 必 meets_min=True、min=2**62 必 False、负阈值抛点名错误原文、阈值逐字
回显；free/used 是本机连续变化量，一律不入判分字段）
③ 端口真 bind 两侧（占用臂由评测自起 bind(0)+listen 的桩构成，available=False 且 detail 的
异常类型逐字为 OSError；已关闭臂 available=True 且 detail=None；越界 70000 抛点名错误；
端口号只作结构核对绝不当判分内容，外部 socket 一枚不碰）
④ 单实例互斥五步链（first=[True,False]→同实例二次 [False,True]→release.released=True→同实例
再申请 [True,False]→**另一实例**二次 [False,True] 且它的 released=False（句柄不属它就不许谎称
释放成功）→重复释放亦 False；非法锁名 a/b 抛点名错误；finally 必收回本进程句柄，绝不把命名
互斥体留给后续用例）
⑤ 诊断派生与降级（健康臂三 check 全 pass 且 [performed,passed,failed]=[3,3,0]、status=healthy、
timestamp 为 None 即刻意不盖戳；带端口臂四项含 port_available；降级臂 data_root_writable=fail
且 status=degraded 且 checks_failed==performed-passed 恒为派生式；**构造不建根**那一臂两侧同写：
RuntimeGuardEngine(data_root=不存在的路径) 后 exists() 必 False，跑完诊断才 True；沙箱残留只有
.runtime-guard 一枚目录而探针文件计数为 0）
+ 1 条守护面校准（假期望把降级臂 status 写成 healthy）+ 1 条预检面校准（假期望把探针关闭的
preflight_unknown 写成 preflight_ready）。守护面五臂的期望逐字段来自只读探针 p59_det_probe.py v2
（每面三轮、把沙箱绝对路径/随机锁名/随机端口/PID/磁盘读数归一后做全字段 deepdiff，五面
diff_count 恒为 0 才立本节用例；v1 的分类器把真值漂移误分成易变字段、自造假绿，已整体作废），
不是照着手抄文档。沙箱走评测私有 _zj_eval_guard 根，先清后建（Phase 32 纪律）。
agent/vision/gui 仍如实 not_evaluated。
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import secrets
import shutil
import statistics
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

SCHEMA = "zaojing-eval/v1"

# 未评测类别的诚实边界（不是遗漏，是声明）
NOT_EVALUATED = {
    "agent": "意图识别/Tool 选择评测需要已配置的真实 LLM Provider 且其输出无确定性本地真值；"
             "为避免伪造通过率，本引擎不评测该类别（Provider 配置见 /api/v1/provider）",
    "vision": "OCR / 对象 / Logo 识别需真实 CV/语义模型（未随服务打包）；"
              "面板几何反解自 Phase 47 起由 panel_recognition_engine 真实交付并入评测黄金域，但语义级栅格识别不在其能力内，不在此编造视觉分数",
    "gui": "桌面 Hub 的窗口本体（真建窗、逐 Label 文本回读、按钮真实 invoke 后的服务端"
           "计数增量）刻意不进评测台：无显示会话的环境会让评测脆断，而窗口是本地 GUI 而非"
           "可搬运真值。评测台判的是其数据层/呈现层/动作层的可证伪纪律（desktop_hub 黄金域："
           "三源只读搬运、形状不合契约的降级、唯一 POST 通道），窗口活体实证在 L2/L4 完成",
}


class EvaluationError(ValueError):
    """评测过程中的诚实错误（结构非法 / 报告不存在等），路由层转 4xx。"""


def _close(a: float, b: float, eps: float = 1e-6) -> bool:
    return isinstance(a, (int, float)) and isinstance(b, (int, float)) and math.isfinite(a) and math.isfinite(b) and abs(a - b) <= eps


# Phase 34：EAN 位串期望以 GS1 公开发布编码例（5901234123457 / 96385074）在本评测
# 源码内逐字符手抄（P33 探针并经结构复算二次核对），与被测引擎实现相互独立。
P34_EAN13_BITS = ("101" "0001011" "0100111" "0110011" "0010011" "0111101" "0011101"
                  "01010" "1100110" "1101100" "1000010" "1011100" "1001110" "1000100" "101")
P34_EAN8_BITS = ("101" "0001011" "0101111" "0111101" "0110111"
                 "01010" "1001110" "1110010" "1000100" "1011100" "101")

# Phase 57：QR 期望同样以 ISO/IEC 18004 公开表在本评测源码内**逐格手抄**，与被测引擎实现相互
# 独立（P34 纪律的延续）。每张表的独立推导路径与交叉凭证见本轮 p57_eval_freeze.txt
# （FREEZE ALL_GREEN bad=0）与 p57_qr_table8_probe 实测（40 格 0.36s 全对账）：
#   Table 7 各版本总码字数（与纠错等级无关）+ 末段余比特列
P57_QR_TOTAL_CW = {1: 26, 2: 44, 3: 70, 4: 100, 5: 134,
                   6: 172, 7: 196, 8: 242, 9: 292, 10: 346}
P57_QR_REMAINDER_BITS = {1: 0, 2: 7, 3: 7, 4: 7, 5: 7, 6: 7, 7: 0, 8: 0, 9: 0, 10: 0}
#   Table 9 第 1、2 版（单块）纠错码字数列：data = total − ec
P57_QR_EC_CW_V12 = {1: {"L": 7, "M": 10, "Q": 13, "H": 17},
                    2: {"L": 10, "M": 16, "Q": 22, "H": 28}}
#   Table 8 Byte(8 位) 模式字符容量 v1..v10 × L/M/Q/H（手抄整表）
P57_QR_BYTE_CAP = {"L": [17, 32, 53, 78, 106, 134, 154, 192, 230, 271],
                   "M": [14, 26, 42, 62, 84, 106, 122, 152, 180, 213],
                   "Q": [11, 20, 32, 46, 60, 74, 86, 108, 130, 151],
                   "H": [7, 14, 24, 34, 44, 58, 64, 84, 98, 119]}
#   Table 3 字符计数指示器宽度：Byte 模式 v1..v9 为 8 位、v10..v40 为 16 位
#   ⇒ 手抄独立式 data_codewords == Byte 容量 + (2 若 v<10 否则 3)
P57_QR_CC_EXTRA = {1: 2, 2: 2, 3: 2, 4: 2, 5: 2, 6: 2, 7: 2, 8: 2, 9: 2, 10: 3}
#   格式信息 BCH(15,5) 生成多项式 0x537 + 掩码 0x5412、版本信息 BCH(18,6) 生成多项式 0x1F25、
#   纠错等级 2 位编码（Table 9 表头：L=01 M=00 Q=11 H=10）
P57_QR_FMT_GEN, P57_QR_FMT_XOR, P57_QR_VER_GEN = 0x537, 0x5412, 0x1F25
P57_QR_EC_BITS = {"L": 0b01, "M": 0b00, "Q": 0b11, "H": 0b10}
#   ISO 公布例逐字符手抄（尺子自校准正对照：评测自带 BCH 实现必须先复现这三串才算可用）
P57_QR_FMT_L_M0 = "111011111000100"    # L 级 mask 0
P57_QR_FMT_M_M0 = "101010000010010"    # M 级 mask 0（恰为 XOR 常量本身）
P57_QR_VER_V7 = "000111110010010100"   # 版本 7 的 18 位版本信息


def _p57_bch(data: int, gen: int, check_bits: int, xor: int = 0) -> int:
    """评测侧独立实现的 BCH 系统编码（多项式长除，与引擎实现无共享代码路径）。"""
    rem = data << check_bits
    for i in range(data.bit_length() - 1, -1, -1):
        if (rem >> (i + check_bits)) & 1:
            rem ^= gen << i
    return ((data << check_bits) | (rem & ((1 << check_bits) - 1))) ^ xor


def _p57_fmt_bits(level: str, mask: int) -> str:
    return format(_p57_bch((P57_QR_EC_BITS[level] << 3) | mask,
                           P57_QR_FMT_GEN, 10, P57_QR_FMT_XOR), "015b")


# ======================================================================
# Phase 63 G 腿：一维（EAN）解码 oracle 入评测台黄金域。
#
# 为什么评测侧必须自写一遍解码而不能借第三方库：产品包的设计前提是「零外部条码库」
# （packaging_engine 自述与 agent_service note 同口径），本引擎被打包进冻结 EXE 内部运行，
# 把 cv2 拉进包里属新增第三方运行时依赖、是另一件事。故此处走 P34/P51 同族的
# 「独立复算型 oracle」路径：纯标准库 + Pillow，从产品自家渲染的 PNG **像素**出发读回码串。
# 可行性实证见本轮 p63_spike_1d_decode.txt（三段裁决现场派生：正对照=True、
# 第三方 cv2 交叉印证=True（补白梯 16/24/32/40 四档读回同一串）、三阴性=True）。
#
# 形制纪律（每一条形成本轮抓到活尺子缺陷的理由，见 p63_gs1_pairwise.txt red=4）：
#  1) L/G/R 三张表**各自手抄**（不派生），再让 GS1 公开构造律 R=complement(L)、
#     G=reverse(complement(L)) 作为**判据**去核它们——若把 R/G 由 L 派生，构造律自检就成了
#     恒真式（空集上的 all() 那种 vacuous 绿），也就永远不会发现 L2/L4 那种「把 L(4) 抄进
#     G(7) 的格子」的活尺子缺陷；
#  2) L 值集与 G 值集必须**互斥**（GS1 用奇/偶码重分类，天然不相交）⇒ 任一格抄成另一族的
#     合法码字会当场撞上，这正是本条判据的靶心；
#  3) 期望码串一律现场派生（校验位由本文件自带的 GS1 加权和算法复算），绝不把 13 位字面串
#     当常量钉进用例；
#  4) 采样几何（条区竖带、左边界、模块宽）从像素派生，不硬编码产品的边距/倍数常量。
P63_EAN_L = {"0": "0001101", "1": "0011001", "2": "0010011", "3": "0111101", "4": "0100011",
             "5": "0110001", "6": "0101111", "7": "0111011", "8": "0110111", "9": "0001011"}
# 2026-10-11 追加改核（GREEN 落笔，不改写上段 RED 结论）：下面 G 表的 "7" 已由盘上实测的
# 缺陷值 "0100011"（= L(4) 的码字，逐字抄自 L2 第 2235 行 / L4 第 833 行）改为构造律值
# "0010001" = reverse(complement(L(7)))，L(7)="0111011"。RED 先行的证据在
# p63_eval_red.txt（修仪器前的首跑另见 p63_ean8_offset_probe.txt），断言一条没动。
P63_EAN_G = {"0": "0100111", "1": "0110011", "2": "0011011", "3": "0100001", "4": "0011101",
             "5": "0111001", "6": "0000101", "7": "0010001", "8": "0001001", "9": "0010111"}
P63_EAN_R = {"0": "1110010", "1": "1100110", "2": "1101100", "3": "1000010", "4": "1011100",
             "5": "1001110", "6": "1010000", "7": "1000100", "8": "1001000", "9": "1110100"}
P63_EAN_PARITY = {"0": "LLLLLL", "1": "LLGLGG", "2": "LLGGLG", "3": "LLGGGL", "4": "LGLLGG",
                  "5": "LGGLLG", "6": "LGGGLL", "7": "LGLGLG", "8": "LGLGGL", "9": "LGGLGL"}
P63_GUARD, P63_MID_GUARD = "101", "01010"
P63_EAN13_PUBLIC = "5901234123457"     # GS1 公开示例（与 P34 位串手抄件同一枚码）
P63_EAN8_PUBLIC = "96385074"


def _p63_complement(bits: str) -> str:
    return "".join("1" if c == "0" else "0" for c in bits)


def _p63_reverse(bits: str) -> str:
    return bits[::-1]


def _p63_check_digit(data: str) -> str:
    """GS1 加权和算法独立复算：自右向左权重 3,1 交替，校验位 = (10 − 和%10) % 10。"""
    total = sum(int(c) * (3 if i % 2 == 0 else 1) for i, c in enumerate(reversed(data)))
    return str((10 - total % 10) % 10)


def _p63_encode_ean13(digits: str) -> str:
    par = P63_EAN_PARITY[digits[0]]
    left = "".join((P63_EAN_L if p == "L" else P63_EAN_G)[d] for d, p in zip(digits[1:7], par))
    right = "".join(P63_EAN_R[d] for d in digits[7:13])
    return P63_GUARD + left + P63_MID_GUARD + right + P63_GUARD


def _p63_encode_ean8(digits: str) -> str:
    left = "".join(P63_EAN_L[d] for d in digits[:4])
    right = "".join(P63_EAN_R[d] for d in digits[4:8])
    return P63_GUARD + left + P63_MID_GUARD + right + P63_GUARD


_P63_L_REV = {v: k for k, v in P63_EAN_L.items()}
_P63_G_REV = {v: k for k, v in P63_EAN_G.items()}
_P63_R_REV = {v: k for k, v in P63_EAN_R.items()}
_P63_PAR_REV = {v: k for k, v in P63_EAN_PARITY.items()}


def _p63_decode_ean13(bits: str, l_rev: Optional[Dict[str, str]] = None,
                      g_rev: Optional[Dict[str, str]] = None,
                      r_rev: Optional[Dict[str, str]] = None,
                      par_rev: Optional[Dict[str, str]] = None) -> Optional[str]:
    """95 模块位串 → 13 位数字；结构不合即 None（诚实失败，绝不猜、绝不部分返回）。
    四张反查表可注入：仅供校准臂出具「错表必错读」的反证，真值路径一律用模块级全等表。"""
    _l = l_rev if l_rev is not None else _P63_L_REV
    _g = g_rev if g_rev is not None else _P63_G_REV
    _r = r_rev if r_rev is not None else _P63_R_REV
    _p = par_rev if par_rev is not None else _P63_PAR_REV
    if (len(bits) != 95 or bits[:3] != P63_GUARD or bits[45:50] != P63_MID_GUARD
            or bits[92:] != P63_GUARD):
        return None
    par, left_digits = "", ""
    for i in range(6):
        grp = bits[3 + 7 * i: 10 + 7 * i]
        if grp in _l:
            par += "L"
            left_digits += _l[grp]
        elif grp in _g:
            par += "G"
            left_digits += _g[grp]
        else:
            return None
    right_digits = ""
    for i in range(6):
        grp = bits[50 + 7 * i: 57 + 7 * i]
        if grp not in _r:
            return None
        right_digits += _r[grp]
    first = _p.get(par)
    if first is None:
        return None
    return first + left_digits + right_digits


def _p63_decode_ean8(bits: str) -> Optional[str]:
    """67 模块位串 → 8 位数字（EAN-8 左半全 L、右半全 R，无 parity）。
    结构偏移由 GS1 布局现场算出：护条 3 + 左 4×7=28 ⇒ 中间护条落在 [31,36)，
    右半 4×7=28 落在 [36,64)，末护条 [64,67)。
    （2026-10-11 追加改核：本函数首版把中间护条写成 bits[32:37]、右半起点写成 37，
     是把 EAN-13 的 [45,50) 心算平移所致；RED 驱动首跑当场读出「EAN-8 腿公开例与
     逐位扫段全为 None」。p63_eval_red.txt 为覆写式收据，首跑字节已被修仪器后的第二跑取代，
     故成因另落独立复量表 p63_ean8_offset_probe.txt（正对照=新偏移从像素读回公开例、
     旧形复现=同一批像素喂给 [32:37]/起点 37 的退役形状读不回、布局实证=产品 _ean8_bits
     与 P34 手抄件都在 [31:36]）。**红的是我这把新尺子自己**，不是产品；
     按纪律改的是仪器偏移，断言一条没动。）"""
    if (len(bits) != 67 or bits[:3] != P63_GUARD or bits[31:36] != P63_MID_GUARD
            or bits[64:] != P63_GUARD):
        return None
    out = ""
    for i in range(4):
        grp = bits[3 + 7 * i: 10 + 7 * i]
        if grp not in _P63_L_REV:
            return None
        out += _P63_L_REV[grp]
    for i in range(4):
        grp = bits[36 + 7 * i: 43 + 7 * i]
        if grp not in _P63_R_REV:
            return None
        out += _P63_R_REV[grp]
    return out


def _p63_bar_geometry(path: str):
    """像素 → (x0, y, scale, bar_w)：条区竖带自首个含暗像素的行起、取连续暗行的首段
    （条码是一条连续暗带，若 draw_text 则人读文字带在更下方、与首段分离）；x0 取该带
    中线首个暗列（一维码模块 0 恒为护条黑模块），scale 由响应申报值带入，
    bar_w 由末个暗列派生——三者全由像素量出，不硬编码产品边距常量。"""
    from PIL import Image
    im = Image.open(path)
    try:
        w, h = im.size
        px = im.convert("L").load()
        dark_rows = []
        for y in range(h):
            hit = False
            for x in range(w):
                if px[x, y] < 128:
                    hit = True
                    break
            dark_rows.append(hit)
        if True not in dark_rows:
            return None
        y0 = dark_rows.index(True)
        y1 = y0
        while y1 + 1 < h and dark_rows[y1 + 1]:
            y1 += 1
        y = (y0 + y1) // 2
        dark_cols = [x for x in range(w) if px[x, y] < 128]
        if not dark_cols:
            return None
        return {"x0": dark_cols[0], "y": y, "bar_first_dark_row": y0,
                "bar_last_dark_row": y1, "x_last_dark": dark_cols[-1],
                "bar_w": dark_cols[-1] - dark_cols[0] + 1, "size": (w, h)}
    finally:
        im.close()


def _p63_read_bits(path: str, scale: int, n_mods: int) -> Tuple[Optional[str], Dict[str, Any]]:
    """按派生几何逐模块采首个像素的黑白 → 模块位串（采点=每模块左边缘，scale>=2 时稳）。"""
    if scale <= 0:
        return None, {"scale": scale, "reason": "申报倍数为零，无法定采样栅格"}
    geo = _p63_bar_geometry(path)
    if geo is None:
        return None, {"reason": "像素内无暗区"}
    x0, y = geo["x0"], geo["y"]
    from PIL import Image
    im = Image.open(path)
    try:
        px = im.convert("L").load()
        if x0 + scale * (n_mods - 1) >= im.size[0]:
            # 图宽容不下 EAN-13 采样窗（例如把 EAN-8 图喂给 95 模块解码腿）⇒ 截断到能采的位数，
            # 仍**喂给被测解码器**，靠结构不合拒收；绝不在调用侧用尺寸闸绕过被测函数。
            n_mods = max(0, (im.size[0] - x0) // scale)
        bits = "".join("1" if px[x0 + scale * m, y] < 128 else "0" for m in range(n_mods))
    finally:
        im.close()
    geo_out = dict(geo)
    geo_out["scale"] = scale
    geo_out["n_mods_sampled"] = n_mods
    return bits, geo_out


class _P37MockHTTP:
    """Phase 37 评测专用进程内 mock HTTP：每用例独立 127.0.0.1:0 临时端口
    （与本机在线服务零端口冲突），routes/seen 逐实例隔离，用毕必 shutdown。"""

    def __init__(self):
        import http.server
        import socketserver
        import threading
        self.routes: Dict[str, Tuple[int, bytes, str]] = {}
        self.seen: List[Dict[str, Any]] = []
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                outer.seen.append({"method": "GET", "path": self.path,
                                   "auth": self.headers.get("Authorization", "")})
                self._reply()

            def do_POST(self):
                n = int(self.headers.get("Content-Length", 0) or 0)
                body = self.rfile.read(n) if n else b""
                outer.seen.append({"method": "POST", "path": self.path,
                                   "auth": self.headers.get("Authorization", ""),
                                   "body": body})
                self._reply()

            def _reply(self):
                r = outer.routes.get(self.path)
                if r is None:
                    self.send_response(404)
                    self.end_headers()
                    self.wfile.write(b'{"error":"no route"}')
                    return
                status, payload, ctype = r
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *a):
                pass

        self._server = socketserver.TCPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    def shutdown(self):
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2.0)


def _lic_array_key(m):
    # Phase 36：以真实签发路径签一枚"载荷为 JSON 数组"的合法票据，
    # 专核 verify 的"载荷结构无效：签名载荷必须是 JSON 对象"防御分支（签名本身必须为真）
    import hmac as _h
    import hashlib as _hsh
    body = json.dumps([1, 2], separators=(",", ":"), sort_keys=True).encode()
    sig = _h.new(m._secret_key(), body, _hsh.sha256).digest()
    return "ZAOJING." + m._b64e(body) + "." + m._b64e(sig)


class EvaluationEngine:
    """能力评估中心：真实黄金用例 → 真实通过率 → 真实报告持久化与对比。

    engines 由宿主注入（agent_service 传已装配的单例，避免重复状态）；
    独立自测时（store_dir + 无注入）引擎自行惰性实例化同族引擎。
    """

    def __init__(self, store_dir: Optional[Path] = None,
                 color=None, layout=None, packaging=None, brand=None,
                 print_qa=None, knowledge_rag=None,
                 review=None, revision=None, security=None, workflow=None,
                 license=None, config=None, model_router=None):
        self.store_dir = Path(store_dir) if store_dir else Path(__file__).resolve().parent / "_eval_store"
        self.report_dir = self.store_dir / "reports"
        self.artifact_dir = self.store_dir / "artifacts"
        self._eng = {"color": color, "layout": layout, "packaging": packaging, "brand": brand,
                     "print_qa": print_qa, "knowledge_rag": knowledge_rag,
                     "review": review, "revision": revision, "security": security,
                     "workflow": workflow, "license": license, "config": config,
                     "model_router": model_router}
        self._typo_prov_cache: Optional[Dict[str, Any]] = None

    # -- 引擎解析（注入优先，缺失时惰性导入同目录引擎做独立评测） --
    def _engine(self, name: str):
        if self._eng.get(name) is None:
            if name == "color":
                import color_engine as m
                self._eng[name] = m.ColorEngine(self.store_dir / "_color")
            elif name == "layout":
                import layout_engine as m
                self._eng[name] = m.LayoutEngine()
            elif name == "packaging":
                import packaging_engine as m
                self._eng[name] = m.PackagingEngine(self.store_dir / "_packaging")
            elif name == "brand":
                import brand_engine as m
                self._eng[name] = m.BrandEngine(color=self._engine("color"))
            elif name == "print_qa":
                import print_qa_engine as m
                self._eng[name] = m.PrintDoctor()
            elif name == "knowledge_rag":
                import knowledge_rag_engine as m
                self._eng[name] = m.KnowledgeEngine(self.store_dir / "_kb")
            elif name == "review":
                import design_review_engine as m
                self._eng[name] = m.DesignReviewEngine()  # 不传 store_dir：评测零落盘副作用
            elif name == "revision":
                import revision_engine as m
                self._eng[name] = m.RevisionEngine()
            elif name == "security":
                import security_engine as m
                self._eng[name] = m.EnhancedPathSecurityChecker(
                    ["C:\\Windows", "C:\\Program Files", "/etc", "/usr"])
            elif name == "workflow":
                import workflow_engine as m
                # 评测态新构造（不注入服务单例）：零注册工具使"未注册即诚实失败"语义
                # 与错误文案精确可核；引擎纯内存、评测零落盘副作用
                self._eng[name] = m.WorkflowEngine()
            elif name == "license":
                import license_service as m
                # 模块即引擎：签发/验签为模块级 HMAC-SHA256 纯函数，无实例状态可污染
                self._eng[name] = m
            elif name == "config":
                import config_persistence as m
                # 模块即引擎：UserSettingsManager 逐用例在评测私有沙箱 _config 下全新构造
                # （先 rmtree 后建，防磁盘旧态复活，同 Phase 32 asset 纪律）
                self._eng[name] = m
            elif name == "model_router":
                import model_router as m
                # 模块即引擎：注册表为评测自供 YAML 夹具，逐用例在私有 _router 沙箱下
                # 全新解析构造 registry+router，请求计数/预算状态跨用例零串扰
                self._eng[name] = m
            elif name == "update":
                import update_engine as m
                # 模块即引擎（Phase 37）：UpdateEngine 实例逐用例在私有 _update 沙箱下
                # 先清后建全新构造；网络面由评测自起 127.0.0.1:0 进程内 mock HTTP 驱动
                self._eng[name] = m
            elif name == "nas":
                import nas_engine as m
                # 模块即引擎（Phase 37）：NasEngine 逐用例私有 _nas 沙箱先清后建，
                # 文件共享夹具为评测自造真目录（filesystem 形态零网络）
                self._eng[name] = m
            elif name == "provider":
                import ai_provider_engine as m
                # 模块即引擎（Phase 37）：ProviderEngine 逐用例私有 _provider 沙箱先清后建，
                # chat/health 走评测自起 mock HTTP，401 与熔断以请求计数 seen 逐数钉死
                self._eng[name] = m
            elif name == "compare":
                import design_compare_engine as m
                # 模块即引擎（Phase 38，第 23 引擎）：compare_objects 为对象列表进出的纯函数，
                # 零 I/O、零实例状态、零时钟——真值面确定，跨语境（顶层/嵌套循环）逐字段全等
                self._eng[name] = m
            elif name == "bg_removal":
                import background_removal_engine as m
                # 模块即引擎（Phase 38，第 24 引擎）：泛洪为纯整数 BFS 运算；夹具为评测自造
                # Pillow 真像素图，产物逐用例落私有 _bg 沙箱先清后建（Phase 32 纪律）
                self._eng[name] = m
            elif name == "panel_recognition":
                import panel_recognition_engine as m
                # 模块即引擎（Phase 47，第 25 引擎）：recognize_panels 每次新建引擎实例，
                # 全程零实例状态/零时钟/零随机；夹具为评测自绘真像素刀版，逐用例落私有
                # _panel 沙箱先清后建（Phase 32 纪律）
                self._eng[name] = m
            elif name == "installer_manifest":
                import installer_manifest_engine as m
                # 模块即引擎（Phase 49，第 26 引擎）：verify_script 为纯只读文本+磁盘校验，
                # install_state 只读注册表/目录；机器相关项一律经模块级接缝（_path_exists/
                # _glob_matches/_env_dir/_enum_uninstall_keys/_read_uninstall_value）在受测
                # 进程内桩换并 finally 还原，夹具逐用例落私有 _manifest 沙箱先清后建
                self._eng[name] = m
            elif name == "desktop_hub":
                import desktop_hub_engine as m
                # 模块即引擎（Phase 50，第 27 引擎）：hub_snapshot/render_rows/perform_action
                # 均为对进程内 mock HTTP 的真实 urllib 调用，零实例状态；判分字段经
                # _hub_norm 把随机端口归一成 "BASE"，故顶层/嵌套两次逐字段全等（P37 纪律）。
                # 窗口本体（tkinter）刻意不在此导入——评测台绝不建窗，见 NOT_EVALUATED["gui"]
                self._eng[name] = m
            elif name == "installer_runtime":
                import installer_runtime_engine as m
                # 模块即引擎（Phase 58 执行器）：install_plan/execute_install/execute_uninstall/
                # execution_state 均为模块级纯编排函数，机器相关量一律经 roots=/registry=/spawn=/
                # build_ledger=/ledger_path= 五枚显式接缝在受测进程内注入，故本域**零子进程、
                # 本机真实落点零触碰**；假根沙箱落系统临时根（store_dir 在源码树内，会被执行器
                # 自己的 install_dir_inside_source_tree 门拒），逐用例先清后建（Phase 32 纪律）
                self._eng[name] = m
            elif name == "adobe_bridge":
                import adobe_bridge_engine as m
                # 模块即引擎（Phase 79 golden 化，第 19 引擎）：AdobeBridgeEngine 是需在私有
                # 沙箱 state_dir 下逐用例全新构造的类（用例内 m.AdobeBridgeEngine(state_dir=...)），
                # 握手/协商/下发/取命令/对账均为纯进程内 async 对象方法，零真实 socket、
                # 零外部依赖、零文档操作（真值来自 §8/§9/§25 协议 round-trip 对账读数，非宿主真改文档）
                self._eng[name] = m
            elif name == "service_registry":
                import service_registry_engine as m
                # 模块即引擎（Phase 80 golden 化，第 28 引擎）：ServiceRegistryEngine 是需
                # 在私有沙箱下逐用例全新构造的类（用例内 m.ServiceRegistryEngine(data_root=...)），
                # 功能开关/工具注册表/技能校验均为纯文件系统 + 内存派生运算，零网络、零时钟、
                # 零随机、跨实例零串扰；真值来自"无宿主上报则 ps.*/ai.* 的 runnable_now 恒 False"
                # 这一确定性可执行性派生（不伪造宿主），与损坏回退/越权拒落盘等诚实降级读数。
                self._eng[name] = m
            elif name == "event":
                import event_engine as m
                # 模块即引擎（Phase 81 golden 化，第 29 引擎）：EventHub 需在黄金用例内以
                # 私有实例全新构造（用例内 m.EventHub(history=...)），publish/connect/recent
                # 均为纯进程内 async 方法——零真实 socket、零网络、零时钟依赖（除 ts 落账），
                # 环形缓冲截断与运行时台账（published_total/kind_counts）确定性可核；真值来自
                # "无存活连接则 delivered_to 恒 0（绝不伪造交付量）"与"截断只削 history 不动台账"。
                self._eng[name] = m
            elif name == "delivery":
                import delivery_engine as m
                # 模块即引擎（Phase 81 golden 化，第 30 引擎）：DeliveryEngine 需在私有 data_root
                # 沙箱下逐用例全新构造（用例内 m.DeliveryEngine(root)），build/tally_skipped 为纯
                # 文件系统 + 哈希 + ZIP 归档运算，零网络、零时钟串扰；真值来自磁盘实物重算 SHA256
                # == 自报、ZIP namelist==收录+README+manifest、拒收三种形制分账（缺席≠0）等可第三方
                # 复核的磁盘副作用——换桩（返回常量 manifest）必致重算对账红。
                self._eng[name] = m
            else:
                raise EvaluationError(f"未知被测引擎: {name}")
        return self._eng[name]

    # -- 品牌用例真实像素夹具（Pillow 确定性合成，非模拟数据；无 Pillow 即如实抛错判 fail） --
    def _brand_fixture(self) -> Dict[str, str]:
        from PIL import Image
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        two = self.artifact_dir / "eval_brand_two_band.png"
        im = Image.new("RGB", (100, 100))
        px = im.load()
        for y in range(100):
            for x in range(100):
                px[x, y] = (30, 140, 60) if y < 50 else (240, 30, 30)
        im.save(two)
        red = self.artifact_dir / "eval_brand_red_only.png"
        Image.new("RGB", (100, 100), (240, 30, 30)).save(red)
        logo = self.artifact_dir / "eval_brand_logo_rect.png"
        lg = Image.new("RGBA", (200, 100), (0, 0, 0, 0))
        for y in range(25, 75):
            for x in range(50, 150):
                lg.putpixel((x, y), (11, 122, 162, 255))
        lg.save(logo)
        return {"two_band": str(two), "red": str(red), "logo": str(logo)}

    # -- 印前用例真像素夹具（Pillow 确定性合成：低分 RGB 与合规 CMYK 各一，落盘真文件） --
    def _print_fixture(self) -> Dict[str, str]:
        from PIL import Image
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        rgb = self.artifact_dir / "eval_print_rgb72.png"
        Image.new("RGB", (60, 60), (200, 30, 30)).save(rgb, dpi=(72, 72))
        cmyk = self.artifact_dir / "eval_print_cmyk300.jpg"
        Image.new("CMYK", (200, 200), (0, 100, 100, 0)).save(cmyk, dpi=(300, 300))
        return {"rgb": str(rgb), "cmyk": str(cmyk)}

    # -- 素材用例夹具（Pillow 确定性纯色图，落评测私有 artifacts；文件名即 embedding 描述符来源，
    #    必须与钉死的 "coffee_mug png image/png image" 逐字符对应，不得随意改名） --
    def _asset_fixture(self) -> Dict[str, str]:
        from PIL import Image
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        mug = self.artifact_dir / "coffee_mug.png"
        Image.new("RGB", (120, 80), (139, 0, 0)).save(mug, "PNG")
        pot = self.artifact_dir / "cooking_pot.jpg"
        Image.new("RGB", (40, 40), (0, 0, 255)).save(pot, "JPEG")
        return {"mug": str(mug), "pot": str(pot)}

    # -- 素材引擎逐用例全新沙箱：_load_databases 会从磁盘复活旧 JSON 状态，
    #    故每次先 rmtree 同名旧目录，保证跨运行/跨用例确定性；落盘仅在评测私有 _asset 下 --
    def _asset_eng(self, tag):
        import asset_engine as aem
        root = self.store_dir / "_asset" / tag
        shutil.rmtree(root, ignore_errors=True)
        return aem.AssetEngine(root)

    # -- 恢复引擎逐用例全新沙箱（快照/状态/文档同置于评测私有 _recovery 下），
    #    文档字节为固定常量，sha256 可离线手工复算 --
    RECOVERY_DOC = b"brand-lockup v1\nrect 10,20 100x50 #8B0000\n" * 3

    def _recovery_env(self, tag):
        import recovery_engine as rem
        root = self.store_dir / "_recovery" / tag
        shutil.rmtree(root, ignore_errors=True)
        doc = root / "doc.txt"
        doc.parent.mkdir(parents=True, exist_ok=True)
        doc.write_bytes(self.RECOVERY_DOC)
        return rem.CrashRecoveryEngine(root / "snaps", root / "states"), doc

    # -- 授权用例固定载荷：签发为 HMAC-SHA256 确定性纯函数（密钥为模块内置常量，
    #    服务装配面零覆盖），同载荷两次票据字节全等本身即真值判分对象 --
    LIC_PAYLOAD = {"edition": "pro", "mid": "*", "seats": 3, "customer": "eval-p36"}

    # -- 配置引擎逐用例全新沙箱（UserSettingsManager 显式 config_path 落评测私有 _config，
    #    先 rmtree 后建防旧文件复活，同 Phase 32 asset 纪律） --
    def _config_env(self, tag):
        import config_persistence as cpm
        root = self.store_dir / "_config" / tag
        shutil.rmtree(root, ignore_errors=True)
        root.mkdir(parents=True)
        cfgp = root / "user-settings.json"
        return cpm.UserSettingsManager(config_path=cfgp), cfgp, root

    # -- 路由用例自供注册表夹具（真 YAML 文件走 ModelRegistry 真实解析路径；两模型两
    #    provider，cost 平票由插入序裁决、latency 由 circuit_breaker 均值裁决，全显式） --
    ROUTER_FIXTURE_YAML = """
models:
  - name: m_free
    type: text
    provider: local
    context_window: 8192
    max_output_tokens: 4096
    avg_latency_ms: 500
    input_cost_per_1k: 0.0
    output_cost_per_1k: 0.0
  - name: m_pro
    type: text
    provider: doubao
    context_window: 32768
    max_output_tokens: 8192
    avg_latency_ms: 1200
    input_cost_per_1k: 0.002
    output_cost_per_1k: 0.006
providers:
  - name: p_local
    type: local
    api_endpoint: "http://localhost:11434"
    api_key_encrypted: ""
    rate_limit: 60
    timeout_seconds: 120
    circuit_breaker: {failure_threshold: 2, reset_timeout_seconds: 60, half_open_requests: 3, avg_latency_ms: 500}
  - name: p_cloud
    type: doubao
    api_endpoint: "https://ark.example.invalid"
    api_key_encrypted: ""
    rate_limit: 100
    timeout_seconds: 60
    daily_budget: 100.0
    circuit_breaker: {failure_threshold: 5, reset_timeout_seconds: 60, half_open_requests: 3, avg_latency_ms: 120}
"""

    def _router_env(self, tag):
        """逐用例全新 registry+router（评测私有 _router 沙箱先清后建；选路纯内存、
        未发起请求的 provider 不读挂钟，判分确定）"""
        import model_router as mrm
        root = self.store_dir / "_router" / tag
        shutil.rmtree(root, ignore_errors=True)
        root.mkdir(parents=True)
        p = root / "registry.yaml"
        p.write_text(self.ROUTER_FIXTURE_YAML, encoding="utf-8")
        reg = mrm.ModelRegistry(str(p))
        return mrm, reg, mrm.ModelRouter(reg)

    # -- Phase 37 三域逐用例全新沙箱（先 rmtree 后建，防磁盘旧态复活，同 Phase 32 纪律） --
    def _update_env(self, tag):
        import update_engine as m
        root = self.store_dir / "_update" / tag
        shutil.rmtree(root, ignore_errors=True)
        return m.UpdateEngine(state_dir=root), root

    def _nas_env(self, tag):
        import nas_engine as m
        root = self.store_dir / "_nas" / tag
        shutil.rmtree(root, ignore_errors=True)
        return m.NasEngine(state_dir=root), root

    def _nas_fix(self, tag):
        """文件共享夹具：share 下固定字节两文件一子目录，outside 为越界禁区（评测自造真目录）"""
        share = self.store_dir / "_nas" / (tag + "_share")
        shutil.rmtree(share, ignore_errors=True)
        (share / "sub").mkdir(parents=True)
        (share / "a.txt").write_bytes(b"12345")
        (share / "b.bin").write_bytes(b"0123456789ab")
        outside = self.store_dir / "_nas" / (tag + "_outside")
        shutil.rmtree(outside, ignore_errors=True)
        outside.mkdir()
        (outside / "secret.txt").write_bytes(b"DO-NOT-READ")
        return share, outside

    def _provider_env(self, tag):
        import ai_provider_engine as m
        root = self.store_dir / "_provider" / tag
        shutil.rmtree(root, ignore_errors=True)
        return m.ProviderEngine(state_dir=root), root

    # -- Phase 38 去背景夹具：评测自造 100×100 真像素图（(200,30,30) 背景 + (30..69)² 蓝方块
    #    + (40..47)² 背景色包围孔洞），固定字节存 _bg/_fix 一次生成后逐用例 copy2 复用；
    #    孔洞是"泛洪≠全局色键"的可证伪判分载体 --
    def _bg_source(self):
        fixdir = self.store_dir / "_bg" / "_fix"
        fixdir.mkdir(parents=True, exist_ok=True)
        src = fixdir / "subject.png"
        if not src.exists():
            from PIL import Image
            im = Image.new("RGB", (100, 100), (200, 30, 30))
            px = im.load()
            for y in range(30, 70):
                for x in range(30, 70):
                    px[x, y] = (20, 20, 220)
            for y in range(40, 48):
                for x in range(40, 48):
                    px[x, y] = (200, 30, 30)
            im.save(src, "PNG")
        return src

    def _bg_sandbox(self, tag):
        root = self.store_dir / "_bg" / tag
        shutil.rmtree(root, ignore_errors=True)
        root.mkdir(parents=True)
        return root

    # -- Phase 47 面板识别夹具：评测自绘刀版，与引擎自测夹具相互独立（不同画幅、不同
    #    划分、不同虚线常数）。3×2 网格：外框 1px 实线=cut，内部 2 竖 1 横虚线（划 9 档 4）
    #    =fold；内区 cols 11..207 被 col 76/142 三等分为 65/65/65、rows 11..89 被 row 50
    #    二等分为 39/39 → 6 面板各 65×39=2535px（面积/邻接/轮廓全部手算可核）。
    #    逐字段期望由 p47_eval_probe.py 从磁盘实测冻结，夹具字节以 sha256 双向钉死 --
    PANEL_FIXTURE_SHA = {
        "grid3x2.png": "5046d5be354e8ca2b7df1aa505381085fc7d838f3ef6b364fc5261f7ec5d30d7",
        "grid3x2_thick5.png":
            "b6ef1d9947bd58a9c7b10ec05ec2b81ea659d1b36568af9e5165f0aeae698da8",
        "blank.png": "81c4fdc7f26741c79dddb858162a8541504e170cbfef183d4322fc811e0b519e",
        "open_cross.png": "1037b070f2a31d06dc9843aafeb142255db2db002decfe38494429372b00c781",
    }

    def _panel_sandbox(self, tag):
        root = self.store_dir / "_panel" / tag
        shutil.rmtree(root, ignore_errors=True)
        root.mkdir(parents=True)
        return root

    def _panel_fixture(self, root, name):
        from PIL import Image, ImageDraw
        p = Path(root) / name
        black = (0, 0, 0)
        im = Image.new("RGB", (200, 120) if name == "open_cross.png" else (220, 100),
                       (255, 255, 255))
        if name != "blank.png":
            d = ImageDraw.Draw(im)
            if name in ("grid3x2.png", "grid3x2_thick5.png"):
                stroke = 1 if name == "grid3x2.png" else 5
                d.rectangle([10, 10, 208, 90], outline=black, width=stroke)
                for col in (76, 142):
                    y = 11
                    while y <= 89:
                        y2 = min(y + 8, 89) if stroke == 1 else 89
                        d.line([col, y, col, y2], fill=black, width=stroke)
                        y = y2 + (4 if stroke == 1 else 1)
                x = 11
                while x <= 207:
                    x2 = min(x + 8, 207) if stroke == 1 else 207
                    d.line([x, 50, x2, 50], fill=black, width=stroke)
                    x = x2 + (4 if stroke == 1 else 1)
            else:  # open_cross.png：一横（row 60）一竖（col 100）虚线，无封闭外框
                x = 10
                while x <= 190:
                    x2 = min(x + 8, 190)
                    d.line([x, 60, x2, 60], fill=black)
                    x = x2 + 4
                y = 10
                while y <= 110:
                    y2 = min(y + 8, 110)
                    d.line([100, y, 100, y2], fill=black)
                    y = y2 + 4
        im.save(p, "PNG")
        assert (hashlib.sha256(p.read_bytes()).hexdigest()
                == self.PANEL_FIXTURE_SHA[name]), "夹具字节漂移: " + name
        return p

    # -- Phase 49 安装清单夹具：评测自造 .iss 文本 + 真实载荷文件（与引擎自测夹具、生产
    #    setup.iss 三方相互独立）。合规夹具零检出；破损夹具的 12 项检出逐项序、逐 severity、
    #    逐 evidence 字段由 p49_eval_probe.py（Phase 49）与 p53_eval_refreeze_probe.py
    #    （Phase 53 扩规则后）从磁盘实测冻结，Phase 58 并入 R14 后由 p58_r14_eval_probe.py
    #    与 p58_counts_probe.py 重新量过一遍（夹具一改必须回磁盘重量，不照旧值放行）。破损夹具刻意用 {tmp} 预建目录
    #    （Inno 合法常量但无环境映射，永不解析）与数据根求交，使 R7 结论机器无关。
    #    Phase 53 口径：合规夹具的形制不再由评测台凭空想象，而是照抄**本机真 iscc 编译通过
    #    （exit=0、0 告警）的生产 setup.iss** 的四条形制——AppId 首花括号双写（单写实测
    #    exit=2 Unknown constant，R6 转义臂）、PrivilegesRequired=lowest 与 HKCU/per-user
    #    区配对（缺省即 admin，真编译器当场告警，R11）、AppVersion 在场（删除实测 exit=2，
    #    R13）、ArchitecturesAllowed/InstallIn64BitMode=x64compatible（写 x64 时编译器自行
    #    替换并告警弃用，R12 合规臂）--
    MF_CLEAN_SCRIPT = "\n".join([
        "[Setup]",                                       # 1
        "AppName=ZaojingAI",                             # 2
        "AppVersion=1.0.0",                              # 3 → R13 合规臂
        "AppId={{11112222-3333-4444-5555-666677778888}",  # 4 → R6 转义合规臂
        "DefaultDirName={autopf}\\ZaojingAI",            # 5
        "PrivilegesRequired=lowest",                     # 6 → R11 配对合规臂
        "ArchitecturesAllowed=x64compatible",            # 7 → R12 合规臂
        "ArchitecturesInstallIn64BitMode=x64compatible",  # 8 → R12 合规臂
        "; 注释行与空行不参与条目计数",                     # 9
        "",                                              # 10
        "[Files]",                                       # 11
        'Source: "zaojing.exe"; DestDir: "{app}"; Flags: ignoreversion',        # 12
        'Source: "docs\\上手指南.pdf"; DestDir: "{app}\\docs"; Flags: ignoreversion \\',  # 13
        "        onlyifdoesntexist",                     # 14（续行，行号归起始 13）
        "[Icons]",                                       # 15
        'Name: "{group}\\ZaojingAI"; Filename: "{app}\\zaojing.exe"; WorkingDir: "{app}"',
        'Name: "{group}\\卸载"; Filename: "{uninstallexe}"',
        "[Registry]",                                    # 18
        'Root: HKCU; Subkey: "Software\\Zaojing"; Flags: uninsdeletekeyifempty',  # 19 → R14 合规臂
        'Root: HKCU; Subkey: "Software\\Zaojing\\ZaojingAI"; Flags: uninsdeletekey',  # 20 → R14 合规臂
        'Root: HKCU; Subkey: "Software\\Zaojing\\ZaojingAI"; ValueType: string; '
        'ValueName: "InstallLocation"; \\',                                    # 21
        'ValueData: "{app}"',                                                  # 22（续行，行号归 21）
    ])

    MF_BROKEN_SCRIPT = "\n".join([
        "[Setup]",                              # 1
        "AppName=ZaojingAI",                    # 2
        "AppVersion=1.0.0",                     # 3（R13 合规臂：缺它才会多出 error）
        "AppId={9A8B7C6D-5E4F-3G2H-1I0J-K9L8M7N6O5P4}",   # 4 → R6 warn
        "DefaultDirName={autopf}\\ZaojingAI",   # 5
        "LicenseFile=",                         # 6 → R9 info（空值需 iscc 定论）
        "[Files]",                              # 7
        'Source: "absent.exe"; DestDir: "{app}"; Flags: ignoreversion',     # 8 → R2 error
        'Source: "libs\\*.dll"; DestDir: "{app}\\libs"; Flags: ignoreversion',  # 9 → R3 error
        "[Icons]",                              # 10
        'Name: "{group}\\{app_name}"; Filename: "{app}\\zaojing.exe"',      # 11 → R1 error
        'Name: "{group}\\卸载"; Filename: "{autopf}\\ZaojingAI\\Uninstall.exe"',  # 12 → R4 error
        "[Dirs]",                               # 13
        'Name: "{tmp}\\ZaojingAI\\Assets"; Permissions: usersfull',         # 14 → R7 warn + R10 error
        "[Registry]",                           # 15
        'Root: HKCU; Subkey: "Software\\ZaojingAI"; ValueType: string; '
        'ValueName: "InstallDate"; ValueData: "{install_date}"',            # 16 → R1 + R8 + R11
    ])  # 行号注释即实测物理行号：R1/R7 两项 evidence 无 line 字段（按 token/section 归并）；
        # R11 的 line 取 PrivilegesRequired 声明行，此处缺声明故为 0（缺省即 admin）

    MF_SHAPE_SCRIPT = "\n".join([
        "[Setup]",
        "AppName=ZaojingAI",
        "AppVersion=1.0.0",
        "AppId={{11112222-3333-4444-5555-666677778888}",
        "DefaultDirName={autopf}\\ZaojingAI",
        "PrivilegesRequired=lowest",
        "[Dirs]",
        'Name: "{localappdata}\\ZaojingAI\\Assets"; Permissions: users-modify',
    ])

    MF_APPID = "{11112222-3333-4444-5555-666677778888}"
    # 脚本里的写法与生效值不是一回事：Inno 要求首花括号双写，解包后才得到 MF_APPID。
    # 键名派生必须与转义状态无关（Phase 53 改双写时若漂移，只读安装态探测永远查不中）。
    MF_APPID_SCRIPT_FORM = "{{11112222-3333-4444-5555-666677778888}"
    # findings 排序键为 (id, subject, line)：install/uninstall 同为 u 开头时按 'unins'<'unres'
    MF_BROKEN_IDS = ["appid_not_uuid", "dirs_miss_runtime_data_root",
                     "files_source_literal_missing", "files_source_wildcard_no_match",
                     "needs_compiler_adjudication", "per_user_area_privileges_mismatch",
                     "permissions_entry_malformed", "registry_key_left_after_uninstall",
                     "registry_valuedata_literal_placeholder", "uninstall_target_mismatch",
                     "unresolved_template_constant", "unresolved_template_constant"]
    MF_BROKEN_SEVS = ["warn", "warn", "error", "error", "info", "warn", "error", "error",
                      "error", "error", "error", "error"]

    # -- Phase 53 三条开火臂专用夹具：合规侧已由 MF_CLEAN_SCRIPT 实证（四臂零检出），破损侧
    #    却需要**各只改一处形制**的独立脚本，否则一条检出掩盖另一条。行号注释即物理行号，
    #    全部由 p53_eval_face_probe.py / p53_face_probe2.py 从磁盘实测冻结。--
    MF_R6_UNESC_SCRIPT = "\n".join([
        "[Setup]",                                       # 1
        "AppName=ZaojingAI",                             # 2
        "AppVersion=1.0.0",                              # 3
        "AppId={11112222-3333-4444-5555-666677778888}",  # 4 → 单写首括号：真 iscc 实测 exit=2
        "DefaultDirName={autopf}\\ZaojingAI",            # 5
        "PrivilegesRequired=lowest",                     # 6
    ])
    MF_R12_ARCH_SCRIPT = "\n".join([
        "[Setup]",                                       # 1
        "AppName=ZaojingAI",                             # 2
        "AppVersion=1.0.0",                              # 3
        "AppId={{11112222-3333-4444-5555-666677778888}",  # 4
        "DefaultDirName={autopf}\\ZaojingAI",            # 5
        "PrivilegesRequired=lowest",                     # 6
        "ArchitecturesAllowed=x64",                      # 7 → 弃用标识：编译器自行替换并告警
        "ArchitecturesInstallIn64BitMode=x64",           # 8 → 同规则第二条 directive 独立开火
    ])
    MF_R13_NOVER_SCRIPT = "\n".join([
        "[Setup]",                                       # 1
        "AppName=ZaojingAI",                             # 2
        "AppId={{11112222-3333-4444-5555-666677778888}",  # 3
        "DefaultDirName={autopf}\\ZaojingAI",            # 4
        "PrivilegesRequired=lowest",                     # 5
    ])  # AppVersion 与 AppVerName 双双缺席：真 iscc 实测 exit=2
    MF_R13_VERNAME_SCRIPT = "\n".join([
        "[Setup]",                                       # 1
        "AppName=ZaojingAI",                             # 2
        "AppVerName=造境 AI 设计智能体 1.0.0",             # 3 → 二选一即可（either_suffices）
        "AppId={{11112222-3333-4444-5555-666677778888}",  # 4
        "DefaultDirName={autopf}\\ZaojingAI",            # 5
        "PrivilegesRequired=lowest",                     # 6
    ])
    MF_R11_FIRE_SCRIPT = "\n".join([
        "[Setup]",                                       # 1
        "AppName=ZaojingAI",                             # 2
        "AppVersion=1.0.0",                              # 3
        "AppId={{11112222-3333-4444-5555-666677778888}",  # 4
        "DefaultDirName={autopf}\\ZaojingAI",            # 5
        "[Registry]",                                    # 6
        'Root: HKCU; Subkey: "Software\\ZaojingAI"; ValueType: string; '
        'ValueName: "k"; ValueData: "v"; Flags: uninsdeletevalue',   # 7 → per-user 区
    ])  # 全程不声明 PrivilegesRequired：Inno 缺省即 admin，真编译器为此出告警（R11 开火臂）
    MF_R11_OK_SCRIPT = "\n".join([
        "[Setup]",                                       # 1
        "AppName=ZaojingAI",                             # 2
        "AppVersion=1.0.0",                              # 3
        "AppId={{11112222-3333-4444-5555-666677778888}",  # 4
        "DefaultDirName={autopf}\\ZaojingAI",            # 5
        "PrivilegesRequired=lowest",                     # 6 → 唯一变量：声明与区域配对
        "[Registry]",                                    # 7
        'Root: HKCU; Subkey: "Software\\ZaojingAI"; ValueType: string; '
        'ValueName: "k"; ValueData: "v"; Flags: uninsdeletevalue',   # 8
    ])
    # -- Phase 58 R14 开火臂夹具：写入型 [Registry] 条目 × 卸载撤销声明。立项依据是本机
    #    真卸载（unins000.exe exit 0、安装根与卸载子键归零、三枚值原样留守）。夹具彼此
    #    只差 [Registry] 段的形制，其余逐字相同 ⇒ "差异即缺陷"可被读出来。--
    MF_R14_RESIDUE_SCRIPT = "\n".join([
        "[Setup]",                                       # 1
        "AppName=ZaojingAI",                             # 2
        "AppVersion=1.0.0",                              # 3
        "AppId={{11112222-3333-4444-5555-666677778888}",  # 4
        "DefaultDirName={autopf}\\ZaojingAI",            # 5
        "PrivilegesRequired=lowest",                     # 6
        "[Registry]",                                    # 7
        'Root: HKCU; Subkey: "Software\\Zaojing\\ZaojingAI"; ValueType: string; '
        'ValueName: "InstallLocation"; ValueData: "{app}"',                    # 8 → R14 error
        'Root: HKCU; Subkey: "Software\\Zaojing\\ZaojingAI"; ValueType: string; '
        'ValueName: "Version"; ValueData: "1.0.0"',                             # 9 → 同键第二条
    ])
    MF_R14_KEY_SCRIPT = "\n".join([                          # 发货形制：父键 ifempty + 应用键整键删
        "[Setup]", "AppName=ZaojingAI", "AppVersion=1.0.0",
        "AppId={{11112222-3333-4444-5555-666677778888}",
        "DefaultDirName={autopf}\\ZaojingAI", "PrivilegesRequired=lowest",
        "[Registry]",                                        # 7
        'Root: HKCU; Subkey: "Software\\Zaojing"; Flags: uninsdeletekeyifempty',   # 8
        'Root: HKCU; Subkey: "Software\\Zaojing\\ZaojingAI"; Flags: uninsdeletekey',  # 9
        'Root: HKCU; Subkey: "Software\\Zaojing\\ZaojingAI"; ValueType: string; '
        'ValueName: "InstallLocation"; ValueData: "{app}"',                    # 10 → 已被覆盖
    ])
    MF_R14_PARENT_DELKEY_SCRIPT = "\n".join([          # 祖先整键删：连同子键一并撤销 ⇒ 零检出
        "[Setup]", "AppName=ZaojingAI", "AppVersion=1.0.0",
        "AppId={{11112222-3333-4444-5555-666677778888}",
        "DefaultDirName={autopf}\\ZaojingAI", "PrivilegesRequired=lowest",
        "[Registry]",
        'Root: HKCU; Subkey: "Software\\Zaojing"; Flags: uninsdeletekey',       # 8
        'Root: HKCU; Subkey: "Software\\Zaojing\\ZaojingAI"; ValueType: string; '
        'ValueName: "InstallLocation"; ValueData: "{app}"',
    ])
    MF_R14_PARENT_IFEMPTY_SCRIPT = "\n".join([        # 父键只有 ifempty ⇒ 带值子键令父键永不为空
        "[Setup]", "AppName=ZaojingAI", "AppVersion=1.0.0",
        "AppId={{11112222-3333-4444-5555-666677778888}",
        "DefaultDirName={autopf}\\ZaojingAI", "PrivilegesRequired=lowest",
        "[Registry]",
        'Root: HKCU; Subkey: "Software\\Zaojing"; Flags: uninsdeletekeyifempty',
        'Root: HKCU; Subkey: "Software\\Zaojing\\ZaojingAI"; ValueType: string; '
        'ValueName: "InstallLocation"; ValueData: "{app}"',
    ])
    MF_R14_IFEMPTY_SAME_KEY_SCRIPT = "\n".join([      # ifempty 写在应用键自身也顶不住（键非空）
        "[Setup]", "AppName=ZaojingAI", "AppVersion=1.0.0",
        "AppId={{11112222-3333-4444-5555-666677778888}",
        "DefaultDirName={autopf}\\ZaojingAI", "PrivilegesRequired=lowest",
        "[Registry]",
        'Root: HKCU; Subkey: "Software\\Zaojing\\ZaojingAI"; Flags: uninsdeletekeyifempty',
        'Root: HKCU; Subkey: "Software\\Zaojing\\ZaojingAI"; ValueType: string; '
        'ValueName: "InstallLocation"; ValueData: "{app}"',
    ])
    MF_R14_NEVER_SCRIPT = "\n".join([                 # 显式让渡：有意保留共享键 ⇒ 不属残留
        "[Setup]", "AppName=ZaojingAI", "AppVersion=1.0.0",
        "AppId={{11112222-3333-4444-5555-666677778888}",
        "DefaultDirName={autopf}\\ZaojingAI", "PrivilegesRequired=lowest",
        "[Registry]",
        'Root: HKCU; Subkey: "Software\\Zaojing\\ZaojingAI"; ValueType: string; '
        'ValueName: "InstallLocation"; ValueData: "{app}"; Flags: uninsneveruninstall',
    ])
    MF_R14_CROSS_ROOT_SCRIPT = "\n".join([            # 撤销挂在 HKLM ⇒ HKCU 那一路仍无人撤销
        "[Setup]", "AppName=ZaojingAI", "AppVersion=1.0.0",
        "AppId={{11112222-3333-4444-5555-666677778888}",
        "DefaultDirName={autopf}\\ZaojingAI", "PrivilegesRequired=lowest",
        "[Registry]",
        'Root: HKLM; Subkey: "Software\\Zaojing\\ZaojingAI"; Flags: uninsdeletekey',
        'Root: HKCU; Subkey: "Software\\Zaojing\\ZaojingAI"; ValueType: string; '
        'ValueName: "InstallLocation"; ValueData: "{app}"',
    ])
    # 编译面证据夹具：假想 iscc 输出尾部（含三信号之一的完成行 + 可控告警行）
    MF_COMPILE_LINE = "Successful compile: 0.500 sec; Output: C:\\x\\zaojing-setup.exe"
    MF_PROBE_PAYLOAD_BYTES = len(b"zaojing-preflight-probe")

    def _mf_tail(self, warnings=()):
        lines = ["   Parsing [Setup] section, line 1", "   Reading [Code] section"]
        lines.extend("Warning: " + w for w in warnings)
        lines.append(self.MF_COMPILE_LINE)
        return "\r\n".join(lines)

    def _mf_evidence(self, script_path, artifact_path, warnings=(),
                     recorded="auto", output_chars="auto", tail="auto", **over):
        """构造构建证据：**字节数与 sha256 由本用例用 hashlib 独立重算**，绝不从引擎返回值
        里抄（抄回来的对账只能证明引擎和自己一致，证不了它对磁盘实况一致）。"""
        sb = Path(script_path).read_bytes()
        ab = Path(artifact_path).read_bytes()
        t = self._mf_tail(warnings) if tail == "auto" else tail
        rec = ([ln.strip() for ln in t.splitlines()
                if ln.strip().startswith(("Warning", "Error", "Deprecated"))][:len(warnings)]
               if recorded == "auto" else recorded)
        ev = {
            "schema": "zaojing-installer-build/v1",
            "script_path": str(script_path), "script_bytes": len(sb),
            "script_sha256": hashlib.sha256(sb).hexdigest(),
            "artifact_path": str(artifact_path), "artifact_bytes": len(ab),
            "artifact_sha256": hashlib.sha256(ab).hexdigest(),
            "iscc_exit_code": 0, "compiler_path": None,
            "iscc_output_tail": t, "iscc_output_chars": len(t) if output_chars == "auto"
            else output_chars,
            "iscc_warnings": rec, "iscc_compile_line": None,
            "manifest_verdict": "manifest_ok", "compiled": True, "fabricated": False,
            "built_at": "2026-09-29 00:00:00",
        }
        import installer_manifest_engine as _ime
        ev["compiler_path"] = _ime.locate_compiler().get("path")
        ev.update(over)
        return ev

    def _manifest_build_sandbox(self, tag, script=None, artifact=b"MZ\x90\x00" + b"a" * 64):
        """编译面/预检面共用沙箱：脚本 + 声明载荷 + 一枚"已编译产物"落盘文件。

        产物是评测自造的字节串（不代表真安装器）——编译面判的是"证据与磁盘是否对得上"，
        与产物内容是不是真安装器无关；真编译实证在 installer/build-evidence.json 与 L2/L4。
        """
        sd = self._manifest_sandbox(tag)
        self._manifest_payloads(sd)
        sp = Path(sd) / "setup.iss"
        sp.write_text(script if script is not None else self.MF_CLEAN_SCRIPT,
                      encoding="utf-8")
        ap = Path(sd) / "zaojing-ai-1.0.0-setup.exe"
        ap.write_bytes(artifact)
        return sd, sp, ap

    def _manifest_sandbox(self, tag):
        root = self.store_dir / "_manifest" / tag
        shutil.rmtree(root, ignore_errors=True)
        root.mkdir(parents=True)
        return root

    def _manifest_payloads(self, sd):
        """合规夹具的真实载荷：磁盘上确有这两个文件，R2 才会走"存在"分支。"""
        (Path(sd) / "docs").mkdir(parents=True)
        (Path(sd) / "zaojing.exe").write_bytes(b"MZ\x90\x00fixture")
        (Path(sd) / "docs" / "上手指南.pdf").write_bytes(b"%PDF-1.4\n")
        return sd

    # ====================================================================
    # Phase 58：安装执行面夹具（installer_execution 黄金域）
    # ====================================================================
    # 与 L1 的分工互不冒充：L1 用**真实子进程**跑两枚假安装器/假卸载器，证的是"门与派生
    # 确实活在生产代码路径上"；评测台刻意**零子进程**——只经 execute_install /
    # execute_uninstall 的 spawn=/registry= 注入接缝，在进程内复现安装器的落盘效应与
    # 注册表回读，判的是纯数据即可决定的部分：门序与拒绝码、差集派生、自报一律不作数、
    # 三形制降级、台账现场重算对账。两侧共用同一套判定代码，任何一侧通过都不证明另一侧。
    #
    # 夹具形制照**本机真装实况**而非 setup.iss 的字面声明，两处差异都是历轮假信号的宿主：
    #   · 安装根 %LOCALAPPDATA%\Programs\造境科技 与 vendor_dir 是同一物理目录
    #     （⇒ 差集若按"落点标签×文件"计数就会把同一枚文件数两遍）
    #   · 数据根的日志目录是小写 logs（产品 logger 先建），[Dirs] 声明的是 Logs
    #     （⇒ NTFS 大小写不敏感，按字面成员判会把"在场"读成"缺失"）
    EXEC_APP_ID_SOURCE_FORM = "{{D4C8A1F2-7B3E-4E5A-9C6D-2F1B8A47E3D5}"      # .iss 源文（双写首括号）
    EXEC_UNINSTALL_KEY_REAL = "{D4C8A1F2-7B3E-4E5A-9C6D-2F1B8A47E3D5}_is1"   # 注册表里真存的键
    EXEC_APP_REG_KEY = "Software\\造境科技\\ZaojingAI"
    # 下面两枚抄自**本机真装当轮从注册表原样读回的值**（Phase 58，winreg 只读探针），
    # 不是抄自 setup.iss 的字面声明：
    #   · EXEC_UNINSTALL_REG_PATH —— Inno 的卸载项落在 Uninstall 根**之下**，全仓库派生的
    #     键名是叶子形态，执行器若拿叶子当 HKCU 相对全路径去开就永远看不见它（真缺陷第 4 枚）
    #   · EXEC_DISPLAY_NAME_MEASURED —— 未声明 UninstallDisplayName 时 Inno 写的是
    #     AppName+" "+AppVersion；旧断言要求逐字等于 app_name，是在验夹具（真缺陷第 5 枚）
    EXEC_UNINSTALL_REG_ROOT = r"Software\Microsoft\Windows\CurrentVersion\Uninstall"
    EXEC_UNINSTALL_REG_PATH = EXEC_UNINSTALL_REG_ROOT + "\\" + EXEC_UNINSTALL_KEY_REAL
    EXEC_DISPLAY_NAME_MEASURED = "ZaojingAI 1.0.0"
    EXEC_ARTIFACT = b"MZ\x90\x00" + b"eval-exec-artifact" * 4
    EXEC_SELF_REPORT = {"returncode": 0,
                        "stdout_tail": "Installation finished. All components installed.",
                        "stderr_tail": ""}
    EXEC_INSTALL_CHECKS = ["install_dir_was_empty_before", "process_exit_zero",
                           "installer_log_written", "exe_in_install_root",
                           "uninstaller_in_install_root", "registry_uninstall_key_present",
                           "registry_app_values_match", "docs_written",
                           "start_menu_group_present", "data_dirs_present"]
    EXEC_CONFIRM = "ZAOJING-CONFIRM-EXECUTE-INSTALLER"   # 手抄：判分不读引擎常量，防同源失真
    EXEC_FLAGS = ["/VERYSILENT", "/NORESTART", "/SUPPRESSMSGBOXES"]
    EXEC_GATES = ["confirmation_required", "artifact_missing",
                  "artifact_outside_allowed_root", "build_ledger_unavailable",
                  "artifact_sha_mismatch_vs_build_ledger", "install_dir_not_empty",
                  "install_dir_inside_source_tree"]
    EXEC_IGNORED = ["install.installed", "install.status", "install.log.seen_finished_line",
                    "uninstall.performed", "uninstall.status", "build_ledger.compiled",
                    "build_ledger.fabricated"]

    def _exec_sandbox(self, tag):
        """执行面私有沙箱：先清后建（Phase 32 纪律——残留台账会让 ledger_absent 臂假绿）。

        沙箱落在**系统临时根**而非 self.store_dir：store_dir 在 outputs/ 里，而执行器
        _gate_install_dir 明令"安装根不得落在源码树内"（那正是本阶段要守的门），把夹具
        放在 store_dir 下会让每条真装臂一进门就被自己的门拒掉。假根与真实落点之间只隔着
        roots= 这一枚显式接缝，因此**本机真实 %LOCALAPPDATA% 全程不被触碰**。
        fake-local/fake-user/fake-appdata 三枚假根注入 engines 的 roots= 接缝；
        Zaojing/Assets 里预置一枚用户文件充当"既有数据"（绝不当测试沙箱用）。
        """
        root = Path(tempfile.gettempdir()) / "_zj_eval_exec" / tag
        shutil.rmtree(root, ignore_errors=True)
        (root / "fake-local" / "Zaojing" / "Assets").mkdir(parents=True)
        (root / "fake-user").mkdir(parents=True)
        (root / "fake-appdata").mkdir(parents=True)
        (root / "log").mkdir(parents=True)
        (root / "fake-local" / "Zaojing" / "Assets" / "user-notes.json").write_bytes(
            b'{"mine":true}')
        (root / "zaojing-ai-1.0.0-setup.exe").write_bytes(self.EXEC_ARTIFACT)
        (root / "setup.iss").write_bytes(b"iss")
        return root

    def _guard_sandbox(self, tag, keep=False):
        """守护面私有沙箱（Phase 59 路线 B）：先清后建（Phase 32 纪律）。

        与执行面同理落在**系统临时根**而非 self.store_dir：本域判据之一是"探针文件必删、
        目录残留只有 .runtime-guard 一枚"，若夹具目录与评测报告同根，残留归因就分不清是
        产品写的还是评测台写的。keep=False 时目录为空壳，由各用例自己往里造普通文件夹具；
        keep=True 只用于**必须不存在**的夹具（构造本身不建根那一臂）。
        """
        root = Path(tempfile.gettempdir()) / "_zj_eval_guard" / tag
        shutil.rmtree(root, ignore_errors=True)
        if not keep:
            root.mkdir(parents=True)
        return root

    def _exec_env(self, tag, vendor_dir="造境科技"):
        """把一次"假安装"所需的全套落点、假构建台账与注册表替身组装成显式接缝。"""
        sd = self._exec_sandbox(tag)
        roots = {"localappdata": str(sd / "fake-local"),
                 "userprofile": str(sd / "fake-user"),
                 "appdata": str(sd / "fake-appdata")}
        art = sd / "zaojing-ai-1.0.0-setup.exe"
        sha = hashlib.sha256(art.read_bytes()).hexdigest()
        bled = sd / "build-evidence.json"
        bled.write_text(json.dumps({
            "artifact_path": str(art), "artifact_bytes": art.stat().st_size,
            "artifact_sha256": sha, "script_path": str(sd / "setup.iss"),
            "script_sha256": hashlib.sha256(b"iss").hexdigest(),
            "iscc_exit_code": 0, "compiled": True, "fabricated": False}),
            encoding="utf-8", newline="\n")
        reg_state = {"uninstall": {}, "app": {}}
        return {
            "sd": sd, "roots": roots, "art": art, "sha": sha, "bled": bled,
            "install_dir": os.path.join(roots["localappdata"], "Programs", vendor_dir),
            "data_root": os.path.join(roots["localappdata"], "Zaojing"),
            "userdocs": os.path.join(roots["userprofile"], "Documents", "ZaojingAI"),
            "menu": os.path.join(roots["appdata"], "Microsoft", "Windows", "Start Menu",
                                 "Programs", "ZaojingAI"),
            "log": str(sd / "log" / "zaojing-install.log"),
            "ledger": str(sd / "exec-ledger.json"),
            "reg_state": reg_state,
            "reg": lambda: {"uninstall": dict(reg_state["uninstall"]),
                            "app": dict(reg_state["app"])},
        }

    def _exec_spawn(self, env, mode="full"):
        """进程内假安装器/假卸载器：只认真 argv 里的 /DIR= 与 /LOG=，副作用落在假根。

        mode 三臂各有明确的被验命题，且**返回的自报体逐字节相同**——否则"信自报"就被
        夹具差异掩盖了：
          full         与本机真装同形地落盘并写注册表替身（数据根日志目录刻意叫 logs）
          self_report  只写那行 "Installation finished" 日志，磁盘与注册表一个字节不动
          noop         什么都不做（卸载臂里代表"卸载器提前退出而现场未清"）
        """
        def call(argv, log_path=None):
            tgt = next((str(a)[5:] for a in argv if str(a).startswith("/DIR=")), "")
            lg = next((str(a)[5:] for a in argv if str(a).startswith("/LOG=")),
                      log_path or env["log"])
            first = os.path.basename(str(argv[0])).lower()
            uninstaller = "unins" in first
            if mode == "noop":
                return dict(self.EXEC_SELF_REPORT)
            if uninstaller:
                # 卸载器不吃 /DIR=：真 Inno 的 unins000.exe 从**注册表卸载项**里取
                # InstallLocation（L1 的真子进程假卸载器同此一条）。若在这里也从 argv 读，
                # 就等于给夹具开了一条引擎从未给卸载器的后门。
                tgt = str((env["reg_state"].get("uninstall") or {}).get("InstallLocation") or "")
                for p in (tgt, env["userdocs"], env["menu"]):
                    if p:
                        shutil.rmtree(p, ignore_errors=True)
                for sub in ("Assets", "Cache", "Logs", "config"):
                    try:
                        os.rmdir(os.path.join(env["data_root"], sub))  # 非空即失败：与 Inno 同形
                    except OSError:
                        pass
                env["reg_state"].pop("uninstall", None)
                env["reg_state"].pop("app", None)
                env["reg_state"].setdefault("uninstall", {})
                env["reg_state"].setdefault("app", {})
                return dict(self.EXEC_SELF_REPORT)
            if mode == "self_report":
                Path(lg).write_text("2026-01-01 00:00:00.000   Installation finished\n",
                                    encoding="utf-8", newline="\n")
                return dict(self.EXEC_SELF_REPORT)
            d = Path(tgt)
            d.mkdir(parents=True, exist_ok=True)
            (d / "ZaojingAI.exe").write_bytes(b"MZ" + b"fake-app-binary" * 7)
            (d / "unins000.exe").write_bytes(b"MZ" + b"fake-uninstaller" * 6)
            docs = Path(env["userdocs"])
            docs.mkdir(parents=True, exist_ok=True)
            (docs / "安装说明.md").write_text("# fake doc\n", encoding="utf-8", newline="\n")
            menu = Path(env["menu"])
            menu.mkdir(parents=True, exist_ok=True)
            (menu / "ZaojingAI.lnk").write_bytes(b"LNK" * 9)
            data = Path(env["data_root"])
            for sub in ("Assets", "Cache", "config", "logs"):
                (data / sub).mkdir(parents=True, exist_ok=True)
            if lg:
                Path(lg).parent.mkdir(parents=True, exist_ok=True)
                Path(lg).write_text("2026-01-01 00:00:00.000   Installation finished\n",
                                    encoding="utf-8", newline="\n")
            env["reg_state"]["uninstall"] = {
                "DisplayName": self.EXEC_DISPLAY_NAME_MEASURED, "DisplayVersion": "1.0.0",
                "UninstallString": '"%s"' % (d / "unins000.exe"), "InstallLocation": tgt}
            env["reg_state"]["app"] = {"Version": "1.0.0", "InstallLocation": tgt,
                                       "InstallDate": "01-01-2026"}
            return dict(self.EXEC_SELF_REPORT)
        return call

    def _exec_listing(self, sd):
        """沙箱逐条清单（相对 posix 名 + 字节 + sha256）：只读面"零落盘"与门前拒绝的落证。"""
        out = []
        for p in sorted(Path(sd).rglob("*")):
            rel = p.relative_to(sd).as_posix()
            if p.is_dir():
                out.append([rel, "d", 0, ""])
            else:
                out.append([rel, "f", p.stat().st_size,
                            hashlib.sha256(p.read_bytes()).hexdigest()[:16]])
        return out

    def _exec_rel(self, sd, paths):
        """绝对路径清单 → 相对沙箱的 posix 串。判分字段绝不含绝对路径（跨机不变形）。

        落在沙箱外的路径不猜、不裁，统一折成 "<outside>/文件名"，让"跑出沙箱"这件事本身
        在判分字段里可见——静默丢弃会把越界写成"没有越界"。
        """
        out = []
        for p in paths:
            q = Path(str(p))
            try:
                out.append(q.relative_to(sd).as_posix())
            except ValueError:
                out.append("<outside>/" + q.name)
        return out

    def _exec_kw(self, env, mode="full", **over):
        """execute_install 的标准入参：假根/假注册表/假进程/允许根/台账五枚接缝一次接满。

        uninstall_key / app_reg_key 一律交**叶子形态**（与 install_plan 派生出的那枚同源），
        补全 Uninstall 根是执行器的责任，不是调用方的——评测因此能看见 registry_observation
        里回显的到底是补全后的路径还是原始叶子。
        """
        kw = {"artifact": str(env["art"]), "install_dir": env["install_dir"],
              "roots": env["roots"], "registry": env["reg"],
              "spawn": self._exec_spawn(env, mode),
              "allow_roots": [str(env["sd"])], "ledger_path": env["ledger"],
              "log_path": env["log"], "build_ledger": str(env["bled"]),
              "uninstall_key": self.EXEC_UNINSTALL_KEY_REAL,
              "app_reg_key": self.EXEC_APP_REG_KEY}
        kw.update(over)
        return kw

    def _exec_un_kw(self, env, mode="full", **over):
        """execute_uninstall 的标准入参（无 /LOG=：卸载器日志由卸载器自己决定，不属本契约）。"""
        kw = {"install_dir": env["install_dir"], "roots": env["roots"],
              "registry": env["reg"], "spawn": self._exec_spawn(env, mode),
              "allow_roots": [str(env["sd"])], "ledger_path": env["ledger"],
              "uninstall_key": self.EXEC_UNINSTALL_KEY_REAL,
              "app_reg_key": self.EXEC_APP_REG_KEY}
        kw.update(over)
        return kw

    def _exec_state_kw(self, env, **over):
        """execution_state 的标准入参：台账 + 假根 + 假注册表 + 构建台账 + 两枚键名现场接缝。"""
        kw = {"ledger_path": env["ledger"], "registry": env["reg"], "roots": env["roots"],
              "build_ledger": str(env["bled"]),
              "uninstall_key": self.EXEC_UNINSTALL_KEY_REAL,
              "app_reg_key": self.EXEC_APP_REG_KEY}
        kw.update(over)
        return kw

    # -- 排版用例自供夹具：fontTools FontBuilder 现场编译两枚真 TTF（固定 cmap/hmtx/垂直度量；
    #    Plane-15 专码位 U+F0010/U+F00FF 经本机全量字体反查确认无真字体覆盖，BMP PUA 与
    #    U+F0001/F0002 均被实机字体撞覆盖已被证伪弃用），断掉对本机字体的依赖 --
    TYPO_P1 = "\U000F0010"
    TYPO_P2 = "\U000F00FF"
    TYPO_CM_SERIF = {0x20: "space", 0x48: "H", 0x65: "e", 0x6C: "l", 0x6F: "o",
                     0xF0010: "uniF0010", 0xF00FF: "uniF00FF"}
    TYPO_CM_SANS = {0x20: "space", 0x48: "H", 0x65: "e", 0x6C: "l", 0x6F: "o",
                    0xF0010: "uniF0010"}
    TYPO_W_SERIF = {"space": 250, "H": 700, "e": 500, "l": 300, "o": 550,
                    "uniF0010": 999, "uniF00FF": 640}
    TYPO_W_SANS = {"space": 260, "H": 640, "e": 520, "l": 280, "o": 560, "uniF0010": 900}

    def _typo_build_font(self, path, family, style, ps, weight, ascent, descent,
                         widths, cmap, upem=1000):
        from fontTools.fontBuilder import FontBuilder
        from fontTools.pens.ttGlyphPen import TTGlyphPen
        fb = FontBuilder(upem, isTTF=True)
        names = list(dict.fromkeys([".notdef"] + sorted(set(cmap.values()))))
        fb.setupGlyphOrder(names)
        fb.setupCharacterMap(cmap)
        glyphs = {n: TTGlyphPen(None).glyph() for n in names}
        fb.setupGlyf(glyphs, calcGlyphBounds=False)
        fb.setupHorizontalMetrics({n: (widths.get(n, 500), 0) for n in names})
        fb.setupHorizontalHeader(ascent=ascent, descent=descent)
        fb.setupNameTable({"familyName": family, "styleName": style, "psName": ps})
        fb.setupOS2(sTypoAscender=ascent, sTypoDescender=descent, usWeightClass=weight,
                    usWinAscent=ascent, usWinDescent=-descent)
        fb.setupPost()
        fb.save(str(path))

    def _typo_prov(self) -> Dict[str, Any]:
        """溯源解析：真编译夹具 TTF → 引擎 A 以 include_os=False 只扫夹具目录（同一条真实
        fontTools 解析路径，不触本机字体目录故秒级完成），产出真 FontRecord，实例内缓存供
        逐用例重放；夹具未真实解析到 2 枚即如实抛错判 fail，绝不降级伪造记录。
        （专码位与本机字体的不撞车性由 Phase 33 探针以真机全量字体反查钉死，见模块头注释。）"""
        if self._typo_prov_cache is not None:
            return self._typo_prov_cache
        import typography_engine as tem
        fix = self.store_dir / "_typo" / "_fix"
        shutil.rmtree(fix, ignore_errors=True)
        fix.mkdir(parents=True)
        self._typo_build_font(fix / "zjfix_serif.ttf", "ZJFixSerif", "Bold", "ZJFixSerif-Bold",
                              700, 900, -300, self.TYPO_W_SERIF, self.TYPO_CM_SERIF)
        self._typo_build_font(fix / "zjfix_sans.ttf", "ZJFixSans", "Regular", "ZJFixSans-Regular",
                              400, 800, -200, self.TYPO_W_SANS, self.TYPO_CM_SANS)
        prov = tem.TypographyEngine(self.store_dir / "_typo" / "_provenance",
                                    extra_dirs=[str(fix)])
        prov.rescan(include_os=False)
        recs = {r["postscript_name"]: r for r in prov.list_fonts(family="ZJFix")}
        if len(recs) != 2:
            raise EvaluationError(f"夹具字体真实解析数不符: {sorted(recs)}")
        self._typo_prov_cache = recs
        return recs

    def _typo_env(self, tag):
        """逐用例全新沙箱（typography 同 asset/recovery 为评测私有沙箱）：重放索引仅含两枚
        夹具记录（id 重映射 fix_serif/fix_sans、file_path 指向本用例副本），ensure_loaded 读
        索引不重扫 → 本机字体零干扰；后续 _tt/cmap/hmtx 全部真实解析真字节文件，零桩。
        rmtree 故意不 ignore_errors——跨轮 tag 复用时上一引擎的 lazy TTFont 句柄会锁夹具
        文件（Windows WinError 32），用例未 close() 即在此如实炸出，绝不静默漏删旧沙箱。"""
        import typography_engine as tem
        recs = self._typo_prov()
        root = self.store_dir / "_typo" / tag
        if root.exists():
            shutil.rmtree(root)
        fonts_dir = root / "fonts"
        fonts_dir.mkdir(parents=True)
        idx: Dict[str, Any] = {}
        for key, ps in (("fix_serif", "ZJFixSerif-Bold"), ("fix_sans", "ZJFixSans-Regular")):
            rec = dict(recs[ps])
            dst = fonts_dir / Path(rec["file_path"]).name
            shutil.copy2(rec["file_path"], dst)
            rec["id"] = key
            rec["file_path"] = str(dst)
            idx[key] = rec
        (root / "fonts_index.json").write_text(
            json.dumps({"fonts": idx}, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        return tem.TypographyEngine(root, extra_dirs=[str(fonts_dir)])

    # ====================================================================
    # 黄金用例集：每条 run(engines) -> (ok, details)
    # ====================================================================

    def _cases(self) -> List[Dict[str, Any]]:
        def case(cid, category, title, run, must_fail=False):
            return {"id": cid, "category": category, "title": title,
                    "run": run, "must_fail": must_fail}

        # ---------- DESIGN：color 引擎 ----------
        def c_contrast_extreme(E):
            r = E["color"].contrast("#000000", "#FFFFFF")
            ok = _close(r["ratio"], 21.0, 1e-9) and r["wcag"]["AAA_normal"] is True
            return ok, {"expected": {"ratio": 21.0, "AAA_normal": True},
                        "actual": {"ratio": r["ratio"], "AAA_normal": r["wcag"]["AAA_normal"]}}

        def c_contrast_gray_band(E):
            r = E["color"].contrast("#777777", "#FFFFFF")
            # 真实 WCAG 真值：#777777/白 对比 4.478 —— 不足 AA 正文(4.5)、够 AA 大字(3.0)
            ok = (4.0 < r["ratio"] < 4.5 and _close(r["ratio"], 4.478, 0.001)
                  and r["wcag"]["AA_normal"] is False and r["wcag"]["AA_large"] is True
                  and r["wcag"]["AAA_normal"] is False)
            return ok, {"expected": "4.478±0.001 且 AA正文不过/AA大字过", "actual": {"ratio": r["ratio"], "wcag": r["wcag"]}}

        def c_convert_red(E):
            v = E["color"].convert("#FF0000")
            ok = (v["rgb"] == {"r": 255, "g": 0, "b": 0} and v["cmyk"]["c"] == 0.0
                  and _close(v["cmyk"]["m"], 100.0) and v["cmyk"]["k"] == 0.0)
            return ok, {"expected": "rgb(255,0,0)/cmyk(0,100,0,0)",
                        "actual": {"rgb": v["rgb"], "cmyk": v["cmyk"]}}

        def c_delta_e_identity_and_distance(E):
            d0 = E["color"].delta_e("#FF0000", "#FF0000")["delta_e"]
            d1 = E["color"].delta_e("#FF0000", "#0000FF")["delta_e"]
            ok = _close(d0, 0.0, 1e-6) and d1 > 50
            return ok, {"expected": "同色ΔE=0 且 红蓝ΔE>50", "actual": {"self": d0, "red_blue": d1}}

        def c_harmony_triadic(E):
            h = E["color"].harmony("#3366CC", "triadic")
            hexes = [c["hex"].upper() for c in h["colors"]]
            ok = len(hexes) == 3 and hexes[0] == "#3366CC" and len(set(hexes)) == 3
            return ok, {"expected": "三色且互异、首位=基准", "actual": {"colors": hexes}}

        def c_nearest_in_palette(E):
            r = E["color"].nearest_in_palette("#FF0505", ["#00FF00", "#FF0000", "#0000FF"])
            top = r["matches"][0] if r["matches"] else {}
            ok = top.get("hex", "").upper() == "#FF0000" and top.get("delta_e", 999) < 3
            return ok, {"expected": "最近色=#FF0000 且 ΔE<3", "actual": top}

        # ---------- DESIGN：layout 引擎 ----------
        def l_collide_overlap(E):
            objs = [{"id": "A", "x": 0, "y": 0, "width": 50, "height": 50},
                    {"id": "B", "x": 40, "y": 0, "width": 50, "height": 50},
                    {"id": "C", "x": 200, "y": 200, "width": 10, "height": 10}]
            r = E["layout"].collide(objs)
            pair = r["collisions"][0] if r["collisions"] else {}
            # 手工真值：重叠 10x50=500；iou=500/(2500+2500-500)=1/9
            ok = (r["object_count"] == 3 and r["collision_count"] == 1
                  and pair.get("pair") == ["A", "B"] and _close(pair.get("overlap_area", -1), 500.0, 0.01)
                  and _close(pair.get("iou", -1), 0.1111, 0.001))
            return ok, {"expected": {"count": 1, "pair": ["A", "B"], "overlap_area": 500, "iou": 0.1111},
                        "actual": {"count": r["collision_count"], "pair": pair.get("pair"),
                                   "overlap_area": pair.get("overlap_area"), "iou": pair.get("iou")}}

        def l_collide_clean(E):
            objs = [{"id": "A", "x": 0, "y": 0, "width": 20, "height": 20},
                    {"id": "B", "x": 30, "y": 0, "width": 20, "height": 20}]
            r = E["layout"].collide(objs)
            return r["collision_count"] == 0, {"expected": 0, "actual": r["collision_count"]}

        def l_align_left(E):
            objs = [{"id": "t", "x": 10, "y": 5, "width": 100, "height": 20},
                    {"id": "s", "x": 47, "y": 30, "width": 60, "height": 12},
                    {"id": "i", "x": 25, "y": 60, "width": 80, "height": 40}]
            r = E["layout"].align(objs, "left")
            ok = (r["aligned"] == 3 and all(_close(o["to"]["x"], 10.0, 1e-6) for o in r["objects"])
                  and all(o["dy"] == 0 for o in r["objects"]))
            return ok, {"expected": "3 项全部移到 x=10 且 y 不动",
                        "actual": [{"id": o["id"], "to_x": o["to"]["x"], "dy": o["dy"]} for o in r["objects"]]}

        def l_safe_area_violation(E):
            objs = [{"id": "ok", "x": 50, "y": 50, "width": 100, "height": 50},
                    {"id": "bad", "x": 5, "y": 100, "width": 40, "height": 40}]
            r = E["layout"].safe_area(objs, {"width": 500, "height": 400}, margin=20)
            vids = [v["id"] for v in r["violations"]]
            ok = r["violation_count"] == 1 and vids == ["bad"] and "left" in r["violations"][0]["sides"]
            return ok, {"expected": {"violation_count": 1, "ids": ["bad"], "sides": ["left"]},
                        "actual": {"violation_count": r["violation_count"], "ids": vids,
                                   "sides": r["violations"][0]["sides"] if r["violations"] else []}}

        # ---------- PRODUCTION：packaging 引擎 ----------
        def p_dieline_boxes(E):
            r = E["packaging"].dieline(100, 80, bleed=3, safe=5)
            ok = (r["bleed_box"] == [-3.0, -3.0, 103.0, 83.0]
                  and _close(r["areas"]["trim_area"], 8000.0) and _close(r["areas"]["bleed_area"], 9116.0)
                  and _close(r["areas"]["safe_area"], 6300.0))
            return ok, {"expected": {"bleed_box": [-3, -3, 103, 83], "trim": 8000, "bleed": 9116, "safe": 6300},
                        "actual": {"bleed_box": r["bleed_box"], "areas": r["areas"]}}

        def p_imposition_known_fit(E):
            r = E["packaging"].imposition(100, 80, 250, 1000, 800, gap=0)
            # 手工真值：直放 10x10=100 up；250 件 → 3 张、耗 50 槽；利用率 250*8000/(3*800000)
            ok = (r["feasible"] and r["n_up"] == 100 and r["sheets_needed"] == 3
                  and r["wasted_slots"] == 50 and _close(r["sheet_utilization_percent"], 83.33, 0.01))
            return ok, {"expected": {"n_up": 100, "sheets": 3, "wasted": 50, "util%": 83.33},
                        "actual": {"n_up": r["n_up"], "sheets": r["sheets_needed"],
                                   "wasted": r["wasted_slots"], "util%": r["sheet_utilization_percent"]}}

        def p_barcode_ean13_render(E):
            self.artifact_dir.mkdir(parents=True, exist_ok=True)
            out = self.artifact_dir / "eval_barcode_ean13.png"
            r = E["packaging"].render_barcode("590123412345", "EAN13", output_path=str(out))
            exists = out.exists()
            magic = out.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n" if exists else False
            ok = (r["status"] == "ok" and r["code"] == "5901234123457" and r["check_digit"] == "7"
                  and r["module_count"] == 95 and exists and magic and r["bytes"] > 0)
            return ok, {"expected": "EAN13 补校验位 7 / 95 模块 / 真实 PNG 落盘",
                        "actual": {"code": r.get("code"), "check_digit": r.get("check_digit"),
                                   "module_count": r.get("module_count"), "png": bool(magic),
                                   "bytes": r.get("bytes")}}

        def p_barcode_bad_checkdigit_rejected(E):
            # 真实校验位算法的负向面：非法校验位必须抛 ValueError 而非静默出图
            try:
                E["packaging"].render_barcode("5901234123450", "EAN13",
                                              output_path=str(self.artifact_dir / "never.png"))
                return False, {"expected": "ValueError(校验位非法)", "actual": "无异常，静默出图"}
            except ValueError as e:
                return True, {"expected": "ValueError", "actual": f"ValueError: {e}"}

        def p_panel_topology_row(E):
            panels = [{"id": "L", "x": 0, "y": 0, "width": 100, "height": 80},
                      {"id": "C", "x": 100, "y": 0, "width": 100, "height": 80},
                      {"id": "R", "x": 200, "y": 0, "width": 100, "height": 80}]
            r = E["packaging"].analyze_panel_topology(panels)
            # 手工真值：2 条共边折线；度 L1/C2/R1；外轮廓 3*360-2*160=760
            ok = (r["status"] == "ok" and r["is_connected"] and r["relation_count"] == 2
                  and r["degrees"] == {"L": 1, "C": 2, "R": 1}
                  and _close(r["cut_boundary_length"], 760.0, 1e-6)
                  and _close(r["extent"]["width"], 300.0, 1e-6))
            return ok, {"expected": {"relations": 2, "degrees": {"L": 1, "C": 2, "R": 1}, "cut_len": 760},
                        "actual": {"relations": r["relation_count"], "degrees": r["degrees"],
                                   "cut_len": r["cut_boundary_length"], "extent_w": r["extent"]["width"]}}

        def p_panel_topology_disconnected(E):
            panels = [{"id": "A", "x": 0, "y": 0, "width": 50, "height": 50},
                      {"id": "B", "x": 200, "y": 0, "width": 50, "height": 50}]
            r = E["packaging"].analyze_panel_topology(panels)
            ok = r["status"] == "disconnected" and r["group_count"] == 2 and r["relation_count"] == 0
            return ok, {"expected": {"status": "disconnected", "groups": 2},
                        "actual": {"status": r["status"], "groups": r["group_count"]}}

        # ---------- BARCODE 类别（Phase 34：packaging_barcode 升格独立黄金域，位串对 GS1 手抄全等 + 真像素采样） ----------
        BOX9_PANELS = [
            {"id": "glue", "x": -15, "y": 0, "width": 15, "height": 100},
            {"id": "front", "x": 0, "y": 0, "width": 60, "height": 100},
            {"id": "right", "x": 60, "y": 0, "width": 40, "height": 100},
            {"id": "back", "x": 100, "y": 0, "width": 60, "height": 100},
            {"id": "left", "x": 160, "y": 0, "width": 40, "height": 100},
            {"id": "top_f", "x": 0, "y": 100, "width": 60, "height": 30},
            {"id": "top_b", "x": 100, "y": 100, "width": 60, "height": 30},
            {"id": "bot_f", "x": 0, "y": -30, "width": 60, "height": 30},
            {"id": "bot_b", "x": 100, "y": -30, "width": 60, "height": 30},
        ]

        def _bc(E, code, sym, fname, **kw):
            self.artifact_dir.mkdir(parents=True, exist_ok=True)
            out = self.artifact_dir / fname
            return E["packaging"].render_barcode(code, sym, output_path=str(out), **kw), out

        def _qr(E, payload, fname, **kw):
            # QR 走 render_barcode 统一入口（服务与工具面同一路径），缺省按 ISO 静区 4 模块
            kw.setdefault("ec_level", "M")
            kw.setdefault("scale", 2)
            kw.setdefault("quiet", 4)
            return _bc(E, payload, "QR", fname, **kw)

        def bc_ean13_bitstring_exact(E):
            r, out = _bc(E, "590123412345", "EAN13", "eval_p34_bc13.png")
            ok = (r["status"] == "ok" and r["code"] == "5901234123457"
                  and r["check_digit"] == "7" and r["module_count"] == 95
                  and r["bit_pattern"] == P34_EAN13_BITS and r["width_px"] == 234
                  and r["height_px"] == 104 and r["fabricated"] is False and out.exists())
            return ok, {"expected": {"code": "5901234123457", "modules": 95,
                                     "bits_head": P34_EAN13_BITS[:20], "px": (234, 104)},
                        "actual": {"code": r["code"], "modules": r["module_count"],
                                   "bits_equal": r["bit_pattern"] == P34_EAN13_BITS,
                                   "bits_head": r["bit_pattern"][:20],
                                   "px": (r["width_px"], r["height_px"])}}

        def bc_ean8_bitstring_exact(E):
            r, _ = _bc(E, "9638507", "EAN8", "eval_p34_bc8.png")
            ok = (r["code"] == "96385074" and r["check_digit"] == "4"
                  and r["module_count"] == 67 and r["bit_pattern"] == P34_EAN8_BITS)
            return ok, {"expected": {"code": "96385074", "modules": 67, "bits_len": 67},
                        "actual": {"code": r["code"], "check": r["check_digit"],
                                   "bits_equal": r["bit_pattern"] == P34_EAN8_BITS,
                                   "modules": r["module_count"]}}

        def bc_pixel_truth(E):
            # 真像素逐点采样（Phase 33 教训：PIL 懒加载句柄 Windows 锁文件，采样后必 close）
            r, out = _bc(E, "590123412345", "EAN13", "eval_p34_px.png")
            from PIL import Image
            im = Image.open(out)
            try:
                px = im.load()
                # scale=2, quiet=11 → 模块 m 首像素 x=22+2m；护条 101 / 左码末位 '0' 起黑 /
                # 中间护条 01010（模块 45 白）/ 末模块 94 黑 / 静区与上留白白
                samples = {"quiet": px[5, 52], "m0": px[22, 52], "m1": px[24, 52],
                           "m2": px[26, 52], "m44": px[110, 52], "m45": px[112, 52],
                           "m94": px[210, 52], "top": px[40, 5]}
                size, mode = im.size, im.mode
            finally:
                im.close()
            ok = (size == (234, 104) and mode == "1"
                  and samples["quiet"] == 255 and samples["m0"] == 0
                  and samples["m1"] == 255 and samples["m2"] == 0
                  and samples["m44"] == 0 and samples["m45"] == 255
                  and samples["m94"] == 0 and samples["top"] == 255
                  and r["bit_pattern"][45:50] == "01010")
            return ok, {"expected": {"size": (234, 104), "mode": "1", "quiet/top/m1/m45": 255,
                                     "m0/m2/m44/m94": 0, "midguard": "01010"},
                        "actual": {"size": size, "mode": mode, "samples": samples,
                                   "midguard": r["bit_pattern"][45:50]}}

        def bc_bad_checkdigit_msg_exact(E):
            never = self.artifact_dir / "eval_p34_never.png"
            never2 = self.artifact_dir / "eval_p34_never2.png"
            for f in (never, never2):
                if f.exists():
                    f.unlink()
            msg13 = msg8 = None
            try:
                E["packaging"].render_barcode("5901234123450", "EAN13", output_path=str(never))
            except ValueError as e:
                msg13 = str(e)
            try:
                E["packaging"].render_barcode("96385070", "EAN8", output_path=str(never2))
            except ValueError as e:
                msg8 = str(e)
            ok = (msg13 == "EAN-13 校验位非法：给定 0，应为 7"
                  and msg8 == "EAN-8 校验位非法：给定 0，应为 4"
                  and not never.exists() and not never2.exists())
            return ok, {"expected": "两码制 ValueError 原文逐字精确且不产文件",
                        "actual": {"ean13": msg13, "ean8": msg8,
                                   "files": [never.exists(), never2.exists()]}}

        def bc_qr_capacity_exact(E):
            """QR 版本最小化恰落在手抄 Table 8 容量边界：满容留本版、+1 字符升下一版；
            v1/v2 单块的 data/ec 逐格对 Table 9 手抄行。"""
            cells, bad = [], []
            for lvl in ("L", "M", "Q", "H"):
                caps = P57_QR_BYTE_CAP[lvl]
                for k in (1, 2, 9, 10):
                    n = caps[k - 1]
                    r, out = _qr(E, "z" + "A" * (n - 1), "eval_p57_cap.png", ec_level=lvl)
                    if r["version"] != k:
                        bad.append("%s 满容 %d 字符应落 v%d，实为 v%s" % (lvl, n, k, r["version"]))
                    if k <= 2:
                        ec = P57_QR_EC_CW_V12[k][lvl]
                        if (r["ec_codewords"] != ec
                                or r["blocks"] != [{"count": 1, "data": P57_QR_TOTAL_CW[k] - ec,
                                                    "ec": ec}]):
                            bad.append("%s/v%d 单块 ec/data 不符手抄 Table 9：%s/%s"
                                       % (lvl, k, r["ec_codewords"], r["blocks"]))
                    if r["mode"] != "Byte" or not out.exists() or r["fabricated"] is not False:
                        bad.append("%s/v%d 模式或落盘异常" % (lvl, k))
                    cells.append("%s%d->v%d" % (lvl, n, r["version"]))
                for k in (1, 2, 9):
                    up = caps[k - 1] + 1
                    r, _ = _qr(E, "z" + "A" * (up - 1), "eval_p57_cap.png", ec_level=lvl)
                    if r["version"] != k + 1:
                        bad.append("%s %d 字符应升 v%d，实为 v%s" % (lvl, up, k + 1, r["version"]))
                    cells.append("%s%d->v%d" % (lvl, up, r["version"]))
            ok = not bad
            return ok, {"expected": {"Table8_L_cap": P57_QR_BYTE_CAP["L"],
                                     "Table9_v1_ec": P57_QR_EC_CW_V12[1],
                                     "rule": "满容留本版 / +1 升下一版"},
                        "actual": {"cells": cells, "bad": bad}}

        def bc_qr_block_structure(E):
            """40 格（v1..v10 × L/M/Q/H）码字算术全等：手抄 total、手抄容量独立式
            data==cap+(2|3)、Σ块数×(data+ec)==total、Σ分块 data/ec==申报和、side/余比特/位串长度。"""
            bad, sample = [], []
            for lvl in ("L", "M", "Q", "H"):
                for k in range(1, 11):
                    cap = P57_QR_BYTE_CAP[lvl][k - 1]
                    r, _ = _qr(E, "z" + "A" * (cap - 1), "eval_p57_blk.png", ec_level=lvl)
                    total = P57_QR_TOTAL_CW[k]
                    blk_data = sum(b["count"] * b["data"] for b in r["blocks"])
                    blk_ec = sum(b["count"] * b["ec"] for b in r["blocks"])
                    blk_all = sum(b["count"] * (b["data"] + b["ec"]) for b in r["blocks"])
                    side = 21 + 4 * (k - 1)
                    if r["version"] != k:
                        bad.append("%s/v%d 版本=%s" % (lvl, k, r["version"]))
                    if r["data_codewords"] != cap + P57_QR_CC_EXTRA[k]:
                        bad.append("%s/v%d data=%s != 手抄容量式 %s"
                                   % (lvl, k, r["data_codewords"], cap + P57_QR_CC_EXTRA[k]))
                    if r["total_codewords"] != total or blk_all != total:
                        bad.append("%s/v%d total=%s Σ块=%s != 手抄 %s"
                                   % (lvl, k, r["total_codewords"], blk_all, total))
                    if blk_data != r["data_codewords"] or blk_ec != r["ec_codewords"]:
                        bad.append("%s/v%d 分块和 %s/%s != 申报 %s/%s"
                                   % (lvl, k, blk_data, blk_ec,
                                      r["data_codewords"], r["ec_codewords"]))
                    if (r["side"] != side or r["module_count"] != side * side
                            or len(r["bit_pattern"]) != side * side
                            or r["remainder_bits"] != P57_QR_REMAINDER_BITS[k]
                            or len(r["matrix"]) != side
                            or any(len(row) != side for row in r["matrix"])):
                        bad.append("%s/v%d 几何/余比特异常 side=%s rem=%s"
                                   % (lvl, k, r["side"], r["remainder_bits"]))
                    cs = r["alignment_centers"]
                    n_ap = 2 + k // 7
                    if k < 2:
                        if cs != []:
                            bad.append("%s/v%d v1 不该有校正图形 %s" % (lvl, k, cs))
                    elif (len(cs) != n_ap or cs[0] != 6 or cs[-1] != side - 7
                          or any((cs[i + 1] - cs[i]) % 2 for i in range(n_ap - 1))
                          or max(cs[i + 1] - cs[i] for i in range(n_ap - 1))
                          - min(cs[i + 1] - cs[i] for i in range(n_ap - 1)) > 2):
                        bad.append("%s/v%d 校正图形中心 %s 不符 ISO 推导（n=2+v//7、首 6、末 side-7、"
                                   "间距偶且等距±2）" % (lvl, k, cs))
                    sample.append("%s%d:%d+%d/%s" % (lvl, k, r["data_codewords"],
                                                     r["ec_codewords"],
                                                     "x".join("%d(%d,%d)" % (b["count"], b["data"],
                                                                             b["ec"])
                                                             for b in r["blocks"])))
            ok = not bad
            return ok, {"expected": {"total(Table 7)": P57_QR_TOTAL_CW,
                                     "data==cap+(2|3)": True,
                                     "Σcount*(data+ec)==total": True},
                        "actual": {"bad": bad, "rows": [sample[0], sample[13], sample[35],
                                                        sample[39]]}}

        def bc_qr_pixel_truth(E):
            """真像素逐点采样 + 全 21×21 栅格与申报 matrix 逐格同源，静区整带必白。"""
            r, out = _qr(E, "hello", "eval_p57_qrpx.png", scale=4, quiet=4, ec_level="M")
            from PIL import Image
            im = Image.open(out)
            try:
                px = im.load()
                size, mode = im.size, im.mode

                def mod(r_, c_):
                    return px[(4 + c_) * 4 + 2, (4 + r_) * 4 + 2]

                s = {"quiet": px[4, 4],
                     "f_tl_c": mod(3, 3), "f_tl_e": mod(0, 0), "f_tl_w": mod(3, 1),
                     "sep_r7": mod(7, 0), "sep_c7": mod(0, 7),
                     "tmr_b": mod(6, 8), "tmr_w": mod(6, 9), "tmr_b2": mod(6, 10),
                     "f_br": mod(20, 20), "f_tr": mod(0, 20),
                     "dark_fixed": mod(13, 8),
                     "alt_tl": px[(4 + 0) * 4, (4 + 0) * 4]}
                mismatch = [(rr, cc) for rr in range(21) for cc in range(21)
                            if (1 if mod(rr, cc) == 0 else 0) != int(r["matrix"][rr][cc])]
                quiet_band = [px[x, y] for x in range(116) for y in (0, 15)] + \
                             [px[x, y] for y in range(116) for x in (0, 15)]
            finally:
                im.close()
            ok = (r["version"] == 1 and r["side"] == 21 and size == (116, 116) and mode == "1"
                  and s["quiet"] == 255 and s["f_tl_c"] == 0 and s["f_tl_e"] == 0
                  and s["f_tl_w"] == 255 and s["sep_r7"] == 255 and s["sep_c7"] == 255
                  and s["tmr_b"] == 0 and s["tmr_w"] == 255 and s["tmr_b2"] == 0
                  and s["f_br"] == 0 and s["f_tr"] == 0 and s["alt_tl"] == 0
                  and s["dark_fixed"] == 0
                  and mismatch == [] and set(quiet_band) == {255}
                  and r["quiet_zone_modules"] == 4 and r["scale"] == 4)
            return ok, {"expected": {"size": (116, 116), "side": 21, "quiet/scale": (4, 4),
                                     "定位符中心/角": (0, 0), "定位符内白/分隔符": (255, 255),
                                     "时序黑·白·黑": (0, 255, 0), "固定黑模块(4v+9,8)": 0,
                                     "matrix↔像素": "441 格逐格同源"},
                        "actual": {"size": size, "mode": mode, "samples": s,
                                   "mismatch_count": len(mismatch),
                                   "mismatch_head": mismatch[:6],
                                   "quiet_band_values": sorted(set(quiet_band))}}

        def bc_qr_mask_format_bch(E):
            """评测侧独立重算格式/版本 BCH 并逐字符对账，掩码必为罚分最小者；
            尺子自身先复现 ISO 三串公布例（L/mask0、M/mask0、v7 版本信息）才算可用。"""
            kat = {"L0": _p57_bch(0b01000, P57_QR_FMT_GEN, 10, P57_QR_FMT_XOR),
                   "M0": _p57_bch(0b00000, P57_QR_FMT_GEN, 10, P57_QR_FMT_XOR),
                   "v7": _p57_bch(7, P57_QR_VER_GEN, 12)}
            kat_str = {"L0": format(kat["L0"], "015b"), "M0": format(kat["M0"], "015b"),
                       "v7": format(kat["v7"], "018b")}
            ruler_ok = (kat_str["L0"] == P57_QR_FMT_L_M0
                        and kat_str["M0"] == P57_QR_FMT_M_M0
                        and kat_str["v7"] == P57_QR_VER_V7)
            bad = [] if ruler_ok else ["评测侧 BCH 尺子未复现 ISO 公布例：%s" % kat_str]
            checks = []
            for lvl, payload, fname, exp_ver in (
                    ("M", "hello", "eval_p57_fmt_m.png", 1),
                    ("L", "hello", "eval_p57_fmt_l.png", 1),
                    ("Q", "https://example.com/a", "eval_p57_fmt_q.png", None),
                    ("H", "ZAOJING 造境 57", "eval_p57_fmt_h.png", None),
                    ("L", "z" + "A" * 153, "eval_p57_fmt_v7.png", 7)):
                r, _ = _qr(E, payload, fname, ec_level=lvl)
                exp_fmt = _p57_fmt_bits(lvl, r["mask_pattern"])
                argmin = min(range(8), key=lambda i: r["mask_penalties"][i])
                bad_fmt = (r["format_bits"] != exp_fmt)
                if bad_fmt:
                    bad.append("%s/%s 格式位 %s != 重算 %s"
                               % (lvl, r["version"], r["format_bits"], exp_fmt))
                if r["mask_pattern"] != argmin or len(r["mask_penalties"]) != 8:
                    bad.append("%s 申报掩码 %s 非罚分最小 %s"
                               % (lvl, r["mask_pattern"], argmin))
                if r["version"] >= 7:
                    exp_ver_bits = format(_p57_bch(r["version"], P57_QR_VER_GEN, 12), "018b")
                    if r["version_info_bits"] != exp_ver_bits:
                        bad.append("v%d 版本位 %s != 重算 %s"
                                   % (r["version"], r["version_info_bits"], exp_ver_bits))
                elif r["version_info_bits"] is not None:
                    bad.append("v%d 不该有版本信息，却给 %s" % (r["version"], r["version_info_bits"]))
                checks.append({"lvl": lvl, "ver": r["version"], "mask": r["mask_pattern"],
                               "fmt": r["format_bits"], "fmt_equal": not bad_fmt,
                               "pen_min": min(r["mask_penalties"]),
                               "vinfo": r["version_info_bits"],
                               "ver_match": (exp_ver is None or r["version"] == exp_ver)})
                if exp_ver is not None and r["version"] != exp_ver:
                    bad.append("%s 夹具版本 %s != 预期 %s" % (lvl, r["version"], exp_ver))
            ok = ruler_ok and not bad
            return ok, {"expected": {"ISO 公布例 L0/M0/v7": [P57_QR_FMT_L_M0, P57_QR_FMT_M_M0,
                                                             P57_QR_VER_V7],
                                     "gen/xor": [hex(P57_QR_FMT_GEN), hex(P57_QR_FMT_XOR),
                                                 hex(P57_QR_VER_GEN)],
                                     "mask": "== argmin(penalties)"},
                        "actual": {"ruler_reproduces": kat_str, "ruler_ok": ruler_ok,
                                   "cases": checks, "bad": bad}}

        def bc_qr_honest_reject(E):
            """QR 侧诚实边界四条原文逐字精确，且每一次拒签都绝不留下半个文件。"""
            never = self.artifact_dir / "eval_p57_qr_never.png"
            cases = (
                ("over_m", lambda: E["packaging"].render_barcode(
                    "A" * 400, "QR", output_path=str(never))),
                ("over_h", lambda: E["packaging"].render_barcode(
                    "A" * 300, "QR", ec_level="H", output_path=str(never))),
                ("empty", lambda: E["packaging"].render_barcode(
                    "", "QR", output_path=str(never))),
                ("badlvl", lambda: E["packaging"].render_barcode(
                    "hi", "QR", ec_level="X", output_path=str(never))),
            )
            msgs = {}
            for key, call in cases:
                if never.exists():
                    never.unlink()
                try:
                    call()
                    msgs[key] = None
                except ValueError as e:
                    msgs[key] = str(e)
                msgs[key + "_nofile"] = not never.exists()
            exp = {
                "over_m": "载荷超出本引擎已实现的 QR 版本范围（v1..v10，纠错等级 M）："
                          "400 字符 / mode=Alphanumeric。按 §2 红线不伪造图像。",
                "over_h": "载荷超出本引擎已实现的 QR 版本范围（v1..v10，纠错等级 H）："
                          "300 字符 / mode=Alphanumeric。按 §2 红线不伪造图像。",
                "empty": "QR 载荷不能为空",
                "badlvl": "非法纠错等级 X，只接受 L/M/Q/H",
            }
            ok = all(msgs[k] == v for k, v in exp.items()) and all(
                msgs[k + "_nofile"] for k in exp)
            return ok, {"expected": exp,
                        "actual": {**msgs, "no_file": {k: msgs[k + "_nofile"] for k in exp}}}

        def bc_honest_degradation(E):
            v13, _ = _bc(E, "5901234123457", "EAN13", "eval_p34_v13.png")
            alt, alt_out = _bc(E, "590123412345", "EAN13", "eval_p34_alt.png",
                               scale=3, height=40, quiet=11, draw_text=False)
            from PIL import Image
            im = Image.open(alt_out)
            try:
                alt_size = im.size
            finally:
                im.close()
            c128, c128_out = _bc(E, "ABC123", "Code128", "eval_p57_c128.png")
            dm, dm_out = _bc(E, "same", "DataMatrix", "eval_p57_datamatrix.png")
            ok = (v13["code"] == "5901234123457"
                  and alt["width_px"] == 351 and alt["height_px"] == 60 and alt_size == (351, 60)
                  and c128["status"] == "needs_external" and c128["implemented"] is False
                  and "Code-128" in c128["reason"] and not c128_out.exists()
                  and dm["status"] == "needs_external" and not dm_out.exists())
            return ok, {"expected": {"13位直传": "码值不变", "alt_px": (351, 60),
                                     "Code-128/DataMatrix": "needs_external + implemented=False"
                                                            " + reason 点名留守理由 + 绝不落盘"},
                        "actual": {"v13": v13["code"],
                                   "alt": (alt["width_px"], alt["height_px"], alt_size),
                                   "c128": (c128["status"], c128["implemented"],
                                            c128_out.exists(), c128["reason"][:80]),
                                   "datamatrix": (dm["status"], dm_out.exists())}}

        # ---------- Phase 63 G 腿：一维（EAN）独立解码 oracle 入黄金域 ----------
        def bc_1d_oracle_construction_law(E):
            """oracle 自身受验收（P57 纪律：正对照 + 结构律 + 与产品对账），先证尺子可用再谈判卷。"""
            bad = []
            for name, tbl in (("L", P63_EAN_L), ("G", P63_EAN_G), ("R", P63_EAN_R)):
                if len(tbl) != 10 or set(tbl) != set("0123456789"):
                    bad.append("%s 表键集/枚数不合：枚数=%s" % (name, len(tbl)))
                if any(len(v) != 7 or (set(v) - set("01")) for v in tbl.values()):
                    bad.append("%s 表存在非 7 位二进制码字" % name)
                if len(set(tbl.values())) != 10:
                    bad.append("%s 表族内码字不互异（重复值令反查表塌缩，多位同码即读歧）" % name)
            collide = sorted(set(P63_EAN_L.values()) & set(P63_EAN_G.values()))
            if collide:
                bad.append("L 值集与 G 值集相交=%s：某格抄成了另一族的合法码字"
                           "（GS1 以奇/偶码重分类保证两族不相交）" % collide)
            for d in "0123456789":
                if P63_EAN_R[d] != _p63_complement(P63_EAN_L[d]):
                    bad.append("R(%s)=%s 应=complement(L(%s))=%s"
                               % (d, P63_EAN_R[d], d, _p63_complement(P63_EAN_L[d])))
                want_g = _p63_reverse(_p63_complement(P63_EAN_L[d]))
                if P63_EAN_G[d] != want_g:
                    bad.append("G(%s)=%s 应=reverse(complement(L(%s)))=%s"
                               % (d, P63_EAN_G[d], d, want_g))
            if len(set(P63_EAN_PARITY.values())) != 10:
                bad.append("PARITY 十串不互异 ⇒ 首位无法由左半区码型唯一反解")
            if any(len(v) != 6 or (set(v) - set("LG")) for v in P63_EAN_PARITY.values()):
                bad.append("PARITY 存在非 6 位 L/G 串")
            enc13, enc8 = _p63_encode_ean13(P63_EAN13_PUBLIC), _p63_encode_ean8(P63_EAN8_PUBLIC)
            if len(enc13) != 95 or enc13 != P34_EAN13_BITS:
                bad.append("现场编码的 EAN-13 公开例与 P34 手抄位串逐模块不合（len=%s）" % len(enc13))
            if len(enc8) != 67 or enc8 != P34_EAN8_BITS:
                bad.append("现场编码的 EAN-8 公开例与 P34 手抄位串逐模块不合（len=%s）" % len(enc8))
            if _p63_check_digit(P63_EAN13_PUBLIC[:12]) != P63_EAN13_PUBLIC[-1]:
                bad.append("EAN-13 校验位复算=%s 应=%s"
                           % (_p63_check_digit(P63_EAN13_PUBLIC[:12]), P63_EAN13_PUBLIC[-1]))
            if _p63_check_digit(P63_EAN8_PUBLIC[:7]) != P63_EAN8_PUBLIC[-1]:
                bad.append("EAN-8 校验位复算=%s 应=%s"
                           % (_p63_check_digit(P63_EAN8_PUBLIC[:7]), P63_EAN8_PUBLIC[-1]))
            r13, _o13 = _bc(E, P63_EAN13_PUBLIC[:12], "EAN13", "eval_p63_law13.png")
            r8, _o8 = _bc(E, P63_EAN8_PUBLIC[:7], "EAN8", "eval_p63_law8.png")
            if not (r13.get("code") == P63_EAN13_PUBLIC and r13.get("module_count") == 95
                    and r13.get("bit_pattern") == enc13):
                bad.append("产品 EAN-13 申报 code/module_count/bit_pattern 与本侧编码不合："
                           "%s/%s" % (r13.get("code"), r13.get("module_count")))
            if not (r8.get("code") == P63_EAN8_PUBLIC and r8.get("module_count") == 67
                    and r8.get("bit_pattern") == enc8):
                bad.append("产品 EAN-8 申报 code/module_count/bit_pattern 与本侧编码不合："
                           "%s/%s" % (r8.get("code"), r8.get("module_count")))
            par0 = P63_EAN_PARITY[P63_EAN13_PUBLIC[0]]
            g_slots = [P63_EAN13_PUBLIC[1 + i] for i, p in enumerate(par0) if p == "G"]
            blind = "7" not in g_slots
            return (not bad), {
                "expected": {"R==complement(L)": True, "G==reverse(complement(L))": True,
                             "L∩G==空集": True, "PARITY 十串互异": True,
                             "双公开例逐模块复现 P34 手抄件": True,
                             "产品申报位串==本侧编码": True},
                "actual": {"bad": bad,
                           "enc13_head": enc13[:24], "enc8_head": enc8[:24],
                           "public_example_parity": par0,
                           "public_example_G_slot_digits": g_slots,
                           "public_example_blind_to_G7": blind,
                           "note": "公开例左半区在 G parity 上的数字见 G_slot_digits；blind=True 即"
                                   "「公开例复现全等」绝不能单独充当 G 表验收——本轮 L2/L4 的"
                                   "G(7) 抄错正是靠这条免疫关系长期静默的（p63_gs1_pairwise.txt）"}}

        def bc_1d_decode_ean13_pixels(E):
            """逐位覆盖扫段：每个数字 d 都在 L 槽、G 槽、R 槽各出现一次，且一律从产品渲染的
            PNG **像素**独立读回 13 位全等（产品自报 bit_pattern 绝不作解码输入）。"""
            rows, bad = [], []
            for d in "0123456789":
                payload = "1" + d + "3" + d + "5" + d + d + d + "8921"
                want = payload + _p63_check_digit(payload)
                r, out = _bc(E, payload, "EAN13", "eval_p63_sweep13_%s.png" % d)
                scale = int(r.get("scale") or 0)
                bits, geo = _p63_read_bits(str(out), scale, 95)
                got = _p63_decode_ean13(bits) if bits is not None else None
                g_idx = [i for i, p in enumerate(P63_EAN_PARITY["1"]) if p == "G"]
                if not g_idx or len(g_idx) >= 6:
                    bad.append("d=%s PARITY[1] 的 G 槽枚数=%s 使扫段无法同时覆盖 L 与 G"
                               % (d, len(g_idx)))
                if [payload[1 + i] for i in g_idx] != [d] * len(g_idx) or payload[1] != d:
                    bad.append("d=%s 夹具没把 d 放到 G 槽/L 槽上：g_vals=%s d1=%s"
                               % (d, [payload[1 + i] for i in g_idx], payload[1]))
                if r.get("code") != want:
                    bad.append("d=%s 产品码串=%s 期望（本侧复算校验位）=%s"
                               % (d, r.get("code"), want))
                if got != want:
                    bad.append("d=%s 像素独立解码读回=%s != 期望=%s" % (d, got, want))
                if (geo.get("n_mods_sampled") != 95 or geo.get("bar_w") != scale * 95
                        or scale <= 0):
                    bad.append("d=%s 采样栅格不合：bar_w=%s scale=%s n=%s（bar_w 应=scale*95）"
                               % (d, geo.get("bar_w"), scale, geo.get("n_mods_sampled")))
                rows.append({"d": d, "want": want, "got": got,
                             "g_slot_digits": [payload[1 + i] for i in g_idx]})
            return (not bad), {
                "expected": {"每数字在 L/G/R 三族各读回一次且逐字符全等": True,
                             "bar_w == scale * 95": True, "n_mods_sampled == 95": True},
                "actual": {"bad": bad, "rows": rows}}

        def bc_1d_decode_ean8_pixels(E):
            """EAN-8 腿：公开例 + 逐位扫段皆从像素读回；本腿只走 L/R 两族（EAN-8 无 parity），
            故对 G 表缺陷天然免疫——这条免疫关系随账落证，防止有人拿 EAN-8 全绿替 G 表背书。"""
            rows, bad = [], []
            pub, _po = _bc(E, P63_EAN8_PUBLIC[:7], "EAN8", "eval_p63_pub8.png")
            scale_p = int(pub.get("scale") or 0)
            bits_p, geo_p = _p63_read_bits(str(_po), scale_p, 67)
            got_p = _p63_decode_ean8(bits_p) if bits_p is not None else None
            if got_p != P63_EAN8_PUBLIC:
                bad.append("公开例 EAN-8 像素解码读回=%s != %s" % (got_p, P63_EAN8_PUBLIC))
            for d in "0123456789":
                data7 = d + "3" + d + "5" + d + "7" + d
                want = data7 + _p63_check_digit(data7)
                r, out = _bc(E, data7, "EAN8", "eval_p63_sweep8_%s.png" % d)
                scale = int(r.get("scale") or 0)
                bits, geo = _p63_read_bits(str(out), scale, 67)
                got = _p63_decode_ean8(bits) if bits is not None else None
                if r.get("code") != want:
                    bad.append("d=%s 产品码串=%s 期望=%s" % (d, r.get("code"), want))
                if got != want:
                    bad.append("d=%s EAN-8 像素解码读回=%s != 期望=%s" % (d, got, want))
                if (geo.get("n_mods_sampled") != 67 or geo.get("bar_w") != scale * 67
                        or scale <= 0):
                    bad.append("d=%s 采样栅格不合：bar_w=%s scale=%s n=%s（bar_w 应=scale*67）"
                               % (d, geo.get("bar_w"), scale, geo.get("n_mods_sampled")))
                rows.append({"d": d, "want": want, "got": got,
                             "L_slots": data7[:4], "R_slots": data7[4:]})
            return (not bad), {
                "expected": {"公开例逐字符读回": P63_EAN8_PUBLIC,
                             "每数字在 L/R 两族各读回一次": True, "bar_w == scale * 67": True},
                "actual": {"bad": bad, "public_readback": got_p, "rows": rows,
                           "exercises_G_parity": False,
                           "note": "EAN-8 左半全 L、右半全 R，无 parity ⇒ 本腿对 G 表抄错免疫，"
                                   "不能与 EAN-13 扫段腿互换充数"}}

        def bc_1d_decode_negatives(E):
            """oracle 的阴性对照（配一条同图正对照做哨兵，防空集上的 all() 那种 vacuous 绿）：
            反色 / 抹掉中间护条 / 篡改单个模块 / 跨码制采样，四形一律不许读回原串。"""
            from PIL import Image
            r, out = _bc(E, P63_EAN13_PUBLIC[:12], "EAN13", "eval_p63_neg13.png")
            scale = int(r.get("scale") or 0)
            bits, geo = _p63_read_bits(str(out), scale, 95)
            pos = _p63_decode_ean13(bits) if bits is not None else None
            bad = []
            if pos != P63_EAN13_PUBLIC:
                bad.append("正对照失效（原图读回=%s）：阴性段此刻的「读不出」毫无价值" % pos)
            x0, y = geo["x0"], geo["y"]
            # 形制更正（本轮盘中实测）：render_barcode 用 Image.new("1", (w,h), 1)，即 1-bit 图，
            # 其像素口径与灰度 0..255 不同；若在 mode "1" 上写 0 来「抹白」，实际涂的是**黑**。
            # 那会让阴三在原始模块本就是黑条时毫无改动 ⇒ 阴性不阴的仪器假案。
            # 故阴性夹具一律先在 convert("L") 的灰度副本上改点（255=白、0=黑），再落盘。
            im = Image.open(out)
            try:
                gray = im.convert("L")
            finally:
                im.close()
            p_inv = self.artifact_dir / "eval_p63_neg_inv.png"
            p_mid = self.artifact_dir / "eval_p63_neg_mid.png"
            p_tam = self.artifact_dir / "eval_p63_neg_tam.png"
            rows = (geo["bar_first_dark_row"], geo["bar_last_dark_row"])
            inv = gray.point(lambda v: 255 - v)
            mid, tam = gray.copy(), gray.copy()
            gray.close()
            try:
                pxm, pxt = mid.load(), tam.load()
                for x in range(x0 + scale * 45, min(x0 + scale * 50, mid.size[0])):
                    for yy in range(rows[0], rows[1] + 1):
                        pxm[x, yy] = 255          # 阴二：整带抹白中间护条
                for x in range(x0 + scale * 50, min(x0 + scale * 51, tam.size[0])):
                    for yy in range(rows[0], rows[1] + 1):
                        pxt[x, yy] = 255          # 阴三：模块 50 黑→白（单模块篡改）
                inv.save(p_inv)
                mid.save(p_mid)
                tam.save(p_tam)
            finally:
                for _h in (inv, mid, tam):
                    _h.close()
            b_inv, _g1 = _p63_read_bits(str(p_inv), scale, 95)
            d_inv = _p63_decode_ean13(b_inv) if b_inv is not None else None
            if d_inv == P63_EAN13_PUBLIC:
                bad.append("阴一 反色图仍读回原串 ⇒ 解码器在猜而不是读")
            b_mid, _g2 = _p63_read_bits(str(p_mid), scale, 95)
            d_mid = _p63_decode_ean13(b_mid) if b_mid is not None else None
            if d_mid == P63_EAN13_PUBLIC:
                bad.append("阴二 抹掉中间护条仍读回原串 ⇒ 结构闸形同虚设")
            b_tam, _g3 = _p63_read_bits(str(p_tam), scale, 95)
            d_tam = _p63_decode_ean13(b_tam) if b_tam is not None else None
            if d_tam == P63_EAN13_PUBLIC:
                bad.append("阴三 篡改模块 50 仍读回原串 ⇒ 采样位与码字脱钩（假绿载体）")
            r8, out8 = _bc(E, P63_EAN8_PUBLIC[:7], "EAN8", "eval_p63_neg_x8.png")
            b_813, geo_8 = _p63_read_bits(str(out8), int(r8.get("scale") or 0), 95)
            d_813 = _p63_decode_ean13(b_813) if b_813 is not None else None
            if d_813 == P63_EAN13_PUBLIC:
                bad.append("阴四 EAN-8 图按 EAN-13 窗采样竟读回 EAN-13 原串")
            b_138, _g5 = _p63_read_bits(str(out), scale, 67)
            d_138 = _p63_decode_ean8(b_138) if b_138 is not None else None
            if d_138 == P63_EAN8_PUBLIC:
                bad.append("阴四 EAN-13 图前 67 模块按 EAN-8 解码竟读回 EAN-8 公开例")
            return (not bad), {
                "expected": "正对照读回原串；四形阴性一律 != 原串（None 或异串皆合格）",
                "actual": {"positive": pos, "inverted": d_inv, "midguard_wiped": d_mid,
                           "tampered_m50": d_tam, "ean8_window_as_ean13": d_813,
                           "ean13_prefix_as_ean8": d_138,
                           "sampled": {"ean13": geo.get("n_mods_sampled"),
                                       "ean8_as13": (b_813 or "") and len(b_813),
                                       "ean13_as8": len(b_138 or "")}}}

        def k_barcode_1d_wrong_expect(E):
            """校准（判卷侧）：把像素解码读回的码串拿去比一个假期望，必须判 fail。"""
            r, out = _bc(E, P63_EAN13_PUBLIC[:12], "EAN13", "eval_p63_calib1.png")
            bits, _geo = _p63_read_bits(str(out), int(r.get("scale") or 0), 95)
            got = _p63_decode_ean13(bits) if bits is not None else None
            return got == "5901234123450", {"expected": "假期望 5901234123450 必判 fail",
                                           "actual": {"readback": got}}

        def k_barcode_1d_g7_typo_table(E):
            """校准（尺子侧，本轮活缺陷的直接靶心）：把 G 表第 7 格抄成 L(4) 的码字
            （= L2 第 2235 行 / L4 第 833 行的盘上实测缺陷值）后，仍声称 d=7 扫段读回全等，
            必须判 fail；本臂只改反查表，绝不触及任何真值路径的判分逻辑。
            夹具串由与扫段腿同一个表达式现场派生（禁手抄 12 位字面串，数错一位就自造夹具）。"""
            _d = "7"
            payload = "1" + _d + "3" + _d + "5" + _d + _d + _d + "8921"
            r, out = _bc(E, payload, "EAN13", "eval_p63_calib2.png")
            typo_g = dict(P63_EAN_G)
            typo_g["7"] = P63_EAN_L["4"]
            bits, _geo = _p63_read_bits(str(out), int(r.get("scale") or 0), 95)
            want = payload + _p63_check_digit(payload)
            got = _p63_decode_ean13(bits, g_rev={v: k for k, v in typo_g.items()})
            return got == want, {"expected": "错表读回真码串必判 fail",
                                "actual": {"readback": got, "want": want,
                                           "typo_G7_cell": typo_g["7"]}}


        # ---------- TOPOLOGY 类别（Phase 34：packaging_panel_topology 升格独立黄金域，几何全字段精确） ----------
        def tp_box9_full_truth(E):
            r = E["packaging"].analyze_panel_topology(list(BOX9_PANELS))
            ok = (r["status"] == "ok" and r["panel_count"] == 9 and r["relation_count"] == 8
                  and r["is_connected"] and r["group_count"] == 1
                  and r["degrees"] == {"glue": 1, "front": 4, "right": 2, "back": 4, "left": 1,
                                       "top_f": 1, "top_b": 1, "bot_f": 1, "bot_b": 1}
                  and _close(r["fold_length_total"], 640.0, 1e-6)
                  and _close(r["cut_boundary_length"], 870.0, 1e-6)
                  and r["extent"] == {"x1": -15.0, "y1": -30.0, "x2": 200.0, "y2": 130.0,
                                      "width": 215.0, "height": 160.0}
                  and _close(r["total_panel_area"], 28700.0, 1e-6) and r["fabricated"] is False)
            return ok, {"expected": {"relations": 8, "fold": 640.0, "cut": 870.0, "area": 28700.0},
                        "actual": {"relations": r["relation_count"], "degrees": r["degrees"],
                                   "fold": r["fold_length_total"], "cut": r["cut_boundary_length"],
                                   "area": r["total_panel_area"]}}

        def tp_fold_line_coords(E):
            r = E["packaging"].analyze_panel_topology(list(BOX9_PANELS))
            gf = [e for e in r["adjacency"] if {e["a"], e["b"]} == {"glue", "front"}]
            ft = [e for e in r["adjacency"] if {e["a"], e["b"]} == {"front", "top_f"}]
            ok = (len(gf) == 1 and gf[0]["axis"] == "vertical"
                  and gf[0]["fold_line"] == {"x": 0.0, "y1": 0.0, "y2": 100.0}
                  and _close(gf[0]["shared_length"], 100.0, 1e-6)
                  and len(ft) == 1 and ft[0]["axis"] == "horizontal"
                  and ft[0]["fold_line"] == {"y": 100.0, "x1": 0.0, "x2": 60.0}
                  and _close(ft[0]["shared_length"], 60.0, 1e-6))
            return ok, {"expected": {"glue|front": "竖折 x=0, 0..100", "front|top_f": "横折 y=100, 0..60"},
                        "actual": {"glue_front": gf[0] if gf else None, "front_top": ft[0] if ft else None}}

        def tp_partial_overlap(E):
            r = E["packaging"].analyze_panel_topology(
                [{"id": "A", "x": 0, "y": 0, "width": 10, "height": 10},
                 {"id": "B", "x": 10, "y": 5, "width": 10, "height": 10}])
            e0 = r["adjacency"][0] if r["adjacency"] else {}
            ok = (r["relation_count"] == 1 and r["is_connected"]
                  and _close(e0.get("shared_length", -1), 5.0, 1e-6)
                  and e0.get("fold_line") == {"x": 10.0, "y1": 5.0, "y2": 10.0}
                  and r["degrees"] == {"A": 1, "B": 1})
            return ok, {"expected": {"shared": 5.0, "fold": {"x": 10.0, "y1": 5.0, "y2": 10.0}},
                        "actual": {"shared": e0.get("shared_length"), "fold": e0.get("fold_line"),
                                   "degrees": r["degrees"]}}

        def tp_rejected_and_disconnected(E):
            pk = E["packaging"]
            m = pk.analyze_panel_topology(
                [{"id": "a", "x": 0, "y": 0, "width": 10, "height": 10},
                 {"id": "b", "x": 10, "y": 0, "width": 10, "height": 10},
                 {"id": "far", "x": 500, "y": 500, "width": 10, "height": 10},
                 {"id": "neg", "x": 0, "y": 0, "width": -5, "height": 10}])
            al = pk.analyze_panel_topology([{"id": "z", "x": 0, "y": 0, "width": 0, "height": 5}])
            empty_msg = None
            try:
                pk.analyze_panel_topology([])
            except ValueError as e:
                empty_msg = str(e)
            ok = (m["status"] == "disconnected" and m["group_count"] == 2
                  and m["groups"] == [["a", "b"], ["far"]] and m["panel_count"] == 3
                  and m["rejected"] == [{"index": 3, "id": "neg", "reason": "宽或高必须为正"}]
                  and _close(m["cut_boundary_length"], 100.0, 1e-6)
                  and al["status"] == "error" and al["fabricated"] is False
                  and al["reason"] == "无有效面板（全部被拒绝）"
                  and empty_msg == "至少需要一个面板")
            return ok, {"expected": "三态如实：混合断连+逐项 rejected / 全非法 error / 空输入抛原文",
                        "actual": {"mix": (m["status"], m["groups"], m["rejected"],
                                           m["cut_boundary_length"]),
                                   "all_bad": (al["status"], al.get("reason")),
                                   "empty": empty_msg}}

        def tp_heuristic_honesty(E):
            r = E["packaging"].analyze_panel_topology(list(BOX9_PANELS))
            hints = {p["id"]: (p["role_hint"], p["size_class"]) for p in r["panels"]}
            ok = ({p["role_basis"] for p in r["panels"]} == {"geometric-heuristic"}
                  and hints["front"] == ("major-face/tall", "primary")
                  and hints["right"] == ("major-face/tall", "primary")
                  and hints["glue"] == ("minor-flap/tall", "minor")
                  and hints["top_f"] == ("minor-flap/wide", "minor"))
            return ok, {"expected": "basis 全 heuristic；front tall-primary；glue tall-minor；top_f wide-minor",
                        "actual": {"basis": sorted({p["role_basis"] for p in r["panels"]}),
                                   "hints": {k: hints[k] for k in ("front", "right", "glue", "top_f")}}}

        # ---------- 品牌类别（Phase 27：brand 引擎已真实交付，可计算部分可评） ----------
        def b_palette_exact(E):
            fx = self._brand_fixture()
            r = E["brand"].analyze_palette(fx["two_band"], k=2)
            hexes = sorted(p["hex"].upper() for p in r["palette"])
            pcts = [p["percent"] for p in r["palette"]]
            ok = (r["distinct_colors"] == 2 and hexes == ["#1E8C3C", "#F01E1E"]
                  and len(pcts) == 2 and all(abs(pct - 50.0) <= 8.0 for pct in pcts)
                  and r.get("method") == "median_cut_via_color_engine")
            return ok, {"expected": "恰两色 #1E8C3C/#F01E1E 且各占 50%±8",
                        "actual": {"hexes": hexes, "percents": pcts}}

        def b_consistency_full_match(E):
            fx = self._brand_fixture()
            r = E["brand"].check_brand_color_consistency(["#1E8C3C", "#F01E1E"], fx["two_band"],
                                                         max_delta_e=3.0, k=4)
            des = [e["delta_e_ciede2000"] for e in r["brand_colors"]]
            ok = (r["coverage"] == 1.0 and r["all_matched"] is True
                  and all(e["matched"] for e in r["brand_colors"])
                  and all(d is not None and d <= 1.0 for d in des))
            return ok, {"expected": "coverage=1.0 且双品牌色 ΔE≤1",
                        "actual": {"coverage": r["coverage"], "delta_es": des}}

        def b_no_overmatch_negative(E):
            # 行为负控黄金用例：绿品牌色打纯红图必须不匹配——防"恒 matched"式过匹配
            fx = self._brand_fixture()
            r = E["brand"].check_brand_color_consistency(["#1E8C3C"], fx["red"], max_delta_e=3.0, k=4)
            e0 = r["brand_colors"][0]
            de = e0["delta_e_ciede2000"]
            ok = (e0["matched"] is False and r["coverage"] == 0.0 and r["all_matched"] is False
                  and de is not None and 20.0 < de < 100.0)
            return ok, {"expected": "matched=False 且 coverage=0.0 且 ΔE∈(20,100)",
                        "actual": {"matched": e0["matched"], "coverage": r["coverage"], "delta_e": de}}

        def b_logo_geometry_exact(E):
            fx = self._brand_fixture()
            r = E["brand"].logo_geometry(fx["logo"], alpha_threshold=8)
            ok = (r["content_bbox"] == [50, 25, 150, 75]
                  and r["margins"] == {"left": 50, "top": 25, "right": 50, "bottom": 25}
                  and _close(r["aspect_ratio"], 2.0, 1e-3) and _close(r["fill_ratio"], 1.0, 1e-3)
                  and r["content_pixels"] == 5000 and r["fabricated"] is False)
            return ok, {"expected": {"bbox": [50, 25, 150, 75], "aspect": 2.0, "fill": 1.0, "px": 5000},
                        "actual": {"bbox": r["content_bbox"], "margins": r["margins"],
                                   "aspect": r["aspect_ratio"], "fill": r["fill_ratio"],
                                   "px": r["content_pixels"]}}

        # ---------- 印前类别（Phase 28：print_qa 引擎已真实交付，色彩/分辨率/出血可确定性核验） ----------
        def p_rgb_lowres_flagged(E):
            fx = self._print_fixture()
            r = E["print_qa"].check_document(fx["rgb"])
            codes = {f.code for f in r.findings}
            ok = ("PRINT-001" in codes and "PRINT-008" in codes
                  and r.overall_status == "error" and r.resolution == 72
                  and r.color_mode.value == "RGB" and r.findings_by_severity["error"] == 3)
            return ok, {"expected": "RGB→PRINT-001 且 72DPI→PRINT-008，overall=error，error 数=3",
                        "actual": {"codes": sorted(codes), "overall": r.overall_status,
                                   "res": r.resolution, "mode": r.color_mode.value,
                                   "errors": r.findings_by_severity["error"]}}

        def p_cmyk_hires_clean_color(E):
            fx = self._print_fixture()
            r = E["print_qa"].check_document(fx["cmyk"])
            codes = {f.code for f in r.findings}
            # 色彩/分辨率合规：绝不出现 PRINT-001/008；展平位图无出血→如实标 PRINT-004
            ok = ("PRINT-001" not in codes and "PRINT-008" not in codes
                  and "PRINT-004" in codes and r.resolution == 300
                  and r.color_mode.value == "CMYK" and r.overall_status == "error")
            return ok, {"expected": "CMYK 300DPI：无 PRINT-001/008，有 PRINT-004（未设出血），overall=error",
                        "actual": {"codes": sorted(codes), "res": r.resolution,
                                   "mode": r.color_mode.value, "overall": r.overall_status}}

        # ---------- 知识库检索类别（Phase 28：knowledge_rag 真实落盘 + 确定性 SHA-256 向量） ----------
        def k_rag_retrieve_ranking(E):
            kb = E["knowledge_rag"]
            kb.ingest_text("咖啡烘焙 温度 曲线 第一爆 二爆 发展期 豆子 产区 风味",
                           title="咖啡烘焙笔记", scope="private", item_id="kb_eval_coffee")
            kb.ingest_text("汽车刹车片 磨损 更换 制动盘 卡钳 厚度 安全 保养周期",
                           title="刹车片保养", scope="private", item_id="kb_eval_brake")
            hits = kb.retrieve("咖啡 烘焙 温度 曲线", top_k=5, allowed_scopes=["private"])
            sim = {h["knowledge_id"]: h["similarity"] for h in hits}
            c, b = sim.get("kb_eval_coffee"), sim.get("kb_eval_brake")
            order = [h["knowledge_id"] for h in hits]
            ok = (c is not None and b is not None and c > b
                  and order[0] == "kb_eval_coffee" and _close(c, 0.282843, 1e-6) and b == 0.0)
            return ok, {"expected": "咖啡查询下 coffee 排第一且 sim≈0.282843、brake=0.0（零向量退化必红）",
                        "actual": {"coffee": c, "brake": b, "top": order[0] if order else None}}

        def k_rag_scope_isolation(E):
            kb = E["knowledge_rag"]
            kb.ingest_text("项目专属规格说明 公差 材料 表面处理", title="项目规格",
                           scope="project", project_id="alpha", item_id="kb_eval_spec")
            vis = kb.retrieve("规格 公差 材料", top_k=5, allowed_scopes=["project"],
                              context={"project_id": "alpha"})
            hid = kb.retrieve("规格 公差 材料", top_k=5, allowed_scopes=["project"],
                              context={"project_id": "beta"})
            vis_ids = {h["knowledge_id"] for h in vis}
            hid_ids = {h["knowledge_id"] for h in hid}
            ok = ("kb_eval_spec" in vis_ids and "kb_eval_spec" not in hid_ids)
            return ok, {"expected": "project 作用域：匹配 project_id 可见、不匹配必隔离",
                        "actual": {"alpha_seen": "kb_eval_spec" in vis_ids,
                                   "beta_seen": "kb_eval_spec" in hid_ids}}

        # ---------- 改稿类别（Phase 30：revision 纯确定性指令解析+真实换算，零时钟/随机/网络） ----------
        def _rv_objs():
            return [
                {"id": "obj_logo", "name": "logo", "type": "image", "width": 200, "height": 80, "color": "#123456"},
                {"id": "obj_title", "name": "主标题", "type": "text", "font_size": 36, "color": "#111111"},
                {"id": "obj_subtitle", "name": "副标题", "type": "text", "font_size": 20},
                {"id": "obj_bg", "name": "背景", "type": "shape", "width": 1000, "height": 1000, "protected": True},
            ]

        def rv_parse_status_matrix(E):
            p = E["revision"].parse(
                "logo 再大一点，主标题换成深红色，背景删掉，主标题和副标题都缩小一点", _rv_objs())
            tgt = {(c["op"], c["status"]): c.get("target") for c in p["changes"]}
            ok = (p["summary"]["changes"] == 4
                  and p["summary"]["by_status"] == {"proposed": 2, "conflict": 1, "needs_clarification": 1}
                  and tgt.get(("resize", "proposed")) == "obj_logo"
                  and tgt.get(("color_change", "proposed")) == "obj_title"
                  and tgt.get(("delete", "conflict")) == "obj_bg"
                  and tgt.get(("resize", "needs_clarification")) is None)
            return ok, {"expected": "4 变更：唯一命中 proposed×2 / 受保护 conflict / 多候选澄清不猜",
                        "actual": {"summary": p["summary"],
                                   "targets": {f"{k[0]}|{k[1]}": v for k, v in tgt.items()}}}

        def rv_apply_exact_math(E):
            eng = E["revision"]
            objs = _rv_objs()
            p = eng.parse("logo 再大一点，主标题换成深红色", objs)
            approved = [c for c in p["changes"] if c["status"] == "proposed"]
            ap = eng.apply(objs, p["changes"], only_ids=[c["id"] for c in approved])
            logo = next(o for o in ap["objects"] if o.get("_id") == "obj_logo")
            title = next(o for o in ap["objects"] if o.get("_id") == "obj_title")
            objs2 = [{"id": "b1", "name": "banner", "type": "shape", "color": "#808080", "width": 10, "height": 10},
                     {"id": "t2", "name": "标题", "type": "text", "font_size": 36, "color": "#111111"}]
            p2 = eng.parse("banner 提亮一点，标题字号减小", objs2)
            ap2 = eng.apply(objs2, p2["changes"],
                            only_ids=[c["id"] for c in p2["changes"] if c["status"] == "proposed"])
            b1 = next(o for o in ap2["objects"] if o.get("_id") == "b1")
            t2 = next(o for o in ap2["objects"] if o.get("_id") == "t2")
            ok = (ap["applied"] == 2 and logo["width"] == 240.0 and logo["height"] == 96.0
                  and title["color"] == "#8B0000" and b1["color"] == "#939393" and t2["font_size"] == 32.0)
            return ok, {"expected": {"logo": [240.0, 96.0], "title": "#8B0000",
                                     "brightened_#808080": "#939393", "font_36-4": 32.0},
                        "actual": {"logo": [logo["width"], logo["height"]], "title": title["color"],
                                   "banner": b1["color"], "font": t2["font_size"]}}

        def rv_unparsed_never_fabricated(E):
            p = E["revision"].parse("今天天气不错，随便聊聊", _rv_objs())
            ok = (p["summary"]["changes"] == 0 and p["summary"]["by_status"] == {}
                  and p["summary"]["unparsed_clauses"] == 2)
            return ok, {"expected": "无指令：0 变更、2 句如实 unparsed，绝不编造 change",
                        "actual": p["summary"]}

        # ---------- 评审类别（Phase 30：design_review 纯数学规则引擎，缺陷/合规双向可核） ----------
        def _rw_canvas():
            return {"width": 1000, "height": 1000, "bleed": 0, "dpi": 300, "background": "#FFFFFF"}

        def _rw_dirty_objs():
            return [
                {"id": "bg", "type": "shape", "x": 0, "y": 0, "width": 1000, "height": 1000,
                 "color": "#FFFFFF", "z": 0, "bleed_target": True},
                {"id": "title", "type": "text", "x": 100, "y": 100, "width": 400, "height": 60,
                 "color": "#EEEEEE", "font_size": 40},
                {"id": "sub", "type": "text", "x": 102, "y": 105, "width": 380, "height": 40,
                 "color": "#111111", "font_size": 4},
            ]

        def rw_dirty_issue_matrix(E):
            r = E["review"].review(_rw_dirty_objs(), canvas=_rw_canvas())
            keys = sorted(f"{i['id']}|{i['severity']}|{i['autofix_available']}" for i in r["issues"])
            ok = (r["score"] == 68 and keys == [
                      "overlap-title-sub|high|False", "sub-fontsize|high|True",
                      "title-contrast|critical|True", "title-printcontrast|medium|True"]
                  and r["summary"]["total_issues"] == 4
                  and r["summary"]["by_severity"] == {"critical": 1, "high": 2, "medium": 1})
            return ok, {"expected": "score=68 且 4 问题的 id/严重度/可自动修复 逐项精确",
                        "actual": {"score": r["score"], "keys": keys, "by_severity": r["summary"]["by_severity"]}}

        def rw_clean_full_marks(E):
            # 反证：合规文档必须零问题满分——评审器若虚报问题（恒报缺陷）此用例必红
            objs = [
                {"id": "hd", "type": "text", "x": 100, "y": 100, "width": 400, "height": 60,
                 "color": "#111111", "font_size": 40},
                {"id": "bd", "type": "text", "x": 100, "y": 200, "width": 400, "height": 30,
                 "color": "#333333", "font_size": 12},
            ]
            r = E["review"].review(objs, canvas=_rw_canvas())
            ok = r["score"] == 100 and r["issues"] == []
            return ok, {"expected": {"score": 100, "issues": 0},
                        "actual": {"score": r["score"], "issues": len(r["issues"])}}

        def rw_contrast_autofix_math(E):
            import design_review_engine as drev
            dirty = drev.contrast_ratio("#EEEEEE", "#FFFFFF")
            good = drev.contrast_ratio("#111111", "#FFFFFF")
            fix = drev.adjust_for_contrast("#EEEEEE", "#FFFFFF", 4.5)
            fixed_ratio = drev.contrast_ratio(fix, "#FFFFFF") if fix else None
            ok = (dirty == 1.16 and good == 18.883 and fix == "#737373"
                  and fixed_ratio is not None and fixed_ratio >= 4.5)
            return ok, {"expected": "#EEEEEE/白=1.16、#111111/白=18.883、修复→#737373 且对比≥4.5",
                        "actual": {"dirty": dirty, "good": good, "fix": fix, "fixed_ratio": fixed_ratio}}

        # ---------- 安全类别（Phase 30：security 确定性规则；服务态用真实注入 checker） ----------
        def sc_path_four_way(E):
            ck = E["security"]
            got = {"system_dir": ck.is_blocked("C:\\Windows\\System32\\cmd.exe"),
                   "double_extension": ck.is_blocked("C:\\Users\\public\\photo.jpg.exe"),
                   "path_traversal": ck.is_blocked("C:\\Users\\..\\..\\windows\\win.ini"),
                   "normal_file": ck.is_blocked("C:\\Users\\public\\design.psd")}
            ok = (got["system_dir"] is True and got["double_extension"] is True
                  and got["path_traversal"] is True and got["normal_file"] is False)
            return ok, {"expected": "系统目录/双扩展/穿越必阻断 且 正常文件必放行（恒真/恒假均红）",
                        "actual": got}

        def sc_permission_intersection(E):
            import security_engine as sec
            pe = sec.PermissionEngine()
            pe.set_user_permissions({sec.PermissionType.USER, sec.PermissionType.TOOL})
            pe.tool_permissions["test.tool"] = {sec.PermissionType.TOOL}
            pe.tool_permissions["dangerous.admin_tool"] = {sec.PermissionType.ADMIN}
            granted = pe.can_execute("test.tool")
            escalation = pe.can_execute("dangerous.admin_tool")
            pe2 = sec.PermissionEngine()
            pe2.set_user_permissions({sec.PermissionType.USER, sec.PermissionType.TOOL})
            pe2.set_skill_permissions("sk.gfx", {sec.PermissionType.USER})  # skill 交集缺 TOOL
            pe2.tool_permissions["test.tool"] = {sec.PermissionType.TOOL}
            skill_gate = pe2.can_execute("test.tool", skill_name="sk.gfx")
            ok = granted is True and escalation is False and skill_gate is False
            return ok, {"expected": "授权 True / 越权(需ADMIN而无) False / Skill 权限不足 False",
                        "actual": {"granted": granted, "escalation": escalation, "skill_gate": skill_gate}}

        def sc_highrisk_no_overreport(E):
            import security_engine as sec
            h = sec.HighRiskOperationHandler()
            pv3 = h.prepare_operation("eval-op-l3", "ps.layer.delete", sec.RiskLevel.L3,
                                      {"objects": ["l1", "l2"], "will_delete": True})
            pv1 = h.prepare_operation("eval-op-l1", "ps.layer.read", sec.RiskLevel.L1, {"read_only": True})
            init_status = pv3["status"]  # 快照：execute 会真实改写同一 dict 的 status
            h.approve_operation("eval-op-l3", True)
            executed = h.execute_approved_operations()
            status = h.pending_operations["eval-op-l3"]["status"]
            h2 = sec.HighRiskOperationHandler()
            h2.register_executor("eval.rec", lambda d: {"done": True})
            h2.prepare_operation("eval-op-x", "eval.rec", sec.RiskLevel.L4, {"objects": ["a"]})
            h2.approve_operation("eval-op-x", True)
            executed2 = h2.execute_approved_operations()
            status2 = h2.pending_operations["eval-op-x"]["status"]
            ok = (pv3["requires_approval"] is True and pv1["requires_approval"] is False
                  and init_status == "pending" and executed == [] and status == "no_executor"
                  and executed2 == ["eval-op-x"] and status2 == "executed")
            return ok, {"expected": "L3 必确认/L1 免确认；无 executor 不虚报；有 executor 真实执行",
                        "actual": {"l3_requires": pv3["requires_approval"], "l1_requires": pv1["requires_approval"],
                                   "executed": executed, "status": status,
                                   "executed2": executed2, "status2": status2}}

        # ---------- 工作流类别（Phase 31：workflow 纯内存状态机；uuid 仅实例 id、时间戳仅记录戳，
        #            判分字段全部剥除 id/戳后精确可核；每用例新构造引擎隔离注册表状态） ----------
        def _wf_defn(wfm, wid, node_specs, edge_specs, variables=None):
            return wfm.WorkflowDefinition(
                id=wid, name=wid, description="eval", version="1.0",
                nodes=[wfm.NodeConfig(id=i, type=t, name=n, config=c) for i, t, n, c in node_specs],
                edges=[wfm.Edge(source_node_id=s, target_node_id=d, condition=cond) for s, d, cond in edge_specs],
                variables=variables or {})

        def _wf_happy_def(wfm):
            return _wf_defn(
                wfm, "eval-wf-happy",
                [("n1", wfm.NodeType.INPUT, "input", {"mapping": {"src": "$text"}}),
                 ("n2", wfm.NodeType.TOOL, "tool", {"tool_name": "echo_upper",
                                                    "parameters": {"text": "$src"},
                                                    "output_variable": "toolout"}),
                 ("n3", wfm.NodeType.OUTPUT, "output", {"select": ["toolout"]})],
                [("n1", "n2", None), ("n2", "n3", None)], variables={"text": "hello"})

        def _async_run(coro_fn):
            # 协程置于独立工作线程执行：/eval/run 为 async 端点时调用线程已有运行中
            # 事件循环，直接 asyncio.run() 必抛 RuntimeError；无循环线程则任何调用
            # 上下文皆可跑，且仍为单循环确定性执行。Phase 31 为 workflow 用例而设，
            # Phase 32 起 asset/recovery 的 async 方法共用，Phase 36 起 model_routing
            # 用它在"有运行循环"语境做熔断对照真值（Phase 36~59 对照的是 create_task 不抛错，
            # Phase 60 根治后对照的是"环内同样不抛且开环/复口两语境判分全同"）
            import asyncio
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(lambda: asyncio.run(coro_fn())).result()

        def _wf_ghost_status():
            import workflow_engine as wfm
            eng = wfm.WorkflowEngine()  # 零注册工具：失败文案必为 "可用: []"
            eng.define_workflow(_wf_defn(
                wfm, "eval-wf-ghost",
                [("n1", wfm.NodeType.INPUT, "input", {}),
                 ("n2", wfm.NodeType.TOOL, "tool", {"tool_name": "ghost"}),
                 ("n3", wfm.NodeType.OUTPUT, "output", {})],
                [("n1", "n2", None), ("n2", "n3", None)]))
            return eng.get_instance_status(_async_run(lambda: eng.execute("eval-wf-ghost")))

        def wf_cycle_detected(E):
            import workflow_engine as wfm
            wd = _wf_defn(wfm, "eval-wf-cycle",
                          [("a", wfm.NodeType.INPUT, "A", {}), ("b", wfm.NodeType.OUTPUT, "B", {})],
                          [("a", "b", None), ("b", "a", None)])
            errs = wfm.WorkflowEditorSupport.validate_workflow(wd)
            ok = errs == ["Circular dependency detected"]
            return ok, {"expected": "a→b→a 环的校验报错恰为 ['Circular dependency detected']",
                        "actual": errs}

        def wf_happy_chain_exact(E):
            import workflow_engine as wfm
            eng = wfm.WorkflowEngine()
            eng.register_tool("echo_upper", lambda text: {"upper": text.upper(), "len": len(text)})
            eng.define_workflow(_wf_happy_def(wfm))
            st = eng.get_instance_status(_async_run(lambda: eng.execute("eval-wf-happy", {"text": "hello"})))
            msgs = [l["message"] for l in st["logs"]]
            ok = (st["status"] == "success" and st["error_message"] is None
                  and st["result"] == {"toolout": {"upper": "HELLO", "len": 5}}
                  and st["node_status"] == {"n1": "success", "n2": "success", "n3": "success"}
                  and len(msgs) == 9 and msgs[0].startswith("Started workflow execution: ")
                  and msgs[1:] == ["Executing node: input (input)", "Node completed: input",
                                   "Executing node: tool (tool)", "Node completed: tool",
                                   "Executing node: output (output)", "Output produced",
                                   "Node completed: output", "Workflow completed successfully"])
            return ok, {"expected": "链贯通：result=HELLO/len5、三节点 success、日志除首条含 uuid 外逐条精确",
                        "actual": {"status": st["status"], "result": st["result"],
                                   "node_status": st["node_status"], "tail_msgs": msgs[1:]}}

        def wf_fail_no_pass_on(E):
            st = _wf_ghost_status()
            ok = (st["status"] == "failed" and st["result"] is None
                  and st["node_status"] == {"n1": "success", "n2": "failed"}
                  and "n3" not in st["node_status"]
                  and st["error_message"] == "Tool 未注册: 'ghost'（可用: []）")
            return ok, {"expected": "未注册工具诚实失败：n2 failed、下游 n3 从未执行连键都不虚设、result 为空",
                        "actual": {"status": st["status"], "node_status": st["node_status"],
                                   "n3_present": "n3" in st["node_status"], "error": st["error_message"]}}

        def wf_conditional_fork(E):
            import workflow_engine as wfm
            eng = wfm.WorkflowEngine()
            eng.define_workflow(_wf_defn(
                wfm, "eval-wf-cond",
                [("n1", wfm.NodeType.INPUT, "input", {}),
                 ("c1", wfm.NodeType.CONDITION, "cond", {"expression": "$flag == True"}),
                 ("yes", wfm.NodeType.OUTPUT, "yes-branch", {"select": ["flag"]}),
                 ("no", wfm.NodeType.OUTPUT, "no-branch", {"select": ["flag"]})],
                [("n1", "c1", None), ("c1", "yes", "$flag == True"), ("c1", "no", "$flag == False")],
                variables={"flag": True}))
            st = eng.get_instance_status(_async_run(lambda: eng.execute("eval-wf-cond")))
            ok = (st["status"] == "success" and st["result"] == {"flag": True}
                  and st["node_status"] == {"n1": "success", "c1": "success",
                                            "yes": "success", "no": "skipped"})
            return ok, {"expected": "条件边分叉：真分支执行、假分支如实 skipped（恒执行/恒跳过都红）",
                        "actual": {"status": st["status"], "result": st["result"],
                                   "node_status": st["node_status"]}}

        def wf_loop_body_aggregate(E):
            import workflow_engine as wfm
            eng = wfm.WorkflowEngine()
            eng.define_workflow(_wf_defn(
                wfm, "eval-wf-loop",
                [("lp", wfm.NodeType.LOOP, "loop", {"items": ["a", "bb", "ccc"]}),
                 ("bd", wfm.NodeType.INPUT, "body", {}),
                 ("o", wfm.NodeType.OUTPUT, "out", {"select": ["lp_iterations"]})],
                [("lp", "bd", None), ("bd", "lp", None), ("lp", "o", None)]))
            st = eng.get_instance_status(_async_run(lambda: eng.execute("eval-wf-loop")))
            ok = (st["status"] == "success" and st["result"] == {"lp_iterations": 3}
                  and st["node_status"] == {"lp": "success", "bd": "success", "o": "success"})
            return ok, {"expected": "循环体 3 项真实跑满并聚合 lp_iterations=3",
                        "actual": {"result": st["result"], "node_status": st["node_status"]}}

        def wf_idempotent_replay(E):
            import workflow_engine as wfm
            eng = wfm.WorkflowEngine()
            eng.register_tool("echo_upper", lambda text: {"upper": text.upper(), "len": len(text)})
            eng.define_workflow(_wf_happy_def(wfm))
            i1 = _async_run(lambda: eng.execute("eval-wf-happy", {"text": "hello"}))
            i2 = _async_run(lambda: eng.execute("eval-wf-happy", {"text": "hello"}))
            keep = lambda s: {k: s[k] for k in ("status", "result", "node_status", "error_message")}
            s1, s2 = eng.get_instance_status(i1), eng.get_instance_status(i2)
            ok = i1 != i2 and keep(s1) == keep(s2) and keep(s1)["status"] == "success"
            return ok, {"expected": "两次独立执行实例 id 不同，剥除 id/时间戳后判分字段全等",
                        "actual": {"ids_differ": i1 != i2, "state_equal": keep(s1) == keep(s2)}}

        # ---------- ASSET：素材引擎（Phase 32；逐用例全新沙箱，判分字段剥除 uuid/时间戳） ----------
        def a_embed_tokenhash_exact(E):
            import asset_engine as aem
            v1 = aem.embed_text("coffee_mug png image/png image")
            v2 = aem.embed_text("coffee_mug png image/png image")
            h = hashlib.sha256(b"coffee").digest()
            bucket = int.from_bytes(h[:4], "big") % 512
            nz = [(i, x) for i, x in enumerate(v1) if x != 0.0]
            ok = (v1 == v2 and len(v1) == 512 and bucket == 372
                  and nz == [(40, 1.0), (87, -1.0), (204, -2.0), (372, -1.0), (381, 2.0)])
            return ok, {"expected": "512 维稀疏向量非零位精确 [(40,1),(87,-1),(204,-2),(372,-1),(381,2)]；"
                                    "token 'coffee' 哈希桶独立复算=372；两次调用全等",
                        "actual": {"stable": v1 == v2, "len": len(v1),
                                   "bucket_coffee": bucket, "nonzero": nz}}

        def a_add_asset_real_image(E):
            import asset_engine as aem
            fx = self._asset_fixture()
            eng = self._asset_eng("add")
            real_size = Path(fx["mug"]).stat().st_size
            meta = _async_run(lambda: eng.add_asset(fx["mug"], "image/png", width=120, height=80))
            tp = Path(meta.thumbnail_path or "")
            emb = eng.get_embedding(meta.asset_id)
            desc = "coffee_mug png image/png image"
            ok = (meta.filename == "coffee_mug.png" and meta.file_size == real_size and real_size > 0
                  and meta.mime_type == "image/png" and meta.asset_type.value == "image"
                  and meta.width == 120 and meta.height == 80
                  and tp.exists() and tp.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
                  and emb is not None and emb.model_name == "tokenhash-sha256-512"
                  and emb.index_status.value == "completed"
                  and emb.vector == aem.embed_text(desc))
            return ok, {"expected": "真实 PNG 入库：文件名/字节数=磁盘实测/类型/尺寸精确，缩略图真落盘含 PNG 魔数，"
                                    "embedding 向量与描述符 embed_text 同源相等",
                        "actual": {"filename": meta.filename, "file_size": meta.file_size,
                                   "real_size": real_size, "asset_type": meta.asset_type.value,
                                   "thumbnail": str(tp)[-40:], "thumb_png_magic": tp.exists() and tp.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n",
                                   "model": emb.model_name, "status": emb.index_status.value,
                                   "vector_same": emb.vector == aem.embed_text(desc)}}

        def a_search_rank_exact(E):
            import asset_engine as aem
            fx = self._asset_fixture()
            eng = self._asset_eng("search")
            m1 = _async_run(lambda: eng.add_asset(fx["mug"], "image/png", width=120, height=80))
            m2 = _async_run(lambda: eng.add_asset(fx["pot"], "image/jpeg", width=40, height=40))
            res = _async_run(lambda: eng.search_assets(
                aem.embed_text("coffee_mug png image/png image"), top_k=5))
            ranked = [(eng.get_asset(i).filename, round(s, 12)) for i, s in res]
            ok = (len(ranked) == 2 and ranked[0][0] == "coffee_mug.png" and _close(ranked[0][1], 1.0, 1e-9)
                  and ranked[1][0] == "cooking_pot.jpg" and _close(ranked[1][1], 0.402015126104, 1e-9))
            return ok, {"expected": "咖啡杯查询自匹配余弦=1.0 居首；锅图 0.402015126104 次之（排序与数值双钉）",
                        "actual": {"ranked": ranked}}

        def a_license_attribution_rule(E):
            fx = self._asset_fixture()
            eng = self._asset_eng("lic")
            meta = _async_run(lambda: eng.add_asset(fx["mug"], "image/png", width=120, height=80))
            import asset_engine as aem
            eng.set_license_status(meta.asset_id, aem.LicenseStatus.COMMERCIAL,
                                   license_type="CC-BY", commercial_use=True, verified=True)
            lic = eng.get_license(meta.asset_id)
            eng.set_license_status(meta.asset_id, aem.LicenseStatus.UNKNOWN,
                                   license_type="internal")
            lic2 = eng.get_license(meta.asset_id)
            ok = (lic.license_status.value == "commercial" and lic.license_type == "CC-BY"
                  and lic.attribution_required is True and lic.commercial_use is True
                  and lic.modification_allowed is True and lic.warnings == []
                  and lic2.attribution_required is False)
            return ok, {"expected": "CC-BY 必置署名要求 True；internal 分支必 False（规则判错任一方向即红）；"
                                    "字段逐项回读精确",
                        "actual": {"ccby": {"status": lic.license_status.value, "type": lic.license_type,
                                             "attribution": lic.attribution_required,
                                             "commercial": lic.commercial_use,
                                             "modification": lic.modification_allowed,
                                             "warnings": lic.warnings},
                                   "internal_attribution": lic2.attribution_required}}

        def a_generation_readback(E):
            eng = self._asset_eng("gen")
            gid = eng.record_generation(None, "red mug", "flux-1", "local",
                                        120, 80, 28, 3.5, 42, 0.01, 1.2)
            gm = eng.generation_metadata.get(gid)
            ok = (bool(gid) and gm is not None and gm.prompt == "red mug"
                  and gm.model_name == "flux-1" and gm.provider == "local"
                  and gm.width == 120 and gm.height == 80 and gm.steps == 28
                  and gm.guidance_scale == 3.5 and gm.seed == 42 and gm.cost_usd == 0.01
                  and gm.duration_seconds == 1.2 and gm.negative_prompt is None
                  and gm.source_asset_id is None)
            return ok, {"expected": "返回 id 仅可核真值；判分为回读全字段逐项精确（含 negative/source 为 None 不虚设）",
                        "actual": {"id_truthy": bool(gid), "prompt": gm.prompt if gm else None,
                                   "model": gm.model_name if gm else None,
                                   "provider": gm.provider if gm else None,
                                   "wh": [gm.width, gm.height] if gm else None,
                                   "steps": gm.steps if gm else None,
                                   "guidance": gm.guidance_scale if gm else None,
                                   "seed": gm.seed if gm else None,
                                   "cost": gm.cost_usd if gm else None,
                                   "duration": gm.duration_seconds if gm else None,
                                   "negative": gm.negative_prompt if gm else None,
                                   "source": gm.source_asset_id if gm else None}}

        def a_delete_cascade_reload(E):
            import asset_engine as aem
            fx = self._asset_fixture()
            eng = self._asset_eng("cascade")
            m1 = _async_run(lambda: eng.add_asset(fx["mug"], "image/png", width=120, height=80))
            _async_run(lambda: eng.add_asset(fx["pot"], "image/jpeg", width=40, height=40))
            eng.set_license_status(m1.asset_id, aem.LicenseStatus.FREE, license_type="CC0")
            eng.record_generation(None, "p", "m", "prov", 10, 10, 1, 1.0, 1, 0.0, 0.1)
            eng2 = aem.AssetEngine(eng.root_path)  # 冷读回：证明状态真落盘而非仅内存
            counts_before = (len(eng2.assets), len(eng2.embeddings),
                             len(eng2.licenses), len(eng2.generation_metadata))
            ok_del = eng2.delete_asset(m1.asset_id)
            gone = (eng2.get_asset(m1.asset_id) is None and eng2.get_embedding(m1.asset_id) is None
                    and eng2.get_license(m1.asset_id) is None)
            eng3 = aem.AssetEngine(eng.root_path)
            counts_after = (len(eng3.assets), len(eng3.embeddings),
                            len(eng3.licenses), len(eng3.generation_metadata))
            ok = (counts_before == (2, 2, 1, 1) and ok_del is True and gone
                  and eng2.delete_asset(m1.asset_id) is False
                  and counts_after == (1, 1, 0, 1))
            return ok, {"expected": "冷启动读回 (2,2,1,1)；删除级联清空素材/向量/授权三库且二次删除判 False；"
                                    "再冷读回 (1,1,0,1)（生成记录独立于素材不受级联）",
                        "actual": {"before": counts_before, "delete": ok_del, "cascaded_none": gone,
                                   "redelete": eng2.delete_asset(m1.asset_id), "after": counts_after}}

        # ---------- RECOVERY：崩溃恢复引擎（Phase 32；文档字节固定，sha256 可离线复算） ----------
        def r_snapshot_sha256_exact(E):
            eng, doc = self._recovery_env("snap")
            snap = _async_run(lambda: eng.snapshot_manager.create_snapshot(
                str(doc), snapshot_type="full"))
            expect = hashlib.sha256(self.RECOVERY_DOC).hexdigest()
            ok = (snap.checksum == expect and snap.size_bytes == len(self.RECOVERY_DOC)
                  and snap.snapshot_type == "full" and snap.metadata.get("recoverable") is True
                  and snap.backup_path is not None and Path(snap.backup_path).exists())
            return ok, {"expected": "快照校验和==对固定字节的独立 sha256 复算，字节数/类型/可恢复标记精确，备份副本真落盘",
                        "actual": {"checksum_matches": snap.checksum == expect,
                                   "sha_head": snap.checksum[:16], "expect_head": expect[:16],
                                   "size": snap.size_bytes, "type": snap.snapshot_type,
                                   "recoverable": snap.metadata.get("recoverable"),
                                   "backup_exists": bool(snap.backup_path) and Path(snap.backup_path).exists()}}

        def r_restore_reverts_corruption(E):
            eng, doc = self._recovery_env("restore")
            snap = _async_run(lambda: eng.snapshot_manager.create_snapshot(
                str(doc), snapshot_type="full"))
            doc.write_bytes(b"CORRUPTED BY CRASH")
            ok_restore = _async_run(lambda: eng.snapshot_manager.restore_snapshot(snap.id))
            reverted = doc.read_bytes() == self.RECOVERY_DOC
            ghost = _async_run(lambda: eng.snapshot_manager.restore_snapshot("no-such-snapshot"))
            ok = ok_restore is True and reverted and ghost is False
            return ok, {"expected": "损坏后还原判 True 且字节精确回归；幽灵快照还原必须判 False（不虚报恢复）",
                        "actual": {"restore": ok_restore, "bytes_back": reverted, "ghost": ghost}}

        def r_transaction_rollback_true(E):
            import recovery_engine as rem
            eng, doc = self._recovery_env("txn")
            tm = eng.transaction_manager
            tid = _async_run(lambda: tm.begin_transaction("task-77", user_id="u1", document_path=str(doc)))
            op = rem.Operation(id="op-1", type=rem.OperationType.UPDATE,
                               timestamp=datetime(2026, 1, 1),
                               target_path=str(doc), target_name="rect")
            tm.record_operation(tid, op)
            tm.record_operation(tid, op)
            hist = tm.get_transaction_history("task-77")
            doc.write_bytes(b"half-finished edit")
            rb = _async_run(lambda: tm.rollback_transaction(tid))
            hist_after = tm.get_transaction_history("task-77")
            bogus = _async_run(lambda: tm.rollback_transaction("no-such-txn"))
            shape = [{"status": h["status"], "operation_count": h["operation_count"]} for h in hist]
            ok = (shape == [{"status": "active", "operation_count": 2}]
                  and rb is True and doc.read_bytes() == self.RECOVERY_DOC
                  and hist_after == [] and bogus is False)
            return ok, {"expected": "事务历史剥除 id/时间戳后恰 [{active,2 ops}]；回滚判 True 且文档字节真实回归；"
                                    "回滚后历史清空；幽灵事务回滚判 False",
                        "actual": {"shape": shape, "rollback": rb,
                                   "bytes_back": doc.read_bytes() == self.RECOVERY_DOC,
                                   "hist_after": hist_after, "bogus": bogus}}

        def r_register_task_fields(E):
            eng, doc = self._recovery_env("task")
            st = _async_run(lambda: eng.register_task("task-77", total_steps=5,
                                                      context={"document": "brand.ai"}))
            persisted = (eng.state_dir / "task_task-77.json").exists()
            ok = (st.task_id == "task-77" and st.status == "running" and st.current_step == 0
                  and st.total_steps == 5 and st.context == {"document": "brand.ai"}
                  and st.checkpoint_snapshot_id is None and st.error_message is None
                  and persisted)
            return ok, {"expected": "注册即 running/步 0 共 5 步/上下文原样/无检查点/无错误，状态真落盘",
                        "actual": {"status": st.status, "step": st.current_step,
                                   "total": st.total_steps, "context": st.context,
                                   "checkpoint": st.checkpoint_snapshot_id,
                                   "error": st.error_message, "persisted": persisted}}

        def r_recover_checkpoint_and_ghost(E):
            import recovery_engine as rem
            eng, doc = self._recovery_env("recover")
            snap = _async_run(lambda: eng.snapshot_manager.create_snapshot(
                str(doc), snapshot_type="full", metadata={"task_id": "task-88"}))
            st = _async_run(lambda: eng.register_task("task-88", total_steps=3, context={}))
            st.checkpoint_snapshot_id = snap.id
            doc.write_bytes(b"garbage mid-crash")
            recovered = _async_run(lambda: eng.recover_task("task-88", rem.RecoveryLevel.TASK_ROLLBACK))
            st_after = eng.task_states["task-88"]
            ghost_msg = None
            try:
                _async_run(lambda: eng.recover_task("ghost-task"))
            except ValueError as e:
                ghost_msg = str(e)
            ok = (recovered is not None and recovered.id == snap.id
                  and doc.read_bytes() == self.RECOVERY_DOC
                  and st_after.status == "running" and st_after.error_message is None
                  and ghost_msg == "Task not found: ghost-task")
            return ok, {"expected": "按检查点真实还原：返回快照即检查点、字节回归、状态回 running 且错误清空；"
                                    "幽灵任务恢复必须抛 ValueError 精确原文而非假成功",
                        "actual": {"same_snapshot": recovered is not None and recovered.id == snap.id,
                                   "bytes_back": doc.read_bytes() == self.RECOVERY_DOC,
                                   "status": st_after.status, "error": st_after.error_message,
                                   "ghost_msg": ghost_msg}}

        def r_no_local_file_honest(E):
            eng, doc = self._recovery_env("nolocal")
            snap = _async_run(lambda: eng.snapshot_manager.create_snapshot(
                document_path="", snapshot_type="full"))
            restore = _async_run(lambda: eng.snapshot_manager.restore_snapshot(snap.id))
            ok = (snap.checksum == "none" and snap.size_bytes == 0
                  and snap.backup_path is None and snap.metadata.get("recoverable") is False
                  and str(snap.metadata.get("recovery_reason", "")).startswith("no_local_file")
                  and restore is False)
            return ok, {"expected": "无本地文件（Adobe 文档未导出）快照诚实记 checksum=none/无备份/不可恢复，"
                                    "还原必判 False 绝不谎报",
                        "actual": {"checksum": snap.checksum, "size": snap.size_bytes,
                                   "backup": snap.backup_path,
                                   "recoverable": snap.metadata.get("recoverable"),
                                   "reason": snap.metadata.get("recovery_reason"),
                                   "restore": restore}}

        # ---------- 排版（typography，自供夹具字体重放索引） ----------
        # 各用例读真字节后引擎持 lazy TTFont 句柄（Windows 锁文件），finally close() 释放，
        # 保证跨轮 tag 复用删得掉旧沙箱；measure/match 复用同一引擎实例，返回前句柄已备齐。
        def t_serif_measure_exact(E):
            eng = self._typo_env("measure_serif")
            try:
                m = eng.measure_text("fix_serif", "Hello" + self.TYPO_P1 + self.TYPO_P2, 24.0)
                fields = ("advance_units", "width_pt", "line_height_pt", "glyph_count",
                          "missing_glyphs", "fully_covered", "font", "units_per_em")
                ok = (m["advance_units"] == 3989 and _close(m["width_pt"], 95.736, 1e-9)
                      and _close(m["line_height_pt"], 28.8, 1e-9) and m["glyph_count"] == 7
                      and m["missing_glyphs"] == 0 and m["fully_covered"] is True
                      and m["font"] == "ZJFixSerif" and m["units_per_em"] == 1000)
                return ok, {"expected": "serif 夹具 7 字形 hmtx 累加=3989 units→95.736pt@24 磅，行高 28.8pt，零缺字全覆盖",
                            "actual": {k: m[k] for k in fields}}
            finally:
                eng.close()

        def t_sans_missing_honest(E):
            eng = self._typo_env("measure_sans")
            try:
                m = eng.measure_text("fix_sans", "Hello" + self.TYPO_P1 + self.TYPO_P2, 24.0)
                fields = ("advance_units", "width_pt", "line_height_pt", "glyph_count",
                          "missing_glyphs", "fully_covered")
                ok = (m["advance_units"] == 3180 and _close(m["width_pt"], 76.32, 1e-9)
                      and _close(m["line_height_pt"], 24.0, 1e-9) and m["glyph_count"] == 6
                      and m["missing_glyphs"] == 1 and m["fully_covered"] is False)
                return ok, {"expected": "sans 夹具缺 U+F00FF：6 字形=3180 units→76.32pt，缺字如实计 1 且绝不谎称全覆盖",
                            "actual": {k: m[k] for k in fields}}
            finally:
                eng.close()

        def t_match_double_gate_isolates(E):
            eng = self._typo_env("match_gate")
            try:
                cands = eng.match_font("ZJFixSerif", text=self.TYPO_P1 + self.TYPO_P2, top_k=10)
                got = [{"family": c["family"], "weight": c["weight"],
                        "similarity": c["similarity"], "score": c["score"]} for c in cands]
                ok = got == [{"family": "ZJFixSerif", "weight": 700, "similarity": 1.0, "score": 1.0}]
                return ok, {"expected": "双专码位覆盖硬门槛过滤后仅剩 serif 唯一候选且 sim/score=1.0（sans 出豆腐块必被淘汰）",
                            "actual": got}
            finally:
                eng.close()

        def t_match_single_gate_weight_order(E):
            eng = self._typo_env("match_weight")
            try:
                cands = eng.match_font("ZJFixSerif", text="H" + self.TYPO_P1, weight=650, top_k=10)
                got = [{"family": c["family"], "weight": c["weight"],
                        "similarity": c["similarity"], "score": c["score"]} for c in cands]
                ok = got == [{"family": "ZJFixSerif", "weight": 700, "similarity": 1.0, "score": 0.9861},
                             {"family": "ZJFixSans", "weight": 400, "similarity": 0.6316, "score": 0.6542}]
                return ok, {"expected": "单码位门槛两候选共存：0.75×sim+0.25×字重距离罚分降序 0.9861>0.6542 逐项精确",
                            "actual": got}
            finally:
                eng.close()

        def t_fallback_rule_persisted(E):
            eng = self._typo_env("fallback")
            try:
                rule = eng.add_fallback("ZJGone", ["ZJFixSans"])
                rm = eng.resolve_missing("ZJGone", text="Hello")
                disk = json.loads((eng.store_dir / "fallback-rules.json").read_text(encoding="utf-8"))
                ok = (rule == {"from": "ZJGone", "to": ["ZJFixSans"]}
                      and rm["source"] == "fallback_rule" and rm["fallback_installed"] is True
                      and [{"requested": r["requested"], "matched_family": r["matched_family"]}
                           for r in rm["resolved"]]
                      == [{"requested": "ZJFixSans", "matched_family": "ZJFixSans"}]
                      and len(disk) == 4 and any(r.get("from") == "ZJGone" for r in disk))
                return ok, {"expected": "规则原样回显+resolve 标明来源 fallback_rule+命中真字体+规则文件真实落盘（3 种子+1 新增）",
                            "actual": {"rule": rule, "source": rm["source"],
                                       "resolved": [{"requested": r["requested"],
                                                     "matched_family": r["matched_family"]}
                                                    for r in rm["resolved"]],
                                       "installed": rm["fallback_installed"], "disk_n": len(disk)}}
            finally:
                eng.close()

        def t_ghost_and_heuristic_truth(E):
            eng = self._typo_env("negative")
            try:
                ghost = eng.get_font("ZjNoSuchFont9")
                err = None
                try:
                    eng.measure_text("ZjNoSuchFont9", "Hi", 12.0)
                except KeyError as e:
                    err = str(e)
                rm = eng.resolve_missing("TotallyAbsentName", text=self.TYPO_P1 + self.TYPO_P2)
                ok = (ghost is None and err == "'未找到字体: ZjNoSuchFont9'"
                      and rm["source"] == "heuristic_match" and len(rm["candidates"]) == 1
                      and rm["resolved"] == [{"requested": "TotallyAbsentName",
                                              "matched_family": "ZJFixSerif", "id": "fix_serif"}])
                return ok, {"expected": "幽灵字体 get 判 None、度量必抛 KeyError 原文；无规则时启发式过双门槛只剩 serif 唯一候选",
                            "actual": {"ghost": ghost, "keyerror": err, "source": rm["source"],
                                       "n_candidates": len(rm["candidates"]),
                                       "resolved": rm["resolved"]}}
            finally:
                eng.close()

        # ---------- LICENSE：license_service 签发/验签真值（探针逐字段钉死后抄录） ----------
        def l_roundtrip_deterministic(E):
            m = E["license"]
            k1 = m.issue_license_key(self.LIC_PAYLOAD)
            k2 = m.issue_license_key(self.LIC_PAYLOAD)
            verified, payload = m.verify_license_key(k1, "ANYMACHINE")
            ok = (k1 == k2 and k1.startswith("ZAOJING.")
                  and verified is True and payload == self.LIC_PAYLOAD)
            return ok, {"expected": "同载荷两次票据字节全等；验签 True 且载荷全等回显",
                        "actual": {"deterministic": k1 == k2, "prefix": k1[:8],
                                   "verified": verified, "payload": payload}}

        def l_tamper_and_format_exact(E):
            m = E["license"]
            k = m.issue_license_key(self.LIC_PAYLOAD)
            sig = k.split(".")[2]
            tam = k.replace(sig, ("A" if sig[0] != "A" else "B") + sig[1:])
            tamper = m.verify_license_key(tam, "M")
            # 真相钉死：urlsafe_b64decode(validate=False) 静默丢弃非法字符，
            # "@@.@@@@" 不落入编码错误分支而落到签名失败分支（实际行为，非理想行为）
            fmt_cases = [
                ("", (False, "密钥为空")),
                ("nope", (False, "格式无效：应为 ZAOJING.<载荷>.<签名>")),
                ("ZAOJING.A.B", (False, "编码无效：载荷或签名不是合法的 Base64URL")),
                ("ZAOJING.@@.@@@@", (False, "签名校验失败：密钥被篡改或非本产品签发")),
            ]
            got = [m.verify_license_key(bad, "M") for bad, _ in fmt_cases]
            arr_ok, arr_msg = m.verify_license_key(_lic_array_key(m), "M")
            ok = (tamper == (False, "签名校验失败：密钥被篡改或非本产品签发")
                  and got == [want for _, want in fmt_cases]
                  and (arr_ok, arr_msg) == (False, "载荷结构无效：签名载荷必须是 JSON 对象"))
            return ok, {"expected": "篡改与四非法格式与数组载荷报错原文逐字",
                        "actual": {"tamper": list(tamper), "formats": [list(g) for g in got],
                                   "array_payload": [arr_ok, arr_msg]}}

        def l_expiry_three_states(E):
            m = E["license"]
            k_old = m.issue_license_key({"edition": "pro", "exp": "2001-01-01T00:00:00+00:00"})
            r_old = m.verify_license_key(k_old, "M")
            k_new = m.issue_license_key({"edition": "pro", "exp": "2099-12-31T23:59:59+00:00"})
            r_new = m.verify_license_key(k_new, "M")
            k_bad = m.issue_license_key({"edition": "pro", "exp": "not-a-date"})
            r_bad = m.verify_license_key(k_bad, "M")
            ok = (r_old == (False, "授权已过期：2001-01-01T00:00:00+00:00")
                  and r_new[0] is True and r_bad == (False, "过期时间格式无效"))
            return ok, {"expected": "过原文/未来真过/非法日期原文三态",
                        "actual": {"expired": list(r_old), "future": r_new[0],
                                   "badexp": list(r_bad)}}

        def l_machine_bind(E):
            m = E["license"]
            k = m.issue_license_key({"edition": "pro", "mid": "ZJ36", "seats": 1})
            hit = m.verify_license_key(k, "ZJ36abcdef")
            miss = m.verify_license_key(k, "OTHER")
            ok = (hit[0] is True
                  and miss == (False, "机器绑定不符：该密钥不适用于本机（期望前缀 ZJ36）"))
            return ok, {"expected": "前缀 startswith 匹配过；不符原文逐字",
                        "actual": {"match": hit[0], "mismatch": list(miss)}}

        # ---------- CONFIG：config_persistence 默认真值·冷读回·迁移·路径拦截 ----------
        def c_defaults_literal(E):
            mgr, cfgp, _ = self._config_env("defaults")
            root_val = mgr.get("paths")["data_root"]
            # 真相：_resolve_all_paths 只展开"整串恰为 ${...}"的值，
            # "${LOCALAPPDATA}\\Zaojing" 后缀拼接串保持字面不展开（实际行为如实钉死）
            ok = (cfgp.is_file() and mgr.get("version") == "1.0.0"
                  and mgr.get("assets")["engine_url"] == "http://127.0.0.1:8082"
                  and len(mgr.get("blocked_paths")) == 7
                  and len(mgr.get("allowed_extensions")) == 13
                  and root_val == "${LOCALAPPDATA}\\Zaojing"
                  and E["config"].expand_path("${LOCALAPPDATA}").is_absolute())
            return ok, {"expected": "1.0.0/8082/7/13/拼接串字面保留/整串变量可展开",
                        "actual": {"version": mgr.get("version"),
                                   "engine_url": mgr.get("assets")["engine_url"],
                                   "blocked_n": len(mgr.get("blocked_paths")),
                                   "allowed_n": len(mgr.get("allowed_extensions")),
                                   "data_root": root_val}}

        def c_cold_readback(E):
            mgr, cfgp, _ = self._config_env("readback")
            mgr.set("zj_probe_key", {"n": 42, "tag": "p36"})
            mgr.save()
            fresh = E["config"].UserSettingsManager(config_path=cfgp)
            ok = (fresh.get("zj_probe_key") == {"n": 42, "tag": "p36"}
                  and "zj_probe_key" in cfgp.read_text(encoding="utf-8"))
            return ok, {"expected": "全新实例读回刚写入键且磁盘文件含该键",
                        "actual": {"readback": fresh.get("zj_probe_key"),
                                   "on_disk": cfgp.is_file()}}

        def c_migration(E):
            mgr, cfgp, _ = self._config_env("migrate")
            mgr.set("zj_probe_key", "marker-kept")
            mgr.save()
            disk = json.loads(cfgp.read_text(encoding="utf-8"))
            disk["version"] = "0.9.9"
            disk.pop("allowed_extensions", None)
            cfgp.write_text(json.dumps(disk), encoding="utf-8")
            fresh = E["config"].UserSettingsManager(config_path=cfgp)
            saved = json.loads(cfgp.read_text(encoding="utf-8"))
            ok = (fresh.get("version") == "1.0.0"
                  and fresh.get("zj_probe_key") == "marker-kept"
                  and len(fresh.get("allowed_extensions")) == 13
                  and saved["version"] == "1.0.0")
            return ok, {"expected": "旧版本抬回 1.0.0/用户标记保留/缺失键补回/迁移真实落盘",
                        "actual": {"version": fresh.get("version"),
                                   "marker": fresh.get("zj_probe_key"),
                                   "allowed_n": len(fresh.get("allowed_extensions")),
                                   "saved_version": saved["version"]}}

        def c_path_guard(E):
            from pathlib import Path as _P
            mgr, _cfgp, root = self._config_env("guard")
            chk = E["config"].PathSecurityChecker([_P("C:/Windows"), _P("C:/Program Files")])
            probes = [
                ("C:/Windows/explorer.exe", True),
                ("C:/Windows", True),
                ("C:/Users/x/../Windows/system32/cmd.exe", True),
                (str(root / "evil.jpg.exe"), True),
                (str(root / "photo.png"), False),
                (str(root / "archive.tar.gz"), False),
            ]
            got = [chk.is_blocked(p) for p, _ in probes]
            ok = (got == [want for _, want in probes]
                  and chk._has_double_extension("a.png.exe") is True
                  and chk._has_double_extension("a.tar.gz") is False)
            return ok, {"expected": "四阻两放 + 双扩展名两态（.tar.gz 不误报）",
                        "actual": {"blocked": got}}

        # ---------- MODEL_ROUTING：自供注册表上的选路/成本/预算/熔断真值 ----------
        def mr_registry_parse(E):
            mrm, reg, _router = self._router_env("registry")
            mp = reg.models["m_pro"]
            ok = (len(reg.models) == 2 and len(reg.providers) == 2
                  and mp.context_window == 32768
                  and mp.type == mrm.ModelType.TEXT
                  and mp.provider == mrm.ProviderType.CLOUD_DOUBAO)
            return ok, {"expected": "2 模型 2 provider；m_pro 枚举与上下文逐字段",
                        "actual": {"models": len(reg.models), "providers": len(reg.providers),
                                   "ctx": mp.context_window, "type": mp.type.value,
                                   "provider": mp.provider.value}}

        def mr_cost_tie_formula(E):
            mrm, reg, router = self._router_env("cost")
            picked = router.select_provider("m_pro", {"priority": "cost"})
            # 真相：_estimate_total_cost 按模型均价 (input+output)/2 计而非 provider 自身，
            # 两 provider 得分同为 0.004 → 稳定排序平票按插入序取先注册的 p_local
            est = router._estimate_total_cost(reg.providers["p_cloud"], reg.models["m_pro"])
            unknown = router.select_provider("no-such-model", {"priority": "cost"})
            ok = picked == "p_local" and est == (0.002 + 0.006) / 2 and unknown is None
            return ok, {"expected": "cost 平票插入序 p_local；模型均价式 0.004；未知 None",
                        "actual": {"picked": picked, "est": est, "unknown": unknown}}

        def mr_privacy_latency(E):
            _mrm, _reg, router = self._router_env("privlat")
            priv = router.select_provider("m_pro", {"priority": "privacy"})
            lat = router.select_provider("m_pro", {"priority": "latency"})
            # 真相：latency 读 circuit_breaker 的 avg_latency_ms（云 120 < 本地 500），
            # 而非模型条目的 1200/500 —— 假期望写成 p_local 必红（见校准负控）
            ok = priv == "p_local" and lat == "p_cloud"
            return ok, {"expected": "privacy→p_local；latency→p_cloud（熔断配置均值裁决）",
                        "actual": {"privacy": priv, "latency": lat}}

        def _no_loop_call(fn):
            # 新建线程必无运行中事件循环：把熔断判分彻底钉在"无循环"语境上
            # （Phase 36 起此处曾以"抛 RuntimeError"为真值；Phase 60 根治后，
            # 同一枚无循环语境改用来证明"开环与复口都不再需要事件循环在场"）
            import threading
            box = {}

            def target():
                try:
                    fn()
                    box["exc"] = None
                except Exception as e:
                    box["exc"] = e
            t = threading.Thread(target=target)
            t.start()
            t.join()
            return box["exc"]

        def mr_budget_breaker(E):
            mrm, reg, router = self._router_env("budget")
            router.record_cost("p_cloud", 100.0)
            after_cloud = router.select_provider("m_pro", {"priority": "latency"})
            router.record_cost("p_local", 500.0)
            all_over = router.select_provider("m_pro", {"priority": "cost"})

            # 臂 1 开环+排除：夹具默认窗口 60 s，无运行循环的线程里失败两轮即开环
            _mrm2, reg2, router2 = self._router_env("breaker")
            pl = reg2.providers["p_local"]

            def _two_failures():
                pl.record_failure()
                pl.record_failure()
            exc = _no_loop_call(_two_failures)
            breaker_msg = str(exc) if exc is not None else None
            opened = pl.can_execute() is False
            excluded = router2.select_provider("m_pro", {"priority": "privacy"})

            # 臂 2 无环到点复口：窗口必须在开环之前设好——开环那一刻就把"最迟可复口时刻"
            # 钉成单调时间戳，事后改配置不会撤掉已开的那一环（归因见 p60_probe11_diag.txt：
            # 先开后改窗口的写法让复口永远不发生，那是探针写错，不是产品缺陷）。
            # 窗口取 0.05 s、睡 0.2 s（4 倍余量），判据钉"到点必复口"而非绝对毫秒。
            _mrm3, reg3, _router3 = self._router_env("rearm")
            pr = reg3.providers["p_local"]
            box60 = {}

            def _open_and_wait():
                pr.config.circuit_breaker["failure_threshold"] = 1
                pr.config.circuit_breaker["reset_timeout_seconds"] = 0.05
                pr.record_failure()
                box60["open_now"] = pr.can_execute() is False
                time.sleep(0.2)
                box60["rearmed"] = pr.can_execute()
            rearm_exc = _no_loop_call(_open_and_wait)
            rearm_without_loop = (rearm_exc is None and box60.get("open_now") is True
                                  and box60.get("rearmed") is True)

            # 臂 3 环内同样不抛：独立沙箱，在有运行循环的线程里开环（create_task 已退役，
            # 有环/无环两语境的判分须一致）
            _mrm4, reg4, _router4 = self._router_env("inloop")

            async def _inloop():
                pc = reg4.providers["p_cloud"]
                pc.config.circuit_breaker["failure_threshold"] = 1
                pc.record_failure()
                return pc.can_execute()
            inloop_can_execute = _async_run(_inloop)
            ok = (after_cloud == "p_local" and all_over is None and opened is True
                  and exc is None and breaker_msg is None
                  and excluded == "p_cloud" and rearm_without_loop is True
                  and inloop_can_execute is False)
            # Phase 60 就地改核：载荷键 can_execute_after 改名 opened_after_failures——
            # 旧键名里装的是"已开环"的布尔，读侧极易当成 can_execute 的返回值误读
            # （本轮归因时就先误读了一次，见 p60_probe11_diag.txt）。
            return ok, {"expected": "预算严格小于排除→p_local；全超 None；"
                                    "熔断开环不依赖事件循环（无循环不抛错但状态已翻、"
                                    "开环者被选路排除、窗口走完后无循环也复口、环内同样不抛）",
                        "actual": {"after_cloud_over": after_cloud, "all_over": all_over,
                                   "breaker_exc": breaker_msg,
                                   "opened_after_failures": opened,
                                   "privacy_after_open": excluded,
                                   "rearm_without_loop": rearm_without_loop,
                                   "rearm_exc": str(rearm_exc) if rearm_exc is not None else None,
                                   "inloop_can_execute": inloop_can_execute}}

        # ---------- 负控校准（期望故意写错：诚实判分必须判 fail） ----------
        def k_contrast_wrong(E):
            r = E["color"].contrast("#000000", "#FFFFFF")
            ok = _close(r["ratio"], 12.345, 1e-9)  # 真值 21，此期望必须失败
            return ok, {"note": "校准用例：期望被故意写错为 12.345", "actual_ratio": r["ratio"]}

        def k_collide_wrong(E):
            objs = [{"id": "A", "x": 0, "y": 0, "width": 20, "height": 20},
                    {"id": "B", "x": 30, "y": 0, "width": 20, "height": 20}]
            r = E["layout"].collide(objs)
            ok = r["collision_count"] == 7  # 真相为 0，此期望必须失败
            return ok, {"note": "校准用例：期望被故意写错为 7", "actual_count": r["collision_count"]}

        def k_brand_palette_wrong_expect(E):
            fx = self._brand_fixture()
            r = E["brand"].analyze_palette(fx["two_band"], k=2)
            hexes = sorted(p["hex"].upper() for p in r["palette"])
            ok = hexes == sorted(["#0000FF", "#FFD700"])  # 真相为绿/红双色，此期望必须判 fail
            return ok, {"note": "校准用例：品牌调色板期望被故意写错为蓝/金", "actual_hexes": hexes}

        def k_print_wrong_expect(E):
            # 真相：RGB 低分图必含 PRINT-001；此处故意期望"不含"，诚实判分必须判 fail
            fx = self._print_fixture()
            r = E["print_qa"].check_document(fx["rgb"])
            codes = {f.code for f in r.findings}
            ok = "PRINT-001" not in codes
            return ok, {"note": "校准用例：印前期望被故意写错为 RGB 不含 PRINT-001", "actual_codes": sorted(codes)}

        def k_rag_wrong_expect(E):
            # 真相：咖啡查询下 coffee 排第一；此处故意期望 brake 排第一，诚实判分必须判 fail
            kb = E["knowledge_rag"]
            kb.ingest_text("咖啡烘焙 温度 曲线 第一爆 二爆 发展期 豆子 产区 风味",
                           title="咖啡烘焙笔记", scope="private", item_id="kb_eval_coffee")
            kb.ingest_text("汽车刹车片 磨损 更换 制动盘 卡钳 厚度 安全 保养周期",
                           title="刹车片保养", scope="private", item_id="kb_eval_brake")
            hits = kb.retrieve("咖啡 烘焙 温度 曲线", top_k=5, allowed_scopes=["private"])
            ok = bool(hits) and hits[0]["knowledge_id"] == "kb_eval_brake"
            return ok, {"note": "校准用例：检索排序期望被故意写错为 brake 居首",
                        "actual_top": hits[0]["knowledge_id"] if hits else None}

        def k_revision_wrong_expect(E):
            # 真相：主标题应用改稿后变 #8B0000；此处故意期望"保持 #111111 不变"，诚实判分必须判 fail
            eng = E["revision"]
            objs = _rv_objs()
            p = eng.parse("主标题换成深红色", objs)
            approved = [c for c in p["changes"] if c["status"] == "proposed"]
            ap = eng.apply(objs, p["changes"], only_ids=[c["id"] for c in approved])
            title = next(o for o in ap["objects"] if o.get("_id") == "obj_title")
            ok = title["color"] == "#111111"
            return ok, {"note": "校准用例：改稿应用结果期望被故意写错为未变色", "actual_color": title["color"]}

        def k_review_wrong_expect(E):
            # 真相：缺陷夹具 score=68；此处故意期望满分 100，诚实判分必须判 fail
            r = E["review"].review(_rw_dirty_objs(), canvas=_rw_canvas())
            ok = r["score"] == 100
            return ok, {"note": "校准用例：缺陷文档评分期望被故意写错为满分", "actual_score": r["score"]}

        def k_security_wrong_expect(E):
            # 真相：正常文件绝不被阻断；此处故意期望"被阻断"，诚实判分必须判 fail
            ck = E["security"]
            blocked = ck.is_blocked("C:\\Users\\public\\design.psd")
            ok = blocked is True
            return ok, {"note": "校准用例：路径判定期望被故意写错为正常文件遭阻断", "actual_blocked": blocked}

        def k_workflow_wrong_expect(E):
            # 真相：未注册 ghost 工具必令 n2 failed 且实例 failed；此处故意期望"n2 成功"，诚实判分必须判 fail
            st = _wf_ghost_status()
            ok = st["node_status"].get("n2") == "success"
            return ok, {"note": "校准用例：工作流失败传播期望被故意写错为成功",
                        "actual_node_status": st["node_status"]}

        def k_asset_wrong_expect(E):
            # 真相：咖啡杯描述符查询必居首；此处故意期望"锅图居首"，诚实判分必须判 fail
            import asset_engine as aem
            fx = self._asset_fixture()
            eng = self._asset_eng("calib")
            _async_run(lambda: eng.add_asset(fx["mug"], "image/png", width=120, height=80))
            _async_run(lambda: eng.add_asset(fx["pot"], "image/jpeg", width=40, height=40))
            res = _async_run(lambda: eng.search_assets(
                aem.embed_text("coffee_mug png image/png image"), top_k=2))
            top = eng.get_asset(res[0][0]).filename if res else None
            ok = top == "cooking_pot.jpg"
            return ok, {"note": "校准用例：素材检索排序期望被故意写错为锅图居首", "actual_top": top}

        def k_recovery_wrong_expect(E):
            # 真相：幽灵快照还原必判 False；此处故意期望"还原成功"，诚实判分必须判 fail
            eng, doc = self._recovery_env("calib")
            ok = _async_run(lambda: eng.snapshot_manager.restore_snapshot("no-such-snapshot")) is True
            return ok, {"note": "校准用例：恢复判定期望被故意写错为幽灵快照可还原",
                        "actual": "restore(ghost) 真值为 False"}

        def k_typo_width_wrong_expect(E):
            # 真相：serif 夹具 7 字形宽=95.736pt；此处故意期望 sans 数值 76.32，诚实判分必须判 fail
            eng = self._typo_env("calib")
            try:
                m = eng.measure_text("fix_serif", "Hello" + self.TYPO_P1 + self.TYPO_P2, 24.0)
                ok = _close(m["width_pt"], 76.32, 1e-9)
                return ok, {"note": "校准用例：排版度量宽度假期望写成 sans 的 76.32",
                            "actual_width": m["width_pt"]}
            finally:
                eng.close()

        def k_typo_gate_wrong_expect(E):
            # 真相：双专码位门槛过滤后仅 serif 1 候选；此处故意期望两候选并存，诚实判分必须判 fail
            eng = self._typo_env("calib_gate")
            try:
                cands = eng.match_font("ZJFixSerif", text=self.TYPO_P1 + self.TYPO_P2, top_k=10)
                ok = [c["family"] for c in cands] == ["ZJFixSerif", "ZJFixSans"]
                return ok, {"note": "校准用例：覆盖门槛假期望写成 sans 也过关",
                            "actual_families": [c["family"] for c in cands]}
            finally:
                eng.close()

        def k_barcode_wrong_expect(E):
            # 真相：590123412345 的校验位是 7；此处故意期望 0，诚实判分必须判 fail
            r, _ = _bc(E, "590123412345", "EAN13", "eval_p34_kbc.png")
            ok = r["check_digit"] == "0"
            return ok, {"note": "校准用例：条码校验位假期望写成 0",
                        "actual_check": r["check_digit"]}

        def k_barcode_qr_wrong_expect(E):
            # 真相：L 级 17 字符恰为手抄 Table 8 满容，必落 v1；此处故意期望升 v2，诚实判分必须判 fail
            r, _ = _qr(E, "z" + "A" * (P57_QR_BYTE_CAP["L"][0] - 1), "eval_p57_kqr.png",
                       ec_level="L")
            ok = r["version"] == 2
            return ok, {"note": "校准用例：QR 版本假期望把满容写成需升版",
                        "actual": {"payload_chars": P57_QR_BYTE_CAP["L"][0],
                                   "version": r["version"], "hand_copy_L_cap_v1":
                                   P57_QR_BYTE_CAP["L"][0]}}

        def k_topology_wrong_expect(E):
            # 真相：相距 200 的两面板必判 disconnected；此处故意期望连通，诚实判分必须判 fail
            r = E["packaging"].analyze_panel_topology(
                [{"id": "A", "x": 0, "y": 0, "width": 50, "height": 50},
                 {"id": "B", "x": 200, "y": 0, "width": 50, "height": 50}])
            ok = r["status"] == "ok" and r["is_connected"] is True
            return ok, {"note": "校准用例：拓扑假期望写成断开刀版判连通",
                        "actual_status": r["status"]}

        def k_license_wrong_expect(E):
            # 真相：篡改签名的票据必判失败；此处故意期望"验签通过"，诚实判分必须判 fail
            m = E["license"]
            k = m.issue_license_key(self.LIC_PAYLOAD)
            sig = k.split(".")[2]
            tam = k.replace(sig, ("A" if sig[0] != "A" else "B") + sig[1:])
            ok = m.verify_license_key(tam, "M")[0] is True
            return ok, {"note": "校准用例：篡改密钥假期望写成验签通过",
                        "actual_msg": m.verify_license_key(tam, "M")[1]}

        def k_config_wrong_expect(E):
            # 真相：拼接串后缀不展开、保持字面；此处故意期望已展开为绝对路径，诚实判分必须判 fail
            mgr, _cfgp, _root = self._config_env("calib_cfg")
            val = mgr.get("paths")["data_root"]
            ok = Path(val).is_absolute()
            return ok, {"note": "校准用例：data_root 假期望写成已展开绝对路径",
                        "actual_data_root": val}

        def k_routing_wrong_expect(E):
            # 真相：latency 依熔断配置均值 120<500 必选 p_cloud；此处故意期望 p_local，必判 fail
            _mrm, _reg, router = self._router_env("calib_router")
            picked = router.select_provider("m_pro", {"priority": "latency"})
            ok = picked == "p_local"
            return ok, {"note": "校准用例：路由假期望写成延迟模式选本地",
                        "actual_picked": picked}

        # ---------- Phase 37 UPDATE：update_engine（期望逐项由 P37 探针 86/86 钉死后抄录） ----------
        def u_no_feed_honest(E):
            upd, _sd = self._update_env("nofeed")
            r = upd.check("stable")
            ch = upd.get_channels()
            msg = None
            try:
                upd.configure("prod", feed_url="http://127.0.0.1:9/m")
            except ValueError as e:
                msg = str(e)
            actual = {"status": r["status"], "verified": r["verified"],
                      "fabricated": r["fabricated"], "channels": len(ch["channels"]),
                      "all_unconfigured": all(c["configured"] is False for c in ch["channels"]),
                      "channels_fabricated": ch["fabricated"], "bad_channel": msg}
            ok = (actual["status"] == "no_update_configured" and actual["verified"] is False
                  and actual["fabricated"] is False and actual["channels"] == 3
                  and actual["all_unconfigured"] is True
                  and actual["channels_fabricated"] is False
                  and actual["bad_channel"] == "未知渠道 prod（可用: stable, beta, dev）")
            return ok, {"actual": actual}

        def u_sign_canonical(E):
            import hmac as _h
            UE = E["update"].UpdateEngine
            man = {"version": "2.0.0", "url": "http://update.invalid/art.bin",
                   "sha256": "0" * 64, "notes": "eval-p37"}
            exp_canon = json.dumps(man, sort_keys=True, separators=(",", ":")).encode("utf-8")
            canon = UE.canonical_bytes(man)
            sig = UE.sign(man, "zj-eval-p37")
            exp_sig = _h.new("zj-eval-p37".encode("utf-8"), exp_canon, hashlib.sha256).hexdigest()
            signed = dict(man, signature=sig)
            actual = {"canon_equal": canon == exp_canon, "sig_independent_equal": sig == exp_sig,
                      "sig_hex64": len(sig) == 64 and all(c in "0123456789abcdef" for c in sig),
                      "verify_ok": UE.verify(signed, "zj-eval-p37"),
                      "verify_tampered": UE.verify(dict(signed, version="9.9.9"), "zj-eval-p37"),
                      "verify_wrong_secret": UE.verify(signed, "other")}
            ok = (actual["canon_equal"] and actual["sig_independent_equal"]
                  and actual["sig_hex64"] and actual["verify_ok"] is True
                  and actual["verify_tampered"] is False
                  and actual["verify_wrong_secret"] is False)
            return ok, {"actual": actual}

        def u_download_apply(E):
            upd, _sd = self._update_env("chain")
            fx = self.store_dir / "_update" / "chain_fx"
            shutil.rmtree(fx, ignore_errors=True)
            fx.mkdir(parents=True)
            target = fx / "app.bin"
            target.write_bytes(b"OLD-VERSION")
            art = b"ZAOJING-P37-ARTIFACT-BYTES"
            art_sha = hashlib.sha256(art).hexdigest()
            man = {"version": "2.0.0", "url": "@ART@", "sha256": art_sha, "notes": "eval-p37"}
            mock = _P37MockHTTP()
            try:
                man["url"] = mock.base + "/art.bin"
                signed = dict(man, signature=E["update"].UpdateEngine.sign(man, "zj-eval-p37"))
                upd.configure("dev", feed_url=mock.base + "/manifest.json",
                              secret="zj-eval-p37", current_version="1.0.0")
                red = upd._redacted("dev")
                mock.routes["/manifest.json"] = (200, json.dumps(signed).encode(), "application/json")
                mock.routes["/art.bin"] = (200, art, "application/octet-stream")
                chk = upd.check("dev")
                dl = upd.download("dev", staged_dir=str(fx / "staged"))
                staged = Path(dl["staged_path"])
                ap = upd.apply(str(target), str(staged), expected_sha256=art_sha)
                pend = upd.pending()
                seen = [s["path"] for s in mock.seen]
            finally:
                mock.shutdown()
            actual = {"check": (chk["status"], chk["verified"],
                                (chk.get("current_version"), chk.get("remote_version"))),
                      "sha": chk.get("sha256") == art_sha,
                      "redacted_clean": "zj-eval-p37" not in json.dumps(red)
                                        and red["has_secret"] is True and red["configured"] is True,
                      "dl": (dl["status"], dl["integrity"], dl["bytes"]),
                      "staged_true": staged.exists() and staged.read_bytes() == art,
                      "apply": (ap["status"], ap.get("applied")),
                      "target_replaced": target.read_bytes() == art
                                         and hashlib.sha256(target.read_bytes()).hexdigest() == art_sha
                                         and not Path(str(target) + ".bak").exists(),
                      "has_pending": pend["has_pending"], "seen": seen}
            ok = (actual["check"] == ("update_available", True, ("1.0.0", "2.0.0"))
                  and actual["sha"] and actual["redacted_clean"]
                  and actual["dl"] == ("downloaded", "sha256_match", len(art))
                  and actual["staged_true"] and actual["apply"] == ("applied", True)
                  and actual["target_replaced"] and actual["has_pending"] is False
                  and seen == ["/manifest.json", "/manifest.json", "/art.bin"])
            return ok, {"actual": actual}

        def u_branch_matrix(E):
            upd, _sd = self._update_env("branch")
            UE = E["update"].UpdateEngine
            art_sha = hashlib.sha256(b"ZAOJING-P37-ARTIFACT-BYTES").hexdigest()
            good = {"version": "2.0.0", "url": "http://127.0.0.1:9/art.bin",
                    "sha256": art_sha, "notes": "eval-p37"}
            signed = dict(good, signature=UE.sign(good, "zj-eval-p37"))
            down = {"version": "0.9", "url": "http://127.0.0.1:9/art.bin", "sha256": art_sha}
            down_s = dict(down, signature=UE.sign(down, "zj-eval-p37"))
            mock = _P37MockHTTP()
            r = {}
            try:
                upd.configure("dev", feed_url=mock.base + "/m1", secret="zj-eval-p37",
                              current_version="1.0.0")
                upd.configure("beta", feed_url=mock.base + "/up", current_version="9.9.9")
                mock.routes["/m1"] = (200, b"not-json{{{", "application/json")
                r["garbage"] = upd.check("dev")["status"]
                mock.routes["/m1"] = (200, json.dumps(dict(signed, notes="tampered")).encode(),
                                      "application/json")
                r["tampered"] = upd.check("dev")["status"]
                mock.routes["/up"] = (200, json.dumps(good).encode(), "application/json")
                rb = upd.check("beta")
                r["no_secret"] = (rb["status"], rb["verified"], rb.get("remote_version"))
                mock.routes["/up"] = (200, b"{}", "application/json")
                r["version_gate_first"] = upd.check("beta")["status"]
                upd.configure("beta", current_version="1.0.0", secret="zj-eval-p37")
                mock.routes["/up"] = (200, json.dumps(down_s).encode(), "application/json")
                rb = upd.check("beta")
                r["downgrade"] = (rb["status"], rb["verified"])
                upd.configure("beta", current_version="9.9.9")
                r["higher_current"] = upd.check("beta")["status"]
            finally:
                mock.shutdown()
            ok = (r["garbage"] == "manifest_invalid" and r["tampered"] == "integrity_failed"
                  and r["no_secret"] == ("signature_unverified", False, "2.0.0")
                  and r["version_gate_first"] == "manifest_invalid"
                  and r["downgrade"] == ("up_to_date", True)
                  and r["higher_current"] == "up_to_date")
            return ok, {"actual": r}

        # ---------- Phase 37 NAS：nas_engine（filesystem 形态，评测自造真目录） ----------
        def n_gate_matrix(E):
            nas, sd = self._nas_env("gates")
            share, _outside = self._nas_fix("gates")
            r = nas.connect("ghost")
            e1 = e2 = None
            try:
                nas.configure("x", "C:\\anything", "ftp")
            except ValueError as e:
                e1 = str(e)
            try:
                nas.configure("x", "   ", "filesystem")
            except ValueError as e:
                e2 = str(e)
            nas.configure("missing", target=str(sd / "no_such_share"), kind="filesystem",
                          share_root=str(share))
            rm = nas.connect("missing")
            actual = {"ghost": (r["status"], r["fabricated"]), "bad_kind": e1,
                      "blank_target": e2, "missing": rm["status"],
                      "reason_prefix": (rm.get("reason") or "").startswith("路径不存在或不可达：")}
            ok = (actual["ghost"] == ("not_configured", False)
                  and e1 == "未知 NAS 类型 ftp（可用: filesystem, webdav）"
                  and e2 == "target 不能为空" and actual["missing"] == "unreachable"
                  and actual["reason_prefix"])
            return ok, {"actual": actual}

        def n_list_truth(E):
            nas, _sd = self._nas_env("list")
            share, _ = self._nas_fix("list")
            nas.configure("home", target=str(share), kind="filesystem", share_root=str(share))
            rc = nas.connect("home")
            l1 = nas.list_dir("home")
            actual = {"connect": (rc["status"], rc["access"], rc["is_dir"]),
                      "names": [(e["name"], e["type"]) for e in l1["entries"]],
                      "sizes": (l1["entries"][0]["size"], l1["entries"][1]["size"]),
                      "cache": l1["cache"], "count": l1["count"],
                      "mtimes_int": all(isinstance(e["mtime"], int) for e in l1["entries"])}
            ok = (actual["connect"] == ("connected", "native-os-redirector", True)
                  and actual["names"] == [("a.txt", "file"), ("b.bin", "file"), ("sub", "dir")]
                  and actual["sizes"] == (5, 12) and actual["cache"] == "miss_stored"
                  and actual["count"] == 3 and actual["mtimes_int"])
            return ok, {"actual": actual}

        def n_path_guard(E):
            nas, sd = self._nas_env("guard")
            share, outside = self._nas_fix("guard")
            nas.configure("home", target=str(share), kind="filesystem", share_root=str(share))
            nas.connect("home")
            tv = nas.list_dir("home", subpath="../outside")
            ab = nas.list_dir("home", subpath=str(outside))
            dl = sd / "dl"
            dn = nas.download("home", "../outside/secret.txt", dest_dir=str(dl))
            bad = nas.download("home", "ghost.bin", dest_dir=str(dl))
            actual = {"traversal": (tv["status"], "路径穿越被拒绝" in tv["reason"]),
                      "absolute": (ab["status"], "绝对路径" in ab["reason"]),
                      "dl_traversal": dn["status"], "leaked": (dl / "secret.txt").exists(),
                      "ghost_dl": bad["status"]}
            ok = (actual["traversal"] == ("forbidden", True)
                  and actual["absolute"] == ("forbidden", True)
                  and actual["dl_traversal"] == "forbidden" and actual["leaked"] is False
                  and actual["ghost_dl"] == "not_found")
            return ok, {"actual": actual}

        def n_cache_cold(E):
            nas, sd = self._nas_env("cache")
            share, _ = self._nas_fix("cache")
            nas.configure("home", target=str(share), kind="filesystem", share_root=str(share))
            l1 = nas.list_dir("home")
            okd = nas.download("home", "a.txt", dest_dir=str(sd / "dl"))
            (share / "a.txt").unlink()
            (share / "b.bin").unlink()
            nas2 = E["nas"].NasEngine(state_dir=sd)
            lc = nas2.list_dir("home", use_cache=True)
            src = [(e["name"], e["type"], e["size"], e["mtime"]) for e in l1["entries"]]
            got = [(e["name"], e["type"], e["size"], e["mtime"]) for e in lc["entries"]]
            st = nas2.cache_stats()
            cc = nas2.cache_clear()
            st_after = nas2.cache_stats()
            fg = nas2.forget("home")
            actual = {"download": (okd["status"], okd["integrity"], okd["bytes"]),
                      "sha_true": okd["sha256"] == hashlib.sha256(b"12345").hexdigest()
                                  and Path(okd["path"]).read_bytes() == b"12345",
                      "cache": lc["cache"], "quads_equal": got == src and len(src) == 3,
                      "on_disk": st["entries"] >= 1 and st["fabricated"] is False,
                      "cleared": cc["cleared"] >= 1 and st_after["entries"] == 0,
                      "forget_removed": fg["removed"]}
            ok = (actual["download"] == ("downloaded", "sha256_computed", 5)
                  and actual["sha_true"] and actual["cache"] == "hit"
                  and actual["quads_equal"] and actual["on_disk"] and actual["cleared"]
                  and actual["forget_removed"] is True)
            return ok, {"actual": actual}

        # ---------- Phase 37 PROVIDER：ai_provider_engine（mock HTTP 驱动真实请求面） ----------
        def p_unconfigured_honest(E):
            prov, _sd = self._provider_env("none")
            c = prov.chat("你好")
            gi = prov.generate_image("cat")
            hl = prov.health()
            actual = {"chat": (c["status"], c["content"], c["fabricated"]),
                      "image": (gi["status"], gi["images_generated"]),
                      "health": (hl["status"], hl["reachable"]),
                      "configured": prov.configured()}
            ok = (actual["chat"] == ("no_provider_configured", "", False)
                  and actual["image"] == ("no_provider_configured", 0)
                  and actual["health"] == ("no_provider_configured", False)
                  and actual["configured"] is False)
            return ok, {"actual": actual}

        def p_key_dpapi(E):
            prov, sd = self._provider_env("keys")
            key = "sk-eval-p37-secret"
            mock = _P37MockHTTP()  # configure 只落盘不发请求：以空 routes 服务器钉死该实际行为
            try:
                v = prov.configure("mock", base_url=mock.base, api_key=key, model="zj-mock-1")
                n_cfg_reqs = len(mock.seen)
            finally:
                mock.shutdown()
            pv = v["providers"]["mock"]
            raw = (sd / "providers.json").read_text(encoding="utf-8")
            cipher = json.loads(raw)["mock"]["api_key_dpapi"]
            actual = {"masked": pv["api_key_masked"],
                      "mask_fn": E["provider"].mask_key(key),
                      "mask_literal": "sk-" + "*" * 11 + "cret",
                      "key_storage": pv["key_storage"], "has_key": pv["has_key"],
                      "cfg_zero_request": n_cfg_reqs == 0,
                      "plaintext_absent": key not in raw and "api_key_dpapi" in raw,
                      "dpapi_roundtrip": E["provider"].dpapi_unprotect(cipher) == key
                                         and cipher != key}
            ok = (actual["masked"] == actual["mask_fn"] == actual["mask_literal"]
                  and actual["key_storage"] == "dpapi" and actual["has_key"] is True
                  and actual["cfg_zero_request"] and actual["plaintext_absent"]
                  and actual["dpapi_roundtrip"])
            return ok, {"actual": actual}

        def p_chat_roundtrip(E):
            prov, _sd = self._provider_env("chat")
            key = "sk-eval-p37-secret"
            reply = {"model": "zj-mock-1",
                     "choices": [{"message": {"role": "assistant",
                                              "content": "收到：造境评测 P37"}}],
                     "usage": {"prompt_tokens": 7, "completion_tokens": 13,
                               "total_tokens": 20}}
            mock = _P37MockHTTP()
            try:
                mock.routes["/chat/completions"] = (200, json.dumps(reply).encode(),
                                                    "application/json")
                mock.routes["/models"] = (200, b'{"data":[]}', "application/json")
                prov.configure("mock", base_url=mock.base, api_key=key, model="zj-mock-1")
                mock.seen.clear()
                c = prov.chat("你好", stream=False)
                sent = dict(mock.seen[0]) if mock.seen else {}
                n_chat = len(mock.seen)
                h2 = prov.health()
                seq = [s["method"] for s in mock.seen]
            finally:
                mock.shutdown()
            actual = {"status_content": (c["status"], c["content"]),
                      "usage": (c["usage"]["total_tokens"], c["usage"]["prompt_tokens"],
                                c["usage"]["completion_tokens"]),
                      "model_provider": (c["model"], c["provider"], c["fabricated"]),
                      "auth": sent.get("auth"),
                      "body_model": json.loads(sent["body"])["model"] if sent.get("body") else None,
                      "one_request": n_chat == 1,
                      "health": (h2["status"], h2["reachable"], h2.get("http_status")),
                      "seq_post_then_get": seq == ["POST", "GET"]}
            ok = (actual["status_content"] == ("ok", "收到：造境评测 P37")
                  and actual["usage"] == (20, 7, 13)
                  and actual["model_provider"] == ("zj-mock-1", "mock", False)
                  and actual["auth"] == "Bearer " + key
                  and actual["body_model"] == "zj-mock-1" and actual["one_request"]
                  and actual["health"] == ("ok", True, 200)
                  and actual["seq_post_then_get"])
            return ok, {"actual": actual}

        def p_auth_breaker(E):
            prov, _sd = self._provider_env("breaker")
            mock = _P37MockHTTP()
            try:
                prov.configure("mock", base_url=mock.base, api_key="sk-eval-p37-secret",
                               model="zj-mock-1")
                mock.routes["/chat/completions"] = (401, b'{"error":"bad key"}',
                                                    "application/json")
                mock.seen.clear()
                c1 = prov.chat("hi")
                n_401 = len(mock.seen)
                for _ in range(5):
                    prov._record_failure("mock", threshold=5, reset_s=30.0)
                mock.seen.clear()
                c2 = prov.chat("hi")
                n_open = len(mock.seen)
                view_open = prov.view("mock")["providers"]["mock"]["circuit_open"]
            finally:
                mock.shutdown()
            actual = {"auth": (c1["status"], c1["error"], n_401),
                      "breaker": (c2["status"], c2["error"], n_open),
                      "view_open": view_open}
            ok = (actual["auth"] == ("error", "auth_failed", 1)
                  and actual["breaker"] == ("error", "circuit_open", 0)
                  and actual["view_open"] is True)
            return ok, {"actual": actual}

        # ---------- Phase 56 品牌语义识别：brand_semantics 域（真实 Provider 视觉透传，评测自起进程内 mock HTTP） ----------
        def _bs_wire(tag, content_obj_or_text, api_key="sk-eval-p56-vision-secret"):
            """构造 (brand_engine 实例含已配置 Provider, mock 服务器, 期望模型回复文本)。
            content 可为 dict（JSON 序列化后作为 choices[0].message.content）或原始字符串。
            self 由外层 _cases 方法闭包捕获，不经参数传入。"""
            import brand_engine as _be
            prov, sd = self._provider_env(tag)
            content = (content_obj_or_text if isinstance(content_obj_or_text, str)
                       else json.dumps(content_obj_or_text, ensure_ascii=False))
            reply = {"model": "vision-mock-p56",
                     "choices": [{"message": {"role": "assistant", "content": content}}],
                     "usage": {"prompt_tokens": 111, "completion_tokens": 22, "total_tokens": 133}}
            mock = _P37MockHTTP()
            mock.routes["/chat/completions"] = (200, json.dumps(reply).encode("utf-8"),
                                                "application/json")
            prov.configure("mockv", base_url=mock.base, api_key=api_key,
                           model="vision-mock-p56")
            mock.seen.clear()
            eng = _be.BrandEngine(color=self._engine("color"), provider=prov)
            return eng, mock, content

        def bs_unconfigured_honest(E):
            # 未配置 Provider 时诚实 needs_external + fabricated=False，绝无任何调性/置信度合成。
            eng, _sd = self._provider_env("unconf")  # 空配置 ProviderEngine
            import brand_engine as _be
            be = _be.BrandEngine(color=self._engine("color"), provider=eng)
            fx = self._brand_fixture()
            r = be.recognize_semantics(fx["logo"])
            ok = (r["status"] == "needs_external" and r["fabricated"] is False
                  and r["provider_status"] == "no_provider_configured" and "semantics" not in r)
            return ok, {"actual": {k: r.get(k) for k in
                                   ("status", "fabricated", "provider_status", "image_bytes")}}

        def bs_no_provider_injected(E):
            # 未注入 Provider（provider=None）时同样诚实 needs_external + reason=no_provider_injected。
            import brand_engine as _be
            be = _be.BrandEngine(color=self._engine("color"), provider=None)
            fx = self._brand_fixture()
            r = be.recognize_semantics(fx["logo"])
            ok = (r["status"] == "needs_external" and r["fabricated"] is False
                  and r["reason"] == "no_provider_injected" and "semantics" not in r)
            return ok, {"actual": {k: r.get(k) for k in
                                   ("status", "fabricated", "reason", "image_bytes")}}

        def bs_verbatim_passthrough(E):
            # 模型真实输出的语义 JSON 必须逐字段全等透传（本引擎不合成任何一个字符）。
            fixed = {"tone_keywords": ["稳重", "专业", "可信赖"],
                     "logo_semantics": "矩形负空间构成，几何无衬线感",
                     "typography_guess": "sans-serif，字重偏高",
                     "palette_notes": "主色为深蓝，辅色留白",
                     "confidence_note": "由所配置视觉模型给出，非本地合成"}
            key = "sk-eval-p56-abcdef123"
            eng, mock, _content = _bs_wire("pass", fixed, api_key=key)
            fx = self._brand_fixture()
            try:
                r = eng.recognize_semantics(fx["logo"])
            finally:
                mock.shutdown()
            ok = (r["status"] == "ok" and r["fabricated"] is False
                  and r["semantics"] == fixed  # 逐字段全等（含顺序无关的 dict 相等）
                  and r["usage"]["total_tokens"] == 133
                  and r["method"] == "real_provider_vision_passthrough"
                  and key not in json.dumps(r, ensure_ascii=False))
            return ok, {"expected": fixed, "actual_semantics": r.get("semantics"),
                        "usage": r.get("usage"), "fabricated": r.get("fabricated")}

        def bs_dataurl_bearer_independent(E):
            # 从磁盘真 PNG 字节独立复算 data URL，逐字符对账进模型的真实请求体；Bearer 头在场、明文密钥不落响应。
            import base64 as _b64
            fixed = {"tone_keywords": ["极简"], "logo_semantics": "几何标志"}
            eng, mock, _content = _bs_wire("durl", fixed, api_key="sk-eval-p56-secretkey9")
            fx = self._brand_fixture()
            logo_bytes = open(fx["logo"], "rb").read()
            expected_url = "data:image/png;base64," + _b64.b64encode(logo_bytes).decode("ascii")
            try:
                r = eng.recognize_semantics(fx["logo"])
                sent = mock.seen[0] if mock.seen else {}
                body = json.loads(sent.get("body") or b"{}")
            finally:
                mock.shutdown()
            msgs = body.get("messages", [])
            img_parts = [c.get("image_url", {}).get("url", "")
                         for m in msgs for c in (m.get("content") or [])
                         if isinstance(c, dict) and c.get("type") == "image_url"]
            ok = (r["status"] == "ok" and expected_url in img_parts
                  and sent.get("auth") == "Bearer sk-eval-p56-secretkey9"
                  and len(img_parts) == 1 and len(mock.seen) == 1
                  and "sk-eval-p56-secretkey9" not in json.dumps(r, ensure_ascii=False))
            return ok, {"expected_url_prefix": expected_url[:48],
                        "sent_url_count": len(img_parts),
                        "url_matches_recompute": expected_url in img_parts,
                        "auth": sent.get("auth"), "requests": len(mock.seen)}

        def bs_unparseable_raw(E):
            # 模型返回非 JSON：provider_output_unparseable + 保留 raw，绝不硬编造 schema 字段。
            eng, mock, _content = _bs_wire("unparse", "this is definitely not json <<<")
            fx = self._brand_fixture()
            try:
                r = eng.recognize_semantics(fx["logo"])
            finally:
                mock.shutdown()
            ok = (r["status"] == "provider_output_unparseable" and r["fabricated"] is False
                  and "raw" in r and "not json" in r["raw"] and "semantics" not in r)
            return ok, {"actual": {"status": r["status"], "fabricated": r["fabricated"],
                                   "raw_kept": "raw" in r, "semantics_present": "semantics" in r}}

        # ---------- Phase 37 校准负控（假期望必被诚实判分证伪） ----------
        def k_update_wrong_expect(E):
            upd, _sd = self._update_env("calib")
            down = {"version": "0.9", "url": "http://127.0.0.1:9/art.bin",
                    "sha256": hashlib.sha256(b"ZAOJING-P37-ARTIFACT-BYTES").hexdigest()}
            signed = dict(down, signature=E["update"].UpdateEngine.sign(down, "zj-eval-p37"))
            mock = _P37MockHTTP()
            try:
                upd.configure("dev", feed_url=mock.base + "/m", secret="zj-eval-p37",
                              current_version="1.0.0")
                mock.routes["/m"] = (200, json.dumps(signed).encode(), "application/json")
                status = upd.check("dev")["status"]
            finally:
                mock.shutdown()
            ok = status == "update_available"
            return ok, {"note": "校准用例：更新域假期望写成远程 0.9 低于当前 1.0.0 却判发现新版",
                        "actual_status": status}

        def k_nas_wrong_expect(E):
            # 真相：删源后冷实例必命中缓存判 hit；此处故意期望 miss_stored，诚实判分必须判 fail
            nas, sd = self._nas_env("calib")
            share, _ = self._nas_fix("calib")
            nas.configure("home", target=str(share), kind="filesystem", share_root=str(share))
            nas.list_dir("home")
            (share / "a.txt").unlink()
            cache = E["nas"].NasEngine(state_dir=sd).list_dir("home", use_cache=True).get("cache")
            ok = cache == "miss_stored"
            return ok, {"note": "校准用例：NAS 假期望写成缓存命中仍是 miss_stored",
                        "actual_cache": cache}

        def k_provider_wrong_expect(E):
            # 真相：未配置 Provider 时 chat 必诚实拒答；此处故意期望 status ok，诚实判分必须判 fail
            prov, _sd = self._provider_env("calib")
            status = prov.chat("x")["status"]
            ok = status == "ok"
            return ok, {"note": "校准用例：Provider 假期望写成未配置时 chat 判 ok",
                        "actual_status": status}

        def k_brand_semantics_wrong_expect(E):
            # 真相：未注入 Provider 时 recognize_semantics 必诚实 needs_external 且无 semantics；
            # 此处故意期望"凭空合成出调性关键词"（status ok + 有 semantics），诚实判分必须判 fail。
            import brand_engine as _be
            be = _be.BrandEngine(color=self._engine("color"), provider=None)
            fx = self._brand_fixture()
            r = be.recognize_semantics(fx["logo"])
            ok = (r["status"] == "ok" and bool(r.get("semantics", {}).get("tone_keywords")))
            return ok, {"note": "校准用例：品牌语义假期望写成无 Provider 时凭空合成调性关键词",
                        "actual_status": r["status"], "fabricated": r.get("fabricated")}

        # ---------- INSTALLER_EXECUTION（Phase 58 执行器）：门序 / 差集派生 / 自报不作数 / 台账对账 ----------
        # 分工：本域**零子进程**，只经 execute_install / execute_uninstall / execution_state 的
        # roots=/registry=/spawn=/build_ledger=/ledger_path= 显式接缝在进程内复现安装器副作用；
        # 真子进程假安装器由 L1 实证，真 Inno 真装真卸由冻结 EXE 的 install-exec CLI 实证。
        # 三侧共用同一套判定代码，任何一侧通过都不证明另一侧，故本域判分只钉纯数据可决定的部分。
        # 期望常量手抄在类属性 EXEC_* 上（判分不读引擎常量，防同源失真）。

        def ex_plan_disk_derived(E):
            eng = E["installer_runtime"]
            env = self._exec_env("plan")
            listing = self._exec_listing(env["sd"])
            p = eng.install_plan(artifact=str(env["art"]),
                                 app_id_braced=self.EXEC_APP_ID_SOURCE_FORM,
                                 roots=env["roots"], build_ledger=str(env["bled"]))
            after = self._exec_listing(env["sd"])
            raw = env["art"].read_bytes()          # 独立复算：评测自己读盘算 sha
            now = {t["key"]: Path(t["path"]).exists() for t in p["touched"]}
            ok = (p["schema"] == "zaojing-installer-exec/v1"
                  and p["capability"] == "installer_execution" and p["action"] == "plan"
                  and p["fabricated"] is False
                  and p["artifact"]["sha256"] == hashlib.sha256(raw).hexdigest()
                  and p["artifact"]["bytes"] == len(raw) == len(self.EXEC_ARTIFACT)
                  and p["build_ledger"]["sha_cross_equal"] is True
                  and p["build_ledger"]["iscc_exit_code"] == 0
                  and self._exec_rel(env["sd"], [p["install_dir"]])[0]
                  == "fake-local/Programs/造境科技"
                  and p["silent_flags"] == self.EXEC_FLAGS
                  and p["argv_template"] == ["{artifact}", *self.EXEC_FLAGS,
                                             "/DIR={install_dir}", "/LOG={log_path}"]
                  and p["gates"] == self.EXEC_GATES
                  and p["confirm_token"] == self.EXEC_CONFIRM
                  # AppId 源文双写括号 ⇒ 生效卸载键必须是解括号后的 {GUID}_is1（P53 转义真值）
                  and p["uninstall_registry_key"] == self.EXEC_UNINSTALL_KEY_REAL
                  and not p["uninstall_registry_key"].startswith("{{")
                  and [t["key"] for t in p["touched"]] == ["install_root", "userdocs",
                                                           "start_menu_group",
                                                           "runtime_data_root", "vendor_dir"]
                  and now == {t["key"]: t["exists_now"] for t in p["touched"]}
                  and sorted(now.items()) == [("install_root", False),
                                              ("runtime_data_root", True),
                                              ("start_menu_group", False),
                                              ("userdocs", False), ("vendor_dir", False)]
                  # 只算不写：plan 前后沙箱逐条（名+字节+sha）全等，且安装根未被创建
                  and after == listing and not Path(env["install_dir"]).exists())
            return ok, {"expected": "plan 只算不跑：制品 sha/字节与独立读盘复算全等；五枚落点标签"
                                    "的 exists_now 逐枚与现场重算一致；卸载键由 {{源文} 解成 "
                                    "{GUID}_is1；七道门·四静默开关·argv 模板·确认令牌逐字；"
                                    "沙箱逐条不变（零落盘）",
                        "actual": {"sha_equal_recompute": p["artifact"]["sha256"]
                                   == hashlib.sha256(raw).hexdigest(),
                                   "install_dir_rel": self._exec_rel(env["sd"],
                                                                     [p["install_dir"]])[0],
                                   "uninstall_key": p["uninstall_registry_key"],
                                   "gates": p["gates"],
                                   "exists_now": sorted(now.items()),
                                   "listing_stable": after == listing}}

        def ex_gates_refuse_zero_side_effects(E):
            eng = E["installer_runtime"]
            K = self._exec_kw
            U = self._exec_un_kw

            def _plant_outside_lean(env):
                """等长替换制品：字节数与台账相同、路径相同，只有 sha 会说话（P49 同款臂）。"""
                env["art"].write_bytes(b"\x90" * len(self.EXEC_ARTIFACT))

            def _plant_len_changed(env):
                env["art"].write_bytes(self.EXEC_ARTIFACT + b"tail")

            def _ledger_points_elsewhere(env):
                copy = env["sd"] / "setup-copy.exe"
                copy.write_bytes(self.EXEC_ARTIFACT)      # 同字节 ⇒ 同 sha，只有路径不同
                env["bled"].write_text(json.dumps({
                    "artifact_path": str(copy), "artifact_bytes": copy.stat().st_size,
                    "artifact_sha256": hashlib.sha256(self.EXEC_ARTIFACT).hexdigest(),
                    "script_path": str(env["sd"] / "setup.iss"),
                    "script_sha256": hashlib.sha256(b"iss").hexdigest(),
                    "iscc_exit_code": 0, "compiled": True, "fabricated": False}),
                    encoding="utf-8", newline="\n")

            def _unreadable_ledger(env):
                env["bled"].write_text("{ 这不是 json", encoding="utf-8", newline="\n")

            def _dir_not_empty(env):
                Path(env["install_dir"]).mkdir(parents=True)
                (Path(env["install_dir"]) / "someone-else.txt").write_text("x", encoding="utf-8")

            arms = [
                ("install.confirm_absent", None,
                 lambda e: eng.execute_install(**K(e, "full", confirm=None))),
                ("install.confirm_empty", None,
                 lambda e: eng.execute_install(**K(e, "full", confirm=""))),
                ("install.confirm_near_miss", None,
                 lambda e: eng.execute_install(
                     **K(e, "full", confirm=self.EXEC_CONFIRM + "-x"))),
                ("install.artifact_absent", None,
                 lambda e: eng.execute_install(
                     **K(e, "full", confirm=self.EXEC_CONFIRM,
                         artifact=str(e["sd"] / "no-such-setup.exe")))),
                ("install.artifact_outside_allow_root", None,
                 lambda e: eng.execute_install(
                     **K(e, "full", confirm=self.EXEC_CONFIRM,
                         allow_roots=[str(Path(e["sd"]) / "fake-user")]))),
                ("install.build_ledger_absent", None,
                 lambda e: eng.execute_install(
                     **K(e, "full", confirm=self.EXEC_CONFIRM,
                         build_ledger=str(e["sd"] / "no-evidence.json")))),
                ("install.build_ledger_unreadable", _unreadable_ledger,
                 lambda e: eng.execute_install(**K(e, "full", confirm=self.EXEC_CONFIRM))),
                ("install.artifact_bytes_changed", _plant_len_changed,
                 lambda e: eng.execute_install(**K(e, "full", confirm=self.EXEC_CONFIRM))),
                ("install.ledger_points_to_other_file", _ledger_points_elsewhere,
                 lambda e: eng.execute_install(**K(e, "full", confirm=self.EXEC_CONFIRM))),
                ("install.artifact_equal_length_swap", _plant_outside_lean,
                 lambda e: eng.execute_install(**K(e, "full", confirm=self.EXEC_CONFIRM))),
                ("install.dir_relative", None,
                 lambda e: eng.execute_install(**K(e, "full", confirm=self.EXEC_CONFIRM,
                                                   install_dir="relative/dir"))),
                ("install.dir_outside_allow_root", None,
                 lambda e: eng.execute_install(
                     **K(e, "full", confirm=self.EXEC_CONFIRM,
                         install_dir=str(Path(e["sd"]).parent / "elsewhere")))),
                ("install.dir_inside_source_tree", None,
                 lambda e: eng.execute_install(
                     **K(e, "full", confirm=self.EXEC_CONFIRM,
                         install_dir=str(Path(eng.__file__).resolve().parent)))),
                ("install.dir_not_empty", _dir_not_empty,
                 lambda e: eng.execute_install(**K(e, "full", confirm=self.EXEC_CONFIRM))),
                ("uninstall.confirm_absent", None,
                 lambda e: eng.execute_uninstall(**U(e, "full", confirm=None))),
                ("uninstall.dir_relative", None,
                 lambda e: eng.execute_uninstall(**U(e, "full", confirm=self.EXEC_CONFIRM,
                                                     install_dir="relative/dir"))),
                ("uninstall.dir_outside_allow_root", None,
                 lambda e: eng.execute_uninstall(
                     **U(e, "full", confirm=self.EXEC_CONFIRM,
                         install_dir=str(Path(e["sd"]) / ".." / "elsewhere-u")))),
            ]
            want = {"install.confirm_absent": "confirmation_required",
                    "install.confirm_empty": "confirmation_required",
                    "install.confirm_near_miss": "confirmation_required",
                    "install.artifact_absent": "artifact_missing",
                    "install.artifact_outside_allow_root": "artifact_outside_allowed_root",
                    "install.build_ledger_absent": "build_ledger_unavailable",
                    "install.build_ledger_unreadable": "build_ledger_unavailable",
                    "install.artifact_bytes_changed": "artifact_sha_mismatch_vs_build_ledger",
                    "install.ledger_points_to_other_file": "build_ledger_stale",
                    "install.artifact_equal_length_swap": "artifact_sha_mismatch_vs_build_ledger",
                    "install.dir_relative": "install_dir_not_absolute",
                    "install.dir_outside_allow_root": "install_dir_outside_allowed_root",
                    "install.dir_inside_source_tree": "install_dir_inside_source_tree",
                    "install.dir_not_empty": "install_dir_not_empty",
                    "uninstall.confirm_absent": "confirmation_required",
                    "uninstall.dir_relative": "install_dir_not_absolute",
                    "uninstall.dir_outside_allow_root": "install_dir_outside_allowed_root"}
            rows = []
            for name, mutate, call in arms:
                env = self._exec_env("gates")        # 每条臂同一基线（先清后建）
                if mutate:
                    mutate(env)
                pre = self._exec_listing(env["sd"])
                try:
                    call(env)
                    got = "<no-refusal>"
                except eng.ExecutionError as exc:
                    got = exc.reason
                except Exception as exc:  # noqa: BLE001 —— 非契约异常必须现形，不当成拒绝
                    got = "WRONG_EXC:" + type(exc).__name__
                rows.append([name, got, got == want[name],
                             self._exec_listing(env["sd"]) == pre,   # 拒绝后现场逐条未变
                             Path(env["ledger"]).exists(),            # 绝不写回执
                             Path(env["install_dir"]).exists()])
            src_tree = Path(eng.__file__).resolve().parent
            dir_created_by = [r[0] for r in rows if r[5]]
            ok = (len(rows) == 17 and all(r[2] for r in rows) and all(r[3] for r in rows)
                  and all(r[4] is False for r in rows)
                  # 十七臂里唯一"安装根在场上"的必须是那条非空臂（它自己种的目录），
                  # 其余各臂连目录都不许被创建出来——拒绝的代价必须真是零
                  and dir_created_by == ["install.dir_not_empty"]
                  # 源码树一条臂尤其不许留痕：仓库目录里绝没有 "ZaojingAI.exe"
                  and not (src_tree / "ZaojingAI.exe").exists()
                  and not (src_tree / "someone-else.txt").exists())
            return ok, {"expected": "十七臂各按其 reason 码拒绝（三形制同意缺失/制品缺席/越允许根/"
                                    "台账缺席与坏 JSON/改长与等长替换两形 sha 门/台账指错文件/"
                                    "安装根相对·越根·落源码树·非空/卸载侧同意与路径三门）；"
                                    "拒绝后沙箱逐条全等、台账从不落盘、安装根从不被创建",
                        "actual": {"arms": rows, "refused": sum(1 for r in rows if r[2]),
                                   "ledger_written": any(r[4] for r in rows),
                                   "dir_created": any(r[5] for r in rows)}}

        def ex_install_verified_from_diff(E):
            eng = E["installer_runtime"]
            env = self._exec_env("install")
            res = eng.execute_install(**self._exec_kw(env, "full", confirm=self.EXEC_CONFIRM))
            # 独立复算：安装根下"真有哪些文件"由评测自己 rglob，不信差集的回显。
            # 两侧必须同为"相对沙箱的 posix 串"再比 —— _exec_rel 收的是清单回的是清单，
            # 忘了取 [0] 就会拿"一串单元素清单"去比"一串字符串"，永远不等（仪器笔误，
            # 不是磁盘真相变了）。
            disk = sorted(self._exec_rel(env["sd"], [str(p)])[0]
                          for p in Path(env["install_dir"]).rglob("*") if p.is_file())
            created = [[f["root"], self._exec_rel(env["sd"], [f["path"]])[0]]
                       for f in res["diff"]["created"]]
            distinct = sorted({c[1] for c in created})
            led = json.loads(Path(env["ledger"]).read_text(encoding="utf-8"))
            ok = (res["status"] == "install_verified" and res["installed"] is True
                  and res["basis"] == "derived_from_disk_and_registry"
                  and res["fabricated"] is False and res["exit_code"] == 0
                  and [c["check"] for c in res["checks"]] == self.EXEC_INSTALL_CHECKS
                  and all(c["ok"] for c in res["checks"])
                  and res["checks_count"] == len(res["checks"]) == 10
                  # 差集里 install_root 标签下的清单必须与磁盘实测逐条全等
                  and sorted(c[1] for c in created if c[0] == "install_root") == disk
                  # 本次真实新增的物理文件恰 4 枚（两枚 exe + 一份文档 + 一枚快捷方式；
                  # 数据子目录只建目录不建文件）。created 是按落点标签展开的，同一枚文件
                  # 若被两个标签（install_root 与 vendor_dir 实为同一目录）各数一遍，
                  # created_count 就会虚报——本条钉的是磁盘真相而不是行数。
                  and len(distinct) == 4 and res["diff"]["created_count"] == len(distinct)
                  and res["registry_before"] == {"uninstall": {}, "app": {}}
                  and res["registry_after"]["uninstall"]["DisplayName"] == self.EXEC_DISPLAY_NAME_MEASURED
                  # 显示名那条观察必须带着它自己的规则出场：逐字相等的旧口径会在这台机器上
                  # 把真装读回的 "ZaojingAI 1.0.0" 判成失败（身份由键名里的 AppId 定，显示名
                  # 只是描述性复核），所以判分核对的是 rule 与 observed 两者同在场。
                  and [c for c in res["checks"]
                       if c["check"] == "registry_uninstall_key_present"][0]["expected_rule"] \
                      == "display_name_startswith_app_name"
                  and res["registry_observation"]["uninstall"] == self.EXEC_UNINSTALL_REG_PATH
                  and res["registry_observation"]["app"] == self.EXEC_APP_REG_KEY
                  and res["registry_observation"]["uninstall_key_was_leaf"] is True
                  and res["registry_observation"]["view"] == "injected"
                  and res["argv"][0] == str(Path(env["art"]).resolve())
                  and res["argv"][1:4] == self.EXEC_FLAGS
                  and res["argv"][4] == "/DIR=" + res["install_dir"]
                  and res["argv"][5] == "/LOG=" + env["log"]
                  and res["log"]["bytes"] > 0 and len(res["log"]["sha256"]) == 64
                  and res["preexisting"]["count"] == 1 and res["preexisting"]["intact"] is True
                  and res["preexisting"]["files"][0]["rel"].endswith("user-notes.json")
                  and led["self_report_ignored"] == self.EXEC_IGNORED
                  and led["uninstall"] is None and led["fabricated"] is False
                  and led["schema"] == "zaojing-installer-exec-ledger/v1"
                  and Path(env["ledger"]).is_file()
                  and (Path(env["install_dir"]) / "ZaojingAI.exe").is_file()
                  and (Path(env["install_dir"]) / "unins000.exe").is_file())
            return ok, {"expected": "真跑一趟静默安装后 installed 只由磁盘+注册表差集派生：十条观察"
                                    "逐项在场且 ok、install_root 差集与评测 rglob 实测逐条全等、"
                                    "新增物理文件恰 4 枚（created_count 必等于去重后的磁盘真相）、"
                                    "注册表从空到有、显示名按本机真装实测的那一形复核、叶子键名"
                                    "在回执里被补全成 Uninstall 根下的全路径、argv 六段、日志字节"
                                    "与 sha 落证、既有数据 1 份逐字节完好、台账 self_report_ignored "
                                    "七枚逐字",
                        "actual": {"status": res["status"], "checks_ok": all(
                            c["ok"] for c in res["checks"]),
                            "disk_files": disk, "created_rel": distinct,
                            "created_count": res["diff"]["created_count"],
                            "created_rows": len(created),
                            "registry_display": res["registry_after"]["uninstall"]["DisplayName"],
                            "registry_observation": res["registry_observation"],
                            "argv_flags": res["argv"][1:4], "log_bytes": res["log"]["bytes"],
                            "preexisting": [res["preexisting"]["count"],
                                            res["preexisting"]["intact"]],
                            "ignored_in_ledger": led["self_report_ignored"]}}

        def ex_verdict_ignores_self_report(E):
            eng = E["installer_runtime"]
            a = self._exec_env("real")
            b = self._exec_env("ghost")
            ra = eng.execute_install(**self._exec_kw(a, "full", confirm=self.EXEC_CONFIRM))
            rb = eng.execute_install(**self._exec_kw(b, "self_report",
                                                     confirm=self.EXEC_CONFIRM))
            st = eng.execution_state(**self._exec_state_kw(b))
            ok = (ra["installed"] is True and ra["status"] == "install_verified"
                  and rb["installed"] is False and rb["status"] == "install_unverified"
                  and rb["fabricated"] is False
                  # 两趟的自报面**逐字段相同**：退出码 0、stdout 尾巴、stderr 尾巴一字不差，
                  # 差别只在盘上有没有东西——所以判分翻转不可能来自自报差异。
                  and ra["exit_code"] == rb["exit_code"] == 0
                  and ra["process"] == rb["process"]
                  and rb["log"]["seen_finished_line"] is True
                  and rb["log"]["bytes"] > 0
                  and [c["check"] for c in rb["checks"]] == self.EXEC_INSTALL_CHECKS
                  and [c["ok"] for c in rb["checks"]] == [True, True, True] + [False] * 7
                  and rb["diff"]["created_count"] == 0
                  and rb["registry_after"] == {"uninstall": {}, "app": {}}
                  and not Path(b["install_dir"]).exists()
                  # 现场读回来"啥也没装"与台账自洽 ⇒ ledger_unverified（不是 inconsistent：
                  # 台账没撒谎，只是它记的那一趟没在盘上留下产物），且绝不伪造回执
                  and st["status"] == "ledger_unverified" and st["mismatches"] == []
                  and st["fabricated"] is False
                  and st["recomputed"]["install_exit_code"] == 0
                  and st["recomputed"]["install_dir_now_exists"] is False
                  and st["recomputed"]["install_checks_ok_all"] is False)
            return ok, {"expected": "同一份自报（exit 0 + 完成行 + stdout 原文）下，盘上有产物的"
                                    "一趟判 install_verified，盘上空手的一趟判 install_unverified "
                                    "且 installed=False；空手趟的十条观察恰好 3 真 7 假（只有"
                                    "目录先前为空/退出码/日志字节三条成立）；其台账与现场自洽但"
                                    "未证成 ⇒ ledger_unverified 而非伪造回执",
                        "actual": {"self_report_identical": ra["process"] == rb["process"],
                                   "exit_codes": [ra["exit_code"], rb["exit_code"]],
                                   "finished_line_both": [ra["log"]["seen_finished_line"],
                                                           rb["log"]["seen_finished_line"]],
                                   "verdicts": [ra["status"], rb["status"]],
                                   "ghost_ok_marks": [c["ok"] for c in rb["checks"]],
                                   "ghost_created": rb["diff"]["created_count"],
                                   "ghost_state": [st["status"], st["mismatches"],
                                                   st["recomputed"]["install_dir_now_exists"]]}}

        def ex_uninstall_forms_and_reconcile(E):
            eng = E["installer_runtime"]
            env = self._exec_env("unins")
            poll = {"poll_timeout_s": 0.0, "poll_interval_s": 0.0}

            def state(e):
                return eng.execution_state(**self._exec_state_kw(e))

            inst = eng.execute_install(**self._exec_kw(env, "full",
                                                       confirm=self.EXEC_CONFIRM))
            st_installed = state(env)
            # 「只有安装这一笔」的台账另存一份原样副本，供本案末尾的**反向**臂使用：本案后面
            # 会真卸一次，现场归零后那份副本仍是 install-only，于是"台账说装成了、本机已经不在
            # 安装态、又没有卸载记录"这一形制能在同一座沙箱里被读出来（第 9 枚缺陷的另一侧）。
            _only = Path(env["sd"]) / "install-only.json"
            _only.write_bytes(Path(env["ledger"]).read_bytes())
            _only_bytes = _only.read_bytes()
            lazy = eng.execute_uninstall(**self._exec_un_kw(env, "noop",
                                                            confirm=self.EXEC_CONFIRM, **poll))
            un = eng.execute_uninstall(**self._exec_un_kw(env, "full",
                                                           confirm=self.EXEC_CONFIRM, **poll))
            st = state(env)
            orig = env["art"].read_bytes()
            env["art"].write_bytes(b"\x90" * len(orig))      # 等长替换：只有 sha 会说话
            st_tamper = state(env)
            env["art"].write_bytes(orig)
            st_restored = state(env)
            # 反向臂（第 9 枚缺陷的判据是**倒过来**钉的，不是取消）：install-only 的那份副本
            # 配上已被真卸清零的现场 ⇒ 必须点名。"没有卸载记录"这件事本身不是错，配上"本机
            # 还在安装态"才是正常窗口；配上"痕迹已经不在"就只能是台账之外的手段抹掉的。
            off = eng.execution_state(**self._exec_state_kw(env, ledger_path=str(_only)))
            nenv = self._exec_env("none")
            r_none = eng.execute_uninstall(**self._exec_un_kw(nenv, "full",
                                                              confirm=self.EXEC_CONFIRM, **poll))
            menv = self._exec_env("miss")
            menv["reg_state"]["uninstall"] = {
                "DisplayName": self.EXEC_DISPLAY_NAME_MEASURED,
                "UninstallString": '"%s"' % (menv["sd"] / "gone" / "unins000.exe")}
            r_miss = eng.execute_uninstall(**self._exec_un_kw(menv, "full",
                                                               confirm=self.EXEC_CONFIRM, **poll))
            ok = (inst["installed"] is True
                  # 第 9 枚真缺陷（本机真装抓住，评测侧同形重放）：只装未卸、而本机现场明明还在
                  # 完整安装态，是一次**正常进行到一半**的旅程，旧口径却把它判成台账对不上账
                  # （判词"台账无卸载记录而本机仍在安装态"），使"装好了等着卸"与"伪造的台账"
                  # 在同一个状态码里分不开；而另一侧 verified_run 又硬性要求一条卸载记录，
                  # install-only 永远升不到任何终态。现在它有中间态，且三枚现场布尔必须全为真
                  # 才配得上这个中间态——缺任何一枚就落到下面的反向臂。
                  and st_installed["status"] == "install_verified_awaiting_uninstall"
                  and st_installed["mismatches"] == []
                  and st_installed["fabricated"] is False
                  and st_installed["recomputed"]["install_dir_now_exists"] is True
                  and st_installed["recomputed"]["uninstall_key_now_present"] is True
                  and st_installed["recomputed"]["app_key_now_present"] is True
                  # 反向臂：同一份 install-only 记录 + 痕迹已不在场的现场 ⇒ 必须点名，逐字段
                  # 说清缺的是哪一侧（期望串按引擎模板手抄现场布尔，判分不读引擎常量）
                  and off["status"] == "ledger_inconsistent"
                  and off["mismatches"] == [
                      "uninstall:台账只有 install_verified=True 这一笔、无卸载记录，而本机现场已"
                      "不在完整安装态（install_dir=False uninstall_key=False app_key=False）——"
                      "安装痕迹是被台账之外的手段抹掉的（手工删目录 / 第三方清理器 / 注册表回滚），"
                      "不能读成一次正常卸载"]
                  and off["recomputed"]["install_dir_now_exists"] is False
                  and off["recomputed"]["uninstall_key_now_present"] is False
                  and off["recomputed"]["app_key_now_present"] is False
                  # 只读面连自己判红的那份台账都不许动：副本逐字节回读全等
                  and _only.read_bytes() == _only_bytes
                  and off["ledger"]["bytes"] == len(_only_bytes)
                  # 卸载器退出码 0 而现场未清 ⇒ uninstall_incomplete，残留逐处点名
                  and lazy["status"] == "uninstall_incomplete" and lazy["performed"] is True
                  and lazy["install_dir_gone"] is False and lazy["uninstall_key_gone"] is False
                  and lazy["polls"] == 1
                  and {"registry_uninstall", "registry_app",
                       "install_root_still_present"} <= {r["label"] for r in lazy["residue"]}
                  # 真卸干净：目录 + 两枚键三处全归零才算 uninstall_verified
                  and un["status"] == "uninstall_verified" and un["performed"] is True
                  and un["polls"] == 1 and un["install_dir_gone"] is True
                  and un["uninstall_key_gone"] is True and un["app_key_gone"] is True
                  and un["basis"] == "derived_from_disk_and_registry"
                  and len(un["removed"]) == 4
                  and un["removed_scope"] == "created_by_install_only"
                  and all(r["created_by_install"] is True and r["gone_now"] is True
                          for r in un["removed"])
                  and un["untouched_non_created"] == []
                  # 用户既有数据不在创建集里 ⇒ 一处不许被删，只能作为残留被**点名**
                  and [[r["label"], r["files"], r["created_by_install"]]
                        for r in un["residue"]] == [["runtime_data_root", 1, False]]
                  and un["preexisting"]["count"] == 1 and un["preexisting"]["intact"] is True
                  and un["preexisting"]["recorded_in_ledger"] is True
                  # "键已消失"这一句必须落在**补全后的全路径**上：叶子形态被直接挂到 HKCU 下
                  # 时那里永远什么都没有，uninstall_verified 会空洞为真（本机真装当轮的第 4 枚
                  # 真缺陷）。三形制的每一形都要求回执指名观察对象，且自报接缝为 injected。
                  and un["registry_observation"]["uninstall"] == self.EXEC_UNINSTALL_REG_PATH
                  and un["registry_observation"]["uninstall_key_was_leaf"] is True
                  and un["registry_observation"]["view"] == "injected"
                  and st["registry_observation"]["uninstall"] == self.EXEC_UNINSTALL_REG_PATH
                  and r_none["registry_observation"]["view"] == "injected"
                  and r_miss["registry_observation"]["uninstall"] == self.EXEC_UNINSTALL_REG_PATH
                  and (Path(env["install_dir"])).exists() is False
                  and (Path(nenv["roots"]["localappdata"]) / "Zaojing" / "Assets"
                       / "user-notes.json").read_bytes() == b'{"mine":true}'
                  and st["status"] == "executed_verified" and st["mismatches"] == []
                  and st["fabricated"] is False
                  and st["recomputed"]["install_exit_code"] == 0
                  and st["recomputed"]["install_dir_now_exists"] is False
                  and st["recomputed"]["uninstall_key_now_present"] is False
                  and st["recomputed"]["app_key_now_present"] is False
                  and st["recomputed"]["preexisting_intact_now"] is True
                  and st["recomputed"]["install_checks_ok_all"] is True
                  and st["recomputed"]["artifact_sha256_on_disk_now"]
                  == hashlib.sha256(orig).hexdigest()
                  and st["recomputed"]["log_sha256_on_disk_now"] == inst["log"]["sha256"]
                  and st["recorded_only"] == ["install.exit_code", "install.argv",
                                              "install.process"]
                  and st["self_report_ignored"] == self.EXEC_IGNORED
                  # 等长篡改只被 sha 抓到 ⇒ 恰一条点名（两份台账彼此仍全等，只有盘上的现算值
                  # 与它们不符），且不降级成"读不动"
                  and st_tamper["status"] == "ledger_inconsistent"
                  and st_tamper["mismatches"]
                  == ["install.artifact.sha256 != 制品在盘上的现算值"]
                  and st_restored["status"] == "executed_verified"
                  # 三形制的前两形：没痕迹 ≠ 卸干净；有痕迹没卸载器 ≠ 成功
                  and r_none["status"] == "nothing_to_uninstall" and r_none["performed"] is False
                  and r_none["polls"] == 0 and r_none["removed"] == []
                  and r_none["install_dir_gone"] is True and r_none["uninstall_key_gone"] is True
                  and "unins000.exe" in r_none["reason"]
                  and r_miss["status"] == "missing_uninstaller" and r_miss["performed"] is False
                  and r_miss["polls"] == 0 and len(r_miss["looked_in"]) == 2
                  and r_miss["uninstall_key_gone"] is False
                  and r_miss["preexisting"]["recorded_in_ledger"] is False)
            return ok, {"expected": "一装两卸三形制+台账对账活证：装好未卸且现场仍在安装态 ⇒ 诚实"
                                    "中间态 install_verified_awaiting_uninstall（mismatches 空、三枚"
                                    "现场布尔全真），同一份 install-only 记录配上痕迹已不在场的现场"
                                    "⇒ 点名 ledger_inconsistent；卸载器退出 0 而现场未清 ⇒ "
                                    "uninstall_incomplete 且三条"
                                    "关键残留入列；真卸 ⇒ 目录与两枚键全归零、作用域锁死在本次创建"
                                    "集（removed 恰 4 枚且逐枚 gone_now）、用户既有数据只作残留被"
                                    "点名而不许出现在 removed；executed_verified 的九项重算逐字段"
                                    "核（制品 sha/日志 sha 由评测独立复算）；等长篡改 ⇒ 恰一条 "
                                    "install.artifact.sha256 != 制品在盘上的现算值（两份台账彼此仍"
                                    "全等），还原后回 executed_verified；nothing_to_uninstall 与 "
                                    "missing_uninstaller 各有独立状态码",
                        "actual": {"installed_state": [st_installed["status"],
                                                       st_installed["mismatches"],
                                                       [st_installed["recomputed"][k] for k in (
                                                           "install_dir_now_exists",
                                                           "uninstall_key_now_present",
                                                           "app_key_now_present")]],
                                   "off_book_state": [off["status"], off["mismatches"],
                                                      off["ledger"]["bytes"],
                                                      _only.read_bytes() == _only_bytes],
                                   "lazy": [lazy["status"], lazy["polls"],
                                            [lazy["performed"], lazy["install_dir_gone"],
                                             lazy["uninstall_key_gone"]],
                                            sorted(r["label"] for r in lazy["residue"])],
                                   "verified": [un["status"], un["polls"], un["install_dir_gone"],
                                                un["uninstall_key_gone"], un["app_key_gone"],
                                                len(un["removed"]), un["removed_scope"],
                                                un["basis"],
                                                [[r["created_by_install"], r["gone_now"]]
                                                 for r in un["removed"]],
                                                [un["preexisting"]["count"],
                                                 un["preexisting"]["intact"],
                                                 un["preexisting"]["recorded_in_ledger"]]],
                                   "residue": [[r["label"], r["files"], r["created_by_install"]]
                                               for r in un["residue"]],
                                   "untouched": un["untouched_non_created"],
                                   "install_dir_absent": not Path(env["install_dir"]).exists(),
                                   "user_notes_hex": (Path(nenv["roots"]["localappdata"])
                                                      / "Zaojing" / "Assets"
                                                      / "user-notes.json").read_bytes().hex(),
                                   "state": [st["status"], st["mismatches"], st["fabricated"],
                                             [st["recomputed"][k] for k in (
                                                 "install_exit_code", "install_dir_now_exists",
                                                 "uninstall_key_now_present",
                                                 "app_key_now_present", "preexisting_intact_now",
                                                 "install_checks_ok_all")],
                                             st["recomputed"]["artifact_sha256_on_disk_now"]
                                             == hashlib.sha256(orig).hexdigest(),
                                             st["recomputed"]["log_sha256_on_disk_now"]
                                             == inst["log"]["sha256"],
                                             st["self_report_ignored"]],
                                   "recomputed_sha_prefix": st["recomputed"]
                                   ["artifact_sha256_on_disk_now"][:12],
                                   "recorded_only": st["recorded_only"],
                                   "tamper": [st_tamper["status"], st_tamper["mismatches"]],
                                   "restored": st_restored["status"],
                                   "none": [r_none["status"], r_none["performed"],
                                            r_none["polls"], r_none["removed"],
                                            r_none["install_dir_gone"],
                                            r_none["uninstall_key_gone"],
                                            "unins000.exe" in r_none["reason"]],
                                   "missing": [r_miss["status"], r_miss["performed"],
                                               r_miss["polls"], len(r_miss["looked_in"]),
                                               r_miss["uninstall_key_gone"],
                                               r_miss["preexisting"]["recorded_in_ledger"]]}}

        def ex_second_round_evidence_not_user_data(E):
            """第 10 枚真缺陷的评测侧同形重放：证据与用户数据分账，两轮往返都不许假红。

            前面每一座沙箱都把台账与日志放在**观察根之外**（sd/exec-ledger.json、sd/log/…），
            于是这条缺陷在评测台里从来看不见——而真实 CLI 交出的台账就落在 %LOCALAPPDATA%\\Zaojing\\
            installer\\ 里（那是第 7 枚缺陷的修法带来的必然结果：证据收进产品数据根，共用目录不再
            漏文件）。本例因此把落点**改成产品形状**，两轮 install→uninstall 全跑一遍：
              轮 1 是对照——一切正常时它就该绿，这样轮 2 的判定不能赖给夹具坏；
              轮 2 的基线里必然出现上一轮的台账（内容本轮就会被重写：created_at、差集、卸载回执
              全在变），旧口径把它当"用户既有数据"逐字节比对 ⇒ 本轮刚写的 execution-evidence.json
              出现在 deviating ⇒ verified 合取里的 pre["intact"] 为假 ⇒ 一次干干净净的卸载判成
              uninstall_incomplete。本机实测正是这个形状（deviating 恰一枚、六份真用户数据逐字节
              相等），所以"判红的到底是哪件事"必须用独立复算的哈希钉死，而不是靠引擎自述。
            """
            eng = E["installer_runtime"]
            env = self._exec_env("round2")
            poll = {"poll_timeout_s": 0.0, "poll_interval_s": 0.0}
            levid = Path(env["roots"]["localappdata"]) / "Zaojing" / "installer"
            led = str(levid / "exec-ledger.json")
            keep = Path(env["roots"]["localappdata"]) / "Zaojing" / "Assets" / "user-notes.json"

            def ikw():
                # log_path 刻意不交：缺省落点由引擎决定（写在台账旁边），本例同时钉住第 7 枚
                # 缺陷的修法在评测侧也成立——证据同根，且落点就在被观察的数据根里。
                return self._exec_kw(env, "full", confirm=self.EXEC_CONFIRM,
                                     ledger_path=led, log_path=None)

            def ukw():
                return self._exec_un_kw(env, "full", confirm=self.EXEC_CONFIRM,
                                        ledger_path=led, **poll)

            def skw():
                return self._exec_state_kw(env, ledger_path=led)

            rounds = []
            sha_before_r2 = ""
            for _rnd in (1, 2):
                # 轮 2 动手**之前**先由评测自己哈希一遍：这就是引擎即将写进 executor_owned 的
                # sha256_before，也是"上一轮那份台账"在盘上的真实指纹（时序错了就抓不到改写）。
                if _rnd == 2:
                    sha_before_r2 = hashlib.sha256(Path(led).read_bytes()).hexdigest()
                i = eng.execute_install(**ikw())
                sha_after_this_install = hashlib.sha256(Path(led).read_bytes()).hexdigest()
                u = eng.execute_uninstall(**ukw())
                st = eng.execution_state(**skw())
                rounds.append({"i": i, "u": u, "st": st,
                               "sha_ledger_after_install": sha_after_this_install})
            i1, u1, s1 = rounds[0]["i"], rounds[0]["u"], rounds[0]["st"]
            i2, u2, s2 = rounds[1]["i"], rounds[1]["u"], rounds[1]["st"]
            led_owned = [q for q in i2["preexisting"]["executor_owned"]
                         if str(q["path"]).endswith("exec-ledger.json")]
            log_owned = [q for q in i2["preexisting"]["executor_owned"]
                         if str(q["path"]).endswith("zaojing-install.log")]
            data_root = Path(env["roots"]["localappdata"]) / "Zaojing"
            left = sorted(str(q.relative_to(data_root)).replace("\\", "/")
                          for q in data_root.rglob("*") if q.is_file())
            ok = (
                # —— 轮 1（对照臂）：证据同根、缺省日志落在台账旁边、干净往返该绿就绿 ——
                i1["status"] == "install_verified"
                and Path(i1["log"]["path"]).parent == levid
                and Path(i1["log"]["path"]).name == "zaojing-install.log"
                and i1["preexisting"]["count"] == 1
                and [f["path"] for f in i1["preexisting"]["files"]] == [str(keep)]
                # 第一轮盘上还没有这两枚证据件：也必须列出来（bytes_before=None），否则
                # "没列"会被读成"本轮不写它"——两轮回执形状一致消费方才无从误读。
                and [q["bytes_before"] for q in i1["preexisting"]["executor_owned"]] == [None, None]
                and u1["status"] == "uninstall_verified" and u1["preexisting"]["deviating"] == []
                and len(u1["removed"]) == 5
                # 日志在轮 1 是"本次新建"，卸后仍在场 ⇒ 它是有意保留的证据，不是漏件：
                # removed 逐枚回显 gone_now，不许把"没删"写成"删了"。
                and [r["gone_now"] for r in u1["removed"]
                     if r["path"] == i1["log"]["path"]] == [False]
                and s1["status"] == "executed_verified" and s1["mismatches"] == []
                # —— 轮 2（缺陷臂）：上一轮那份台账真的被本轮改写了（评测独立复算哈希）——
                and i2["status"] == "install_verified"
                and len(led_owned) == 1 and led_owned[0]["sha256_before"] == sha_before_r2
                and rounds[1]["sha_ledger_after_install"] != sha_before_r2
                # 日志内容本轮没变，但它同样从用户基线里逐出、单独另账回显
                and len(log_owned) == 1 and log_owned[0]["sha256_before"] == i1["log"]["sha256"]
                and all(q["this_run_rewrites_it"] is True
                        for q in i2["preexisting"]["executor_owned"])
                and i2["preexisting"]["count"] == 1
                and [f["path"] for f in i2["preexisting"]["files"]] == [str(keep)]
                # 本轮再没有新文件可数：日志早已在场 ⇒ 创建集从 5 枚回到 4 枚
                and i2["diff"]["created_count"] == 4
                # 这就是第 10 枚缺陷的判卷面：修复前这里是 uninstall_incomplete
                and u2["status"] == "uninstall_verified"
                and u2["install_dir_gone"] is True and u2["uninstall_key_gone"] is True
                and u2["app_key_gone"] is True and len(u2["removed"]) == 4
                and all(r["gone_now"] for r in u2["removed"])
                and u2["preexisting"]["count"] == 1 and u2["preexisting"]["intact"] is True
                and u2["preexisting"]["deviating"] == []
                and u2["preexisting"]["recorded_in_ledger"] is True
                # 逐出不等于丢账：数据根里剩下什么，评测自己 rglob 一遍独立核
                and left == ["Assets/user-notes.json", "installer/exec-ledger.json",
                             "installer/zaojing-install.log"]
                and keep.read_bytes() == b'{"mine":true}'
                # 对账面在第二轮台账上同样判得动，且它**只读**：自家证据逐枚回显却一句
                # "本轮会重写"都不许说（那是安装/卸载面的时序，不是它的）
                and s2["status"] == "executed_verified" and s2["mismatches"] == []
                and s2["preexisting"]["count"] == 1
                and s2["preexisting"]["recorded_in_ledger"] is True
                and s2["preexisting"]["uninstall_receipt"] == {
                    "intact_claimed": True, "deviating": [], "deviating_all_executor_owned": False}
                and all(q["this_run_rewrites_it"] is False
                        for q in s2["preexisting"]["executor_owned"])
                and len(s2["preexisting"]["executor_owned"]) == 2)
            return ok, {"expected": "台账与缺省日志落在**被观察的数据根内**（真实 CLI 的产品形状）"
                                    "时，两轮 install→uninstall 都必须诚实：轮 1 对照绿（证据同根、"
                                    "缺省日志就在台账旁边、干净往返判 uninstall_verified）；轮 2 里"
                                    "上一轮的台账被本轮改写（评测独立复算的哈希证明它确实变了），"
                                    "但它是执行器自家证据 ⇒ 从用户既有数据基线逐出、另账回显，"
                                    "卸载仍判 uninstall_verified 且 deviating 为空；创建集从 5 枚回"
                                    "到 4 枚（日志早已在场）；对账面 executed_verified 且对自家证据"
                                    "只回显不声称重写；数据根剩下的三枚文件由评测自己 rglob 核对",
                        "actual": {"r1": [i1["status"], Path(i1["log"]["path"]).relative_to(data_root).as_posix(),
                                          i1["preexisting"]["count"], u1["status"],
                                          len(u1["removed"]),
                                          [r["gone_now"] for r in u1["removed"]],
                                          s1["status"], s1["mismatches"]],
                                   "r1_owned": [[q["path"], q["bytes_before"],
                                                 q["this_run_rewrites_it"]]
                                                for q in i1["preexisting"]["executor_owned"]],
                                   "r2_owned_before": [[q["rel"], q["bytes_before"],
                                                        q["sha256_before"][:16],
                                                        q["this_run_rewrites_it"],
                                                        q["recorded_as_user_data_in_older_ledger"]]
                                                       for q in i2["preexisting"]["executor_owned"]],
                                   "ledger_sha_before_rewrite": sha_before_r2[:16],
                                   "ledger_sha_after_rewrite":
                                       rounds[1]["sha_ledger_after_install"][:16],
                                   "r2_created_count": i2["diff"]["created_count"],
                                   "r2_pre_count": i2["preexisting"]["count"],
                                   "r2_pre_files": [f["path"]
                                                    for f in i2["preexisting"]["files"]],
                                   "r3": [u2["status"], u2["preexisting"]["count"],
                                          u2["preexisting"]["intact"],
                                          u2["preexisting"]["deviating"],
                                          u2["preexisting"]["recorded_in_ledger"],
                                          len(u2["removed"]), s2["status"], s2["mismatches"],
                                          s2["preexisting"]["uninstall_receipt"],
                                          [q["this_run_rewrites_it"]
                                           for q in s2["preexisting"]["executor_owned"]]],
                                   "left_in_data_root": left,
                                   "user_notes_hex": keep.read_bytes().hex()}}

        def k_exec_trust_self_report(E):
            # 真相：退出码 0 + 日志"Installation finished" 而盘上一个文件没多 ⇒ installed=False。
            # 此处故意期望"自报即通过"，诚实判分必须判 fail（P24 以来的校准形制）。
            eng = E["installer_runtime"]
            env = self._exec_env("calib")
            res = eng.execute_install(**self._exec_kw(env, "self_report",
                                                      confirm=self.EXEC_CONFIRM))
            ok = (res["exit_code"] == 0 and res["log"]["seen_finished_line"] is True
                  and res["installed"] is True)
            return ok, {"note": "校准用例：假期望把安装器自报（退出码 0 + 完成行）当成已装成",
                        "actual_exit_code": res["exit_code"],
                        "actual_finished_line": res["log"]["seen_finished_line"],
                        "actual_installed": res["installed"],
                        "actual_status": res["status"]}

        # ---------- COMPARE（Phase 38 第 23 引擎）：design_compare 纯函数六类 diff 真值 ----------
        def cp_zero_diff(E):
            r = E["compare"].compare_objects([{"id": "a", "x": 0}], [{"id": "a", "x": 0}])
            zero = {"geometry": 0, "text": 0, "color": 0, "asset": 0, "layer": 0, "visual": 0}
            ok = (r["identical"] is True
                  and r["summary"] == {"added": 0, "removed": 0, "changed": 0, "moved": 0,
                                       "by_category": zero}
                  and sorted(r["categories"]) == ["asset", "color", "geometry", "layer",
                                                  "text", "visual"])
            return ok, {"expected": "identical=True + 六类计数全 0 + 键集合恰六类",
                        "actual": {"identical": r["identical"], "summary": r["summary"],
                                   "keys": sorted(r["categories"])}}

        def cp_field_categories(E):
            before = [{"id": "o1", "type": "text", "x": 0, "y": 0, "width": 10, "height": 10,
                       "color": "#111111", "font_size": 24, "src": "a.png", "opacity": 100, "z": 1}]
            after = [{"id": "o1", "type": "text", "x": 5, "y": 5, "width": 10, "height": 10,
                      "color": "#222222", "font_size": 30, "src": "b.png", "opacity": 60, "z": 2}]
            r = E["compare"].compare_objects(before, after)
            sm = r["summary"]
            ok = ([sm["added"], sm["removed"], sm["changed"], sm["moved"]] == [0, 0, 1, 1]
                  and sm["by_category"] == {"geometry": 2, "text": 1, "color": 1,
                                            "asset": 1, "layer": 1, "visual": 1}
                  and r["moved"][0] == {"key": "o1", "position": {
                      "x": {"before": 0, "after": 5}, "y": {"before": 0, "after": 5}}}
                  and [(i["key"], i["field"]) for i in r["categories"]["geometry"]]
                  == [("o1", "x"), ("o1", "y")]
                  # type 两侧相同不入 diff（真相，非"应然愿望"）
                  and not any(i["field"] == "type"
                              for c in r["categories"].values() for i in c))
            return ok, {"expected": "六类逐数 {g:2,t:1,c:1,a:1,l:1,v:1}；moved 逐字段；"
                                    "geometry 项序 (x,y)；相同 type 不入 diff",
                        "actual": {"summary": sm, "moved0": r["moved"][0]}}

        def cp_structure_typemap(E):
            r = E["compare"].compare_objects(
                [{"id": "a", "type": "shape", "z": 1}, {"id": "gone", "type": "mystery"}],
                [{"id": "a", "type": "shape", "z": 2},
                 {"id": "newimg", "type": "image"}, {"id": "newx", "type": "whatever"}])
            sm = r["summary"]
            ok = ([sm["added"], sm["removed"], sm["changed"], sm["moved"]] == [2, 1, 1, 0]
                  and sm["by_category"] == {"geometry": 0, "text": 0, "color": 0,
                                            "asset": 1, "layer": 3, "visual": 0}
                  and [(i["key"], i["field"], i.get("op")) for i in r["categories"]["layer"]]
                  == [("a", "z", None), ("newx", "*", "added"), ("gone", "*", "removed")]
                  and r["categories"]["asset"][0] == {
                      "key": "newimg", "field": "*", "op": "added", "before": None,
                      "after": {"id": "newimg", "type": "image"}})
            return ok, {"expected": "image→asset；未知 type 兜底 layer；z 改动同入 layer"
                                    "（layer 项序 a/newx/gone 逐字段）",
                        "actual": {"summary": sm,
                                   "layer": [(i["key"], i.get("op")) for i in
                                             r["categories"]["layer"]]}}

        def cp_keys_universe(E):
            r = E["compare"].compare_objects(
                [{"id": "k", "x": 0, "font_size": 12, "opacity": 0.5}],
                [{"id": "k", "x": 9, "font_size": 30, "opacity": 0.1}],
                compare_keys=["x", "font_size"])
            # 索引真相：无 id 用 name，再无则序号；未列字段（opacity）在显式字段集下不参与 diff
            r2 = E["compare"].compare_objects([{"name": "甲", "w": 1}, {"id": 7, "w": 2}],
                                              [{"name": "甲", "w": 9}, {"id": 7, "w": 2}])
            ok = ([r["summary"]["changed"], r["summary"]["moved"],
                   r["summary"]["by_category"]["visual"]] == [1, 1, 0]
                  and r["changed"][0]["properties"] == {
                      "font_size": {"before": 12, "after": 30}}
                  and r2["changed"][0]["key"] == "甲" and r2["summary"]["changed"] == 1
                  and r2["summary"]["by_category"] == {"geometry": 0, "text": 0, "color": 0,
                                                       "asset": 0, "layer": 0, "visual": 1})
            return ok, {"expected": "compare_keys 限字段集（opacity 不diff）；name 索引；"
                                    "未知字段 w 兜底 visual",
                        "actual": {"summary": r["summary"], "changed_props":
                                   r["changed"][0]["properties"],
                                   "r2_key": r2["changed"][0]["key"],
                                   "r2_by_category": r2["summary"]["by_category"]}}

        def k_compare_wrong_expect(E):
            # 真相：fill_color 改动归 color 类（layer=0）；此处故意期望落 layer，诚实判分必红
            r = E["compare"].compare_objects([{"id": "z", "fill_color": "#FF0000"}],
                                             [{"id": "z", "fill_color": "#00FF00"}])
            ok = r["summary"]["by_category"]["layer"] == 1
            return ok, {"note": "校准用例：假期望把 fill_color 改动归 layer 类",
                        "actual_by_category": r["summary"]["by_category"]}

        # ---------- BG_REMOVAL（Phase 38 第 24 引擎）：真像素泛洪逐数真值 ----------
        def bg_flood_exact(E):
            from PIL import Image
            src = self._bg_source()
            out = self._bg_sandbox("flood") / "no_bg.png"
            r = E["bg_removal"].remove_background(src, out, 32)
            oi = Image.open(r["output"])
            alphas = [oi.getpixel((0, 0))[3], oi.getpixel((31, 31))[3],
                      oi.getpixel((43, 43))[3]]
            ok = (r["background_sample"] == [200, 30, 30] and r["threshold_used"] == 32
                  and r["pixels_cleared"] == 8400
                  and round(r["background_cleared_ratio"], 6) == 0.84
                  and [r["width"], r["height"], r["mode"]] == [100, 100, "RGBA"]
                  and alphas == [0, 255, 255]
                  and Path(r["output"]).read_bytes()[:8] == b"\x89PNG\r\n\x1a\n")
            return ok, {"expected": "bg 采样 [200,30,30]；清 8400px（84%）；角 0/方块 255/"
                                    "被包围孔洞 255（泛洪≠色键）；输出真 PNG",
                        "actual": {"sample": r["background_sample"],
                                   "cleared": r["pixels_cleared"],
                                   "ratio": round(r["background_cleared_ratio"], 6),
                                   "alphas": alphas}}

        def bg_threshold_bound(E):
            src = self._bg_source()
            r0 = E["bg_removal"].remove_background(src, self._bg_sandbox("t0") / "o.png", 0)
            r1 = E["bg_removal"].remove_background(src, self._bg_sandbox("t1") / "o.png", 1)
            rh = E["bg_removal"].remove_background(src, self._bg_sandbox("tmax") / "o.png", 999)
            ok = (r0["pixels_cleared"] == 8400 and r1["pixels_cleared"] == 8400
                  and rh["threshold_used"] == 255 and rh["pixels_cleared"] == 10000)
            return ok, {"expected": "thr0/1 仅等色泛洪 8400；999 如实钳 255 → 全幅 10000 达",
                        "actual": {"t0": r0["pixels_cleared"], "t1": r1["pixels_cleared"],
                                   "clamp": rh["threshold_used"], "tmax": rh["pixels_cleared"]}}

        def bg_ghost_no_write(E):
            sd = self._bg_sandbox("ghost")
            never = sd / "never.png"
            msg = None
            raised = False
            try:
                E["bg_removal"].remove_background(sd / "ghost.png", never, 32)
            except FileNotFoundError as e:
                raised = True
                msg = str(e)
            ok = (raised and msg.startswith("输入图像不存在") and never.exists() is False)
            return ok, {"expected": "FileNotFoundError『输入图像不存在』前缀 + 绝不落盘假产物",
                        "actual": {"raised": raised, "msg_head": (msg or "")[:7],
                                   "never_written": not never.exists()}}

        def bg_default_output_and_determinism(E):
            sd = self._bg_sandbox("default")
            pic = sd / "pic.png"
            shutil.copy2(self._bg_source(), pic)
            r1 = E["bg_removal"].remove_background(pic, None, 32)
            r2 = E["bg_removal"].remove_background(pic, sd / "again.png", 32)
            ok = (r1["output"] == str(sd / "pic_no_bg.png")
                  and Path(r1["output"]).exists()
                  and [r1["pixels_cleared"], r1["background_sample"]]
                  == [r2["pixels_cleared"], r2["background_sample"]]
                  and r1["algorithm"] == "edge-flood-fill (PIL, real pixel processing)")
            return ok, {"expected": "output=None 落同级 <stem>_no_bg.png；两次跑逐数幂等"
                                    "（确定性反查）；algorithm 字面",
                        "actual": {"out": r1["output"],
                                   "same": [r1["pixels_cleared"], r2["pixels_cleared"]],
                                   "algorithm": r1["algorithm"]}}

        # ---------- PANEL_RECOGNITION（Phase 47 第 25 引擎）：栅格刀版→面板几何反解 ----------
        def pr_grid_inversion(E):
            sd = self._panel_sandbox("grid")
            png = self._panel_fixture(sd, "grid3x2.png")
            r = E["panel_recognition"].recognize_panels(png)
            ps = r["panels"]
            ok = (r["status"] == "recognized" and r["implemented"] is True
                  and r["fabricated"] is False
                  and [r["image"]["width_px"], r["image"]["height_px"],
                       r["image"]["source_mode"]] == [220, 100, "RGB"]
                  and r["binarize"]["method"] == "otsu"
                  and r["binarize"]["threshold"] == 0
                  and [r["binarize"][k] for k in ("ink_px", "line_px", "other_ink_px",
                                                  "barrier_px", "blocked_px")]
                  == [826, 817, 9, 900, 909]
                  and r["panel_count"] == 6
                  and [p["bbox_px"] for p in ps]
                  == [[11, 11, 75, 49], [77, 11, 141, 49], [143, 11, 207, 49],
                      [11, 51, 75, 89], [77, 51, 141, 89], [143, 51, 207, 89]]
                  and [p["area_px"] for p in ps] == [2535] * 6
                  and all(p["rectangularity"] == 1.0 and p["rectangular"] is True
                          and p["interior_obstacle_px"] == 0 for p in ps)
                  and r["rejected_count"] == 0 and r["rejected"] == []
                  and r["background"] == {"component_count": 1, "largest_area_px": 5881}
                  and r["lines"]["cut"] == {"segments": 4, "length_px": 552,
                                            "covered_px": 552, "dashes": 4}
                  and r["lines"]["fold"] == {"segments": 3, "length_px": 347,
                                             "covered_px": 267, "dashes": 30}
                  and r["topology"]["adjacency_count"] == 7
                  and r["topology"]["degree_sequence"] == [3, 3, 2, 2, 2, 2]
                  and r["topology"]["degrees"] == {"panel-1": 2, "panel-2": 3, "panel-3": 2,
                                                   "panel-4": 2, "panel-5": 3, "panel-6": 2}
                  and r["topology"]["connected_groups"] == 1
                  and r["topology"]["is_single_connected_net"] is True
                  and all(a["separating_line"] == "fold" and a["line_gap_px"] == 1
                          and a["line_basis"] == "geometric-heuristic"
                          for a in r["topology"]["adjacencies"])
                  and [r["outline"][k] for k in ("perimeter_px", "union_area_px",
                                                 "row_runs", "col_runs")]
                  == [1248, 15210, 234, 390]
                  and r["outline"]["bbox"] == [11, 11, 207, 89]
                  and len(r["caveats"]) == 5)
            return ok, {"expected": "6 面板各 65x39=2535px（并集 15210=6x2535）；外框 4 段 "
                                    "cut·552px 全占；内部 3 段 fold·覆盖 267/347；邻接 7、"
                                    "度序列 [3,3,2,2,2,2]、单一连通网；轮廓 2x234+2x390=1248；"
                                    "背景余量 22000-909-15210=5881",
                        "actual": {"status": r["status"],
                                   "areas": [p["area_px"] for p in ps],
                                   "binarize": [r["binarize"][k] for k in
                                                ("ink_px", "line_px", "other_ink_px",
                                                 "barrier_px", "blocked_px")],
                                   "lines": [r["lines"]["cut"]["segments"],
                                             r["lines"]["cut"]["length_px"],
                                             r["lines"]["fold"]["segments"],
                                             r["lines"]["fold"]["covered_px"]],
                                   "adj": [r["topology"]["adjacency_count"],
                                           r["topology"]["degree_sequence"],
                                           r["topology"]["connected_groups"]],
                                   "outline": [r["outline"]["perimeter_px"],
                                               r["outline"]["union_area_px"],
                                               r["outline"]["row_runs"],
                                               r["outline"]["col_runs"]],
                                   "bg": r["background"]}}

        def pr_param_gates(E):
            sd = self._panel_sandbox("params")
            thin = self._panel_fixture(sd, "grid3x2.png")
            thick = self._panel_fixture(sd, "grid3x2_thick5.png")
            a = E["panel_recognition"].recognize_panels(thick, max_line_width=12)
            b = E["panel_recognition"].recognize_panels(thick, max_line_width=3)
            u = E["panel_recognition"].recognize_panels(thin, px_per_mm=2.0)
            ok = (a["status"] == "recognized" and a["panel_count"] == 6
                  and [p["area_px"] for p in a["panels"]]
                  == [2013, 2013, 1947, 1947, 1947, 1947]
                  and a["binarize"]["line_px"] == 4005
                  and a["lines"]["cut"]["segments"] == 0
                  and a["lines"]["fold"] == {"segments": 7, "length_px": 851,
                                             "covered_px": 801, "dashes": 17}
                  and b["status"] == "no_lines_detected" and b["panel_count"] == 0
                  and [b["binarize"][k] for k in ("ink_px", "line_px", "other_ink_px",
                                                  "barrier_px", "blocked_px")]
                  == [4305, 0, 4305, 0, 4305]
                  and b["panels"] == [] and b["rejected"] == []
                  and b["topology"]["adjacencies"] == []
                  and b["outline"]["perimeter_px"] == 0 and isinstance(b["reason"], str)
                  and set(b) == set(a)
                  and u["units"] == {"px_per_mm": 2.0}
                  and [(p["width_mm"], p["height_mm"]) for p in u["panels"]]
                  == [(32.5, 19.5)] * 6
                  and [p["area_px"] for p in u["panels"]] == [2535] * 6)
            return ok, {"expected": "同一图 max_line_width 3↔12 两态（5px 粗线在 3 门下如实"
                                    "无线可报，降级分支字段集与正常分支全等）；粗框 6 面板"
                                    "面积 2013/1947（斜接补角致 coverage 0.94<0.95 判 fold 而"
                                    "非 cut，几何事实如实记录）；px_per_mm=2 → 65/39px 即 "
                                    "32.5/19.5mm 而面积仍按像素",
                        "actual": {"thin12": [a["panel_count"], a["binarize"]["line_px"],
                                              [p["area_px"] for p in a["panels"]]],
                                   "thick_fold": a["lines"]["fold"],
                                   "lw3": [b["status"], b["panel_count"],
                                           [b["binarize"][k] for k in
                                            ("ink_px", "line_px", "other_ink_px",
                                             "barrier_px", "blocked_px")]],
                                   "same_keys": set(b) == set(a),
                                   "mm": [(p["width_mm"], p["height_mm"])
                                          for p in u["panels"]][:2]}}

        def pr_honest_empties(E):
            sd = self._panel_sandbox("honest")
            blank = self._panel_fixture(sd, "blank.png")
            cross = self._panel_fixture(sd, "open_cross.png")
            grid = self._panel_fixture(sd, "grid3x2.png")
            r0 = E["panel_recognition"].recognize_panels(blank)
            r1 = E["panel_recognition"].recognize_panels(cross)
            r2 = E["panel_recognition"].recognize_panels(grid, min_panel_area=4000)
            r3 = E["panel_recognition"].recognize_panels(grid)  # 反零向量
            ok = (r0["status"] == "no_lines_detected" and r0["binarize"]["ink_px"] == 0
                  and r0["panel_count"] == 0 and r0["fabricated"] is False
                  and r0["panels"] == [] and r0["binarize"]["barrier_px"] == 0
                  and isinstance(r0["reason"], str)
                  and r1["status"] == "no_panels_detected" and r1["panel_count"] == 0
                  and r1["lines"]["fold"]["segments"] == 2
                  and r1["background"] == {"component_count": 1, "largest_area_px": 23719}
                  and isinstance(r1["reason"], str)
                  and r2["status"] == "no_panels_detected" and r2["panel_count"] == 0
                  and r2["rejected_count"] == 6
                  and all(x["area_px"] == 2535 and "min_panel_area=4000" in x["reason"]
                          for x in r2["rejected"])
                  and r2["lines"]["fold"]["segments"] == 3
                  and r3["panel_count"] == 6
                  and set(r0) == set(r1) == set(r2) == set(r3))
            return ok, {"expected": "无墨→no_lines_detected、有段无域→no_panels_detected、"
                                    "全域被 min_panel_area 拒→rejected 6 条含门限原文；三者"
                                    "panel_count 皆 0 且 fabricated=False，而同一沙箱同一方法"
                                    "在真刀版上出 6 面板（反零向量：0 不是流水线坏了）",
                        "actual": {"statuses": [r0["status"], r1["status"], r2["status"],
                                                r3["status"]],
                                   "counts": [r0["panel_count"], r1["panel_count"],
                                              r2["panel_count"], r3["panel_count"]],
                                   "rej6": [x["area_px"] for x in r2["rejected"]],
                                   "bg_cross": r1["background"],
                                   "same_face": set(r0) == set(r3)}}

        def pr_determinism(E):
            sd = self._panel_sandbox("idem")
            png = self._panel_fixture(sd, "grid3x2.png")
            r1 = E["panel_recognition"].recognize_panels(png)
            r2 = E["panel_recognition"].recognize_panels(png, min_panel_area=64,
                                                         max_line_width=12, px_per_mm=None)
            r3 = E["panel_recognition"].recognize_panels(png)
            stripped = json.dumps({k: v for k, v in r1.items() if k != "image"},
                                  ensure_ascii=False, sort_keys=True)
            ok = (r1 == r2 == r3
                  and r1["image"]["path"] == str(png)
                  and r1["params"] == {"min_panel_area": 64, "max_line_width": 12,
                                       "min_line_length": 8, "dash_merge_gap": 12,
                                       "solid_coverage_threshold": 0.95,
                                       "rectangularity_threshold": 0.98}
                  and r1["method"].startswith("otsu-binarize")
                  and hashlib.sha256(stripped.encode("utf-8")).hexdigest()
                  == hashlib.sha256(json.dumps(
                      {k: v for k, v in r3.items() if k != "image"},
                      ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest())
            return ok, {"expected": "默认参与显式等价参三次调用逐字段全等（含 image.path）；"
                                    "params 回显默认门限 64/12/8/12/0.95/0.98；剥去 path 后 "
                                    "JSON 序列化 sha256 相同（无时钟无随机）",
                        "actual": {"equal": r1 == r2 == r3,
                                   "params": r1["params"],
                                   "image": r1["image"],
                                   "method_head": r1["method"][:24]}}

        def k_panel_wrong_expect(E):
            # 真相：内部三条线 coverage 0.7672/0.7722 远低于 solid 门限 0.95，判 fold；
            # 此处故意期望它们判 cut，诚实判分必红（若判绿即判分器失真）
            sd = self._panel_sandbox("calibwrong")
            png = self._panel_fixture(sd, "grid3x2.png")
            r = E["panel_recognition"].recognize_panels(png)
            inner = [x for x in r["segments"]["horizontal"] + r["segments"]["vertical"]
                     if x["band"] in (50, 76, 142)]
            ok = len(inner) == 3 and all(x["kind"] == "cut" for x in inner)
            return ok, {"note": "校准用例：假期望把 coverage 0.77 的虚线折线判成 cut",
                        "actual": [(x["band"], x["kind"], x["coverage"]) for x in inner]}

        def k_bgremoval_wrong_expect(E):
            # 真相：被包围孔洞的背景色像素泛洪不可达、alpha 必 255；
            # 此处故意期望其被清除（全局色键冒充泛洪的假期望），诚实判分必红
            from PIL import Image
            out = self._bg_sandbox("calibwrong") / "o.png"
            E["bg_removal"].remove_background(self._bg_source(), out, 32)
            alpha = Image.open(out).getpixel((43, 43))[3]
            ok = alpha == 0
            return ok, {"note": "校准用例：假期望泛洪清除被包围孔洞背景色（色键冒充泛洪）",
                        "actual_hole_alpha": alpha}

        # ---------- INSTALLER_MANIFEST（Phase 49 第 26 引擎）：清单静态校验 + 只读安装态 --
        def mf_clean_zero_findings(E):
            m = E["installer_manifest"]
            sd = self._manifest_sandbox("clean")
            self._manifest_payloads(sd)
            listing = sorted(p.relative_to(sd).as_posix() for p in sd.rglob("*"))
            rep = m.verify_script(self.MF_CLEAN_SCRIPT, str(sd), "")
            secs = m.parse_script(self.MF_CLEAN_SCRIPT)
            after = sorted(p.relative_to(sd).as_posix() for p in sd.rglob("*"))
            ok = (rep["schema"] == "zaojing-installer-manifest/v1"
                  and rep["verdict"] == "manifest_ok"
                  and rep["counts"] == {"errors": 0, "warnings": 0, "infos": 0, "total": 0}
                  and rep["findings"] == []
                  and rep["sections_parsed"] == ["Files", "Icons", "Registry", "Setup"]
                  and rep["entries_by_section"] == {"Files": 2, "Icons": 2,
                                                    "Registry": 3, "Setup": 7}
                  and rep["app_id"] == self.MF_APPID_SCRIPT_FORM
                  and rep["uninstall_registry_key"] == self.MF_APPID + "_is1"
                  and rep["default_dir_name"] == "{autopf}\\ZaojingAI"
                  and rep["app_name"] == "ZaojingAI"
                  and rep["payload_root"] == str(sd) and rep["runtime_data_root"] == ""
                  and rep["fabricated"] is False
                  and rep["execution"]["performed"] is False
                  # P58 起这枚字段的意思变了：不再是"这台机器装不了"，而是"我这台只读仪器没装"。
                  # 退役 ≠ 改写——旧状态原文必须由 retired_boundary.was 逐字带出，否则翻转就成了抹历史。
                  and rep["execution"]["status"] == "not_executed_by_this_engine"
                  and rep["execution"]["retired_boundary"]["was"] == "needs_installer_environment"
                  and rep["execution"]["retired_boundary"]["retired_in"] == "Phase 58"
                  # 解析器真值：注释与空行不计数、行尾续行并入上一条且行号取起始物理行
                  and [ln for ln, _ in secs["Setup"]] == [2, 3, 4, 5, 6, 7, 8]
                  and [ln for ln, _ in secs["Files"]] == [12, 13]
                  and [ln for ln, _ in secs["Registry"]] == [19, 20, 21]
                  and m.split_params(secs["Files"][1][1])["flags"] == "ignoreversion "
                                                            "onlyifdoesntexist"
                  and m.split_params('Name: "文档; 说明"; DestDir: "{app}"')[
                      "name"] == "文档; 说明"
                  # 只读声明可证伪：校验前后沙箱内容逐条全等（零落盘）
                  and listing == ["docs", "docs/上手指南.pdf", "zaojing.exe"]
                  and after == listing)
            return ok, {"expected": "合规自造清单零检出（形制照抄本机真 iscc exit=0 且 0 告警的"
                                    "生产 setup.iss：AppId 双写括号 + PrivilegesRequired=lowest"
                                    " + x64compatible + AppVersion，即 R6 转义/R11 配对/R12 架构"
                                    "/R13 版本四臂的合规侧实证）+ 分段计数 {F2,I2,R3,S7} + "
                                    "续行/注释解析真值 + 校验零落盘",
                        "actual": {"verdict": rep["verdict"], "counts": rep["counts"],
                                   "entries": rep["entries_by_section"],
                                   "files_lines": [ln for ln, _ in secs["Files"]],
                                   "flags": m.split_params(secs["Files"][1][1])["flags"],
                                   "listing_stable": after == listing}}

        def mf_broken_findings_exact(E):
            m = E["installer_manifest"]
            sd = self._manifest_sandbox("broken")
            data_root = str(self.store_dir / "_manifest" / "broken_data" / "ZaojingAI")
            rep = m.verify_script(self.MF_BROKEN_SCRIPT, str(sd), data_root)
            ids = [f["id"] for f in rep["findings"]]
            by = {(f["id"], f["subject"]): f for f in rep["findings"]}
            recount = {"error": 0, "warn": 0, "info": 0}
            for f in rep["findings"]:
                recount[f["severity"]] += 1
            appid = by[("appid_not_uuid",
                        "{9A8B7C6D-5E4F-3G2H-1I0J-K9L8M7N6O5P4}")]["evidence"]
            lit = by[("files_source_literal_missing", "absent.exe")]["evidence"]
            wc = by[("files_source_wildcard_no_match", "libs\\*.dll")]["evidence"]
            uni = by[("uninstall_target_mismatch", "{group}\\卸载")]["evidence"]
            dirs = by[("dirs_miss_runtime_data_root", "[Dirs]")]["evidence"]
            perm = by[("permissions_entry_malformed", "Dirs: usersfull")]["evidence"]
            reg = by[("registry_valuedata_literal_placeholder",
                      "Software\\ZaojingAI")]["evidence"]
            r14 = by[("registry_key_left_after_uninstall",
                      "HKCU\\Software\\ZaojingAI")]["evidence"]
            info = by[("needs_compiler_adjudication", "[Setup] licensefile=")]["evidence"]
            priv = by[("per_user_area_privileges_mismatch",
                       "[Setup] PrivilegesRequired")]["evidence"]
            unres = [f for f in rep["findings"] if f["id"] == "unresolved_template_constant"]
            rep_noroot = m.verify_script(self.MF_BROKEN_SCRIPT, str(sd), "")
            norm = lambda s: str(s).replace("\\", "/")
            ok = (rep["verdict"] == "manifest_invalid"
                  and rep["counts"] == {"errors": 8, "warnings": 3, "infos": 1, "total": 12}
                  and ids == self.MF_BROKEN_IDS
                  and [f["severity"] for f in rep["findings"]] == self.MF_BROKEN_SEVS
                  and ids == sorted(ids)
                  and recount == {"error": 8, "warn": 3, "info": 1}
                  and rep["entries_by_section"] == {"Dirs": 1, "Files": 2, "Icons": 2,
                                                    "Registry": 1, "Setup": 5}
                  # R6：非十六进制字母逐项、长度 36、单写括号形制如实回显 escaped=False
                  # （单写本身只在 GUID 形制成立时才触发转义臂；此处非十六进制故不出该臂）
                  and appid == {"line": 4, "non_hex_letters": list("GHIJKLMNOP"),
                                "length": 36, "escaped": False}
                  # R2：字面 Source 按脚本目录逐段解析后真实不存在
                  and lit["line"] == 8 and lit["exists"] is False
                  and norm(lit["resolved_path"]) == norm(Path(sd) / "absent.exe")
                  and lit["payload_root"] == str(sd)
                  # R3：通配零命中（沙箱先清后建，libs 目录确实不存在）
                  and wc["line"] == 9 and wc["matches"] == 0
                  and wc["parent_dir_exists"] is False and wc["flags"] == "ignoreversion"
                  and norm(wc["resolved_glob"]).endswith(norm(Path("libs") / "*.dll"))
                  # R4：卸载器真身在安装根下 unins000.exe，硬写 Uninstall.exe 必指空
                  and uni["line"] == 12 and uni["section"] == "Icons"
                  and uni["declared_filename"] == "{autopf}\\ZaojingAI\\Uninstall.exe"
                  and norm(uni["expected_from_defaultdirname"])
                  == "{autopf}/ZaojingAI/unins000.exe"
                  # R7：{tmp} 是 Inno 合法常量却无环境映射，永不解析 → 与数据根零交集
                  and dirs["declared"] == [{"line": 14, "name": "{tmp}\\ZaojingAI\\Assets"}]
                  and dirs["declared_resolved"] == ["{tmp}\\ZaojingAI\\Assets"]
                  and dirs["covering_entries"] == []
                  and dirs["runtime_data_root"] == data_root
                  # R10：Permissions 缺 <组>-<级别> 连字符形态
                  and perm["line"] == 14 and perm["section"] == "Dirs"
                  and perm["entry"] == "usersfull" and perm["full_value"] == "usersfull"
                  and perm["expected_shape"] == "<group>-<level>，例如 users-modify"
                  # R1+R8：同一条 {install_date} 双落两规则，逐字段可核
                  and reg["line"] == 16 and reg["value_name"] == "InstallDate"
                  and reg["value_data"] == "{install_date}"
                  and reg["unknown_tokens"] == ["{install_date}"]
                  # R14（Phase 58 新增）：同一条写入还兼任"卸载撤销缺席"的 subject——
                  # 破损夹具的 [Registry] 只有一条 ValueType: string 且不带任何 Flags，
                  # 归一后的视图 hkcu + 键 software\zaojingai 上没有任何撤销声明，故必开火。
                  # 逐字段钉死，防"只数条数不核证据"的假绿。
                  and r14 == {"root": "hkcu", "subkey": "Software\\ZaojingAI", "line": 16,
                              "write_lines": [16], "value_names": ["InstallDate"],
                              "observed_cleanup_flags": [],
                              "accepted_flags": ["uninsdeletekey", "uninsdeletevalue"],
                              "live_measurement": r14["live_measurement"]}
                  and r14["live_measurement"].startswith("Phase 58 本机真卸载")
                  and [(f["subject"], f["evidence"]["occurrences"], f["evidence"]["count"],
                        f["evidence"]["sections"]) for f in unres] == [
                       ("{app_name}", [{"line": 11, "section": "Icons"}], 1, ["Icons"]),
                       ("{install_date}", [{"line": 16, "section": "Registry"}], 1,
                        ["Registry"])]
                  # R9：编译器专属项只出 info，且把"PATH 单路命中"与"三路寻径定位"分开回显
                  # （Phase 53 前二者被当成同一件事：本机 iscc 不在 PATH 却一直装在固定目录，
                  #  于是连续三十余轮把"本机没有编译器"写进报告。此处把区别本身钉死）
                  and info["line"] == 6 and info["value"] == ""
                  and info["iscc_on_path"] == bool(shutil.which("iscc"))
                  and info["compiler_located"] is m.iscc_available()
                  and info["compiler_mode"] in ("fixed_dir", "registry", "path", "none")
                  and (info["compiler_located"] or not info["iscc_on_path"])
                  # R11：HKCU 系 per-user 区域而 PrivilegesRequired 缺省即 admin → warn，
                  # 且 severity 与编译器一致（iscc 只告警不拒绝编译），evidence 回指原话
                  and priv["privileges_required_declared"] is None
                  and priv["privileges_required_effective"] == "admin"
                  and priv["default_when_absent"] == "admin"
                  and priv["per_user_areas_observed"] == ["HKCU"]
                  and priv["per_user_area_sites"] == [
                      {"area": "HKCU",
                       "sites": [{"line": 16, "section": "Registry"}]}]
                  and priv["compiler_proven"] is True
                  and "PrivilegesRequired" in priv["iscc_observed"]
                  and priv["line"] == 0
                  # 合规写法不误报：{app} 载荷目录、有 DefaultDirName 时不降级为 undecidable
                  and "payload_dir_split" not in ids
                  and "uninstall_target_undecidable" not in ids
                  and "appid_guid_unescaped" not in ids      # 非 GUID 故不入转义臂
                  and "setup_missing_version_directive" not in ids   # AppVersion 在场
                  and "arch_identifier_deprecated" not in ids        # 未声明架构指令
                  # 数据根差分：data_root 传空则 R7 整体不出结论，其余 11 项逐 id 不变
                  and [i for i in ids if i != "dirs_miss_runtime_data_root"] == \
                  [f["id"] for f in rep_noroot["findings"]]
                  and rep_noroot["counts"] == {"errors": 8, "warnings": 2, "infos": 1,
                                               "total": 11})
            return ok, {"expected": "12 项检出逐 id 逐项序（8 error·3 warn·1 info）+ "
                                    "每条 evidence 行号/字段精确 + 合规写法零误报 + "
                                    "PATH 命中≠三路定位 + data_root 差分",
                        "actual": {"verdict": rep["verdict"], "counts": rep["counts"],
                                   "ids": ids, "severities": [f["severity"] for f in
                                                               rep["findings"]],
                                   "noroot_counts": rep_noroot["counts"]}}

        def mf_seam_stub_falsification(E):
            m = E["installer_manifest"]
            seams = ("_path_exists", "_dir_exists", "_glob_matches", "_env_dir",
                     "_enum_uninstall_keys", "_read_uninstall_value")
            saved = {n: getattr(m, n) for n in seams}

            def restore():
                for n in seams:
                    setattr(m, n, saved[n])

            sd = self._manifest_sandbox("stubs")
            self._manifest_payloads(sd)
            data_root = str(self.store_dir / "_manifest" / "stub_data" / "ZaojingAI")
            fake_appdata = self.store_dir / "_manifest" / "fakeappdata"
            base_clean = m.verify_script(self.MF_CLEAN_SCRIPT, str(sd), "")
            base_broken = m.verify_script(self.MF_BROKEN_SCRIPT, str(sd), data_root)
            seen = {}
            try:
                # 桩 1：磁盘存在性判 False → 合规清单必翻出缺件 error（零检出非硬编码）
                m._path_exists = lambda p: False
                s1 = m.verify_script(self.MF_CLEAN_SCRIPT, str(sd), "")
                restore()
                seen["path_stub"] = ([f["id"] for f in s1["findings"]], s1["verdict"])

                # 桩 2：通配桩出命中 → 零命中 error 消失（该 error 由真实 glob 派生）
                m._glob_matches = lambda pat: [str(Path(pat).parent / "fake.dll")]
                s2 = m.verify_script(self.MF_BROKEN_SCRIPT, str(sd), data_root)
                restore()
                seen["glob_stub_ids"] = [f["id"] for f in s2["findings"]]

                # 桩 3：环境目录可解析 → R7 零检出；不可解析 → R7 warn（同脚本双向翻转）
                m._env_dir = lambda name: (str(fake_appdata)
                                           if name == "LOCALAPPDATA" else "")
                s3a = m.verify_script(self.MF_SHAPE_SCRIPT, str(sd),
                                      str(fake_appdata / "ZaojingAI"))
                m._env_dir = lambda name: ""
                s3b = m.verify_script(self.MF_SHAPE_SCRIPT, str(sd),
                                      str(fake_appdata / "ZaojingAI"))
                restore()
                seen["envdir_pair"] = (s3a["counts"]["total"],
                                       [(f["id"], f["severity"]) for f in s3b["findings"]])

                # 桩 4：注册表桩命中 → installed 真翻 True 并回显逐项观察；桩空 → False
                # （Phase 53 起接缝带 view 参数：root×视图四路枚举，桩必须同形制，
                #  否则桩本身失配——旧桩写单参数 lambda 时直接被引擎以两参调用炸掉）
                #
                # DisplayName 用本机真装实测那枚 "ZaojingAI 1.0.0"（Phase 58：未声明
                # UninstallDisplayName 时 Inno 写 AppName+" "+AppVersion）。桩若继续写
                # "ZaojingAI"，就只会证明"精确键名那条路走得通"，而兜底那条路在自测里
                # 永远不跑——第 6 枚真缺陷正是这样藏了下来的。
                m._enum_uninstall_keys = (lambda root_name, view="64":
                                          ([self.MF_APPID + "_is1"]
                                           if root_name == "HKCU" else []))
                m._read_uninstall_value = (lambda r, k, n, view="64": {
                    "DisplayName": "ZaojingAI 1.0.0", "DisplayVersion": "1.0.0",
                    "UninstallString": "C:\\fixture\\unins000.exe",
                    "InstallLocation": "C:\\fixture"}[n])
                m._dir_exists = lambda p: False
                s4a = m.install_state(self.MF_APPID, "ZaojingAI", [str(sd)])
                # 精确命中优先于兜底：app_id 已知且真在盘上时，回执必须是"按派生键名命中"，
                # 不许同时报一枚"按显示名认领"——否则显示名规则就成了第二把随时能开的门。
                s4c = m.install_state("", "ZaojingAI", [str(sd)])
                m._enum_uninstall_keys = lambda root_name, view="64": []
                s4b = m.install_state(self.MF_APPID, "ZaojingAI", [str(sd)])
                restore()
                seen["reg_pair"] = (s4a["installed"], s4b["installed"],
                                    [p["probe"] for p in s4b["probes"]])
                seen["exact_beats_fallback"] = (
                    s4a["installed"],
                    [p["probe"] for p in s4a["probes"]].count("registry_display_name"))
                seen["fallback_only"] = (
                    s4c["installed"],
                    [(p["probe"], p["root"], p["view"], p.get("subkey", "n/a"),
                      p.get("display_name", "n/a"))
                     for p in s4c["probes"] if p["probe"] == "registry_display_name"])
                hkcu = [p for p in s4a["probes"]
                        if p["probe"] == "registry_uninstall" and p["root"] == "HKCU"
                        and p["view"] == "64"][0]
            finally:
                restore()
            ok = (base_clean["counts"]["total"] == 0
                  and base_clean["verdict"] == "manifest_ok"
                  and seen["path_stub"] == (["files_source_literal_missing",
                                             "files_source_literal_missing"],
                                            "manifest_invalid")
                  and "files_source_wildcard_no_match" not in seen["glob_stub_ids"]
                  and len(seen["glob_stub_ids"]) == len(base_broken["findings"]) - 1
                  and seen["envdir_pair"] == (0, [("dirs_miss_runtime_data_root", "warn")])
                  and seen["reg_pair"][0] is True and seen["reg_pair"][1] is False
                  # 未命中时 root×视图四路枚举全部回显（Phase 53 显式化 32/64 视图，
                  # 只查 64 位视图会把 WOW6432Node 里的真安装项判成未安装）
                  and seen["reg_pair"][2] == ["registry_uninstall", "registry_uninstall",
                                              "registry_uninstall", "registry_uninstall",
                                              "start_menu_group", "app_data_root",
                                              "portable_flag"]
                  and hkcu["present"] is True and hkcu["subkey_count"] == 1
                  and hkcu["view"] == "64"
                  and hkcu["looking_for"] == self.MF_APPID + "_is1"
                  and hkcu["display_version"] == "1.0.0"
                  # 显示名精确命中时也必须回显**实测那枚形制**（AppName+" "+AppVersion），
                  # 且四路枚举不因命中而短路——"看过哪几路"是回执要自证的事，不是内部细节。
                  and hkcu["display_name"] == "ZaojingAI 1.0.0"
                  and [p["probe"] for p in s4a["probes"]].count("registry_uninstall") == 4
                  # 精确查找命中 ⇒ 兜底一趟都不许跑（否则显示名成了第二把随时能开的门）
                  and seen["exact_beats_fallback"] == (True, 0)
                  # 无 app_id 时兜底必须命中实测显示名：这条路径在 Phase 58 之前**零覆盖**，
                  # 因为桩里的显示名写成逐字相等的旧形制，于是"按 DisplayName 兜底"这句
                  # 文档承诺从未被任何仪器跑过（第 6 枚真缺陷的藏身处）。
                  and seen["fallback_only"] == (
                      True, [("registry_display_name", "HKCU", "64",
                              self.MF_APPID + "_is1", "ZaojingAI 1.0.0")])
                  and s4a["installed_basis"].startswith("installed 仅由注册表卸载项真实命中派生")
                  and all(getattr(m, n) is saved[n] for n in seams))
            return ok, {"expected": "四路接缝反证：真实磁盘/glob/环境/注册表逐项决定结论，"
                                    "桩换即翻转，还原后与基线一致（判分可证伪）；注册表那一趟"
                                    "root×视图四路全回显、精确命中优先于 DisplayName 兜底，"
                                    "且兜底必须认得本机实写的 AppName+空格+AppVersion 形制",
                        "actual": seen}

        def mf_rejects_and_installed_derivation(E):
            m = E["installer_manifest"]
            sd = self._manifest_sandbox("rejects")
            self._manifest_payloads(sd)
            msgs = {}
            for label, fn in (
                    ("text_none", lambda: m.verify_script(None, str(sd))),
                    ("text_blank", lambda: m.verify_script("   ", str(sd))),
                    ("text_not_str", lambda: m.verify_script(7, str(sd))),
                    ("root_blank", lambda: m.verify_script(self.MF_CLEAN_SCRIPT, "  ")),
                    ("root_none", lambda: m.verify_script(self.MF_CLEAN_SCRIPT, None)),
                    ("state_nonstr", lambda: m.install_state(7, "ZaojingAI")),
                    ("extra_blank", lambda: m.install_state(self.MF_APPID, "ZaojingAI", [""])),
                    ("extra_nonstr", lambda: m.install_state(self.MF_APPID, "ZaojingAI", [7]))):
                try:
                    fn()
                    msgs[label] = "NO_RAISE"
                except m.ManifestError as e:
                    msgs[label] = str(e)
                except Exception as e:  # 非 ManifestError 即不如实
                    msgs[label] = "%s:%s" % (type(e).__name__, e)
            r1 = m.verify_script(self.MF_BROKEN_SCRIPT, str(sd), str(Path(sd) / "data"))
            r2 = m.verify_script(self.MF_BROKEN_SCRIPT, str(sd), str(Path(sd) / "data"))
            h = lambda o: hashlib.sha256(json.dumps(o, sort_keys=True,
                                                    ensure_ascii=False).encode("utf-8")
                                         ).hexdigest()
            (sd / "portable.flag").write_text("portable\n", encoding="utf-8")
            st = m.install_state(self.MF_APPID, "ZaojingAI", [str(sd)])
            derived = any(p.get("present") is True or p["probe"] == "registry_display_name"
                          for p in st["probes"])
            flags = [p for p in st["probes"] if p["probe"] == "portable_flag"]
            nf = lambda p: str(p).replace("\\", "/")
            verdict_ok = (r1["verdict"] == "manifest_invalid"
                          and r1["counts"]["errors"] > 0)
            ok = (msgs == {"text_none": "脚本内容为空或非文本",
                           "text_blank": "脚本内容为空或非文本",
                           "text_not_str": "脚本内容为空或非文本",
                           "root_blank": "payload_root 必须是非空路径",
                           "root_none": "payload_root 必须是非空路径",
                           "state_nonstr": "app_id/app_name 必须是字符串",
                           "extra_blank": "extra_roots 每项必须是非空字符串",
                           "extra_nonstr": "extra_roots 每项必须是非空字符串"}
                  and h(r1) == h(r2) and verdict_ok
                  and r1["counts"]["total"] == len(r1["findings"])
                  and m.uninstall_registry_key(self.MF_APPID) == self.MF_APPID + "_is1"
                  # 脚本里的转义写法与生效值必须派生出同一枚键名（Phase 53 改双写时若漂移，
                  # 只读安装态探测在真机上永远查不中）
                  and m.uninstall_registry_key(self.MF_APPID_SCRIPT_FORM)
                  == self.MF_APPID + "_is1"
                  # 无括号 AppId 绝不补括号：本机 HKLM 32 位视图里实测存在的
                  # "Inno Setup 6_is1"（AppId 本身无括号）就是旧补括号实现的反证
                  and m.uninstall_registry_key("not-a-guid") == "not-a-guid_is1"
                  and st["derived_uninstall_key"] == self.MF_APPID + "_is1"
                  and st["environment"] in ("windows", "non_windows")
                  and len([p for p in st["probes"] if p["probe"] == "registry_uninstall"]) == 4
                  and all(p["looking_for"] == self.MF_APPID + "_is1"
                          and isinstance(p["present"], bool)
                          and isinstance(p["subkey_count"], int)
                          for p in st["probes"] if p["probe"] == "registry_uninstall")
                  and sorted({(p["root"], p["view"]) for p in st["probes"]
                              if p["probe"] == "registry_uninstall"}) == [
                      ("HKCU", "32"), ("HKCU", "64"), ("HKLM", "32"), ("HKLM", "64")]
                  and flags and flags[0]["exists"] is True
                  and nf(flags[0]["path"]) == nf(Path(sd) / "portable.flag")
                  and st["installed"] is derived      # installed 由 probes 逐项派生，非硬编码
                  and st["fabricated"] is False
                  and st["execution"]["performed"] is False
                  # P58：只读引擎的执行域归口从"Inno 编译产物"改写为"同产品 CLI 执行器"——
                  # 执行器真交付了，但本引擎仍然零子进程，所以 performed 依旧 False。
                  and st["execution"]["owned_by"].startswith("installer_runtime_engine CLI executor"))
            return ok, {"expected": "入参拒绝八类原文逐字 + 两次调用 JSON sha256 全等 + "
                                    "卸载键名形状 + installed 必由 probes 真命中派生"
                                    "（真 portable 标记不翻 installed）",
                        "actual": {"rejects": msgs, "sha_equal": h(r1) == h(r2),
                                   "counts": r1["counts"], "installed": st["installed"],
                                   "derived_from_probes": derived,
                                   "flag_exists": bool(flags and flags[0]["exists"])}}

        # ==================================================================
        # Phase 53：installer_manifest 新增两脸用例（编译面 verify_build_evidence +
        # 预检面 preflight_install）与三条静态开火臂（R6 转义 / R12 弃用架构 / R13 版本
        # 形制 / R11 配对）。机器无关纪律：凡随本机而异的量（磁盘余量、令牌是否已提权、
        # Program Files 实际落点、write_text 落盘的 CRLF 字节数）一律写成**关系式**或走
        # 显式接缝桩；随本机而异的值绝不写进 details.actual（顶层/嵌套两次判分细节全等是
        # Phase 49 立的硬门）。期望逐项取自 p53_face_probe3/4/5.py 对评测台原件夹具的
        # 磁盘实测冻结。
        # ==================================================================
        def mf_build_evidence_verified(E):
            m = E["installer_manifest"]
            sd, sp, ap = self._manifest_build_sandbox("bv")
            ev = self._mf_evidence(sp, ap)
            ab, sb = ap.read_bytes(), sp.read_bytes()
            dr = str(self.store_dir / "_manifest" / "bv_data")
            before = sorted(p.relative_to(sd).as_posix() for p in sd.rglob("*"))
            rep = m.verify_build_evidence(ev, str(sp), str(ap), dr)
            lie = m.verify_build_evidence(dict(ev, compiled=False, fabricated=True),
                                          str(sp), str(ap), dr)
            after = sorted(p.relative_to(sd).as_posix() for p in sd.rglob("*"))

            def without_self(r):
                return json.dumps({k: v for k, v in r.items()
                                   if k != "evidence_self_report"},
                                  sort_keys=True, default=str)

            ok = (
                # 证据先与本用例独立重算的字节/哈希对账（抄引擎返回值只能自证一致）
                ev["artifact_bytes"] == len(ab)
                and ev["artifact_sha256"] == hashlib.sha256(ab).hexdigest()
                and ev["script_bytes"] == len(sb)
                and ev["script_sha256"] == hashlib.sha256(sb).hexdigest()
                and ev["iscc_output_chars"] == len(self._mf_tail())
                # 编译成功 = 三独立信号同立（退出码 0 + 尾部真见过完成行 + 产物在盘）
                and rep["signals"]["exit_zero"] is True
                and rep["signals"]["compile_line_seen"] is True
                and rep["signals"]["compile_line_source"] == "tail"
                and rep["signals"]["compile_line_text"] == self.MF_COMPILE_LINE
                and rep["signals"]["artifact_present"] is True
                and rep["compile_succeeded"] is True
                # 逐侧双比：脚本与产物各比字节数与 sha256
                and rep["script"]["present"] is True
                and [rep["script"]["bytes_match"], rep["script"]["sha256_match"]] == [True, True]
                and [rep["artifact"]["bytes_match"], rep["artifact"]["sha256_match"]] == [True, True]
                and rep["verdict"] == "artifact_verified"
                and rep["blocking_codes"] == [] and rep["missing_fields"] == []
                and rep["face"] == "installer_artifact_build" and rep["schema"] == m.SCHEMA
                # 告警可比：记 0 数 = 全文尾部实 0 行
                and [rep["warnings"]["recorded_count"], rep["warnings"]["tail_recount"]] == [0, 0]
                and rep["warnings"]["tail_is_full_output"] is True
                and rep["warnings"]["consistent"] is True
                and rep["output_tail"]["key"] == "iscc_output_tail"
                and rep["output_tail"]["chars"] == len(ev["iscc_output_tail"])
                and rep["output_tail"]["recorded_total_chars"] == ev["iscc_output_chars"]
                # 静态复校当下重跑（不抄记录值）且与记录一致
                and rep["manifest_now"]["verdict"] == "manifest_ok"
                and rep["manifest_now"]["counts"] == {"errors": 0, "warnings": 0,
                                                     "infos": 0, "total": 0}
                and rep["manifest_now"]["finding_ids"] == []
                and rep["manifest_now"]["same_as_recorded"] is True
                and rep["manifest_now"]["data_root"] == dr
                # 编译器只回显实况：recorded_on_disk 由证据自带路径实读派生，
                # 现寻径结果与模块级 iscc_available() 同口径（不各说各话）
                and rep["compiler"]["recorded_on_disk"] is bool(
                    ev["compiler_path"] and Path(ev["compiler_path"]).is_file())
                and rep["compiler"]["now_located"]["found"] is m.iscc_available()
                and rep["compiler"]["now_located"]["mode"] in ("fixed_dir", "registry",
                                                              "path", "none")
                # 自报布尔不参与判定：翻转 compiled/fabricated 后除回显字段外整份报告全等
                and rep["evidence_self_report"]["ignored"] is True
                and lie["evidence_self_report"] == dict(rep["evidence_self_report"],
                                                        compiled=False, fabricated=True)
                and without_self(rep) == without_self(lie)
                and lie["verdict"] == "artifact_verified"
                # 编译 ≠ 安装（真值表 installer_execution 仍 not_shipped）
                and rep["not_install"]["installed_claimed"] is False
                and rep["not_install"]["artifact_present"] is True
                and rep["fabricated"] is False
                # 只读声明可证伪：校验前后沙箱内容逐条全等
                and before == after and len(before) == 5)
            return ok, {"expected": "编译面正控：三独立信号同立 + 证据字节/sha256 与本用例"
                                    "独立重算一致 + 全文尾部告警可比 + 静态复校当下重跑且与"
                                    "记录一致 + 自报 compiled/fabricated 翻转不改判 + 校验零落盘",
                        "actual": {"verdict": rep["verdict"], "codes": rep["blocking_codes"],
                                   "line_source": rep["signals"]["compile_line_source"],
                                   "matches": [rep["script"]["bytes_match"],
                                               rep["script"]["sha256_match"],
                                               rep["artifact"]["bytes_match"],
                                               rep["artifact"]["sha256_match"]],
                                   "warn": [rep["warnings"]["recorded_count"],
                                            rep["warnings"]["tail_recount"],
                                            rep["warnings"]["tail_is_full_output"]],
                                   "lie_verdict": lie["verdict"],
                                   "lie_rest_equal": without_self(rep) == without_self(lie),
                                   "listing_stable": before == after}}

        def mf_build_evidence_mismatch_arms(E):
            m = E["installer_manifest"]
            dr = str(self.store_dir / "_manifest" / "bm_data")
            # 臂 1：等长原地替换（字节数仍相符，只有 sha256 能发现）
            sd1, sp1, ap1 = self._manifest_build_sandbox("bm_eq")
            ev1 = self._mf_evidence(sp1, ap1)
            ap1.write_bytes(b"MZ\x90\x00" + b"b" * 64)
            r1 = m.verify_build_evidence(ev1, str(sp1), str(ap1), dr)
            # 臂 2：编译之后脚本又被改动（只比证据/产物必漏的陈旧态）
            sd2, sp2, ap2 = self._manifest_build_sandbox("bm_stale")
            ev2 = self._mf_evidence(sp2, ap2)
            orig = sp2.read_bytes()
            sp2.write_text(self.MF_CLEAN_SCRIPT + "\n; 编译后又改了一行\n", encoding="utf-8")
            r2 = m.verify_build_evidence(ev2, str(sp2), str(ap2), dr)
            # 臂 3：产物不在盘
            sd3, sp3, ap3 = self._manifest_build_sandbox("bm_gone")
            ev3 = self._mf_evidence(sp3, ap3)
            os.remove(ap3)
            r3 = m.verify_build_evidence(ev3, str(sp3), str(ap3), dr)
            # 臂 4：产物在盘而三信号不齐（退出码非零 + 尾部无完成行）
            sd4, sp4, ap4 = self._manifest_build_sandbox("bm_np")
            abort = "   Compiling Setup\r\n   Aborted"
            ev4 = self._mf_evidence(sp4, ap4, tail=abort, recorded=[],
                                    output_chars=len(abort), iscc_exit_code=2)
            r4 = m.verify_build_evidence(ev4, str(sp4), str(ap4), dr)
            # 优先级实证：产物缺席先于"编译未证成"
            r3b = m.verify_build_evidence(dict(ev3, iscc_exit_code=2), str(sp3), str(ap3), dr)
            stale = r2["blocking"][0]["detail"]
            ok = (
                r1["verdict"] == "evidence_mismatch"
                and r1["blocking_codes"] == ["artifact_sha256_mismatch"]
                and r1["artifact"]["bytes_match"] is True
                and r1["artifact"]["sha256_match"] is False
                and len(ap1.read_bytes()) == ev1["artifact_bytes"]
                # "编译成功"与"产物被换掉"是两回事：信号仍齐但判据不符
                and r1["compile_succeeded"] is True
                and r2["verdict"] == "evidence_mismatch"
                and r2["blocking_codes"] == ["artifact_not_from_current_script"]
                and [r2["artifact"]["bytes_match"], r2["artifact"]["sha256_match"]] == [True, True]
                and [r2["script"]["bytes_match"], r2["script"]["sha256_match"]] == [False, False]
                and stale["script_expected"]["sha256"] == ev2["script_sha256"]
                and stale["script_expected"]["bytes"] == len(orig)
                and stale["script_observed"]["sha256"] == hashlib.sha256(
                    sp2.read_bytes()).hexdigest()
                and stale["artifact_sha256_match"] is True
                # 只跑静态面必漏：追加的是注释行，当下复校仍判 manifest_ok
                and r2["manifest_now"]["verdict"] == "manifest_ok"
                and r2["manifest_now"]["same_as_recorded"] is True
                and r3["verdict"] == "artifact_missing" and r3["blocking_codes"] == []
                and r3["compile_succeeded"] is False
                and r3["artifact"]["present"] is False and r3["artifact"]["observed"] is None
                and r3["signals"]["artifact_present"] is False
                and r3["not_install"]["installed_claimed"] is False
                and r3["not_install"]["artifact_present"] is False
                and r3["evidence_self_report"]["ignored"] is True
                and r3b["verdict"] == "artifact_missing"
                and r4["verdict"] == "compile_not_proven" and r4["blocking_codes"] == []
                and r4["compile_succeeded"] is False
                and r4["signals"]["exit_zero"] is False
                and r4["signals"]["compile_line_seen"] is False
                and r4["signals"]["compile_line_source"] == "absent"
                and r4["signals"]["iscc_exit_code"] == 2
                and r4["artifact"]["present"] is True
                # 静态合规 ≠ 编译通过（臂 4 的脚本正是零检出的合规夹具）
                and r4["manifest_now"]["verdict"] == "manifest_ok")
            return ok, {"expected": "编译面四臂：等长替换只有 sha256 能发现（compile_succeeded "
                                    "仍 True）+ 编译后改脚本=artifact_not_from_current_script "
                                    "而静态复校照旧 ok（陈旧态只有脚本侧对比能抓）+ 产物缺席"
                                    "判 artifact_missing 且优先于编译未证成 + 退出码非零无完成"
                                    "行判 compile_not_proven",
                        "actual": {"equal_len": [r1["verdict"], r1["blocking_codes"],
                                                 r1["artifact"]["bytes_match"],
                                                 r1["artifact"]["sha256_match"],
                                                 r1["compile_succeeded"]],
                                   "stale": [r2["verdict"], r2["blocking_codes"],
                                             r2["script"]["sha256_match"],
                                             r2["artifact"]["sha256_match"],
                                             r2["manifest_now"]["verdict"],
                                             stale["script_expected"]["bytes"] == len(orig)],
                                   "gone": [r3["verdict"], r3["blocking_codes"],
                                            r3["artifact"]["present"], r3b["verdict"]],
                                   "not_proven": [r4["verdict"], r4["blocking_codes"],
                                                  r4["signals"]["compile_line_source"],
                                                  r4["artifact"]["present"],
                                                  r4["manifest_now"]["verdict"]]}}

        def mf_build_evidence_warnings_and_completeness(E):
            m = E["installer_manifest"]
            dr = str(self.store_dir / "_manifest" / "bw_data")
            sd, sp, ap = self._manifest_build_sandbox("bw")
            t2 = self._mf_tail(("w0", "w1"))
            three = ["Warning: w off-screen", "Warning: w0", "Warning: w1"]
            arms = {}
            for tag, kw in (("eq_full", {}), ("more_full", {"recorded": three}),
                            ("more_excerpt", {"recorded": three,
                                              "output_chars": len(t2) + 700}),
                            ("less_excerpt", {"recorded": ["Warning: w0"],
                                              "output_chars": len(t2) + 700})):
                r = m.verify_build_evidence(self._mf_evidence(sp, ap, ("w0", "w1"), **kw),
                                            str(sp), str(ap), dr)
                arms[tag] = {"verdict": r["verdict"], "codes": r["blocking_codes"],
                            "rec": r["warnings"]["recorded_count"],
                            "recount": r["warnings"]["tail_recount"],
                            "full": r["warnings"]["tail_is_full_output"],
                            "consistent": r["warnings"]["consistent"],
                            "lines": r["warnings"]["tail_warning_lines"],
                            "detail": (r["blocking"][0]["detail"] if r["blocking"] else None)}
            # 缺字段臂：证据残缺优先于一切判词
            sdi, spi, api = self._manifest_build_sandbox("bi")
            evi = self._mf_evidence(spi, api)
            for k in ("script_sha256", "artifact_bytes", "iscc_output_tail",
                      "iscc_output_chars", "iscc_warnings"):
                evi[k] = None
            ri = m.verify_build_evidence(evi, str(spi), str(api), dr)
            # 完成行来源两臂：尾部无行而派生字段有行 / 旧形制 stderr 尾部
            sd2, sp2, ap2 = self._manifest_build_sandbox("bdl")
            no_line = "   Compiling Setup\r\n   Done\r\n"
            rdl = m.verify_build_evidence(
                self._mf_evidence(sp2, ap2, tail=no_line, recorded=[],
                                  output_chars=len(no_line),
                                  iscc_compile_line=self.MF_COMPILE_LINE),
                str(sp2), str(ap2), dr)
            ev_old = self._mf_evidence(sp2, ap2)
            ev_old["iscc_stderr_tail"] = ev_old.pop("iscc_output_tail")
            rold = m.verify_build_evidence(ev_old, str(sp2), str(ap2), dr)
            # 证据来自文件（生产 installer/build-evidence.json 的真形制）
            sdf = self._manifest_sandbox("bf")
            ef = sdf / "build-evidence.json"
            ef.write_text(json.dumps(self._mf_evidence(sp2, ap2), ensure_ascii=False),
                          encoding="utf-8")
            rfile = m.verify_build_evidence(str(ef))
            ok = (
                # 等式口径（全文尾部）：记 2 = 实 2 放行
                arms["eq_full"]["verdict"] == "artifact_verified"
                and arms["eq_full"]["full"] is True
                and [arms["eq_full"]["rec"], arms["eq_full"]["recount"]] == [2, 2]
                and arms["eq_full"]["codes"] == []
                # 记 3 > 实 2 且尾部是全文 → 多记即谎报编译器多说话
                and arms["more_full"]["verdict"] == "evidence_mismatch"
                and arms["more_full"]["codes"] == ["warnings_count_mismatch"]
                and arms["more_full"]["detail"]["recorded_count"] == 3
                and arms["more_full"]["detail"]["tail_recount"] == 2
                and arms["more_full"]["lines"] == ["Warning: w0", "Warning: w1"]
                # 下界口径（截断尾部）：记 3 ≥ 实 2 不误报
                and arms["more_excerpt"]["full"] is False
                and arms["more_excerpt"]["verdict"] == "artifact_verified"
                and arms["more_excerpt"]["codes"] == []
                # 截断也不可少记：记 1 < 实 2 仍是谎报
                and arms["less_excerpt"]["verdict"] == "evidence_mismatch"
                and arms["less_excerpt"]["codes"] == ["warnings_count_mismatch"]
                and arms["less_excerpt"]["consistent"] is False
                and arms["less_excerpt"]["detail"]["recorded_count"] == 1
                # 缺字段臂
                and ri["verdict"] == "evidence_incomplete"
                and ri["missing_fields"] == ["script_sha256", "artifact_bytes",
                                             "iscc_output_tail", "iscc_stderr_tail"]
                and ri["blocking_codes"] == ["artifact_not_from_current_script",
                                             "artifact_bytes_mismatch"]
                and ri["compile_succeeded"] is False
                and ri["signals"]["compile_line_source"] == "absent"
                and ri["output_tail"]["key"] is None and ri["output_tail"]["chars"] == 0
                # 诚实性：无尾部字段既不能说编译器说过话，也不能说它没说话
                and ri["output_tail"]["provenance"].startswith("无输出尾部字段")
                and ri["warnings"]["recorded_count"] is None
                and ri["warnings"]["consistent"] is True
                # 完成行退回派生字段口径仍算见过
                and rdl["verdict"] == "artifact_verified"
                and rdl["signals"]["compile_line_source"] == "derived_field"
                and rdl["signals"]["compile_line_text"] == self.MF_COMPILE_LINE
                and rdl["output_tail"]["provenance"].startswith("iscc_output_tail")
                and rdl["blocking_codes"] == []
                # 旧形制 stderr 尾部：键名与 provenance 一起如实回显，不冒充 stdout 全文
                and rold["output_tail"]["key"] == "iscc_stderr_tail"
                and rold["output_tail"]["provenance"].startswith("iscc_stderr_tail")
                and rold["signals"]["compile_line_source"] == "tail"
                and rold["verdict"] == "artifact_verified"
                # 文件形制证据
                and rfile["evidence_source"]["from"] == "file"
                and rfile["evidence_source"]["bytes"] == ef.stat().st_size
                and rfile["evidence_source"]["schema_match"] is True
                and rfile["verdict"] == "artifact_verified"
                and rfile["manifest_now"]["verdict"] == "manifest_ok")
            return ok, {"expected": "告警四臂等式/下界口径（全文尾部记 2=实 2 放行、记 3 报不符；"
                                    "截断尾部多记不误报、少记仍报不符）+ 缺字段判 "
                                    "evidence_incomplete 且 provenance 不替编译器说话 + "
                                    "完成行可退回派生字段 + stderr 旧键如实回显 + "
                                    "证据走 build-evidence.json 文件形制",
                        "actual": {"arms": arms, "incomplete": [ri["verdict"],
                                                                 ri["missing_fields"],
                                                                 ri["blocking_codes"],
                                                                 ri["output_tail"]["key"]],
                                   "derived": [rdl["signals"]["compile_line_source"],
                                               rdl["verdict"]],
                                   "old_key": [rold["output_tail"]["key"], rold["verdict"]],
                                   "file_src": [rfile["evidence_source"]["from"],
                                                rfile["evidence_source"]["bytes"] ==
                                                ef.stat().st_size]}}

        def mf_preflight_ready_and_readonly_promise(E):
            m = E["installer_manifest"]
            sd, sp, ap = self._manifest_build_sandbox("pr")
            ev = self._mf_evidence(sp, ap)
            dr = str(self.store_dir / "_manifest" / "pr_data")
            tgt = str(sd / "install-target")
            tgt2 = str(sd / "probe-disabled")
            before = sorted(p.relative_to(sd).as_posix() for p in sd.rglob("*"))
            real_tok = m._token_elevation
            TOK_UP = {"available": True, "elevated": True, "elevation_type": 1,
                      "is_admin_account": True, "reason": None, "basis": "eval 显式桩"}
            try:
                # 真令牌臂：只以关系式判（探测不到 → result None 且进 unknown，不阻断）
                live = m.preflight_install(self.MF_CLEAN_SCRIPT, str(sd), dr,
                                           evidence=ev, target_override=tgt)
                m._token_elevation = lambda: dict(TOK_UP)
                ready = m.preflight_install(self.MF_CLEAN_SCRIPT, str(sd), dr,
                                            evidence=ev, target_override=tgt)
                # 未给 target_override：真实安装根绝不作为探针目标（只读承诺）
                nover = m.preflight_install(self.MF_CLEAN_SCRIPT, str(sd), dr, evidence=ev)
                # 调用方显式关闭写探针：不声称可写也不声称不可写，且连目录都不建
                off = m.preflight_install(self.MF_CLEAN_SCRIPT, str(sd), dr, evidence=ev,
                                          target_override=tgt2, probe_writable=False)
            finally:
                m._token_elevation = real_tok
            after = sorted(p.relative_to(sd).as_posix() for p in sd.rglob("*"))
            c = ready["checks"]
            cands = c["install_root"]["candidates"]
            payload_bytes = sum(p.stat().st_size for p in
                                (sd / "zaojing.exe", sd / "docs" / "上手指南.pdf"))
            live_elev = live["checks"]["privileges"]["elevation"]
            ok = (
                ready["verdict"] == "preflight_ready"
                and ready["blocking_codes"] == [] and ready["unknown_codes"] == []
                and ready["face"] == "installer_preflight" and ready["schema"] == m.SCHEMA
                and sorted(c) == ["artifact", "disk_space", "install_root", "payload",
                                  "privileges", "target_conflict", "writability"]
                and all(c[k]["result"] is True for k in c)
                # 真令牌关系臂：不要求提权时 available 为 None 只可能是 None 而非 False
                and live["checks"]["privileges"]["result"] is (
                    None if live_elev["available"] is None else True)
                and ("elevation_state_unresolved" in live["unknown_codes"]) is (
                    live_elev["available"] is None)
                and live["blocking_codes"] == []
                and c["privileges"]["declared"] == "lowest"
                and c["privileges"]["effective"] == "lowest"
                # 写探针真落盘且自清：探针文件与其自建目录都不留
                and c["writability"]["probed"] is True
                and c["writability"]["target"] == tgt
                and c["writability"]["detail"]["via"] == "real_probe_file"
                and c["writability"]["detail"]["bytes"] == self.MF_PROBE_PAYLOAD_BYTES
                and c["writability"]["detail"]["created_directory"] is True
                and c["writability"]["detail"]["probe_removed"] is True
                and os.path.exists(tgt) is False
                and os.path.exists(Path(tgt) / m.PROBE_FILE_NAME) is False
                # 安装根候选：{autopf} 成对给出 commonpf/userpf，解析与路径一致
                and c["install_root"]["judged_on"] == "target_override"
                and c["install_root"]["declared_default_dir_name"] == "{autopf}\\ZaojingAI"
                and [x["inno_form"] for x in cands] == ["commonpf", "userpf"]
                and all(x["constant"] == "{autopf}"
                        and bool(x["path"]) is not x["unresolved"] for x in cands)
                # 目标冲突：只读观察 + share=0 试探，全新目录无冲突可查
                and c["target_conflict"]["detail"]["exists_as_file"] is False
                and c["target_conflict"]["detail"]["held"] == []
                and c["target_conflict"]["detail"]["exe_checked"] == []
                # 磁盘：需求 = 产物字节 + 声明载荷字节实测和（系数不外推）
                and c["disk_space"]["required_bytes"] == (c["disk_space"]["artifact_bytes"]
                                                          + c["disk_space"]["payload_bytes"])
                and c["disk_space"]["artifact_bytes"] == ap.stat().st_size
                and c["disk_space"]["payload_bytes"] == payload_bytes
                and c["disk_space"]["required_bytes"] == 68 + 20
                # 载荷与静态面 R2/R3 同一判据
                and c["payload"]["declared_sources"] == 2
                and c["payload"]["present_count"] == 2
                and c["payload"]["missing_count"] == 0 and c["payload"]["findings"] == []
                # 产物复用编译面结论
                and c["artifact"]["state"] == "verified"
                and c["artifact"]["verdict"] == "artifact_verified"
                and c["artifact"]["detail"]["artifact"]["sha256_match"] is True
                # 执行边界取自单一真相源常量：预检不装、不写注册表、不建快捷方式
                and ready["execution"] == dict(m.EXECUTION_BOUNDARY)
                and ready["execution"]["performed"] is False
                and ready["execution"]["status"] == "not_executed_by_this_engine"
                and ready["execution"]["retired_boundary"]["was"] == "needs_installer_environment"
                and ready["not_install"]["installed_claimed"] is False
                and ready["not_install"]["preflight_is_not_install"] is True
                and ready["not_install"]["compile_is_not_install"] is True
                and ready["fabricated"] is False
                and ready["target_override"] == tgt and ready["probe_writable"] is True
                # 未给 override：真实安装根只作候选回显，探针不落
                and nover["verdict"] == "preflight_unknown"
                and nover["unknown_codes"] == ["write_probe_unprobed"]
                and nover["blocking_codes"] == []
                and nover["checks"]["writability"]["result"] is None
                and nover["checks"]["writability"]["probed"] is False
                and nover["checks"]["writability"]["detail"]["reason"] == "no_probe_target"
                and nover["checks"]["install_root"]["judged_on"] == "first_resolved_candidate"
                and nover["checks"]["writability"]["target"] == next(
                    x["path"] for x in nover["checks"]["install_root"]["candidates"]
                    if x["path"])
                and os.path.exists(Path(str(nover["checks"]["writability"]["target"]))
                                   / m.PROBE_FILE_NAME) is False
                # 显式关闭探针：unknown 而非 blocked，目录也不建
                and off["verdict"] == "preflight_unknown"
                and off["unknown_codes"] == ["write_probe_unprobed"]
                and off["checks"]["writability"]["detail"]["reason"] == "caller_disabled_probe"
                and off["checks"]["writability"]["probed"] is False
                and os.path.exists(tgt2) is False
                # 接缝还原 + 预检零落盘
                and m._token_elevation is real_tok
                and before == after and len(before) == 5)
            return ok, {"expected": "预检面正控：七项检查全 True 且 blocked/unknown 皆空（令牌"
                                    "走显式桩以钉 verdict，真令牌臂只以关系式判）+ 写探针真落盘"
                                    "自清（探针文件与自建目录均不留）+ {autopf} 两条候选 + "
                                    "需求字节=产物+载荷实测和 + 执行边界取单一真相源常量 + "
                                    "不给 override 时真实安装根零探针 + 预检零落盘",
                        "actual": {"verdict": ready["verdict"],
                                   "codes": [ready["blocking_codes"], ready["unknown_codes"]],
                                   "check_results": {k: c[k]["result"] for k in sorted(c)},
                                   "write": [c["writability"]["detail"]["via"],
                                             c["writability"]["detail"]["bytes"],
                                             c["writability"]["detail"]["created_directory"],
                                             c["writability"]["detail"]["probe_removed"]],
                                   "target_left": os.path.exists(tgt),
                                   "forms": [x["inno_form"] for x in cands],
                                   "disk": [c["disk_space"]["artifact_bytes"],
                                            c["disk_space"]["payload_bytes"],
                                            c["disk_space"]["required_bytes"]],
                                   "nover": [nover["verdict"], nover["unknown_codes"],
                                             nover["checks"]["writability"]["probed"]],
                                   "off": [off["verdict"], os.path.exists(tgt2)],
                                   "listing_stable": before == after}}

        def mf_preflight_blocked_and_seam_stubs(E):
            m = E["installer_manifest"]
            sd, sp, ap = self._manifest_build_sandbox("pb")
            ev = self._mf_evidence(sp, ap)
            dr = str(self.store_dir / "_manifest" / "pb_data")
            tgt = str(sd / "t")
            hold = sd / "hold"
            hold.mkdir(parents=True)
            (hold / "zaojing.exe").write_bytes(b"MZ")
            as_file = sd / "as-file"
            as_file.write_bytes(b"x")
            admin = self.MF_CLEAN_SCRIPT.replace("PrivilegesRequired=lowest",
                                                 "PrivilegesRequired=admin")
            miss = self.MF_CLEAN_SCRIPT.replace('Source: "zaojing.exe"',
                                                'Source: "no-such.exe"')
            seams = ("_token_elevation", "_disk_free", "_exclusive_open", "_write_probe")
            saved = {n: getattr(m, n) for n in seams}
            TOK_UP = {"available": True, "elevated": True, "elevation_type": 1,
                      "is_admin_account": True, "reason": None, "basis": "eval 显式桩"}
            TOK_DOWN = {"available": True, "elevated": False, "elevation_type": 1,
                        "is_admin_account": True, "reason": None, "basis": "eval 显式桩"}
            TOK_NONE = {"available": None, "elevated": None, "elevation_type": None,
                        "is_admin_account": None, "reason": "stub err=24", "basis": "eval 显式桩"}

            def restore_tok():
                m._token_elevation = lambda: dict(TOK_UP)

            def brief(p):
                return {"verdict": p["verdict"], "blocking": p["blocking_codes"],
                        "unknown": p["unknown_codes"]}

            seen = {}
            try:
                restore_tok()
                base = m.preflight_install(self.MF_CLEAN_SCRIPT, str(sd), dr,
                                           evidence=ev, target_override=tgt)
                seen["base"] = brief(base)
                # 真接缝臂（无需桩）：目标为同名文件 / 载荷缺失 / 证据缺席 / 证据被篡改
                pf = m.preflight_install(self.MF_CLEAN_SCRIPT, str(sd), dr, evidence=ev,
                                         target_override=str(as_file))
                seen["file_target"] = brief(pf)
                seen["file_target_write"] = [pf["checks"]["writability"]["result"],
                                             pf["checks"]["writability"]["probed"],
                                             pf["checks"]["writability"]["detail"]["reason"],
                                             pf["checks"]["target_conflict"]["detail"]
                                             ["exists_as_file"]]
                pm = m.preflight_install(miss, str(sd), dr, evidence=ev, target_override=tgt)
                seen["miss_payload"] = brief(pm)
                pm2 = m.preflight_install(miss, str(sd), dr, evidence=ev)
                seen["miss_payload_beats_unknown"] = brief(pm2)
                pnone = m.preflight_install(self.MF_CLEAN_SCRIPT, str(sd), dr,
                                            evidence=None, target_override=tgt)
                seen["no_evidence"] = brief(pnone)
                seen["no_evidence_state"] = [pnone["checks"]["artifact"]["state"],
                                             pnone["checks"]["artifact"]["verdict"]]
                bad = dict(ev, artifact_sha256="0" * 64)
                ptm = m.preflight_install(self.MF_CLEAN_SCRIPT, str(sd), dr,
                                          evidence=bad, target_override=tgt)
                seen["tampered"] = brief(ptm)
                seen["tampered_state"] = [ptm["checks"]["artifact"]["state"],
                                          ptm["checks"]["artifact"]["verdict"]]
                # 桩 1/2：磁盘余量读不到 → unknown；余量 10 字节 → blocked
                m._disk_free = lambda path: None
                seen["free_none"] = brief(m.preflight_install(self.MF_CLEAN_SCRIPT, str(sd),
                                                              dr, evidence=ev,
                                                              target_override=tgt))
                m._disk_free = lambda path: 10
                seen["free_tiny"] = brief(m.preflight_install(self.MF_CLEAN_SCRIPT, str(sd),
                                                              dr, evidence=ev,
                                                              target_override=tgt))
                m._disk_free = saved["_disk_free"]
                # 桩 3：目标 exe 独占打开三态（err=2 视为未被占用，与编译器无关）
                for tag, res in (("held32", (False, 32)), ("miss2", (False, 2)),
                                 ("free0", (True, 0))):
                    m._exclusive_open = lambda path, _r=res: _r
                    seen["exe_" + tag] = brief(m.preflight_install(
                        self.MF_CLEAN_SCRIPT, str(sd), dr, evidence=ev,
                        target_override=str(hold)))
                m._exclusive_open = saved["_exclusive_open"]
                seen["exe_real"] = brief(m.preflight_install(
                    self.MF_CLEAN_SCRIPT, str(sd), dr, evidence=ev,
                    target_override=str(hold)))
                # 桩 4：令牌两态（未提权 → 阻断；探测不到 → unknown 而非阻断）
                for tag, tok in (("elev_false", TOK_DOWN), ("unresolved", TOK_NONE)):
                    m._token_elevation = (lambda _t=tok: dict(_t))
                    seen["admin_" + tag] = brief(m.preflight_install(admin, str(sd), dr,
                                                                      evidence=ev,
                                                                      target_override=tgt))
                restore_tok()
                # 桩 5：写探针三种归因（建不出目录 / 建得出写不进 / 不可归因）
                for tag, ret in (("mkdir", (False, {"os_error": 5,
                                                    "exception": "PermissionError",
                                                    "mkdir_failed": True,
                                                    "detail": "stub"})),
                                 ("write", (False, {"os_error": 5,
                                                    "exception": "PermissionError",
                                                    "mkdir_failed": False,
                                                    "detail": "stub"})),
                                 ("none", (None, {"reason": "stub_unresolved"}))):
                    m._write_probe = (lambda d, _r=ret: (_r[0], dict(_r[1])))
                    seen["probe_" + tag] = brief(m.preflight_install(
                        self.MF_CLEAN_SCRIPT, str(sd), dr, evidence=ev,
                        target_override=str(sd / ("wp-" + tag))))
                m._write_probe = saved["_write_probe"]
                seen["restore"] = brief(m.preflight_install(self.MF_CLEAN_SCRIPT, str(sd),
                                                            dr, evidence=ev,
                                                            target_override=tgt))
            finally:
                for n in seams:
                    setattr(m, n, saved[n])
            ok = (
                seen["base"] == {"verdict": "preflight_ready", "blocking": [], "unknown": []}
                # 目标是同名文件：阻断且写探针不适用（归因清楚，不混为不可写）
                and seen["file_target"] == {"verdict": "preflight_blocked",
                                            "blocking": ["target_exists_as_file"],
                                            "unknown": []}
                and seen["file_target_write"] == [None, False, "target_exists_as_file", True]
                # 载荷缺失
                and seen["miss_payload"] == {"verdict": "preflight_blocked",
                                             "blocking": ["payload_source_missing"],
                                             "unknown": []}
                # 优先级：有阻断项时即便同时有未探测项也判 blocked
                and seen["miss_payload_beats_unknown"] == {
                    "verdict": "preflight_blocked",
                    "blocking": ["payload_source_missing"],
                    "unknown": ["write_probe_unprobed"]}
                # 无证据 / 证据被篡改
                and seen["no_evidence"]["blocking"] == ["artifact_missing"]
                and seen["no_evidence_state"] == ["not_supplied", None]
                and seen["tampered"]["blocking"] == ["artifact_not_verified"]
                and seen["tampered_state"] == ["not_verified", "evidence_mismatch"]
                # 磁盘两态
                and seen["free_none"] == {"verdict": "preflight_unknown", "blocking": [],
                                          "unknown": ["disk_free_unresolved"]}
                and seen["free_tiny"]["blocking"] == ["space_insufficient"]
                and seen["free_tiny"]["verdict"] == "preflight_blocked"
                # 占用三态 + 真接缝回归
                and seen["exe_held32"]["blocking"] == ["target_exe_in_use"]
                and seen["exe_held32"]["verdict"] == "preflight_blocked"
                and seen["exe_miss2"] == seen["base"] and seen["exe_free0"] == seen["base"]
                and seen["exe_real"] == seen["base"]
                # 权限两态：未提权阻断，探测不到只进 unknown（三态原则）
                and seen["admin_elev_false"] == {"verdict": "preflight_blocked",
                                                 "blocking": ["privileges_insufficient"],
                                                 "unknown": []}
                and seen["admin_unresolved"] == {"verdict": "preflight_unknown",
                                                 "blocking": [],
                                                 "unknown": ["elevation_state_unresolved"]}
                # 写探针三种归因
                and seen["probe_mkdir"]["blocking"] == ["cannot_create_target"]
                and seen["probe_write"]["blocking"] == ["target_not_writable"]
                and seen["probe_none"] == {"verdict": "preflight_unknown", "blocking": [],
                                           "unknown": ["write_probe_unresolved"]}
                # 还原后回基线 + 四路接缝全还原
                and seen["restore"] == seen["base"]
                and all(getattr(m, n) is saved[n] for n in seams)
                and os.path.exists(str(sd / "wp-mkdir")) is False
                and os.path.exists(str(sd / "wp-none")) is False)
            return ok, {"expected": "预检面阻断/未探测十臂：同名文件占位、载荷缺失（先阻断后未知）"
                                    "、证据缺席、证据篡改、余量读不到/不足、目标 exe 占用三态、"
                                    "令牌未提权与探测不到、写探针三种归因；桩全部还原并回基线",
                        "actual": seen}

        def mf_static_p53_fire_arms(E):
            m = E["installer_manifest"]
            dr = str(self.store_dir / "_manifest" / "fire_data")
            reps = {}
            for tag, fix in (("r6", self.MF_R6_UNESC_SCRIPT),
                             ("r12", self.MF_R12_ARCH_SCRIPT),
                             ("r13no", self.MF_R13_NOVER_SCRIPT),
                             ("r13vn", self.MF_R13_VERNAME_SCRIPT),
                             ("r11fire", self.MF_R11_FIRE_SCRIPT),
                             ("r11ok", self.MF_R11_OK_SCRIPT)):
                sd = self._manifest_sandbox("fire_" + tag)
                reps[tag] = m.verify_script(fix, str(sd), dr)
            # 合规侧对照（同一 data_root，载荷齐备以走 R2"存在"分支）
            sd_ok = self._manifest_sandbox("fire_clean")
            self._manifest_payloads(sd_ok)
            clean = m.verify_script(self.MF_CLEAN_SCRIPT, str(sd_ok), dr)
            one = lambda rep: rep["findings"][0] if len(rep["findings"]) == 1 else None
            r6, r13, f11 = one(reps["r6"]), one(reps["r13no"]), one(reps["r11fire"])
            arch = {f["evidence"]["directive"]: f for f in reps["r12"]["findings"]}
            ok = (
                # R6 转义臂：单写首括号 → error（真 iscc 实测 exit=2 Unknown constant）
                reps["r6"]["verdict"] == "manifest_invalid"
                and reps["r6"]["counts"] == {"errors": 1, "warnings": 0, "infos": 0, "total": 1}
                and r6["id"] == "appid_guid_unescaped" and r6["severity"] == "error"
                and r6["subject"] == self.MF_APPID
                and r6["evidence"]["line"] == 4
                and r6["evidence"]["raw_value"] == self.MF_APPID
                and r6["evidence"]["expected_form"] == self.MF_APPID_SCRIPT_FORM
                and r6["evidence"]["compiler_proven"] is True
                # 键名派生与转义状态无关（写错的脚本由 R6 报 error，键名照样算得出）
                and m.uninstall_registry_key(self.MF_APPID) == m.uninstall_registry_key(
                    self.MF_APPID_SCRIPT_FORM)
                # R12 弃用标识：两条 directive 各自独立开火，severity 与编译器一致
                and reps["r12"]["verdict"] == "manifest_warning"
                and reps["r12"]["counts"] == {"errors": 0, "warnings": 2, "infos": 0, "total": 2}
                and sorted(arch) == ["architecturesallowed", "architecturesinstallin64bitmode"]
                and [arch["architecturesallowed"]["evidence"]["line"],
                     arch["architecturesinstallin64bitmode"]["evidence"]["line"]] == [7, 8]
                and [arch["architecturesallowed"]["subject"],
                     arch["architecturesinstallin64bitmode"]["subject"]] == [
                    "ArchitecturesAllowed", "ArchitecturesInstallIn64BitMode"]
                and all(f["id"] == "arch_identifier_deprecated" and f["severity"] == "warn"
                        and f["evidence"]["offending_tokens"] == ["x64"]
                        and f["evidence"]["preferred"] == "x64compatible"
                        and f["evidence"]["declared_value"] == "x64"
                        and f["evidence"]["iscc_observed"] == m.ISCC_ARCH_WARNING
                        for f in reps["r12"]["findings"])
                # 整词比较：合规写法 x64compatible / x64os 不被子串冤枉
                and "arch_identifier_deprecated" not in [f["id"] for f in clean["findings"]]
                and "appid_guid_unescaped" not in [f["id"] for f in clean["findings"]]
                and "setup_missing_version_directive" not in [f["id"] for f in clean["findings"]]
                and "per_user_area_privileges_mismatch" not in [f["id"] for f in clean["findings"]]
                and clean["findings"] == [] and clean["verdict"] == "manifest_ok"
                # R13：AppVersion 与 AppVerName 二选一即合规，缺两者为 error（编译器 exit=2）
                and reps["r13no"]["verdict"] == "manifest_invalid"
                and r13["id"] == "setup_missing_version_directive"
                and r13["severity"] == "error"
                and r13["evidence"]["appversion_present"] is False
                and r13["evidence"]["appvername_present"] is False
                and r13["evidence"]["either_suffices"] is True
                and r13["evidence"]["iscc_exit_code_observed"] == 2
                and r13["evidence"]["iscc_observed"] == m.ISCC_MISSING_VERSION_ERROR
                and reps["r13vn"]["findings"] == []
                and reps["r13vn"]["verdict"] == "manifest_ok"
                # R11 配对：唯一变量是一行 PrivilegesRequired，检出随之归零
                and reps["r11fire"]["counts"] == {"errors": 0, "warnings": 1, "infos": 0,
                                                  "total": 1}
                and reps["r11fire"]["verdict"] == "manifest_warning"
                and f11["id"] == "per_user_area_privileges_mismatch"
                and f11["severity"] == "warn"
                and f11["evidence"]["line"] == 0
                and f11["evidence"]["privileges_required_declared"] is None
                and f11["evidence"]["privileges_required_effective"] == "admin"
                and f11["evidence"]["default_when_absent"] == "admin"
                and f11["evidence"]["per_user_areas_observed"] == ["HKCU"]
                and f11["evidence"]["per_user_area_sites"] == [
                    {"area": "HKCU", "sites": [{"line": 7, "section": "Registry"}]}]
                and f11["evidence"]["iscc_observed"] == m.ISCC_PRIVILEGES_WARNING
                and reps["r11ok"]["findings"] == [] and reps["r11ok"]["verdict"] == "manifest_ok"
                and self.MF_R11_OK_SCRIPT.splitlines()[5] == "PrivilegesRequired=lowest"
                # counts 与 findings 自洽（verdict 不是硬编码）
                and all(r["counts"]["total"] == len(r["findings"]) for r in reps.values()))
            return ok, {"expected": "四条编译器定论臂各自单独开火：R6 单写括号 error（双写形制"
                                    "回显 + 键名派生与转义无关）、R12 两条 directive 各出一条 warn"
                                    "（整词比较，x64compatible 零误报）、R13 缺版本形制 error "
                                    "（AppVerName 单独在场即合规）、R11 per-user 区 × 缺省 admin "
                                    "配对 warn（补 lowest 归零，唯一变量）；counts 与 findings 自洽",
                        "actual": {k: [v["verdict"], v["counts"],
                                        [(f["id"], f["severity"], f["evidence"].get("line"))
                                         for f in v["findings"]]]
                                   for k, v in reps.items()}
                        | {"clean": [clean["verdict"], clean["counts"]]}}

        def mf_registry_uninstall_arms(E):
            """R14 七臂（Phase 58）：写入型 [Registry] × 卸载撤销声明的判定面。

            立项依据不是 Inno 文档而是**本机真卸载实测**：卸载器 exit 0、安装根与卸载子键
            双双消失、四棵目录树归零、六枚既有数据文件逐字节未动，而应用键的三枚值原样
            留守（执行器据此判 uninstall_incomplete 并轮询满 180 s）。因此每一臂都必须在
            同一枚夹具上同时给出"报/不报"与"为什么"，只数条数不算证明规则活着。
            """
            m = E["installer_manifest"]
            dr = str(self.store_dir / "_manifest" / "r14_data")
            reps = {}
            for tag, fix in (("residue", self.MF_R14_RESIDUE_SCRIPT),
                             ("key", self.MF_R14_KEY_SCRIPT),
                             ("parent_delkey", self.MF_R14_PARENT_DELKEY_SCRIPT),
                             ("parent_ifempty", self.MF_R14_PARENT_IFEMPTY_SCRIPT),
                             ("ifempty_same_key", self.MF_R14_IFEMPTY_SAME_KEY_SCRIPT),
                             ("never", self.MF_R14_NEVER_SCRIPT),
                             ("cross_root", self.MF_R14_CROSS_ROOT_SCRIPT)):
                reps[tag] = m.verify_script(fix, str(self.store_dir), dr)
            rid = lambda tag: {f["id"]: f for f in reps[tag]["findings"]}
            fire = lambda tag: rid(tag).get("registry_key_left_after_uninstall")
            ev_res = fire("residue")["evidence"]
            ev_pie = fire("parent_ifempty")["evidence"]
            ev_ise = fire("ifempty_same_key")["evidence"]
            ev_cr = fire("cross_root")["evidence"]
            ok = (
                # 臂一：无撤销声明 ⇒ 唯一一条 error，逐字段（两行写入归并到同一组）
                reps["residue"]["verdict"] == "manifest_invalid"
                and reps["residue"]["counts"] == {"errors": 1, "warnings": 0, "infos": 0,
                                                  "total": 1}
                and fire("residue")["severity"] == "error"
                and fire("residue")["subject"] == "HKCU\\Software\\Zaojing\\ZaojingAI"
                and ev_res["root"] == "hkcu" and ev_res["line"] == 8
                and ev_res["write_lines"] == [8, 9]
                and ev_res["value_names"] == ["InstallLocation", "Version"]
                and ev_res["observed_cleanup_flags"] == []
                # 臂二/三/四：发货形制、祖先整键删、显式让渡各自归零（漏一个就是假红）
                and reps["key"]["findings"] == [] and reps["key"]["verdict"] == "manifest_ok"
                and reps["parent_delkey"]["findings"] == []
                and reps["parent_delkey"]["verdict"] == "manifest_ok"
                and reps["never"]["findings"] == [] and reps["never"]["verdict"] == "manifest_ok"
                # 臂五：父键只 ifempty 顶不住带值子键 ⇒ 报，且如实回显"看见过哪枚旗标"
                and reps["parent_ifempty"]["counts"]["errors"] == 1
                and ev_pie["subkey"] == "Software\\Zaojing\\ZaojingAI"
                and ev_pie["observed_cleanup_flags"] == ["uninsdeletekeyifempty"]
                # 臂六：ifempty 写在应用键自身也一样顶不住 ⇒ 不得被"有旗标"洗白
                and reps["ifempty_same_key"]["counts"]["errors"] == 1
                and ev_ise["observed_cleanup_flags"] == ["uninsdeletekeyifempty"]
                and "uninsdeletekeyifempty" not in ev_ise["accepted_flags"]
                and ev_ise["accepted_flags"] == ["uninsdeletekey", "uninsdeletevalue"]
                # 臂七：撤销挂在 HKLM、写入在 HKCU ⇒ 两把不同的钥匙，照报
                and reps["cross_root"]["counts"]["errors"] == 1
                and ev_cr["root"] == "hkcu"
                and ev_cr["observed_cleanup_flags"] == []
                # 三枚"报"的臂都带回真机量测凭证（判据出处可核，不是文档抄来的）
                and all(fire(t)["evidence"]["live_measurement"].startswith(
                            "Phase 58 本机真卸载")
                        for t in ("residue", "parent_ifempty", "ifempty_same_key",
                                  "cross_root"))
                # 唯一变量定律：residue 与 key 只差 [Registry] 段的撤销声明，其余逐字相同
                and self.MF_R14_RESIDUE_SCRIPT.splitlines()[:6] \
                    == self.MF_R14_KEY_SCRIPT.splitlines()[:6]
                and all(r["counts"]["total"] == len(r["findings"]) for r in reps.values()))
            return ok, {"expected": "R14 七臂各自单独开火/归零：无撤销声明 error（多行写入归并"
                                    "一组、值名逐枚回显）、发货形制与祖先 uninsdeletekey 与"
                                    "显式 uninsneveruninstall 三臂零检出、父键 ifempty 与同键 "
                                    "ifempty 两臂不得洗白（accepted 集合里就没有它）、跨 root "
                                    "声明不算覆盖；每条 error 携带本机真卸载量测凭证",
                        "actual": {k: [v["verdict"], v["counts"],
                                        [(f["id"], f["severity"],
                                          f["evidence"]["write_lines"],
                                          f["evidence"]["observed_cleanup_flags"])
                                         for f in v["findings"]]]
                                   for k, v in reps.items()}}

        def k_manifest_build_wrong_expect(E):
            # 真相：编译成功由三独立信号同立，证据自报的 compiled 布尔不参与判定（本轮根除的
            # 正是把自报/探针伪影写成环境事实）；此处故意期望"自报 compiled=False 即判
            # compile_not_proven"，诚实判分必红
            m = E["installer_manifest"]
            sd, sp, ap = self._manifest_build_sandbox("calibbuild")
            ev = self._mf_evidence(sp, ap)
            lie = m.verify_build_evidence(dict(ev, compiled=False, fabricated=True),
                                          str(sp), str(ap),
                                          str(self.store_dir / "_manifest" / "cb_data"))
            ok = lie["verdict"] == "compile_not_proven"
            return ok, {"note": "校准用例：假期望把证据自报 compiled=False 当成编译未证成的判据",
                        "actual": {"verdict": lie["verdict"],
                                   "self_report": [lie["evidence_self_report"]["compiled"],
                                                   lie["evidence_self_report"]["fabricated"],
                                                   lie["evidence_self_report"]["ignored"]]}}

        def k_manifest_wrong_expect(E):
            # 真相：{tmp} 预建目录是 Inno 合法常量但无环境映射、永不解析，R7 只判 warn；
            # 此处故意期望它判 error（把"预建目录与数据根无交集"当成硬失败），诚实判分必红
            m = E["installer_manifest"]
            sd = self._manifest_sandbox("calibwrong")
            data_root = str(self.store_dir / "_manifest" / "calibwrong_data" / "ZaojingAI")
            rep = m.verify_script(self.MF_BROKEN_SCRIPT, str(sd), data_root)
            got = [(f["id"], f["severity"]) for f in rep["findings"]
                   if f["id"] == "dirs_miss_runtime_data_root"]
            ok = got == [("dirs_miss_runtime_data_root", "error")]
            return ok, {"note": "校准用例：假期望把 R7 预建目录无交集判成 error",
                        "actual": got}

        # ==================================================================
        # Phase 59 路线 B：第 22 引擎的**守护面**升为独立黄金域 runtime_guard
        # （5 条真值 + 1 条自有校准）。期望逐字来自只读探针 p59_det_probe.py v2 ——
        # 该探针对每一面跑三轮、把沙箱绝对路径/随机锁名/随机端口/PID/磁盘读数归一后
        # 做**全字段** deepdiff，五面 diff_count 恒为 0 才有本节用例（v1 的分类器把
        # 真值漂移误分成易变字段、自造假绿，已整体作废）。故此处期望源自实测，不来自手抄。
        # 夹具纪律：端口一律评测自起 bind(0)（占用臂 listen 后必在 finally 里 close），
        # 绝不触碰外部 socket；锁名逐用例随机（uuid 尾段），只作非判分字段归一为 <LOCK>。
        # ==================================================================
        GUARD_ERR_MIN = "RuntimeGuardError|min_free_bytes 不能为负"
        GUARD_ERR_PORT = "RuntimeGuardError|port 需为 1~65535 的整数"
        GUARD_ERR_NAME = "RuntimeGuardError|instance_name 含路径/保留字符，拒绝"

        def _guard_norm(text, sb, lock=None):
            """把逐轮必变量归一成符号：沙箱根→<SB>、随机锁名→<LOCK>、反斜杠→正斜杠。
            与 Phase 50 的 _hub_norm 同法——归一后才可能"两次运行逐字段全等"。"""
            t = str(text).replace("\\", "/")
            t = t.replace(str(sb).replace("\\", "/"), "<SB>")
            if lock:
                t = t.replace(lock, "<LOCK>")
            return t

        def _guard_ports():
            """评测自起的一枚占用端口与一枚已关闭端口；调用方必 close 返回的套接字。"""
            import socket as _sk
            occ = _sk.socket(_sk.AF_INET, _sk.SOCK_STREAM)
            occ.bind(("127.0.0.1", 0))
            occ.listen(1)
            free = _sk.socket(_sk.AF_INET, _sk.SOCK_STREAM)
            free.bind(("127.0.0.1", 0))
            port = free.getsockname()[1]
            free.close()
            return occ, occ.getsockname()[1], port

        def gr_write_probe_three_arms(E):
            m = E["installer_runtime"]
            sb = self._guard_sandbox("write")
            eng = m.RuntimeGuardEngine(data_root=sb)
            ok_dir = sb / "probe-ok"
            afile = sb / "afile"
            afile.write_bytes(b"not-a-directory")
            a = eng.probe_write(ok_dir)                     # 可写臂
            b = eng.probe_write(str(afile))                 # 目标本身是普通文件
            c = eng.probe_write(str(afile / "sub"))         # 父路径是普通文件
            probe_keys = ("writable", "bytes_verified", "cleaned", "method", "fabricated")
            actual = {
                "writable_dir": {k: a[k] for k in probe_keys},
                # 两枚失败臂只回显三枚布尔：method/fabricated 照样在 ok 里判，只是不占判分字段
                "target_is_file": {k: b[k] for k in ("writable", "bytes_verified", "cleaned")},
                "parent_is_file": {k: c[k] for k in ("writable", "bytes_verified", "cleaned")},
                "target_err_type": (b["error"] or "").split(":")[0],
                "parent_err_type": (c["error"] or "").split(":")[0],
                "residue_probe_file_count": len(list(ok_dir.glob("zj_write_probe_*"))),
                "sb_probe_files": len(list(sb.rglob("zj_write_probe_*"))),
                "dir_created": ok_dir.is_dir(),
                "target_norm": _guard_norm(a["target"], sb),
            }
            ok = (
                actual["writable_dir"] == {"writable": True, "bytes_verified": True,
                                           "cleaned": True,
                                           "method": "real_write_readback_delete",
                                           "fabricated": False}
                and actual["target_is_file"] == {"writable": False, "bytes_verified": False,
                                                 "cleaned": False}
                and b["method"] == "real_write_readback_delete" and b["fabricated"] is False
                and actual["parent_is_file"] == {"writable": False, "bytes_verified": False,
                                                 "cleaned": False}
                and c["method"] == "real_write_readback_delete" and c["fabricated"] is False
                # 两侧同写：可写臂必真、两枚敌意臂必假且错误类型逐字对账
                and actual["target_err_type"] == "FileExistsError"
                and actual["parent_err_type"] == "FileExistsError"
                and actual["residue_probe_file_count"] == 0
                and actual["sb_probe_files"] == 0
                and actual["dir_created"] is True
                and actual["target_norm"] == "<SB>/probe-ok")
            return ok, {"expected": "可写臂 writable/bytes_verified/cleaned 三真且 method/fabricated "
                                    "如实；目标为普通文件与父路径为普通文件两臂三假且 error 起手 "
                                    "FileExistsError；探针文件零残留；目录由探针真建；target 归一 "
                                    "<SB>/probe-ok",
                        "actual": actual}

        def gr_disk_threshold_two_sided(E):
            m = E["installer_runtime"]
            sb = self._guard_sandbox("disk")
            eng = m.RuntimeGuardEngine(data_root=sb)
            lo = eng.disk_space(eng.data_root, 0)
            hi = eng.disk_space(eng.data_root, 2 ** 62)
            err = None
            try:
                eng.disk_space(eng.data_root, -1)
            except Exception as e:
                err = "%s|%s" % (type(e).__name__, e)
            actual = {"min_zero_meets": lo["meets_min"], "min_huge_meets": hi["meets_min"],
                      "total_positive": lo["total_bytes"] > 0, "method": lo["method"],
                      "fabricated": lo["fabricated"], "negative_raises": err,
                      "min_echo": [lo["min_free_bytes"], hi["min_free_bytes"]]}
            # free_bytes/used_bytes 是本机连续变化量，一律不进判分字段（只钉派生布尔与阈值回显）
            ok = (actual["min_zero_meets"] is True and actual["min_huge_meets"] is False
                  and actual["total_positive"] is True
                  and actual["method"] == "shutil_disk_usage"
                  and actual["fabricated"] is False
                  and actual["negative_raises"] == GUARD_ERR_MIN
                  and hi["method"] == "shutil_disk_usage" and hi["fabricated"] is False
                  and actual["min_echo"] == [0.0, float(2 ** 62)])
            return ok, {"expected": "min=0 必 meets_min=True、min=2**62 必 False、负阈值抛点名错误、"
                                    "阈值逐字回显；连续量不入判分",
                        "actual": actual}

        def gr_port_bind_probe_two_sided(E):
            m = E["installer_runtime"]
            sb = self._guard_sandbox("port")
            eng = m.RuntimeGuardEngine(data_root=sb)
            occ, occ_port, free_port = _guard_ports()
            try:
                free = eng.check_port(free_port)
                busy = eng.check_port(occ_port)
            finally:
                occ.close()
            oob = None
            try:
                eng.check_port(70000)
            except Exception as e:
                oob = "%s|%s" % (type(e).__name__, e)
            actual = {"free_available": free["available"], "occ_available": busy["available"],
                      "free_detail_none": free["detail"] is None,
                      "occ_detail_is_oserror": bool(busy["detail"]) and "Error" in busy["detail"],
                      "occ_detail_type": (busy["detail"] or "").split(":")[0],
                      "method": free["method"], "fabricated": free["fabricated"],
                      "out_of_range_raises": oob,
                      "port_echo": [free["port"], busy["port"]]}
            ok = (actual["free_available"] is True and actual["occ_available"] is False
                  and actual["free_detail_none"] is True
                  and actual["occ_detail_is_oserror"] is True
                  and actual["occ_detail_type"] == "OSError"
                  and busy["method"] == "real_tcp_bind_probe" and busy["fabricated"] is False
                  and actual["out_of_range_raises"] == GUARD_ERR_PORT
                  # 端口号只作结构核对（逐轮随机，绝不当判分内容——探针同法剔除）
                  and actual["port_echo"] == [free_port, occ_port])
            return ok, {"expected": "自起监听桩的端口必 available=False 且 detail 带 OS 错误原文；"
                                    "OS 指派后自关的端口必 available=True 且 detail=None；"
                                    "越界入参抛点名错误；探针文件与本面夹具零外部 socket",
                        "actual": actual}

        def gr_single_instance_mutex_chain(E):
            import uuid as _uuid
            m = E["installer_runtime"]
            sb = self._guard_sandbox("mutex")
            name = "zj-p59-guard-" + _uuid.uuid4().hex[:10]
            eng = m.RuntimeGuardEngine(data_root=sb)
            other = m.RuntimeGuardEngine(data_root=sb)
            rows = {}
            try:
                a1 = eng.acquire_single_instance(name)
                a2 = eng.acquire_single_instance(name)
                rl = eng.release_single_instance(name)
                a3 = eng.acquire_single_instance(name)
                a4 = other.acquire_single_instance(name)
                rl2 = other.release_single_instance(name)
                bad = None
                try:
                    eng.acquire_single_instance("a/b")
                except Exception as e:
                    bad = "%s|%s" % (type(e).__name__, e)
            finally:
                # 无论判分成败都把本进程持有的句柄收回，绝不把命名互斥体留给后续用例
                eng.release_single_instance(name)
            pair = lambda r: [r["acquired"], r["already_running"]]  # noqa: E731
            actual = {"first": pair(a1), "second_same_instance": pair(a2),
                      "released": rl["released"],
                      "after_release_same_instance": pair(a3),
                      "after_release_other_instance": pair(a4),
                      "other_release": rl2["released"],
                      "backend": a1["backend"], "fabricated": a1["fabricated"],
                      "name_echo": _guard_norm(a1["name"], sb, name),
                      "bad_name_rejected": bad,
                      "release_repeat": eng.release_single_instance(name)["released"]}
            ok = (actual["first"] == [True, False]
                  and actual["second_same_instance"] == [False, True]
                  and actual["released"] is True
                  and actual["after_release_same_instance"] == [True, False]
                  and actual["after_release_other_instance"] == [False, True]
                  # 句柄不属它就不许谎称释放成功——released 必 False
                  and actual["other_release"] is False
                  and actual["backend"] == "kernel32-named-mutex"
                  and actual["fabricated"] is False
                  and actual["name_echo"] == "<LOCK>"
                  and actual["bad_name_rejected"] == GUARD_ERR_NAME
                  and actual["release_repeat"] is False
                  and all(r["backend"] == "kernel32-named-mutex" for r in (a2, a3, a4))
                  and rl["fabricated"] is False and rl2["fabricated"] is False)
            return ok, {"expected": "五步链逐值：[T,F]→[F,T]→release True→[T,F]→另一实例 [F,T] "
                                    "且它的 released=False；非法锁名抛点名错误",
                        "actual": actual}

        def gr_diagnostic_derived_and_degrades(E):
            m = E["installer_runtime"]
            sb = self._guard_sandbox("diag")
            eng = m.RuntimeGuardEngine(data_root=sb)
            base = eng.run_diagnostic(None)
            occ, occ_port, free_port = _guard_ports()
            try:
                withport = eng.run_diagnostic(free_port)
            finally:
                occ.close()
            sb2 = self._guard_sandbox("diag-degraded")
            blocker = sb2 / "blocker"
            blocker.write_bytes(b"not-a-directory")
            bad = m.RuntimeGuardEngine(data_root=str(blocker / "deep")).run_diagnostic()
            # 构造本身不建根那一臂：根必须"不存在→交给探针"，探针真建后才存在
            fresh = self._guard_sandbox("diag-fresh", keep=True)
            eng3 = m.RuntimeGuardEngine(data_root=fresh)
            exists_after_ctor = fresh.exists()
            first3 = eng3.run_diagnostic(None)
            exists_after_probe = fresh.exists()
            names = lambda d: [[c["check"], c["status"]] for c in d["checks"]]  # noqa: E731
            actual = {
                "checks": names(base), "counts": [base["checks_performed"],
                                                  base["checks_passed"], base["checks_failed"]],
                "status": base["status"], "method": base["method"],
                "fabricated": base["fabricated"], "timestamp_none": base["timestamp"] is None,
                "with_port_checks": names(withport),
                "with_port_count": withport["checks_performed"],
                "with_port_status": withport["status"],
                "degraded_checks": names(bad), "degraded_status": bad["status"],
                "degraded_timestamp_none": bad["timestamp"] is None,
                "failed_eq_derived": bad["checks_failed"] == (bad["checks_performed"]
                                                              - bad["checks_passed"]),
                "dir_exists_first_vs_second": [base["system"]["data_root_exists"],
                                               withport["system"]["data_root_exists"]],
                "dir_exists_before_vs_after": [exists_after_ctor, exists_after_probe],
                "fresh_first_snapshot": first3["system"]["data_root_exists"],
                "residue": sorted(p.name for p in sb.rglob("*")),
                "residue_probe_files": len(list(sb.rglob("zj_write_probe_*"))),
            }
            healthy = [["data_root_writable", "pass"], ["disk_free", "pass"],
                       ["python_version", "pass"]]
            ok = (actual["checks"] == healthy
                  and actual["counts"] == [3, 3, 0]
                  and actual["status"] == "healthy"
                  and actual["method"] == "derived_from_real_checks"
                  and actual["fabricated"] is False
                  and actual["timestamp_none"] is True
                  and actual["with_port_checks"] == healthy + [["port_available", "pass"]]
                  and actual["with_port_count"] == 4
                  and actual["with_port_status"] == "healthy"
                  and actual["degraded_checks"] == [["data_root_writable", "fail"],
                                                    ["disk_free", "pass"],
                                                    ["python_version", "pass"]]
                  and actual["degraded_status"] == "degraded"
                  and actual["degraded_timestamp_none"] is True
                  and actual["failed_eq_derived"] is True
                  and actual["dir_exists_first_vs_second"] == [True, True]
                  # 两侧同写：引擎构造不建根（False），探针跑完才建出（True）
                  and actual["dir_exists_before_vs_after"] == [False, True]
                  and actual["fresh_first_snapshot"] is False
                  and actual["residue"] == [".runtime-guard"]
                  and actual["residue_probe_files"] == 0)
            return ok, {"expected": "健康臂三项全 pass 且计数 [3,3,0]、status=healthy、"
                                    "timestamp 刻意为 None；带端口臂四项含 port_available；"
                                    "降级臂 data_root_writable=fail 且 status=degraded 且 "
                                    "checks_failed 恒为 performed-passed 的派生式；构造不建根而探针建根；"
                                    "残留只有 .runtime-guard 一枚目录、探针文件计数为 0",
                        "actual": actual}

        def k_guard_wrong_expect(E):
            # 真相：data_root 落在普通文件之下时写探针必失败，status 由逐项检查**派生**成
            # degraded；此处故意把降级臂期望写成 healthy（把"写不进去"当成"写得进去"），
            # 诚实判分必红——判分器若让它通过，就是在替产品说话。
            m = E["installer_runtime"]
            sb = self._guard_sandbox("diagcalib")
            blocker = sb / "blocker"
            blocker.write_bytes(b"not-a-directory")
            bad = m.RuntimeGuardEngine(data_root=str(blocker / "deep")).run_diagnostic()
            ok = bad["status"] == "healthy"
            return ok, {"note": "校准用例：假期望把降级臂 status 写成 healthy",
                        "actual": {"status": bad["status"],
                                   "checks_failed": bad["checks_failed"]}}

        def k_manifest_preflight_wrong_expect(E):
            # 真相：调用方显式关闭写探针时，可写性既不能声称可写也不能声称不可写，
            # 诚实形制是 verdict=preflight_unknown 且 unknown_codes=[write_probe_unprobed]；
            # 此处故意把这一态期望写成 preflight_ready（把"没探测"当成"探测通过"），必红。
            m = E["installer_manifest"]
            sd, sp, ap = self._manifest_build_sandbox("pfc")
            ev = self._mf_evidence(sp, ap)
            dr = str(self.store_dir / "_manifest" / "pfc_data")
            real_tok = m._token_elevation
            try:
                off = m.preflight_install(self.MF_CLEAN_SCRIPT, str(sd), dr, evidence=ev,
                                          target_override=str(sd / "probe-disabled"),
                                          probe_writable=False)
            finally:
                m._token_elevation = real_tok
            ok = off["verdict"] == "preflight_ready"
            return ok, {"note": "校准用例：假期望把探针关闭的 preflight_unknown 写成 ready",
                        "actual": {"verdict": off["verdict"],
                                   "unknown_codes": off["unknown_codes"]}}

        # ==================================================================
        # Phase 50 第十一种黄金域：desktop_hub（第 27 引擎，桌面 Hub 客户端）
        # 夹具：评测自起 127.0.0.1:0 进程内真 HTTP（_P37MockHTTP，routes/seen 逐实例
        # 隔离、用毕必 shutdown）。判分字段一律把 base_url 归一成 "BASE"——端口号逐次
        # 随机，不归一就做不到"顶层/嵌套两次全等"（Phase 37 同源纪律）。
        # GUI 本体（真建窗/逐 Label 文本/invoke）刻意不进评测台：无显示会话会让评测
        # 脆断，其活体实证在 L2/L4。此处判的是"只搬运不编数"的数据层派生纪律。
        # ==================================================================
        HUB_FIX_HEALTH = {"status": "healthy", "version": "9.9.9-probe",
                          "uptime_seconds": 12.5, "mode": "portable",
                          "data_root": "R:/probe/data",
                          "engines": {"alpha": "ready", "beta": "ready",
                                      "gamma": "not loaded"}}
        HUB_FIX_CAPS = {
            "summary": {"total": 11, "supported": 7, "needs_adobe_host": 2,
                        "prototype_not_shipped": 0, "not_shipped": 2},
            "engines_loaded": ["alpha", "beta", "gamma"],
            "capabilities": [{"id": "c_ok", "status": "supported"},
                             {"id": "c_hub", "status": "supported"},
                             {"id": "c_psd", "status": "needs_adobe_host"},
                             {"id": "c_inst", "status": "not_shipped"}],
            "registry": {"capabilities": [
                {"id": "c_ok", "verified": True, "evidence_kind": "golden_cases"},
                {"id": "c_hub", "verified": True, "evidence_kind": "golden_cases"},
                {"id": "c_psd", "verified": False, "evidence_kind": "honest_boundary"},
                {"id": "c_inst", "verified": False, "evidence_kind": "honest_boundary"}]}}
        HUB_FIX_REPORTS = {"reports": [
            {"report_id": "eval-old", "executed_cases": 3, "pass_rate": 0.5,
             "honest": True, "finished_at": "2026-01-01T00:00:00"},
            {"report_id": "eval-new", "executed_cases": 9, "pass_rate": 1.0,
             "honest": True, "finished_at": "2026-02-02T00:00:00"}]}

        def _hub_mock(with_post_route=False):
            """将夹具挂到一个全新的进程内真 HTTP 服务上；调用方必 shutdown。"""
            m = _P37MockHTTP()
            m.routes["/api/v1/health"] = (200, json.dumps(HUB_FIX_HEALTH).encode(),
                                          "application/json")
            m.routes["/api/v1/capabilities"] = (200, json.dumps(HUB_FIX_CAPS).encode(),
                                                "application/json")
            m.routes["/api/v1/eval/reports"] = (200, json.dumps(HUB_FIX_REPORTS).encode(),
                                                "application/json")
            if with_post_route:
                # 故意把 POST 路由也备着：若只读路径有任何越权 POST，seen 计数仪立刻现形
                m.routes["/api/v1/eval/run"] = (200, b'{"report_id":"eval-clicked"}',
                                                "application/json")
            m.seen.clear()
            return m

        def _hub_norm(text, base):
            return text.replace(base, "BASE")

        def hb_transport_fidelity(E):
            h = E["desktop_hub"]
            m = _hub_mock()
            try:
                snap = h.hub_snapshot(m.base, timeout=5.0)
                rows = [_hub_norm(r, m.base) for r in h.render_rows(snap)]
                seen = [{"method": s["method"], "path": s["path"], "auth": s["auth"]}
                        for s in m.seen]
            finally:
                m.shutdown()
            actual = {
                "seen": seen,
                "post_count": sum(1 for s in seen if s["method"] == "POST"),
                "service": snap["service"],
                "engines": snap["engines"],
                "capabilities": snap["capabilities"],
                "evaluation_latest": snap["evaluation"]["latest"],
                "reports_on_disk": snap["evaluation"]["reports_on_disk"],
                "picked_by": snap["evaluation"]["picked_by"],
                "unavailable_fields": snap["unavailable_fields"],
                "all_sources_live": snap["all_sources_live"],
                "read_only": snap["read_only"],
                "actions_performed": snap["actions_performed"],
                "fabricated": snap["fabricated"],
                "base_url_source": snap["base_url_source"],
                "rows": rows,
            }
            ok = (
                # 三次只读 GET 的序与形：路径逐条对上 READ_SOURCES，零 POST、零鉴权头
                seen == [{"method": "GET", "path": p, "auth": ""}
                         for _, p in h.READ_SOURCES]
                and actual["post_count"] == 0
                # 服务身份逐字段搬运（None 不许出现在这里）
                and snap["service"]["version"] == "9.9.9-probe"
                and snap["service"]["status"] == "healthy"
                and snap["service"]["uptime_seconds"] == 12.5
                and snap["service"]["mode"] == "portable"
                and snap["service"]["data_root"] == "R:/probe/data"
                and snap["service"]["shape_valid"] is True
                # 引擎数由 /health 的 engines 字典真实计数（gamma 非 ready 故 2/3）
                and snap["engines"]["health_reported"] == ["alpha", "beta", "gamma"]
                and snap["engines"]["health_ready_count"] == 2
                and snap["engines"]["agree"] is True
                # 真值表与 Registry 计数是"搬运+真实计数"，非任何写死数字
                and snap["capabilities"]["summary"] == HUB_FIX_CAPS["summary"]
                and snap["capabilities"]["items_seen"] == 4
                and snap["capabilities"]["not_supported_ids"] == ["c_inst", "c_psd"]
                and snap["capabilities"]["registry_verified_count"] == 2
                and snap["capabilities"]["registry_honest_boundary_ids"] == ["c_inst",
                                                                             "c_psd"]
                # 最新报告按 max(finished_at) 挑（夹具序为 old→new，取到的是 new）
                and snap["evaluation"]["latest"] == HUB_FIX_REPORTS["reports"][1]
                and snap["evaluation"]["reports_on_disk"] == 2
                and snap["evaluation"]["picked_by"] == "max(finished_at)"
                and snap["unavailable_fields"] == []
                and snap["all_sources_live"] is True
                and snap["read_only"] is True
                and snap["actions_performed"] == [] and snap["fabricated"] is False
                and snap["base_url_source"] == "cli"
                # 窗口要显示的每一行都是快照字段的搬运，且只可能来自夹具里的值
                and rows == [
                    "Hub 目标 BASE（来源 cli）",
                    "服务 造境 AI v9.9.9-probe · healthy · 运行 12.5s",
                    "数据根 R:/probe/data",
                    "引擎 2/3 ready（/health 与 /capabilities 同源=True）",
                    "能力真值表 11 项：supported 7 / needs_adobe_host 2 / "
                    "prototype_not_shipped 0 / not_shipped 2",
                    "未 supported 的能力：c_inst、c_psd",
                    "Registry 已验证 2 项（honest_boundary 2 项）",
                    "最新评测 eval-new：9 用例执行 · pass_rate 1.0 · honest=True"])
            return ok, {"actual": actual}

        def hb_derive_not_invent(E):
            """两形"绝不编数"反证：跨源自报不一致须显形；JSON 合法但形状不合造境契约
            的 health 不得被当成连上——缺 version 时该源即判降级（P50 探针抓到并根治
            的旧缺陷：那时派生行写成"造境 AI vNone · None · 运行 Nones"）"""
            h = E["desktop_hub"]
            m = _hub_mock()
            try:
                bad = json.loads(json.dumps(HUB_FIX_CAPS))
                bad["engines_loaded"] = ["alpha", "beta"]      # 比 /health 少一项
                m.routes["/api/v1/capabilities"] = (200, json.dumps(bad).encode(),
                                                    "application/json")
                s1 = h.hub_snapshot(m.base, timeout=5.0)
                r1 = [_hub_norm(x, m.base) for x in h.render_rows(s1)]
                m.routes["/api/v1/capabilities"] = (200, json.dumps(HUB_FIX_CAPS).encode(),
                                                    "application/json")
                m.routes["/api/v1/health"] = (200, json.dumps({"status": "healthy"}).encode(),
                                              "application/json")   # 缺 version
                s2 = h.hub_snapshot(m.base, timeout=5.0)
                r2 = [_hub_norm(x, m.base) for x in h.render_rows(s2)]
            finally:
                m.shutdown()
            actual = {"agree_inconsistent": s1["engines"]["agree"],
                      "inconsistent_still_live": s1["all_sources_live"],
                      "row_engines_inconsistent": r1[3],
                      "shape_valid": s2["service"]["shape_valid"],
                      "degraded_health": s2["degraded"]["failed_sources"],
                      "unavailable_fields": s2["unavailable_fields"],
                      "service_after_bad_shape": s2["service"],
                      "rows_after_bad_shape": r2,
                      "no_none_row": not any("vNone" in x or "None ·" in x for x in r2)}
            ok = (actual["agree_inconsistent"] is False
                  and actual["inconsistent_still_live"] is True
                  and actual["row_engines_inconsistent"]
                  == "引擎 2/3 ready（/health 与 /capabilities 同源=False）"
                  and actual["shape_valid"] is False
                  and actual["degraded_health"] == ["health"]
                  and s2["all_sources_live"] is False
                  and actual["unavailable_fields"] == ["service.version", "service.status",
                                                       "service.mode", "engines",
                                                       "cross_source_engines_agree"]
                  and s2["service"]["version"] is None
                  and s2["service"]["reachable"] is True
                  and actual["no_none_row"]
                  and r2[1] == ("服务回了 JSON 但形状不合造境 health 契约（缺 "
                                "status/version），身份字段一律留空")
                  and r2[-1].startswith("降级中：health ——"))
            return ok, {"actual": actual}

        def hb_degradation_three_forms(E):
            """三种真实失败形制各有独立真值，且缺失字段一律留空不占位"""
            h = E["desktop_hub"]
            import socket as _sk
            sk = _sk.socket()
            sk.bind(("127.0.0.1", 0))
            dead_port = sk.getsockname()[1]
            sk.close()
            dead = "http://127.0.0.1:%d" % dead_port
            out = {}
            s_dead = h.hub_snapshot(dead, timeout=1.0)
            out["dead_rows"] = [_hub_norm(r, dead) for r in h.render_rows(s_dead)]
            out["dead_verdicts"] = {k: v["verdict"] for k, v in s_dead["sources"].items()}
            out["dead_error_class"] = {k: v.get("error_class")
                                       for k, v in s_dead["sources"].items()}
            out["dead_unavailable"] = s_dead["unavailable_fields"]
            out["dead_all_live"] = s_dead["all_sources_live"]
            m = _hub_mock()
            try:
                # 形制二：端口在、回的却是别的服务（HTML），且 capabilities 干脆 404，
                # 而 eval_reports 仍是真的造境载荷 —— 逐源独立降级，一个源坏不连坐
                m.routes["/api/v1/health"] = (200, b"<html>not json</html>",
                                              "text/html; charset=utf-8")
                del m.routes["/api/v1/capabilities"]
                s_html = h.hub_snapshot(m.base, timeout=5.0)
                out["html_verdict"] = s_html["sources"]["health"]["verdict"]
                out["html_transport"] = s_html["sources"]["health"]["transport"]
                out["html_caps_verdict"] = s_html["sources"]["capabilities"]["verdict"]
                out["html_caps_status"] = s_html["sources"]["capabilities"]["status"]
                out["html_reports_verdict"] = s_html["sources"]["eval_reports"]["verdict"]
                out["html_reachable"] = s_html["service"]["reachable"]
                out["html_rows"] = [_hub_norm(r, m.base) for r in h.render_rows(s_html)]
                out["html_degraded"] = s_html["degraded"]["failed_sources"]
                out["html_service_version"] = s_html["service"]["version"]
                out["html_caps_summary"] = s_html["capabilities"]["summary"]
                out["html_reports_on_disk"] = s_html["evaluation"]["reports_on_disk"]
                m.routes["/api/v1/health"] = (200, b'{"status": "healthy"',
                                              "application/json")
                m.routes["/api/v1/capabilities"] = (200, json.dumps(HUB_FIX_CAPS).encode(),
                                                    "application/json")
                s_bad = h.hub_snapshot(m.base, timeout=5.0)
                out["bad_verdict"] = s_bad["sources"]["health"]["verdict"]
                out["bad_error_class"] = s_bad["sources"]["health"]["error_class"]
                out["bad_status_field"] = s_bad["service"]["status"]
            finally:
                m.shutdown()
            prev = os.environ.get("ZAOJING_HUB_HEADLESS")
            os.environ["ZAOJING_HUB_HEADLESS"] = "1"
            try:
                rep = h.open_hub(base_url=dead, enter_mainloop=False, timeout=1.0)
                out["headless_window"] = rep["window"]
                out["headless_fabricated"] = rep["fabricated"]
                out["headless_rows_would"] = rep["window"]["rows_would_render"]
            finally:
                if prev is None:
                    os.environ.pop("ZAOJING_HUB_HEADLESS", None)
                else:
                    os.environ["ZAOJING_HUB_HEADLESS"] = prev
            ok = (out["dead_verdicts"] == {"health": "connection_failed",
                                            "capabilities": "connection_failed",
                                            "eval_reports": "connection_failed"}
                  and out["dead_error_class"] == {"health": "URLError",
                                                   "capabilities": "URLError",
                                                   "eval_reports": "URLError"}
                  and out["dead_all_live"] is False
                  and len(out["dead_unavailable"]) == 8
                  and len(out["dead_rows"]) == 3
                  and out["dead_rows"][1].startswith("连不上服务：URLError ")
                  and out["dead_rows"][2].startswith("降级中：health、capabilities、"
                                                     "eval_reports ——")
                  and out["html_verdict"] == "not_zaojing_service"
                  and out["html_transport"] == "non_json"
                  and out["html_caps_verdict"] == "not_zaojing_service"
                  and out["html_caps_status"] == 404
                  and out["html_reports_verdict"] == "zaojing_payload"
                  and out["html_reachable"] is True
                  and out["html_degraded"] == ["health", "capabilities"]
                  and out["html_service_version"] is None
                  and out["html_caps_summary"] is None
                  and out["html_reports_on_disk"] == 2
                  and out["html_rows"][1] == "端口在，但不是造境服务（not_zaojing_service）"
                  and out["html_rows"][-1].startswith("降级中：health、capabilities ——")
                  and out["bad_verdict"] == "malformed_json"
                  and out["bad_error_class"] == "JSONDecodeError"
                  and out["bad_status_field"] is None
                  and out["headless_window"]["created"] is False
                  and out["headless_window"]["reason"] == "headless_requested"
                  and out["headless_rows_would"] == len(out["dead_rows"])
                  and out["headless_fabricated"] is False)
            return ok, {"actual": out}

        def hb_action_single_post(E):
            """唯一 POST 通道：只读路径恒零 POST，显式动作恰一次 POST，未知动作抛错"""
            h = E["desktop_hub"]
            m = _hub_mock(with_post_route=True)
            err = None
            try:
                h.hub_snapshot(m.base, timeout=5.0)          # 开窗只读
                read_seen = [dict(s) for s in m.seen]
                m.seen.clear()
                act = h.perform_action(m.base, "eval_run", timeout=5.0)
                post_seen = [{"method": s["method"], "path": s["path"],
                              "body": (s.get("body") or b"").decode("utf-8", "replace")}
                             for s in m.seen]
                m.seen.clear()
                ref = h.perform_action(m.base, "refresh", timeout=5.0)
                refresh_seen = [s["method"] for s in m.seen]
                m.seen.clear()
                m.routes["/api/v1/eval/run"] = (500, b'{"detail":"boom"}',
                                                "application/json")
                act500 = h.perform_action(m.base, "eval_run", timeout=5.0)
                before_unknown = len(m.seen)
                try:
                    h.perform_action(m.base, "no_such_action", timeout=5.0)
                except h.HubError as e:
                    err = str(e)
                unknown_seen = [dict(s) for s in m.seen]
            finally:
                m.shutdown()
            actual = {"read_seen": read_seen, "post_count_on_read":
                      sum(1 for s in read_seen if s["method"] == "POST"),
                      "post_seen": post_seen,
                      "post_result": {k: v for k, v in act["results"][0].items()
                                      if k not in ("url", "elapsed_ms")},
                      "mutating": act["mutating"],
                      "refresh_methods": refresh_seen,
                      "refresh_verdicts": [r.get("verdict") for r in ref["results"]],
                      "act500": {k: v for k, v in act500["results"][0].items()
                                 if k != "url"},
                      "unknown_error": err,
                      "requests_before_unknown": before_unknown,
                      "requests_after_unknown": len(unknown_seen),
                      "actions_table": {k: {kk: v0 for kk, v0 in v.items()
                                            if kk in ("method", "paths", "mutating")}
                                        for k, v in sorted(h.ACTIONS.items())}}
            ok = (actual["read_seen"] == [{"method": "GET", "path": p, "auth": ""}
                                          for _, p in h.READ_SOURCES]
                  and actual["post_count_on_read"] == 0
                  and len(actual["post_seen"]) == 1
                  and actual["post_seen"][0] == {"method": "POST",
                                                  "path": "/api/v1/eval/run",
                                                  "body": "{}"}
                  and actual["mutating"] is True
                  and actual["post_result"]["status"] == 200
                  and actual["post_result"]["method"] == "POST"
                  and actual["refresh_methods"] == ["GET", "GET", "GET"]
                  and actual["refresh_verdicts"] == ["zaojing_payload"] * 3
                  and actual["act500"]["status"] == 500
                  and actual["act500"]["error_class"] == "HTTPError"
                  and actual["unknown_error"] == "unknown_action: 'no_such_action'"
                  and actual["requests_after_unknown"] == actual["requests_before_unknown"]
                  and actual["actions_table"] == {
                      "eval_run": {"method": "POST", "mutating": True,
                                    "paths": ["/api/v1/eval/run"]},
                      "refresh": {"method": "GET", "mutating": False,
                                   "paths": [p for _, p in h.READ_SOURCES]}})
            return ok, {"actual": actual}

        def k_hub_wrong_expect(E):
            # 真相：端口关闭时三源全 connection_failed、all_sources_live=False、缺失字段
            # 一律 None；此处故意期望"仍算连上且 registry 有 2 项"（把降级当成数据），
            # 诚实判分必红——这条校准盯的正是 Hub 最容易犯的"上一轮数字占位"诱惑
            h = E["desktop_hub"]
            import socket as _sk
            sk = _sk.socket()
            sk.bind(("127.0.0.1", 0))
            p = sk.getsockname()[1]
            sk.close()
            snap = h.hub_snapshot("http://127.0.0.1:%d" % p, timeout=1.0)
            got = {"all_live": snap["all_sources_live"],
                   "registry_verified_count": snap["capabilities"]["registry_verified_count"]}
            ok = (got["all_live"] is True and got["registry_verified_count"] == 2)
            return ok, {"note": "校准用例：假期望把端口关闭当成连上且有 Registry 计数",
                        "actual": got}

        # ---- Phase 79 golden 化：第 19 引擎 adobe_bridge 三枚桥接黄金域用例 ----
        # 期望逐字段来自进程内协议 round-trip 的真实对账读数（非文档手抄）。把
        # DocumentReader.reconcile 换成常量桩、或把 dispatch 返回值写死，这三条必翻红。
        def k_bridge_handshake_auth_origin_caps(E):
            import json as _json
            m = E["adobe_bridge"]
            root = self.store_dir / "_bridge" / "hs"
            shutil.rmtree(root, ignore_errors=True)
            root.mkdir(parents=True, exist_ok=True)
            eng = m.AdobeBridgeEngine(state_dir=root)
            d = {}

            async def _drive():
                try:
                    await eng.handshake("coreldraw", "24.0", {"document_read": True},
                                        "WRONG-TOKEN", "127.0.0.1")
                    d["auth_reject"] = False
                except m.BridgeError as e:
                    d["auth_reject"] = (e.kind == "auth_failed")
                try:
                    await eng.handshake("coreldraw", "24.0", {"document_read": True},
                                        eng.session_token, "10.9.8.7")
                    d["origin_reject"] = False
                except m.BridgeError as e:
                    d["origin_reject"] = (e.kind == "origin_rejected")
                try:
                    await eng.handshake("gimp", "1.0", {"document_read": True},
                                        eng.session_token, "127.0.0.1")
                    d["unknown_host_reject"] = False
                except m.BridgeError as e:
                    d["unknown_host_reject"] = (e.kind == "unknown_host")
                hs = await eng.handshake("coreldraw", "24.0",
                                         {"document_read": True, "object_write": True},
                                         eng.session_token, "127.0.0.1")
                d["connected"] = (hs["status"] == "connected" and hs["fabricated"] is False)
                d["caps_echo"] = (hs["capabilities"] == {"document_read": True, "object_write": True})
                d["token_masked"] = (hs["session"] != eng.session_token
                                     and eng.session_token not in _json.dumps(hs))
                d["token_random"] = (len(eng.session_token) > 20)
                return hs
            _async_run(_drive)
            ok = (d["auth_reject"] and d["origin_reject"] and d["unknown_host_reject"]
                  and d["connected"] and d["caps_echo"] and d["token_masked"] and d["token_random"])
            return ok, {"expected": "错token→auth_failed；非loopback→origin_rejected；未知宿主→unknown_host；"
                                    "有效握手→connected且能力回显且session仅掩码不外泄原文",
                        "actual": d, "fabricated": False}

        def k_bridge_cdr_read_roundtrip_reconcile(E):
            import asyncio as _aio
            m = E["adobe_bridge"]
            root = self.store_dir / "_bridge" / "rd"
            shutil.rmtree(root, ignore_errors=True)
            root.mkdir(parents=True, exist_ok=True)
            d = {}
            eng0 = m.AdobeBridgeEngine(state_dir=root / "_nohost")

            async def _nohost():
                try:
                    await eng0.dispatch("cdr.object.list", {}, "p", "d",
                                        timeout=1, client_ip="127.0.0.1")
                    d["no_host"] = False
                except m.BridgeError as e:
                    d["no_host"] = (e.kind == "no_host_connected")
            _async_run(_nohost)
            eng = m.AdobeBridgeEngine(state_dir=root / "_rt")

            async def _rt():
                hs = await eng.handshake("coreldraw", "24.0", {"document_read": True},
                                         eng.session_token, "127.0.0.1")
                hid = hs["host_id"]
                disp = _aio.ensure_future(eng.dispatch("cdr.object.list", {}, "p1", "doc-7",
                                                        timeout=2, client_ip="127.0.0.1"))
                cmds = await eng.poll(hid, eng.session_token, "127.0.0.1", timeout=2)
                d["cmd_count"] = len(cmds)
                d["intent"] = cmds[0]["intent"] if cmds else None
                rid = cmds[0]["request_id"]
                await eng.submit_result(rid, eng.session_token, "127.0.0.1",
                                        {"status": "ok",
                                         "payload": {"document_id": "doc-7", "object_count": 3,
                                                     "objects": [{"i": 1}, {"i": 2}, {"i": 3}],
                                                     "source": "cdr"}})
                pack = await disp
                d["status"] = pack["status"]
                d["fabricated"] = pack["fabricated"]
                d["recon"] = pack["reconciled"]
                return pack
            _async_run(_rt)
            r = d.get("recon", {})
            ok = (d.get("no_host") is True and d.get("cmd_count") == 1
                  and d.get("intent") == "object.list" and d.get("status") == "ok"
                  and d.get("fabricated") is False and r.get("source") == "host"
                  and r.get("object_count") == 3 and r.get("document_id") == "doc-7"
                  and r.get("result_source") == "cdr")
            return ok, {"expected": "无宿主→no_host_connected（绝不伪造）；有宿主→round-trip 对账 "
                                    "source=host/object_count=3/document_id=doc-7/result_source=cdr",
                        "actual": {k: v for k, v in d.items() if k != "recon"},
                        "reconciled": r, "fabricated": False}

        def k_bridge_cdr_write_gate_and_readonly_reject(E):
            import asyncio as _aio
            m = E["adobe_bridge"]
            root = self.store_dir / "_bridge" / "wr"
            shutil.rmtree(root, ignore_errors=True)
            root.mkdir(parents=True, exist_ok=True)
            d = {}
            engA = m.AdobeBridgeEngine(state_dir=root / "_gate")
            engB = m.AdobeBridgeEngine(state_dir=root / "_ro")
            engC = m.AdobeBridgeEngine(state_dir=root / "_rt")

            async def _gate():
                p = await engA.dispatch("cdr.object.create", {"kind": "rectangle"}, "p", "d",
                                        timeout=1, client_ip="127.0.0.1")
                d["gate_status"] = p.get("status")
                d["gate_fabricated"] = p.get("fabricated")
                # 确认门先于宿主查找：零宿主亦返回 confirmation_required 且零入队
                d["gate_zero_enqueue"] = (len(engA.connected_hosts()) == 0
                                          and p.get("status") == "confirmation_required")
            _async_run(_gate)

            async def _ro():
                await engB.handshake("coreldraw", "24.0", {"document_read": True},
                                     engB.session_token, "127.0.0.1")
                try:
                    await engB.dispatch("cdr.object.create", {"kind": "rectangle", "confirm": True},
                                        "p", "d", timeout=1, client_ip="127.0.0.1")
                    d["ro_reject"] = False
                except m.BridgeError as e:
                    d["ro_reject"] = (e.kind == "capability_not_negotiated")
            _async_run(_ro)

            async def _rt():
                hs = await engC.handshake("coreldraw", "24.0",
                                          {"document_read": True, "object_write": True},
                                          engC.session_token, "127.0.0.1")
                hid = hs["host_id"]
                disp = _aio.ensure_future(engC.dispatch("cdr.object.create",
                                          {"kind": "rectangle", "confirm": True}, "p", "doc-9",
                                          timeout=2, client_ip="127.0.0.1"))
                cmds = await engC.poll(hid, engC.session_token, "127.0.0.1", timeout=2)
                d["cmd_count"] = len(cmds)
                rid = cmds[0]["request_id"]
                await engC.submit_result(rid, engC.session_token, "127.0.0.1",
                                         {"status": "ok",
                                          "payload": {"document_id": "doc-9", "verified": True,
                                                      "object_count": 4, "undo_group": "grouped",
                                                      "source": "cdr", "objects": []}})
                pack = await disp
                d["status"] = pack["status"]
                d["recon"] = pack["reconciled"]
                d["fabricated"] = pack["fabricated"]
            _async_run(_rt)
            r = d.get("recon", {})
            ok = (d.get("gate_status") == "confirmation_required"
                  and d.get("gate_fabricated") is False and d.get("gate_zero_enqueue") is True
                  and d.get("ro_reject") is True and d.get("cmd_count") == 1
                  and d.get("status") == "ok" and d.get("fabricated") is False
                  and r.get("source") == "host" and r.get("verified") is True
                  and r.get("object_count") == 4 and r.get("undo_group") == "grouped"
                  and r.get("result_source") == "cdr")
            return ok, {"expected": "缺confirm→confirmation_required零入队零变更；只读宿主跑写→"
                                    "capability_not_negotiated；object_write宿主回传 verified/4/grouped 逐字段透传 source=host",
                        "actual": {k: v for k, v in d.items() if k != "recon"},
                        "reconciled": r, "fabricated": False}

        # ---------- Phase 80：服务侧持久化配置三能力 golden 化（第 28 引擎） ----------
        # 三枚用例逐用例私有沙箱全新构造 ServiceRegistryEngine，期望值手算；因 agent_service
        # 已委托同一引擎（单一真相源），此处驱动的就是服务实际跑的那段代码——把被测换成
        # 常量桩，这里连同 agent_service 端点一起失真（判据：换桩即红）。

        def k_flags_seed_merge_persist(E):
            m = E["service_registry"]
            d = {}
            root = self.store_dir / "_sr" / "flags"
            shutil.rmtree(root, ignore_errors=True)
            e = m.ServiceRegistryEngine(root)
            f0 = e.get_flags()
            d["seeded_keys"] = sorted(f0.keys())
            d["defaults_exact"] = (f0 == dict(m.DEFAULT_FLAGS))
            d["written_to_disk"] = e._flags_path().is_file()
            d["disk_matches_defaults"] = json.loads(
                e._flags_path().read_text(encoding="utf-8")) == dict(m.DEFAULT_FLAGS)
            # 写盘后经全新实例读回须仍为 True（证明落盘，非进程内内存）
            e.set_flag("beta_design_review", 1)
            d["set_returns_true"] = (e.get_flags()["beta_design_review"] is True)
            d["persisted_across_instance"] = (
                m.ServiceRegistryEngine(root).get_flags()["beta_design_review"] is True)
            # setdefault 合并：已有文件里的自定义键与既存值不被默认覆盖，同时补齐缺失默认键
            root3 = self.store_dir / "_sr" / "flags_merge"
            shutil.rmtree(root3, ignore_errors=True)
            cfg3 = root3 / "config"
            cfg3.mkdir(parents=True, exist_ok=True)
            (cfg3 / "feature-flags.json").write_text(
                json.dumps({"custom_x": True, "beta_design_review": True}), encoding="utf-8")
            f3 = m.ServiceRegistryEngine(root3).get_flags()
            d["merge_custom_kept"] = (f3.get("custom_x") is True)
            d["merge_existing_not_overwritten"] = (f3.get("beta_design_review") is True)
            d["merge_defaults_added"] = all(k in f3 for k in m.DEFAULT_FLAGS)
            # 损坏持久化文件 → 如实回退默认（不半写、不伪造、WARN）
            e._flags_path().write_text("{broken json", encoding="utf-8")
            d["corrupt_falls_back_to_defaults"] = (
                m.ServiceRegistryEngine(root).get_flags() == dict(m.DEFAULT_FLAGS))
            expected = {
                "seeded_keys": ["auto_preflight_on_import", "beta_design_review",
                                "telemetry_enabled", "webp_preview_enabled"],
                "defaults_exact": True, "written_to_disk": True, "disk_matches_defaults": True,
                "set_returns_true": True, "persisted_across_instance": True,
                "merge_custom_kept": True, "merge_existing_not_overwritten": True,
                "merge_defaults_added": True, "corrupt_falls_back_to_defaults": True,
            }
            return d == expected, {"expected": expected, "actual": d}

        def k_tool_registry_runnable_derive(E):
            m = E["service_registry"]
            d = {}
            root = self.store_dir / "_sr" / "tools"
            shutil.rmtree(root, ignore_errors=True)
            e = m.ServiceRegistryEngine(root)
            reg = e.get_tool_registry()
            d["seed_total"] = len(reg)
            d["high_risk_delete_is_L4"] = (reg["file.delete"]["risk_level"] == "L4")
            d["ps_layer_needs_host"] = (reg["ps.add_layer"]["requires_adobe_host"] is True)
            d["registry_written"] = e._tools_path().is_file()
            # 无宿主上报：6 枚非宿主工具 runnable_now=True，4 枚宿主工具恒 False（绝不伪造可执行）
            view = e.tool_view()
            d["no_host_runnable_true"] = sorted(t["name"] for t in view if t["runnable_now"])
            d["no_host_runnable_false"] = sorted(t["name"] for t in view if not t["runnable_now"])
            # 宿主上报仅 ps.add_layer → 只有这一枚翻 True，其余宿主工具仍 False（逐项派生非一刀切）
            cmap = e.get_capability_map()
            cmap["hosts"] = {"photoshop": {"capabilities": {"ps.add_layer": True}}}
            view2 = e.tool_view()
            d["after_report_addlayer_true"] = next(
                t["runnable_now"] for t in view2 if t["name"] == "ps.add_layer")
            d["after_report_export_false"] = next(
                t["runnable_now"] for t in view2 if t["name"] == "ps.export") is False
            # 登记新工具落盘，全新实例读回（注册表是真实持久化契约存储）
            e.register_tool("custom.eval_tool", "评测自造", "L3", True)
            reg2 = m.ServiceRegistryEngine(root).get_tool_registry()
            d["registered_persisted"] = ("custom.eval_tool" in reg2
                                         and reg2["custom.eval_tool"]["risk_level"] == "L3")
            expected = {
                "seed_total": 10, "high_risk_delete_is_L4": True, "ps_layer_needs_host": True,
                "registry_written": True,
                "no_host_runnable_true": ["asset.search", "file.delete", "generate.image",
                                          "preflight.check", "recovery.snapshot", "workflow.execute"],
                "no_host_runnable_false": ["ai.create_object", "ps.add_layer", "ps.export",
                                           "ps.open_document"],
                "after_report_addlayer_true": True, "after_report_export_false": True,
                "registered_persisted": True,
            }
            return d == expected, {"expected": expected, "actual": d}

        def k_skill_validate_and_load(E):
            m = E["service_registry"]
            d = {}
            root = self.store_dir / "_sr" / "skills"
            shutil.rmtree(root, ignore_errors=True)
            e = m.ServiceRegistryEngine(root)
            # 干净清单：权限在白名单内 + 工具已注册 + 风险合法 → 零错误
            d["clean_no_errors"] = (e.validate_skill(
                {"permissions": ["asset.read", "preflight.run"],
                 "tools": ["preflight.check"], "risk": "low"}) == [])
            # 越权 + 未注册工具 + 非法风险 → 三类错误逐条精确命中
            errs = e.validate_skill({"permissions": ["admin.everything"],
                                     "tools": ["ghost.tool"], "risk": "extreme"})
            d["unknown_perm_flagged"] = ("未知/越权权限: admin.everything" in errs)
            d["unknown_tool_flagged"] = ("引用未注册工具: ghost.tool" in errs)
            d["bad_risk_flagged"] = ("risk 必须为 low/medium/high" in errs)
            d["err_count_exact"] = (len(errs) == 3)
            # 跨能力联动：技能校验读的是同一份工具注册表——先缺后 register，校验即转通过
            d["ref_missing_before_register"] = (
                "引用未注册工具: brand.eval_new" in e.validate_skill(
                    {"tools": ["brand.eval_new"], "risk": "low", "permissions": []}))
            e.register_tool("brand.eval_new", "联动", "L1", False)
            d["ref_ok_after_register"] = (e.validate_skill(
                {"tools": ["brand.eval_new"], "risk": "low", "permissions": []}) == [])
            # load：带 id 的清单被加载；缺 id 的如实跳过（id 门控，绝不把无效清单冒充技能）
            good = e.skills_dir() / "s_ok"
            good.mkdir(parents=True, exist_ok=True)
            (good / "manifest.json").write_text(
                json.dumps({"id": "s_ok", "version": "1.0", "risk": "low"}), encoding="utf-8")
            bad = e.skills_dir() / "s_noid"
            bad.mkdir(parents=True, exist_ok=True)
            (bad / "manifest.json").write_text(
                json.dumps({"version": "0.0"}), encoding="utf-8")
            loaded = e.load_skill_manifests()
            d["loaded_ids"] = sorted(loaded.keys())
            d["missing_id_skipped"] = ("s_noid" not in loaded)
            expected = {
                "clean_no_errors": True, "unknown_perm_flagged": True, "unknown_tool_flagged": True,
                "bad_risk_flagged": True, "err_count_exact": True,
                "ref_missing_before_register": True, "ref_ok_after_register": True,
                "loaded_ids": ["s_ok"], "missing_id_skipped": True,
            }
            return d == expected, {"expected": expected, "actual": d}

        # ---------- Phase 81（轨道 A 服务侧最后一刀）：交付归档 + WebSocket 事件账本 ----------
        def k_delivery_package_build_archive(E):
            import zipfile as _zip
            m = E["delivery"]
            d = {}
            root = self.store_dir / "_delivery" / "build"
            shutil.rmtree(root, ignore_errors=True)
            root.mkdir(parents=True, exist_ok=True)
            eng = m.DeliveryEngine(root)
            # 私有 data_root 沙箱：Source/Preview/Report 各 1 枚普通文件（前两枚在 root 内，
            # 第三枚刻意落在系统临时=数据根之外），外加 3 种拒收形制各 1 条由调用方传入。
            src_a = root / "src_a.txt"; src_a.write_bytes(b"alpha-bytes-A")
            src_b = root / "src_b.bin"; src_b.write_bytes(bytes(range(64)))
            outside = Path(tempfile.gettempdir()) / "zj_eval_delivery_outside.bin"
            outside.write_bytes(b"OUTSIDE-DATA-ROOT")
            plan = {"Source": [src_a], "Preview": [src_b], "Report": [outside]}
            skipped = [
                {"category": "Print", "path": str(root / "gone.bin"),
                 "reason": "not_found", "detail": "no such file"},
                {"category": "Print", "path": str(root / "sub"),
                 "reason": "not_a_file", "detail": "directory"},
                {"category": "Source", "path": str(root / "denied.bin"),
                 "reason": "blocked_by_policy", "detail": "denied"},
            ]
            try:
                manifest, zp = eng.build(pkg_id="eval_deliv", project="eval", notes="n",
                                         categories=list(plan.keys()), plan=plan, skipped=skipped)
                # 1) 收录 3 枚；SHA256 由磁盘实物重算 == 引擎自报（换桩返回常量 manifest 必红）
                d["included"] = manifest["included"]
                sha_ok = True
                for es in manifest["categories"].values():
                    for entry in es:
                        disk_bytes = (eng.package_dir("eval_deliv") / entry["category"]
                                      / entry["name"]).read_bytes()
                        if hashlib.sha256(disk_bytes).hexdigest() != entry["sha256"]:
                            sha_ok = False
                d["sha_disk_matches_report"] = sha_ok
                # 2) inside_data_root 逐条派生：root 内两枚 True、数据根外一枚 False → 计数 1
                flags = {(x["category"], x["name"]): x["inside_data_root"]
                         for x in manifest["included_files"]}
                d["inside_src_a"] = flags[("Source", "src_a.txt")]
                d["inside_src_b"] = flags[("Preview", "src_b.bin")]
                d["inside_outside"] = flags[("Report", outside.name)]
                d["outside_root_count"] = manifest["outside_root_count"]
                # 3) 拒收台账：三种形制各 1 条且互不合并；tally_skipped 恒回三键（缺席=0 非缺键）
                d["skipped_by_reason"] = manifest["skipped_by_reason"]
                d["tally"] = eng.tally_skipped(manifest)
                # 4) ZIP namelist 第三方对账：收录 3 + README + manifest == 5；拒收绝不入 ZIP
                with _zip.ZipFile(zp) as zf:
                    names = set(zf.namelist())
                d["zip_count"] = len(names)
                d["zip_has_readme"] = any(n.endswith("README.txt") for n in names)
                d["zip_has_manifest"] = any(n.endswith("manifest.json") for n in names)
                d["zip_no_rejected"] = not any(("gone.bin" in n or "denied.bin" in n) for n in names)
                d["zip_structure_ok"] = all(
                    f"{cat}/{en}" in names for cat, en in
                    [("Source", "src_a.txt"), ("Preview", "src_b.bin"), ("Report", outside.name)])
                # 5) manifest.json 原子落盘可复读，与内存副本全等（非半写、非伪造）
                m_reload = json.loads((eng.package_dir("eval_deliv") / "manifest.json")
                                      .read_text(encoding="utf-8"))
                d["reload_included_match"] = (m_reload["included"] == manifest["included"])
                d["reload_files_match"] = (m_reload["included_files"] == manifest["included_files"])
                # 6) 空 plan（合法请求：所有路径皆被拒收）→ included=0，tally 缺席形制以 0 出现
                m2, _zp2 = eng.build(pkg_id="eval_empty", project="empty", notes="",
                                     categories=[], plan={}, skipped=[
                                         {"category": "Print", "path": "/x",
                                          "reason": "blocked_by_policy", "detail": "denied"}])
                d["empty_included"] = m2["included"]
                d["empty_tally"] = eng.tally_skipped(m2)
            finally:
                try:
                    outside.unlink()
                except OSError:
                    pass
            expected = {
                "included": 3, "sha_disk_matches_report": True,
                "inside_src_a": True, "inside_src_b": True, "inside_outside": False,
                "outside_root_count": 1,
                "skipped_by_reason": {"not_found": 1, "not_a_file": 1, "blocked_by_policy": 1},
                "tally": {"skipped_not_found": 1, "skipped_blocked_by_policy": 1,
                          "skipped_not_a_file": 1},
                "zip_count": 5, "zip_has_readme": True, "zip_has_manifest": True,
                "zip_no_rejected": True, "zip_structure_ok": True,
                "reload_included_match": True, "reload_files_match": True,
                "empty_included": 0,
                "empty_tally": {"skipped_not_found": 0, "skipped_blocked_by_policy": 1,
                               "skipped_not_a_file": 0},
            }
            return d == expected, {"expected": expected, "actual": d}

        def k_websocket_events_ledger_and_ring(E):
            m = E["event"]

            async def _core():
                r = {}
                hub = m.EventHub(history=5)
                # 1) 空连接发布 → 仍入缓冲 + 累计台账，delivered_to=0（绝不伪造交付量）
                r0 = await hub.publish("task.registered", {"task_id": "t0", "total_steps": 3})
                r["empty_delivered_to"] = r0["delivered_to"]
                r["after_first_total"] = hub.published_total
                # 2) 环形截断：再连发 7 条 task.progress → published_total=8、history 只留 5、
                #    truncated=True（截断只削 history 不动台账，"截断"与"未截断"是两回事）
                for i in range(7):
                    await hub.publish("task.progress", {"task_id": "t0", "current_step": i + 1})
                snap = hub.recent(limit=100)
                r["published_total"] = snap["published_total"]
                r["history_len"] = len(snap["events"])
                r["truncated"] = snap["truncated"]
                r["kind_registered"] = snap["kind_counts"]["task.registered"]
                r["kind_progress"] = snap["kind_counts"]["task.progress"]
                # 3) json_safe：Path→str / Enum→name / set→list，绝不静默丢弃未知类型
                import enum as _e

                class _Color(_e.Enum):
                    RED = 1
                js = m.json_safe({"a": Path("C:/tmp/x"), "c": _Color.RED, "d": {1, 2, 3}})
                r["jsonsafe_path_str"] = isinstance(js["a"], str) and js["a"].endswith("x")
                r["jsonsafe_enum_name"] = (js["c"] == "RED")
                r["jsonsafe_set_kept"] = (len(js["d"]) == 3)
                # 4) 死连接摘除：delivered_to 只计存活（1），坏连接失败即从簿记移除（不静默保留）
                class _FakeWS:
                    def __init__(self, fail=False):
                        self.sent = []
                        self.fail = fail

                    async def send_json(self, payload):
                        if self.fail:
                            raise RuntimeError("simulated disconnect")
                        self.sent.append(payload)
                ok = _FakeWS()
                bad = _FakeWS(fail=True)
                await hub.connect(ok)
                await hub.connect(bad)
                r["client_count_2"] = hub.client_count
                rp = await hub.publish("task.completed", {"task_id": "t0"})
                r["delivered_live_only"] = rp["delivered_to"]
                r["client_count_after_dead_removed"] = hub.client_count
                r["published_total_9"] = hub.published_total
                # 5) disconnect 幂等（再次摘除同一 ws 不炸）+ 进程级单例 hub() is _EVENT_HUB
                await hub.disconnect(ok)
                await hub.disconnect(ok)
                r["client_count_zero"] = hub.client_count
                r["singleton_identity"] = (m.hub() is m._EVENT_HUB)
                return r
            d = _async_run(_core)
            expected = {
                "empty_delivered_to": 0, "after_first_total": 1,
                "published_total": 8, "history_len": 5, "truncated": True,
                "kind_registered": 1, "kind_progress": 7,
                "jsonsafe_path_str": True, "jsonsafe_enum_name": True, "jsonsafe_set_kept": True,
                "client_count_2": 2, "delivered_live_only": 1,
                "client_count_after_dead_removed": 1, "published_total_9": 9,
                "client_count_zero": 0, "singleton_identity": True,
            }
            return d == expected, {"expected": expected, "actual": d}

        return [
            case("eval-color-contrast-extreme", "design", "黑白对比度真值 21:1", c_contrast_extreme),
            case("eval-color-contrast-gray-band", "design", "灰白对比落在 4~5 带且 AA/AAA 判定正确", c_contrast_gray_band),
            case("eval-color-convert-red", "design", "纯红 hex→RGB/CMYK 精确转换", c_convert_red),
            case("eval-color-delta-e", "design", "同色 ΔE=0 且红蓝 ΔE>50", c_delta_e_identity_and_distance),
            case("eval-color-harmony-triadic", "design", "三角配色派生 3 个互异色", c_harmony_triadic),
            case("eval-color-nearest-palette", "design", "色板最近色命中 #FF0000", c_nearest_in_palette),
            case("eval-layout-collide-overlap", "design", "重叠检测：面积/IoU 精确真值", l_collide_overlap),
            case("eval-layout-collide-clean", "design", "无重叠时不误报", l_collide_clean),
            case("eval-layout-align-left", "design", "左对齐全部贴基准且纵向不动", l_align_left),
            case("eval-layout-safe-area", "design", "安全区违规精确定位到越界对象", l_safe_area_violation),
            case("eval-packaging-dieline", "production", "刀版三框几何与面积精确真值", p_dieline_boxes),
            case("eval-packaging-imposition", "production", "拼版 n_up/张数/利用率手工可核真值", p_imposition_known_fit),
            case("eval-packaging-barcode-ean13", "production", "EAN-13 校验位+95 模块+真实 PNG 落盘", p_barcode_ean13_render),
            case("eval-packaging-barcode-bad-checkdigit", "production", "非法校验位必须抛错不静默出图", p_barcode_bad_checkdigit_rejected),
            case("eval-packaging-panel-row", "production", "三面板共边拓扑/外轮廓长精确真值", p_panel_topology_row),
            case("eval-packaging-panel-disconnected", "production", "断开刀版诚实标 disconnected", p_panel_topology_disconnected),
            case("eval-barcode-ean13-bitstring", "barcode", "EAN-13 95 模块位串逐字符对 GS1 公开例全等", bc_ean13_bitstring_exact),
            case("eval-barcode-ean8-bitstring", "barcode", "EAN-8 67 模块位串+校验位 4 全等", bc_ean8_bitstring_exact),
            case("eval-barcode-pixel-truth", "barcode", "渲染 PNG 真像素逐点采样：护条/静区/留白黑白精确", bc_pixel_truth),
            case("eval-barcode-bad-checkdigit-msg", "barcode", "双码制非法校验位 ValueError 原文逐字精确且绝不落盘", bc_bad_checkdigit_msg_exact),
            case("eval-barcode-qr-capacity-exact", "barcode", "QR 满容留本版/+1 升版逐字对 Table 8、v1/v2 单块对 Table 9", bc_qr_capacity_exact),
            case("eval-barcode-qr-block-structure", "barcode", "40 格码字算术全等：手抄 total、data==cap+(2|3)、Σ块==total、校正图形按 n=2+v//7 推导", bc_qr_block_structure),
            case("eval-barcode-qr-pixel-truth", "barcode", "QR 真像素 12 点采样 + 21×21 栅格与申报 matrix 逐格同源 + 静区整带全白", bc_qr_pixel_truth),
            case("eval-barcode-qr-mask-format-bch", "barcode", "评测侧独立重算格式/版本 BCH 逐字符对账、掩码=罚分最小、尺子先复现 ISO 三串", bc_qr_mask_format_bch),
            case("eval-barcode-qr-honest-reject", "barcode", "QR 四条诚实边界 ValueError 原文逐字精确且绝不落盘", bc_qr_honest_reject),
            case("eval-barcode-honest-degradation", "barcode", "Code-128/DataMatrix 留守不虚报不产图 + 13 位合法直传 + 参数化几何真值", bc_honest_degradation),
            case("eval-barcode-1d-oracle-law", "barcode", "一维 oracle 自身受验收：三表构造律 R=complement(L)/G=reverse(complement(L)) + L∩G 必空 + PARITY 十串互异 + 双公开例逐模块复现 P34 手抄件 + 与产品申报位串对账", bc_1d_oracle_construction_law),
            case("eval-barcode-1d-decode-ean13", "barcode", "EAN-13 独立解码扫段：0..9 每位在 L/G/R 三族各走一遍，从 PNG 像素读回 13 位逐字符全等（产品自报串绝不作解码输入）", bc_1d_decode_ean13_pixels),
            case("eval-barcode-1d-decode-ean8", "barcode", "EAN-8 独立解码：公开例 + 逐位扫段从像素读回 8 位全等，并如实自陈本腿对 G 表缺陷免疫（无 parity）", bc_1d_decode_ean8_pixels),
            case("eval-barcode-1d-decode-negatives", "barcode", "一维 oracle 三阴性+：反色/抹中间护条/篡改单模块/跨码制采样一律不许读回原串，同图正对照作哨兵防空集假绿", bc_1d_decode_negatives),
            case("eval-topology-box9-full", "topology", "直插盒 9 面板度序列/折长 640/裁切线 870/面积全字段精确", tp_box9_full_truth),
            case("eval-topology-fold-coords", "topology", "折线坐标逐数精确（竖折 x=0 全高 / 横折 y=100 宽 60）", tp_fold_line_coords),
            case("eval-topology-partial-overlap", "topology", "共边仅部分重叠 shared=5.0 折线段坐标精确", tp_partial_overlap),
            case("eval-topology-rejected-honest", "topology", "断开+非法混合三态如实：rejected 逐项/空输入抛原文", tp_rejected_and_disconnected),
            case("eval-topology-heuristic-honesty", "topology", "role_hint 几何启发式如实标注，宽扁高身分级精确", tp_heuristic_honesty),
            case("eval-brand-palette-exact", "brand", "双色带真图中位切分恰两色各占 50%", b_palette_exact),
            case("eval-brand-consistency-match", "brand", "品牌双色打同源图 coverage=1.0 且 ΔE≤1", b_consistency_full_match),
            case("eval-brand-no-overmatch", "brand", "绿品牌色打纯红图必不匹配（防过匹配）", b_no_overmatch_negative),
            case("eval-brand-logo-geometry", "brand", "Logo alpha 包围盒/留白/宽高比逐数真值", b_logo_geometry_exact),
            case("eval-print-rgb-lowres-flagged", "print", "RGB 低分图必标 PRINT-001+008 且 error=3", p_rgb_lowres_flagged),
            case("eval-print-cmyk-hires-clean-color", "print", "CMYK 300DPI 色彩/分辨率合规但仍如实报未设出血", p_cmyk_hires_clean_color),
            case("eval-knowledge-rag-retrieve-rank", "knowledge", "咖啡查询 coffee 居首 sim≈0.282843 且 brake=0.0", k_rag_retrieve_ranking),
            case("eval-knowledge-rag-scope-isolation", "knowledge", "project 作用域按 project_id 匹配可见/不匹配隔离", k_rag_scope_isolation),
            case("eval-revision-parse-matrix", "revision", "指令矩阵：唯一命中/保护冲突/多候选澄清逐项定位", rv_parse_status_matrix),
            case("eval-revision-apply-math", "revision", "apply 真实换算：240×96/深红/提亮/字号逐项精确", rv_apply_exact_math),
            case("eval-revision-unparsed-honest", "revision", "闲聊文本 0 变更 2 句如实 unparsed 绝不编造", rv_unparsed_never_fabricated),
            case("eval-review-dirty-matrix", "review", "缺陷夹具 score=68 与 4 问题逐项精确", rw_dirty_issue_matrix),
            case("eval-review-clean-hundred", "review", "合规夹具零问题满分（评审器虚报即红）", rw_clean_full_marks),
            case("eval-review-contrast-autofix", "review", "WCAG 对比度真值 1.16/18.883 与自动修复 #737373", rw_contrast_autofix_math),
            case("eval-security-path-four-way", "security", "路径判定三阻一放四向哨兵（恒真/恒假均红）", sc_path_four_way),
            case("eval-security-permission-intersection", "security", "User∩Skill∩Tool 交集：授权/越权/技能不足三态", sc_permission_intersection),
            case("eval-security-highrisk-no-overreport", "security", "高危审批：无 executor 不虚报、有则真实执行", sc_highrisk_no_overreport),
            case("eval-workflow-cycle-detected", "workflow", "环依赖 DAG 校验精确报 Circular dependency detected", wf_cycle_detected),
            case("eval-workflow-happy-chain", "workflow", "INPUT→TOOL→OUTPUT 链贯通：result 与日志序列逐条精确", wf_happy_chain_exact),
            case("eval-workflow-fail-no-pass-on", "workflow", "未注册工具：n2 诚实 failed 且 n3 不被虚报执行", wf_fail_no_pass_on),
            case("eval-workflow-conditional-fork", "workflow", "条件边分叉：真分支执行、假分支如实 skipped", wf_conditional_fork),
            case("eval-workflow-loop-aggregate", "workflow", "循环体 3 项真实跑满并聚合 iterations=3", wf_loop_body_aggregate),
            case("eval-workflow-idempotent-replay", "workflow", "同链两次执行剥除 id/时间戳后判分字段全等", wf_idempotent_replay),
            case("eval-asset-embed-exact", "asset", "token-hash 稀疏向量非零位与哈希桶独立复算精确真值", a_embed_tokenhash_exact),
            case("eval-asset-add-image", "asset", "真实 PNG 入库：元数据/缩略图落盘/向量同源逐项精确", a_add_asset_real_image),
            case("eval-asset-search-rank", "asset", "检索排序双钉：自匹配 1.0 居首、异图 0.402015 精确次之", a_search_rank_exact),
            case("eval-asset-license-attribution", "asset", "CC-BY 署名规则双向分支逐项回读精确", a_license_attribution_rule),
            case("eval-asset-generation-readback", "asset", "生成元数据回读全字段精确，None 字段不虚设", a_generation_readback),
            case("eval-asset-delete-cascade", "asset", "冷读回计数+删除级联三库清空+二次删除判 False", a_delete_cascade_reload),
            case("eval-recovery-snapshot-sha256", "recovery", "快照校验和与固定字节独立 sha256 复算精确相符", r_snapshot_sha256_exact),
            case("eval-recovery-restore-revert", "recovery", "损坏后还原字节真回归，幽灵快照还原判 False 不虚报", r_restore_reverts_corruption),
            case("eval-recovery-txn-rollback", "recovery", "事务操作计数真值+回滚真实还原+幽灵事务判 False", r_transaction_rollback_true),
            case("eval-recovery-register-task", "recovery", "任务注册字段真值且状态真实落盘", r_register_task_fields),
            case("eval-recovery-checkpoint", "recovery", "检查点恢复真实还原字节，幽灵任务如实抛 ValueError", r_recover_checkpoint_and_ghost),
            case("eval-recovery-no-file-honest", "recovery", "无本地文件快照诚实记不可恢复，还原必判 False", r_no_local_file_honest),
            case("eval-typography-measure-serif", "typography", "serif 夹具度量逐数精确：3989 units→95.736pt/行高 28.8/零缺字", t_serif_measure_exact),
            case("eval-typography-measure-sans", "typography", "sans 夹具缺字如实计 1 不谎称全覆盖，76.32pt 精确", t_sans_missing_honest),
            case("eval-typography-gate-isolates", "typography", "双专码位覆盖硬门槛：豆腐块字体必淘汰仅剩唯一候选", t_match_double_gate_isolates),
            case("eval-typography-weight-order", "typography", "单门槛+字重罚分：0.9861>0.6542 降序逐项精确", t_match_single_gate_weight_order),
            case("eval-typography-fallback-persist", "typography", "替换规则回显+resolve 来源+规则文件真实落盘", t_fallback_rule_persisted),
            case("eval-typography-ghost-truth", "typography", "幽灵字体 None+KeyError 原文，启发式过门槛真值", t_ghost_and_heuristic_truth),
            case("eval-license-roundtrip-deterministic", "license", "同载荷两次签发票据字节全等+验签载荷全等回显", l_roundtrip_deterministic),
            case("eval-license-tamper-format-exact", "license", "篡改签名与四非法格式及数组载荷报错原文逐字", l_tamper_and_format_exact),
            case("eval-license-expiry-states", "license", "过期原文/未来真通过/非法日期报错三态精确", l_expiry_three_states),
            case("eval-license-machine-bind", "license", "mid 前缀绑定：匹配放行、不符拒绝原文逐字", l_machine_bind),
            case("eval-config-defaults-literal", "config", "默认真值：版本/引擎URL/7阻断/13扩展/拼接串不展开如实", c_defaults_literal),
            case("eval-config-cold-readback", "config", "set+save 后全新实例冷读回键，磁盘文件真实含键", c_cold_readback),
            case("eval-config-migration", "config", "旧版本+缺键迁移：抬版本/保标记/补键/真实落盘四字段", c_migration),
            case("eval-config-path-guard", "config", "路径拦截四阻两放+双扩展名两态（.tar.gz 不误报）", c_path_guard),
            case("eval-routing-registry-parse", "model_routing", "自供 YAML 注册表真实解析：2模型2provider 枚举逐字段", mr_registry_parse),
            case("eval-routing-cost-tie", "model_routing", "cost 按模型均价 0.004 平票插入序裁决+未知模型 None", mr_cost_tie_formula),
            case("eval-routing-privacy-latency", "model_routing", "privacy 择本地、latency 随熔断配置均值择云", mr_privacy_latency),
            case("eval-routing-budget-breaker", "model_routing", "预算严格小于排除+熔断开环不依赖事件循环（无环不抛且状态翻/开环被排除/窗口走完无环复口/环内同样不抛）", mr_budget_breaker),
            case("calib-contrast-wrong-expect", "calibration", "判分器校准：错期望必须判 fail", k_contrast_wrong, must_fail=True),
            case("calib-collide-wrong-expect", "calibration", "判分器校准：虚构期望必须判 fail", k_collide_wrong, must_fail=True),
            case("calib-brand-palette-wrong-expect", "calibration", "判分器校准：品牌调色板假期望必须判 fail", k_brand_palette_wrong_expect, must_fail=True),
            case("calib-print-wrong-expect", "calibration", "判分器校准：印前假期望（RGB 不含 PRINT-001）必须判 fail", k_print_wrong_expect, must_fail=True),
            case("calib-rag-wrong-expect", "calibration", "判分器校准：检索假排序（brake 居首）必须判 fail", k_rag_wrong_expect, must_fail=True),
            case("calib-revision-wrong-expect", "calibration", "判分器校准：改稿假期望（深红改色判未变）必须判 fail", k_revision_wrong_expect, must_fail=True),
            case("calib-review-wrong-expect", "calibration", "判分器校准：评审假期望（缺陷文档满分）必须判 fail", k_review_wrong_expect, must_fail=True),
            case("calib-security-wrong-expect", "calibration", "判分器校准：安全假期望（正常文件遭阻断）必须判 fail", k_security_wrong_expect, must_fail=True),
            case("calib-workflow-wrong-expect", "calibration", "判分器校准：工作流假期望（未注册工具判成功）必须判 fail", k_workflow_wrong_expect, must_fail=True),
            case("calib-asset-wrong-expect", "calibration", "判分器校准：素材假期望（锅图冒充顶位）必须判 fail", k_asset_wrong_expect, must_fail=True),
            case("calib-recovery-wrong-expect", "calibration", "判分器校准：恢复假期望（幽灵快照判可还原）必须判 fail", k_recovery_wrong_expect, must_fail=True),
            case("calib-typography-wrong-expect", "calibration", "判分器校准：排版度假期望（serif 宽写成 sans 值）必须判 fail", k_typo_width_wrong_expect, must_fail=True),
            case("calib-typography-gate-wrong-expect", "calibration", "判分器校准：门槛假期望（缺字字体冒充过关）必须判 fail", k_typo_gate_wrong_expect, must_fail=True),
            case("calib-barcode-wrong-expect", "calibration", "判分器校准：条码假期望（校验位 7 写成 0）必须判 fail", k_barcode_wrong_expect, must_fail=True),
            case("calib-barcode-qr-wrong-expect", "calibration", "判分器校准：QR 假期望（手抄满容 17 字符判需升版）必须判 fail", k_barcode_qr_wrong_expect, must_fail=True),
            case("calib-barcode-1d-wrong-expect", "calibration", "判分器校准：一维假期望（公开例读回串末位改一格）必须判 fail", k_barcode_1d_wrong_expect, must_fail=True),
            case("calib-barcode-1d-g7-typo-table", "calibration", "判分器校准：错表必错读——把 G(7) 抄成 L(4) 后同一像素串读回非原串（反证本判据真在走 G 表）", k_barcode_1d_g7_typo_table, must_fail=True),
            case("calib-topology-wrong-expect", "calibration", "判分器校准：拓扑假期望（断开刀版判连通）必须判 fail", k_topology_wrong_expect, must_fail=True),
            case("calib-license-wrong-expect", "calibration", "判分器校准：授权假期望（篡改密钥判验签通过）必须判 fail", k_license_wrong_expect, must_fail=True),
            case("calib-config-wrong-expect", "calibration", "判分器校准：配置假期望（拼接串判已展开绝对路径）必须判 fail", k_config_wrong_expect, must_fail=True),
            case("calib-routing-wrong-expect", "calibration", "判分器校准：路由假期望（延迟模式判选本地）必须判 fail", k_routing_wrong_expect, must_fail=True),
            case("eval-update-no-feed", "update", "无更新源诚实拒伪造：no_update_configured 三字段+三渠道未配+非法渠道原文", u_no_feed_honest),
            case("eval-update-sign-canonical", "update", "清单规范化字节与 HMAC-SHA256 独立复算全等，篡改/错钥必验签失败", u_sign_canonical),
            case("eval-update-download-apply", "update", "真实 HTTP 全链：验签通过→下载 sha256 相符→原子应用字节回归", u_download_apply),
            case("eval-update-branch-matrix", "update", "分支矩阵逐项真值：乱码/篡改/未设钥/版本门先于验签门/降级", u_branch_matrix),
            case("eval-nas-gate-matrix", "nas", "未配置/非法类型/空目标/不可达四态如实，报错原文逐字", n_gate_matrix),
            case("eval-nas-list-truth", "nas", "文件共享连接与目录清单逐项真值：排序/类型/大小/首见缓存", n_list_truth),
            case("eval-nas-path-guard", "nas", "路径穿越与绝对路径双向拦截，越界下载绝不落盘", n_path_guard),
            case("eval-nas-cache-cold", "nas", "真实下载+冷实例缓存命中四元组全等+清缓存/遗忘逐项回读", n_cache_cold),
            case("eval-provider-unconfigured", "provider", "未配置不虚报：chat/生图/健康检查三态零伪造", p_unconfigured_honest),
            case("eval-provider-key-dpapi", "provider", "密钥脱敏字面真值+DPAPI 落盘：明文绝不在 JSON", p_key_dpapi),
            case("eval-provider-chat-roundtrip", "provider", "真实 HTTP 对话：content/usage/Bearer 头/单请求逐字段精确", p_chat_roundtrip),
            case("eval-provider-auth-breaker", "provider", "401 不重试+熔断开路零 HTTP+view circuit_open 真值", p_auth_breaker),
            case("calib-update-wrong-expect", "calibration", "判分器校准：更新假期望（远程降级判发现新版）必须判 fail", k_update_wrong_expect, must_fail=True),
            case("calib-nas-wrong-expect", "calibration", "判分器校准：NAS 假期望（缓存命中冒充首见）必须判 fail", k_nas_wrong_expect, must_fail=True),
            case("calib-provider-wrong-expect", "calibration", "判分器校准：Provider 假期望（未配置冒充可用）必须判 fail", k_provider_wrong_expect, must_fail=True),
            case("eval-compare-zero-diff", "compare", "空 diff：identical=True 且六类计数全 0", cp_zero_diff),
            case("eval-compare-field-categories", "compare", "富字段对象六类逐数归类+moved 逐字段+相同 type 不 diff", cp_field_categories),
            case("eval-compare-structure-typemap", "compare", "结构增删按 type 归类：image→asset、未知兜底 layer、项序逐字段", cp_structure_typemap),
            case("eval-compare-keys-universe", "compare", "compare_keys 限字段集+name 索引+未知字段兜底 visual", cp_keys_universe),
            case("eval-bgre-flood-exact", "bg_removal", "真像素泛洪：采样色/清 8400px/ratio 0.84/孔洞保留（泛洪≠色键）", bg_flood_exact),
            case("eval-bgre-threshold-bound", "bg_removal", "阈值边界真值：0/1 仅等色、999 钳 255 全幅皆达", bg_threshold_bound),
            case("eval-bgre-ghost-no-write", "bg_removal", "幽灵输入诚实抛错：FileNotFoundError 中文原文+绝不落盘", bg_ghost_no_write),
            case("eval-bgre-default-determinism", "bg_removal", "缺省输出落同级 _no_bg.png+两次跑逐数幂等（确定性反查）", bg_default_output_and_determinism),
            case("calib-compare-wrong-expect", "calibration", "判分器校准：对比假期望（fill_color 改动归 layer）必须判 fail", k_compare_wrong_expect, must_fail=True),
            case("calib-bgre-wrong-expect", "calibration", "判分器校准：去背景假期望（色键冒充泛洪清孔洞）必须判 fail", k_bgremoval_wrong_expect, must_fail=True),
            case("eval-panel-grid-inversion", "panel_recognition",
                 "3×2 网格真像素反解：6 面板 2535px/线型/邻接 7/度序列/轮廓 1248 全字段",
                 pr_grid_inversion),
            case("eval-panel-param-gates", "panel_recognition",
                 "参数门效应：线宽 3↔12 两态+降级响应面全等+px_per_mm 换算不改像素面积",
                 pr_param_gates),
            case("eval-panel-honest-empty", "panel_recognition",
                 "三态诚实空报：无墨/无封闭域/被门限拒绝均 0 面板且反零向量出 6 面板",
                 pr_honest_empties),
            case("eval-panel-determinism", "panel_recognition",
                 "三次调用逐字段全等+params 回显默认门限+剥 path 后 JSON sha256 相同",
                 pr_determinism),
            case("calib-panel-wrong-expect", "calibration",
                 "判分器校准：面板识别假期望（coverage 0.77 虚线判成 cut）必须判 fail",
                 k_panel_wrong_expect, must_fail=True),
            case("eval-manifest-clean-zero-findings", "installer_manifest",
                 "合规自造清单零检出+分段计数 {F2,I2,R1,S7}+续行/注释解析真值+校验零落盘",
                 mf_clean_zero_findings),
            case("eval-manifest-broken-findings-exact", "installer_manifest",
                 "破损清单 12 项检出逐 id 逐项序（8 error·3 warn·1 info）+evidence 行号字段"
                 "精确+合规写法零误报+PATH 命中≠三路定位+数据根差分",
                 mf_broken_findings_exact),
            case("eval-manifest-seam-stub-falsification", "installer_manifest",
                 "四路接缝反证：磁盘/glob/环境目录/注册表桩换即翻转，还原后回基线",
                 mf_seam_stub_falsification),
            case("eval-manifest-rejects-and-installed", "installer_manifest",
                 "入参拒绝八类原文逐字+两次调用 JSON sha256 全等+installed 必由 probes 真命中派生",
                 mf_rejects_and_installed_derivation),
            case("eval-manifest-build-evidence-verified", "installer_artifact_build",
                 "编译面正控：三独立信号同立+证据字节/sha256 与本用例独立重算一致+自报 "
                 "compiled/fabricated 翻转不改判+校验前后沙箱逐条全等（零落盘）",
                 mf_build_evidence_verified),
            case("eval-manifest-build-evidence-arms", "installer_artifact_build",
                 "编译面四臂：等长替换唯 sha256 可查（信号仍齐）/编译后改脚本判陈旧而静态复校"
                 "照旧 ok/产物缺席优先于编译未证成/退出码非零无完成行判 compile_not_proven",
                 mf_build_evidence_mismatch_arms),
            case("eval-manifest-build-warnings-completeness", "installer_artifact_build",
                 "告警四臂全文等式与截断下界两口径+缺字段判 evidence_incomplete 且 provenance "
                 "不替编译器说话+完成行可退回派生字段与 stderr 旧键+build-evidence.json 文件形制",
                 mf_build_evidence_warnings_and_completeness),
            case("eval-manifest-preflight-ready-readonly", "installer_preflight",
                 "预检面正控：七项全 True 且 blocked/unknown 皆空（令牌走显式桩、真令牌只以关系"
                 "式判）+写探针真落盘自清+{autopf} 两条候选+需求字节=产物+载荷实测+不给 override "
                 "时真实安装根零探针",
                 mf_preflight_ready_and_readonly_promise),
            case("eval-manifest-preflight-blocked-stubs", "installer_preflight",
                 "预检面十臂：同名文件/载荷缺失（先阻断后未知）/证据缺席与篡改/余量读不到与不足/"
                 "目标 exe 占用三态/令牌未提权与探测不到/写探针三种归因，四路接缝还原后回基线",
                 mf_preflight_blocked_and_seam_stubs),
            case("eval-manifest-static-fire-arms", "installer_manifest",
                 "四条编译器定论臂各自单独开火：R6 单写括号 error（键名派生与转义无关）/R12 两条 "
                 "directive 各一条 warn（整词比较零误报）/R13 缺版本 error/R11 缺省 admin 配对 warn",
                 mf_static_p53_fire_arms),
            case("eval-manifest-registry-uninstall-arms", "installer_manifest",
                 "R14 七臂：无撤销声明 error（多行写入归并、值名逐枚）/发货形制与祖先整键删与"
                 "显式永不撤销三臂零检出/父键与同键 ifempty 均不得洗白/跨 root 不算覆盖，"
                 "每条 error 带本机真卸载量测凭证",
                 mf_registry_uninstall_arms),
            case("calib-manifest-build-wrong-expect", "calibration",
                 "判分器校准：编译面假期望（把证据自报 compiled=False 当成编译未证成）必须判 fail",
                 k_manifest_build_wrong_expect, must_fail=True),
            case("calib-manifest-wrong-expect", "calibration",
                 "判分器校准：清单假期望（R7 预建目录与数据根无交集判 error）必须判 fail",
                 k_manifest_wrong_expect, must_fail=True),
            case("eval-hub-transport-fidelity", "desktop_hub",
                 "三源只读 GET 逐字段搬运：八行派生全等+零 POST+跨源 agree=True+按 finished_at 挑最新",
                 hb_transport_fidelity),
            case("eval-hub-derive-not-invent", "desktop_hub",
                 "绝不编数反证：跨源引擎清单不一致须显 False 不静默、形状不合的 health 判该源降级（绝不写 vNone 占位）",
                 hb_derive_not_invent),
            case("eval-hub-degradation-three-forms", "desktop_hub",
                 "三形制诚实降级：端口关闭三源 connection_failed/非造境服务逐源独立/JSON 残缺 + headless 建窗如实 False",
                 hb_degradation_three_forms),
            case("eval-hub-action-single-post", "desktop_hub",
                 "唯一 POST 通道：只读路径恒零 POST、eval_run 恰一次体 {}、500 照抄不吞、未知动作抛错且零新增请求",
                 hb_action_single_post),
            case("calib-hub-wrong-expect", "calibration",
                 "判分器校准：Hub 假期望（端口关闭当成连上且有 Registry 计数）必须判 fail",
                 k_hub_wrong_expect, must_fail=True),
            case("eval-brand-semantics-unconfigured-honest", "brand_semantics",
                 "未配置 Provider 诚实 needs_external+fabricated=False+provider_status no_provider_configured，零语义合成",
                 bs_unconfigured_honest),
            case("eval-brand-semantics-no-provider-injected", "brand_semantics",
                 "未注入 Provider（provider=None）诚实 needs_external+reason no_provider_injected，绝不含 semantics",
                 bs_no_provider_injected),
            case("eval-brand-semantics-verbatim-passthrough", "brand_semantics",
                 "模型真实语义 JSON 逐字段全等透传+usage 133+method 真值+明文密钥不落响应（引擎不合成任何字符）",
                 bs_verbatim_passthrough),
            case("eval-brand-semantics-dataurl-bearer-independent", "brand_semantics",
                 "从真 PNG 字节独立复算 data URL 逐字符对账进模型请求体+Bearer 头在场+恰 1 图 1 请求+明文密钥缺席",
                 bs_dataurl_bearer_independent),
            case("eval-brand-semantics-unparseable-raw", "brand_semantics",
                 "模型返回非 JSON→provider_output_unparseable+保留 raw 原文+绝不硬编 schema 字段",
                 bs_unparseable_raw),
            case("calib-brand-semantics-wrong-expect", "calibration",
                 "判分器校准：品牌语义假期望（无 Provider 时凭空合成调性关键词）必须判 fail",
                 k_brand_semantics_wrong_expect, must_fail=True),
            case("eval-exec-plan-disk-derived", "installer_execution",
                 "plan 只算不跑：sha/字节独立读盘复算+五枚落点 exists_now 现场重算一致+"
                 "卸载键解括号派生+七门四开关逐字+沙箱逐条不变",
                 ex_plan_disk_derived),
            case("eval-exec-gates-refuse-zero-side-effects", "installer_execution",
                 "十七臂各按 reason 码拒绝且拒绝代价为零：同意缺失三形制/制品缺席越根/台账缺席"
                 "坏 JSON/改长与等长替换/台账指错文件/安装根相对越根落源码树非空/卸载侧三门",
                 ex_gates_refuse_zero_side_effects),
            case("eval-exec-install-verified-from-diff", "installer_execution",
                 "installed 只由差集派生：十项观察逐项在场+install_root 差集与评测 rglob 实测"
                 "全等+新增物理文件恰 4 枚（同目录双标签不许数两遍）+既有数据逐字节完好",
                 ex_install_verified_from_diff),
            case("eval-exec-verdict-ignores-self-report", "installer_execution",
                 "同一份自报（exit 0+完成行+stdout 原文一字不差）两趟判分相反：盘上有物才装成，"
                 "空手趟 3 真 7 假且台账与现场自洽只判 ledger_unverified 不伪造回执",
                 ex_verdict_ignores_self_report),
            case("eval-exec-uninstall-three-forms-and-reconcile", "installer_execution",
                 "一装两卸三形制+对账活证：退出 0 未清判 incomplete、真卸目录与两枚键归零且作用域"
                 "锁死创建集、用户数据只点名不删、executed_verified 九项重算逐字段、等长篡改唯 sha"
                 "可查还原即回绿、nothing_to_uninstall 与 missing_uninstaller 各有独立状态码；"
                 "装好未卸且现场在位 ⇒ 诚实中间态，同一份 install-only 记录配上痕迹已不在 ⇒ 点名",
                 ex_uninstall_forms_and_reconcile),
            case("eval-exec-second-round-evidence-not-user-data", "installer_execution",
                 "证据与用户数据分账（产品形状：台账+缺省日志就在被观察的数据根内）：轮 1 对照绿、"
                 "轮 2 上一轮台账被本轮改写（评测独立复算哈希为证）仍判 uninstall_verified 且 "
                 "deviating 空、创建集 5→4、对账面只回显不声称重写、数据根剩三枚由评测 rglob 核",
                 ex_second_round_evidence_not_user_data),
            case("calib-exec-trust-self-report", "calibration",
                 "判分器校准：执行面假期望（安装器自报退出码 0+完成行当成已装成）必须判 fail",
                 k_exec_trust_self_report, must_fail=True),
            case("calib-manifest-preflight-wrong-expect", "calibration",
                 "判分器校准：预检面假期望（调用方关闭写探针当成可写已证）必须判 fail",
                 k_manifest_preflight_wrong_expect, must_fail=True),
            # --- Phase 59 路线 B：第 22 引擎的守护面升为独立黄金域 runtime_guard（5 真值 + 1 校准） ---
            # 期望逐字段来自只读探针 p59_det_probe.py v2（每面三轮、归一后全字段比对，diff_count 恒 0），
            # 其 steps 与本处字段**同名同值**；不是照着手抄文档。
            case("eval-guard-write-probe-three-arms", "runtime_guard",
                 "写入探针三臂：可写臂三真且探针零残留/目标为文件与父为文件两臂三假且 FileExistsError 起手",
                 gr_write_probe_three_arms),
            case("eval-guard-disk-threshold-two-sided", "runtime_guard",
                 "磁盘阈值两侧：min=0 必达、min=2**62 必不达、负阈值抛中文原文、连续量不入判分",
                 gr_disk_threshold_two_sided),
            case("eval-guard-port-bind-probe-two-sided", "runtime_guard",
                 "端口真 bind 两侧：自起监听桩判不可用且 detail 是 OSError、已关端口判可用且 detail 为 None、越界抛原文",
                 gr_port_bind_probe_two_sided),
            case("eval-guard-single-instance-mutex-chain", "runtime_guard",
                 "单实例互斥五步链：同实例二次已存在→释放→再申请→**另一实例**视角仍已存在且 released=False",
                 gr_single_instance_mutex_chain),
            case("eval-guard-diagnostic-derived-and-degrades", "runtime_guard",
                 "诊断由逐项派生：健康 3/3/0、带端口 4 项、数据根不可写降级 degraded 且 failed=performed-passed",
                 gr_diagnostic_derived_and_degrades),
            case("calib-guard-wrong-expect", "calibration",
                 "判分器校准：守护面假期望（数据根写探针 fail 仍判 healthy）必须判 fail",
                 k_guard_wrong_expect, must_fail=True),
            # --- Phase 79 golden 化：第 19 引擎 adobe_bridge 桥接机制升为三枚独立黄金域 ---
            # 期望逐字段来自进程内协议 round-trip 的真实对账读数（handshake/dispatch/poll/
            # submit_result/reconcile），非照文档手抄；把 DocumentReader.reconcile 换成常量桩
            # 或把 dispatch 返回值写死，下面三域断言即刻翻红。桥接为纯 stdlib async、零真 socket。
            case("eval-bridge-handshake-auth-origin-caps", "bridge_handshake",
                 "§8/§25 握手面：错token→auth_failed、非loopback→origin_rejected、未知宿主→unknown_host、"
                 "有效握手→connected且能力回显、session token 随机且对外掩码（不外泄原文）",
                 k_bridge_handshake_auth_origin_caps),
            case("eval-bridge-cdr-read-roundtrip-reconcile", "bridge_cdr_read",
                 "§9 只读 round-trip：dispatch(cdr.object.list)→poll 得 intent=object.list 一条→宿主回传 "
                 "object_count=3→对账 source=host/object_count=3/document_id 透传/result_source=cdr/fabricated=false；"
                 "无宿主时同样 dispatch 必诚实 no_host_connected（绝不伪造）",
                 k_bridge_cdr_read_roundtrip_reconcile),
            case("eval-bridge-cdr-write-confirm-gate-and-readonly-reject", "bridge_cdr_write",
                 "§97/§25 写侧：缺 confirm→confirmation_required 且零入队零变更(fabricated=false)；"
                 "object_write 已协商宿主 confirm=true 下发→宿主回传 verified=true/object_count=4/undo_group "
                 "→对账逐字段透传(source=host)；只读宿主跑写工具必 capability_not_negotiated（绝不硬跑）",
                 k_bridge_cdr_write_gate_and_readonly_reject),
            # --- Phase 80（轨道 A 第二刀）：服务侧持久化配置三枚 golden 用例（均 scored，校准恒 34） ---
            case("eval-flags-seed-merge-persist", "feature_flags",
                 "功能开关真值：空沙箱种子 4 默认键精确+落盘 JSON=默认；set_flag 全新实例读回证落盘非内存；"
                 "已有文件 setdefault 合并不覆盖自定义键与既存值只补缺；损坏文件如实回退默认（不半写不伪造）",
                 k_flags_seed_merge_persist),
            case("eval-tool-registry-runnable-derive", "tool_registry",
                 "工具注册表真值：种子 10 工具+file.delete=L4+ps.add_layer 需宿主+落盘；无宿主上报 6 枚非宿主 "
                 "runnable=true/4 枚宿主=false（不伪造可执行）；宿主仅上报 ps.add_layer→只这一枚翻 True 其余仍 "
                 "False（逐项派生非一刀切）；register 落盘新实例读回",
                 k_tool_registry_runnable_derive),
            case("eval-skill-validate-and-load", "skill_runtime",
                 "技能运行时真值：干净清单零错；越权+未注册工具+非法风险三错逐条命中；技能校验读同一工具注册表"
                 "（先缺后 register 校验即转通过，跨能力联动非各写各）；带 id 清单加载、缺 id 如实跳过（id 门控）",
                 k_skill_validate_and_load),
            # --- Phase 81（轨道 A 服务侧最后一刀）：交付归档 + WebSocket 事件账本两枚 golden 用例（均 scored，校准恒 34） ---
            case("eval-delivery-package-archive", "delivery_package",
                 "一键交付归档真值：私有 data_root 沙箱 build 收录 3 枚，SHA256 由磁盘实物重算==自报（换桩即红）；"
                 "inside_data_root 逐条派生（root 内 True/数据根外 False，outside_root_count=1，如实标注不拦）；"
                 "拒收三种形制各 1 条互不合并、tally_skipped 缺席形制以 0 出现；ZIP namelist==收录3+README+manifest=5 "
                 "且拒收绝不入 ZIP；manifest 原子落盘复读全等；空 plan→included=0 仍出 ZIP",
                 k_delivery_package_build_archive),
            case("eval-websocket-events-ledger-and-ring", "websocket_events",
                 "WebSocket 事件账本真值：空连接 publish delivered_to=0 却台账累计（绝不伪造交付量）；环形 maxlen=5 连发 8 "
                 "→published_total=8/len(history)=5/truncated=True（截断只削 history 不动台账）；json_safe 把 Path/Enum/set "
                 "收敛为 JSON 安全值不静默丢弃；死连接 send 失败 delivered_to 只计存活(1)且坏连接从簿记摘除；disconnect 幂等；"
                 "进程级 hub() 单例指向 _EVENT_HUB",
                 k_websocket_events_ledger_and_ring),
        ]

    # ====================================================================
    # 执行与判分
    # ====================================================================

    def run_full(self) -> Dict[str, Any]:
        cases = self._cases()
        engines = {name: self._engine(name) for name in
                   ("color", "layout", "packaging", "brand", "print_qa", "knowledge_rag",
                    "review", "revision", "security", "workflow",
                    "license", "config", "model_router",
                    "update", "nas", "provider", "compare", "bg_removal",
                    "panel_recognition", "installer_manifest", "desktop_hub",
                    "installer_runtime", "adobe_bridge", "service_registry",
                    "event", "delivery")}
        started = datetime.now()
        results: List[Dict[str, Any]] = []
        for c in cases:
            t0 = time.perf_counter()
            try:
                ok, details = c["run"](engines)
                crashed = None
            except Exception as e:  # 被测引擎抛异常 = 真实 fail，并如实记异常
                ok, details, crashed = False, {"exception": f"{type(e).__name__}: {e}"}, True
            dt_ms = round((time.perf_counter() - t0) * 1000, 3)
            verdict = bool(ok)
            results.append({
                "id": c["id"], "category": c["category"], "title": c["title"],
                "must_fail": c["must_fail"], "passed": verdict,
                "crashed": bool(crashed), "latency_ms": dt_ms, "details": details,
            })
        finished = datetime.now()

        # ---- 校准：must_fail 用例若被判 pass，判分器失真，整份报告不诚实 ----
        calibration = []
        for r in [x for x in results if x["must_fail"]]:
            good = (r["passed"] is False)
            calibration.append({"id": r["id"], "expected_verdict": "fail",
                                "actual_verdict": "pass" if r["passed"] else "fail",
                                "calibration_ok": good})
        # 2026-10-11 追加改核（Phase 63 G 腿，不改写上段结论）：一维解码两枚校准臂
        # （calib-barcode-1d-wrong-expect / calib-barcode-1d-g7-typo-table）并入 ⇒
        # 校准枚数 32→34。此处数字取自本轮盘上读数（p63_eval_red.txt「校准段枚数=34」、
        # p63_eval_green.txt 同口径复量），非手抄；若下轮再增臂须同样从读数重钉。
        calibration_ok = (all(c["calibration_ok"] for c in calibration)
                          and len(calibration) == 34)

        scored = [r for r in results if not r["must_fail"]]
        passed = sum(1 for r in scored if r["passed"])
        lat = [r["latency_ms"] for r in results]

        def cat_metrics(cat):
            rows = [r for r in scored if r["category"] == cat]
            p = sum(1 for r in rows if r["passed"])
            return {"total": len(rows), "passed": p, "failed": len(rows) - p,
                    "pass_rate": (p / len(rows)) if rows else None}

        report = {
            "schema": SCHEMA,
            "report_id": "eval-" + finished.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(3),
            "started_at": started.isoformat(timespec="seconds"),
            "finished_at": finished.isoformat(timespec="seconds"),
            "honest": calibration_ok,
            "calibration_error": (not calibration_ok),
            "calibration": calibration,
            "summary": {
                "executed_cases": len(scored),
                "passed": passed,
                "failed": len(scored) - passed,
                "pass_rate": (passed / len(scored)) if scored else None,
                "crashed_cases": sum(1 for r in results if r["crashed"]),
                "latency_ms": {"total": round(sum(lat), 3), "max": max(lat) if lat else 0,
                               "median": round(statistics.median(lat), 3) if lat else 0},
            },
            "categories": {
                "design": cat_metrics("design"),
                "production": cat_metrics("production"),
                "barcode": cat_metrics("barcode"),
                "topology": cat_metrics("topology"),
                "brand": cat_metrics("brand"),
                "print": cat_metrics("print"),
                "knowledge": cat_metrics("knowledge"),
                "revision": cat_metrics("revision"),
                "review": cat_metrics("review"),
                "security": cat_metrics("security"),
                "workflow": cat_metrics("workflow"),
                "asset": cat_metrics("asset"),
                "recovery": cat_metrics("recovery"),
                "typography": cat_metrics("typography"),
                "license": cat_metrics("license"),
                "config": cat_metrics("config"),
                "model_routing": cat_metrics("model_routing"),
                "update": cat_metrics("update"),
                "nas": cat_metrics("nas"),
                "provider": cat_metrics("provider"),
                "compare": cat_metrics("compare"),
                "bg_removal": cat_metrics("bg_removal"),
                "panel_recognition": cat_metrics("panel_recognition"),
                "installer_manifest": cat_metrics("installer_manifest"),
                "desktop_hub": cat_metrics("desktop_hub"),
                "brand_semantics": cat_metrics("brand_semantics"),
                "installer_execution": cat_metrics("installer_execution"),
                "installer_artifact_build": cat_metrics("installer_artifact_build"),
                "installer_preflight": cat_metrics("installer_preflight"),
                "runtime_guard": cat_metrics("runtime_guard"),
                "bridge_handshake": cat_metrics("bridge_handshake"),
                "bridge_cdr_read": cat_metrics("bridge_cdr_read"),
                "bridge_cdr_write": cat_metrics("bridge_cdr_write"),
                "feature_flags": cat_metrics("feature_flags"),
                "tool_registry": cat_metrics("tool_registry"),
                "skill_runtime": cat_metrics("skill_runtime"),
                "delivery_package": cat_metrics("delivery_package"),
                "websocket_events": cat_metrics("websocket_events"),
            },
            "not_evaluated": NOT_EVALUATED,
            "method_note": "所有分数来自对 color/layout/packaging/brand/print_qa/knowledge_rag/revision/review/security/workflow/asset/recovery/typography"
                           "/license/config/model_router"
                           " 引擎的真实调用与手工可核期望的逐字段比对；asset/recovery/typography 逐用例全新评测私有沙箱，"
                           "uuid/时间戳仅渗入非计分字段；typography 以现场编译的自供夹具 TTF 走溯源解析→重放索引，"
                           "本机字体零干扰且全链路真实解析无桩；barcode 位串期望在评测源码内以 GS1 公开例逐字符手抄、"
                           "像素真值直接采样渲染产物，topology 为纯几何全字段精确（与 production 域共引擎但独立挂证）；"
                           "license 判分为 HMAC 纯函数零状态，config/model_routing 逐用例在评测私有 "
                           "_config/_router 沙箱先清后建全新构造（注册表为自供 YAML 夹具走真实解析路径，"
                           "熔断无环怪癖在独立线程钉死、与调用语境无关）；update/nas/provider"
                           " 三域引擎同为模块即引擎、逐用例在评测私有 _update/_nas/_provider "
                           "沙箱先清后建全新构造（文件共享夹具为评测自造真目录；网络面一律由"
                           "评测自起的 127.0.0.1:0 临时端口进程内 mock HTTP 服务器驱动，"
                           "routes/seen 逐实例隔离、用毕必 shutdown，判分字段不含端口号，"
                           "下载/应用/缓存命中/熔断零 HTTP 均以真实字节与请求计数落证）；"
                           "compare 为 Phase 38 自服务层逐字下沉的纯函数（六类 diff 逐字段逐计数精确，"
                           "相同 type 不入 diff、未知兜底等怪癖真值同样钉死），bg_removal 逐用例在评测私有 "
                           "_bg 沙箱先清后建，夹具为评测自造 100×100 真像素图（背景色包围孔洞 alpha 必 255 "
                           "证泛洪≠全局色键），清 8400px/84% 与两次跑幂等均直接采样输出 PNG 落证；"
                           "panel_recognition 为 Phase 47 第 25 引擎（Otsu 自算阈值 + 轴对齐游程抽线 + "
                           "共线间隙合并 + 虚线支撑屏障 + 游程并查集连通域，零 CV/ML），夹具由评测自绘 "
                           "220×100 刀版并与引擎自测夹具在画幅/划分/虚线常数上相互独立、字节 sha256 双向钉死，"
                           "6 面板 2535px、邻接 7、轮廓 1248 等期望手算可核且逐字段实测冻结；同图在 "
                           "max_line_width 3 与 12 下如实出两态（降级分支字段集与正常分支全等、绝不缺键），"
                           "无墨/无封闭域/全域被面积门限拒三态均 0 面板且 fabricated=False，反零向量以同沙箱"
                           "同法在真刀版上出 6 面板证明该 0 非流水线失效；installer_manifest 为 "
                           "Phase 49 第 26 引擎（.iss 分段解析 + R1~R14 十四条规则，纯只读零落盘；"
                           "Phase 53 补 R11 per-user 区域与 PrivilegesRequired 配对、R12 弃用架构标识、"
                           "R13 缺 AppVersion/AppVerName 三臂，均以本机真 iscc 实测退出码/告警原文为据；"
                           "Phase 58 补 R14 写入型 [Registry] 缺卸载撤销声明，判据出处为本机真卸载实测"
                           "（卸载器 exit 0、安装根与卸载子键消失、四树归零而应用键三值原样留守）；"
                           "同轮再转编译面 verify_build_evidence（三独立信号+逐侧字节与 sha256+告警"
                           "等式/下界两口径+静态复校当下重跑）与预检面 preflight_install（七项三态判据："
                           "提权、目标占用、可写探针、磁盘余量实测、载荷可得性、安装根解析、产物证成），"
                           "两脸纯计算/只读探测零安装零卸载，随本机而异的量只以关系式判或走显式接缝桩"
                           "（_token_elevation/_disk_free/_exclusive_open/_write_probe 用毕必还原），"
                           "自报 compiled/fabricated 只回显不参与判定），"
                           "夹具为评测自造 .iss 文本与真实载荷文件、逐用例落私有 _manifest 沙箱先清后建；"
                           "合规夹具的形制照抄本机真编译通过（exit=0、0 告警）的生产 setup.iss——AppId 首"
                           "花括号双写 + PrivilegesRequired=lowest + x64compatible + AppVersion 在场，"
                           "使 R6 转义/R11 配对/R12 架构/R13 版本/R14 卸载撤销五臂的合规侧与破损侧在同一"
                           "域内双向可核；"
                           "12 项检出的 id 序/severity 序/evidence 行号全部手算可核并实测冻结"
                           "（{tmp} 系 Inno 合法常量但无环境映射、永不解析，故 R7 触发机器无关；"
                           "{install_date} 一条字面量同落 R1/R8 两规则）；iscc 的"
                           "PATH 单路命中与三路寻径定位分开回显且互不冒充（本机实测 on_path=False、"
                           "fixed_dir=True），编译器专属项仍只出 info 不硬编本机值；"
                           "结论的可证伪性由四路模块级接缝反证钉死——_path_exists 桩假使合规清单翻出"
                           "缺件 error、_glob_matches 桩真使零命中通配 error 消失、_env_dir 双向桩使 R7 "
                           "在 0 检出与 warn 间翻转、注册表桩使 installed 真翻转且真 portable 标记"
                           "不翻 installed（未桩态 installed 必等于 probes 逐项派生），桩后必还原并复验"
                           "同一性；"
                           "desktop_hub 为 Phase 50 第 27 引擎（纯标准库 tkinter+urllib 的"
                           "桌面 Hub 客户端），评测台驱动的是其数据/呈现/动作三层纪律而非 GUI："
                           "夹具为评测自起的 127.0.0.1:0 进程内真 HTTP（三条只读 GET 载荷逐字段"
                           "自报），判分前把 base_url 归一成 \"BASE\" 以剔除随机端口（P37 同源"
                           "纪律，保证顶层/嵌套两次逐字段全等）；八行窗口文本按逐字符全等钉死且"
                           "每一行只可能来自夹具字段（vNone/占位数字这类编数形态由形状门用例"
                           "直接反证——P50 探针曾抓到 200+JSON 但缺 version 被当成连上的真缺陷并"
                           "已根治）；跨源引擎清单不一致须显 False 而非静默、端口关闭/非造境服务/"
                           "JSON 残缺三形制各有独立真值且缺失字段一律 None；POST 通道唯一性以"
                           "服务端 seen 计数仪落证：只读路径恒零 POST、显式动作恰一次体 {} 的 "
                           "POST、500 原文照抄、未知动作抛错且零新增请求；无显示会话下 open_hub "
                           "如实回 created=False/reason=headless_requested（不谎称建窗）；"
                           "brand_semantics 为 Phase 56 把 not_shipped 转正的独立域（复用第 21 brand 引擎"
                           "的 recognize_semantics + 已内置的真实 Provider 客户端，零新引擎）：网络面由评测"
                           "自起的 127.0.0.1:0 进程内 mock HTTP 驱动真实 urllib /chat/completions 往返，"
                           "判分字段不含端口号（P37 同源纪律）；真实视觉语义 JSON 逐字段全等透传（引擎"
                           "不合成任何一个字符，usage 逐字对齐 mock 回报），data:image/…;base64 多模态 URL"
                           "从磁盘真 PNG 字节独立复算后逐字符对账进模型请求体，Bearer 头在场且明文密钥绝不"
                           "落响应；未配置/未注入 Provider 与模型返回非 JSON 三形制诚实降级各有真值且绝不"
                           "由引擎代劳伪造（needs_external+fabricated=False、provider_output_unparseable 保留 raw）；"
                           "runtime_guard 为 Phase 59 把第 22 引擎（installer_runtime_engine）的守护面升为独立"
                           "黄金域：写探针三臂（可写臂三真且探针文件零残留、目标为普通文件与父路径为普通文件"
                           "两臂三假且 error 以 FileExistsError 起手）、磁盘阈值两侧（min=0 必达、min=2**62 必"
                           "不达、负阈值抛点名错误，free/used 连续量不入判分）、端口真 bind 两侧（占用臂由评测"
                           "自起 bind(0)+listen 的桩构成，端口号只作结构核对不作判分内容、外部 socket 一枚不碰）、"
                           "单实例互斥五步链（另一实例视角仍已存在且它的 released=False，句柄不属它就不许谎称"
                           "释放成功）、诊断由逐项检查派生（健康 3/3/0、带端口 4 项、数据根落在普通文件之下"
                           "判 degraded 且 checks_failed==performed-passed，且 RuntimeGuardEngine 构造不建根、"
                           "跑完探针才建根——两侧同写）；沙箱走评测私有 _zj_eval_guard 根先清后建，期望逐字段"
                           "来自只读探针 p59_det_probe.py v2 的三轮归一实测（v1 分类器把真值漂移误分成易变字段、"
                           "自造假绿，已整体作废）；同轮把 installer_manifest 的编译面三件、预检面两件改挂 "
                           "installer_artifact_build / installer_preflight 独立域名（判据原文一字未改，只换挂证"
                           "域名，清单域由 11 条收为 6 条），并补预检面校准一条（把'调用方关闭写探针'当成"
                           "'可写已证'的假期望必红）；"
                           "通过率=passed/executed_cases 真实派生，无固定模拟分",
            "results": results,
        }
        self._save_report(report)
        return report

    def suite_manifest(self) -> Dict[str, Any]:
        return {
            "schema": SCHEMA,
            "cases": [{"id": c["id"], "category": c["category"], "title": c["title"],
                       "must_fail": c["must_fail"]} for c in self._cases()],
            "not_evaluated": NOT_EVALUATED,
        }

    # ====================================================================
    # 报告持久化 / 查询 / 对比 / 建议（全部针对真实落盘 JSON）
    # ====================================================================

    def _save_report(self, report: Dict[str, Any]) -> Path:
        self.report_dir.mkdir(parents=True, exist_ok=True)
        path = self.report_dir / f"{report['report_id']}.json"
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def list_reports(self) -> List[Dict[str, Any]]:
        out = []
        if not self.report_dir.exists():
            return out
        for p in sorted(self.report_dir.glob("eval-*.json")):
            try:
                r = json.loads(p.read_text(encoding="utf-8"))
                out.append({"report_id": r.get("report_id", p.stem),
                            "finished_at": r.get("finished_at"),
                            "honest": r.get("honest"),
                            "pass_rate": (r.get("summary") or {}).get("pass_rate"),
                            "executed_cases": (r.get("summary") or {}).get("executed_cases")})
            except Exception:
                out.append({"report_id": p.stem, "error": "报告文件损坏，无法解析（如实上报，不猜测内容）"})
        return sorted(out, key=lambda x: x.get("finished_at") or "", reverse=True)

    def get_report(self, report_id: str) -> Dict[str, Any]:
        if not report_id or "/" in report_id or "\\" in report_id or ".." in report_id:
            raise EvaluationError("非法 report_id")
        p = self.report_dir / f"{report_id}.json"
        if not p.exists():
            raise EvaluationError(f"报告不存在: {report_id}")
        return json.loads(p.read_text(encoding="utf-8"))

    def compare(self, id_a: str, id_b: str) -> Dict[str, Any]:
        a, b = self.get_report(id_a), self.get_report(id_b)
        ca = {r["id"]: r for r in a["results"]}
        cb = {r["id"]: r for r in b["results"]}
        shared = sorted(set(ca) & set(cb))
        flips = [{"id": i, "a_passed": ca[i]["passed"], "b_passed": cb[i]["passed"]}
                 for i in shared if ca[i]["passed"] != cb[i]["passed"]]
        rate_a = (a.get("summary") or {}).get("pass_rate")
        rate_b = (b.get("summary") or {}).get("pass_rate")
        return {
            "a": {"report_id": a["report_id"], "finished_at": a["finished_at"], "pass_rate": rate_a},
            "b": {"report_id": b["report_id"], "finished_at": b["finished_at"], "pass_rate": rate_b},
            "pass_rate_delta": (round(rate_b - rate_a, 6) if rate_a is not None and rate_b is not None else None),
            "incomparable_cases_only_in": {"a": sorted(set(ca) - set(cb)), "b": sorted(set(cb) - set(ca))},
            "shared_cases": len(shared),
            "flips": flips,
            "regressed": [f["id"] for f in flips if f["a_passed"] and not f["b_passed"]],
            "improved": [f["id"] for f in flips if not f["a_passed"] and f["b_passed"]],
        }

    def recommendations(self, report: Dict[str, Any]) -> List[str]:
        recs: List[str] = []
        if report.get("calibration_error"):
            recs.append("[BLOCK] 判分校准用例未全部判 fail——本轮通过率数字不可信，先修判分器再看分数")
        s = report.get("summary", {})
        for cat, m in (report.get("categories") or {}).items():
            pr = m.get("pass_rate")
            if pr is None:
                continue
            fails = [r["id"] for r in report.get("results", []) if r["category"] == cat and not r["passed"]]
            if pr < 0.7:
                recs.append(f"[WARN] {cat} 通过率 {pr:.0%} 偏低，未过用例: {', '.join(fails) or '-'}")
            elif pr < 1.0:
                recs.append(f"[INFO] {cat} 未全过（{pr:.0%}），未过用例: {', '.join(fails)}")
        if s.get("crashed_cases"):
            recs.append(f"[WARN] {s['crashed_cases']} 条用例使被测引擎抛异常，见 results[].details.exception")
        if not recs:
            recs.append("[OK] 全部已评测用例通过且判分校准正常；未评测类别仍按 not_evaluated 如实声明")
        return recs


if __name__ == "__main__":
    import shutil
    import tempfile

    tmp = Path(tempfile.mkdtemp(prefix="zj_eval_selftest_"))
    try:
        eng = EvaluationEngine(store_dir=tmp / "eval")
        rep = eng.run_full()
        # --- 真实断言（非恒真：把判分换成常量桩这里必红） ---
        assert rep["honest"] is True and rep["calibration_error"] is False, rep["calibration"]
        assert len(rep["calibration"]) == 34 and all(c["calibration_ok"] for c in rep["calibration"])
        sm = rep["summary"]
        # Phase 58：installer_execution 黄金域 6 条真值 + 1 条校准转正（执行器交付），
        # 另随第 26 引擎新增静态规则 R14 的七臂用例（计分 +1）⇒
        # 本机实量（第 10 枚缺陷的第二轮用例并入后）：计分 133→134、总 163→164、校准 30
        # Phase 59 路线 B（runtime_guard 独立黄金域 + 编译面/预检面从清单域拆出 + 预检校准）⇒
        # 本机实量：计分 134→139（+5 守护真值）、校准 30→32（守护 + 预检各 1）、总 164→171
        # 2026-10-11 追加改核（Phase 63 G 腿，不改写上段结论）：barcode 域并入一维解码
        # 独立复算 oracle 真值 4 枚（位串对账 / EAN-13 逐位扫段读回 / EAN-8 逐位扫段读回 /
        # 三阴性同图正对照）⇒ 本机实量：计分 139→143、校准 32→34（错期望 + 错表反证各 1）、
        # 总 171→177、barcode 10→14。四数同取 p63_eval_green.txt 现场读数，非手抄。
        # Phase 79（轨道 A 第一刀：Adobe 桥 golden 化）：新增 bridge_handshake /
        # bridge_cdr_read / bridge_cdr_write 三个黄金域各 1 枚真值用例（计分 +3），均为
        # scored（非 must_fail），故校准恒 34 不动 ⇒ 本机实量（run_full 现场读数，非手抄）：
        # 计分 143→146、校准 34→34、总 177→180。三枚用例真实驱动 handshake→dispatch→
        # poll→submit_result→reconcile 协议往返（协议合规 mock 宿主，非真实 Adobe/CAD 文档改写）。
        # Phase 80（轨道 A 第二刀：服务侧持久化配置 golden 化）：新增 feature_flags /
        # tool_registry / skill_runtime 三个黄金域各 1 枚真值用例（计分 +3），均为 scored（非
        # must_fail），故校准恒 34 不动 ⇒ 本机实量（run_full 现场读数，非手抄）：计分 146→149、
        # 校准 34→34、总 180→183。三枚用例逐用例私有沙箱全新构造第 28 引擎 ServiceRegistryEngine
        # 实例，驱动 agent_service 委托的同一份代码（单一真相源），真值来自种子精确/setdefault
        # 合并/损坏回退、无宿主则 ps.*/ai.* runnable_now 恒 False 的确定性派生、越权拒落盘与 id 门控。
        # Phase 81（轨道 A 服务侧最后一刀：交付归档 + WebSocket 事件账本 golden 化）：新增
        # delivery_package / websocket_events 两个黄金域各 1 枚真值用例（计分 +2），均为 scored（非
        # must_fail），故校准恒 34 不动 ⇒ 本机实量（run_full 现场读数，非手抄）：计分 149→151、
        # 校准 34→34、总 183→185。两枚用例逐用例私有沙箱全新构造第 29/30 引擎 EventHub/DeliveryEngine
        # 实例，驱动 agent_service 委托的同一份代码（单一真相源），真值来自磁盘实物 SHA256 重算==自报、
        # ZIP namelist 对账、拒收三形制分账、环形截断只削 history 不动台账、无存活连接 delivered_to=0。
        assert sm["executed_cases"] == 151, sm
        assert sm["passed"] == 151 and sm["failed"] == 0, [r for r in rep["results"] if not r["passed"]]
        assert _close(sm["pass_rate"], 1.0)
        assert rep["categories"]["design"]["total"] == 10 and rep["categories"]["production"]["total"] == 6
        assert rep["categories"]["brand"] == {"total": 4, "passed": 4, "failed": 0, "pass_rate": 1.0}
        assert rep["categories"]["print"] == {"total": 2, "passed": 2, "failed": 0, "pass_rate": 1.0}
        assert rep["categories"]["knowledge"] == {"total": 2, "passed": 2, "failed": 0, "pass_rate": 1.0}
        _three = {"total": 3, "passed": 3, "failed": 0, "pass_rate": 1.0}
        assert rep["categories"]["revision"] == _three and rep["categories"]["review"] == _three
        assert rep["categories"]["security"] == _three
        assert rep["categories"]["workflow"] == {"total": 6, "passed": 6, "failed": 0, "pass_rate": 1.0}
        _six = {"total": 6, "passed": 6, "failed": 0, "pass_rate": 1.0}
        assert rep["categories"]["asset"] == _six and rep["categories"]["recovery"] == _six
        assert rep["categories"]["typography"] == _six
        _five = {"total": 5, "passed": 5, "failed": 0, "pass_rate": 1.0}
        # 2026-10-11 追加改核（Phase 63 G 腿）：barcode 域由 10 枚扩为 14 枚（读数见上注），
        # 变量随之更名 _bc10→_bc14，值由本轮 run_full 的 categories["barcode"] 现场派生。
        _bc14 = {"total": 14, "passed": 14, "failed": 0, "pass_rate": 1.0}
        assert rep["categories"]["barcode"] == _bc14 \
            and rep["categories"]["topology"] == _five
        _four = {"total": 4, "passed": 4, "failed": 0, "pass_rate": 1.0}
        assert rep["categories"]["license"] == _four and rep["categories"]["config"] == _four
        assert rep["categories"]["model_routing"] == _four
        assert rep["categories"]["update"] == _four and rep["categories"]["nas"] == _four
        assert rep["categories"]["provider"] == _four
        assert rep["categories"]["compare"] == _four and rep["categories"]["bg_removal"] == _four
        assert rep["categories"]["panel_recognition"] == _four
        _six_mf = {"total": 6, "passed": 6, "failed": 0, "pass_rate": 1.0}
        _three_mf = {"total": 3, "passed": 3, "failed": 0, "pass_rate": 1.0}
        _two_mf = {"total": 2, "passed": 2, "failed": 0, "pass_rate": 1.0}
        _five_gr = {"total": 5, "passed": 5, "failed": 0, "pass_rate": 1.0}
        assert rep["categories"]["installer_manifest"] == _six_mf, rep["categories"]["installer_manifest"]
        assert rep["categories"]["installer_artifact_build"] == _three_mf
        assert rep["categories"]["installer_preflight"] == _two_mf
        assert rep["categories"]["runtime_guard"] == _five_gr
        assert rep["categories"]["desktop_hub"] == _four
        assert rep["categories"]["brand_semantics"] == _five
        assert rep["categories"]["installer_execution"] == _six
        # Phase 79：Adobe 桥三个黄金域各 1 枚真值用例（协议往返，非只置布尔位）
        _one_br = {"total": 1, "passed": 1, "failed": 0, "pass_rate": 1.0}
        assert rep["categories"]["bridge_handshake"] == _one_br, rep["categories"]["bridge_handshake"]
        assert rep["categories"]["bridge_cdr_read"] == _one_br, rep["categories"]["bridge_cdr_read"]
        assert rep["categories"]["bridge_cdr_write"] == _one_br, rep["categories"]["bridge_cdr_write"]
        # Phase 80：服务侧持久化配置三个黄金域各 1 枚真值用例（驱动单一真相源引擎，非只置布尔位）
        assert rep["categories"]["feature_flags"] == _one_br, rep["categories"]["feature_flags"]
        assert rep["categories"]["tool_registry"] == _one_br, rep["categories"]["tool_registry"]
        assert rep["categories"]["skill_runtime"] == _one_br, rep["categories"]["skill_runtime"]
        # Phase 81：交付归档 + WebSocket 事件账本两个黄金域各 1 枚真值用例（驱动第 29/30 引擎磁盘/内存副作用）
        assert rep["categories"]["delivery_package"] == _one_br, rep["categories"]["delivery_package"]
        assert rep["categories"]["websocket_events"] == _one_br, rep["categories"]["websocket_events"]
        # 三枚用例 details.actual 落证真由引擎派生（换常量桩这里必红）：
        _fl = next(r for r in rep["results"] if r["id"] == "eval-flags-seed-merge-persist")
        assert _fl["passed"] and _fl["details"]["actual"]["defaults_exact"] is True \
            and _fl["details"]["actual"]["seeded_keys"] == ["auto_preflight_on_import",
                "beta_design_review", "telemetry_enabled", "webp_preview_enabled"] \
            and _fl["details"]["actual"]["corrupt_falls_back_to_defaults"] is True, _fl["details"]
        _tr = next(r for r in rep["results"] if r["id"] == "eval-tool-registry-runnable-derive")
        assert _tr["passed"] and _tr["details"]["actual"]["no_host_runnable_false"] == \
            ["ai.create_object", "ps.add_layer", "ps.export", "ps.open_document"] \
            and _tr["details"]["actual"]["after_report_export_false"] is True, _tr["details"]
        _sk = next(r for r in rep["results"] if r["id"] == "eval-skill-validate-and-load")
        assert _sk["passed"] and _sk["details"]["actual"]["err_count_exact"] is True \
            and _sk["details"]["actual"]["ref_ok_after_register"] is True \
            and _sk["details"]["actual"]["loaded_ids"] == ["s_ok"], _sk["details"]
        # Phase 81 两枚用例 details.actual 落证真由引擎磁盘/内存副作用派生（换常量桩这里必红）：
        _dp = next(r for r in rep["results"] if r["id"] == "eval-delivery-package-archive")
        assert _dp["passed"] and _dp["details"]["actual"]["sha_disk_matches_report"] is True \
            and _dp["details"]["actual"]["outside_root_count"] == 1 \
            and _dp["details"]["actual"]["skipped_by_reason"] == \
                {"not_found": 1, "not_a_file": 1, "blocked_by_policy": 1} \
            and _dp["details"]["actual"]["zip_count"] == 5 \
            and _dp["details"]["actual"]["empty_included"] == 0, _dp["details"]
        _we = next(r for r in rep["results"] if r["id"] == "eval-websocket-events-ledger-and-ring")
        assert _we["passed"] and _we["details"]["actual"]["empty_delivered_to"] == 0 \
            and _we["details"]["actual"]["published_total"] == 8 \
            and _we["details"]["actual"]["history_len"] == 5 \
            and _we["details"]["actual"]["truncated"] is True, _we["details"]
        # 品牌语义透传用例真实驱动了 Provider HTTP 往返（逐字段语义 + usage 落证，非只置布尔位）
        _bs = next(r for r in rep["results"] if r["id"] == "eval-brand-semantics-verbatim-passthrough")
        assert _bs["passed"] and _bs["details"]["usage"]["total_tokens"] == 133 \
            and _bs["details"]["fabricated"] is False \
            and _bs["details"]["actual_semantics"]["tone_keywords"] == ["稳重", "专业", "可信赖"]
        _bsd = next(r for r in rep["results"]
                    if r["id"] == "eval-brand-semantics-dataurl-bearer-independent")
        assert _bsd["passed"] and _bsd["details"]["url_matches_recompute"] is True \
            and _bsd["details"]["sent_url_count"] == 1 and _bsd["details"]["requests"] == 1 \
            and _bsd["details"]["auth"] == "Bearer sk-eval-p56-secretkey9"
        assert (rep["not_evaluated"].get("agent") and rep["not_evaluated"].get("vision")
                and rep["not_evaluated"].get("gui"))
        # 通过率必须是从真实 passed/total 派生（防常量 0.85 回归）
        assert abs(sm["pass_rate"] - sm["passed"] / sm["executed_cases"]) < 1e-12
        assert sm["pass_rate"] != 0.85
        # 报告真实落盘并可按 id 读回
        got = eng.get_report(rep["report_id"])
        assert got["report_id"] == rep["report_id"] and len(got["results"]) == 185
        assert (tmp / "eval" / "reports" / f"{rep['report_id']}.json").exists()
        # 条码产物是真实 PNG 文件（非只写字段）
        bar = next(r for r in rep["results"] if r["id"] == "eval-packaging-barcode-ean13")
        assert bar["passed"] and bar["details"]["actual"]["png"] is True
        # 品牌夹具是真像素 PNG（评测真实驱动了像素而非字段核对）
        logo_png = tmp / "eval" / "artifacts" / "eval_brand_logo_rect.png"
        assert logo_png.exists() and logo_png.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
        # 印前夹具是真像素位图（PNG + JPEG magic bytes，评测真实驱动了色彩/分辨率解析）
        prgb = tmp / "eval" / "artifacts" / "eval_print_rgb72.png"
        pcmyk = tmp / "eval" / "artifacts" / "eval_print_cmyk300.jpg"
        assert prgb.exists() and prgb.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
        assert pcmyk.exists() and pcmyk.read_bytes()[:2] == b"\xff\xd8"
        # 知识检索真实落盘：coffee 条目 JSON 存在于评测私有 _kb 存储
        assert (tmp / "eval" / "_kb" / "kb_eval_coffee.json").exists()
        # 安装清单用例驱动的是真实文件系统：16 份沙箱各留一份真载荷（P49 三条 + P53 编译面/
        # 预检面 11 份 build 沙箱 + 静态开火臂的合规对照沙箱 + P59 预检校准 pfc），字节为夹具
        # 写入值——判分不是字段核对，把夹具删空这里必红；第 17 份 zaojing.exe 是预检占用桩（内容为 b"MZ"，
        # 只在 pb/hold 下充当"目标 exe 被别处占用"的探针靶子，不参与载荷判分）
        _mfp = sorted((tmp / "eval" / "_manifest").rglob("zaojing.exe"))
        assert len(_mfp) == 17, [str(p) for p in _mfp]
        assert sum(1 for p in _mfp if p.read_bytes() == b"MZ\x90\x00fixture") == 16, \
            [str(p) for p in _mfp if p.read_bytes() != b"MZ\x90\x00fixture"]
        assert [str(p.parent.name) for p in _mfp if p.read_bytes() == b"MZ"] == ["hold"]
        assert len(list((tmp / "eval" / "_manifest").rglob("上手指南.pdf"))) == 16
        # Phase 58 执行面夹具同样驱动的是真实文件系统：十座假根沙箱各留一份"用户既有数据"
        # （user-notes.json，绝不当测试沙箱用），真装出来的两枚 exe 只可能来自 full 模式。
        # 把 _exec_spawn 的落盘效应删空，这里与上面三条执行面用例一起必红。
        # 第十座 round2 是第 10 枚缺陷（证据件与用户数据同根、第二轮往返）专用沙箱。
        _xr = Path(tempfile.gettempdir()) / "_zj_eval_exec"
        assert sorted(p.parents[3].name for p in _xr.rglob("user-notes.json")) == \
            ["calib", "gates", "ghost", "install", "miss", "none", "plan", "real", "round2",
             "unins"], \
            sorted(str(p) for p in _xr.rglob("user-notes.json"))
        # full 模式只可能来自 install / real 两座沙箱（unins 那趟已被真卸流程归零）
        assert sum(1 for p in _xr.rglob("ZaojingAI.exe")) == 2, \
            [str(p) for p in _xr.rglob("ZaojingAI.exe")]
        assert sum(1 for p in _xr.rglob("unins000.exe")) == 2, \
            [str(p) for p in _xr.rglob("unins000.exe")]
        assert not (_xr / "unins" / "fake-local" / "Programs" / "造境科技").exists()
        assert (_xr / "ghost" / "fake-local" / "Programs").exists() is False
        # 新三引擎用例真实驱动了返回值（details 落证可核，非只置布尔位）
        rd = next(r for r in rep["results"] if r["id"] == "eval-review-dirty-matrix")
        assert rd["passed"] and rd["details"]["actual"]["score"] == 68
        rv = next(r for r in rep["results"] if r["id"] == "eval-revision-apply-math")
        assert rv["passed"] and rv["details"]["actual"]["title"] == "#8B0000"
        sh = next(r for r in rep["results"] if r["id"] == "eval-security-highrisk-no-overreport")
        assert sh["passed"] and sh["details"]["actual"]["status"] == "no_executor"
        # 工作流用例真实驱动了状态机（失败传播 n3 键缺席与错误文案逐字落证，非只置布尔位）
        fw = next(r for r in rep["results"] if r["id"] == "eval-workflow-fail-no-pass-on")
        assert fw["passed"] and fw["details"]["actual"]["n3_present"] is False \
            and fw["details"]["actual"]["error"] == "Tool 未注册: 'ghost'（可用: []）"
        # 素材用例真实驱动了入库/检索（自匹配余弦=1.0 与冷读回计数落证，非只置布尔位）
        _ar = next(r for r in rep["results"] if r["id"] == "eval-asset-search-rank")
        assert _ar["passed"] and _ar["details"]["actual"]["ranked"][0][0] == "coffee_mug.png" \
            and _ar["details"]["actual"]["ranked"][0][1] == 1.0
        _ac = next(r for r in rep["results"] if r["id"] == "eval-asset-delete-cascade")
        assert _ac["passed"] and _ac["details"]["actual"]["before"] == (2, 2, 1, 1) \
            and _ac["details"]["actual"]["after"] == (1, 1, 0, 1)
        # 素材夹具与真实缩略图/引擎 JSON 确落盘于评测私有目录
        assert (tmp / "eval" / "artifacts" / "coffee_mug.png").read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
        assert (tmp / "eval" / "artifacts" / "cooking_pot.jpg").read_bytes()[:2] == b"\xff\xd8"
        assert (tmp / "eval" / "_asset" / "add" / "thumbnails").exists() \
            and any((tmp / "eval" / "_asset" / "add" / "thumbnails").glob("*.png"))
        assert (tmp / "eval" / "_asset" / "search" / "assets.db.json").exists()
        # 恢复用例真实驱动了字节回归与 sha256 相符（独立复算比对落证）
        _rs = next(r for r in rep["results"] if r["id"] == "eval-recovery-snapshot-sha256")
        assert _rs["passed"] and _rs["details"]["actual"]["checksum_matches"] is True \
            and _rs["details"]["actual"]["size"] == 126
        _rr = next(r for r in rep["results"] if r["id"] == "eval-recovery-restore-revert")
        assert _rr["passed"] and _rr["details"]["actual"]["bytes_back"] is True \
            and _rr["details"]["actual"]["ghost"] is False
        assert (tmp / "eval" / "_recovery" / "task" / "states" / "task_task-77.json").exists()
        # 排版用例以自供夹具字体真实驱动度量：夹具 TTF 真编译落盘、逐用例重放索引真存在、度量真值落证
        _tf = tmp / "eval" / "_typo" / "_fix"
        assert (_tf / "zjfix_serif.ttf").read_bytes()[:4] == b"\x00\x01\x00\x00" \
            and (_tf / "zjfix_sans.ttf").read_bytes()[:4] == b"\x00\x01\x00\x00"
        assert (tmp / "eval" / "_typo" / "measure_serif" / "fonts_index.json").exists() \
            and (tmp / "eval" / "_typo" / "measure_serif" / "fonts" / "zjfix_serif.ttf").exists()
        _tm = next(r for r in rep["results"] if r["id"] == "eval-typography-measure-serif")
        assert _tm["passed"] and _tm["details"]["actual"]["advance_units"] == 3989 \
            and _tm["details"]["actual"]["width_pt"] == 95.736
        _tg = next(r for r in rep["results"] if r["id"] == "eval-typography-ghost-truth")
        assert _tg["passed"] and _tg["details"]["actual"]["ghost"] is None \
            and _tg["details"]["actual"]["keyerror"] == "'未找到字体: ZjNoSuchFont9'"
        # 条码用例以真渲染 PNG 逐点采样（位串全等与黑白像素落证，非只置布尔位；负控不落盘被核实）
        _b13 = next(r for r in rep["results"] if r["id"] == "eval-barcode-ean13-bitstring")
        assert _b13["passed"] and _b13["details"]["actual"]["bits_equal"] is True \
            and _b13["details"]["actual"]["px"] == (234, 104)
        assert (tmp / "eval" / "artifacts" / "eval_p34_bc13.png").read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
        _bp = next(r for r in rep["results"] if r["id"] == "eval-barcode-pixel-truth")
        assert _bp["passed"] and _bp["details"]["actual"]["samples"]["m0"] == 0 \
            and _bp["details"]["actual"]["samples"]["m1"] == 255
        _bn = next(r for r in rep["results"] if r["id"] == "eval-barcode-bad-checkdigit-msg")
        assert _bn["passed"] and _bn["details"]["actual"]["files"] == [False, False] \
            and not (tmp / "eval" / "artifacts" / "eval_p34_never.png").exists()
        # Phase 57 QR 真值落证：期望逐格取自本文件手抄 ISO 表，落盘 PNG 与尺子自校准串一并核实
        _bq1 = next(r for r in rep["results"] if r["id"] == "eval-barcode-qr-capacity-exact")
        assert _bq1["passed"] and _bq1["details"]["actual"]["bad"] == [] \
            and "L17->v1" in _bq1["details"]["actual"]["cells"] \
            and "L18->v2" in _bq1["details"]["actual"]["cells"] \
            and _bq1["details"]["expected"]["Table9_v1_ec"] == {"L": 7, "M": 10, "Q": 13, "H": 17}
        _bq2 = next(r for r in rep["results"] if r["id"] == "eval-barcode-qr-block-structure")
        assert _bq2["passed"] and _bq2["details"]["actual"]["bad"] == [] \
            and _bq2["details"]["actual"]["rows"][0] == "L1:19+7/1(19,7)" \
            and _bq2["details"]["expected"]["total(Table 7)"] == P57_QR_TOTAL_CW
        _bq3 = next(r for r in rep["results"] if r["id"] == "eval-barcode-qr-pixel-truth")
        assert _bq3["passed"] and _bq3["details"]["actual"]["mismatch_count"] == 0 \
            and _bq3["details"]["actual"]["size"] == (116, 116) \
            and _bq3["details"]["actual"]["samples"]["dark_fixed"] == 0 \
            and _bq3["details"]["actual"]["quiet_band_values"] == [255] \
            and (tmp / "eval" / "artifacts" / "eval_p57_qrpx.png").read_bytes()[:8] \
            == b"\x89PNG\r\n\x1a\n"
        _bq4 = next(r for r in rep["results"] if r["id"] == "eval-barcode-qr-mask-format-bch")
        assert _bq4["passed"] and _bq4["details"]["actual"]["ruler_ok"] is True \
            and _bq4["details"]["actual"]["ruler_reproduces"]["M0"] == P57_QR_FMT_M_M0 \
            and _bq4["details"]["actual"]["ruler_reproduces"]["L0"] == P57_QR_FMT_L_M0 \
            and _bq4["details"]["actual"]["ruler_reproduces"]["v7"] == P57_QR_VER_V7 \
            and all(c["fmt_equal"] for c in _bq4["details"]["actual"]["cases"]) \
            and all(c["ver_match"] for c in _bq4["details"]["actual"]["cases"])
        _bq5 = next(r for r in rep["results"] if r["id"] == "eval-barcode-qr-honest-reject")
        assert _bq5["passed"] and _bq5["details"]["actual"]["empty"] == "QR 载荷不能为空" \
            and _bq5["details"]["actual"]["badlvl"] == "非法纠错等级 X，只接受 L/M/Q/H" \
            and _bq5["details"]["actual"]["over_m"].startswith(
                "载荷超出本引擎已实现的 QR 版本范围（v1..v10，纠错等级 M）：400 字符") \
            and "mode=Alphanumeric" in _bq5["details"]["actual"]["over_h"] \
            and not (tmp / "eval" / "artifacts" / "eval_p57_qr_never.png").exists()
        _bdg = next(r for r in rep["results"] if r["id"] == "eval-barcode-honest-degradation")
        assert _bdg["passed"] and _bdg["details"]["actual"]["c128"][0] == "needs_external" \
            and _bdg["details"]["actual"]["c128"][2] is False \
            and "Code-128" in _bdg["details"]["actual"]["c128"][3] \
            and not (tmp / "eval" / "artifacts" / "eval_p57_c128.png").exists() \
            and not (tmp / "eval" / "artifacts" / "eval_p57_datamatrix.png").exists()
        # 拓扑用例几何全字段精确（度序列/裁切线 870/rejected 逐项落证）
        _tb = next(r for r in rep["results"] if r["id"] == "eval-topology-box9-full")
        assert _tb["passed"] and _tb["details"]["actual"]["cut"] == 870.0 \
            and _tb["details"]["actual"]["relations"] == 8
        _tj = next(r for r in rep["results"] if r["id"] == "eval-topology-rejected-honest")
        assert _tj["passed"] and _tj["details"]["actual"]["mix"][2][0]["id"] == "neg" \
            and _tj["details"]["actual"]["empty"] == "至少需要一个面板"
        # 授权用例真实驱动了签发/验签（报错原文与静默丢字符真值落证，非只置布尔位）
        _lk = next(r for r in rep["results"] if r["id"] == "eval-license-tamper-format-exact")
        assert _lk["passed"] and _lk["details"]["actual"]["tamper"][1] == "签名校验失败：密钥被篡改或非本产品签发" \
            and _lk["details"]["actual"]["formats"][3][1] == "签名校验失败：密钥被篡改或非本产品签发" \
            and _lk["details"]["actual"]["array_payload"] == [False, "载荷结构无效：签名载荷必须是 JSON 对象"]
        _lb = next(r for r in rep["results"] if r["id"] == "eval-license-machine-bind")
        assert _lb["passed"] and _lb["details"]["actual"]["match"] is True \
            and _lb["details"]["actual"]["mismatch"][1] == "机器绑定不符：该密钥不适用于本机（期望前缀 ZJ36）"
        # 配置用例真实驱动了落盘与迁移（沙箱文件在盘、迁移四字段落证）
        _cm = next(r for r in rep["results"] if r["id"] == "eval-config-migration")
        assert _cm["passed"] and _cm["details"]["actual"]["marker"] == "marker-kept" \
            and _cm["details"]["actual"]["saved_version"] == "1.0.0"
        assert "zj_probe_key" in (tmp / "eval" / "_config" / "migrate" / "user-settings.json").read_text(encoding="utf-8")
        assert (tmp / "eval" / "_config" / "readback" / "user-settings.json").is_file()
        # 路由用例真实驱动了选路与熔断（平票裁决、预算排除、开环不抛错与无环到点复口落证）
        _rc = next(r for r in rep["results"] if r["id"] == "eval-routing-cost-tie")
        assert _rc["passed"] and _rc["details"]["actual"]["picked"] == "p_local" \
            and _rc["details"]["actual"]["est"] == (0.002 + 0.006) / 2
        _rb = next(r for r in rep["results"] if r["id"] == "eval-routing-budget-breaker")
        assert _rb["passed"] and _rb["details"]["actual"]["all_over"] is None \
            and _rb["details"]["actual"]["privacy_after_open"] == "p_cloud" \
            and _rb["details"]["actual"]["breaker_exc"] is None \
            and _rb["details"]["actual"]["rearm_without_loop"] is True
        assert (tmp / "eval" / "_router" / "cost" / "registry.yaml").is_file()
        # Phase 37：update/nas/provider 用例真实驱动了下载应用、缓存命中与熔断零 HTTP
        # （沙箱文件与 mock 请求计数落证，非只置布尔位）
        _ua = next(r for r in rep["results"] if r["id"] == "eval-update-download-apply")
        assert _ua["passed"] and _ua["details"]["actual"]["dl"] == ("downloaded", "sha256_match", 26) \
            and _ua["details"]["actual"]["seen"] == ["/manifest.json", "/manifest.json", "/art.bin"]
        assert (tmp / "eval" / "_update" / "chain" / "update-config.json").exists()
        assert (tmp / "eval" / "_update" / "chain_fx" / "app.bin").read_bytes() \
            == b"ZAOJING-P37-ARTIFACT-BYTES"
        _nc = next(r for r in rep["results"] if r["id"] == "eval-nas-cache-cold")
        assert _nc["passed"] and _nc["details"]["actual"]["cache"] == "hit" \
            and _nc["details"]["actual"]["quads_equal"] is True
        assert (tmp / "eval" / "_nas" / "cache" / "cache").is_dir()
        _pb = next(r for r in rep["results"] if r["id"] == "eval-provider-auth-breaker")
        assert _pb["passed"] and _pb["details"]["actual"]["breaker"] == ("error", "circuit_open", 0)
        _pk_raw = (tmp / "eval" / "_provider" / "keys" / "providers.json").read_text(encoding="utf-8")
        assert "sk-eval-p37-secret" not in _pk_raw and "api_key_dpapi" in _pk_raw
        # Phase 38：compare/bg_removal 用例真实驱动了六类 diff 与泛洪清像素（夹具与输出
        # 真 PNG 落盘于评测私有 _bg 沙箱，非只置布尔位）
        _cz = next(r for r in rep["results"] if r["id"] == "eval-compare-field-categories")
        assert _cz["passed"] and _cz["details"]["actual"]["summary"]["by_category"] \
            == {"geometry": 2, "text": 1, "color": 1, "asset": 1, "layer": 1, "visual": 1}
        _bf = next(r for r in rep["results"] if r["id"] == "eval-bgre-flood-exact")
        assert _bf["passed"] and _bf["details"]["actual"]["cleared"] == 8400 \
            and _bf["details"]["actual"]["alphas"] == [0, 255, 255]
        assert (tmp / "eval" / "_bg" / "_fix" / "subject.png").read_bytes()[:8] \
            == b"\x89PNG\r\n\x1a\n"
        assert (tmp / "eval" / "_bg" / "flood" / "no_bg.png").read_bytes()[:8] \
            == b"\x89PNG\r\n\x1a\n"
        # 两次运行对比：同一用例集全量共有、无翻转
        rep2 = eng.run_full()
        cmp = eng.compare(rep["report_id"], rep2["report_id"])
        assert cmp["shared_cases"] == 185 and cmp["flips"] == [] and cmp["pass_rate_delta"] == 0.0
        assert len(eng.list_reports()) == 2
        # 建议生成基于真实指标
        recs = eng.recommendations(rep)
        assert recs and recs[0].startswith("[OK]")
        # 判分校准的反证：手动构造 calibration_error 报告 → recommendations 必须给 BLOCK
        bad = json.loads(json.dumps(rep)); bad["calibration_error"] = True
        assert eng.recommendations(bad)[0].startswith("[BLOCK]")
        # 非法/不存在报告诚实报错，不伪造内容
        try:
            eng.get_report("eval-not-exist"); raise AssertionError("应抛 EvaluationError")
        except EvaluationError:
            pass
        try:
            eng.get_report("../outside"); raise AssertionError("应拒绝路径穿越")
        except EvaluationError:
            pass
        # Phase 31 语境无关性 + Phase 32/33 扩展：服务里 /eval/run 是 async 端点（调用线程已有运行中
        # 事件循环），workflow/asset/recovery 用例若直呼 asyncio.run() 必炸——此处在嵌套运行循环内
        # 重跑全量，异步域与 typography、Phase 36 起 model_routing（Phase 36~59 为熔断开环的
        # create_task 无环抛错真值；Phase 60 根治后改钉"无环也能开环与到点复口"，
        # 两语境仍须判全同）用例仍须全过且 crashed_cases=0（无此修复时必红）
        import asyncio as _aio

        async def _nested_run():
            return eng.run_full()
        rep_nested = _aio.run(_nested_run())
        _nsm = rep_nested["summary"]
        assert _nsm["crashed_cases"] == 0 and _nsm["executed_cases"] == 151, _nsm
        assert rep_nested["categories"]["workflow"] == {"total": 6, "passed": 6, "failed": 0, "pass_rate": 1.0}, rep_nested["categories"]["workflow"]
        assert rep_nested["categories"]["asset"] == {"total": 6, "passed": 6, "failed": 0, "pass_rate": 1.0}, rep_nested["categories"]["asset"]
        assert rep_nested["categories"]["recovery"] == {"total": 6, "passed": 6, "failed": 0, "pass_rate": 1.0}, rep_nested["categories"]["recovery"]
        assert rep_nested["categories"]["typography"] == {"total": 6, "passed": 6, "failed": 0, "pass_rate": 1.0}, rep_nested["categories"]["typography"]
        assert rep_nested["categories"]["barcode"] == _bc14 \
            and rep_nested["categories"]["topology"] == _five, \
            rep_nested["categories"].get("barcode")
        _nfour = {"total": 4, "passed": 4, "failed": 0, "pass_rate": 1.0}
        assert rep_nested["categories"]["license"] == _nfour \
            and rep_nested["categories"]["config"] == _nfour \
            and rep_nested["categories"]["model_routing"] == _nfour
        assert rep_nested["categories"]["update"] == _nfour \
            and rep_nested["categories"]["nas"] == _nfour \
            and rep_nested["categories"]["provider"] == _nfour
        assert rep_nested["categories"]["compare"] == _nfour \
            and rep_nested["categories"]["bg_removal"] == _nfour \
            and rep_nested["categories"]["panel_recognition"] == _nfour \
            and rep_nested["categories"]["desktop_hub"] == _nfour
        assert rep_nested["categories"]["brand_semantics"] == _five, \
            rep_nested["categories"].get("brand_semantics")
        assert rep_nested["categories"]["installer_execution"] == _six, \
            rep_nested["categories"].get("installer_execution")
        assert rep_nested["categories"]["installer_manifest"] == _six_mf, \
            rep_nested["categories"]["installer_manifest"]
        # Phase 59 路线 B：守护面升为独立黄金域，编译面/预检面从清单域拆出各自挂证；
        # 互斥链在嵌套语境（主线程已有运行中循环）下须与顶层同值，否则 kernel32 句柄
        # 泄漏会伪装成"运行中"而 here 必红
        assert rep_nested["categories"]["installer_artifact_build"] == _three \
            and rep_nested["categories"]["installer_preflight"] == _two_mf, \
            rep_nested["categories"].get("installer_preflight")
        assert rep_nested["categories"]["runtime_guard"] == _five_gr, \
            rep_nested["categories"].get("runtime_guard")
        # Phase 79：Adobe 桥三枚异步协议往返用例在嵌套语境（主线程已有运行中循环）下
        # 仍须经 _async_run 独立线程起循环并全等判分——若 _async_run 退化或直接 await，
        # 这里会因 "cannot be called from a running event loop" 崩溃而必红
        _none_br = {"total": 1, "passed": 1, "failed": 0, "pass_rate": 1.0}
        assert rep_nested["categories"]["bridge_handshake"] == _none_br, \
            rep_nested["categories"].get("bridge_handshake")
        assert rep_nested["categories"]["bridge_cdr_read"] == _none_br, \
            rep_nested["categories"].get("bridge_cdr_read")
        assert rep_nested["categories"]["bridge_cdr_write"] == _none_br, \
            rep_nested["categories"].get("bridge_cdr_write")
        # Phase 80：服务侧三黄金域在嵌套语境（主线程已有运行中循环）下仍须各自 1/1 通过，
        # 且 details.actual 顶层/嵌套两次逐字段全等——用例走私有 _sr 沙箱先清后建、判分字段
        # 不含绝对路径/时间戳，故语境无关；若实现渗入机器实况或串扰残留，这里必红。
        assert rep_nested["categories"]["feature_flags"] == _none_br, \
            rep_nested["categories"].get("feature_flags")
        assert rep_nested["categories"]["tool_registry"] == _none_br, \
            rep_nested["categories"].get("tool_registry")
        assert rep_nested["categories"]["skill_runtime"] == _none_br, \
            rep_nested["categories"].get("skill_runtime")
        for _pid in ("eval-flags-seed-merge-persist", "eval-tool-registry-runnable-derive",
                     "eval-skill-validate-and-load"):
            _pn = next(r for r in rep_nested["results"] if r["id"] == _pid)
            _pt = next(r for r in rep["results"] if r["id"] == _pid)
            assert _pn["details"]["actual"] == _pt["details"]["actual"], (_pid, _pn["details"])
        # Phase 49 接缝桩还原可证伪实证 + Phase 53 编译面/预检面扩展：安装清单十条用例在
        # "桩实验之后再全量重跑"的语境下判分细节必须与首轮逐字段全等——若 finally 还原漏了
        # 任何一个桩（_token_elevation/_disk_free/_exclusive_open/_write_probe），第二轮的
        # verdict/codes 会被污染，这里必红（独立自测看不到这种状态泄漏）；同时凡随本机而异的
        # 量（磁盘余量、真令牌、Program Files 落点、CRLF 字节数）都只以关系式判或走显式桩，
        # 故 details.actual 顶层/嵌套两次全等本身就是"判分不含机器实况"的落证
        for _pid in ("eval-manifest-clean-zero-findings", "eval-manifest-broken-findings-exact",
                     "eval-manifest-seam-stub-falsification",
                     "eval-manifest-rejects-and-installed",
                     "eval-manifest-build-evidence-verified",
                     "eval-manifest-build-evidence-arms",
                     "eval-manifest-build-warnings-completeness",
                     "eval-manifest-preflight-ready-readonly",
                     "eval-manifest-preflight-blocked-stubs",
                     "eval-manifest-static-fire-arms",
                     "eval-manifest-registry-uninstall-arms"):
            _pn = next(r for r in rep_nested["results"] if r["id"] == _pid)
            _pt = next(r for r in rep["results"] if r["id"] == _pid)
            assert _pn["details"]["actual"] == _pt["details"]["actual"], (_pid, _pn["details"])
        # Phase 59 守护面判分语境无关实证：写探针/磁盘阈值/互斥链/诊断四臂的 details.actual
        # 在顶层与嵌套两次须逐字段全等（沙箱根→<SB>、随机锁名→<LOCK> 已归一，连续量不入
        # 判分字段）。端口臂**刻意不入此列**：它的 port_echo 逐轮随机（OS 指派 bind(0)），
        # 归一端口号就等于把它自己的夹具形制写进判分——把"两次全等"的声称范围说清楚，
        # 而不是悄悄少一条。
        for _pid in ("eval-guard-write-probe-three-arms", "eval-guard-disk-threshold-two-sided",
                     "eval-guard-single-instance-mutex-chain",
                     "eval-guard-diagnostic-derived-and-degrades"):
            _pn = next(r for r in rep_nested["results"] if r["id"] == _pid)
            _pt = next(r for r in rep["results"] if r["id"] == _pid)
            assert _pn["details"]["actual"] == _pt["details"]["actual"], (_pid, _pn["details"])
        # Phase 37 mock HTTP 判分语境无关实证：嵌套与顶层两次的三域判分细节全等
        for _pid in ("eval-update-download-apply", "eval-nas-cache-cold",
                     "eval-provider-auth-breaker"):
            _pn = next(r for r in rep_nested["results"] if r["id"] == _pid)
            _pt = next(r for r in rep["results"] if r["id"] == _pid)
            assert _pn["details"]["actual"] == _pt["details"]["actual"], (_pid, _pn["details"])
        # Phase 38 判分语境无关实证：嵌套与顶层两次的 compare/bg_removal 判分细节全等
        # （纯函数与真像素泛洪理应零语境依赖，此处以实际落证钉死）
        for _pid in ("eval-compare-field-categories", "eval-bgre-flood-exact",
                     "eval-bgre-default-determinism", "eval-panel-grid-inversion",
                     "eval-panel-determinism"):
            _pn = next(r for r in rep_nested["results"] if r["id"] == _pid)
            _pt = next(r for r in rep["results"] if r["id"] == _pid)
            assert _pn["details"]["actual"] == _pt["details"]["actual"], (_pid, _pn["details"])
        # Phase 50 判分语境无关实证：desktop_hub 四条用例走真实 urllib + 进程内真 HTTP，
        # 端口号经 _hub_norm 归一后须与顶层逐字段全等（若判分字段里渗入端口/耗时，
        # 或窗口行里渗入上一轮数字，这里必红）
        for _pid in ("eval-hub-transport-fidelity", "eval-hub-derive-not-invent",
                     "eval-hub-degradation-three-forms", "eval-hub-action-single-post"):
            _pn = next(r for r in rep_nested["results"] if r["id"] == _pid)
            _pt = next(r for r in rep["results"] if r["id"] == _pid)
            assert _pn["details"]["actual"] == _pt["details"]["actual"], (_pid, _pn["details"])
        # Phase 58 判分语境无关实证：执行器五域用例在沙箱（系统临时根）内跑，判分细节里凡
        # 随机器而异的量（绝对路径、时间戳、日志字节之外的现场量）一律以 _exec_rel 归一为
        # 沙箱相对 posix 名后才入 actual，故顶层/嵌套两次逐字段全等本身就是"判分不含机器实况"的落证
        for _pid in ("eval-exec-plan-disk-derived", "eval-exec-gates-refuse-zero-side-effects",
                     "eval-exec-install-verified-from-diff",
                     "eval-exec-verdict-ignores-self-report",
                     "eval-exec-uninstall-three-forms-and-reconcile"):
            _pn = next(r for r in rep_nested["results"] if r["id"] == _pid)
            _pt = next(r for r in rep["results"] if r["id"] == _pid)
            assert _pn["details"]["actual"] == _pt["details"]["actual"], (_pid, _pn["details"])
        # 熔断判分语境无关实证：嵌套（主线程有循环）与顶层（无循环）两次的判分细节全等
        # （Phase 60 根治后无环语境也不再抛错，此处全等的是"开环/排除/复口/环内"四态真值）
        _nb = next(r for r in rep_nested["results"] if r["id"] == "eval-routing-budget-breaker")
        _tb2 = next(r for r in rep["results"] if r["id"] == "eval-routing-budget-breaker")
        assert _nb["details"]["actual"] == _tb2["details"]["actual"], (_nb["details"], _tb2["details"])
        # Phase 47：面板识别夹具是真 PNG 且逐用例私有沙箱先清后建（非只置布尔位）
        _pg = next(r for r in rep["results"] if r["id"] == "eval-panel-grid-inversion")
        assert _pg["passed"] and _pg["details"]["actual"]["areas"] == [2535] * 6 \
            and _pg["details"]["actual"]["adj"] == [7, [3, 3, 2, 2, 2, 2], 1]
        assert (tmp / "eval" / "_panel" / "grid" / "grid3x2.png").read_bytes()[:8] \
            == b"\x89PNG\r\n\x1a\n"
        _ph = next(r for r in rep["results"] if r["id"] == "eval-panel-honest-empty")
        assert _ph["passed"] and _ph["details"]["actual"]["counts"] == [0, 0, 0, 6] \
            and _ph["details"]["actual"]["same_face"] is True
        print(f"[OK] evaluation_engine 自测通过：{len(rep['results'])} 用例"
              f"（{sm['executed_cases']} 真实评测 + {len(rep['calibration'])} 判分校准），"
              f"pass_rate={sm['pass_rate']}，报告落盘/对比/建议/负控全部坐实")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
