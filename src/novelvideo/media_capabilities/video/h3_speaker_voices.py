"""Resolve native-video voice direction from saved character assets."""


def required_voice_speakers(dialogue_lines, *, speaker="", dialogue=""):
    """Use structured dialogue speakers, with the runner's legacy fallback."""
    return [line.speaker for line in dialogue_lines] or ([speaker] if dialogue.strip() else [])


def speaker_voice_preflight(speakers, characters):
    """Native video supplies its own audio; voice direction is optional."""
    return {"ready": True, "issues": []}


def resolve_speaker_voices(speakers, characters) -> dict[str, str]:
    result = {}
    characters = tuple(characters)
    for speaker in dict.fromkeys(speakers):
        if not speaker.strip():
            continue
        matches = [c for c in characters if speaker == c.name or speaker in c.aliases]
        if len(matches) != 1:
            continue
        character = matches[0]
        facts = character.voice_facts
        if facts.conflicts or not facts.voice_traits.strip():
            continue
        result[speaker] = facts.voice_traits.strip()
    return result
