"""Resolve submitted references to saved project evidence, then freeze once."""
from dataclasses import asdict
from pathlib import Path
import sqlite3

from .runtime import WRITER_KINDS
from .service import TeamError
from .store import RevisionConflict

SUPPORTED = {*(('writer', k) for k in WRITER_KINDS), ('script_parser','screenplay_semantics'), ('director','director_plan')}
TASK_ROLES = {'writer': 'script_creation', 'script_parser':'episode_normalization', 'director':'director_plan'}


async def choices(ctx, role_id, subtask_id, episode):
    if (role_id,subtask_id) not in SUPPORTED:
        return {'trial_supported': False, 'documents': [], 'sources': []}
    if role_id == 'writer':
        from novelvideo.script_creation.store import DocumentStore
        path = Path(ctx.state_dir) / 'data.db'
        docs = []
        if path.is_file():
            try:
                docs = await DocumentStore(path).list()
            except sqlite3.OperationalError as exc:
                if 'no such table' not in str(exc):
                    raise
        return {'trial_supported': True, 'documents': [
            {'id': d.id,'revision_id': d.current_revision_id,'title':d.title,'kind':d.kind,'episode':d.episode_number} for d in docs], 'sources': []}
    from novelvideo.api.deps import make_sqlite_store_for_context
    from novelvideo.episode_source_store import EpisodeSourceStore
    sources = await EpisodeSourceStore(await make_sqlite_store_for_context(ctx)).list_sources()
    items = []
    for source in sources:
        if source.episode_number != episode:
            continue
        reason = None
        if role_id == 'director':
            from novelvideo.screenplay_semantics import ScreenplaySemanticStore
            semantic = ScreenplaySemanticStore(ctx.output_dir).load_active(episode)
            if semantic is None:
                reason = 'SCREENPLAY_SEMANTICS_REQUIRED'
            elif semantic.source_revision != source.source_revision:
                reason = 'SCREENPLAY_SEMANTICS_SOURCE_CONFLICT'
        items.append({'episode':source.episode_number,'source_revision':source.source_revision,
                      'available':reason is None,'unavailable_reason':reason})
    return {'trial_supported': True, 'documents': [], 'sources': items}


async def freeze_input(ctx, request):
    if (request.role_id, request.subtask_id) not in SUPPORTED:
        raise ValueError('trial method is not supported')
    if request.role_id == 'writer':
        from novelvideo.script_creation.store import DocumentStore
        path = Path(ctx.state_dir) / 'data.db'
        if not path.is_file():
            raise ValueError('saved documents are required')
        store = DocumentStore(path)
        refs = []
        if not request.input.documents:
            raise ValueError('select at least one saved document')
        for ref in request.input.documents:
            from novelvideo.script_creation.store import DocumentNotFound
            try:
                doc = await store.get(ref.id)
            except DocumentNotFound as exc:
                raise ValueError('saved document not found') from exc
            except sqlite3.OperationalError as exc:
                if 'no such table' not in str(exc):
                    raise
                raise ValueError('saved documents are required') from exc
            if doc.current_revision_id != ref.revision_id:
                raise RevisionConflict('saved document revision changed')
            refs.append(asdict(doc))
        return {'documents':refs,'episode':request.episode,'script_mode':request.script_mode,
                'episode_count':request.episode_count,'instruction':request.instruction}
    from novelvideo.api.deps import make_sqlite_store_for_context
    from novelvideo.episode_source_store import EpisodeSourceStore
    sources = await EpisodeSourceStore(await make_sqlite_store_for_context(ctx)).list_sources()
    source = next((s for s in sources if s.episode_number == request.episode), None)
    if source is None or source.source_revision != request.input.source_revision:
        raise RevisionConflict('saved episode source revision changed or is missing')
    if request.role_id == 'director':
        from novelvideo.task_backend.runners.director_plan import _build_director_plan_input, DirectorPlanTaskError
        try:
            value = await _build_director_plan_input({'project_id':ctx.project_id,'episode':request.episode,
                                                      'source_revision':source.source_revision},ctx)
        except DirectorPlanTaskError as exc:
            if 'CONFLICT' in exc.error_code:
                raise RevisionConflict(exc.error_code) from exc
            raise TeamError(exc.error_code,'input') from exc
        return value.model_dump(mode='json')
    return {'content':source.content,'source_revision':source.source_revision,'episode':request.episode}
