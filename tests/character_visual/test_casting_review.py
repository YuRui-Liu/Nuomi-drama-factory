import asyncio
from types import SimpleNamespace

import pytest
from PIL import Image

from tests.character_visual.test_casting_store import pending, store_at


def ready(tmp_path):
    store = store_at(tmp_path)
    store.create_pending(pending())
    store.claim_generation("c1", task_id="task1")
    path = store.output_path("c1")
    path.parent.mkdir(parents=True)
    Image.new("RGB", (8, 8)).save(path)
    store.complete_generation("c1", path)
    return store


def findings():
    return [dict(finding_id=d, dimension=d, verdict="unjudgeable", description="不可见或无对照")
            for d in ("facts", "design", "distinctiveness")]


def test_review_contract_exists():
    from novelvideo.character_visual import casting_review
    assert callable(casting_review.validate_review_report)


@pytest.mark.parametrize("fault", ["missing", "duplicate", "unknown_fact", "no_reference_pass"])
def test_invalid_findings_rejected(fault):
    from novelvideo.character_visual.casting_review import validate_review_report
    rows = findings()
    if fault == "missing": rows.pop()
    if fault == "duplicate": rows[1]["finding_id"] = rows[0]["finding_id"]
    if fault == "unknown_fact": rows[0]["fact_ids"] = ["invented"]
    if fault == "no_reference_pass": rows[2]["verdict"] = "conforms"
    with pytest.raises(ValueError):
        validate_review_report(dict(findings=rows, reviewer="server", model="m", version="1"), pending().snapshot)


def test_attempt_cas_and_duplicate_delivery(tmp_path):
    store = ready(tmp_path)
    assert store.begin_review("c1", task_id="review1", attempt_id="a")
    assert not store.begin_review("c1", task_id="review1", attempt_id="a")
    assert store.begin_review("c1", task_id="review2", attempt_id="b")
    assert not store.fail_review("c1", attempt_id="a", error="review_failed")
    assert store.fail_review("c1", attempt_id="b", error="review_failed")
    assert not store.begin_review("c1", task_id="review1", attempt_id="a")
    assert store.get("c1").generation_status == "succeeded"


@pytest.mark.parametrize("mode", ["ok", "invalid", "timeout", "tamper", "cancel"])
def test_review_runtime_and_safe_failure(tmp_path, mode):
    from novelvideo.character_visual.casting_review import review_candidate
    store = ready(tmp_path)
    original = store.get("c1")
    calls = []
    class Runtime:
        snapshot = SimpleNamespace(task_role="identity_sheet_qc", runtime="model_api", model="configured-model")
        async def run_structured(self, **kwargs):
            calls.append(kwargs)
            assert kwargs["images"][0].data == store.read_verified_asset("c1")
            if mode == "timeout": raise asyncio.TimeoutError("secret token")
            if mode == "cancel": raise asyncio.CancelledError()
            if mode == "tamper": Image.new("RGB", (8, 8), "red").save(original.asset_path)
            return {"findings": [] if mode == "invalid" else findings()}
    async def run():
        return await review_candidate(store=store, candidate_id="c1", task_id="review1", attempt_id="a", references=[], runtime=Runtime())
    if mode == "cancel":
        with pytest.raises(asyncio.CancelledError): asyncio.run(run())
    else:
        asyncio.run(run())
    result = store.get("c1")
    assert result.review_status == ("completed" if mode == "ok" else "failed")
    assert result.snapshot == original.snapshot
    assert result.asset_path == original.asset_path
    assert result.generation_metadata == original.generation_metadata
    assert "secret" not in (result.error or "")
    assert result.review_attempts[0].provenance["policy_version"] == "casting-review-v1"
    assert result.review_attempts[0].provenance["model"] == "configured-model"
    asyncio.run(run())
    assert len(calls) == 1


@pytest.mark.parametrize("kind", ["digest", "outside", "symlink", "project", "relationship"])
def test_reference_integrity(tmp_path, kind):
    from novelvideo.character_visual.casting_review import ReviewReference, read_reference
    store = ready(tmp_path)
    candidate = store.get("c1")
    values = dict(project_id="project", character_id="other", candidate_id="workflow-version", version="v1",
                  asset_path=candidate.asset_path, asset_sha256=candidate.asset_sha256)
    if kind == "digest": values["asset_sha256"] = "0" * 64
    if kind == "outside": values["asset_path"] = str(tmp_path / "outside.png")
    if kind == "project": values["project_id"] = "other"
    if kind == "symlink":
        link = store.project_dir / "link.png"
        link.symlink_to(candidate.asset_path)
        values["asset_path"] = str(link)
    if kind == "relationship":
        values["relationship_facts"] = [dict(fact_id="kin", field="relationship", value="siblings", evidence="siblings",
                                              source_span=dict(start_line=1, end_line=1), confidence=1)]
    with pytest.raises(ValueError): read_reference(store, ReviewReference(**values))


