"""Opt-in migration with explicit reports; never change legacy defaults."""

import hashlib
from pathlib import Path
from .registry import WorkflowRegistry
from .importer import import_workflow
from .templates import configure_template


def migrate_existing(
    store, sources: dict[str, Path], packages_root: Path
) -> list[dict]:
    settings = store.get_runninghub_workflows()
    registry = WorkflowRegistry(store)
    templates = {"music_acestep": "acestep", "video_minimax_h3_ref": "h3-reference"}
    report = []
    for key, workflow_id in settings.model_dump().items():
        if not isinstance(workflow_id, str) or not workflow_id:
            continue
        entry = {
            "setting": key,
            "workflow_id": workflow_id,
            "status": "legacy_unverified",
        }
        source = sources.get(key)
        if source is None or not Path(source).is_file():
            report.append(entry | {"reason": "api_graph_not_supplied"})
            continue
        if key not in templates:
            report.append(entry | {"reason": "explicit_manifest_required"})
            continue
        digest = hashlib.sha256(Path(source).read_bytes()).hexdigest()
        destination = Path(packages_root) / f"{key}-{workflow_id}-{digest[:12]}"
        try:
            if not destination.exists():
                import_workflow(
                    source,
                    identifier=f"builtin-{templates[key]}",
                    workflow_id=workflow_id,
                    out_dir=destination,
                )
                configure_template(destination, templates[key])
            manifest = registry.register(destination)
            report.append(
                entry
                | {
                    "status": "registered",
                    "id": manifest.id,
                    "version": manifest.version,
                }
            )
        except (ValueError, OSError, RuntimeError) as exc:
            report.append(
                entry
                | {
                    "status": "blocked",
                    "reason": getattr(exc, "code", type(exc).__name__),
                }
            )
    return report
