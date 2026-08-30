"""Explicit-cost smoke through the real application provider orchestration."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import re
import sys
import time
from io import BytesIO
from pathlib import Path

from PIL import Image

from novelvideo.director_plan.models import SourceSpan
from novelvideo.director_plan.planner import DirectorPlanInput, DirectorPlanner
from novelvideo.director_plan.service import DirectorPlanService
from novelvideo.director_plan.store import DirectorPlanStore
from novelvideo.extension_styles.schema import FRAGMENT_KEYS
from novelvideo.media_capabilities.models import ImageGenerationRequest, MediaCapability
from novelvideo.media_capabilities.video.h3_episode_pack import H3EpisodeInput, H3EpisodeVideoSegment, create_h3_episode_pack_optimizer
from novelvideo.media_capabilities.video.h3_prompt_optimizer import H3PromptContext
from novelvideo.media_capabilities.video.h3_timeline import H3DirectorSegment
from novelvideo.media_capabilities.video.models import H3Mode
from novelvideo.media_capabilities.video.runtime import generate_h3_director_video
from novelvideo.project_context import ProjectContext
from novelvideo.styles.resolver import ProjectionStyle, StyleResolver
from novelvideo.task_backend.runners.narrative_group import _split_existing_grid
from novelvideo.task_backend.runners.narrative_group_video import run_video_segment
from novelvideo.task_backend.runners.narrative_group_video_compose import SegmentCompositionItem, build_local_composition_plan
from novelvideo.text_runtime_settings import load_text_runtime_settings


_SECRETS: list[str] = []
_TOKEN = re.compile(r"(?i)(bearer\s+|sk-)[A-Za-z0-9._-]+")


class _TrackingAgent:
    def __init__(self, agent: object, model_name: str) -> None:
        self._agent = agent
        self.model_name = model_name
        self.request_id = ""

    async def run(self, prompt: str) -> object:
        response = await self._agent.run(prompt)
        for message in reversed(response.all_messages()):
            details = getattr(message, "provider_details", None) or {}
            candidate = str(details.get("response_id") or details.get("request_id") or "").strip()
            if candidate:
                self.request_id = candidate
                break
        return response


def _safe_detail(exc: Exception) -> str:
    value = str(exc)
    for secret in sorted((item for item in _SECRETS if item), key=len, reverse=True):
        value = value.replace(secret, "***")
    value = _TOKEN.sub(lambda match: f"{match.group(1)}***", value)
    value = re.sub(r"(?is)\b(?:body|headers?)\s*=.*", "provider response omitted", value)
    return " ".join(value.split())[:300]


def _ctx(root: Path) -> ProjectContext:
    return ProjectContext(project_id="director-v2-real-smoke", project_name="director-v2-real-smoke", owner_type="user", owner_id="operator", owner_username="operator", requester_user_id="operator", requester_username="operator", requester_principals=(("user", "operator"),), effective_role="owner", home_node_id="local", output_dir=root, state_dir=root / "state", runtime_dir=root / "runtime", is_home_node=True)


async def _wait_grsai(client: object, task_id: str, key: str) -> object:
    deadline = time.monotonic() + 900
    while time.monotonic() < deadline:
        snapshot = await client.query(task_id, api_key=key)
        if snapshot.status == "succeeded":
            if not snapshot.results:
                raise RuntimeError("GRSAI succeeded without results")
            return snapshot
        if snapshot.status in {"failed", "violation", "cancelled"}:
            raise RuntimeError(f"GRSAI task ended as {snapshot.status}")
        await asyncio.sleep(5)
    raise TimeoutError("GRSAI task timed out")


async def _run(root: Path) -> None:
    from novelvideo.api.deps import get_media_capability_store, get_media_credential_resolver
    from novelvideo.media_capabilities.runtime.configuration import load_grsai_runtime_configuration, load_runninghub_runtime_configuration

    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    style = StyleResolver((ProjectionStyle(id="cinematic", version="1", panel_tag="cinematic-panel", prompt_fragment={key: (f"{key} cinematic",) for key in FRAGMENT_KEYS}),)).resolve("cinematic", None)
    text_runtime = load_text_runtime_settings()
    media_store, credential_resolver = get_media_capability_store(), get_media_credential_resolver()
    grsai_runtime = load_grsai_runtime_configuration(media_store, credential_resolver)
    runninghub_runtime = load_runninghub_runtime_configuration(media_store, credential_resolver)
    _SECRETS.extend((text_runtime.api_key, grsai_runtime.api_key, runninghub_runtime.api_key))

    spans = (SourceSpan(id="span-1", ordinal=1, scene="走廊", time="夜", text="林默走到门前并打开门。"),)
    store = DirectorPlanStore(root)
    configured_planner = DirectorPlanner()
    tracked_agent = _TrackingAgent(configured_planner._agent, configured_planner.model_name)
    draft = await DirectorPlanService(store, DirectorPlanner(agent=tracked_agent)).create_draft(DirectorPlanInput(episode=1, source_script_hash="b" * 64, source_spans=spans, relevant_bible={}, aspect_ratio="9:16", style_director={"projection": style.projections.director}, project_style_snapshot_id=style.snapshot_id, project_style_snapshot=style))
    if not draft.validation_report.passed:
        raise RuntimeError("DeepSeek DirectorPlan schema validation failed")
    active = store.activate(1, draft.revision_id)
    deepseek_id = tracked_agent.request_id
    if not deepseek_id:
        raise RuntimeError("DeepSeek response omitted provider request id")
    plan_path = root / "director-plan.json"
    plan_path.write_text(active.model_dump_json(indent=2), encoding="utf-8")

    grsai = grsai_runtime.create_client()
    try:
        task_id = await grsai.submit(ImageGenerationRequest(capability=MediaCapability.IMAGE_STORYBOARD_GRID, model=grsai_runtime.model, prompt="Vertical 9:16 cinematic storyboard diptych, two equal cells, adult man approaches a door then opens it, safe neutral night corridor, no text, no border", aspect_ratio="9:16", image_size="1K"), api_key=grsai_runtime.api_key)
        snapshot = await _wait_grsai(grsai, task_id, grsai_runtime.api_key)
        image_bytes = await grsai.download(str(snapshot.results[0]["url"]))
        with Image.open(BytesIO(image_bytes)) as decoded:
            decoded.verify()
        image_path = root / "grsai-grid.png"
        image_path.write_bytes(image_bytes)
    finally:
        await grsai.http.aclose()

    split = _split_existing_grid(str(image_path), {"output_dir": str(root), "episode": 1, "stage": "render", "revision": 1, "group_id": active.groups[0].id, "batch_id": "real-smoke", "aspect_ratio": "9:16", "layout": {"rows": 1, "columns": 2}, "beats": [{"beat_number": 1}, {"beat_number": 2}], "cell_to_beat": [{"beat_id": "start"}, {"beat_id": "end"}], "style_hash": style.style_hash}, _ctx(root))
    first_frame = str(split["cell_assets"][0]["path"])
    duration = min(5.0, sum(shot.duration_seconds for shot in active.groups[0].shots))
    source = H3DirectorSegment(segment_id="segment-real-1", beat_number=1, prompt="林默走到门前并打开门", duration_seconds=duration, first_frame=first_frame)
    entry = H3EpisodeVideoSegment(segment_id=source.segment_id, group_id=active.groups[0].id, shot_ids=tuple(shot.id for shot in active.groups[0].shots), duration_seconds=duration, style_snapshot_id=style.snapshot_id, source_segment=source, context=H3PromptContext(visual_description="夜间走廊里林默站在门边", narration="林默打开门", prev_summary="", next_summary="", first_frame_sha256=hashlib.sha256(Path(first_frame).read_bytes()).hexdigest(), model_id=text_runtime.model), mode=H3Mode.I2VA, summary="林默打开门", scene_anchor="夜间走廊")
    optimized = await create_h3_episode_pack_optimizer(cache_dir=root / "h3-cache").optimize(H3EpisodeInput(episode=1, director_revision_id=active.revision_id, style_hash=style.style_hash, style_video={"projection": style.projections.video}, segments=(entry,)))
    source = source.model_copy(update={"prompt": optimized.segments[0].prompt})
    context = _ctx(root)

    async def provider(_request: object) -> object:
        return await generate_h3_director_video(context, segments=(source,), output_path=str(root / "runninghub-h3.mp4"), aspect_ratio="9:16")

    result = await run_video_segment(source, tts=None, provider=provider, audio_mode="h3_original")
    if result.status != "completed" or not result.output_path or not result.provider_task_id:
        raise RuntimeError("RunningHub segment runner returned incomplete evidence")
    composition = build_local_composition_plan((SegmentCompositionItem(group_ordinal=1, segment_ordinal=1, path=result.output_path, relation_to_previous="single"),))
    if composition.paths != (result.output_path,):
        raise RuntimeError("segment composition path mismatch")

    print(f"deepseek_request_id={deepseek_id}")
    print(f"grsai_request_id={task_id}")
    print(f"runninghub_request_id={result.provider_task_id}")
    for path in (plan_path, image_path, Path(result.output_path)):
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
    except Exception as exc:
        response = getattr(exc, "response", None)
        status = getattr(exc, "status_code", None) or getattr(response, "status_code", None) or "unknown"
        content_type = getattr(response, "headers", {}).get("content-type", "unknown")
        print(f"provider_smoke_failed type={type(exc).__name__} status={status} content_type={content_type} detail={_safe_detail(exc)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
