from __future__ import annotations

import hashlib
import json
import os
from dataclasses import replace
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from novelvideo.media_capabilities.video.h3_timeline import H3DirectorSegment
from novelvideo.narrative_groups.video_references import ResolvedVideoReference


def _reference(path: Path, content: bytes, *, reference_id: str = "ref-1"):
    return ResolvedVideoReference(
        reference_id=reference_id,
        source_kind="character_identity",
        label="阿明",
        subject_description="阿明，黑色短发",
        path=path,
        content=content,
        sha256=hashlib.sha256(content).hexdigest(),
    )


def _png_bytes() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (3, 3), "green").save(buffer, format="PNG")
    return buffer.getvalue()


def test_task_display_metadata_persists_reference_snapshot_ownership() -> None:
    from novelvideo.ports.tasks import display_metadata_for_task

    metadata = display_metadata_for_task(
        "narrative_group_video",
        {
            "reference_snapshot_id": "a" * 32,
            "reference_snapshot_digest": "b" * 64,
        },
    )

    assert metadata == {
        "reference_snapshot_id": "a" * 32,
        "reference_snapshot_digest": "b" * 64,
    }


def test_reference_idempotency_snapshot_changes_with_order_content_and_description(
    tmp_path: Path,
) -> None:
    from novelvideo.media_capabilities.video.h3_reference_runtime import (
        _freeze_inputs,
        _idempotency_input,
    )
    from novelvideo.media_capabilities.video.h3_timeline import build_h3_timeline_data

    frame = tmp_path / "frame.png"
    Image.new("RGB", (5, 5), "black").save(frame)
    first_content = _png_bytes()
    buffer = BytesIO()
    Image.new("RGB", (4, 4), "blue").save(buffer, format="PNG")
    second_content = buffer.getvalue()
    first = _reference(tmp_path / "ref-1.png", first_content, reference_id="ref-1")
    second = _reference(tmp_path / "ref-2.png", second_content, reference_id="ref-2")
    segment = H3DirectorSegment(
        segment_id="s1", beat_number=1, prompt="走近", duration_seconds=2,
        first_frame=str(frame),
    )
    timeline = build_h3_timeline_data((segment,), strict_first_frame=True)

    def snapshot(references):
        frozen, _, _, frames = _freeze_inputs(
            (segment,), references, reference_limit=5, project_root=tmp_path
        )
        return _idempotency_input(
            timeline=timeline, references=frozen, frames=frames,
            workflow_id="2096502793044582401", reference_limit=5,
            mode="auto", aspect_ratio="9:16", resolution="720p",
            output_path="out.mp4",
        )

    baseline = snapshot((first, second))
    assert snapshot((second, first)) != baseline
    changed_description = replace(
        first,
        subject_description="阿明，黑色短发，戴眼镜",
    )
    assert snapshot((changed_description, second)) != baseline
    changed_buffer = BytesIO()
    Image.new("RGB", (6, 6), "red").save(changed_buffer, format="PNG")
    assert snapshot((
        _reference(
            tmp_path / "ref-1.png",
            changed_buffer.getvalue(),
            reference_id="ref-1",
        ),
        second,
    )) != baseline


def test_reference_manifest_snapshot_is_path_and_content_free_and_legacy_compatible() -> None:
    from novelvideo.media_capabilities.video.h3_timeline import (
        H3DirectorOutputManifest,
        H3ReferenceManifestEntry,
        build_h3_timeline_data,
    )

    timeline = build_h3_timeline_data((H3DirectorSegment(
        segment_id="s1", beat_number=1, prompt="走近", duration_seconds=2,
        first_frame="first.png",
    ),))
    reference = H3ReferenceManifestEntry(
        picture_index=1, reference_id="ref-1",
        source_kind="character_identity", label="阿明",
        subject_description="阿明，黑色短发", sha256="a" * 64,
    )
    manifest = H3DirectorOutputManifest(
        entries=timeline.entries,
        provider_workflow_id="2096502793044582401",
        reference_settings_revision=3,
        reference_limit=5,
        global_references=(reference,),
    )

    dumped = manifest.model_dump(mode="json")
    assert dumped["global_references"] == [reference.model_dump(mode="json")]
    assert "path" not in json.dumps(dumped["global_references"])
    assert "content" not in json.dumps(dumped["global_references"])
    assert H3DirectorOutputManifest.model_validate(
        {"entries": timeline.model_dump(mode="json")["entries"]}
    ).global_references == ()


