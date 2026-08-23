from __future__ import annotations

import json

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

from novelvideo.api import api_router
from novelvideo.api.app import (
    MAX_WORKFLOW_IMPORT_REQUEST_BODY_BYTES,
    _StreamingBodyLimitMiddleware,
    _request_body_limit,
)
from novelvideo.api.auth import get_api_user
from novelvideo.api.deps import get_media_capability_store, get_media_credential_store
from novelvideo.api.routes import media_capabilities
from novelvideo.media_capabilities.store import MediaCapabilityStore
from novelvideo.media_capabilities.models import MediaCapability, WorkflowProfile
from novelvideo.media_capabilities.workflow_profiles import MAX_SOURCE_BYTES


ADMIN_USER = {"id": "local", "username": "local", "role": "owner"}


@pytest.fixture
def store(tmp_path) -> MediaCapabilityStore:
    return MediaCapabilityStore(tmp_path / "settings.db")


def _client(
    store: MediaCapabilityStore,
    *,
    user: dict | None = ADMIN_USER,
    credential_store=None,
) -> TestClient:
    app = FastAPI()
    app.include_router(media_capabilities.catalog_router, prefix="/api/v1")
    app.include_router(media_capabilities.router, prefix="/api/v1")
    app.dependency_overrides[get_media_capability_store] = lambda: store
    if credential_store is not None:
        app.dependency_overrides[get_media_credential_store] = lambda: credential_store
    if user is None:
        async def reject_anonymous() -> None:
            raise HTTPException(status_code=401, detail="authentication required")

        app.dependency_overrides[get_api_user] = reject_anonymous
    else:
        app.dependency_overrides[get_api_user] = lambda: user
    return TestClient(app)


class FakeCredentialStore:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def set(self, reference: str, value: str) -> None:
        self.values[reference] = value

    def get(self, reference: str) -> str | None:
        return self.values.get(reference)

    def delete(self, reference: str) -> None:
        self.values.pop(reference, None)


def _provider_body() -> dict[str, object]:
    return {
        "provider_type": "runninghub",
        "base_url": "https://www.runninghub.cn",
        "credential_ref": "secret://runninghub-main",
        "max_concurrency": 5,
        "poll_concurrency": 20,
        "queue_limit": 100,
        "capability_limits": {"video.*": 3},
    }


def _workflow_bytes() -> bytes:
    return json.dumps(
        {
            "114": {
                "class_type": "LoadImage",
                "inputs": {"image": "placeholder.png"},
            },
            "133": {
                "class_type": "Text",
                "inputs": {"prompt": "placeholder"},
            },
            "136": {"class_type": "SaveVideo", "inputs": {}},
        }
    ).encode()


def test_video_models_endpoint_lists_disabled_h3_without_secrets(
    store: MediaCapabilityStore,
) -> None:
    response = _client(store).get("/api/v1/media-capabilities/video/models")

    assert response.status_code == 200
    item = next(
        model
        for model in response.json()["data"]
        if model["id"] == "runninghub:minimax-h3"
    )
    assert item["available"] is False
    assert item["supported_modes"] == ["auto", "i2va", "fl2va"]
    assert item["default_mode"] == "auto"
    assert "key" not in response.text.lower()


def test_video_models_catalog_is_readable_by_authenticated_editor(
    store: MediaCapabilityStore,
) -> None:
    response = _client(store, user={"id": "editor", "role": "editor"}).get(
        "/api/v1/media-capabilities/video/models"
    )
    assert response.status_code == 200
    assert response.json()["data"][0]["id"] == "runninghub:minimax-h3"


def _import_workflow(client: TestClient, *, bindings: dict | None = None):
    return client.post(
        "/api/v1/media-capabilities/workflows/import",
        data={
            "profile_id": "minimax-h3",
            "version": "1",
            "workflow_id": "2087934731806658562",
            "capabilities": json.dumps(["video.i2va"]),
            "bindings": json.dumps(
                bindings
                if bindings is not None
                else {
                    "first_frame": {"node_id": "114", "field": "image"},
                    "prompt": {"node_id": "133", "field": "prompt"},
                }
            ),
            "outputs": json.dumps(
                {"video": {"node_id": "136", "media_type": "video"}}
            ),
            "constraints": "{}",
        },
        files={"workflow": ("workflow.json", _workflow_bytes(), "application/json")},
    )


