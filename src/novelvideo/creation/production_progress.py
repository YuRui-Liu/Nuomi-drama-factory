"""Durable per-episode checkpoints for Codex-led production."""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
from copy import deepcopy
import hashlib
import json
from pathlib import Path, PurePosixPath
import re

from .production_documents import (
    DocumentValidation,
    _atomic_write_bytes,
    _contained_path,
    _project_file_lock,
    _relative_parts,
)


STAGES = ("screenplay", "assets", "storyboard", "video", "audio", "composition")
STATUSES = ("pending", "running", "awaiting_review", "ready", "failed")
REVIEW_STAGES = frozenset({"screenplay", "assets", "storyboard"})
_EPISODE_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_HASH_RE = re.compile(r"[0-9a-f]{64}\Z")
_UNSET = object()

_DEPENDENTS = {
    "screenplay": frozenset({"assets", "storyboard", "video", "audio", "composition"}),
    "assets": frozenset({"storyboard", "video", "composition"}),
    "storyboard": frozenset({"video", "composition"}),
    "video": frozenset({"composition"}),
    "audio": frozenset({"composition"}),
    "composition": frozenset(),
}
_REQUIRED_OUTPUTS = {
    "screenplay": (frozenset({"script", "screenplay"}),),
    "assets": (frozenset({"assets", "reuse_checklist", "checklist"}),),
    "storyboard": (frozenset({"table"}), frozenset({"grid"})),
    "video": (frozenset({"video", "videos", "segments"}),),
    "audio": (frozenset({"audio", "dubbing", "music"}),),
    "composition": (frozenset({"composition", "final"}),),
}


class ProgressError(RuntimeError):
    """Base error for production progress operations."""


class ProgressConflict(ProgressError):
    """The saved stage or review fingerprint no longer matches."""


class ProgressNotFound(ProgressError, FileNotFoundError):
    """No progress record exists for the requested episode."""


class ProgressValidation(ProgressError, ValueError):
    """The requested progress transition or output reference is invalid."""


@contextmanager
def _project_roots_lock(*roots: Path):
    """Take all involved project locks in stable order, collapsing equal roots."""
    unique_roots = sorted(set(roots), key=lambda path: str(path))
    with ExitStack() as stack:
        for root in unique_roots:
            stack.enter_context(_project_file_lock(root))
        yield


def _episode_id(value: str) -> str:
    if not isinstance(value, str) or _EPISODE_ID_RE.fullmatch(value) is None:
        raise ProgressValidation("episode_id must be a safe path segment")
    return value


def _stage(value: str) -> str:
    if not isinstance(value, str) or value not in STAGES:
        raise ProgressValidation(f"unknown production stage: {value!r}")
    return value


def _mode(value: str) -> str:
    if value not in {"review", "auto"}:
        raise ProgressValidation("mode must be 'review' or 'auto'")
    return value


def _stop_after(value: str | None) -> str | None:
    if value is None:
        return None
    return _stage(value)


def _hash_json(value) -> str:
    canonical = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _empty_stage() -> dict:
    return {
        "status": "pending",
        "input_hash": None,
        "outputs": {},
        "output_hash": None,
        "approval_hash": None,
        "approved_hash": None,
        "review_resolution": None,
        "error": None,
    }


def new_progress(
    episode_id: str,
    mode: str = "review",
    stop_after: str | None = None,
) -> dict:
    """Return an empty progress record for one episode."""
    episode_id = _episode_id(episode_id)
    mode = _mode(mode)
    stop_after = _stop_after(stop_after)
    return {
        "episode_id": episode_id,
        "mode": mode,
        "stop_after": stop_after,
        "stages": {stage: _empty_stage() for stage in STAGES},
        "blocked": None,
    }


def _validate_state(progress: dict) -> dict:
    if not isinstance(progress, dict):
        raise ProgressValidation("progress must be an object")
    episode_id = _episode_id(progress.get("episode_id"))
    mode = _mode(progress.get("mode"))
    stop_after = _stop_after(progress.get("stop_after"))
    stages = progress.get("stages")
    if not isinstance(stages, dict) or set(stages) != set(STAGES):
        raise ProgressValidation("progress stages are incomplete or invalid")
    normalized_stages = {}
    for name in STAGES:
        item = stages[name]
        if not isinstance(item, dict) or item.get("status") not in STATUSES:
            raise ProgressValidation(f"invalid state for stage {name}")
        normalized_stages[name] = {**_empty_stage(), **deepcopy(item)}
    blocked = progress.get("blocked")
    if blocked is not None and (
        not isinstance(blocked, dict)
        or blocked.get("stage") not in STAGES
        or blocked.get("episode_id") != episode_id
        or not isinstance(blocked.get("reason"), str)
    ):
        raise ProgressValidation("blocked progress state is invalid")
    return {
        "episode_id": episode_id,
        "mode": mode,
        "stop_after": stop_after,
        "stages": normalized_stages,
        "blocked": deepcopy(blocked),
    }


