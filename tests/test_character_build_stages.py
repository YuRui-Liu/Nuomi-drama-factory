import asyncio
import json
from pathlib import Path

import pytest

from novelvideo import structured_extraction as extraction
from novelvideo.story_analysis import SourceChunk


def chunk(index, text="石九进门"):
    return SourceChunk(chunk_id=f"c{index}", chunk_index=index, section_type="scene",
                       section_label=f"片段 {index}", source_start=index * 10,
                       source_end=index * 10 + len(text), text=text)


def proposals():
    rows = [("长脸，下颌收窄", "左眉尾有断眉", "短发侧分"),
            ("方脸，宽下巴", "右眼下有小痣", "粗硬寸发"),
            ("菱形脸，高颧骨", "左嘴角有凹点", "细软长发")]
    return [dict(proposal_id=f"p{i}", title=f"方案{i}", face_shape=face, rationale="原文未指定骨相，提供可区分的创作演绎",
                 facial_features=[detail], hair_style=hair, distinctive_features=[detail],
                 identity_anchors=[face, detail, hair], asymmetry_detail=detail,
                 casting_decisions=[dict(decision_id=f"p{i}-creative", attribute="face_shape", value=face,
                    reason="原文未指定骨相，作为自由创作", basis="creative_choice")],
                 recommended=i == 0) for i, (face, detail, hair) in enumerate(rows)]


class Facts:
    def __init__(self):
        self.calls = []

    async def run(self, prompt):
        self.calls.append(prompt)
        return {"characters": [{"name": "石九", "evidence": [{"quote": "石九"}]}]}


class Designs:
    def __init__(self, reject_first=False):
        self.calls = []
        self.reject_first = reject_first

    async def run(self, prompt):
        self.calls.append(prompt)
        return {"design_proposals": [] if self.reject_first and len(self.calls) == 1 else proposals()}


def test_fact_schema_has_no_design_fields():
    schema = json.dumps(extraction.ChunkCharacterFacts.model_json_schema())
    assert "design_proposals" not in schema
    assert "identity_anchors" not in schema


@pytest.mark.asyncio
async def test_merge_before_design_and_retry_only_design():
    facts, designs, progress = Facts(), Designs(reject_first=True), []
    result = await extraction.extract_characters_from_chunks(
        [chunk(0), chunk(1)], agent=facts, design_agent=designs,
        on_progress=lambda value, message: progress.append((value, message)),
    )
    assert len(facts.calls) == 2
    assert len(designs.calls) == 2  # one unique character, one local repair
    assert len(result) == 1
    assert len(result[0].design_proposals) == 3
    assert "石九进门" not in designs.calls[1]
    assert [p[0] for p in progress] == sorted(p[0] for p in progress)
    assert any("2/2" in message for _, message in progress)


@pytest.mark.asyncio
async def test_checkpoint_reuses_facts_and_valid_design():
    cache = {}

    async def load(key):
        return cache.get(key, "")

    async def save(key, value):
        cache[key] = value

    facts, designs = Facts(), Designs()
    for _ in range(2):
        await extraction.extract_characters_from_chunks(
            [chunk(0)], agent=facts, design_agent=designs,
            load_checkpoint=load, save_checkpoint=save,
        )
    assert len(facts.calls) == 1
    assert len(designs.calls) == 1
    await extraction.extract_characters_from_chunks(
        [chunk(0, "石九出门")], agent=facts, design_agent=designs,
        load_checkpoint=load, save_checkpoint=save,
    )
    assert len(facts.calls) == 2


@pytest.mark.asyncio
async def test_fact_failure_cancels_other_inflight_chunks():
    stopped = asyncio.Event()

    class FailingFacts:
        async def run(self, prompt):
            if prompt == "石九进门":
                await asyncio.sleep(0.01)
                raise RuntimeError("provider failed")
            try:
                await asyncio.sleep(30)
            finally:
                stopped.set()

    with pytest.raises(RuntimeError, match="provider failed"):
        await extraction.extract_characters_from_chunks(
            [chunk(0), chunk(1, "石九出门")], agent=FailingFacts(), design_agent=Designs(),
        )
    assert stopped.is_set()


