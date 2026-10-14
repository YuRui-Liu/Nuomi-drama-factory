"""Real publishing entry points and immutable semantic media evidence."""
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
import json
import sqlite3
import asyncio
import errno

import pytest

from novelvideo.creation.asset_adapter import AssetAdapter
from novelvideo.creation.store import ObjectStore
from novelvideo.models import NovelCharacter, NovelScene, CharacterIdentity
from novelvideo.production_workflow.store import ProductionWorkflowStore
from novelvideo.sqlite_store import SQLiteStore
from tests.character_visual.test_casting_adoption import adoption_env


@pytest.fixture
async def media_project(tmp_path):
    source = SQLiteStore('test', output_dir=str(tmp_path / 'output'), state_dir=str(tmp_path / 'state'))
    await source.initialize()
    await source.add_character(NovelCharacter(name='Lin'))
    await source.add_character_identity('Lin', CharacterIdentity(identity_id='identity', character_name='Lin', identity_name='coat'))
    await source.add_scene(NovelScene(name='Hall', stale_reference_kinds=['master']))
    await source.add_scene(NovelScene(name='Night', base_scene_id='Hall', variant_id='night'))
    adapter = AssetAdapter(source, ObjectStore(source.state_dir))
    yield source, adapter
    await source.close()


def workflow(source):
    return ProductionWorkflowStore(Path(source.state_dir) / 'production_workflow.json')


def candidate(source, slot='character:Lin:portrait', version='candidate', content=b'new-image', *, provisional=False, kind='character_portrait', canonical=None):
    root = Path(source.project_dir)
    path = root / 'assets' / (version + '.png')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    workflow(source).register_candidate_version(slot_id=slot, asset_kind=kind, version_id=version,
        asset_path=path.relative_to(root).as_posix(), source_attempt_id='generation-task', qc_passed=True,
        generation_metadata={'provider': 'test', **({'canonical_path': canonical} if canonical else {})},
        actor='test', at=datetime.now(timezone.utc), auto_provisional=provisional)
    return path


def media(adapter, ref, revision=None):
    return adapter.objects.read(ref.variant_id or ref.base_asset_id, revision)['payload']['_media']


async def adopt_production(source, monkeypatch, slot, version):
    from novelvideo.api.routes import production_assets
    async def resolve(*args, **kwargs):
        return SimpleNamespace(project_dir=Path(source.project_dir), state_dir=source.state_dir,
            ctx=SimpleNamespace(owner_project_label='test', requester_user_id='actor'))
    monkeypatch.setattr(production_assets, 'resolve_project_scope', resolve)
    return await production_assets.adopt_production_asset_version('p', slot, version,
        production_assets.AdoptVersionRequest(reason='chosen'), {'id': 'actor'})


async def test_registration_captures_provisional_not_candidate_and_freezes_same_path(media_project):
    source, adapter = media_project
    path = candidate(source, provisional=True, content=b'first')
    candidate(source, version='not-adopted')
    base = await adapter.register_base('character', 'Lin')
    first = media(adapter, base)[0]
    assert first['version_id'] == 'candidate'
    assert first['adoption_status'] == 'provisional'
    assert first['source_attempt_id'] == 'generation-task'
    assert first['provenance']['provider'] == 'test'
    archived = Path(source.project_dir) / first['blob']['path']
    assert archived.read_bytes() == b'first'
    path.write_bytes(b'same path different bytes')
    updated = await adapter.refresh_source(base.base_asset_id, expected_revision=1)
    assert updated.revision == 2
    assert media(adapter, base)[0]['blob']['sha256'] != first['blob']['sha256']
    assert archived.read_bytes() == b'first'
    assert media(adapter, base, 1) == [first]


