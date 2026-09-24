from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from tests.character_visual.test_casting_store import pending, store_at


def context(tmp_path):
    return SimpleNamespace(output_dir=tmp_path / "output", state_dir=tmp_path / "state", project_id="project",
                           owner_project_label="test/project", owner_username="test", project_name="project")


@pytest.mark.asyncio
async def test_generate_only_exact_snapshot_and_idempotent_replay(tmp_path):
    from novelvideo.task_backend.runners.character_casting import generate_casting_candidate
    store = store_at(tmp_path)
    candidate = pending()
    store.create_pending(candidate)
    current = tmp_path / "output" / "assets" / "characters" / candidate.character_id / "portrait.png"
    current.parent.mkdir(parents=True)
    Image.new("RGB", (8, 8), "blue").save(current)
    before = current.read_bytes()
    from novelvideo.character_visual.models import CharacterVisualBible, CharacterVisualWorkspace
    from novelvideo.character_visual.store import CharacterVisualWorkspaceStore
    from tests.character_visual.test_casting_compiler import inputs
    revision, _, profile = inputs()
    visual_store = CharacterVisualWorkspaceStore(tmp_path / "output", state_dir=tmp_path / "state")
    bible = CharacterVisualBible(character_id=profile.character_id, revision_id="old-bible", status="confirmed",
                                 face_shape="圆脸", facial_features=["浓眉", "圆眼"], identity_anchors=["圆脸", "浓眉", "圆眼"], confirmed_by="test")
    visual_store.save(CharacterVisualWorkspace(character_id=profile.character_id, profile=profile, casting_revision=revision, visual_bible=bible))
    workspace_before = visual_store.path.read_bytes()
    calls = []
    async def generate(**kwargs):
        calls.append(kwargs)
        Path(kwargs["output_path"]).parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (8, 8), "red").save(kwargs["output_path"])
        return Path(kwargs["output_path"])
    kwargs = dict(ctx=context(tmp_path), candidate_id="c1", character_id=candidate.character_id, identity_id=None,
                  task_id="task1", resolution=SimpleNamespace(model="gpt-image-2", requested_model="gpt-image-2", resolution_source="request"), generate=generate)
    first = await generate_casting_candidate(**kwargs)
    assert await generate_casting_candidate(**kwargs) == first
    assert len(calls) == 1
    assert calls[0]["prompt"] == candidate.snapshot.prompt
    assert current.read_bytes() == before
    assert visual_store.path.read_bytes() == workspace_before
    assert not (tmp_path / "state" / "production_workflow.json").exists()
    assert store_at(tmp_path).get("c1").generation_metadata["resolved_model"] == "gpt-image-2"


@pytest.mark.asyncio
async def test_uncertain_running_never_resubmits_and_known_output_recovers(tmp_path):
    from novelvideo.task_backend.runners.character_casting import generate_casting_candidate
    store = store_at(tmp_path)
    candidate = pending()
    store.create_pending(candidate)
    store.claim_generation("c1", task_id="task1")
    async def forbidden(**kwargs): raise AssertionError("must not submit twice")
    kwargs = dict(ctx=context(tmp_path), candidate_id="c1", character_id=candidate.character_id, identity_id=None,
                  task_id="task1", resolution=SimpleNamespace(model="gpt-image-2", requested_model="gpt-image-2", resolution_source="request"), generate=forbidden)
    with pytest.raises(RuntimeError, match="running|uncertain"):
        await generate_casting_candidate(**kwargs)
    assert store.get("c1").generation_status == "running"
    output = store.output_path("c1")
    output.parent.mkdir(parents=True)
    Image.new("RGB", (8, 8)).save(output)
    result = await generate_casting_candidate(**kwargs)
    assert result["candidate_id"] == "c1"


