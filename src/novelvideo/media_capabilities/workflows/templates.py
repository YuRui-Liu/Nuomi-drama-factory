"""Explicit templates for the two supplied API graph contracts."""

from __future__ import annotations
import json
from pathlib import Path
from .models import WorkflowManifest, WorkflowError
from .graph import validate_bindings


def configure_template(directory: str | Path, template: str) -> WorkflowManifest:
    root = Path(directory)
    data = json.loads((root / "manifest.json").read_text(encoding="utf-8-sig"))
    if data.get("status") != "draft":
        raise WorkflowError("template_requires_draft")
    if template == "acestep":
        data["inputs"] = {
            "tags": {
                "type": "string",
                "required": True,
                "min_length": 1,
                "max_length": 4000,
            },
            "lyrics": {"type": "string", "default": "[Instrumental]"},
            "bpm": {"type": "integer", "default": 75, "minimum": 30, "maximum": 300},
            "duration": {
                "type": "number",
                "default": 30,
                "minimum": 0.01,
                "maximum": 600,
            },
            "seed": {"type": "string", "required": True, "format": "uint64"},
        }
        fields = [
            ("tags", "94", "tags"),
            ("lyrics", "94", "lyrics"),
            ("bpm", "203", "value"),
            ("duration", "205", "value"),
            ("seed", "109", "value"),
        ]
        data["bindings"] = [
            {
                "source": name,
                "targets": [{"node_id": node, "field": field, "encoding": "string"}],
            }
            for name, node, field in fields
        ]
        data["outputs"] = {
            "audio": {"node_id": "107", "kind": "audio", "min_count": 1, "max_count": 1}
        }
        data["integrations"] = [
            {
                "capability": "music.generate",
                "scene": "music_desk",
                "adapter": {"id": "acestep-music", "version": 1},
            }
        ]
        data["media_type"] = "audio"
    elif template == "h3-reference":
        data["inputs"] = {
            "timeline_data": {
                "type": "object",
                "required": True,
                "additional_properties": True,
            }
        }
        data["bindings"] = [
            {
                "source": "timeline_data",
                "targets": [
                    {"node_id": "12", "field": "timeline_data", "encoding": "json"}
                ],
            }
        ]
        data["outputs"] = {
            "video": {"node_id": "7", "kind": "video", "min_count": 1, "max_count": 1}
        }
        data["integrations"] = [
            {
                "capability": "video.ref2va",
                "scene": "narrative_group",
                "adapter": {"id": "h3-reference", "version": 1},
            }
        ]
        data["media_type"] = "video"
    else:
        raise WorkflowError("unknown_template", template)
    data["status"] = "active"
    manifest = WorkflowManifest.model_validate(data)
    graph = json.loads((root / manifest.source.path).read_text(encoding="utf-8-sig"))
    validate_bindings(manifest, graph)
    from .builtin_adapters import validate_builtin_graph

    validate_builtin_graph(template, graph)
    (root / "manifest.json").write_text(
        json.dumps(manifest.snapshot(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return manifest
