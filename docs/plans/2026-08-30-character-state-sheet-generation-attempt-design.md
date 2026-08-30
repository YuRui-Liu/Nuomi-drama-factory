# NuomiDrama 角色状态表与统一生成记录设计

日期：2026-08-30
状态：已确认
方案：B——统一 `GenerationAttempt`，保留身份锚点并采用 Higgsfield 三栏角色状态表

## 1. 背景与问题

NuomiDrama 当前已经存在角色基础肖像、身份肖像、服装参考和多视角身份图，但这些资产的职责、采用状态与历史版本没有被统一表达：

- 基础肖像负责面部身份，但与身份状态图之间缺少明确的继承关系。
- 当前身份图采用四栏布局：面部特写、全身正面、四分之三全身、全身背面；与 Higgsfield 的生产型三栏角色表并不一致。
- 重新生成通常围绕同一规范文件及目录备份展开，缺少结构化候选、质检结果和“当前采用版本”指针。
- 下游叙事组图、单镜图和返工生成虽然能够引用组合角色图，但无法稳定回答引用的是哪个候选、为何采用、使用了哪些风格和服装参考。

本设计将角色参考统一为“身份锚点 + 角色状态表”两层资产，并接入统一的 `GenerationAttempt` 记录。

## 2. 目标

1. 身份、年龄、服装、发型、体型和剧情状态具有明确且可追踪的资产关系。
2. 角色状态表采用适合影视连续性生产的三栏布局。
3. 每次生成均保留输入、模型、参数、候选输出、质检结果和采用关系，不覆盖历史记录。
4. 主角与常驻角色必须由用户明确采用；临时配角可以自动采用首个通过质检的候选。
5. 被采用的角色状态表默认进入下游出图，但可在镜头级手动取消。
6. 保留 RunningHub MiniMax H3 作为核心视频生成能力，不改变其供应商地位。

## 3. 非目标

- 本阶段不接入 Higgsfield 供应商或训练 Soul ID。
- 本阶段不重建所有历史任务、生产 DAG 或 H3 manifest。
- 本阶段不处理生物、怪物、载具等特殊资产表格式。
- 本阶段不强制给每一次临时换装建立独立状态，避免状态资产无限膨胀。

## 4. 核心资产模型

### 4.1 身份锚点 `CharacterIdentityAnchor`

身份锚点回答“这个人是谁”，采用高清中性面部或面部及肩部肖像。它负责锁定：

- 五官与脸型
- 肤色和基础年龄段
- 眼睛、眉毛及主要身份特征
- 不随服装变化的稳定特征

身份锚点继续复用现有角色基础肖像和身份肖像能力，不被三栏状态表替代。

### 4.2 角色状态 `CharacterState`

角色状态回答“这个角色在当前剧情阶段如何出现”。建议字段：

```text
state_id
project_id
character_id
identity_id
name
age_or_period
appearance
body_shape
hair
costume
makeup
injuries
accessories
continuity_priority
approved_attempt_id
created_at
updated_at
```

典型状态包括：日常装、工作装、战斗装、童年、老年、受伤后、淋雨后和重要变身状态。

普通一次性换装可以仅使用服装文字或服装参考图；重复出现或影响连续性的换装必须建立独立角色状态。

### 4.3 角色状态表 `CharacterStateSheet`

状态表是一张横向三栏资源图：

| 左栏 | 中栏 | 右栏 |
|---|---|---|
| 全身正面 | 全身背面 | 面部及肩部特写 |

规范如下：

- 三栏等宽，使用细而低对比度的分隔线。
- 背景为统一的中性中灰摄影棚背景，不使用场景、渐变或装饰。
- 正面和背面必须完整展示头顶至鞋底，姿态中性，手臂略离身体。
- 特写采用中性表情，眼睛清晰且有自然眼神光，避免反光和强阴影。
- 三栏必须保持同一身份、年龄、肤色、发型、服装、体型、比例和饰品。
- 禁止文字、标签、水印、道具、额外人物和环境元素。
- 项目视觉风格控制绘制媒介；角色状态表只锁身份与外观，不锁实际镜头的场景照明。

四分之三视角不再进入规范状态表。需要时由已采用状态表派生生成，避免基础图中出现额外低分辨率面孔并增加身份漂移风险。

## 5. 统一生成记录

每次状态表生成建立一条 `GenerationAttempt`：

```text
attempt_id
project_id
stage = character_state_sheet
asset_scope:
  character_id
  identity_id
  state_id
parent_attempt_id
repair_reason
task_id
provider_task_id
prompt_snapshot
style_snapshot_id
style_snapshot_hash
typed_references:
  identity_anchor
  costume_reference
  project_style
model
workflow
parameters
aspect_ratio
resolution
outputs
qc_result
status
created_at
```

`outputs` 可以保存一批候选及其独立状态。`CharacterState.approved_attempt_id` 指向正式采用的候选；旧候选不删除、不覆盖。

## 6. 生成与审批流程

```text
身份锚点
  + 角色身份描述
  + 角色状态描述
  + 服装参考
  + 项目视觉风格
        ↓
生成 2～4 个三栏候选
        ↓
自动质量检查
        ↓
主角/常驻角色：等待用户采用
临时配角：自动采用首个通过质检的候选
        ↓
写入 approved_attempt_id
        ↓
成为叙事组图、单镜图与返工生成的默认角色参考
```

