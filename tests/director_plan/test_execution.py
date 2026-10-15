from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from novelvideo.director_plan.execution import video_duration_issues
from novelvideo.director_plan.prompts import CREATIVE_AUTHORITY, build_episode_prompt
from tests.director_plan.creative_fixtures import creative_shot
from tests.director_plan.test_models import make_group
from tests.director_plan.test_planner import episode


@pytest.mark.parametrize("duration,blocked", [(3, True), (4, False), (15, False), (25, True)])
def test_creative_duration_boundaries(duration, blocked):
    group = make_group(creative_contract_version=1, shots=(creative_shot(duration=duration),))
    assert bool(video_duration_issues((group,))) is blocked


def test_author_and_critic_receive_executable_contract():
    for prompt in (CREATIVE_AUTHORITY, build_episode_prompt(episode())):
        assert "4-15 seconds" in prompt
        assert "Do not recut to meet a provider limit" not in prompt
        assert "total runtime" in prompt


def test_creative_revision_keeps_the_initial_asset_reference_contract():
    from novelvideo.director_plan.prompts import ASSET_REFERENCE_CONTRACT, _REPAIR_AUTHORITY
    # Creative repair and independent QC both include CREATIVE_AUTHORITY.
    for prompt in (CREATIVE_AUTHORITY, build_episode_prompt(episode()), _REPAIR_AUTHORITY):
        assert ASSET_REFERENCE_CONTRACT in prompt
        assert "Never combine multiple catalog entities into one entity_key" in prompt


def test_historic_pass_is_not_shown_as_ready_in_review_ui():
    from novelvideo.api.routes.director_plans import _dump_revision
    from novelvideo.director_plan.models import ValidationReport
    from tests.director_plan.test_models import make_revision
    revision = make_revision(creative_contract_version=1,
        groups=(make_group(creative_contract_version=1, shots=(creative_shot(duration=25),)),),
        validation_report=ValidationReport(passed=True))
    data = _dump_revision(revision)
    assert not data["production_ready"]
    assert not data["validation_report"]["passed"]
    assert data["validation_report"]["issues"][0]["code"] == "video_shot_duration_out_of_range"


def test_duration_repair_cannot_succeed_by_truncating_runtime():
    from novelvideo.director_plan.creative_review import validate_repair
    from tests.director_plan.test_models import make_revision
    before = make_revision(creative_contract_version=1, groups=(make_group(
        creative_contract_version=1, shots=(creative_shot(duration=25),)),))
    after = before.model_copy(update={"groups": (make_group(
        creative_contract_version=1, shots=(creative_shot(duration=15),)),)})
    with pytest.raises(ValueError, match="total runtime"):
        validate_repair(before, after, ())


def test_creative_repair_schema_requires_full_shot_decisions():
    from novelvideo.director_plan.creative_review import CreativeV1RepairDraft
    schema = CreativeV1RepairDraft.model_json_schema()
    shot = schema["$defs"]["CreativeRepairShot"]
    assert {"creative", "cinematography"}.issubset(shot["required"])
    group = schema["$defs"]["CreativeRepairGroup"]
    assert "creative_context" in group["required"]


@pytest.mark.asyncio
async def test_old_active_plan_is_blocked_before_storyboard_purchase(monkeypatch, tmp_path):
    from novelvideo.api.routes import narrative_groups as api
    group = make_group(creative_contract_version=1, shots=(creative_shot(duration=25),))
    async def resolve(*args):
        return SimpleNamespace(project_dir=tmp_path), [group], []
    monkeypatch.setattr(api, "_resolve_groups", resolve)
    monkeypatch.setattr(api, "_group_beats", lambda *args: (group, []))
    monkeypatch.setattr(api, "DirectorPlanStore", lambda root: SimpleNamespace(
        load_active=lambda episode: SimpleNamespace(groups=(group,))))
    with pytest.raises(HTTPException) as caught:
        await api._enqueue_group_action("project", 1, group.id, "render", {})
    assert caught.value.status_code == 409
    assert caught.value.detail["code"] == "director_plan_not_executable"


@pytest.mark.asyncio
async def test_already_queued_invalid_storyboard_fails_without_provider_call(monkeypatch, tmp_path):
    from novelvideo.task_backend.runners import narrative_group as runner
    from novelvideo.director_plan.store import DirectorPlanStore
    group = make_group(creative_contract_version=1, shots=(creative_shot(duration=25),))
    monkeypatch.setattr(DirectorPlanStore, "load_active", lambda *args: SimpleNamespace(groups=(group,)))
    monkeypatch.setattr(runner, "stage_payload", lambda *args: {
        "layout": {}, "beat_ids": [], "cell_to_beat": []})
    recorded = []
    monkeypatch.setattr(runner, "record_stage_result", lambda *args, **kwargs: recorded.append(kwargs))
    async def never_generate(*args):
        pytest.fail("Invalid director plan reached the paid generator")
    monkeypatch.setattr(runner, "_generate_grid", never_generate)
    with pytest.raises(ValueError, match="duration"):
        await runner._execute_single({"episode": 1, "payload": {
            "group_id": group.id, "stage": "render", "revision": 1,
        }}, SimpleNamespace(output_dir=tmp_path), split_only=False)
    assert [item["status"] for item in recorded] == ["running", "failed"]
