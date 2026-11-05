import type { FreezoneStoryScriptRow } from '@/api/ops';
import { CANVAS_NODE_TYPES, type CanvasEdge, type CanvasNode, type StoryboardGenFrameItem } from '../domain/canvasNodes';

/** Stable content signature, not a user-facing version or an approval gate. */
export const storyboardSourceSignature = (rows: FreezoneStoryScriptRow[]) => JSON.stringify(rows);

const text = (value: unknown) => typeof value === 'string' || typeof value === 'number' ? String(value).trim() : '';
const MAX_STORYBOARD_CELLS = 4;

export function storyboardFrame(row: FreezoneStoryScriptRow, index: number): StoryboardGenFrameItem {
  const fields = [
    ['画面', row.shot_prompt || row.visual_description], ['角色', row.character],
    ['动作与站位', row.action], ['表情', row.emotion], ['景别', row.shot],
    ['场景', row.scene_tags], ['灯光', row.lighting_mood],
    ['对白', row.dialogue], ['声音', row.sound], ['运镜与动态', row.video_motion_prompt],
  ];
  const duration = Number.parseFloat(text(row.duration));
  return {
    id: `shot-${text(row.shot_no) || index + 1}`,
    description: fields.filter(([, value]) => text(value)).map(([label, value]) => `${label}：${text(value)}`).join('\n'),
    referenceIndex: null,
    ...(Number.isFinite(duration) && duration > 0 ? { duration_seconds: duration } : {}),
  };
}

/** Respect explicit narrative groups, otherwise keep consecutive shots in the same scene.
 * Split at four exact cells to keep portrait single-shot crops legible; never pad shots. */
export function buildStoryboardGroups(rows: FreezoneStoryScriptRow[]) {
  const groups: { key: string; frames: StoryboardGenFrameItem[] }[] = [];
  let lastKey = '';
  rows.forEach((row, index) => {
    const explicitKey = text(row.narrative_group) || text(row.group_id) || text(row.scene_id);
    const sceneDescription = text(row.scene_description) || text(row.scene_tags);
    // Tags and scene descriptions often append shot-specific weather, lighting,
    // or prop details. Their first token is the stable location label.
    const sceneKey = sceneDescription.split(/[、，,；;。]/, 1)[0].trim();
    const key = explicitKey || sceneKey || lastKey || '本集';
    let group = groups[groups.length - 1];
    if (!group || key !== lastKey || group.frames.length >= MAX_STORYBOARD_CELLS) {
      group = { key: `${key}:${groups.length + 1}`, frames: [] };
      groups.push(group);
    }
    group.frames.push(storyboardFrame(row, index));
    lastKey = key;
  });
  return groups.map(group => {
    const count = group.frames.length;
    // Keep the layout unambiguous: 1–3 shots use one row, four use 2×2.
    const cols = count === 4 ? 2 : count;
    return { ...group, gridCols: cols, gridRows: count / cols };
  });
}

export function findEpisodeTable(nodes: CanvasNode[], screenplayNodeId: string) {
  return nodes.find(node => node.type === CANVAS_NODE_TYPES.script && node.data.screenplaySourceNodeId === screenplayNodeId);
}

/** Reconcile only edges that this source created; manual connections remain user-owned. */
export function reconcileStoryboardReferenceEdges(edges: CanvasEdge[], sourceNodeId: string, targetNodeId: string, referenceIds: string[]): CanvasEdge[] {
  const desired = new Set(referenceIds);
  const retained = edges.filter(edge => !(edge.target === targetNodeId &&
    edge.data?.lightweightManagedReferenceForSourceNodeId === sourceNodeId && !desired.has(edge.source)));
  const added: CanvasEdge[] = [];
  desired.forEach(source => {
    if (retained.some(edge => edge.source === source && edge.target === targetNodeId)) return;
    added.push({ id: `e-${source}-${targetNodeId}`, source, target: targetNodeId,
      sourceHandle: 'source', targetHandle: 'target', type: 'disconnectableEdge',
      data: { lightweightManagedReferenceForSourceNodeId: sourceNodeId } });
  });
  return retained.length === edges.length && !added.length ? edges : [...retained, ...added];
}
