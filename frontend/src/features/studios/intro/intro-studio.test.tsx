import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import { IntroStudio } from './intro-studio';

const mock = vi.hoisted(() => ({ request: vi.fn(), list: vi.fn(), save: vi.fn(), preview: vi.fn() }));
vi.mock('./intro-api', () => ({ introRequest: mock.request, introPreview: mock.preview, introMedia: (_p: string, path: string) => `/media/${path}` }));
vi.mock('../studio-api', () => ({ listStudioDocuments: mock.list, saveStudioDocument: mock.save }));
vi.mock('./intro-background', () => ({ IntroBackground: () => null, defaultBackgroundSettings: {modelId:'', prompt:'', size:'2K', quality:'medium'} }));

beforeEach(() => {
  vi.clearAllMocks();
  mock.list.mockResolvedValue([]);
  mock.preview.mockResolvedValue('blob:preview');
  mock.request.mockImplementation((_p, route) => Promise.resolve(route === 'capabilities' ? {fonts: [{id:'pingfang',name:'苹方'}],videos:[],local_render:true,ai_effects:false,ai_reason:'未接入 AI 服务'} : []));
  mock.save.mockImplementation((_p, _k, id, body) => Promise.resolve({id, revision:1, ...body}));
});

it('edits independent text without starting a render and saves with expected revision', async () => {
  render(<IntroStudio project="project-a" />);
  await screen.findByText('苹方');
  fireEvent.change(screen.getByLabelText('片名'), {target:{value:'新的片名'}});
  fireEvent.click(screen.getByRole('button', {name:/保存草稿/}));
  await waitFor(() => expect(mock.save).toHaveBeenCalledWith('project-a', 'intro', expect.any(String), expect.objectContaining({expected_revision:0, data:expect.objectContaining({spec:expect.objectContaining({title:'新的片名'})})})));
  expect(mock.request.mock.calls.some((call) => call[1] === 'jobs' && call[2])).toBe(false);
  expect(screen.getByLabelText('插入当前集片头')).toBeDisabled();
});

it('retains a failed candidate and exposes its actual error with retry', async () => {
  mock.request.mockImplementation((_p, route) => Promise.resolve(route === 'capabilities' ? {fonts: [],videos:[],local_render:false,ai_effects:false} : [{id:'failed', status:'failed', error:'编码器不可用', progress:0, spec:{title:'已有片名'}, created_at:'2026-09-26'}]));
  render(<IntroStudio project="project-a" />);
  await screen.findByText('编码器不可用');
  expect(screen.getByRole('button', {name:'重试此候选'})).toBeVisible();
});

it('saves and restores the selected source movie and frame independently of insertion', async () => {
  mock.request.mockImplementation((_p, route) => Promise.resolve(route === 'capabilities' ? {fonts:[{id:'pingfang',name:'苹方'}],videos:['ep001_final.mp4'],local_render:true} : []));
  const view = render(<IntroStudio project="source-frame" />);
  await screen.findByText('苹方');
  fireEvent.change(screen.getByLabelText('已有成片'), {target:{value:'ep001_final.mp4'}});
  fireEvent.change(screen.getByLabelText('选帧秒数'), {target:{value:'5.5'}});
  fireEvent.click(screen.getByRole('button', {name:/保存草稿/}));
  await waitFor(() => expect(mock.save).toHaveBeenCalled());
  const body = mock.save.mock.calls[0][3];
  expect(body.data).toMatchObject({source_video:'ep001_final.mp4', frame_time:5.5, spec:{insert_video:''}});
  view.unmount();
  mock.list.mockResolvedValue([{id:'saved', revision:1, name:'片头', ...body}]);
  render(<IntroStudio project="source-frame" />);
  await waitFor(() => expect(screen.getByLabelText('选帧秒数')).toHaveValue(5.5));
  expect(screen.getByLabelText('已有成片')).toHaveValue('ep001_final.mp4');
  expect(screen.getByLabelText('插入当前集片头')).not.toBeChecked();
});

it('does not apply an old save revision to the draft loaded while saving', async () => {
  const docs = [
    {id:'draft-a', name:'A', revision:2, data:{spec:{title:'A', font:'pingfang'}}},
    {id:'draft-b', name:'B', revision:7, data:{spec:{title:'B', font:'pingfang'}}},
  ];
  mock.list.mockResolvedValue(docs);
  let finishSave!: (value: unknown) => void;
  mock.save.mockImplementationOnce(() => new Promise(resolve => { finishSave = resolve; }));
  render(<IntroStudio project="project-a" />);
  await waitFor(() => expect(screen.getByLabelText('片名')).toHaveValue('A'));
  fireEvent.click(screen.getByRole('button', {name:/保存草稿/}));
  await waitFor(() => expect(mock.save).toHaveBeenCalledTimes(1));
  fireEvent.change(screen.getByLabelText('加载片头草稿'), {target:{value:'draft-b'}});
  await waitFor(() => expect(screen.getByLabelText('片名')).toHaveValue('B'));
  await act(async () => { finishSave({...docs[0], revision:3}); });
  fireEvent.change(screen.getByLabelText('片名'), {target:{value:'B edited'}});
  fireEvent.click(screen.getByRole('button', {name:/保存草稿/}));
  await waitFor(() => expect(mock.save).toHaveBeenLastCalledWith('project-a', 'intro', 'draft-b', expect.objectContaining({expected_revision:7})));
});

