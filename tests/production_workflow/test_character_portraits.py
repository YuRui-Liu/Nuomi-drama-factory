from concurrent.futures import ThreadPoolExecutor
import io
from pathlib import Path

import pytest
from PIL import Image

from novelvideo.production_workflow import ProductionWorkflowStore
from novelvideo.production_workflow.character_portraits import (
    commit_character_portrait_current,
    reconcile_character_portrait_canonical,
)
from novelvideo.production_workflow.store import production_workflow_project_lock


def _png(color: str) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (8, 8), color).save(output, format="PNG")
    return output.getvalue()


def test_portrait_commit_rejects_unsafe_character_name_before_writing(tmp_path):
    with pytest.raises(ValueError, match="invalid character name"):
        commit_character_portrait_current(
            state_dir=tmp_path / "state",
            project_dir=tmp_path,
            character_name="../scenes/villain",
            image_bytes=_png("red"),
            actor="test",
        )

    assert not (tmp_path / "assets").exists()
    assert not (tmp_path / "state").exists()


def test_portrait_commit_rolls_back_workflow_and_canonical_on_adopt_failure(
    tmp_path, monkeypatch
):
    state_dir = tmp_path / "state"
    canonical = commit_character_portrait_current(
        state_dir=state_dir,
        project_dir=tmp_path,
        character_name="林昭",
        image_bytes=_png("blue"),
        actor="test",
    )
    workflow_path = state_dir / "production_workflow.json"
    workflow_before = workflow_path.read_bytes()
    canonical_before = canonical.read_bytes()
    versions_root = canonical.parent / "portrait_versions"
    versions_before = {path.name for path in versions_root.iterdir()}

    def fail_adopt(*_args, **_kwargs):
        raise RuntimeError("adoption failed")

    monkeypatch.setattr(ProductionWorkflowStore, "adopt_version", fail_adopt)
    with pytest.raises(RuntimeError, match="adoption failed"):
        commit_character_portrait_current(
            state_dir=state_dir,
            project_dir=tmp_path,
            character_name="林昭",
            image_bytes=_png("red"),
            actor="test",
        )

    assert workflow_path.read_bytes() == workflow_before
    assert canonical.read_bytes() == canonical_before
    assert {path.name for path in versions_root.iterdir()} == versions_before


def test_concurrent_portrait_commits_keep_current_version_and_canonical_aligned(
    tmp_path,
):
    state_dir = tmp_path / "state"

    def publish(color: str) -> None:
        commit_character_portrait_current(
            state_dir=state_dir,
            project_dir=tmp_path,
            character_name="林昭",
            image_bytes=_png(color),
            actor=color,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(publish, ("red", "blue")))

    workflow = ProductionWorkflowStore(state_dir / "production_workflow.json")
    slot, versions = workflow.get_slot("character:林昭:portrait")
    current = versions[slot.current_version_id]
    version_path = tmp_path / current.asset_path
    canonical = tmp_path / "assets" / "characters" / "林昭" / "portrait.png"
    assert current.adoption_status.value == "adopted"
    assert canonical.read_bytes() == version_path.read_bytes()


def test_interrupted_canonical_mirror_is_reconciled_from_immutable_current(
    tmp_path, monkeypatch
):
    state_dir = tmp_path / "state"
    canonical = commit_character_portrait_current(
        state_dir=state_dir,
        project_dir=tmp_path,
        character_name="林昭",
        image_bytes=_png("blue"),
        actor="test",
    )
    original_replace = __import__(
        "novelvideo.production_workflow.character_portraits", fromlist=["os"]
    ).os.replace
    interrupted = False

    def interrupt_once(source, destination):
        nonlocal interrupted
        if Path(destination) == canonical and not interrupted:
            interrupted = True
            raise KeyboardInterrupt("simulated process interruption")
        return original_replace(source, destination)

    monkeypatch.setattr(
        "novelvideo.production_workflow.character_portraits.os.replace",
        interrupt_once,
    )
    with pytest.raises(KeyboardInterrupt, match="simulated process interruption"):
        commit_character_portrait_current(
            state_dir=state_dir,
            project_dir=tmp_path,
            character_name="林昭",
            image_bytes=_png("red"),
            actor="test",
        )
    monkeypatch.setattr(
        "novelvideo.production_workflow.character_portraits.os.replace",
        original_replace,
    )

    workflow = ProductionWorkflowStore(state_dir / "production_workflow.json")
    slot, versions = workflow.get_slot("character:林昭:portrait")
    current_path = tmp_path / versions[slot.current_version_id].asset_path
    assert current_path.read_bytes() != canonical.read_bytes()
    with production_workflow_project_lock(state_dir):
        assert reconcile_character_portrait_canonical(
            workflow=workflow,
            project_dir=tmp_path,
            character_name="林昭",
        )
    assert canonical.read_bytes() == current_path.read_bytes()
