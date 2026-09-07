"""Offline aggregate evaluation for explicit H3 director manifests."""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from pydantic import BaseModel, ConfigDict

from novelvideo.media_capabilities.video.h3_timeline import (
    H3DirectorOutputManifest,
    H3TimelineEntry,
)

LOCK_KINDS = ("identity", "spatial", "prop", "camera", "lighting")


class H3ContinuityEvaluation(BaseModel):
    """Aggregate continuity metrics for a caller-selected manifest cohort."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    total_entries: int
    accepted_shots: int
    scored_entries: int
    unscored_entries: int
    first_pass_usable_rate: float
    attempts_per_accepted_shot: float
    boundary_match_rate: float
    lock_violation_rates: dict[str, float]


def _planned_carry_out(entry: H3TimelineEntry) -> str | None:
    if not entry.continuity_contracts:
        return None
    terminal_contract = entry.continuity_contracts[-1]
    boundary = terminal_contract.get("boundary")
    if not isinstance(boundary, Mapping):
        return None
    planned = boundary.get("planned_carry_out")
    if not isinstance(planned, str) or not planned.strip():
        return None
    return planned.strip()


def evaluate_manifests(
    manifests: Iterable[H3DirectorOutputManifest],
) -> H3ContinuityEvaluation:
    """Evaluate only the manifests explicitly supplied by the caller."""

    entries = [entry for manifest in manifests for entry in manifest.entries]
    accepted = [entry for entry in entries if entry.status == "completed"]
    scored = [
        (entry, planned)
        for entry in accepted
        if (planned := _planned_carry_out(entry)) is not None
        and entry.observed_carry_out is not None
    ]

    total_entries = len(entries)
    accepted_shots = len(accepted)
    scored_entries = len(scored)
    first_pass_shots = sum(len(entry.attempts) == 1 for entry in accepted)
    total_attempts = sum(len(entry.attempts) for entry in accepted)
    boundary_matches = sum(
        entry.observed_carry_out is not None
        and entry.observed_carry_out.value == planned
        for entry, planned in scored
    )
    violation_counts = {
        kind: sum(
            entry.observed_carry_out is not None
            and kind in entry.observed_carry_out.lock_violations
            for entry, _ in scored
        )
        for kind in LOCK_KINDS
    }

    return H3ContinuityEvaluation(
        total_entries=total_entries,
        accepted_shots=accepted_shots,
        scored_entries=scored_entries,
        unscored_entries=total_entries - scored_entries,
        first_pass_usable_rate=(first_pass_shots / total_entries if total_entries else 0.0),
        attempts_per_accepted_shot=(
            total_attempts / accepted_shots if accepted_shots else 0.0
        ),
        boundary_match_rate=(
            boundary_matches / scored_entries if scored_entries else 0.0
        ),
        lock_violation_rates={
            kind: count / scored_entries if scored_entries else 0.0
            for kind, count in violation_counts.items()
        },
    )


__all__ = ["H3ContinuityEvaluation", "LOCK_KINDS", "evaluate_manifests"]
