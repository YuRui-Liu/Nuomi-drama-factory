"""Contract tests for the append-only director revision feedback ledger."""

import pytest
from pydantic import ValidationError

from novelvideo.director_plan.revision_feedback import (
    AuthorIssueResponse, AuthorRevisionRound, FeedbackHistory, FeedbackIssue,
    IssueDisposition, QCFeedbackRound,
)


def issue(issue_id="axis"):
    return FeedbackIssue(
        issue_id=issue_id, discovered_round_id="q1", discovered_revision_id="r1",
        description="Shot 2 crosses the dialogue axis", evidence_paths=("shots[2].axis",),
        impact="Character screen direction reverses", acceptance_criteria="Preserve left-right order",
    )


def qc(round_id="q1", revision_id="r1", **kwargs):
    return QCFeedbackRound(round_id=round_id, revision_id=revision_id, rules_version="v1",
                           qc_context_id="qc-session", **kwargs)


def response(issue_id="axis", **kwargs):
    return AuthorIssueResponse(issue_id=issue_id, status="proposed_change",
        explanation="Moved shot 2 camera to the same side of the dialogue axis",
        affected_paths=("shots[2].camera",), evidence_paths=("diffs/r2/shots/2",), **kwargs)


def revision(round_id="a1", base_revision_id="r1", revision_id="r2", responses=None):
    return AuthorRevisionRound(round_id=round_id, base_revision_id=base_revision_id,
        revision_id=revision_id, rules_version="v1", author_session_id="author-session",
        responses=(response(),) if responses is None else responses)


def initial():
    return FeedbackHistory.start("r1", "v1", "author-session")


def opened():
    return initial().apply_qc(qc(new_issues=(issue(),)))


def test_author_cannot_close_issue_and_complete_history_survives_round_trip():
    first = opened()
    second = first.submit_revision(revision())
    assert second.open_issues[0].status == "persists"
    assert first.current_revision_id == "r1"
    third = second.apply_qc(qc("q2", "r2", dispositions=(IssueDisposition(
        issue_id="axis", status="persists", explanation="Shot 3 still crosses the axis",
        evidence_paths=("shots[3].axis",)),)))
    fourth = third.submit_revision(revision("a2", "r2", "r3"))
    assert len(fourth.author_rounds) == 2
    assert len(fourth.qc_rounds) == 2
    assert fourth.qc_rounds[1].dispositions[0].explanation == "Shot 3 still crosses the axis"
    assert FeedbackHistory.model_validate_json(fourth.model_dump_json()) == fourth
    with pytest.raises(ValidationError):
        fourth.current_revision_id = "forged"


@pytest.mark.parametrize("responses", [(), (response("unknown"),), (response(), response())])
def test_author_must_cover_open_issue_ids_exactly(responses):
    with pytest.raises(ValueError):
        opened().submit_revision(revision(responses=responses))


@pytest.mark.parametrize("change", [
    {"status": "resolved"}, {"explanation": "已优化"}, {"explanation": "   "},
    {"evidence_paths": ()}, {"affected_paths": ()}, {"evidence_paths": (" ",)},
])
def test_author_responses_require_concrete_evidence_and_cannot_resolve(change):
    values = response().model_dump()
    values.update(change)
    with pytest.raises(ValidationError):
        AuthorIssueResponse.model_validate(values)


@pytest.mark.parametrize("status", ["blocked", "disputed"])
def test_refusal_never_erases_issue(status):
    values = response().model_dump()
    values.update(status=status, explanation="Source script requires this camera position")
    history = opened().submit_revision(revision(responses=(AuthorIssueResponse(**values),)))
    assert len(history.open_issues) == 1


def test_only_current_independent_qc_can_close_and_resolved_issue_can_regress():
    history = opened().submit_revision(revision())
    verdict = IssueDisposition(issue_id="axis", status="resolved",
        explanation="All shots now preserve the same axis", evidence_paths=("shots[2].axis",))
    with pytest.raises(ValueError, match="stale"):
        history.apply_qc(qc("late", "r1", dispositions=(verdict,)))
    with pytest.raises(ValueError, match="independent"):
        history.apply_qc(QCFeedbackRound(round_id="self", revision_id="r2", rules_version="v1",
            qc_context_id="author-session", dispositions=(verdict,)))
    history = history.apply_qc(qc("q2", "r2", dispositions=(verdict,)))
    assert history.open_issues == ()
    assert history.is_clear
    history = history.submit_revision(revision("a2", "r2", "r3", responses=()))
    assert not history.is_clear  # A new revision always needs its own review.
    regression = verdict.model_dump()
    regression.update(status="regressed", explanation="Shot 2 crosses the axis again")
    history = history.apply_qc(qc("q3", "r3", dispositions=(IssueDisposition(**regression),)))
    assert history.open_issues[0].status == "regressed"
    assert not history.is_clear


