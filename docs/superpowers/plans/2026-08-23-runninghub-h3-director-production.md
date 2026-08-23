# RunningHub 视频注册表与 H3 导演编译器实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 将叙事组视频收口到可扩充的 RunningHub 工作流注册表，并用可验证、可复盘的两阶段 MiniMax H3 导演提示词编译器提升生成质量。

**架构：** 服务端注册表是视频模型、能力和执行适配器的唯一事实来源；当前只注册 RunningHub MiniMax H3。H3 适配器先生成类型化导演计划，经质量门禁后确定性编译为官方 wire prompt，再提交现有 RunningHub 并发执行器；最终计划、提示词和质量报告写入 manifest 并由叙事组结果卡展示。

**技术栈：** Python 3.11、FastAPI、Pydantic v2、PydanticAI、pytest、React 18、TypeScript、TanStack Query、Vitest、Testing Library、RunningHub workflow runtime。

---

## 变更边界与文件结构

- 创建 `src/novelvideo/media_capabilities/video/workflow_registry.py`：视频工作流定义、场景过滤、默认模型和适配器解析。
- 修改 `src/novelvideo/media_capabilities/video/catalog.py`：由注册表生成公开目录，不再手写 H3 目录项。
- 修改 `src/novelvideo/api/routes/narrative_groups.py`、`src/novelvideo/api/routes/projects.py`：注册表校验请求和项目默认值。
- 创建 `src/novelvideo/media_capabilities/video/adapters.py`：执行适配器协议与 H3 适配器。
- 创建 `src/novelvideo/media_capabilities/video/h3_director_plan.py`：类型化镜头、动作、对白、首尾差异和质量报告。
- 创建 `src/novelvideo/media_capabilities/video/h3_prompt_compiler.py`：确定性官方格式编译。
- 修改 `src/novelvideo/media_capabilities/video/h3_prompt_optimizer.py`：改为导演计划生成器，去除直接 NewAPI 工厂依赖。
- 修改 `src/novelvideo/media_capabilities/video/h3_prompt_profile.py`：升级官方规则版本。
- 修改 `src/novelvideo/media_capabilities/video/h3_timeline.py`：manifest 保存导演计划、最终提示词、质量报告和版本快照。
- 修改 `src/novelvideo/task_backend/runners/narrative_group_video.py`：注册表路由、计划生成、质量门禁与 manifest 持久化。
- 修改 `frontend/src/lib/queries/media-models.ts`：删除 legacy 合并，提供有效模型解析。
- 修改 `frontend/src/lib/queries/narrative-groups.ts`：增加提示词复盘 DTO 与查询。
- 修改 `frontend/src/components/episode/narrative-workbench/narrative-group-workbench.tsx`：单模型标签、多模型选择和旧值回退。
- 修改 `frontend/src/components/episode/narrative-workbench/group-video-result.tsx`：增加“生成提示词”入口。
- 创建 `frontend/src/components/episode/narrative-workbench/group-video-prompt-drawer.tsx`：展示、复制和下载最终提交提示词。
- 测试分别落在现有 `tests/media_capabilities/video/`、`tests/test_api_narrative_groups.py`、`tests/test_task_narrative_group_video_runner.py` 和前端 narrative-workbench 测试目录。

> 当前 main 工作树已有大量未提交改动。每个提交只能暂存本任务列出的文件；不得使用 `git add .`、reset 或 checkout 覆盖其他改动。现有临时单值 `Literal["runninghub:minimax-h3"]` 和固定 H3 标签只是红灯阶段实现，必须在任务 2/3 替换。

### 任务 1：建立可扩充的视频工作流注册表

**文件：**
- 创建：`src/novelvideo/media_capabilities/video/workflow_registry.py`
- 修改：`src/novelvideo/media_capabilities/video/catalog.py`
- 测试：`tests/media_capabilities/video/test_workflow_registry.py`
- 测试：`tests/media_capabilities/video/test_catalog.py`

- [ ] **步骤 1：编写失败的注册表测试**

