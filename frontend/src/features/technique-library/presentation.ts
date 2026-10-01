import type { TechniqueCard, DirectorCapabilities } from '@/api/videoDirector';
import type { DirectorSegment } from '@/features/canvas/domain/canvasNodes';
import { techniqueCompatibility } from '@/features/canvas/director/directorValidation';

export interface TechniqueContext {
  segment: DirectorSegment;
  hasReferences: boolean;
  capabilities: DirectorCapabilities | null;
  onSelect: (selection: DirectorSegment['technique']) => void;
}
export const categories = ['performance', 'relationship', 'camera', 'action', 'continuity'] as const;
export type Category = typeof categories[number];
export const techniqueKey = (card: TechniqueCard): string => `${card.id}@${card.version}`;

// Preserve curated ID order while favorites follow its latest active edition.
export function browseTechniques(cards: TechniqueCard[]): TechniqueCard[] {
  const latest = new Map<string, TechniqueCard>();
  for (const card of cards) {
    const previous = latest.get(card.id);
    if (!previous || (card.status === 'active' && previous.status !== 'active')
      || (card.status === previous.status && compareVersions(card.version, previous.version) > 0)) {
      latest.set(card.id, card);
    }
  }
  return [...latest.values()];
}

function compareVersions(a: string, b: string): number {
  const [aCore, aPre] = a.split('+')[0].split('-', 2);
  const [bCore, bPre] = b.split('+')[0].split('-', 2);
  const core = aCore.localeCompare(bCore, 'en', { numeric: true });
  if (core) return core;
  if (aPre === undefined || bPre === undefined) return Number(aPre === undefined) - Number(bPre === undefined);
  return aPre.localeCompare(bPre, 'en', { numeric: true });
}
export const categoryLabels: Record<Category, string> = { performance: '人物表演', relationship: '双人关系', camera: '镜头运动', action: '动作与揭示', continuity: '首尾帧衔接' };
export function categoryOf(card: TechniqueCard): Category {
  if (card.id === 'subject-entrance' || card.category === '动作高潮') return 'action';
  if (card.category === '双人调度') return 'relationship';
  if (card.category === '首尾帧') return 'continuity';
  if (['跟拍', '揭示运镜'].includes(card.category)) return 'camera';
  return 'performance';
}
export function incompatibility(card: TechniqueCard, context?: TechniqueContext): string | null {
  if (!context) return null;
  if (!context.capabilities) return 'capabilitiesUnavailable';
  const mode = context.hasReferences ? 'ref_only' : context.segment.lastFrame ? 'fl2v' : 'i2v';
  if (context.capabilities.modes.length && !context.capabilities.modes.some((entry) => entry.id === mode && entry.supported)) return 'modeUnavailable';
  return techniqueCompatibility(card, context.segment, context.hasReferences, context.capabilities);
}
export function durationLabel(value: number): string {
  const rounded = Math.round(value * 100) / 100;
  return `${Math.abs(value - rounded) > 1e-9 ? '≈' : ''}${rounded}`;
}
