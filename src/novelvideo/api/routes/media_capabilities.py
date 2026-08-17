"""Authenticated management API for media capability configuration."""

from __future__ import annotations

import json
import os
from typing import Annotated, Any

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Response,
    UploadFile,
    status,
)
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PositiveInt,
    SecretStr,
    ValidationError,
)

from novelvideo.api.auth import get_api_user
from novelvideo.api.deps import get_media_capability_store, get_media_credential_store
from novelvideo.media_capabilities.models import (
    CapabilityImplementation,
    DEFAULT_GRSAI_IMAGE_MODEL,
    MediaCapability,
    NonEmptyText,
    ProviderAccount,
    RoutingPolicy,
    RunningHubWorkflowSettings,
    WorkflowProfile,
)
from novelvideo.media_capabilities.store import (
    MediaCapabilityStore,
    StoreConflictError,
)
from novelvideo.media_capabilities.runtime.credential_store import (
    CredentialStoreError,
)
from novelvideo.media_capabilities.workflow_profiles import (
    MAX_SOURCE_BYTES,
    WorkflowImportError,
    import_profile,
)
from novelvideo.media_capabilities.runtime.credentials import CredentialResolver
from novelvideo.media_capabilities.video.catalog import list_video_models


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProviderAccountBody(_Body):
    provider_type: NonEmptyText
    base_url: str | None = None
    model: str | None = None
    # Validate this in the route so FastAPI never echoes a rejected secret-like
    # value in its default request-validation response.
    credential_ref: Any = None
    enabled: bool = True
    max_concurrency: int = Field(default=1, gt=0)
    poll_concurrency: int = Field(default=1, gt=0)
    queue_limit: int = Field(default=1, gt=0)
    capability_limits: dict[str, PositiveInt] = Field(default_factory=dict)


class ProviderAccountView(_Body):
    id: str
    provider_type: str
    base_url: str | None
    model: str | None
    enabled: bool
    max_concurrency: int
    poll_concurrency: int
    queue_limit: int
    capability_limits: dict[str, int]
    credential_configured: bool
    credential_scheme: str


class ProviderCredentialBody(_Body):
    api_key: SecretStr


class ProviderSettingsBody(_Body):
    provider_type: NonEmptyText
    base_url: str | None = None
    model: str | None = None
    api_key: SecretStr | None = None
    enabled: bool = True
    max_concurrency: int = Field(default=1, gt=0)
    poll_concurrency: int = Field(default=1, gt=0)
    queue_limit: int = Field(default=1, gt=0)
    capability_limits: dict[str, PositiveInt] = Field(default_factory=dict)
    workflows: RunningHubWorkflowSettings | None = None


class ProviderSettingsView(_Body):
    provider: ProviderAccountView
    workflows: RunningHubWorkflowSettings | None = None


class CapabilityImplementationBody(_Body):
    capability: MediaCapability
    provider_account: NonEmptyText
    workflow_profile: str | None = None
    prompt_profile: str | None = None


class RoutingPolicyBody(_Body):
    default_implementation: NonEmptyText
    fallback_chain: list[str] = Field(default_factory=list)
    concurrency_limit: int | None = Field(default=None, gt=0)


async def require_media_capability_admin(
    user: dict = Depends(get_api_user),
) -> dict:
    """Authorize writes to process-global provider configuration."""
    if user.get("credential_kind") == "agent_session":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="system media capability management requires a user session",
        )
    if str(user.get("role") or "").lower() not in {"admin", "owner"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="system administrator role required",
        )
    return user


router = APIRouter(
    prefix="/media-capabilities",
    dependencies=[Depends(require_media_capability_admin)],
)

catalog_router = APIRouter(
    prefix="/media-capabilities",
    dependencies=[Depends(get_api_user)],
)

AdminUser = Annotated[dict, Depends(require_media_capability_admin)]
Store = Annotated[MediaCapabilityStore, Depends(get_media_capability_store)]
CredentialStore = Annotated[Any, Depends(get_media_credential_store)]


