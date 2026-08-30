"""Free smoke through Director Plan v2 production orchestration paths."""
from __future__ import annotations

import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from PIL import Image, ImageDraw
from novelvideo.director_plan.models import NarrativeGroupPlan, ShotPlan, SourceSpan
from novelvideo.director_plan.planner import DirectorPlanDraft, DirectorPlanInput, DirectorPlanner
from novelvideo.director_plan.service import DirectorPlanService
from novelvideo.director_plan.store import DirectorPlanStore
from novelvideo.extension_styles.schema import FRAGMENT_KEYS
from novelvideo.media_capabilities.video.h3_director_plan import H3ActionPlan, H3CameraPlan, H3DirectorPlan, H3ShotPlan
from novelvideo.media_capabilities.video.h3_episode_pack import H3EpisodeInput, H3EpisodePackOptimizer, H3EpisodePromptPack, H3EpisodeVideoSegment, H3SegmentPromptPlan
from novelvideo.media_capabilities.video.h3_prompt_optimizer import H3PromptContext
from novelvideo.media_capabilities.video.h3_timeline import H3DirectorSegment
from novelvideo.media_capabilities.video.models import H3Mode
from novelvideo.narrative_groups.service import load_materialized_groups
from novelvideo.styles.resolver import ProjectionStyle, StyleResolver
from novelvideo.task_backend.runners.narrative_group import _split_existing_grid
from novelvideo.task_backend.runners.narrative_group_video import run_video_segments
from novelvideo.task_backend.runners.narrative_group_video_compose import SegmentCompositionItem, build_local_composition_plan


class _Agent:
    def __init__(self, output: object, model_name: str = "local-fake") -> None:
        self.output, self.model_name = output, model_name

    async def run(self, _prompt: str) -> SimpleNamespace:
        return SimpleNamespace(output=self.output)


def _shot(number: int, span: str) -> ShotPlan:
    return ShotPlan(id=f"shot-{number}", source_span_ids=(span,), subject="林默", action="快步走到门边并停住", visible_start_state="站在走廊远端", visible_end_state="停在门把手前", duration_seconds=3)


def _groups() -> tuple[NarrativeGroupPlan, ...]:
    return (
        NarrativeGroupPlan(id="ng-1", ordinal=1, source_span_ids=("span-1",), scene_anchor="走廊", time_anchor="夜", objective="接近房门", visible_turn="人物停在门前", relation_to_previous="single", shots=(_shot(1, "span-1"),)),
        NarrativeGroupPlan(id="ng-2", ordinal=2, source_span_ids=("span-2", "span-3"), scene_anchor="走廊", time_anchor="夜", objective="打开房门", visible_turn="门被推开", relation_to_previous="causal", shots=(_shot(2, "span-2"), _shot(3, "span-3"))),
    )


def _h3_plan() -> H3DirectorPlan:
    return H3DirectorPlan(mode=H3Mode.I2VA, total_frames=72, visual_style="cinematic realism", continuity_locks=("preserve identity and corridor geography",), shots=(H3ShotPlan(shot_id="1", start_frame=0, end_frame=72, framing="medium shot", angle="eye level", focus="Lin", composition="Lin beside the door", camera=H3CameraPlan(type="static"), actions=(H3ActionPlan(phase="establish", start_frame=0, end_frame=24, description="Hold the exact opening pose."), H3ActionPlan(phase="execute", start_frame=24, end_frame=56, description="He quickly reaches the handle and stops with his grip locked."), H3ActionPlan(phase="settle", start_frame=56, end_frame=72, description="He deliberately opens the door and holds at the threshold."))),), soundscape="Quiet footsteps and a latch click.", music="Low strings.")


