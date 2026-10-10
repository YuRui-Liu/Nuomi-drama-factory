import pytest
from novelvideo.director_plan.creative_review import (
    CreativeQCResult, CreativeRepairDraft, NewCreativeIssue, bind_qc, repair_prompt)
from novelvideo.director_plan.models import ValidationReport
from novelvideo.director_plan.revision_feedback import AuthorIssueResponse, IssueDisposition
from novelvideo.director_plan.service import DirectorPlanService, DirectorPlanPlanningError
from novelvideo.director_plan.store import DirectorPlanStore
from novelvideo.director_plan.planner import DirectorPlanDraft
from tests.director_plan.test_service import episode, group
from tests.director_plan.creative_qc_fixtures import complete_assessments


@pytest.mark.asyncio
async def test_qc_cannot_silently_skip_existing_findings(tmp_path, monkeypatch):
    monkeypatch.setattr("novelvideo.director_plan.service.validate_director_plan", lambda *args: ValidationReport(passed=True))
    value = episode().model_copy(update={"creative_contract_version": 1, "author_chain_id": "chain"})
    candidate = await DirectorPlanService(DirectorPlanStore(tmp_path), Planner()).create_draft(value)
    with pytest.raises(ValueError, match="missing=.*eye"):
        bind_qc(CreativeQCResult(assessments=complete_assessments(candidate)), candidate, candidate.creative_feedback)