def test_omitted_issues_keep_their_state_new_issues_are_appended():
    history = opened().submit_revision(revision())
    new = issue("lighting").model_dump()
    new.update(discovered_round_id="q2", discovered_revision_id="r2")
    history = history.apply_qc(qc("q2", "r2", new_issues=(FeedbackIssue(**new),)))
    assert {item.issue.issue_id for item in history.open_issues} == {"axis", "lighting"}
    assert history.issues[0].last_reviewed_revision_id == "r1"


def test_identical_retries_do_not_append_even_after_revision_advanced():
    first_qc = qc(new_issues=(issue(),))
    author_round = revision()
    history = initial().apply_qc(first_qc).submit_revision(author_round)
    assert history.apply_qc(first_qc) is history
    assert history.submit_revision(author_round) is history
    with pytest.raises(ValueError, match="conflict"):
        history.apply_qc(qc(new_issues=()))
    with pytest.raises(ValueError, match="conflict"):
        history.submit_revision(revision(revision_id="different"))


def test_invalid_rounds_do_not_advance_history():
    history = opened()
    with pytest.raises(ValueError, match="stale"):
        history.submit_revision(revision(base_revision_id="stale"))
    with pytest.raises(ValueError):
        history.submit_revision(revision(revision_id="r1"))
    with pytest.raises(ValueError, match="rules"):
        history.apply_qc(QCFeedbackRound(round_id="q2", revision_id="r1", rules_version="v2",
                                        qc_context_id="qc-session"))
    with pytest.raises(ValueError, match="unknown"):
        history.apply_qc(qc("q2", dispositions=(IssueDisposition(issue_id="missing",
            status="resolved", explanation="Verified camera changes", evidence_paths=("shots[2]",)),)))
    with pytest.raises(ValueError):
        history.apply_qc(qc("q2", new_issues=(issue(),)))


def test_unreviewed_revision_cannot_be_skipped_by_another_author_round():
    history = opened().submit_revision(revision())
    with pytest.raises(ValueError, match="QC"):
        history.submit_revision(revision("a2", "r2", "r3"))


def test_needs_human_is_open_and_evidence_is_retained():
    history = opened().submit_revision(revision())
    verdict = IssueDisposition(issue_id="axis", status="needs_human",
        explanation="Script direction conflicts with the selected camera continuity rule",
        evidence_paths=("script/scene/2", "shots[2].axis"))
    history = history.apply_qc(qc("q2", "r2", dispositions=(verdict,)))
    assert history.open_issues[0].status == "needs_human"
    assert not history.is_clear
    assert history.qc_rounds[-1].dispositions == (verdict,)


@pytest.mark.parametrize("invalid", [
    {"author_session_id": "different-session"}, {"rules_version": "different-rules"},
])
def test_author_binding_cannot_change(invalid):
    values = revision().model_dump()
    values.update(invalid)
    with pytest.raises(ValueError):
        opened().submit_revision(AuthorRevisionRound(**values))


def test_round_ids_are_unique_across_author_and_qc():
    with pytest.raises(ValueError, match="conflict"):
        opened().submit_revision(revision(round_id="q1"))


def test_duplicate_qc_dispositions_and_new_ids_are_rejected():
    verdict = IssueDisposition(issue_id="axis", status="persists",
        explanation="Shot 2 still reverses screen direction", evidence_paths=("shots[2]",))
    with pytest.raises(ValueError, match="Duplicate"):
        opened().apply_qc(qc("q2", dispositions=(verdict, verdict)))
    with pytest.raises(ValueError, match="unique"):
        initial().apply_qc(qc(new_issues=(issue(), issue())))


def test_discovery_binds_to_report_and_ids_cannot_be_recycled():
    with pytest.raises(ValueError, match="discovery"):
        initial().apply_qc(qc("q2", new_issues=(issue(),)))
    changed = issue().model_dump()
    changed.update(discovered_round_id="q2")
    with pytest.raises(ValueError, match="unique"):
        opened().apply_qc(qc("q2", new_issues=(FeedbackIssue(**changed),)))


def test_nested_models_are_immutable_and_reject_extra_verdict_fields():
    history = opened()
    with pytest.raises(ValidationError):
        history.issues[0].status = "resolved"
    with pytest.raises(ValidationError):
        history.issues[0].issue.description = "forged"
    values = response().model_dump()
    values["resolved"] = True
    with pytest.raises(ValidationError):
        AuthorIssueResponse.model_validate(values)


def test_no_qc_means_no_clearance_but_clean_initial_review_can_clear():
    assert not initial().is_clear
    assert initial().apply_qc(qc()).is_clear


def test_omitted_resolved_issue_needs_regression_review_on_new_revision():
    verdict = IssueDisposition(issue_id="axis", status="resolved",
        explanation="Verified screen direction across all shots", evidence_paths=("shots[2].axis",))
    history = opened().apply_qc(qc("q2", dispositions=(verdict,)))
    assert history.is_clear
    history = history.submit_revision(revision(responses=()))
    history = history.apply_qc(qc("q3", "r2"))
    assert history.open_issues == ()  # Omission retains the recorded conclusion.
    assert not history.is_clear  # But cannot substitute for a regression check.
    assert history.apply_qc(qc("q4", "r2", dispositions=(verdict,))).is_clear
