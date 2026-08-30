from pydantic_ai import PromptedOutput

import novelvideo.config as config_module
import novelvideo.structured_extraction as extraction_module


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