def test_frame_snapshot_enforces_single_file_byte_limit(tmp_path: Path, monkeypatch) -> None:
    from novelvideo.media_capabilities.video import h3_reference_runtime as runtime

    frame = tmp_path / "frame.png"
    Image.new("RGB", (4, 4), "black").save(frame)
    monkeypatch.setattr(runtime, "H3_FRAME_MAX_BYTES", frame.stat().st_size - 1)

    with pytest.raises(ValueError, match="byte limit"):
        runtime.freeze_h3_reference_frames((H3DirectorSegment(
            segment_id="s1", beat_number=1, prompt="one", duration_seconds=2,
            first_frame=str(frame),
        ),), project_root=tmp_path)
    monkeypatch.setattr(runtime, "H3_FRAME_MAX_BYTES", frame.stat().st_size)
    assert runtime.freeze_h3_reference_frames((H3DirectorSegment(
        segment_id="s1", beat_number=1, prompt="one", duration_seconds=2,
        first_frame=str(frame),
    ),), project_root=tmp_path)[str(frame)].content == frame.read_bytes()


def test_frame_snapshot_enforces_pixel_and_format_limits(tmp_path: Path, monkeypatch) -> None:
    from novelvideo.media_capabilities.video import h3_reference_runtime as runtime

    large = tmp_path / "large.png"
    Image.new("RGB", (4, 4), "black").save(large)
    monkeypatch.setattr(runtime, "H3_FRAME_MAX_PIXELS", 15)
    with pytest.raises(ValueError, match="pixel limit"):
        runtime.freeze_h3_reference_frames((H3DirectorSegment(
            segment_id="large", beat_number=1, prompt="one", duration_seconds=2,
            first_frame=str(large),
        ),), project_root=tmp_path)
    monkeypatch.setattr(runtime, "H3_FRAME_MAX_PIXELS", 16)
    assert runtime.freeze_h3_reference_frames((H3DirectorSegment(
        segment_id="large", beat_number=1, prompt="one", duration_seconds=2,
        first_frame=str(large),
    ),), project_root=tmp_path)[str(large)].width == 4

    gif = tmp_path / "frame.gif"
    Image.new("RGB", (2, 2), "black").save(gif, format="GIF")
    with pytest.raises(ValueError, match="PNG, JPEG, or WEBP"):
        runtime.freeze_h3_reference_frames((H3DirectorSegment(
            segment_id="gif", beat_number=1, prompt="one", duration_seconds=2,
            first_frame=str(gif),
        ),), project_root=tmp_path)


def test_frame_snapshot_enforces_group_cumulative_byte_limit(
    tmp_path: Path, monkeypatch
) -> None:
    from novelvideo.media_capabilities.video import h3_reference_runtime as runtime

    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    Image.new("RGB", (3, 3), "black").save(first)
    Image.new("RGB", (3, 3), "white").save(second)
    monkeypatch.setattr(
        runtime,
        "H3_GROUP_FRAME_SNAPSHOT_MAX_BYTES",
        first.stat().st_size + second.stat().st_size - 1,
    )

    with pytest.raises(ValueError, match="group byte limit"):
        runtime.freeze_h3_reference_frames((
            H3DirectorSegment(
                segment_id="s1", beat_number=1, prompt="one", duration_seconds=2,
                first_frame=str(first),
            ),
            H3DirectorSegment(
                segment_id="s2", beat_number=2, prompt="two", duration_seconds=2,
                first_frame=str(second),
            ),
        ), project_root=tmp_path)
    monkeypatch.setattr(
        runtime,
        "H3_GROUP_FRAME_SNAPSHOT_MAX_BYTES",
        first.stat().st_size + second.stat().st_size,
    )
    assert len(runtime.freeze_h3_reference_frames((
        H3DirectorSegment(
            segment_id="s1", beat_number=1, prompt="one", duration_seconds=2,
            first_frame=str(first),
        ),
        H3DirectorSegment(
            segment_id="s2", beat_number=2, prompt="two", duration_seconds=2,
            first_frame=str(second),
        ),
    ), project_root=tmp_path)) == 2


@pytest.mark.parametrize("link_kind", ["ancestor", "final"])
def test_frame_snapshot_rejects_symlinks_below_project_root(
    tmp_path: Path, link_kind: str
) -> None:
    from novelvideo.media_capabilities.video import h3_reference_runtime as runtime

    project = tmp_path / "project"
    frames = project / "frames"
    outside = tmp_path / "outside"
    frames.mkdir(parents=True)
    outside.mkdir()
    outside_frame = outside / "frame.png"
    Image.new("RGB", (3, 3), "red").save(outside_frame)
    if link_kind == "ancestor":
        source = project / "linked" / "frame.png"
        (project / "linked").symlink_to(outside, target_is_directory=True)
    else:
        source = frames / "frame.png"
        source.symlink_to(outside_frame)

    with pytest.raises(ValueError, match="no-follow|symlink|reparse"):
        runtime.freeze_h3_reference_frames(
            (H3DirectorSegment(
                segment_id="unsafe", beat_number=1, prompt="one",
                duration_seconds=2, first_frame=str(source),
            ),),
            project_root=project,
        )


