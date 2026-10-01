from dataclasses import replace
from hashlib import sha256

import pytest
from PIL import Image

from novelvideo.narrative_groups import service
from novelvideo.narrative_groups.models import CellMapping, GridLayout, GroupStageState, NarrativeGroup


def fixture_group(root):
    paths = []
    for index, color in enumerate(('red', 'blue')):
        path = root / f'cell{index}.png'
        Image.new('RGB', (160, 90), color).save(path)
        paths.append(str(path))
    group = NarrativeGroup(id='g1', ordinal=1, beat_ids=('s1', 's2'),
        layout=GridLayout(rows=1, columns=2, capacity=2),
        cell_to_beat=(CellMapping(cell=0, beat_id='s1'), CellMapping(cell=1, beat_id='s2')))
    group = replace(group, stages={**group.stages, 'render': GroupStageState(
        status='completed', revision=1, cell_assets=tuple(
            {'cell': i, 'beat_id': f's{i+1}', 'path': path} for i, path in enumerate(paths)))})
    service.save_groups(root, 1, [group])
    return group


def test_repair_input_missing_original_is_honest(tmp_path):
    from novelvideo.narrative_groups.storyboard_repair import repair_input
    group = fixture_group(tmp_path)
    data = repair_input(tmp_path, group, 'render', 's1', synthesized='current description')
    assert data['original_prompt'] is None
    assert data['prompt'] == 'current description'
    assert data['source_revision'] == 1


def test_reservation_deduplicates_without_clearing_images(tmp_path):
    from novelvideo.narrative_groups.storyboard_repair import repair_input, reserve_repair
    group = fixture_group(tmp_path)
    data = repair_input(tmp_path, group, 'render', 's1')
    kwargs = dict(source_revision=1, source_asset=data['source_asset'], prompt='dry desk', feedback='rain outside')
    reserve_repair(tmp_path, 1, 'g1', 'render', 's1', **kwargs)
    saved = service.load_groups(tmp_path, 1)[0]
    assert saved.stages['render'].cell_assets == group.stages['render'].cell_assets
    with pytest.raises(RuntimeError, match='进行中'):
        reserve_repair(tmp_path, 1, 'g1', 'render', 's1', **kwargs)
    with pytest.raises(RuntimeError, match='进行中'):
        service.advance_revision(tmp_path, 1, 'g1', 'render', regenerate=True)


def test_publish_changes_only_target_and_preserves_history(tmp_path):
    from novelvideo.narrative_groups.storyboard_repair import repair_input, reserve_repair, publish_repair
    group = fixture_group(tmp_path)
    data = repair_input(tmp_path, group, 'render', 's1')
    ticket = reserve_repair(tmp_path, 1, 'g1', 'render', 's1', source_revision=1,
        source_asset=data['source_asset'], prompt='dry desk', feedback='rain outside')
    generated = tmp_path / 'new.png'
    Image.new('RGB', (160, 90), 'green').save(generated)
    result = publish_repair(tmp_path, 1, 'g1', 'render', ticket['id'], generated,
        snapshot={'original_prompt': 'exact sent prompt', 'prompt': 'dry desk', 'feedback': 'rain outside'}, project_id='p1')
    assert result.stages['render'].revision == 2
    assert result.stages['render'].cell_assets[1] == group.stages['render'].cell_assets[1]
    assert result.stages['render'].cell_assets[0]['path'] != group.stages['render'].cell_assets[0]['path']
    assert result.stages['render'].revision_history[0]['cell_assets'] == list(group.stages['render'].cell_assets)
    assert result.stages['video'].needs_regeneration
    latest = repair_input(tmp_path, result, 'render', 's1')
    assert latest['original_prompt'] == 'exact sent prompt'
    assert latest['feedback'] == 'rain outside'
    again = publish_repair(tmp_path, 1, 'g1', 'render', ticket['id'], generated,
        snapshot={'original_prompt': 'exact sent prompt'}, project_id='p1')
    assert again.stages['render'].revision == 2
    assert again.stages['render'].provider_parameters['cell_repair']['status'] == 'completed'
    restored = service.rollback_stage_revision(tmp_path, 1, 'g1', 'render', revision=1)
    assert restored.stages['render'].cell_assets == group.stages['render'].cell_assets


