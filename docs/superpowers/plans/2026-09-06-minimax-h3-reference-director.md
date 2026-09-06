# MiniMax H3 带 Ref 导演台实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 新增独立的 `runninghub:minimax-h3-ref` 视频模型，通过 RunningHub 工作流 `2096502793044582401` 同时提交叙事组级全局 Ref 与视频单元首尾帧；原 `runninghub:minimax-h3` 保持默认、配置和 payload 行为不变。

**架构：** 在视频工作流注册表中并列注册旧 H3 和 H3 Ref，并由注册项显式选择 RunningHub 工作流配置键。新增独立的叙事组视频 Ref 配置、候选解析、临时上传、组合 payload 编译器和适配器；任务提交冻结 Ref revision，runner 将同一全局 Ref 快照注入每个物理视频单元并写入结果 manifest。前端仅在用户选择 H3 Ref 时展示 Ref 管理器，默认上限 5 张、供应商设置可在 1–10 间调整。线上兼容性通过显式启用且需用户确认费用的真实 smoke 脚本验证，任何失败都不降级到旧导演台或无 Ref payload。

**技术栈：** Python 3.11、FastAPI、Pydantic v2、Pillow、pytest/pytest-asyncio、React 19、TypeScript、TanStack Query、Vitest、Testing Library、RunningHub timeline API。

---

## 文件结构与职责

- 修改 `src/novelvideo/media_capabilities/models.py`：增加 H3 Ref 工作流 ID 与全局 Ref 上限配置，保持既有 `VideoGenerationRequest` 能力不变量。
- 修改 `src/novelvideo/media_capabilities/runtime/configuration.py`：支持按注册项工作流设置键解析远端 ID，保留旧 capability 入口。
- 修改 `src/novelvideo/media_capabilities/video/workflow_registry.py`：并列注册旧 H3 和 H3 Ref，声明 Ref 策略、配置键和独立 adapter key。
- 修改 `src/novelvideo/media_capabilities/video/catalog.py`：向前端输出模型级 Ref 策略。
- 创建 `src/novelvideo/media_capabilities/video/profiles/minimax_h3_ref.json`：固定新工作流 timeline 输入节点 12、视频输出节点 7 和默认工作流 ID。
- 创建 `tests/fixtures/runninghub/minimax_h3_ref_api.json`：从用户提供的 RunningHub API JSON 归一化出的离线契约 fixture，不修改 `runninghub/` 下用户文件。
- 修改 `src/novelvideo/narrative_groups/models.py`：持久化有 revision 的叙事组视频 Ref 选择和 Subject 描述。
- 创建 `src/novelvideo/narrative_groups/video_references.py`：解析角色、场景、关键道具和临时上传候选，校验顺序、数量、文件和描述。
- 修改 `src/novelvideo/narrative_groups/service.py`：读写视频 Ref 配置并执行 revision/stage 并发保护。
- 修改 `src/novelvideo/api/routes/narrative_groups.py`：提供 Ref 预览、上传、保存接口，并在生成请求中接收 `reference_revision`。
- 创建 `src/novelvideo/media_capabilities/video/h3_reference_payload.py`：将全局 Ref、Subject/Picture 映射和原首尾帧 timeline 编译为新工作流 payload。
- 创建 `src/novelvideo/media_capabilities/video/h3_reference_runtime.py`：加载独立 profile、上传 Ref 与帧、计算幂等输入并调用现有 RunningHub pipeline。
- 修改 `src/novelvideo/media_capabilities/video/adapters.py`：增加 H3 Ref 请求快照与独立 adapter。
- 修改 `src/novelvideo/media_capabilities/video/h3_timeline.py`：在输出 manifest 中持久化 Ref 快照、revision、上限和哈希摘要。
- 修改 `src/novelvideo/task_backend/runners/narrative_group_video.py`：冻结并验证 Ref revision，把同一组 Ref 传给每个视频单元。
- 修改 `frontend/src/lib/queries/knowledge-runtime.ts`：增加 RunningHub H3 Ref 设置字段。
- 修改 `frontend/src/components/settings/knowledge-runtime-section.tsx`：增加新工作流 ID 和 1–10 Ref 上限输入。
- 修改 `frontend/src/lib/queries/media-models.ts`：接收模型 Ref 策略。
- 修改 `frontend/src/lib/queries/narrative-groups.ts`：增加 Ref DTO、预览/上传/保存 hooks 和生成 revision。
- 创建 `frontend/src/components/episode/narrative-workbench/group-video-reference-dialog.tsx`：候选选择、临时上传、排序和 Subject 描述编辑。
- 修改 `frontend/src/components/episode/narrative-workbench/group-video-stage.tsx`：只为 H3 Ref 展示入口和提交门禁。
- 修改 `frontend/src/components/episode/narrative-workbench/narrative-group-workbench.tsx`：协调模型切换、Ref 缓存、保存和生成 revision。
- 修改 `frontend/src/components/episode/narrative-workbench/group-video-prompt-drawer.tsx`：展示任务冻结的 Ref 映射。
- 修改 `frontend/public/locales/zh/translation.json`、`frontend/public/locales/en/translation.json`：新模型、设置、校验和 Ref 管理文案。
- 创建 `scripts/smoke_runninghub_h3_ref.py`：显式启用的真实 RunningHub 混合输入兼容性关卡。
- 修改中英文媒体生产与模型配置文档：说明新旧模型差异、上限、失败行为和 smoke 操作。

