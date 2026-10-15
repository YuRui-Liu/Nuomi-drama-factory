import { beforeEach, describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, waitFor, act, within } from '@testing-library/react';
import * as api from '@/api/creativeSettings';
import { CreativeSettingsPanel } from '@/features/creative-settings/CreativeSettingsPanel';
import {
  createSettingsOperation,
  readSettingsIntent,
} from '@/features/creative-settings/settingsOperation';
import { ApiError } from '@/api/client';
import { CreativeSettingsEntry } from '@/features/creative-settings/CreativeSettingsEntry';
import { useAuthStore } from '@/stores/auth-store';
import { captureFreezoneCanvasRuntime } from '@/features/freezone/canvasSyncRuntime';
const navigation = vi.hoisted(() => ({ state: { location: { pathname: '/p', search: {} } } }));
const selection = vi.hoisted(() => ({
  nodes: [] as { id: string; type: string; selected: boolean }[],
}));
vi.mock('@tanstack/react-router', () => ({
  useRouter: () => navigation,
  useRouterState: ({ select }: { select: (value: typeof navigation.state) => unknown }) =>
    select(navigation.state),
}));
vi.mock('@/stores/canvasStore', () => ({
  useCanvasStore: { getState: () => ({ nodes: selection.nodes }) },
}));
vi.mock('@/features/freezone/canvasSyncRuntime', () => ({ captureFreezoneCanvasRuntime: vi.fn() }));
vi.mock('@/api/creativeSettings', () => ({
  listDefinitions: vi.fn(async () => []),
  getDefinition: vi.fn(),
  listConstraints: vi.fn(async () => []),
  getConstraint: vi.fn(),
  getEffectiveConstraints: vi.fn(async (_p, scope) => ({
    scope,
    scope_proof: 'scope',
    chain: [scope],
    effective: {},
    drafts: [],
    conflicts: [],
  })),
  listCreativeObjects: vi.fn(async () => []),
  postSettingsOperation: vi.fn(),
  getSettingsReceipt: vi.fn(async () => ({ status: 'unknown' })),
  getConstraintProposal: vi.fn(),
}));
vi.mock('@/api/creation', () => ({
  listCreationCanvases: vi.fn(async () => [
    { canvas_id: 'c', title: '第一集', status: 'applied', episode_id: 'e' },
  ]),
}));
vi.mock('@/api/canvas', () => ({
  getFreezoneCanvas: vi.fn(async () => ({
    nodes: [
      { id: 'g', type: 'groupNode', data: { label: '雨夜' } },
      { id: 'a', type: 'textAnnotationNode', parentId: 'g', data: { title: '线索' } },
    ],
  })),
}));
const owner = { user: 'u', project: 'p', canvas: 'c' };
const runtime = { isCurrent: () => true, flush: vi.fn(async () => true) };
const definition = {
  name: 'clue',
  title: '线索',
  category: 'object' as const,
  fields: { text: { type: 'string' as const, required: true } },
};
const agreement = {
  constraint_id: 'tone',
  revision: 1,
  key: '色调',
  value: '温暖',
  level: 'draft' as const,
  scope: { level: 'project' as const },
  citation: '创作意图',
  source: { kind: 'agent', actor_id: 'assistant' },
  scope_status: 'valid' as const,
};
beforeEach(() => {
  const held = new Set<string>();
  Object.defineProperty(navigator, 'locks', {configurable:true,value:{request:async(name:string,_options:unknown,callback:(lock:unknown)=>unknown)=>{if(held.has(name))return callback(null);held.add(name);try{return await callback({name});}finally{held.delete(name);}}}});
  localStorage.clear();
  vi.clearAllMocks();
  runtime.flush.mockResolvedValue(true);
  useAuthStore.setState({ username: 'u' });
  vi.mocked(captureFreezoneCanvasRuntime).mockReturnValue(runtime as never);
  vi.mocked(api.listDefinitions).mockResolvedValue([]);
  vi.mocked(api.listConstraints).mockResolvedValue([]);
  vi.mocked(api.getEffectiveConstraints).mockImplementation(async (_p, scope) => ({
    scope,
    scope_proof: 'scope',
    chain: [scope],
    effective: {},
    drafts: [],
    conflicts: [],
  }));
  vi.mocked(api.getSettingsReceipt).mockResolvedValue({ status: 'unknown', request_id: 'r' });
  vi.mocked(api.postSettingsOperation).mockResolvedValue({
    status: 'applied',
    request_id: 'r',
    revision: 1,
  });
});
const show = (selected: { id: string; type?: string }[] = []) =>
  render(
    <CreativeSettingsPanel
      owner={owner}
      selected={selected}
      runtime={runtime}
      isCurrent={() => true}
    />,
  );
