"""Version-bound, optimistic publication of read-only identity image QC."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any
import uuid
from datetime import datetime, timezone

from novelvideo.character_visual.identity_sheet import IdentitySheetQualityReport
from novelvideo.narrative_groups.reference_uploads import validate_reference_image
from novelvideo.production_workflow import ProductionWorkflowStore, production_workflow_project_lock
from novelvideo.production_workflow.character_portraits import validate_character_name
from novelvideo.production_workflow.slot_ids import character_state_slot_id


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _target(ctx, character_name: str, identity_id: str, version_id: str):
    name = validate_character_name(character_name)
    root = Path(ctx.output_dir).resolve()
    directory = root / "assets" / "characters" / name / "identities"
    slot_id = character_state_slot_id(name, identity_id)
    store = ProductionWorkflowStore(Path(ctx.state_dir) / "production_workflow.json")
    slot, versions = store.get_slot(slot_id)
    version = versions.get(version_id)
    if version is None or slot.asset_kind != "character_state":
        raise ValueError("identity version not found")
    relative = Path(version.asset_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("identity asset path is invalid")
    path = root / relative
    path.relative_to(directory)
    # Use project root so symlinks in assets/characters are also rejected.
    validated = validate_reference_image(path, allowed_roots=(root,), expected_mime="image/png")
    if path.parent.name != "versions":
        raise ValueError("recheck requires an immutable identity version")
    canonical = directory / f"{path.parent.parent.name}.png"
    metadata = version.generation_metadata or {}
    if metadata.get("canonical_path") != canonical.relative_to(root).as_posix():
        raise ValueError("identity canonical path does not match version")
    if canonical.is_symlink() or canonical.parent.resolve() != canonical.parent:
        raise ValueError("identity canonical path is redirected")
    return store, slot, version, path, canonical, validated


def prepare_recheck(ctx, *, character_name: str, identity_id: str, version_id: str) -> dict:
    with production_workflow_project_lock(ctx.state_dir):
        _, _, version, _, _, validated = _target(ctx, character_name, identity_id, version_id)
        return {
            "character_name": character_name, "identity_id": identity_id,
            "version_id": version_id, "image_sha256": validated.sha256,
            "version_digest": _digest(version.model_dump(mode="json")),
        }


def read_recheck_image(ctx, target: dict) -> bytes:
    with production_workflow_project_lock(ctx.state_dir):
        _, _, version, path, _, validated = _target(
            ctx, target["character_name"], target["identity_id"], target["version_id"],
        )
        if (validated.sha256 != target["image_sha256"] or
                _digest(version.model_dump(mode="json")) != target["version_digest"]):
            raise ValueError("identity image or version changed since recheck was requested")
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != validated.sha256:
            raise ValueError("identity image changed while reading")
        return data


def recheck_cache(ctx, target: dict, fingerprint: str) -> dict | None:
    with production_workflow_project_lock(ctx.state_dir):
        _, _, version, _, _, validated = _target(
            ctx, target["character_name"], target["identity_id"], target["version_id"],
        )
        history = (version.generation_metadata or {}).get("qc_history", [])
        if not history:
            return None
        last = history[-1]
        if last.get("fingerprint") == fingerprint and last.get("image_sha256") == validated.sha256:
            return {"version_id": version.version_id, "qc_passed": version.qc_passed,
                    "adoption_status": version.adoption_status.value, "quality_report": last["report"]}
        return None


def publish_recheck(ctx, *, target: dict, report: IdentitySheetQualityReport,
                    fingerprint: str, route: dict, actor: str) -> dict:
    with production_workflow_project_lock(ctx.state_dir):
        image = read_recheck_image(ctx, target)
        store, slot, version, _, canonical, _ = _target(
            ctx, target["character_name"], target["identity_id"], target["version_id"],
        )
        snapshot = store.capture_file_snapshot()
        staged = None
        # Only the first usable candidate can become current automatically.
        promote = report.passed and slot.current_version_id is None and version.adoption_status.value == "candidate"
        if promote:
            staged = canonical.with_name(f".{canonical.name}.qc-{uuid.uuid4().hex}.tmp")
        try:
            if staged is not None:
                staged.write_bytes(image)
            version = store.record_quality_recheck(
                slot_id=slot.slot_id, version_id=version.version_id,
                report=report.model_dump(mode="json"), image_sha256=target["image_sha256"],
                fingerprint=fingerprint, route=route, actor=actor, at=datetime.now(timezone.utc),
            )
            if promote:
                os.replace(staged, canonical)
        except BaseException:
            store.restore_file_snapshot(snapshot)
            raise
        finally:
            if staged is not None:
                staged.unlink(missing_ok=True)
        return {"slot_id": slot.slot_id, "version_id": version.version_id,
                "qc_passed": version.qc_passed, "adoption_status": version.adoption_status.value,
                "quality_report": report.model_dump(mode="json"),
                "image_sha256": target["image_sha256"]}