async def test_precise_base_variant_and_scene_ownership(media_project):
    source, adapter = media_project
    for slot, kind, version in [('character:Lin:portrait','character_portrait','base'),
        ('character:Lin:identity:identity:portrait','character_portrait','face'),
        ('character:Lin:state:identity','character_state','sheet'),
        ('scene:Hall:base:master','scene_base','hall'), ('scene:Hall:state:Night:master','scene_state','night')]:
        candidate(source, slot, version, provisional=True, kind=kind)
    base = await adapter.register_base('character', 'Lin')
    variant = await adapter.register_variant(base.base_asset_id, 'identity')
    scene = await adapter.register_base('scene', 'Hall')
    state = await adapter.register_variant(scene.base_asset_id, 'Night')
    assert [m['version_id'] for m in media(adapter, base)] == ['base']
    assert {m['version_id'] for m in media(adapter, variant)} == {'face','sheet'}
    assert [m['version_id'] for m in media(adapter, scene)] == ['hall']
    assert [m['version_id'] for m in media(adapter, state)] == ['night']


async def test_actual_production_adoption_advances_snapshot_and_replay_does_not(media_project, monkeypatch):
    source, adapter = media_project
    candidate(source, version='before', content=b'old', provisional=True)
    base = await adapter.register_base('character', 'Lin')
    canonical = Path(source.project_dir) / 'assets/characters/Lin/portrait.png'
    canonical.parent.mkdir(parents=True)
    canonical.write_bytes(b'old')
    candidate(source, canonical='assets/characters/Lin/portrait.png')
    assert (await adopt_production(source, monkeypatch, 'character:Lin:portrait', 'candidate'))['ok']
    assert adapter.objects.read(base.base_asset_id)['revision'] == 2
    assert media(adapter, base)[0]['adoption_status'] == 'adopted'
    assert media(adapter, base)[0]['version_id'] == 'candidate'
    assert canonical.read_bytes() == b'new-image'
    await adopt_production(source, monkeypatch, 'character:Lin:portrait', 'candidate')
    assert adapter.objects.read(base.base_asset_id)['revision'] == 2
    await adapter.publish_revision(base.base_asset_id, None, 2, {'face_prompt':'new face'})
    assert media(adapter, base)[0]['version_id'] == 'candidate'


def test_fallback_interruption_and_crash_remnant_are_not_accepted(tmp_path, monkeypatch):
    from novelvideo.creation import asset_media
    source = tmp_path / 'source.png'
    source.write_bytes(b'complete media')
    def unsupported(*args):
        raise OSError(errno.ENOTSUP, 'no hardlinks')
    monkeypatch.setattr(asset_media.os, 'link', unsupported)
    original = asset_media._digest
    def interrupted(path, destination=None):
        if destination is not None and Path(path) != source:
            destination.write(b'partial')
            raise asyncio.CancelledError()
        return original(path, destination)
    monkeypatch.setattr(asset_media, '_digest', interrupted)
    with pytest.raises(asyncio.CancelledError):
        asset_media.capture_blob(tmp_path, source, archive=True)
    monkeypatch.setattr(asset_media, '_digest', original)
    blob = asset_media.capture_blob(tmp_path, source, archive=False)
    target = tmp_path / blob['path']
    assert not target.exists()
    # A process-killed copy cannot clean up. It must not be overwritten or
    # mistaken for a successfully published content-addressed file on restart.
    target.write_bytes(b'crash remnant')
    with pytest.raises(ValueError, match='digest mismatch'):
        asset_media.capture_blob(tmp_path, source, archive=True)
    assert target.read_bytes() == b'crash remnant'
    target.unlink()  # Explicit operator removal of proven corrupt orphan.
    asset_media.capture_blob(tmp_path, source, archive=True)
    assert target.read_bytes() == source.read_bytes()


@pytest.mark.parametrize('failure', [errno.EACCES, errno.ENOSPC])
def test_archive_does_not_hide_real_storage_errors(tmp_path, monkeypatch, failure):
    from novelvideo.creation import asset_media
    source = tmp_path / 'source.png'
    source.write_bytes(b'media')
    def failed(*args):
        raise OSError(failure, 'real storage failure')
    monkeypatch.setattr(asset_media.os, 'link', failed)
    with pytest.raises(OSError) as error:
        asset_media.capture_blob(tmp_path, source, archive=True)
    assert error.value.errno == failure


