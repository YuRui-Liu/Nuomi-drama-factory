from types import SimpleNamespace
import json

import httpx
import pytest
from pydantic_ai import PromptedOutput
from pydantic_ai.exceptions import ModelAPIError, ModelHTTPError

import novelvideo.media_capabilities.video.h3_prompt_optimizer as h3_prompt_optimizer
from novelvideo.media_capabilities.video.h3_prompt_optimizer import (
    H3PromptContext,
    H3PromptOptimizationError,
    H3PromptOptimizationResult,
    H3PromptOptimizer,
    H3PromptStructuredOutput,
)
from novelvideo.media_capabilities.video.h3_director_plan import (
    H3ActionPlan,
    H3CameraPlan,
    H3DialogueCue,
    H3DirectorPlan,
    H3FrameDifference,
    H3ShotPlan,
)
from novelvideo.media_capabilities.video.h3_timeline import H3DirectorSegment
from novelvideo.media_capabilities.video.models import H3Mode
from novelvideo.media_capabilities.video.h3_prompt_quality import H3PromptQualityError


def _segment() -> H3DirectorSegment:
    return H3DirectorSegment(
        segment_id="s1",
        beat_number=1,
        prompt="男人转身看向门口",
        duration_seconds=5,
        first_frame="first.png",
        last_frame="last.png",
        dialogue="别过来",
        speaker="林默",
        tone="压低声音、急促",
    )


def _context() -> H3PromptContext:
    return H3PromptContext(
        visual_description="昏暗走廊里，一个男人站在门边",
        narration="门外的脚步声突然停下",
        prev_summary="男人听见脚步声",
        next_summary="门把手开始转动",
        first_frame_sha256="a" * 64,
        last_frame_sha256="b" * 64,
        model_id="DC-h3-prompt-optimizer-LLM",
    )


class FakeAgent:
    def __init__(self, output: H3PromptStructuredOutput):
        self.output = output
        self.calls = []

    async def run(self, task):
        self.calls.append(task)
        return SimpleNamespace(output=self.output)


def test_production_optimizer_uses_prompted_output_without_tool_choice(
    monkeypatch, tmp_path
):
    captured = {}

    class CapturingAgent:
        def __init__(self, model, **kwargs):
            captured["model"] = model
            captured.update(kwargs)

    monkeypatch.setattr(h3_prompt_optimizer, "Agent", CapturingAgent)
    model = object()

    h3_prompt_optimizer.create_h3_prompt_optimizer(
        cache_dir=tmp_path,
        director_model_factory=lambda: model,
        model_settings={"openai_reasoning_effort": "low"},
    )

    assert isinstance(captured["output_type"], PromptedOutput)
    assert captured["output_type"].outputs is H3DirectorPlan
    assert captured["model"] is model
    assert captured["retries"] == {"tools": 1, "output": 3}


@pytest.mark.asyncio
async def test_optimizer_renders_typed_content_with_fixed_fl2va_structure(tmp_path):
    agent = FakeAgent(_director_plan(H3Mode.FL2VA))
    result = await H3PromptOptimizer(agent, tmp_path).optimize_segment(
        _segment(), _context(), H3Mode.FL2VA
    )

    assert isinstance(result, H3PromptOptimizationResult)
    assert result.cache_hit is False
    assert result.format_version == 4
    assert result.plan.mode is H3Mode.FL2VA
    assert result.quality_report.passed is True
    assert "Picture 2 (from Shot 1) aligns with the 5.00-second mark" in result.prompt
    assert "<d>[Chinese]别过来</d>" in result.prompt
    assert "without rewriting" in agent.calls[0]


@pytest.mark.asyncio
async def test_optimizer_caches_complete_result_by_segment_input_hash(tmp_path):
    agent = FakeAgent(_director_plan())
    optimizer = H3PromptOptimizer(agent, tmp_path)

    first = await optimizer.optimize_segment(_segment(), _context(), H3Mode.I2VA)
    second = await optimizer.optimize_segment(_segment(), _context(), H3Mode.I2VA)

    assert len(agent.calls) == 1
    assert second.prompt == first.prompt
    assert second.input_hash == first.input_hash
    assert second.cache_hit is True
    snapshot = json.loads(next(tmp_path.glob("*.json")).read_text(encoding="utf-8"))
    assert snapshot["prompt_profile_id"] == "minimax-h3-director"
    assert snapshot["prompt_profile_version"] == 4
    assert snapshot["compiler_version"] == 1


