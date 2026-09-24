from novelvideo.character_visual.models import CharacterNarrativeProfile, CharacterVisualWorkspace
from novelvideo.character_visual.casting_models import CastingDecision


def profile(*facts):
    return CharacterNarrativeProfile(character_id="甲", name="甲", occupation="医生", facts=[
        dict(fact_id=f"f{i}", source_span=dict(start_line=1, end_line=1),
             evidence=value, confidence=1, **fields)
        for i, (value, fields) in enumerate(facts)
    ])


def test_dossier_preserves_explicit_beauty_elder_species_and_provenance():
    from novelvideo.character_visual.casting_brief import build_casting_dossier, build_casting_revision
    p = profile(("甲很漂亮", dict(field="beauty", value="漂亮")),
                ("甲已七十岁", dict(field="age_range", value="七十岁")),
                ("甲是一只猫", dict(field="species", value="猫")))
    dossier = build_casting_dossier(p, None, "source1", "style1")
    assert {f.field for f in dossier.hard_constraints} == {"beauty", "age_range", "species"}
    assert dossier.hard_constraints[0].source_revision == "source1"
    revision = build_casting_revision(CharacterVisualWorkspace(character_id="甲", profile=p), None, "source1", "style1")
    assert revision.profile_hash == dossier.dossier_hash
    assert {d.value for d in revision.decisions} == {"漂亮", "七十岁", "猫"}


def test_identity_age_is_not_imposed_on_other_stage_and_conflict_is_visible():
    from novelvideo.character_visual.casting_brief import build_casting_dossier
    p = profile(("老年甲七十岁", dict(field="age_range", value="七十岁", identity_id="old")),
                ("少年甲十五岁", dict(field="age_range", value="十五岁", identity_id="young")))
    dossier = build_casting_dossier(p, "young", "s", "t")
    assert [f.value for f in dossier.hard_constraints] == ["十五岁"]
    assert build_casting_dossier(p, None, "s", "t").issues
    p.facts[1].identity_id = "old"
    assert any("conflicting" in x for x in build_casting_dossier(p, "old", "s", "t").issues)


def test_bad_references_stage_and_occupation_to_face_are_rejected():
    from novelvideo.character_visual.casting_brief import validate_casting_decisions
    p = profile(("甲是医生", dict(field="occupation", value="医生")),
                ("甲七十岁", dict(field="age_range", value="七十岁", identity_id="old")))
    for attribute, value, refs in [("face_shape", "方脸", ["f0"]), ("age_range", "七十岁", ["f1"]), ("beauty", "漂亮", ["fake"])]:
        d = CastingDecision(decision_id="d", attribute=attribute, value=value, reason="依据", basis="evidence", fact_ids=refs)
        assert validate_casting_decisions(p, [d], "young")


def test_invented_scars_and_bogus_quote_are_not_evidence():
    from novelvideo.character_visual.casting_brief import validate_casting_decisions
    p = profile(("甲到来", dict(field="scar", value="左眼有疤")))
    evidence = CastingDecision(decision_id="d", attribute="scar", value="左眼有疤", reason="原文", basis="evidence", fact_ids=["f0"])
    assert validate_casting_decisions(p, [evidence], None)
    creative = CastingDecision(decision_id="c", attribute="distinctive_feature", value="左眼有疤", reason="辨识度", basis="creative_choice")
    assert validate_casting_decisions(p, [creative], None)


def test_missing_facts_are_visible_and_job_does_not_create_phenotype():
    from novelvideo.character_visual.casting_brief import build_casting_dossier
    dossier = build_casting_dossier(profile(), None, "s", "t")
    assert not dossier.hard_constraints
    assert "missing:visual_evidence" in dossier.issues
    assert not dossier.interpretations


def test_work_habits_can_support_grooming_interpretation_but_not_bone_structure():
    from novelvideo.character_visual.casting_brief import validate_casting_decisions
    p = profile(("甲每天在尘土中劳作", dict(field="work_habits", value="每天在尘土中劳作")))
    d = CastingDecision(decision_id="d", attribute="grooming", value="发梢略有浮尘", reason="劳作环境支持暂时的打理状态，并非天生特征", basis="evidence", fact_ids=["f0"])
    assert not validate_casting_decisions(p, [d], None)
    assert validate_casting_decisions(p, [d.model_copy(update={"attribute": "face_shape", "value": "方脸"})], None)


def test_precise_age_and_compatible_age_group_are_not_conflicting():
    from novelvideo.character_visual.casting_brief import build_casting_dossier
    p = profile(("甲十九岁", dict(field="age_range", value="十九岁")), ("甲是青年", dict(field="age_group", value="youth")))
    assert not any("conflicting" in x for x in build_casting_dossier(p, None, "s", "t").issues)


def test_creative_choice_cannot_override_explicit_age_and_quote_negation_is_invalid():
    from novelvideo.character_visual.casting_brief import validate_casting_decisions, build_casting_dossier
    p = profile(("甲七十岁", dict(field="age_range", value="七十岁")))
    d = CastingDecision(decision_id="d", attribute="age_range", value="十九岁", reason="自由创作", basis="creative_choice")
    assert validate_casting_decisions(p, [d], None)
    negated = profile(("甲并不漂亮", dict(field="beauty", value="漂亮")))
    assert not build_casting_dossier(negated, None, "s", "t").hard_constraints


def test_profile_from_merged_does_not_trust_wrong_quote_location():
    from novelvideo.character_visual.casting_brief import profile_from_merged, build_casting_dossier
    from novelvideo.structured_extraction import MergedCharacter
    item = MergedCharacter(name="甲", evidence=[dict(field="face", value="漂亮", evidence_text="甲很漂亮", source_start=0, source_end=4)])
    p = profile_from_merged(item, "s", "甲不是这个人")
    assert not build_casting_dossier(p, None, "s", "t").hard_constraints


def test_recast_revision_does_not_select_old_adopted_proposal():
    from novelvideo.character_visual.casting_brief import build_casting_revision
    from novelvideo.character_visual.models import CharacterDesignProposal
    workspace = CharacterVisualWorkspace(character_id="甲", profile=profile(), selected_proposal_id="old",
        design_proposals=[CharacterDesignProposal(proposal_id="new", title="新方案")])
    revision = build_casting_revision(workspace, None, "s", "new-style")
    assert revision.proposal_ids == ["new"]
    assert revision.selected_proposal_id is None
    assert workspace.selected_proposal_id == "old"
