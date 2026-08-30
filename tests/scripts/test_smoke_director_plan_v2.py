"""Free, deterministic Director Plan v2 end-to-end smoke."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image, ImageDraw

from novelvideo.director_plan.generation import (
    plan_generation_batches,
    plan_video_segments,
)
from novelvideo.director_plan.models import (
    DirectorPlanRevision,
    NarrativeGroupPlan,
    ShotPlan,
    SourceSpan,
)
from novelvideo.director_plan.validation import validate_director_plan
from novelvideo.media_capabilities.video.h3_director_plan import (
    H3ActionPlan,
    H3CameraPlan,
    H3DirectorPlan,
    H3ShotPlan,
)
from novelvideo.media_capabilities.video.h3_episode_pack import (
    H3EpisodePromptPack,
    H3SegmentPromptPlan,
)
from novelvideo.media_capabilities.video.h3_prompt_compiler import (
    compile_h3_director_plan,
)
from novelvideo.media_capabilities.video.models import H3Mode
from novelvideo.narrative_groups.grid_cleanup import split_and_cleanup
from novelvideo.task_backend.runners.narrative_group_video_compose import (
    SegmentCompositionItem,
    build_local_composition_plan,
)


def _shot(number: int, span_id: str) -> ShotPlan:
    return ShotPlan(
        id=f"shot-{number}",
        source_span_ids=(span_id,),
        subject="林默",
        action="快步走到门边并停住",
        visible_start_state="站在走廊远端",
        visible_end_state="停在门把手前",
        duration_seconds=3,
    )


def _fixture() -> tuple[DirectorPlanRevision, tuple[SourceSpan, ...]]:
    spans = tuple(
        SourceSpan(
            id=f"span-{number}", ordinal=number, scene="走廊", time="夜",
            text=f"动作 {number}",
        )
        for number in range(1, 4)
    )
    groups = (
        NarrativeGroupPlan(
            id="ng-1", ordinal=1, source_span_ids=("span-1",),
            scene_anchor="走廊", time_anchor="夜", objective="接近房门",
            visible_turn="人物停在门前", relation_to_previous="single",
            shots=(_shot(1, "span-1"),), style_snapshot_id="style-1",
        ),
        NarrativeGroupPlan(
            id="ng-2", ordinal=2, source_span_ids=("span-2", "span-3"),
            scene_anchor="走廊", time_anchor="夜", objective="打开房门",
            visible_turn="门被推开", relation_to_previous="causal",
            shots=(_shot(2, "span-2"), _shot(3, "span-3")),
            style_snapshot_id="style-1",
        ),
    )
    revision = DirectorPlanRevision(
        revision_id="rev-smoke", episode=1, status="draft",
        source_script_hash="a" * 64, director_model="local-smoke",
        prompt_version="v2", project_style_snapshot_id="style-1",
        groups=groups, created_at=datetime.now(timezone.utc),
    )
    return revision, spans


def _h3_plan() -> H3DirectorPlan:
    return H3DirectorPlan(
        mode=H3Mode.I2VA, total_frames=72, visual_style="cinematic realism",
        continuity_locks=("preserve identity and corridor geography",),
        shots=(H3ShotPlan(
            shot_id="1", start_frame=0, end_frame=72, framing="medium shot",
            angle="eye level", focus="Lin", composition="Lin beside the door",
            camera=H3CameraPlan(type="static"),
            actions=(
                H3ActionPlan(phase="establish", start_frame=0, end_frame=24,
                             description="Hold the exact opening pose."),
                H3ActionPlan(phase="execute", start_frame=24, end_frame=56,
                             description="He quickly reaches the handle and turns it."),
                H3ActionPlan(phase="settle", start_frame=56, end_frame=72,
                             description="The door opens and he stops at the threshold."),
            ),
        ),), soundscape="Quiet footsteps and a latch click.", music="Low strings.",
    )


def main() -> int:
    revision, spans = _fixture()
    assert validate_director_plan(revision, spans).passed
    batches = tuple(batch for group in revision.groups for batch in plan_generation_batches(group))
    assert [len(batch.shot_ids) for batch in batches] == [1, 2]
    segments = tuple(segment for group in revision.groups for segment in plan_video_segments(group))
    assert len(segments) == 3

    with TemporaryDirectory(prefix="director-plan-v2-") as raw:
        root = Path(raw)
        grid = Image.new("RGB", (182, 320), "white")
        draw = ImageDraw.Draw(grid)
        draw.rectangle((0, 0, 89, 319), fill=(30, 60, 90))
        draw.rectangle((93, 0, 181, 319), fill=(90, 60, 30))
        grid_path = root / "grid.png"
        grid.save(grid_path)
        cells, reports = split_and_cleanup(
            grid_path, expected_layout="diptych", target_aspect="9:16",
            output_dir=root / "cells",
        )
        assert len(cells) == 2 and len(reports) == 2
        assert all(path.is_file() for path in cells)

    pack = H3EpisodePromptPack(
        episode=1, director_revision_id=revision.revision_id,
        segments=tuple(
            H3SegmentPromptPlan(segment_id=segment.id, director_plan=_h3_plan())
            for segment in segments
        ),
    )
    assert all(compile_h3_director_plan(item.director_plan) for item in pack.segments)
    composition = build_local_composition_plan(tuple(
        SegmentCompositionItem(
            group_ordinal=1 if segment.group_id == "ng-1" else 2,
            segment_ordinal=index, path=f"segment-{index}.mp4",
            relation_to_previous="single" if index == 1 else "causal",
        )
        for index, segment in enumerate(segments, start=1)
    ))
    assert len(composition.paths) == 3 and len(composition.transitions) == 2
    print("DIRECTOR_PLAN_V2_LOCAL_SMOKE_OK groups=2 batches=2 segments=3")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
