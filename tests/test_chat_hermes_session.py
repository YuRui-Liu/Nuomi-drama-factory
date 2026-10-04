from unittest.mock import AsyncMock
import pytest
from novelvideo.chat.hermes_sdk import HermesSdkThread


async def test_failed_resume_never_creates_session(tmp_path, monkeypatch):
    thread = HermesSdkThread(cli_path=tmp_path/'unused', cwd=tmp_path,
                             env={}, model=None, username='alice', session_id='saved-id')
    send = AsyncMock(return_value=1)
    monkeypatch.setattr(thread, '_send', send)
    monkeypatch.setattr(thread, '_read_until_id', AsyncMock(return_value=({'error': 'missing'}, [])))
    with pytest.raises(RuntimeError, match='resume'):
        await thread._ensure_session()
    assert [call.args[0] for call in send.await_args_list] == ['session/load']
