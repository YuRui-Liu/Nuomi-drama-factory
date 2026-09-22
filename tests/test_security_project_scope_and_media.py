"""Regression coverage for non-path project scopes and untrusted media delivery."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from novelvideo import project_context
from novelvideo.api.routes import files
from novelvideo.ports.project import Principal, ProjectRecord


@pytest.fixture
def registered_project(monkeypatch, tmp_path):
    record = ProjectRecord(
        id="project-b", name="b", owner_type="user", owner_id="local",
        owner_username="local", home_node_id="local", output_dir=str(tmp_path),
        state_dir=str(tmp_path), runtime_dir=str(tmp_path), status="active",
    )
    registry = SimpleNamespace(
        get_project=AsyncMock(return_value=record),
        get_project_by_owner_name=AsyncMock(return_value=record),
    )
    access = SimpleNamespace(
        resolve_requester_principals=AsyncMock(return_value=[Principal("user", "local")]),
        effective_project_role=AsyncMock(return_value="owner"),
    )
    monkeypatch.setattr(project_context, "get_project_registry", lambda: registry)
    monkeypatch.setattr(project_context, "get_project_access", lambda: access)
    return record


@pytest.mark.parametrize("lookup", [{"project_id": "project-b"}, {"project_name": "b"}])
@pytest.mark.parametrize("scope", ["project", "home"])
async def test_agent_cannot_resolve_project_outside_active_scope(registered_project, lookup, scope):
    user = {
        "id": "local", "username": "local", "credential_kind": "agent_session",
        "scopes": ["projects:write"], "current_scope_kind": scope,
        "current_project_id": "project-a",
    }
    with pytest.raises(HTTPException) as exc:
        await project_context.resolve_project_context(user=user, required_role="editor", **lookup)
    assert exc.value.status_code == 403


@pytest.mark.parametrize("agent", [False, True])
async def test_browser_and_matching_agent_can_resolve_project(registered_project, agent):
    user = {"id": "local", "username": "local"}
    if agent:
        user.update(credential_kind="agent_session", current_scope_kind="project",
                    current_project_id="project-b")
    ctx = await project_context.resolve_project_context(user=user, project_id="project-b")
    assert ctx.project_id == "project-b"


@pytest.fixture
def media_client(monkeypatch, tmp_path):
    from novelvideo import config
    from novelvideo.utils import oss_client

    monkeypatch.setattr(config, "DOWNLOAD_VIA_OSS", False)
    monkeypatch.setattr(oss_client, "maybe_presign_static", lambda *args: None)
    app = FastAPI()

    @app.get("/media/{filename}")
    def media(filename: str, download: bool = False):
        return files._serve_or_redirect_to_oss(tmp_path / filename, as_download=download)

    return TestClient(app), tmp_path


@pytest.mark.parametrize("extension", ["html", "svg", "xml", "bin"])
def test_untrusted_documents_are_downloads_not_same_origin_pages(media_client, extension):
    client, root = media_client
    name = f"payload.{extension}"
    payload = b"<script>window.__uploaded_script_executed = true</script>"
    (root / name).write_bytes(payload)
    response = client.get(f"/media/{name}")
    assert response.status_code == 200
    assert response.content == payload
    assert response.headers["content-type"] == "application/octet-stream"
    assert response.headers["content-disposition"].startswith("attachment;")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "sandbox" in response.headers["content-security-policy"]


@pytest.mark.parametrize("extension,content_type", [("png", "image/png"), ("mp4", "video/mp4"), ("mp3", "audio/mpeg")])
def test_media_preview_still_supports_inline_ranges_and_explicit_download(media_client, extension, content_type):
    client, root = media_client
    name = f"asset.{extension}"
    (root / name).write_bytes(b"0123456789")
    response = client.get(f"/media/{name}", headers={"Range": "bytes=2-5"})
    assert response.status_code == 206
    assert response.content == b"2345"
    assert response.headers["content-type"] == content_type
    assert "content-disposition" not in response.headers
    response = client.get(f"/media/{name}?download=true")
    assert response.status_code == 200
    assert response.headers["content-disposition"].startswith("attachment;")


def test_unsafe_document_does_not_redirect_around_response_policy(media_client, monkeypatch):
    from novelvideo.utils import oss_client

    client, root = media_client
    (root / "payload.html").write_text("<script>alert(1)</script>")
    monkeypatch.setattr(oss_client, "maybe_presign_static", lambda *args: "https://media.invalid/payload.html")
    response = client.get("/media/payload.html", follow_redirects=False)
    assert response.status_code == 200
    assert response.headers["content-disposition"].startswith("attachment;")
