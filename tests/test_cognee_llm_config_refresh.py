"""Legacy gateway fingerprints are isolated from the native knowledge runtime."""


def test_first_ce_gateway_configuration_does_not_require_restart(monkeypatch):
    from novelvideo.cognee import config as nv_config

    monkeypatch.setenv("ST_EDITION", "ce")
    monkeypatch.delenv("ST_CONTROL_PLANE_DSN", raising=False)
    monkeypatch.setattr(nv_config, "_active_gateway_fingerprint", None)
    monkeypatch.setattr(nv_config, "_current_gateway_fingerprint", lambda: "configured")

    assert nv_config.cognee_gateway_restart_required() is False


def test_gateway_change_is_only_a_legacy_compatibility_signal(monkeypatch):
    from novelvideo.cognee import config as nv_config

    monkeypatch.setenv("ST_EDITION", "ce")
    monkeypatch.delenv("ST_CONTROL_PLANE_DSN", raising=False)
    monkeypatch.setattr(nv_config, "_active_gateway_fingerprint", "old")
    monkeypatch.setattr(nv_config, "_current_gateway_fingerprint", lambda: "new")

    assert nv_config.cognee_gateway_restart_required() is True
