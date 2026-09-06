from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

from PIL import Image

from scripts import smoke_runninghub_h3_ref as smoke


def _image(path: Path, color: str) -> Path:
    Image.new("RGB", (8, 8), color).save(path)
    return path


async def _probe_720p(_path: Path):
    return SimpleNamespace(width=736, height=1280)


def test_smoke_refuses_real_call_without_explicit_authorization(
    tmp_path: Path, capsys
) -> None:
    called = False

    async def forbidden_generator(*_args, **_kwargs):
        nonlocal called
        called = True

    exit_code = smoke.main([], environ={}, generator=forbidden_generator)

    assert exit_code == 2
    assert "RUNNINGHUB_REAL_SMOKE=1" in capsys.readouterr().err
    assert called is False


def test_smoke_requires_reference_first_frame_and_explicit_resolution(
    tmp_path: Path, capsys
) -> None:
    async def forbidden_generator(*_args, **_kwargs):
        raise AssertionError("invalid input must not reach the generator")

    exit_code = smoke.main(
        ["--resolution", "720p", "--output", str(tmp_path / "out.mp4")],
        environ={"RUNNINGHUB_REAL_SMOKE": "1"},
        generator=forbidden_generator,
    )

    assert exit_code == 2
    error = capsys.readouterr().err
    assert "--reference" in error
    assert "--first-frame" in error

    reference = _image(tmp_path / "reference.png", "green")
    first = _image(tmp_path / "first.png", "black")
    exit_code = smoke.main(
        [
            "--reference", str(reference),
            "--first-frame", str(first),
            "--output", str(tmp_path / "out.mp4"),
        ],
        environ={"RUNNINGHUB_REAL_SMOKE": "1"},
        generator=forbidden_generator,
    )

    assert exit_code == 2
    assert "--resolution" in capsys.readouterr().err

    exit_code = smoke.main(
        [
            "--reference", str(reference),
            "--subject", "   ",
            "--first-frame", str(first),
            "--resolution", "720p",
            "--output", str(tmp_path / "out.mp4"),
        ],
        environ={"RUNNINGHUB_REAL_SMOKE": "1"},
        generator=forbidden_generator,
    )

    assert exit_code == 2
    assert "--subject" in capsys.readouterr().err


def test_smoke_submits_refs_first_and_optional_last_frame_once(
    tmp_path: Path, capsys
) -> None:
    reference = _image(tmp_path / "reference.png", "green")
    first = _image(tmp_path / "first.png", "black")
    last = _image(tmp_path / "last.png", "blue")
    output = tmp_path / "result.mp4"

    class FakeClient:
        def __init__(self) -> None:
            self.calls = []

        async def generate(self, ctx, **kwargs):
            self.calls.append((ctx, kwargs))
            output.write_bytes(b"fake video")
            return SimpleNamespace(
                provider_task_id="provider-task-1",
                output_path=str(output),
                actual_mode="fl2va",
            )

    client = FakeClient()
    exit_code = smoke.main(
        [
            "--reference", str(reference),
            "--first-frame", str(first),
            "--last-frame", str(last),
            "--resolution", "720p",
            "--output", str(output),
        ],
        environ={"RUNNINGHUB_REAL_SMOKE": "1"},
        generator=client.generate,
        probe_video=_probe_720p,
    )

    assert exit_code == 0
    assert len(client.calls) == 1
    ctx, request = client.calls[0]
    runtime_dir = Path(ctx.runtime_dir)
    assert runtime_dir.parent == tmp_path
    assert runtime_dir.exists() is False
    assert request["workflow_id"] == "2096502793044582401"
    assert request["reference_limit"] == 5
    assert len(request["global_references"]) == 1
    assert request["global_references"][0].content == reference.read_bytes()
    assert request["segments"][0].first_frame == str(first)
    assert request["segments"][0].last_frame == str(last)
    assert set(request["frozen_frames"]) == {str(first), str(last)}
    assert request["frozen_frames"][str(first)].content == first.read_bytes()
    assert request["frozen_frames"][str(last)].content == last.read_bytes()
    assert request["resolution"] == "720p"
    stdout = capsys.readouterr().out
    assert "workflow_id=2096502793044582401" in stdout
    assert "provider_task_id=provider-task-1" in stdout
    assert "input_sha256=" in stdout
    assert "status=succeeded" in stdout
    assert "runtime_cache=fresh" in stdout
    assert f"output_sha256={hashlib.sha256(b'fake video').hexdigest()}" in stdout
    assert "actual_size=736x1280" in stdout
    assert f"output_path={output}" in stdout


def test_remote_rejection_does_not_retry_or_fallback(tmp_path: Path, capsys) -> None:
    reference = _image(tmp_path / "reference.png", "green")
    first = _image(tmp_path / "first.png", "black")
    calls = []

    async def rejecting_generator(_ctx, **kwargs):
        calls.append(kwargs)
        raise RuntimeError("provider rejected hybrid payload")

    exit_code = smoke.main(
        [
            "--reference", str(reference),
            "--first-frame", str(first),
            "--resolution", "720p",
            "--output", str(tmp_path / "rejected.mp4"),
        ],
        environ={"RUNNINGHUB_REAL_SMOKE": "1"},
        generator=rejecting_generator,
        probe_video=_probe_720p,
    )

    assert exit_code == 1
    assert len(calls) == 1
    assert calls[0]["workflow_id"] == smoke.WORKFLOW_ID
    assert calls[0]["global_references"]
    assert "status=failed" in capsys.readouterr().err


