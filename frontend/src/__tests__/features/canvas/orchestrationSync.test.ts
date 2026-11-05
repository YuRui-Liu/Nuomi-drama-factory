import { beforeEach, describe, expect, it, vi } from 'vitest';

const mocked = vi.hoisted(() => ({
  flush: vi.fn(), apply: vi.fn(), get: vi.fn(),
  create: vi.fn(),
  capture: vi.fn(), canvas: { nodes: [] as unknown[], edges: [], viewportBookmarks: [] },
  metadata: vi.fn(), shot: vi.fn(),
}));
vi.mock('@/features/freezone/canvasSyncRuntime', () => ({
  flushFreezoneCanvasRuntime: mocked.flush, applyRemoteFreezoneCanvas: mocked.apply,
  captureFreezoneCanvasRuntime: mocked.capture,
}));
vi.mock('@/api/canvas', () => ({ getFreezoneCanvas: mocked.get, createFreezoneProductionGraph: mocked.create }));
vi.mock('@/stores/canvasStore', () => ({ useCanvasStore: { getState: () => mocked.canvas } }));
vi.mock('@/features/freezone/canvasMetadataContext', () => ({ getFreezoneCanvasMetadata: mocked.metadata }));
vi.mock('@/features/freezone/shotMetadataStore', () => ({ useShotMetadataStore: { getState: () => ({ shot: mocked.shot() }) } }));
import { refreshCanvasRunResults } from '@/features/canvas/infrastructure/orchestrationGateway';
import { refreshAgentCanvasChanges } from '@/features/canvas/infrastructure/orchestrationGateway';
import { createProductionGraph } from '@/features/canvas/infrastructure/orchestrationGateway';

