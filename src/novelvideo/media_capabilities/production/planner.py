"""Deterministic previews and content-hash invalidation for production DAGs."""

from __future__ import annotations

import hashlib
import heapq
import json
import math
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, JsonValue


class ProductionPlannerError(ValueError):
    """Base class for invalid production plans."""


class UnknownProductionNodeType(ProductionPlannerError):
    """Raised when a plan requests a node type outside the registry."""


class ProductionCycleError(ProductionPlannerError):
    """Raised when production dependencies contain a cycle."""


class ProductionPlanAction(StrEnum):
    CREATE = "create"
    REUSE = "reuse"
    INVALIDATE = "invalidate"
    SKIP = "skip"


class AuditPolicy(StrEnum):
    STRICT = "strict"
    BALANCED = "balanced"
    AUTO = "auto"


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ProductionNodeType(_FrozenModel):
    """Registered execution identity and pricing for one permitted node type."""

    capability: str = Field(min_length=1)
    implementation: str = Field(min_length=1)
    workflow_version: JsonValue
    unit_cost: float = Field(default=0.0, ge=0, allow_inf_nan=False)


class ProductionPlanNode(_FrozenModel):
    node_id: str
    node_type: str
    action: ProductionPlanAction
    fingerprint: str
    quantity: int = Field(ge=1)
    estimated_cost: float = Field(ge=0, allow_inf_nan=False)
    reason: str | None = None
    upstream_hashes: dict[str, str] = Field(default_factory=dict)


class ProductionPlanPreview(_FrozenModel):
    audit_policy: AuditPolicy
    nodes: tuple[ProductionPlanNode, ...]
    counts: dict[str, int]
    estimated_cost: float = Field(ge=0, allow_inf_nan=False)


def _non_empty(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProductionPlannerError(f"{field} must be a non-empty string")
    return value.strip()


def _canonical(value: object) -> JsonValue:
    """Return JSON data with deterministic mapping order and finite numbers."""

    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ProductionPlannerError("fingerprint values must be finite JSON")
        return value
    if isinstance(value, Mapping):
        normalized: dict[str, JsonValue] = {}
        for key in sorted(value, key=lambda item: str(item)):
            if not isinstance(key, str):
                raise ProductionPlannerError("fingerprint mapping keys must be strings")
            normalized[key] = _canonical(value[key])
        return normalized
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_canonical(item) for item in value]
    if isinstance(value, StrEnum):
        return value.value
    raise ProductionPlannerError(
        f"fingerprint value {type(value).__name__} is not JSON-compatible"
    )


