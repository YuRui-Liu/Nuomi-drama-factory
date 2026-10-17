"""Public node operations. Availability is explicit until an adapter is installed."""

from __future__ import annotations

from novelvideo.freezone.skill_registry import list_skills

# (category, accepted media, produced media, operations). Do not remove blocked
# operations to make an acceptance report appear complete.
_SPECS = {
    "ideationNode": ("generation", ("text", "json"), ("json",), ("generate",)),
    "screenplayNode": ("generation", ("text", "json"), ("json",), ("plan", "inspect", "review", "rewrite")),
    "narrativeGroupStoryboardNode": ("input", ("json",), ("json",), ("validate",)),
    "directorWorkspaceNode": ("generation", ("text", "json"), ("json",),
                               ("prepare", "preview_handoff", "inspect", "save_blocking", "compile_blocking", "edit_shot", "edit_structure", "render_groups", "retry_render_groups", "produce", "retry_production")),
    "characterVoiceNode": ("generation", ("text", "audio", "json"), ("audio", "json"), ("design", "prepare_design")),
    "uploadNode": ("input", (), ("image", "video", "audio"), ("validate", "upload")),
    "imageNode": ("generation", ("image", "text"), ("image",), ("edit",)),
    "imageGenNode": ("generation", ("image", "text"), ("image",), ("generate", "edit")),
    "exportImageNode": ("transform", ("image",), ("image",), ("export",)),
    "beatContextNode": ("input", (), ("text", "image", "json"), ("validate",)),
    "textAnnotationNode": ("input", ("text",), ("text",), ("validate",)),
    "groupNode": ("container", (), (), ("validate",)),
    "storyboardNode": ("transform", ("image",), ("image",), ("split",)),
    "storyboardGenNode": ("generation", ("text", "image"), ("image",), ("generate",)),
    "videoNode": ("generation", ("text", "image", "video", "audio"), ("video",),
                  ("generate", "image_to_video", "keyframes", "reference", "edit", "upscale", "erase", "extract_frames", "separate_audio")),
    "videoDirectorNode": ("generation", ("text", "image", "json"), ("video",), ("generate",)),
    "audioNode": ("generation", ("text", "audio", "json"), ("audio", "json"),
                  ("speech", "music", "prepare_dubbing", "generate_dubbing", "group_dubbing", "audition")),
    "videoStoryNode": ("generation", ("video", "text"), ("text", "json"), ("analyze",)),
    "videoComposeNode": ("transform", ("video", "audio"), ("video",), ("compose",)),
    "musicDeskNode": ("transform", ("video", "audio"), ("audio", "video"), ("edit", "export")),
    "scriptNode": ("generation", ("text", "image", "video", "json"), ("text", "json"), ("generate", "plan_episode")),
    "pano360ViewerNode": ("interactive", ("image",), ("image", "json"), ("view", "capture")),
    "threeDWorldNode": ("interactive", ("image", "text", "json"), ("image", "json"), ("generate", "view", "capture")),
    "skillNode": ("generation", ("text", "image", "json"), ("text", "image", "json"), ("run",)),
}


def capabilities(available: set[tuple[str, str]] | None = None) -> list[dict]:
    installed = available or set()
    result = []
    for kind, (category, inputs, outputs, operations) in _SPECS.items():
        entry = {
            "node_type": kind, "category": category,
            "inputs": [{"name": media, "media_type": media, "cardinality": "multi"} for media in inputs],
            "outputs": [{"name": media, "media_type": media} for media in outputs],
            "default_operation": operations[0],
            "operations": [{"name": op, "available": (kind, op) in installed,
                            "reason": None if (kind, op) in installed else "adapter_not_installed"}
                           for op in operations],
        }
        if kind == "skillNode":
            entry["skills"] = [skill.model_dump() for skill in list_skills()]
        result.append(entry)
    return result


def catalog_by_type() -> dict[str, dict]:
    return {entry["node_type"]: entry for entry in capabilities()}
