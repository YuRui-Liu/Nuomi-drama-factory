"""Explicitly authorized compatibility smoke for MiniMax H3 Director Ref."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import stat
import sys
import tempfile
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from types import MappingProxyType

from PIL import Image

from novelvideo.media_capabilities.video.h3_reference_runtime import (
    H3FrozenFrame,
)
from novelvideo.media_capabilities.video.h3_size_settings import (
    resolve_h3_size_setting,
)
from novelvideo.media_capabilities.video.h3_timeline import H3DirectorSegment
from novelvideo.media_capabilities.video.runtime import _probe_video
from novelvideo.narrative_groups.video_references import (
    MAX_VIDEO_REFERENCE_BYTES,
    ResolvedVideoReference,
    _validate_decoded_image,
)


WORKFLOW_ID = "2096502793044582401"
_REAL_SMOKE_ENV = "RUNNINGHUB_REAL_SMOKE"
_DEFAULT_REFERENCE_LIMIT = 5

Generator = Callable[..., Awaitable[object]]
ProbeVideo = Callable[[Path], Awaitable[object]]


@dataclass(frozen=True, slots=True)
class _PreparedInputs:
    references: tuple[ResolvedVideoReference, ...]
    segment: H3DirectorSegment
    frozen_frames: Mapping[str, H3FrozenFrame]


@dataclass(frozen=True, slots=True)
class _OutputReservation:
    path: Path
    device: int
    inode: int


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Submit exactly one paid MiniMax H3 Director Ref compatibility smoke. "
            "Requires RUNNINGHUB_REAL_SMOKE=1."
        )
    )
    parser.add_argument("--reference", type=Path, action="append", default=[])
    parser.add_argument("--subject", action="append", default=[])
    parser.add_argument("--first-frame", type=Path)
    parser.add_argument("--last-frame", type=Path)
    parser.add_argument("--resolution", choices=("720p", "1080p"))
    parser.add_argument("--aspect-ratio", choices=("9:16", "16:9"), default="9:16")
    parser.add_argument("--reference-limit", type=int, default=_DEFAULT_REFERENCE_LIMIT)
    parser.add_argument("--prompt", default="人物轻轻眨眼并缓慢抬头，镜头稳定")
    parser.add_argument("--duration", type=float, default=5.0)
    parser.add_argument("--output", type=Path)
    return parser


def _validation_errors(args: argparse.Namespace) -> list[str]:
    errors: list[str] = []
    if not args.reference:
        errors.append("at least one --reference is required")
    if args.first_frame is None:
        errors.append("--first-frame is required")
    if args.resolution is None:
        errors.append("--resolution is required")
    if args.output is None:
        errors.append("--output is required")
    if not 1 <= args.reference_limit <= 10:
        errors.append("--reference-limit must be from 1 to 10")
    if len(args.reference) > args.reference_limit:
        errors.append("--reference count exceeds --reference-limit")
    if args.subject and len(args.subject) != len(args.reference):
        errors.append("repeat --subject once for every --reference, or omit it")
    if any(not subject.strip() for subject in args.subject):
        errors.append("--subject must not be blank")
    if args.duration <= 0:
        errors.append("--duration must be positive")
    for flag, path in [
        *(("--reference", path) for path in args.reference),
        ("--first-frame", args.first_frame),
        ("--last-frame", args.last_frame),
    ]:
        if path is None:
            continue
        if not path.is_absolute():
            errors.append(f"{flag} must be an absolute path: {path}")
        elif not path.is_file():
            errors.append(f"{flag} file is unavailable: {path}")
    return errors


def _snapshot_image(path: Path, *, label: str) -> bytes:
    with path.open("rb") as handle:
        content = handle.read(MAX_VIDEO_REFERENCE_BYTES + 1)
    if len(content) > MAX_VIDEO_REFERENCE_BYTES:
        raise ValueError(f"{label} exceeds the 20 MiB image limit")
    _validate_decoded_image(content, label)
    return content


def _frozen_frame(path: Path, *, label: str) -> H3FrozenFrame:
    content = _snapshot_image(path, label=label)
    with Image.open(BytesIO(content)) as image:
        suffix = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp"}[str(image.format)]
        width, height = image.size
    return H3FrozenFrame(
        source=str(path),
        content=content,
        sha256=hashlib.sha256(content).hexdigest(),
        width=width,
        height=height,
        suffix=suffix,
    )


def _resolved_references(args: argparse.Namespace) -> tuple[ResolvedVideoReference, ...]:
    references = []
    for index, path in enumerate(args.reference, start=1):
        content = _snapshot_image(path, label=f"reference {index}")
        digest = hashlib.sha256(content).hexdigest()
        label = path.stem.strip() or f"Reference {index}"
        subject = args.subject[index - 1].strip() if args.subject else label
        if not subject:
            raise ValueError(f"--subject {index} must not be blank")
        references.append(
            ResolvedVideoReference(
                reference_id=f"smoke-ref-{index}-{digest[:12]}",
                source_kind="temporary_upload",
                label=label,
                subject_description=subject,
                path=path,
                content=content,
                sha256=digest,
            )
        )
    return tuple(references)


def _prepare_inputs(args: argparse.Namespace) -> _PreparedInputs:
    references = _resolved_references(args)
    segment = H3DirectorSegment(
        segment_id="h3-ref-real-smoke",
        beat_number=1,
        prompt=args.prompt,
        duration_seconds=args.duration,
        first_frame=str(args.first_frame),
        last_frame=(str(args.last_frame) if args.last_frame else None),
    )
    frames = {
        str(args.first_frame): _frozen_frame(args.first_frame, label="first frame")
    }
    if args.last_frame is not None:
        frames[str(args.last_frame)] = _frozen_frame(args.last_frame, label="last frame")
    return _PreparedInputs(references, segment, MappingProxyType(frames))


def _reserve_output(path: Path) -> _OutputReservation:
    if not path.is_absolute():
        raise ValueError(f"--output must be an absolute path: {path}")
    parent = path.parent
    if not parent.is_dir():
        raise ValueError(f"--output parent directory must already exist: {parent}")
    if parent.is_symlink() or parent.resolve(strict=True) != parent:
        raise ValueError("--output parent must not traverse symbolic links")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError as exc:
        raise ValueError(f"--output already exists and will not be overwritten: {path}") from exc
    except OSError as exc:
        raise ValueError(f"--output cannot be safely created: {path}") from exc
    try:
        created = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    return _OutputReservation(path=path, device=created.st_dev, inode=created.st_ino)


def _remove_unused_reservation(reservation: _OutputReservation) -> None:
    try:
        current = reservation.path.lstat()
    except FileNotFoundError:
        return
    if (
        current.st_dev == reservation.device
        and current.st_ino == reservation.inode
        and current.st_size == 0
        and stat.S_ISREG(current.st_mode)
    ):
        reservation.path.unlink()


def _input_digest(
    args: argparse.Namespace,
    references: Sequence[ResolvedVideoReference],
    frames: Mapping[str, H3FrozenFrame],
) -> str:
    payload = {
        "workflow_id": WORKFLOW_ID,
        "reference_limit": args.reference_limit,
        "references": [reference.sha256 for reference in references],
        "first_frame": frames[str(args.first_frame)].sha256,
        "last_frame": (
            frames[str(args.last_frame)].sha256 if args.last_frame is not None else None
        ),
        "prompt": args.prompt,
        "duration": args.duration,
        "aspect_ratio": args.aspect_ratio,
        "resolution": args.resolution,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
    return hashlib.sha256(encoded).hexdigest()


async def _verify_output(
    args: argparse.Namespace,
    result: object,
    *,
    probe_video: ProbeVideo,
) -> tuple[str, int, int]:
    status = getattr(result, "status", None)
    if status is not None:
        normalized_status = str(getattr(status, "value", status)).lower()
        if normalized_status != "succeeded":
            raise RuntimeError(f"generator returned non-success status: {normalized_status}")
    provider_task_id = str(getattr(result, "provider_task_id", "") or "").strip()
    if not provider_task_id:
        raise RuntimeError("generator returned no provider task ID")
    result_path = Path(str(getattr(result, "output_path", "")))
    if result_path != args.output:
        raise RuntimeError("generator returned an unexpected output path")
    if result_path.is_symlink() or not result_path.is_file():
        raise RuntimeError("generated output artifact is missing or unsafe")
    digest = hashlib.sha256()
    size = 0
    with result_path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    if size == 0:
        raise RuntimeError("generated output artifact is empty")
    probe = await probe_video(result_path)
    expected = resolve_h3_size_setting(args.resolution, args.aspect_ratio)
    actual_size = (int(probe.width), int(probe.height))
    expected_size = (expected.width, expected.height)
    if actual_size != expected_size:
        raise RuntimeError(
            f"actual video size {actual_size[0]}x{actual_size[1]} does not match "
            f"requested {expected_size[0]}x{expected_size[1]}"
        )
    return digest.hexdigest(), *actual_size


async def _run(
    args: argparse.Namespace,
    prepared: _PreparedInputs,
    *,
    generator: Generator | None = None,
    probe_video: ProbeVideo = _probe_video,
) -> None:
    if generator is None:
        from novelvideo.media_capabilities.video.h3_reference_runtime import (
            generate_h3_reference_director_video,
        )

        generator = generate_h3_reference_director_video

    input_digest = _input_digest(
        args, prepared.references, prepared.frozen_frames
    )
    print(f"workflow_id={WORKFLOW_ID}")
    print(f"input_sha256={input_digest}")
    print("runtime_cache=fresh")
    with tempfile.TemporaryDirectory(
        prefix=".h3-ref-smoke-", dir=args.output.parent
    ) as runtime_dir:
        result = await generator(
            SimpleNamespace(runtime_dir=runtime_dir),
            segments=(prepared.segment,),
            output_path=str(args.output),
            aspect_ratio=args.aspect_ratio,
            resolution=args.resolution,
            mode="auto",
            global_references=prepared.references,
            reference_limit=args.reference_limit,
            workflow_id=WORKFLOW_ID,
            frozen_frames=prepared.frozen_frames,
        )
        output_sha256, width, height = await _verify_output(
            args, result, probe_video=probe_video
        )
    print(f"provider_task_id={result.provider_task_id}")
    print(f"output_sha256={output_sha256}")
    print(f"actual_size={width}x{height}")
    print(f"output_path={result.output_path}")
    print("status=succeeded")


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    generator: Generator | None = None,
    probe_video: ProbeVideo = _probe_video,
) -> int:
    args = _parser().parse_args(argv)
    active_environment = os.environ if environ is None else environ
    if active_environment.get(_REAL_SMOKE_ENV) != "1":
        print(
            "real RunningHub call is not authorized; set RUNNINGHUB_REAL_SMOKE=1 "
            "only after confirming the workflow, inputs, resolution, and cost",
            file=sys.stderr,
        )
        return 2
    errors = _validation_errors(args)
    if errors:
        print("invalid real smoke request: " + "; ".join(errors), file=sys.stderr)
        return 2
    try:
        prepared = _prepare_inputs(args)
        reservation = _reserve_output(args.output)
    except (OSError, ValueError) as exc:
        print(f"invalid real smoke request: {exc}", file=sys.stderr)
        return 2
    try:
        asyncio.run(
            _run(
                args,
                prepared,
                generator=generator,
                probe_video=probe_video,
            )
        )
    except Exception as exc:
        print(
            f"workflow_id={WORKFLOW_ID} status=failed no_retry=true error={exc}",
            file=sys.stderr,
        )
        return 1
    finally:
        _remove_unused_reservation(reservation)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
