import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { I18nextProvider, initReactI18next } from 'react-i18next';
import i18next from 'i18next';
import { http, HttpResponse } from 'msw';
import type { ReactNode } from 'react';
import type { NodeProps } from '@xyflow/react';
import { beforeEach, expect, it, vi } from 'vitest';
import { server } from '@/__mocks__/msw/server';
import { CANVAS_NODE_TYPES } from '@/features/canvas/domain/canvasNodes';
import { useCanvasStore } from '@/stores/canvasStore';

const mocks = vi.hoisted(() => ({ apiCall: vi.fn() }));

vi.mock('@/api/client', () => ({ apiCall: mocks.apiCall }));
vi.mock('@/lib/url-params', () => ({ readUrl: () => ({ project: 'demo' }) }));
vi.mock('@/hooks/use-task-controller', () => ({
  useTaskController: () => ({ started: false, start: vi.fn(), stop: vi.fn(), stopping: false, stream: {}, logs: [] }),
}));
vi.mock('@/lib/api', async () => {
  const ky = (await import('ky')).default;
  return { api: ky.create({ baseUrl: 'http://localhost:3000/' }) };
});
vi.mock('@/features/canvas/production/ProductionNodeShell', () => ({
  ProductionNodeShell: ({ children }: {
    children?: ReactNode | ((controls: { requestClose: () => void; forceClose: () => void }) => ReactNode);
  }) => <div>{typeof children === 'function' ? children({ requestClose: vi.fn(), forceClose: vi.fn() }) : children}</div>,
}));
vi.mock('@/features/canvas/production/workspace/ProductionWorkspaceLayout', () => ({
  ProductionWorkspaceLayout: ({ title, navigation, children, inspector, actions }: {
    title: string; navigation: ReactNode; children: ReactNode; inspector?: ReactNode; actions?: ReactNode;
  }) => <div className="production-workspace-layout" role="dialog" aria-label={title}>
    <nav aria-label="角色选择">{navigation}</nav><main>{children}</main><aside>{inspector}</aside><footer>{actions}</footer>
  </div>,
}));

import { CharacterVoiceNode } from '@/features/canvas/nodes/CharacterVoiceNode';

function CanvasBoundCharacterVoiceNode({ id }: { id: string }) {
  const data = useCanvasStore(state => state.nodes.find(node => node.id === id)?.data);
  return data ? <CharacterVoiceNode {...({ id, data } as NodeProps)} /> : null;
}

const i18n = i18next.createInstance();
await i18n.use(initReactI18next).init({
  lng: 'zh', fallbackLng: 'zh',
  resources: {
    zh: { translation: { characters: {
      voiceSamples: {
        title: '声线管理', hint: '声线管理', loading: '正在读取声线样本', loadFailed: '读取声线样本失败',
        defaultRequired: '默认（必填）', ageDefaultRequired: '{{age}}（默认 · 必填）',
        optionalOverride: '{{age}}（可选覆盖）', missingDefault: '未配置', inheritedDefault: '继承默认',
        missing: '未配置', upload: '上传声音样本', record: '录音', design: 'AI 设计音色',
        designed: '音色设计完成', designQueued: '音色设计任务已进入队列', trim: '裁剪', clear: '清除',
      },
      ageGroups: { child: '幼年', young: '青年', middle: '中年', elder: '老年' },
    } } },
  },
  interpolation: { escapeValue: false },
});

function renderNode(client = new QueryClient({ defaultOptions: { queries: { retry: false } } })) {
  const view = render(
    <I18nextProvider i18n={i18n}>
      <QueryClientProvider client={client}><CanvasBoundCharacterVoiceNode id="voice-1" /></QueryClientProvider>
    </I18nextProvider>,
  );
  return { ...view, client };
}

const characters = [
  { name: 'integration-A', character_id: 'character-A', voice_facts: { vocalization_mode: 'dialogue' } },
  { name: 'integration-B', character_id: 'character-B', voice_facts: { vocalization_mode: 'dialogue' } },
];
let activeCharacters = characters;

