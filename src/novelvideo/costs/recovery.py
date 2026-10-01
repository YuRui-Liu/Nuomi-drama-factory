"""Recover measured usage by querying already submitted tasks; never submit work."""
from .context import CostContext, cost_context


async def refresh_runninghub_usage(service, project_id, clients):
    """Poll known ledger identities using account-keyed RunningHub clients.

    Run ``backfill_project`` first to import proven historical submissions. Clients
    must use this service and their corresponding configured account. The caller
    owns/ closes clients. Only counts are returned; no responses or secrets escape.
    Missing historical identities remain coverage gaps, never synthetic charges.
    """
    result = dict(queried=0, measured=0, missing_account=0, failed=0)
    for attempt in service.store.list_attempts(project_id):
        if attempt.provider != 'runninghub' or not attempt.external_id:
            continue
        client = clients.get(attempt.account_id)
        if client is None:
            result['missing_account'] += 1
            continue
        capture = client._cost_capture
        if capture.account_id != attempt.account_id or capture.service is not service:
            raise ValueError('Recovery client must use the matching ledger and account')
        try:
            with cost_context(CostContext(project_id=project_id, attempt_id=attempt.attempt_id,
                                          media_type=attempt.media_type)):
                await client.query(attempt.external_id)
            result['queried'] += 1
            current = service.store.get_attempt(attempt.attempt_id)
            if current.usage_source == 'provider' and 'credit' in current.usage:
                result['measured'] += 1
        except Exception:
            # Provider errors may include sensitive response bodies.
            result['failed'] += 1
    return result