@pytest.mark.parametrize('identity', [None, 'old'])
async def test_actual_casting_adoption_and_retry_snapshot_parent_child_once(adoption_env, identity):
    env = await adoption_env(identity)
    adapter = AssetAdapter(env.sql, ObjectStore(env.ctx.state_dir))
    base = await adapter.register_base('character', '甲')
    child = await adapter.register_variant(base.base_asset_id, 'old')
    result = await env.adopt()
    target = child if identity else base
    evidence = media(adapter, target)
    assert evidence[0]['version_id'] == result['version_id']
    assert evidence[0]['adoption_status'] == 'adopted'
    assert adapter.objects.read(target.variant_id or target.base_asset_id)['revision'] == 2
    revisions = [adapter.objects.read(ref.variant_id or ref.base_asset_id)['revision'] for ref in (base, child)]
    assert await env.adopt() == result
    assert [adapter.objects.read(ref.variant_id or ref.base_asset_id)['revision'] for ref in (base, child)] == revisions


@pytest.mark.parametrize('phase', ['prepared', 'published', 'captured', 'committed'])
@pytest.mark.parametrize('cancel', [False, True])
async def test_casting_media_durable_windows(adoption_env, monkeypatch, phase, cancel):
    from novelvideo.creation import asset_media_publication as publication
    from novelvideo.creation.asset_sync import SourceSyncPending
    env = await adoption_env()
    adapter = AssetAdapter(env.sql, ObjectStore(env.ctx.state_dir))
    base = await adapter.register_base('character', '甲')
    def fail(at):
        if at == phase:
            raise asyncio.CancelledError() if cancel else RuntimeError('process lost')
    monkeypatch.setattr(publication, '_checkpoint', fail)
    with pytest.raises(asyncio.CancelledError if cancel else RuntimeError):
        await env.adopt()
    assert (await adapter.resolve(base))['revision'] == 1
    if phase != 'committed':
        with pytest.raises(SourceSyncPending):
            adapter.objects.read(base.base_asset_id)
        with pytest.raises(SourceSyncPending):
            await env.sql.update_character('甲', face_prompt='blocked')
    monkeypatch.setattr(publication, '_checkpoint', lambda _: None)
    if phase == 'published':
        # A committed native adoption without semantic after proof is unknown;
        # retry must not pretend the visible media was the intended result.
        with pytest.raises(SourceSyncPending):
            await env.adopt()
        with pytest.raises(SourceSyncPending):
            await publication.reconcile_media_publications(env.sql)
    else:
        await publication.reconcile_media_publications(env.sql)
        result = await env.adopt()
        assert adapter.objects.read(base.base_asset_id)['revision'] == 2
        assert media(adapter, base)[0]['version_id'] == result['version_id']


async def test_scene_adoption_includes_clear_stale_and_rename(media_project, monkeypatch):
    source, adapter = media_project
    old_path = candidate(source, 'scene:Hall:base:master', 'old', provisional=True, kind='scene_base')
    base = await adapter.register_base('scene', 'Hall')
    await source.rename_scene('Hall', 'Room')
    path = candidate(source, 'scene:Room:base:master', 'new', kind='scene_base')
    store = workflow(source)
    # These are trusted workflow facts, not adoption request fields.
    saved = json.loads(store.state_path.read_text())
    for version in saved['versions']:
        if version['version_id'] == 'new':
            version['generation_metadata'].update(scene_id='Room', base_scene_id='Room', anchor_kind='master', canonical_path='assets/scenes/Room/master.png')
    store.state_path.write_text(json.dumps(saved))
    await adopt_production(source, monkeypatch, 'scene:Room:base:master', 'new')
    current = adapter.objects.read(base.base_asset_id)
    assert current['revision'] == 3
    assert current['payload']['stale_reference_kinds'] == []
    assert current['payload']['_media'][0]['slot_id'] == 'scene:Room:base:master'
    assert (Path(source.project_dir) / media(adapter, base, 1)[0]['blob']['path']).read_bytes() == old_path.read_bytes()


