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
             'Use the remaining references only for matching subjects in this frame; do not add unrelated characters or scenes.',
             payload.get('image_projection', ''), payload['repair_prompt']]
    mappings = ['original frame: preserve character identity, setting and visual style']
    mappings.extend(re.sub(r'; use for panels [^.]*\.', '', str(value))
                    for value in payload.get('repair_reference_mappings') or ())
    for index, mapping in enumerate(mappings[1:], start=2):
        if mapping:
            parts.append(f'Reference {index}: {mapping}')
    if payload.get('repair_feedback'):
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
            seen = {str(original)}
            for index, reference in enumerate(recorded):
                # A previous repair's frame reference is replaced by the latest
                # frame, rather than accumulating obsolete frames on every edit.
                if index == 0 and snapshot.get('source_asset'):
                    continue
                if index < len(original_mappings) and original_mappings[index] == 'storyboard sketch grid':
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
