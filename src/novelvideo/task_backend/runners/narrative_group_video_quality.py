"""Existing-video inspection task, with no generation or automatic retry."""
import asyncio
from novelvideo.shot_continuity.quality_workflow import execute_quality_check
from novelvideo.task_backend.registry import register_project_task_runner


def run_narrative_group_video_quality(envelope, ctx):
    return asyncio.run(execute_quality_check(envelope, ctx))


register_project_task_runner('narrative_group_video_quality', run_narrative_group_video_quality,
    text_task_role='h3_episode_pack')
