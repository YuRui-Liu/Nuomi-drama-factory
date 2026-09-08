# H3 导演台官方五模式实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 `subagent-driven-development`（推荐）或 `executing-plans` 逐任务实现此计划。每个任务都按红灯测试、最小实现、绿灯验证、独立提交执行。

**目标：** 让 H3 导演台严格支持 MiniMax H3 官方 T2VA、I2VA、FL2VA、L2VA、Ref2VA 五种模式，并输出与官方技能一致的 wire format，同时保留现有 Higgsfield 刚性导演数据作为内部 IR。

**架构：** 新增单一的官方 wire 编译层，由模式选择、导演 IR 投影、质量门和各运行时适配器共同调用。导演台展示全部五种模式；模型或工作流尚未验证的模式保持可见但禁用，并明确原因，禁止静默降级。现有 15 段刚性字段继续服务内部规划与质检，但不会泄漏到最终提示词。

**技术栈：** Python 3.11+、Pydantic v2、FastAPI、pytest、React、TypeScript、Vitest、Testing Library

---

## 文件结构

新增：

- `src/novelvideo/media_capabilities/video/h3_wire.py`：官方五模式数据契约、渲染器和静态校验。
- `tests/media_capabilities/video/test_h3_wire.py`：五种 wire format 的黄金测试。
- `tests/fixtures/minimax_h3/t2va_prompt.txt`
- `tests/fixtures/minimax_h3/i2va_prompt.txt`
- `tests/fixtures/minimax_h3/fl2va_prompt.txt`
- `tests/fixtures/minimax_h3/l2va_prompt.txt`
- `tests/fixtures/minimax_h3/ref2va_prompt.txt`
- `frontend/src/components/episode/narrative-workbench/h3-video-mode-select.tsx`：导演台五模式选择器与禁用原因。
- `frontend/src/__tests__/components/episode/narrative-workbench/h3-video-mode-select.test.tsx`

修改：

- `src/novelvideo/media_capabilities/video/h3_director_plan.py`
- `src/novelvideo/media_capabilities/video/h3_prompt_compiler.py`
- `src/novelvideo/media_capabilities/video/h3_prompt.py`
- `src/novelvideo/media_capabilities/video/h3_prompt_quality.py`
- `src/novelvideo/media_capabilities/video/h3_reference_payload.py`
- `src/novelvideo/media_capabilities/video/runtime.py`
- `src/novelvideo/media_capabilities/workflow_registry.py`
- `src/novelvideo/shot_continuity/models.py`
- `src/novelvideo/shot_continuity/mode_selector.py`
- `src/novelvideo/api/schemas.py`
- `frontend/src/lib/queries/media-models.ts`
- `frontend/src/lib/queries/narrative-groups.ts`
- `frontend/src/components/episode/narrative-workbench/group-video-stage.tsx`
- `frontend/src/components/episode/narrative-workbench/narrative-group-workbench.tsx`
- 与上述入口直接对应的既有测试文件。

不修改：

- `profiles/minimax_h3.json` 不虚构 T2VA/L2VA/Ref2VA 传输能力，仍只声明已经验证的 I2VA/FL2VA。
- `profiles/minimax_h3_ref.json` 只声明已经验证到配置层的 Ref2VA；在 RunningHub 混合输入未完成实测前，注册表继续给出不可用原因。
- 既有用户改动、非 H3 文件和历史生成结果不做整理或回滚。

## 官方契约基线

实现和测试以 MiniMax 官方 H3 skill 为唯一外部契约：

- 五种模式：`T2VA`、`I2VA`、`FL2VA`、`L2VA`、`Ref2VA`。
- Base wire 顺序固定为 `integrated_multimodal_description`、`overall_soundscape`、`non_diegetic_music`。
- Ref wire 顺序固定为 `subject_definitions`、`summary`、`retention_analysis`、`detailed_description`、`overall_soundscape`、`non_diegetic_music`。
- 台词说话人使用稳定标签 `(S1)`、`(S2)`；台词正文使用 `<d>[语言]原文</d>`。
- 转场与截断只使用 `<scenetrans>`、`<cutoff>`。
- 无非叙事音乐时输出 `N/A`。
- 时长必须在 4–15 秒内；镜头描述按播放顺序书写。
- I2VA 必须显式承接首帧；FL2VA 必须显式承接首帧并收束到尾帧；L2VA 必须显式收束到尾帧；Ref2VA 必须完整输出六段引用 wire。

### 任务 1：建立单一官方 H3 wire 契约

**文件：**

- 新增：`src/novelvideo/media_capabilities/video/h3_wire.py`
- 新增：`tests/media_capabilities/video/test_h3_wire.py`
- 新增：`tests/fixtures/minimax_h3/{t2va,i2va,fl2va,l2va,ref2va}_prompt.txt`

- [ ] **步骤 1：先写五模式黄金测试**

测试必须构造完整对象并逐字比对 fixture，而不是只检查包含关键字：