```python
def test_registry_lists_only_enabled_narrative_workflows():
    registry = VideoWorkflowRegistry((
        VideoWorkflowDefinition(
            id="runninghub:minimax-h3", label="RunningHub MiniMax H3",
            provider="runninghub", adapter_key="minimax-h3",
            scenes=frozenset({VideoWorkflowScene.NARRATIVE_GROUP}),
            supported_modes=("auto", "i2va", "fl2va"), default_mode="auto",
            available=True,
        ),
        VideoWorkflowDefinition(
            id="runninghub:future", label="Future", provider="runninghub",
            adapter_key="future", scenes=frozenset({VideoWorkflowScene.FREEZONE}),
            supported_modes=("i2va",), default_mode="i2va", available=True,
        ),
    ))
    assert [item.id for item in registry.list(VideoWorkflowScene.NARRATIVE_GROUP)] == [
        "runninghub:minimax-h3"
    ]
    assert registry.default(VideoWorkflowScene.NARRATIVE_GROUP).id == "runninghub:minimax-h3"
```

- [ ] **步骤 2：运行测试验证失败**

运行：`python -m pytest tests/media_capabilities/video/test_workflow_registry.py tests/media_capabilities/video/test_catalog.py -q`

预期：FAIL，`workflow_registry` 模块不存在。

- [ ] **步骤 3：实现注册表最小接口**

```python
class VideoWorkflowScene(StrEnum):
    NARRATIVE_GROUP = "narrative_group"
    SINGLE_BEAT = "single_beat"
    FREEZONE = "freezone"

class VideoWorkflowDefinition(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    id: str
    label: str
    provider: Literal["runninghub"]
    adapter_key: str
    scenes: frozenset[VideoWorkflowScene]
    supported_modes: tuple[str, ...]
    default_mode: str
    available: bool
    unavailable_reason: str | None = None

class VideoWorkflowRegistry:
    def __init__(self, definitions: tuple[VideoWorkflowDefinition, ...]):
        self._definitions = definitions

    def list(self, scene: VideoWorkflowScene) -> tuple[VideoWorkflowDefinition, ...]:
        return tuple(item for item in self._definitions if scene in item.scenes)

    def resolve(self, model_id: str, scene: VideoWorkflowScene) -> VideoWorkflowDefinition:
        item = next((item for item in self.list(scene) if item.id == model_id), None)
        if item is None or not item.available:
            raise VideoWorkflowUnavailable(model_id)
        return item

    def default(self, scene: VideoWorkflowScene) -> VideoWorkflowDefinition:
        item = next((item for item in self.list(scene) if item.available), None)
        if item is None:
            raise VideoWorkflowUnavailable(f"no workflow for {scene.value}")
        return item
```

`build_video_workflow_registry(store, resolver)` 当前只创建 H3 定义，并复用现有凭据、workflow ID 和 profile 可用性检查。将 `catalog.list_video_models` 改为注册表目录投影。

- [ ] **步骤 4：运行注册表和目录测试**

运行：`python -m pytest tests/media_capabilities/video/test_workflow_registry.py tests/media_capabilities/video/test_catalog.py tests/test_api_media_capabilities.py -q`

预期：全部 PASS，公开目录只包含 `runninghub:minimax-h3`。

- [ ] **步骤 5：提交**

```bash
git add src/novelvideo/media_capabilities/video/workflow_registry.py src/novelvideo/media_capabilities/video/catalog.py tests/media_capabilities/video/test_workflow_registry.py tests/media_capabilities/video/test_catalog.py
git commit -m "feat(video): add RunningHub workflow registry"
```

### 任务 2：用注册表校验 API 和项目默认模型

**文件：**
- 修改：`src/novelvideo/api/routes/narrative_groups.py`
- 修改：`src/novelvideo/api/routes/projects.py`
- 测试：`tests/test_api_narrative_groups.py`
- 测试：`tests/test_project_media_defaults.py`

- [ ] **步骤 1：编写失败的 API 契约测试**

