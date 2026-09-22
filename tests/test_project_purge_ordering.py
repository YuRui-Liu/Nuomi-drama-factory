from pathlib import Path

import pytest

from novelvideo.ports.project import ProjectRecord
from novelvideo.project_context import ProjectContext


@pytest.mark.asyncio
async def test_purge_removes_project_files_before_registry_row(monkeypatch, tmp_path) -> None:
    from novelvideo.api.routes import projects as projects_route

    events: list[str] = []
    output_dir = tmp_path / "output"
    state_dir = tmp_path / "state"
    runtime_dir = tmp_path / "runtime"
    for path in (output_dir, state_dir, runtime_dir):
        path.mkdir()

    ctx = ProjectContext(
        project_id="project-1",
        project_name="demo",
        owner_type="user",
        owner_id="local",
        owner_username="alice",
        requester_user_id="local",
        requester_username="alice",
        requester_principals=(("user", "local"),),
        effective_role="owner",
        home_node_id="local",
        output_dir=output_dir,
        state_dir=state_dir,
        runtime_dir=runtime_dir,
        is_home_node=True,
    )
    record = ProjectRecord(
        id=ctx.project_id,
        owner_type=ctx.owner_type,
        owner_id=ctx.owner_id,
        owner_username=ctx.owner_username,
        name=ctx.project_name,
        home_node_id=ctx.home_node_id,
        output_dir=str(output_dir),
        state_dir=str(state_dir),
        runtime_dir=str(runtime_dir),
        status="deleted",
    )

    class Registry:
        async def get_project(self, project_id):
            assert project_id == ctx.project_id
            return record

        async def mark_project_purged(self, project_id):
            assert project_id == ctx.project_id
            events.append("registry")
            return ProjectRecord(**{**record.__dict__, "purged_at": "now"})

        async def delete_project_home(self, project_id):
            assert project_id == ctx.project_id

    async def resolve(**_kwargs):
        return ctx

    async def audit(**_kwargs):
        return None

    def remove(path: Path) -> None:
        events.append(f"remove:{path.name}")

    monkeypatch.setattr(projects_route, "resolve_project_context", resolve)
    monkeypatch.setattr(projects_route, "get_project_registry", Registry)
    monkeypatch.setattr(projects_route, "emit_project_audit", audit)
    monkeypatch.setattr(projects_route.shutil, "rmtree", remove)

    response = await projects_route.purge_project("demo", user={"id": "local"})

    assert response["ok"] is True
    assert events == ["remove:output", "remove:state", "remove:runtime", "registry"]
