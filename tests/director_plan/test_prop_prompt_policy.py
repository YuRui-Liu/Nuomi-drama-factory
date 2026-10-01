from novelvideo.director_plan.prompts import _EPISODE_AUTHORITY, _REPAIR_AUTHORITY


def test_generation_and_repair_keep_props_catalog_only_and_optional():
    for prompt in (_EPISODE_AUTHORITY, _REPAIR_AUTHORITY):
        assert 'Do not request new props.' in prompt
        assert 'existing prop catalog' in prompt
        assert 'required=false' in prompt
        assert 'explicitly selected' in prompt
