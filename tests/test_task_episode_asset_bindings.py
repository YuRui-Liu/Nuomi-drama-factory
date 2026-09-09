from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import UTC, datetime
import hashlib
import json
import threading
from types import SimpleNamespace

import pytest
from PIL import Image

from novelvideo.models import NovelEpisode, NovelProp, NovelScene, PropMenuItem, SceneMenuItem
from novelvideo.narrative_groups.planned_binding_service import (
    bindings_for_director_plan,
    resolve_planned_reference_preview,
)
from novelvideo.narrative_groups.planned_bindings import PlannedReferenceBinding
from novelvideo.production_workflow import ProductionWorkflowStore
from novelvideo.production_workflow.slot_ids import (
    scene_base_slot_id,
    scene_state_slot_id,
)
from novelvideo.sqlite_store import SQLiteStore
from novelvideo.task_backend.runners.episode_assets import _episode_asset_bindings


def _digest(data: dict) -> str:
    raw = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def _binding(kind: str, entity_id: str) -> PlannedReferenceBinding:
    return PlannedReferenceBinding.create(
        project_id="owner/project",
        episode_number=1,
        source_plan_revision_id="director-r2",
        asset_kind=kind,
        entity_id=entity_id,
        base_entity_id="咖啡馆" if kind == "scene_variant" else "",
        variant_id="暴雨版" if kind == "scene_variant" else "",
        asset_slot_id=f"slot:{kind}:{entity_id}",
        status="ready",
        resolution="auto_matched",
        display_label=entity_id,
    )


def _scene_bindings(
    *requirements: SimpleNamespace,
    scenes: tuple[object, ...],
    available_scene_reference_slots: frozenset[str] | None = None,
):
    shots = tuple(
        SimpleNamespace(
            id=f"shot-{index}",
            dramatic_beat_ids=(f"beat-{index}",),
            asset_requirements=(requirement,),
        )
        for index, requirement in enumerate(requirements, start=1)
    )
    plan = SimpleNamespace(
        revision_id="director-r2",
        groups=tuple(
            SimpleNamespace(
                id=f"group-{index}",
                dramatic_beat_ids=(f"beat-{index}",),
                shots=(shot,),
            )
            for index, shot in enumerate(shots, start=1)
        ),
    )
    values = {
        "project_id": "owner/project",
        "episode_number": 1,
        "source_plan_revision_id": plan.revision_id,
        "groups": plan.groups,
        "shots": shots,
        "characters": (),
        "scenes": scenes,
        "props": (),
    }
    if available_scene_reference_slots is not None:
        values["available_scene_reference_slots"] = available_scene_reference_slots
    return bindings_for_director_plan(
        **values,
    )


def _scene_state(variant_id: str = "暴雨版") -> SimpleNamespace:
    return SimpleNamespace(
        kind="scene_state",
        entity_key="咖啡馆",
        visible_change=variant_id,
        required=True,
    )


class _PlannedBindingStore:
    def __init__(self, binding: PlannedReferenceBinding) -> None:
        self.binding = binding

    async def list_planned_reference_bindings(
        self, episode_number: int, group_id: str | None = None
    ) -> list[PlannedReferenceBinding]:
        if episode_number != self.binding.episode_number:
            return []
        if group_id is not None and group_id not in self.binding.group_ids:
            return []
        return [self.binding]


def test_scene_and_prop_binding_projection_overlays_draft_entities():
    requirements = (
        SimpleNamespace(kind="scene_base", entity_key="咖啡馆", required=True),
        SimpleNamespace(
            kind="scene_state",
            entity_key="咖啡馆",
            visible_change="暴雨版",
            required=True,
        ),
        SimpleNamespace(kind="prop", entity_key="强光手电", required=True),
    )
    shot = SimpleNamespace(id="shot-1", dramatic_beat_ids=("beat-1",), asset_requirements=requirements)
    plan = SimpleNamespace(
        revision_id="director-r2",
        groups=(SimpleNamespace(id="group-1", dramatic_beat_ids=("beat-1",), shots=(shot,)),),
    )
    scenes = (
        NovelScene(name="咖啡馆"),
        NovelScene(name="雨中咖啡馆", base_scene_id="咖啡馆", variant_id="暴雨版"),
    )
    props = (NovelProp(name="强光手电"),)

    scene_bindings = _episode_asset_bindings(
        asset_kind="scene",
        project_id="owner/project",
        episode_number=1,
        director_plan=plan,
        changed_entities=scenes,
        scenes=(),
        props=(),
        characters=(),
    )
    prop_bindings = _episode_asset_bindings(
        asset_kind="prop",
        project_id="owner/project",
        episode_number=1,
        director_plan=plan,
        changed_entities=props,
        scenes=scenes,
        props=(),
        characters=(),
    )

    assert [(item.asset_kind, item.entity_id) for item in scene_bindings] == [
        ("scene_base", "咖啡馆"),
        ("scene_variant", "雨中咖啡馆"),
    ]
    assert [(item.base_entity_id, item.variant_id) for item in scene_bindings[1:]] == [
        ("咖啡馆", "暴雨版")
    ]
    assert [(item.asset_kind, item.entity_id) for item in prop_bindings] == [
        ("prop", "强光手电")
    ]
    assert all(item.entity_id != "咖啡馆_暴雨版" for item in scene_bindings)


def test_scene_variant_with_reference_image_keeps_real_variant_state_slot():
    [binding] = _scene_bindings(
        _scene_state(),
        scenes=(
            SimpleNamespace(
                name="咖啡馆",
                base_scene_id="",
                variant_id="",
                master_image="assets/scenes/cafe.png",
            ),
            SimpleNamespace(
                name="雨中咖啡馆",
                base_scene_id="咖啡馆",
                variant_id="暴雨版",
                master_image="assets/scenes/cafe-rain.png",
            ),
        ),
    )

    assert binding.asset_kind == "scene_variant"
    assert binding.entity_id == "雨中咖啡馆"
    assert binding.asset_slot_id == "scene:咖啡馆:state:雨中咖啡馆:master"
    assert binding.status == "ready"
    assert binding.resolution == "auto_matched"


def test_scene_variant_prefers_only_candidate_with_reference_image():
    [binding] = _scene_bindings(
        _scene_state(),
        scenes=(
            SimpleNamespace(
                name="雨中咖啡馆-旧",
                base_scene_id="咖啡馆",
                variant_id="暴雨版",
                master_image="",
            ),
            SimpleNamespace(
                name="雨中咖啡馆-可用",
                base_scene_id="咖啡馆",
                variant_id="暴雨版",
                master_image="assets/scenes/cafe-rain.png",
            ),
        ),
    )

    assert binding.entity_id == "雨中咖啡馆-可用"
    assert binding.asset_slot_id == "scene:咖啡馆:state:雨中咖啡馆-可用:master"
    assert binding.status == "ready"
    assert binding.resolution == "auto_matched"


def test_scene_variant_with_multiple_reference_images_requires_confirmation():
    [binding] = _scene_bindings(
        _scene_state(),
        scenes=(
            SimpleNamespace(
                name="雨中咖啡馆-A",
                base_scene_id="咖啡馆",
                variant_id="暴雨版",
                master_image="assets/scenes/cafe-rain-a.png",
            ),
            SimpleNamespace(
                name="雨中咖啡馆-B",
                base_scene_id="咖啡馆",
                variant_id="暴雨版",
                master_image="assets/scenes/cafe-rain-b.png",
            ),
        ),
    )

    assert binding.status == "pending_confirmation"
    assert binding.asset_slot_id == ""


@pytest.mark.parametrize("include_empty_variant", [False, True])
def test_unavailable_scene_variant_falls_back_to_exact_base_scene(
    include_empty_variant: bool,
):
    scenes: tuple[object, ...] = (
        SimpleNamespace(
            name="咖啡馆",
            base_scene_id="",
            variant_id="",
            master_image="assets/scenes/cafe.png",
        ),
    )
    if include_empty_variant:
        scenes += (
            SimpleNamespace(
                name="雨中咖啡馆",
                base_scene_id="咖啡馆",
                variant_id="暴雨版",
                master_image="",
            ),
        )

    [binding] = _scene_bindings(_scene_state(), scenes=scenes)

    assert binding.asset_kind == "scene_variant"
    assert (binding.base_entity_id, binding.variant_id) == ("咖啡馆", "暴雨版")
    assert binding.entity_id == "咖啡馆"
    assert binding.asset_slot_id == "scene:咖啡馆:base:master"
    assert binding.status == "ready"
    assert binding.resolution == "explicit_fallback"


