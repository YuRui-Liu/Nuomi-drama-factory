import pytest
from types import SimpleNamespace

from novelvideo.creation.agent_context import node_snapshot


@pytest.fixture
def ctx(tmp_path):
    return SimpleNamespace(project_id='p', state_dir=tmp_path / 'state', output_dir=tmp_path / 'output')


def test_node_snapshot_exposes_structured_static_panel_fields_without_arbitrary_data():
    snapshot = node_snapshot({
        'id': 'storyboard', 'type': 'storyboardGenNode', 'data': {
            'frames': [{
                'id': 'frame-1', 'description': '两人对峙', 'referenceIndex': 0,
                'static_panel': {
                    'id': 'panel-1', 'shot_id': 'shot-1', 'anchor': 'start',
                    'state': '两人隔桌相望', 'framing': '中景', 'setting': '雨夜站台',
                    'lighting': '冷色侧光', 'visible_text': [],
                    'private_locator': 'https://example.invalid/secret.png',
                },
                'static_source_description': '参考上游分镜',
                'private_field': 'must not leak',
            }],
            'extraParams': {'api_key': 'must not leak'},
            'imageUrl': '/private/result.png',
        },
    })
    frame = snapshot['editable_fields']['frames'][0]
    assert frame['static_panel']['lighting'] == '冷色侧光'
    assert frame['static_panel']['framing'] == '中景'
    assert 'private_locator' not in frame['static_panel']
    assert 'private_field' not in frame
    assert 'extraParams' not in snapshot['editable_fields']
    assert 'imageUrl' not in snapshot['editable_fields']


def test_editable_frames_ignore_malformed_duration_and_read_only_identifiers():
    snapshot = node_snapshot({
        'id': 'storyboard', 'type': 'storyboardGenNode', 'data': {
            'frames': [
                {'id': 'frame-a', 'referenceIndex': 'corrupt', 'duration_seconds': 'bad',
                 'description': '旧帧'},
                {'id': 'frame-b', 'referenceIndex': -1, 'duration_seconds': 1.25,
                 'description': '合法帧'},
            ],
        },
    })
    frames = snapshot['editable_fields']['frames']
    assert frames == [{'description': '旧帧'}, {'description': '合法帧', 'duration_seconds': 1.25}]


async def test_script_document_input_is_current_same_project_agent_draft(ctx):
    from novelvideo.creation.agent_context import inputs
    from novelvideo.script_creation.store import DocumentStore

    ctx.state_dir.mkdir(parents=True)
    store = DocumentStore(ctx.state_dir / 'data.db')
    await store.initialize()
    document = await store.create(kind='episode_script', title='第2集', markdown='## 2-1 内景',
        episode_number=2, client_mutation_id='agent-draft', source_origin={
            'kind': 'agent_script_draft', 'project_id': 'p', 'canvas_id': 'canvas-2',
            'delegation_id': 'writer-task', 'member_id': 'writer', 'member_revision': 1,
            'source_ref': None,
        })
    ref = {'kind': 'script_document', 'canvas_id': 'canvas-2', 'document_id': document.id,
           'revision_id': document.current_revision_id}
    with __import__('sqlite3').connect(':memory:') as conn:
        resolved = inputs(ctx, conn, [ref], current_canvas_id='canvas-2')
    assert resolved[0]['snapshot']['kind'] == 'episode_script'
    assert resolved[0]['snapshot']['episode_number'] == 2
    assert resolved[0]['snapshot']['markdown'] == '## 2-1 内景'


@pytest.mark.parametrize('change', [
    {'current_canvas_id': 'canvas-1'},
    {'revision_id': 'older-or-foreign-revision'},
])
async def test_script_document_input_rejects_wrong_scope_or_noncurrent_revision(ctx, change):
    from novelvideo.creation.agent_context import inputs
    from novelvideo.creation.operations import OperationError
    from novelvideo.script_creation.store import DocumentStore

    ctx.state_dir.mkdir(parents=True)
    store = DocumentStore(ctx.state_dir / 'data.db')
    await store.initialize()
    document = await store.create(kind='episode_script', title='第2集', markdown='## 2-1 内景',
        episode_number=2, client_mutation_id='agent-draft-scoped', source_origin={
            'kind': 'agent_script_draft', 'project_id': 'p', 'canvas_id': 'canvas-2',
            'delegation_id': 'writer-task', 'member_id': 'writer', 'member_revision': 1,
            'source_ref': None,
        })
    ref = {'kind': 'script_document', 'canvas_id': 'canvas-2', 'document_id': document.id,
           'revision_id': change.get('revision_id', document.current_revision_id)}
    with __import__('sqlite3').connect(':memory:') as conn:
        with pytest.raises(OperationError):
            inputs(ctx, conn, [ref], current_canvas_id=change.get('current_canvas_id', 'canvas-2'))