```python
def test_video_generate_accepts_registered_model_and_rejects_newapi(monkeypatch, tmp_path):
    accepted = client.post(endpoint, json={**payload, "model": "runninghub:minimax-h3"})
    rejected = client.post(endpoint, json={**payload, "model": "newapi_seedance-1.0-pro-fast"})
    assert accepted.status_code == 202
    assert rejected.status_code == 422

def test_media_defaults_fall_back_when_saved_model_is_not_registered(monkeypatch, tmp_path):
    save_project_config_in_state_dir(state_dir, config={
        "video_backend": "newapi_seedance-1.0-pro-fast"
    })
    response = client.get("/api/v1/projects/demo/media-defaults")
    assert response.json()["data"]["video_model"] == "runninghub:minimax-h3"
```

- [ ] **步骤 2：运行测试验证旧配置仍泄漏或单值 Literal 阻碍扩充**

运行：`python -m pytest tests/test_api_narrative_groups.py tests/test_project_media_defaults.py -q`

预期：至少一个 FAIL；当前请求 schema 写死 H3，且读取默认值原样返回 NewAPI。

- [ ] **步骤 3：改为字符串输入加注册表解析**

```python
class NarrativeGroupVideoRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str = Field(min_length=1)
    mode: Literal["auto", "i2va", "fl2va"] = "auto"

workflow = registry.resolve(
    request.model, VideoWorkflowScene.NARRATIVE_GROUP
)
if request.mode not in workflow.supported_modes:
    raise HTTPException(status_code=422, detail="Unsupported video workflow mode")
```

`_media_defaults_payload` 接收 registry，将未知、停用或非叙事组模型归一到 `registry.default(VideoWorkflowScene.NARRATIVE_GROUP).id`；PUT 同样拒绝无效模型。

- [ ] **步骤 4：运行 API 测试**

运行：`python -m pytest tests/test_api_narrative_groups.py tests/test_project_media_defaults.py -q`

预期：全部 PASS；NewAPI 返回 422，旧保存值读取为 H3。

- [ ] **步骤 5：提交**

```bash
git add src/novelvideo/api/routes/narrative_groups.py src/novelvideo/api/routes/projects.py tests/test_api_narrative_groups.py tests/test_project_media_defaults.py
git commit -m "fix(video): validate project models through registry"
```

### 任务 3：前端由能力目录自动决定固定标签或选择器

**文件：**
- 修改：`frontend/src/lib/queries/media-models.ts`
- 修改：`frontend/src/components/episode/narrative-workbench/narrative-group-workbench.tsx`
- 修改：`frontend/src/components/episode/narrative-workbench/project-video-model-select.tsx`
- 测试：`frontend/src/__tests__/lib/queries/media-models.test.ts`
- 测试：`frontend/src/__tests__/components/episode/narrative-workbench/narrative-group-workbench-references.test.tsx`

- [ ] **步骤 1：编写目录解析与 UI 红灯测试**

```ts
expect(resolveVideoModel("newapi_seedance-1.0-pro-fast", [h3])).toEqual(h3);
expect(screen.getByText("RunningHub MiniMax H3")).toBeInTheDocument();
expect(screen.queryByText("项目默认视频模型")).not.toBeInTheDocument();

// 注入 h3 与 future 两个可用目录项后
expect(screen.getByRole("combobox", { name: "视频模型" })).toBeInTheDocument();
```

- [ ] **步骤 2：运行测试验证第二模型不会自动出现**

运行：`frontend/node_modules/.bin/vitest.cmd run frontend/src/__tests__/lib/queries/media-models.test.ts frontend/src/__tests__/components/episode/narrative-workbench/narrative-group-workbench-references.test.tsx`

预期：FAIL；当前页面固定 H3 标签。

- [ ] **步骤 3：实现有效模型解析，删除 legacy 合并**

```ts
export function availableVideoModels(models: VideoModelCatalogItem[]) {
  return models.filter((item) => item.available);
}

export function resolveVideoModel(saved: string | null | undefined, models: VideoModelCatalogItem[]) {
  const available = availableVideoModels(models);
  return available.find((item) => item.id === saved) ?? available[0];
}
```

页面使用解析结果作为请求模型。一个可用模型渲染固定标签；多个可用模型渲染 `ProjectVideoModelSelect`。删除 `mergeVideoModelCatalog` 和 `useVideoBackends` 依赖。

