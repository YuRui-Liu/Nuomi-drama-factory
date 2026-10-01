import json
import pytest

from novelvideo.character_visual.identity_sheet import build_identity_sheet_v3_prompt
from novelvideo.character_visual import identity_sheet_qc as qc


def test_confirmed_creature_does_not_receive_human_wardrobe_instructions():
    prompt = build_identity_sheet_v3_prompt(
        character_name="朏朏", character_tag="朏朏", appearance="四足狸状小兽，白尾有鬣，无衣物",
        project_style="guoman_3d", style_instructions="", avoid_instructions="",
        ethnicity="Chinese", has_costume_reference=False, nonhuman_species="狸状异兽",
    )
    assert "NONHUMAN ANATOMY" in prompt
    assert "four-legged" in prompt
    assert "no clothing" in prompt
    assert "collar and shoulder" not in prompt
    assert "Default ethnicity" not in prompt


@pytest.mark.asyncio
async def test_creature_qc_receives_state_and_blocks_anthropomorphic_mismatch(monkeypatch):
    captured = {}
    async def fake_call(**kwargs):
        captured.update(kwargs)
        flags = dict.fromkeys(qc._ISSUE_CODES, False)
        flags['state_inconsistent'] = True
        return 'vision', json.dumps(flags)
    monkeypatch.setattr(qc, 'call_freezone_vision_model', fake_call)
    report = await qc.assess_identity_sheet_quality(
        image_data=b'image', style='guoman_3d', expected_appearance='四足狸状小兽，白尾有鬣，无衣物', nonhuman_species='狸状异兽',
    )
    assert '四足狸状小兽' in captured['prompt']
    assert 'anthropomorphic' in captured['prompt']
    assert not report.passed
    assert 'state_inconsistent' in report.blocking_issues
