"""Run one real MiniMax H3 first-frame video smoke test via RunningHub."""

from __future__ import annotations

import argparse
import asyncio
import os
import time
from pathlib import Path
from urllib.parse import urlsplit

from novelvideo.media_capabilities.runtime.runninghub_client import RunningHubClient


WORKFLOW_ID = "2087934731806658562"


def _load_key(env_file: Path | None) -> str:
    key = os.environ.get("RUNNINGHUB_API_KEY") or os.environ.get("RUNNINGHUB_KEY")
    if key:
        return key
    if env_file is not None:
        for line in env_file.read_text(encoding="utf-8-sig").splitlines():
            name, separator, value = line.partition("=")
            if separator and name.strip() in {"RUNNINGHUB_API_KEY", "RUNNINGHUB_KEY"}:
                candidate = value.strip().strip("'\"")
                if candidate:
                    return candidate
    raise RuntimeError("RunningHub API key is unavailable")


async def _run(args: argparse.Namespace) -> None:
    api_key = _load_key(args.env_file)
    async with RunningHubClient(api_key) as client:
        task_id = args.task_id
        if task_id is None:
            if args.image is None:
                raise ValueError("--image is required when --task-id is omitted")
            remote_image = await client.upload(args.image)
            remote_last_image = await client.upload(args.last_image or args.image)
            task_id = await client.submit(
                WORKFLOW_ID,
                [
                    {"nodeId": "114", "fieldName": "image", "fieldValue": remote_image},
                    {
                        "nodeId": "115",
                        "fieldName": "aspect_ratio",
                        "fieldValue": "9:16 (Portrait Widescreen)",
                    },
                    {"nodeId": "131", "fieldName": "noise_seed", "fieldValue": 7},
                    {"nodeId": "132", "fieldName": "fps", "fieldValue": 24},
                    {"nodeId": "133", "fieldName": "prompt", "fieldValue": args.prompt},
                    {"nodeId": "135", "fieldName": "value", "fieldValue": 5},
                    {
                        "nodeId": "141",
                        "fieldName": "image",
                        "fieldValue": remote_last_image,
                    },
                ],
            )
            print(f"submitted task {task_id}")
        deadline = time.monotonic() + args.timeout
        while True:
            snapshot = await client.query(task_id)
            detail = f" message={snapshot.provider_message}" if snapshot.provider_message else ""
            print(f"status={snapshot.status}{detail}")
            if snapshot.status == "succeeded":
                if not snapshot.results:
                    raise RuntimeError("RunningHub succeeded without output")
                result_url = snapshot.results[0].url
                break
            if snapshot.status in {"failed", "cancelled"}:
                raise RuntimeError(f"RunningHub task ended with {snapshot.status}")
            if time.monotonic() >= deadline:
                raise TimeoutError("RunningHub H3 smoke test timed out")
            await asyncio.sleep(args.poll_interval)

    host = urlsplit(result_url).hostname
    if not host:
        raise RuntimeError("RunningHub result URL has no host")
    async with RunningHubClient(api_key, download_allowed_hosts={host}) as client:
        video = await client.download(result_url)
    if len(video) < 1024 or b"ftyp" not in video[:64]:
        raise RuntimeError("downloaded output is not a recognizable MP4")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(video)
    print(f"saved {len(video)} bytes to {args.output}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", type=Path)
    parser.add_argument("--last-image", type=Path)
    parser.add_argument("--task-id")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--prompt", default="人物轻轻眨眼并缓慢抬头，镜头稳定")
    parser.add_argument("--poll-interval", type=float, default=5.0)
    parser.add_argument("--timeout", type=float, default=1200.0)
    asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    main()