@catalog_router.get("/video/models")
def get_video_models(
    store: Store,
    credentials: CredentialStore,
) -> dict[str, object]:
    """List stable video model IDs without exposing credential material."""
    resolver = CredentialResolver(
        keyring_reader=credentials.get,
        secret_reader=credentials.get,
    )
    return {
        "ok": True,
        "data": [item.model_dump(mode="json") for item in list_video_models(store, resolver)],
    }

_PUBLISH_REQUIREMENTS: dict[MediaCapability, tuple[frozenset[str], str]] = {
    MediaCapability.IMAGE_STORYBOARD_GRID: (frozenset({"prompt"}), "image"),
    MediaCapability.IMAGE_SINGLE: (frozenset({"prompt"}), "image"),
    MediaCapability.IMAGE_GRID_UPSCALE_SPLIT: (
        frozenset({"source_image"}),
        "image",
    ),
    MediaCapability.VIDEO_T2VA: (frozenset({"prompt"}), "video"),
    MediaCapability.VIDEO_I2VA: (
        frozenset({"first_frame", "prompt"}),
        "video",
    ),
    MediaCapability.VIDEO_L2VA: (
        frozenset({"last_frame", "prompt"}),
        "video",
    ),
    MediaCapability.VIDEO_FL2VA: (
        frozenset({"first_frame", "last_frame", "prompt"}),
        "video",
    ),
    MediaCapability.VIDEO_REF2VA: (
        frozenset({"reference_images", "prompt"}),
        "video",
    ),
    MediaCapability.TTS_SYNTHESIZE: (frozenset({"text"}), "audio"),
    MediaCapability.TTS_VOICE_DESIGN: (
        frozenset({"voice_description"}),
        "audio",
    ),
    MediaCapability.TTS_VOICE_CLONE: (
        frozenset({"reference_audio", "text"}),
        "audio",
    ),
}


def _not_found(resource: str, identifier: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={
            "code": "configuration_not_found",
            "resource": resource,
            "id": identifier,
        },
    )


def _conflict(exc: StoreConflictError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={"code": "configuration_conflict", "message": str(exc)},
    )


def _invalid_configuration() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail={"code": "configuration_invalid"},
    )


def _provider_view(
    account: ProviderAccount,
    credential_store: Any | None = None,
) -> ProviderAccountView:
    scheme, _, reference = account.credential_ref.partition("://")
    configured = bool(account.credential_ref)
    if scheme == "env":
        configured = bool(os.environ.get(reference, "").strip())
    elif scheme == "keyring":
        configured = bool(
            credential_store is not None and credential_store.get(reference)
        )
    return ProviderAccountView(
        id=account.id,
        provider_type=account.provider_type,
        base_url=account.base_url,
        model=(
            account.model or DEFAULT_GRSAI_IMAGE_MODEL
            if account.provider_type == "grsai"
            else account.model
        ),
        enabled=account.enabled,
        max_concurrency=account.max_concurrency,
        poll_concurrency=account.poll_concurrency,
        queue_limit=account.queue_limit,
        capability_limits=dict(account.capability_limits),
        credential_configured=configured,
        credential_scheme=scheme,
    )


def _decode_form_json(
    value: str,
    *,
    field: str,
    expected_type: type[list] | type[dict],
) -> list[Any] | dict[str, Any]:
    try:
        decoded = json.loads(value)
    except (json.JSONDecodeError, RecursionError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "workflow_metadata_invalid", "field": field},
        ) from exc
    if not isinstance(decoded, expected_type):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "workflow_metadata_invalid", "field": field},
        )
    return decoded


