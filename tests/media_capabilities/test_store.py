from __future__ import annotations

import json
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from novelvideo.media_capabilities.models import (
    CapabilityImplementation,
    MediaCapability,
    ProviderAccount,
    RoutingPolicy,
    WorkflowProfile,
)
from novelvideo.media_capabilities.store import (
    MediaCapabilityStore,
    StoreConflictError,
)


def provider(identifier: str = "provider-b") -> ProviderAccount:
    return ProviderAccount(
        id=identifier,
        provider_type="example",
        base_url="https://media.invalid/v1",
        credential_ref=f"secret://media/{identifier}",
        max_concurrency=3,
    )


def workflow(
    identifier: str = "workflow-b",
    version: int = 1,
    *,
    status: str = "draft",
    source_sha256: str = "draft-source",
) -> WorkflowProfile:
    return WorkflowProfile(
        id=identifier,
        version=version,
        workflow_id=f"remote-{identifier}",
        capabilities=[MediaCapability.IMAGE_SINGLE],
        bindings={"prompt": "positive_prompt"},
        outputs={"images": "$.outputs"},
        source_sha256=source_sha256,
        status=status,
    )


def implementation(
    identifier: str = "implementation-b",
    *,
    capability: MediaCapability = MediaCapability.IMAGE_SINGLE,
    provider_account: str = "provider-b",
    workflow_profile: str | None = "workflow-b",
) -> CapabilityImplementation:
    return CapabilityImplementation(
        id=identifier,
        capability=capability,
        provider_account=provider_account,
        workflow_profile=workflow_profile,
    )


def policy(
    capability: MediaCapability = MediaCapability.IMAGE_SINGLE,
    *,
    default_implementation: str = "implementation-b",
    fallback_chain: list[str] | None = None,
) -> RoutingPolicy:
    return RoutingPolicy(
        capability=capability,
        default_implementation=default_implementation,
        fallback_chain=[] if fallback_chain is None else fallback_chain,
        concurrency_limit=2,
    )


def test_store_creates_expected_schema(tmp_path: Path) -> None:
    database = tmp_path / "nested" / "media.db"
    MediaCapabilityStore(database)

    connection = sqlite3.connect(database)
    try:
        expected = {
            "media_provider_accounts": ("id",),
            "media_workflow_profiles": ("id", "version"),
            "media_capability_implementations": ("id",),
            "media_routing_policies": ("capability",),
        }
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert set(expected) <= tables
        for table, primary_key in expected.items():
            columns = connection.execute(f"PRAGMA table_info({table})").fetchall()
            assert [row[1] for row in columns] == [
                *primary_key,
                "body_json",
                "updated_at",
            ]
            assert tuple(
                name
                for _, name in sorted(
                    (row[5], row[1]) for row in columns if row[5]
                )
            ) == primary_key
    finally:
        connection.close()


def test_crud_round_trips_all_models(tmp_path: Path) -> None:
    store = MediaCapabilityStore(tmp_path / "media.db")
    account = provider()
    profile = workflow(status="active")
    capability_implementation = implementation()
    routing_policy = policy()

    store.save_provider(account)
    store.save_workflow(profile)
    store.save_implementation(capability_implementation)
    store.save_policy(routing_policy)

    assert store.get_provider(account.id) == account
    assert store.get_workflow(profile.id, profile.version) == profile
    assert store.get_implementation(capability_implementation.id) == (
        capability_implementation
    )
    assert store.get_policy(routing_policy.capability) == routing_policy
    assert store.get_provider("missing") is None
    assert store.get_workflow("missing", 1) is None
    assert store.get_implementation("missing") is None
    assert store.get_policy(MediaCapability.TTS_SYNTHESIZE) is None


