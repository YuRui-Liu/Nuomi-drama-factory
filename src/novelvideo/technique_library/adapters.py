"""Allowlisted metadata adapters for three pinned local repository snapshots."""
import json
import os
from pathlib import Path
import re
import subprocess

from .models import Case, safe_url

SOURCES = (
    ('beatapi', 'BeatAPI/awesome-minimax-h3-prompts', '03a0d24259341413597b3c6ff0ce0c0164007e8b'),
    ('skynotsilent', 'SkyNotSilent/awesome-MiniMax-H3-cases', '75a80139a9118a2739d2041981260dd2c3f4ac4b'),
    ('stimqq', 'stimQQ/stunning-minimax-h3-prompts', 'a964b870e38a99eac3085d2e94a7bf928f9441fb'),
)
LABELS = {'cinema': '叙事镜头与场景组织', 'performance': '人物表演与动作衔接',
          'action': '动态场面与运动节奏', 'product': '产品呈现与视觉重点',
          'music': '音乐节奏与声画配合', 'stylized': '风格化画面与造型一致性'}
KEYWORDS = {'cinema': ('cinema', 'story', 'horror', '电影', '叙事', '运镜', 'camera'),
            'performance': ('performance', 'character', 'dialogue', 'comedy', '人物', '表演', '对白'),
            'action': ('action', 'vfx', 'gameplay', '动作', '战斗', '运动'),
            'product': ('product', 'advert', 'brand', 'fashion', 'ads', '产品', '广告'),
            'music': ('music', 'sound', '音乐', '立体声', '音效'),
            'stylized': ('anime', 'animation', 'styl', 'graphic', '动画', '风格', '转场')}


def verify_snapshot(base: Path, revision: str) -> None:
    """Reject unpinned, modified, or incomplete source checkouts; never fetch."""
    if not (base / '.git').exists():
        raise ValueError('snapshot must be a pinned Git checkout')
    def git(*args):
        try:
            result = subprocess.run(['git', '-C', str(base), *args], capture_output=True,
                                    text=True, timeout=20, check=False,
                                    env={**os.environ, 'GIT_NO_LAZY_FETCH': '1'})
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ValueError('snapshot verification failed') from exc
        if result.returncode:
            raise ValueError('snapshot verification failed or source content modified')
        return result.stdout.strip()
    if git('rev-parse', 'HEAD') != revision:
        raise ValueError('snapshot revision does not match pinned revision')
    git('diff', '--quiet', 'HEAD', '--', 'prompts', 'data/cases.json')
    paths = git('ls-tree', '-r', '--name-only', revision, '--', 'prompts', 'data/cases.json').splitlines()
    if revision == SOURCES[0][2]:
        relevant = [path for path in paths if re.fullmatch(r'prompts/[^/]+\.json', path)]
        actual = {path.relative_to(base).as_posix() for path in (base / 'prompts').glob('*.json')}
    elif revision == SOURCES[1][2]:
        relevant = ['data/cases.json']
        actual = {'data/cases.json'} if (base / 'data/cases.json').is_file() else set()
    else:
        relevant = [path for path in paths if path.endswith('.md')]
        actual = {path.relative_to(base).as_posix() for path in (base / 'prompts').rglob('*.md')}
    if actual - set(relevant):
        raise ValueError('snapshot contains extra case files outside the pinned commit')
    if not relevant or any(not (base / path).is_file() for path in relevant):
        raise ValueError('snapshot is incomplete')


def _provenance(value: str) -> str:
    value = value.lower()
    if 'reconstruct' in value:
        return 'reconstructed'
    if value in ('not-published', 'unpublished'):
        return 'unpublished'
    if value in ('official', 'official-verbatim'):
        return 'official'
    if value in ('same-post', 'same-author-thread', 'creator-verbatim', 'author') or "author's own" in value:
        return 'author'
    return 'unknown'


def _row(repository, revision, path, upstream_id, title, url, author='', provenance='unknown',
         metadata='', mode=None, duration=None, media=None, prompt_provenance=None, source_verification=None):
    if not upstream_id or not isinstance(title, str) or not title.strip():
        raise ValueError('missing upstream ID or title')
    uses = [key for key, words in KEYWORDS.items() if any(word in metadata.lower() for word in words)]
    summary = ('可用于研究' + '、'.join(LABELS[key] for key in uses) + '。' if uses else
               '收录为镜头编排参考，可从主体呈现和场景连续性角度开展分析。')
    if mode:
        summary += f'来源标注生成方式为 {mode}。'
    if duration is not None:
        summary += f'来源标注时长为 {duration}。'
    claim = dict(repository=repository, revision=revision, path=path, upstream_id=str(upstream_id),
                 url=safe_url(url), author=str(author or ''), provenance=provenance,
                 prompt_provenance=prompt_provenance, source_verification=source_verification,
                 mode=str(mode) if mode else None, duration=str(duration) if duration is not None else None)
    result = dict(title=title.strip(), summary=summary, use_cases=uses, provenance=provenance,
                  sources=[claim], mode=claim['mode'], duration=claim['duration'], media_url=media)
    Case.model_validate(dict(result, id='h3-' + '0'*16))
    return result


