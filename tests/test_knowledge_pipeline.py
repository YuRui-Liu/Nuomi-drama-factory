from __future__ import annotations

import json
from pathlib import Path

import pytest

from novelvideo.knowledge_pipeline import (
    COGNEE_LEGACY,
    KNOWLEDGE_PIPELINE_STRUCTURED,
    STATUS_LEGACY_READY,
    STATUS_STRUCTURED_FAILED,
    STATUS_STRUCTURED_PENDING,
    STATUS_STRUCTURED_READY,
    STATUS_STRUCTURED_RUNNING,
    KnowledgePipelineLocked,
    KnowledgePipelineConfigurationError,
    KnowledgePipelineTransitionError,
    knowledge_pipeline_state_from_state_dir,
    switch_to_cognee_legacy,
    transition_structured_pipeline,
)


def _write_config(state_dir: Path, config: dict) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "project_config.json").write_text(json.dumps(config), encoding="utf-8")


def test_missing_pipeline_is_legacy_without_read_side_write(tmp_path: Path) -> None:
    config_path = tmp_path / "project_config.json"
    state = knowledge_pipeline_state_from_state_dir(tmp_path)
    assert state.pipeline == COGNEE_LEGACY
    assert state.status == STATUS_LEGACY_READY
    assert not config_path.exists()


def test_corrupt_project_config_never_silently_falls_back_to_legacy(
    tmp_path: Path,
) -> None:
    (tmp_path / "project_config.json").write_text("{broken", encoding="utf-8")

    with pytest.raises(KnowledgePipelineConfigurationError, match="Invalid project"):
        knowledge_pipeline_state_from_state_dir(tmp_path)


def test_explicit_structured_without_status_is_pending_compatibly(tmp_path: Path) -> None:
    _write_config(tmp_path, {"knowledge_pipeline": KNOWLEDGE_PIPELINE_STRUCTURED})
    state = knowledge_pipeline_state_from_state_dir(tmp_path)
    assert state.pipeline == KNOWLEDGE_PIPELINE_STRUCTURED
    assert state.status == STATUS_STRUCTURED_PENDING


def test_structured_transition_persists_run_identity_and_clears_error(tmp_path: Path) -> None:
    _write_config(tmp_path, {
        "knowledge_pipeline": KNOWLEDGE_PIPELINE_STRUCTURED,
        "knowledge_pipeline_status": STATUS_STRUCTURED_PENDING,
        "knowledge_pipeline_error": "old",
    })
    identity = {
        "source_sha256": "a" * 64,
        "schema_version": "structured_v1",
        "pipeline_version": "1",
    }
    running = transition_structured_pipeline(
        tmp_path, STATUS_STRUCTURED_RUNNING,
        expected_status=STATUS_STRUCTURED_PENDING, run_identity=identity,
    )
    ready = transition_structured_pipeline(
        tmp_path, STATUS_STRUCTURED_READY,
        expected_status=STATUS_STRUCTURED_RUNNING,
    )
    assert running.run_identity == identity
    assert running.error is None
    assert ready.status == STATUS_STRUCTURED_READY
    raw = json.loads((tmp_path / "project_config.json").read_text(encoding="utf-8"))
    assert raw["knowledge_pipeline_run_identity"] == identity
    assert "knowledge_pipeline_error" not in raw


def test_failed_pipeline_can_retry_but_never_automatically_falls_back(tmp_path: Path) -> None:
    _write_config(tmp_path, {
        "knowledge_pipeline": KNOWLEDGE_PIPELINE_STRUCTURED,
        "knowledge_pipeline_status": STATUS_STRUCTURED_FAILED,
    })
    state = transition_structured_pipeline(tmp_path, STATUS_STRUCTURED_RUNNING)
    assert state.pipeline == KNOWLEDGE_PIPELINE_STRUCTURED
    assert state.status == STATUS_STRUCTURED_RUNNING


def test_illegal_transition_leaves_config_unchanged(tmp_path: Path) -> None:
    original = {
        "knowledge_pipeline": KNOWLEDGE_PIPELINE_STRUCTURED,
        "knowledge_pipeline_status": STATUS_STRUCTURED_READY,
    }
    _write_config(tmp_path, original)
    with pytest.raises(KnowledgePipelineTransitionError):
        transition_structured_pipeline(tmp_path, STATUS_STRUCTURED_PENDING)
    assert json.loads((tmp_path / "project_config.json").read_text(encoding="utf-8")) == original


