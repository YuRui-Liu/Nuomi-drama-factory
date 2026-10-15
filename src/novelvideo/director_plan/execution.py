"""Executable shot contract shared by authoring, activation and H3 packing.

Keep the limit at the production-shot boundary: the adapter must not silently
cut or stretch an adopted creative shot to make it executable.
"""
from .models import ValidationIssue

MIN_CREATIVE_SHOT_SECONDS = 4
MAX_SHOT_SECONDS = 15


def video_duration_issues(groups):
    issues = []
    for group_index, group in enumerate(groups):
        for shot_index, shot in enumerate(group.shots):
            minimum = MIN_CREATIVE_SHOT_SECONDS if (
                group.creative_contract_version == 1 or shot.creative is not None
            ) else 0
            if shot.duration_seconds > MAX_SHOT_SECONDS or shot.duration_seconds < minimum:
                issues.append(ValidationIssue(
                    code="video_shot_duration_out_of_range",
                    location=f"groups.{group_index}.shots.{shot_index}.duration_seconds",
                    message=(f"Shot {shot.id} duration {shot.duration_seconds:g}s is outside "
                             f"the current video production range {minimum}-{MAX_SHOT_SECONDS}s. "
                             "Revise coverage at motivated action/dialogue cuts; preserve source "
                             "events, dialogue, total runtime and adjacent-shot continuity. "
                             "Do not truncate, accelerate speech or defer recutting to the adapter."),
                ))
    return tuple(issues)


def require_executable_shots(groups):
    issues = video_duration_issues(groups)
    if issues:
        raise ValueError("; ".join(issue.message for issue in issues))


VIDEO_EXECUTION_CONTRACT = f"""
VIDEO EXECUTION CONTRACT (current narrative-group H3 production path):
Each creative shot must last {MIN_CREATIVE_SHOT_SECONDS}-{MAX_SHOT_SECONDS} seconds.
These bounds constrain authoring and independent QC before activation, not just adapters.
For longer action, plan motivated coverage at action, dialogue or reaction boundaries
inside the same narrative group. Preserve all source events/dialogue and total runtime;
never satisfy a limit by dropping action, rushing speech or mechanically slicing time.
Update every new shot's opening/end state, keyframe and successor handoff coherently.
Keep unaffected groups and valid assets when repairing. A physically unexecutable
explicit creative request must be reported as a conflict, never silently accepted.
"""
