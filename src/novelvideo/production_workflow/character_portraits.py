"""Atomic publication of canonical character portraits and workflow versions."""

from __future__ import annotations

import hashlib
import io
import os
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from novelvideo.narrative_groups.reference_uploads import validate_reference_image

from .models import AdoptionStatus, AssetOrigin
from .slot_ids import character_portrait_slot_id
from .store import ProductionWorkflowStore, production_workflow_project_lock


def validate_character_name(character_name: str) -> str:
    """Return one safe on-disk character directory name."""

    safe_name = str(character_name or "").strip()
    if (
        not safe_name
        or safe_name in {".", ".."}
        or "/" in safe_name
        or "\\" in safe_name
        or ":" in safe_name
        or any(ord(character) < 32 or ord(character) == 127 for character in safe_name)
    ):
        raise ValueError("invalid character name")
    return safe_name


def _resolve_under_root(root: Path, relative: str | Path) -> Path:
    relative_path = Path(relative)
    if relative_path.is_absolute():
        raise ValueError("character portrait path is outside its controlled root")
    resolved_root = root.resolve(strict=False)
    candidate = (resolved_root / relative_path).resolve(strict=False)
    try:
        candidate.relative_to(resolved_root)
    except ValueError:
        raise ValueError("character portrait path is outside its controlled root") from None
    return candidate


def _character_paths(project_dir: str | Path, character_name: str) -> tuple[Path, Path, Path]:
    root = Path(project_dir).resolve(strict=False)
    characters_root = _resolve_under_root(root, Path("assets") / "characters")
    character_root = _resolve_under_root(characters_root, validate_character_name(character_name))
    canonical_path = _resolve_under_root(character_root, "portrait.png")
    versions_root = _resolve_under_root(character_root, "portrait_versions")
    return root, canonical_path, versions_root


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


def _validated_current_path(
    *,
    workflow: ProductionWorkflowStore,
    project_dir: Path,
    character_name: str,
) -> tuple[Path, Path] | None:
    root, canonical_path, _versions_root = _character_paths(project_dir, character_name)
    character_root = canonical_path.parent
    try:
        slot, versions = workflow.get_slot(character_portrait_slot_id(character_name))
    except KeyError:
        return None
    if slot.asset_kind != "character_portrait" or not slot.current_version_id:
        return None
    current = versions.get(slot.current_version_id)
    if current is None or current.adoption_status not in {
        AdoptionStatus.PROVISIONAL,
        AdoptionStatus.ADOPTED,
    }:
        return None
    try:
        current_path = _resolve_under_root(root, current.asset_path)
        validate_reference_image(
            current_path,
            allowed_roots=(character_root,),
            expected_mime="image/png",
        )
    except (OSError, ValueError):
        return None
    return current_path, canonical_path


def reconcile_character_portrait_canonical(
    *,
    workflow: ProductionWorkflowStore,
    project_dir: str | Path,
    character_name: str,
) -> bool:
    """Repair the compatibility mirror from the immutable workflow current."""

    safe_name = validate_character_name(character_name)
    resolved = _validated_current_path(
        workflow=workflow,
        project_dir=Path(project_dir),
        character_name=safe_name,
    )
    if resolved is None:
        return False
    current_path, canonical_path = resolved
    if current_path == canonical_path:
        return True
    try:
        current_sha = hashlib.sha256(current_path.read_bytes()).hexdigest()
        canonical_sha = (
            hashlib.sha256(canonical_path.read_bytes()).hexdigest()
            if canonical_path.is_file()
            else ""
        )
    except OSError:
        canonical_sha = ""
        current_sha = hashlib.sha256(current_path.read_bytes()).hexdigest()
    if canonical_sha != current_sha:
        canonical_path.parent.mkdir(parents=True, exist_ok=True)
        stage = canonical_path.with_name(f".portrait.reconcile-{uuid.uuid4().hex}.png")
        try:
            shutil.copy2(current_path, stage)
            with stage.open("rb") as staged_file:
                os.fsync(staged_file.fileno())
            os.replace(stage, canonical_path)
        finally:
            stage.unlink(missing_ok=True)
    return True