def test_lists_are_stably_sorted(tmp_path: Path) -> None:
    store = MediaCapabilityStore(tmp_path / "media.db")
    for item in (provider("provider-z"), provider("provider-a")):
        store.save_provider(item)
    for item in (
        workflow("workflow-z", 1, status="active"),
        workflow("workflow-a", 2, status="active"),
        workflow("workflow-a", 1, status="active"),
    ):
        store.save_workflow(item)
    store.save_provider(provider("provider-b"))
    for item in (
        implementation("implementation-z", workflow_profile=None),
        implementation("implementation-a", workflow_profile=None),
    ):
        store.save_implementation(item)
    for item in (
        policy(
            MediaCapability.IMAGE_SINGLE,
            default_implementation="implementation-a",
        ),
        policy(
            MediaCapability.VIDEO_T2VA,
            default_implementation="implementation-video",
        ),
    ):
        if item.capability == MediaCapability.VIDEO_T2VA:
            store.save_implementation(
                implementation(
                    "implementation-video",
                    capability=MediaCapability.VIDEO_T2VA,
                    workflow_profile=None,
                )
            )
        store.save_policy(item)

    assert [item.id for item in store.list_providers()] == [
        "provider-a",
        "provider-b",
        "provider-z",
    ]
    assert [(item.id, item.version) for item in store.list_workflows()] == [
        ("workflow-a", 1),
        ("workflow-a", 2),
        ("workflow-z", 1),
    ]
    assert [item.id for item in store.list_implementations()] == [
        "implementation-a",
        "implementation-video",
        "implementation-z",
    ]
    assert [item.capability for item in store.list_policies()] == [
        MediaCapability.IMAGE_SINGLE,
        MediaCapability.VIDEO_T2VA,
    ]


def test_delete_is_consistent_for_all_models(tmp_path: Path) -> None:
    store = MediaCapabilityStore(tmp_path / "media.db")
    store.save_provider(provider())
    store.save_workflow(workflow(status="active"))
    store.save_implementation(implementation())
    store.save_policy(policy())

    assert store.delete_policy(MediaCapability.IMAGE_SINGLE) is True
    assert store.delete_implementation("implementation-b") is True
    with pytest.raises(StoreConflictError):
        store.delete_workflow("workflow-b", 1)
    store.save_workflow(workflow("workflow-draft", status="draft"))
    assert store.delete_workflow("workflow-draft", 1) is True
    assert store.delete_provider("provider-b") is True
    assert store.delete_policy(MediaCapability.IMAGE_SINGLE) is False
    assert store.delete_implementation("implementation-b") is False
    assert store.delete_provider("provider-b") is False
    assert store.delete_workflow("workflow-draft", 1) is False


def test_raw_provider_json_contains_only_controlled_credential_reference(
    tmp_path: Path,
) -> None:
    store = MediaCapabilityStore(tmp_path / "media.db")
    store.save_provider(provider())

    raw = store.raw_provider_json("provider-b")
    assert raw is not None
    body = json.loads(raw)
    assert body["credential_ref"] == "secret://media/provider-b"
    assert "api_key" not in body
    assert "token" not in body
    assert "known-plaintext-token" not in raw


def test_implementation_requires_existing_provider_and_preserves_old_value(
    tmp_path: Path,
) -> None:
    store = MediaCapabilityStore(tmp_path / "media.db")
    store.save_provider(provider())
    store.save_workflow(workflow(status="active"))
    original = implementation()
    store.save_implementation(original)

    with pytest.raises(
        StoreConflictError,
        match=r"provider missing-provider does not exist",
    ):
        store.save_implementation(
            implementation(
                provider_account="missing-provider",
                workflow_profile=None,
            )
        )

    assert store.get_implementation("implementation-b") == original


@pytest.mark.parametrize("workflow_status", [None, "draft", "archived"])
def test_implementation_requires_published_workflow_when_referenced(
    tmp_path: Path,
    workflow_status: str | None,
) -> None:
    store = MediaCapabilityStore(tmp_path / "media.db")
    store.save_provider(provider())
    if workflow_status is not None:
        store.save_workflow(workflow(status=workflow_status))

    with pytest.raises(
        StoreConflictError,
        match=r"workflow workflow-b has no published version",
    ):
        store.save_implementation(implementation())

    assert store.get_implementation("implementation-b") is None


def test_implementation_accepts_any_published_version_for_workflow_id(
    tmp_path: Path,
) -> None:
    store = MediaCapabilityStore(tmp_path / "media.db")
    store.save_provider(provider())
    store.save_workflow(workflow(version=1, status="draft"))
    store.save_workflow(workflow(version=2, status="published"))
    item = implementation()

    store.save_implementation(item)

    assert store.get_implementation(item.id) == item


