from pathlib import Path

from novelvideo.media_capabilities.reference_planner import ReferenceKind
from novelvideo.production_workflow.scene_assets import (
    SceneAnchor,
    SceneAnchorPack,
    SceneBase,
    SceneStateChange,
    plan_scene_state,
)


def test_scene_base_keeps_stable_structure_and_lighting_defaults_to_relight() -> None:
    base = SceneBase(
        scene_id="广播站",
        structure="地下矩形房间，北墙双扇门，东侧控制台",
        furnishings=("控制台", "两把转椅"),
    )

    decision = plan_scene_state(
        base=base,
        state_id="广播站_夜",
        change=SceneStateChange(lighting="深夜冷蓝顶灯"),
        consecutive_shots=2,
    )

    assert decision.reuse_base is True
    assert decision.relight is True
    assert decision.materialize_state is False
    assert decision.state.base_scene_id == "广播站"
    assert decision.state.structure == base.structure
    assert decision.state.furnishings == base.furnishings


def test_structure_or_furnishing_change_creates_scene_state() -> None:
    base = SceneBase(
        scene_id="广播站",
        structure="地下矩形房间，北墙双扇门",
        furnishings=("控制台",),
    )

    decision = plan_scene_state(
        base=base,
        state_id="广播站_爆炸后",
        change=SceneStateChange(
            structure_delta="北墙破开一个洞",
            furnishing_delta=("控制台倾倒", "地面散落电缆"),
        ),
        consecutive_shots=1,
    )

    assert decision.reuse_base is False
    assert decision.relight is False
    assert decision.materialize_state is True
    assert decision.state.structure_delta == "北墙破开一个洞"
    assert decision.state.furnishing_delta == ("控制台倾倒", "地面散落电缆")


def test_long_or_narratively_critical_lighting_state_can_be_materialized() -> None:
    base = SceneBase(scene_id="走廊", structure="狭长直走廊")

    long_state = plan_scene_state(
        base=base,
        state_id="走廊_停电",
        change=SceneStateChange(lighting="红色应急灯"),
        consecutive_shots=3,
    )
    key_state = plan_scene_state(
        base=base,
        state_id="走廊_黎明",
        change=SceneStateChange(lighting="黎明逆光"),
        consecutive_shots=1,
        narrative_key=True,
    )

    assert long_state.materialize_state is True
    assert key_state.materialize_state is True
    assert long_state.relight is True
    assert key_state.relight is True


def test_anchor_pack_only_exposes_requested_views_to_reference_plan(tmp_path: Path) -> None:
    pack = SceneAnchorPack(
        scene_id="广播站",
        state_id="广播站_爆炸后",
        anchors=(
            SceneAnchor(view="master", asset_path="assets/scenes/广播站/master.png"),
            SceneAnchor(view="reverse", asset_path="assets/scenes/广播站/reverse_master.png"),
            SceneAnchor(view="spatial", asset_path="assets/scenes/广播站/spatial_layout.png"),
        ),
    )

    candidates = pack.reference_candidates(required_views={"reverse"})

    assert [candidate.reference_id for candidate in candidates] == [
        "scene:广播站:广播站_爆炸后:reverse"
    ]
    assert candidates[0].kind is ReferenceKind.SCENE_STATE
    assert candidates[0].asset_path.endswith("reverse_master.png")
