# Higgsfield 刚性镜头提示词与灯光能力实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法跟踪进度。

**目标：** 保留 MiniMax H3 外层 wire 格式，把新版付费生成路径升级为固定十五段镜头协议，并让灯光成为强类型、可推导、可编译、可校验的一级能力。

**架构：** `H3DirectorPlan` 增加向后兼容的 `schema_version` 与可选 `H3RigidPromptPlan`；旧计划保持 v1 可读和可重放，新优化任务必须生成 v2。编译器按版本分派，v2 在 `integrated_multimodal_description` 内确定性编译十五段；共享 quality gate 在所有付费调用前拒绝不完整或矛盾的 v2 计划。

**技术栈：** Python 3.11、Pydantic v2、pydantic-ai、pytest。

---

## 1. 文件结构

- 创建 `src/novelvideo/media_capabilities/video/h3_rigid_prompt.py`：十五段补充契约及递归“只补空字段”纯函数。
- 修改 `src/novelvideo/media_capabilities/video/h3_director_plan.py`：v1/v2 版本与 `rigid_prompt` 入口、对白声音描述。
- 修改 `src/novelvideo/media_capabilities/video/h3_prompt_compiler.py`：v1 兼容分支、v2 十五段编译。
- 修改 `src/novelvideo/media_capabilities/video/h3_prompt_quality.py`：v2 语义质量门。
- 修改 `src/novelvideo/media_capabilities/video/h3_prompt_profile.py`：要求 v2 和固定协议。
- 修改 `src/novelvideo/media_capabilities/video/h3_prompt_optimizer.py`：上下文事实、缓存版本、修订合并和失败关闭。
- 修改 `src/novelvideo/media_capabilities/video/h3_episode_pack.py`：整集任务事实、质量版本缓存键与修订。
- 修改 `src/novelvideo/task_backend/runners/narrative_group_video.py`：传递 Style Prefix 和已有连续性事实。
- 修改 `src/novelvideo/task_backend/runners/video.py`：移除单 Beat 裸 draft 绕过。
- 修改对应定向测试；不修改当前已有未提交变更的 `pipeline.py` 与 `test_pipeline.py`。

## 2. 任务分解

### 任务 1：建立 v2 刚性提示词领域契约

**文件：**

- 创建：`src/novelvideo/media_capabilities/video/h3_rigid_prompt.py`
- 修改：`src/novelvideo/media_capabilities/video/h3_director_plan.py`
- 测试：`tests/media_capabilities/video/test_h3_director_plan.py`

- [ ] **步骤 1：先写失败测试**

测试应证明：旧 JSON 默认解析为 v1 且原字段不丢；v2 可承载精确人物、引用、空间地图、首帧调度、格式、光学、物理、灯光、表演、风格、质量和正向约束；灯光字段冻结并禁止额外字段；递归补空只把 `None`、纯空白和空容器视为空，不覆盖 `0`、`False` 或非空嵌套值。

```python
def test_legacy_plan_remains_v1_and_round_trips():
    plan = H3DirectorPlan.model_validate(legacy_payload())
    assert plan.schema_version == 1
    assert plan.rigid_prompt is None
    assert plan.model_dump()["visual_style"] == "cinematic realism"


def test_fill_empty_preserves_existing_lighting_fields():
    merged = fill_empty_fields(
        {"source_logic": "window daylight", "catchlight": ""},
        {"source_logic": "overhead lamp", "catchlight": "small eye reflection"},
    )
    assert merged == {
        "source_logic": "window daylight",
        "catchlight": "small eye reflection",
    }
```

- [ ] **步骤 2：运行红灯**

```bash
uv run pytest tests/media_capabilities/video/test_h3_director_plan.py -q
```

预期：因 `H3RigidPromptPlan`、`schema_version` 或 `fill_empty_fields` 不存在而 FAIL。

- [ ] **步骤 3：最小实现强类型契约**

实现以下稳定类型，统一 `ConfigDict(frozen=True, extra="forbid")`：

```python
class H3SceneContextPlan(BaseModel):
    exact_character_count: int = Field(ge=0)
    active_characters: tuple[str, ...]
    summary: str = Field(min_length=1)

class H3ActiveReference(BaseModel):
    tag: str = Field(pattern=r"^@[A-Za-z0-9][A-Za-z0-9_.-]*$")
    kind: Literal["character", "location"]
    role: str = Field(min_length=1)
    inherit: str = Field(min_length=1)
    exclude: str = Field(min_length=1)

class H3LightingPlan(BaseModel):
    source_logic: str = Field(min_length=1)
    primary_source: str = Field(min_length=1)
    origin: str = Field(min_length=1)
    direction: str = Field(min_length=1)
    shadow_direction: str = Field(min_length=1)
    quality: str = Field(min_length=1)
    color: str = Field(min_length=1)
    subject_effect: str = Field(min_length=1)
    environment_effect: str = Field(min_length=1)
    fill_logic: str = Field(min_length=1)
    catchlight: str = Field(min_length=1)
    contact_shadows: str = Field(min_length=1)
    continuity_key: str = Field(min_length=1)
```

