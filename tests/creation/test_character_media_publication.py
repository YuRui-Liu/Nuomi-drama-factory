"""Character API/runner publications preserve semantic media history."""
import io
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image
from fastapi import UploadFile

from novelvideo.creation.asset_adapter import AssetAdapter
from novelvideo.creation.store import ObjectStore
from novelvideo.models import NovelCharacter, CharacterIdentity
from novelvideo.sqlite_store import SQLiteStore


def png(color):
    buffer = io.BytesIO()
    Image.new('RGB', (8, 8), color).save(buffer, format='PNG')
    return buffer.getvalue()


@pytest.fixture
async def project(tmp_path, monkeypatch):
    from novelvideo.api.routes import characters
    source = SQLiteStore('test', output_dir=str(tmp_path / 'output'), state_dir=str(tmp_path / 'state'))
    await source.initialize()
    await source.add_character(NovelCharacter(name='Lin'))
    await source.add_character_identity('Lin', CharacterIdentity(identity_id='id', character_name='Lin', identity_name='coat', face_prompt='face'))
    ctx = SimpleNamespace(output_dir=Path(source.project_dir), state_dir=source.state_dir,
                          requester_user_id='actor', requester_username='actor', project_id='test')
    async def resolve(*args, **kwargs):
        return ctx, 'actor', 'test', Path(source.project_dir), str(source.project_dir), source
    monkeypatch.setattr(characters, '_resolve_character_project', resolve)
    monkeypatch.setattr(characters, '_asset_url', lambda ctx, root, path: str(path))
    yield source, ctx
    await source.close()


async def upload(kind, data):
    from novelvideo.api.routes import characters
    file = UploadFile(filename='image.png', file=io.BytesIO(data))
    if kind == 'base':
        return await characters.upload_portrait('test', 'Lin', file, {})
    if kind == 'state':
        return await characters.upload_identity_image('test', 'Lin', 'coat', file, {})
    method = characters.upload_identity_portrait if kind == 'portrait' else characters.upload_identity_costume
    return await method('test', 'Lin', 'id', file, {})


@pytest.mark.parametrize('kind', ['base', 'state', 'portrait', 'costume'])
@pytest.mark.parametrize('registered', [True, False])
async def test_real_uploads_capture_same_path_and_keep_history(project, kind, registered):
    source, ctx = project
    adapter = AssetAdapter(source, ObjectStore(source.state_dir)) if registered else None
    if adapter:
        base = await adapter.register_base('character', 'Lin')
        child = await adapter.register_variant(base.base_asset_id, 'id')
        object_id = base.base_asset_id if kind == 'base' else child.variant_id
    assert (await upload(kind, png('red')))['ok']
    if adapter:
        before = adapter.objects.read(object_id)
        assert before['revision'] == 2
        blob = before['payload']['_media'][0]['current_blob']
        assert (ctx.output_dir / blob['path']).read_bytes() == png('red')
    assert (await upload(kind, png('blue')))['ok']
    if adapter:
        after = adapter.objects.read(object_id)
        assert after['revision'] == 3
        assert (ctx.output_dir / after['payload']['_media'][0]['current_blob']['path']).read_bytes() == png('blue')
        assert (ctx.output_dir / blob['path']).read_bytes() == png('red')
        assert adapter.objects.read(object_id, 2) == before
    else:
        assert not (Path(source.state_dir) / 'creation').exists()


@pytest.mark.parametrize('kind', ['state','costume','portrait'])
@pytest.mark.parametrize('mutate_cached_object', [False, True])
async def test_identity_upload_rejects_locator_changed_during_file_read(project, kind, mutate_cached_object):
    from fastapi import HTTPException
    from novelvideo.api.routes import characters
    source, ctx = project
    await upload(kind,png('red'))
    adapter = AssetAdapter(source,ObjectStore(source.state_dir)); base = await adapter.register_base('character','Lin')
    child = await adapter.register_variant(base.base_asset_id,'id')
    fixed = adapter.objects.read(child.variant_id,1)
    assets = {p:p.read_bytes() for p in (ctx.output_dir / 'assets').rglob('*') if p.is_file()}
    cached_character = source.get_character('Lin')
    class RenameDuringRead(UploadFile):
        async def read(self, size=-1):
            await source.update_character_identity('Lin','id',identity_name='newcoat')
            if mutate_cached_object:
                cached_identity = cached_character.identities[0]
                latest = source.get_character('Lin')
                cached_identity.__dict__.update(latest.identities[0].__dict__)
                cached_character.__dict__.update(latest.__dict__)
                cached_character.identities = [cached_identity]
            return png('blue')
    file = RenameDuringRead(filename='image.png',file=io.BytesIO())
    method = {'state':characters.upload_identity_image,'costume':characters.upload_identity_costume,
              'portrait':characters.upload_identity_portrait}[kind]
    with pytest.raises(HTTPException) as error:
        await method('test','Lin','coat' if kind == 'state' else 'id',file,{})
    assert error.value.status_code == 409
    assert all(p.read_bytes() == data for p,data in assets.items())
    assert adapter.objects.read(child.variant_id,1) == fixed
    assert source.get_character('Lin').identities[0].identity_name == 'newcoat'


