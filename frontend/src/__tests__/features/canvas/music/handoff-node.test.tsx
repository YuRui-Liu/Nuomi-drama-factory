import {render,screen} from '@testing-library/react';
import {it,expect,vi} from 'vitest';
import {MusicDeskNode} from '@/features/canvas/nodes/MusicDeskNode';
import type {NodeProps} from '@xyflow/react';
import {useUpstreamNodes} from '@/features/canvas/application/useUpstreamGraph';
vi.mock('@xyflow/react',()=>({Handle:()=>null,Position:{Left:'left',Right:'right'}}));
vi.mock('@/stores/canvasStore',()=>({useCanvasStore:(select:(s:unknown)=>unknown)=>select({updateNodeData:vi.fn()})}));
vi.mock('@/features/canvas/application/useUpstreamGraph',()=>({useUpstreamNodes:vi.fn(()=>[])}));
vi.mock('@/lib/url-params',()=>({readUrl:()=>({project:'p',canvas:'music-ep1-sha'})}));
vi.mock('@/features/canvas/ui/NodeHeader',()=>({NodeHeader:()=>null,NODE_HEADER_FLOATING_POSITION_CLASS:''}));
vi.mock('@/features/canvas/music/MusicDeskModal',()=>({MusicDeskModal:({sourceUrl}:{sourceUrl:string})=><div role="dialog">{sourceUrl}</div>}));
it('opens a bound handoff node without requiring a live upstream connection',()=>{
 render(<MusicDeskNode {...{id:'music-desk',data:{sourceUrl:'/original.mp4'}} as unknown as NodeProps}/>);
 expect(screen.getByRole('button',{name:'打开配乐台'})).not.toBeDisabled();
 expect(screen.getByRole('dialog')).toHaveTextContent('/original.mp4');
});
it('warns about a changed live connection while opening the original saved source',()=>{
 vi.mocked(useUpstreamNodes).mockReturnValueOnce([{id:'video',type:'videoNode',data:{videoUrl:'/new.mp4'},position:{x:0,y:0}}] as ReturnType<typeof useUpstreamNodes>);
 render(<MusicDeskNode {...{id:'music-desk',data:{sourceUrl:'/original.mp4'}} as unknown as NodeProps}/>);
 expect(screen.getByText('源连接已变化，已保存计划仍使用原成片版本')).toBeTruthy();
 expect(screen.getByRole('dialog')).toHaveTextContent('/original.mp4');
});
