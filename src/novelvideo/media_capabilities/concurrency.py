"""Fair, capability-aware concurrency coordination for media providers."""

from __future__ import annotations

import asyncio
from collections import Counter
from collections.abc import AsyncIterator, Mapping
from concurrent.futures import Future
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from threading import RLock
from types import MappingProxyType

from novelvideo.media_capabilities.models import MediaCapability


DEFAULT_QUEUE_LIMIT = 100


class ConcurrencyConfigurationError(ValueError):
    """Raised when provider concurrency configuration is missing or invalid."""


class ConcurrencyQueueFull(RuntimeError):
    """Raised when a provider's bounded concurrency queue is full."""


@dataclass(frozen=True, slots=True)
class _ProviderConfig:
    max_concurrency: int
    capability_limits: Mapping[str, int]
    queue_limit: int


@dataclass(slots=True)
class _Waiter:
    capability: str
    future: Future[ConcurrencyLease]


@dataclass(slots=True)
class _ProviderState:
    config: _ProviderConfig
    active_by_capability: Counter[str] = field(default_factory=Counter)
    waiters: list[_Waiter] = field(default_factory=list)


class ConcurrencyLease:
    """One idempotently releasable provider concurrency grant."""

    __slots__ = ("_capability", "_coordinator", "_provider_id", "_released")

    def __init__(
        self,
        coordinator: ProviderConcurrencyCoordinator,
        provider_id: str,
        capability: str,
    ) -> None:
        self._coordinator = coordinator
        self._provider_id = provider_id
        self._capability = capability
        self._released = False

    @property
    def provider_id(self) -> str:
        return self._provider_id

    @property
    def capability(self) -> str:
        return self._capability

    @property
    def released(self) -> bool:
        return self._released

    async def release(self) -> None:
        """Release this grant; repeated calls have no effect."""
        self._coordinator._release(self)

    async def __aenter__(self) -> ConcurrencyLease:
        return self

    async def __aexit__(
        self,
        exc_type: object,
        exc: BaseException | None,
        traceback: object,
    ) -> None:
        await self.release()


