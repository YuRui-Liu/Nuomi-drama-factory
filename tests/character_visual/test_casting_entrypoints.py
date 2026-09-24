"""Recovery must precede reads; media responses pin immutable adopted bytes."""
from types import SimpleNamespace
import sys

import pytest

from tests.character_visual.test_casting_adoption import adoption_env as adoption_env


def test_media_resolution_recovers_before_missing_canonical_check(tmp_path, monkeypatch):
    from novelvideo.api.routes import files

    root = tmp_path / "output"
    root.mkdir()
    immutable = root / "version.png"
    immutable.write_bytes(b"immutable")
    requested = root / "assets/characters/A/portrait.png"
    calls = []

    def resolve(project_dir, state_dir, path):
        calls.append((project_dir, state_dir, path))
        return immutable

    monkeypatch.setitem(sys.modules, "novelvideo.character_visual.casting_recovery",
                        SimpleNamespace(resolve_casting_media_path=resolve))
    context = SimpleNamespace(project_dir=root, state_dir=tmp_path / "state")
    assert files._resolve_project_file(context, "assets/characters/A/portrait.png") == immutable
    assert calls == [(root, context.state_dir, requested)]


@pytest.mark.asyncio
async def test_project_scope_recovers_before_source_database_registration(tmp_path, monkeypatch):
    from novelvideo.api import deps
    from novelvideo import episode_source_versions

    ctx = SimpleNamespace(output_dir=tmp_path / "output", state_dir=tmp_path / "state",
                          runtime_dir=tmp_path / "runtime", owner_username="u", project_name="p")
    events = []

    async def context(**kwargs):
        return ctx

    async def register(*args):
        events.append("register")

    monkeypatch.setattr(deps, "resolve_project_context", context)
    monkeypatch.setattr(deps, "require_project_home_node", lambda *args, **kwargs: None)
    monkeypatch.setattr(episode_source_versions, "register_existing_source_database", register)
    monkeypatch.setitem(sys.modules, "novelvideo.character_visual.casting_recovery", SimpleNamespace(
        recover_casting_adoptions=lambda *args: events.append("recover")))
    result = await deps.resolve_project_scope("p", {"username": "u"})
    assert result.ctx is ctx
    assert events == ["recover", "register"]


def test_identity_reference_scan_recovers_before_workflow_read(tmp_path, monkeypatch):
    from novelvideo.task_backend.runners import identity

    events = []
    monkeypatch.setitem(sys.modules, "novelvideo.character_visual.casting_recovery", SimpleNamespace(
        recover_casting_adoptions=lambda *args: events.append("recover")))
    monkeypatch.setattr(identity, "ProductionWorkflowStore", lambda *args: events.append("workflow"))
    ctx = SimpleNamespace(output_dir=tmp_path / "output", state_dir=tmp_path / "state")
    assert identity._available_character_portraits(ctx=ctx, characters=[]) == frozenset()
    assert events == ["recover", "workflow"]


@pytest.mark.asyncio
@pytest.mark.parametrize("identity_id", [None, "old"])
async def test_generic_workflow_import_cannot_bypass_casting(adoption_env, identity_id, monkeypatch):
    from fastapi import HTTPException
    from novelvideo.api.routes import production_assets
    from novelvideo.production_workflow.slot_ids import (
        character_portrait_slot_id, character_identity_portrait_slot_id,
    )

    env = await adoption_env(identity_id)
    resolved = SimpleNamespace(project_dir=env.ctx.output_dir, state_dir=env.ctx.state_dir)

    async def scope(*args, **kwargs):
        return resolved

    monkeypatch.setattr(production_assets, "resolve_project_scope", scope)
    slot = (character_identity_portrait_slot_id("甲", identity_id)
            if identity_id else character_portrait_slot_id("甲"))
    asset = env.candidates.output_path("candidate").relative_to(env.ctx.output_dir).as_posix()
    with pytest.raises(HTTPException) as exc:
        await production_assets.materialize_legacy_asset(
            "project", slot,
            production_assets.LegacyAssetRequest(asset_kind="character_portrait", asset_path=asset),
            user={"username": "owner"},
        )
    assert exc.value.status_code == 409
    assert not (env.ctx.state_dir / "production_workflow.json").exists()


