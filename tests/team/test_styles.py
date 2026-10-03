import pytest
from fastapi import HTTPException


@pytest.mark.asyncio
async def test_team_style_generation_requires_project_editor(monkeypatch):
    from novelvideo.api.routes import styles
    from novelvideo.api.schemas import StylePreviewRequest
    monkeypatch.setenv("ST_EDITION", "team")

    async def scope(project, user, required_role):
        assert required_role == "editor"
        raise HTTPException(403, "read only")

    monkeypatch.setattr(styles, "resolve_project_scope", scope)
    with pytest.raises(HTTPException) as exc:
        await styles.preview_style("sample", StylePreviewRequest(project="shared"), {"username": "member", "role": "member"})
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_team_style_generation_requires_explicit_project(monkeypatch):
    from novelvideo.api.routes import styles
    from novelvideo.api.schemas import StylePreviewRequest
    monkeypatch.setenv("ST_EDITION", "team")
    with pytest.raises(HTTPException) as exc:
        await styles.preview_style("sample", StylePreviewRequest(), {"username": "member", "role": "member"})
    assert exc.value.status_code == 422
