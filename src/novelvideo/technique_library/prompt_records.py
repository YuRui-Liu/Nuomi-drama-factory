"""Preserve source text verbatim; never manufacture bodies from summaries."""
import hashlib
import re

from .models import CasePrompt, Source


def prompt_record(text: str | None, source: Source | dict, *, language: str = 'und') -> CasePrompt | None:
    claim = source if isinstance(source, Source) else Source.model_validate(source)
    if text is None or claim.provenance == 'unpublished':
        return None
    if not isinstance(text, str):
        raise ValueError('Prompt body must be a string')
    stripped = text.strip()
    if (not stripped or re.fullmatch(r'https?://\S+', stripped)
            or stripped.casefold() in {'not published', 'not-published', 'unpublished', '未公开', '暂无', 'n/a'}):
        return None
    return CasePrompt(text=text, language=language, provenance=claim.provenance,
                      repository=claim.repository, revision=claim.revision, path=claim.path,
                      upstream_id=claim.upstream_id, source_url=claim.url,
                      content_sha256=hashlib.sha256(text.encode('utf-8')).hexdigest())


def prompt_identity(record: CasePrompt) -> tuple[str, ...]:
    return (record.repository, record.revision, record.path, record.upstream_id, record.content_sha256)
