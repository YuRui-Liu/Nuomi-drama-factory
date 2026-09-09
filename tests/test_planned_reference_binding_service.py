from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

import pytest
from PIL import Image

from novelvideo.narrative_groups.planned_binding_service import (
    bindings_by_kind,
    bindings_for_director_plan,
    resolve_planned_reference_preview,
)
from novelvideo.narrative_groups.planned_bindings import PlannedReferenceBinding
from novelvideo.production_workflow import ProductionWorkflowStore


def _group(group_id: str, beat_id: str, *shot_ids: str) -> dict:
    return {"id": group_id, "beat_ids": [beat_id], "shot_ids": list(shot_ids)}


def _shot(shot_id: str, *requirements: dict) -> dict:
    return {"id": shot_id, "asset_requirements": list(requirements)}


def _project(**overrides):
    values = {
        "project_id": "project-1",
        "episode_number": 2,
        "source_plan_revision_id": "director-r3",
        "groups": [_group("group-1", "beat-1", "shot-1")],
        "shots": [],
        "characters": [],
        "scenes": [],
        "props": [],
    }
    values.update(overrides)
    return bindings_for_director_plan(**values)


class _BindingStore:
    def __init__(self, binding: PlannedReferenceBinding) -> None:
        self.binding = binding

    async def list_planned_reference_bindings(
        self, episode_number: int, group_id: str | None = None
    ) -> list[PlannedReferenceBinding]:
        if self.binding.episode_number != episode_number:
            return []
        if group_id is not None and group_id not in self.binding.group_ids:
            return []
        return [self.binding]


def test_projects_all_four_ready_kinds_with_canonical_slots() -> None:
    result = _project(
        shots=[
            _shot(
                "shot-1",
                {"kind": "character_identity", "entity_key": "linmo-duty"},
                {"kind": "scene_base", "entity_key": "hall"},
                {
                    "kind": "scene_state",
                    "entity_key": "hall",
                    "visible_change": "night",
                },
                {"kind": "prop", "entity_key": "letter"},
            )
        ],
        characters=[
            {
                "name": "Lin Mo",
                "identities": [
                    {
                        "identity_id": "linmo-duty",
                        "identity_name": "Duty",
                        "reference_images": ["assets/linmo-duty.png"],
                    }
                ],
            }
        ],
        scenes=[
            {
                "name": "hall",
                "base_scene_id": "",
                "variant_id": "",
                "master_image": "hall.png",
            },
            {
                "name": "hall-night",
                "base_scene_id": "hall",
                "variant_id": "night",
                "master_image": "hall-night.png",
            },
        ],
        props=[{"name": "letter", "reference_image": "letter.png"}],
    )

    assert [binding.asset_kind for binding in result] == [
        "character_identity",
        "scene_base",
        "scene_variant",
        "prop",
    ]
    assert [binding.status for binding in result] == ["ready"] * 4
    assert [binding.resolution for binding in result] == ["auto_matched"] * 4
    assert [binding.asset_slot_id for binding in result] == [
        "character:Lin Mo:state:linmo-duty",
        "scene:hall:base:master",
        "scene:hall:state:hall-night:master",
        "prop:letter:reference",
    ]
    variant = result[2]
    assert (variant.entity_id, variant.base_entity_id, variant.variant_id) == (
        "hall-night",
        "hall",
        "night",
    )
    assert bindings_by_kind(result)["scene_variant"] == (variant,)


def test_merges_scope_required_and_preserves_first_requirement_order() -> None:
    result = _project(
        groups=[
            _group("group-1", "beat-1", "shot-1"),
            _group("group-2", "beat-2", "shot-2"),
        ],
        shots=[
            _shot(
                "shot-1",
                {"kind": "prop", "entity_key": "letter", "required": False},
                {"kind": "character_identity", "entity_key": "linmo-duty"},
            ),
            _shot(
                "shot-2",
                {"kind": "prop", "entity_key": "letter", "required": True},
            ),
        ],
        characters=[
            {
                "name": "Lin Mo",
                "identities": [
                    {
                        "identity_id": "linmo-duty",
                        "identity_name": "Duty",
                        "reference_images": ["identity.png"],
                    }
                ],
            }
        ],
        props=[{"name": "letter"}],
    )

    assert [binding.asset_kind for binding in result] == ["prop", "character_identity"]
    prop = result[0]
    assert prop.required is True
    assert prop.group_ids == ("group-1", "group-2")
    assert prop.beat_ids == ("beat-1", "beat-2")
    assert prop.shot_ids == ("shot-1", "shot-2")


