# MiniMax H3 导演台工作流实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 将单镜/多镜 H3 统一迁移到 Director 工作流，一次生成多镜 VideoSpan，并以官方范式中文提示词、Director manifest、音频拆轨和逐镜对白策略驱动最终合成。

**架构：** 共享 `h3_timeline` 模型编译节点 12 的 `timeline_data` 并持久化输出清单；H3 prompt optimizer、RunningHub runtime、音频 stems 和前端组任务围绕该契约独立实现。合成器按 VideoSpan 而非 Beat 视频数工作，manifest 是视频、配音、字幕和导出的唯一时间权威。

**技术栈：** Python 3.11、FastAPI、Pydantic、pytest/pytest-asyncio、RunningHub、FFmpeg、可配置 Demucs CLI、React 19、TypeScript、TanStack Query、Vitest。

---

## 文件结构

- 创建 `src/novelvideo/media_capabilities/video/h3_timeline.py`：segment、帧网格、timeline_data、manifest 模型与原子存储。
- 创建 `src/novelvideo/media_capabilities/video/h3_prompt_optimizer.py`：H3 专用优化、校验、缓存与 fail-closed 错误。
- 创建 `src/novelvideo/media_capabilities/audio/stem_separator.py`：可配置 Demucs 执行与 stem 结果。
- 创建 `src/novelvideo/task_backend/runners/narrative_group_video.py`：单次多镜任务、落盘、拆轨与组状态。
- 修改 `media_capabilities/video/runtime.py`、profile/fixtures：Director runtime 与节点 12/7 契约。
- 修改 `task_backend/runners/video.py`：单镜 wrapper 与 manifest-aware compose。
- 修改 `narrative_groups/*`、API schemas/routes：组视频任务及 VideoSpan 结果。
- 修改 `export/episode_export.py`、`utils/path_resolver.py`、episode API：manifest 驱动字幕/导出。
- 修改 narrative-workbench 前端：一次组任务、提示词/stem 状态、组合视频展示。

## 并行执行图

- Phase 1（串行）：任务 1。
- Phase 2（并行）：任务 2、3、4。
- Phase 3（串行集成）：任务 5。
- Phase 4（并行）：任务 6、7、8。
- Phase 5（串行）：任务 9。

### 任务 1：Director 时间线与输出清单契约

**文件：**
- 创建：`src/novelvideo/media_capabilities/video/h3_timeline.py`
- 修改：`src/novelvideo/utils/path_resolver.py`
- 测试：`tests/media_capabilities/video/test_h3_timeline.py`

- [ ] **步骤 1：写失败测试**，覆盖 `frames_for_duration(5,24)==124`、三镜累计 start/total、缺首帧产品校验、单物理视频覆盖多个 entries、manifest 原子往返。
- [ ] **步骤 2：运行** `uv run pytest tests/media_capabilities/video/test_h3_timeline.py -q`，预期因模块不存在而 FAIL。
- [ ] **步骤 3：实现最小契约**：

```python
class DialogueSource(StrEnum):
    EXTERNAL_TTS = "external_tts"
    H3_NATIVE = "h3_native"

class H3DirectorSegment(BaseModel):
    segment_id: str
    beat_number: int
    prompt: str
    duration_seconds: float
    first_frame: str | None
    last_frame: str | None = None
    dialogue: str = ""
    dialogue_source: DialogueSource = DialogueSource.EXTERNAL_TTS

def frames_for_duration(seconds: float, fps: int = 24) -> int: ...
def build_h3_timeline_data(segments: Sequence[H3DirectorSegment]) -> H3CompiledTimeline: ...
def save_director_manifest(path: Path, manifest: H3DirectorOutputManifest) -> None: ...
```

- [ ] **步骤 4：增加** `director_video(group_id, revision)`、`director_manifest(...)`、stem 路径方法并运行测试，预期 PASS。
- [ ] **步骤 5：Commit** `feat(h3): add director timeline and manifest contract`。

### 任务 2：H3 官方范式中文提示词优化器

**文件：**
- 创建：`src/novelvideo/media_capabilities/video/h3_prompt_optimizer.py`
- 修改：`src/novelvideo/media_capabilities/video/h3_prompt.py`
- 修改：`src/novelvideo/official_defaults.py`
- 测试：`tests/media_capabilities/video/test_h3_prompt_optimizer.py`
- 测试：`tests/test_superpower_model_config.py`

