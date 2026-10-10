import pytest
from pydantic import ValidationError

from novelvideo.director_plan.creative_review import CreativeQCResult, bind_qc, creative_clearance
from novelvideo.director_plan.revision_feedback import FeedbackHistory, QCFeedbackRound
from tests.director_plan.test_creative_invalidation import revision
from tests.director_plan.creative_qc_fixtures import complete_assessments
from tests.director_plan.creative_fixtures import creative_shot
from tests.director_plan.test_models import make_group, make_revision


def test_empty_qc_cannot_clear_candidate():
    with pytest.raises(ValidationError):
        CreativeQCResult.model_validate({})


def test_review_evidence_map_uses_real_candidate_paths():
    from novelvideo.director_plan.creative_review import evidence_map, at_path
    candidate = revision()
    paths = evidence_map(candidate)
    assert "groups.0.shots.0.cinematography.subjects" in paths
    assert "groups.0.shots.0.creative.lighting" in paths
    for path in paths:
        at_path(candidate, path)


def test_evidence_path_repairs_only_unambiguous_container_typos():
    from novelvideo.director_plan.creative_review import canonical_evidence_path
    candidate = revision()
    assert canonical_evidence_path(candidate, "groups.0.shots.0.shots.0.action") == "groups.0.shots.0.action"
    assert canonical_evidence_path(candidate, "groups.0.creative.cinematography.subjects") == "groups.0.shots.0.cinematography.subjects"
    with pytest.raises(ValueError):
        canonical_evidence_path(candidate, "groups.0.shots.9.action")


def test_full_assessment_is_persisted_and_can_clear():
    candidate = revision()
    history = FeedbackHistory.start(candidate.revision_id, "director-creative-v1", "author")
    result = CreativeQCResult(assessments=complete_assessments(candidate))
    reviewed = history.apply_qc(bind_qc(result, candidate, history))
    assert len(reviewed.qc_rounds[-1].assessments) == 8
    assert creative_clearance(candidate.model_copy(update={"creative_feedback": reviewed}))


def test_legacy_empty_round_never_confers_creative_clearance():
    candidate = revision()
    history = FeedbackHistory.start(candidate.revision_id, "director-creative-v1", "author")
    history = history.apply_qc(QCFeedbackRound(round_id="old", revision_id=candidate.revision_id,
        rules_version="director-creative-v1", qc_context_id="qc"))
    assert history.is_clear
    assert not creative_clearance(candidate.model_copy(update={"creative_feedback": history}))


def test_missing_dimension_or_unrelated_evidence_is_rejected():
    candidate = revision()
    history = FeedbackHistory.start(candidate.revision_id, "director-creative-v1", "author")
    complete = complete_assessments(candidate)
    with pytest.raises(ValueError, match="coverage"):
        bind_qc(CreativeQCResult(assessments=complete[:-1]), candidate, history)
    wrong = complete[0].model_copy(update={"evidence_paths": ("groups",)})
    with pytest.raises(ValueError, match="dimension evidence"):
        bind_qc(CreativeQCResult(assessments=(wrong, *complete[1:])), candidate, history)


def test_nonpassed_assessment_requires_unresolved_issue_link():
    candidate = revision()
    history = FeedbackHistory.start(candidate.revision_id, "director-creative-v1", "author")
    complete = complete_assessments(candidate)
    failed = complete[0].model_copy(update={"conclusion": "needs_human"})
    with pytest.raises(ValueError, match="unresolved issue"):
        bind_qc(CreativeQCResult(assessments=(failed, *complete[1:])), candidate, history)


def test_cross_group_handoff_requires_actual_next_snapshot():
    first = make_group(creative_contract_version=1, shots=(creative_shot("a", "b", duration=5),))
    second = make_group(id="g2", ordinal=2, creative_contract_version=1, shots=(creative_shot("b", duration=5),))
    candidate = make_revision(creative_contract_version=1, groups=(first, second))
    history = FeedbackHistory.start(candidate.revision_id, "director-creative-v1", "author")
    complete = list(complete_assessments(candidate))
    assert len(bind_qc(CreativeQCResult(assessments=complete), candidate, history).assessments) == 16
    complete[7] = complete[7].model_copy(update={"evidence_paths": ("groups.0.shots.0.creative.handoff", "groups.1.shots.0.id")})
    with pytest.raises(ValueError, match="next shot evidence"):
        bind_qc(CreativeQCResult(assessments=complete), candidate, history)
