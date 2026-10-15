import type { FrozenTemplate, TemplateOrigin, TemplateActivation } from '@/api/creativeTemplates';
import type { Scalar } from '@/api/creativeSettings';
import { AgentSummary } from '@/features/agent-configuration/AgentEditor';
import { levelLabel } from '@/features/creative-settings/ConstraintEditor';
export function TemplateOriginLabel({ origin }: { origin: TemplateOrigin }) {
  return (
    <details className="text-xs text-muted-foreground">
      <summary>固定来源{origin.revision ? ` · 第${origin.revision}版` : ''}</summary>
      <p>项目：{origin.project_id}</p>
      {origin.name && <p>定义：{origin.name}</p>}
      {(origin.id || origin.constraint_id || origin.adoption_id) && (
        <p>来源标识：{origin.id ?? origin.constraint_id ?? origin.adoption_id}</p>
      )}
    </details>
  );
}
export function TemplateData({
  data,
  labels = {},
}: {
  data: Record<string, Scalar>;
  labels?: Record<string, string>;
}) {
  return (
    <dl className="space-y-1 text-sm">
      {Object.entries(data).map(([key, value]) => (
        <div key={key}>
          <dt className="font-medium">{labels[key] ?? key}</dt>
          <dd className="whitespace-pre-wrap break-words text-muted-foreground">{String(value)}</dd>
        </div>
      ))}
    </dl>
  );
}
export function TemplateSummary({ value }: { value: FrozenTemplate }) {
  const definition = (key: string) => value.definitions.find((d) => d.key === key)?.definition;
  const title = (key: string) => definition(key)?.title ?? '历史定义';
  const labels = (key: string) =>
    Object.fromEntries(
      Object.entries(definition(key)?.fields ?? {}).map(([k, v]) => [k, v.title ?? k]),
    );
  const exampleName = (key: string) => {
    const e = value.examples.find((x) => x.key === key);
    return e
      ? `${title(e.definition_key)} · 示例 ${value.examples.indexOf(e) + 1}`
      : '未选择的示例';
  };
  return (
    <div className="space-y-4 text-sm">
      <h4 className="font-medium">{value.name}</h4>
      <p className="whitespace-pre-wrap">{value.description}</p>
      <p className="text-muted-foreground">
        {value.definitions.length} 项定义 · {value.constraints.length} 项默认约定 ·{' '}
        {value.agents.length} 个 Agent 配置 · {value.examples.length} 个示例 ·{' '}
        {value.relations.length} 条示例关系
      </p>
      <details open>
        <summary className="cursor-pointer font-medium">创作对象与关系定义</summary>
        <div className="mt-2 space-y-3">
          {value.definitions.map((d) => (
            <article key={d.key} className="rounded border p-3">
              <p>
                {d.definition.title} · 来源第{d.origin.revision}版 ·{' '}
                {d.was_confirmed ? '源项目已确认' : '源项目草稿'}
              </p>
              {d.definition.category === 'relation' && (
                <p>
                  关系类型：
                  {value.definitions.find(
                    (v) => `custom:${v.definition.name}` === d.definition.source_kind,
                  )?.definition.title ?? '源对象'}{' '}
                  → {d.definition.title} →{' '}
                  {value.definitions.find(
                    (v) => `custom:${v.definition.name}` === d.definition.target_kind,
                  )?.definition.title ?? '目标对象'}
                </p>
              )}
              <ul>
                {Object.entries(d.definition.fields).map(([key, f]) => (
                  <li key={key}>
                    {f.title ?? key} ·{' '}
                    {{ string: '文本', integer: '整数', number: '数值', boolean: '是或否' }[f.type]}
                    {f.required ? ' · 必填' : ''}
                    {f.enum ? ` · 允许值 ${f.enum.join('、')}` : ''}
                  </li>
                ))}
              </ul>
              <TemplateOriginLabel origin={d.origin} />
            </article>
          ))}
        </div>
      </details>
      <details open>
        <summary className="cursor-pointer font-medium">默认约定（采用后仍为草稿）</summary>
        <div className="mt-2 space-y-3">
          {value.constraints.map((c, i) => (
            <article key={i} className="rounded border p-3">
              <p>
                {c.key}：{String(c.value)}
              </p>
              <p>
                源建议级别：
                {levelLabel[c.suggested_level as keyof typeof levelLabel] ?? c.suggested_level}
                ；来源说明：{c.citation}
              </p>
              {c.rule && (
                <p>
                  属性规则：
                  {c.rule_key ? (labels(c.rule_key)[c.rule.field] ?? c.rule.field) : c.rule.field}{' '}
                  等于 {String(c.value)}
                </p>
              )}
              <TemplateOriginLabel origin={c.origin} />
            </article>
          ))}
        </div>
      </details>
      <details>
        <summary className="cursor-pointer font-medium">固定 Agent 配置</summary>
        <div className="space-y-3">
          {value.agents.map((a, i) => (
            <article key={i} className="rounded border p-3">
              <AgentSummary configuration={a.configuration} />
              <TemplateOriginLabel origin={a.origin} />
            </article>
          ))}
        </div>
      </details>
      <details open>
        <summary className="cursor-pointer font-medium">明确选择的示例与关系</summary>
        <div className="mt-2 space-y-3">
          {value.examples.map((e, i) => (
            <article key={e.key} className="rounded border p-3">
              <p className="font-medium">
                {title(e.definition_key)} · 示例 {i + 1}
              </p>
              <TemplateData data={e.data} labels={labels(e.definition_key)} />
              <TemplateOriginLabel origin={e.origin} />
            </article>
          ))}
          {value.relations.map((r) => (
            <article key={r.key} className="rounded border p-3">
              <p>
                {exampleName(r.source_key)} → {title(r.definition_key)} →{' '}
                {exampleName(r.target_key)}
              </p>
              <TemplateData data={r.data} labels={labels(r.definition_key)} />
              <TemplateOriginLabel origin={r.origin} />
            </article>
          ))}
        </div>
      </details>
    </div>
  );
}
export function ActivationSummary({ value }: { value: TemplateActivation }) {
  const title = (name: string) =>
    value.definitions.find((d) => d.name === name)?.definition.title ?? name;
  return (
    <div className="space-y-3 text-sm">
      <p>
        本次启用 {value.definitions.length} 项固定定义，创建 {value.examples.length} 个示例和{' '}
        {value.relations.length} 条关系。默认约定保持草稿。
      </p>
      {value.definitions.map((d) => (
        <article key={d.name} className="rounded border p-3">
          <p>
            {d.definition.title} · 第{d.revision}版
          </p>
          <ul>
            {Object.entries(d.definition.fields).map(([key, f]) => (
              <li key={key}>
                {f.title ?? key} ·{' '}
                {{ string: '文本', integer: '整数', number: '数值', boolean: '是或否' }[f.type]}
              </li>
            ))}
          </ul>
        </article>
      ))}
      {value.examples.map((e, i) => (
        <article key={e.id} className="rounded border p-3">
          <p>
            {title(e.definition.name)} · 示例{i + 1}
          </p>
          <TemplateData
            data={e.data}
            labels={Object.fromEntries(
              Object.entries(
                value.definitions.find((d) => d.name === e.definition.name)?.definition.fields ??
                  {},
              ).map(([k, f]) => [k, f.title ?? k]),
            )}
          />
          <TemplateOriginLabel origin={e.origin} />
        </article>
      ))}
      {value.relations.map((r) => (
        <article key={r.id} className="rounded border p-3">
          <p>
            示例{value.examples.findIndex((e) => e.id === r.source_id) + 1} →{' '}
            {title(r.definition.name)} → 示例
            {value.examples.findIndex((e) => e.id === r.target_id) + 1}
          </p>
          <TemplateData data={r.data} />
          <TemplateOriginLabel origin={r.origin} />
        </article>
      ))}
    </div>
  );
}
