from __future__ import annotations

import multiprocessing
import os
import stat
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits are not portable")
def test_save_manifest_uses_readable_default_and_preserves_existing_mode(tmp_path):
    from novelvideo.director_world import stage_manifest

    manifest = stage_manifest.save_manifest(
        tmp_path, "permissions-scene", {"writer": "initial"}
    )
    assert stat.S_IMODE(manifest.stat().st_mode) == 0o644

    manifest.chmod(0o640)
    stage_manifest.save_manifest(
        tmp_path, "permissions-scene", {"writer": "replacement"}
    )
    assert stat.S_IMODE(manifest.stat().st_mode) == 0o640


def _gated_manifest_update(
    project_dir: str,
    field: str,
    loaded,
    release,
) -> None:
    from novelvideo.director_world import stage_manifest

    original_load = stage_manifest.load_manifest

    def gated_load(*args, **kwargs):
        manifest = original_load(*args, **kwargs)
        loaded.set()
        if not release.wait(10):
            raise TimeoutError("manifest update gate was not released")
        return manifest

    stage_manifest.load_manifest = gated_load
    stage_manifest.update_manifest(Path(project_dir), "shared-scene", **{field: field})


def test_update_manifest_serializes_read_modify_write_across_processes(tmp_path):
    from novelvideo.director_world import stage_manifest

    stage_manifest.update_manifest(tmp_path, "shared-scene", initial="initial")
    methods = multiprocessing.get_all_start_methods()
    method = "fork" if "fork" in methods else "spawn"
    process_context = multiprocessing.get_context(method)
    first_loaded = process_context.Event()
    first_release = process_context.Event()
    second_loaded = process_context.Event()
    second_release = process_context.Event()
    first = process_context.Process(
        target=_gated_manifest_update,
        args=(str(tmp_path), "first", first_loaded, first_release),
    )
    second = process_context.Process(
        target=_gated_manifest_update,
        args=(str(tmp_path), "second", second_loaded, second_release),
    )

    first.start()
    assert first_loaded.wait(10)
    second.start()
    second_loaded_before_first_finished = second_loaded.wait(0.5 if method == "fork" else 3)
    first_release.set()
    first.join(10)
    assert second_loaded.wait(10)
    second_release.set()
    second.join(10)

    assert first.exitcode == 0
    assert second.exitcode == 0
    assert not second_loaded_before_first_finished
    manifest = stage_manifest.load_manifest(tmp_path, "shared-scene")
    assert manifest is not None
    assert manifest["first"] == "first"
    assert manifest["second"] == "second"


def test_save_manifest_uses_unique_temporary_files_for_concurrent_writers(
    tmp_path, monkeypatch
):
    from novelvideo.director_world import stage_manifest

    original_replace = stage_manifest.os.replace
    replace_barrier = threading.Barrier(2)
    temporary_paths: list[Path] = []

    def synchronized_replace(source, destination):
        temporary_paths.append(Path(source))
        replace_barrier.wait(timeout=5)
        original_replace(source, destination)

    monkeypatch.setattr(stage_manifest.os, "replace", synchronized_replace)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(
                stage_manifest.save_manifest,
                tmp_path,
                "shared-scene",
                {"writer": writer},
            )
            for writer in ("one", "two")
        ]
        for future in futures:
            future.result(timeout=10)

    assert len(set(temporary_paths)) == 2
    assert all(path.parent == stage_manifest.stage_dir(tmp_path, "shared-scene") for path in temporary_paths)
    assert not any(path.exists() for path in temporary_paths)


def test_save_manifest_cleans_temporary_file_when_replace_fails(tmp_path, monkeypatch):
    import pytest

    from novelvideo.director_world import stage_manifest

    def fail_replace(_source, _destination):
        raise OSError("replace failed")

    monkeypatch.setattr(stage_manifest.os, "replace", fail_replace)

    with pytest.raises(OSError, match="replace failed"):
        stage_manifest.save_manifest(tmp_path, "shared-scene", {"writer": "one"})

    stage_directory = stage_manifest.stage_dir(tmp_path, "shared-scene")
    assert list(stage_directory.iterdir()) == []
