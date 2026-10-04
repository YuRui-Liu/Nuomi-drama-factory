# 创作画布编排 CLI 与整集制作 Skill 设计

日期：2026-10-04

状态：用户已选择“CLI 调用画布编排服务，网页与 CLI 共用执行逻辑”；本文件是该方案的书面规格，供实施前审查。本文命令和新接口均为拟实现契约，不代表已经可用。尚未执行节点真机验收或整集生产。

## 1. 目标与完成定义

新增 `nuomi canvas` 命令组及独立 `nuomi-canvas-production` Skill，使代理从完整剧本创建真实画布、规划制作图、创建节点与连线、按依赖执行、回写可见产物，最终交付可播放的一集视频。用户可随时在网页查看同一画布、检查输入输出，停止或继续工作。

交付同时满足三个条件：

1. 当前注册的每一种画布节点均完成对应真实能力验收；生成类节点必须调用真实运行时或媒体供应商，不能以 mock、空产物、已有无关素材冒充。
2. 代理在只获得新 Skill、项目标识和剧本输入的情况下，使用公开 CLI 从空白画布完成一集。不得由开发者预建制作图或直接写最终媒体结果代替 Skill。
3. 留下持久化运行记录、逐节点证据、完整画布与成片；重启服务或关闭网页后能够恢复。任何必需节点失败、缺少素材或完整剧情未覆盖，整集不得报告成功。

代码测试通过不等于上述验收完成。节点“任务完成”也不等于产物有效。

## 2. 现有实现与差距

已核对的维护源：

- `frontend/src/features/canvas/domain/canvasNodes.ts`：声明 19 种节点类型。
- `frontend/src/features/canvas/domain/nodeRegistry.ts`：节点默认值、连线能力及菜单可见性。
- `src/novelvideo/freezone/canvas_store.py`：画布版本、保存幂等键、历史和写锁。
- `src/novelvideo/api/routes/freezone.py`：画布读写、文本、图像、视频、音频、Skill 和合成接口。
- `src/novelvideo/freezone/skill_registry.py`：带输入输出角色的 `skill.v1` 契约。
- `src/novelvideo/api/routes/freezone_video_director.py`：导演尝试、状态、重试与恢复接口。
- `frontend/src/features/canvas/nodes/VideoComposeNode.tsx`：合成后由浏览器新建结果节点与连线；时间线初始顺序依赖节点 y 坐标。
- `src/novelvideo/production_cli.py`：现有 CLI transport、项目选择、认证与命令入口。

差距是缺少服务端统一节点调度、结果落图和无人值守恢复。现有媒体任务和画布保存接口可以复用，但不能仅包装 HTTP 请求就宣称图执行已完成。纯前端操作需要补齐服务端等价能力；交互式预览仍由网页展示。

## 3. 架构选择

```text
nuomi-canvas-production Skill ── nuomi canvas CLI ──┐
                                                  ├─ Canvas Orchestration API
网页节点按钮 / 画布运行入口 ────────────────────────┘
                                                        │
                       能力目录 / 图校验 / 图规划 / 运行记录 / 调度器
                                                        │
                              节点适配器 → 现有任务后端 → 运行时及媒体服务
                                                        │
                              产物校验 → 版本化结果补丁 → 真实画布
```

编排属于画布领域。复用现有任务后端、鉴权、计费归属、供应商和画布持久化，不另建供应商 SDK，不通过浏览器点击承担无人值守调度，不调用主线 `batch` 制作完再伪装成画布过程。

仍按叙事组规划视频生产：一个叙事组可以包含多镜头，供应商内部物理片段不改成独立付费制作单位。画布上的拆图、预览、编辑节点可独立运行，但不能让默认整集流程退化为逐镜头无约束生成。

## 4. 模块边界

新增 `src/novelvideo/freezone/orchestration/`，分离以下职责：

- `models.py`：图计划、能力声明、运行快照、节点尝试、产物与错误契约。
- `capabilities.py`：注册类型、操作、输入输出、默认值、可用性及缺失依赖。
- `graph.py`：端口校验、执行依赖、拓扑、循环检测和目标祖先子图。
- `planning.py`：从完整剧本生成带场次与叙事组来源的制作图提案。
- `store.py`：运行和尝试的持久化、租约、调度幂等和断点。
- `scheduler.py`：依赖调度、并发、状态对账、取消和恢复。
- `adapters/`：按文本、图像、视频、音频、媒体工具等能力分文件，调用既有服务。
- `projection.py`：受版本保护的节点数据和连线补丁、结果节点创建。
- `verification.py`：文件有效性、媒体元数据和验收证据生成。

