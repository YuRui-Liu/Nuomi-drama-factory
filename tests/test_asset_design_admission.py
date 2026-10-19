from novelvideo.creation.asset_design.admission import check_admission


def test_missing_confirmation_blocks():
    issues = check_admission(brief={"admission": {"confirmed": False}}, asset_exists=False)
    assert issues[0].code == "brief_admission_missing"


def test_confirmed_allows():
    issues = check_admission(
        brief={"admission": {"confirmed": True, "confirmed_by": "user", "basis": "剧本明示"}},
        asset_exists=True,
    )
    assert issues == []
