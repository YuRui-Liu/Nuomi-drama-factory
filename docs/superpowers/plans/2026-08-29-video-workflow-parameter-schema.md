# 视频工作流参数 Schema 实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 为叙事组视频建立可扩展的工作流参数 Schema，并让 MiniMax H3 Director 的 720p/1080p 选择准确映射为内置 `megapixels` 档位，支持项目默认、本组覆盖、稳定生成快照和前端手动调整。

**架构：** 工作流目录只公开供应商无关的产品参数；项目媒体默认和叙事组 sidecar 分层保存参数；生成入队前由后端合并、校验并冻结快照；H3 适配器独占把产品档位转换为 Director 内部尺寸字段；manifest、stage 和提示词审查接口保存同一份历史事实。

**技术栈：** Python 3.11、FastAPI、Pydantic v2、dataclasses、pytest；React 19、TypeScript、TanStack Query、Vitest、Testing Library。

---

## 文件结构与职责

### 后端新增文件

- `src/novelvideo/media_capabilities/video/parameters.py`：通用参数 Schema、选项模型、默认值合并和产品值校验。
- `src/novelvideo/media_capabilities/video/h3_size_settings.py`：MiniMax H3 Director 内置 `megapixels` 档位及画幅到确定尺寸的唯一映射。
- `tests/media_capabilities/video/test_parameters.py`：通用 Schema 的未知键、非法值、默认合并和隔离测试。
- `tests/media_capabilities/video/test_h3_size_settings.py`：0.9MP/2.0MP、9:16/16:9 和 32 倍数映射测试。
- `frontend/src/components/episode/narrative-workbench/group-video-parameters.tsx`：叙事组视频清晰度控件及继承/覆盖操作。
- `frontend/src/__tests__/components/episode/narrative-workbench/group-video-parameters.test.tsx`：控件交互和禁用状态测试。

### 后端修改文件

- `src/novelvideo/media_capabilities/video/workflow_registry.py`：工作流声明参数 Schema。
- `src/novelvideo/media_capabilities/video/catalog.py`、`src/novelvideo/api/routes/media_capabilities.py`：向前端发布产品参数定义。
- `src/novelvideo/api/schemas.py`、`src/novelvideo/api/routes/projects.py`：保存按工作流分区的项目默认参数。
- `src/novelvideo/narrative_groups/models.py`、`src/novelvideo/narrative_groups/service.py`：保存叙事组设置、revision 和生成事实。
- `src/novelvideo/api/routes/narrative_groups.py`：设置 CAS API、生成请求 revision 和提示词审查序列化。
- `src/novelvideo/media_capabilities/video/runtime.py`：使用 H3 内置档位生成精确 Director output。
- `src/novelvideo/media_capabilities/video/adapters.py`：用通用参数快照替代裸 `resolution` 传递，并返回供应商参数与实际输出。
- `src/novelvideo/media_capabilities/video/h3_timeline.py`：manifest 记录产品参数、供应商参数与实际尺寸。
- `src/novelvideo/task_backend/runners/narrative_group_video.py`：校验设置 revision、传递不可变快照并处理实际尺寸不一致。

### 前端修改文件

- `frontend/src/lib/queries/media-models.ts`：参数 Schema 和项目默认值类型/请求。
- `frontend/src/lib/queries/narrative-groups.ts`：叙事组设置类型、CAS mutation 和带 revision 的生成请求。
- `frontend/src/components/episode/narrative-workbench/narrative-group-workbench.tsx`：合并默认/覆盖、保存设置、触发生成。
- `frontend/src/components/episode/narrative-workbench/group-video-stage.tsx`：把参数控件放入视频生成卡片。
- `frontend/src/components/episode/narrative-workbench/group-video-result.tsx`：保留旧视频并显示当前目标与历史实际设置差异。
- `frontend/src/components/episode/narrative-workbench/group-video-prompt-drawer.tsx`：展示可复盘的产品、供应商和实际输出参数。

## 任务 1：锁定 H3 Director 内置尺寸档位

**文件：**

- 新建：`src/novelvideo/media_capabilities/video/h3_size_settings.py`
- 新建：`tests/media_capabilities/video/test_h3_size_settings.py`
- 修改：`src/novelvideo/media_capabilities/video/runtime.py`
- 修改：`tests/media_capabilities/video/test_h3_runtime.py`

- [ ] **步骤 1：先写 H3 档位映射失败测试**

