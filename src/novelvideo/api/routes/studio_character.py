"""Personal character design cards with authenticated ownership and explicit copies."""
from uuid import uuid4
from typing import Literal
from asyncio import to_thread

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from novelvideo.api.auth import get_api_user, require_scope
from novelvideo.api.deps import get_user_base_dir
from novelvideo.api.routes.characters import _resolve_character_project
from novelvideo.creative_studios.character import portable_character, references_character, capture_character_media, restore_character_media
from novelvideo.creative_studios.store import StudioStore

router = APIRouter()


def _personal_store(user: dict) -> StudioStore:
    username = user.get("username")
    if not isinstance(username, str) or not username or any(value in username for value in ("/", "\\", "..")):
        raise HTTPException(403, "无法确定个人角色库归属")
    return StudioStore(get_user_base_dir(username) / ".creative-studios" / "character-library.db")


@router.get("/studios/character-library")
async def personal_cards(user: dict = Depends(get_api_user)):
    return {"ok": True, "data": _personal_store(user).list("character")}


@router.post("/projects/{project}/studios/characters/{name}/library")
async def save_personal_card(project: str, name: str, user: dict = Depends(get_api_user)):
    ctx, _, _, root, _, store = await _resolve_character_project(project, user, required_role="viewer")
    character = store.get_character(name)
    if character is None:
        raise HTTPException(404, "角色不存在")
    source = character.model_dump(mode="json")
    source["identities"] = [item.model_dump(mode="json") for item in character.identities]
    personal = _personal_store(user)
    card_id = uuid4().hex
    try:
        media = await to_thread(capture_character_media, root, source, personal.path.parent, card_id)
    except (ValueError, OSError) as exc:
        raise HTTPException(409, str(exc)) from exc
    result = personal.save("character", card_id, character.name, {"character": portable_character(source), "media": media, "voice_included": False, "source_project": str(ctx.project_id) if ctx else project}, 0)
    return {"ok": True, "data": result}


class CardCopy(BaseModel):
    name: str = Field(min_length=1, max_length=100)


@router.post("/projects/{project}/studios/character-library/{card_id}/copy")
async def copy_personal_card(project: str, card_id: str, body: CardCopy, user: dict = Depends(get_api_user)):
    from novelvideo.models import CharacterIdentity, NovelCharacter
    _, _, _, root, _, target = await _resolve_character_project(project, user, required_role="editor")
    try:
        card = _personal_store(user).get("character", card_id)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if card is None:
        raise HTTPException(404, "个人角色卡不存在")
    name = body.name.strip()
    if not name or any(value in name for value in ("/", "\\")):
        raise HTTPException(422, "角色名称无效")
    if target.get_character(name) is not None:
        raise HTTPException(409, "目标项目已有同名角色，请选择其他名称")
    data = portable_character(card["data"]["character"])
    identities = data.pop("identities")
    data["name"] = name
    character = NovelCharacter(**data, extraction_locked=True)
    copied_identities = [CharacterIdentity(identity_id=f"{name}_{uuid4().hex[:12]}", character_name=name, source="user_created", **item) for item in identities]
    written = []
    try:
        written = await to_thread(restore_character_media, _personal_store(user).path.parent, card_id, card["data"].get("media", []), root, name, copied_identities)
        character.identities = copied_identities
        added = await target.add_characters_atomic([character], skip_existing=True)
    except (ValueError, OSError) as exc:
        for path in written:
            path.unlink(missing_ok=True)
        raise HTTPException(409, str(exc)) from exc
    if name not in added:
        for path in written:
            path.unlink(missing_ok=True)
        raise HTTPException(409, "目标项目已有同名角色，请选择其他名称")
    return {"ok": True, "data": {"name": name, "card_id": card_id, "source_project": card["data"].get("source_project"), "media_copied": bool(written), "voice_included": False}}


