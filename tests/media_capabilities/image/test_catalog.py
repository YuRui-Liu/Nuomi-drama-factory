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
    model: str | None = "nano-banana-pro",
    enabled: bool = True,
) -> None:
    store.save_provider(
        ProviderAccount(
            id="grsai-main",
            provider_type=provider_type,
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