```python
from pathlib import Path

import pytest

from novelvideo.media_capabilities.video.h3_prompt import H3Mode
from novelvideo.media_capabilities.video.h3_wire import (
    H3BaseWire,
    H3ReferenceWire,
    H3RetentionItem,
    compile_h3_wire,
)

FIXTURES = Path(__file__).parents[2] / "fixtures" / "minimax_h3"


@pytest.mark.parametrize(
    ("mode", "fixture_name", "description"),
    [
        (H3Mode.T2VA, "t2va_prompt.txt", "[0-6s] A static medium shot of (S1) waiting by the rain-streaked window."),
        (H3Mode.I2VA, "i2va_prompt.txt", "[0-6s] Continue exactly from the provided first image: (S1) slowly turns toward camera."),
        (H3Mode.FL2VA, "fl2va_prompt.txt", "[0-6s] Continue exactly from the provided first image; (S1) crosses the room and lands exactly on the provided last image."),
        (H3Mode.L2VA, "l2va_prompt.txt", "[0-6s] (S1) crosses the room and converges exactly into the provided last image."),
    ],
)
def test_compile_base_wire_matches_official_golden(
    mode: H3Mode,
    fixture_name: str,
    description: str,
) -> None:
    wire = H3BaseWire(
        mode=mode,
        duration_seconds=6,
        integrated_multimodal_description=description,
        overall_soundscape="Rain taps the glass; soft footsteps cross the wooden floor.",
        non_diegetic_music="N/A",
    )

    assert compile_h3_wire(wire) == (FIXTURES / fixture_name).read_text().rstrip("\n")


def test_compile_reference_wire_matches_official_golden() -> None:
    wire = H3ReferenceWire(
        duration_seconds=6,
        subject_definitions="(S1): the woman in reference image 1, preserving face, hair, coat, and proportions.",
        summary="A restrained dramatic beat in a rain-dark apartment.",
        retention_analysis=[
            H3RetentionItem(subject="(S1)", retain="identity, wardrobe, body proportions"),
        ],
        detailed_description="[0-6s] Static medium shot. (S1) turns from the window and says <d>[Chinese]你终于来了。</d>",
        overall_soundscape="Rain taps the glass; a floorboard creaks beneath (S1).",
        non_diegetic_music="N/A",
    )

    assert compile_h3_wire(wire) == (FIXTURES / "ref2va_prompt.txt").read_text().rstrip("\n")
```

五个 fixture 使用上述内容，字段名与字段顺序逐字固定；文件末尾只保留一个换行。

- [ ] **步骤 2：运行测试，确认红灯来自模块尚不存在**

运行：

```bash
uv run pytest tests/media_capabilities/video/test_h3_wire.py -q
```

预期：测试收集失败，错误为无法导入 `h3_wire`，而不是环境或依赖错误。

- [ ] **步骤 3：实现最小 wire 类型与渲染器**

`h3_wire.py` 的公开接口固定为：

```python
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .h3_prompt import H3Mode


class H3RetentionItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    subject: str = Field(min_length=1)
    retain: str = Field(min_length=1)


class H3BaseWire(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: Literal[H3Mode.T2VA, H3Mode.I2VA, H3Mode.FL2VA, H3Mode.L2VA]
    duration_seconds: Annotated[float, Field(ge=4, le=15)]
    integrated_multimodal_description: str = Field(min_length=1)
    overall_soundscape: str = Field(min_length=1)
    non_diegetic_music: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_frame_alignment(self) -> "H3BaseWire":
        lowered = self.integrated_multimodal_description.lower()
        if self.mode in {H3Mode.I2VA, H3Mode.FL2VA} and "provided first image" not in lowered:
            raise ValueError(f"{self.mode.value} requires explicit first-image alignment")
        if self.mode in {H3Mode.FL2VA, H3Mode.L2VA} and "provided last image" not in lowered:
            raise ValueError(f"{self.mode.value} requires explicit last-image alignment")
        return self


class H3ReferenceWire(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: Literal[H3Mode.REF2VA] = H3Mode.REF2VA
    duration_seconds: Annotated[float, Field(ge=4, le=15)]
    subject_definitions: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    retention_analysis: list[H3RetentionItem] = Field(min_length=1)
    detailed_description: str = Field(min_length=1)
    overall_soundscape: str = Field(min_length=1)
    non_diegetic_music: str = Field(min_length=1)


H3Wire = H3BaseWire | H3ReferenceWire


def compile_h3_wire(wire: H3Wire) -> str:
    if isinstance(wire, H3ReferenceWire):
        retention = "\n".join(
            f"- {item.subject}: {item.retain}" for item in wire.retention_analysis
        )
        return "\n\n".join(
            [
                f"subject_definitions:\n{wire.subject_definitions}",
                f"summary:\n{wire.summary}",
                f"retention_analysis:\n{retention}",
                f"detailed_description:\n{wire.detailed_description}",
                f"overall_soundscape:\n{wire.overall_soundscape}",
                f"non_diegetic_music:\n{wire.non_diegetic_music}",
            ]
        )
    return "\n\n".join(
        [
            "integrated_multimodal_description:\n"
            f"{wire.integrated_multimodal_description}",
            f"overall_soundscape:\n{wire.overall_soundscape}",
            f"non_diegetic_music:\n{wire.non_diegetic_music}",
        ]
    )
```

