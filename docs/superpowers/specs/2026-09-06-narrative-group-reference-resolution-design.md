# 叙事组生成前引用解析与补救设计

## 背景

叙事组生成前引用弹窗目前直接展示解析到的图片引用。该结构无法完整表达导演方案的资产需求：道具没有图片时会消失；场景状态可能以 `基础场景_变体` 的扁平名称出现；用户也不能从项目资产或本地文件自由追加本次生成所需的图片。

`谢家碑坊_暴雨天井` 是本次问题的代表案例。项目中存在基础场景「谢家碑坊」，导演方案表达的是其「暴雨天井」变体，但当前解析器把完整字符串当作独立场景 ID，因而报告主图缺失。

## 目标

- 将导演方案中的资产需求与用于生成的具体图片分开表达。
- 识别基础场景与场景变体；变体不存在时自动建立待确认的草稿变体。
- 道具缺少资产或图片时仍显示需求，并提供补救操作。
- 允许从项目资产库或本地文件自由追加图片。
- 本地上传默认仅用于本次生成，可由用户选择保存到项目资产库。
- 保存本次生成的引用决策快照，支持任务详情追溯。

## 非目标

- 不自动将相似名称的已有变体静默绑定到当前需求。
- 不要求所有缺失道具必须补齐后才能生成。
- 不在关闭弹窗时保存普通勾选状态。
- 不改变项目资产中心现有的人物、场景和道具编辑职责。

## 核心模型

### ReferenceRequirement

`ReferenceRequirement` 表达「导演镜头需要什么」，不依赖图片是否存在。

字段：

```text
id
kind: character_identity | scene_base | scene_variant | prop
entity_id
base_entity_id
variant_id
label
shot_ids
required
source
status
candidate_asset_ids
available_actions
warning
```

其中 `status` 可取：

- `matched`：已匹配正式资产及有效图片。
- `fallback`：使用默认肖像或基础场景图。
- `draft_variant`：已建立草稿变体，等待用户处理。
- `missing_asset`：项目中不存在对应资产记录。
- `missing_image`：存在资产记录，但没有有效参考图。
- `temporary`：当前由本次临时上传图片满足。
- `ignored`：用户明确忽略。
- `invalid`：文件丢失、格式不支持或路径不安全。

### ReferenceBinding

`ReferenceBinding` 表达「本次使用什么满足需求」。

字段：

```text
requirement_id
decision: project_asset | fallback | temporary_upload | ignored
asset_id
asset_kind
image_path
persist_upload
target_entity_id
```

### ReferenceDecisionSnapshot

任务提交时保存不可变快照，至少记录：

- 需求及覆盖镜头；
- 最终选用的正式资产和临时图片；
- 使用的回退项；
- 用户明确忽略的项目；
- 生成时实际存在且通过安全校验的图片；
- 风格、模型和分辨率选择。

## 场景变体解析

新数据应直接提供结构化的 `scene_id` 与 `variant_id`。旧数据仅有扁平字符串时采用兼容解析：

1. 读取项目现有基础场景名称。
2. 对输入执行最长基础场景名前缀匹配。
3. 仅当剩余文本以 `_` 分隔且非空时，将剩余部分作为 `variant_id`。
4. 无可靠基础场景匹配时保留 `missing_asset`，不进行模糊绑定。

示例：

```text
输入：谢家碑坊_暴雨天井
scene_id：谢家碑坊
variant_id：暴雨天井
初始状态：draft_variant
```

若变体记录不存在，系统自动建立未确认的草稿变体。用户在提交生成前必须选择以下一项：

- 确认草稿变体；
- 改绑已有变体；
- 使用基础场景主图；
- 上传本次临时图片。

自动建立草稿不等于自动确认，也不应覆盖同名正式变体。

## 道具需求

导演方案中的 `prop` 需求必须进入引用预览，即使项目不存在对应道具或参考图。

- 无资产记录：`missing_asset`。
- 有道具、无有效图片：`missing_image`。
- 有有效参考图：`matched`。

缺失时提供：选择已有道具、新建道具、本地上传、明确忽略。忽略属于显式决策，写入任务快照；它不是硬阻塞条件。

## 弹窗信息架构

采用「问题优先」布局。

### 待处理问题

置于弹窗主要位置，展示 `fallback`、`draft_variant`、`missing_asset`、`missing_image` 和 `invalid`。每张卡片显示资产类型、规范名称、覆盖镜头、当前状态和就地补救操作。

### 已匹配引用

