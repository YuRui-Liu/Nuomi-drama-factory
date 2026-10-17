import copy
import json

import pytest
from pydantic import ValidationError

from novelvideo.media_capabilities.workflows.models import WorkflowManifest, WorkflowError
from novelvideo.media_capabilities.workflows.graph import graph_digest, validate_graph, validate_deployed_graph
from novelvideo.media_capabilities.workflows.compiler import compile_inputs
from novelvideo.media_capabilities.workflows.importer import import_workflow, load_package


@pytest.fixture
def graph():
    return {"1": {"class_type": "Text", "inputs": {"text": "example"}},
            "2": {"class_type": "SaveAudio", "inputs": {"audio": ["1", 0]}}}


@pytest.fixture
def manifest(graph):
    return WorkflowManifest.model_validate({
        "schema_version": 1, "id": "custom-audio", "version": 1,
        "label": "Custom audio", "provider": "runninghub", "workflow_id": "123",
        "source": {"path": "workflow_api.json", "sha256": "0" * 64, "graph_sha256": graph_digest(graph)},
        "inputs": {"prompt": {"type": "string", "required": True}},
        "bindings": [{"source": "prompt", "targets": [{"node_id": "1", "field": "text", "encoding": "native"}]}],
        "outputs": {"audio": {"node_id": "2", "kind": "audio", "min_count": 1, "max_count": 1}},
    })


def test_compile_and_unknown_inputs(manifest, graph):
    assert compile_inputs(manifest, graph, {"prompt": "hello"}) == [
        {"nodeId": "1", "fieldName": "text", "fieldValue": "hello"}]
    with pytest.raises(WorkflowError, match="unknown_input"):
        compile_inputs(manifest, graph, {"prompt": "hello", "oops": 1})
    with pytest.raises(WorkflowError, match="required"):
        compile_inputs(manifest, graph, {})


@pytest.mark.parametrize("value", [None, 3, True, [], {}])
def test_strict_type(manifest, graph, value):
    with pytest.raises(WorkflowError):
        compile_inputs(manifest, graph, {"prompt": value})


def test_duplicate_target_rejected(manifest):
    data = manifest.model_dump(exclude_unset=True)
    data["bindings"].append(copy.deepcopy(data["bindings"][0]))
    with pytest.raises(ValidationError):
        WorkflowManifest.model_validate(data)


def test_link_cannot_be_overwritten(manifest, graph):
    manifest.bindings[0].targets[0].node_id = "2"
    manifest.bindings[0].targets[0].field = "audio"
    with pytest.raises(WorkflowError, match="linked_field"):
        compile_inputs(manifest, graph, {"prompt": "hello"})


def test_structure_change_and_metadata(manifest, graph):
    remote = copy.deepcopy(graph)
    remote["1"]["_meta"] = {"title": "different"}
    validate_deployed_graph(manifest, graph, remote)
    remote["1"]["class_type"] = "Different"
    with pytest.raises(WorkflowError, match="graph_drift"):
        validate_deployed_graph(manifest, graph, remote)


def test_defaults_encoding_and_numeric_bounds(manifest, graph):
    data = manifest.model_dump(exclude_unset=True)
    data["inputs"] = {"prompt": {"type": "integer", "default": 5, "minimum": 1, "maximum": 10}}
    data["bindings"][0]["targets"][0]["encoding"] = "string"
    m = WorkflowManifest.model_validate(data)
    assert compile_inputs(m, graph, {})[0]["fieldValue"] == "5"
    for value in [True, 1.5, 0, 11, float("nan")]:
        with pytest.raises(WorkflowError):
            compile_inputs(m, graph, {"prompt": value})


def test_json_encoding(manifest, graph):
    data = manifest.model_dump(exclude_unset=True)
    data["inputs"] = {"prompt": {"type": "object", "required": True}}
    data["bindings"][0]["targets"][0]["encoding"] = "json"
    m = WorkflowManifest.model_validate(data)
    assert json.loads(compile_inputs(m, graph, {"prompt": {"a": [1]}})[0]["fieldValue"]) == {"a": [1]}


def test_import_is_draft_and_preserves_source(tmp_path, graph):
    source = tmp_path / "source.json"
    source.write_text(json.dumps(graph), encoding="utf-8")
    out = tmp_path / "package"
    report = import_workflow(source, identifier="custom", workflow_id="123", out_dir=out)
    assert report["status"] == "draft"
    assert (out / "workflow_api.json").read_bytes() == source.read_bytes()
    with pytest.raises(WorkflowError, match="draft"):
        load_package(out)
    assert json.loads((out / "manifest.json").read_text())["inputs"] == {}
    with pytest.raises(WorkflowError, match="exists"):
        import_workflow(source, identifier="custom", workflow_id="123", out_dir=out)


@pytest.mark.parametrize("graph", [{}, {"nodes": [], "links": []}, {"1": {"inputs": {}}}])
def test_invalid_graph(graph):
    with pytest.raises(WorkflowError):
        validate_graph(graph)