如果仓库当前 `H3Mode` 的成员名与示例大小写不同，保留其现有枚举成员名，只保证序列化值为官方字符串。

- [ ] **步骤 4：运行黄金测试并补齐边界测试**

另加测试覆盖：3.99 秒、15.01 秒、缺失首帧/尾帧对齐文案、Ref2VA 空 `retention_analysis` 都必须校验失败。

运行：

```bash
uv run pytest tests/media_capabilities/video/test_h3_wire.py -q
```

预期：全部通过。

- [ ] **步骤 5：提交任务 1**

```bash
git add src/novelvideo/media_capabilities/video/h3_wire.py tests/media_capabilities/video/test_h3_wire.py tests/fixtures/minimax_h3
git commit -m "feat: add official H3 wire contract"
```

### 任务 2：把导演计划扩展为五模式内部 IR

**文件：**

- 修改：`src/novelvideo/media_capabilities/video/h3_director_plan.py`
- 修改：`tests/media_capabilities/video/test_h3_director_plan.py`

- [ ] **步骤 1：写失败测试，固定五模式与摄像机语义**

增加以下断言：

```python
@pytest.mark.parametrize("mode", list(H3Mode))
def test_director_plan_accepts_every_official_mode(mode: H3Mode) -> None:
    plan = make_valid_director_plan(mode=mode)
    assert plan.mode is mode


def test_static_camera_does_not_require_motion_parameters() -> None:
    camera = H3CameraPlan(movement="static")
    assert camera.direction is None
    assert camera.amplitude is None
    assert camera.speed is None


def test_dynamic_camera_allows_normal_motion_defaults_to_be_omitted() -> None:
    camera = H3CameraPlan(movement="dolly_in", direction="forward")
    assert camera.amplitude is None
    assert camera.speed is None
```

再分别测试：I2VA/FL2VA 缺首帧上下文失败，FL2VA/L2VA 缺尾帧上下文失败，Ref2VA 缺 subject 定义或 retention 分析失败，T2VA 不允许携带首尾帧锚点。

- [ ] **步骤 2：运行测试，确认现有 I2VA/FL2VA 限制触发红灯**

```bash
uv run pytest tests/media_capabilities/video/test_h3_director_plan.py -q
```

预期：T2VA、L2VA、Ref2VA 参数被拒绝，动态镜头省略普通幅度或速度时被拒绝。

- [ ] **步骤 3：扩展 IR，但保留现有刚性字段**

实现原则：

- `mode` 接受官方五种枚举值。
- 首帧、尾帧、引用主体信息均为可选输入，但由 mode 的模型校验器施加条件必需性。
- 保留现有动作节拍、构图、表演、摄影、音效等内部字段及 schema 版本；不删除对现有生成记录的读取能力。
- `H3CameraPlan` 在静态镜头下禁止运动参数；动态镜头只要求方向，幅度和速度缺省代表 normal，渲染时不输出多余 normal 描述。
- Ref2VA 的 IR 至少包含稳定主体编号、来源引用和保留特征；同一主体在台词与动作中复用 `(S1)` 标签。

- [ ] **步骤 4：运行导演计划与反序列化回归测试**

```bash
uv run pytest tests/media_capabilities/video/test_h3_director_plan.py tests/media_capabilities/video/test_h3_prompt_optimizer.py -q
```

预期：五模式新测试通过；已有 schema v2 数据仍可读取。

- [ ] **步骤 5：提交任务 2**

```bash
git add src/novelvideo/media_capabilities/video/h3_director_plan.py tests/media_capabilities/video/test_h3_director_plan.py
git commit -m "feat: expand H3 director plan to five modes"
```

### 任务 3：把刚性导演 IR 投影为官方 wire

**文件：**

- 修改：`src/novelvideo/media_capabilities/video/h3_prompt_compiler.py`
- 修改：`src/novelvideo/media_capabilities/video/h3_prompt_profile.py`
- 修改：`tests/media_capabilities/video/test_h3_prompt_compiler.py`
- 修改：`tests/media_capabilities/video/test_h3_prompt_quality.py`

- [ ] **步骤 1：写失败测试，禁止内部字段泄漏**

