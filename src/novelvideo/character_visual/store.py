from __future__ import annotations

import json
import os
import time
from collections.abc import Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from uuid import uuid4

import portalocker

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
    def _recovered_project_lock(self):
        from .casting_recovery import recover_casting_adoptions
        with production_workflow_project_lock(self.state_dir):
            recover_casting_adoptions(self.project_dir, self.state_dir)
            yield

    @contextmanager
    def _exclusive_write_lock(self) -> Iterator[None]:
        # Kernel locks release on process death. A create-exclusive marker left
        # by a killed writer must not prevent immediate journal recovery/retry.
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        with portalocker.Lock(self.lock_path, mode='a+b', timeout=self.lock_timeout_seconds):
            yield

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
        with self._recovered_project_lock():
            raw = self._read_all().get(character_id)
            return CharacterVisualWorkspace.model_validate(raw) if isinstance(raw, dict) else None

    def _assert_bible_unchanged(self, previous, incoming):
        """A stale editor cannot separate a casting portrait from its bible."""
        if not isinstance(previous, dict):
            return
        from .casting_recovery import assert_legacy_portrait_mutation_allowed
        changed = []
        if previous.get('visual_bible') != incoming.get('visual_bible'):
            changed.append(None)
        before = previous.get('identity_visual_bibles', {})
        after = incoming.get('identity_visual_bibles', {})
        changed.extend(identity for identity in before.keys() | after.keys() if before.get(identity) != after.get(identity))
        for identity in changed:
            try:
                assert_legacy_portrait_mutation_allowed(self.project_dir, self.state_dir, incoming['character_id'], identity)
            except ValueError as exc:
                raise ValueError('CHARACTER_CASTING_REQUIRED: casting bible must change with explicit portrait adoption') from exc

    def save(self, workspace: CharacterVisualWorkspace) -> CharacterVisualWorkspace:
        with self._recovered_project_lock(), self._exclusive_write_lock():
            payload = self._read_all()
            incoming = workspace.model_dump(mode="json")
            self._assert_bible_unchanged(payload.get(workspace.character_id), incoming)
            payload[workspace.character_id] = incoming
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

        with self._recovered_project_lock(), self._exclusive_write_lock():
            payload = self._read_all()
            for workspace in items:
                existing_raw = payload.get(workspace.character_id)
                if isinstance(existing_raw, dict):
                    existing = CharacterVisualWorkspace.model_validate(existing_raw)
                    preserve_draft = (bool(existing.casting_limitation_reasons)
                        or bool(existing.selected_proposal_id) or existing.visual_bible is not None
                        or workspace.casting_revision is None)
                    workspace = workspace.model_copy(
                        update={
                            **({"profile": existing.profile, "design_proposals": existing.design_proposals}
                               if preserve_draft and existing.casting_revision is not None else {}),
                            "selected_proposal_id": existing.selected_proposal_id,
                            "visual_bible": existing.visual_bible,
                            "casting_revision": existing.casting_revision if preserve_draft else workspace.casting_revision,
                            "identity_casting_revisions": existing.identity_casting_revisions,
                            "identity_design_proposals": existing.identity_design_proposals,
                            "identity_selected_proposal_ids": existing.identity_selected_proposal_ids,
                            "identity_visual_bibles": existing.identity_visual_bibles,
                            "casting_limitation_reasons": existing.casting_limitation_reasons,
                            "casting_design_budgets": existing.casting_design_budgets,
                        }
                    )
                payload[workspace.character_id] = workspace.model_dump(mode="json")
            self._write_all(payload)
        return items

    def mutate(self, character_id: str, *, identity_id: str | None,
               expected_revision: str | None, change) -> CharacterVisualWorkspace:
        """Read, compare, mutate and validate in one synchronous transaction."""
        with self._recovered_project_lock(), self._exclusive_write_lock():
            payload = self._read_all()
            workspace = CharacterVisualWorkspace.model_validate(payload[character_id])
            current = (workspace.casting_revision if identity_id is None
                       else workspace.identity_casting_revisions.get(identity_id))
            if (current.revision_id if current else None) != expected_revision:
                raise ValueError("casting revision conflict")
            change(workspace)
            workspace = CharacterVisualWorkspace.model_validate(workspace.model_dump(mode="json"))
            if workspace.character_id != character_id:
                raise ValueError("workspace ownership mismatch")
            incoming = workspace.model_dump(mode="json")
            self._assert_bible_unchanged(payload.get(character_id), incoming)
            payload[character_id] = incoming
            self._write_all(payload)
            return workspace

    def mutate_casting_revision(self, character_id: str, *, identity_id: str | None,
                                expected_revision: str | None,
                                revision: CastingRevision) -> CharacterVisualWorkspace:
        """Check and replace one stage within the same locked read/write transaction."""
        if (revision.character_id, revision.identity_id) != (character_id, identity_id):
            raise ValueError("casting revision ownership mismatch")
        with self._recovered_project_lock(), self._exclusive_write_lock():
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
