"""Resolve native-video voice direction from saved character assets."""


def required_voice_speakers(dialogue_lines, *, speaker="", dialogue=""):
    """Use structured dialogue speakers, with the runner's legacy fallback."""
    return [line.speaker for line in dialogue_lines] or ([speaker] if dialogue.strip() else [])


def speaker_voice_preflight(speakers, characters):
    """Report the existing native-video guard without requiring paid TTS assets."""
    characters = tuple(characters)
    issues = []
    for speaker in dict.fromkeys(speakers):
        try:
            resolve_speaker_voices([speaker], characters)
        except ValueError as exc:
            issues.append({"speaker": speaker, "reason": str(exc)})
    return {"ready": not issues, "issues": issues}


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
