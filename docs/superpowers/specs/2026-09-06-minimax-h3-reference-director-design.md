# MiniMax H3 带 Ref 导演台设计

## 1. 背景与目标

当前叙事组视频生产只注册 `runninghub:minimax-h3`，默认使用 RunningHub 工作流 `2089723723468328961`。它以每个视频单元的首帧或首尾帧作为硬边界，支持 I2V/FL2V，但不会把角色、场景或道具参考图作为全局主体约束。

本设计新增第二个独立视频模型 `runninghub:minimax-h3-ref`，接入 RunningHub 工作流 `2096502793044582401`。新模型在保留每个视频单元首帧/尾帧输入的同时，允许整个叙事组共用一组全局 Ref 图和可编辑的 `Subject/Picture` 映射。原导演台的模型 ID、工作流、行为和项目默认地位全部保留。

目标是让用户能主动选择“MiniMax H3 导演台 · Ref”，自动带入当前叙事组涉及的角色、场景和关键道具参考图，也可加入临时上传；提交时将 Ref、主体映射和首尾帧共同编译进新工作流的 `timeline_data`，并让每次任务可追踪、可恢复、可用同一输入重试。

## 2. 已确认决策

- 采用“独立模型 + 独立 Ref 适配器”，不在原模型上增加开关。
- 原 `runninghub:minimax-h3` 继续作为默认模型；新模型必须由用户主动选择。
- 新工作流 ID 固定默认值为 `2096502793044582401`，可在 RunningHub 设置中修改。
- Ref 为叙事组级全局输入，所有视频单元共用同一组，不提供分段覆盖。
- 自动候选包含角色身份图、场景主图和关键道具参考图。
- 用户可取消自动候选、调整顺序、编辑主体描述，也可临时上传新图。
- 每张 Ref 的 `<Subject n> 是来自 <Picture n> 的资产描述`由资产元数据自动生成，提交前可编辑。
- 全局 Ref 上限属于 RunningHub 供应商设置，范围为 1–10，默认 5。
- 上限只计算全局角色、场景、道具及临时 Ref，不计算各视频单元原有的首帧/尾帧。
- 新模型必须同时提交全局 Ref 与首帧/尾帧；Ref 不能替代帧约束。
- 0 张 Ref、超过当前上限或缺少必要首帧时阻止提交，不自动切回原导演台。

## 3. 非目标

- 不改变原导演台的 payload、提示词、默认配置或历史任务解释。
- 不提供纯 Ref2V、纯 T2V、视频参考或音频参考入口。
- 不提供视频单元级 Ref 覆盖。
- 不把临时上传自动登记为角色、场景或道具正式资产。
- 不开放任意 RunningHub 节点或原始 JSON 编辑。
- 不复制 RunningHub 上传、并发、轮询、恢复、下载或质检基础设施。
- 不在自动化测试中调用付费 RunningHub 任务。

## 4. 架构

### 4.1 双模型注册

视频工作流注册表包含两个一等模型：

| 稳定模型 ID | 展示名 | 适配器 | 工作流设置键 | 默认 |
|---|---|---|---|---|
| `runninghub:minimax-h3` | RunningHub MiniMax H3 | `minimax-h3` | `video_minimax_h3` | 是 |
| `runninghub:minimax-h3-ref` | RunningHub MiniMax H3 · Ref | `minimax-h3-ref` | `video_minimax_h3_ref` | 否 |

两者都只在 `narrative_group` 场景暴露，并继续支持自动、首帧和首尾帧的产品模式。新注册项额外声明：

- `requires_global_references=true`；
- `reference_source_kinds=character_identity,scene_base,prop`；
- `min_references=1`；
- `max_references` 读取当前 RunningHub 供应商配置；
- 使用独立的 Ref payload 编译器。

注册表顺序与显式默认标记必须保持原模型为默认。项目只有在用户主动切换并保存后才持久化新模型；不能因为发现可用 Ref 而自动改变项目默认。

### 4.2 工作流配置解析

`RunningHubWorkflowSettings` 增加：

```json
{
  "video_minimax_h3": "2089723723468328961",
  "video_minimax_h3_ref": "2096502793044582401",
  "video_minimax_h3_ref_max_images": 5
}
```

`video_minimax_h3_ref_max_images` 只接受 1–10 的整数。设置页把两个工作流分别显示为“MiniMax H3 导演台 Workflow ID”和“MiniMax H3 带 Ref 导演台 Workflow ID”，并显示“带 Ref 导演台全局 Ref 上限”。

