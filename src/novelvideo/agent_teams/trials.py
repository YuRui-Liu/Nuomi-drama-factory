"""Run native text methods against immutable evidence without production writes."""
import asyncio
import hashlib
import json
import inspect
import time
from uuid import uuid4

from .models import ExecutionSnapshot, MethodConfig
from .runtime import method_scope
from .service import StoredData, builtin_template
from .store import RevisionConflict


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def freeze_methods(service, project, request, frozen_input, route):
    draft = service.store.get_draft(project)
    if not draft or draft['draft_revision'] != request.expected_draft_revision:
        raise RevisionConflict('saved draft revision changed or is missing')
    active = service.store.get_binding(project)
    result = {}
    for side, data, revision in (
        ('active', StoredData.model_validate(active['snapshot']) if active else StoredData(template=builtin_template()), active['active_revision'] if active else 1),
        ('draft', StoredData.model_validate(draft['data']), draft['draft_revision']),
    ):
        method = MethodConfig.model_validate(service._effective(data)[request.role_id][request.subtask_id]['config'])
        pins = {(r.id,r.revision) for r in (*method.skills,*method.references)}
        snapshot = ExecutionSnapshot(id=uuid4().hex,project_id=project,template_id=data.template_id,
            template_revision=data.template_revision,active_revision=revision,role_id=request.role_id,subtask_id=request.subtask_id,
            input_revision=digest(frozen_input),input_hash=digest(frozen_input),resolved_method=method,
            resolved_model=route if method.model == 'project' else method.model,
            resource_snapshots=tuple(r for r in data.resources if (r.id,r.revision) in pins))
        result[side] = snapshot.model_dump(mode='json')
    return result


async def candidate(role, subtask, frozen):
    if role == 'writer':
        from novelvideo.script_creation.models import Document, Revision, Block
        from novelvideo.script_creation.prompts import build_prompt, craft_guidance
        from novelvideo.script_creation.generation import GenerationOutput, validate_stage_output, GENERATION_SYSTEM_PROMPT
        from .adapters import method_runtime, craft_method
        refs = []
        for raw in frozen['documents']:
            value = dict(raw)
            revision = dict(value['revision'])
            revision['blocks'] = tuple(Block(**b) for b in revision['blocks'])
            value['revision'] = Revision(**revision)
            refs.append(Document(**value))
        output = GenerationOutput.model_validate(await method_runtime(role,subtask).run_structured(
            prompt=build_prompt(kind=subtask,script_mode=frozen['script_mode'],episode_number=frozen['episode'],
                                episode_count=frozen['episode_count'],instruction=frozen['instruction'],references=refs),
            output_type=GenerationOutput,system_prompt=GENERATION_SYSTEM_PROMPT + craft_method(role,subtask,craft_guidance(subtask))))
        validate_stage_output(subtask,output.markdown,has_written_script=any(d.kind == 'episode_script' for d in refs))
        return {'markdown':output.markdown,'validation_report':{'passed':True}}
    if role == 'script_parser':
        from novelvideo.screenplay_semantics.parser import parse_screenplay_document
        from novelvideo.screenplay_semantics.extractor import extract_scene_beats, SceneExtractionFailure
        from novelvideo.screenplay_semantics.validation import validate_scene_beats
        parsed = parse_screenplay_document(frozen['content'])
        if not parsed.scenes:
            raise ValueError('screenplay has no scenes')
        results = await extract_scene_beats(parsed.scenes)
        reports = []
        for scene,result in zip(parsed.scenes,results):
            if isinstance(result,SceneExtractionFailure):
                raise ValueError('scene extraction failed: ' + result.scene_id)
            reports.append(validate_scene_beats(scene,result.beats).model_dump(mode='json'))
        return {'scenes':[s.model_dump(mode='json') for s in parsed.scenes], 'results':[r.model_dump(mode='json') for r in results],
                'validation_report':{'passed':all(r['passed'] for r in reports),'scenes':reports}}
    if role == 'director':
        from novelvideo.director_plan.planner import DirectorPlanner, DirectorPlanInput
        from novelvideo.director_plan.service import DirectorPlanService
        from novelvideo.director_plan.validation import validate_director_plan
        planner = DirectorPlanner()
        value = DirectorPlanInput.model_validate(frozen).model_copy(update={'director_model':planner.model_name})
        draft = await planner.plan_episode(value)
        revision = DirectorPlanService._make_revision(value,draft.groups)
        report = validate_director_plan(revision,value.source_spans,value.dramatic_beats)
        return {'plan':revision.model_dump(mode='json'),'validation_report':report.model_dump(mode='json')}
    raise ValueError('unsupported trial')


async def execute_side(store, project, id, side, attempt, cancel_check=None):
    trial = store.transition(project,id,side,attempt,{'queued'},status='running')
    if trial is None:
        return None
    start = time.monotonic()
    try:
        if cancel_check:
            check = cancel_check()
            if inspect.isawaitable(check):
                await check
        with method_scope([trial['methods'][side]],project_id=project):
            output = await candidate(trial['role_id'],trial['subtask_id'],trial['frozen_input'])
        if cancel_check:
            check = cancel_check()
            if inspect.isawaitable(check):
                await check
        store.transition(project,id,side,attempt,{'running'},status='completed',candidate=output,duration_seconds=time.monotonic()-start)
        return output
    except BaseException as exc:
        from novelvideo.task_backend.cancel import TaskCancelled, TaskLeaseLost
        cancelled = isinstance(exc,(asyncio.CancelledError,TaskCancelled,TaskLeaseLost))
        store.transition(project,id,side,attempt,{'running'},status='cancelled' if cancelled else 'failed',candidate=None,
                         error=type(exc).__name__,duration_seconds=time.monotonic()-start)
        raise