@pytest.mark.parametrize('operation', ['restore','delete_costume','delete_image'])
async def test_identity_file_mutation_rejects_cached_locator_after_waiting_for_lock(project, monkeypatch, operation):
    from contextlib import asynccontextmanager
    from fastapi import HTTPException
    from novelvideo.api.routes import characters
    from novelvideo.creation import asset_media_publication as publication
    from novelvideo.production_workflow.coordination import asset_project_lock
    source, ctx = project
    await upload('costume',png('red')); await upload('costume',png('blue'))
    await upload('state',png('red'))
    target = ctx.output_dir / 'assets/characters/Lin/identities/coat_costume.png'
    entries = characters._character_asset_history_entries(ctx=ctx,project_dir=ctx.output_dir,target=target)
    waiting = asyncio.Event()
    @asynccontextmanager
    async def observed_lock(state_dir):
        waiting.set()
        async with asset_project_lock(state_dir):
            yield
    monkeypatch.setattr(publication,'asset_project_lock',observed_lock)
    async with asset_project_lock(ctx.state_dir):
        if operation == 'restore':
            call = characters.restore_character_asset_history('test','Lin',characters.CharacterAssetRestoreRequest(
                kind='identity_costume',identity_id='id',history_id=entries[0]['history_id']),{})
        else:
            method = characters.delete_identity_costume if operation == 'delete_costume' else characters.delete_identity_image
            call = method('test','Lin','id',{})
        task = asyncio.create_task(call)
        await waiting.wait()
        writer = SQLiteStore('second',output_dir=str(ctx.output_dir),state_dir=str(ctx.state_dir))
        try:
            await writer.load_graph_state()
            await writer.update_character_identity('Lin','id',identity_name='newcoat')
        finally:
            await writer.close()
        files = {p:p.read_bytes() for p in (ctx.output_dir / 'assets').rglob('*') if p.is_file()}
    with pytest.raises(HTTPException) as error:
        await task
    assert error.value.status_code == 409
    assert all(p.read_bytes() == data for p,data in files.items())


async def test_portrait_runner_publishes_after_provider_without_lock(project, monkeypatch):
    from novelvideo.task_backend.runners import character_image
    from novelvideo.creation.asset_media_publication import publication_targets
    source, ctx = project
    adapter = AssetAdapter(source, ObjectStore(source.state_dir))
    base = await adapter.register_base('character', 'Lin')
    async def generate(**kwargs):
        assert not publication_targets(source.state_dir)
        assert adapter.objects.read(base.base_asset_id)['revision'] == 1
        path = Path(kwargs['output_path']); path.write_bytes(png('red')); return path
    monkeypatch.setattr(character_image, '_generate_grsai_image', generate)
    await character_image._generate_character_portrait(character=source.get_character('Lin'), ethnicity='Chinese',
        output_dir=ctx.output_dir, style='', model='test', task_type='character_portrait', scope='',
        update=lambda *args: None, compiled_prompt='face', ctx=ctx)
    assert adapter.objects.read(base.base_asset_id)['revision'] == 2


