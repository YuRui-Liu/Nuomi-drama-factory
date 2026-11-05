import { render, screen } from '@testing-library/react';
import { expect, it } from 'vitest';
import { GroupVideoKeyframeStatus, type GroupKeyframes } from '@/features/canvas/nodes/GroupVideoKeyframes';

const data: GroupKeyframes = { director_revision_id: 'current', revision: 2, stale: false, shot_ids: ['S03'], bindings: [{ shot_id: 'S03', first_frame: { path: 'start.png', sha256: 'a' }, last_frame: { path: 'end.png', sha256: 'b' } }] };

it('shows the actual bound pair independently of a storyboard contact sheet', () => {
  render(<GroupVideoKeyframeStatus data={data} revision="current" project="project" />);
  expect(screen.getByText('实际视频首尾帧 · 已绑定 1/1 镜')).toBeInTheDocument();
  expect(screen.getByAltText('S03 实际尾帧')).toHaveAttribute('src', expect.stringContaining('/end.png'));
});

it('does not report a missing last frame as a complete pair', () => {
  render(<GroupVideoKeyframeStatus data={{ ...data, bindings: [{ shot_id: 'S03', first_frame: data.bindings[0].first_frame }] }} revision="current" project="project" />);
  expect(screen.getByText('实际视频首尾帧 · 已绑定 0/1 镜')).toBeInTheDocument();
  expect(screen.getByText('未绑定')).toBeInTheDocument();
});

it('does not present stale director bindings as current', () => {
  render(<GroupVideoKeyframeStatus data={data} revision="new-revision" project="project" />);
  expect(screen.getByRole('status')).toHaveTextContent('旧导演版本');
  expect(screen.queryByAltText('S03 实际首帧')).not.toBeInTheDocument();
});