```python
import pytest

from novelvideo.media_capabilities.video.h3_size_settings import (
    H3SizeSettingError,
    resolve_h3_size_setting,
)


@pytest.mark.parametrize(
    ("resolution", "aspect_ratio", "megapixels", "width", "height"),
    [
        ("720p", "9:16", 0.9, 736, 1280),
        ("720p", "16:9", 0.9, 1280, 736),
        ("1080p", "9:16", 2.0, 1088, 1920),
        ("1080p", "16:9", 2.0, 1920, 1088),
    ],
)
def test_resolve_h3_size_setting_uses_director_presets(
    resolution, aspect_ratio, megapixels, width, height
):
    value = resolve_h3_size_setting(resolution, aspect_ratio)
    assert value.megapixels == megapixels
    assert (value.width, value.height) == (width, height)
    assert value.multiple == 32
    assert value.long_edge == max(width, height)
    assert value.ref_max_size == value.long_edge


def test_resolve_h3_size_setting_rejects_unknown_product_value():
    with pytest.raises(H3SizeSettingError, match="unsupported H3 resolution"):
        resolve_h3_size_setting("4k", "9:16")
```

- [ ] **步骤 2：运行测试并确认因模块不存在而失败**

运行：

```powershell
& '.\.venv\Scripts\python.exe' -m pytest tests\media_capabilities\video\test_h3_size_settings.py -q
```

预期：`ModuleNotFoundError: novelvideo.media_capabilities.video.h3_size_settings`。

- [ ] **步骤 3：实现确定性 H3 档位值对象和解析器**

```python
from dataclasses import dataclass


class H3SizeSettingError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class H3SizeSetting:
    resolution: str
    aspect_ratio: str
    megapixels: float
    multiple: int
    width: int
    height: int

    @property
    def long_edge(self) -> int:
        return max(self.width, self.height)

    @property
    def ref_max_size(self) -> int:
        return self.long_edge


_PRESETS = {
    ("720p", "9:16"): (0.9, 736, 1280),
    ("720p", "16:9"): (0.9, 1280, 736),
    ("1080p", "9:16"): (2.0, 1088, 1920),
    ("1080p", "16:9"): (2.0, 1920, 1088),
}
```

解析器只接受表中组合，不从宽高面积反算 `megapixels`，也不把 `720p` 解析为长边 720。

- [ ] **步骤 4：让 runtime 使用产品档位并传显式质量目标**

将 `_director_output_settings()` 改为调用 `resolve_h3_size_setting()`，返回：

```python
{
    "mode": "fixed",
    "aspectRatio": setting.aspect_ratio,
    "megapixels": setting.megapixels,
    "multiple": setting.multiple,
    "width": setting.width,
    "height": setting.height,
    "longEdge": setting.long_edge,
    "refMaxSize": setting.ref_max_size,
}
```

创建 `VideoGenerationRequest` 时将质量校验分辨率设为 `f"{width}x{height}"`，避免 `quality.py` 忽略 `720p` 标签。

- [ ] **步骤 5：更新 runtime 旧断言并运行测试**

把 `test_h3_runtime.py` 中旧的 `416x736 / 0.306` 断言改为 `736x1280 / 0.9`，补充 1080p 和横屏断言。

运行：

```powershell
& '.\.venv\Scripts\python.exe' -m pytest tests\media_capabilities\video\test_h3_size_settings.py tests\media_capabilities\video\test_h3_runtime.py -q
```

预期：全部通过，且测试明确断言 `megapixels` 保持 `0.9` 或 `2.0`。

- [ ] **步骤 6：提交**

```powershell
git add src/novelvideo/media_capabilities/video/h3_size_settings.py src/novelvideo/media_capabilities/video/runtime.py tests/media_capabilities/video/test_h3_size_settings.py tests/media_capabilities/video/test_h3_runtime.py
git commit -m "fix(video): map H3 size presets exactly"
```

## 任务 2：建立通用工作流参数 Schema

**文件：**

- 新建：`src/novelvideo/media_capabilities/video/parameters.py`
- 新建：`tests/media_capabilities/video/test_parameters.py`
- 修改：`src/novelvideo/media_capabilities/video/workflow_registry.py`
- 修改：`tests/media_capabilities/video/test_workflow_registry.py`
- 修改：`src/novelvideo/media_capabilities/video/catalog.py`
- 修改：`tests/media_capabilities/video/test_catalog.py`
- 修改：`tests/test_api_media_capabilities.py`

- [ ] **步骤 1：写参数 Schema 和目录契约失败测试**

覆盖：

```python
definition = build_test_h3_definition()
assert definition.parameters[0].key == "resolution"
assert definition.parameters[0].default == "720p"
assert [option.value for option in definition.parameters[0].options] == [
    "720p", "1080p"
]
assert resolve_workflow_parameters(definition, {}) == {"resolution": "720p"}
```

并断言未知键、`resolution=4k`、重复参数键、默认值不在 options 内都会抛出 `VideoWorkflowParameterError` 或 Pydantic 校验错误。

- [ ] **步骤 2：运行测试确认缺少参数模型**

```powershell
& '.\.venv\Scripts\python.exe' -m pytest tests\media_capabilities\video\test_parameters.py tests\media_capabilities\video\test_workflow_registry.py tests\media_capabilities\video\test_catalog.py tests\test_api_media_capabilities.py -q
```

预期：新导入或 `parameters` 字段断言失败。

