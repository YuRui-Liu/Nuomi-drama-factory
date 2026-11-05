# Agent + 画布整集验收记录

状态：**未通过整集真实成片验收**。代码测试与真实生成验收分开记录，不能互相替代。

## 用户确认后的验收口径

用户已指出下列历史试跑仍未证明真实群组创作与集画布链路。其任务和媒体回执仅作为历史诊断，不计作新方案端到端通过。已批准的设计为常驻成员按主创调度、点名或任务事件参与，构思资产建立可执行出图节点，导演编辑与制作归属对应集画布。

实施依据为 `docs/superpowers/specs/2026-10-18-agent-collaboration-canvas-integration-design.md` 与对应实现计划。最终须新建项目，从产品群聊完成专业分工、资产出图、分集交接和真实成片；不能用人工 API 补节点替代产品能力。当前正在接通真实专业任务，尚未宣称完整协作已接通。

后续实现进度（尚非产品真测）：权威画布作用域已通过后端 78、前端 120 项定向测试及两阶段审查；常驻成员／固定主创与点名讨论后端通过 39 项相关测试及两阶段审查；根级模型额度通过 37 项 AgentRuns/API 测试及两阶段审查。独立专业子运行正在实施，前端群聊接线、资产出图闭环、分集导演协作及新成片均未验收。当前 API 尚未为这些增量重启，不将测试结果当作当前浏览器已具备的功能。

## 同步与链路

- `feat/canvas` 已拉取并合并远端，合并提交 `6ffb2b0`；原工作区修改已恢复，autostash 备份保留。
- Agent 节点接入运行面板；导演生产入口建立固定版本的组分镜 → 组视频 → 整集合成画布图。
- 新执行入口不再提交旧 `production_batch`，历史任务保留对账能力。
- 重试仅针对失败节点；提交结果未知保持对账状态，避免重复生成。
- 合成固定导演版本、完整组集合与视频来源；幂等重放保留原回执。

## 真实验收项目

- 项目：最后一盏灯 · Agent画布整集验收
- ID：`01M55D4506KC3B2WSPSHCX9JBT`
- 画布：`1afe547a80a04e4a96b731680213ab62`
- 简报文档：`2bf462a89421441d97c28f9c3fc7810c`
- 简报版本：`5f5be861eea84352979615a71f6fc531`
- 范围：90 秒原创完整单集，雨夜停电楼道内，电工林舟帮助陈奶奶修复作为夜间送药信号的门灯。

## 实际配置核实

- 图像：`grsai-main` / `gpt-image-2`，已启用且凭据已配置。
- 视频：MiniMax 导演台，保存模型 `runninghub:minimax-h3`，RunningHub 凭据已配置。
- 不应以统一网关的 `mediaConfigured=false` 判断上述媒体服务未配置。
- 用户指定文本使用 Codex。实际已有八个后台任务角色均配置为 Codex / `gpt-6-luna`，剧本创作为 medium、导演规划为 high。
- 新增的 `creation_agent_decision` 当时未进入可配置角色列表，代码又仅允许 `model_api`，所以错误使用了数据库中的 `deepseek-v4-flash-vision-exp`。这不是用户没有配置 Codex；需要补齐新画布 Agent 的 Codex 接入。

## 当前阻塞与对账

Codex 接入已补齐：新增角色默认继承已有 `script_creation` 路由；实际服务确认 `codex / gpt-6-luna / medium`。严格模式单次 CLI 调用、无自动修复重试，禁用原生操作工具；决策通过专用 wire schema 解码后再次执行原有严格校验。相关 155 项测试通过，独立审查未发现残留阻塞问题。

新讨论请求 `5070c385-2043-47b9-bca4-129540eb52e8` 已由 Codex 成功执行，POST HTTP 200 / `completed`，历史中的 user 与 assistant 均 completed，diagnostic 为空。此前 DeepSeek 失败不是当前 Codex 路由的失败；保留下列旧回执供对账，不重复提交。

