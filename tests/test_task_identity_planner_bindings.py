import asyncio
import hashlib
import threading
import time
from types import SimpleNamespace

import pytest

from novelvideo.agents.identity_planner import IdentityPlanDraft, IdentityPlanner
from novelvideo.director_plan.store import DirectorPlanStore
from novelvideo.models import CharacterIdentity, NovelCharacter, NovelEpisode
from novelvideo.narrative_groups.planned_bindings import PlannedReferenceBinding
from novelvideo.narrative_groups.planned_binding_service import (
    _ProjectedRequirement,
    _binding as _project_binding,
    bindings_for_director_plan,
)
from novelvideo.production_workflow.slot_ids import (
    character_portrait_slot_id,
    character_state_slot_id,
)
from novelvideo.sqlite_store import SQLiteStore
from novelvideo.task_backend.runners.identity import (
    _build_identity_planner_result,
    _character_identity_bindings,
)
from tests.director_plan.test_store import make_revision


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


def _project_character_binding(
    entity_key: str,
    *,
    characters: tuple[NovelCharacter, ...],
    episode_identity_ids: tuple[str, ...] = (),
    identity_default_map: dict[str, str] | None = None,
    available_character_portraits: tuple[str, ...] = (),
    available_character_identity_ids: tuple[str, ...] | None = None,
) -> PlannedReferenceBinding:
    shot = SimpleNamespace(
        id="shot-1",
        dramatic_beat_ids=(),
        asset_requirements=(
            SimpleNamespace(
                kind="character_identity",
                entity_key=entity_key,
                required=True,
            ),
        ),
    )
    return bindings_for_director_plan(
        project_id="owner/project",
        episode_number=1,
        source_plan_revision_id="director-r2",
        groups=(),
        shots=(shot,),
        characters=characters,
        scenes=(),
        props=(),
        episode_identity_ids=episode_identity_ids,
        identity_default_map=identity_default_map or {},
        available_character_portraits=available_character_portraits,
        available_character_identity_ids=available_character_identity_ids,
    )[0]


def test_character_name_requirement_uses_episode_default_identity():
    default_identity = _identity("陆辰_青年时期", with_image=True)
    alternate_identity = _identity("陆辰_战斗装", with_image=True)

    binding = _project_character_binding(
        "陆辰",
        characters=(_character("陆辰", default_identity, alternate_identity),),
        episode_identity_ids=(
            default_identity.identity_id,
            alternate_identity.identity_id,
        ),
        identity_default_map={"陆辰": default_identity.identity_id},
    )

    assert binding.entity_id == default_identity.identity_id
    assert binding.status == "ready"
    assert binding.asset_slot_id == "character:陆辰:state:陆辰_青年时期"


def test_character_name_requirement_uses_only_episode_identity_without_default():
    prior_identity = _identity("陆辰_少年时期", with_image=True)
    episode_identity = _identity("陆辰_青年时期", with_image=True)

    binding = _project_character_binding(
        "陆辰",
        characters=(_character("陆辰", prior_identity, episode_identity),),
        episode_identity_ids=(episode_identity.identity_id,),
    )

    assert binding.entity_id == episode_identity.identity_id
    assert binding.status == "ready"


def test_unique_identity_without_image_stays_missing_when_portrait_is_unavailable():
    identity = _identity("陆辰_青年时期")

    binding = _project_character_binding(
        "陆辰",
        characters=(_character("陆辰", identity),),
        episode_identity_ids=(identity.identity_id,),
    )

    assert binding.asset_kind == "character_identity"
    assert binding.entity_id == identity.identity_id
    assert binding.asset_slot_id == character_portrait_slot_id("陆辰")
    assert binding.status == "missing_image"
    assert binding.resolution == "explicit_fallback"
    assert "基础头像" in binding.display_label


def test_unique_identity_without_image_uses_available_character_portrait():
    identity = _identity("陆辰_青年时期")

    binding = _project_character_binding(
        "陆辰",
        characters=(_character("陆辰", identity),),
        episode_identity_ids=(identity.identity_id,),
        available_character_portraits=("陆辰",),
    )

    assert binding.entity_id == identity.identity_id
    assert binding.asset_slot_id == character_portrait_slot_id("陆辰")
    assert binding.status == "ready"
    assert binding.resolution == "explicit_fallback"


