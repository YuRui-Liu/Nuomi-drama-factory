"""Business contract validators for explicitly compatible production workflows."""

from .models import WorkflowError
from .adapters import register_integration


def validate_builtin_graph(template, graph):
    expected = (
        {"12": "MiniMaxH3Director", "7": "SaveVideo"}
        if template == "h3-reference"
        else {
            "94": "TextEncodeAceStepAudio1.5",
            "203": "Int",
            "205": "Float",
            "109": "PrimitiveInt",
            "107": "SaveAudioMP3",
        }
    )
    for node, kind in expected.items():
        if graph.get(node, {}).get("class_type") != kind:
            raise WorkflowError("business_graph_contract", node)


def _h3(integration, manifest):
    if (
        integration.capability != "video.ref2va"
        or integration.scene != "narrative_group"
    ):
        raise WorkflowError("integration_scene")
    rule = manifest.inputs.get("timeline_data")
    targets = [
        (b.source, t.node_id, t.field, t.encoding)
        for b in manifest.bindings
        for t in b.targets
    ]
    if (
        rule is None
        or rule.type != "object"
        or targets != [("timeline_data", "12", "timeline_data", "json")]
    ):
        raise WorkflowError("h3_input_contract")
    if len(manifest.outputs) != 1 or "video" not in manifest.outputs:
        raise WorkflowError("h3_output_contract")
    output = manifest.outputs["video"]
    if (output.node_id, output.kind, output.min_count, output.max_count) != (
        "7",
        "video",
        1,
        1,
    ):
        raise WorkflowError("h3_output_contract")
    if manifest.adapter is not None:
        raise WorkflowError("h3_adapter_contract")


def _music(integration, manifest):
    if integration.capability != "music.generate" or integration.scene != "music_desk":
        raise WorkflowError("integration_scene")
    if not {"tags", "lyrics", "bpm", "duration", "seed"}.issubset(manifest.inputs):
        raise WorkflowError("music_input_contract")
    if len(manifest.outputs) != 1 or "audio" not in manifest.outputs:
        raise WorkflowError("music_output_contract")
    output = manifest.outputs["audio"]
    if output.kind != "audio" or output.min_count != 1 or output.max_count != 1:
        raise WorkflowError("music_output_contract")


def install_builtin_adapters():
    register_integration("h3-reference", 1, _h3)
    register_integration("acestep-music", 1, _music)
