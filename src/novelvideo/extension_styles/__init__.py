"""Public extension style catalog API."""

from .schema import (
    FRAGMENT_KEYS,
    ExtensionStyle,
    compile_prompt_fragment,
    load_catalog,
)
from .registry import (
    CatalogDiagnostics,
    CatalogError,
    CatalogFingerprint,
    CatalogSnapshot,
    ExtensionStyleRegistry,
)

__all__ = (
    "ExtensionStyle",
    "ExtensionStyleRegistry",
    "CatalogDiagnostics",
    "CatalogError",
    "CatalogFingerprint",
    "CatalogSnapshot",
    "compile_prompt_fragment",
    "load_catalog",
    "FRAGMENT_KEYS",
)