async def test_scene_clear_stale_failure_stays_pending(media_project, monkeypatch):
    from novelvideo.api.routes import production_assets
    from novelvideo.creation.asset_sync import SourceSyncPending
    from fastapi import HTTPException
    source, adapter = media_project
    base = await adapter.register_base('scene', 'Hall')
    candidate(source, 'scene:Hall:base:master', kind='scene_base', canonical='assets/scenes/Hall/master.png')
    store = workflow(source)
    payload = json.loads(store.state_path.read_text())
    payload['versions'][0]['generation_metadata'].update(scene_id='Hall', anchor_kind='master')
    store.state_path.write_text(json.dumps(payload))
    async def interrupted(*args):
        raise RuntimeError('clear stale failed')
    monkeypatch.setattr(production_assets, '_clear_adopted_scene_stale_reference', interrupted)
    with pytest.raises(RuntimeError, match='clear stale'):
        await adopt_production(source, monkeypatch, 'scene:Hall:base:master', 'candidate')
    with pytest.raises(SourceSyncPending):
        adapter.objects.read(base.base_asset_id)
    with pytest.raises(HTTPException) as error:
        await adopt_production(source, monkeypatch, 'scene:Hall:base:master', 'candidate')
    assert error.value.status_code == 409 and error.value.detail['code'] == 'asset_media_pending'
    assert adapter.objects.read(base.base_asset_id, 1)['revision'] == 1


async def test_old_flat_snapshot_upgrades_only_on_explicit_write(media_project):
    source, adapter = media_project
    candidate(source, provisional=True)
    base = await adapter.register_base('character', 'Lin')
    with adapter.objects.transaction() as conn:
        payload = adapter.objects.read(base.base_asset_id, 1)['payload']
        payload.pop('_media')
        conn.execute('UPDATE object_versions SET payload=? WHERE object_id=?', (json.dumps(payload), base.base_asset_id))
    before = adapter.objects.db_path.read_bytes()
    assert '_media' not in (await adapter.resolve(base))['payload']
    assert '_media' not in adapter.objects.read(base.base_asset_id)['payload']
    assert adapter.objects.db_path.read_bytes() == before
    await source.update_character('Lin', face_prompt='explicit edit')
    assert adapter.objects.read(base.base_asset_id)['revision'] == 2
    assert media(adapter, base)[0]['version_id'] == 'candidate'
    assert '_media' not in adapter.objects.read(base.base_asset_id, 1)['payload']


async def test_failed_metadata_write_does_not_upgrade_flat_snapshot(media_project, monkeypatch):
    from novelvideo.creation.asset_sync import SourceSync
    source, adapter = media_project
    base = await adapter.register_base('character', 'Lin')
    with adapter.objects.transaction() as conn:
        payload = adapter.objects.read(base.base_asset_id, 1)['payload']
        payload.pop('_media')
        conn.execute('UPDATE object_versions SET payload=? WHERE object_id=?', (json.dumps(payload), base.base_asset_id))
    sync = SourceSync(source, adapter.objects)
    token = await sync.prepare('character', {'Lin'})
    await sync.failed_write(token)
    assert adapter.objects.read(base.base_asset_id)['revision'] == 1


@pytest.mark.parametrize('path_case', ['outside', 'symlink', 'archive_symlink'])
async def test_snapshot_path_boundaries(media_project, tmp_path, path_case):
    source, adapter = media_project
    path = candidate(source, provisional=True)
    if path_case == 'archive_symlink':
        (Path(source.project_dir) / 'assets/.creation-media').symlink_to(tmp_path, target_is_directory=True)
    else:
        outside = tmp_path / 'outside.png'
        outside.write_bytes(b'outside')
        if path_case == 'symlink':
            path.unlink()
            path.symlink_to(outside)
        else:
            store = workflow(source)
            payload = json.loads(store.state_path.read_text())
            payload['versions'][0]['asset_path'] = str(outside)
            store.state_path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match='[Ss]ymlink|controlled'):
        await adapter.register_base('character', 'Lin')
    with adapter.objects.transaction() as conn:
        assert conn.execute('SELECT count(*) FROM asset_sources').fetchone()[0] == 0