def test_frame_snapshot_keeps_open_handle_content_when_path_is_replaced(
    tmp_path: Path, monkeypatch
) -> None:
    from novelvideo.media_capabilities.video import h3_reference_runtime as runtime
    from novelvideo.narrative_groups import video_references

    project = tmp_path / "project"
    project.mkdir()
    source = project / "frame.png"
    outside = tmp_path / "outside.png"
    Image.new("RGB", (3, 3), "green").save(source)
    Image.new("RGB", (3, 3), "red").save(outside)
    original = source.read_bytes()
    original_read = video_references.os.read
    replaced = False

    def replace_after_open(descriptor, size):
        nonlocal replaced
        if not replaced:
            replaced = True
            source.unlink()
            source.symlink_to(outside)
        return original_read(descriptor, size)

    monkeypatch.setattr(video_references.os, "read", replace_after_open)
    frozen = runtime.freeze_h3_reference_frames(
        (H3DirectorSegment(
            segment_id="race", beat_number=1, prompt="one",
            duration_seconds=2, first_frame=str(source),
        ),),
        project_root=project,
    )

    assert frozen[str(source)].content == original
    assert source.read_bytes() != original


@pytest.mark.parametrize("reparse_target", ["parent", "file"])
def test_frame_snapshot_rejects_windows_reparse_components(
    tmp_path: Path, reparse_target: str
) -> None:
    from novelvideo.media_capabilities.video import h3_reference_runtime as runtime

    project = tmp_path / "project"
    parent = project / "frames"
    parent.mkdir(parents=True)
    frame = parent / "first.png"
    Image.new("RGB", (3, 3), "green").save(frame)

    class Win32:
        DIRECTORY = 0x10
        REPARSE_POINT = 0x400
        DISK_FILE_TYPE = 1

        def open_path(self, path, *, directory):
            return (Path(path), directory)

        def attributes(self, handle):
            path, directory = handle
            reparse = (
                reparse_target == "parent" and path == parent
            ) or (reparse_target == "file" and path == frame)
            return (self.DIRECTORY if directory else 0) | (
                self.REPARSE_POINT if reparse else 0
            )

        def file_type(self, _handle):
            return self.DISK_FILE_TYPE

        def final_path_for_handle(self, handle):
            return handle[0]

        def file_size(self, _handle):
            return frame.stat().st_size

        def read_file(self, _handle, _max_bytes):
            return frame.read_bytes()

        def close(self, _handle):
            return None

    with pytest.raises(ValueError, match="reparse"):
        runtime.freeze_h3_reference_frames(
            (H3DirectorSegment(
                segment_id="windows", beat_number=1, prompt="one",
                duration_seconds=2, first_frame=str(frame),
            ),),
            project_root=project,
            win32_adapter=Win32(),
            platform_name="nt",
        )


