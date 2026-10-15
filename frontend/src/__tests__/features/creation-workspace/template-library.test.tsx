import { beforeEach, describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import * as api from '@/api/creativeTemplates';
import { TemplateLibraryPanel } from '@/features/template-library/TemplateLibraryPanel';
import {
  createTemplateOperation,
  finishTemplateIntent,
  readTemplateIntent,
} from '@/features/template-library/templateOperation';
import { TemplateLibraryEntry } from '@/features/template-library/TemplateLibraryEntry';
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
vi.mock('@/features/freezone/canvasSyncRuntime', () => ({ captureFreezoneCanvasRuntime: vi.fn() }));
vi.mock('@/api/creativeTemplates', () => ({
  listPersonalTemplates: vi.fn(),
  listTemplateAdoptions: vi.fn(),
  getPersonalTemplate: vi.fn(),
  getTemplateAdoption: vi.fn(),
  previewTemplate: vi.fn(),
  previewTemplatePatch: vi.fn(),
  previewTemplateAdoption: vi.fn(),
  previewTemplateOverride: vi.fn(),
  previewTemplateUpgrade: vi.fn(),
  previewTemplateActivation: vi.fn(),
  postTemplateOperation: vi.fn(),
  getTemplateReceipt: vi.fn(),
  listTemplateSources: vi.fn(),
}));
const owner = { user: 'u', project: 'p', canvas: 'c' },
  runtime = { flush: vi.fn(async () => true), isCurrent: () => true };
const configuration = {
  name: '线索模板',
  description: '模板说明',
  definitions: [],
  constraints: [],
  agents: [],
  examples: [],
  relations: [],
};
const fixed = { id: 't', revision: 1, proof: 'fixed', configuration };
const adoption = {
  adoption_id: 'a',
  revision: 1,
  fixed,
  effective: configuration,
  overrides: {},
  source: { user_id: 'u', ref: { id: 't', revision: 1 } },
  definition_mapping: {},
  constraint_drafts: [],
  example_drafts: [],
  relation_drafts: [],
};
const sources = { definitions: [], constraints: [], agents: [], objects: [], relations: [] };
beforeEach(() => {
  localStorage.clear();
  vi.resetAllMocks();
  runtime.flush.mockResolvedValue(true);
  vi.mocked(api.listPersonalTemplates).mockResolvedValue([fixed]);
  vi.mocked(api.listTemplateAdoptions).mockResolvedValue([adoption]);
  vi.mocked(api.listTemplateSources).mockResolvedValue(sources);
  vi.mocked(api.previewTemplate).mockResolvedValue({ configuration, proof: 'preview' });
  vi.mocked(api.postTemplateOperation).mockResolvedValue({ id: 't', revision: 2 });
  vi.mocked(api.getTemplateReceipt).mockResolvedValue({ status: 'unknown' });
  useAuthStore.setState({ username: 'u' });
  vi.mocked(captureFreezoneCanvasRuntime).mockReturnValue({ ...runtime, run: vi.fn() });
  navigation.state.location = { pathname: '/projects/p', search: {} };
  const held = new Set<string>();
  Object.defineProperty(navigator, 'locks', {
    configurable: true,
    value: {
      request: vi.fn(
        async (name: string, _options: unknown, callback: (lock: unknown) => unknown) => {
          if (held.has(name)) return callback(null);
          held.add(name);
          try {
            return await callback({ name });
          } finally {
            held.delete(name);
          }
        },
      ),
    },
  });
});
const show = () =>
  render(<TemplateLibraryPanel owner={owner} runtime={runtime} isCurrent={() => true} />);
describe('template library', () => {
  it('starts with no examples, previews actual content read-only, then explicitly publishes', async () => {
    show();
    fireEvent.click(await screen.findByRole('button', { name: '个人模板' }));
    fireEvent.click(await screen.findByRole('button', { name: '新建模板' }));
    fireEvent.change(screen.getByLabelText('模板名称'), { target: { value: '线索模板' } });
    fireEvent.change(screen.getByLabelText('模板说明'), { target: { value: '模板说明' } });
    fireEvent.click(screen.getByRole('button', { name: '预览发布内容' }));
    await screen.findByRole('heading', { name: '本次实际发布内容' });
    expect(api.postTemplateOperation).not.toHaveBeenCalled();
    expect(api.previewTemplate).toHaveBeenCalledWith(
      'p',
      expect.objectContaining({ examples: [], relations: [] }),
    );
    fireEvent.click(screen.getByRole('button', { name: '保存个人模板版本' }));
    await waitFor(() =>
      expect(api.postTemplateOperation).toHaveBeenCalledWith(
        'p',
        'personal',
        expect.objectContaining({
          action: 'save',
          payload: expect.objectContaining({ expected_revision: 0, proof: 'preview' }),
        }),
      ),
    );
  });
  it('patches only name and description without resolving unchanged fixed dependencies', async () => {
    vi.mocked(api.previewTemplatePatch).mockResolvedValue({
      configuration: { ...configuration, description: '新版说明' },
      proof: 'patch',
    });
    show();
    fireEvent.click(await screen.findByRole('button', { name: '个人模板' }));
    fireEvent.click(await screen.findByRole('button', { name: '编辑名称与说明 线索模板' }));
    fireEvent.change(screen.getByLabelText('模板说明'), { target: { value: '新版说明' } });
    fireEvent.click(screen.getByRole('button', { name: '预览发布内容' }));
    await screen.findByRole('heading', { name: '本次实际发布内容' });
    expect(api.previewTemplatePatch).toHaveBeenCalledWith(
      'p',
      { id: 't', revision: 1 },
      { description: '新版说明' },
    );
    expect(api.previewTemplate).not.toHaveBeenCalled();
  });
  it('persists unknown identity and recovers the original personal receipt without duplicate publish', async () => {
    vi.mocked(api.postTemplateOperation).mockRejectedValue(new Error('lost'));
    vi.mocked(api.getTemplateReceipt).mockRejectedValue(new Error('offline'));
    const input = {
      channel: 'personal' as const,
      label: '模板',
      request: { request_id: 'same', action: 'save', payload: {} },
    };
    expect((await createTemplateOperation(owner, () => true).run(input)).status).toBe('pending');
    expect(readTemplateIntent(owner)?.request.request_id).toBe('same');
    vi.mocked(api.getTemplateReceipt).mockResolvedValue({ id: 't', revision: 1 });
    show();
    fireEvent.click(await screen.findByRole('button', { name: '恢复原请求' }));
    await screen.findByText('模板 · 已保存个人 v1');
    expect(api.postTemplateOperation).toHaveBeenCalledTimes(1);
  });
});
const definition = {
  name: 'clue',
  category: 'object' as const,
  current_revision: 1,
  active_revision: 1,
  revision: 1,
  definition: {
    name: 'clue',
    title: '线索',
    category: 'object' as const,
    fields: { text: { type: 'string' as const, title: '线索内容' } },
  },
  source: { kind: 'user', actor_id: 'u' },
  confirmations: [],
};
const object = (id: string, text: string) => ({
  object_id: id,
  revision: 1,
  kind: 'custom:clue',
  definition_name: 'clue',
  schema_revision: 1,
  scope: { level: 'project' as const },
  title: '线索',
  data: { text },
});
const origin = { project_id: 'A', name: 'clue', revision: 1 };
const withDefinition = {
  ...configuration,
  definitions: [{ key: 'clue@1', definition: definition.definition, origin, was_confirmed: true }],
};
const plan = {
  ref: { id: 't', revision: 1 },
  proof: 'install',
  definition_names: {},
  definitions: [
    {
      key: 'clue@1',
      definition: definition.definition,
      expected_revision: 0,
      revision: 1,
      reuse: false,
      active_revision: 0,
    },
  ],
  conflicts: [],
};
async function personalTab() {
  fireEvent.click(await screen.findByRole('button', { name: '个人模板' }));
  await waitFor(() => expect(screen.getByRole('button', { name: '新建模板' })).toBeEnabled());
}
describe('explicit portable contents and project lifecycle', () => {
  it('does not silently select relation endpoints or portable local constraints', async () => {
    vi.mocked(api.listTemplateSources).mockResolvedValue({
      ...sources,
      definitions: [definition],
      objects: [object('one', '雨夜信封'), object('two', '售票记录')],
      relations: [
        {
          relation_id: 'rel',
          source_id: 'one',
          target_id: 'two',
          definition_name: 'link',
          schema_revision: 1,
          title: '相互印证',
        },
      ],
      constraints: [
        {
          constraint_id: 'local',
          revision: 1,
          key: '节奏',
          value: '慢',
          level: 'hard',
          scope: { level: 'canvas', canvas_id: 'c' },
          citation: '作者',
          source: { kind: 'user', actor_id: 'u' },
        },
      ],
    });
    show();
    await personalTab();
    fireEvent.click(screen.getByRole('button', { name: '新建模板' }));
    fireEvent.change(screen.getByLabelText('模板名称'), { target: { value: '例子' } });
    fireEvent.change(screen.getByLabelText('模板说明'), { target: { value: '说明' } });
    expect(screen.getByRole('checkbox', { name: /节奏/ })).toBeDisabled();
    fireEvent.click(screen.getByRole('checkbox', { name: /相互印证/ }));
    expect(screen.getByRole('checkbox', { name: /雨夜信封.*第1版/ })).not.toBeChecked();
    expect(screen.getByRole('button', { name: '预览发布内容' })).toBeDisabled();
    expect(api.previewTemplate).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('checkbox', { name: /雨夜信封.*第1版/ }));
    fireEvent.click(screen.getByRole('checkbox', { name: /售票记录.*第1版/ }));
    fireEvent.click(screen.getByRole('button', { name: '预览发布内容' }));
    await waitFor(() =>
      expect(api.previewTemplate).toHaveBeenCalledWith(
        'p',
        expect.objectContaining({
          examples: [
            { id: 'one', revision: 1 },
            { id: 'two', revision: 1 },
          ],
          relations: ['rel'],
        }),
      ),
    );
  });
  it('requires explicit mapping after a same-name definition conflict and never auto adopts', async () => {
    vi.mocked(api.listPersonalTemplates).mockResolvedValue([
      { ...fixed, configuration: withDefinition },
    ]);
    vi.mocked(api.previewTemplateAdoption)
      .mockResolvedValueOnce({
        ...plan,
        conflicts: [{ source_key: 'clue@1', target_name: 'clue', existing_revision: 2 }],
      })
      .mockResolvedValue({ ...plan, proof: 'mapped' });
    vi.mocked(api.postTemplateOperation).mockResolvedValue({
      status: 'applied',
      adoption_id: 'new',
      revision: 1,
      request_id: 'r',
    });
    show();
    await personalTab();
    fireEvent.click(screen.getByRole('button', { name: '采用 线索模板' }));
    fireEvent.click(screen.getByRole('button', { name: '比较目标与依赖' }));
    await screen.findByRole('alert');
    expect(screen.getByRole('button', { name: '采用到本项目（保持草稿）' })).toBeDisabled();
    expect(api.postTemplateOperation).not.toHaveBeenCalled();
    fireEvent.change(screen.getByRole('combobox', { name: '线索' }), {
      target: { value: 'independent' },
    });
    expect(
      screen.queryByRole('button', { name: '采用到本项目（保持草稿）' }),
    ).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '比较目标与依赖' }));
    await waitFor(() =>
      expect(screen.getByRole('button', { name: '采用到本项目（保持草稿）' })).toBeEnabled(),
    );
    fireEvent.click(screen.getByRole('button', { name: '采用到本项目（保持草稿）' }));
    await waitFor(() =>
      expect(api.postTemplateOperation).toHaveBeenCalledWith(
        'p',
        'project',
        expect.objectContaining({
          action: 'adopt',
          payload: expect.objectContaining({
            proof: 'mapped',
            definition_names: { 'clue@1': expect.stringMatching(/^template_/) },
          }),
        }),
      ),
    );
  });
  it('activates exact frozen definitions and both examples in one atomic command, leaving agreements draft', async () => {
    const activation = {
      adoption_id: 'a',
      expected_revision: 1,
      definitions: [definition],
      examples: [
        {
          id: 'x',
          definition: { name: 'clue', revision: 1 },
          data: { text: '雨夜信封' },
          origin: { project_id: 'A', id: 'one', revision: 1 },
        },
        {
          id: 'y',
          definition: { name: 'clue', revision: 1 },
          data: { text: '售票记录' },
          origin: { project_id: 'A', id: 'two', revision: 1 },
        },
      ],
      relations: [
        {
          id: 'r',
          definition: { name: 'link', revision: 1 },
          data: {},
          source_id: 'x',
          target_id: 'y',
          origin: { project_id: 'A', id: 'rel' },
        },
      ],
      proof: 'activate',
      notice: '',
    };
    vi.mocked(api.previewTemplateActivation).mockResolvedValue(activation);
    vi.mocked(api.postTemplateOperation).mockResolvedValue({
      status: 'applied',
      adoption_id: 'a',
      revision: 2,
      request_id: 'r',
    });
    show();
    const button = await screen.findByRole('button', { name: '预览启用与示例' });
    await waitFor(() => expect(button).toBeEnabled());
    fireEvent.click(button);
    await screen.findByText('雨夜信封');
    expect(screen.getByText('售票记录')).toBeVisible();
    expect(api.postTemplateOperation).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText('启用依据'), {
      target: { value: '明确采用此示例结构' },
    });
    fireEvent.click(screen.getByRole('button', { name: '启用所列定义并创建示例' }));
    await waitFor(() => expect(api.postTemplateOperation).toHaveBeenCalledTimes(1));
    expect(api.postTemplateOperation).toHaveBeenCalledWith(
      'p',
      'project',
      expect.objectContaining({
        action: 'activate_examples',
        payload: {
          adoption_id: 'a',
          expected_revision: 1,
          definitions: [{ name: 'clue', revision: 1 }],
          basis: '明确采用此示例结构',
          proof: 'activate',
        },
      }),
    );
  });
  it('shows project override B and final keep B/drop C before upgrade, retaining non-conflicting overrides', async () => {
    const overrides = {
      name: { value: '项目名称B', base: '个人名称A' },
      description: { value: '项目说明', base: '原说明' },
    };
    const before = { ...configuration, name: '个人名称A' },
      after = { ...configuration, name: '个人名称C' };
    vi.mocked(api.listPersonalTemplates).mockResolvedValue([
      { ...fixed, revision: 2, configuration: after },
    ]);
    vi.mocked(api.listTemplateAdoptions).mockResolvedValue([
      {
        ...adoption,
        effective: { ...configuration, name: '项目名称B', description: '项目说明' },
        overrides,
      },
    ]);
    vi.mocked(api.previewTemplateUpgrade).mockResolvedValue({
      adoption_id: 'a',
      expected_revision: 1,
      before,
      after,
      overrides,
      conflicts: ['name'],
      ref: { id: 't', revision: 2 },
      proof: 'upgrade',
      installation: plan,
      existing_constraint_drafts: [],
      notice: '',
    });
    show();
    const btn = await screen.findByRole('button', { name: '比较新版 v2' });
    await waitFor(() => expect(btn).toBeEnabled());
    fireEvent.click(btn);
    fireEvent.click(screen.getByRole('button', { name: '比较目标与依赖' }));
    await screen.findByText('当前项目覆盖：项目名称B');
    expect(screen.getByRole('button', { name: '按所选处理升级' })).toBeDisabled();
    fireEvent.change(screen.getByLabelText('名称冲突处理'), { target: { value: 'keep' } });
    expect(screen.getAllByRole('heading', { name: '项目名称B' })).toHaveLength(2);
    expect(screen.getAllByText('项目说明').length).toBeGreaterThanOrEqual(2);
    fireEvent.change(screen.getByLabelText('名称冲突处理'), { target: { value: 'drop' } });
    expect(screen.getAllByRole('heading', { name: '个人名称C' })).toHaveLength(2);
    expect(screen.getAllByRole('heading', { name: '项目名称B' })).toHaveLength(1);
    expect(api.postTemplateOperation).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: '按所选处理升级' }));
    await waitFor(() =>
      expect(api.postTemplateOperation).toHaveBeenCalledWith(
        'p',
        'project',
        expect.objectContaining({
          action: 'upgrade',
          payload: expect.objectContaining({
            expected_revision: 1,
            proof: 'upgrade',
            resolutions: { name: 'drop' },
          }),
        }),
      ),
    );
  });
  it('cancels only the chosen project override through null and an exact revision preview', async () => {
    const a = {
      ...adoption,
      effective: { ...configuration, description: '本地说明' },
      overrides: { description: { value: '本地说明', base: '模板说明' } },
    };
    vi.mocked(api.listTemplateAdoptions).mockResolvedValue([a]);
    vi.mocked(api.previewTemplateOverride).mockResolvedValue({
      adoption_id: 'a',
      expected_revision: 1,
      before: a.effective,
      changes: { description: null },
      proof: 'override',
    });
    show();
    const b = await screen.findByRole('button', { name: '调整本项目名称与说明' });
    await waitFor(() => expect(b).toBeEnabled());
    fireEvent.click(b);
    fireEvent.click(screen.getByRole('checkbox', { name: '取消说明覆盖，使用采用版本' }));
    fireEvent.click(screen.getByRole('button', { name: '预览项目调整' }));
    await screen.findByRole('heading', { name: '本次项目有效内容' });
    expect(api.previewTemplateOverride).toHaveBeenCalledWith('p', 'a', { description: null });
    fireEvent.click(screen.getByRole('button', { name: '保存项目调整' }));
    await waitFor(() =>
      expect(api.postTemplateOperation).toHaveBeenCalledWith(
        'p',
        'project',
        expect.objectContaining({
          action: 'override',
          payload: {
            adoption_id: 'a',
            expected_revision: 1,
            changes: { description: null },
            proof: 'override',
          },
        }),
      ),
    );
  });
  it('refreshes a conflicted personal catalog in the same dialog while retaining the original draft CAS', async () => {
    vi.mocked(api.previewTemplatePatch).mockResolvedValue({
      configuration: { ...configuration, description: '我的草稿' },
      proof: 'old',
    });
    vi.mocked(api.postTemplateOperation).mockRejectedValue(
      Object.assign(new Error('版本已改变'), { code: 'revision_conflict' }),
    );
    show();
    await personalTab();
    fireEvent.click(screen.getByRole('button', { name: '编辑名称与说明 线索模板' }));
    fireEvent.change(screen.getByLabelText('模板说明'), { target: { value: '我的草稿' } });
    fireEvent.click(screen.getByRole('button', { name: '预览发布内容' }));
    fireEvent.click(await screen.findByRole('button', { name: '保存个人模板版本' }));
    await screen.findByText('线索模板 · 请求未应用');
    vi.mocked(api.listPersonalTemplates).mockResolvedValue([
      { ...fixed, revision: 2, configuration: { ...configuration, description: '别人的新版' } },
    ]);
    fireEvent.click(await screen.findByRole('button', { name: '保留记录，继续比较' }));
    await waitFor(() =>
      expect(screen.getByRole('button', { name: '恢复原草稿 线索模板' })).toBeEnabled(),
    );
    fireEvent.click(screen.getByRole('button', { name: '恢复原草稿 线索模板' }));
    expect(screen.getByLabelText('模板说明')).toHaveValue('我的草稿');
    fireEvent.click(screen.getByRole('button', { name: '预览发布内容' }));
    await waitFor(() =>
      expect(api.previewTemplatePatch).toHaveBeenLastCalledWith(
        'p',
        { id: 't', revision: 1 },
        { description: '我的草稿' },
      ),
    );
    expect(screen.getByText(/已有新版本 2/)).toBeInTheDocument();
  });
  it('rejects a failed flush before any catalog or preview', async () => {
    runtime.flush.mockResolvedValue(false);
    show();
    await screen.findByRole('alert');
    expect(api.listPersonalTemplates).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: '项目模板' })).toBeEnabled();
  });
  it('does not apply a late result into a detached workspace but persists its original receipt', async () => {
    let current = true;
    let resolve!: (r: api.TemplateResult) => void;
    vi.mocked(api.postTemplateOperation).mockReturnValue(
      new Promise((r) => {
        resolve = r;
      }),
    );
    const input = {
      channel: 'project' as const,
      label: '采用',
      request: { request_id: 'late', action: 'adopt', payload: {} },
    };
    const promise = createTemplateOperation(owner, () => current).run(input);
    await waitFor(() => expect(api.postTemplateOperation).toHaveBeenCalled());
    current = false;
    resolve({ status: 'applied', adoption_id: 'a', revision: 2, request_id: 'late' });
    expect((await promise).status).toBe('detached');
    expect(readTemplateIntent(owner)?.phase).toBe('applied');
    expect(readTemplateIntent({ ...owner, user: 'other' })).toBeNull();
  });
  it('does not issue writes when durable storage fails', async () => {
    const spy = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('quota');
    });
    try {
      const response = await createTemplateOperation(owner, () => true).run({
        channel: 'personal',
        label: 'x',
        request: { request_id: 'no-storage', action: 'copy', payload: {} },
      });
      expect(response.status).toBe('failed');
      expect(api.postTemplateOperation).not.toHaveBeenCalled();
    } finally {
      spy.mockRestore();
    }
  });
  it('refreshes a conflicted project catalog in the same dialog before explicit draft rebase', async () => {
    vi.mocked(api.previewTemplateOverride).mockResolvedValue({
      adoption_id: 'a',
      expected_revision: 1,
      before: configuration,
      changes: { name: '项目草稿' },
      proof: 'old',
    });
    vi.mocked(api.postTemplateOperation).mockRejectedValue(
      Object.assign(new Error('项目版本已改变'), { code: 'revision_conflict' }),
    );
    show();
    const edit = await screen.findByRole('button', { name: '调整本项目名称与说明' });
    await waitFor(() => expect(edit).toBeEnabled());
    fireEvent.click(edit);
    fireEvent.change(screen.getByLabelText('模板名称'), { target: { value: '项目草稿' } });
    fireEvent.click(screen.getByRole('button', { name: '预览项目调整' }));
    fireEvent.click(await screen.findByRole('button', { name: '保存项目调整' }));
    await screen.findByText('项目草稿 · 请求未应用');
    vi.mocked(api.listTemplateAdoptions).mockResolvedValue([
      { ...adoption, revision: 2, effective: { ...configuration, name: '外部项目更新' } },
    ]);
    fireEvent.click(await screen.findByRole('button', { name: '保留记录，继续比较' }));
    const restore = await screen.findByRole('button', { name: '恢复原草稿 项目草稿' });
    await waitFor(() => expect(restore).toBeEnabled());
    fireEvent.click(restore);
    expect(screen.getByLabelText('模板名称')).toHaveValue('项目草稿');
    expect(screen.getByText('项目草稿 · 原项目修订 1')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '以最新版本重新比较此草稿' }));
    vi.mocked(api.previewTemplateOverride).mockResolvedValue({
      adoption_id: 'a',
      expected_revision: 2,
      before: configuration,
      changes: { name: '项目草稿' },
      proof: 'new',
    });
    fireEvent.click(screen.getByRole('button', { name: '预览项目调整' }));
    await waitFor(() =>
      expect(api.previewTemplateOverride).toHaveBeenLastCalledWith('p', 'a', { name: '项目草稿' }),
    );
    fireEvent.click(await screen.findByRole('button', { name: '保存项目调整' }));
    await waitFor(() =>
      expect(api.postTemplateOperation).toHaveBeenLastCalledWith(
        'p',
        'project',
        expect.objectContaining({
          payload: expect.objectContaining({ expected_revision: 2, proof: 'new' }),
        }),
      ),
    );
  });
  it('reselects template content explicitly for a new personal version instead of resolving fixed dependencies on edit', async () => {
    show();
    await personalTab();
    fireEvent.click(screen.getByRole('button', { name: '重新选择内容发布新版' }));
    fireEvent.click(screen.getByRole('button', { name: '预览发布内容' }));
    await screen.findByRole('heading', { name: '本次实际发布内容' });
    expect(api.previewTemplate).toHaveBeenCalledWith(
      'p',
      expect.objectContaining({ definitions: [], examples: [], relations: [] }),
    );
    expect(api.previewTemplatePatch).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: '保存个人模板版本' }));
    await waitFor(() =>
      expect(api.postTemplateOperation).toHaveBeenCalledWith(
        'p',
        'personal',
        expect.objectContaining({
          action: 'save',
          payload: expect.objectContaining({ id: 't', expected_revision: 1, proof: 'preview' }),
        }),
      ),
    );
  });
  it('preserves project unknown request id and refuses a different command until recovery', async () => {
    vi.mocked(api.postTemplateOperation).mockRejectedValue(new Error('lost'));
    vi.mocked(api.getTemplateReceipt).mockRejectedValue(new Error('offline'));
    const old = {
      channel: 'project' as const,
      label: '采用',
      request: { request_id: 'old', action: 'adopt', payload: { ref: { id: 't', revision: 1 } } },
    };
    const op = createTemplateOperation(owner, () => true);
    expect((await op.run(old)).status).toBe('pending');
    expect((await op.run({ ...old, request: { ...old.request, request_id: 'new' } })).status).toBe(
      'busy',
    );
    vi.mocked(api.getTemplateReceipt).mockResolvedValue({
      status: 'applied',
      adoption_id: 'a',
      revision: 1,
      request_id: 'old',
    });
    expect((await op.run()).status).toBe('applied');
    expect(api.postTemplateOperation).toHaveBeenCalledTimes(1);
    expect(readTemplateIntent(owner)?.request).toEqual(old.request);
  });
  it('freezes Entry account, location and runtime so late previews cannot reopen a new workspace', async () => {
    let resolve!: (value: api.TemplatePreview) => void;
    vi.mocked(api.previewTemplatePatch).mockReturnValue(
      new Promise((r) => {
        resolve = r;
      }),
    );
    const view = render(<TemplateLibraryEntry project="p" canvasId="c" />);
    fireEvent.click(screen.getByRole('button', { name: '创作模板' }));
    await personalTab();
    fireEvent.click(screen.getByRole('button', { name: '编辑名称与说明 线索模板' }));
    fireEvent.change(screen.getByLabelText('模板说明'), { target: { value: '旧页面草稿' } });
    fireEvent.click(screen.getByRole('button', { name: '预览发布内容' }));
    await waitFor(() => expect(api.previewTemplatePatch).toHaveBeenCalled());
    act(() => useAuthStore.setState({ username: 'other' }));
    await act(async () => {
      resolve({ configuration, proof: 'late' });
    });
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(api.postTemplateOperation).not.toHaveBeenCalled();
    act(() => useAuthStore.setState({ username: 'u' }));
    fireEvent.click(screen.getByRole('button', { name: '创作模板' }));
    await screen.findByRole('button', { name: '恢复原草稿 线索模板' });
    expect(screen.queryByRole('heading', { name: '本次实际发布内容' })).not.toBeInTheDocument();
    navigation.state.location = { pathname: '/projects/p', search: {} };
    view.rerender(<TemplateLibraryEntry project="p" canvasId="c" />);
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  });
});

