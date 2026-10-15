from types import SimpleNamespace
from PIL import Image
import pytest
from novelvideo.freezone.canvas_store import atomic_write_json, read_canvas
from novelvideo.freezone.paths import canvas_path


@pytest.fixture
def ctx(tmp_path):
    Image.new('RGB', (101, 57), 'red').save(tmp_path / 'board.png')
    canvas = {'revision': 1, 'nodes': [
        {'id': 'board', 'type': 'storyboardGenNode', 'data': {'imageUrl': 'board.png', 'gridRows': 2, 'gridCols': 2,
         'frames': [{'description': str(i)} for i in range(4)]}},
        {'id': 'video', 'type': 'videoDirectorNode', 'data': {'activeInputMode': 'frames', 'inputModeIntent': 'manual', 'draft': {'revision': 0, 'aspectRatio': '16:9', 'resolution': '720p',
         'segments': [{'id': 'old', 'prompt': 'group', 'durationSeconds': 10}]}, 'videoUrl': 'old.mp4'}}],
        'edges': [{'source': 'board', 'target': 'video'}]}
    atomic_write_json(canvas_path(tmp_path, 'canvas'), canvas)
    return SimpleNamespace(output_dir=tmp_path, state_dir=tmp_path, project_id='p', owner_username='u', project_name='p')


def test_prepare_idempotent_and_manual_conflict(ctx):
    from novelvideo.freezone.video_director.storyboard_preparation import prepare_storyboard
    args = dict(canvas_id='canvas', node_id='video', base_revision=1, request_id='a')
    result = prepare_storyboard(ctx, **args)
    assert len(result['draft']['segments']) == 4
    assert result['canvas']['nodes'][1]['data']['videoUrl'] == 'old.mp4'
    assert prepare_storyboard(ctx, **args)['draft'] == result['draft']
    canvas = read_canvas(ctx.output_dir, 'canvas')
    canvas['nodes'][1]['data']['draft']['segments'][0]['prompt'] = 'manual'
    atomic_write_json(canvas_path(ctx.output_dir, 'canvas'), canvas)
    Image.new('RGB', (101, 57), 'blue').save(ctx.output_dir / 'board.png')
    with pytest.raises(ValueError, match='manual'):
        prepare_storyboard(ctx, canvas_id='canvas', node_id='video', base_revision=2, request_id='b')
    kept = prepare_storyboard(ctx, canvas_id='canvas', node_id='video', base_revision=2, request_id='b', resolution='keep_edits')
    assert kept['draft']['segments'][0]['prompt'] == 'manual'


def test_missing_description_and_stale_guard(ctx):
    from novelvideo.freezone.video_director.storyboard_preparation import prepare_storyboard, guard_prepared
    result = prepare_storyboard(ctx, canvas_id='canvas', node_id='video', base_revision=1, request_id='a')
    guard_prepared(ctx, 'canvas', 'video', result['draft'])
    Image.new('RGB', (101, 57), 'blue').save(ctx.output_dir / 'board.png')
    with pytest.raises(ValueError, match='stale'):
        guard_prepared(ctx, 'canvas', 'video', result['draft'])


def test_preparation_persists_explicit_pair_and_guards_pair_edits(ctx):
    from novelvideo.freezone.video_director.storyboard_preparation import prepare_storyboard, guard_prepared
    canvas = read_canvas(ctx.output_dir, 'canvas')
    board = canvas['nodes'][0]['data']
    for i, frame in enumerate(board['frames']):
        frame['id'] = f'cell-{i}'
    board['videoFramePairs'] = [{'id': 'shot', 'first_frame_id': 'cell-0', 'last_frame_id': 'cell-3',
                               'description': '抬手后停住', 'duration_seconds': 10}]
    atomic_write_json(canvas_path(ctx.output_dir, 'canvas'), canvas)
    result = prepare_storyboard(ctx, canvas_id='canvas', node_id='video', base_revision=1, request_id='pairs')
    segment = result['draft']['segments'][0]
    assert segment['first_frame']['url'] == result['preparation']['cells'][0]['imageUrl']
    assert segment['last_frame']['url'] == result['preparation']['cells'][3]['imageUrl']
    assert result['draft']['references'] == []
    guard_prepared(ctx, 'canvas', 'video', result['draft'])
    canvas = read_canvas(ctx.output_dir, 'canvas')
    canvas['nodes'][0]['data']['videoFramePairs'][0]['last_frame_id'] = 'cell-2'
    atomic_write_json(canvas_path(ctx.output_dir, 'canvas'), canvas)
    with pytest.raises(ValueError, match='stale'):
        guard_prepared(ctx, 'canvas', 'video', result['draft'])


