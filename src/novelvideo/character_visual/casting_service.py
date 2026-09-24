"""Casting drafts are independent of the currently adopted visual bible."""
from __future__ import annotations

import inspect
import json

from .casting_brief import build_casting_dossier, build_casting_revision
from .casting_compiler import compile_casting_snapshot, snapshot_digest
from .casting_proposals import validate_casting_proposals, validate_casting_proposal
from .models import CharacterDesignProposal


def stage_workspace(workspace, identity_id):
    workspace = workspace.model_copy(deep=True)
    workspace.profile.facts = [f for f in workspace.profile.facts if f.identity_id in (None, identity_id)]
    if identity_id is None:
        return workspace.model_copy(deep=True)
    return workspace.model_copy(deep=True, update={
        'design_proposals': workspace.identity_design_proposals.get(identity_id, []),
        'selected_proposal_id': workspace.identity_selected_proposal_ids.get(identity_id),
        'casting_revision': workspace.identity_casting_revisions.get(identity_id),
        'visual_bible': workspace.identity_visual_bibles.get(identity_id)})


def save_stage(workspace, draft, identity_id):
    if identity_id is None:
        workspace.design_proposals = draft.design_proposals
        workspace.selected_proposal_id = draft.selected_proposal_id
        workspace.casting_revision = draft.casting_revision
    else:
        workspace.identity_design_proposals[identity_id] = draft.design_proposals
        workspace.identity_selected_proposal_ids[identity_id] = draft.selected_proposal_id
        workspace.identity_casting_revisions[identity_id] = draft.casting_revision


def resolved_style(ctx):
    from novelvideo.project_config import load_project_config_file_from_state_dir
    from novelvideo.services.style_service import StyleService
    config = load_project_config_file_from_state_dir(ctx.state_dir)
    style = str(config.get('visual_style') or 'chinese_period_drama')
    snapshot = StyleService.resolve_style_snapshot(style, project_dir=str(ctx.output_dir),
        username=ctx.owner_username, project=ctx.project_name)
    return json.dumps(snapshot.model_dump(mode='json'), ensure_ascii=False, sort_keys=True)


def revise_draft(store, character_id, *, identity_id, expected_revision, selected_proposal_id, proposals=None):
    def change(workspace):
        draft = stage_workspace(workspace, identity_id)
        revision = draft.casting_revision
        if revision is None:
            raise ValueError('casting revision missing; recast first')
        if proposals is not None:
            if {p.proposal_id for p in proposals} != set(revision.proposal_ids):
                raise ValueError('proposal IDs must match existing draft')
            draft.design_proposals = proposals
            issues = validate_casting_proposals(draft.profile, proposals, identity_id,
                limitation_reason=workspace.casting_limitation_reasons.get(identity_id or 'base', ''),
                source_revision=revision.source_revision, style_revision=revision.style_revision)
            if issues:
                raise ValueError('; '.join(issues))
        if selected_proposal_id not in {p.proposal_id for p in draft.design_proposals}:
            raise ValueError('unknown proposal selection')
        for p in draft.design_proposals:
            issues = validate_casting_proposal(draft.profile, p, identity_id,
                source_revision=revision.source_revision, style_revision=revision.style_revision)
            if issues:
                raise ValueError('; '.join(issues))
        draft.selected_proposal_id = selected_proposal_id
        draft.casting_revision = build_casting_revision(draft, identity_id, revision.source_revision, revision.style_revision)
        save_stage(workspace, draft, identity_id)
    return store.mutate(character_id, identity_id=identity_id, expected_revision=expected_revision, change=change)


def compile_current(workspace, identity_id, expected_revision, source_revision, style):
    draft = stage_workspace(workspace, identity_id)
    revision = draft.casting_revision
    if revision is None or revision.revision_id != expected_revision:
        raise ValueError('casting revision conflict')
    if revision.source_revision != source_revision or revision.style_revision != style:
        raise ValueError('source/style changed; recast required')
    proposal = next((p for p in draft.design_proposals if p.proposal_id == draft.selected_proposal_id), None)
    if proposal is None:
        raise ValueError('select a casting proposal first')
    return compile_casting_snapshot(revision, proposal, draft.profile, style)


