from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from novelvideo.narrative_groups.planned_binding_service import (
    PlannedReferencesRequired,
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


def _planned_runner_payload(
    tmp_path: Path, image: Path, *, sha256: str, use_style: bool = True
) -> dict[str, object]:
    return {
        "project_dir": str(tmp_path),
        "project_id": "p1",
        "episode": 1,
        "group_id": "group-01",
        "stage": "render",
        "beats": [{"id": "beat-07", "beat_number": 1, "action": "Alice enters"}],
        "image_projection": "STYLE_PROJECTION",
        "use_style": use_style,
        "reference_scope": {
            "beat_ids": ["beat-07"],
            "shot_ids": ["shot-07"],
        },
        "reference_resolution": {
            "id": "refsnap-planned",
            "schema_version": "narrative-reference-decision/v2",
            "ignored_requirement_ids": [],
            "warnings": [],
            "images": [{
                "requirement_id": "planned-ref-hero",
                "source": "matched",
                "source_id": "version-1",
                "asset_kind": "character_identity",
                "image_path": str(image),
                "resolution": "matched",
                "entity_id": "alice-youth",
                "shot_ids": ["shot-07"],
                "beat_ids": ["beat-07"],
                "group_ids": ["group-01"],
                "binding_id": "planned-ref-hero",
                "asset_slot_id": "character:alice:state:alice-youth",
                "version_id": "version-1",
                "relative_path": "assets/alice.png",
                "sha256": sha256,
                "project_id": "p1",
                "episode_number": 1,
            }],
        },
    }


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
    assert resolved.status == "pending_confirmation"
    assert resolved.selected_by_default is False
    assert resolved.version_id == ""
    assert resolved.relative_path == ""
    assert "candidate" in resolved.warning


@pytest.mark.asyncio
async def test_preview_derives_unavailable_status_from_live_workflow_state(
    tmp_path: Path,
) -> None:
    binding = _binding()
    missing_slot = ProductionWorkflowStore(tmp_path / "missing" / "workflow.json")
    missing_slot_preview = await resolve_planned_reference_preview(
        _BindingStore([binding]),
        missing_slot,
        project_id="p1",
        episode_number=1,
        group_id="group-01",
        project_dir=tmp_path,
    )
    assert missing_slot_preview.bindings[0].status == "missing_asset"

    missing_current = _workflow(tmp_path / "missing-current", binding)
    slot, _ = missing_current.get_slot(binding.asset_slot_id)
    missing_current._slots[binding.asset_slot_id] = slot.model_copy(  # noqa: SLF001
        update={"current_version_id": "deleted-version"}
    )
    missing_current_preview = await resolve_planned_reference_preview(
        _BindingStore([binding]),
        missing_current,
        project_id="p1",
        episode_number=1,
        group_id="group-01",
        project_dir=tmp_path / "missing-current",
    )
    assert missing_current_preview.bindings[0].status == "missing_image"

    missing_image_root = tmp_path / "missing-image"
    missing_image = _workflow(missing_image_root, binding)
    (missing_image_root / "assets" / "alice.png").unlink()
    missing_image_preview = await resolve_planned_reference_preview(
        _BindingStore([binding]),
        missing_image,
        project_id="p1",
        episode_number=1,
        group_id="group-01",
        project_dir=missing_image_root,
    )
    assert missing_image_preview.bindings[0].status == "missing_image"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bindings,active_revision",
    [
        ((_binding(), _binding().model_copy(update={"binding_id": "planned-ref-other", "source_plan_revision_id": "director-r4"})), "director-r3"),
        ((_binding(),), "director-r4"),
    ],
)
async def test_preview_rejects_mixed_or_inactive_plan_revisions(
    tmp_path: Path,
    bindings: tuple[PlannedReferenceBinding, ...],
    active_revision: str,
) -> None:
    workflow = _workflow(tmp_path, bindings[0])

    with pytest.raises(StaleReferenceBinding):
        await resolve_planned_reference_preview(
            _BindingStore(list(bindings)),
            workflow,
            project_id="p1",
            episode_number=1,
            group_id="group-01",
            project_dir=tmp_path,
            active_plan_revision_id=active_revision,
        )


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
async def test_snapshot_reloads_workflow_under_project_lock_before_freezing(
    tmp_path: Path,
) -> None:
    binding = _binding()
    store = _BindingStore([binding])
    writer = _workflow(tmp_path, binding)
    stale_reader = ProductionWorkflowStore(writer.state_path)
    preview = await resolve_planned_reference_preview(
        store,
        stale_reader,
        project_id="p1",
        episode_number=1,
        group_id="group-01",
        project_dir=tmp_path,
    )
    writer.register_candidate_version(
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
    writer.adopt_version(
        slot_id=binding.asset_slot_id,
        version_id="version-2",
        actor="test",
        reason="new current",
        at=datetime.now(UTC),
    )

    with pytest.raises(StaleReferenceBinding):
        await build_planned_reference_snapshot(
            store,
            stale_reader,
            project_id="p1",
            episode_number=1,
            group_id="group-01",
            project_dir=tmp_path,
            selected_binding_ids=(binding.binding_id,),
            upload_ids=(),
            reference_revision=preview.reference_revision,
        )


@pytest.mark.asyncio
async def test_snapshot_holds_workflow_lock_through_version_validation(
    tmp_path: Path, monkeypatch
) -> None:
    import threading

    from novelvideo.narrative_groups import planned_binding_service as service
    from novelvideo.production_workflow.store import production_workflow_project_lock

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
    attempted = threading.Event()
    acquired = threading.Event()
    writer: threading.Thread | None = None
    original = service._preview_from_bindings

    def competing_writer() -> None:
        attempted.set()
        with production_workflow_project_lock(workflow.state_path.parent):
            acquired.set()

    def observed(*args, **kwargs):
        nonlocal writer
        writer = threading.Thread(target=competing_writer)
        writer.start()
        assert attempted.wait(1)
        assert not acquired.wait(0.05)
        return original(*args, **kwargs)

    monkeypatch.setattr(service, "_preview_from_bindings", observed)
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
    assert acquired.wait(1)
    assert writer is not None
    writer.join(timeout=1)


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
    assert snapshot.schema_version == "narrative-reference-decision/v2"
    assert image.binding_id == binding.binding_id
    assert image.asset_slot_id == binding.asset_slot_id
    assert image.version_id == "version-1"
    assert image.relative_path == "assets/alice.png"
    assert len(image.sha256) == 64
    assert image.group_ids == ("group-01",)
    assert image.beat_ids == ("beat-07",)
    assert image.shot_ids == ("shot-07",)


@pytest.mark.asyncio
async def test_snapshot_requires_every_ready_required_binding_to_be_selected(
    tmp_path: Path,
) -> None:
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

    with pytest.raises(PlannedReferencesRequired):
        await build_planned_reference_snapshot(
            store,
            workflow,
            project_id="p1",
            episode_number=1,
            group_id="group-01",
            project_dir=tmp_path,
            selected_binding_ids=(),
            upload_ids=(),
            reference_revision=preview.reference_revision,
        )


def test_runner_rejects_missing_snapshot_instead_of_legacy_name_fallback(
    tmp_path: Path,
) -> None:
    from novelvideo.task_backend.runners.narrative_group import (
        ReferenceSnapshotInvalid,
        _generation_input,
    )

    with pytest.raises(ReferenceSnapshotInvalid):
        _generation_input({"project_dir": str(tmp_path), "beats": []})


def test_runner_rejects_tampered_planned_snapshot_digest(tmp_path: Path) -> None:
    from novelvideo.task_backend.runners.narrative_group import (
        ReferenceSnapshotInvalid,
        _generation_input,
    )

    image = _image(tmp_path / "assets" / "alice.png")
    payload = _planned_runner_payload(tmp_path, image, sha256="0" * 64)

    with pytest.raises(ReferenceSnapshotInvalid):
        _generation_input(payload)

def test_runner_rejects_tampered_temporary_upload_snapshot(tmp_path: Path) -> None:
    import hashlib

    from novelvideo.task_backend.runners.narrative_group import (
        ReferenceSnapshotInvalid,
        _generation_input,
    )

    image = _image(
        tmp_path / ".runtime" / "reference_uploads" / "upload-1.png"
    )
    digest = hashlib.sha256(image.read_bytes()).hexdigest()
    payload = _planned_runner_payload(tmp_path, image, sha256=digest)
    frozen = payload["reference_resolution"]["images"][0]
    frozen.update(
        {
            "source": "upload",
            "source_id": "upload-1",
            "resolution": "temporary",
            "binding_id": "",
            "asset_slot_id": "",
            "version_id": "",
            "relative_path": ".runtime/reference_uploads/upload-1.png",
            "beat_ids": [],
            "shot_ids": [],
        }
    )
    _image(image, "blue")

    with pytest.raises(ReferenceSnapshotInvalid):
        _generation_input(payload)

    frozen["sha256"] = hashlib.sha256(image.read_bytes()).hexdigest()
    frozen["project_id"] = "foreign-project"
    with pytest.raises(ReferenceSnapshotInvalid):
        _generation_input(payload)


@pytest.mark.parametrize(
    "missing_field",
    ("relative_path", "sha256", "project_id", "episode_number", "group_ids"),
)
def test_runner_rejects_planned_snapshot_with_missing_frozen_metadata(
    tmp_path: Path, missing_field: str
) -> None:
    import hashlib

    from novelvideo.task_backend.runners.narrative_group import (
        ReferenceSnapshotInvalid,
        _generation_input,
    )

    image = _image(tmp_path / "assets" / "alice.png")
    payload = _planned_runner_payload(
        tmp_path, image, sha256=hashlib.sha256(image.read_bytes()).hexdigest()
    )
    del payload["reference_resolution"]["images"][0][missing_field]

    with pytest.raises(ReferenceSnapshotInvalid):
        _generation_input(payload)


def test_runner_disables_style_image_and_projection_under_strong_sketch(
    tmp_path: Path, monkeypatch
) -> None:
    import hashlib

    from novelvideo.task_backend.runners import narrative_group

    image = _image(tmp_path / "assets" / "alice.png")
    style = _image(tmp_path / "assets" / "style.png", "green")
    sketch = _image(tmp_path / "sketch.png", "blue")
    payload = _planned_runner_payload(
        tmp_path, image, sha256=hashlib.sha256(image.read_bytes()).hexdigest()
    )
    payload.update(
        {
            "constraint_mode": "strong_sketch",
            "source_sketch_revision": 2,
            "source_sketch_asset": str(sketch),
        }
    )
    payload["reference_resolution"]["style_reference"] = str(style)
    group = SimpleNamespace(
        id="group-01",
        stages={
            "sketch": SimpleNamespace(
                status="completed", revision=2, grid_asset=str(sketch)
            )
        },
    )
    monkeypatch.setattr(
        narrative_group, "load_materialized_groups", lambda *_: [group]
    )

    payload["use_style"] = False
    without_style = narrative_group._generation_input(payload)
    payload["use_style"] = True
    with_style = narrative_group._generation_input(payload)

    assert without_style.references == (str(sketch), str(image))
    assert "STYLE_PROJECTION" not in without_style.prompt
    assert with_style.references == (str(sketch), str(style), str(image))
    assert "STYLE_PROJECTION" in with_style.prompt

def test_runner_validates_planned_scope_and_applies_use_style(tmp_path: Path) -> None:
    import hashlib

    from novelvideo.task_backend.runners.narrative_group import (
        ReferenceSnapshotInvalid,
        _generation_input,
    )

    image = _image(tmp_path / "assets" / "alice.png")
    digest = hashlib.sha256(image.read_bytes()).hexdigest()
    without_style = _generation_input(
        _planned_runner_payload(tmp_path, image, sha256=digest, use_style=False)
    )
    with_style = _generation_input(
        _planned_runner_payload(tmp_path, image, sha256=digest, use_style=True)
    )
    assert "STYLE_PROJECTION" not in without_style.prompt
    assert "STYLE_PROJECTION" in with_style.prompt

    wrong_scope = _planned_runner_payload(tmp_path, image, sha256=digest)
    wrong_scope["group_id"] = "foreign-group"
    with pytest.raises(ReferenceSnapshotInvalid):
        _generation_input(wrong_scope)

    absolute_path = _planned_runner_payload(tmp_path, image, sha256=digest)
    absolute_path["reference_resolution"]["images"][0]["relative_path"] = str(
        image
    )
    with pytest.raises(ReferenceSnapshotInvalid):
        _generation_input(absolute_path)
