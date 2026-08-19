from __future__ import annotations

import copy
import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from novelvideo.media_capabilities.models import MediaCapability
from novelvideo.media_capabilities.workflow_profiles import (
    WorkflowImportError,
    import_profile,
)


FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "runninghub"
    / "minimax_h3_api.json"
)
MAX_SOURCE_BYTES = 5 * 1024 * 1024
MAX_DEPTH = 64
MAX_NODES = 10_000
MAX_BINDINGS = 256
MAX_OUTPUTS = 256
MAX_CONTAINER_ITEMS = 100_000
BINDINGS = {"timeline_data": {"node_id": "12", "field": "timeline_data"}}
OUTPUTS = {"video": {"node_id": "7", "media_type": "video"}}
FIXTURE_NODE_IDS = {"12", "7"}
SENSITIVE_FIELD_NAMES = {
    "api_key",
    "access_token",
    "authorization",
    "credential",
    "credentials",
    "secret",
    "password",
    "passwd",
    "token",
    "client_secret",
    "refresh_token",
    "private_key",
    "bearer_token",
}
SECRET_KEY_VARIANTS = [
    ("api_key", "api_key"),
    ("api_key", "apiKey"),
    ("api_key", "api-key"),
    ("access_token", "access_token"),
    ("access_token", "accessToken"),
    ("access_token", "access-token"),
    ("authorization", "authorization"),
    ("authorization", "Authorization"),
    ("authorization", "AUTHORIZATION"),
    ("credential", "credential"),
    ("credential", "Credential"),
    ("credential", "CREDENTIAL"),
    ("credentials", "credentials"),
    ("credentials", "Credentials"),
    ("credentials", "CREDENTIALS"),
    ("secret", "secret"),
    ("secret", "Secret"),
    ("secret", "SECRET"),
    ("password", "password"),
    ("password", "Password"),
    ("password", "PASSWORD"),
    ("passwd", "passwd"),
    ("passwd", "Passwd"),
    ("passwd", "PASSWD"),
    ("token", "token"),
    ("token", "Token"),
    ("token", "TOKEN"),
    ("client_secret", "client_secret"),
    ("client_secret", "clientSecret"),
    ("client_secret", "client-secret"),
    ("refresh_token", "refresh_token"),
    ("refresh_token", "refreshToken"),
    ("refresh_token", "refresh-token"),
    ("private_key", "private_key"),
    ("private_key", "privateKey"),
    ("private_key", "private-key"),
    ("bearer_token", "bearer_token"),
    ("bearer_token", "bearerToken"),
    ("bearer_token", "bearer-token"),
]


def load_fixture() -> dict[str, object]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def import_minimax(source: object = FIXTURE, **overrides: object):
    arguments = {
        "workflow_id": "runninghub-minimax-h3",
        "source": source,
        "bindings": BINDINGS,
        "outputs": OUTPUTS,
    }
    if isinstance(source, Path) and "allowed_root" not in overrides:
        arguments["allowed_root"] = source.parent
    arguments.update(overrides)
    return import_profile(**arguments)


def test_runninghub_fixture_has_exact_sanitized_shape() -> None:
    fixture = load_fixture()

    assert set(fixture) == FIXTURE_NODE_IDS
    for node in fixture.values():
        assert isinstance(node, dict)
        assert set(node) == {"class_type", "inputs", "_meta"}
        assert set(node["_meta"]) == {"title"}


def test_runninghub_fixture_serialization_contains_no_sensitive_material() -> None:
    serialized = FIXTURE.read_text(encoding="utf-8").casefold()
    original_hashes = {
        "4c83d4d440c102678aeddd6dd2271b9c5353876fe22f52d54e9b017cffdd5a2a",
        "86bac6f74fee5cde5e31c44771382390a191093c907b4fe2387537d3f484eea2",
    }

    assert ".jpg" not in serialized
    assert ".png" not in serialized
    for original_hash in original_hashes:
        assert original_hash not in serialized
    for field_name in SENSITIVE_FIELD_NAMES:
        assert field_name not in serialized


