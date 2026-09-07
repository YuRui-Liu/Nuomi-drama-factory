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
