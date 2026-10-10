import pytest


def test_grsai_canvas_size_preserves_ratio_and_rejects_unsupported_tier():
    from novelvideo.freezone.jobs import _grsai_canvas_size
    assert _grsai_canvas_size("gpt-image-2", "16:9", "1K") == "1280x720"
    assert _grsai_canvas_size("gpt-image-2-vip", "16:9", "2K") == "2048x1152"
    with pytest.raises(ValueError, match="does not support"):
        _grsai_canvas_size("gpt-image-2", "16:9", "2K")
    assert _grsai_canvas_size("nano-banana-pro", "16:9", "2K") == "2K"


def test_configured_catalog_models_route_to_grsai():
    import novelvideo.api  # Initialize route imports before their schema helper.
    from novelvideo.freezone.route_helpers import split_provider_and_model, resolve_freezone_image_provider
    assert split_provider_and_model(None, "gpt-image-2") == ("grsai", "gpt-image-2")
    assert resolve_freezone_image_provider("grsai") == "grsai"


@pytest.mark.asyncio
@pytest.mark.parametrize("edit", [False, True])
async def test_shared_canvas_image_jobs_use_grsai_runtime(monkeypatch, tmp_path, edit):
    from novelvideo.freezone import jobs
    calls = []
    async def generate(**kwargs):
        calls.append(kwargs)
        return kwargs["out"]
    monkeypatch.setattr(jobs, "_run_grsai_image", generate, raising=False)
    if edit:
        await jobs.run_freezone_edit(project_dir=tmp_path, job_id="a", prompt="edit", base_path="base.png",
                                    extra_reference_paths=["other.png"], provider="grsai", model="gpt-image-2")
    else:
        await jobs.run_freezone_gen(project_dir=tmp_path, job_id="a", prompt="draw", provider="grsai", model="gpt-image-2")
    assert len(calls) == 1
    assert calls[0]["reference_paths"] == (["base.png", "other.png"] if edit else [])
