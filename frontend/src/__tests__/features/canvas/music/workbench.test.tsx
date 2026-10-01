import {render,screen,waitFor,fireEvent} from '@testing-library/react';
import {describe,it,expect,vi} from 'vitest';
import {MusicDeskModal} from '@/features/canvas/music/MusicDeskModal';
import {emptyPlan} from '@/features/canvas/music/timeline';
const plan=emptyPlan({assetVersionId:'v',sha256:'a'.repeat(64),durationMs:90000});
vi.mock('@/features/canvas/music/api',()=>({musicApi:{
 load:vi.fn(async()=>({plan})),library:vi.fn(async()=>[]),favorites:vi.fn(async()=>[]),jobs:vi.fn(async()=>[]),
 capabilities:vi.fn(async()=>({generation:false,reason:'未配置'})),
 save:vi.fn(async(_p,_c,_n,p)=>({...p,revision:1})),
},musicPrefix:()=> 'projects/p/music'}));

describe('music desk',()=>{
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
