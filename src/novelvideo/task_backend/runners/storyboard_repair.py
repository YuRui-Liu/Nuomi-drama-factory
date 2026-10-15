"""One paid image request followed by an atomic single-cell publication."""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import re

from PIL import Image

from novelvideo.narrative_groups import service
from novelvideo.narrative_groups.storyboard_repair import (
    _fingerprint, load_prompt_snapshot, publish_repair, update_repair,
)
from novelvideo.utils.safe_paths import resolve_under_root


def single_generation_input(payload):
    from .narrative_group import GroupGenerationInput

    root = Path(payload['project_dir'])
    references = []
    for reference in payload['repair_references']:
        path = resolve_under_root(root, reference['path'])
        if sha256(path.read_bytes()).hexdigest() != reference['sha256']:
            raise ValueError('修复参考图已变化，请重新提交')
        references.append(str(path))
    mode = 'black-and-white storyboard sketch' if payload['stage'] == 'sketch' else 'final cinematic image'
    parts = [f'Generate exactly ONE {mode}, a single camera frame, no grid, montage, panels, borders or captions.',
             f"Compose at {payload['aspect_ratio']} aspect ratio.",
             'Reference 1 is the original frame. Preserve character identity, setting and visual style; apply the requested corrections.',
             'Use each remaining reference according to its labelled role. Continuity frames establish '
             'shared world geometry, ground elevation, thresholds and relative scale, not a pose to copy. '
             'Do not import characters, actions or locations from a different scene. '
             'Never raise the ground into a platform to fit faces and ground writing into a close shot; '
             'adjust camera framing instead when the correction requires it.',
             payload.get('image_projection', ''), payload.get('adjacent_keyframe_context', ''), payload['repair_prompt']]
    if payload.get('keyframe_prompt') and payload['keyframe_prompt'] != payload['repair_prompt']:
        parts.append('Original frozen keyframe direction (preserve spatial facts except those explicitly '
                     'changed by the requested correction; do not infer a new action):\n' + payload['keyframe_prompt'])
    mappings = ['original frame: preserve character identity, setting and visual style']
    mappings.extend(re.sub(r'; use for panels [^.]*\.', '', str(value))
                    for value in payload.get('repair_reference_mappings') or ())
    for index, mapping in enumerate(mappings[1:], start=2):
        if mapping:
            parts.append(f'Reference {index}: {mapping}')
    if payload.get('repair_feedback'):
        parts.append('The explicit correction below overrides conflicting original-frame geometry and '
                     'old framing notes. Preserve all unaffected story facts, identities and visual style.')
        parts.append('修改意见（应用于当前分镜）：\n' + payload['repair_feedback'])
    return GroupGenerationInput(prompt='\n'.join(p for p in parts if p), references=tuple(references),
                                reference_mappings=tuple(mappings))