def test_empty_scene_variant_and_empty_base_keep_base_fallback_identity():
    [binding] = _scene_bindings(
        _scene_state(),
        scenes=(
            SimpleNamespace(
                name="咖啡馆",
                base_scene_id="",
                variant_id="",
                master_image="",
            ),
            SimpleNamespace(
                name="雨中咖啡馆",
                base_scene_id="咖啡馆",
                variant_id="暴雨版",
                master_image="",
            ),
        ),
    )

    assert binding.entity_id == "咖啡馆"
    assert binding.asset_slot_id == "scene:咖啡馆:base:master"
    assert binding.status == "missing_image"
    assert binding.resolution == "explicit_fallback"


@pytest.mark.parametrize(
    ("scenes", "expected_status"),
    [
        (
            (
                SimpleNamespace(
                    name="咖啡馆_暴雨版",
                    base_scene_id="",
                    variant_id="",
                    master_image="assets/scenes/fuzzy.png",
                ),
            ),
            "missing_asset",
        ),
        (
            (
                SimpleNamespace(
                    name="咖啡馆",
                    base_scene_id="",
                    variant_id="",
                    master_image="assets/scenes/cafe-a.png",
                ),
                SimpleNamespace(
                    name="咖啡馆",
                    base_scene_id="",
                    variant_id="",
                    master_image="assets/scenes/cafe-b.png",
                ),
            ),
            "pending_confirmation",
        ),
        (
            (
                SimpleNamespace(
                    name="咖啡馆",
                    base_scene_id="",
                    variant_id="",
                    master_image="",
                ),
            ),
            "missing_image",
        ),
    ],
)
def test_scene_variant_fallback_reports_exact_base_failure(
    scenes: tuple[object, ...], expected_status: str
):
    [binding] = _scene_bindings(_scene_state(), scenes=scenes)

    assert binding.status == expected_status
    assert binding.entity_id == "咖啡馆"
    assert (binding.base_entity_id, binding.variant_id) == ("咖啡馆", "暴雨版")
    assert binding.resolution == "explicit_fallback"
    if expected_status in {"missing_asset", "pending_confirmation"}:
        assert binding.asset_slot_id == ""
    else:
        assert binding.asset_slot_id == "scene:咖啡馆:base:master"


def test_scene_variant_fallback_bindings_keep_requested_variants_distinct():
    bindings = _scene_bindings(
        _scene_state("暴雨版"),
        _scene_state("夜景版"),
        scenes=(
            SimpleNamespace(
                name="咖啡馆",
                base_scene_id="",
                variant_id="",
                master_image="assets/scenes/cafe.png",
            ),
        ),
    )

    assert [binding.variant_id for binding in bindings] == ["暴雨版", "夜景版"]
    assert len({binding.binding_id for binding in bindings}) == 2
    assert {binding.asset_slot_id for binding in bindings} == {
        "scene:咖啡馆:base:master"
    }


@pytest.mark.asyncio
async def test_scene_variant_base_fallback_is_selected_in_preview(tmp_path):
    [binding] = _scene_bindings(
        _scene_state(),
        scenes=(
            SimpleNamespace(
                name="咖啡馆",
                base_scene_id="",
                variant_id="",
                master_image="assets/scenes/cafe.png",
            ),
        ),
    )
    image_path = tmp_path / "assets" / "scenes" / "cafe.png"
    image_path.parent.mkdir(parents=True)
    Image.new("RGB", (8, 8), "red").save(image_path)
    workflow = ProductionWorkflowStore(tmp_path / "state" / "workflow.json")
    workflow.register_candidate_version(
        slot_id=binding.asset_slot_id,
        asset_kind="scene_base",
        version_id="scene-base-v1",
        asset_path=str(image_path),
        source_attempt_id="scene-attempt-1",
        qc_passed=True,
        generation_metadata=None,
        actor="test",
        at=datetime.now(UTC),
    )

    preview = await resolve_planned_reference_preview(
        _PlannedBindingStore(binding),
        workflow,
        project_id="owner/project",
        episode_number=1,
        group_id="group-1",
        project_dir=tmp_path,
    )

    [resolved] = preview.bindings
    assert resolved.status == "ready"
    assert resolved.selected_by_default is True
    assert resolved.asset_slot_id == "scene:咖啡馆:base:master"
    assert resolved.version_id == "scene-base-v1"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("published_variant_id", "expected_status", "selected_by_default"),
    [
        ("暴雨版", "pending_confirmation", False),
        ("夜景版", "ready", True),
        (None, "ready", True),
    ],
)
async def test_preview_only_rejects_fallback_for_exact_metadata_variant(
    tmp_path,
    published_variant_id: str | None,
    expected_status: str,
    selected_by_default: bool,
):
    [binding] = _scene_bindings(
        _scene_state(),
        scenes=(
            SimpleNamespace(
                name="咖啡馆",
                base_scene_id="",
                variant_id="",
                master_image="assets/scenes/咖啡馆/master.png",
            ),
        ),
    )
    workflow = ProductionWorkflowStore(tmp_path / "state" / "workflow.json")
    for slot_id, asset_kind, relative_path, version_id, color in (
        (
            binding.asset_slot_id,
            "scene_base",
            "assets/scenes/咖啡馆/master.png",
            "base-v1",
            "red",
        ),
        (
            scene_state_slot_id("咖啡馆", "雨中咖啡馆", "master"),
            "scene_state",
            "assets/scenes/雨中咖啡馆/master.png",
            "variant-v1",
            "blue",
        ),
    ):
        image_path = tmp_path / relative_path
        image_path.parent.mkdir(parents=True)
        Image.new("RGB", (8, 8), color).save(image_path)
        workflow.register_candidate_version(
            slot_id=slot_id,
            asset_kind=asset_kind,
            version_id=version_id,
            asset_path=relative_path,
            source_attempt_id=f"{version_id}-attempt",
            qc_passed=True,
            generation_metadata=(
                {"variant_id": published_variant_id}
                if asset_kind == "scene_state" and published_variant_id is not None
                else None
            ),
            actor="test",
            at=datetime.now(UTC),
        )

    preview = await resolve_planned_reference_preview(
        _PlannedBindingStore(binding),
        workflow,
        project_id="owner/project",
        episode_number=1,
        group_id="group-1",
        project_dir=tmp_path,
    )

    [resolved] = preview.bindings
    assert resolved.status == expected_status
    assert resolved.selected_by_default is selected_by_default
    if not selected_by_default:
        assert "re-run scene planning" in resolved.warning


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("binding", "scene_name", "expected_slot_fragment"),
    [
        (
            PlannedReferenceBinding.create(
                project_id="owner/project",
                episode_number=1,
                source_plan_revision_id="director-r2",
                asset_kind="scene_variant",
                entity_id="雨中咖啡馆",
                base_entity_id="咖啡馆",
                variant_id="暴雨版",
                asset_slot_id="scene:咖啡馆:state:雨中咖啡馆:master",
                group_ids=("group-1",),
                status="ready",
                resolution="auto_matched",
                display_label="咖啡馆 / 暴雨版",
            ),
            "雨中咖啡馆",
            ":state:",
        ),
        (
            PlannedReferenceBinding.create(
                project_id="owner/project",
                episode_number=1,
                source_plan_revision_id="director-r2",
                asset_kind="scene_variant",
                entity_id="咖啡馆",
                base_entity_id="咖啡馆",
                variant_id="暴雨版",
                asset_slot_id="scene:咖啡馆:base:master",
                group_ids=("group-1",),
                status="ready",
                resolution="explicit_fallback",
                display_label="咖啡馆 / 暴雨版（基础场景）",
            ),
            "咖啡馆",
            ":base:",
        ),
    ],
)
async def test_scene_variant_preview_reads_canonical_legacy_without_persisting(
    tmp_path,
    binding: PlannedReferenceBinding,
    scene_name: str,
    expected_slot_fragment: str,
):
    canonical = tmp_path / "assets" / "scenes" / scene_name / "master.png"
    canonical.parent.mkdir(parents=True)
    Image.new("RGB", (8, 8), "red").save(canonical)
    workflow_path = tmp_path / "state" / "production_workflow.json"
    workflow = ProductionWorkflowStore(workflow_path)

    preview = await resolve_planned_reference_preview(
        _PlannedBindingStore(binding),
        workflow,
        project_id="owner/project",
        episode_number=1,
        group_id="group-1",
        project_dir=tmp_path,
    )

    [resolved] = preview.bindings
    assert resolved.status == "ready"
    assert resolved.selected_by_default is True
    assert resolved.asset_slot_id == binding.asset_slot_id
    assert resolved.relative_path == canonical.relative_to(tmp_path).as_posix()
    assert resolved.sha256
    assert expected_slot_fragment in resolved.asset_slot_id
    assert not workflow_path.exists()