def _fingerprint(value: object) -> str:
    payload = json.dumps(
        _canonical(value),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _mapping(value: object, field: str) -> Mapping[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ProductionPlannerError(f"{field} must be a mapping")
    return value


class ProductionPlanner:
    """Preview a registered production DAG without mutating persisted state."""

    def __init__(
        self,
        registered_node_types: Mapping[str, ProductionNodeType | Mapping[str, Any]],
    ) -> None:
        if not registered_node_types:
            raise ProductionPlannerError("at least one production node type is required")
        self._node_types = {
            _non_empty(name, "node type"): (
                spec
                if isinstance(spec, ProductionNodeType)
                else ProductionNodeType.model_validate(spec)
            )
            for name, spec in registered_node_types.items()
        }

    def preview(
        self,
        scope: Mapping[str, Any],
        snapshot: Mapping[str, Any],
        existing_artifacts: Mapping[str, Any] | Iterable[Mapping[str, Any]],
    ) -> ProductionPlanPreview:
        scope = _mapping(scope, "scope")
        snapshot = _mapping(snapshot, "snapshot")
        policy = AuditPolicy(scope.get("audit_policy", snapshot.get("audit_policy", "balanced")))
        raw_nodes = scope.get("nodes", ())
        if not isinstance(raw_nodes, Sequence) or isinstance(raw_nodes, (str, bytes)):
            raise ProductionPlannerError("scope.nodes must be a sequence")

        nodes, order = self._read_nodes(raw_nodes)
        dependencies = self._read_dependencies(nodes, order, scope.get("edges", ()))
        topological_ids = self._stable_topological_order(order, dependencies)
        artifacts = self._artifact_index(existing_artifacts)
        overrides = self._snapshot_overrides(snapshot)

        planned: list[ProductionPlanNode] = []
        output_hashes: dict[str, str] = {}
        actions: dict[str, ProductionPlanAction] = {}
        counts: Counter[str] = Counter(action.value for action in ())
        total_cost = 0.0

        for node_id in topological_ids:
            node = nodes[node_id]
            node_type = _non_empty(
                node.get("node_type", node.get("type")), f"node {node_id} node_type"
            )
            try:
                registration = self._node_types[node_type]
            except KeyError as exc:
                raise UnknownProductionNodeType(
                    f"unregistered production node type: {node_type}"
                ) from exc

            quantity = node.get("quantity", 1)
            if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 1:
                raise ProductionPlannerError(f"node {node_id} quantity must be positive")
            upstream_hashes = {
                upstream: output_hashes[upstream]
                for upstream in dependencies[node_id]
            }
            override = overrides.get(node_id, {})
            if not isinstance(override, Mapping):
                raise ProductionPlannerError(
                    f"snapshot override for {node_id} must be a mapping"
                )
            identity = {
                "capability": override.get(
                    "capability", node.get("capability", registration.capability)
                ),
                "implementation": override.get(
                    "implementation",
                    node.get("implementation", registration.implementation),
                ),
                "workflow_version": override.get(
                    "workflow_version",
                    node.get("workflow_version", registration.workflow_version),
                ),
                "normalized_inputs": override.get(
                    "inputs", node.get("inputs", {})
                ),
                "params": override.get("params", node.get("params", {})),
                "upstream_hashes": upstream_hashes,
            }
            fingerprint = _fingerprint(identity)
            artifact = artifacts.get(node_id)
            action, reason = self._action_for(
                node=node,
                artifact=artifact,
                fingerprint=fingerprint,
                policy=policy,
                upstream_actions={key: actions[key] for key in dependencies[node_id]},
            )
            cost = (
                registration.unit_cost * quantity
                if action in {ProductionPlanAction.CREATE, ProductionPlanAction.INVALIDATE}
                else 0.0
            )
            planned.append(
                ProductionPlanNode(
                    node_id=node_id,
                    node_type=node_type,
                    action=action,
                    fingerprint=fingerprint,
                    quantity=quantity,
                    estimated_cost=cost,
                    reason=reason,
                    upstream_hashes=upstream_hashes,
                )
            )
            counts[action.value] += quantity
            total_cost += cost
            actions[node_id] = action
            output_hashes[node_id] = self._output_hash(artifact, fingerprint, action)

        return ProductionPlanPreview(
            audit_policy=policy,
            nodes=tuple(planned),
            counts={action.value: counts[action.value] for action in ProductionPlanAction},
            estimated_cost=total_cost,
        )

    @staticmethod
    def _read_nodes(
        raw_nodes: Sequence[Any],
    ) -> tuple[dict[str, Mapping[str, Any]], list[str]]:
        nodes: dict[str, Mapping[str, Any]] = {}
        order: list[str] = []
        for index, raw_node in enumerate(raw_nodes):
            if not isinstance(raw_node, Mapping):
                raise ProductionPlannerError(f"scope.nodes[{index}] must be a mapping")
            node_id = _non_empty(raw_node.get("id"), f"scope.nodes[{index}].id")
            if node_id in nodes:
                raise ProductionPlannerError(f"duplicate production node id: {node_id}")
            nodes[node_id] = raw_node
            order.append(node_id)
        return nodes, order

    @staticmethod
    def _read_dependencies(
        nodes: Mapping[str, Mapping[str, Any]],
        order: Sequence[str],
        raw_edges: object,
    ) -> dict[str, list[str]]:
        dependencies: dict[str, list[str]] = {node_id: [] for node_id in order}
        for node_id in order:
            raw_dependencies = nodes[node_id].get("depends_on", ())
            if not isinstance(raw_dependencies, Sequence) or isinstance(
                raw_dependencies, (str, bytes)
            ):
                raise ProductionPlannerError(
                    f"node {node_id} depends_on must be a sequence"
                )
            for upstream in raw_dependencies:
                ProductionPlanner._add_dependency(
                    dependencies, nodes, _non_empty(upstream, "upstream node"), node_id
                )

        if raw_edges is None:
            raw_edges = ()
        if not isinstance(raw_edges, Sequence) or isinstance(raw_edges, (str, bytes)):
            raise ProductionPlannerError("scope.edges must be a sequence")
        for index, edge in enumerate(raw_edges):
            if isinstance(edge, Mapping):
                upstream = edge.get(
                    "upstream_node_id", edge.get("upstream", edge.get("from"))
                )
                downstream = edge.get(
                    "downstream_node_id", edge.get("downstream", edge.get("to"))
                )
            elif isinstance(edge, Sequence) and not isinstance(edge, (str, bytes)) and len(edge) == 2:
                upstream, downstream = edge
            else:
                raise ProductionPlannerError(f"scope.edges[{index}] is invalid")
            ProductionPlanner._add_dependency(
                dependencies,
                nodes,
                _non_empty(upstream, "edge upstream"),
                _non_empty(downstream, "edge downstream"),
            )
        return dependencies

    @staticmethod
    def _add_dependency(
        dependencies: dict[str, list[str]],
        nodes: Mapping[str, Mapping[str, Any]],
        upstream: str,
        downstream: str,
    ) -> None:
        if upstream not in nodes:
            raise ProductionPlannerError(f"unknown upstream production node: {upstream}")
        if downstream not in nodes:
            raise ProductionPlannerError(f"unknown downstream production node: {downstream}")
        if upstream == downstream:
            raise ProductionCycleError(f"production node depends on itself: {upstream}")
        if upstream not in dependencies[downstream]:
            dependencies[downstream].append(upstream)

    @staticmethod
    def _stable_topological_order(
        order: Sequence[str], dependencies: Mapping[str, Sequence[str]]
    ) -> list[str]:
        positions = {node_id: index for index, node_id in enumerate(order)}
        indegree = {node_id: len(dependencies[node_id]) for node_id in order}
        downstream: dict[str, list[str]] = {node_id: [] for node_id in order}
        for node_id in order:
            for upstream in dependencies[node_id]:
                downstream[upstream].append(node_id)
        ready = [(positions[node_id], node_id) for node_id in order if indegree[node_id] == 0]
        heapq.heapify(ready)
        result: list[str] = []
        while ready:
            _, node_id = heapq.heappop(ready)
            result.append(node_id)
            for child in downstream[node_id]:
                indegree[child] -= 1
                if indegree[child] == 0:
                    heapq.heappush(ready, (positions[child], child))
        if len(result) != len(order):
            cyclic = [node_id for node_id in order if indegree[node_id] > 0]
            raise ProductionCycleError(
                f"production DAG contains a cycle involving: {', '.join(cyclic)}"
            )
        return result

    @staticmethod
    def _artifact_index(
        artifacts: Mapping[str, Any] | Iterable[Mapping[str, Any]],
    ) -> dict[str, Mapping[str, Any]]:
        if isinstance(artifacts, Mapping):
            result: dict[str, Mapping[str, Any]] = {}
            for node_id, artifact in artifacts.items():
                if not isinstance(artifact, Mapping):
                    raise ProductionPlannerError(
                        f"artifact for {node_id} must be a mapping"
                    )
                result[_non_empty(node_id, "artifact node id")] = artifact
            return result
        result = {}
        for artifact in artifacts:
            if not isinstance(artifact, Mapping):
                raise ProductionPlannerError("existing artifact must be a mapping")
            node_id = _non_empty(
                artifact.get("node_id", artifact.get("id")), "artifact node id"
            )
            if node_id in result:
                raise ProductionPlannerError(f"duplicate artifact for node: {node_id}")
            result[node_id] = artifact
        return result

    @staticmethod
    def _snapshot_overrides(snapshot: Mapping[str, Any]) -> Mapping[str, Any]:
        overrides = snapshot.get("node_overrides", snapshot.get("nodes", {}))
        if overrides is None:
            return {}
        if not isinstance(overrides, Mapping):
            raise ProductionPlannerError("snapshot node overrides must be a mapping")
        return overrides

    @staticmethod
    def _action_for(
        *,
        node: Mapping[str, Any],
        artifact: Mapping[str, Any] | None,
        fingerprint: str,
        policy: AuditPolicy,
        upstream_actions: Mapping[str, ProductionPlanAction],
    ) -> tuple[ProductionPlanAction, str | None]:
        if node.get("skip", False):
            return ProductionPlanAction.SKIP, "scope_skip"
        if any(action is ProductionPlanAction.SKIP for action in upstream_actions.values()):
            return ProductionPlanAction.SKIP, "blocked_upstream"
        if artifact is not None:
            status = str(artifact.get("status", "succeeded")).lower()
            risk = str(artifact.get("risk", artifact.get("risk_level", "low"))).lower()
            if status == "quality_failed":
                return ProductionPlanAction.SKIP, "quality_failed"
            if risk == "high":
                return ProductionPlanAction.SKIP, "high_risk"
        if artifact is None:
            return ProductionPlanAction.CREATE, "missing_artifact"
        existing_fingerprint = artifact.get(
            "fingerprint", artifact.get("task_fingerprint", artifact.get("input_sha256"))
        )
        if existing_fingerprint == fingerprint:
            return ProductionPlanAction.REUSE, "content_hash_match"
        return ProductionPlanAction.INVALIDATE, "content_hash_changed"

    @staticmethod
    def _output_hash(
        artifact: Mapping[str, Any] | None,
        fingerprint: str,
        action: ProductionPlanAction,
    ) -> str:
        if artifact is not None and action in {
            ProductionPlanAction.REUSE,
            ProductionPlanAction.SKIP,
        }:
            for key in ("content_sha256", "content_hash", "sha256"):
                value = artifact.get(key)
                if isinstance(value, str) and value:
                    return value
        return fingerprint


__all__ = [
    "AuditPolicy",
    "ProductionCycleError",
    "ProductionNodeType",
    "ProductionPlanAction",
    "ProductionPlanNode",
    "ProductionPlanPreview",
    "ProductionPlanner",
    "ProductionPlannerError",
    "UnknownProductionNodeType",
]