@pytest.mark.asyncio
async def test_failure_and_ownership_do_not_publish(tmp_path):
    from novelvideo.task_backend.runners.character_casting import generate_casting_candidate
    store = store_at(tmp_path)
    candidate = pending()
    store.create_pending(candidate)
    async def failed(**kwargs): raise RuntimeError("provider failed")
    kwargs = dict(ctx=context(tmp_path), candidate_id="c1", character_id=candidate.character_id, identity_id=None,
                  task_id="task1", resolution=SimpleNamespace(model="gpt-image-2", requested_model="gpt-image-2", resolution_source="request"), generate=failed)
    with pytest.raises(ValueError, match="ownership"):
        await generate_casting_candidate(**{**kwargs, "identity_id": "wrong"})
    assert store.get("c1").generation_status == "queued"
    with pytest.raises(RuntimeError, match="provider failed"):
        await generate_casting_candidate(**kwargs)
    assert store_at(tmp_path).get("c1").generation_status == "failed"
    assert not (tmp_path / "output" / "assets" / "characters").exists()


@pytest.mark.asyncio
async def test_runner_uses_stored_model_and_rejects_legacy_publish(monkeypatch, tmp_path):
    from novelvideo.task_backend.runners import character_image
    from novelvideo.character_visual.models import CharacterVisualWorkspace
    from novelvideo.character_visual.store import CharacterVisualWorkspaceStore
    from tests.character_visual.test_casting_compiler import inputs
    candidate = pending().model_copy(update={"requested_model": "gpt-image-2"})
    store_at(tmp_path).create_pending(candidate)
    character = SimpleNamespace(name=candidate.character_id, identities=[])
    class FakeSQLiteStore:
        def __init__(self, *args, **kwargs):
            assert kwargs["state_dir"] == str(tmp_path / "state")
        async def initialize(self): pass
        async def load_graph_state(self): pass
        async def close(self): pass
        def get_character(self, name): return character
    calls = []
    async def generate(**kwargs):
        calls.append(kwargs)
        path = Path(kwargs["output_path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (8, 8)).save(path)
        return path
    monkeypatch.setattr("novelvideo.sqlite_store.SQLiteStore", FakeSQLiteStore)
    monkeypatch.setattr(character_image, "_generate_grsai_image", generate)
    monkeypatch.setattr(character_image, "get_task_manager", lambda: SimpleNamespace(update_progress_for_project=lambda *a, **k: None))
    monkeypatch.setattr("novelvideo.api.deps.get_media_capability_store", lambda: object())
    monkeypatch.setattr("novelvideo.api.deps.get_media_credential_resolver", lambda: object())
    monkeypatch.setattr("novelvideo.media_capabilities.runtime.configuration.load_grsai_runtime_configuration", lambda *a: SimpleNamespace(model="nano-banana-pro"))
    monkeypatch.setattr("novelvideo.project_config.load_project_config_file", lambda *a: {"character_image_selection": "nano-banana-pro"})
    envelope = {"task_id": "task1", "task_type": "character_portrait", "payload": {"mode": "casting_candidate", "candidate_id": "c1", "character_name": candidate.character_id}}
    result = await character_image._run_character_image(envelope, context(tmp_path))
    assert result["candidate_id"] == "c1"
    assert calls[0]["model"] == "gpt-image-2"
    assert calls[0]["prompt"] == candidate.snapshot.prompt
    assert not (tmp_path / "output" / "assets" / "characters").exists()
    revision, proposal, profile = inputs()
    CharacterVisualWorkspaceStore(tmp_path / "output").save(CharacterVisualWorkspace(character_id=profile.character_id, profile=profile, casting_revision=revision))
    envelope["payload"]["mode"] = "portrait"
    with pytest.raises(RuntimeError, match="CASTING"):
        await character_image._run_character_image(envelope, context(tmp_path))
    assert len(calls) == 1
    envelope["payload"]["mode"] = "casting_candidate"
    envelope["payload"]["output_dir"] = str(tmp_path / "other")
    with pytest.raises(ValueError, match="output"):
        await character_image._run_character_image(envelope, context(tmp_path))
