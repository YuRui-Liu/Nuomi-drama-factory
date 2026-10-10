from types import SimpleNamespace

import pytest

from novelvideo.task_backend.runners import freezone_video_director as runner


@pytest.mark.asyncio
async def test_runner_preserves_director_failure_reason(monkeypatch):
    async def resume(attempt_id):
        return {"stage": "failed", "failed_stage": "uploading",
                "error": "RunningHub 网络或服务暂时不可用，请稍后重试。"}
    monkeypatch.setattr(runner, "DirectorService", lambda ctx: SimpleNamespace(resume=resume))
    with pytest.raises(RuntimeError, match="RunningHub 网络或服务暂时不可用"):
        await runner._run({"payload": {"attempt_id": "attempt"}}, None)
