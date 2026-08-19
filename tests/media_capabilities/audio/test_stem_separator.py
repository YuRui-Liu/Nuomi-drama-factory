from __future__ import annotations

import asyncio
import hashlib
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
        run_output = Path(argv[argv.index("-o") + 1])
        stem_dir = run_output / "htdemucs" / source.stem
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
    argv = call["argv"]
    assert argv[:7] == (
        "C:/tools/demucs.exe", "--two-stems", "vocals", "-n", "htdemucs",
        "-d", "cpu",
    )
    assert argv[-3] == "-o"
    assert Path(argv[-1]) == source.resolve()
    assert Path(argv[-2]).parent.name == ".demucs-runs"
    assert "shell" not in call["kwargs"]
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    stable = (output / "htdemucs" / digest).resolve()
    assert result.vocals == stable / "vocals.wav"
    assert result.no_vocals == stable / "no_vocals.wav"
    assert result.source == source.resolve()
    assert result.status == "succeeded"
    assert result.model == "htdemucs"
    assert probed[-2:] == [result.vocals, result.no_vocals]
    assert [path.name for path in probed[:2]] == ["vocals.wav", "no_vocals.wav"]
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
    created = asyncio.Event()

    async def communicate() -> tuple[bytes, bytes]:
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    process.communicate = communicate  # type: ignore[method-assign]

    async def create(*_argv, **_kwargs):
        created.set()
        return process

    monkeypatch.setattr("shutil.which", lambda _command: "demucs")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
    operation = DemucsStemSeparator(timeout=0.001).separate(source, tmp_path / "stems")
    if cancelled:
        task = asyncio.create_task(operation)
        await created.wait()
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
        run_output = Path(_argv[_argv.index("-o") + 1])
        stem_dir = run_output / "htdemucs" / source.stem
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


async def test_same_name_different_content_publishes_distinct_stems(
    tmp_path: Path, monkeypatch
) -> None:
    first = tmp_path / "a" / "song.wav"
    second = tmp_path / "b" / "song.wav"
    first.parent.mkdir()
    second.parent.mkdir()
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    output = tmp_path / "stems"

    async def create(*argv, **_kwargs):
        source = Path(argv[-1])
        run_output = Path(argv[argv.index("-o") + 1])
        stem_dir = run_output / "htdemucs" / source.stem
        stem_dir.mkdir(parents=True)
        (stem_dir / "vocals.wav").write_bytes(source.read_bytes() + b"-v")
        (stem_dir / "no_vocals.wav").write_bytes(source.read_bytes() + b"-m")
        return FakeProcess()

    async def probe(_path: Path) -> float:
        return 1.0

    monkeypatch.setattr("shutil.which", lambda _command: "demucs")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
    separator = DemucsStemSeparator(audio_probe=probe)
    one = await separator.separate(first, output)
    two = await separator.separate(second, output)
    assert one.vocals.parent != two.vocals.parent
    assert one.vocals.read_bytes() == b"first-v"
    assert two.vocals.read_bytes() == b"second-v"


async def test_concurrent_same_source_uses_isolated_runs_and_converges(
    tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "song.wav"
    source.write_bytes(b"same")
    output = tmp_path / "stems"
    run_outputs: list[Path] = []

    async def create(*argv, **_kwargs):
        run_output = Path(argv[argv.index("-o") + 1])
        run_outputs.append(run_output)
        stem_dir = run_output / "htdemucs" / source.stem
        stem_dir.mkdir(parents=True)
        (stem_dir / "vocals.wav").write_bytes(b"voice")
        (stem_dir / "no_vocals.wav").write_bytes(b"music")
        return FakeProcess()

    async def probe(_path: Path) -> float:
        await asyncio.sleep(0)
        return 1.0

    monkeypatch.setattr("shutil.which", lambda _command: "demucs")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
    separator = DemucsStemSeparator(audio_probe=probe)
    first, second = await asyncio.gather(
        separator.separate(source, output), separator.separate(source, output)
    )
    assert len(set(run_outputs)) == 2
    assert first.vocals == second.vocals
    assert first.no_vocals == second.no_vocals
    assert first.vocals.read_bytes() == b"voice"
