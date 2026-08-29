# DirectorPlan v2 M3 图片、H3 视频与风格快照实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 让 active DirectorPlan 驱动动态宫格图、无白边切分、整集 H3 Prompt Pack、单 VideoSegment 真实生成，以及不可变 StyleSnapshot 全链路投影。

**架构：** StyleResolver 把项目默认和叙事组覆盖冻结成用途投影；GenerationBatch planner 只按组内 Shot 生成 1/2/3/4 格批次；H3 Prompt Pack 整集规划并按 Segment 缓存、局部修复，实际 RunningHub/MiniMax 按 Segment 单独执行。TTS 在有对白 Segment 前置锁时长，组/集预览由本地确定性合成。

**技术栈：** Python 3.11、Pydantic 2、Pillow、pydantic-ai、GRSAI、RunningHub/MiniMax H3、FFmpeg、React 19、pytest、Vitest

---

## 文件结构

- 创建 `src/novelvideo/styles/resolver.py`：结构化风格投影、快照和哈希。
- 修改 `src/novelvideo/services/style_service.py`：返回结构化 extension fragments。
- 修改 `src/novelvideo/extension_styles/schema.py`：稳定投影输入。
- 修改 `src/novelvideo/api/routes/styles.py`：快照预览和风格变更策略 API。
- 修改 `src/novelvideo/director_plan/models.py`：StyleSnapshot、GenerationBatch、VideoSegment。
- 创建 `src/novelvideo/director_plan/generation.py`：从 Shot 生成批次与视频段。
- 修改 `src/novelvideo/media_capabilities/image/grid_plan.py`：1/2/3/4 格布局和尺寸选择。
- 修改 `src/novelvideo/narrative_groups/grid_cleanup.py`：混合分隔线检测与内缩裁切。
- 修改 `src/novelvideo/task_backend/runners/narrative_group.py`：按 GenerationBatch 生成和记录 style hash。
- 创建 `src/novelvideo/media_capabilities/video/h3_episode_pack.py`：整集 Segment 规划与局部修复。
- 修改 `src/novelvideo/media_capabilities/video/h3_prompt_optimizer.py`：复用单 Segment 编译/缓存。
- 修改 `src/novelvideo/task_backend/runners/narrative_group_video.py`：一个 Segment 一个真实任务。
- 修改 `src/novelvideo/task_backend/runners/narrative_group_video_compose.py`：本地确定性组预览。
- 修改 `src/novelvideo/media_capabilities/video/h3_timeline.py`：确定性转场与音频边界。
- 修改 `frontend/src/lib/queries/narrative-groups.ts`：新批次、Segment、StyleSnapshot DTO。
- 修改 `frontend/src/components/episode/narrative-workbench/group-grid-stage.tsx`：动态布局和批次状态。
- 修改 `frontend/src/components/episode/narrative-workbench/group-video-stage.tsx`：Segment 任务与重试。
- 创建 `frontend/src/components/episode/narrative-workbench/style-change-dialog.tsx`：仅换画风/重新导演。
- 修改 `frontend/src/components/episode/narrative-workbench/narrative-group-workbench.tsx`：叙事组覆盖和新流程。
- 创建/修改对应 Python、Vitest 和真实 provider smoke 测试。

### 任务 1：实现不可变 StyleSnapshot 和分层投影

**文件：**
- 创建：`src/novelvideo/styles/resolver.py`
- 修改：`src/novelvideo/services/style_service.py`
- 修改：`src/novelvideo/extension_styles/schema.py`
- 修改：`src/novelvideo/director_plan/models.py`
- 修改：`src/novelvideo/api/routes/styles.py`
- 测试：`tests/test_style_resolver.py`
- 修改：`tests/test_api_styles.py`

- [ ] **步骤 1：编写失败的投影、优先级和稳定哈希测试**

```python
def test_resolver_builds_stable_purpose_specific_snapshot():
    snapshot = StyleResolver(catalog).resolve(project_style="drama_ext.ink", override=None)
    assert snapshot.projections.image
    assert snapshot.projections.video
    assert "negative prompt" not in snapshot.projections.video.lower()
    assert snapshot.style_hash == StyleResolver(catalog).resolve("drama_ext.ink", None).style_hash


def test_explicit_shot_camera_wins_over_style_tendency():
    shot = shot_plan(camera_motion="handheld push-in")
    resolved = apply_director_projection(shot, snapshot_with_camera("static wide shot"))
    assert resolved.camera_motion == "handheld push-in"
```