@pytest.mark.asyncio
async def test_freezone_cannot_overwrite_casting_stage_portrait(adoption_env, monkeypatch):
    from fastapi import HTTPException
    from novelvideo.api.routes import freezone
    from novelvideo.api.schemas import PushRequest
    from novelvideo.utils.path_resolver import canonical_identity_portrait_path

    env = await adoption_env("old")
    target = canonical_identity_portrait_path(env.ctx.output_dir, "甲", "old")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"original")

    async def scope(*args, **kwargs):
        return env.ctx, "owner", "project", env.ctx.output_dir, str(env.ctx.output_dir)

    monkeypatch.setattr(freezone, "_resolve_freezone_project", scope)
    source = env.candidates.output_path("candidate").relative_to(env.ctx.output_dir).as_posix()
    with pytest.raises(HTTPException) as exc:
        await freezone.freezone_push("project", PushRequest(
            source_url=source,
            target={"kind": "identity_portrait", "character": "甲", "identity_id": "old"},
        ), user={"username": "owner"})
    assert exc.value.status_code == 409
    assert target.read_bytes() == b"original"


@pytest.mark.asyncio
async def test_legacy_workspace_route_recovers_using_registered_state_directory(adoption_env, monkeypatch):
    from novelvideo.api.routes import characters
    from novelvideo.character_visual import casting_adoption
    from tests.character_visual.test_casting_adoption import SimulatedCrash

    env = await adoption_env()
    before = env.visual.get("甲").model_dump(mode="json")

    def crash(point):
        if point == "bible_saved":
            raise SimulatedCrash()

    monkeypatch.setattr(casting_adoption, "_checkpoint", crash)
    with pytest.raises(SimulatedCrash):
        await env.adopt()

    async def scope(*args, **kwargs):
        return env.ctx, "owner", "project", env.ctx.output_dir, str(env.ctx.output_dir), env.sql

    monkeypatch.setattr(characters, "_resolve_character_project", scope)
    result = await characters.get_character_visual_workspace("project", "甲", {"username": "owner"})
    assert result["data"] == before
    assert not (env.ctx.output_dir / "assets/characters/甲/portrait.png").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["upload", "delete", "portrait_upload"])
async def test_identity_sheet_upload_cannot_alias_casting_portrait(adoption_env, monkeypatch, operation):
    import io
    from fastapi import HTTPException, UploadFile
    from novelvideo.api.routes import characters
    from novelvideo.utils.path_resolver import canonical_identity_portrait_path
    from tests.character_visual.test_casting_adoption import png
    from novelvideo.models import CharacterIdentity

    env = await adoption_env("old")
    await env.adopt()
    target = canonical_identity_portrait_path(env.ctx.output_dir, "甲", "老年")
    before = target.read_bytes()
    await env.sql.add_character_identity("甲", CharacterIdentity(
        identity_id="alias", character_name="甲",
        identity_name=("甲_老年" if operation == "portrait_upload" else
                       "甲_甲_老年_portrait" if operation == "delete" else "甲_老年_portrait"),
    ))

    async def scope(*args, **kwargs):
        return env.ctx, "owner", "project", env.ctx.output_dir, str(env.ctx.output_dir), env.sql

    monkeypatch.setattr(characters, "_resolve_character_project", scope)
    with pytest.raises(HTTPException) as exc:
        if operation == "delete":
            await characters.delete_identity_image("project", "甲", "alias", {"username": "owner"})
        elif operation == "portrait_upload":
            await characters.upload_identity_portrait("project", "甲", "alias",
                UploadFile(filename="new.png", file=io.BytesIO(png("blue"))), {"username": "owner"})
        else:
            await characters.upload_identity_image("project", "甲", "甲_老年_portrait",
                UploadFile(filename="new.png", file=io.BytesIO(png("blue"))), {"username": "owner"})
    assert exc.value.status_code == 409
    assert target.read_bytes() == before