describe('CLI graph refresh', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
  });
  it('adopts the complete saved graph after local edits are flushed', async () => {
    const remote = { revision: 12, nodes: [{ id: 'new-cli-node' }], edges: [{ id: 'new-cli-edge' }] };
    mocked.flush.mockResolvedValue(true);
    mocked.get.mockResolvedValue(remote);
    mocked.apply.mockReturnValue(true);
    await refreshCanvasRunResults('p', 'c');
    expect(mocked.apply).toHaveBeenCalledWith('p', 'c', remote);
    expect(mocked.flush.mock.invocationCallOrder[0]).toBeLessThan(mocked.get.mock.invocationCallOrder[0]);
  });
  it('keeps unsaved local edits and the old revision when flush conflicts', async () => {
    mocked.flush.mockResolvedValue(false);
    await refreshCanvasRunResults('p', 'c');
    expect(mocked.get).not.toHaveBeenCalled();
    expect(mocked.apply).not.toHaveBeenCalled();
  });

  it('flushes, materializes, and three-way merges the production graph into the captured canvas', async () => {
    const baseline = { revision: 4, nodes: [], edges: [] };
    const remote = {
      revision: 5,
      nodes: [{ id: 'production-compose', type: 'episodeComposeNode', data: { directorRevisionId: 'rev-1' } }],
      edges: [],
    };
    const runtime = { flush: vi.fn().mockResolvedValue(true), isCurrent: () => true };
    mocked.capture.mockReturnValue(runtime);
    mocked.get.mockResolvedValueOnce(baseline).mockResolvedValueOnce(remote);
    mocked.create.mockResolvedValue({ canvas: remote, target: 'production-compose', node_ids: ['production-compose'], submitted: false });
    mocked.canvas = { nodes: [], edges: [], viewportBookmarks: [] };
    mocked.metadata.mockReturnValue(null);
    mocked.shot.mockReturnValue({});
    mocked.apply.mockImplementation((_project, _canvas, saved, merge) => {
      mocked.canvas.nodes = merge(saved.nodes, saved.edges, mocked.canvas.nodes, []).nodes;
      return true;
    });

    const result = await createProductionGraph('p', 'c', 2, 'rev-1', 'request-1');

    expect(result.target).toBe('production-compose');
    expect(runtime.flush).toHaveBeenCalledOnce();
    expect(mocked.create).toHaveBeenCalledWith('p', 'c', {
      base_revision: 4, request_id: 'request-1', episode: 2, revision_id: 'rev-1',
    });
    expect(mocked.canvas.nodes).toMatchObject([{ id: 'production-compose' }]);
  });

  it('does not refresh or apply a created graph after its captured runtime detaches', async () => {
    const baseline = { revision: 4, nodes: [], edges: [] };
    let current = true;
    const runtime = { flush: vi.fn().mockResolvedValue(true), isCurrent: () => current };
    mocked.capture.mockReturnValue(runtime);
    mocked.get.mockResolvedValue(baseline);
    mocked.create.mockImplementation(async () => {
      current = false;
      return { canvas: { ...baseline, revision: 5 }, target: 'production-compose', node_ids: ['production-compose'], submitted: false };
    });

    await expect(createProductionGraph('p', 'c', 2, 'rev-1', 'request-1'))
      .rejects.toThrow('当前画布已切换');
    expect(mocked.get).toHaveBeenCalledOnce();
    expect(mocked.apply).not.toHaveBeenCalled();
  });

  it('applies an agent lighting edit to an existing canvas node when local inputs are unchanged', async () => {
    const baseline = {
      revision: 4,
      nodes: [{ id: 'lamp', type: 'lightNode', data: { lighting: { intensity: 1 }, title: 'Key' } }],
      edges: [],
    };
    const remote = {
      revision: 5,
      nodes: [{ id: 'lamp', type: 'lightNode', data: { lighting: { intensity: 2 }, title: 'Key' } }],
      edges: [],
    };
    mocked.canvas = {
      nodes: structuredClone(baseline.nodes), edges: [], viewportBookmarks: [],
    };
    mocked.metadata.mockReturnValue(null);
    mocked.shot.mockReturnValue({});
    mocked.capture.mockReturnValue({ isCurrent: () => true });
    mocked.get.mockResolvedValue(remote);
    mocked.apply.mockImplementation((_project, _canvas, saved, merge) => {
      mocked.canvas.nodes = merge(saved.nodes, saved.edges, mocked.canvas.nodes, []).nodes;
      return true;
    });

    const result = await refreshAgentCanvasChanges('p', 'c', baseline as never);
    expect(result.status).toBe('applied');
    expect(mocked.canvas.nodes).toMatchObject([
      { id: 'lamp', data: { lighting: { intensity: 2 }, title: 'Key' } },
    ]);
  });

  it('preserves concurrent local edits and reports a field conflict without applying the remote graph', async () => {
    const baseline = {
      revision: 4,
      nodes: [{ id: 'lamp', type: 'lightNode', data: { lighting: { intensity: 1 }, title: 'Key' } }],
      edges: [],
    };
    const remote = {
      revision: 5,
      nodes: [{ id: 'lamp', type: 'lightNode', data: { lighting: { intensity: 2 }, title: 'Key' } }],
      edges: [],
    };
    const localNodes = [{
      id: 'lamp', type: 'lightNode', data: { lighting: { intensity: 3 }, title: 'Key' },
    }];
    mocked.canvas = { nodes: localNodes, edges: [], viewportBookmarks: [] };
    mocked.metadata.mockReturnValue(null);
    mocked.shot.mockReturnValue({});
    mocked.capture.mockReturnValue({ isCurrent: () => true });
    mocked.get.mockResolvedValue(remote);

    const result = await refreshAgentCanvasChanges('p', 'c', baseline as never);
    expect(result.status).toBe('conflict');
    expect(result.status === 'conflict' && result.paths).toContain('nodes.lamp.data.lighting.intensity');
    expect(mocked.canvas.nodes).toBe(localNodes);
    expect(mocked.apply).not.toHaveBeenCalled();
  });

  it('waits for the receipt revision before marking an agent node change synchronized', async () => {
    const baseline = { revision: 4, nodes: [{ id: 'lamp', data: {} }], edges: [] };
    mocked.canvas = { nodes: structuredClone(baseline.nodes), edges: [], viewportBookmarks: [] };
    mocked.metadata.mockReturnValue(null);
    mocked.shot.mockReturnValue({});
    mocked.capture.mockReturnValue({ isCurrent: () => true });
    mocked.get.mockResolvedValue({ revision: 4, nodes: baseline.nodes, edges: [] });

    const result = await refreshAgentCanvasChanges('p', 'c', baseline as never, () => true, 5);
    expect(result.status).toBe('pending');
    expect(mocked.apply).not.toHaveBeenCalled();
  });

  it('uses the advanced baseline for consecutive agent outputs without local edits', async () => {
    const baseline = {
      revision: 4,
      nodes: [{ id: 'lamp', type: 'lightNode', data: { intensity: 1, color: 'warm' } }],
      edges: [],
    };
    const firstRemote = {
      revision: 5,
      nodes: [{ id: 'lamp', type: 'lightNode', data: { intensity: 2, color: 'warm' } }],
      edges: [],
    };
    const secondRemote = {
      revision: 6,
      nodes: [{ id: 'lamp', type: 'lightNode', data: { intensity: 2, color: 'cool' } }],
      edges: [],
    };
    mocked.canvas = { nodes: structuredClone(baseline.nodes), edges: [], viewportBookmarks: [] };
    mocked.metadata.mockReturnValue(null);
    mocked.shot.mockReturnValue({});
    mocked.capture.mockReturnValue({ isCurrent: () => true });
    mocked.get.mockResolvedValueOnce(firstRemote).mockResolvedValueOnce(secondRemote);
    mocked.apply.mockImplementation((_project, _canvas, remote, merge) => {
      const result = merge(remote.nodes, remote.edges, mocked.canvas.nodes, []);
      mocked.canvas.nodes = result.nodes;
      return true;
    });

    const first = await refreshAgentCanvasChanges('p', 'c', baseline as never);
    expect(first.status).toBe('applied');
    if (first.status !== 'applied') return;
    const second = await refreshAgentCanvasChanges('p', 'c', first.baseline as never);

    expect(second.status).toBe('applied');
    expect(mocked.canvas.nodes).toMatchObject([
      { id: 'lamp', data: { intensity: 2, color: 'cool' } },
    ]);
  });

  it('rehydrates the advanced baseline after remount before applying the next remote output', async () => {
    const owner = { user: 'alice', project: 'p', canvas: 'c', conversation: 'chat' };
    const baselineA = {
      revision: 5,
      nodes: [{ id: 'lamp', type: 'lightNode', data: { intensity: 2, color: 'warm' } }],
      edges: [],
    };
    const remoteB = {
      revision: 6,
      nodes: [{ id: 'lamp', type: 'lightNode', data: { intensity: 2, color: 'cool' } }],
      edges: [],
    };
    const { readDelegationCanvasBaseline, runOwnerKey } = await import(
      '@/features/agent-runtime/runOperation'
    );
    const archiveKey = `agent-run-intent:v1:${runOwnerKey(owner)}:archive:finished`;
    localStorage.setItem(archiveKey, JSON.stringify({
      id: 'finished', label: '灯光操作', owner, phase: 'applied',
      canvasBaseline: baselineA,
      steps: [{ kind: 'delegation', request: { request_id: 'd' }, result: { delegation_id: 'root' } }],
    }));
    const recoveredBaseline = readDelegationCanvasBaseline(owner, 'root');
    expect(recoveredBaseline).toEqual(baselineA);

    mocked.canvas = { nodes: structuredClone(baselineA.nodes), edges: [], viewportBookmarks: [] };
    mocked.metadata.mockReturnValue(null);
    mocked.shot.mockReturnValue({});
    mocked.capture.mockReturnValue({ isCurrent: () => true });
    mocked.get.mockResolvedValue(remoteB);
    mocked.apply.mockImplementation((_project, _canvas, saved, merge) => {
      mocked.canvas.nodes = merge(saved.nodes, saved.edges, mocked.canvas.nodes, []).nodes;
      return true;
    });

    const result = await refreshAgentCanvasChanges('p', 'c', recoveredBaseline as never);

    expect(result.status).toBe('applied');
    expect(mocked.canvas.nodes).toMatchObject([
      { id: 'lamp', data: { intensity: 2, color: 'cool' } },
    ]);
  });
});
