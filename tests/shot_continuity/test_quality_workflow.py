from types import SimpleNamespace

import pytest

from tests.shot_continuity.test_production_review import segment
from novelvideo.media_capabilities.video.h3_timeline import H3DirectorOutputManifest, H3TimelineEntry


def manifest(tmp_path):
    video = tmp_path / 'existing.mp4'
    video.write_bytes(b'existing paid video')
    return H3DirectorOutputManifest(physical_video=str(video), entries=(
        H3TimelineEntry(segment=segment(), start_frame=0, frame_count=24, physical_video=str(video)),))


async def test_missing_frozen_facts_never_calls_reviewer(tmp_path):
    from novelvideo.shot_continuity.quality_workflow import review_existing_manifest
    result = await review_existing_manifest(manifest(tmp_path), project_dir=tmp_path,
        episode=1, ctx=SimpleNamespace(state_dir=tmp_path))
    assert result[0]['status'] == 'unavailable'
    assert result[0]['technical_error'] == '缺少原始摄影快照，待人工复核'


@pytest.mark.parametrize('segments', [1, 2])
async def test_legacy_frozen_h3_reviews_original_facts_and_keeps_partial_status(tmp_path, monkeypatch, segments):
    from PIL import Image
    from novelvideo.shot_continuity import production_review, quality_workflow
    from novelvideo.text_task_runtime import runtime
    from novelvideo.shot_continuity.visual_review import FROZEN_H3_POLICY_VERSION
    plan = {'schema_version': 1, 'mode': 'i2va', 'fps': 24, 'total_frames': 24,
        'visual_style': 'natural', 'continuity_locks': ['same path'], 'soundscape': 'wind', 'music': 'none',
        'shots': [{'shot_id': '1', 'framing': 'wide', 'angle': 'path side', 'focus': 'path',
            'composition': 'faces path', 'start_frame': 0, 'end_frame': 24,
            'camera': {'type': 'static'}, 'actions': [{'phase': 'establish',
                'start_frame': 0, 'end_frame': 1, 'description': 'faces path'},
                {'phase': 'execute', 'start_frame': 1, 'end_frame': 24,
                 'description': 'looks up the path', 'change_domain': 'subject_or_prop'}]}]}
    value = manifest(tmp_path)
    value = value.model_copy(update={'entries': (value.entries[0].model_copy(update={'director_plan': plan}),)})
    if segments == 2:
        second = value.entries[0].model_copy(update={'segment': value.entries[0].segment.model_copy(
            update={'segment_id': 'next'}), 'start_frame': 24})
        value = value.model_copy(update={'entries': (*value.entries, second)})
    def extract(source, directory):
        image = directory / 'frame.png'
        Image.new('RGB', (2, 2), 'red').save(image)
        return dict.fromkeys(('start', 'middle', 'end'), image)
    async def vision(**kwargs):
        import json
        assert 'faces path' in kwargs['prompt']
        assert 'frozen_h3_director_plan' in kwargs['prompt']
        labels = json.loads(kwargs['prompt'].splitlines()[-1])['ordered_frames']
        return {'status': 'passed', 'evidence': [{'frame_label': role,
            'observation': 'back faces path', 'face_status': 'not_visible',
            'face_observation': 'back of head only'} for role in labels]}
    monkeypatch.setattr(production_review, '_extract_frames', extract)
    monkeypatch.setattr(runtime, 'current_text_task_runtime', lambda: SimpleNamespace(
        snapshot=SimpleNamespace(model_dump_json=lambda: 'frozen-runtime'), run_structured=vision))
    reports = await quality_workflow.review_existing_manifest(value, project_dir=tmp_path,
        episode=1, ctx=SimpleNamespace(state_dir=tmp_path))
    assert reports[0]['status'] == 'passed'
    assert reports[0]['policy_version'] == FROZEN_H3_POLICY_VERSION
    assert set(reports[0]['frame_assets']) == {'start', 'middle', 'end'}
    assert len(reports) == 2 * segments - 1
    if segments == 2:
        assert reports[-1]['review_kind'] == 'actual_boundary'
        assert len(reports[-1]['frame_assets']) == 6
    assert production_review.quality_status(reports, [value.entries[0].segment.segment_id]) == 'needs_review'