def test_routes_are_registered_on_main_api_router() -> None:
    registrations = [
        route
        for route in api_router.routes
        if getattr(route, "original_router", None) is media_capabilities.router
    ]
    assert len(registrations) == 1
    assert registrations[0].include_context.prefix == "/api/v1"
    paths = {
        f"/api/v1{route.path}" for route in media_capabilities.router.routes
    }
    assert "/api/v1/media-capabilities/providers" in paths
    assert "/api/v1/media-capabilities/workflows/import" in paths
    assert "/api/v1/media-capabilities/implementations" in paths
    assert "/api/v1/media-capabilities/routes" in paths


def test_provider_crud_never_returns_credential_reference(
    store: MediaCapabilityStore,
) -> None:
    client = _client(store)
    created = client.put(
        "/api/v1/media-capabilities/providers/runninghub-main",
        json=_provider_body(),
    )
    assert created.status_code == 200
    assert created.json() == {
        "id": "runninghub-main",
        "provider_type": "runninghub",
        "base_url": "https://www.runninghub.cn",
        "model": None,
        "enabled": True,
        "max_concurrency": 5,
        "poll_concurrency": 20,
        "queue_limit": 100,
        "capability_limits": {"video.*": 3},
        "credential_configured": True,
        "credential_scheme": "secret",
    }
    assert "credential_ref" not in created.text

    listed = client.get("/api/v1/media-capabilities/providers")
    fetched = client.get(
        "/api/v1/media-capabilities/providers/runninghub-main"
    )
    assert listed.status_code == fetched.status_code == 200
    assert listed.json() == [created.json()]
    assert fetched.json() == created.json()

    deleted = client.delete(
        "/api/v1/media-capabilities/providers/runninghub-main"
    )
    assert deleted.status_code == 204
    assert client.get(
        "/api/v1/media-capabilities/providers/runninghub-main"
    ).status_code == 404


def test_runninghub_workflow_ids_have_supported_defaults_and_can_be_saved(
    store: MediaCapabilityStore,
) -> None:
    client = _client(store)

    defaults = client.get(
        "/api/v1/media-capabilities/providers/runninghub-main/workflows"
    )
    assert defaults.status_code == 200
    assert defaults.json() == {
        "image_upscale": "",
        "video_minimax_h3": "2089723723468328961",
        "tts_qwen3_voice_design": "",
        "tts_indextts2_voice_clone": "",
    }

    saved = client.put(
        "/api/v1/media-capabilities/providers/runninghub-main/workflows",
        json={
            "image_upscale": " 1001 ",
            "video_minimax_h3": "2002",
            "tts_qwen3_voice_design": "3003",
            "tts_indextts2_voice_clone": "4004",
        },
    )
    assert saved.status_code == 200
    assert saved.json() == {
        "image_upscale": "1001",
        "video_minimax_h3": "2002",
        "tts_qwen3_voice_design": "3003",
        "tts_indextts2_voice_clone": "4004",
    }
    assert client.get(
        "/api/v1/media-capabilities/providers/runninghub-main/workflows"
    ).json() == saved.json()


def test_runninghub_workflow_ids_reject_non_numeric_values(
    store: MediaCapabilityStore,
) -> None:
    response = _client(store).put(
        "/api/v1/media-capabilities/providers/runninghub-main/workflows",
        json={
            "image_upscale": "not-an-id",
            "video_minimax_h3": "2087934731806658562",
            "tts_qwen3_voice_design": "",
            "tts_indextts2_voice_clone": "",
        },
    )
    assert response.status_code == 422


