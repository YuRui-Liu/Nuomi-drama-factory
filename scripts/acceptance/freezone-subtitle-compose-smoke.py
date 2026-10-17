"""Local-only FFmpeg smoke check for the subtitle export path.

Run with `uv run python scripts/acceptance/freezone-subtitle-compose-smoke.py`.
The script generates its own five-second source video in a temporary directory,
packs three 3x clips at fractional-second boundaries, and does not contact
providers or use project media.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from novelvideo.freezone.export_manifest import build_export_manifest, sha256_file  # noqa: E402
from novelvideo.freezone.jobs import run_freezone_video_compose  # noqa: E402


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise RuntimeError(f"command failed: {' '.join(command)}\n{completed.stderr[-2000:]}")
    return completed


async def main() -> None:
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise SystemExit("FFmpeg smoke requires both ffmpeg and ffprobe on PATH")

    with tempfile.TemporaryDirectory(prefix="nuomi_compose_subtitle_smoke_") as temp_dir:
        project_dir = Path(temp_dir)
        source_path = project_dir / "source.mp4"
        _run(
            [
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "color=c=0x123456:s=320x180:d=5:r=30",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:duration=5:sample_rate=48000",
                "-t",
                "5",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-shortest",
                str(source_path),
            ]
        )

        # Match the editor's 5000ms-at-3x pack: these boundaries must stay precise
        # through JSON/API conversion so the backend does not see sub-ms overlaps.
        items = [
            {
                "item_id": f"smoke_video_{index + 1}",
                "source_url": "/local-smoke/source.mp4",
                "source_node_id": None,
                "source_path": str(source_path),
                "timeline_start": index * 5.0 / 3.0,
                "source_start": 0.0,
                "source_end": 5.0,
                "volume": 1.0,
                "muted": False,
                "speed": 3.0,
            }
            for index in range(3)
        ]
        tracks = [{"track_id": "video", "kind": "video", "items": items}]
        subtitle_tracks = [
            {
                "track_id": "subtitle",
                "cues": [
                    {
                        "cue_id": "zh_smoke",
                        "text": "你好，世界",
                        "timeline_start": 0.05,
                        "timeline_end": 0.48,
                        "timing": "manual",
                        "timing_confirmed": True,
                    },
                    {
                        "cue_id": "en_smoke",
                        "text": "Hello, subtitles!",
                        "timeline_start": 4.52,
                        "timeline_end": 4.95,
                        "timing": "manual",
                        "timing_confirmed": True,
                    },
                ],
            }
        ]
        manifest = build_export_manifest(
            project_id="local-smoke",
            project_dir=project_dir,
            resolution="720p",
            resolution_origin="user",
            fps=30,
            fps_origin="user",
            tracks=tracks,
            subtitle_tracks=subtitle_tracks,
            draft_revision=1,
            dubbing_store=None,
        )
        output_path = await run_freezone_video_compose(
            project_dir=project_dir,
            job_id="subtitle_smoke",
            resolution="720p",
            fps=30,
            tracks=tracks,
            subtitle_tracks=subtitle_tracks,
            draft_revision=1,
            export_manifest=manifest,
            project_id="local-smoke",
        )

        probe = json.loads(
            _run(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-show_entries",
                    "stream=codec_type",
                    "-of",
                    "json",
                    str(output_path),
                ]
            ).stdout
        )
        if not any(stream.get("codec_type") == "video" for stream in probe.get("streams", [])):
            raise RuntimeError("rendered smoke output has no playable video stream")
        duration = float(probe.get("format", {}).get("duration") or 0)
        if not 4.8 <= duration <= 5.2:
            raise RuntimeError(f"fractional 3x clips rendered to unexpected duration: {duration:.3f}s")

        sidecar_path = output_path.with_suffix(".manifest.json")
        sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
        frozen = sidecar["manifest"]
        artifact = sidecar["artifact"]
        subtitle = artifact["subtitle_file"]
        subtitle_path = project_dir / subtitle["path"]
        subtitle_text = subtitle_path.read_text(encoding="utf-8")
        for expected in ("你好，世界", "Hello, subtitles!"):
            if expected not in subtitle_text:
                raise RuntimeError(f"SRT sidecar is missing expected caption: {expected}")
        if frozen.get("draft_revision") != 1 or frozen.get("timeline_revision") != manifest["timeline_revision"]:
            raise RuntimeError("sidecar does not preserve the frozen timeline revision")
        if artifact.get("sha256") != sha256_file(output_path):
            raise RuntimeError("sidecar video hash does not match rendered output")
        if subtitle.get("sha256") != sha256_file(subtitle_path):
            raise RuntimeError("sidecar subtitle hash does not match rendered SRT")
        if subtitle.get("rendered_in_video") is not True or not artifact.get("ffprobe"):
            raise RuntimeError("sidecar does not confirm subtitle rendering and media probing")

        print(
            "PASS: FFmpeg rendered three fractionally packed 3x clips, burned Chinese/English subtitles, "
            "and wrote hash-linked SRT/export-manifest sidecars."
        )


if __name__ == "__main__":
    asyncio.run(main())
