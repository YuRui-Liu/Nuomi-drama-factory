"""Register immutable packages in the existing media capability store."""

from __future__ import annotations
from pathlib import Path
from novelvideo.media_capabilities.models import WorkflowProfile, MediaCapability
from novelvideo.media_capabilities.store import MediaCapabilityStore, StoreConflictError
from .models import WorkflowManifest, WorkflowError
from .importer import load_package


class WorkflowRegistry:
    def __init__(self, store: MediaCapabilityStore):
        self.store = store
        from .builtin_adapters import install_builtin_adapters

        install_builtin_adapters()

    def register(self, directory: str | Path) -> WorkflowManifest:
        manifest, graph = load_package(directory)
        if manifest.status != "active":
            raise WorkflowError("inactive_package")
        from .adapters import get_adapter

        get_adapter(manifest.adapter)
        for integration in manifest.integrations:
            from .adapters import validate_integration

            validate_integration(integration, manifest)
            if integration.adapter.id == "h3-reference":
                from .builtin_adapters import validate_builtin_graph

                validate_builtin_graph("h3-reference", graph)
        profile = WorkflowProfile(
            id=manifest.id,
            version=manifest.version,
            provider="runninghub",
            workflow_id=manifest.workflow_id,
            manifest=manifest.snapshot(),
            source_graph=graph,
            source_sha256=manifest.source.sha256,
            capabilities=[MediaCapability.WORKFLOW_EXECUTE],
            outputs={k: v.model_dump() for k, v in manifest.outputs.items()},
        )
        existing = self.store.get_workflow(manifest.id, manifest.version)
        if existing is not None:
            if existing != profile:
                raise StoreConflictError("workflow version is immutable")
            return manifest
        try:
            self.store.save_workflow(profile)
        except StoreConflictError:
            if self.store.get_workflow(manifest.id, manifest.version) != profile:
                raise
        return manifest

    def list(self) -> list[WorkflowManifest]:
        return [
            WorkflowManifest.model_validate(p.manifest)
            for p in self.store.list_workflows()
            if p.manifest
        ]

    def get(self, identifier: str, version: int) -> tuple[WorkflowManifest, dict]:
        profile = self.store.get_workflow(identifier, version)
        if profile is None or not profile.manifest or not profile.source_graph:
            raise WorkflowError("workflow_not_found", identifier)
        return WorkflowManifest.model_validate(profile.manifest), profile.source_graph

    def for_scene(self, capability: str, scene: str) -> list[WorkflowManifest]:
        return [
            m
            for m in self.list()
            if m.status == "active"
            and any(
                i.capability == capability and i.scene == scene for i in m.integrations
            )
        ]