## 任务 1：注册第二个模型并增加 RunningHub 配置

**文件：**
- 修改：`src/novelvideo/media_capabilities/models.py`
- 修改：`src/novelvideo/media_capabilities/runtime/configuration.py`
- 修改：`src/novelvideo/media_capabilities/video/workflow_registry.py`
- 修改：`src/novelvideo/media_capabilities/video/catalog.py`
- 创建：`src/novelvideo/media_capabilities/video/profiles/minimax_h3_ref.json`
- 修改：`tests/media_capabilities/runtime/test_configuration.py`
- 修改：`tests/media_capabilities/video/test_workflow_registry.py`
- 修改：`tests/media_capabilities/video/test_catalog.py`

- [ ] **步骤 1：先写配置、注册表和 catalog 的失败测试**

覆盖以下契约：

```python
settings = RunningHubWorkflowSettings()
assert settings.video_minimax_h3 == "2089723723468328961"
assert settings.video_minimax_h3_ref == "2096502793044582401"
assert settings.video_minimax_h3_ref_max_images == 5

registry = build_video_workflow_registry(runtime)
assert [item.id for item in registry[:2]] == [
    "runninghub:minimax-h3",
    "runninghub:minimax-h3-ref",
]
assert registry[0].is_default is True
assert registry[1].adapter_key == "minimax-h3-ref"
assert registry[1].workflow_settings_key == "video_minimax_h3_ref"
assert registry[1].reference_policy.model_dump() == {
    "required": True,
    "min_images": 1,
    "max_images": 5,
    "source_kinds": [
        "character_identity",
        "scene_master",
        "prop_reference",
        "temporary_upload",
    ],
}
```

增加参数化测试确认 `video_minimax_h3_ref_max_images=0`、`11`、布尔值和小数均被拒绝，1 与 10 被接受。增加回归断言：旧模型仍排第一、仍使用 `minimax-h3` adapter、仍解析 `video_minimax_h3`。

- [ ] **步骤 2：运行测试并确认失败原因是字段和第二模型尚不存在**

运行：

```bash
uv run pytest tests/media_capabilities/runtime/test_configuration.py tests/media_capabilities/video/test_workflow_registry.py tests/media_capabilities/video/test_catalog.py -q
```

预期：新增字段访问、第二模型查找和 `reference_policy` 断言失败；既有旧模型测试继续通过。

- [ ] **步骤 3：实现严格配置与模型级工作流解析**

在 `RunningHubWorkflowSettings` 中增加：

```python
video_minimax_h3_ref: str = "2096502793044582401"
video_minimax_h3_ref_max_images: int = Field(default=5, ge=1, le=10, strict=True)
```

将 `video_minimax_h3_ref` 纳入数字工作流 ID validator。为 `VideoWorkflowDefinition` 增加以下明确字段，而不是通过 capability 猜模型：

```python
class VideoReferencePolicy(BaseModel):
    required: bool = False
    min_images: int = 0
    max_images: int = 0
    source_kinds: tuple[str, ...] = ()

class VideoWorkflowDefinition(BaseModel):
    # 保留现有字段
    workflow_settings_key: str
    is_default: bool = False
    reference_policy: VideoReferencePolicy = VideoReferencePolicy()
```

给运行时增加 `workflow_id_for_key(settings_key: str) -> str`，只允许注册表声明的工作流字段；现有 `workflow_id(capability)` 保持原行为，继续返回旧 H3 ID。

- [ ] **步骤 4：注册新模型并建立独立 profile**

旧模型注册项显式设为默认并排在第一。新模型使用：

```python
VideoWorkflowDefinition(
    id="runninghub:minimax-h3-ref",
    label="RunningHub MiniMax H3 · Ref",
    provider="runninghub",
    adapter_key="minimax-h3-ref",
    workflow_settings_key="video_minimax_h3_ref",
    scenes=("narrative_group",),
    supported_modes=("auto", "i2va", "fl2va"),
    default_mode="auto",
    reference_policy=VideoReferencePolicy(
        required=True,
        min_images=1,
        max_images=settings.video_minimax_h3_ref_max_images,
        source_kinds=(
            "character_identity",
            "scene_master",
            "prop_reference",
            "temporary_upload",
        ),
    ),
)
```

创建 `minimax_h3_ref.json`，固定 `timeline_data` 绑定节点 12、视频输出节点 7、默认 workflow `2096502793044582401`。catalog 原样序列化 `reference_policy`，不可根据候选 Ref 自动改变默认模型。

- [ ] **步骤 5：运行定向测试、静态检查并提交**

```bash
uv run pytest tests/media_capabilities/runtime/test_configuration.py tests/media_capabilities/video/test_workflow_registry.py tests/media_capabilities/video/test_catalog.py -q
uv run ruff check src/novelvideo/media_capabilities/models.py src/novelvideo/media_capabilities/runtime/configuration.py src/novelvideo/media_capabilities/video/workflow_registry.py src/novelvideo/media_capabilities/video/catalog.py tests/media_capabilities/runtime/test_configuration.py tests/media_capabilities/video/test_workflow_registry.py tests/media_capabilities/video/test_catalog.py
git add src/novelvideo/media_capabilities/models.py src/novelvideo/media_capabilities/runtime/configuration.py src/novelvideo/media_capabilities/video/workflow_registry.py src/novelvideo/media_capabilities/video/catalog.py src/novelvideo/media_capabilities/video/profiles/minimax_h3_ref.json tests/media_capabilities/runtime/test_configuration.py tests/media_capabilities/video/test_workflow_registry.py tests/media_capabilities/video/test_catalog.py
git commit -m "feat(video): register MiniMax H3 reference workflow"
```

