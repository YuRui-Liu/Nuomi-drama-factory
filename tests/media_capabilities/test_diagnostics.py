from __future__ import annotations

import pytest

from novelvideo.media_capabilities.diagnostics import sanitize_diagnostics
from novelvideo.media_capabilities.models import MediaCapability
from novelvideo.media_capabilities.task_store import TaskStore


def test_sanitize_diagnostics_keeps_only_public_model_fields() -> None:
    diagnostics = sanitize_diagnostics(
        {
            "provider": "runninghub",
            "channel": "production",
            "logical_model": "video.default",
            "resolved_model": "runninghub:minimax-h3",
            "mode": "i2va",
            "aspect": "9:16",
            "duration": 5,
            "quality": "1080p",
            "workflow_id": "123",
            "workflow_version": 7,
            "reference_summary": {"images": 1},
            "upstream_task_id": "upstream-1",
            "status": "running",
            "retries": 1,
            "fallback_reason": "none",
            "api_key": "must-not-escape",
            "queue": {"position": 2},
        }
    )
    assert set(diagnostics) == {
        "provider", "channel", "logical_model", "resolved_model", "mode",
        "aspect", "duration", "quality", "workflow_id", "workflow_version",
        "reference_summary", "upstream_task_id", "status", "retries",
        "fallback_reason",
    }
    assert "api_key" not in diagnostics
    assert "queue" not in diagnostics


def test_sanitize_diagnostics_redacts_credentials_signed_urls_and_absolute_paths() -> None:
    diagnostics = sanitize_diagnostics(
        {
            "provider": "Bearer top-secret",
            "reference_summary": {
                "source": "https://cdn.example/video.mp4?X-Amz-Signature=secret&expires=1",
                "audio": r"C:\\Users\\operator\\voice.wav",
                "nested_token": "secret",
            },
            "fallback_reason": "读取 /srv/private/input.png 失败，token=secret",
        }
    )
    encoded = repr(diagnostics)
    assert "top-secret" not in encoded
    assert "X-Amz-Signature" not in encoded
    assert "C:\\Users" not in encoded
    assert "/srv/private" not in encoded
    assert "token=secret" not in encoded
    assert diagnostics["reference_summary"]["source"] == "https://cdn.example/video.mp4"
    assert diagnostics["reference_summary"]["nested_token"] == "[REDACTED]"


def test_sanitize_diagnostics_normalizes_sensitive_nested_key_styles() -> None:
    diagnostics = sanitize_diagnostics(
        {
            "reference_summary": {
                "accessToken": "camel-secret",
                "clientSecret": "client-secret",
                "api-key": "kebab-secret",
                "auth.token": "dotted-secret",
                "safe_count": 2,
            }
        }
    )

    summary = diagnostics["reference_summary"]
    assert summary["safe_count"] == 2
    assert all(
        summary[key] == "[REDACTED]"
        for key in ("accessToken", "clientSecret", "api-key", "auth.token")
    )
    encoded = repr(diagnostics)
    for secret in ("camel-secret", "client-secret", "kebab-secret", "dotted-secret"):
        assert secret not in encoded


def test_sanitize_diagnostics_removes_sensitive_url_fragment() -> None:
    diagnostics = sanitize_diagnostics(
        {"reference_summary": {"source": "https://cdn.example/video.mp4#access_token=secret"}}
    )

    assert diagnostics["reference_summary"]["source"] == "https://cdn.example/video.mp4"


def test_sanitize_diagnostics_redacts_single_segment_posix_path_without_harming_slashes() -> None:
    diagnostics = sanitize_diagnostics(
        {
            "fallback_reason": "读取 /tmp 失败",
            "status": "I/O retry 1/3",
        }
    )

    assert diagnostics["fallback_reason"] == "[REDACTED]"
    assert diagnostics["status"] == "I/O retry 1/3"


def test_sanitize_diagnostics_removes_url_userinfo() -> None:
    diagnostics = sanitize_diagnostics(
        {
            "reference_summary": {
                "source": "https://user:supersecret@cdn.example/video.mp4"
            }
        }
    )

    assert diagnostics["reference_summary"]["source"] == "https://cdn.example/video.mp4"
    assert "user" not in repr(diagnostics)
    assert "supersecret" not in repr(diagnostics)


def test_sanitize_diagnostics_redacts_embedded_windows_unc_path() -> None:
    diagnostics = sanitize_diagnostics(
        {"fallback_reason": r"failed at \\server\share\input.png"}
    )

    assert diagnostics["fallback_reason"] == "[REDACTED]"


def test_sanitize_diagnostics_redacts_quoted_or_parenthesized_posix_path() -> None:
    diagnostics = sanitize_diagnostics(
        {
            "fallback_reason": "failed at '/tmp'",
            "status": "I/O retry 1/3 (safe)",
        }
    )

    assert diagnostics["fallback_reason"] == "[REDACTED]"
    assert diagnostics["status"] == "I/O retry 1/3 (safe)"


@pytest.mark.parametrize(
    "value",
    [
        "/数据/输入.png",
        "failed at //server/share/input.png",
        r"failed at \\server\share\input.png",
        r"failed at C:\Users\operator\input.png",
        "failed at '/tmp'",
        "failed at (/srv/data/input.png)",
        "copy /var/cache/media now",
        "failed source:/srv/private/input.png",
        "failed source=file:///srv/private/input.png",
    ],
)
def test_sanitize_diagnostics_redacts_cross_platform_absolute_path_tokens(
    value: str,
) -> None:
    assert sanitize_diagnostics({"fallback_reason": value})["fallback_reason"] == "[REDACTED]"


@pytest.mark.parametrize(
    "value",
    [
        "I/O retry 1/3",
        "ratio 9/16",
        "assets/input.png",
        "./assets/input.png",
        "../assets/input.png",
        "namespace:value",
        "time:12/30",
    ],
)
def test_sanitize_diagnostics_preserves_non_absolute_slash_text(value: str) -> None:
    assert sanitize_diagnostics({"status": value})["status"] == value


def test_task_store_exposes_existing_diagnostics_through_public_whitelist(tmp_path) -> None:
    store = TaskStore(tmp_path / "tasks.db")
    task = store.create_task(
        MediaCapability.VIDEO_I2VA,
        "diagnostics-test",
        {"provider": "runninghub"},
        {"prompt": "move"},
    )
    attempt = store.start_attempt(task.id, "runninghub-main")
    store.record_provider_status(
        attempt.id,
        "running",
        {
            "provider": "runninghub",
            "resolved_model": "runninghub:minimax-h3",
            "status": "running",
            "queue": {"position": 2},
        },
    )
    assert store.get_public_diagnostics(attempt.id) == {
        "provider": "runninghub",
        "resolved_model": "runninghub:minimax-h3",
        "status": "running",
    }
