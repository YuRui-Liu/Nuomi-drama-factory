import asyncio
import json
from pathlib import Path

from novelvideo.freezone.video_director.capabilities import describe_capabilities, validate_generation
from novelvideo.technique_library.catalog import get_case

from .models import ProposalInput, ProposalResult
from .prompts import SYSTEM_PROMPT
from .store import ProposalStore


class ProposalService:
    def __init__(self, ctx, *, reference_limit=5):
        self.store = ProposalStore(Path(ctx.runtime_dir) / 'freezone' / 'case_reference')
        self.reference_limit = reference_limit

    def create(self, owner: str, body: ProposalInput):
        case = get_case(body.case_id)
        if not case:
            raise ValueError('案例不存在')
        records = [p for p in case.get('prompts', []) if p['content_sha256'] in body.prompt_hashes]
        if set(body.prompt_hashes) != {p['content_sha256'] for p in records}:
            raise ValueError('所选提示词不属于此案例，请刷新后重试')
        if not records and len(body.description.strip()) < 10:
            raise ValueError('缺少具体依据：请选择原提示词，或补充至少 10 字的案例画面描述')
        validate_generation(body.effective_draft, reference_limit=self.reference_limit)
        evidence = dict(prompts=records, sources=case.get('sources', []), description=body.description.strip(),
                        media_analyzed=False, capabilities=describe_capabilities(self.reference_limit))
        return self.store.create(owner, body.request_id, body.model_dump(mode='json'), evidence)

    async def run(self, proposal_id, owner, runtime, route=None):
        if not self.store.claim(proposal_id, owner):
            return self.store.get(proposal_id, owner)
        item = self.store.transition(proposal_id, owner, ('running',), 'running', text_route_snapshot=route or {})
        try:
            body = ProposalInput.model_validate(item['input'])
            response = await runtime.run_structured(system_prompt=SYSTEM_PROMPT,
                prompt='INPUT_JSON:\n' + json.dumps(dict(target=body.effective_draft.model_dump(mode='json'),
                    segment_id=body.segment_id, dimensions=body.dimensions, evidence=item['evidence']), ensure_ascii=False),
                output_type=ProposalResult)
            result = ProposalResult.model_validate(response)
            if not result.compatible or result.conflict:
                raise ValueError(result.conflict or '案例与当前分段不兼容')
            if not result.prompt.strip() or sorted(s.dimension for s in result.suggestions) != sorted(body.dimensions):
                raise ValueError('参考方案缺少所选维度的完整依据')
            updated = body.effective_draft.model_copy(update={'segments': tuple(
                s.model_copy(update={'prompt': result.prompt}) if s.id == body.segment_id else s
                for s in body.effective_draft.segments)})
            validate_generation(updated, reference_limit=item['evidence']['capabilities']['reference_limit'])
            return self.store.transition(proposal_id, owner, ('running',), 'succeeded', result=result.model_dump(mode='json'))
        except asyncio.CancelledError:
            self.store.transition(proposal_id, owner, ('queued', 'running'), 'cancelled')
            raise
        except Exception as exc:
            return self.store.transition(proposal_id, owner, ('running',), 'failed', error=str(exc)[:2000])