- [ ] **步骤 3：实现供应商无关的 enum 参数模型**

在 `parameters.py` 定义 frozen Pydantic 模型：

```python
class VideoWorkflowParameterOption(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    value: str
    label: str
    description: str = ""
    relative_cost: Literal["standard", "higher"] = "standard"


class VideoWorkflowParameterDefinition(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    key: str
    type: Literal["enum"] = "enum"
    label: str
    description: str = ""
    default: str
    scope: Literal["narrative_group"] = "narrative_group"
    options: tuple[VideoWorkflowParameterOption, ...]
```

实现 `resolve_workflow_parameters(definition, overrides)`：只接收 Schema 声明的键，先填默认值再覆盖，返回新的 `dict[str, str]`，非法值必须报错而非回退。

- [ ] **步骤 4：在 H3 工作流声明 resolution 参数**

`VideoWorkflowDefinition` 增加：

```python
parameters: tuple[VideoWorkflowParameterDefinition, ...] = ()
```

H3 参数选项固定为：

```python
VideoWorkflowParameterDefinition(
    key="resolution",
    label="生成清晰度",
    description="同一叙事组内所有视频单元统一使用该档位。",
    default="720p",
    options=(
        VideoWorkflowParameterOption(
            value="720p", label="720p 标准", relative_cost="standard"
        ),
        VideoWorkflowParameterOption(
            value="1080p",
            label="1080p 高清",
            description="画质更高，预计耗时和额度增加。",
            relative_cost="higher",
        ),
    ),
)
```

- [ ] **步骤 5：让目录 API 原样公开产品参数**

给 `VideoModelCatalogItem` 增加 `parameters`，从 registry 复制；API 不得出现 `megapixels`、节点 ID、width 或 height。

- [ ] **步骤 6：运行测试并提交**

```powershell
& '.\.venv\Scripts\python.exe' -m pytest tests\media_capabilities\video\test_parameters.py tests\media_capabilities\video\test_workflow_registry.py tests\media_capabilities\video\test_catalog.py tests\test_api_media_capabilities.py -q
git add src/novelvideo/media_capabilities/video/parameters.py src/novelvideo/media_capabilities/video/workflow_registry.py src/novelvideo/media_capabilities/video/catalog.py tests/media_capabilities/video/test_parameters.py tests/media_capabilities/video/test_workflow_registry.py tests/media_capabilities/video/test_catalog.py tests/test_api_media_capabilities.py
git commit -m "feat(video): publish workflow parameter schema"
```

预期：测试全绿；目录响应只包含产品级 Schema。

## 任务 3：保存项目级工作流参数默认值

**文件：**

- 修改：`src/novelvideo/api/schemas.py`
- 修改：`src/novelvideo/api/routes/projects.py`
- 修改：`tests/test_project_media_defaults.py`
- 修改：`frontend/src/lib/queries/media-models.ts`
- 修改：`frontend/src/__tests__/lib/queries/media-models.test.ts`

- [ ] **步骤 1：写部分更新与工作流隔离失败测试**

后端测试必须证明：

```python
request = MediaDefaultsRequest(
    video_model="runninghub:minimax-h3",
    video_workflow_parameters={
        "runninghub:minimax-h3": {"resolution": "1080p"}
    },
)
```

保存后 GET 原样返回；后续只更新图像默认值时保留 `video_workflow_parameters`；非法工作流 ID、未知参数和 `4k` 在写配置前返回 422/400。

- [ ] **步骤 2：运行测试确认请求模型和响应缺字段**

```powershell
& '.\.venv\Scripts\python.exe' -m pytest tests\test_project_media_defaults.py -q
```

- [ ] **步骤 3：扩展项目媒体默认契约**

在 `MediaDefaultsRequest` 增加可选字段：

```python
video_workflow_parameters: dict[str, dict[str, str]] | None = None
```

`_media_defaults_payload()` 总是返回规范化后的：

```json
"video_workflow_parameters": {
  "runninghub:minimax-h3": {"resolution": "720p"}
}
```

PUT 时只在请求显式带该字段时替换对应工作流参数；未提供时保留已有配置。每个命名空间通过 registry 和 `resolve_workflow_parameters()` 校验。

- [ ] **步骤 4：更新前端类型和 mutation**

增加：

```ts
export interface VideoWorkflowParameterOption { /* 与 API 同名字段 */ }
export interface VideoWorkflowParameterDefinition { /* enum Schema */ }
export type VideoWorkflowParameterValues = Record<string, string>;

export interface MediaDefaults {
  // 现有字段保持不变
  video_workflow_parameters: Record<string, VideoWorkflowParameterValues>;
}
```

`useUpdateMediaDefaults()` 接受并发送可选 `videoWorkflowParameters`，不再用旧顶层 `video_resolution` 作为新 UI 的事实来源。

- [ ] **步骤 5：运行前后端测试并提交**

