"""Narrative-group aggregation and orchestration."""

from .models import CellMapping, GridLayout, GroupStageState, NarrativeGroup
from .service import group_beats, layout_for_group

__all__ = [
    "CellMapping",
    "GridLayout",
    "GroupStageState",
    "NarrativeGroup",
    "group_beats",
    "layout_for_group",
]
