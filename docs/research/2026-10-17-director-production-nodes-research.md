# 导演交接后的节点、流程与专业 Agent：深化研究

日期：2026-10-17

状态：研究记录，不代表能力已经实现。后续用户逐项确认结果已归档到 [导演与成片节点 PRD](../superpowers/specs/2026-10-17-director-production-nodes-design.md)。承接 [专业剧本节点 PRD](../superpowers/specs/2026-10-17-professional-screenplay-node-design.md) 与 [专业岗位契约](../superpowers/specs/2026-10-16-manga-production-agent-contracts.md)。

## 1. 既定边界

用户已确认：采用剧本仅固定版本；显式“交给导演”生成导演画面版、资产拆解及叙事组方案。该动作不自动购买图片或视频。专业岗位必须能调用相关受支持节点；画布承载可编辑创作内容，不强制每个岗位一个节点或一次模型调用。

本轮继续研究这些结果如何组织、专业意见怎样融合、现有节点如何接入以及后续媒体生产如何取回结果。外部技能只阅读，不安装、不执行生成；公开成片和说明不能代替 Nuomi 的真机验收。

既有研究见 [2026-10-16 索引](2026-10-16-agent-canvas/README.md)。本轮以具体专业能力与产品操作为增量，保留旧资料的观察日期、固定提交与证据局限。

## 2. 补充专业工具的一手证据