def _validate_output_value(value, label: str) -> None:
    if isinstance(value, str):
        if not value.strip():
            raise ProgressValidation(f"output reference {label!r} must not be empty")
        return
    if isinstance(value, list):
        if not value:
            raise ProgressValidation(f"output reference list {label!r} must not be empty")
        for index, item in enumerate(value):
            _validate_output_value(item, f"{label}[{index}]")
        return
    if isinstance(value, dict):
        if set(value) == {"path", "sha256"}:
            if not isinstance(value.get("path"), str) or not value["path"].strip():
                raise ProgressValidation(f"output reference {label!r} has no path")
            if not isinstance(value.get("sha256"), str) or _HASH_RE.fullmatch(value["sha256"]) is None:
                raise ProgressValidation(f"output reference {label!r} has an invalid content hash")
            return
        if not value:
            raise ProgressValidation(f"output reference object {label!r} must not be empty")
        for key, item in value.items():
            if not isinstance(key, str) or not key.strip():
                raise ProgressValidation(f"output reference {label!r} has an invalid key")
            _validate_output_value(item, f"{label}.{key}")
        return
    raise ProgressValidation(f"output reference {label!r} must name a real project file")


def _validated_outputs(stage: str, outputs: dict) -> dict:
    stage = _stage(stage)
    if not isinstance(outputs, dict) or not outputs:
        raise ProgressValidation(f"{stage} requires output references")
    missing = [
        "/".join(sorted(group))
        for group in _REQUIRED_OUTPUTS[stage]
        if not group.intersection(outputs)
    ]
    if missing:
        raise ProgressValidation(f"{stage} output reference is required: {', '.join(missing)}")
    normalized = deepcopy(outputs)
    for key, value in normalized.items():
        if not isinstance(key, str) or not key.strip():
            raise ProgressValidation("output reference names must be non-empty strings")
        _validate_output_value(value, key)
    return normalized


def _invalidate_dependents(progress: dict, changed_stage: str) -> None:
    for dependent in _DEPENDENTS[changed_stage]:
        progress["stages"][dependent] = _empty_stage()
    blocked = progress.get("blocked")
    if blocked and blocked.get("stage") in _DEPENDENTS[changed_stage]:
        progress["blocked"] = None


def invalidate_stage(progress: dict, stage: str, reason: str = "source_changed") -> dict:
    """Clear a changed stage and its downstream receipts without deleting media."""
    current = _validate_state(progress)
    stage = _stage(stage)
    if not isinstance(reason, str) or not reason.strip():
        raise ProgressValidation("invalidation reason is required")
    invalidated = {stage, *_DEPENDENTS[stage]}
    for name in invalidated:
        current["stages"][name] = _empty_stage()
        current["stages"][name]["error"] = reason.strip()
    blocked = current.get("blocked")
    if blocked and blocked.get("stage") in invalidated:
        current["blocked"] = None
    return current


