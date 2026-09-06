"""Provenance-aware character visual domain."""

from .compiler import VisualPromptSnapshot, compile_visual_prompt_snapshot
from .identity_sheet import (
    IDENTITY_SHEET_LAYOUT_VERSION,
    IDENTITY_SHEET_PANEL_LAYOUT,
    IdentitySheetComposition,
    IdentitySheetQualityReport,
    IdentitySheetStyleFamily,
    build_identity_sheet_v2_prompt,
    classify_identity_sheet_style,
    compose_identity_sheet_v2,
    resolve_identity_sheet_style_family,
)
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
    "IDENTITY_SHEET_LAYOUT_VERSION",
    "IDENTITY_SHEET_PANEL_LAYOUT",
    "IdentitySheetComposition",
    "IdentitySheetQualityReport",
    "IdentitySheetStyleFamily",
    "ProposalQualityError",
    "LegacyVisualField",
    "SourceSpan",
    "VisualPromptSnapshot",
    "classify_legacy_visual_field",
    "compile_visual_prompt_snapshot",
    "build_identity_sheet_v2_prompt",
    "classify_identity_sheet_style",
    "compose_identity_sheet_v2",
    "resolve_identity_sheet_style_family",
    "assess_design_proposal",
    "build_character_visual_workspace",
    "validate_design_proposals",
]
