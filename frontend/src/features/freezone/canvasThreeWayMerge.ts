import type { FreezoneCanvasPayload } from '@/api/canvas';
import type { CanvasNode } from '@/stores/canvasStore';

const equal = (a: unknown, b: unknown): boolean => JSON.stringify(a) === JSON.stringify(b);
const object = (v: unknown): v is Record<string, unknown> => !!v && typeof v === 'object' && !Array.isArray(v);

export function graphReferenceConflicts(nodes: unknown[], edges: unknown[]): string[] {
  const conflicts: string[] = [];
  const byId = new Map(nodes.filter(object).map(node => [node.id, node]));
  for (const edge of edges.filter(object)) {
    for (const endpoint of ['source', 'target']) {
      if (!byId.has(edge[endpoint])) conflicts.push(`edges.${edge.id}.${endpoint}`);
    }
  }
  for (const node of byId.values()) {
    if (node.parentId && byId.get(node.parentId)?.type !== 'groupNode') conflicts.push(`nodes.${node.id}.parentId`);
  }
  const visited = new Set<unknown>();
  for (const id of byId.keys()) {
    const path = new Set<unknown>();
    let current: unknown = id;
    while (byId.has(current) && !visited.has(current)) {
      if (path.has(current)) { conflicts.push(`nodes.${current}.parentId`); break; }
      path.add(current);
      current = byId.get(current)?.parentId;
    }
    for (const item of path) visited.add(item);
  }
  return conflicts;
}

/** Undo is local editing, not a domain-operation undo. Pin server-owned media even when unchanged by this bind. */
export function pinHistoryBindings(nodes: CanvasNode[], remoteNodes: CanvasNode[]): CanvasNode[] {
  const remote = new Map(remoteNodes.map(node => [node.id, node]));
  return nodes.map(node => {
    const server = remote.get(node.id);
    if (!server?.data.assetRef) return node;
    const data = { ...node.data };
    for (const field of ['assetRef', 'assetMediaSelection', 'imageUrl', 'previewImageUrl', 'panoUrl', 'plyUrl', 'assetSceneManifest']) {
      if (field in server.data) data[field] = server.data[field]; else delete data[field];
    }
    if (Array.isArray(server.data.sources)) {
      const localSources = Array.isArray(node.data.sources) ? node.data.sources as Record<string, unknown>[] : [];
      data.sources = (server.data.sources as Record<string, unknown>[]).map(source => {
        const local = localSources.find(item => item.id === source.id);
        return local?.transform ? { ...source, transform: local.transform } : source;
      });
      const sources = data.sources as Record<string, unknown>[];
      const selected = sources.find(source => source.id === node.data.activeSourceId)
        ?? sources.find(source => source.id === server.data.activeSourceId);
      data.activeSourceId = selected?.id ?? server.data.activeSourceId;
      if (selected) {
        data.plyUrl = selected.ply_url ?? null;
        data.panoUrl = selected.pano_url ?? null;
      }
    }
    return { ...node, type: server.type, data };
  });
}

/** JSON field merge. Conflicting local values remain recoverable; callers must not save them. */
export function mergeCanvasOperation(base: FreezoneCanvasPayload, local: FreezoneCanvasPayload, remote: FreezoneCanvasPayload, serverWinsConflicts = false, resolveReferences = false) {
  const conflicts: string[] = [];
  const merge = (b: unknown, l: unknown, r: unknown, path: string): unknown => {
    if (equal(l, b)) return r;
    if (equal(r, b) || equal(l, r)) return l;
    if (object(l) && object(r) && (object(b) || b === undefined)) {
      const result: Record<string, unknown> = {};
      for (const key of new Set([...Object.keys(b ?? {}), ...Object.keys(l), ...Object.keys(r)])) {
        const value = merge(object(b) ? b[key] : undefined, l[key], r[key], `${path}.${key}`);
        if (value !== undefined) result[key] = value;
      }
      return result;
    }
    conflicts.push(path);
    return serverWinsConflicts ? r : l;
  };
  const graph = (key: 'nodes' | 'edges') => {
    const index = (canvas: FreezoneCanvasPayload) => new Map((canvas[key] as { id: string }[]).map(x => [x.id, x]));
    const b = index(base), l = index(local), r = index(remote);
    return [...new Set([...r.keys(), ...l.keys()])].flatMap(id => {
      const value = merge(b.get(id), l.get(id), r.get(id), `${key}.${id}`);
      return value === undefined ? [] : [value];
    });
  };
  let nodes = graph('nodes');
  let edges = graph('edges');
  if (resolveReferences) {
    const serverNodes = new Map((remote.nodes as Record<string, unknown>[]).map(node => [node.id, node]));
    const serverEdges = new Set((remote.edges as { id: string }[]).map(edge => edge.id));
    const byId = new Map(nodes.filter(object).map(node => [node.id, node]));
    edges = edges.filter(object).filter(edge => {
      for (const endpoint of ['source', 'target']) {
        if (!byId.has(edge[endpoint])) {
          const server = serverNodes.get(edge[endpoint]);
          if (!serverEdges.has(edge.id as string) || !server) return false;
          byId.set(server.id, server);
        }
      }
      return true;
    });
    for (const node of byId.values()) {
      if (node.parentId && byId.get(node.parentId)?.type !== 'groupNode') {
        const parent = serverNodes.get(node.parentId);
        if (parent?.type === 'groupNode') byId.set(parent.id, parent);
        else { const next = { ...node }; delete next.parentId; delete next.extent; byId.set(node.id, next); }
      }
    }
    nodes = [...byId.values()];
    for (let attempt = 0; attempt < nodes.length; attempt++) {
      const invalid = graphReferenceConflicts(nodes, edges).find(path => path.endsWith('.parentId'));
      if (!invalid) break;
      const id = invalid.slice('nodes.'.length, -'.parentId'.length);
      nodes = nodes.map(node => {
        if (!object(node) || node.id !== id) return node;
        const next = { ...node }; delete next.parentId; delete next.extent;
        return next;
      });
    }
  }
  // Independently valid edits can form an invalid graph when combined: a deletion
  // on one side must not strand a new edge or child introduced by the other.
  conflicts.push(...graphReferenceConflicts(nodes, edges));
  return { canvas: { ...remote, nodes, edges,
    metadata: merge(base.metadata, local.metadata, remote.metadata, 'metadata') as FreezoneCanvasPayload['metadata'],
    viewport: local.viewport ?? remote.viewport }, conflicts };
}
