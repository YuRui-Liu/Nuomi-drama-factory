import importlib.util
import pytest
from types import SimpleNamespace


def test_recast_runner_rejects_wrong_runtime_without_source_access(monkeypatch):
    assert importlib.util.find_spec('novelvideo.task_backend.runners.character_casting_proposals'), 'runner missing'
    from novelvideo.task_backend.runners import character_casting_proposals as runner
    monkeypatch.setattr(runner, 'current_text_task_runtime', lambda: None)
    with pytest.raises(ValueError, match='runtime'):
        runner.run_character_casting_proposals({'project_id': 'p', 'payload': {}}, SimpleNamespace(project_id='p'))


@pytest.mark.asyncio
async def test_submission_token_runner_binds_before_generation(tmp_path):
    from tests.character_visual.test_casting_store import store_at, pending
    from novelvideo.task_backend.runners.character_casting import generate_casting_candidate
    from PIL import Image
    store = store_at(tmp_path)
    candidate = pending().model_copy(update={'task_id': 'submission:token', 'submission_token': 'token'})
    store.create_pending(candidate)
    calls = []
    async def generate(**kwargs):
        assert store.get('c1').task_id == 'authoritative'
        calls.append(kwargs)
        kwargs['output_path'].parent.mkdir(parents=True, exist_ok=True)
        Image.new('RGB', (8, 8)).save(kwargs['output_path'])
        return kwargs['output_path']
    assert 'submission_token' in __import__('inspect').signature(generate_casting_candidate).parameters
    await generate_casting_candidate(ctx=SimpleNamespace(output_dir=store.project_dir, state_dir=store.state_dir, project_id='project'),
        candidate_id='c1', character_id=candidate.character_id, identity_id=None, task_id='authoritative',
        submission_token='token', resolution=SimpleNamespace(model='frozen-model', requested_model='frozen-model', resolution_source='explicit'),
        generate=generate)
    assert calls[0]['prompt'] == candidate.snapshot.prompt
    assert calls[0]['model'] == 'frozen-model'
    assert store.get('c1').generation_status == 'succeeded'


@pytest.mark.asyncio
async def test_late_character_deletion_rejects_generation_result(tmp_path):
    from tests.character_visual.test_casting_store import store_at, pending
    from novelvideo.task_backend.runners.character_casting import generate_casting_candidate
    from PIL import Image
    store = store_at(tmp_path)
    store.create_pending(pending())
    async def generate(**kwargs):
        kwargs['output_path'].parent.mkdir(parents=True, exist_ok=True)
        Image.new('RGB', (8, 8)).save(kwargs['output_path'])
        return kwargs['output_path']
    async def before_publish():
        raise ValueError('character deleted')
    assert 'before_publish' in __import__('inspect').signature(generate_casting_candidate).parameters
    with pytest.raises(ValueError, match='deleted'):
        await generate_casting_candidate(ctx=SimpleNamespace(output_dir=store.project_dir, state_dir=store.state_dir, project_id='project'),
            candidate_id='c1', character_id='甲', identity_id=None, task_id='task1',
            resolution=SimpleNamespace(model='m', requested_model='m', resolution_source='explicit'),
            generate=generate, before_publish=before_publish)
    assert store.get('c1').generation_status == 'failed'
    assert store.get('c1').asset_path is None


