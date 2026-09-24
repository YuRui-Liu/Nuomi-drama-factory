"""Explicit story-grounded casting. Reads never enqueue generation or recasting."""
from __future__ import annotations

import hashlib
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from novelvideo.api.auth import get_api_user
from novelvideo.api.deps import resolve_project_scope, make_sqlite_store_for_context, make_static_url_for_context
from novelvideo.character_visual.casting_brief import build_casting_dossier
from novelvideo.character_visual.casting_compiler import snapshot_digest
from novelvideo.character_visual.casting_models import CastingAdoption, CastingCandidate, NonBlank
from novelvideo.character_visual.casting_review import ReviewReference
from novelvideo.character_visual.casting_service import (
    stage_workspace, revise_draft, compile_current, resolved_style, adopt_candidate, is_stale,
)
from novelvideo.character_visual.casting_source import load_sources
from novelvideo.character_visual.casting_store import CastingCandidateStore
from novelvideo.character_visual.casting_submission import SubmissionJournal, submission_view, submit_once
from novelvideo.character_visual.models import CharacterDesignProposal, CharacterNarrativeProfile, CharacterVisualWorkspace
from novelvideo.character_visual.store import CharacterVisualWorkspaceStore
from novelvideo.ports import get_task_backend
from novelvideo.task_state import get_task_manager
from novelvideo.text_task_runtime.settings import resolve_configured_agent_task_route

router = APIRouter()
BASE = '/projects/{project}/characters/{name}/casting'


class Request(BaseModel):
    model_config = ConfigDict(extra='forbid')


class RecastRequest(Request):
    idempotency_key: NonBlank
    expected_revision: NonBlank | None = None


class GenerateRequest(Request):
    idempotency_key: NonBlank
    expected_revision: NonBlank
    model: NonBlank | None = None


class DraftRequest(Request):
    expected_revision: NonBlank
    selected_proposal_id: NonBlank
    proposals: list[CharacterDesignProposal] | None = None


class ReferenceSelection(Request):
    character_id: NonBlank
    identity_id: NonBlank | None = None


class ReviewRequest(Request):
    idempotency_key: NonBlank
    references: list[ReferenceSelection] | None = Field(default=None, max_length=7)


@asynccontextmanager
async def scope(project, name, user, identity_id, *, editor=False):
    resolved = await resolve_project_scope(project, user, required_role='editor' if editor else 'viewer')
    ctx = resolved.ctx
    if editor and ctx.effective_role not in ('editor', 'owner', 'admin'):
        raise HTTPException(403, 'editor role required')
    store = await make_sqlite_store_for_context(ctx)
    try:
        character = store.get_character(name)
        if character is None or (identity_id is not None and not any(i.identity_id == identity_id for i in character.identities)):
            raise HTTPException(404, 'character or identity not found')
        yield ctx, store, character
    finally:
        await store.close()


def workspace_store(ctx):
    return CharacterVisualWorkspaceStore(ctx.output_dir, state_dir=ctx.state_dir)


def candidate_store(ctx):
    return CastingCandidateStore(ctx.output_dir, state_dir=ctx.state_dir, project_id=ctx.project_id)


def workspace_or_empty(ctx, name):
    return workspace_store(ctx).get(name) or CharacterVisualWorkspace(character_id=name,
        profile=CharacterNarrativeProfile(character_id=name, name=name))


def owned_candidate(ctx, name, identity_id, candidate_id):
    candidate = candidate_store(ctx).get(candidate_id)
    if candidate is None or (candidate.character_id, candidate.identity_id) != (name, identity_id):
        raise HTTPException(404, 'candidate not found')
    return candidate


def freeze_image_model(ctx, requested):
    from novelvideo import config
    from novelvideo.api.deps import get_media_capability_store, get_media_credential_resolver
    from novelvideo.media_capabilities.runtime.configuration import load_grsai_runtime_configuration
    from novelvideo.media_capabilities.image.catalog import resolve_grsai_image_model, legacy_image_model_values
    from novelvideo.project_config import load_project_config_file_from_state_dir
    runtime = load_grsai_runtime_configuration(get_media_capability_store(), get_media_credential_resolver())
    resolution = resolve_grsai_image_model(requested_model=requested,
        project_model=load_project_config_file_from_state_dir(ctx.state_dir).get('character_image_selection'),
        runtime_model=runtime.model,
        legacy_values=legacy_image_model_values(config.IMAGE_GENERATION_SELECTIONS, config.LEGACY_IMAGE_GENERATION_SELECTION_ALIASES))
    return resolution.model, {'requested_model': resolution.requested_model, 'resolution_source': resolution.resolution_source}