def test_invalid_output_never_calls_generator(tmp_path: Path, capsys) -> None:
    reference = _image(tmp_path / "reference.png", "green")
    first = _image(tmp_path / "first.png", "black")
    calls = []

    async def forbidden_generator(*args, **kwargs):
        calls.append((args, kwargs))

    for output in (
        tmp_path,
        tmp_path / "existing.mp4",
        tmp_path / "missing-parent" / "out.mp4",
    ):
        if output.name == "existing.mp4":
            output.write_bytes(b"keep me")
        exit_code = smoke.main(
            [
                "--reference", str(reference),
                "--first-frame", str(first),
                "--resolution", "720p",
                "--output", str(output),
            ],
            environ={"RUNNINGHUB_REAL_SMOKE": "1"},
            generator=forbidden_generator,
            probe_video=_probe_720p,
        )
        assert exit_code == 2

    assert calls == []
    assert (tmp_path / "existing.mp4").read_bytes() == b"keep me"
    assert "--output" in capsys.readouterr().err


def test_missing_or_wrong_size_artifact_is_not_success(tmp_path: Path, capsys) -> None:
    reference = _image(tmp_path / "reference.png", "green")
    first = _image(tmp_path / "first.png", "black")

    async def missing_generator(_ctx, **kwargs):
        return SimpleNamespace(
            provider_task_id="provider-missing",
            output_path=kwargs["output_path"],
        )

    output = tmp_path / "missing.mp4"
    common = [
        "--reference", str(reference),
        "--first-frame", str(first),
        "--resolution", "720p",
        "--output", str(output),
    ]
    exit_code = smoke.main(
        common,
        environ={"RUNNINGHUB_REAL_SMOKE": "1"},
        generator=missing_generator,
        probe_video=_probe_720p,
    )
    captured = capsys.readouterr()
    assert exit_code == 1
    assert "status=succeeded" not in captured.out
    assert "status=failed" in captured.err

    async def wrong_size_generator(_ctx, **kwargs):
        Path(kwargs["output_path"]).write_bytes(b"wrong size video")
        return SimpleNamespace(
            provider_task_id="provider-wrong-size",
            output_path=kwargs["output_path"],
        )

    async def wrong_size_probe(_path: Path):
        return SimpleNamespace(width=1280, height=720)

    exit_code = smoke.main(
        [*common[:-1], str(tmp_path / "wrong-size.mp4")],
        environ={"RUNNINGHUB_REAL_SMOKE": "1"},
        generator=wrong_size_generator,
        probe_video=wrong_size_probe,
    )
    captured = capsys.readouterr()
    assert exit_code == 1
    assert "status=succeeded" not in captured.out
    assert "actual video size" in captured.err

    async def quality_failed_generator(_ctx, **kwargs):
        Path(kwargs["output_path"]).write_bytes(b"quality failed video")
        return SimpleNamespace(
            status="quality_failed",
            provider_task_id="provider-quality-failed",
            output_path=kwargs["output_path"],
        )

    exit_code = smoke.main(
        [*common[:-1], str(tmp_path / "quality-failed.mp4")],
        environ={"RUNNINGHUB_REAL_SMOKE": "1"},
        generator=quality_failed_generator,
        probe_video=_probe_720p,
    )
    captured = capsys.readouterr()
    assert exit_code == 1
    assert "status=succeeded" not in captured.out
    assert "non-success status" in captured.err


def test_input_image_limits_fail_before_generator(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    reference = tmp_path / "reference.gif"
    Image.new("RGB", (8, 8), "green").save(reference, format="GIF")
    first = _image(tmp_path / "first.png", "black")
    calls = []

    async def forbidden_generator(*args, **kwargs):
        calls.append((args, kwargs))

    args = [
        "--reference", str(reference),
        "--first-frame", str(first),
        "--resolution", "720p",
        "--output", str(tmp_path / "out.mp4"),
    ]
    assert smoke.main(
        args,
        environ={"RUNNINGHUB_REAL_SMOKE": "1"},
        generator=forbidden_generator,
        probe_video=_probe_720p,
    ) == 2

    reference = _image(tmp_path / "large.png", "green")
    monkeypatch.setattr(smoke, "MAX_VIDEO_REFERENCE_BYTES", 4)
    args[1] = str(reference)
    assert smoke.main(
        args,
        environ={"RUNNINGHUB_REAL_SMOKE": "1"},
        generator=forbidden_generator,
        probe_video=_probe_720p,
    ) == 2
    assert calls == []
    error = capsys.readouterr().err
    assert "unsupported" in error
    assert "20 MiB" in error


def test_frame_format_and_reference_pixel_limit_fail_before_generator(
    tmp_path: Path, capsys
) -> None:
    reference = _image(tmp_path / "reference.png", "green")
    invalid_frame = tmp_path / "first.gif"
    Image.new("RGB", (8, 8), "black").save(invalid_frame, format="GIF")
    calls = []

    async def forbidden_generator(*args, **kwargs):
        calls.append((args, kwargs))

    base = [
        "--reference", str(reference),
        "--first-frame", str(invalid_frame),
        "--resolution", "720p",
        "--output", str(tmp_path / "out.mp4"),
    ]
    assert smoke.main(
        base,
        environ={"RUNNINGHUB_REAL_SMOKE": "1"},
        generator=forbidden_generator,
        probe_video=_probe_720p,
    ) == 2

    oversized_pixels = tmp_path / "oversized-pixels.png"
    Image.new("1", (8000, 5001)).save(oversized_pixels, format="PNG")
    base[1] = str(oversized_pixels)
    base[3] = str(_image(tmp_path / "valid-first.png", "black"))
    assert smoke.main(
        base,
        environ={"RUNNINGHUB_REAL_SMOKE": "1"},
        generator=forbidden_generator,
        probe_video=_probe_720p,
    ) == 2
    assert calls == []
    error = capsys.readouterr().err
    assert "unsupported" in error
    assert "pixel limit" in error
