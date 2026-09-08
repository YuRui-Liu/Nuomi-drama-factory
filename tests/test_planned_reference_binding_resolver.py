from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from PIL import Image

from novelvideo.narrative_groups.planned_binding_service import (
    StaleReferenceBinding,
    build_planned_reference_snapshot,
    resolve_planned_reference_preview,
)
from novelvideo.narrative_groups.planned_bindings import PlannedReferenceBinding
from novelvideo.production_workflow import AdoptionStatus, ProductionWorkflowStore
from novelvideo.api.schemas import NarrativeReferenceResolutionRequest
from pydantic import ValidationError


class _BindingStore:
    def __init__(self, bindings: list[PlannedReferenceBinding]) -> None:
        self.bindings = bindings
        self.reads = 0

    async def list_planned_reference_bindings(
        self, episode_number: int, group_id: str | None = None
    ) -> list[PlannedReferenceBinding]:
        self.reads += 1
        return [
            item
            for item in self.bindings
            if item.episode_number == episode_number
            and (group_id is None or group_id in item.group_ids)
        ]


def _image(path: Path, color: str = "red") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (8, 8), color).save(path)
    return path


def _binding(**updates: object) -> PlannedReferenceBinding:
    fields = {
        "project_id": "p1",
        "episode_number": 1,
        "source_plan_revision_id": "director-r3",
        "asset_kind": "character_identity",
        "entity_id": "alice-youth",
        "asset_slot_id": "character:alice:state:alice-youth",
        "group_ids": ("group-01",),
        "beat_ids": ("beat-07",),
        "shot_ids": ("shot-07",),
        "required": True,
        "status": "ready",
        "resolution": "auto_matched",
        "display_label": "Alice / youth",
    }
    fields.update(updates)
    return PlannedReferenceBinding.create(**fields)  # type: ignore[arg-type]


def _workflow(project_dir: Path, binding: PlannedReferenceBinding) -> ProductionWorkflowStore:
    workflow = ProductionWorkflowStore(project_dir / "state" / "production_workflow.json")
    workflow.register_candidate_version(
        slot_id=binding.asset_slot_id,
        asset_kind="character_state",
        version_id="version-1",
        asset_path=str(_image(project_dir / "assets" / "alice.png")),
        source_attempt_id="attempt-1",
        qc_passed=True,
        generation_metadata=None,
        actor="test",
        at=datetime.now(UTC),
    )
    return workflow


def test_resolution_request_accepts_only_stable_ids_and_revision() -> None:
    request = NarrativeReferenceResolutionRequest.model_validate(
        {
            "selected_binding_ids": ["planned-ref-1"],
            "upload_ids": ["upload-1"],
            "reference_revision": "revision-1",
        }
    )
    assert request.selected_binding_ids == ["planned-ref-1"]
    with pytest.raises(ValidationError):
        NarrativeReferenceResolutionRequest.model_validate(
            {
                "decisions": [],
                "additional_asset_ids": ["asset-by-name"],
                "reference_revision": "revision-1",
            }
        )


@pytest.mark.asyncio
async def test_preview_resolves_exact_current_version_without_writes(tmp_path: Path) -> None:
    binding = _binding()
    binding_store = _BindingStore([binding])
    workflow = _workflow(tmp_path, binding)
    before = workflow.state_path.read_bytes()

    preview = await resolve_planned_reference_preview(
        binding_store,
        workflow,
        project_id="p1",
        episode_number=1,
        group_id="group-01",
        project_dir=tmp_path,
        max_images=8,
    )

    assert workflow.state_path.read_bytes() == before
    assert binding_store.reads == 1
    assert preview.reference_revision
    assert preview.max_images == 8
    assert preview.bindings[0].binding_id == binding.binding_id
    assert preview.bindings[0].version_id == "version-1"
    assert preview.bindings[0].adoption_status == "provisional"
    assert preview.bindings[0].selected_by_default is True
    assert preview.bindings[0].relative_path == "assets/alice.png"
    assert len(preview.bindings[0].sha256) == 64


