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


@pytest.mark.asyncio
async def test_character_extraction_retries_quality_rejected_visual_proposals():
    prompts: list[str] = []

    def proposal(
        proposal_id: str,
        *,
        face_shape: str,
        facial_feature: str,
        hair_style: str,
        asymmetry_detail: str,
        recommended: bool = False,
    ) -> dict[str, object]:
        return {
            "proposal_id": proposal_id,
            "title": proposal_id,
            "face_shape": face_shape,
            "facial_features": [facial_feature],
            "hair_style": hair_style,
            "distinctive_features": [asymmetry_detail],
            "identity_anchors": [face_shape, facial_feature, hair_style],
            "asymmetry_detail": asymmetry_detail,
            "recommended": recommended,
        }

    rejected = [
        proposal(
            f"shi-0{index}",
            face_shape="长脸",
            facial_feature="窄眼",
            hair_style="短发",
            asymmetry_detail="略有不对称",
            recommended=index == 1,
        )
        for index in range(1, 4)
    ]
    accepted = [
        proposal(
            "shi-01",
            face_shape="长脸，下颌收窄",
            facial_feature="左眉尾有断眉",
            hair_style="短发侧分",
            asymmetry_detail="左眉尾有断眉",
            recommended=True,
        ),
        proposal(
            "shi-02",
            face_shape="方脸，宽下巴",
            facial_feature="右眼下有小痣",
            hair_style="粗硬寸发",
            asymmetry_detail="右眼下有小痣",
        ),
        proposal(
            "shi-03",
            face_shape="菱形脸，高颧骨",
            facial_feature="左嘴角有凹点",
            hair_style="细软长发",
            asymmetry_detail="左嘴角有凹点",
        ),
    ]

    class FakeAgent:
        async def run(self, prompt: str):
            prompts.append(prompt)
            proposals = rejected if len(prompts) == 1 else accepted
            return {
                "characters": [
                    {
                        "name": "石九",
                        "design_proposals": proposals,
                        "evidence": [{"quote": "石九", "kind": "mention"}],
                    }
                ]
            }

    chunk = SourceChunk(
        chunk_id="c-quality",
        chunk_index=0,
        section_type="scene",
        section_label="片段 1",
        source_start=0,
        source_end=4,
        text="石九进门",
    )

    result = await extraction_module.extract_characters_from_chunks(
        [chunk], agent=FakeAgent()
    )

    assert len(prompts) == 2
    assert "individual_structure:required" in prompts[1]
    assert "structure_collision:shi-03" in prompts[1]
    assert result[0].design_proposals[0]["asymmetry_detail"] == "左眉尾有断眉"


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


def test_character_outfit_state_wire_list_is_merged_back_to_mapping():
    chunk = SourceChunk(
        chunk_id="c3",
        chunk_index=0,
        section_type="scene",
        section_label="1-3",
        source_start=0,
        source_end=5,
        text="梁真推开门",
    )
    proposals = [
        {
            "proposal_id": f"proposal-{index}",
            "title": f"方案 {index}",
            "outfit_states": [
                {"state": "default", "description": f"深色外套 {index}"}
            ],
        }
        for index in range(1, 4)
    ]
    output = extraction_module.ChunkCharacterOutput.model_validate(
        {
            "characters": [
                {
                    "name": "梁真",
                    "design_proposals": proposals,
                    "evidence": [{"quote": "梁真", "kind": "mention"}],
                }
            ]
        }
    )

    merged = extraction_module.merge_character_candidates([(chunk, output)])

    assert merged[0].design_proposals[0]["outfit_states"] == {
        "default": "深色外套 1"
    }


def test_character_outfit_state_accepts_legacy_mapping_input():
    proposal = extraction_module.CharacterProposalCandidate.model_validate(
        {
            "proposal_id": "proposal-1",
            "title": "旧格式",
            "outfit_states": {"default": "深色外套"},
        }
    )

    assert [(item.state, item.description) for item in proposal.outfit_states] == [
        ("default", "深色外套")
    ]


def test_character_outfit_state_rejects_invalid_legacy_mapping_values():
    with pytest.raises(ValueError):
        extraction_module.CharacterProposalCandidate.model_validate(
            {
                "proposal_id": "proposal-1",
                "title": "无效旧格式",
                "outfit_states": {"default": None},
            }
        )
