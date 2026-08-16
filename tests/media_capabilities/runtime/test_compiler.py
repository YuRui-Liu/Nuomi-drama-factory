from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))

from novelvideo.media_capabilities.models import WorkflowProfile
from novelvideo.media_capabilities.runtime.compiler import (
    WorkflowCompileError,
    compile_node_info,
)


def profile_with_bindings(
    *,
    constraints: dict[str, object] | None = None,
    **bindings: tuple[str, str],
) -> WorkflowProfile:
    return WorkflowProfile(
        id="minimax-h3",
        version=1,
        workflow_id="runninghub-minimax-h3",
        bindings={
            name: {"node_id": node_id, "field": field}
            for name, (node_id, field) in bindings.items()
        },
        constraints=constraints or {},
    )


def assert_compile_error(
    profile: WorkflowProfile,
    values: dict[str, object],
    code: str,
    *,
    secret_value: object | None = None,
) -> None:
    with pytest.raises(WorkflowCompileError) as captured:
        compile_node_info(profile, values)  # type: ignore[arg-type]

    assert captured.value.code == code
    assert str(captured.value) == code
    if secret_value is not None:
        assert str(secret_value) not in str(captured.value)


def test_compile_only_emits_bound_allowlisted_fields() -> None:
    profile = profile_with_bindings(
        prompt=("133", "prompt"), seed=("131", "noise_seed")
    )

    result = compile_node_info(profile, {"prompt": "向前走", "seed": 7})

    assert result == [
        {"nodeId": "131", "fieldName": "noise_seed", "fieldValue": 7},
        {"nodeId": "133", "fieldName": "prompt", "fieldValue": "向前走"},
    ]


def test_rejects_unknown_semantic_input_without_leaking_value() -> None:
    profile = profile_with_bindings(prompt=("133", "prompt"))

    assert_compile_error(
        profile,
        {"prompt": "ok", "api_key": "super-secret"},
        "workflow.unknown_semantic_input",
        secret_value="super-secret",
    )


def test_rejects_missing_required_semantic_input() -> None:
    profile = profile_with_bindings(
        prompt=("133", "prompt"),
        constraints={"required": ["prompt"]},
    )

    assert_compile_error(profile, {}, "workflow.required_semantic_input")


@pytest.mark.parametrize(
    "required",
    [
        "prompt",
        [["prompt"]],
        [1],
        {"name": "prompt"},
    ],
)
def test_rejects_malformed_required_constraint(required: object) -> None:
    profile = profile_with_bindings(
        prompt=("133", "prompt"),
        constraints={"required": required},
    )

    assert_compile_error(
        profile,
        {},
        "workflow.invalid_constraint_descriptor",
        secret_value=required,
    )


def test_rejects_value_outside_enum_without_leaking_it() -> None:
    profile = profile_with_bindings(
        mode=("3", "mode"),
        constraints={"properties": {"mode": {"enum": ["fast", "quality"]}}},
    )

    assert_compile_error(
        profile,
        {"mode": "private-mode"},
        "workflow.enum_constraint",
        secret_value="private-mode",
    )


@pytest.mark.parametrize(
    ("value", "code"),
    [
        (0.5, "workflow.minimum_constraint"),
        (10.5, "workflow.maximum_constraint"),
    ],
)
def test_rejects_values_outside_numeric_bounds(value: float, code: str) -> None:
    profile = profile_with_bindings(
        duration=("135", "value"),
        constraints={
            "properties": {"duration": {"minimum": 1, "maximum": 10}}
        },
    )

    assert_compile_error(profile, {"duration": value}, code, secret_value=value)


def test_rejects_present_mutually_exclusive_inputs() -> None:
    profile = profile_with_bindings(
        first_frame=("114", "image"),
        last_frame=("141", "image"),
        constraints={"mutually_exclusive": [["first_frame", "last_frame"]]},
    )

    assert_compile_error(
        profile,
        {"first_frame": "first.png", "last_frame": "last.png"},
        "workflow.mutually_exclusive",
        secret_value="first.png",
    )


