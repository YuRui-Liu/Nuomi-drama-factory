"""Real files and SQLite exercise the explicit publication boundary."""
import importlib.util
import io
import json
import sqlite3
import subprocess
import sys
from types import SimpleNamespace

import pytest
from PIL import Image

from novelvideo.character_visual.casting_brief import build_casting_revision
from novelvideo.character_visual.casting_compiler import compile_casting_snapshot
from novelvideo.character_visual.casting_models import CastingAdoption, CastingCandidate, CastingReviewReport, CastingFinding
from novelvideo.character_visual.casting_store import CastingCandidateStore
from novelvideo.character_visual.models import CharacterVisualWorkspace
from novelvideo.character_visual.store import CharacterVisualWorkspaceStore
from novelvideo.models import NovelCharacter, CharacterIdentity
from novelvideo.sqlite_store import SQLiteStore
from novelvideo.story_analysis import source_sha256
from tests.character_visual.test_casting_compiler import inputs


def implementation():
    assert importlib.util.find_spec('novelvideo.character_visual.casting_adoption'), 'recoverable adoption missing'
    from novelvideo.character_visual import casting_adoption
    return casting_adoption


def png(color):
    out = io.BytesIO()
    Image.new('RGB', (3, 3), color).save(out, 'PNG')
    return out.getvalue()


@pytest.fixture
async def adoption_env(tmp_path, monkeypatch):
    stores = []
    async def make(identity_id=None, review='completed'):
        from novelvideo.character_visual import casting_service, casting_source
        ctx = SimpleNamespace(project_id='project', output_dir=tmp_path / 'output', state_dir=tmp_path / 'separate-state',
            effective_role='editor', requester_user_id='trusted-user', project_name='project', owner_username='owner')
        sql = SQLiteStore('owner/project', output_dir=str(ctx.output_dir), state_dir=str(ctx.state_dir))
        await sql.initialize()
        stores.append(sql.close)
        await sql.add_character(NovelCharacter(name='甲'))
        await sql.add_character_identity('甲', CharacterIdentity(identity_id='old', character_name='甲', identity_name='老年', appearance_details='保留描述'))
        text = '甲七十岁。'
        db = await sql._ensure_db()
        await db.execute('INSERT INTO episode_sources(episode_number,title,raw_content,content_hash,source_filename,source_revision,imported_at,updated_at) VALUES(?,?,?,?,?,?,?,?)',
            (1, '一', text, source_sha256(text), 'episode.txt', 1, 'now', 'now'))
        await db.commit()
        _, source_revision = await casting_source.load_sources(ctx.output_dir, sql)
        _, proposal, profile = inputs(('甲七十岁。', dict(field='age_range', value='七十岁', source_document='episode:0001',
            source_start=0, source_end=len(text), source_revision=source_revision)))
        workspace = CharacterVisualWorkspace(character_id='甲', profile=profile, design_proposals=[proposal], selected_proposal_id=proposal.proposal_id)
        revision = build_casting_revision(workspace, identity_id, source_revision, '水墨')
        if identity_id:
            workspace.identity_casting_revisions[identity_id] = revision
            workspace.identity_design_proposals[identity_id] = [proposal]
            workspace.identity_selected_proposal_ids[identity_id] = proposal.proposal_id
            workspace.design_proposals = []
            workspace.selected_proposal_id = None
        else:
            workspace.casting_revision = revision
        visual = CharacterVisualWorkspaceStore(ctx.output_dir, state_dir=ctx.state_dir)
        visual.save(workspace)
        snapshot = compile_casting_snapshot(revision, proposal, profile, '水墨')
        candidates = CastingCandidateStore(ctx.output_dir, state_dir=ctx.state_dir, project_id=ctx.project_id)
        candidates.create_pending(CastingCandidate(candidate_id='candidate', project_id=ctx.project_id,
            character_id='甲', identity_id=identity_id, snapshot=snapshot, task_id='generate'))
        candidates.claim_generation('candidate', task_id='generate')
        path = candidates.output_path('candidate')
        path.parent.mkdir(parents=True)
        path.write_bytes(png('red'))
        candidates.complete_generation('candidate', path)
        if review != 'not_started':
            candidates.begin_review('candidate', task_id='review', attempt_id='review-1')
            if review == 'completed':
                candidates.complete_review('candidate', attempt_id='review-1', report=CastingReviewReport(reviewer='ai', model='model', version='v1', findings=[
                    CastingFinding(finding_id='clear', dimension='facts', verdict='conforms', description='符合', visibility='visible')]))
            elif review == 'failed':
                candidates.fail_review('candidate', attempt_id='review-1', error='review_failed')
        monkeypatch.setattr(casting_service, 'resolved_style', lambda ctx: '水墨')
        command = CastingAdoption(candidate_id='candidate', expected_revision=revision.revision_id, idempotency_key='key',
            expected_review_attempt_id=None if review == 'not_started' else 'review-1')
        return SimpleNamespace(ctx=ctx, sql=sql, visual=visual, candidates=candidates, command=command, identity_id=identity_id,
            adopt=lambda command=command: casting_service.adopt_candidate(ctx=ctx, character_id='甲', identity_id=identity_id, command=command, actor='trusted-user', sqlite_store=sql))
    yield make
    for close in stores:
        await close()


