import hashlib
import json

import pytest

from novelvideo.character_visual.casting_brief import build_casting_revision
from novelvideo.character_visual.models import CharacterVisualWorkspace
from tests.character_visual.test_casting_brief import profile
from tests.character_visual.test_casting_proposals import proposal


def inputs(*facts):
    p = profile(*facts)
    selected = proposal()
    selected.title = "甲的骨相方案"
    selected.rationale = "保留明确约束"
    from novelvideo.character_visual.casting_models import CastingDecision
    selected.casting_decisions = [CastingDecision(decision_id="creative", attribute="face_shape", value=selected.face_shape,
        reason="自由骨相", basis="creative_choice")]
    for f in p.facts:
        if f.identity_id is None:
            selected.casting_decisions.append(CastingDecision(decision_id=f.fact_id, attribute=f.field,
                value=f.value, reason="原文明示", basis="evidence", fact_ids=[f.fact_id]))
            selected.facial_features.append(f.value)
    workspace = CharacterVisualWorkspace(character_id=p.character_id, profile=p, design_proposals=[selected], selected_proposal_id=selected.proposal_id)
    return build_casting_revision(workspace, None, "source1", "水墨"), selected, p


def compile_inputs(data):
    from novelvideo.character_visual.casting_compiler import compile_casting_snapshot
    return compile_casting_snapshot(*data, "水墨")


def rebuild(data):
    revision, selected, p = data
    workspace = CharacterVisualWorkspace(character_id=p.character_id, profile=p,
        design_proposals=[selected], selected_proposal_id=selected.proposal_id)
    return build_casting_revision(workspace, revision.identity_id, revision.source_revision, revision.style_revision), selected, p


