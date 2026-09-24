"""Deterministic, detached casting inputs for generation and later adoption."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from .casting_brief import build_casting_dossier, validate_casting_decisions
from .casting_models import CastingRevision, CastingSnapshot
from .casting_proposals import validate_casting_proposal
from .models import CharacterDesignProposal, CharacterNarrativeProfile


def snapshot_digest(payload: Any) -> str:
    """SHA256 of canonical JSON data (callers exclude snapshot_hash itself)."""
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False,
        separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


_PORTRAIT_FIELDS = {"age_range", "age_group", "gender", "hair_style", "face_shape", "facial_feature",
    "facial_features", "distinctive_feature", "beauty", "appearance", "species", "face", "scar",
    "injury_state", "disability", "grooming", "maintenance", "presentation"}


def compile_casting_snapshot(revision: CastingRevision, proposal: CharacterDesignProposal,
                             profile: CharacterNarrativeProfile, style: str) -> CastingSnapshot:
    """Compile a selected proposal using fully resolved style text, not a style name.

    Current revisions store that text in style_revision. Source revisions are
    checked against fact provenance and the dossier content digest. Returned
    data is detached; stores must serialize it and never mutate nested values.
    """
    if proposal.proposal_id not in revision.proposal_ids or revision.selected_proposal_id != proposal.proposal_id:
        raise ValueError("unselected proposal or proposal outside revision")
    if revision.character_id != profile.character_id:
        raise ValueError("profile ownership mismatch")
    if style != revision.style_revision:
        raise ValueError("stale style content")
    dossier = build_casting_dossier(profile, revision.identity_id, revision.source_revision, style)
    if dossier.dossier_hash != revision.profile_hash:
        raise ValueError("stale profile/source/style content")
    issues = validate_casting_proposal(profile, proposal, revision.identity_id,
        source_revision=revision.source_revision, style_revision=style)
    issues += validate_casting_decisions(profile, revision.decisions, revision.identity_id)
    if issues:
        raise ValueError("invalid casting: " + "; ".join(issues))
    constraints = [f"{f.field}: {f.value}" for f in dossier.hard_constraints if f.field in _PORTRAIT_FIELDS]
    decisions = [f"{d.attribute}: {d.value}" for d in proposal.casting_decisions if d.attribute in _PORTRAIT_FIELDS]
    details = [proposal.face_shape, *proposal.facial_features, proposal.hair_style,
        *proposal.distinctive_features, proposal.asymmetry_detail]
    nonhuman = any(f.field == "species" and f.value.casefold() not in {"人", "人类", "human"}
                   for f in dossier.hard_constraints)
    framing = ("动物身份肖像，保持物种自然解剖，完整头部与自然颈部，不拟人化。" if nonhuman else
               "身份肖像，完整头部和颈部，仅保留少量领口，不展示身体或完整服装。")
    prompt = "\n".join([framing, "原文明示约束（最高优先级）：", *constraints,
        "选定设计：", *decisions, *[x for x in details if x],
        "参考风格（不得覆盖原文或选定结构）：" + style,
        "默认呈现：中性纯色背景，均匀柔和光照，清晰身份识别，自然皮肤或物种表面质感。",
        "不添加场景、道具或复杂服装；不凭职业和善恶改变容貌，不虚构伤疤。"])
    hard_ids = {f.fact_id for f in dossier.hard_constraints}
    payload = dict(schema_version=1, revision_id=revision.revision_id, source_revision=revision.source_revision,
        style_revision=revision.style_revision, profile_hash=revision.profile_hash, character_id=revision.character_id,
        identity_id=revision.identity_id, proposal_id=proposal.proposal_id, prompt=prompt,
        hard_constraints=[f.model_dump(mode="json") for f in dossier.hard_constraints],
        design_decisions=[d.model_dump(mode="json") for d in proposal.casting_decisions], style=style,
        proposal_snapshot=proposal.model_dump(mode="json"), source_fact_ids=[f.fact_id for f in dossier.hard_constraints],
        interpretations=[d.model_dump(mode="json") for d in proposal.casting_decisions
                         if d.basis == "evidence" and not set(d.fact_ids) <= hard_ids],
        creative_choices=[d.model_dump(mode="json") for d in proposal.casting_decisions if d.basis == "creative_choice"])
    # JSON round-trip also detaches every nested dict/list from editable drafts.
    payload = json.loads(json.dumps(payload, ensure_ascii=False))
    return CastingSnapshot.model_validate({**payload, "snapshot_hash": snapshot_digest(payload)})