@pytest.mark.asyncio
@pytest.mark.parametrize('identity_id', [None, 'old'])
async def test_adopt_publishes_exact_image_bible_workflow_and_scoped_identity(adoption_env, identity_id):
    implementation()
    e = await adoption_env(identity_id)
    from novelvideo.production_workflow import ProductionWorkflowStore
    from novelvideo.production_workflow.slot_ids import character_portrait_slot_id, character_identity_portrait_slot_id
    result = await e.adopt()
    slot_id = character_identity_portrait_slot_id('甲', 'old') if identity_id else character_portrait_slot_id('甲')
    slot, versions = ProductionWorkflowStore(e.ctx.state_dir / 'production_workflow.json').get_slot(slot_id)
    assert slot.current_version_id == result['version_id']
    version = versions[slot.current_version_id]
    assert version.adoption_status == 'adopted'
    assert (e.ctx.output_dir / version.asset_path).read_bytes() == png('red')
    workspace = e.visual.get('甲')
    bible = workspace.identity_visual_bibles['old'] if identity_id else workspace.visual_bible
    assert bible.status == 'confirmed' and bible.confirmed_by == 'trusted-user'
    assert bible.revision_id == e.command.expected_revision
    assert bible.source_fact_ids == ['f0']
    assert version.generation_metadata['casting_adoption']['snapshot']['snapshot_hash'] == e.candidates.get('candidate').snapshot.snapshot_hash
    with sqlite3.connect(e.sql.db_path) as db:
        identities = json.loads(db.execute('SELECT identities_json FROM characters WHERE name=?', ('甲',)).fetchone()[0])
    if identity_id:
        assert identities[0]['appearance_details'] == '保留描述'
        assert (e.ctx.output_dir / identities[0]['portrait_image']).read_bytes() == png('red')
        assert workspace.visual_bible is None
        assert e.sql.get_character('甲').identities[0].portrait_image == identities[0]['portrait_image']
    else:
        assert identities[0]['portrait_image'] == ''
    assert await e.adopt() == result
    assert len(versions) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('status,sentinel', [('not_started', 'review_not_started'), ('failed', 'review_failed')])
async def test_unknown_review_is_advisory_without_ack_or_reason(adoption_env, status, sentinel):
    implementation()
    e = await adoption_env(review=status)
    result = await e.adopt()
    from novelvideo.production_workflow import ProductionWorkflowStore
    _, versions = ProductionWorkflowStore(e.ctx.state_dir / 'production_workflow.json').get_slot('character:甲:portrait')
    assert versions[result['version_id']].qc_passed is False
    assert versions[result['version_id']].soft_issues == [sentinel]


