"""Greedy, order-preserving batching of H3 segments under the attempt limit.

A single segment that cannot fit the limit is a planner bug, so it surfaces as
batch_over_limit instead of being silently split or dropped.
"""

from __future__ import annotations


def batch_segments(segments: list[dict], *, limit_seconds: float = 15.0) -> list[list[dict]]:
    """Group consecutive segments so each batch sums to at most limit_seconds."""
    batches: list[list[dict]] = []
    current: list[dict] = []
    total = 0.0
    for segment in segments:
        duration = float(segment["duration_seconds"])
        if current and total + duration > limit_seconds:
            batches.append(current)
            current, total = [], 0.0
        current.append(segment)
        total += duration
    if current:
        batches.append(current)

    for batch in batches:
        if sum(float(item["duration_seconds"]) for item in batch) > limit_seconds:
            raise ValueError("batch_over_limit")
    return batches


__all__ = ["batch_segments"]
