from __future__ import annotations

import pytest
from pydantic import ValidationError

from novelvideo.media_capabilities.video.parameters import (
    VideoWorkflowParameterDefinition,
    VideoWorkflowParameterError,
    VideoWorkflowParameterOption,
    resolve_workflow_parameters,
)
from novelvideo.media_capabilities.video.workflow_registry import (
    VideoWorkflowDefinition,
    VideoWorkflowScene,
)


def _option(value: str = "720p", **updates) -> VideoWorkflowParameterOption:
    values = {"value": value, "label": value}
    values.update(updates)
    return VideoWorkflowParameterOption(**values)


def _parameter(**updates) -> VideoWorkflowParameterDefinition:
    values = {
        "key": "resolution",
        "label": "分辨率",
        "default": "720p",
        "options": (_option("720p"), _option("1080p")),
    }
    values.update(updates)
    return VideoWorkflowParameterDefinition(**values)


def _workflow(*parameters: VideoWorkflowParameterDefinition) -> VideoWorkflowDefinition:
    return VideoWorkflowDefinition(
        id="workflow",
        label="Workflow",
        provider="provider",
        adapter_key="adapter",
        scenes=frozenset({VideoWorkflowScene.NARRATIVE_GROUP}),
        supported_modes=("auto",),
        parameters=parameters,
    )


def test_parameter_models_normalize_declared_strings() -> None:
    option = VideoWorkflowParameterOption(value=" 720p ", label=" 标准 ")
    parameter = VideoWorkflowParameterDefinition(
        key=" resolution ",
        label=" 分辨率 ",
        default="720p",
        options=(option,),
    )

    assert option.value == "720p"
    assert option.label == "标准"
    assert parameter.key == "resolution"
    assert parameter.label == "分辨率"


@pytest.mark.parametrize(
    ("model", "values"),
    [
        (VideoWorkflowParameterOption, {"value": " ", "label": "标准"}),
        (VideoWorkflowParameterOption, {"value": "720p", "label": " "}),
        (
            VideoWorkflowParameterDefinition,
            {"key": " ", "label": "分辨率", "default": "720p", "options": (_option(),)},
        ),
        (
            VideoWorkflowParameterDefinition,
            {"key": "resolution", "label": " ", "default": "720p", "options": (_option(),)},
        ),
    ],
)
def test_parameter_models_reject_blank_declared_strings(model, values) -> None:
    with pytest.raises(ValidationError):
        model(**values)


def test_parameter_models_are_frozen_and_forbid_extra_fields() -> None:
    option = _option()
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        VideoWorkflowParameterOption(value="720p", label="标准", width=1280)
    with pytest.raises(ValidationError, match="Instance is frozen"):
        option.label = "changed"  # type: ignore[misc]


def test_parameter_definition_rejects_empty_or_duplicate_options() -> None:
    with pytest.raises(ValidationError, match="options must not be empty"):
        _parameter(options=())
    with pytest.raises(ValidationError, match="duplicate parameter option value: 720p"):
        _parameter(options=(_option("720p"), _option("720p", label="重复")))


def test_parameter_definition_rejects_default_outside_options() -> None:
    with pytest.raises(ValidationError, match="parameter default must be an option"):
        _parameter(default="4k")


def test_resolve_workflow_parameters_merges_defaults_then_overrides() -> None:
    workflow = _workflow(_parameter())

    assert resolve_workflow_parameters(workflow, {}) == {"resolution": "720p"}
    assert resolve_workflow_parameters(workflow, {"resolution": "1080p"}) == {
        "resolution": "1080p"
    }


def test_resolve_workflow_parameters_rejects_unknown_keys_and_values() -> None:
    workflow = _workflow(_parameter())

    with pytest.raises(VideoWorkflowParameterError, match="unknown workflow parameter: quality"):
        resolve_workflow_parameters(workflow, {"quality": "high"})
    with pytest.raises(
        VideoWorkflowParameterError,
        match="invalid value for workflow parameter resolution: 4k",
    ):
        resolve_workflow_parameters(workflow, {"resolution": "4k"})


def test_resolve_workflow_parameters_returns_new_dict_without_mutating_input() -> None:
    workflow = _workflow(_parameter())
    overrides = {"resolution": "1080p"}

    resolved = resolve_workflow_parameters(workflow, overrides)

    assert resolved is not overrides
    assert overrides == {"resolution": "1080p"}