@router.get("/projects/{project}/studios/characters/{name}/impact")
async def character_impact(project: str, name: str, user: dict = Depends(get_api_user)):
    from novelvideo.director_plan.store import DirectorPlanStore
    from novelvideo.freezone.canvas_store import list_canvases, read_canvas
    from novelvideo.narrative_groups.service import load_groups
    _, _, _, root, _, store = await _resolve_character_project(project, user, required_role="viewer")
    character = store.get_character(name)
    if character is None:
        raise HTTPException(404, "角色不存在")
    names = {name, *(item.identity_id for item in character.identities)}
    groups, canvases, warnings = [], [], []
    for episode in await store.list_episodes():
        number = int(episode.number)
        try:
            plan = DirectorPlanStore(root).load_active(number)
            if plan is None:
                if name in episode.character_names:
                    warnings.append(f"第 {number} 集尚无活动导演方案，无法精确列出旧镜头影响")
                continue
            materialized = {item.id: item for item in load_groups(root, number)}
            for group in plan.groups:
                matching = [shot.id for shot in group.shots if references_character(shot.model_dump(mode="json"), names)]
                if not matching:
                    continue
                media = materialized.get(group.id)
                stages = media.to_dict()["stages"] if media else {}
                groups.append({"episode": number, "group_id": group.id, "shot_ids": matching, "director_revision_id": plan.revision_id, "stages": {key: {field: value.get(field) for field in ("status", "revision", "grid_asset", "video_asset", "needs_regeneration")} for key, value in stages.items()}})
        except (ValueError, OSError) as exc:
            warnings.append(f"第 {number} 集引用信息不可读：{type(exc).__name__}")
    for summary in list_canvases(root):
        canvas = read_canvas(root, summary["id"]) or {}
        for node in canvas.get("nodes", []):
            if references_character(node, names):
                canvases.append({"canvas_id": summary["id"], "node_id": node.get("id"), "title": node.get("data", {}).get("label") or node.get("id"), "revision": canvas.get("revision", 0)})
    return {"ok": True, "data": {"groups": groups, "canvases": canvases, "warnings": warnings, "price_estimate": None}}


class RefreshCharacterMedia(BaseModel):
    episode: int = Field(gt=0)
    group_id: str = Field(min_length=1, max_length=200)
    stage: Literal["render", "video"]
    expected_render_revision: int = Field(ge=0)
    idempotency_key: str = Field(pattern=r"^[A-Za-z0-9_-]{1,100}$")


@router.post("/projects/{project}/studios/characters/{name}/refresh-media")
async def refresh_character_media(project: str, name: str, body: RefreshCharacterMedia, user: dict = Depends(require_scope("tasks:submit"))):
    from pathlib import Path
    from novelvideo.api.deps import get_media_capability_store, get_media_credential_resolver
    from novelvideo.api.routes import narrative_groups as routes
    from novelvideo.creative_studios.director import claim_submission
    from novelvideo.narrative_groups.service import load_groups
    from novelvideo.project_config import load_project_config_file_from_state_dir
    ctx, _, _, root, _, _ = await _resolve_character_project(project, user, required_role="editor")
    if ctx is None:
        raise HTTPException(409, "当前项目缺少可提交任务的运行上下文")
    impact = (await character_impact(project, name, user))["data"]
    if not any(item["episode"] == body.episode and item["group_id"] == body.group_id for item in impact["groups"]):
        raise HTTPException(409, "所选叙事组不再引用此角色，请重新加载影响范围")
    group = next((item for item in load_groups(root, body.episode) if item.id == body.group_id), None)
    if group is None:
        raise HTTPException(404, "叙事组素材状态不存在")
    render = group.stages["render"]
    if render.revision != body.expected_render_revision:
        raise HTTPException(409, "上游分镜版本已变化，请重新加载")
    if any(value.status in {"queued", "running"} for value in group.stages.values()):
        raise HTTPException(409, "所选组有运行中任务，请等待其完成")
    if body.stage == "video" and (render.status != "completed" or render.needs_regeneration):
        raise HTTPException(409, "上游分镜尚未成功或已过期，不能提交依赖视频")
    config = load_project_config_file_from_state_dir(ctx.state_dir)
    aspect = "16:9" if config.get("aspect_ratio") == "16:9" else "9:16"
    if body.stage == "render":
        preview = (await routes.preview_group_references(project, body.episode, body.group_id, "render", user))["data"]
        selected = [item["binding_id"] for item in preview["bindings"] if item["required"] or item["selected_by_default"]]
        unavailable = [item for item in preview["bindings"] if item["binding_id"] in selected and item["status"] != "ready"]
        if unavailable:
            raise HTTPException(409, "必需角色、场景或道具引用未就绪，请先补齐素材")
        request = routes.NarrativeGroupGenerationRequest(aspect_ratio=aspect, allow_unconstrained=False, reference_resolution={"selected_binding_ids": selected, "upload_ids": [], "reference_revision": preview["reference_revision"]})
    key = f"character-refresh:{name}:{body.episode}:{body.group_id}:{body.stage}:{body.idempotency_key}"
    if not claim_submission(Path(ctx.state_dir) / "creative-studios.db", key):
        raise HTTPException(409, "这次更新已提交，请到任务中心核对状态，勿重复购买")
    if body.stage == "render":
        return await routes._enqueue_group_action(project, body.episode, body.group_id, "render", user, regenerate=True, generation_request=request)
    request = routes.NarrativeGroupVideoRequest(model=group.video_settings.workflow_id, aspect_ratio=aspect, revision=group.stages["video"].revision, plan_revision=group.video_plan.revision, settings_revision=group.video_settings.revision, reference_revision=group.video_reference_settings.revision)
    return await routes._enqueue_group_video(project, body.episode, body.group_id, user, request, get_media_capability_store(), get_media_credential_resolver())


