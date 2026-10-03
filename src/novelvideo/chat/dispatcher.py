"""Select a chat execution backend from a per-turn configuration snapshot."""

import asyncio

from novelvideo.chat.runtime_settings import load_chat_runtime_settings


async def get_chat_thread(username: str, *, scope_kind: str, project_id: str | None):
    settings = load_chat_runtime_settings()
    if settings.backend == "hermes":
        from novelvideo.chat.hermes_pool import pool

        return await pool.get_for_user(
            username, scope_kind=scope_kind, project_id=project_id,
            model=settings.model,
        )
    from novelvideo.chat.cli_agent import CliAgentThread

    return CliAgentThread(
        username, scope_kind, project_id, settings.backend, settings.model,
        settings.reasoning_effort,
    )


async def cancel_user(username: str) -> bool:
    # Configuration may have changed since this user's active turn started.
    from novelvideo.chat.cli_agent import cancel_user as cancel_cli
    from novelvideo.chat.hermes_pool import pool

    results = await asyncio.gather(
        cancel_cli(username), pool.close_user(username), return_exceptions=True,
    )
    return any(result is True for result in results)
