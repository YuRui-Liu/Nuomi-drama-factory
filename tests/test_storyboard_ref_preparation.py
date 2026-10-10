from copy import deepcopy

import pytest
from PIL import Image
from tests.test_storyboard_preparation import ctx
from novelvideo.freezone.canvas_store import atomic_write_json, read_canvas
from novelvideo.freezone.paths import canvas_path
from novelvideo.freezone.video_director.storyboard_preparation import prepare_storyboard, guard_prepared
from novelvideo.freezone.video_director.models import DirectorDraft
from novelvideo.freezone.video_director.capabilities import validate_generation


def ref_canvas(ctx):
    canvas = read_canvas(ctx.state_dir, 'canvas')
    canvas['nodes'][1]['data'].update(activeInputMode='ref', inputModeIntent='manual')
    for key in ('actor-a', 'actor-b'):
        Image.new('RGB', (20, 20), 'blue').save(ctx.output_dir / f'{key}.png')
        canvas['nodes'].append({'id': key, 'type': 'imageGenNode', 'data': {'imageUrl': f'{key}.png', 'characterId': key}})
        canvas['edges'].append({'source': key, 'target': 'video', 'data': {'slot': {'kind': 'reference'}}})
    atomic_write_json(canvas_path(ctx.state_dir, 'canvas'), canvas)
    return canvas


def test_ref_prepares_full_board_and_identity_references(ctx):
    ref_canvas(ctx)
    result = prepare_storyboard(ctx, canvas_id='canvas', node_id='video', base_revision=1, request_id='ref')
    draft = DirectorDraft.model_validate(result['draft'])
    assert [r.image_id for r in draft.references] == ['board', 'actor-a', 'actor-b']
    assert all(s.first_frame is None and s.last_frame is None for s in draft.segments)
    assert '<Picture 1>' in draft.segments[0].prompt and '第1行第1列' in draft.segments[0].prompt
    assert '第2行第2列' in draft.segments[3].prompt
    assert validate_generation(draft).route == 'h3_ref'
    assert result['preparation']['inputMode'] == 'ref'
    assert result['canvas']['nodes'][1]['data']['activeInputMode'] == 'ref'
    guard_prepared(ctx, 'canvas', 'video', draft)


def test_ref_capacity_rejects_without_dropping_assets(ctx):
    ref_canvas(ctx)
    with pytest.raises(ValueError, match='at most 2'):
        prepare_storyboard(ctx, canvas_id='canvas', node_id='video', base_revision=1, request_id='ref', reference_limit=2)
    assert read_canvas(ctx.state_dir, 'canvas')['revision'] == 1


def test_reference_source_changes_and_mode_switch_require_prepare(ctx):
    ref_canvas(ctx)
    first = prepare_storyboard(ctx, canvas_id='canvas', node_id='video', base_revision=1, request_id='ref')
    Image.new('RGB', (20, 20), 'green').save(ctx.output_dir / 'actor-a.png')
    with pytest.raises(ValueError, match='stale'):
        guard_prepared(ctx, 'canvas', 'video', first['draft'])
    second = prepare_storyboard(ctx, canvas_id='canvas', node_id='video', base_revision=2, request_id='ref2')
    assert second['draft']['revision'] == first['draft']['revision'] + 1
    canvas = second['canvas']
    canvas['nodes'][1]['data'].update(activeInputMode='frames', inputModeIntent='manual')
    atomic_write_json(canvas_path(ctx.state_dir, 'canvas'), canvas)
    with pytest.raises(ValueError, match='stale'):
        guard_prepared(ctx, 'canvas', 'video', second['draft'])
    frames = prepare_storyboard(ctx, canvas_id='canvas', node_id='video', base_revision=3, request_id='frames')
    assert not frames['draft']['references']
    assert all(s['first_frame'] for s in frames['draft']['segments'])


@pytest.mark.asyncio
async def test_api_uses_configured_reference_limit(ctx, monkeypatch):
    from fastapi import HTTPException
    from novelvideo.api.routes import freezone_video_director as api
    ref_canvas(ctx)
    async def resolve(*args, **kwargs):
        return ctx, None, None, ctx.output_dir, str(ctx.output_dir)
    monkeypatch.setattr(api, '_resolve_freezone_project', resolve)
    monkeypatch.setattr(api, '_limits', lambda: (2, 2))
    with pytest.raises(HTTPException) as error:
        await api.prepare('p', api.PrepareStoryboardBody(canvas_id='canvas', node_id='video', base_revision=1, request_id='a'), {})
    assert error.value.status_code == 422


@pytest.mark.asyncio
async def test_automatic_ref_orchestration_matches_persisted_draft(ctx, monkeypatch):
    from novelvideo.api.routes import freezone, freezone_video_director as api
    from novelvideo.freezone.orchestration.adapters import NodeAdapter
    from novelvideo.freezone.orchestration.models import input_fingerprint
    canvas = ref_canvas(ctx)
    run = {'snapshot': deepcopy(canvas), 'canvas_id': 'canvas', 'project_id': 'p', 'actor': {},
           'input_fingerprints': {n['id']: input_fingerprint(n) for n in canvas['nodes']}}
    async def resolve(*args, **kwargs):
        return ctx, None, None, ctx.output_dir, str(ctx.output_dir)
    captured = []
    async def submit(project, body, user):
        guard_prepared(ctx, 'canvas', 'video', body.draft)
        captured.append(body.draft)
        return {'data': {'attempt_id': 'no-paid-call'}}
    monkeypatch.setattr(freezone, '_resolve_freezone_project', resolve)
    monkeypatch.setattr(api, 'create_attempt', submit)
    monkeypatch.setattr(api, '_limits', lambda: (5, 5))
    await NodeAdapter(ctx.state_dir).submit(run, {'node_id': 'video', 'id': 'a'}, [])
    assert validate_generation(captured[0]).route == 'h3_ref'
    assert len(captured[0].references) == 3


@pytest.mark.parametrize('mutation', ['remove', 'replace'])
def test_corrupted_persisted_references_cannot_reuse_preparation(ctx, mutation):
    from novelvideo.freezone.orchestration.adapters.requests import director_draft
    ref_canvas(ctx)
    first = prepare_storyboard(ctx, canvas_id='canvas', node_id='video', base_revision=1, request_id='ref')
    canvas = first['canvas']
    references = canvas['nodes'][1]['data']['draft']['references']
    if mutation == 'remove':
        references.pop(0)
    else:
        references[0]['url'] = 'actor-a.png'
    atomic_write_json(canvas_path(ctx.state_dir, 'canvas'), canvas)
    corrupted = director_draft(canvas['nodes'][1]['data']['draft'])
    with pytest.raises(ValueError, match='reference'):
        guard_prepared(ctx, 'canvas', 'video', corrupted)
    with pytest.raises(ValueError):
        prepare_storyboard(ctx, canvas_id='canvas', node_id='video', base_revision=2, request_id='again')
    repaired = prepare_storyboard(ctx, canvas_id='canvas', node_id='video', base_revision=2, request_id='replace', resolution='replace_generated')
    assert repaired['draft']['references'][0]['url'] == 'board.png'