@pytest.mark.asyncio
async def test_scene_variant_preview_does_not_bypass_corrupt_workflow_with_legacy(
    tmp_path,
):
    binding = PlannedReferenceBinding.create(
        project_id="owner/project",
        episode_number=1,
        source_plan_revision_id="director-r2",
        asset_kind="scene_variant",
        entity_id="咖啡馆",
        base_entity_id="咖啡馆",
        variant_id="暴雨版",
        asset_slot_id="scene:咖啡馆:base:master",
        group_ids=("group-1",),
        status="ready",
        resolution="explicit_fallback",
        display_label="咖啡馆 / 暴雨版（基础场景）",
    )
    canonical = tmp_path / "assets" / "scenes" / "咖啡馆" / "master.png"
    canonical.parent.mkdir(parents=True)
    Image.new("RGB", (8, 8), "red").save(canonical)
    workflow_path = tmp_path / "state" / "production_workflow.json"
    workflow_path.parent.mkdir(parents=True)
    workflow_path.write_text("{broken", encoding="utf-8")

    preview = await resolve_planned_reference_preview(
        _PlannedBindingStore(binding),
        ProductionWorkflowStore(workflow_path),
        project_id="owner/project",
        episode_number=1,
        group_id="group-1",
        project_dir=tmp_path,
    )

    [resolved] = preview.bindings
    assert resolved.status == "missing_asset"
    assert resolved.selected_by_default is False
    assert workflow_path.read_text(encoding="utf-8") == "{broken"


def test_real_novel_scenes_follow_available_workflow_master_slots(tmp_path):
    from novelvideo.task_backend.runners.episode_assets import (
        _available_scene_reference_slots,
    )

    base = NovelScene(name="咖啡馆")
    variant = NovelScene(
        name="雨中咖啡馆", base_scene_id="咖啡馆", variant_id="暴雨版"
    )
    base_image = tmp_path / "assets" / "scenes" / "咖啡馆" / "versions" / "base.png"
    base_image.parent.mkdir(parents=True)
    Image.new("RGB", (8, 8), "red").save(base_image)
    workflow = ProductionWorkflowStore(tmp_path / "state" / "production_workflow.json")
    workflow.register_candidate_version(
        slot_id=scene_base_slot_id(base.name, "master"),
        asset_kind="scene_base",
        version_id="base-v1",
        asset_path=base_image.relative_to(tmp_path).as_posix(),
        source_attempt_id="base-attempt",
        qc_passed=True,
        generation_metadata=None,
        actor="test",
        at=datetime.now(UTC),
    )
    ctx = SimpleNamespace(output_dir=tmp_path, state_dir=tmp_path / "state")

    available = _available_scene_reference_slots(ctx=ctx, scenes=(base, variant))
    [fallback] = _scene_bindings(
        _scene_state(),
        scenes=(base, variant),
        available_scene_reference_slots=available,
    )

    assert available == frozenset({scene_base_slot_id(base.name, "master")})
    assert fallback.entity_id == base.name
    assert fallback.asset_slot_id == scene_base_slot_id(base.name, "master")
    assert fallback.resolution == "explicit_fallback"

    variant_image = (
        tmp_path / "assets" / "scenes" / variant.name / "versions" / "variant.png"
    )
    variant_image.parent.mkdir(parents=True)
    Image.new("RGB", (8, 8), "blue").save(variant_image)
    workflow.register_candidate_version(
        slot_id=scene_state_slot_id(base.name, variant.name, "master"),
        asset_kind="scene_state",
        version_id="variant-v1",
        asset_path=variant_image.relative_to(tmp_path).as_posix(),
        source_attempt_id="variant-attempt",
        qc_passed=True,
        generation_metadata=None,
        actor="test",
        at=datetime.now(UTC),
    )

    available = _available_scene_reference_slots(ctx=ctx, scenes=(base, variant))
    [direct] = _scene_bindings(
        _scene_state(),
        scenes=(base, variant),
        available_scene_reference_slots=available,
    )

    assert direct.entity_id == variant.name
    assert direct.asset_slot_id == scene_state_slot_id(
        base.name, variant.name, "master"
    )
    assert direct.resolution == "auto_matched"


def test_scene_slot_availability_reads_safe_legacy_canonical_without_writing(
    tmp_path,
):
    from novelvideo.task_backend.runners.episode_assets import (
        _available_scene_reference_slots,
    )

    scene = NovelScene(name="咖啡馆")
    canonical = tmp_path / "assets" / "scenes" / scene.name / "master.png"
    canonical.parent.mkdir(parents=True)
    Image.new("RGB", (8, 8), "red").save(canonical)

    available = _available_scene_reference_slots(
        ctx=SimpleNamespace(output_dir=tmp_path, state_dir=tmp_path / "state"),
        scenes=(scene,),
    )

    slot_id = scene_base_slot_id(scene.name, "master")
    assert available == frozenset({slot_id})
    assert not (tmp_path / "state" / "production_workflow.json").exists()


def test_scene_slot_availability_does_not_bypass_corrupt_workflow(tmp_path):
    from novelvideo.task_backend.runners.episode_assets import (
        _available_scene_reference_slots,
    )

    scene = NovelScene(name="咖啡馆")
    canonical = tmp_path / "assets" / "scenes" / scene.name / "master.png"
    canonical.parent.mkdir(parents=True)
    Image.new("RGB", (8, 8), "red").save(canonical)
    workflow_path = tmp_path / "state" / "production_workflow.json"
    workflow_path.parent.mkdir(parents=True)
    workflow_path.write_text("{broken", encoding="utf-8")

    available = _available_scene_reference_slots(
        ctx=SimpleNamespace(output_dir=tmp_path, state_dir=tmp_path / "state"),
        scenes=(scene,),
    )

    assert available == frozenset()
    assert workflow_path.read_text(encoding="utf-8") == "{broken"


def test_scene_slot_availability_rejects_cross_slot_current_version(tmp_path):
    from novelvideo.task_backend.runners.episode_assets import (
        _available_scene_reference_slots,
    )

    scene = NovelScene(name="咖啡馆")
    other = NovelScene(name="车站")
    workflow_path = tmp_path / "state" / "production_workflow.json"
    workflow = ProductionWorkflowStore(workflow_path)
    for item, version_id, color in (
        (scene, "cafe-v1", "red"),
        (other, "station-v1", "blue"),
    ):
        image_path = (
            tmp_path / "assets" / "scenes" / item.name / "versions" / f"{version_id}.png"
        )
        image_path.parent.mkdir(parents=True)
        Image.new("RGB", (8, 8), color).save(image_path)
        workflow.register_candidate_version(
            slot_id=scene_base_slot_id(item.name, "master"),
            asset_kind="scene_base",
            version_id=version_id,
            asset_path=image_path.relative_to(tmp_path).as_posix(),
            source_attempt_id=f"{version_id}-attempt",
            qc_passed=True,
            generation_metadata=None,
            actor="test",
            at=datetime.now(UTC),
        )
    payload = json.loads(workflow_path.read_text(encoding="utf-8"))
    cafe_slot = payload["slots"][scene_base_slot_id(scene.name, "master")]
    cafe_slot["current_version_id"] = "station-v1"
    cafe_slot["version_ids"].append("station-v1")
    workflow_path.write_text(json.dumps(payload), encoding="utf-8")

    available = _available_scene_reference_slots(
        ctx=SimpleNamespace(output_dir=tmp_path, state_dir=tmp_path / "state"),
        scenes=(scene,),
    )

    assert available == frozenset()


def test_scene_slot_availability_rejects_mismatched_slot_identity(tmp_path):
    from novelvideo.task_backend.runners.episode_assets import (
        _available_scene_reference_slots,
    )

    scene = NovelScene(name="咖啡馆")
    slot_id = scene_base_slot_id(scene.name, "master")
    image_path = tmp_path / "assets" / "scenes" / scene.name / "master.png"
    image_path.parent.mkdir(parents=True)
    Image.new("RGB", (8, 8), "red").save(image_path)
    workflow_path = tmp_path / "state" / "production_workflow.json"
    workflow = ProductionWorkflowStore(workflow_path)
    workflow.register_candidate_version(
        slot_id=slot_id,
        asset_kind="scene_base",
        version_id="base-v1",
        asset_path=image_path.relative_to(tmp_path).as_posix(),
        source_attempt_id="base-attempt",
        qc_passed=True,
        generation_metadata=None,
        actor="test",
        at=datetime.now(UTC),
    )
    payload = json.loads(workflow_path.read_text(encoding="utf-8"))
    payload["slots"][slot_id]["slot_id"] = scene_base_slot_id("车站", "master")
    workflow_path.write_text(json.dumps(payload), encoding="utf-8")

    available = _available_scene_reference_slots(
        ctx=SimpleNamespace(output_dir=tmp_path, state_dir=tmp_path / "state"),
        scenes=(scene,),
    )

    assert available == frozenset()