预期：测试和 Ruff 全部通过；提交只包含本任务文件。

## 任务 2：建立叙事组级 Ref 持久模型与候选解析

**文件：**
- 修改：`src/novelvideo/narrative_groups/models.py`
- 创建：`src/novelvideo/narrative_groups/video_references.py`
- 修改：`src/novelvideo/narrative_groups/service.py`
- 创建：`tests/test_narrative_group_video_references.py`

- [ ] **步骤 1：先写旧 sidecar 兼容、候选顺序和 revision 的失败测试**

覆盖：旧 JSON 没有 `video_reference_settings` 时加载为空 revision 0；角色身份图、场景主图、关键道具图按“角色、场景、道具”稳定排序并去重；缺失文件进入 warning 而不是可选项；保存时只接受服务器候选 ID；重复 ID、空描述、0 张、超上限和 stale revision 被拒绝；生成 stage 活跃时禁止改写。

核心断言：

```python
group = service.get_group(episode_number=1, group_id="group-1")
assert group.video_reference_settings.revision == 0
assert group.video_reference_settings.references == ()

preview = resolve_group_video_reference_preview(
    store=store,
    project_dir=project_dir,
    episode_number=1,
    group=group,
    max_images=5,
)
assert [item.source_kind for item in preview.candidates] == [
    "character_identity",
    "scene_master",
    "prop_reference",
]
```

- [ ] **步骤 2：运行新测试并确认模块尚不存在**

```bash
uv run pytest tests/test_narrative_group_video_references.py -q
```

预期：导入 `video_references` 或新模型字段失败。

- [ ] **步骤 3：实现持久类型和向后兼容加载**

在 `models.py` 增加：

```python
VideoReferenceSourceKind = Literal[
    "character_identity",
    "scene_master",
    "prop_reference",
    "temporary_upload",
]

@dataclass(frozen=True)
class VideoReferenceItem:
    reference_id: str
    source_kind: VideoReferenceSourceKind
    label: str
    subject_description: str
    asset_id: str = ""
    temporary_upload_id: str = ""

@dataclass(frozen=True)
class VideoReferenceSettings:
    revision: int = 0
    references: tuple[VideoReferenceItem, ...] = ()
```

将其加入 `NarrativeGroup.to_dict()` 和 `_group_from_dict()`。缺失字段必须得到空设置，不能迁移旧图像生成 `references.py` 的数据，也不能改变旧模型选择。

- [ ] **步骤 4：实现候选解析、默认描述和原子保存**

新模块复用 `real_detected_identities`、`real_detected_props`、`beat_scene_id` 与 `path_resolver` 中的规范资产路径函数。候选 ID 使用来源类型和稳定资产 ID 生成不透明哈希；默认描述由资产名称和现有视觉描述组成，不写入 Picture 编号，编号只在 payload 编译阶段产生。

实现：

```python
def resolve_group_video_reference_preview(..., max_images: int) -> VideoReferencePreview: ...
def resolve_saved_video_references(..., max_images: int) -> tuple[ResolvedVideoReference, ...]: ...
def save_group_video_reference_settings(
    ...,
    expected_revision: int,
    selections: Sequence[VideoReferenceSelection],
    max_images: int,
) -> VideoReferenceSettings: ...
```

保存逻辑使用现有 sidecar lock，复用 `update_video_settings` 的 stage 活跃检查；成功后 revision 加 1。`resolve_saved_video_references` 再次验证路径位于项目目录内、文件存在、可被 Pillow 解码、ID 唯一、描述非空、数量 1..max。

- [ ] **步骤 5：运行测试、静态检查并提交**

```bash
uv run pytest tests/test_narrative_group_video_references.py tests/test_narrative_group_video_settings.py -q
uv run ruff check src/novelvideo/narrative_groups/models.py src/novelvideo/narrative_groups/video_references.py src/novelvideo/narrative_groups/service.py tests/test_narrative_group_video_references.py
git add src/novelvideo/narrative_groups/models.py src/novelvideo/narrative_groups/video_references.py src/novelvideo/narrative_groups/service.py tests/test_narrative_group_video_references.py
git commit -m "feat(video): persist narrative group reference settings"
```

预期：新旧 sidecar、并发保护和候选解析测试全部通过。

## 任务 3：提供 Ref 预览、临时上传、保存和提交 API

**文件：**
- 修改：`src/novelvideo/api/routes/narrative_groups.py`
- 修改：`tests/test_api_narrative_groups.py`

- [ ] **步骤 1：先写三个 endpoint 和生成 revision 的失败测试**

使用以下不与既有 stage reference 路由冲突的路径：

```text
GET  /api/projects/{project_id}/episodes/{episode}/narrative-groups/{group_id}/video/reference-preview
POST /api/projects/{project_id}/episodes/{episode}/narrative-groups/{group_id}/video/reference-uploads
PUT  /api/projects/{project_id}/episodes/{episode}/narrative-groups/{group_id}/video/references
```

测试以下状态：预览返回 `revision/max_images/candidates/selected/warnings`；上传只接受图片且返回服务器生成的 `reference_id`；保存要求 `expected_revision` 和有序 `references`；stale revision 返回 409；0 张或超限返回 422；客户端路径字段被忽略或拒绝；新模型生成缺少/过期 `reference_revision` 返回 409；旧模型不要求该字段。

