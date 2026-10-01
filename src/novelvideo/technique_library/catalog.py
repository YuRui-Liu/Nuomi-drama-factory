"""Validated local queries. Never fetch upstream repositories at runtime."""
import json
from functools import lru_cache
from pathlib import Path

from .models import Bundle

CATALOG_PATH = Path(__file__).parent / 'data' / 'catalog.json'


class CatalogUnavailable(RuntimeError):
    pass


@lru_cache(maxsize=4)
def _load(path: Path, modified_ns: int, size: int, inode: int) -> Bundle:
    bundle = Bundle.model_validate_json(path.read_text(encoding='utf-8'))
    ids = [case.id for case in bundle.cases]
    if len(ids) != len(set(ids)):
        raise ValueError('duplicate case IDs')
    return bundle


def _cases() -> list[dict]:
    try:
        stat = CATALOG_PATH.stat()
        bundle = _load(CATALOG_PATH, stat.st_mtime_ns, stat.st_size, stat.st_ino)
        return [case.model_dump(mode='json') for case in bundle.cases]
    except (OSError, ValueError) as exc:
        raise CatalogUnavailable('Case catalog unavailable or invalid') from exc


def query_cases(q: str = '', use_case: str = '', provenance: str = '', offset: int = 0, limit: int = 24) -> dict:
    if offset < 0 or not 1 <= limit <= 100:
        raise ValueError('offset must be nonnegative and limit between 1 and 100')
    needle = q.strip().casefold()
    items = [case for case in _cases()
             if (not needle or needle in json.dumps(case, ensure_ascii=False).casefold())
             and (not use_case or use_case in case['use_cases'])
             and (not provenance or provenance == case['provenance'])]
    return {'items': items[offset:offset + limit], 'total': len(items), 'offset': offset, 'limit': limit}


def get_case(case_id: str) -> dict | None:
    return next((case for case in _cases() if case['id'] == case_id), None)