function setCharacterPrefix(prefix: string) {
  activeCharacters = [
    { name: `${prefix}-A`, character_id: 'character-A', voice_facts: { vocalization_mode: 'dialogue' } },
    { name: `${prefix}-B`, character_id: 'character-B', voice_facts: { vocalization_mode: 'dialogue' } },
  ];
  act(() => useCanvasStore.getState().setCanvasData([
    { id: 'voice-1', type: CANVAS_NODE_TYPES.characterVoice, position: { x: 0, y: 0 }, data: {
      characterId: 'character-A', characterName: `${prefix}-A`,
      voiceDesign: { voice_spec: { texture: 'A 原始音色' }, performance: {} },
      voiceDescription: 'A 原始描述', voiceDescriptionDraft: '', auditionText: 'A 原始台词', voicePerceivedAge: '',
    } } as never,
  ], []));
  return activeCharacters;
}

beforeEach(() => {
  vi.clearAllMocks();
  activeCharacters = characters;
  useCanvasStore.getState().setCanvasData([
    { id: 'voice-1', type: CANVAS_NODE_TYPES.characterVoice, position: { x: 0, y: 0 }, data: {
      characterId: 'character-A', characterName: 'integration-A',
      voiceDesign: { voice_spec: { texture: 'A 原始音色' }, performance: {} },
      voiceDescription: 'A 原始描述', voiceDescriptionDraft: '', auditionText: 'A 原始台词', voicePerceivedAge: '',
    } } as never,
  ], []);
  mocks.apiCall.mockResolvedValue({ character_id: 'character-A', versions: [], current: [] });
  server.use(
    http.get('http://localhost:3000/api/v1/projects/demo', () => HttpResponse.json({ ok: true, data: {} })),
    http.get('http://localhost:3000/api/v1/projects/demo/characters', () => HttpResponse.json({ ok: true, data: activeCharacters })),
    http.get('http://localhost:3000/api/v1/projects/demo/characters/:name/voice-samples', ({ params }) => HttpResponse.json({
      ok: true, data: { character: String(params.name), slots: [], voice_facts: { vocalization_mode: 'dialogue' } },
    })),
    http.post('http://localhost:3000/api/v1/projects/demo/characters/:name/voice-recommendation', () =>
      HttpResponse.json({ ok: false, error: '推荐暂不可用' }, { status: 503 })),
  );
});

it('keeps each real panel draft through A→B→A and after reopening the node', async () => {
  const view = renderNode();
  await screen.findByRole('textbox', { name: '音色质感' });
  fireEvent.change(screen.getByRole('textbox', { name: '音色质感' }), { target: { value: 'A 编辑后的音色' } });
  fireEvent.change(screen.getByRole('textbox', { name: '自定义声音描述' }), { target: { value: 'A 自由描述' } });
  fireEvent.change(screen.getByRole('textbox', { name: '试听文本' }), { target: { value: 'A 编辑后的台词' } });
  fireEvent.change(screen.getByRole('textbox', { name: '听感年龄' }), { target: { value: 'A 听感年龄' } });

  expect(useCanvasStore.getState().nodes.find(node => node.id === 'voice-1')?.data).toMatchObject({
    voiceDraftsByCharacter: { 'id:character-A': {
      voiceDesign: { voice_spec: { texture: 'A 编辑后的音色' } },
      voiceDescriptionDraft: 'A 自由描述', auditionText: 'A 编辑后的台词', voicePerceivedAge: 'A 听感年龄',
    } },
  });

  fireEvent.click(screen.getByRole('button', { name: 'integration-B' }));
  expect(await screen.findByRole('textbox', { name: '自定义声音描述' })).toHaveValue('');
  fireEvent.click(screen.getByRole('button', { name: 'integration-A' }));
  expect(await screen.findByRole('textbox', { name: '音色质感' })).toHaveValue('A 编辑后的音色');
  expect(screen.getByRole('textbox', { name: '自定义声音描述' })).toHaveValue('A 自由描述');
  expect(screen.getByRole('textbox', { name: '试听文本' })).toHaveValue('A 编辑后的台词');
  expect(screen.getByRole('textbox', { name: '听感年龄' })).toHaveValue('A 听感年龄');

  view.unmount();
  renderNode(view.client);
  expect(await screen.findByRole('textbox', { name: '音色质感' })).toHaveValue('A 编辑后的音色');
  expect(screen.getByRole('textbox', { name: '自定义声音描述' })).toHaveValue('A 自由描述');
  expect(screen.getByRole('textbox', { name: '试听文本' })).toHaveValue('A 编辑后的台词');
  expect(screen.getByRole('textbox', { name: '听感年龄' })).toHaveValue('A 听感年龄');
}, 15_000);