def test_invisible_marker_cannot_pass_without_visibility(tmp_path):
    from novelvideo.character_visual.casting_review import validate_review_report
    rows = findings()
    rows[0].update(verdict="conforms", visibility="not_visible")
    with pytest.raises(ValueError):
        validate_review_report(dict(findings=rows, reviewer="server", model="m", version="1"), pending().snapshot)


def test_runner_ownership_rejected_before_review(tmp_path):
    from novelvideo.task_backend.runners.character_casting_review import run_character_casting_review
    store = ready(tmp_path)
    ctx = SimpleNamespace(output_dir=store.project_dir, state_dir=store.state_dir, project_id="project")
    with pytest.raises(ValueError):
        run_character_casting_review(dict(project_id="other", task_id="review", payload=dict(candidate_id="c1", character_id="甲")), ctx)


def test_pending_cannot_review(tmp_path):
    store = store_at(tmp_path)
    store.create_pending(pending())
    with pytest.raises(ValueError): store.begin_review("c1", task_id="review", attempt_id="a")


def test_age_beauty_and_design_are_snapshot_evidence():
    from novelvideo.character_visual.casting_review import build_review_input, validate_review_report
    from tests.character_visual.test_casting_compiler import compile_inputs, inputs
    snapshot = compile_inputs(inputs(("甲七十岁", dict(field="age_range", value="七十岁")),
                                     ("甲很漂亮", dict(field="beauty", value="漂亮"))))
    candidate = pending().model_copy(update={"snapshot": snapshot})
    data = build_review_input(candidate, [])
    assert "七十岁" in str(data) and "漂亮" in str(data)
    rows = findings()
    rows[0].update(verdict="deviation", fact_ids=[snapshot.hard_constraints[0].fact_id], description="面部年龄明显年轻", visibility="visible")
    rows[1].update(verdict="deviation", decision_ids=[snapshot.design_decisions[0].decision_id], description="美化偏离所选骨相", visibility="visible")
    report = validate_review_report(dict(findings=rows, reviewer="server", model="m", version="1"), snapshot)
    assert report.findings[0].verdict == "deviation"


def test_report_rejects_invisible_even_with_valid_fact():
    from novelvideo.character_visual.casting_review import validate_review_report
    from tests.character_visual.test_casting_compiler import compile_inputs, inputs
    snapshot = compile_inputs(inputs(("甲七十岁", dict(field="age_range", value="七十岁"))))
    rows = findings()
    rows[0].update(verdict="conforms", fact_ids=[snapshot.hard_constraints[0].fact_id], visibility="not_visible")
    with pytest.raises(ValueError):
        validate_review_report(dict(findings=rows, reviewer="server", model="m", version="1"), snapshot)


def test_sourced_kinship_and_reference_scope(tmp_path):
    from novelvideo.character_visual.casting_review import ReviewReference, build_review_input, read_reference, validate_review_report
    store = ready(tmp_path)
    candidate = store.get("c1")
    ref = ReviewReference(project_id="project", character_id="sibling", candidate_id="workflow:v1", version="v1",
        asset_path=candidate.asset_path, asset_sha256=candidate.asset_sha256,
        relationship_facts=[dict(fact_id="kin", field="relationship", value="siblings", evidence="原文二人为亲兄弟",
            source_span=dict(start_line=1, end_line=1), confidence=1, source_document="story", source_revision="source1")])
    assert read_reference(store, ref) == store.read_verified_asset("c1")
    assert build_review_input(candidate, [ref])["references"][0]["relationship_facts"][0]["evidence"] == "原文二人为亲兄弟"
    rows = findings()
    rows[2].update(verdict="conforms", visibility="visible", reference_candidate_ids=["workflow:v1"], description="亲缘相似有原文依据，身份区别仍可识别")
    assert validate_review_report(dict(findings=rows, reviewer="server", model="m", version="1"), candidate.snapshot, [ref])
