"""Production task runners for narrative-group grid generation and splitting."""

from __future__ import annotations

import asyncio
from math import gcd
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from novelvideo.narrative_groups.service import (
    record_stage_result,
    retry_split,
    run_group_grid,
    stage_payload,
)
from novelvideo.narrative_groups.references import (
    GroupImageReference,
    apply_group_reference_selection,
    resolve_group_reference_preview,
)
from novelvideo.project_context import ProjectContext
from novelvideo.task_backend.registry import register_project_task_runner


def _project_dir(payload: Mapping[str, Any], ctx: ProjectContext) -> Path:
    return Path(str(payload.get("project_dir") or ctx.output_dir))


def _beat_number(beat: Mapping[str, Any], fallback: int) -> int:
    for field in ("beat_number", "panel_index", "id", "beat_id"):
        value = beat.get(field)
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return fallback


@dataclass(frozen=True)
class GroupGenerationInput:
    prompt: str
    references: tuple[str, ...]
    warnings: tuple[str, ...] = ()


def _reference_mapping(
    references: tuple[GroupImageReference, ...], *, start: int = 1
) -> str:
    lines = []
    for index, ref in enumerate(references, start=start):
        beats = ", ".join(str(number) for number in ref.beat_numbers)
        if ref.kind == "character":
            subject = f"character {ref.character_name}, identity {ref.identity_id}"
        else:
            subject = f"scene {ref.scene_id}"
        lines.append(f"Reference {index}: {subject}; use for panels {beats}.")
    return "\n".join(lines)


def _grid_prompt(
    payload: Mapping[str, Any],
    *,
    style_prompt: str = "",
    selected_references: tuple[GroupImageReference, ...] = (),
) -> str:
    layout = payload.get("layout") or {}
    beats = list(payload.get("beats") or [])
    stage = str(payload.get("stage") or "render")
    visual = "black-and-white storyboard sketch" if stage == "sketch" else "final image"
    panels = []
    for index, beat in enumerate(beats, start=1):
        description = (
            beat.get("visual_description")
            or beat.get("shot_description")
            or beat.get("action")
            or beat.get("content")
            or beat.get("description")
            or beat.get("title")
            or "continue the scene"
        )
        panels.append(f"Panel {index}: {description}")
    grid_rules = (
        f"Create one clean {layout.get('rows', 1)}x{layout.get('columns', 1)} storyboard grid. "
        f"Every individual cell must be composed at {payload.get('aspect_ratio') or '9:16'} aspect ratio. "
        f"Each cell is a separate {visual}; preserve character, location, lighting and time continuity. "
        "Use equal cells in reading order, no borders, captions, labels, text, collage overlap, or extra panels.\n"
    )
    strong_lock = str(payload.get("constraint_mode") or "") == "strong_sketch"
    if strong_lock:
        grid_rules += (
            "图片1是本组已确认的草图多宫格，作为强构图约束。"
            "必须逐格保持图片1的分镜数量、阅读顺序、人物位置、动作轮廓、画面布局、"
            "景别、机位、透视和主体占比；不得改变分镜数量、画面布局、景别、机位。"
            "只允许把草图细化为最终成片画面，并结合后续身份图、场景图和风格信息完善材质、"
            "服装、表情、灯光与细节。\n"
        )
    parts = [part for part in (style_prompt, grid_rules, "\n".join(panels)) if part]
    mapping = _reference_mapping(selected_references, start=2 if strong_lock else 1)
    if mapping:
        parts.append(mapping)
    return "\n".join(parts)


