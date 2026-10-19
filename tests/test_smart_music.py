from novelvideo.music.smart_scoring import plan_group_music


def test_uses_match_when_available(monkeypatch):
    monkeypatch.setattr("novelvideo.music.smart_scoring.match_assets",
                        lambda assets, query='', duration_ms=0, instrumental_only=False: [{"id": "m1", "duration_ms": 20000}])
    plan = plan_group_music(group={"id": "G01"}, mood="压抑", duration_ms=13000,
                            enabled=True, assets=[{"name": "压抑氛围", "vocals": "none"}])
    assert plan["source"] == "library"
    assert plan["group_id"] == "G01"
    assert plan["asset"]["id"] == "m1"
    assert plan["duck"] is True


def test_disabled_returns_none():
    assert plan_group_music(group={"id": "G01"}, mood="压抑", duration_ms=13000,
                            enabled=False, assets=[]) is None


def test_falls_back_to_generate_when_library_empty(monkeypatch):
    monkeypatch.setattr("novelvideo.music.smart_scoring.match_assets",
                        lambda assets, query='', duration_ms=0, instrumental_only=False: [])
    plan = plan_group_music(group={"id": "G01"}, mood="压抑", duration_ms=13000,
                            enabled=True, assets=[])
    assert plan["source"] == "generate"
    assert plan["prompt"] == "压抑"
    assert plan["duration_ms"] == 13000
    assert plan["duck"] is True
