import json
import sqlite3
from types import SimpleNamespace

import pytest

from novelvideo.director_plan.models import ValidationReport
from novelvideo.director_plan.store import DirectorPlanStore
from novelvideo.episode_source_store import EpisodeSourceStore
from novelvideo.episode_sources import build_episode_candidate
from novelvideo.sqlite_store import SQLiteStore
from tests.director_plan.test_migration import _plan, _shot
from tests.screenplay_semantics.test_models import beat, scene


@pytest.fixture
async def source_project(tmp_path):
    sqlite = SQLiteStore("audit/source", str(tmp_path / "project"), str(tmp_path / "state"))
    await sqlite.initialize()
    repository = EpisodeSourceStore(sqlite)
    await repository.upsert_sources(
        [build_episode_candidate("E01.md", "第1集\n1-1 走廊 夜 内\n阿远：开门。")], expected_revision=0,
    )
    source = (await repository.list_sources())[0]
    plan = _plan("old", (_shot("old-shot"),)).model_copy(update={
        "source_script_hash": source.content_hash, "validation_report": ValidationReport(passed=True),
    })
    store = DirectorPlanStore(tmp_path / "project")
    store.save(plan)
    try:
        yield tmp_path / "project", repository, store, plan
    finally:
        await sqlite.close()


async def _overwrite(repository):
    await repository.upsert_sources(
        [build_episode_candidate("E01.md", "第1集\n1-1 海边 日 外\n小明：海真蓝。")], expected_revision=1,
    )


def _mock_project_context(monkeypatch, root):
    from novelvideo.api import deps
    ctx = SimpleNamespace(
        output_dir=root, state_dir=root.parent / "state", runtime_dir=root.parent / "runtime",
        owner_username="audit", project_name="source", is_home_node=True,
    )
    async def resolve(**kwargs):
        return ctx
    monkeypatch.setattr(deps, "resolve_project_context", resolve)
    return ctx


@pytest.mark.asyncio
async def test_first_plan_read_upgrades_existing_source_database(source_project, monkeypatch):
    from novelvideo.api.routes import director_plans
    root, repository, store, plan = source_project
    store.activate(1, plan.revision_id)
    await _overwrite(repository)
    (root / ".episode-source-db.json").unlink()
    with sqlite3.connect(root.parent / "state" / "data.db") as db:
        db.execute("DROP TABLE episode_source_identity")
    _mock_project_context(monkeypatch, root)
    response = await director_plans.list_director_plans("project", 1, {})
    assert response["data"][0]["source_stale"] is True
    assert (root / ".episode-source-db.json").is_file()
    with pytest.raises(ValueError, match="SOURCE_VERSION_CONFLICT"):
        store.load_active(1)


@pytest.mark.asyncio
@pytest.mark.parametrize("database_kind", ["absent", "no_table", "empty_table"])
async def test_project_resolution_preserves_source_less_legacy(tmp_path, monkeypatch, database_kind):
    from novelvideo.api.deps import resolve_project_scope
    root = tmp_path / "project"
    root.mkdir()
    ctx = _mock_project_context(monkeypatch, root)
    database = ctx.state_dir / "data.db"
    if database_kind != "absent":
        ctx.state_dir.mkdir()
        with sqlite3.connect(database) as db:
            if database_kind == "empty_table":
                db.execute("CREATE TABLE episode_sources (episode_number INTEGER)")
    await resolve_project_scope("project", {})
    assert not (root / ".episode-source-db.json").exists()
    assert database.exists() is (database_kind != "absent")


@pytest.mark.asyncio
async def test_source_overwrite_blocks_old_active_materialization(source_project):
    from novelvideo.narrative_groups.service import load_materialized_groups
    root, repository, store, plan = source_project
    store.activate(1, plan.revision_id)
    load_materialized_groups(root, 1)
    await _overwrite(repository)
    with pytest.raises(ValueError, match="source|SOURCE"):
        load_materialized_groups(root, 1)
    assert store.list(1)[0].revision_id == plan.revision_id


@pytest.mark.asyncio
async def test_source_overwrite_rejects_old_review_activation(source_project):
    _root, repository, store, plan = source_project
    await _overwrite(repository)
    with pytest.raises(ValueError, match="source|SOURCE"):
        store.activate(1, plan.revision_id)


@pytest.mark.asyncio
async def test_source_overwrite_during_planning_rejects_publish(source_project):
    _root, repository, store, plan = source_project
    await _overwrite(repository)
    with pytest.raises(ValueError, match="source|SOURCE"):
        store.save(plan.model_copy(update={"revision_id": "late-result"}))


@pytest.mark.asyncio
async def test_source_locator_cannot_redirect_to_another_database(source_project, tmp_path):
    root, _repository, store, plan = source_project
    store.activate(1, plan.revision_id)
    locator = root / ".episode-source-db.json"
    payload = json.loads(locator.read_text())
    payload["db_path"] = str(tmp_path / "unregistered.db")
    locator.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="SOURCE_VERSION_UNAVAILABLE"):
        store.load_active(1)
    assert not (tmp_path / "unregistered.db").exists()


