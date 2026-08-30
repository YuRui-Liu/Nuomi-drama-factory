# 统一智能体路由、风格目录与导演工作台设计

日期：2026-08-30  
状态：已确认，待实现计划  
目标版本：DirectorPlan V2 前端与运行时收口

## 1. 背景

当前系统已有部分 Codex 运行时、DirectorPlan V2、GenerationBatch、VideoSegment 和扩展风格目录能力，但前端与配置层仍沿用旧结构：Codex 无法按任务职责选择；扩展风格缺少分组、重载和诊断；新导演数据结构仍被埋在旧叙事组页面中。

本设计选择“方案 B：统一生产工作台”，并采用破坏性升级：现有数据均为测试数据，不为旧导演结构提供迁移或兼容层。

## 2. 目标与非目标

### 2.1 目标

- 普通文本和智能规划任务可独立选择 `Codex` 或 `model_api`。
- 图片、视频、TTS 供应商与上游规划运行时完全解耦。
- 任务执行前冻结真实运行时、模型、Skill、风格和上下文配置。
- 风格页稳定展示“内置 6 + 扩展 18 + 自定义”，支持动态重载和诊断。
- 导演版本、叙事组、镜头、生成批次和 VideoSegment 收口到同一工作台。
- 失败可定位到具体阶段、真实调用目标和可重试单元。

### 2.2 非目标

- 不兼容旧 DirectorPlan、旧叙事组生产页面和旧 API。
- 不自动迁移旧测试项目的导演数据或素材关联。
- 不让 Codex 替代 GRSAI、MiniMax、RunningHub 等媒体服务。
- 不在本次设计中重做完整任务中心、素材管理或剪辑器。

## 3. 总体架构

系统分为三个边界：

1. **Agent Runtime**：理解、规划、结构化提取、提示词和质量修复。
2. **Media Provider**：图片、视频和语音的真实生成。
3. **Production Domain**：导演版本、叙事结构、生产批次、视频片段和素材结果。

文本智能任务统一调用：

```text
Task Role
  -> Route Resolver
  -> Frozen AgentTaskRoute
  -> CodexRuntimeAdapter | ModelApiRuntimeAdapter
  -> Structured Result + Execution Manifest
```

媒体规划可使用 Agent Runtime，真实生成必须走 Media Provider：

```text
H3 Prompt Planning (Codex / model_api)
  -> Frozen Prompt Pack
  -> MiniMax H3 Provider
  -> Video Asset
```

任何层级不得通过隐藏环境变量或隐式默认值绕开最终路由。

## 4. 设置与导航

设置页新增“运行时与媒体”，包含：

1. **运行状态**：Codex、普通大模型、图像、视频和 TTS 服务可用性。
2. **任务路由**：按任务职责设置运行时、模型、推理强度、Skill 和执行参数。
3. **媒体供应商**：管理真实图片、视频和 TTS 渠道。
4. **上下文与安全**：Skill 白名单、知识范围、超时、并发和权限。

配置采用三级解析：

```text
系统默认 -> 项目覆盖 -> 本次任务临时覆盖
```

前端同时展示配置值、配置来源、本次实际生效值和最近执行结果。项目覆盖提供“恢复继承”，不能用空字段表达含糊状态。

## 5. 智能任务路由

### 5.1 可路由职责

- 剧本解析与规范化；
- 角色、场景、道具提取；
- 整集 DirectorPlan 规划；
- H3 整集提示词包生成；
- H3 失败 Segment 局部修复；
- Cognee 图谱结构化提取；
- 通用提示词优化与质量检查。

任务职责是稳定产品语义，不直接等同后台 task type；多个后台任务可以复用同一职责路由。

### 5.2 AgentTaskRoute

```text
task_role
runtime                 # codex | model_api
model
reasoning_effort
timeout_seconds
max_retries
concurrency
skill_id
skill_version
context_policy
fallback_policy
```

`context_policy` 描述可读取的剧本、角色、场景、道具、风格、历史和知识图谱范围。`fallback_policy` 只允许显式停止、重试或切换到指定备用路由。

### 5.3 冻结与追溯

任务入队前解析最终配置，将 task role、runtime、model、reasoning effort、Skill、context policy、StyleSnapshot、provider route、执行参数和配置来源冻结到 execution manifest。

运行中的任务不受后续设置修改影响。禁止静默回退；Codex 不可用时在启动前快速失败。

## 6. 风格目录

### 6.1 三类风格

- 内置风格：6 个，系统只读；
- 扩展风格：18 个，来自外置目录，只读但可应用；
- 自定义风格：用户创建，可编辑和删除。

扩展风格详情展示中英文名、分类、预览图、正向提示词、负向约束、资产注入规则、版本、来源和项目使用状态。

### 6.2 动态目录与诊断

风格服务支持显式重载，不能永久依赖启动时缓存。API 返回分类、catalog version、discovered / loaded / failed 数量、单项失败原因、loaded_at 和预览资源状态。

前端“刷新”重新请求服务；“重新加载风格库”触发服务重载。缺少预览图只显示占位和诊断，不隐藏条目。

### 6.3 StyleSnapshot

项目应用风格时形成版本化 `StyleSnapshot`。导演规划、角色/场景/道具提示词、宫格出图和 H3 视频提示词读取同一快照，任务完成后仍可追溯实际注入内容。

## 7. 导演领域模型

```text
DirectorPlan
  -> NarrativeGroup
       -> Beat
       -> Shot
  -> GenerationBatch (references ordered Shots)
  -> VideoSegment (references ordered Shots)
```