def _generation_input(payload: Mapping[str, Any]) -> GroupGenerationInput:
    preview = resolve_group_reference_preview(
        Path(str(payload["project_dir"])),
        list(payload.get("beats") or []),
        stage=str(payload.get("stage") or "render"),
    )
    options = payload.get("reference_selection") or {}
    selection = apply_group_reference_selection(
        preview,
        use_style=bool(options.get("use_style", True)),
        selected_character_reference_ids=options.get("selected_character_reference_ids"),
        selected_scene_reference_ids=options.get("selected_scene_reference_ids"),
    )
    references = selection.image_paths
    prompt_references = selection.selected
    warnings = list(selection.warnings)
    if str(payload.get("constraint_mode") or "") == "strong_sketch":
        from novelvideo.narrative_groups.service import load_groups

        group = next(
            (
                item for item in load_groups(Path(str(payload["project_dir"])), int(payload["episode"]))
                if item.id == str(payload["group_id"])
            ),
            None,
        )
        sketch = group.stages["sketch"] if group is not None else None
        frozen_revision = int(payload.get("source_sketch_revision") or 0)
        frozen_asset = Path(str(payload.get("source_sketch_asset") or ""))
        if (
            sketch is None
            or sketch.status != "completed"
            or sketch.revision != frozen_revision
            or not sketch.grid_asset
            or Path(sketch.grid_asset).resolve() != frozen_asset.resolve()
            or not frozen_asset.is_file()
        ):
            raise RuntimeError("narrative group sketch revision is stale")
        if len(references) > 8:
            warnings.append("强构图模式为草图保留首个参考位，仅使用前 8 张其他参考图")
        references = (str(frozen_asset), *references[:8])
        prompt_references = selection.selected[:8]
    return GroupGenerationInput(
        prompt=_grid_prompt(
            payload,
            style_prompt=selection.style_prompt,
            selected_references=prompt_references,
        ),
        references=tuple(references),
        warnings=tuple(warnings),
    )


def _grid_request_aspect_ratio(payload: Mapping[str, Any]) -> str:
    layout = payload.get("layout") or {}
    rows = max(1, int(layout.get("rows") or 1))
    columns = max(1, int(layout.get("columns") or 1))
    value = str(payload.get("aspect_ratio") or "9:16")
    if value not in {"9:16", "16:9"}:
        raise ValueError(f"unsupported narrative-group aspect ratio: {value}")
    width, height = (int(part) for part in value.split(":"))
    grid_width, grid_height = width * columns, height * rows
    divisor = gcd(grid_width, grid_height)
    return f"{grid_width // divisor}:{grid_height // divisor}"


def _provider_grid_aspect_ratio(payload: Mapping[str, Any], model: str) -> str:
    """Choose the closest real canvas supported by the selected image model."""
    requested = _grid_request_aspect_ratio(payload)
    if not model.startswith("gpt-image"):
        return requested
    left, right = (int(part) for part in requested.split(":"))
    desired = left / right
    supported = (("2:3", 2 / 3), ("1:1", 1.0), ("3:2", 3 / 2))
    return min(supported, key=lambda item: abs(item[1] - desired))[0]