- [ ] **步骤 4：运行前端测试**

运行：`frontend/node_modules/.bin/vitest.cmd run frontend/src/__tests__/lib/queries/media-models.test.ts frontend/src/__tests__/components/episode/narrative-workbench/narrative-group-workbench-references.test.tsx`

预期：全部 PASS。

- [ ] **步骤 5：提交**

```bash
git add frontend/src/lib/queries/media-models.ts frontend/src/components/episode/narrative-workbench/narrative-group-workbench.tsx frontend/src/components/episode/narrative-workbench/project-video-model-select.tsx frontend/src/__tests__/lib/queries/media-models.test.ts frontend/src/__tests__/components/episode/narrative-workbench/narrative-group-workbench-references.test.tsx
git commit -m "feat(video): drive model picker from workflow catalog"
```

### 任务 4：增加工作流执行适配器，避免 runner 写死 H3

**文件：**
- 创建：`src/novelvideo/media_capabilities/video/adapters.py`
- 修改：`src/novelvideo/task_backend/runners/narrative_group_video.py`
- 测试：`tests/media_capabilities/video/test_adapters.py`
- 测试：`tests/test_task_narrative_group_video_runner.py`

- [ ] **步骤 1：编写适配器选择红灯测试**

```python
def test_adapter_registry_resolves_h3_and_rejects_unknown():
    adapters = VideoWorkflowAdapters({"minimax-h3": fake_adapter})
    assert adapters.resolve("minimax-h3") is fake_adapter
    with pytest.raises(VideoWorkflowUnavailable):
        adapters.resolve("missing")
```

runner 测试注入两个 workflow definition，断言 payload 的 `model` 决定 adapter，且停用或未知 model 不调用 transport。

- [ ] **步骤 2：运行测试验证 runner 直接调用 H3 函数**

运行：`python -m pytest tests/media_capabilities/video/test_adapters.py tests/test_task_narrative_group_video_runner.py -q`

预期：FAIL，适配器模块不存在。

- [ ] **步骤 3：实现协议和 H3 适配器**

```python
class H3WorkflowAdapter:
    async def generate_narrative_group(
        self, ctx: ProjectContext, *, segments: tuple[H3DirectorSegment, ...],
        output_path: str, aspect_ratio: str, resolution: str | None,
    ) -> H3GenerationResult:
        return await generate_h3_director_video(
            ctx, segments=segments, output_path=output_path,
            aspect_ratio=aspect_ratio, resolution=resolution,
        )
```

runner 通过 registry definition 的 `adapter_key` 获取适配器。当前行为不改变 RunningHub 并发协调器。

- [ ] **步骤 4：运行适配器与 runner 测试**

运行：`python -m pytest tests/media_capabilities/video/test_adapters.py tests/test_task_narrative_group_video_runner.py -q`

预期：全部 PASS。

- [ ] **步骤 5：提交**

```bash
git add src/novelvideo/media_capabilities/video/adapters.py src/novelvideo/task_backend/runners/narrative_group_video.py tests/media_capabilities/video/test_adapters.py tests/test_task_narrative_group_video_runner.py
git commit -m "refactor(video): dispatch narrative jobs through adapters"
```

### 任务 5：定义结构化 H3 导演计划和官方格式编译器

**文件：**
- 创建：`src/novelvideo/media_capabilities/video/h3_director_plan.py`
- 创建：`src/novelvideo/media_capabilities/video/h3_prompt_compiler.py`
- 修改：`src/novelvideo/media_capabilities/video/h3_prompt_profile.py`
- 测试：`tests/media_capabilities/video/test_h3_director_plan.py`
- 测试：`tests/media_capabilities/video/test_h3_prompt_compiler.py`

- [ ] **步骤 1：编写 I2VA/FL2VA 黄金测试**

