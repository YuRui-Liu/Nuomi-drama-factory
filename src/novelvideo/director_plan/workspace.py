"""Versioned scene blocking and strict script handoff context for the director workspace."""

from __future__ import annotations

import json
import os
import threading
import uuid
from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import portalocker
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from ulid import ULID

from .cinematography import PropBlocking
from .models import DirectorPlanRevision, SceneBlockingRevisionReference


class StrictHandoffReviewRequired(RuntimeError):
    """The selected screenplay revision has no clean current consistency check."""


class SceneBlockingConflict(RuntimeError):
    """A scene blocking edit was based on a revision that is no longer current."""


class SceneBlockingCompileConflict(RuntimeError):
    """Saved scene blocking cannot be compiled against the adopted director plan."""


class _BlockingModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _BlockingPathPoint(_BlockingModel):
    x: float = Field(ge=0, le=100, allow_inf_nan=False)
    y: float = Field(ge=0, le=100, allow_inf_nan=False)
    at_seconds: float = Field(ge=0, allow_inf_nan=False)


class _BlockingPosition(_BlockingModel):
    id: str = Field(min_length=1, max_length=128)
    label: str = Field(min_length=1, max_length=160)
    x: float = Field(ge=0, le=100, allow_inf_nan=False)
    y: float = Field(ge=0, le=100, allow_inf_nan=False)
    facing: float = Field(ge=0, le=359, allow_inf_nan=False)
    gaze_target: str = Field(default="", max_length=128)
    path: list[_BlockingPathPoint] = Field(default_factory=list, max_length=100)

    @field_validator("id", "label", mode="before")
    @classmethod
    def trim_required_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class _BlockingCamera(_BlockingModel):
    x: float = Field(ge=0, le=100, allow_inf_nan=False)
    y: float = Field(ge=0, le=100, allow_inf_nan=False)
    facing: float = Field(ge=0, le=359, allow_inf_nan=False)


class _BlockingLight(_BlockingModel):
    id: str = Field(min_length=1, max_length=128)
    label: str = Field(min_length=1, max_length=160)
    x: float = Field(ge=0, le=100, allow_inf_nan=False)
    y: float = Field(ge=0, le=100, allow_inf_nan=False)
    facing: float = Field(default=0, ge=0, le=359, allow_inf_nan=False)
    intensity: float = Field(ge=0, le=100, allow_inf_nan=False)

    @field_validator("id", "label", mode="before")
    @classmethod
    def trim_required_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class _BlockingProp(_BlockingModel):
    id: str = Field(min_length=1, max_length=128)
    label: str = Field(min_length=1, max_length=160)
    x: float = Field(ge=0, le=100, allow_inf_nan=False)
    y: float = Field(ge=0, le=100, allow_inf_nan=False)
    facing: float = Field(default=0, ge=0, le=359, allow_inf_nan=False)
    held_by: str = Field(default="", max_length=128)
    path: list[_BlockingPathPoint] = Field(default_factory=list, max_length=100)

    @field_validator("id", "label", mode="before")
    @classmethod
    def trim_required_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class SceneBlocking(_BlockingModel):
    label: str = Field(min_length=1, max_length=200)
    actors: list[_BlockingPosition] = Field(default_factory=list, max_length=100)
    props: list[_BlockingProp] = Field(default_factory=list, max_length=100)
    camera: _BlockingCamera | None
    lights: list[_BlockingLight] = Field(default_factory=list, max_length=100)

    @field_validator("label", mode="before")
    @classmethod
    def trim_label(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode="after")
    def unique_object_ids(self) -> SceneBlocking:
        actor_ids = [actor.id for actor in self.actors]
        prop_ids = [prop.id for prop in self.props]
        light_ids = [light.id for light in self.lights]
        if len(actor_ids + prop_ids) != len(set(actor_ids + prop_ids)):
            raise ValueError("actor and prop ids must be unique")
        if len(light_ids) != len(set(light_ids)):
            raise ValueError("light ids must be unique")
        target_ids = set(actor_ids + prop_ids)
        for actor in self.actors:
            if actor.gaze_target and actor.gaze_target not in target_ids:
                raise ValueError(f"gaze target {actor.gaze_target!r} is not in this scene")
            _validate_path_times(actor.path)
        for prop in self.props:
            if prop.held_by and prop.held_by not in set(actor_ids):
                raise ValueError(f"prop holder {prop.held_by!r} is not a scene character")
            _validate_path_times(prop.path)
        return self