def test_media_fingerprint_changes_with_bytes(tmp_path):
    from novelvideo.shot_continuity.quality_workflow import media_fingerprint
    value = manifest(tmp_path)
    before = media_fingerprint(value, tmp_path)
    (tmp_path / 'existing.mp4').write_bytes(b'replacement')
    assert before != media_fingerprint(value, tmp_path)


def test_manual_disposition_requires_matching_evidence_and_reason(tmp_path):
    from novelvideo.shot_continuity.quality_workflow import disposition_record, issue_id, media_fingerprint
    value = manifest(tmp_path)
    issue = {'dimension': 'face_integrity', 'description': 'distorted', 'frame_labels': ['middle']}
    fingerprint = media_fingerprint(value, tmp_path)
    review = {'phase': 'generated', 'status': 'failed', 'review_id': 'a' * 64,
        'media_fingerprint': fingerprint, 'issues': [issue], 'evidence': [{'frame_label': 'middle'}],
        'frame_assets': {'middle': '/frame.png'}}
    value = value.model_copy(update={'cinematography_reviews': (review,)})
    kwargs = dict(review_id=review['review_id'], issue_id=issue_id(issue),
        media_fingerprint=fingerprint, reason='Checked original frame', decision='acknowledged', actor='tester')
    row = disposition_record(value, current_fingerprint=fingerprint, **kwargs)
    assert row['reason'] == kwargs['reason']
    assert row['review_id'] == review['review_id']
    with pytest.raises(ValueError):
        disposition_record(value, current_fingerprint=fingerprint, **{**kwargs, 'reason': ' '})
    with pytest.raises(ValueError):
        disposition_record(value, current_fingerprint='changed', **kwargs)
    with pytest.raises(ValueError):
        disposition_record(value, current_fingerprint=fingerprint, **{**kwargs, 'decision': 'passed'})
    unavailable = value.model_copy(update={'cinematography_reviews': ({**review, 'status': 'unavailable'},)})
    with pytest.raises(ValueError):
        disposition_record(unavailable, current_fingerprint=fingerprint, **kwargs)


async def test_recheck_preserves_video_and_appends_audit(tmp_path, monkeypatch):
    from contextlib import nullcontext
    from novelvideo.shot_continuity import quality_workflow as service
    from novelvideo.media_capabilities.video.h3_timeline import load_h3_director_manifest, save_h3_director_manifest
    value = manifest(tmp_path)
    path = tmp_path / 'manifest.json'
    save_h3_director_manifest(path, value)
    def snapshot(*args):
        current = load_h3_director_manifest(path)
        return current.model_dump(mode='json'), SimpleNamespace(manifest_asset=str(path)), current
    monkeypatch.setattr(service, 'load_snapshot', snapshot)
    monkeypatch.setattr(service, 'narrative_group_sidecar_guard', lambda *args: nullcontext())
    envelope = {'episode': 1, 'payload': {'group_id': 'g1', 'revision': 1,
        'manifest_digest': service.digest(value.model_dump(mode='json')),
        'media_fingerprint': service.media_fingerprint(value, tmp_path)}}
    result = await service.execute_quality_check(envelope, SimpleNamespace(output_dir=tmp_path, state_dir=tmp_path))
    updated = load_h3_director_manifest(path)
    assert result['quality_status'] == 'needs_review'
    assert updated.status == 'completed'
    assert updated.physical_video == value.physical_video
    assert (tmp_path / 'existing.mp4').read_bytes() == b'existing paid video'
    assert updated.cinematography_reviews[0]['media_fingerprint'] == envelope['payload']['media_fingerprint']
    assert updated.cinematography_reviews[0]['attempt_id'] == result['attempt_id']


