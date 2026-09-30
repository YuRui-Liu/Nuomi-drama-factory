"""The read-only director technique catalog and its selection rules."""

from hashlib import sha256
import json
from urllib.parse import urlparse

import pytest

from novelvideo.freezone.video_director import techniques as catalog
from novelvideo.freezone.video_director.techniques import (
    CATALOG_VERSION,
    check_applicability,
    list_techniques,
    project_technique,
    resolve_technique,
)


def test_catalog_has_distinct_curated_cards_with_traceable_sources():
    cards = list_techniques()
    assert 8 <= len(cards) <= 12
    assert CATALOG_VERSION
    assert len({(card.id, card.version) for card in cards}) == len(cards)
    assert all(card.status == "active" for card in cards)
    for card in cards:
        assert card.title and card.summary and card.category
        assert card.intent and card.action_beats and card.performance
        assert card.camera and card.ending_composition and card.avoid
        assert card.applicability.modes
        assert set(card.applicability.modes) <= {"i2v", "fl2v", "ref_only"}
        assert card.applicability.min_duration_seconds > 0
        assert card.applicability.max_duration_seconds >= card.applicability.min_duration_seconds
        assert card.sources
        for source in card.sources:
            assert source.url.startswith("https://")
            assert source.credit and source.source_type and source.checked_at and source.basis
            assert source.source_type in {"author_original", "reconstructed", "analysis_only"}


def test_analysis_sources_point_to_a_creator_case_or_official_guide():
    for card in list_techniques():
        for source in card.sources:
            parsed = urlparse(source.url)
            if parsed.netloc == "x.com":
                handle, marker, post_id = parsed.path.strip("/").split("/")
                assert marker == "status" and post_id.isdigit(), card.id
                assert source.credit == f"@{handle}", card.id
            else:
                assert source.url == (
                    "https://github.com/MiniMax-AI/MiniMax-H3/blob/main/"
                    "skills/h3-prompt-writing/references/base-en.txt"
                ), card.id
                assert source.credit == "MiniMax-AI", card.id
            assert len(source.basis) >= 20, card.id
            assert "归纳" not in source.basis or "案例" in source.basis, card.id


def test_content_hash_tracks_only_technique_semantics():
    card = list_techniques()[0]
    payload = {
        "intent": card.intent,
        "action_beats": card.action_beats,
        "performance": card.performance,
        "camera": card.camera,
        "ending_composition": card.ending_composition,
        "avoid": card.avoid,
    }
    expected = sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                 separators=(",", ":")).encode()).hexdigest()
    assert card.content_hash == expected
    assert card.model_copy(update={"title": "A new label"}).content_hash == expected
    assert card.model_copy(update={"camera": "A changed camera move"}).content_hash != expected


def test_lookup_rejects_unknown_or_inactive_versions_and_projection_omits_sources():
    card = list_techniques()[0]
    assert resolve_technique(card.id, card.version) == card
    with pytest.raises(ValueError, match="unknown technique"):
        resolve_technique(card.id, "missing")
    with pytest.raises(ValueError, match="unknown technique"):
        resolve_technique("missing", card.version)
    retired = card.model_copy(update={"status": "retired"})
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(catalog, "_CARDS", (retired,))
        with pytest.raises(ValueError, match="inactive technique"):
            resolve_technique(card.id, card.version)
    inactive = check_applicability(retired, "i2v", 5)
    assert inactive["reasons"][0]["code"] == "inactive_technique"
    projected = project_technique(card)
    assert projected["content_hash"] == card.content_hash
    assert projected["action_beats"] == list(card.action_beats)
    assert "sources" not in projected and "summary" not in projected


def test_applicability_reports_mode_and_aligned_duration_reasons():
    card = next(card for card in list_techniques() if card.id == "fixed-reaction")
    assert check_applicability(card, "i2v", 5)["compatible"] is True
    wrong_mode = check_applicability(card, "ref_only", 5)
    assert wrong_mode["compatible"] is False
    assert wrong_mode["reasons"][0]["field"] == "mode"
    assert wrong_mode["reasons"][0]["code"] == "unsupported_mode"
    low = check_applicability(card, "i2v", 1)
    assert low["reasons"][0]["field"] == "duration_seconds"
    assert low["reasons"][0]["code"] == "duration_too_short"
    high = check_applicability(card, "i2v", 20)
    assert high["reasons"][0]["code"] == "duration_too_long"
    assert high["aligned_duration_seconds"] >= 20


def test_applicability_uses_h3_frame_alignment_not_requested_seconds():
    card = next(card for card in list_techniques() if card.id == "fixed-reaction")
    narrowed = card.model_copy(update={
        "applicability": card.applicability.model_copy(update={
            "min_duration_seconds": 5.1, "max_duration_seconds": 5.2,
        }),
    })
    result = check_applicability(narrowed, "i2v", 5)
    assert result["compatible"] is True
    assert result["aligned_duration_seconds"] == pytest.approx(124 / 24)
    capped = card.model_copy(update={
        "applicability": card.applicability.model_copy(update={"max_duration_seconds": 5}),
    })
    assert check_applicability(capped, "i2v", 5)["reasons"][0]["code"] == "duration_too_long"


@pytest.mark.parametrize("card_id, requested_max", [
    ("fixed-reaction", 12),
    ("two-person-gaze", 15),
])
def test_catalog_advertised_max_accepts_its_requested_boundary(card_id, requested_max):
    card = next(card for card in list_techniques() if card.id == card_id)
    result = check_applicability(card, "i2v", requested_max)
    assert result["compatible"] is True
    assert card.applicability.max_duration_seconds == result["aligned_duration_seconds"]
    assert check_applicability(card, "i2v", card.applicability.max_duration_seconds)["compatible"] is True


def test_endpoint_transition_requires_first_last_mode_and_rejects_bad_duration():
    card = next(card for card in list_techniques() if card.id == "endpoint-continuity")
    assert card.applicability.last_frame_constraint == "required"
    assert check_applicability(card, "fl2v", 5)["compatible"] is True
    assert check_applicability(card, "i2v", 5)["reasons"][0]["code"] == "unsupported_mode"
    assert check_applicability(card, "fl2v", float("inf"))["reasons"][0]["code"] == "invalid_duration"
