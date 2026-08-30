from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest


@pytest.fixture
def catalog_path(tmp_path: Path) -> Path:
    source = (
        Path(__file__).parents[1]
        / "src"
        / "novelvideo"
        / "extension_styles"
        / "catalog.json"
    )
    target = tmp_path / "catalog.json"
    target.write_bytes(source.read_bytes())
    return target


@pytest.fixture
def registry(catalog_path: Path):
    from novelvideo.extension_styles.registry import ExtensionStyleRegistry

    return ExtensionStyleRegistry(catalog_path)


def test_initial_snapshot_loads_eighteen_styles(registry) -> None:
    snapshot = registry.snapshot()

    assert len(snapshot.styles) == 18
    assert snapshot.generation == 1
    assert snapshot.catalog_hash
    assert snapshot.diagnostics.discovered == 18
    assert snapshot.diagnostics.loaded == 18
    assert snapshot.diagnostics.failed == 0
    assert snapshot.diagnostics.degraded is False


def test_changed_catalog_updates_fingerprint_and_generation(
    registry, catalog_path: Path
) -> None:
    first = registry.snapshot()
    raw = json.loads(catalog_path.read_text(encoding="utf-8"))
    raw[0]["summary"] += " refreshed"
    catalog_path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    second = registry.snapshot()

    assert second.generation == first.generation + 1
    assert second.catalog_hash != first.catalog_hash
    assert second.fingerprint != first.fingerprint


def test_invalid_reload_keeps_last_known_good(registry, catalog_path: Path) -> None:
    first = registry.snapshot()
    catalog_path.write_text("not-json", encoding="utf-8")

    second = registry.reload()

    assert second.styles == first.styles
    assert second.generation == first.generation
    assert second.catalog_hash == first.catalog_hash
    assert second.diagnostics.degraded is True
    assert second.diagnostics.failed == 1
    assert second.diagnostics.errors == (
        {"code": "invalid_catalog", "file": "catalog.json"},
    )
    assert str(catalog_path.parent) not in repr(second.diagnostics.errors)


def test_repaired_catalog_recovers_from_degraded_state(
    registry, catalog_path: Path
) -> None:
    original = catalog_path.read_bytes()
    first = registry.snapshot()
    catalog_path.write_text("not-json", encoding="utf-8")
    failed = registry.reload()
    catalog_path.write_bytes(original)

    recovered = registry.reload()

    assert failed.diagnostics.degraded is True
    assert recovered.diagnostics.degraded is False
    assert recovered.generation == first.generation + 1
    assert recovered.diagnostics.errors == ()


def test_initial_invalid_catalog_fails_closed(catalog_path: Path) -> None:
    from novelvideo.extension_styles.registry import ExtensionStyleRegistry

    catalog_path.write_text("not-json", encoding="utf-8")

    snapshot = ExtensionStyleRegistry(catalog_path).snapshot()

    assert snapshot.styles == ()
    assert snapshot.generation == 0
    assert snapshot.catalog_hash == ""
    assert snapshot.diagnostics.degraded is True


def test_concurrent_snapshots_parse_changed_catalog_once(
    monkeypatch: pytest.MonkeyPatch, registry, catalog_path: Path
) -> None:
    import novelvideo.extension_styles.registry as registry_module

    registry.snapshot()
    original_load = registry_module.load_catalog
    calls = 0

    def counting_load(path):
        nonlocal calls
        calls += 1
        return original_load(path)

    monkeypatch.setattr(registry_module, "load_catalog", counting_load)
    raw = json.loads(catalog_path.read_text(encoding="utf-8"))
    raw[0]["summary"] += " concurrent"
    catalog_path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    with ThreadPoolExecutor(max_workers=8) as pool:
        snapshots = tuple(pool.map(lambda _: registry.snapshot(), range(16)))

    assert calls == 1
    assert len({snapshot.generation for snapshot in snapshots}) == 1
    assert len({snapshot.catalog_hash for snapshot in snapshots}) == 1