```python
FORBIDDEN_INTERNAL_HEADINGS = {
    "CORE INTENT",
    "SHOT SUMMARY",
    "COMPOSITION",
    "CAMERA MOVEMENT",
    "ACTING",
    "LIGHTING",
    "AUDIO",
    "NEGATIVE CONSTRAINTS",
}


@pytest.mark.parametrize("mode", list(H3Mode))
def test_rigid_plan_compiles_to_official_wire_without_internal_headings(mode: H3Mode) -> None:
    prompt = compile_h3_director_prompt(make_valid_director_plan(mode=mode))
    for heading in FORBIDDEN_INTERNAL_HEADINGS:
        assert heading not in prompt


def test_dialogue_uses_stable_speaker_and_language_tag() -> None:
    prompt = compile_h3_director_prompt(
        make_valid_director_plan(
            mode=H3Mode.T2VA,
            dialogue=[make_dialogue(speaker="S1", language="Chinese", text="你终于来了。")],
        )
    )
    assert "(S1) says <d>[Chinese]你终于来了。</d>" in prompt
    assert "dialogue:" not in prompt.lower()


def test_no_music_uses_official_na_value() -> None:
    prompt = compile_h3_director_prompt(make_valid_director_plan(mode=H3Mode.T2VA))
    assert prompt.endswith("non_diegetic_music:\nN/A")
    assert "No music. SFX only." not in prompt
```

为五模式各加一个首/尾帧或引用主体对齐断言，并断言 `overall_soundscape` 只出现一次。

- [ ] **步骤 2：运行编译器测试，确认旧 15 段文本触发红灯**

```bash
uv run pytest tests/media_capabilities/video/test_h3_prompt_compiler.py tests/media_capabilities/video/test_h3_prompt_quality.py -q
```

预期：旧大写段落、重复音频、旧无音乐短语导致失败。

- [ ] **步骤 3：实现 IR 到 wire 的纯投影**

将编译器拆成两个明确阶段：

```python
def project_director_plan_to_wire(plan: H3DirectorPlan) -> H3Wire:
    if plan.mode is H3Mode.REF2VA:
        return H3ReferenceWire(
            duration_seconds=plan.duration_seconds,
            subject_definitions=render_subject_definitions(plan),
            summary=render_summary(plan),
            retention_analysis=render_retention_analysis(plan),
            detailed_description=render_playback_description(plan),
            overall_soundscape=render_soundscape(plan),
            non_diegetic_music=render_music(plan),
        )
    return H3BaseWire(
        mode=plan.mode,
        duration_seconds=plan.duration_seconds,
        integrated_multimodal_description=render_playback_description(plan),
        overall_soundscape=render_soundscape(plan),
        non_diegetic_music=render_music(plan),
    )


def compile_h3_director_prompt(plan: H3DirectorPlan) -> str:
    return compile_h3_wire(project_director_plan_to_wire(plan))
```

`render_playback_description` 必须按时间顺序融合：首/尾帧锚点、构图与镜头、主体动作、表演、对白、场景状态变化、转场/截断。它不输出内部字段名，也不输出“想要什么”之类元指令。

`h3_prompt_profile.py` 的 2–3 个动作限制改为时长预算与可读性质量规则：4–6 秒建议一个主节拍，7–10 秒最多两个清晰节拍，11–15 秒最多三个清晰节拍；失败原因返回给导演台，不把硬编码节拍数写入 wire。

- [ ] **步骤 4：运行 H3 编译、优化和质量回归**

```bash
uv run pytest tests/media_capabilities/video/test_h3_prompt_compiler.py tests/media_capabilities/video/test_h3_prompt_quality.py tests/media_capabilities/video/test_h3_prompt_optimizer.py -q
```

预期：所有测试通过；输出仅有官方字段。

- [ ] **步骤 5：提交任务 3**

```bash
git add src/novelvideo/media_capabilities/video/h3_prompt_compiler.py src/novelvideo/media_capabilities/video/h3_prompt_profile.py tests/media_capabilities/video/test_h3_prompt_compiler.py tests/media_capabilities/video/test_h3_prompt_quality.py
git commit -m "feat: compile H3 director plans to official wire"
```

### 任务 4：建立模式感知的正式质量门

**文件：**

- 修改：`src/novelvideo/media_capabilities/video/h3_prompt_quality.py`
- 修改：`tests/media_capabilities/video/test_h3_prompt_quality.py`

- [ ] **步骤 1：先写结构化失败报告测试**

质量门返回稳定的规则代码，至少覆盖：

```python
@pytest.mark.parametrize(
    ("mode", "prompt", "expected_code"),
    [
        (H3Mode.I2VA, valid_base_prompt_without_first_alignment(), "h3.first_image_alignment_missing"),
        (H3Mode.FL2VA, valid_base_prompt_without_last_alignment(), "h3.last_image_alignment_missing"),
        (H3Mode.L2VA, valid_base_prompt_without_last_alignment(), "h3.last_image_alignment_missing"),
        (H3Mode.REF2VA, reference_prompt_without_retention(), "h3.retention_analysis_missing"),
        (H3Mode.T2VA, base_prompt_with_internal_heading(), "h3.internal_heading_leaked"),
        (H3Mode.T2VA, base_prompt_with_raw_dialogue(), "h3.dialogue_wire_invalid"),
    ],
)
def test_quality_gate_reports_mode_specific_rule(
    mode: H3Mode,
    prompt: str,
    expected_code: str,
) -> None:
    report = inspect_h3_prompt(prompt=prompt, mode=mode, duration_seconds=6)
    assert expected_code in {issue.code for issue in report.issues}
```

