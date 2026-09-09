"""Atomic publication of canonical character portraits and workflow versions."""

from __future__ import annotations

import io
import os
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from novelvideo.narrative_groups.reference_uploads import validate_reference_image
from novelvideo.utils.safe_paths import resolve_under_root, validate_path_segment

from .slot_ids import character_portrait_slot_id
from .store import ProductionWorkflowStore, production_workflow_project_lock


def _normalized_png(image_bytes: bytes) -> bytes:
    try:
        with Image.open(io.BytesIO(image_bytes)) as image:
            output = io.BytesIO()
            image.convert("RGB").save(output, format="PNG")
    except (OSError, UnidentifiedImageError):
        raise ValueError("character portrait is not a valid image") from None
    return output.getvalue()


def _atomic_restore(path: Path, data: bytes | None) -> None:
    if data is None:
        path.unlink(missing_ok=True)
        return
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.rollback")
    temporary.write_bytes(data)
    os.replace(temporary, path)


def commit_character_portrait_current(
    *,
    state_dir: str | Path,
    project_dir: str | Path,
    character_name: str,
    image_bytes: bytes,
    actor: str,
    source_attempt_id: str | None = None,
) -> Path:
    """Publish one portrait so canonical bytes and workflow current stay aligned."""

    safe_name = validate_path_segment(character_name, label="character name")
    normalized = _normalized_png(image_bytes)
    root = Path(project_dir).resolve()
    character_root = resolve_under_root(root / "assets" / "characters", safe_name)
    canonical_path = resolve_under_root(character_root, "portrait.png")
    version_id = f"portrait-{uuid.uuid4().hex}"
    versions_root = resolve_under_root(character_root, "portrait_versions")
    version_path = resolve_under_root(versions_root, f"{version_id}.png")
    staging_path = resolve_under_root(versions_root, f".{version_id}.stage.png")
    canonical_stage = resolve_under_root(
        character_root, f".portrait.{version_id}.stage.png"
    )
    backup_path: Path | None = None
    workflow_path = Path(state_dir) / "production_workflow.json"
    slot_id = character_portrait_slot_id(safe_name)

    with production_workflow_project_lock(state_dir):
        workflow = ProductionWorkflowStore(workflow_path)
        try:
            existing_slot, _versions = workflow.get_slot(slot_id)
        except KeyError:
            pass
        else:
            if existing_slot.asset_kind != "character_portrait":
                raise RuntimeError(
                    f"production workflow slot {slot_id} has incompatible asset kind"
                )

        workflow_snapshot = workflow.capture_file_snapshot()
        canonical_snapshot = (
            canonical_path.read_bytes() if canonical_path.is_file() else None
        )
        try:
            versions_root.mkdir(parents=True, exist_ok=True)
            staging_path.write_bytes(normalized)
            with staging_path.open("rb") as staged_file:
                os.fsync(staged_file.fileno())
            validate_reference_image(
                staging_path,
                allowed_roots=(character_root,),
                expected_mime="image/png",
            )
            os.replace(staging_path, version_path)
            if canonical_snapshot is not None:
                backup_path = canonical_path.with_name(
                    f"portrait_{datetime.now():%Y%m%d%H%M%S%f}.png"
                )
                shutil.copy2(canonical_path, backup_path)
            _slot, version, _event = workflow.register_candidate_version(
                slot_id=slot_id,
                asset_kind="character_portrait",
                version_id=version_id,
                asset_path=version_path.relative_to(root).as_posix(),
                source_attempt_id=source_attempt_id,
                qc_passed=True,
                generation_metadata={
                    "canonical_path": canonical_path.relative_to(root).as_posix()
                },
                actor=actor or "system",
                at=datetime.now(timezone.utc),
            )
            workflow.adopt_version(
                slot_id=slot_id,
                version_id=version.version_id,
                actor=actor or "system",
                reason="canonical character portrait updated",
                at=datetime.now(timezone.utc),
            )
            shutil.copy2(version_path, canonical_stage)
            with canonical_stage.open("rb") as staged_file:
                os.fsync(staged_file.fileno())
            os.replace(canonical_stage, canonical_path)
        except Exception:
            workflow.restore_file_snapshot(workflow_snapshot)
            _atomic_restore(canonical_path, canonical_snapshot)
            version_path.unlink(missing_ok=True)
            if backup_path is not None:
                backup_path.unlink(missing_ok=True)
            raise
        finally:
            staging_path.unlink(missing_ok=True)
            canonical_stage.unlink(missing_ok=True)
    return canonical_path
