import pytest

from novelvideo.director_plan.cinematography import ShotCinematography
from tests.shot_continuity.test_cinematography import photography


@pytest.fixture
def inputs(tmp_path):
    from PIL import Image
    path = tmp_path / 'frame.png'
    Image.new('RGB', (2, 2), 'red').save(path)
    return {'reference': path}, {'reference': ShotCinematography.model_validate(photography())}


def verdict(labels=('reference',), issues=None):
    return {'status': 'failed' if issues else 'passed',
            'evidence': [{'frame_label': label, 'observation': 'Lantern illuminates the uphill-facing subject from below.'} for label in labels],
            'issues': issues or []}


async def review(inputs, response, **kwargs):
    from novelvideo.shot_continuity.visual_review import review_cinematography
    async def vision(**call):
        assert call['images'][0].media_type == 'image/png'
        assert 'world-space' in call['prompt']
        assert 'intentional hard cut' in call['prompt']
        return response
    frames, facts = inputs
    return await review_cinematography(frames=frames, facts=facts, run_structured=vision,
                                      reviewer_fingerprint='route/model-v1', **kwargs)


async def test_observed_reference_passes(inputs):
    result = await review(inputs, verdict())
    assert result.status == 'passed'
    assert result.evidence[0].frame_label == 'reference'


@pytest.mark.parametrize('response', [verdict(labels=()),
    verdict(labels=('invented',)), {'status': 'passed', 'evidence': [], 'issues': []},
    verdict(issues=[{'dimension': 'lighting', 'description': 'Wrong light', 'frame_labels': []}])])
async def test_empty_or_unattributable_evidence_cannot_pass(inputs, response):
    assert (await review(inputs, response)).status == 'unavailable'


async def test_failure_retains_dimension_and_frame_evidence(inputs):
    result = await review(inputs, verdict(issues=[{'dimension': 'blocking',
        'description': 'Subject faces downhill instead of uphill.', 'frame_labels': ['reference']}]))
    assert result.status == 'failed'
    assert result.issues[0].dimension == 'blocking'


@pytest.mark.parametrize('invalid', ['missing', 'corrupt', 'empty', 'extra_facts'])
async def test_missing_or_invalid_images_are_unavailable_without_call(inputs, invalid, tmp_path):
    from novelvideo.shot_continuity.visual_review import review_cinematography
    frames, facts = inputs
    if invalid == 'missing':
        frames['reference'] = tmp_path / 'missing.png'
    elif invalid == 'corrupt':
        frames['reference'].write_bytes(b'not an image')
    elif invalid == 'empty':
        frames = {}
    else:
        facts['extra'] = facts['reference']
    async def forbidden(**kwargs):
        pytest.fail('Invalid attachments must not reach the provider')
    result = await review_cinematography(frames=frames, facts=facts, run_structured=forbidden,
                                        reviewer_fingerprint='model')
    assert result.status == 'unavailable'


async def test_boundary_requires_explicit_cut_intent_and_both_frames(inputs):
    frames, facts = inputs
    frames['next_start'] = frames['reference']
    facts['next_start'] = facts['reference']
    assert (await review(inputs, verdict(tuple(frames)), mode='boundary')).status == 'unavailable'
    result = await review(inputs, verdict(tuple(frames)), mode='boundary',
                          cut_intent='intentional reverse angle hard cut')
    assert result.status == 'passed'


@pytest.mark.parametrize('pair', [('previous_end', 'next_start'), ('missing', 'next_start'),
                                 ('next_start', 'previous_end'), ('previous_end', 'previous_end')])
async def test_boundary_with_temporal_context_requires_valid_ordered_pair(inputs, pair):
    from novelvideo.shot_continuity.visual_review import review_cinematography
    frames, facts = inputs
    labels = [f'{side}_{role}' for side in ('previous', 'next') for role in ('start', 'middle', 'end')]
    calls = []
    async def vision(**kwargs):
        import json
        calls.append(kwargs)
        context = json.loads('{' + kwargs['prompt'].split('\n{', 1)[1])
        assert context['boundary_labels'] == list(pair)
        return verdict(labels)
    result = await review_cinematography(
        frames={label: frames['reference'] for label in labels},
        facts={label: facts['reference'] for label in labels}, run_structured=vision,
        reviewer_fingerprint='model', mode='boundary', cut_intent='same-side closer cut',
        boundary_labels=pair)
    valid = pair == ('previous_end', 'next_start')
    assert result.status == ('passed' if valid else 'unavailable')
    assert len(calls) == int(valid)


async def test_cache_depends_on_content_facts_route_and_policy(inputs):
    from novelvideo.shot_continuity.visual_review import review_cinematography
    from PIL import Image
    frames, facts = inputs
    calls = []
    async def vision(**kwargs):
        calls.append(kwargs)
        return verdict()
    cache = {}
    async def run(**kwargs):
        return await review_cinematography(frames=frames, facts=facts, run_structured=vision,
                    cache=cache, reviewer_fingerprint=kwargs.pop('reviewer_fingerprint', 'model'), **kwargs)
    assert (await run()).status == 'passed'
    await run()
    assert len(calls) == 1
    Image.new('RGB', (2, 2), 'blue').save(frames['reference'])
    await run()
    facts['reference'] = facts['reference'].model_copy(update={'camera_side': 'opposite'})
    await run()
    await run(reviewer_fingerprint='other-model')
    await run(policy_version='next-policy')
    assert len(calls) == 5