def test_runninghub_fixture_uses_director_timeline_and_neutral_output_prefix() -> None:
    fixture = load_fixture()

    assert fixture["12"]["inputs"]["timeline_data"] == ""  # type: ignore[index]
    assert fixture["7"]["inputs"]["filename_prefix"] == "video/output"  # type: ignore[index]


@pytest.mark.parametrize("source_kind", ["path", "bytes", "text", "mapping"])
def test_imports_minimax_profile_from_supported_sources(source_kind: str) -> None:
    raw = FIXTURE.read_bytes()
    sources = {
        "path": FIXTURE,
        "bytes": raw,
        "text": raw.decode("utf-8"),
        "mapping": json.loads(raw),
    }

    profile = import_minimax(
        sources[source_kind],
        id="minimax-h3-fl2v",
        version=2,
        capabilities=[MediaCapability.VIDEO_FL2VA],
        constraints={"max_duration": 10},
    )

    assert profile.id == "minimax-h3-fl2v"
    assert profile.version == 2
    assert profile.workflow_id == "runninghub-minimax-h3"
    assert profile.bindings == BINDINGS
    assert profile.outputs == OUTPUTS
    assert profile.capabilities == [MediaCapability.VIDEO_FL2VA]
    assert profile.constraints == {"max_duration": 10}
    assert profile.status == "draft"


def test_path_source_requires_allowed_root() -> None:
    with pytest.raises(
        WorkflowImportError, match="^workflow_import.path_root_required$"
    ):
        import_minimax(allowed_root=None)


def test_path_source_must_resolve_within_allowed_root(tmp_path: Path) -> None:
    allowed_root = tmp_path / "allowed"
    allowed_root.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_bytes(FIXTURE.read_bytes())

    with pytest.raises(WorkflowImportError, match="^workflow_import.invalid_path$"):
        import_minimax(outside, allowed_root=allowed_root)


def test_path_source_rejects_non_file(tmp_path: Path) -> None:
    with pytest.raises(WorkflowImportError, match="^workflow_import.invalid_path$"):
        import_minimax(tmp_path, allowed_root=tmp_path)


def test_path_source_rejects_symlink(tmp_path: Path) -> None:
    target = tmp_path / "workflow.json"
    target.write_bytes(FIXTURE.read_bytes())
    link = tmp_path / "workflow-link.json"
    try:
        link.symlink_to(target)
    except OSError as exc:
        pytest.skip(f"symlink unavailable: {exc}")

    with pytest.raises(WorkflowImportError, match="^workflow_import.invalid_path$"):
        import_minimax(link, allowed_root=tmp_path)


def test_path_source_uses_bounded_read(tmp_path: Path) -> None:
    oversized = tmp_path / "oversized.json"
    oversized.write_bytes(b"x" * (MAX_SOURCE_BYTES + 1))

    with pytest.raises(WorkflowImportError, match="5 MiB"):
        import_minimax(oversized, allowed_root=tmp_path)


def test_defaults_profile_identity_version_and_draft_status() -> None:
    profile = import_minimax()

    assert profile.id == "runninghub-minimax-h3"
    assert profile.version == 1
    assert profile.status == "draft"