class SimulatedCrash(BaseException):
    pass


@pytest.mark.asyncio
@pytest.mark.parametrize('point', ['prepared', 'current_published', 'bible_saved', 'sql_updated', 'workflow_before_commit'])
async def test_crash_recovers_old_pair_on_visual_read_and_retry(adoption_env, monkeypatch, point):
    m = implementation()
    e = await adoption_env('old')
    from novelvideo.utils.path_resolver import canonical_identity_portrait_path
    canonical = canonical_identity_portrait_path(e.ctx.output_dir, '甲', '老年')
    canonical.parent.mkdir(parents=True, exist_ok=True)
    canonical.write_bytes(png('blue'))
    before = e.visual.get('甲').model_dump(mode='json')
    def crash(at):
        if at == point:
            raise SimulatedCrash(at)
    monkeypatch.setattr(m, '_checkpoint', crash)
    with pytest.raises(SimulatedCrash):
        await e.adopt()
    assert e.visual.get('甲').model_dump(mode='json') == before
    assert canonical.read_bytes() == png('blue')
    with sqlite3.connect(e.sql.db_path) as db:
        identities = json.loads(db.execute('SELECT identities_json FROM characters WHERE name=?', ('甲',)).fetchone()[0])
    assert identities[0]['portrait_image'] == ''
    assert not (e.ctx.state_dir / 'production_workflow.json').exists()
    monkeypatch.setattr(m, '_checkpoint', lambda at: None)
    assert (await e.adopt())['candidate_id'] == 'candidate'


@pytest.mark.asyncio
async def test_stale_snapshot_and_review_attempt_and_reused_key_rejected(adoption_env):
    implementation()
    e = await adoption_env()
    e.candidates.begin_review('candidate', task_id='review-again', attempt_id='review-2')
    assert (await e.adopt())['adoption_status'] == 'adopted'
    e.candidates.fail_review('candidate', attempt_id='review-2', error='review_failed')
    command = e.command.model_copy(update={'idempotency_key': 'after-review', 'expected_review_attempt_id': 'review-2'})
    await e.adopt(command)
    with pytest.raises(ValueError, match='idempotency'):
        await e.adopt(command.model_copy(update={'override_reason': '换了另一个解释，但用了相同请求键'}))
    w = e.visual.get('甲')
    w.profile.biography = '修改资料但未改变 revision ID'
    e.visual.save(w)
    with pytest.raises(ValueError, match='stale|changed'):
        await e.adopt(command.model_copy(update={'idempotency_key': 'new'}))


@pytest.mark.asyncio
async def test_real_process_death_releases_lock_and_recovers_immediately(adoption_env, monkeypatch):
    implementation()
    e = await adoption_env('old')
    script = '''
import asyncio, json, os, sys
from types import SimpleNamespace
from novelvideo.character_visual import casting_service, casting_adoption
casting_service.resolved_style = lambda ctx: '水墨'
casting_adoption._checkpoint = lambda point: os._exit(73) if point == 'sql_updated' else None
ctx = SimpleNamespace(**json.loads(sys.argv[1]))
asyncio.run(casting_service.adopt_candidate(ctx=ctx, character_id='甲', identity_id='old', command=json.loads(sys.argv[2]), actor='trusted-user'))
'''
    data = {k: str(v) if k.endswith('_dir') else v for k, v in vars(e.ctx).items()}
    proc = subprocess.run([sys.executable, '-c', script, json.dumps(data), e.command.model_dump_json()], capture_output=True, timeout=20)
    assert proc.returncode == 73, proc.stderr.decode()
    monkeypatch.setattr(CharacterVisualWorkspaceStore, 'lock_timeout_seconds', .1)
    assert e.visual.get('甲').identity_visual_bibles == {}
    assert (await e.adopt())['adoption_status'] == 'adopted'