- [ ] **步骤 2：运行测试，确认旧质量门无法识别官方结构**

```bash
uv run pytest tests/media_capabilities/video/test_h3_prompt_quality.py -q
```

预期：新增规则断言失败。

- [ ] **步骤 3：实现质量门规则**

质量门必须同时检查：

- 模式对应字段集合与字段顺序。
- 时长 4–15 秒及最后时间戳不超过请求时长。
- 模式所需首帧、尾帧、引用保留锚点。
- `(S1)` 稳定说话人和 `<d>[Language]text</d>` 语法。
- 仅允许 `<scenetrans>`、`<cutoff>` 两种控制标记。
- `overall_soundscape` 与 `non_diegetic_music` 各出现一次。
- 禁止 15 段内部标题、`mode:` 前缀、裸 `dialogue:` 块和旧无音乐短语。
- 对“动作过载”给出可修复错误；对正常幅度/速度缺省不报错。

返回值继续兼容现有调用方，但每个问题同时包含 `code`、`message`、`field`、`severity`，供前端展示。

- [ ] **步骤 4：运行质量门及全部 prompt 测试**

```bash
uv run pytest tests/media_capabilities/video/test_h3_prompt_quality.py tests/media_capabilities/video/test_h3_prompt.py tests/media_capabilities/video/test_h3_prompt_compiler.py -q
```

预期：全部通过。

- [ ] **步骤 5：提交任务 4**

```bash
git add src/novelvideo/media_capabilities/video/h3_prompt_quality.py tests/media_capabilities/video/test_h3_prompt_quality.py
git commit -m "feat: enforce mode-aware H3 prompt quality gate"
```

### 任务 5：统一五模式选择、冻结输入与 bundle 契约

**文件：**

- 修改：`src/novelvideo/shot_continuity/models.py`
- 修改：`src/novelvideo/shot_continuity/mode_selector.py`
- 修改：`src/novelvideo/media_capabilities/video/runtime.py`
- 修改：`tests/shot_continuity/test_models.py`
- 修改：`tests/shot_continuity/test_mode_selector.py`
- 修改：`tests/media_capabilities/video/test_h3_prompt.py`

- [ ] **步骤 1：写 auto 决策矩阵失败测试**

```python
@pytest.mark.parametrize(
    ("has_first", "has_last", "has_references", "expected"),
    [
        (False, False, False, H3Mode.T2VA),
        (True, False, False, H3Mode.I2VA),
        (True, True, False, H3Mode.FL2VA),
        (False, True, False, H3Mode.L2VA),
        (False, False, True, H3Mode.REF2VA),
        (True, True, True, H3Mode.REF2VA),
    ],
)
def test_auto_mode_uses_frozen_input_matrix(
    has_first: bool,
    has_last: bool,
    has_references: bool,
    expected: H3Mode,
) -> None:
    decision = select_h3_mode(
        requested="auto",
        first_frame="first.png" if has_first else None,
        last_frame="last.png" if has_last else None,
        reference_assets=["subject.png"] if has_references else [],
    )
    assert decision.mode is expected
```

另加显式模式输入缺失测试，错误码分别为 `h3.first_frame_required`、`h3.last_frame_required`、`h3.references_required`；显式模式不能被自动改写。

- [ ] **步骤 2：运行测试，确认当前首帧必需和 I/FL 双模式限制触发红灯**

```bash
uv run pytest tests/shot_continuity/test_models.py tests/shot_continuity/test_mode_selector.py -q
```

预期：无首帧 bundle、T2VA/L2VA/Ref2VA 选择失败。

- [ ] **步骤 3：扩展决策模型和 bundle 条件约束**

实现：

- 请求值接受 `auto` 加五种官方模式。
- `CompiledShotBundle.first_frame` 与 `last_frame` 改为可选，并按最终 mode 条件验证。
- 新增冻结的 `reference_assets` 快照或等价不可变字段。
- `auto` 严格使用上表，引用优先；没有静默回退。
- 决策结果记录 `requested_mode`、`resolved_mode`、`reason_code`、输入摘要及模型能力判断，便于迭代账本复现。

- [ ] **步骤 4：让 runtime 只接受已解析模式**

`runtime.resolve_h3_mode` 调用同一选择器，不维护第二份 I/FL 判断；调用方必须在任务创建时冻结首帧、尾帧和引用资产。模型能力不支持时返回 `h3.mode_unsupported_by_workflow`，不得换成 I2VA。

- [ ] **步骤 5：运行 selector、runtime 与连续性回归**

