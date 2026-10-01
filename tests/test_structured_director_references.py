from novelvideo.narrative_groups.planned_binding_service import (
    bindings_for_director_plan, required_binding_keys_for_director_group,
    _preview_from_bindings,
)
from novelvideo.production_workflow import ProductionWorkflowStore


def test_structural_character_blocking_and_scene_require_missing_references():
    shot = {"id": "s1", "subject": "水滴、水箱和居民甲", "asset_requirements": [],
            "cinematography": {"subjects": [{"subject_id": "居民甲"}, {"subject_id": "居民乙"}]}}
    group = {"id": "g1", "scene_anchor": "水箱区", "shots": [shot]}
    expected = {("character_identity", "居民甲", ""), ("character_identity", "居民乙", ""), ("scene_base", "水箱区", "")}
    assert required_binding_keys_for_director_group({"groups": [group]}, "g1") == expected
    bindings = bindings_for_director_plan(project_id="p", episode_number=1,
        source_plan_revision_id="r1", groups=[group], shots=[shot], characters=[], scenes=[], props=[])
    assert {(b.asset_kind, b.entity_id, "") for b in bindings} == expected
    assert all(b.required and b.status == "missing_asset" for b in bindings)


def test_insert_shot_does_not_invent_characters_from_prose():
    shot = {"id": "s1", "subject": "水滴与水箱", "asset_requirements": [], "cinematography": {"subjects": []}}
    group = {"id": "g1", "scene_anchor": "水箱区", "shots": [shot]}
    assert required_binding_keys_for_director_group({"groups": [group]}, "g1") == {("scene_base", "水箱区", "")}


def test_unpublished_structural_requirements_are_visible_as_missing_bindings(tmp_path):
    preview = _preview_from_bindings([], ProductionWorkflowStore(tmp_path / "workflow.json"),
        project_id="p", episode_number=1, group_id="g1", project_dir=tmp_path, max_images=8,
        active_plan_revision_id="r1", required_binding_keys=frozenset({("character_identity", "居民甲", ""), ("scene_base", "水箱区", "")}))
    assert len(preview.bindings) == 2
    assert all(b.status == "missing_asset" and b.required and not b.selected_by_default for b in preview.bindings)
    assert preview.warnings