def complete_stage(progress: dict, stage: str, input_hash: str, outputs: dict) -> dict:
    """Record a completed stage, returning a new state without side effects."""
    current = _validate_state(progress)
    stage = _stage(stage)
    if not isinstance(input_hash, str) or not input_hash.strip():
        raise ProgressValidation("input_hash is required")
    normalized_outputs = _validated_outputs(stage, outputs)
    output_hash = _hash_json(normalized_outputs)
    approval_hash = _hash_json({"stage": stage, "input_hash": input_hash, "output_hash": output_hash})
    previous = current["stages"][stage]
    unchanged = previous.get("approval_hash") == approval_hash
    stage_record = {
        "status": "ready" if current["mode"] == "auto" or stage not in REVIEW_STAGES else "awaiting_review",
        "input_hash": input_hash,
        "outputs": normalized_outputs,
        "output_hash": output_hash,
        "approval_hash": approval_hash,
        "approved_hash": None,
        "review_resolution": "auto" if current["mode"] == "auto" and stage in REVIEW_STAGES else None,
        "error": None,
    }
    if unchanged and previous.get("status") in {"ready", "awaiting_review"}:
        # Replaying a successful completion does not undo an approval or
        # re-open a review checkpoint that was already presented to the user.
        # A failed stage is different: the same verified output is a successful
        # recovery and must clear the failure state.
        stage_record["status"] = previous["status"]
        stage_record["approved_hash"] = previous.get("approved_hash")
        stage_record["review_resolution"] = previous.get("review_resolution")
        stage_record["error"] = previous.get("error")
    else:
        _invalidate_dependents(current, stage)
    current["stages"][stage] = stage_record
    blocked = current.get("blocked")
    if blocked is not None and blocked.get("stage") == stage:
        current["blocked"] = None
    return current


def approve_stage(progress: dict, stage: str, input_hash: str) -> dict:
    """Approve only the exact result fingerprint shown at the review stop."""
    current = _validate_state(progress)
    stage = _stage(stage)
    item = current["stages"][stage]
    if item["status"] != "awaiting_review":
        raise ProgressConflict(f"stage {stage} is not awaiting review")
    if not isinstance(input_hash, str) or input_hash != item.get("approval_hash"):
        raise ProgressConflict("review fingerprint is stale; reload the current result")
    item["status"] = "ready"
    item["approved_hash"] = item["approval_hash"]
    item["review_resolution"] = "approved"
    return current


def configure_progress(
    progress: dict,
    *,
    mode: str | None = None,
    stop_after: str | None | object = _UNSET,
) -> dict:
    """Change execution policy while preserving every existing stage result."""
    current = _validate_state(progress)
    if mode is not None:
        updated_mode = _mode(mode)
        current["mode"] = updated_mode
        if updated_mode == "auto":
            # Explicitly choosing auto clears an already reached review gate;
            # it does not fabricate a user approval receipt.
            for stage in REVIEW_STAGES:
                item = current["stages"][stage]
                if item["status"] == "awaiting_review":
                    item["status"] = "ready"
                    item["review_resolution"] = "auto"
    if stop_after is not _UNSET:
        current["stop_after"] = _stop_after(stop_after)
    return current


def should_stop_after(progress: dict, stage: str) -> bool:
    current = _validate_state(progress)
    stage = _stage(stage)
    item = current["stages"][stage]
    return current["stop_after"] == stage and item["status"] in {"ready", "awaiting_review", "failed"}


def should_pause_after(progress: dict, stage: str) -> bool:
    """Return whether the runner must stop for an explicit or review checkpoint."""
    current = _validate_state(progress)
    stage = _stage(stage)
    status = current["stages"][stage]["status"]
    return status in {"awaiting_review", "failed"} or should_stop_after(current, stage)


def fail_stage(progress: dict, stage: str, reason: str, *, input_hash: str | None = None) -> dict:
    current = _validate_state(progress)
    stage = _stage(stage)
    if not isinstance(reason, str) or not reason.strip():
        raise ProgressValidation("failure reason is required")
    item = current["stages"][stage]
    item["status"] = "failed"
    item["input_hash"] = input_hash
    item["error"] = reason.strip()
    _invalidate_dependents(current, stage)
    current["blocked"] = {
        "episode_id": current["episode_id"],
        "stage": stage,
        "reason": reason.strip(),
    }
    return current


def _snapshot_output_refs(root: Path, outputs: dict) -> dict:
    """Resolve output refs under the project and capture their current bytes."""

    def snapshot(value, label):
        if isinstance(value, str):
            try:
                parts = _relative_parts(value, text_document=False)
                relative = PurePosixPath(*parts).as_posix()
                path = _contained_path(root, relative, text_document=False)
            except DocumentValidation as exc:
                raise ProgressValidation(str(exc)) from exc
            if not path.exists() or not path.is_file():
                raise ProgressValidation(f"output file does not exist: {relative}")
            try:
                data = path.read_bytes()
            except OSError as exc:
                raise ProgressValidation(f"output file cannot be read: {relative}") from exc
            return {"path": relative, "sha256": hashlib.sha256(data).hexdigest()}
        if isinstance(value, list):
            if not value:
                raise ProgressValidation(f"output reference list {label!r} must not be empty")
            return [snapshot(item, f"{label}[{index}]") for index, item in enumerate(value)]
        if isinstance(value, dict):
            if not value:
                raise ProgressValidation(f"output reference object {label!r} must not be empty")
            if set(value) == {"path", "sha256"}:
                reference = snapshot(value["path"], label)
                if value["sha256"] != reference["sha256"]:
                    raise ProgressValidation(
                        f"output content hash does not match file: {reference['path']}"
                    )
                return reference
            return {key: snapshot(item, f"{label}.{key}") for key, item in value.items()}
        raise ProgressValidation(f"output reference {label!r} must name a project file")

    return {key: snapshot(value, key) for key, value in outputs.items()}


