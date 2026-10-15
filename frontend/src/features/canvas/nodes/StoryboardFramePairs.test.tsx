import { render, screen, fireEvent } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { StoryboardFramePairs } from './StoryboardFramePairs';

const api = vi.hoisted(() => ({ call: vi.fn() }));
vi.mock('@/api/client', () => ({ apiCall: api.call }));
vi.mock('@/lib/url-params', () => ({ readUrl: () => ({ project: 'demo' }) }));

it('saves an explicit tail selection without substituting the adjacent cell', () => {
  const onChange = vi.fn();
  render(<StoryboardFramePairs frames={['a', 'b', 'c'].map(id => ({ id, description: id }))}
    pairs={[{ id: 'shot', first_frame_id: 'a', last_frame_id: '', description: '抬手', duration_seconds: 4 }]} onChange={onChange} />);
  fireEvent.change(screen.getByLabelText('片段 1 尾帧'), { target: { value: 'c' } });
  expect(onChange).toHaveBeenCalledWith([expect.objectContaining({ first_frame_id: 'a', last_frame_id: 'c' })]);
});

it('switching to first/last never invents an adjacent tail', () => {
  const onChange = vi.fn();
  render(<StoryboardFramePairs frames={['a', 'b'].map(id => ({ id, description: id }))}
    pairs={[{ id: 'g', first_frame_id: 'a', last_frame_id: '', description: '行走', duration_seconds: 5 }]} onChange={onChange} />);
  fireEvent.change(screen.getByLabelText('片段 1 模式'), { target: { value: 'first_last' } });
  expect(onChange).toHaveBeenCalledWith([expect.objectContaining({ mode: 'first_last', last_frame_id: '', selection_source: 'manual' })]);
});

it('shows AI reasons without overwriting manual edits and rejects stale application', async () => {
  const onChange = vi.fn();
  const frames = [{ id: 'a', description: '走出门' }];
  const pair = { id: 'g', first_frame_id: 'a', last_frame_id: '', description: '行走', duration_seconds: 5 };
  api.call.mockResolvedValueOnce({ pairs: [{ ...pair, recommendation: { mode: 'first', reason: '结束位置无需锁定' } }] });
  const view = render(<StoryboardFramePairs frames={frames} pairs={[pair]} onChange={onChange} />);
  fireEvent.click(screen.getByText('AI 推荐帧模式'));
  expect(await screen.findByText(/结束位置无需锁定/)).toBeInTheDocument();
  expect(onChange).not.toHaveBeenCalled();
  view.rerender(<StoryboardFramePairs frames={frames} pairs={[{ ...pair, description: '手动修改' }]} onChange={onChange} />);
  expect(screen.getByText('应用推荐（替换当前选择）')).toBeDisabled();
});

it('recommends four shot segments for a four-shot board instead of one group endpoint pair', async () => {
  const frames = ['s1', 's2', 's3', 's4'].map(id => ({ id, description: id, durationSeconds: 4 }));
  api.call.mockResolvedValueOnce({ pairs: [] });
  render(<StoryboardFramePairs frames={frames} pairs={[]} onChange={vi.fn()} />);
  fireEvent.click(screen.getByText('AI 推荐帧模式'));
  await screen.findByText('应用推荐（替换当前选择）');
  const options = api.call.mock.calls[api.call.mock.calls.length - 1]![1];
  expect(options.json.segments.map((s: { id: string }) => s.id)).toEqual(['s1', 's2', 's3', 's4']);
  expect(options.json.segments).toHaveLength(4);
});
