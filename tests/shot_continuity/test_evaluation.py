import pytest
from pydantic import ValidationError

from novelvideo.media_capabilities.video.h3_timeline import (
    H3DirectorOutputManifest,
    H3DirectorSegment,
    H3GenerationAttemptEvidence,
    H3ObservedBoundary,
    H3TimelineEntry,
)
from novelvideo.shot_continuity import H3ContinuityEvaluation, evaluate_manifests


def _entry(
    segment_id: str,
    *,
    status: str = "completed",
    attempts: int = 1,
    planned: str | None = "door open",
    observed: str | None = "door open",
    violations: tuple[str, ...] = (),
    contracts: tuple[dict[str, object], ...] | None = None,
) -> H3TimelineEntry:
    segment = H3DirectorSegment(
        segment_id=segment_id,
        beat_number=1,
        prompt="continue the shot",
        duration_seconds=1,
        first_frame="first.png",
    )
    if contracts is None:
        contracts = (
            ({"boundary": {"planned_carry_out": planned}} if planned is not None else {}),
        )
    observed_boundary = None
    if observed is not None:
        observed_boundary = H3ObservedBoundary(
            value=observed,
            source_contract_revision=1,
            result_contract_revision=1,
            lock_violations=violations,
        )
    return H3TimelineEntry(
        segment=segment,
        start_frame=0,
        frame_count=39,
        status=status,
        attempts=tuple(
            H3GenerationAttemptEvidence(attempt=index, status="completed")
            for index in range(1, attempts + 1)
        ),
        continuity_contracts=contracts,
        observed_carry_out=observed_boundary,
    )


def _manifest(*entries: H3TimelineEntry) -> H3DirectorOutputManifest:
    return H3DirectorOutputManifest(entries=entries)


def test_evaluate_manifests_flattens_entries_and_reports_approved_metrics() -> None:
    first = _entry(
        "first",
        violations=("spatial", "prop"),
    )
    second = _entry(
        "second",
        attempts=2,
        observed="door closed",
    )

    result = evaluate_manifests((_manifest(first), _manifest(second)))

    assert result == H3ContinuityEvaluation(
        total_entries=2,
        accepted_shots=2,
        scored_entries=2,
        unscored_entries=0,
        first_pass_usable_rate=0.5,
        attempts_per_accepted_shot=1.5,
        boundary_match_rate=0.5,
        lock_violation_rates={
            "identity": 0.0,
            "spatial": 0.5,
            "prop": 0.5,
            "camera": 0.0,
            "lighting": 0.0,
        },
    )


def test_legacy_accepted_entry_without_evidence_is_unscored_not_a_match() -> None:
    legacy = _entry(
        "legacy",
        planned=None,
        observed=None,
        contracts=(),
    )

    result = evaluate_manifests((_manifest(legacy),))

    assert result.accepted_shots == 1
    assert result.scored_entries == 0
    assert result.unscored_entries == 1
    assert result.boundary_match_rate == 0.0
    assert all(rate == 0.0 for rate in result.lock_violation_rates.values())


def test_rejected_entry_remains_in_first_pass_denominator() -> None:
    accepted = _entry("accepted")
    rejected = _entry("rejected", status="quality_rejected")

    result = evaluate_manifests((_manifest(accepted), _manifest(rejected)))

    assert result.total_entries == 2
    assert result.accepted_shots == 1
    assert result.first_pass_usable_rate == 0.5
    assert result.attempts_per_accepted_shot == 1.0


def test_empty_manifest_iterable_is_zero_safe() -> None:
    result = evaluate_manifests(())

    assert result.total_entries == 0
    assert result.accepted_shots == 0
    assert result.scored_entries == 0
    assert result.unscored_entries == 0
    assert result.first_pass_usable_rate == 0.0
    assert result.attempts_per_accepted_shot == 0.0
    assert result.boundary_match_rate == 0.0
    assert all(rate == 0.0 for rate in result.lock_violation_rates.values())


def test_planned_boundary_uses_terminal_contract() -> None:
    entry = _entry(
        "revised",
        observed="terminal plan",
        contracts=(
            {"boundary": {"planned_carry_out": "stale plan"}},
            {"boundary": {"planned_carry_out": "terminal plan"}},
        ),
    )

    result = evaluate_manifests((_manifest(entry),))

    assert result.scored_entries == 1
    assert result.boundary_match_rate == 1.0


def test_blank_terminal_planned_boundary_is_unscored() -> None:
    entry = _entry(
        "blank-terminal",
        contracts=(
            {"boundary": {"planned_carry_out": "old plan"}},
            {"boundary": {"planned_carry_out": "  "}},
        ),
    )

    result = evaluate_manifests((_manifest(entry),))

    assert result.scored_entries == 0
    assert result.unscored_entries == 1


def test_evaluation_is_frozen_and_unknown_lock_violation_is_rejected() -> None:
    result = evaluate_manifests(())
    with pytest.raises(ValidationError):
        result.total_entries = 1

    with pytest.raises(ValidationError):
        _entry("unknown-lock", violations=("dialogue",))
