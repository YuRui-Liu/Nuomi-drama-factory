import { fireEvent, render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { StaticPanelEditor } from './StaticPanelEditor';

it('requires an explicit still and adopts edits without copying the full action', () => {
  const adopt = vi.fn();
  render(<StaticPanelEditor frame={{ id: 's1', description: '写字之后跑走', referenceIndex: null }} onAdopt={adopt} />);
  expect(screen.getByRole('button', { name: '采用此画面' })).toBeDisabled();
  for (const [label, value] of [['当前可见状态', '蓝袖手持炭条'], ['单一机位与构图', '俯视近景'],
    ['场景', '钟楼'], ['当前光线', '冷月光']]) {
    fireEvent.change(screen.getByLabelText(label), { target: { value } });
  }
  fireEvent.click(screen.getByRole('button', { name: '采用此画面' }));
  expect(adopt).toHaveBeenCalledWith(expect.objectContaining({ static_source_description: '写字之后跑走',
    static_panel: expect.objectContaining({ state: '蓝袖手持炭条', framing: '俯视近景' }) }));
  expect(JSON.stringify(adopt.mock.calls[0][0].static_panel)).not.toContain('跑走');
});
