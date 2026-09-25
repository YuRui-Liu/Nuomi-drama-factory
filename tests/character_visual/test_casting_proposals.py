import json

import pytest

from novelvideo.character_visual.models import CharacterDesignProposal
from tests.character_visual.test_casting_brief import profile


def proposal(pid="p", **changes):
    return CharacterDesignProposal(proposal_id=pid, title="漂亮的甲", rationale="保留原文漂亮",
        face_shape="长脸下颌窄", facial_features=["细长眼", "薄唇"], hair_style="短发",
        distinctive_features=["左眉稍高"], asymmetry_detail="左眉稍高",
        identity_anchors=["长脸", "细长眼", "短发"], recommended=pid == "p", **changes)


def test_beauty_fact_is_retained_but_empty_beauty_is_not_design():
    from novelvideo.character_visual.proposals import assess_design_proposal
    p = profile(("甲很漂亮", dict(field="beauty", value="漂亮")))
    assert not assess_design_proposal(proposal(), profile=p).quality_issues
    assert assess_design_proposal(CharacterDesignProposal(proposal_id="x", title="漂亮精致"), profile=p).quality_issues


def test_clothes_only_variants_rejected_and_constrained_single_needs_review():
    from novelvideo.character_visual.casting_proposals import validate_casting_proposals
    p = profile(("甲很漂亮", dict(field="beauty", value="漂亮")))
    choices = [proposal(pid, outfit_states={"default": color}) for pid, color in [("p", "红"), ("q", "绿"), ("r", "蓝")]]
    assert any("structure_collision" in x for x in validate_casting_proposals(p, choices, None))
    assert "proposal_count:explanation_required" in validate_casting_proposals(p, [proposal()], None)
    assert "proposal_count:explanation_required" not in validate_casting_proposals(p, [proposal()], None, limitation_reason="原文明示骨相，不能为了三套改变固定特征")
    assert any("requires_review" in issue for issue in validate_casting_proposals(p, [proposal()], None, limitation_reason="硬约束限制差异"))


@pytest.mark.asyncio
async def test_actual_design_request_contains_dossier_and_style_changes_checkpoint():
    from novelvideo.character_design_stage import design_merged_characters
    from novelvideo.structured_extraction import MergedCharacter
    class Agent:
        def __init__(self): self.prompts = []
        async def run(self, prompt):
            self.prompts.append(prompt)
            return {"design_proposals": []}
    calls, agent = [], Agent()
    async def load(key):
        calls.append(key)
        return ""
    item = MergedCharacter(name="甲", occupation="医生", evidence=[dict(field="beauty", value="漂亮", evidence_text="甲很漂亮", source_start=0, source_end=4)])
    for style in ["水墨", "写实"]:
        await design_merged_characters([item], agent=agent, source_revision="source-r", project_style=style, load_checkpoint=load)
    assert calls[0] != calls[1]
    payload = json.loads(agent.prompts[0])
    assert payload["casting_dossier"]["hard_constraints"][0]["evidence"] == "甲很漂亮"
    assert payload["source_revision"] == "source-r"
    assert payload["project_style"] == "水墨"
    assert payload["design_prompt_version"]


def test_negated_scars_are_not_invented():
    from novelvideo.character_visual.casting_brief import validate_casting_decisions
    from novelvideo.character_visual.casting_models import CastingDecision
    d = CastingDecision(decision_id="d", attribute="distinctive_feature", value="无伤疤", reason="不虚构", basis="creative_choice")
    assert not validate_casting_decisions(profile(), [d], None)


def test_nonhuman_does_not_require_human_face_or_invented_asymmetry():
    from novelvideo.character_visual.casting_proposals import validate_casting_proposals
    p = profile(("甲是一只猫", dict(field="species", value="猫")))
    proposals = []
    for index, features in enumerate([("杏核瞳", "矮壮躯干", "三角耳"), ("椭圆瞳", "修长躯干", "圆耳尖"), ("细长瞳", "中等躯干", "窄耳根")]):
        proposals.append(CharacterDesignProposal(proposal_id=str(index), title="猫的演绎", rationale="保留猫的四足结构",
            facial_features=[features[0]], body_type="猫的四足结构，" + features[1], distinctive_features=[features[2]],
            identity_anchors=list(features), recommended=index == 0,
            casting_decisions=[dict(decision_id=str(index), attribute="species", value="猫", reason="原文明示", basis="evidence", fact_ids=["f0"]),
                dict(decision_id=f"creative-{index}", attribute="body_type", value=features[1], reason="原文未限定体态，提供不同剪影", basis="creative_choice")]))
    assert not validate_casting_proposals(p, proposals, None)