```powershell
& '.\.venv\Scripts\python.exe' -m pytest tests\test_project_media_defaults.py -q
pnpm --dir frontend test -- src/__tests__/lib/queries/media-models.test.ts
git add src/novelvideo/api/schemas.py src/novelvideo/api/routes/projects.py tests/test_project_media_defaults.py frontend/src/lib/queries/media-models.ts frontend/src/__tests__/lib/queries/media-models.test.ts
git commit -m "feat(video): persist workflow parameter defaults"
```

## 任务 4：叙事组设置、revision 与 CAS API

**文件：**

- 修改：`src/novelvideo/narrative_groups/models.py`
- 修改：`src/novelvideo/narrative_groups/service.py`
- 修改：`src/novelvideo/api/routes/narrative_groups.py`
- 修改：`tests/test_narrative_group_service.py`
- 修改：`tests/test_api_narrative_groups.py`

- [ ] **步骤 1：写 sidecar 迁移和 CAS 失败测试**

测试以下事实：

- 旧 sidecar 无 `video_settings` 时加载为 `workflow_id="runninghub:minimax-h3"`、`revision=0`、空覆盖。
- 更新 `resolution=1080p` 后 revision 从 0 变为 1。
- 使用旧 `expected_revision=0` 再更新会报 stale，且 sidecar 不变。
- 保存与项目默认相同的值会从 `overrides` 删除。
- queued/running 状态拒绝修改。
- 切换工作流时不复用旧工作流覆盖值。

- [ ] **步骤 2：运行测试确认模型和服务函数缺失**

```powershell
& '.\.venv\Scripts\python.exe' -m pytest tests\test_narrative_group_service.py tests\test_api_narrative_groups.py -q
```

- [ ] **步骤 3：增加不可变 VideoSettings 模型并兼容旧 sidecar**

```python
@dataclass(frozen=True)
class VideoSettings:
    workflow_id: str = H3_WORKFLOW_ID
    revision: int = 0
    overrides: dict[str, str] = field(default_factory=dict)
```

`NarrativeGroup` 增加 `video_settings`；`_group_from_dict()` 缺字段时创建默认值；`to_dict()` 显式序列化为 JSON 对象。

- [ ] **步骤 4：实现 sidecar 锁内 CAS 更新**

新增：

```python
def update_video_settings(
    project_dir,
    episode,
    group_id,
    *,
    expected_revision: int,
    workflow_id: str,
    overrides: Mapping[str, str],
    project_defaults: Mapping[str, str],
) -> NarrativeGroup:
    ...
```

在 `_sidecar_guard` 内检查 settings revision 和 video stage 状态；规范化覆盖项，只保留与项目默认不同的键；原子保存 sidecar。

- [ ] **步骤 5：增加设置更新 API**

新增路由：

```text
PUT /api/v1/projects/{project}/episodes/{episode}/narrative-groups/{group_id}/video/settings
```

请求：

```json
{
  "expected_revision": 0,
  "workflow_id": "runninghub:minimax-h3",
  "overrides": {"resolution": "1080p"}
}
```

路由解析项目默认，调用 Schema 校验，再调用 CAS 服务；stale 返回 409，非法键和值返回 422，queued/running 返回 409。

- [ ] **步骤 6：运行测试并提交**

```powershell
& '.\.venv\Scripts\python.exe' -m pytest tests\test_narrative_group_service.py tests\test_api_narrative_groups.py -q
git add src/novelvideo/narrative_groups/models.py src/novelvideo/narrative_groups/service.py src/novelvideo/api/routes/narrative_groups.py tests/test_narrative_group_service.py tests/test_api_narrative_groups.py
git commit -m "feat(video): add narrative group parameter overrides"
```

## 任务 5：冻结生成参数快照并贯通适配器

**文件：**

- 修改：`src/novelvideo/api/routes/narrative_groups.py`
- 修改：`src/novelvideo/task_backend/runners/narrative_group_video.py`
- 修改：`src/novelvideo/media_capabilities/video/adapters.py`
- 修改：`src/novelvideo/media_capabilities/video/runtime.py`
- 修改：`tests/test_api_narrative_groups.py`
- 修改：`tests/test_task_narrative_group_video_runner.py`
- 修改：`tests/media_capabilities/video/test_adapters.py`

- [ ] **步骤 1：写入队快照和 stale revision 失败测试**

断言生成请求包含：

```json
{
  "revision": 4,
  "plan_revision": 2,
  "settings_revision": 1,
  "model": "runninghub:minimax-h3"
}
```

后端入队 payload 冻结：

```json
"workflow_parameters": {"resolution": "1080p"}
```

而不是依赖 worker 执行时的项目默认。设置 revision 不一致必须在预留 video revision 和调用 DeepSeek/RunningHub 前返回 409。

- [ ] **步骤 2：运行测试确认生成契约缺 settings revision**

