import importlib.util
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI, HTTPException

from novelvideo.character_visual.models import CharacterVisualWorkspace
from novelvideo.character_visual.store import CharacterVisualWorkspaceStore
from tests.character_visual.test_casting_compiler import inputs


@pytest.fixture
def setup_casting(tmp_path, monkeypatch):
    assert importlib.util.find_spec('novelvideo.api.routes.character_casting'), 'casting routes missing'
    from novelvideo.api.routes import character_casting as api
    from novelvideo.api.auth import get_api_user
    revision, proposal, profile = inputs()
    ctx = SimpleNamespace(project_id='p', output_dir=tmp_path / 'out', state_dir=tmp_path / 'state',
        effective_role='editor', requester_user_id='server-actor', requester_username='editor',
        owner_username='owner', project_name='p')
    character = SimpleNamespace(name=profile.name, identities=[], image_path=None)
    async def resolve(project, user, required_role='viewer'):
        if project != 'p':
            raise HTTPException(404)
        if required_role == 'editor' and ctx.effective_role == 'viewer':
            raise HTTPException(403)
        return SimpleNamespace(ctx=ctx)
    async def make(ctx):
        return SimpleNamespace(get_character=lambda name: character if name == character.name else None, close=close)
    async def close(): pass
    async def sources(*args): return {}, 'source1'
    monkeypatch.setattr(api, 'resolve_project_scope', resolve)
    monkeypatch.setattr(api, 'make_sqlite_store_for_context', make)
    monkeypatch.setattr(api, 'load_sources', sources)
    monkeypatch.setattr(api, 'resolved_style', lambda ctx: '水墨')
    monkeypatch.setattr(api, 'freeze_image_model', lambda ctx, requested: ('nano-banana-pro', {'requested_model': requested or 'nano-banana-pro'}))
    monkeypatch.setattr(api, 'resolve_configured_agent_task_route', lambda **kw: SimpleNamespace(model_dump=lambda **kw: {}))
    manager = SimpleNamespace(get_task_for_project=lambda *args, **kw: None)
    monkeypatch.setattr(api, 'get_task_manager', lambda: manager)
    queued = []
    async def enqueue(ctx, **kw):
        queued.append(kw)
        return SimpleNamespace(task_state=SimpleNamespace(task_id='task-' + str(len(queued)), status='queued'))
    monkeypatch.setattr(api, 'get_task_backend', lambda: SimpleNamespace(enqueue_project_task=enqueue))
    store = CharacterVisualWorkspaceStore(ctx.output_dir, state_dir=ctx.state_dir)
    store.save(CharacterVisualWorkspace(character_id=profile.character_id, profile=profile,
        design_proposals=[proposal], selected_proposal_id=proposal.proposal_id, casting_revision=revision))
    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[get_api_user] = lambda: {'id': 'client-ignored'}
    return SimpleNamespace(api=api, app=app, ctx=ctx, store=store, revision=revision, proposal=proposal,
        queued=queued, monkeypatch=monkeypatch, base='/projects/p/characters/甲/casting')


async def request(env, method, suffix='', **kw):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=env.app), base_url='http://test') as client:
        return await client.request(method, env.base + suffix, **kw)


@pytest.mark.asyncio
async def test_read_does_not_enqueue_and_exposes_permissions(setup_casting):
    e = setup_casting
    e.ctx.effective_role = 'viewer'
    response = await request(e, 'GET')
    assert response.status_code == 200
    assert response.json()['data']['can_edit'] is False
    assert response.json()['data']['selected_proposal_id'] == e.proposal.proposal_id
    assert e.queued == []


@pytest.mark.asyncio
@pytest.mark.parametrize('suffix,body', [('/recast', {'idempotency_key': 'k'}),
    ('/candidates', {'idempotency_key': 'k', 'expected_revision': 'v'}),
    ('/candidates/c/review', {'idempotency_key': 'k'}),
    ('/candidates/c/adopt', {'candidate_id': 'c', 'idempotency_key': 'k', 'expected_revision': 'v'})])
