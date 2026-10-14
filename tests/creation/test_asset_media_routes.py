"""Real retained production routes publish fixed semantic current media."""
from pathlib import Path
from types import SimpleNamespace
import asyncio

import pytest

from novelvideo.api.routes import production_assets as routes
from novelvideo.creation.asset_adapter import AssetAdapter
from novelvideo.creation.store import ObjectStore
from novelvideo.models import NovelCharacter
from novelvideo.sqlite_store import SQLiteStore


@pytest.fixture
async def route_project(tmp_path, monkeypatch):
    source = SQLiteStore('test', output_dir=str(tmp_path / 'output'), state_dir=str(tmp_path / 'state'))
    await source.initialize()
    await source.add_character(NovelCharacter(name='Lin'))
    async def resolve(*args, **kwargs):
        assert kwargs['required_role'] == 'editor'
        return SimpleNamespace(project_dir=Path(source.project_dir), state_dir=source.state_dir,
                               ctx=SimpleNamespace(owner_project_label='test', requester_user_id='actor'))
    monkeypatch.setattr(routes, 'resolve_project_scope', resolve)
    yield source
    await source.close()


def file(source, name, content):
    path = Path(source.project_dir) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return name


async def candidate(source, version, *, slot='character:Lin:portrait'):
    path = file(source, f'assets/{version}.png', version.encode())
    return await routes.register_production_asset_candidate('test', slot,
        routes.CandidateVersionRequest(asset_kind='character_portrait', asset_path=path,
                                       version_id=version, qc_passed=True), {'id': 'actor'})


@pytest.mark.parametrize('registered', [False, True])
@pytest.mark.parametrize('entry', ['materialize', 'candidate'])
async def test_first_current_routes_publish_provisional_and_preserve_legacy(route_project, registered, entry):
    source = route_project
    adapter = AssetAdapter(source, ObjectStore(source.state_dir)) if registered else None
    base = await adapter.register_base('character', 'Lin') if adapter else None
    if entry == 'materialize':
        path = file(source, 'assets/import.png', b'imported')
        result = await routes.materialize_legacy_asset('test', 'character:Lin:portrait',
            routes.LegacyAssetRequest(asset_kind='character_portrait', asset_path=path), {'id': 'actor'})
    else:
        result = await candidate(source, 'first')
    assert result['ok']
    if adapter:
        obj = adapter.objects.read(base.base_asset_id)
        assert obj['revision'] == 2
        assert obj['payload']['_media'][0]['adoption_status'] == 'provisional'
        assert adapter.objects.read(base.base_asset_id, 1)['payload']['_media'] == []
    else:
        assert not (Path(source.state_dir) / 'creation').exists()


async def test_existing_current_candidate_delete_and_current_fallback_keep_history(route_project):
    source = route_project
    adapter = AssetAdapter(source, ObjectStore(source.state_dir))
    base = await adapter.register_base('character', 'Lin')
    await candidate(source, 'first')
    first = adapter.objects.read(base.base_asset_id)
    assert first['revision'] == 2
    frozen = first['payload']['_media'][0]['blob']
    await candidate(source, 'second')
    assert adapter.objects.read(base.base_asset_id)['revision'] == 2
    await routes.adopt_production_asset_version('test', 'character:Lin:portrait', 'second',
        routes.AdoptVersionRequest(), {'id': 'actor'})
    assert adapter.objects.read(base.base_asset_id)['revision'] == 3
    await candidate(source, 'unused')
    await routes.delete_production_asset_version('test', 'character:Lin:portrait', 'unused', {'id': 'actor'})
    assert adapter.objects.read(base.base_asset_id)['revision'] == 3
    await routes.delete_production_asset_version('test', 'character:Lin:portrait', 'second', {'id': 'actor'})
    fallback = adapter.objects.read(base.base_asset_id)
    assert fallback['revision'] == 4
    assert fallback['payload']['_media'][0]['version_id'] == 'first'
    await routes.delete_production_asset_version('test', 'character:Lin:portrait', 'first', {'id': 'actor'})
    assert adapter.objects.read(base.base_asset_id)['revision'] == 5
    assert adapter.objects.read(base.base_asset_id)['payload']['_media'] == []
    assert not (Path(source.project_dir) / 'assets/first.png').exists()
    assert (Path(source.project_dir) / frozen['path']).read_bytes() == b'first'
    assert adapter.objects.read(base.base_asset_id, 2)['payload'] == first['payload']