def current_version(ctx, name, identity_id):
    from novelvideo.production_workflow import ProductionWorkflowStore
    from novelvideo.production_workflow.slot_ids import character_portrait_slot_id, character_identity_portrait_slot_id
    workflow = ProductionWorkflowStore(Path(ctx.state_dir) / 'production_workflow.json')
    slot_id = character_identity_portrait_slot_id(name, identity_id) if identity_id else character_portrait_slot_id(name)
    try:
        slot, versions = workflow.get_slot(slot_id)
    except KeyError:
        return None
    version = versions.get(slot.current_version_id)
    if version is None or slot.asset_kind != 'character_portrait':
        return None
    safe = candidate_store(ctx)
    path = safe.safe_path(version.asset_path)
    # Mutable canonical files are never offered as immutable review references.
    immutable = path.parent.name in ('portrait_versions', 'versions') or path.name == 'candidate.png' and 'casting_candidates' in path.parts
    result = {'version_id': version.version_id, 'adoption_status': version.adoption_status,
        'url': make_static_url_for_context(ctx, path.relative_to(safe.project_dir).as_posix(), local_path=path),
        'candidate_id': (version.generation_metadata or {}).get('candidate_id'),
        'immutable': immutable, '_path': path}
    return result


def legacy_current(ctx, character, identity_id):
    if identity_id:
        identity = next(i for i in character.identities if i.identity_id == identity_id)
        path = getattr(identity, 'portrait_image', '')
    else:
        from novelvideo.utils.path_resolver import compute_portrait_path
        path = compute_portrait_path(ctx.output_dir, character.name)
    if not path:
        return None
    store = candidate_store(ctx)
    try:
        safe = store.safe_path(path)
        if not safe.is_file():
            return None
        return {'url': make_static_url_for_context(ctx, safe.relative_to(store.project_dir).as_posix(), local_path=safe),
                'adoption_status': 'legacy_unconfirmed', 'reference_eligible': False}
    except (ValueError, OSError):
        return None


def resolve_references(ctx, sqlite_store, candidate, selections, documents=None, source_revision=''):
    from novelvideo.character_visual.casting_source import verified_fact
    automatic = selections is None
    if automatic:
        selections = [ReferenceSelection(character_id=c.name, identity_id=identity_id)
            for c in sorted(sqlite_store.get_all_characters(), key=lambda c: c.name)
            for identity_id in [None, *sorted(i.identity_id for i in c.identities)]
            if c.name != candidate.character_id]
    refs = []
    seen = set()
    excluded = []
    for selected in selections:
        target = sqlite_store.get_character(selected.character_id)
        if target is None or (selected.identity_id and not any(i.identity_id == selected.identity_id for i in target.identities)):
            raise HTTPException(404, 'reference character or identity not found')
        if (selected.character_id, selected.identity_id) == (candidate.character_id, candidate.identity_id):
            raise HTTPException(422, 'candidate cannot be its own comparison reference')
        try:
            version = current_version(ctx, selected.character_id, selected.identity_id)
        except (ValueError, OSError):
            if not automatic:
                raise HTTPException(409, 'reference asset is unavailable or unsafe')
            version = None
        if not version or not version['immutable'] or version['adoption_status'] != 'adopted':
            if automatic:
                excluded.append({'character_id': selected.character_id, 'identity_id': selected.identity_id, 'reason': 'no_confirmed_immutable_current'})
                continue
            raise HTTPException(409, 'reference requires a confirmed immutable current version')
        ref_id = version['candidate_id'] or 'workflow:' + version['version_id']
        if ref_id == candidate.candidate_id or ref_id in seen:
            raise HTTPException(422, 'duplicate or self reference')
        seen.add(ref_id)
        facts = []
        for name in (candidate.character_id, selected.character_id):
            workspace = workspace_store(ctx).get(name)
            for fact in workspace.profile.facts if workspace else []:
                if (fact.field in ('relationship', 'relationships') and fact.assertion == 'explicit'
                    and fact.trust == 'trusted' and candidate.character_id in fact.evidence
                    and selected.character_id in fact.evidence):
                    verified = verified_fact(fact, documents or {}, [name], source_revision)
                    if verified:
                        facts.append(verified)
        refs.append(ReviewReference(project_id=ctx.project_id, character_id=selected.character_id,
            identity_id=selected.identity_id, candidate_id=ref_id, version=version['version_id'],
            asset_path=str(version['_path']), asset_sha256=hashlib.sha256(version['_path'].read_bytes()).hexdigest(),
            relationship_facts=list({f.fact_id: f for f in facts}.values())))
    eligible = len(refs)
    for ref in refs[7:]:
        excluded.append({'character_id': ref.character_id, 'identity_id': ref.identity_id, 'reason': 'comparison_limit_7'})
    refs = refs[:7]
    return refs, {'scope': 'supplied_references' if refs else 'none', 'selection': 'automatic' if automatic else 'explicit',
                  'eligible_count': eligible, 'compared_count': len(refs), 'excluded': excluded}


