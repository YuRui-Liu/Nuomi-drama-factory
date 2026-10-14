import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import * as api from '@/api/reusableAgents';
import { AgentConfigurationPanel } from '@/features/agent-configuration/AgentConfigurationPanel';
import {
  createAgentOperation,
  readAgentIntent,
} from '@/features/agent-configuration/agentOperation';
import { listDirectionCatalog } from '@/api/directions';
import { AgentConfigurationEntry } from '@/features/agent-configuration/AgentConfigurationEntry';
import { captureFreezoneCanvasRuntime } from '@/features/freezone/canvasSyncRuntime';
import { useAuthStore } from '@/stores/auth-store';
const navigation = vi.hoisted(() => ({
  state: { location: { pathname: '/projects/p', search: {} } },
}));
vi.mock('@tanstack/react-router', () => ({
  useRouter: () => navigation,
  useRouterState: ({ select }: { select: (state: typeof navigation.state) => unknown }) =>
    select(navigation.state),
}));
vi.mock('@/features/freezone/canvasSyncRuntime', () => ({
  captureFreezoneCanvasRuntime: vi.fn(() => ({ isCurrent: () => true })),
}));
vi.mock('@/api/reusableAgents', () => ({
  listPersonalAgents: vi.fn(),
  listAgentAdoptions: vi.fn(),
  getAgentCapabilities: vi.fn(),
  previewAgent: vi.fn(),
  previewAgentPatch: vi.fn(),
  previewAgentOverride: vi.fn(),
  previewAgentUpgrade: vi.fn(),
  postAgentOperation: vi.fn(),
  getAgentReceipt: vi.fn(),
  listAgentStudioResources: vi.fn(async () => []),
}));
vi.mock('@/api/directions', () => ({ listDirectionCatalog: vi.fn(async () => []) }));
vi.mock('@/features/studios/studio-api', () => ({
  listStudioDocuments: vi.fn(async () => []),
  getStudioHistory: vi.fn(async () => []),
}));
const owner = { user: 'alice', project: 'p', canvas: 'c' };
const config = {
  name: '人物主创',
  responsibilities: '动机',
  methods: '先事实',
  references: [],
  skill_notes: [],
  capabilities: [],
  defaults: {},
  studio_refs: [],
};
const fixed = { id: 'personal', revision: 1, kind: 'agent', configuration: config, proof: 'fixed' };
const adoption = {
  adoption_id: 'adopted',
  revision: 1,
  fixed,
  overrides: {},
  effective: config,
  source: { user_id: 'alice', ref: { id: 'personal', revision: 1 } },
};
function show(isCurrent = () => true) {
  return render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <AgentConfigurationPanel owner={owner} isCurrent={isCurrent} />
    </QueryClientProvider>,
  );
}
beforeEach(() => {
  localStorage.clear();
  vi.clearAllMocks();
  useAuthStore.setState({ username: 'alice' });
  vi.mocked(captureFreezoneCanvasRuntime).mockReturnValue({ isCurrent: () => true } as never);
  vi.mocked(api.listPersonalAgents).mockResolvedValue([fixed] as never);
  vi.mocked(api.listAgentAdoptions).mockResolvedValue([adoption] as never);
  vi.mocked(api.getAgentCapabilities).mockResolvedValue({
    items: [{ id: 'proposal.create', title: '提出建议', runtime_permission: 'proposal_only' }],
    execution_enabled: false,
  });
  vi.mocked(api.previewAgent).mockResolvedValue({
    configuration: config,
    proof: 'preview',
  } as never);
  vi.mocked(api.previewAgentPatch).mockResolvedValue({
    configuration: { ...config, methods: '新方法' },
    proof: 'patch',
  } as never);
  vi.mocked(api.getAgentReceipt).mockResolvedValue({ status: 'unknown' } as never);
  vi.mocked(api.postAgentOperation).mockResolvedValue({ id: 'personal', revision: 2 });
});
describe('Agent configuration management', () => {
  it('browses without writing and edits a fixed version via a patch preview', async () => {
    show();
    fireEvent.click(await screen.findByRole('button', { name: '个人库' }));
    fireEvent.click(await screen.findByRole('button', { name: '编辑 人物主创 v1' }));
    fireEvent.change(screen.getByLabelText('创作方法'), { target: { value: '新方法' } });
    expect(api.postAgentOperation).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: '预览发布内容' }));
    await screen.findByText('发布预览');
    expect(api.previewAgentPatch).toHaveBeenCalledWith(
      'p',
      { id: 'personal', revision: 1 },
      { methods: '新方法' },
    );
    fireEvent.click(screen.getByRole('button', { name: '保存个人新版' }));
    await waitFor(() => expect(api.postAgentOperation).toHaveBeenCalled());
    expect(vi.mocked(api.postAgentOperation).mock.calls[0][2]).toMatchObject({
      action: 'patch',
      payload: {
        ref: { id: 'personal', revision: 1 },
        changes: { methods: '新方法' },
        proof: 'patch',
      },
    });
  });
  it('retains drafts across close and reopen without a global dirty event', async () => {
    const view = show();
    fireEvent.click(await screen.findByRole('button', { name: '个人库' }));
    fireEvent.click(await screen.findByRole('button', { name: '新建 Agent' }));
    fireEvent.change(screen.getByLabelText('名称'), { target: { value: '未完成配置' } });
    view.unmount();
    show();
    fireEvent.click(await screen.findByRole('button', { name: '个人库' }));
    fireEvent.click(await screen.findByRole('button', { name: '新建 Agent' }));
    expect(screen.getByLabelText('名称')).toHaveValue('未完成配置');
  });
  it('persists original unknown request and resumes its personal receipt after reopen', async () => {
    const request = {
      request_id: 'same',
      action: 'save',
      payload: { configuration: config, proof: 'preview', expected_revision: 0 },
    };
    vi.mocked(api.postAgentOperation).mockRejectedValue(new Error('lost'));
    vi.mocked(api.getAgentReceipt).mockRejectedValue(new Error('offline'));
    expect(
      (
        await createAgentOperation(owner, () => true).run({
          channel: 'personal',
          request,
          label: '人物主创',
        })
      ).status,
    ).toBe('pending');
    expect(readAgentIntent(owner)?.request.request_id).toBe('same');
    vi.mocked(api.getAgentReceipt).mockResolvedValue({ id: 'personal', revision: 1 });
    const result = await createAgentOperation(owner, () => true).run();
    expect(result.status).toBe('applied');
    expect(api.postAgentOperation).toHaveBeenCalledTimes(1);
  });
  it('does not submit without durable storage or update a departed workspace', async () => {
    const request = {
      request_id: 'one',
      action: 'copy',
      payload: { ref: { id: 'personal', revision: 1 }, name: 'copy' },
    };
    const spy = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('full');
    });
    expect(
      (
        await createAgentOperation(owner, () => true).run({
          channel: 'personal',
          request,
          label: 'copy',
        })
      ).status,
    ).toBe('failed');
    expect(api.postAgentOperation).not.toHaveBeenCalled();
    spy.mockRestore();
    expect(
      (
        await createAgentOperation(owner, () => false).run({
          channel: 'personal',
          request,
          label: 'copy',
        })
      ).status,
    ).toBe('detached');
  });
  it('creates all configuration fields visually, previewing without writes', async () => {
    vi.mocked(listDirectionCatalog).mockImplementation(async (_p, kind) => [
      {
        kind,
        id: kind,
        proof: 'trusted',
        title: kind === 'style' ? '水墨' : '悬念',
        version: '1',
        available: true,
        configuration: {},
        metadata: {},
      },
    ]);
    vi.mocked(api.listAgentStudioResources).mockResolvedValue([
      {
        kind: 'director',
        id: 'studio',
        revision: 2,
        name: '导演方法',
        portable: true,
        reason: null,
        excluded_fields: ['api_key'],
        source: { kind: 'project_studio', project_id: 'p' },
      },
    ]);
    show();
    fireEvent.click(await screen.findByRole('button', { name: '个人库' }));
    fireEvent.click(screen.getByRole('button', { name: '新建 Agent' }));
    fireEvent.change(screen.getByLabelText('名称'), { target: { value: '新主创' } });
    fireEvent.change(screen.getByLabelText('职责'), { target: { value: '情节' } });
    fireEvent.change(screen.getByLabelText('创作方法'), { target: { value: '核对' } });
    fireEvent.change(screen.getByLabelText(/参考作品/), { target: { value: '作品一\n作品二' } });
    fireEvent.change(screen.getByLabelText(/技能说明/), { target: { value: '只建议' } });
    fireEvent.click(screen.getByLabelText('提出建议'));
    fireEvent.click(screen.getByRole('button', { name: '重新选择默认风格与手法' }));
    await screen.findByRole('option', { name: '水墨 · 1' });
    fireEvent.change(screen.getByLabelText('默认风格'), { target: { value: 'style' } });
    fireEvent.click(screen.getByRole('button', { name: '重新选择 Studio 参考' }));
    fireEvent.click(await screen.findByRole('checkbox', { name: /导演方法/ }));
    fireEvent.click(screen.getByRole('button', { name: '预览发布内容' }));
    await screen.findByText('发布预览');
    expect(api.previewAgent).toHaveBeenCalledWith(
      'p',
      expect.objectContaining({
        name: '新主创',
        references: ['作品一', '作品二'],
        capabilities: ['proposal.create'],
        defaults: { style: { id: 'style', proof: 'trusted' } },
        studio_refs: [{ kind: 'director', id: 'studio', revision: 2 }],
      }),
    );
    expect(api.postAgentOperation).not.toHaveBeenCalled();
  });
  it('cancels a project override using null instead of changing the personal version', async () => {
    vi.mocked(api.listAgentAdoptions).mockResolvedValue([
      {
        ...adoption,
        effective: { ...config, methods: '本项目' },
        overrides: { methods: { value: '本项目', base: '先事实' } },
      },
    ] as never);
    vi.mocked(api.previewAgentOverride).mockResolvedValue({
      proof: 'override',
      before: { ...config, methods: '本项目' },
      changes: { methods: null },
      adoption_id: 'adopted',
      expected_revision: 1,
    });
    show();
    fireEvent.click(await screen.findByRole('button', { name: '查看与局部调整' }));
    fireEvent.click(screen.getByRole('button', { name: '取消本级覆盖：创作方法' }));
    expect(screen.getByLabelText('创作方法')).toHaveValue('先事实');
    fireEvent.click(screen.getByRole('button', { name: '预览项目调整' }));
    await screen.findByText('发布预览');
    fireEvent.click(screen.getByRole('button', { name: '保存项目覆盖' }));
    await waitFor(() =>
      expect(api.postAgentOperation).toHaveBeenCalledWith(
        'p',
        'project',
        expect.objectContaining({
          action: 'override',
          payload: expect.objectContaining({ changes: { methods: null }, expected_revision: 1 }),
        }),
      ),
    );
  });
  it('requires an explicit resolution for every upgrade conflict', async () => {
    vi.mocked(api.listPersonalAgents).mockResolvedValue([{ ...fixed, revision: 2 }] as never);
    vi.mocked(api.previewAgentUpgrade).mockResolvedValue({
      adoption_id: 'adopted',
      expected_revision: 1,
      before: config,
      after: { ...config, methods: '新版' },
      overrides: { methods: { base: '先事实', value: '本项目' } },
      conflicts: ['methods'],
      dependencies_before: {},
      dependencies_after: {},
      ref: { id: 'personal', revision: 2 },
      proof: 'upgrade',
    });
    show();
    fireEvent.click(await screen.findByRole('button', { name: '比较个人新版 v2' }));
    const save = await screen.findByRole('button', { name: '确认升级，保留未冲突覆盖' });
    expect(save).toBeDisabled();
    fireEvent.change(screen.getByLabelText('处理冲突：创作方法'), { target: { value: 'keep' } });
    fireEvent.click(save);
    await waitFor(() =>
      expect(api.postAgentOperation).toHaveBeenCalledWith(
        'p',
        'project',
        expect.objectContaining({
          action: 'upgrade',
          payload: expect.objectContaining({
            resolutions: { methods: 'keep' },
            ref: { id: 'personal', revision: 2 },
            proof: 'upgrade',
          }),
        }),
      ),
    );
  });
  it('shows current project overrides and the resolved effective configuration while upgrading', async () => {
    vi.mocked(api.listPersonalAgents).mockResolvedValue([{ ...fixed, revision: 2 }] as never);
    vi.mocked(api.previewAgentUpgrade).mockResolvedValue({
      adoption_id: 'adopted',
      expected_revision: 1,
      before: config,
      after: { ...config, methods: '新版C' },
      overrides: {
        methods: { base: '先事实', value: '项目B' },
        responsibilities: { base: '动机', value: '本项目职责' },
      },
      conflicts: ['methods'],
      dependencies_before: {},
      dependencies_after: {},
      ref: { id: 'personal', revision: 2 },
      proof: 'upgrade',
    });
    show();
    fireEvent.click(await screen.findByRole('button', { name: '比较个人新版 v2' }));
    const current = await screen.findByRole('region', { name: '项目当前生效配置' });
    expect(within(current).getByText('项目B')).toBeInTheDocument();
    expect(within(current).getByText('本项目职责')).toBeInTheDocument();
    expect(screen.queryByRole('region', { name: '升级后生效预览' })).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('处理冲突：创作方法'), { target: { value: 'keep' } });
    let effective = screen.getByRole('region', { name: '升级后生效预览' });
    expect(within(effective).getByText('项目B')).toBeInTheDocument();
    expect(within(effective).getByText('本项目职责')).toBeInTheDocument();
    expect(within(effective).queryByText('新版C')).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('处理冲突：创作方法'), { target: { value: 'drop' } });
    effective = screen.getByRole('region', { name: '升级后生效预览' });
    expect(within(effective).getByText('新版C')).toBeInTheDocument();
    expect(within(effective).getByText('本项目职责')).toBeInTheDocument();
    expect(within(effective).queryByText('项目B')).not.toBeInTheDocument();
  });
  it('shows an actionable terminal conflict and keeps the form draft for re-comparison', async () => {
    vi.mocked(api.postAgentOperation).mockRejectedValue(
      Object.assign(new Error('版本已变化'), { code: 'revision_conflict' }),
    );
    show();
    fireEvent.click(await screen.findByRole('button', { name: '个人库' }));
    fireEvent.click(await screen.findByRole('button', { name: '编辑 人物主创 v1' }));
    fireEvent.change(screen.getByLabelText('创作方法'), { target: { value: '草稿保留' } });
    fireEvent.click(screen.getByRole('button', { name: '预览发布内容' }));
    fireEvent.click(await screen.findByRole('button', { name: '保存个人新版' }));
    await screen.findByText(/人物主创 · 保存未完成/);
    fireEvent.click(screen.getByRole('button', { name: '完成，继续配置' }));
    fireEvent.click(await screen.findByRole('button', { name: '编辑 人物主创 v1' }));
    expect(screen.getByLabelText('创作方法')).toHaveValue('草稿保留');
  });
  it.each(['personal', 'project'] as const)(
    'keeps a stale %s draft reachable after the actual catalog advances',
    async (kind) => {
      vi.mocked(api.postAgentOperation).mockRejectedValue(
        Object.assign(new Error('版本已变化'), { code: 'revision_conflict' }),
      );
      vi.mocked(api.previewAgentOverride).mockResolvedValue({
        proof: 'old-proof',
        before: config,
        changes: { methods: '旧草稿' },
        adoption_id: 'adopted',
        expected_revision: 1,
      });
      show();
      if (kind === 'personal') {
        fireEvent.click(await screen.findByRole('button', { name: '个人库' }));
        fireEvent.click(await screen.findByRole('button', { name: '编辑 人物主创 v1' }));
      } else fireEvent.click(await screen.findByRole('button', { name: '查看与局部调整' }));
      fireEvent.change(screen.getByLabelText('创作方法'), { target: { value: '旧草稿' } });
      fireEvent.click(
        screen.getByRole('button', { name: kind === 'personal' ? '预览发布内容' : '预览项目调整' }),
      );
      fireEvent.click(
        await screen.findByRole('button', {
          name: kind === 'personal' ? '保存个人新版' : '保存项目覆盖',
        }),
      );
      await screen.findByText(/保存未完成/);
      vi.mocked(api.listPersonalAgents).mockResolvedValue([
        { ...fixed, revision: 2, configuration: { ...config, methods: '新版方法' } },
      ] as never);
      vi.mocked(api.listAgentAdoptions).mockResolvedValue([
        { ...adoption, revision: 2, effective: { ...config, methods: '新版方法' } },
      ] as never);
      fireEvent.click(screen.getByRole('button', { name: '完成，继续配置' }));
      fireEvent.click(await screen.findByRole('button', { name: /恢复草稿：人物主创/ }));
      expect(screen.getByLabelText('创作方法')).toHaveValue('旧草稿');
      expect(await screen.findByRole('region', { name: '草稿原基线与最新配置' })).toHaveTextContent(
        '新版方法',
      );
      expect(api.postAgentOperation).toHaveBeenCalledTimes(1);
      fireEvent.click(screen.getByRole('button', { name: '保留我的修改，以最新版本重新预览' }));
      expect(screen.getByLabelText('创作方法')).toHaveValue('旧草稿');
      fireEvent.click(
        screen.getByRole('button', { name: kind === 'personal' ? '预览发布内容' : '预览项目调整' }),
      );
      await screen.findByText('发布预览');
      if (kind === 'personal')
        expect(api.previewAgentPatch).toHaveBeenLastCalledWith(
          'p',
          { id: 'personal', revision: 2 },
          { methods: '旧草稿' },
        );
      expect(api.postAgentOperation).toHaveBeenCalledTimes(1);
    },
  );
  it('rejects a different request while unknown and isolates a late response from another owner', async () => {
    let resolve!: (v: api.AgentResult) => void;
    vi.mocked(api.postAgentOperation).mockImplementation(
      () =>
        new Promise((r) => {
          resolve = r;
        }),
    );
    let current = true;
    const request = {
      request_id: 'late',
      action: 'copy',
      payload: { ref: { id: 'personal', revision: 1 }, name: '副本' },
    };
    const pending = createAgentOperation(owner, () => current).run({
      channel: 'personal',
      request,
      label: '副本',
    });
    expect(
      (
        await createAgentOperation(owner, () => true).run({
          channel: 'personal',
          request: { ...request, request_id: 'other' },
          label: 'other',
        })
      ).status,
    ).toBe('busy');
    current = false;
    resolve({ id: 'copy', revision: 1 });
    expect((await pending).status).toBe('detached');
    expect(readAgentIntent(owner)?.result).toEqual({ id: 'copy', revision: 1 });
    expect(readAgentIntent({ ...owner, user: 'bob' })).toBeNull();
  });
  it('guards real Entry on account/navigation/runtime changes without a global store fallback', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const entry = () => (
      <QueryClientProvider client={client}>
        <AgentConfigurationEntry project="p" canvasId="c" />
      </QueryClientProvider>
    );
    const view = render(entry());
    fireEvent.click(screen.getByRole('button', { name: '项目 Agent 配置' }));
    await screen.findByRole('dialog');
    act(() => useAuthStore.setState({ username: 'bob' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    vi.mocked(captureFreezoneCanvasRuntime).mockReturnValue(null);
    fireEvent.click(screen.getByRole('button', { name: '项目 Agent 配置' }));
    expect(screen.getByRole('alert')).toHaveTextContent('尚未就绪');
    vi.mocked(captureFreezoneCanvasRuntime).mockReturnValue({ isCurrent: () => true } as never);
    fireEvent.click(screen.getByRole('button', { name: '项目 Agent 配置' }));
    await screen.findByRole('dialog');
    navigation.state.location = { pathname: '/projects/other', search: {} };
    view.rerender(entry());
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(api.postAgentOperation).not.toHaveBeenCalled();
  });
  it('previews adoption read-only and reports a project revision after explicit adoption', async () => {
    vi.mocked(api.postAgentOperation).mockResolvedValue({
      status: 'applied',
      revision: 5,
      adoption_id: 'adopted',
    } as never);
    show();
    fireEvent.click(await screen.findByRole('button', { name: '个人库' }));
    fireEvent.click(await screen.findByRole('button', { name: '采用到项目' }));
    await screen.findByText('发布预览');
    expect(api.postAgentOperation).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: '采用到当前项目' }));
    await screen.findByText('人物主创 · 已保存项目修订 5');
    expect(api.postAgentOperation).toHaveBeenCalledWith(
      'p',
      'project',
      expect.objectContaining({
        action: 'adopt',
        payload: { ref: { id: 'personal', revision: 1 }, proof: 'fixed' },
      }),
    );
  });
  it('recovers a persisted pending request in the actual reopened panel without republishing', async () => {
    vi.mocked(api.postAgentOperation).mockRejectedValue(new Error('lost response'));
    vi.mocked(api.getAgentReceipt).mockRejectedValue(new Error('offline'));
    await createAgentOperation(owner, () => true).run({
      channel: 'personal',
      label: '保存中的配置',
      request: {
        request_id: 'recover-ui',
        action: 'save',
        payload: { configuration: config, proof: 'preview', expected_revision: 0 },
      },
    });
    vi.mocked(api.getAgentReceipt).mockResolvedValue({ id: 'personal', revision: 3 });
    show();
    fireEvent.click(await screen.findByRole('button', { name: '恢复原请求' }));
    await screen.findByText('保存中的配置 · 已保存个人 v3');
    expect(api.postAgentOperation).toHaveBeenCalledTimes(1);
    expect(api.getAgentReceipt).toHaveBeenLastCalledWith('p', 'personal', 'recover-ui');
  });
});
