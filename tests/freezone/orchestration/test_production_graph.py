from copy import deepcopy
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from novelvideo.freezone.orchestration.models import OrchestrationError


def test_production_graph_has_independent_group_branches_and_complete_export():
    from novelvideo.freezone.orchestration.production_graph import build_production_graph

    graph = build_production_graph(1, 'director-v1', ['g-one', 'g-two'])
    nodes = {node['id']: node for node in graph['nodes']}
    assert len(nodes) == 5
    export = nodes[graph['target']]
    assert export['type'] == 'episodeComposeNode'
    assert export['data']['productionGroupIds'] == ['g-one', 'g-two']
    for group in ['g-one', 'g-two']:
        pair = [n for n in nodes.values() if n['data'].get('groupId') == group]
        render = next(n for n in pair if n['type'] == 'narrativeGroupStoryboardNode')
        video = next(n for n in pair if n['type'] == 'narrativeGroupVideoNode')
        assert render['data']['operation'] == 'render'
        assert video['data']['operation'] == 'video'
        assert all(n['data']['directorRevisionId'] == 'director-v1' for n in pair)
        assert any(e['source'] == render['id'] and e['target'] == video['id'] for e in graph['edges'])
        assert any(e['source'] == video['id'] and e['target'] == export['id'] for e in graph['edges'])
    assert build_production_graph(1, 'director-v1', ['g-one', 'g-two']) == graph
    assert set(nodes).isdisjoint(n['id'] for n in build_production_graph(1, 'director-v2', ['g-one', 'g-two'])['nodes'])


@pytest.mark.parametrize('episode,revision,groups', [
    (0, 'v1', ['g']), (True, 'v1', ['g']), (1, '', ['g']),
    (1, 'v1', []), (1, 'v1', ['g', 'g']), (1, 'v1', ['../g']),
    # The materializer and the production adapter must accept the same IDs;
    # otherwise a graph can be saved successfully but fail only at execution.
    (1, 'v1', ['group with spaces']), (1, 'v1', ['g?draft']),
])
def test_production_graph_rejects_ambiguous_binding(episode, revision, groups):
    from novelvideo.freezone.orchestration.production_graph import build_production_graph

    with pytest.raises(OrchestrationError):
        build_production_graph(episode, revision, groups)


def test_requested_production_groups_do_not_materialize_omitted_branches():
    from novelvideo.freezone.orchestration.production_graph import build_production_graph

    graph = build_production_graph(3, 'director-v7', ['g-selected'])
    branch_groups = {node['data']['groupId'] for node in graph['nodes'] if 'groupId' in node['data']}
    assert branch_groups == {'g-selected'}
    assert len(graph['nodes']) == 3
    assert len(graph['edges']) == 2


def test_episode_compose_result_projects_to_a_valid_video_output_node(tmp_path):
    from novelvideo.freezone.canvas_store import read_canvas
    from novelvideo.freezone.orchestration.graph import validate_graph
    from novelvideo.freezone.orchestration.models import input_fingerprint
    from novelvideo.freezone.orchestration.production_graph import materialize_production_graph
    from novelvideo.freezone.orchestration.projection import create_canvas, project_result

    create_canvas(tmp_path, 'compose-output', project_id='p', name='compose', actor_id='u')
    graph = materialize_production_graph(tmp_path, 'compose-output', base_revision=1,
        request_id='production-graph', episode=1, revision_id='director-v1', group_ids=['g1'])
    canvas = graph['canvas']
    target = graph['target']
    compose = next(node for node in canvas['nodes'] if node['id'] == target)

    projected = project_result(tmp_path, 'compose-output', node_id=target,
        expected_input=input_fingerprint(compose), attempt_id='compose-attempt',
        result={'resultVideoUrl': '/static/projects/p/episode-1.mp4'})

    report = validate_graph(projected['nodes'], projected['edges'])
    assert report['valid'], report['errors']
    output = next(node for node in projected['nodes'] if node['type'] == 'videoNode'
                  and node['data'].get('videoUrl') == '/static/projects/p/episode-1.mp4')
    assert any(edge['source'] == target and edge['target'] == output['id']
               and edge['data']['sourcePort'] == 'video' for edge in projected['edges'])


