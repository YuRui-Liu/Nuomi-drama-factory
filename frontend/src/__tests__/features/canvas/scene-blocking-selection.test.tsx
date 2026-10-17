import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { SceneBlockingEditor } from '@/features/canvas/production/SceneBlockingEditor';

describe('scene blocking namespaced identity', () => {
  it('edits actor and prop coordinates without truncating their IDs', () => {
    const save = vi.fn();
    render(<SceneBlockingEditor initial={{
      label: '仓库',
      actors: [{ id: 'character:lin-mo', label: '林墨', x: 20, y: 30, facing: 0 }],
      props: [{ id: 'prop:torch', label: '手电', x: 40, y: 50, facing: 0 }],
      lights: [], camera: null,
    }} busy={false} onSave={save} />);
    fireEvent.change(screen.getByLabelText('选中对象'), { target: { value: 'actor:character:lin-mo' } });
    fireEvent.change(screen.getByLabelText('X'), { target: { value: '27' } });
    fireEvent.change(screen.getByLabelText('选中对象'), { target: { value: 'prop:prop:torch' } });
    fireEvent.change(screen.getByLabelText('X'), { target: { value: '48' } });
    fireEvent.click(screen.getByRole('button', { name: '保存调度新版本' }));
    const draft = save.mock.calls[0][0];
    expect(draft.actors[0]).toMatchObject({ id: 'character:lin-mo', x: 27 });
    expect(draft.props[0]).toMatchObject({ id: 'prop:torch', x: 48 });
  });
});
