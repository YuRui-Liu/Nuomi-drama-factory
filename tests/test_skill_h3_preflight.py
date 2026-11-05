import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

from PIL import Image

from tests.media_capabilities.video.test_h3_episode_pack import _plan as corridor_plan
from tests.media_capabilities.video.test_h3_prompt_compiler import _v3_plan
from novelvideo.media_capabilities.video.h3_prepared_plans import (
    prepared_h3_context_digest,
)
from novelvideo.media_capabilities.video.h3_prompt_optimizer import H3PromptContext
from novelvideo.media_capabilities.video.models import H3Mode
from novelvideo.narrative_groups.video_repairs import (
    VideoRepairPriorEntry,
    VideoRepairSnapshot,
    _snapshot_digest,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "skills" / "nuomi-canvas-production" / "scripts" / "h3_preflight.py"


def _bundle(tmp_path: Path, *, leak: bool = False, size=(1600, 900)) -> dict:
    frame = tmp_path / "first.png"
    Image.new("RGB", size, color=(80, 90, 100)).save(frame)
    frame_sha = hashlib.sha256(frame.read_bytes()).hexdigest()
    input_hash = "b" * 64
    pins = {
        "project_id": "project-fixture",
        "episode": 1,
        "group_id": "G01",
        "workflow_id": "runninghub:minimax-h3",
        "requested_mode": "auto",
        "dialogue_source": "external_tts",
        "video_revision": 0,
        "plan_revision": 1,
        "settings_revision": 0,
        "reference_revision": 0,
        "director_revision_id": "external-fixture",
        "keyframe_revision": 0,
        "keyframe_binding_sha256": "c" * 64,
    }
    source_segment = {
        "segment_id": "S01",
        "source_shot_ids": ["S01"],
        "beat_number": 1,
        "prompt": "Lin looks at the door.",
        "duration_seconds": 5.0,
        "first_frame": str(frame),
        "last_frame": None,
        "dialogue": "",
        "speaker": "",
        "tone": "",
        "dialogue_lines": [],
        "voice_style": "",
        "dialogue_source": "external_tts",
    }
    compiler_context = H3PromptContext(
        visual_description="Lin stands in a repair shop.",
        narration="",
        prev_summary="",
        next_summary="",
        first_frame_sha256=frame_sha,
        model_id="fixture",
        style_prefix="2.5D ink animation",
        active_character_ids=("lin",),
    ).model_dump(mode="json")
    context_segment = {
        "segment_id": "S01",
        "source_shot_ids": ["S01"],
        "mode": "i2va",
        "duration_seconds": 5.0,
        "total_frames": 120,
        "first_frame_sha256": frame_sha,
        "last_frame_sha256": None,
        "input_hash": input_hash,
        "source_segment": source_segment,
        "compiler_context": compiler_context,
    }
    preparation_sha = prepared_h3_context_digest(
        pins, [("S01", input_hash)]
    )
    context = {
        "schema_version": 1,
        "workflow_id": "runninghub:minimax-h3",
        "pins": pins,
        "preparation_sha256": preparation_sha,
        "segments": [context_segment],
    }

    plan = corridor_plan().model_dump(mode="json")
    plan["first_frame_anchor"]["sha256"] = frame_sha
    rigid = plan["rigid_prompt"]
    rigid["active_references"] = []
    rigid["positive_constraints"] = [
        {"assertion": "Show exactly one visible character.", "count": 1, "target": "characters"},
        {"assertion": "Use zero resolved references.", "count": 0, "target": "references"},
        {"assertion": "Use zero visible props.", "count": 0, "target": "props"},
    ]
    # An off-screen look target is not a visible character inventory assertion.
    rigid["spatial_blocking"][0]["subjects"][0]["gaze"] = "toward off-screen 朏朏"
    if leak:
        rigid["physics"]["statements"].append(
            "朏朏 keeps natural quadruped anatomy and one continuous white tail."
        )
        rigid["quality"]["requirements"].append(
            "Keep 朏朏's animal anatomy consistent."
        )
    prepared = {
        "schema_version": 1,
        "preparation_sha256": preparation_sha,
        "pins": pins,
        "segments": [
            {
                "segment_id": "S01",
                "input_hash": input_hash,
                "director_plan": plan,
            }
        ],
    }
    return {
        "target_aspect_ratio": "16:9",
        "character_names": ["lin", "朏朏"],
        "groups": [{"context": context, "prepared": prepared}],
    }


def _repair_snapshot(group_id: str, director_revision_id: str) -> VideoRepairSnapshot:
    prior_entries = (
        VideoRepairPriorEntry(
            segment_id="S01",
            source_shot_ids=("S01",),
            physical_video=f"prior/{group_id}-S01.mp4",
            physical_video_sha256="d" * 64,
            duration_seconds=5.0,
            entry_json=json.dumps({"segment_id": "S01"}, separators=(",", ":")),
            reuse=False,
        ),
        VideoRepairPriorEntry(
            segment_id="S02",
            source_shot_ids=("S02",),
            physical_video=f"prior/{group_id}-S02-4x3.mp4",
            physical_video_sha256="e" * 64,
            duration_seconds=5.0,
            entry_json=json.dumps(
                {"segment_id": "S02", "source_aspect_ratio": "4:3"},
                separators=(",", ":"),
            ),
            reuse=True,
        ),
    )
    snapshot_fields = {
        "schema_version": 1,
        "source_manifest_path": f"prior/{group_id}-manifest.json",
        "source_manifest_sha256": "c" * 64,
        "episode": 1,
        "group_id": group_id,
        "director_revision_id": director_revision_id,
        "expected_source_shot_ids": ("S01", "S02"),
        "repair_shot_ids": ("S01",),
        "prior_entries": prior_entries,
    }
    digest_fields = {
        **snapshot_fields,
        "prior_entries": [entry.model_dump(mode="json") for entry in prior_entries],
        "expected_source_shot_ids": ["S01", "S02"],
        "repair_shot_ids": ["S01"],
    }
    return VideoRepairSnapshot(
        **snapshot_fields,
        snapshot_sha256=_snapshot_digest(digest_fields),
    )


def _h3_ref_repair_bundle(tmp_path: Path, *, group_count: int = 4) -> dict:
    bundle = _bundle(tmp_path)
    template_group = bundle["groups"][0]
    groups = []
    for index in range(1, group_count + 1):
        group_id = f"G{index:02d}"
        group = json.loads(json.dumps(template_group))
        context = group["context"]
        prepared = group["prepared"]
        pins = context["pins"]
        pins["group_id"] = group_id
        pins["workflow_id"] = "runninghub:minimax-h3-ref"
        pins["reference_binding_sha256"] = "a" * 64
        snapshot = _repair_snapshot(group_id, pins["director_revision_id"])
        pins["source_reuse_digest"] = snapshot.snapshot_sha256
        context["workflow_id"] = pins["workflow_id"]
        context["source_reuse_snapshot"] = snapshot.model_dump(mode="json")

        segment = context["segments"][0]
        segment["mode"] = H3Mode.REF2VA.value
        segment["source_shot_ids"] = ["S01"]
        segment["duration_seconds"] = 101 / 24
        segment["total_frames"] = 101
        segment["source_segment"]["duration_seconds"] = 101 / 24
        segment["compiler_context"]["resolved_reference_tags"] = ["@lin"]
        segment["compiler_context"]["style_prefix"] = "cinematic realism"
        segment["compiler_context"]["resolved_references"] = [
            {
                "tag": "@lin",
                "reference_id": "lin-portrait",
                "provider_subject": "<Subject 1>",
                "kind": "character",
                "label": "Lin portrait",
                "description": "Lin in a black coat",
            }
        ]

        plan = _v3_plan(H3Mode.REF2VA).model_dump(mode="json")
        plan["rigid_prompt"]["active_references"][0]["tag"] = "@lin"
        plan["reference_subjects"][0]["speaker_id"] = None
        plan["shots"][0]["dialogue"] = []
        plan["shots"][0]["actions"][1]["description"] = (
            "He turns slowly toward the handle and grips it, ending with his hand fixed on the handle."
        )
        plan["rigid_prompt"]["physics"]["statements"] = [
            "Lin's weight stays supported through contact with both feet, and his inertia settles after the turn."
        ]
        prepared["pins"] = json.loads(json.dumps(pins))
        prepared["segments"][0]["director_plan"] = plan
        input_hash = segment["input_hash"]
        preparation_sha = prepared_h3_context_digest(pins, [("S01", input_hash)])
        context["preparation_sha256"] = preparation_sha
        prepared["preparation_sha256"] = preparation_sha
        groups.append(group)
    bundle["groups"] = groups
    return bundle


def _run_preflight(tmp_path: Path, bundle: dict) -> subprocess.CompletedProcess:
    bundle_path = tmp_path / "bundle.json"
    bundle_path.write_text(json.dumps(bundle, ensure_ascii=False), encoding="utf-8")
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--bundle", str(bundle_path)],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_preflight_blocks_inactive_named_character_leaked_in_positive_prompt_fields(tmp_path):
    completed = _run_preflight(tmp_path, _bundle(tmp_path, leak=True))

    report = json.loads(completed.stdout)
    assert completed.returncode == 1
    assert report["ok"] is False
    assert any(
        item["code"] == "inactive_character_leak"
        and item["segment_id"] == "S01"
        for item in report["blockers"]
    )
    assert all(
        "gaze" not in item.get("field", "")
        for item in report["blockers"]
    )


def test_preflight_accepts_clean_widescreen_plan_and_reports_frame_only_conditioning(tmp_path):
    completed = _run_preflight(tmp_path, _bundle(tmp_path))

    report = json.loads(completed.stdout)
    assert completed.returncode == 0
    assert report["ok"] is True
    assert report["blockers"] == []
    assert any(item["code"] == "base_frame_only_conditioning" for item in report["warnings"])
    assert report["groups"][0]["identity_reference_count"] == 0


def test_preflight_blocks_stale_segment_hash(tmp_path):
    bundle = _bundle(tmp_path)
    bundle["groups"][0]["prepared"]["segments"][0]["input_hash"] = "f" * 64
    completed = _run_preflight(tmp_path, bundle)

    report = json.loads(completed.stdout)
    assert completed.returncode == 1
    assert any(item["code"] in {"segment_input_hash_mismatch", "preparation_digest_mismatch"}
               for item in report["blockers"])


def test_preflight_blocks_endpoint_aspect_mismatch(tmp_path):
    completed = _run_preflight(tmp_path, _bundle(tmp_path, size=(1200, 900)))

    report = json.loads(completed.stdout)
    assert completed.returncode == 1
    assert any(item["code"] == "frame_aspect_ratio_mismatch" for item in report["blockers"])


def test_preflight_blocks_frame_bytes_that_differ_from_pinned_hash(tmp_path):
    bundle = _bundle(tmp_path)
    bundle["groups"][0]["context"]["segments"][0]["first_frame_sha256"] = "d" * 64
    completed = _run_preflight(tmp_path, bundle)

    report = json.loads(completed.stdout)
    assert completed.returncode == 1
    assert any(item["code"] == "frame_hash_mismatch" for item in report["blockers"])


def test_preflight_accepts_four_h3_ref_repair_contexts_with_reused_old_clips(tmp_path):
    completed = _run_preflight(tmp_path, _h3_ref_repair_bundle(tmp_path))

    report = json.loads(completed.stdout)
    assert completed.returncode == 0
    assert report["ok"] is True
    assert len(report["groups"]) == 4
    assert all(group["workflow_id"] == "runninghub:minimax-h3-ref" for group in report["groups"])
    assert all(group["selected_reference_count"] == 1 for group in report["groups"])
    assert all(group["segment_count"] == 1 for group in report["groups"])


def test_preflight_blocks_h3_ref_plan_subject_not_bound_to_selected_reference(tmp_path):
    bundle = _h3_ref_repair_bundle(tmp_path, group_count=1)
    bundle["groups"][0]["context"]["segments"][0]["compiler_context"][
        "resolved_references"
    ][0]["provider_subject"] = "<Subject 2>"
    completed = _run_preflight(tmp_path, bundle)

    report = json.loads(completed.stdout)
    assert completed.returncode == 1
    assert any(item["code"] == "plan_reference_subject_mismatch" for item in report["blockers"])


def test_preflight_blocks_stale_source_reuse_snapshot_pin(tmp_path):
    bundle = _h3_ref_repair_bundle(tmp_path, group_count=1)
    bundle["groups"][0]["context"]["pins"]["source_reuse_digest"] = "f" * 64
    completed = _run_preflight(tmp_path, bundle)

    report = json.loads(completed.stdout)
    assert completed.returncode == 1
    assert any(item["code"] == "source_reuse_digest_mismatch" for item in report["blockers"])