async def test_delete_unregistered_keeps_creation_absent(route_project):
    source = route_project
    await candidate(source, 'first')
    await routes.delete_production_asset_version('test', 'character:Lin:portrait', 'first', {'id': 'actor'})
    assert not (Path(source.state_dir) / 'creation').exists()
    assert not (Path(source.project_dir) / 'assets/first.png').exists()


async def test_rejected_delete_and_unapproved_candidate_do_not_advance(route_project):
    from fastapi import HTTPException
    source = route_project
    adapter = AssetAdapter(source, ObjectStore(source.state_dir))
    base = await adapter.register_base('character', 'Lin')
    path = file(source, 'assets/rejected.png', b'candidate')
    await routes.register_production_asset_candidate('test', 'character:Lin:portrait',
        routes.CandidateVersionRequest(asset_kind='character_portrait', asset_path=path,
                                       version_id='rejected', qc_passed=False), {'id':'actor'})
    assert adapter.objects.read(base.base_asset_id)['revision'] == 1
    assert adapter.objects.read(base.base_asset_id)['payload']['_media'] == []
    with pytest.raises(HTTPException) as error:
        await routes.delete_production_asset_version('test', 'character:Lin:portrait', 'missing', {'id':'actor'})
    assert error.value.status_code == 404
    assert adapter.objects.read(base.base_asset_id)['revision'] == 1
    with adapter.objects.transaction() as conn:
        assert not conn.execute('SELECT 1 FROM asset_media_publications').fetchone()


@pytest.mark.parametrize('entry', ['materialize', 'candidate', 'delete'])
@pytest.mark.parametrize('phase', ['published', 'captured'])
@pytest.mark.parametrize('cancel', [False, True])
async def test_real_route_interruption_retains_proof_and_history(route_project, monkeypatch, entry, phase, cancel):
    from fastapi import HTTPException
    from novelvideo.creation import asset_media_publication as publication
    from novelvideo.creation.asset_sync import SourceSyncPending
    source = route_project
    adapter = AssetAdapter(source, ObjectStore(source.state_dir))
    base = await adapter.register_base('character', 'Lin')
    path = file(source, 'assets/import.png', b'import')
    if entry == 'delete':
        await candidate(source, 'first')
    before = adapter.objects.read(base.base_asset_id)
    async def invoke():
        if entry == 'materialize':
            return await routes.materialize_legacy_asset('test', 'character:Lin:portrait',
                routes.LegacyAssetRequest(asset_kind='character_portrait', asset_path=path), {'id': 'actor'})
        if entry == 'candidate':
            return await candidate(source, 'first')
        return await routes.delete_production_asset_version('test', 'character:Lin:portrait', 'first', {'id': 'actor'})
    def fail(at):
        if at == phase:
            raise asyncio.CancelledError() if cancel else RuntimeError('interrupted')
    monkeypatch.setattr(publication, '_checkpoint', fail)
    with pytest.raises(asyncio.CancelledError if cancel else RuntimeError):
        await invoke()
    with pytest.raises(SourceSyncPending):
        adapter.objects.read(base.base_asset_id)
    assert adapter.objects.read(base.base_asset_id, before['revision']) == before
    monkeypatch.setattr(publication, '_checkpoint', lambda _: None)
    if phase == 'captured':
        await publication.reconcile_media_publications(source)
        assert adapter.objects.read(base.base_asset_id)['revision'] == before['revision'] + 1
    else:
        with pytest.raises(HTTPException) as error:
            await invoke()
        assert error.value.status_code == 409
        assert error.value.detail['code'] == 'asset_media_pending'
        with pytest.raises(SourceSyncPending):
            await publication.reconcile_media_publications(source)


