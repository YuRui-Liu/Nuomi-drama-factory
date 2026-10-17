import json
import pytest
from novelvideo.media_capabilities.workflows.templates import configure_template
from novelvideo.media_capabilities.workflows.importer import (
    import_workflow,
    load_package,
)
from novelvideo.media_capabilities.workflows.compiler import compile_inputs
from novelvideo.media_capabilities.workflows.models import WorkflowError
from novelvideo.media_capabilities.workflows.registry import WorkflowRegistry
from novelvideo.media_capabilities.store import MediaCapabilityStore


def test_acestep_configuration_preserves_links(tmp_path):
    graph = {
        "94": {
            "class_type": "TextEncodeAceStepAudio1.5",
            "inputs": {
                "tags": "",
                "lyrics": "",
                "bpm": ["203", 0],
                "duration": ["205", 0],
            },
        },
        "203": {"class_type": "Int", "inputs": {"value": 75}},
        "205": {"class_type": "Float", "inputs": {"value": 15}},
        "109": {"class_type": "PrimitiveInt", "inputs": {"value": 1}},
        "107": {"class_type": "SaveAudioMP3", "inputs": {}},
    }
    source = tmp_path / "api.json"
    source.write_text(json.dumps(graph))
    root = tmp_path / "package"
    import_workflow(source, identifier="music", workflow_id="123", out_dir=root)
    configure_template(root, "acestep")
    manifest, loaded = load_package(root)
    nodes = compile_inputs(
        manifest, loaded, {"tags": "piano", "seed": "18446744073709551615"}
    )
    assert any(n["nodeId"] == "205" and n["fieldName"] == "value" for n in nodes)
    assert not any(n["fieldName"] in {"bpm", "duration"} for n in nodes)
    assert loaded == graph
    with pytest.raises(WorkflowError):
        compile_inputs(
            manifest, loaded, {"tags": "piano", "seed": "18446744073709551616"}
        )


def test_h3_template_registers_for_narrative_groups(tmp_path):
    graph = {
        "12": {"class_type": "MiniMaxH3Director", "inputs": {"timeline_data": "{}"}},
        "7": {"class_type": "SaveVideo", "inputs": {"video": ["12", 0]}},
    }
    source = tmp_path / "api.json"
    source.write_text(json.dumps(graph))
    root = tmp_path / "package"
    import_workflow(source, identifier="custom-ref", workflow_id="123", out_dir=root)
    configure_template(root, "h3-reference")
    registry = WorkflowRegistry(MediaCapabilityStore(tmp_path / "settings.db"))
    registry.register(root)
    assert len(registry.for_scene("video.ref2va", "narrative_group")) == 1
    from novelvideo.media_capabilities.video.workflow_registry import (
        build_video_workflow_registry,
    )
    from novelvideo.media_capabilities.runtime.credentials import CredentialResolver

    definitions = build_video_workflow_registry(
        registry.store, CredentialResolver()
    ).list()
    custom = next(d for d in definitions if d.id == "runninghub:workflow:custom-ref:v1")
    assert custom.provider_workflow_id == "123"
    assert custom.workflow_profile["manifest"]["version"] == 1
    assert not custom.is_default
    manifest, loaded = load_package(root)
    value = compile_inputs(manifest, loaded, {"timeline_data": {"shots": []}})[0]
    assert json.loads(value["fieldValue"]) == {"shots": []}
