from types import SimpleNamespace
from pathlib import Path
from unittest.mock import AsyncMock

import pytest


@pytest.mark.parametrize("runtime", ["codex", "workbuddy", "deepseek_harness"])
def test_team_accepts_isolated_local_agent_runtimes(monkeypatch, runtime):
    from novelvideo.text_task_runtime.runtime import build_text_task_runtime
    from novelvideo.text_task_runtime.models import AgentTaskRouteSnapshot
    monkeypatch.setenv("ST_EDITION", "team")
    result = build_text_task_runtime(AgentTaskRouteSnapshot(runtime=runtime, task_role="script_creation", source="global"))
    assert result.snapshot.runtime == runtime


def test_team_accepts_model_api_runtime(monkeypatch):
    from novelvideo.text_task_runtime.runtime import build_text_task_runtime, ModelApiStructuredRuntime
    monkeypatch.setenv("ST_EDITION", "team")
    route = SimpleNamespace(runtime="model_api", skill_id=None, skill_version=None)
    assert isinstance(build_text_task_runtime(route), ModelApiStructuredRuntime)


def test_team_default_roles_use_api(monkeypatch):
    from novelvideo.text_task_runtime.settings import default_agent_task_route
    monkeypatch.setenv("ST_EDITION", "team")
    assert default_agent_task_route("identity_sheet_qc").runtime == "model_api"
    assert default_agent_task_route("episode_asset_planning").runtime == "model_api"


def test_team_codex_disables_shell_tools(monkeypatch):
    from novelvideo.knowledge_runtime.codex import build_codex_exec_argv
    monkeypatch.setenv("ST_EDITION", "team")
    argv = build_codex_exec_argv(codex_bin="codex", cwd="/tmp", output_path="/tmp/out", schema_path=None, model="gpt-test")
    assert 'shell_tool' in argv
    assert 'unified_exec' in argv


@pytest.mark.asyncio
@pytest.mark.parametrize('runtime', ['workbuddy', 'deepseek_harness', 'codex'])
async def test_team_launches_use_sandbox_env_and_task_home(monkeypatch, tmp_path, runtime):
    from novelvideo.team import execution
    from novelvideo.text_task_runtime.runtime import build_text_task_runtime
    from novelvideo.text_task_runtime.models import AgentTaskRouteSnapshot
    from novelvideo.knowledge_runtime import codex
    from novelvideo.text_task_runtime import workbuddy, deepseek_harness
    monkeypatch.setenv('ST_EDITION', 'team')
    monkeypatch.setenv('ST_TEAM_DATABASE_URL', 'must-not-leak')
    shared_home = tmp_path / 'server-dsh'
    monkeypatch.setenv('NOVELVIDEO_DSH_HOME', str(shared_home))
    monkeypatch.setattr(deepseek_harness, '_resolve_default_model', lambda _: ('test-model', None))
    monkeypatch.setattr(workbuddy, 'workbuddy_command', lambda: '/installed/workbuddy')
    monkeypatch.setattr(deepseek_harness, 'dsh_command', lambda: '/installed/dsh')
    seen = {}

    def prepare(argv, scratch, *, runtime):
        env = execution.isolated_env(scratch, runtime=runtime, environ={})
        seen['home'] = scratch
        return ['strict-sandbox', *argv], env

    async def spawn(*argv, **kwargs):
        assert argv[0] == 'strict-sandbox'
        assert kwargs['cwd'] == str(seen['home'])
        assert Path(kwargs['env']['HOME']).is_relative_to(seen['home'].resolve())
        assert 'ST_TEAM_DATABASE_URL' not in kwargs['env']
        if runtime == 'deepseek_harness':
            dsh_home = Path(kwargs['env']['DSH_HOME'])
            assert (dsh_home / 'settings.yaml').is_file()
            assert not shared_home.exists()
        sink = kwargs.get('stdout')
        if hasattr(sink, 'write'):
            sink.write(b'{"result":"ok"}')
            sink.flush()
        return SimpleNamespace(returncode=0, communicate=AsyncMock(return_value=(b'ok', b'')))

    monkeypatch.setattr(execution, 'prepare_cli', prepare)
    monkeypatch.setattr(codex.asyncio, 'create_subprocess_exec', spawn)
    monkeypatch.setattr(codex, 'supervise_codex_process', AsyncMock(return_value=SimpleNamespace(
        returncode=0, completed_from_final_message=True, output='ok')))
    selected = build_text_task_runtime(AgentTaskRouteSnapshot(runtime=runtime, model='test-model', task_role='script_creation', source='global'))
    assert await selected.run_structured(prompt='task', output_type=str) == 'ok'
    assert not seen['home'].exists()


@pytest.mark.asyncio
async def test_codex_thread_fallback_keeps_explicit_isolated_env(monkeypatch, tmp_path):
    from novelvideo.knowledge_runtime import codex
    monkeypatch.setattr(codex.asyncio, 'create_subprocess_exec', AsyncMock(side_effect=NotImplementedError))
    seen = {}
    def threaded(argv, **kwargs):
        seen.update(kwargs)
        return 'child'
    monkeypatch.setattr(codex, '_ThreadedProcess', threaded)
    env = {'HOME': str(tmp_path)}
    assert await codex._create_codex_process(['sandbox', 'codex'], stdin=-1, env=env, cwd=str(tmp_path)) == 'child'
    assert seen['env'] == env
    assert seen['cwd'] == str(tmp_path)