def test_conflict_keeps_current_image(tmp_path):
    from novelvideo.narrative_groups.storyboard_repair import repair_input, reserve_repair, publish_repair
    group = fixture_group(tmp_path)
    data = repair_input(tmp_path, group, 'render', 's1')
    ticket = reserve_repair(tmp_path, 1, 'g1', 'render', 's1', source_revision=1,
        source_asset=data['source_asset'], prompt='dry desk', feedback='')
    changed = service.load_groups(tmp_path, 1)[0]
    service.save_groups(tmp_path, 1, [replace(changed, stages={**changed.stages,
        'render': replace(changed.stages['render'], revision=3)})])
    generated = tmp_path / 'new.png'
    Image.new('RGB', (160, 90), 'green').save(generated)
    with pytest.raises(RuntimeError, match='变化'):
        publish_repair(tmp_path, 1, 'g1', 'render', ticket['id'], generated, snapshot={}, project_id='p1')
    assert service.load_groups(tmp_path, 1)[0].stages['render'].cell_assets == group.stages['render'].cell_assets


def test_failed_repair_keeps_draft_and_old_image(tmp_path):
    from novelvideo.narrative_groups.storyboard_repair import repair_input, reserve_repair, update_repair
    group = fixture_group(tmp_path)
    data = repair_input(tmp_path, group, 'render', 's1')
    ticket = reserve_repair(tmp_path, 1, 'g1', 'render', 's1', source_revision=1,
        source_asset=data['source_asset'], prompt='edited', feedback='rain outside')
    update_repair(tmp_path, 1, 'g1', 'render', ticket['id'], status='failed', error='provider failed')
    saved = service.load_groups(tmp_path, 1)[0]
    assert saved.stages['render'].cell_assets == group.stages['render'].cell_assets
    data = repair_input(tmp_path, saved, 'render', 's1')
    assert data['repair_status'] == 'failed'
    assert data['prompt'] == 'edited'
    assert data['feedback'] == 'rain outside'


def test_failed_publication_retry_reuses_paid_result(tmp_path):
    from novelvideo.narrative_groups.storyboard_repair import repair_input, reserve_repair, update_repair
    group = fixture_group(tmp_path)
    data = repair_input(tmp_path, group, 'render', 's1')
    kwargs = dict(source_revision=1, source_asset=data['source_asset'], prompt='edited', feedback='')
    ticket = reserve_repair(tmp_path, 1, 'g1', 'render', 's1', **kwargs)
    result = {'grid_asset': str(tmp_path / 'paid.png')}
    update_repair(tmp_path, 1, 'g1', 'render', ticket['id'], status='failed', generation_result=result)
    retry = reserve_repair(tmp_path, 1, 'g1', 'render', 's1', **kwargs)
    assert retry['generation_result'] == result


def test_versioned_repair_updates_video_binding_and_retains_untouched_path(tmp_path):
    from novelvideo.narrative_groups.storyboard_sources import StoryboardCellSource, StoryboardSource
    from novelvideo.narrative_groups.storyboard_binding import freeze_selected_storyboard
    from novelvideo.narrative_groups.storyboard_repair import repair_input, reserve_repair, publish_repair
    group = fixture_group(tmp_path)
    grid = tmp_path / 'grid.png'
    Image.new('RGB', (320, 90), 'red').save(grid)
    source = StoryboardSource(project_id='p1', episode=1, group_id='g1', batch_id='g1',
        asset_id='original', generation_id='original', grid_path='grid.png',
        grid_sha256=sha256(grid.read_bytes()).hexdigest(), rows=1, columns=2, splitter_version='test',
        cells=tuple(StoryboardCellSource(shot_id=f's{i+1}', cell_index=i, path=f'cell{i}.png',
            sha256=sha256((tmp_path / f'cell{i}.png').read_bytes()).hexdigest(), width=160, height=90,
            crop_box=(160*i, 0, 160*(i+1), 90), scale_size=(160, 90)) for i in range(2)))
    group = service.register_storyboard_source(tmp_path, 1, 'g1', source=source, project_id='p1')
    old_path = group.stages['render'].cell_assets[1]['path']
    data = repair_input(tmp_path, group, 'render', 's1')
    ticket = reserve_repair(tmp_path, 1, 'g1', 'render', 's1', source_revision=1,
        source_asset=data['source_asset'], prompt='edited', feedback='')
    generated = tmp_path / 'new.png'
    Image.new('RGB', (200, 200), 'green').save(generated)
    result = publish_repair(tmp_path, 1, 'g1', 'render', ticket['id'], generated, snapshot={}, project_id='p1')
    binding, frames = freeze_selected_storyboard(result, media_root=tmp_path, project_id='p1', episode=1)
    assert binding.selection_id != source.source_id
    assert result.stages['render'].cell_assets[1]['path'] == old_path
    assert binding.cell_assets(tmp_path) == result.stages['render'].cell_assets
    assert len(frames) == 2