def _validate_path_times(path: list[_BlockingPathPoint]) -> None:
    times = [point.at_seconds for point in path]
    if times != sorted(times) or len(times) != len(set(times)):
        raise ValueError("path event times must increase")


_LOCK_GUARD = threading.Lock()
_LOCKS: dict[tuple[str, int], threading.RLock] = {}


def _lock_for(project_dir: Path, episode: int) -> threading.RLock:
    key = (str(project_dir), episode)
    with _LOCK_GUARD:
        return _LOCKS.setdefault(key, threading.RLock())


def _blocking_path_text(path: Sequence[_BlockingPathPoint]) -> str:
    return "; ".join(
        f"{point.at_seconds:g}s at x={point.x:g}%, y={point.y:g}%"
        for point in path
    )


def _atomic_write(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(
        f"{path.name}.tmp-{os.getpid()}-{threading.get_ident()}-{uuid.uuid4().hex}"
    )
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class DirectorWorkspaceStore:
    """Persist immutable blocking revisions and pinned group references per episode."""

    def __init__(self, project_dir: str | Path) -> None:
        self._project_dir = Path(project_dir).absolute().resolve()

    def save_scene_blocking(
        self,
        episode: int,
        scene_id: str,
        *,
        base_version: int | str | None,
        semantic_revision_id: str | None,
        director_revision_id: str | None = None,
        blocking: Mapping[str, Any],
        group_ids: Sequence[str],
    ) -> dict[str, Any]:
        if episode <= 0:
            raise ValueError("episode must be positive")
        scene_id = self._scene_id(scene_id)
        validated = SceneBlocking.model_validate(blocking)
        groups = tuple(self._required_id(item, "group id") for item in group_ids)
        if len(groups) != len(set(groups)):
            raise ValueError("group ids must be unique")
        director_id = (
            self._required_id(director_revision_id, "director revision id")
            if director_revision_id is not None else None
        )
        if groups and director_id is None:
            raise ValueError("director revision id is required when pinning groups")
        semantic_id = self._optional_id(semantic_revision_id)

        with self._guard(episode) as (path, data):
            scene = data["scenes"].setdefault(
                scene_id, {"history": [], "group_references": {}}
            )
            history = scene["history"]
            current = history[-1] if history else None
            self._require_base_version(current, base_version)
            revision = {
                "revision_id": str(ULID()),
                "version": int(current["version"]) + 1 if current else 1,
                "semantic_revision_id": semantic_id,
                "blocking": validated.model_dump(mode="json"),
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            history.append(revision)
            # group_ids is the exact reference set for this scene. Reassigning a
            # group also clears its stale pin from any other scene.
            scene["group_references"] = {}
            selected_group_ids = set(groups)
            for other_scene_id, other_scene in data["scenes"].items():
                if other_scene_id != scene_id:
                    for group_id in selected_group_ids:
                        other_scene["group_references"].pop(group_id, None)
            for group_id in groups:
                scene["group_references"][group_id] = {
                    "scene_id": scene_id,
                    "group_id": group_id,
                    "director_revision_id": director_id,
                    "version": revision["version"],
                    "revision_id": revision["revision_id"],
                }
            _atomic_write(path, data)
            return dict(revision)

    def get_scene_blocking(self, episode: int, scene_id: str) -> dict[str, Any] | None:
        scene_id = self._scene_id(scene_id)
        with self._guard(episode, write=False) as (_path, data):
            scene = data["scenes"].get(scene_id)
            if scene is None:
                return None
            history = list(scene["history"])
            return {
                "scene_id": scene_id,
                "current": dict(history[-1]) if history else None,
                "history": history,
                "group_references": sorted(
                    scene["group_references"].values(), key=lambda ref: ref["group_id"]
                ),
            }

    def list_scene_blockings(self, episode: int) -> dict[str, dict[str, Any]]:
        with self._guard(episode, write=False) as (_path, data):
            return {
                scene_id: {
                    "scene_id": scene_id,
                    "current": dict(scene["history"][-1]) if scene["history"] else None,
                    "history": list(scene["history"]),
                    "group_references": sorted(
                        scene["group_references"].values(), key=lambda ref: ref["group_id"]
                    ),
                }
                for scene_id, scene in sorted(data["scenes"].items())
            }

    def list_group_references(self, episode: int) -> list[dict[str, Any]]:
        references = [
            reference
            for scene in self.list_scene_blockings(episode).values()
            for reference in scene["group_references"]
        ]
        return sorted(references, key=lambda ref: (ref["scene_id"], ref["group_id"]))

    def set_group_reference(
        self, episode: int, scene_id: str, group_id: str, revision_id: str,
        director_revision_id: str | None = None,
    ) -> dict[str, Any]:
        scene_id = self._scene_id(scene_id)
        group_id = self._required_id(group_id, "group id")
        revision_id = self._required_id(revision_id, "revision id")
        director_id = (
            self._required_id(director_revision_id, "director revision id")
            if director_revision_id is not None else None
        )
        if director_id is None:
            raise ValueError("director revision id is required when pinning a group")
        with self._guard(episode) as (path, data):
            scene = data["scenes"].get(scene_id)
            if scene is None:
                raise KeyError(scene_id)
            revision = next(
                (item for item in scene["history"] if item["revision_id"] == revision_id),
                None,
            )
            if revision is None:
                raise KeyError(revision_id)
            for other_scene_id, other_scene in data["scenes"].items():
                if other_scene_id != scene_id:
                    other_scene["group_references"].pop(group_id, None)
            reference = {
                "scene_id": scene_id,
                "group_id": group_id,
                "director_revision_id": director_id,
                "version": int(revision["version"]),
                "revision_id": revision_id,
            }
            scene["group_references"][group_id] = reference
            _atomic_write(path, data)
            return reference

    def clear_scene_group_references(
        self, episode: int, scene_id: str, *, base_revision_id: str
    ) -> dict[str, Any]:
        """Remove current pins while keeping every immutable blocking revision."""
        scene_id = self._scene_id(scene_id)
        base_revision_id = self._required_id(base_revision_id, "base revision id")
        with self._guard(episode) as (path, data):
            scene = data["scenes"].get(scene_id)
            if scene is None or not scene.get("history"):
                raise KeyError(scene_id)
            current = scene["history"][-1]
            self._require_base_version(current, base_revision_id)
            removed = sorted(scene.get("group_references", {}))
            scene["group_references"] = {}
            _atomic_write(path, data)
            return {
                "scene_id": scene_id,
                "revision_id": current["revision_id"],
                "version": int(current["version"]),
                "removed_group_ids": removed,
            }

    @staticmethod
    def _require_base_version(current: dict[str, Any] | None, base_version: int | str | None) -> None:
        if current is None:
            if base_version not in (None, 0):
                raise SceneBlockingConflict("scene blocking version does not match")
            return
        matches_revision = isinstance(base_version, str) and base_version == current["revision_id"]
        matches_version = isinstance(base_version, int) and not isinstance(base_version, bool) and base_version == current["version"]
        if not (matches_revision or matches_version):
            raise SceneBlockingConflict("scene blocking version does not match")

    @staticmethod
    def _scene_id(value: object) -> str:
        result = DirectorWorkspaceStore._required_id(value, "scene id")
        if len(result) > 200:
            raise ValueError("scene id is too long")
        return result

    @staticmethod
    def _required_id(value: object, name: str) -> str:
        result = value.strip() if isinstance(value, str) else ""
        if not result:
            raise ValueError(f"{name} must not be blank")
        return result

    @staticmethod
    def _optional_id(value: object) -> str | None:
        if value is None:
            return None
        return DirectorWorkspaceStore._required_id(value, "semantic revision id")

    @contextmanager
    def _guard(self, episode: int, *, write: bool = True):
        if episode <= 0:
            raise ValueError("episode must be positive")
        directory = self._project_dir / "director_workspace" / f"episode_{episode:03d}"
        path = directory / "scene_blocking.json"
        with _lock_for(self._project_dir, episode):
            directory.mkdir(parents=True, exist_ok=True)
            with portalocker.Lock(str(directory / ".workspace.lock"), mode="a+", timeout=60):
                if path.is_file():
                    data = json.loads(path.read_text(encoding="utf-8"))
                    if data.get("schema_version") != 1 or not isinstance(data.get("scenes"), dict):
                        raise ValueError("invalid director workspace data")
                else:
                    data = {"schema_version": 1, "scenes": {}}
                yield path, data


def compile_scene_blocking_candidate(
    revision: DirectorPlanRevision,
    references: Sequence[Mapping[str, Any]],
    *,
    source_spans: Sequence[Any],
    dramatic_beats: Sequence[Any] = (),
) -> DirectorPlanRevision:
    """Compile pinned scene blocking into a new, separately adoptable director revision.

    Only coordinates attached to IDs already present in the selected shots are
    projected. Cinematography's source and source IDs remain unchanged; the new
    revision separately records the exact scene/group blocking versions used.
    """
    if revision.status != "active":
        raise SceneBlockingCompileConflict("an active director plan is required")
    if not references:
        raise SceneBlockingCompileConflict("no scene blocking group references are saved")

    groups_by_id = {group.id: group for group in revision.groups}
    if len(groups_by_id) != len(revision.groups):
        raise SceneBlockingCompileConflict("adopted director plan has duplicate group IDs")

    by_group: dict[str, tuple[Mapping[str, Any], SceneBlocking]] = {}
    scene_references: list[SceneBlockingRevisionReference] = []
    for reference in references:
        try:
            scene_id = DirectorWorkspaceStore._scene_id(reference.get("scene_id"))
            group_id = DirectorWorkspaceStore._required_id(reference.get("group_id"), "group id")
            blocking_revision_id = DirectorWorkspaceStore._required_id(
                reference.get("revision_id"), "blocking revision id"
            )
            director_revision_id = reference.get("director_revision_id")
            version = int(reference.get("version"))
            semantic_revision_id = DirectorWorkspaceStore._optional_id(
                reference.get("semantic_revision_id")
            )
            blocking = SceneBlocking.model_validate(reference.get("blocking"))
        except (TypeError, ValueError) as exc:
            raise SceneBlockingCompileConflict(f"invalid scene blocking reference: {exc}") from exc
        if version <= 0:
            raise SceneBlockingCompileConflict("scene blocking version must be positive")
        if semantic_revision_id != revision.semantic_revision_id:
            raise SceneBlockingCompileConflict(
                f"scene blocking for {scene_id!r} is not based on the active semantic revision"
            )
        if director_revision_id != revision.revision_id:
            raise SceneBlockingCompileConflict(
                f"scene blocking pin for {scene_id!r} is bound to director revision "
                f"{director_revision_id!r}, not adopted revision {revision.revision_id!r}; "
                "clear and re-save the scene reference"
            )
        if group_id not in groups_by_id:
            raise SceneBlockingCompileConflict(
                f"scene blocking references group {group_id!r}, which is not in the adopted plan"
            )
        if group_id in by_group:
            previous = by_group[group_id][0]
            raise SceneBlockingCompileConflict(
                f"group {group_id!r} has more than one scene blocking reference "
                f"({previous.get('scene_id')!r}, {scene_id!r})"
            )
        by_group[group_id] = (reference, blocking)
        scene_references.append(
            SceneBlockingRevisionReference(
                scene_id=scene_id,
                group_id=group_id,
                director_revision_id=director_revision_id,
                revision_id=blocking_revision_id,
                version=version,
                semantic_revision_id=semantic_revision_id,
            )
        )

    # A scene-level blocking draft can be referenced by several groups whose
    # asset inventories differ. Validate against the union of groups pinned to
    # the same scene-map revision, then apply only each group's own inventory.
    inventories: dict[tuple[str, str], dict[str, set[str]]] = {}
    blocking_by_revision: dict[tuple[str, str], SceneBlocking] = {}
    for group_id, (reference, blocking) in by_group.items():
        key = (str(reference.get("scene_id")), str(reference.get("revision_id")))
        if key in blocking_by_revision and blocking_by_revision[key] != blocking:
            raise SceneBlockingCompileConflict(
                f"scene blocking revision {key[1]!r} has inconsistent contents"
            )
        blocking_by_revision[key] = blocking
        inventory = inventories.setdefault(key, {"actors": set(), "lights": set(), "props": set()})
        group = groups_by_id[group_id]
        inventory["actors"].update(
            subject.subject_id
            for shot in group.shots
            if shot.cinematography is not None
            for subject in shot.cinematography.subjects
        )
        inventory["lights"].update(
            light.light_id
            for shot in group.shots
            if shot.cinematography is not None
            for light in shot.cinematography.lights
        )
        inventory["props"].update(
            requirement.entity_key
            for shot in group.shots
            for requirement in shot.asset_requirements
            if requirement.kind == "prop"
        )
        inventory["props"].update(
            prop.prop_id
            for shot in group.shots
            if shot.cinematography is not None
            for prop in shot.cinematography.props
        )

    for key, blocking in blocking_by_revision.items():
        inventory = inventories[key]
        unknown_actor_ids = {actor.id for actor in blocking.actors} - inventory["actors"]
        if unknown_actor_ids:
            raise SceneBlockingCompileConflict(
                f"actor id {sorted(unknown_actor_ids)[0]!r} does not match a subject in the referenced scene groups"
            )
        unknown_light_ids = {light.id for light in blocking.lights} - inventory["lights"]
        if unknown_light_ids:
            raise SceneBlockingCompileConflict(
                f"light id {sorted(unknown_light_ids)[0]!r} does not match a light in the referenced scene groups"
            )
        unknown_prop_ids = {prop.id for prop in blocking.props} - inventory["props"]
        if unknown_prop_ids:
            raise SceneBlockingCompileConflict(
                f"prop id {sorted(unknown_prop_ids)[0]!r} does not match a prop in the referenced scene groups"
            )

    updated_groups = []
    for group in revision.groups:
        pinned = by_group.get(group.id)
        if pinned is None:
            updated_groups.append(group)
            continue
        reference, blocking = pinned
        # Scene-map references may mention a character from another group in
        # the same scene. Keep that reference textual; only project objects
        # that exist in each shot's cinematography/asset requirements.
        actor_by_id = {actor.id: actor for actor in blocking.actors}
        prop_by_id = {prop.id: prop for prop in blocking.props}
        light_by_id = {light.id: light for light in blocking.lights}
        updated_shots = []
        for shot in group.shots:
            cinematography = shot.cinematography
            if cinematography is None:
                raise SceneBlockingCompileConflict(
                    f"group {group.id!r} shot {shot.id!r} has no cinematography to compile into"
                )
            subjects = tuple(
                subject.model_copy(
                    update={
                        "world_position": (
                            f"{actor_by_id[subject.subject_id].label} on scene map at "
                            f"x={actor_by_id[subject.subject_id].x:g}%, "
                            f"y={actor_by_id[subject.subject_id].y:g}%"
                        ),
                        "facing": (
                            f"scene-map heading {actor_by_id[subject.subject_id].facing:g}°"
                        ),
                        "gaze_target": (
                            (
                                f"{actor_by_id[subject.subject_id].gaze_target}"
                                if actor_by_id[subject.subject_id].gaze_target
                                else "no explicit gaze target"
                            )
                        ),
                        "motion_path": (
                            _blocking_path_text(actor_by_id[subject.subject_id].path)
                            or subject.motion_path
                        ),
                    }
                )
                if subject.subject_id in actor_by_id
                else subject
                for subject in cinematography.subjects
            )
            lights = tuple(
                light.model_copy(
                    update={
                        "world_position": (
                            f"{light_by_id[light.light_id].label} on scene map at "
                            f"x={light_by_id[light.light_id].x:g}%, "
                            f"y={light_by_id[light.light_id].y:g}%"
                        ),
                        "relative_intensity": (
                            f"user scene-blocking intensity "
                            f"{light_by_id[light.light_id].intensity:g}%"
                        ),
                        "direction": (
                            f"scene-map heading {light_by_id[light.light_id].facing:g}°"
                        ),
                    }
                )
                if light.light_id in light_by_id
                else light
                for light in cinematography.lights
            )
            existing_props = {item.prop_id: item for item in cinematography.props}
            shot_prop_ids = {
                requirement.entity_key for requirement in shot.asset_requirements
                if requirement.kind == "prop"
            } | existing_props.keys()
            props = tuple(
                PropBlocking(
                    prop_id=prop_id,
                    world_position=(
                        f"{prop.label} on scene map at x={prop.x:g}%, y={prop.y:g}%"
                    ),
                    facing=f"scene-map heading {prop.facing:g}°",
                    held_by=(
                        f"{actor_by_id[prop.held_by].label} ({prop.held_by})"
                        if prop.held_by in actor_by_id
                        else "not held"
                    ),
                    motion_path=_blocking_path_text(prop.path) or "stationary",
                )
                if (prop := prop_by_id.get(prop_id)) is not None
                else existing_props[prop_id]
                for prop_id in dict.fromkeys(
                    [
                        *(prop_id for prop_id in existing_props if prop_id in shot_prop_ids),
                        *sorted(shot_prop_ids & prop_by_id.keys()),
                    ]
                )
            )
            camera = blocking.camera
            camera_side = cinematography.camera_side
            if camera is not None:
                camera_side = (
                    f"{camera_side}; {blocking.label} scene-map camera at "
                    f"x={camera.x:g}%, y={camera.y:g}%, "
                    f"heading {camera.facing:g}°"
                )
            updated_cinematography = cinematography.model_copy(
                update={
                    "subjects": subjects,
                    "props": props,
                    "lights": lights,
                    "camera_side": camera_side,
                }
            )
            updated_shots.append(
                type(shot).model_validate(
                    {
                        **shot.model_dump(mode="python"),
                        "cinematography": updated_cinematography,
                    }
                )
            )
        updated_groups.append(
            type(group).model_validate(
                {**group.model_dump(mode="python"), "shots": tuple(updated_shots)}
            )
        )

    candidate = DirectorPlanRevision.model_validate(
        {
            **revision.model_dump(mode="python"),
            "revision_id": str(ULID()),
            "parent_revision_id": revision.revision_id,
            "status": "review_required",
            "edit_source": "human",
            "groups": tuple(updated_groups),
            "scene_blocking_references": tuple(scene_references),
            "creative_feedback": None,
            "validation_report": revision.validation_report,
            "created_at": datetime.now(timezone.utc),
            "activated_at": None,
        }
    )
    from .validation import validate_director_plan

    report = validate_director_plan(candidate, source_spans, dramatic_beats)
    if not report.passed:
        errors = "; ".join(issue.code for issue in report.issues)
        raise SceneBlockingCompileConflict(
            f"compiled director candidate did not pass validation: {errors}"
        )
    return candidate.model_copy(update={"validation_report": report})


async def strict_handoff_context(
    handoff: Any, document_id: str, revision_id: str
) -> tuple[dict[str, str], dict[str, str]]:
    """Return only evidence-backed references and a current actual check acknowledgement."""
    async with handoff.documents._db() as db:
        rows = await (await db.execute(
            "SELECT data FROM script_consistency_runs ORDER BY created_at DESC,rowid DESC"
        )).fetchall()
        current_runs: list[dict[str, Any]] = []
        for row in rows:
            run = json.loads(row["data"])
            if (run.get("episode_document_id") != document_id
                    or run.get("mode") != "actual" or run.get("status") != "completed"):
                continue
            context = run.get("context_revisions")
            if not isinstance(context, dict) or context.get(document_id) != revision_id:
                continue
            is_current = True
            for context_document_id, context_revision_id in context.items():
                head = await (await db.execute(
                    "SELECT current_revision_id FROM script_documents WHERE id=?",
                    (context_document_id,),
                )).fetchone()
                if head is None or head["current_revision_id"] != context_revision_id:
                    is_current = False
                    break
            if is_current:
                current_runs.append(run)

        if not current_runs:
            raise StrictHandoffReviewRequired(
                "a current completed actual consistency check is required"
            )

        for run in current_runs:
            issues = await (await db.execute(
                "SELECT data FROM script_consistency_issues WHERE run_id=?", (run["id"],)
            )).fetchall()
            if any(
                (issue := json.loads(item["data"])).get("category") == "fact"
                and not issue.get("proposal_id")
                and not str(issue.get("intentional_reason") or "").strip()
                for item in issues
            ):
                raise StrictHandoffReviewRequired(
                    "current consistency checks contain unresolved fact issues"
                )

        chosen = current_runs[0]
        references = {
            str(context_document_id): str(context_revision_id)
            for context_document_id, context_revision_id in chosen["context_revisions"].items()
            if context_document_id != document_id
        }
        return references, {"mode": "checked", "run_id": str(chosen["id"])}


__all__ = [
    "DirectorWorkspaceStore",
    "SceneBlocking",
    "SceneBlockingCompileConflict",
    "SceneBlockingConflict",
    "StrictHandoffReviewRequired",
    "compile_scene_blocking_candidate",
    "strict_handoff_context",
]