async def test_after_proof_refuses_human_drift_and_keeps_fixed_history(media_project, monkeypatch):
    from novelvideo.creation import asset_media_publication as publication
    from novelvideo.creation.asset_sync import SourceSyncPending
    source, adapter = media_project
    base = await adapter.register_base('character', 'Lin')
    path = candidate(source)
    monkeypatch.setattr(publication, '_checkpoint', lambda p: (_ for _ in ()).throw(RuntimeError('lost')) if p == 'captured' else None)
    with pytest.raises(RuntimeError):
        await adopt_production(source, monkeypatch, 'character:Lin:portrait', 'candidate')
    path.write_bytes(b'unrelated human replacement')
    with pytest.raises(SourceSyncPending):
        await publication.reconcile_media_publications(source)
    assert path.read_bytes() == b'unrelated human replacement'
    assert adapter.objects.read(base.base_asset_id, 1)['revision'] == 1


async def test_media_owner_is_task_exact_and_does_not_suppress_independent_asset(media_project):
    from novelvideo.creation.asset_media_publication import media_publication, publication_targets
    from novelvideo.creation.asset_sync import SourceSync, SourceSyncPending
    source, adapter = media_project
    base = await adapter.register_base('character', 'Lin')
    scene = await adapter.register_base('scene', 'Hall')
    async with media_publication(source, ['character:Lin:portrait'], 'test-owner', 'test'):
        assert publication_targets(source.state_dir) == {base.base_asset_id}
        async def child():
            assert not publication_targets(source.state_dir)
            with pytest.raises(SourceSyncPending):
                await SourceSync(source, adapter.objects).prepare('character', {'Lin'})
        await asyncio.create_task(child())
        await source.update_scene('Hall', notes='independent')
    assert adapter.objects.read(base.base_asset_id)['revision'] == 1
    assert adapter.objects.read(scene.base_asset_id)['revision'] == 2


async def test_casting_real_route_reports_pending_409(adoption_env, monkeypatch):
    from contextlib import asynccontextmanager
    from novelvideo.api.routes import character_casting
    from novelvideo.creation import asset_media_publication as publication
    from fastapi import HTTPException
    env = await adoption_env()
    adapter = AssetAdapter(env.sql, ObjectStore(env.ctx.state_dir))
    await adapter.register_base('character', '甲')
    monkeypatch.setattr(publication, '_checkpoint', lambda p: (_ for _ in ()).throw(RuntimeError('lost')) if p == 'published' else None)
    with pytest.raises(RuntimeError):
        await env.adopt()
    @asynccontextmanager
    async def scoped(*args, **kwargs):
        yield env.ctx, env.sql, env.sql.get_character('甲')
    monkeypatch.setattr(character_casting, 'scope', scoped)
    monkeypatch.setattr(character_casting, 'owned_candidate', lambda *args: None)
    with pytest.raises(HTTPException) as error:
        await character_casting.adopt('project', '甲', 'candidate', env.command, user={'id':'trusted-user'})
    assert error.value.status_code == 409
    assert error.value.detail['code'] == 'asset_media_pending'


@pytest.mark.parametrize('kind,name,relative', [('character','Lin','assets/characters/Lin/portrait.png'),
                                              ('scene','Hall','assets/scenes/Hall/master.png')])
async def test_explicit_registration_freezes_existing_legacy_media_without_materializing_workflow(media_project, kind, name, relative):
    source, adapter = media_project
    path = Path(source.project_dir) / relative
    path.parent.mkdir(parents=True)
    path.write_bytes(b'legacy actual media')
    ref = await adapter.register_base(kind, name)
    current = media(adapter, ref)[0]
    assert current['adoption_status'] == 'provisional'
    assert current['workflow_persisted'] is False
    assert current['origin'] == 'legacy_import'
    assert (Path(source.project_dir) / current['blob']['path']).read_bytes() == b'legacy actual media'
    assert not (Path(source.state_dir) / 'production_workflow.json').exists()