当前运行时按媒体能力映射到唯一工作流字段，无法区分两个都支持 I2V/FL2V 的模型。新设计改为由注册项提供工作流设置键，适配器按已解析的注册项取得远端 Workflow ID。通用运行时不根据 `MediaCapability` 猜测具体导演台；旧调用入口仍映射到 `video_minimax_h3` 以保持兼容。

### 4.3 能力语义

现有 `video.ref2va` 明确禁止首帧/尾帧，不能用于本功能。新模型表达的是“帧约束 + 全局 Ref”的组合能力，而不是放宽 `video.ref2va` 的既有不变量。

内部能力校验应明确区分：

- Ref-I2V：必须有首帧、至少一张全局 Ref，不允许尾帧；
- Ref-FL2V：必须有首帧、尾帧和至少一张全局 Ref；
- auto：每个视频单元有尾帧时选择 Ref-FL2V，否则选择 Ref-I2V。

可以通过新增明确的组合能力枚举，或在注册表定义中以帧策略和 Ref 策略组合校验；无论具体代码形态如何，都不能修改现有 `video.ref2va`、`video.i2va`、`video.fl2va` 的输入不变量。

### 4.4 共享与隔离

新适配器只负责：

1. 解析并校验叙事组级 Ref 配置；
2. 上传 Ref 并生成稳定的 Picture 顺序；
3. 编译全局 `refs` 与 `subject_definitions`；
4. 把它们与现有视频单元首帧/尾帧 payload 合并；
5. 生成包含 Ref 的幂等输入和 Manifest 证据。

上传客户端、并发租约、持久任务、轮询、重启恢复、结果下载、媒体探测、质量检查和产物复制继续复用现有 H3 基础设施。

## 5. Ref 候选与用户配置

### 5.1 自动候选

服务端根据叙事组覆盖的 Beat 和资产关系收集候选：

1. 角色身份图；
2. 场景主图；
3. 关键道具参考图。

主排序按上述类型优先级，类型内部按其在叙事组中的首次出现顺序，再以稳定资产 ID 作为平局规则。候选必须具有可解析的项目内资产路径；缺图条目作为带原因的不可用项返回，不进入默认选择。

服务端复用 `ReferenceCandidate`、`ReferenceKind` 与引用规划能力，但选择结果必须完整返回容量超限项，不能静默丢弃。候选数超过当前上限时，界面默认选择排序靠前的项目，同时展示所有未选项与“已达到上限”提示，用户确认后才能生成。

### 5.2 Ref 配置模型

叙事组保存一份版本化的 `video_reference_settings`：

```json
{
  "workflow_id": "runninghub:minimax-h3-ref",
  "revision": 3,
  "references": [
    {
      "reference_id": "character:char-12:identity-main",
      "source_kind": "character_identity",
      "source_asset_id": "char-12",
      "temporary_upload_id": null,
      "label": "沈璃",
      "subject_description": "<Subject 1> 是来自 <Picture 1> 的沈璃，锁定面容、发型与服装。"
    }
  ]
}
```

数组顺序就是 Picture 顺序。保存时服务端重新生成连续的 `<Subject 1…N>` 和 `<Picture 1…N>` 编号骨架，并校验用户编辑后的描述仍引用自己的编号，禁止重复、跳号或跨项引用。

Ref 配置使用 revision 乐观锁。模型切换到原导演台时保留该配置但不读取；再次切回新模型时可以恢复。配置不属于已生成视频，修改 Ref 后旧视频仍可预览，但页面标记“Ref 设置已变化，需重新生成”。

### 5.3 临时上传

临时上传通过项目和叙事组作用域内的 multipart 接口写入视频运行目录，并复用现有图片解码、格式、尺寸和路径安全校验。API 只向前端返回临时引用 ID 与缩略图 URL，不暴露服务端任意路径。

临时图片：

- 可与自动资产候选一起排序和编辑描述；
- 计入当前全局 Ref 上限；
- 不写入角色、场景或道具资产库；
- 在 Ref 配置仍引用它、历史任务快照仍需要它时保留；
- 删除 Ref 配置中的临时项只解除引用，不删除历史任务已经冻结的输入证据。

## 6. 前端交互

### 6.1 模型选择

模型目录返回两个可用模型时，叙事组页显示现有模型选择器。原模型仍为初始默认。新模型展示为“RunningHub MiniMax H3 · Ref”。

选中新模型后，视频卡片新增：

- `N / 上限` Ref 就绪状态；
- 已选 Ref 缩略图及 `Picture n`；
- “管理 Ref”按钮；
- 配置缺失、Ref 为空、超过上限或帧输入不完整的禁用原因。

原模型不显示这些内容，避免用户误以为 Ref 会参与原工作流。