@pytest.mark.asyncio
async def test_optimizer_quality_failure_raises_before_writing_cache(tmp_path):
    plan = _director_plan()
    vague = plan.shots[0].actions[1].model_copy(
        update={"description": "The person moves naturally."}
    )
    shot = plan.shots[0].model_copy(
        update={"actions": (plan.shots[0].actions[0], vague, plan.shots[0].actions[2])}
    )
    agent = FakeAgent(plan.model_copy(update={"shots": (shot,)}))

    with pytest.raises(H3PromptQualityError, match="vague_action"):
        await H3PromptOptimizer(agent, tmp_path).optimize_segment(
            _segment(), _context(), H3Mode.I2VA
        )

    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_optimizer_feeds_quality_report_back_to_director_then_succeeds(tmp_path):
    valid_plan = _director_plan()
    vague = valid_plan.shots[0].actions[1].model_copy(
        update={"description": "The person moves naturally."}
    )
    vague_shot = valid_plan.shots[0].model_copy(
        update={
            "actions": (
                valid_plan.shots[0].actions[0],
                vague,
                valid_plan.shots[0].actions[2],
            )
        }
    )
    invalid_plan = valid_plan.model_copy(update={"shots": (vague_shot,)})

    class RevisingAgent:
        def __init__(self):
            self.outputs = [invalid_plan, valid_plan]
            self.tasks = []

        async def run(self, task):
            self.tasks.append(task)
            return SimpleNamespace(output=self.outputs.pop(0))

    agent = RevisingAgent()
    result = await H3PromptOptimizer(
        agent, tmp_path, quality_revisions=2
    ).optimize_segment(_segment(), _context(), H3Mode.I2VA)

    assert result.quality_report.passed is True
    assert len(agent.tasks) == 2
    assert "vague_action" in agent.tasks[1]
    assert "shots.0.actions.1" in agent.tasks[1]
    assert len(list(tmp_path.glob("*.json"))) == 1


@pytest.mark.asyncio
async def test_optimizer_rejects_after_quality_revision_budget_is_exhausted(tmp_path):
    plan = _director_plan()
    vague = plan.shots[0].actions[1].model_copy(
        update={"description": "The person moves naturally."}
    )
    shot = plan.shots[0].model_copy(
        update={"actions": (plan.shots[0].actions[0], vague, plan.shots[0].actions[2])}
    )

    agent = FakeAgent(plan.model_copy(update={"shots": (shot,)}))
    with pytest.raises(H3PromptQualityError, match="vague_action"):
        await H3PromptOptimizer(
            agent, tmp_path, quality_revisions=2
        ).optimize_segment(_segment(), _context(), H3Mode.I2VA)

    assert len(agent.calls) == 3
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_optimizer_failure_raises_and_never_returns_or_caches_draft(tmp_path):
    class FailingAgent:
        async def run(self, task):
            raise RuntimeError("provider unavailable")

    optimizer = H3PromptOptimizer(FailingAgent(), tmp_path)
    with pytest.raises(H3PromptOptimizationError, match="provider unavailable"):
        await optimizer.optimize_segment(_segment(), _context(), H3Mode.I2VA)

    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_optimizer_retries_transient_connection_error_then_succeeds(tmp_path):
    output = _director_plan()

    class FlakyAgent:
        def __init__(self):
            self.calls = 0

        async def run(self, task):
            self.calls += 1
            if self.calls < 3:
                transport_error = httpx.ConnectError("relay disconnected")
                raise ModelAPIError("deepseek-v4-flash", "Connection error.") from transport_error
            return SimpleNamespace(output=output)

    agent = FlakyAgent()
    optimizer = H3PromptOptimizer(
        agent,
        tmp_path,
        max_attempts=3,
        retry_base_delay_seconds=0,
    )

    result = await optimizer.optimize_segment(_segment(), _context(), H3Mode.I2VA)

    assert result.prompt
    assert agent.calls == 3


@pytest.mark.asyncio
async def test_optimizer_does_not_retry_non_transient_http_400(tmp_path):
    class InvalidRequestAgent:
        def __init__(self):
            self.calls = 0

        async def run(self, task):
            self.calls += 1
            raise ModelHTTPError(
                status_code=400,
                model_name="deepseek-v4-flash",
                body={"message": "invalid request"},
            )

    agent = InvalidRequestAgent()
    optimizer = H3PromptOptimizer(
        agent,
        tmp_path,
        max_attempts=3,
        retry_base_delay_seconds=0,
    )

    with pytest.raises(H3PromptOptimizationError, match="status_code: 400"):
        await optimizer.optimize_segment(_segment(), _context(), H3Mode.I2VA)

    assert agent.calls == 1