async def test_all_mutations_require_editor(setup_casting, suffix, body):
    e = setup_casting
    e.ctx.effective_role = 'viewer'
    assert (await request(e, 'POST', suffix, json=body)).status_code == 403
    assert (await request(e, 'PATCH', json={'expected_revision': 'v', 'selected_proposal_id': 'x'})).status_code == 403


@pytest.mark.asyncio
async def test_scope_conflict_and_invalid_decision(setup_casting):
    e = setup_casting
    assert (await request(e, 'GET', '?identity_id=other')).status_code == 404
    assert (await request(e, 'PATCH', json={'expected_revision': 'old', 'selected_proposal_id': e.proposal.proposal_id})).status_code == 409
    bad = e.proposal.model_dump()
    bad['casting_decisions'][0]['fact_ids'] = ['fake']
    assert (await request(e, 'PATCH', json={'expected_revision': e.revision.revision_id,
        'selected_proposal_id': e.proposal.proposal_id, 'proposals': [bad]})).status_code == 422


@pytest.mark.asyncio
async def test_generate_key_is_durable_and_current_unchanged(setup_casting):
    e = setup_casting
    before = e.store.get('甲').model_dump()
    body = {'idempotency_key': 'k', 'expected_revision': e.revision.revision_id}
    first = await request(e, 'POST', '/candidates', json=body)
    second = await request(e, 'POST', '/candidates', json=body)
    assert first.status_code == second.status_code == 202
    assert first.json()['data']['candidate_id'] == second.json()['data']['candidate_id']
    assert len(e.queued) == 1
    rows = (await request(e, 'GET', '/candidates')).json()['data']
    assert len(rows) == 1 and rows[0]['stale'] is False
    assert 'asset_path' not in rows[0]
    assert rows[0]['requested_model'] == 'nano-banana-pro'
    assert e.store.get('甲').model_dump() == before


@pytest.mark.asyncio
async def test_adoption_boundary_uses_server_actor_and_fail_closed(setup_casting):
    e = setup_casting
    body = {'idempotency_key': 'k', 'expected_revision': e.revision.revision_id}
    generated = (await request(e, 'POST', '/candidates', json=body)).json()['data']['candidate_id']
    body.update(candidate_id=generated)
    assert (await request(e, 'POST', f'/candidates/{generated}/adopt', json=body)).status_code == 503
    calls = []
    async def adopt(**kwargs):
        calls.append(kwargs)
        return {'candidate_id': generated}
    e.monkeypatch.setattr(e.api, 'adopt_candidate', adopt)
    response = await request(e, 'POST', f'/candidates/{generated}/adopt', json=body)
    assert response.status_code == 200
    assert calls[0]['actor'] == 'server-actor'
    assert calls[0]['identity_id'] is None


@pytest.mark.asyncio
async def test_legacy_workspace_cannot_bypass_casting_selection(setup_casting):
    from novelvideo.api.routes import characters
    from novelvideo.api.schemas import CharacterVisualWorkspaceUpdate, CharacterVisualBibleConfirmRequest
    e = setup_casting
    async def resolve(*a, **kw):
        return e.ctx, 'owner', 'p', e.ctx.output_dir, str(e.ctx.output_dir), SimpleNamespace(get_character=lambda n: SimpleNamespace(name=n))
    e.monkeypatch.setattr(characters, '_resolve_character_project', resolve)
    update = await characters.update_character_visual_workspace('p', '甲', CharacterVisualWorkspaceUpdate(), {})
    assert update.status_code == 409
    confirm = await characters.confirm_character_visual_bible('p', '甲', CharacterVisualBibleConfirmRequest(confirmed_by='client'), {})
    assert confirm.status_code == 409 and b'CHARACTER_CASTING_REQUIRED' in confirm.body