def _normalize_image_aspect(path: Path, aspect_ratio: str) -> None:
    from PIL import Image

    target_width, target_height = (int(part) for part in aspect_ratio.split(":"))
    with Image.open(path) as source:
        image = source.convert("RGB")
        width, height = image.size
        scale = min(width // target_width, height // target_height)
        if scale < 1:
            image = image.resize((target_width, target_height))
        else:
            crop_width = target_width * scale
            crop_height = target_height * scale
            left = (width - crop_width) // 2
            top = (height - crop_height) // 2
            image = image.crop(
                (left, top, left + crop_width, top + crop_height)
            )
        image.save(path, format="PNG")


async def _generate_grid(payload: Mapping[str, Any], ctx: ProjectContext) -> dict[str, Any]:
    """Generate exactly one group grid through the configured GRSAI account."""
    from novelvideo.api.deps import get_media_capability_store, get_media_credential_resolver
    from novelvideo.media_capabilities.models import ImageGenerationRequest, MediaCapability
    from novelvideo.media_capabilities.runtime.configuration import load_grsai_runtime_configuration

    provider_id = str(payload.get("provider_id") or "grsai-main")
    runtime = load_grsai_runtime_configuration(
        get_media_capability_store(), get_media_credential_resolver(),
        provider_id=provider_id,
    )
    generation_input = _generation_input(payload)
    model = str(payload.get("model") or runtime.model)
    request = ImageGenerationRequest(
        capability=MediaCapability.IMAGE_STORYBOARD_GRID,
        prompt=generation_input.prompt,
        model=model,
        references=list(generation_input.references),
        aspect_ratio=_provider_grid_aspect_ratio(payload, model),
        image_size="2K",
    )
    client = runtime.create_client()
    try:
        task_id = await client.submit(request, api_key=runtime.api_key)
        poll_interval = max(0.05, float(os.environ.get("GRSAI_POLL_INTERVAL_SECONDS", "2")))
        max_polls = max(1, int(os.environ.get("GRSAI_MAX_POLLS", "300")))
        snapshot = None
        for _ in range(max_polls):
            snapshot = await client.query(task_id, api_key=runtime.api_key)
            if snapshot.status == "succeeded":
                break
            if snapshot.status in {"failed", "violation"}:
                raise RuntimeError(f"GRSAI grid generation failed: {snapshot.status}")
            await asyncio.sleep(poll_interval)
        if snapshot is None or snapshot.status != "succeeded" or not snapshot.results:
            raise TimeoutError(f"GRSAI grid generation timed out: {task_id}")
        url = next(
            (str(snapshot.results[0].get(key) or "") for key in ("url", "fileUrl", "downloadUrl") if snapshot.results[0].get(key)),
            "",
        )
        if not url:
            raise RuntimeError("GRSAI grid response has no result URL")
        image_bytes = await client.download(url) if hasattr(client, "download") else (await client.http.get(url)).content
        output_dir = Path(str(payload.get("output_dir") or ctx.output_dir))
        target = output_dir / "grids" / f"ep{int(payload['episode']):03d}" / "narrative_groups" / (
            f"{payload['group_id']}_{payload['stage']}_r{int(payload['revision'])}_{os.getpid()}_{datetime.now().strftime('%Y%m%d%H%M%S%f')}.png"
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(image_bytes)
        return {
            "grid_asset": str(target),
            "actual_provider": provider_id,
            "actual_model": request.model,
            "actual_mode": str(payload.get("stage") or "render"),
            "source_sketch_revision": int(payload.get("source_sketch_revision") or 0),
            "constraint_mode": str(payload.get("constraint_mode") or ""),
            "provider_task_id": task_id,
            "reference_count": len(generation_input.references),
            "reference_warnings": list(generation_input.warnings),
        }
    finally:
        await client.http.aclose()


def _split_existing_grid(
    grid_asset: str, payload: Mapping[str, Any], ctx: ProjectContext
) -> dict[str, Any]:
    from novelvideo.generators.pool_indexer import save_grid_and_split
    from novelvideo.utils.path_resolver import PathResolver

    layout = payload.get("layout") or {}
    beats = list(payload.get("beats") or [])
    mapping = list(payload.get("cell_to_beat") or [])
    beat_nums = [
        _beat_number(beats[index], index + 1) if index < len(beats) else _beat_number(item, index + 1)
        for index, item in enumerate(mapping)
    ]
    output_dir = Path(str(payload.get("output_dir") or ctx.output_dir))
    episode = int(payload["episode"])
    stage = str(payload["stage"])
    paths = PathResolver(str(output_dir), episode)
    promote_dir = paths.sketches_dir() if stage == "sketch" else paths.frames_dir()
    promote_dir.mkdir(parents=True, exist_ok=True)
    grid_aspect_ratio = _grid_request_aspect_ratio(payload)
    result = save_grid_and_split(
        grid_image_path=grid_asset,
        episode_grids_dir=output_dir / "grids" / f"ep{episode:03d}",
        grid_type=stage,
        mode_key=(
            f"{int(layout['rows'])}x{int(layout['columns'])}_"
            f"{grid_aspect_ratio.replace(':', '-')}"
        ),
        beat_nums=beat_nums,
        preset="custom",
        rows=int(layout["rows"]),
        cols=int(layout["columns"]),
        ts=f"ng{payload['group_id']}_r{int(payload['revision'])}_{datetime.now().strftime('%Y%m%d%H%M%S%f')}",
        promote_dir=promote_dir,
        force_promote=True,
        beats=beats if stage == "sketch" else None,
    )
    cell_paths = list(result.get("cell_paths") or [])
    target_aspect_ratio = str(payload.get("aspect_ratio") or "9:16")
    import shutil

    for index, path in enumerate(cell_paths):
        cell_path = Path(path)
        _normalize_image_aspect(cell_path, target_aspect_ratio)
        if index < len(beat_nums):
            shutil.copy2(cell_path, promote_dir / f"beat_{beat_nums[index]:02d}.png")
    return {
        "cell_assets": [
            {"cell": index, "beat_id": mapping[index]["beat_id"], "path": str(path)}
            for index, path in enumerate(cell_paths)
            if index < len(mapping)
        ],
        "errors": [],
    }


async def _execute(envelope: dict[str, Any], ctx: ProjectContext, *, split_only: bool) -> dict[str, Any]:
    payload = dict(envelope.get("payload") or {})
    episode = int(envelope.get("episode") or payload.get("episode") or 0)
    payload["episode"] = episode
    project_dir = _project_dir(payload, ctx)
    group_id = str(payload["group_id"])
    stage = str(payload["stage"])
    saved = stage_payload(project_dir, episode, group_id, stage)
    for field in ("layout", "beat_ids", "cell_to_beat"):
        payload.setdefault(field, saved[field])
    expected_revision = int(payload["revision"])
    record_stage_result(
        project_dir,
        episode,
        group_id,
        stage,
        expected_revision=expected_revision,
        status="running",
        error="",
    )
    try:
        if split_only:
            payload["grid_asset"] = saved["grid_asset"]
            result = await retry_split(
                payload, splitter=lambda grid, data: _split_existing_grid(grid, data, ctx)
            )
        else:
            generation_metadata: dict[str, Any] = {}

            async def generate(data: Mapping[str, Any]) -> dict[str, Any]:
                generated = await _generate_grid(data, ctx)
                for field in (
                    "reference_count", "reference_warnings",
                    "source_sketch_revision", "constraint_mode",
                ):
                    if field in generated:
                        generation_metadata[field] = generated[field]
                return generated

            result = await run_group_grid(
                payload,
                generator=generate,
                splitter=lambda grid, data: _split_existing_grid(grid, data, ctx),
            )
            result.update(generation_metadata)
        error = "; ".join(str(item.get("message") or item) for item in result.get("errors") or [])
        record_stage_result(
            project_dir,
            episode,
            group_id,
            stage,
            expected_revision=expected_revision,
            status=result["status"],
            grid_asset=result.get("grid_asset"),
            cell_assets=result.get("cell_assets") or [],
            error=error,
            actual_provider=result.get("actual_provider"),
            actual_model=result.get("actual_model"),
            actual_mode=result.get("actual_mode"),
            source_sketch_revision=result.get("source_sketch_revision"),
            constraint_mode=result.get("constraint_mode"),
        )
        return result
    except Exception as exc:
        record_stage_result(
            project_dir,
            episode,
            group_id,
            stage,
            expected_revision=expected_revision,
            status="failed",
            error=str(exc),
        )
        raise


def run_narrative_group_grid(envelope: dict[str, Any], ctx: ProjectContext) -> dict[str, Any]:
    return asyncio.run(_execute(envelope, ctx, split_only=False))


def run_narrative_group_split(envelope: dict[str, Any], ctx: ProjectContext) -> dict[str, Any]:
    return asyncio.run(_execute(envelope, ctx, split_only=True))


register_project_task_runner("narrative_group_grid", run_narrative_group_grid)
register_project_task_runner("narrative_group_split", run_narrative_group_split)


__all__ = ["run_narrative_group_grid", "run_narrative_group_split"]