```powershell
& '.\.venv\Scripts\python.exe' -m pytest tests\test_api_narrative_groups.py tests\test_task_narrative_group_video_runner.py tests\media_capabilities\video\test_adapters.py -q
```

- [ ] **步骤 3：扩展生成请求并在路由内冻结参数**

`NarrativeGroupVideoRequest`（API 模型）增加 `settings_revision: int | None`。新前端必传；旧请求可缺省并走兼容分支。

在 `_enqueue_group_video()` 中：

1. 读取 workflow definition。
2. 检查 group `video_settings.revision`。
3. 读取该 workflow 的项目默认。
4. 合并本组覆盖并通过 Schema 校验。
5. 将 `workflow_parameters` 写入 task payload。
6. 再预留新的 video stage revision。

- [ ] **步骤 4：把通用参数快照传给适配器**

将适配器请求改为：

```python
@dataclass(frozen=True, slots=True)
class NarrativeGroupVideoRequest:
    segments: tuple[H3DirectorSegment, ...]
    output_path: str
    aspect_ratio: str
    workflow_parameters: Mapping[str, str]
    on_provider_submitted: Callable[..., ...] | None = None
```

H3 适配器读取 `workflow_parameters["resolution"]` 并调用 H3 runtime。其他工作流未来可实现自己的映射，不得在 runner 中出现 H3 的 `megapixels` 分支。

- [ ] **步骤 5：保留旧 resolution 兼容但统一校验**

当旧客户端只发送顶层 `resolution` 时，将其规范化为 `workflow_parameters={"resolution": value}`，仍通过 workflow Schema；非法值不可静默回退。新任务 payload 不再新增裸 `resolution`。

- [ ] **步骤 6：运行测试并提交**

```powershell
& '.\.venv\Scripts\python.exe' -m pytest tests\test_api_narrative_groups.py tests\test_task_narrative_group_video_runner.py tests\media_capabilities\video\test_adapters.py -q
git add src/novelvideo/api/routes/narrative_groups.py src/novelvideo/task_backend/runners/narrative_group_video.py src/novelvideo/media_capabilities/video/adapters.py src/novelvideo/media_capabilities/video/runtime.py tests/test_api_narrative_groups.py tests/test_task_narrative_group_video_runner.py tests/media_capabilities/video/test_adapters.py
git commit -m "feat(video): freeze workflow parameters per generation"
```

## 任务 6：记录供应商参数、实际输出与质量异常

**文件：**

- 修改：`src/novelvideo/media_capabilities/video/h3_timeline.py`
- 修改：`src/novelvideo/media_capabilities/video/adapters.py`
- 修改：`src/novelvideo/media_capabilities/video/runtime.py`
- 修改：`src/novelvideo/task_backend/runners/narrative_group_video.py`
- 修改：`src/novelvideo/narrative_groups/models.py`
- 修改：`src/novelvideo/narrative_groups/service.py`
- 修改：`src/novelvideo/api/routes/narrative_groups.py`
- 修改：`tests/media_capabilities/video/test_h3_timeline.py`
- 修改：`tests/media_capabilities/video/test_adapters.py`
- 修改：`tests/test_task_narrative_group_video_runner.py`
- 修改：`tests/test_api_narrative_groups.py`

- [ ] **步骤 1：写历史事实和尺寸异常失败测试**

manifest 必须保存：

```json
{
  "workflow_parameters": {"resolution": "1080p"},
  "provider_parameters": {
    "megapixels": 2.0,
    "multiple": 32,
    "width": 1088,
    "height": 1920,
    "longEdge": 1920,
    "refMaxSize": 1920
  },
  "actual_output": {"width": 1088, "height": 1920}
}
```

另写测试模拟 ffprobe 返回 `736x1280`，而目标为 `1088x1920`：产物与 provider task ID 必须保留，stage 状态为 `partial_failure`，错误包含目标和实际尺寸。

- [ ] **步骤 2：运行测试确认 manifest/result 缺字段**

```powershell
& '.\.venv\Scripts\python.exe' -m pytest tests\media_capabilities\video\test_h3_timeline.py tests\media_capabilities\video\test_adapters.py tests\test_task_narrative_group_video_runner.py tests\test_api_narrative_groups.py -q
```

- [ ] **步骤 3：扩展生成结果和 manifest**

```python
@dataclass(frozen=True, slots=True)
class NarrativeGroupVideoResult:
    output_path: str
    provider_task_id: str | None
    actual_mode: str
    provider_parameters: dict[str, object]
    actual_output: dict[str, int]
```

`H3DirectorOutputManifest` 增加默认空对象字段 `workflow_parameters`、`provider_parameters`、`actual_output`，保证旧 manifest 仍可加载。

- [ ] **步骤 4：把同一快照写入所有 manifest 状态**

`quality_rejected`、`submitted`、`transport_failed`、`generated`、`postprocess_failed`、`completed` 都保留 `workflow_parameters` 和已知的供应商参数；`_manifest_with_status()` 只能覆盖显式更新字段，不能丢失历史快照。