| 来源 | 观察事实 | 对 Nuomi 的设计推论及限制 |
| --- | --- | --- |
| [Shot Designer](https://www.hollywoodcamerawork.com/shot-designer.html) | 场景平面调度、人物／摄影机运动、镜头表和分镜关联；图与表联动；可保存场景快照；页面把 3D 层列为路线图 | 场景空间应共用，镜头引用调度，不应让不同岗位各维护一份冲突描述。不能把路线图当已交付能力 |
| [Storyboarder 仓库](https://github.com/wonderunit/storyboarder)、[Shot Generator 设计说明](https://wonderunit.com/shot-generator/) | 查询时约 3.9k stars。说明展示人物／物体／摄影机摆放、姿态及镜头场景保存；强调灯光为粗略方向表达，非精确模拟 | 预演首先服务构图、空间与动作可读性。可以用简化人形与几何体；不能声称粗略布局可预测生成模型的物理照明效果。设计说明包含历史愿景，不据此推断所有功能当前可用 |
| [Toon Boom 动态分镜文档](https://docs.toonboom.com/help/storyboard-pro-20/storyboard/getting-started/animatic.html)、[声音文档](https://docs.toonboom.com/help/storyboard-pro-22/storyboard/sound/about-sound.html) | 文档定义按面板时长、摄影机移动及声轨组织动态分镜并播放预览 | 静态分镜之外，需要验证事件、动作、对白与切点的时间关系。低成本时间预览不等于最终视频，不强制购买 AI 视频来验证每个面板 |
| [StudioBinder 镜头表](https://support.studiobinder.com/en/articles/1455273-how-to-populate-the-shot-list) | 场景与镜头层级、镜头规格和参考图可用于表达拍摄计划 | 镜头表、图像分镜应是同一组镜头内容的不同视图，避免复制后失去关联 |
| [set.a.light 3D 灯光模型](https://www.elixxier.com/en/docs/set-a-light-3d/lighting/how-light-works/) | 区分光源、控光附件与支架，位置、方向及调光决定模拟结果；文档明确模拟边界 | 灯光方案应有光源动机、方向、软硬、冷暖、明暗关系与变化，而非仅写“电影感”。本产品未实现其物理模拟器，不能承诺照度或灯具参数精确可控 |
| [Cascadeur 动作工作流](https://cascadeur.com/help/getting_started/workflow_basics)、[AutoPhysics](https://cascadeur.com/help/tools/physics_tools/autophysics/autophysics_settings) | 动作以关键姿态和时间组织，可使用专门物理辅助工具 | 动作师交付起点、预备、关键动作、接触／受力反馈和落点；用事件时间关联摄影和表情。本产品不因此获得骨骼仿真或精确动力学能力 |

星数仅为关注度快照；专业商业工具通过其具体文档评价，不与开源仓库星数混排。以上产品均未在本轮安装或实操验证。

## 3. 用户提供来源的深化核对

### 用户提供来源：本轮深化证据

| 来源 | 本轮确认内容 | 借鉴与证据边界 |
| --- | --- | --- |
| [小云雀 Web 手册](https://xyq.jianying.com/tutorials/web-manual)、[资产画布](https://xyq.jianying.com/tutorials/short-drama-agent-canvas) | 剧本关联全局人物／场景，再到可编辑分集故事板；可增删镜头，引用资产及前镜画面、灯光／道具／站位参考 | 借鉴引用与就近编辑；手册没有公开专业多 Agent 的提示词或调度实现，不能据 UI 推定内部岗位架构 |
| [小云雀 3D 导演台](https://xyq.jianying.com/tutorials/short-drama-agent-3d-director) | 文档说明人物／道具位置姿态、动作轨迹、摄影机位置／FOV／观察点、关键帧时间线；导出静帧或参考视频回到画布 | 支持共享空间与时间表达。文档建议先静态构图再动画；不能把该控制能力直接当成 Nuomi 已接入，也不能声称其灯光具备精确物理仿真 |
| [小云雀智能预演](https://xyq.jianying.com/tutorials/short-drama-agent-smart-preview) | 选取一组已确认提示词片段，生成并回填相应关键分镜；文档列出片段和引用数量条件 | 借鉴组级预演与局部修改。文档阈值不硬编码进 Nuomi；减少重试为产品主张，未经本轮复现 |
| [H3 cases](https://github.com/SkyNotSilent/awesome-MiniMax-H3-cases)、[统计快照](https://github.com/SkyNotSilent/awesome-MiniMax-H3-cases/blob/main/data/project-stats.json)、[收录规则](https://github.com/SkyNotSilent/awesome-MiniMax-H3-cases/blob/main/CONTRIBUTING.md) | 2026-10-17 页面 301 stars；2026-10-16 统计为 2,680 案例、726 完整公开提示词、25 教程，其中 3 个官方案例。收录规则区分官方可复现与社区材料，缺失提示词不能猜补 | 适合作为 Agent 方法示例索引，展示输入／结果／公开程度／来源；它是社区案例库，不是全部案例均官方验证的能力基准 |
| [Hell Grind 项目](https://higgsfield.ai/@higgsfield.studio/projects/hell-grind)、[作品页](https://higgsfield.ai/original-series/hell-grind/episode-1) | 官方展示作品、资产／简报／详情；项目动态内容本轮静态读取未展开。前轮浏览器见场次目录、Regenerations 和 CINEDANCE／ACTING／LIRA 附件入口，附件打开超时 | 可借鉴场次资料、局部修改及过程可见性；未阅读附件全文、未复现，不能命名附件就认定拥有相应专业方法。厂商时长／生成计数不作为成本基准 |

[Hell Grind 许可](https://higgsfield.ai/licences/owl-p-nl-1.0) 对公开材料的使用范围有条件；本轮只研究交互与信息组织，不复制其素材或附件进入产品。公开案例不能视为无条件可移植代码。

### LibTV、DramaClaw、OpenMontage：深入到技能和产物结构

沿用前轮固定提交核对具体文件，而非仅依赖营销介绍；未安装运行或复现生成效果。

- **LibTV**：固定提交 `c609246c1eca69f6bc129bcbb5d64c36734e4a4a` 的 [技能](https://github.com/libtv-labs/libtv-skills/blob/c609246c1eca69f6bc129bcbb5d64c36734e4a4a/skills/libtv-skill/SKILL.md) 将意图与素材交给会话，使用 session／project 标识和消息序号继续读取结果。公开适配层未提供专业导演岗位、可编辑分镜结构或稳定镜头资产版本关系。可借鉴委派入口，不能把它当成完整导演数据模型。
- **DramaClaw**：固定提交 `a627206b3919f78769bd5efe4e6b3cd3946eeb93` 的 [分镜导演技能](https://github.com/dramaclaw/dramaclaw/blob/a627206b3919f78769bd5efe4e6b3cd3946eeb93/.hermes/skills/sketch-storyboard-director/SKILL.md) 与 [输出结构](https://github.com/dramaclaw/dramaclaw/blob/a627206b3919f78769bd5efe4e6b3cd3946eeb93/.hermes/skills/sketch-storyboard-director/references/output_schema.md) 把构图／站位／动作问题定位到需要修改的画格，产出结构化修改记录与候选；不改的画格可省略。值得吸收的是定位、局部修订和结果证据，不自动追加整集付费复查。
- 该提交的 [组规划](https://github.com/dramaclaw/dramaclaw/blob/a627206b3919f78769bd5efe4e6b3cd3946eeb93/src/novelvideo/freezone/agent_catalog/builtins/recipes/drama-shot-planning.json)、[组细化](https://github.com/dramaclaw/dramaclaw/blob/a627206b3919f78769bd5efe4e6b3cd3946eeb93/src/novelvideo/freezone/agent_catalog/builtins/recipes/drama-shot-group-detail.json)、[组分镜图](https://github.com/dramaclaw/dramaclaw/blob/a627206b3919f78769bd5efe4e6b3cd3946eeb93/src/novelvideo/freezone/agent_catalog/builtins/recipes/drama-shot-group-storyboard.json) 展示镜头作用、动作轨迹、摄影运动、对白声音与灯光连续性的传递。这是配方文件，不证明各岗位独立 Agent 运行。其固定时长和镜头数不迁入 Nuomi。
- **OpenMontage**：固定提交 `e45f5b12bd46440b04f3675af9a77d0718e6a254` 的 [scene_plan schema](https://github.com/calesthio/OpenMontage/blob/e45f5b12bd46440b04f3675af9a77d0718e6a254/schemas/artifacts/scene_plan.schema.json) 将脚本来源、时间、景别、运动、光线、景深、色温、叙事目的、人物动作／情绪／对白／交互对象关联起来；[scene-director](https://github.com/calesthio/OpenMontage/blob/e45f5b12bd46440b04f3675af9a77d0718e6a254/skills/pipelines/cinematic/scene-director.md) 强调主体、时间动作、场景、空间构图及摄影描述。适合参考专业字段的共同语义。
- OpenMontage [Backlot](https://github.com/calesthio/OpenMontage/blob/e45f5b12bd46440b04f3675af9a77d0718e6a254/backlot/README.md) 是连接剧本、scene_plan 和资产清单的只读视图，不是已实现的双向编辑画布；[checkpoint 协议](https://github.com/calesthio/OpenMontage/blob/e45f5b12bd46440b04f3675af9a77d0718e6a254/skills/meta/checkpoint-protocol.md) 描述阶段及部分场景进展恢复。可借鉴产物关联与恢复，不能声称其固定流水线等同 Nuomi 专业机组。

这三个来源各有不同价值：轻量委派、可定位的专业局部修改、结构化导演产物。不能混写成一套它们都已具备的完整系统，也不整体移植外部确认门禁或质检循环。

### 建议的内容层次（待确认）

建议区分场景、镜头、叙事组、媒体任务与剪辑片段：

- **场景**承载共同空间、人物／道具、时间天气和光源基线，引用剧本稳定场景 ID。
- **镜头**承载叙事目的、视点、景别、动作／反应和声音时序，引用场景设计修订。
- **叙事组**组织可生成的连续叙事与组内镜头顺序，保持正式生产的 group_id；不是画布装饰分组。
- **媒体任务**绑定本次采用的方案、引用和执行合同，返回实际产物；组内供应商物理分段不自动变成新的生产组。
- **剪辑片段**引用已生成媒体的时间范围，成片顺序由时间线决定，非节点坐标。

一场可以有多个组；跨场组必须明确各镜头归属和转场，不能假定 group_id 等于 scene_id。画布内容组仅用于组织，不能从折叠或拖拽推断任务依赖。

默认候选布局：整集导演方案作为导航／版本入口，按场展开共享调度，按组展示分镜与实际媒体；灯光、动作、表情和摄影是相关内容的专业视图，也可按需展开为关联节点。是否采用该布局需用户确认。

## 4. 专业岗位怎样真正交付（设计推论）

### 现有代码能力核对

| 已有部分 | 源码位置 | 本次设计应做的事 |
| --- | --- | --- |
| 画布剧本、分镜生成、视频导演与媒体节点 | `frontend/src/features/canvas/domain/canvasNodes.ts`、`nodeRegistry.ts` | 保留已有节点，新增内容与操作桥接；不将旧 scriptNode 误当新版专业剧本主节点 |
| 组分镜生成与图像拆分 | `freezone/orchestration/adapters/storyboard.py`、前端 `StoryboardNode.tsx`／`toolProcessor.ts` | 分镜生成与本地拆图是不同操作；`storyboardNode.split` 虽在能力说明中出现，尚未安装到编排 `available_operations()`，Agent 可执行性需逐项核验 |
| 专业导演结构 | `src/novelvideo/director_plan/models.py`、`cinematography.py`、`creative_contract.py` | 已有动作起止、轴线／机位、光源／阴影／曝光、摄影／构图／表演与交接字段，优先暴露和编辑这些结构，不能笼统说专业能力不存在 |
| 导演方案版本 | `director_plan/service.py`、`api/routes/director_plans.py` | 已有创建／编辑／比较／激活／引用刷新，绑定脚本哈希与语义版本；新增受限画布操作，激活不能冒充剧本采用 |
| 生产叙事组 | `narrative_groups/service.py`、`models.py` | 已关联 director_revision_id、source_span_ids、shot_ids；桥接到画布内容，保持与视觉 groupNode 的区别 |
| 媒体执行 | `freezone/orchestration/adapters/requests.py` | 复用已有图像／视频／声音／合成入口；videoDirectorNode.generate 是媒体尝试路径，不可用于不生成媒体的“交给导演” |
| 专业岗位 | `creation/professional_agents.py` | 当前多为通用节点操作配置，角色本身不授予执行权限；缺少通过导演方案服务编辑专业结构的受限操作和三产物准备交接 |

核心缺口是**现有专业数据与画布／Agent 操作之间的连接**，以及采用剧本到三类导演准备结果的完整事务／任务身份；不是重新实现所有媒体节点或再建一份摄影灯光数据模型。本文源码核对未运行供应商，已有路径不代表本轮端到端真机通过。

### 岗位输入输出建议

| Agent | 读取 | 交付与节点操作目标 | 应检查的可见事实 |
| --- | --- | --- | --- |
| 导演 | 采用剧本、约定、目标片长 | 导演画面版、场景意图、组方案，融合各专业候选 | 关键剧情／对白有来源覆盖，冲突可定位 |
| 分镜师 | 导演意图、空间与组边界 | 镜头顺序、景别、切点、构图和声音节拍 | 镜头承担叙事作用，切点与事件一致 |
| 站位师 | 场景、人物和道具 | 平面位置、朝向、视线轴、进出路径、遮挡 | 接触对象和移动路径明确，前后空间可接 |
| 摄影师 | 分镜与场景调度 | 摄影视点、构图、运动起止、焦点变化 | 画面能看见关键行动／反应；有意越轴需解释 |
| 灯光师 | 场景时间天气、美术与摄影 | 基准光向、来源、明暗／色温关系、事件性变化 | 光线与剧情环境相容，跨镜头保持来源一致 |
| 动作师 | 剧情事件、站位与时长 | 动作起始／关键姿态／过程／落点及对象交互 | 先后因果、接触和反应可理解，时长合理 |
| 表情师 | 人物目标、对白与动作节拍 | 视线对象、情绪转变、反应时机及强度 | 表情变化有事件依据，避免只有情绪标签 |
| 美术／资产师 | 文字拆解、造型与场景需求 | 已有资产选择、版本／状态引用、缺失清单；授权后调用资产节点 | 文字设计与真实可访问素材区分 |
| 连续性师 | 前后场／组方案及媒体 | 可定位的身份、道具、空间、状态和声画差异 | 只阻断明确的关键冲突，不将审美不确定性升级成重生要求 |
| 图像／视频执行师 | 融合方案、实际引用、项目设置 | 受支持节点／组任务提交、查询、原任务恢复和产物回填 | 执行合同与源方案对应，不静默改剧情／模型／组边界 |
| 声音师 | 对白、动作、原生音轨 | 音色／表演和声音时序、音频候选 | 保留可用原生音频，不无条件重配 |
| 剪辑师／交付检查 | 实际组视频与声音 | 片段顺序、裁切、音轨字幕和导出；必要补充任务 | 文件可播放、关键内容无明显遗漏，补镜有具体来源 |

这些是职责与操作目标，不是宣称对应专用字段和适配器均已实现。每个 Agent 的真实能力需对应现有操作、版本校验、权限和回执。

## 5. 冲突、版本与专业控制边界

导演融合专业意见，主笔维护采用的故事事实，用户硬约束优先；执行师不能在最后拼提示词时暗中决定冲突。专业候选修改应保留作者、范围与依据，并只失效受影响的下游内容。

例如人物换位会影响镜头视线、动作接触和光向判断；灯光调整未必要求重写剧本。系统应展示具体影响，不默认整集重做。生成中仍使用冻结快照，返回候选不能覆盖用户并发修改。

必须分别显示“创作意图”“模型实际支持的控制”“已提交参数”“实际结果”。摄影机焦段、微表情、精确步点等描述可能只是提示词约束，不能伪装成引擎已严格执行的物理参数。

## 6. 后续设计讨论顺序

### 可用于交互验收的虚构示例

以下仅为设计样例，不是用户选定剧情或生成结果。场景“门口交还钥匙”：甲从门边走近，递出钥匙；乙先看钥匙，再抬眼看甲，迟疑后接住。

共享场景调度记录门、两人初始位置、递物区域及光源方向。动作师给出走近／停步／递出／接住的事件；表情师把迟疑绑定到“看见钥匙”，摄影师安排空间交代、钥匙与反应的视点，灯光师保持门外光源的方向。分镜师将事件组织为组内镜头；执行师只把采用结果转换为当前模型支持的输入。

若用户改成“乙不接钥匙”，这是剧情结果变化，应回到主剧本候选；若只将摄影机移近，则评估构图／视线与已有媒体影响，不重写人物关系。以这个样例验证专业修改范围、冲突提示和局部结果回填，比检查目录中是否出现岗位名字更有意义。

### 讨论选项与后续决定

推荐 A：每场共享一份场景调度设计（空间、人物、光源基线），各叙事组引用其具体版本；镜头拥有局部摄影／动作／表情变化。跨组共同事实一致，修改时可显示影响。

对照 B：各叙事组独立保存完整调度方案，适合快速独立探索，但相邻组的空间／光线变化需要额外对照与同步。即使选 B，已采用剧本和资产身份仍然共享。

两者都是内容组织选择，不等于强制增加一套 3D 编辑器，也不改变已确认的统一画布、现有节点能力或叙事组生产单位。

### 后续顺序

先确认导演产物在场景／组／现有节点中的组织方式，再讨论共享调度编辑、分镜与时间预演、专业局部委托、执行准备与采用，最后确定声音／剪辑回路和 E01 验收。沿用已确认的采用与导演交接动作，不重新要求用户选择相同决策。

研究稿只提出可讨论方向；不会据此生成媒体或修改业务代码。

后续确认：用户已采用共享场景调度、平面图与镜头预览联动、每组分镜主节点、导演按需调度专业岗位、默认轻量预演、采用与制作分开、局部修改与重生成分开、统一音画时间线，以及显式导出。正文中研究阶段的“待讨论”不覆盖这些后续决定；完整合同以关联 PRD 为准。