@pytest.mark.asyncio
async def test_generation_runner_may_bind_before_enqueue_returns(setup_casting):
    e = setup_casting
    from novelvideo.character_visual.casting_store import CastingCandidateStore
    store = CastingCandidateStore(e.ctx.output_dir, state_dir=e.ctx.state_dir, project_id='p')
    async def enqueue(ctx, **kw):
        e.queued.append(kw)
        payload = kw['payload']
        store.bind_generation_task(payload['candidate_id'], submission_token=payload['submission_token'], task_id='actual')
        return SimpleNamespace(task_state=SimpleNamespace(task_id='actual', status='running'))
    e.monkeypatch.setattr(e.api, 'get_task_backend', lambda: SimpleNamespace(enqueue_project_task=enqueue))
    body = {'idempotency_key': 'fast-runner', 'expected_revision': e.revision.revision_id}
    response = await request(e, 'POST', '/candidates', json=body)
    cid = response.json()['data']['candidate_id']
    assert store.get(cid).task_id == 'actual'
    assert (await request(e, 'POST', '/candidates', json=body)).json()['data']['candidate_id'] == cid
    assert len(e.queued) == 1


@pytest.mark.asyncio
async def test_candidate_cross_character_and_stage_are_404(setup_casting):
    from tests.character_visual.test_casting_store import pending
    e = setup_casting
    other = pending().model_copy(update={'project_id': 'p', 'identity_id': 'other',
        'snapshot': pending().snapshot.model_copy(update={'identity_id': 'other'})})
    from novelvideo.character_visual.casting_compiler import snapshot_digest
    other.snapshot = other.snapshot.model_copy(update={'snapshot_hash': snapshot_digest(other.snapshot.model_dump(mode='json', exclude={'snapshot_hash'}))})
    e.api.candidate_store(e.ctx).create_pending(other)
    assert (await request(e, 'POST', '/candidates/c1/review', json={'idempotency_key': 'k'})).status_code == 404


@pytest.mark.asyncio
async def test_review_retry_never_generates_and_refs_are_server_paths(setup_casting):
    from PIL import Image
    e = setup_casting
    body = {'idempotency_key': 'gen', 'expected_revision': e.revision.revision_id}
    cid = (await request(e, 'POST', '/candidates', json=body)).json()['data']['candidate_id']
    store = e.api.candidate_store(e.ctx)
    candidate = store.get(cid)
    store.bind_generation_task(cid, submission_token=candidate.submission_token, task_id='actual')
    store.claim_generation(cid, task_id='actual')
    image = store.output_path(cid)
    image.parent.mkdir(parents=True, exist_ok=True)
    Image.new('RGB', (8, 8)).save(image)
    store.complete_generation(cid, image)
    first = await request(e, 'POST', f'/candidates/{cid}/review', json={'idempotency_key': 'r1', 'references': []})
    replay = await request(e, 'POST', f'/candidates/{cid}/review', json={'idempotency_key': 'r1', 'references': []})
    second = await request(e, 'POST', f'/candidates/{cid}/review', json={'idempotency_key': 'r2', 'references': []})
    assert first.status_code == replay.status_code == second.status_code == 202
    assert first.json()['data']['attempt_id'] == replay.json()['data']['attempt_id']
    assert first.json()['data']['attempt_id'] != second.json()['data']['attempt_id']
    assert [q['task_type'] for q in e.queued] == ['character_portrait', 'character_casting_review', 'character_casting_review']
    assert (await request(e, 'POST', f'/candidates/{cid}/review', json={'idempotency_key': 'r3',
        'references': [{'character_id': '乙', 'asset_path': '/tmp/private.png'}]})).status_code == 422