@router.get("/projects/{project}/studios/characters/{name}/costume-targets")
async def costume_targets(project: str, name: str, user: dict = Depends(get_api_user)):
    from novelvideo.sqlite_store import SQLiteStore
    _, _, _, _, _, store = await _resolve_character_project(project, user, required_role="viewer")
    if store.get_character(name) is None:
        raise HTTPException(404, "角色不存在")
    return {"ok": True, "data": [{"episode": episode.number, "title": episode.title, "current_identity_id": episode.identity_default_map.get(name), "expected_episode_digest": SQLiteStore.identity_episode_baseline_digest(episode.identity_ids, episode.identity_default_map)} for episode in await store.list_episodes() if name in episode.character_names or name in episode.identity_default_map]}


class ApplyCostume(BaseModel):
    episode: int = Field(gt=0)
    identity_id: str = Field(min_length=1, max_length=200)
    expected_episode_digest: str = Field(min_length=1, max_length=100)


@router.post("/projects/{project}/studios/characters/{name}/apply-costume")
async def apply_costume(project: str, name: str, body: ApplyCostume, user: dict = Depends(get_api_user)):
    from novelvideo.director_plan.store import DirectorPlanStore
    from novelvideo.narrative_groups.planned_binding_service import bindings_for_director_plan
    ctx, _, _, root, _, store = await _resolve_character_project(project, user, required_role="editor")
    if ctx is None:
        raise HTTPException(409, "当前项目缺少可发布引用的运行上下文")
    character = store.get_character(name)
    if character is None:
        raise HTTPException(404, "角色不存在")
    own_ids = {item.identity_id for item in character.identities}
    if body.identity_id not in own_ids:
        raise HTTPException(422, "所选服装身份不属于此角色")
    episode = store.get_episode(body.episode)
    if episode is None:
        raise HTTPException(404, "剧集不存在")
    defaults = {**episode.identity_default_map, name: body.identity_id}
    identities = [item for item in episode.identity_ids if item not in own_ids] + [body.identity_id]
    plan_store = DirectorPlanStore(root)
    try:
        # The same activation lock used by director consumers keeps the projected
        # references and their source plan consistent through the atomic publish.
        with plan_store.lock_active_revision(body.episode) as plan:
            bindings = None
            if plan is not None:
                projected = bindings_for_director_plan(project_id=str(ctx.project_id), episode_number=body.episode, source_plan_revision_id=plan.revision_id, groups=plan.groups, shots=[shot for group in plan.groups for shot in group.shots], characters=store.get_all_characters(), scenes=[], props=[], episode_identity_ids=identities, identity_default_map=defaults)
                existing = await store.list_planned_reference_bindings(body.episode)
                affected_entities = own_ids | {name}
                bindings = [item for item in existing if item.asset_kind == "character_identity" and item.entity_id not in affected_entities]
                bindings.extend(item for item in projected if item.asset_kind == "character_identity" and item.entity_id in affected_entities)
            await store.publish_identity_plan_atomic(episode_number=body.episode, characters=[], episode_identity_ids=identities, identity_default_map=defaults, identity_baseline_digests={}, episode_identity_baseline_digest=body.expected_episode_digest, bindings=bindings)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(409, "引用版本已变化或与当前导演方案冲突，请刷新后重试") from exc
    return {"ok": True, "data": {"episode": body.episode, "identity_id": body.identity_id, "existing_media_modified": False}}