async def test_real_qc_runner_promotes_and_captures_current(project, monkeypatch):
    from novelvideo.production_workflow import ProductionWorkflowStore
    from novelvideo.character_visual.recheck import prepare_recheck
    from novelvideo.character_visual.identity_sheet import IdentitySheetQualityReport
    from novelvideo.task_backend.runners import character_qc
    source, ctx = project
    path = ctx.output_dir / 'assets/characters/Lin/identities/coat/versions/v1.png'
    path.parent.mkdir(parents=True); path.write_bytes(png('red'))
    workflow = ProductionWorkflowStore(Path(ctx.state_dir) / 'production_workflow.json')
    workflow.register_candidate_version(slot_id='character:Lin:state:id', asset_kind='character_state',
        version_id='v1', asset_path=path.relative_to(ctx.output_dir).as_posix(), source_attempt_id='attempt',
        qc_passed=False, generation_metadata={'canonical_path':'assets/characters/Lin/identities/coat.png'},
        actor='actor', at=datetime.now(timezone.utc))
    adapter = AssetAdapter(source, ObjectStore(source.state_dir))
    base = await adapter.register_base('character', 'Lin')
    child = await adapter.register_variant(base.base_asset_id, 'id')
    target = prepare_recheck(ctx, character_name='Lin', identity_id='id', version_id='v1')
    async def assess(*args):
        return IdentitySheetQualityReport(passed=True, checks={}, issues=[], style_family='3d_realistic')
    monkeypatch.setattr(character_qc, '_assess', assess)
    monkeypatch.setattr(character_qc, 'raise_if_envelope_cancel_requested', lambda *args, **kwargs: None)
    result = await asyncio.to_thread(character_qc.run_character_qc,
        {'payload': {'target':target, 'fingerprint':'qc', 'qc_route':{}}}, ctx)
    assert result['adoption_status'] == 'provisional'
    assert adapter.objects.read(child.variant_id)['revision'] == 2
    assert adapter.objects.read(child.variant_id)['payload']['_media'][0]['current_blob']


@pytest.mark.parametrize('kind,restore_kind', [('base','portrait'), ('state','identity'), ('portrait','identity_portrait'), ('costume','identity_costume')])
async def test_restore_real_route_includes_media_and_source(project, kind, restore_kind):
    from novelvideo.api.routes import characters
    source, ctx = project
    adapter = AssetAdapter(source, ObjectStore(source.state_dir))
    base = await adapter.register_base('character', 'Lin')
    child = await adapter.register_variant(base.base_asset_id, 'id')
    await upload(kind, png('red'))
    await upload(kind, png('blue'))
    target, _ = characters._resolve_character_asset_path(project_dir=ctx.output_dir,
        character=source.get_character('Lin'), kind=restore_kind, identity_id='id')
    entries = characters._character_asset_history_entries(ctx=ctx, project_dir=ctx.output_dir, target=target)
    result = await characters.restore_character_asset_history('test', 'Lin',
        characters.CharacterAssetRestoreRequest(kind=restore_kind, identity_id='id' if kind != 'base' else '',
                                               history_id=entries[0]['history_id']), {})
    assert result['ok']
    obj = adapter.objects.read(base.base_asset_id if kind == 'base' else child.variant_id)
    assert obj['revision'] == 4
    assert (ctx.output_dir / obj['payload']['_media'][0]['current_blob']['path']).read_bytes() == png('red')


async def test_upload_records_canonical_override_without_claiming_old_attempt(project):
    from novelvideo.production_workflow import ProductionWorkflowStore
    source, ctx = project
    await upload('state', png('red'))
    path = ctx.output_dir / 'assets/version.png'; path.write_bytes(png('red'))
    workflow = ProductionWorkflowStore(Path(ctx.state_dir) / 'production_workflow.json')
    workflow.register_candidate_version(slot_id='character:Lin:state:id', asset_kind='character_state', version_id='v1',
        asset_path='assets/version.png', source_attempt_id='old-attempt', qc_passed=True,
        generation_metadata={}, actor='actor', at=datetime.now(timezone.utc))
    adapter = AssetAdapter(source, ObjectStore(source.state_dir))
    base = await adapter.register_base('character', 'Lin'); child = await adapter.register_variant(base.base_asset_id, 'id')
    await upload('state', png('blue'))
    item = adapter.objects.read(child.variant_id)['payload']['_media'][0]
    assert item['version_id'] == 'v1' and item['source_attempt_id'] == 'old-attempt'
    assert item['current_source']['kind'] == 'canonical_override'
    assert item['current_source']['source_attempt_id'] is None
    assert (ctx.output_dir / item['blob']['path']).read_bytes() == png('red')
    assert (ctx.output_dir / item['current_blob']['path']).read_bytes() == png('blue')