describe('creative settings', () => {
  it('uses the original confirmation payload and exact baseline revision when a legacy proposal omits requested', async () => {
    vi.mocked(api.getConstraint).mockResolvedValue(agreement);
    vi.mocked(api.postSettingsOperation).mockResolvedValue({
      status: 'conflict',
      request_id: 'fallback',
      exception_proposal: { proposal_id: 'old', conflicting_refs: [], approval_available: false },
    });
    await createSettingsOperation(owner, () => true).run({
      domain: 'constraints',
      label: '色调',
      request: {
        request_id: 'fallback',
        action: 'constraint.confirm',
        payload: {
          constraint_id: 'tone',
          expected_revision: 1,
          level: 'hard',
          value: '冷蓝',
          basis: '本次确认',
        },
      },
    });
    show();
    const proposed = await screen.findByRole('region', { name: '本次未应用的请求' });
    await within(proposed).findByText('色调：冷蓝');
    expect(proposed).toHaveTextContent('硬约定');
    expect(api.getConstraint).toHaveBeenCalledWith('p', 'tone', 1);
  });
  it.each(['constraint.save', 'constraint.confirm'])(
    'compares the frozen requested value with existing agreements for %s conflicts',
    async (action) => {
      const existing = { ...agreement, level: 'hard' as const };
      const requested = {
        ...agreement,
        value: '冷蓝',
        level: action === 'constraint.save' ? 'preference' : 'hard',
        scope: { level: 'project' },
      };
      vi.mocked(api.getConstraint).mockResolvedValue(existing);
      vi.mocked(api.postSettingsOperation).mockResolvedValue({
        status: 'conflict',
        request_id: 'compare',
        exception_proposal: {
          proposal_id: 'proposal',
          requested,
          conflicting_refs: [{ constraint_id: 'tone', revision: 1 }],
          approval_available: false,
        },
      });
      await createSettingsOperation(owner, () => true).run({
        domain: 'constraints',
        label: '色调',
        request: {
          request_id: 'compare',
          action,
          payload: { ...requested, value: '不能展示的可变旧值' },
        },
      });
      show();
      const proposed = await screen.findByRole('region', { name: '本次未应用的请求' });
      expect(within(proposed).getByText('色调：冷蓝')).toBeInTheDocument();
      expect(proposed).toHaveTextContent(action === 'constraint.save' ? '偏好' : '硬约定');
      expect(proposed).toHaveTextContent('整个项目');
      const old = await screen.findByRole('region', { name: '已有约定' });
      await waitFor(() => expect(old).toHaveTextContent('色调：温暖'));
      expect(screen.queryByText(/不能展示的可变旧值/)).not.toBeInTheDocument();
      expect(api.postSettingsOperation).toHaveBeenCalledTimes(1);
    },
  );
  it('confirms a persisted canvas record despite JSON key ordering while retaining scope proof validation', async () => {
    const stored = { ...agreement, scope: { canvas_id: 'c', level: 'canvas' as const } };
    vi.mocked(api.listConstraints).mockResolvedValue([stored]);
    vi.mocked(api.getConstraint).mockResolvedValue(stored);
    vi.mocked(api.getEffectiveConstraints).mockImplementation(async () => ({
      scope: { level: 'canvas', canvas_id: 'c' },
      scope_proof: 'same',
      chain: [{ level: 'canvas', canvas_id: 'c' }],
      effective: {},
      drafts: [],
      conflicts: [],
    }));
    show();
    fireEvent.click(screen.getByRole('button', { name: '项目创作约定' }));
    fireEvent.click(await screen.findByRole('button', { name: '查看与调整 色调' }));
    fireEvent.change(await screen.findByLabelText('确认级别'), { target: { value: 'hard' } });
    fireEvent.change(screen.getByLabelText('确认依据'), { target: { value: '创作者锁定' } });
    fireEvent.click(screen.getByRole('button', { name: '确认调整此约定' }));
    await waitFor(() =>
      expect(api.postSettingsOperation).toHaveBeenCalledWith(
        'p',
        'constraints',
        expect.objectContaining({
          action: 'constraint.confirm',
          payload: {
            constraint_id: 'tone',
            expected_revision: 1,
            level: 'hard',
            basis: '创作者锁定',
          },
        }),
      ),
    );
  });
  it('creates readable attributes with generated stable keys and focuses only on editor identity changes', async () => {
    const scroll = vi.fn();
    Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', {
      configurable: true,
      value: scroll,
    });
    show();
    await waitFor(() => expect(screen.getByRole('button', { name: '新建对象定义' })).toBeEnabled());
    fireEvent.click(screen.getByRole('button', { name: '新建对象定义' }));
    expect(screen.getByLabelText('显示名称')).toHaveFocus();
    expect(scroll).toHaveBeenCalledTimes(1);
    fireEvent.change(screen.getByLabelText('显示名称'), { target: { value: '线索' } });
    fireEvent.click(screen.getByRole('button', { name: '添加属性' }));
    fireEvent.change(screen.getByLabelText('属性名称 1'), { target: { value: '线索内容' } });
    expect(scroll).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole('button', { name: '保存定义草稿' }));
    await waitFor(() => expect(api.postSettingsOperation).toHaveBeenCalled());
    const value = vi.mocked(api.postSettingsOperation).mock.calls[0][2].payload
      .definition as api.CreativeDefinition;
    expect(value.name).toMatch(/^object_[a-f0-9]+$/);
    expect(Object.values(value.fields)[0].title).toBe('线索内容');
    expect(Object.keys(value.fields)[0]).toMatch(/^field_/);
  });
  it('browses read-only, saves a visual definition draft without activating it', async () => {
    show();
    fireEvent.click(await screen.findByRole('button', { name: '新建对象定义' }));
    fireEvent.change(screen.getByLabelText('显示名称'), { target: { value: '线索' } });
    fireEvent.change(screen.getByLabelText(/^稳定名称/), { target: { value: 'clue' } });
    fireEvent.click(screen.getByRole('button', { name: '添加属性' }));
    fireEvent.change(screen.getByLabelText('属性名称 1'), { target: { value: 'text' } });
    expect(api.postSettingsOperation).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: '保存定义草稿' }));
    await waitFor(() =>
      expect(api.postSettingsOperation).toHaveBeenCalledWith(
        'p',
        'ontology',
        expect.objectContaining({
          action: 'definition.draft',
          payload: expect.objectContaining({
            expected_revision: 0,
            definition: expect.objectContaining({ name: 'clue' }),
          }),
        }),
      ),
    );
  });
  it('requires exact version and explicit basis to enable a fixed definition', async () => {
    vi.mocked(api.listDefinitions).mockResolvedValue([
      { name: 'clue', category: 'object', current_revision: 2, active_revision: 1 },
    ] as never);
    vi.mocked(api.getDefinition).mockResolvedValue({
      name: 'clue',
      current_revision: 2,
      active_revision: 1,
      revision: 2,
      definition,
      source: { kind: 'user', actor_id: 'u' },
      confirmations: [],
    } as never);
    show();
    fireEvent.click(await screen.findByRole('button', { name: /查看定义 clue/ }));
    await screen.findByText(/固定第 2 版/);
    expect(screen.getByRole('button', { name: '启用此版本' })).toBeDisabled();
    fireEvent.change(screen.getByLabelText('启用依据'), { target: { value: '明确采用' } });
    fireEvent.click(screen.getByRole('button', { name: '启用此版本' }));
    await waitFor(() =>
      expect(api.postSettingsOperation).toHaveBeenCalledWith(
        'p',
        'ontology',
        expect.objectContaining({
          action: 'definition.confirm',
          payload: { name: 'clue', revision: 2, expected_active_revision: 1, basis: '明确采用' },
        }),
      ),
    );
  });
  it('keeps HTTP200 conflicts terminal with the original proposal and does not report success', async () => {
    vi.mocked(api.postSettingsOperation).mockResolvedValue({
      status: 'conflict',
      request_id: 'r',
      exception_proposal: {
        proposal_id: 'proposal',
        approval_available: false,
        conflicts: [{ constraint_id: 'hard', revision: 3 }],
      },
    } as never);
    const result = await createSettingsOperation(owner, () => true).run({
      domain: 'constraints',
      label: '色调',
      request: { request_id: 'r', action: 'constraint.save', payload: {} },
    });
    expect(result.status).toBe('conflict');
    expect(readSettingsIntent(owner)?.result?.exception_proposal).toMatchObject({
      proposal_id: 'proposal',
    });
  });
  it('classifies real structured API CAS errors as terminal and preserves the request', async () => {
    vi.mocked(api.postSettingsOperation).mockRejectedValue(
      new ApiError('约定已变化', 409, { detail: { code: 'revision_conflict' } }),
    );
    expect(
      (
        await createSettingsOperation(owner, () => true).run({
          domain: 'constraints',
          label: '色调',
          request: { request_id: 'cas', action: 'constraint.save', payload: {} },
        })
      ).status,
    ).toBe('failed');
    expect(readSettingsIntent(owner)?.request.request_id).toBe('cas');
  });
  it('selects a real single group and leaves multi selection unresolved', async () => {
    const view = show([{ id: 'g', type: 'groupNode' }]);
    fireEvent.click(screen.getByRole('button', { name: '项目创作约定' }));
    await waitFor(() => expect(screen.getByLabelText('具体目标')).toHaveValue('g'));
    expect(screen.getByLabelText('影响范围')).toHaveValue('group');
    expect(screen.getByRole('option', { name: '雨夜' })).toBeInTheDocument();
    view.unmount();
    show([
      { id: 'g', type: 'groupNode' },
      { id: 'a', type: 'textAnnotationNode' },
    ]);
    fireEvent.click(screen.getByRole('button', { name: '项目创作约定' }));
    expect(screen.getByLabelText('影响范围')).toHaveValue('choose');
    expect(screen.getByRole('button', { name: '新增创作约定' })).toBeDisabled();
  });
  it('saves a preference to an explicitly chosen node without confirming hard', async () => {
    show([{ id: 'a', type: 'textAnnotationNode' }]);
    fireEvent.click(screen.getByRole('button', { name: '项目创作约定' }));
    await waitFor(() => expect(screen.getByRole('button', { name: '新增创作约定' })).toBeEnabled());
    fireEvent.click(screen.getByRole('button', { name: '新增创作约定' }));
    fireEvent.change(screen.getByLabelText('约定主题'), { target: { value: '色调' } });
    fireEvent.change(screen.getByLabelText('约定内容'), { target: { value: '温暖' } });
    fireEvent.change(screen.getByLabelText('内容来源'), { target: { value: '用户设定' } });
    fireEvent.change(screen.getByLabelText('保存级别'), { target: { value: 'preference' } });
    await waitFor(() => expect(screen.getByRole('button', { name: '保存为偏好' })).toBeEnabled());
    fireEvent.click(screen.getByRole('button', { name: '保存为偏好' }));
    await waitFor(() =>
      expect(api.postSettingsOperation).toHaveBeenCalledWith(
        'p',
        'constraints',
        expect.objectContaining({
          action: 'constraint.save',
          payload: {
            key: '色调',
            value: '温暖',
            citation: '用户设定',
            level: 'preference',
            scope: { level: 'node', canvas_id: 'c', node_id: 'a' },
          },
        }),
      ),
    );
  });
  it('archives an inactive hard agreement without changing value or scope', async () => {
    const hard = { ...agreement, level: 'hard' as const, scope_status: 'inactive' as const };
    vi.mocked(api.listConstraints).mockResolvedValue([hard]);
    vi.mocked(api.getConstraint).mockResolvedValue(hard);
    show();
    fireEvent.click(screen.getByRole('button', { name: '项目创作约定' }));
    fireEvent.click(await screen.findByRole('button', { name: '查看与调整 色调' }));
    await screen.findByRole('button', { name: '归档为草稿' });
    expect(screen.queryByLabelText('确认内容')).not.toBeInTheDocument();
    expect(screen.getByLabelText('确认级别').children).toHaveLength(1);
    fireEvent.change(screen.getByLabelText('确认依据'), {
      target: { value: '目标已删除，保留历史' },
    });
    fireEvent.click(screen.getByRole('button', { name: '归档为草稿' }));
    await waitFor(() =>
      expect(api.postSettingsOperation).toHaveBeenCalledWith(
        'p',
        'constraints',
        expect.objectContaining({
          action: 'constraint.confirm',
          payload: {
            constraint_id: 'tone',
            expected_revision: 1,
            level: 'draft',
            basis: '目标已删除，保留历史',
          },
        }),
      ),
    );
  });
  it('restores an unknown original request after reopening and blocks new input', async () => {
    vi.mocked(api.postSettingsOperation).mockRejectedValue(new Error('lost'));
    vi.mocked(api.getSettingsReceipt).mockRejectedValue(new Error('offline'));
    const input = {
      domain: 'ontology' as const,
      label: '线索',
      request: { request_id: 'same', action: 'definition.draft', payload: {} },
    };
    expect((await createSettingsOperation(owner, () => true).run(input)).status).toBe('pending');
    expect(
      (
        await createSettingsOperation(owner, () => true).run({
          ...input,
          request: { ...input.request, request_id: 'new' },
        })
      ).status,
    ).toBe('busy');
    vi.mocked(api.getSettingsReceipt).mockResolvedValue({
      status: 'applied',
      request_id: 'same',
      revision: 1,
    });
    show();
    fireEvent.click(await screen.findByRole('button', { name: '恢复原请求' }));
    await screen.findByText('线索 · 已保存');
    expect(api.postSettingsOperation).toHaveBeenCalledTimes(1);
  });
  it('keeps a late receipt only in the original owner journal', async () => {
    let resolve!: (value: api.SettingsResult) => void,
      current = true;
    vi.mocked(api.postSettingsOperation).mockImplementation(
      () => new Promise((r) => (resolve = r)),
    );
    const operation = createSettingsOperation(owner, () => current).run({
      domain: 'ontology',
      label: '线索',
      request: { request_id: 'late', action: 'definition.draft', payload: {} },
    });
    current = false;
    resolve({ status: 'applied', request_id: 'late' });
    expect((await operation).status).toBe('detached');
    expect(readSettingsIntent(owner)?.phase).toBe('applied');
    expect(readSettingsIntent({ ...owner, user: 'other' })).toBeNull();
  });
  it('refuses writes when durable storage fails', async () => {
    const spy = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('full');
    });
    expect(
      (
        await createSettingsOperation(owner, () => true).run({
          domain: 'ontology',
          label: '线索',
          request: { request_id: 'storage', action: 'definition.draft', payload: {} },
        })
      ).status,
    ).toBe('failed');
    expect(api.postSettingsOperation).not.toHaveBeenCalled();
    spy.mockRestore();
  });
  it('keeps a CAS-conflicted definition draft reachable when the actual current version advances', async () => {
    const v1 = {
      name: 'clue',
      category: 'object' as const,
      current_revision: 1,
      active_revision: 1,
      revision: 1,
      definition,
      source: { kind: 'user', actor_id: 'u' },
      confirmations: [],
    };
    vi.mocked(api.listDefinitions).mockResolvedValue([v1]);
    vi.mocked(api.getDefinition).mockResolvedValue(v1);
    vi.mocked(api.postSettingsOperation).mockRejectedValue(
      new ApiError('版本已变化', 409, { detail: { code: 'revision_conflict' } }),
    );
    const view = show();
    fireEvent.click(await screen.findByRole('button', { name: '查看定义 clue' }));
    fireEvent.click(await screen.findByRole('button', { name: '以此版本编辑草稿' }));
    fireEvent.change(screen.getByLabelText('显示名称'), { target: { value: '我的修改' } });
    fireEvent.click(screen.getByRole('button', { name: '保存定义草稿' }));
    await screen.findByText('我的修改 · 保存未完成');
    const v2 = {
      ...v1,
      revision: 2,
      current_revision: 2,
      definition: { ...definition, title: '他人的修改' },
    };
    vi.mocked(api.listDefinitions).mockResolvedValue([v2]);
    vi.mocked(api.getDefinition).mockResolvedValue(v2);
    fireEvent.click(screen.getByRole('button', { name: '保留草稿，继续编辑' }));
    await screen.findByText('原版本已有更新，当前草稿仍保留原比较版本。');
    expect(screen.getByLabelText('显示名称')).toHaveValue('我的修改');
    expect(screen.getByRole('button', { name: '保存定义草稿' })).toBeDisabled();
    view.unmount();
    show();
    fireEvent.click(await screen.findByRole('button', { name: /恢复草稿：我的修改/ }));
    expect(screen.getByLabelText('显示名称')).toHaveValue('我的修改');
    expect(api.postSettingsOperation).toHaveBeenCalledTimes(1);
  });
  it('rejects changed group membership evidence before submitting a constraint', async () => {
    show([{ id: 'a', type: 'textAnnotationNode' }]);
    fireEvent.click(screen.getByRole('button', { name: '项目创作约定' }));
    await waitFor(() => expect(screen.getByRole('button', { name: '新增创作约定' })).toBeEnabled());
    fireEvent.click(screen.getByRole('button', { name: '新增创作约定' }));
    fireEvent.change(screen.getByLabelText('约定主题'), { target: { value: '色调' } });
    fireEvent.change(screen.getByLabelText('约定内容'), { target: { value: '温暖' } });
    fireEvent.change(screen.getByLabelText('内容来源'), { target: { value: '设定' } });
    vi.mocked(api.getEffectiveConstraints).mockImplementation(async (_p, scope) => ({
      scope,
      scope_proof: 'moved',
      chain: [scope],
      effective: {},
      drafts: [],
      conflicts: [],
    }));
    await waitFor(() => expect(screen.getByRole('button', { name: '保存约定草稿' })).toBeEnabled());
    fireEvent.click(screen.getByRole('button', { name: '保存约定草稿' }));
    await screen.findByRole('alert');
    expect(screen.getByRole('alert')).toHaveTextContent('分组归属已变化');
    expect(api.postSettingsOperation).not.toHaveBeenCalled();
  });
  it('keeps natural-language preferences distinct from explicitly selected attribute rules', async () => {
    const v1 = {
      name: 'clue',
      category: 'object' as const,
      current_revision: 1,
      active_revision: 1,
      revision: 1,
      definition: {
        ...definition,
        fields: { text: { type: 'string' as const, title: '线索内容' } },
      },
      source: { kind: 'user', actor_id: 'u' },
      confirmations: [],
    };
    vi.mocked(api.listDefinitions).mockResolvedValue([v1]);
    vi.mocked(api.getDefinition).mockResolvedValue(v1);
    show();
    fireEvent.click(screen.getByRole('button', { name: '项目创作约定' }));
    await waitFor(() => expect(screen.getByRole('button', { name: '新增创作约定' })).toBeEnabled());
    fireEvent.click(screen.getByRole('button', { name: '新增创作约定' }));
    fireEvent.change(screen.getByLabelText('约定主题'), { target: { value: '线索颜色' } });
    fireEvent.change(screen.getByLabelText('内容来源'), { target: { value: '剧本' } });
    fireEvent.change(screen.getByLabelText('对象定义'), { target: { value: 'clue' } });
    fireEvent.change(screen.getByLabelText('属性等于'), { target: { value: 'text' } });
    fireEvent.change(screen.getByLabelText('约定内容'), { target: { value: '红色' } });
    await waitFor(() => expect(screen.getByRole('button', { name: '保存约定草稿' })).toBeEnabled());
    fireEvent.click(screen.getByRole('button', { name: '保存约定草稿' }));
    await waitFor(() =>
      expect(api.postSettingsOperation).toHaveBeenCalledWith(
        'p',
        'constraints',
        expect.objectContaining({
          payload: expect.objectContaining({
            rule: { kind: 'custom:clue', schema_revision: 1, field: 'text', operator: 'equals' },
            level: 'draft',
          }),
        }),
      ),
    );
  });
  it('freezes real Entry selection and closes on account/navigation without changing canvas', async () => {
    selection.nodes = [{ id: 'g', type: 'groupNode', selected: true }];
    const view = render(<CreativeSettingsEntry project="p" canvasId="c" />);
    fireEvent.click(screen.getByRole('button', { name: '创作对象与约定' }));
    await screen.findByRole('dialog');
    selection.nodes = [{ id: 'a', type: 'textAnnotationNode', selected: true }];
    fireEvent.click(screen.getByRole('button', { name: '项目创作约定' }));
    await waitFor(() => expect(screen.getByLabelText('具体目标')).toHaveValue('g'));
    act(() => useAuthStore.setState({ username: 'other' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: '创作对象与约定' }));
    await screen.findByRole('dialog');
    navigation.state.location = { pathname: '/other', search: {} };
    view.rerender(<CreativeSettingsEntry project="p" canvasId="c" />);
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(api.postSettingsOperation).not.toHaveBeenCalled();
  });
  it('does not fall back to reading unsaved canvas targets when flush fails', async () => {
    runtime.flush.mockResolvedValue(false);
    show();
    await screen.findByRole('alert');
    expect(api.listDefinitions).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: '新建对象定义' })).toBeDisabled();
  });
});

