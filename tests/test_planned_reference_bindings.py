from __future__ import annotations

import hashlib
import json

import pytest


def _binding(
    *,
    entity_id: str,
    asset_kind: str = "character_identity",
    group_ids: tuple[str, ...] = (),
    revision: str = "plan-r1",
    display_label: str = "林默 / 值班员",
):
    from novelvideo.narrative_groups.planned_bindings import PlannedReferenceBinding

    return PlannedReferenceBinding.create(
        project_id="project-1",
        episode_number=1,
        source_plan_revision_id=revision,
        asset_kind=asset_kind,
        entity_id=entity_id,
        asset_slot_id=f"slot:{entity_id}",
        group_ids=group_ids,
        beat_ids=("beat-1",),
        shot_ids=("shot-1",),
        status="ready",
        resolution="auto_matched",
        display_label=display_label,
    )


def test_binding_id_uses_only_stable_asset_identity_fields() -> None:
    first = _binding(entity_id="linmo-duty", group_ids=("group-1",))
    replanned = _binding(
        entity_id="linmo-duty",
        group_ids=("group-2",),
        revision="plan-r2",
        display_label="林默（值班）",
    )
    canonical = json.dumps(
        {
            "asset_kind": "character_identity",
            "asset_slot_id": "slot:linmo-duty",
            "base_entity_id": "",
            "entity_id": "linmo-duty",
            "episode_number": 1,
            "project_id": "project-1",
            "variant_id": "",
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )

    assert first.binding_id == "planned-ref-" + hashlib.sha256(
        canonical.encode("utf-8")
    ).hexdigest()[:24]
    assert replanned.binding_id == first.binding_id
    assert replanned.group_ids == ("group-2",)


def test_binding_id_uses_validated_canonical_values() -> None:
    integer_episode = _binding(entity_id="linmo-duty")
    string_episode = integer_episode.create(
        project_id="project-1",
        episode_number="1",  # type: ignore[arg-type]
        source_plan_revision_id="plan-r2",
        asset_kind="character_identity",
        entity_id="linmo-duty",
        asset_slot_id="slot:linmo-duty",
        status="ready",
        resolution="auto_matched",
        display_label="replanned",
    )

    assert string_episode.episode_number == 1
    assert string_episode.binding_id == integer_episode.binding_id


def test_direct_binding_construction_normalizes_required_text_fields() -> None:
    from novelvideo.narrative_groups.planned_bindings import PlannedReferenceBinding

    expected = _binding(entity_id="linmo-duty")
    direct = PlannedReferenceBinding(
        **{
            **expected.model_dump(),
            "project_id": " project-1 ",
            "source_plan_revision_id": " plan-r1 ",
            "entity_id": " linmo-duty ",
            "asset_slot_id": " slot:linmo-duty ",
            "display_label": " 林默 / 值班员 ",
        }
    )

    assert direct.project_id == "project-1"
    assert direct.source_plan_revision_id == "plan-r1"
    assert direct.entity_id == "linmo-duty"
    assert direct.asset_slot_id == "slot:linmo-duty"
    assert direct.display_label == "林默 / 值班员"
    assert direct.binding_id == expected.binding_id


@pytest.mark.parametrize(
    "field_name",
    [
        "project_id",
        "source_plan_revision_id",
        "entity_id",
        "asset_slot_id",
        "display_label",
    ],
)
def test_direct_binding_construction_rejects_blank_required_text(
    field_name: str,
) -> None:
    from pydantic import ValidationError

    from novelvideo.narrative_groups.planned_bindings import PlannedReferenceBinding

    with pytest.raises(ValidationError, match=field_name):
        PlannedReferenceBinding.model_validate(
            {**_binding(entity_id="linmo-duty").model_dump(), field_name: " \t "}
        )


def test_direct_binding_construction_rejects_forged_binding_id() -> None:
    from pydantic import ValidationError

    from novelvideo.narrative_groups.planned_bindings import PlannedReferenceBinding

    with pytest.raises(ValidationError, match="binding_id"):
        PlannedReferenceBinding.model_validate(
            {**_binding(entity_id="linmo-duty").model_dump(), "binding_id": "forged"}
        )


@pytest.mark.parametrize(
    ("base_entity_id", "variant_id"),
    [("", "night"), ("hall", "")],
)
def test_scene_variant_requires_base_and_variant_ids(
    base_entity_id: str, variant_id: str
) -> None:
    from pydantic import ValidationError

    from novelvideo.narrative_groups.planned_bindings import PlannedReferenceBinding

    valid = PlannedReferenceBinding.create(
        project_id="project-1",
        episode_number=1,
        source_plan_revision_id="plan-r1",
        asset_kind="scene_variant",
        entity_id="hall-night",
        base_entity_id="hall",
        variant_id="night",
        asset_slot_id="scene:hall:state:hall-night:master",
        status="ready",
        resolution="auto_matched",
        display_label="大厅 / 夜景",
    )
    with pytest.raises(ValidationError, match="scene_variant"):
        PlannedReferenceBinding.model_validate(
            {
                **valid.model_dump(),
                "base_entity_id": base_entity_id,
                "variant_id": variant_id,
            }
        )


@pytest.mark.parametrize("status", ["pending_confirmation", "missing_asset"])
def test_unresolved_bindings_allow_empty_slot_and_scene_variant_structure(
    status: str,
) -> None:
    from novelvideo.narrative_groups.planned_bindings import PlannedReferenceBinding

    binding = PlannedReferenceBinding.create(
        project_id="project-1",
        episode_number=1,
        source_plan_revision_id="plan-r1",
        asset_kind="scene_variant",
        entity_id="legacy-scene-state",
        asset_slot_id="",
        status=status,
        resolution="auto_matched",
        display_label="Legacy scene state",
    )

    assert binding.asset_kind == "scene_variant"
    assert binding.asset_slot_id == ""
    assert binding.base_entity_id == ""
    assert binding.variant_id == ""


@pytest.mark.parametrize("status", ["ready", "missing_image"])
def test_resolved_bindings_require_slot_and_scene_variant_structure(
    status: str,
) -> None:
    from pydantic import ValidationError

    from novelvideo.narrative_groups.planned_bindings import PlannedReferenceBinding

    with pytest.raises(ValidationError, match="asset_slot_id"):
        PlannedReferenceBinding.create(
            project_id="project-1",
            episode_number=1,
            source_plan_revision_id="plan-r1",
            asset_kind="scene_variant",
            entity_id="hall-night",
            asset_slot_id="",
            status=status,
            resolution="auto_matched",
            display_label="Hall night",
        )


def test_binding_is_frozen_forbids_extra_and_requires_positive_episode() -> None:
    from pydantic import ValidationError

    from novelvideo.narrative_groups.planned_bindings import PlannedReferenceBinding

    binding = _binding(entity_id="linmo-duty")
    with pytest.raises(ValidationError):
        binding.display_label = "changed"
    with pytest.raises(ValidationError):
        PlannedReferenceBinding.model_validate(
            {**binding.model_dump(), "unexpected": True}
        )
    with pytest.raises(ValidationError):
        PlannedReferenceBinding.model_validate(
            {**binding.model_dump(), "episode_number": 0}
        )


@pytest.mark.asyncio
async def test_store_lists_filters_and_gets_bindings_in_requested_order(tmp_path) -> None:
    from novelvideo.sqlite_store import SQLiteStore

    store = SQLiteStore(
        "test/planned-bindings",
        output_dir=str(tmp_path / "output"),
        state_dir=str(tmp_path / "state"),
    )
    try:
        first = _binding(entity_id="linmo-duty", group_ids=("group-1",))
        second = _binding(
            entity_id="hall",
            asset_kind="scene_base",
            group_ids=("group-2", "group-1"),
            display_label="大厅",
        )
        await store.replace_planned_reference_bindings_atomic(
            1, ("character_identity", "scene_base"), (first, second)
        )

        listed = await store.list_planned_reference_bindings(1)
        assert {item.binding_id for item in listed} == {
            first.binding_id,
            second.binding_id,
        }
        assert [item.binding_id for item in await store.list_planned_reference_bindings(
            1, group_id="group-2"
        )] == [second.binding_id]
        assert await store.get_planned_reference_bindings(
            1, (second.binding_id, first.binding_id)
        ) == [second, first]
        with pytest.raises(ValueError, match="duplicate"):
            await store.get_planned_reference_bindings(
                1, (first.binding_id, first.binding_id)
            )
        with pytest.raises(ValueError, match="missing-binding"):
            await store.get_planned_reference_bindings(
                1, (first.binding_id, "missing-binding")
            )
        with pytest.raises(ValueError, match=second.binding_id):
            await store.get_planned_reference_bindings(2, (second.binding_id,))
        with pytest.raises(ValueError, match="256"):
            await store.get_planned_reference_bindings(
                1, tuple(f"binding-{index}" for index in range(257))
            )
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_replace_bindings_rolls_back_delete_when_insert_fails(tmp_path) -> None:
    from novelvideo.sqlite_store import SQLiteStore

    store = SQLiteStore(
        "test/planned-bindings-rollback",
        output_dir=str(tmp_path / "output"),
        state_dir=str(tmp_path / "state"),
    )
    try:
        original = _binding(entity_id="linmo-duty")
        await store.replace_planned_reference_bindings_atomic(
            1, ("character_identity",), (original,)
        )
        replacement = _binding(entity_id="zhou", display_label="周警官")

        with pytest.raises(Exception):
            await store.replace_planned_reference_bindings_atomic(
                1,
                ("character_identity",),
                (replacement, replacement),
            )

        assert await store.list_planned_reference_bindings(1) == [original]
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_concurrent_binding_replacements_do_not_share_a_transaction(tmp_path) -> None:
    import asyncio

    from novelvideo.sqlite_store import SQLiteStore

    store = SQLiteStore(
        "test/planned-bindings-concurrent",
        output_dir=str(tmp_path / "output"),
        state_dir=str(tmp_path / "state"),
    )
    try:
        first = _binding(entity_id="linmo-duty")
        second = _binding(entity_id="zhou", display_label="周警官")
        await asyncio.gather(
            store.replace_planned_reference_bindings_atomic(
                1, ("character_identity",), (first,)
            ),
            store.replace_planned_reference_bindings_atomic(
                1, ("character_identity",), (second,)
            ),
        )

        listed = await store.list_planned_reference_bindings(1)
        assert listed in ([first], [second])
    finally:
        await store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("episode_number", "asset_kinds", "error"),
    [
        (0, ("character_identity",), "episode_number"),
        (1, ("character_identity_typo",), "asset_kinds"),
    ],
)
async def test_replace_bindings_rejects_invalid_empty_replacement_scope(
    tmp_path, episode_number, asset_kinds, error
) -> None:
    from novelvideo.sqlite_store import SQLiteStore

    store = SQLiteStore(
        "test/planned-bindings-invalid-scope",
        output_dir=str(tmp_path / "output"),
        state_dir=str(tmp_path / "state"),
    )
    try:
        with pytest.raises(ValueError, match=error):
            await store.replace_planned_reference_bindings_atomic(
                episode_number, asset_kinds, ()
            )
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_replace_bindings_rejects_binding_outside_replacement_scope(
    tmp_path,
) -> None:
    from novelvideo.sqlite_store import SQLiteStore

    store = SQLiteStore(
        "test/planned-bindings-mismatched-scope",
        output_dir=str(tmp_path / "output"),
        state_dir=str(tmp_path / "state"),
    )
    binding = _binding(entity_id="linmo-duty")
    try:
        with pytest.raises(ValueError, match="episode_number"):
            await store.replace_planned_reference_bindings_atomic(
                2, ("character_identity",), (binding,)
            )
        with pytest.raises(ValueError, match="asset_kind"):
            await store.replace_planned_reference_bindings_atomic(
                1, ("scene_base",), (binding,)
            )
    finally:
        await store.close()