def test_scene_slot_availability_does_not_replace_wrong_kind_with_legacy(
    tmp_path,
):
    from novelvideo.task_backend.runners.episode_assets import (
        _available_scene_reference_slots,
    )

    scene = NovelScene(name="咖啡馆")
    canonical = tmp_path / "assets" / "scenes" / scene.name / "master.png"
    canonical.parent.mkdir(parents=True)
    Image.new("RGB", (8, 8), "red").save(canonical)
    slot_id = scene_base_slot_id(scene.name, "master")
    workflow = ProductionWorkflowStore(
        tmp_path / "state" / "production_workflow.json"
    )
    workflow.register_candidate_version(
        slot_id=slot_id,
        asset_kind="scene_state",
        version_id="wrong-kind-v1",
        asset_path=canonical.relative_to(tmp_path).as_posix(),
        source_attempt_id="wrong-kind-attempt",
        qc_passed=True,
        generation_metadata=None,
        actor="test",
        at=datetime.now(UTC),
    )

    available = _available_scene_reference_slots(
        ctx=SimpleNamespace(output_dir=tmp_path, state_dir=tmp_path / "state"),
        scenes=(scene,),
    )

    assert available == frozenset()
    slot, _versions = ProductionWorkflowStore(
        tmp_path / "state" / "production_workflow.json"
    ).get_slot(slot_id)
    assert slot.asset_kind == "scene_state"


@pytest.mark.parametrize(
    "unsafe_name",
    ["..", "../车站", "咖啡馆/二楼", "咖啡馆\\二楼", "咖啡馆:二楼", "咖啡馆\x1f"],
)
def test_scene_slot_availability_rejects_non_segment_scene_names(
    tmp_path, unsafe_name: str
):
    from novelvideo.task_backend.runners.episode_assets import (
        _available_scene_reference_slots,
    )

    cross_scene = tmp_path / "assets" / "scenes" / "车站" / "master.png"
    cross_scene.parent.mkdir(parents=True)
    Image.new("RGB", (8, 8), "red").save(cross_scene)
    if "/" not in unsafe_name and "\\" not in unsafe_name:
        candidate = tmp_path / "assets" / "scenes" / unsafe_name / "master.png"
        candidate.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (8, 8), "blue").save(candidate)

    available = _available_scene_reference_slots(
        ctx=SimpleNamespace(output_dir=tmp_path, state_dir=tmp_path / "state"),
        scenes=(SimpleNamespace(name=unsafe_name, base_scene_id=""),),
    )

    assert available == frozenset()
    assert not (tmp_path / "state" / "production_workflow.json").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("binding", "wrong_asset_kind"),
    [
        (
            PlannedReferenceBinding.create(
                project_id="owner/project",
                episode_number=1,
                source_plan_revision_id="director-r2",
                asset_kind="scene_variant",
                entity_id="雨中咖啡馆",
                base_entity_id="咖啡馆",
                variant_id="暴雨版",
                asset_slot_id="scene:咖啡馆:state:雨中咖啡馆:master",
                group_ids=("group-1",),
                status="ready",
                resolution="auto_matched",
                display_label="咖啡馆 / 暴雨版",
            ),
            "scene_base",
        ),
        (
            PlannedReferenceBinding.create(
                project_id="owner/project",
                episode_number=1,
                source_plan_revision_id="director-r2",
                asset_kind="scene_variant",
                entity_id="咖啡馆",
                base_entity_id="咖啡馆",
                variant_id="暴雨版",
                asset_slot_id="scene:咖啡馆:base:master",
                group_ids=("group-1",),
                status="ready",
                resolution="explicit_fallback",
                display_label="咖啡馆 / 暴雨版",
            ),
            "scene_state",
        ),
    ],
)
async def test_scene_variant_preview_rejects_incompatible_slot_kind(
    tmp_path, binding: PlannedReferenceBinding, wrong_asset_kind: str
):
    image_path = tmp_path / "assets" / "scenes" / "wrong.png"
    image_path.parent.mkdir(parents=True)
    Image.new("RGB", (8, 8), "red").save(image_path)
    workflow = ProductionWorkflowStore(tmp_path / "state" / "workflow.json")
    workflow.register_candidate_version(
        slot_id=binding.asset_slot_id,
        asset_kind=wrong_asset_kind,
        version_id="wrong-v1",
        asset_path=str(image_path),
        source_attempt_id="wrong-attempt",
        qc_passed=True,
        generation_metadata=None,
        actor="test",
        at=datetime.now(UTC),
    )

    preview = await resolve_planned_reference_preview(
        _PlannedBindingStore(binding),
        workflow,
        project_id="owner/project",
        episode_number=1,
        group_id="group-1",
        project_dir=tmp_path,
    )

    [resolved] = preview.bindings
    assert resolved.status == "missing_asset"
    assert resolved.selected_by_default is False


@pytest.mark.asyncio
async def test_scene_variant_preview_rejects_mismatched_slot_identity(tmp_path):
    binding = PlannedReferenceBinding.create(
        project_id="owner/project",
        episode_number=1,
        source_plan_revision_id="director-r2",
        asset_kind="scene_variant",
        entity_id="咖啡馆",
        base_entity_id="咖啡馆",
        variant_id="暴雨版",
        asset_slot_id=scene_base_slot_id("咖啡馆", "master"),
        group_ids=("group-1",),
        status="ready",
        resolution="explicit_fallback",
        display_label="咖啡馆 / 暴雨版（基础场景）",
    )
    image_path = tmp_path / "assets" / "scenes" / "咖啡馆" / "master.png"
    image_path.parent.mkdir(parents=True)
    Image.new("RGB", (8, 8), "red").save(image_path)
    workflow_path = tmp_path / "state" / "workflow.json"
    workflow = ProductionWorkflowStore(workflow_path)
    workflow.register_candidate_version(
        slot_id=binding.asset_slot_id,
        asset_kind="scene_base",
        version_id="base-v1",
        asset_path=image_path.relative_to(tmp_path).as_posix(),
        source_attempt_id="base-attempt",
        qc_passed=True,
        generation_metadata=None,
        actor="test",
        at=datetime.now(UTC),
    )
    payload = json.loads(workflow_path.read_text(encoding="utf-8"))
    payload["slots"][binding.asset_slot_id]["slot_id"] = scene_base_slot_id(
        "车站", "master"
    )
    workflow_path.write_text(json.dumps(payload), encoding="utf-8")

    preview = await resolve_planned_reference_preview(
        _PlannedBindingStore(binding),
        ProductionWorkflowStore(workflow_path),
        project_id="owner/project",
        episode_number=1,
        group_id="group-1",
        project_dir=tmp_path,
    )

    [resolved] = preview.bindings
    assert resolved.status == "missing_asset"
    assert resolved.selected_by_default is False


def test_prop_binding_resolves_existing_asset_alias_to_canonical_id():
    prop = NovelProp(name="功德碑", aliases=["镇河碑"])
    requirement = SimpleNamespace(kind="prop", entity_key="镇河碑", required=True)
    shot = SimpleNamespace(
        id="shot-1",
        dramatic_beat_ids=("beat-1",),
        asset_requirements=(requirement,),
    )
    group = SimpleNamespace(id="group-1", dramatic_beat_ids=("beat-1",), shots=(shot,))
    plan = SimpleNamespace(revision_id="director-r2", groups=(group,))

    bindings = _episode_asset_bindings(
        asset_kind="prop",
        project_id="owner/project",
        episode_number=1,
        director_plan=plan,
        changed_entities=(prop,),
        scenes=(),
        props=(prop,),
        characters=(),
    )

    assert [(item.asset_kind, item.entity_id) for item in bindings] == [
        ("prop", "功德碑")
    ]


