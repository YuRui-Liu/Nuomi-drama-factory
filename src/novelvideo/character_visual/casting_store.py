"""Durable generate-only candidates. No operation publishes a current portrait."""
from __future__ import annotations

import io
import json
import os
from pathlib import Path
from uuid import uuid4

from PIL import Image

from novelvideo.production_workflow import production_workflow_project_lock
from .casting_compiler import snapshot_digest
from .casting_models import CastingCandidate


class CastingCandidateStore:
    def __init__(self, project_dir: str | Path, *, state_dir: str | Path, project_id: str):
        self.project_dir = Path(project_dir).absolute()
        self.state_dir = Path(state_dir)
        self.project_id = project_id
        self.path = self.state_dir / "casting_candidates.json"

    def _read(self) -> dict:
        if not self.path.exists():
            return {}
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("invalid casting candidate store")
        return data

    def _write(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.{uuid4().hex}.tmp")
        try:
            temporary.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)

    def _decode(self, raw: dict) -> CastingCandidate:
        candidate = CastingCandidate.model_validate(raw)
        if candidate.project_id != self.project_id:
            raise ValueError("candidate project mismatch")
        snapshot = candidate.snapshot
        if snapshot.snapshot_hash != snapshot_digest(snapshot.model_dump(mode="json", exclude={"snapshot_hash"})):
            raise ValueError("candidate snapshot hash mismatch")
        return candidate

    def get(self, candidate_id: str) -> CastingCandidate | None:
        raw = self._read().get(candidate_id)
        return self._decode(raw) if raw is not None else None

    def list_candidates(self, character_id: str, identity_id: str | None = None) -> list[CastingCandidate]:
        return [candidate for raw in self._read().values()
                if (candidate := self._decode(raw)).character_id == character_id and candidate.identity_id == identity_id]

    def create_pending(self, candidate: CastingCandidate) -> CastingCandidate:
        raw = candidate.model_dump(mode="json")
        candidate = self._decode(raw)
        if candidate.generation_status != "queued" or candidate.asset_path or candidate.review_status != "not_started" or candidate.error or candidate.generation_metadata:
            raise ValueError("new candidate must be pending")
        with production_workflow_project_lock(self.state_dir):
            data = self._read()
            if candidate.candidate_id in data:
                existing = self._decode(data[candidate.candidate_id])
                immutable = {"candidate_id", "project_id", "character_id", "identity_id", "snapshot", "task_id", "requested_model"}
                if existing.model_dump(include=immutable) != candidate.model_dump(include=immutable):
                    raise ValueError("candidate id already bound to different input")
                return existing
            data[candidate.candidate_id] = raw
            self._write(data)
        return self._decode(raw)

    def claim_generation(self, candidate_id: str, *, task_id: str, generation_metadata: dict | None = None) -> bool:
        with production_workflow_project_lock(self.state_dir):
            data = self._read()
            candidate = self._decode(data[candidate_id])
            if candidate.task_id != task_id:
                raise ValueError("candidate task mismatch")
            if candidate.generation_status != "queued":
                return False
            updated = candidate.model_dump(mode="json")
            updated.update(generation_status="running", generation_metadata=generation_metadata or {})
            data[candidate_id] = self._decode(updated).model_dump(mode="json")
            self._write(data)
            return True

    def safe_path(self, path: str | Path) -> Path:
        path = Path(path)
        if not path.is_absolute():
            path = self.project_dir / path
        if ".." in path.parts or not path.is_relative_to(self.project_dir):
            raise ValueError("candidate path outside project")
        if any(part.is_symlink() for part in (path, *path.parents)):
            raise ValueError("symlink candidate path forbidden")
        return path

    def output_path(self, candidate_id: str) -> Path:
        # Hash rather than interpolate untrusted IDs into filesystem names.
        name = snapshot_digest({"project": self.project_id, "candidate": candidate_id})
        return self.safe_path(self.project_dir / "assets" / "casting_candidates" / name / "generated.png")

    def complete_generation(self, candidate_id: str, asset_path: str | Path) -> CastingCandidate:
        with production_workflow_project_lock(self.state_dir):
            data = self._read()
            candidate = self._decode(data[candidate_id])
            if candidate.generation_status == "succeeded":
                return candidate
            if candidate.generation_status != "running":
                raise ValueError("only running candidates can complete")
            source = self.safe_path(asset_path)
            try:
                image_bytes = source.read_bytes()
                with Image.open(io.BytesIO(image_bytes)) as image:
                    image.verify()
            except (OSError, ValueError, SyntaxError) as exc:
                raise ValueError("candidate output must be a valid image") from exc
            destination = self.safe_path(self.output_path(candidate_id).with_name("candidate.png"))
            destination.parent.mkdir(parents=True, exist_ok=True)
            # A crash after writing bytes can be safely recovered; never replace them.
            try:
                with destination.open("xb") as handle:
                    handle.write(image_bytes)
                    handle.flush()
                    os.fsync(handle.fileno())
            except FileExistsError:
                if destination.read_bytes() != image_bytes:
                    raise ValueError("immutable candidate image conflict")
            completed = candidate.model_copy(update={"asset_path": str(destination), "generation_status": "succeeded", "error": None})
            data[candidate_id] = completed.model_dump(mode="json")
            self._write(data)
            return self._decode(data[candidate_id])

    def fail_generation(self, candidate_id: str, error: str) -> CastingCandidate:
        with production_workflow_project_lock(self.state_dir):
            data = self._read()
            candidate = self._decode(data[candidate_id])
            if candidate.generation_status in {"succeeded", "failed"}:
                return candidate
            candidate = candidate.model_copy(update={"generation_status": "failed", "error": error or "generation failed"})
            data[candidate_id] = candidate.model_dump(mode="json")
            self._write(data)
            return self._decode(data[candidate_id])
