import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { CanvasReferences, toCanvasReferences, CANVAS_REFERENCE_EVENT } from './canvas-references';

const mocks = vi.hoisted(() => ({flush: vi.fn(), focus: vi.fn(), select: vi.fn(), nodes: [
  {id: 'source', type: 'imageNode', data: {displayName: '庭院'}},
  {id: 'result', type: 'imageNode', data: {displayName: '庭院候选', chatOrigin: {node_id: 'source', canvas_id: 'board'}}},
]}));
vi.mock('@/stores/canvasStore', () => ({useCanvasStore: Object.assign((selector: (state: unknown) => unknown) => selector({nodes: mocks.nodes}), {getState: () => ({setSelectedNode: mocks.select, requestFocusNode: mocks.focus})})}));
vi.mock('@/lib/api', () => ({api: {get: () => ({json: async () => ({data: {assets: [{id: 'audio:one', label: '低沉旁白'}]}})})}}));
vi.mock('@/features/freezone/canvasSyncRuntime', () => ({flushFreezoneCanvasRuntime: mocks.flush}));

describe('canvas references', () => {
  beforeEach(() => {vi.clearAllMocks(); mocks.flush.mockResolvedValue(true);});
  it('keeps reference order and groups cross-canvas assets without duplication', () => {
    expect(toCanvasReferences([
      {id:'one',kind:'node',canvas:'a',label:'主目标'},
      {id:'audio',kind:'asset',canvas:'b',label:'音频'},
      {id:'one',kind:'node',canvas:'a',label:'重复'},
    ])).toEqual([{canvas_id:'a',node_ids:['one'],asset_ids:[]},{canvas_id:'b',node_ids:[],asset_ids:['audio']}]);
  });
  it('blocks unsaved node references and accepts saved nodes', async () => {
    const change = vi.fn();
    mocks.flush.mockResolvedValueOnce(false);
    render(<CanvasReferences project="p" canvas="board" items={[]} onChange={change} />);
    fireEvent.click(screen.getByText('@ 引用节点或资产'));
    fireEvent.click(screen.getByText('节点 · 庭院'));
    await screen.findByText('请先保存画布并解决冲突，再引用节点');
    expect(change).not.toHaveBeenCalled();
    fireEvent.click(screen.getByText('节点 · 庭院'));
    await waitFor(() => expect(change).toHaveBeenCalledWith([{id:'source',label:'庭院',kind:'node',canvas:'board'}]));
  });
  it('locates the associated candidate and does not consume another conversation’s global event', () => {
    const change = vi.fn();
    render(<CanvasReferences project="p" canvas="board" items={[{id:'source',label:'庭院',kind:'node',canvas:'board'}]} onChange={change} />);
    fireEvent.click(screen.getByText('定位结果 · 庭院候选'));
    expect(mocks.focus).toHaveBeenCalledWith('result');
    window.dispatchEvent(new CustomEvent(CANVAS_REFERENCE_EVENT, {detail:{id:'other',label:'其他会话',kind:'node',canvas:'board'}}));
    expect(change).not.toHaveBeenCalled();
    fireEvent.click(screen.getByLabelText('移除引用 庭院'));
    expect(change).toHaveBeenCalledWith([]);
  });
});
