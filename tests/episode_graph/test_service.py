from __future__ import annotations

import asyncio
from collections.abc import Sequence

import pytest

from novelvideo.episode_graph.checkpoints import EpisodeGraphCheckpointStore
from novelvideo.episode_graph.grouping import group_episode_sources
from novelvideo.episode_graph.models import EpisodeGraphExtraction, EpisodeGraphSource
from novelvideo.episode_graph.service import (
    EpisodeGraphBuildService,
    EpisodeGraphGroupsFailed,
)


def sources() -> list[EpisodeGraphSource]:
    return [
        EpisodeGraphSource(number=n, title=f"E{n}", content=f"正文{n}", source_revision=2)
        for n in range(2, 31)
    ]


class Candidate:
    def __init__(self) -> None:
        self.writes = 0

    async def remove_episode_contributions(self, episodes: set[int]) -> None:
        self.writes += 1

    async def upsert_entities(self, entities: Sequence[object]) -> None:
        self.writes += 1

    async def upsert_events(self, events: Sequence[object]) -> None:
        self.writes += 1

    async def upsert_relations(self, relations: Sequence[object]) -> None:
        self.writes += 1

    async def index_embeddings(self, *, batch_size: int = 64) -> None:
        self.writes += 1


class Candidates:
    def __init__(self) -> None:
        self.created = 0
        self.discarded = 0

    async def create_candidate(self, target_revision: int) -> Candidate:
        self.created += 1
        return Candidate()

    async def discard_candidate(self, candidate: Candidate) -> None:
        self.discarded += 1


@pytest.mark.asyncio
async def test_partial_failure_checkpoints_success_and_retry_calls_only_failed_group(tmp_path):
    calls: list[str] = []
    fail = {"e7-e11"}

    async def extract(groups, **kwargs):
        results = []
        for group in groups:
            calls.append(group.key)
            results.append(RuntimeError("boom") if group.key in fail else EpisodeGraphExtraction(group_key=group.key))
        return results

    candidates = Candidates()
    service = EpisodeGraphBuildService(
        checkpoints=EpisodeGraphCheckpointStore(tmp_path),
        candidates=candidates,
        extractor=extract,
    )
    with pytest.raises(EpisodeGraphGroupsFailed) as error:
        await service.build(sources(), target_revision=2)
    assert error.value.failed_group_keys == ("e7-e11",)
    assert "e7-e11: RuntimeError: boom" in str(error.value)
    assert calls == [group.key for group in group_episode_sources(sources())]
    assert candidates.created == 0

    calls.clear()
    fail.clear()
    result = await service.build(sources(), target_revision=2)
    assert calls == ["e7-e11"]
    assert result.group_count == 6
    assert result.candidate.writes == 5
    assert candidates.created == 1


@pytest.mark.asyncio
async def test_candidate_is_discarded_when_write_fails(tmp_path):
    class BrokenCandidate(Candidate):
        async def index_embeddings(self, *, batch_size: int = 64) -> None:
            raise RuntimeError("embedding failed")

    class BrokenCandidates(Candidates):
        async def create_candidate(self, target_revision: int) -> BrokenCandidate:
            self.created += 1
            return BrokenCandidate()

    async def extract(groups, **kwargs):
        return [EpisodeGraphExtraction(group_key=group.key) for group in groups]

    candidates = BrokenCandidates()
    service = EpisodeGraphBuildService(
        checkpoints=EpisodeGraphCheckpointStore(tmp_path), candidates=candidates, extractor=extract
    )
    with pytest.raises(RuntimeError, match="embedding failed"):
        await service.build(sources()[:1], target_revision=2)
    assert candidates.discarded == 1


@pytest.mark.asyncio
async def test_service_forwards_concurrency_six_and_group_events(tmp_path):
    active = 0
    peak = 0
    events: list[tuple[str, str]] = []

    async def extract(groups, **kwargs):
        nonlocal active, peak
        assert len(groups) == 1
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        return [EpisodeGraphExtraction(group_key=group.key) for group in groups]

    await EpisodeGraphBuildService(
        checkpoints=EpisodeGraphCheckpointStore(tmp_path), candidates=Candidates(), extractor=extract
    ).build(
        sources(),
        target_revision=2,
        on_group_event=lambda event, group: events.append((event, group.key)),
    )
    assert peak == 6


@pytest.mark.asyncio
async def test_completed_group_is_checkpointed_before_another_group_is_cancelled(tmp_path):
    source_items = sources()[:6]
    groups = group_episode_sources(source_items)
    assert len(groups) == 2
    first_done = asyncio.Event()
    release_second = asyncio.Event()

    async def extract(items, **kwargs):
        group = items[0]
        if group.key == groups[0].key:
            first_done.set()
            return [EpisodeGraphExtraction(group_key=group.key)]
        await release_second.wait()
        return [EpisodeGraphExtraction(group_key=group.key)]

    checkpoints = EpisodeGraphCheckpointStore(tmp_path)
    task = asyncio.create_task(
        EpisodeGraphBuildService(
            checkpoints=checkpoints, candidates=Candidates(), extractor=extract
        ).build(source_items, target_revision=2)
    )
    await first_done.wait()
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert checkpoints.load_success(
        2, groups[0].key, groups[0].content_hash
    ) == EpisodeGraphExtraction(group_key=groups[0].key).model_dump(mode="json")
    assert checkpoints.load_success(2, groups[1].key, groups[1].content_hash) is None
