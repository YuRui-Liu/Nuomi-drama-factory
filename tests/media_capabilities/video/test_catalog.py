from __future__ import annotations

from novelvideo.media_capabilities.models import (
    ProviderAccount,
    RunningHubWorkflowSettings,
)
from novelvideo.media_capabilities.runtime.credentials import CredentialResolver
from novelvideo.media_capabilities.store import MediaCapabilityStore
from novelvideo.media_capabilities.video.catalog import (
    H3_MODEL_ID,
    list_video_models,
    resolve_video_model_route,
)
from novelvideo.media_capabilities.video.workflow_registry import build_video_workflow_registry


def test_h3_is_listed_when_unconfigured_without_leaking_key(tmp_path) -> None:
    store = MediaCapabilityStore(tmp_path / "settings.db")
    models = list_video_models(store, CredentialResolver(env={}))

    assert [item.id for item in models] == [
        "runninghub:minimax-h3",
        "runninghub:minimax-h3-ref",
    ]
    assert models[0].available is False
    assert models[0].supported_modes == ("auto", "i2va", "fl2va")
    assert models[1].available is False
    assert models[1].unavailable_reason == "hybrid_input_unverified"
    assert "credential" not in models[0].model_dump_json().lower()
    assert "api_key" not in models[0].model_dump_json().lower()


def test_h3_is_available_only_with_enabled_account_key_workflow_and_profile(
    tmp_path,
) -> None:
    store = MediaCapabilityStore(tmp_path / "settings.db")
    store.save_provider(
        ProviderAccount(
            id="runninghub-main",
            provider_type="runninghub",
            base_url="https://www.runninghub.cn",
            credential_ref="env://RUNNINGHUB_API_KEY",
            enabled=True,
        )
    )
    resolver = CredentialResolver(env={"RUNNINGHUB_API_KEY": "rh-secret"})

    assert list_video_models(store, resolver)[0].available is True

    store.save_runninghub_workflows(RunningHubWorkflowSettings(video_minimax_h3=""))
    unavailable = list_video_models(store, resolver)[0]
    assert unavailable.available is False
    assert unavailable.unavailable_reason == "workflow_not_configured"


def test_catalog_is_credential_free_registry_projection(tmp_path) -> None:
    store = MediaCapabilityStore(tmp_path / "settings.db")
    resolver = CredentialResolver(env={})

    definitions = build_video_workflow_registry(store, resolver).list()
    items = list_video_models(store, resolver)

    for definition, item in zip(definitions, items, strict=True):
        assert item.parameters == definition.parameters
        assert item.reference_policy == definition.reference_policy
        assert item.model_dump() == {
            "id": definition.id,
            "label": definition.label,
            "provider": definition.provider,
            "available": definition.available,
            "supported_modes": definition.supported_modes,
            "default_mode": definition.default_mode,
            "parameters": definition.model_dump()["parameters"],
            "reference_policy": definition.model_dump()["reference_policy"],
            "unavailable_reason": definition.unavailable_reason,
        }
        assert "credential" not in item.model_dump_json().lower()

    assert items[0].id == H3_MODEL_ID
    assert items[0].reference_policy.model_dump(mode="json") == {
        "required": False,
        "min_images": 0,
        "max_images": 0,
        "source_kinds": [],
    }
    assert items[1].reference_policy.required is True


def test_catalog_publishes_only_product_parameter_schema(tmp_path) -> None:
    items = list_video_models(
        MediaCapabilityStore(tmp_path / "settings.db"), CredentialResolver(env={})
    )

    expected_parameters = [
        {
            "key": "resolution",
            "type": "enum",
            "label": "分辨率",
            "description": "",
            "default": "720p",
            "scope": "narrative_group",
            "options": [
                {
                    "value": "720p",
                    "label": "标准",
                    "description": "",
                    "relative_cost": "standard",
                },
                {
                    "value": "1080p",
                    "label": "高清",
                    "description": "画质更高，预计耗时和额度增加。",
                    "relative_cost": "higher",
                },
            ],
        },
        {
            "key": "continuity_policy",
            "type": "enum",
            "label": "连续性策略",
            "description": "",
            "default": "legacy",
            "scope": "narrative_group",
            "options": [
                {
                    "value": "legacy",
                    "label": "旧流程",
                    "description": "",
                    "relative_cost": "standard",
                },
                {
                    "value": "observe",
                    "label": "只观察",
                    "description": "",
                    "relative_cost": "standard",
                },
                {
                    "value": "guard",
                    "label": "阻断确定性错误",
                    "description": "",
                    "relative_cost": "standard",
                },
                {
                    "value": "enforce",
                    "label": "启用新编译",
                    "description": "",
                    "relative_cost": "standard",
                },
            ],
        },
    ]
    for item in items:
        assert item.model_dump(mode="json")["parameters"] == expected_parameters
        serialized = item.model_dump_json().lower()
        for internal_name in ("megapixels", "node_id", "width", "height"):
            assert internal_name not in serialized


def test_video_route_priority_keeps_explicit_runninghub_ahead_of_generic_models() -> None:
    route = resolve_video_model_route(
        project_runninghub=H3_MODEL_ID,
        local_custom=("local:wan",),
        official_catalog=("newapi:seedance", H3_MODEL_ID),
        system_default="newapi:default",
        available={H3_MODEL_ID, "local:wan", "newapi:seedance", "newapi:default"},
    )
    assert route == (
        H3_MODEL_ID,
        "local:wan",
        "newapi:seedance",
        "newapi:default",
    )


def test_video_route_priority_skips_unavailable_layers_without_reordering() -> None:
    route = resolve_video_model_route(
        project_runninghub=H3_MODEL_ID,
        local_custom=("local:missing", "local:ready"),
        official_catalog=("official:ready",),
        system_default="system:ready",
        available={"local:ready", "official:ready", "system:ready"},
    )
    assert route == ("local:ready", "official:ready", "system:ready")
