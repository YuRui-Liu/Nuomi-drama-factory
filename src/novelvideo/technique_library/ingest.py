"""Deterministic metadata ingestion and atomic publication."""
import hashlib
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import urlsplit, urlunsplit

from .models import Bundle, Case, safe_url


class IngestError(ValueError):
    def __init__(self, message: str, report: dict | None = None):
        super().__init__(message)
        self.report = report or {}


def normalize_url(value: str) -> str:
    safe_url(value)
    parsed = urlsplit(value)
    host = parsed.hostname.lower()
    if host in {'twitter.com', 'www.twitter.com', 'x.com', 'www.x.com', 'mobile.twitter.com'}:
        match = re.search(r'/status/(\d+)(/(?:video|photo)/\d+)?', parsed.path)
        if match:
            return 'https://x.com/i/status/' + match[1] + (match[2] or '')
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path, parsed.query, ''))


def _atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()


def _source_key(source) -> str:
    if not isinstance(source, dict):
        source = source.model_dump()
    return source['repository'] + ':' + source['upstream_id']


def merge_cases(rows: list[dict], previous: list[Case] | None = None) -> tuple[list[Case], int]:
    grouped: dict[str, Case] = {}
    for row in rows:
        # A post alone is insufficient: one post can contain several independent clips.
        source = row['sources'][0]
        identity = ('media:' + normalize_url(row['media_url']) if row.get('media_url') else
                    'source:' + source['repository'] + ':' + source['upstream_id'])
        case_id = 'h3-' + hashlib.sha256(_source_key(source).encode()).hexdigest()[:16]
        incoming = Case.model_validate({**row, 'id': case_id})
        existing = grouped.get(identity)
        if existing is None:
            grouped[identity] = incoming
            continue
        for claim in incoming.sources:
            if claim not in existing.sources:
                existing.sources.append(claim)
        existing.use_cases = sorted(set(existing.use_cases + incoming.use_cases))
        for field in ('provenance', 'mode', 'duration'):
            unknown = 'unknown' if field == 'provenance' else None
            claims = {getattr(claim, field) for claim in existing.sources} - {unknown}
            if len(claims) > 1:
                existing.conflicts = sorted(set(existing.conflicts + [field]))
                setattr(existing, field, unknown)
            elif claims:
                setattr(existing, field, next(iter(claims)))
    known = {_source_key(source): case for case in (previous or []) for source in case.sources}
    now = datetime.now(timezone.utc).isoformat(timespec='seconds')
    result = list(grouped.values())
    for case in result:
        inherited = {known[_source_key(source)].id for source in case.sources if _source_key(source) in known}
        if len(inherited) > 1:
            raise IngestError('Merge would combine established case identities; explicit migration required')
        if inherited:
            case.id = inherited.pop()
            old = next(known[_source_key(source)] for source in case.sources if _source_key(source) in known)
            case.imported_at = old.imported_at or now
        else:
            identity = min(_source_key(source) for source in case.sources)
            case.id = 'h3-' + hashlib.sha256(identity.encode()).hexdigest()[:16]
            case.imported_at = now
    if len({case.id for case in result}) != len(result):
        raise IngestError('Source changes split an established identity; explicit migration required')
    result.sort(key=lambda case: case.id)
    return result, len(rows) - len(result)


def import_catalog(source_root: str | Path, output: str | Path, report_path: str | Path | None = None) -> dict:
    from .adapters import enumerate_sources
    output = Path(output)
    if report_path is not None:
        report_path = Path(report_path)
        if (report_path.resolve() == output.resolve() or
                (report_path.exists() and output.exists() and report_path.samefile(output))):
            raise IngestError('Report and catalog must use different paths')
    rows, sources, report = enumerate_sources(Path(source_root))
    if report['errors'] or not rows or any(not item['parsed'] for item in report['sources']):
        if report_path:
            _atomic_json(Path(report_path), report)
        raise IngestError('Import refused: source is missing, empty, or malformed', report)
    previous = []
    if Path(output).exists():
        try:
            previous = Bundle.model_validate_json(Path(output).read_text(encoding='utf-8')).cases
        except (ValueError, OSError) as exc:
            raise IngestError('Existing output is invalid; refusing to overwrite identity history', report) from exc
    try:
        cases, merged = merge_cases(rows, previous)
    except IngestError as exc:
        exc.report = report
        raise
    by_post: dict[str, set[str]] = {}
    for case in cases:
        for source in case.sources:
            url = normalize_url(source.url)
            if 'x.com/i/status/' in url:
                url = re.sub(r'/(?:video|photo)/\d+$', '', url)
            by_post.setdefault(url, set()).add(case.id)
    report.update(merged=merged, final=len(cases),
                  duplicate_candidates=[{'url': url, 'case_ids': sorted(ids)} for url, ids in sorted(by_post.items()) if len(ids) > 1],
                  conflicts={'count': sum(bool(case.conflicts) for case in cases),
                             'case_ids': [case.id for case in cases if case.conflicts]})
    bundle = Bundle(sources=sources, report=report, cases=cases).model_dump(mode='json')
    # The catalog is the publication commit point. A separate report is diagnostic;
    # write it first so report failures cannot replace the last usable catalog.
    if report_path:
        _atomic_json(Path(report_path), report)
    _atomic_json(Path(output), bundle)
    return bundle
