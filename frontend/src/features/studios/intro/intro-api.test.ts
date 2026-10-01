import { expect, it, vi } from 'vitest';
import { introCreditCost } from './intro-api';

const mock = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock('@/lib/api', () => ({ api: { get: mock.get } }));
vi.mock('@/lib/api-errors', () => ({ jsonWithBackendError: async (response: unknown) => response }));

it('quotes the actual catalog model instead of treating it as a legacy image selection', async () => {
  mock.get.mockReturnValue({ data: { cost: 12, display: '12' } });
  const signal = new AbortController().signal;
  await expect(introCreditCost('gpt-image-2', '2K', 'high', signal)).resolves.toEqual({cost:12, display:'12'});
  expect(mock.get).toHaveBeenCalledWith('api/v1/generation-credit-cost', expect.objectContaining({
    searchParams: {kind:'model', value:'gpt-image-2', surface:'canvas', params:JSON.stringify({size:'2K', quality:'high'}), quantity:'1'},
    signal,
  }));
});