async def execute_repair(envelope, ctx):
    from . import narrative_group as runner

    payload = envelope.get('payload') or envelope
    root = runner._project_dir(payload, ctx)
    episode, group_id, stage = int(payload['episode']), str(payload['group_id']), str(payload['stage'])
    repair_id = str(payload['repair_id'])
    with service.narrative_group_sidecar_guard(root, episode):
        group = next(g for g in service.load_materialized_groups(root, episode) if g.id == group_id)
        state = group.stages[stage]
        ticket = state.provider_parameters.get('cell_repair', {})
        if ticket.get('id') != repair_id:
            raise RuntimeError('修复任务已被替代')
        if ticket['status'] == 'completed':
            return {'status': 'completed', 'cell_asset': ticket.get('result_asset')}
        generated = ticket.get('generation_result')
        if ticket['status'] != 'queued' and not generated:
            raise RuntimeError('修复任务已执行，未自动重复生成；请查看任务结果')
        if _fingerprint(root, state) != ticket['source_asset']:
            update_repair(root, episode, group_id, stage, repair_id, status='failed', error='分镜版本已变化')
            raise RuntimeError('分镜版本已变化')
        update_repair(root, episode, group_id, stage, repair_id, status='running')
    try:
        if not generated:
            original = resolve_under_root(root, ticket['source_path'])
            snapshot = load_prompt_snapshot(root, original)
            with Image.open(original) as image:
                aspect = '16:9' if image.width > image.height else '9:16'
            references = [{'path': str(original), 'sha256': sha256(original.read_bytes()).hexdigest()}]
            mappings = []
            # Match the comparison dialog: immediate neighbours, using the current
            # render where available, otherwise sketch. Never reuse stale neighbours
            # from the previous repair's prompt snapshot.
            shot_ids = [m.beat_id for m in group.cell_to_beat]
            position = shot_ids.index(ticket['shot_id'])
            for offset in (-1, 1):
                neighbour_index = position + offset
                if not 0 <= neighbour_index < len(shot_ids):
                    continue
                neighbour_id = shot_ids[neighbour_index]
                cell = next((c for s in ('render', 'sketch') for c in group.stages[s].cell_assets
                             if str(c.get('shot_id') or c.get('beat_id')) == neighbour_id and c.get('path')), None)
                if cell is None:
                    continue
                path = resolve_under_root(root, cell['path'])
                references.append({'path': str(path), 'sha256': sha256(path.read_bytes()).hexdigest()})
                mappings.append(f'continuity frame {neighbour_id}: shared scene geometry and ground elevation only; '
                                'preserve this shot\'s own action and camera angle')
            generation = ticket.get('generation') or {}
            recorded = list(snapshot.get('references') or ())
            original_mappings = list(snapshot.get('reference_mappings') or ())
            if not recorded:
                # Older generations retained formal identity provenance in the
                # stage audit, before exact prompt snapshots were introduced.
                for item in (generation.get('reference_audit') or {}).get('asset_references') or ():
                    if item.get('shot_ids') and ticket['shot_id'] not in item['shot_ids']:
                        continue
                    recorded.append({'path': item['asset_path'], 'sha256': item['sha256']})
                    original_mappings.append(str(item.get('entity_key') or ''))
            seen = {r['path'] for r in references}
            for index, reference in enumerate(recorded):
                # A previous repair's frame reference is replaced by the latest
                # frame, rather than accumulating obsolete frames on every edit.
                if index == 0 and snapshot.get('source_asset'):
                    continue
                if index < len(original_mappings) and original_mappings[index] == 'storyboard sketch grid':
                    continue
                if index < len(original_mappings) and original_mappings[index].startswith('continuity frame '):
                    continue
                if reference['path'] in seen or len(references) >= 9:
                    continue
                references.append(reference)
                seen.add(reference['path'])
                mappings.append(original_mappings[index] if index < len(original_mappings) else '')
            data = {**payload, **generation, 'revision': state.revision + 1,
                'single_storyboard_repair': True, 'repair_prompt': ticket['prompt'],
                'repair_feedback': ticket['feedback'], 'repair_references': references,
                'repair_reference_mappings': mappings,
                'keyframe_prompt': snapshot.get('keyframe_prompt') or snapshot.get('prompt', ''),
                'adjacent_keyframe_context': snapshot.get('adjacent_keyframe_context', ''),
                'image_projection': snapshot.get('image_projection', generation.get('image_projection', '')),
                'aspect_ratio': aspect, 'layout': {'rows': 1, 'columns': 1, 'capacity': 1}}
            from novelvideo.costs.context import CostContext, cost_context
            from novelvideo.costs.providers import requested_cost_context

            with cost_context(CostContext(project_id=str(payload['project_id']),
                    task_id=str(envelope.get('task_id') or ticket.get('task_id') or repair_id),
                    resource_id=ticket['shot_id'], media_type='image')), requested_cost_context(
                        'image', attempt_id=repair_id, usage={'item': '1'},
                        specifications=(('aspect_ratio', aspect),)):
                generated = await runner._generate_grid(data, ctx)
            # Persist before publication so a publish failure can reuse paid media.
            update_repair(root, episode, group_id, stage, repair_id, generation_result=generated)
        snapshot = {**generated.get('prompt_snapshot', {}), 'prompt': ticket['prompt'],
                    'feedback': ticket['feedback'], 'source_asset': ticket['source_asset']}
        result = publish_repair(root, episode, group_id, stage, repair_id, generated['grid_asset'],
                                snapshot=snapshot, project_id=str(payload['project_id']))
        return {'status': 'completed', 'revision': result.stages[stage].revision,
                'cell_asset': result.stages[stage].provider_parameters['cell_repair']['result_asset']}
    except BaseException as exc:
        update_repair(root, episode, group_id, stage, repair_id, only_if_active=True,
                      status='failed', error=str(exc))
        raise