@pytest.mark.parametrize(
    ("default_implementation", "fallback_chain", "missing"),
    [
        ("missing-default", [], "missing-default"),
        ("implementation-b", ["missing-fallback"], "missing-fallback"),
    ],
)
def test_policy_rejects_missing_implementation_and_preserves_old_value(
    tmp_path: Path,
    default_implementation: str,
    fallback_chain: list[str],
    missing: str,
) -> None:
    store = MediaCapabilityStore(tmp_path / "media.db")
    store.save_provider(provider())
    store.save_implementation(implementation(workflow_profile=None))
    original = policy()
    store.save_policy(original)

    with pytest.raises(
        StoreConflictError,
        match=rf"implementation {missing} does not exist",
    ):
        store.save_policy(
            policy(
                default_implementation=default_implementation,
                fallback_chain=fallback_chain,
            )
        )

    assert store.get_policy(MediaCapability.IMAGE_SINGLE) == original


def test_policy_rejects_implementation_with_different_capability(
    tmp_path: Path,
) -> None:
    store = MediaCapabilityStore(tmp_path / "media.db")
    store.save_provider(provider())
    store.save_implementation(
        implementation(
            capability=MediaCapability.VIDEO_T2VA,
            workflow_profile=None,
        )
    )

    with pytest.raises(
        StoreConflictError,
        match=(
            r"implementation implementation-b capability video.t2va "
            r"does not match policy image.single"
        ),
    ):
        store.save_policy(policy())

    assert store.get_policy(MediaCapability.IMAGE_SINGLE) is None


@pytest.mark.parametrize("as_fallback", [False, True])
def test_update_implementation_rejects_capability_change_when_policy_references_it(
    tmp_path: Path,
    as_fallback: bool,
) -> None:
    store = MediaCapabilityStore(tmp_path / "media.db")
    store.save_provider(provider())
    original = implementation(workflow_profile=None)
    store.save_implementation(original)
    if as_fallback:
        store.save_implementation(
            implementation("implementation-default", workflow_profile=None)
        )
        routing = policy(
            default_implementation="implementation-default",
            fallback_chain=[original.id],
        )
    else:
        routing = policy(default_implementation=original.id)
    store.save_policy(routing)

    with pytest.raises(
        StoreConflictError,
        match=(
            r"implementation implementation-b is referenced by policy image.single "
            r"and cannot change capability to video.t2va"
        ),
    ):
        store.save_implementation(
            implementation(
                capability=MediaCapability.VIDEO_T2VA,
                workflow_profile=None,
            )
        )

    assert store.get_implementation(original.id) == original
    assert store.get_policy(MediaCapability.IMAGE_SINGLE) == routing


def test_delete_provider_rejects_referenced_account(tmp_path: Path) -> None:
    store = MediaCapabilityStore(tmp_path / "media.db")
    account = provider()
    store.save_provider(account)
    store.save_implementation(implementation(workflow_profile=None))

    with pytest.raises(
        StoreConflictError,
        match=r"provider provider-b is referenced by implementation implementation-b",
    ):
        store.delete_provider("provider-b")

    assert store.get_provider("provider-b") == account


@pytest.mark.parametrize("published_status", ["active", "published"])
def test_delete_workflow_rejects_non_draft_version(
    tmp_path: Path,
    published_status: str,
) -> None:
    store = MediaCapabilityStore(tmp_path / "media.db")
    saved = workflow(status=published_status)
    store.save_workflow(saved)

    with pytest.raises(
        StoreConflictError,
        match=r"workflow workflow-b version 1 is immutable",
    ):
        store.delete_workflow("workflow-b", 1)

    assert store.get_workflow("workflow-b", 1) == saved


def test_delete_draft_workflow_rejects_referenced_id(tmp_path: Path) -> None:
    store = MediaCapabilityStore(tmp_path / "media.db")
    store.save_provider(provider())
    draft = workflow(version=1, status="draft")
    store.save_workflow(draft)
    store.save_workflow(workflow(version=2, status="active"))
    store.save_implementation(implementation())

    with pytest.raises(
        StoreConflictError,
        match=r"workflow workflow-b is referenced by implementation implementation-b",
    ):
        store.delete_workflow("workflow-b", 1)

    assert store.get_workflow("workflow-b", 1) == draft


