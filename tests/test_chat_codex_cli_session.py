import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from novelvideo.chat.codex_cli_session import CodexCliSession


def session(tmp_path, thread_id=None):
    return CodexCliSession(codex_bin='/codex', cwd=tmp_path, env={}, model='test-model', thread_id=thread_id)


def process(events):
    stdout, stderr = asyncio.StreamReader(), asyncio.StreamReader()
    for event in events:
        stdout.feed_data(json.dumps(event).encode() + b'\n')
    stdout.feed_eof(); stderr.feed_eof()
    return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=0, wait=AsyncMock(return_value=0),
        stdin=SimpleNamespace(write=lambda _: None, drain=AsyncMock(), close=lambda: None))


async def test_native_resume_keeps_id_and_emits_reply(monkeypatch, tmp_path):
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', AsyncMock(return_value=process([
        {'type': 'thread.started', 'thread_id': 'existing'},
        {'type': 'error', 'message': 'Reconnecting... 1/5'},
        {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'remembered'}},
        {'type': 'turn.completed'},
    ])))
    thread = session(tmp_path, 'existing')
    events = [event async for event in thread.stream('next')]
    assert thread.id == 'existing'
    assert events[-1].text == 'remembered'
    assert thread.argv()[5:7] == ['resume', 'existing']
    assert '--ephemeral' not in thread.argv()


async def test_resume_cannot_silently_create_another_session(monkeypatch, tmp_path):
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', AsyncMock(return_value=process([
        {'type': 'thread.started', 'thread_id': 'different'},
    ])))
    with pytest.raises(RuntimeError, match='不同会话'):
        _ = [event async for event in session(tmp_path, 'existing').stream('next')]


async def test_cancel_drains_busy_stdout_and_terminates_process(monkeypatch, tmp_path):
    import sys
    launch = asyncio.create_subprocess_exec
    children = []
    async def fake_codex(*args, **kwargs):
        child = await launch(sys.executable, '-c',
            "import os; line=b'{\"type\":\"noise\",\"text\":\"'+b'x'*65536+b'\"}\\n';\nwhile True: os.write(1,line)", **kwargs)
        children.append(child)
        return child
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', fake_codex)
    async def consume():
        return [event async for event in session(tmp_path).stream('test')]
    running = asyncio.create_task(consume())
    await asyncio.sleep(.1)
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(running, 3)
    assert children and children[0].returncode is not None