async def test_legacy_casting_does_not_initialize_creation(adoption_env):
    env = await adoption_env()
    await env.adopt()
    assert not (env.ctx.state_dir / 'creation').exists()


async def test_registered_casting_rollback_does_not_advance_snapshot(adoption_env, monkeypatch):
    from novelvideo.character_visual import casting_adoption
    env = await adoption_env()
    adapter = AssetAdapter(env.sql, ObjectStore(env.ctx.state_dir))
    base = await adapter.register_base('character', '甲')
    def fail(phase):
        if phase == 'workflow_before_commit':
            raise RuntimeError('native rollback')
    monkeypatch.setattr(casting_adoption, '_checkpoint', fail)
    with pytest.raises(RuntimeError, match='native rollback'):
        await env.adopt()
    assert adapter.objects.read(base.base_asset_id)['revision'] == 1
    monkeypatch.setattr(casting_adoption, '_checkpoint', lambda _: None)
    await env.adopt()
    assert adapter.objects.read(base.base_asset_id)['revision'] == 2


async def test_reconcile_refuses_corrupt_content_archive(media_project, monkeypatch):
    from novelvideo.creation import asset_media_publication as publication
    source, adapter = media_project
    base = await adapter.register_base('character', 'Lin')
    candidate(source)
    monkeypatch.setattr(publication, '_checkpoint', lambda p: (_ for _ in ()).throw(RuntimeError('lost')) if p == 'captured' else None)
    with pytest.raises(RuntimeError):
        await adopt_production(source, monkeypatch, 'character:Lin:portrait', 'candidate')
    with adapter.objects.transaction() as conn:
        proof = json.loads(conn.execute('SELECT source_after FROM asset_media_publications').fetchone()[0])
    archive = Path(source.project_dir) / proof['_media'][0]['blob']['path']
    archive.write_bytes(b'corrupt archive')
    with pytest.raises(ValueError, match='digest mismatch'):
        await publication.reconcile_media_publications(source)
    assert adapter.objects.read(base.base_asset_id, 1)['revision'] == 1


@pytest.mark.parametrize('flat,drift', [(False, 'source'), (False, 'media'), (True, 'source'), (True, None)])
async def test_repeat_registration_only_enriches_compatible_flat_snapshot(media_project, flat, drift):
    from novelvideo.creation.asset_sync import SourceSyncPending
    source, adapter = media_project
    path = candidate(source, provisional=True, content=b'original')
    base = await adapter.register_base('character', 'Lin')
    if flat:
        with adapter.objects.transaction() as conn:
            payload = adapter.objects.read(base.base_asset_id, 1)['payload']
            payload.pop('_media')
            conn.execute('UPDATE object_versions SET payload=? WHERE object_id=?', (json.dumps(payload), base.base_asset_id))
    if drift == 'source':
        with sqlite3.connect(source.db_path) as conn:
            conn.execute("UPDATE characters SET description='human drift' WHERE name='Lin'")
        await source.load_graph_state()
    elif drift == 'media':
        path.write_bytes(b'external overwrite')
    if drift:
        with pytest.raises(SourceSyncPending, match='refresh'):
            await adapter.register_base('character', 'Lin')
        assert adapter.objects.read(base.base_asset_id)['revision'] == 1
        assert (await adapter.refresh_source(base.base_asset_id, expected_revision=1)).revision == 2
    else:
        assert (await adapter.register_base('character', 'Lin')).revision == 2
        assert '_media' not in adapter.objects.read(base.base_asset_id, 1)['payload']
        assert media(adapter, base)[0]['blob']['sha256']
    assert (await adapter.register_base('character', 'Lin')).revision == 2