新增独立 API 路由模块 `src/novelvideo/api/routes/freezone_orchestration.py`，避免继续向现有大型 `freezone.py` 堆积编排逻辑。必要时仅从旧路由提取将被网页和编排共同使用的服务函数，不重构无关功能。

CLI 作为薄客户端，拟由 `src/novelvideo/canvas_cli.py` 注册到现有 `nuomi`。网页通过新增 orchestration gateway 和运行 hook 调用同一契约；节点组件负责输入编辑与展示，不承担第二套任务执行或结果落图逻辑。

## 5. 节点能力契约

能力以 `(node_type, operation)` 标识，不能把类型名直接等同于唯一执行动作。目录返回：端口名、媒体类型、单/多输入、必填字段、输出字段、可执行操作、运行通道、所需供应商、本机能力状态和诊断原因。

节点分为生成、变换、输入、容器及交互展示。非生成节点不得制造虚假 task_id；其执行证据可以是输入文件校验、图结构、真实导出文件或浏览器交互录像/截图。

服务端能力清单与前端类型注册表须有集合一致性测试。隐藏但仍可被保存画布引用的类型也纳入清单，记录其兼容入口；没有执行器的类型在预检阶段明确报错，不在运行中静默跳过。`skillNode` 还需按实际注册的 skill ID 展开能力，不能只测试空壳节点。

## 6. 图及运行契约

每个图计划记录 `schema_version`、项目、画布 ID、原文 hash、计划 ID、节点 ID、语义端口连线和制作顺序。节点保留 `scene_id`、`group_id`、来源范围和规划时长，使画布能对回剧本。

计划输出包含机器可验证的剧情覆盖表：每场对应哪些制作组、每组哪些节点产出、关键动作与对白覆盖情况。规划中的创作解释与原文事实分开；不得把截断剧本当成完整一集。默认验收输入是此前完整四场《这次，别留白》，保存为独立 fixture，不覆盖当前仅两场的项目原文。

新画布先创建原文节点及计划提案，验证图后原子应用制作图。视觉 `groupNode` 的容器关系不充当执行依赖。制作片段顺序使用显式 ordinal 和时间线，不用 x/y 坐标、任务完成顺序或边插入顺序推断。

运行锁定：`run_id`、图版本、节点输入、上游产物版本、模型与运行时设置、提示词/Skill 版本、画幅、时长及并发来源。文本和规划沿用用户已选 Codex；图像、视频、配音使用已保存服务配置，不要求所有模态都交给 Codex。

节点尝试状态：`pending → ready → submitting → running → verifying → succeeded`；异常为 `failed`、`blocked`、`submission_unknown`、`cancel_requested`、`cancelled`。后端任务成功后先验证产物，再写画布，最后才认定该节点完成。画布写冲突不应重新购买生成。

## 7. 保存、幂等与恢复

- 运行、节点尝试和 provider task 映射必须持久化，网页关闭不停止任务。
- 每个逻辑提交使用稳定幂等键；同一请求重试返回同一尝试，不重复计费。
- 请求超时且无法判断供应商是否接单时标记 `submission_unknown`，先对账，不能盲目重发。供应商缺少幂等/查询能力时明确阻塞人工核实，不承诺无法实现的 exactly-once。
- 结果应用使用画布锁、当前版本和受限补丁。只合并本次尝试拥有的结果字段，保留用户布局、标题等无关编辑；同一输入被用户修改时保留产物并标记冲突，不能覆盖新的输入。
- 应用补丁也使用稳定幂等键。合成结果节点和结果边 ID 由运行/逻辑输出导出，重复恢复不能出现两份成片节点。
- 已成功节点按输入及上游产物指纹复用。输入变化仅使依赖后代过期；不受影响的分支继续复用。
- 取消停止新提交，并向支持取消的任务传播请求。已接单但无法取消的任务继续对账，产物不会丢失或在取消后自动串接下游。
- 同一画布执行租约避免两个 CLI 同时调度同一计划；不同画布仍受全局及用户级并发限制。

## 8. 拟提供的 CLI 与 API

以下命令是交付接口，不是当前可执行命令：

