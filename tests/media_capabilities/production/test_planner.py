from __future__ import annotations

from copy import deepcopy

import pytest

from novelvideo.media_capabilities.production.planner import (
    ProductionCycleError,
    ProductionNodeType,
    ProductionPlanAction,
    ProductionPlanner,
    UnknownProductionNodeType,
)


NODE_TYPES = {
    "video": ProductionNodeType(
        capability="video.i2va",
        implementation="video-primary",
        workflow_version={"id": "h3", "version": 3, "source_sha256": "wf-video"},
        unit_cost=2.5,
    ),
    "tts": ProductionNodeType(
        capability="tts.synthesize",
        implementation="tts-primary",
        workflow_version={"id": "speech", "version": 2, "source_sha256": "wf-tts"},
        unit_cost=0.4,
    ),
    "compose": ProductionNodeType(
        capability="media.compose",
        implementation="ffmpeg-local",
        workflow_version={"id": "compose", "version": 1},
        unit_cost=0.1,
    ),
    "metadata": ProductionNodeType(
        capability="metadata.export",
        implementation="local",
        workflow_version={"id": "metadata", "version": 1},
        unit_cost=0.0,
    ),
}


@pytest.fixture
def planner() -> ProductionPlanner:
    return ProductionPlanner(NODE_TYPES)


@pytest.fixture
def episode_scope() -> dict:
    return {
        "audit_policy": "auto",
        "nodes": [
            {
                "id": "video-1",
                "node_type": "video",
                "inputs": {"shot_image_sha256": "image-a"},
                "params": {"duration": 5},
            },
            {
                "id": "video-2",
                "node_type": "video",
                "inputs": {"shot_image_sha256": "image-b"},
                "params": {"duration": 5},
            },
            {
                "id": "tts-alice",
                "node_type": "tts",
                "inputs": {"text": "hello", "voice_profile_sha256": "voice-a"},
                "params": {"speed": 1.0},
            },
            {
                "id": "tts-bob",
                "node_type": "tts",
                "inputs": {"text": "world", "voice_profile_sha256": "voice-b"},
                "params": {"speed": 1.0},
            },
            {
                "id": "compose",
                "node_type": "compose",
                "depends_on": ["video-1", "video-2", "tts-alice", "tts-bob"],
                "inputs": {"episode": 1},
            },
            {
                "id": "optional-metadata",
                "node_type": "metadata",
                "skip": True,
            },
        ],
    }


def _artifacts_from_preview(preview) -> dict[str, dict[str, str]]:
    return {
        node.node_id: {
            "fingerprint": node.fingerprint,
            "content_sha256": node.fingerprint,
            "status": "succeeded",
            "risk": "low",
        }
        for node in preview.nodes
        if node.action is not ProductionPlanAction.SKIP
    }


def _actions(preview) -> dict[str, ProductionPlanAction]:
    return {node.node_id: node.action for node in preview.nodes}


def test_preview_counts_costs_and_reuses_unchanged_nodes(
    planner: ProductionPlanner,
    episode_scope: dict,
) -> None:
    created = planner.preview(episode_scope, snapshot={}, existing_artifacts={})

    assert [node.node_id for node in created.nodes] == [
        "video-1",
        "video-2",
        "tts-alice",
        "tts-bob",
        "compose",
        "optional-metadata",
    ]
    assert created.counts == {"create": 5, "reuse": 0, "invalidate": 0, "skip": 1}
    assert created.estimated_cost == pytest.approx(5.9)

    reused = planner.preview(
        episode_scope,
        snapshot={},
        existing_artifacts=_artifacts_from_preview(created),
    )

    assert reused.counts == {"create": 0, "reuse": 5, "invalidate": 0, "skip": 1}
    assert reused.estimated_cost == 0


