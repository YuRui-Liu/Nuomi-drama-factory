"""Source-bound episode blueprints and deterministic canvas graph compilation."""

from __future__ import annotations

import re
import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from novelvideo.director_plan.models import NarrativeGroupPlan
from novelvideo.narrative_groups.storyboard_prompt import StaticPanel

from .graph import validate_graph
from .models import OrchestrationError, fingerprint


class Asset(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]+$")
    name: str
    kind: Literal["character", "scene", "prop"]
    prompt: str = Field(min_length=1)
    image_url: str | None = None
    version_id: str | None = None
    asset_sha256: str | None = None


class Shot(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str | None = None
    description: str = Field(min_length=1)
    duration_seconds: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    static_panel: StaticPanel | None = None
    static_source_description: str | None = None


class Group(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]+$")
    scene_id: str
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    duration: float = Field(gt=0, allow_inf_nan=False)
    prompt: str = Field(min_length=1)
    asset_ids: list[str]
    shots: list[Shot] = Field(default_factory=list)
    director_plan: NarrativeGroupPlan | None = None


def storyboard_layout(count: int) -> tuple[int, int]:
    """Pack without inventing filler shots; layout never chooses coverage."""
    if count < 1 or count > 81:
        raise OrchestrationError('storyboard_capacity', 'Storyboard requires 1–81 panels')
    pairs = [(rows, count // rows) for rows in range(1, 10)
             if count % rows == 0 and count // rows <= 9]
    if not pairs:
        raise OrchestrationError('storyboard_capacity', 'Shot count requires multiple storyboard batches')
    return min(pairs, key=lambda pair: (abs(pair[0] - pair[1]), pair[0]))


class Blueprint(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str
    style_prompt: str = ''
    assets: list[Asset] = Field(default_factory=list)
    groups: list[Group] = Field(min_length=1)


def source_scenes(script: str) -> list[dict]:
    lines = script.splitlines()
    if not re.search(r"【本集完】|本集结束|THE END", script, re.I):
        raise OrchestrationError("incomplete_source", "请提供完整剧本，包含明确的本集结束标记；不能用截断原文制作整集")
    headers = [index for index, line in enumerate(lines, 1)
               if re.match(r"^\s*(?:#{1,6}\s*)?(?:第[一二三四五六七八九十百\d]+场|场次\s*\d+|场\s*\d+|\d+[.-]\d+\s|INT\.|EXT\.)", line)]
    if not headers:
        raise OrchestrationError("scene_headers_required", "剧本需要明确场次标题")
    return [{"id": f"scene-{i+1}", "start_line": start,
             "end_line": headers[i+1] - 1 if i+1 < len(headers) else len(lines), "heading": lines[start-1]}
            for i, start in enumerate(headers)]


def compile_plan(script: str, blueprint: dict, *, duration: float, aspect_ratio: str, image_route: dict | None = None) -> dict:
    scenes = source_scenes(script)
    try:
        parsed = Blueprint.model_validate(blueprint)
    except ValidationError as exc:
        raise OrchestrationError("invalid_blueprint", "Generated blueprint failed validation",
                                 errors=exc.errors(include_input=False, include_url=False)) from exc
    lines = script.splitlines()
    covered = set()
    assets = {asset.id: asset for asset in parsed.assets}
    if len(assets) != len(parsed.assets) or len({g.id for g in parsed.groups}) != len(parsed.groups):
        raise OrchestrationError("duplicate_id", "Blueprint IDs must be unique")
    groups = sorted(parsed.groups, key=lambda g: g.start_line)
    coverage = []
    for group in groups:
        if group.shots:
            storyboard_layout(len(group.shots))
            durations = [s.duration_seconds for s in group.shots]
            if any(d is not None for d in durations) and (
                any(d is None for d in durations)
                or not math.isclose(sum(d or 0 for d in durations), group.duration, abs_tol=0.01)
            ):
                raise OrchestrationError('shot_duration_mismatch', 'All shot durations must sum to the group duration')
            ids = [s.id for s in group.shots if s.id is not None]
            if len(ids) != len(set(ids)):
                raise OrchestrationError('duplicate_id', 'Shot IDs must be unique within a group')
        span = set(range(group.start_line, group.end_line+1))
        if span & covered:
            raise OrchestrationError("coverage_overlap", "Narrative groups overlap source lines")
        scene = next((s for s in scenes if s["id"] == group.scene_id), None)
        if not span or not scene or group.start_line < scene["start_line"] or group.end_line > scene["end_line"]:
            raise OrchestrationError("source_range", "Group source range does not belong to its scene")
        if set(group.asset_ids) - set(assets):
            raise OrchestrationError("unknown_asset", "Group references an unknown asset")
        covered.update(span)
        coverage.append({"scene_id": group.scene_id, "group_id": group.id,
                         "start_line": group.start_line, "end_line": group.end_line})
    required = {i for i in range(scenes[0]["start_line"], len(lines)+1) if lines[i-1].strip()}
    if required - covered:
        raise OrchestrationError("coverage_gap", "Plan omits source lines", lines=sorted(required - covered))
    total = sum(group.duration for group in groups)
    if abs(total-duration) > 0.1:
        raise OrchestrationError("duration_mismatch", "Planned duration differs from requested episode duration", duration=total, expected=duration)
    for scene in scenes:
        timing = re.search(r"[（(]\s*(\d+(?:\.\d+)?)\s*秒\s*[）)]", scene["heading"])
        if timing:
            expected = float(timing.group(1))
            actual = sum(g.duration for g in groups if g.scene_id == scene["id"])
            if abs(actual - expected) > 0.1:
                raise OrchestrationError("scene_duration_mismatch", "Preserve the script's explicit scene durations",
                                         scene_id=scene["id"], expected=expected, actual=actual)

    def node(key, kind, data, x, y):
        return {"id": key, "type": kind, "position": {"x": x, "y": y}, "data": data}
    nodes = [node("source", "textAnnotationNode", {"content": script, "displayName": "完整剧本", "referenceOnly": True}, 0, 0),
             node("script", "scriptNode", {"sourceText": script, "scriptResult": parsed.model_dump(),
                  "scriptTitle": parsed.title, "referenceOnly": True, "displayName": "已校验制作计划"}, 400, 0)]
    edges = []
    def connect(source, target, media):
        metadata = {"sourcePort": media, "targetPort": media}
        if target.startswith("video-") and media == "image":
            metadata.update(edgeKind="videoDirectorImage", slot={"kind": "reference"})
        edges.append({"id": f"{source}--{target}", "source": source, "target": target,
                      "sourceHandle": "source", "targetHandle": "target",
                      "data": metadata})
    connect("source", "script", "text")
    for i, asset in enumerate(parsed.assets):
        nodes.append(node("asset-"+asset.id, "imageGenNode", {"prompt": '\n'.join(filter(None, [parsed.style_prompt, asset.prompt])), "displayName": asset.name,
                          "aspectRatio": aspect_ratio, "requestAspectRatio": aspect_ratio,
                          "size": "1K", "count": 1, "assetKind": asset.kind}, 850, i*440))
        if asset.image_url:
            nodes[-1]['data'].update(referenceOnly=True, imageUrl=asset.image_url,
                                    assetVersionId=asset.version_id, assetSha256=asset.asset_sha256)
        # Asset-specific description is deliberate: the entire script need not
        # contaminate reference-sheet composition.
    timeline = []
    for ordinal, group in enumerate(groups):
        excerpt = "\n".join(lines[group.start_line-1:group.end_line])
        board, video = "board-"+group.id, "video-"+group.id
        prompt = f"{parsed.style_prompt}\n{group.prompt}\n\n严格实现以下原文，保留动作、说话人和每句对白，不添加人物：\n{excerpt}".strip()
        provenance = {"scene_id": group.scene_id, "group_id": group.id, "ordinal": ordinal,
                      "source_start_line": group.start_line, "source_end_line": group.end_line}
        rows, cols = storyboard_layout(len(group.shots)) if group.shots else (2, 2)
        nodes.append(node(board, "storyboardGenNode", {**provenance, "prompt": prompt,
                          "storyboardMode": "narrative", "gridRows": rows, "gridCols": cols,
                          "frames": [shot.model_dump(exclude_none=True) for shot in group.shots],
                          "ratioControlMode": "overall",
                          "aspectRatio": aspect_ratio, "requestAspectRatio": aspect_ratio, "size": "1K",
                          "referenceOrder": ["asset-"+a for a in group.asset_ids],
                          "staticStylePrompt": parsed.style_prompt,
                          "referenceBindings": [
                              {"node_id": "asset-"+a, "entity_id": assets[a].name,
                               "name": assets[a].name, "role": "base_design" if assets[a].kind == "character" else assets[a].kind,
                               "panel_ids": [s.static_panel.id for s in group.shots if s.static_panel is not None
                                   and (assets[a].kind != "character" or any(
                                       subject.entity_id == assets[a].name for subject in s.static_panel.subjects))]}
                              for a in group.asset_ids if any(s.static_panel is not None
                                  and (assets[a].kind != "character" or any(
                                      subject.entity_id == assets[a].name for subject in s.static_panel.subjects)) for s in group.shots)],
                          "displayName": f"叙事组 {ordinal+1} 分镜"}, 1400, ordinal*500))
        if group.director_plan is not None:
            nodes[-1]['data']['directorPlan'] = group.director_plan.model_dump(mode='json')
        for asset_id in group.asset_ids:
            connect("asset-"+asset_id, board, "image")
        # Full group storyboard is the primary reference. Character references
        # remain linked to preserve identity across narrative groups.
        character_ids = [a for a in group.asset_ids if assets[a].kind == "character"]
        draft = {"schemaVersion": 1, "revision": 0, "modelId": "minimax-h3", "aspectRatio": aspect_ratio, "resolution": "720p",
                 "references": [], "segments": [{"id": group.id, "prompt": prompt+"\n对白使用普通话原声，只说原文对白，不加旁白。",
                 "durationSeconds": group.duration, "firstFrame": None, "lastFrame": None, "technique": None}]}
        nodes.append(node(video, "videoDirectorNode", {**provenance, "draft": draft, "displayName": f"叙事组 {ordinal+1} 视频",
                          "referenceOrder": [board]+["asset-"+a for a in character_ids]}, 1950, ordinal*500))
        if group.shots:
            if any(not shot.description.strip() for shot in group.shots):
                raise OrchestrationError('missing_shot_description', 'Every shot requires a nonempty description')
            # Planned panels are selected moments, not declared endpoint pairs.
            nodes[-1]['data'].update(autoStoryboard={'sourceNodeId': board}, activeInputMode='ref')
        connect(board, video, "image")
        for asset_id in character_ids:
            connect("asset-"+asset_id, video, "image")
        timeline.append({"node_id": video, "duration": group.duration, "ordinal": ordinal})
    nodes.append(node("final", "videoComposeNode", {"displayName": parsed.title+" · 成片", "timeline": timeline,
                      "resolution": "1080p", "keepOriginalAudio": True}, 2500, 0))
    for item in timeline:
        connect(item["node_id"], "final", "video")
    if image_route:
        for item in nodes:
            if item["type"] in {"imageGenNode", "storyboardGenNode"}:
                item["data"].update(model=image_route["model"], request=dict(image_route))
                if image_route.get('image_size'):
                    item['data']['size'] = image_route['image_size']
    report = validate_graph(nodes, edges)
    if not report["valid"]:
        raise OrchestrationError("invalid_graph", "Compiled graph is invalid", errors=report["errors"])
    return {"schema_version": 1, "source_hash": fingerprint(script), "duration": total,
            "aspect_ratio": aspect_ratio, "title": parsed.title, "coverage": coverage,
            "target": "final", "nodes": nodes, "edges": edges}


async def generate_blueprint(script: str, *, duration: float, aspect_ratio: str,
                             director_brief: str = '', asset_catalog=(), project_style='', image_model=None, image_size='1K'):
    from novelvideo.text_task_runtime import current_text_task_runtime
    from novelvideo.api.deps import get_media_capability_store, get_media_credential_resolver
    from novelvideo.media_capabilities.image.catalog import list_image_models
    runtime = current_text_task_runtime()
    if runtime is None:
        raise OrchestrationError("runtime_required", "Canvas planning requires a frozen text runtime")
    models = list_image_models(get_media_capability_store(), get_media_credential_resolver())
    if not models:
        raise OrchestrationError("image_configuration_required", "请先在模型设置中配置可用图片模型")
    selected = next((model for model in models if model.id == image_model), None) if image_model else models[0]
    if selected is None:
        raise OrchestrationError('image_configuration_required', f'Configured image model is unavailable: {image_model}')
    image_route = {"provider": selected.provider, "model": selected.id, "image_size": image_size}
    from .director_bridge import direct_canvas
    source_scenes(script)
    feedback = ''
    for attempt in range(3):
        try:
            blueprint = Blueprint.model_validate(await direct_canvas(script, duration=duration,
                aspect_ratio=aspect_ratio, director_brief=director_brief + feedback,
                asset_catalog=asset_catalog, project_style=project_style))
            if any(group.director_plan is None for group in blueprint.groups):
                raise OrchestrationError('missing_director_contract', 'Every new group requires the shared director contract')
            compiled = compile_plan(script, blueprint.model_dump(), duration=duration, aspect_ratio=aspect_ratio, image_route=image_route)
            return {"blueprint": blueprint.model_dump(), "plan": compiled, "image_route": image_route,
                    "director_brief": director_brief,
                    "runtime": runtime.snapshot.model_dump(mode="json")}
        except (OrchestrationError, ValueError) as exc:
            if str(exc).startswith(('base_design_required:', 'existing_prop_required:')):
                raise
            if attempt == 2:
                raise
            feedback = f'\n规划校验反馈：{exc}。保留用户导演要求，修复冲突后返回完整方案。'
