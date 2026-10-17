import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import { ProjectSettingsMenu } from '@/features/creation-workspace/ProjectSettingsEntry';
import { useAuthStore } from '@/stores/auth-store';

const routerState = vi.hoisted(() => ({ location: { pathname: '/projects/p/freezone', href: '/projects/p/freezone' } }));
const runtimeState = vi.hoisted(() => ({
  current: null as null | { flush: ReturnType<typeof vi.fn>; isCurrent: ReturnType<typeof vi.fn> },
}));

vi.mock('@tanstack/react-router', () => ({
  useRouter: () => ({ state: routerState }),
  useRouterState: ({ select }: { select: (value: { location: typeof routerState.location }) => unknown }) =>
    select({ location: routerState.location }),
}));
vi.mock('@/features/freezone/canvasSyncRuntime', () => ({
  captureFreezoneCanvasRuntime: () => runtimeState.current,
}));
vi.mock('@/features/direction-references/DirectionReferencePanel', () => ({
  DirectionReferencePanel: () => <section>默认方向内容</section>,
}));
vi.mock('@/features/agent-configuration/AgentConfigurationPanel', () => ({
  AgentConfigurationPanel: () => <section>项目 Agent 内容</section>,
}));
vi.mock('@/features/creative-settings/CreativeSettingsPanel', () => ({
  CreativeSettingsPanel: () => <section>创作对象与约定内容</section>,
}));
vi.mock('@/features/template-library/TemplateLibraryPanel', () => ({
  TemplateLibraryPanel: () => <section>创作模板内容</section>,
}));

beforeEach(() => {
  useAuthStore.setState({ username: 'alice' });
  runtimeState.current = { flush: vi.fn(async () => true), isCurrent: vi.fn(() => true) };
});

it('opens one settings dialog with all four categories inside the workspace', async () => {
  render(<ProjectSettingsMenu project="p" canvasId="canvas-1" />);
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();

  fireEvent.click(screen.getByRole('button', { name: '项目设置' }));

  expect(await screen.findByRole('dialog')).toBeInTheDocument();
  expect(screen.getAllByRole('dialog')).toHaveLength(1);
  expect(screen.getByRole('tab', { name: /项目 Agent/ })).toBeInTheDocument();
  expect(screen.getByRole('tab', { name: /默认风格与手法/ })).toBeInTheDocument();
  expect(screen.getByRole('tab', { name: /创作对象与约定/ })).toBeInTheDocument();
  expect(screen.getByRole('tab', { name: /创作模板/ })).toBeInTheDocument();
  expect(screen.getByText('默认方向内容')).toBeInTheDocument();
});

it('does not open settings when the registered canvas runtime is unavailable', () => {
  runtimeState.current = null;
  render(<ProjectSettingsMenu project="p" canvasId="canvas-1" />);

  fireEvent.click(screen.getByRole('button', { name: '项目设置' }));

  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(screen.getByRole('alert')).toHaveTextContent('画布同步尚未就绪');
});

it('keeps the entry disabled without a usable canvas', () => {
  render(<ProjectSettingsMenu project="p" />);
  expect(screen.getByRole('button', { name: '项目设置' })).toBeDisabled();
});

it('closes when the captured runtime instance becomes stale without a route render', async () => {
  const view = render(<ProjectSettingsMenu project="p" canvasId="canvas-1" />);

  fireEvent.click(screen.getByRole('button', { name: '项目设置' }));
  expect(await screen.findByRole('dialog')).toBeInTheDocument();

  runtimeState.current!.isCurrent.mockReturnValue(false);
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument(), { timeout: 1_500 });
  view.unmount();
});