def test_unique_identity_with_image_keeps_state_slot_auto_match():
    identity = _identity("陆辰_青年时期", with_image=True)

    binding = _project_character_binding(
        "陆辰",
        characters=(_character("陆辰", identity),),
        episode_identity_ids=(identity.identity_id,),
    )

    assert binding.asset_slot_id == character_state_slot_id(
        "陆辰", identity.identity_id
    )
    assert binding.status == "ready"
    assert binding.resolution == "auto_matched"
    assert "基础头像" not in binding.display_label


def test_stale_identity_reference_falls_back_to_available_character_portrait():
    identity = _identity("陆辰_青年时期", with_image=True)

    binding = _project_character_binding(
        "陆辰",
        characters=(_character("陆辰", identity),),
        episode_identity_ids=(identity.identity_id,),
        available_character_portraits=("陆辰",),
        available_character_identity_ids=(),
    )

    assert binding.entity_id == identity.identity_id
    assert binding.asset_slot_id == character_portrait_slot_id("陆辰")
    assert binding.status == "ready"
    assert binding.resolution == "explicit_fallback"


def test_identity_runner_requires_real_workflow_identity_asset(tmp_path):
    from datetime import UTC, datetime

    from PIL import Image

    from novelvideo.production_workflow import ProductionWorkflowStore
    from novelvideo.task_backend.runners.identity import _available_character_identity_ids

    identity = CharacterIdentity(
        identity_id="陆辰_青年时期",
        character_name="陆辰",
        identity_name="青年时期",
        reference_images=["assets/characters/陆辰/identities/missing.png"],
    )
    character = _character("陆辰", identity)
    ctx = SimpleNamespace(output_dir=tmp_path, state_dir=tmp_path / "state")
    assert _available_character_identity_ids(
        ctx=ctx, characters=(character,)
    ) == frozenset()

    image_path = tmp_path / "assets" / "characters" / "陆辰" / "identities" / "state.png"
    image_path.parent.mkdir(parents=True)
    Image.new("RGB", (8, 8), "red").save(image_path)
    workflow = ProductionWorkflowStore(ctx.state_dir / "production_workflow.json")
    workflow.register_candidate_version(
        slot_id=character_state_slot_id("陆辰", identity.identity_id),
        asset_kind="character_state",
        version_id="state-v1",
        asset_path=image_path.relative_to(tmp_path).as_posix(),
        source_attempt_id=None,
        qc_passed=True,
        generation_metadata={"identity_id": identity.identity_id},
        actor="test",
        at=datetime.now(UTC),
    )

    assert _available_character_identity_ids(
        ctx=ctx, characters=(character,)
    ) == frozenset({identity.identity_id})


def test_identity_runner_materializes_safe_legacy_identity_reference(tmp_path):
    from PIL import Image

    from novelvideo.production_workflow import ProductionWorkflowStore
    from novelvideo.task_backend.runners.identity import _available_character_identity_ids

    image_path = tmp_path / "assets" / "characters" / "陆辰" / "identities" / "legacy.png"
    image_path.parent.mkdir(parents=True)
    Image.new("RGB", (8, 8), "red").save(image_path)
    identity = CharacterIdentity(
        identity_id="陆辰_青年时期",
        character_name="陆辰",
        identity_name="青年时期",
        reference_images=[image_path.relative_to(tmp_path).as_posix()],
    )
    ctx = SimpleNamespace(output_dir=tmp_path, state_dir=tmp_path / "state")

    assert _available_character_identity_ids(
        ctx=ctx, characters=(_character("陆辰", identity),)
    ) == frozenset({identity.identity_id})
    slot, versions = ProductionWorkflowStore(
        ctx.state_dir / "production_workflow.json"
    ).get_slot(character_state_slot_id("陆辰", identity.identity_id))
    assert slot.asset_kind == "character_state"
    assert versions[slot.current_version_id].asset_path.startswith(
        "assets/characters/陆辰/identities/_workflow_versions/legacy-"
    )
    assert (tmp_path / versions[slot.current_version_id].asset_path).read_bytes() == (
        image_path.read_bytes()
    )


def test_identity_runner_rejects_character_directory_symlink(tmp_path):
    from PIL import Image

    from novelvideo.task_backend.runners.identity import _available_character_identity_ids

    external = tmp_path / "assets" / "scenes" / "villain"
    external.mkdir(parents=True)
    image_path = external / "identity.png"
    Image.new("RGB", (8, 8), "red").save(image_path)
    characters_root = tmp_path / "assets" / "characters"
    characters_root.mkdir(parents=True)
    (characters_root / "陆辰").symlink_to(external, target_is_directory=True)
    identity = CharacterIdentity(
        identity_id="陆辰_青年时期",
        character_name="陆辰",
        identity_name="青年时期",
        reference_images=[image_path.relative_to(tmp_path).as_posix()],
    )
    ctx = SimpleNamespace(output_dir=tmp_path, state_dir=tmp_path / "state")

    assert _available_character_identity_ids(
        ctx=ctx, characters=(_character("陆辰", identity),)
    ) == frozenset()
    assert not (ctx.state_dir / "production_workflow.json").exists()


