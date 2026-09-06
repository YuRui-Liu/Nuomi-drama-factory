"""Production task runners for narrative-group grid generation and splitting."""

from __future__ import annotations

import asyncio
from hashlib import sha256
from math import gcd
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from novelvideo.narrative_groups.service import (
    record_stage_result,
    retry_split,
    run_group_grid,
    stage_payload,
    load_materialized_groups,
)
from novelvideo.narrative_groups.references import (
    GroupImageReference,
    apply_group_reference_selection,
    resolve_group_reference_preview,
)
from novelvideo.narrative_groups.reference_uploads import (
    InvalidReferenceUpload,
    validate_reference_image,
)
from novelvideo.project_context import ProjectContext
from novelvideo.task_backend.registry import register_project_task_runner


def _project_dir(payload: Mapping[str, Any], ctx: ProjectContext) -> Path:
    return _contained_output_path(
        ctx, payload.get("project_dir") or ctx.output_dir
    )


def _contained_output_path(ctx: ProjectContext, value: Any) -> Path:
    root = Path(str(ctx.output_dir)).resolve()
    candidate = Path(str(value or root))
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve()
    if not resolved.is_relative_to(root):
        raise ValueError("path must stay inside the project output root")
    return resolved


def _safe_path_slug(value: Any, *, prefix: str) -> str:
    digest = sha256(str(value or "").encode("utf-8")).hexdigest()[:20]
    return f"{prefix}-{digest}"


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
    reference_audit: Mapping[str, Any] = field(default_factory=dict)


class ReferenceSnapshotInvalid(RuntimeError):
    """A stable, caller-safe failure for stale or tampered reference inputs."""

    error_code = "REFERENCE_SNAPSHOT_INVALID"

    def __init__(self) -> None:
        super().__init__(self.error_code)


def _snapshot_generation_input(
    payload: Mapping[str, Any], snapshot: Mapping[str, Any]
) -> GroupGenerationInput:
    """Revalidate a frozen decision snapshot without re-resolving user intent."""
    try:
        if snapshot.get("schema_version") != "narrative-reference-decision/v1":
            raise ValueError
        snapshot_id = str(snapshot["id"]).strip()
        images = snapshot["images"]
        ignored = snapshot["ignored_requirement_ids"]
        warnings = snapshot.get("warnings", ())
        if (
            not snapshot_id
            or not isinstance(images, (list, tuple))
            or not isinstance(ignored, (list, tuple))
            or not isinstance(warnings, (list, tuple))
        ):
            raise ValueError

        project_dir = Path(str(payload["project_dir"])).resolve(strict=False)
        assets_root = project_dir / "assets"
        uploads_root = project_dir / ".runtime" / "reference_uploads"
        references: list[str] = []
        counts = {"formal": 0, "temporary": 0, "fallback": 0}
        for raw in images:
            if not isinstance(raw, Mapping):
                raise ValueError
            resolution = str(raw.get("resolution") or "")
            if resolution in {"matched", "project_asset"}:
                counts["formal"] += 1
            elif resolution in {"temporary", "fallback"}:
                counts[resolution] += 1
            else:
                raise ValueError
            validated = validate_reference_image(
                str(raw["image_path"]), allowed_roots=(assets_root, uploads_root)
            )
            references.append(validated.image_path)

        style_reference = str(snapshot.get("style_reference") or "")
        if style_reference:
            validated_style = validate_reference_image(
                style_reference, allowed_roots=(assets_root,)
            )
            references.insert(0, validated_style.image_path)
        audit = {
            "snapshot_id": snapshot_id,
            **counts,
            "ignored": len(ignored),
        }
    except (KeyError, TypeError, ValueError, InvalidReferenceUpload):
        raise ReferenceSnapshotInvalid() from None

    return GroupGenerationInput(
        prompt=_grid_prompt(payload),
        references=tuple(references),
        warnings=tuple(str(item) for item in warnings),
        reference_audit=audit,
    )


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
        panel_tag = str(payload.get("panel_tag") or "").strip()
        suffix = f"; panel style: {panel_tag}" if panel_tag else ""
        panels.append(f"Panel {index}: {description}{suffix}")
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
    image_projection = str(payload.get("image_projection") or "").strip()
    parts = [
        part
        for part in (image_projection, style_prompt, grid_rules, "\n".join(panels))
        if part
    ]
    mapping = _reference_mapping(selected_references, start=2 if strong_lock else 1)
    if mapping:
        parts.append(mapping)
    return "\n".join(parts)