`H3RigidPromptPlan` 组合所有章节事实；`CAMERA`、`ACTION TIMING` 和对白复用现有 typed plan。`H3DirectorPlan` 增加 `schema_version: Literal[1, 2] = 1` 和 `rigid_prompt: H3RigidPromptPlan | None = None`。v1 不接收 `rigid_prompt`；v2 缺失补充对象交给 quality gate 报告，不在顶层 Pydantic validator 提前吞掉结构化质量错误。

- [ ] **步骤 4：运行绿灯并做兼容回归**

```bash
uv run pytest tests/media_capabilities/video/test_h3_director_plan.py tests/shot_continuity/test_compiler.py -q
```

预期：PASS，旧式构造保持可用。

### 任务 2：确定性编译十五段正文

**文件：**

- 修改：`src/novelvideo/media_capabilities/video/h3_prompt_compiler.py`
- 测试：`tests/media_capabilities/video/test_h3_prompt_compiler.py`

- [ ] **步骤 1：先写 v2 golden 失败测试**

构造一个最小合法 v2 plan，断言外层 Picture alignment 和三个 wire 字段不变；正文十五个标题精确出现一次且严格有序；人物首行是 `EXACT N CHARACTERS — NO DUPLICATES`；对白只在 `AUDIO`；外层音乐固定为 `No music. SFX only.`；`STYLE` 使用计划中的原样 Style Prefix。

```python
EXPECTED_SECTIONS = (
    "SCENE CONTEXT", "ACTIVE REFERENCES", "LOCATION MAP",
    "FIRST FRAME AND SPATIAL BLOCKING", "FORMAT MODE", "OPTICS",
    "CAMERA", "ACTION TIMING", "PHYSICS", "LIGHTING", "AUDIO",
    "CHARACTER ACTING", "STYLE", "QUALITY", "POSITIVE CONSTRAINTS",
)

def test_v2_compiles_rigid_sections_in_exact_order():
    prompt = compile_h3_director_plan(rigid_plan())
    positions = [prompt.index(f"\n\n{title}\n") for title in EXPECTED_SECTIONS]
    assert positions == sorted(positions)
    assert prompt.endswith("non_diegetic_music: No music. SFX only.")
```

- [ ] **步骤 2：运行红灯**

```bash
uv run pytest tests/media_capabilities/video/test_h3_prompt_compiler.py -q
```

预期：v2 golden FAIL，既有 v1 测试仍 PASS。

- [ ] **步骤 3：实现 v1/v2 分派**

保留现有 `_compile_description_v1`。新增 `H3_RIGID_SECTION_ORDER` 和 `_compile_description_v2`，按稳定 shot ID 输出每个镜头的 blocking、optics、camera 和 action timing。`AUDIO` 组合环境声、声音描述和逐字对白 marker；v2 编译器无条件输出外层 `No music. SFX only.`，但 quality gate 仍负责在编译前报告计划中的配乐冲突。

- [ ] **步骤 4：运行绿灯**

```bash
uv run pytest tests/media_capabilities/video/test_h3_prompt_compiler.py -q
```

预期：PASS。

### 任务 3：让单段与整集优化器生成 v2 并保留权威事实

**文件：**

- 修改：`src/novelvideo/media_capabilities/video/h3_prompt_profile.py`
- 修改：`src/novelvideo/media_capabilities/video/h3_prompt_optimizer.py`
- 修改：`src/novelvideo/media_capabilities/video/h3_episode_pack.py`
- 修改：`src/novelvideo/task_backend/runners/narrative_group_video.py`
- 测试：`tests/media_capabilities/video/test_h3_prompt_optimizer.py`
- 测试：`tests/media_capabilities/video/test_h3_episode_pack.py`

- [ ] **步骤 1：先写失败测试**

断言 profile 和单段/整集 task 明确要求 `schema_version=2`、十五段、单一动机光逻辑、SFX only、项目风格原样继承；整集 `_prompt_segment` 不再丢失 continuity JSON、风险、Style Prefix 或 resolved reference tags；缓存键包含新 profile/compiler/quality/format 版本。

再测试质量修订候选通过 `fill_empty_fields(previous, candidate)` 合并：已有非空刚性事实不被重写，只有空字段由候选补齐；`shot_id` 规范化和 action timeline mechanical normalization 保持现有例外行为。

- [ ] **步骤 2：运行红灯**

```bash
uv run pytest tests/media_capabilities/video/test_h3_prompt_optimizer.py tests/media_capabilities/video/test_h3_episode_pack.py -q
```

预期：新断言 FAIL。

- [ ] **步骤 3：最小实现上下文与版本升级**

`H3PromptContext` 增加具有空默认值的 `style_prefix`、`active_character_ids`、`resolved_reference_tags` 和 `lighting_facts_json`。runner 从当前 Style Snapshot 的 video projection、连续性 contracts 和 Director World 中填充已有事实；缺失事实继续由模型补空，不制造 `unknown` 占位符。

