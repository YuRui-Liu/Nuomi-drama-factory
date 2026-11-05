"""Versioned, project-local first/last frame bindings for director shots."""

from __future__ import annotations

import hashlib
import json
import os
import threading
import uuid
from collections.abc import Mapping
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from novelvideo.director_plan.store import DirectorPlanStore
from novelvideo.media_capabilities.video.h3_reference_runtime import (
    freeze_h3_reference_frames,
)
from novelvideo.narrative_groups.models import GroupStageState, NarrativeGroup
from novelvideo.narrative_groups.service import (
    _materialize_active_groups,
    narrative_group_sidecar_guard,
)
from novelvideo.utils.safe_paths import resolve_under_root


Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Identifier = Annotated[str, Field(min_length=1, max_length=128)]
_SIDECAR_VERSION = 1


def _project_relative_path(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("frame path must be project relative")
    path = PurePosixPath(value)
    if (
        not value
        or path.is_absolute()
        or ".." in path.parts
        or "\\" in value
        or "\0" in value
        or ":" in value
        or str(path) == "."
    ):
        raise ValueError("frame path must be project relative")
    return value


class VideoFrameReference(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    path: str
    sha256: Digest

    _validate_path = field_validator("path")(_project_relative_path)


class ShotVideoKeyframes(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    shot_id: Identifier
    first_frame: VideoFrameReference
    last_frame: VideoFrameReference


class VideoKeyframeBinding(BaseModel):
    """Immutable binding body; revisions and scope are server generated."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: Literal[1] = 1
    project_id: Identifier
    episode: int = Field(strict=True, gt=0)
    group_id: Identifier
    revision: int = Field(strict=True, ge=0)
    director_revision_id: Identifier
    shot_ids: tuple[Identifier, ...]
    bindings: tuple[ShotVideoKeyframes, ...]
    binding_sha256: Digest | None = None

    @model_validator(mode="after")
    def _validate_coverage_and_digest(self) -> Self:
        if len(set(self.shot_ids)) != len(self.shot_ids):
            raise ValueError("duplicate shot in video keyframe binding")
        bound_ids = tuple(binding.shot_id for binding in self.bindings)
        if self.revision == 0:
            if self.bindings:
                raise ValueError("revision zero cannot contain video keyframes")
        elif bound_ids != self.shot_ids:
            raise ValueError("video keyframes must cover every shot in plan order")
        if len(set(bound_ids)) != len(bound_ids):
            raise ValueError("duplicate shot in video keyframe binding")
        if self.binding_sha256 is not None and self.binding_sha256 != _binding_sha256(self):
            raise ValueError("video keyframe binding digest mismatch")
        return self


class VideoKeyframeBindingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(strict=True, ge=0)
    expected_director_revision_id: Identifier
    bindings: tuple[ShotVideoKeyframes, ...] = Field(min_length=1)

    @field_validator("expected_director_revision_id", mode="before")
    @classmethod
    def _trim_revision_id(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


@contextmanager
def _locked_active_group(
    project_dir: str | Path,
    episode: int,
    group_id: str,
    *,
    expected_director_revision_id: str | None = None,
):
    root = Path(project_dir).resolve()
    with narrative_group_sidecar_guard(root, episode):
        store = DirectorPlanStore(root)
        with store.lock_active_revision(episode) as active:
            if active is None:
                raise RuntimeError("active director plan is required for video keyframes")
            active_revision_id = str(active.revision_id)
            if (
                expected_director_revision_id is not None
                and expected_director_revision_id != active_revision_id
            ):
                raise RuntimeError("director plan revision is stale")
            groups = _materialize_active_groups(root, episode, active)
            group = next((item for item in groups if item.id == group_id), None)
            if group is None:
                raise KeyError(group_id)
            if group.director_revision_id != active_revision_id:
                raise RuntimeError("group director revision is stale")
            yield active_revision_id, group


def _sidecar_path(project_dir: str | Path, episode: int) -> Path:
    return Path(project_dir) / ".narrative_groups" / f"ep{episode:03d}-video-keyframes.json"


def _empty_document(project_id: str, episode: int) -> dict:
    return {"version": _SIDECAR_VERSION, "project_id": project_id,
            "episode": episode, "groups": {}}


def _read_document(project_dir: str | Path, project_id: str, episode: int) -> dict:
    path = _sidecar_path(project_dir, episode)
    if not path.is_file():
        return _empty_document(project_id, episode)
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError("video keyframe sidecar is invalid") from exc
    if (
        not isinstance(document, dict)
        or document.get("version") != _SIDECAR_VERSION
        or document.get("episode") != episode
        or document.get("project_id") != project_id
        or not isinstance(document.get("groups"), dict)
    ):
        raise ValueError("video keyframe sidecar scope or version is invalid")
    return document


def _write_document(project_dir: str | Path, document: dict) -> None:
    target = _sidecar_path(project_dir, int(document["episode"]))
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(
        f"{target.name}.tmp-{os.getpid()}-{threading.get_ident()}-{uuid.uuid4().hex}"
    )
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(document, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def _binding_sha256(binding: VideoKeyframeBinding) -> str:
    data = binding.model_dump(mode="json", exclude={"binding_sha256"})
    return hashlib.sha256(
        json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


def _sealed(binding: VideoKeyframeBinding) -> VideoKeyframeBinding:
    return binding.model_copy(update={"binding_sha256": _binding_sha256(binding)})


def validate_video_keyframe_binding(raw: Mapping) -> VideoKeyframeBinding:
    binding = VideoKeyframeBinding.model_validate(raw)
    if binding.binding_sha256 is None:
        raise ValueError("video keyframe binding digest is missing")
    return binding


def _stored_binding(document: dict, group_id: str) -> VideoKeyframeBinding | None:
    raw = document["groups"].get(group_id)
    if raw is None:
        return None
    return validate_video_keyframe_binding(raw)


def _validate_bound_images(project_dir: str | Path, bindings) -> None:
    root = Path(project_dir).resolve()
    expected: dict[str, str] = {}
    for binding in bindings:
        for frame in (binding.first_frame, binding.last_frame):
            path = frame.path
            previous = expected.setdefault(path, frame.sha256)
            if previous != frame.sha256:
                raise ValueError("one project frame path cannot have two expected hashes")
    frozen = freeze_h3_reference_frames(
        tuple(SimpleNamespace(first_frame=path, last_frame=None) for path in expected),
        project_root=root,
    )
    for path, digest in expected.items():
        if frozen[path].sha256 != digest:
            raise ValueError("video keyframe sha256 does not match project image")


def _view(
    *,
    project_id: str,
    episode: int,
    group: NarrativeGroup,
    director_revision_id: str,
    stored: VideoKeyframeBinding | None,
) -> dict:
    shot_ids = tuple(group.production_beat_ids)
    is_current = bool(
        stored is not None
        and stored.project_id == project_id
        and stored.episode == episode
        and stored.group_id == group.id
        and stored.director_revision_id == director_revision_id
        and stored.shot_ids == shot_ids
    )
    if stored is None:
        current = _sealed(VideoKeyframeBinding(
            project_id=project_id, episode=episode, group_id=group.id,
            revision=0, director_revision_id=director_revision_id,
            shot_ids=shot_ids, bindings=(),
        ))
    else:
        current = stored
    return {
        "revision": current.revision,
        "director_revision_id": director_revision_id,
        "group_id": group.id,
        "shot_ids": list(shot_ids),
        "bindings": [item.model_dump(mode="json") for item in current.bindings],
        "binding_sha256": current.binding_sha256,
        "stale": stored is not None and not is_current,
    }


def get_video_keyframes(
    project_dir: str | Path,
    *,
    project_id: str,
    episode: int,
    group_id: str,
) -> dict:
    with _locked_active_group(project_dir, episode, group_id) as (director_id, group):
        document = _read_document(project_dir, project_id, episode)
        stored = _stored_binding(document, group_id)
        return _view(project_id=project_id, episode=episode, group=group,
                     director_revision_id=director_id, stored=stored)


def update_video_keyframes(
    project_dir: str | Path,
    *,
    project_id: str,
    episode: int,
    group_id: str,
    request: VideoKeyframeBindingRequest,
) -> dict:
    _validate_bound_images(project_dir, request.bindings)
    with _locked_active_group(
        project_dir,
        episode,
        group_id,
        expected_director_revision_id=request.expected_director_revision_id,
    ) as (director_id, group):
        shot_ids = tuple(group.production_beat_ids)
        bound_ids = tuple(item.shot_id for item in request.bindings)
        if bound_ids != shot_ids:
            raise ValueError("video keyframes must cover the current shots in plan order")
        video_status = group.stages.get("video", GroupStageState()).status
        if video_status in {"queued", "running"}:
            raise RuntimeError(f"cannot update video keyframes while video stage is {video_status}")
        document = _read_document(project_dir, project_id, episode)
        previous = _stored_binding(document, group_id)
        previous_revision = previous.revision if previous is not None else 0
        if request.expected_revision != previous_revision:
            raise RuntimeError("video keyframe binding revision is stale")
        binding = _sealed(VideoKeyframeBinding(
            project_id=project_id,
            episode=episode,
            group_id=group.id,
            revision=previous_revision + 1,
            director_revision_id=director_id,
            shot_ids=shot_ids,
            bindings=request.bindings,
        ))
        document["groups"][group_id] = binding.model_dump(mode="json")
        _write_document(project_dir, document)
        return _view(project_id=project_id, episode=episode, group=group,
                     director_revision_id=director_id, stored=binding)


def load_current_video_keyframe_binding(
    project_dir: str | Path,
    *,
    project_id: str,
    episode: int,
    group_id: str,
    expected_director_revision_id: str,
) -> VideoKeyframeBinding | None:
    with _locked_active_group(
        project_dir,
        episode,
        group_id,
        expected_director_revision_id=expected_director_revision_id,
    ) as (director_id, group):
        document = _read_document(project_dir, project_id, episode)
        binding = _stored_binding(document, group_id)
        if binding is None:
            return None
        if (
            binding.project_id != project_id
            or binding.episode != episode
            or binding.group_id != group_id
            or binding.director_revision_id != director_id
            or binding.shot_ids != tuple(group.production_beat_ids)
        ):
            raise RuntimeError("video keyframe binding is stale for the active director plan")
        _validate_bound_images(project_dir, binding.bindings)
        return binding


def resolved_video_keyframes(
    binding: VideoKeyframeBinding, project_dir: str | Path
) -> dict[str, dict[str, str]]:
    root = Path(project_dir).resolve()
    return {
        item.shot_id: {
            "first_frame": str(resolve_under_root(root, item.first_frame.path)),
            "last_frame": str(resolve_under_root(root, item.last_frame.path)),
        }
        for item in binding.bindings
    }


def video_keyframe_frame_sources(
    binding: VideoKeyframeBinding, project_dir: str | Path
) -> tuple[str, ...]:
    root = Path(project_dir).resolve()
    return tuple(dict.fromkeys(
        str(resolve_under_root(root, frame.path))
        for item in binding.bindings
        for frame in (item.first_frame, item.last_frame)
    ))


def validate_frozen_video_keyframes(
    binding: VideoKeyframeBinding,
    frozen_frames: Mapping[str, object],
    project_dir: str | Path,
) -> None:
    root = Path(project_dir).resolve()
    for item in binding.bindings:
        for reference in (item.first_frame, item.last_frame):
            source = str(resolve_under_root(root, reference.path))
            frame = frozen_frames.get(source)
            if frame is None or getattr(frame, "sha256", None) != reference.sha256:
                raise ValueError("frozen video keyframe does not match its bound digest")


def validate_video_keyframe_binding_for_group(
    binding: VideoKeyframeBinding,
    *,
    project_id: str,
    episode: int,
    group: NarrativeGroup,
) -> None:
    if (
        binding.project_id != project_id
        or binding.episode != episode
        or binding.group_id != group.id
        or binding.director_revision_id != group.director_revision_id
        or binding.shot_ids != tuple(group.production_beat_ids)
        or tuple(item.shot_id for item in binding.bindings) != tuple(group.production_beat_ids)
    ):
        raise ValueError("queued video keyframe binding does not match the active director group")

