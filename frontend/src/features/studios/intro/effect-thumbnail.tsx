export function EffectThumbnail({effect}: {effect:string}) {
  return <div aria-hidden="true" className="relative mb-2 flex h-20 items-center justify-center overflow-hidden rounded-md bg-slate-950 text-white">
    <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_top_right,rgba(148,163,184,0.3),transparent_70%)]" />
    {effect === 'fade' ? <div className="relative flex gap-2 font-serif"><span className="opacity-20">片</span><span className="opacity-50">头</span><span>映像</span></div>
      : effect === 'typewriter' ? <span className="relative font-serif tracking-[.2em]">片头<span className="ml-1 border-r-2 border-amber-200">映</span><span className="opacity-20">像</span></span>
      : effect === 'zoom' ? <><span className="absolute scale-150 font-serif tracking-widest opacity-10">片头映像</span><span className="relative text-xs font-serif tracking-[.25em]">片头映像</span><span className="absolute bottom-1 right-2 text-amber-100/70">↗</span></>
      : <span className="relative font-serif tracking-[.25em]">片头映像</span>}
    {effect === 'shine' && <div className="absolute inset-y-0 left-1/2 w-5 -skew-x-12 bg-amber-100/40 blur-sm" />}
    <span className="absolute bottom-1 left-2 text-[9px] font-normal tracking-normal text-white/40">模板示意</span>
  </div>;
}
