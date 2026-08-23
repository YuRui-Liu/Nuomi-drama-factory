"""Public extension style catalog API."""

from .schema import (
    FRAGMENT_KEYS,
    ExtensionStyle,
    compile_prompt_fragment,
    load_catalog,
)

__all__ = (
    "ExtensionStyle",
    "compile_prompt_fragment",
    "load_catalog",
    "FRAGMENT_KEYS",
)