### 6.2 管理 Ref 对话框

对话框一次编辑整个叙事组的全局 Ref：

- 展示自动候选的缩略图、来源类型、资产名与覆盖 Beat；
- 支持勾选/取消、拖动排序和临时上传；
- 每个已选项展示实时 `Picture n` 和可编辑 Subject 描述；
- 展示当前供应商配置上限；
- 0 张、超过上限、描述为空或编号不一致时禁用确认；
- queued/running 状态下禁止修改当前 revision 的 Ref 配置。

关闭未保存对话框不改变已保存配置。确认后先保存 Ref 配置 revision，再允许发起生成。

### 6.3 生成行为

生成请求继续提交稳定模型 ID、模式、画幅、视频计划 revision、视频设置 revision 和视频 stage revision，并新增 Ref 配置 revision。后端重新读取并校验全部 revision 后才预留新视频任务。

新模型的生成按钮要求：

- 至少一张有效 Ref；
- Ref 数量不超过当前供应商上限；
- 每个视频单元有首帧；
- 请求 FL2V 的视频单元有尾帧；
- 视频计划和三类设置 revision 均未过期。

任一条件不满足时提供具体修复提示，不自动切换、回退或删减输入。

## 7. RunningHub Payload

### 7.1 工作流 Profile

为 `2096502793044582401` 增加独立、版本化的 profile。与提供的 API JSON一致，首版只允许绑定节点 `12.timeline_data`，输出只接受节点 7 的视频。不得让客户端透传节点 12 的其他采样或模型字段。

### 7.2 上传与序列化

一次任务先收集去重后的首帧、尾帧和全局 Ref。所有文件上传成功后才创建远端任务。

全局 Ref 按保存顺序序列化到：

```json
{
  "global": {
    "refs": [
      {"index": 0, "imageFile": "<uploaded-ref-1>", "fileName": "", "type": "input", "subfolder": ""}
    ],
    "prompt": "subject_definitions:\n<Subject 1> 是来自 <Picture 1> 的沈璃，锁定面容、发型与服装。",
    "commonEnabled": true,
    "commonCollapsed": true
  }
}
```

`index` 从 0 开始，文本中的 Subject/Picture 从 1 开始。每个视频单元继续序列化 `startImage`、可选 `endImage`、`genImage`、keyframe 和原有镜头 Prompt。全局 Ref 与首尾帧使用独立字段；全局 Ref 上限不包含帧图。

新适配器必须从提供的工作流 fixture 固化精确 task type、timeline mode 和 Ref/帧组合字段。不能仅通过向原 payload 追加未知字段实现，也不能在远端拒绝后重试为无 Ref payload。

### 7.3 Prompt 语义

`global.prompt` 只保存全局 `subject_definitions`。每个视频单元原有的导演 Prompt 继续保存镜头、动作、声音和首尾帧对齐信息，并可引用 `<Subject n>`。

Picture 编号存在两个上下文：全局 `<Picture n>` 指全局 Ref，镜头 Prompt 中带 `(from Shot 1)` 的 Picture 指该单元首尾帧。新编译器必须保持上下文标识完整，并通过 fixture 测试防止用户编辑的 Subject 描述覆盖镜头帧对齐段。

## 8. 稳定快照、幂等与历史解释

任务提交时冻结：

- 稳定模型 ID 与实际 Workflow ID；
- Ref 配置 revision 和供应商上限；
- 每张 Ref 的顺序、来源类型、稳定资产 ID或临时上传 ID、标签、最终描述、SHA-256；
- 首尾帧路径对应的 SHA-256；
- 视频单元 Prompt、帧范围、画幅、分辨率和工作流参数；
- 编译后的 `timeline_data` 摘要与编译器版本。

幂等输入必须包含 Ref 哈希、顺序和最终描述。任意 Ref 内容、排序或文字变化都会产生不同幂等键；单纯重试同一冻结任务则复用原输入，不重新读取当前资产或当前 Ref 设置。

视频结果区和提示词抽屉展示模型、Workflow ID、Ref 数量、Picture/Subject 映射、来源与哈希摘要。历史展示只读任务快照，不按当前项目资产重新推导。

## 9. 错误处理

以下错误在上传或调用 RunningHub 前返回，不创建远端任务：

- 新工作流未配置、ID 非数字或供应商凭据不可用；
- Ref 上限不在 1–10；
- Ref 数量为 0 或超过当前上限；
- Ref ID 重复、排序无效、文件缺失、图片解码失败；
- Subject 描述为空或 Subject/Picture 编号不一致；
- 视频单元缺少首帧，或 FL2V 单元缺少尾帧；
- 视频计划、视频参数、Ref 配置或 stage revision 冲突；
- 选择的模型已停用或不适用于叙事组。