- [ ] **步骤 2：运行测试确认缺少 resolver**

运行：`uv run pytest tests/test_style_resolver.py tests/test_api_styles.py -q`

预期：FAIL，无法导入 `StyleResolver` 或 API 不含 snapshot preview。

- [ ] **步骤 3：实现 StyleSnapshot**

```python
class StyleProjections(FrozenModel):
    director: str
    image: str
    video: str
    panel_tag: str


class StyleSnapshot(FrozenModel):
    snapshot_id: str
    style_id: str
    style_version: str
    catalog_hash: str
    style_hash: str
    projections: StyleProjections
```

哈希输入使用排序后的 UTF-8 JSON：`style_id`、`style_version`、`catalog_hash`、`projections`。`director` 使用 medium/camera/constraints 中可执行内容；`image` 使用全部六类；`video` 使用 medium/rendering/lighting/color，过滤图片负面词和相机覆盖语句；`panel_tag` 每类最多一个短语。

- [ ] **步骤 4：扩展 Style API**

增加 `GET /styles/{style_id}/snapshot-preview`，返回四种投影、版本和哈希；不返回文件路径或内部 source audit。现有 18 个只读扩展风格、六个原始预设和 UI 分组保持不变。

- [ ] **步骤 5：运行风格测试**

运行：`uv run pytest tests/test_style_resolver.py tests/test_api_styles.py tests/test_extension_style_catalog.py -q`

预期：PASS。

- [ ] **步骤 6：提交**

```bash
git add src/novelvideo/styles/resolver.py src/novelvideo/services/style_service.py src/novelvideo/extension_styles/schema.py src/novelvideo/director_plan/models.py src/novelvideo/api/routes/styles.py tests/test_style_resolver.py tests/test_api_styles.py
git commit -m "feat: freeze purpose-specific style snapshots"
```

### 任务 2：从 Shot 规划 GenerationBatch 和 VideoSegment

**文件：**
- 创建：`src/novelvideo/director_plan/generation.py`
- 修改：`src/novelvideo/director_plan/models.py`
- 测试：`tests/director_plan/test_generation.py`

- [ ] **步骤 1：编写失败的批次和 Segment 规划测试**

```python
@pytest.mark.parametrize(
    ("count", "layouts"),
    [(1, ("single",)), (2, ("diptych",)), (3, ("triptych",)), (4, ("grid_2x2",)), (5, ("triptych", "diptych"))],
)
def test_generation_batches_never_have_blank_cells(count, layouts):
    batches = plan_generation_batches(group_with_shots(count))
    assert tuple(batch.layout for batch in batches) == layouts
    assert sum(len(batch.shot_ids) for batch in batches) == count
    assert all(batch.capacity == len(batch.shot_ids) for batch in batches)


def test_video_segments_default_to_one_shot_and_merge_only_continuous_action():
    segments = plan_video_segments(group_with_continuity_flags(False, True, False))
    assert [segment.shot_ids for segment in segments] == [("s1",), ("s2", "s3"), ("s4",)]
```

- [ ] **步骤 2：运行测试确认模块不存在**

运行：`uv run pytest tests/director_plan/test_generation.py -q`

预期：FAIL，无法导入 generation planner。

- [ ] **步骤 3：实现批次和 Segment 模型**

```python
class GenerationBatch(FrozenModel):
    id: str
    group_id: str
    shot_ids: tuple[str, ...]
    layout: Literal["single", "diptych", "triptych", "grid_2x2"]
    rows: int
    columns: int
    capacity: int
    style_snapshot_id: str


class VideoSegment(FrozenModel):
    id: str
    group_id: str
    shot_ids: tuple[str, ...]
    duration_seconds: float
    continuity_reason: str
    audio_mode: Literal["project_default", "external_tts", "h3_original"]
    style_snapshot_id: str
```

生成函数逐 NarrativeGroup 执行，函数签名不接受跨组 Shot。连续合并必须同时满足同一主体、同一空间、动作连续标记和总时长不超过 15 秒。

- [ ] **步骤 4：运行规划测试**

运行：`uv run pytest tests/director_plan/test_generation.py -q`

预期：PASS。

- [ ] **步骤 5：提交**

```bash
git add src/novelvideo/director_plan/generation.py src/novelvideo/director_plan/models.py tests/director_plan/test_generation.py
git commit -m "feat: separate image batches from video segments"
```

