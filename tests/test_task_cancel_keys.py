import pytest

from novelvideo.task_backend.cancel import cancel_key
from novelvideo.task_jobs import read_task_result, wait_for_task_terminal

pytestmark = pytest.mark.m07


def test_cancel_key_is_scoped_to_single_task_run():
    first = cancel_key(
        project_id="proj",
        task_type="stage_asset",
        episode=0,
        scope="stage_asset__abc",
        task_id="run-1",
    )
    second = cancel_key(
        project_id="proj",
        task_type="stage_asset",
        episode=0,
        scope="stage_asset__abc",
        task_id="run-2",
    )

    assert first != second
    assert first.endswith(":run-1")
    assert second.endswith(":run-2")


def test_cancelled_is_terminal_for_read_and_wait(monkeypatch):
    snapshot = {"status": "cancelled"}
    monkeypatch.setattr("novelvideo.task_jobs.get_task_snapshot", lambda **_kwargs: snapshot)
    fields = dict(task_type="render", username="u", project="p", episode=1)

    assert read_task_result(**fields, require_terminal=True) is snapshot
    assert wait_for_task_terminal(**fields, timeout_seconds=0) is snapshot
