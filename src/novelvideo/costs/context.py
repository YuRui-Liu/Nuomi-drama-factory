"""Scoped, explicitly attributed cost metadata. Never carries provider responses."""
from contextlib import contextmanager
from contextvars import ContextVar
from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator
from .models import Identity, MediaType, CostAttempt

SAFE_USAGE = frozenset({'item', 'second', 'call', 'character', 'input_tokens', 'output_tokens', 'credit'})
SAFE_SPECIFICATIONS = frozenset({'width', 'height', 'resolution', 'size', 'duration', 'quality', 'aspect_ratio', 'fps', 'format', 'voice_id'})


def validate_safe_metadata(usage, specifications=()):
    if set(usage) - SAFE_USAGE:
        raise ValueError('unsupported usage fields')
    if any(key not in SAFE_SPECIFICATIONS or len(value) > 128 for key, value in specifications):
        raise ValueError('unsupported specification fields')


class CostContext(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    project_id: Identity
    task_id: Identity | None = None
    resource_id: Identity | None = None
    media_type: MediaType
    attempt_id: Identity | None = None
    usage: dict[Identity, StrictStr] = Field(default_factory=dict)
    specifications: tuple[tuple[Identity, StrictStr], ...] = ()

    @field_validator('usage')
    @classmethod
    def valid_usage(cls, value):
        validate_safe_metadata(value)
        return CostAttempt.validate_usage(value)

    @field_validator('specifications')
    @classmethod
    def safe_specifications(cls, value):
        validate_safe_metadata({}, value)
        return value


_CONTEXT = ContextVar('cost_context', default=None)


@contextmanager
def cost_context(context: CostContext):
    token = _CONTEXT.set(CostContext.model_validate(context))
    try:
        yield _CONTEXT.get()
    finally:
        _CONTEXT.reset(token)


def resolve_cost_context(media_type: MediaType | None = None) -> CostContext | None:
    context = _CONTEXT.get()
    if context is not None:
        return context if media_type is None else CostContext.model_validate({**context.model_dump(), 'media_type': media_type})
    from novelvideo.llm_instrumentation import get_project_context
    project = get_project_context()
    if not project:
        return None
    from novelvideo.task_state import get_current_project_task_id
    return CostContext(project_id=project, task_id=get_current_project_task_id() or None,
                       media_type=media_type or 'text')
