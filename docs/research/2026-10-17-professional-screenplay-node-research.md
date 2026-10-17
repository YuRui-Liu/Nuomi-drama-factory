# 专业漫剧剧本节点：研究与设计议题

日期：2026-10-17

状态：研究记录。后续用户逐项确认结果已整理为 [剧本节点 PRD](../superpowers/specs/2026-10-17-professional-screenplay-node-design.md)，本记录不替代规格。立意节点确认结果见 [立意 PRD](../superpowers/specs/2026-10-17-ideation-node-design.md)。

## 研究边界

目标是让画布剧本节点连接真实专业创作与制作业务。评价依据为可读剧本、可靠上下文、审改证据、版本保护和制作可执行性。GitHub 星数仅作为检索信号，不能作为剧本质量或生产验证证据。外部技能仅阅读研究，本轮没有安装或执行其生成指令。

## 已核对的项目基础

| 能力 | 当前证据 | 设计意义 |
| --- | --- | --- |
| 创作设定 | `frontend/src/features/script-creation/script-setter.tsx` | 可以复用题材与设定编辑，无需重造选择器 |
| 文档与修订 | `src/novelvideo/script_creation/models.py` | 已有 brief／outline／people／scenes／props／episode_synopsis／episode_script；正文与画布应共用内容身份 |
| 专业工艺 | `src/novelvideo/script_creation/generation.py` 调用 `run_craft` | 不能把已有能力误报为缺失，重点是接通节点 |
| 审改与恢复 | `src/novelvideo/script_creation/craft_pipeline.py` | 已有独立审阅、带原文依据的问题、有限返修、台词整合、输入绑定和恢复记录 |
| 制作交接 | `src/novelvideo/script_creation/handoff.py` | 已有项目内不可变剧本交接、修订绑定、任务 outbox 与失败状态 |
| 语义解析 | `src/novelvideo/screenplay_semantics/parser.py` | 已有场景／动作／对白及原文定位，可服务节点局部操作与交接完整性 |
| 画布剧本节点 | `frontend/src/features/canvas/nodes/ScriptNode.tsx`、`src/novelvideo/freezone/text_node.py` | 当前剧情转分镜表能力与文档式专业写作不同，需明确兼容与接入，不能直接替换导致旧节点失效 |

既有方法设计见 [2026-10-03 工艺流程设计](../superpowers/specs/2026-10-03-script-creation-craft-pipeline-design.md)。其现状章节描述当时问题，不能当成今天的代码状态。

## 本地 script-master 方法提炼

来源：[总控](../../skills/script-master/SKILL.md)、[框架](../../skills/script-master/references/framework.md)、[写作](../../skills/script-master/references/writing.md)、[会诊](../../skills/script-master/references/doctor.md)、[台词](../../skills/script-master/references/dialogue.md)、[导演画面版](../../skills/script-master/references/visual-director.md)。用户要求作为产品设计参考，当前不启动其中的故事创作问诊。

- 故事基础不仅是梗概：主题与冲突、人物欲望和需求、关系、信息边界、世界规则、伏笔状态共同支撑续写。
- 正文必须能表演和拍摄：场次、时空、人物、可见动作、对白及必要声音；人物决策推动因果，旁白不能代替全部行动。
- 审稿需给出可定位原文和修复目标；结构修改与台词润色的权限及影响范围不同。
- 导演画面版从主剧本派生，补足出入场、站位、动作过程、受击反馈、环境及声画承接，不暗改故事事实。
- 正文、内部创作笔记、审稿报告、对外交付分别保存，避免把评分和流程话术塞进剧本。
- 风格以用户设定为准。知识库是按题材调用的参考，不照抄固定模板，不把机械爽点／伏笔数量变成所有题材的硬门槛。
- 原技能的多阶段、评委池和确认方式需要适配已确认的统一画布与有限调用范围；不增加固定九页工作台，不强制每个岗位独立调用，不自动无限返修。

### 方法到产品的映射

| 专业能力 | 节点呈现建议 | 运行边界 |
| --- | --- | --- |
| 框架与人物设计 | 关联大纲、人物、世界规则和伏笔；支持局部展开 | 连续剧与单篇分别组织上下文，不强制所有任务重做全套档案 |
| 正文写作 | 连贯阅读的中文拍摄稿，场景导航及选段委托 | 读取冻结简报、计划与前文；保留未采用候选的身份 |
| 剧本医生 | 侧栏显示原句、问题影响、修复建议及应保留设计 | 复用现有独立审阅和最多两轮正文结构返修；规划文档最多一次，不运行模拟评委投票 |
| 台词专家 | 原句／修改建议对照，区分措辞与剧情改动 | 只处理有依据的问题，剧情级变化不能冒充台词润色 |
| 导演画面版 | 独立派生内容，关联同一剧本与场景版本 | 保留人物关系、台词含义和事件结果；制作规格以项目设置及采用的方案为准 |
| 制作交接 | 当前采用版本、差异、资产引用和任务结果 | 使用现有交接服务，保护已有生产来源与成功媒体 |

当前 `craft_models.py`、`craft_store.py` 与 `generation.py` 已覆盖报告、阶段记录、输入版本和候选依赖；现有 `craft-report.tsx` 可作为节点详情的复用起点。是否增加独立专业剧本节点类型及如何命名既有分镜表节点，需在下一轮明确，避免历史节点数据被误解释。

