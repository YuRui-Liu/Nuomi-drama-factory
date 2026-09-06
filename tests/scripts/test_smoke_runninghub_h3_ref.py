from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from PIL import Image

from scripts import smoke_runninghub_h3_ref as smoke


def _image(path: Path, color: str) -> Path:
    Image.new("RGB", (8, 8), color).save(path)
    return path


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
        ["--resolution", "720p"],
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
        ["--reference", str(reference), "--first-frame", str(first)],
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
            "--runtime-dir", str(tmp_path / "runtime"),
        ],
        environ={"RUNNINGHUB_REAL_SMOKE": "1"},
        generator=client.generate,
    )

    assert exit_code == 0
    assert len(client.calls) == 1
    ctx, request = client.calls[0]
    assert Path(ctx.runtime_dir) == tmp_path / "runtime"
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
        ],
        environ={"RUNNINGHUB_REAL_SMOKE": "1"},
        generator=rejecting_generator,
    )

    assert exit_code == 1
    assert len(calls) == 1
    assert calls[0]["workflow_id"] == smoke.WORKFLOW_ID
    assert calls[0]["global_references"]
    assert "status=failed" in capsys.readouterr().err
