import { expect, it, vi } from 'vitest';
import { fetchFreezoneImageModels } from '@/api/ops';
import { getModelProvider } from '@/features/canvas/models';

vi.mock('@/api/client', () => ({ apiCall: vi.fn(async () => [{
  id: 'gpt-image-2', providerId: 'grsai-main', provider: 'grsai', apiModel: 'gpt-image-2', label: 'gpt-image-2',
}]) }));

it('uses the executable provider instead of the credential binding ID', async () => {
  const models = await fetchFreezoneImageModels('p');
  expect(models[0].providerId).toBe('grsai');
});
it('keeps GrsAI provider identity in the model menu', () => {
  expect(getModelProvider('grsai')).toMatchObject({ id: 'grsai', label: 'GrsAI' });
});
