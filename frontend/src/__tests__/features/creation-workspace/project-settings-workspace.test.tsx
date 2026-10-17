import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { beforeEach, expect, it, vi } from 'vitest';
import { ProjectSettingsWorkspace } from '@/features/creation-workspace/ProjectSettingsWorkspace';

const guardState = vi.hoisted(() => ({
  dirty: false,
  saveResult: false,
  discard: vi.fn(),
}));

vi.mock('@/features/direction-references/DirectionReferencePanel', () => ({
  DirectionReferencePanel: ({ draftGuardRef }: { draftGuardRef: { current: unknown } }) => {
    draftGuardRef.current = {
      isDirty: () => guardState.dirty,
      save: async () => guardState.saveResult,
      discard: () => {
        guardState.discard();
        guardState.dirty = false;
      },
    };
    return <section data-testid="direction-panel"><button onClick={() => { guardState.dirty = true; }}>编辑方向</button></section>;
  },
}));
vi.mock('@/features/agent-configuration/AgentConfigurationPanel', () => ({
  AgentConfigurationPanel: () => <section data-testid="agent-panel">项目 Agent 内容</section>,
}));
vi.mock('@/features/creative-settings/CreativeSettingsPanel', () => ({
  CreativeSettingsPanel: () => <section data-testid="creative-panel">创作对象与约定内容</section>,
}));
vi.mock('@/features/template-library/TemplateLibraryPanel', () => ({
  TemplateLibraryPanel: () => <section data-testid="template-panel">创作模板内容</section>,
}));
vi.mock('@/components/ui/dialog', () => ({
  DialogTitle: ({ children }: { children: ReactNode }) => <h1>{children}</h1>,
  DialogDescription: ({ children }: { children: ReactNode }) => <p>{children}</p>,
}));

const context = {
  instance: 'workspace-1',
  user: 'alice',
  project: 'project-1',
  canvasId: 'canvas-1',
  runtime: { flush: vi.fn(async () => true), isCurrent: vi.fn(() => true) },
};

beforeEach(() => {
  guardState.dirty = false;
  guardState.saveResult = false;
  guardState.discard.mockClear();
});

it('shows each settings category in the same workspace content region', () => {
  render(<ProjectSettingsWorkspace context={context} isCurrent={() => true} onClose={vi.fn()} />);

  expect(screen.getByTestId('direction-panel')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('tab', { name: /项目 Agent/ }));
  expect(screen.getByTestId('agent-panel')).toBeInTheDocument();
  expect(screen.queryByTestId('direction-panel')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('tab', { name: /创作对象与约定/ }));
  expect(screen.getByTestId('creative-panel')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('tab', { name: /创作模板/ }));
  expect(screen.getByTestId('template-panel')).toBeInTheDocument();
});

it('keeps the current category when draft saving fails and only discards on request', async () => {
  render(<ProjectSettingsWorkspace context={context} isCurrent={() => true} onClose={vi.fn()} />);

  fireEvent.click(screen.getByRole('button', { name: '编辑方向' }));
  fireEvent.click(screen.getByRole('tab', { name: /项目 Agent/ }));
  expect(screen.getByRole('alertdialog')).toBeInTheDocument();

  fireEvent.click(screen.getByRole('button', { name: '保存草稿' }));
  await waitFor(() => expect(screen.getByTestId('direction-panel')).toBeInTheDocument());
  expect(screen.getByRole('alert')).toHaveTextContent('草稿未能保存');

  fireEvent.click(screen.getByRole('button', { name: '放弃更改' }));
  expect(guardState.discard).toHaveBeenCalledOnce();
  expect(screen.getByTestId('agent-panel')).toBeInTheDocument();
});

it('switches categories after the active draft has been saved', async () => {
  guardState.saveResult = true;
  render(<ProjectSettingsWorkspace context={context} isCurrent={() => true} onClose={vi.fn()} />);

  fireEvent.click(screen.getByRole('button', { name: '编辑方向' }));
  fireEvent.click(screen.getByRole('tab', { name: /项目 Agent/ }));
  fireEvent.click(screen.getByRole('button', { name: '保存草稿' }));

  expect(await screen.findByTestId('agent-panel')).toBeInTheDocument();
  expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument();
});

it('closes a workspace when its captured project or canvas context is no longer current', async () => {
  const onClose = vi.fn();
  const view = render(
    <ProjectSettingsWorkspace context={context} isCurrent={() => true} onClose={onClose} />,
  );

  view.rerender(
    <ProjectSettingsWorkspace context={context} isCurrent={() => false} onClose={onClose} />,
  );
  await waitFor(() => expect(onClose).toHaveBeenCalledOnce());
});