### 任务 3：实现动态宫格尺寸和无白边切分

**文件：**
- 修改：`src/novelvideo/media_capabilities/image/grid_plan.py`
- 修改：`src/novelvideo/narrative_groups/image_resolution.py`
- 修改：`src/novelvideo/narrative_groups/grid_cleanup.py`
- 修改：`src/novelvideo/task_backend/runners/narrative_group.py`
- 修改：`tests/media_capabilities/image/test_grid_plan.py`
- 修改：`tests/test_narrative_group_image_resolution.py`
- 修改：`tests/test_narrative_group_grid_cleanup.py`
- 修改：`tests/test_task_narrative_group_runners.py`

- [ ] **步骤 1：编写失败的 9:16 布局和白边测试**

```python
def test_two_vertical_shots_request_a_two_cell_canvas_not_four_cells():
    plan = build_grid_plan(layout="diptych", cell_aspect="9:16", quality="2k", model="gpt-image-2-vip")
    assert plan.cell_count == 2
    assert plan.output_cells == ((0, 0), (0, 1))
    assert plan.cell_pixel_width >= 1080


def test_splitter_removes_detected_white_separator(tmp_path):
    grid = make_grid_with_white_separator(tmp_path, cells=2, separator=12)
    cells, reports = split_and_cleanup(grid, expected_layout="diptych", target_aspect="9:16")
    assert len(cells) == 2
    assert all(report.remaining_bright_border_ratio < 0.01 for report in reports)
```

- [ ] **步骤 2：运行测试确认旧逻辑固定 2×2 或残留白边**

运行：`uv run pytest tests/media_capabilities/image/test_grid_plan.py tests/test_narrative_group_image_resolution.py tests/test_narrative_group_grid_cleanup.py -q`

预期：FAIL，cell_count/layout 或边缘质量断言失败。

- [ ] **步骤 3：实现布局感知尺寸选择**

`build_grid_plan()` 先根据单格目标画幅和最低像素计算理论画布，再从模型尺寸白名单选择像素充足且面积差最小的尺寸。1K/2K/4K 只影响最低单格像素，不改变 Shot 或 batch 数量。

- [ ] **步骤 4：实现混合切分**

对每条理论分隔线在 ±3% 画布范围内扫描亮度和纹理方差，选择最可能的分隔带中心；未检测到分隔带时使用理论坐标。每格向内裁切 `max(2px, detected_separator_width/2 + 1px)`，按目标画幅裁切，再检测四边亮边并最多二次内缩。输出 `cleanup_reports` 保存理论线、实际线、内缩量和边缘亮度比例。

- [ ] **步骤 5：让 runner 按 batch 执行**

`narrative_group_grid` payload 必须包含 `batch_id`、`shot_ids`、`layout`、`target_cell_aspect`、`style_snapshot_id`。提示词完整 `image` 投影只拼接一次，每格只使用 `panel_tag`；stage manifest 记录 `style_hash`、请求尺寸和实际单格尺寸。

- [ ] **步骤 6：运行图片链路测试**

运行：`uv run pytest tests/media_capabilities/image/test_grid_plan.py tests/test_narrative_group_image_resolution.py tests/test_narrative_group_grid_cleanup.py tests/test_task_narrative_group_runners.py -q`

预期：PASS。

- [ ] **步骤 7：提交**

```bash
git add src/novelvideo/media_capabilities/image/grid_plan.py src/novelvideo/narrative_groups/image_resolution.py src/novelvideo/narrative_groups/grid_cleanup.py src/novelvideo/task_backend/runners/narrative_group.py tests/media_capabilities/image/test_grid_plan.py tests/test_narrative_group_image_resolution.py tests/test_narrative_group_grid_cleanup.py tests/test_task_narrative_group_runners.py
git commit -m "feat: generate and split variable narrative grids"
```

### 任务 4：实现整集 H3 Prompt Pack

**文件：**
- 创建：`src/novelvideo/media_capabilities/video/h3_episode_pack.py`
- 修改：`src/novelvideo/media_capabilities/video/h3_prompt_optimizer.py`
- 测试：`tests/media_capabilities/video/test_h3_episode_pack.py`
- 修改：`tests/media_capabilities/video/test_h3_prompt_optimizer.py`

- [ ] **步骤 1：编写失败的整集单调用和局部修复测试**