@pytest.mark.asyncio
async def test_qc_retries_incomplete_report_with_exact_missing_ids(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from novelvideo.director_plan.planner import DirectorPlanner
    monkeypatch.setattr("novelvideo.director_plan.service.validate_director_plan", lambda *args: ValidationReport(passed=True))
    value = episode().model_copy(update={"creative_contract_version": 1, "author_chain_id": "chain"})
    candidate = await DirectorPlanService(DirectorPlanStore(tmp_path), Planner()).create_draft(value)
    calls = []
    class Runtime:
        async def run_structured(self, **kwargs):
            calls.append(kwargs["prompt"])
            return CreativeQCResult(assessments=complete_assessments(candidate), dispositions=() if len(calls) == 1 else (
                IssueDisposition(issue_id="eye", status="resolved", explanation="Verified current gaze", evidence_paths=("groups.0.objective",)),))
    planner = DirectorPlanner(agent=SimpleNamespace(model_name="test"))
    planner._runtime = Runtime()
    result = await planner.review_creative(value, candidate, candidate.creative_feedback)
    assert len(calls) == 2
    assert "missing=['eye']" in calls[1]
    assert result.dispositions[0].issue_id == "eye"


@pytest.mark.asyncio
async def test_repair_prompt_prioritizes_latest_verdict_and_fixed_acceptance(tmp_path, monkeypatch):
    import json
    monkeypatch.setattr("novelvideo.director_plan.service.validate_director_plan", lambda *args: ValidationReport(passed=True))
    value = episode().model_copy(update={"creative_contract_version": 1, "author_chain_id": "chain", "creative_repair_budget": 1})
    candidate = await DirectorPlanService(DirectorPlanStore(tmp_path), Planner()).create_draft(value)
    from novelvideo.director_plan.creative_review import _payload
    payload = json.loads(_payload(value, candidate, candidate.creative_feedback))
    issue = payload["issue_worklist"][0]
    assert issue["acceptance_criteria"] == "Gaze reaches partner"
    assert issue["latest_qc"]["explanation"] == "Inspected actual changed direction"
    assert issue["latest_author_response"]["issue_id"] == "eye"
    assert "creative_feedback" not in payload["candidate"]
    assert "prior_creative_feedback" not in payload["source"]


class Planner:
    def __init__(self):
        self.plans = 0
        self.qcs = []
        self.repairs = []

    async def plan_episode(self, value):
        from tests.director_plan.creative_fixtures import creative_shot
        from tests.director_plan.test_models import make_group
        self.plans += 1
        return DirectorPlanDraft(groups=(make_group(creative_contract_version=1,
            shots=(creative_shot(),)),))

    async def review_creative(self, value, candidate, history):
        self.qcs.append(history)
        if not history.issues:
            return CreativeQCResult(assessments=complete_assessments(candidate), new_issues=(NewCreativeIssue(issue_id="eye",
                description="Gaze misses partner", evidence_paths=("groups.0.objective",),
                impact="Unreadable exchange", acceptance_criteria="Gaze reaches partner"),))
        return CreativeQCResult(assessments=complete_assessments(candidate), dispositions=(IssueDisposition(issue_id="eye",
            status="resolved" if len(history.author_rounds) == 2 else "persists",
            explanation="Inspected actual changed direction", evidence_paths=("groups.0.objective",)),))

    async def repair_creative(self, value, candidate, history):
        self.repairs.append(repair_prompt(value, candidate, history))
        return CreativeRepairDraft(groups=(candidate.groups[0].model_copy(update={
            "objective": "look at partner " + str(len(history.author_rounds))}),),
            responses=(AuthorIssueResponse(issue_id="eye", status="proposed_change",
                explanation="Redirected actor gaze toward partner, preserving lighting",
                affected_paths=("groups.0.objective",), evidence_paths=("groups.0.objective",)),))


@pytest.mark.asyncio
async def test_repeated_regression_label_keeps_issue_open_without_aborting_qc(tmp_path, monkeypatch):
    monkeypatch.setattr("novelvideo.director_plan.service.validate_director_plan", lambda *args: ValidationReport(passed=True))
    class RepeatedRegression(Planner):
        async def review_creative(self, value, candidate, history):
            result = await super().review_creative(value, candidate, history)
            return result.model_copy(update={"dispositions": tuple(
                item.model_copy(update={"status": "regressed"}) for item in result.dispositions)})
    value = episode().model_copy(update={"creative_contract_version": 1, "author_chain_id": "chain"})
    result = await DirectorPlanService(DirectorPlanStore(tmp_path), RepeatedRegression()).create_draft(value)
    assert not result.creative_feedback.is_clear
    assert len(result.creative_feedback.qc_rounds) == 3
    assert result.creative_feedback.open_issues[0].status == "persists"
    assert not any(issue.code == "creative_qc_unavailable" for issue in result.validation_report.issues)


@pytest.mark.asyncio
async def test_two_rounds_complete_history_and_resume(tmp_path, monkeypatch):
    monkeypatch.setattr("novelvideo.director_plan.service.validate_director_plan", lambda *args: ValidationReport(passed=True))
    store, planner = DirectorPlanStore(tmp_path), Planner()
    value = episode().model_copy(update={"creative_contract_version": 1, "author_chain_id": "chain"})
    result = await DirectorPlanService(store, planner).create_draft(value)
    assert result.creative_feedback.is_clear
    assert len(result.creative_feedback.author_rounds) == 2
    assert "Gaze misses partner" in planner.repairs[1]
    assert "Redirected actor gaze" in planner.repairs[1]
    assert "persists" in planner.repairs[1]
    assert all(q.qc_context_id != "chain" for q in result.creative_feedback.qc_rounds)
    assert await DirectorPlanService(store, planner).create_draft(value) == result
    assert planner.plans == 1
    store.activate(1, result.revision_id)
    with pytest.raises(DirectorPlanPlanningError, match="creative_input_changed"):
        await DirectorPlanService(store, planner).create_draft(value.model_copy(update={"source_script_hash": "changed"}))


@pytest.mark.asyncio
async def test_qc_failure_never_clears_or_activates(tmp_path, monkeypatch):
    monkeypatch.setattr("novelvideo.director_plan.service.validate_director_plan", lambda *args: ValidationReport(passed=True))
    planner = Planner()
    async def broken(*args):
        raise TimeoutError("review timeout")
    planner.review_creative = broken
    store = DirectorPlanStore(tmp_path)
    result = await DirectorPlanService(store, planner).create_draft(episode().model_copy(
        update={"creative_contract_version": 1, "author_chain_id": "failed"}))
    assert result.status == "review_required"
    assert not result.creative_feedback.is_clear
    with pytest.raises(ValueError, match="creative QC"):
        store.activate(1, result.revision_id)


def test_payload_cannot_forge_authority_or_invalid_evidence():
    with pytest.raises(ValueError):
        CreativeQCResult.model_validate({"qc_context_id": "forged"})
    from novelvideo.director_plan.revision_feedback import FeedbackHistory
    candidate = DirectorPlanService._make_revision(episode(), (group("g1", 1, "s1"),))
    history = FeedbackHistory.start(candidate.revision_id, "director-creative-v1", "author")
    with pytest.raises(ValueError, match="evidence path"):
        bind_qc(CreativeQCResult(assessments=complete_assessments(candidate), new_issues=(NewCreativeIssue(issue_id="bad",
            description="x", evidence_paths=("/etc/passwd",), impact="x", acceptance_criteria="x"),)), candidate, history)


@pytest.mark.asyncio
async def test_restart_after_repair_does_not_replan(tmp_path, monkeypatch):
    monkeypatch.setattr("novelvideo.director_plan.service.validate_director_plan", lambda *args: ValidationReport(passed=True))
    import asyncio
    planner = Planner()
    original = planner.review_creative
    async def interrupt(value, candidate, history):
        if history.author_rounds:
            raise asyncio.CancelledError()
        return await original(value, candidate, history)
    planner.review_creative = interrupt
    store = DirectorPlanStore(tmp_path)
    value = episode().model_copy(update={"creative_contract_version": 1, "author_chain_id": "restart"})
    with pytest.raises(asyncio.CancelledError):
        await DirectorPlanService(store, planner).create_draft(value)
    replacement = Planner()
    result = await DirectorPlanService(store, replacement).create_draft(value)
    assert replacement.plans == 0
    assert result.creative_feedback.is_clear
    assert len(result.creative_feedback.author_rounds) == 2


@pytest.mark.asyncio
async def test_budget_zero_stays_unresolved(tmp_path, monkeypatch):
    monkeypatch.setattr("novelvideo.director_plan.service.validate_director_plan", lambda *args: ValidationReport(passed=True))
    store, planner = DirectorPlanStore(tmp_path), Planner()
    result = await DirectorPlanService(store, planner).create_draft(episode().model_copy(update={
        "creative_contract_version": 1, "author_chain_id": "bounded", "creative_repair_budget": 0}))
    assert not result.creative_feedback.is_clear
    assert not planner.repairs
    with pytest.raises(ValueError, match="creative QC"):
        store.activate(1, result.revision_id)


@pytest.mark.asyncio
async def test_planner_routes_qc_to_independent_runtime():
    from tests.director_plan.creative_fixtures import creative_shot
    from tests.director_plan.test_models import make_group
    from novelvideo.director_plan.planner import DirectorPlanner
    from novelvideo.director_plan.revision_feedback import FeedbackHistory
    calls = []
    class Runtime:
        def __init__(self, name):
            self.name = name
        async def run_structured(self, **kwargs):
            calls.append((self.name, kwargs["prompt"]))
            if self.name == "qc-original":
                return CreativeQCResult(assessments=complete_assessments(candidate))
            return CreativeRepairDraft(groups=(group("g1", 1, "s1"),), responses=())
    planner = object.__new__(DirectorPlanner)
    planner._runtime = Runtime("qc-original")
    author = Runtime("author-native-session")
    planner._author_for = lambda value: author
    candidate = DirectorPlanService._make_revision(episode(), (make_group(
        creative_contract_version=1, shots=(creative_shot(),)),))
    history = FeedbackHistory.start(candidate.revision_id, "director-creative-v1", "native-id")
    await planner.review_creative(episode(), candidate, history)
    await planner.repair_creative(episode(), candidate, history)
    assert [name for name, _ in calls] == ["qc-original", "author-native-session"]
    assert all("complete_feedback_history" in prompt for _, prompt in calls)


@pytest.mark.asyncio
async def test_missing_author_response_cannot_close_issue(tmp_path, monkeypatch):
    monkeypatch.setattr("novelvideo.director_plan.service.validate_director_plan", lambda *args: ValidationReport(passed=True))
    planner = Planner()
    async def incomplete(value, candidate, history):
        return CreativeRepairDraft(groups=candidate.groups, responses=())
    planner.repair_creative = incomplete
    result = await DirectorPlanService(DirectorPlanStore(tmp_path), planner).create_draft(episode().model_copy(update={
        "creative_contract_version": 1, "author_chain_id": "missing"}))
    assert not result.creative_feedback.is_clear
    assert not result.creative_feedback.author_rounds
    assert result.validation_report.issues[-1].code == "creative_repair_invalid"


def real_input(chain="real"):
    from novelvideo.director_plan.models import SourceSpan
    return episode().model_copy(update={"creative_contract_version": 1,
        "author_chain_id": chain, "source_spans": (SourceSpan(id="span-1", ordinal=1,
            scene="hallway", time="night", text="Door remains closed."),)})


class RealPlanner:
    def __init__(self):
        self.plans = 0
        self.reviews = []
    async def plan_episode(self, value):
        from tests.director_plan.creative_fixtures import creative_shot
        from tests.director_plan.test_models import make_group
        self.plans += 1
        return DirectorPlanDraft(groups=(make_group(creative_contract_version=1,
            shots=(creative_shot(),)),))
    async def review_creative(self, value, candidate, history):
        self.reviews.append((value, candidate, history))
        return CreativeQCResult(assessments=complete_assessments(candidate), dispositions=tuple(IssueDisposition(issue_id=state.issue.issue_id,
            status="resolved", explanation="Verified gaze on the current edited candidate",
            evidence_paths=("groups.0.shots.0.action",)) for state in history.issues))


@pytest.mark.asyncio
async def test_complete_retry_returns_current_activated_revision(tmp_path):
    store, planner = DirectorPlanStore(tmp_path), RealPlanner()
    service = DirectorPlanService(store, planner)
    result = await service.create_draft(real_input())
    active = store.activate(1, result.revision_id)
    resumed = await service.create_draft(real_input())
    assert resumed == active
    assert resumed.status == "active"
    assert planner.plans == 1


@pytest.mark.asyncio
async def test_review_existing_candidate_preserves_content_author_and_findings(tmp_path):
    from novelvideo.director_plan.editing import apply_edit
    from novelvideo.director_plan.models import UpdateShot
    from novelvideo.director_plan.revision_feedback import FeedbackHistory, FeedbackIssue, QCFeedbackRound
    store, planner = DirectorPlanStore(tmp_path), RealPlanner()
    service = DirectorPlanService(store, planner)
    original = await service.create_draft(real_input())
    edited = apply_edit(original, UpdateShot(shot_id="shot-1", action="Door holds still after the pause."), real_input().source_spans)
    prior = FeedbackHistory.start(original.revision_id, "director-creative-v1", "native-author")
    prior = prior.apply_qc(QCFeedbackRound(round_id="prior-q", revision_id=original.revision_id,
        rules_version="director-creative-v1", qc_context_id="qc-before", new_issues=(FeedbackIssue(
            issue_id="retained-issue", discovered_round_id="prior-q", discovered_revision_id=original.revision_id,
            description="Door action lost pause", evidence_paths=("groups.0.shots.0.action",),
            impact="Timing unclear", acceptance_criteria="Visible pause remains"),)))
    edited = edited.model_copy(update={"creative_feedback": prior})
    store.save(edited)
    reviewed = await service.review_revision(real_input("review-task"), edited)
    assert reviewed.groups == edited.groups
    assert reviewed.parent_revision_id == edited.revision_id
    assert reviewed.author_chain_id == original.author_chain_id
    assert planner.plans == 1
    assert reviewed.creative_feedback.is_clear
    assert reviewed.creative_feedback.issues[0].issue.issue_id == "retained-issue"
    assert planner.reviews[-1][0].prior_creative_feedback == prior
    assert planner.reviews[-1][0].author_chain_id == original.author_chain_id
    assert await service.review_revision(real_input("review-task"), edited) == reviewed
    assert store.activate(1, reviewed.revision_id).status == "active"


@pytest.mark.asyncio
async def test_v1_keeps_asset_migration_candidates(tmp_path):
    from novelvideo.director_plan.migration import LegacyShotAsset
    store, planner = DirectorPlanStore(tmp_path), RealPlanner()
    service = DirectorPlanService(store, planner)
    old = await service.create_draft(real_input("old"))
    shot = old.groups[0].shots[0]
    asset = LegacyShotAsset(asset_id="asset-existing", asset_path="assets/existing.png",
        old_shot_id=shot.id, source_span_ids=shot.source_span_ids, subject=shot.subject,
        scene=old.groups[0].scene_anchor, action=shot.action, shot_size=shot.shot_size,
        camera_angle=shot.camera_angle, style_hash=old.project_style_snapshot_id)
    result = await service.create_draft(real_input("replacement"), old_plan=old, assets=(asset,))
    assert len(result.migration_report.items) == 1
    assert result.migration_report.items[0]["old_asset_id"] == "asset-existing"


@pytest.mark.asyncio
async def test_rereview_repair_reuses_original_author_identity(tmp_path):
    from contextlib import nullcontext
    store = DirectorPlanStore(tmp_path)
    original = await DirectorPlanService(store, RealPlanner()).create_draft(real_input("original-chain"))
    class RepairingPlanner(RealPlanner):
        author_session_id = "original-native-session"
        def author_chain_guard(self, value):
            assert value.author_chain_id == "original-chain"
            return nullcontext()
        async def review_creative(self, value, candidate, history):
            assert value.author_chain_id == "original-chain"
            if not history.issues:
                return CreativeQCResult(assessments=complete_assessments(candidate), new_issues=(NewCreativeIssue(
                    issue_id="pause", description="Need explicit pause", evidence_paths=("groups.0.shots.0.action",),
                    impact="Unclear timing", acceptance_criteria="Pause is explicit"),))
            return await super().review_creative(value, candidate, history)
        async def repair_creative(self, value, candidate, history):
            assert value.author_chain_id == "original-chain"
            assert history.author_session_id == self.author_session_id
            shot = candidate.groups[0].shots[0].model_copy(update={"action": "Door remains shut through a pause."})
            return CreativeRepairDraft(groups=(candidate.groups[0].model_copy(update={"shots": (shot,)}),),
                responses=(AuthorIssueResponse(issue_id="pause", status="proposed_change",
                    explanation="Added an explicit held pause before the cut",
                    affected_paths=("groups.0.shots.0.action",), evidence_paths=("groups.0.shots.0.action",)),))
    planner = RepairingPlanner()
    reviewed = await DirectorPlanService(store, planner).review_revision(real_input("review-task-id"), original)
    assert planner.plans == 0
    assert reviewed.creative_feedback.author_rounds[-1].author_session_id == "original-native-session"
    assert reviewed.author_chain_id == "original-chain"
    assert reviewed.creative_feedback.is_clear
