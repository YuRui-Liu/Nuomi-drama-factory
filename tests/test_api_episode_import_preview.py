from __future__ import annotations

import io
from types import SimpleNamespace

import pytest
from fastapi import UploadFile


class PreviewStore:
    def __init__(self) -> None:
        self.saved = None

    async def current_revision(self):
        return 7

    async def list_sources(self):
        return [SimpleNamespace(episode_number=2, source_revision=3)]

    async def save_preview(self, **kwargs):
        self.saved = kwargs
        return SimpleNamespace(id="preview-1", expires_at="2099-01-01T00:00:00+00:00")


@pytest.mark.asyncio
async def test_preview_accepts_repeated_files_and_body_number_wins(monkeypatch):
    from novelvideo.api.routes import episode_imports

    store = PreviewStore()
    monkeypatch.setattr(episode_imports, "_resolve_store", lambda *a, **k: _async(store))
    files = [
        UploadFile(file=io.BytesIO("第2集\n正文".encode()), filename="E09.md"),
        UploadFile(file=io.BytesIO("第3集\n正文".encode()), filename="E03.txt"),
    ]

    response = await episode_imports.preview_episode_imports(
        "project-1", files=files, user={"username": "alice"}
    )

    assert response["ok"] is True
    assert response["data"]["base_revision"] == 7
    assert [item["episode_number"] for item in response["data"]["files"]] == [2, 3]
    assert response["data"]["files"][0]["number_source"] == "body"
    assert response["data"]["files"][0]["status"] == "conflict"
    assert response["data"]["files"][0]["file_id"] != "E09.md"
    assert response["data"]["files"][0]["filename"] == "E09.md"
    assert store.saved["base_revision"] == 7
    assert not hasattr(store, "commit_preview")


@pytest.mark.asyncio
async def test_preview_reports_manual_duplicate_and_unsupported(monkeypatch):
    from novelvideo.api.routes import episode_imports

    store = PreviewStore()
    monkeypatch.setattr(episode_imports, "_resolve_store", lambda *a, **k: _async(store))
    files = [
        UploadFile(file=io.BytesIO("没有集号".encode()), filename="extra.md"),
        UploadFile(file=io.BytesIO("第4集\n甲".encode()), filename="a.md"),
        UploadFile(file=io.BytesIO("第4集\n乙".encode()), filename="b.md"),
        UploadFile(file=io.BytesIO(b"pdf"), filename="bad.pdf"),
    ]

    response = await episode_imports.preview_episode_imports(
        "project-1", files=files, user={"username": "alice"}
    )

    assert [item["status"] for item in response["data"]["files"]] == [
        "needs_episode_number", "conflict", "conflict", "invalid"
    ]
    assert response["data"]["files"][-1]["error"] == "不支持的文件格式"
    assert "detail" not in response["data"]["files"][-1]
    assert len(store.saved["items"]) == 3


async def _async(value):
    return value