@pytest.mark.asyncio
async def test_runner_requests_one_frame_and_passes_feedback(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from novelvideo.narrative_groups.storyboard_repair import repair_input, reserve_repair
    from novelvideo.task_backend.runners import narrative_group as runner
    from novelvideo.task_backend.runners.storyboard_repair import execute_repair
    group = fixture_group(tmp_path)
    data = repair_input(tmp_path, group, 'render', 's1')
    ticket = reserve_repair(tmp_path, 1, 'g1', 'render', 's1', source_revision=1,
        source_asset=data['source_asset'], prompt='edited', feedback='rain outside',
        generation={'model': 'gpt-image-2', 'provider_id': 'grsai-main'})
    calls = []
    async def generate(payload, ctx):
        calls.append(payload)
        from novelvideo.costs.context import resolve_cost_context
        cost = resolve_cost_context('image')
        assert cost.project_id == 'p1' and cost.resource_id == 's1'
        assert cost.usage == {'item': '1'}
        actual = runner._generation_input(payload)
        assert 'edited' in actual.prompt and 'rain outside' in actual.prompt
        assert 'Panel 2' not in actual.prompt
        assert len(actual.references) == 1
        output = tmp_path / 'generated.png'
        Image.new('RGB', (160, 90), 'green').save(output)
        return {'grid_asset': str(output), 'prompt_snapshot': {'original_prompt': actual.prompt}}
    monkeypatch.setattr(runner, '_generate_grid', generate)
    payload = {'project_dir': str(tmp_path), 'output_dir': str(tmp_path), 'project_id': 'p1',
               'episode': 1, 'group_id': 'g1', 'stage': 'render', 'repair_id': ticket['id']}
    ctx = SimpleNamespace(output_dir=str(tmp_path))
    await execute_repair({'payload': payload}, ctx)
    assert len(calls) == 1
    assert calls[0]['layout'] == {'rows': 1, 'columns': 1, 'capacity': 1}
    assert service.load_groups(tmp_path, 1)[0].stages['render'].revision == 2
    # Re-delivery is idempotent and never buys the same image again.
    await execute_repair({'payload': payload}, ctx)
    assert len(calls) == 1


def test_api_single_repair_validates_source_and_queues_once(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from novelvideo.api.routes import narrative_groups as api
    fixture_group(tmp_path)
    calls = []
    async def resolve(*args, **kwargs):
        return SimpleNamespace(project_dir=tmp_path, output_dir=str(tmp_path),
            ctx=SimpleNamespace(project_id='p1', output_dir=str(tmp_path))), service.load_groups(tmp_path, 1), []
    async def enqueue(ctx, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(task_state=SimpleNamespace(task_id='t1'), backend='test', queue='default')
    monkeypatch.setattr(api, '_resolve_groups', resolve)
    monkeypatch.setattr(api, '_image_binding', lambda *args: ('grsai-main', 'gpt-image-2', '1K'))
    monkeypatch.setattr(api, 'get_task_backend', lambda: SimpleNamespace(enqueue_project_task=enqueue))
    app = FastAPI()
    app.include_router(api.router, prefix='/api/v1')
    app.dependency_overrides[api.get_api_user] = lambda: {'id': 'u1'}
    client = TestClient(app)
    url = '/api/v1/projects/demo/episodes/1/narrative-groups/g1/render/cells/s1/repair'
    response = client.get(url)
    assert response.status_code == 200
    data = response.json()['data']
    body = {'source_revision': data['source_revision'], 'source_asset': data['source_asset'],
            'prompt': 'edited', 'feedback': 'rain outside'}
    assert client.post(url, json={**body, 'source_revision': 2}).status_code == 409
    assert client.post(url, json=body).status_code == 200
    assert client.post(url, json=body).status_code == 409
    assert len(calls) == 1 and calls[0]['task_type'] == 'narrative_storyboard_repair'
    assert calls[0]['payload']['repair_id']
    assert client.get(url).json()['data']['repair_status'] == 'queued'
    from novelvideo.narrative_groups.storyboard_repair import update_repair
    repair_id = calls[0]['payload']['repair_id']
    update_repair(tmp_path, 1, 'g1', 'render', repair_id, status='failed')
    async def reject_enqueue(*args, **kwargs):
        raise RuntimeError('queue unavailable')
    monkeypatch.setattr(api, 'get_task_backend', lambda: SimpleNamespace(enqueue_project_task=reject_enqueue))
    assert client.post(url, json=body).status_code == 503
    assert client.get(url).json()['data']['repair_status'] == 'failed'
    assert service.load_groups(tmp_path, 1)[0].stages['render'].cell_assets[0]['path'].endswith('cell0.png')


def test_single_prompt_preserves_reference_mapping_for_next_repair(tmp_path):
    from novelvideo.task_backend.runners.storyboard_repair import single_generation_input
    original = tmp_path / 'original.png'
    identity = tmp_path / 'identity.png'
    Image.new('RGB', (16, 9)).save(original)
    Image.new('RGB', (16, 9)).save(identity)
    result = single_generation_input({'project_dir': str(tmp_path), 'stage': 'render',
        'aspect_ratio': '16:9', 'repair_prompt': 'desk', 'repair_feedback': 'dry',
        'repair_references': [{'path': str(p), 'sha256': sha256(p.read_bytes()).hexdigest()}
                              for p in (original, identity)],
        'repair_reference_mappings': ['character Hero, identity Hero_casual; use for panels 2.']})
    assert len(result.reference_mappings) == len(result.references)
    assert 'Hero_casual' in result.reference_mappings[1]
    assert 'panels 2' not in result.prompt


@pytest.mark.asyncio
async def test_old_image_repair_reuses_recorded_character_reference(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from novelvideo.narrative_groups.storyboard_repair import repair_input, reserve_repair
    from novelvideo.task_backend.runners import narrative_group as runner
    from novelvideo.task_backend.runners.storyboard_repair import execute_repair
    group = fixture_group(tmp_path)
    identity = tmp_path / 'identity.png'
    Image.new('RGB', (16, 9), 'yellow').save(identity)
    data = repair_input(tmp_path, group, 'render', 's1')
    ticket = reserve_repair(tmp_path, 1, 'g1', 'render', 's1', source_revision=1,
        source_asset=data['source_asset'], prompt='edited', feedback='', generation={
            'reference_audit': {'asset_references': [{'entity_key': 'Hero', 'asset_path': str(identity),
                'sha256': sha256(identity.read_bytes()).hexdigest(), 'shot_ids': ['s1']}]}})
    async def generate(payload, ctx):
        actual = runner._generation_input(payload)
        assert str(identity) in actual.references
        assert 'Hero' in actual.prompt
        path = tmp_path / 'generated.png'
        Image.new('RGB', (160, 90)).save(path)
        return {'grid_asset': str(path)}
    monkeypatch.setattr(runner, '_generate_grid', generate)
    await execute_repair({'payload': {'project_dir': str(tmp_path), 'output_dir': str(tmp_path),
        'project_id': 'p1', 'episode': 1, 'group_id': 'g1', 'stage': 'render', 'repair_id': ticket['id']}},
        SimpleNamespace(output_dir=str(tmp_path)))


def test_terminal_task_releases_repair_without_opening_dialog(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from novelvideo.narrative_groups.storyboard_repair import repair_input, reserve_repair, update_repair
    from novelvideo.api.routes import narrative_groups as api
    group = fixture_group(tmp_path)
    data = repair_input(tmp_path, group, 'render', 's1')
    ticket = reserve_repair(tmp_path, 1, 'g1', 'render', 's1', source_revision=1,
        source_asset=data['source_asset'], prompt='edited', feedback='')
    update_repair(tmp_path, 1, 'g1', 'render', ticket['id'], task_id='t1')
    monkeypatch.setattr(api, 'get_task_manager', lambda: SimpleNamespace(
        get_task_for_project=lambda *args, **kwargs: SimpleNamespace(status='failed', error='worker stopped'),
        list_tasks_for_project=lambda *args: []))
    groups = api._reconcile_orphan_stages(SimpleNamespace(project_dir=tmp_path, ctx=object()),
                                          1, service.load_groups(tmp_path, 1))
    assert groups[0].stages['render'].provider_parameters['cell_repair']['status'] == 'failed'


@pytest.mark.asyncio
async def test_grid_adapter_records_exact_prompt_and_never_retries_repair_policy(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from novelvideo.task_backend.runners import narrative_group as runner
    from novelvideo.narrative_groups.storyboard_repair import load_prompt_snapshot
    from novelvideo.media_capabilities.image.grsai import GrsaiPolicyViolation
    image_path = tmp_path / 'original.png'
    Image.new('RGB', (160, 90)).save(image_path)
    raw = image_path.read_bytes()
    monkeypatch.setattr('novelvideo.api.deps.get_media_capability_store', lambda: object())
    monkeypatch.setattr('novelvideo.api.deps.get_media_credential_resolver', lambda: object())
    monkeypatch.setattr('novelvideo.media_capabilities.runtime.configuration.load_grsai_runtime_configuration',
                        lambda *a, **kw: SimpleNamespace(model='gpt-image-2'))
    requests = []
    async def execute(runtime, request, **kwargs):
        requests.append(request)
        return SimpleNamespace(task_id='provider-1', content=raw)
    monkeypatch.setattr('novelvideo.media_capabilities.runtime.grsai_execution.execute_grsai_generation', execute)
    payload = {'project_dir': str(tmp_path), 'output_dir': str(tmp_path), 'episode': 1, 'group_id': 'g1',
        'stage': 'render', 'revision': 2, 'single_storyboard_repair': True, 'aspect_ratio': '16:9',
        'repair_references': [{'path': str(image_path), 'sha256': sha256(raw).hexdigest()}],
        'repair_prompt': 'a dry indoor desk', 'repair_feedback': 'rain only outside',
        'layout': {'rows': 1, 'columns': 1, 'capacity': 1}}
    result = await runner._generate_grid(payload, SimpleNamespace(output_dir=str(tmp_path)))
    snapshot = load_prompt_snapshot(tmp_path, result['grid_asset'])
    assert snapshot['original_prompt'] == requests[0].prompt
    assert snapshot['prompt'] == 'a dry indoor desk'
    assert snapshot['feedback'] == 'rain only outside'
    attempts = []
    async def reject(*args, **kwargs):
        attempts.append(1)
        raise GrsaiPolicyViolation('rejected')
    monkeypatch.setattr('novelvideo.media_capabilities.runtime.grsai_execution.execute_grsai_generation', reject)
    with pytest.raises(GrsaiPolicyViolation):
        await runner._generate_grid(payload, SimpleNamespace(output_dir=str(tmp_path)))
    assert attempts == [1]


@pytest.mark.asyncio
async def test_grid_policy_rejection_preserves_authored_zombies_without_retry(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from novelvideo.task_backend.runners import narrative_group as runner
    from novelvideo.media_capabilities.image.grsai import GrsaiPolicyViolation

    monkeypatch.setattr('novelvideo.api.deps.get_media_capability_store', lambda: object())
    monkeypatch.setattr('novelvideo.api.deps.get_media_credential_resolver', lambda: object())
    monkeypatch.setattr('novelvideo.media_capabilities.runtime.configuration.load_grsai_runtime_configuration',
                        lambda *a, **kw: SimpleNamespace(model='gpt-image-2'))
    prompt = '室内丧尸群挡住通道，制服上有血迹。保留作者设定。'
    monkeypatch.setattr(runner, '_generation_input', lambda *a, **kw: SimpleNamespace(prompt=prompt, references=[]))
    requests = []
    async def reject(runtime, request, **kwargs):
        requests.append(request)
        raise GrsaiPolicyViolation('rejected')
    monkeypatch.setattr('novelvideo.media_capabilities.runtime.grsai_execution.execute_grsai_generation', reject)
    payload = {'project_dir': str(tmp_path), 'output_dir': str(tmp_path), 'episode': 1,
        'group_id': 'g1', 'stage': 'render', 'revision': 1, 'aspect_ratio': '16:9',
        'layout': {'rows': 1, 'columns': 1, 'capacity': 1}}
    with pytest.raises(GrsaiPolicyViolation, match='原始提示词'):
        await runner._generate_grid(payload, SimpleNamespace(output_dir=str(tmp_path)))
    assert len(requests) == 1
    assert requests[0].prompt == prompt


@pytest.mark.asyncio
async def test_strong_sketch_snapshot_reference_slots_are_aligned(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from novelvideo.task_backend.runners import narrative_group as runner
    from novelvideo.narrative_groups.storyboard_repair import load_prompt_snapshot
    files = [tmp_path / 'sketch.png', tmp_path / 'hero.png']
    for path in files:
        Image.new('RGB', (160, 90)).save(path)
    monkeypatch.setattr('novelvideo.api.deps.get_media_capability_store', lambda: object())
    monkeypatch.setattr('novelvideo.api.deps.get_media_credential_resolver', lambda: object())
    monkeypatch.setattr('novelvideo.media_capabilities.runtime.configuration.load_grsai_runtime_configuration',
                        lambda *a, **kw: SimpleNamespace(model='gpt-image-2'))
    monkeypatch.setattr(runner, '_generation_input', lambda _: runner.GroupGenerationInput(
        prompt='exact prompt', references=tuple(str(p) for p in files), reference_mappings=('Hero',)))
    async def execute(*args, **kwargs):
        return SimpleNamespace(task_id='provider-1', content=files[0].read_bytes())
    monkeypatch.setattr('novelvideo.media_capabilities.runtime.grsai_execution.execute_grsai_generation', execute)
    result = await runner._generate_grid({'project_dir': str(tmp_path), 'output_dir': str(tmp_path),
        'episode': 1, 'group_id': 'g1', 'stage': 'render', 'revision': 1,
        'constraint_mode': 'strong_sketch', 'layout': {'rows': 1, 'columns': 1}, 'aspect_ratio': '16:9'},
        SimpleNamespace(output_dir=str(tmp_path)))
    snapshot = load_prompt_snapshot(tmp_path, result['grid_asset'])
    assert snapshot['reference_mappings'] == ['storyboard sketch grid', 'Hero']


@pytest.mark.asyncio
async def test_normal_grid_generation_attaches_prompt_to_split_cell(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from novelvideo.task_backend.runners import narrative_group as runner
    from novelvideo.narrative_groups.storyboard_repair import repair_input
    group = fixture_group(tmp_path)
    async def generate(*args):
        grid = tmp_path / 'new-grid.png'
        Image.new('RGB', (320, 90), 'blue').save(grid)
        return {'grid_asset': str(grid), 'prompt_snapshot': {
            'original_prompt': 'exact original full grid prompt',
            'panel_prompts': {'s1': 'first shot description', 's2': 'second shot description'}}}
    monkeypatch.setattr(runner, '_generate_grid', generate)
    await runner._execute({'episode': 1, 'payload': {'project_dir': str(tmp_path), 'group_id': group.id,
        'stage': 'render', 'revision': 1, 'aspect_ratio': '16:9', 'model': ''}},
        SimpleNamespace(output_dir=str(tmp_path)), split_only=False)
    current = service.load_groups(tmp_path, 1)[0]
    data = repair_input(tmp_path, current, 'render', 's1')
    assert data['original_prompt'] == 'exact original full grid prompt'
    assert data['prompt'] == 'first shot description'


def test_task_reconciliation_cannot_overwrite_concurrent_completion(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from novelvideo.narrative_groups.storyboard_repair import repair_input, reserve_repair, update_repair
    from novelvideo.api.routes import narrative_groups as api
    group = fixture_group(tmp_path)
    data = repair_input(tmp_path, group, 'render', 's1')
    ticket = reserve_repair(tmp_path, 1, 'g1', 'render', 's1', source_revision=1,
        source_asset=data['source_asset'], prompt='edited', feedback='')
    stale = service.load_groups(tmp_path, 1)[0]
    def get_task(*args, **kwargs):
        update_repair(tmp_path, 1, 'g1', 'render', ticket['id'], status='completed')
        return SimpleNamespace(status='failed', error='late duplicate failed')
    monkeypatch.setattr(api, 'get_task_manager', lambda: SimpleNamespace(get_task_for_project=get_task))
    actual = api._reconcile_cell_repair(SimpleNamespace(project_dir=tmp_path, ctx=object()), 1, stale, 'render')
    assert actual.stages['render'].provider_parameters['cell_repair']['status'] == 'completed'
