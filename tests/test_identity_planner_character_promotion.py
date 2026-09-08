import pytest
import asyncio

from novelvideo.agents.identity_planner import (
    AppearanceDescription,
    DefaultIdentityRequirement,
    EpisodeDefaultIdentities,
    EpisodeIdentityRequirements,
    IdentityRequirement,
    IdentityPlanner,
)
from novelvideo.models import CharacterIdentity, NovelCharacter, NovelEpisode


class FakeIdentityStore:
    def __init__(self, content):
        self.content = content
        self._characters = {}
        self.updated_episode = None

    def get_all_characters(self):
        return list(self._characters.values())

    async def load_episode_content(self, episode_number):
        return self.content

    def resolve_name(self, name):
        return name

    def get_character(self, name):
        return self._characters.get(name)

    async def add_character(self, character):
        self._characters[character.name] = character

    async def update_episode(self, episode_number, **updates):
        self.updated_episode = (episode_number, updates)


class ExistingCharacterIdentityPlanner(IdentityPlanner):
    async def _filter_cast(self, all_names, content_text, episode, on_log=None):
        assert "陆辰" in all_names
        return ["陆辰"], ""

    async def _analyze_default_identities(
        self,
        episode,
        on_log=None,
        cast_names=None,
        content_text=None,
        graph_context="",
    ):
        return EpisodeDefaultIdentities(
            defaults=[
                DefaultIdentityRequirement(
                    character_name="陆辰",
                    visual_state="默认",
                    reason="图谱已有角色，场次头明确标注出场人物",
                )
            ]
        )

    async def _resolve_requirements(self, episode_number, requirements, on_log=None):
        return 0, ["陆辰_默认"], {("陆辰", "默认"): "陆辰_默认"}

    async def _analyze_special_identities(
        self,
        episode,
        on_log=None,
        cast_names=None,
        content_text=None,
        graph_context="",
        already_resolved=None,
    ):
        return EpisodeIdentityRequirements()


class MissingCharacterIdentityPlanner(IdentityPlanner):
    async def _filter_cast(self, all_names, content_text, episode, on_log=None):
        assert all_names == []
        assert self.cognee_store.get_character("陆辰") is None
        return [], ""


@pytest.mark.asyncio
async def test_identity_planner_uses_existing_characters_without_auto_creation():
    store = FakeIdentityStore(
        """
场次（1） 地点：地下室，夜，内；出场人物：陆辰
陆辰推开一个腐朽的空书架。
"""
    )
    await store.add_character(
        NovelCharacter(
            name="陆辰",
            aliases=["陆先生"],
            gender="男",
            description="手动确认过的全局角色",
        )
    )
    planner = ExistingCharacterIdentityPlanner(store)

    new_count, resolved_count = await planner.plan_single_episode(
        NovelEpisode(number=1, title="命运之书")
    )

    assert new_count == 0
    assert resolved_count == 1
    assert sorted(store._characters) == ["陆辰"]
    assert planner.auto_promoted_characters == []
    assert store.updated_episode == (
        1,
        {
            "identity_ids": ["陆辰_默认"],
            "character_names": ["陆辰"],
            "identity_default_map": {"陆辰": "陆辰_默认"},
        },
    )


@pytest.mark.asyncio
async def test_identity_planner_does_not_auto_create_missing_characters():
    store = FakeIdentityStore(
        """
场次（1） 地点：地下室，夜，内；出场人物：陆辰
陆辰推开一个腐朽的空书架。
"""
    )
    planner = MissingCharacterIdentityPlanner(store)

    with pytest.raises(ValueError, match="Pass 0"):
        await planner.plan_single_episode(NovelEpisode(number=1, title="命运之书"))

    assert store.get_character("陆辰") is None
    assert planner.auto_promoted_characters == []
    assert store.updated_episode is None


@pytest.mark.asyncio
async def test_identity_plan_draft_does_not_write_during_planning():
    store = FakeIdentityStore("陆辰在地下室推开一个腐朽的空书架。")
    await store.add_character(NovelCharacter(name="陆辰", gender="男"))
    planner = ExistingCharacterIdentityPlanner(store)

    draft = await planner.build_identity_plan_draft(
        NovelEpisode(number=1, title="命运之书")
    )

    assert draft.new_count == 0
    assert draft.resolved_count == 1
    assert draft.episode_identity_ids == ("陆辰_默认",)
    assert draft.identity_default_map == {"陆辰": "陆辰_默认"}
    assert draft.characters == ()
    assert draft.identity_baseline_digests == {}
    assert store.updated_episode is None


