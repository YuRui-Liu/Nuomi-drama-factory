"""Local, optional two-stem audio separation backed by the Demucs CLI."""

from __future__ import annotations

import asyncio
import shutil
from dataclasses import dataclass
from pathlib import Path


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
        executable: str = "demucs",
        model: str = "htdemucs",
        device: str = "cpu",
        timeout: float = 30 * 60,
    ) -> None:
        if not executable.strip():
            raise ValueError("executable must not be empty")
        if not model.strip():
            raise ValueError("model must not be empty")
        if not device.strip():
            raise ValueError("device must not be empty")
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        self.executable = executable
        self.model = model
        self.device = device
        self.timeout = float(timeout)

    @property
    def resolved_executable(self) -> str | None:
        return shutil.which(self.executable)

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
        stem_directory = output_path / self.model / source_path.stem
        vocals = stem_directory / "vocals.wav"
        no_vocals = stem_directory / "no_vocals.wav"
        previous = {path: self._fingerprint(path) for path in (vocals, no_vocals)}

        process = await asyncio.create_subprocess_exec(
            executable,
            "--two-stems",
            "vocals",
            "-n",
            self.model,
            "-d",
            self.device,
            "-o",
            str(output_path),
            str(source_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=self.timeout
            )
        except TimeoutError as exc:
            await self._terminate(process)
            raise StemSeparationError(
                f"Demucs separation timed out after {self.timeout:g} seconds"
            ) from exc
        except asyncio.CancelledError:
            await self._terminate(process)
            raise

        if process.returncode != 0:
            detail = stderr.decode(errors="replace").strip()
            suffix = f": {detail}" if detail else ""
            raise StemSeparationError(
                f"Demucs separation failed with exit code {process.returncode}{suffix}"
            )
        for path in (vocals, no_vocals):
            current = self._fingerprint(path)
            if current is None or current[0] <= 0 or current == previous[path]:
                raise StemSeparationError(
                    f"Demucs output is missing or empty/stale: {path.name}"
                )
        return StemSeparationResult(
            source=source_path,
            vocals=vocals,
            no_vocals=no_vocals,
            status="succeeded",
            model=self.model,
        )

    @staticmethod
    def _fingerprint(path: Path) -> tuple[int, int] | None:
        try:
            stat = path.stat()
        except OSError:
            return None
        return stat.st_size, stat.st_mtime_ns

    @staticmethod
    async def _terminate(process: asyncio.subprocess.Process) -> None:
        if process.returncode is None:
            process.terminate()
        await process.wait()


__all__ = [
    "DemucsStemSeparator",
    "StemSeparationError",
    "StemSeparationResult",
    "StemSeparationUnavailable",
]
