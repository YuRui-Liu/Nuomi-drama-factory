"""Durable generate-only candidates. No operation publishes a current portrait."""
from __future__ import annotations

import io
import hashlib
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
            with temporary.open("w", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            self._sync_directory(self.path.parent)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _sync_directory(path: Path) -> None:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

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
        if candidate.generation_status != "queued" or candidate.asset_path or candidate.asset_sha256 or candidate.review_status != "not_started" or candidate.error or candidate.generation_metadata:
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
            source = self.safe_path(asset_path)
            if not source.is_relative_to(self.output_path(candidate_id).parent):
                raise ValueError("candidate output must be within its own candidate directory")
            if candidate.generation_status == "succeeded":
                self.read_verified_asset(candidate_id)
                return candidate
            if candidate.generation_status != "running":
                raise ValueError("only running candidates can complete")
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
            self._sync_directory(destination.parent)
            completed = candidate.model_copy(update={"asset_path": str(destination), "asset_sha256": hashlib.sha256(image_bytes).hexdigest(),
                                                      "generation_status": "succeeded", "error": None})
            data[candidate_id] = completed.model_dump(mode="json")
            self._write(data)
            return self._decode(data[candidate_id])

    def read_verified_asset(self, candidate_id: str) -> bytes:
        """Read only the immutable owned asset, bound to its persisted byte digest."""
        candidate = self.get(candidate_id)
        if candidate is None or candidate.generation_status != "succeeded" or not candidate.asset_path or not candidate.asset_sha256:
            raise ValueError("candidate has no completed verified asset")
        path = self.safe_path(candidate.asset_path)
        if path != self.output_path(candidate_id).with_name("candidate.png"):
            raise ValueError("candidate asset path mismatch")
        image_bytes = path.read_bytes()
        if hashlib.sha256(image_bytes).hexdigest() != candidate.asset_sha256:
            raise ValueError("candidate asset digest mismatch")
        return image_bytes

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
