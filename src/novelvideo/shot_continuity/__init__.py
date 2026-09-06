"""Public shot continuity domain contracts."""

from .builder import (
    ContinuityContractUnavailable,
    build_shot_continuity_contract,
    contracts_for_segment,
    director_world_binding,
)
from .compiler import (
    H3_SHOT_COMPILER_VERSION,
    compile_shot_bundle,
    continuity_locks_for,
)
from .hashing import canonical_sha256
from .mode_selector import select_h3_mode
from .models import (
    AssetEvidence,
    BoundaryState,
    CameraLock,
    CompiledShotBundle,
    ContractRef,
    DirectorWorldBinding,
    Evidence,
    FrameEvidence,
    H3ModeDecision,
    H3ReferenceBinding,
    LightingLock,
    PropLock,
    RiskDimensionScore,
    SceneLock,
    ShotContinuityContract,
    ShotRiskReport,
    SubjectLock,
)
from .risk import (
    ShotRiskSignals,
    audit_h3_shot,
    continuity_score,
    identity_score,
    motion_score,
    signals_for_shot,
    spatial_score,
)
from .store import ContinuityRevisionConflict, ShotContinuityStore

__all__ = [
    "AssetEvidence",
    "BoundaryState",
    "CameraLock",
    "CompiledShotBundle",
    "ContinuityContractUnavailable",
    "ContinuityRevisionConflict",
    "ContractRef",
    "DirectorWorldBinding",
    "Evidence",
    "FrameEvidence",
    "H3ModeDecision",
    "H3ReferenceBinding",
    "H3_SHOT_COMPILER_VERSION",
    "LightingLock",
    "PropLock",
    "RiskDimensionScore",
    "SceneLock",
    "ShotContinuityContract",
    "ShotContinuityStore",
    "ShotRiskReport",
    "ShotRiskSignals",
    "SubjectLock",
    "audit_h3_shot",
    "build_shot_continuity_contract",
    "canonical_sha256",
    "continuity_score",
    "compile_shot_bundle",
    "continuity_locks_for",
    "contracts_for_segment",
    "director_world_binding",
    "identity_score",
    "motion_score",
    "select_h3_mode",
    "signals_for_shot",
    "spatial_score",
]