@pytest.mark.parametrize(
    ("scenes", "requirement"),
    [
        (
            [{"name": "hall_night", "base_scene_id": "", "variant_id": ""}],
            {"kind": "scene_state", "entity_key": "hall_night", "visible_change": ""},
        ),
        (
            [{"name": "hall-night", "base_scene_id": "hall", "variant_id": "night"}],
            {"kind": "scene_state", "entity_key": "hall", "visible_change": ""},
        ),
    ],
)
def test_scene_state_without_unique_structured_match_is_pending_confirmation(
    scenes: list[dict], requirement: dict
) -> None:
    [binding] = _project(shots=[_shot("shot-1", requirement)], scenes=scenes)

    assert binding.status == "pending_confirmation"


def test_unstructured_scene_state_does_not_collide_with_scene_base_binding() -> None:
    result = _project(
        shots=[
            _shot(
                "shot-1",
                {"kind": "scene_base", "entity_key": "hall_night"},
                {"kind": "scene_state", "entity_key": "hall_night"},
            )
        ]
    )

    assert [binding.asset_kind for binding in result] == [
        "scene_base",
        "scene_variant",
    ]
    assert [binding.status for binding in result] == [
        "missing_asset",
        "pending_confirmation",
    ]
    assert len({binding.binding_id for binding in result}) == 2
    assert [binding.asset_slot_id for binding in result] == ["", ""]


def test_distinct_unstructured_scene_states_remain_distinct_and_merge_own_scope() -> None:
    result = _project(
        groups=[
            _group("group-1", "beat-1", "shot-1"),
            _group("group-2", "beat-2", "shot-2"),
            _group("group-3", "beat-3", "shot-3"),
        ],
        shots=[
            _shot("shot-1", {"kind": "scene_state", "entity_key": "hall_night"}),
            _shot("shot-2", {"kind": "scene_state", "entity_key": "street_day"}),
            _shot("shot-3", {"kind": "scene_state", "entity_key": "hall_night"}),
        ],
    )

    assert [binding.entity_id for binding in result] == ["hall_night", "street_day"]
    assert result[0].group_ids == ("group-1", "group-3")
    assert result[0].beat_ids == ("beat-1", "beat-3")
    assert result[0].shot_ids == ("shot-1", "shot-3")
    assert result[1].group_ids == ("group-2",)


def test_multiple_exact_candidates_are_pending_confirmation() -> None:
    [binding] = _project(
        shots=[_shot("shot-1", {"kind": "prop", "entity_key": "letter"})],
        props=[{"name": "letter"}, {"name": "letter"}],
    )

    assert binding.status == "pending_confirmation"


def test_missing_asset_and_unavailable_identity_fallback_are_distinct() -> None:
    result = _project(
        shots=[
            _shot(
                "shot-1",
                {"kind": "prop", "entity_key": "absent"},
                {"kind": "character_identity", "entity_key": "linmo-duty"},
            )
        ],
        characters=[
            {
                "name": "Lin Mo",
                "identities": [
                    {
                        "identity_id": "linmo-duty",
                        "identity_name": "Duty",
                        "reference_images": [],
                    }
                ],
            }
        ],
    )

    assert [(binding.asset_kind, binding.status) for binding in result] == [
        ("prop", "missing_asset"),
        ("character_identity", "missing_image"),
    ]
    assert result[1].resolution == "explicit_fallback"
    assert result[1].asset_slot_id == "character:Lin Mo:portrait"


@pytest.mark.asyncio
async def test_identity_portrait_fallback_requires_valid_workflow_version(
    tmp_path: Path,
) -> None:
    [binding] = _project(
        shots=[
            _shot(
                "shot-1",
                {"kind": "character_identity", "entity_key": "linmo-duty"},
            )
        ],
        characters=[
            {
                "name": "Lin Mo",
                "identities": [
                    {
                        "identity_id": "linmo-duty",
                        "identity_name": "Duty",
                        "reference_images": [],
                    }
                ],
            }
        ],
        available_character_portraits=("Lin Mo",),
    )
    workflow = ProductionWorkflowStore(tmp_path / "state" / "workflow.json")

    unavailable = await resolve_planned_reference_preview(
        _BindingStore(binding),
        workflow,
        project_id="project-1",
        episode_number=2,
        group_id="group-1",
        project_dir=tmp_path,
    )

    assert unavailable.bindings[0].status == "missing_asset"
    assert unavailable.bindings[0].selected_by_default is False
    assert unavailable.bindings[0].version_id == ""
    assert unavailable.bindings[0].thumbnail_url == ""

    portrait_path = tmp_path / "assets" / "characters" / "lin-mo.png"
    portrait_path.parent.mkdir(parents=True)
    Image.new("RGB", (8, 8), "red").save(portrait_path)
    workflow.register_candidate_version(
        slot_id=binding.asset_slot_id,
        asset_kind="character_portrait",
        version_id="portrait-v1",
        asset_path=str(portrait_path),
        source_attempt_id="portrait-attempt-1",
        qc_passed=True,
        generation_metadata=None,
        actor="test",
        at=datetime.now(UTC),
    )

    resolved = await resolve_planned_reference_preview(
        _BindingStore(binding),
        workflow,
        project_id="project-1",
        episode_number=2,
        group_id="group-1",
        project_dir=tmp_path,
    )

    assert resolved.bindings[0].status == "ready"
    assert resolved.bindings[0].selected_by_default is True
    assert resolved.bindings[0].asset_slot_id == "character:Lin Mo:portrait"
    assert resolved.bindings[0].version_id == "portrait-v1"
    assert resolved.bindings[0].thumbnail_url == str(portrait_path)


