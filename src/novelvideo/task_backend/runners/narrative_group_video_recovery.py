"""Verify existing paid media before recovering a historical visual-QC failure."""
from __future__ import annotations

import json
import math
import subprocess
from pathlib import Path

from novelvideo.media_capabilities.video.adapters import NarrativeGroupVideoResult
from novelvideo.media_capabilities.video.h3_size_settings import resolve_h3_size_setting
from novelvideo.media_capabilities.video.quality import resolution_matches


def probe_video(path: Path) -> dict:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-protocol_whitelist", "file,pipe", "-select_streams", "v:0",
         "-show_entries", "stream=width,height,duration:format=duration", "-of", "json", str(path)],
        capture_output=True, check=True, timeout=20,
    )
    data = json.loads(result.stdout)
    stream = data["streams"][0]
    return {"width": int(stream["width"]), "height": int(stream["height"]),
            "duration": float(stream.get("duration") or data["format"]["duration"])}


def recover_reviewed_clip(entry, *, segment, project_dir: Path, resolution: str,
                          aspect_ratio: str, mode: str) -> NarrativeGroupVideoResult | None:
    """Caller must first validate the manifest input snapshot and workflow settings."""
    if (not entry.physical_video or entry.status not in {"completed", "quality_mismatch"}
            or entry.segment.prompt != segment.prompt):
        return None
    try:
        path = Path(entry.physical_video).resolve(strict=True)
        if not path.is_relative_to(project_dir.resolve()) or not path.is_file():
            return None
        measured = probe_video(path)
        size = resolve_h3_size_setting(resolution, aspect_ratio)
        duration = float(measured["duration"])
        if (not math.isfinite(duration) or duration <= 0
                or abs(duration - segment.duration_seconds) > max(0.5, segment.duration_seconds * 0.1)
                or not resolution_matches((size.width, size.height),
                                          (measured["width"], measured["height"]), tolerance_px=32)):
            return None
    except (OSError, ValueError, KeyError, IndexError, TypeError, subprocess.SubprocessError):
        return None
    return NarrativeGroupVideoResult(
        output_path=str(path), provider_task_id=entry.provider_task_id, actual_mode=mode,
        provider_parameters={"width": size.width, "height": size.height},
        actual_output={"width": measured["width"], "height": measured["height"]},
    )
