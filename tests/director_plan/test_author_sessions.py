from uuid import uuid4

import pytest


def store(tmp_path):
    from novelvideo.director_plan.author_sessions import AuthorSessionStore
    return AuthorSessionStore(tmp_path, project_id="project-a", task_kind="director")


def test_binding_survives_restart_and_chain_isolation(tmp_path):
    first = store(tmp_path).bind("chain-a", route={"model": "test"})
    session = str(uuid4())
    store(tmp_path).record_session(first, session)
    assert store(tmp_path).bind("chain-a", route={"model": "test"}).session_id == session
    assert store(tmp_path).bind("chain-b", route={"model": "test"}).session_id is None


def test_route_change_requires_explicit_rebuild(tmp_path):
    store(tmp_path).bind("chain-a", route={"model": "first"})
    with pytest.raises(ValueError, match="scope_or_route"):
        store(tmp_path).bind("chain-a", route={"model": "second"})


def test_record_session_rejects_replacement_and_wrong_scope(tmp_path):
    from novelvideo.director_plan.author_sessions import AuthorSessionStore
    binding = store(tmp_path).bind("chain-a", route={})
    store(tmp_path).record_session(binding, str(uuid4()))
    with pytest.raises(ValueError, match="session_mismatch"):
        store(tmp_path).record_session(binding, str(uuid4()))
    other = AuthorSessionStore(tmp_path, project_id="project-b", task_kind="director")
    with pytest.raises(ValueError, match="scope"):
        other.record_session(binding, str(uuid4()))


def test_corrupt_binding_never_silently_recreates(tmp_path):
    binding = store(tmp_path).bind("chain-a", route={})
    binding_path = store(tmp_path).session_dir(binding) / "binding.json"
    binding_path.write_text("broken")
    with pytest.raises(ValueError):
        store(tmp_path).bind("chain-a", route={})


def test_opaque_chain_cannot_escape_project(tmp_path):
    binding = store(tmp_path).bind("../../elsewhere", route={})
    assert store(tmp_path).session_dir(binding).is_relative_to(tmp_path)


def test_same_chain_lock_rejects_concurrent_revision(tmp_path):
    binding = store(tmp_path).bind("chain-a", route={})
    with store(tmp_path).claim(binding):
        with pytest.raises(ValueError, match="chain_busy"):
            with store(tmp_path).claim(binding):
                pass


@pytest.mark.asyncio
async def test_planner_uses_one_author_wrapper_for_plan_and_repair(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from novelvideo.director_plan.author_sessions import AuthorSessionStore
    from novelvideo.director_plan.planner import DirectorPlanner, GroupRepairInput
    from tests.director_plan.test_planner import episode, group

    calls = []
    draft = {"groups": [group("g1", 1, "s1").model_dump()]}

    class Runtime:
        snapshot = SimpleNamespace(runtime="codex", model="test")
        async def run_structured(self, **kwargs):
            calls.append(kwargs)
            return draft

    base, author = Runtime(), Runtime()
    bindings = []
    def wrap(self, runtime, binding):
        assert runtime is base
        bindings.append(binding)
        return author
    monkeypatch.setattr(AuthorSessionStore, "wrap_runtime", wrap)
    planner = DirectorPlanner(agent=SimpleNamespace(model_name="test"),
                              author_project_dir=tmp_path, author_project_id="project-a")
    planner._runtime = base
    source = episode().model_copy(update={"author_chain_id": "chain-a"})
    result = await planner.plan_episode(source)
    await planner.repair_group(GroupRepairInput(episode=source, failed_group=result.groups[0],
                                                relevant_source_spans=source.source_spans, issues=()))
    assert len(bindings) == 1
    assert len(calls) == 2
    assert planner._runtime is base  # Independent QC can use the untouched route.
    assert planner.author_chain_id == "chain-a"


def test_scoped_planner_guard_claims_whole_chain(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from novelvideo.director_plan.author_sessions import AuthorSessionStore
    from novelvideo.director_plan.planner import DirectorPlanner
    from tests.director_plan.test_planner import episode
    planner = DirectorPlanner(agent=SimpleNamespace(model_name="test"),
                              author_project_dir=tmp_path, author_project_id="p")
    planner._runtime = SimpleNamespace(snapshot=SimpleNamespace(runtime="codex", model="test"))
    monkeypatch.setattr(AuthorSessionStore, "wrap_runtime", lambda self, runtime, binding: runtime)
    source = episode().model_copy(update={"author_chain_id": "chain"})
    with planner.author_chain_guard(source):
        with pytest.raises(ValueError, match="chain_busy"):
            with planner.author_chain_guard(source):
                pass


def test_method_snapshot_change_cannot_reuse_chain(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from novelvideo.director_plan.author_sessions import AuthorSessionStore
    from novelvideo.director_plan.planner import DirectorPlanner
    from tests.director_plan.test_planner import episode
    monkeypatch.setattr(AuthorSessionStore, "wrap_runtime", lambda self, runtime, binding: runtime)
    def planner(version):
        value = DirectorPlanner(agent=SimpleNamespace(model_name="test"),
                                author_project_dir=tmp_path, author_project_id="p")
        value._runtime = SimpleNamespace(snapshot=SimpleNamespace(runtime="codex", model="test"),
            method=SimpleNamespace(model_dump=lambda **kwargs: {"revision": version}))
        return value
    source = episode().model_copy(update={"author_chain_id": "chain"})
    planner(1)._author_for(source)
    with pytest.raises(ValueError, match="scope_or_route"):
        planner(2)._author_for(source)
