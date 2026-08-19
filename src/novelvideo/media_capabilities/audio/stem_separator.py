"""Local, optional two-stem audio separation backed by the Demucs CLI."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable
from uuid import uuid4


class StemSeparationError(RuntimeError):
    """Demucs could not produce a trustworthy pair of stems."""


class StemSeparationUnavailable(StemSeparationError):
    """The optional Demucs capability is not installed."""


@dataclass(frozen=True, slots=True)
class StemSeparationResult:
    source: Path
    vocals: Path
    no_vocals: Path
    status: str
    model: str


class DemucsStemSeparator:
    """Run Demucs without a shell and validate its two-stem output."""

    def __init__(
        self,
        *,
        executable: str | None = None,
        model: str = "htdemucs",
        device: str = "cpu",
        timeout: float = 30 * 60,
        terminate_timeout: float = 5,
        audio_probe: Callable[[Path], Awaitable[float]] | None = None,
    ) -> None:
        if executable is not None and not executable.strip():
            raise ValueError("executable must not be empty")
        if not model.strip():
            raise ValueError("model must not be empty")
        if not device.strip():
            raise ValueError("device must not be empty")
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        if terminate_timeout <= 0:
            raise ValueError("terminate_timeout must be positive")
        self.executable = executable
        self.model = model
        self.device = device
        self.timeout = float(timeout)
        self.terminate_timeout = float(terminate_timeout)
        self.audio_probe = audio_probe or self._probe_audio

    @property
    def resolved_executable(self) -> str | None:
        configured = self.executable or os.getenv("DRAMACLAW_DEMUCS_BIN") or "demucs"
        return shutil.which(configured)

    @property
    def available(self) -> bool:
        return self.resolved_executable is not None

    async def separate(
        self, source: str | Path, output_directory: str | Path
    ) -> StemSeparationResult:
        executable = self.resolved_executable
        if executable is None:
            raise StemSeparationUnavailable(
                f"Demucs audio separation is unavailable: {self.executable!r} was not found"
            )

        source_path = Path(source).expanduser().resolve()
        if not source_path.is_file():
            raise StemSeparationError(f"source audio does not exist: {source_path}")
        output_path = Path(output_directory).expanduser().resolve()
        output_path.mkdir(parents=True, exist_ok=True)
        digest = await asyncio.to_thread(self._sha256, source_path)
        stable_directory = output_path / self.model / digest
        run_output = output_path / ".demucs-runs" / uuid4().hex
        run_output.mkdir(parents=True)
        try:
            process = await asyncio.create_subprocess_exec(
                executable,
                "--two-stems",
                "vocals",
                "-n",
                self.model,
                "-d",
                self.device,
                "-o",
                str(run_output),
                str(source_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                _stdout, stderr = await asyncio.wait_for(
                    process.communicate(), timeout=self.timeout
                )
            except TimeoutError as exc:
                await self._terminate(process, timeout=self.terminate_timeout)
                raise StemSeparationError(
                    f"Demucs separation timed out after {self.timeout:g} seconds"
                ) from exc
            except asyncio.CancelledError:
                await self._terminate(process, timeout=self.terminate_timeout)
                raise
            if process.returncode != 0:
                detail = stderr.decode(errors="replace").strip()
                suffix = f": {detail}" if detail else ""
                raise StemSeparationError(
                    f"Demucs separation failed with exit code {process.returncode}{suffix}"
                )
            run_stems = run_output / self.model / source_path.stem
            await self._validate_stems(run_stems)
            stable_directory.parent.mkdir(parents=True, exist_ok=True)
            try:
                run_stems.rename(stable_directory)
            except OSError as exc:
                if not stable_directory.is_dir():
                    raise StemSeparationError("could not atomically publish stems") from exc
            await self._validate_stems(stable_directory)
        finally:
            await asyncio.to_thread(shutil.rmtree, run_output, True)

        vocals = stable_directory / "vocals.wav"
        no_vocals = stable_directory / "no_vocals.wav"
        return StemSeparationResult(
            source=source_path,
            vocals=vocals,
            no_vocals=no_vocals,
            status="succeeded",
            model=self.model,
        )

    async def _validate_stems(self, directory: Path) -> None:
        for path in (directory / "vocals.wav", directory / "no_vocals.wav"):
            current = self._fingerprint(path)
            if current is None or current[0] <= 0:
                raise StemSeparationError(
                    f"Demucs output is missing or empty/stale: {path.name}"
                )
            try:
                duration = await self.audio_probe(path)
            except (StemSeparationError, asyncio.CancelledError):
                raise
            except Exception as exc:
                raise StemSeparationError(
                    f"Demucs output is not decodable: {path.name}"
                ) from exc
            if duration <= 0:
                raise StemSeparationError(
                    f"Demucs output has invalid duration: {path.name}"
                )

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _fingerprint(path: Path) -> tuple[int, int] | None:
        try:
            stat = path.stat()
        except OSError:
            return None
        return stat.st_size, stat.st_mtime_ns

    @staticmethod
    async def _terminate(
        process: asyncio.subprocess.Process, *, timeout: float
    ) -> None:
        if process.returncode is None:
            process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=timeout)
            return
        except TimeoutError:
            if process.returncode is None:
                process.kill()
        try:
            await asyncio.wait_for(process.wait(), timeout=timeout)
        except TimeoutError:
            return

    async def _probe_audio(self, path: Path) -> float:
        ffprobe = shutil.which("ffprobe")
        if ffprobe is None:
            raise StemSeparationUnavailable("audio validation unavailable: ffprobe not found")
        process = await asyncio.create_subprocess_exec(
            ffprobe, "-v", "error", "-show_entries", "format=duration",
            "-of", "json", str(path), stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=min(self.timeout, 30)
            )
        except TimeoutError as exc:
            await self._terminate(process, timeout=self.terminate_timeout)
            raise StemSeparationError("ffprobe audio validation timed out") from exc
        except asyncio.CancelledError:
            await self._terminate(process, timeout=self.terminate_timeout)
            raise
        if process.returncode != 0:
            detail = stderr.decode(errors="replace").strip()
            raise StemSeparationError(
                f"Demucs output is not decodable: {path.name}: {detail}"
            )
        try:
            payload = json.loads(stdout)
            return float((payload.get("format") or {}).get("duration") or 0)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise StemSeparationError(
                f"Demucs output is not decodable: {path.name}"
            ) from exc


__all__ = [
    "DemucsStemSeparator",
    "StemSeparationError",
    "StemSeparationResult",
    "StemSeparationUnavailable",
]
