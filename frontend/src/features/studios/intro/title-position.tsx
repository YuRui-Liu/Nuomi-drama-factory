import { useId, useRef, type PointerEvent } from 'react';

type Position = { x: number; y: number };
const clamp = (value: number) => Math.round(Math.min(1, Math.max(0, value)) * 10000) / 10000;

export function moveTitle(position: Position, delta: Position, bounds: {width: number; height: number}): Position {
  if (bounds.width <= 0 || bounds.height <= 0) return position;
  return {x:clamp(position.x + delta.x / bounds.width), y:clamp(position.y + delta.y / bounds.height)};
}

export function TitlePositionPreview({src, position, onChange, ratio = '16:9'}: {src:string; position:Position; onChange:(position:Position) => void; ratio?:'16:9'|'9:16'|'1:1'}) {
  const image = useRef<HTMLImageElement>(null);
  const drag = useRef<{pointerId:number; start:Position; origin:Position; bounds:DOMRect} | null>(null);
  const helpId = useId();
  const aspect = ratio === '9:16' ? 9 / 16 : ratio === '1:1' ? 1 : 16 / 9;
  function move(event: PointerEvent<HTMLButtonElement>) {
    const active = drag.current;
    if (active?.pointerId !== event.pointerId) return;
    onChange(moveTitle(active.origin, {x:event.clientX - active.start.x,y:event.clientY - active.start.y}, active.bounds));
  }
  return <div className="flex min-h-0 min-w-0 items-center justify-center" style={{width:'100%',height:'100%',containerType:'size'}}>
    <div className="relative shrink-0" style={{width:`min(100cqw, calc(100cqh * ${aspect}))`,aspectRatio:aspect}}>
    <img ref={image} src={src} alt="当前片头效果预览" draggable={false} className="block h-full w-full select-none object-contain" />
    <button type="button" aria-label="移动片名位置" aria-describedby={helpId}
      className="absolute z-10 flex size-9 -translate-x-1/2 -translate-y-1/2 touch-none cursor-move items-center justify-center rounded-full border border-white/80 bg-black/60 text-white shadow-lg outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2"
      style={{left:`${position.x * 100}%`,top:`${position.y * 100}%`}}
      onPointerDown={event => { if (event.button !== 0 || !image.current) return; event.preventDefault(); event.currentTarget.focus(); drag.current = {pointerId:event.pointerId,start:{x:event.clientX,y:event.clientY},origin:position,bounds:image.current.getBoundingClientRect()}; event.currentTarget.setPointerCapture(event.pointerId); }}
      onPointerMove={move}
      onPointerUp={event => { move(event); drag.current = null; if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId); }}
      onPointerCancel={() => { drag.current = null; }} onLostPointerCapture={() => { drag.current = null; }}
      onKeyDown={event => { const step = event.shiftKey ? .05 : .01; const delta = ({ArrowLeft:{x:-step,y:0},ArrowRight:{x:step,y:0},ArrowUp:{x:0,y:-step},ArrowDown:{x:0,y:step}} as Record<string, Position>)[event.key]; if (!delta) return; event.preventDefault(); onChange(moveTitle(position, delta, {width:1,height:1})); }}>
      <svg aria-hidden="true" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5"><path d="M12 3v18M3 12h18M8 7l4-4 4 4M8 17l4 4 4-4M7 8l-4 4 4 4M17 8l4 4-4 4" /></svg>
    </button>
    <span id={helpId} className="sr-only">拖动十字定位片名；方向键移动百分之一，Shift 加方向键移动百分之五。当前水平 {Math.round(position.x * 100)}%，垂直 {Math.round(position.y * 100)}%。</span>
    </div>
  </div>;
}