def test_provider_api_key_is_saved_to_os_store_and_never_returned(
    store: MediaCapabilityStore,
) -> None:
    credentials = FakeCredentialStore()
    client = _client(store, credential_store=credentials)
    provider = _provider_body() | {
        "credential_ref": "keyring://dramaclaw/media/runninghub-main"
    }
    created = client.put(
        "/api/v1/media-capabilities/providers/runninghub-main",
        json=provider,
    )
    assert created.status_code == 200
    assert created.json()["credential_configured"] is False

    secret = "rh-secret-value"
    saved = client.put(
        "/api/v1/media-capabilities/providers/runninghub-main/credential",
        json={"api_key": secret},
    )
    assert saved.status_code == 200
    assert saved.json()["credential_configured"] is True
    assert credentials.values["dramaclaw/media/runninghub-main"] == secret
    assert secret not in saved.text


def test_updating_provider_without_new_key_preserves_existing_reference(
    store: MediaCapabilityStore,
) -> None:
    client = _client(store, credential_store=FakeCredentialStore())
    client.put(
        "/api/v1/media-capabilities/providers/runninghub-main",
        json=_provider_body(),
    )
    body = _provider_body()
    body.pop("credential_ref")
    updated = client.put(
        "/api/v1/media-capabilities/providers/runninghub-main",
        json=body | {"max_concurrency": 7},
    )
    assert updated.status_code == 200
    assert updated.json()["credential_scheme"] == "secret"
    assert store.get_provider("runninghub-main").credential_ref == (
        "secret://runninghub-main"
    )


def test_provider_settings_atomically_save_key_and_runninghub_workflows(
    store: MediaCapabilityStore,
) -> None:
    credentials = FakeCredentialStore()
    client = _client(store, credential_store=credentials)
    response = client.put(
        "/api/v1/media-capabilities/providers/runninghub-main/settings",
        json={
            "provider_type": "runninghub",
            "base_url": "https://www.runninghub.cn",
            "api_key": "rh-secret",
            "max_concurrency": 5,
            "poll_concurrency": 10,
            "queue_limit": 100,
            "workflows": {
                "image_upscale": "1001",
                "video_minimax_h3": "2002",
                "tts_qwen3_voice_design": "3003",
                "tts_indextts2_voice_clone": "4004",
            },
        },
    )
    assert response.status_code == 200
    assert response.json()["provider"]["credential_configured"] is True
    assert response.json()["workflows"]["video_minimax_h3"] == "2002"
    assert "rh-secret" not in response.text
    assert store.get_runninghub_workflows().image_upscale == "1001"
    assert credentials.get("dramaclaw/media/runninghub-main") == "rh-secret"


def test_non_runninghub_provider_cannot_overwrite_runninghub_workflows(
    store: MediaCapabilityStore,
) -> None:
    response = _client(store, credential_store=FakeCredentialStore()).put(
        "/api/v1/media-capabilities/providers/grsai-main/settings",
        json={
            "provider_type": "grsai",
            "base_url": "https://grsai.example",
            "api_key": "grsai-secret",
            "workflows": {
                "image_upscale": "1001",
                "video_minimax_h3": "2002",
                "tts_qwen3_voice_design": "3003",
                "tts_indextts2_voice_clone": "4004",
            },
        },
    )
    assert response.status_code == 422
    assert store.get_runninghub_workflows().video_minimax_h3 == (
        "2089723723468328961"
    )


def test_grsai_settings_persist_and_return_effective_model(
    store: MediaCapabilityStore,
) -> None:
    response = _client(store, credential_store=FakeCredentialStore()).put(
        "/api/v1/media-capabilities/providers/grsai-main/settings",
        json={
            "provider_type": "grsai",
            "base_url": "https://grsaiapi.com",
            "api_key": "grsai-secret",
            "model": "gpt-image-2-vip",
        },
    )
    assert response.status_code == 200
    assert response.json()["provider"]["model"] == "gpt-image-2-vip"
    assert store.get_provider("grsai-main").model == "gpt-image-2-vip"


