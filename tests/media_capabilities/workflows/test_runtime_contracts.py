import hashlib
import json

import pytest

from novelvideo.media_capabilities.workflows.models import WorkflowError
from novelvideo.media_capabilities.workflows.graph import graph_digest
from novelvideo.media_capabilities.workflows.registry import WorkflowRegistry
from novelvideo.media_capabilities.workflows.files import resolve_files
from novelvideo.media_capabilities.store import MediaCapabilityStore, StoreConflictError
from novelvideo.media_capabilities.models import MediaCapability


def package(tmp_path):
    root = tmp_path / "package"
    root.mkdir()
    graph = {
        "1": {"class_type": "Text", "inputs": {"text": ""}},
        "2": {"class_type": "SaveAudio", "inputs": {"audio": ["1", 0]}},
    }
    raw = json.dumps(graph).encode()
    (root / "workflow_api.json").write_bytes(raw)
    data = {
        "schema_version": 1,
        "id": "custom",
        "version": 1,
        "label": "Custom",
        "workflow_id": "123",
        "source": {
            "path": "workflow_api.json",
            "sha256": hashlib.sha256(raw).hexdigest(),
            "graph_sha256": graph_digest(graph),
        },
        "inputs": {"prompt": {"type": "string", "required": True}},
        "bindings": [
            {"source": "prompt", "targets": [{"node_id": "1", "field": "text"}]}
        ],
        "outputs": {"audio": {"node_id": "2", "kind": "audio", "max_count": 2}},
    }
    (root / "manifest.json").write_text(json.dumps(data))
    return root, data, graph


def test_registry_is_immutable_and_does_not_change_routing(tmp_path):
    root, data, graph = package(tmp_path)
    store = MediaCapabilityStore(tmp_path / "settings.db")
    registry = WorkflowRegistry(store)
    registry.register(root)
    registry.register(root)
    assert len(registry.list()) == 1
    assert store.list_policies() == []
    assert registry.get("custom", 1)[1] == graph
    data["label"] = "changed"
    (root / "manifest.json").write_text(json.dumps(data))
    with pytest.raises(StoreConflictError):
        registry.register(root)
    assert MediaCapability("workflow.execute").value == "workflow.execute"


async def test_files_preserve_order_and_checkpoint(tmp_path):
    from novelvideo.media_capabilities.workflows.models import InputDefinition

    path = tmp_path / "ref.wav"
    path.write_bytes(b"audio")
    calls = []

    class Client:
        async def upload(self, path):
            calls.append(str(path))
            return "provider/ref.wav"

    rules = {"refs": InputDefinition(type="files", extensions=[".wav"])}
    values = {
        "refs": [
            {"kind": "local_path", "value": str(path)},
            {"kind": "provider_file", "value": "existing.wav"},
        ]
    }
    checkpoint = {}

    def save(data):
        checkpoint.update(data)

    first = await resolve_files(rules, values, Client(), "account", checkpoint, save)
    second = await resolve_files(rules, values, Client(), "account", checkpoint, save)
    assert first == second == {"refs": ["provider/ref.wav", "existing.wav"]}
    assert len(calls) == 1


async def test_invalid_file_does_not_upload(tmp_path):
    from novelvideo.media_capabilities.workflows.models import InputDefinition

    class Client:
        async def upload(self, path):
            pytest.fail("invalid file uploaded")

    with pytest.raises(WorkflowError):
        await resolve_files(
            {"ref": InputDefinition(type="file")},
            {"ref": {"kind": "local_path", "value": str(tmp_path / "missing")}},
            Client(),
            "a",
            {},
            lambda data: None,
        )


async def test_resume_rejects_changed_file(tmp_path):
    from novelvideo.media_capabilities.workflows.models import InputDefinition

    path = tmp_path / "ref.wav"
    path.write_bytes(b"first")
    checkpoint = {}

    class Client:
        async def upload(self, path):
            return "remote.wav"

    rules = {"ref": InputDefinition(type="file")}
    values = {"ref": {"kind": "local_path", "value": str(path)}}
    await resolve_files(rules, values, Client(), "a", checkpoint, checkpoint.update)
    path.write_bytes(b"changed")
    with pytest.raises(WorkflowError, match="file_changed"):
        await resolve_files(rules, values, Client(), "a", checkpoint, checkpoint.update)
