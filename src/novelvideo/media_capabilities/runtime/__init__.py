"""Runtime adapters for media capability providers."""

from novelvideo.media_capabilities.runtime.credentials import (
    CredentialResolutionError,
    CredentialResolver,
)
from novelvideo.media_capabilities.runtime.runninghub_client import (
    ProviderResult,
    ProviderTaskSnapshot,
    RunningHubClient,
    RunningHubError,
)
from novelvideo.media_capabilities.runtime.configuration import (
    GrsaiRuntimeConfiguration,
    MediaRuntimeConfigurationError,
    RunningHubRuntimeConfiguration,
    load_grsai_runtime_configuration,
    load_runninghub_runtime_configuration,
)

__all__ = [
    "CredentialResolutionError",
    "CredentialResolver",
    "GrsaiRuntimeConfiguration",
    "MediaRuntimeConfigurationError",
    "ProviderResult",
    "ProviderTaskSnapshot",
    "RunningHubClient",
    "RunningHubError",
    "RunningHubRuntimeConfiguration",
    "load_grsai_runtime_configuration",
    "load_runninghub_runtime_configuration",
]