class ProviderConcurrencyCoordinator:
    """Coordinate provider-wide and overlapping capability concurrency limits."""

    def __init__(self) -> None:
        self._providers: dict[str, _ProviderState] = {}
        # State transitions never await. A process-local lock protects provider
        # counts and queues shared by tasks running in different threads/loops.
        self._lock = RLock()

    def configure(
        self,
        provider_id: str,
        max_concurrency: int,
        capability_limits: Mapping[str | MediaCapability, int] | None = None,
        queue_limit: int = DEFAULT_QUEUE_LIMIT,
    ) -> None:
        """Add or replace one provider's limits without revoking active leases."""
        normalized_provider = self._normalize_provider_id(provider_id)
        self._require_positive("max_concurrency", max_concurrency)
        self._require_positive("queue_limit", queue_limit)

        normalized_limits: dict[str, int] = {}
        for pattern, limit in (capability_limits or {}).items():
            normalized_pattern = self._normalize_pattern(pattern)
            self._require_positive(
                f"capability limit for {normalized_pattern!r}", limit
            )
            normalized_limits[normalized_pattern] = limit

        config = _ProviderConfig(
            max_concurrency=max_concurrency,
            capability_limits=MappingProxyType(normalized_limits),
            queue_limit=queue_limit,
        )
        with self._lock:
            state = self._providers.get(normalized_provider)
            if state is None:
                self._providers[normalized_provider] = _ProviderState(config=config)
                return

            state.config = config
            self._drain(normalized_provider, state)

    async def acquire(
        self,
        provider_id: str,
        capability: str | MediaCapability,
    ) -> ConcurrencyLease:
        """Wait for and return a grant satisfying every matching limit."""
        normalized_provider = self._normalize_provider_id(provider_id)
        normalized_capability = self._normalize_capability(capability)
        with self._lock:
            state = self._state(normalized_provider)
            self._drain(normalized_provider, state)
            if self._can_grant(state, normalized_capability):
                return self._grant(state, normalized_provider, normalized_capability)

            if len(state.waiters) >= state.config.queue_limit:
                raise ConcurrencyQueueFull(
                    f"concurrency queue for provider {normalized_provider!r} is full"
                )

            future: Future[ConcurrencyLease] = Future()
            waiter = _Waiter(normalized_capability, future)
            state.waiters.append(waiter)
            self._drain(normalized_provider, state)

        wrapped = asyncio.wrap_future(future)
        try:
            return await asyncio.shield(wrapped)
        except asyncio.CancelledError:
            with self._lock:
                if future.done() and not future.cancelled():
                    self._release(future.result())
                else:
                    self._remove_waiter(state, waiter)
                    future.cancel()
                    self._drain(normalized_provider, state)
            raise

    @asynccontextmanager
    async def lease(
        self,
        provider_id: str,
        capability: str | MediaCapability,
    ) -> AsyncIterator[ConcurrencyLease]:
        """Acquire a lease and release it on normal exit, error, or cancellation."""
        acquired = await self.acquire(provider_id, capability)
        try:
            yield acquired
        finally:
            await acquired.release()

    def snapshot(self, provider_id: str) -> Mapping[str, object]:
        """Return an immutable observation of current active and waiting work."""
        normalized_provider = self._normalize_provider_id(provider_id)
        with self._lock:
            state = self._state(normalized_provider)
            active_by_rule = {
                pattern: sum(
                    count
                    for capability, count in state.active_by_capability.items()
                    if self._matches(pattern, capability)
                )
                for pattern in state.config.capability_limits
            }
            return MappingProxyType(
                {
                    "active_total": sum(state.active_by_capability.values()),
                    "active_by_rule": MappingProxyType(active_by_rule),
                    "waiting": sum(
                        not waiter.future.done() for waiter in state.waiters
                    ),
                }
            )

    def _state(self, provider_id: str) -> _ProviderState:
        try:
            return self._providers[provider_id]
        except KeyError as exc:
            raise ConcurrencyConfigurationError(
                f"provider {provider_id!r} has not been configured"
            ) from exc

    @staticmethod
    def _normalize_provider_id(provider_id: str) -> str:
        if not isinstance(provider_id, str) or not provider_id.strip():
            raise ConcurrencyConfigurationError("provider_id must be a non-empty string")
        return provider_id.strip()

    @staticmethod
    def _require_positive(name: str, value: int) -> None:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ConcurrencyConfigurationError(f"{name} must be a positive integer")

    @staticmethod
    def _normalize_capability(capability: str | MediaCapability) -> str:
        try:
            return MediaCapability(capability).value
        except (TypeError, ValueError) as exc:
            raise ConcurrencyConfigurationError(
                f"unknown media capability: {capability!r}"
            ) from exc

    @classmethod
    def _normalize_pattern(cls, pattern: str | MediaCapability) -> str:
        if isinstance(pattern, MediaCapability):
            return pattern.value
        if not isinstance(pattern, str):
            raise ConcurrencyConfigurationError(
                f"invalid capability pattern: {pattern!r}"
            )
        try:
            return MediaCapability(pattern).value
        except ValueError:
            pass

        if (
            pattern.endswith(".*")
            and pattern.count("*") == 1
            and any(
                capability.value.startswith(pattern[:-1])
                for capability in MediaCapability
            )
        ):
            return pattern
        raise ConcurrencyConfigurationError(
            f"invalid capability pattern: {pattern!r}"
        )

    @staticmethod
    def _matches(pattern: str, capability: str) -> bool:
        if pattern.endswith(".*"):
            return capability.startswith(pattern[:-1])
        return capability == pattern

    def _can_grant(self, state: _ProviderState, capability: str) -> bool:
        if sum(state.active_by_capability.values()) >= state.config.max_concurrency:
            return False
        for pattern, limit in state.config.capability_limits.items():
            if not self._matches(pattern, capability):
                continue
            active = sum(
                count
                for active_capability, count in state.active_by_capability.items()
                if self._matches(pattern, active_capability)
            )
            if active >= limit:
                return False
        return True

    def _grant(
        self,
        state: _ProviderState,
        provider_id: str,
        capability: str,
    ) -> ConcurrencyLease:
        state.active_by_capability[capability] += 1
        return ConcurrencyLease(self, provider_id, capability)

    def _drain(self, provider_id: str, state: _ProviderState) -> None:
        state.waiters[:] = [
            waiter for waiter in state.waiters if not waiter.future.done()
        ]
        while state.waiters:
            grant_index = next(
                (
                    index
                    for index, waiter in enumerate(state.waiters)
                    if self._can_grant(state, waiter.capability)
                ),
                None,
            )
            if grant_index is None:
                return
            waiter = state.waiters.pop(grant_index)
            lease = self._grant(state, provider_id, waiter.capability)
            waiter.future.set_result(lease)

    def _release(self, lease: ConcurrencyLease) -> None:
        with self._lock:
            state = self._state(lease._provider_id)
            if lease._released:
                return
            lease._released = True
            state.active_by_capability[lease._capability] -= 1
            if state.active_by_capability[lease._capability] <= 0:
                del state.active_by_capability[lease._capability]
            self._drain(lease._provider_id, state)

    @staticmethod
    def _remove_waiter(state: _ProviderState, target: _Waiter) -> None:
        state.waiters[:] = [waiter for waiter in state.waiters if waiter is not target]


__all__ = [
    "ConcurrencyConfigurationError",
    "ConcurrencyLease",
    "ConcurrencyQueueFull",
    "ProviderConcurrencyCoordinator",
]
