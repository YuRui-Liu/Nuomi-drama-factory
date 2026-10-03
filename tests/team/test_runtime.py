def test_team_runtime_is_authenticated_self_hosted(monkeypatch):
    from novelvideo.shared import runtime_env
    monkeypatch.setenv("ST_EDITION", "team")
    monkeypatch.setenv("ST_CONTROL_PLANE_DSN", "")
    assert not runtime_env.is_ce_effective()
    assert runtime_env.is_self_hosted()
    from novelvideo.api.routes.config import _runtime_edition
    assert _runtime_edition() == "team"


def test_ce_and_ee_keep_their_runtime_modes(monkeypatch):
    from novelvideo.shared import runtime_env
    monkeypatch.setenv("ST_EDITION", "ce")
    monkeypatch.setenv("ST_CONTROL_PLANE_DSN", "")
    assert runtime_env.is_self_hosted()
    monkeypatch.setenv("ST_EDITION", "ee")
    assert not runtime_env.is_self_hosted()
