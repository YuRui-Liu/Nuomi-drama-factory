import importlib

import pytest
from pydantic import ValidationError

from novelvideo.character_visual.models import CharacterVisualWorkspace


def contracts():
    return importlib.import_module("novelvideo.character_visual.casting_models")


def decision(**changes):
    return dict(decision_id="d1", attribute="hair", value="short", reason="silhouette", basis="creative_choice", fact_ids=[]) | changes


def snapshot(**changes):
    return dict(revision_id="r1", source_revision="s1", style_revision="t1", profile_hash="p1", character_id="c1", identity_id=None, proposal_id="p1", prompt="portrait", hard_constraints=[], design_decisions=[], style="anime", snapshot_hash="h1") | changes


def candidate(**changes):
    return dict(candidate_id="cnd1", project_id="project1", character_id="c1", identity_id=None, snapshot=snapshot(), task_id="task1") | changes


def test_old_workspace_has_nullable_casting_revision():
    workspace = CharacterVisualWorkspace.model_validate({"character_id": "c1", "profile": {"character_id": "c1", "name": "Hero"}})
    assert workspace.casting_revision is None


@pytest.mark.parametrize("changes", [dict(basis="evidence"), dict(basis="evidence", fact_ids=[" "]), dict(fact_ids=["fake"]), dict(reason=" "), dict(value=" "), dict(attribute=" "), dict(decision_id=" ")])
def test_decision_requires_valid_provenance_and_nonblank_text(changes):
    with pytest.raises(ValidationError):
        contracts().CastingDecision(**decision(**changes))


def test_revision_allows_unselected_draft_but_rejects_bad_selection_and_duplicates():
    model = contracts().CastingRevision
    data = dict(revision_id="r1", character_id="c1", identity_id=None, source_revision="s1", style_revision="t1", profile_hash="h1")
    assert model(**data).selected_proposal_id is None
    for changes in [dict(selected_proposal_id="absent"), dict(proposal_ids=["a", "a"]), dict(decisions=[decision(), decision()])]:
        with pytest.raises(ValidationError):
            model(**(data | changes))


def test_candidate_nullable_stages_roundtrip_and_ownership():
    model = contracts().CastingCandidate
    draft = model(**candidate())
    assert draft.asset_path is None and draft.report is None
    assert model.model_validate_json(draft.model_dump_json()) == draft
    for changes in [dict(character_id="wrong"), dict(identity_id="wrong"), dict(generation_status="stale")]:
        with pytest.raises(ValidationError):
            model(**candidate(**changes))


def test_completed_review_requires_report_and_failed_review_requires_error():
    model = contracts().CastingCandidate
    for changes in [dict(review_status="completed"), dict(review_status="failed")]:
        with pytest.raises(ValidationError):
            model(**candidate(**changes))
    failed = model(**candidate(review_status="failed", error="reviewer unavailable"))
    assert failed.report is None
    report = dict(reviewer="vision", model="model1", version="v1", findings=[dict(finding_id="f1", dimension="facts", verdict="unjudgeable", description="scar not visible", fact_ids=["fact1"])])
    reviewed = model(**candidate(review_status="completed", report=report))
    assert reviewed.report.findings[0].verdict == "unjudgeable"


def test_snapshot_uses_narrative_fact_contract_and_is_frozen():
    model = contracts().CastingSnapshot
    fact = dict(fact_id="f1", field="scar", value="left brow", source_span=dict(start_line=1, end_line=1), evidence="scar above left brow", confidence=1)
    value = model(**snapshot(hard_constraints=[fact]))
    assert value.hard_constraints[0].source_span.start_line == 1
    with pytest.raises(ValidationError):
        value.prompt = "changed"


def test_adoption_command_has_no_client_actor_authority():
    model = contracts().CastingAdoption
    command = model(candidate_id="cnd1", expected_revision="r1", idempotency_key="key1", acknowledged_findings=["f1"])
    assert command.override_reason is None
    with pytest.raises(ValidationError):
        model(**(command.model_dump() | {"actor": "admin"}))
