"""Versioned, packaged default methods. Custom skills may replace these entirely."""
import hashlib
import json
from importlib.resources import files


def _read(name: str) -> str:
    # Missing resources are installation errors and deliberately propagate.
    return files('novelvideo.script_creation').joinpath('craft_resources', name).read_text(encoding='utf-8')


def craft_guidance(kind: str) -> str:
    section = {'brief': 'planning', 'outline': 'planning', 'people': 'planning', 'scenes': 'planning',
               'props': 'planning', 'episode_synopsis': 'planning'}.get(kind, 'writing')
    parts = ['principles.md', section + '.md', 'review.md']
    if kind in {'episode_script', 'rewrite'}:
        parts.append('dialogue.md')
    return '\n'.join(_read(name) for name in parts)


def stage_guidance(stage: str, kind: str) -> str:
    if stage.startswith('dialogue'):
        return _read('dialogue.md')
    if 'review' in stage:
        return _read('review.md')
    return craft_guidance(kind)


def provenance() -> dict:
    result = json.loads(_read('sources.json'))
    result['resource_hashes'] = {
        name: hashlib.sha256(_read(name).encode()).hexdigest()
        for name in ['principles.md', 'planning.md', 'writing.md', 'review.md', 'dialogue.md']}
    return result