- [ ] **步骤 1：写失败测试**：I2VA/FL2VA 对齐声明、三个字段顺序、中文主体、准确对白/说话人/时间、缓存失效、模型异常不回退 draft。
- [ ] **步骤 2：运行聚焦测试**，预期 FAIL。
- [ ] **步骤 3：实现 typed optimizer**：模型只返回结构字段，程序确定性渲染固定标签；`optimize_segment(...) -> H3PromptOptimizationResult`，输入哈希包含草稿、帧 SHA256、实际时长、上下文、模型及 format version。
- [ ] **步骤 4：加入结构校验**；对白 Beat 缺少可辨识对白即抛 `H3PromptOptimizationError`。
- [ ] **步骤 5：运行** `uv run pytest tests/media_capabilities/video/test_h3_prompt.py tests/media_capabilities/video/test_h3_prompt_optimizer.py tests/test_superpower_model_config.py -q`，预期 PASS。
- [ ] **步骤 6：Commit** `feat(h3): optimize prompts with official director format`。

### 任务 3：工作流 Profile 与 Director Runtime

**文件：**
- 修改：`src/novelvideo/media_capabilities/video/profiles/minimax_h3.json`
- 修改：`src/novelvideo/media_capabilities/models.py`
- 修改：`src/novelvideo/media_capabilities/video/runtime.py`
- 修改：`tests/fixtures/runninghub/minimax_h3_video_profile.json`
- 修改：`tests/fixtures/runninghub/minimax_h3_api.json`
- 测试：`tests/media_capabilities/video/test_h3_workflow.py`
- 测试：`tests/media_capabilities/video/test_h3_runtime.py`

- [ ] **步骤 1：写失败测试**，精确断言 Workflow `2089723723468328961`、节点 `12.timeline_data`、输出节点 `7`、三镜只调用一次 submit。
- [ ] **步骤 2：运行测试确认旧节点 114/136 导致 FAIL**。
- [ ] **步骤 3：替换 profile/fixture，并实现** `generate_h3_director_video(*, segments, output_path)`；旧 `generate_h3_video` 仅包装一个 segment。
- [ ] **步骤 4：移除/改造 `runninghub_h3.py` 与 legacy generator 的旧节点直连，确保不存在绕过路径。
- [ ] **步骤 5：运行相关 workflow/runtime/executor 测试，预期 PASS。
- [ ] **步骤 6：Commit** `feat(h3): migrate runtime to director workflow`。

### 任务 4：音频拆轨能力

**文件：**
- 创建：`src/novelvideo/media_capabilities/audio/stem_separator.py`
- 修改：`pyproject.toml`
- 测试：`tests/media_capabilities/audio/test_stem_separator.py`

- [ ] **步骤 1：写失败测试**：安全参数列表、超时/取消、输出 vocals/no_vocals 映射、坏输出 fail closed、原音轨不删除。
- [ ] **步骤 2：运行测试确认 FAIL**。
- [ ] **步骤 3：实现 `DemucsStemSeparator.separate(input_path, output_dir)`**；二进制由 `DRAMACLAW_DEMUCS_BIN` 或受控默认值解析，不使用 shell 字符串。
- [ ] **步骤 4：将 Demucs 放入可选 `audio-separation` 依赖组；缺少能力时返回明确 unavailable，不静默降级叠音。
- [ ] **步骤 5：运行聚焦测试与 ruff，预期 PASS。
- [ ] **步骤 6：Commit** `feat(audio): add dialogue stem separation capability`。

### 任务 5：组级一次性 Director 任务

**文件：**
- 创建：`src/novelvideo/task_backend/runners/narrative_group_video.py`
- 修改：`src/novelvideo/narrative_groups/models.py`
- 修改：`src/novelvideo/narrative_groups/service.py`
- 修改：`src/novelvideo/api/routes/narrative_groups.py`
- 修改：`src/novelvideo/task_backend/run_core.py`
- 测试：`tests/test_task_narrative_group_video_runner.py`
- 测试：`tests/test_api_narrative_groups.py`

- [ ] **步骤 1：写失败测试**：一个组 N beats 只 submit 一次、并行优化缺失/过期 prompts、顺序冻结、manifest+stems 原子登记、revision 过期不覆盖。
- [ ] **步骤 2：运行测试确认 task type/route 不存在。
- [ ] **步骤 3：新增** `POST .../narrative-groups/{group_id}/video/generate`，payload 只含稳定 IDs/模型/模式/revision；runner 重新解析 canonical 资产。
- [ ] **步骤 4：实现状态机 queued→running→completed/failed；供应商任务原子，提示词优化用受限 `asyncio.gather` 并行。
- [ ] **步骤 5：运行 API/runner/registry 测试，预期 PASS。
- [ ] **步骤 6：Commit** `feat(h3): generate narrative group in one director task`。

