import { describe, expect, it } from 'vitest';

import {
  imageGenResolutionOptions,
  resolveImageGenResolutionSelection,
} from '@/features/canvas/nodes/imageGenResolution';
import { catalogImageDefinition } from '@/features/canvas/models/catalogDefinition';

describe('image generation resolution capability', () => {
  it('limits standard Grsai GPT Image 2 to 1K', () => {
    expect(imageGenResolutionOptions({ providerId: 'grsai', apiModel: 'gpt-image-2' })).toEqual(['1K']);
    expect(resolveImageGenResolutionSelection(
      { providerId: 'grsai', apiModel: 'grsai/gpt-image-2' },
      undefined,
    )).toMatchObject({ value: '1K', supported: true, options: ['1K'] });
  });

  it('preserves the VIP catalog tiers and validates saved 2K values', () => {
    const model = { providerId: 'grsai' as const, apiModel: 'gpt-image-2-vip' };
    expect(imageGenResolutionOptions(model)).toEqual(['1K', '2K', '4K']);
    expect(resolveImageGenResolutionSelection(model, '2K')).toMatchObject({
      value: '2K',
      supported: true,
    });
  });

  it('keeps a saved unsupported size visible instead of silently changing it', () => {
    expect(resolveImageGenResolutionSelection(
      { providerId: 'grsai', apiModel: 'gpt-image-2' },
      '2K',
    )).toMatchObject({ value: '2K', supported: false, options: ['1K'] });
  });

  it('does not apply Grsai limits to similarly named models from other providers', () => {
    expect(imageGenResolutionOptions({ providerId: 'openai', apiModel: 'gpt-image-2' })).toEqual(['1K', '2K', '4K']);
    expect(imageGenResolutionOptions({
      providerId: 'huimeng',
      apiModel: 'gpt-image-2',
      resolutionOptions: ['1K', '2K'],
    })).toEqual(['1K', '2K']);
    expect(catalogImageDefinition({
      id: 'openai/gpt-image-2', providerId: 'openai', apiModel: 'gpt-image-2', label: 'GPT Image 2',
    }).resolutions.map((option) => option.value)).toEqual(['1K', '2K', '4K']);
    expect(catalogImageDefinition({
      id: 'grsai/gpt-image-2', providerId: 'grsai', apiModel: 'gpt-image-2', label: 'GPT Image 2',
    }).resolutions.map((option) => option.value)).toEqual(['1K']);
  });

  it('uses provider-advertised VIP tiers when present', () => {
    expect(imageGenResolutionOptions({
      providerId: 'grsai',
      apiModel: 'gpt-image-2-vip',
      resolutionOptions: ['1K', '2K'],
    })).toEqual(['1K', '2K']);
  });
});
