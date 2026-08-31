"""Project task runner registration package.

Importing this package registers every built-in project task runner.
"""

from novelvideo.task_backend.runners import (  # noqa: F401
    audio,
    character_image,
    director_plan,
    episode_assets,
    episode_import,
    episode_graph,
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
    script,
    sketch,
    sketch_edit_execute,
    stage_asset,
    video,
    voice_design,
)