it('resets unsaved text when the project changes', async () => {
  const view = render(<IntroStudio project="project-a" />);
  await screen.findByText('苹方');
  fireEvent.change(screen.getByLabelText('片名'), {target:{value:'A 项目专属'}});
  view.rerender(<IntroStudio project="project-b" />);
  await waitFor(() => expect(screen.getByLabelText('片名')).toHaveValue('我的故事'));
});

it('loads the destination episode draft when navigating within the same project', async () => {
  const initialUrl = window.location.href;
  try {
    mock.list.mockResolvedValue([
      {id:'ep1', revision:2, data:{source:{episode:1}, spec:{title:'第一集片头', font:'pingfang'}}},
      {id:'ep2', revision:4, data:{source:{episode:2}, spec:{title:'第二集片头', font:'pingfang'}}},
    ]);
    window.history.replaceState({}, '', '?studio=intro&episode=1');
    const view = render(<IntroStudio project="same-project" />);
    await waitFor(() => expect(screen.getByLabelText('片名')).toHaveValue('第一集片头'));
    window.history.replaceState({}, '', '?studio=intro&episode=2');
    view.rerender(<IntroStudio project="same-project" />);
    await waitFor(() => expect(screen.getByLabelText('片名')).toHaveValue('第二集片头'));
  } finally { window.history.replaceState({}, '', initialUrl); }
});

it('previews a real candidate, adopts it on save, and links to its actual output', async () => {
  mock.request.mockImplementation((_p, route) => Promise.resolve(route === 'capabilities' ? {fonts:[{id:'pingfang',name:'苹方'}],videos:[],local_render:true} : [{id:'candidate-1',status:'completed',output:'studios/intro/one.mp4',progress:1,spec:{title:'完成的片头',effect:'fade'},created_at:'2026-09-26'}]));
  render(<IntroStudio project="project-a" />);
  const card = await screen.findByRole('article', {name:'候选 完成的片头'});
  expect(within(card).getByLabelText('候选视频缩略预览')).toHaveAttribute('src', '/media/studios/intro/one.mp4');
  fireEvent.click(within(card).getByRole('button', {name:'查看'}));
  expect(screen.getByLabelText('当前候选视频')).toHaveAttribute('src', '/media/studios/intro/one.mp4');
  fireEvent.click(within(card).getByRole('button', {name:'采纳'}));
  expect(within(card).getByText('已采纳')).toBeVisible();
  expect(within(card).getByRole('link', {name:'下载 MP4'})).toHaveAttribute('href', '/media/studios/intro/one.mp4');
  fireEvent.click(screen.getByRole('button', {name:/保存草稿/}));
  await waitFor(() => expect(mock.save).toHaveBeenCalledWith('project-a','intro',expect.any(String),expect.objectContaining({data:expect.objectContaining({adopted_candidate:'candidate-1'})})));
  expect(mock.request.mock.calls.some(call => call[1] === 'jobs' && call[2])).toBe(false);
});

it('submits corrected input after an explicit validation rejection', async () => {
  let attempts = 0;
  mock.request.mockImplementation((_project, route, body) => {
    if (route === 'capabilities') return Promise.resolve({fonts:[{id:'pingfang',name:'苹方'}],videos:[],local_render:true});
    if (route === 'jobs' && body) {
      attempts++;
      if (attempts === 1) return Promise.reject(Object.assign(new Error('字体校验失败'), {status:422}));
      return Promise.resolve({id:'done', status:'completed', spec:body.spec, progress:1, output:'intro.mp4', created_at:'2026-09-26'});
    }
    return Promise.resolve([]);
  });
  render(<IntroStudio project="project-a" />);
  await screen.findByText('苹方');
  fireEvent.click(screen.getByRole('button', {name:'渲染新候选 · 本地免费'}));
  await screen.findByText(/字体校验失败/);
  fireEvent.change(screen.getByLabelText('片名'), {target:{value:'修正后的片名'}});
  fireEvent.click(screen.getByRole('button', {name:'渲染新候选 · 本地免费'}));
  await waitFor(() => expect(attempts).toBe(2));
  const submitted = mock.request.mock.calls.filter(call => call[1] === 'jobs' && call[2]);
  expect(submitted[1][2].spec.title).toBe('修正后的片名');
});
