# 业务上下文怎样流向资产、分镜和视频

- **范围**：当前主仓库的剧集生产路径；代码核对基线 `bf8519d`（2026-09-29）
- **用途**：回答“某张图、某段视频依据了哪些业务事实，模型实际收到什么，结果写到哪里”
- **深入阅读**：[生产资产](03-production-assets.md) · [剧本与语义](04-screenplay.md) · [分镜与图像](05-storyboard.md) · [视频生成](07-video.md) · [存储与项目文件](../development/storage-and-files.md)

这里的“业务上下文”不是一个自动传遍全流程的大 Prompt，而是一组带来源和版本的事实、创作决定与媒体证据。每次生成只读取其中一部分，再编译成该模型能消费的文本、图片或结构化字段。**上游存有某项信息，不代表下游模型已经看到它。**排查时必须看调用处实际组装的 payload、选中的资产版本和最终发送的请求。

## 先区分五层信息

| 层 | 例子 | 谁决定其权威性 | 下游应怎样使用 |
| --- | --- | --- | --- |
| 原始事实 | 剧集原文、source span、语义 scene/beat、已确认角色视觉 Bible | 剧集来源 revision、active semantic revision、Bible revision | 引用来源 ID；不能把模型补写的情节反过来当原文事实 |
| 创作决定 | DirectorPlan 的叙事组、shot、场景锚点、机位、时长、资产需求 | 当前 active DirectorPlan revision；用户的分镜/视频设置 revision | 按当前方案投影为画面与运动要求 |
| 媒体选择 | 角色身份状态、场景、道具的 slot/current version；分镜草图或 render cell 的选择 | 资产采用记录、分镜选中记录 | 解析成具体文件，保留版本、路径和内容哈希 |
| 模型调用 | 文本 Prompt、图片引用、结构化输出 schema、视频 workflow 参数 | 各阶段 compiler/Runner | 这是“模型实际看到什么”的边界；不得用 UI 上的摘要代替 |
| 生成证据 | provider task id、prompt snapshot、H3 manifest、MP4 与状态 | 任务与产物记录 | 用来追溯一次运行；后续改动不应篡改既有证据 |

“版本当前有效”与“历史任务当时用过”是两个问题。资产采用改变 current；已排队任务应依据自己的冻结输入或在执行前发现过期，而不是悄悄改用新的 current。

## 当前主线

```mermaid
flowchart LR
  S[剧集原文/source revision] --> M[语义 revision]
  M --> D[DirectorPlan revision]
  A[视觉 Bible/资产 slot 与版本] --> R[参考图解析]
  D --> G[叙事组 shot → 分镜网格]
  R --> G
  G --> C[render cell/选中分镜]
  C --> F[首尾帧与参考图快照]
  D --> O[H3 episode-pack 提示词优化]
  F --> O
  O --> V[RunningHub workflow/分段视频]
  F --> V
  V --> P[manifest/组视频/后续合成]
```

图中两条输入线有不同作用：DirectorPlan 规定“拍什么、怎么拍”；资产版本和 render cell 提供“长什么样”。分镜图像生成调用、H3 文本优化调用和 RunningHub 视频调用是三次不同的模型/供应商边界。

## 逐次交接：事实、模型输入、结果

| 交接 | 读取和选择 | 实际给模型/供应商 | 输出与留痕 | 过期判断 |
| --- | --- | --- | --- | --- |
| 角色图、场景图、道具图 | 已确认视觉 Bible、项目风格，或场景/道具记录与创作来源上下文；显式参考图 | 各自的图像 Prompt 和 reference paths；角色编译器写入脸、发、身份锚点、服装状态等 | 候选图片与 prompt/recipe/reference metadata；合格首版可 provisional，后续采用更新 slot current | 生成版本不自动等于已采用版本；上传与生成的版本记录路径也不同 |
| 剧本语义 → DirectorPlan | 指定 source revision、active semantic revision、风格快照和可选导演配置 | `source_script_hash`、source spans、scenes、dramatic beats、画幅、导演风格；要求结构化 `DirectorPlanDraft` | DirectorPlan revision、叙事组与 shots | source/semantic/style revision 不一致时拒绝或要求重建 |
| DirectorPlan → 分镜网格 | active plan 的 group shots；没有 plan 时才使用 legacy Beats；解析当前组的资产绑定/参考图快照 | 每格 `visual_description`、场景时间、style image projection、布局规则、按顺序编号的参考图；图像供应商接收 Prompt 与图片文件 | 原网格、拆出的 cells、prompt snapshot（原 Prompt、每格文案、参考路径与 SHA、model/task id） | 参考快照、草图 revision 或 group revision 不匹配时不能复用旧输入 |
| 分镜 → H3 输入 | 选中分镜/render cells、组视频方案、首尾帧、组级参考图与 workflow 设置 | 入队先冻结帧与参考图快照及 digest；执行时构造 segment、mode、duration 和 storyboard images | 带 revision/snapshot ID 的任务与后续 manifest | 分镜选择、plan、设置或参考图 revision 变化要拦住旧任务 |
| H3 Prompt 优化 → 视频供应商 | shot/Beat 文本、相邻已完成组的上下文、DirectorPlan、风格、帧哈希、连续性契约、引用事实 | 文本模型得到 episode-pack 上下文；如有冻结分镜图，还得到图像用于视觉判断。优化后的每段 Prompt 再和首/尾帧、参考素材、mode、workflow 参数交给视频 adapter | 优化段、质量/连续性证据、provider attempts、分段 MP4、组 manifest 与合成 MP4 | manifest 记录当时输入与尝试；失败片段和过期 revision 不能被误当成当前成功视频 |