def test_reference_input_snapshot_store_round_trips_without_source_paths(
    tmp_path: Path,
) -> None:
    from novelvideo.media_capabilities.video import h3_reference_runtime as runtime

    project = tmp_path / "project"
    state = tmp_path / "state"
    project.mkdir()
    frame = project / "frames" / "first.png"
    frame.parent.mkdir()
    Image.new("RGB", (4, 5), "green").save(frame)
    reference_content = _png_bytes()
    references = (_reference(project / "private-ref.png", reference_content),)
    frames = runtime.freeze_h3_reference_frames(
        (H3DirectorSegment(
            segment_id="s1", beat_number=1, prompt="one", duration_seconds=2,
            first_frame=str(frame),
        ),),
        project_root=project,
    )
    frozen_frame_content = frame.read_bytes()

    persisted = runtime.persist_h3_reference_input_snapshot(
        state_root=state,
        references=references,
        frames=frames,
        reference_revision=7,
        reference_limit=5,
        provider_workflow_id="2096502793044582401",
    )
    snapshot_id = persisted.snapshot_id
    Image.new("RGB", (4, 5), "red").save(frame)
    loaded = runtime.load_h3_reference_input_snapshot(
        state_root=state,
        snapshot_id=snapshot_id,
        expected_digest=persisted.digest,
        frame_sources=(str(frame),),
    )

    assert loaded.reference_revision == 7
    assert loaded.reference_limit == 5
    assert loaded.provider_workflow_id == "2096502793044582401"
    assert loaded.references[0].content == reference_content
    assert loaded.frames[str(frame)].content == frozen_frame_content
    assert loaded.frames[str(frame)].content != frame.read_bytes()
    descriptor = next((state / "h3_reference_input_snapshots").rglob("snapshot.json"))
    descriptor_text = descriptor.read_text(encoding="utf-8")
    assert str(frame) not in descriptor_text
    assert str(references[0].path) not in descriptor_text
    assert "content" not in descriptor_text
    orphan = runtime.persist_h3_reference_input_snapshot(
        state_root=state,
        references=references,
        frames=frames,
        reference_revision=7,
        reference_limit=5,
        provider_workflow_id="2096502793044582401",
    )
    storage = state / "h3_reference_input_snapshots"
    (storage / orphan.snapshot_id / "lease.json").write_text(
        '{"expires_at":100}', encoding="utf-8"
    )
    queued = runtime.persist_h3_reference_input_snapshot(
        state_root=state,
        references=references,
        frames=frames,
        reference_revision=7,
        reference_limit=5,
        provider_workflow_id="2096502793044582401",
    )
    runtime.bind_h3_reference_snapshot_owner(
        state_root=state,
        snapshot_id=queued.snapshot_id,
        snapshot_digest=queued.digest,
        task_id="queued-owner",
    )
    os.utime(storage / queued.snapshot_id, (1, 1))
    assert runtime.garbage_collect_h3_reference_input_snapshots(
        state_root=state,
        protected_ids=(snapshot_id,),
        ttl_seconds=10,
        now=10**12,
    ) == 1
    assert not (storage / orphan.snapshot_id).exists()
    assert (storage / snapshot_id).exists()
    assert (storage / queued.snapshot_id).exists()
    assert runtime.delete_h3_reference_input_snapshot(
        state_root=state, snapshot_id=queued.snapshot_id
    ) is True
    assert runtime.delete_h3_reference_input_snapshot(
        state_root=state, snapshot_id=snapshot_id
    ) is True
    assert runtime.delete_h3_reference_input_snapshot(
        state_root=state, snapshot_id=snapshot_id
    ) is False


def test_failed_attempt_can_retry_from_snapshot_after_sources_are_removed(
    tmp_path: Path,
) -> None:
    from novelvideo.media_capabilities.video import h3_reference_runtime as runtime

    project = tmp_path / "project"
    state = tmp_path / "state"
    project.mkdir()
    frame = project / "frame.png"
    Image.new("RGB", (4, 5), "green").save(frame)
    reference_path = project / "reference.png"
    reference_content = _png_bytes()
    reference_path.write_bytes(reference_content)
    frames = runtime.freeze_h3_reference_frames(
        (H3DirectorSegment(
            segment_id="s1", beat_number=1, prompt="one", duration_seconds=2,
            first_frame=str(frame),
        ),),
        project_root=project,
    )
    persisted = runtime.persist_h3_reference_input_snapshot(
        state_root=state,
        references=(_reference(reference_path, reference_content),),
        frames=frames,
        reference_revision=1,
        reference_limit=5,
        provider_workflow_id="2096502793044582401",
    )
    first_attempt = runtime.load_h3_reference_input_snapshot(
        state_root=state,
        snapshot_id=persisted.snapshot_id,
        expected_digest=persisted.digest,
        frame_sources=(str(frame),),
    )

    frame.unlink()
    reference_path.unlink()
    second_attempt = runtime.load_h3_reference_input_snapshot(
        state_root=state,
        snapshot_id=persisted.snapshot_id,
        expected_digest=persisted.digest,
        frame_sources=(str(frame),),
    )

    assert second_attempt.frames[str(frame)].content == first_attempt.frames[str(frame)].content
    assert second_attempt.references[0].content == first_attempt.references[0].content
    assert second_attempt.digest == first_attempt.digest


