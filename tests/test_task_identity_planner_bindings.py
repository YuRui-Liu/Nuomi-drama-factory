import hashlib
from types import SimpleNamespace

import pytest

from novelvideo.agents.identity_planner import IdentityPlanDraft
from novelvideo.models import CharacterIdentity, NovelCharacter, NovelEpisode
from novelvideo.narrative_groups.planned_bindings import PlannedReferenceBinding
from novelvideo.production_workflow.slot_ids import character_state_slot_id
from novelvideo.sqlite_store import SQLiteStore
from novelvideo.task_backend.runners.identity import (
    _build_identity_planner_result,
    _character_identity_bindings,
)


def _character(name: str, *identities: CharacterIdentity) -> NovelCharacter:
    character = NovelCharacter(name=name)
    character.identities = list(identities)
    return character


def _identity(identity_id: str, *, with_image: bool = False) -> CharacterIdentity:
    character_name, identity_name = identity_id.split("_", 1)
    return CharacterIdentity(
        identity_id=identity_id,
        character_name=character_name,
        identity_name=identity_name,
        reference_images=["assets/reference.png"] if with_image else [],
    )


def _binding(identity_id: str, *, revision: str) -> PlannedReferenceBinding:
    character_name = identity_id.split("_", 1)[0]
    return PlannedReferenceBinding.create(
        project_id="owner/project",
        episode_number=1,
        source_plan_revision_id=revision,
        asset_kind="character_identity",
        entity_id=identity_id,
        asset_slot_id=character_state_slot_id(character_name, identity_id),
        status="missing_image",
        resolution="auto_matched",
        display_label=identity_id,
    )


def test_character_binding_projection_uses_new_identity_from_draft():
    new_identity = _identity("陆辰_战斗装", with_image=True)
    draft = IdentityPlanDraft(
        new_count=1,
        resolved_count=1,
        characters=(_character("陆辰", new_identity),),
        episode_identity_ids=(new_identity.identity_id,),
        identity_default_map={"陆辰": new_identity.identity_id},
        identity_baseline_digests={"陆辰": "baseline"},
    )
    shot = SimpleNamespace(
        id="shot-1",
        dramatic_beat_ids=("beat-1",),
        asset_requirements=(
            SimpleNamespace(
                kind="character_identity",
                entity_key=new_identity.identity_id,
                required=True,
            ),
        ),
    )
    plan = SimpleNamespace(
        revision_id="director-r2",
        groups=(
            SimpleNamespace(
                id="group-1",
                dramatic_beat_ids=("beat-1",),
                shots=(shot,),
            ),
        ),
    )

    bindings = _character_identity_bindings(
        project_id="owner/project",
        episode_number=1,
        director_plan=plan,
        draft=draft,
        characters=(_character("陆辰"),),
        scenes=(),
        props=(),
    )

    assert len(bindings) == 1
    assert bindings[0].status == "ready"
    assert bindings[0].asset_slot_id == "character:陆辰:state:陆辰_战斗装"
    assert bindings[0].group_ids == ("group-1",)
    assert bindings[0].shot_ids == ("shot-1",)


def test_identity_runner_result_aggregates_binding_statuses():
    result = _build_identity_planner_result(
        episode=1,
        new_count=1,
        resolved_count=2,
        identities=[],
        auto_promoted_characters=[],
        binding_count=3,
        binding_statuses={"ready": 2, "missing_image": 1},
    )

    assert result["binding_count"] == 3
    assert result["binding_statuses"] == {"ready": 2, "missing_image": 1}


@pytest.mark.asyncio
async def test_publish_identity_plan_atomic_exposes_identity_and_binding_together(tmp_path):
    store = SQLiteStore(
        "owner/project", output_dir=str(tmp_path), state_dir=str(tmp_path)
    )
    await store.initialize()
    old_identity = _identity("陆辰_默认")
    new_identity = _identity("陆辰_战斗装")
    await store.add_character(_character("陆辰", old_identity))
    await store.add_episode(
        NovelEpisode(
            number=1,
            title="第一集",
            character_names=["陆辰"],
            identity_ids=[old_identity.identity_id],
            identity_default_map={"陆辰": old_identity.identity_id},
        )
    )

    await store.publish_identity_plan_atomic(
        episode_number=1,
        characters=(_character("陆辰", old_identity, new_identity),),
        episode_identity_ids=(new_identity.identity_id,),
        identity_default_map={"陆辰": new_identity.identity_id},
        identity_baseline_digests={
            "陆辰": hashlib.sha256(
                _character("陆辰", old_identity).identities_json.encode()
            ).hexdigest()
        },
        bindings=(_binding(new_identity.identity_id, revision="director-r2"),),
    )

    persisted_character = (await store.list_characters())[0]
    persisted_episode = (await store.list_episodes())[0]
    persisted_bindings = await store.list_planned_reference_bindings(1)
    assert [item.identity_id for item in persisted_character.identities] == [
        old_identity.identity_id,
        new_identity.identity_id,
    ]
    assert persisted_episode.identity_ids == [new_identity.identity_id]
    assert persisted_episode.identity_default_map == {"陆辰": new_identity.identity_id}
    assert persisted_bindings == [_binding(new_identity.identity_id, revision="director-r2")]