默认折叠，标题显示已匹配项目数和图片数。展开后可查看缩略图、来源、覆盖镜头并取消选择。

### 自由追加

独立于自动识别结果，允许：

- 从项目资产库选择人物身份图、场景基础图、场景变体图或道具图；
- 从本地上传图片。

本地上传默认只用于本次生成。用户可勾选「同时保存到项目资产库」，随后必须选择资产类型、目标实体；保存到场景时还需选择基础场景和变体。

### 提交

提交按钮显示引用和忽略数量，例如「生成 · 3 张参考图 · 1 项已忽略」。

`draft_variant` 必须处理后才能提交。`missing_asset` 和 `missing_image` 可以补充或明确忽略；仍有未处理项时，点击提交触发二次确认，不静默跳过。

## 状态转换

```text
自动解析
  -> matched
  -> fallback -> project_asset | temporary | ignored
  -> draft_variant -> confirmed_variant | existing_variant | fallback | temporary
  -> missing_asset -> project_asset | created_asset | temporary | ignored
  -> missing_image -> project_asset | temporary | ignored
  -> invalid -> project_asset | temporary | ignored
```

只有 `matched`、已确认的 `fallback`、`temporary` 和 `ignored` 可以进入最终决策快照。

## 组件边界

### ReferenceRequirementResolver

从导演镜头的 `asset_requirements` 生成规范需求；负责结构化场景变体和旧扁平名称兼容，不负责查找图片。

### ReferenceAssetMatcher

从项目人物身份、场景与变体、道具资产中查找候选项，返回状态、警告和可执行动作。缺图需求仍保留在输出中。

### ReferenceDecisionService

验证用户选择，处理回退、改绑、忽略和临时上传，生成 `ReferenceDecisionSnapshot`。执行端只读取该快照，不重新推断用户意图。

### ReferenceUploadService

管理临时上传文件。只有 `persist_upload=true` 时才写入正式资产路径和资产记录；失败时保留临时引用，并向用户报告正式保存失败。

### 前端组件

- `UnresolvedReferenceSection`
- `MatchedReferenceSection`
- `FreeReferencePicker`
- `ProjectAssetPicker`
- `ReferenceUploadDialog`
- `UnresolvedSubmissionDialog`

## API 契约

引用预览响应需要同时返回 `requirements` 和 `bindings`，不能只返回已有图片。建议新增或演进为：

```json
{
  "requirements": [],
  "bindings": [],
  "style": {},
  "limits": { "max_images": 9 },
  "warnings": []
}
```

生成请求提交每项需求的决策及自由追加项。后端校验未知需求 ID、跨项目资产 ID、失效文件、图片数量上限和未处理状态；校验通过后创建快照并入队。

## 错误处理

- 路径越出项目资产目录、文件不存在或类型不支持：标记 `invalid`，不进入生成输入。
- 草稿变体创建冲突：重新读取已有记录并要求用户确认，不覆盖。
- 正式保存上传失败：保留临时引用，显示保存失败；用户仍可决定是否继续本次生成。
- 提交后图片失效：任务在调用模型前按快照复核，返回具体失效引用，不用零引用继续生成。
- 图片超过模型上限：保留用户顺序并提示超限，不自动静默裁剪。

## 验收标准

- `谢家碑坊_暴雨天井` 被解析为基础场景「谢家碑坊」与草稿变体「暴雨天井」。
- 基础场景名包含下划线时仍通过最长前缀正确解析。
- 找不到基础场景时不做模糊绑定。
- 无资产或无图片的道具仍显示在待处理区。
- 用户可以选择已有变体、确认草稿、退回基础场景或上传图片。
- 用户可以从项目资产库自由添加图片。
- 本地上传默认不写入项目资产库。
- 勾选保存后，图片进入用户选择的正确资产链路。
- 明确忽略的需求进入任务快照。
- 未处理缺失项触发二次确认。
- 生成端只接收快照中通过安全检查且实际存在的图片。
- 已匹配引用默认折叠，待处理问题优先显示。

## 测试范围

- Resolver 单元测试：结构化变体、最长前缀、未知基础场景、人物和道具需求。
- Matcher 单元测试：正式资产、回退、缺资产、缺图片、无效路径。
- DecisionService 单元测试：改绑、确认草稿、临时上传、正式保存、忽略、超限。
- API 契约测试：预览完整性、跨项目 ID 拒绝、未处理项、决策快照。
- 前端组件测试：问题优先排序、补救动作、自由追加、二次确认和提交摘要。
- 集成测试：导演方案到预览、预览到生成快照、任务执行前复核。