def _validate_profile_for_publish(profile: WorkflowProfile) -> None:
    if not profile.capabilities:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "workflow_publish_invalid",
                "message": "at least one capability is required",
            },
        )
    missing: dict[str, list[str]] = {}
    wrong_output_types: dict[str, str] = {}
    for capability in profile.capabilities:
        required_bindings, output_media_type = _PUBLISH_REQUIREMENTS[capability]
        capability_missing = sorted(required_bindings - profile.bindings.keys())
        if capability_missing:
            missing[capability.value] = capability_missing
        has_output = any(
            isinstance(descriptor, dict)
            and descriptor.get("media_type") == output_media_type
            for descriptor in profile.outputs.values()
        )
        if not has_output:
            wrong_output_types[capability.value] = output_media_type
    if missing or wrong_output_types:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "workflow_publish_invalid",
                "missing_bindings": missing,
                "required_output_media_types": wrong_output_types,
            },
        )


def _resolve_available_workflow(
    store: MediaCapabilityStore,
    profile_id: str,
) -> WorkflowProfile:
    available = [
        profile
        for profile in store.list_workflows()
        if profile.id == profile_id and profile.status in {"active", "published"}
    ]
    if not available:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "configuration_conflict",
                "message": f"workflow {profile_id} has no active or published version",
            },
        )
    return max(available, key=lambda profile: profile.version)


@router.get("/providers", response_model=list[ProviderAccountView])
def list_providers(
    store: Store,
    credential_store: CredentialStore,
) -> list[ProviderAccountView]:
    return [
        _provider_view(item, credential_store) for item in store.list_providers()
    ]


@router.get("/providers/{provider_id}", response_model=ProviderAccountView)
def get_provider(
    provider_id: str,
    store: Store,
    credential_store: CredentialStore,
) -> ProviderAccountView:
    account = store.get_provider(provider_id)
    if account is None:
        raise _not_found("provider", provider_id)
    return _provider_view(account, credential_store)


@router.put("/providers/{provider_id}", response_model=ProviderAccountView)
def put_provider(
    provider_id: str,
    body: ProviderAccountBody,
    store: Store,
    credential_store: CredentialStore,
    _user: AdminUser,
) -> ProviderAccountView:
    values = body.model_dump()
    if values["credential_ref"] is None:
        existing = store.get_provider(provider_id)
        if existing is None:
            raise _invalid_configuration()
        values["credential_ref"] = existing.credential_ref
    try:
        account = ProviderAccount(id=provider_id, **values)
    except ValidationError as exc:
        raise _invalid_configuration() from exc
    store.save_provider(account)
    return _provider_view(account, credential_store)


@router.put(
    "/providers/{provider_id}/credential",
    response_model=ProviderAccountView,
)
def save_provider_credential(
    provider_id: str,
    body: ProviderCredentialBody,
    store: Store,
    credential_store: CredentialStore,
) -> ProviderAccountView:
    account = store.get_provider(provider_id)
    if account is None:
        raise _not_found("provider", provider_id)
    scheme, _, reference = account.credential_ref.partition("://")
    if scheme != "keyring":
        raise _invalid_configuration()
    try:
        credential_store.set(reference, body.api_key.get_secret_value())
    except CredentialStoreError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "credential_store_unavailable"},
        ) from exc
    return _provider_view(account, credential_store)


@router.put(
    "/providers/{provider_id}/settings",
    response_model=ProviderSettingsView,
)
def save_provider_settings(
    provider_id: str,
    body: ProviderSettingsBody,
    store: Store,
    credential_store: CredentialStore,
) -> ProviderSettingsView:
    if body.workflows is not None and (
        provider_id != "runninghub-main" or body.provider_type != "runninghub"
    ):
        raise _invalid_configuration()
    existing = store.get_provider(provider_id)
    secret = body.api_key.get_secret_value().strip() if body.api_key else ""
    if secret:
        credential_ref = f"keyring://dramaclaw/media/{provider_id}"
    elif existing is not None:
        credential_ref = existing.credential_ref
    else:
        raise _invalid_configuration()
    try:
        account = ProviderAccount(
            id=provider_id,
            credential_ref=credential_ref,
            **body.model_dump(exclude={"api_key", "workflows"}),
        )
    except ValidationError as exc:
        raise _invalid_configuration() from exc

    reference = credential_ref.partition("://")[2]
    previous_secret = credential_store.get(reference) if secret else None
    if secret:
        try:
            credential_store.set(reference, secret)
        except CredentialStoreError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"code": "credential_store_unavailable"},
            ) from exc
    try:
        store.save_provider_bundle(account, body.workflows)
    except Exception:
        if secret:
            try:
                if previous_secret:
                    credential_store.set(reference, previous_secret)
                else:
                    credential_store.delete(reference)
            except Exception:
                pass
        raise
    return ProviderSettingsView(
        provider=_provider_view(account, credential_store),
        workflows=body.workflows,
    )


