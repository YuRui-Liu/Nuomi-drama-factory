"""RunningHub transport for frozen canvas Director attempts."""

from __future__ import annotations

from pathlib import Path

from novelvideo.api.deps import get_media_capability_store, get_media_credential_resolver
from novelvideo.media_capabilities.models import MediaCapability, RunningHubWorkflowSettingsKey
from novelvideo.media_capabilities.runtime.compiler import compile_node_info
from novelvideo.media_capabilities.runtime.configuration import load_runninghub_runtime_configuration
from novelvideo.media_capabilities.video.runtime import get_h3_concurrency_coordinator, load_h3_workflow_profile
from novelvideo.media_capabilities.video.workflow_registry import load_h3_reference_workflow_profile

from .h3_adapter import compile_director_payload


class RunningHubDirectorProvider:
    def __init__(self, ctx, *, runtime=None, coordinator=None):
        self.ctx = ctx
        self.runtime = runtime or load_runninghub_runtime_configuration(
            get_media_capability_store(), get_media_credential_resolver())
        self.coordinator = coordinator or get_h3_concurrency_coordinator(self.runtime.account.id)
        account = self.runtime.account
        self.coordinator.configure(account.id, account.max_concurrency,
                                   account.capability_limits, account.queue_limit)
        self.capability = MediaCapability.VIDEO_I2VA

    async def prepare(self, draft, optimized, paths: dict[str, Path], reference_limit: int):
        if optimized.route == "h3_ref":
            workflow_id = self.runtime.workflow_id_for_key(RunningHubWorkflowSettingsKey.VIDEO_MINIMAX_H3_REF)
            profile = load_h3_reference_workflow_profile(workflow_id=workflow_id)
            capability = MediaCapability.VIDEO_REF2VA
        else:
            capability = (MediaCapability.VIDEO_FL2VA if any(s.last_frame for s in draft.segments)
                          else MediaCapability.VIDEO_I2VA)
            workflow_id = self.runtime.workflow_id(capability)
            profile = load_h3_workflow_profile(workflow_id=workflow_id)
        self.capability = capability
        uploads = {}
        async with self.coordinator.lease(self.runtime.account.id, capability):
            async with self.runtime.create_client() as client:
                for image_id, path in paths.items():
                    uploads[image_id] = {"imageFile": await client.upload(path)}
        payload = compile_director_payload(draft, optimized, uploads, reference_limit=reference_limit)
        semantic_values = {"timeline_data": payload.timeline_data}
        semantic_values.update({key: value for key, value in payload.semantic_values.items()
                                if key in profile.bindings})
        return {
            "workflow_id": workflow_id,
            "profile_id": profile.id,
            "profile_version": profile.version,
            "node_info": compile_node_info(profile, semantic_values),
            "capability": capability,
        }

    def set_route(self, route, draft):
        self.capability = (MediaCapability.VIDEO_REF2VA if route == "h3_ref" else
                           MediaCapability.VIDEO_FL2VA if any(s.last_frame for s in draft.segments) else
                           MediaCapability.VIDEO_I2VA)

    async def submit(self, prepared):
        async with self.coordinator.lease(self.runtime.account.id, prepared["capability"]):
            async with self.runtime.create_client() as client:
                return await client.submit(prepared["workflow_id"], prepared["node_info"])

    async def query(self, task_id):
        async with self.coordinator.lease(self.runtime.account.id, self.capability):
            async with self.runtime.create_client() as client:
                return await client.query(task_id)

    async def download(self, url):
        async with self.coordinator.lease(self.runtime.account.id, self.capability):
            async with self.runtime.create_client() as client:
                return await client.download(url)
