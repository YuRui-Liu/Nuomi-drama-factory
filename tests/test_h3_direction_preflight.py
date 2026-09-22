import pytest

from tests.shot_continuity.test_cinematography import shot
from tests.shot_continuity.test_builder import _plan


def test_new_video_cannot_use_empty_legacy_directions():
    from novelvideo.director_plan.cinematography import require_production_directions
    with pytest.raises(ValueError, match="cinematography_missing"):
        require_production_directions(_plan(shot(cinematography=None)), "group-1")


def test_new_video_requires_active_plan_and_group():
    from novelvideo.director_plan.cinematography import require_production_directions
    with pytest.raises(ValueError, match="director_plan_required"):
        require_production_directions(None, "group-1")
    with pytest.raises(ValueError, match="director_group_missing"):
        require_production_directions(_plan(shot()), "absent")


def test_complete_director_facts_pass_without_mutation():
    from novelvideo.director_plan.cinematography import require_production_directions
    value = _plan(shot())
    before = value.model_dump_json()
    require_production_directions(value, "group-1")
    assert value.model_dump_json() == before


def test_v3_plan_validation_requires_directions():
    from novelvideo.director_plan.validation import validate_director_plan
    from novelvideo.director_plan.models import SourceSpan
    value = _plan(shot(cinematography=None)).model_copy(update={"prompt_version": "director-plan-v3"})
    result = validate_director_plan(value, [SourceSpan(id="span-1", ordinal=1,
        scene="attic", time="night", text="climb")])
    assert "cinematography_missing" in {item.code for item in result.issues}


def test_api_rejects_unplanned_video_before_enqueue(monkeypatch, tmp_path):
    from novelvideo.api.routes import narrative_groups
    from tests.test_api_narrative_groups import make_client
    client, backend = make_client(monkeypatch, tmp_path, enforce_direction_preflight=True)
    client.get("/api/v1/projects/demo/episodes/1/narrative-groups")
    response = client.post("/api/v1/projects/demo/episodes/1/narrative-groups/ng-01/video/generate",
                           json={"model": "runninghub:minimax-h3", "revision": 0, "plan_revision": 1})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "production_directions_required"
    assert not backend.calls
