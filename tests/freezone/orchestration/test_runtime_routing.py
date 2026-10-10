from types import SimpleNamespace

import pytest


def test_canvas_planner_uses_a_user_configurable_role():
    from novelvideo.task_backend.runners import canvas_planning
    from novelvideo.task_backend.registry import get_project_task_runner_registration
    assert get_project_task_runner_registration("freezone_canvas_plan").text_task_role == "script_creation"


@pytest.mark.asyncio
async def test_canvas_script_honors_current_text_runtime(monkeypatch):
    from novelvideo.freezone import text_node
    from novelvideo.text_task_runtime import runtime
    calls = []
    class Runtime:
        async def run_structured(self, **kwargs):
            calls.append(kwargs)
            return kwargs["output_type"](title="done", rows=[])
    monkeypatch.setattr(runtime, "current_text_task_runtime", lambda: Runtime())
    monkeypatch.setattr(text_node, "get_freezone_story_script_agent", lambda *a: pytest.fail("must not use direct model API"))
    result = await text_node.generate_freezone_story_script(source_text="完整剧本")
    assert result.title == "done"
    assert "完整剧本" in calls[0]["prompt"]