@pytest.mark.parametrize('missing_evidence', [False, True])
async def test_contextual_boundary_does_not_override_failure_or_missing_evidence(inputs, missing_evidence):
    from novelvideo.shot_continuity.visual_review import review_cinematography
    frames, facts = inputs
    labels = ('previous_start', 'previous_end', 'next_start', 'next_end')
    async def vision(**kwargs):
        return verdict(labels[:-1] if missing_evidence else labels, issues=[{
            'dimension': 'cut', 'description': 'Subject switches hand across the cut.',
            'frame_labels': ['previous_end', 'next_start']}])
    result = await review_cinematography(
        frames={label: frames['reference'] for label in labels},
        facts={label: facts['reference'] for label in labels}, run_structured=vision,
        reviewer_fingerprint='model', mode='boundary', cut_intent='same-side closer cut',
        boundary_labels=('previous_end', 'next_start'))
    assert result.status == ('unavailable' if missing_evidence else 'failed')


async def test_unavailable_is_retryable_and_error_is_sanitized(inputs):
    from novelvideo.shot_continuity.visual_review import review_cinematography
    frames, facts = inputs
    calls = 0
    async def vision(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError('secret signed URL')
        return verdict()
    kwargs = dict(frames=frames, facts=facts, run_structured=vision,
                  reviewer_fingerprint='model', cache={})
    first = await review_cinematography(**kwargs)
    assert first.status == 'unavailable'
    assert 'secret' not in first.model_dump_json()
    assert (await review_cinematography(**kwargs)).status == 'passed'
    assert calls == 2


async def test_failed_report_is_cached_without_repeat_review(inputs):
    from novelvideo.shot_continuity.visual_review import review_cinematography
    frames, facts = inputs
    calls = 0
    async def vision(**kwargs):
        nonlocal calls
        calls += 1
        return verdict(issues=[{'dimension': 'camera', 'description': 'Wrong camera side',
                                'frame_labels': ['reference']}])
    kwargs = dict(frames=frames, facts=facts, run_structured=vision,
                  reviewer_fingerprint='model', cache={})
    assert (await review_cinematography(**kwargs)).status == 'failed'
    assert (await review_cinematography(**kwargs)).status == 'failed'
    assert calls == 1


async def test_timeout_returns_unavailable_without_cache(inputs):
    import asyncio
    from novelvideo.shot_continuity.visual_review import review_cinematography
    frames, facts = inputs
    async def vision(**kwargs):
        await asyncio.Event().wait()
    cache = {}
    result = await review_cinematography(frames=frames, facts=facts, run_structured=vision,
        reviewer_fingerprint='model', cache=cache, timeout_seconds=0.001)
    assert result.status == 'unavailable'
    assert result.technical_error == 'TimeoutError'
    assert cache == {}


async def test_frame_count_is_bounded_before_provider_call(inputs):
    from novelvideo.shot_continuity.visual_review import review_cinematography
    frames, facts = inputs
    async def forbidden(**kwargs):
        pytest.fail('Too many images must not reach provider')
    result = await review_cinematography(
        frames={str(i): frames['reference'] for i in range(9)},
        facts={str(i): facts['reference'] for i in range(9)},
        run_structured=forbidden, reviewer_fingerprint='model')
    assert result.status == 'unavailable'


@pytest.mark.parametrize('invalid', ['truncated_jpeg', 'excessive_pixels'])
async def test_undecodable_or_oversized_image_never_reaches_reviewer(inputs, invalid):
    from PIL import Image
    from novelvideo.shot_continuity.visual_review import review_cinematography
    frames, facts = inputs
    path = frames['reference']
    if invalid == 'truncated_jpeg':
        Image.new('RGB', (200, 200), 'red').save(path, format='JPEG')
        path.write_bytes(path.read_bytes()[:-100])
        # JPEG structure verification alone does not detect this missing payload.
        with Image.open(path) as image:
            image.verify()
    else:
        Image.new('L', (8001, 4000)).save(path, format='PNG')
    calls = []
    async def vision(**kwargs):
        calls.append(kwargs)
        return verdict()
    result = await review_cinematography(frames=frames, facts=facts,
        run_structured=vision, reviewer_fingerprint='model')
    assert result.status == 'unavailable'
    assert calls == []


async def test_frame_context_informs_review_and_invalidates_cache(inputs):
    from novelvideo.shot_continuity.visual_review import review_cinematography
    frames, facts = inputs
    calls = []
    async def vision(**kwargs):
        calls.append(kwargs)
        return verdict()
    cache = {}
    kwargs = dict(frames=frames, facts=facts, run_structured=vision,
                  reviewer_fingerprint='model', cache=cache)
    context = {'reference': 'actual end; visible_start_state=foot planted; '
               'visible_end_state=next step; camera_motion=track uphill'}
    result = await review_cinematography(**kwargs, frame_context=context)
    assert result.status == 'passed'
    assert context['reference'] in calls[0]['prompt']
    await review_cinematography(**kwargs, frame_context=context)
    assert len(calls) == 1
    await review_cinematography(**kwargs, frame_context={'reference': 'actual start'})
    assert len(calls) == 2
    result = await review_cinematography(**kwargs, frame_context={'unknown': 'actual end'})
    assert result.status == 'unavailable'
    assert len(calls) == 2
