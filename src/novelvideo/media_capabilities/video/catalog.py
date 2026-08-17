"""Public, credential-free catalog of production video models."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from novelvideo.media_capabilities.runtime.credentials import CredentialResolver
from novelvideo.media_capabilities.store import MediaCapabilityStore
from novelvideo.media_capabilities.video.runtime import load_h3_workflow_profile


H3_MODEL_ID = "runninghub:minimax-h3"


class VideoModelCatalogItem(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    label: str
    provider: str
    available: bool
    supported_modes: tuple[str, ...]
    default_mode: str = "auto"
    unavailable_reason: str | None = None


def list_video_models(
    store: MediaCapabilityStore,
    resolver: CredentialResolver,
) -> tuple[VideoModelCatalogItem, ...]:
    reason: str | None = None
    account = store.get_provider("runninghub-main")
    if account is None or account.provider_type != "runninghub" or not account.enabled:
        reason = "provider_not_configured"
    else:
        try:
            resolver.resolve(account.credential_ref)
        except Exception:
            reason = "credential_unavailable"

    if not str(store.get_runninghub_workflows().video_minimax_h3).strip():
        reason = "workflow_not_configured"
    try:
        load_h3_workflow_profile()
    except Exception:
        reason = "profile_invalid"

    return (
        VideoModelCatalogItem(
            id=H3_MODEL_ID,
            label="MiniMax H3",
            provider="runninghub",
            available=reason is None,
            supported_modes=("auto", "i2va", "fl2va"),
            unavailable_reason=reason,
        ),
    )


__all__ = ["H3_MODEL_ID", "VideoModelCatalogItem", "list_video_models"]