```bash
uv run pytest tests/shot_continuity/test_models.py tests/shot_continuity/test_mode_selector.py tests/shot_continuity/test_compiler.py tests/media_capabilities/video/test_h3_prompt.py -q
```

预期：全部通过。

- [ ] **步骤 6：提交任务 5**

```bash
git add src/novelvideo/shot_continuity/models.py src/novelvideo/shot_continuity/mode_selector.py src/novelvideo/media_capabilities/video/runtime.py tests/shot_continuity/test_models.py tests/shot_continuity/test_mode_selector.py tests/media_capabilities/video/test_h3_prompt.py
git commit -m "feat: resolve all H3 modes from frozen inputs"
```

### 任务 6：收敛运行时适配器与真实能力门控

**文件：**

- 修改：`src/novelvideo/media_capabilities/video/h3_reference_payload.py`
- 修改：`src/novelvideo/media_capabilities/workflow_registry.py`
- 修改：`tests/media_capabilities/video/test_h3_reference_payload.py`
- 修改：`tests/test_task_video_runner_h3.py`
- 修改：`tests/test_task_narrative_group_video_runner.py`

- [ ] **步骤 1：写失败测试，固定 Ref2VA 六段和禁止降级**

```python
def test_reference_payload_uses_complete_reference_wire() -> None:
    payload = build_h3_reference_payload(make_reference_request())
    prompt = payload.prompt
    assert prompt.index("subject_definitions:") < prompt.index("summary:")
    assert prompt.index("summary:") < prompt.index("retention_analysis:")
    assert prompt.index("retention_analysis:") < prompt.index("detailed_description:")
    assert prompt.index("detailed_description:") < prompt.index("overall_soundscape:")
    assert prompt.index("overall_soundscape:") < prompt.index("non_diegetic_music:")


def test_unverified_transport_mode_fails_without_silent_fallback() -> None:
    with pytest.raises(H3ModeUnavailableError, match="h3.mode_unsupported_by_workflow"):
        run_h3_video(make_request(mode=H3Mode.T2VA), workflow=minimax_h3_workflow())
```

- [ ] **步骤 2：运行适配器测试，确认旧 ref prompt 和降级路径触发红灯**

```bash
uv run pytest tests/media_capabilities/video/test_h3_reference_payload.py tests/test_task_video_runner_h3.py tests/test_task_narrative_group_video_runner.py -q
```

预期：Ref2VA 只有 subject 定义，或 unsupported mode 被错误接受。

- [ ] **步骤 3：统一适配器入口**

- `h3_reference_payload.py` 只负责把 `H3ReferenceWire` 映射到节点参数，不再自行拼接另一套 prompt。
- 基础 H3 与引用 H3 都接收已经通过质量门的最终 wire 文本。
- `workflow_registry.py` 从 profile 的 capability 声明推导模式集合；基础工作流保持 I2VA/FL2VA，引用工作流只暴露 Ref2VA。
- 当前未验证的 RunningHub 引用混合输入继续标记不可用并返回具体原因。
- 当未来 profile 增加 T2VA/L2VA 能力时，无需修改选择器和编译器即可启用；本任务不伪造节点映射。

- [ ] **步骤 4：验证任务快照保存模式与最终 wire**

任务记录至少保存 `requested_mode`、`resolved_mode`、冻结输入哈希、prompt schema 版本、最终 wire、质量报告和 workflow id。重试使用原快照，除非用户显式创建“一次只改一个变量”的新迭代。

- [ ] **步骤 5：运行后端适配器与任务回归**

```bash
uv run pytest tests/media_capabilities/video/test_h3_reference_payload.py tests/test_task_video_runner_h3.py tests/test_task_narrative_group_video_runner.py -q
```

预期：全部通过；不支持的模式产生稳定错误，不降级。

- [ ] **步骤 6：提交任务 6**

```bash
git add src/novelvideo/media_capabilities/video/h3_reference_payload.py src/novelvideo/media_capabilities/workflow_registry.py tests/media_capabilities/video/test_h3_reference_payload.py tests/test_task_video_runner_h3.py tests/test_task_narrative_group_video_runner.py
git commit -m "feat: gate H3 modes by verified workflow capability"
```

### 任务 7：在 H3 导演台呈现五模式和禁用原因

**文件：**

- 修改：`src/novelvideo/api/schemas.py`
- 修改：相关 FastAPI 路由中 H3 mode 校验与响应映射
- 修改：`tests/test_api_media_capabilities.py`
- 修改：`tests/test_project_media_defaults.py`
- 修改：`tests/test_api_narrative_groups.py`
- 修改：`frontend/src/lib/queries/media-models.ts`
- 修改：`frontend/src/lib/queries/narrative-groups.ts`
- 新增：`frontend/src/components/episode/narrative-workbench/h3-video-mode-select.tsx`
- 新增：`frontend/src/__tests__/components/episode/narrative-workbench/h3-video-mode-select.test.tsx`
- 修改：`frontend/src/components/episode/narrative-workbench/group-video-stage.tsx`
- 修改：`frontend/src/components/episode/narrative-workbench/narrative-group-workbench.tsx`
- 修改：相应既有前端测试