def test_snapshot_lease_states_protect_queued_and_expire_retained(
    tmp_path: Path, monkeypatch
) -> None:
    from novelvideo.media_capabilities.video import h3_reference_runtime as runtime

    project = tmp_path / "project"
    project.mkdir()
    frame = project / "frame.png"
    Image.new("RGB", (3, 3), "green").save(frame)
    frames = runtime.freeze_h3_reference_frames(
        (H3DirectorSegment(
            segment_id="s1", beat_number=1, prompt="one", duration_seconds=2,
            first_frame=str(frame),
        ),),
        project_root=project,
    )
    state = tmp_path / "state"
    persisted = runtime.persist_h3_reference_input_snapshot(
        state_root=state,
        references=(_reference(project / "ref.png", _png_bytes()),),
        frames=frames,
        reference_revision=1,
        reference_limit=5,
        provider_workflow_id="2096502793044582401",
    )
    monkeypatch.setattr(runtime.time, "time", lambda: 200.0)
    runtime.bind_h3_reference_snapshot_owner(
        state_root=state,
        snapshot_id=persisted.snapshot_id,
        snapshot_digest=persisted.digest,
        task_id="task-queued",
    )
    assert runtime.garbage_collect_h3_reference_input_snapshots(
        state_root=state, now=10**12, ttl_seconds=10
    ) == 0
    runtime.mark_h3_reference_snapshot_running(
        state_root=state,
        snapshot_id=persisted.snapshot_id,
    )
    runtime.retain_h3_reference_snapshot(
        state_root=state,
        snapshot_id=persisted.snapshot_id,
        ttl_seconds=10,
    )
    runtime.bind_h3_reference_snapshot_owner(
        state_root=state,
        snapshot_id=persisted.snapshot_id,
        snapshot_digest=persisted.digest,
        task_id="task-queued",
    )
    lease = json.loads(
        (
            state
            / "h3_reference_input_snapshots"
            / persisted.snapshot_id
            / "lease.json"
        ).read_text(encoding="utf-8")
    )
    assert lease["state"] == "retained"
    assert lease["owner_task_id"] == "task-queued"

    assert runtime.garbage_collect_h3_reference_input_snapshots(
        state_root=state, now=205, ttl_seconds=10
    ) == 0
    assert runtime.garbage_collect_h3_reference_input_snapshots(
        state_root=state,
        now=211,
        ttl_seconds=10,
        owner_resolver=lambda **_kwargs: False,
    ) == 1


def test_pending_snapshot_gc_honors_zero_now(tmp_path: Path) -> None:
    from novelvideo.media_capabilities.video import h3_reference_runtime as runtime

    project = tmp_path / "project"
    project.mkdir()
    frame = project / "frame.png"
    Image.new("RGB", (3, 3), "green").save(frame)
    frames = runtime.freeze_h3_reference_frames(
        (H3DirectorSegment(
            segment_id="s1", beat_number=1, prompt="one", duration_seconds=2,
            first_frame=str(frame),
        ),),
        project_root=project,
    )
    state = tmp_path / "state"
    persisted = runtime.persist_h3_reference_input_snapshot(
        state_root=state,
        references=(_reference(project / "ref.png", _png_bytes()),),
        frames=frames,
        reference_revision=1,
        reference_limit=5,
        provider_workflow_id="2096502793044582401",
    )
    lease = (
        state / "h3_reference_input_snapshots" / persisted.snapshot_id / "lease.json"
    )
    lease.write_text('{"state":"pending","expires_at":0}', encoding="utf-8")

    assert runtime.garbage_collect_h3_reference_input_snapshots(
        state_root=state,
        now=0,
        ttl_seconds=10,
        owner_resolver=lambda **_kwargs: True,
    ) == 0
    assert runtime.garbage_collect_h3_reference_input_snapshots(
        state_root=state,
        now=0,
        ttl_seconds=10,
        owner_resolver=lambda **_kwargs: False,
    ) == 1