async def test_recheck_conflict_does_not_overwrite_new_manifest(tmp_path, monkeypatch):
    from contextlib import nullcontext
    from novelvideo.shot_continuity import quality_workflow as service
    from novelvideo.media_capabilities.video.h3_timeline import load_h3_director_manifest, save_h3_director_manifest
    value = manifest(tmp_path)
    path = tmp_path / 'manifest.json'
    save_h3_director_manifest(path, value)
    def snapshot(*args):
        current = load_h3_director_manifest(path)
        return current.model_dump(mode='json'), SimpleNamespace(manifest_asset=str(path)), current
    async def changed(*args, **kwargs):
        save_h3_director_manifest(path, value.model_copy(update={'quality_dispositions': ({'reason': 'concurrent'},)}))
        return [{'status': 'passed'}]
    monkeypatch.setattr(service, 'review_existing_manifest', changed)
    monkeypatch.setattr(service, 'load_snapshot', snapshot)
    monkeypatch.setattr(service, 'narrative_group_sidecar_guard', lambda *args: nullcontext())
    envelope = {'episode': 1, 'payload': {'group_id': 'g1', 'revision': 1,
        'manifest_digest': service.digest(value.model_dump(mode='json')),
        'media_fingerprint': service.media_fingerprint(value, tmp_path)}}
    with pytest.raises(RuntimeError, match='检查期间'):
        await service.execute_quality_check(envelope, SimpleNamespace(output_dir=tmp_path, state_dir=tmp_path))
    updated = load_h3_director_manifest(path)
    assert not updated.cinematography_reviews
    assert updated.quality_dispositions[0]['reason'] == 'concurrent'


async def test_existing_review_uses_configured_runtime_and_cache(tmp_path, monkeypatch):
    from PIL import Image
    from novelvideo.shot_continuity import production_review
    from novelvideo.shot_continuity.quality_workflow import review_existing_manifest
    from novelvideo.text_task_runtime import runtime as runtimes
    from tests.shot_continuity.test_production_review import Runtime
    from tests.shot_continuity.test_cinematography import shot
    image = tmp_path / 'frame.png'
    Image.new('RGB', (16, 16), 'red').save(image)
    seen = []
    def frames(source, directory):
        seen.append(source)
        return {key: image for key in ('start', 'middle', 'end')}
    monkeypatch.setattr(production_review, '_extract_frames', frames)
    runtime = Runtime()
    monkeypatch.setattr(runtimes, 'current_text_task_runtime', lambda: runtime)
    value = manifest(tmp_path).model_copy(update={'cinematography_shots': (shot().model_dump(mode='json'),)})
    kwargs = dict(project_dir=tmp_path, episode=1, ctx=SimpleNamespace(state_dir=tmp_path))
    first = await review_existing_manifest(value, **kwargs)
    second = await review_existing_manifest(value, **kwargs)
    assert first[0]['status'] == second[0]['status'] == 'passed'
    assert len(runtime.calls) == 1
    assert seen == [tmp_path / 'existing.mp4'] * 2


async def test_api_returns_pending_dedicated_task(tmp_path, monkeypatch):
    import threading
    from contextlib import nullcontext
    from unittest.mock import AsyncMock
    from novelvideo.api.routes import narrative_groups as api
    from novelvideo.shot_continuity import quality_workflow as service
    value = manifest(tmp_path)
    ctx = SimpleNamespace(output_dir=tmp_path)
    resolved = SimpleNamespace(project_dir=tmp_path, ctx=ctx)
    monkeypatch.setattr(api, '_resolve_groups', AsyncMock(return_value=(resolved, [], None)))
    monkeypatch.setattr(api, 'narrative_group_sidecar_guard', lambda *args: nullcontext())
    event_loop_thread = threading.get_ident()
    def load_snapshot(*args):
        assert threading.get_ident() != event_loop_thread, 'Blocking snapshot reads must run off the event loop'
        return value.model_dump(mode='json'), None, value
    monkeypatch.setattr(service, 'load_snapshot', load_snapshot)
    enqueue = AsyncMock(return_value=SimpleNamespace(task_state=SimpleNamespace(task_id='task1'), backend='inline', queue='default'))
    monkeypatch.setattr(api, 'get_task_backend', lambda: SimpleNamespace(enqueue_project_task=enqueue))
    response = await api.check_existing_group_video('p', 1, 'g', api.VideoQualityCheckRequest(revision=2), {})
    assert response['data']['status'] == 'pending'
    assert response['data']['task_id'] == 'task1'
    assert enqueue.call_args.kwargs['task_type'] == 'narrative_group_video_quality'
    assert enqueue.call_args.kwargs['payload']['manifest_digest'] == service.digest(value.model_dump(mode='json'))
