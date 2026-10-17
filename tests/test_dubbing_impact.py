from __future__ import annotations


def test_impact_is_scoped_to_changed_dialogue_and_deleted_lines_keep_history() -> None:
    from novelvideo.audio.dubbing_contracts import DialogueSnapshot
    from novelvideo.audio.dubbing_service import calculate_dubbing_impact

    first = DialogueSnapshot(
        dialogue_id="dialogue-1",
        character_id="character-1",
        identity_id="adult",
        script_revision="script-r1",
        group_id="group-1",
        text="别进去。",
        voice_version_id="voice-v1",
        performance={"emotion": "克制"},
    )
    second = DialogueSnapshot(
        dialogue_id="dialogue-2",
        character_id="character-1",
        identity_id="adult",
        script_revision="script-r1",
        group_id="group-1",
        text="我听见里面有人。",
        voice_version_id="voice-v1",
        performance={},
    )
    changed_first = first.model_copy(update={"text": "进去看看。", "script_revision": "script-r2"})
    same_second = second.model_copy(update={"script_revision": "script-r2"})

    impact = calculate_dubbing_impact(
        previous=[first, second], current=[changed_first, same_second]
    )
    assert impact["stale_dialogue_ids"] == ["dialogue-1"]
    assert impact["deleted_dialogue_ids"] == []

    deleted = calculate_dubbing_impact(
        previous=[first, second], current=[first], take_dialogue_ids=["dialogue-2"],
    )
    assert deleted["deleted_dialogue_ids"] == ["dialogue-2"]
    assert deleted["preserved_take_dialogue_ids"] == ["dialogue-2"]


def test_adopting_new_voice_marks_only_lines_bound_to_old_version() -> None:
    from novelvideo.audio.dubbing_contracts import DialogueSnapshot
    from novelvideo.audio.dubbing_service import voice_version_impact

    lines = [
        DialogueSnapshot(
            dialogue_id=f"dialogue-{number}",
            character_id="character-1",
            identity_id="adult",
            script_revision="script-r1",
            group_id="group-1",
            text=f"台词{number}。",
            voice_version_id=version,
        )
        for number, version in ((1, "voice-v1"), (2, "voice-v1"), (3, "voice-v2"))
    ]

    impact = voice_version_impact(lines, character_id="character-1", identity_id="adult", old_version_id="voice-v1", new_version_id="voice-v3")
    assert impact == ["dialogue-1", "dialogue-2"]