def test_canonical_digest():
    from novelvideo.character_visual.casting_compiler import snapshot_digest
    a = {"乙": [1, {"b": 2, "a": 1}], "a": 2}
    b = {"a": 2, "乙": [1, {"a": 1, "b": 2}]}
    assert snapshot_digest(a) == snapshot_digest(b) == hashlib.sha256(json.dumps(a, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()


def test_preserves_beauty_age_and_detaches_provenance():
    data = inputs(("甲很漂亮", dict(field="beauty", value="漂亮")), ("甲七十岁", dict(field="age_range", value="七十岁")))
    revision, selected, p = data
    selected.outfit_states = {"default": "红色铠甲"}
    selected.body_type = "修长躯干"
    selected.rationale = "在雪夜宫殿奔跑"
    data = rebuild(data)
    snapshot = compile_inputs(data)
    assert "漂亮" in snapshot.prompt and "七十岁" in snapshot.prompt
    assert "自然皮肤" in snapshot.prompt
    assert all(x not in snapshot.prompt for x in ["红色铠甲", "修长躯干", "雪夜宫殿"])
    assert snapshot.proposal_snapshot["outfit_states"] == {"default": "红色铠甲"}
    before = snapshot.model_dump()
    selected.facial_features.append("后来修改")
    p.facts[0].value = "后来修改"
    revision.decisions[0].value = "后来修改"
    assert snapshot.model_dump() == before


def test_proposal_changes_hash_and_live_source_style_changes_rejected():
    from novelvideo.character_visual.casting_compiler import compile_casting_snapshot
    data = inputs()
    first = compile_inputs(data)
    data[1].hair_style = "卷发"
    with pytest.raises(ValueError, match="stale proposal"):
        compile_inputs(data)
    fresh = rebuild(data)
    assert fresh[0].revision_id != data[0].revision_id
    assert compile_inputs(fresh).snapshot_hash != first.snapshot_hash
    with pytest.raises(ValueError, match="style"):
        compile_casting_snapshot(*data, "写实")
    data[2].biography = "新版本"
    with pytest.raises(ValueError, match="stale"):
        compile_inputs(data)


@pytest.mark.parametrize("fault", ["unselected", "wrong_source", "wrong_stage", "scar", "contradict_age"])
def test_rejects_invalid_selection_and_visual_constraints(fault):
    from novelvideo.character_visual.casting_models import CastingDecision
    data = inputs(("甲七十岁", dict(field="age_range", value="七十岁")))
    if fault == "unselected": data[0].selected_proposal_id = None
    if fault == "wrong_source": data[2].facts[0].source_revision = "old"
    if fault == "wrong_stage":
        data[2].facts.append(data[2].facts[0].model_copy(update={"fact_id": "other", "identity_id": "other"}))
        data[1].casting_decisions.append(CastingDecision(decision_id="other", attribute="age_range", value="七十岁", reason="引用", basis="evidence", fact_ids=["other"]))
    if fault == "scar": data[1].facial_features.append("左眼有伤疤")
    if fault == "contradict_age": data[1].facial_features.append("十九岁少年面容")
    with pytest.raises(ValueError): compile_inputs(data)


def test_nonhuman_preserves_natural_anatomy_without_human_bust():
    data = inputs(("甲是一只猫", dict(field="species", value="猫")))
    data[1].body_type = "猫的四足躯干"
    data = rebuild(data)
    snapshot = compile_inputs(data)
    assert "猫" in snapshot.prompt and "自然解剖" in snapshot.prompt
    assert "半身" not in snapshot.prompt and "肩" not in snapshot.prompt


def test_uniform_is_metadata_not_portrait_instruction():
    data = inputs(("甲穿红色军装", dict(field="uniform", value="红色军装")))
    data[1].facial_features.remove("红色军装")
    data[1].outfit_states = {"default": "红色军装"}
    data = rebuild(data)
    snapshot = compile_inputs(data)
    assert snapshot.hard_constraints[0].value == "红色军装"
    assert "红色军装" not in snapshot.prompt


def test_snapshot_hash_covers_style_source_and_can_be_recomputed():
    from novelvideo.character_visual.casting_compiler import compile_casting_snapshot, snapshot_digest
    from novelvideo.character_visual.casting_brief import build_casting_dossier
    data = inputs()
    first = compile_inputs(data)
    assert first.snapshot_hash == snapshot_digest(first.model_dump(mode="json", exclude={"snapshot_hash"}))
    revision, selected, p = data
    revision.source_revision = "source2"
    revision.style_revision = "写实"
    revision.profile_hash = build_casting_dossier(p, None, "source2", "写实").dossier_hash
    assert compile_casting_snapshot(revision, selected, p, "写实").snapshot_hash != first.snapshot_hash


def test_duplicate_decisions_and_conflicting_structured_visual_field_rejected():
    data = inputs(("甲是圆脸", dict(field="face_shape", value="圆脸")))
    data[1].casting_decisions[0].attribute = "hair_style"
    data[1].casting_decisions[0].value = "短发"
    data = rebuild(data)
    with pytest.raises(ValueError, match="visual_field"):
        compile_inputs(data)
    data[1].face_shape = "圆脸"
    data[1].casting_decisions[0].attribute = "hair_style"
    data[1].casting_decisions[0].value = "短发"
    data[1].casting_decisions.append(data[1].casting_decisions[0].model_copy(deep=True))
    data = rebuild(data)
    with pytest.raises(ValueError, match="decision_ids"):
        compile_inputs(data)


def test_legacy_revision_requires_explicit_rebuild_before_compile():
    from novelvideo.character_visual.casting_models import CastingRevision
    data = inputs()
    legacy = data[0].model_dump(exclude={"proposal_hashes"})
    decoded = CastingRevision.model_validate(legacy)
    with pytest.raises(ValueError, match="rebuild"):
        compile_inputs((decoded, data[1], data[2]))
    assert compile_inputs(rebuild((decoded, data[1], data[2])))


def test_creative_cat_without_source_species_has_no_human_anatomy():
    from novelvideo.character_visual.casting_models import CastingDecision
    data = inputs()
    selected = data[1]
    selected.face_shape = None
    selected.facial_features = ["杏核瞳", "三角耳"]
    selected.body_type = "猫的四足结构"
    selected.hair_style = None
    selected.asymmetry_detail = ""
    selected.distinctive_features = ["浅色耳尖"]
    selected.identity_anchors = ["杏核瞳", "三角耳", "浅色耳尖"]
    selected.casting_decisions = [CastingDecision(decision_id="species", attribute="species", value="猫",
        reason="原文未指定，选定猫的演绎", basis="creative_choice")]
    snapshot = compile_inputs(rebuild(data))
    assert "species: 猫" in snapshot.prompt
    assert not snapshot.hard_constraints
    assert snapshot.creative_choices[0].attribute == "species"
    assert all(term not in snapshot.prompt for term in ["领口", "颈部", "动物身份"])


@pytest.mark.parametrize("species", [None, "机械生命体"])
def test_unknown_and_mechanical_species_use_neutral_anatomy(species):
    data = inputs(*([("甲是机械生命体", dict(field="species", value=species))] if species else []))
    if species:
        data[1].body_type = "机械生命体构造"
    snapshot = compile_inputs(rebuild(data))
    assert "完整头部" in snapshot.prompt
    assert all(term not in snapshot.prompt for term in ["领口", "颈部", "动物身份"])