首个真实节点委托 `481cdb7882f94b42bacf95bc30c62d41` 仅授权立意节点 `generate` 一次；运行 `2a187db04be049308538529062bc0597` 收到模型响应后以 `model_response_invalid` 阻塞。核对输出和节点生成历史均为空，未提交生成任务。已发现并修正提示中的通用参数与节点精确参数合约冲突，并补齐安全阶段诊断；由于原始响应未保存，不能断定该冲突是此失败的唯一原因。修复相关 66 项测试通过、独立复审通过，待新运行验证。

修复后的独立委托 `5b5c6354eb1b4948b291521f2dc7a219` 保持相同单节点授权。运行 `2232bc51e710452da77656dd62132f94` 已 `delivered`；节点运行 `c2e9029414dc43c9815a87c394ae97c5` 已 `succeeded`，画布版本 13→14。真实立意 workflow `61aa21e5-59b4-4e52-b713-61920e6e13d1` / task `383791f8-b332-454d-b221-48a40d130341` 已 completed，生成三项候选。此结果证明 Codex 决策 → 授权节点执行 → 内容生成回写已实际运行成功。

已采用候选 `4c5ccc0d483f4c7092789982d2e5cb8e` / revision `356ffd2d54e0454daf84969547fddaa9`。单集规划 run `b9936657ed8c4936a158da079f890468` / task `dabce29e-a434-4796-af8c-e69b7a774f0c` 已完成，产出大纲、人物、场景、道具及本集梗概五份文档；逐份审阅确认保持原简报范围。

剧本 run `4209f49fedc24069ae7865be123888e2` / task `072859e4-26c6-442b-89d1-8457d79b7d77` 已完成；文档 `654ff5cb14a849b0b09eea56cf4b8aae` / revision `242f14e4d8a74a68a65b67f010660604` 已审阅并采用。画布增加 `acceptance-screenplay-e01` 与 `acceptance-director-e01`，固定该版本，保留原有节点。

导演节点 run `3e6c27b3235e46bf84a3061769f01398` 在提交前被正式门禁拒绝：`DIRECTOR_WORKSPACE_NEEDS_REVIEW`，要求当前版本真实一致性审查完成；无 handoff 生成任务。先补齐审查，再重试该失败节点，不能绕过门禁。

实际一致性审查 `0a4ca8a6ec5540d08bad7d0c1c8b599f` / task `3bf6c348-0eaf-4c83-b8c2-83e3fdc4a7af` 已 completed，0 issues。随后导演节点第二次尝试建立 handoff `892db0dfa2174fa79fe6b20230855c8a`，source revision 1；语义任务 `7c3cdf7b-7b3c-4cc1-8584-26651d85f69f` 完成，正式采用语义版本 `01M56BFY8WXH8CXHKXK7R95D78`（一场、四个 beat）。导演规划 task `6fc1b053-62aa-4681-be87-5fd685c219f6` 已启动。

真测发现待审阅的 `needs_review` 被画布错误归为 failed；已修正为保留原提交回执继续对账，并提供只恢复该误判状态的正式重试路径。34 项回归及复审通过。实际调用正式重试入口后，同一 attempt `fafa1df63b5740c9b21371103a578d4f` 与 handoff 保持不变，run 已 succeeded，attempt 总数仍为 2，没有第三次提交。

导演 task 已完成，候选 `01M56C7C0K0VW3AN5KQB5ZAFMW` 结构校验与创意复核通过，但总时长仅 58 秒（单组八镜），未直接采用。经正式 edits 创建 `01M56CM4Y42G17NZ6RYF2MVJ3H`，八镜调整为 10/12/13/12/15/12/10/6 秒。正式创意审查 task `9e15787e-bcc6-44cf-9495-2dc7a42b5920` 完成，产出 `01M56CN84PMSHQ4FY890FSBAXT`：90 秒、来源仍为当前已采用剧本、结构与创意审查 passed、production_ready=true，四项审查问题均 resolved。此版本已通过正式 activate 采用并刷新 references；浏览器也已显示该已采用版本及八镜时长。

