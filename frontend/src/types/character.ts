// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
export interface CharacterVoiceSample {
  path: string;
  sha256?: string;
  updated_at?: string;
}

export type CharacterVoiceSlotId =
  | "default"
  | "child"
  | "youth"
  | "middle"
  | "elder";

export interface CharacterVoiceSlot {
  slot: CharacterVoiceSlotId | string;
  label: string;
  path: string;
  url: string;
  sha256: string;
  updated_at: string;
  inherited_from_default: boolean;
  required: boolean;
}

export interface CharacterVoiceSamples {
  character: string;
  slots: CharacterVoiceSlot[];
}

export type CharacterAssetKind =
  | "portrait"
  | "identity"
  | "identity_costume"
  | "identity_portrait";

export interface CharacterAssetHistoryEntry {
  history_id: string;
  filename: string;
  url: string;
  created_at?: string;
  bytes?: number;
}

export interface CharacterAssetHistory {
  kind: CharacterAssetKind;
  identity_id?: string;
  current_url?: string | null;
  entries: CharacterAssetHistoryEntry[];
}

export interface CharacterAssetRestoreResult {
  kind: CharacterAssetKind;
  identity_id?: string;
  restored: boolean;
  url: string;
  backup_history_id?: string;
}

export interface Character {
  name: string;
  aliases?: string[];
  description?: string;
  role?: string;
  gender?: string;
  age_group?: string;
  is_main?: boolean;
  face_prompt?: string;
  body_type?: string;
  reference_audio_path?: string;
  reference_audio_url?: string;
  reference_audio_sha256?: string;
  reference_audio_updated_at?: string;
  voice_samples_by_age_group?: Record<string, CharacterVoiceSample>;
  portrait_path?: string | null;
  portrait_url?: string | null;
  history_url?: string;
  restore_url?: string;
  extraction_locked?: boolean;
}

export interface CharacterVisualFact {
  fact_id: string;
  field: string;
  value: string;
  source_span: { start_line: number; end_line: number };
  evidence: string;
  assertion: "explicit" | "inferred";
  trust: "trusted" | "legacy_untrusted";
}

export interface CharacterDesignProposal {
  proposal_id: string;
  title: string;
  rationale: string;
  recommended?: boolean;
  identity_anchors?: string[];
  asymmetry_detail?: string | null;
  quality_issues?: string[];
  face_shape?: string | null;
  facial_features?: string[];
  hair_style?: string | null;
  body_type?: string | null;
  distinctive_features?: string[];
  outfit_states?: Record<string, string>;
}

export interface CharacterVisualBible {
  revision_id: string;
  status: "draft" | "confirmed" | "superseded";
  face_shape?: string | null;
  facial_features: string[];
  hair_style?: string | null;
  body_type?: string | null;
  distinctive_features: string[];
  outfit_states: Record<string, string>;
  identity_anchors?: string[];
  asymmetry_detail?: string | null;
  quality_issues?: string[];
}

export interface CharacterVisualWorkspace {
  character_id: string;
  profile: {
    character_id: string;
    name: string;
    biography: string;
    facts: CharacterVisualFact[];
  };
  design_proposals: CharacterDesignProposal[];
  selected_proposal_id?: string | null;
  visual_bible?: CharacterVisualBible | null;
  legacy_fields: Array<{
    field: string;
    value: string;
    trust: "legacy_untrusted";
    allowed_for_generation: false;
  }>;
}

export interface CharacterVisualWorkspaceUpdate {
  selected_proposal_id?: string | null;
}

export interface Identity {
  identity_id: string;
  identity_name: string;
  appearance_details?: string;
  face_prompt?: string;
  age_group?: string;
  body_type?: string;
  reference_audio_path?: string;
  reference_audio_url?: string;
  reference_audio_sha256?: string;
  reference_audio_updated_at?: string;
  image_path?: string | null;
  image_url?: string | null;
  costume_image_path?: string | null;
  costume_image_url?: string | null;
  portrait_image_path?: string | null;
  portrait_image_url?: string | null;
  history_url?: string;
  restore_url?: string;
  costume_history_url?: string;
  portrait_history_url?: string;
}

export interface IdentityAttempts {
  image_attempts: number;
  portrait_attempts: number;
}