- [ ] **步骤 5：保存 stage 生成事实并检测尺寸不一致**

给 `GroupStageState` 增加默认空字典：

```python
workflow_parameters: dict[str, str]
provider_parameters: dict[str, object]
actual_output: dict[str, int]
```

runner 在下载后使用 `_probe_video()` 的真实尺寸。若不匹配：

- 保留 `video_asset`、`manifest_asset`、provider task ID 和参数；
- manifest 状态设为 `quality_mismatch`；
- stage 状态设为 `partial_failure`；
- 不返回完整成功。

- [ ] **步骤 6：让提示词审查读取历史 manifest**

`_serialize_prompt_review()` 顶层返回上述三组参数，禁止从当前项目默认重新推导。旧 manifest 缺字段时返回空对象。

- [ ] **步骤 7：运行测试并提交**

```powershell
& '.\.venv\Scripts\python.exe' -m pytest tests\media_capabilities\video\test_h3_timeline.py tests\media_capabilities\video\test_adapters.py tests\test_task_narrative_group_video_runner.py tests\test_api_narrative_groups.py -q
git add src/novelvideo/media_capabilities/video/h3_timeline.py src/novelvideo/media_capabilities/video/adapters.py src/novelvideo/media_capabilities/video/runtime.py src/novelvideo/task_backend/runners/narrative_group_video.py src/novelvideo/narrative_groups/models.py src/novelvideo/narrative_groups/service.py src/novelvideo/api/routes/narrative_groups.py tests/media_capabilities/video/test_h3_timeline.py tests/media_capabilities/video/test_adapters.py tests/test_task_narrative_group_video_runner.py tests/test_api_narrative_groups.py
git commit -m "feat(video): persist output parameter provenance"
```

## 任务 7：增加前端查询契约和清晰度控件

**文件：**

- 修改：`frontend/src/lib/queries/narrative-groups.ts`
- 修改：`frontend/src/__tests__/lib/queries/narrative-groups.test.ts`
- 新建：`frontend/src/components/episode/narrative-workbench/group-video-parameters.tsx`
- 新建：`frontend/src/__tests__/components/episode/narrative-workbench/group-video-parameters.test.tsx`
- 修改：`frontend/src/components/episode/narrative-workbench/group-video-stage.tsx`

- [ ] **步骤 1：写前端 payload 和控件失败测试**

查询测试断言：

```ts
expect(narrativeGroupVideoPayload(input)).toEqual({
  model: "runninghub:minimax-h3",
  mode: "auto",
  revision: 3,
  plan_revision: 2,
  settings_revision: 1,
  aspect_ratio: "9:16",
});
```

组件测试覆盖：

- 默认档位显示“720p 标准”和“项目默认”。
- 点击 1080p 只调用 `onOverrideChange({ resolution: "1080p" })`。
- 1080p 显示“画质更高，预计耗时和额度增加”。
- 本组覆盖显示“恢复项目默认”和“设为项目默认”。
- queued/running 时两个选项和操作按钮全部禁用。

- [ ] **步骤 2：运行测试确认类型和组件不存在**

```powershell
pnpm --dir frontend test -- src/__tests__/lib/queries/narrative-groups.test.ts src/__tests__/components/episode/narrative-workbench/group-video-parameters.test.tsx
```

- [ ] **步骤 3：增加叙事组设置类型与 mutation**

```ts
export interface NarrativeGroupVideoSettings {
  workflow_id: string;
  revision: number;
  overrides: Record<string, string>;
}
```

为 `NarrativeGroup` 增加 `video_settings`。新增设置路径、`useUpdateNarrativeGroupVideoSettings()`，PUT 请求发送 `expected_revision`、`workflow_id`、`overrides`；成功后使叙事组 query 失效。

生成 payload 增加 `settingsRevision`，新前端不发送裸 `resolution`。

- [ ] **步骤 4：实现聚焦的 GroupVideoParameters 组件**

组件 props 只接收：参数定义、effective values、source、busy、`onOverrideChange`、`onRestoreDefault`、`onPromoteDefault`。组件不读取 RunningHub 字段，不硬编码节点 ID；首版可限定渲染 `type === "enum"`。

720p/1080p 用紧凑单选卡或按钮组直接放在“视频生成”卡片头部下方，不弹窗。

- [ ] **步骤 5：接入 GroupVideoStage 并运行测试**

`GroupVideoStage` 接收一个 `parameters` React 节点或明确参数 props，并将控件放在模型信息和方案单元之间。保持相邻 Beat 合并/拆分功能不变。

```powershell
pnpm --dir frontend test -- src/__tests__/lib/queries/narrative-groups.test.ts src/__tests__/components/episode/narrative-workbench/group-video-parameters.test.tsx src/__tests__/components/episode/narrative-workbench/group-video-stage.test.tsx
```

- [ ] **步骤 6：提交**

