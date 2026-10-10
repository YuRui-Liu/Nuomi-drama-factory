from novelvideo.director_plan.creative_memory import CreativeMemoryStore
from novelvideo.director_plan.revision_feedback import FeedbackHistory, FeedbackIssue, QCFeedbackRound, AuthorRevisionRound, AuthorIssueResponse, IssueDisposition


def test_memory_requires_verified_resolution_and_scope(tmp_path):
    history = FeedbackHistory.start("r1", "rules1", "author")
    history = history.apply_qc(QCFeedbackRound(round_id="q1", revision_id="r1", rules_version="rules1", qc_context_id="qc1",
        new_issues=(FeedbackIssue(issue_id="i1", discovered_round_id="q1", discovered_revision_id="r1", description="gaze", evidence_paths=("groups",), impact="confusion", acceptance_criteria="partner visible"),)))
    memory = CreativeMemoryStore(tmp_path, project_id="p", task_kind="director_plan")
    memory.record(history, source_hash="source")
    assert memory.load(source_hash="source", rules_version="rules1") == []
    history = history.submit_revision(AuthorRevisionRound(round_id="a1", base_revision_id="r1", revision_id="r2", rules_version="rules1", author_session_id="author", responses=(AuthorIssueResponse(issue_id="i1", status="proposed_change", explanation="Turned gaze to partner", affected_paths=("groups",), evidence_paths=("groups",)),)))
    history = history.apply_qc(QCFeedbackRound(round_id="q2", revision_id="r2", rules_version="rules1", qc_context_id="qc2", dispositions=(IssueDisposition(issue_id="i1", status="resolved", explanation="Visible partner gaze confirmed", evidence_paths=("groups",)),)))
    memory.record(history, source_hash="source")
    assert len(memory.load(source_hash="source", rules_version="rules1")) == 1
    assert memory.load(source_hash="changed", rules_version="rules1") == []
    assert memory.load(source_hash="source", rules_version="rules2") == []
    assert CreativeMemoryStore(tmp_path, project_id="other", task_kind="director_plan").load(source_hash="source", rules_version="rules1") == []
    assert CreativeMemoryStore(tmp_path, project_id="p", task_kind="screenplay").load(source_hash="source", rules_version="rules1") == []
    memory.invalidate()
    assert memory.load(source_hash="source", rules_version="rules1") == []