- [ ] **步骤 2：运行定向 API 测试并确认 404/协议失败**

```bash
uv run pytest tests/test_api_narrative_groups.py -q -k "video_reference or h3_ref or reference_revision"
```

预期：新路由 404，新生成字段断言失败。

- [ ] **步骤 3：实现安全临时上传**

上传使用 `UploadFile`，复用 `novelvideo.utils.upload_safety.MAX_UPLOAD_BYTES`，读入后由 Pillow 解码并规范化为 PNG。服务器生成 `upload_id`，保存到：

```text
{project_dir}/videos/ep{episode:03d}/narrative_groups/references/{group_id}/{upload_id}.png
```

文件名、路径和 `reference_id` 不接受客户端控制；响应包含缩略图 URL、label、默认 Subject 描述和 `temporary_upload` 来源类型。无效 MIME、超限、解码失败或非图像返回 422。

- [ ] **步骤 4：实现预览、保存和 enqueue 门禁**

增加请求模型：

```python
class VideoReferenceSelectionRequest(BaseModel):
    reference_id: str
    subject_description: str = Field(min_length=1, max_length=500)

class UpdateVideoReferencesRequest(BaseModel):
    expected_revision: int = Field(ge=0)
    references: list[VideoReferenceSelectionRequest]

class NarrativeGroupVideoRequest(BaseModel):
    # 保留现有字段
    reference_revision: int | None = Field(default=None, ge=0)
```

`_enqueue_group_video` 仅在 registry 的 `reference_policy.required` 为真时验证已保存 Ref、当前上限、必要首帧和精确 revision，再预留 stage；旧模型忽略 Ref 配置且保持既有请求兼容。把 `reference_revision` 写入队列 payload，不在 API 层读取客户端文件路径。

- [ ] **步骤 5：运行 API 回归、静态检查并提交**

```bash
uv run pytest tests/test_api_narrative_groups.py tests/test_narrative_group_video_references.py -q
uv run ruff check src/novelvideo/api/routes/narrative_groups.py tests/test_api_narrative_groups.py
git add src/novelvideo/api/routes/narrative_groups.py tests/test_api_narrative_groups.py
git commit -m "feat(api): add narrative group video reference endpoints"
```

预期：新接口契约、旧生成接口回归和错误码断言全部通过。

## 任务 4：编译 Ref 与首尾帧并存的独立 H3 payload

**文件：**
- 创建：`tests/fixtures/runninghub/minimax_h3_ref_api.json`
- 创建：`src/novelvideo/media_capabilities/video/h3_reference_payload.py`
- 创建：`tests/media_capabilities/video/test_h3_reference_payload.py`
- 修改：`tests/media_capabilities/video/test_h3_runtime.py`

- [ ] **步骤 1：归一化用户提供的 API JSON 为只读 fixture**

从 `runninghub/MiniMax H3 导演台全能工作流-参考_api.json` 提取与 API 契约相关的节点、字段名、task type、timeline mode、输入节点 12 和输出节点 7，删除凭据、任务实例值和无关 UI 元数据，保存为 `tests/fixtures/runninghub/minimax_h3_ref_api.json`。不得修改或提交 `runninghub/` 下的用户原文件。

- [ ] **步骤 2：先写 payload 编译失败测试**

测试一个 Ref-I2V 单元和一个 Ref-FL2V 单元，断言：

```python
assert payload["global"]["commonEnabled"] is True
assert payload["global"]["commonCollapsed"] is True
assert payload["global"]["refs"] == [
    {"index": 0, "url": "rh://ref-character"},
    {"index": 1, "url": "rh://ref-scene"},
]
assert payload["global"]["prompt"] == (
    "subject_definitions:\n"
    "<Subject 1> is the heroine from <Picture 1>\n"
    "<Subject 2> is the apartment interior from <Picture 2>"
)
assert payload["segments"][0]["startImage"] == "rh://first-frame"
assert payload["segments"][1]["endImage"] == "rh://last-frame"
```

再断言 Ref 为零、重复 ID、空描述、超过上限、缺首帧、FL2V 缺尾帧、Subject/Picture 编号漂移均在编译前失败。增加旧 `_director_timeline_payload` fixture 快照断言，证明原 payload 未增加 `global.refs`。

- [ ] **步骤 3：运行定向测试并确认编译器尚不存在**

```bash
uv run pytest tests/media_capabilities/video/test_h3_reference_payload.py tests/media_capabilities/video/test_h3_runtime.py -q
```

预期：新模块导入失败；旧 runtime 测试仍通过。

- [ ] **步骤 4：实现专用输入类型和纯函数编译器**

不要放宽 `VideoGenerationRequest` 对 REF2VA 与首尾帧互斥的校验。新增适配器专用类型：

```python
@dataclass(frozen=True)
class H3GlobalReference:
    reference_id: str
    source_kind: str
    label: str
    subject_description: str
    uploaded_url: str
    sha256: str

def build_h3_reference_timeline_payload(
    *,
    timeline: H3Timeline,
    references: Sequence[H3GlobalReference],
    max_references: int,
) -> dict[str, object]: ...
```

编译器复用原 segment 构造规则，保持 `startImage`、`endImage`、`genImage`、keyframe 和单元 Prompt；只在新 payload 增加有序 `global.refs` 和 `subject_definitions`。Picture/Subject 编号由最终顺序零基数组映射为一基文案，用户描述不能覆盖镜头 Prompt 的 `(from Shot 1)` 帧语义。

