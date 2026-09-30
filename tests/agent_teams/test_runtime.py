from types import SimpleNamespace

import pytest

from novelvideo.agent_teams.models import ExecutionSnapshot, MethodConfig
from novelvideo.text_task_runtime.models import AgentTaskRoute


def snapshot(**changes):
    values = dict(id='s', project_id='p', template_id='builtin', template_revision=1,
                  active_revision=1, role_id='writer', subtask_id='brief',
                  input_revision='r', input_hash='h', resolved_method=MethodConfig(prompt='method'),
                  resolved_model=AgentTaskRoute(model='custom-model'))
    return ExecutionSnapshot(**{**values, **changes})


def test_scope_validates_project_and_cleans_up():
    from novelvideo.agent_teams.runtime import method_scope, current_method
    with pytest.raises(ValueError, match='project'):
        with method_scope([snapshot()], project_id='other'):
            pass
    with pytest.raises(RuntimeError):
        with method_scope([snapshot()], project_id='p'):
            assert current_method('writer', 'brief') == snapshot()
            raise RuntimeError()
    assert current_method('writer', 'brief') is None


def test_no_team_does_not_create_database(tmp_path):
    from novelvideo.agent_teams.runtime import freeze_task_methods
    ctx = SimpleNamespace(state_dir=tmp_path, project_id='p')
    assert freeze_task_methods(ctx, 'script_creation', {}, AgentTaskRoute()) == []
    assert not (tmp_path / 'agent-team.db').exists()


@pytest.mark.asyncio
async def test_adapter_applies_model_guidance_and_keeps_contract(monkeypatch):
    from novelvideo.agent_teams.runtime import method_scope
    from novelvideo.agent_teams.adapters import method_runtime
    calls = []
    class Fake:
        async def run_structured(self, **kwargs):
            calls.append(kwargs)
            return 'ok'
    def build(route):
        assert route.model == 'custom-model'
        assert route.task_role == 'script_creation'
        result = Fake()
        result.snapshot = route
        return result
    monkeypatch.setattr('novelvideo.agent_teams.adapters.build_text_task_runtime', build)
    legacy = SimpleNamespace(snapshot=SimpleNamespace(task_role='script_creation'))
    assert method_runtime('writer', 'brief', legacy) is legacy
    with method_scope([snapshot()], project_id='p'):
        runtime = method_runtime('writer', 'brief', legacy)
        assert await runtime.run_structured(prompt='input', system_prompt='fixed schema', output_type=str) == 'ok'
    assert calls[0]['system_prompt'] == 'fixed schema'
    assert 'method' in calls[0]['prompt'] and 'input' in calls[0]['prompt']


def test_segment_task_freezes_actual_episode_pack_consumer():
    from novelvideo.agent_teams.runtime import task_methods
    assert task_methods('narrative_group_video_segment') == [('video_director', 'h3_episode_pack')]


def test_freeze_survives_activation_and_cache_uses_content(tmp_path):
    from novelvideo.agent_teams.runtime import freeze_task_methods, method_scope, current_method, method_cache_dir
    from novelvideo.agent_teams.service import AgentTeamService
    from novelvideo.agent_teams.store import AgentTeamStore
    from novelvideo.agent_teams.runtime import connected_subtasks
    ctx = SimpleNamespace(state_dir=tmp_path, project_id='p')
    service = AgentTeamService(AgentTeamStore(tmp_path / 'agent-team.db'), None, 'u', connected_subtasks)
    draft = service.save_draft('p', {'overrides': {'writer': {'brief': {'prompt': 'first'}}}}, 0)
    service.activate('p', draft['draft_revision'], 0)
    frozen = freeze_task_methods(ctx, 'script_creation_generation', {'run_id': 'r'}, AgentTaskRoute())
    assert len(frozen) == 7
    draft = service.save_draft('p', {'overrides': {'writer': {'brief': {'prompt': 'second'}}}}, 1)
    service.activate('p', draft['draft_revision'], 1)
    with method_scope(frozen, project_id='p'):
        assert current_method('writer', 'brief').resolved_method.prompt == 'first'
        first = method_cache_dir(tmp_path, 'writer', 'brief')
    changed = freeze_task_methods(ctx, 'script_creation_generation', {'run_id': 'r'}, AgentTaskRoute())
    with method_scope(changed, project_id='p'):
        assert method_cache_dir(tmp_path, 'writer', 'brief') != first


