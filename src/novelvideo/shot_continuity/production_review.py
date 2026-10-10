"""Read-only H3 cinematography review orchestration and content-keyed QC cache."""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import re
import subprocess
import tempfile
from collections.abc import Iterator, Mapping, MutableMapping, Sequence
from fractions import Fraction
from pathlib import Path

from novelvideo.director_plan.models import ShotPlan
from novelvideo.media_capabilities.video.h3_reference_runtime import H3FrozenFrame
from novelvideo.media_capabilities.video.h3_timeline import H3DirectorSegment
from novelvideo.text_task_runtime.runtime import StructuredTextRuntime
from novelvideo.task_backend.cancel import TaskCancelled, TaskTimedOut, TaskLeaseLost
from .visual_review import MAX_FRAME_PIXELS, POLICY_VERSION, FrozenH3Facts, VisualReviewReport, review_cinematography


def quality_status(reviews: Sequence[Mapping], segment_ids: Sequence[str]) -> str:
    """Technical generation is separate from complete actual-video acceptance.

    Legacy/reference-only reviews cannot certify actual video. Latest observations
    replace only the same coverage key; old evidence remains available in the manifest.
    """
    actual = [row for row in reviews if row.get('phase') == 'generated']
    if not actual:
        return 'generated'
    expected = {('actual_segment', (value,)) for value in segment_ids}
    expected.update(('actual_boundary', pair) for pair in zip(segment_ids, segment_ids[1:]))
    # A failed whole-run attempt has no segment key. It invalidates prior
    # certification until a later attempt supplies complete coverage again.
    barrier = max((index for index, row in enumerate(actual)
        if not row.get('review_kind') or not row.get('source_segment_ids')), default=-1)
    actual = actual[barrier + 1:]
    latest = {(row.get('review_kind'), tuple(row.get('source_segment_ids') or ())): row for row in actual}
    for key in expected:
        row = latest.get(key, {})
        labels = {'start', 'middle', 'end'} if key[0] == 'actual_segment' else {
            f'{side}_{role}' for side in ('previous', 'next') for role in ('start', 'middle', 'end')}
        evidence = row.get('evidence') or []
        digests, assets = row.get('frame_digests') or {}, row.get('frame_assets') or {}
        if (not isinstance(evidence, (list, tuple))
            or any(not isinstance(item, Mapping) for item in evidence)
            or len(evidence) != len(labels)
            or {item.get('frame_label') for item in evidence} != labels
            or not isinstance(digests, Mapping) or set(digests) != labels
            or not isinstance(assets, Mapping) or set(assets) != labels
            or not re.fullmatch(r'[a-f0-9]{64}', str(row.get('review_id') or ''))
            or any(not re.fullmatch(r'[a-f0-9]{64}', str(digests[label]))
                   or not isinstance(assets[label], str)
                   or Path(assets[label]).stem != digests[label] for label in labels)):
            return 'needs_review'
        if (row.get('status') != 'passed' or row.get('policy_version') != POLICY_VERSION
            or not row.get('review_id') or not row.get('evidence') or row.get('issues')
            or any(item.get('face_status') not in {'assessed', 'not_visible'}
                   or not str(item.get('face_observation') or '').strip()
                   for item in row.get('evidence', []))):
            return 'needs_review'
    return 'accepted' if expected else 'needs_review'


class VisualReviewCache(MutableMapping[str, VisualReviewReport]):
    """One atomic JSON record per strict digest; corrupt records are cache misses."""
    def __init__(self, directory: str | Path):
        self.directory = Path(directory)

    def _path(self, key: str) -> Path:
        if not re.fullmatch(r"[a-f0-9]{64}", key):
            raise ValueError("Cache key must be a SHA-256 digest")
        return self.directory / f"{key}.json"

    def __getitem__(self, key: str) -> VisualReviewReport:
        path = self._path(key)
        try:
            if path.is_symlink() or path.stat().st_size > 1024 * 1024:
                raise ValueError("Invalid cache record")
            report = VisualReviewReport.model_validate_json(path.read_bytes())
            if report.status == "unavailable" or not report.evidence or (
                (report.status == "failed") != bool(report.issues)
            ):
                raise ValueError("Invalid cached verdict")
            return report
        except (OSError, ValueError) as error:
            raise KeyError(key) from error

    def __setitem__(self, key: str, value: VisualReviewReport) -> None:
        path = self._path(key)
        if value.status == "unavailable":
            return
        self.directory.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=self.directory, suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            try:
                stream.write(value.model_dump_json().encode())
                stream.flush()
                os.replace(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)

    def __delitem__(self, key: str) -> None:
        try:
            self._path(key).unlink()
        except FileNotFoundError as error:
            raise KeyError(key) from error

    def __iter__(self) -> Iterator[str]:
        return (path.stem for path in self.directory.glob("*.json")
                if re.fullmatch(r"[a-f0-9]{64}", path.stem))

    def __len__(self) -> int:
        return sum(1 for _ in self)