审批规则已经确认：

- 主角和常驻角色必须手动采用。
- 临时配角可以自动采用第一张通过质检的候选。
- 自动采用后用户仍可切换到其他候选。
- 重新生成只增加新尝试，不隐式替换当前采用版本。

## 7. 自动质检

状态表候选至少检查：

1. 正面全身是否头脚完整且没有裁切。
2. 背面是否为真正背面，构图、比例是否与正面一致。
3. 特写与全身是否为同一身份。
4. 三栏服装、发型、体型、肤色和饰品是否一致。
5. 是否为统一中性灰背景和柔和均匀照明。
6. 是否出现文字、标签、道具、环境、额外人物或错误分栏。
7. 是否存在面部畸变、肢体异常、强烈阴影、眩光或过曝。

质检结果结构应包含：

```text
passed
score
checks[]
warnings[]
rejection_reasons[]
```

自动质检只负责筛除明显错误，不替代主角和常驻角色的人工审美判断。

## 8. 下游默认引用规则

### 8.1 叙事组图与单镜图

- 已采用角色状态表默认开启，可手动取消。
- 项目视觉风格默认开启，可手动取消。
- 匹配的场景参考默认开启，可手动取消。
- 镜头级界面必须显示真实引用来源，而不是只显示“已引用”。
- 面部近景在模型支持多参考图时追加身份锚点。
- 全景、动作镜头优先使用状态表以保持服装与身体比例。
- 模型只支持一张参考图时，优先采用角色状态表。

### 8.2 视频生成

RunningHub MiniMax H3 继续作为核心视频能力。视频生成优先接受已经锁定角色与场景的起始帧、结束帧或叙事组图；只有具体工作流支持直接角色参考时，才额外传递状态表。

视频任务的提示词优化、画幅比例和引用清单仍必须记录到对应的 `GenerationAttempt`，以便定位供应商输入和成片之间的偏差。

## 9. 前端交互

角色页的身份卡扩展为“角色状态”卡片：

- 状态名称与连续性等级。
- 身份锚点缩略图。
- 服装参考缩略图。
- 当前采用的三栏状态表。
- “生成候选”“重新生成”“查看历史”“采用此图”操作。
- 候选画廊显示质检分数、失败原因、模型和生成时间。
- 当前采用项必须有明确标识。

叙事组和镜头引用选择器显示：

- 视觉风格
- 角色状态表
- 身份锚点（仅在适用时）
- 场景参考

每项默认启用并允许单独取消；取消仅作用于当前镜头或当前生成尝试，不删除资产。

## 10. 兼容与迁移

1. 现有角色基础肖像迁移为身份锚点，不重新生成。
2. 现有四栏身份图保留为历史资产，可以继续被旧任务读取。
3. 新生成统一使用三栏状态表规范。
4. 存在旧身份图但没有状态记录时，为其建立“默认状态”，并将旧图标记为 legacy 输出。
5. 现有目录备份继续可读，但新候选以结构化 `GenerationAttempt` 为准。
6. 旧接口暂时保留，通过适配器写入新记录，待前后端稳定后再移除旧写法。

## 11. 建议实施批次

### 第一批：模型与生成闭环

- 新增 `CharacterState` 与 `approved_attempt_id`。
- 建立通用 `GenerationAttempt` 最小数据结构。
- 将现有四栏提示词替换为三栏状态表规范。
- 生成多个候选并保留历史。
- 增加采用、取消采用和候选查询接口。
- 保持现有下游接口兼容。

### 第二批：前端与默认引用

- 增加角色状态卡和候选审批画廊。
- 叙事组和镜头页展示真实引用来源。
- 实现视觉风格、角色状态表、身份锚点和场景参考的默认启用与手动取消。
- 将实际引用快照写入每条 `GenerationAttempt`。

### 第三批：质检与迁移

- 接入规则质检和视觉质检。
- 完成旧四栏图与目录备份的兼容迁移。
- 添加主角人工审批闸门和临时配角自动采用策略。
- 补充端到端连续性测试与真实模型冒烟测试。

## 12. 验收标准

- 新角色状态表稳定输出全身正面、全身背面和面部肩部特写三栏图。
- 主角与常驻角色在未采用候选前不能作为默认角色状态进入新镜头生成。
- 临时配角可以自动采用通过质检的候选，且用户能切换。
- 重新生成不会覆盖已采用版本或历史候选。
- 镜头生成记录可以还原当次使用的视觉风格、角色、场景、提示词、模型、参数和画幅。
- 用户可以在镜头级取消任意默认参考，且取消不修改全局角色资产。
- RunningHub MiniMax H3 现有视频生成能力和工作流配置保持可用。
- 旧角色图和历史项目仍可打开，不发生强制性破坏迁移。

## 13. 参考资料

- Higgsfield Academy：Character sheets in Soul Cinema
  https://higgsfield.ai/academy/courses/santiago-cinematic/character-sheets-in-soul-cinema
- Higgsfield Academy：Age your character for flashbacks
  https://higgsfield.ai/academy/courses/santiago-cinematic/age-your-character-for-flashbacks
- Higgsfield Academy：Stage 2 — Building Character, Location, and Prop Assets
  https://higgsfield.ai/academy/courses/blockbuster-4k/stage-2-building-character-location-and-prop-assets
- Higgsfield Skills
  https://github.com/higgsfield-ai/skills