真实讨论请求 `e8973ad6-5c41-4704-9653-e5f55c7c912a` 仅提交一次，回执及历史均记录 `submission_unknown`，安全诊断为 `provider_rejected` / `ModelHTTPError` / HTTP 402；没有 assistant 回复。未保存服务商原始响应，不能据此确定具体账户原因。

此前请求 `031d65bd-8a5f-4eee-9b73-6943b335dbae` 也为未知回执。两者均未重复提交。

Codex 路由、真实立意生成、单集规划、剧本生成与采纳、正式一致性审查、90 秒导演规划与采纳现已通过。新项目角色和场景 catalog 初始为空，正在走正式素材建立与参考图生成流程；真实组图、组视频及整集合成仍待完成。没有成片文件，媒体服务真实生成能力尚未验收。不得重发上述未知请求。

## 已有验证

- 真实界面点击“创建制作节点”发现 HTTP 500：接口误读 `NarrativeGroupPlan.group_id`，实际字段为 `id`。已修正，并把路由测试中的自由模拟对象替换为经过验证的真实 `NarrativeGroupPlan` / `ShotPlan`，修前复现失败，修后生产图与路由相关 18 项通过。等待媒体任务安全间隙重启服务后复测。
- 场景首次真实 Grsai 生成任务 `ce7eeada-49f6-4b09-9df0-e84dc722df3d` 完成，但目视发现项目默认古装风格与亮灯状态不符合简报，未采用。通过项目级 API 将风格改为 `realistic`，修订后任务 `41a9b1f2-7bad-41e3-9fc9-1ac45d897105` 完成；版本 `master-20261018T022210229383Z` 经目视复核为现代雨夜门厅、壁灯熄灭，已正式采用。生产 slot 的 `qc_passed` 仅代表文件校验，不能替代这次目视检查。
- 已核实 90 秒叙事组由正式组 runner 划为八段 H3 请求，符合每段 4–15 秒约束。另发现缺省 `external_tts` 音频策略与新画布节点链不完整匹配，正在补齐冻结的音频策略，尚未提交真实组视频。

- 后端创作与编排等合并测试集曾通过 1494 项；后续组生产、幂等、合成与重试修复相关 247 项通过，创作与整集合成测试集 1283 项通过（存在重叠，不累加）。这些结果均早于新增 Codex 接入。
- 前端 TypeScript 检查与相关组件测试通过；诊断消息与状态文案增量 23 项通过。最终 Codex 增量测试结果以任务交付记录为准。
- 本地浏览器已验证新项目画布、助手会话切换入口与持久化的讨论消息能够加载。

## 恢复验收

### 新群聊链路实际进度（2026-10-18）

