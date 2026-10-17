"""Durable dependency scheduler. External submission is never blindly replayed."""

from __future__ import annotations

import asyncio
from copy import deepcopy
import inspect

from novelvideo.freezone.canvas_lock import canvas_write_lock
from .capabilities import catalog_by_type
from ..canvas_store import read_canvas
from .models import (OrchestrationError, RESULT_FIELDS, dependency_edge_fingerprint, fingerprint,
                     frozen_node_result, input_fingerprint, media_projection, result_projection)
from .store import RunStore, TERMINAL


class Scheduler:
    def __init__(self, store: RunStore, adapter, *, authorize_submission=None,
                 authorize_projection=None):
        self.store, self.adapter = store, adapter
        self.authorize_submission = authorize_submission
        self.authorize_projection = authorize_projection

    async def _authorize_agent_submission(self, run, attempt):
        marker = run.get("agent_execution")
        if not marker:
            return
        target = marker.get("target_node_id")
        if attempt["node_id"] != target:
            raise OrchestrationError("scope_violation", "Agent runs may submit only their authorized target")
        if self.authorize_submission is None:
            raise OrchestrationError("authorization_unavailable", "Agent execution authorization is unavailable")
        node = next((n for n in run["snapshot"]["nodes"] if n["id"] == target), None)
        if node is None:
            raise OrchestrationError("node_not_found", "Agent run target no longer exists")
        operation = node.get("data", {}).get("operation") or catalog_by_type()[node["type"]]["default_operation"]
        if operation != marker.get("operation"):
            raise OrchestrationError("input_conflict", "Authorized node operation changed")
        current = read_canvas(self.store.project_dir, run["canvas_id"])
        current_node = next((n for n in (current or {}).get("nodes", []) if n["id"] == target), None)
        if current_node is None or input_fingerprint(current_node) != run["input_fingerprints"].get(target):
            raise OrchestrationError("input_conflict", "Node inputs changed before submission", node_id=target,
                                     current_result_fingerprint=fingerprint(result_projection(current_node))
                                     if current_node else None,
                                     current_media_fingerprint=fingerprint(media_projection(current_node))
                                     if current_node else None,
                                     current_media=media_projection(current_node) if current_node else {})
        already_projected = self.agent_projection_matches(run, attempt, current, current_node)
        if (not already_projected
                and (fingerprint(result_projection(current_node)) != marker.get("target_result_fingerprint")
                     or fingerprint(media_projection(current_node)) != marker.get("target_media_fingerprint"))):
            raise OrchestrationError("input_conflict", "Node output changed before submission", node_id=target,
                                     current_result_fingerprint=fingerprint(result_projection(current_node)),
                                     current_media_fingerprint=fingerprint(media_projection(current_node)),
                                     current_media=media_projection(current_node))
        if dependency_edge_fingerprint(current, target) != marker.get("dependency_edge_fingerprint"):
            raise OrchestrationError("input_conflict", "Node dependency edges changed before submission", node_id=target)
        current_nodes = {node["id"]: node for node in (current or {}).get("nodes", [])}
        for source_id, expected in marker.get("source_output_fingerprints", {}).items():
            source = current_nodes.get(source_id)
            if source is None or fingerprint(frozen_node_result(source)) != expected:
                raise OrchestrationError("input_conflict", "A frozen upstream input changed before submission",
                                         node_id=source_id)
        try:
            await self.authorize_submission(run, attempt)
        except OrchestrationError:
            raise
        except Exception as exc:
            raise OrchestrationError("authorization_unavailable", "Could not recheck agent execution authorization") from exc

    @staticmethod
    def agent_projection_matches(run, attempt, canvas, node) -> bool:
        """Whether live canvas still contains this attempt's verified projection."""
        marker = run.get("agent_execution")
        if not marker or node is None or canvas is None:
            return False
        target = marker.get("target_node_id")
        orchestration = node.get("data", {}).get("orchestration") or {}
        if (orchestration.get("attempt_id") != attempt.get("id")
                or input_fingerprint(node) != run.get("input_fingerprints", {}).get(target)
                or orchestration.get("projected_result_fingerprint")
                != fingerprint(result_projection(node))
                or orchestration.get("projected_media_fingerprint")
                != fingerprint(media_projection(node))):
            return False
        snapshot_node = next((item for item in run.get("snapshot", {}).get("nodes", [])
                              if item.get("id") == target), None)
        if snapshot_node is None or snapshot_node.get("type") != node.get("type"):
            return False
        expected_result = deepcopy(result_projection(snapshot_node))
        allowed = RESULT_FIELDS.get(snapshot_node.get("type"), set())
        expected_result.update({key: deepcopy(value) for key, value in (attempt.get("result") or {}).items()
                                if key in allowed})
        if fingerprint(result_projection(node)) != fingerprint(expected_result):
            return False
        if dependency_edge_fingerprint(canvas, target) != marker.get("dependency_edge_fingerprint"):
            return False
        current_nodes = {item["id"]: item for item in canvas.get("nodes", [])}
        return all(
            source_id in current_nodes
            and fingerprint(frozen_node_result(current_nodes[source_id])) == expected
            for source_id, expected in marker.get("source_output_fingerprints", {}).items())

    def _external_submission(self, run, attempt) -> bool:
        method = getattr(self.adapter, "requires_external_submission", None)
        return bool(method(run, attempt)) if method else True

    async def _project_agent_result(self, run, attempt):
        if self.authorize_projection is None:
            raise OrchestrationError("authorization_unavailable",
                                     "Agent canvas projection authorization is unavailable")
        # The async live resolver runs before acquiring RunStore/canvas locks.
        # The synchronous fence below runs from save_canvas's CAS builder while
        # holding the canvas lock, serializing against host stop/revocation.
        project = self.adapter.project
        try:
            supports_fence = "authorize_projection" in inspect.signature(project).parameters
        except (TypeError, ValueError):
            supports_fence = False
        if not supports_fence:
            raise OrchestrationError("authorization_unavailable",
                                     "Node adapter cannot enforce the projection authorization fence")
        def fence():
            try:
                result = self.authorize_projection(run, attempt)
            except OrchestrationError:
                raise
            except Exception as exc:
                raise OrchestrationError("authorization_revoked",
                                         "Agent execution authorization changed before projection") from exc
            if result is False:
                raise OrchestrationError("authorization_revoked",
                                         "Agent execution authorization changed before projection")
            return result
        return await project(run, attempt, authorize_projection=fence)

    async def tick(self, run_id: str) -> dict:
        run = self.store.get(run_id)
        if run["status"] in TERMINAL:
            return run
        # Separate from the canvas save lock: serialize ticks across processes
        # while allowing result projection to use the regular canvas lock.
        with canvas_write_lock(self.store.root, 'run-' + run_id, timeout_seconds=0):
            return await self._tick(self.store.get(run_id))

    async def _tick(self, run):
        latest = {key: attempts[-1] for key, attempts in run["attempts"].items()}
        dependencies = {key: [] for key in latest}
        for edge in run["snapshot"]["edges"]:
            if (edge.get("data") or {}).get("contextLink"):
                continue
            if edge["target"] in latest and edge["source"] in latest:
                dependencies[edge["target"]].append(edge["source"])
        run["status"] = "running"
        for key in run["order"]:
            attempt = latest[key]
            marker = run.get("agent_execution")
            state = attempt["status"]
            if state == "submission_unknown" and hasattr(self.adapter, "reconcile_submission"):
                evidence = await self.adapter.reconcile_submission(run, attempt)
                attempt["provider_status"] = evidence.get("status")
                if evidence.get('status') == 'submitted' and isinstance(evidence.get('submission'), dict) and any(
                    evidence['submission'].get(key) for key in (
                        'task_id', 'candidate_id', 'dubbing_task_id', 'workflow_id',
                        'preparation_id', 'receipt_key',
                    )
                ):
                    # A durable operation receipt recovers the original
                    # submission without replaying its side effect.
                    attempt.update(status='running', submission=evidence['submission'], error=None)
                    state = 'running'
                    self.store.save(run, 'submission_reconciled_accepted')
                elif evidence["status"] == "not_submitted":
                    marker = run.get("agent_execution")
                    if marker:
                        # Explicit evidence that no durable operation receipt
                        # exists permits retrying this stable attempt.
                        self.store.release_node_run(marker.get("root_delegation_id", ""), attempt["id"],
                                                    definitive_not_submitted=True)
                    attempt.update(status="pending", error={"code": "capacity_wait", "evidence": evidence["evidence"]})
                    self.store.save(run, "submission_reconciled_not_accepted")
            if state == "submitting":
                # A crashed process persisted intent but no response. Adapter
                # reconciliation may resolve this later; never call submit again.
                attempt.update(status="submission_unknown", error={"code": "submission_unknown"})
            if state == "running":
                try:
                    response = await self.adapter.reconcile(run, attempt)
                    attempt["provider_status"] = response.get("status")
                    if response["status"] == "succeeded":
                        attempt.update(status="verifying", result=response.get("result"), error=None)
                    elif response["status"] in {"failed", "cancelled", "submission_unknown"}:
                        attempt.update(status=response["status"], error=response.get("error"))
                        if response["status"] == "cancelled":
                            attempt["cancel_reason"] = "provider_cancelled"
                    self.store.save(run, "reconciled")
                except Exception as exc:
                    # A read failure is not evidence that a provider task failed.
                    attempt["error"] = {"code": "reconcile_error", "message": str(exc)}
                    self.store.save(run, "reconcile_error")
            if attempt["status"] == "verifying":
                try:
                    if not attempt["artifacts"]:
                        attempt["artifacts"] = await self.adapter.verify(run, attempt)
                        self.store.save(run, "verified")
                    if self.store.get(run["id"])["cancel_requested"]:
                        # Reconcile and verify accepted provider work even after
                        # local stop, but keep its result for recovery without
                        # adopting it into the live canvas.
                        attempt.update(status="cancelled", cancel_reason="adoption_stopped",
                                       error={"code": "adoption_stopped"})
                        self.store.save(run, "cancelled_after_verification")
                        continue
                    if marker:
                        # Resolve async AgentRuns/delegation state before taking
                        # the RunStore transaction; the sync projection fence
                        # below closes the race at the canvas CAS itself.
                        await self._authorize_agent_submission(run, attempt)
                        # Stop and projection are serialized by the durable
                        # RunStore write transaction; the adapter also checks
                        # live authorization inside the canvas CAS/save lock.
                        with self.store.adoption_fence(run["id"]) as (db, current_run):
                            if current_run["cancel_requested"]:
                                latest_attempt = current_run["attempts"][key][-1]
                                latest_attempt.update(status="cancelled", cancel_reason="adoption_stopped",
                                                      error={"code": "adoption_stopped"})
                                current_run["status"] = "cancelled"
                                self.store._write(db, current_run, "cancelled_after_verification")
                                run.update(current_run)
                                continue
                            await self._project_agent_result(run, attempt)
                            attempt.update(status="succeeded", error=None)
                            run["status"] = "succeeded"
                            self.store._write(db, run, "projected")
                    else:
                        await self.adapter.project(run, attempt)
                        attempt.update(status="succeeded", error=None)
                        self.store.save(run, "projected")
                except OrchestrationError as exc:
                    attempt["error"] = exc.as_dict()
                    if exc.code == "invalid_artifact":
                        attempt["status"] = "failed"
                    self.store.save(run, "verification_blocked")
                except Exception as exc:
                    attempt["error"] = {"code": "projection_error", "message": str(exc)}
                    self.store.save(run, "projection_blocked")

        # Reload cancellation before each new external submission.
        run["cancel_requested"] = self.store.get(run["id"])["cancel_requested"]
        limit = max(1, int(run["settings"].get("concurrency", 1)))
        active = sum(a["status"] in {"submitting", "running"} for a in latest.values())
        for key in run["order"]:
            attempt = latest[key]
            if attempt["status"] not in {"pending", "blocked"}:
                continue
            if run["cancel_requested"]:
                attempt.update(status="cancelled", cancel_reason="cancelled_before_submit")
                continue
            upstream = [latest[source] for source in dependencies[key]]
            if run.get("agent_execution") and key == run["agent_execution"].get("target_node_id"):
                upstream = deepcopy(run["agent_execution"].get("frozen_inputs", upstream))
            elif any(a["status"] in {"failed", "blocked", "submission_unknown", "cancelled"} for a in upstream):
                attempt.update(status="blocked", error={"code": "dependency_failed"})
                continue
            frozen_agent_inputs = bool(run.get("agent_execution")
                                       and key == run["agent_execution"].get("target_node_id"))
            if (not frozen_agent_inputs and not all(a["status"] == "succeeded" for a in upstream)) or active >= limit:
                continue
            run["cancel_requested"] = self.store.get(run["id"])["cancel_requested"]
            if run["cancel_requested"]:
                attempt.update(status="cancelled", cancel_reason="cancelled_before_submit")
                continue
            marker = run.get("agent_execution")
            external = False
            reserved = False
            if marker:
                try:
                    # Check the trusted grant before touching the durable
                    # admission counter, then check once more at the submit edge.
                    await self._authorize_agent_submission(run, attempt)
                    external = self._external_submission(run, attempt)
                    if external:
                        reserved = self.store.reserve_node_run(
                            marker.get("root_delegation_id", ""), attempt["id"],
                            delegation_id=marker.get("delegation_id", ""),
                            root_limit=marker.get("root_max_node_runs"),
                            delegation_limit=marker.get("max_node_runs"),
                            budget_chain=marker.get("node_run_budget_chain"))
                        if not reserved:
                            # A durable reservation for this same attempt may
                            # mean submit was called before the process died.
                            # Reconcile it instead of submitting again.
                            attempt.update(status="submission_unknown",
                                           error={"code": "submission_unknown",
                                                  "reason": "existing_attempt_reservation"})
                            self.store.save(run, "existing_attempt_reservation")
                            continue
                except OrchestrationError as exc:
                    attempt.update(status="blocked", error=exc.as_dict())
                    self.store.save(run, "agent_submission_blocked")
                    continue
            attempt["status"] = "submitting"
            self.store.save(run, "submission_intent")
            try:
                if marker:
                    await self._authorize_agent_submission(run, attempt)
                    # stop_run may race with the second live authorization
                    # check. Re-read cancellation at the exact submit edge and
                    # release the reservation if no provider request began.
                    if self.store.get(run["id"])["cancel_requested"]:
                        if external and reserved:
                            self.store.release_node_run(marker["root_delegation_id"], attempt["id"])
                        attempt.update(status="cancelled", cancel_reason="cancelled_before_submit",
                                       error={"code": "cancelled_before_submit"})
                        self.store.save(run, "cancelled_before_submit")
                        continue
                attempt["submission"] = await self.adapter.submit(run, attempt, upstream)
                attempt["status"] = "running"
                active += 1
                if marker and external:
                    self.store.consume_node_run(marker["root_delegation_id"], attempt["id"])
            except OrchestrationError as exc:
                release = marker and reserved and exc.code in {
                    "capacity_wait", "request_rejected", "invalid_input", "input_required",
                    "authorization_revoked", "authorization_unavailable", "input_conflict",
                    "scope_violation",
                }
                if release:
                    self.store.release_node_run(marker["root_delegation_id"], attempt["id"])
                status = "pending" if exc.code == "capacity_wait" else "blocked" if marker and (
                    exc.code.startswith("authorization_") or exc.code in {"input_conflict", "scope_violation", "node_run_budget_exhausted"}
                ) else "failed"
                attempt.update(status=status, error=exc.as_dict())
            except Exception as exc:
                if marker and external:
                    # The provider response may have been lost after acceptance.
                    self.store.consume_node_run(marker["root_delegation_id"], attempt["id"])
                attempt.update(status="submission_unknown", error={"code": "submission_unknown", "message": str(exc)})
            self.store.save(run, "submitted")

        states = {a["status"] for a in latest.values()}
        if states <= {"succeeded"}:
            run["status"] = "succeeded"
        elif run["cancel_requested"] and not states.intersection({"running", "submitting", "verifying", "submission_unknown"}):
            run["status"] = "cancelled"
        elif states.intersection({"running", "pending", "submitting"}):
            run["status"] = "cancel_requested" if run["cancel_requested"] else "running"
        elif "submission_unknown" in states:
            run["status"] = "submission_unknown"
        elif "verifying" in states:
            run["status"] = "blocked"
        elif "failed" in states:
            run["status"] = "failed"
        else:
            run["status"] = "blocked"
        return self.store.save(run, "tick")

    async def drive(self, run_id: str, *, poll_seconds: float = 2.0) -> dict:
        while True:
            run = await self.tick(run_id)
            if run["status"] in TERMINAL | {"blocked", "submission_unknown"}:
                return run
            await asyncio.sleep(poll_seconds)
