import pytest


def test_ce_saved_assistant_route_overrides_environment(monkeypatch, tmp_path):
    from novelvideo.chat.runtime_settings import load_chat_runtime_settings, save_chat_runtime_settings
    from novelvideo import config
    monkeypatch.setattr(config, 'STATE_DIR', str(tmp_path))
    monkeypatch.setenv('ST_EDITION', 'ce')
    monkeypatch.setenv('NOVELVIDEO_STATE_DIR', str(tmp_path))
    monkeypatch.setenv('DRAMACLAW_CHAT_BACKEND', 'codex')
    assert load_chat_runtime_settings().backend == 'codex'
    save_chat_runtime_settings(backend='workbuddy', model='test-model', reasoning_effort='low')
    assert load_chat_runtime_settings().backend == 'workbuddy'


def test_chat_capabilities_remain_separate():
    from novelvideo.chat.runtime_settings import ChatRuntimeSettings
    from novelvideo.text_task_runtime.models import AgentTaskRoute
    from pydantic import ValidationError
    assert ChatRuntimeSettings(backend='hermes', model='').model == ''
    with pytest.raises(ValidationError):
        AgentTaskRoute(runtime='hermes', model='x')
