import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from novelvideo.director_plan.models import (
    DirectorPlanRevision,
    NarrativeGroupPlan,
    ShotPlan,
    ValidationReport,
)
from novelvideo.director_plan.store import DirectorPlanStore
from novelvideo.narrative_groups import service
from novelvideo.narrative_groups.models import GridLayout, NarrativeGroup, VideoPlan
from novelvideo.narrative_groups.service import load_groups, save_groups, sidecar_path


def _group(group_id: str = "ng-01", **overrides) -> NarrativeGroup:
    base = dict(
        id=group_id,
        ordinal=1,
        beat_ids=("shot-01-01", "shot-01-02"),
        layout=GridLayout(rows=1, columns=2, capacity=2),
        cell_to_beat=(),
    )
    base.update(overrides)
    return NarrativeGroup(**base)


def _plan_shot(shot_id: str, source_span_id: str) -> ShotPlan:
    return ShotPlan(
        id=shot_id,
        source_span_ids=(source_span_id,),
        subject="hero",
        action="acts",
        visible_start_state="before",
        visible_end_state="after",
        duration_seconds=3,
    )


def _plan_group(
    group_id: str,
    ordinal: int,
    source_span_ids: tuple[str, ...],
    shot_ids: tuple[str, ...],
) -> NarrativeGroupPlan:
    return NarrativeGroupPlan(
        id=group_id,
        ordinal=ordinal,
        source_span_ids=source_span_ids,
        scene_anchor="hallway",
        time_anchor="night",
        objective=f"objective-{ordinal}",
        visible_turn=f"turn-{ordinal}",
        relation_to_previous="single" if ordinal == 1 else "causal",
        shots=tuple(
            _plan_shot(shot_id, source_span_ids[min(index, len(source_span_ids) - 1)])
            for index, shot_id in enumerate(shot_ids)
        ),
    )


def _activate(
    project_dir: Path,
    groups: tuple[NarrativeGroupPlan, ...],
    *,
    revision_id: str = "rev-active",
) -> DirectorPlanRevision:
    """Save and activate a director plan revision (same pattern as test_legacy_projection)."""

    revision = DirectorPlanRevision(
        revision_id=revision_id,
        episode=1,
        status="review_required",
        source_script_hash="sha256:abc",
        director_model="director-v1",
        prompt_version="v2",
        project_style_snapshot_id="style-1",
        groups=groups,
        validation_report=ValidationReport(passed=True),
        created_at=datetime(2026, 8, 30, 12, tzinfo=timezone.utc),
    )
    store = DirectorPlanStore(project_dir)
    store.save(revision)
    return store.activate(1, revision.revision_id)


def test_group_defaults_to_no_image_prompt_overrides():
    assert _group().image_prompt_overrides == {}


def test_sidecar_without_the_field_still_loads(tmp_path):
    project = tmp_path / "proj"
    save_groups(project, 1, [_group()])

    path = sidecar_path(project, 1)
    payload = json.loads(path.read_text(encoding="utf-8"))
    for item in payload["groups"]:
        item.pop("image_prompt_overrides", None)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    assert load_groups(project, 1)[0].image_prompt_overrides == {}


def test_overrides_survive_a_sidecar_round_trip(tmp_path):
    project = tmp_path / "proj"
    save_groups(project, 1, [_group(image_prompt_overrides={"shot-01-01": "改成铜甲"})])

    assert load_groups(project, 1)[0].image_prompt_overrides == {"shot-01-01": "改成铜甲"}


def test_blank_override_values_are_dropped_on_load(tmp_path):
    project = tmp_path / "proj"
    save_groups(project, 1, [_group()])

    path = sidecar_path(project, 1)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["groups"][0]["image_prompt_overrides"] = {
        "shot-01-01": "   ",
        "shot-01-02": "保留",
        "shot-01-03": "",
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    assert load_groups(project, 1)[0].image_prompt_overrides == {"shot-01-02": "保留"}


def test_overrides_survive_an_unchanged_projection_sync(tmp_path: Path) -> None:
    """规格：投影结构不变时，覆盖随同步保留，且保留结果落盘。"""

    _activate(tmp_path, (_plan_group("director-a", 1, ("span-1",), ("shot-1",)),))
    [materialized] = service.load_materialized_groups(tmp_path, 1)
    service.save_groups(
        tmp_path,
        1,
        [
            replace(
                materialized,
                image_prompt_overrides={"shot-1": "改成铜甲"},
                # 清空 video_plan 以强制本次同步真正重写 sidecar（结构判定不含该字段）。
                video_plan=VideoPlan(),
            )
        ],
    )

    [projected] = service.load_effective_groups(tmp_path, 1, [])

    assert projected.video_plan.units  # 同步确实修复并重写了 sidecar
    assert projected.image_prompt_overrides == {"shot-1": "改成铜甲"}
    payload = json.loads(sidecar_path(tmp_path, 1).read_text(encoding="utf-8"))
    assert payload["groups"][0]["image_prompt_overrides"] == {"shot-1": "改成铜甲"}


def test_overrides_survive_the_legacy_ensure_path(tmp_path: Path) -> None:
    """规格：无活跃导演计划时，旧节拍制分支同样保留覆盖。"""

    project = tmp_path / "proj"
    save_groups(project, 1, [_group(image_prompt_overrides={"shot-01-01": "改成铜甲"})])

    [group] = service.load_effective_groups(
        project, 1, [{"id": "shot-01-01"}, {"id": "shot-01-02"}]
    )

    assert group.image_prompt_overrides == {"shot-01-01": "改成铜甲"}
    payload = json.loads(sidecar_path(project, 1).read_text(encoding="utf-8"))
    assert payload["groups"][0]["image_prompt_overrides"] == {"shot-01-01": "改成铜甲"}
