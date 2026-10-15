"""Canvas projection of the shared director contract, never a second director."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

from novelvideo.director_plan.models import SourceSpan
from novelvideo.director_plan.planner import DirectorPlanInput, DirectorPlanner
from novelvideo.director_plan.execution import require_executable_shots


def character_asset(name, current):
    current = current or {}
    return {'kind': 'character', 'name': name, 'version_id': current.get('version_id'),
            'image_url': current.get('sheet_url') if current.get('adoption_status') == 'adopted' else None}


def bind_canvas_designs(catalog, bindings, *, project_dir, project_id):
    """Explicit episode-scoped full designs; never infer a sheet from an avatar."""
    from .verification import verify_media
    result = {(a['kind'], a['name']): dict(a) for a in catalog}
    seen = set()
    for binding in bindings:
        name = str(binding.get('name', '')).strip()
        if not name or binding.get('role') != 'base_design':
            raise ValueError('base_design binding requires a name and explicit base_design role')
        key = ('character', name)
        if key in seen:
            raise ValueError(f'duplicate base_design binding: {name}')
        seen.add(key)
        if result.get(key, {}).get('image_url'):
            raise ValueError(f'base_design already adopted: {name}; use the library design')
        url = binding.get('image_url')
        if not isinstance(url, str) or not url:
            raise ValueError(f'base_design image_url required: {name}')
        evidence = verify_media(Path(project_dir), project_id, url, 'image')
        if binding.get('sha256') and binding['sha256'] != evidence['sha256']:
            raise ValueError(f'base_design image changed: {name}')
        result[key] = {'kind': 'character', 'name': name, 'image_url': url,
                       'asset_sha256': evidence['sha256'], 'version_id': 'sha256:' + evidence['sha256']}
    return list(result.values())


async def project_asset_catalog(ctx):
    """Read adopted base designs through the same resolver as the asset UI."""
    from novelvideo.api.deps import make_sqlite_store_for_context, make_static_url_for_context
    from novelvideo.api.routes.character_casting import current_version
    from novelvideo.utils.path_resolver import compute_scene_master_path, compute_prop_reference_path
    store = await make_sqlite_store_for_context(ctx)
    try:
        catalog = [character_asset(c.name, current_version(ctx, c.name, None))
                   for c in store.get_all_characters()]
        root = Path(ctx.output_dir)
        for kind, records, resolve in [('scene', await store.list_scenes(), compute_scene_master_path),
                                      ('prop', await store.list_props(), compute_prop_reference_path)]:
            for record in records:
                path = resolve(root, record.name)
                asset = {'kind': kind, 'name': record.name, 'image_url': None}
                if path:
                    digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
                    asset.update(image_url=make_static_url_for_context(ctx, Path(path).relative_to(root).as_posix(), local_path=Path(path)),
                                 asset_sha256=digest, version_id='sha256:' + digest)
                catalog.append(asset)
        return catalog
    finally:
        await store.close()


def source_spans(script):
    from .planning import source_scenes
    scenes = source_scenes(script)
    return tuple(SourceSpan(id=f'line-{i}', ordinal=i,
        scene=next(s['heading'] for s in scenes if s['start_line'] <= i <= s['end_line']),
        time='', text=line, dialogue_text=line if re.match(r'^(?!场景[：:]|人物[：:]|出场人物[：:]|时间[：:]|地点[：:]|时长[：:])[^#△：:]{1,30}[：:]\s*\S', line.strip()) else '')
        for i, line in enumerate(script.splitlines(), 1)
        if i >= scenes[0]['start_line'] and line.strip())


def project_draft(script, draft, asset_catalog=()):
    from .planning import source_scenes
    spans = {s.id: s for s in source_spans(script)}
    scenes = source_scenes(script)
    assets, groups = {}, []
    catalog = {(a['kind'], a['name']): a for a in asset_catalog}
    used_shot_ids = set()
    for group in draft.groups:
        if not group.source_span_ids or set(group.source_span_ids) - spans.keys():
            raise ValueError('unknown or empty director source span')
        ordinals = [spans[key].ordinal for key in group.source_span_ids]
        start, end = min(ordinals), max(ordinals)
        scene = next(s for s in scenes if s['start_line'] <= start <= s['end_line'])
        if end > scene['end_line']:
            raise ValueError('director source crosses scene boundary')
        expected = {key for key, span in spans.items() if start <= span.ordinal <= end}
        if set(group.source_span_ids) != expected:
            raise ValueError('director source coverage has a hole')
        shots, asset_ids = [], []
        for shot in group.shots:
            if shot.id in used_shot_ids or not shot.source_span_ids or set(shot.source_span_ids) - set(group.source_span_ids):
                raise ValueError('duplicate shot or invalid shot source')
            used_shot_ids.add(shot.id)
            if any(key not in spans or not spans[key].dialogue_text for key in shot.dialogue_source_ids):
                raise ValueError('invalid dialogue source')
            for req in shot.asset_requirements:
                kind = 'character' if req.kind.startswith('character') else 'scene' if req.kind.startswith('scene') else 'prop'
                key = (kind, req.entity_key)
                asset_id = 'ref-' + hashlib.sha256(f'{kind}:{req.entity_key}'.encode()).hexdigest()[:12]
                asset = catalog.get(key)
                design_prompt = req.design_notes or req.entity_key
                if kind == 'character':
                    design_prompt += '\n人物基础形象设计图：同一人物正面、侧面、背面全身与面部细节，服装和外形一致；简单背景，无剧情场景，无文字。不可只生成头像。'
                if kind == 'prop' and req.required and not asset:
                    raise ValueError(f'existing_prop_required: {req.entity_key}')
                if kind == 'prop' and not req.required and not asset:
                    continue
                assets[asset_id] = {'id': asset_id, 'kind': kind, 'name': req.entity_key,
                    'prompt': design_prompt,
                    **({k: asset[k] for k in ('image_url', 'version_id', 'asset_sha256') if k in asset} if asset else {})}
                if asset_id not in asset_ids:
                    asset_ids.append(asset_id)
            description = '\n'.join([
                f'镜头 {shot.id}；{shot.shot_size}；{shot.camera_angle}；{shot.composition}',
                f'起始画面：{shot.visible_start_state}', f'动作：{shot.action}',
                f'结束画面：{shot.visible_end_state}', f'运镜：{shot.camera_motion}',
                f'导演意图：{shot.intent.model_dump_json() if shot.intent else group.objective}',
                f'原文对白：{" / ".join(spans[key].dialogue_text for key in shot.dialogue_source_ids) or "无"}',
                f'导演决策：{shot.creative.model_dump_json() if shot.creative else ""}',
                f'空间与灯光：{shot.cinematography.model_dump_json() if shot.cinematography else ""}',
            ])
            projected = {'id': shot.id, 'description': description, 'duration_seconds': shot.duration_seconds}
            if shot.static_panel is not None:
                static = shot.static_panel.model_dump(mode='json')
                if static['shot_id'] != shot.id:
                    raise ValueError('static panel shot_id differs from director shot')
                for subject in static['subjects']:
                    key = ('character', subject['entity_id'])
                    asset_id = 'ref-' + hashlib.sha256(f'{key[0]}:{key[1]}'.encode()).hexdigest()[:12]
                    if asset_id not in asset_ids:
                        raise ValueError('static subject has no declared character asset')
                    subject['appearance_reference'] = 'asset-' + asset_id
                projected.update(static_panel=static, static_source_description=description)
            shots.append(projected)
        groups.append({'id': group.id, 'scene_id': scene['id'], 'start_line': start, 'end_line': end,
            'duration': sum(s.duration_seconds for s in group.shots),
            'prompt': f'戏剧目标：{group.objective}\n可见转折：{group.visible_turn}',
            'asset_ids': asset_ids, 'shots': shots, 'director_plan': group.model_dump(mode='json')})
    return {'title': script.splitlines()[0].lstrip('# ').strip(), 'assets': list(assets.values()), 'groups': groups}


async def direct_canvas(script, *, duration, aspect_ratio, director_brief='', asset_catalog=(), project_style=''):
    """Use the same author/service/schema as the episode director workflow."""
    episode_match = re.search(r'第\s*(\d+)\s*集', script.splitlines()[0])
    input = DirectorPlanInput(episode=int(episode_match.group(1)) if episode_match else 1, source_script_hash=hashlib.sha256(script.encode()).hexdigest(),
        source_spans=source_spans(script), relevant_bible={'assets': list(asset_catalog)},
        aspect_ratio=aspect_ratio, creative_contract_version=1,
        style_director={'project_style': project_style, 'studio_config': {'preferences': {
            'director_brief': director_brief, 'total_runtime_seconds': duration,
            'source_coverage': 'Cover every supplied source span. With no semantic beats supplied, use empty dramatic_beat_ids.',
            'asset_binding': 'Reuse exact catalog entity names and supplied base design references. For a source character without a base design, declare a character asset requirement with full appearance and costume design notes; the canvas will generate that design before storyboards. Never substitute an avatar for a full design.',
            'canvas_contract': 'Use ASCII group IDs. Preserve explicit scene durations. Group boundaries follow dramatic turns, not provider request duration. Preserve motivated cuts and never invent filler panels. Return an explicit intent for every shot.',
        }}}, project_style_snapshot_id='canvas-' + hashlib.sha256(project_style.encode()).hexdigest()[:16])
    planner = DirectorPlanner()
    draft = await planner.plan_episode(input)
    require_executable_shots(draft.groups)
    from novelvideo.director_plan.models import DirectorPlanRevision
    from novelvideo.director_plan.validation import validate_director_plan
    revision = DirectorPlanRevision.new(episode=input.episode, source_script_hash=input.source_script_hash,
        director_model=planner.model_name, prompt_version=input.prompt_version,
        project_style_snapshot_id=input.project_style_snapshot_id, groups=draft.groups,
        creative_contract_version=1)
    report = validate_director_plan(revision, input.source_spans)
    if not report.passed:
        raise ValueError(report.model_dump_json())
    if any(shot.intent is None for group in draft.groups for shot in group.shots):
        raise ValueError('Every director shot requires an explicit purpose and continuity strategy')
    spoken = [key for group in draft.groups for shot in group.shots for key in shot.dialogue_source_ids]
    expected = {span.id for span in input.source_spans if span.dialogue_text}
    if set(spoken) != expected or len(spoken) != len(set(spoken)):
        raise ValueError('Dialogue must be assigned exactly once; preserve all source dialogue')
    result = project_draft(script, draft, asset_catalog)
    result['style_prompt'] = project_style
    return result