def _check_output_snapshot(root: Path, outputs: dict) -> None:
    def check(value, label):
        if isinstance(value, dict) and set(value) == {"path", "sha256"}:
            try:
                parts = _relative_parts(value["path"], text_document=False)
                relative = PurePosixPath(*parts).as_posix()
                path = _contained_path(root, relative, text_document=False)
            except (DocumentValidation, TypeError) as exc:
                raise ProgressConflict(f"review output changed or is unsafe: {label}") from exc
            if not path.exists() or not path.is_file():
                raise ProgressConflict(f"review output changed or disappeared: {relative}")
            try:
                current_hash = hashlib.sha256(path.read_bytes()).hexdigest()
            except OSError as exc:
                raise ProgressConflict(f"review output changed or cannot be read: {relative}") from exc
            if current_hash != value["sha256"]:
                raise ProgressConflict(f"review output changed after it was displayed: {relative}")
            return
        if isinstance(value, list):
            for index, item in enumerate(value):
                check(item, f"{label}[{index}]")
            return
        if isinstance(value, dict):
            for key, item in value.items():
                check(item, f"{label}.{key}")
            return
        raise ProgressConflict(f"saved review output is invalid: {label}")

    for key, value in outputs.items():
        check(value, key)


class ProductionProgress:
    """Persist independent episode checkpoints under ``creation/progress``."""

    def __init__(self, root: str | Path, *, output_root: str | Path | None = None) -> None:
        """Use the state root for receipts and output_root for media references.

        Canvas state commonly lives under ``ctx.state_dir`` while generated
        documents and media live under ``ctx.output_dir``. The default keeps
        single-root local callers and tests convenient.
        """
        self.root = Path(root).expanduser().resolve(strict=False)
        self.output_root = (
            Path(output_root).expanduser().resolve(strict=False)
            if output_root is not None
            else self.root
        )

    @staticmethod
    def _relative_path(episode_id: str) -> str:
        episode_id = _episode_id(episode_id)
        return f"creation/progress/{episode_id}.json"

    def path_for(self, episode_id: str) -> Path:
        relative = self._relative_path(episode_id)
        try:
            return _contained_path(self.root, relative, text_document=False)
        except DocumentValidation as exc:
            raise ProgressValidation(str(exc)) from exc

    def _read_unlocked(self, episode_id: str, *, create: bool) -> dict:
        path = self.path_for(episode_id)
        if not path.exists():
            if not create:
                raise ProgressNotFound(f"progress not found for episode {episode_id}")
            state = new_progress(episode_id)
            self._write_unlocked(state)
            return state
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProgressValidation(f"progress state is unreadable for episode {episode_id}") from exc
        state = _validate_state(payload)
        if state["episode_id"] != episode_id:
            raise ProgressValidation("progress state episode_id does not match its file")
        return state

    def _write_unlocked(self, progress: dict) -> None:
        state = _validate_state(progress)
        relative = self._relative_path(state["episode_id"])
        try:
            _atomic_write_bytes(
                self.root,
                relative,
                (json.dumps(state, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8"),
                text_document=False,
            )
        except DocumentValidation as exc:
            raise ProgressValidation(str(exc)) from exc

    def get(self, episode_id: str, *, create: bool = True) -> dict:
        episode_id = _episode_id(episode_id)
        with _project_file_lock(self.root):
            return self._read_unlocked(episode_id, create=create)

    def invalidate(self, episode_id: str, stage: str, *, reason: str = "source_changed") -> dict | None:
        """Invalidate an existing managed receipt; never enroll an experiment."""
        episode_id = _episode_id(episode_id)
        stage = _stage(stage)
        with _project_roots_lock(self.root, self.output_root):
            if not self.path_for(episode_id).exists():
                return None
            state = self._read_unlocked(episode_id, create=False)
            updated = invalidate_stage(state, stage, reason)
            self._write_unlocked(updated)
            return updated

    def verify_stage_outputs(self, episode_id: str, stage: str) -> tuple[dict, str | None]:
        """Recheck a saved receipt's content-addressed outputs before admission.

        ``input_hash`` is caller-provided metadata, not proof of current inputs.
        The receipt is trusted only while its own output and approval fingerprints
        agree with the stored references and the referenced files still match.
        """
        episode_id = _episode_id(episode_id)
        stage = _stage(stage)
        with _project_roots_lock(self.root, self.output_root):
            state = self._read_unlocked(episode_id, create=False)
            item = state["stages"][stage]
            if item["status"] not in {"ready", "awaiting_review"}:
                return state, None

            output_hash = item.get("output_hash")
            approval_hash = item.get("approval_hash")
            expected_approval_hash = _hash_json({
                "stage": stage,
                "input_hash": item.get("input_hash"),
                "output_hash": output_hash,
            })
            reason = None
            if (not isinstance(item.get("input_hash"), str) or not item["input_hash"].strip()
                    or output_hash != _hash_json(item.get("outputs") or {})
                    or approval_hash != expected_approval_hash
                    or item.get("status") == "ready" and item.get("review_resolution") == "approved"
                    and item.get("approved_hash") != approval_hash):
                reason = "receipt_fingerprint_mismatch"
            else:
                try:
                    _check_output_snapshot(self.output_root, item.get("outputs", {}))
                except ProgressConflict:
                    reason = "output_changed"

            if reason is None:
                return state, None
            updated = invalidate_stage(state, stage, reason)
            self._write_unlocked(updated)
            return updated, reason

    def configure(
        self,
        episode_id: str,
        *,
        mode: str | None = None,
        stop_after: str | None | object = _UNSET,
    ) -> dict:
        episode_id = _episode_id(episode_id)
        with _project_roots_lock(self.root, self.output_root):
            state = self._read_unlocked(episode_id, create=True)
            if mode == "auto":
                for stage in REVIEW_STAGES:
                    item = state["stages"][stage]
                    if item["status"] == "awaiting_review":
                        _check_output_snapshot(self.output_root, item.get("outputs", {}))
            updated = configure_progress(state, mode=mode, stop_after=stop_after)
            self._write_unlocked(updated)
            return updated

    def complete(self, episode_id: str, stage: str, input_hash: str, outputs: dict) -> dict:
        episode_id = _episode_id(episode_id)
        stage = _stage(stage)
        # Catch missing required labels before touching project files.
        normalized = _validated_outputs(stage, outputs)
        with _project_roots_lock(self.root, self.output_root):
            state = self._read_unlocked(episode_id, create=True)
            verified = _snapshot_output_refs(self.output_root, normalized)
            updated = complete_stage(state, stage, input_hash, verified)
            self._write_unlocked(updated)
            return updated

    def approve(self, episode_id: str, stage: str, input_hash: str) -> dict:
        episode_id = _episode_id(episode_id)
        stage = _stage(stage)
        with _project_roots_lock(self.root, self.output_root):
            state = self._read_unlocked(episode_id, create=False)
            item = state["stages"][stage]
            if item.get("status") == "awaiting_review":
                _check_output_snapshot(self.output_root, item.get("outputs", {}))
            updated = approve_stage(state, stage, input_hash)
            self._write_unlocked(updated)
            return updated

    def fail(self, episode_id: str, stage: str, reason: str, *, input_hash: str | None = None) -> dict:
        episode_id = _episode_id(episode_id)
        with _project_file_lock(self.root):
            state = self._read_unlocked(episode_id, create=True)
            updated = fail_stage(state, stage, reason, input_hash=input_hash)
            self._write_unlocked(updated)
            return updated


__all__ = [
    "ProgressConflict",
    "ProgressError",
    "ProgressNotFound",
    "ProgressValidation",
    "ProductionProgress",
    "REVIEW_STAGES",
    "STAGES",
    "STATUSES",
    "approve_stage",
    "complete_stage",
    "configure_progress",
    "fail_stage",
    "invalidate_stage",
    "new_progress",
    "should_pause_after",
    "should_stop_after",
]