- [ ] **步骤 1：先写 API 枚举和能力响应失败测试**

API 应接受：

```python
@pytest.mark.parametrize("mode", ["auto", "t2va", "i2va", "fl2va", "l2va", "ref2va"])
def test_project_accepts_every_h3_mode(mode: str, client: TestClient) -> None:
    response = client.patch("/api/projects/project-1", json={"h3_mode": mode})
    assert response.status_code == 200
```

能力响应针对每个模式返回 `enabled` 与 `reason`；不得仅返回一个模糊的模型可用布尔值。

- [ ] **步骤 2：写前端选择器失败测试**

```tsx
it("shows auto and all five official H3 modes", () => {
  render(
    <H3VideoModeSelect
      value="auto"
      capabilities={makeCapabilities({ i2va: true, fl2va: true })}
      inputs={{ hasFirstFrame: true, hasLastFrame: true, referenceCount: 0 }}
      onChange={vi.fn()}
    />,
  );

  for (const label of ["自动", "T2VA", "I2VA", "FL2VA", "L2VA", "Ref2VA"]) {
    expect(screen.getByRole("option", { name: new RegExp(label) })).toBeInTheDocument();
  }
});


it("keeps unsupported modes visible and explains why they are disabled", () => {
  render(
    <H3VideoModeSelect
      value="auto"
      capabilities={makeCapabilities({ i2va: true, fl2va: true })}
      inputs={{ hasFirstFrame: true, hasLastFrame: true, referenceCount: 0 }}
      onChange={vi.fn()}
    />,
  );

  expect(screen.getByRole("option", { name: /T2VA.*当前工作流未验证/ })).toBeDisabled();
  expect(screen.getByRole("option", { name: /Ref2VA.*需要引用资产/ })).toBeDisabled();
});
```

- [ ] **步骤 3：运行 API 和前端测试，确认当前类型只允许 I/FL**

```bash
uv run pytest tests/test_api_media_capabilities.py tests/test_project_media_defaults.py tests/test_api_narrative_groups.py -q
cd frontend && npm test -- --run src/__tests__/components/episode/narrative-workbench/h3-video-mode-select.test.tsx src/__tests__/lib/queries/media-models.test.ts
```

预期：新枚举值在后端 422 或前端类型/组件测试失败。

- [ ] **步骤 4：扩展共享前后端类型与能力响应**

前端类型固定为：

```ts
export type H3VideoMode = "auto" | "t2va" | "i2va" | "fl2va" | "l2va" | "ref2va";

export type H3ModeAvailability = {
  mode: Exclude<H3VideoMode, "auto">;
  enabled: boolean;
  reason: string | null;
  requiresFirstFrame: boolean;
  requiresLastFrame: boolean;
  requiresReferences: boolean;
};
```

后端模式字段同步接受相同小写 wire 值；官方大写仅用于用户标签和文档，不在数据库中混入第二种枚举写法。

- [ ] **步骤 5：实现导演台选择器与生成按钮门控**

- 在现有 `GroupVideoStage` 内放置选择器，不新建独立页面。
- 显示自动与五种官方模式；不可用项保持可见。
- 禁用原因按优先级展示：缺少所需输入、工作流未验证、模型不支持。
- 移除“所有模式都必须有首帧”的前端硬门；改由最终 mode 的输入条件控制生成按钮。
- auto 旁展示当前解析结果，例如“自动 → FL2VA”。
- 提交请求时同时传 requested mode；服务端记录 resolved mode。

- [ ] **步骤 6：运行导演台与 API 回归**

```bash
uv run pytest tests/test_api_media_capabilities.py tests/test_project_media_defaults.py tests/test_api_narrative_groups.py -q
cd frontend && npm test -- --run src/__tests__/components/episode/narrative-workbench/h3-video-mode-select.test.tsx src/__tests__/components/episode/narrative-workbench/group-video-stage.test.tsx src/__tests__/components/episode/narrative-workbench/narrative-group-workbench-references.test.tsx src/__tests__/lib/queries/media-models.test.ts
```

预期：全部通过。

- [ ] **步骤 7：提交任务 7**

```bash
git add src/novelvideo/api/schemas.py tests/test_api_media_capabilities.py tests/test_project_media_defaults.py tests/test_api_narrative_groups.py frontend/src/lib/queries/media-models.ts frontend/src/lib/queries/narrative-groups.ts frontend/src/components/episode/narrative-workbench/h3-video-mode-select.tsx frontend/src/components/episode/narrative-workbench/group-video-stage.tsx frontend/src/components/episode/narrative-workbench/narrative-group-workbench.tsx frontend/src/__tests__/components/episode/narrative-workbench/h3-video-mode-select.test.tsx frontend/src/__tests__/components/episode/narrative-workbench/group-video-stage.test.tsx frontend/src/__tests__/components/episode/narrative-workbench/narrative-group-workbench-references.test.tsx frontend/src/__tests__/lib/queries/media-models.test.ts
git commit -m "feat: expose official H3 modes in director console"
```

