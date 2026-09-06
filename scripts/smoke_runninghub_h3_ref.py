"""Explicitly authorized compatibility smoke for MiniMax H3 Director Ref."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
from collections.abc import Awaitable, Callable, Mapping, Sequence
from pathlib import Path
from types import SimpleNamespace

from novelvideo.media_capabilities.video.h3_reference_runtime import (
    H3FrozenFrame,
    freeze_h3_reference_frames,
)
from novelvideo.media_capabilities.video.h3_timeline import H3DirectorSegment
from novelvideo.narrative_groups.video_references import ResolvedVideoReference


WORKFLOW_ID = "2096502793044582401"
_REAL_SMOKE_ENV = "RUNNINGHUB_REAL_SMOKE"
_DEFAULT_REFERENCE_LIMIT = 5

Generator = Callable[..., Awaitable[object]]


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
    parser.add_argument("--output", type=Path, default=Path("runninghub-h3-ref-smoke.mp4"))
    parser.add_argument("--runtime-dir", type=Path, default=Path(".runtime"))
    return parser


def _validation_errors(args: argparse.Namespace) -> list[str]:
    errors: list[str] = []
    if not args.reference:
        errors.append("at least one --reference is required")
    if args.first_frame is None:
        errors.append("--first-frame is required")
    if args.resolution is None:
        errors.append("--resolution is required")
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


def _resolved_references(args: argparse.Namespace) -> tuple[ResolvedVideoReference, ...]:
    references = []
    for index, path in enumerate(args.reference, start=1):
        content = path.read_bytes()
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


async def _run(args: argparse.Namespace, *, generator: Generator | None = None) -> None:
    if generator is None:
        from novelvideo.media_capabilities.video.h3_reference_runtime import (
            generate_h3_reference_director_video,
        )

        generator = generate_h3_reference_director_video

    references = _resolved_references(args)
    segment = H3DirectorSegment(
        segment_id="h3-ref-real-smoke",
        beat_number=1,
        prompt=args.prompt,
        duration_seconds=args.duration,
        first_frame=str(args.first_frame),
        last_frame=(str(args.last_frame) if args.last_frame else None),
    )
    frozen_frames = freeze_h3_reference_frames((segment,))
    input_digest = _input_digest(args, references, frozen_frames)
    print(f"workflow_id={WORKFLOW_ID}")
    print(f"input_sha256={input_digest}")
    result = await generator(
        SimpleNamespace(runtime_dir=args.runtime_dir),
        segments=(segment,),
        output_path=str(args.output),
        aspect_ratio=args.aspect_ratio,
        resolution=args.resolution,
        mode="auto",
        global_references=references,
        reference_limit=args.reference_limit,
        workflow_id=WORKFLOW_ID,
        frozen_frames=frozen_frames,
    )
    print(f"provider_task_id={result.provider_task_id}")
    print("status=succeeded")
    print(f"output_path={result.output_path}")


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    generator: Generator | None = None,
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
        asyncio.run(_run(args, generator=generator))
    except Exception as exc:
        print(
            f"workflow_id={WORKFLOW_ID} status=failed no_retry=true error={exc}",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
