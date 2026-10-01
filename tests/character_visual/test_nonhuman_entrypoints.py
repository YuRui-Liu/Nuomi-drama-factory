from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


@pytest.mark.asyncio
async def test_recheck_loads_current_identity_facts(monkeypatch):
    from novelvideo.api.routes import identity_qc as api
    store = SimpleNamespace(get_character=lambda name: SimpleNamespace(
        voice_facts=SimpleNamespace(species="狸状异兽", provenance="source"),
        identities=[SimpleNamespace(identity_id="i", appearance_details="四足，白尾，无衣物")]), close=AsyncMock())
    monkeypatch.setattr(api, "make_sqlite_store_for_context", AsyncMock(return_value=store), raising=False)
    facts = await api._current_identity_facts(object(), "朏朏", "i")
    assert facts == {"expected_appearance": "四足，白尾，无衣物", "nonhuman_species": "狸状异兽"}
    store.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_recheck_runner_forwards_anatomy(monkeypatch):
    from novelvideo.task_backend.runners import character_qc as runner
    monkeypatch.setattr(runner, "current_text_task_runtime", lambda: SimpleNamespace(snapshot=SimpleNamespace(task_role="identity_sheet_qc")))
    monkeypatch.setattr(runner, "read_recheck_image", lambda *a: b"image")
    assess = AsyncMock(return_value="report")
    monkeypatch.setattr(runner, "assess_identity_sheet_quality", assess)
    async def watch(awaitable, *a, **kw):
        return await awaitable
    monkeypatch.setattr(runner, "await_envelope_with_cancel_watch", watch)
    await runner._assess({"payload": {"target": {}, "style": "anime", "style_family": "2d", "expected_appearance": "四足", "nonhuman_species": "狸"}}, SimpleNamespace(output_dir="unused"))
    assert assess.call_args.kwargs["nonhuman_species"] == "狸"
    assert assess.call_args.kwargs["expected_appearance"] == "四足"


def test_legacy_prompt_wrapper_preserves_species():
    from novelvideo.generators.nanobanana_character import build_character_state_sheet_prompt
    prompt = build_character_state_sheet_prompt(character_name="朏朏", character_tag="朏朏", appearance="四足", style_instructions="", avoid_instructions="", ethnicity="Chinese", has_costume_reference=False, nonhuman_species="狸")
    assert "NONHUMAN ANATOMY: 狸" in prompt