```python
plan = H3DirectorPlan(
    mode=H3Mode.FL2VA, duration_seconds=8.0, style="2D animation",
    shots=(H3ShotPlan(
        index=1, start_seconds=0, end_seconds=8,
        composition="medium shot", camera=CameraMove(
            kind="Pull Out", direction="backward", amplitude="small", speed="slow"
        ),
        beats=(
            ActionBeat(at_seconds=0, phase="establish", action="Lock Picture 1 identity and pose"),
            ActionBeat(at_seconds=2, phase="prepare", action="She releases the handle"),
            ActionBeat(at_seconds=5, phase="execute", action="She opens the umbrella"),
            ActionBeat(at_seconds=7.5, phase="settle", action="Land on Picture 2 composition"),
        ),
    ),),
    soundscape="Steady rain and fabric movement.", music="N/A",
)
prompt = compile_h3_director_prompt(plan, dialogue=())
assert prompt.startswith("How the reference pictures align")
assert "Picture 2 (from Shot 1) aligns with the 8.00-second mark" in prompt
assert "pulls out with small amplitude at slow speed" in prompt
```

- [ ] **步骤 2：运行测试验证模型与编译器不存在**

运行：`python -m pytest tests/media_capabilities/video/test_h3_director_plan.py tests/media_capabilities/video/test_h3_prompt_compiler.py -q`

预期：FAIL，目标模块不存在。

- [ ] **步骤 3：实现不可变计划 DTO 和确定性编译**

`H3DirectorPlan` 验证镜头连续覆盖总时长、Shot index 递增、动作时间在镜头内；FL2VA 验证最后动作 phase 为 `settle`。编译器生成官方 Picture 对齐首行、三字段、稳定 speaker ID、原文对白与严格两位小数时间。

- [ ] **步骤 4：运行黄金测试和现有 H3 prompt 测试**

运行：`python -m pytest tests/media_capabilities/video/test_h3_director_plan.py tests/media_capabilities/video/test_h3_prompt_compiler.py tests/media_capabilities/video/test_h3_prompt_optimizer.py -q`

预期：全部 PASS；旧测试按 profile 新版本更新，不降低原有身份与终帧约束。

- [ ] **步骤 5：提交**

```bash
git add src/novelvideo/media_capabilities/video/h3_director_plan.py src/novelvideo/media_capabilities/video/h3_prompt_compiler.py src/novelvideo/media_capabilities/video/h3_prompt_profile.py tests/media_capabilities/video/test_h3_director_plan.py tests/media_capabilities/video/test_h3_prompt_compiler.py tests/media_capabilities/video/test_h3_prompt_optimizer.py
git commit -m "feat(h3): add typed director plan compiler"
```

### 任务 6：导演计划生成与付费前质量门禁

**文件：**
- 修改：`src/novelvideo/media_capabilities/video/h3_prompt_optimizer.py`
- 创建：`src/novelvideo/media_capabilities/video/h3_prompt_quality.py`
- 测试：`tests/media_capabilities/video/test_h3_prompt_optimizer.py`
- 测试：`tests/media_capabilities/video/test_h3_prompt_quality.py`

- [ ] **步骤 1：编写计划生成和阻断测试**

```python
@pytest.mark.asyncio
async def test_planner_returns_plan_then_compiles_prompt(tmp_path):
    result = await H3DirectorPlanner(fake_agent, tmp_path).plan_segment(segment, context, H3Mode.I2VA)
    assert result.plan.shots[0].camera.kind == "Push In"
    assert result.prompt.startswith("For the target video, at 0.00 seconds")

def test_quality_gate_rejects_vague_motion():
    report = inspect_h3_prompt(
        plan_with_action("The person moves naturally and the camera slowly moves.")
    )
    assert report.passed is False
    assert "vague_action" in report.codes
```

- [ ] **步骤 2：运行测试验证当前优化器直接生成三段文字**

运行：`python -m pytest tests/media_capabilities/video/test_h3_prompt_optimizer.py tests/media_capabilities/video/test_h3_prompt_quality.py -q`

预期：FAIL；当前 output schema 是三个字符串字段，没有导演计划和质量报告。

- [ ] **步骤 3：改造优化器**