- [ ] **步骤 5：运行 payload 与旧 runtime 回归并提交**

```bash
uv run pytest tests/media_capabilities/video/test_h3_reference_payload.py tests/media_capabilities/video/test_h3_runtime.py -q
uv run ruff check src/novelvideo/media_capabilities/video/h3_reference_payload.py tests/media_capabilities/video/test_h3_reference_payload.py tests/media_capabilities/video/test_h3_runtime.py
git add tests/fixtures/runninghub/minimax_h3_ref_api.json src/novelvideo/media_capabilities/video/h3_reference_payload.py tests/media_capabilities/video/test_h3_reference_payload.py tests/media_capabilities/video/test_h3_runtime.py
git commit -m "feat(video): compile H3 reference timeline payload"
```

预期：新混合 payload fixture 通过，旧 H3 payload 快照无变化。

## 任务 5：接入 H3 Ref runtime、adapter、runner 与冻结 manifest

**文件：**
- 创建：`src/novelvideo/media_capabilities/video/h3_reference_runtime.py`
- 修改：`src/novelvideo/media_capabilities/video/adapters.py`
- 修改：`src/novelvideo/media_capabilities/video/h3_timeline.py`
- 修改：`src/novelvideo/task_backend/runners/narrative_group_video.py`
- 创建：`tests/media_capabilities/video/test_h3_reference_runtime.py`
- 修改：`tests/test_task_narrative_group_video_runner.py`
- 修改：`tests/test_task_narrative_group_video_compose_runner.py`

- [ ] **步骤 1：先写上传、幂等、逐单元注入和 manifest 的失败测试**

用 fake uploader/pipeline 覆盖：先解析并校验所有本地 Ref 与帧，再上传；任一上传失败时 `create_task` 未调用；同一组全局 Ref 被传给叙事组内每个 adapter 调用；Ref 内容、顺序或描述变化会改变幂等键；相同冻结输入重试保持幂等键；stale `reference_revision` 在 provider 调用前失败；manifest 包含实际 workflow ID、revision、上限、每张 Ref 的来源/描述/SHA-256 和最终编号。

- [ ] **步骤 2：运行 runtime/runner 测试并确认新 adapter 尚未注册**

```bash
uv run pytest tests/media_capabilities/video/test_h3_reference_runtime.py tests/test_task_narrative_group_video_runner.py tests/test_task_narrative_group_video_compose_runner.py -q -k "reference or h3_ref"
```

预期：新 runtime 导入失败或 adapter key 未找到。

- [ ] **步骤 3：实现独立 runtime，不改变旧生成函数**

`generate_h3_reference_director_video` 接收已经冻结的本地 Ref 快照、首尾帧和显式 workflow ID。执行顺序固定为：

1. 校验所有 Ref 和必要帧并计算 SHA-256；
2. 上传全部 Ref 和帧；
3. 使用 `h3_reference_payload` 编译 timeline；
4. 将 Ref 哈希、顺序、描述、帧哈希、workflow ID、参数和编译器版本纳入 idempotency material；
5. 调用现有 `H3VideoPipeline.generate_timeline`；
6. 复用下载、视频探测、质检、取消和 provider task ID 保存逻辑。

远端拒绝混合输入时直接返回规范化错误；不得重试为无 Ref、无尾帧或旧 workflow。

- [ ] **步骤 4：实现 adapter、runner 快照和 manifest**

扩展 `NarrativeGroupVideoRequest` 领域对象：

```python
reference_revision: int | None = None
global_references: tuple[ResolvedVideoReference, ...] = ()
reference_limit: int | None = None
```

新增 `H3ReferenceWorkflowAdapter`，只调用新 runtime。runner 在开始实际生成前验证队列中的 revision 与当前保存值一致，解析一次全局 Ref 快照，然后把同一不可变 tuple 传给每个视频单元。

给 `H3DirectorOutputManifest` 增加：

```python
reference_settings_revision: int | None = None
reference_limit: int | None = None
global_references: tuple[dict[str, object], ...] = ()
```

旧模型写空 tuple 和 `None`，保持旧 manifest 可读取。H3 Ref manifest 写最终 Picture/Subject 编号、稳定来源 ID、描述和 SHA-256；历史 UI 必须读取此快照，不重新解析当前资产。

- [ ] **步骤 5：运行 runner 全量回归、静态检查并提交**

```bash
uv run pytest tests/media_capabilities/video/test_h3_reference_runtime.py tests/media_capabilities/video/test_h3_runtime.py tests/test_task_narrative_group_video_runner.py tests/test_task_narrative_group_video_compose_runner.py -q
uv run ruff check src/novelvideo/media_capabilities/video/h3_reference_runtime.py src/novelvideo/media_capabilities/video/adapters.py src/novelvideo/media_capabilities/video/h3_timeline.py src/novelvideo/task_backend/runners/narrative_group_video.py tests/media_capabilities/video/test_h3_reference_runtime.py tests/test_task_narrative_group_video_runner.py tests/test_task_narrative_group_video_compose_runner.py
git add src/novelvideo/media_capabilities/video/h3_reference_runtime.py src/novelvideo/media_capabilities/video/adapters.py src/novelvideo/media_capabilities/video/h3_timeline.py src/novelvideo/task_backend/runners/narrative_group_video.py tests/media_capabilities/video/test_h3_reference_runtime.py tests/test_task_narrative_group_video_runner.py tests/test_task_narrative_group_video_compose_runner.py
git commit -m "feat(video): run H3 reference jobs with frozen inputs"
```

