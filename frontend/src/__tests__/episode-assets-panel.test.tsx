import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { vi, test, expect, beforeEach } from 'vitest';
import { EpisodeAssetsPanel } from '@/components/episode/episode-assets-panel';

const mocks = vi.hoisted(() => ({ save: vi.fn(), navigate: vi.fn() }));
vi.mock('@tanstack/react-router', () => ({ useNavigate: () => mocks.navigate }));
vi.mock('@/lib/queries/characters', () => ({ useIdentityOwnerIndex: () => ({identities: [{id:'white',owner:'白尾'}],isLoading:false}) }));
vi.mock('@/lib/queries/scenes', () => ({useScenes: () => ({data:{data:[{name:'山门'}]}})}));
vi.mock('@/lib/queries/props', () => ({useProps: () => ({data:{data:[{name:'铃'}]}})}));
vi.mock('@/lib/queries/episodes', () => ({useSaveEpisodeAssetBindings: () => ({mutateAsync:mocks.save,isPending:false})}));
beforeEach(() => vi.clearAllMocks());
test('saves this episode selected bindings and retains unsaved values on failure', async () => {
  mocks.save.mockResolvedValue({ok:false,error:'失败'});
  const close = vi.fn();
  render(<EpisodeAssetsPanel project="p" episode={{number:2,title:'第二集',identity_ids:[]}} onClose={close}/>);
  fireEvent.click(screen.getByLabelText('白尾 · white'));
  fireEvent.click(screen.getByRole('button',{name:'保存绑定'}));
  await waitFor(() => expect(mocks.save).toHaveBeenCalledWith({identity_ids:['white'],scene_ids:[],prop_ids:[]}));
  expect(screen.getByLabelText('白尾 · white')).toBeChecked();
  expect(close).not.toHaveBeenCalled();
});
test('incoming parse results refresh clean selections but cannot overwrite dirty edits', () => {
  const close = vi.fn();
  const {rerender} = render(<EpisodeAssetsPanel project="p" episode={{number:2,title:'第二集'}} onClose={close}/>);
  rerender(<EpisodeAssetsPanel project="p" episode={{number:2,title:'第二集',identity_ids:['white']}} onClose={close}/>);
  expect(screen.getByLabelText('白尾 · white')).toBeChecked();
  fireEvent.click(screen.getByLabelText('白尾 · white'));
  rerender(<EpisodeAssetsPanel project="p" episode={{number:2,title:'第二集',identity_ids:['white'],scene_menu:[{scene_id:'山门'}]}} onClose={close}/>);
  expect(screen.getByRole('alert')).toHaveTextContent('已被其他任务更新');
  expect(screen.getByRole('button',{name:'保存绑定'})).toBeDisabled();
});
test('closing dirty bindings asks before discarding', () => {
  const close = vi.fn();
  render(<EpisodeAssetsPanel project="p" episode={{number:1,title:'第一集'}} onClose={close}/>);
  fireEvent.click(screen.getByLabelText('白尾 · white'));
  fireEvent.click(screen.getByRole('button',{name:'Close'}));
  expect(close).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button',{name:'继续编辑'}));
  expect(screen.getByLabelText('白尾 · white')).toBeChecked();
  fireEvent.click(screen.getByRole('button',{name:'Close'}));
  fireEvent.click(screen.getByRole('button',{name:'放弃'}));
  expect(close).toHaveBeenCalledOnce();
});