def test_materialization_preserves_results_and_does_not_submit(tmp_path):
    from novelvideo.freezone.canvas_store import read_canvas
    from novelvideo.freezone.orchestration.production_graph import materialize_production_graph
    from novelvideo.freezone.orchestration.projection import create_canvas, patch_graph

    create_canvas(tmp_path, 'canvas', project_id='p', name='test', actor_id='u')
    args = dict(episode=1, revision_id='v1', group_ids=['g1', 'g2'])
    first = materialize_production_graph(tmp_path, 'canvas', base_revision=1, request_id='make', **args)
    replay = materialize_production_graph(tmp_path, 'canvas', base_revision=1, request_id='make', **args)
    assert replay == first
    node = first['canvas']['nodes'][0]
    patched = patch_graph(tmp_path, 'canvas', base_revision=first['canvas']['revision'], request_id='result',
        operations=[{'op': 'update_node', 'node_id': node['id'], 'data': {'groupRenderResult': {'task_id': 'existing'}}}])
    before = deepcopy(patched['nodes'])
    again = materialize_production_graph(tmp_path, 'canvas', base_revision=patched['revision'], request_id='ensure', **args)
    assert again['canvas']['nodes'] == before
    assert len(again['canvas']['edges']) == 4
    assert read_canvas(tmp_path, 'canvas')['nodes'] == before
    assert not (tmp_path / 'freezone' / 'orchestration' / 'runs.sqlite3').exists()


def test_episode_target_executes_all_group_dependencies():
    from novelvideo.freezone.orchestration.graph import execution_order
    from novelvideo.freezone.orchestration.production_graph import build_production_graph

    graph = build_production_graph(1, 'v1', ['g1', 'g2'])
    order = execution_order(graph['nodes'], graph['edges'], graph['target'])
    assert set(order) == {node['id'] for node in graph['nodes']}
    assert order[-1] == graph['target']
    for edge in graph['edges']:
        assert order.index(edge['source']) < order.index(edge['target'])


def test_graph_api_requires_active_revision_and_materializes_all_groups(monkeypatch, tmp_path):
    from fastapi import FastAPI, HTTPException
    from fastapi.testclient import TestClient
    from novelvideo.api.auth import get_api_user
    from novelvideo.api.routes import freezone_orchestration as routes
    from novelvideo.creation.canvas_workspaces import CanvasWorkspace, CanvasCreate
    from novelvideo.creation.projection import ProjectionService
    from novelvideo.creation.store import ObjectStore
    from novelvideo.creation.workspace import EpisodeCreate, EpisodeWorkspace
    from novelvideo.director_plan.store import DirectorPlanStore
    from novelvideo.director_plan.models import NarrativeGroupPlan, ShotPlan
    from novelvideo.freezone.canvas_store import read_canvas

    state_dir = tmp_path / 'state'
    output_dir = tmp_path / 'output'
    objects = ObjectStore(state_dir)
    episode_workspace = EpisodeWorkspace(objects, project_id='p')
    e1 = episode_workspace.create('alice', EpisodeCreate(request_id='e1', title='第一集'))
    e2 = episode_workspace.create('alice', EpisodeCreate(request_id='e2', title='第二集'))
    free = CanvasWorkspace(objects, project_id='p').create('alice',
        CanvasCreate(request_id='free', kind='free', title='自由画布'))
    for operation in (e1, e2, free):
        ProjectionService(objects).apply(operation.operation_id)
    e1_canvas_id = e1.object_refs[0]['canvas_id']

    async def resolve(project, user, required_role='editor'):
        if project != 'p':
            raise HTTPException(403, 'denied')
        return SimpleNamespace(state_dir=state_dir, output_dir=output_dir), 'u', 'p', state_dir, str(output_dir)

    @contextmanager
    def active(self, episode):
        groups = [NarrativeGroupPlan(
            id=group_id, ordinal=index, source_span_ids=('span-1',),
            scene_anchor='hallway', time_anchor='night', objective='enter',
            visible_turn='door opens', relation_to_previous='single',
            shots=(ShotPlan(id=f'{group_id}-shot', source_span_ids=('span-1',),
                subject='hero', action='opens door', visible_start_state='closed',
                visible_end_state='open', duration_seconds=6),),
        ) for index, group_id in enumerate(('g1', 'g2'), start=1)]
        yield SimpleNamespace(status='active', revision_id='v1',
                              groups=groups)

    monkeypatch.setattr(routes, '_resolve_freezone_project', resolve)
    monkeypatch.setattr(DirectorPlanStore, 'lock_active_revision', active)
    monkeypatch.setattr(routes, 'launch', lambda *args: pytest.fail('Graph creation must not launch production'))
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[get_api_user] = lambda: {'id': 'u'}
    client = TestClient(app)
    prefix = '/projects/p/freezone/orchestration'
    body = {'base_revision': 1, 'request_id': 'make', 'episode': 1, 'revision_id': 'stale'}
    wrong_scope = {**body, 'episode': 2, 'revision_id': 'v1'}
    denied = client.post(prefix + f'/canvases/{e1_canvas_id}/production-graph', json=wrong_scope)
    assert denied.status_code == 409
    untouched = read_canvas(state_dir, e1_canvas_id)
    assert untouched['revision'] == 1 and untouched['nodes'] == []

    denied = client.post(prefix + f'/canvases/{e1_canvas_id}/production-graph', json=body)
    assert denied.status_code == 409
    body['revision_id'] = 'v1'
    response = client.post(prefix + f'/canvases/{e1_canvas_id}/production-graph', json=body)
    assert response.status_code == 200, response.text
    data = response.json()['data']
    assert data['submitted'] is False
    assert len(data['canvas']['nodes']) == 5
    assert client.post(prefix.replace('/p/', '/other/') + f'/canvases/{e1_canvas_id}/production-graph', json=body).status_code == 403


