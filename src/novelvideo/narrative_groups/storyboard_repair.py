"""Single-cell repair transactions and immutable image prompt provenance."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from uuid import uuid4

from PIL import Image, ImageOps

from . import service
from .storyboard_sources import StoryboardCellSource, StoryboardSource
from novelvideo.utils.safe_paths import resolve_under_root


def _cell(group, stage, shot_id):
    if stage not in {'render', 'sketch'} or shot_id not in group.production_beat_ids:
        raise ValueError('分镜不属于当前组')
    cell = next((c for c in group.stages[stage].cell_assets
                 if str(c.get('shot_id') or c.get('beat_id')) == shot_id), None)
    if cell is None or not cell.get('path'):
        raise ValueError('当前分镜尚无可修复图片')
    return cell


def _fingerprint(root, state):
    cells = [(c.get('shot_id') or c.get('beat_id'), c['path'],
              sha256(resolve_under_root(root, c['path']).read_bytes()).hexdigest())
             for c in state.cell_assets]
    return sha256(json.dumps([state.revision, state.selected_storyboard_id, cells],
                            sort_keys=True).encode()).hexdigest()


def save_prompt_snapshot(root, asset, snapshot):
    path = resolve_under_root(root, asset)
    data = {**snapshot, 'asset_sha256': sha256(path.read_bytes()).hexdigest()}
    target = resolve_under_root(root, str(path) + '.prompt.json')
    temporary = target.with_suffix('.tmp-' + uuid4().hex)
    temporary.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    temporary.replace(target)


def load_prompt_snapshot(root, asset):
    path = resolve_under_root(root, asset)
    target = resolve_under_root(root, str(path) + '.prompt.json')
    try:
        data = json.loads(target.read_text(encoding='utf-8'))
        if data.get('asset_sha256') == sha256(path.read_bytes()).hexdigest():
            return data
    except (OSError, ValueError, AttributeError):
        pass
    return {}


def repair_input(root, group, stage, shot_id, *, synthesized=''):
    cell = _cell(group, stage, shot_id)
    state = group.stages[stage]
    snapshot = load_prompt_snapshot(root, cell['path'])
    pending = state.provider_parameters.get('cell_repair', {})
    same = pending.get('shot_id') == shot_id
    draft = pending if same and pending.get('status') == 'failed' else snapshot
    return {
        'shot_id': shot_id, 'stage': stage, 'source_revision': state.revision,
        'source_asset': _fingerprint(root, state),
        'original_prompt': snapshot.get('original_prompt') or None,
        'prompt': draft.get('prompt') or group.image_prompt_overrides.get(shot_id) or synthesized,
        'feedback': draft.get('feedback') or '',
        'repair_status': pending.get('status', 'idle') if same else (
            pending.get('status') if pending.get('status') in {'queued', 'running'} else 'idle'),
        'repair_error': pending.get('error', '') if same else '',
    }


def reserve_repair(root, episode, group_id, stage, shot_id, *, source_revision,
                   source_asset, prompt, feedback, generation=None):
    if not prompt.strip() or len(prompt) > 16000 or len(feedback) > 4000:
        raise ValueError('请填写分镜提示词，提示词最多 16000 字，修改意见最多 4000 字')
    with service.narrative_group_sidecar_guard(root, episode):
        groups = service.load_materialized_groups(root, episode)
        for index, group in enumerate(groups):
            if group.id != group_id:
                continue
            cell = _cell(group, stage, shot_id)
            state = group.stages[stage]
            previous = state.provider_parameters.get('cell_repair', {})
            if state.status in {'queued', 'running'} or previous.get('status') in {'queued', 'running'}:
                raise RuntimeError('本组图片生成正在进行中')
            if state.revision != source_revision or _fingerprint(root, state) != source_asset:
                raise RuntimeError('分镜版本已变化，请重新打开编辑窗口')
            ticket = {
                'id': uuid4().hex, 'shot_id': shot_id, 'status': 'queued', 'error': '',
                'source_revision': source_revision, 'source_asset': source_asset,
                'source_path': cell['path'], 'prompt': prompt.strip(), 'feedback': feedback.strip(),
                'generation': generation or {}, 'created_at': datetime.now(timezone.utc).isoformat(),
            }
            if (previous.get('status') == 'failed' and previous.get('generation_result')
                and all(previous.get(key) == ticket[key] for key in (
                    'shot_id', 'source_revision', 'source_asset', 'prompt', 'feedback', 'generation'))):
                ticket['generation_result'] = previous['generation_result']
            groups[index] = replace(group, stages={**group.stages, stage: replace(state,
                provider_parameters={**state.provider_parameters, 'cell_repair': ticket})})
            service.save_groups(root, episode, groups)
            return ticket
        raise KeyError(group_id)


def update_repair(root, episode, group_id, stage, repair_id, *, only_if_active=False, **updates):
    with service.narrative_group_sidecar_guard(root, episode):
        groups = service.load_materialized_groups(root, episode)
        for index, group in enumerate(groups):
            if group.id != group_id:
                continue
            state = group.stages[stage]
            ticket = state.provider_parameters.get('cell_repair', {})
            if ticket.get('id') != repair_id:
                return
            if only_if_active and ticket.get('status') not in {'queued', 'running'}:
                return
            groups[index] = replace(group, stages={**group.stages, stage: replace(state,
                provider_parameters={**state.provider_parameters, 'cell_repair': {**ticket, **updates}})})
            service.save_groups(root, episode, groups)
            return


def _compose(root, group, state, shot_id, target, folder, project_id, episode, repair_id):
    """Recompose locally; unmodified cells retain their original bytes and paths."""
    old_sources = [StoryboardSource.model_validate(raw) for raw in state.storyboard_sources]
    selected = service._storyboard_selection(state, old_sources)
    source = next((s for s in old_sources if selected.get(s.batch_id) == s.source_id
                   and any(c.shot_id == shot_id for c in s.cells)), None)
    if state.selected_storyboard_id and source is None:
        raise ValueError('找不到当前选中的分镜来源')
    if source:
        source.validate_files(Path(root), project_id=project_id)
        shot_ids = [c.shot_id for c in source.cells]
        rows, columns = source.rows, source.columns
    else:
        shot_ids = [m.beat_id for m in group.cell_to_beat]
        rows, columns = group.layout.rows, group.layout.columns
    by_shot = {str(c.get('shot_id') or c.get('beat_id')): c for c in state.cell_assets}
    with Image.open(resolve_under_root(root, by_shot[shot_id]['path'])) as old:
        width, height = old.size
    with Image.open(target) as generated:
        fitted = ImageOps.fit(generated.convert('RGB'), (width, height))
        fitted.save(target, format='PNG')
        fitted.close()
    grid = Image.new('RGB', (width * columns, height * rows))
    cells = []
    try:
        for index, key in enumerate(shot_ids):
            path = target if key == shot_id else resolve_under_root(root, by_shot[key]['path'])
            with Image.open(path) as image:
                if image.size != (width, height):
                    raise ValueError('分镜尺寸不一致，无法安全重组')
                grid.paste(image.convert('RGB'), ((index % columns) * width, (index // columns) * height))
            left, top = (index % columns) * width, (index // columns) * height
            cells.append(StoryboardCellSource(shot_id=key, cell_index=index,
                path=path.relative_to(Path(root).resolve()).as_posix(),
                sha256=sha256(path.read_bytes()).hexdigest(), width=width, height=height,
                crop_box=(left, top, left + width, top + height), scale_size=(width, height)))
        grid_path = folder / 'grid.png'
        grid.save(grid_path)
    finally:
        grid.close()
    replacement = None
    if source:
        replacement = StoryboardSource(project_id=project_id, episode=episode, group_id=group.id,
            batch_id=source.batch_id, asset_id=repair_id, generation_id=repair_id,
            grid_path=grid_path.relative_to(Path(root).resolve()).as_posix(),
            grid_sha256=sha256(grid_path.read_bytes()).hexdigest(), rows=rows, columns=columns,
            splitter_version='single-cell-repair/v1', cells=tuple(cells))
        replacement.validate_files(Path(root), project_id=project_id)
    return grid_path, replacement


def publish_repair(root, episode, group_id, stage, repair_id, generated, *, snapshot, project_id):
    root = Path(root).resolve()
    with service.narrative_group_sidecar_guard(root, episode):
        groups = service.load_materialized_groups(root, episode)
        group = next(g for g in groups if g.id == group_id)
        state = group.stages[stage]
        ticket = state.provider_parameters.get('cell_repair', {})
        if ticket.get('id') == repair_id and ticket.get('status') == 'completed':
            return group
        if ticket.get('id') != repair_id or state.revision != ticket.get('source_revision'):
            raise RuntimeError('分镜版本已变化，生成结果已保留但未覆盖当前图片')
        if _fingerprint(root, state) != ticket['source_asset']:
            raise RuntimeError('分镜版本已变化，生成结果已保留但未覆盖当前图片')
        folder = resolve_under_root(root, f'frames/ep{episode:03d}/repairs/{repair_id}')
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / 'cell.png'
        target.write_bytes(resolve_under_root(root, generated).read_bytes())
        grid_path, source = _compose(root, group, state, ticket['shot_id'], target, folder,
                                     project_id, episode, repair_id)
        save_prompt_snapshot(root, target, snapshot)
        original = replace(state, provider_parameters={k: v for k, v in state.provider_parameters.items()
                                                       if k != 'cell_repair'})
        if source:
            from .storyboard_binding import selection_identity

            service._validate_storyboard_batch(group, source)
            sources = [StoryboardSource.model_validate(raw) for raw in state.storyboard_sources]
            selection = service._storyboard_selection(state, sources)
            selection[source.batch_id] = source.source_id
            sources.append(source)
            selected = [s for s in sources if selection.get(s.batch_id) == s.source_id]
            cells_by_shot = {c.shot_id: {
                'cell': c.cell_index, 'beat_id': c.shot_id, 'shot_id': c.shot_id,
                'path': str(root / c.path), 'sha256': c.sha256, 'storyboard_source_id': s.source_id,
            } for s in selected for c in s.cells}
            new_state = replace(state,
                storyboard_sources=tuple(s.model_dump(mode='json') for s in sources),
                selected_storyboard_sources=selection, selected_storyboard_id=selection_identity(selection),
                grid_asset=str(grid_path) if len(selection) == 1 else '',
                cell_assets=tuple(cells_by_shot[shot] for shot in group.production_beat_ids if shot in cells_by_shot))
        else:
            cells = tuple({**c, 'path': str(target), 'sha256': sha256(target.read_bytes()).hexdigest()}
                          if str(c.get('shot_id') or c.get('beat_id')) == ticket['shot_id'] else c
                          for c in state.cell_assets)
            new_state = replace(state, cell_assets=cells, grid_asset=str(grid_path))
        new_state = replace(new_state, revision=state.revision + 1, status='completed', error='',
            actual_provider=str(snapshot.get('provider_id') or state.actual_provider),
            actual_model=str(snapshot.get('model') or state.actual_model),
            created_at=datetime.now(timezone.utc).isoformat(),
            revision_history=(*state.revision_history, service._stage_snapshot(original)),
            provider_parameters={**new_state.provider_parameters, 'cell_repair': {
                **ticket, 'status': 'completed', 'result_asset': str(target), 'error': ''}})
        stages = {**group.stages, stage: new_state}
        for downstream in (('video',) if stage == 'render' else ('render', 'video')):
            stages[downstream] = replace(stages[downstream], needs_regeneration=True,
                                        stale_reason=f'{stage}_revision_changed')
        updated = replace(group, stages=stages)
        groups = service.load_materialized_groups(root, episode)
        service.save_groups(root, episode, [updated if g.id == group_id else g for g in groups])
        return updated