@pytest.mark.asyncio
async def test_api_prepare_editor_contract(ctx, monkeypatch):
    from novelvideo.api.routes import freezone_video_director as api
    roles = []
    async def resolve(project, user, required_role):
        roles.append(required_role)
        return ctx, None, None, ctx.output_dir, str(ctx.output_dir)
    monkeypatch.setattr(api, '_resolve_freezone_project', resolve)
    result = await api.prepare('p', api.PrepareStoryboardBody(canvas_id='canvas', node_id='video', base_revision=1, request_id='a'), {})
    assert result['ok']
    assert roles == ['editor']
    assert result['data']['draft']['segments'][0]['first_frame']['url']


@pytest.mark.asyncio
async def test_orchestration_uses_same_persisted_preparation(ctx, monkeypatch):
    from copy import deepcopy
    from novelvideo.freezone.orchestration.adapters import NodeAdapter
    from novelvideo.api.routes import freezone, freezone_video_director as api
    from novelvideo.freezone.orchestration.models import input_fingerprint
    canvas = read_canvas(ctx.output_dir, 'canvas')
    run = {'snapshot': deepcopy(canvas), 'canvas_id': 'canvas', 'project_id': 'p', 'actor': {},
           'input_fingerprints': {n['id']: input_fingerprint(n) for n in canvas['nodes']}}
    async def resolve(*args, **kwargs):
        return ctx, None, None, ctx.output_dir, str(ctx.output_dir)
    captured = []
    async def submit(project, body, user):
        captured.append(body.draft.model_dump(mode='json'))
        return {'data': {'attempt_id': 'paid-not-called'}}
    monkeypatch.setattr(freezone, '_resolve_freezone_project', resolve)
    monkeypatch.setattr(api, 'create_attempt', submit)
    await NodeAdapter(ctx.output_dir).submit(run, {'node_id': 'video', 'id': 'attempt'}, [
        {'node_id': 'board', 'result': {'imageUrl': 'board.png'}}])
    saved = read_canvas(ctx.output_dir, 'canvas')
    from novelvideo.freezone.orchestration.adapters.requests import director_draft
    assert captured[0] == director_draft(saved['nodes'][1]['data']['draft'])
    assert len(captured[0]['segments']) == 4
    assert run['input_fingerprints']['video'] == input_fingerprint(saved['nodes'][1])


def test_prepared_descriptions_and_source_binding_are_guarded(ctx):
    from novelvideo.freezone.video_director.storyboard_preparation import prepare_storyboard, guard_prepared
    result = prepare_storyboard(ctx, canvas_id='canvas', node_id='video', base_revision=1, request_id='a')
    assert result['preparation']['sourceWidth'] == 101
    assert result['preparation']['sourceUrl'] == 'board.png'
    canvas = read_canvas(ctx.output_dir, 'canvas')
    canvas['nodes'][0]['data']['frames'][0]['description'] = 'changed story'
    atomic_write_json(canvas_path(ctx.output_dir, 'canvas'), canvas)
    with pytest.raises(ValueError, match='stale'):
        guard_prepared(ctx, 'canvas', 'video', result['draft'])