def test_run_store_validates_executed_episode_nodes_against_registered_canvas(tmp_path):
    from novelvideo.creation.canvas_workspaces import CanvasWorkspace, CanvasCreate
    from novelvideo.creation.store import ObjectStore
    from novelvideo.creation.workspace import EpisodeCreate, EpisodeWorkspace
    from novelvideo.creation.canvas_scope import resolve_canvas_scope
    from novelvideo.freezone.orchestration.store import RunStore

    state_dir = tmp_path / 'state'
    output_dir = tmp_path / 'output'
    objects = ObjectStore(state_dir)
    episodes = EpisodeWorkspace(objects, project_id='p')
    e1 = episodes.create('alice', EpisodeCreate(request_id='e1', title='第一集'))
    e2 = episodes.create('alice', EpisodeCreate(request_id='e2', title='第二集'))
    CanvasWorkspace(objects, project_id='p').create('alice',
        CanvasCreate(request_id='free', kind='free', title='自由画布'))
    scope = resolve_canvas_scope(state_dir, e1.object_refs[0]['canvas_id'])
    assert scope['episode_number'] == e1.object_refs[0]['number'] == 1

    # An executed node cannot smuggle E02 into E01, even if node.data is forged.
    canvas = {'canvas_id': e1.object_refs[0]['canvas_id'], 'revision': 1,
        'nodes': [
            {'id': 'target', 'type': 'textAnnotationNode', 'data': {'content': 'text'}},
            {'id': 'forged-e02', 'type': 'directorWorkspaceNode', 'data': {'episodeNumber': 2}},
        ], 'edges': []}
    with pytest.raises(OrchestrationError) as rejected:
        RunStore(output_dir).create(canvas, target='forged-e02', request_id='run', project_id='p',
            settings={}, actor={'id': 'alice'}, workspace_scope=scope)
    assert rejected.value.code == 'episode_scope_mismatch'
    assert RunStore(output_dir).find_request(canvas['canvas_id'], 'run') is None

    # A managed run that cannot resolve its authoritative registry scope is
    # rejected too; it cannot use the node payload as a substitute.
    with pytest.raises(OrchestrationError) as unresolved:
        RunStore(output_dir).create(canvas, target='forged-e02', request_id='unknown', project_id='p',
            settings={}, actor={'id': 'alice'}, workspace_scope=None)
    assert unresolved.value.code == 'episode_scope_mismatch'
    assert e2.object_refs[0]['number'] == 2


@pytest.mark.parametrize('status', ['running', 'conflict'])
def test_run_store_does_not_admit_non_applied_registered_scope(tmp_path, status):
    from novelvideo.creation.canvas_scope import resolve_canvas_scope
    from novelvideo.creation.store import ObjectStore
    from novelvideo.creation.workspace import EpisodeCreate, EpisodeWorkspace
    from novelvideo.freezone.orchestration.store import RunStore

    state_dir = tmp_path / 'state'
    output_dir = tmp_path / 'output'
    operation = EpisodeWorkspace(ObjectStore(state_dir), project_id='p').create(
        'alice', EpisodeCreate(request_id='e1', title='第一集'))
    scope = resolve_canvas_scope(state_dir, operation.object_refs[0]['canvas_id'])
    scope['status'] = status
    canvas = {'canvas_id': operation.object_refs[0]['canvas_id'], 'revision': 1,
        'nodes': [{'id': 'target', 'type': 'textAnnotationNode', 'data': {'content': 'text'}}], 'edges': []}

    with pytest.raises(OrchestrationError) as rejected:
        RunStore(output_dir).create(canvas, target='target', request_id='run', project_id='p',
            settings={}, actor={'id': 'alice'}, workspace_scope=scope)

    assert rejected.value.code == 'episode_scope_mismatch'
    assert RunStore(output_dir).find_request(canvas['canvas_id'], 'run') is None


