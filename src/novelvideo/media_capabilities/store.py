"""SQLite persistence for media capability configuration."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

from novelvideo.media_capabilities.models import (
    CapabilityImplementation,
    MediaCapability,
    ProviderAccount,
    RoutingPolicy,
    RunningHubWorkflowSettings,
    WorkflowProfile,
)
from novelvideo.sqlite_pragmas import configure_sqlite_connection


class StoreConflictError(RuntimeError):
    """Raised when a store write violates an immutability constraint."""


ModelT = TypeVar("ModelT", bound=BaseModel)


class MediaCapabilityStore:
    """Persist media providers, workflows, implementations, and routing policies."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.path), timeout=10)
        try:
            connection.row_factory = sqlite3.Row
            configure_sqlite_connection(connection)
            return connection
        except Exception:
            connection.close()
            raise

    def _initialize_schema(self) -> None:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS media_provider_accounts (
                    id TEXT PRIMARY KEY,
                    body_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS media_workflow_profiles (
                    id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    body_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (id, version)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS media_capability_implementations (
                    id TEXT PRIMARY KEY,
                    body_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS media_routing_policies (
                    capability TEXT PRIMARY KEY,
                    body_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS media_runtime_settings (
                    id TEXT PRIMARY KEY,
                    body_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _updated_at() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _save(
        self,
        table: str,
        key_column: str,
        key: str,
        model: BaseModel,
    ) -> None:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                f"""
                INSERT INTO {table} ({key_column}, body_json, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT({key_column}) DO UPDATE SET
                    body_json = excluded.body_json,
                    updated_at = excluded.updated_at
                """,
                (key, model.model_dump_json(), self._updated_at()),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _get(
        self,
        table: str,
        key_column: str,
        key: str,
        model: type[ModelT],
    ) -> ModelT | None:
        connection = self._connect()
        try:
            row = connection.execute(
                f"SELECT body_json FROM {table} WHERE {key_column} = ?",
                (key,),
            ).fetchone()
            if row is None:
                return None
            return model.model_validate_json(row["body_json"])
        finally:
            connection.close()

    def _list(
        self,
        table: str,
        order_by: str,
        model: type[ModelT],
    ) -> list[ModelT]:
        connection = self._connect()
        try:
            rows = connection.execute(
                f"SELECT body_json FROM {table} ORDER BY {order_by}"
            ).fetchall()
            return [model.model_validate_json(row["body_json"]) for row in rows]
        finally:
            connection.close()

    def _delete(self, table: str, key_column: str, key: str) -> bool:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                f"DELETE FROM {table} WHERE {key_column} = ?",
                (key,),
            )
            connection.commit()
            return cursor.rowcount > 0
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def save_provider(self, provider: ProviderAccount) -> None:
        self._save("media_provider_accounts", "id", provider.id, provider)

    def get_provider(self, identifier: str) -> ProviderAccount | None:
        return self._get(
            "media_provider_accounts", "id", identifier, ProviderAccount
        )

    def list_providers(self) -> list[ProviderAccount]:
        return self._list("media_provider_accounts", "id", ProviderAccount)

    def delete_provider(self, identifier: str) -> bool:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            provider_row = connection.execute(
                "SELECT 1 FROM media_provider_accounts WHERE id = ?",
                (identifier,),
            ).fetchone()
            if provider_row is None:
                connection.commit()
                return False
            implementation_rows = connection.execute(
                """
                SELECT body_json FROM media_capability_implementations
                ORDER BY id
                """
            ).fetchall()
            for row in implementation_rows:
                implementation = CapabilityImplementation.model_validate_json(
                    row["body_json"]
                )
                if implementation.provider_account == identifier:
                    raise StoreConflictError(
                        f"provider {identifier} is referenced by implementation "
                        f"{implementation.id}"
                    )
            cursor = connection.execute(
                "DELETE FROM media_provider_accounts WHERE id = ?",
                (identifier,),
            )
            connection.commit()
            return cursor.rowcount > 0
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def save_runninghub_workflows(
        self,
        settings: RunningHubWorkflowSettings,
    ) -> None:
        self._save(
            "media_runtime_settings",
            "id",
            "runninghub-workflows",
            settings,
        )

    def save_provider_bundle(
        self,
        provider: ProviderAccount,
        workflows: RunningHubWorkflowSettings | None = None,
    ) -> None:
        """Atomically persist a provider and its optional workflow settings."""
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            now = self._updated_at()
            connection.execute(
                """
                INSERT INTO media_provider_accounts (id, body_json, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    body_json = excluded.body_json,
                    updated_at = excluded.updated_at
                """,
                (provider.id, provider.model_dump_json(), now),
            )
            if workflows is not None:
                connection.execute(
                    """
                    INSERT INTO media_runtime_settings (id, body_json, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        body_json = excluded.body_json,
                        updated_at = excluded.updated_at
                    """,
                    (
                        "runninghub-workflows",
                        workflows.model_dump_json(),
                        now,
                    ),
                )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def get_runninghub_workflows(self) -> RunningHubWorkflowSettings:
        return self._get(
            "media_runtime_settings",
            "id",
            "runninghub-workflows",
            RunningHubWorkflowSettings,
        ) or RunningHubWorkflowSettings()

    def raw_provider_json(self, identifier: str) -> str | None:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT body_json FROM media_provider_accounts WHERE id = ?",
                (identifier,),
            ).fetchone()
            return None if row is None else str(row["body_json"])
        finally:
            connection.close()

    def save_workflow(self, workflow: WorkflowProfile) -> None:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """
                SELECT body_json FROM media_workflow_profiles
                WHERE id = ? AND version = ?
                """,
                (workflow.id, workflow.version),
            ).fetchone()
            if existing is not None:
                saved = WorkflowProfile.model_validate_json(existing["body_json"])
                if saved.status != "draft":
                    raise StoreConflictError(
                        f"workflow {workflow.id} version {workflow.version} is immutable"
                    )
            connection.execute(
                """
                INSERT INTO media_workflow_profiles (
                    id, version, body_json, updated_at
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(id, version) DO UPDATE SET
                    body_json = excluded.body_json,
                    updated_at = excluded.updated_at
                """,
                (
                    workflow.id,
                    workflow.version,
                    workflow.model_dump_json(),
                    self._updated_at(),
                ),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def get_workflow(self, identifier: str, version: int) -> WorkflowProfile | None:
        connection = self._connect()
        try:
            row = connection.execute(
                """
                SELECT body_json FROM media_workflow_profiles
                WHERE id = ? AND version = ?
                """,
                (identifier, version),
            ).fetchone()
            if row is None:
                return None
            return WorkflowProfile.model_validate_json(row["body_json"])
        finally:
            connection.close()

    def list_workflows(self) -> list[WorkflowProfile]:
        return self._list(
            "media_workflow_profiles", "id, version", WorkflowProfile
        )

    def delete_workflow(self, identifier: str, version: int) -> bool:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            workflow_row = connection.execute(
                """
                SELECT body_json FROM media_workflow_profiles
                WHERE id = ? AND version = ?
                """,
                (identifier, version),
            ).fetchone()
            if workflow_row is None:
                connection.commit()
                return False
            saved = WorkflowProfile.model_validate_json(workflow_row["body_json"])
            if saved.status != "draft":
                raise StoreConflictError(
                    f"workflow {identifier} version {version} is immutable"
                )
            implementation_rows = connection.execute(
                """
                SELECT body_json FROM media_capability_implementations
                ORDER BY id
                """
            ).fetchall()
            for row in implementation_rows:
                implementation = CapabilityImplementation.model_validate_json(
                    row["body_json"]
                )
                if implementation.workflow_profile == identifier:
                    raise StoreConflictError(
                        f"workflow {identifier} is referenced by implementation "
                        f"{implementation.id}"
                    )
            cursor = connection.execute(
                "DELETE FROM media_workflow_profiles WHERE id = ? AND version = ?",
                (identifier, version),
            )
            connection.commit()
            return cursor.rowcount > 0
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def save_implementation(self, implementation: CapabilityImplementation) -> None:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            provider_row = connection.execute(
                "SELECT 1 FROM media_provider_accounts WHERE id = ?",
                (implementation.provider_account,),
            ).fetchone()
            if provider_row is None:
                raise StoreConflictError(
                    f"provider {implementation.provider_account} does not exist"
                )
            if implementation.workflow_profile:
                workflow_rows = connection.execute(
                    """
                    SELECT body_json FROM media_workflow_profiles
                    WHERE id = ? ORDER BY version
                    """,
                    (implementation.workflow_profile,),
                ).fetchall()
                workflows = [
                    WorkflowProfile.model_validate_json(row["body_json"])
                    for row in workflow_rows
                ]
                if not any(
                    item.status in {"active", "published"} for item in workflows
                ):
                    raise StoreConflictError(
                        "workflow "
                        f"{implementation.workflow_profile} has no published version"
                    )
            existing_row = connection.execute(
                """
                SELECT body_json FROM media_capability_implementations
                WHERE id = ?
                """,
                (implementation.id,),
            ).fetchone()
            if existing_row is not None:
                CapabilityImplementation.model_validate_json(
                    existing_row["body_json"]
                )
                policy_rows = connection.execute(
                    """
                    SELECT body_json FROM media_routing_policies
                    ORDER BY capability
                    """
                ).fetchall()
                policies = [
                    RoutingPolicy.model_validate_json(row["body_json"])
                    for row in policy_rows
                ]
                for policy in policies:
                    is_referenced = (
                        implementation.id == policy.default_implementation
                        or implementation.id in policy.fallback_chain
                    )
                    if is_referenced and implementation.capability != policy.capability:
                        raise StoreConflictError(
                            f"implementation {implementation.id} is referenced by policy "
                            f"{policy.capability.value} and cannot change capability to "
                            f"{implementation.capability.value}"
                        )
            connection.execute(
                """
                INSERT INTO media_capability_implementations (
                    id, body_json, updated_at
                ) VALUES (?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    body_json = excluded.body_json,
                    updated_at = excluded.updated_at
                """,
                (
                    implementation.id,
                    implementation.model_dump_json(),
                    self._updated_at(),
                ),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def get_implementation(
        self, identifier: str
    ) -> CapabilityImplementation | None:
        return self._get(
            "media_capability_implementations",
            "id",
            identifier,
            CapabilityImplementation,
        )

    def list_implementations(self) -> list[CapabilityImplementation]:
        return self._list(
            "media_capability_implementations", "id", CapabilityImplementation
        )

    def delete_implementation(self, identifier: str) -> bool:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            implementation_row = connection.execute(
                "SELECT 1 FROM media_capability_implementations WHERE id = ?",
                (identifier,),
            ).fetchone()
            if implementation_row is None:
                connection.commit()
                return False
            policy_rows = connection.execute(
                "SELECT body_json FROM media_routing_policies ORDER BY capability"
            ).fetchall()
            for row in policy_rows:
                policy = RoutingPolicy.model_validate_json(row["body_json"])
                if identifier == policy.default_implementation or (
                    identifier in policy.fallback_chain
                ):
                    raise StoreConflictError(
                        f"implementation {identifier} is referenced by policy "
                        f"{policy.capability.value}"
                    )
            cursor = connection.execute(
                "DELETE FROM media_capability_implementations WHERE id = ?",
                (identifier,),
            )
            connection.commit()
            return cursor.rowcount > 0
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def save_policy(self, policy: RoutingPolicy) -> None:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            implementation_ids = [
                policy.default_implementation,
                *policy.fallback_chain,
            ]
            for identifier in implementation_ids:
                row = connection.execute(
                    """
                    SELECT body_json FROM media_capability_implementations
                    WHERE id = ?
                    """,
                    (identifier,),
                ).fetchone()
                if row is None:
                    raise StoreConflictError(
                        f"implementation {identifier} does not exist"
                    )
                implementation = CapabilityImplementation.model_validate_json(
                    row["body_json"]
                )
                if implementation.capability != policy.capability:
                    raise StoreConflictError(
                        f"implementation {identifier} capability "
                        f"{implementation.capability.value} does not match policy "
                        f"{policy.capability.value}"
                    )
            connection.execute(
                """
                INSERT INTO media_routing_policies (
                    capability, body_json, updated_at
                ) VALUES (?, ?, ?)
                ON CONFLICT(capability) DO UPDATE SET
                    body_json = excluded.body_json,
                    updated_at = excluded.updated_at
                """,
                (
                    policy.capability.value,
                    policy.model_dump_json(),
                    self._updated_at(),
                ),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def get_policy(self, capability: MediaCapability) -> RoutingPolicy | None:
        return self._get(
            "media_routing_policies",
            "capability",
            capability.value,
            RoutingPolicy,
        )

    def list_policies(self) -> list[RoutingPolicy]:
        return self._list("media_routing_policies", "capability", RoutingPolicy)

    def delete_policy(self, capability: MediaCapability) -> bool:
        return self._delete(
            "media_routing_policies", "capability", capability.value
        )