`H3PromptStructuredOutput` 替换为 `H3DirectorPlan`；system/task prompt完整吸收官方 base guide 的首帧锚定、运镜类型/幅度/速度、按时长动作阶段、FL2VA 单镜头和终态收敛规则。生成后依次执行：

```python
plan = H3DirectorPlan.model_validate(response.output)
report = inspect_h3_plan(plan, segment=segment, context=context)
if not report.passed:
    raise H3PromptQualityError(report)
prompt = compile_h3_director_prompt(plan, dialogue=dialogue_cues(segment))
```

生产 factory 改为依赖注入的通用导演文本模型 factory；本模块不得 import `get_newapi_text_pydantic_model`。缓存格式提升并包含 profile/compiler 版本。

- [ ] **步骤 4：运行优化器与质量测试**

运行：`python -m pytest tests/media_capabilities/video/test_h3_prompt_optimizer.py tests/media_capabilities/video/test_h3_prompt_quality.py -q`

预期：全部 PASS；`rg 'get_newapi_text' src/novelvideo/media_capabilities/video/h3_prompt_optimizer.py` 无输出。

- [ ] **步骤 5：提交**

```bash
git add src/novelvideo/media_capabilities/video/h3_prompt_optimizer.py src/novelvideo/media_capabilities/video/h3_prompt_quality.py tests/media_capabilities/video/test_h3_prompt_optimizer.py tests/media_capabilities/video/test_h3_prompt_quality.py
git commit -m "feat(h3): plan and gate director prompts"
```

### 任务 7：接入叙事组 runner 并扩充可追溯 manifest

**文件：**
- 修改：`src/novelvideo/media_capabilities/video/h3_timeline.py`
- 修改：`src/novelvideo/task_backend/runners/narrative_group_video.py`
- 测试：`tests/media_capabilities/video/test_h3_timeline.py`
- 测试：`tests/test_task_narrative_group_video_runner.py`

- [ ] **步骤 1：编写 manifest 与阻断 transport 测试**

```python
assert manifest.entries[0].director_plan is not None
assert manifest.entries[0].segment.prompt == submitted_timeline_prompt
assert manifest.entries[0].prompt_profile == {
    "id": "minimax-h3-director", "version": 4, "compiler_version": 1
}
assert manifest.entries[0].quality_report["passed"] is True

with pytest.raises(H3PromptQualityError):
    run_narrative_group_video(bad_plan_envelope, ctx)
assert runninghub_calls == []
```

- [ ] **步骤 2：运行测试验证 manifest 缺少计划和报告**

运行：`python -m pytest tests/media_capabilities/video/test_h3_timeline.py tests/test_task_narrative_group_video_runner.py -q`

预期：FAIL，manifest entry 没有新增快照字段。

- [ ] **步骤 3：持久化实际提交证据**

扩展 timeline entry 的可选字段：

```python
director_plan: dict[str, JsonValue] | None = None
prompt_profile: dict[str, JsonValue] | None = None
quality_report: dict[str, JsonValue] | None = None
input_summary: dict[str, JsonValue] | None = None
```

runner 使用任务 6 结果替换 segment.prompt，再调用任务 4 的 adapter；写 manifest 时保存最终 prompt、计划、profile/compiler 版本、帧哈希与安全生成参数。旧 manifest 缺字段仍可加载。

- [ ] **步骤 4：运行 runner、timeline 和 H3 runtime 回归**

运行：`python -m pytest tests/media_capabilities/video/test_h3_timeline.py tests/test_task_narrative_group_video_runner.py tests/media_capabilities/video/test_h3_runtime.py -q`

预期：全部 PASS。

- [ ] **步骤 5：提交**

```bash
git add src/novelvideo/media_capabilities/video/h3_timeline.py src/novelvideo/task_backend/runners/narrative_group_video.py tests/media_capabilities/video/test_h3_timeline.py tests/test_task_narrative_group_video_runner.py
git commit -m "feat(h3): persist submitted director prompts"
```

### 任务 8：提供安全的提示词复盘 API

**文件：**
- 修改：`src/novelvideo/api/routes/narrative_groups.py`
- 修改：`src/novelvideo/narrative_groups/service.py`
- 测试：`tests/test_api_narrative_groups.py`

