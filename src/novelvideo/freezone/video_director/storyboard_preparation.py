"""Persist storyboard crops and an editable Director draft before submission."""
from __future__ import annotations

import hashlib
import os
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

from novelvideo.freezone.canvas_store import read_canvas, save_canvas, utc_now_iso
from novelvideo.freezone.orchestration.adapters.requests import director_draft
from novelvideo.freezone.orchestration.models import fingerprint
from .capabilities import validate_generation
from .models import DirectorDraft, DirectorImage
from .storyboard_adaptation import adapt_image, build_draft


def browser_draft(value):
    names = {'schema_version': 'schemaVersion', 'model_id': 'modelId', 'aspect_ratio': 'aspectRatio',
             'duration_seconds': 'durationSeconds', 'first_frame': 'firstFrame', 'last_frame': 'lastFrame',
             'image_id': 'imageId', 'asset_id': 'assetId', 'character_id': 'characterId', 'variant_id': 'variantId',
             'variant_label': 'variantLabel', 'asset_kind': 'assetKind', 'case_reference': 'caseReference'}
    if isinstance(value, dict):
        # Frontend draftToWire forwards caseReference verbatim: its nested
        # provenance fields intentionally retain their API snake_case names.
        return {names.get(k, k): deepcopy(v) if k == 'case_reference' else browser_draft(v)
                for k, v in value.items()}
    if isinstance(value, list):
        return [browser_draft(v) for v in value]
    return value


def _source(canvas, node_id):
    nodes = {n['id']: n for n in canvas['nodes']}
    node = nodes.get(node_id)
    if node is None or node['type'] != 'videoDirectorNode':
        raise ValueError('Director node not found')
    candidates = {e['source'] for e in canvas['edges'] if e['target'] == node_id
                  and nodes.get(e['source'], {}).get('type') == 'storyboardGenNode'}
    if len(candidates) != 1:
        raise ValueError('exactly one incoming storyboard is required')
    return node, nodes[candidates.pop()]


def _adapt(ctx, board, ratio, rects=None):
    from .service import _image_path
    data = board['data']
    path = _image_path(ctx, data.get('imageUrl', ''))
    if path.stat().st_size > 20 * 1024 * 1024:
        raise ValueError('storyboard image exceeds 20 MiB')
    return adapt_image(path.read_bytes(), data.get('gridRows', 2), data.get('gridCols', 2), ratio,
                       rects if rects is not None else data.get('cellRects'))


def _source_layout_changed(board, preparation):
    data = board['data']
    return bool(preparation) and (
        preparation.get('sourceNodeId') != board['id']
        or preparation.get('sourceUrl') != data.get('imageUrl')
        or preparation.get('sourceGridRows') != data.get('gridRows', 2)
        or preparation.get('sourceGridCols') != data.get('gridCols', 2))


def _mode(data):
    # The saved mode is the contract shared by the editor and CLI. A UI-only
    # intent flag must never turn valid Ref inputs into missing frame pairs.
    if data.get('activeInputMode') in {'ref', 'frames'}:
        return data['activeInputMode']
    draft = data.get('draft') or {}
    if draft.get('references'):
        return 'ref'
    if any(s.get('firstFrame') or s.get('first_frame') for s in draft.get('segments', [])):
        return 'frames'
    return 'ref'