@pytest.mark.parametrize('module,factory,subtask', [
    ('h3_episode_pack', 'create_h3_episode_pack_optimizer', 'h3_episode_pack'),
])
def test_h3_factories_use_method_runtime_and_cache(monkeypatch, tmp_path, module, factory, subtask):
    import importlib
    from novelvideo.agent_teams.runtime import method_scope
    fake = SimpleNamespace(snapshot=SimpleNamespace(model='custom-model'))
    monkeypatch.setattr('novelvideo.agent_teams.adapters.build_text_task_runtime', lambda route: fake)
    mod = importlib.import_module('novelvideo.media_capabilities.video.' + module)
    with method_scope([snapshot(role_id='video_director', subtask_id=subtask)], project_id='p'):
        optimizer = getattr(mod, factory)(cache_dir=tmp_path)
        assert optimizer._cache_dir.parent == tmp_path
        assert optimizer._cache_dir.name.startswith('method-')


@pytest.mark.asyncio
async def test_enqueue_retry_reuses_run_snapshot_and_copies_payload(monkeypatch, tmp_path):
    from novelvideo.ports.local import tasks
    from novelvideo.text_task_runtime.models import AgentTaskRouteSnapshot
    frozen = [snapshot().model_dump(mode='json')]
    previous = SimpleNamespace(metadata={'agent_team_snapshots': frozen}, status='failed')
    captured = {}
    class Manager:
        def get_task_for_project(self, *a, **kw): return previous
        def reserve_task_for_project(self, *a, **kw):
            captured['metadata'] = kw['metadata']
            return SimpleNamespace(task_id='t', status='queued'), True
        def update_progress_for_project(self, *a, **kw): pass
        def claim_task_lease(self, *a, **kw): return True
    monkeypatch.setattr(tasks, 'require_project_home_node', lambda *a, **kw: None)
    monkeypatch.setattr(tasks, 'get_task_manager', lambda: Manager())
    monkeypatch.setattr(tasks, '_ensure_builtin_runners_registered', lambda: None)
    monkeypatch.setattr(tasks, 'get_project_task_runner_registration', lambda _: SimpleNamespace(text_task_role='script_creation'))
    monkeypatch.setattr(tasks, 'resolve_configured_agent_task_route', lambda **kw: AgentTaskRouteSnapshot(task_role='script_creation', source='project'))
    monkeypatch.setattr('novelvideo.agent_teams.runtime.freeze_task_methods', lambda *a: pytest.fail('retry refroze method'))
    backend = tasks.InlineTaskBackend.__new__(tasks.InlineTaskBackend)
    backend._execution_owner_id, backend._lease_seconds = 'o', 30
    backend._bind_cancellation_store = lambda ctx: None
    backend._submit_lane_job = lambda job: captured.update(job=job)
    ctx = SimpleNamespace(project_id='p', requester_user_id='u', state_dir=tmp_path)
    payload = {'run_id': 'r', 'nested': {'value': 'before'}}
    await backend.enqueue_project_task(ctx, task_type='script_creation_generation', scope='run:r', payload=payload)
    payload['nested']['value'] = 'after'
    assert captured['job'].envelope['payload']['nested']['value'] == 'before'
    assert captured['metadata']['agent_team_snapshots'] == frozen
    assert captured['job'].envelope['agent_team_snapshots'] == frozen
    # Task-state TTL expires, but the logical generation run is durable.
    previous = None
    monkeypatch.setattr(tasks, 'resolve_configured_agent_task_route', lambda **kw: pytest.fail('retry resolved current route'))
    await backend.enqueue_project_task(ctx, task_type='script_creation_generation', scope='run:r', payload=payload)
    assert captured['job'].envelope['agent_team_snapshots'] == frozen


def test_durable_run_binding_first_submission_wins(tmp_path):
    from novelvideo.agent_teams.runtime import bind_generation_methods, load_generation_methods
    ctx = SimpleNamespace(state_dir=tmp_path, project_id='p')
    original = {'agent_team_snapshots': [], 'agent_route_snapshot': {'model': 'original'}}
    assert bind_generation_methods(ctx, 'r', original) == original
    assert bind_generation_methods(ctx, 'r', {'changed': True}) == original
    assert load_generation_methods(ctx, 'r') == original
    assert load_generation_methods(SimpleNamespace(state_dir=tmp_path, project_id='other'), 'r') is None
    assert not (tmp_path / 'agent-team.db').exists()