@pytest.mark.asyncio
@pytest.mark.parametrize('changed', [None, 'source', 'style', 'character'])
async def test_real_recast_publishes_once_and_rejects_late_changes(tmp_path, monkeypatch, changed):
    import asyncio
    from novelvideo.task_backend.runners import character_casting_proposals as runner
    from novelvideo.character_visual.models import CharacterVisualWorkspace, CharacterNarrativeProfile
    from novelvideo.character_visual.store import CharacterVisualWorkspaceStore
    from novelvideo.character_visual.casting_compiler import snapshot_digest
    from novelvideo.character_visual.casting_submission import SubmissionJournal, submit_once
    from novelvideo.character_visual.casting_source import FactExtraction
    from novelvideo.character_design_stage import CharacterDesignOutput
    from novelvideo.sqlite_store import SQLiteStore
    from novelvideo.models import NovelCharacter
    from novelvideo.story_analysis import source_sha256
    from novelvideo.task_state import project_task_run_context
    from tests.test_character_build_stages import proposals
    from novelvideo.api import deps
    ctx = SimpleNamespace(project_id='p', output_dir=tmp_path / 'out', state_dir=tmp_path / 'state',
        owner_project_label='u/p', is_home_node=True)
    async def make(ctx):
        sql = SQLiteStore('u/p', output_dir=str(ctx.output_dir), state_dir=str(ctx.state_dir))
        await sql.initialize()
        await sql.load_graph_state()
        return sql
    sql = await make(ctx)
    await sql.add_character(NovelCharacter(name='甲'))
    await sql.close()
    ctx.output_dir.mkdir(parents=True, exist_ok=True)
    novel = ctx.output_dir / 'novel.txt'
    novel.write_text('甲出现了。')
    store = CharacterVisualWorkspaceStore(ctx.output_dir, state_dir=ctx.state_dir)
    original = store.save(CharacterVisualWorkspace(character_id='甲', profile=CharacterNarrativeProfile(character_id='甲', name='甲')))
    style = ['水墨']
    calls = []
    async def model(**kwargs):
        calls.append(kwargs)
        if kwargs['output_type'] is FactExtraction:
            return FactExtraction()
        if changed == 'source':
            novel.write_text('甲突然老了。')
        elif changed == 'style':
            style[0] = '油画'
        elif changed == 'character':
            live = await make(ctx)
            db = await live._ensure_db()
            await db.execute("DELETE FROM characters WHERE name='甲'")
            await db.commit()
            await live.close()
        return CharacterDesignOutput(design_proposals=proposals())
    monkeypatch.setattr(runner, 'current_text_task_runtime', lambda: SimpleNamespace(run_structured=model, snapshot=SimpleNamespace(task_role='knowledge_extraction')))
    monkeypatch.setattr(runner, 'resolved_style', lambda ctx: style[0])
    monkeypatch.setattr(runner, 'raise_if_envelope_cancel_requested', lambda *a, **kw: None)
    monkeypatch.setattr(deps, 'make_sqlite_store_for_context', make)
    dispatched = []
    async def enqueue(ctx, **kw):
        dispatched.append(kw)
        return SimpleNamespace(task_state=SimpleNamespace(task_id='actual', status='queued'))
    await submit_once(ctx=ctx, journal=SubmissionJournal(ctx), operation='recast', character_id='甲', identity_id=None,
        idempotency_key='k', fingerprint='f', task_type='character_casting_proposals',
        payload={'character_id': '甲', 'identity_id': None, 'workspace_hash': snapshot_digest(original.model_dump(mode='json')),
            'expected_revision': None, 'source_revision': source_sha256('甲出现了。'), 'style': '水墨'},
        backend=SimpleNamespace(enqueue_project_task=enqueue), manager=SimpleNamespace(get_task_for_project=lambda *a, **kw: None))
    envelope = {**dispatched[0], 'project_id': 'p'}
    with project_task_run_context('actual'):
        if changed:
            with pytest.raises(ValueError, match='changed|no longer'):
                await asyncio.to_thread(runner.run_character_casting_proposals, envelope, ctx)
            assert store.get('甲').casting_revision is None
        else:
            first = await asyncio.to_thread(runner.run_character_casting_proposals, envelope, ctx)
            second = await asyncio.to_thread(runner.run_character_casting_proposals, envelope, ctx)
            assert first == second
            assert len(calls) == 2  # one targeted extraction, one design; no paid replay
            assert len(store.get('甲').design_proposals) == 3
            assert store.get('甲').visual_bible is None