这些交接对应的代码入口：

- 资产：`src/novelvideo/character_visual/compiler.py` 的 `compile_visual_prompt_snapshot`；`src/novelvideo/task_backend/runners/{character_image,scene_reference,prop_reference}.py`；`src/novelvideo/production_workflow/store.py` 与 `src/novelvideo/api/routes/production_assets.py`。
- 导演方案：`src/novelvideo/task_backend/runners/director_plan.py` 的 `_build_director_plan_input`；`src/novelvideo/director_plan/prompts.py` 的 `build_episode_prompt`；`src/novelvideo/director_plan/planner.py` 的 `run_structured(..., output_type=DirectorPlanDraft)`。
- 分镜：`src/novelvideo/narrative_groups/service.py` 的 `generation_beats_for_group`；`src/novelvideo/narrative_groups/planned_binding_service.py` 的 `build_planned_reference_snapshot`；`src/novelvideo/task_backend/runners/narrative_group.py` 的 `_grid_prompt`、`_generation_input`、`_generate_grid`。
- 组视频：`src/novelvideo/api/routes/narrative_groups.py` 的 `_enqueue_group_video`；`src/novelvideo/task_backend/runners/narrative_group_video.py` 的 `_optimize_missing_prompts` 与组视频 Runner；`src/novelvideo/media_capabilities/video/h3_prompt_optimizer.py` 的 `H3PromptContext`。

### 1. 资产：先定义身份，再选择文件

角色身份图的 Prompt 由**已确认** VisualBible 编译。编译器明确读取项目风格、面部与发型特征、身份锚点、服装状态和显式参考路径；未确认的 Bible 不进入该编译路径。场景与道具 Runner 还可读取 `script_creation` 的 authoring context 作为设计约束。它们不是“把整集剧本直接发给画图模型”。

生成后，候选版本与 slot 分离：第一个合格版本在空 slot 中可成为 provisional current；后来新图仍是 candidate，需要采用才替换 current。下游引用必须同时关心 **slot 是什么**、**选中了哪个 version**、**该 version 对应哪个文件/哈希**。直接上传角色/场景图目前还有绕开正常 version 注册的路径，不能推断每张 canonical 图都已有完整的 uploaded-version 证据；细节见[生产资产](03-production-assets.md)。

### 2. 导演方案：结构化计划不等于实际画面

导演规划读取同一剧集的 source revision 与 active semantic revision；它要求二者一致，并把源片段、语义场景/戏剧 Beat、风格快照等序列化到模型输入。模型返回 `DirectorPlanDraft`，随后成为可投影的叙事组/shot。**当前 `_build_director_plan_input` 明确传入 `relevant_bible={}`**：即使资产库存在角色/场景信息，也不能声称导演规划这一步已经收到完整资产 Bible。这是当前上下文链路最重要的缺口之一。

`generation_beats_for_group` 再把 active plan 的 shot 投影成画面生成用 Beat：包括主体、动作、场景/时间锚点、摄影事实、对白来源与 `asset_requirements`。这里的生产 Beat 是投影单元；不要与剧本语义层的 DramaticBeat 或旧 SQLite VisualBeat 混为同一条记录。没有 active plan 时才退回 legacy Beat。详见[剧本与语义](04-screenplay.md)和[分镜与图像](05-storyboard.md)。

### 3. 分镜：图片提供外观，格子描述提供动作

网格 Prompt 由每个 shot/Beat 的 `visual_description`、布局、画幅、风格投影及参考图映射拼成。图像请求把选定的图片作为 `references` 另传给供应商。强草图模式会把确认的草图网格放在第一参考位，其余参考位受数量限制；草图负责锁构图，身份/场景参考负责外观。生成后同时保留供应商原图、拆格结果与 prompt snapshot，后者含参考文件的 SHA256。因而“模型看到了某角色”应以 snapshot 中的引用和发送顺序为证据，不能只看资产库是否有这张图。