@pytest.mark.asyncio
async def test_optimizer_reports_attempts_after_connection_retries_exhausted(tmp_path):
    class OfflineAgent:
        def __init__(self):
            self.calls = 0

        async def run(self, task):
            self.calls += 1
            transport_error = httpx.ConnectTimeout("relay timeout")
            raise ModelAPIError("deepseek-v4-flash", "Connection error.") from transport_error

    agent = OfflineAgent()
    optimizer = H3PromptOptimizer(
        agent,
        tmp_path,
        max_attempts=3,
        retry_base_delay_seconds=0,
    )

    with pytest.raises(
        H3PromptOptimizationError,
        match=r"Connection error.*3 attempts.*ModelAPIError",
    ):
        await optimizer.optimize_segment(_segment(), _context(), H3Mode.I2VA)

    assert agent.calls == 3


@pytest.mark.asyncio
async def test_i2va_alignment_uses_official_image_one_zero_timestamp(tmp_path):
    agent = FakeAgent(_director_plan())
    result = await H3PromptOptimizer(agent, tmp_path).optimize_segment(
        _segment().model_copy(update={"last_frame": None}), _context(), H3Mode.I2VA
    )
    assert result.prompt.startswith("For the target video, at 0.00 seconds")
    assert "Picture 2" not in result.prompt


@pytest.mark.asyncio
async def test_fl2va_alignment_uses_actual_fractional_end_timestamp(tmp_path):
    agent = FakeAgent(_director_plan(H3Mode.FL2VA, total_frames=102))
    result = await H3PromptOptimizer(agent, tmp_path).optimize_segment(
        _segment().model_copy(update={"duration_seconds": 4.25}), _context(), H3Mode.FL2VA
    )
    assert "Picture 2 (from Shot 1) aligns with the 4.25-second mark" in result.prompt


@pytest.mark.asyncio
async def test_dialogue_tone_is_optional(tmp_path):
    agent = FakeAgent(_director_plan())
    segment = _segment().model_copy(update={"tone": ""})

    result = await H3PromptOptimizer(agent, tmp_path).optimize_segment(
        segment, _context(), H3Mode.I2VA
    )

    assert "林默 (S1) says: <d>[Chinese]别过来</d>" in result.prompt


@pytest.mark.asyncio
async def test_typed_output_validation_is_wrapped_and_not_cached(tmp_path):
    agent = FakeAgent({"integrated_multimodal_description": "缺少音频字段"})
    with pytest.raises(H3PromptOptimizationError, match="typed director plan"):
        await H3PromptOptimizer(agent, tmp_path).optimize_segment(_segment(), _context(), H3Mode.I2VA)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_dialogue_intent_without_dialogue_fails_closed_before_agent_call(tmp_path):
    segment = _segment().model_copy(update={"dialogue": ""})
    agent = FakeAgent(_director_plan())
    with pytest.raises(H3PromptOptimizationError, match="dialogue"):
        await H3PromptOptimizer(agent, tmp_path).optimize_segment(segment, _context(), H3Mode.I2VA)
    assert agent.calls == []
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_explicit_dialogue_required_with_all_cue_fields_empty_fails_closed(tmp_path):
    segment = _segment().model_copy(update={"dialogue": "", "speaker": "", "tone": ""})
    context = _context().model_copy(update={"dialogue_required": True})
    agent = FakeAgent(_director_plan(dialogue="", speaker=""))

    with pytest.raises(H3PromptOptimizationError, match="dialogue is required"):
        await H3PromptOptimizer(agent, tmp_path).optimize_segment(segment, context, H3Mode.I2VA)

    assert agent.calls == []
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_explicit_silent_segment_with_all_cue_fields_empty_is_valid(tmp_path):
    segment = _segment().model_copy(update={"dialogue": "", "speaker": "", "tone": ""})
    context = _context().model_copy(update={"dialogue_required": False})
    agent = FakeAgent(_director_plan(dialogue="", speaker=""))

    result = await H3PromptOptimizer(agent, tmp_path).optimize_segment(segment, context, H3Mode.I2VA)

    assert result.prompt
    assert len(agent.calls) == 1


@pytest.mark.asyncio
async def test_optimizer_hashes_unsafe_segment_id_for_cache_path(tmp_path):
    agent = FakeAgent(_director_plan())
    optimizer = H3PromptOptimizer(agent, tmp_path / "cache")
    segment = _segment().model_copy(update={"segment_id": "../../escape"})

    result = await optimizer.optimize_segment(segment, _context(), H3Mode.I2VA)

    assert result.cache_hit is False
    cache_files = list((tmp_path / "cache").glob("*.json"))
    assert len(cache_files) == 1
    assert ".." not in cache_files[0].name
    assert cache_files[0].parent == tmp_path / "cache"


