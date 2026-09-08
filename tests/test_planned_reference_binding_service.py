from __future__ import annotations

from copy import deepcopy

import pytest

from novelvideo.narrative_groups.planned_binding_service import (
    bindings_by_kind,
    bindings_for_director_plan,
)


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


def test_multiple_exact_candidates_are_pending_confirmation() -> None:
    [binding] = _project(
        shots=[_shot("shot-1", {"kind": "prop", "entity_key": "letter"})],
        props=[{"name": "letter"}, {"name": "letter"}],
    )

    assert binding.status == "pending_confirmation"


def test_missing_asset_and_explicit_missing_image_are_distinct() -> None:
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
    assert result[1].resolution == "auto_matched"


def test_explicit_missing_images_keep_canonical_entity_slots() -> None:
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
    assert [binding.resolution for binding in result] == ["auto_matched"] * 3
    assert result[1].asset_slot_id == "scene:hall:state:hall-night:master"


def test_projection_does_not_mutate_inputs_or_call_legacy_or_write_entry_points(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import builtins
    from pathlib import Path

    from novelvideo.narrative_groups import reference_requirements

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
    monkeypatch.setattr(builtins, "open", forbidden)
    monkeypatch.setattr(Path, "write_text", forbidden)
    monkeypatch.setattr(Path, "write_bytes", forbidden)

    result = _project(**inputs)

    assert result[0].status == "ready"
    assert inputs == before
