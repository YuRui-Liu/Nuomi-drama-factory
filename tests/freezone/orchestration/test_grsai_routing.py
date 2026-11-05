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
        kwargs["out"].write_bytes(b"image")
        return kwargs["out"]
    monkeypatch.setattr(jobs, "_run_grsai_image", generate, raising=False)
    if edit:
        await jobs.run_freezone_edit(project_dir=tmp_path, job_id="a", prompt="edit", base_path="base.png",
                                    extra_reference_paths=["other.png"], provider="grsai", model="gpt-image-2")
    else:
        await jobs.run_freezone_gen(project_dir=tmp_path, job_id="a", prompt="draw", provider="grsai", model="gpt-image-2")
    assert len(calls) == 1
    assert calls[0]["reference_paths"] == (["base.png", "other.png"] if edit else [])


@pytest.mark.asyncio
async def test_freezone_gen_publishes_only_after_staged_image_is_complete(monkeypatch, tmp_path):
    import asyncio
    from novelvideo.freezone import jobs

    out = jobs.output_path_for_job(tmp_path, "freezone_gen", "atomic")
    writing = asyncio.Event()
    finish = asyncio.Event()
    staged_paths = []

    async def generate(**kwargs):
        staged = kwargs["out"]
        staged_paths.append(staged)
        staged.write_bytes(b"partial image")
        writing.set()
        await finish.wait()
        staged.write_bytes(b"complete image")
        return staged

    monkeypatch.setattr(jobs, "_run_grsai_image", generate)
    task = asyncio.create_task(jobs.run_freezone_gen(
        project_dir=tmp_path, job_id="atomic", prompt="draw", provider="grsai", model="gpt-image-2"
    ))
    await writing.wait()
    final_visible_while_writing = out.exists()
    staged_suffix = staged_paths[0].suffix
    finish.set()
    returned = await task

    assert not final_visible_while_writing
    assert staged_paths[0] != out
    assert staged_suffix == out.suffix == ".png"
    assert returned == out
    assert out.read_bytes() == b"complete image"
    assert not staged_paths[0].exists()


@pytest.mark.asyncio
async def test_freezone_gen_failure_preserves_existing_published_image(monkeypatch, tmp_path):
    from novelvideo.freezone import jobs

    out = jobs.output_path_for_job(tmp_path, "freezone_gen", "preserve")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(b"previous successful image")
    staged_paths = []

    async def fail_after_partial_write(**kwargs):
        staged = kwargs["out"]
        staged_paths.append(staged)
        staged.write_bytes(b"partial replacement")
        raise RuntimeError("provider write failed")

    monkeypatch.setattr(jobs, "_run_grsai_image", fail_after_partial_write)
    with pytest.raises(RuntimeError, match="provider write failed"):
        await jobs.run_freezone_gen(
            project_dir=tmp_path, job_id="preserve", prompt="draw", provider="grsai", model="gpt-image-2"
        )

    assert out.read_bytes() == b"previous successful image"
    assert staged_paths and staged_paths[0] != out
    assert not staged_paths[0].exists()