describe('cross-tab Settings recovery', () => {
  const installLocks = () => {
    const held = new Set<string>();
    Object.defineProperty(navigator, 'locks', {
      configurable: true,
      value: {
        request: async (name: string, _options: unknown, callback: (lock: unknown) => unknown) => {
          if (held.has(name)) return callback(null);
          held.add(name);
          try {
            return await callback({ name });
          } finally {
            held.delete(name);
          }
        },
      },
    });
  };
  beforeEach(installLocks);
  const request = {
    domain: 'constraints' as const,
    label: '原请求',
    request: { request_id: 'A', action: 'save', payload: {} },
  };
  it('serializes independent modules and rejects finish while the request lock is held', async () => {
    vi.resetModules();
    const a = await import('@/features/creative-settings/settingsOperation');
    vi.resetModules();
    const b = await import('@/features/creative-settings/settingsOperation');
    let release!: (v: any) => void;
    vi.mocked(api.postSettingsOperation).mockReturnValue(
      new Promise((r) => {
        release = r;
      }),
    );
    const first = a.createSettingsOperation(owner, () => true).run(request);
    await waitFor(() => expect(api.postSettingsOperation).toHaveBeenCalledTimes(1));
    const second = b.createSettingsOperation(owner, () => true).run(request);
    const finish = Promise.resolve().then(() =>
      Reflect.apply(b.finishSettingsIntent, null, [owner, 'A', () => true]),
    );
    await expect(finish).rejects.toThrow();
    await new Promise((r) => setTimeout(r, 0));
    const calls = vi.mocked(api.postSettingsOperation).mock.calls.length;
    release({
      status: 'conflict' as const,
      request_id: 'A',
      exception_proposal: { proposal_id: 'proposal', approval_available: false },
    });
    expect((await second).status).toBe('busy');
    expect(calls).toBe(1);
    await first;
  });
  it('refuses stale finish A against terminal B and preserves its full receipt', async () => {
    const m = await import('@/features/creative-settings/settingsOperation');
    vi.mocked(api.postSettingsOperation).mockResolvedValue({
      status: 'conflict' as const,
      request_id: 'A',
      exception_proposal: { proposal_id: 'proposal', approval_available: false },
    });
    await m.createSettingsOperation(owner, () => true).run(request);
    await Reflect.apply(m.finishSettingsIntent, null, [owner, 'A', () => true]);
    vi.mocked(api.postSettingsOperation).mockResolvedValue({
      status: 'conflict' as const,
      request_id: 'B',
      exception_proposal: { proposal_id: 'proposal', approval_available: false },
    });
    await m
      .createSettingsOperation(owner, () => true)
      .run({ ...request, request: { ...request.request, request_id: 'B' } });
    const before = m.readSettingsIntent(owner);
    await expect(
      Promise.resolve().then(() =>
        Reflect.apply(m.finishSettingsIntent, null, [owner, 'A', () => true]),
      ),
    ).rejects.toThrow();
    expect(m.readSettingsIntent(owner)).toEqual(before);
    expect(before?.result).toEqual({
      status: 'conflict' as const,
      request_id: 'B',
      exception_proposal: { proposal_id: 'proposal', approval_available: false },
    });
  });
  it('fails closed without Web Locks and rechecks active identity after acquisition', async () => {
    const m = await import('@/features/creative-settings/settingsOperation');
    Object.defineProperty(navigator, 'locks', { configurable: true, value: undefined });
    expect((await m.createSettingsOperation(owner, () => true).run(request)).status).toBe('failed');
    await expect(
      Promise.resolve().then(() =>
        Reflect.apply(m.finishSettingsIntent, null, [owner, 'A', () => true]),
      ),
    ).rejects.toThrow();
    expect(api.postSettingsOperation).not.toHaveBeenCalled();
    let active = true;
    Object.defineProperty(navigator, 'locks', {
      configurable: true,
      value: {
        request: async (_n: string, _o: unknown, callback: (lock: unknown) => unknown) => {
          active = false;
          return callback({ name: 'lock' });
        },
      },
    });
    expect((await m.createSettingsOperation(owner, () => active).run(request)).status).toBe(
      'detached',
    );
    expect(api.postSettingsOperation).not.toHaveBeenCalled();
  });
});