def test_grsai_settings_reject_unknown_model(store: MediaCapabilityStore) -> None:
    response = _client(store, credential_store=FakeCredentialStore()).put(
        "/api/v1/media-capabilities/providers/grsai-main/settings",
        json={
            "provider_type": "grsai",
            "base_url": "https://grsaiapi.com",
            "api_key": "grsai-secret",
            "model": "unknown-model",
        },
    )
    assert response.status_code == 422


def test_reads_require_auth_and_writes_require_system_admin(
    store: MediaCapabilityStore,
) -> None:
    assert _client(store, user=None).get(
        "/api/v1/media-capabilities/providers"
    ).status_code == 401
    viewer = _client(store, user={"id": "u1", "role": "viewer"})
    assert viewer.get("/api/v1/media-capabilities/providers").status_code == 403
    assert viewer.put(
        "/api/v1/media-capabilities/providers/runninghub-main",
        json=_provider_body(),
    ).status_code == 403
    agent = _client(
        store,
        user={
            "id": "agent",
            "role": "owner",
            "credential_kind": "agent_session",
            "scopes": ["projects:write"],
        },
    )
    assert agent.get("/api/v1/media-capabilities/providers").status_code == 403
    assert agent.put(
        "/api/v1/media-capabilities/providers/runninghub-main",
        json=_provider_body(),
    ).status_code == 403


def test_provider_validation_and_not_found_are_stable(
    store: MediaCapabilityStore,
) -> None:
    client = _client(store)
    invalid = _provider_body() | {"max_concurrency": 0}
    assert client.put(
        "/api/v1/media-capabilities/providers/bad",
        json=invalid,
    ).status_code == 422
    assert client.get(
        "/api/v1/media-capabilities/providers/missing"
    ).status_code == 404

    invalid_reference = _provider_body() | {
        "credential_ref": "plaintext-super-secret"
    }
    rejected = client.put(
        "/api/v1/media-capabilities/providers/bad-secret",
        json=invalid_reference,
    )
    assert rejected.status_code == 422
    assert "plaintext-super-secret" not in rejected.text


def test_invalid_credential_reference_is_never_echoed(
    store: MediaCapabilityStore,
) -> None:
    client = _client(store)
    raw_secret = "plaintext-super-secret"
    invalid = _provider_body() | {"credential_ref": raw_secret}
    response = client.put(
        "/api/v1/media-capabilities/providers/bad-secret",
        json=invalid,
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "configuration_invalid"
    assert raw_secret not in response.text


def test_workflow_import_requires_valid_explicit_bindings_and_publishes_metadata(
    store: MediaCapabilityStore,
) -> None:
    client = _client(store)
    invalid = _import_workflow(
        client,
        bindings={"prompt": {"node_id": "missing", "field": "prompt"}},
    )
    assert invalid.status_code == 422
    assert invalid.json()["detail"]["code"] == "workflow_import_invalid"

    imported = _import_workflow(client)
    assert imported.status_code == 201
    profile = imported.json()
    assert profile["status"] == "draft"
    assert profile["source_sha256"]
    assert "workflow_source" not in profile
    assert "placeholder.png" not in imported.text

    published = client.post(
        "/api/v1/media-capabilities/workflows/minimax-h3/1/publish"
    )
    assert published.status_code == 200
    assert published.json()["status"] == "published"
    assert client.get(
        "/api/v1/media-capabilities/workflows/minimax-h3/1"
    ).json() == published.json()
    assert client.get("/api/v1/media-capabilities/workflows").json() == [
        published.json()
    ]


def test_workflow_import_rejects_missing_bindings_and_source_over_five_mib(
    store: MediaCapabilityStore,
) -> None:
    client = _client(store)
    missing = _import_workflow(client, bindings={})
    assert missing.status_code == 422

    oversized = client.post(
        "/api/v1/media-capabilities/workflows/import",
        data={
            "profile_id": "too-large",
            "version": "1",
            "workflow_id": "wf",
            "capabilities": "[]",
            "bindings": json.dumps(
                {"prompt": {"node_id": "1", "field": "prompt"}}
            ),
            "outputs": json.dumps(
                {"video": {"node_id": "1", "media_type": "video"}}
            ),
            "constraints": "{}",
        },
        files={
            "workflow": (
                "workflow.json",
                b"{" + b" " * MAX_SOURCE_BYTES,
                "application/json",
            )
        },
    )
    assert oversized.status_code == 413
    assert oversized.json()["detail"]["code"] == "workflow_source_too_large"


def test_multipart_request_limit_allows_business_payload_plus_overhead() -> None:
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/media-capabilities/workflows/import",
            "headers": [(b"content-type", b"multipart/form-data; boundary=x")],
            "query_string": b"",
            "server": ("testserver", 80),
            "scheme": "http",
            "client": ("testclient", 50000),
        }
    )
    assert _request_body_limit(request) > MAX_SOURCE_BYTES


