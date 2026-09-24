from __future__ import annotations

import json
import os
import time
from collections.abc import Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from uuid import uuid4

from .models import CharacterVisualBible, CharacterVisualWorkspace
from .casting_models import CastingRevision
from novelvideo.production_workflow import production_workflow_project_lock


class CharacterVisualWorkspaceStore:
    """Project-local persistence for provenance-aware character visual state."""

    filename = "character_visual_workspaces.json"
    lock_timeout_seconds = 10.0
    stale_lock_seconds = 60.0

    def __init__(self, project_dir: str | Path, *, state_dir: str | Path | None = None):
        self.project_dir = Path(project_dir)
        self.state_dir = Path(state_dir) if state_dir is not None else self.project_dir / "state"
        self.path = self.project_dir / "state" / self.filename
        self.lock_path = self.path.with_name(f"{self.path.name}.lock")

    @contextmanager
    def _exclusive_write_lock(self) -> Iterator[None]:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        token = uuid4().hex
        deadline = time.monotonic() + self.lock_timeout_seconds

        while True:
            try:
                descriptor = os.open(
                    self.lock_path,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                )
            except FileExistsError:
                try:
                    lock_age = time.time() - self.lock_path.stat().st_mtime
                except FileNotFoundError:
                    continue
                if lock_age >= self.stale_lock_seconds:
                    try:
                        self.lock_path.unlink()
                    except FileNotFoundError:
                        pass
                    continue
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        f"timed out waiting for character visual store lock: "
                        f"{self.lock_path}"
                    )
                time.sleep(0.025)
                continue

            try:
                os.write(descriptor, token.encode("ascii"))
            finally:
                os.close(descriptor)
            break

        try:
            yield
        finally:
            try:
                if self.lock_path.read_text(encoding="ascii") == token:
                    self.lock_path.unlink(missing_ok=True)
            except FileNotFoundError:
                pass

    def _read_all(self) -> dict[str, dict]:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def _write_all(self, payload: dict[str, dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_name(f"{self.path.name}.{uuid4().hex}.tmp")
        temp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        try:
            for attempt in range(4):
                try:
                    os.replace(temp, self.path)
                    return
                except PermissionError:
                    if attempt == 3:
                        raise
                    time.sleep(0.025 * (attempt + 1))
        finally:
            temp.unlink(missing_ok=True)

    def get(self, character_id: str) -> CharacterVisualWorkspace | None:
        raw = self._read_all().get(character_id)
        return CharacterVisualWorkspace.model_validate(raw) if isinstance(raw, dict) else None

    def save(self, workspace: CharacterVisualWorkspace) -> CharacterVisualWorkspace:
        with production_workflow_project_lock(self.state_dir), self._exclusive_write_lock():
            payload = self._read_all()
            payload[workspace.character_id] = workspace.model_dump(mode="json")
            self._write_all(payload)
        return workspace

    def save_many(
        self, workspaces: Sequence[CharacterVisualWorkspace]
    ) -> list[CharacterVisualWorkspace]:
        items = list(workspaces)
        character_ids = [workspace.character_id for workspace in items]
        if len(set(character_ids)) != len(character_ids):
            raise ValueError("duplicate character_id in workspace batch")
        if not items:
            return []

        with production_workflow_project_lock(self.state_dir), self._exclusive_write_lock():
            payload = self._read_all()
            for workspace in items:
                existing_raw = payload.get(workspace.character_id)
                if isinstance(existing_raw, dict):
                    existing = CharacterVisualWorkspace.model_validate(existing_raw)
                    workspace = workspace.model_copy(
                        update={
                            "selected_proposal_id": existing.selected_proposal_id,
                            "visual_bible": existing.visual_bible,
                            "casting_revision": existing.casting_revision,
                            "identity_casting_revisions": existing.identity_casting_revisions,
                        }
                    )
                payload[workspace.character_id] = workspace.model_dump(mode="json")
            self._write_all(payload)
        return items

    def mutate_casting_revision(self, character_id: str, *, identity_id: str | None,
                                expected_revision: str | None,
                                revision: CastingRevision) -> CharacterVisualWorkspace:
        """Check and replace one stage within the same locked read/write transaction."""
        if (revision.character_id, revision.identity_id) != (character_id, identity_id):
            raise ValueError("casting revision ownership mismatch")
        with production_workflow_project_lock(self.state_dir), self._exclusive_write_lock():
            payload = self._read_all()
            workspace = CharacterVisualWorkspace.model_validate(payload[character_id])
            current = (workspace.casting_revision if identity_id is None
                       else workspace.identity_casting_revisions.get(identity_id))
            if (current.revision_id if current else None) != expected_revision:
                raise ValueError("casting revision conflict")
            if identity_id is None:
                workspace.casting_revision = revision.model_copy(deep=True)
            else:
                workspace.identity_casting_revisions[identity_id] = revision.model_copy(deep=True)
            payload[character_id] = workspace.model_dump(mode="json")
            self._write_all(payload)
            return workspace

    def get_confirmed_bible(self, character_id: str) -> CharacterVisualBible | None:
        workspace = self.get(character_id)
        bible = workspace.visual_bible if workspace else None
        return bible if bible and bible.status == "confirmed" else None
