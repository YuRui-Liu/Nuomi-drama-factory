import { useId } from 'react';
import { useTranslation } from 'react-i18next';

function Person({ x, y = 51, scale = 1, gaze = 0 }: { x: number; y?: number; scale?: number; gaze?: number }) {
  return <g transform={`translate(${x} ${y}) scale(${scale})`} stroke="currentColor" strokeWidth="2" fill="none" strokeLinecap="round">
    <circle cy="-12" r="8" /><path d="M-13 19 Q-13 1 0 1 Q13 1 13 19 M0 19v12 M0 22l-10 15 M0 22l10 15" />
    {gaze !== 0 && <path d={`M${gaze > 0 ? 5 : -5} -13h${gaze * 17}`} stroke="#fbbf24" />}
  </g>;
}

/** Original schematic drawings. No generated sample or external image is implied. */
export function TechniqueSketch({ id, large = false }: { id: string; large?: boolean }) {
  const marker = useId().replace(/:/g, '');
  const { t } = useTranslation();
  if (!['fixed-reaction', 'expression-build', 'two-person-gaze', 'lateral-follow', 'slow-push-in', 'subject-entrance', 'contained-climax', 'endpoint-continuity'].includes(id)) {
    return <div className="flex min-h-28 items-center justify-center rounded-lg bg-[#101923] p-4 text-center text-xs leading-relaxed text-slate-400">{t('techniqueLibrary.sketchUnavailable', { defaultValue: '此手法暂无镜头示意，请参阅文字说明' })}</div>;
  }
  const arrow = `url(#${marker})`;
  return <figure className={`overflow-hidden rounded-lg bg-[#101923] ${large ? 'p-3' : 'p-2'}`}>
    <svg viewBox="0 0 360 138" role="img" aria-label={t('techniqueLibrary.sketch', { defaultValue: '镜头示意' })} className="w-full text-cyan-200">
      <defs><marker id={marker} markerWidth="5" markerHeight="5" refX="4" refY="2.5" orient="auto"><path d="M0 0L5 2.5 0 5" fill="#fbbf24" /></marker></defs>
      {[0, 1, 2].map((phase) => <g key={phase} transform={`translate(${phase * 120 + 3} 5)`}>
        <rect x="0" y="0" width="110" height="102" rx="5" fill="#16232e" stroke="#344657" />
        <path d="M7 85h96" stroke="#344657" strokeDasharray="3 4" />
        <text x="8" y="15" fill="#7a97aa" fontSize="10">0{phase + 1}</text>
        {id === 'two-person-gaze' ? <><Person x={30} scale={0.85} gaze={phase > 0 ? 1 : 0} /><Person x={82} scale={0.85} gaze={phase === 2 ? -1 : 0} />
          {phase > 0 && <path d={phase === 1 ? 'M42 31h27' : 'M69 28H42'} stroke="#fbbf24" markerEnd={arrow} />}</>
          : id === 'slow-push-in' ? <><Person x={55} y={54 + phase * 7} scale={0.7 + phase * 0.27} /><path d={`M${16 + phase * 5} 23h9m-9 0v9 M${94 - phase * 5} 23h-9m9 0v9`} stroke="#fbbf24" fill="none" /></>
          : id === 'lateral-follow' ? <><path d={`M${20 - phase * 8} 20v62m55-62v62`} stroke="#344657" /><Person x={48 + phase * 7} /><path d="M25 93h58" stroke="#fbbf24" markerEnd={arrow} /></>
          : id === 'subject-entrance' ? <><path d="M78 25v58M78 25h20" stroke="#537282" fill="none" />{phase > 0 && <Person x={phase === 1 ? 22 : 55} />}<path d={`M8 70h${phase === 0 ? 20 : 30}`} stroke="#fbbf24" markerEnd={arrow} /></>
          : id === 'contained-climax' ? <><g transform={`rotate(${phase === 1 ? -20 : phase === 0 ? 12 : 0} 50 65)`}><Person x={50} /></g><path d={phase === 1 ? 'M55 48L91 30M75 29l-6-5M91 40l9 2' : phase === 0 ? 'M59 50L75 58' : 'M60 50L80 70'} stroke="#fbbf24" strokeWidth="2" /><circle cx={phase === 2 ? 88 : 80} cy={phase === 2 ? 80 : 64} r="3" fill="#fbbf24" /></>
          : id === 'endpoint-continuity' ? <><rect x="8" y="23" width="94" height="66" rx="2" fill="none" stroke={phase === 1 ? '#537282' : '#fbbf24'} strokeDasharray={phase === 1 ? '3 3' : undefined} /><Person x={30 + phase * 24} scale={0.85} />{phase === 1 && <path d="M25 79Q55 57 83 79" fill="none" stroke="#fbbf24" markerEnd={arrow} />}</>
          : id === 'expression-build' ? <><circle cx="55" cy="51" r="27" fill="none" stroke="currentColor" strokeWidth="2" /><path d={phase === 0 ? 'M40 44h8m14 0h8M45 63h20' : phase === 1 ? 'M40 41l8 3m14 0l8-3M45 65q10-6 20 0' : 'M40 38l8 5m14 0l8-5M44 66q11-13 22 0'} fill="none" stroke="#fbbf24" strokeWidth="2" /></>
          : <><Person x={55} gaze={phase > 0 ? 1 : 0} />{phase === 1 && <path d="M77 27l7-8m-4 16h11" stroke="#fbbf24" />}{phase === 2 && <path d="M79 42v14m5-14v14" stroke="#fbbf24" />}</>}
        <g transform={`translate(${id === 'lateral-follow' ? 27 + phase * 15 : 47} 112)`} stroke="#7a97aa" fill="none"><rect width="14" height="9" rx="2" /><path d="M14 2l5-2v9l-5-2M7 9v5m-5 0h10" /></g>
      </g>)}
    </svg>
    <figcaption className="mt-1 text-[10px] tracking-wide text-slate-400">{t('techniqueLibrary.sketchNote', { defaultValue: '镜头示意 · 原创三阶段图解，非生成样片' })}</figcaption>
  </figure>;
}
