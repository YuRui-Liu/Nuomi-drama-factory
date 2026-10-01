"""Project-bound cost recovery orchestration without generation or credential output."""
from datetime import datetime, timezone
from pathlib import Path

from novelvideo.media_capabilities.runtime.runninghub_client import RunningHubClient
from novelvideo.project_context import is_record_home_node

from .backfill import backfill_project
from .recovery import refresh_runninghub_usage
from .historical import manifest_tasks, recover_manifest_receipts


async def recover_project_costs(project_record, service, configured_accounts, credential_resolver):
    """Import local proven submissions, then query their original RH accounts.

    The caller must supply an authorized registry ProjectRecord and configured
    ProviderAccount objects, never caller-provided paths or provider definitions.
    Unavailable/disabled accounts remain missing; no fallback account is used.
    """
    if not is_record_home_node(project_record):
        raise ValueError('Recovery must run on the project home node')
    runtime_dir = project_record.runtime_dir
    if not runtime_dir or not Path(runtime_dir).is_absolute():
        raise ValueError('Registry runtime_dir must be an absolute project directory')
    backfill = backfill_project(service.store, project_record.id, runtime_dir,
        now=datetime.now(timezone.utc), created_at=project_record.created_at)
    needed = {a.account_id for a in service.store.list_attempts(project_record.id)
              if a.provider == 'runninghub' and a.external_id}
    legacy_tasks = manifest_tasks(project_record)
    if legacy_tasks:
        needed.update(a.id for a in configured_accounts if a.provider_type == 'runninghub' and a.enabled)
    clients = {}
    unavailable = 0
    try:
        for account in configured_accounts:
            if (account.id not in needed or account.id in clients
                    or account.provider_type != 'runninghub' or not account.enabled):
                continue
            try:
                key = credential_resolver.resolve(account.credential_ref)
                clients[account.id] = RunningHubClient(key, account_id=account.id,
                    base_url=account.base_url or RunningHubClient.DEFAULT_BASE_URL,
                    cost_service=service)
            except Exception:
                # Credential and transport construction errors can contain secrets.
                unavailable += 1
        try:
            refresh = await refresh_runninghub_usage(service, project_record.id, clients)
        except Exception:
            raise RuntimeError('Usage recovery failed') from None
        historical = await recover_manifest_receipts(service.store, project_record.id, legacy_tasks, clients)
        return {'backfill': backfill, 'refresh': refresh, 'historical': historical, 'unavailable_accounts': unavailable}
    finally:
        for client in clients.values():
            try:
                await client.close()
            except Exception:
                # Close every client even if one transport fails to shut down.
                pass
