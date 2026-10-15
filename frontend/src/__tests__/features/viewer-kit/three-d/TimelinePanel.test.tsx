import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { TimelinePanel } from '@/features/viewer-kit/three-d/TimelinePanel';
import { emptyTimeline } from '@/features/viewer-kit/three-d/sceneTimeline';
import type { ViewerApp } from '@/features/viewer-kit/three-d/engine/viewerApp';
function fixture() {
  const viewer = {
    getTimeline: () => emptyTimeline(), setTimeline: vi.fn(), seekTimeline: vi.fn(), onMarkersChange: () => () => {},
    camera: { camera: { fov: 60 } },
    fly: { getCameraState: () => ({ azim: 10, elev: 20, distance: 5, focalPoint: [1, 2, 3] }) },
    getSelection: () => ({ kind: 'actor', index: 0 }),
    exportSceneSnapshot: () => ({ actors: [{ id: 'stable-actor', position: [2, 0, 3], yawDeg: 40, scale: [1, 1, 1] }] }),
  };
  render(<TimelinePanel viewer={viewer as unknown as ViewerApp} rotationOnly={false} />);
  return viewer;
}
describe('timeline controls', () => {
  it('scrubs the renderer and records the object under its stable ID', () => {
    const viewer = fixture();
    fireEvent.change(screen.getByLabelText('时间游标'), { target: { value: '2' } });
    expect(viewer.seekTimeline).toHaveBeenCalledWith(2, false);
    fireEvent.click(screen.getByText('记录物体关键帧'));
    expect(viewer.setTimeline.mock.calls[0][0].tracks['stable-actor'].keys[0]).toEqual({ time: 2, position: [2, 0, 3], yawDeg: 40, scale: [1, 1, 1] });
  });
  it('persists camera orientation, focal position and FOV together', () => {
    const viewer = fixture();
    fireEvent.change(screen.getByLabelText('视场角'), { target: { value: '75' } });
    fireEvent.click(screen.getByText('记录机位关键帧'));
    expect(viewer.setTimeline.mock.calls[0][0].camera[0]).toMatchObject({ time: 0, fov: 75, azim: 10, focalPoint: [1, 2, 3] });
  });
  it('adds an explicit bounded action clip rather than toggling a loop', () => {
    const viewer = fixture();
    fireEvent.change(screen.getByLabelText('片段/路径结束秒'), { target: { value: '3' } });
    fireEvent.click(screen.getByText('添加动作片段'));
    expect(viewer.setTimeline.mock.calls[0][0].tracks['stable-actor'].clips).toEqual([{ start: 0, end: 3, pose: 'walking', speed: 1 }]);
  });
});
