"""Phase reporting for the narrative-group video task.

Regression: 「生成组合视频」kept the task centre at its 1%「任务已开始」seed for
the whole run, so the progress bar never moved and no phase text appeared even
though the run has clearly separated phases (prompt planning → one provider
round trip per segment → composition).
"""

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from novelvideo.task_backend.runners import narrative_group_video
from tests.test_task_narrative_group_video_runner import (
    _optimizer_result,
    _patch_segment_optimizer,
    _seed_group,
)


class RecordingManager:
    def __init__(self):
        self.updates = []

    def update_progress_for_project(self, ctx, task_type, episode, **kwargs):
        self.updates.append(
            SimpleNamespace(
                task_type=task_type,
                episode=episode,
                scope=kwargs.get("scope"),
                progress=kwargs.get("progress"),
                current_task=kwargs.get("current_task"),
                logs=kwargs.get("logs"),
                expected_task_id=kwargs.get("expected_task_id"),
            )
        )


def test_progress_reporter_routes_to_the_video_task(monkeypatch):
    manager = RecordingManager()
    monkeypatch.setattr(narrative_group_video, "get_task_manager", lambda: manager)
    reporter = narrative_group_video._progress_reporter(
        SimpleNamespace(project_id="demo"),
        {"scope": "group_ng-01_video_r1", "__run_task_id": "run-1"},
        3,
    )
    reporter(0.5, "生成中…")

    [update] = manager.updates
    assert update.task_type == "narrative_group_video"
    assert update.episode == 3
    assert update.scope == "group_ng-01_video_r1"
    assert update.progress == 0.5
    assert update.current_task == "生成中…"
    assert update.logs == ["生成中…"]
    assert update.expected_task_id == "run-1"


@pytest.mark.parametrize(
    "value,expected", [(-1.0, 0.0), (0.0, 0.0), (0.42, 0.42), (1.0, 1.0), (7.0, 1.0)]
)
def test_progress_reporter_clamps_out_of_range_values(monkeypatch, value, expected):
    manager = RecordingManager()
    monkeypatch.setattr(narrative_group_video, "get_task_manager", lambda: manager)
    narrative_group_video._progress_reporter(
        SimpleNamespace(project_id="demo"), {}, 1
    )(value, "x")

    assert manager.updates[0].progress == expected


def test_progress_reporting_never_breaks_generation(monkeypatch):
    class Broken:
        def update_progress_for_project(self, *args, **kwargs):
            raise RuntimeError("task store unavailable")

    monkeypatch.setattr(narrative_group_video, "get_task_manager", lambda: Broken())
    reporter = narrative_group_video._progress_reporter(
        SimpleNamespace(project_id="demo"), {}, 1
    )

    reporter(0.5, "生成中…")  # must not raise


def test_group_video_reports_each_phase_to_the_task_centre(tmp_path, monkeypatch):
    _seed_group(tmp_path)
    manager = RecordingManager()
    monkeypatch.setattr(narrative_group_video, "get_task_manager", lambda: manager)

    class Optimizer:
        async def optimize_segment(self, segment, context, mode):
            await asyncio.sleep(0)
            return _optimizer_result(f"优化：{segment.segment_id}", segment=segment)

    async def get_beats(_ctx, _episode):
        return [
            {"id": "beat-1", "beat_number": 1, "video_prompt": "雨夜里他抬头。", "dialogue": "别走。", "speaker": "阿明", "tone": "急切"},
            {"id": "beat-2", "beat_number": 2, "video_prompt": "她停在门口。", "dialogue": "我会回来。", "speaker": "小雨", "tone": "克制"},
        ]

    async def generate(ctx, *, segments, output_path, **_kwargs):
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_bytes(b"video")
        return SimpleNamespace(
            output_path=output_path, provider_task_id="provider-1", actual_mode="fl2va"
        )

    async def separate(video, _directory):
        return {
            "original_audio_path": str(video),
            "dialogue_stem_path": str(video),
            "ambience_stem_path": str(video),
            "dialogue_stem_status": "succeeded",
            "ambience_stem_status": "succeeded",
        }

    monkeypatch.setattr(narrative_group_video, "_load_canonical_beats", get_beats)
    _patch_segment_optimizer(monkeypatch, narrative_group_video, Optimizer())
    monkeypatch.setattr(narrative_group_video, "generate_h3_director_video", generate)
    monkeypatch.setattr(narrative_group_video, "_separate_stems", separate)
    ctx = SimpleNamespace(
        output_dir=str(tmp_path),
        runtime_dir=str(tmp_path),
        state_dir=tmp_path / "state",
        project_id="demo",
    )

    result = narrative_group_video.run_narrative_group_video(
        {
            "episode": 1,
            "scope": "group_ng-01_video_r1",
            "payload": {"group_id": "ng-01", "revision": 1, "mode": "auto"},
        },
        ctx,
    )

    assert result["status"] == "completed"
    values = [update.progress for update in manager.updates]
    messages = [update.current_task for update in manager.updates]
    assert manager.updates and all(
        update.task_type == "narrative_group_video"
        and update.scope == "group_ng-01_video_r1"
        and update.episode == 1
        for update in manager.updates
    )
    # The bar must advance monotonically and land near the end.
    assert values == sorted(values), values
    assert values[0] <= 0.05 and values[-1] >= 0.9, values
    assert len(set(values)) >= 5, values
    assert any("分镜提示词" in message for message in messages)
    assert any("1/2" in message for message in messages)
    assert any("2/2" in message for message in messages)
    assert any("合片" in message for message in messages)