@pytest.mark.asyncio
@pytest.mark.parametrize('damage', ['source', 'style', 'proposal', 'image', 'missing', 'project', 'stage', 'actor', 'role'])
async def test_structural_failures_cannot_be_overridden(adoption_env, monkeypatch, damage):
    implementation()
    e = await adoption_env()
    from novelvideo.character_visual import casting_service
    if damage == 'source':
        with sqlite3.connect(e.sql.db_path) as db:
            db.execute("UPDATE episode_sources SET source_revision=2")
    elif damage == 'style':
        monkeypatch.setattr(casting_service, 'resolved_style', lambda ctx: '写实')
    elif damage == 'proposal':
        w = e.visual.get('甲')
        w.design_proposals[0].hair_style = '另一个发型'
        e.visual.save(w)
    elif damage in ('image', 'missing'):
        path = e.candidates.output_path('candidate').with_name('candidate.png')
        if damage == 'image':
            path.write_bytes(png('blue'))
        else:
            path.unlink()
    elif damage == 'project':
        e.ctx.project_id = 'different-project'
    elif damage == 'actor':
        e.ctx.requester_user_id = 'another-user'
    elif damage == 'role':
        e.ctx.effective_role = 'viewer'
    command = e.command.model_copy(update={'override_reason': '这不能越过结构性约束'})
    with pytest.raises(ValueError):
        await casting_service.adopt_candidate(ctx=e.ctx, character_id='甲', identity_id='old' if damage == 'stage' else None,
            command=command, actor='trusted-user')
    assert e.visual.get('甲').visual_bible is None
    assert not (e.ctx.state_dir / 'production_workflow.json').exists()


@pytest.mark.asyncio
async def test_every_deviation_and_unjudgeable_finding_is_retained_without_ack(adoption_env):
    implementation()
    e = await adoption_env(review='running')
    e.candidates.complete_review('candidate', attempt_id='review-1', report=CastingReviewReport(reviewer='ai', model='model', version='v1', findings=[
        CastingFinding(finding_id='deviation', dimension='facts', verdict='deviation', description='偏差'),
        CastingFinding(finding_id='hidden', dimension='design', verdict='unjudgeable', description='不可见')]))
    result = await e.adopt()
    from novelvideo.production_workflow import ProductionWorkflowStore
    _, versions = ProductionWorkflowStore(e.ctx.state_dir / 'production_workflow.json').get_slot('character:甲:portrait')
    v = versions[result['version_id']]
    assert not v.qc_passed and v.soft_issues == ['deviation', 'hidden']
    assert v.generation_metadata['casting_adoption']['review_report']['findings'][0]['verdict'] == 'deviation'


@pytest.mark.asyncio
async def test_committed_recovery_keeps_new_pair_and_media_pins_immutable(adoption_env):
    implementation()
    e = await adoption_env()
    from novelvideo.character_visual.casting_recovery import recover_casting_adoptions, resolve_casting_media_path, assert_legacy_portrait_mutation_allowed
    await e.adopt()
    canonical = e.ctx.output_dir / 'assets/characters/甲/portrait.png'
    pinned = resolve_casting_media_path(e.ctx.output_dir, e.ctx.state_dir, canonical)
    assert pinned != canonical and pinned.read_bytes() == png('red')
    recover_casting_adoptions(e.ctx.output_dir, e.ctx.state_dir)
    assert canonical.read_bytes() == png('red') and e.visual.get('甲').visual_bible.status == 'confirmed'
    canonical.write_bytes(png('blue'))
    assert pinned.read_bytes() == png('red')
    with pytest.raises(ValueError, match='CHARACTER_CASTING_REQUIRED'):
        assert_legacy_portrait_mutation_allowed(e.ctx.output_dir, e.ctx.state_dir, '甲')
    assert_legacy_portrait_mutation_allowed(e.ctx.output_dir, e.ctx.state_dir, '甲', 'old')


