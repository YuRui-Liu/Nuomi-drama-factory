import { act,render,screen } from '@testing-library/react';
import { beforeEach,afterEach,expect,it,vi } from 'vitest';
import { useNodeToolbarPlacement } from '@/features/canvas/ui/useNodeToolbarPlacement';
const viewport=vi.hoisted(()=>({x:0,y:0,zoom:0.4}));
vi.mock('@xyflow/react',()=>({Position:{Top:'top',Bottom:'bottom'},useViewport:()=>viewport}));
let top=100, height=40;
const rect=(y:number,h:number)=>({top:y,bottom:y+h,left:0,right:1000,width:1000,height:h,x:0,y,toJSON:()=>({})});
function Toolbar(){const {ref,position,origin}=useNodeToolbarPlacement('n',42);return <div className="react-flow"><div className="react-flow__node" data-id="n"/><div ref={ref} data-testid="toolbar" data-position={position} data-origin={origin}><div data-content/></div></div>;}
beforeEach(()=>{
 top=100;height=40;viewport.zoom=0.4;
 vi.spyOn(HTMLElement.prototype,'getBoundingClientRect').mockImplementation(function(this:HTMLElement){return this.matches('header')?rect(0,64):this.matches('.react-flow')?rect(56,700):this.matches('.react-flow__node')?rect(top,100):rect(0,height);});
 const header=document.createElement('header');document.body.append(header);
});
afterEach(()=>{document.querySelector('header')?.remove();vi.restoreAllMocks();vi.unstubAllGlobals();});
it('flips under the node when the top would be obscured and restores top on viewport change',()=>{
 const view=render(<Toolbar/>);
 expect(screen.getByTestId('toolbar')).toHaveAttribute('data-position','bottom');
 expect(screen.getByTestId('toolbar')).toHaveAttribute('data-origin','top center');
 top=240;viewport.y=140;view.rerender(<Toolbar/>);
 expect(screen.getByTestId('toolbar')).toHaveAttribute('data-position','top');
 expect(screen.getByTestId('toolbar')).toHaveAttribute('data-origin','bottom center');
});
it('remeasures rendered height after zoom and container resize without moving nodes',()=>{
 top=180;const view=render(<Toolbar/>);
 expect(screen.getByTestId('toolbar')).toHaveAttribute('data-position','top');
 height=100;viewport.zoom=1;view.rerender(<Toolbar/>);
 expect(screen.getByTestId('toolbar')).toHaveAttribute('data-position','bottom');
 height=30;act(()=>window.dispatchEvent(new Event('resize')));
 expect(screen.getByTestId('toolbar')).toHaveAttribute('data-position','top');
 expect(document.querySelector('.react-flow__node')).not.toHaveAttribute('style');
});
it('observes container/content changes and disconnects on unmount',()=>{
 let resize=()=>{};const observe=vi.fn(),disconnect=vi.fn();
 vi.stubGlobal('ResizeObserver',class {constructor(callback:()=>void){resize=callback;}observe=observe;disconnect=disconnect;});
 top=220;const view=render(<Toolbar/>);
 expect(observe).toHaveBeenCalledWith(document.querySelector('.react-flow'));
 expect(screen.getByTestId('toolbar')).toHaveAttribute('data-position','top');
 height=130;act(()=>resize());
 expect(screen.getByTestId('toolbar')).toHaveAttribute('data-position','bottom');
 view.unmount();expect(disconnect).toHaveBeenCalled();
});
