"""Provenance-aware character visual domain."""

from .compiler import VisualPromptSnapshot, compile_visual_prompt_snapshot
from .models import (
    CharacterDesignBrief,
    CharacterDesignProposal,
    CharacterNarrativeFact,
    CharacterNarrativeProfile,
    CharacterVisualBible,
    CharacterVisualWorkspace,
    LegacyVisualField,
    SourceSpan,
    classify_legacy_visual_field,
)
from .store import CharacterVisualWorkspaceStore
from .proposals import (
    ProposalQualityError,
    assess_design_proposal,
    build_character_visual_workspace,
    validate_design_proposals,
)

__all__ = [
    "CharacterDesignBrief",
    "CharacterDesignProposal",
    "CharacterNarrativeFact",
    "CharacterNarrativeProfile",
    "CharacterVisualBible",
    "CharacterVisualWorkspace",
    "CharacterVisualWorkspaceStore",
    "ProposalQualityError",
    "LegacyVisualField",
    "SourceSpan",
    "VisualPromptSnapshot",
    "classify_legacy_visual_field",
    "compile_visual_prompt_snapshot",
    "assess_design_proposal",
    "build_character_visual_workspace",
    "validate_design_proposals",
]