提交前用 `git diff --name-only --cached` 确认没有夹带未列出的用户文件；如果实际路由文件被修改，将其显式加入本任务清单与提交。

### 任务 8：收敛旧 prompt 入口并完成端到端验证

**文件：**

- 修改：`src/novelvideo/media_capabilities/video/h3_prompt.py`
- 修改：`tests/media_capabilities/video/test_h3_prompt.py`
- 修改：受影响的任务/API 集成测试

- [ ] **步骤 1：写兼容入口失败测试**

```python
@pytest.mark.parametrize("mode", list(H3Mode))
def test_legacy_prompt_entry_delegates_to_canonical_wire(mode: H3Mode) -> None:
    request = make_valid_prompt_request(mode=mode)
    assert render_h3_prompt(request) == compile_h3_wire(build_expected_wire(request))


def test_legacy_entry_never_emits_mode_prefix_or_raw_dialogue_block() -> None:
    prompt = render_h3_prompt(make_valid_prompt_request(mode=H3Mode.T2VA))
    assert not prompt.startswith("mode:")
    assert "\ndialogue:" not in prompt.lower()
```

- [ ] **步骤 2：运行测试，确认旧通用渲染路径触发红灯**

```bash
uv run pytest tests/media_capabilities/video/test_h3_prompt.py -q
```

预期：旧 `mode:` 前缀或裸台词块导致失败。

- [ ] **步骤 3：让所有入口委托给 canonical wire**

- `h3_prompt.py` 保留公共类型和兼容函数签名，但内部只构造 `H3BaseWire` 或 `H3ReferenceWire` 并调用 `compile_h3_wire`。
- 删除重复渲染逻辑，不删除历史 schema 读取器。
- 单镜头、叙事组、Ref2VA 适配器和质量门得到同一段最终 wire。
- 任务重试继续读取冻结快照；旧任务没有新字段时按其历史 schema 走兼容读取，不重写已有结果。

- [ ] **步骤 4：运行完整 H3 后端测试集**

```bash
uv run pytest tests/media_capabilities/video tests/shot_continuity tests/test_task_video_runner_h3.py tests/test_task_narrative_group_video_runner.py tests/test_api_media_capabilities.py tests/test_project_media_defaults.py tests/test_api_narrative_groups.py -q
```

预期：全部通过。

- [ ] **步骤 5：运行前端目标测试与构建**

```bash
cd frontend && npm test -- --run src/__tests__/components/episode/narrative-workbench/h3-video-mode-select.test.tsx src/__tests__/components/episode/narrative-workbench/group-video-stage.test.tsx src/__tests__/components/episode/narrative-workbench/narrative-group-workbench-references.test.tsx src/__tests__/lib/queries/media-models.test.ts
cd frontend && npm run build
```

预期：测试和生产构建均成功。

- [ ] **步骤 6：执行静态检查和差异审计**

```bash
uv run ruff check src/novelvideo/media_capabilities/video src/novelvideo/shot_continuity src/novelvideo/api tests/media_capabilities/video tests/shot_continuity
git diff --check
git status --short
```

审计要求：

- 五个黄金 fixture 与 MiniMax 官方字段顺序一致。
- 最终 wire 无内部 15 段标题、无 `mode:`、无裸 `dialogue:`。
- 导演台显示五模式；当前工作流未验证的模式可见且禁用。
- 不支持模式无静默降级。
- git 状态中原有用户改动仍保持原样，本任务只提交计划列出的 H3 范围文件。

- [ ] **步骤 7：提交任务 8**

```bash
git add src/novelvideo/media_capabilities/video/h3_prompt.py tests/media_capabilities/video/test_h3_prompt.py
git commit -m "refactor: converge H3 prompt entry points"
```

- [ ] **步骤 8：请求代码审查**

使用 `requesting-code-review` 技能审查完整提交序列，重点检查：官方 wire 偏差、模式条件错配、隐藏降级、旧任务兼容、前端禁用原因和非任务文件夹带。审查问题修复后重新执行步骤 4–6，再进入分支收尾。

## 实施纪律

- 每个任务开始前检查 `git status --short`，记录并避开用户已有改动。
- 每个功能先写能证明契约的失败测试，再写最小实现；不得先改生产代码再补测试。
- 每次提交前使用 `git diff --name-only --cached` 做范围审计。
- 只有模型 profile 与实际工作流节点都验证支持时才能把对应模式设为 enabled。
- “支持五模式”指产品契约、编译器与导演台完整覆盖五模式；不等于宣称现有 RunningHub 工作流已经能传输所有五模式。
- 如果真实节点字段与官方 skill 冲突，保留官方 canonical wire，在适配器边界做显式映射并补集成测试，不污染导演 IR。
