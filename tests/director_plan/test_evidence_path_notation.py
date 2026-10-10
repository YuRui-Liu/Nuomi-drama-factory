import pytest

from novelvideo.director_plan.creative_review import (
    at_path, canonical_evidence_path, repair_prompt, validate_repair,
)
from novelvideo.director_plan.revision_feedback import AuthorIssueResponse, FeedbackHistory
from novelvideo.director_plan.service import DirectorPlanService
from tests.director_plan.test_service import episode, group


@pytest.fixture
def candidate():
    return DirectorPlanService._make_revision(episode(), (group("g1", 1, "s1"),))


@pytest.mark.parametrize("path", ["groups[0].shots[0].action", "$.groups[0].shots[0].action"])
def test_bracket_indices_resolve_to_the_same_evidence(candidate, path):
    assert at_path(candidate, path) == at_path(candidate, "groups.0.shots.0.action")
    assert canonical_evidence_path(candidate, path) == "groups.0.shots.0.action"


def test_repair_accepts_equivalent_paths_and_still_requires_actual_change(candidate):
    after = candidate.model_copy(update={"groups": (
        candidate.groups[0].model_copy(update={"objective": "新的明确目标"}),
    )})
    response = AuthorIssueResponse(issue_id="repair", status="proposed_change",
        explanation="修正目标", affected_paths=("groups[0].objective",),
        evidence_paths=("$.groups[0].objective",))
    validate_repair(candidate, after, (response,))
    with pytest.raises(ValueError, match="No actual change"):
        validate_repair(candidate, candidate, (response,))


@pytest.mark.parametrize("path", ["/etc/passwd", "groups[*].shots", "groups[-1].objective", "groups[50].objective", "groups[0].missing", "groups[0].shots[0].action()"])
def test_nonexistent_or_nonliteral_evidence_is_rejected(candidate, path):
    with pytest.raises(ValueError, match="evidence path"):
        at_path(candidate, path)


def test_repair_prompt_supplies_actual_evidence_paths(candidate):
    history = FeedbackHistory.start(candidate.revision_id, "director-creative-v1", "author")
    assert "groups.0.shots.0.action" in repair_prompt(episode(), candidate, history)


def test_new_group_is_a_real_change_even_when_no_old_index_exists(candidate):
    after = candidate.model_copy(update={"groups": (*candidate.groups, group("g2", 2, "s1"))})
    response = AuthorIssueResponse(issue_id="split", status="proposed_change",
        explanation="拆出独立场次组", affected_paths=("groups.1.source_span_ids",),
        evidence_paths=("groups.1.source_span_ids",))
    validate_repair(candidate, after, (response,))


@pytest.mark.asyncio
async def test_author_retries_invalid_evidence_with_rejected_candidate_paths(candidate):
    from novelvideo.director_plan.planner import DirectorPlanner
    from novelvideo.director_plan.creative_review import CreativeRepairDraft

    calls = []
    new_group = candidate.groups[0].model_copy(update={"objective": "修复后的目标"})

    class Runtime:
        async def run_structured(self, **kwargs):
            calls.append(kwargs["prompt"])
            path = "groups.0.shots.9.action" if len(calls) == 1 else "groups.0.objective"
            return CreativeRepairDraft(groups=(new_group,), responses=(AuthorIssueResponse(
                issue_id="fix", status="proposed_change", explanation="修正目标内容",
                affected_paths=(path,), evidence_paths=(path,),
            ),))

    planner = object.__new__(DirectorPlanner)
    planner._runtime = Runtime()
    planner._author_for = lambda _: planner._runtime
    history = FeedbackHistory.start(candidate.revision_id, "director-creative-v1", "author")
    from novelvideo.director_plan.revision_feedback import FeedbackIssue, QCFeedbackRound
    history = history.apply_qc(QCFeedbackRound(round_id="q", revision_id=candidate.revision_id,
        rules_version=history.rules_version, qc_context_id="qc",
        new_issues=(FeedbackIssue(issue_id="fix", discovered_round_id="q",
            discovered_revision_id=candidate.revision_id, description="Fix objective",
            impact="unclear objective", acceptance_criteria="Objective explains the turn",
            evidence_paths=("groups.0.objective",)),)))
    result = await planner.repair_creative(episode(), candidate, history)
    assert len(calls) == 2
    assert "groups.0.shots.9.action" in calls[1]
    assert "修复后的目标" in calls[1]
    assert "VALID RETURNED CANDIDATE PATHS" in calls[1]
    validate_repair(candidate, candidate.model_copy(update={"groups": result.groups}), result.responses)
