import hashlib
import io
import json
import shutil
import subprocess
from types import SimpleNamespace

import pytest
from PIL import Image

from novelvideo.media_capabilities.video.h3_reference_runtime import H3FrozenFrame
from novelvideo.media_capabilities.video.h3_timeline import H3DirectorSegment
from tests.shot_continuity.test_cinematography import shot


def segment(name='s1'):
    return H3DirectorSegment(segment_id=name, source_shot_ids=(name,), beat_number=1,
        prompt='Walk uphill', duration_seconds=1, first_frame=f'{name}-start.png',
        last_frame=f'{name}-end.png')


@pytest.fixture
def frozen():
    stream = io.BytesIO()
    Image.new('RGB', (16, 16), 'red').save(stream, format='PNG')
    data = stream.getvalue()
    return {f'{name}-{role}.png': H3FrozenFrame(source=f'{name}-{role}.png', content=data,
        sha256=hashlib.sha256(data).hexdigest(), width=16, height=16, suffix='.png')
        for name in ('s1', 's2') for role in ('start', 'end')}


class Runtime:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail
        self.snapshot = SimpleNamespace(model_dump_json=lambda: '{"model":"test-vision"}')

    async def run_structured(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail:
            raise RuntimeError('private error')
        context = json.loads(kwargs['prompt'][kwargs['prompt'].index('\n{') + 1:])
        return {'status': 'passed', 'evidence': [{'frame_label': label,
            'observation': 'Subject faces uphill with warm lantern light from below.'}
            for label in context['ordered_frames']], 'issues': []}


async def test_reference_reviews_use_frozen_bytes_and_boundary_context(frozen, tmp_path):
    from novelvideo.shot_continuity.production_review import review_reference_inputs
    runtime = Runtime()
    reports = await review_reference_inputs([segment(), segment('s2')],
        {'s1': shot(), 's2': shot(id='s2')}, frozen, runtime, tmp_path / 'cache')
    assert len(reports) == 3
    assert all(report.status == 'passed' for report in reports)
    assert all(image.data == frozen['s1-start.png'].content for call in runtime.calls
               for image in call['images'])
    prompts = '\n'.join(call['prompt'] for call in runtime.calls)
    for value in ('reference start', 'reference end', 'foot planted', 'next step',
                  'static', 'hard cut matching upward gaze'):
        assert value in prompts
    assert json.loads('{' + runtime.calls[-1]['prompt'].split('\n{', 1)[1])['mode'] == 'boundary'


async def test_reference_cache_persists_and_corruption_is_a_miss(frozen, tmp_path):
    from novelvideo.shot_continuity.production_review import review_reference_inputs
    args = ([segment()], {'s1': shot()}, frozen)
    cache_dir = tmp_path / 'cache'
    first = Runtime()
    await review_reference_inputs(*args, first, cache_dir)
    second = Runtime()
    await review_reference_inputs(*args, second, cache_dir)
    assert second.calls == []
    records = list(cache_dir.glob('*.json'))
    assert len(records) == 1
    records[0].write_text('{broken', encoding='utf-8')
    third = Runtime()
    await review_reference_inputs(*args, third, cache_dir)
    assert len(third.calls) == 1


@pytest.mark.parametrize('missing', ['runtime', 'facts', 'media', 'empty'])
async def test_missing_reference_prerequisites_are_unavailable(frozen, tmp_path, missing):
    from novelvideo.shot_continuity.production_review import review_reference_inputs
    reports = await review_reference_inputs([] if missing == 'empty' else [segment()],
        {'s1': shot(cinematography=None)} if missing == 'facts' else {'s1': shot()},
        {} if missing == 'media' else frozen, None if missing == 'runtime' else Runtime(), tmp_path)
    assert reports
    assert all(report.status == 'unavailable' for report in reports)


async def test_provider_failure_is_not_persisted(frozen, tmp_path):
    from novelvideo.shot_continuity.production_review import review_reference_inputs
    reports = await review_reference_inputs([segment()], {'s1': shot()}, frozen, Runtime(fail=True), tmp_path)
    assert reports[0].status == 'unavailable'
    assert list(tmp_path.glob('*.json')) == []


async def test_generated_segments_extract_frames_and_review_boundary(tmp_path):
    from novelvideo.shot_continuity.production_review import review_generated_segments
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        pytest.skip('Local ffmpeg tools unavailable')
    path = tmp_path / 'clip.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
        'color=c=red:s=32x32:r=10:d=1', '-c:v', 'mpeg4', str(path)], check=True, timeout=20)
    runtime = Runtime()
    reports = await review_generated_segments([(segment(), path), (segment('s2'), path)],
        {'s1': shot(), 's2': shot(id='s2')}, runtime, tmp_path / 'cache')
    assert len(reports) == 3
    assert all(report.status == 'passed' for report in reports)
    assert [len(call['images']) for call in runtime.calls] == [3, 3, 6]
    boundary = json.loads('{' + runtime.calls[-1]['prompt'].split('\n{', 1)[1])
    assert boundary['boundary_labels'] == ['previous_end', 'next_start']
    assert boundary['ordered_frames'] == [f'{side}_{role}' for side in ('previous', 'next')
                                          for role in ('start', 'middle', 'end')]
    assert 'actual middle' in runtime.calls[0]['prompt']


