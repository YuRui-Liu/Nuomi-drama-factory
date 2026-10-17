// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab

/** Shared director portrait for the launcher and assistant messages. */
export const AI_AVATAR_PATH = "/images/avatar-nuomi-director.png";

export function loadAiAvatarUrl(): Promise<string> {
  return Promise.resolve(AI_AVATAR_PATH);
}

export function useAiAvatarUrl(): string {
  return AI_AVATAR_PATH;
}