```bash
nuomi --project PROJECT canvas capabilities
nuomi --project PROJECT canvas create --name "这次，别留白"
nuomi --project PROJECT canvas get CANVAS_ID
nuomi --project PROJECT canvas nodes add CANVAS_ID --body-file node.json
nuomi --project PROJECT canvas nodes update CANVAS_ID NODE_ID --body-file patch.json
nuomi --project PROJECT canvas edges add CANVAS_ID --body-file edge.json
nuomi --project PROJECT canvas plan CANVAS_ID --script script.md --duration 90
nuomi --project PROJECT canvas validate CANVAS_ID --plan PLAN_ID
nuomi --project PROJECT canvas apply CANVAS_ID --plan PLAN_ID --base-revision REVISION
nuomi --project PROJECT canvas run CANVAS_ID --target FINAL_NODE_ID
nuomi --project PROJECT canvas status RUN_ID
nuomi --project PROJECT canvas wait RUN_ID
nuomi --project PROJECT canvas resume RUN_ID
nuomi --project PROJECT canvas retry RUN_ID --failed
nuomi --project PROJECT canvas cancel RUN_ID
nuomi --project PROJECT canvas export RUN_ID --output episode.mp4
```

`run --node NODE_ID --operation OPERATION` 支持单节点验收。整集运行在目标产物需要的依赖子图上调度；独立分支可并发。`resume` 只恢复及对账，`retry --failed` 才重试失败节点；每次显式恢复批次对每个失败节点最多发起一次新尝试。

新增 API 以 `/projects/{project}/freezone/orchestration` 为前缀：能力目录、plans 创建/验证/应用、runs 创建/查询/事件/恢复/重试/取消。简单画布读取复用既有 GET；节点/边修改走有版本保护的图补丁服务，CLI 不直接写本地画布 JSON。

CLI 沿用现有认证、API 地址和项目参数；结构化输出包含 `ok`、`code`、`run_id`、`canvas_id`、节点进度、产物与阻塞原因。`wait` 使用事件或有界轮询，可中断，不密集刷新。退出码：0 为对应命令成功，1 为运行失败/阻塞，2 为输入或契约错误；提交成功不等同成片成功。

## 9. 默认整集制作图

```text
完整剧本输入 → 脚本/规划节点 → 场次及叙事组计划
                                  │
                  角色、场景、必要道具参考节点
                                  │
                  每组分镜图 → 必需拆图/参考节点
                                  │
                         每组导演视频节点
                                  │
               对白音轨/旁白/音乐（按实际需要）
                                  │
                    显式时间线 → 合成节点 → 成片结果节点
```

参考资产保持跨组身份与场景一致。已有合格资产可复用；新建测试画布使用独立的 run 与节点来源，不能偷换为历史整集成片。音频确定一种主对白来源：若采用生成视频自带对白，则不重复叠配音；需要独立配音时按人物和台词时间线生成，合成时明确原声保留策略。

合成复用现有裁剪、顺序拼接和音轨混音能力。首版不加入复杂转场、重叠视频轨或特效引擎。虽然底层合成允许补黑场，默认完整一集验收禁止用非剧本要求的长黑场补足时长。

## 10. Skill 交付

目录：`skills/nuomi-canvas-production/SKILL.md`，附 CLI 操作手册、节点验收规则、恢复流程和完整剧本 fixture。执行前按 skill-creator/writing-skills 的要求验证技能质量；本规格阶段不创建假定已经可用的 Skill。

Skill 必须指导代理：

1. 查询能力与当前画布，核对剧本完整性及项目配置。
2. 创建独立画布、保存原文、生成并验证计划，避免覆盖用户画布。
3. 自动创建真实节点与连线，记录角色/场景引用和显式片段顺序。
4. 执行目标成片子图，读取保存的并发，持续跟踪任务。
5. 针对真实错误修复并通过原尝试恢复，不跳过节点、不伪造产物。
6. 检查成片与原文覆盖关系、声音和播放，交付画布入口与视频。

Skill 使用公共 CLI；不能靠读取源码并临时调用私有 Python 函数才能完成正常流程。CLI 缺少能力时，修复公共入口，再回到 Skill 流程。

## 11. 逐节点真机验收矩阵

实施时从注册表重新枚举并冻结版本；下列 19 类为当前基线。每类默认操作必须真实通过；目录若公开多个不同执行能力，每个公开能力都要列出对应验收结果。

