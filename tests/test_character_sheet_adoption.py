from pathlib import Path

import pytest
from PIL import Image

from tests.character_visual.test_casting_store import pending, store_at
from tests.character_visual.test_casting_adoption import adoption_env as adoption_env


def completed_sheet(tmp_path):
    store = store_at(tmp_path)
    store.create_pending(pending().model_copy(update={'sheet_contract': 'character_sheet_v4'}))
    store.claim_generation('c1', task_id='task1')
    path = store.output_path('c1')
    path.parent.mkdir(parents=True)
    Image.new('RGB', (120, 80), 'red').save(path)
    store.complete_generation('c1', path)
    return store


def test_crop_is_derived_and_versioned_without_publishing(tmp_path):
    store = completed_sheet(tmp_path)
    original = store.read_verified_asset('c1')
    candidate = store.crop_avatar('c1', (.05, .1, .2, .6), expected_version=0)
    assert Image.open(candidate.avatar_image).size == (24, 48)
    assert store.read_verified_avatar('c1') == Path(candidate.avatar_image).read_bytes()
    assert store.read_verified_asset('c1') == original
    assert not (tmp_path / 'output/assets/characters').exists()
    with pytest.raises(ValueError, match='version'):
        store.crop_avatar('c1', (0, 0, .3, .5), expected_version=0)
    Path(candidate.avatar_image).write_bytes(b'changed')
    with pytest.raises(ValueError, match='digest'):
        store.read_verified_avatar('c1')


def test_sheet_requires_avatar_and_completed_review(tmp_path):
    from novelvideo.character_visual.casting_adoption import adoption_requirements
    store = completed_sheet(tmp_path)
    assert adoption_requirements(store.get('c1'))['blocked_reason'] == 'avatar_missing'
    store.crop_avatar('c1', (0, 0, .3, .5), expected_version=0)
    assert adoption_requirements(store.get('c1'))['blocked_reason'] == 'review_not_completed'


@pytest.mark.asyncio
@pytest.mark.parametrize('identity_id', [None, 'old'])
@pytest.mark.parametrize('reuse_stale_draft', [False, True])
@pytest.mark.parametrize('qc_passed', [False, True])
async def test_adoption_publishes_crop_and_keeps_full_sheet(adoption_env, identity_id, reuse_stale_draft, qc_passed, monkeypatch):
    from novelvideo.character_visual.casting_models import CastingCandidate, CastingReviewReport, CastingFinding
    from novelvideo.api.routes.character_casting import current_version
    env = await adoption_env(identity_id)
    base = env.candidates.get('candidate')
    env.candidates.create_pending(CastingCandidate(candidate_id='sheet', project_id=base.project_id,
        character_id=base.character_id, identity_id=base.identity_id, snapshot=base.snapshot,
        task_id='sheet-task', sheet_contract='character_sheet_v4', reuse_stale_draft=reuse_stale_draft))
    if reuse_stale_draft:
        from novelvideo.character_visual import casting_service
        monkeypatch.setattr(casting_service, 'resolved_style', lambda ctx: '更新后的画风')
        import sqlite3
        with sqlite3.connect(env.sql.db_path) as db:
            db.execute("UPDATE characters SET description='更新的小传' WHERE name='甲'")
    revised={**base.snapshot.proposal_snapshot,'outfit_states':{'default':'用户确认的红外套'}}
    env.candidates.save_revision_prompt('sheet','完整四区设定图，红外套',proposal=revised)
    env.candidates.claim_generation('sheet', task_id='sheet-task')
    path = env.candidates.output_path('sheet')
    path.parent.mkdir(parents=True)
    Image.new('RGB', (120, 80), 'green').save(path)
    env.candidates.complete_generation('sheet', path)
    env.candidates.begin_review('sheet', task_id='sheet-task', attempt_id='qc')
    env.candidates.save_sheet_quality('sheet', attempt_id='qc', quality={'passed': qc_passed, 'issues': [] if qc_passed else [{'problem':'袖宽差异'}]})
    env.candidates.complete_review('sheet', attempt_id='qc', report=CastingReviewReport(
        reviewer='test', model='test', version='v4', findings=[CastingFinding(finding_id='layout',
        dimension='design', verdict='conforms', visibility='visible', description='四区合格')]))
    cropped = env.candidates.crop_avatar('sheet', (0, 0, .25, .5), expected_version=0)
    main = env.ctx.output_dir / 'assets/characters/甲/portrait.png'
    before = main.read_bytes() if main.exists() else None
    command = env.command.model_copy(update={'candidate_id':'sheet','idempotency_key':'sheet-adopt',
        'expected_asset_version':cropped.asset_version,'expected_review_attempt_id':'qc',
        'override_reason':None if qc_passed else '用户确认当前效果可用，人工通过并采用'})
    await env.adopt(command)
    current = current_version(env.ctx, '甲', identity_id)
    assert current['sheet_url']
    assert Image.open(current['_path']).size == (30, 40)
    from novelvideo.character_visual.casting_recovery import resolve_casting_media_path
    import json
    workflow_path = env.ctx.state_dir / 'production_workflow.json'
    workflow_data = json.loads(workflow_path.read_text())
    version = next(v for v in workflow_data['versions'] if v['version_id'] == current['version_id'])
    canonical = env.ctx.output_dir / version['generation_metadata']['canonical_path']
    assert resolve_casting_media_path(env.ctx.output_dir, env.ctx.state_dir, canonical).read_bytes() == Path(cropped.avatar_image).read_bytes()
    # Already-adopted sheets predate the dedicated portrait digest.
    version['generation_metadata']['casting_adoption'].pop('portrait_sha256', None)
    workflow_path.write_text(json.dumps(workflow_data))
    resolved = resolve_casting_media_path(env.ctx.output_dir, env.ctx.state_dir, canonical)
    assert resolved.read_bytes() == Path(cropped.avatar_image).read_bytes()
    saved = resolved.read_bytes()
    resolved.write_bytes(b'tampered')
    with pytest.raises(ValueError, match='immutable asset mismatch'):
        resolve_casting_media_path(env.ctx.output_dir, env.ctx.state_dir, canonical)
    resolved.write_bytes(saved)
    assert Image.open(env.candidates.get('sheet').asset_path).size == (120, 80)
    from novelvideo.character_visual.store import CharacterVisualWorkspaceStore
    workspace=CharacterVisualWorkspaceStore(env.ctx.output_dir,state_dir=env.ctx.state_dir).get('甲')
    bible=workspace.identity_visual_bibles[identity_id] if identity_id else workspace.visual_bible
    assert bible.outfit_states=={'default':'用户确认的红外套'}
    assert bible.revision_id.startswith('casting-modified-')
    if identity_id:
        assert (main.read_bytes() if main.exists() else None) == before
        from novelvideo.production_workflow import ProductionWorkflowStore
        from novelvideo.production_workflow.slot_ids import character_state_slot_id
        slot, versions = ProductionWorkflowStore(env.ctx.state_dir / 'production_workflow.json').get_slot(character_state_slot_id('甲', identity_id))
        assert versions[slot.current_version_id].asset_path == env.candidates.get('sheet').asset_path
        assert versions[slot.current_version_id].adoption_status == 'adopted'
