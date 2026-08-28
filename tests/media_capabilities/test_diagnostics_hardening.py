from __future__ import annotations

import pytest

from novelvideo.media_capabilities.diagnostics import (
    contains_sensitive_value,
    sanitize_diagnostics,
)
from novelvideo.media_capabilities.models import MediaCapability
from novelvideo.media_capabilities.task_store import TaskStore, TaskStoreConflictError


@pytest.mark.parametrize(
    "value",
    [
        "password=secret",
        "credential=secret",
        "client_secret=secret",
        "Cookie: session=secret",
        "Authorization: Bearer secret",
        "Bearer secret",
        "abcdefgh.ijklmnop.qrstuvwx",
    ],
)
def test_public_and_task_store_share_sensitive_value_detection(value: str, tmp_path) -> None:
    assert contains_sensitive_value(value) is True
    assert sanitize_diagnostics({"fallback_reason": value}) == {
        "fallback_reason": "[REDACTED]"
    }

    store = TaskStore(tmp_path / "tasks.db")
    task = store.create_task(
        MediaCapability.VIDEO_I2VA,
        "shared-sensitive-value",
        {"provider": "runninghub"},
        {"prompt": "move"},
    )
    attempt = store.start_attempt(task.id, "runninghub-main")
    with pytest.raises(TaskStoreConflictError, match="prohibited"):
        store.record_provider_status(
            attempt.id,
            "running",
            {"fallback_reason": value},
        )


@pytest.mark.parametrize("key", ["api_key_value", "private_key_pem"])
def test_compound_sensitive_keys_are_redacted(key: str) -> None:
    diagnostics = sanitize_diagnostics(
        {"reference_summary": {key: "secret", "count": 1}}
    )
    assert diagnostics["reference_summary"][key] == "[REDACTED]"
    assert diagnostics["reference_summary"]["count"] == 1


def test_url_path_with_embedded_credential_is_redacted() -> None:
    diagnostics = sanitize_diagnostics(
        {
            "reference_summary": {
                "source": "https://cdn.example/download/token=SECRET/file"
            }
        }
    )
    assert diagnostics["reference_summary"]["source"] == "[REDACTED]"


def test_recursive_sanitizer_redacts_cycles_without_raising() -> None:
    cyclic: dict[str, object] = {}
    cyclic["self"] = cyclic
    diagnostics = sanitize_diagnostics({"reference_summary": cyclic})
    assert diagnostics == {"reference_summary": {"self": "[REDACTED]"}}


def test_recursive_sanitizer_redacts_over_depth_without_raising() -> None:
    deep: dict[str, object] = {}
    cursor = deep
    for _ in range(64):
        child: dict[str, object] = {}
        cursor["child"] = child
        cursor = child
    diagnostics = sanitize_diagnostics({"reference_summary": deep})
    assert "[REDACTED]" in repr(diagnostics)


def test_recursive_sanitizer_redacts_over_node_budget_without_raising() -> None:
    wide = {f"item_{index}": index for index in range(2_000)}
    diagnostics = sanitize_diagnostics({"reference_summary": wide})
    assert diagnostics == {"reference_summary": "[REDACTED]"}


@pytest.mark.parametrize(
    ("url", "safe_url"),
    [
        (
            "https://cdn.example/video.mp4?q=Bearer%20secret",
            "https://cdn.example/video.mp4",
        ),
        (
            "https://cdn.example/video.mp4?next=abcdefgh.ijklmnop.qrstuvwx",
            "https://cdn.example/video.mp4",
        ),
    ],
)
def test_query_values_are_checked_for_secrets_in_public_and_task_store(
    url: str,
    safe_url: str,
    tmp_path,
) -> None:
    assert sanitize_diagnostics(
        {"reference_summary": {"source": url}}
    ) == {"reference_summary": {"source": safe_url}}

    store = TaskStore(tmp_path / "tasks.db")
    task = store.create_task(
        MediaCapability.VIDEO_I2VA,
        "sensitive-query-value",
        {"provider": "runninghub"},
        {"prompt": "move"},
    )
    attempt = store.start_attempt(task.id, "runninghub-main")
    with pytest.raises(TaskStoreConflictError, match="prohibited"):
        store.record_provider_status(
            attempt.id,
            "running",
            {"reference_summary": {"source": url}},
        )


def test_safe_query_is_preserved_and_accepted_by_task_store(tmp_path) -> None:
    url = "https://cdn.example/video.mp4?q=forest&next=scene-2"
    assert sanitize_diagnostics(
        {"reference_summary": {"source": url}}
    ) == {"reference_summary": {"source": url}}

    store = TaskStore(tmp_path / "tasks.db")
    task = store.create_task(
        MediaCapability.VIDEO_I2VA,
        "safe-query-value",
        {"provider": "runninghub"},
        {"prompt": "move"},
    )
    attempt = store.start_attempt(task.id, "runninghub-main")
    store.record_provider_status(
        attempt.id,
        "running",
        {"reference_summary": {"source": url}},
    )


@pytest.mark.parametrize(
    "key",
    [
        "Bearer actual-secret",
        "Bearer opaque-value",
        "Authorization: Basic actual-secret",
    ],
)
def test_sensitive_mapping_key_text_is_not_copied_and_task_store_rejects(
    key: str,
    tmp_path,
) -> None:
    raw = {"reference_summary": {key: 1, "count": 2}}
    diagnostics = sanitize_diagnostics(raw)
    assert diagnostics == {
        "reference_summary": {"[REDACTED]": "[REDACTED]", "count": 2}
    }
    assert key not in repr(diagnostics)

    store = TaskStore(tmp_path / "tasks.db")
    task = store.create_task(
        MediaCapability.VIDEO_I2VA,
        "sensitive-mapping-key",
        {"provider": "runninghub"},
        {"prompt": "move"},
    )
    attempt = store.start_attempt(task.id, "runninghub-main")
    with pytest.raises(TaskStoreConflictError, match="prohibited"):
        store.record_provider_status(attempt.id, "running", raw)
