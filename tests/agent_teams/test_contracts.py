import pytest
from pydantic import ValidationError
from novelvideo.agent_teams.catalog import ROLE_CATALOG
from novelvideo.agent_teams.models import ExecutionSnapshot, MethodConfig, ProjectDraft, ResourceVersion, TeamVersion
from novelvideo.agent_teams.resolver import resolve_fields


def method():
    return dict(model="project", prompt="Create a story", skills=[{"id": "skill", "revision": 2}],
                references=[], director_preferences={"pace": "slow"})


def test_field_override_inherits_and_explicit_empty_replaces():
    base = method()
    result = resolve_fields(base, {"prompt": "Other", "skills": []})
    assert result["prompt"] == "Other"
    assert result["model"] == base["model"]
    assert result["skills"] == []
    result["director_preferences"]["pace"] = "fast"
    assert base["director_preferences"]["pace"] == "slow"
    assert len(base["skills"]) == 1


@pytest.mark.parametrize("override", [
    {"unknown": 1}, {"model": ""}, {"model": 3}, {"skills": None},
    {"skills": ["unpinned"]}, {"references": [{"id": "x"}]},
    {"skills": [{"id": "p", "revision": 0}]}, {"api_key": "secret"},
    {"director_preferences": {"unknown": "x"}},
    {"director_preferences": {"pace": b"fast"}},
])
def test_rejects_invalid_overrides(override):
    with pytest.raises((ValidationError, ValueError)):
        resolve_fields(method(), override)


def test_resources_and_versions_reject_assignment_and_copy_inputs():
    resource = ResourceVersion(id="x", kind="skill", revision=1, owner="local", content="text", content_hash="sha")
    with pytest.raises(ValidationError):
        resource.revision = 2
    source = {"writer": {"script_creation_generation": method()}}
    team = TeamVersion(id="t", revision=1, name="Team", owner="local", roles=source)
    source["writer"]["script_creation_generation"]["prompt"] = "changed"
    assert team.roles["writer"]["script_creation_generation"].prompt == "Create a story"
    assert TeamVersion.model_validate_json(team.model_dump_json()) == team


def test_prompt_resource_is_versioned():
    resource = ResourceVersion(id="prompt", kind="prompt", revision=1, owner="local",
                               content="Write", content_hash="sha")
    assert resource.kind == "prompt"


def test_draft_validates_fields_and_does_not_share_overrides():
    source = {"writer": {"script_creation_generation": {"skills": []}}}
    draft = ProjectDraft(project_id="p", template_id="t", template_revision=1, overrides=source)
    source["writer"]["script_creation_generation"]["skills"].append("mutated")
    assert draft.overrides["writer"]["script_creation_generation"]["skills"] == []
    with pytest.raises(ValidationError):
        ProjectDraft(project_id="p", template_id="t", template_revision=1,
                     overrides={"writer": {"script_creation_generation": {"unknown": True}}})


def test_catalog_is_fixed_and_honest_about_connection():
    assert {r.id for r in ROLE_CATALOG} == {
        "writer", "script_review", "script_parser", "director", "shot_review",
        "asset_design", "asset_review", "video_director",
    }
    assert all(r.subtasks and not r.connected and r.adapter_id is None for r in ROLE_CATALOG)


def test_snapshot_is_pinned_and_forbids_credentials():
    values = dict(id="run", project_id="p", template_id="t", template_revision=1, active_revision=1,
                  role_id="writer", subtask_id="script_creation_generation", input_revision="1", input_hash="sha",
                  resolved_method=method(), resolved_model={"runtime": "codex", "model": "gpt-5.5"},
                  resource_snapshots=[dict(id="skill", revision=2, kind="skill", owner="local", content="Guide", content_hash="sha")])
    snapshot = ExecutionSnapshot(**values)
    assert snapshot.resolved_model.runtime == "codex"
    with pytest.raises(ValidationError):
        ExecutionSnapshot(**values, api_key="secret")
    with pytest.raises(ValidationError):
        snapshot.input_hash = "changed"
    assert ExecutionSnapshot.model_validate_json(snapshot.model_dump_json()) == snapshot
    for resources in ([], [dict(values["resource_snapshots"][0], revision=1)],
                      [dict(values["resource_snapshots"][0], kind="reference")],
                      values["resource_snapshots"] * 2,
                      values["resource_snapshots"] + [dict(values["resource_snapshots"][0], id="other")]):
        with pytest.raises(ValidationError):
            ExecutionSnapshot(**dict(values, resource_snapshots=resources))
    with pytest.raises(ValidationError, match="conflicting resource pins"):
        ExecutionSnapshot(**dict(values, resolved_method=dict(method(), skills=[
            {"id": "skill", "revision": 2}, {"id": "skill", "revision": 3}])))


def test_template_owner_is_required():
    with pytest.raises(ValidationError):
        TeamVersion(id="t", revision=1, name="Team")