预期：新旧 adapter、逐单元合成、恢复和 manifest 回归全部通过。

## 任务 6：在前端接入模型策略、供应商设置和 Ref API

**文件：**
- 修改：`frontend/src/lib/queries/knowledge-runtime.ts`
- 修改：`frontend/src/components/settings/knowledge-runtime-section.tsx`
- 修改：`frontend/src/lib/queries/media-models.ts`
- 修改：`frontend/src/lib/queries/narrative-groups.ts`
- 修改：`frontend/src/__tests__/lib/queries/knowledge-runtime.test.tsx`
- 修改：`frontend/src/__tests__/components/settings/knowledge-runtime-section.test.tsx`
- 修改：`frontend/src/__tests__/lib/queries/media-models.test.ts`
- 修改：`frontend/src/__tests__/lib/queries/narrative-groups.test.ts`

- [ ] **步骤 1：先写设置与 query 契约的失败测试**

断言默认表单显示工作流 `2096502793044582401` 和上限 5；上限输入约束为 1–10；保存 payload 同时保留旧 `video_minimax_h3`；catalog 能解析 `reference_policy`；Ref hooks 使用任务 3 的精确路径；生成新模型时发送 `reference_revision`，旧调用不发送该字段。

- [ ] **步骤 2：运行前端定向测试并确认类型/元素缺失**

```bash
pnpm --dir frontend test -- src/__tests__/lib/queries/knowledge-runtime.test.tsx src/__tests__/components/settings/knowledge-runtime-section.test.tsx src/__tests__/lib/queries/media-models.test.ts src/__tests__/lib/queries/narrative-groups.test.ts
```

预期：新字段类型、表单标签或 query hook 不存在。

- [ ] **步骤 3：扩展 TypeScript 契约和 hooks**

增加：

```ts
export type VideoReferencePolicy = {
  required: boolean
  min_images: number
  max_images: number
  source_kinds: Array<
    | 'character_identity'
    | 'scene_master'
    | 'prop_reference'
    | 'temporary_upload'
  >
}

export type VideoReferencePreview = {
  revision: number
  max_images: number
  candidates: VideoReferenceCandidate[]
  selected: VideoReferenceSelection[]
  warnings: string[]
}
```

实现 `useNarrativeGroupVideoReferencePreview`、`useUploadNarrativeGroupVideoReference` 和 `useUpdateNarrativeGroupVideoReferences`。保存成功后只失效当前项目/剧集/叙事组的 reference preview 与 narrative-group cache。

- [ ] **步骤 4：增加 RunningHub 设置 UI**

设置页增加“MiniMax H3 带 Ref 导演台 Workflow ID”和“带 Ref 导演台全局 Ref 上限”。数字输入使用 `min={1}`、`max={10}`、`step={1}`，提交前将字符串解析为整数并拒绝空值、小数和越界值。旧工作流输入保持原标签、值和保存字段。

- [ ] **步骤 5：运行测试、TypeScript 构建并提交**

```bash
pnpm --dir frontend test -- src/__tests__/lib/queries/knowledge-runtime.test.tsx src/__tests__/components/settings/knowledge-runtime-section.test.tsx src/__tests__/lib/queries/media-models.test.ts src/__tests__/lib/queries/narrative-groups.test.ts
pnpm --dir frontend build
git add frontend/src/lib/queries/knowledge-runtime.ts frontend/src/components/settings/knowledge-runtime-section.tsx frontend/src/lib/queries/media-models.ts frontend/src/lib/queries/narrative-groups.ts frontend/src/__tests__/lib/queries/knowledge-runtime.test.tsx frontend/src/__tests__/components/settings/knowledge-runtime-section.test.tsx frontend/src/__tests__/lib/queries/media-models.test.ts frontend/src/__tests__/lib/queries/narrative-groups.test.ts
git commit -m "feat(frontend): add H3 reference model contracts"
```

预期：定向 Vitest 和前端生产构建通过。

## 任务 7：实现 Ref 管理器与生成门禁

**文件：**
- 创建：`frontend/src/components/episode/narrative-workbench/group-video-reference-dialog.tsx`
- 修改：`frontend/src/components/episode/narrative-workbench/group-video-stage.tsx`
- 修改：`frontend/src/components/episode/narrative-workbench/narrative-group-workbench.tsx`
- 修改：`frontend/src/components/episode/narrative-workbench/group-video-prompt-drawer.tsx`
- 修改：`frontend/public/locales/zh/translation.json`
- 修改：`frontend/public/locales/en/translation.json`
- 创建：`frontend/src/__tests__/components/episode/narrative-workbench/group-video-reference-dialog.test.tsx`
- 修改：`frontend/src/__tests__/components/episode/narrative-workbench/group-video-stage.test.tsx`
- 修改：`frontend/src/__tests__/components/episode/narrative-workbench/narrative-group-workbench-references.test.tsx`
- 修改：`frontend/src/__tests__/components/episode/narrative-workbench/group-video-prompt-drawer.test.tsx`
- 修改：`frontend/src/__tests__/components/episode/narrative-workbench/group-video-submit.test.ts`

- [ ] **步骤 1：先写对话框、模型切换和提交门禁的失败测试**