def test_voice_profile_change_invalidates_only_related_audio_and_compose(
    planner: ProductionPlanner,
    episode_scope: dict,
) -> None:
    original = planner.preview(episode_scope, {}, {})
    artifacts = _artifacts_from_preview(original)
    changed = deepcopy(episode_scope)
    changed["nodes"][2]["inputs"]["voice_profile_sha256"] = "voice-a-v2"

    preview = planner.preview(changed, {}, artifacts)

    assert _actions(preview) == {
        "video-1": ProductionPlanAction.REUSE,
        "video-2": ProductionPlanAction.REUSE,
        "tts-alice": ProductionPlanAction.INVALIDATE,
        "tts-bob": ProductionPlanAction.REUSE,
        "compose": ProductionPlanAction.INVALIDATE,
        "optional-metadata": ProductionPlanAction.SKIP,
    }
    assert preview.counts == {"create": 0, "reuse": 3, "invalidate": 2, "skip": 1}
    assert preview.estimated_cost == pytest.approx(0.5)


def test_shot_image_change_invalidates_only_corresponding_video_and_compose(
    planner: ProductionPlanner,
    episode_scope: dict,
) -> None:
    original = planner.preview(episode_scope, {}, {})
    artifacts = _artifacts_from_preview(original)
    changed = deepcopy(episode_scope)
    changed["nodes"][0]["inputs"]["shot_image_sha256"] = "image-a-v2"

    preview = planner.preview(changed, {}, artifacts)

    assert _actions(preview) == {
        "video-1": ProductionPlanAction.INVALIDATE,
        "video-2": ProductionPlanAction.REUSE,
        "tts-alice": ProductionPlanAction.REUSE,
        "tts-bob": ProductionPlanAction.REUSE,
        "compose": ProductionPlanAction.INVALIDATE,
        "optional-metadata": ProductionPlanAction.SKIP,
    }
    assert preview.estimated_cost == pytest.approx(2.6)


def test_topological_order_is_stable_and_cycle_is_rejected(
    planner: ProductionPlanner,
) -> None:
    scope = {
        "nodes": [
            {"id": "independent-b", "node_type": "metadata"},
            {"id": "root", "node_type": "metadata"},
            {
                "id": "child",
                "node_type": "metadata",
                "depends_on": ["root"],
            },
            {"id": "independent-a", "node_type": "metadata"},
        ]
    }

    preview = planner.preview(scope, {}, {})

    assert [node.node_id for node in preview.nodes] == [
        "independent-b",
        "root",
        "child",
        "independent-a",
    ]

    cyclic = deepcopy(scope)
    cyclic["nodes"][1]["depends_on"] = ["child"]
    with pytest.raises(ProductionCycleError, match="root.*child|child.*root"):
        planner.preview(cyclic, {}, {})


def test_unknown_node_type_is_rejected(planner: ProductionPlanner) -> None:
    with pytest.raises(UnknownProductionNodeType, match="shell-command"):
        planner.preview(
            {"nodes": [{"id": "unsafe", "node_type": "shell-command"}]},
            {},
            {},
        )


@pytest.mark.parametrize("policy", ["strict", "balanced", "auto"])
def test_audit_policies_skip_failed_or_high_risk_artifacts(
    planner: ProductionPlanner,
    policy: str,
) -> None:
    scope = {"audit_policy": policy, "nodes": [{"id": "video", "node_type": "video"}]}
    original = planner.preview(scope, {}, {})
    artifacts = _artifacts_from_preview(original)
    artifacts["video"]["status"] = "quality_failed"

    failed = planner.preview(scope, {}, artifacts)
    assert failed.nodes[0].action is ProductionPlanAction.SKIP
    assert failed.nodes[0].reason == "quality_failed"

    artifacts["video"]["status"] = "succeeded"
    artifacts["video"]["risk"] = "high"
    risky = planner.preview(scope, {}, artifacts)
    assert risky.nodes[0].action is ProductionPlanAction.SKIP
    assert risky.nodes[0].reason == "high_risk"