@pytest.mark.parametrize('phase', ['published','captured'])
@pytest.mark.parametrize('cancel', [True, False])
async def test_upload_failure_and_cancel_pending_are_explicit(project, monkeypatch, phase, cancel):
    from fastapi import HTTPException
    from novelvideo.creation import asset_media_publication as publication
    from novelvideo.creation.asset_sync import SourceSyncPending
    source, ctx = project
    adapter = AssetAdapter(source, ObjectStore(source.state_dir))
    base = await adapter.register_base('character', 'Lin'); child = await adapter.register_variant(base.base_asset_id, 'id')
    def fail(at):
        if at == phase:
            raise asyncio.CancelledError() if cancel else RuntimeError('lost')
    monkeypatch.setattr(publication, '_checkpoint', fail)
    with pytest.raises(asyncio.CancelledError if cancel else RuntimeError):
        await upload('costume', png('red'))
    monkeypatch.setattr(publication, '_checkpoint', lambda _: None)
    assert adapter.objects.read(child.variant_id, 1)['payload']['_media'] == []
    with pytest.raises(HTTPException) as error:
        await upload('costume', png('blue'))
    assert error.value.status_code == 409 and error.value.detail['code'] == 'asset_media_pending'
    if phase == 'captured':
        await publication.reconcile_media_publications(source)
        assert adapter.objects.read(child.variant_id)['revision'] == 2
        assert adapter.objects.read(base.base_asset_id)['revision'] == 2
    else:
        with pytest.raises(SourceSyncPending):
            await publication.reconcile_media_publications(source)


@pytest.mark.parametrize('kind', ['state', 'costume'])
async def test_delete_clears_workflow_current_but_keeps_fixed_history(project, kind):
    from novelvideo.api.routes import characters
    from novelvideo.production_workflow import ProductionWorkflowStore
    source, ctx = project
    await upload(kind, png('red'))
    suffix = 'coat.png' if kind == 'state' else 'coat_costume.png'
    slot = 'character:Lin:state:id' if kind == 'state' else 'character:Lin:identity:id:costume'
    workflow = ProductionWorkflowStore(Path(ctx.state_dir) / 'production_workflow.json')
    workflow.materialize_legacy_current(slot_id=slot, asset_kind='character_state',
        asset_path=f'assets/characters/Lin/identities/{suffix}')
    adapter = AssetAdapter(source, ObjectStore(source.state_dir))
    base = await adapter.register_base('character', 'Lin'); child = await adapter.register_variant(base.base_asset_id, 'id')
    fixed = adapter.objects.read(child.variant_id)
    method = characters.delete_identity_image if kind == 'state' else characters.delete_identity_costume
    assert (await method('test','Lin','id',{}))['ok']
    latest = ProductionWorkflowStore(workflow.state_path)
    assert latest.get_slot(slot)[0].current_version_id is None
    assert latest.get_slot(slot)[0].version_ids
    assert adapter.objects.read(child.variant_id)['payload']['_media'] == []
    assert (ctx.output_dir / fixed['payload']['_media'][0]['current_blob']['path']).read_bytes() == png('red')
    revision = adapter.objects.read(child.variant_id)['revision']
    await method('test','Lin','id',{})
    assert adapter.objects.read(child.variant_id)['revision'] == revision
    candidate_path = ctx.output_dir / 'assets/new.png'; candidate_path.write_bytes(png('blue'))
    latest.register_candidate_version(slot_id=slot, asset_kind='character_state', version_id='new', asset_path='assets/new.png',
        source_attempt_id=None, qc_passed=True, generation_metadata={}, actor='test', at=datetime.now(timezone.utc))
    assert latest.get_slot(slot)[0].current_version_id == 'new'


@pytest.mark.parametrize('phase', ['waiting', 'provider'])
@pytest.mark.parametrize('changed_name', ['Lin', 'Other', 'candidate'])
async def test_compiled_portrait_preserves_target_visual_input_evidence(project, monkeypatch, phase, changed_name):
    from contextlib import asynccontextmanager
    from novelvideo.task_backend.runners import character_image
    from novelvideo.creation import asset_media_publication as publication
    from novelvideo.production_workflow.coordination import asset_project_lock
    from novelvideo.character_visual import CharacterVisualWorkspaceStore
    source, ctx = project
    path = CharacterVisualWorkspaceStore(ctx.output_dir,state_dir=ctx.state_dir).path
    path.parent.mkdir(parents=True,exist_ok=True)
    evidence = {'Lin':{'visual_bible':{'concept':'OLD'}},'Other':{'visual_bible':{'concept':'OLD'}}}
    path.write_text(json.dumps(evidence))
    character = source.get_character('Lin')
    async with publication.character_media_publication(ctx.output_dir,ctx.state_dir,'Lin',publish=False,input_character=character) as token:
        compiled_prompt = evidence['Lin']['visual_bible']['concept']
    waiting = asyncio.Event()
    @asynccontextmanager
    async def observed_lock(state_dir):
        waiting.set()
        async with asset_project_lock(state_dir):
            yield
    monkeypatch.setattr(publication,'asset_project_lock',observed_lock)
    calls = []
    def change():
        if changed_name == 'candidate':
            evidence['Lin']['design_proposals'] = [{'proposal_id':'new candidate'}]
        else:
            evidence[changed_name]['visual_bible']['concept'] = 'NEW'
        path.write_text(json.dumps(evidence))
    async def provider(**kwargs):
        calls.append(kwargs)
        if phase == 'provider':
            async with asset_project_lock(ctx.state_dir):
                change()
        output = Path(kwargs['output_path']); output.write_bytes(png('red')); return output
    monkeypatch.setattr(character_image,'_generate_grsai_image',provider)
    call = character_image._generate_character_portrait(character=character,ethnicity='Chinese',output_dir=ctx.output_dir,
        style='',model='test',task_type='character_portrait',scope='',update=lambda *args:None,
        compiled_prompt=compiled_prompt,expected_inputs=token,ctx=ctx)
    if phase == 'waiting':
        async with asset_project_lock(ctx.state_dir):
            task = asyncio.create_task(call)
            await waiting.wait()
            change()
    else:
        task = asyncio.create_task(call)
    if changed_name == 'Lin':
        with pytest.raises(ValueError,match='changed during generation'):
            await task
        assert len(calls) == (0 if phase == 'waiting' else 1)
        assert not (ctx.output_dir / 'assets/characters/Lin/portrait.png').exists()
    else:
        await task
        assert len(calls) == 1


