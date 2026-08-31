"""Filesystem store for immutable screenplay semantic revisions."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
from uuid import uuid4

from novelvideo.screenplay_semantics.models import ScreenplaySemanticRevision


class ScreenplaySemanticActivationConflict(RuntimeError):
    pass


class ScreenplaySemanticStore:
    def __init__(self, output_dir: str | Path) -> None:
        self.root = Path(output_dir) / "screenplay_semantics"

    def _episode_dir(self, episode: int) -> Path:
        return self.root / f"ep{episode:03d}"

    def _revision_path(self, episode: int, revision_id: str) -> Path:
        return self._episode_dir(episode) / "revisions" / f"{revision_id}.json"

    @staticmethod
    def _atomic_json(path: Path, payload: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f"{path.name}.{uuid4().hex}.tmp")
        try:
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def save(self, revision: ScreenplaySemanticRevision) -> ScreenplaySemanticRevision:
        path = self._revision_path(revision.episode, revision.revision_id)
        payload = revision.model_dump(mode="json")
        if path.exists():
            current = json.loads(path.read_text(encoding="utf-8"))
            if current != payload:
                raise FileExistsError(f"semantic revision is immutable: {revision.revision_id}")
            return revision
        self._atomic_json(path, payload)
        return revision

    def load(self, episode: int, revision_id: str) -> ScreenplaySemanticRevision | None:
        path = self._revision_path(episode, revision_id)
        if not path.exists():
            return None
        return ScreenplaySemanticRevision.model_validate_json(path.read_text(encoding="utf-8"))

    def list_revisions(self, episode: int) -> tuple[ScreenplaySemanticRevision, ...]:
        directory = self._episode_dir(episode) / "revisions"
        if not directory.exists():
            return ()
        revisions = [
            ScreenplaySemanticRevision.model_validate_json(path.read_text(encoding="utf-8"))
            for path in directory.glob("*.json")
        ]
        return tuple(sorted(revisions, key=lambda item: item.created_at, reverse=True))

    def load_active(self, episode: int) -> ScreenplaySemanticRevision | None:
        pointer = self._episode_dir(episode) / "active.json"
        if not pointer.exists():
            return None
        data = json.loads(pointer.read_text(encoding="utf-8"))
        revision = self.load(episode, str(data["revision_id"]))
        if revision is None:
            return None
        activated_at = datetime.fromisoformat(str(data["activated_at"]))
        return revision.model_copy(
            update={"status": "active", "activated_at": activated_at}
        )

    def activate(
        self,
        episode: int,
        revision_id: str,
        *,
        expected_source_revision: int,
    ) -> ScreenplaySemanticRevision:
        revision = self.load(episode, revision_id)
        if revision is None:
            raise LookupError(f"semantic revision not found: {revision_id}")
        if revision.source_revision != expected_source_revision:
            raise ScreenplaySemanticActivationConflict("source revision changed")
        if not revision.validation_report.passed:
            raise ScreenplaySemanticActivationConflict("validation report did not pass")
        activated_at = datetime.now(timezone.utc)
        self._atomic_json(
            self._episode_dir(episode) / "active.json",
            {
                "revision_id": revision_id,
                "source_revision": expected_source_revision,
                "activated_at": activated_at.isoformat(),
            },
        )
        return revision.model_copy(
            update={"status": "active", "activated_at": activated_at}
        )


__all__ = ["ScreenplaySemanticActivationConflict", "ScreenplaySemanticStore"]
