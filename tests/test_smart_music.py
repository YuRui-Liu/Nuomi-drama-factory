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
    assert plan["loop"] is False


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
    assert plan["loop"] is False


def test_prefers_shortest_sufficient_asset(monkeypatch):
    monkeypatch.setattr("novelvideo.music.smart_scoring.match_assets",
                        lambda assets, query='', duration_ms=0, instrumental_only=False: [
                            {"id": "long", "durationMs": 20000},
                            {"id": "exact", "durationMs": 15000},
                            {"id": "short", "durationMs": 12000},
                        ])
    plan = plan_group_music(group={"id": "G01"}, mood="压抑", duration_ms=13000,
                            enabled=True, assets=[])
    assert plan["asset"]["id"] == "exact"
    assert plan["loop"] is False


def test_loops_longest_asset_when_none_covers(monkeypatch):
    monkeypatch.setattr("novelvideo.music.smart_scoring.match_assets",
                        lambda assets, query='', duration_ms=0, instrumental_only=False: [
                            {"id": "a", "durationMs": 8000},
                            {"id": "b", "durationMs": 12000},
                        ])
    plan = plan_group_music(group={"id": "G01"}, mood="压抑", duration_ms=13000,
                            enabled=True, assets=[])
    assert plan["asset"]["id"] == "b"
    assert plan["loop"] is True


def test_missing_duration_is_treated_as_zero(monkeypatch):
    monkeypatch.setattr("novelvideo.music.smart_scoring.match_assets",
                        lambda assets, query='', duration_ms=0, instrumental_only=False: [{"id": "nodur"}])
    plan = plan_group_music(group={"id": "G01"}, mood="压抑", duration_ms=13000,
                            enabled=True, assets=[])
    assert plan["asset"]["id"] == "nodur"
    assert plan["loop"] is True
