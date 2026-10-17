from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
async def director_context(tmp_path):
    from novelvideo.project_context import ProjectContext
    from novelvideo.script_creation.store import DocumentStore
    from novelvideo.sqlite_store import SQLiteStore

    output_dir = tmp_path / "output"
    state_dir = tmp_path / "state"
    runtime_dir = tmp_path / "runtime"
    output_dir.mkdir()
    state_dir.mkdir()
    runtime_dir.mkdir()
    sqlite = SQLiteStore("owner/project", str(output_dir), str(state_dir))
    await sqlite.initialize()
    documents = DocumentStore(sqlite.db_path)
    await documents.initialize()
    script = await documents.create(
        kind="episode_script",
        title="第一集",
        episode_number=1,
        markdown="1-1 仓库 夜\n甲：你来了。",
        client_mutation_id="create-script",
    )
    await documents.adopt_revision(
        script.id,
        revision_id=script.current_revision_id,
        client_mutation_id="adopt-script",
    )
    script = await documents.get(script.id)
    ctx = ProjectContext(
        project_id="project",
        project_name="project",
        owner_type="user",
        owner_id="owner",
        owner_username="owner",
        requester_user_id="owner",
        requester_username="owner",
        requester_principals=(),
        effective_role="owner",
        home_node_id="local",
        output_dir=Path(output_dir),
        state_dir=Path(state_dir),
        runtime_dir=Path(runtime_dir),
        is_home_node=True,
    )
    try:
        yield ctx, script
    finally:
        await sqlite.close()