@pytest.mark.asyncio
async def test_recovery_preserves_unrelated_fresh_identity_fields(adoption_env, monkeypatch):
    m = implementation()
    e = await adoption_env('old')
    def crash(point):
        if point == 'sql_updated':
            raise SimulatedCrash()
    monkeypatch.setattr(m, '_checkpoint', crash)
    with pytest.raises(SimulatedCrash):
        await e.adopt()
    with sqlite3.connect(e.sql.db_path) as db:
        row = json.loads(db.execute('SELECT identities_json FROM characters WHERE name=?', ('甲',)).fetchone()[0])
        row[0]['costume_image'] = 'later-costume.png'
        db.execute('UPDATE characters SET identities_json=? WHERE name=?', (json.dumps(row), '甲'))
    e.visual.get('甲')
    with sqlite3.connect(e.sql.db_path) as db:
        row = json.loads(db.execute('SELECT identities_json FROM characters WHERE name=?', ('甲',)).fetchone()[0])
    assert row[0]['costume_image'] == 'later-costume.png' and row[0]['portrait_image'] == ''


@pytest.mark.asyncio
async def test_real_http_adoption_updates_current_and_exposes_review_contract(adoption_env, monkeypatch):
    implementation()
    e = await adoption_env('old', review='not_started')
    from fastapi import FastAPI
    import httpx
    from novelvideo.api.auth import get_api_user
    from novelvideo.api.routes import character_casting as api
    async def resolve(*args, **kwargs): return SimpleNamespace(ctx=e.ctx)
    async def make(*args): return e.sql
    async def close(): pass
    monkeypatch.setattr(api, 'resolve_project_scope', resolve)
    monkeypatch.setattr(api, 'make_sqlite_store_for_context', make)
    # The fixture owns this real SQLite connection, so route teardown must leave
    # it alive until the subsequent GET observes the refreshed identity cache.
    monkeypatch.setattr(e.sql, 'close', close)
    monkeypatch.setattr(api, 'resolved_style', lambda ctx: '水墨')
    monkeypatch.setattr(api, 'make_static_url_for_context', lambda ctx, path, **kw: '/static/' + path)
    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[get_api_user] = lambda: {'id': 'untrusted-client-id'}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        base = '/projects/project/characters/甲/casting'
        candidates = await client.get(base + '/candidates?identity_id=old')
        requirements = candidates.json()['data'][0]['adoption_requirements']
        assert requirements['required_acknowledgements'] == []
        body = e.command.model_dump(mode='json')
        response = await client.post(base + '/candidates/candidate/adopt?identity_id=old', json=body)
        assert response.status_code == 200, response.text
        current = await client.get(base + '?identity_id=old')
        assert current.status_code == 200, current.text
        data = current.json()['data']
        assert data['current']['candidate_id'] == 'candidate'
        assert data['identities'][0]['name'] == '老年'
        assert data['current_visual_bible']['confirmed_by'] == 'trusted-user'
        assert e.sql.get_character('甲').identities[0].portrait_image


@pytest.mark.asyncio
async def test_identity_rename_during_preload_rejected(adoption_env, monkeypatch):
    implementation()
    e = await adoption_env('old')
    from novelvideo.character_visual import casting_source
    original = casting_source.load_sources
    async def changing(*args):
        loaded = await original(*args)
        with sqlite3.connect(e.sql.db_path) as db:
            row = json.loads(db.execute('SELECT identities_json FROM characters WHERE name=?', ('甲',)).fetchone()[0])
            row[0]['identity_name'] = '突然改名'
            db.execute('UPDATE characters SET identities_json=? WHERE name=?', (json.dumps(row), '甲'))
        return loaded
    monkeypatch.setattr(casting_source, 'load_sources', changing)
    with pytest.raises(ValueError, match='identity.*changed'):
        await e.adopt()
    assert e.visual.get('甲').identity_visual_bibles == {}


