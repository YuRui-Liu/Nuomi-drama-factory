from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from novelvideo.media_capabilities.audio.stem_separator import (
    DemucsStemSeparator,
    StemSeparationError,
    StemSeparationUnavailable,
)


class FakeProcess:
    def __init__(self, *, returncode: int = 0) -> None:
        self.returncode: int | None = None
        self._completed_returncode = returncode
        self.terminated = False
        self.killed = False

    async def communicate(self) -> tuple[bytes, bytes]:
        self.returncode = self._completed_returncode
        return b"", b"demucs failed" if self.returncode else b""

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True

    async def wait(self) -> int:
        self.returncode = self._completed_returncode
        return self.returncode


def test_unavailable_when_demucs_executable_cannot_be_resolved(monkeypatch) -> None:
    monkeypatch.setattr("shutil.which", lambda _command: None)
    separator = DemucsStemSeparator()
    assert separator.available is False
    with pytest.raises(StemSeparationUnavailable, match="Demucs.*unavailable"):
        asyncio.run(separator.separate("track.wav", "stems"))


def test_executable_precedence_is_explicit_then_environment_then_default(monkeypatch) -> None:
    seen: list[str] = []

    def which(command: str) -> str | None:
        seen.append(command)
        return f"resolved/{command}"

    monkeypatch.setenv("DRAMACLAW_DEMUCS_BIN", "env-demucs")
    monkeypatch.setattr("shutil.which", which)
    assert DemucsStemSeparator(executable="explicit-demucs").resolved_executable == (
        "resolved/explicit-demucs"
    )
    assert DemucsStemSeparator().resolved_executable == "resolved/env-demucs"
    monkeypatch.delenv("DRAMACLAW_DEMUCS_BIN")
    assert DemucsStemSeparator().resolved_executable == "resolved/demucs"
    assert seen == ["explicit-demucs", "env-demucs", "demucs"]


async def test_separate_uses_safe_argv_and_maps_two_stems(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "song; touch unsafe.wav"
    source.write_bytes(b"original")
    output = tmp_path / "stems"
    process = FakeProcess()
    call: dict[str, object] = {}

    async def create(*argv, **kwargs):
        call["argv"] = argv
        call["kwargs"] = kwargs
        stem_dir = output / "htdemucs" / source.stem
        stem_dir.mkdir(parents=True)
        (stem_dir / "vocals.wav").write_bytes(b"vocals")
        (stem_dir / "no_vocals.wav").write_bytes(b"music")
        return process

    monkeypatch.setattr("shutil.which", lambda _command: "C:/tools/demucs.exe")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
    probed: list[Path] = []

    async def probe(path: Path) -> float:
        probed.append(path)
        return 1.5

    separator = DemucsStemSeparator(
        model="htdemucs", device="cpu", timeout=5, audio_probe=probe
    )
    result = await separator.separate(source, output)
    assert call["argv"] == (
        "C:/tools/demucs.exe", "--two-stems", "vocals", "-n", "htdemucs",
        "-d", "cpu", "-o", str(output.resolve()), str(source.resolve()),
    )
    assert "shell" not in call["kwargs"]
    assert result.vocals == (output / "htdemucs" / source.stem / "vocals.wav").resolve()
    assert result.no_vocals == (output / "htdemucs" / source.stem / "no_vocals.wav").resolve()
    assert result.source == source.resolve()
    assert result.status == "succeeded"
    assert result.model == "htdemucs"
    assert probed == [result.vocals, result.no_vocals]
    assert source.read_bytes() == b"original"


async def test_bad_or_incomplete_output_fails_closed(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "song.wav"
    source.write_bytes(b"original")

    async def create(*_argv, **_kwargs):
        return FakeProcess()

    monkeypatch.setattr("shutil.which", lambda _command: "demucs")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
    with pytest.raises(StemSeparationError, match="missing or empty"):
        await DemucsStemSeparator(audio_probe=lambda _path: asyncio.sleep(0, result=1.0)).separate(
            source, tmp_path / "stems"
        )
    assert source.exists()


@pytest.mark.parametrize("cancelled", [False, True])
async def test_timeout_or_cancellation_terminates_process(
    tmp_path: Path, monkeypatch, cancelled: bool
) -> None:
    source = tmp_path / "song.wav"
    source.write_bytes(b"original")
    process = FakeProcess()

    async def communicate() -> tuple[bytes, bytes]:
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    process.communicate = communicate  # type: ignore[method-assign]

    async def create(*_argv, **_kwargs):
        return process

    monkeypatch.setattr("shutil.which", lambda _command: "demucs")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
    operation = DemucsStemSeparator(timeout=0.001).separate(source, tmp_path / "stems")
    if cancelled:
        task = asyncio.create_task(operation)
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        with pytest.raises(StemSeparationError, match="timed out"):
            await operation
    assert process.terminated is True
    assert source.exists()


async def test_zero_duration_probe_fails_closed(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "song.wav"
    source.write_bytes(b"original")
    output = tmp_path / "stems"

    async def create(*_argv, **_kwargs):
        stem_dir = output / "htdemucs" / source.stem
        stem_dir.mkdir(parents=True)
        (stem_dir / "vocals.wav").write_bytes(b"not-really-audio")
        (stem_dir / "no_vocals.wav").write_bytes(b"not-really-audio")
        return FakeProcess()

    async def probe(_path: Path) -> float:
        return 0.0

    monkeypatch.setattr("shutil.which", lambda _command: "demucs")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
    with pytest.raises(StemSeparationError, match="not decodable|duration"):
        await DemucsStemSeparator(audio_probe=probe).separate(source, output)


async def test_terminate_escalates_to_kill_when_process_does_not_exit() -> None:
    process = FakeProcess()
    waits = 0

    async def wait() -> int:
        nonlocal waits
        waits += 1
        if waits == 1:
            await asyncio.Event().wait()
        process.returncode = -9
        return -9

    process.wait = wait  # type: ignore[method-assign]
    await DemucsStemSeparator._terminate(process, timeout=0.001)
    assert process.terminated is True
    assert process.killed is True
    assert waits == 2