async def test_rename_alias_and_delete_canonical_fallback(route_project):
    source = route_project
    file(source, 'assets/characters/Lin/portrait.png', b'first')
    adapter = AssetAdapter(source, ObjectStore(source.state_dir))
    base = await adapter.register_base('character', 'Lin')
    await candidate(source, 'first')
    await source.rename_character('Lin', 'New')
    slot = 'character:New:portrait'
    canonical = file(source, 'assets/characters/New/portrait.png', b'first')
    second = file(source, 'assets/second.png', b'second')
    await routes.register_production_asset_candidate('test', slot,
        routes.CandidateVersionRequest(asset_kind='character_portrait', asset_path=second,
            version_id='second', qc_passed=True, generation_metadata={'canonical_path': canonical}), {'id':'actor'})
    await routes.adopt_production_asset_version('test', slot, 'second', routes.AdoptVersionRequest(), {'id':'actor'})
    old = adapter.objects.read(base.base_asset_id)
    assert (Path(source.project_dir) / canonical).read_bytes() == b'second'
    await routes.delete_production_asset_version('test', slot, 'second', {'id':'actor'})
    assert (Path(source.project_dir) / canonical).read_bytes() == b'first'
    current = adapter.objects.read(base.base_asset_id)
    assert current['payload']['_media'][0]['slot_id'] == slot
    assert current['payload']['_media'][0]['version_id'] == 'first'
    blob = old['payload']['_media'][0]['blob']
    assert (Path(source.project_dir) / blob['path']).read_bytes() == b'second'


@pytest.mark.parametrize('attack', ['candidate-source', 'candidate-target', 'old-candidate-delete', 'adopt-target', 'delete-target'])
async def test_routes_cannot_mutate_fixed_history_namespace(route_project, attack):
    import json
    from fastapi import HTTPException
    source = route_project
    adapter = AssetAdapter(source, ObjectStore(source.state_dir))
    base = await adapter.register_base('character', 'Lin')
    await candidate(source, 'first')
    archived = adapter.objects.read(base.base_asset_id, 2)['payload']['_media'][0]['blob']
    await candidate(source, 'second')
    await routes.adopt_production_asset_version('test', 'character:Lin:portrait', 'second', routes.AdoptVersionRequest(), {'id':'actor'})
    sidecar = Path(source.state_dir) / 'production_workflow.json'
    if attack not in ('candidate-source', 'candidate-target'):
        await candidate(source, 'third')
        data = json.loads(sidecar.read_text())
        for version in data['versions']:
            if version['version_id'] == ('second' if attack == 'delete-target' else 'third'):
                if attack == 'old-candidate-delete':
                    version['asset_path'] = archived['path']
                else:
                    version['generation_metadata'] = {'canonical_path': archived['path']}
        sidecar.write_text(json.dumps(data))
        if attack == 'delete-target':
            # Simulate a previously registered unsafe current, before protections
            # existed. Explicit refresh records evidence without mutating files.
            await adapter.refresh_source(base.base_asset_id, expected_revision=3)
    before_workflow = sidecar.read_bytes()
    before = adapter.objects.read(base.base_asset_id)
    archive = Path(source.project_dir) / archived['path']
    with pytest.raises(HTTPException) as error:
        if attack in ('candidate-source', 'candidate-target'):
            await routes.register_production_asset_candidate('test', 'character:Lin:portrait',
                routes.CandidateVersionRequest(asset_kind='character_portrait',
                    asset_path=archived['path'] if attack == 'candidate-source' else 'assets/second.png',
                    generation_metadata={'canonical_path': archived['path']} if attack == 'candidate-target' else None,
                    version_id='third', qc_passed=False), {'id':'actor'})
        elif attack == 'adopt-target':
            await routes.adopt_production_asset_version('test', 'character:Lin:portrait', 'third', routes.AdoptVersionRequest(), {'id':'actor'})
        else:
            await routes.delete_production_asset_version('test', 'character:Lin:portrait',
                'third' if attack == 'old-candidate-delete' else 'second', {'id':'actor'})
    assert error.value.status_code == 409
    assert sidecar.read_bytes() == before_workflow
    assert adapter.objects.read(base.base_asset_id) == before
    assert archive.read_bytes() == b'first'
    assert adapter.objects.read(base.base_asset_id, 2)['payload']['_media'][0]['blob'] == archived
    assert (Path(source.project_dir) / 'assets/second.png').read_bytes() == b'second'
