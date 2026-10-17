# 专业 Agent 目录与剧本交接画布：研究综合

研究日期：2026-10-16。目的：在编写新 PRD 前，明确值得借鉴的产品机制、证据边界和 Nuomi 的实际增量。本报告是研究结论与待讨论建议，不是已经批准的规格。

## 阅读入口与证据

四路并行研究覆盖 LibTV、小云雀、DramaClaw、OpenMontage、MiniMax H3 及社区案例、Higgsfield，并扩展到 Toonflow、Huobao、NarratoAI、MoneyPrinterTurbo、StoryMind、Google Flow、Adobe Firefly 和 LTX Studio。另对 Hell Grind 项目做了浏览器观察，并与 Nuomi 当前代码核对。

| 分报告 | 研究重点 |
| --- | --- |
| [Nuomi 基线](nuomi-baseline.md) | 当前代码、既有 PRD、已具备能力与真正缺口 |
| [LibTV 与小云雀](libtv-xiaoyunque.md) | 会话任务承接、剧本实体关联、通用画布与短剧流程的区别 |
| [DramaClaw 与 OpenMontage](dramaclaw-openmontage.md) | 自由探索、作品采用、结构化产物、恢复边界与同源疑点 |
| [MiniMax 与 Higgsfield](minimax-higgsfield.md) | 模型方法论、示例证据、输入角色、声音与镜头结构 |
| [Hell Grind 项目观察](hell-grind-observation.md) | 长片项目公开制作笔记、资产状态、局部迭代与剪辑反馈 |
| [开源与大厂横向比较](landscape.md) | 高星项目源码、Google、Adobe、LTX 产品资料 |

各分报告的字母等级仅适用于其内部。综合判断统一区分：**源码可见实现、官方文档描述、浏览器直接观察、作者案例自述、研究者推论**。源码不等于运行验收；官方提供复现脚本不等于本轮已经复现；可播放案例不等于完整生产链公开。本次没有登录付费账户、执行模型生成或验证完整成片质量。

## 核心判断

Nuomi 的机会在于把已有配置、委托、交接和画布能力接成可理解、可编辑、可追溯的创作过程。专业目录负责让用户选对人，交接负责保留原意与来源，画布承接可采用的成果，主创协调有限范围内的后续工作。角色名称数量和节点数量都不能单独证明这条链路成立。

### 1. 专业 Agent 应展示交付能力和方法