async def test_reconcile_committed_enriched_snapshot_still_checks_media(media_project):
    from novelvideo.creation.store import RevisionConflict
    source, adapter = media_project
    candidate(source, provisional=True)
    base = await adapter.register_base('character', 'Lin')
    before = source.get_character('Lin').model_dump(mode='json')
    requested = {**before, 'description': 'published'}
    with adapter.objects.transaction() as conn:
        conn.execute('INSERT INTO asset_publication_intents VALUES (?,?,?,?)',
                     (base.base_asset_id, 1, json.dumps({'description': before['description']}), json.dumps({'description': 'published'})))
        conn.execute('INSERT INTO asset_publication_evidence VALUES (?,?,?)',
                     (base.base_asset_id, json.dumps(before), json.dumps(requested)))
    with sqlite3.connect(source.db_path) as conn:
        conn.execute("UPDATE characters SET description='published' WHERE name='Lin'")
    await source.load_graph_state()
    payload = adapter.objects.read(base.base_asset_id, 1)['payload']
    payload['description'] = 'published'
    payload['_media'][0]['blob']['sha256'] = 'different already committed media'
    adapter.objects.update(base.base_asset_id, 1, payload, 'test')
    with pytest.raises(RevisionConflict):
        await adapter.reconcile_publication(base.base_asset_id)
    assert adapter.objects.read(base.base_asset_id, 2)['payload'] == payload
    with adapter.objects.transaction() as conn:
        assert conn.execute('SELECT 1 FROM asset_publication_intents').fetchone()


@pytest.mark.parametrize('suffix,content_type,attachment', [('.png', 'image/png', False), ('.svg', 'application/octet-stream', True), ('.unknown', 'application/octet-stream', True)])
def test_archived_media_uses_real_preview_response(tmp_path, monkeypatch, suffix, content_type, attachment):
    from novelvideo.creation.asset_media import capture_blob
    from novelvideo.api.routes.files import _serve_or_redirect_to_oss
    monkeypatch.setattr('novelvideo.utils.oss_client.maybe_presign_static', lambda *args: None)
    path = tmp_path / ('source' + suffix)
    path.write_bytes(b'archived media')
    blob = capture_blob(tmp_path, path, archive=True)
    archive = tmp_path / blob['path']
    response = _serve_or_redirect_to_oss(archive, as_download=False)
    assert response.headers['content-type'] == content_type
    assert ('attachment' in response.headers.get('content-disposition', '')) == attachment
    assert archive.read_bytes() == path.read_bytes()
    assert archive.stem == blob['sha256']


@pytest.mark.parametrize('error_code', [errno.ENOTSUP, errno.EPERM])
@pytest.mark.parametrize('recover', [False, True])
async def test_no_hardlink_registration_and_real_adoption(media_project, monkeypatch, error_code, recover):
    from novelvideo.creation import asset_media
    from novelvideo.creation import asset_media_publication as publication
    source, adapter = media_project
    candidate(source, version='old', content=b'old bytes', provisional=True)
    def unsupported(*args):
        raise OSError(error_code, 'hardlinks unavailable')
    monkeypatch.setattr(asset_media.os, 'link', unsupported)
    base = await adapter.register_base('character', 'Lin')
    old = media(adapter, base)[0]['blob']
    candidate(source, content=b'new bytes')
    if recover:
        def crash(phase):
            if phase == 'captured':
                raise RuntimeError('after proof captured')
        monkeypatch.setattr(publication, '_checkpoint', crash)
        with pytest.raises(RuntimeError, match='proof captured'):
            await adopt_production(source, monkeypatch, 'character:Lin:portrait', 'candidate')
        monkeypatch.setattr(publication, '_checkpoint', lambda _: None)
        await publication.reconcile_media_publications(source)
    else:
        await adopt_production(source, monkeypatch, 'character:Lin:portrait', 'candidate')
    assert adapter.objects.read(base.base_asset_id)['revision'] == 2
    assert (Path(source.project_dir) / old['path']).read_bytes() == b'old bytes'
    assert media(adapter, base, 1)[0]['blob'] == old
    assert (Path(source.project_dir) / media(adapter, base)[0]['blob']['path']).read_bytes() == b'new bytes'
    await adopt_production(source, monkeypatch, 'character:Lin:portrait', 'candidate')
    assert adapter.objects.read(base.base_asset_id)['revision'] == 2