def test_h3_task_contains_versioned_director_rules_and_context():
    context = _context().model_copy(
        update={"director_context": '{"camera":{"azim":12},"actors":[{"name":"林默"}]}' }
    )

    task = h3_prompt_optimizer._build_task(_segment(), context, H3Mode.FL2VA)

    assert "H3_DIRECTOR_PROFILE" in task
    assert "Actions must cover every frame" in task
    assert "camera" in task and "amplitude" in task and "speed" in task
    assert "action pacing or physical effort" in task
    assert "visible end state" in task
    assert "teleport" in task and "Picture 2" in task
    assert "invent visible text, UI" in task
    assert "林默" in task


def test_shared_compile_and_quality_gate_rejects_mode_mismatch():
    with pytest.raises(ValueError, match="does not match"):
        h3_prompt_optimizer.compile_and_gate_h3_plan(
            _director_plan(H3Mode.FL2VA),
            segment=_segment(),
            context=_context(),
            mode=H3Mode.I2VA,
            input_hash="a" * 64,
        )


def _director_plan(
    mode: H3Mode = H3Mode.I2VA,
    *,
    dialogue: str = "别过来",
    speaker: str = "林默",
    total_frames: int = 120,
) -> H3DirectorPlan:
    dialogue_cues = (
        H3DialogueCue(
            start_frame=48,
            end_frame=min(84, total_frames),
            speaker=speaker,
            speaker_id="S1",
            text=dialogue,
            language="Chinese",
        ),
    ) if dialogue else ()
    settle_start = total_frames - 24
    return H3DirectorPlan(
        mode=mode,
        total_frames=total_frames,
        visual_style="cinematic realism",
        continuity_locks=("preserve identity and corridor geography",),
        shots=(
            H3ShotPlan(
                shot_id="1",
                start_frame=0,
                end_frame=total_frames,
                framing="medium shot",
                angle="eye level",
                focus="Lin Mo",
                composition="Lin Mo remains left of the doorway",
                camera=H3CameraPlan(
                    type="push in",
                    direction="forward",
                    amplitude="subtle",
                    speed="slow and steady",
                ),
                actions=(
                    H3ActionPlan(
                        phase="establish",
                        start_frame=0,
                        end_frame=24,
                        description="Hold the exact Picture 1 pose and corridor layout.",
                    ),
                    H3ActionPlan(
                        phase="execute",
                        start_frame=24,
                        end_frame=settle_start,
                        description="Lin Mo turns his head toward the doorway and braces his shoulder.",
                    ),
                    H3ActionPlan(
                        phase="settle",
                        start_frame=settle_start,
                        end_frame=total_frames,
                        description="His gaze locks on the handle as the camera settles.",
                    ),
                ),
                dialogue=dialogue_cues,
            ),
        ),
        frame_differences=(
            (
                H3FrameDifference(
                    description="Lin Mo reaches the exact Picture 2 head angle.",
                    convergence_frame=settle_start,
                ),
            )
            if mode is H3Mode.FL2VA
            else ()
        ),
        soundscape="Footsteps stop and the lock clicks.",
        music="Low strings tighten without masking dialogue.",
    )


@pytest.mark.asyncio
async def test_planner_returns_typed_plan_then_quality_gates_and_compiles(tmp_path):
    agent = FakeAgent(_director_plan())

    result = await H3PromptOptimizer(agent, tmp_path).optimize_segment(
        _segment().model_copy(update={"last_frame": None}),
        _context().model_copy(update={"last_frame_sha256": None}),
        H3Mode.I2VA,
    )

    assert result.plan.shots[0].camera.type == "push in"
    assert result.quality_report.passed is True
    assert result.prompt.startswith("For the target video, at 0.00 seconds")
    assert "<d>[Chinese]别过来</d>" in result.prompt


def test_production_optimizer_accepts_generic_director_model_factory(monkeypatch, tmp_path):
    captured = {}

    class CapturingAgent:
        def __init__(self, model, **kwargs):
            captured["model"] = model
            captured.update(kwargs)

    model = object()
    monkeypatch.setattr(h3_prompt_optimizer, "Agent", CapturingAgent)

    h3_prompt_optimizer.create_h3_prompt_optimizer(
        cache_dir=tmp_path,
        director_model_factory=lambda: model,
        model_settings={},
    )

    assert captured["model"] is model
    assert captured["output_type"].outputs is H3DirectorPlan


def test_default_director_model_uses_h3_text_runtime_factory(monkeypatch):
    import novelvideo.config as config

    calls = []
    configured_model = object()

    def get_newapi_text_pydantic_model(*args, **kwargs):
        calls.append((args, kwargs))
        return configured_model

    monkeypatch.setattr(
        config,
        "get_newapi_text_pydantic_model",
        get_newapi_text_pydantic_model,
    )

    result = h3_prompt_optimizer._default_director_model_factory()

    assert result is configured_model
    assert calls == [
        (("H3_PROMPT_OPTIMIZER_MODEL", "DC-h3-prompt-optimizer-LLM"), {})
    ]