@pytest.mark.asyncio
async def test_identity_portrait_fallback_rejects_wrong_workflow_slot_kind(
    tmp_path: Path,
) -> None:
    [binding] = _project(
        shots=[
            _shot(
                "shot-1",
                {"kind": "character_identity", "entity_key": "linmo-duty"},
            )
        ],
        characters=[
            {
                "name": "Lin Mo",
                "identities": [
                    {
                        "identity_id": "linmo-duty",
                        "identity_name": "Duty",
                        "reference_images": [],
                    }
                ],
            }
        ],
        available_character_portraits=("Lin Mo",),
    )
    portrait_path = tmp_path / "assets" / "characters" / "lin-mo.png"
    portrait_path.parent.mkdir(parents=True)
    Image.new("RGB", (8, 8), "red").save(portrait_path)
    workflow = ProductionWorkflowStore(tmp_path / "state" / "workflow.json")
    workflow.register_candidate_version(
        slot_id=binding.asset_slot_id,
        asset_kind="character_state",
        version_id="wrong-kind-v1",
        asset_path=str(portrait_path),
        source_attempt_id="attempt-1",
        qc_passed=True,
        generation_metadata=None,
        actor="test",
        at=datetime.now(UTC),
    )

    preview = await resolve_planned_reference_preview(
        _BindingStore(binding),
        workflow,
        project_id="project-1",
        episode_number=2,
        group_id="group-1",
        project_dir=tmp_path,
    )

    assert preview.bindings[0].status == "missing_asset"
    assert preview.bindings[0].selected_by_default is False
    assert preview.bindings[0].version_id == ""


def test_identity_without_image_field_is_unknown_and_remains_ready() -> None:
    [binding] = _project(
        shots=[
            _shot(
                "shot-1",
                {"kind": "character_identity", "entity_key": "linmo-duty"},
            )
        ],
        characters=[
            {
                "name": "Lin Mo",
                "identities": [
                    {"identity_id": "linmo-duty", "identity_name": "Duty"}
                ],
            }
        ],
    )

    assert binding.status == "ready"
    assert binding.asset_slot_id == "character:Lin Mo:state:linmo-duty"


@pytest.mark.parametrize(
    ("bad_requirement", "characters", "scenes", "props", "expected_identity"),
    [
        (
            {"kind": "character_identity", "entity_key": "linmo-duty"},
            [
                {
                    "name": "Lin:Mo",
                    "identities": [
                        {
                            "identity_id": "linmo-duty",
                            "reference_images": ["identity.png"],
                        }
                    ],
                }
            ],
            [],
            [],
            ("character_identity", "linmo-duty", "", ""),
        ),
        (
            {"kind": "scene_base", "entity_key": "hall:west"},
            [],
            [{"name": "hall:west", "base_scene_id": "", "variant_id": ""}],
            [],
            ("scene_base", "hall:west", "", ""),
        ),
        (
            {
                "kind": "scene_state",
                "entity_key": "hall:west",
                "visible_change": "night",
            },
            [],
            [
                {
                    "name": "hall-night",
                    "base_scene_id": "hall:west",
                    "variant_id": "night",
                }
            ],
            [],
            ("scene_variant", "hall-night", "hall:west", "night"),
        ),
        (
            {"kind": "prop", "entity_key": "letter\x00sealed"},
            [],
            [],
            [{"name": "letter\x00sealed"}, {"name": "normal"}],
            ("prop", "letter\x00sealed", "", ""),
        ),
    ],
)
def test_invalid_canonical_slot_isolated_as_pending_and_later_binding_survives(
    bad_requirement: dict,
    characters: list[dict],
    scenes: list[dict],
    props: list[dict],
    expected_identity: tuple[str, str, str, str],
) -> None:
    if not any(prop["name"] == "normal" for prop in props):
        props = [*props, {"name": "normal"}]
    result = _project(
        shots=[
            _shot(
                "shot-1",
                bad_requirement,
                {"kind": "prop", "entity_key": "normal"},
            )
        ],
        characters=characters,
        scenes=scenes,
        props=props,
    )

    bad, normal = result
    assert (
        bad.asset_kind,
        bad.entity_id,
        bad.base_entity_id,
        bad.variant_id,
    ) == expected_identity
    assert bad.status == "pending_confirmation"
    assert bad.asset_slot_id == ""
    assert bad.group_ids == ("group-1",)
    assert bad.beat_ids == ("beat-1",)
    assert bad.shot_ids == ("shot-1",)
    assert normal.status == "ready"
    assert normal.asset_slot_id == "prop:normal:reference"