def materialize_legacy_character_portrait(
    *,
    workflow: ProductionWorkflowStore,
    project_dir: str | Path,
    character_name: str,
) -> bool:
    """Record a canonical legacy portrait through an immutable version copy."""

    safe_name = validate_character_name(character_name)
    root, canonical_path, versions_root = _character_paths(project_dir, safe_name)
    character_root = canonical_path.parent
    slot_id = character_portrait_slot_id(safe_name)
    existing_slot = None
    try:
        existing_slot, existing_versions = workflow.get_slot(slot_id)
    except KeyError:
        pass
    else:
        if existing_slot.asset_kind != "character_portrait":
            return False
        current = existing_versions.get(str(existing_slot.current_version_id or ""))
        if (
            current is None
            or current.slot_id != slot_id
            or current.adoption_status
            not in {AdoptionStatus.PROVISIONAL, AdoptionStatus.ADOPTED}
        ):
            return False
        try:
            current_path = _resolve_under_root(root, current.asset_path)
        except ValueError:
            return False
        if current_path != canonical_path:
            return True
    try:
        validated = validate_reference_image(
            canonical_path,
            allowed_roots=(character_root,),
            expected_mime="image/png",
        )
    except (OSError, ValueError):
        return False
    version_path = _resolve_under_root(
        versions_root, f"legacy-{validated.sha256[:20]}.png"
    )
    stage = _resolve_under_root(
        versions_root, f".legacy-{validated.sha256[:20]}-{uuid.uuid4().hex}.stage.png"
    )
    created = False
    workflow_snapshot = workflow.capture_file_snapshot()
    try:
        versions_root.mkdir(parents=True, exist_ok=True)
        if version_path.is_file():
            existing = validate_reference_image(
                version_path,
                allowed_roots=(character_root,),
                expected_mime="image/png",
            )
            if existing.sha256 != validated.sha256:
                raise RuntimeError("legacy portrait history content mismatch")
        else:
            shutil.copy2(canonical_path, stage)
            validate_reference_image(
                stage,
                allowed_roots=(character_root,),
                expected_mime="image/png",
            )
            os.replace(stage, version_path)
            created = True
        relative_version_path = version_path.relative_to(root).as_posix()
        if existing_slot is None:
            workflow.materialize_legacy_current(
                slot_id=slot_id,
                asset_kind="character_portrait",
                asset_path=relative_version_path,
            )
        else:
            _slot, version, _event = workflow.register_candidate_version(
                slot_id=slot_id,
                asset_kind="character_portrait",
                version_id=f"legacy-migrated-{uuid.uuid4().hex}",
                asset_path=relative_version_path,
                source_attempt_id=None,
                qc_passed=True,
                generation_metadata={"migrated_from": current.asset_path},
                actor="legacy-migration",
                at=datetime.now(timezone.utc),
                origin=AssetOrigin.LEGACY_IMPORT,
            )
            workflow.adopt_version(
                slot_id=slot_id,
                version_id=version.version_id,
                actor="legacy-migration",
                reason="migrate mutable legacy portrait current",
                at=datetime.now(timezone.utc),
            )
    except Exception:
        workflow.restore_file_snapshot(workflow_snapshot)
        if created:
            version_path.unlink(missing_ok=True)
        raise
    finally:
        stage.unlink(missing_ok=True)
    return True


def commit_character_portrait_current(
    *,
    state_dir: str | Path,
    project_dir: str | Path,
    character_name: str,
    image_bytes: bytes,
    actor: str,
    source_attempt_id: str | None = None,
    origin: AssetOrigin = AssetOrigin.GENERATED,
) -> Path:
    """Publish one portrait so canonical bytes and workflow current stay aligned."""

    safe_name = validate_character_name(character_name)
    normalized = _normalized_png(image_bytes)
    root, canonical_path, versions_root = _character_paths(project_dir, safe_name)
    character_root = canonical_path.parent
    version_id = f"portrait-{uuid.uuid4().hex}"
    version_path = _resolve_under_root(versions_root, f"{version_id}.png")
    staging_path = _resolve_under_root(versions_root, f".{version_id}.stage.png")
    canonical_stage = _resolve_under_root(
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
            reconcile_character_portrait_canonical(
                workflow=workflow,
                project_dir=root,
                character_name=safe_name,
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
                origin=origin,
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