def is_stale(candidate, workspace, identity_id, source_revision, style):
    try:
        snapshot = compile_current(workspace, identity_id, candidate.snapshot.revision_id, source_revision, style)
        return snapshot.snapshot_hash != candidate.snapshot.snapshot_hash
    except ValueError:
        return True


async def design_and_publish(*, store, character_id, identity_id, expected_revision,
                             grounded_profile, source_revision, style, runtime, assert_live,
                             original_hash=None, before_commit=None):
    from novelvideo.character_design_stage import CharacterDesignOutput, CHARACTER_DESIGN_SYSTEM_PROMPT
    original = store.get(character_id)
    original_hash = original_hash or snapshot_digest(original.model_dump(mode='json'))
    if runtime is None or getattr(getattr(runtime, 'snapshot', None), 'task_role', None) != 'knowledge_extraction':
        raise ValueError('knowledge_extraction runtime required')
    design_profile = grounded_profile.model_copy(deep=True)
    design_profile.facts = [f for f in design_profile.facts if f.identity_id in (None, identity_id)]
    dossier = build_casting_dossier(design_profile, identity_id, source_revision, style)
    if any(i.startswith(('conflicting:', 'identity_required:')) for i in dossier.issues):
        raise ValueError('source evidence conflicts; review source facts before design')
    output = await runtime.run_structured(output_type=CharacterDesignOutput,
        system_prompt=CHARACTER_DESIGN_SYSTEM_PROMPT,
        prompt=json.dumps({'casting_dossier': dossier.model_dump(mode='json'), 'identity_id': identity_id,
                           'project_style': style}, ensure_ascii=False))
    checked = assert_live()
    if inspect.isawaitable(checked):
        await checked
    if snapshot_digest(store.get(character_id).model_dump(mode='json')) != original_hash:
        raise ValueError('casting inputs changed during recast')
    output = CharacterDesignOutput.model_validate(output.model_dump() if hasattr(output, 'model_dump') else vars(output))
    proposals = []
    for item in output.design_proposals:
        data = item.model_dump(mode='json')
        data['outfit_states'] = {v.state: v.description for v in item.outfit_states}
        proposals.append(CharacterDesignProposal.model_validate(data))
    issues = validate_casting_proposals(design_profile, proposals, identity_id,
        limitation_reason=output.limitation_reason, source_revision=source_revision, style_revision=style)
    if issues:
        raise ValueError('casting proposals rejected: ' + '; '.join(issues))
    def change(workspace):
        if before_commit:
            before_commit()
        if snapshot_digest(workspace.model_dump(mode='json')) != original_hash:
            raise ValueError('casting inputs changed during recast')
        draft = stage_workspace(workspace, identity_id)
        # Preserve other-stage evidence while updating only this stage's source facts.
        untouched = [f for f in workspace.profile.facts if f.identity_id not in (None, identity_id)]
        grounded_profile.facts = list({f.fact_id: f for f in [*untouched, *grounded_profile.facts]}.values())
        workspace.profile = grounded_profile.model_copy(deep=True)
        draft.profile = stage_workspace(workspace, identity_id).profile
        draft.design_proposals = proposals
        draft.selected_proposal_id = None
        draft.casting_revision = build_casting_revision(draft, identity_id, source_revision, style)
        save_stage(workspace, draft, identity_id)
        workspace.casting_limitation_reasons[identity_id or 'base'] = output.limitation_reason
    return store.mutate(character_id, identity_id=identity_id, expected_revision=expected_revision, change=change)


async def adopt_candidate(*args, **kwargs):
    from .casting_adoption import adopt_candidate as publish
    return await publish(*args, **kwargs)
