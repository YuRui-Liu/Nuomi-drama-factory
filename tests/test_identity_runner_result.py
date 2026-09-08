import pytest

from novelvideo.task_backend.runners.identity import (
    _build_identity_planner_result,
    _refresh_identity_caches,
)


def test_identity_runner_result_includes_auto_promoted_characters():
    result = _build_identity_planner_result(
        episode=1,
        new_count=2,
        resolved_count=3,
        identities=[
            {
                "character_name": "陆辰",
                "identity_id": "陆辰_默认",
                "identity_name": "默认",
                "appearance_details": "",
            }
        ],
        auto_promoted_characters=["陆辰"],
    )

    assert result == {
        "episode": 1,
        "new_count": 2,
        "resolved_count": 3,
        "identities": [
            {
                "character_name": "陆辰",
                "identity_id": "陆辰_默认",
                "identity_name": "默认",
                "appearance_details": "",
            }
        ],
        "auto_promoted_characters": ["陆辰"],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("refresh_failure", ["false", "exception"])
async def test_identity_runner_cache_refresh_failure_is_reported_as_pending(
    refresh_failure,
):
    class CogneeCache:
        async def load_graph_state(self):
            if refresh_failure == "false":
                return False
            raise RuntimeError("cache unavailable")

    assert await _refresh_identity_caches(CogneeCache()) is False


def test_identity_runner_result_reports_cache_refresh_pending():
    result = _build_identity_planner_result(
        episode=1,
        new_count=1,
        resolved_count=0,
        identities=[],
        auto_promoted_characters=[],
        cache_refresh_pending=True,
    )

    assert result["cache_refresh_pending"] is True
