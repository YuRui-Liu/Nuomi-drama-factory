// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useCallback, useEffect, useId, useMemo, useRef, useState, type PointerEvent } from "react";
import { Maximize2, Minus, Network, Plus, RotateCcw, X } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import type {
  KnowledgeGraphNode,
  KnowledgeGraphSnapshot,
} from "@/lib/queries/ingest";
import { cn } from "@/lib/utils";
import { KnowledgeGraphEditor } from "./KnowledgeGraphEditor";
import type { KnowledgeGraphUpdate } from "@/lib/queries/knowledge-graph";

const VIEW_WIDTH = 1000;
const VIEW_HEIGHT = 520;

const TYPE_COLORS: Record<string, string> = {
  Character: "#a78bfa",
  Scene: "#22d3ee",
  Prop: "#f59e0b",
  Episode: "#34d399",
  Entity: "#a78bfa",
  EntityType: "#22d3ee",
  TextSummary: "#f472b6",
  Document: "#34d399",
  DocumentChunk: "#f59e0b",
};

interface LayoutNode extends KnowledgeGraphNode {
  x: number;
  y: number;
  radius: number;
  color: string;
}

function hashText(value: string): number {
  let hash = 2166136261;
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return hash >>> 0;
}

export function buildKnowledgeGraphLayout(graph: KnowledgeGraphSnapshot): LayoutNode[] {
  const nodes = graph.nodes.map((node, index) => {
    const seed = hashText(node.id);
    const angle = ((seed % 360) * Math.PI) / 180 + index * 2.399;
    const radius = 45 + Math.sqrt(index + 1) * 27;
    return {
      ...node,
      x: VIEW_WIDTH / 2 + Math.cos(angle) * radius,
      y: VIEW_HEIGHT / 2 + Math.sin(angle) * radius * 0.58,
      radius: Math.min(18, 7 + Math.sqrt(Math.max(1, node.degree)) * 1.8),
      color: TYPE_COLORS[node.type] ?? "#60a5fa",
      vx: 0,
      vy: 0,
    };
  });
  const indexById = new Map(nodes.map((node, index) => [node.id, index]));
  const springs = graph.edges
    .map((edge) => [indexById.get(edge.source), indexById.get(edge.target)] as const)
    .filter((pair): pair is readonly [number, number] => pair[0] != null && pair[1] != null);

  // A small deterministic force pass gives the real topology an organic layout
  // without adding another runtime dependency or persisting presentation state.
  for (let step = 0; step < 120; step += 1) {
    for (let left = 0; left < nodes.length; left += 1) {
      for (let right = left + 1; right < nodes.length; right += 1) {
        const a = nodes[left];
        const b = nodes[right];
        let dx = b.x - a.x;
        let dy = b.y - a.y;
        const distanceSquared = Math.max(64, dx * dx + dy * dy);
        const distance = Math.sqrt(distanceSquared);
        dx /= distance;
        dy /= distance;
        const force = 1350 / distanceSquared;
        a.vx -= dx * force;
        a.vy -= dy * force;
        b.vx += dx * force;
        b.vy += dy * force;
      }
    }
    for (const [sourceIndex, targetIndex] of springs) {
      if (sourceIndex === targetIndex) continue;
      const source = nodes[sourceIndex];
      const target = nodes[targetIndex];
      const dx = target.x - source.x;
      const dy = target.y - source.y;
      const distance = Math.max(1, Math.sqrt(dx * dx + dy * dy));
      const force = (distance - 82) * 0.0024;
      source.vx += (dx / distance) * force;
      source.vy += (dy / distance) * force;
      target.vx -= (dx / distance) * force;
      target.vy -= (dy / distance) * force;
    }
    for (const node of nodes) {
      node.vx += (VIEW_WIDTH / 2 - node.x) * 0.00045;
      node.vy += (VIEW_HEIGHT / 2 - node.y) * 0.0008;
      node.vx *= 0.84;
      node.vy *= 0.84;
      node.x = Math.min(VIEW_WIDTH - 35, Math.max(35, node.x + node.vx));
      node.y = Math.min(VIEW_HEIGHT - 28, Math.max(28, node.y + node.vy));
    }
  }

  return nodes.map(({ vx: _vx, vy: _vy, ...node }) => node);
}

