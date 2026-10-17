from __future__ import annotations

import hashlib
import pytest


def _preparation(preparation_id: str, dialogue_id: str) -> object:
    from novelvideo.audio.dubbing_contracts import DialogueSnapshot, DubbingPreparation

    dialogue = DialogueSnapshot(
        dialogue_id=dialogue_id,
        character_id="character-1",
        identity_id="identity-adult",
        script_revision="script-r1",
        source_revision="director-r1",
        group_id="group-1",
        text="别进去。",
        voice_version_id="voice-v1",
    )
    digest = hashlib.sha256(dialogue.model_dump_json().encode()).hexdigest()
    return DubbingPreparation(
        preparation_id=preparation_id,
        episode=1,
        group_id="group-1",
        script_revision="script-r1",
        source_revision="director-r1",
        expected_revision=0,
        source_snapshot_hash=digest,
        dialogue_ids=(dialogue_id,),
        dialogues=(dialogue,),
    )


def _line(dialogue_id: str, text: str = "别进去。") -> dict:
    return {
        "dialogue_id": dialogue_id,
        "character_id": "character-1",
        "identity_id": "identity-adult",
        "script_revision": "script-r1",
        "group_id": "group-1",
        "text": text,
        "voice_version_id": "voice-v1",
        "performance": {"emotion": "克制紧张"},
    }


def test_line_input_hash_ignores_layout_and_group_source_revision() -> None:
    from novelvideo.audio.dubbing_store import line_input_hash

    original = _line("dialogue-1")
    moved = {
        **original,
        "canvas_x": 500,
        "canvas_y": 700,
        "script_revision": "script-r2",
        "source_revision": "director-r2",
    }

    assert line_input_hash(original) == line_input_hash(moved)
    assert line_input_hash(original) != line_input_hash({**original, "text": "进去吧。"})
    assert line_input_hash(original) != line_input_hash({**original, "voice_version_id": "voice-v2"})


def test_preparation_id_is_immutable_and_exact_retry_returns_saved_receipt(tmp_path) -> None:
    from novelvideo.audio.dubbing_store import DubbingRequestConflict, DubbingStore

    store = DubbingStore(tmp_path / "data.db", tmp_path)
    preparation = _preparation("prep-stable", "dialogue-1")

    saved = store.save_preparation(preparation)
    retried = store.save_preparation(preparation)

    assert retried == saved
    with pytest.raises(DubbingRequestConflict, match="preparation id reused"):
        store.save_preparation(preparation.model_copy(update={"request_fingerprint": "a" * 64}))


def test_legacy_dialogue_import_keeps_old_takes_and_never_reuses_ids_after_source_revision(tmp_path) -> None:
    from novelvideo.audio.dubbing_contracts import DubbingTake
    from novelvideo.audio.dubbing_store import DubbingStore

    store = DubbingStore(tmp_path / "data.db", tmp_path)
    source = [
        {"source_key": "span-0:line-0", **_line("", "旧台词。")},
        {"source_key": "span-0:line-1", **_line("", "重复。")},
        {"source_key": "span-0:line-2", **_line("", "重复。")},
    ]
    first = store.import_legacy_dialogues(
        episode=1, group_id="group-1", source_revision="director-r1", lines=source
    )
    repeated = store.import_legacy_dialogues(
        episode=1, group_id="group-1", source_revision="director-r1", lines=source
    )

    assert [item.dialogue_id for item in first] == [item.dialogue_id for item in repeated]
    assert first[1].dialogue_id != first[2].dialogue_id
    old_id = first[0].dialogue_id
    store.save_preparation(_preparation("prep-old", old_id))
    store.save_take(
        DubbingTake(
            take_id="take-old",
            preparation_id="prep-old",
            task_id="task-old",
            dialogue_id=old_id,
            input_hash="a" * 64,
            voice_version_id="voice-v1",
            audio_path="assets/dubbing/take-old.wav",
            audio_sha256="b" * 64,
            actual_duration_seconds=2.5,
            status="succeeded",
        )
    )

    # A changed source gets fresh IDs. An inserted line cannot silently inherit
    # a previous take merely because it now occupies the same array position.
    revised = store.import_legacy_dialogues(
        episode=1,
        group_id="group-1",
        source_revision="director-r2",
        lines=[
            {"source_key": "span-0:line-0", **_line("", "新增台词。")},
            {"source_key": "span-0:line-1", **_line("", "旧台词。")},
        ],
    )
    assert old_id not in {item.dialogue_id for item in revised}
    assert store.get_take("take-old").dialogue_id == old_id


def test_take_is_immutable_and_adoption_uses_revision_cas(tmp_path) -> None:
    from novelvideo.audio.dubbing_contracts import DubbingTake
    from novelvideo.audio.dubbing_store import DubbingStore, DubbingRevisionConflict

    store = DubbingStore(tmp_path / "data.db", tmp_path)
    store.save_preparation(_preparation("prep-1", "dialogue-1"))
    take = DubbingTake(
        take_id="take-1",
        preparation_id="prep-1",
        task_id="task-1",
        dialogue_id="dialogue-1",
        input_hash="a" * 64,
        voice_version_id="voice-v1",
        audio_path="assets/dubbing/take-1.wav",
        audio_sha256="b" * 64,
        actual_duration_seconds=2.5,
        status="succeeded",
    )
    store.save_take(take)

    with pytest.raises(ValueError, match="immutable"):
        store.save_take(take.model_copy(update={"audio_sha256": "c" * 64}))

    adopted = store.adopt_takes(
        episode=1,
        group_id="group-1",
        take_ids=["take-1"],
        expected_revision=0,
        actor="user-1",
        reason="审核通过",
    )
    assert adopted["revision"] == 1
    assert adopted["adoptions"]["dialogue-1"]["take_id"] == "take-1"

    with pytest.raises(DubbingRevisionConflict):
        store.adopt_takes(
            episode=1,
            group_id="group-1",
            take_ids=["take-1"],
            expected_revision=0,
            actor="user-2",
            reason="过期提交",
        )
    assert store.get_group_state(1, "group-1")["adoptions"]["dialogue-1"]["take_id"] == "take-1"