@pytest.mark.parametrize('entry', ['runner', 'route'])
@pytest.mark.parametrize('changed_name', ['Lin', 'Other'])
async def test_waiting_preflight_checks_the_actual_cached_generation_input(project, monkeypatch, entry, changed_name):
    from contextlib import asynccontextmanager
    from fastapi import HTTPException
    from novelvideo.api.routes import characters
    from novelvideo.task_backend.runners import character_image
    from novelvideo.creation import asset_media_publication as publication
    from novelvideo.production_workflow.coordination import asset_project_lock
    source, ctx = project
    await source.update_character('Lin',face_prompt='OLD')
    await source.add_character(NovelCharacter(name='Other'))
    cached = source.get_character('Lin').model_copy(deep=True)
    waiting = asyncio.Event()
    @asynccontextmanager
    async def observed_lock(state_dir):
        waiting.set()
        async with asset_project_lock(state_dir):
            yield
    monkeypatch.setattr(publication,'asset_project_lock',observed_lock)
    calls = []
    async def provider(**kwargs):
        calls.append(kwargs)
        path = Path(kwargs.get('output_path') or (Path(kwargs['output_dir']) / 'result.png'))
        path.parent.mkdir(parents=True,exist_ok=True); path.write_bytes(png('red'))
        return path
    async def route_provider(**kwargs):
        return [str(await provider(**kwargs))]
    monkeypatch.setattr(character_image,'_generate_grsai_image',provider)
    monkeypatch.setattr('novelvideo.generators.image_generator.generate_character_reference_unified',route_provider)
    async with asset_project_lock(ctx.state_dir):
        call = (characters.generate_single_portrait('test','Lin',characters.PortraitGenRequest(),{}) if entry == 'route' else
            character_image._generate_character_portrait(character=cached,ethnicity='Chinese',output_dir=ctx.output_dir,
                style='',model='test',task_type='character_portrait',scope='',update=lambda *args:None,compiled_prompt='OLD',ctx=ctx))
        task = asyncio.create_task(call)
        await waiting.wait()
        writer = SQLiteStore('second',output_dir=str(ctx.output_dir),state_dir=str(ctx.state_dir))
        try:
            await writer.load_graph_state()
            await writer.update_character(changed_name,face_prompt='NEW')
        finally:
            await writer.close()
    if changed_name == 'Lin':
        with pytest.raises(HTTPException if entry == 'route' else ValueError,match='generation input'):
            await task
        assert calls == []
        assert not (ctx.output_dir / 'assets/characters/Lin/portrait.png').exists()
    else:
        await task
        assert len(calls) == 1


