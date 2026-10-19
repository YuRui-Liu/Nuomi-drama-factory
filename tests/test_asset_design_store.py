from novelvideo.creation.asset_design.models import AssetDesignBrief
from novelvideo.creation.asset_design.store import AssetBriefStore


def test_write_read_roundtrip(tmp_path):
    store = AssetBriefStore(tmp_path)
    brief = AssetDesignBrief(asset_kind="prop", asset_id="backpack", design={"material": "帆布"})
    saved = store.write(brief, expected_hash=None)
    loaded = store.read("prop/backpack")
    assert loaded.asset_id == "backpack"
    assert saved["hash"] == store.hash(loaded)


def test_variant_without_base_is_rejected(tmp_path):
    store = AssetBriefStore(tmp_path)
    brief = AssetDesignBrief(asset_kind="character", asset_id="lin-du", variant_id="coat", design={})
    try:
        store.write(brief, expected_hash=None)
    except ValueError as exc:
        assert "base brief" in str(exc)
    else:
        raise AssertionError("expected ValueError")
