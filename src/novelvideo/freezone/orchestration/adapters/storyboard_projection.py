"""Local storyboard projection: a bound director revision -> storyboard table rows.

The operation is a pure read of versioned project state. It never submits a
provider request, and it freezes the exact revision identity into its result so
a downstream consumer can prove which plan produced the rows.
"""

from __future__ import annotations

from ..models import OrchestrationError, fingerprint

OPERATIONS = {("scriptNode", "project")}
LOCAL_OPERATIONS = set(OPERATIONS)

_MAX_REVISION_ID = 128


def _bound(data: dict) -> tuple[int, str]:
    """Read the fixed episode/revision binding; never infer it from the graph."""
    episode = data.get("episodeNumber")
    revision_id = data.get("directorRevisionId")
    missing: list[str] = []
    if type(episode) is not int or episode < 1:
        missing.append("episodeNumber")
    if (not isinstance(revision_id, str) or not revision_id.strip()
            or len(revision_id.strip()) > _MAX_REVISION_ID
            or any(character in revision_id for character in "/\\%?#")):
        missing.append("directorRevisionId")
    if missing:
        raise OrchestrationError("input_required",
                                 "Storyboard projection requires " + " and ".join(missing),
                                 fields=missing)
    return episode, revision_id.strip()


async def submit(run, attempt, node, operation):
    from novelvideo.api.routes import director_workspace as director
    from novelvideo.director_plan.projection import project_plan_rows
    from novelvideo.director_plan.store import DirectorPlanStore

    episode, revision_id = _bound(node.get("data") or {})
    ctx = await director._resolve(run["project_id"], run["actor"], role="viewer")
    try:
        revision = DirectorPlanStore(ctx.output_dir).load(episode, revision_id)
    except FileNotFoundError as exc:
        raise OrchestrationError("director_revision_not_found",
                                 "Director revision does not exist",
                                 episode=episode, revision_id=revision_id) from exc
    except ValueError as exc:
        raise OrchestrationError("invalid_input", str(exc)) from exc
    if revision.episode != episode:
        raise OrchestrationError("scope_violation",
                                 "Director revision episode does not match", episode=episode)
    rows = project_plan_rows(revision)
    return {"local": True, "result": {"workflowResult": {
        "episode": episode,
        "directorRevisionId": revision_id,
        "projectionHash": fingerprint(rows),
        "rows": rows,
    }}}
