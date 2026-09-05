"""Resolve persisted provider settings into executable runtime configuration."""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from novelvideo.media_capabilities.image.grsai import GrsaiClient
from novelvideo.media_capabilities.models import (
    DEFAULT_GRSAI_IMAGE_MODEL,
    MediaCapability,
    ProviderAccount,
    RunningHubWorkflowSettings,
)
from novelvideo.media_capabilities.runtime.credentials import CredentialResolver
from novelvideo.media_capabilities.runtime.runninghub_client import RunningHubClient
from novelvideo.media_capabilities.store import MediaCapabilityStore


class MediaRuntimeConfigurationError(RuntimeError):
    """Persisted media configuration is incomplete or unavailable."""


_WORKFLOW_FIELDS: dict[MediaCapability, str] = {
    MediaCapability.IMAGE_GRID_UPSCALE_SPLIT: "image_upscale",
    MediaCapability.VIDEO_I2VA: "video_minimax_h3",
    MediaCapability.VIDEO_L2VA: "video_minimax_h3",
    MediaCapability.VIDEO_FL2VA: "video_minimax_h3",
    MediaCapability.TTS_VOICE_DESIGN: "tts_qwen3_voice_design",
    MediaCapability.TTS_VOICE_CLONE: "tts_indextts2_voice_clone",
}

RUNNINGHUB_DOWNLOAD_HOSTS = (
    "rh-images.xiaoyaoyou.com",
    "rh-images-1252422369.cos.ap-beijing.myqcloud.com",
    "rh-images-switch-1252422369.cos.ap-guangzhou.myqcloud.com",
)


@dataclass(frozen=True, slots=True)
class RunningHubRuntimeConfiguration:
    account: ProviderAccount
    api_key: str
    workflows: RunningHubWorkflowSettings

    def workflow_id(self, capability: MediaCapability) -> str:
        field = _WORKFLOW_FIELDS.get(capability)
        if field is None:
            raise MediaRuntimeConfigurationError(
                f"RunningHub does not support {capability.value}"
            )
        value = str(getattr(self.workflows, field)).strip()
        if not value:
            raise MediaRuntimeConfigurationError(
                f"RunningHub workflow is not configured for {capability.value}"
            )
        return value

    def create_client(
        self,
        *,
        download_allowed_hosts: tuple[str, ...] = RUNNINGHUB_DOWNLOAD_HOSTS,
    ) -> RunningHubClient:
        return RunningHubClient(
            self.api_key,
            base_url=self.account.base_url or RunningHubClient.DEFAULT_BASE_URL,
            download_allowed_hosts=download_allowed_hosts,
        )


@dataclass(frozen=True, slots=True)
class GrsaiRuntimeConfiguration:
    account: ProviderAccount
    api_key: str

    @property
    def model(self) -> str:
        return self.account.model or DEFAULT_GRSAI_IMAGE_MODEL

    def create_client(self) -> GrsaiClient:
        if not self.account.base_url:
            raise MediaRuntimeConfigurationError("GRSAI base URL is not configured")
        return GrsaiClient(
            httpx.AsyncClient(
                base_url=self.account.base_url,
                trust_env=True,
                timeout=httpx.Timeout(connect=20, read=60, write=60, pool=20),
            ),
            default_model=self.model,
        )


def _load_provider_key(
    store: MediaCapabilityStore,
    resolver: CredentialResolver,
    *,
    provider_id: str,
    provider_type: str,
) -> tuple[ProviderAccount, str]:
    account = store.get_provider(provider_id)
    if (
        account is None
        or account.provider_type != provider_type
        or not account.enabled
    ):
        raise MediaRuntimeConfigurationError(
            f"{provider_type.upper()} provider is not configured"
        )
    try:
        api_key = resolver.resolve(account.credential_ref)
    except Exception as exc:
        raise MediaRuntimeConfigurationError(
            f"{provider_type.upper()} credential is unavailable"
        ) from exc
    return account, api_key


def load_runninghub_runtime_configuration(
    store: MediaCapabilityStore,
    resolver: CredentialResolver,
    *,
    provider_id: str = "runninghub-main",
) -> RunningHubRuntimeConfiguration:
    account, api_key = _load_provider_key(
        store,
        resolver,
        provider_id=provider_id,
        provider_type="runninghub",
    )
    return RunningHubRuntimeConfiguration(
        account=account,
        api_key=api_key,
        workflows=store.get_runninghub_workflows(),
    )


def load_grsai_runtime_configuration(
    store: MediaCapabilityStore,
    resolver: CredentialResolver,
    *,
    provider_id: str = "grsai-main",
) -> GrsaiRuntimeConfiguration:
    account, api_key = _load_provider_key(
        store,
        resolver,
        provider_id=provider_id,
        provider_type="grsai",
    )
    return GrsaiRuntimeConfiguration(account=account, api_key=api_key)


__all__ = [
    "GrsaiRuntimeConfiguration",
    "MediaRuntimeConfigurationError",
    "RunningHubRuntimeConfiguration",
    "load_grsai_runtime_configuration",
    "load_runninghub_runtime_configuration",
]
