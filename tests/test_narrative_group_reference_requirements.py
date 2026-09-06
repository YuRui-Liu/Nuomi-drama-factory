from novelvideo.narrative_groups.reference_requirements import (
    ReferenceRequirement,
    reference_requirements_for_shots,
    parse_scene_requirement,
)


def test_reference_requirement_is_an_immutable_transport_record() -> None:
    requirement = ReferenceRequirement(
        id="scene:palace:snow",
        kind="scene_variant",
        entity_id="palace_snow",
        base_entity_id="palace",
        variant_id="snow",
        shot_ids=("shot-1", "shot-2"),
    )

    assert requirement.shot_ids == ("shot-1", "shot-2")
    assert requirement.required is True


def test_parse_scene_requirement_prefers_longest_known_base_name() -> None:
    assert parse_scene_requirement(
        "palace_great_hall_snow_night",
        {"palace", "palace_great_hall", "palace_great_hall_snow_night"},
    ) == ("palace_great_hall_snow_night", "")
    assert parse_scene_requirement(
        "palace_great_hall_snow_night",
        {"palace", "palace_great_hall"},
    ) == ("palace_great_hall", "snow_night")


def test_parse_scene_requirement_treats_unknown_value_as_its_own_base() -> None:
    assert parse_scene_requirement("unknown_snow", {"palace"}) == (
        "unknown_snow",
        "",
    )


def test_reference_requirements_merge_shot_ids_but_keep_variants_separate() -> None:
    shots = [
        {
            "id": "shot-1",
            "asset_requirements": [
                {"kind": "character_identity", "entity_key": "hero"},
                {
                    "kind": "scene_state",
                    "entity_key": "palace_great_hall_snow",
                },
            ],
        },
        {
            "id": "shot-2",
            "asset_requirements": [
                {"kind": "character_identity", "entity_key": "hero"},
                {
                    "kind": "scene_state",
                    "entity_key": "palace_great_hall_rain",
                },
            ],
        },
    ]

    result = reference_requirements_for_shots(
        shots, known_scene_ids={"palace", "palace_great_hall"}
    )

    assert [(item.kind, item.entity_id, item.variant_id, item.shot_ids) for item in result] == [
        ("character_identity", "hero", "", ("shot-1", "shot-2")),
        ("scene_variant", "palace_great_hall_snow", "snow", ("shot-1",)),
        ("scene_variant", "palace_great_hall_rain", "rain", ("shot-2",)),
    ]


def test_reference_requirements_merge_optional_then_required_with_required_or() -> None:
    result = reference_requirements_for_shots(
        [
            {
                "id": "shot-1",
                "asset_requirements": [
                    {"kind": "prop", "entity_key": "letter", "required": False}
                ],
            },
            {
                "id": "shot-2",
                "asset_requirements": [
                    {"kind": "prop", "entity_key": "letter", "required": True}
                ],
            },
        ]
    )

    assert len(result) == 1
    assert result[0].id == "prop:letter"
    assert result[0].shot_ids == ("shot-1", "shot-2")
    assert result[0].required is True


def test_reference_requirements_merge_required_then_optional_with_required_or() -> None:
    result = reference_requirements_for_shots(
        [
            {
                "id": "shot-1",
                "asset_requirements": [
                    {"kind": "prop", "entity_key": "letter", "required": True}
                ],
            },
            {
                "id": "shot-2",
                "asset_requirements": [
                    {"kind": "prop", "entity_key": "letter", "required": False}
                ],
            },
        ]
    )

    assert len(result) == 1
    assert result[0].id == "prop:letter"
    assert result[0].shot_ids == ("shot-1", "shot-2")
    assert result[0].required is True


def test_scene_requirements_merge_flat_and_structured_variants_canonically() -> None:
    result = reference_requirements_for_shots(
        [
            {
                "id": "shot-1",
                "asset_requirements": [
                    {"kind": "scene_state", "entity_key": "palace_snow"}
                ],
            },
            {
                "id": "shot-2",
                "asset_requirements": [
                    {
                        "kind": "scene_state",
                        "entity_key": "palace",
                        "visible_change": "snow",
                    }
                ],
            },
        ],
        known_scene_ids={"palace"},
    )

    assert len(result) == 1
    assert result[0] == ReferenceRequirement(
        id="scene_variant:palace:snow",
        kind="scene_variant",
        entity_id="palace_snow",
        base_entity_id="palace",
        variant_id="snow",
        shot_ids=("shot-1", "shot-2"),
    )


def test_scene_requirement_does_not_split_unknown_flat_name() -> None:
    [requirement] = reference_requirements_for_shots(
        [
            {
                "id": "shot-1",
                "asset_requirements": [
                    {"kind": "scene_state", "entity_key": "unknown_snow"}
                ],
            }
        ],
        known_scene_ids={"palace"},
    )

    assert requirement.kind == "scene_base"
    assert requirement.entity_id == "unknown_snow"
    assert requirement.base_entity_id == "unknown_snow"
    assert requirement.variant_id == ""