覆盖：旧模型不显示 Ref 区；选择 H3 Ref 后显示“管理 Ref”与 `已选 n/5`；首次打开按后端候选顺序预选最多上限内候选；用户可取消、临时上传、编辑描述、拖动排序，并可通过键盘“上移/下移”完成同等排序；0 张、超过上限、空描述和未保存修改禁用生成；保存成功后发送新 revision；切回旧模型不删除已保存 Ref；再次切回新模型恢复配置；结果抽屉只显示任务 manifest 快照。

- [ ] **步骤 2：运行组件测试并确认 UI 尚不存在**

```bash
pnpm --dir frontend test -- src/__tests__/components/episode/narrative-workbench/group-video-reference-dialog.test.tsx src/__tests__/components/episode/narrative-workbench/group-video-stage.test.tsx src/__tests__/components/episode/narrative-workbench/narrative-group-workbench-references.test.tsx src/__tests__/components/episode/narrative-workbench/group-video-prompt-drawer.test.tsx src/__tests__/components/episode/narrative-workbench/group-video-submit.test.ts
```

预期：新组件导入或 Ref 控件查询失败。

- [ ] **步骤 3：实现可访问的 Ref 管理器**

对话框分为候选列表、已选列表和临时上传。已选项显示缩略图、来源、label、`<Picture n>`、`<Subject n>` 与可编辑描述。HTML5 drag 负责指针排序，同时提供带 `aria-label` 的上移/下移按钮；任何排序都重新连续编号，但不修改稳定 `reference_id`。

本地状态只保存 `reference_id + subject_description`；路径、哈希和上传 URL 由后端管理。保存按钮在数量不处于 `min_images..max_images`、描述 trim 后为空或存在重复 ID 时禁用，并显示明确中文/英文错误。

- [ ] **步骤 4：接入 workbench、生成请求和历史展示**

`narrative-group-workbench.tsx` 仅在当前模型 `reference_policy.required` 时启用 preview query。未保存修改与当前 revision 分开存储；保存成功后更新 revision。提交参数：

```ts
const referenceRevision = selectedModel.reference_policy?.required
  ? savedReferencePreview.revision
  : undefined
```

`group-video-stage.tsx` 除 Ref 配置有效外仍执行原首帧/尾帧、计划 revision 和任务状态门禁。`group-video-prompt-drawer.tsx` 从 manifest 展示实际 workflow、Ref 数量、来源、Picture/Subject 映射和哈希短摘要；不得读取当前 preview 重建历史。

- [ ] **步骤 5：运行组件测试、前端全量测试与构建并提交**

```bash
pnpm --dir frontend test -- src/__tests__/components/episode/narrative-workbench/group-video-reference-dialog.test.tsx src/__tests__/components/episode/narrative-workbench/group-video-stage.test.tsx src/__tests__/components/episode/narrative-workbench/narrative-group-workbench-references.test.tsx src/__tests__/components/episode/narrative-workbench/group-video-prompt-drawer.test.tsx src/__tests__/components/episode/narrative-workbench/group-video-submit.test.ts
pnpm --dir frontend test
pnpm --dir frontend build
git add frontend/src/components/episode/narrative-workbench/group-video-reference-dialog.tsx frontend/src/components/episode/narrative-workbench/group-video-stage.tsx frontend/src/components/episode/narrative-workbench/narrative-group-workbench.tsx frontend/src/components/episode/narrative-workbench/group-video-prompt-drawer.tsx frontend/public/locales/zh/translation.json frontend/public/locales/en/translation.json frontend/src/__tests__/components/episode/narrative-workbench/group-video-reference-dialog.test.tsx frontend/src/__tests__/components/episode/narrative-workbench/group-video-stage.test.tsx frontend/src/__tests__/components/episode/narrative-workbench/narrative-group-workbench-references.test.tsx frontend/src/__tests__/components/episode/narrative-workbench/group-video-prompt-drawer.test.tsx frontend/src/__tests__/components/episode/narrative-workbench/group-video-submit.test.ts
git commit -m "feat(frontend): add narrative video reference manager"
```

预期：组件测试、前端全量测试和生产构建全部通过。

## 任务 8：增加付费隔离的真实兼容性关卡与文档

**文件：**
- 创建：`scripts/smoke_runninghub_h3_ref.py`
- 创建：`tests/scripts/test_smoke_runninghub_h3_ref.py`
- 修改：`docs/zh/guides/media-production.md`
- 修改：`docs/en/guides/media-production.md`
- 修改：`docs/zh/getting-started/configuring-models.md`
- 修改：`docs/en/getting-started/configuring-models.md`

- [ ] **步骤 1：先写 smoke 脚本的离线失败测试**

测试脚本默认不联网；缺少 `RUNNINGHUB_REAL_SMOKE=1` 时退出码 2 并说明未授权真实调用；缺少 Ref 或首帧路径时退出码 2；传入 fake client 时断言工作流 ID、至少 1 张 Ref、首帧和可选尾帧同时存在；远端拒绝时只报告失败，不构造无 Ref 或旧 workflow 的第二次请求。

- [ ] **步骤 2：运行脚本测试并确认文件尚不存在**

```bash
uv run pytest tests/scripts/test_smoke_runninghub_h3_ref.py -q
```

预期：脚本模块导入失败。

- [ ] **步骤 3：实现显式启用的 smoke 命令**

命令接口固定为：

```bash
RUNNINGHUB_REAL_SMOKE=1 uv run python scripts/smoke_runninghub_h3_ref.py \
  --reference /absolute/path/to/reference.png \
  --first-frame /absolute/path/to/first.png \
  --last-frame /absolute/path/to/last.png
```

