from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

from .checkpoints import EpisodeGraphCheckpointStore
from .extractor import GroupEvent, extract_groups
from .grouping import group_episode_sources
from .merge import merge_extractions
from .models import EpisodeGraphExtraction, EpisodeGraphSource, MergedEpisodeGraph
from .writer import EpisodeGraphWriter, GraphWriteBackend

Extractor = Callable[..., Awaitable[list[EpisodeGraphExtraction | BaseException]]]


class CandidateManager(Protocol):
    async def create_candidate(self, target_revision: int) -> GraphWriteBackend: ...

    async def discard_candidate(self, candidate: GraphWriteBackend) -> None: ...


class EpisodeGraphGroupsFailed(RuntimeError):
    def __init__(self, failures: dict[str, BaseException]) -> None:
        self.failures = dict(failures)
        self.failed_group_keys = tuple(sorted(failures))
        details = "; ".join(self._failure_detail(key) for key in self.failed_group_keys)
        super().__init__("episode graph groups failed: " + details)

    def _failure_detail(self, key: str) -> str:
        failure = self.failures[key]
        message = str(failure)
        if len(message) > 800:
            message = "..." + message[-800:]
        return f"{key}: {type(failure).__name__}: {message}"


@dataclass(frozen=True)
class EpisodeGraphBuildResult:
    target_revision: int
    group_count: int
    graph: MergedEpisodeGraph
    candidate: GraphWriteBackend


class EpisodeGraphBuildService:
    """Builds a complete candidate while leaving pointer activation to the caller."""

    def __init__(
        self,
        *,
        checkpoints: EpisodeGraphCheckpointStore,
        candidates: CandidateManager,
        extractor: Extractor = extract_groups,
    ) -> None:
        self._checkpoints = checkpoints
        self._candidates = candidates
        self._extractor = extractor

    async def build(
        self,
        sources: Sequence[EpisodeGraphSource],
        *,
        target_revision: int,
        on_group_event: GroupEvent | None = None,
    ) -> EpisodeGraphBuildResult:
        if target_revision < 1:
            raise ValueError("target_revision must be positive")
        if any(source.source_revision != target_revision for source in sources):
            raise ValueError("episode source revision does not match target revision")

        groups = group_episode_sources(sources)
        completed: dict[str, EpisodeGraphExtraction] = {}
        missing = []
        for group in groups:
            payload = self._checkpoints.load_success(
                target_revision, group.key, group.content_hash
            )
            if payload is None:
                missing.append(group)
                continue
            try:
                completed[group.key] = EpisodeGraphExtraction.model_validate(payload)
            except ValueError:
                missing.append(group)
                continue
            if on_group_event:
                on_group_event("checkpoint", group)

        failures: dict[str, BaseException] = {}
        if missing:
            semaphore = asyncio.Semaphore(6)

            async def extract_one(group):
                async with semaphore:
                    results = await self._extractor(
                        [group], concurrency=1, on_group_event=on_group_event
                    )
                if len(results) != 1:
                    raise RuntimeError("extractor must return one result per group")
                result = results[0]
                if isinstance(result, BaseException):
                    return group, result
                if result.group_key != group.key:
                    return group, ValueError(
                        f"extraction group key mismatch for {group.key}"
                    )
                completed[group.key] = result
                self._checkpoints.save_success(
                    target_revision=target_revision,
                    group_key=group.key,
                    content_hash=group.content_hash,
                    result=result.model_dump(mode="json"),
                )
                return group, result

            tasks = [asyncio.create_task(extract_one(group)) for group in missing]
            try:
                for future in asyncio.as_completed(tasks):
                    group, result = await future
                    if isinstance(result, BaseException):
                        failures[group.key] = result
                        if on_group_event:
                            on_group_event("failed", group)
                        continue
            finally:
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)

        if failures:
            raise EpisodeGraphGroupsFailed(failures)

        graph = merge_extractions([completed[group.key] for group in groups])
        candidate = await self._candidates.create_candidate(target_revision)
        try:
            await EpisodeGraphWriter(candidate).replace_sources(
                {source.number for source in sources}, graph
            )
        except BaseException:
            await self._candidates.discard_candidate(candidate)
            raise
        return EpisodeGraphBuildResult(
            target_revision=target_revision,
            group_count=len(groups),
            graph=graph,
            candidate=candidate,
        )
