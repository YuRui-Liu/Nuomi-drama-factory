import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { CanvasWorkspaceSwitcher } from '@/features/creation-workspace/CanvasWorkspaceSwitcher';
import type { CreationCanvas } from '@/features/creation-workspace/contracts';

const canvases: CreationCanvas[] = [
  { canvas_id: 'concept-1', kind: 'concept', episode_id: null, title: '雨夜构思', status: 'applied' },
  { canvas_id: 'episode-1', kind: 'episode', episode_id: 'episode-1', title: '第一集', status: 'applied' },
  { canvas_id: 'free-1', kind: 'free', episode_id: null, title: '自由草稿', status: 'applied' },
];

describe('CanvasWorkspaceSwitcher', () => {
  it('shows only the current canvas button until opened', () => {
    render(<CanvasWorkspaceSwitcher canvases={canvases} selectedId="concept-1" onSelect={vi.fn()} onCreate={vi.fn().mockResolvedValue(true)} pending={false} />);

    expect(screen.getByRole('button', { name: '雨夜构思' })).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByRole('textbox', { name: '画布名称' })).not.toBeInTheDocument();
    expect(screen.queryByRole('textbox', { name: '搜索画布' })).not.toBeInTheDocument();
  });

  it('opens a searchable list and only renders groups with matching canvases', async () => {
    const user = userEvent.setup();
    render(<CanvasWorkspaceSwitcher canvases={canvases} selectedId="concept-1" onSelect={vi.fn()} onCreate={vi.fn().mockResolvedValue(true)} pending={false} />);

    await user.click(screen.getByRole('button', { name: '雨夜构思' }));
    expect(screen.getByRole('textbox', { name: '搜索画布' })).toBeVisible();
    expect(screen.getByRole('button', { name: '构思画布' })).toBeVisible();
    expect(screen.getByRole('button', { name: '分集画布' })).toBeVisible();
    expect(screen.getByRole('button', { name: '自由画布' })).toBeVisible();

    await user.type(screen.getByRole('textbox', { name: '搜索画布' }), '自由');
    expect(screen.getByRole('button', { name: '自由画布' })).toBeVisible();
    expect(screen.queryByRole('button', { name: '构思画布' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '分集画布' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '自由草稿' })).toBeVisible();

    const search = screen.getByRole('textbox', { name: '搜索画布' });
    await user.clear(search);
    await user.type(search, '不存在');
    expect(screen.getByRole('button', { name: '清除搜索' })).toBeVisible();
    expect(screen.getByRole('button', { name: '新建画布' })).toBeVisible();
    await user.click(screen.getByRole('button', { name: '清除搜索' }));
    expect(screen.getByRole('button', { name: '构思画布' })).toBeVisible();
  });

  it('selects a canvas and closes the list with focus returned to the switcher', async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();
    render(<CanvasWorkspaceSwitcher canvases={canvases} selectedId="concept-1" onSelect={onSelect} onCreate={vi.fn().mockResolvedValue(true)} pending={false} />);

    const trigger = screen.getByRole('button', { name: '雨夜构思' });
    await user.click(trigger);
    await user.click(screen.getByRole('button', { name: '自由草稿' }));

    expect(onSelect).toHaveBeenCalledWith('free-1');
    expect(screen.queryByRole('textbox', { name: '搜索画布' })).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });

  it('preserves the draft type and name when creation fails', async () => {
    const user = userEvent.setup();
    const onCreate = vi.fn().mockResolvedValue(false);
    render(<CanvasWorkspaceSwitcher canvases={canvases} selectedId="episode-1" onSelect={vi.fn()} onCreate={onCreate} pending={false} />);

    await user.click(screen.getByRole('button', { name: '第一集' }));
    expect(screen.queryByRole('textbox', { name: '画布名称' })).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: '新建画布' }));
    const title = screen.getByRole('textbox', { name: '画布名称' });
    const kind = screen.getByRole('combobox', { name: '画布类型' });
    await user.type(title, '雨夜分集');
    await user.selectOptions(kind, 'free');
    await user.click(screen.getByRole('button', { name: '创建并打开' }));

    await waitFor(() => expect(onCreate).toHaveBeenCalledWith('free', '雨夜分集'));
    expect(title).toHaveValue('雨夜分集');
    expect(kind).toHaveValue('free');
    expect(screen.getByRole('dialog')).toBeVisible();
  });

  it('cancels without creating or switching and restores focus to the create trigger', async () => {
    const user = userEvent.setup();
    const onCreate = vi.fn().mockResolvedValue(true);
    const onSelect = vi.fn();
    render(<CanvasWorkspaceSwitcher canvases={canvases} selectedId="concept-1" onSelect={onSelect} onCreate={onCreate} pending={false} />);

    const switcherTrigger = screen.getByRole('button', { name: '雨夜构思' });
    await user.click(switcherTrigger);
    const createTrigger = screen.getByRole('button', { name: '新建画布' });
    await user.click(createTrigger);
    await user.click(screen.getByRole('button', { name: '取消' }));

    expect(onCreate).not.toHaveBeenCalled();
    expect(onSelect).not.toHaveBeenCalled();
    expect(switcherTrigger).toBeVisible();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    await waitFor(() => expect(createTrigger).toHaveFocus());
  });

  it('keeps the create action disabled for a blank name', async () => {
    const user = userEvent.setup();
    const onCreate = vi.fn().mockResolvedValue(true);
    render(<CanvasWorkspaceSwitcher canvases={[]} onSelect={vi.fn()} onCreate={onCreate} pending={false} />);

    await user.click(screen.getByRole('button', { name: '选择或新建画布' }));
    await user.click(screen.getByRole('button', { name: '新建画布' }));
    const submit = screen.getByRole('button', { name: '创建并打开' });
    expect(submit).toBeDisabled();
    fireEvent.submit(screen.getByRole('textbox', { name: '画布名称' }).closest('form')!);
    expect(onCreate).not.toHaveBeenCalled();
  });

  it('closes the list on Escape and outside click, returning focus to the trigger', async () => {
    const user = userEvent.setup();
    render(<><CanvasWorkspaceSwitcher canvases={canvases} selectedId="concept-1" onSelect={vi.fn()} onCreate={vi.fn().mockResolvedValue(true)} pending={false} /><button type="button">外部控件</button></>);
    const trigger = screen.getByRole('button', { name: '雨夜构思' });

    await user.click(trigger);
    await user.keyboard('{Escape}');
    expect(screen.queryByRole('textbox', { name: '搜索画布' })).not.toBeInTheDocument();
    await waitFor(() => expect(trigger).toHaveFocus());

    await user.click(trigger);
    await user.click(screen.getByRole('button', { name: '外部控件' }));
    expect(screen.queryByRole('textbox', { name: '搜索画布' })).not.toBeInTheDocument();
    await waitFor(() => expect(trigger).toHaveFocus());
  });

  it('does not send duplicate requests while creation is pending', async () => {
    const user = userEvent.setup();
    let resolveCreate!: (created: boolean) => void;
    const onCreate = vi.fn(() => new Promise<boolean>(resolve => { resolveCreate = resolve; }));
    render(<CanvasWorkspaceSwitcher canvases={[]} onSelect={vi.fn()} onCreate={onCreate} pending={false} />);

    await user.click(screen.getByRole('button', { name: '选择或新建画布' }));
    await user.click(screen.getByRole('button', { name: '新建画布' }));
    await user.type(screen.getByRole('textbox', { name: '画布名称' }), '慢创建');
    const submit = screen.getByRole('button', { name: '创建并打开' });
    await user.click(submit);
    await user.click(submit);

    expect(onCreate).toHaveBeenCalledTimes(1);
    resolveCreate(true);
  });
});