| 类型 | 必须核验的真实行为 |
| --- | --- |
| uploadNode | 实际上传媒体、项目范围路径、预览与重新打开 |
| imageNode | 兼容存量节点执行图像编辑，真实输出可解码 |
| imageGenNode | 文生图及公开图生图入口，参考输入实际传递 |
| exportImageNode | 真实导出文件、尺寸和来源选择 |
| beatContextNode | 引用/上下文内容持久化并能被下游实际消费 |
| textAnnotationNode | 原文输入、修改、保存及下游文本引用 |
| groupNode | 容器、节点父子关系、移动/重开后图结构稳定 |
| storyboardNode | 实际图片拆分、格子顺序、输出回写及下游引用 |
| storyboardGenNode | 真实多格分镜图生成及对应分组/格子元数据 |
| videoNode | 真实视频生成及公开输入模式、回放与媒体信息 |
| videoDirectorNode | 导演计划、真实生成、尝试恢复及产物回写 |
| audioNode | 真实音频生成、试听及合成输入 |
| videoStoryNode | 真实视频分析/故事输出及下游可消费性 |
| videoComposeNode | 真实多片段时间线合成、音轨策略及唯一结果节点 |
| musicDeskNode | 核对实际公开操作，执行选曲/编辑/导出及音轨消费；若公开生成能力则必须实调 |
| scriptNode | 完整剧本到结构化脚本的真实运行及来源保留 |
| pano360ViewerNode | 真实全景资源加载、视角交互、公开捕获/输出能力 |
| threeDWorldNode | 真实三维资源加载、交互、公开生成/捕获能力及产物 |
| skillNode | 按注册 skill ID 核对输入角色，真实运行、应用输出与回写 |

验收报告每行含：代码版本、类型、operation/skill ID、canvas_id、node_id、run_id、attempt/task ID（若适用）、输入摘要、输出 URL/hash、实际运行时与模型、媒体尺寸/时长、截图或交互证据、passed/failed/blocked。

没有依赖、模型无权限、供应商拒绝或能力未实现均记为 blocked/failed。不得以“辅助节点”“隐藏节点”或“已有单元测试”为由跳过并声称全节点通过。报告不得包含密钥或敏感签名参数。

## 12. 整集验收与故障注入

默认用完整四场、约 90 秒剧本，在新建验收画布制作，沿用项目画幅与当前保存模型设置。成片目标 90 秒，允许 85–95 秒；优先以内容完整性为准，不能删除关键动作、对白或结尾换取时长达标。

验收过程：能力预检 → Skill 接收剧本 → 自动建图 → 真实生成 → 真实合成 → 打开同一画布核对 → 播放成片。独立保留各类节点验收画布，不把全景、三维等无关节点硬塞进这集故事。

必须证明：

- 四场顺序、互相拆穿、坦露恐惧、共同报名、并肩重算的起承转合均存在。
- 必需对白可听清，不出现重复配音、严重截断、缺片段或意外黑场。
- 所有生成产物有真实任务来源，刷新后能预览，成片可以导出并播放。
- CLI 中途退出、网页关闭后，服务端继续执行；恢复不会重复购买完成产物。
- 一个节点失败时依赖后代阻塞，独立分支可继续；修复后只续跑需要的部分。
- 用户移动/改名节点不会丢失；用户修改生成输入导致的冲突有明确处理结果。
- 合成成功但回写冲突时只恢复回写，不重新生成视频；最终只有一个逻辑结果节点。

自动测试覆盖提交幂等、任务对账、并发、版本冲突、边类型、循环、跨项目引用、断点、取消和媒体失效。真实故障注入选用可恢复场景，避免为了证明恢复而制造额外付费失败。真实供应商运行按需要执行，已有合格结果复用；没有新增证据不重复做昂贵质量检查。

## 13. 实施顺序与审查点

1. 能力盘点、19 类节点与 Skill ID 清单、端口契约及测试基线。
2. 图补丁和运行持久化，先通过幂等、版本冲突及恢复测试。
3. 复用服务的节点适配器和统一执行 API，补齐前端独占操作的服务端等价能力。
4. CLI 和网页迁移到同一执行/回写服务；保留现有手动操作与旧画布兼容。
5. 剧本规划器和完整整集制作图；验证原文来源、时序和资产一致性。
6. 新 Skill 与操作手册，通过真实任务验证它能独立驱动 CLI。
7. 全节点真机矩阵、整集视频、恢复证据及最终交付报告。

各阶段使用失败测试先行、小范围实现和针对性验证。工作区存在其他任务的未提交改动，实施前逐文件确认所有权；只提交本任务的文件，不回滚、不夹带、不擅自迁移其他修改。若创建隔离工作树，必须包含本任务实际依赖的当前代码，不能从陈旧默认分支开始后宣称验证了运行中的版本。

## 14. 非目标与交付边界

本次不建设新的模型供应商、不替换主线制作工作台、不承诺所有模型变体都兼容、不加入新的复杂剪辑引擎。节点和公开操作范围以冻结清单为准；任何缺口均列入未完成事项，不能通过缩小清单宣布全覆盖。

最终交付：实现代码与测试、`nuomi canvas` CLI、独立 Skill、完整验收画布与视频、逐节点证据报告、已知限制。只有第 1 节的三项条件全部满足才宣布端到端完成。