def candidate_view(ctx, candidate, workspace, identity_id, source_revision, style):
    from novelvideo.character_visual.casting_adoption import adoption_requirements
    data = candidate.model_dump(mode='json', exclude={'asset_path', 'submission_token'})
    data['stale'] = is_stale(candidate, workspace, identity_id, source_revision, style)
    data['adoption_requirements'] = adoption_requirements(candidate)
    if data['stale']:
        data['adoption_requirements']['blocked_reason'] = 'stale_candidate'
    data['url'] = None
    if candidate.asset_path and candidate.generation_status == 'succeeded':
        store = candidate_store(ctx)
        try:
            store.read_verified_asset(candidate.candidate_id)
            path = store.safe_path(candidate.asset_path)
            data['url'] = make_static_url_for_context(ctx, path.relative_to(store.project_dir).as_posix(), local_path=path)
        except (OSError, ValueError):
            data['asset_error'] = 'candidate_asset_unavailable'
    return data


def conflict(exc):
    raise HTTPException(409, str(exc)) from exc


@router.get(BASE)
async def get_casting(project: str, name: str, identity_id: str | None = None, user: dict = Depends(get_api_user)):
    async with scope(project, name, user, identity_id) as (ctx, sql, character):
        source_revision, prerequisite = '', None
        try:
            _, source_revision = await load_sources(ctx.output_dir, sql)
        except ValueError as exc:
            prerequisite = str(exc)
        # Source loading may await. Capture bible + workflow current together only
        # after it completes, under the same project lock used by adoption.
        from novelvideo.production_workflow import production_workflow_project_lock
        with production_workflow_project_lock(ctx.state_dir):
            workspace = workspace_or_empty(ctx, name)
            draft = stage_workspace(workspace, identity_id)
            revision = draft.casting_revision
            style = resolved_style(ctx)
            current = current_version(ctx, name, identity_id)
            if current:
                current.pop('_path', None)
            journal = SubmissionJournal(ctx)
            dossier = build_casting_dossier(draft.profile, identity_id, source_revision or 'missing', style)
            draft_stale = bool(revision and (revision.source_revision != source_revision
                or revision.style_revision != style or revision.profile_hash != dossier.dossier_hash))
            return {'ok': True, 'data': {'character_id': name, 'identity_id': identity_id,
                'can_edit': ctx.effective_role in ('editor', 'owner', 'admin'),
                'identities': [{'identity_id': i.identity_id, 'name': getattr(i, 'identity_name', '')} for i in character.identities],
                'dossier': dossier.model_dump(mode='json'), 'draft_stale': draft_stale,
                'current_source_revision': source_revision, 'current_style_revision': style,
                'revision': revision.model_dump(mode='json') if revision else None,
                'proposals': [p.model_dump(mode='json') for p in draft.design_proposals],
                'selected_proposal_id': draft.selected_proposal_id, 'current': current,
                'legacy_current': legacy_current(ctx, character, identity_id) if current is None else None,
                'current_visual_bible': draft.visual_bible.model_dump(mode='json') if draft.visual_bible else None,
                'limitation_reason': workspace.casting_limitation_reasons.get(identity_id or 'base', ''),
                'prerequisite_error': prerequisite,
                'tasks': [submission_view(row, ctx, get_task_manager()) for row in journal.list(name, identity_id)]}}


