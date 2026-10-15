from copy import deepcopy

import pytest

from novelvideo.chat import service
from novelvideo.chat.store import chat_store, ChatScope
from novelvideo.chat.session_store import conversation_scope


@pytest.mark.asyncio
async def test_snapshot_is_resolved_once_and_survives_history_reload(monkeypatch, tmp_path):
    from novelvideo.api.routes import chat_canvas
    monkeypatch.setenv('NOVELVIDEO_STATE_DIR', str(tmp_path))
    source = {'canvas_id': 'board', 'revision': 3, 'nodes': [{'id': 'image', 'type': 'imageNode',
              'fingerprint': 'a' * 64, 'data': {'imageUrl': '/old.png', 'displayName': '旧图片'}}], 'assets': []}
    calls = []
    async def resolve(project, body, user):
        calls.append(body)
        return {'data': deepcopy(source)}
    monkeypatch.setattr(chat_canvas, 'resolve_references', resolve)
    references = [{'canvas_id': 'board', 'node_ids': ['image']}]
    snapshot = await chat_canvas.resolve_chat_snapshot('p', references, {'username': 'alice'}, turn_id='turn-1')
    source['nodes'][0]['data']['imageUrl'] = '/new.png'
    prompt = await chat_canvas.resolved_chat_context('p', references, {'username': 'alice'}, snapshot=snapshot)
    assert len(calls) == 1
    assert '/old.png' in prompt and '/new.png' not in prompt
    with conversation_scope('one'):
        saved = service.add_user_message('alice', 'p', '修改这张图片', metadata={'canvas_context': snapshot, 'turn_id': 'turn-1'})
        reloaded = service.list_messages('alice', 'p')[0]
        assert reloaded['canvas_context'] == snapshot
        assert reloaded['turn_id'] == 'turn-1'
        assert reloaded['content'] == '修改这张图片'
        assert reloaded['id'] == saved['id']
        assert chat_store.list_messages('alice', ChatScope('project', 'p'))[0]['canvas_context'] == snapshot
    with conversation_scope('two'):
        assert service.list_messages('alice', 'p') == []
    assert snapshot['primary_target'] == {'canvas_id': 'board', 'node_id': 'image', 'fingerprint': 'a' * 64}


@pytest.mark.asyncio
async def test_combined_snapshot_size_is_bounded(monkeypatch):
    from novelvideo.api.routes import chat_canvas
    from fastapi import HTTPException
    async def resolve(*args):
        return {'data': {'canvas_id': 'board', 'nodes': [], 'assets': [{'text': 'a' * 150000}]}}
    monkeypatch.setattr(chat_canvas, 'resolve_references', resolve)
    with pytest.raises(HTTPException) as caught:
        await chat_canvas.resolve_chat_snapshot('p', [{'canvas_id': 'board'}, {'canvas_id': 'other'}], {})
    assert caught.value.status_code == 413
