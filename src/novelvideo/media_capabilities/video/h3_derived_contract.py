"""Deterministic audit indexes; never invent or rewrite story actions."""

from .h3_director_plan import H3DirectorPlan
from .h3_rigid_prompt import H3PositiveConstraint


def normalize_derived_contract(plan: H3DirectorPlan) -> H3DirectorPlan:
    rigid = plan.rigid_prompt
    if rigid is None:
        return plan
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
        "physics": rigid.physics.model_copy(update={"moving_entities": entities}),
        "positive_constraints": constraints,
    })})
