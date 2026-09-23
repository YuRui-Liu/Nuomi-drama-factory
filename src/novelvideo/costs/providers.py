"""Provider-side lifecycle capture; only explicit, safe request facts enter the ledger."""
from contextlib import contextmanager
from datetime import datetime, timezone
from hashlib import sha256
import logging
from uuid import uuid4

from .context import CostContext, cost_context, resolve_cost_context
from .models import CostAttempt
from .service import Observation, get_cost_service

logger = logging.getLogger(__name__)


@contextmanager
def requested_cost_context(media_type, *, attempt_id=None, usage=None, specifications=()):
    """Attach semantic quantities at callers, without inspecting provider node payloads."""
    context = resolve_cost_context()
    if context is None:
        yield
        return
    inherited_usage = context.usage if context.media_type == media_type else {}
    inherited_specs = context.specifications if context.media_type == media_type else ()
    updates = {'media_type': media_type, 'usage': {**inherited_usage, **(usage or {})},
               'specifications': tuple(sorted({**dict(inherited_specs), **dict(specifications)}.items()))}
    updates['attempt_id'] = attempt_id or str(uuid4())
    with cost_context(CostContext.model_validate({**context.model_dump(), **updates})):
        yield


class ProviderCapture:
    def __init__(self, provider, *, account_id=None, context=None, service=None):
        self.provider = provider
        self.account_id = account_id
        self.context = context
        self._service = service
        self._accepted = {}

    @property
    def service(self):
        if self._service is None:
            self._service = get_cost_service()
        return self._service

    def resolve(self, media_type=None):
        context = self.context or resolve_cost_context()
        if context is None:
            return None
        if not self.account_id:
            raise ValueError('Project cost attribution requires a configured account_id')
        if media_type is not None:
            context = CostContext.model_validate({**context.model_dump(), 'media_type': media_type,
                'usage': context.usage if context.media_type == media_type else {},
                'specifications': context.specifications if context.media_type == media_type else ()})
        return context

    def prepare(self, *, model, media_type, workflow=None, usage=None, fresh=False):
        context = self.resolve(media_type)
        if context is None:
            return None
        if media_type is None:
            raise ValueError('Project cost attribution requires workflow media metadata')
        return self.service.prepare(CostAttempt(
            attempt_id=str(uuid4()) if fresh else context.attempt_id or str(uuid4()),
            project_id=context.project_id, task_id=context.task_id, resource_id=context.resource_id,
            provider=self.provider, account_id=self.account_id, model=model,
            media_type=media_type, workflow=workflow, occurred_at=datetime.now(timezone.utc),
            usage={'call': '1'} if fresh else {**context.usage, **(usage or {}), 'call': '1'}, usage_source='request',
            specifications=() if fresh else context.specifications,
        ))

    def _after_send(self, operation):
        # Accounting failure must not turn an accepted generation into a retry.
        # Do not include exception text: database/adapter exceptions can carry secrets.
        try:
            return operation()
        except Exception:
            logger.error('Provider cost capture data gap: provider=%s', self.provider)
            return None

    def accepted(self, attempt, external_id):
        if attempt is not None:
            self._accepted[external_id] = attempt
            self._after_send(lambda: self.service.submitted(attempt.attempt_id, external_id))

    def interrupted(self, attempt):
        if attempt is not None:
            self._after_send(lambda: self.service.mark_submission_unknown(attempt.attempt_id))

    def observe(self, external_id, status):
        context = self.resolve()
        if context is None:
            return
        def apply():
            attempt = self.service.find_by_external(self.provider, self.account_id, external_id)
            if attempt is None:
                attempt = self._accepted.get(external_id)
                if attempt is None and context.attempt_id:
                    try:
                        attempt = self.service.store.get_attempt(context.attempt_id)
                    except KeyError:
                        return
                if attempt is None:
                    return
                if (attempt.project_id != context.project_id or attempt.provider != self.provider
                        or attempt.account_id != self.account_id or attempt.external_id not in (None, external_id)):
                    return
                self.service.submitted(attempt.attempt_id, external_id)
            if attempt.project_id != context.project_id:
                return
            state = {'queued': 'pending'}.get(status, status)
            if state not in {'pending', 'running', 'succeeded', 'failed', 'cancelled', 'unknown'}:
                state = 'unknown'
            facts = Observation(execution_status=state)
            event_id = 'poll:' + sha256(facts.model_dump_json().encode()).hexdigest()
            self.service.observe(attempt.attempt_id, event_id, facts)
        self._after_send(apply)
