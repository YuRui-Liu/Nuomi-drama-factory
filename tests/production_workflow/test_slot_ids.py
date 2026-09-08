import pytest

from novelvideo.production_workflow.slot_ids import (
    character_state_slot_id,
    prop_reference_slot_id,
    scene_base_slot_id,
    scene_state_slot_id,
)


def test_slot_id_factories_preserve_existing_production_formats() -> None:
    assert character_state_slot_id("林默", "linmo-duty") == (
        "character:林默:state:linmo-duty"
    )
    assert scene_base_slot_id("大厅", "master") == "scene:大厅:base:master"
    assert scene_state_slot_id("hall", "hall-night", "reverse_master") == (
        "scene:hall:state:hall-night:reverse_master"
    )
    assert prop_reference_slot_id("手机") == "prop:手机:reference"


def test_slot_id_factories_do_not_normalize_non_empty_fields() -> None:
    assert prop_reference_slot_id(" 手机 ") == "prop: 手机 :reference"


@pytest.mark.parametrize(
    ("factory", "args"),
    [
        (character_state_slot_id, ("", "identity")),
        (character_state_slot_id, ("character", "  ")),
        (scene_base_slot_id, ("", "master")),
        (scene_state_slot_id, ("base", "", "master")),
        (scene_state_slot_id, ("base", "state", "")),
        (prop_reference_slot_id, (" ",)),
    ],
)
def test_slot_id_factories_reject_empty_fields(factory, args) -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        factory(*args)


@pytest.mark.parametrize(
    ("factory", "args"),
    [
        (character_state_slot_id, ("林:默", "identity")),
        (character_state_slot_id, ("林默", "id:1")),
        (scene_base_slot_id, ("大:厅", "master")),
        (scene_base_slot_id, ("大厅", "reverse:master")),
        (scene_state_slot_id, ("ha:ll", "hall-night", "master")),
        (scene_state_slot_id, ("hall", "hall:night", "master")),
        (scene_state_slot_id, ("hall", "hall-night", "mas:ter")),
        (prop_reference_slot_id, ("手:机",)),
    ],
)
def test_slot_id_factories_reject_collision_delimiter(factory, args) -> None:
    with pytest.raises(ValueError, match="colon"):
        factory(*args)


@pytest.mark.parametrize("value", ["line\nbreak", "tab\tvalue", "delete\x7fvalue"])
def test_slot_id_factories_reject_control_characters(value: str) -> None:
    with pytest.raises(ValueError, match="control"):
        prop_reference_slot_id(value)
