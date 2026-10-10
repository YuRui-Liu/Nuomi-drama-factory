"""Versioned caption events shared by exports, burn-in and future alignment tools.

Estimated timings are draft editorial suggestions, never evidence of speech timing.
Manual/aligned producers should supply already split, timed events via CaptionTrack.
"""
from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CaptionEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    dialogue_id: str = Field(min_length=1)
    speaker: str = ""
    text: str = Field(min_length=1)
    start: float = Field(ge=0, allow_inf_nan=False)
    end: float = Field(gt=0, allow_inf_nan=False)
    source: Literal["aligned", "manual", "estimated"]
    review_status: Literal["needs_review", "approved"] = "needs_review"
    audio_fingerprint: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_event(self):
        if self.end <= self.start:
            raise ValueError("caption end must follow start")
        if self.source == "estimated" and self.review_status != "needs_review":
            raise ValueError("estimated captions require review; approve as manual")
        if len(self.text.splitlines()) > 2 or any(len(line) > 16 for line in self.text.splitlines()):
            raise ValueError("caption must fit two lines of at most 16 characters")
        return self


class CaptionTrack(BaseModel):
    model_config = ConfigDict(extra="forbid")
    format_version: Literal[1] = 1
    duration: float = Field(ge=0, allow_inf_nan=False)
    audio_fingerprint: str = Field(min_length=1)
    timing_basis: Literal["actual", "estimated"] = "actual"
    source_durations: list[float] = Field(default_factory=list)
    timing_notes: list[str] = Field(default_factory=list)
    alignment_issues: list[dict] = Field(default_factory=list)
    events: list[CaptionEvent] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_timeline(self):
        import math
        if any(not math.isfinite(value) or value <= 0 for value in self.source_durations):
            raise ValueError("source durations must be positive and finite")
        if self.source_durations and abs(sum(self.source_durations) - self.duration) > 1e-6:
            raise ValueError("source durations must sum to track duration")
        previous_end = 0.0
        for event in self.events:
            if event.start < previous_end or event.end > self.duration + 1e-6:
                raise ValueError("captions overlap or exceed the composition timeline")
            if event.audio_fingerprint != self.audio_fingerprint:
                raise ValueError("caption audio fingerprint does not match track")
            previous_end = event.end
        return self


def estimate_events(dialogue_id: str, text: str, start: float, end: float,
                    audio_fingerprint: str, speaker: str = "") -> list[CaptionEvent]:
    """Split turns and sentences without rewriting; durations remain estimates."""
    if not text.strip() or end <= start:
        return []
    # A speaker tag at a boundary starts a new turn, including inline Q/A.
    turns = re.split(r"\n+|\s+(?=[^\s：:，。！？!?]{1,12}[：:])|(?<=[。！？!?；;])\s*(?=[^\s：:，。！？!?]{1,12}[：:])", text.strip())
    pieces: list[tuple[str, str]] = []
    for turn in turns:
        turn = turn.strip()
        if not turn:
            continue
        match = re.match(r"^([^：:]{1,12})[：:]", turn)
        who = match.group(1) if match else speaker
        # Sentence boundaries also separate questions from answers with no tags.
        sentences = re.findall(r"[^。！？!?；;]+[。！？!?；;]*|[。！？!?；;]+", turn)
        for sentence in sentences:
            while sentence:
                cut = 32
                if len(sentence) > 32:
                    boundaries = [match.end() for match in re.finditer(r"[，,、：:]", sentence[:32])]
                    if boundaries and boundaries[-1] >= 8:
                        cut = boundaries[-1]
                chunk, sentence = sentence[:cut], sentence[cut:]
                pieces.append((who, chunk[:16] + ("\n" + chunk[16:] if len(chunk) > 16 else "")))
    weight = sum(len(value.replace("\n", "")) for _, value in pieces)
    cursor = start
    events = []
    for index, (who, value) in enumerate(pieces):
        finish = end if index == len(pieces) - 1 else cursor + (end - start) * len(value.replace("\n", "")) / weight
        events.append(CaptionEvent(dialogue_id=f"{dialogue_id}:{index + 1}", speaker=who,
                                   text=value, start=cursor, end=finish, source="estimated",
                                   audio_fingerprint=audio_fingerprint))
        cursor = finish
    return events


def _time(seconds: float, ass: bool = False) -> str:
    units = int(round(seconds * (100 if ass else 1000)))
    scale = 100 if ass else 1000
    seconds, fraction = divmod(units, scale)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    return (f"{hours}:{minutes:02}:{seconds:02}.{fraction:02}" if ass
            else f"{hours:02}:{minutes:02}:{seconds:02},{fraction:03}")


def render_srt(track: CaptionTrack) -> str:
    return "\n".join(f"{i}\n{_time(e.start)} --> {_time(e.end)}\n{e.text}\n"
                     for i, e in enumerate(track.events, 1))


def render_ass(track: CaptionTrack, width: int, height: int, font: str = "Noto Sans CJK SC") -> str:
    if width <= 0 or height <= 0:
        raise ValueError("subtitle resolution must be positive")
    portrait = height >= width
    scale = width / 720 if portrait else height / 720
    size = 32 * scale
    outline = 1.5 * scale
    margin_x, margin_y = round(width * .08), round(height * (.10 if portrait else .07))
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
ScaledBorderAndShadow: yes
WrapStyle: 2

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font},{size:g},&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,{outline:g},0,2,{margin_x},{margin_x},{margin_y},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    def escape(text: str) -> str:
        # ASS has no C-style escaped backslash: doubling it still leaves an
        # active \N/\n/\h. A zero-width WORD JOINER separates a literal slash
        # from the following letter without adding/changing visible glyphs.
        # Stored caption text and SRT remain untouched by this ASS encoding.
        replacements = {"\\": "\\\u2060", "{": r"\{", "}": r"\}", "\n": r"\N"}
        return "".join(replacements.get(character, character) for character in text)
    return header + "\n".join(f"Dialogue: 0,{_time(e.start, True)},{_time(e.end, True)},Default,,0,0,0,,{escape(e.text)}" for e in track.events) + "\n"
