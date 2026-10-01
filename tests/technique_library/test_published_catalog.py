"""Published original direction cards and their display-only provenance."""
from collections import Counter
from pathlib import Path

from novelvideo.freezone.video_director.techniques import check_applicability, list_techniques, project_technique
from novelvideo.media_capabilities.video.h3_timeline import H3_FPS, frames_for_duration
from novelvideo.technique_library.models import Bundle

OLD_HASHES = {
    "fixed-reaction": "cbf9d5549b32815b24bd14cab1ae6561f9477bb899d3b6c04caf9dff71751195",
    "expression-build": "a92d4251dd78c45fc6c7afe4be72135c648569b73798bd5f0ff22d9a471910e8",
    "two-person-gaze": "e3c3f7d498662352757f80881fac469600e47a827db97a4bef7188b68a49a827",
    "lateral-follow": "ce88c9eb5fd221c0b45bd1147dd30cb20022957128aef2d816efcc3dd39522c6",
    "slow-push-in": "571bec98200fdd5b48d3706a541937a6e08560352a15b0fb7b4d1dd7b1093212",
    "subject-entrance": "628f4016e10a56cb905cd8bc8dc4d3460940483bc7f27b539017fabee51a1ab2",
    "contained-climax": "4149c12b37c8c02d2e0f566c32e89afc8e768443f2a5f5310ce736aa58e3b6fb",
    "endpoint-continuity": "0696bb4124eeb39cea8609e59089aaae797f9928a9a95a11d50fb0ef5ef57e1c"
}

def test_published_cards_cover_all_six_uses_and_resolve_case_sources():
    cards = list_techniques()
    assert len(cards) == 24
    assert len({c.id for c in cards}) == 24
    assert len({c.content_hash for c in cards}) == 24
    uses = Counter(use for card in cards for use in card.use_cases)
    assert set(uses) == {"cinema", "performance", "action", "product", "music", "stylized"}
    assert all(count >= 2 for count in uses.values())
    path = Path(__file__).parents[2] / "src/novelvideo/technique_library/data/catalog.json"
    cases = {case.id: case for case in Bundle.model_validate_json(path.read_text()).cases}
    for card in cards:
        assert card.use_cases
        assert len(set(card.use_cases)) == len(card.use_cases)
        assert card.case_ids or card.id == "endpoint-continuity"
        for case_id in card.case_ids:
            assert case_id in cases
            assert {s.url for s in card.sources} & {s.url for s in cases[case_id].sources}
        assert all(s.source_type == "analysis_only" for s in card.sources)

def test_legacy_versions_hashes_and_display_projection_are_stable():
    for card in list_techniques():
        if card.id in OLD_HASHES:
            assert card.version == "1.0.0"
            assert card.content_hash == OLD_HASHES[card.id]
        changed = card.model_copy(update={"use_cases": ("music",), "case_ids": ("h3-0000000000000000",)})
        assert changed.content_hash == card.content_hash
        assert project_technique(changed) == project_technique(card)
        assert not {"use_cases", "case_ids", "sources"} & project_technique(card).keys()

def test_new_cards_respect_local_capabilities_and_provenance():
    for card in list_techniques():
        if card.id in OLD_HASHES:
            continue
        assert card.applicability.max_duration_seconds == frames_for_duration(15, H3_FPS) / H3_FPS
        for mode in card.applicability.modes:
            assert check_applicability(card, mode, 15)["compatible"]
            assert check_applicability(card, mode, card.applicability.max_duration_seconds)["compatible"]
        assert card.applicability.last_frame_constraint == "forbidden"
        assert "fl2v" not in card.applicability.modes
        assert any(label in card.sources[0].basis for label in ("作者公开原文", "reconstructed", "官方"))
    speech = next(c for c in list_techniques() if c.id == "speech-settle")
    assert "对白" in speech.intent
    assert any("音频" in avoid and "视频编辑" in avoid for avoid in speech.avoid)
