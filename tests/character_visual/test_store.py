from concurrent.futures import ThreadPoolExecutor
from threading import Event

from novelvideo.character_visual import (
    CharacterDesignProposal,
    CharacterNarrativeProfile,
    CharacterVisualBible,
    CharacterVisualWorkspace,
    CharacterVisualWorkspaceStore,
)
import pytest


def _workspace(status: str = "draft") -> CharacterVisualWorkspace:
    return CharacterVisualWorkspace(
        character_id="林默",
        profile=CharacterNarrativeProfile(character_id="林默", name="林默"),
        visual_bible=CharacterVisualBible(
            character_id="林默",
            revision_id="vb-1",
            status=status,
            confirmed_by="director" if status == "confirmed" else None,
            face_shape="窄脸",
            facial_features=["深眼窝", "鼻梁偏直"],
            distinctive_features=["左眉尾断眉"],
            identity_anchors=["窄脸", "深眼窝", "左眉尾断眉"],
        ),
    )


def test_workspace_store_roundtrips_project_local_state(tmp_path):
    store = CharacterVisualWorkspaceStore(tmp_path)
    store.save(_workspace())

    restored = store.get("林默")
    assert restored is not None
    assert restored.visual_bible is not None
    assert restored.visual_bible.face_shape == "窄脸"
    assert store.path == tmp_path / "state" / "character_visual_workspaces.json"


def test_only_confirmed_bible_is_available_to_generation(tmp_path):
    store = CharacterVisualWorkspaceStore(tmp_path)
    store.save(_workspace("draft"))
    assert store.get_confirmed_bible("林默") is None

    store.save(_workspace("confirmed"))
    assert store.get_confirmed_bible("林默").revision_id == "vb-1"


def test_workspace_store_saves_a_batch_with_one_atomic_write(tmp_path, monkeypatch):
    store = CharacterVisualWorkspaceStore(tmp_path)
    first = _workspace()
    second = CharacterVisualWorkspace(
        character_id="沈青",
        profile=CharacterNarrativeProfile(character_id="沈青", name="沈青"),
    )
    writes = []
    real_write_all = store._write_all

    def recording_write(payload):
        writes.append(payload)
        real_write_all(payload)

    monkeypatch.setattr(store, "_write_all", recording_write)

    saved = store.save_many([first, second])

    assert saved == [first, second]
    assert len(writes) == 1
    assert store.get("林默") == first
    assert store.get("沈青") == second


def test_workspace_store_rejects_duplicate_ids_without_writing(tmp_path, monkeypatch):
    store = CharacterVisualWorkspaceStore(tmp_path)
    writes = []
    monkeypatch.setattr(store, "_write_all", writes.append)

    with pytest.raises(ValueError, match="duplicate character_id"):
        store.save_many([_workspace(), _workspace()])

    assert writes == []


def test_workspace_store_serializes_writes_across_store_instances(
    tmp_path, monkeypatch
):
    first_store = CharacterVisualWorkspaceStore(tmp_path)
    second_store = CharacterVisualWorkspaceStore(tmp_path)
    first_entered_write = Event()
    release_first_write = Event()
    second_entered_write = Event()
    real_first_write = first_store._write_all
    real_second_write = second_store._write_all

    def blocked_first_write(payload):
        first_entered_write.set()
        assert release_first_write.wait(timeout=2)
        real_first_write(payload)

    def observed_second_write(payload):
        second_entered_write.set()
        real_second_write(payload)

    monkeypatch.setattr(first_store, "_write_all", blocked_first_write)
    monkeypatch.setattr(second_store, "_write_all", observed_second_write)
    second = CharacterVisualWorkspace(
        character_id="沈青",
        profile=CharacterNarrativeProfile(character_id="沈青", name="沈青"),
    )

    with ThreadPoolExecutor(max_workers=2) as executor:
        first_future = executor.submit(first_store.save, _workspace())
        assert first_entered_write.wait(timeout=2)
        second_future = executor.submit(second_store.save, second)

        try:
            assert not second_entered_write.wait(timeout=0.15)
        finally:
            release_first_write.set()
        first_future.result(timeout=2)
        second_future.result(timeout=2)

    assert first_store.get("林默") is not None
    assert first_store.get("沈青") == second


def test_save_many_merges_automatic_fields_without_overwriting_manual_state(tmp_path):
    automatic_store = CharacterVisualWorkspaceStore(tmp_path)
    user_store = CharacterVisualWorkspaceStore(tmp_path)
    proposal = CharacterDesignProposal(
        proposal_id="proposal-a",
        title="窄脸断眉",
        face_shape="窄脸",
        facial_features=["深眼窝", "鼻梁偏直"],
        distinctive_features=["左眉尾断眉"],
        identity_anchors=["窄脸", "深眼窝", "左眉尾断眉"],
    )
    stale_automatic_workspace = _workspace("draft").model_copy(
        update={
            "profile": CharacterNarrativeProfile(
                character_id="林默",
                name="林默",
                biography="自动提取的新人物小传",
            ),
            "design_proposals": [proposal],
            "selected_proposal_id": None,
        }
    )
    automatic_store.save(stale_automatic_workspace)

    confirmed_bible = stale_automatic_workspace.visual_bible.model_copy(
        update={"status": "confirmed", "confirmed_by": "director"}
    )
    user_store.save(
        stale_automatic_workspace.model_copy(
            update={
                "selected_proposal_id": "proposal-a",
                "visual_bible": confirmed_bible,
            }
        )
    )

    automatic_store.save_many(
        [
            stale_automatic_workspace.model_copy(
                update={
                    "profile": stale_automatic_workspace.profile.model_copy(
                        update={"biography": "第二轮自动提取的小传"}
                    ),
                    "selected_proposal_id": None,
                    "visual_bible": stale_automatic_workspace.visual_bible,
                }
            )
        ]
    )

    restored = automatic_store.get("林默")
    assert restored is not None
    assert restored.profile.biography == "第二轮自动提取的小传"
    assert restored.selected_proposal_id == "proposal-a"
    assert restored.visual_bible == confirmed_bible