def test_prop_binding_omits_unselected_or_missing_asset_requirement():
    prop = NovelProp(name="功德碑")
    requirement = SimpleNamespace(kind="prop", entity_key="普通雨伞", required=True)
    shot = SimpleNamespace(
        id="shot-1",
        dramatic_beat_ids=("beat-1",),
        asset_requirements=(requirement,),
    )
    group = SimpleNamespace(id="group-1", dramatic_beat_ids=("beat-1",), shots=(shot,))
    plan = SimpleNamespace(revision_id="director-r2", groups=(group,))

    bindings = _episode_asset_bindings(
        asset_kind="prop",
        project_id="owner/project",
        episode_number=1,
        director_plan=plan,
        changed_entities=(),
        scenes=(),
        props=(prop,),
        characters=(),
    )

    assert bindings == ()


def test_binding_projection_retains_unchanged_full_catalog_alongside_overlay():
    requirements = (
        SimpleNamespace(kind="scene_base", entity_key="旧车站", required=True),
        SimpleNamespace(kind="scene_base", entity_key="咖啡馆", required=True),
    )
    shot = SimpleNamespace(id="shot-1", dramatic_beat_ids=(), asset_requirements=requirements)
    plan = SimpleNamespace(
        revision_id="director-r2",
        groups=(SimpleNamespace(id="group-1", dramatic_beat_ids=(), shots=(shot,)),),
    )

    bindings = _episode_asset_bindings(
        asset_kind="scene",
        project_id="owner/project",
        episode_number=1,
        director_plan=plan,
        changed_entities=(NovelScene(name="咖啡馆", environment_prompt="新规划"),),
        scenes=(
            NovelScene(name="旧车站"),
            NovelScene(name="咖啡馆", environment_prompt="旧规划"),
        ),
        props=(),
        characters=(),
    )

    assert [item.entity_id for item in bindings] == ["旧车站", "咖啡馆"]


@pytest.mark.asyncio
async def test_publish_scene_plan_atomic_exposes_entity_menu_and_binding_together(tmp_path):
    store = SQLiteStore("owner/project", output_dir=str(tmp_path), state_dir=str(tmp_path))
    await store.initialize()
    await store.add_episode(NovelEpisode(number=1, title="第一集"))
    scene = NovelScene(name="咖啡馆", environment_prompt="四面空间描述")
    menu = (SceneMenuItem(scene_id="咖啡馆"),)

    await store.publish_scene_plan_atomic(
        episode_number=1,
        scenes=(scene,),
        scene_menu=menu,
        scene_baseline_digests={},
        episode_scene_menu_baseline_digest=store.asset_menu_baseline_digest([]),
        bindings=(_binding("scene_base", "咖啡馆"),),
    )

    persisted_scene = await store.get_scene("咖啡馆")
    assert persisted_scene.name == scene.name
    assert persisted_scene.environment_prompt == scene.environment_prompt
    assert (await store.list_episodes())[0].scene_menu == list(menu)
    assert await store.list_planned_reference_bindings(1) == [_binding("scene_base", "咖啡馆")]


@pytest.mark.asyncio
async def test_cancel_during_real_sqlite_scene_reconcile_waits_for_final_binding(
    tmp_path,
):
    from novelvideo.task_backend.runners.episode_assets import (
        _shield_scene_publication,
    )

    store = SQLiteStore(
        "owner/project", output_dir=str(tmp_path), state_dir=str(tmp_path)
    )
    await store.initialize()
    await store.add_episode(NovelEpisode(number=1, title="第一集"))
    fallback = PlannedReferenceBinding.create(
        project_id="owner/project",
        episode_number=1,
        source_plan_revision_id="director-r2",
        asset_kind="scene_variant",
        entity_id="咖啡馆",
        base_entity_id="咖啡馆",
        variant_id="暴雨版",
        asset_slot_id=scene_base_slot_id("咖啡馆", "master"),
        status="ready",
        resolution="explicit_fallback",
        display_label="咖啡馆 / 暴雨版（基础场景）",
    )
    direct = PlannedReferenceBinding.create(
        project_id="owner/project",
        episode_number=1,
        source_plan_revision_id="director-r2",
        asset_kind="scene_variant",
        entity_id="雨中咖啡馆",
        base_entity_id="咖啡馆",
        variant_id="暴雨版",
        asset_slot_id=scene_state_slot_id("咖啡馆", "雨中咖啡馆", "master"),
        status="ready",
        resolution="auto_matched",
        display_label="咖啡馆 / 暴雨版",
    )
    release_reconcile = asyncio.Event()

    await store.publish_scene_plan_atomic(
        episode_number=1,
        scenes=(NovelScene(name="咖啡馆"),),
        scene_menu=(SceneMenuItem(scene_id="咖啡馆"),),
        scene_baseline_digests={},
        episode_scene_menu_baseline_digest=store.asset_menu_baseline_digest([]),
        bindings=(fallback,),
        refresh_cache=False,
    )

    async def reconcile_after_commit():
        await release_reconcile.wait()
        await store.replace_planned_reference_bindings_atomic(
            1, ("scene_base", "scene_variant"), (direct,)
        )

    task = asyncio.create_task(
        _shield_scene_publication(reconcile_after_commit())
    )
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    release_reconcile.set()
    with pytest.raises(asyncio.CancelledError):
        await task

    [persisted] = await store.list_planned_reference_bindings(1)
    assert persisted.entity_id == "雨中咖啡馆"
    assert persisted.resolution == "auto_matched"


@pytest.mark.asyncio
async def test_publish_prop_plan_atomic_rolls_back_all_state_on_binding_failure(tmp_path, monkeypatch):
    store = SQLiteStore("owner/project", output_dir=str(tmp_path), state_dir=str(tmp_path))
    await store.initialize()
    old_menu = [PropMenuItem(prop_id="旧道具")]
    old_binding = _binding("prop", "旧道具")
    await store.add_episode(NovelEpisode(number=1, title="第一集", prop_menu=old_menu))
    await store.replace_planned_reference_bindings_atomic(1, ("prop",), (old_binding,))

    async def fail_insert(db, binding):
        raise RuntimeError("binding insert failed")

    monkeypatch.setattr(store, "_insert_planned_reference_binding", fail_insert)
    with pytest.raises(RuntimeError, match="binding insert failed"):
        await store.publish_prop_plan_atomic(
            episode_number=1,
            props=(NovelProp(name="强光手电", visual_prompt="黑色金属"),),
            prop_menu=(PropMenuItem(prop_id="强光手电"),),
            prop_baseline_digests={},
            episode_prop_menu_baseline_digest=store.asset_menu_baseline_digest(old_menu),
            bindings=(_binding("prop", "强光手电"),),
        )

    assert await store.get_prop("强光手电") is None
    assert (await store.list_episodes())[0].prop_menu == old_menu
    assert await store.list_planned_reference_bindings(1) == [old_binding]


@pytest.mark.asyncio
async def test_publish_scene_plan_fails_closed_on_menu_conflict(tmp_path):
    store = SQLiteStore("owner/project", output_dir=str(tmp_path), state_dir=str(tmp_path))
    await store.initialize()
    await store.add_episode(NovelEpisode(number=1, title="第一集"))
    baseline = store.asset_menu_baseline_digest([])
    concurrent_menu = [SceneMenuItem(scene_id="用户场景")]
    await store.update_episode(1, scene_menu=concurrent_menu)

    with pytest.raises(ValueError, match="scene menu conflict"):
        await store.publish_scene_plan_atomic(
            episode_number=1,
            scenes=(NovelScene(name="咖啡馆"),),
            scene_menu=(SceneMenuItem(scene_id="咖啡馆"),),
            scene_baseline_digests={},
            episode_scene_menu_baseline_digest=baseline,
            bindings=(),
        )

    assert await store.get_scene("咖啡馆") is None
    assert (await store.list_episodes())[0].scene_menu == concurrent_menu


@pytest.mark.asyncio
async def test_scene_publish_preserves_concurrent_non_planning_fields(tmp_path):
    store = SQLiteStore("owner/project", output_dir=str(tmp_path), state_dir=str(tmp_path))
    await store.initialize()
    original = NovelScene(name="咖啡馆", environment_prompt="旧描述", notes="原备注")
    await store.add_scene(original)
    await store.add_episode(NovelEpisode(number=1, title="第一集"))
    baseline = store.scene_plan_baseline_digest(original)
    await store.update_scene("咖啡馆", notes="用户并发备注")

    await store.publish_scene_plan_atomic(
        episode_number=1,
        scenes=(original.model_copy(update={"environment_prompt": "新描述"}),),
        scene_menu=(SceneMenuItem(scene_id="咖啡馆"),),
        scene_baseline_digests={"咖啡馆": baseline},
        episode_scene_menu_baseline_digest=store.asset_menu_baseline_digest([]),
        bindings=(),
    )

    persisted = await store.get_scene("咖啡馆")
    assert persisted.environment_prompt == "新描述"
    assert persisted.notes == "用户并发备注"