@router.delete(
    "/providers/{provider_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_provider(
    provider_id: str,
    store: Store,
    credential_store: CredentialStore,
    _user: AdminUser,
) -> Response:
    account = store.get_provider(provider_id)
    try:
        deleted = store.delete_provider(provider_id)
    except StoreConflictError as exc:
        raise _conflict(exc) from exc
    if not deleted:
        raise _not_found("provider", provider_id)
    if account is not None:
        scheme, _, reference = account.credential_ref.partition("://")
        if scheme == "keyring":
            credential_store.delete(reference)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/providers/runninghub-main/workflows",
    response_model=RunningHubWorkflowSettings,
)
def get_runninghub_workflows(store: Store) -> RunningHubWorkflowSettings:
    return store.get_runninghub_workflows()


@router.put(
    "/providers/runninghub-main/workflows",
    response_model=RunningHubWorkflowSettings,
)
def save_runninghub_workflows(
    body: RunningHubWorkflowSettings,
    store: Store,
) -> RunningHubWorkflowSettings:
    store.save_runninghub_workflows(body)
    return body


@router.get("/workflows", response_model=list[WorkflowProfile])
def list_workflows(store: Store) -> list[WorkflowProfile]:
    return store.list_workflows()


@router.get(
    "/workflows/{profile_id}/{version}",
    response_model=WorkflowProfile,
)
def get_workflow(
    profile_id: str,
    version: int,
    store: Store,
) -> WorkflowProfile:
    profile = store.get_workflow(profile_id, version)
    if profile is None:
        raise _not_found("workflow", f"{profile_id}:{version}")
    return profile


@router.post(
    "/workflows/import",
    response_model=WorkflowProfile,
    status_code=status.HTTP_201_CREATED,
)
async def import_workflow(
    store: Store,
    _user: AdminUser,
    workflow: UploadFile = File(...),
    profile_id: str = Form(...),
    version: int = Form(..., ge=1),
    workflow_id: str = Form(...),
    capabilities: str = Form("[]"),
    bindings: str = Form(...),
    outputs: str = Form(...),
    constraints: str = Form("{}"),
) -> WorkflowProfile:
    try:
        source = await workflow.read(MAX_SOURCE_BYTES + 1)
    finally:
        await workflow.close()
    if len(source) > MAX_SOURCE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail={
                "code": "workflow_source_too_large",
                "limit": MAX_SOURCE_BYTES,
            },
        )

    parsed_capabilities = _decode_form_json(
        capabilities,
        field="capabilities",
        expected_type=list,
    )
    parsed_bindings = _decode_form_json(
        bindings,
        field="bindings",
        expected_type=dict,
    )
    parsed_outputs = _decode_form_json(
        outputs,
        field="outputs",
        expected_type=dict,
    )
    parsed_constraints = _decode_form_json(
        constraints,
        field="constraints",
        expected_type=dict,
    )
    if not parsed_bindings or not parsed_outputs:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "workflow_metadata_invalid",
                "message": "explicit semantic bindings and outputs are required",
            },
        )
    try:
        profile = import_profile(
            workflow_id=workflow_id,
            source=source,
            bindings=parsed_bindings,
            outputs=parsed_outputs,
            id=profile_id,
            version=version,
            capabilities=parsed_capabilities,
            constraints=parsed_constraints,
        )
        store.save_workflow(profile)
    except WorkflowImportError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "workflow_import_invalid", "message": str(exc)},
        ) from exc
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "workflow_metadata_invalid", "message": str(exc)},
        ) from exc
    except StoreConflictError as exc:
        raise _conflict(exc) from exc
    return profile


