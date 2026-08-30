"""Cost-gated real-provider smoke for DeepSeek, GRSAI, and RunningHub.

This script deliberately contains no mock or fallback path. It is not part of
the free test suite and must be invoked explicitly with ``--confirm-cost``.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from novelvideo.media_capabilities.runtime.runninghub_client import RunningHubClient


_SECRET = re.compile(r"(?i)(bearer\s+|sk-)[A-Za-z0-9._-]+")


def _redact(value: object) -> str:
    return _SECRET.sub(lambda match: f"{match.group(1)}***", str(value))[:800]


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"missing required configuration: {name}")
    return value


async def _poll_json(client: httpx.AsyncClient, url: str, headers: dict[str, str], task_id: str) -> dict:
    deadline = time.monotonic() + 900
    while time.monotonic() < deadline:
        response = await client.get(url, params={"id": task_id}, headers=headers)
        response.raise_for_status()
        data = response.json()
        if data.get("status") == "succeeded":
            return data
        if data.get("status") in {"failed", "violation", "cancelled"}:
            raise RuntimeError(f"provider task {task_id} ended as {data.get('status')}: {_redact(data.get('error'))}")
        await asyncio.sleep(5)
    raise TimeoutError(f"provider task {task_id} timed out")


async def _run(output_dir: Path) -> None:
    deepseek_key = _required("DEEPSEEK_API_KEY")
    grsai_key = _required("GRSAI_API_KEY")
    runninghub_key = os.environ.get("RUNNINGHUB_API_KEY") or _required("RUNNINGHUB_KEY")
    grsai_base = os.environ.get("GRSAI_BASE_URL", "https://grsaiapi.com").rstrip("/")
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    async with httpx.AsyncClient(timeout=120) as client:
        plan_response = await client.post(
            "https://api.deepseek.com/chat/completions",
            headers={"Authorization": f"Bearer {deepseek_key}"},
            json={
                "model": "deepseek-chat", "stream": False,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": "Return JSON only: a minimal vertical-video director plan with exactly two shots."},
                    {"role": "user", "content": "Night corridor: Lin walks to a door and opens it. Include visible start and end states."},
                ],
            },
        )
        plan_response.raise_for_status()
        plan_data = plan_response.json()
        deepseek_id = str(plan_data.get("id") or "")
        if not deepseek_id:
            raise RuntimeError("DeepSeek response omitted request id")
        plan_path = output_dir / "deepseek-plan.json"
        plan_path.write_text(plan_data["choices"][0]["message"]["content"], encoding="utf-8")

        headers = {"Authorization": f"Bearer {grsai_key}"}
        image_response = await client.post(
            f"{grsai_base}/v1/images/generations", headers=headers,
            json={
                "model": "gpt-image-2", "size": "1024x1792", "n": 1,
                "prompt": "Vertical 9:16 cinematic storyboard diptych, two equal cells, night corridor, adult man approaches a door then opens it, safe neutral scene, no text, no border",
            },
        )
        image_response.raise_for_status()
        image_data = image_response.json()
        grsai_id = str(image_data.get("id") or image_data.get("task_id") or "")
        if not grsai_id:
            raise RuntimeError("GRSAI response omitted task id")
        if image_data.get("status") != "succeeded":
            image_data = await _poll_json(client, f"{grsai_base}/v1/api/result", headers, grsai_id)
        image_url = str((image_data.get("results") or image_data.get("data") or [{}])[0].get("url") or "")
        if not image_url:
            raise RuntimeError("GRSAI succeeded without an image URL")
        image_bytes = (await client.get(image_url)).content
        image_path = output_dir / "grsai-diptych.png"
        image_path.write_bytes(image_bytes)

    workflow_id = os.environ.get("RUNNINGHUB_H3_WORKFLOW_ID", "2089723723468328961")
    async with RunningHubClient(runninghub_key) as runninghub:
        remote_image = await runninghub.upload(image_path)
        from novelvideo.media_capabilities.video.runninghub_h3 import _director_timeline_payload

        timeline = _director_timeline_payload(
            first_frame_url=remote_image, last_frame_url=None,
            prompt="At a measured pace, Lin turns the handle; the door opens and he stops visibly at the threshold. Static camera.",
            duration=5.0, aspect_ratio="9:16",
        )
        runninghub_id = await runninghub.submit(workflow_id, [{"nodeId": "12", "fieldName": "timeline_data", "fieldValue": timeline}])
        deadline = time.monotonic() + 1200
        while time.monotonic() < deadline:
            snapshot = await runninghub.query(runninghub_id)
            if snapshot.status == "succeeded":
                if not snapshot.results:
                    raise RuntimeError("RunningHub succeeded without output")
                video_url = snapshot.results[0].url
                break
            if snapshot.status in {"failed", "cancelled"}:
                raise RuntimeError(f"RunningHub {runninghub_id} ended as {snapshot.status}: {_redact(snapshot.provider_message)}")
            await asyncio.sleep(5)
        else:
            raise TimeoutError(f"RunningHub task {runninghub_id} timed out")

    host = urlsplit(video_url).hostname
    if not host:
        raise RuntimeError("RunningHub result URL has no host")
    async with RunningHubClient(runninghub_key, download_allowed_hosts={host}) as runninghub:
        video_path = output_dir / "runninghub-h3.mp4"
        video_path.write_bytes(await runninghub.download(video_url))

    print(f"deepseek_request_id={deepseek_id}")
    print(f"grsai_request_id={grsai_id}")
    print(f"runninghub_request_id={runninghub_id}")
    for path in (plan_path, image_path, video_path):
        print(f"artifact={path.resolve()}")
    print("DIRECTOR_PLAN_V2_REAL_PROVIDER_SMOKE_OK")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--confirm-cost", action="store_true")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if not args.confirm_cost:
        parser.error("--confirm-cost is required because this smoke makes paid requests")
    try:
        asyncio.run(_run(args.output_dir))
    except Exception as exc:  # operator-facing fail-closed boundary
        status = getattr(exc, "status_code", None) or getattr(getattr(exc, "response", None), "status_code", None)
        content_type = getattr(getattr(exc, "response", None), "headers", {}).get("content-type", "unknown")
        print(f"provider_smoke_failed type={type(exc).__name__} status={status or 'unknown'} content_type={content_type} detail={_redact(exc)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