- 已从产品 UI 新建项目 `01M56MGSFHPJ821FZ6Z4AWCY1B`（`agent_group_episode_acceptance_20261018`），构思画布 `ae2af8c1093d4211b74b73abb38f0d13`，会话“主创与资产设计组”。采用主创制片统筹及常驻美术资产师、主笔、剧本编辑；项目固定风格已通过 UI 保存为 `realistic`。
- API 已统一 reload 至 session `74960`，加载本轮正式剧本与摄影机参数修复；reload 本身不代表媒体生成或整集验收通过。
- 已通过实际群聊提交三个资产设计目标。主创 run `fe3f7b5da54243a5be05d8d421198aea` 仅调度美术资产师子 run `1c84d57a00714cb19ada2713aa1c1138`，未调用主笔或编辑。
- 本次未通过：子 run 返回三个动作但均被参数校验拒绝，记录为 `blocked / model_response_invalid / decision_contract_invalid`，主创为 `partial`；画布尚无资产节点。发现模型参数说明包含非法 `source_mode` 字段、空目标与来源互斥参数说明不完整，正在修复。不得将该次讨论回复当成资产交付。
- 当前新项目尚未提交图片或视频生成，也没有完成的正式剧本、导演方案或成片。此前项目的媒体与文字成果不计入本次新链路验收。
- 用户要求功能优先、集中测试，并授权最多六路并行；当前为 root 加五个子 Agent 共六路。App 升级使旧 API/前端服务停止，现已重新启动。第二次群聊资产设计 run `43c8639746984a2da289f1afb0e476da` 的结果为中断且未知；root 已从真实 UI 停止该 run，保留未知轮次，不自动重发。新项目尚无真实媒体生成，该状态不代表资产链路或整集验收通过。
- 第三次真实请求：主创 run `4445a0b0dfa54cd7861e695ab32ea0fb` 仅调度美术资产师 `6944e596bda0425a8d124be7dbcabaf8`，两者均 `delivered`；浏览器看到林舟、陈奶奶、居民楼门厅三个内容组，各有设计文本与真实图像生成节点，任务卡有三个出图候选。尚未授权媒体生成。
- 第三次成果的语义验收未通过：子任务缺少原始简报中的人物年龄、职业服装及雨夜停电要求，节点内容把这些已明确要求写成“未确认”。已定位为父任务只传模型概括的子目标、未固定继承原始用户简报；正在修复并通过局部委托修订，不能把节点存在等同于正确交付。
- 独立代码审查发现同一任务连续落图导致先前资产候选误报过期（整画布 revision 门禁）；正在改为具体节点、设计和参考版本验证并增加多候选顺序授权回归。导演工作空间、原生音频与集画布参考配置的模拟测试不计为真实成片验收。
- 上述简报继承与多候选授权修复已通过定向测试和独立复审。真实 UI 已点名美术资产师、选定“林舟”内容组提交局部修订，run `4bc3d0d4662248beae852e68cfef04f5` 的讨论回复符合明确设定，但动作被 `decision_contract_invalid` 拒绝，尚未产生修正节点；原始响应未保留，无法从通用 `value_error` 判断具体参数。后续增加稳定且不泄漏原始值的参数诊断。
- 该局部任务点击一次“检查并恢复”后仍为 blocked、仅一次模型调用；已定位任务卡控制桥未接纳 blocked 状态，虽然底层 AgentRuns 支持显式恢复已收到但无效的响应。正在修复控制桥与可见错误提示，未知提交仍禁止自动重发。
- 资产布局已增加持久化坐标和避让测试；正在补旧组零坐标子节点的兼容。后端短暂断连导致项目跳回首页的问题已修复并通过六项定向测试。前端实际构建类型检查 `tsc -b` 已通过，不能用仅检查引用根配置的 `tsc --noEmit` 替代。
- 引用配置独立审查发现两项待修问题：分镜配置改变后可能复用旧图；旧视频配置对话框可能写入新导演版本。分别修复精确引用复用证据与保存/上传的导演版本门禁后，再进入媒体真测。
- 局部任务 `4bc3d0d4662248beae852e68cfef04f5` 已在修复后从 UI 显式恢复，同一 run 第二回合为 `applied / delivered`。真实画布和任务卡出现“林舟角色资产 v2”，内容明确为 30 岁维修电工、短黑发和蓝灰工装；旧草案保留，其他组未被本次委托改动。
- 已从该候选任务卡授权单张正式出图，创建 continuation run `abe794ce9242449899c50829a34fbe9d`。此 run 在 `decision_saved` 后动作阶段中断，模型回执为 `response_received`，泛化错误却标为 `model_submission_unknown`；正核对节点和媒体回执，未重复提交，不能宣称图片已生成。
- 旧 `/director-workspace/production` 新提交已禁用，仅保留精确历史 scope 回执；旧 runner 不再启动批生产。独立复审确认原接口旁路关闭。adopt 已统一激活、物化和引用刷新，另补激活落盘后刷新失败的重试恢复。分镜引用复用修复已通过独立复审。
- API 重启后跳首页的真实原因另定位为开发监视器 `assign('/')`，已改为重载当前 URL；此前仅修项目列表错误门禁不足以解决这一触发条件。测试文件热更新导致的频繁整页重载也已定位，正在排除开发期无关刷新。
- 林舟续跑的实际失败原因已查明：本地图片任务进入 `freezone_gen` 后错误走到旧 HuiMeng 网关凭证检查，报 `HUIMENGI_API_KEY` 未配置；用户已有 Grsai 配置，不能要求再配置旧网关作为解决办法。没有生成候选，节点回执为 terminal failed，正在修正到已配置的 Grsai 媒体路由。该失败授权已消耗，不会伪装成未发送并复用。
- 陈奶奶局部任务 run `8d26590fc01d401381a2a1e46d3aaed1` 的冻结输入为空，但模型动作引用了不存在的输入索引，执行前拒绝且无动作副作用。正在把索引范围校验前移到决策保存之前，保留已消费回合和错误历史，不静默丢弃模型引用。
- 用户新增验收要求：非头像资产不得默认方图。默认按用途区分头像 1:1、人物半身／全身 2:3、场景 16:9；明确比例应保留，并验证生成请求实际消费比例。截图中居民楼门厅默认 1:1 以及场景节点误标“角色设计”均待修。
- 摄影机参数已接入新资产设计默认值：头像 75mm f/4、半身及旧 `body` 用途 50mm f/4、全身 50mm f/5.6、场景 35mm f/8；机身和镜头从现有目录选项写入 ImageGen 节点。默认值可在节点摄影机选择器中继续编辑，已有节点的用户参数不会被新默认覆盖；准备请求也允许提交合法目录值覆盖对应默认。Camera block 在最终提示词中明确优先于描述或画风里的冲突摄影参数。`body` 输入作为半身兼容别名规范存储为 `half_body`。
- 摄影机参数增量验证：后端定向组 111 passed，覆盖资产设计用途与相机默认冻结、相机字段到实际 image request 的透传、非法目录值拒绝、Agent 参数合同、Grsai 路由兼容及提示词冲突优先级；前端 `fixed-assets.test.tsx` 12 passed，构建检查 `pnpm exec tsc -b --pretty false` exit 0。具体命令为 `.venv/bin/pytest -q tests/creation/test_asset_generation_references.py tests/creation/test_agent_model.py::test_asset_design_accepts_explicit_usage_and_aspect_ratio_but_rejects_mismatch tests/creation/test_agent_tools.py tests/creation/test_operations.py tests/freezone/orchestration/test_adapters.py tests/test_freezone_image_backend.py::test_camera_prompt_contains_camera_body_lens_focal_and_aperture` 与 `pnpm exec vitest run src/__tests__/features/creation-workspace/fixed-assets.test.tsx`。
- 林舟节点本轮通过真实 UI 设置为 ARRI Alexa65 / SignaturePrime 50mm f/4。首次 2K 请求 task `a1251a1c-0735-4ec4-839c-25d6c066685a` 在提交 provider 前因尺寸不支持而失败；随后在 UI 明确改为 1K，并新建 task `8b2f9819-a01f-488a-8289-21577665979d`，Grsai `gpt-image-2` 成功回执为 `3-abf02dca-9652-4319-a17d-6ec2837c4b44`。产物为 1024 × 1536，路径：`/Users/liuyuxiang05/Liu/nuomi-drama-data/output/local/agent_group_episode_acceptance_20261018/freezone/_outputs/freezone_gen/6e196fdddf7c4432.png`。root 已目视确认脸部与蓝灰工装清楚、画面无文字；这张林舟角色资产通过单张视觉检查，但尚未发布，也不代表整集验收通过。
- 后续真实 UI 核实：1006 px 视口下图像节点工具栏的操作项均可见、可点击；点击“保存资产”成功。root 随后将林舟半身图经“保存共享资产”流程存为角色肖像，新建基础资产并命名为“林舟”，UI 当前显示已保存版本 2；构思画布仍引用原先版本。该资产只作为人物身份参考，不视为角色设计最终图。
- 正式角色设计仍待完成：需补脸部特写及正面、侧面、背面全身设定 sheet；相关实现正在进行。完整角色资产与整集成片均未验收，整体状态继续为 **NOT PASSED**。
- 并发基线 P2 已修复：协作发送前先 flush，再以 runtime 实际服务端基线为祖先，对当前本地图与最新 GET 结果做三方合并；只有画布成功对齐后才保存协作基线并提交。发生字段冲突或外部操作 barrier 拒绝应用时会中止提交，未知结果仍不会自动重发。远端新增节点 B 的回归验证通过。
- 本轮前端定向验证：讨论／并发合并／runtime apply 回执共 22 项通过，`pnpm exec tsc -b --pretty false` 通过。真实 UI 热更新后曾短暂显示空画布，root 从服务端恢复全部 12 个节点，Lin camera UI 保存至 revision 30；这只证明画布恢复，不代表新群聊、媒体生成或整集成片验收通过。
- 正式剧本 handoff/intake 修复已通过 43 项与 9 项定向测试及独立 review：无 `scriptNode` 时由主创明确选择 formal 准备任务，writer 产物交给独立 editor 审阅固定版本；默认委托不再获得 `script.document.draft/review`，只有授权范围明确的 `formal_episode_script` 任务可使用。
- 真实 UI 新发 formal run `4e2cc…` 请求主创调度 writer 并交由独立 editor；该轮使用的旧 fixed roster 缺少 exact capabilities，错误分类正在修正，尚未重放，因此没有可验收的正式剧本结果。角色图片尚未发布，整集真实成片验收继续保持 **NOT PASSED**。
- 随后真实 UI 新建完整角色设计任务 `task-1f95d05148120b029892b5a3ecbe34e9`，root run `1f13f15b630944ff8d75ac8c590de355`。该轮实际准备失败：资产设计动作被 `asset_design_source_mode` 校验拒绝，且 bridge 冻结输入 `input_refs` 为空；没有可验收的新角色设计交付，也未生成媒体。
- 最新真实正式剧本任务 `task-70978deed429e375103ad0c946505063`（root run `1bd15cf6e0de4d59884514c675f8ebdb`）在收到模型响应后于决策校验失败：`delegations[1].input_indices[0]` 越界（`index_out_of_range`），没有创建主笔或编辑子运行。本轮没有新正式剧本草稿、doc/revision 引用或独立审阅记录；旧草稿不计入本轮成果。整体真实成片验收继续保持 **NOT PASSED**。
- 后续真实生产路径进展（2026-10-18）：正式草稿 `e9dc0642181544b885451cdf97cb92c2` 的正文已通过 task `24f13…` 成功导入既有 Episode 1；这是旧集导入进展，不代表新集整集交付。新建 managed Episode 1 画布 `60f0ffb8b25a44c881cdaaf8a01f9294`，对应 episode `00450…`；其规划 run `7bbc…` 仍在运行中，不能记为通过。
- 本轮实际 Grsai 资产文件位于 `/Users/liuyuxiang05/Liu/nuomi-drama-data/output/local/agent_group_episode_acceptance_20261018/freezone/_outputs/freezone_gen/`：林舟 `edac1b74c7604ea3.png`、陈奶奶 `2da470dfc4694cc8.png`、门厅 `1815857e5f0645f2.png`。三张均为 1672 × 941；两张人物图包含脸部特写及正面、侧面、背面全身设定，root 已逐张目视检查。陈奶奶 run `fd30…` 与场景 run `d955…` 已 succeeded。林舟 run `b400…` 的 provider 生成成功，但因展示宽高比造成 projection 冲突；修复已通过 review，等待 reload 后显式恢复投影，未重新生成。
- 整体状态仍为 **NOT PASSED**：目前尚无真实分镜视频，也没有可供最终播放检查的整集成片。

1. 在新项目构思画布群聊提交目标，核验主创与必要专家真实调用、固定身份和成果节点；无关成员不调用。
2. 从资产设计节点出图并明确发布，在产品内交接到对应集画布，形成导演方案及集内人物身份／引用绑定。
3. 在该集画布完成专业局部委托、组分镜与视频制作，禁止转入旧批任务；保留媒体参数与并发策略。
4. 对真实组图、视频、声音和约 90 秒整集实际播放检查，记录调用身份、画布归属、文件和视觉证据后才能标记通过。
