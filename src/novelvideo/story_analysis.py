"""Shared deterministic source analysis primitives for ``structured_v1``.

The import and build phases intentionally use the same chunk contract.  Keeping
this small facade avoids a second, subtly different splitter while preserving
the public module introduced by the v2 workflow.
"""

from novelvideo.structured_ingest import (
    SourceChunk,
    chunk_source_text,
    source_sha256,
)

__all__ = ["SourceChunk", "chunk_source_text", "source_sha256"]
