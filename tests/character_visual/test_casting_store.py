from pathlib import Path

import pytest
from PIL import Image

from novelvideo.character_visual.casting_models import CastingCandidate
from tests.character_visual.test_casting_compiler import compile_inputs, inputs


def pending(candidate_id="c1"):
    snapshot = compile_inputs(inputs())
    return CastingCandidate(candidate_id=candidate_id, project_id="project", character_id=snapshot.character_id,
                            snapshot=snapshot, task_id="task1")


def store_at(tmp_path):
    from novelvideo.character_visual.casting_store import CastingCandidateStore
    return CastingCandidateStore(tmp_path / "output", state_dir=tmp_path / "state", project_id="project")


def test_pending_is_detached_idempotent_and_conflicts(tmp_path):
    store = store_at(tmp_path)
    candidate = pending()
    saved = store.create_pending(candidate)
    assert store.create_pending(candidate) == saved
    candidate.snapshot.proposal_snapshot["title"] = "mutated"
    assert store.get("c1").snapshot.proposal_snapshot["title"] != "mutated"
    with pytest.raises(ValueError):
        store.create_pending(candidate)
    assert store_at(tmp_path).get("c1") == saved


def test_claim_and_image_completion_are_immutable_and_restart_safe(tmp_path):
    store = store_at(tmp_path)
    store.create_pending(pending())
    assert store.claim_generation("c1", task_id="task1") is True
    assert store.claim_generation("c1", task_id="task1") is False
    source = tmp_path / "output" / "source.png"
    source.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (8, 8), "red").save(source)
    done = store.complete_generation("c1", source)
    immutable_bytes = Path(done.asset_path).read_bytes()
    Image.new("RGB", (8, 8), "blue").save(source)
    assert Path(store_at(tmp_path).get("c1").asset_path).read_bytes() == immutable_bytes
    assert store.complete_generation("c1", source) == done
    assert not store.claim_generation("c1", task_id="task1")
    assert store.create_pending(pending()) == done


@pytest.mark.parametrize("kind", ["external", "symlink", "invalid", "traversal"])
def test_rejects_unsafe_and_nonimage_outputs(tmp_path, kind):
    store = store_at(tmp_path)
    store.create_pending(pending())
    store.claim_generation("c1", task_id="task1")
    root = tmp_path / "output"
    root.mkdir(exist_ok=True)
    outside = tmp_path / "outside.png"
    Image.new("RGB", (8, 8)).save(outside)
    source = outside
    if kind == "symlink":
        source = root / "link.png"
        source.symlink_to(outside)
    elif kind == "invalid":
        source = root / "invalid.png"
        source.write_text("not an image")
    elif kind == "traversal":
        source = root / ".." / "outside.png"
    with pytest.raises(ValueError):
        store.complete_generation("c1", source)
    assert store.get("c1").generation_status == "running"


def test_failure_is_terminal_and_wrong_project_or_hash_is_rejected(tmp_path):
    store = store_at(tmp_path)
    candidate = pending()
    wrong = candidate.model_copy(update={"project_id": "other"})
    with pytest.raises(ValueError): store.create_pending(wrong)
    store.create_pending(candidate)
    store.claim_generation("c1", task_id="task1")
    store.fail_generation("c1", "uncertain provider submission")
    assert not store.claim_generation("c1", task_id="task1")
    candidate = pending("c2")
    candidate.snapshot.proposal_snapshot["title"] = "changed"
    with pytest.raises(ValueError): store.create_pending(candidate)


def test_workspace_compare_and_swap_and_stage_independence(tmp_path):
    from novelvideo.character_visual.models import CharacterVisualWorkspace
    from novelvideo.character_visual.store import CharacterVisualWorkspaceStore
    revision, proposal, profile = inputs()
    store = CharacterVisualWorkspaceStore(tmp_path / "output", state_dir=tmp_path / "state")
    original = CharacterVisualWorkspace(character_id=profile.character_id, profile=profile)
    store.save(original)
    base = store.mutate_casting_revision(profile.character_id, identity_id=None, expected_revision=None, revision=revision)
    stage = revision.model_copy(update={"identity_id": "older", "revision_id": "older-v1"})
    store.mutate_casting_revision(profile.character_id, identity_id="older", expected_revision=None, revision=stage)
    with pytest.raises(ValueError, match="revision"):
        store.mutate_casting_revision(profile.character_id, identity_id=None, expected_revision=None, revision=revision)
    store.save_many([original])
    restored = store.get(profile.character_id)
    assert restored.casting_revision == base.casting_revision
    assert restored.identity_casting_revisions["older"] == stage
    assert CharacterVisualWorkspace.model_validate(original.model_dump(exclude={"identity_casting_revisions"})).identity_casting_revisions == {}


def test_workspace_concurrent_writers_have_one_winner(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from novelvideo.character_visual.models import CharacterVisualWorkspace
    from novelvideo.character_visual.store import CharacterVisualWorkspaceStore
    revision, _, profile = inputs()
    store = CharacterVisualWorkspaceStore(tmp_path)
    store.save(CharacterVisualWorkspace(character_id=profile.character_id, profile=profile))
    barrier = Barrier(2)
    def mutate(i):
        barrier.wait()
        try:
            store.mutate_casting_revision(profile.character_id, identity_id=None, expected_revision=None,
                                          revision=revision.model_copy(update={"revision_id": f"revision-{i}"}))
            return True
        except ValueError:
            return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(mutate, [1, 2])) == [False, True]


def test_candidate_stage_listing_isolated(tmp_path):
    from novelvideo.character_visual.casting_compiler import snapshot_digest
    store = store_at(tmp_path)
    first = pending()
    store.create_pending(first)
    snapshot = first.snapshot.model_dump(mode="json", exclude={"snapshot_hash"})
    snapshot["identity_id"] = "older"
    snapshot["snapshot_hash"] = snapshot_digest(snapshot)
    second = CastingCandidate.model_validate({**first.model_dump(mode="json"), "candidate_id": "c2",
                                             "identity_id": "older", "snapshot": snapshot})
    store.create_pending(second)
    assert [x.candidate_id for x in store.list_candidates(first.character_id)] == ["c1"]
    assert [x.candidate_id for x in store.list_candidates(first.character_id, "older")] == ["c2"]


def test_concurrent_claims_have_one_winner(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    store = store_at(tmp_path)
    store.create_pending(pending())
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: store_at(tmp_path).claim_generation("c1", task_id="task1"), range(2)))
    assert sorted(results) == [False, True]
