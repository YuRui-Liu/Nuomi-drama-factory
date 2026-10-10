/** Cyan describes the subject; amber marks the changing action or camera path. */
export const additionalSketchIds = ['product-orbit', 'overhead-assembly', 'motivated-light-reveal',
  'low-backward-track', 'motion-match-transition', 'demonstration-inserts', 'projected-light-performance',
  'macro-contact-texture', 'centered-match-transition', 'performance-arc', 'impact-cut',
  'listener-countershot', 'contrasting-energy-pair', 'event-aftershock', 'rack-focus-reveal', 'speech-settle'];

function Face({ x = 55, y = 48, scale = 1, quiet = false }: { x?: number; y?: number; scale?: number; quiet?: boolean }) {
  return <g transform={`translate(${x} ${y}) scale(${scale})`} fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
    <circle r="15" /><path d="M-22 35q0-17 22-17t22 17M-7-3h2m10 0h2" />
    <path d={quiet ? 'M-5 7h10' : 'M-5 6q5 7 10 0'} stroke="#fbbf24" />
  </g>;
}

export function AdditionalTechniqueSketch({ id, phase: p, arrow }: { id: string; phase: number; arrow: string }) {
  const gold = '#fbbf24', muted = '#537282';
  const flow = (d: string) => <path d={d} fill="none" stroke={gold} strokeWidth="1.6" markerEnd={arrow} />;
  const box = (x: number, y: number, w: number, h: number, color = 'currentColor') => <rect x={x} y={y} width={w} height={h} rx="2" fill="none" stroke={color} strokeWidth="2" />;
  switch (id) {
    case 'product-orbit':
      return <><ellipse cx="55" cy="78" rx="34" ry="9" fill="none" stroke={muted} strokeDasharray="3 3" />
        <path d={`M35 39l${12 + p * 4}-9 29 6v36l-${12 + p * 4} 9-29-6z M35 39l29 6v36 M64 45l${12 + p * 4}-9`} fill="none" stroke="currentColor" strokeWidth="2" />
        {flow('M24 78Q42 98 87 77')}<circle cx={25 + p * 28} cy={p === 1 ? 87 : 78} r="3" fill={gold} /></>;
    case 'overhead-assembly':
      return <>{box(16, 24, 78, 60, muted)}{box(35, 47, 40, 24)}
        {box(p === 0 ? 22 : 39, p === 0 ? 29 : 51, 14, 16, gold)}{box(p < 2 ? 75 : 57, p < 2 ? 29 : 51, 14, 16, gold)}
        {p < 2 && <><path d={`M${p === 0 ? 30 : 82} 20v13l5 4v8h-10v-8`} fill="none" stroke="currentColor" />{flow(p === 0 ? 'M29 49l14 9' : 'M82 49l-15 9')}</>}
        {p === 2 && <path d="M43 78h24" stroke={gold} />}</>;
    case 'motivated-light-reveal':
      return <><path d="M15 25h15v9H15zM22 34v44" fill="none" stroke={muted} /><path d="M29 32L95 21v62z" fill={gold} opacity={p === 0 ? 0.025 : 0.15} />
        <g opacity={p === 0 ? 0.3 : 1}><Face x={68} scale={0.8} quiet /></g>{p === 1 && flow('M30 40L49 48')}</>;
    case 'low-backward-track':
      return <><path d="M9 86L46 38h18l37 48M22 86l26-35m40 35L62 51" fill="none" stroke={muted} /><Face y={38} scale={0.65} quiet />
        <path d={p === 1 ? 'M50 61L37 83m22-22 12 13' : 'M50 61l-6 14m15-14 7 22'} stroke="currentColor" strokeWidth="2" />{box(44, 88, 22, 9, muted)}{flow('M55 73v11')}</>;
    case 'motion-match-transition':
    case 'centered-match-transition':
      return <><path d={p === 0 ? 'M13 83V27h24v56M78 83V20h20v63' : 'M10 83L28 29l20 54M67 83l15-61 18 61'} stroke={muted} fill="none" />
        {id === 'centered-match-transition' ? <Face scale={0.8} quiet /> : <circle cx={35 + p * 20} cy="53" r="13" stroke="currentColor" fill="none" strokeWidth="2" />}
        {p === 1 && <path d="M55 22v64" stroke={gold} strokeDasharray="3 3" />}{flow(id === 'centered-match-transition' ? 'M55 91v-17' : 'M23 78h64')}</>;
    case 'demonstration-inserts':
      return p === 1 ? <>{box(26, 29, 60, 48)}<circle cx="61" cy="53" r="9" fill="none" stroke={gold} /><path d="M16 66l30-15h12v8H45L24 76" fill="none" stroke="currentColor" strokeWidth="2" />{flow('M77 42q8 10 0 20')}</>
        : <><Face x={34} y={43} scale={0.7} quiet />{box(65, 52, 25, 24)}<path d="M42 63l27-3" stroke={gold} strokeWidth="2" /></>;
    case 'projected-light-performance':
      return <><Face quiet /><g fill={gold} opacity="0.18" transform={`translate(${p * 8} 0)`}><path d="M15 27h13l20 57H35zM44 27h13l20 57H64z" /></g>{p === 1 && flow('M25 22h53')}</>;
    case 'macro-contact-texture':
      return <><path d="M15 68q10-8 20 0t20 0t20 0t20 0M15 77q10-8 20 0t20 0t20 0" fill="none" stroke="currentColor" strokeWidth="2" />
        {p < 2 ? <><path d={`M55 ${p === 0 ? 27 : 48}q-15 20 0 20q15 0 0-20`} fill={gold} opacity="0.8" />{p === 0 && flow('M77 33v21')}</> : <><ellipse cx="55" cy="67" rx="21" ry="5" stroke={gold} fill="#fbbf2420" /><path d="M25 57l-7-5m66 5 7-5" stroke={gold} /></>}</>;
    case 'performance-arc':
      return <><Face scale={0.7 + p * 0.2} quiet={p < 2} /><path d={p === 0 ? 'M57 38l7 4-7 3' : 'M50 43h2m8 0h2'} fill="none" stroke={gold} />{flow('M18 80Q51 103 93 73')}</>;
    case 'impact-cut':
      return <>{box(74, 44, 16, 34, muted)}<path d={p === 0 ? 'M20 38l25 9 13-18' : p === 1 ? 'M16 54h58' : 'M18 65l24-8h29'} fill="none" stroke="currentColor" strokeWidth={p === 1 ? 7 : 3} strokeLinecap="round" />
        {p === 1 ? <path d="M72 48l-4-12m10 10 8-13m-5 20 15 1" stroke={gold} strokeWidth="2" /> : p === 0 ? flow('M42 61h23') : <path d="M77 40v-8m10 9 5-6" stroke={gold} />}</>;
    case 'listener-countershot':
      return p === 0 ? <><Face x={30} scale={0.7} /><Face x={81} scale={0.7} quiet />{flow('M44 34h21')}</>
        : <><path d="M5 90q-4-31 14-40l-3-21" fill="none" stroke={muted} strokeWidth="10" /><Face x={65} quiet /><path d={p === 1 ? 'M56 40l5 2m10-2 5-2' : 'M57 42h4m9 0h4'} stroke={gold} /></>;
    case 'contrasting-energy-pair':
      return <><g transform={`rotate(${p === 1 ? -12 : 0} 30 58)`}><Face x={29} scale={p === 2 ? 0.55 : 0.7} /></g><Face x={80} scale={p === 2 ? 0.92 : 0.7} quiet />
        {p < 2 && <path d="M8 30l-4-5m12 0-2-7M41 59l9-10" stroke={gold} />}<path d="M71 74h17" stroke={gold} /></>;
    case 'event-aftershock':
      return <><path d="M17 80V28h25" stroke={muted} fill="none" /><g transform={`rotate(${p === 1 ? 12 : 0} 66 62)`}><Face x={65} scale={0.85} quiet={p === 2} /></g>
        {p === 1 ? <path d="M20 43l-9-6m11 14H8m14 9-9 7" stroke={gold} strokeWidth="2" /> : p === 2 ? <path d="M85 79h12m-12 5h12" stroke={gold} /> : <circle cx="21" cy="49" r="3" fill={gold} />}</>;
    case 'rack-focus-reveal':
      return <><g opacity={p === 0 ? 0.25 : p === 1 ? 0.6 : 1}><Face x={77} y={39} scale={0.7} quiet /></g><g opacity={p === 2 ? 0.25 : p === 1 ? 0.6 : 1}>{box(18, 54, 30, 27)}<path d="M23 61h20m-20 7h20" stroke="currentColor" /></g>
        {p === 1 ? flow('M40 47L68 35') : <path d={p === 0 ? 'M13 58v-9h10m20 36h10v-9' : 'M58 27v-9h10m18 39h10v-9'} fill="none" stroke={gold} />}</>;
    case 'speech-settle':
      return <><Face quiet={p > 0} />{p === 0 ? <path d="M79 43h17m-15 6h11m-11 6h15" stroke={gold} /> : p === 1 ? <path d="M47 58h16" stroke={gold} /> : <>{flow('M72 43h15')}<path d="M82 69v10m5-10v10" stroke={gold} /></>}</>;
    default: return null;
  }
}
