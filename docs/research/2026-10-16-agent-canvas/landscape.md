# AI 短剧与影视生产工具：竞品与模式调研

**快照日期：2026-10-16（UTC）**。GitHub 星数、license 字段及 `pushed_at` 取自当天 GitHub REST 仓库 API；星数会变化，复查时应重新查询。活动日期表示仓库最近推送时间，另有说明时使用默认分支最近提交。没有运行产品、触发模型任务或消耗付费额度。此报告用于产品形态与架构对照，不是 PRD。

## 结论

短剧工作流里最直接的开源参照是 Huobao Drama 与 Toonflow：前者把改剧本、抽取角色场景道具、拆分分镜、生成提示词做成专门 Agent；后者更像可本地运行的开放创作画布，Agent、插件和文件素材能力可组合，但创作者仍需自行编排流程。两者互补，不应把 Toonflow 的空白画布当作短剧生产流程已被完整解决。

大型产品中，LTX Studio 明确宣传“上传剧本→场景与分镜”，最接近剧本到预演；Google Flow 在项目内 Agent、素材集合、生成历史和可导出的版本元数据方面证据最完整；Adobe Firefly Boards 更适合视觉探索、协作和素材溯源，不能据公开资料视为可运行的分镜生产管线。MoneyPrinterTurbo 星数最高，但其主任务是主题/文案加库存或生成素材的短视频自动剪辑，适合作为任务状态与本地交付参考，不是戏剧连续性系统。

## 开源候选：活跃度、许可与适配度

