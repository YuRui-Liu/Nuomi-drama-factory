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
    "model",
    [VideoWorkflowParameterOption, VideoWorkflowParameterDefinition],
)
def test_parameter_models_enable_strict_validation(model) -> None:
    assert model.model_config["strict"] is True


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
        (
            VideoWorkflowParameterDefinition,
            {"key": "resolution", "label": "分辨率", "default": " ", "options": (_option(),)},
        ),
    ],
)
def test_parameter_models_reject_blank_declared_strings(model, values) -> None:
    with pytest.raises(ValidationError):
        model(**values)


def test_parameter_option_is_frozen_and_forbids_extra_fields() -> None:
    option = _option()
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        VideoWorkflowParameterOption(value="720p", label="标准", width=1280)
    with pytest.raises(ValidationError, match="Instance is frozen"):
        option.label = "changed"  # type: ignore[misc]


def test_parameter_definition_is_frozen() -> None:
    parameter = _parameter()

    with pytest.raises(ValidationError, match="Instance is frozen"):
        parameter.label = "changed"  # type: ignore[misc]


def test_parameter_definition_forbids_extra_fields() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        VideoWorkflowParameterDefinition(
            key="resolution",
            label="分辨率",
            default="720p",
            options=(_option(),),
            node_id="internal",
        )


def test_parameter_models_reject_invalid_literal_values() -> None:
    with pytest.raises(ValidationError, match="enum"):
        _parameter(type="range")
    with pytest.raises(ValidationError, match="narrative_group"):
        _parameter(scope="project")
    with pytest.raises(ValidationError, match="standard|higher"):
        _option(relative_cost="lower")


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


def test_resolve_workflow_parameters_rejects_unknown_key_without_mutating_input() -> None:
    workflow = _workflow(_parameter())
    overrides = {"quality": "high"}

    with pytest.raises(VideoWorkflowParameterError, match="unknown workflow parameter: quality"):
        resolve_workflow_parameters(workflow, overrides)

    assert overrides == {"quality": "high"}


def test_resolve_workflow_parameters_rejects_unknown_value_without_mutating_input() -> None:
    workflow = _workflow(_parameter())
    overrides = {"resolution": "4k"}

    with pytest.raises(
        VideoWorkflowParameterError,
        match="invalid value for workflow parameter resolution: 4k",
    ):
        resolve_workflow_parameters(workflow, overrides)

    assert overrides == {"resolution": "4k"}


def test_resolve_workflow_parameters_returns_new_dict_without_mutating_input() -> None:
    workflow = _workflow(_parameter())
    overrides = {"resolution": "1080p"}

    resolved = resolve_workflow_parameters(workflow, overrides)

    assert resolved is not overrides
    assert overrides == {"resolution": "1080p"}


def test_resolve_workflow_parameters_rejects_non_mapping_without_mutating_input() -> None:
    workflow = _workflow(_parameter())
    overrides = [("resolution", "1080p")]

    with pytest.raises(VideoWorkflowParameterError, match="must be a mapping"):
        resolve_workflow_parameters(workflow, overrides)  # type: ignore[arg-type]

    assert overrides == [("resolution", "1080p")]


def test_resolve_workflow_parameters_rejects_non_string_key_without_mutating_input() -> None:
    workflow = _workflow(_parameter())
    overrides = {1: "1080p"}

    with pytest.raises(VideoWorkflowParameterError, match="keys must be strings"):
        resolve_workflow_parameters(workflow, overrides)  # type: ignore[arg-type]

    assert overrides == {1: "1080p"}


def test_resolve_workflow_parameters_rejects_list_value_without_mutating_input() -> None:
    workflow = _workflow(_parameter())
    overrides = {"resolution": ["1080p"]}

    with pytest.raises(VideoWorkflowParameterError, match="values must be strings"):
        resolve_workflow_parameters(workflow, overrides)  # type: ignore[arg-type]

    assert overrides == {"resolution": ["1080p"]}