def test_windows_gc_and_renewal_are_serialized_by_exclusive_lease_handle(
    tmp_path: Path, monkeypatch
) -> None:
    from novelvideo.media_capabilities.video import h3_reference_runtime as runtime

    snapshot_id = "a" * 32
    state = tmp_path / "state"
    storage = state / "h3_reference_input_snapshots"

    class Win32:
        DIRECTORY = 0x10
        REPARSE_POINT = 0x400

        def __init__(self):
            self.lease = {"state": "retained", "expires_at": 1}
            self.renew_wins_before_lock = True
            self.rewrites = []

        def open_write_directory(self, path):
            if Path(path).name == "b" * 32:
                raise ValueError("corrupt snapshot")
            return ("directory", Path(path))

        def attributes(self, handle):
            return self.DIRECTORY if handle[0] == "directory" else 0

        def final_path_for_handle(self, handle):
            return handle[1]

        def close(self, _handle):
            return None

        def list_names(self, directory):
            if Path(directory) == storage:
                if self.renew_wins_before_lock:
                    self.lease = {"state": "running", "expires_at": None}
                    self.renew_wins_before_lock = False
                return ("b" * 32, snapshot_id)
            return ()

        def open_exclusive_file(self, path):
            return ("lease", Path(path))

        def read_file(self, _handle, _limit):
            return json.dumps(self.lease).encode("utf-8")

        def rewrite_file(self, _handle, content):
            self.lease = json.loads(content)
            self.rewrites.append(self.lease["state"])

        def modified_time(self, _handle):
            return 0

    win32 = Win32()
    deleted = []
    fail_delete = [True]

    def delete_snapshot(_state, item, **_kwargs):
        if fail_delete[0]:
            fail_delete[0] = False
            raise OSError("interrupted delete")
        deleted.append(item)
        return True

    monkeypatch.setattr(
        runtime,
        "_delete_windows_snapshot",
        delete_snapshot,
    )

    assert runtime.garbage_collect_h3_reference_input_snapshots(
        state_root=state,
        now=100,
        ttl_seconds=10,
        win32_adapter=win32,
        platform_name="nt",
    ) == 0
    assert deleted == []

    win32.lease = {"state": "retained", "expires_at": 1}
    assert runtime.garbage_collect_h3_reference_input_snapshots(
        state_root=state,
        now=100,
        ttl_seconds=10,
        win32_adapter=win32,
        platform_name="nt",
    ) == 0
    assert win32.rewrites == ["deleting"]
    assert runtime.garbage_collect_h3_reference_input_snapshots(
        state_root=state,
        now=100 + runtime.H3_REFERENCE_INPUT_DELETING_GRACE_SECONDS - 1,
        ttl_seconds=10,
        win32_adapter=win32,
        platform_name="nt",
    ) == 0
    assert runtime.garbage_collect_h3_reference_input_snapshots(
        state_root=state,
        now=100 + runtime.H3_REFERENCE_INPUT_DELETING_GRACE_SECONDS,
        ttl_seconds=10,
        win32_adapter=win32,
        platform_name="nt",
    ) == 1
    assert deleted == [snapshot_id]
    with pytest.raises(ValueError, match="transition"):
        runtime._transition_h3_reference_snapshot_lease(
            state_root=state,
            snapshot_id=snapshot_id,
            target_state="running",
            allowed_states=frozenset({"queued", "retained", "running"}),
            ttl_seconds=None,
            win32_adapter=win32,
            platform_name="nt",
        )