```powershell
git add frontend/src/lib/queries/narrative-groups.ts frontend/src/__tests__/lib/queries/narrative-groups.test.ts frontend/src/components/episode/narrative-workbench/group-video-parameters.tsx frontend/src/__tests__/components/episode/narrative-workbench/group-video-parameters.test.tsx frontend/src/components/episode/narrative-workbench/group-video-stage.tsx frontend/src/__tests__/components/episode/narrative-workbench/group-video-stage.test.tsx
git commit -m "feat(video): add narrative group resolution controls"
```

## 任务 8：接入工作台、项目默认和旧视频差异提示

**文件：**

- 修改：`frontend/src/components/episode/narrative-workbench/narrative-group-workbench.tsx`
- 修改：`frontend/src/components/episode/narrative-workbench/group-video-result.tsx`
- 修改：`frontend/src/components/episode/narrative-workbench/group-video-prompt-drawer.tsx`
- 修改：`frontend/src/__tests__/components/episode/narrative-workbench/narrative-group-workbench-references.test.tsx`
- 修改：`frontend/src/__tests__/components/episode/narrative-workbench/group-video-submit.test.tsx`
- 修改：`frontend/src/__tests__/components/episode/narrative-workbench/group-video-result.test.tsx`
- 修改：`frontend/src/__tests__/components/episode/narrative-workbench/group-video-prompt-drawer.test.tsx`

- [ ] **步骤 1：写工作台行为失败测试**

覆盖完整用户路径：

1. 当前项目默认 720p，本组无覆盖。
2. 点击 1080p，PUT 本组 settings，刷新后显示“本组覆盖”。
3. 点击生成，请求携带最新 `settings_revision`。
4. 点击“恢复项目默认”，PUT 空 overrides。
5. 点击“设为项目默认”，更新项目默认为 1080p，然后清理本组冗余覆盖。
6. 已有 720p 视频时切换到 1080p，视频仍存在且显示“现有视频：720p · 目标设置：1080p · 需重新生成”。

- [ ] **步骤 2：运行组件测试确认尚未接线**

```powershell
pnpm --dir frontend test -- src/__tests__/components/episode/narrative-workbench/narrative-group-workbench-references.test.tsx src/__tests__/components/episode/narrative-workbench/group-video-submit.test.tsx src/__tests__/components/episode/narrative-workbench/group-video-result.test.tsx src/__tests__/components/episode/narrative-workbench/group-video-prompt-drawer.test.tsx
```

- [ ] **步骤 3：在 workbench 计算 effective 参数和来源**

算法固定为：

```ts
const projectValues = defaults.video_workflow_parameters[modelId] ?? schemaDefaults;
const overrides = group.video_settings.workflow_id === modelId
  ? group.video_settings.overrides
  : {};
const effectiveValues = { ...projectValues, ...overrides };
const source = Object.keys(overrides).length ? "group_override" : "project_default";
```

修改档位先等待设置 PUT 成功；生成按钮使用服务端返回的最新 revision。queued/running 时不允许 mutation。

- [ ] **步骤 4：实现“设为项目默认”的两步一致性操作**

先更新项目 media defaults，再以最新 settings revision 清除本组同值覆盖。任一步失败都提示错误并重新拉取两个 query，不能在客户端假装成功。

- [ ] **步骤 5：展示当前目标、历史参数和实际输出**

`GroupVideoResult` 从 stage 快照判断 stale，不比较文案标签：

```ts
const needsRegeneration =
  stage.video_asset
  && stage.workflow_parameters?.resolution !== effectiveValues.resolution;
```

旧视频播放器、下载和提示词按钮保持可用。`GroupVideoPromptDrawer` 展示：产品档位、`megapixels`、目标宽高、ffprobe 实际宽高；字段缺失时显示“历史任务未记录”，不从当前默认补写。

- [ ] **步骤 6：运行组件测试和 TypeScript 构建**

```powershell
pnpm --dir frontend test -- src/__tests__/components/episode/narrative-workbench/narrative-group-workbench-references.test.tsx src/__tests__/components/episode/narrative-workbench/group-video-submit.test.tsx src/__tests__/components/episode/narrative-workbench/group-video-result.test.tsx src/__tests__/components/episode/narrative-workbench/group-video-prompt-drawer.test.tsx src/__tests__/components/episode/narrative-workbench/group-video-parameters.test.tsx
pnpm --dir frontend build
```

- [ ] **步骤 7：提交**

```powershell
git add frontend/src/components/episode/narrative-workbench/narrative-group-workbench.tsx frontend/src/components/episode/narrative-workbench/group-video-result.tsx frontend/src/components/episode/narrative-workbench/group-video-prompt-drawer.tsx frontend/src/__tests__/components/episode/narrative-workbench/narrative-group-workbench-references.test.tsx frontend/src/__tests__/components/episode/narrative-workbench/group-video-submit.test.tsx frontend/src/__tests__/components/episode/narrative-workbench/group-video-result.test.tsx frontend/src/__tests__/components/episode/narrative-workbench/group-video-prompt-drawer.test.tsx
git commit -m "feat(video): wire resolution defaults and provenance UI"
```

