import {render,screen,waitFor,fireEvent} from '@testing-library/react';
import {describe,it,expect,vi} from 'vitest';
import {MusicDeskModal} from '@/features/canvas/music/MusicDeskModal';
import {emptyPlan} from '@/features/canvas/music/timeline';
import {musicApi} from '@/features/canvas/music/api';
const plan=emptyPlan({assetVersionId:'v',sha256:'a'.repeat(64),durationMs:90000});
vi.mock('@/features/canvas/music/api',()=>({musicApi:{
 load:vi.fn(async()=>({plan})),library:vi.fn(async()=>[]),favorites:vi.fn(async()=>[]),jobs:vi.fn(async()=>[]),
 capabilities:vi.fn(async()=>({generation:false,reason:'未配置'})),
 save:vi.fn(async(_p,_c,_n,p)=>({...p,revision:1})),
},musicPrefix:()=> 'projects/p/music'}));

describe('music desk',()=>{
 it.each([0,89000])('gap replacement clears existing music starting at %i through the selection end',async(startMs)=>{
  const existing=structuredClone(plan);
  existing.tracks[0].clips=[{id:'old',assetVersionId:'old-music',startMs,sourceInMs:0,lengthMs:90000-startMs,gainDb:-18,fadeInMs:0,fadeOutMs:0,loop:null}];
  vi.mocked(musicApi.load).mockResolvedValueOnce({plan:existing});
  vi.mocked(musicApi.save).mockClear();
  vi.mocked(musicApi.library).mockResolvedValueOnce([{id:'a',versionId:'a1',name:'短音乐',durationMs:89000,url:'/a.wav',revision:1,tags:[],vocals:'none'}]);
  render(<MusicDeskModal project="p" canvas="c" node="n" sourceUrl="/video.mp4" onClose={()=>{}} onSaved={()=>{}} onExport={()=>{}}/>);
  fireEvent.click(await screen.findByRole('button',{name:'放入选区'}));
  fireEvent.click(screen.getByRole('button',{name:'保留末尾留白'}));
  fireEvent.click(screen.getByRole('button',{name:'替换选区'}));
  fireEvent.click(screen.getByRole('button',{name:'保存草稿'}));
  await waitFor(()=>expect(musicApi.save).toHaveBeenCalled());
  const saved=vi.mocked(musicApi.save).mock.calls[0][3];
  expect(saved.tracks[0].clips).toHaveLength(1);
  expect(saved.tracks[0].clips[0]).toMatchObject({assetVersionId:'a1',lengthMs:89000,loop:null});
 });
 it.each([
  ['保留末尾留白',89000,89000,null],
  ['按实际时长采用',89999,89999,null],
  ['明确循环填满选区',89000,90000,{startMs:0,endMs:89000}],
 ])('requires explicit short music choice: %s',async(choice,durationMs,lengthMs,loop)=>{
  vi.mocked(musicApi.save).mockClear();
  vi.mocked(musicApi.library).mockResolvedValueOnce([{id:'a',versionId:'a1',name:'短音乐',durationMs:durationMs as number,url:'/a.wav',revision:1,tags:[],vocals:'none'}]);
  render(<MusicDeskModal project="p" canvas="c" node="n" sourceUrl="/video.mp4" onClose={()=>{}} onSaved={()=>{}} onExport={()=>{}}/>);
  fireEvent.click(await screen.findByRole('button',{name:'放入选区'}));
  expect(await screen.findByRole('dialog',{name:'音乐短于选区'})).toBeTruthy();
  if(durationMs===89999) expect(screen.getByText(/这是毫秒级尾差/)).toBeTruthy();
  fireEvent.click(screen.getByRole('button',{name:choice as string}));
  fireEvent.click(screen.getByRole('button',{name:'保存草稿'}));
  await waitFor(()=>expect(musicApi.save).toHaveBeenCalled());
  const calls=vi.mocked(musicApi.save).mock.calls;
  const saved=calls[calls.length-1][3];
  expect(saved.tracks[0].clips[0]).toMatchObject({lengthMs,loop});
 });
 it('loads the saved draft and adds independent tracks with undo',async()=>{
  render(<MusicDeskModal project="p" canvas="c" node="n" sourceUrl="/video.mp4" onClose={()=>{}} onSaved={()=>{}} onExport={()=>{}}/>);
  await screen.findByRole('button',{name:'新增配乐轨'});
  fireEvent.click(screen.getByRole('button',{name:'新增配乐轨'}));
  expect(screen.getAllByLabelText('轨道名称')).toHaveLength(2);
  fireEvent.click(screen.getByRole('button',{name:'撤销'}));
  expect(screen.getAllByLabelText('轨道名称')).toHaveLength(1);
  expect(screen.getByRole('button',{name:'生成配乐'})).toBeDisabled();
  fireEvent.click(screen.getByRole('button',{name:'保存草稿'}));
  await waitFor(()=>expect(screen.getByText('已保存')).toBeTruthy());
 });
});
