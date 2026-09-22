import pytest

from novelvideo.narrative_groups import service
from novelvideo.screenplay_semantics.models import ScreenplaySemanticRevision, SemanticValidationReport
from novelvideo.screenplay_semantics.store import ScreenplaySemanticStore
from tests.director_plan.test_legacy_projection import _activate, _group
from tests.screenplay_semantics.test_models import beat, scene, source_block


def _source_project(tmp_path, dialogue_id="line-8"):
    block = source_block().model_copy(update={"kind": "dialogue", "text": "林默：灯还亮着。"})
    semantic = ScreenplaySemanticRevision.new(
        episode=1, source_revision=1, source_hash="sha256:abc",
        scenes=(scene(blocks=(block,)),), beats=(beat(),),
        validation_report=SemanticValidationReport(passed=True),
    )
    store = ScreenplaySemanticStore(tmp_path)
    store.save(semantic)
    store.activate(1, semantic.revision_id, expected_source_revision=1)
    group = _group("g1", 1, ("line-8",), ("shot-1",))
    group = group.model_copy(update={"shots": (group.shots[0].model_copy(
        update={"dialogue_source_ids": (dialogue_id,)}),)})
    _activate(tmp_path, (group,), semantic_revision_id=semantic.revision_id)


def test_generation_projects_exact_dialogue_and_time_from_frozen_source(tmp_path):
    _source_project(tmp_path)
    [shot] = service.generation_beats_for_group(tmp_path, 1, "g1", [])
    assert shot["dialogue"] == "灯还亮着。"
    assert shot["speaker"] == "林默"
    assert shot["dialogue_required"] is True
    assert shot["time_of_day"] == "night"
    assert "night" in shot["visual_description"]
    assert shot["scene_name"] == "hallway"


def test_missing_dialogue_reference_fails_before_generation(tmp_path):
    _source_project(tmp_path, dialogue_id="missing-line")
    with pytest.raises(ValueError, match="dialogue source.*missing-line"):
        service.generation_beats_for_group(tmp_path, 1, "g1", [])
