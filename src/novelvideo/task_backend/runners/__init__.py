"""Project task runner registration package.

Importing this package registers every built-in project task runner.
"""

from importlib import import_module

from novelvideo.task_backend.runners import (  # noqa: F401
    audio,
    character_image,
    director_plan,
    episode_assets,
    episode_import,
    freezone,
    graph_build,
    identity,
    ingest,
    narrative_group,
    narrative_group_video,
    narrative_group_video_compose,
    prop_reference,
    render,
    scene_reference,
    screenplay_semantics,
    screenplay_semantic_repair,
    script,
    sketch,
    sketch_edit_execute,
    stage_asset,
    video,
    voice_design,
)

# The episode-graph runner landed after some feature-branch bases. Register it
# whenever that module is present; do not make unrelated task registration
# prevent this package from loading on an older base.
try:  # pragma: no cover - depends on the integration branch's module set
    episode_graph = import_module("novelvideo.task_backend.runners.episode_graph")
except ModuleNotFoundError as exc:  # pragma: no cover
    if exc.name != "novelvideo.task_backend.runners.episode_graph":
        raise
