import pytest

from novelvideo.api.routes.director_plans import _dump_revision
from novelvideo.director_plan.models import ValidationReport
from novelvideo.director_plan.revision_feedback import FeedbackHistory, QCFeedbackRound
from tests.director_plan.creative_fixtures import creative_shot
from tests.director_plan.creative_qc_fixtures import complete_assessments
from tests.director_plan.test_models import make_group, make_revision


def test_unavailable_qc_is_not_reported_as_content_rejection():
    from novelvideo.director_plan.models import ValidationIssue
    revision = make_revision(creative_contract_version=1,
        validation_report=ValidationReport(passed=True, issues=(ValidationIssue(
            code="creative_qc_unavailable", message="provider unavailable", location="creative_feedback", severity="warning"),)))
    assert _dump_revision(revision)["creative_review_status"] == "unavailable"


@pytest.mark.parametrize("rules,valid,expected", [
    ("director-creative-v1", True, "passed"),
    ("obsolete", True, "needs_revision"),
    ("director-creative-v1", False, "needs_revision"),
])
def test_api_clearance_requires_current_rules_and_valid_structure(rules, valid, expected):
    revision = make_revision(creative_contract_version=1,
        groups=(make_group(creative_contract_version=1, shots=(creative_shot(duration=5),)),))
    history = FeedbackHistory.start(revision.revision_id, rules, "author")
    history = history.apply_qc(QCFeedbackRound(round_id="qc1", revision_id=revision.revision_id,
        rules_version=rules, qc_context_id="independent", assessments=complete_assessments(revision)))
    revision = revision.model_copy(update={"creative_feedback": history,
        "validation_report": ValidationReport(passed=valid)})
    dumped = _dump_revision(revision)
    assert dumped["creative_review_status"] == expected
    if expected != "passed":
        assert not dumped["production_ready"]


def test_review_endpoint_queues_existing_candidate(monkeypatch):
    from types import SimpleNamespace
    from tests.test_api_director_plans import _client
    client, calls, ctx, module = _client(monkeypatch)
    revision = make_revision(status="review_required", creative_contract_version=1,
        groups=(make_group(creative_contract_version=1, shots=(creative_shot(duration=5),)),))
    monkeypatch.setattr(module, "_build_director_plan_store", lambda _: SimpleNamespace(
        load=lambda episode, revision_id: revision, require_current=lambda candidate: None))
    queued = []
    async def enqueue(context, **kwargs):
        queued.append(kwargs)
        return SimpleNamespace(task_state=SimpleNamespace(task_id="review-task"), backend="inline", queue="default")
    monkeypatch.setattr(module, "get_task_backend", lambda: SimpleNamespace(enqueue_project_task=enqueue))
    response = client.post(f"/api/v1/projects/project-1/episodes/2/director-plans/{revision.revision_id}/review")
    assert response.status_code == 202
    assert queued[0]["payload"]["review_revision_id"] == revision.revision_id
    assert queued[0]["scope"] == f"creative-review:{revision.revision_id}"
    assert calls == [("project-1", "editor")]
