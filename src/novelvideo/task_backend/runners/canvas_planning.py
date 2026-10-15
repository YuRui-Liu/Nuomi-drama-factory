"""Canvas planning uses the existing routed, durable text task backend."""

import asyncio

from novelvideo.freezone.orchestration.planning import generate_blueprint
from novelvideo.task_backend.cancel import await_envelope_with_cancel_watch
from novelvideo.task_backend.registry import register_project_task_runner


def run_canvas_planning(envelope, ctx):
    payload = envelope["payload"]
    async def plan():
        from novelvideo.freezone.orchestration.director_bridge import project_asset_catalog, bind_canvas_designs
        catalog = await project_asset_catalog(ctx)
        catalog = bind_canvas_designs(catalog, payload.get('base_design_bindings', []),
                                      project_dir=ctx.output_dir, project_id=ctx.project_id)
        from novelvideo.project_config import load_project_config_file_from_state_dir
        from novelvideo.character_visual.casting_service import resolved_style
        config = load_project_config_file_from_state_dir(ctx.state_dir)
        return await generate_blueprint(payload['script'], duration=payload['duration'],
            aspect_ratio=payload['aspect_ratio'], director_brief=payload.get('director_brief', ''),
            asset_catalog=catalog, project_style=resolved_style(ctx), image_model=config.get('narrative_render_model'),
            image_size=config.get('narrative_render_image_size', '2K'))
    return asyncio.run(await_envelope_with_cancel_watch(
        plan(),
        envelope, task_type="freezone_canvas_plan"))


register_project_task_runner("freezone_canvas_plan", run_canvas_planning, text_task_role="director_plan")
