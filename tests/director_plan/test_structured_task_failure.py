from novelvideo.task_backend.run_core import _project_task_failure_for_exception


class _StructuredTaskError(RuntimeError):
    error_code = "DIRECTOR_PLAN_VALIDATION_FAILED"
    validation_report = {
        "passed": False,
        "issues": [{"code": "missing_source", "location": "groups.0"}],
    }


def test_project_task_failure_preserves_safe_structured_error_metadata() -> None:
    error, payload, handled = _project_task_failure_for_exception(
        _StructuredTaskError("DIRECTOR_PLAN_VALIDATION_FAILED")
    )

    assert handled is True
    assert error == "DIRECTOR_PLAN_VALIDATION_FAILED"
    assert payload == {
        "error_code": "DIRECTOR_PLAN_VALIDATION_FAILED",
        "validation_report": _StructuredTaskError.validation_report,
    }
