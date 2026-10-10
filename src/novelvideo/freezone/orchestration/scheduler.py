"""Durable dependency scheduler. External submission is never blindly replayed."""

from __future__ import annotations

import asyncio

from novelvideo.freezone.canvas_lock import canvas_write_lock
from .capabilities import catalog_by_type
from .models import OrchestrationError
from .store import RunStore, TERMINAL


class Scheduler:
    def __init__(self, store: RunStore, adapter):
        self.store, self.adapter = store, adapter

    async def tick(self, run_id: str) -> dict:
        run = self.store.get(run_id)
        if run["status"] in TERMINAL:
            return run
        # Separate from the canvas save lock: serialize ticks across processes
        # while allowing result projection to use the regular canvas lock.
        with canvas_write_lock(self.store.root, run["canvas_id"], timeout_seconds=0):
            return await self._tick(self.store.get(run_id))

    async def _tick(self, run):
        latest = {key: attempts[-1] for key, attempts in run["attempts"].items()}
        dependencies = {key: [] for key in latest}
        for edge in run["snapshot"]["edges"]:
            if edge["target"] in latest and edge["source"] in latest:
                dependencies[edge["target"]].append(edge["source"])
        run["status"] = "running"
        for key in run["order"]:
            attempt = latest[key]
            state = attempt["status"]
            if state == "submission_unknown" and hasattr(self.adapter, "reconcile_submission"):
                evidence = await self.adapter.reconcile_submission(run, attempt)
                if evidence["status"] == "not_submitted":
                    attempt.update(status="pending", error={"code": "capacity_wait", "evidence": evidence["evidence"]})
                    self.store.save(run, "submission_reconciled_not_accepted")
            if state == "submitting":
                # A crashed process persisted intent but no response. Adapter
                # reconciliation may resolve this later; never call submit again.
                attempt.update(status="submission_unknown", error={"code": "submission_unknown"})
            if state == "running":
                try:
                    response = await self.adapter.reconcile(run, attempt)
                    if response["status"] == "succeeded":
                        attempt.update(status="verifying", result=response.get("result"), error=None)
                    elif response["status"] in {"failed", "cancelled", "submission_unknown"}:
                        attempt.update(status=response["status"], error=response.get("error"))
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
                attempt["status"] = "cancelled"
                continue
            upstream = [latest[source] for source in dependencies[key]]
            if any(a["status"] in {"failed", "blocked", "submission_unknown", "cancelled"} for a in upstream):
                attempt.update(status="blocked", error={"code": "dependency_failed"})
                continue
            if not all(a["status"] == "succeeded" for a in upstream) or active >= limit:
                continue
            run["cancel_requested"] = self.store.get(run["id"])["cancel_requested"]
            if run["cancel_requested"]:
                attempt["status"] = "cancelled"
                continue
            attempt["status"] = "submitting"
            self.store.save(run, "submission_intent")
            try:
                attempt["submission"] = await self.adapter.submit(run, attempt, upstream)
                attempt["status"] = "running"
                active += 1
            except OrchestrationError as exc:
                attempt.update(status="pending" if exc.code == "capacity_wait" else "failed", error=exc.as_dict())
            except Exception as exc:
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
