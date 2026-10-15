"""Deterministic audit indexes; never invent or rewrite story actions."""

from .h3_director_plan import H3DirectorPlan
from .h3_rigid_prompt import H3PositiveConstraint


def normalize_derived_contract(plan: H3DirectorPlan) -> H3DirectorPlan:
    rigid = plan.rigid_prompt
    if rigid is None:
        return plan
    format_mode = rigid.format_mode
    # The shot timeline owns cut timing. Recompute redundant metadata only
    # for a complete, contiguous timeline; malformed shots still fail QC.
    if (plan.shots and plan.fps > 0
            and plan.shots[0].start_frame == 0
            and plan.shots[-1].end_frame == plan.total_frames
            and all(shot.end_frame > shot.start_frame for shot in plan.shots)
            and all(left.end_frame == right.start_frame
                    for left, right in zip(plan.shots, plan.shots[1:]))
            and format_mode.mode == "hard_cuts"):
        format_mode = format_mode.model_copy(update={
            "mode": "hard_cuts" if len(plan.shots) > 1 else "single_take",
            "total_duration_seconds": plan.total_frames / plan.fps,
            "cut_points_seconds": tuple(shot.start_frame / plan.fps for shot in plan.shots[1:]),
        })
    entities = tuple(dict.fromkeys(
        entity for shot in plan.shots for action in shot.actions
        for entity in action.moving_entities
    ))
    held_props = {prop for block in rigid.spatial_blocking
                  for subject in block.subjects for prop in subject.held_props}
    counts = {
        "characters": len(rigid.scene_context.active_characters),
        "references": len(rigid.active_references),
        "props": len(held_props),
    }
    constraints = tuple(item for item in rigid.positive_constraints
                        if item.target not in counts) + tuple(
        H3PositiveConstraint(target=target, count=count,
                             assertion=f"The structured {target} inventory contains {count} entries.")
        for target, count in counts.items()
    )
    return plan.model_copy(update={"rigid_prompt": rigid.model_copy(update={
        "format_mode": format_mode,
        "physics": rigid.physics.model_copy(update={"moving_entities": entities}),
        "positive_constraints": constraints,
    })})