@pytest.mark.parametrize('entry', ['runner', 'route'])
@pytest.mark.parametrize('change', ['target', 'source', 'unrelated'])
async def test_late_provider_rechecks_target_and_related_source(project, monkeypatch, change, entry):
    from novelvideo.task_backend.runners import character_image
    source, ctx = project
    await source.add_character(NovelCharacter(name='Other'))
    adapter = AssetAdapter(source, ObjectStore(source.state_dir))
    base = await adapter.register_base('character', 'Lin')
    async def generate(**kwargs):
        if change == 'target':
            await upload('base', png('blue'))
        else:
            await source.update_character('Other' if change == 'unrelated' else 'Lin', description='human edit')
        path = Path(kwargs['output_path']); path.write_bytes(png('red')); return path
    monkeypatch.setattr(character_image, '_generate_grsai_image', generate)
    call = character_image._generate_character_portrait(character=source.get_character('Lin'), ethnicity='Chinese',
        output_dir=ctx.output_dir, style='', model='test', task_type='character_portrait', scope='',
        update=lambda *args: None, compiled_prompt='face', ctx=ctx)
    if entry == 'route':
        from novelvideo.api.routes import characters
        from fastapi import HTTPException
        call.close()
        async def provider(**kwargs):
            return [str(await generate(output_path=Path(kwargs['output_dir']) / 'result.png'))]
        monkeypatch.setattr('novelvideo.generators.image_generator.generate_character_reference_unified', provider)
        call = characters.generate_single_portrait('test','Lin',characters.PortraitGenRequest(),{})
    if change == 'unrelated':
        await call
        assert adapter.objects.read(base.base_asset_id)['revision'] == 2
    else:
        with pytest.raises(ValueError if entry == 'runner' else HTTPException) as error:
            await call
        assert 'changed during generation' in str(error.value)
        assert adapter.objects.read(base.base_asset_id)['revision'] == 2
        if change == 'target':
            assert (ctx.output_dir / 'assets/characters/Lin/portrait.png').read_bytes() == png('blue')


@pytest.mark.parametrize('drift', [None, 'canonical', 'provenance'])
async def test_old_after_proof_without_derived_fields_recovers_strictly(project, monkeypatch, drift):
    from novelvideo.creation import asset_media_publication as publication
    from novelvideo.creation.asset_sync import SourceSyncPending
    source, ctx = project
    await upload('base', png('red'))
    adapter = AssetAdapter(source, ObjectStore(source.state_dir)); base = await adapter.register_base('character', 'Lin')
    monkeypatch.setattr(publication, '_checkpoint', lambda p: (_ for _ in ()).throw(RuntimeError('lost')) if p == 'captured' else None)
    with pytest.raises(RuntimeError):
        await upload('base', png('blue'))
    def old(value):
        value = json.loads(value)
        for item in value['_media']:
            item.pop('current_blob'); item.pop('current_source')
        return json.dumps(value)
    with adapter.objects.transaction() as conn:
        version = conn.execute('SELECT payload FROM object_versions WHERE object_id=?', (base.base_asset_id,)).fetchone()[0]
        conn.execute('UPDATE object_versions SET payload=? WHERE object_id=?', (old(version), base.base_asset_id))
        entry = conn.execute('SELECT * FROM asset_media_publications').fetchone()
        for field in ('snapshot_before','source_before','source_after'):
            conn.execute(f'UPDATE asset_media_publications SET {field}=?', (old(entry[field]),))
    fixed = adapter.objects.read(base.base_asset_id, 1)
    if drift == 'canonical':
        (ctx.output_dir / 'assets/characters/Lin/portrait.png').write_bytes(png('green'))
    elif drift == 'provenance':
        path = Path(ctx.state_dir) / 'production_workflow.json'; state = json.loads(path.read_text())
        for version in state['versions']:
            version['generation_metadata'] = {**(version.get('generation_metadata') or {}), 'human': 'changed'}
        path.write_text(json.dumps(state))
    if drift:
        with pytest.raises(SourceSyncPending):
            await publication.reconcile_media_publications(source)
    else:
        await publication.reconcile_media_publications(source)
        assert adapter.objects.read(base.base_asset_id)['revision'] == 2
    assert adapter.objects.read(base.base_asset_id, 1) == fixed