LibTV 的公开接口以会话、用户消息、任务状态和结果承接意图；OpenMontage 的技能组织、MiniMax 的任务输入规范以及案例库的来源标记，从不同角度说明“角色介绍”需要落到可检查的工作内容。参见 [LibTV 源码](https://github.com/libtv-labs/libtv-skills)、[OpenMontage](https://github.com/calesthio/OpenMontage)、[H3 提示词指南](https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/docs/VIDEO_PROMPT_WRITING_GUIDE_base_en.md)。

建议后续设计验证以下展示内容：适合的任务、需要的输入、交付物样例、工作方法、可调用能力、当前不能完成的工作、采用的配置版本。角色、技能、模型和执行授权应分别表达。例如，角色叫摄影指导，不意味着它已经能调用全部视频模型；方法说明也不应直接授予执行权限。

案例建议区分成片展示、公开提示词、公开输入素材、公开工作流和独立复现记录。社区库的 verified 含义应按原项目解释，不能将其转换为 Nuomi 的质量保证。[H3 案例贡献规则](https://github.com/SkyNotSilent/awesome-MiniMax-H3-cases/blob/main/CONTRIBUTING.md)

### 2. 剧本交接需要保留语义和编辑责任

小云雀官方资料展示了剧本、角色、场景和分镜的关联；Higgsfield 的文档强调共享 Project Brief、演员与参考素材；H3 文档进一步区分首尾帧和多模态参考的输入角色。这些证据支持“带关系的交接”，但不足以证明竞品具有完整、可靠的版本事务。[小云雀短剧画布](https://xyq.jianying.com/tutorials/short-drama-agent-canvas)、[Cinema Studio](https://higgsfield.ai/cinematic-video-generator)、[H3 API](https://platform.minimax.io/docs/api-reference/video-generation-v2-create)

对 Nuomi，值得验证的交接对象包括原始剧本及修订、用户明确的约束、已有角色/场景引用、Agent 提取或推断的候选，以及后续工作范围。原文事实、AI 补充和用户确认应能区分；重交接应展示差异，保留人工修改。先复用已有快照、diff 和 outbox，再确认统一画布的落点与回执。

### 3. 画布的价值在关系、采用与修订

小云雀通用画布的多会话、引用和历史版本，与其短剧阶段流程应分开看。Nuomi 已批准统一工作空间，不应因为竞品有阶段导航就重新设置创作门槛。[小云雀通用画布](https://xyq.jianying.com/tutorials/creation-agent-canvas)

建议把“候选生成出来”“用户采用它”“下游使用了哪个版本”视为不同事件。改一份角色设定，可以提示受影响内容并支持选择性更新；不应默认重写全部下游成果。自由探索转入作品时，应讨论选择范围、引用或复制、来源保留与冲突处理，而不是只增加一个转入按钮。

DramaClaw 与 Nuomi 存在相同模块路径和领域概念，部分能力可能已经存在于本地。研究未证明二者完整来源关系，不能称其为外部独有创新；必须逐项比较当前实现。

另一个需要修正的预期是 OpenMontage：其 Backlot 根据项目产物、checkpoint 和事件记录聚合场景卡，是只读看板，并非可编辑节点画布。它适合借鉴执行状态与产物的展示；不能直接作为剧本交接画布的实现依据。其阶段 gate 也不适合照搬到 Nuomi。[Backlot 源码](https://github.com/calesthio/OpenMontage/blob/e45f5b12bd46440b04f3675af9a77d0718e6a254/backlot/state.py)、[服务接口](https://github.com/calesthio/OpenMontage/blob/e45f5b12bd46440b04f3675af9a77d0718e6a254/backlot/server.py)

### 4. 方法论应转成可编辑中间产物

H3 官方指南将画面与动作、场景声音、配乐分开，并描述时间化的镜头变化；Context-IR 还可能补充原输入未明确的细节。因此有价值的借鉴是保存原始意图、增强后的描述和实际生成请求，并让用户理解增补来自哪里，而非把某套模型语法固化为全产品通用格式。[H3 指南](https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/docs/VIDEO_PROMPT_WRITING_GUIDE_base_en.md)、[Context-IR 文档](https://platform.minimax.io/docs/api-reference/video-generation-v2-h3-context-ir)

Hell Grind 公开笔记强调参考图与描述配合、同一人物的状态变化、空间关系、局部修正和剪辑反向提出补镜需求。它提示我们研究“为什么改、改了哪里、结果是否采用”的记录。该判断来自公开项目笔记和可见界面，未独立验证制作规模、效果或成本，也未读到全部附件。[项目页](https://higgsfield.ai/@higgsfield.studio/projects/hell-grind)

### 5. 会话历史、任务回执和恢复执行是不同能力

LibTV 脚本展示了提交与查询接口；Toonflow 有会话历史和运行控制，但看到 JSONL 与内存中的活动状态不足以证明进程重启后能恢复；Huobao 可见任务记录，也不能据此推断完整重试链。具体证据见各分报告。

后续 PRD 应基于 Nuomi 实际运行链讨论：提交是否收到、当前运行到哪里、产物是否落库、失败后从哪里继续，以及再次点击是否造成重复任务。不要把一条“已完成”聊天消息作为交接与生产成功的唯一依据。画布保存恢复也不能替代生成任务恢复。

DramaClaw 源码进一步提供了可修订草稿、确认状态、任务与运行 ID、幂等请求和 runner lease 的证据；这比只有历史记录更接近执行追踪。但本轮没有验证崩溃恢复或逐个 provider 的重试、结算行为。OpenMontage 的 checkpoint 同样不等于所有外部工具都具备可靠恢复。[详细代码证据与未验证项](dramaclaw-openmontage.md)

### 6. 高星、易用和适合 Nuomi 是三个维度

Toonflow 与 Huobao 与本需求更直接相关；NarratoAI 更偏已有视频的解说与剪辑；MoneyPrinterTurbo 的高关注度不能证明它解决长篇角色连续性；StoryMind 仅作为低成熟度相关候选。星数是研究日的动态快照，详见横向报告，不作为能力评分。

大厂产品各有重点：Google Flow 的项目上下文、素材历史与导出值得研究；Adobe Firefly Boards 的探索和协作更贴近前期创意；LTX 的官方资料强调剧本到分镜、复用元素和时间线。文档没有覆盖的后台恢复与版本一致性均保留为未知。[Flow Agent](https://support.google.com/flow/answer/17093911?hl=en)、[Firefly Boards](https://helpx.adobe.com/firefly/web/create-mood-boards/firefly-boards/about-firefly-boards.html)、[LTX](https://website.ltx.studio/)

## 对 Nuomi 的增量映射

| 方向 | 已有基础 | 后续设计重点 |
| --- | --- | --- |
| 专业 Agent 目录 | 配置、版本、个人发布、项目采用；少量内置角色 | 找得到、看得懂、任务匹配、交付示例与真实能力状态 |
| 剧本进入统一画布 | 冻结正文、来源修订、实体与参考、差异确认、outbox | 落到哪个画布、对象如何对应、重交接如何保护人工编辑 |
| 主创接手 | 主创选择、节点上下文、局部委托、文本候选和草案保存 | 交接回执、下一步建议、执行范围与候选采用体验 |
| 可追溯成果 | 配置固定版本、运行/委托/对象关联等基础 | 将已有信息呈现给用户；核实引用与下游更新的缺口 |
| 恢复能力 | 不同子系统已有保存、状态与生产恢复机制 | 逐条验证交接—任务—产物的连接，不另造一套泛化运行系统 |

以上来自静态代码审阅，不表示 UI 或真实模型链已通过端到端验收。尤其不能将配置中的 `execution_enabled: False` 理解为全部 Agent runtime 尚未实现。具体代码链接见 [本地基线](nuomi-baseline.md)。

## PRD 前建议验证的场景

这些是后续设计的验证用例，不是本轮已执行的测试：

1. 用户有完整剧本：交接后能找到原文、关联设定和待确认候选；不自动覆盖既有人工内容。
2. 用户只想修改一场戏：专业 Agent 接收局部范围，交回可比较的候选，不强制推进整集制作。
3. 用户更新人物状态：能看到可能受影响的内容，选择采用新版本，旧结果仍可追溯。
4. 交接或生成中断：重新打开能判断任务和产物状态，恢复动作不会静默重复提交。
5. 用户浏览一个 Agent：从输入和交付示例就能判断是否适合，且不会把展示案例误认为本地已接通的能力。

下一步设计应优先明确交接完成后的自动推进深度。用户已经选择“目录＋交接＋主创接手”的整体方向，但尚未明确批准自动执行到哪一步；研究不能替代这项产品选择。

## 保留的限制

- 未运行竞品或 Nuomi 的付费生成、跨重启恢复与完整端到端用例；未评估输出质量排名。
- Hell Grind 部分附件打开失败；部分社区原帖不可直接读取，相关内容按仓库记录而非原帖独立核实标记。
- Higgsfield 的不同官方页面存在规格差异；星数、案例计数与默认分支内容会变化。规格落地前按具体模型、账户和版本核实。
- 开源代码许可与收录的媒体、提示词、人物肖像权利不同。Hell Grind 另有平台限定许可，本轮只借鉴抽象方法，不复制受限素材或提示词包。
- 当前生产约束继续遵守仓库 AGENTS.md：按叙事组、使用保存的并发与受支持入口、按失败组恢复、比例适当的核验；竞品研究不授权增加默认付费质检或绕开现有流程。

本轮成果仅为研究文档，未修改产品代码、未新写 PRD。