@pytest.mark.asyncio
async def test_preview_leaves_sqlite_and_workflow_files_byte_identical(
    tmp_path: Path,
) -> None:
    from novelvideo.sqlite_store import SQLiteStore

    binding = _binding()
    store = SQLiteStore(
        "test/planned-preview",
        output_dir=str(tmp_path),
        state_dir=str(tmp_path / "state"),
    )
    workflow = _workflow(tmp_path, binding)
    try:
        await store.replace_planned_reference_bindings_atomic(
            1, ("character_identity",), (binding,)
        )
        before_db = Path(store.db_path).read_bytes()
        before_workflow = workflow.state_path.read_bytes()

        await resolve_planned_reference_preview(
            store,
            workflow,
            project_id="p1",
            episode_number=1,
            group_id="group-01",
            project_dir=tmp_path,
        )

        assert Path(store.db_path).read_bytes() == before_db
        assert workflow.state_path.read_bytes() == before_workflow
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_preview_does_not_fall_back_to_named_asset_for_candidate_version(
    tmp_path: Path,
) -> None:
    binding = _binding()
    workflow = _workflow(tmp_path, binding)
    slot, versions = workflow.get_slot(binding.asset_slot_id)
    version = versions[slot.current_version_id or ""]
    workflow._versions[version.version_id] = version.model_copy(  # noqa: SLF001
        update={"adoption_status": AdoptionStatus.CANDIDATE}
    )
    _image(tmp_path / "assets" / "characters" / "alice-youth.png", "blue")

    preview = await resolve_planned_reference_preview(
        _BindingStore([binding]),
        workflow,
        project_id="p1",
        episode_number=1,
        group_id="group-01",
        project_dir=tmp_path,
    )

    resolved = preview.bindings[0]
    assert resolved.selected_by_default is False
    assert resolved.version_id == ""
    assert resolved.relative_path == ""
    assert "candidate" in resolved.warning


@pytest.mark.asyncio
async def test_snapshot_rejects_revision_after_current_version_changes(tmp_path: Path) -> None:
    binding = _binding()
    store = _BindingStore([binding])
    workflow = _workflow(tmp_path, binding)
    preview = await resolve_planned_reference_preview(
        store,
        workflow,
        project_id="p1",
        episode_number=1,
        group_id="group-01",
        project_dir=tmp_path,
    )
    workflow.register_candidate_version(
        slot_id=binding.asset_slot_id,
        asset_kind="character_state",
        version_id="version-2",
        asset_path=str(_image(tmp_path / "assets" / "alice-2.png", "green")),
        source_attempt_id="attempt-2",
        qc_passed=True,
        generation_metadata=None,
        actor="test",
        at=datetime.now(UTC),
    )
    workflow.adopt_version(
        slot_id=binding.asset_slot_id,
        version_id="version-2",
        actor="test",
        reason="new current",
        at=datetime.now(UTC),
    )

    with pytest.raises(StaleReferenceBinding):
        await build_planned_reference_snapshot(
            store,
            workflow,
            project_id="p1",
            episode_number=1,
            group_id="group-01",
            project_dir=tmp_path,
            selected_binding_ids=(binding.binding_id,),
            upload_ids=(),
            reference_revision=preview.reference_revision,
        )


@pytest.mark.asyncio
async def test_snapshot_freezes_version_digest_and_scope(tmp_path: Path) -> None:
    binding = _binding()
    store = _BindingStore([binding])
    workflow = _workflow(tmp_path, binding)
    preview = await resolve_planned_reference_preview(
        store,
        workflow,
        project_id="p1",
        episode_number=1,
        group_id="group-01",
        project_dir=tmp_path,
    )

    snapshot = await build_planned_reference_snapshot(
        store,
        workflow,
        project_id="p1",
        episode_number=1,
        group_id="group-01",
        project_dir=tmp_path,
        selected_binding_ids=(binding.binding_id,),
        upload_ids=(),
        reference_revision=preview.reference_revision,
    )

    image = snapshot.images[0]
    assert snapshot.schema_version == "narrative-reference-decision/v1"
    assert image.binding_id == binding.binding_id
    assert image.asset_slot_id == binding.asset_slot_id
    assert image.version_id == "version-1"
    assert image.relative_path == "assets/alice.png"
    assert len(image.sha256) == 64
    assert image.group_ids == ("group-01",)
    assert image.beat_ids == ("beat-07",)
    assert image.shot_ids == ("shot-07",)
