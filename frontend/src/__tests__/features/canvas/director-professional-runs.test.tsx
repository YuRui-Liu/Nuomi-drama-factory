import { render, screen } from '@testing-library/react';
import { expect, it } from 'vitest';
import { DirectorProfessionalRuns } from '@/features/canvas/production/workspace/DirectorProfessionalRuns';

it('renders only dispatched specialists with their actual scoped outputs and failures', () => {
  render(<DirectorProfessionalRuns decision="雨夜门厅需要摄影师确认机位连续性。" runs={[
    { run_id: 'camera-run', role_id: 'builtin:cinematographer', profile_name: '摄影师',
      profile_revision: 1, group_id: 'hallway', shot_ids: ['shot-2'], status: 'completed',
      task: '检查门厅内外视线方向', reason: '人物转身不能越轴',
      output: { summary: '镜头保持人物左侧机位。', contributions: [
        { shot_id: 'shot-2', camera_side: '人物左侧', camera_motion: '缓慢推进' },
      ] } },
    { run_id: 'light-run', profile_name: '灯光师', profile_revision: 1,
      group_id: 'hallway', shot_ids: ['shot-2'], status: 'unknown',
      task: '设计旧灯点亮后的光线变化', error_code: 'submission_unknown' },
  ]} />);
  expect(screen.getByText('摄影师 · 已完成')).toBeInTheDocument();
  expect(screen.getByText('镜头保持人物左侧机位。')).toBeInTheDocument();
  expect(screen.getAllByText(/叙事组 hallway · 镜头 shot-2 · 岗位版本 1/)).toHaveLength(2);
  expect(screen.getByText('灯光师 · 结果待确认')).toBeInTheDocument();
  expect(screen.getByRole('alert')).toHaveTextContent('submission_unknown');
  expect(screen.queryByText(/分镜师/)).not.toBeInTheDocument();
});

it('does not manufacture a collaboration section without evidence', () => {
  const { container } = render(<DirectorProfessionalRuns runs={undefined} decision={undefined} />);
  expect(container).toBeEmptyDOMElement();
});