上传阶段只要任一输入失败，就不提交 RunningHub；已完成的临时远端上传可以由现有清理策略处理。提交成功后立即保存 provider task ID。远端失败、取消、超时或返回非节点 7 视频时保留冻结快照、任务号和规范化错误，允许同输入重试。

不得自动换回原导演台、删掉 Ref、删掉尾帧或截断超过上限的列表来换取成功。

## 10. 兼容性关卡

提供的 API JSON展示的是 R2V 示例，工作流说明同时区分 `ref2va` 与 `fl2va` 底模；本地 fixture 本身不能证明工作流 `2096502793044582401` 能在同一视频单元中同时使用全局 Ref 与首帧/尾帧。

因此实施包含一个明确关卡：

1. 单元与契约测试先证明系统生成了预期的混合 payload；
2. 在用户单独授权 RunningHub 额度后，用一张 Ref、一个首帧和最短单元执行真实烟测；
3. 记录远端 task ID、提交 payload 摘要、最终视频与媒体探测结果；
4. 只有烟测成功，才能宣称该部署工作流支持混合输入。

如果烟测失败，产品保持新模型不可用或明确报告 `workflow_hybrid_input_unsupported`。不能把失败解释为成功，也不能隐式降级为纯 R2V、I2V 或 FL2V。

## 11. 测试与验收

### 11.1 后端

- `RunningHubWorkflowSettings` 接受两个独立 Workflow ID，拒绝非数字 ID。
- Ref 上限接受 1 和 10，默认 5，拒绝 0、11、非整数。
- 注册表同时返回两个模型，原模型仍为默认，各自使用正确适配器与配置键。
- 原模型配置缺失只影响原模型；Ref 工作流配置缺失只影响新模型。
- 自动候选按角色、场景、道具和首次出现顺序稳定排序，并报告容量超限项。
- Ref 配置校验覆盖 0 张、超过上限、重复 ID、缺图、空描述、错号和 revision 冲突。
- 临时上传不能越过项目作用域或引用任意服务器路径。
- 混合 payload 同时包含 `global.refs`、`subject_definitions`、每段首帧和可选尾帧。
- 节点绑定只允许 `12.timeline_data`，结果只接受节点 7 视频。
- Ref 顺序、内容或描述变化会改变幂等键；同一冻结快照重试保持一致。
- 原 H3 payload 的回归 fixture 保持字节级语义不变。
- 上传、远端失败、超时、重启恢复和质量失败保留完整任务证据且不降级。

### 11.2 前端

- 首次进入仍选中原导演台，用户可主动选择新导演台并保存项目默认。
- 只有新模型显示 Ref 状态和管理入口。
- 自动候选、角色/场景/道具标签、缩略图、临时上传、取消、拖动排序和描述编辑正确。
- 上限从供应商目录读取；1–10 的设置修改能反映到对话框和生成按钮。
- 0 张、超过上限、描述非法或帧不完整时禁止提交并显示具体原因。
- queued/running 时禁止修改 Ref 配置。
- Ref 设置改变后旧视频仍可预览，同时显示“需重新生成”。
- 生成请求提交 Ref revision，不提交服务器路径或 RunningHub 节点字段。
- 切回原模型后不发送 Ref 字段，也不丢失已保存的 Ref 配置。

### 11.3 集成与真实烟测

- MockTransport 覆盖上传去重、混合 payload、节点 7 下载、任务恢复和同输入重试，不产生真实费用。
- 真实烟测必须由用户单独授权，只运行一张 Ref、一个首帧、最低成本短视频。
- 真机验收核对 provider task ID、工作流 ID、最终文件、SHA-256、时长、尺寸、音轨和可播放性。

## 12. 成功标准

- 原 MiniMax H3 导演台继续存在、继续默认、行为和历史解释不变。
- 新模型可独立配置并使用 Workflow `2096502793044582401`。
- 用户可为整个叙事组管理全局角色、场景、道具和临时 Ref，默认上限 5，可在 RunningHub 设置中调整为 1–10。
- 系统自动生成并允许编辑连续、稳定的 Subject/Picture 映射。
- 新任务同时携带全局 Ref 和每个视频单元的首帧/可选尾帧，不静默省略任何一类输入。
- 每个任务都能追溯模型、工作流、Ref 顺序与哈希、主体描述、首尾帧和最终输出。
- 所有本地自动化测试通过；在宣称真实混合能力前完成一次明确授权的 RunningHub 烟测。