class RealResolveDraftPlanner(IdentityPlanner):
    async def _filter_cast(self, all_names, content_text, episode, on_log=None):
        return ["陆辰"], ""

    async def _analyze_default_identities(self, *args, **kwargs):
        return EpisodeDefaultIdentities(
            defaults=[
                DefaultIdentityRequirement(
                    character_name="陆辰",
                    visual_state="默认",
                    reason="现实主线",
                )
            ]
        )

    async def _analyze_special_identities(self, *args, **kwargs):
        return EpisodeIdentityRequirements(
            requirements=[
                IdentityRequirement(
                    character_name="陆辰",
                    visual_state="战斗装",
                    reason="稳定战斗造型",
                )
            ]
        )

    async def _generate_appearance(self, *args, **kwargs):
        return AppearanceDescription(
            appearance_details="深色束袖战袍配皮革护腕与金属腰封，长发高束便于行动"
        )


@pytest.mark.asyncio
async def test_identity_draft_real_resolve_adds_and_repairs_only_in_memory():
    pending = CharacterIdentity(
        identity_id="陆辰_默认",
        character_name="陆辰",
        identity_name="默认",
        source="identity_planner",
    )
    store = FakeIdentityStore("陆辰换上战斗装进入地下室。")
    original = NovelCharacter(name="陆辰", gender="男")
    original.identities = [pending]
    await store.add_character(original)
    await store.add_character(NovelCharacter(name="路人", gender="男"))
    planner = RealResolveDraftPlanner(store)

    draft = await planner.build_identity_plan_draft(
        NovelEpisode(number=1, title="命运之书")
    )

    assert [item.identity_id for item in original.identities] == ["陆辰_默认"]
    assert original.identities[0].appearance_details == ""
    assert [character.name for character in draft.characters] == ["陆辰"]
    assert list(draft.identity_baseline_digests) == ["陆辰"]
    planned = draft.characters[0]
    assert [item.identity_id for item in planned.identities] == [
        "陆辰_默认",
        "陆辰_战斗装",
    ]
    assert all(item.appearance_details for item in planned.identities)
    assert store.updated_episode is None


class ConcurrentDraftPlanner(ExistingCharacterIdentityPlanner):
    def __init__(self, store):
        super().__init__(store)
        self.entered = [asyncio.Event(), asyncio.Event()]
        self.releases = [asyncio.Event(), asyncio.Event()]
        self.call_count = 0

    async def _filter_cast(self, all_names, content_text, episode, on_log=None):
        index = self.call_count
        self.call_count += 1
        self.entered[index].set()
        await self.releases[index].wait()
        return ["陆辰"], ""


@pytest.mark.asyncio
async def test_concurrent_drafts_on_same_planner_serialize_store_swap():
    store = FakeIdentityStore("陆辰在地下室。")
    await store.add_character(NovelCharacter(name="陆辰", gender="男"))
    planner = ConcurrentDraftPlanner(store)

    first = asyncio.create_task(
        planner.build_identity_plan_draft(NovelEpisode(number=1, title="一"))
    )
    await planner.entered[0].wait()
    second = asyncio.create_task(
        planner.build_identity_plan_draft(NovelEpisode(number=2, title="二"))
    )
    await asyncio.sleep(0)
    assert not planner.entered[1].is_set()
    planner.releases[0].set()
    await planner.entered[1].wait()
    planner.releases[1].set()
    await asyncio.gather(first, second)

    assert planner.cognee_store is store
    assert store.updated_episode is None


@pytest.mark.asyncio
async def test_concurrent_plan_single_calls_hold_lock_through_legacy_publish():
    store = FakeIdentityStore("陆辰在地下室。")
    await store.add_character(NovelCharacter(name="陆辰", gender="男"))
    planner = ConcurrentDraftPlanner(store)

    first = asyncio.create_task(
        planner.plan_single_episode(NovelEpisode(number=1, title="一"))
    )
    await planner.entered[0].wait()
    second = asyncio.create_task(
        planner.plan_single_episode(NovelEpisode(number=2, title="二"))
    )
    await asyncio.sleep(0)
    assert not planner.entered[1].is_set()
    planner.releases[0].set()
    await planner.entered[1].wait()
    assert store.updated_episode is not None
    assert store.updated_episode[0] == 1
    planner.releases[1].set()
    await asyncio.gather(first, second)

    assert planner.cognee_store is store
    assert store.updated_episode[0] == 2