async def _run(root: Path) -> None:
    style = StyleResolver((ProjectionStyle(id="cinematic", version="1", panel_tag="cinematic-panel", prompt_fragment={key: (f"{key} cinematic",) for key in FRAGMENT_KEYS}),)).resolve("cinematic", None)
    spans = tuple(SourceSpan(id=f"span-{n}", ordinal=n, scene="走廊", time="夜", text=f"动作 {n}") for n in range(1, 4))
    store = DirectorPlanStore(root)
    planner = DirectorPlanner(agent=_Agent(DirectorPlanDraft(groups=_groups())))
    draft = await DirectorPlanService(store, planner).create_draft(DirectorPlanInput(episode=1, source_script_hash="a" * 64, source_spans=spans, relevant_bible={}, aspect_ratio="9:16", style_director={"projection": style.projections.director}, project_style_snapshot_id=style.snapshot_id, project_style_snapshot=style))
    assert draft.validation_report.passed
    active = store.activate(1, draft.revision_id)
    groups = load_materialized_groups(root, 1)
    assert active.status == "active"
    assert [len(g.generation_batches[0]["shot_ids"]) for g in groups] == [1, 2]

    grid = Image.new("RGB", (182, 320), "white")
    draw = ImageDraw.Draw(grid)
    draw.rectangle((0, 0, 89, 319), fill=(30, 60, 90))
    draw.rectangle((93, 0, 181, 319), fill=(90, 60, 30))
    grid_path = root / "grid.png"
    grid.save(grid_path)
    split = _split_existing_grid(str(grid_path), {"output_dir": str(root), "episode": 1, "stage": "render", "revision": 1, "group_id": "ng-2", "batch_id": "batch:ng-2:1", "aspect_ratio": "9:16", "layout": {"rows": 1, "columns": 2}, "beats": [{"beat_number": 2}, {"beat_number": 3}], "cell_to_beat": [{"beat_id": "shot-2"}, {"beat_id": "shot-3"}], "style_hash": style.style_hash}, SimpleNamespace(output_dir=str(root)))
    assert len(split["cell_assets"]) == 2

    planned = tuple(segment for group in groups for segment in group.video_segments)
    sources = tuple(H3DirectorSegment(segment_id=str(segment["id"]), beat_number=index, prompt="林默走到门前并停住", duration_seconds=float(segment["duration_seconds"]), first_frame=str(split["cell_assets"][min(index - 1, 1)]["path"])) for index, segment in enumerate(planned, start=1))
    episode_segments = tuple(H3EpisodeVideoSegment(segment_id=source.segment_id, group_id=str(planned[index]["group_id"]), shot_ids=tuple(planned[index]["shot_ids"]), duration_seconds=source.duration_seconds, style_snapshot_id=style.snapshot_id, source_segment=source, context=H3PromptContext(visual_description="夜间走廊里林默站在门边", narration="林默打开门", prev_summary="", next_summary="", first_frame_sha256=str(index + 1) * 64, model_id="local-fake"), mode=H3Mode.I2VA, summary="林默打开门", scene_anchor="夜间走廊") for index, source in enumerate(sources))
    pack = H3EpisodePromptPack(episode=1, director_revision_id=active.revision_id, segments=tuple(H3SegmentPromptPlan(segment_id=source.segment_id, director_plan=_h3_plan()) for source in sources))
    optimized = await H3EpisodePackOptimizer(_Agent(pack), root / "h3-cache").optimize(H3EpisodeInput(episode=1, director_revision_id=active.revision_id, style_hash=style.style_hash, style_video={"projection": style.projections.video}, segments=episode_segments))
    optimized_sources = tuple(source.model_copy(update={"prompt": result.prompt}) for source, result in zip(sources, optimized.segments, strict=True))

    async def provider(request: object) -> SimpleNamespace:
        segment_id = request.segment_ids[0]
        output = root / f"{segment_id.replace(':', '-')}.mp4"
        output.write_bytes(b"local-fake-video")
        return SimpleNamespace(output_path=str(output), provider_task_id=f"fake:{segment_id}")

    results = await run_video_segments(optimized_sources, tts=None, provider=provider)
    assert all(result.status == "completed" for result in results)
    composition = build_local_composition_plan(tuple(SegmentCompositionItem(group_ordinal=1 if segment.group_id == "ng-1" else 2, segment_ordinal=index, path=str(result.output_path), relation_to_previous="single" if index == 1 else "causal") for index, (segment, result) in enumerate(zip(episode_segments, results, strict=True), start=1)))
    assert len(composition.paths) == 3 and len(composition.transitions) == 2


def main() -> int:
    with TemporaryDirectory(prefix="director-plan-v2-") as raw:
        asyncio.run(_run(Path(raw)))
    print("DIRECTOR_PLAN_V2_LOCAL_SMOKE_OK groups=2 batches=2 segments=3")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