脚本复用应用的凭据与 `generate_h3_reference_director_video`，输出 workflow ID、provider task ID、输入哈希摘要、最终状态和本地结果路径。真实命令会产生 RunningHub 费用，因此执行计划时必须先向用户展示将使用的输入和费用风险并取得明确确认；自动测试、CI 和普通本地验证不得设置该环境变量。

- [ ] **步骤 4：更新中英文文档**

文档必须明确：

- 原 H3 仍是默认且不使用 Ref；
- H3 Ref 使用独立 workflow `2096502793044582401`；
- 自动候选来源与临时上传含义；
- 默认 5 张、设置范围 1–10，且不计首尾帧；
- H3 Ref 要求至少 1 张 Ref 和首帧，FL2V 还要求尾帧；
- 失败不会切回旧模型、删除 Ref、删除尾帧或截断列表；
- 真实 smoke 是发布前兼容性证据，不属于自动测试。

- [ ] **步骤 5：运行离线测试、文档检查并提交**

```bash
uv run pytest tests/scripts/test_smoke_runninghub_h3_ref.py -q
uv run ruff check scripts/smoke_runninghub_h3_ref.py tests/scripts/test_smoke_runninghub_h3_ref.py
git diff --check
git add scripts/smoke_runninghub_h3_ref.py tests/scripts/test_smoke_runninghub_h3_ref.py docs/zh/guides/media-production.md docs/en/guides/media-production.md docs/zh/getting-started/configuring-models.md docs/en/getting-started/configuring-models.md
git commit -m "docs(video): add H3 reference compatibility gate"
```

预期：离线测试和静态检查通过；未产生真实 RunningHub 请求。

## 任务 9：执行总回归并取得发布证据

**文件：**
- 验证：本计划涉及的全部后端、前端、fixture、文档和脚本文件

- [ ] **步骤 1：运行后端 H3 Ref 与旧 H3 定向回归**

```bash
uv run pytest tests/media_capabilities/runtime/test_configuration.py tests/media_capabilities/video/test_workflow_registry.py tests/media_capabilities/video/test_catalog.py tests/media_capabilities/video/test_h3_reference_payload.py tests/media_capabilities/video/test_h3_reference_runtime.py tests/media_capabilities/video/test_h3_runtime.py tests/test_narrative_group_video_references.py tests/test_api_narrative_groups.py tests/test_task_narrative_group_video_runner.py tests/test_task_narrative_group_video_compose_runner.py tests/scripts/test_smoke_runninghub_h3_ref.py -q
```

预期：全部通过；测试日志不包含真实 provider task ID。

- [ ] **步骤 2：运行后端全量测试与静态检查**

```bash
uv run pytest -q
uv run ruff check src tests scripts/smoke_runninghub_h3_ref.py
```

预期：全部通过。若出现与当前脏工作区中其他用户改动有关的失败，记录精确测试名和堆栈，不回滚或覆盖那些文件。

- [ ] **步骤 3：运行前端全量测试与生产构建**

```bash
pnpm --dir frontend test
pnpm --dir frontend build
```

预期：Vitest、TypeScript、Vite 构建和 bundle budget 检查全部通过。

- [ ] **步骤 4：执行代码差异与旧模型保护检查**

```bash
git diff --check
git diff -- src/novelvideo/media_capabilities/video/runtime.py src/novelvideo/media_capabilities/video/profiles/minimax_h3.json
```

预期：无空白错误；旧 runtime/profile 没有 payload 行为改动。若为复用无状态 helper 必须修改旧 runtime，需有任务 4/5 的旧 payload 快照证明序列化结果完全一致。

- [ ] **步骤 5：经用户明确确认后执行一次真实混合输入 smoke**

先向用户展示 workflow `2096502793044582401`、Ref/首帧/尾帧文件、分辨率、预计调用一次付费任务和失败不重试策略。获得明确确认后运行任务 8 的命令。

成功证据必须包括：provider 接受同一请求中的全局 Ref 与首帧/尾帧、输出来自节点 7、下载视频可探测且质检通过。若失败，则保留 provider task ID 与规范化错误，将功能标为“兼容性关卡未通过”，不声明可发布，也不改变 payload 以静默降级。

- [ ] **步骤 6：提交验证期间产生的必要小修并检查提交边界**

```bash
git status --short
git log --oneline -10
```

只提交本功能的必要修复；不暂存 `runninghub/` 原始文件或其他用户改动。最终交付列出定向测试、全量测试、前端构建、真实 smoke 状态及对应命令结果。

## 完成标准

- 原 `runninghub:minimax-h3` 仍是默认模型，旧 workflow、请求兼容和 payload 快照不变。
- `runninghub:minimax-h3-ref` 独立展示并使用 `video_minimax_h3_ref`，默认 ID 为 `2096502793044582401`。
- 全局 Ref 默认上限为 5、设置范围为 1–10，仅统计角色、场景、道具和临时 Ref，不统计首尾帧。
- H3 Ref 的每次物理视频调用同时携带冻结的全局 Ref 与该单元必要首尾帧。
- 0 张、超限、空描述、文件失效、缺帧和 revision 冲突均在 provider 调用前失败。
- 任务 manifest 足以独立解释历史 Picture/Subject 映射、来源、顺序、描述和哈希。
- 远端失败不触发旧模型、无 Ref、无尾帧或截断列表降级。
- 自动测试不产生 RunningHub 费用；真实 smoke 仅在用户明确确认后执行并作为发布关卡。