def test_hashes_canonical_json_not_original_key_order() -> None:
    source = load_fixture()
    reversed_source = dict(reversed(list(source.items())))
    canonical = json.dumps(
        source, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")

    first = import_minimax(source)
    second = import_minimax(reversed_source)

    assert first.source_sha256 == hashlib.sha256(canonical).hexdigest()
    assert second.source_sha256 == first.source_sha256


def test_does_not_mutate_mapping_source() -> None:
    source = load_fixture()
    original = copy.deepcopy(source)

    import_minimax(source)

    assert source == original


@pytest.mark.parametrize(
    ("overrides", "missing_node"),
    [
        (
            {"bindings": {**BINDINGS, "timeline_data": {"node_id": "999", "field": "timeline_data"}}},
            "999",
        ),
        (
            {"outputs": {"video": {"node_id": "999", "media_type": "video"}}},
            "999",
        ),
    ],
)
def test_rejects_missing_binding_or_output_node(
    overrides: dict[str, object], missing_node: str
) -> None:
    with pytest.raises(
        WorkflowImportError, match="^workflow_import.invalid_reference$"
    ) as exc_info:
        import_minimax(**overrides)

    assert missing_node not in str(exc_info.value)


def test_rejects_missing_binding_input_field() -> None:
    attacker_field = "attacker-field-" + ("x" * 1000)
    bindings = {
        **BINDINGS,
        "timeline_data": {"node_id": "12", "field": attacker_field},
    }

    with pytest.raises(
        WorkflowImportError, match="^workflow_import.invalid_reference$"
    ) as exc_info:
        import_minimax(bindings=bindings)

    assert attacker_field not in str(exc_info.value)


@pytest.mark.parametrize(
    ("argument", "descriptor"),
    [
        ("bindings", {"timeline_data": {"node_id": "12", "field": "timeline_data", "extra": 1}}),
        ("outputs", {"video": {"node_id": "7", "media_type": "video", "extra": 1}}),
        ("outputs", {"video": {"node_id": "7"}}),
    ],
)
def test_rejects_extra_or_missing_descriptor_fields(
    argument: str, descriptor: dict[str, object]
) -> None:
    with pytest.raises(WorkflowImportError):
        import_minimax(**{argument: descriptor})


@pytest.mark.parametrize(
    ("node_id", "replacement"),
    [
        ("12", {"class_type": 123, "inputs": {"timeline_data": "example"}}),
        ("12", {"class_type": "Example", "inputs": []}),
        ("7", {"class_type": 123, "inputs": {}}),
    ],
)
def test_rejects_malformed_referenced_nodes(
    node_id: str, replacement: dict[str, object]
) -> None:
    source = load_fixture()
    source[node_id] = replacement

    with pytest.raises(
        WorkflowImportError, match="^workflow_import.invalid_reference$"
    ) as exc_info:
        import_minimax(source)

    assert node_id not in str(exc_info.value)


@pytest.mark.parametrize(
    "source",
    [
        b"\xff",
        b"{not-json}",
        b"[]",
    ],
)
def test_rejects_invalid_utf8_json_and_non_object(source: object) -> None:
    with pytest.raises(WorkflowImportError):
        import_minimax(source)


def test_duplicate_key_error_does_not_echo_attacker_key() -> None:
    attacker_key = "secret-" + ("x" * 1000)
    source = f'{{"{attacker_key}": 1, "{attacker_key}": 2}}'

    with pytest.raises(
        WorkflowImportError, match="^workflow_import.duplicate_key$"
    ) as exc_info:
        import_minimax(source)

    assert attacker_key not in str(exc_info.value)


def test_rejects_raw_source_over_five_mib() -> None:
    source = b'{"padding":"' + (b"x" * MAX_SOURCE_BYTES) + b'"}'

    with pytest.raises(WorkflowImportError, match="5 MiB"):
        import_minimax(source)


def test_rejects_normalized_mapping_over_five_mib() -> None:
    source = {"padding": "x" * MAX_SOURCE_BYTES}

    with pytest.raises(WorkflowImportError, match="5 MiB"):
        import_minimax(source)


@pytest.mark.parametrize(("canonical_key", "secret_key"), SECRET_KEY_VARIANTS)
def test_rejects_secret_fields_without_echoing_values(
    canonical_key: str, secret_key: str
) -> None:
    source = load_fixture()
    secret_value = "do-not-leak-this-value"
    source["12"]["inputs"]["nested"] = {secret_key: secret_value}  # type: ignore[index]

    with pytest.raises(WorkflowImportError) as exc_info:
        import_minimax(source)

    assert canonical_key in SENSITIVE_FIELD_NAMES
    assert str(exc_info.value) == "workflow_import.prohibited_field"
    assert secret_key not in str(exc_info.value)
    assert secret_value not in str(exc_info.value)


@pytest.mark.parametrize(
    "secret_key",
    [
        "apiToken",
        "secretKey",
        "x-api-key",
        "apikey",
        "accesstoken",
        "clientsecret",
        "refreshtoken",
        "privatekey",
        "bearertoken",
        "secretkey",
        "apisecret",
        "authkey",
    ],
)
def test_rejects_semantic_secret_key_variants(secret_key: str) -> None:
    source = load_fixture()
    source["12"]["inputs"]["nested"] = {secret_key: "hidden"}  # type: ignore[index]

    with pytest.raises(
        WorkflowImportError, match="^workflow_import.prohibited_field$"
    ):
        import_minimax(source)


def test_does_not_reject_tokenizer_key() -> None:
    source = load_fixture()
    source["12"]["inputs"]["tokenizer"] = "example"  # type: ignore[index]

    profile = import_minimax(source)

    assert profile.status == "draft"


@pytest.mark.parametrize("location", ["constraints", "bindings", "outputs"])
def test_scans_profile_metadata_for_secret_fields(location: str) -> None:
    overrides: dict[str, object]
    if location == "constraints":
        overrides = {"constraints": {"secretKey": "hidden"}}
    elif location == "bindings":
        overrides = {
            "bindings": {**BINDINGS, "apiToken": BINDINGS["timeline_data"]}
        }
    else:
        overrides = {"outputs": {**OUTPUTS, "x-api-key": OUTPUTS["video"]}}

    with pytest.raises(
        WorkflowImportError, match="^workflow_import.prohibited_field$"
    ):
        import_minimax(**overrides)


def test_rejects_structure_deeper_than_limit() -> None:
    constraints: dict[str, object] = {}
    current = constraints
    for _ in range(MAX_DEPTH + 1):
        child: dict[str, object] = {}
        current["value"] = child
        current = child

    with pytest.raises(
        WorkflowImportError, match="^workflow_import.structure_limit$"
    ):
        import_minimax(constraints=constraints)


def test_rejects_cyclic_mapping_as_workflow_import_error() -> None:
    source = load_fixture()
    cycle: dict[str, object] = {}
    cycle["self"] = cycle
    source["12"]["inputs"]["cycle"] = cycle  # type: ignore[index]

    with pytest.raises(
        WorkflowImportError, match="^workflow_import.invalid_structure$"
    ):
        import_minimax(source)


def test_rejects_too_many_workflow_nodes() -> None:
    source = {
        str(index): {"class_type": "Example", "inputs": {}}
        for index in range(MAX_NODES + 1)
    }

    with pytest.raises(
        WorkflowImportError, match="^workflow_import.structure_limit$"
    ):
        import_profile("workflow", source, {}, {})


def test_rejects_too_many_bindings() -> None:
    bindings = {
        f"binding_{index}": {"node_id": "12", "field": "timeline_data"}
        for index in range(MAX_BINDINGS + 1)
    }

    with pytest.raises(
        WorkflowImportError, match="^workflow_import.structure_limit$"
    ):
        import_minimax(bindings=bindings)


def test_rejects_too_many_outputs() -> None:
    outputs = {
        f"output_{index}": {"node_id": "7", "media_type": "video"}
        for index in range(MAX_OUTPUTS + 1)
    }

    with pytest.raises(
        WorkflowImportError, match="^workflow_import.structure_limit$"
    ):
        import_minimax(outputs=outputs)


def test_rejects_too_many_container_items() -> None:
    constraints = {"items": [None] * (MAX_CONTAINER_ITEMS + 1)}

    with pytest.raises(
        WorkflowImportError, match="^workflow_import.structure_limit$"
    ):
        import_minimax(constraints=constraints)


@pytest.mark.parametrize(
    "overrides",
    [
        {"workflow_id": ""},
        {"workflow_id": " workflow"},
        {"id": ""},
        {"id": "profile "},
        {"bindings": {"timeline_data": {"node_id": " 12", "field": "timeline_data"}}},
        {"bindings": {"timeline_data": {"node_id": "12", "field": "timeline_data "}}},
        {"outputs": {"video": {"node_id": "7 ", "media_type": "video"}}},
        {"outputs": {"video": {"node_id": "7", "media_type": " video"}}},
    ],
)
def test_rejects_empty_or_edge_whitespace_identifiers(
    overrides: dict[str, object]
) -> None:
    with pytest.raises(WorkflowImportError):
        import_minimax(**overrides)