def test_automatic_references_are_fixed_bounded_and_relationship_grounded(setup_casting):
    from novelvideo.character_visual.casting_source import SourceDocument
    from novelvideo.character_visual.models import CharacterNarrativeFact, SourceSpan
    from tests.character_visual.test_casting_store import pending
    from PIL import Image
    e = setup_casting
    names = ['乙', *[f'角色{i}' for i in range(8)]]
    rows = [SimpleNamespace(name=n, identities=[]) for n in ['甲', *names]]
    sql = SimpleNamespace(get_all_characters=lambda: rows, get_character=lambda n: next((c for c in rows if c.name == n), None))
    paths = {}
    for name in names:
        path = e.ctx.output_dir / 'assets' / name / 'versions' / 'v.png'
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new('RGB', (8, 8)).save(path)
        paths[name] = path
    e.monkeypatch.setattr(e.api, 'current_version', lambda ctx, name, identity: {
        'immutable': True, 'adoption_status': 'adopted', 'candidate_id': None, 'version_id': 'version-' + name, '_path': paths[name]})
    quote = '甲和乙是兄妹。'
    workspace = e.store.get('甲')
    workspace.profile.facts = [CharacterNarrativeFact(fact_id='relationship', field='relationship', value='兄妹',
        evidence=quote, source_start=0, source_end=len(quote), source_document='novel.txt', source_revision='hash',
        confidence=1, source_span=SourceSpan(start_line=1, end_line=1))]
    e.store.save(workspace)
    references, coverage = e.api.resolve_references(e.ctx, sql, pending(), None,
        {'novel.txt': SourceDocument('novel.txt', quote, 'hash')}, 'hash')
    assert len(references) == coverage['compared_count'] == 7
    assert coverage['eligible_count'] == 9 and len(coverage['excluded']) == 2
    assert all(r.project_id == 'p' and r.character_id != '甲' for r in references)
    assert references[0].character_id == '乙' and references[0].relationship_facts[0].fact_id == 'relationship'
    before = references[0].asset_sha256
    Image.new('RGB', (8, 8), 'red').save(paths['乙'])
    assert references[0].asset_sha256 == before


@pytest.mark.asyncio
async def test_effective_model_and_prompt_remain_frozen_after_config_change(setup_casting):
    e = setup_casting
    selected = ['nano-banana-pro']
    e.monkeypatch.setattr(e.api, 'freeze_image_model', lambda ctx, requested: (selected[0], {}))
    body = {'idempotency_key': 'frozen', 'expected_revision': e.revision.revision_id}
    first = (await request(e, 'POST', '/candidates', json=body)).json()['data']
    candidate = e.api.candidate_store(e.ctx).get(first['candidate_id'])
    selected[0] = 'nano-banana-fast'
    e.monkeypatch.setattr(e.api, 'resolved_style', lambda ctx: '油画')
    replay = (await request(e, 'POST', '/candidates', json=body)).json()['data']
    assert replay['candidate_id'] == first['candidate_id']
    saved = e.api.candidate_store(e.ctx).get(first['candidate_id'])
    assert saved.requested_model == 'nano-banana-pro'
    assert saved.snapshot.prompt == candidate.snapshot.prompt
    assert (await request(e, 'GET', '/candidates')).json()['data'][0]['stale'] is True
    assert (await request(e, 'GET')).json()['data']['draft_stale'] is True


def test_identity_portrait_current_never_uses_full_identity_sheet_slot(setup_casting):
    from novelvideo.production_workflow import ProductionWorkflowStore
    from novelvideo.production_workflow import slot_ids
    from novelvideo.production_workflow.models import AssetSlot, AssetVersion, AdoptionStatus
    e = setup_casting
    assert hasattr(slot_ids, 'character_identity_portrait_slot_id'), 'identity portrait needs a separate slot'
    portrait_slot = slot_ids.character_identity_portrait_slot_id('甲', 'older')
    sheet_slot = slot_ids.character_state_slot_id('甲', 'older')
    assert portrait_slot != sheet_slot
    calls = []
    def slot(self, slot_id):
        calls.append(slot_id)
        return AssetSlot(slot_id=sheet_slot, asset_kind='character_state', current_version_id='sheet'), {
            'sheet': AssetVersion(version_id='sheet', slot_id=sheet_slot, asset_path='assets/sheet/versions/v.png', adoption_status=AdoptionStatus.ADOPTED)}
    e.monkeypatch.setattr(ProductionWorkflowStore, 'get_slot', slot)
    assert e.api.current_version(e.ctx, '甲', 'older') is None
    assert calls == [portrait_slot]