```python
@pytest.mark.asyncio
async def test_episode_pack_calls_director_once_then_repairs_only_bad_segment(tmp_path):
    model = FakeEpisodeModel(pack=pack_with_bad_segment("seg-03"), repair=valid_segment("seg-03"))
    result = await H3EpisodePackOptimizer(model, tmp_path).optimize(episode_input())
    assert model.episode_calls == 1
    assert model.repair_calls == ["seg-03"]
    assert all(item.quality_report.passed for item in result.segments)
```

- [ ] **步骤 2：运行测试确认缺少 episode pack**

运行：`uv run pytest tests/media_capabilities/video/test_h3_episode_pack.py -q`

预期：FAIL，无法导入 `H3EpisodePackOptimizer`。

- [ ] **步骤 3：实现整集结构化输出**

```python
class H3EpisodePromptPack(BaseModel):
    episode: int
    director_revision_id: str
    segments: tuple[H3SegmentPromptPlan, ...]


class H3SegmentPromptPlan(BaseModel):
    segment_id: str
    director_plan: H3DirectorPlan
```

整集 agent 使用 `PromptedOutput(H3EpisodePromptPack)` 且不传 `tool_choice`。输入包含全部 VideoSegment、相邻关系、角色/场景锚点和 `StyleSnapshot.video`，不包含图片负面词。

- [ ] **步骤 4：复用现有编译器和质量验证**

每个 `H3DirectorPlan` 仍通过现有 compiler/quality gate 生成最终 prompt。失败 Segment 仅携带自身、前后 Segment 摘要和 issue 修复，最多两次。缓存键包含 `director_revision_id`、`segment_id`、`style_hash`、输入帧 hash 和 prompt compiler version。

- [ ] **步骤 5：运行 H3 pack 测试**

运行：`uv run pytest tests/media_capabilities/video/test_h3_episode_pack.py tests/media_capabilities/video/test_h3_prompt_optimizer.py tests/media_capabilities/video/test_h3_prompt_compiler.py tests/media_capabilities/video/test_h3_prompt_quality.py -q`

预期：PASS。

- [ ] **步骤 6：提交**

```bash
git add src/novelvideo/media_capabilities/video/h3_episode_pack.py src/novelvideo/media_capabilities/video/h3_prompt_optimizer.py tests/media_capabilities/video/test_h3_episode_pack.py tests/media_capabilities/video/test_h3_prompt_optimizer.py
git commit -m "feat: optimize h3 prompts as an episode pack"
```

### 任务 5：按 Segment 真实生成并确定性合成

**文件：**
- 修改：`src/novelvideo/task_backend/runners/narrative_group_video.py`
- 修改：`src/novelvideo/task_backend/runners/narrative_group_video_compose.py`
- 修改：`src/novelvideo/media_capabilities/video/h3_timeline.py`
- 修改：`tests/test_task_narrative_group_video_runner.py`
- 修改：`tests/test_task_narrative_group_video_compose_runner.py`
- 修改：`tests/media_capabilities/video/test_h3_timeline.py`

- [ ] **步骤 1：编写失败的单 Segment 任务与音频时序测试**

```python
@pytest.mark.asyncio
async def test_dialogue_segment_locks_tts_duration_before_video_request():
    result = await run_segment(segment_with_dialogue(), tts=fake_tts(4.2), provider=provider)
    assert provider.requests[0].duration_seconds == 4.2
    assert provider.requests[0].segment_ids == ("seg-01",)


def test_transition_policy_defaults_to_hard_cut_and_uses_audio_lead_for_dialogue():
    assert transition_for("causal", has_leading_dialogue=False).kind == "hard_cut"
    assert transition_for("progressive", has_leading_dialogue=True).audio == "j_cut"
    assert transition_for("time_jump", has_leading_dialogue=False).kind == "dissolve"
```

- [ ] **步骤 2：运行测试确认 runner 仍按整组执行**

运行：`uv run pytest tests/test_task_narrative_group_video_runner.py tests/test_task_narrative_group_video_compose_runner.py tests/media_capabilities/video/test_h3_timeline.py -q`

预期：FAIL，provider request 包含多个 Segment 或 TTS 未前置。

- [ ] **步骤 3：改为一个 Segment 一个 provider 调用**

任务键固定为 `task:narrative_group_video_segment:project:{project}:episode:{episode}:segment:{segment_id}`。每个任务只解析一个 Segment 的帧、H3 prompt 和 audio override。失败只更新该 Segment 状态，不清空同组其他成功结果。