### 7.1 NarrativeGroup

叙事组表达完整的戏剧动作、情绪目标和连续性边界，不按固定 Beat 数机械分组。Beat 是组内故事节拍，一个组可以包含多个 Beat。

### 7.2 GenerationBatch

生成批次只表达媒体生产约束，包括模型、宫格布局、画幅、分辨率、参考图上限和重试边界，不能改变叙事语义。

布局由目标镜头数与供应商能力共同确定，1 或 2 个镜头不得被强制生成 2x2。宫格分辨率和切分策略协同，切分后不得残留白边。

### 7.3 VideoSegment

VideoSegment 是可独立生成、替换、重试和合成的视频单元，可覆盖一个或多个连续镜头。整集一次生成全部 H3 提示词包；失败 Segment 再局部修复。

默认硬切；只依据确定性叙事关系少量应用转场，以及声音先入或延出。

## 8. 统一导演工作台

旧“叙事组生产”和独立“重新导演分镜”合并为三栏工作台。

### 8.1 左栏：叙事结构与版本

- 当前和历史 DirectorPlan；
- NarrativeGroup 列表；
- Beat 范围、戏剧目标、连续性摘要；
- 版本状态和异常数量。

### 8.2 中栏：镜头与生成批次

- Shot 列表及拆分、合并、排序、跨组移动；
- GenerationBatch 归属；
- 宫格布局、画幅、分辨率、模型和参考图；
- 批次生成、整批重生成、切分与质量状态。

### 8.3 右栏：VideoSegment 与结果

- Segment 覆盖镜头和时长；
- H3 提示词与质量报告；
- 参考图和真实视频模型；
- 进度、结果、失败阶段与局部重试；
- 音频衔接、确定性转场和合成状态。

三栏双向联动：选中任一组、镜头、批次或 Segment，其他栏高亮关联项。

### 8.4 版本审阅

重新导演、版本对比、结构编辑和激活在同一工作台的审阅模式或抽屉中完成。首版不迁移旧素材；新版本激活前仍展示将失效和重建的当前素材范围。

## 9. 状态与错误诊断

统一展示：

```text
规划 -> 提示词 -> 媒体请求 -> 异步查询 -> 素材落库 -> 质量检查
```

错误详情包含失败阶段、实际 runtime/model/provider、task/request/provider job ID、transport 是否调用、冻结快照、截断后的原始错误、局部重试能力和目标。

耗时操作进入任务中心，同时在导演台关联单元显示实时状态和日志摘要，不能长期停在“任务开始 1%”且无心跳。

## 10. 破坏性升级

- 移除旧导演台、旧 API 和旧数据适配层；
- 数据库直接切换到新领域结构；
- 当前测试项目的导演数据允许清空并重新生成；
- 旧媒体文件可留在磁盘，但不自动重建关联；
- 检测到旧结构时提示重建测试项目，不自动转换；
- 不编写旧数据迁移与兼容测试。

破坏性初始化只允许在明确的开发/测试升级流程中执行，不能在普通启动时静默删除数据。

## 11. API 边界

- Agent runtime health 与 capability；
- global/project task route CRUD、resolve 和 effective preview；
- task execution manifest 查询；
- style catalog list、reload、diagnostics 和 project apply；
- DirectorPlan create/list/read/activate；
- NarrativeGroup 与 Shot 结构编辑；
- GenerationBatch plan/generate/regenerate/split；
- VideoSegment prompt-pack/generate/repair/retry；
- impact preview 和版本激活。

具体 URL 在实现计划中按现有规范确定；前端不得依赖数据库字段或内部运行器名称。

## 12. 验收标准

1. 每类普通文本任务可选择 Codex 或普通大模型。
2. 同一项目可让导演规划走 Codex、角色提取走 DeepSeek。
3. 每个任务可查询真实 runtime、model、Skill、provider 和请求标识。
4. 图片、视频、TTS 路由不随文本运行时漂移。
5. 风格页稳定显示内置 6、扩展 18 和自定义分组。
6. 扩展风格无需重启应用即可重载并查看诊断。
7. 导演台明确显示 NarrativeGroup、GenerationBatch 和 VideoSegment 三层关系。
8. 1 或 2 镜头不会错误生成 4 宫格；切分图无白边。
9. 单个批次或 Segment 可局部重试，不重跑整集规划。
10. Codex 或供应商不可用时快速失败并给出可行动诊断。
11. 刷新或重启后，配置、风格目录和任务状态一致。
12. 清理旧测试数据后，新项目可从剧本导入完整跑通生产链路。

## 13. 验证策略

- 单元测试：路由解析、冻结清单、StyleSnapshot、目录重载、宫格布局和切分。
- API 契约：全局/项目覆盖、effective preview、风格诊断、DirectorPlan 和局部重试。
- 前端交互：任务路由表、风格分组、三栏联动、审阅模式和错误详情。
- 领域集成：整集规划到 GenerationBatch、H3 pack、VideoSegment。
- 真实冒烟：一条 Codex/DeepSeek 文本链路、一条 GRSAI 图片链路和一条 MiniMax 视频链路。

## 14. 实施顺序

1. 统一路由模型、执行清单和运行时适配器；
2. 扩展风格目录动态加载与 StyleSnapshot；
3. 新导演领域 API 和测试数据重建工具；
4. 设置页和风格页；
5. 统一三栏导演工作台；
6. 删除旧页面、旧 API 和兼容逻辑；
7. 分层验证与少量真实冒烟。

在新链路具备契约测试前不删除旧入口；删除只在新前后端闭环可运行后进行。