@pytest.mark.asyncio
async def test_six_chunks_only_design_unique_character_once():
    facts, designs = Facts(), Designs()
    await extraction.extract_characters_from_chunks(
        [chunk(i, "石九进门。" + "雨" * 10000) for i in range(6)],
        agent=facts, design_agent=designs,
    )
    assert len(facts.calls) == 6
    assert len(designs.calls) == 1


@pytest.mark.asyncio
async def test_sqlite_checkpoints_survive_builder_failure_and_retry(tmp_path, monkeypatch):
    from novelvideo.sqlite_store import SQLiteStore
    from novelvideo.structured_builders import build_characters_structured
    import novelvideo.character_design_stage as design_stage

    store = SQLiteStore("user/test", output_dir=str(tmp_path / "output"),
                        state_dir=str(tmp_path / "state"))
    await store.initialize()
    await store.load_graph_state()
    Path(store.project_dir).mkdir(parents=True, exist_ok=True)
    (Path(store.project_dir) / "novel.txt").write_text("石九进门", encoding="utf-8")
    facts = Facts()

    class InterruptedDesigns(Designs):
        async def run(self, prompt):
            self.calls.append(prompt)
            if len(self.calls) == 1:
                raise RuntimeError("design interrupted")
            return {"design_proposals": proposals()}

    designs = InterruptedDesigns()
    monkeypatch.setattr(extraction, "_create_agent", lambda *args, **kwargs: facts)
    monkeypatch.setattr(design_stage, "_create_agent", lambda *args, **kwargs: designs)
    try:
        with pytest.raises(RuntimeError, match="design interrupted"):
            await build_characters_structured(store)
        assert store.get_character("石九") is None
        await store.close()
        store = SQLiteStore("user/test", output_dir=str(tmp_path / "output"),
                            state_dir=str(tmp_path / "state"))
        await store.initialize()
        await store.load_graph_state()
        await build_characters_structured(store)
        assert len(facts.calls) == 1
        assert len(designs.calls) == 2
        assert store.get_character("石九") is not None
        await build_characters_structured(store)
        from novelvideo.character_visual import CharacterVisualWorkspaceStore

        workspace = CharacterVisualWorkspaceStore(store.project_dir).get("石九")
        assert not any(p.quality_issues for p in workspace.design_proposals)
        assert len(designs.calls) == 2
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_runner_logs_do_not_reset_progress(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from novelvideo.task_backend.runners import graph_build
    from novelvideo import structured_builders

    values = []

    class Store:
        def get_all_characters(self):
            return []

        async def close(self):
            pass

    async def load(_ctx):
        return Store()

    async def build(_store, *, on_progress, on_log):
        on_progress(0.55, "事实完成")
        on_log("正在设计")
        on_progress(0.75, "设计完成")
        return []

    monkeypatch.setattr(graph_build, "require_imported_novel", lambda *_: None)
    monkeypatch.setattr(graph_build, "is_structured_pipeline", lambda *_: True)
    monkeypatch.setattr(graph_build, "_load_store", load)
    monkeypatch.setattr(graph_build, "_progress", lambda ctx, kind, value, msg: values.append(value))
    monkeypatch.setattr(structured_builders, "build_characters_structured", build)
    await graph_build._run_build_characters(SimpleNamespace(output_dir=tmp_path, state_dir=tmp_path))
    assert values == [0.55, 0.55, 0.75]


@pytest.mark.asyncio
async def test_sparse_usable_proposal_set_is_retained_on_next_build(tmp_path, monkeypatch):
    from novelvideo.sqlite_store import SQLiteStore
    from novelvideo.structured_builders import build_characters_structured
    import novelvideo.character_design_stage as design_stage

    class RepairDesigns(Designs):
        async def run(self, prompt):
            self.calls.append(prompt)
            rejected = [dict(proposal_id=f"p{i}", title="空方案", recommended=i == 0) for i in range(3)]
            return {"design_proposals": rejected if len(self.calls) <= 2 else proposals()}

    facts, designs = Facts(), RepairDesigns()
    monkeypatch.setattr(extraction, "_create_agent", lambda *args, **kwargs: facts)
    monkeypatch.setattr(design_stage, "_create_agent", lambda *args, **kwargs: designs)
    store = SQLiteStore("user/test", output_dir=str(tmp_path / "output"), state_dir=str(tmp_path / "state"))
    await store.initialize()
    await store.load_graph_state()
    Path(store.project_dir).mkdir(parents=True, exist_ok=True)
    (Path(store.project_dir) / "novel.txt").write_text("石九进门", encoding="utf-8")
    try:
        first = await build_characters_structured(store)
        assert first.stats["proposal_failed"] == []
        second = await build_characters_structured(store)
        assert second.stats["proposal_failed"] == []
        assert len(facts.calls) == 1
        assert len(designs.calls) == 1
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_roster_collision_is_cached_without_extra_model_calls():
    from novelvideo.character_design_stage import design_merged_characters

    cache = {}

    async def load(key):
        return cache.get(key, "")

    async def save(key, value):
        cache[key] = value

    designs = Designs()
    for names in (("甲", "乙"), ("乙", "甲")):
        people = [extraction.MergedCharacter(name=name) for name in names]
        await design_merged_characters(people, agent=designs, concurrency=1,
                                      load_checkpoint=load, save_checkpoint=save)
    assert len(designs.calls) == 2
    assert all(person.design_accepted for person in people)


@pytest.mark.asyncio
async def test_source_change_does_not_reuse_unconfirmed_workspace(tmp_path, monkeypatch):
    from novelvideo.sqlite_store import SQLiteStore
    from novelvideo.structured_builders import build_characters_structured
    import novelvideo.character_design_stage as design_stage

    facts, designs = Facts(), Designs()
    monkeypatch.setattr(extraction, "_create_agent", lambda *args, **kwargs: facts)
    monkeypatch.setattr(design_stage, "_create_agent", lambda *args, **kwargs: designs)
    store = SQLiteStore("user/test", output_dir=str(tmp_path / "output"), state_dir=str(tmp_path / "state"))
    await store.initialize()
    await store.load_graph_state()
    Path(store.project_dir).mkdir(parents=True, exist_ok=True)
    source = Path(store.project_dir) / "novel.txt"
    try:
        source.write_text("石九进门", encoding="utf-8")
        await build_characters_structured(store)
        source.write_text("石九穿制服出门", encoding="utf-8")
        await build_characters_structured(store)
        assert len(facts.calls) == 2
        assert len(designs.calls) == 2
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_late_similar_design_does_not_displace_or_reject_earlier_design(tmp_path, monkeypatch):
    from novelvideo.sqlite_store import SQLiteStore
    from novelvideo.structured_builders import build_characters_structured
    import novelvideo.character_design_stage as design_stage

    class TwoFacts:
        async def run(self, prompt):
            return {"characters": [{"name": name, "evidence": [{"quote": name}]} for name in ("A", "B")]}

    class RacingDesigns:
        async def run(self, prompt):
            if '"name": "A"' in prompt:
                await asyncio.sleep(0.02)
            return {"design_proposals": proposals()}

    monkeypatch.setattr(extraction, "_create_agent", lambda *args, **kwargs: TwoFacts())
    monkeypatch.setattr(design_stage, "_create_agent", lambda *args, **kwargs: RacingDesigns())
    store = SQLiteStore("user/test", output_dir=str(tmp_path / "output"), state_dir=str(tmp_path / "state"))
    await store.initialize()
    await store.load_graph_state()
    Path(store.project_dir).mkdir(parents=True, exist_ok=True)
    (Path(store.project_dir) / "novel.txt").write_text("A看向B", encoding="utf-8")
    try:
        result = await build_characters_structured(store)
        assert result.stats["proposal_failed"] == []
    finally:
        await store.close()