- [ ] **步骤 4：实现音频决策**

项目默认和 Segment override 合并后：有对白默认 `external_tts`，先生成/复用 TTS 并使用真实音频时长；无对白默认 `h3_original`。H3 prompt 固定 `non_diegetic_music=N/A`，保留环境和动作音效。项目 BGM 只在本地整集合成时添加。

- [ ] **步骤 5：实现确定性本地合成**

按 NarrativeGroup ordinal 和 Segment 顺序拼接。默认硬切；`time_jump` 使用 8 帧叠化；`montage` 仍硬切但按 Segment 时长节拍排列；有先入对白使用 300ms J-cut，有延续环境声使用最多 500ms L-cut。输出 manifest 记录每个规则来源。

- [ ] **步骤 6：运行视频 runner 测试**

运行：`uv run pytest tests/test_task_narrative_group_video_runner.py tests/test_task_narrative_group_video_compose_runner.py tests/media_capabilities/video/test_h3_timeline.py -q`

预期：PASS。

- [ ] **步骤 7：提交**

```bash
git add src/novelvideo/task_backend/runners/narrative_group_video.py src/novelvideo/task_backend/runners/narrative_group_video_compose.py src/novelvideo/media_capabilities/video/h3_timeline.py tests/test_task_narrative_group_video_runner.py tests/test_task_narrative_group_video_compose_runner.py tests/media_capabilities/video/test_h3_timeline.py
git commit -m "feat: generate h3 video per segment and compose locally"
```

### 任务 6：更新叙事组生产 UI 和风格变更路径

**文件：**
- 修改：`frontend/src/lib/queries/narrative-groups.ts`
- 修改：`frontend/src/components/episode/narrative-workbench/group-grid-stage.tsx`
- 修改：`frontend/src/components/episode/narrative-workbench/group-video-stage.tsx`
- 创建：`frontend/src/components/episode/narrative-workbench/style-change-dialog.tsx`
- 修改：`frontend/src/components/episode/narrative-workbench/narrative-group-workbench.tsx`
- 修改：`frontend/src/__tests__/lib/queries/narrative-groups.test.ts`
- 修改：`frontend/src/__tests__/components/episode/narrative-workbench/group-video-stage.test.tsx`
- 创建：`frontend/src/__tests__/components/episode/narrative-workbench/group-grid-stage-v2.test.tsx`
- 创建：`frontend/src/__tests__/components/episode/narrative-workbench/style-change-dialog.test.tsx`

- [ ] **步骤 1：编写失败的动态布局、Segment 重试和风格分支测试**

```tsx
it("shows a diptych batch for two shots and retries only one failed segment", async () => {
  render(<NarrativeGroupWorkbench project="p" episode={1} />)
  expect(await screen.findByText("二联画 · 2 个镜头")).toBeVisible()
  await user.click(screen.getByRole("button", { name: "重试片段 seg-02" }))
  expect(requests.at(-1)?.body).toMatchObject({ segment_id: "seg-02" })
})


it("offers restyle or redirection when changing style", async () => {
  render(<StyleChangeDialog />)
  expect(screen.getByRole("button", { name: "仅换画风" })).toBeVisible()
  expect(screen.getByRole("button", { name: "按新风格重新导演" })).toBeVisible()
})
```

- [ ] **步骤 2：运行测试确认旧 UI 不识别 batch/segment**

运行：`corepack pnpm --dir frontend test -- group-grid-stage-v2.test.tsx group-video-stage.test.tsx style-change-dialog.test.tsx narrative-groups.test.ts`

预期：FAIL，找不到布局或单 Segment 重试控件。

- [ ] **步骤 3：更新 DTO 和生成状态 UI**

NarrativeGroup DTO 增加 `generation_batches`、`video_segments`、`effective_style_snapshot`。宫格区按 batch 展示真实布局、模型、分辨率、style hash 和切分报告；视频区按 Segment 展示 prompt、音频模式、provider 状态、独立重试和本地合成状态。

- [ ] **步骤 4：实现风格修改对话框**

“仅换画风”调用 restyle API，使不同 style hash 的图片/视频失效但保留 groups/shots/segments；“按新风格重新导演”创建新的 DirectorPlan task。叙事组允许显式覆盖并提供“恢复项目默认”。

- [ ] **步骤 5：运行前端测试和 build**

