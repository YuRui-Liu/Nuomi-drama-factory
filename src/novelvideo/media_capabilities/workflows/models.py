"""Versioned workflow package contracts; no provider credentials belong here."""
from __future__ import annotations

from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints, model_validator

Name = Annotated[str, StringConstraints(pattern=r"^[a-zA-Z][a-zA-Z0-9_.-]*$", max_length=128)]
Digest = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]


class WorkflowError(ValueError):
    def __init__(self, code: str, message: str = "", *, phase: str = "validate", recoverable: bool = False):
        self.code, self.phase, self.recoverable = code, phase, recoverable
        super().__init__(f"workflow.{code}" + (f": {message}" if message else ""))


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Source(Model):
    path: str = "workflow_api.json"
    sha256: Digest
    graph_sha256: Digest


class InputDefinition(Model):
    type: Literal["string", "integer", "number", "boolean", "object", "array", "file", "files"]
    required: bool = False
    nullable: bool = False
    default: JsonValue = None
    minimum: float | int | None = None
    maximum: float | int | None = None
    enum: list[JsonValue] | None = None
    min_length: int | None = Field(default=None, ge=0)
    max_length: int | None = Field(default=None, ge=0)
    pattern: str | None = None
    properties: dict[str, InputDefinition] = Field(default_factory=dict)
    additional_properties: bool = False
    items: InputDefinition | None = None
    max_bytes: int | None = Field(default=None, gt=0)
    extensions: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def bounds(self):
        import math
        import re
        for v in (self.minimum, self.maximum):
            if v is not None and not math.isfinite(v):
                raise ValueError("non-finite bound")
        for lo, hi in ((self.minimum, self.maximum), (self.min_length, self.max_length)):
            if lo is not None and hi is not None and lo > hi:
                raise ValueError("reversed bounds")
        if self.pattern is not None:
            re.compile(self.pattern)
        return self


class Target(Model):
    node_id: str = Field(min_length=1)
    field: str = Field(min_length=1)
    encoding: Literal["native", "string", "json"] = "native"


class Binding(Model):
    source: Name
    targets: list[Target] = Field(min_length=1)


class OutputDefinition(Model):
    node_id: str = Field(min_length=1)
    kind: Literal["image", "video", "audio", "document", "file"]
    min_count: int = Field(default=1, ge=0)
    max_count: int | None = Field(default=1, ge=1)

    @model_validator(mode="after")
    def bounds(self):
        if self.max_count is not None and self.min_count > self.max_count:
            raise ValueError("reversed output count")
        return self


class AdapterReference(Model):
    id: Name
    version: int = Field(default=1, ge=1)


class Integration(Model):
    capability: str = Field(min_length=1)
    scene: str = Field(min_length=1)
    adapter: AdapterReference
    parameters: dict[str, str] = Field(default_factory=dict)


class WorkflowManifest(Model):
    schema_version: Literal[1] = 1
    id: Name
    version: int = Field(ge=1)
    label: str = Field(min_length=1)
    provider: Literal["runninghub"] = "runninghub"
    workflow_id: str = Field(pattern=r"^[0-9]+$")
    source: Source
    inputs: dict[str, InputDefinition] = Field(default_factory=dict)
    bindings: list[Binding] = Field(default_factory=list)
    outputs: dict[str, OutputDefinition] = Field(min_length=1)
    adapter: AdapterReference | None = None
    integrations: list[Integration] = Field(default_factory=list)
    media_type: Literal["image", "video", "audio", "text"] | None = None
    status: Literal["draft", "active", "disabled"] = "active"

    @model_validator(mode="after")
    def unique_targets(self):
        targets = [(t.node_id, t.field) for b in self.bindings for t in b.targets]
        if len(targets) != len(set(targets)):
            raise ValueError("duplicate binding target")
        if self.adapter is None and any(b.source not in self.inputs for b in self.bindings):
            raise ValueError("binding source must be declared")
        nodes = [o.node_id for o in self.outputs.values()]
        if len(nodes) != len(set(nodes)):
            raise ValueError("output node must belong to one named output")
        return self

    def snapshot(self) -> dict:
        # Preserve missing defaults versus explicit null across persistence.
        return self.model_dump(mode="json", exclude_unset=True)


class FileReference(Model):
    kind: Literal["local_path", "artifact_id", "provider_file"]
    value: str = Field(min_length=1)


class RunResult(Model):
    run_id: str
    provider_task_id: str | None
    status: str
    artifacts: dict[str, list[dict]] = Field(default_factory=dict)