@pytest.mark.parametrize('entry', ['base-route', 'portrait-route', 'state-route', 'portrait-runner', 'state-runner'])
async def test_real_generated_entrypoints_publish_and_candidates_stay_candidates(project, monkeypatch, entry):
    from novelvideo.api.routes import characters
    from novelvideo.task_backend.runners import character_image
    from novelvideo.character_visual.identity_sheet import IdentitySheetQualityReport
    source, ctx = project
    ctx.owner_project_label = 'test'; ctx.owner_username = 'actor'; ctx.project_name = 'test'
    await upload('portrait', png('green'))
    adapter = AssetAdapter(source, ObjectStore(source.state_dir)); base = await adapter.register_base('character', 'Lin')
    child = await adapter.register_variant(base.base_asset_id, 'id')
    monkeypatch.setattr(characters, 'load_project_config', lambda *args: {})
    monkeypatch.setattr(characters, '_resolve_character_image_model', lambda *args: 'test')
    counter = 0
    async def provider(**kwargs):
        nonlocal counter
        counter += 1
        path = Path(kwargs.get('output_path') or ctx.output_dir / f'generated-{counter}.png')
        path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(png('red' if counter == 1 else 'blue'))
        return path
    async def portraits(**kwargs):
        return [str(await provider(**kwargs))]
    async def state_image(**kwargs):
        await provider(**kwargs); return True
    async def quality(**kwargs):
        return IdentitySheetQualityReport(passed=True, checks={}, issues=[], style_family='3d_realistic')
    monkeypatch.setattr('novelvideo.generators.image_generator.generate_character_reference_unified', portraits)
    monkeypatch.setattr('novelvideo.generators.generate_character_reference_unified', portraits)
    monkeypatch.setattr('novelvideo.generators.image_generator.generate_identity_image_unified', state_image)
    monkeypatch.setattr(characters, 'assess_identity_sheet_quality', quality)
    monkeypatch.setattr(character_image, '_generate_grsai_image', provider)
    monkeypatch.setattr('novelvideo.character_visual.CharacterVisualWorkspaceStore.get_confirmed_bible', lambda *args: object())
    monkeypatch.setattr('novelvideo.character_visual.compile_visual_prompt_snapshot', lambda **kwargs: SimpleNamespace(prompt='face'))
    monkeypatch.setattr(character_image, 'get_task_manager', lambda: SimpleNamespace(update_progress_for_project=lambda *args, **kwargs: None))
    monkeypatch.setattr('novelvideo.api.deps.get_media_capability_store', lambda: object())
    monkeypatch.setattr('novelvideo.api.deps.get_media_credential_resolver', lambda: object())
    monkeypatch.setattr('novelvideo.media_capabilities.runtime.configuration.load_grsai_runtime_configuration', lambda *args: SimpleNamespace(model='test'))
    monkeypatch.setattr('novelvideo.media_capabilities.image.catalog.resolve_grsai_image_model', lambda **kwargs: SimpleNamespace(requested_model='test', model='test', resolution_source='explicit'))
    monkeypatch.setattr('novelvideo.project_config.load_project_config_file', lambda *args: {})
    async def generation(**kwargs):
        path = await provider(output_path=ctx.output_dir / f'candidate-{counter}.png')
        return character_image.CharacterStateGeneration(output_path=path,
            canonical_path=ctx.output_dir / 'assets/characters/Lin/identities/coat.png', reference_paths=(), prompt='state',
            state_id='id', raw_candidate_path=path, face_source=ctx.output_dir / 'assets/characters/Lin/identities/Lin_coat_portrait.png',
            quality_report=await quality())
    monkeypatch.setattr(character_image, '_generate_identity_image', generation)
    async def invoke():
        if entry == 'base-route':
            return await characters.generate_single_portrait('test','Lin',characters.PortraitGenRequest(),{})
        if entry == 'portrait-route':
            return await characters.generate_identity_portrait('test','Lin','id',characters.IdentityImageGenRequest(),{})
        if entry == 'state-route':
            return await characters.generate_identity_image('test','Lin','id',characters.IdentityImageGenRequest(),{})
        if entry == 'portrait-runner':
            return await character_image._generate_identity_portrait(store=source, character=source.get_character('Lin'),
                ethnicity='Chinese', identity_id='id', identity_name='coat', output_dir=ctx.output_dir, style='', model='test',
                task_type='character_portrait', scope='', update=lambda *args: None, ctx=ctx)
        return await character_image._run_character_image({'task_id':f'task-{counter}', 'payload':{
            'mode':'identity_image','character_name':'Lin','identity_id':'id','identity_name':'coat','model':'test'}}, ctx)
    result = await invoke()
    if isinstance(result, dict):
        assert result.get('ok', True), result
    oid = base.base_asset_id if entry == 'base-route' else child.variant_id
    first = adapter.objects.read(oid)
    assert first['revision'] == 2
    await invoke()
    assert adapter.objects.read(oid)['revision'] == (2 if entry.startswith('state-') else 3)


