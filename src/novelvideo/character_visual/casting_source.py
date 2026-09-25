"""Single-character extraction over exact, bounded source windows."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from novelvideo.story_analysis import source_sha256
from .casting_brief import evidence_supports
from .casting_compiler import snapshot_digest
from .models import CharacterNarrativeFact, CharacterNarrativeProfile, SourceSpan


@dataclass(frozen=True)
class SourceDocument:
    document_id: str
    text: str
    content_hash: str
    revision: str = ''
    filename: str = ''


class ExtractedFact(BaseModel):
    model_config = ConfigDict(extra='forbid')
    field: Literal['age_range', 'age_group', 'gender', 'body_type', 'hair_style',
        'face_shape', 'facial_feature', 'distinctive_feature', 'scar', 'disability',
        'uniform', 'clothing_state', 'injury_state', 'beauty', 'appearance', 'species',
        'face', 'build', 'occupation', 'social_identity', 'relationship', 'personality',
        'work_habits', 'environment', 'behavior', 'biography', 'dramatic_function']
    value: str = Field(min_length=1, description='必须逐字截取 evidence 中直接描述该角色的连续词语，不概括、不改写、不推断。')
    evidence: str
    source_document: str
    source_start: int = Field(ge=0)
    source_end: int = Field(ge=0)
    identity_id: str | None = None


class FactExtraction(BaseModel):
    facts: list[ExtractedFact] = Field(default_factory=list, max_length=50)


class SourceFactVerificationError(ValueError):
    def __init__(self, reason, fact):
        self.reason = reason
        field = fact.field if re.fullmatch(r'[a-z_]{1,40}', fact.field) else 'unrecognized'
        super().__init__('source quote, offset, value or character attribution could not be verified; '
                         f'reason={reason}; field={field}; offsets={fact.source_start}:{fact.source_end}')


# These are optional narrative interpretations, never visual hard constraints.
_NARRATIVE_CONTEXT_FIELDS = frozenset({'biography', 'behavior', 'personality',
    'dramatic_function', 'work_habits', 'environment', 'relationship', 'occupation', 'social_identity'})

# Failures about where a quote lives, not about what it claims: a verbatim
# re-location in the project sources settles them without weakening the
# value and attribution gates.
_POSITION_REASONS = frozenset({'document', 'offset_range', 'quote_mismatch'})


async def load_sources(project_dir, sqlite_store):
    from novelvideo.episode_source_store import EpisodeSourceStore
    from novelvideo.novel_source import require_imported_novel

    episodes = await EpisodeSourceStore(sqlite_store).list_sources()  # recover pending imports first
    documents = {}
    if (Path(project_dir) / 'novel.txt').is_file():
        text = require_imported_novel(project_dir)
        documents['novel.txt'] = SourceDocument('novel.txt', text, source_sha256(text), filename='novel.txt')
    for source in episodes:
        document_id = f'episode:{source.episode_number:04d}'
        digest = source_sha256(source.content)
        # EpisodeSourceStore writes algorithm-prefixed digests; older imports
        # stored the same SHA256 bytes as bare hex. Verify either exact format.
        if source.content_hash not in (digest, f'sha256:{digest}'):
            raise ValueError('原文内容校验失败')
        documents[document_id] = SourceDocument(document_id, source.content, digest,
            str(source.source_revision), source.source_filename)
    if not documents:
        raise ValueError('请先导入小说或分集原文，再重新选角')
    revision = (documents['novel.txt'].content_hash if list(documents) == ['novel.txt'] else
        snapshot_digest({k: (v.content_hash, v.revision, v.filename) for k, v in sorted(documents.items())}))
    return documents, revision


def assert_live_sources(ctx, sqlite_store, documents, character_id, identity_id):
    """Synchronous last check for use inside the publication lock (no await)."""
    import sqlite3
    novel = Path(ctx.output_dir) / 'novel.txt'
    expected_novel = documents.get('novel.txt')
    if novel.is_file() != bool(expected_novel) or (expected_novel and source_sha256(novel.read_text(encoding='utf-8')) != expected_novel.content_hash):
        raise ValueError('source changed during recast')
    with sqlite3.connect(f'file:{sqlite_store.db_path}?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        character = db.execute('SELECT identities_json FROM characters WHERE name = ?', (character_id,)).fetchone()
        if character is None or (identity_id and not any(i['identity_id'] == identity_id for i in json.loads(character[0] or '[]'))):
            raise ValueError('character or identity no longer exists')
        rows = db.execute('SELECT * FROM episode_sources ORDER BY episode_number').fetchall()
        if len(rows) != len([k for k in documents if k.startswith('episode:')]):
            raise ValueError('source changed during recast')
        for row in rows:
            doc = documents.get(f"episode:{row['episode_number']:04d}")
            if doc is None or (doc.content_hash, doc.revision, doc.filename) != (source_sha256(row['raw_content']), str(row['source_revision']), row['source_filename']):
                raise ValueError('source changed during recast')


def attested_names(profile, documents):
    names = [profile.name]
    for alias in profile.aliases:
        if not alias or alias == profile.name:
            continue
        pattern = re.escape(profile.name) + r'[^。\n]{0,20}(?:又名|别名|化名|人称)[^。\n]{0,4}' + re.escape(alias)
        if any(re.search(pattern, doc.text) for doc in documents.values()):
            names.append(alias)
    return names


def _relocate_quote(evidence, documents, claimed_doc, claimed_start):
    """Find a verbatim quote in project sources; model offsets are never trusted."""
    if not evidence:
        return None
    others = sorted((doc for doc in documents.values() if doc is not claimed_doc),
                    key=lambda doc: doc.document_id)
    for doc in ([claimed_doc] if claimed_doc is not None else []) + others:
        positions = [match.start() for match in re.finditer(re.escape(evidence), doc.text)]
        if not positions:
            continue
        anchor = claimed_start if doc is claimed_doc and isinstance(claimed_start, int) else 0
        start = min(positions, key=lambda position: (abs(position - anchor), position))
        return doc, start, start + len(evidence)
    return None


def verified_fact(fact, documents, names, source_revision, *, strict=False):
    doc = documents.get(fact.source_document or 'novel.txt')
    start, end = fact.source_start, fact.source_end
    reason = None
    if doc is None:
        reason = 'document'
    elif start is None or end is None or not 0 <= start < end <= len(doc.text):
        reason = 'offset_range'
    elif doc.text[start:end] != fact.evidence:
        reason = 'quote_mismatch'
    if reason in _POSITION_REASONS:
        # Model-reported offsets are unreliable; a verbatim hit in the project
        # sources replaces the claim instead of failing the fact. The gates below
        # then run on the real source bytes.
        relocated = _relocate_quote(fact.evidence, documents, doc, start)
        if relocated is not None:
            doc, start, end = relocated
            fact = fact.model_copy(update={'source_document': doc.document_id, 'source_start': start,
                'source_end': end, 'evidence': doc.text[start:end]})
            reason = None
    if reason is None and fact.source_revision not in (None, source_revision, doc.content_hash):
        reason = 'source_revision'
    if reason is None and not evidence_supports(fact):
        # Attribution itself uses evidence_supports; distinguish unsupported
        # values first so the diagnostic identifies the failed prerequisite.
        reason = 'value_support'
    if reason is None and not attributed_clause(fact, names):
        reason = 'attribution'
    if reason is None:
        # Do not let a truncated quote hide a later attributive complement,
        # e.g. claiming "甲是七十岁" from "甲是七十岁的乙的儿子".
        left = max((doc.text.rfind(mark, 0, start) for mark in '。！？!?；;，,\n'), default=-1) + 1
        boundaries = [pos for mark in '。！？!?；;，,\n' if (pos := doc.text.find(mark, max(start, end - 1))) >= 0]
        right = min(boundaries) + 1 if boundaries else len(doc.text)
        if not attributed_clause(fact.model_copy(update={'evidence': doc.text[left:right]}), names):
            reason = 'full_clause_attribution'
    if reason is not None:
        if strict:
            raise SourceFactVerificationError(reason, fact)
        return None
    return fact.model_copy(update={'source_document': doc.document_id, 'source_revision': source_revision,
        'source_span': SourceSpan(start_line=doc.text.count('\n', 0, start) + 1,
                                end_line=doc.text.count('\n', 0, end) + 1), 'trust': 'trusted'})


def attributed_clause(fact, names):
    """Conservative direct-subject check, not a general semantic entailment proof.

    Whole paragraphs mentioning a target are not evidence for attributes asserted
    about someone else. Ambiguous actions/complements require a narrower quote.
    """
    for clause in re.split(r'[。！？!?；;，,\n]', fact.evidence):
        if not evidence_supports(fact.model_copy(update={'evidence': clause})):
            continue
        for name in names:
            match = re.search(re.escape(name), clause)
            if match is None:
                continue
            remainder = clause[match.end():].lstrip(' ：:—-')
            if fact.field in ('relationship', 'relationships'):
                if re.match(r'(?:和|与|是|的)', remainder):
                    return True
                continue
            # Descriptions of someone's relative/owner are not attributes of
            # that person. Both literal and normalized values obey this guard.
            if (re.search(r'的[^。！？!?；;，,\n]*的', remainder)
                or re.search(r'(?:的)(?:父亲|母亲|父母|儿子|女儿|孩子|哥哥|弟弟|姐姐|妹妹|兄弟|姐妹|丈夫|妻子|爷爷|奶奶|祖父|祖母|外公|外婆|朋友|邻居|学生|老师|主人|仆人|亲戚|亲人|后代|孙子|孙女)', remainder)):
                continue
            # A short direct predication is intentionally narrower than mere
            # co-occurrence; unresolved syntax is never promoted to hard facts.
            value_at = remainder.find(fact.value)
            if value_at >= 0:
                prefix = remainder[:value_at]
                if re.fullmatch(r'(?:的|是|为|很|已经|已|今年|年纪|年龄|年方|年近|年过|年仅|现年|约|大约|将近|近|才|刚|只有|一名|一位|一个|一只|一种|名|位|个|只|长得|生得|有|长着|留着|身穿|穿着|身材|面容|脸型|头发|职业|身份|担任|从事|显得|看上去|看起来|：|:|\s)*', prefix):
                    return True
            elif re.match(r'^(?:是|为|一只|一种|年过|已|今年|现年)', remainder):
                # Normalized species/gender/age aliases still require direct
                # descriptive syntax, never a transitive observation/action.
                return True
    return False


async def reuse_artifact_facts(profile, documents, source_revision, sqlite_store):
    """Artifacts are hints; every fact is still revalidated against current bytes."""
    from .casting_brief import profile_from_merged
    from types import SimpleNamespace

    evidence = await sqlite_store.list_entity_evidence('character', profile.name)
    facts = []
    db = await sqlite_store._ensure_db()
    for run_id in dict.fromkeys(row['run_id'] for row in evidence):
        async with db.execute('SELECT source_sha256 FROM story_analysis_runs WHERE run_id = ?', (run_id,)) as cursor:
            run = await cursor.fetchone()
        if not run or run[0] not in {doc.content_hash for doc in documents.values()}:
            continue
        raw = await sqlite_store.get_analysis_artifact(run_id, 'characters')
        if not raw:
            continue
        try:
            payload = json.loads(raw)
            rows = payload if isinstance(payload, list) else payload.get('characters', [])
            for row in rows:
                if row.get('name') != profile.name:
                    continue
                # No scalar defaults, biography or face prompts become evidence.
                item = SimpleNamespace(name=profile.name, aliases=[], biography='', description='', occupation='',
                    role='', social_identity='', relationships=[], personality=[], dramatic_function='',
                    evidence=row.get('evidence', []), voice_facts=None)
                for doc in documents.values():
                    if doc.content_hash == run[0]:
                        item.evidence = [{**e, 'source_document': e.get('source_document') or doc.document_id}
                                         for e in row.get('evidence', [])]
                        facts.extend(profile_from_merged(item, doc.content_hash, doc.text).facts)
        except (ValueError, TypeError, KeyError):
            continue
    return facts


async def ground_profile(profile, documents, source_revision, *, runtime, identity_id=None, artifact_facts=()):
    if not documents:
        raise ValueError('请先导入原文，再重新选角')
    names = attested_names(profile, documents)
    warnings = list(profile.source_warnings)
    facts = [verified for f in [*profile.facts, *artifact_facts]
             if f.trust == 'trusted' and f.identity_id in (None, identity_id)
             and (verified := verified_fact(f, documents, names, source_revision))]
    if not facts:
        warnings = []
        windows = []
        for doc in documents.values():
            for match in re.finditer('|'.join(map(re.escape, names)), doc.text):
                start, end = max(0, match.start() - 300), min(len(doc.text), match.end() + 600)
                if any(w['source_document'] == doc.document_id and w['start'] <= match.start() < w['end'] for w in windows):
                    continue
                windows.append(dict(source_document=doc.document_id, start=start, end=end, text=doc.text[start:end],
                                    content_hash=doc.content_hash, source_revision=doc.revision, filename=doc.filename))
                if len(windows) >= 12:
                    break
            if len(windows) >= 12:
                break
        if not windows:
            raise ValueError('原文中未找到该角色或有证据支持的别名；请检查角色名称')
        if runtime is None:
            raise ValueError('请先配置 knowledge_extraction 文本任务运行时')
        output = await runtime.run_structured(output_type=FactExtraction,
            system_prompt='仅提取指定角色在原文直接陈述的事实，不设计形象，不推断年龄、职业或性格。field 必须使用 schema 中的英文枚举。value 必须逐字截取 evidence 中直接描述该角色的连续词语，不得总结、改写、补全或把动作概括成性格/职业。每条 evidence 必须是逐字原文且含角色名或已核验别名，offset 是文档绝对字符位置。只选角色本人的直接陈述，例如“甲是老人”的 value 是“老人”；“甲看见老人”不能证明甲是老人。不得引用其他人物。不要从身份阶段名称推断事实。无满足条件的证据返回空列表，不必凑数。',
            prompt=json.dumps(dict(character=profile.name, attested_aliases=names[1:], identity_id=identity_id, windows=windows), ensure_ascii=False))
        output = FactExtraction.model_validate(output.model_dump() if isinstance(output, BaseModel) else output)
        for row in output.facts:
            fact = CharacterNarrativeFact(**row.model_dump(), fact_id='source-' + snapshot_digest(row.model_dump())[:24],
                source_revision=source_revision, confidence=1, source_span=SourceSpan(start_line=1, end_line=1))
            outside_window = not any(w['source_document'] == row.source_document
                and w['start'] <= row.source_start < row.source_end <= w['end'] for w in windows)
            reason = 'wrong_identity' if row.identity_id not in (None, identity_id) else None
            if reason is None:
                try:
                    facts.append(verified_fact(fact, documents, names, source_revision, strict=True))
                    continue
                except SourceFactVerificationError as exc:
                    reason = exc.reason
                    if outside_window and reason in _POSITION_REASONS:
                        reason += '/outside_window'
            # A single unverifiable fact only removes that fact: the recast
            # continues with the verified remainder and reports what was dropped.
            warnings.append(('excluded_narrative:' if fact.field in _NARRATIVE_CONTEXT_FIELDS
                else 'excluded_source:') + f'{fact.field}:{reason}')
    # Narrative summaries are not independently trusted; derive only verified fields.
    fields = {f.field: f.value for f in facts if f.identity_id is None}
    return CharacterNarrativeProfile(character_id=profile.character_id, name=profile.name, aliases=names[1:],
        biography=fields.get('biography', ''), occupation=fields.get('occupation', ''),
        social_identity=fields.get('social_identity', ''), personality=[fields['personality']] if 'personality' in fields else [],
        relationships=[f.value for f in facts if f.field == 'relationship'],
        dramatic_function=fields.get('dramatic_function', ''), facts=list({f.fact_id: f for f in facts}.values()),
        source_warnings=list(dict.fromkeys(warnings)))