@pytest.mark.asyncio
async def test_stale_public_workspace_save_cannot_overwrite_adopted_bible(adoption_env):
    implementation()
    e = await adoption_env()
    stale = e.visual.get('甲')
    await e.adopt()
    stale.profile.biography = '早先页面上的改动'
    with pytest.raises(ValueError, match='casting.*bible'):
        e.visual.save(stale)
    assert e.visual.get('甲').visual_bible.status == 'confirmed'


@pytest.mark.asyncio
async def test_normal_exception_uses_same_recovery_and_preserves_old_history(adoption_env, monkeypatch):
    m = implementation()
    e = await adoption_env()
    from novelvideo.production_workflow import ProductionWorkflowStore
    from novelvideo.character_visual.casting_recovery import recover_casting_adoptions
    canonical = e.ctx.output_dir / 'assets/characters/甲/portrait.png'
    canonical.parent.mkdir(parents=True, exist_ok=True)
    canonical.write_bytes(png('blue'))
    def fail(point):
        if point == 'workflow_before_commit':
            raise RuntimeError('injected disk failure')
    monkeypatch.setattr(m, '_checkpoint', fail)
    with pytest.raises(RuntimeError, match='disk failure'):
        await e.adopt()
    assert canonical.read_bytes() == png('blue')
    assert e.visual.get('甲').visual_bible is None
    recover_casting_adoptions(e.ctx.output_dir, e.ctx.state_dir)
    monkeypatch.setattr(m, '_checkpoint', lambda at: None)
    await e.adopt()
    _, versions = ProductionWorkflowStore(e.ctx.state_dir / 'production_workflow.json').get_slot('character:甲:portrait')
    old = next(v for v in versions.values() if v.adoption_status == 'superseded')
    assert (e.ctx.output_dir / old.asset_path).read_bytes() == png('blue')


@pytest.mark.asyncio
async def test_journal_path_injection_and_symlink_asset_rejected(adoption_env, monkeypatch, tmp_path):
    m = implementation()
    e = await adoption_env()
    from novelvideo.character_visual.casting_recovery import recover_casting_adoptions, read_journal, write_journal
    def crash(point):
        if point == 'prepared':
            raise SimulatedCrash()
    monkeypatch.setattr(m, '_checkpoint', crash)
    with pytest.raises(SimulatedCrash):
        await e.adopt()
    journal = read_journal(e.ctx.state_dir)
    entry = next(iter(journal['entries'].values()))
    original = entry['canonical_path']
    entry['canonical_path'] = '../../outside.png'
    write_journal(e.ctx.state_dir, journal)
    with pytest.raises(ValueError, match='path mismatch'):
        recover_casting_adoptions(e.ctx.output_dir, e.ctx.state_dir)
    assert not (tmp_path / 'outside.png').exists()
    entry['canonical_path'] = original
    write_journal(e.ctx.state_dir, journal)
    recover_casting_adoptions(e.ctx.output_dir, e.ctx.state_dir)
    image = e.candidates.output_path('candidate').with_name('candidate.png')
    alternate = tmp_path / 'alternate.png'
    alternate.write_bytes(image.read_bytes())
    image.unlink()
    image.symlink_to(alternate)
    with pytest.raises(ValueError, match='symlink'):
        await e.adopt()


@pytest.mark.asyncio
async def test_limitation_marker_protects_slot_before_revision_exists(adoption_env):
    implementation()
    e = await adoption_env()
    workspace = e.visual.get('甲')
    workspace.casting_revision = None
    workspace.casting_limitation_reasons = {'base': '等待重新选角', 'old': ''}
    e.visual.save(workspace)
    from novelvideo.character_visual.casting_recovery import assert_legacy_portrait_mutation_allowed
    for identity_id in (None, 'old'):
        with pytest.raises(ValueError, match='CHARACTER_CASTING_REQUIRED'):
            assert_legacy_portrait_mutation_allowed(e.ctx.output_dir, e.ctx.state_dir, '甲', identity_id)


