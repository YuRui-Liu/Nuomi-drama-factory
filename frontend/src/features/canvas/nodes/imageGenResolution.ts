// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import type { ImageSize } from '@/features/canvas/domain/canvasNodes';
import type { ModelOption } from '@/features/canvas/ui/ProviderModelPicker';

export const IMAGE_GEN_RESOLUTION_OPTIONS: readonly ImageSize[] = ['1K', '2K', '4K'];

export interface ImageGenResolutionSelection {
  value: string;
  supported: boolean;
  options: ImageSize[];
}

function normalizedModelName(model: Pick<ModelOption, 'apiModel'>): string {
  return model.apiModel.trim().toLowerCase().replace(/^grsai\//, '');
}

export function imageGenResolutionOptions(
  model: Pick<ModelOption, 'providerId' | 'apiModel' | 'resolutionOptions'> | null | undefined,
): ImageSize[] {
  if (!model) return [...IMAGE_GEN_RESOLUTION_OPTIONS];

  const modelName = normalizedModelName(model);
  if (model.providerId === 'grsai' && modelName === 'gpt-image-2') {
    return ['1K'];
  }

  const advertised = model.resolutionOptions?.filter(
    (value): value is ImageSize => IMAGE_GEN_RESOLUTION_OPTIONS.includes(value as ImageSize),
  );
  if (advertised && advertised.length > 0) return advertised;

  if (model.providerId === 'grsai' && modelName === 'gpt-image-2-vip') {
    return [...IMAGE_GEN_RESOLUTION_OPTIONS];
  }

  // Keep other providers on their own legacy capability defaults. In
  // particular, never infer Grsai limits from a model name used by OpenAI or
  // HuiMeng.
  return [...IMAGE_GEN_RESOLUTION_OPTIONS];
}

export function resolveImageGenResolutionSelection(
  model: Pick<ModelOption, 'providerId' | 'apiModel' | 'resolutionOptions'> | null | undefined,
  savedValue: unknown,
): ImageGenResolutionSelection {
  const options = imageGenResolutionOptions(model);
  const value = typeof savedValue === 'string' && savedValue.trim()
    ? savedValue.trim()
    : options.includes('2K') ? '2K' : options[0] ?? '1K';
  return { value, supported: options.includes(value as ImageSize), options };
}