async def test_generated_missing_media_does_not_call_reviewer(tmp_path):
    from novelvideo.shot_continuity.production_review import review_generated_segments
    runtime = Runtime()
    reports = await review_generated_segments([(segment(), tmp_path / 'missing.mp4')],
        {'s1': shot()}, runtime, tmp_path / 'cache')
    assert reports[0].status == 'unavailable'
    assert runtime.calls == []


def test_persistent_cache_rejects_path_keys_and_unavailable(tmp_path):
    from novelvideo.shot_continuity.production_review import VisualReviewCache
    from novelvideo.shot_continuity.visual_review import VisualReviewReport
    cache = VisualReviewCache(tmp_path)
    with pytest.raises(ValueError):
        cache['../escaped'] = VisualReviewReport(status='passed')
    cache['a' * 64] = VisualReviewReport(status='unavailable')
    assert list(tmp_path.glob('*.json')) == []


async def test_i2va_reference_uses_start_only_and_defers_unknown_boundary(frozen, tmp_path):
    from novelvideo.shot_continuity.production_review import review_reference_inputs
    runtime = Runtime()
    segments = [segment().model_copy(update={'last_frame': None}),
                segment('s2').model_copy(update={'last_frame': None})]
    reports = await review_reference_inputs(segments, {'s1': shot(), 's2': shot(id='s2')},
                                           frozen, runtime, tmp_path)
    assert len(reports) == 2
    assert all(report.status == 'passed' for report in reports)
    assert [len(call['images']) for call in runtime.calls] == [1, 1]


async def test_fl2va_to_i2va_reviews_three_reference_frames_and_boundary(frozen, tmp_path):
    from novelvideo.shot_continuity.production_review import review_reference_inputs
    runtime = Runtime()
    reports = await review_reference_inputs([segment(), segment('s2').model_copy(update={'last_frame': None})],
        {'s1': shot(), 's2': shot(id='s2')}, frozen, runtime, tmp_path)
    assert len(reports) == 3
    assert all(report.status == 'passed' for report in reports)
    assert [len(call['images']) for call in runtime.calls] == [2, 1, 2]


async def test_paired_shot_context_contains_intended_timeline_without_claiming_observed_cut(frozen, tmp_path):
    from novelvideo.shot_continuity.production_review import review_reference_inputs
    runtime = Runtime()
    paired = segment().model_copy(update={'source_shot_ids': ('s1', 's2')})
    await review_reference_inputs([paired], {'s1': shot(), 's2': shot(id='s2', camera_motion='pan right')},
                                  frozen, runtime, tmp_path)
    prompt = runtime.calls[0]['prompt']
    assert 'intended_source_timeline' in prompt
    assert 'pan right' in prompt
    assert 'not an observed cut' in prompt


def test_cache_concurrent_distinct_keys_are_complete_json(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from novelvideo.shot_continuity.production_review import VisualReviewCache
    from novelvideo.shot_continuity.visual_review import FrameEvidence, VisualReviewReport
    cache = VisualReviewCache(tmp_path)
    report = VisualReviewReport(status='passed', evidence=(FrameEvidence(
        frame_label='start', observation='Visible warm lantern light'),))
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda key: cache.__setitem__(key, report), ['a' * 64, 'b' * 64]))
    assert cache['a' * 64] == report
    assert cache['b' * 64] == report
    assert len(list(tmp_path.glob('*.json'))) == 2
    assert list(tmp_path.glob('*.tmp')) == []


async def test_probe_timeout_is_unavailable_and_uses_no_shell(tmp_path, monkeypatch):
    from novelvideo.shot_continuity import production_review
    path = tmp_path / 'clip.mp4'
    path.write_bytes(b'fake')
    def timeout(command, **kwargs):
        assert kwargs['shell'] is False
        assert kwargs['timeout'] == 20
        raise subprocess.TimeoutExpired(command, 20)
    monkeypatch.setattr(production_review.subprocess, 'run', timeout)
    runtime = Runtime()
    reports = await production_review.review_generated_segments([(segment(), path)],
        {'s1': shot()}, runtime, tmp_path / 'cache')
    assert reports[0].status == 'unavailable'
    assert runtime.calls == []