def test_action_evidence_decision_is_removed_before_visual_validation():
    from novelvideo.character_visual.casting_proposals import strip_nonvisual_evidence_decisions
    from novelvideo.character_visual.casting_models import CastingDecision
    from novelvideo.character_visual.models import CharacterNarrativeFact, SourceSpan
    p = profile()
    p.facts.append(CharacterNarrativeFact(fact_id="action", field="behavior", value="修补窗纸",
        evidence="甲修补窗纸", source_span=SourceSpan(start_line=1, end_line=1), confidence=1))
    proposal_item = proposal().model_copy(update={"casting_decisions": [
        CastingDecision(decision_id="action", attribute="behavior", value="修补窗纸",
                        reason="剧情动作", basis="evidence", fact_ids=["action"]),
    ]})
    cleaned = strip_nonvisual_evidence_decisions(p, [proposal_item])[0]
    assert cleaned.casting_decisions == []


@pytest.mark.asyncio
async def test_extraction_builder_persists_source_and_style_without_reextract_on_style_change(tmp_path, monkeypatch):
    from pathlib import Path
    from novelvideo import structured_extraction as extraction
    from novelvideo import character_design_stage as stage
    from novelvideo.sqlite_store import SQLiteStore
    from novelvideo.structured_builders import build_characters_structured
    from novelvideo.character_visual import CharacterVisualWorkspaceStore
    from tests.test_character_build_stages import proposals

    class Facts:
        calls = 0
        async def run(self, prompt):
            self.calls += 1
            return {"characters": [{"name": "石九", "evidence": [{"quote": "石九很漂亮", "field": "face", "value": "漂亮"}]}]}
    class Designs:
        def __init__(self): self.calls = []
        async def run(self, prompt):
            payload = json.loads(prompt)
            self.calls.append(payload)
            fact = payload["casting_dossier"]["hard_constraints"][0]
            choices = proposals()
            for p in choices:
                p["rationale"] = "漂亮的不同具体五官演绎"
                p["facial_features"].append("漂亮的五官")
                p["casting_decisions"] = [dict(decision_id=p["proposal_id"] + "-face", attribute="face", value="漂亮", reason="原文明示", basis="evidence", fact_ids=[fact["fact_id"]]),
                    dict(decision_id=p["proposal_id"] + "-creative", attribute="face_shape", value=p["face_shape"], reason="原文未限定骨相，提供不同具体演绎", basis="creative_choice")]
            return {"design_proposals": choices}
    facts, designs = Facts(), Designs()
    monkeypatch.setattr(extraction, "_create_agent", lambda *a, **kw: facts)
    monkeypatch.setattr(stage, "_create_agent", lambda *a, **kw: designs)
    store = SQLiteStore("user/test", output_dir=str(tmp_path / "out"), state_dir=str(tmp_path / "state"))
    await store.initialize()
    await store.load_graph_state()
    Path(store.project_dir).mkdir(parents=True, exist_ok=True)
    (Path(store.project_dir) / "novel.txt").write_text("石九很漂亮", encoding="utf-8")
    try:
        for style in ["ink", "realistic"]:
            (Path(store.state_dir) / "project_config.json").write_text(json.dumps({"visual_style": style}))
            result = await build_characters_structured(store)
            assert result.stats["proposal_failed"] == []
            saved = CharacterVisualWorkspaceStore(store.project_dir).get("石九")
            assert saved.profile.facts[0].source_document == "novel.txt"
            assert saved.profile.facts[0].source_start == 0
            assert saved.casting_revision.source_revision == designs.calls[-1]["source_revision"]
            assert style in saved.casting_revision.style_revision
            assert saved.design_proposals[0].casting_decisions[0].fact_ids == [saved.profile.facts[0].fact_id]
        assert facts.calls == 1
        assert len(designs.calls) == 2
    finally:
        await store.close()


def test_undeclared_creative_choices_and_unreferenced_scars_are_rejected():
    from novelvideo.character_visual.casting_proposals import validate_casting_proposals
    p = profile(("甲很漂亮", dict(field="beauty", value="漂亮")))
    choices = [proposal()]
    choices[0].casting_decisions = []
    assert any("creative_choices:required" in x for x in validate_casting_proposals(p, choices, None))
    choices[0].distinctive_features = ["左眼有伤疤"]
    assert any("invented_history" in x for x in validate_casting_proposals(p, choices, None))


