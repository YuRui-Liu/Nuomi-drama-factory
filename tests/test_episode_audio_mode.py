from novelvideo.music.alignment import AudioMode, GroupAudioPlan, validate_audio_plan


def test_hybrid_requires_line_ids():
    plan = GroupAudioPlan(group_id="G01", audio_mode=AudioMode.HYBRID)
    issues = validate_audio_plan(plan)
    assert issues[0].code == "audio_mode_conflict"


def test_original_blocks_dub():
    plan = GroupAudioPlan(group_id="G01", audio_mode=AudioMode.ORIGINAL, replaced_line_ids=["L1"])
    issues = validate_audio_plan(plan)
    assert any(issue.code == "audio_mode_conflict" for issue in issues)


def test_valid_plans_have_no_issues():
    assert validate_audio_plan(GroupAudioPlan(group_id="G01")) == []
    assert validate_audio_plan(
        GroupAudioPlan(group_id="G01", audio_mode=AudioMode.HYBRID, replaced_line_ids=["L1"])
    ) == []
