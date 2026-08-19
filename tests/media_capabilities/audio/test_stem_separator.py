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

    async def communicate(self) -> tuple[bytes, bytes]:
        self.returncode = self._completed_returncode
        return b"", b"demucs failed" if self.returncode else b""

    def terminate(self) -> None:
        self.terminated = True

    async def wait(self) -> int:
        self.returncode = self._completed_returncode
        return self.returncode


def test_unavailable_when_demucs_executable_cannot_be_resolved(monkeypatch) -> None:
    monkeypatch.setattr("shutil.which", lambda _command: None)
    separator = DemucsStemSeparator()
    assert separator.available is False
    with pytest.raises(StemSeparationUnavailable, match="Demucs.*unavailable"):
        asyncio.run(separator.separate("track.wav", "stems"))


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
    separator = DemucsStemSeparator(model="htdemucs", device="cpu", timeout=5)
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
    assert source.read_bytes() == b"original"


async def test_bad_or_incomplete_output_fails_closed(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "song.wav"
    source.write_bytes(b"original")

    async def create(*_argv, **_kwargs):
        return FakeProcess()

    monkeypatch.setattr("shutil.which", lambda _command: "demucs")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
    with pytest.raises(StemSeparationError, match="missing or empty"):
        await DemucsStemSeparator().separate(source, tmp_path / "stems")
    assert source.exists()


@pytest.mark.parametrize("cancelled", [False, True])
async def test_timeout_or_cancellation_terminates_process(
    tmp_path: Path, monkeypatch, cancelled: bool
) -> None:
    source = tmp_path / "song.wav"
    source.write_bytes(b"original")
    process = FakeProcess()

    async def create(*_argv, **_kwargs):
        return process

    async def stop(_awaitable, _timeout):
        if cancelled:
            raise asyncio.CancelledError
        raise TimeoutError

    monkeypatch.setattr("shutil.which", lambda _command: "demucs")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
    monkeypatch.setattr(asyncio, "wait_for", stop)
    expected = asyncio.CancelledError if cancelled else StemSeparationError
    with pytest.raises(expected, match=None if cancelled else "timed out"):
        await DemucsStemSeparator(timeout=0.01).separate(source, tmp_path / "stems")
    assert process.terminated is True
    assert source.exists()