@pytest.mark.asyncio
async def test_prop_publish_fails_closed_on_planning_field_conflict(tmp_path):
    store = SQLiteStore("owner/project", output_dir=str(tmp_path), state_dir=str(tmp_path))
    await store.initialize()
    original = NovelProp(name="手电", visual_prompt="旧外观", notes="用户备注")
    await store.add_prop(original)
    await store.add_episode(NovelEpisode(number=1, title="第一集"))
    baseline = store.prop_plan_baseline_digest(original)
    await store.update_prop("手电", visual_prompt="用户并发外观")

    with pytest.raises(ValueError, match="prop plan conflict"):
        await store.publish_prop_plan_atomic(
            episode_number=1,
            props=(original.model_copy(update={"visual_prompt": "规划外观"}),),
            prop_menu=(PropMenuItem(prop_id="手电"),),
            prop_baseline_digests={"手电": baseline},
            episode_prop_menu_baseline_digest=store.asset_menu_baseline_digest([]),
            bindings=(),
        )

    assert (await store.get_prop("手电")).visual_prompt == "用户并发外观"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kind", "raw_menu", "canonical_menu"),
    [
        ("scene", [{"scene_id": "咖啡馆"}], [SceneMenuItem(scene_id="咖啡馆")]),
        ("prop", [{"prop_id": "手电"}], [PropMenuItem(prop_id="手电")]),
        ("scene", ["历史场景"], []),
        ("prop", ["历史道具"], []),
    ],
)
async def test_asset_menu_baseline_canonicalizes_legacy_json(
    tmp_path, kind, raw_menu, canonical_menu
):
    store = SQLiteStore("owner/project", output_dir=str(tmp_path), state_dir=str(tmp_path))
    await store.initialize()
    episode = NovelEpisode(number=1, title="第一集")
    if kind == "scene":
        episode.scene_menu_json = json.dumps(raw_menu, ensure_ascii=False)
    else:
        episode.prop_menu_json = json.dumps(raw_menu, ensure_ascii=False)
    await store.add_episode(episode)
    baseline_items = raw_menu if raw_menu and isinstance(raw_menu[0], str) else canonical_menu
    baseline = store.asset_menu_baseline_digest(baseline_items, asset_kind=kind)

    if kind == "scene":
        await store.publish_scene_plan_atomic(
            episode_number=1,
            scenes=(),
            scene_menu=tuple(canonical_menu),
            scene_baseline_digests={},
            episode_scene_menu_baseline_digest=baseline,
            bindings=None,
        )
    else:
        await store.publish_prop_plan_atomic(
            episode_number=1,
            props=(),
            prop_menu=tuple(canonical_menu),
            prop_baseline_digests={},
            episode_prop_menu_baseline_digest=baseline,
            bindings=None,
        )


@pytest.mark.asyncio
async def test_publish_prop_plan_atomic_exposes_entity_menu_and_binding_together(tmp_path):
    store = SQLiteStore("owner/project", output_dir=str(tmp_path), state_dir=str(tmp_path))
    await store.initialize()
    await store.add_episode(NovelEpisode(number=1, title="第一集"))
    prop = NovelProp(name="强光手电", visual_prompt="黑色金属")
    menu = (PropMenuItem(prop_id=prop.name),)

    await store.publish_prop_plan_atomic(
        episode_number=1,
        props=(prop,),
        prop_menu=menu,
        prop_baseline_digests={},
        episode_prop_menu_baseline_digest=store.asset_menu_baseline_digest(
            [], asset_kind="prop"
        ),
        bindings=(_binding("prop", prop.name),),
    )

    assert (await store.get_prop(prop.name)).visual_prompt == prop.visual_prompt
    assert (await store.list_episodes())[0].prop_menu == list(menu)
    assert await store.list_planned_reference_bindings(1) == [_binding("prop", prop.name)]


@pytest.mark.asyncio
async def test_publish_scene_plan_atomic_rolls_back_all_state_on_binding_failure(
    tmp_path, monkeypatch
):
    store = SQLiteStore("owner/project", output_dir=str(tmp_path), state_dir=str(tmp_path))
    await store.initialize()
    old_menu = [SceneMenuItem(scene_id="旧场景")]
    old_binding = _binding("scene_base", "旧场景")
    await store.add_episode(NovelEpisode(number=1, title="第一集", scene_menu=old_menu))
    await store.replace_planned_reference_bindings_atomic(
        1, ("scene_base", "scene_variant"), (old_binding,)
    )

    async def fail_insert(db, binding):
        raise RuntimeError("binding insert failed")

    monkeypatch.setattr(store, "_insert_planned_reference_binding", fail_insert)
    with pytest.raises(RuntimeError, match="binding insert failed"):
        await store.publish_scene_plan_atomic(
            episode_number=1,
            scenes=(NovelScene(name="咖啡馆"),),
            scene_menu=(SceneMenuItem(scene_id="咖啡馆"),),
            scene_baseline_digests={},
            episode_scene_menu_baseline_digest=store.asset_menu_baseline_digest(
                old_menu, asset_kind="scene"
            ),
            bindings=(_binding("scene_base", "咖啡馆"),),
        )

    assert await store.get_scene("咖啡馆") is None
    assert (await store.list_episodes())[0].scene_menu == old_menu
    assert await store.list_planned_reference_bindings(1) == [old_binding]


@pytest.mark.asyncio
async def test_publish_scene_plan_fails_closed_on_planning_field_conflict(tmp_path):
    store = SQLiteStore("owner/project", output_dir=str(tmp_path), state_dir=str(tmp_path))
    await store.initialize()
    original = NovelScene(name="咖啡馆", environment_prompt="旧描述")
    await store.add_scene(original)
    await store.add_episode(NovelEpisode(number=1, title="第一集"))
    baseline = store.scene_plan_baseline_digest(original)
    await store.update_scene("咖啡馆", environment_prompt="用户并发描述")

    with pytest.raises(ValueError, match="scene plan conflict"):
        await store.publish_scene_plan_atomic(
            episode_number=1,
            scenes=(original.model_copy(update={"environment_prompt": "规划描述"}),),
            scene_menu=(SceneMenuItem(scene_id="咖啡馆"),),
            scene_baseline_digests={"咖啡馆": baseline},
            episode_scene_menu_baseline_digest=store.asset_menu_baseline_digest(
                [], asset_kind="scene"
            ),
            bindings=(),
        )

    assert (await store.get_scene("咖啡馆")).environment_prompt == "用户并发描述"


@pytest.mark.asyncio
async def test_publish_prop_plan_fails_closed_on_menu_conflict(tmp_path):
    store = SQLiteStore("owner/project", output_dir=str(tmp_path), state_dir=str(tmp_path))
    await store.initialize()
    await store.add_episode(NovelEpisode(number=1, title="第一集"))
    baseline = store.asset_menu_baseline_digest([], asset_kind="prop")
    concurrent = [PropMenuItem(prop_id="用户道具")]
    await store.update_episode(1, prop_menu=concurrent)

    with pytest.raises(ValueError, match="prop menu conflict"):
        await store.publish_prop_plan_atomic(
            episode_number=1,
            props=(NovelProp(name="手电"),),
            prop_menu=(PropMenuItem(prop_id="手电"),),
            prop_baseline_digests={},
            episode_prop_menu_baseline_digest=baseline,
            bindings=(),
        )

    assert await store.get_prop("手电") is None
    assert (await store.list_episodes())[0].prop_menu == concurrent


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["scene", "prop"])
async def test_asset_publish_rejects_new_name_that_appeared_concurrently(tmp_path, kind):
    store = SQLiteStore("owner/project", output_dir=str(tmp_path), state_dir=str(tmp_path))
    await store.initialize()
    await store.add_episode(NovelEpisode(number=1, title="第一集"))
    if kind == "scene":
        await store.add_scene(NovelScene(name="同名资产", notes="用户创建"))
        with pytest.raises(ValueError, match="scene now exists"):
            await store.publish_scene_plan_atomic(
                episode_number=1,
                scenes=(NovelScene(name="同名资产", environment_prompt="规划"),),
                scene_menu=(SceneMenuItem(scene_id="同名资产"),),
                scene_baseline_digests={},
                episode_scene_menu_baseline_digest=store.asset_menu_baseline_digest(
                    [], asset_kind="scene"
                ),
                bindings=(),
            )
        assert (await store.get_scene("同名资产")).notes == "用户创建"
    else:
        await store.add_prop(NovelProp(name="同名资产", notes="用户创建"))
        with pytest.raises(ValueError, match="prop now exists"):
            await store.publish_prop_plan_atomic(
                episode_number=1,
                props=(NovelProp(name="同名资产", visual_prompt="规划"),),
                prop_menu=(PropMenuItem(prop_id="同名资产"),),
                prop_baseline_digests={},
                episode_prop_menu_baseline_digest=store.asset_menu_baseline_digest(
                    [], asset_kind="prop"
                ),
                bindings=(),
            )
        assert (await store.get_prop("同名资产")).notes == "用户创建"


