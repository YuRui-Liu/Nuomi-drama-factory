"""Small admission checks for explicitly managed episode production."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from novelvideo.freezone.orchestration.models import OrchestrationError

from .production_progress import ProgressNotFound, ProductionProgress


_REQUIRED_STAGES = {
    "screenplay": (),
    "assets": ("screenplay",),
    "storyboard": ("screenplay", "assets"),
    "video": ("screenplay", "assets", "storyboard"),
    # Dubbing can be produced alongside video, but the episode script is its source.
    "audio": ("screenplay",),
    "composition": ("screenplay", "assets", "storyboard", "video"),
}

_DIRECTOR_BLOCKING_OPERATIONS = frozenset({
    "prepare", "compile_blocking",
})
_AUDIO_OPERATIONS = frozenset({
    "speech", "music", "prepare_dubbing", "generate_dubbing", "group_dubbing",
})
_VIDEO_OPERATIONS = frozenset({
    "generate", "image_to_video", "reference", "keyframes", "edit", "upscale",
})


def _episode_binding(data: dict[str, Any]) -> int | None:
    value = data.get("episodeNumber", data.get("episode_number"))
    if type(value) is int and value > 0:
        return value
    for key in ("narrativeGroupReference", "narrative_group_reference"):
        reference = data.get(key)
        if isinstance(reference, dict):
            value = reference.get("episodeNumber", reference.get("episode_number"))
            if type(value) is int and value > 0:
                return value
    return None


def classify_target_stage(
    node: dict[str, Any],
    operation: str,
    *,
    episode_number: int,
    asset_design_execution: dict[str, Any] | None = None,
) -> str | None:
    """Return a production stage only for an explicitly episode-bound operation."""
    if not isinstance(node, dict) or not isinstance(node.get("data"), dict):
        return None
    data = node["data"]
    if _episode_binding(data) != episode_number:
        return None
    pair = (node.get("type"), operation)
    if pair in {
        ("ideationNode", "generate"),
        ("screenplayNode", "plan"), ("screenplayNode", "rewrite"),
        ("screenplayNode", "review"),
    }:
        return "screenplay"
    asset_receipt = (isinstance(asset_design_execution, dict)
                     and isinstance(asset_design_execution.get("candidate"), dict))
    explicit_asset = (isinstance(data.get("artifactKind"), str)
                      and data["artifactKind"] in {"asset", "character", "scene", "prop"})
    episode_asset = (
        isinstance(data.get("episodeAssetRequirement"), str)
        and bool(data["episodeAssetRequirement"].strip())
        and data.get("episodeAssetType") in {"character", "scene", "prop"}
    )
    if pair[0] in {"imageGenNode", "imageNode"} and operation in {"generate", "edit"} and (
        asset_receipt or explicit_asset or episode_asset
    ):
        return "assets"
    if pair[0] == "directorWorkspaceNode" and operation in _DIRECTOR_BLOCKING_OPERATIONS:
        return "storyboard"
    if pair == ("scriptNode", "project"):
        return "storyboard"
    if pair == ("scriptNode", "plan_episode"):
        return "storyboard"
    if pair == ("scriptNode", "generate"):
        return "storyboard"
    if pair == ("storyboardGenNode", "generate"):
        return "storyboard"
    if pair in {
        ("narrativeGroupStoryboardNode", "render"),
        ("narrativeGroupStoryboardNode", "retry_render"),
    }:
        return "storyboard"
    if pair in {
        ("narrativeGroupVideoNode", "video"),
        ("narrativeGroupVideoNode", "retry_video"),
        ("videoDirectorNode", "generate"),
    } or pair[0] == "videoNode" and operation in _VIDEO_OPERATIONS:
        return "video"
    if pair == ("videoNode", "separate_audio"):
        return "audio"
    if pair[0] == "audioNode" and operation in _AUDIO_OPERATIONS:
        return "audio"
    if pair in {
        ("episodeComposeNode", "compose"),
        ("videoComposeNode", "compose"),
        ("videoComposeNode", "musicDesk"),
    }:
        return "composition"
    return None


def storyboard_operation_is_planning(node: dict[str, Any], operation: str) -> bool:
    """Whether a storyboard-bound operation only creates or edits plan text."""
    if not isinstance(node, dict):
        return False
    pair = (node.get("type"), operation)
    return pair in {
        ("directorWorkspaceNode", "prepare"),
        ("directorWorkspaceNode", "compile_blocking"),
        ("scriptNode", "generate"),
        ("scriptNode", "plan_episode"),
    }


def composition_requires_audio(
    node: dict[str, Any],
    *,
    nodes: list[dict[str, Any]] | None = None,
    edges: list[dict[str, Any]] | None = None,
) -> bool:
    """Require the audio receipt only when the saved compose request has audio inputs."""
    data = node.get("data") if isinstance(node, dict) else None
    if not isinstance(data, dict):
        return False
    if any(data.get(key) is True for key in ("requiresAudio", "includeDialogue", "addBgm")):
        return True
    audio_keys = {
        "audioUrl", "externalAudioUrl", "audioTracks", "dialogueIds",
        "dialogueTrack", "externalAudio", "audioInputs",
    }
    if any(data.get(key) not in (None, False, "", [], {}) for key in audio_keys):
        return True
    timeline = data.get("draftTimeline")
    if isinstance(timeline, dict):
        tracks = timeline.get("tracks")
        if isinstance(tracks, list) and any(
            isinstance(track, dict) and track.get("kind") == "audio"
            and bool(track.get("clips") or track.get("items"))
            for track in tracks
        ):
            return True
    by_id = {item.get("id"): item for item in (nodes or []) if isinstance(item, dict)}
    target_id = node.get("id")
    for edge in edges or []:
        if not isinstance(edge, dict) or edge.get("target") != target_id:
            continue
        source = by_id.get(edge.get("source"))
        source_data = source.get("data") if isinstance(source, dict) else None
        if isinstance(source_data, dict) and (
            source.get("type") == "audioNode"
            or source_data.get("audioUrl")
            or source_data.get("externalAudioUrl")
        ):
            return True
    return False


def _blocked(stage: str, required_stage: str, status: str, reason: str) -> OrchestrationError:
    return OrchestrationError(
        "production_stage_blocked",
        f"Managed production stage {stage} is blocked by {required_stage}",
        stage=stage,
        required_stage=required_stage,
        status=status,
        reason=reason,
    )


def admit_managed_stage(
    state_root: str | Path,
    output_root: str | Path,
    workspace_scope: dict[str, Any] | None,
    stage: str,
    *,
    requires_audio: bool = False,
    planning_only: bool = False,
) -> bool:
    """Gate a new stage only when an applied episode already opted into progress.

    This intentionally leaves historical/free canvas experiments alone. The
    progress file itself is the opt-in receipt created by `configure` or stage
    completion; this function never creates one.
    """
    if stage not in _REQUIRED_STAGES:
        raise ValueError(f"unknown production stage: {stage!r}")
    scope = workspace_scope
    episode_number = scope.get("episode_number") if isinstance(scope, dict) else None
    if (not isinstance(scope, dict) or scope.get("kind") != "episode"
            or scope.get("status") != "applied" or type(episode_number) is not int
            or episode_number < 1 or not isinstance(scope.get("episode_id"), str)
            or not scope.get("episode_id")):
        return False

    episode_id = f"{episode_number:03d}"
    progress = ProductionProgress(state_root, output_root=output_root)
    try:
        state = progress.get(episode_id, create=False)
    except ProgressNotFound:
        return False

    if planning_only and stage != "storyboard":
        raise ValueError("planning_only is valid only for the storyboard stage")
    required = (["screenplay"] if planning_only else list(_REQUIRED_STAGES[stage]))
    if stage == "composition" and requires_audio:
        required.append("audio")
    if stage == "composition" and state.get("stop_after") == "audio":
        audio_status = state["stages"]["audio"]["status"]
        raise _blocked(stage, "audio", audio_status, "stop_after")
    for required_stage in required:
        state, stale_reason = progress.verify_stage_outputs(episode_id, required_stage)
        item = state["stages"][required_stage]
        if stale_reason:
            raise _blocked(stage, required_stage, item["status"], stale_reason)
        status = item["status"]
        if status != "ready":
            reason = "awaiting_review" if status == "awaiting_review" else (
                "failed" if status == "failed" else "not_ready"
            )
            raise _blocked(stage, required_stage, status, reason)
        if state.get("stop_after") == required_stage:
            raise _blocked(stage, required_stage, status, "stop_after")
    return True


def applied_episode_scope(state_root: str | Path, episode_number: int) -> dict[str, Any] | None:
    """Resolve one trusted applied episode canvas from its project registry."""
    if state_root is None or type(episode_number) is not int or episode_number < 1:
        return None
    from .canvas_workspaces import list_canvases

    matches = [item for item in list_canvases(state_root)
               if isinstance(item, dict) and item.get("kind") == "episode"
               and item.get("status") == "applied"
               and item.get("episode_number") == episode_number
               and isinstance(item.get("episode_id"), str) and item.get("episode_id")]
    if len(matches) != 1:
        return None
    item = matches[0]
    return {
        "canvas_id": item.get("canvas_id"),
        "kind": "episode",
        "episode_id": item["episode_id"],
        "episode_number": episode_number,
        "status": "applied",
    }


def admit_registered_episode_stage(
    state_root: str | Path,
    output_root: str | Path,
    episode_number: int,
    stage: str,
    *,
    requires_audio: bool = False,
    planning_only: bool = False,
) -> bool:
    """Gate a direct episode API only when its registered episode opted in."""
    scope = applied_episode_scope(state_root, episode_number)
    if scope is None:
        return False
    return admit_managed_stage(
        state_root, output_root, scope, stage, requires_audio=requires_audio,
        planning_only=planning_only,
    )


def stage_for_document_path(path: str, episode_number: int) -> str | None:
    """Map only canonical, explicitly episode-qualified production documents."""
    if not isinstance(path, str):
        return None
    parts = path.split("/")
    if len(parts) < 3 or parts[0] != "episodes" or not parts[1].isdigit():
        return None
    try:
        if int(parts[1]) != episode_number:
            return None
    except ValueError:
        return None
    remainder = parts[2:]
    basename = remainder[-1]
    if basename in {"script.md", "screenplay.md"}:
        return "screenplay"
    if basename in {"assets.md", "asset-design.md", "reuse-checklist.md",
                    "characters.md", "scenes.md", "props.md"}:
        return "assets"
    if basename in {"storyboard.md", "shotlist.md", "blocking.md"}:
        return "storyboard"
    if basename in {"video.md", "videos.md"}:
        return "video"
    if basename in {"audio.md", "dubbing.md", "music.md", "voice-design.md"}:
        return "audio"
    if basename in {"composition.md", "final.md"}:
        return "composition"
    if remainder[0] in {"assets", "characters", "scenes", "props"}:
        return "assets"
    if remainder[0] in {"storyboard", "director"}:
        return "storyboard"
    if remainder[0] in {"video", "videos"}:
        return "video"
    if remainder[0] in {"audio", "dubbing", "music"}:
        return "audio"
    if remainder[0] in {"composition", "final"}:
        return "composition"
    return None


def bound_patch_stages(
    canvas: dict[str, Any], operations: list[dict[str, Any]], episode_number: int,
) -> set[str]:
    """Find stages whose explicitly bound inputs are changed by a graph patch."""
    from novelvideo.freezone.orchestration.adapters import NodeAdapter
    from novelvideo.freezone.orchestration.capabilities import catalog_by_type
    from novelvideo.freezone.orchestration.graph import _is_screenplay_image_reference

    nodes = {node.get("id"): node for node in canvas.get("nodes", [])
             if isinstance(node, dict) and isinstance(node.get("id"), str)}
    edges = {edge.get("id"): edge for edge in canvas.get("edges", [])
             if isinstance(edge, dict) and isinstance(edge.get("id"), str)}
    changed: set[str] = set()
    adapter = NodeAdapter(Path("."))
    catalog = catalog_by_type()

    def is_non_execution_edge(edge: dict[str, Any]) -> bool:
        data = edge.get("data")
        if isinstance(data, dict) and data.get("contextLink"):
            return True
        return _is_screenplay_image_reference(edge, nodes, catalog)

    def add(node: dict[str, Any] | None, operation: str | None = None) -> None:
        if not isinstance(node, dict):
            return
        data = node.get("data") if isinstance(node.get("data"), dict) else {}
        op = operation or data.get("operation")
        if not isinstance(op, str):
            try:
                op = adapter.operation(node)
            except (KeyError, TypeError):
                return
        stage = classify_target_stage(node, op, episode_number=episode_number)
        if stage:
            changed.add(stage)

    for item in operations:
        if not isinstance(item, dict):
            continue
        op = item.get("op")
        if op == "add_node":
            node = item.get("node")
            if isinstance(node, dict):
                add(node)
                node_id = node.get("id")
                if isinstance(node_id, str):
                    nodes[node_id] = node
        elif op == "update_node":
            node = nodes.get(item.get("node_id"))
            changed_fields = item.get("data")
            if isinstance(node, dict) and isinstance(changed_fields, dict):
                # Presentation-only edits do not change production inputs.
                if set(changed_fields) - {"displayName", "isSizeManuallyAdjusted", "width", "height"}:
                    updated = {**node, "data": {**(node.get("data") or {}), **changed_fields}}
                    add(node)
                    add(updated)
                else:
                    updated = {**node, "data": {**(node.get("data") or {}), **changed_fields}}
                if isinstance(node.get("id"), str):
                    nodes[node["id"]] = updated
        elif op == "add_edge":
            edge = item.get("edge")
            if isinstance(edge, dict) and not is_non_execution_edge(edge):
                add(nodes.get(edge.get("target")))
                edge_id = edge.get("id")
                if isinstance(edge_id, str):
                    edges[edge_id] = edge
        elif op == "remove_edge":
            edge = edges.get(item.get("edge_id"))
            if isinstance(edge, dict) and not is_non_execution_edge(edge):
                add(nodes.get(edge.get("target")))
            if isinstance(edge, dict):
                edges.pop(edge.get("id"), None)
    return changed


__all__ = [
    "admit_managed_stage",
    "admit_registered_episode_stage",
    "applied_episode_scope",
    "bound_patch_stages",
    "classify_target_stage",
    "composition_requires_audio",
    "stage_for_document_path",
]
