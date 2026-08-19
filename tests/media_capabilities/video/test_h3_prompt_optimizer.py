from types import SimpleNamespace

import pytest

from novelvideo.media_capabilities.video.h3_prompt_optimizer import (
    H3PromptContext,
    H3PromptOptimizationError,
    H3PromptOptimizationResult,
    H3PromptOptimizer,
    H3PromptStructuredOutput,
)
from novelvideo.media_capabilities.video.h3_timeline import H3DirectorSegment
from novelvideo.media_capabilities.video.models import H3Mode


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


@pytest.mark.asyncio
async def test_optimizer_renders_typed_content_with_fixed_fl2va_structure(tmp_path):
    agent = FakeAgent(
        H3PromptStructuredOutput(
            integrated_multimodal_description="镜头缓慢推近，男人转身看向门口。",
            overall_soundscape="脚步声停止，门锁轻响。",
            non_diegetic_music="低沉弦乐逐渐增强。",
        )
    )
    result = await H3PromptOptimizer(agent, tmp_path).optimize_segment(
        _segment(), _context(), H3Mode.FL2VA
    )

    assert isinstance(result, H3PromptOptimizationResult)
    assert result.cache_hit is False
    assert result.format_version == 2
    assert result.prompt == (
        "How the reference pictures align with the target video — Picture 1 (from Shot 1) "
        "aligns with the 0.00-second mark of the target video; Picture 2 (from Shot 1) "
        "aligns with the 5.00-second mark of the target video.\n\n"
        "integrated_multimodal_description: 镜头缓慢推近，男人转身看向门口。\n"
        "林默 (S1) says with 压低声音、急促 delivery: <d>[Chinese]别过来</d>\n\n"
        "overall_soundscape: 脚步声停止，门锁轻响。\n\n"
        "non_diegetic_music: 低沉弦乐逐渐增强。"
    )
    assert "English" in agent.calls[0]
    assert "不得改写" in agent.calls[0]


@pytest.mark.asyncio
async def test_optimizer_caches_complete_result_by_segment_input_hash(tmp_path):
    agent = FakeAgent(
        H3PromptStructuredOutput(
            integrated_multimodal_description="镜头前推。",
            overall_soundscape="风声。",
            non_diegetic_music="无。",
        )
    )
    optimizer = H3PromptOptimizer(agent, tmp_path)

    first = await optimizer.optimize_segment(_segment(), _context(), H3Mode.I2VA)
    second = await optimizer.optimize_segment(_segment(), _context(), H3Mode.I2VA)

    assert len(agent.calls) == 1
    assert second.prompt == first.prompt
    assert second.input_hash == first.input_hash
    assert second.cache_hit is True


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
async def test_i2va_alignment_uses_official_image_one_zero_timestamp(tmp_path):
    agent = FakeAgent(H3PromptStructuredOutput(integrated_multimodal_description="镜头前推。", overall_soundscape="风声。", non_diegetic_music="无。"))
    result = await H3PromptOptimizer(agent, tmp_path).optimize_segment(
        _segment().model_copy(update={"last_frame": None}), _context(), H3Mode.I2VA
    )
    assert result.prompt.startswith("For the target video, at 0.00 seconds")
    assert "Picture 2" not in result.prompt


@pytest.mark.asyncio
async def test_fl2va_alignment_uses_actual_fractional_end_timestamp(tmp_path):
    agent = FakeAgent(H3PromptStructuredOutput(integrated_multimodal_description="镜头前推。", overall_soundscape="风声。", non_diegetic_music="无。"))
    result = await H3PromptOptimizer(agent, tmp_path).optimize_segment(
        _segment().model_copy(update={"duration_seconds": 4.25}), _context(), H3Mode.FL2VA
    )
    assert "Picture 2 (from Shot 1) aligns with the 4.25-second mark" in result.prompt


@pytest.mark.asyncio
async def test_dialogue_tone_is_optional(tmp_path):
    agent = FakeAgent(H3PromptStructuredOutput(
        integrated_multimodal_description="[Shot 1] The man braces the door.",
        overall_soundscape="The door rattles.",
        non_diegetic_music="N/A",
    ))
    segment = _segment().model_copy(update={"tone": ""})

    result = await H3PromptOptimizer(agent, tmp_path).optimize_segment(
        segment, _context(), H3Mode.I2VA
    )

    assert "林默 (S1) says: <d>[Chinese]别过来</d>" in result.prompt


@pytest.mark.asyncio
async def test_typed_output_validation_is_wrapped_and_not_cached(tmp_path):
    agent = FakeAgent({"integrated_multimodal_description": "缺少音频字段"})
    with pytest.raises(H3PromptOptimizationError, match="typed output"):
        await H3PromptOptimizer(agent, tmp_path).optimize_segment(_segment(), _context(), H3Mode.I2VA)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_dialogue_intent_without_dialogue_fails_closed_before_agent_call(tmp_path):
    segment = _segment().model_copy(update={"dialogue": ""})
    agent = FakeAgent(H3PromptStructuredOutput(integrated_multimodal_description="镜头前推。", overall_soundscape="风声。", non_diegetic_music="无。"))
    with pytest.raises(H3PromptOptimizationError, match="dialogue"):
        await H3PromptOptimizer(agent, tmp_path).optimize_segment(segment, _context(), H3Mode.I2VA)
    assert agent.calls == []
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_explicit_dialogue_required_with_all_cue_fields_empty_fails_closed(tmp_path):
    segment = _segment().model_copy(update={"dialogue": "", "speaker": "", "tone": ""})
    context = _context().model_copy(update={"dialogue_required": True})
    agent = FakeAgent(H3PromptStructuredOutput(integrated_multimodal_description="镜头前推。", overall_soundscape="风声。", non_diegetic_music="无。"))

    with pytest.raises(H3PromptOptimizationError, match="dialogue is required"):
        await H3PromptOptimizer(agent, tmp_path).optimize_segment(segment, context, H3Mode.I2VA)

    assert agent.calls == []
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_explicit_silent_segment_with_all_cue_fields_empty_is_valid(tmp_path):
    segment = _segment().model_copy(update={"dialogue": "", "speaker": "", "tone": ""})
    context = _context().model_copy(update={"dialogue_required": False})
    agent = FakeAgent(H3PromptStructuredOutput(integrated_multimodal_description="镜头前推。", overall_soundscape="风声。", non_diegetic_music="无。"))

    result = await H3PromptOptimizer(agent, tmp_path).optimize_segment(segment, context, H3Mode.I2VA)

    assert result.prompt
    assert len(agent.calls) == 1


@pytest.mark.asyncio
async def test_optimizer_hashes_unsafe_segment_id_for_cache_path(tmp_path):
    agent = FakeAgent(H3PromptStructuredOutput(
        integrated_multimodal_description="镜头前推。",
        overall_soundscape="风声。",
        non_diegetic_music="无。",
    ))
    optimizer = H3PromptOptimizer(agent, tmp_path / "cache")
    segment = _segment().model_copy(update={"segment_id": "../../escape"})

    result = await optimizer.optimize_segment(segment, _context(), H3Mode.I2VA)

    assert result.cache_hit is False
    cache_files = list((tmp_path / "cache").glob("*.json"))
    assert len(cache_files) == 1
    assert ".." not in cache_files[0].name
    assert cache_files[0].parent == tmp_path / "cache"
