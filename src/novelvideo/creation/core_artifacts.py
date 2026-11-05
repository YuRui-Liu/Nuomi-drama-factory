"""Semantic projection of canvas nodes into the lightweight production view."""

from collections.abc import Mapping
from typing import Any

CORE_KINDS = (
    "idea",
    "screenplay",
    "asset",
    "storyboard",
    "grid",
    "shot",
    "director",
    "dubbing",
    "music",
    "composition",
)
_CORE_KIND_SET = frozenset(CORE_KINDS)

# These node types have one stable production meaning. Ambiguous historical
# image generators are deliberately omitted and need an explicit artifactKind
# or identifying asset/shot reference in their data.
_LEGACY_NODE_KINDS = {
    "ideationNode": "idea",
    "screenplayNode": "screenplay",
    "uploadNode": "asset",
    "characterVoiceNode": "asset",
    "scriptNode": "storyboard",
    "narrativeGroupStoryboardNode": "storyboard",
    "storyboardGenNode": "grid",
    "videoNode": "shot",
    "videoDirectorNode": "director",
    "audioNode": "dubbing",
    "musicDeskNode": "music",
    "videoComposeNode": "composition",
    "episodeComposeNode": "composition",
}


def _record(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def artifact_kind_for_node(node: Mapping[str, Any]) -> str | None:
    """Return the semantic kind for a node, or ``None`` when it is not core.

    Explicit semantics take precedence over legacy type inference. An explicit
    non-core kind (for example ``execution_plan``) is therefore always hidden.
    """

    data = _record(node.get("data"))
    explicit_kind = data.get("artifactKind")
    if isinstance(explicit_kind, str):
        return explicit_kind if explicit_kind in _CORE_KIND_SET else None

    node_type = node.get("type")
    if not isinstance(node_type, str):
        return None
    if node_type == "exportImageNode":
        result_kind = data.get("resultKind")
        if result_kind == "storyboardSplitExport":
            return "shot"
        if result_kind == "storyboardGenOutput":
            return "grid"
        return None
    if node_type == "imageGenNode":
        if any(data.get(key) for key in ("assetId", "episodeAssetId", "identityId", "episodeAssetType")):
            return "asset"
        if any(data.get(key) for key in ("shotId", "segmentId", "storyboardFrameId")):
            return "shot"
        return None
    if node_type == "scriptNode" and not (
        isinstance(data.get("screenplaySourceNodeId"), str)
        and data["screenplaySourceNodeId"].strip()
    ):
        # A standalone script-generation helper is not the storyboard table.
        return None
    return _LEGACY_NODE_KINDS.get(node_type)


def is_core_artifact(node: Mapping[str, Any]) -> bool:
    """Whether ``node`` belongs in the lightweight production canvas."""

    return artifact_kind_for_node(node) is not None