describe('cross-tab template request ownership', () => {
  const input = {
    channel: 'personal' as const,
    label: '固定模板',
    request: { request_id: 'tab-a', action: 'copy', payload: { ref: { id: 't', revision: 1 } } },
  };
  it('serializes two independently loaded controllers through the same browser lock', async () => {
    vi.resetModules();
    const tabA = await import('@/features/template-library/templateOperation');
    vi.resetModules();
    const tabB = await import('@/features/template-library/templateOperation');
    let release!: (value: api.TemplateResult) => void;
    vi.mocked(api.postTemplateOperation).mockReturnValue(
      new Promise((r) => {
        release = r;
      }),
    );
    const first = tabA.createTemplateOperation(owner, () => true).run(input);
    await waitFor(() => expect(api.postTemplateOperation).toHaveBeenCalledTimes(1));
    const second = tabB.createTemplateOperation(owner, () => true).run(input);
    await new Promise((r) => setTimeout(r, 0));
    const count = vi.mocked(api.postTemplateOperation).mock.calls.length;
    release({ id: 't', revision: 2 });
    expect((await second).status).toBe('busy');
    expect(count).toBe(1);
    expect((await first).status).toBe('applied');
  });
  it('rejects an old terminal finish after another tab starts a new pending request', async () => {
    await createTemplateOperation(owner, () => true).run(input);
    await Reflect.apply(finishTemplateIntent, null, [owner, 'tab-a', () => true]);
    vi.mocked(api.postTemplateOperation).mockRejectedValue(new Error('lost'));
    vi.mocked(api.getTemplateReceipt).mockRejectedValue(new Error('offline'));
    const next = { ...input, request: { ...input.request, request_id: 'tab-b' } };
    await createTemplateOperation(owner, () => true).run(next);
    await expect(
      Promise.resolve().then(() =>
        Reflect.apply(finishTemplateIntent, null, [owner, 'tab-a', () => true]),
      ),
    ).rejects.toThrow();
    expect(readTemplateIntent(owner)?.request.request_id).toBe('tab-b');
    expect(readTemplateIntent(owner)?.phase).toBe('pending');
  });
  it('rejects stale finish A when another tab has completed B', async () => {
    await createTemplateOperation(owner, () => true).run(input);
    await finishTemplateIntent(owner, 'tab-a', () => true);
    await createTemplateOperation(owner, () => true).run({
      ...input,
      request: { ...input.request, request_id: 'tab-b' },
    });
    await expect(finishTemplateIntent(owner, 'tab-a', () => true)).rejects.toThrow('另一标签更新');
    expect(readTemplateIntent(owner)?.request.request_id).toBe('tab-b');
    expect(readTemplateIntent(owner)?.phase).toBe('applied');
  });
  it('rejects finish while another tab holds the run lock without deleting its journal', async () => {
    vi.resetModules();
    const tab = await import('@/features/template-library/templateOperation');
    let release!: (value: api.TemplateResult) => void;
    vi.mocked(api.postTemplateOperation).mockReturnValue(
      new Promise((r) => {
        release = r;
      }),
    );
    const running = createTemplateOperation(owner, () => true).run(input);
    await waitFor(() => expect(api.postTemplateOperation).toHaveBeenCalledTimes(1));
    await expect(tab.finishTemplateIntent(owner, 'tab-a', () => true)).rejects.toThrow(
      '另一标签正在处理',
    );
    expect(readTemplateIntent(owner)?.phase).toBe('pending');
    release({ id: 't', revision: 2 });
    expect((await running).status).toBe('applied');
  });
  it('fails closed without Web Locks and never posts or clears a receipt', async () => {
    await createTemplateOperation(owner, () => true).run(input);
    const before = readTemplateIntent(owner);
    vi.mocked(api.postTemplateOperation).mockClear();
    Object.defineProperty(navigator, 'locks', { configurable: true, value: undefined });
    expect(
      (await createTemplateOperation({ ...owner, canvas: 'different' }, () => true).run(input))
        .status,
    ).toBe('failed');
    await expect(
      Promise.resolve().then(() =>
        Reflect.apply(finishTemplateIntent, null, [owner, 'tab-a', () => true]),
      ),
    ).rejects.toThrow();
    expect(api.postTemplateOperation).not.toHaveBeenCalled();
    expect(readTemplateIntent(owner)).toEqual(before);
  });
  it('rechecks active identity inside the acquired lock before writing', async () => {
    let current = true;
    Object.defineProperty(navigator, 'locks', {
      configurable: true,
      value: {
        request: async (_n: string, _o: unknown, callback: (lock: unknown) => unknown) => {
          current = false;
          return callback({ name: 'lock' });
        },
      },
    });
    expect((await createTemplateOperation(owner, () => current).run(input)).status).toBe(
      'detached',
    );
    expect(api.postTemplateOperation).not.toHaveBeenCalled();
    expect(readTemplateIntent(owner)).toBeNull();
  });
});
