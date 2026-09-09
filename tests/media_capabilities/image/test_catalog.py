from __future__ import annotations

import pytest

from novelvideo.media_capabilities.image.catalog import list_image_models
from novelvideo.media_capabilities.models import GRSAI_IMAGE_MODELS, ProviderAccount
from novelvideo.media_capabilities.runtime.credentials import CredentialResolver
from novelvideo.media_capabilities.store import MediaCapabilityStore


def _save_grsai_provider(
    store: MediaCapabilityStore,
    *,
    provider_type: str = "grsai",
    base_url: str | None = "https://grsai.example",
    model: str | None = "nano-banana-pro",
    enabled: bool = True,
) -> None:
    store.save_provider(
        ProviderAccount(
            id="grsai-main",
            provider_type=provider_type,
            base_url=base_url,
            model=model,
            credential_ref="env://GRSAI_API_KEY",
            enabled=enabled,
        )
    )


def test_catalog_lists_default_first_then_remaining_models_stably(tmp_path) -> None:
    store = MediaCapabilityStore(tmp_path / "settings.db")
    _save_grsai_provider(store, model="nano-banana-pro")

    items = list_image_models(
        store,
        CredentialResolver(env={"GRSAI_API_KEY": "top-secret-value"}),
    )

    assert [item.id for item in items] == [
        "nano-banana-pro",
        *sorted(GRSAI_IMAGE_MODELS - {"nano-banana-pro"}),
    ]
    assert [item.label for item in items] == [item.id for item in items]
    assert {item.provider_id for item in items} == {"grsai-main"}
    assert {item.provider for item in items} == {"grsai"}
    serialized = "".join(item.model_dump_json() for item in items)
    assert "top-secret-value" not in serialized
    assert "credential" not in serialized.lower()
    assert "api_key" not in serialized.lower()


def test_catalog_uses_documented_default_when_account_model_is_unset(tmp_path) -> None:
    store = MediaCapabilityStore(tmp_path / "settings.db")
    _save_grsai_provider(store, model=None)

    items = list_image_models(
        store,
        CredentialResolver(env={"GRSAI_API_KEY": "available"}),
    )

    assert items[0].id == "gpt-image-2"
    assert {item.id for item in items} == GRSAI_IMAGE_MODELS


@pytest.mark.parametrize(
    ("provider_state", "resolver"),
    [
        ("missing", CredentialResolver(env={"GRSAI_API_KEY": "available"})),
        ("disabled", CredentialResolver(env={"GRSAI_API_KEY": "available"})),
        ("wrong-type", CredentialResolver(env={"GRSAI_API_KEY": "available"})),
        ("missing-credential", CredentialResolver(env={})),
    ],
)
def test_catalog_is_empty_when_provider_is_unavailable(
    tmp_path,
    provider_state: str,
    resolver: CredentialResolver,
) -> None:
    store = MediaCapabilityStore(tmp_path / f"{provider_state}.db")
    if provider_state != "missing":
        _save_grsai_provider(
            store,
            provider_type="other" if provider_state == "wrong-type" else "grsai",
            enabled=provider_state != "disabled",
        )

    assert list_image_models(store, resolver) == ()


def test_catalog_is_empty_when_credential_reader_fails(tmp_path) -> None:
    store = MediaCapabilityStore(tmp_path / "settings.db")
    def fail_to_read(_: str) -> str:
        raise RuntimeError("reader leaked internal failure")

    resolver = CredentialResolver(env={}, secret_reader=fail_to_read)
    store.save_provider(
        ProviderAccount(
            id="grsai-main",
            provider_type="grsai",
            credential_ref="secret://grsai-main",
        )
    )

    assert list_image_models(store, resolver) == ()


@pytest.mark.parametrize(
    "base_url",
    [None, "", "   ", "not-a-url", "ftp://grsai.example"],
)
def test_catalog_is_empty_when_grsai_base_url_is_not_executable(
    tmp_path,
    base_url: str | None,
) -> None:
    store = MediaCapabilityStore(tmp_path / "settings.db")
    _save_grsai_provider(store, base_url=base_url)

    assert list_image_models(
        store,
        CredentialResolver(env={"GRSAI_API_KEY": "available"}),
    ) == ()


def test_model_selection_classifier_and_resolver_are_fail_closed() -> None:
    from novelvideo.media_capabilities.image.catalog import (
        ImageModelSelectionKind,
        classify_image_model_selection,
        resolve_grsai_image_model,
    )

    legacy_values = {"newapi_gpt_image2", "LingShan-G2"}
    assert classify_image_model_selection(
        "", legacy_values=legacy_values
    ) is ImageModelSelectionKind.EMPTY
    assert classify_image_model_selection(
        "gpt-image-2-vip", legacy_values=legacy_values
    ) is ImageModelSelectionKind.GRSAI
    assert classify_image_model_selection(
        "LingShan-G2", legacy_values=legacy_values
    ) is ImageModelSelectionKind.LEGACY
    assert classify_image_model_selection(
        "outside", legacy_values=legacy_values
    ) is ImageModelSelectionKind.UNKNOWN

    explicit = resolve_grsai_image_model(
        requested_model="gpt-image-2-vip",
        project_model="nano-banana-pro",
        runtime_model="gpt-image-2",
        legacy_values=legacy_values,
    )
    assert (explicit.model, explicit.requested_model, explicit.resolution_source) == (
        "gpt-image-2-vip",
        "gpt-image-2-vip",
        "explicit",
    )
    project = resolve_grsai_image_model(
        requested_model="",
        project_model="nano-banana-pro",
        runtime_model="gpt-image-2",
        legacy_values=legacy_values,
    )
    assert (project.model, project.requested_model, project.resolution_source) == (
        "nano-banana-pro",
        "nano-banana-pro",
        "project",
    )
    runtime = resolve_grsai_image_model(
        requested_model="newapi_gpt_image2",
        project_model="nano-banana-pro",
        runtime_model="gpt-image-2",
        legacy_values=legacy_values,
    )
    assert (runtime.model, runtime.requested_model, runtime.resolution_source) == (
        "gpt-image-2",
        "newapi_gpt_image2",
        "runtime",
    )
    with pytest.raises(ValueError, match="Unsupported GRSAI image model"):
        resolve_grsai_image_model(
            requested_model="",
            project_model="outside",
            runtime_model="gpt-image-2",
            legacy_values=legacy_values,
        )
