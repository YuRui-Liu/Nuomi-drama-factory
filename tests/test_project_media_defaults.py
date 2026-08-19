from novelvideo.api.routes.projects import _media_defaults_payload
from novelvideo.api.schemas import MediaDefaultsRequest


def test_media_defaults_use_real_stage_specific_image_models():
    assert _media_defaults_payload({}) == {
        "video_model": "runninghub:minimax-h3",
        "h3_mode": "auto",
        "narrative_sketch_provider": "grsai-main",
        "narrative_sketch_model": "nano-banana-2",
        "narrative_render_provider": "grsai-main",
        "narrative_render_model": "gpt-image-2",
    }


def test_media_defaults_request_preserves_independent_image_bindings():
    request = MediaDefaultsRequest(
        video_model="runninghub:minimax-h3",
        narrative_sketch_provider="grsai-backup",
        narrative_sketch_model="nano-banana-2-4k-cl",
        narrative_render_provider="grsai-main",
        narrative_render_model="gpt-image-2-vip",
    )

    assert request.narrative_sketch_model == "nano-banana-2-4k-cl"
    assert request.narrative_render_model == "gpt-image-2-vip"
