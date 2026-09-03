from novelvideo.production_workflow import (
    AdoptionStatus,
    AssetSlot,
    AssetVersion,
    ExecutionStatus,
    GenerationAttempt,
    GenerationRouteSnapshot,
    ProjectProductionSettings,
)


def test_execution_and_adoption_states_are_orthogonal():
    attempt = GenerationAttempt(
        attempt_id="attempt-1",
        slot_id="character:lin-mo:portrait",
        status=ExecutionStatus.PROVIDER_PROCESSING,
        input_snapshot={"prompt": "compiled snapshot"},
        routing_snapshot={"provider": "grsai", "workflow": "portrait-v1"},
        provider_task_id="provider-42",
        cost={"amount": 0.12, "currency": "CNY"},
        error=None,
        output={"candidate_path": "candidate.png"},
    )
    version = AssetVersion(
        version_id="v1",
        slot_id=attempt.slot_id,
        source_attempt_id=attempt.attempt_id,
        adoption_status=AdoptionStatus.CANDIDATE,
        asset_path="candidate.png",
    )
    slot = AssetSlot(slot_id=attempt.slot_id, asset_kind="character_portrait")

    assert attempt.status == ExecutionStatus.PROVIDER_PROCESSING
    assert version.adoption_status == AdoptionStatus.CANDIDATE
    assert slot.current_version_id is None


def test_project_production_settings_cover_runtime_policy():
    settings = ProjectProductionSettings(
        aspect_ratio="9:16",
        strict_mode=True,
        production_recipe_version="recipe-v3",
        default_routes={"video": "runninghub:minimax-h3"},
        concurrency_limits={"video": 5},
        auto_provisional=True,
    )
    assert settings.concurrency_limits["video"] == 5
    assert settings.strict_mode is True


def test_generation_attempt_freezes_resolved_runtime_route():
    route = GenerationRouteSnapshot(
        provider="runninghub",
        model="minimax-h3",
        workflow_id="2089723723468328961",
        workflow_revision="director-v5",
        bindings={"timeline_data": {"node_id": "12", "field": "timeline_data"}},
        aspect_ratio="9:16",
        duration=10,
        mode="fl2va",
    )
    attempt = GenerationAttempt(
        attempt_id="attempt-h3",
        slot_id="narrative-group:ng-08:video",
        resolved_route=route,
    )

    assert attempt.resolved_route == route
    assert GenerationAttempt.model_validate_json(attempt.model_dump_json()) == attempt
