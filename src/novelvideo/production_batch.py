"""Resumable multi-episode production using the public project APIs."""

from __future__ import annotations

from collections.abc import Callable
import re
from typing import Any


class BatchFailure(RuntimeError):
    """An actionable failure confined to a group or episode."""


def parse_episodes(value: str) -> list[int]:
    result: list[int] = []
    for part in value.split(","):
        match = re.fullmatch(r"\s*([1-9]\d*)(?:-([1-9]\d*))?\s*", part)
        if not match:
            raise ValueError("Use episode numbers/ranges such as 1-5,8")
        start, end = int(match[1]), int(match[2] or match[1])
        if end < start or end - start >= 1000 or end > 100000:
            raise ValueError("Episode ranges must be ascending and contain at most 1000 entries")
        result.extend(range(start, end + 1))
        if len(result) > 1000:
            raise ValueError("At most 1000 episode entries are supported per batch")
    return list(dict.fromkeys(result))


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise BatchFailure(f"invalid_response: expected {label}")
    return value


def _records(value: Any, label: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise BatchFailure(f"invalid_response: expected {label}")
    return value


class _Producer:
    def __init__(self, request, wait_task, emit, *, max_submissions, aspect_ratio, retry_failed):
        self.request = request
        self.wait_task = wait_task
        self.emit = emit
        self.max_submissions = max_submissions
        self.submissions = 0
        self.aspect_ratio = aspect_ratio
        self.retry_failed = retry_failed

    def read(self, path: str) -> Any:
        response = self.request("GET", path)
        if response.get("ok") is not True:
            raise BatchFailure("api_error: read failed")
        return response.get("data")

    def submit(self, path: str, body: dict | None = None, *, task: bool = True) -> dict:
        if self.submissions >= self.max_submissions:
            raise BatchFailure("submission_limit: batch submission limit reached")
        # Count attempts, including ambiguous transport failures. Never automatically retry POST.
        self.submissions += 1
        response = self.request("POST", path, body)
        if response.get("ok") is not True:
            raise BatchFailure("api_error: submission failed")
        if not task:
            return response
        data = response.get("data")
        task_id = response.get("task_id") or (data.get("task_id") if isinstance(data, dict) else None)
        if not task_id:
            raise BatchFailure("submission_unknown: API returned no task ID; inspect project tasks")
        self.emit({"event": "submitted", "path": path, "task_id": task_id})
        return self.wait_task(str(task_id))

    def prepare_plan(self, episode: int) -> None:
        root = f"episodes/{episode}/director-plans"
        plans = [plan for plan in _records(self.read(root), "director plans")
                 if not plan.get("source_stale")]
        incomplete = [plan for plan in plans if plan.get("production_ready") is False]
        plans = [plan for plan in plans if plan.get("production_ready") is not False]
        if any(plan.get("status") == "active" for plan in plans):
            return
        ready = [plan for plan in plans if plan.get("status") == "review_required"
                 and isinstance(plan.get("validation_report"), dict)
                 and plan["validation_report"].get("passed") is True]
        if ready:
            plan = ready[-1]
        else:
            tasks = _records(self.read("tasks"), "tasks")
            existing = [task for task in tasks if task.get("episode") == episode
                        and task.get("task_type") == "director_plan"
                        and task.get("status") in {"queued", "running", "pending"}]
            if incomplete and any(task.get("episode") == episode
                                  and task.get("status") in {"queued", "running", "pending"}
                                  and task.get("task_type") != "director_plan" for task in tasks):
                raise BatchFailure("production_directions_pending: wait for in-flight episode tasks before planning a new version")
            if len(existing) > 1:
                raise BatchFailure("ambiguous_task: multiple director tasks are active")
            if existing:
                outcome = self.wait_task(str(existing[0]["task_id"]))
            elif plans and not self.retry_failed:
                raise BatchFailure("plan_invalid: no machine-validated plan; fix reported issues or use --retry-failed")
            else:
                self.prepare_semantics(episode)
                outcome = self.submit(root)
            revision_id = _object(outcome.get("result"), "director result").get("revision_id")
            if not revision_id:
                raise BatchFailure("plan_invalid: director task returned no revision")
            plan = _object(self.read(f"{root}/{revision_id}"), "director revision")
        if _object(plan.get("validation_report"), "validation report").get("passed") is not True:
            raise BatchFailure("plan_invalid: machine validation failed")
        if plan.get("production_ready") is False:
            raise BatchFailure("production_directions_required: new plan still lacks blocking or lighting")
        self.submit(f"{root}/{plan['revision_id']}/activate", task=False)

    def prepare_semantics(self, episode: int) -> None:
        root = f"episodes/{episode}/screenplay-semantics"
        state = _object(self.read(root), "screenplay semantics")
        if state.get("active_revision_id"):
            return
        outcome = self.submit(root, {})
        revision_id = _object(outcome.get("result"), "semantic result").get("semantic_revision_id")
        if not revision_id:
            raise BatchFailure("semantic_invalid: task returned no semantic revision")
        self.submit(f"{root}/{revision_id}/activate", task=False)

    def groups(self, episode: int) -> list[dict]:
        return _records(self.read(f"episodes/{episode}/narrative-groups"), "narrative groups")

    def group(self, episode: int, group_id: str) -> dict:
        group = next((item for item in self.groups(episode) if item.get("id") == group_id), None)
        if group is None:
            raise BatchFailure("group_changed: group is no longer in the active plan")
        return group

    @staticmethod
    def usable(state: dict, stage: str) -> bool:
        return (state.get("status") == "completed" and not state.get("needs_regeneration")
                and bool(state.get("video_asset") if stage == "video"
                         else state.get("grid_asset") and state.get("cell_assets")))

    def image_body(self, root: str, stage: str) -> dict:
        preview = _object(self.read(f"{root}/{stage}/references"), "reference preview")
        bindings = _records(preview.get("bindings"), "reference bindings")
        missing = [item.get("binding_id") for item in bindings
                   if item.get("required") and item.get("status") != "ready"]
        if missing:
            raise BatchFailure(f"missing_required_assets: {','.join(map(str, missing))}")
        selected = [item["binding_id"] for item in bindings
                    if item.get("status") == "ready"
                    and (item.get("required") or item.get("selected_by_default"))]
        if len(selected) > int(preview.get("max_images", 9)):
            raise BatchFailure("reference_limit: planned references exceed model capacity")
        revision = preview.get("reference_revision")
        if not revision:
            raise BatchFailure("reference_revision_missing: refresh reference planning")
        return {"aspect_ratio": self.aspect_ratio, "allow_unconstrained": True,
                "reference_resolution": {"reference_revision": revision,
                                         "selected_binding_ids": selected, "upload_ids": []}}

    def stage(self, episode: int, group_id: str, stage: str, *, force: bool = False) -> str:
        group = self.group(episode, group_id)
        state = _object(_object(group.get("stages"), "stages").get(stage), "stage")
        if self.usable(state, stage) and not force:
            return "reused"
        root = f"episodes/{episode}/narrative-groups/{group_id}"
        status = state.get("status")
        if status in {"queued", "running"}:
            scope = f"group_{group_id}_{stage}_r{state.get('revision', 0)}"
            task_type = "narrative_group_video" if stage == "video" else "narrative_group_grid"
            tasks = _records(self.read("tasks"), "tasks")
            matches = [task for task in tasks if task.get("episode") == episode
                       and task.get("scope") == scope
                       and task.get("task_type") in {task_type, "narrative_group_split"}]
            active = [task for task in matches if task.get("status") in {"pending", "queued", "running"}]
            matches = active or matches
            if len(matches) != 1 or not matches[0].get("task_id"):
                raise BatchFailure("task_unresolved: inspect existing task; no duplicate was submitted")
            self.wait_task(str(matches[0]["task_id"]))
        elif stage != "video" and status == "partial_failure" and state.get("grid_asset"):
            # Splitting reuses the paid grid; never regenerate an image just to repair slicing.
            self.submit(f"{root}/{stage}/split")
        else:
            if status in {"failed", "partial_failure"} and not self.retry_failed:
                raise BatchFailure("previous_failure: use --retry-failed for one explicit retry")
            if status not in {"pending", "completed", "failed", "partial_failure"}:
                raise BatchFailure(f"stage_not_ready: {status}")
            if stage == "video":
                plan = _object(group.get("video_plan"), "video plan")
                if not plan.get("revision") or not plan.get("units"):
                    raise BatchFailure("video_plan_missing: no recommended video plan")
                settings = _object(group.get("video_settings", {}), "video settings")
                references = _object(group.get("video_reference_settings", {}), "video references")
                body = {"model": settings.get("workflow_id") or "runninghub:minimax-h3",
                        "mode": "auto", "aspect_ratio": self.aspect_ratio,
                        "revision": state.get("revision", 0), "plan_revision": plan["revision"],
                        "settings_revision": settings.get("revision", 0),
                        "reference_revision": references.get("revision", 0)}
                action = "generate"
            else:
                body = self.image_body(root, stage)
                action = "regenerate" if state.get("revision", 0) else "generate"
            self.submit(f"{root}/{stage}/{action}", body)
        current = _object(self.group(episode, group_id)["stages"][stage], "stage")
        if not self.usable(current, stage):
            raise BatchFailure("artifact_not_ready: task completed without current usable media")
        return "completed"


def run_batch(
    *, request: Callable, wait_task: Callable, episodes: list[int],
    through: str = "compose", with_sketch: bool = False,
    max_submissions: int = 100, aspect_ratio: str = "9:16",
    retry_failed: bool = False, emit: Callable = lambda _event: None,
) -> dict[str, Any]:
    """Run each independent group; collect failures and compose only complete episodes."""
    if through not in {"render", "video", "compose"} or aspect_ratio not in {"9:16", "16:9"}:
        raise ValueError("Unsupported stage or aspect ratio")
    producer = _Producer(request, wait_task, emit, max_submissions=max_submissions,
                         aspect_ratio=aspect_ratio, retry_failed=retry_failed)
    results = []
    stages = (["sketch"] if with_sketch else []) + ["render"]
    if through != "render":
        stages.append("video")
    for episode in episodes:
        item: dict[str, Any] = {"episode": episode, "status": "completed", "groups": []}
        try:
            producer.prepare_plan(episode)
            groups = producer.groups(episode)
            if not groups:
                raise BatchFailure("groups_missing: active plan contains no groups")
            for group in groups:
                group_id = str(group["id"])
                group_result: dict[str, Any] = {"group_id": group_id, "stages": {}}
                try:
                    upstream_changed = False
                    for stage in stages:
                        group_result["stages"][stage] = producer.stage(
                            episode, group_id, stage, force=upstream_changed,
                        )
                        upstream_changed = group_result["stages"][stage] == "completed"
                        emit({"event": "stage", "episode": episode, "group_id": group_id,
                              "stage": stage, "status": group_result["stages"][stage]})
                except BatchFailure as exc:
                    group_result["error"] = str(exc)
                    item["status"] = "blocked"
                item["groups"].append(group_result)
            if through == "compose" and item["status"] == "completed":
                # The final API has no source revision fingerprint. Recompose from current clips
                # even if an older final exists, so interrupted runs cannot return stale exports.
                producer.submit(f"episodes/{episode}/videos/compose", {
                    "resolution": "720x1280" if aspect_ratio == "9:16" else "1280x720",
                })
                final = _object(producer.read(f"episodes/{episode}/final"), "final video")
                if final.get("exists") is not True or not final.get("video_url"):
                    raise BatchFailure("final_missing: composition produced no downloadable video")
                item["video_url"] = final["video_url"]
        except BatchFailure as exc:
            item.update(status="blocked", error=str(exc))
        results.append(item)
        emit({"event": "episode", **item})
    return {"ok": all(item["status"] == "completed" for item in results),
            "submissions": producer.submissions, "episodes": results}