def test_identity_runner_rejects_characters_root_symlink(tmp_path):
    from PIL import Image

    from novelvideo.task_backend.runners.identity import _available_character_identity_ids

    external = tmp_path / "assets" / "scenes"
    character_root = external / "陆辰"
    character_root.mkdir(parents=True)
    image_path = character_root / "identity.png"
    Image.new("RGB", (8, 8), "red").save(image_path)
    (tmp_path / "assets" / "characters").symlink_to(
        external, target_is_directory=True
    )
    identity = CharacterIdentity(
        identity_id="陆辰_青年时期",
        character_name="陆辰",
        identity_name="青年时期",
        reference_images=[image_path.relative_to(tmp_path).as_posix()],
    )
    ctx = SimpleNamespace(output_dir=tmp_path, state_dir=tmp_path / "state")

    assert _available_character_identity_ids(
        ctx=ctx, characters=(_character("陆辰", identity),)
    ) == frozenset()
    assert not (ctx.state_dir / "production_workflow.json").exists()
    assert not (character_root / "identities" / "_workflow_versions").exists()


def test_identity_legacy_snapshot_rejects_changed_copy(tmp_path, monkeypatch):
    from PIL import Image

    from novelvideo.task_backend.runners import identity as identity_runner

    image_path = tmp_path / "assets" / "characters" / "陆辰" / "identities" / "legacy.png"
    image_path.parent.mkdir(parents=True)
    Image.new("RGB", (8, 8), "red").save(image_path)
    identity = CharacterIdentity(
        identity_id="陆辰_青年时期",
        character_name="陆辰",
        identity_name="青年时期",
        reference_images=[image_path.relative_to(tmp_path).as_posix()],
    )

    def replace_during_copy(_source, destination):
        Image.new("RGB", (8, 8), "blue").save(destination)

    monkeypatch.setattr(identity_runner.shutil, "copy2", replace_during_copy)
    ctx = SimpleNamespace(output_dir=tmp_path, state_dir=tmp_path / "state")

    assert identity_runner._available_character_identity_ids(
        ctx=ctx, characters=(_character("陆辰", identity),)
    ) == frozenset()
    assert not (ctx.state_dir / "production_workflow.json").exists()


def test_character_name_requirement_uses_only_identity_for_legacy_episode():
    only_identity = _identity("陆辰_默认", with_image=True)

    binding = _project_character_binding(
        "陆辰",
        characters=(_character("陆辰", only_identity),),
    )

    assert binding.entity_id == only_identity.identity_id
    assert binding.status == "ready"


def test_character_name_requirement_does_not_guess_between_episode_identities():
    young = _identity("陆辰_青年时期", with_image=True)
    fighter = _identity("陆辰_战斗装", with_image=True)

    binding = _project_character_binding(
        "陆辰",
        characters=(_character("陆辰", young, fighter),),
        episode_identity_ids=(young.identity_id, fighter.identity_id),
    )

    assert binding.entity_id == "陆辰"
    assert binding.status == "pending_confirmation"
    assert binding.asset_slot_id == ""


def test_planned_phase_alias_resolves_only_within_same_character():
    matching = _identity("陆辰_青年时期", with_image=True)
    other_character = _identity("沈砚_青年时期", with_image=True)

    binding = _project_character_binding(
        "陆辰_青年期",
        characters=(
            _character("陆辰", matching),
            _character("沈砚", other_character),
        ),
        episode_identity_ids=(matching.identity_id, other_character.identity_id),
    )

    assert binding.entity_id == matching.identity_id
    assert binding.status == "ready"