@pytest.mark.asyncio
@pytest.mark.parametrize("identity_id", [None, "old"])
@pytest.mark.parametrize("victim_kind", ["canonical", "immutable"])
@pytest.mark.parametrize("operation", ["adopt", "delete_asset", "delete_canonical"])
async def test_other_slot_cannot_mutate_protected_casting_paths(
    adoption_env, monkeypatch, identity_id, victim_kind, operation,
):
    from datetime import datetime, timezone
    from fastapi import HTTPException
    from novelvideo.api.routes import production_assets
    from novelvideo.character_visual.casting_recovery import canonical_portrait_path
    from novelvideo.production_workflow import ProductionWorkflowStore
    from novelvideo.production_workflow.slot_ids import (
        character_portrait_slot_id, character_identity_portrait_slot_id,
    )
    from tests.character_visual.test_casting_adoption import png

    env = await adoption_env(identity_id)
    adopted = await env.adopt()
    workflow_path = env.ctx.state_dir / "production_workflow.json"
    workflow = ProductionWorkflowStore(workflow_path)
    protected_slot = (character_identity_portrait_slot_id("甲", identity_id)
                      if identity_id else character_portrait_slot_id("甲"))
    _, versions = workflow.get_slot(protected_slot)
    immutable = env.ctx.output_dir / versions[adopted["version_id"]].asset_path
    canonical = canonical_portrait_path(env.ctx.output_dir, "甲", identity_id, "老年")
    victim = canonical if victim_kind == "canonical" else immutable
    blue = env.ctx.output_dir / "blue.png"
    blue.write_bytes(png("blue"))
    victim_before = victim.read_bytes()

    # Seed an old untagged version in another slot. Existing metadata must not
    # acquire permission to mutate a protected path through its unrelated slot.
    unrelated_slot = character_portrait_slot_id("乙")
    workflow.register_candidate_version(
        slot_id=unrelated_slot, asset_kind="character_portrait", version_id="other",
        asset_path=(victim if operation == "delete_asset" else blue).relative_to(env.ctx.output_dir).as_posix(),
        source_attempt_id=None, qc_passed=True,
        generation_metadata={} if operation == "delete_asset" else {
            "canonical_path": victim.relative_to(env.ctx.output_dir).as_posix(),
        },
        actor="owner", at=datetime.now(timezone.utc),
    )
    workflow_before = workflow_path.read_bytes()

    async def scope(*args, **kwargs):
        return SimpleNamespace(project_dir=env.ctx.output_dir, state_dir=env.ctx.state_dir)

    monkeypatch.setattr(production_assets, "resolve_project_scope", scope)
    with pytest.raises(HTTPException) as exc:
        if operation == "adopt":
            await production_assets.adopt_production_asset_version(
                "project", unrelated_slot, "other",
                production_assets.AdoptVersionRequest(reason="legacy adoption"),
                user={"username": "owner"},
            )
        else:
            await production_assets.delete_production_asset_version(
                "project", unrelated_slot, "other", user={"username": "owner"},
            )
    assert exc.value.status_code == 409
    assert "CHARACTER_CASTING_REQUIRED" in str(exc.value.detail)
    assert victim.read_bytes() == victim_before
    assert workflow_path.read_bytes() == workflow_before


@pytest.mark.asyncio
async def test_casting_read_returns_matching_bible_when_adopted_during_source_load(adoption_env, monkeypatch):
    from contextlib import asynccontextmanager
    from novelvideo.api.routes import character_casting

    env = await adoption_env()

    @asynccontextmanager
    async def scope(*args, **kwargs):
        yield env.ctx, env.sql, env.sql.get_character("甲")

    load_sources = character_casting.load_sources

    async def sources_with_concurrent_adoption(*args):
        sources = await load_sources(*args)
        await env.adopt()
        return sources

    monkeypatch.setattr(character_casting, "scope", scope)
    monkeypatch.setattr(character_casting, "load_sources", sources_with_concurrent_adoption)
    monkeypatch.setattr(character_casting, "resolved_style", lambda ctx: "水墨")
    monkeypatch.setattr(character_casting, "make_static_url_for_context", lambda *args, **kwargs: "local")
    result = await character_casting.get_casting("project", "甲", user={"username": "owner"})
    data = result["data"]
    assert data["current"] is not None
    assert data["current_visual_bible"] is not None
    assert data["current_visual_bible"]["revision_id"] == env.command.expected_revision
