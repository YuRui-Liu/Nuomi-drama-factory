import { expect, it } from 'vitest';
import { buildSavePayload } from '@/features/freezone/canvasSyncCore';

it('preserves grid provenance and pending HD tasks across the canvas save boundary', () => {
  const binding = { project: 'project', episode: 1, groupId: 'group', directorRevisionId: 'director-v1', shotIds: ['shot-1'] };
  const origin = {
    gridNodeId: 'grid', gridResultId: 'result-v2', gridResultRevision: 2, binding,
    cellIndex: 0, shotId: 'shot-1', crop: { x: 0, y: 0, width: 301, height: 201 },
  };
  const nodes = [{ id: 'grid', type: 'imageGenNode', data: {
    gridMode: 'narrative-group', gridBinding: binding,
    gridRuns: [{ runId: 'run', batchId: 'batch-1', resultId: 'result-v2', resultRevision: 2,
      taskKey: 'task', taskScope: 'group_render_r2', status: 'completed', imageUrl: '/grid.png',
      rows: 1, cols: 1, cellMappings: [{ cellIndex: 0, shotId: 'shot-1' }] }],
  } }, { id: 'frame', type: 'imageNode', data: {
    imageUrl: '/crop.png', gridFrameOrigin: origin,
    gridFrameRecord: { origin, cropUrl: '/crop.png', candidates: [{
      candidateId: 'hd-1', taskKey: 'hd-task-1', status: 'running', model: 'configured-model', size: '2K',
    }] },
  } }];
  const saved = buildSavePayload({
    canvasId: 'workspace', nodes, edges: [], envelope: {}, metadata: null,
    clientSaveId: 'save', baseRevision: 3,
    decision: { kind: 'send', saveSource: 'autosave', allowEmptyOverwrite: false },
  });
  const reloaded = JSON.parse(JSON.stringify(saved));
  expect(reloaded.nodes).toEqual(nodes);
  expect(reloaded.base_revision).toBe(3);
});