### 任务 6：单镜统一与防绕过

**文件：**
- 修改：`src/novelvideo/task_backend/runners/video.py`
- 修改：`src/novelvideo/generators/video_generator.py`
- 测试：`tests/test_task_video_runner_h3.py`
- 测试：`tests/test_runninghub_h3_video_generator.py`

- [ ] **步骤 1：写失败测试**：单镜包装一个 segment；optimizer 失败时 runtime 零调用；自由 draft 不直传。
- [ ] **步骤 2：实现 single_video H3 fail-closed 门控并调用统一 Director runtime。
- [ ] **步骤 3：运行测试，预期 PASS。
- [ ] **步骤 4：Commit** `refactor(h3): route single videos through director runtime`。

### 任务 7：VideoSpan 合成、字幕与导出

**文件：**
- 修改：`src/novelvideo/task_backend/runners/video.py`
- 修改：`src/novelvideo/export/episode_export.py`
- 修改：`src/novelvideo/api/routes/generation.py`
- 修改：`src/novelvideo/api/routes/episodes.py`
- 测试：`tests/test_compose_episode_h3_director.py`
- 测试：`tests/test_episode_export_h3_director.py`

- [ ] **步骤 1：写失败测试**：三镜一个视频只入轨一次、旧 beat 视频不重复、逐 entry native/external 混音、静默镜头推进时间、SRT/ZIP 共用 manifest。
- [ ] **步骤 2：抽出** `resolve_episode_composition_sources(...) -> list[VideoSpan]`，Director 覆盖优先、legacy fallback。
- [ ] **步骤 3：构造完整 episode 音轨；external 使用 ambience+TTS，native 使用 original slice；缺 stem 时 external fail closed。
- [ ] **步骤 4：修复 SRT 静默 Beat 不推进时间的问题并导出 manifest/stems。
- [ ] **步骤 5：运行 compose/export contract，预期 PASS。
- [ ] **步骤 6：Commit** `feat(compose): support multi-shot director spans`。

### 任务 8：前端组视频一次提交与结果展示

**文件：**
- 修改：`frontend/src/lib/queries/narrative-groups.ts`
- 修改：`frontend/src/components/episode/narrative-workbench/narrative-group-workbench.tsx`
- 修改：`frontend/src/components/episode/narrative-workbench/group-video-stage.tsx`
- 新增：`frontend/src/components/episode/narrative-workbench/group-video-result.tsx`
- 测试：对应 `frontend/src/__tests__/components/episode/narrative-workbench/*`

- [ ] **步骤 1：写失败测试**：按钮只发一次 group request、只追踪一个 scope、展示 prompt/stem 状态、组合 `<video>`、逐镜 dialogue source。
- [ ] **步骤 2：新增 hook 与 DTO；删除 `Promise.allSettled` 单 Beat fan-out 和 N 个 tracker。
- [ ] **步骤 3：实现 queued/running/failed/completed 与 VideoSpan 预览；切换 dialogue source 后只触发重新合成，不重生视频。
- [ ] **步骤 4：运行** `pnpm test -- narrative-workbench` 与 `pnpm build`，预期 PASS。
- [ ] **步骤 5：Commit** `feat(ui): add director group video workflow`。

### 任务 9：迁移、全量验证与文档

**文件：**
- 修改：`scripts/smoke_runninghub_h3.py`
- 修改：`.env.example`
- 修改：相关设置 UI 测试与运行文档。

- [ ] **步骤 1：旧 Workflow ID/profile 检测给出明确迁移错误，不静默覆盖用户配置。
- [ ] **步骤 2：更新 smoke 脚本到节点 12/7，记录镜头数、总帧数、task ID，不打印密钥。
- [ ] **步骤 3：运行后端聚焦测试、前端测试/build、`uv run pytest -q`、`uv run ruff check`、`git diff --check`、gitleaks。
- [ ] **步骤 4：检查工作区，只提交本功能文件，不纳入用户原有修改。
- [ ] **步骤 5：Commit** `chore(h3): complete director workflow migration`。

## 计划自检

- 规格覆盖：工作流、提示词、单/多镜、manifest、VideoSpan、拆轨、两种对白、字幕、导出、迁移均有任务。
- 类型一致：全链路统一使用 `H3DirectorSegment`、`H3DirectorOutputManifest`、`DialogueSource`、`VideoSpan`。
- 并发边界：仅提示词优化、独立测试与互不共享文件的 Phase 2/4 并行；RunningHub 多镜本身仍只 submit 一次。