def test_prepare_keeps_revision_when_unchanged(ctx):
    from novelvideo.freezone.video_director.storyboard_preparation import prepare_storyboard
    first = prepare_storyboard(ctx, canvas_id='canvas', node_id='video', base_revision=1, request_id='a')
    second = prepare_storyboard(ctx, canvas_id='canvas', node_id='video', base_revision=2, request_id='b')
    assert second['canvas']['revision'] == first['canvas']['revision']
    assert second['draft'] == first['draft']


@pytest.mark.parametrize('mutation,message', [
    ('descriptions', 'description'), ('foreign', 'another project'),
])
def test_invalid_preparation_does_not_change_canvas(ctx, mutation, message):
    from novelvideo.freezone.video_director.storyboard_preparation import prepare_storyboard
    canvas = read_canvas(ctx.output_dir, 'canvas')
    if mutation == 'descriptions':
        canvas['nodes'][0]['data']['frames'] = []
    elif mutation == 'ref':
        canvas['nodes'][1]['data'].update(activeInputMode='ref', inputModeIntent='manual')
    else:
        canvas['nodes'][0]['data']['imageUrl'] = '/static/projects/other/board.png'
    atomic_write_json(canvas_path(ctx.output_dir, 'canvas'), canvas)
    with pytest.raises(ValueError, match=message):
        prepare_storyboard(ctx, canvas_id='canvas', node_id='video', base_revision=1, request_id='a')
    assert read_canvas(ctx.output_dir, 'canvas') == canvas


@pytest.mark.asyncio
async def test_orchestration_missing_shots_is_known_rejection(ctx, monkeypatch):
    from novelvideo.freezone.orchestration.adapters import NodeAdapter
    from novelvideo.freezone.orchestration.models import OrchestrationError, input_fingerprint
    from novelvideo.api.routes import freezone
    canvas = read_canvas(ctx.output_dir, 'canvas')
    canvas['nodes'][0]['data']['frames'] = []
    atomic_write_json(canvas_path(ctx.output_dir, 'canvas'), canvas)
    async def resolve(*args, **kwargs):
        return ctx, None, None, ctx.output_dir, str(ctx.output_dir)
    monkeypatch.setattr(freezone, '_resolve_freezone_project', resolve)
    run = {'snapshot': canvas, 'canvas_id': 'canvas', 'project_id': 'p', 'actor': {},
           'input_fingerprints': {n['id']: input_fingerprint(n) for n in canvas['nodes']}}
    with pytest.raises(OrchestrationError, match='description'):
        await NodeAdapter(ctx.output_dir).submit(run, {'node_id': 'video', 'id': 'a'}, [])


@pytest.mark.parametrize('field,value', [('prompt', 'altered'), ('duration_seconds', 99), ('first_frame', None)])
def test_guard_rejects_unsaved_same_revision_draft(ctx, field, value):
    from copy import deepcopy
    from novelvideo.freezone.video_director.storyboard_preparation import prepare_storyboard, guard_prepared
    prepared = prepare_storyboard(ctx, canvas_id='canvas', node_id='video', base_revision=1, request_id='a')
    altered = deepcopy(prepared['draft'])
    altered['segments'][0][field] = value
    with pytest.raises(ValueError, match='persisted'):
        guard_prepared(ctx, 'canvas', 'video', altered)


def test_legacy_incoming_board_requires_preparation(ctx):
    from novelvideo.freezone.video_director.storyboard_preparation import guard_prepared
    from novelvideo.freezone.orchestration.adapters.requests import director_draft
    canvas = read_canvas(ctx.output_dir, 'canvas')
    with pytest.raises(ValueError, match='preparation'):
        guard_prepared(ctx, 'canvas', 'video', director_draft(canvas['nodes'][1]['data']['draft']))