function formatProperty(value: unknown): string {
  if (value == null) return "—";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

export function KnowledgeGraphVisualization({
  graph,
  className,
  onSave,
}: {
  graph: KnowledgeGraphSnapshot;
  className?: string;
  onSave?: (update: KnowledgeGraphUpdate) => Promise<unknown>;
}) {
  const { t } = useTranslation();
  const filterId = useId().replace(/:/g, "");
  const [revealedIds, setRevealedIds] = useState<Set<string>>(() => new Set());
  const [positions, setPositions] = useState<Record<string, { x: number; y: number }>>({});
  const [search, setSearch] = useState("");
  const [editing, setEditing] = useState(false);
  const visibleGraph = useMemo(() => {
    const initial = [...graph.nodes].sort((a, b) => b.degree - a.degree).slice(0, 80);
    const ids = new Set([...initial.map((node) => node.id), ...revealedIds]);
    return { ...graph, nodes: graph.nodes.filter((node) => ids.has(node.id)), edges: graph.edges.filter((edge) => ids.has(edge.source) && ids.has(edge.target)) };
  }, [graph, revealedIds]);
  const layout = useMemo(() => buildKnowledgeGraphLayout(visibleGraph), [visibleGraph]);
  const nodes = useMemo(() => layout.map((node) => ({ ...node, ...positions[node.id] })), [layout, positions]);
  const nodesById = useMemo(
    () => new Map(nodes.map((node) => [node.id, node])),
    [nodes],
  );
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [expanded, setExpanded] = useState(false);
  const [view, setView] = useState({ x: 0, y: 0, scale: 1 });
  const dragRef = useRef<{
    pointerId: number;
    clientX: number;
    clientY: number;
    x: number;
    y: number;
    nodeId?: string;
    moved?: boolean;
  } | null>(null);
  const movedRef = useRef(false);
  const svgRef = useRef<SVGSVGElement>(null);
  const allNodesById = useMemo(() => new Map(graph.nodes.map((node) => [node.id, node])), [graph.nodes]);
  const selected = selectedId ? nodesById.get(selectedId) ?? null : null;
  const selectedRelations = useMemo(() => {
    if (!selectedId) return [];
    return graph.edges
      .filter((edge) => edge.source === selectedId || edge.target === selectedId)
      .map((edge) => {
        const neighborId = edge.source === selectedId ? edge.target : edge.source;
        return {
          id: edge.id,
          relation: edge.relation,
          neighborId,
          neighbor: allNodesById.get(neighborId)?.label ?? neighborId,
          outgoing: edge.source === selectedId,
          properties: edge.properties,
        };
      });
  }, [graph.edges, allNodesById, selectedId]);
  const selectedNeighborIds = useMemo(() => {
    if (!selectedId) return new Set<string>();
    const result = new Set<string>();
    for (const edge of graph.edges) {
      if (edge.source === selectedId) result.add(edge.target);
      if (edge.target === selectedId) result.add(edge.source);
    }
    return result;
  }, [graph.edges, selectedId]);
  const visibleLabels = useMemo(
    () => new Set([...nodes].sort((a, b) => b.degree - a.degree).slice(0, 24).map((node) => node.id)),
    [nodes],
  );

  const zoom = useCallback((factor: number) =>
    setView((current) => {
      const scale = Math.min(3.5, Math.max(0.3, current.scale * factor));
      const ratio = scale / current.scale;
      return { x: VIEW_WIDTH / 2 - (VIEW_WIDTH / 2 - current.x) * ratio, y: VIEW_HEIGHT / 2 - (VIEW_HEIGHT / 2 - current.y) * ratio, scale };
    }), []);

  useEffect(() => {
    const svg = svgRef.current;
    if (!svg) return;
    const wheel = (event: WheelEvent) => {
      event.preventDefault();
      zoom(event.deltaY < 0 ? 1.12 : 0.89);
    };
    // React delegates wheel as passive; a native listener keeps page scroll
    // from competing with graph zoom while the cursor is over the canvas.
    svg.addEventListener("wheel", wheel, { passive: false });
    return () => svg.removeEventListener("wheel", wheel);
  }, [zoom]);

  const selectNode = (id: string) => {
    if (editing) return;
    setRevealedIds((current) => new Set([...current, id]));
    setSelectedId(id);
    setSearch("");
  };
  const expandNeighbors = () => setRevealedIds((current) => new Set([...current, ...selectedNeighborIds]));

  const handlePointerDown = (event: PointerEvent<SVGSVGElement>) => {
    if (event.button !== 0) return;
    movedRef.current = false;
    event.currentTarget.setPointerCapture?.(event.pointerId);
    dragRef.current = {
      pointerId: event.pointerId,
      clientX: event.clientX,
      clientY: event.clientY,
      x: view.x,
      y: view.y,
    };
  };

  const handlePointerMove = (event: PointerEvent<SVGSVGElement>) => {
    const drag = dragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    const bounds = event.currentTarget.getBoundingClientRect();
    const ratio = Math.min(bounds.width / VIEW_WIDTH, bounds.height / VIEW_HEIGHT) || 1;
    const dx = (event.clientX - drag.clientX) / ratio;
    const dy = (event.clientY - drag.clientY) / ratio;
    if (Math.abs(dx) + Math.abs(dy) > 3) movedRef.current = true;
    if (drag.nodeId) {
      setPositions((current) => ({ ...current, [drag.nodeId!]: { x: drag.x + dx / view.scale, y: drag.y + dy / view.scale } }));
    } else {
      setView((current) => ({ ...current, x: drag.x + dx, y: drag.y + dy }));
    }
  };

  const handlePointerUp = (event: PointerEvent<SVGSVGElement>) => {
    const drag = dragRef.current;
    if (drag?.pointerId !== event.pointerId) return;
    if (drag.nodeId && !movedRef.current) {
      selectNode(drag.nodeId);
      // A captured node click is retargeted to the SVG: don't clear it again.
      movedRef.current = true;
    }
    dragRef.current = null;
    event.currentTarget.releasePointerCapture?.(event.pointerId);
  };

  return (
    <section
      aria-label={t("ingest.knowledgeGraph.title")}
      className={cn(
        "overflow-hidden rounded-xl border bg-card text-foreground",
        className,
      )}
    >
      <div className="flex flex-wrap items-center gap-3 border-b p-4">
      <div className="mr-auto flex items-center gap-3">
        <span className="flex size-9 items-center justify-center rounded-lg border bg-muted text-primary">
          <Network className="size-[18px]" />
        </span>
        <div>
          <h2 className="text-sm font-semibold tracking-wide text-foreground">
            {t("ingest.knowledgeGraph.title")}
          </h2>
          <p className="mt-0.5 text-[11px] text-muted-foreground">
            {t("ingest.knowledgeGraph.stats", {
              nodes: graph.total_nodes,
              edges: graph.total_edges,
            })}
          </p>
        </div>
      </div>

      <div className="flex items-center gap-1 rounded-lg border bg-muted/40 p-1">
        <Button type="button" variant="ghost" size="icon-xs" onClick={() => zoom(0.82)} aria-label={t("ingest.knowledgeGraph.zoomOut")}>
          <Minus className="size-3.5" />
        </Button>
        <Button type="button" variant="ghost" size="icon-xs" onClick={() => { setView({ x: 0, y: 0, scale: 1 }); setPositions({}); }} aria-label={t("ingest.knowledgeGraph.resetView")}>
          <RotateCcw className="size-3.5" />
        </Button>
        <Button type="button" variant="ghost" size="icon-xs" onClick={() => zoom(1.22)} aria-label={t("ingest.knowledgeGraph.zoomIn")}>
          <Plus className="size-3.5" />
        </Button>
        <Button type="button" variant="ghost" size="icon-xs" onClick={() => setExpanded((value) => !value)} aria-label={t("ingest.knowledgeGraph.expand")}>
          <Maximize2 className="size-3.5" />
        </Button>
      </div>

      <div className="relative w-full sm:order-first sm:w-60">
        <input aria-label="搜索图谱节点" placeholder="搜索角色、场景、剧集…" value={search} disabled={editing} onChange={(event) => setSearch(event.target.value)} className="w-full rounded-lg border border-border bg-background px-3 py-2 text-xs text-foreground outline-none focus:border-ring" />
        {search && <div className="mt-1 max-h-60 overflow-auto rounded-lg border border-border bg-popover p-1">
          {graph.nodes.filter((node) => node.label.toLowerCase().includes(search.toLowerCase())).slice(0, 30).map((node) => <button key={node.id} type="button" aria-label={`定位 ${node.label}`} onClick={() => { selectNode(node.id); setView({ x: 0, y: 0, scale: 1 }); }} className="block w-full truncate rounded px-2 py-2 text-left text-xs text-muted-foreground hover:bg-accent">{node.label} · {node.type}</button>)}
          {!graph.nodes.some((node) => node.label.toLowerCase().includes(search.toLowerCase())) && <p className="p-2 text-xs text-muted-foreground">未找到匹配节点</p>}
        </div>}
      </div>
      <span className="text-xs text-muted-foreground">{graph.editable && onSave ? "可编辑图谱" : "只读图谱"}</span>
      </div>
      <div className="grid gap-4 p-4 lg:grid-cols-[minmax(0,1fr)_320px]">
      <div className={cn("relative min-w-0 overflow-hidden rounded-lg border bg-background", expanded ? "h-[680px]" : "h-[490px]")}>
      {graph.nodes.length === 0 && <div className="absolute inset-0 z-10 flex flex-col items-center justify-center gap-2 text-sm text-muted-foreground"><p>暂无知识图谱节点</p><p className="text-xs">导入完成后可在这里查看角色、场景和剧集关系。</p></div>}

      <svg
        ref={svgRef}
        aria-label="知识图谱画布"
        viewBox={`0 0 ${VIEW_WIDTH} ${VIEW_HEIGHT}`}
        className="absolute inset-0 size-full cursor-grab touch-none active:cursor-grabbing"
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={handlePointerUp}
        onPointerCancel={() => { dragRef.current = null; movedRef.current = true; }}
        onClick={() => { if (!movedRef.current && !editing) setSelectedId(null); }}
      >
        <defs>
          <pattern id={`${filterId}-grid`} width="36" height="36" patternUnits="userSpaceOnUse">
            <circle cx="1" cy="1" r="0.8" fill="var(--border)" />
          </pattern>
        </defs>
        <rect width={VIEW_WIDTH} height={VIEW_HEIGHT} fill={`url(#${filterId}-grid)`} opacity="0.42" />
        <g transform={`translate(${view.x} ${view.y}) scale(${view.scale})`}>
          {visibleGraph.edges.map((edge) => {
            const source = nodesById.get(edge.source);
            const target = nodesById.get(edge.target);
            if (!source || !target) return null;
            const connected = selectedId === edge.source || selectedId === edge.target;
            return (
              <line
                key={edge.id}
                x1={source.x}
                y1={source.y}
                x2={target.x}
                y2={target.y}
                stroke={connected ? "var(--primary)" : "var(--muted-foreground)"}
                strokeWidth={connected ? 1.8 : 0.72}
                strokeOpacity={selectedId ? (connected ? 0.82 : 0.08) : 0.26}
              >
                <title>{`${source.label} → ${edge.relation} → ${target.label}`}</title>
              </line>
            );
          })}
          {nodes.map((node) => {
            const active = selectedId === node.id;
            const muted = selectedId != null && !active && !selectedNeighborIds.has(node.id);
            return (
              <g
                key={node.id}
                role="button"
                tabIndex={0}
                aria-label={`${node.label}, ${node.type}`}
                transform={`translate(${node.x} ${node.y})`}
                className="cursor-pointer outline-none"
                opacity={muted ? 0.24 : 1}
                onPointerDown={(event) => {
                  event.stopPropagation();
                  if (event.button !== 0) return;
                  movedRef.current = false;
                  svgRef.current?.setPointerCapture?.(event.pointerId);
                  dragRef.current = { pointerId: event.pointerId, clientX: event.clientX, clientY: event.clientY, x: node.x, y: node.y, nodeId: node.id };
                }}
                onClick={(event) => {
                  event.stopPropagation();
                  if (!movedRef.current) selectNode(node.id);
                }}
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === " ") { event.preventDefault(); selectNode(node.id); }
                }}
              >
                <title>{node.label}</title>
                <circle r={node.radius} fill="var(--card)" stroke={node.color} strokeWidth={active ? 3 : 1.4} />
                <circle r={Math.max(2.8, node.radius * 0.28)} fill={node.color} />
                {(visibleLabels.has(node.id) || active) && (
                  <text y={node.radius + 14} textAnchor="middle" fill="var(--foreground)" fontSize={active ? 11 : 9.5} fontWeight={active ? 600 : 450}>
                    {node.label.length > 15 ? `${node.label.slice(0, 14)}…` : node.label}
                  </text>
                )}
              </g>
            );
          })}
        </g>
      </svg>

      </div>
      <aside aria-label="节点详情" className={cn("relative min-w-0 overflow-y-auto rounded-lg border bg-card p-4", expanded ? "lg:max-h-[680px]" : "lg:max-h-[490px]")}>
      {selected ? (
        <>
          <button type="button" disabled={editing} onClick={() => setSelectedId(null)} className="absolute right-3 top-3 text-muted-foreground transition-colors hover:text-foreground disabled:opacity-25" aria-label={t("common.close")}>
            <X className="size-4" />
          </button>
          <span className="inline-flex rounded-full px-2 py-0.5 text-[10px] font-semibold" style={{ color: selected.color, backgroundColor: `${selected.color}18` }}>
            {selected.type}
          </span>
          <h3 className="mt-3 pr-6 text-base font-semibold leading-6 text-foreground">{selected.label}</h3>
          <p className="mt-1 text-[11px] text-muted-foreground">
            {t("ingest.knowledgeGraph.connections", { count: selected.degree })}
          </p>
          <Button type="button" variant="outline" size="sm" className="mt-3" onClick={expandNeighbors}>展开相邻节点</Button>
          {graph.editable && graph.revision && onSave && <KnowledgeGraphEditor key={selected.id} graph={graph} node={selected} onSave={onSave} onEditingChange={setEditing} />}
          {editing && <p className="mt-2 text-xs text-muted-foreground">保存或取消编辑后可切换节点。</p>}
          {selectedRelations.length > 0 && (
            <div className="mt-4 border-t border-border pt-4">
              <p className="text-[10px] uppercase tracking-wider text-muted-foreground">
                {t("ingest.knowledgeGraph.relationships")}
              </p>
              <div className="mt-2 space-y-1.5">
                {selectedRelations.map((relation) => (
                  <div key={relation.id} className="space-y-1 text-[11px]">
                    <div className="flex items-center gap-2">
                    <span className="text-muted-foreground">{relation.outgoing ? "→" : "←"}</span>
                    <span className="max-w-[118px] truncate rounded bg-muted px-1.5 py-0.5 text-foreground">
                      {relation.relation}
                    </span>
                    <button type="button" disabled={editing} onClick={() => selectNode(relation.neighborId)} className="truncate text-muted-foreground underline underline-offset-2">{relation.neighbor}</button>
                    </div>
                    {Object.entries(relation.properties).map(([key, value]) => <p key={key} className="break-words text-muted-foreground">{key}: {formatProperty(value)}</p>)}
                  </div>
                ))}
              </div>
            </div>
          )}
          <div className="mt-4 space-y-3 border-t border-border pt-4">
            {Object.entries(selected.properties).map(([key, value]) => (
              <div key={key}>
                <p className="text-[10px] uppercase tracking-wider text-muted-foreground">{key}</p>
                <p className="mt-1 whitespace-pre-wrap break-words text-xs leading-5 text-muted-foreground">{formatProperty(value)}</p>
              </div>
            ))}
          </div>
        </>
      ) : <div className="flex h-full min-h-40 flex-col items-center justify-center gap-2 text-center text-sm text-muted-foreground"><Network className="size-6" /><p>选择节点查看详情</p><p className="text-xs">查看属性、关联关系，或展开相邻节点。</p></div>}
      </aside>
      </div>

      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-t px-4 py-3 text-xs text-muted-foreground">
        <span>滚轮缩放 · 拖动画布或节点 · 点击查看及编辑</span>
        <span>显示 {nodes.length} / {graph.total_nodes} 个节点</span>
        {graph.truncated && <span>{t("ingest.knowledgeGraph.truncated")}</span>}
      </div>
    </section>
  );
}