@router.patch(BASE)
async def patch_casting(project: str, name: str, body: DraftRequest, identity_id: str | None = None,
                        user: dict = Depends(get_api_user)):
    async with scope(project, name, user, identity_id, editor=True) as (ctx, sql, character):
        try:
            draft = stage_workspace(workspace_or_empty(ctx, name), identity_id)
            if not draft.casting_revision or draft.casting_revision.revision_id != body.expected_revision:
                raise ValueError('casting revision conflict')
            _, source_revision = await load_sources(ctx.output_dir, sql)
            if source_revision != draft.casting_revision.source_revision or resolved_style(ctx) != draft.casting_revision.style_revision:
                raise ValueError('source/style changed; recast required')
        except ValueError as exc:
            conflict(exc)
        try:
            result = revise_draft(workspace_store(ctx), name, identity_id=identity_id,
                expected_revision=body.expected_revision, selected_proposal_id=body.selected_proposal_id, proposals=body.proposals)
        except ValueError as exc:
            raise HTTPException(409 if 'revision conflict' in str(exc) else 422, str(exc)) from exc
        draft = stage_workspace(result, identity_id)
        return {'ok': True, 'data': {'revision': draft.casting_revision.model_dump(mode='json'),
            'selected_proposal_id': draft.selected_proposal_id, 'proposals': [p.model_dump(mode='json') for p in draft.design_proposals]}}


@router.post(BASE + '/recast', status_code=202)
async def recast(project: str, name: str, body: RecastRequest, identity_id: str | None = None,
                 user: dict = Depends(get_api_user)):
    async with scope(project, name, user, identity_id, editor=True) as (ctx, sql, character):
        journal = SubmissionJournal(ctx)
        fingerprint = snapshot_digest(body.model_dump(mode='json'))
        try:
            existing = journal.existing('recast', name, identity_id, body.idempotency_key, fingerprint)
            if existing:
                return {'ok': True, 'data': submission_view(existing, ctx, get_task_manager())}
            _, source_revision = await load_sources(ctx.output_dir, sql)
            workspace = workspace_or_empty(ctx, name)
            draft = stage_workspace(workspace, identity_id)
            if (draft.casting_revision.revision_id if draft.casting_revision else None) != body.expected_revision:
                raise ValueError('casting revision conflict')
            style = resolved_style(ctx)
            route = resolve_configured_agent_task_route(ctx=ctx, task_role='knowledge_extraction')
            if workspace_store(ctx).get(name) is None:
                workspace_store(ctx).save(workspace)
            result = await submit_once(ctx=ctx, journal=journal, operation='recast', character_id=name,
                identity_id=identity_id, idempotency_key=body.idempotency_key, fingerprint=fingerprint,
                task_type='character_casting_proposals', backend=get_task_backend(), manager=get_task_manager(),
                payload={'character_id': name, 'identity_id': identity_id, 'expected_revision': body.expected_revision,
                    'workspace_hash': snapshot_digest(workspace.model_dump(mode='json')), 'source_revision': source_revision,
                    'style': style, 'agent_route_override': route.model_dump(exclude={'source', 'task_role'}),
                    'display_name': f'角色重新选角 · {name}'})
        except ValueError as exc:
            conflict(exc)
        return {'ok': True, 'data': result}


@router.get(BASE + '/candidates')
async def list_candidates(project: str, name: str, identity_id: str | None = None, user: dict = Depends(get_api_user)):
    async with scope(project, name, user, identity_id) as (ctx, sql, character):
        try:
            _, source_revision = await load_sources(ctx.output_dir, sql)
        except ValueError:
            source_revision = ''
        workspace, style = workspace_or_empty(ctx, name), resolved_style(ctx)
        return {'ok': True, 'data': [candidate_view(ctx, c, workspace, identity_id, source_revision, style)
            for c in candidate_store(ctx).list_candidates(name, identity_id)]}


