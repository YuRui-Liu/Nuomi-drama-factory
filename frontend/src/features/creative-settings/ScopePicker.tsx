import type { CreativeScope, CreativeObject } from '@/api/creativeSettings';
import type { CreationCanvas } from '@/features/creation-workspace/contracts';
import { control } from './DefinitionEditor';
export interface ScopeNode {
  id: string;
  type?: string;
  parentId?: string;
  data?: Record<string, unknown>;
}
export const nodeTitle = (node: ScopeNode, index = 0) =>
  String(
    node.data?.displayName ??
      node.data?.label ??
      node.data?.title ??
      `${node.type === 'groupNode' ? '分组' : '节点'} ${index + 1}`,
  );
export function scopeTitle(
  scope: CreativeScope,
  canvas: CreationCanvas[],
  nodes: ScopeNode[],
  objects: CreativeObject[],
): string {
  if (scope.level === 'project') return '整个项目';
  if (scope.level === 'episode')
    return canvas.find((c) => c.episode_id === scope.episode_id)?.title ?? '历史集';
  if (scope.level === 'object') {
    const index = objects.findIndex((o) => o.object_id === scope.object_id);
    return index < 0 ? '历史创作对象' : `${objects[index].title} · 对象 ${index + 1}`;
  }
  const name = canvas.find((c) => c.canvas_id === scope.canvas_id)?.title ?? '历史画布';
  if (scope.level === 'canvas') return name;
  const id = scope.level === 'node' ? scope.node_id : scope.group_id;
  const index = nodes.findIndex((n) => n.id === id);
  return `${name} / ${index < 0 ? '历史目标' : nodeTitle(nodes[index], index)}`;
}
export function ScopePicker({
  value,
  onChange,
  canvasId,
  canvases,
  nodes,
  objects,
  disabled = false,
}: {
  value: CreativeScope | null;
  onChange: (scope: CreativeScope | null) => void;
  canvasId: string;
  canvases: CreationCanvas[];
  nodes: ScopeNode[];
  objects: CreativeObject[];
  disabled?: boolean;
}) {
  const level = value?.level ?? 'choose';
  return (
    <fieldset disabled={disabled} className="space-y-2">
      <label className="block text-sm">
        影响范围
        <select
          className={control}
          value={level}
          onChange={(e) => {
            const next = e.target.value;
            if (next === 'project') onChange({ level: 'project' });
            else if (next === 'canvas') onChange({ level: 'canvas', canvas_id: canvasId });
            else if (next === 'group')
              onChange({ level: 'group', canvas_id: canvasId, group_id: '' });
            else if (next === 'node') onChange({ level: 'node', canvas_id: canvasId, node_id: '' });
            else if (next === 'episode') onChange({ level: 'episode', episode_id: '' });
            else if (next === 'object') onChange({ level: 'object', object_id: '' });
            else onChange(null);
          }}
        >
          <option value="choose">请选择一个明确目标</option>
          <option value="project">整个项目</option>
          <option value="canvas" disabled={!canvases.some((c) => c.canvas_id === canvasId)}>
            当前画布
          </option>
          <option value="group">画布中的分组</option>
          <option value="node">画布中的节点</option>
          <option value="episode">集</option>
          <option value="object">创作对象</option>
        </select>
      </label>
      {(level === 'node' || level === 'group') && value && (
        <label className="block text-sm">
          具体目标
          <select
            className={control}
            value={
              value.level === 'group' ? value.group_id : value.level === 'node' ? value.node_id : ''
            }
            onChange={(e) =>
              onChange(
                level === 'group'
                  ? { level: 'group', canvas_id: canvasId, group_id: e.target.value }
                  : { level: 'node', canvas_id: canvasId, node_id: e.target.value },
              )
            }
          >
            <option value="">请选择目标</option>
            {nodes
              .filter((n) => (level === 'group' ? n.type === 'groupNode' : n.type !== 'groupNode'))
              .map((n, i) => (
                <option key={n.id} value={n.id}>
                  {nodeTitle(n, i)}
                </option>
              ))}
          </select>
        </label>
      )}
      {value?.level === 'episode' && (
        <label className="block text-sm">
          具体集
          <select
            className={control}
            value={value.episode_id}
            onChange={(e) => onChange({ level: 'episode', episode_id: e.target.value })}
          >
            <option value="">请选择集</option>
            {canvases
              .filter((c) => c.episode_id)
              .map((c) => (
                <option key={c.episode_id} value={c.episode_id!}>
                  {c.title}
                </option>
              ))}
          </select>
        </label>
      )}
      {value?.level === 'object' && (
        <label className="block text-sm">
          具体创作对象
          <select
            className={control}
            value={value.object_id}
            onChange={(e) => onChange({ level: 'object', object_id: e.target.value })}
          >
            <option value="">请选择对象</option>
            {objects.map((o, i) => (
              <option key={o.object_id} value={o.object_id}>
                {o.title} · 对象 {i + 1} · 第{o.revision}版
              </option>
            ))}
          </select>
        </label>
      )}
      {value && (
        <p className="text-xs text-muted-foreground">
          {scopeTitle(value, canvases, nodes, objects)}
        </p>
      )}
      {!value && (
        <p className="text-sm text-muted-foreground">
          多选或混合选择不会自动扩大范围，请明确一个目标后保存。
        </p>
      )}
    </fieldset>
  );
}
export const validScope = (value: CreativeScope | null) =>
  !!value && Object.values(value).every((v) => typeof v === 'string' && v.length > 0);
