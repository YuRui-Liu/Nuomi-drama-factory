import {it, expect, vi} from 'vitest';
import {openEpisodeMusicDesk} from '@/features/canvas/music/handoff';
import {musicApi} from '@/features/canvas/music/api';
import {getFreezoneCanvas, putFreezoneCanvas} from '@/api/canvas';
vi.mock('@/features/canvas/music/api',()=>({musicApi:{source:vi.fn(async()=>({versionId:'v1',sha256:'a'.repeat(64),durationMs:90000})),load:vi.fn(async()=>({plan:null})),save:vi.fn(async(_p,_c,_n,plan)=>({...plan,revision:1}))}}));
vi.mock('@/api/canvas',()=>({getFreezoneCanvas:vi.fn(async()=>({nodes:[],edges:[],revision:2})),putFreezoneCanvas:vi.fn(async()=>({revision:3})),generateClientSaveId:()=> 'save-1'}));
it('saves an immutable source plan before inserting the music desk into a project canvas',async()=>{
 const target=await openEpisodeMusicDesk('p',1,'/static/p/ep001_final.mp4');
 expect(musicApi.save).toHaveBeenCalledWith('p',target.canvas,'music-desk',expect.objectContaining({source:{assetVersionId:'v1',sha256:'a'.repeat(64),durationMs:90000}}));
 expect(putFreezoneCanvas).toHaveBeenCalledWith('p',target.canvas,expect.objectContaining({base_revision:2,nodes:expect.arrayContaining([expect.objectContaining({id:'music-desk',type:'musicDeskNode',data:expect.objectContaining({sourceUrl:'/static/p/ep001_final.mp4'})})])}));
});
it('reuses a saved version canvas without replacing its music plan',async()=>{
 vi.mocked(musicApi.save).mockClear(); vi.mocked(putFreezoneCanvas).mockClear();
 vi.mocked(getFreezoneCanvas).mockResolvedValueOnce({nodes:[{id:'music-desk',type:'musicDeskNode'}],edges:[],revision:3});
 const target=await openEpisodeMusicDesk('p',1,'/static/p/ep001_final.mp4');
 expect(target.canvas).toBe('music-ep1-'+ 'a'.repeat(40));
 expect(musicApi.save).not.toHaveBeenCalled();
 expect(putFreezoneCanvas).not.toHaveBeenCalled();
});
it('a recomposed final URL creates a separate content-version canvas',async()=>{
 vi.mocked(musicApi.source).mockResolvedValueOnce({id:'a2',versionId:'v2',sha256:'b'.repeat(64),durationMs:91000,name:'final',url:'/version',revision:1,tags:[],vocals:'unknown'});
 const target=await openEpisodeMusicDesk('p',1,'/static/p/ep001_final.mp4');
 expect(target.canvas).toBe('music-ep1-'+ 'b'.repeat(40));
 expect(musicApi.save).toHaveBeenCalledWith('p',target.canvas,'music-desk',expect.objectContaining({source:expect.objectContaining({assetVersionId:'v2'})}));
});