## 任务 9：兼容迁移与集中回归

**文件：**

- 修改：上述实现触及的测试文件；只在回归暴露缺陷时修改对应生产文件。

- [ ] **步骤 1：增加旧数据兼容夹具**

至少覆盖：

- 旧项目配置只有 `video_resolution`。
- 旧 narrative group sidecar 没有 `video_settings` 和 stage 参数快照。
- 旧 H3 manifest 没有 `workflow_parameters`、`provider_parameters`、`actual_output`。
- 旧客户端生成请求仍提交顶层 `resolution=720p`。

迁移规则：旧配置若为受支持的 `720p`/`1080p`，读取时映射到 H3 默认命名空间；未知旧值返回明确配置错误，不自动消耗额度。

- [ ] **步骤 2：运行视频能力后端测试集**

```powershell
& '.\.venv\Scripts\python.exe' -m pytest tests\media_capabilities\video tests\test_api_media_capabilities.py tests\test_project_media_defaults.py tests\test_narrative_group_service.py tests\test_api_narrative_groups.py tests\test_task_narrative_group_video_runner.py -q
```

预期：全部通过，无网络和付费供应商调用。

- [ ] **步骤 3：运行后端静态检查**

```powershell
& '.\.venv\Scripts\python.exe' -m ruff check src\novelvideo\media_capabilities\video src\novelvideo\narrative_groups src\novelvideo\api\routes\narrative_groups.py src\novelvideo\api\routes\projects.py src\novelvideo\task_backend\runners\narrative_group_video.py tests\media_capabilities\video tests\test_project_media_defaults.py tests\test_narrative_group_service.py tests\test_api_narrative_groups.py tests\test_task_narrative_group_video_runner.py
```

- [ ] **步骤 4：运行前端相关测试和构建**

```powershell
pnpm --dir frontend test -- src/__tests__/lib/queries/media-models.test.ts src/__tests__/lib/queries/narrative-groups.test.ts src/__tests__/components/episode/narrative-workbench
pnpm --dir frontend build
```

- [ ] **步骤 5：检查 diff 和提交最后兼容修正**

```powershell
git diff --check
git status --short
```

若本任务产生尚未提交的兼容修正，先用 `git diff --name-only` 列出文件，再逐个核对，只对任务 1–8 已列出的相关生产文件或测试文件执行显式 `git add path/to/file`，然后提交：

```powershell
git commit -m "test(video): cover workflow parameter compatibility"
```

不得添加工作区中原有的无关改动。

## 任务 10：本地 UI 验收与付费真机检查点

**文件：**

- 不预设代码修改；发现缺陷时回到对应任务，以测试先行修复。

- [ ] **步骤 1：启动后端和前端进行无付费 UI 验收**

使用项目已有启动方式，在任一有叙事组和历史视频的项目验证：

- 视频卡片直接显示 720p/1080p。
- 切换后显示本组覆盖，不自动生成。
- 恢复默认和设为项目默认行为正确。
- 已有视频仍可播放，并在设置不一致时显示“需重新生成”。
- 提示词抽屉可看到历史参数快照。

- [ ] **步骤 2：检查浏览器请求与后端任务快照**

确认请求只发送产品参数和 revision，不出现 RunningHub 节点 ID；后端任务 payload 已冻结 `workflow_parameters`。

- [ ] **步骤 3：停在付费真机授权闸门**

向用户分别请求 720p 和 1080p 两次真实 RunningHub 生成额度授权。未获得明确授权时不得调用供应商。

- [ ] **步骤 4：获批后执行真机对照测试**

使用同一叙事组短素材各生成一次，记录：

- 本地 task ID 和 RunningHub task ID；
- manifest 中的 `megapixels`、目标尺寸；
- ffprobe 实际尺寸、编码、时长和可播放性；
- 前端结果区与提示词抽屉展示的一致性。

720p 应为 9:16 `736×1280` / 16:9 `1280×736`，1080p 应为 9:16 `1088×1920` / 16:9 `1920×1088`。任何不一致都按质量异常保留产物并报告，不宣称通过。

## 完成判据

- [ ] H3 720p/1080p 分别使用精确 `0.9`/`2.0` Director 档位。
- [ ] 项目默认和本组覆盖有独立、可追踪的 revision。
- [ ] 生成任务冻结产品参数，worker 不受后续默认值变化影响。
- [ ] manifest、stage、提示词审查和 UI 展示同一历史事实。
- [ ] 设置变化不删除旧视频，也不自动触发付费生成。
- [ ] 实际尺寸不匹配保留产物并进入质量异常。
- [ ] 后端相关测试、ruff、前端相关测试和 build 全部通过。
- [ ] `git diff --check` 通过，且未提交用户原有无关改动。