async def test_rename_alias_upload_and_clear_reject_retired_name(project, monkeypatch):
    from fastapi import HTTPException
    from novelvideo.api.routes import characters
    from novelvideo.production_workflow import ProductionWorkflowStore
    source, ctx = project
    await upload('state', png('red'))
    old_character = source.get_character('Lin').model_copy(deep=True)
    workflow = ProductionWorkflowStore(Path(ctx.state_dir) / 'production_workflow.json')
    workflow.materialize_legacy_current(slot_id='character:Lin:state:id', asset_kind='character_state',
        asset_path='assets/characters/Lin/identities/coat.png')
    adapter = AssetAdapter(source, ObjectStore(source.state_dir)); base = await adapter.register_base('character','Lin')
    child = await adapter.register_variant(base.base_asset_id,'id')
    fixed = adapter.objects.read(child.variant_id,1)
    await source.rename_character('Lin','New')
    old = ctx.output_dir / 'assets/characters/Lin/identities/coat.png'
    get = source.get_character
    monkeypatch.setattr(source,'get_character',lambda name: old_character if name == 'Lin' else get(name))
    with pytest.raises(HTTPException) as error:
        await upload('state',png('blue'))
    assert error.value.status_code == 409
    assert old.read_bytes() == png('red')
    await characters.upload_identity_image('test','New','coat',UploadFile(filename='image.png',file=io.BytesIO(png('blue'))),{})
    media = adapter.objects.read(child.variant_id)['payload']['_media'][0]
    # A materialized legacy version uses the canonical itself, not an immutable
    # generated candidate. Its actual bytes change, with no generation attempt.
    assert (ctx.output_dir / media['current_blob']['path']).read_bytes() == png('blue')
    assert media['current_source']['source_attempt_id'] is None
    assert adapter.objects.read(child.variant_id,1) == fixed
    assert (ctx.output_dir / fixed['payload']['_media'][0]['current_blob']['path']).read_bytes() == png('red')
    await characters.delete_identity_image('test','New','id',{})
    assert adapter.objects.read(child.variant_id)['payload']['_media'] == []
    current = ProductionWorkflowStore(workflow.state_path)
    assert current.get_slot('character:New:state:id')[0].current_version_id is None
    with pytest.raises(ValueError, match='retired'):
        current.clear_current(slot_id='character:Lin:state:id')


async def test_old_enriched_snapshot_accepts_explicit_source_write_without_rewriting_history(project):
    source, ctx = project
    await upload('base',png('red'))
    adapter = AssetAdapter(source,ObjectStore(source.state_dir)); base = await adapter.register_base('character','Lin')
    before = adapter.objects.read(base.base_asset_id,1)
    for item in before['payload']['_media']:
        item.pop('current_blob'); item.pop('current_source')
    with adapter.objects.transaction() as conn:
        conn.execute('UPDATE object_versions SET payload=? WHERE object_id=?',(json.dumps(before['payload']),base.base_asset_id))
    await source.update_character('Lin',description='explicit metadata')
    assert adapter.objects.read(base.base_asset_id)['revision'] == 2
    assert adapter.objects.read(base.base_asset_id,1) == before


async def test_known_legacy_portrait_layout_is_captured_without_workflow(project):
    source, ctx = project
    path = ctx.output_dir / 'assets/characters/Lin/identities/coat_portrait.png'
    path.parent.mkdir(parents=True,exist_ok=True); path.write_bytes(png('red'))
    adapter = AssetAdapter(source,ObjectStore(source.state_dir)); base = await adapter.register_base('character','Lin')
    child = await adapter.register_variant(base.base_asset_id,'id')
    media = adapter.objects.read(child.variant_id)['payload']['_media'][0]
    assert media['current_source']['kind'] == 'legacy_canonical'
    assert (ctx.output_dir / media['current_blob']['path']).read_bytes() == png('red')
    assert not (Path(ctx.state_dir) / 'production_workflow.json').exists()


@pytest.mark.parametrize('key,value', [('kind','canonical_override'),('path','assets/other.png'),('source_attempt_id','forged')])
def test_media_fingerprint_keeps_unknown_source_facts_and_conflicting_current_blob(key, value):
    from novelvideo.creation.asset_sync import fingerprint
    item = {'blob':{'sha256':'original','path':'assets/image.png'}, 'source_attempt_id':None,'workflow_persisted':True}
    original = {'_media':[item]}
    derived = {'_media':[{**item,'current_blob':item['blob'],'current_source':{
        'kind':'workflow','path':'assets/image.png','source_attempt_id':None}}]}
    assert fingerprint(original) == fingerprint(derived)
    altered = json.loads(json.dumps(derived))
    altered['_media'][0]['current_source'][key] = value
    assert fingerprint(original) != fingerprint(altered)
    derived['_media'][0]['current_source']['new_provenance'] = 'human'
    assert fingerprint(original) != fingerprint(derived)
    assert fingerprint(original) != fingerprint({'_media':[{**item,'current_blob':{'sha256':'other'}}]})