def _references(ctx, canvas, node, board, draft, previous):
    """Only identity/asset references; storyboard cells belong to frame slots."""
    from .service import _image_path
    nodes = {n['id']: n for n in canvas['nodes']}
    connected = {e['source'] for e in canvas['edges'] if e['target'] == node['id'] and e['source'] != board['id']
                 and e.get('data', {}).get('slot', {}).get('kind', 'reference') == 'reference'}
    order = node['data'].get('referenceOrder', [])
    rank = {key: index for index, key in enumerate(order)}
    ids = sorted(connected, key=lambda key: (rank.get(key, len(rank)), key))
    refs, bindings = [], []
    for key in ids:
        source = nodes.get(key, {}).get('data', {})
        url = source.get('imageUrl') or source.get('committed_slot_url') or source.get('previewImageUrl')
        if not url:
            raise ValueError(f'connected reference image is unavailable: {key}')
        image = {'image_id': key, 'url': url}
        for camel, snake in [('assetId', 'asset_id'), ('characterId', 'character_id'), ('variantId', 'variant_id'),
                             ('variantLabel', 'variant_label'), ('assetKind', 'asset_kind')]:
            if source.get(camel) is not None:
                image[snake] = source[camel]
        if key == board['id']:
            image['asset_kind'] = 'storyboard'
        refs.append(DirectorImage.model_validate(image).model_dump(mode='json'))
    managed = {b['imageId'] for b in previous.get('refBindings', []) if b.get('sourceNodeId')}
    connected_urls = {r['url'] for r in refs}
    for image in draft.get('references', []):
        if image['image_id'] == board['id'] or image.get('asset_kind') == 'storyboard' or image['url'] == board['data'].get('imageUrl'):
            continue
        if image['image_id'] not in managed and image['image_id'] not in ids and image['url'] not in connected_urls:
            refs.append(deepcopy(image))
    seen = set()
    unique = []
    for image in refs:
        if image['image_id'] in seen:
            continue
        seen.add(image['image_id'])
        path = _image_path(ctx, image['url'])
        if path.stat().st_size > 20 * 1024 * 1024:
            raise ValueError('reference image exceeds 20 MiB')
        bindings.append({'sourceNodeId': image['image_id'] if image['image_id'] in ids else None,
                         'imageId': image['image_id'], 'url': image['url'], 'pictureIndex': len(unique) + 1,
                         'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
        unique.append(image)
    return unique, bindings


def prepare_storyboard(ctx, *, canvas_id: str, node_id: str, base_revision: int, request_id: str,
                       resolution='safe', shots=None, rects=None, reference_limit=5) -> dict:
    if resolution not in {'safe', 'keep_edits', 'replace_generated'}:
        raise ValueError('invalid edit resolution')
    from novelvideo.api.routes.freezone import _canvas_state_project_dir
    root = Path(ctx.output_dir)
    state_root = _canvas_state_project_dir(ctx, root)
    request_hash = fingerprint([node_id, resolution, shots, rects])
    response = {}

    def build(existing):
        if not existing:
            raise ValueError('canvas not found')
        canvas = deepcopy(existing)
        node, board = _source(canvas, node_id)
        data = node['data']
        mode = _mode(data)
        old = DirectorDraft.model_validate(director_draft(data.get('draft') or {})).model_dump(mode='json')
        previous = data.get('storyboardPreparation') or {}
        layout_changed = _source_layout_changed(board, previous)
        if layout_changed and rects is None:
            board['data'].pop('cellRects', None)
        adapted = _adapt(ctx, board, old.get('aspect_ratio', '16:9'), rects)
        existing_frames = board['data'].get('frames', [])
        descriptors = deepcopy(shots if shots is not None else existing_frames)
        for index, descriptor in enumerate(descriptors):
            previous_frame = existing_frames[index] if index < len(existing_frames) else {}
            descriptor['id'] = descriptor.get('id') or previous_frame.get('id') or f'{board["id"]}-cell-{index + 1}'
            old_source = previous.get('sourceNodeId')
            if old_source and old_source != board['id'] and descriptor['id'] == f'{old_source}-cell-{index + 1}':
                descriptor['id'] = f'{board["id"]}-cell-{index + 1}'
            if 'referenceIndex' in previous_frame:
                descriptor['referenceIndex'] = previous_frame['referenceIndex']
            if 'static_panel' in previous_frame:
                descriptor['static_panel'] = deepcopy(previous_frame['static_panel'])
                descriptor['static_source_description'] = previous_frame.get('static_source_description')
        duration = previous.get('requestedDurationSeconds') or sum(s.get('duration_seconds', 0) for s in old.get('segments', []))
        references, bindings = _references(ctx, canvas, node, board, old, previous) if mode == 'ref' else ([], [])
        frame_pairs = board['data'].get('videoFramePairs')
        if any(s.get('static_panel') for s in descriptors) and not frame_pairs:
            raise ValueError('尚未选择视频起始画格：请使用 AI 推荐或手动选择首帧；尾帧可选，不能将相邻镜头自动当作首尾帧')
        settings_hash = fingerprint([board['id'], adapted['fingerprint'], descriptors, frame_pairs, old.get('aspect_ratio'), old.get('resolution'), old.get('model_id'), mode, references, bindings])
        if (not layout_changed and previous.get('settingsFingerprint') == settings_hash
                and old.get('references', []) == references):
            validate_generation(DirectorDraft.model_validate(old), reference_limit=reference_limit)
            response.update(canvas=existing, draft=old, preparation=previous)
            return existing
        baseline = previous.get('baselineDraft')
        changed = baseline is not None and old != baseline
        if changed and resolution == 'safe':
            raise ValueError('manual edits conflict with changed storyboard; choose keep_edits or replace_generated')
        from novelvideo.api.deps import make_static_url_for_context
        cells = []
        for i, cell in enumerate(adapted['cells']):
            stored = {'id': f'{board["id"]}-cell-{i + 1}', 'rect': cell['rect']}
            for field, key in [('crop', 'cropUrl'), ('normalized', 'imageUrl')]:
                digest = hashlib.sha256(cell[field]).hexdigest()
                rel = f'freezone/_outputs/storyboard_cells/{digest}.png'
                path = root / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                if not path.exists():
                    temporary = path.with_name('.' + path.name + '.' + uuid4().hex)
                    try:
                        temporary.write_bytes(cell[field])
                        os.replace(temporary, path)
                    finally:
                        temporary.unlink(missing_ok=True)
                stored[key] = make_static_url_for_context(ctx, rel)
            cells.append(stored)
        template = {k: old[k] for k in ('schema_version', 'revision', 'model_id', 'aspect_ratio', 'resolution') if k in old}
        fresh = build_draft(board['id'], template, [c['imageUrl'] for c in cells], descriptors, duration, frame_pairs=frame_pairs)
        if mode == 'ref':
            fresh['references'] = references
        fresh['revision'] = old.get('revision', 0) + 1
        generated = deepcopy(fresh)
        if changed and resolution == 'keep_edits':
            old_segments = {s['id']: s for s in old.get('segments', [])}
            baseline_segments = {s['id']: s for s in baseline.get('segments', [])}
            if set(old_segments) != set(baseline_segments):
                raise ValueError('manual segment structure changed; replace_generated is required')
            for segment in fresh['segments']:
                for key in ('prompt', 'duration_seconds', 'first_frame', 'last_frame', 'technique', 'case_reference'):
                    before = baseline_segments.get(segment['id'], {})
                    current = old_segments.get(segment['id'], {})
                    if current.get(key) != before.get(key):
                        segment[key] = current.get(key)
        validated = DirectorDraft.model_validate(fresh)
        timing = validate_generation(validated, reference_limit=reference_limit)
        fresh = validated.model_dump(mode='json')
        generated = DirectorDraft.model_validate(generated).model_dump(mode='json')
        metadata = {'sourceNodeId': board['id'], 'sourceFingerprint': adapted['fingerprint'],
                    'inputMode': mode, 'refBindings': bindings,
                    'sourceUrl': board['data']['imageUrl'], 'sourceWidth': adapted['width'], 'sourceHeight': adapted['height'],
                    'sourceGridRows': board['data'].get('gridRows', 2), 'sourceGridCols': board['data'].get('gridCols', 2),
                    'settingsFingerprint': settings_hash, 'status': 'prepared', 'baselineDraft': generated,
                    'cells': cells, 'requestedDurationSeconds': duration,
                    'actualDurationSeconds': timing.actual_duration_seconds, 'actualTotalFrames': timing.total_frames}
        board['data'].update(cells=cells, frames=descriptors)
        if rects is not None:
            board['data']['cellRects'] = rects
        data.update(draft=browser_draft(fresh), storyboardPreparation=metadata,
                    autoStoryboard={'sourceNodeId': board['id']}, activeInputMode=mode)
        canvas.update(revision=existing['revision'] + 1, updated_at=utc_now_iso())
        response.update(canvas=canvas, draft=fresh, preparation=metadata)
        return canvas

    # The build callback resolves and compares the source while holding the canvas write lock.
    saved = save_canvas(state_root, canvas_id, base_revision=base_revision, build_payload=build,
                        client_save_id='storyboard-' + request_id, request_hash=request_hash,
                        save_source='storyboard_preparation')
    if not response:
        canvas = read_canvas(state_root, canvas_id)
        node, _ = _source(canvas, node_id)
        response.update(canvas=canvas, draft=director_draft(node['data']['draft']),
                        preparation=node['data'].get('storyboardPreparation'))
    return response


def guard_prepared(ctx, canvas_id, node_id, draft):
    from novelvideo.api.routes.freezone import _canvas_state_project_dir
    canvas = read_canvas(_canvas_state_project_dir(ctx, Path(ctx.output_dir)), canvas_id)
    if not canvas:
        return
    node = next((n for n in canvas['nodes'] if n['id'] == node_id), None)
    if not node:
        return
    data = node['data']
    board_ids = {n['id'] for n in canvas['nodes'] if n.get('type') == 'storyboardGenNode'}
    incoming_board = any(e.get('target') == node_id and e.get('source') in board_ids
                         for e in canvas.get('edges', []))
    if not incoming_board and not data.get('autoStoryboard') and not data.get('storyboardPreparation'):
        return
    node, board = _source(canvas, node_id)
    prepared = data.get('storyboardPreparation') or {}
    if not prepared or prepared.get('status') != 'prepared':
        raise ValueError('storyboard preparation is required before generation')
    supplied = draft.model_dump(mode='json') if isinstance(draft, DirectorDraft) else draft
    supplied = DirectorDraft.model_validate(supplied).model_dump(mode='json')
    persisted = DirectorDraft.model_validate(director_draft(data.get('draft') or {})).model_dump(mode='json')
    if supplied != persisted:
        raise ValueError('submitted draft differs from persisted prepared draft; save and prepare before generation')
    if _source_layout_changed(board, prepared):
        raise ValueError('stale storyboard preparation; source URL or grid layout changed')
    mode = _mode(data)
    if mode != prepared.get('inputMode'):
        raise ValueError('stale storyboard preparation; input mode changed')
    adapted = _adapt(ctx, board, supplied['aspect_ratio'])
    references, bindings = _references(ctx, canvas, node, board, supplied, prepared) if mode == 'ref' else ([], [])
    if supplied.get('references', []) != references:
        raise ValueError('stale storyboard reference bindings; prepare again before generation')
    settings_hash = fingerprint([board['id'], adapted['fingerprint'], board['data'].get('frames', []),
                                 board['data'].get('videoFramePairs'),
                                 supplied.get('aspect_ratio'), supplied.get('resolution'), supplied.get('model_id'), mode, references, bindings])
    if (adapted['fingerprint'] != prepared.get('sourceFingerprint')
            or board['id'] != prepared.get('sourceNodeId')
            or settings_hash != prepared.get('settingsFingerprint')):
        raise ValueError('stale storyboard preparation; prepare again before generation')
    if supplied['revision'] != data['draft']['revision']:
        raise ValueError('stale Director draft revision')
