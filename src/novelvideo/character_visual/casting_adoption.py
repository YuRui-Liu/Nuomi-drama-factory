"""Explicit, journalled publication of an immutable portrait and matching bible."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import hashlib

from novelvideo.production_workflow import ProductionWorkflowStore, production_workflow_project_lock
from novelvideo.production_workflow.slot_ids import character_portrait_slot_id, character_identity_portrait_slot_id
from .casting_compiler import snapshot_digest
from .casting_models import CastingAdoption
from .casting_store import CastingCandidateStore
from .models import CharacterVisualBible, CharacterVisualWorkspace
from .store import CharacterVisualWorkspaceStore
from . import casting_recovery as recovery


def _checkpoint(point):
    """Transaction fault-injection seam, with no production side effects."""


def adoption_requirements(candidate):
    """The UI renders exactly the findings/sentinels the server will require."""
    blocked = None
    if candidate.generation_status != 'succeeded':
        blocked = 'generation_not_succeeded'
    elif candidate.review_status == 'running':
        blocked = 'review_running'
    if candidate.review_status == 'completed':
        required = [f.finding_id for f in candidate.report.findings if f.verdict != 'conforms']
        if not candidate.report.findings:
            required = ['review_unjudgeable']
    elif candidate.review_status == 'running':
        required = []
    else:
        required = ['review_failed' if candidate.review_status == 'failed' else 'review_not_started']
    return {'expected_review_attempt_id': candidate.review_attempt_id, 'required_acknowledgements': required,
        'override_reason_required': bool(required), 'blocked_reason': blocked}


def _review_decision(candidate, command):
    if candidate.review_attempt_id != command.expected_review_attempt_id:
        raise ValueError('review attempt changed; reload candidate before adoption')
    if candidate.review_status == 'running':
        raise ValueError('review is running; wait before adoption')
    if candidate.review_status == 'completed':
        attempt = next((a for a in candidate.review_attempts if a.attempt_id == candidate.review_attempt_id), None)
        if attempt is None or attempt.status != 'completed' or attempt.report != candidate.report:
            raise ValueError('review attempt report mismatch')
        warnings = [f.finding_id for f in candidate.report.findings if f.verdict != 'conforms']
        if not candidate.report.findings:
            warnings = ['review_unjudgeable']
        allowed = {f.finding_id for f in candidate.report.findings} | set(warnings)
    else:
        warnings = ['review_failed' if candidate.review_status == 'failed' else 'review_not_started']
        allowed = set(warnings)
        if candidate.review_status == 'failed':
            attempt = next((a for a in candidate.review_attempts if a.attempt_id == candidate.review_attempt_id), None)
            if attempt is None or attempt.status != 'failed':
                raise ValueError('review attempt failure mismatch')
    acknowledged = set(command.acknowledged_findings)
    if not acknowledged.issubset(allowed):
        raise ValueError('acknowledged findings do not belong to this review')
    if warnings and (not set(warnings).issubset(acknowledged) or not str(command.override_reason or '').strip()):
        raise ValueError('acknowledge every review warning and supply an explicit override reason')
    return warnings


def _binding(ctx, character_id, identity_id, command, actor):
    scope = {'project_id': ctx.project_id, 'character_id': character_id, 'identity_id': identity_id}
    key = snapshot_digest({**scope, 'idempotency_key': command.idempotency_key})
    digest = snapshot_digest({**scope, 'actor': actor, 'command': command.model_dump(mode='json')})
    return key, digest


def _verified_asset(store, candidate_id):
    try:
        return store.read_verified_asset(candidate_id)
    except OSError as exc:
        raise ValueError('candidate asset unavailable; generate a new candidate') from exc


def _replay(journal, key, digest):
    previous = journal['entries'].get(key)
    if previous:
        if previous['command_hash'] != digest:
            raise ValueError('idempotency key is already bound to a different adoption command')
        if previous['status'] != 'committed':
            raise ValueError('unfinished casting adoption requires recovery')
        return previous['result']
    return None


def _archive_old_current(workflow, root, canonical, slot_id, previous_bytes):
    """Freeze old mutable references before replacing the compatibility mirror."""
    try:
        slot, versions = workflow.get_slot(slot_id)
    except KeyError:
        slot, versions = None, {}
    if slot and slot.asset_kind != 'character_portrait':
        raise ValueError('incompatible portrait workflow slot')
    mutable = [v.version_id for v in versions.values()
        if recovery.controlled_path(root, v.asset_path) == canonical]
    if previous_bytes is None:
        if mutable:
            raise ValueError('old current portrait is missing; cannot preserve history')
        return
    old = canonical.parent / 'portrait_versions' / ('legacy-' + hashlib.sha256(previous_bytes).hexdigest() + '.png')
    recovery.controlled_path(root, old.relative_to(root))
    if old.exists() and old.read_bytes() != previous_bytes:
        raise ValueError('immutable old portrait history mismatch')
    if not old.exists():
        recovery.durable_write(old, previous_bytes)
    if mutable:
        workflow.retarget_version_asset_paths(slot_id=slot_id, version_ids=tuple(mutable), asset_path=old.relative_to(root).as_posix())
    if slot is None:
        workflow.materialize_legacy_current(slot_id=slot_id, asset_kind='character_portrait', asset_path=old.relative_to(root).as_posix())


async def adopt_candidate(*, ctx, character_id, identity_id=None, command, actor, sqlite_store=None):
    from . import casting_service, casting_source
    from novelvideo.sqlite_store import SQLiteStore

    command = CastingAdoption.model_validate(command)
    if getattr(ctx, 'effective_role', None) not in {'editor', 'owner', 'admin'}:
        raise ValueError('editor permission required for casting adoption')
    if not actor or actor != getattr(ctx, 'requester_user_id', None):
        raise ValueError('casting actor must match authenticated requester')
    root, state = Path(ctx.output_dir).absolute(), Path(ctx.state_dir).absolute()
    key, command_hash = _binding(ctx, character_id, identity_id, command, actor)
    with production_workflow_project_lock(state):
        recovery.recover_casting_adoptions(root, state)
        replay = _replay(recovery.read_journal(state), key, command_hash)
        if replay is not None:
            return replay
        candidate_store = CastingCandidateStore(root, state_dir=state, project_id=ctx.project_id)
        candidate = candidate_store.get(command.candidate_id)
        if candidate is None or (candidate.character_id, candidate.identity_id) != (character_id, identity_id):
            raise ValueError('candidate ownership mismatch')
        _verified_asset(candidate_store, command.candidate_id)
        _review_decision(candidate, command)
        initial_identity = recovery.identity_record(state, character_id, identity_id)
    # Source imports and cache initialization may await. Never hold a project/file
    # lock here; all inputs are synchronously revalidated inside the transaction.
    own_store = sqlite_store is None
    sql = sqlite_store or SQLiteStore(f'{ctx.owner_username}/{ctx.project_name}', output_dir=str(root), state_dir=str(state))
    try:
        if own_store:
            await sql.initialize()
            await sql.load_graph_state()
        if Path(sql.db_path).absolute() != state / 'data.db':
            raise ValueError('casting database scope mismatch')
        documents, source_revision = await casting_source.load_sources(root, sql)
        style = casting_service.resolved_style(ctx)
        visual = CharacterVisualWorkspaceStore(root, state_dir=state)
        with production_workflow_project_lock(state):
            recovery.recover_casting_adoptions(root, state)
            journal = recovery.read_journal(state)
            replay = _replay(journal, key, command_hash)
            if replay is not None:
                return replay
            with visual._exclusive_write_lock():
                casting_source.assert_live_sources(ctx, sql, documents, character_id, identity_id)
                if casting_service.resolved_style(ctx) != style:
                    raise ValueError('style changed during casting adoption')
                payload = visual._read_all()
                workspace = CharacterVisualWorkspace.model_validate(payload[character_id])
                if workspace.character_id != character_id or workspace.profile.character_id != character_id or workspace.profile.name != character_id:
                    raise ValueError('workspace character ownership mismatch')
                draft = casting_service.stage_workspace(workspace, identity_id)
                names = casting_source.attested_names(workspace.profile, documents)
                for fact in draft.profile.facts:
                    casting_source.verified_fact(fact, documents, names, source_revision, strict=True)
                live = casting_service.compile_current(workspace, identity_id, command.expected_revision, source_revision, style)
                candidate = candidate_store.get(command.candidate_id)
                if candidate is None or (candidate.character_id, candidate.identity_id) != (character_id, identity_id):
                    raise ValueError('candidate ownership mismatch')
                if candidate.snapshot.model_dump(mode='json') != live.model_dump(mode='json'):
                    raise ValueError('candidate snapshot changed; generate a new candidate')
                image_bytes = _verified_asset(candidate_store, command.candidate_id)
                warnings = _review_decision(candidate, command)
                identity = recovery.identity_record(state, character_id, identity_id)
                if identity is not None and identity.get('identity_name') != initial_identity.get('identity_name'):
                    raise ValueError('identity name changed during adoption')
                identity_name = identity['identity_name'] if identity else None
                canonical = recovery.canonical_portrait_path(root, character_id, identity_id, identity_name)
                recovery.assert_portrait_scope_path_available(root, state, character_id, identity_id, canonical)
                immutable = recovery.controlled_path(root, Path('assets') / 'characters' / character_id / 'portrait_versions' / ('casting-' + key + '.png'))
                if immutable.exists() and immutable.read_bytes() != image_bytes:
                    raise ValueError('immutable adopted portrait mismatch')
                bible_fields = set(CharacterVisualBible.model_fields) - {'character_id', 'revision_id', 'status', 'source_fact_ids', 'confirmed_by'}
                bible = CharacterVisualBible(**{k: v for k, v in candidate.snapshot.proposal_snapshot.items() if k in bible_fields},
                    character_id=character_id, revision_id=candidate.snapshot.revision_id, status='confirmed',
                    source_fact_ids=candidate.snapshot.source_fact_ids, confirmed_by=actor)
                if identity_id is None:
                    workspace.visual_bible = bible
                else:
                    workspace.identity_visual_bibles[identity_id] = bible
                payload[character_id] = workspace.model_dump(mode='json')
                slot_id = character_portrait_slot_id(character_id) if identity_id is None else character_identity_portrait_slot_id(character_id, identity_id)
                workflow_path = recovery.controlled_path(state, 'production_workflow.json')
                workflow = ProductionWorkflowStore(workflow_path)
                if workflow.read_only_reason:
                    raise ValueError(workflow.read_only_reason)
                at = datetime.now(timezone.utc)
                version_id = 'casting-' + key
                result = {'candidate_id': candidate.candidate_id, 'version_id': version_id, 'revision_id': bible.revision_id,
                    'identity_id': identity_id, 'snapshot_hash': candidate.snapshot.snapshot_hash, 'adoption_status': 'adopted'}
                audit = {'actor': actor, 'candidate_id': candidate.candidate_id, 'command_hash': command_hash,
                    'snapshot': candidate.snapshot.model_dump(mode='json'), 'asset_sha256': candidate.asset_sha256,
                    'review_status': candidate.review_status, 'review_attempt_id': candidate.review_attempt_id,
                    'review_report': candidate.report.model_dump(mode='json') if candidate.report else None,
                    'review_attempt': next((a.model_dump(mode='json') for a in candidate.review_attempts if a.attempt_id == candidate.review_attempt_id), None),
                    'warnings': warnings, 'acknowledged_findings': command.acknowledged_findings,
                    'override_reason': command.override_reason, 'at': at.isoformat()}
                entry = {'status': 'prepared', 'project_id': ctx.project_id, 'character_id': character_id,
                    'identity_id': identity_id, 'identity_name': identity_name, 'command_hash': command_hash,
                    'canonical_path': canonical.relative_to(root).as_posix(), 'immutable_path': immutable.relative_to(root).as_posix(),
                    'result': result, 'audit': audit,
                    'before': {'canonical': recovery.capture(canonical), 'workspace': recovery.capture(visual.path),
                        'workflow': recovery.capture(workflow_path), 'identity_portrait': identity.get('portrait_image', '') if identity else None}}
                journal['entries'][key] = entry
                journal.update(project_dir=str(root), state_dir=str(state))
                recovery.write_journal(state, journal)
                try:
                    _checkpoint('prepared')
                    previous_bytes = canonical.read_bytes() if canonical.exists() else None
                    _archive_old_current(workflow, root, canonical, slot_id, previous_bytes)
                    if not immutable.exists():
                        recovery.durable_write(immutable, image_bytes)
                    recovery.durable_write(canonical, image_bytes)
                    _checkpoint('current_published')
                    recovery.durable_write(visual.path, recovery.json_bytes(payload))
                    _checkpoint('bible_saved')
                    if identity_id is not None:
                        recovery.update_identity_portrait_reference(root, state, character_id, identity_id, str(canonical))
                    _checkpoint('sql_updated')
                    workflow.register_candidate_version(slot_id=slot_id, asset_kind='character_portrait', version_id=version_id,
                        asset_path=immutable.relative_to(root).as_posix(), source_attempt_id=candidate.task_id,
                        qc_passed=candidate.review_status == 'completed' and not warnings, soft_issues=warnings,
                        generation_metadata={'canonical_path': entry['canonical_path'], 'candidate_id': candidate.candidate_id, 'casting_adoption': audit},
                        actor=actor, at=at, auto_provisional=False)
                    workflow.adopt_casting_version(slot_id=slot_id, version_id=version_id, actor=actor, at=at)
                    recovery.durable_write(workflow_path, workflow_path.read_bytes())
                    _checkpoint('workflow_before_commit')
                    entry['status'] = 'committed'
                    entry.pop('before')
                    recovery.write_journal(state, journal)
                except Exception:
                    # Same durable recovery path as after process death. It only
                    # uses raw files/SQL, so it cannot recurse into this file lock.
                    recovery.recover_casting_adoptions(root, state)
                    raise
        await sql.load_graph_state()
        return result
    finally:
        if own_store:
            await sql.close()