@pytest.mark.asyncio
async def test_distinct_state_and_media_roots(ctx, tmp_path, monkeypatch):
    from copy import deepcopy
    from novelvideo.freezone.video_director.storyboard_preparation import prepare_storyboard, guard_prepared
    from novelvideo.freezone.orchestration.adapters import NodeAdapter
    from novelvideo.freezone.orchestration.models import input_fingerprint
    from novelvideo.api.routes import freezone, freezone_video_director as api
    canvas = read_canvas(ctx.output_dir, 'canvas')
    ctx.state_dir = tmp_path / 'state'
    atomic_write_json(canvas_path(ctx.state_dir, 'canvas'), canvas)
    canvas_path(ctx.output_dir, 'canvas').unlink()
    prepared = prepare_storyboard(ctx, canvas_id='canvas', node_id='video', base_revision=1, request_id='a')
    guard_prepared(ctx, 'canvas', 'video', prepared['draft'])
    assert read_canvas(ctx.state_dir, 'canvas')['revision'] == 2
    assert read_canvas(ctx.output_dir, 'canvas') is None
    assert list((ctx.output_dir / 'freezone/_outputs/storyboard_cells').glob('*.png'))
    assert not (ctx.state_dir / 'freezone/_outputs').exists()
    canvas = prepared['canvas']
    run = {'snapshot': deepcopy(canvas), 'canvas_id': 'canvas', 'project_id': 'p', 'actor': {},
           'input_fingerprints': {n['id']: input_fingerprint(n) for n in canvas['nodes']}}
    async def resolve(*args, **kwargs):
        return ctx, None, None, ctx.output_dir, str(ctx.output_dir)
    async def submit(project, body, user):
        guard_prepared(ctx, 'canvas', 'video', body.draft)
        return {'data': {'attempt_id': 'not-paid'}}
    monkeypatch.setattr(freezone, '_resolve_freezone_project', resolve)
    monkeypatch.setattr(api, 'create_attempt', submit)
    await NodeAdapter(ctx.state_dir).submit(run, {'node_id': 'video', 'id': 'attempt'}, [])


def test_reconnected_identical_board_rebuilds_source_binding(ctx):
    from copy import deepcopy
    from novelvideo.freezone.video_director.storyboard_preparation import prepare_storyboard, guard_prepared
    prepared = prepare_storyboard(ctx, canvas_id='canvas', node_id='video', base_revision=1, request_id='a')
    canvas = prepared['canvas']
    duplicate = deepcopy(canvas['nodes'][0])
    duplicate['id'] = 'board-copy'
    canvas['nodes'].append(duplicate)
    canvas['edges'][0]['source'] = 'board-copy'
    atomic_write_json(canvas_path(ctx.state_dir, 'canvas'), canvas)
    new = prepare_storyboard(ctx, canvas_id='canvas', node_id='video', base_revision=2, request_id='b', resolution='replace_generated')
    assert new['preparation']['sourceNodeId'] == 'board-copy'
    assert new['draft']['segments'][0]['id'] == 'board-copy-cell-1'
    guard_prepared(ctx, 'canvas', 'video', new['draft'])


@pytest.mark.parametrize('rows,cols,new_url', [(2, 2, True), (3, 3, False), (1, 4, False)])
def test_source_change_discards_previous_rectangles(ctx, rows, cols, new_url):
    from novelvideo.freezone.video_director.storyboard_preparation import prepare_storyboard, guard_prepared
    from novelvideo.freezone.video_director.storyboard_adaptation import grid_rects
    prepared = prepare_storyboard(ctx, canvas_id='canvas', node_id='video', base_revision=1, request_id='a',
        rects=[[0, 0, 10, 10], [10, 0, 20, 10], [0, 10, 10, 20], [10, 10, 20, 20]])
    canvas = prepared['canvas']
    board = canvas['nodes'][0]['data']
    board.update(gridRows=rows, gridCols=cols, frames=[{'description': str(i)} for i in range(rows * cols)])
    if new_url:
        (ctx.output_dir / 'new.png').write_bytes((ctx.output_dir / 'board.png').read_bytes())
        board['imageUrl'] = 'new.png'
    atomic_write_json(canvas_path(ctx.state_dir, 'canvas'), canvas)
    with pytest.raises(ValueError, match='stale'):
        guard_prepared(ctx, 'canvas', 'video', prepared['draft'])
    new = prepare_storyboard(ctx, canvas_id='canvas', node_id='video', base_revision=2, request_id='b')
    assert [tuple(c['rect']) for c in new['preparation']['cells']] == grid_rects(101, 57, rows, cols)
    assert new['preparation']['sourceGridRows'] == rows
    assert new['preparation']['sourceGridCols'] == cols
    guard_prepared(ctx, 'canvas', 'video', new['draft'])