@pytest.mark.parametrize(
    ("requirement", "characters", "scenes", "expected_kind"),
    [
        (
            {"kind": "character_identity", "entity_key": "identity-1"},
            [
                {
                    "name": "",
                    "identities": [
                        {
                            "identity_id": "identity-1",
                            "identity_name": "",
                            "reference_images": ["identity.png"],
                        }
                    ],
                }
            ],
            [],
            "character_identity",
        ),
        (
            {
                "kind": "scene_state",
                "entity_key": "hall",
                "visible_change": "night",
            },
            [],
            [{"name": "", "base_scene_id": "hall", "variant_id": "night"}],
            "scene_variant",
        ),
    ],
)
def test_empty_matched_metadata_falls_back_only_for_pending_binding_record(
    requirement: dict,
    characters: list[dict],
    scenes: list[dict],
    expected_kind: str,
) -> None:
    result = _project(
        shots=[
            _shot(
                "shot-1",
                requirement,
                {"kind": "prop", "entity_key": "normal"},
            )
        ],
        characters=characters,
        scenes=scenes,
        props=[{"name": "normal"}],
    )

    invalid, normal = result
    assert invalid.asset_kind == expected_kind
    assert invalid.status == "pending_confirmation"
    assert invalid.asset_slot_id == ""
    assert invalid.entity_id == requirement["entity_key"]
    assert invalid.display_label == requirement["entity_key"]
    assert normal.status == "ready"
    assert normal.asset_slot_id == "prop:normal:reference"


def test_explicit_missing_variant_and_base_images_keep_base_fallback_slot() -> None:
    result = _project(
        shots=[
            _shot(
                "shot-1",
                {"kind": "scene_base", "entity_key": "hall"},
                {
                    "kind": "scene_state",
                    "entity_key": "hall",
                    "visible_change": "night",
                },
                {"kind": "prop", "entity_key": "letter"},
            )
        ],
        scenes=[
            {
                "name": "hall",
                "base_scene_id": "",
                "variant_id": "",
                "master_image": "",
            },
            {
                "name": "hall-night",
                "base_scene_id": "hall",
                "variant_id": "night",
                "master_image": "",
            },
        ],
        props=[{"name": "letter", "reference_image": ""}],
    )

    assert [binding.status for binding in result] == ["missing_image"] * 3
    assert [binding.resolution for binding in result] == [
        "auto_matched",
        "explicit_fallback",
        "auto_matched",
    ]
    assert result[1].entity_id == "hall"
    assert result[1].asset_slot_id == "scene:hall:base:master"


def test_projection_does_not_mutate_inputs_or_call_write_entry_points(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import builtins
    from pathlib import Path

    from novelvideo.narrative_groups import reference_requirements
    from novelvideo.sqlite_store import SQLiteStore

    inputs = {
        "groups": [_group("group-1", "beat-1", "shot-1")],
        "shots": [_shot("shot-1", {"kind": "scene_base", "entity_key": "hall"})],
        "characters": [],
        "scenes": [{"name": "hall", "base_scene_id": "", "variant_id": ""}],
        "props": [],
    }
    before = deepcopy(inputs)

    def forbidden(*_args, **_kwargs):
        raise AssertionError(
            "projection attempted a legacy, persistence, or file write"
        )

    monkeypatch.setattr(reference_requirements, "parse_scene_requirement", forbidden)
    monkeypatch.setattr(
        SQLiteStore, "replace_planned_reference_bindings_atomic", forbidden
    )
    monkeypatch.setattr(builtins, "open", forbidden)
    monkeypatch.setattr(Path, "write_text", forbidden)
    monkeypatch.setattr(Path, "write_bytes", forbidden)

    result = _project(**inputs)

    assert result[0].status == "ready"
    assert inputs == before
