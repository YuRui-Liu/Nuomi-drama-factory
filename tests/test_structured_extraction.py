from pydantic_ai import PromptedOutput
import pytest

import novelvideo.config as config_module
import novelvideo.structured_extraction as extraction_module
from novelvideo.story_analysis import SourceChunk


def test_structured_character_agent_uses_prompted_output_with_thinking(
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}
    model = object()
    settings = {"openai_reasoning_effort": "low"}

    class CapturingAgent:
        def __init__(self, received_model, **kwargs):
            captured.update(model=received_model, **kwargs)

    monkeypatch.setattr("pydantic_ai.Agent", CapturingAgent)
    monkeypatch.setattr(
        config_module,
        "get_newapi_text_pydantic_model",
        lambda *_args, **_kwargs: model,
    )
    monkeypatch.setattr(
        config_module,
        "get_newapi_text_pydantic_model_settings",
        lambda *_args, **_kwargs: settings,
    )

    extraction_module._create_agent()

    assert captured["model"] is model
    assert captured["model_settings"] is settings
    assert isinstance(captured["output_type"], PromptedOutput)
    assert captured["output_type"].outputs is extraction_module.ChunkCharacterOutput


@pytest.mark.asyncio
async def test_locked_characters_are_redacted_before_model_and_filtered_from_result():
    seen_prompts: list[str] = []

    class FakeAgent:
        async def run(self, prompt: str):
            seen_prompts.append(prompt)
            return {
                "characters": [
                    {
                        "name": "周禾",
                        "evidence": [{"quote": "周禾", "kind": "mention"}],
                    },
                    {
                        "name": "梁真",
                        "role": "记者",
                        "biography": "追查广播站旧案的外采记者",
                        "evidence": [{"quote": "梁真走进广播站", "kind": "mention"}],
                    },
                ]
            }

    chunk = SourceChunk(
        chunk_id="c1",
        chunk_index=0,
        section_type="scene",
        section_label="1-1",
        source_start=0,
        source_end=13,
        text="周禾看向梁真。梁真走进广播站",
    )
    result = await extraction_module.extract_characters_from_chunks(
        [chunk],
        agent=FakeAgent(),
        excluded_names={"周禾"},
    )

    assert all("周禾" not in prompt for prompt in seen_prompts)
    assert [item.name for item in result] == ["梁真"]
    assert result[0].biography == "追查广播站旧案的外采记者"


def test_character_prompt_requires_profile_and_three_distinct_visual_proposals():
    prompt = extraction_module.CHARACTER_EXTRACTION_SYSTEM_PROMPT
    assert "人物小传" in prompt
    assert "三套" in prompt
    assert "identity_anchors" in prompt
    assert "不得根据姓名" in prompt


def test_redaction_placeholder_cannot_become_a_character():
    chunk = SourceChunk(
        chunk_id="c2",
        chunk_index=0,
        section_type="scene",
        section_label="1-2",
        source_start=0,
        source_end=6,
        text="□□推开门。",
    )
    output = extraction_module.ChunkCharacterOutput.model_validate(
        {
            "characters": [
                {
                    "name": "□□",
                    "evidence": [{"quote": "□□推开门", "kind": "mention"}],
                }
            ]
        }
    )
    assert extraction_module.merge_character_candidates([(chunk, output)]) == []