def test_resolved_duplicate_bindings_merge_scope_and_required():
    identity = _identity("陆辰_青年时期", with_image=True)
    shot_one = SimpleNamespace(
        id="shot-1",
        dramatic_beat_ids=(),
        asset_requirements=(
            SimpleNamespace(
                kind="character_identity", entity_key="陆辰", required=False
            ),
        ),
    )
    shot_two = SimpleNamespace(
        id="shot-2",
        dramatic_beat_ids=(),
        asset_requirements=(
            SimpleNamespace(
                kind="character_identity", entity_key="陆辰_青年期", required=True
            ),
        ),
    )

    bindings = bindings_for_director_plan(
        project_id="owner/project",
        episode_number=1,
        source_plan_revision_id="director-r2",
        groups=(
            SimpleNamespace(id="group-1", beat_ids=("beat-1",), shots=(shot_one,)),
            SimpleNamespace(id="group-2", beat_ids=("beat-2",), shots=(shot_two,)),
        ),
        shots=(shot_one, shot_two),
        characters=(_character("陆辰", identity),),
        scenes=(),
        props=(),
        episode_identity_ids=(identity.identity_id,),
        identity_default_map={"陆辰": identity.identity_id},
    )

    assert len(bindings) == 1
    assert bindings[0].entity_id == identity.identity_id
    assert bindings[0].group_ids == ("group-1", "group-2")
    assert bindings[0].beat_ids == ("beat-1", "beat-2")
    assert bindings[0].shot_ids == ("shot-1", "shot-2")
    assert bindings[0].required is True


def test_exact_identity_id_keeps_matching_outside_episode_selection():
    exact = _identity("陆辰_少年时期", with_image=True)

    binding = _project_character_binding(
        exact.identity_id,
        characters=(_character("陆辰", exact),),
        episode_identity_ids=(),
    )

    assert binding.entity_id == exact.identity_id
    assert binding.status == "ready"


def test_internal_binding_keeps_legacy_call_signature():
    exact = _identity("陆辰_少年时期", with_image=True)

    binding = _project_binding(
        _ProjectedRequirement(kind="character_identity", entity_key=exact.identity_id),
        project_id="owner/project",
        episode_number=1,
        source_plan_revision_id="director-r2",
        characters=(_character("陆辰", exact),),
        scenes=(),
        props=(),
    )

    assert binding.entity_id == exact.identity_id
    assert binding.status == "ready"


