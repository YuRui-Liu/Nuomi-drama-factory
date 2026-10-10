import type { ModelOption } from '@/features/canvas/ui/ProviderModelPicker';
import type { ImageModelDefinition } from './types';

/** Adapt the project's executable catalog to the canvas parameter controls. */
export function catalogImageDefinition(model: ModelOption): ImageModelDefinition {
  const sizes = model.resolutionOptions?.length ? model.resolutionOptions
    : model.apiModel === 'gpt-image-2' ? ['1K'] : ['1K', '2K', '4K'];
  return {
    id: model.id, mediaType: 'image', displayName: model.label,
    providerId: model.providerId, description: '', eta: '',
    defaultAspectRatio: '16:9', defaultResolution: sizes[0],
    aspectRatios: ['1:1', '16:9', '9:16', '4:3', '3:4', '3:2', '2:3', '21:9'].map(value => ({ value, label: value })),
    resolutions: sizes.map(value => ({ value, label: value })),
    resolveRequest: () => ({ requestModel: model.apiModel, modeLabel: '生成' }),
  };
}
