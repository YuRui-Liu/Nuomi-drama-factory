from types import SimpleNamespace as NS
import json

import pytest

from novelvideo.api.routes import assets
from novelvideo.director_plan.store import DirectorPlanStore
from novelvideo.director_plan.models import DirectorPlanRevision, NarrativeGroupPlan, ShotPlan, AssetRequirement


@pytest.mark.asyncio
async def test_asset_references_include_active_groups_and_episode_bindings(monkeypatch, tmp_path):
    requirement = lambda kind, key: NS(kind=kind, entity_key=key)
    group = NS(id="group-current", ordinal=2, shots=[NS(id="shot-1", asset_requirements=[
        requirement("prop", "朏朏残简"), requirement("prop", "朏朏残简"),
        requirement("character_identity", "步知遥"), requirement("scene_base", "山门"),
    ])])
    active = NS(revision_id="active-1", groups=[group])
    episode = NS(number=1, identity_ids=["步知遥_青年"], identity_default_map={"步知遥": "步知遥_青年"},
                 scene_menu=[NS(scene_id="山门")], prop_menu=[NS(prop_id="朏朏残简"), NS(prop_id="未入镜道具")])
    bindings = [NS(source_plan_revision_id=revision, asset_kind="prop", entity_id=entity,
                   base_entity_id="", group_ids=["group-current"], shot_ids=[])
                for revision, entity in [("active-1", "朏朏残简"), ("old-plan", "旧版道具")]]
    class Store:
        async def list_visual_beats(self): return []
        async def list_episodes(self): return [episode]
        async def list_planned_reference_bindings(self, number): return bindings
        async def close(self): pass
    async def scope(*args, **kwargs): return NS(ctx=NS(output_dir=tmp_path))
    async def store(*args): return Store()
    def load(self, number, *, check_source=True):
        assert check_source is False
        return active
    monkeypatch.setattr(assets, "resolve_project_scope", scope)
    monkeypatch.setattr(assets, "make_sqlite_store_for_context", store)
    monkeypatch.setattr(DirectorPlanStore, "_load_active", load)
    ids = ["prop:朏朏残简", "prop:旧版道具", "prop:未入镜道具", "identity:步知遥_青年", "scene:山门"]
    result = (await assets.get_project_asset_references("p", ids=ids, user={}))['data']
    ref = {"episode": 1, "group_id": "group-current", "group_ordinal": 2}
    assert result["usages"]["prop:朏朏残简"] == [ref]
    assert "prop:旧版道具" not in result["usages"]
    assert result["usages"]["identity:步知遥_青年"] == [ref]
    assert result["usages"]["prop:未入镜道具"] == [{"episode": 1, "binding": True}]
    assert result["scene_co_occurrence"]["山门"] == {"identities": ["步知遥_青年"], "props": ["朏朏残简"]}
    single = (await assets.get_asset_references("p", "prop", "朏朏残简", user={}))['data']
    assert single["beats"] == [ref]
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_active_plan_snapshot_is_read_without_materialization(monkeypatch, tmp_path):
    shot = ShotPlan(id="shot", source_span_ids=(), subject="人", action="走",
                    visible_start_state="站立", visible_end_state="离开", duration_seconds=3,
                    asset_requirements=(AssetRequirement(kind="scene_state", entity_key="山门_夜晚"),
                                        AssetRequirement(kind="character_state", entity_key="步知遥")))
    group = NarrativeGroupPlan(id="g1", ordinal=1, source_span_ids=(), scene_anchor="山门",
                               time_anchor="夜", objective="离开", visible_turn="离开", relation_to_previous="single", shots=(shot,))
    revision = DirectorPlanRevision.new(episode=1, source_script_hash="stale-source-is-still-a-reference",
                director_model="test", prompt_version="1", project_style_snapshot_id="style", groups=(group,))
    directory = tmp_path / "director_plans" / "episode_001"
    (directory / "revisions").mkdir(parents=True)
    (directory / "revisions" / f"{revision.revision_id}.json").write_text(revision.model_dump_json())
    (directory / "active.json").write_text(json.dumps({"revision_id": revision.revision_id}))
    # An unactivated revision must not contribute usages.
    (directory / "revisions" / "draft.json").write_text("not an active revision")
    before = {str(p.relative_to(tmp_path)): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    class Store:
        async def list_visual_beats(self): return []
        async def list_episodes(self):
            return [NS(number=1, identity_ids=["步知遥_青年"], identity_default_map={}, scene_menu=[], prop_menu=[])]
        def get_all_characters(self):
            return [NS(name="步知遥", identities=[NS(identity_id="步知遥_青年")])]
        async def list_scenes(self): return [NS(name="山门")]
        async def close(self): pass
    async def store(*args): return Store()
    monkeypatch.setattr(assets, "make_sqlite_store_for_context", store)
    rows = await assets._load_beat_asset_refs(NS(output_dir=tmp_path))
    assert rows == [({"episode": 1, "group_id": "g1", "group_ordinal": 1}, {"步知遥_青年"}, set(), {"山门"})]
    assert {str(p.relative_to(tmp_path)): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == before