def _unavailable() -> list[VisualReviewReport]:
    return [VisualReviewReport(status="unavailable", technical_error="ProductionReviewUnavailable")]


def _shots(segment: H3DirectorSegment, shots_by_id: Mapping[str, ShotPlan]) -> list[ShotPlan]:
    shots = [shots_by_id[shot_id] for shot_id in segment.source_shot_ids]
    if not shots or any(shot.cinematography is None for shot in shots):
        raise ValueError("Source shot cinematography is required")
    return shots


def _context(shot: ShotPlan, role: str, shots: Sequence[ShotPlan] = ()) -> str:
    if isinstance(shot, FrozenH3Facts):
        return json.dumps({'role': role, 'provenance': shot.provenance,
            'timing_caveat': 'Use original H3 shot frame ranges as intended timing only, not observed cuts. '
                'All original shots are supplied. Missing spatial constraints remain unknown.'})
    return json.dumps({"role": role, "shot_id": shot.id,
        "visible_start_state": shot.visible_start_state,
        "visible_end_state": shot.visible_end_state, "camera_motion": shot.camera_motion,
        "intended_source_timeline": [item.model_dump(mode="json", include={
            "id", "duration_seconds", "visible_start_state", "visible_end_state",
            "camera_motion", "cinematography", "action"}) for item in shots],
        "timing_caveat": "Midpoint assignment is proportional expected planning, not an observed cut. "
            "For paired shots, consider both intended states; do not fail solely on this assumed cut timing."},
        ensure_ascii=False)


async def _review(rows, runtime: StructuredTextRuntime, cache_dir: str | Path, kind: str,
                  evidence_dir: str | Path | None = None):
    cache = VisualReviewCache(cache_dir)
    fingerprint = runtime.snapshot.model_dump_json()
    reports = []
    async def observe(*, source_segment_ids, review_kind, **kwargs):
        report = await review_cinematography(**kwargs, require_face_integrity=kind == "actual")
        # Keep original evidence after temporary extraction directories disappear.
        # Content addressing preserves prior reviews and never overwrites paid media.
        assets = {}
        for label, source in kwargs['frames'].items():
            content = Path(source).read_bytes()
            digest = hashlib.sha256(content).hexdigest()
            suffix = '.png' if content.startswith(b'\x89PNG') else '.jpg' if content.startswith(b'\xff\xd8') else '.webp'
            target = (Path(evidence_dir) if evidence_dir else Path(cache_dir) / 'frames') / f'{digest}{suffix}'
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                try:
                    with target.open('xb') as stream:
                        stream.write(content)
                except FileExistsError:
                    pass
            assets[label] = str(target)
        return report.model_copy(update={"frame_assets": assets,
            "source_segment_ids": source_segment_ids, "review_kind": review_kind})
    for segment, frames, shots in rows:
        # This is expected planning only; the context explicitly disclaims an observed cut.
        middle = shots[0] if isinstance(shots[0], FrozenH3Facts) or shots[0].duration_seconds > sum(s.duration_seconds for s in shots) / 2 else shots[-1]
        assignments = {"start": shots[0], "middle": middle, "end": shots[-1]}
        reports.append(await observe(frames=frames,
            source_segment_ids=(segment.segment_id,), review_kind=f'{kind}_segment',
            facts={label: (assignments[label] if isinstance(assignments[label], FrozenH3Facts)
                else assignments[label].cinematography) for label in frames},
            frame_context={label: _context(assignments[label], f"{kind} {label}", shots) for label in frames},
            run_structured=runtime.run_structured, reviewer_fingerprint=fingerprint, cache=cache))
    for previous, current in zip(rows, rows[1:]):
        _, before, previous_shots = previous
        _, after, next_shots = current
        if "end" not in before:
            continue  # I2VA supplies no reference end; generated boundaries are checked later.
        last = next_shots[0]
        boundary_frames, boundary_facts, boundary_context = {}, {}, {}
        for side, images, shots in (("previous", before, previous_shots),
                                    ("next", after, next_shots)):
            middle = shots[0] if isinstance(shots[0], FrozenH3Facts) or shots[0].duration_seconds > sum(s.duration_seconds for s in shots) / 2 else shots[-1]
            assignments = {"start": shots[0], "middle": middle, "end": shots[-1]}
            # Actual clips already have all three samples. Reference inputs retain
            # their two cut images; no invented temporal evidence or extra IO.
            roles = ("start", "middle", "end") if kind == "actual" else (
                ("end",) if side == "previous" else ("start",))
            for role in roles:
                label = f"{side}_{role}"
                boundary_frames[label] = images[role]
                boundary_facts[label] = (assignments[role] if isinstance(assignments[role], FrozenH3Facts)
                    else assignments[role].cinematography)
                boundary_context[label] = _context(assignments[role], f"{kind} {role}", shots)
        reports.append(await observe(
            source_segment_ids=(previous[0].segment_id, current[0].segment_id),
            review_kind=f'{kind}_boundary',
            frames=boundary_frames, facts=boundary_facts, frame_context=boundary_context,
            boundary_labels=("previous_end", "next_start"),
            mode="boundary", cut_intent=(
                'Only use explicitly recorded handoff or transition facts in original frozen H3 plans; '
                'otherwise cut intent is unknown and comparison unavailable.'
                if isinstance(last, FrozenH3Facts) else last.cinematography.transition_intent),
            run_structured=runtime.run_structured, reviewer_fingerprint=fingerprint, cache=cache))
    return reports


