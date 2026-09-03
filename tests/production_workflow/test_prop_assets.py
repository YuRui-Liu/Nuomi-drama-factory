from __future__ import annotations

import pytest
from pydantic import ValidationError

from novelvideo.production_workflow.prop_assets import (
    NormalizedRegion,
    PropAppearanceState,
    PropAssetReason,
    PropContentLayer,
    PropUsage,
    plan_prop_asset,
)


@pytest.mark.parametrize(
    ("usage", "reason"),
    [
        (PropUsage(plot_critical=True), PropAssetReason.PLOT_CRITICAL),
        (PropUsage(close_up=True), PropAssetReason.CLOSE_UP),
        (PropUsage(shot_count=2), PropAssetReason.MULTI_SHOT),
        (PropUsage(repeated_use=True), PropAssetReason.REPEATED_USE),
        (PropUsage(requires_exact_content=True), PropAssetReason.EXACT_CONTENT),
    ],
)
def test_each_formal_prop_trigger_materializes_an_asset(
    usage: PropUsage,
    reason: PropAssetReason,
) -> None:
    decision = plan_prop_asset(usage)

    assert decision.formal_asset_required is True
    assert reason in decision.reasons


def test_single_incidental_shot_does_not_materialize_a_formal_asset() -> None:
    decision = plan_prop_asset(PropUsage(shot_count=1))

    assert decision.formal_asset_required is False
    assert decision.reasons == ()


def test_prop_appearance_and_readable_content_are_separate_models() -> None:
    appearance = PropAppearanceState(
        prop_id="phone-01",
        state_id="phone-01-cracked",
        category="phone",
        appearance="黑色手机，右上角蛛网状裂纹",
        condition="screen_cracked",
        asset_path="assets/props/phone-01/cracked.png",
    )
    layer = PropContentLayer(
        layer_id="phone-01-message",
        prop_id=appearance.prop_id,
        appearance_state_id=appearance.state_id,
        target_region=NormalizedRegion(x=0.16, y=0.12, width=0.68, height=0.72),
        exact_text="今晚 23:30，旧广播站见。",
        font_family="Noto Sans CJK SC",
        font_size_px=36,
        text_color="#F4F4F4",
    )

    assert "exact_text" not in PropAppearanceState.model_fields
    assert "appearance" not in PropContentLayer.model_fields
    assert layer.appearance_state_id == appearance.state_id


def test_content_layer_is_a_deterministic_post_composite_spec() -> None:
    text_layer = PropContentLayer(
        layer_id="letter-body",
        prop_id="letter-01",
        appearance_state_id="letter-01-open",
        target_region=NormalizedRegion(x=0.1, y=0.2, width=0.8, height=0.6),
        exact_text="撤离时间：9 月 3 日 06:00",
        font_family="Source Han Serif SC",
        font_size_px=28,
        text_color="#201A17",
        horizontal_alignment="left",
    )
    screen_layer = PropContentLayer(
        layer_id="id-card-fields",
        prop_id="id-card-01",
        appearance_state_id="id-card-01-front",
        target_region=NormalizedRegion(x=0.42, y=0.2, width=0.5, height=0.6),
        rendered_content_path="assets/props/id-card-01/exact-fields.png",
    )

    assert text_layer.post_process_required is True
    assert text_layer.image_model_output_is_authoritative is False
    assert text_layer.composition_method == "deterministic_post_composite"
    assert screen_layer.rendered_content_path.endswith("exact-fields.png")

    with pytest.raises(ValidationError):
        PropContentLayer.model_validate(
            {
                **text_layer.model_dump(),
                "image_model_output_is_authoritative": True,
            }
        )


def test_content_layer_requires_exactly_one_authoritative_content_source() -> None:
    common = {
        "layer_id": "screen-content",
        "prop_id": "phone-01",
        "appearance_state_id": "phone-01-on",
        "target_region": NormalizedRegion(x=0.1, y=0.1, width=0.8, height=0.8),
    }

    with pytest.raises(ValidationError, match="exactly one"):
        PropContentLayer(**common)
    with pytest.raises(ValidationError, match="exactly one"):
        PropContentLayer(
            **common,
            exact_text="authoritative",
            rendered_content_path="authoritative.png",
        )


def test_prop_models_are_frozen_and_forbid_unknown_fields() -> None:
    usage = PropUsage()

    with pytest.raises(ValidationError):
        usage.plot_critical = True
    with pytest.raises(ValidationError):
        PropUsage(unknown=True)
