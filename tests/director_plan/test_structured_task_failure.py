from novelvideo.task_backend.run_core import _project_task_failure_for_exception
from novelvideo.task_backend.runners.director_plan import DirectorPlanTaskError


def test_real_director_plan_error_preserves_lowercase_snake_case_payload() -> None:
    validation_report = {
        "passed": False,
        "issues": [{"code": "missing_span", "location": "groups.0"}],
    }
    failure = DirectorPlanTaskError(
        "director_plan_validation_failed",
        validation_report=validation_report,
    )

    error, payload, handled = _project_task_failure_for_exception(failure)

    assert handled is True
    assert error == "director_plan_validation_failed"
    assert payload == {
        "error_code": "director_plan_validation_failed",
        "validation_report": validation_report,
    }


def test_structured_error_code_rejects_unsafe_characters() -> None:
    failure = DirectorPlanTaskError("director-plan/../../secret")

    error, payload, handled = _project_task_failure_for_exception(failure)

    assert handled is False
    assert payload == {}
    assert "director-plan/../../secret" in error