| 项目 | 适配判断 | Stars（10/16） | 最近推送（UTC） | GitHub license 字段 / 已核对许可 |
|---|---|---:|---|---|
| [Toonflow（HBAI-Ltd/Toonflow-app）](https://github.com/HBAI-Ltd/Toonflow-app) | 高：短剧/漫剧/视频创作画布、角色与场景素材、Agent、MCP；核心是创作者编辑和组合节点 | 17,962 | 10/16 08:31 | MIT；README 还说明历史版本适用旧许可，见下文 |
| [Huobao Drama（chatfire-AI/huobao-drama）](https://github.com/chatfire-AI/huobao-drama) | 高：叙事型短剧端到端，实体、剧本、分镜和逐镜生成任务都有领域模型 | 16,291 | 10/05 16:33 | API 为 `NOASSERTION`；仓库 `LICENSE` 实际是 CC BY-NC-SA 4.0，含非商业条款 |
| [NarratoAI](https://github.com/linyqh/NarratoAI) | 中：面向影视/短剧解说、字幕分析、文案匹配与剪映草稿；适合后期和已有视频改编，非原创剧集生产中枢 | 11,418 | 09/17 06:26 | MIT |
| [MoneyPrinterTurbo](https://github.com/harry0703/MoneyPrinterTurbo) | 中低：高采用度的本地短视频自动化，支持自定义脚本、库存/本地/AI素材、配音字幕和批任务；缺少剧集、角色连续性等影视领域结构 | 131,996 | 10/16 09:47 | MIT |
| [StoryMind](https://github.com/LinHao-city/StoryMind) | 方向高度相关、成熟度低：README 描述先由 Storyboard Director 生成 shot list，再生成/检索画面并合成；不能用低星数证明实用性 | 15 | 07/03 07:58 | AGPL-3.0 |

计数与状态可由官方接口复核：[Toonflow API](https://api.github.com/repos/HBAI-Ltd/Toonflow-app)、[Huobao API](https://api.github.com/repos/chatfire-AI/huobao-drama)、[NarratoAI API](https://api.github.com/repos/linyqh/NarratoAI)、[MoneyPrinterTurbo API](https://api.github.com/repos/harry0703/MoneyPrinterTurbo)、[StoryMind API](https://api.github.com/repos/LinHao-city/StoryMind)。`license` 为 GitHub 的仓库识别字段，不替代检查实际 LICENSE 文件：Huobao 的 API 未归类，但仓库文件明确写 CC BY-NC-SA 4.0；Toonflow 当前根目录为 MIT，同时 README 标明 v1.0.8 前 AGPL-3.0、v1.0.8–v1.1.8 Apache-2.0 加附加协议的历史许可不追溯变更。复用代码时要按具体版本核实。

**同名/镜像核实：**搜索会同时返回 `Core5God/toonflow-app`（0 stars、fork）和其他个人 fork；主项目是 `HBAI-Ltd/Toonflow-app`。星数对比按 canonical repo 统计。当前 Toonflow API 显示 10/16 有推送，但最近默认分支提交页面可见提交为 10/13；因此表中推送时间和[提交历史](https://github.com/HBAI-Ltd/Toonflow-app/commits/master/)是两个不同活动信号。其余日期来自 API `pushed_at`，并非每次推送必然包含代码提交。

## 源码重点：Huobao Drama

以下结论来自当前 `master` 的源码，证据强度高（可复查实现）；README 中的宣传性能力未单独当作源码证明。

- **专职 Agent 有清楚的职责边界。** [`backend/src/agents/index.ts`](https://github.com/chatfire-AI/huobao-drama/blob/master/backend/src/agents/index.ts) 注册 `script_rewriter`、`extractor`、`storyboard_breaker`、`prompt_generator`；[`backend/src/agents/skills.ts`](https://github.com/chatfire-AI/huobao-drama/blob/master/backend/src/agents/skills.ts) 将项目技能目录里的 `SKILL.md` 按 Agent 注入，并支持工作区技能动态发现、技能管理和本地文件访问。这比通用聊天 Agent 更接近清晰的生产岗位目录。
- **剧本来源与改写可编辑，但 revision 不完整。** [`script-tools.ts`](https://github.com/chatfire-AI/huobao-drama/blob/master/backend/src/agents/tools/script-tools.ts) 从 episode 的 `content` / `scriptContent` 读取原稿，改写后将格式化剧本写入 `scriptContent`；schema 将原始 `content` 与改写稿分开。该写入覆盖当前字段，所查 schema 未见剧本 revision 表或不可变修订链。公开实现也未证明有 DOCX/Final Draft 等专业 screenplay 文件解析。
- **组织结构对制作有用。** [`schema.ts`](https://github.com/chatfire-AI/huobao-drama/blob/master/backend/src/db/schema.ts) 使用 drama→episode→storyboard，并建角色、场景、道具与 episode/storyboard 的关联；单条 storyboard 保存镜头类型、角度、运动、时长、图像/视频提示、首尾帧及素材地址。[`storyboard-tools.ts`](https://github.com/chatfire-AI/huobao-drama/blob/master/backend/src/agents/tools/storyboard-tools.ts) 校验并同步角色/道具/场景绑定；分镜 Agent 指引按节拍拆分、批次写入，并默认整集重新生成时清掉旧镜头。整集重做有明确行为，增量修改必须显式提出。
- **生成来源与故障可查，但不是完整恢复队列。** `sys_task` 保存媒体类型、关联实体、供应商、提示词、模型、参数、provider task ID、结果 URL、本地路径、状态及错误（见 [`schema.ts`](https://github.com/chatfire-AI/huobao-drama/blob/master/backend/src/db/schema.ts)）；[`routes/tasks.ts`](https://github.com/chatfire-AI/huobao-drama/blob/master/backend/src/routes/tasks.ts) 提供创建、按 ID/条件查询及删除任务。可据此把生成物回连任务上下文；所查路由未见原任务 retry/attempt 链或持久化队列恢复端点，不能把“能查失败”推断成“进程崩溃后自动续跑”。日志工具 [`task-logger.ts`](https://github.com/chatfire-AI/huobao-drama/blob/master/backend/src/utils/task-logger.ts) 还有 URL token 与 API key 脱敏处理。
- **本地部署有实际结构，但需遵守许可。** 项目包含桌面端、SQLite schema、工作区提示词和技能；不能忽略非商业条款，也不要把 CC BY-NC-SA 误写成宽松的代码许可证。

## 源码重点：Toonflow

以下结论来自当前 `master` 源码与当前 README，证据强度高。项目 README 明说创作者在文本节点中写剧本和分镜、校对编辑后再接参考素材、生成图像/视频；这不是自动接收剧本并保证完成整剧的承诺。

- **Agent 是开放式能力目录，不是短剧岗位目录。** [`apps/server/src/agent/skills/index.ts`](https://github.com/HBAI-Ltd/Toonflow-app/blob/master/apps/server/src/agent/skills/index.ts) 会从 workspace 和 global 目录动态加载技能，支持管理；MCP/插件文档支持将工具与外部 Agent 接进来。AI 能力面可扩展，但默认专业角色、角色之间必须经过的生产关卡并未由源码定义为一条固定短剧流程。
- **剧本是画布内容，专业导入器未证实。** README 将脚本、分镜、参考素材和视频片段放在无限画布；Agent runtime 支持文本附件和被识别的文档格式，能够读取本地 workspace 文件。所查代码未发现 screenplay 解析器把场景、对白、角色转为可编辑生产对象的专用入口。因此更准确的描述是“本地文件/画布+Agent 辅助改写”，不是已验证的剧本结构化导入。
- **本地素材和防覆盖很强，历史来源链要分范围看。** [`utils/assets/index.ts`](https://github.com/HBAI-Ltd/Toonflow-app/blob/master/apps/server/src/utils/assets/index.ts) 将素材存于本地 assets 目录，读取返回 SHA-256 revision，带旧 revision 写入时可发现并发修改；导入素材会返回源路径及目标路径。它提供并发一致性，不代表保留每次编辑的历史快照。3D 导演 Agent 的 [`agentPrompt.ts`](https://github.com/HBAI-Ltd/Toonflow-app/blob/master/packages/nodes/director3dNode/src/agentPrompt.ts) 明确将已选方案复制成新草稿，旧方案只读，属于特定导演模块的版本模式，不应泛化为所有节点/剧本均有版本控制。
- **执行过程可见且可控，运行状态仍有进程边界。** [`singleAgent/run.ts`](https://github.com/HBAI-Ltd/Toonflow-app/blob/master/apps/server/src/routes/singleAgent/run.ts) 逐事件返回 NDJSON；[`singleAgent/control.ts`](https://github.com/HBAI-Ltd/Toonflow-app/blob/master/apps/server/src/routes/singleAgent/control.ts) 暴露 pause/resume/stop/close。普通 Agent 对话写入 `.agent/sessions/*.jsonl`；子 Agent 通过 session 和事件反馈父 Agent。相对地，`utils/singleAgent/index.ts` 把临时会话、run 状态放在进程内 Map，源码注释要求多进程时自行共享会话/回调通道。应借鉴过程事件和人工控制，不能误以为这些接口就是持久化任务队列。

## 大型厂商产品：以官方文档为准

| 产品 | 公开材料能确认的能力 | 对五个评估面向的判断 |
|---|---|---|
| **Google Flow** | 官方帮助说明 Agent 可构思 storyboard、优化 prompt、生成/编辑/批量变化、整理资产；Agent session 按项目保存，可配置项目指令。Flow 支持项目、嵌套 collections、角色/场景、素材搜索/筛选与片段编排。 | **Agent**：一个项目创作 Agent，无证据显示有导演/编剧等固定岗位目录。**剧本**：帮助页支持从概念规划 storyboard，但未见 screenplay 上传解析为场景表。**来源/版本**：优势突出；历史面板保存编辑版本和提示，Takeout 包含 prompt、设置、ID、角色/场景、edit stack 版本链及配对 metadata。**本地修订**：服务为云端，项目/数据可下载导出，属于备份/迁移能力。**执行/恢复**：可恢复历史版本并导出；公开帮助未说明有失败生成重试队列。 |
| **Adobe Firefly Boards + Firefly AI Assistant** | Boards 官方文档支持无限画布、storyboard/moodboard、图像和视频、多模型、共享评论；Assistant 官方概览称可用自然语言安排并执行跨工具多步流程，包含按镜头创建 storyboard。 | **Agent**：是跨 Adobe 工具的助手，不是可见的多岗位制作 Agent 目录。**剧本**：画布可编辑文本/图片、逐帧编 storyboard；所查 Boards 文档未证明导入 screenplay 后自动建生产结构。**来源/版本**：生成历史、项目副本、Creative Cloud PSD/AI linked docs 的源端更新，加 Content Credentials 为素材溯源提供好模式。**本地修订**：云端工作空间，可下载单个产物；没有发现整项目本地编辑历史。**执行/恢复**：可从 generation history 回看/重新打开产物；官方资料中未找到面向批量视频运行的任务恢复协议。Boards 更像视觉前期与协作面板。 |
| **LTX Studio** | 官方站点称可上传 script 直接生成 scenes 和 storyboards；还有 Dynamic Storyboard、Timeline Editor、Sound Design、复用 Characters/Objects/Locations 的 Elements、项目协作。Enterprise 页面宣称跨部门协作和 version control。 | **Agent**：公开资料未见岗位目录或 Agent 调度设计。**剧本**：三者中公开材料最明确支持剧本上传转场景/分镜。**来源/版本**：Elements 支持角色/道具/地点跨场景复用；具体生成 lineage、revision 导出格式在公开资料里不清楚。**本地修订**：产品为在线平台；没有找到本地优先/离线编辑证据。**执行/恢复**：有 timeline/故事板工作流，公开站点未说明失败重试、队列或崩溃恢复机制。属于很强的功能形态参照，内部行为需产品实测才能下结论。 |

厂商源链接（均为第一方）：[Flow Agent](https://support.google.com/flow/answer/17093911?hl=en)、[Flow 项目与素材管理](https://support.google.com/flow/answer/16935308?hl=en)、[Flow 编辑/历史/Scenebuilder](https://support.google.com/flow/answer/16935718?hl=en)、[Flow 数据导出与 edit stacks](https://support.google.com/labs/answer/17571126?hl=en)、[Firefly Boards 官方介绍](https://helpx.adobe.com/firefly/web/create-mood-boards/firefly-boards/about-firefly-boards.html)、[Firefly Boards 协作](https://helpx.adobe.com/firefly/web/create-mood-boards/firefly-boards/share-for-collaboration.html)、[生成历史](https://helpx.adobe.com/firefly/web/access-your-files/view-generation-history.html)、[linked PSD/AI 文档](https://helpx.adobe.com/firefly/web/create-mood-boards/firefly-boards/linked-documents.html)、[Firefly AI Assistant](https://helpx.adobe.com/firefly/web/firefly-ai-assistant/firefly-ai-assistant-overview.html)、[LTX Studio 产品页](https://website.ltx.studio/)、[LTX 企业工作流](https://website.ltx.studio/studio/enterprise)、[LTX FAQ](https://website.ltx.studio/studio/faq)。

## 可借鉴模式与反模式

**可借鉴：**

1. Huobao 的明确 Agent 职责、可编辑技能规范与领域对象关联，适合将专业岗位能力和团队自有规程拆开维护；不能只按模型供应商罗列 Agent。
2. 把 episode/scene/shot/character/prop 作为有 ID 的对象，并将每次生成关联到 provider、model、prompt、参数、task ID、输出和错误，可支持选定资产重做与来源追踪。Huobao 源码证明这种关联方式可落地。
3. Flow 的素材 collections、按项目隔离的 Agent sessions，以及包含 edit stacks 和逐项 metadata 的可导出包，形成“创作组织结构+版本链+离线备份”的完整参照。
4. Toonflow 的本地素材库、乐观并发 revision、Agent 事件流与暂停/继续/取消，补足单纯画布交互；3D Director 新建草稿保留旧方案的做法值得借鉴，但限定于它那个模块。
5. LTX 将脚本、分镜、镜头生产与时间线放在同一产品语境，适合观察“剧本层如何连接到可视画面/成片”，不必照搬生成模型或界面。

**反模式/边界：**

- 背景图和分镜卡片排版本身不等于生产系统：若没有来源对象、镜头状态、任务结果与恢复机制，就只是视觉整理板。Adobe Boards 的公开定位也偏构思与协作。
- 可编辑文本字段不等于剧本 revision。Huobao `save_script` 更新当前字段；若每次改写都覆盖且无法比较/恢复，审稿和版本回滚成本会留给用户。
- 只有任务状态查询不足以承诺“恢复”。Huobao 持久化生成 task，但所查实现无 retry lineage；Toonflow Agent 工作流有实时暂停/停止，但 single-agent 活跃状态是进程内对象。产品状态应把“能看到失败”“可重试”“服务重启后续跑”作为不同能力。
- 无限画布的自由度增加用户自行设计流程的负担；如主用户是制作组，仍需提供有顺序、可编辑、可重复执行的默认生产单位。Toonflow README 确认需要创作者审阅和手动组织。
- MoneyPrinterTurbo 的短视频拼接、库存素材和旁白任务不能直接移植为戏剧管线：随机/关键词素材满足短视频，不等于剧本镜头覆盖、角色连续性或剧情因果。
- 许可不是次要元数据：Huobao 使用 CC BY-NC-SA，StoryMind 使用 AGPL-3.0；Toonflow 的不同历史版本许可也不同。商业产品代码复用前应逐版本审核许可证和第三方素材许可。

## 证据强度与未答问题

- **强（A）**：GitHub API 的 stars、license 字段、推送时间；Huobao 与 Toonflow 源码字段、路由、Agent 工具及工作区读写实现；Google/Adobe 官方帮助中心的操作说明。
- **中（B）**：仓库 README 对产品范围/项目示例的自述；LTX 官方产品页、企业页的功能宣传。产品页证明“厂商公开如此定位”，不证明复杂生产在所有账号、地区和套餐里实际可用。
- **弱/未证实（C）**：Star 数代表使用量、产品质量或稳定性；README 中的“端到端”宣称等同自动可恢复；未在所审文件找到某能力就断言产品绝对没有。本文未对 LTX、Google Flow 或 Adobe 进行付费实机试跑，也未触发生成任务。

实施设计仍需实测的问题：Flow 导出包能否完整还原 Agent 会话与可继续编辑的项目状态；Adobe Boards 的 storyboard 工作是否支持结构化镜头字段和历史版本导出；LTX script 上传后的场景/分镜字段能否稳定编辑、导出与跨项目复用，version control 是否对所有套餐开放；Huobao 失败 task 的实际重跑是否会复用已有上下文/资产，还是创建新 task；Toonflow 普通文本节点是否有可用的开箱技能模板，以及服务器重启后 Agent 历史和媒体生成任务能恢复到什么程度。以上需要不消耗或经授权的产品验证，不能仅据营销页补全。