def _json_row(item, name, repository, revision, path, anomalies):
    if name == 'beatapi':
        title = item.get('title')
        if isinstance(title, dict):
            title = title.get('zh') or title.get('en')
        source = item.get('source') or {}
        args = dict(upstream_id=item.get('slug'), title=title, url=source.get('url', ''),
                    author=source.get('name', ''), provenance=_provenance(item.get('promptVisibility', '')))
        media = item.get('video')
    else:
        args = dict(upstream_id=item.get('id'), title=item.get('title'), url=item.get('sourceUrl', ''),
                    author=item.get('author', ''), provenance=_provenance(item.get('promptProvenance', '')))
        media = item.get('mediaUrl')
    if media and media.startswith('/'):
        # Repository-relative web paths have no verified public origin.
        anomalies.append({'path': path, 'upstream_id': args['upstream_id'], 'reason': 'relative_media_omitted'})
        media = None
    metadata = json.dumps([item.get(key) for key in ('category', 'tags', 'styles', 'scenes', 'inputTypes')], ensure_ascii=False)
    return _row(repository, revision, path, **args, metadata=metadata,
                mode=item.get('mode'), duration=item.get('duration'), media=media,
                prompt_provenance=item.get('promptVisibility') if name == 'beatapi' else item.get('promptProvenance'),
                source_verification=item.get('outputStatus') if name == 'beatapi' else item.get('verified'))


def _markdown_row(text, repository, revision, path, category=''):
    # Read metadata preceding the prompt only; never retain prompt prose or excerpts.
    header = text.split('## Prompt', 1)[0]
    title = re.search(r'^#{1,3}\s+(?:\d+\.\s+)?(.+)$', header, re.M)
    identity = re.search(r'https://apimodels\.app/minimax-h3-prompts#prompt-([\w-]+)', header)
    source = re.search(r'\*\*(?:Source|Reference clip):\*\*\s*\[([^\]]+)\]\((https?://[^)]+)\)', header)
    if not title or not identity or not source:
        raise ValueError('missing Markdown case title, gallery ID, or source link')
    category_match = re.search(r'\*\*Category:\*\*\s*([^\n]+)', header)
    if category_match:
        category = category_match[1]
    # Gallery prompt excerpts are deliberately excluded from provenance and metadata.
    evidence = '\n'.join(line for line in header.splitlines() if
                         'Source:**' in line or 'Read the full prompt' in line or '(author\'s own)' in line)
    provenance = _provenance(evidence)
    duration = re.search(r'·\s*(\d+(?:\.\d+)?s)\b', header)
    return _row(repository, revision, path, identity[1], title[1], source[2], source[1],
                provenance, metadata=category, duration=duration[1] if duration else None,
                prompt_provenance=provenance)


def enumerate_sources(root: Path):
    rows, manifests = [], []
    report = dict(enumerated=0, parsed=0, errors=0, merged=0, final=0, sources=[], anomalies=[])
    for name, repository, revision in SOURCES:
        base = root / name
        manifests.append(dict(repository=repository, revision=revision))
        count = dict(repository=repository, enumerated=0, parsed=0, errors=0)
        report['sources'].append(count)

        def accept(path, operation):
            count['enumerated'] += 1
            try:
                rows.append(operation())
                count['parsed'] += 1
            except (ValueError, TypeError, KeyError, AttributeError) as exc:
                count['errors'] += 1
                report['anomalies'].append(dict(path=path, reason=str(exc)))

        try:
            verify_snapshot(base, revision)
            if name == 'beatapi':
                index_file = base / 'prompts/catalog.json'
                index = json.loads(index_file.read_text(encoding='utf-8'))
                expected = [item['slug'] for item in index['prompts']]
                actual = [file.stem for file in (base / 'prompts').glob('*.json') if file.name != 'catalog.json']
                if len(expected) != len(set(expected)) or set(expected) != set(actual):
                    raise ValueError('BeatAPI index and prompt files differ')
                for file in sorted((base / 'prompts').glob('*.json')):
                    relative = file.relative_to(base).as_posix()
                    if file.name == 'catalog.json':
                        index = json.loads(file.read_text(encoding='utf-8'))
                        if not isinstance(index.get('prompts'), list):
                            raise ValueError('invalid BeatAPI catalog index')
                        report['anomalies'].append(dict(path=relative, reason='duplicate_catalog_index_excluded'))
                        continue
                    accept(relative, lambda f=file, p=relative: _json_row(json.loads(f.read_text(encoding='utf-8')), name, repository, revision, p, report['anomalies']))
            elif name == 'skynotsilent':
                relative = 'data/cases.json'
                items = json.loads((base / relative).read_text(encoding='utf-8'))
                if not isinstance(items, list):
                    raise ValueError('cases.json must contain a list')
                for item in items:
                    accept(relative, lambda item=item: _json_row(item, name, repository, revision, relative, report['anomalies']))
            else:
                gallery = base / 'prompts/GALLERY.md'
                content = gallery.read_text(encoding='utf-8')
                category = ''
                for part in re.split(r'(?=^##(?: |# ))', content, flags=re.M):
                    if part.startswith('## '):
                        category = part.splitlines()[0][3:]
                    elif part.startswith('### '):
                        accept('prompts/GALLERY.md', lambda part=part, category=category: _markdown_row(part, repository, revision, 'prompts/GALLERY.md', category))
                for file in sorted((base / 'prompts').rglob('*.md')):
                    if file.name.startswith('GALLERY'):
                        continue
                    relative = file.relative_to(base).as_posix()
                    accept(relative, lambda f=file, p=relative: _markdown_row(f.read_text(encoding='utf-8'), repository, revision, p))
            if not count['enumerated']:
                raise ValueError('source contains no cases')
        except (OSError, ValueError, TypeError, KeyError) as exc:
            count['errors'] += 1
            report['anomalies'].append(dict(repository=repository, reason=str(exc)))
        for field in ('enumerated', 'parsed', 'errors'):
            report[field] += count[field]
    return rows, manifests, report