这里的“漫剧”指以剧本驱动的动画／动态漫影视生产。漫画书页数、格数与印刷排版不是本轮默认目标；如果新增漫画书交付，应另定格式合同。

## 外部一手来源

### Fountain

[官方语法](https://fountain.io/syntax/) 将场景头、动作、角色提示、对白等表达为可读文本。可借鉴正文与语义元素兼容的思路，支持从原文定位场景与对白。中文拍摄稿继续使用项目既有解析规则，不能直接用英文大写角色规则替代；Fountain 导入导出属于后续可选能力。

### MiniMax H3 官方动画短片技能

[官方技能](https://github.com/MiniMax-AI/MiniMax-H3/blob/main/skills/3d-animation-short-generator/SKILL.cn.md) 强调故事、角色／场景参考、空间连续性和将创作产物保存在画布。值得吸收的是耐久产物、明确引用与制作连续性。其默认视觉风格、模型、逐镜生成和逐步确认不适用于本项目；Nuomi 仍读取保存配置，并按叙事组正式生产入口执行。

### 专业技能与产品补充核验

以下热度为 2026-10-17 页面观察，GitHub 显示为约数；只证明关注度。产品功能依据官方文档，开源工作流依据仓库声明，未作独立成片质量验证。

| 来源 | 观察与值得借鉴的能力 | 局限与取舍 |
| --- | --- | --- |
| [zenstory-ai/drama-skills](https://github.com/zenstory-ai/drama-skills)、[写作技能](https://github.com/zenstory-ai/drama-skills/blob/main/skills/short-drama-write/SKILL.md) | 约 3.0k stars；稳定 EP／SC／SHOT／MOTION 标识，剧本与视觉材料分离，跨文档连续性和时长检查；局部修改保留原文，记录人物知识、道具及结束状态 | 工作室生产经验为维护者自述。借鉴标识和交接，不直接移植其执行器 |
| [jtydhr88/screenwriting-skills](https://github.com/jtydhr88/screenwriting-skills)、[工作流](https://github.com/jtydhr88/screenwriting-skills/blob/main/plugins/screenwriting/skills/sw-workflow/SKILL.md) | 约 1.7k stars；26 个技能，区分电影与连续剧，维护 story bible 与创作决策；单次交付可跳过完整圣经 | 适合创作方法参考，不能代替媒体制作。书籍／剧本来源为仓库声明；第三方摘录不自动受其 MIT 授权覆盖，不复制摘录库入产品 |
| [SkillHub novel-to-storyboard](https://skillhub.cn/skills/user_5b12983f/novel-to-storyboard)、[script-to-manga](https://skillhub.cn/skills/user_f0835403/script-to-manga) | 页面分别显示 16,062／8,018 下载、54／17 收藏；描述剧本拆场、角色场景卡及视线／镜头交接 | 用户发布的目录说明与热度，非专业认证、非 GitHub 星数；仅作为补充候选线索 |
| [Celtx Breakdown](https://support.celtx.com/hc/en-us/articles/216581678-Breakdown) | 从剧本原文标记场景资产，关联生产目录／报告；场景移动后保留剧情日信息 | 借鉴原文锚点与场景元数据；真人剧组的通告／排班不作为漫剧首版必需 |
| [Final Draft 功能](https://www.finaldraft.com/products/features/)、[锁页指南](https://kb.finaldraft.com/hc/en-us/articles/27817054455828-How-do-I-lock-or-unlock-script-pages-and-use-the-locking-tools) | 修订标记、替代文本、锁页和省略场景，支持分发后的稳定引用；官方建议进入筹备／制作后再锁定 | 借鉴“创作中自由改、发制作时固定版本”；Nuomi 优先稳定场景与内容标识，不机械照搬纸张页码 |

综合推论：主稿使用稳定的集／场标识，连续性记录按需维护；拆解与导演执行信息引用主稿，在正式交接时固定版本。画布位置不决定剧本场序，移动节点不改变故事发生顺序。

## 节点组织方案与后续决定

推荐：每集一个剧本主节点，内部提供正文及场景导航；故事圣经、人物和分集规划为关联内容，按需展开到画布。用户选中某场或某段可直接委托专业 Agent，必要时提取为关联节点，保持同一内容身份和版本。

对照方案：默认每场一个独立节点。局部并行更直观，但整集阅读、跨场修改与版本一致性负担更高；需要清楚处理正式场序与画布坐标不同的问题。

用户后续已选择推荐方案，并确认先交付大纲与人物方案、确认后再写标准中文拍摄稿、自动审稿与有限返修、采用后显式交给导演。完整约定见剧本节点 PRD；本轮没有修改业务代码。

## 生产级验收候选标准

1. 从已采用立意和简报版本生成可读的 E01；每场有明确叙事作用，正文与目标时长相容。时长估算须标为估算，不能用字数伪装最终片长。
2. 续写读取人物当前状态、规则、伏笔和已发生事实，区别正式稿、计划和候选；上下文超限明确报告，不能默默丢弃关键材料。
3. 结构与对白问题可定位，有限修订后仍有问题则保留草稿及报告；审美建议不冒充生产硬错误。
4. 局部改写保护未授权场景和人工改动，报告受影响上下游；采用、回退和并发冲突具有可靠版本证据。
5. 主剧本、导演画面版、角色场景资产与叙事组拥有可追溯关联；缺失场景或对白的交接不能被标为成功。
6. 真实模型文本调用、正式制作任务和最终可播放 E01 分别提供证据；UI、单元测试或外部产品演示均不能替代真机验收。
