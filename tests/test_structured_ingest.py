from __future__ import annotations

import json
from pathlib import Path

import pytest


DRAMA_TEXT = """第一集

1-1 林家客厅 日 内
人物：林默、林母
林默推开门。

1-2 巷口 夜 外
人物：林默
林默独自走过巷口。
"""

NARRATED_TEXT = """第一章 归来

林默回到故乡。

第二章 旧友

他在巷口遇见旧友。
"""


class _Store:
    def __init__(self, root: Path) -> None:
        self.state_dir = str(root / "state")
        self.project_dir = str(root / "project")
        Path(self.state_dir).mkdir(parents=True)
        Path(self.project_dir).mkdir(parents=True)
        self.saved: list[str] = []

    def save_novel_content(self, content: str) -> None:
        self.saved.append(content)
        (Path(self.project_dir) / "novel.txt").write_text(content, encoding="utf-8")


def test_scene_and_chapter_chunks_are_deterministic_and_quote_exact_source() -> None:
    from novelvideo.structured_ingest import chunk_source_text

    for text, template, expected_type in (
        (DRAMA_TEXT, "drama", "scene"),
        (NARRATED_TEXT, "narrated", "chapter"),
    ):
        first = chunk_source_text(text, template)
        second = chunk_source_text(text, template)
        assert first == second
        assert first
        assert all(chunk.section_type == expected_type for chunk in first)
        assert len({chunk.chunk_id for chunk in first}) == len(first)
        for chunk in first:
            assert text[chunk.source_start : chunk.source_end] == chunk.text


def test_run_identity_reuses_same_input_and_changes_with_versions() -> None:
    from novelvideo.structured_ingest import build_structured_run_plan

    first = build_structured_run_plan(
        NARRATED_TEXT,
        "narrated",
        schema_version="1",
        pipeline_version="structured_v1",
    )
    retry = build_structured_run_plan(
        NARRATED_TEXT,
        "narrated",
        schema_version="1",
        pipeline_version="structured_v1",
    )
    upgraded = build_structured_run_plan(
        NARRATED_TEXT,
        "narrated",
        schema_version="2",
        pipeline_version="structured_v1",
    )

    assert retry.run_id == first.run_id
    assert retry.identity == first.identity
    assert upgraded.run_id != first.run_id
    assert first.identity.as_dict() == {
        "source_sha256": first.identity.source_sha256,
        "schema_version": "1",
        "pipeline_version": "structured_v1",
    }


@pytest.mark.asyncio
async def test_ingest_reuses_manifest_emits_progress_and_never_calls_cognee(
    tmp_path: Path, monkeypatch
) -> None:
    import cognee

    from novelvideo.structured_ingest import ingest_source_text_structured

    for name in ("add", "cognify", "memify"):
        monkeypatch.setattr(
            cognee,
            name,
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError("structured ingest must not call Cognee")
            ),
            raising=False,
        )

    source = tmp_path / "source.txt"
    async def fake_build_publication(*_args, **_kwargs):
        return object()

    async def fake_publish(
        _store,
        _publication,
        *,
        run_id=None,
        before_commit=None,
    ):
        assert run_id
        await before_commit()
        return {"episodes": 0, "characters": 0, "scenes": 0}

    monkeypatch.setattr(
        "novelvideo.structured_publication.build_structured_publication_from_source",
        fake_build_publication,
    )
    monkeypatch.setattr(
        "novelvideo.structured_builders.publish_structured_publication",
        fake_publish,
    )
    source.write_text(NARRATED_TEXT, encoding="utf-8")
    store = _Store(tmp_path)
    transitions: list[tuple[str, dict]] = []
    progress: list[tuple[float, str]] = []

    def transition(_state_dir, status: str, **kwargs):
        transitions.append((status, kwargs))

    first = await ingest_source_text_structured(
        store,
        str(source),
        spine_template="narrated",
        on_progress=lambda value, message: progress.append((value, message)),
        transition=transition,
    )
    second = await ingest_source_text_structured(
        store,
        str(source),
        spine_template="narrated",
        transition=transition,
    )

    assert first["run_id"] == second["run_id"]
    assert first["reused"] is False
    assert second["reused"] is True
    assert store.saved == [NARRATED_TEXT, NARRATED_TEXT]
    assert [value for value, _ in progress] == sorted(value for value, _ in progress)
    assert progress[-1][0] == 1.0
    assert transitions[0][0] == "structured_running"
    assert transitions[-1][0] == "structured_ready"
    run_identity = transitions[0][1]["run_identity"]
    assert set(run_identity) == {
        "source_sha256",
        "schema_version",
        "pipeline_version",
    }
    manifest = Path(store.state_dir) / "structured_runs" / f"{first['run_id']}.json"
    persisted = json.loads(manifest.read_text(encoding="utf-8"))
    assert persisted["run_id"] == first["run_id"]
    assert persisted["chunks"]


@pytest.mark.asyncio
async def test_failed_ingest_marks_failed_without_replacing_success_marker(
    tmp_path: Path,
) -> None:
    from novelvideo.structured_ingest import ingest_source_text_structured

    source = tmp_path / "empty.txt"
    source.write_text("  \n", encoding="utf-8")
    store = _Store(tmp_path)
    marker = Path(store.project_dir) / "novel.txt"
    marker.write_text("上次正式内容", encoding="utf-8")
    transitions: list[tuple[str, dict]] = []

    def transition(_state_dir, status: str, **kwargs):
        transitions.append((status, kwargs))

    with pytest.raises(ValueError, match="内容为空"):
        await ingest_source_text_structured(
            store,
            str(source),
            spine_template="narrated",
            transition=transition,
        )

    assert marker.read_text(encoding="utf-8") == "上次正式内容"
    assert store.saved == []
    assert transitions[-1][0] == "structured_failed"
    assert "内容为空" in transitions[-1][1]["error"]
