import { useQuery } from '@tanstack/react-query';
import { listDirectionCatalog } from '@/api/directions';
import {
  listAgentStudioResources,
  type AgentChanges,
  type AgentConfiguration,
  type AgentDefault,
  type FrozenStudio,
  type StudioRef,
} from '@/api/reusableAgents';
import type { AgentOwner } from './agentOperation';
export const agentFields: Record<keyof AgentConfiguration, string> = {
  name: '名称',
  responsibilities: '职责',
  methods: '创作方法',
  references: '参考作品',
  skill_notes: '技能说明',
  capabilities: '可用能力',
  defaults: '默认风格与手法',
  studio_refs: 'Studio 固定参考',
};
const inputClass = 'w-full rounded-md border border-input bg-background px-3 py-2 text-sm';
export const emptyAgent: AgentConfiguration = {
  name: '',
  responsibilities: '',
  methods: '',
  references: [],
  skill_notes: [],
  capabilities: [],
  defaults: {},
  studio_refs: [],
};
export const sourceLabel = (source?: { kind: string; project_id?: string }) =>
  !source
    ? '历史来源未记录'
    : source.kind === 'global_catalog'
      ? '全局目录'
      : `来源项目：${source.project_id ?? '未记录'}`;
function DefaultLabel({ value }: { value: AgentDefault }) {
  return (
    <span>
      {value.title ?? value.id} ·{' '}
      {value.version
        ? `版本 ${value.version.length > 10 ? value.version.slice(0, 8) : value.version}`
        : '固定选择'}{' '}
      · {sourceLabel(value.source)}
    </span>
  );
}
function StudioLabel({ value }: { value: StudioRef | FrozenStudio }) {
  return 'ref' in value ? (
    <span>
      {value.name} · v{value.ref.revision} · {sourceLabel(value.source)}
      {value.excluded_fields?.length ? (
        <small className="block text-muted-foreground">
          已排除非配置字段：{value.excluded_fields.join('、')}
        </small>
      ) : null}
    </span>
  ) : (
    <span>
      {value.kind} · v{value.revision}
    </span>
  );
}
const capabilityTitles: Record<string, string> = {
  'context.read': '读取已授权创作上下文',
  'proposal.create': '提出创作修改建议',
  'ontology.draft': '提出对象定义草案',
  'media.propose': '提出媒体生成方案',
};
export function AgentSummary({ configuration }: { configuration: AgentConfiguration }) {
  return (
    <dl className="space-y-3 text-sm">
      {(Object.keys(agentFields) as (keyof AgentConfiguration)[]).map((key) => (
        <div key={key}>
          <dt className="font-medium">{agentFields[key]}</dt>
          <dd className="mt-1 whitespace-pre-wrap break-words text-muted-foreground">
            {key === 'defaults'
              ? Object.values(configuration.defaults).map((v, i) => (
                  <div key={i}>
                    <DefaultLabel value={v} />
                  </div>
                ))
              : key === 'studio_refs'
                ? configuration.studio_refs.map((v, i) => (
                    <div key={i}>
                      <StudioLabel value={v} />
                    </div>
                  ))
                : Array.isArray(configuration[key])
                  ? (configuration[key] as string[])
                      .map((value) =>
                        key === 'capabilities'
                          ? (capabilityTitles[value] ?? '历史能力（目录已变更）')
                          : value,
                      )
                      .join('、') || '未设置'
                  : String(configuration[key] || '未设置')}
          </dd>
        </div>
      ))}
    </dl>
  );
}
export function AgentEditor({
  owner,
  base,
  inherited,
  changes,
  onChange,
  capabilities,
  disabled = false,
}: {
  owner: AgentOwner;
  base: AgentConfiguration;
  inherited?: AgentConfiguration;
  changes: AgentChanges;
  onChange: (value: AgentChanges) => void;
  capabilities: { id: string; title: string; runtime_permission: string }[];
  disabled?: boolean;
}) {
  const value = {
    ...base,
    ...Object.fromEntries(
      Object.entries(changes).map(([k, v]) => [
        k,
        v === null ? (inherited ?? base)[k as keyof AgentConfiguration] : v,
      ]),
    ),
  } as AgentConfiguration;
  const defaultsEditing =
    Object.prototype.hasOwnProperty.call(changes, 'defaults') && changes.defaults !== null;
  const studiosEditing =
    Object.prototype.hasOwnProperty.call(changes, 'studio_refs') && changes.studio_refs !== null;
  const styles = useQuery({
    queryKey: ['agent-resource-styles', owner.user, owner.project],
    queryFn: () => listDirectionCatalog(owner.project, 'style'),
    enabled: defaultsEditing,
  });
  const techniques = useQuery({
    queryKey: ['agent-resource-techniques', owner.user, owner.project],
    queryFn: () => listDirectionCatalog(owner.project, 'technique'),
    enabled: defaultsEditing,
  });
  const studios = useQuery({
    queryKey: ['agent-studio-resources', owner.user, owner.project],
    queryFn: () => listAgentStudioResources(owner.project),
    enabled: studiosEditing,
  });
  const update = <K extends keyof AgentConfiguration>(key: K, next: AgentConfiguration[K]) =>
    onChange({ ...changes, [key]: next });
  return (
    <fieldset disabled={disabled} className="space-y-4 disabled:opacity-70">
      {(['name', 'responsibilities', 'methods'] as const).map((key) => (
        <label key={key} className="block space-y-1 text-sm">
          <span>{agentFields[key]}</span>
          {key === 'name' ? (
            <input
              className={inputClass}
              value={value[key]}
              onChange={(e) => update(key, e.target.value)}
            />
          ) : (
            <textarea
              className={inputClass}
              rows={3}
              value={value[key]}
              onChange={(e) => update(key, e.target.value)}
            />
          )}
        </label>
      ))}
      {(['references', 'skill_notes'] as const).map((key) => (
        <label key={key} className="block space-y-1 text-sm">
          <span>{agentFields[key]}（每行一项）</span>
          <textarea
            className={inputClass}
            rows={2}
            value={value[key].join('\n')}
            onChange={(e) => update(key, e.target.value.split('\n'))}
          />
        </label>
      ))}
      <div className="space-y-2">
        <p className="text-sm font-medium">可用能力</p>
        <p className="text-xs text-muted-foreground">
          技能文本只是创作资料，不授予工具权限。实际操作以本次授权范围为准。
        </p>
        {capabilities.map((cap) => (
          <label key={cap.id} className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={value.capabilities.includes(cap.id)}
              onChange={(e) =>
                update(
                  'capabilities',
                  e.target.checked
                    ? [...value.capabilities, cap.id]
                    : value.capabilities.filter((v) => v !== cap.id),
                )
              }
            />
            {cap.title}
          </label>
        ))}
      </div>
      <section className="space-y-2 rounded-lg border p-3">
        <p className="text-sm font-medium">默认风格与手法</p>
        {!defaultsEditing ? (
          <>
            <div className="text-xs text-muted-foreground">
              {Object.values(value.defaults).length
                ? Object.values(value.defaults).map((v, i) => (
                    <p key={i}>
                      <DefaultLabel value={v} />
                    </p>
                  ))
                : '未设置'}
            </div>
            <button
              type="button"
              className="text-sm underline"
              onClick={() => update('defaults', {})}
            >
              重新选择默认风格与手法
            </button>
            <p className="text-xs text-muted-foreground">
              未修改时保留原固定版本；重新选择将替换这一组默认配置。
            </p>
          </>
        ) : (
          <>
            {(['style', 'technique'] as const).map((kind) => {
              const query = kind === 'style' ? styles : techniques;
              return (
                <label key={kind} className="block text-sm">
                  {kind === 'style' ? '默认风格' : '默认手法'}
                  <select
                    className={inputClass}
                    value={value.defaults[kind]?.id ?? ''}
                    onChange={(e) => {
                      const selected = query.data?.find((v) => v.id === e.target.value);
                      const next = { ...value.defaults };
                      if (selected) next[kind] = { id: selected.id, proof: selected.proof };
                      else delete next[kind];
                      update('defaults', next);
                    }}
                  >
                    <option value="">不设置</option>
                    {query.data
                      ?.filter((v) => v.available)
                      .map((v) => (
                        <option key={v.id} value={v.id}>
                          {v.title} · {v.version.length > 10 ? v.version.slice(0, 8) : v.version}
                        </option>
                      ))}
                  </select>
                  {query.isLoading && <span>正在读取目录…</span>}
                  {query.error && <span role="alert">目录读取失败，请重试</span>}
                </label>
              );
            })}
          </>
        )}
      </section>
      <section className="space-y-2 rounded-lg border p-3">
        <p className="text-sm font-medium">Studio 固定参考</p>
        {!studiosEditing ? (
          <>
            <div className="space-y-2 text-xs text-muted-foreground">
              {value.studio_refs.length
                ? value.studio_refs.map((v, i) => (
                    <div key={i}>
                      <StudioLabel value={v} />
                    </div>
                  ))
                : '未设置'}
            </div>
            <button
              type="button"
              className="text-sm underline"
              onClick={() => update('studio_refs', [])}
            >
              重新选择 Studio 参考
            </button>
            <p className="text-xs text-muted-foreground">
              仅从当前项目重新选择整组固定版本，不将其他项目的同名资源当作原来源。
            </p>
          </>
        ) : (
          <>
            {studios.isLoading && <p>正在读取固定版本…</p>}
            {studios.error && <p role="alert">Studio 目录读取失败</p>}
            {studios.data?.length === 0 && (
              <p className="text-xs text-muted-foreground">
                当前项目没有 Studio 资源，可先在完整工作室保存配置。
              </p>
            )}
            {studios.data?.map((resource) => {
              const checked = value.studio_refs.some(
                (v) =>
                  !('ref' in v) &&
                  v.id === resource.id &&
                  v.revision === resource.revision &&
                  v.kind === resource.kind,
              );
              return (
                <label
                  key={`${resource.kind}:${resource.id}:${resource.revision}`}
                  className="flex items-start gap-2 text-sm"
                >
                  <input
                    type="checkbox"
                    disabled={!resource.portable}
                    checked={checked}
                    onChange={(e) => {
                      const refs = value.studio_refs.filter(
                        (v) =>
                          !('ref' in v) &&
                          !(
                            v.id === resource.id &&
                            v.revision === resource.revision &&
                            v.kind === resource.kind
                          ),
                      );
                      update(
                        'studio_refs',
                        e.target.checked
                          ? [
                              ...refs,
                              { kind: resource.kind, id: resource.id, revision: resource.revision },
                            ]
                          : refs,
                      );
                    }}
                  />
                  <span>
                    {resource.name} · v{resource.revision}
                    <small className="block text-muted-foreground">
                      {resource.portable ? '固定文字配置' : resource.reason}
                      {resource.excluded_fields.length
                        ? `；排除：${resource.excluded_fields.join('、')}`
                        : ''}
                    </small>
                  </span>
                </label>
              );
            })}
          </>
        )}
      </section>
    </fieldset>
  );
}