- [ ] **步骤 1：编写复盘 API 红灯测试**

```python
response = client.get(
    "/api/v1/projects/demo/episodes/1/narrative-groups/ng-01/video/prompts"
)
item = response.json()["data"]["units"][0]
assert item["final_prompt"] == manifest.entries[0].segment.prompt
assert item["mode"] == "fl2va"
assert item["director_plan"]
assert item["quality_report"]["passed"] is True
assert "api_key" not in response.text.lower()
assert "authorization" not in response.text.lower()
```

- [ ] **步骤 2：运行测试验证端点不存在**

运行：`python -m pytest tests/test_api_narrative_groups.py -q`

预期：FAIL，返回 404。

- [ ] **步骤 3：实现只读 manifest 投影**

新增 `GET /api/v1/projects/{project}/episodes/{episode}/narrative-groups/{group_id}/video/prompts`，通过当前 stage 的 project-scoped manifest 路径加载数据，返回 Beat、模式、时长、帧 URL、导演计划、最终 prompt、profile、workflow/provider task ID 和质量报告。使用现有 project asset 路径校验；不返回凭据、Authorization、workflow JSON 或服务端绝对秘密路径。

- [ ] **步骤 4：运行 API 测试**

运行：`python -m pytest tests/test_api_narrative_groups.py -q`

预期：全部 PASS。

- [ ] **步骤 5：提交**

```bash
git add src/novelvideo/api/routes/narrative_groups.py src/novelvideo/narrative_groups/service.py tests/test_api_narrative_groups.py
git commit -m "feat(h3): expose safe prompt review API"
```

### 任务 9：在组合视频结果卡展示实际提交提示词

**文件：**
- 修改：`frontend/src/lib/queries/narrative-groups.ts`
- 创建：`frontend/src/components/episode/narrative-workbench/group-video-prompt-drawer.tsx`
- 修改：`frontend/src/components/episode/narrative-workbench/group-video-result.tsx`
- 测试：`frontend/src/__tests__/components/episode/narrative-workbench/group-video-result.test.tsx`
- 测试：`frontend/src/__tests__/components/episode/narrative-workbench/group-video-prompt-drawer.test.tsx`

- [ ] **步骤 1：编写 UI 红灯测试**

```tsx
await user.click(screen.getByRole("button", { name: "生成提示词" }));
expect(await screen.findByText("最终提交提示词")).toBeInTheDocument();
expect(screen.getByText(/integrated_multimodal_description/)).toBeInTheDocument();
expect(screen.getByText("Beat 8 → Beat 9")).toBeInTheDocument();
expect(screen.getByRole("button", { name: "复制最终提示词" })).toBeInTheDocument();
expect(screen.getByRole("link", { name: "下载 manifest" })).toBeInTheDocument();
```

- [ ] **步骤 2：运行测试验证入口不存在**

运行：`frontend/node_modules/.bin/vitest.cmd run frontend/src/__tests__/components/episode/narrative-workbench/group-video-result.test.tsx frontend/src/__tests__/components/episode/narrative-workbench/group-video-prompt-drawer.test.tsx`

预期：FAIL，找不到“生成提示词”按钮。

- [ ] **步骤 3：实现查询和抽屉**

新增 `useNarrativeGroupVideoPrompts(project, episode, groupId, enabled)`。结果卡按钮打开抽屉后才请求数据。抽屉按视频单元展示“原始 Beat 提示词 / 导演计划 / 最终提交提示词”三个清晰区块，并支持 Clipboard API 复制与 manifest 下载。旧 manifest 返回空计划时显示“旧版本无导演计划”，仍展示 final prompt。

- [ ] **步骤 4：运行 UI 测试**

运行：`frontend/node_modules/.bin/vitest.cmd run frontend/src/__tests__/components/episode/narrative-workbench/group-video-result.test.tsx frontend/src/__tests__/components/episode/narrative-workbench/group-video-prompt-drawer.test.tsx frontend/src/__tests__/components/episode/narrative-workbench/narrative-group-workbench-references.test.tsx`

