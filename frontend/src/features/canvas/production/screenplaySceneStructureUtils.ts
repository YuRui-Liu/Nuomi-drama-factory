import type { ScriptBlock } from '@/features/script-creation/types';

export interface ScreenplaySceneRange {
  id: string;
  ordinal: number;
  start: number;
  end: number;
  headerEnd: number;
  heading: string;
}

export type ScreenplayStructureOperation = 'reorder' | 'split' | 'merge';

export interface ScreenplayStructureChange {
  operation: ScreenplayStructureOperation;
  sourceRevisionId: string;
  beforeSceneIds: string[];
  retainedSceneIds: string[];
  mergedSceneIds: string[];
  newSceneHeading?: string;
  summary: string;
}

const SCENE_START = /^\s*(?:#{1,6}\s*)?(?:\d+\s*[-－]\s*\d+(?:(?:\s+|[｜|·:：,，]).*)?|(?:场次|第)?[（(]?\d+[）)]?\s*场(?:\s|[：:]|$)|(?:地点|环境|场景)[：:])/u;

function headerLines(markdown: string) {
  const result: Array<{ start: number; end: number; text: string }> = [];
  let offset = 0;
  for (const line of markdown.matchAll(/[^\n]*(?:\n|$)/gu)) {
    const raw = line[0];
    if (!raw) continue;
    const text = raw.replace(/\r?\n$/u, '');
    if (SCENE_START.test(text)) result.push({ start: offset, end: offset + raw.length, text });
    offset += raw.length;
  }
  return result;
}

export function screenplaySceneRanges(
  markdown: string,
  blocks: ScriptBlock[] = [],
  stableIds: readonly string[] = [],
): ScreenplaySceneRange[] {
  const headers = headerLines(markdown);
  let blockStart = 0;
  const positioned = blocks.map((block) => {
    const result = { ...block, start: blockStart, end: blockStart + block.markdown.length };
    blockStart = result.end;
    return result;
  });
  return headers.map((header, index) => {
    const owner = positioned.find((block) => header.start >= block.start && header.start < block.end);
    const offsetInBlock = owner ? header.start - owner.start : header.start;
    return {
      id: stableIds[index] ?? (owner ? `${owner.id}:${offsetInBlock}` : `scene:${header.start}`),
      ordinal: index + 1,
      start: header.start,
      end: headers[index + 1]?.start ?? markdown.length,
      headerEnd: header.end,
      heading: header.text.trim(),
    };
  });
}

function renumberHeading(heading: string, ordinal: number): string {
  const numbered = heading.match(/^(\s*(?:#{1,6}\s*)?\d+\s*[-－]\s*)(\d+)(.*)$/u);
  if (numbered) return `${numbered[1]}${ordinal}${numbered[3]}`;
  const chinese = heading.match(/^(\s*(?:#{1,6}\s*)?(?:第|场次\s*)(?:[（(])?)(\d+)(\s*[）)]?\s*场.*)$/u);
  if (chinese) return `${chinese[1]}${ordinal}${chinese[3]}`;
  return heading;
}

function renumberScenes(markdown: string): string {
  const ranges = screenplaySceneRanges(markdown);
  if (!ranges.length) return markdown;
  let result = markdown;
  for (const scene of [...ranges].reverse()) {
    const line = result.slice(scene.start, scene.headerEnd);
    const ending = line.endsWith('\r\n') ? '\r\n' : line.endsWith('\n') ? '\n' : '';
    const current = line.slice(0, line.length - ending.length);
    const next = renumberHeading(current, scene.ordinal);
    if (next !== current) result = result.slice(0, scene.start) + next + ending + result.slice(scene.headerEnd);
  }
  return result;
}

export function reorderScreenplayScene(markdown: string, scenes: ScreenplaySceneRange[], index: number, delta: -1 | 1): string | null {
  const target = index + delta;
  if (target < 0 || target >= scenes.length) return null;
  const before = markdown.slice(0, scenes[0].start);
  const chunks = scenes.map((scene) => markdown.slice(scene.start, scene.end));
  [chunks[index], chunks[target]] = [chunks[target], chunks[index]];
  return renumberScenes(before + chunks.map((chunk, position) =>
    position < chunks.length - 1 && !chunk.endsWith('\n') ? `${chunk}\n\n` : chunk,
  ).join(''));
}

export function mergeScreenplayScenes(markdown: string, scenes: ScreenplaySceneRange[], index: number): string | null {
  const first = scenes[index];
  const second = scenes[index + 1];
  if (!first || !second) return null;
  const firstChunk = markdown.slice(first.start, first.end);
  const secondChunk = markdown.slice(second.start, second.end);
  const body = secondChunk.slice(second.headerEnd - second.start);
  const separator = firstChunk.endsWith('\n\n') || !body ? '' : firstChunk.endsWith('\n') ? '\n' : '\n\n';
  const merged = markdown.slice(0, first.start) + firstChunk + separator + body + markdown.slice(second.end);
  return renumberScenes(merged);
}

export function splitScreenplayScene(
  markdown: string,
  scenes: ScreenplaySceneRange[],
  selectionStart: number,
  heading: string,
): string | null {
  const splitAt = screenplaySelectionOffset(selectionStart, markdown);
  const index = splitAt;
  const scene = scenes.find((item) => index >= item.start && index < item.end);
  if (!scene || index < scene.headerEnd || !heading.trim()) return null;
  if (splitAt > 0 && markdown[splitAt - 1] !== '\n') return null;
  const header = heading.trim();
  if (!SCENE_START.test(header)) return null;
  const before = markdown.slice(0, splitAt).replace(/[ \t]+$/u, '').replace(/\n{2,}$/u, '\n');
  const after = markdown.slice(splitAt).replace(/^\n+/u, '');
  return renumberScenes(`${before}\n\n${header}\n\n${after}`);
}

export function screenplaySelectionOffset(codePointOffset: number, markdown: string): number {
  return Array.from(markdown).slice(0, codePointOffset).join('').length;
}
