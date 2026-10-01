"""Read the small, offline curated publication once; never scan source cases.

This module deliberately does not import director models, avoiding a cycle.
"""
from functools import lru_cache
from importlib.resources import files
import json


@lru_cache(maxsize=1)
def load_technique_publication() -> dict:
    publication = json.loads(
        files("novelvideo.technique_library").joinpath("data/techniques.json").read_text(encoding="utf-8")
    )
    if publication.get("schema_version") != "1.0":
        raise ValueError("unsupported technique publication schema")
    return publication