预期：全部 PASS。

- [ ] **步骤 5：提交**

```bash
git add frontend/src/lib/queries/narrative-groups.ts frontend/src/components/episode/narrative-workbench/group-video-prompt-drawer.tsx frontend/src/components/episode/narrative-workbench/group-video-result.tsx frontend/src/__tests__/components/episode/narrative-workbench/group-video-result.test.tsx frontend/src/__tests__/components/episode/narrative-workbench/group-video-prompt-drawer.test.tsx
git commit -m "feat(h3): show submitted prompts in narrative results"
```

### 任务 10：迁移验证、综合回归和浏览器验收

**文件：**
- 修改：仅修复本计划测试暴露的相关文件
- 测试：上述所有目标测试

- [ ] **步骤 1：运行后端目标回归**

```powershell
$sp=(Resolve-Path '.\.venv\Lib\site-packages').Path
$env:PYTHONPATH=($sp+';'+(Join-Path $sp 'win32')+';'+(Join-Path $sp 'win32\lib')+';'+(Join-Path $sp 'pywin32_system32')+';'+(Resolve-Path '.\src').Path)
$env:PATH=((Join-Path $sp 'pywin32_system32')+';'+$env:PATH)
& '.\.python\cpython-3.11.15-windows-x86_64-none\python.exe' -m pytest tests\media_capabilities\video tests\test_api_media_capabilities.py tests\test_api_narrative_groups.py tests\test_project_media_defaults.py tests\test_task_narrative_group_video_runner.py -q
```

预期：全部 PASS；仅允许记录现有第三方 deprecation warnings。

- [ ] **步骤 2：运行前端目标回归**

```powershell
& '.\node_modules\.bin\vitest.cmd' run src/__tests__/lib/queries/media-models.test.ts src/__tests__/components/episode/narrative-workbench --reporter=verbose
```

工作目录：`frontend`。预期：全部 PASS。

- [ ] **步骤 3：检查 NewAPI 与硬编码残留**

```powershell
rg -n 'newapi|mergeVideoModelCatalog|useVideoBackends' frontend/src/components/episode/narrative-workbench frontend/src/lib/queries/media-models.ts src/novelvideo/media_capabilities/video src/novelvideo/task_backend/runners/narrative_group_video.py
rg -n 'Literal\["runninghub:minimax-h3"\]' src/novelvideo/api
```

预期：叙事组视频链路无 NewAPI/legacy 合并，无单模型 Literal；测试夹具中的拒绝案例除外。

- [ ] **步骤 4：启动本地服务并做只读浏览器验收**

在 `http://127.0.0.1:5173/projects/01KZXX36RST1EGBACB7K2555QJ/episodes/1/beats` 验证：

1. 当前只有 H3 时显示固定“RunningHub MiniMax H3”，没有 NewAPI。
2. 保存 NewAPI 的旧项目仍回退 H3。
3. 已完成组合视频出现“生成提示词”按钮。
4. 抽屉显示实际 final prompt、导演计划、质量报告和 manifest 下载。
5. 不点击“生成组合视频”，避免产生付费请求。

- [ ] **步骤 5：运行最终差异检查**

```bash
git diff --check
git status --short
```

预期：`git diff --check` 无输出；本计划各任务均已按任务提交，状态中只保留进入本计划前就存在的无关改动。若步骤 1–4 暴露回归，返回对应任务完成测试、精确暂存和提交后，再重新执行本步骤。

## 完成标准

- 视频模型目录、项目默认值、API 和 runner 都由统一注册表解析。
- 当前 UI 只提供 RunningHub MiniMax H3；注册第二个 RunningHub 工作流后无需修改页面或 API schema 即可切换。
- H3 提示词遵循官方 Picture 对齐与三字段结构，含可验证时间线、运镜、动作桥、对白和 FL2VA 终态收敛。
- 低质量计划在 RunningHub 付费提交前被阻断。
- 用户可在组合视频结果卡查看、复制实际提交提示词并下载 manifest。
- 历史 manifest 与旧 NewAPI 项目配置安全迁移，不破坏视频播放。
