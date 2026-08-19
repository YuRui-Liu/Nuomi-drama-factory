from types import SimpleNamespace

import pytest

from novelvideo.media_capabilities.video.h3_prompt_optimizer import (
    H3PromptContext,
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
    assert result.format_version == 1
    assert result.prompt == (
        "mode: fl2va\n\n"
        "frame_alignment:\n首帧为动作起点，尾帧为动作终点；所有运动连续且不可偏离两帧可见事实。\n\n"
        "integrated_multimodal_description:\n镜头缓慢推近，男人转身看向门口。\n"
        "[00:00.000-00:05.000] 林默（压低声音、急促）说：\u201c别过来\u201d\n\n"
        "overall_soundscape:\n脚步声停止，门锁轻响。\n\n"
        "non_diegetic_music:\n低沉弦乐逐渐增强。"
    )
    assert "优先使用中文" in agent.calls[0]
    assert "准确、可辨识" in agent.calls[0]


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
    with pytest.raises(RuntimeError, match="provider unavailable"):
        await optimizer.optimize_segment(_segment(), _context(), H3Mode.I2VA)

    assert list(tmp_path.iterdir()) == []

