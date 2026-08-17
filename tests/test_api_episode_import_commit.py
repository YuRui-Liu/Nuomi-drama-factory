from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from novelvideo.api.schemas import EpisodeImportCommitRequest
from novelvideo.episode_source_store import (
    EpisodeImportPreviewNotFound,
    EpisodeSourceRevisionConflict,
)
from novelvideo.episode_sources import build_episode_candidate


class CommitStore:
    def __init__(self, error=None):
        self.error = error

    async def get_preview(self, preview_id):
        if self.error:
            raise self.error
        return SimpleNamespace(
            id=preview_id,
            base_revision=4,
            items=(build_episode_candidate("E01.md", "第1集\n新"),),
        )

    async def current_revision(self):
        return 4

    async def list_sources(self):
        return [SimpleNamespace(episode_number=1, source_revision=2)]


@pytest.mark.asyncio
async def test_commit_requires_explicit_resolution_and_snapshots_payload(monkeypatch):
    from novelvideo.api.routes import episode_imports

    store = CommitStore()
    queued_payload = {}
    monkeypatch.setattr(episode_imports, "_resolve_store", lambda *a, **k: _async(store))
    monkeypatch.setattr(episode_imports, "resolve_project_scope", _scope)

    class Backend:
        async def enqueue_project_task(self, ctx, **kwargs):
            queued_payload.update(kwargs["payload"])
            return SimpleNamespace(
                task_state=SimpleNamespace(task_id="task-1"), backend="inline", queue="default"
            )

    monkeypatch.setattr(episode_imports, "get_task_backend", lambda: Backend())

    with pytest.raises(HTTPException) as missing:
        await episode_imports.commit_episode_imports(
            "project-1", EpisodeImportCommitRequest(preview_id="p", expected_revision=4),
            user={"username": "alice"},
        )
    assert missing.value.status_code == 409
    assert missing.value.detail["code"] == "EPISODE_IMPORT_CONFLICT_UNRESOLVED"

    response = await episode_imports.commit_episode_imports(
        "project-1",
        EpisodeImportCommitRequest(
            preview_id="p", expected_revision=4,
            resolutions=[{"file_id": "E01.md", "episode_number": 1, "action": "overwrite"}],
        ),
        user={"username": "alice"},
    )
    assert response["task_id"] == "task-1"
    assert queued_payload["expected_revision"] == 4
    assert queued_payload["target_revision"] == 5
    assert queued_payload["items"][0]["content_hash"].startswith("sha256:")
    assert queued_payload["snapshot"]["preview_id"] == "p"
    assert queued_payload["snapshot"]["items"][0]["source_filename"] == "E01.md"


@pytest.mark.asyncio
async def test_commit_blocks_unconfirmed_fallback_legacy_project(monkeypatch):
    from novelvideo.api.routes import episode_imports

    store = CommitStore()
    monkeypatch.setattr(episode_imports, "_resolve_store", lambda *a, **k: _async(store))
    monkeypatch.setattr(
        episode_imports,
        "_ensure_migration",
        lambda *a, **k: _async(SimpleNamespace(status="confirmation_required")),
    )

    with pytest.raises(HTTPException) as caught:
        await episode_imports.commit_episode_imports(
            "project-1",
            EpisodeImportCommitRequest(preview_id="p", expected_revision=4),
            user={"username": "alice"},
        )
    assert caught.value.status_code == 409
    assert caught.value.detail["code"] == "EPISODE_IMPORT_LEGACY_CONFIRMATION_REQUIRED"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "code"),
    [(EpisodeImportPreviewNotFound("p"), "EPISODE_IMPORT_PREVIEW_STALE"),
     (EpisodeSourceRevisionConflict("changed"), "EPISODE_IMPORT_REVISION_CONFLICT")],
)
async def test_commit_maps_stale_and_revision_conflict_to_stable_409(monkeypatch, error, code):
    from novelvideo.api.routes import episode_imports

    monkeypatch.setattr(
        episode_imports, "_resolve_store", lambda *a, **k: _raise_async(error)
    )
    with pytest.raises(HTTPException) as caught:
        await episode_imports.commit_episode_imports(
            "project-1", EpisodeImportCommitRequest(preview_id="p", expected_revision=4),
            user={"username": "alice"},
        )
    assert caught.value.status_code == 409
    assert caught.value.detail["code"] == code


async def _async(value):
    return value


async def _raise_async(error):
    raise error


async def _scope(*args, **kwargs):
    return SimpleNamespace(ctx=SimpleNamespace(project_id="project-1"))