@pytest.mark.asyncio
async def test_recovery_rejects_wrong_output_root_and_invalid_journal_state(adoption_env, monkeypatch, tmp_path):
    m = implementation()
    e = await adoption_env()
    from novelvideo.character_visual.casting_recovery import recover_casting_adoptions, read_journal, write_journal
    def crash(point):
        if point == 'prepared':
            raise SimulatedCrash()
    monkeypatch.setattr(m, '_checkpoint', crash)
    with pytest.raises(SimulatedCrash):
        await e.adopt()
    with pytest.raises(ValueError, match='root.*mismatch'):
        recover_casting_adoptions(tmp_path / 'different-output', e.ctx.state_dir)
    assert not (tmp_path / 'different-output').exists()
    journal = read_journal(e.ctx.state_dir)
    next(iter(journal['entries'].values()))['status'] = 'corrupt-state'
    write_journal(e.ctx.state_dir, journal)
    with pytest.raises(ValueError, match='journal'):
        recover_casting_adoptions(e.ctx.output_dir, e.ctx.state_dir)


@pytest.mark.asyncio
@pytest.mark.parametrize('identity_id', [None, 'old'])
async def test_actual_path_guard_protects_canonical_candidates_and_history(adoption_env, identity_id):
    e = await adoption_env(identity_id)
    from novelvideo.character_visual import casting_recovery as r
    assert hasattr(r, 'assert_casting_path_mutation_allowed'), 'casting path protection missing'
    def guard(path):
        return r.assert_casting_path_mutation_allowed(e.ctx.output_dir, e.ctx.state_dir, path)
    canonical = r.canonical_portrait_path(e.ctx.output_dir, '甲', identity_id, '老年' if identity_id else None)
    for path in (canonical, e.candidates.output_path('candidate').with_name('candidate.png')):
        with pytest.raises(ValueError, match='CHARACTER_CASTING_REQUIRED'):
            guard(path)
    if identity_id:
        legacy = canonical.with_name('老年_portrait.png')
        legacy.parent.mkdir(parents=True, exist_ok=True)
        legacy.write_bytes(png('blue'))
        with pytest.raises(ValueError, match='CHARACTER_CASTING_REQUIRED'):
            guard(legacy)
    guard(e.ctx.output_dir / 'assets/characters/甲/identities/老年.png')
    guard(e.ctx.output_dir / 'assets/characters/甲/identities/老年_costume.png')
    await e.adopt()
    entry = next(iter(r.read_journal(e.ctx.state_dir)['entries'].values()))
    with pytest.raises(ValueError, match='CHARACTER_CASTING_REQUIRED'):
        guard(e.ctx.output_dir / entry['immutable_path'])


@pytest.mark.asyncio
async def test_get_cannot_mix_old_bible_with_concurrently_adopted_current(adoption_env, monkeypatch):
    e = await adoption_env()
    from fastapi import FastAPI
    import httpx
    from novelvideo.api.auth import get_api_user
    from novelvideo.api.routes import character_casting as api
    async def resolve(*args, **kwargs): return SimpleNamespace(ctx=e.ctx)
    async def make(*args): return e.sql
    async def close(): pass
    original_sources = api.load_sources
    async def interleave(*args):
        await e.adopt()
        return await original_sources(*args)
    monkeypatch.setattr(api, 'load_sources', interleave)
    monkeypatch.setattr(api, 'resolve_project_scope', resolve)
    monkeypatch.setattr(api, 'make_sqlite_store_for_context', make)
    monkeypatch.setattr(e.sql, 'close', close)
    monkeypatch.setattr(api, 'resolved_style', lambda ctx: '水墨')
    monkeypatch.setattr(api, 'make_static_url_for_context', lambda ctx, path, **kw: '/static/' + path)
    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[get_api_user] = lambda: {}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        response = await client.get('/projects/project/characters/甲/casting')
    assert response.status_code == 200, response.text
    data = response.json()['data']
    assert data['current']['candidate_id'] == 'candidate'
    assert data['current_visual_bible'] is not None, 'GET mixed old bible and newly committed current'
    assert data['current_visual_bible']['revision_id'] == e.command.expected_revision


