import pytest

from novelvideo.director_plan.creative_review import CreativeRepairDraft
from novelvideo.director_plan.models import ValidationIssue, ValidationReport
from novelvideo.director_plan.planner import DirectorPlanner
from novelvideo.director_plan.revision_feedback import (
    AuthorIssueResponse, FeedbackHistory, FeedbackIssue, QCFeedbackRound,
)
from novelvideo.director_plan.service import DirectorPlanService
from tests.director_plan.test_service import episode, group


def history_for(candidate):
    return FeedbackHistory.start(candidate.revision_id, "director-creative-v1", "author").apply_qc(
        QCFeedbackRound(round_id="q", revision_id=candidate.revision_id,
            rules_version="director-creative-v1", qc_context_id="structural-independent",
            new_issues=(FeedbackIssue(issue_id="original", discovered_round_id="q",
                discovered_revision_id=candidate.revision_id, description="Split scenes",
                evidence_paths=("groups",), impact="hard_boundary_crossed",
                acceptance_criteria="Structural validation passes: groups.0.source_span_ids"),)))


def test_structural_recheck_reuses_issue_and_closes_only_validated_errors():
    from novelvideo.director_plan.creative_review import structural_feedback_round
    candidate = DirectorPlanService._make_revision(episode(), (group("g1", 1, "s1"),))
    history = history_for(candidate)
    report = ValidationReport(passed=False, issues=(ValidationIssue(
        code="hard_boundary_crossed", message="Split scenes",
        location="groups.0.source_span_ids", severity="error"),))
    for _ in range(3):
        history = history.apply_qc(structural_feedback_round(candidate, history, report))
    assert len(history.issues) == 1
    assert history.open_issues[0].issue.issue_id == "original"
    assert history.qc_rounds[-1].dispositions[0].status == "persists"
    resolved = history.apply_qc(structural_feedback_round(candidate, history, ValidationReport(passed=True)))
    assert resolved.issues[0].status == "resolved"
    regressed = resolved.apply_qc(structural_feedback_round(candidate, resolved, report))
    assert regressed.issues[0].status == "regressed"


def test_historical_duplicate_findings_remain_auditable_without_new_duplicates():
    from novelvideo.director_plan.creative_review import structural_feedback_round
    candidate = DirectorPlanService._make_revision(episode(), (group("g1", 1, "s1"),))
    history = history_for(candidate)
    original = history.issues[0].issue
    history = history.apply_qc(QCFeedbackRound(round_id="old-duplicate", revision_id=candidate.revision_id,
        rules_version=history.rules_version, qc_context_id="structural-old-qc", new_issues=(
            original.model_copy(update={"issue_id": "duplicate", "discovered_round_id": "old-duplicate"}),)))
    report = ValidationReport(passed=False, issues=(ValidationIssue(code="hard_boundary_crossed",
        message="Split scenes", location="groups.0.source_span_ids", severity="error"),))
    round_ = structural_feedback_round(candidate, history, report)
    assert not round_.new_issues
    assert {d.issue_id for d in round_.dispositions} == {"original", "duplicate"}
    assert len(history.apply_qc(round_).issues) == 2


def test_creative_qc_text_cannot_be_closed_by_structural_validator():
    from novelvideo.director_plan.creative_review import structural_feedback_round
    candidate = DirectorPlanService._make_revision(episode(), (group("g1", 1, "s1"),))
    history = history_for(candidate)
    history = history.model_copy(update={"qc_rounds": (
        history.qc_rounds[0].model_copy(update={"qc_context_id": "creative-independent"}),)})
    round_ = structural_feedback_round(candidate, history, ValidationReport(passed=True))
    assert not round_.dispositions
    assert history.apply_qc(round_).open_issues


@pytest.mark.asyncio
async def test_missing_responses_are_retried_in_same_author_session():
    candidate = DirectorPlanService._make_revision(episode(), (group("g1", 1, "s1"),))
    history = history_for(candidate)
    calls = []
    class Runtime:
        async def run_structured(self, **kwargs):
            calls.append(kwargs["prompt"])
            return CreativeRepairDraft(groups=candidate.groups, responses=() if len(calls) == 1 else (
                AuthorIssueResponse(issue_id="original", status="blocked",
                    explanation="Cannot change scene without required source details",
                    affected_paths=("groups",), evidence_paths=("groups",)),))
    planner = object.__new__(DirectorPlanner)
    planner._runtime = Runtime()
    planner._author_for = lambda _: planner._runtime
    result = await planner.repair_creative(episode(), candidate, history)
    assert len(calls) == 2
    assert "original" in calls[1]
    assert result.responses[0].issue_id == "original"
