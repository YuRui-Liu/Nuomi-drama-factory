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
    assert response["data"]["files"][0]["display_name"] == "E09.md"
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


@pytest.mark.asyncio
async def test_preview_flattens_bundle_and_preserves_source_identity(monkeypatch):
    from novelvideo.api.routes import episode_imports

    store = PreviewStore()
    monkeypatch.setattr(episode_imports, "_resolve_store", lambda *a, **k: _async(store))
    files = [
        UploadFile(
            file=io.BytesIO(
                "序言\n第1集 起点\n甲\nEpisode 2: Next\n乙".encode()
            ),
            filename="第一季.md",
        )
    ]

    response = await episode_imports.preview_episode_imports(
        "project-1", files=files, user={"username": "alice"}
    )

    items = response["data"]["files"]
    assert [item["episode_number"] for item in items] == [1, 2]
    assert [item["display_name"] for item in items] == [
        "第一季.md · 第 1 集",
        "第一季.md · 第 2 集",
    ]
    assert [item["filename"] for item in items] == ["第一季.md", "第一季.md"]
    assert len({item["file_id"] for item in items}) == 2
    assert [item.source_filename for item in store.saved["items"]] == [
        "第一季.md",
        "第一季.md",
    ]


@pytest.mark.asyncio
async def test_preview_keeps_valid_candidates_when_one_split_episode_is_empty(
    monkeypatch,
):
    from novelvideo.api.routes import episode_imports

    store = PreviewStore()
    monkeypatch.setattr(episode_imports, "_resolve_store", lambda *a, **k: _async(store))
    files = [
        UploadFile(
            file=io.BytesIO("第1集 空集\n\n第2集 有内容\n正文".encode()),
            filename="合集.txt",
        ),
        UploadFile(
            file=io.BytesIO("第3集\n独立正文".encode()), filename="E03.md"
        ),
    ]

    response = await episode_imports.preview_episode_imports(
        "project-1", files=files, user={"username": "alice"}
    )

    items = response["data"]["files"]
    assert [item["status"] for item in items] == ["invalid", "conflict", "new"]
    assert items[0]["error"] == "分集标题后缺少正文"
    assert [item.episode_number for item in store.saved["items"]] == [2, 3]


@pytest.mark.asyncio
async def test_list_episode_imports_exposes_content_length_without_content(monkeypatch):
    from novelvideo.api.routes import episode_imports
    from novelvideo.episode_source_store import EpisodeSource

    class ListStore:
        async def current_revision(self):
            return 3

        async def list_sources(self):
            return [
                EpisodeSource(
                    episode_number=2,
                    title="Second episode",
                    content="第2集\n正文内容",
                    content_hash="sha256:test",
                    source_filename="E002.md",
                    source_revision=3,
                    downstream_stale=False,
                    imported_at="2026-08-30T00:00:00Z",
                    updated_at="2026-08-30T00:00:00Z",
                )
            ]

    store = ListStore()
    monkeypatch.setattr(episode_imports, "_resolve_store", lambda *a, **k: _async(store))

    response = await episode_imports.list_episode_imports(
        "project-1", user={"username": "alice"}
    )

    item = response["data"]["items"][0]
    assert item["char_count"] == len("第2集\n正文内容")
    assert "content" not in item


async def _async(value):
    return value