describe('Settings unknown journal protection', () => {
  it('preserves unknown B against stale A finish and recovers its original receipt', async () => {
    const m = await import('@/features/creative-settings/settingsOperation');
    const input = {
      domain: 'constraints' as const,
      label: '原请求',
      request: { request_id: 'A', action: 'save', payload: {} },
    };
    vi.mocked(api.postSettingsOperation).mockResolvedValue({
      status: 'conflict' as const,
      request_id: 'A',
      exception_proposal: { proposal_id: 'P', approval_available: false },
    });
    await m.createSettingsOperation(owner, () => true).run(input);
    await m.finishSettingsIntent(owner, 'A', () => true);
    vi.mocked(api.postSettingsOperation).mockRejectedValue(new Error('lost'));
    vi.mocked(api.getSettingsReceipt).mockRejectedValue(new Error('offline'));
    const b = { ...input, request: { ...input.request, request_id: 'B' } };
    expect((await m.createSettingsOperation(owner, () => true).run(b)).status).toBe('pending');
    await expect(m.finishSettingsIntent(owner, 'A', () => true)).rejects.toThrow();
    expect(m.readSettingsIntent(owner)?.request).toEqual(b.request);
    const calls = vi.mocked(api.postSettingsOperation).mock.calls.length;
    vi.mocked(api.getSettingsReceipt).mockResolvedValue({
      status: 'conflict' as const,
      request_id: 'B',
      exception_proposal: { proposal_id: 'P', approval_available: false },
    });
    await m.createSettingsOperation(owner, () => true).run();
    expect(api.postSettingsOperation).toHaveBeenCalledTimes(calls);
    expect(m.readSettingsIntent(owner)?.result).toEqual({
      status: 'conflict' as const,
      request_id: 'B',
      exception_proposal: { proposal_id: 'P', approval_available: false },
    });
    const before = m.readSettingsIntent(owner);
    await expect(m.finishSettingsIntent(owner, 'B', () => false)).rejects.toThrow();
    expect(m.readSettingsIntent(owner)).toEqual(before);
    Object.defineProperty(navigator, 'locks', { configurable: true, value: undefined });
    await expect(m.finishSettingsIntent(owner, 'B', () => true)).rejects.toThrow();
    expect(m.readSettingsIntent(owner)).toEqual(before);
  });
});
