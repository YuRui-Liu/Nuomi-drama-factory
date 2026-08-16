"""Real smoke tests for the auxiliary RunningHub and local imaging workflows."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from PIL import Image, ImageDraw


def _load_key(env_file: Path) -> str:
    for name in ("RUNNINGHUB_API_KEY", "RUNNINGHUB_KEY"):
        if os.environ.get(name):
            return str(os.environ[name])
    for line in env_file.read_text(encoding="utf-8-sig").splitlines():
        name, separator, value = line.partition("=")
        if separator and name.strip() in {"RUNNINGHUB_API_KEY", "RUNNINGHUB_KEY"}:
            key = value.strip().strip("'\"")
            if key:
                return key
    project_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(project_root / "src"))
    from novelvideo.media_capabilities.runtime.credential_store import WindowsCredentialStore
    from novelvideo.media_capabilities.runtime.credentials import CredentialResolver
    from novelvideo.media_capabilities.store import MediaCapabilityStore

    settings_path = project_root / "state" / "local" / "settings.db"
    credential_path = project_root / "state" / "local" / "media-credentials.dpapi.json"
    store = MediaCapabilityStore(settings_path)
    account = store.get_provider("runninghub-main")
    if account is None:
        raise RuntimeError("RunningHub provider is unavailable in software settings")
    credential_store = WindowsCredentialStore(credential_path)
    return CredentialResolver(keyring_reader=credential_store.get).resolve(account.credential_ref)


def _make_grid(path: Path) -> None:
    image = Image.new("RGB", (512, 512), "white")
    draw = ImageDraw.Draw(image)
    colors = ("#d94b4b", "#3f7fdb", "#51a663", "#d99a32")
    for index, color in enumerate(colors):
        x = 16 + (index % 2) * 240
        y = 16 + (index // 2) * 240
        draw.rectangle((x, y, x + 223, y + 223), fill=color)
        draw.ellipse((x + 64, y + 64, x + 159, y + 159), fill="white")
    image.save(path)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--skill-root", type=Path, required=True)
    parser.add_argument("--upscale-workflow", required=True)
    parser.add_argument("--voice-design-workflow", required=True)
    parser.add_argument("--voice-clone-workflow", required=True)
    args = parser.parse_args()

    sys.path.insert(0, str(args.skill_root))
    from imaging.aspect_ops import trim_white_edges
    from providers.runninghub import RunningHubProvider

    args.output_dir.mkdir(parents=True, exist_ok=True)
    grid = args.output_dir / "synthetic-grid-with-white-border.png"
    trimmed_path = args.output_dir / "synthetic-grid-trimmed.png"
    _make_grid(grid)
    source = Image.open(grid)
    trimmed = trim_white_edges(source)
    trimmed.save(trimmed_path)
    if trimmed.size != (464, 464):
        raise RuntimeError(f"unexpected trim result: {source.size} -> {trimmed.size}")

    provider = RunningHubProvider(
        {
            "api_key": _load_key(args.env_file),
            "base_url": "https://www.runninghub.cn",
            "upscale_workflow_id": args.upscale_workflow,
            "voice_design_workflow_id": args.voice_design_workflow,
            "dub_clone_workflow_id": args.voice_clone_workflow,
            "poll_interval": 5,
            "poll_timeout": 1800,
        }
    )
    voice = Path(
        provider.design_voice(
            "qwen3-public-smoke",
            "青年女性，普通话清晰自然，语速适中，语气温和，录音干净",
            "zh",
            str(args.output_dir),
        )
    )
    if voice.stat().st_size < 1024:
        raise RuntimeError("Qwen3 voice-design output is unexpectedly small")

    clone = args.output_dir / "indextts2-public-smoke.wav"
    clone.write_bytes(
        provider.clone_voice(
            "这是一次端到端工作流连通性测试，声音内容不包含任何私人信息。",
            str(voice),
            "平静、自然、清晰",
        )
    )
    if clone.stat().st_size < 1024:
        raise RuntimeError("IndexTTS2 output is unexpectedly small")

    upscale_paths = [
        Path(item)
        for item in provider.upscale_grid(str(trimmed_path), 2, 2, str(args.output_dir / "upscale"))
    ]
    if not upscale_paths or any(path.stat().st_size < 1024 for path in upscale_paths):
        raise RuntimeError("upscale/split output is missing or unexpectedly small")

    report = {
        "trim_white_edges": {
            "source": str(grid.resolve()),
            "source_size": list(source.size),
            "output": str(trimmed_path.resolve()),
            "output_size": list(trimmed.size),
        },
        "qwen3_voice_design": {"output": str(voice.resolve()), "bytes": voice.stat().st_size},
        "indextts2_voice_clone": {"output": str(clone.resolve()), "bytes": clone.stat().st_size},
        "upscale_split": [
            {"output": str(path.resolve()), "bytes": path.stat().st_size}
            for path in upscale_paths
        ],
    }
    report_path = args.output_dir / "smoke-report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