it('preserves persisted node design and audition when a late recommendation result arrives', async () => {
  const [character] = setCharacterPrefix('saved');
  act(() => useCanvasStore.getState().updateNodeData('voice-1', {
    voiceDesign: { voice_spec: { texture: 'A 原始音色' }, performance: {}, audition_text: 'A 原始台词' },
    auditionText: undefined,
  }));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  renderNode(client);
  await screen.findByRole('textbox', { name: '音色质感' });
  await waitFor(() => expect(client.getQueryData(['projects', 'demo'])).toBeDefined());

  const recommendationKey = [
    'character-voice-recommendation', 'demo', character.name,
    undefined, undefined, undefined, undefined, character.voice_facts, {},
  ];
  act(() => client.setQueryData(recommendationKey, {
    design: { voice_spec: { texture: '晚到的推荐音色' }, performance: {} },
    voice_description: '晚到的推荐描述', audition_text: '晚到的推荐台词', audition_source: 'example',
    rationale: '冷挂载回归测试', source_digest: 'saved-node', voice_facts: character.voice_facts,
  }));

  await waitFor(() => {
    expect(screen.getByRole('textbox', { name: '音色质感' })).toHaveValue('A 原始音色');
    expect(screen.getByRole('textbox', { name: '试听文本' })).toHaveValue('A 原始台词');
  });
});

it('drops a deferred A refinement after the real node switches to B', async () => {
  const [characterA, characterB] = setCharacterPrefix('refine');
  let releaseRefinement!: () => void;
  let signalRefinementStarted!: () => void;
  let signalRefinementFinished!: () => void;
  const refinementGate = new Promise<void>(resolve => { releaseRefinement = resolve; });
  const refinementStarted = new Promise<void>(resolve => { signalRefinementStarted = resolve; });
  const refinementFinished = new Promise<void>(resolve => { signalRefinementFinished = resolve; });
  server.use(http.post(`http://localhost:3000/api/v1/projects/demo/characters/${characterA.name}/voice-recommendation`, async ({ request }) => {
    const body = await request.json() as { adjustment?: string };
    if (!body.adjustment) return HttpResponse.json({ ok: false, error: '推荐暂不可用' }, { status: 503 });
    signalRefinementStarted();
    await refinementGate;
    signalRefinementFinished();
    return HttpResponse.json({ ok: true, data: {
      design: { voice_spec: { texture: 'A 迟到的音色' }, performance: {} },
      voice_description: 'A 迟到的描述', audition_text: 'A 迟到的台词', audition_source: 'example',
      rationale: '异步回归测试', source_digest: 'integration-a', voice_facts: { vocalization_mode: 'dialogue' },
    } });
  }));

  renderNode();
  fireEvent.click(await screen.findByRole('button', { name: '重新拟定声音方案' }));
  await refinementStarted;
  fireEvent.click(screen.getByRole('button', { name: characterB.name }));
  expect(await screen.findByRole('textbox', { name: '自定义声音描述' })).toHaveValue('');
  fireEvent.change(screen.getByRole('textbox', { name: '自定义声音描述' }), { target: { value: 'B 的草案' } });

  releaseRefinement();
  await act(async () => {
    await refinementFinished;
    await new Promise(resolve => setTimeout(resolve, 20));
  });

  const data = useCanvasStore.getState().nodes.find(node => node.id === 'voice-1')?.data;
  expect(data).toMatchObject({ characterId: characterB.character_id, characterName: characterB.name });
  expect(data).toMatchObject({ voiceDraftsByCharacter: { [`id:${characterB.character_id}`]: { voiceDescriptionDraft: 'B 的草案' } } });
  expect(data).not.toMatchObject({ voiceDraftsByCharacter: { 'id:character-A': { voiceDescription: 'A 迟到的描述' } } });
  expect(screen.queryByDisplayValue('A 迟到的描述')).not.toBeInTheDocument();
});