def test_age_in_rationale_does_not_preserve_age_in_rendered_details():
    from novelvideo.character_visual.casting_proposals import validate_casting_proposals
    from tests.test_character_build_stages import proposals
    p = profile(("甲七十岁", dict(field="age_range", value="七十岁")))
    choices = []
    for payload in proposals():
        payload["rationale"] = "保留七十岁原文设定"
        payload["casting_decisions"] = [dict(decision_id="age", attribute="age_range", value="七十岁", reason="原文", basis="evidence", fact_ids=["f0"]),
            dict(decision_id="face", attribute="face_shape", value=payload["face_shape"], reason="自由骨相演绎", basis="creative_choice")]
        choices.append(CharacterDesignProposal.model_validate(payload))
    assert any("constraint_not_visualized" in issue for issue in validate_casting_proposals(p, choices, None))
    for choice in choices:
        choice.facial_features.append("七十岁面容")
    assert not validate_casting_proposals(p, choices, None)
    choices[0].facial_features.append("十九岁少年面容，毫无皱纹")
    assert any("age_contradiction" in issue for issue in validate_casting_proposals(p, choices, None))


@pytest.mark.asyncio
async def test_new_unsourced_response_requires_reason_and_decisions_but_legacy_reuse_is_allowed():
    from novelvideo.character_design_stage import design_merged_characters
    from novelvideo.structured_extraction import MergedCharacter
    from tests.test_character_build_stages import proposals
    legacy = proposals()
    for p in legacy:
        p.pop("rationale", None)
        p.pop("casting_decisions", None)
    class Agent:
        def __init__(self): self.calls = 0
        async def run(self, prompt):
            self.calls += 1
            return {"design_proposals": legacy}
    agent = Agent()
    fresh = MergedCharacter(name="甲")
    await design_merged_characters([fresh], agent=agent)
    assert not fresh.design_accepted
    assert agent.calls == 2
    existing = MergedCharacter(name="乙")
    await design_merged_characters([existing], agent=agent, existing_designs={"乙": legacy})
    assert existing.design_accepted
    assert agent.calls == 2


def test_persistence_keeps_strict_issues_for_fresh_unsourced_proposals():
    from novelvideo.structured_builders import _visual_workspace_for_merged_character
    from novelvideo.structured_extraction import MergedCharacter
    from novelvideo.character_visual.models import CharacterVisualWorkspace
    from tests.test_character_build_stages import proposals
    legacy = proposals()
    for p in legacy:
        p.pop("rationale", None)
        p.pop("casting_decisions", None)
    item = MergedCharacter(name="甲", design_proposals=legacy, design_accepted=False)
    fresh = _visual_workspace_for_merged_character(item=item, source_text="甲来了", existing_workspace=None, existing_roster_proposals=[])
    assert all(p.quality_issues for p in fresh.design_proposals)
    assert all(any("casting_decisions:required" in issue for issue in p.quality_issues) for p in fresh.design_proposals)
    assert fresh.visual_bible is None
    prior = CharacterVisualWorkspace(character_id="甲", profile=profile(), selected_proposal_id="p0",
        design_proposals=[CharacterDesignProposal.model_validate(p) for p in legacy])
    retained = _visual_workspace_for_merged_character(item=item, source_text="甲来了", existing_workspace=prior, existing_roster_proposals=[])
    assert retained.selected_proposal_id == "p0"
    assert retained.design_proposals == prior.design_proposals


@pytest.mark.asyncio
async def test_excluded_source_warning_does_not_waste_a_design_revision(tmp_path):
    """A display-only source warning must not be treated as a fixable rejection.

    `source_warnings` reach `validate_casting_proposals` through the dossier, so
    an unexempted `excluded_source:` code burned a second design call and then
    still failed the recast.
    """
    from novelvideo.character_design_stage import CharacterDesignOutput
    from novelvideo.character_visual import casting_service
    from novelvideo.character_visual.models import CharacterNarrativeProfile, CharacterVisualWorkspace
    from novelvideo.character_visual.store import CharacterVisualWorkspaceStore
    from tests.test_character_build_stages import proposals as valid_proposals

    character = CharacterNarrativeProfile(character_id="甲", name="甲",
        source_warnings=["excluded_source:hair_style:quote_mismatch"])
    store = CharacterVisualWorkspaceStore(tmp_path)
    store.save(CharacterVisualWorkspace(character_id="甲", profile=character))
    prompts: list[str] = []

    async def design(**kwargs):
        prompts.append(kwargs["prompt"])
        return CharacterDesignOutput(design_proposals=valid_proposals())

    await casting_service.design_and_publish(
        store=store,
        character_id="甲",
        identity_id=None,
        expected_revision=None,
        grounded_profile=character,
        source_revision="source1",
        style="水墨",
        runtime=type("R", (), {"run_structured": staticmethod(design), "snapshot": type("S", (), {"task_role": "knowledge_extraction"})()})(),
        assert_live=lambda: None,
    )

    assert len(prompts) == 1, "a display-only source warning must not trigger a revision"
    assert len(store.get("甲").design_proposals) == 3
