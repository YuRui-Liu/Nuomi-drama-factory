"""Resolve native-video voice direction from saved character assets."""


def resolve_speaker_voices(speakers, characters) -> dict[str, str]:
    result = {}
    characters = tuple(characters)
    for speaker in dict.fromkeys(speakers):
        if not speaker.strip():
            continue
        matches = [c for c in characters if speaker == c.name or speaker in c.aliases]
        if len(matches) != 1:
            raise ValueError(f"角色 {speaker} 的声音绑定缺失或名称不唯一，请在资产中心补齐声音设定")
        character = matches[0]
        facts = character.voice_facts
        if facts.conflicts or not facts.voice_traits.strip():
            raise ValueError(f"角色 {speaker} 的声音设定缺失或存在冲突，请在资产中心确认声音特征")
        result[speaker] = facts.voice_traits.strip()
    return result