def _normalize_generation_batch_payload(
    raw_payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Accept current dict payloads and future GenerationBatch-shaped mappings."""
    payload = dict(raw_payload)
    batch = payload.get("generation_batch")
    if isinstance(batch, Mapping):
        for key, value in batch.items():
            payload.setdefault(str(key), value)
    layout_value = payload.get("layout")
    if isinstance(layout_value, str):
        layouts = {
            "single": (1, 1),
            "diptych": (1, 2),
            "triptych": (1, 3),
            "grid_2x2": (2, 2),
        }
        try:
            rows, columns = layouts[layout_value]
        except KeyError:
            raise ValueError(f"unsupported generation batch layout: {layout_value}") from None
        payload["layout_name"] = layout_value
        payload["layout"] = {
            "rows": rows,
            "columns": columns,
            "capacity": rows * columns,
        }
    shots = [str(value) for value in payload.get("shot_ids") or ()]
    if shots:
        layout = payload.get("layout") or {}
        capacity = int(layout.get("capacity") or 0)
        if capacity != len(shots):
            raise ValueError("generation batch capacity must equal shot count")
        payload["beat_ids"] = shots
        payload["cell_to_beat"] = [
            {"cell": index, "beat_id": shot_id}
            for index, shot_id in enumerate(shots)
        ]
    if payload.get("target_cell_aspect"):
        payload["aspect_ratio"] = str(payload["target_cell_aspect"])
    snapshot = payload.get("style_snapshot")
    if isinstance(snapshot, Mapping):
        projections = snapshot.get("projections")
        if isinstance(projections, Mapping):
            payload.setdefault("image_projection", projections.get("image"))
            payload.setdefault("panel_tag", projections.get("panel_tag"))
        payload.setdefault("style_hash", snapshot.get("style_hash"))
        payload.setdefault("style_snapshot_id", snapshot.get("snapshot_id"))
    return payload


def _generation_input(payload: Mapping[str, Any]) -> GroupGenerationInput:
    snapshot = payload.get("reference_resolution")
    if snapshot is not None and not isinstance(snapshot, Mapping):
        raise ReferenceSnapshotInvalid()
    if isinstance(snapshot, Mapping):
        generation_input = _snapshot_generation_input(payload, snapshot)
        if str(payload.get("constraint_mode") or "") != "strong_sketch":
            return generation_input
        # Strong-lock processing below is shared with legacy selections.
        references = generation_input.references
        prompt_references: tuple[GroupImageReference, ...] = ()
        warnings = list(generation_input.warnings)
        style_prompt = ""
        reference_audit = generation_input.reference_audit
    else:
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
        style_prompt = selection.style_prompt
        reference_audit = {}
    if str(payload.get("constraint_mode") or "") == "strong_sketch":
        group = next(
            (
                item
                for item in load_materialized_groups(
                    Path(str(payload["project_dir"])), int(payload["episode"])
                )
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
            style_prompt=style_prompt,
            selected_references=prompt_references,
        ),
        references=tuple(references),
        warnings=tuple(warnings),
        reference_audit=reference_audit,
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


_NON_GRAPHIC_REPLACEMENTS = (
    (re.compile(r"沾满血迹|沾着血迹|沾血|血迹"), "带有深色灰尘污渍"),
    (re.compile(r"鲜血|流血|血液"), "深色污渍"),
    (re.compile(r"血肉模糊|肢解|断肢|内脏"), "被深色阴影遮挡的区域"),
    (re.compile(r"尸体|尸骸"), "远处静止的模糊物体"),
    (re.compile(r"丧尸"), "门外若隐若现的模糊身影"),
    (re.compile(r"\b(?:blood|bloody|gore|gory|corpse)\b", re.IGNORECASE), "dark dust stain"),
)


def _non_graphic_retry_prompt(prompt: str) -> str:
    safe = prompt
    for pattern, replacement in _NON_GRAPHIC_REPLACEMENTS:
        safe = pattern.sub(replacement, safe)
    return (
        "PG-rated suspense illustration with indirect environmental tension. "
        "Use clean clothing, neutral dust, and deep shadows; keep every person intact and safely framed.\n"
        f"{safe}"
    )


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


def _rebuild_normalized_grid(
    grid_path: Path, cell_paths: list[str], *, rows: int, columns: int
) -> None:
    """Recompose normalized cells so the displayed grid matches its real geometry."""
    from PIL import Image

    if not cell_paths:
        return
    cells = []
    try:
        for path in cell_paths:
            with Image.open(path) as source:
                cells.append(source.convert("RGB"))
        cell_width, cell_height = cells[0].size
        canvas = Image.new("RGB", (cell_width * columns, cell_height * rows), "black")
        for index, cell in enumerate(cells[: rows * columns]):
            if cell.size != (cell_width, cell_height):
                cell = cell.resize((cell_width, cell_height))
            canvas.paste(cell, ((index % columns) * cell_width, (index // columns) * cell_height))
        canvas.save(grid_path, format="PNG")
    finally:
        for cell in cells:
            cell.close()


async def _generate_grid(payload: Mapping[str, Any], ctx: ProjectContext) -> dict[str, Any]:
    """Generate exactly one group grid through the configured GRSAI account."""
    from novelvideo.api.deps import get_media_capability_store, get_media_credential_resolver
    from novelvideo.media_capabilities.image.grsai import GrsaiPolicyViolation
    from novelvideo.media_capabilities.models import ImageGenerationRequest, MediaCapability
    from novelvideo.media_capabilities.runtime.configuration import load_grsai_runtime_configuration

    provider_id = str(payload.get("provider_id") or "grsai-main")
    runtime = load_grsai_runtime_configuration(
        get_media_capability_store(), get_media_credential_resolver(),
        provider_id=provider_id,
    )
    generation_input = _generation_input(payload)
    model = str(payload.get("model") or runtime.model)
    requested_tier = str(payload.get("image_size") or "1K")
    resolution = None
    if model.startswith("gpt-image"):
        from novelvideo.narrative_groups.image_resolution import (
            resolve_grid_image_resolution,
        )

        layout = payload.get("layout") or {}
        resolution = resolve_grid_image_resolution(
            model,
            requested_tier,
            str(payload.get("aspect_ratio") or "9:16"),
            int(layout.get("rows") or 1),
            int(layout.get("columns") or 1),
        )
    request = ImageGenerationRequest(
        capability=MediaCapability.IMAGE_STORYBOARD_GRID,
        prompt=generation_input.prompt,
        model=model,
        references=list(generation_input.references),
        aspect_ratio=(
            resolution.provider_aspect_ratio
            if resolution is not None
            else _provider_grid_aspect_ratio(payload, model)
        ),
        image_size=(resolution.provider_size if resolution is not None else requested_tier),
    )
    client = runtime.create_client()
    policy_retry = False
    try:
        async def submit_and_wait(
            candidate: ImageGenerationRequest,
        ) -> tuple[str, Any]:
            provider_task_id = await client.submit(
                candidate, api_key=runtime.api_key
            )
            poll_interval = max(
                0.05, float(os.environ.get("GRSAI_POLL_INTERVAL_SECONDS", "2"))
            )
            max_polls = max(1, int(os.environ.get("GRSAI_MAX_POLLS", "300")))
            provider_snapshot = None
            for _ in range(max_polls):
                provider_snapshot = await client.query(
                    provider_task_id, api_key=runtime.api_key
                )
                if provider_snapshot.status == "succeeded":
                    break
                if provider_snapshot.status == "violation":
                    raise GrsaiPolicyViolation(
                        "grsai.policy_violation status=violation"
                    )
                if provider_snapshot.status == "failed":
                    raise RuntimeError("GRSAI grid generation failed: failed")
                await asyncio.sleep(poll_interval)
            if (
                provider_snapshot is None
                or provider_snapshot.status != "succeeded"
                or not provider_snapshot.results
            ):
                raise TimeoutError(
                    f"GRSAI grid generation timed out: {provider_task_id}"
                )
            return provider_task_id, provider_snapshot

        try:
            task_id, snapshot = await submit_and_wait(request)
        except GrsaiPolicyViolation:
            policy_retry = True
            request = request.model_copy(
                update={"prompt": _non_graphic_retry_prompt(request.prompt)}
            )
            task_id, snapshot = await submit_and_wait(request)
        url = next(
            (str(snapshot.results[0].get(key) or "") for key in ("url", "fileUrl", "downloadUrl") if snapshot.results[0].get(key)),
            "",
        )
        if not url:
            raise RuntimeError("GRSAI grid response has no result URL")
        image_bytes = await client.download(url) if hasattr(client, "download") else (await client.http.get(url)).content
        output_dir = _contained_output_path(
            ctx, payload.get("output_dir") or ctx.output_dir
        )
        group_slug = _safe_path_slug(payload.get("group_id"), prefix="group")
        batch_slug = _safe_path_slug(payload.get("batch_id"), prefix="batch")
        target = output_dir / "grids" / f"ep{int(payload['episode']):03d}" / "narrative_groups" / (
            f"{group_slug}_{batch_slug}_{payload['stage']}_r{int(payload['revision'])}_"
            f"{os.getpid()}_{datetime.now().strftime('%Y%m%d%H%M%S%f')}.png"
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(image_bytes)
        actual_pixel_size = ""
        try:
            from io import BytesIO
            from PIL import Image

            with Image.open(BytesIO(image_bytes)) as generated_image:
                actual_pixel_size = (
                    f"{generated_image.width}x{generated_image.height}"
                )
        except Exception:
            actual_pixel_size = ""
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
            "reference_audit": dict(generation_input.reference_audit),
            "policy_retry": policy_retry,
            "requested_image_size": requested_tier,
            "requested_pixel_size": (
                resolution.provider_size if resolution is not None else request.image_size
            ),
            "actual_pixel_size": actual_pixel_size,
            "resolution_warning": (
                resolution.reason if resolution is not None else None
            ),
            "target_cell_size": (
                f"{resolution.target_cell_width}x{resolution.target_cell_height}"
                if resolution is not None
                else ""
            ),
            "cell_upscale_required": (
                resolution.requires_cell_upscale if resolution is not None else False
            ),
            "degraded": resolution.degraded if resolution is not None else False,
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
    output_dir = _contained_output_path(
        ctx, payload.get("output_dir") or ctx.output_dir
    )
    grid_path = Path(grid_asset).resolve()
    if not grid_path.is_relative_to(output_dir) or not grid_path.is_file():
        raise ValueError("grid asset must stay inside the project output root")
    episode = int(payload["episode"])
    stage = str(payload["stage"])
    paths = PathResolver(str(output_dir), episode)
    promote_dir = paths.sketches_dir() if stage == "sketch" else paths.frames_dir()
    promote_dir.mkdir(parents=True, exist_ok=True)
    grid_aspect_ratio = _grid_request_aspect_ratio(payload)
    rows = int(layout["rows"])
    columns = int(layout["columns"])
    layout_name = {
        (1, 1): "single",
        (1, 2): "diptych",
        (1, 3): "triptych",
        (2, 2): "grid_2x2",
    }.get((rows, columns))
    if layout_name is not None:
        from novelvideo.narrative_groups.grid_cleanup import split_and_cleanup

        group_slug = _safe_path_slug(payload.get("group_id"), prefix="group")
        batch_slug = _safe_path_slug(payload.get("batch_id"), prefix="batch")
        split_dir = promote_dir / f".{group_slug}_{batch_slug}_r{int(payload['revision'])}"
        target_cell_size = None
        model = str(payload.get("model") or "")
        quality = str(payload.get("image_size") or "1K")
        if model.startswith("gpt-image"):
            from novelvideo.narrative_groups.image_resolution import (
                resolve_grid_image_resolution,
            )

            resolution = resolve_grid_image_resolution(
                model,
                quality,
                str(payload.get("aspect_ratio") or "9:16"),
                rows,
                columns,
            )
            target_cell_size = (
                resolution.target_cell_width,
                resolution.target_cell_height,
            )
        raw_paths, cleanup_reports = split_and_cleanup(
            grid_path,
            expected_layout=layout_name,
            target_aspect=str(payload.get("aspect_ratio") or "9:16"),
            output_dir=split_dir,
            target_cell_size=target_cell_size,
        )
        cell_paths: list[str] = []
        import shutil

        for index, raw_path in enumerate(raw_paths):
            target = promote_dir / f"beat_{beat_nums[index]:02d}.png"
            shutil.copy2(raw_path, target)
            cell_paths.append(str(target))
        cleaned_cell_size = (
            "x".join(str(value) for value in cleanup_reports[0]["output_size"])
            if cleanup_reports
            else ""
        )
        _rebuild_normalized_grid(grid_path, cell_paths, rows=rows, columns=columns)
        return {
            "cell_assets": [
                {
                    "cell": index,
                    "beat_id": mapping[index]["beat_id"],
                    "shot_id": mapping[index]["beat_id"],
                    "path": path,
                    "style_hash": str(payload.get("style_hash") or ""),
                }
                for index, path in enumerate(cell_paths)
                if index < len(mapping)
            ],
            "cleanup_reports": cleanup_reports,
            "cleaned_cell_size": cleaned_cell_size,
            "upscaled": any(
                bool(report.get("upscaled")) for report in cleanup_reports
            ),
            "degraded": any(
                bool(report.get("degraded")) for report in cleanup_reports
            ),
            "errors": [],
        }
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
        ts=(
            f"{_safe_path_slug(payload.get('group_id'), prefix='group')}_"
            f"r{int(payload['revision'])}_{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
        ),
        promote_dir=promote_dir,
        force_promote=True,
        beats=beats if stage == "sketch" else None,
    )
    cell_paths = list(result.get("cell_paths") or [])
    target_aspect_ratio = str(payload.get("aspect_ratio") or "9:16")
    from novelvideo.narrative_groups.grid_cleanup import cleanup_grid_cells

    cleanup_reports, cleaned_cell_size = cleanup_grid_cells(
        cell_paths, target_aspect_ratio
    )
    import shutil

    for index, path in enumerate(cell_paths):
        cell_path = Path(path)
        if index < len(beat_nums):
            shutil.copy2(cell_path, promote_dir / f"beat_{beat_nums[index]:02d}.png")
    rebuilt_paths = {
        Path(grid_asset),
        Path(str(result.get("grid_path") or grid_asset)),
    }
    for rebuilt_path in rebuilt_paths:
        _rebuild_normalized_grid(
            rebuilt_path,
            cell_paths,
            rows=int(layout["rows"]),
            columns=int(layout["columns"]),
        )
    return {
        "cell_assets": [
            {"cell": index, "beat_id": mapping[index]["beat_id"], "path": str(path)}
            for index, path in enumerate(cell_paths)
            if index < len(mapping)
        ],
        "cleanup_reports": cleanup_reports,
        "cleaned_cell_size": cleaned_cell_size,
        "errors": [],
    }


async def _execute(envelope: dict[str, Any], ctx: ProjectContext, *, split_only: bool) -> dict[str, Any]:
    payload = _normalize_generation_batch_payload(envelope.get("payload") or {})
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
                    "reference_count", "reference_warnings", "reference_audit",
                    "source_sketch_revision", "constraint_mode",
                    "requested_image_size", "requested_pixel_size",
                    "actual_pixel_size", "resolution_warning", "cleaned_cell_size",
                    "target_cell_size", "cell_upscale_required", "degraded",
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
            requested_image_size=result.get("requested_image_size"),
            requested_pixel_size=result.get("requested_pixel_size"),
            actual_pixel_size=result.get("actual_pixel_size"),
            resolution_warning=result.get("resolution_warning"),
            source_sketch_revision=result.get("source_sketch_revision"),
            constraint_mode=result.get("constraint_mode"),
            cleanup_reports=result.get("cleanup_reports"),
            provider_parameters={
                "batch_id": str(payload.get("batch_id") or ""),
                "style_snapshot_id": str(payload.get("style_snapshot_id") or ""),
                "style_hash": str(payload.get("style_hash") or ""),
                "requested_image_size": str(
                    result.get("requested_image_size") or payload.get("image_size") or ""
                ),
                "actual_cell_size": str(result.get("cleaned_cell_size") or ""),
                "target_cell_size": str(result.get("target_cell_size") or ""),
                "upscaled": bool(result.get("upscaled")),
                "degraded": bool(result.get("degraded")),
                "reference_audit": dict(result.get("reference_audit") or {}),
            },
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
