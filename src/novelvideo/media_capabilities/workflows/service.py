"""Public asynchronous service built on the existing durable media executor."""
from __future__ import annotations
import asyncio
from uuid import uuid4
from novelvideo.media_capabilities.models import WorkflowProfile, MediaCapability, MediaTaskStatus
from novelvideo.media_capabilities.runtime.executor import RunningHubExecutor
from novelvideo.media_capabilities.task_store import TaskNotFoundError
from .models import WorkflowManifest, WorkflowError, RunResult
from .compiler import normalize_inputs, compile_bindings
from .graph import validate_deployed_graph, validate_bindings
from .adapters import get_adapter
from .files import resolve_files


class WorkflowService:
    def __init__(self, registry, store, client, artifacts, concurrency, *, account_id: str, artifact_resolver=None):
        self.registry, self.store, self.client = registry, store, client
        self.account_id, self.artifact_resolver = account_id, artifact_resolver
        self.executor = RunningHubExecutor(store, client, artifacts, concurrency)

    def status(self, run_id: str) -> RunResult:
        task = self.store.get_task(run_id)
        if task is None:
            raise TaskNotFoundError(run_id)
        attempts = self.store.list_attempts(run_id)
        return RunResult(run_id=run_id, provider_task_id=attempts[-1].provider_task_id if attempts else None,
                         status=task.status.value, artifacts=task.output.get("named_artifacts", {}))

    async def validate(self, identifier: str, version: int, *, remote: bool = False) -> dict:
        manifest, graph = self.registry.get(identifier, version)
        validate_bindings(manifest, graph)
        if remote:
            validate_deployed_graph(manifest, graph, await self.client.workflow_json(manifest.workflow_id))
        return {"valid": True, "remote_checked": remote}

    async def run(self, identifier: str, version: int, inputs: dict, *, idempotency_key: str | None = None,
                  capability: MediaCapability = MediaCapability.WORKFLOW_EXECUTE, wait: bool = False) -> RunResult:
        manifest, graph = self.registry.get(identifier, version)
        if manifest.status != "active":
            raise WorkflowError("inactive_workflow")
        normalized = normalize_inputs(manifest.inputs, inputs)
        snapshot = {"manifest": manifest.snapshot(), "graph": graph, "account_id": self.account_id}
        task = self.store.create_task(capability, idempotency_key or uuid4().hex, snapshot, normalized)
        attempts = self.store.list_attempts(task.id)
        if not attempts:
            self.store.start_attempt(task.id, self.account_id, workflow_version={"id": manifest.id, "version": manifest.version})
            result = await self.resume(task.id)
        else:
            result = self.status(task.id)
        return await self.wait(task.id) if wait else result

    async def resume(self, run_id: str) -> RunResult:
        task = self.store.get_task(run_id)
        if task is None:
            raise TaskNotFoundError(run_id)
        if task.status in {MediaTaskStatus.SUCCEEDED, MediaTaskStatus.FAILED, MediaTaskStatus.UNKNOWN,
                            MediaTaskStatus.CANCELLED, MediaTaskStatus.QUALITY_FAILED}:
            return self.status(run_id)
        snapshot = task.implementation_snapshot
        if snapshot.get("account_id") != self.account_id:
            raise WorkflowError("account_mismatch")
        manifest = WorkflowManifest.model_validate(snapshot["manifest"])
        graph = snapshot["graph"]
        attempts = self.store.list_attempts(run_id)
        if not attempts:
            self.store.start_attempt(run_id, self.account_id)
            attempts = self.store.list_attempts(run_id)
        attempt = attempts[-1]
        checkpoint = self.store.get_checkpoint(attempt.id)
        if "node_info" not in checkpoint and not attempt.provider_task_id:
            validate_deployed_graph(manifest, graph, await self.client.workflow_json(manifest.workflow_id))
            adapter = get_adapter(manifest.adapter)
            prepared = adapter.prepare(task.input_snapshot)
            rules = dict(manifest.inputs)
            # Adapter file declarations carry the same strict input descriptors.
            from .models import InputDefinition
            file_values = dict(prepared.values)
            for name, declaration in prepared.files.items():
                rules[name] = InputDefinition.model_validate(declaration["rule"])
                file_values[name] = declaration["value"]
            uploaded = await resolve_files(rules, file_values, self.client, self.account_id, checkpoint,
                lambda updates: self.store.save_checkpoint(attempt.id, updates), artifact_resolver=self.artifact_resolver)
            values = adapter.compile(prepared, uploaded)
            nodes = compile_bindings(manifest, graph, values)
            checkpoint = self.store.save_checkpoint(attempt.id, {"node_info": nodes})
        nodes = checkpoint.get("node_info", [])
        # Reuse the existing submission claim, cost capture, status and cancellation path.
        bindings = {f"input_{i}": {"node_id": n["nodeId"], "field": n["fieldName"]} for i, n in enumerate(nodes)}
        values = {f"input_{i}": n["fieldValue"] for i, n in enumerate(nodes)}
        profile = WorkflowProfile(id=manifest.id, version=manifest.version, workflow_id=manifest.workflow_id,
                                  bindings=bindings, manifest=manifest.snapshot())
        try:
            await self.executor.step(run_id, profile=profile, semantic_values=values)
        except Exception:
            if self.status(run_id).status != "unknown":
                raise
        return self.status(run_id)

    async def wait(self, run_id: str, *, poll_interval: float = 3.0, timeout: float = 1800) -> RunResult:
        async with asyncio.timeout(timeout):
            while True:
                result = self.status(run_id)
                if result.status in {"succeeded", "failed", "unknown", "cancelled", "quality_failed"}:
                    return result
                await asyncio.sleep(poll_interval)
                await self.resume(run_id)

    async def cancel(self, run_id: str) -> RunResult:
        await self.executor.cancel(run_id)
        return self.status(run_id)

    async def retry(self, run_id: str) -> RunResult:
        task = self.store.get_task(run_id)
        if task is None or task.status not in {MediaTaskStatus.FAILED, MediaTaskStatus.QUALITY_FAILED}:
            raise WorkflowError("retry_not_allowed")
        if task.implementation_snapshot.get("account_id") != self.account_id:
            raise WorkflowError("account_mismatch")
        self.store.start_retry_attempt(run_id, self.account_id)
        return await self.resume(run_id)