@pytest.mark.asyncio
async def test_publish_identity_plan_atomic_rolls_back_all_state_on_binding_insert_failure(
    tmp_path, monkeypatch
):
    store = SQLiteStore(
        "owner/project", output_dir=str(tmp_path), state_dir=str(tmp_path)
    )
    await store.initialize()
    old_identity = _identity("陆辰_默认")
    new_identity = _identity("陆辰_战斗装")
    old_binding = _binding(old_identity.identity_id, revision="director-r1")
    await store.add_character(_character("陆辰", old_identity))
    await store.add_episode(
        NovelEpisode(
            number=1,
            title="第一集",
            character_names=["陆辰"],
            identity_ids=[old_identity.identity_id],
            identity_default_map={"陆辰": old_identity.identity_id},
        )
    )
    await store.replace_planned_reference_bindings_atomic(
        1, ("character_identity",), (old_binding,)
    )

    async def fail_binding_insert(_db, _binding):
        raise RuntimeError("binding insert failed")

    monkeypatch.setattr(store, "_insert_planned_reference_binding", fail_binding_insert)
    with pytest.raises(RuntimeError, match="binding insert failed"):
        await store.publish_identity_plan_atomic(
            episode_number=1,
            characters=(_character("陆辰", old_identity, new_identity),),
            episode_identity_ids=(new_identity.identity_id,),
            identity_default_map={"陆辰": new_identity.identity_id},
            identity_baseline_digests={
                "陆辰": hashlib.sha256(
                    _character("陆辰", old_identity).identities_json.encode()
                ).hexdigest()
            },
            bindings=(_binding(new_identity.identity_id, revision="director-r2"),),
        )

    persisted_character = (await store.list_characters())[0]
    persisted_episode = (await store.list_episodes())[0]
    persisted_bindings = await store.list_planned_reference_bindings(1)
    assert [item.identity_id for item in persisted_character.identities] == [
        old_identity.identity_id
    ]
    assert persisted_episode.identity_ids == [old_identity.identity_id]
    assert persisted_episode.identity_default_map == {"陆辰": old_identity.identity_id}
    assert persisted_bindings == [old_binding]


@pytest.mark.asyncio
async def test_publish_preserves_concurrent_non_identity_character_fields(tmp_path):
    store = SQLiteStore(
        "owner/project", output_dir=str(tmp_path), state_dir=str(tmp_path)
    )
    await store.initialize()
    old_identity = _identity("陆辰_默认")
    new_identity = _identity("陆辰_战斗装")
    original = _character("陆辰", old_identity)
    await store.add_character(original)
    await store.add_episode(NovelEpisode(number=1, title="第一集"))
    await store._update_character_field("陆辰", "description", "规划期间人工补充")

    await store.publish_identity_plan_atomic(
        episode_number=1,
        characters=(_character("陆辰", old_identity, new_identity),),
        episode_identity_ids=(new_identity.identity_id,),
        identity_default_map={"陆辰": new_identity.identity_id},
        identity_baseline_digests={
            "陆辰": hashlib.sha256(original.identities_json.encode()).hexdigest()
        },
        bindings=(_binding(new_identity.identity_id, revision="director-r2"),),
    )

    persisted = (await store.list_characters())[0]
    assert persisted.description == "规划期间人工补充"
    assert [item.identity_id for item in persisted.identities] == [
        old_identity.identity_id,
        new_identity.identity_id,
    ]


@pytest.mark.asyncio
async def test_publish_rejects_concurrent_identity_change_and_rolls_back_scope(tmp_path):
    store = SQLiteStore(
        "owner/project", output_dir=str(tmp_path), state_dir=str(tmp_path)
    )
    await store.initialize()
    old_identity = _identity("陆辰_默认")
    planned_identity = _identity("陆辰_战斗装")
    concurrent_identity = _identity("陆辰_人工身份")
    original = _character("陆辰", old_identity)
    baseline_digest = hashlib.sha256(original.identities_json.encode()).hexdigest()
    old_binding = _binding(old_identity.identity_id, revision="director-r1")
    await store.add_character(original)
    await store.add_episode(
        NovelEpisode(
            number=1,
            title="第一集",
            identity_ids=[old_identity.identity_id],
            identity_default_map={"陆辰": old_identity.identity_id},
        )
    )
    await store.replace_planned_reference_bindings_atomic(
        1, ("character_identity",), (old_binding,)
    )
    await store.add_character_identity("陆辰", concurrent_identity)

    with pytest.raises(ValueError, match="identity plan conflict.*陆辰"):
        await store.publish_identity_plan_atomic(
            episode_number=1,
            characters=(_character("陆辰", old_identity, planned_identity),),
            episode_identity_ids=(planned_identity.identity_id,),
            identity_default_map={"陆辰": planned_identity.identity_id},
            identity_baseline_digests={"陆辰": baseline_digest},
            bindings=(_binding(planned_identity.identity_id, revision="director-r2"),),
        )

    persisted_character = (await store.list_characters())[0]
    persisted_episode = (await store.list_episodes())[0]
    assert [item.identity_id for item in persisted_character.identities] == [
        old_identity.identity_id,
        concurrent_identity.identity_id,
    ]
    assert persisted_episode.identity_ids == [old_identity.identity_id]
    assert persisted_episode.identity_default_map == {"陆辰": old_identity.identity_id}
    assert await store.list_planned_reference_bindings(1) == [old_binding]