@pytest.mark.asyncio
async def test_prop_publish_preserves_concurrent_notes(tmp_path):
    store = SQLiteStore("owner/project", output_dir=str(tmp_path), state_dir=str(tmp_path))
    await store.initialize()
    original = NovelProp(name="手电", visual_prompt="旧外观", notes="原备注")
    await store.add_prop(original)
    await store.add_episode(NovelEpisode(number=1, title="第一集"))
    baseline = store.prop_plan_baseline_digest(original)
    await store.update_prop("手电", notes="用户并发备注")

    await store.publish_prop_plan_atomic(
        episode_number=1,
        props=(original.model_copy(update={"visual_prompt": "规划外观"}),),
        prop_menu=(PropMenuItem(prop_id="手电"),),
        prop_baseline_digests={"手电": baseline},
        episode_prop_menu_baseline_digest=store.asset_menu_baseline_digest(
            [], asset_kind="prop"
        ),
        bindings=(),
    )

    persisted = await store.get_prop("手电")
    assert persisted.visual_prompt == "规划外观"
    assert persisted.notes == "用户并发备注"


def test_only_scene_planner_uses_asset_planning_text_task_route():
    import novelvideo.task_backend.runners.episode_assets  # noqa: F401
    from novelvideo.task_backend.registry import get_project_task_runner_registration

    scene_registration = get_project_task_runner_registration("episode_scene_planner")
    prop_registration = get_project_task_runner_registration("episode_prop_planner")

    assert scene_registration is not None
    assert scene_registration.text_task_role == "episode_asset_planning"
    assert prop_registration is not None
    assert prop_registration.text_task_role is None


@pytest.mark.asyncio
async def test_runner_long_draft_does_not_block_activation_and_stale_revision_is_rejected(
    tmp_path, monkeypatch
):
    import novelvideo.agents.asset_compiler as compiler_module
    import novelvideo.cognee as cognee_module
    import novelvideo.director_plan.store as director_store_module
    import novelvideo.sqlite_store as sqlite_module
    import novelvideo.task_backend.runners.episode_assets as runner

    requirement = SimpleNamespace(
        kind="scene_state",
        entity_key="咖啡馆",
        visible_change="暴雨版",
        required=True,
    )
    shot = SimpleNamespace(
        id="shot-1", dramatic_beat_ids=("beat-1",), asset_requirements=(requirement,)
    )
    group = SimpleNamespace(
        id="group-1", dramatic_beat_ids=("beat-1",), shots=(shot,)
    )
    old_plan = SimpleNamespace(revision_id="director-old", groups=(group,))
    new_plan = SimpleNamespace(revision_id="director-new", groups=(group,))
    draft_started = asyncio.Event()
    release_draft = asyncio.Event()
    activation_finished = threading.Event()
    published_revision_ids: list[str] = []
    published_entity_ids: list[str] = []
    replaced_entity_ids: list[str] = []
    block_publish = False
    publish_error = ""
    inject_variant_after_read = False
    remove_variant_after_read = False
    publish_started = asyncio.Event()
    release_publish = asyncio.Event()

    class FakeDirectorPlanStore:
        lock = threading.Lock()
        active = old_plan

        def __init__(self, project_dir):
            pass

        @contextmanager
        def lock_active_revision(self, episode):
            with self.lock:
                yield self.active

        def activate_new(self):
            with self.lock:
                type(self).active = new_plan
                activation_finished.set()

    class FakeSQLiteStore:
        def __init__(self, *args, **kwargs):
            self.scenes = [NovelScene(name="咖啡馆")]

        async def initialize(self):
            pass

        async def load_graph_state(self):
            pass

        async def list_scenes(self):
            return list(self.scenes)

        async def list_props(self):
            return []

        async def publish_scene_plan_atomic(self, **kwargs):
            nonlocal block_publish, publish_error
            published_revision_ids.extend(
                binding.source_plan_revision_id for binding in kwargs["bindings"]
            )
            published_entity_ids.extend(
                binding.entity_id for binding in kwargs["bindings"]
            )
            if publish_error:
                raise ValueError(publish_error)
            if block_publish:
                publish_started.set()
                await release_publish.wait()

        async def replace_planned_reference_bindings_atomic(
            self, episode, asset_kinds, bindings
        ):
            assert episode == 1
            assert asset_kinds == ("scene_base", "scene_variant")
            replaced_entity_ids[:] = [binding.entity_id for binding in bindings]

    class FakeCogneeStore:
        def __init__(self, *args, sqlite_store, **kwargs):
            self.sqlite_store = sqlite_store
            self.episode = NovelEpisode(number=1, title="第一集")

        async def initialize(self):
            pass

        async def load_graph_state(self):
            pass

        def get_episode(self, episode):
            return self.episode

        def get_all_characters(self):
            return []

    class FakeCompiler:
        def __init__(self, store, *, director_plan):
            assert director_plan.revision_id == "director-old"

        async def build_scene_plan_draft(self, episode, **kwargs):
            draft_started.set()
            await release_draft.wait()
            return compiler_module.ScenePlanDraft(
                scenes=(
                    NovelScene(
                        name="雨中咖啡馆",
                        base_scene_id="咖啡馆",
                        variant_id="暴雨版",
                    ),
                ),
                scene_menu=(SceneMenuItem(scene_id="咖啡馆"),),
                new_count=1,
                scene_baseline_digests={},
                episode_scene_menu_baseline_digest="baseline",
            )

    class FakeUsageMeter:
        async def set_project_llm_usage_context(self, **kwargs):
            pass

    monkeypatch.setattr(compiler_module, "AssetCompiler", FakeCompiler)
    monkeypatch.setattr(cognee_module, "CogneeStore", FakeCogneeStore)
    monkeypatch.setattr(sqlite_module, "SQLiteStore", FakeSQLiteStore)
    monkeypatch.setattr(director_store_module, "DirectorPlanStore", FakeDirectorPlanStore)
    monkeypatch.setattr(runner, "get_usage_meter", lambda: FakeUsageMeter())
    monkeypatch.setattr(
        runner,
        "get_task_manager",
        lambda: SimpleNamespace(update_progress_for_project=lambda *args, **kwargs: None),
    )
    ctx = SimpleNamespace(
        owner_username="owner",
        project_name="project",
        owner_project_label="owner/project",
        project_id="owner/project",
        output_dir=tmp_path,
        state_dir=tmp_path,
    )
    envelope = {
        "task_type": "episode_scene_planner",
        "episode": 1,
        "payload": {"asset_kind": "scene"},
    }
    canonical = tmp_path / "assets" / "scenes" / "咖啡馆" / "master.png"
    canonical.parent.mkdir(parents=True)
    Image.new("RGB", (8, 8), "red").save(canonical)
    workflow_path = tmp_path / "production_workflow.json"
    variant_image = (
        tmp_path
        / "assets"
        / "scenes"
        / "雨中咖啡馆"
        / "versions"
        / "variant.png"
    )
    real_snapshot = runner._scene_reference_catalog_snapshot

    def observe_snapshot(**kwargs):
        nonlocal inject_variant_after_read, remove_variant_after_read
        snapshot = real_snapshot(**kwargs)
        if inject_variant_after_read:
            inject_variant_after_read = False
            variant_image.parent.mkdir(parents=True)
            Image.new("RGB", (8, 8), "blue").save(variant_image)
            ProductionWorkflowStore(workflow_path).register_candidate_version(
                slot_id=scene_state_slot_id("咖啡馆", "雨中咖啡馆", "master"),
                asset_kind="scene_state",
                version_id="variant-v1",
                asset_path=variant_image.relative_to(tmp_path).as_posix(),
                source_attempt_id="concurrent-variant",
                qc_passed=True,
                generation_metadata=None,
                actor="test",
                at=datetime.now(UTC),
            )
        elif remove_variant_after_read:
            remove_variant_after_read = False
            variant_image.unlink()
        return snapshot

    monkeypatch.setattr(runner, "_scene_reference_catalog_snapshot", observe_snapshot)

    task = asyncio.create_task(runner._run_episode_asset_planner(envelope, ctx))
    await draft_started.wait()
    activator = threading.Thread(target=FakeDirectorPlanStore(tmp_path).activate_new)
    activator.start()
    await asyncio.sleep(0.05)
    assert activation_finished.is_set()
    release_draft.set()
    with pytest.raises(ValueError, match="ACTIVE_DIRECTOR_PLAN_STALE"):
        await task
    await asyncio.to_thread(activator.join, 5)

    assert published_revision_ids == []
    assert not workflow_path.exists()

    FakeDirectorPlanStore.active = old_plan
    activation_finished.clear()
    block_publish = True
    inject_variant_after_read = True
    task = asyncio.create_task(runner._run_episode_asset_planner(envelope, ctx))
    await publish_started.wait()
    activator = threading.Thread(target=FakeDirectorPlanStore(tmp_path).activate_new)
    activator.start()
    await asyncio.sleep(0.05)
    assert not activation_finished.is_set()
    release_publish.set()
    result = await task
    await asyncio.to_thread(activator.join, 5)

    assert published_revision_ids == ["director-old"]
    assert activation_finished.is_set()
    assert result["binding_count"] == 1
    assert result["binding_statuses"] == {"ready": 1}
    assert published_entity_ids == ["咖啡馆"]
    assert replaced_entity_ids == ["雨中咖啡馆"]

    published_entity_ids.clear()
    replaced_entity_ids.clear()
    FakeDirectorPlanStore.active = old_plan
    block_publish = False
    remove_variant_after_read = True
    result = await runner._run_episode_asset_planner(envelope, ctx)

    assert result["binding_statuses"] == {"ready": 1}
    assert published_entity_ids == ["雨中咖啡馆"]
    assert replaced_entity_ids == ["咖啡馆"]

    workflow_path.unlink()
    FakeDirectorPlanStore.active = old_plan
    block_publish = False
    for publish_error in ("scene catalog conflict", "binding insert failed"):
        with pytest.raises(ValueError, match=publish_error):
            await runner._run_episode_asset_planner(envelope, ctx)
        assert not workflow_path.exists()

    publish_error = ""
    block_publish = True
    publish_started.clear()
    release_publish.clear()
    task = asyncio.create_task(runner._run_episode_asset_planner(envelope, ctx))
    await publish_started.wait()
    task.cancel()
    release_publish.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not workflow_path.exists()

    block_publish = False
    catalog_read = 0

    def always_changing_snapshot(**kwargs):
        nonlocal catalog_read
        available, revision = real_snapshot(**kwargs)
        catalog_read += 1
        return available, f"{revision}-{catalog_read}"

    monkeypatch.setattr(
        runner, "_scene_reference_catalog_snapshot", always_changing_snapshot
    )
    with pytest.raises(ValueError, match="SCENE_REFERENCE_CATALOG_BUSY"):
        await runner._run_episode_asset_planner(envelope, ctx)
    assert replaced_entity_ids == []
    assert not workflow_path.exists()