def test_implementation_and_routing_policy_crud(
    store: MediaCapabilityStore,
) -> None:
    client = _client(store)
    assert client.put(
        "/api/v1/media-capabilities/providers/runninghub-main",
        json=_provider_body(),
    ).status_code == 200
    implementation = {
        "capability": "video.fl2va",
        "provider_account": "runninghub-main",
        "workflow_profile": None,
        "prompt_profile": "minimax-h3-v1",
    }
    saved_implementation = client.put(
        "/api/v1/media-capabilities/implementations/runninghub-h3",
        json=implementation,
    )
    assert saved_implementation.status_code == 200
    assert saved_implementation.json()["id"] == "runninghub-h3"
    assert client.get(
        "/api/v1/media-capabilities/implementations/runninghub-h3"
    ).json() == saved_implementation.json()
    assert client.get(
        "/api/v1/media-capabilities/implementations"
    ).json() == [saved_implementation.json()]

    policy = {
        "default_implementation": "runninghub-h3",
        "fallback_chain": [],
        "concurrency_limit": 5,
    }
    saved_policy = client.put(
        "/api/v1/media-capabilities/routes/video.fl2va",
        json=policy,
    )
    assert saved_policy.status_code == 200
    assert saved_policy.json()["capability"] == "video.fl2va"
    assert client.get(
        "/api/v1/media-capabilities/routes/video.fl2va"
    ).json() == saved_policy.json()
    assert client.get("/api/v1/media-capabilities/routes").json() == [
        saved_policy.json()
    ]

    conflict = client.delete(
        "/api/v1/media-capabilities/implementations/runninghub-h3"
    )
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "configuration_conflict"
    assert client.delete(
        "/api/v1/media-capabilities/routes/video.fl2va"
    ).status_code == 204
    assert client.delete(
        "/api/v1/media-capabilities/implementations/runninghub-h3"
    ).status_code == 204


def test_workflow_and_policy_not_found_errors_are_stable(
    store: MediaCapabilityStore,
) -> None:
    client = _client(store)
    assert client.post(
        "/api/v1/media-capabilities/workflows/missing/1/publish"
    ).status_code == 404
    assert client.get(
        "/api/v1/media-capabilities/routes/video.fl2va"
    ).status_code == 404


def _draft_profile(
    identifier: str,
    capability: MediaCapability,
    *,
    bindings: dict[str, object],
    media_type: str,
    version: int = 1,
    status: str = "draft",
) -> WorkflowProfile:
    return WorkflowProfile(
        id=identifier,
        version=version,
        workflow_id=f"workflow-{identifier}-{version}",
        capabilities=[capability],
        bindings=bindings,
        outputs={"result": {"node_id": "out", "media_type": media_type}},
        status=status,
    )


