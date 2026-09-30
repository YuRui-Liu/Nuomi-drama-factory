"""Field-level inheritance with explicit replacement of lists and objects."""
from copy import deepcopy
from typing import Any

from .models import MethodConfig, validate_override_fields


def resolve_fields(base: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    """Return an independent validated method; omission alone means inheritance."""
    MethodConfig.model_validate(base)
    validate_override_fields(overrides)
    resolved = deepcopy(base)
    resolved.update(deepcopy(overrides))
    MethodConfig.model_validate(resolved)
    return resolved