def test_reference_input_snapshot_store_rejects_tampered_blob(tmp_path: Path) -> None:
    from novelvideo.media_capabilities.video import h3_reference_runtime as runtime

    project = tmp_path / "project"
    project.mkdir()
    frame = project / "frame.png"
    Image.new("RGB", (3, 3), "green").save(frame)
    frames = runtime.freeze_h3_reference_frames(
        (H3DirectorSegment(
            segment_id="s1", beat_number=1, prompt="one", duration_seconds=2,
            first_frame=str(frame),
        ),),
        project_root=project,
    )
    state = tmp_path / "state"
    persisted = runtime.persist_h3_reference_input_snapshot(
        state_root=state,
        references=(_reference(project / "ref.png", _png_bytes()),),
        frames=frames,
        reference_revision=1,
        reference_limit=5,
        provider_workflow_id="2096502793044582401",
    )
    snapshot_id = persisted.snapshot_id
    snapshot_dir = state / "h3_reference_input_snapshots" / snapshot_id
    frame_blob = next((snapshot_dir / "frames").iterdir())
    changed = BytesIO()
    Image.new("RGB", (3, 3), "red").save(changed, format="PNG")
    changed_content = changed.getvalue()
    frame_blob.write_bytes(changed_content)
    descriptor_path = snapshot_dir / "snapshot.json"
    descriptor = json.loads(descriptor_path.read_text(encoding="utf-8"))
    descriptor["frames"][0]["sha256"] = hashlib.sha256(changed_content).hexdigest()
    descriptor_path.write_text(
        json.dumps(
            descriptor, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="digest"):
        runtime.load_h3_reference_input_snapshot(
            state_root=state,
            snapshot_id=snapshot_id,
            expected_digest=persisted.digest,
            frame_sources=(str(frame),),
        )


def test_reference_input_snapshot_store_rejects_symlink_storage(
    tmp_path: Path,
) -> None:
    from novelvideo.media_capabilities.video import h3_reference_runtime as runtime

    project = tmp_path / "project"
    state = tmp_path / "state"
    outside = tmp_path / "outside"
    project.mkdir()
    state.mkdir()
    outside.mkdir()
    (state / "h3_reference_input_snapshots").symlink_to(
        outside, target_is_directory=True
    )
    frame = project / "frame.png"
    Image.new("RGB", (3, 3), "green").save(frame)
    frames = runtime.freeze_h3_reference_frames(
        (H3DirectorSegment(
            segment_id="s1", beat_number=1, prompt="one", duration_seconds=2,
            first_frame=str(frame),
        ),),
        project_root=project,
    )

    with pytest.raises((OSError, ValueError)):
        runtime.persist_h3_reference_input_snapshot(
            state_root=state,
            references=(_reference(project / "ref.png", _png_bytes()),),
            frames=frames,
            reference_revision=1,
            reference_limit=5,
            provider_workflow_id="2096502793044582401",
        )
    assert list(outside.iterdir()) == []


@pytest.mark.asyncio
async def test_reference_runtime_validates_all_local_inputs_before_upload(
    tmp_path: Path, monkeypatch
) -> None:
    from novelvideo.media_capabilities.video import h3_reference_runtime as runtime

    uploads = []

    class Client:
        async def upload(self, path):
            uploads.append(Path(path).read_bytes())
            return "uploaded://asset"

        async def close(self):
            return None

    configured = SimpleNamespace(
        account=SimpleNamespace(
            id="ref-test", max_concurrency=1, capability_limits={}, queue_limit=2
        ),
        create_client=Client,
    )
    monkeypatch.setattr(runtime, "_load_runtime", lambda: configured)
    missing = tmp_path / "missing.png"
    reference = _reference(tmp_path / "deleted-reference.png", _png_bytes())

    with pytest.raises(ValueError, match="missing.*H3 frame"):
        await runtime.generate_h3_reference_director_video(
            SimpleNamespace(runtime_dir=tmp_path / "runtime"),
            segments=(H3DirectorSegment(
                segment_id="s1", beat_number=1, prompt="走近",
                duration_seconds=2, first_frame=str(missing),
            ),),
            output_path=str(tmp_path / "out.mp4"),
            aspect_ratio="9:16", resolution="720p", mode="auto",
            global_references=(reference,), reference_limit=5,
            workflow_id="2096502793044582401",
        )

    assert uploads == []


@pytest.mark.asyncio
async def test_reference_runtime_uploads_frozen_reference_bytes_and_complete_frames(
    tmp_path: Path, monkeypatch
) -> None:
    from novelvideo.media_capabilities.video import h3_reference_runtime as runtime

    first = tmp_path / "first.png"
    last = tmp_path / "last.png"
    ref_path = tmp_path / "reference.png"
    Image.new("RGB", (11, 13), "red").save(first)
    Image.new("RGB", (17, 19), "blue").save(last)
    Image.new("RGB", (7, 9), "green").save(ref_path)
    frozen_reference = ref_path.read_bytes()
    reference = _reference(ref_path, frozen_reference)
    ref_path.write_bytes(b"changed-after-resolution")
    segment = H3DirectorSegment(
        segment_id="s1", beat_number=1, prompt="走近",
        duration_seconds=2, first_frame=str(first), last_frame=str(last),
    )
    frozen_frames = runtime.freeze_h3_reference_frames(
        (segment,), project_root=tmp_path
    )
    frozen_first = first.read_bytes()
    frozen_last = last.read_bytes()
    first.write_bytes(b"changed-after-group-preflight")
    last.write_bytes(b"changed-after-group-preflight")
    uploads = []
    captured = {}
    submitted = []
    runtime_root = tmp_path / "runtime"
    artifact = runtime_root / "media_h3_ref" / "artifacts" / "generated.mp4"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"video")

    class Client:
        async def upload(self, path):
            content = Path(path).read_bytes()
            uploads.append(content)
            return f"uploaded://{len(uploads)}"

        async def close(self):
            return None

    class Pipeline:
        def __init__(self, **kwargs):
            captured["profile"] = kwargs["workflow_profile"]

        async def generate_timeline(self, request, **kwargs):
            captured["request"] = request
            captured.update(kwargs)
            callback = kwargs.get("on_provider_submitted")
            if callback is not None:
                await callback("provider-7")
            return SimpleNamespace(
                status=runtime.MediaTaskStatus.SUCCEEDED,
                quality_issues=(),
                artifact=SimpleNamespace(local_path="generated.mp4"),
                provider_task_id="provider-7",
                probe=SimpleNamespace(width=736, height=1280),
            )

    configured = SimpleNamespace(
        account=SimpleNamespace(
            id="ref-test", max_concurrency=1, capability_limits={}, queue_limit=2
        ),
        create_client=Client,
    )
    monkeypatch.setattr(runtime, "_load_runtime", lambda: configured)
    monkeypatch.setattr(runtime, "H3VideoPipeline", Pipeline)

    async def on_submitted(task_id):
        submitted.append(task_id)

    result = await runtime.generate_h3_reference_director_video(
        SimpleNamespace(runtime_dir=runtime_root),
        segments=(segment,),
        output_path=str(tmp_path / "out.mp4"),
        aspect_ratio="9:16", resolution="720p", mode="auto",
        global_references=(reference,), reference_limit=5,
        workflow_id="2096502793044582401",
        frozen_frames=frozen_frames,
        on_provider_submitted=on_submitted,
    )

    assert uploads[0] == frozen_reference
    assert set(uploads[1:]) == {frozen_first, frozen_last}
    assert captured["profile"].workflow_id == "2096502793044582401"
    assert not captured["request"].reference_images
    assert captured["request"].capability.value == "video.fl2va"
    assert "first.png" not in str(captured["idempotency_input"])
    assert captured["idempotency_input"]["compiler_version"] == 5
    assert captured["idempotency_input"]["references"] == [{
        "picture_index": 1,
        "reference_id": "ref-1",
        "source_kind": "character_identity",
        "label": "阿明",
        "subject_description": "阿明，黑色短发",
        "sha256": hashlib.sha256(frozen_reference).hexdigest(),
    }]
    assert result.provider_task_id == "provider-7"
    assert submitted == ["provider-7"]
    assert Path(result.output_path).read_bytes() == b"video"
    assert not (runtime_root / "media_h3_ref" / "staging").exists()