@pytest.mark.asyncio
async def test_scene_commit_reports_cache_refresh_pending_without_rolling_back(
    tmp_path, monkeypatch
):
    store = SQLiteStore("owner/project", output_dir=str(tmp_path), state_dir=str(tmp_path))
    await store.initialize()
    await store.add_episode(NovelEpisode(number=1, title="第一集"))

    original_refresh = store.load_graph_state

    async def fail_refresh():
        raise RuntimeError("cache unavailable")

    monkeypatch.setattr(store, "load_graph_state", fail_refresh)
    result = await store.publish_scene_plan_atomic(
        episode_number=1,
        scenes=(NovelScene(name="咖啡馆"),),
        scene_menu=(SceneMenuItem(scene_id="咖啡馆"),),
        scene_baseline_digests={},
        episode_scene_menu_baseline_digest=store.asset_menu_baseline_digest(
            [], asset_kind="scene"
        ),
        bindings=(_binding("scene_base", "咖啡馆"),),
    )

    assert result == {"committed": True, "cache_refresh_pending": True}
    assert await store.get_scene("咖啡馆") is not None
    assert (await store.list_episodes())[0].scene_menu == [SceneMenuItem(scene_id="咖啡馆")]
    assert await store.list_planned_reference_bindings(1) == [
        _binding("scene_base", "咖啡馆")
    ]
    assert store._cache_refresh_pending is True

    monkeypatch.setattr(store, "load_graph_state", original_refresh)
    await store.load_graph_state()
    assert store._cache_refresh_pending is False


@pytest.mark.asyncio
async def test_scene_publish_rejects_stale_unchanged_catalog_used_for_projection(tmp_path):
    store = SQLiteStore("owner/project", output_dir=str(tmp_path), state_dir=str(tmp_path))
    await store.initialize()
    unchanged = NovelScene(name="旧车站", environment_prompt="投影时描述")
    await store.add_scene(unchanged)
    await store.add_episode(NovelEpisode(number=1, title="第一集"))
    scene_catalog_baseline = store.scene_catalog_baseline_digest([unchanged])
    prop_catalog_baseline = store.prop_catalog_baseline_digest([])
    await store.update_scene("旧车站", environment_prompt="并发修改")

    with pytest.raises(ValueError, match="scene catalog conflict"):
        await store.publish_scene_plan_atomic(
            episode_number=1,
            scenes=(NovelScene(name="咖啡馆"),),
            scene_menu=(SceneMenuItem(scene_id="咖啡馆"),),
            scene_baseline_digests={},
            episode_scene_menu_baseline_digest=store.asset_menu_baseline_digest(
                [], asset_kind="scene"
            ),
            scene_catalog_baseline_digest=scene_catalog_baseline,
            prop_catalog_baseline_digest=prop_catalog_baseline,
            bindings=(_binding("scene_base", "咖啡馆"),),
        )

    assert await store.get_scene("咖啡馆") is None
    assert await store.list_planned_reference_bindings(1) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["create", "delete"])
async def test_scene_publish_rejects_created_or_deleted_projection_catalog_entity(
    tmp_path, mutation
):
    store = SQLiteStore("owner/project", output_dir=str(tmp_path), state_dir=str(tmp_path))
    await store.initialize()
    existing_prop = NovelProp(name="旧钥匙", visual_prompt="铜钥匙")
    await store.add_prop(existing_prop)
    await store.add_episode(NovelEpisode(number=1, title="第一集"))
    scene_catalog_baseline = store.scene_catalog_baseline_digest([])
    prop_catalog_baseline = store.prop_catalog_baseline_digest([existing_prop])
    if mutation == "create":
        await store.add_prop(NovelProp(name="新钥匙", visual_prompt="银钥匙"))
    else:
        assert await store.delete_prop("旧钥匙") is True

    with pytest.raises(ValueError, match="prop catalog conflict"):
        await store.publish_scene_plan_atomic(
            episode_number=1,
            scenes=(NovelScene(name="咖啡馆"),),
            scene_menu=(SceneMenuItem(scene_id="咖啡馆"),),
            scene_baseline_digests={},
            episode_scene_menu_baseline_digest=store.asset_menu_baseline_digest(
                [], asset_kind="scene"
            ),
            scene_catalog_baseline_digest=scene_catalog_baseline,
            prop_catalog_baseline_digest=prop_catalog_baseline,
            bindings=(_binding("scene_base", "咖啡馆"),),
        )

    assert await store.get_scene("咖啡馆") is None
    assert await store.list_planned_reference_bindings(1) == []


def test_legacy_string_menu_digest_preserves_string_identity():
    assert SQLiteStore.asset_menu_baseline_digest(
        ["场景甲"], asset_kind="scene"
    ) != SQLiteStore.asset_menu_baseline_digest(["场景乙"], asset_kind="scene")


def test_mixed_legacy_menu_digest_preserves_source_order():
    structured = {"scene_id": "咖啡馆"}
    assert SQLiteStore.asset_menu_baseline_digest(
        ["旧场景", structured], asset_kind="scene"
    ) != SQLiteStore.asset_menu_baseline_digest(
        [structured, "旧场景"], asset_kind="scene"
    )


@pytest.mark.asyncio
async def test_runner_cache_refresh_treats_cognee_false_status_as_pending():
    from novelvideo.task_backend.runners.episode_assets import _refresh_asset_caches

    class SQLiteCache:
        async def load_graph_state(self):
            return None

    class CogneeCache:
        async def load_graph_state(self):
            return False

    assert await _refresh_asset_caches(SQLiteCache(), CogneeCache()) is False