### 4. H3：先优化运动 Prompt，再生成视频

组视频入口不接受浏览器直接提交任意 frame path 或 Beat 正文。API 依据服务端状态确认 plan、render、视频方案和 workflow，并冻结首尾帧及参考图的 snapshot ID/digest。Runner 构造单 shot 或相邻双 shot segment：通常分别对应 I2VA 或 FL2VA，最终以有效帧、mode 与 workflow 能力判断。H3 优化器把画面描述、对白/旁白、前后镜头摘要、DirectorPlan、风格、帧哈希和连续性信息编成文本模型输入；有冻结分镜图片时，还可让文本模型看图作视觉判断。优化产物再进入视频 adapter。**分镜图片送给优化模型看**与**作为 RunningHub 的首尾帧或 Ref 输入**是两件事，应分别核对。

组视频的 `.manifest.json` 记录 segment、workflow、参考输入、优化/质量与 provider attempt 等证据；生成片段再由本地流程组合。它是“这次如何生成”的追溯文件，不是单纯展示用缩略图。更细的模式、质量与失败语义见[视频生成](07-video.md)。

## 一个可追溯的例子

以下是假设示例，说明核对方法，不代表仓库中的真实项目数据：

1. 剧集来源 revision `12` 写到“角色甲握住旧书，转身看向门”；语义 revision `sem-12` 标出该段与角色甲、书、房间。
2. active DirectorPlan `plan-4` 把它编成两个 shots：近景握书、转身中景，并声明角色身份状态、书、房间为资产需求。由于当前 `relevant_bible={}`，这一步的角色外观不能被视为已从资产库注入规划模型。
3. 资产解析选择角色状态 slot 的 version `v3`、道具书 `v2`、房间 `v1`。分镜网格请求包含两个 panel 文案及这三张文件引用；prompt snapshot 记录路径与哈希。
4. 用户选中两个 render cells。视频任务冻结首帧/尾帧与参考快照；H3 优化器根据图、文本和连续性约束写出运动 Prompt；视频 adapter 以冻结的帧和最终 Prompt 调 RunningHub。
5. 要回答“为什么角色脸变了”，逆向检查 manifest → 首帧哈希 → render cell prompt snapshot → `v3` 文件，而不是只读取**现在** slot 的 current version；它可能已变成 `v4`。

## 三个入口不要混写

| 入口 | 生产单位 | 上下文与产物边界 |
| --- | --- | --- |
| NarrativeGroup 主线 | active DirectorPlan 的 group/shot，或回退的 legacy Beats | 分镜网格、选中 cells、组 H3 segment 与 manifest；本页重点 |
| 单 Beat 工作台 | SQLite Beat | `single_video`、Beat 首帧/尾帧与视频池；不自动更新 NarrativeGroup video stage |
| Freezone / 独立画布 | 画布节点及其输入边 | 与剧集 NarrativeGroup 的 revision、资产绑定和 manifest 不是同一套契约；需按节点实现单独追踪 |

独立导演台视频节点正在隔离工作区中开发，不能因为它与组视频同用 MiniMax H3，就把这里的 NarrativeGroup 输入、质量流程和产物语义直接套用到该节点。本文描述当前主仓库可核验的主线。

## 当前缺口与排查顺序

| 现象 | 先确认什么 | 当前边界 |
| --- | --- | --- |
| “资产有角色图，分镜却不像” | slot/current version、叙事组绑定快照、网格 prompt snapshot 的图片路径/哈希/顺序 | 资产存在不代表本次分镜引用了它 |
| “导演方案没用角色设定” | `DirectorPlanInput.relevant_bible` 和实际请求 | 当前构建处传空对象；需单独设计事实注入与版本跟踪 |
| “视频与新分镜不一致” | 视频 task 的 storyboard selection、frame/reference snapshot ID/digest、manifest 中帧与参考证据 | 旧视频不会因新选图自动改写 |
| “同一段的结果无法复现” | source/semantic/plan/style revision、资产版本、prompt snapshot、workflow 参数、provider attempt | 只留最终 Prompt 不足以还原图片与业务决定 |
| “看到两种 Beat 或两种 H3 路径” | 当前入口的任务类型和生产单位 | 语义 DramaticBeat、SQLite Beat、DirectorPlan shot；单 Beat 与组 H3 各自独立 |

实际排查建议从**结果倒查**：先拿组视频 manifest 或分镜 prompt snapshot，找 task scope、group/shot ID 与 revision；再核对冻结的 frame/reference hash；最后回到 DirectorPlan 与资产 slot/version。若缺少其中一环，应明确记录“未留证据”，不要用当前数据库状态推断历史模型输入。