@pytest.mark.parametrize('action', ['resume', 'retry'])
def test_old_wrong_episode_run_is_rejected_before_resume_or_retry_writes(tmp_path, monkeypatch, action):
    import asyncio
    from fastapi import HTTPException
    from types import SimpleNamespace
    from novelvideo.creation.canvas_workspaces import CanvasWorkspace, CanvasCreate
    from novelvideo.creation.projection import ProjectionService
    from novelvideo.creation.store import ObjectStore
    from novelvideo.creation.workspace import EpisodeCreate, EpisodeWorkspace
    from novelvideo.api.routes import freezone_orchestration as routes
    from novelvideo.freezone.orchestration.store import RunStore

    state_dir, output_dir = tmp_path / 'state', tmp_path / 'output'
    objects = ObjectStore(state_dir)
    episode = EpisodeWorkspace(objects, project_id='p').create(
        'alice', EpisodeCreate(request_id='e1', title='第一集'))
    ProjectionService(objects).apply(episode.operation_id)
    canvas_id = episode.object_refs[0]['canvas_id']
    canvas = {'canvas_id': canvas_id, 'project_id': 'p', 'revision': 1,
        'nodes': [{'id': 'director', 'type': 'directorWorkspaceNode', 'data': {'episodeNumber': 2}}],
        'edges': []}
    store = RunStore(output_dir)
    run = store.create(canvas, target='director', request_id='old-run', project_id='p',
        settings={}, actor={'id': 'alice'})
    if action == 'resume':
        run['status'] = 'cancelled'
        run['attempts']['director'][-1]['status'] = 'cancelled'
    else:
        run['status'] = 'failed'
        run['attempts']['director'][-1]['status'] = 'failed'
    before = store.save(run, 'fixture_old_scope')
    launched = []

    async def resolve(project, user, required_role='editor'):
        return SimpleNamespace(state_dir=state_dir), 'alice', 'p', output_dir, str(output_dir)

    monkeypatch.setattr(routes, '_resolve_freezone_project', resolve)
    monkeypatch.setattr(routes, 'launch', lambda *args: launched.append(args))
    with pytest.raises(HTTPException) as rejected:
        if action == 'resume':
            asyncio.run(routes.resume('p', before['id'], {'id': 'alice'}))
        else:
            asyncio.run(routes.retry('p', before['id'], routes.RetryRun(request_id='retry-1'), {'id': 'alice'}))

    assert rejected.value.status_code == 409
    after = store.get(before['id'])
    assert after == before
    assert launched == []


def test_startup_recovery_blocks_wrong_episode_run_without_launch(tmp_path, monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from novelvideo.config import STATE_DIR as ORIGINAL_STATE_DIR
    from novelvideo.creation.projection import ProjectionService
    from novelvideo.creation.store import ObjectStore
    from novelvideo.creation.workspace import EpisodeCreate, EpisodeWorkspace
    from novelvideo.api.routes import freezone_orchestration as routes
    from novelvideo.freezone.orchestration.store import RunStore

    state_dir = tmp_path / 'creation-state'
    project_root = tmp_path / 'users' / 'alice' / 'p'
    objects = ObjectStore(state_dir)
    episode = EpisodeWorkspace(objects, project_id='p').create(
        'alice', EpisodeCreate(request_id='e1', title='第一集'))
    ProjectionService(objects).apply(episode.operation_id)
    canvas_id = episode.object_refs[0]['canvas_id']
    canvas = {'canvas_id': canvas_id, 'project_id': 'p', 'revision': 1,
        'nodes': [{'id': 'director', 'type': 'directorWorkspaceNode', 'data': {'episodeNumber': 2}}],
        'edges': []}
    store = RunStore(project_root)
    run = store.create(canvas, target='director', request_id='old-run', project_id='p',
        settings={}, actor={'id': 'alice'})
    launched = []

    async def resolve(project, user, required_role='editor'):
        assert project == 'p'
        return SimpleNamespace(state_dir=state_dir), 'alice', 'p', project_root, str(project_root)

    monkeypatch.setattr('novelvideo.config.STATE_DIR', tmp_path)
    monkeypatch.setattr(routes, '_resolve_freezone_project', resolve)
    monkeypatch.setattr(routes, 'launch', lambda *args: launched.append(args))
    asyncio.run(routes.recover_runs())

    blocked = store.get(run['id'])
    assert blocked['status'] == 'blocked'
    assert blocked['workspace_scope_error']['code'] == 'episode_scope_mismatch'
    assert blocked['attempts']['director'][-1]['status'] == 'blocked'
    assert launched == []
