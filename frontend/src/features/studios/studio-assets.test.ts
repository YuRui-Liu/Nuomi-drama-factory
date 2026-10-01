import { expect, it } from 'vitest';
import { previsCanvasAsset } from './studio-assets';

it('makes a project-bound real asset while retaining source node context', () => {
  const asset = previsCanvasAsset('项目', {id:'a'.repeat(64),kind:'frame',created_at:'2026-09-28T00:00:00Z',source:{id:'node-1'}});
  expect(asset?.kind).toBe('image');
  expect(asset?.url).toContain('/projects/%E9%A1%B9%E7%9B%AE/studios/previs-tools/outputs/');
  expect(asset?.nodeId).toBe('node-1');
  expect(previsCanvasAsset('p',{id:'bad/../id',kind:'frame'})).toBeNull();
});
