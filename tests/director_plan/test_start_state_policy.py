import pytest

from novelvideo.director_plan.prompts import _EPISODE_AUTHORITY, _REPAIR_AUTHORITY


@pytest.mark.parametrize("prompt", [_EPISODE_AUTHORITY, _REPAIR_AUTHORITY], ids=["generation", "repair"])
def test_generation_and_repair_define_a_self_contained_still_start(prompt):
    assert "visible_start_state" in prompt
    assert "self-contained still frame" in prompt
    assert "prop scale" in prompt
    assert "blank" in prompt
    assert "subject, action and visible_end_state" in prompt
