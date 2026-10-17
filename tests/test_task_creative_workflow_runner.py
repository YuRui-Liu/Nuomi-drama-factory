from pathlib import Path
from types import SimpleNamespace

from novelvideo.script_creation.store import DocumentStore
from novelvideo.script_creation.workflow import CreativeWorkflowService
from novelvideo.task_backend.registry import get_project_task_runner_registration


async def test_registered_ideation_runner_persists_three_llm_candidates(tmp_path, monkeypatch):
    from novelvideo.task_backend.runners import creative_workflow

    store = DocumentStore(tmp_path / "data.db")
    await store.initialize()
    brief = await store.create(kind="brief", title="简报", markdown="都市悬疑，林夏寻找姐姐。",
                               client_mutation_id="runner-brief")
    service = CreativeWorkflowService(store)
    run = await service.create_ideation(
        brief_document_id=brief.id, brief_revision_id=brief.current_revision_id,
        client_mutation_id="runner-ideation", generate=True)

    class Runtime:
        snapshot = SimpleNamespace(runtime="model_api", model="configured-model")

        async def run_structured(self, *, prompt, output_type, **_):
            return output_type(candidates=[
                {"one_line": f"方向{i}：主角作出选择", "protagonist_conflict": f"主角{i}面对规则冲突",
                 "highlights": f"方向{i}的故事看点", "final_choice": f"主角{i}承担选择后果"}
                for i in range(1, 4)
            ])

    monkeypatch.setattr(creative_workflow, "current_text_task_runtime", lambda: Runtime())
    monkeypatch.setattr(creative_workflow, "get_task_manager", lambda: SimpleNamespace(
        update_progress_for_project=lambda *args, **kwargs: None))
    ctx = SimpleNamespace(project_id="project", state_dir=tmp_path)
    envelope = {
        "project_id": "project", "task_type": "creative_workflow", "episode": 0,
        "scope": f"ideation:{run['id']}", "__run_task_id": "ideation-task",
        "payload": {"project_id": "project", "operation": "generate_ideation",
                    "ideation_id": run["id"]},
    }

    result = await creative_workflow._run(envelope, ctx)

    saved = await service.get_ideation(run["id"])
    assert result["candidate_count"] == 3
    assert saved["status"] == "completed"
    assert len(saved["candidates"]) == 3
    assert get_project_task_runner_registration("creative_workflow").text_task_role == "script_creation"
    for candidate in saved["candidates"]:
        assert (await store.get(candidate["id"])).current_revision_id == candidate["revision_id"]