async def review_reference_inputs(
    segments: Sequence[H3DirectorSegment], shots_by_id: Mapping[str, ShotPlan],
    frozen_frames: Mapping[str, H3FrozenFrame], runtime: StructuredTextRuntime | None,
    cache_dir: str | Path,
    evidence_dir: str | Path | None = None,
) -> list[VisualReviewReport]:
    """Use frozen bytes; first frame is required, I2VA end/boundary checks are optional.

    Modes without a first reference frame cannot run this keyframe review.
    """
    if runtime is None or not segments:
        return _unavailable()
    try:
        with tempfile.TemporaryDirectory(prefix="h3-reference-qc-") as temporary:
            rows = []
            for index, segment in enumerate(segments):
                shots = _shots(segment, shots_by_id)
                frames = {}
                for role, source in (("start", segment.first_frame), ("end", segment.last_frame)):
                    if role == "end" and source is None:
                        continue
                    frame = frozen_frames[source]
                    path = Path(temporary) / f"{index}-{role}.image"
                    path.write_bytes(frame.content)
                    frames[role] = path
                rows.append((segment, frames, shots))
            return await _review(rows, runtime, cache_dir, "reference", evidence_dir)
    except (TaskCancelled, TaskTimedOut, TaskLeaseLost):
        raise
    except Exception:
        return _unavailable()


def _extract_frames(source: Path, directory: Path) -> dict[str, Path]:
    source = source.resolve(strict=True)
    probe = subprocess.run(["ffprobe", "-v", "error", "-protocol_whitelist", "file,pipe",
        "-select_streams", "v:0", "-show_entries", "stream=width,height,duration,avg_frame_rate:format=duration",
        "-of", "json", str(source)], shell=False, check=True, capture_output=True, timeout=20)
    data = json.loads(probe.stdout)
    stream = data["streams"][0]
    duration = float(stream.get("duration") or data["format"]["duration"])
    fps = float(Fraction(stream["avg_frame_rate"]))
    if not math.isfinite(duration) or not 0 < duration <= 3600 or not 0 < fps <= 240:
        raise ValueError("Invalid clip timeline")
    if not 0 < stream["width"] * stream["height"] <= MAX_FRAME_PIXELS:
        raise ValueError("Invalid clip dimensions")
    frames = {}
    for role, timestamp in (("start", 0), ("middle", duration / 2), ("end", max(0, duration - 1 / fps))):
        path = directory / f"{role}.png"
        subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-protocol_whitelist", "file,pipe",
            "-ss", str(timestamp), "-i", str(source), "-map", "0:v:0", "-frames:v", "1", str(path)],
            shell=False, check=True, capture_output=True, timeout=30)
        if not path.is_file():
            raise ValueError("Frame extraction produced no image")
        frames[role] = path
    return frames


async def review_generated_segments(
    segments_and_paths: Sequence[tuple[H3DirectorSegment, Path]], shots_by_id: Mapping[str, ShotPlan],
    runtime: StructuredTextRuntime | None, cache_dir: str | Path,
    evidence_dir: str | Path | None = None,
    *, frozen_h3_plans: Mapping[str, dict] | None = None,
) -> list[VisualReviewReport]:
    """Sample local videos at start/middle/end, then review adjacent cut boundaries."""
    if runtime is None or not segments_and_paths:
        return _unavailable()
    try:
        with tempfile.TemporaryDirectory(prefix="h3-generated-qc-") as temporary:
            rows = []
            for index, (segment, source) in enumerate(segments_and_paths):
                if frozen_h3_plans is not None:
                    from novelvideo.media_capabilities.video.h3_director_plan import H3DirectorPlan
                    original = frozen_h3_plans[segment.segment_id]
                    # Validate the retained plan, but pass its exact original facts.
                    H3DirectorPlan.model_validate(original)
                    shots = [FrozenH3Facts(director_plan=original)]
                else:
                    shots = _shots(segment, shots_by_id)
                directory = Path(temporary) / str(index)
                directory.mkdir()
                frames = await asyncio.to_thread(_extract_frames, Path(source), directory)
                rows.append((segment, frames, shots))
            return await _review(rows, runtime, cache_dir, "actual", evidence_dir)
    except (TaskCancelled, TaskTimedOut, TaskLeaseLost):
        raise
    except Exception:
        return _unavailable()
