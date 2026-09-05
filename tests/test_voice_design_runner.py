from types import SimpleNamespace

import pytest


@pytest.mark.asyncio
async def test_voice_design_loads_sqlite_character_state_before_lookup(
    monkeypatch, tmp_path
):
    from novelvideo.models import NovelCharacter
    from novelvideo.task_backend.runners import voice_design

    character = NovelCharacter(name="阿远")
    calls: list[str] = []

    class FakeSQLiteStore:
        def __init__(self, *_args, **_kwargs):
            self.loaded = False

        async def initialize(self):
            calls.append("initialize")

        async def load_graph_state(self):
            self.loaded = True
            calls.append("load_graph_state")

        def get_character(self, name):
            calls.append("get_character")
            return character if self.loaded and name == "阿远" else None

        async def update_character(self, *_args, **_kwargs):
            calls.append("update_character")

        async def close(self):
            calls.append("close")

    async def fake_generate(*_args, **_kwargs):
        return b"wav", "sample.wav"

    monkeypatch.setattr("novelvideo.sqlite_store.SQLiteStore", FakeSQLiteStore)
    monkeypatch.setattr(
        "novelvideo.media_capabilities.runtime.configuration.load_runninghub_runtime_configuration",
        lambda *_args: object(),
    )
    monkeypatch.setattr(
        "novelvideo.media_capabilities.tts.runninghub_voice_design.generate_qwen3_voice_sample",
        fake_generate,
    )
    monkeypatch.setattr(
        "novelvideo.seedance2_i2v.character_voice_storage.persist_character_voice_file",
        lambda **_kwargs: ("audio/sample.wav", "sha256", "now"),
    )
    monkeypatch.setattr(
        voice_design,
        "get_task_manager",
        lambda: SimpleNamespace(update_progress_for_project=lambda *_a, **_k: None),
    )
    monkeypatch.setattr("novelvideo.api.deps.get_media_capability_store", lambda: object())
    monkeypatch.setattr("novelvideo.api.deps.get_media_credential_resolver", lambda: object())

    ctx = SimpleNamespace(
        owner_project_label="local/demo",
        output_dir=tmp_path,
        state_dir=tmp_path,
    )
    await voice_design._run_voice_design(
        {
            "scope": "character:阿远:voice:default",
            "payload": {
                "character_name": "阿远",
                "slot": "default",
                "audition_text": "我是阿远。",
                "voice_description": "青年男性，中低音。",
            },
        },
        ctx,
    )

    assert calls.index("load_graph_state") < calls.index("get_character")