def test_ready_pipeline_requires_a_different_identity_for_new_run(tmp_path: Path) -> None:
    old_identity = {
        "source_sha256": "a" * 64,
        "schema_version": "structured_v1",
        "pipeline_version": "1",
    }
    new_identity = {**old_identity, "pipeline_version": "2"}
    _write_config(tmp_path, {
        "knowledge_pipeline": KNOWLEDGE_PIPELINE_STRUCTURED,
        "knowledge_pipeline_status": STATUS_STRUCTURED_READY,
        "knowledge_pipeline_run_identity": old_identity,
    })

    with pytest.raises(KnowledgePipelineTransitionError):
        transition_structured_pipeline(
            tmp_path,
            STATUS_STRUCTURED_RUNNING,
            run_identity=old_identity,
        )
    with pytest.raises(KnowledgePipelineTransitionError):
        transition_structured_pipeline(
            tmp_path,
            STATUS_STRUCTURED_RUNNING,
        )

    running = transition_structured_pipeline(
        tmp_path,
        STATUS_STRUCTURED_RUNNING,
        expected_status=STATUS_STRUCTURED_READY,
        run_identity=new_identity,
    )
    assert running.status == STATUS_STRUCTURED_RUNNING
    assert running.run_identity == new_identity


def test_attempt_token_allows_same_identity_retry_and_rejects_stale_completion(
    tmp_path: Path,
) -> None:
    identity = {
        "source_sha256": "a" * 64,
        "schema_version": "structured_v1",
        "pipeline_version": "1",
    }
    _write_config(
        tmp_path,
        {
            "knowledge_pipeline": KNOWLEDGE_PIPELINE_STRUCTURED,
            "knowledge_pipeline_status": STATUS_STRUCTURED_PENDING,
        },
    )
    transition_structured_pipeline(
        tmp_path,
        STATUS_STRUCTURED_RUNNING,
        run_identity=identity,
        attempt_id="attempt-old",
    )
    transition_structured_pipeline(
        tmp_path,
        STATUS_STRUCTURED_READY,
        expected_status=STATUS_STRUCTURED_RUNNING,
        attempt_id="attempt-old",
    )

    running = transition_structured_pipeline(
        tmp_path,
        STATUS_STRUCTURED_RUNNING,
        expected_status=STATUS_STRUCTURED_READY,
        run_identity=identity,
        attempt_id="attempt-new",
    )

    assert running.status == STATUS_STRUCTURED_RUNNING
    assert running.run_identity == identity
    assert running.attempt_id == "attempt-new"
    with pytest.raises(KnowledgePipelineTransitionError, match="attempt"):
        transition_structured_pipeline(
            tmp_path,
            STATUS_STRUCTURED_FAILED,
            expected_status=STATUS_STRUCTURED_RUNNING,
            attempt_id="attempt-old",
            error="late failure",
        )
    assert knowledge_pipeline_state_from_state_dir(tmp_path).attempt_id == "attempt-new"


@pytest.mark.parametrize("formal_asset_count", [None, 1])
def test_legacy_switch_fails_closed_when_assets_unknown_or_present(
    tmp_path: Path, formal_asset_count: int | None,
) -> None:
    _write_config(tmp_path, {
        "knowledge_pipeline": KNOWLEDGE_PIPELINE_STRUCTURED,
        "knowledge_pipeline_status": STATUS_STRUCTURED_FAILED,
    })
    with pytest.raises(KnowledgePipelineLocked):
        switch_to_cognee_legacy(tmp_path, formal_asset_count=formal_asset_count)
    assert knowledge_pipeline_state_from_state_dir(tmp_path).pipeline == KNOWLEDGE_PIPELINE_STRUCTURED


def test_pending_empty_project_can_explicitly_switch_to_legacy(tmp_path: Path) -> None:
    _write_config(tmp_path, {
        "knowledge_pipeline": KNOWLEDGE_PIPELINE_STRUCTURED,
        "knowledge_pipeline_status": STATUS_STRUCTURED_PENDING,
    })
    state = switch_to_cognee_legacy(tmp_path, formal_asset_count=0)
    assert state.pipeline == COGNEE_LEGACY
    assert state.status == STATUS_LEGACY_READY
    raw = json.loads((tmp_path / "project_config.json").read_text(encoding="utf-8"))
    assert raw["knowledge_pipeline"] == COGNEE_LEGACY
    assert raw["knowledge_pipeline_status"] == STATUS_LEGACY_READY


def test_project_config_update_uses_cross_platform_file_lock(
    tmp_path: Path, monkeypatch,
) -> None:
    import novelvideo.project_config as project_config

    calls: list[str] = []

    class FakeLock:
        def __init__(self, *_args, **_kwargs) -> None:
            calls.append("init")

        def __enter__(self):
            calls.append("enter")
            return self

        def __exit__(self, *_args) -> None:
            calls.append("exit")

    monkeypatch.setattr(
        project_config,
        "portalocker",
        type("FakePortalocker", (), {"Lock": FakeLock}),
        raising=False,
    )
    project_config.update_project_config_file_in_state_dir(
        tmp_path, lambda config: config.update({"lock_probe": True})
    )
    assert calls == ["init", "enter", "exit"]
    assert project_config.load_project_config_file_from_state_dir(tmp_path)["lock_probe"] is True