提升 `H3_PROMPT_PROFILE_VERSION`、`H3_PROMPT_COMPILER_VERSION`、`H3_PROMPT_QUALITY_VERSION`、单段 `_FORMAT_VERSION` 和整集 `_PACK_FORMAT_VERSION`；把 quality version 加入 episode cache hash。初次生成和 repair 都要求完整 v2；repair 合并后再进入 gate。

- [ ] **步骤 4：运行绿灯**

```bash
uv run pytest tests/media_capabilities/video/test_h3_prompt_optimizer.py tests/media_capabilities/video/test_h3_episode_pack.py -q
```

预期：PASS。

### 任务 4：增加 v2 质量门并封闭付费绕过

**文件：**

- 修改：`src/novelvideo/media_capabilities/video/h3_prompt_quality.py`
- 修改：`src/novelvideo/task_backend/runners/video.py`
- 测试：`tests/media_capabilities/video/test_h3_prompt_quality.py`
- 测试：`tests/test_task_video_runner_h3.py`
- 复用：`tests/test_task_narrative_group_video_runner.py`

- [ ] **步骤 1：先写表驱动失败测试**

从一个合法 v2 baseline 分别突变并断言稳定错误码：

```python
@pytest.mark.parametrize(("mutation", "code"), [
    ("legacy_protocol", "rigid_prompt_required"),
    ("character_count", "character_count_mismatch"),
    ("duplicate_reference", "duplicate_active_reference"),
    ("unresolved_reference", "unresolved_active_reference"),
    ("blocking_coverage", "first_frame_character_missing"),
    ("lighting_conflict", "lighting_source_conflict"),
    ("music", "non_diegetic_music_forbidden"),
    ("acting_coverage", "character_acting_missing"),
    ("style", "style_prefix_mismatch"),
])
def test_v2_quality_gate_reports_stable_issue_codes(mutation, code): ...
```

增加单 Beat runner 测试：优化器 unavailable 或质量失败时 `runtime_calls == []`，不得回退到裸 draft。复用叙事组已有“质量失败时 adapter 零调用”的测试，不新增真实 RunningHub 调用。

- [ ] **步骤 2：运行红灯**

```bash
uv run pytest tests/media_capabilities/video/test_h3_prompt_quality.py tests/test_task_video_runner_h3.py -q
```

预期：新 gate 与失败关闭断言 FAIL。

- [ ] **步骤 3：实现中央质量门和失败关闭**

`inspect_h3_plan` 在存在 strict context 时拒绝 v1；逐项校验人物集合、引用标签、首帧覆盖、format/optics、动作物理、灯光来源与 `lighting_facts_json`、对白、表演覆盖、Style Prefix、正向约束和无配乐。单 Beat optimizer unavailable 不再提交 draft，直接返回结构化失败；Narrative Group 继续使用现有 fail-closed 顺序。

- [ ] **步骤 4：运行定向绿灯**

```bash
uv run pytest \
  tests/media_capabilities/video/test_h3_director_plan.py \
  tests/media_capabilities/video/test_h3_prompt_compiler.py \
  tests/media_capabilities/video/test_h3_prompt_optimizer.py \
  tests/media_capabilities/video/test_h3_episode_pack.py \
  tests/media_capabilities/video/test_h3_prompt_quality.py \
  tests/test_task_video_runner_h3.py \
  tests/test_task_narrative_group_video_runner.py \
  tests/shot_continuity/test_compiler.py -q
```

预期：全部 PASS；不运行全仓库测试和真实付费生成。

- [ ] **步骤 5：静态检查**

```bash
uv run ruff check \
  src/novelvideo/media_capabilities/video/h3_rigid_prompt.py \
  src/novelvideo/media_capabilities/video/h3_director_plan.py \
  src/novelvideo/media_capabilities/video/h3_prompt_compiler.py \
  src/novelvideo/media_capabilities/video/h3_prompt_optimizer.py \
  src/novelvideo/media_capabilities/video/h3_episode_pack.py \
  src/novelvideo/media_capabilities/video/h3_prompt_quality.py \
  src/novelvideo/media_capabilities/video/h3_prompt_profile.py \
  src/novelvideo/task_backend/runners/narrative_group_video.py \
  src/novelvideo/task_backend/runners/video.py
git diff --check
```

预期：无新增错误。

## 3. 完成标准

- 新优化任务只产出并接受 v2 刚性计划；
- v2 Prompt 固定编译十五段，灯光是独立强类型章节；
- 项目 Style Prefix 原样进入 `STYLE`，不强制写实；
- H3 生成阶段固定 SFX only、无配乐；
- 自动补全不覆盖已存在的非空刚性事实；
- 单 Beat 和叙事组都不能绕过质量门进入付费调用；
- 旧 v1 计划和旧 manifest 保持可读。
