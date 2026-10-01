import json

import pytest

from tests.script_creation.test_prop_extraction import setup, preview  # noqa: F401
from novelvideo.models import NovelProp
from novelvideo.script_creation.store import DocumentConflict


async def committed(setup):
    store, assets, service, doc = setup
    await assets.add_prop(NovelProp(name='记录本', description='保留已有资产'))
    run = await preview(service, doc)
    run = await service.confirm(run['id'], base_revision_id=doc.current_revision_id,
        candidate_ids=[c['id'] for c in run['candidates']], client_mutation_id='confirm')
    return run


@pytest.mark.parametrize('legacy', [False, True])
async def test_rollback_created_preserves_reuse_audit_and_can_reconfirm(setup, tmp_path, legacy):
    store, assets, service, doc = setup
    run = await committed(setup)
    async with store._db() as db:
        if legacy:
            for result in run['result']:
                result.pop('created_snapshot', None)
            await service._write(db, run)
        await db.execute('INSERT INTO episodes(number,prop_menu_json) VALUES(1,?)',
            (json.dumps([{'prop_id': '手电'}, {'prop_id': '记录本'}, {'prop_id': '无关资产'}]),))
        await db.commit()
    rolled = await service.rollback_created(run['id'], output_dir=tmp_path)
    assert rolled['status'] == 'ready' and rolled['result'] == []
    assert len(rolled['rollback_history'][0]['removed_asset_ids']) == 2
    assert rolled['raw_output'] == run['raw_output']
    assert await service.rollback_created(run['id'], output_dir=tmp_path) == rolled
    assert [p.name for p in await assets.list_props()] == ['记录本']
    async with store._db() as db:
        menu = json.loads((await (await db.execute('SELECT prop_menu_json FROM episodes WHERE number=1')).fetchone())[0])
        assert [p['prop_id'] for p in menu] == ['记录本', '无关资产']
        assert (await (await db.execute('SELECT count(*) FROM script_prop_import_links')).fetchone())[0] == 1
        assert (await (await db.execute('SELECT count(*) FROM asset_registry WHERE deleted_at IS NOT NULL')).fetchone())[0] == 2
    with pytest.raises(DocumentConflict):
        await service.confirm(run['id'], base_revision_id=doc.current_revision_id,
            candidate_ids=[c['id'] for c in run['candidates']], client_mutation_id='confirm')
    selected = run['candidates'][1]['id']
    again = await service.confirm(run['id'], base_revision_id=doc.current_revision_id,
        candidate_ids=[selected], client_mutation_id='confirm-again')
    assert [r['name'] for r in again['result']] == ['手电']


@pytest.mark.parametrize('change', ['edit', 'media', 'other-link'])
async def test_rollback_conflict_is_atomic(setup, tmp_path, change):
    store, assets, service, _ = setup
    run = await committed(setup)
    if change == 'media':
        path = tmp_path / 'assets' / 'props' / '铅笔' / 'versions' / 'candidate.png'
        path.parent.mkdir(parents=True)
        path.write_bytes(b'image')
    else:
        async with store._db() as db:
            if change == 'edit':
                await db.execute("UPDATE props SET notes='人工新编辑' WHERE name='铅笔'")
            else:
                asset = next(r['asset_id'] for r in run['result'] if r['name'] == '铅笔')
                await db.execute("INSERT INTO script_prop_import_links VALUES ('other','doc','rev','block',?,'candidate','other-run','evidence','now')", (asset,))
            await db.commit()
    with pytest.raises(DocumentConflict):
        await service.rollback_created(run['id'], output_dir=tmp_path)
    assert len(await assets.list_props()) == 3
    assert (await service.get(run['id']))['status'] == 'committed'