运行：`corepack pnpm --dir frontend test -- group-grid-stage-v2.test.tsx group-video-stage.test.tsx style-change-dialog.test.tsx narrative-groups.test.ts`

预期：PASS。

运行：`corepack pnpm --dir frontend build`

预期：通过 TypeScript、Vite 和 bundle budget。

- [ ] **步骤 6：提交**

```bash
git add frontend/src/lib/queries/narrative-groups.ts frontend/src/components/episode/narrative-workbench frontend/src/__tests__/lib/queries/narrative-groups.test.ts frontend/src/__tests__/components/episode/narrative-workbench
git commit -m "feat: operate narrative batches and video segments"
```

### 任务 7：执行真实 provider smoke 和最终门禁

**文件：**
- 创建：`tests/scripts/test_smoke_director_plan_v2.py`
- 创建：`tests/scripts/test_smoke_director_plan_v2_providers.py`
- 修改：`docs/operations/real-provider-smoke.md`

- [ ] **步骤 1：实现不花费 provider 配额的本地 smoke**

脚本读取一个最小两组、三 Shot fixture，执行 DirectorPlan 验证、1+2 GenerationBatch 规划、宫格切分 fixture、H3 Prompt Pack 编译和本地 Segment 合成。运行：

`uv run python tests/scripts/test_smoke_director_plan_v2.py`

预期输出：`DIRECTOR_PLAN_V2_LOCAL_SMOKE_OK groups=2 batches=2 segments=3`。

- [ ] **步骤 2：实现显式授权的真实 provider smoke**

真实脚本必须要求 `--confirm-cost`，并检查 DeepSeek、GRSAI、RunningHub 配置后才执行：一集极小剧本整集规划；一张 2 格 9:16 草图；一个 3～5 秒 H3 Segment。不得 fallback 到 mock。

运行：`uv run python tests/scripts/test_smoke_director_plan_v2_providers.py --confirm-cost`

预期：输出三个真实 request ID、产物绝对路径和 `DIRECTOR_PLAN_V2_PROVIDER_SMOKE_OK`；任一 provider 失败则退出码非零并打印 provider、status、content-type 和脱敏响应摘要。

- [ ] **步骤 3：运行后端定向总门禁**

运行：`uv run pytest tests/director_plan tests/test_style_resolver.py tests/media_capabilities/image/test_grid_plan.py tests/test_narrative_group_grid_cleanup.py tests/media_capabilities/video/test_h3_episode_pack.py tests/media_capabilities/video/test_h3_prompt_optimizer.py tests/test_task_narrative_group_video_runner.py tests/test_task_narrative_group_video_compose_runner.py -q`

预期：PASS。

运行：`uv run ruff check src/novelvideo/director_plan src/novelvideo/styles/resolver.py src/novelvideo/media_capabilities/video/h3_episode_pack.py`

预期：`All checks passed!`

- [ ] **步骤 4：运行前端定向总门禁**

运行：`corepack pnpm --dir frontend test -- narrative-groups.test.ts group-grid-stage-v2.test.tsx group-video-stage.test.tsx style-change-dialog.test.tsx`

预期：PASS。

运行：`corepack pnpm --dir frontend build`

预期：PASS。

- [ ] **步骤 5：记录真实 smoke 证据并提交**

在 `docs/operations/real-provider-smoke.md` 记录执行时间、代码 revision、provider request ID、脱敏模型名、输出路径和结果，不记录 API key。

```bash
git add tests/scripts/test_smoke_director_plan_v2.py tests/scripts/test_smoke_director_plan_v2_providers.py docs/operations/real-provider-smoke.md
git commit -m "test: add director plan v2 provider smoke"
```

## M3 完成条件

- 1/2/3/4/5 个 Shot 分别按 single/diptych/triptych/2×2/3+2 生成，无空格。
- 9:16 切分结果无可见白边，1K/2K/4K 不改变分镜结构。
- H3 正常路径整集只调用一次 prompt planner，失败 Segment 局部修复。
- 每个 VideoSegment 独立真实调用 RunningHub/MiniMax。
- 有对白 Segment 先锁定 TTS 时长，无对白默认 H3 原声。
- StyleSnapshot 在 revision 内不可变，Shot 明确镜头优先于风格倾向。
- 真实 DeepSeek、GRSAI、RunningHub/MiniMax smoke 全部通过后才可宣称生产链路完成。
