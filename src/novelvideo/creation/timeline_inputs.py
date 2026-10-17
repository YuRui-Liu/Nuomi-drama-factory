"""Bounded Agent edits to an existing editor timeline; media identity is immutable."""
from __future__ import annotations

from copy import deepcopy
import math

from .operations import OperationError


def _invalid(message):
    raise OperationError('invalid_patch', message)


def _number(value, *, minimum=0, maximum=86_400_000):
    return type(value) in {float, int} and math.isfinite(value) and minimum <= value <= maximum


def _item(items, index):
    if not isinstance(items, list) or type(index) is not int or not 0 <= index < len(items):
        _invalid('Timeline field must refer to an existing track or clip')
    item = items[index]
    if not isinstance(item, dict):
        _invalid('Timeline item is invalid')
    return item


def _source_timing(clip):
    speed = clip.get('speed', 1)
    start, end = clip.get('trimStartMs'), clip.get('trimEndMs')
    position = clip.get('timelineStartMs')
    if (not _number(speed, minimum=0.25, maximum=4) or not _number(start)
            or not _number(end) or end <= start or not _number(position)):
        _invalid('Subtitle source clip timing is invalid')
    return {'timelineStartMs': position, 'timelineEndMs': position + (end - start) / speed,
            'sourceStartMs': start, 'sourceEndMs': end, 'speed': speed}


def _validate_cue(cue, draft):
    if not isinstance(cue.get('id'), str) or not cue['id'] or len(cue['id']) > 128:
        _invalid('Subtitle cue id is invalid')
    text = cue.get('text')
    if not isinstance(text, str) or not text.strip() or len(text) > 4000 or '\x00' in text:
        _invalid('Subtitle text must be nonempty bounded text')
    start, end = cue.get('timelineStartMs'), cue.get('timelineEndMs')
    if not _number(start) or not _number(end) or end <= start:
        _invalid('Subtitle cue end must follow its start')
    if cue.get('timing') not in {'manual', 'estimated_audio_clip'} or type(cue.get('timingConfirmed')) is not bool:
        _invalid('Subtitle timing state is invalid')
    source_id = cue.get('sourceClipId')
    timing = cue.get('sourceTiming')
    if timing is not None and (not isinstance(timing, dict) or set(timing) != {
        'timelineStartMs', 'timelineEndMs', 'sourceStartMs', 'sourceEndMs', 'speed',
    } or any(not _number(item) for item in timing.values())):
        _invalid('Subtitle timing snapshot must contain finite timing fields')
    if source_id and cue.get('timingConfirmed'):
        tracks = draft.get('tracks')
        if not isinstance(tracks, list):
            _invalid('Subtitle source tracks are invalid')
        source = next((clip for track in tracks if isinstance(track, dict)
                       and isinstance(track.get('clips'), list) for clip in track['clips']
                       if isinstance(clip, dict) and clip.get('id') == source_id
                       and clip.get('kind') == 'audio'), None)
        if source is None or cue.get('sourceClipUrl') != source.get('sourceUrl'):
            _invalid('Subtitle source changed; explicitly replace or unlink it in the editor')
        expected = _source_timing(source)
        actual = cue.get('sourceTiming')
        if not isinstance(actual, dict) or set(actual) != set(expected) or any(
            not _number(actual[key], maximum=86_400_000) or abs(actual[key] - value) > 0.01
            for key, value in expected.items()
        ):
            _invalid('Subtitle timing confirmation must match the current source clip')


def validate_timeline_patch(node, path, value):
    """Return the accepted path, or None for fields owned by another validator."""
    if not path or path[0] != 'draftTimeline':
        return None
    draft = node.get('data', {}).get('draftTimeline')
    if not isinstance(draft, dict):
        _invalid('Open and save an explicit timeline before editing its clips')
    if len(path) == 6 and path[1] == 'tracks' and path[3] == 'clips':
        track = _item(draft.get('tracks'), path[2])
        clip = _item(track.get('clips'), path[4])
        field = path[5]
        if field not in {'timelineStartMs', 'trimStartMs', 'trimEndMs', 'volume', 'muted', 'speed'}:
            _invalid('Agent clip edits cannot change media identity, provenance or source URLs')
        if field == 'muted':
            if type(value) is not bool:
                _invalid('Muted must be boolean')
        elif field == 'volume':
            if not _number(value, maximum=1):
                _invalid('Volume must be between zero and one')
        elif field == 'speed':
            if not _number(value, minimum=0.25, maximum=4):
                _invalid('Playback speed must be between 0.25 and 4')
        elif not _number(value):
            _invalid('Timeline positions must be finite nonnegative milliseconds')
        changed = {**clip, field: value}
        start, end = changed.get('trimStartMs'), changed.get('trimEndMs')
        if not _number(start) or not _number(end) or end <= start:
            _invalid('Clip trim range must be nonempty')
        duration = changed.get('durationMs')
        if _number(duration) and duration > 0 and end > duration:
            _invalid('Clip trim cannot extend beyond its known source duration')
        return tuple(path)
    if len(path) == 5 and path[1] == 'subtitleTrack' and path[2] == 'cues':
        subtitles = draft.get('subtitleTrack')
        cue = _item(subtitles.get('cues') if isinstance(subtitles, dict) else None, path[3])
        if path[4] not in {'text', 'timelineStartMs', 'timelineEndMs', 'timingConfirmed', 'sourceTiming'}:
            _invalid('Subtitle source references cannot be silently changed')
        changed = {**cue, path[4]: deepcopy(value)}
        _validate_cue(changed, draft)
        return tuple(path)
    if path == ['draftTimeline', 'subtitleTrack']:
        if not isinstance(value, dict) or set(value) != {'id', 'cues'}:
            _invalid('Subtitle track requires id and cues')
        if not isinstance(value['id'], str) or not 0 < len(value['id']) <= 128:
            _invalid('Subtitle track id is invalid')
        cues = value['cues']
        if not isinstance(cues, list) or len(cues) > 2000:
            _invalid('Subtitle track must have at most 2000 cues')
        prior = draft.get('subtitleTrack') or {}
        if not isinstance(prior, dict) or not isinstance(prior.get('cues', []), list):
            _invalid('Existing subtitle track is invalid')
        by_id = {cue['id']: cue for cue in prior.get('cues', [])
                 if isinstance(cue, dict) and isinstance(cue.get('id'), str)}
        if prior and value['id'] != prior.get('id'):
            _invalid('The existing subtitle track identity must be retained')
        seen = set()
        for cue in cues:
            if not isinstance(cue, dict):
                _invalid('Subtitle cue must be an object')
            allowed = {'id', 'text', 'timelineStartMs', 'timelineEndMs', 'timing', 'timingConfirmed',
                       'sourceClipId', 'sourceClipUrl', 'sourceTakeId', 'sourceDialogueId', 'sourceGroupId', 'sourceTiming'}
            if set(cue) - allowed:
                _invalid('Subtitle cue has unsupported fields')
            _validate_cue(cue, draft)
            if cue['id'] in seen:
                _invalid('Subtitle ids must be unique')
            seen.add(cue['id'])
            old = by_id.get(cue['id'], {})
            for field in ('sourceClipId', 'sourceClipUrl', 'sourceTakeId', 'sourceDialogueId', 'sourceGroupId'):
                if cue.get(field) != old.get(field):
                    _invalid('New manual cues cannot forge dialogue or take provenance')
            if not old and cue['timing'] != 'manual':
                _invalid('New manual subtitles must explicitly use manual timing')
        return tuple(path)
    _invalid('Only scoped clip edits and subtitle edits are supported')