@pytest.mark.parametrize(
    "mutually_exclusive",
    [
        "first_frame",
        ["first_frame", "last_frame"],
        [["first_frame", ["last_frame"]]],
        [["first_frame", {"name": "last_frame"}]],
    ],
)
def test_rejects_malformed_mutually_exclusive_constraint(
    mutually_exclusive: object,
) -> None:
    profile = profile_with_bindings(
        first_frame=("114", "image"),
        last_frame=("141", "image"),
        constraints={"mutually_exclusive": mutually_exclusive},
    )

    assert_compile_error(
        profile,
        {"first_frame": "private-frame.png"},
        "workflow.invalid_constraint_descriptor",
        secret_value="private-frame.png",
    )


@pytest.mark.parametrize(
    "properties",
    [
        [],
        {"duration": []},
        {"duration": {"enum": "fast"}},
        {"duration": {"minimum": "low"}},
        {"duration": {"maximum": False}},
    ],
)
def test_rejects_malformed_properties_constraint(properties: object) -> None:
    profile = profile_with_bindings(duration=("135", "value"))
    profile.constraints = {"properties": properties}  # type: ignore[dict-item]

    assert_compile_error(
        profile,
        {"duration": 7},
        "workflow.invalid_constraint_descriptor",
        secret_value=properties,
    )


@pytest.mark.parametrize(
    "rules",
    [
        {"minimum": float("nan")},
        {"minimum": float("inf")},
        {"minimum": float("-inf")},
        {"maximum": float("nan")},
        {"maximum": float("inf")},
        {"maximum": float("-inf")},
        {"minimum": True},
        {"maximum": False},
        {"minimum": 10, "maximum": 1},
    ],
)
def test_rejects_invalid_numeric_constraint_descriptor(
    rules: dict[str, object],
) -> None:
    profile = profile_with_bindings(
        duration=("135", "value"),
        constraints={"properties": {"duration": rules}},
    )

    assert_compile_error(
        profile,
        {"duration": 7},
        "workflow.invalid_constraint_descriptor",
        secret_value=next(iter(rules.values())),
    )


@pytest.mark.parametrize(
    "descriptor",
    [
        "133.prompt",
        {"node_id": "133"},
        {"node_id": "133", "field": "prompt", "extra": True},
        {"node_id": " 133", "field": "prompt"},
        {"node_id": "133", "field": "prompt "},
    ],
)
def test_revalidates_binding_descriptors_at_compile_boundary(
    descriptor: object,
) -> None:
    profile = WorkflowProfile(
        id="direct-profile",
        version=1,
        workflow_id="workflow",
        bindings={"prompt": descriptor},  # type: ignore[dict-item]
    )

    assert_compile_error(
        profile,
        {"prompt": "do-not-leak"},
        "workflow.invalid_binding_descriptor",
        secret_value="do-not-leak",
    )


def test_sorts_by_natural_node_id_then_field_and_semantic_name() -> None:
    profile = profile_with_bindings(
        zeta=("10", "same"),
        beta=("2", "z-field"),
        gamma=("2", "a-field"),
        alpha=("10", "same"),
    )

    result = compile_node_info(
        profile,
        {"zeta": "z", "beta": "b", "gamma": "g", "alpha": "a"},
    )

    assert [item["fieldValue"] for item in result] == ["g", "b", "a", "z"]


def test_preserves_json_values_exactly() -> None:
    nested = {
        "enabled": True,
        "count": 0,
        "ratio": 1.25,
        "optional": None,
        "items": ["x", 2, False, {"nested": [None]}],
    }
    profile = profile_with_bindings(payload=("7", "payload"))

    result = compile_node_info(profile, {"payload": nested})

    assert result == [
        {"nodeId": "7", "fieldName": "payload", "fieldValue": nested}
    ]
    assert result[0]["fieldValue"] is nested


def test_ignores_unknown_constraints_for_forward_compatibility() -> None:
    profile = profile_with_bindings(
        prompt=("133", "prompt"),
        constraints={"future_constraint": {"enabled": True}},
    )

    assert compile_node_info(profile, {"prompt": "ok"}) == [
        {"nodeId": "133", "fieldName": "prompt", "fieldValue": "ok"}
    ]
