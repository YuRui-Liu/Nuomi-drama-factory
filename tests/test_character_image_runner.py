from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.mark.asyncio
async def test_character_portrait_uses_sqlite_and_persisted_grsai(monkeypatch, tmp_path):
    from novelvideo.character_visual import (
        CharacterNarrativeProfile,
        CharacterVisualBible,
        CharacterVisualWorkspace,
        CharacterVisualWorkspaceStore,
    )
    from novelvideo.models import NovelCharacter
    from novelvideo.task_backend.runners import character_image

    character = NovelCharacter(
        name="小鹿",
        face_prompt="女性，青年，黑色长发，黑色杏眼，白皙肤色，鹅蛋脸",
    )
    CharacterVisualWorkspaceStore(tmp_path).save(
        CharacterVisualWorkspace(
            character_id="小鹿",
            profile=CharacterNarrativeProfile(character_id="小鹿", name="小鹿"),
            visual_bible=CharacterVisualBible(
                character_id="小鹿",
                revision_id="vb-test",
                status="confirmed",
                face_shape="窄鹅蛋脸",
                facial_features=["眼尾微垂", "鼻梁偏直"],
                distinctive_features=["左眉尾浅痣"],
                identity_anchors=["窄鹅蛋脸", "眼尾微垂", "左眉尾浅痣"],
                confirmed_by="tester",
            ),
        )
    )
    calls: dict[str, object] = {}

    class FakeSQLiteStore:
        def __init__(self, project_name, *, output_dir, state_dir):
            calls["store_args"] = (project_name, output_dir, state_dir)

        async def initialize(self):
            calls["sqlite_initialized"] = True

        async def load_graph_state(self):
            calls["state_loaded"] = True

        def get_character(self, name):
            return character if name == character.name else None

        async def close(self):
            calls["sqlite_closed"] = True

    class FailCogneeStore:
        def __init__(self, *_args, **_kwargs):
            raise AssertionError("character image generation must not initialize Cognee")

    async def fake_grsai_image(**kwargs):
        calls["grsai"] = kwargs
        output = Path(kwargs["output_path"])
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"png")
        return output

    monkeypatch.setattr("novelvideo.sqlite_store.SQLiteStore", FakeSQLiteStore)
    monkeypatch.setattr("novelvideo.cognee.CogneeStore", FailCogneeStore)
    monkeypatch.setattr(character_image, "_generate_grsai_image", fake_grsai_image)
    monkeypatch.setattr(
        character_image,
        "get_task_manager",
        lambda: SimpleNamespace(update_progress_for_project=lambda *_a, **_k: None),
    )
    monkeypatch.setattr(
        "novelvideo.api.deps.get_media_capability_store", lambda: object()
    )
    monkeypatch.setattr(
        "novelvideo.api.deps.get_media_credential_resolver", lambda: object()
    )
    monkeypatch.setattr(
        "novelvideo.media_capabilities.runtime.configuration.load_grsai_runtime_configuration",
        lambda *_args: SimpleNamespace(model="gpt-image-2"),
    )
    monkeypatch.setattr(
        "novelvideo.project_config.load_project_config_file",
        lambda *_args: {"ethnicity": "Chinese"},
    )

    ctx = SimpleNamespace(
        owner_project_label="frank/demo",
        owner_username="frank",
        project_name="demo",
        output_dir=tmp_path,
        state_dir=tmp_path,
    )
    result = await character_image._run_character_image(
        {
            "task_type": "character_portrait",
            "scope": "character:小鹿:portrait",
            "payload": {
                "mode": "portrait",
                "character_name": "小鹿",
                "model": "newapi_gpt_image2",
                "output_dir": str(tmp_path),
            },
        },
        ctx,
    )

    assert Path(result["path"]).read_bytes() == b"png"
    assert calls["sqlite_initialized"] is True
    assert calls["state_loaded"] is True
    assert calls["sqlite_closed"] is True
    assert calls["grsai"]["model"] == "gpt-image-2"
    assert "newapi_gpt_image2" not in str(calls["grsai"])


@pytest.mark.asyncio
async def test_character_portrait_refuses_unconfirmed_visual_identity_before_transport(
    monkeypatch, tmp_path
):
    from novelvideo.models import NovelCharacter
    from novelvideo.task_backend.runners import character_image

    character = NovelCharacter(
        name="林默",
        gender="男",
        age_group="youth",
        body_type="清瘦",
        description="二十多岁的广播站值班员，神情警觉",
        face_prompt="",
    )
    captured: dict[str, object] = {}

    async def fake_grsai_image(**kwargs):
        captured.update(kwargs)
        output = Path(kwargs["output_path"])
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"png")
        return output

    monkeypatch.setattr(character_image, "_generate_grsai_image", fake_grsai_image)

    with pytest.raises(RuntimeError) as exc_info:
        await character_image._generate_character_portrait(
            character=character,
            ethnicity="Chinese",
            output_dir=tmp_path,
            style="post_apocalyptic",
            model="gpt-image-2",
            task_type="character_portrait",
            scope="character:林默:portrait",
            update=lambda *_args: None,
        )

    assert "CHARACTER_VISUAL_BIBLE_REQUIRED" in str(exc_info.value)
    assert '"transport_called": false' in str(exc_info.value)
    assert captured == {}


def test_legacy_face_prompt_is_not_silently_trusted_for_portrait():
    from novelvideo.models import NovelCharacter
    from novelvideo.task_backend.runners.character_image import _character_portrait_face_prompt

    character = NovelCharacter(
        name="林默",
        description="广播站值班员",
        face_prompt="雨夜撞门后惊恐回头",
    )

    prompt = _character_portrait_face_prompt(character)
    assert "雨夜撞门后惊恐回头" not in prompt
    assert "source description: 广播站值班员" in prompt