@pytest.mark.parametrize("as_fallback", [False, True])
def test_delete_implementation_rejects_policy_reference(
    tmp_path: Path,
    as_fallback: bool,
) -> None:
    store = MediaCapabilityStore(tmp_path / "media.db")
    store.save_provider(provider())
    target = implementation(workflow_profile=None)
    store.save_implementation(target)
    if as_fallback:
        store.save_implementation(
            implementation("implementation-default", workflow_profile=None)
        )
        routing = policy(
            default_implementation="implementation-default",
            fallback_chain=[target.id],
        )
    else:
        routing = policy(default_implementation=target.id)
    store.save_policy(routing)

    with pytest.raises(
        StoreConflictError,
        match=rf"implementation {target.id} is referenced by policy image.single",
    ):
        store.delete_implementation(target.id)

    assert store.get_implementation(target.id) == target


def test_draft_workflow_can_update_and_be_promoted(tmp_path: Path) -> None:
    store = MediaCapabilityStore(tmp_path / "media.db")
    store.save_workflow(workflow(source_sha256="first"))
    store.save_workflow(workflow(source_sha256="second"))
    promoted = workflow(status="active", source_sha256="published")
    store.save_workflow(promoted)

    assert store.get_workflow("workflow-b", 1) == promoted


@pytest.mark.parametrize("published_status", ["active", "published"])
def test_non_draft_workflow_is_immutable_and_conflict_rolls_back(
    tmp_path: Path, published_status: str
) -> None:
    store = MediaCapabilityStore(tmp_path / "media.db")
    published = workflow(status=published_status, source_sha256="original")
    store.save_workflow(published)

    with pytest.raises(
        StoreConflictError,
        match=r"workflow workflow-b version 1 is immutable",
    ):
        store.save_workflow(workflow(status="draft", source_sha256="replacement"))

    assert store.get_workflow("workflow-b", 1) == published


def test_two_store_instances_can_write_without_corruption(tmp_path: Path) -> None:
    database = tmp_path / "media.db"
    first = MediaCapabilityStore(database)
    second = MediaCapabilityStore(database)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(first.save_provider, provider("provider-a")),
            executor.submit(second.save_provider, provider("provider-b")),
        ]
        for future in futures:
            future.result()

    assert [item.id for item in first.list_providers()] == [
        "provider-a",
        "provider-b",
    ]


@pytest.mark.parametrize(
    ("table", "key_column", "key", "save", "read"),
    [
        (
            "media_provider_accounts",
            "id",
            "provider-b",
            lambda store: store.save_provider(provider()),
            lambda store: store.get_provider("provider-b"),
        ),
        (
            "media_workflow_profiles",
            "id",
            "workflow-b",
            lambda store: store.save_workflow(workflow()),
            lambda store: store.get_workflow("workflow-b", 1),
        ),
        (
            "media_capability_implementations",
            "id",
            "implementation-b",
            lambda store: (
                store.save_provider(provider()),
                store.save_workflow(workflow(status="active")),
                store.save_implementation(implementation()),
            ),
            lambda store: store.get_implementation("implementation-b"),
        ),
        (
            "media_routing_policies",
            "capability",
            MediaCapability.IMAGE_SINGLE.value,
            lambda store: (
                store.save_provider(provider()),
                store.save_workflow(workflow(status="active")),
                store.save_implementation(implementation()),
                store.save_policy(policy()),
            ),
            lambda store: store.get_policy(MediaCapability.IMAGE_SINGLE),
        ),
    ],
)
def test_reads_revalidate_corrupted_json(
    tmp_path: Path,
    table: str,
    key_column: str,
    key: str,
    save,
    read,
) -> None:
    database = tmp_path / "media.db"
    store = MediaCapabilityStore(database)
    save(store)
    connection = sqlite3.connect(database)
    try:
        connection.execute(
            f"UPDATE {table} SET body_json = ? WHERE {key_column} = ?",
            ('{"unexpected": true}', key),
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(ValidationError):
        read(store)