def test_character_binding_projection_uses_default_identity_from_draft():
    alternate_identity = _identity("陆辰_日常装", with_image=True)
    new_identity = _identity("陆辰_战斗装", with_image=True)
    draft = IdentityPlanDraft(
        new_count=1,
        resolved_count=1,
        characters=(_character("陆辰", alternate_identity, new_identity),),
        episode_identity_ids=(
            alternate_identity.identity_id,
            new_identity.identity_id,
        ),
        identity_default_map={"陆辰": new_identity.identity_id},
        identity_baseline_digests={"陆辰": "baseline"},
        episode_identity_baseline_digest="episode-baseline",
    )
    shot = SimpleNamespace(
        id="shot-1",
        dramatic_beat_ids=("beat-1",),
        asset_requirements=(
            SimpleNamespace(
                kind="character_identity",
                entity_key="陆辰",
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


def test_character_binding_projection_forwards_available_portraits():
    identity = _identity("陆辰_战斗装")
    draft = IdentityPlanDraft(
        new_count=1,
        resolved_count=0,
        characters=(_character("陆辰", identity),),
        episode_identity_ids=(identity.identity_id,),
        identity_default_map={"陆辰": identity.identity_id},
        identity_baseline_digests={"陆辰": "baseline"},
        episode_identity_baseline_digest="episode-baseline",
    )
    shot = SimpleNamespace(
        id="shot-1",
        dramatic_beat_ids=(),
        asset_requirements=(
            SimpleNamespace(
                kind="character_identity",
                entity_key="陆辰",
                required=True,
            ),
        ),
    )
    plan = SimpleNamespace(
        revision_id="director-r2",
        groups=(SimpleNamespace(id="group-1", beat_ids=(), shots=(shot,)),),
    )

    bindings = _character_identity_bindings(
        project_id="owner/project",
        episode_number=1,
        director_plan=plan,
        draft=draft,
        characters=(_character("陆辰"),),
        scenes=(),
        props=(),
        available_character_portraits=("陆辰",),
    )

    assert bindings[0].status == "ready"
    assert bindings[0].asset_slot_id == character_portrait_slot_id("陆辰")
    assert bindings[0].resolution == "explicit_fallback"


def test_identity_runner_materializes_safe_legacy_character_portrait(tmp_path):
    from PIL import Image

    from novelvideo.production_workflow import ProductionWorkflowStore
    from novelvideo.task_backend.runners.identity import _available_character_portraits

    portrait = tmp_path / "assets" / "characters" / "陆辰" / "portrait.png"
    portrait.parent.mkdir(parents=True)
    Image.new("RGB", (8, 8), "red").save(portrait)
    ctx = SimpleNamespace(output_dir=tmp_path, state_dir=tmp_path / "state")

    available = _available_character_portraits(
        ctx=ctx,
        characters=(_character("陆辰"),),
    )

    assert available == frozenset({"陆辰"})
    slot, versions = ProductionWorkflowStore(
        tmp_path / "state" / "production_workflow.json"
    ).get_slot(character_portrait_slot_id("陆辰"))
    assert slot.asset_kind == "character_portrait"
    assert slot.current_version_id
    current = versions[slot.current_version_id]
    assert current.asset_path.startswith(
        "assets/characters/陆辰/portrait_versions/legacy-"
    )
    immutable_path = tmp_path / current.asset_path
    assert immutable_path.read_bytes() == portrait.read_bytes()


def test_identity_runner_migrates_mutable_legacy_current_before_reconcile(tmp_path):
    from PIL import Image

    from novelvideo.production_workflow import ProductionWorkflowStore
    from novelvideo.task_backend.runners.identity import _available_character_portraits

    portrait = tmp_path / "assets" / "characters" / "陆辰" / "portrait.png"
    portrait.parent.mkdir(parents=True)
    Image.new("RGB", (8, 8), "red").save(portrait)
    original_bytes = portrait.read_bytes()
    state_dir = tmp_path / "state"
    workflow = ProductionWorkflowStore(state_dir / "production_workflow.json")
    workflow.materialize_legacy_current(
        slot_id=character_portrait_slot_id("陆辰"),
        asset_kind="character_portrait",
        asset_path="assets/characters/陆辰/portrait.png",
    )
    old_slot, _old_versions = workflow.get_slot(character_portrait_slot_id("陆辰"))
    old_version_id = old_slot.current_version_id

    ctx = SimpleNamespace(output_dir=tmp_path, state_dir=state_dir)
    assert _available_character_portraits(
        ctx=ctx,
        characters=(_character("陆辰"),),
    ) == frozenset({"陆辰"})
    migrated = ProductionWorkflowStore(state_dir / "production_workflow.json")
    slot, versions = migrated.get_slot(character_portrait_slot_id("陆辰"))
    current = versions[slot.current_version_id]
    assert current.asset_path.startswith(
        "assets/characters/陆辰/portrait_versions/legacy-"
    )
    immutable_path = tmp_path / current.asset_path
    assert immutable_path.read_bytes() == original_bytes
    assert versions[old_version_id].asset_path == current.asset_path

    Image.new("RGB", (8, 8), "blue").save(portrait)
    assert _available_character_portraits(
        ctx=ctx,
        characters=(_character("陆辰"),),
    ) == frozenset({"陆辰"})
    assert portrait.read_bytes() == original_bytes


def test_identity_runner_does_not_readopt_rejected_mutable_legacy_current(tmp_path):
    import json

    from PIL import Image

    from novelvideo.production_workflow import ProductionWorkflowStore
    from novelvideo.task_backend.runners.identity import _available_character_portraits

    portrait = tmp_path / "assets" / "characters" / "陆辰" / "portrait.png"
    portrait.parent.mkdir(parents=True)
    Image.new("RGB", (8, 8), "red").save(portrait)
    state_dir = tmp_path / "state"
    state_path = state_dir / "production_workflow.json"
    workflow = ProductionWorkflowStore(state_path)
    workflow.materialize_legacy_current(
        slot_id=character_portrait_slot_id("陆辰"),
        asset_kind="character_portrait",
        asset_path="assets/characters/陆辰/portrait.png",
    )
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    payload["versions"][0]["adoption_status"] = "rejected"
    state_path.write_text(json.dumps(payload), encoding="utf-8")

    available = _available_character_portraits(
        ctx=SimpleNamespace(output_dir=tmp_path, state_dir=state_dir),
        characters=(_character("陆辰"),),
    )

    assert available == frozenset()
    slot, versions = ProductionWorkflowStore(state_path).get_slot(
        character_portrait_slot_id("陆辰")
    )
    assert versions[slot.current_version_id].adoption_status.value == "rejected"
    assert versions[slot.current_version_id].asset_path.endswith("/portrait.png")


def test_identity_runner_does_not_overwrite_wrong_kind_portrait_slot(tmp_path):
    from datetime import UTC, datetime

    from PIL import Image

    from novelvideo.production_workflow import ProductionWorkflowStore
    from novelvideo.task_backend.runners.identity import _available_character_portraits

    portrait = tmp_path / "assets" / "characters" / "陆辰" / "portrait.png"
    portrait.parent.mkdir(parents=True)
    Image.new("RGB", (8, 8), "red").save(portrait)
    state_dir = tmp_path / "state"
    workflow = ProductionWorkflowStore(state_dir / "production_workflow.json")
    workflow.register_candidate_version(
        slot_id=character_portrait_slot_id("陆辰"),
        asset_kind="character_state",
        version_id="wrong-kind-v1",
        asset_path="assets/characters/陆辰/portrait.png",
        source_attempt_id=None,
        qc_passed=True,
        generation_metadata=None,
        actor="test",
        at=datetime.now(UTC),
    )

    available = _available_character_portraits(
        ctx=SimpleNamespace(output_dir=tmp_path, state_dir=state_dir),
        characters=(_character("陆辰"),),
    )

    assert available == frozenset()
    slot, _versions = ProductionWorkflowStore(
        state_dir / "production_workflow.json"
    ).get_slot(character_portrait_slot_id("陆辰"))
    assert slot.asset_kind == "character_state"


def test_identity_runner_rejects_cross_asset_legacy_portrait_path(tmp_path):
    from PIL import Image

    from novelvideo.task_backend.runners.identity import _available_character_portraits

    misplaced = tmp_path / "assets" / "scenes" / "villain" / "portrait.png"
    misplaced.parent.mkdir(parents=True)
    Image.new("RGB", (8, 8), "red").save(misplaced)

    available = _available_character_portraits(
        ctx=SimpleNamespace(output_dir=tmp_path, state_dir=tmp_path / "state"),
        characters=(_character("../scenes/villain"),),
    )

    assert available == frozenset()
    assert not (tmp_path / "state" / "production_workflow.json").exists()


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
        episode_identity_baseline_digest=store.identity_episode_baseline_digest(
            [old_identity.identity_id], {"陆辰": old_identity.identity_id}
        ),
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
@pytest.mark.parametrize("refresh_failure", ["false", "exception"])
async def test_identity_commit_reports_cache_refresh_pending_without_rolling_back(
    tmp_path, monkeypatch, refresh_failure
):
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
            identity_ids=[old_identity.identity_id],
            identity_default_map={"陆辰": old_identity.identity_id},
        )
    )
    original_refresh = store.load_graph_state

    async def fail_refresh():
        if refresh_failure == "false":
            return False
        raise RuntimeError("cache unavailable")

    monkeypatch.setattr(store, "load_graph_state", fail_refresh)
    result = await store.publish_identity_plan_atomic(
        episode_number=1,
        characters=(_character("陆辰", old_identity, new_identity),),
        episode_identity_ids=(new_identity.identity_id,),
        identity_default_map={"陆辰": new_identity.identity_id},
        identity_baseline_digests={
            "陆辰": hashlib.sha256(
                _character("陆辰", old_identity).identities_json.encode()
            ).hexdigest()
        },
        episode_identity_baseline_digest=store.identity_episode_baseline_digest(
            [old_identity.identity_id], {"陆辰": old_identity.identity_id}
        ),
        bindings=(_binding(new_identity.identity_id, revision="director-r2"),),
    )

    assert result == {"committed": True, "cache_refresh_pending": True}
    assert [
        item.identity_id for item in (await store.list_characters())[0].identities
    ] == [old_identity.identity_id, new_identity.identity_id]
    assert (await store.list_episodes())[0].identity_ids == [new_identity.identity_id]
    assert await store.list_planned_reference_bindings(1) == [
        _binding(new_identity.identity_id, revision="director-r2")
    ]
    assert store._cache_refresh_pending is True

    monkeypatch.setattr(store, "load_graph_state", original_refresh)
    await store.load_graph_state()
    assert store._cache_refresh_pending is False


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
            episode_identity_baseline_digest=store.identity_episode_baseline_digest(
                [old_identity.identity_id], {"陆辰": old_identity.identity_id}
            ),
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
        episode_identity_baseline_digest=store.identity_episode_baseline_digest([], {}),
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
            episode_identity_baseline_digest=store.identity_episode_baseline_digest(
                [old_identity.identity_id], {"陆辰": old_identity.identity_id}
            ),
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


@pytest.mark.asyncio
async def test_publish_rejects_concurrent_episode_identity_mapping_change(tmp_path):
    store = SQLiteStore(
        "owner/project", output_dir=str(tmp_path), state_dir=str(tmp_path)
    )
    await store.initialize()
    old_identity = _identity("陆辰_默认")
    planned_identity = _identity("陆辰_战斗装")
    original = _character("陆辰", old_identity)
    old_binding = _binding(old_identity.identity_id, revision="director-r1")
    episode = NovelEpisode(
        number=1,
        title="第一集",
        identity_ids=[old_identity.identity_id],
        identity_default_map={"陆辰": old_identity.identity_id},
    )
    await store.add_character(original)
    await store.add_episode(episode)
    await store.replace_planned_reference_bindings_atomic(
        1, ("character_identity",), (old_binding,)
    )
    baseline = store.identity_episode_baseline_digest(
        episode.identity_ids, episode.identity_default_map
    )
    await store.update_episode(
        1,
        identity_ids=["陆辰_人工身份"],
        identity_default_map={"陆辰": "陆辰_人工身份"},
    )

    with pytest.raises(ValueError, match="identity plan episode conflict"):
        await store.publish_identity_plan_atomic(
            episode_number=1,
            characters=(_character("陆辰", old_identity, planned_identity),),
            episode_identity_ids=(planned_identity.identity_id,),
            identity_default_map={"陆辰": planned_identity.identity_id},
            identity_baseline_digests={
                "陆辰": hashlib.sha256(original.identities_json.encode()).hexdigest()
            },
            episode_identity_baseline_digest=baseline,
            bindings=(_binding(planned_identity.identity_id, revision="director-r2"),),
        )

    persisted = (await store.list_episodes())[0]
    assert persisted.identity_ids == ["陆辰_人工身份"]
    assert persisted.identity_default_map == {"陆辰": "陆辰_人工身份"}
    assert [item.identity_id for item in (await store.list_characters())[0].identities] == [
        old_identity.identity_id
    ]
    assert await store.list_planned_reference_bindings(1) == [old_binding]


def test_director_plan_publication_guard_serializes_activation(tmp_path):
    store = DirectorPlanStore(tmp_path)
    store.save(make_revision("rev-old"))
    store.save(make_revision("rev-new"))
    store.activate(1, "rev-old")
    publication_entered = threading.Event()
    release_publication = threading.Event()
    activation_finished = threading.Event()
    published_revision_ids = []

    def publish_under_guard():
        with store.lock_active_revision(1) as revision:
            published_revision_ids.append(revision.revision_id)
            publication_entered.set()
            release_publication.wait(timeout=5)

    def activate_new_revision():
        publication_entered.wait(timeout=5)
        store.activate(1, "rev-new")
        activation_finished.set()

    publisher = threading.Thread(target=publish_under_guard)
    activator = threading.Thread(target=activate_new_revision)
    publisher.start()
    activator.start()
    assert publication_entered.wait(timeout=5)
    time.sleep(0.05)
    assert not activation_finished.is_set()
    release_publication.set()
    publisher.join(timeout=5)
    activator.join(timeout=5)

    assert not publisher.is_alive()
    assert not activator.is_alive()
    assert published_revision_ids == ["rev-old"]
    assert activation_finished.is_set()
    assert store.load_active(1).revision_id == "rev-new"


@pytest.mark.asyncio
async def test_publisher_captures_deep_snapshot_before_first_await(tmp_path, monkeypatch):
    store = SQLiteStore(
        "owner/project", output_dir=str(tmp_path), state_dir=str(tmp_path)
    )
    await store.initialize()
    old_identity = _identity("陆辰_默认")
    planned_identity = _identity("陆辰_战斗装")
    character = _character("陆辰", old_identity, planned_identity)
    original = _character("陆辰", old_identity)
    await store.add_character(original)
    await store.add_episode(NovelEpisode(number=1, title="第一集"))
    identity_ids = [planned_identity.identity_id]
    default_map = {"陆辰": planned_identity.identity_id}
    entered = asyncio.Event()
    release = asyncio.Event()
    original_ensure_db = store._ensure_db

    async def gated_ensure_db():
        entered.set()
        await release.wait()
        return await original_ensure_db()

    monkeypatch.setattr(store, "_ensure_db", gated_ensure_db)
    publication = asyncio.create_task(
        store.publish_identity_plan_atomic(
            episode_number=1,
            characters=(character,),
            episode_identity_ids=identity_ids,
            identity_default_map=default_map,
            identity_baseline_digests={
                "陆辰": hashlib.sha256(original.identities_json.encode()).hexdigest()
            },
            episode_identity_baseline_digest=store.identity_episode_baseline_digest(
                [], {}
            ),
            bindings=(_binding(planned_identity.identity_id, revision="director-r2"),),
        )
    )
    await entered.wait()
    character.identities = [old_identity, _identity("陆辰_外部篡改")]
    identity_ids[:] = ["陆辰_外部篡改"]
    default_map["陆辰"] = "陆辰_外部篡改"
    release.set()
    await publication

    persisted_character = (await store.list_characters())[0]
    persisted_episode = (await store.list_episodes())[0]
    assert [item.identity_id for item in persisted_character.identities] == [
        old_identity.identity_id,
        planned_identity.identity_id,
    ]
    assert persisted_episode.identity_ids == [planned_identity.identity_id]
    assert persisted_episode.identity_default_map == {"陆辰": planned_identity.identity_id}


@pytest.mark.asyncio
async def test_atomic_legacy_publish_with_none_bindings_preserves_existing_bindings(tmp_path):
    store = SQLiteStore(
        "owner/project", output_dir=str(tmp_path), state_dir=str(tmp_path)
    )
    await store.initialize()
    old_identity = _identity("陆辰_默认")
    old_binding = _binding(old_identity.identity_id, revision="director-r1")
    await store.add_character(_character("陆辰", old_identity))
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

    await store.publish_identity_plan_atomic(
        episode_number=1,
        characters=(),
        episode_identity_ids=(old_identity.identity_id,),
        identity_default_map={"陆辰": old_identity.identity_id},
        identity_baseline_digests={},
        episode_identity_baseline_digest=store.identity_episode_baseline_digest(
            [old_identity.identity_id], {"陆辰": old_identity.identity_id}
        ),
        bindings=None,
    )

    assert await store.list_planned_reference_bindings(1) == [old_binding]


class DraftReturningPlanner(IdentityPlanner):
    def __init__(self, store, draft):
        super().__init__(store)
        self.draft = draft
        self.built = asyncio.Event()
        self.release = asyncio.Event()

    async def _build_identity_plan_draft_unlocked(self, episode, on_log=None):
        self.built.set()
        await self.release.wait()
        return self.draft


@pytest.mark.asyncio
async def test_legacy_sqlite_planner_conflict_rolls_back_without_touching_bindings(tmp_path):
    store_one = SQLiteStore(
        "owner/project", output_dir=str(tmp_path), state_dir=str(tmp_path)
    )
    store_two = SQLiteStore(
        "owner/project", output_dir=str(tmp_path), state_dir=str(tmp_path)
    )
    await store_one.initialize()
    await store_two.initialize()
    old_identity = _identity("陆辰_默认")
    planned_identity = _identity("陆辰_战斗装")
    original = _character("陆辰", old_identity)
    old_binding = _binding(old_identity.identity_id, revision="director-r1")
    episode = NovelEpisode(
        number=1,
        title="第一集",
        identity_ids=[old_identity.identity_id],
        identity_default_map={"陆辰": old_identity.identity_id},
    )
    await store_one.add_character(original)
    await store_one.add_episode(episode)
    await store_one.replace_planned_reference_bindings_atomic(
        1, ("character_identity",), (old_binding,)
    )
    await store_one.load_graph_state()
    await store_two.load_graph_state()
    draft = IdentityPlanDraft(
        new_count=1,
        resolved_count=1,
        characters=(_character("陆辰", old_identity, planned_identity),),
        episode_identity_ids=(planned_identity.identity_id,),
        identity_default_map={"陆辰": planned_identity.identity_id},
        identity_baseline_digests={
            "陆辰": hashlib.sha256(original.identities_json.encode()).hexdigest()
        },
        episode_identity_baseline_digest=store_one.identity_episode_baseline_digest(
            episode.identity_ids, episode.identity_default_map
        ),
    )
    first = DraftReturningPlanner(store_one, draft)
    second = IdentityPlanner(store_two)

    publication = asyncio.create_task(first.plan_single_episode(episode))
    await first.built.wait()
    await second.cognee_store.update_episode(
        1,
        identity_ids=["陆辰_并发身份"],
        identity_default_map={"陆辰": "陆辰_并发身份"},
    )
    first.release.set()
    with pytest.raises(ValueError, match="identity plan episode conflict"):
        await publication

    persisted_character = (await store_one.list_characters())[0]
    persisted_episode = (await store_one.list_episodes())[0]
    assert [item.identity_id for item in persisted_character.identities] == [
        old_identity.identity_id
    ]
    assert persisted_episode.identity_ids == ["陆辰_并发身份"]
    assert await store_one.list_planned_reference_bindings(1) == [old_binding]
