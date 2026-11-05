import { act,render,screen } from '@testing-library/react';
import { beforeEach,afterEach,expect,it,vi } from 'vitest';
import { fitNodeToolbarToViewport, useNodeToolbarPlacement } from '@/features/canvas/ui/useNodeToolbarPlacement';
const viewport=vi.hoisted(()=>({x:0,y:0,zoom:0.4}));
vi.mock('@xyflow/react',()=>({Position:{Top:'top',Bottom:'bottom'},useViewport:()=>viewport}));
let top=100, height=40, canvasLeft=0, canvasWidth=1000, nodeLeft=0, nodeWidth=1000, toolbarWidth=1000;
const rect=(x:number,y:number,w:number,h:number)=>({top:y,bottom:y+h,left:x,right:x+w,width:w,height:h,x,y,toJSON:()=>({})});
function Toolbar(){const {ref,position,origin,horizontalOffset,maxWidth}=useNodeToolbarPlacement('n',42);return <div className="react-flow"><div className="react-flow__node" data-id="n"/><div ref={ref} data-testid="toolbar" data-position={position} data-origin={origin} style={{maxWidth:maxWidth===null?undefined:`${maxWidth}px`,transform:horizontalOffset?`translateX(${horizontalOffset}px)`:undefined}}><div data-content/></div></div>;}
beforeEach(()=>{
 top=100;height=40;canvasLeft=0;canvasWidth=1000;nodeLeft=0;nodeWidth=1000;toolbarWidth=1000;viewport.zoom=0.4;
 vi.spyOn(HTMLElement.prototype,'getBoundingClientRect').mockImplementation(function(this:HTMLElement){return this.matches('header')?rect(0,0,1000,64):this.matches('.react-flow')?rect(canvasLeft,56,canvasWidth,700):this.matches('.react-flow__node')?rect(nodeLeft,top,nodeWidth,100):this.matches('[data-testid="toolbar"]')?rect(0,0,toolbarWidth,height):rect(0,0,1000,height);});
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
it('keeps a wide action toolbar inside the canvas viewport',()=>{
 const canvas={left:40,right:1046,width:1006};
 const toolbar={left:243,right:1300,width:1057};
 const fit=fitNodeToolbarToViewport(canvas,toolbar);
 const visibleWidth=Math.min(toolbar.width,fit.maxWidth);
 const visibleLeft=toolbar.left+fit.offsetX;
 expect(fit.maxWidth).toBe(982);
 expect(visibleLeft).toBeGreaterThanOrEqual(canvas.left+12);
 expect(visibleLeft+visibleWidth).toBeLessThanOrEqual(canvas.right-12);
});
it('applies a viewport-width cap and clamps a right-edge toolbar in screen space',()=>{
 canvasLeft=80;canvasWidth=800;nodeLeft=700;nodeWidth=150;toolbarWidth=1100;
 render(<Toolbar/>);
 const toolbar=screen.getByTestId('toolbar');
 expect(toolbar).toHaveStyle({maxWidth:'776px',transform:'translateX(-295px)'});
 const visibleLeft=775-776/2-295;
 expect(visibleLeft).toBeGreaterThanOrEqual(canvasLeft+12);
 expect(visibleLeft+776).toBeLessThanOrEqual(canvasLeft+canvasWidth-12);
});