@pytest.mark.asyncio
@pytest.mark.parametrize('names', [('老年', '甲_老年'), ('老/年', '老:年')])
@pytest.mark.parametrize('existing', ['unadopted', 'adopted', 'journal_only'])
async def test_adoption_rejects_identity_canonical_path_collision(adoption_env, names, existing):
    e = await adoption_env('old')
    from novelvideo.character_visual import casting_service, casting_recovery
    with sqlite3.connect(e.sql.db_path) as db:
        row = json.loads(db.execute('SELECT identities_json FROM characters WHERE name=?', ('甲',)).fetchone()[0])
        row[0]['identity_name'] = names[0]
        db.execute('UPDATE characters SET identities_json=? WHERE name=?', (json.dumps(row), '甲'))
    if existing != 'unadopted':
        await e.adopt()
    # Add a genuinely valid second casting stage after the first was adopted.
    # Character-prefix stripping and invalid-character replacement can both
    # collapse distinct identity names to the exact same canonical file.
    with sqlite3.connect(e.sql.db_path) as db:
        row = json.loads(db.execute('SELECT identities_json FROM characters WHERE name=?', ('甲',)).fetchone()[0])
        collision = CharacterIdentity(character_name='甲', identity_id='collision', identity_name=names[1]).model_dump(mode='json')
        row = [collision] if existing == 'journal_only' else [*row, collision]
        db.execute('UPDATE characters SET identities_json=? WHERE name=?', (json.dumps(row), '甲'))
    w = e.visual.get('甲')
    proposal = w.identity_design_proposals['old'][0]
    draft = CharacterVisualWorkspace(character_id='甲', profile=w.profile, design_proposals=[proposal], selected_proposal_id=proposal.proposal_id)
    source_revision = e.candidates.get('candidate').snapshot.source_revision
    revision = build_casting_revision(draft, 'collision', source_revision, '水墨')
    w.identity_casting_revisions['collision'] = revision
    w.identity_design_proposals['collision'] = [proposal]
    w.identity_selected_proposal_ids['collision'] = proposal.proposal_id
    e.visual.save(w)
    snapshot = compile_casting_snapshot(revision, proposal, w.profile, '水墨')
    e.candidates.create_pending(CastingCandidate(candidate_id='collision', project_id=e.ctx.project_id,
        character_id='甲', identity_id='collision', snapshot=snapshot, task_id='generate-collision'))
    e.candidates.claim_generation('collision', task_id='generate-collision')
    source = e.candidates.output_path('collision')
    source.parent.mkdir(parents=True)
    source.write_bytes(png('blue'))
    e.candidates.complete_generation('collision', source)
    e.candidates.begin_review('collision', task_id='review-collision', attempt_id='review-collision')
    e.candidates.complete_review('collision', attempt_id='review-collision', report=e.candidates.get('candidate').report)
    canonical = casting_recovery.canonical_portrait_path(e.ctx.output_dir, '甲', 'old', names[0])
    assert canonical == casting_recovery.canonical_portrait_path(e.ctx.output_dir, '甲', 'collision', names[1])
    before_image = canonical.read_bytes() if canonical.exists() else None
    before_workspace = e.visual.path.read_bytes()
    workflow = e.ctx.state_dir / 'production_workflow.json'
    before_workflow = workflow.read_bytes() if workflow.exists() else None
    command = CastingAdoption(candidate_id='collision', expected_revision=revision.revision_id, idempotency_key='collision', expected_review_attempt_id='review-collision')
    with pytest.raises(ValueError, match='portrait path.*another.*scope'):
        await casting_service.adopt_candidate(ctx=e.ctx, character_id='甲', identity_id='collision', command=command, actor='trusted-user', sqlite_store=e.sql)
    assert (canonical.read_bytes() if canonical.exists() else None) == before_image
    assert e.visual.path.read_bytes() == before_workspace
    assert (workflow.read_bytes() if workflow.exists() else None) == before_workflow
