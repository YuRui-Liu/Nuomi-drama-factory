from types import SimpleNamespace

import pytest


@pytest.mark.asyncio
async def test_upload_listing_only_returns_safe_document_metadata(monkeypatch, tmp_path):
    from novelvideo.api.routes import ingest
    project = tmp_path / "project"
    uploads = project / "uploads"
    uploads.mkdir(parents=True)
    (uploads / "novel.txt").write_text("story")
    (uploads / ".hidden.txt").write_text("private")
    (uploads / "image.png").write_bytes(b"image")
    secret = tmp_path / "secret.txt"
    secret.write_text("secret")
    (uploads / "link.txt").symlink_to(secret)

    async def resolve(project_id, user, *, required_role):
        assert project_id == "p1" and required_role == "viewer"
        return SimpleNamespace(project_dir=project)

    monkeypatch.setattr(ingest, "resolve_project_scope", resolve)
    result = await ingest.list_ingest_uploads("p1", {"username": "alice"})
    assert result["data"]["count"] == 1
    assert result["data"]["files"][0]["filename"] == "novel.txt"
    assert "path" not in result["data"]["files"][0]