@router.post(
    "/workflows/{profile_id}/{version}/publish",
    response_model=WorkflowProfile,
)
def publish_workflow(
    profile_id: str,
    version: int,
    store: Store,
    _user: AdminUser,
) -> WorkflowProfile:
    profile = store.get_workflow(profile_id, version)
    if profile is None:
        raise _not_found("workflow", f"{profile_id}:{version}")
    _validate_profile_for_publish(profile)
    published = profile.model_copy(update={"status": "published"})
    try:
        store.save_workflow(published)
    except StoreConflictError as exc:
        raise _conflict(exc) from exc
    return published


@router.delete(
    "/workflows/{profile_id}/{version}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_workflow(
    profile_id: str,
    version: int,
    store: Store,
    _user: AdminUser,
) -> Response:
    try:
        deleted = store.delete_workflow(profile_id, version)
    except StoreConflictError as exc:
        raise _conflict(exc) from exc
    if not deleted:
        raise _not_found("workflow", f"{profile_id}:{version}")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/implementations",
    response_model=list[CapabilityImplementation],
)
def list_implementations(store: Store) -> list[CapabilityImplementation]:
    return store.list_implementations()


@router.get(
    "/implementations/{implementation_id}",
    response_model=CapabilityImplementation,
)
def get_implementation(
    implementation_id: str,
    store: Store,
) -> CapabilityImplementation:
    implementation = store.get_implementation(implementation_id)
    if implementation is None:
        raise _not_found("implementation", implementation_id)
    return implementation


@router.put(
    "/implementations/{implementation_id}",
    response_model=CapabilityImplementation,
)
def put_implementation(
    implementation_id: str,
    body: CapabilityImplementationBody,
    store: Store,
    _user: AdminUser,
) -> CapabilityImplementation:
    implementation = CapabilityImplementation(
        id=implementation_id,
        **body.model_dump(),
    )
    if implementation.workflow_profile:
        workflow = _resolve_available_workflow(
            store,
            implementation.workflow_profile,
        )
        if implementation.capability not in workflow.capabilities:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "configuration_conflict",
                    "message": (
                        f"workflow {workflow.id} version {workflow.version} does not "
                        f"support {implementation.capability.value}"
                    ),
                },
            )
    try:
        store.save_implementation(implementation)
    except StoreConflictError as exc:
        raise _conflict(exc) from exc
    return implementation


@router.delete(
    "/implementations/{implementation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_implementation(
    implementation_id: str,
    store: Store,
    _user: AdminUser,
) -> Response:
    try:
        deleted = store.delete_implementation(implementation_id)
    except StoreConflictError as exc:
        raise _conflict(exc) from exc
    if not deleted:
        raise _not_found("implementation", implementation_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/routes", response_model=list[RoutingPolicy])
def list_routes(store: Store) -> list[RoutingPolicy]:
    return store.list_policies()


@router.get("/routes/{capability}", response_model=RoutingPolicy)
def get_route(
    capability: MediaCapability,
    store: Store,
) -> RoutingPolicy:
    policy = store.get_policy(capability)
    if policy is None:
        raise _not_found("routing_policy", capability.value)
    return policy


@router.put("/routes/{capability}", response_model=RoutingPolicy)
def put_route(
    capability: MediaCapability,
    body: RoutingPolicyBody,
    store: Store,
    _user: AdminUser,
) -> RoutingPolicy:
    try:
        policy = RoutingPolicy(capability=capability, **body.model_dump())
        store.save_policy(policy)
    except ValidationError as exc:
        raise _invalid_configuration() from exc
    except StoreConflictError as exc:
        raise _conflict(exc) from exc
    return policy


@router.delete(
    "/routes/{capability}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_route(
    capability: MediaCapability,
    store: Store,
    _user: AdminUser,
) -> Response:
    if not store.delete_policy(capability):
        raise _not_found("routing_policy", capability.value)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
