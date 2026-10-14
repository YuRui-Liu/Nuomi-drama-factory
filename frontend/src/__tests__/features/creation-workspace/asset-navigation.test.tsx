import { beforeEach, describe, expect, it, vi } from 'vitest';
import { listCreationCanvases } from '@/api/creation';
import { getAppRouter } from '@/lib/app-router';
import { readAssetReturn, returnAssetToWorkspace } from '@/features/creation-workspace/assetReferenceNavigation';
vi.mock('@/api/creation', () => ({ listCreationCanvases: vi.fn() }));
vi.mock('@/lib/app-router', () => ({ getAppRouter: vi.fn() }));
describe('asset workspace navigation', () => {
  beforeEach(() => { vi.clearAllMocks(); sessionStorage.clear(); });
  const intent = { project: 'p', canvasId: 'ep2', entry: { title: 'Lin' }, media: { role: 'portrait' } } as never;
  it('persists the exact chosen target and never infers a selected node', async () => {
    const navigate = vi.fn();
    vi.mocked(getAppRouter).mockReturnValue({ state: { location: {} }, navigate } as never);
    vi.mocked(listCreationCanvases).mockResolvedValue([{ canvas_id: 'ep2', episode_id: 'episode2', kind: 'episode', title: '第二集', status: 'applied' }]);
    await returnAssetToWorkspace(intent);
    expect(readAssetReturn('p', 'ep2')).toEqual(intent);
    expect(readAssetReturn('p', 'ep2')).not.toHaveProperty('targetNodeId');
    expect(navigate).toHaveBeenCalledWith(expect.objectContaining({ search: { workspace: 'ep2' } }));
  });
  it('rejects an absent workspace and isolates leave-and-return navigation objects', async () => {
    const router = { state: { location: { pathname: '/same' } }, navigate: vi.fn() };
    vi.mocked(getAppRouter).mockReturnValue(router as never);
    vi.mocked(listCreationCanvases).mockResolvedValue([]);
    await expect(returnAssetToWorkspace(intent)).rejects.toThrow();
    vi.mocked(listCreationCanvases).mockImplementation(async () => { router.state.location = { pathname: '/same' }; return []; });
    await returnAssetToWorkspace(intent);
    expect(router.navigate).not.toHaveBeenCalled();
    expect(readAssetReturn('p', 'ep2')).toBeNull();
  });
});