it('drops a deferred A acceptance after the real node switches to B', async () => {
  const [characterA, characterB] = setCharacterPrefix('accept');
  let releasePreparation!: () => void;
  let signalPreparationStarted!: () => void;
  let signalPreparationFinished!: () => void;
  const preparationGate = new Promise<void>(resolve => { releasePreparation = resolve; });
  const preparationStarted = new Promise<void>(resolve => { signalPreparationStarted = resolve; });
  const preparationFinished = new Promise<void>(resolve => { signalPreparationFinished = resolve; });
  server.use(
    http.post(`http://localhost:3000/api/v1/projects/demo/characters/${characterA.name}/voice-recommendation`, () =>
      HttpResponse.json({ ok: true, data: {
        design: { voice_spec: { texture: 'A 待审阅音色' }, performance: {} },
        voice_description: 'A 待审阅描述', audition_text: 'A 待审阅台词', audition_source: 'example',
        rationale: '异步回归测试', source_digest: 'integration-a', voice_facts: { vocalization_mode: 'dialogue' },
      } })),
    http.post(`http://localhost:3000/api/v1/projects/demo/characters/${characterA.name}/voice-samples/default/prepare`, async ({ request }) => {
      const body = await request.json() as { design: { voice_spec: { texture: string } }; audition_text: string };
      signalPreparationStarted();
      await preparationGate;
      signalPreparationFinished();
      return HttpResponse.json({ ok: true, data: {
        design: body.design, voice_description: 'A 接受后的迟到描述', audition_text: body.audition_text,
        voice_spec: body.design.voice_spec, compiler_version: 'integration-compiler',
      } });
    }),
  );

  renderNode();
  fireEvent.change(await screen.findByRole('textbox', { name: '自定义声音描述' }), { target: { value: 'A 待整理描述' } });
  fireEvent.click(screen.getByRole('button', { name: '解析并查看声线字段差异' }));
  fireEvent.click(await screen.findByRole('button', { name: '接受差异并重新编译' }));
  await preparationStarted;

  fireEvent.click(screen.getByRole('button', { name: characterB.name }));
  expect(await screen.findByRole('textbox', { name: '自定义声音描述' })).toHaveValue('');
  fireEvent.change(screen.getByRole('textbox', { name: '自定义声音描述' }), { target: { value: 'B 的草案' } });

  releasePreparation();
  await act(async () => {
    await preparationFinished;
    await new Promise(resolve => setTimeout(resolve, 20));
  });

  const data = useCanvasStore.getState().nodes.find(node => node.id === 'voice-1')?.data;
  expect(data).toMatchObject({ characterId: characterB.character_id, characterName: characterB.name });
  expect(data).toMatchObject({ voiceDraftsByCharacter: { [`id:${characterB.character_id}`]: { voiceDescriptionDraft: 'B 的草案' } } });
  expect(data).not.toMatchObject({ voiceDraftsByCharacter: { 'id:character-A': { voiceDescription: 'A 接受后的迟到描述' } } });
  expect(screen.queryByDisplayValue('A 接受后的迟到描述')).not.toBeInTheDocument();
});