@pytest.mark.asyncio
async def test_semantic_activation_and_loading_reject_outdated_source(source_project):
    from novelvideo.screenplay_semantics.models import ScreenplaySemanticRevision, SemanticValidationReport
    from novelvideo.screenplay_semantics.store import ScreenplaySemanticStore
    root, repository, _store, plan = source_project
    semantic = ScreenplaySemanticRevision.new(
        episode=1, source_revision=1, source_hash=plan.source_script_hash,
        scenes=(scene(),), beats=(beat(),), validation_report=SemanticValidationReport(passed=True),
    )
    semantics = ScreenplaySemanticStore(root)
    semantics.save(semantic)
    semantics.activate(1, semantic.revision_id, expected_source_revision=1)
    await _overwrite(repository)
    assert semantics.load_active(1) is None
    with pytest.raises((ValueError, RuntimeError), match="source|SOURCE"):
        semantics.activate(1, semantic.revision_id, expected_source_revision=1)


@pytest.mark.asyncio
async def test_current_plan_can_replace_stale_active(source_project):
    _root, repository, store, old = source_project
    store.activate(1, old.revision_id)
    await _overwrite(repository)
    current = (await repository.list_sources())[0]
    replacement = old.model_copy(update={"revision_id": "current", "source_script_hash": current.content_hash})
    store.save(replacement)
    assert store.activate(1, replacement.revision_id).status == "active"
    assert store.load_active(1).revision_id == "current"


@pytest.mark.asyncio
async def test_semantic_revision_change_invalidates_director_plan(source_project):
    from novelvideo.screenplay_semantics.models import ScreenplaySemanticRevision, SemanticValidationReport
    from novelvideo.screenplay_semantics.store import ScreenplaySemanticStore
    root, _repository, store, plan = source_project
    semantics = ScreenplaySemanticStore(root)
    first = ScreenplaySemanticRevision.new(
        episode=1, source_revision=1, source_hash=plan.source_script_hash,
        scenes=(scene(),), beats=(beat(),), validation_report=SemanticValidationReport(passed=True),
    )
    semantics.save(first)
    semantics.activate(1, first.revision_id, expected_source_revision=1)
    bound = plan.model_copy(update={"revision_id": "bound", "semantic_revision_id": first.revision_id})
    store.save(bound)
    store.activate(1, bound.revision_id)
    second = first.model_copy(update={"revision_id": "new-semantics"})
    semantics.save(second)
    semantics.activate(1, second.revision_id, expected_source_revision=1)
    with pytest.raises(ValueError, match="SEMANTIC"):
        store.load_active(1)


@pytest.mark.asyncio
async def test_stale_plan_remains_listable_but_activation_api_returns_conflict(source_project, monkeypatch):
    from novelvideo.api.routes import director_plans
    from fastapi import HTTPException
    from types import SimpleNamespace
    root, repository, store, plan = source_project
    await _overwrite(repository)
    async def resolve(*args, **kwargs):
        return SimpleNamespace(output_dir=root)
    monkeypatch.setattr(director_plans, "_resolve", resolve)
    monkeypatch.setattr(director_plans, "_build_director_plan_store", lambda _: store)
    response = await director_plans.list_director_plans("project", 1, {})
    assert response["data"][0]["source_stale"] is True
    with pytest.raises(HTTPException) as exc:
        await director_plans.activate_director_plan("project", 1, plan.revision_id, {})
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_locator_rejects_another_registered_project_identity(source_project, tmp_path):
    root, _repository, store, plan = source_project
    store.activate(1, plan.revision_id)
    other_root = tmp_path / "other-project"
    other = SQLiteStore("audit/other", str(other_root), str(tmp_path / "other-state"))
    await other.initialize()
    try:
        await EpisodeSourceStore(other).current_revision()
        (root / ".episode-source-db.json").write_text((other_root / ".episode-source-db.json").read_text())
        with pytest.raises(ValueError, match="SOURCE_VERSION_UNAVAILABLE"):
            store.load_active(1)
    finally:
        await other.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["director", "semantics"])
async def test_planning_runner_rejects_source_change_before_return(source_project, monkeypatch, kind):
    from types import SimpleNamespace
    root, repository, _store, plan = source_project
    ctx = SimpleNamespace(project_id="project", output_dir=root)
    payload = {"project_id": "project", "episode": 1, "source_revision": 1}
    manager = SimpleNamespace(update_progress_for_project=lambda *args, **kwargs: None)
    if kind == "director":
        from novelvideo.task_backend.runners import director_plan as runner
        async def build_input(*args):
            return object()
        class Service:
            async def create_draft(self, *args, **kwargs):
                await _overwrite(repository)
                return plan
        monkeypatch.setattr(runner, "_build_director_plan_input", build_input)
        monkeypatch.setattr(runner, "_load_asset_migration_context", lambda *args: (None, ()))
        monkeypatch.setattr(runner, "_build_director_plan_service", lambda _: Service())
        execute = runner._run_director_plan
    else:
        from novelvideo.task_backend.runners import screenplay_semantics as runner
        async def build_repository(*args):
            return repository
        class Service:
            async def build(self, *args, **kwargs):
                await _overwrite(repository)
                return object()
        monkeypatch.setattr(runner, "_build_episode_source_store", build_repository)
        monkeypatch.setattr(runner, "_build_service", lambda _: Service())
        execute = runner._run_screenplay_semantics
    monkeypatch.setattr(runner, "get_task_manager", lambda: manager)
    with pytest.raises((ValueError, RuntimeError), match="SOURCE"):
        await execute({"payload": payload}, ctx)