def test_post_shots_preserves_frame_identity_and_hash(ctx):
    from novelvideo.freezone.video_director.storyboard_preparation import prepare_storyboard, guard_prepared
    canvas = read_canvas(ctx.state_dir, 'canvas')
    canvas['nodes'][0]['data']['frames'][0].update(id='original', referenceIndex=7)
    atomic_write_json(canvas_path(ctx.state_dir, 'canvas'), canvas)
    prepared = prepare_storyboard(ctx, canvas_id='canvas', node_id='video', base_revision=1, request_id='a',
        shots=[{'description': f'new{i}'} for i in range(4)])
    frames = prepared['canvas']['nodes'][0]['data']['frames']
    assert frames[0]['id'] == 'original' and frames[0]['referenceIndex'] == 7
    assert frames[1]['id'] == 'board-cell-2'
    guard_prepared(ctx, 'canvas', 'video', prepared['draft'])


@pytest.mark.parametrize('change', ['url', 'legacy'])
def test_default_crop_cache_refreshes_layout_metadata(ctx, change):
    from novelvideo.freezone.video_director.storyboard_preparation import prepare_storyboard, guard_prepared
    first = prepare_storyboard(ctx, canvas_id='canvas', node_id='video', base_revision=1, request_id='a')
    canvas = first['canvas']
    if change == 'url':
        (ctx.output_dir / 'copy.png').write_bytes((ctx.output_dir / 'board.png').read_bytes())
        canvas['nodes'][0]['data']['imageUrl'] = 'copy.png'
    else:
        canvas['nodes'][1]['data']['storyboardPreparation'].pop('sourceGridRows')
    atomic_write_json(canvas_path(ctx.state_dir, 'canvas'), canvas)
    refreshed = prepare_storyboard(ctx, canvas_id='canvas', node_id='video', base_revision=2, request_id='b')
    assert refreshed['canvas']['revision'] == 3
    assert refreshed['preparation']['sourceGridRows'] == 2
    assert refreshed['preparation']['sourceUrl'] == canvas['nodes'][0]['data']['imageUrl']
    guard_prepared(ctx, 'canvas', 'video', refreshed['draft'])


@pytest.mark.parametrize('reference', [None, {'proposal_id': 'proposal', 'case_id': 'case',
    'dimensions': ['camera'], 'source_prompts': [], 'description': 'example',
    'applied_at': '2026-10-08T00:00:00Z', 'prompt_sha256': 'a' * 64}])
def test_case_reference_frontend_roundtrip(ctx, reference):
    from novelvideo.freezone.video_director.storyboard_preparation import prepare_storyboard, browser_draft
    from novelvideo.freezone.orchestration.adapters.requests import director_draft
    from novelvideo.freezone.video_director.models import DirectorDraft
    first = prepare_storyboard(ctx, canvas_id='canvas', node_id='video', base_revision=1, request_id='a')
    wire = first['draft']
    wire['segments'][0]['case_reference'] = reference
    browser = browser_draft(wire)
    assert browser['segments'][0]['caseReference'] == reference
    assert DirectorDraft.model_validate(director_draft(browser)).segments[0].case_reference is not None if reference else director_draft(browser)['segments'][0]['case_reference'] is None
    canvas = first['canvas']
    canvas['nodes'][1]['data']['draft'] = browser
    atomic_write_json(canvas_path(ctx.state_dir, 'canvas'), canvas)
    second = prepare_storyboard(ctx, canvas_id='canvas', node_id='video', base_revision=2, request_id='b')
    assert second['draft']['segments'][0]['case_reference'] == reference
