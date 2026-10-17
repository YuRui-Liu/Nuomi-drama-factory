"""Configured Python entry point without importing the FastAPI application."""

from contextlib import asynccontextmanager
from pathlib import Path


@asynccontextmanager
async def open_workflow_service(
    *, account_id: str = "runninghub-main", state_dir: str | Path | None = None
):
    from novelvideo import config
    from novelvideo.media_capabilities.store import MediaCapabilityStore
    from novelvideo.media_capabilities.task_store import TaskStore
    from novelvideo.media_capabilities.runtime.configuration import (
        load_runninghub_runtime_configuration,
    )
    from novelvideo.media_capabilities.runtime.credentials import CredentialResolver
    from novelvideo.media_capabilities.runtime.credential_store import (
        create_credential_store,
    )
    from novelvideo.media_capabilities.runtime.artifacts import ArtifactStore
    from novelvideo.media_capabilities.video.runtime import (
        get_h3_concurrency_coordinator,
    )
    from .registry import WorkflowRegistry
    from .service import WorkflowService

    root = (
        Path(state_dir) if state_dir is not None else Path(config.STATE_DIR) / "local"
    )
    registry = WorkflowRegistry(MediaCapabilityStore(root / "settings.db"))
    credentials = create_credential_store()
    resolver = CredentialResolver(
        keyring_reader=credentials.get, secret_reader=credentials.get
    )
    runtime = load_runninghub_runtime_configuration(
        registry.store, resolver, provider_id=account_id
    )
    concurrency = get_h3_concurrency_coordinator(account_id)
    concurrency.configure(
        account_id,
        runtime.account.max_concurrency,
        runtime.account.capability_limits,
        runtime.account.queue_limit,
    )
    async with runtime.create_client() as client:
        yield WorkflowService(
            registry,
            TaskStore(root / "runninghub" / "tasks.db"),
            client,
            ArtifactStore(root / "runninghub" / "artifacts"),
            concurrency,
            account_id=account_id,
        )