def test_publish_requires_fl2va_first_last_and_prompt_bindings(
    store: MediaCapabilityStore,
) -> None:
    store.save_workflow(
        _draft_profile(
            "fl2va-missing-last",
            MediaCapability.VIDEO_FL2VA,
            bindings={"first_frame": {}, "prompt": {}},
            media_type="video",
        )
    )

    response = _client(store).post(
        "/api/v1/media-capabilities/workflows/fl2va-missing-last/1/publish"
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "workflow_publish_invalid"


@pytest.mark.parametrize(
    ("capability", "bindings", "wrong_media_type"),
    [
        (MediaCapability.IMAGE_SINGLE, {"prompt": {}}, "video"),
        (MediaCapability.VIDEO_T2VA, {"prompt": {}}, "audio"),
        (MediaCapability.TTS_SYNTHESIZE, {"text": {}}, "image"),
    ],
)
def test_publish_rejects_wrong_output_media_type_for_each_capability_family(
    store: MediaCapabilityStore,
    capability: MediaCapability,
    bindings: dict[str, object],
    wrong_media_type: str,
) -> None:
    identifier = capability.value.replace(".", "-")
    store.save_workflow(
        _draft_profile(
            identifier,
            capability,
            bindings=bindings,
            media_type=wrong_media_type,
        )
    )

    response = _client(store).post(
        f"/api/v1/media-capabilities/workflows/{identifier}/1/publish"
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "workflow_publish_invalid"


def test_implementation_uses_highest_available_workflow_version_and_capability(
    store: MediaCapabilityStore,
) -> None:
    store.save_workflow(
        _draft_profile(
            "versioned-video",
            MediaCapability.VIDEO_I2VA,
            bindings={"first_frame": {}, "prompt": {}},
            media_type="video",
            version=1,
            status="published",
        )
    )
    store.save_workflow(
        _draft_profile(
            "versioned-video",
            MediaCapability.VIDEO_FL2VA,
            bindings={"first_frame": {}, "last_frame": {}, "prompt": {}},
            media_type="video",
            version=2,
            status="draft",
        )
    )
    client = _client(store)
    assert client.put(
        "/api/v1/media-capabilities/providers/runninghub-main",
        json=_provider_body(),
    ).status_code == 200

    mismatch = client.put(
        "/api/v1/media-capabilities/implementations/fl2va-versioned",
        json={
            "capability": "video.fl2va",
            "provider_account": "runninghub-main",
            "workflow_profile": "versioned-video",
        },
    )

    assert mismatch.status_code == 409
    assert mismatch.json()["detail"]["code"] == "configuration_conflict"


@pytest.mark.parametrize("declared_length", [None, "1"])
async def test_streaming_body_limit_rejects_chunked_or_falsely_small_length(
    declared_length: str | None,
) -> None:
    headers = [(b"content-type", b"multipart/form-data; boundary=x")]
    if declared_length is not None:
        headers.append((b"content-length", declared_length.encode()))
    receive_calls = 0
    sent: list[dict] = []

    async def receive():
        nonlocal receive_calls
        receive_calls += 1
        return {
            "type": "http.request",
            "body": b"x" * (1024 * 1024),
            "more_body": True,
        }

    async def downstream(scope, receive_limited, send):
        try:
            while True:
                await receive_limited()
        except Exception:
            await send({"type": "http.response.start", "status": 400, "headers": []})
            await send({"type": "http.response.body", "body": b"bad request"})

    async def capture(message):
        sent.append(message)

    middleware = _StreamingBodyLimitMiddleware(downstream)
    await middleware(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/media-capabilities/workflows/import",
            "headers": headers,
            "query_string": b"",
            "server": ("testserver", 80),
            "scheme": "http",
            "client": ("testclient", 50000),
        },
        receive,
        capture,
    )

    assert MAX_WORKFLOW_IMPORT_REQUEST_BODY_BYTES == 6 * 1024 * 1024
    assert sent[0]["status"] == 413
    assert receive_calls == 7


def test_invalid_routing_policy_returns_sanitized_422(
    store: MediaCapabilityStore,
) -> None:
    client = _client(store)
    assert client.put(
        "/api/v1/media-capabilities/providers/runninghub-main",
        json=_provider_body(),
    ).status_code == 200
    assert client.put(
        "/api/v1/media-capabilities/implementations/runninghub-h3",
        json={
            "capability": "video.fl2va",
            "provider_account": "runninghub-main",
        },
    ).status_code == 200
    response = client.put(
        "/api/v1/media-capabilities/routes/video.fl2va",
        json={
            "default_implementation": "runninghub-h3",
            "fallback_chain": ["runninghub-h3"],
        },
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "configuration_invalid"