@router.post(BASE + '/candidates', status_code=202)
async def generate_candidate(project: str, name: str, body: GenerateRequest, identity_id: str | None = None,
                             user: dict = Depends(get_api_user)):
    async with scope(project, name, user, identity_id, editor=True) as (ctx, sql, character):
        journal = SubmissionJournal(ctx)
        fingerprint = snapshot_digest(body.model_dump(mode='json'))
        try:
            existing = journal.existing('generate', name, identity_id, body.idempotency_key, fingerprint)
            if existing:
                return {'ok': True, 'data': submission_view(existing, ctx, get_task_manager())}
            _, source_revision = await load_sources(ctx.output_dir, sql)
            style = resolved_style(ctx)
            model, selection = freeze_image_model(ctx, body.model)
            def prepare(row):
                workspace = workspace_or_empty(ctx, name)
                snapshot = compile_current(workspace, identity_id, body.expected_revision, source_revision, style)
                candidate_id = 'casting-' + row['submission_token']
                candidate_store(ctx).create_pending(CastingCandidate(candidate_id=candidate_id, project_id=ctx.project_id,
                    character_id=name, identity_id=identity_id, snapshot=snapshot, requested_model=model,
                    task_id='submission:' + row['submission_token'], submission_token=row['submission_token']))
                row['candidate_id'] = candidate_id
                row['payload'].update(candidate_id=candidate_id, model=model, model_selection=selection)
            result = await submit_once(ctx=ctx, journal=journal, operation='generate', character_id=name,
                identity_id=identity_id, idempotency_key=body.idempotency_key, fingerprint=fingerprint,
                task_type='character_portrait', backend=get_task_backend(), manager=get_task_manager(), prepare=prepare,
                payload={'mode': 'casting_candidate', 'character_name': name, 'identity_id': identity_id,
                         'display_name': f'选角候选 · {name}'})
        except ValueError as exc:
            conflict(exc)
        return {'ok': True, 'data': result}


@router.post(BASE + '/candidates/{candidate_id}/review', status_code=202)
async def review(project: str, name: str, candidate_id: str, body: ReviewRequest,
                 identity_id: str | None = None, user: dict = Depends(get_api_user)):
    async with scope(project, name, user, identity_id, editor=True) as (ctx, sql, character):
        candidate = owned_candidate(ctx, name, identity_id, candidate_id)
        journal = SubmissionJournal(ctx)
        fingerprint = snapshot_digest([candidate_id, body.model_dump(mode='json')])
        try:
            existing = journal.existing('review', name, identity_id, body.idempotency_key, fingerprint)
            if existing:
                return {'ok': True, 'data': submission_view(existing, ctx, get_task_manager())}
            candidate_store(ctx).read_verified_asset(candidate_id)
            try:
                documents, source_revision = await load_sources(ctx.output_dir, sql)
            except ValueError:
                documents, source_revision = {}, ''
            references, coverage = resolve_references(ctx, sql, candidate, body.references, documents, source_revision)
            route = resolve_configured_agent_task_route(ctx=ctx, task_role='identity_sheet_qc')
            def prepare(row):
                row['candidate_id'] = candidate_id
                row['attempt_id'] = row['submission_token']
                row['payload']['attempt_id'] = row['attempt_id']
                row['reference_coverage'] = coverage
            result = await submit_once(ctx=ctx, journal=journal, operation='review', character_id=name,
                identity_id=identity_id, idempotency_key=body.idempotency_key, fingerprint=fingerprint,
                task_type='character_casting_review', backend=get_task_backend(), manager=get_task_manager(), prepare=prepare,
                payload={'candidate_id': candidate_id, 'character_id': name, 'identity_id': identity_id,
                    'references': [r.model_dump(mode='json') for r in references],
                    'agent_route_override': route.model_dump(exclude={'source', 'task_role'}),
                    'display_name': f'选角候选复核 · {name}'})
            result['reference_coverage'] = coverage
        except ValueError as exc:
            conflict(exc)
        return {'ok': True, 'data': result}


@router.post(BASE + '/candidates/{candidate_id}/adopt')
async def adopt(project: str, name: str, candidate_id: str, body: CastingAdoption,
                identity_id: str | None = None, user: dict = Depends(get_api_user)):
    async with scope(project, name, user, identity_id, editor=True) as (ctx, sql, character):
        owned_candidate(ctx, name, identity_id, candidate_id)
        if body.candidate_id != candidate_id:
            raise HTTPException(422, 'candidate ID must match route')
        try:
            result = await adopt_candidate(ctx=ctx, character_id=name, identity_id=identity_id,
                command=body, actor=ctx.requester_user_id, sqlite_store=sql)
        except ValueError as exc:
            raise HTTPException(503 if 'service unavailable' in str(exc) else 409, str(exc)) from exc
        return {'ok': True, 'data': result}