@pytest.mark.asyncio
async def test_reference_runtime_removes_partial_staging_when_chmod_fails(
    tmp_path: Path, monkeypatch
) -> None:
    from novelvideo.media_capabilities.video import h3_reference_runtime as runtime

    frame = tmp_path / "frame.png"
    Image.new("RGB", (5, 5), "black").save(frame)
    configured = SimpleNamespace(
        account=SimpleNamespace(
            id="cleanup-test", max_concurrency=1, capability_limits={}, queue_limit=2
        ),
        create_client=lambda: pytest.fail("client must not be created"),
    )
    monkeypatch.setattr(runtime, "_load_runtime", lambda: configured)
    original_chmod = Path.chmod

    def fail_staging_chmod(path, mode):
        if path.parent.name == "staging":
            raise OSError("chmod failed")
        return original_chmod(path, mode)

    monkeypatch.setattr(Path, "chmod", fail_staging_chmod)

    with pytest.raises(OSError, match="chmod failed"):
        await runtime.generate_h3_reference_director_video(
            SimpleNamespace(runtime_dir=tmp_path / "runtime"),
            segments=(H3DirectorSegment(
                segment_id="s1", beat_number=1, prompt="走近", duration_seconds=2,
                first_frame=str(frame),
            ),),
            output_path=str(tmp_path / "out.mp4"), aspect_ratio="9:16",
            resolution="720p", global_references=(
                _reference(tmp_path / "gone.png", _png_bytes()),
            ), reference_limit=5, workflow_id="2096502793044582401",
        )

    assert not (tmp_path / "runtime" / "media_h3_ref" / "staging").exists()


@pytest.mark.asyncio
async def test_reference_runtime_preserves_primary_error_when_close_fails_and_cleans(
    tmp_path: Path, monkeypatch
) -> None:
    from novelvideo.media_capabilities.video import h3_reference_runtime as runtime

    frame = tmp_path / "frame.png"
    Image.new("RGB", (5, 5), "black").save(frame)

    class Client:
        async def upload(self, _path):
            return "uploaded://asset"

        async def close(self):
            raise OSError("close failed")

    class Pipeline:
        def __init__(self, **_kwargs):
            pass

        async def generate_timeline(self, *_args, **_kwargs):
            raise RuntimeError("primary transport failure")

    configured = SimpleNamespace(
        account=SimpleNamespace(
            id="cleanup-close-test", max_concurrency=1,
            capability_limits={}, queue_limit=2,
        ),
        create_client=Client,
    )
    monkeypatch.setattr(runtime, "_load_runtime", lambda: configured)
    monkeypatch.setattr(runtime, "H3VideoPipeline", Pipeline)

    with pytest.raises(RuntimeError, match="primary transport failure"):
        await runtime.generate_h3_reference_director_video(
            SimpleNamespace(runtime_dir=tmp_path / "runtime"),
            segments=(H3DirectorSegment(
                segment_id="s1", beat_number=1, prompt="走近", duration_seconds=2,
                first_frame=str(frame),
            ),),
            output_path=str(tmp_path / "out.mp4"), aspect_ratio="9:16",
            resolution="720p", global_references=(
                _reference(tmp_path / "gone.png", _png_bytes()),
            ), reference_limit=5, workflow_id="2096502793044582401",
        )

    assert not (tmp_path / "runtime" / "media_h3_ref" / "staging").exists()
