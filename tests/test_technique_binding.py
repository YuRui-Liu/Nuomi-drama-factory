"""C 切片：手法/案例绑定。

以真实的 experience.search_experience 返回结构为准：
{"items": [{"id", "summary", "applicability", "sources", "compatible_technique_ids"}],
 "unavailable": bool}。案例 ID 在 id 字段，来源在 sources 列表，
compatible_technique_ids 是手法 id 字符串列表；手法版本需从本地目录解析。
"""

from types import SimpleNamespace

import pytest

from novelvideo.technique_library import binding


def _card(card_id, version="1.0.0", status="active"):
    return SimpleNamespace(id=card_id, version=version, status=status)


def _found(*, compatible, unavailable=False):
    return {
        "items": [
            {
                "id": "h3-0123456789abcdef",
                "summary": "克制反应的短案例",
                "applicability": {
                    "requested_mode": "ref_plus_first",
                    "requested_duration_seconds": 6,
                    "source_metadata": {"mode": "i2v", "duration": "6.1"},
                    "use_cases": ["performance"],
                },
                "sources": [
                    {
                        "id": "upstream-1",
                        "link": "https://example.com/case",
                        "provenance": "author",
                    }
                ],
                "compatible_technique_ids": list(compatible),
            }
        ],
        "unavailable": unavailable,
    }


def test_only_compatible_ids(monkeypatch):
    calls = []

    def fake_search(query, mode, duration_seconds, limit=5):
        calls.append((query, mode, duration_seconds, limit))
        return _found(compatible=["impact-cut"])

    monkeypatch.setattr(binding, "search_experience", fake_search)
    monkeypatch.setattr(
        binding,
        "list_techniques",
        lambda: (_card("slow-push-in"), _card("impact-cut")),
    )

    ref, case = binding.bind_experience(
        intent="克制的惊讶", mode="ref_plus_first", duration=6, user_technique=None
    )

    assert calls == [("克制的惊讶", "ref_plus_first", 6, 5)]
    assert ref == {"id": "impact-cut", "version": "1.0.0"}
    assert case == {
        "case_id": "h3-0123456789abcdef",
        "source_id": "upstream-1",
        "provenance": "author",
    }


def test_bound_reference_fits_segment_contract(monkeypatch):
    from novelvideo.freezone.video_director.segment_planner import SegmentDraft

    monkeypatch.setattr(
        binding, "search_experience", lambda *args, **kwargs: _found(compatible=["impact-cut"])
    )
    monkeypatch.setattr(binding, "list_techniques", lambda: (_card("impact-cut"),))

    ref, _ = binding.bind_experience(
        intent="速度感", mode="ref_plus_first", duration=6, user_technique=None
    )
    draft = SegmentDraft(segment_id="G01-S01", duration_seconds=6.0, technique=ref)
    assert draft.technique.version == "1.0.0"


def test_user_technique_conflict(monkeypatch):
    monkeypatch.setattr(
        binding, "search_experience", lambda *args, **kwargs: _found(compatible=["impact-cut"])
    )
    monkeypatch.setattr(binding, "list_techniques", lambda: (_card("impact-cut"),))

    with pytest.raises(ValueError, match="technique_conflict"):
        binding.bind_experience(
            intent="速度感",
            mode="ref_plus_first",
            duration=6,
            user_technique={"id": "slow-push-in", "version": "1.0.0"},
        )


def test_user_technique_kept_when_compatible(monkeypatch):
    monkeypatch.setattr(
        binding, "search_experience", lambda *args, **kwargs: _found(compatible=["impact-cut"])
    )

    ref, case = binding.bind_experience(
        intent="速度感",
        mode="ref_plus_first",
        duration=6,
        user_technique={"id": "impact-cut", "version": "1.0.0"},
    )
    assert ref == {"id": "impact-cut", "version": "1.0.0"}
    assert case["case_id"] == "h3-0123456789abcdef"


def test_no_case_does_not_block(monkeypatch):
    monkeypatch.setattr(
        binding, "search_experience", lambda *args, **kwargs: {"items": [], "unavailable": False}
    )

    assert binding.bind_experience(
        intent="无关意图", mode="ref_plus_first", duration=6, user_technique=None
    ) == (None, None)


def test_unavailable_catalog_is_not_a_silent_index(monkeypatch):
    monkeypatch.setattr(
        binding, "search_experience", lambda *args, **kwargs: {"items": [], "unavailable": True}
    )

    assert binding.bind_experience(
        intent="无关意图", mode="ref_plus_first", duration=6, user_technique=None
    ) == (None, None)
    with pytest.raises(ValueError, match="technique_unavailable"):
        binding.bind_experience(
            intent="无关意图",
            mode="ref_plus_first",
            duration=6,
            user_technique={"id": "impact-cut", "version": "1.0.0"},
        )


def test_case_without_compatible_technique_still_binds_case(monkeypatch):
    monkeypatch.setattr(
        binding, "search_experience", lambda *args, **kwargs: _found(compatible=[])
    )

    ref, case = binding.bind_experience(
        intent="克制的惊讶", mode="ref_plus_first", duration=6, user_technique=None
    )
    assert ref is None
    assert case["case_id"] == "h3-0123456789abcdef"
