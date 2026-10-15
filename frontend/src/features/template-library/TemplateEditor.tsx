import type { TemplateSelection, TemplateSources } from '@/api/creativeTemplates';
import { control } from '@/features/creative-settings/DefinitionEditor';
import { TemplateData } from './TemplateSummary';
export const blankSelection = (name = '', description = ''): TemplateSelection => ({
  name,
  description,
  definitions: [],
  constraints: [],
  agents: [],
  examples: [],
  relations: [],
});
export const invalidExampleRelations = (value: TemplateSelection, sources: TemplateSources) =>
  value.relations.some((id) => {
    const r = sources.relations.find((v) => v.relation_id === id);
    return (
      !r ||
      !value.examples.some((e) => e.id === r.source_id) ||
      !value.examples.some((e) => e.id === r.target_id)
    );
  });
export function TemplateEditor({
  value,
  onChange,
  sources,
  content,
  disabled,
}: {
  value: TemplateSelection;
  onChange: (value: TemplateSelection) => void;
  sources: TemplateSources;
  content: boolean;
  disabled: boolean;
}) {
  const toggle = (
    field: 'constraints' | 'agents' | 'examples',
    id: string,
    revision: number,
    checked: boolean,
  ) =>
    onChange({
      ...value,
      [field]: checked
        ? [...value[field], { id, revision }]
        : value[field].filter((r) => r.id !== id),
    });
  const exampleName = (id: string) => {
    const index = sources.objects.findIndex((o) => o.object_id === id);
    const object = sources.objects[index];
    return object
      ? `${object.title} · 示例${index + 1}${object.data ? `：${Object.values(object.data).map(String).join('、').slice(0, 60)}` : ''}`
      : '缺失示例';
  };
  return (
    <fieldset disabled={disabled} className="space-y-4">
      <label className="block text-sm">
        模板名称
        <input
          className={control}
          value={value.name}
          onChange={(e) => onChange({ ...value, name: e.target.value })}
        />
      </label>
      <label className="block text-sm">
        模板说明
        <textarea
          className={control}
          rows={3}
          value={value.description}
          onChange={(e) => onChange({ ...value, description: e.target.value })}
        />
      </label>
      {content && (
        <>
          <p className="text-sm text-muted-foreground">
            从当前项目选择固定版本。不默认带入剧本、媒体、运行历史或对象示例。预览会展示必要定义依赖与实际内容。
          </p>
          <details open className="rounded-lg border p-3">
            <summary className="cursor-pointer text-sm font-medium">创作对象与关系定义</summary>
            <div className="mt-2 space-y-2">
              {sources.definitions.map((d) => {
                const supported =
                  d.category === 'object' ||
                  (d.definition.source_kind?.startsWith('custom:') &&
                    d.definition.target_kind?.startsWith('custom:'));
                return (
                  <label key={d.name} className="flex items-start gap-2 text-sm">
                    <input
                      type="checkbox"
                      disabled={!supported}
                      checked={value.definitions.some(
                        (r) => r.name === d.name && r.revision === d.revision,
                      )}
                      onChange={(e) =>
                        onChange({
                          ...value,
                          definitions: e.target.checked
                            ? [...value.definitions, { name: d.name, revision: d.revision }]
                            : value.definitions.filter((r) => r.name !== d.name),
                        })
                      }
                    />
                    <span>
                      {d.definition.title} · 第{d.revision}版 ·{' '}
                      {d.active_revision === d.revision ? '已启用' : '草稿'}
                      {!supported && (
                        <small className="block text-muted-foreground">
                          包含专业资产端点，当前不能作为可移植关系定义。
                        </small>
                      )}
                    </span>
                  </label>
                );
              })}
              {!sources.definitions.length && (
                <p className="text-sm text-muted-foreground">项目尚无扩展定义。</p>
              )}
            </div>
          </details>
          <details open className="rounded-lg border p-3">
            <summary className="cursor-pointer text-sm font-medium">默认创作约定</summary>
            <div className="mt-2 space-y-2">
              {sources.constraints.map((c) => (
                <label key={c.constraint_id} className="flex items-start gap-2 text-sm">
                  <input
                    type="checkbox"
                    disabled={c.scope.level !== 'project'}
                    checked={value.constraints.some(
                      (r) => r.id === c.constraint_id && r.revision === c.revision,
                    )}
                    onChange={(e) =>
                      toggle('constraints', c.constraint_id, c.revision, e.target.checked)
                    }
                  />
                  <span>
                    {c.key}：{String(c.value)} · 第{c.revision}版
                    {c.scope.level !== 'project' && (
                      <small className="block text-muted-foreground">
                        局部约定不能直接放入模板，请先明确整理为项目约定。
                      </small>
                    )}
                  </span>
                </label>
              ))}
            </div>
          </details>
          <details className="rounded-lg border p-3">
            <summary className="cursor-pointer text-sm font-medium">项目固定 Agent 配置</summary>
            <div className="mt-2 space-y-2">
              {sources.agents.map((a) => (
                <label key={a.adoption_id} className="flex items-start gap-2 text-sm">
                  <input
                    type="checkbox"
                    checked={value.agents.some(
                      (r) => r.id === a.adoption_id && r.revision === a.revision,
                    )}
                    onChange={(e) => toggle('agents', a.adoption_id, a.revision, e.target.checked)}
                  />
                  {a.effective.name} · 项目修订{a.revision}
                </label>
              ))}
            </div>
          </details>
          <details className="rounded-lg border p-3">
            <summary className="cursor-pointer text-sm font-medium">可选：明确带入创作示例</summary>
            <p className="my-2 text-xs text-muted-foreground">
              仅支持创作对象与有类型关系。专业角色、场景和媒体素材不在此处复制；不可移植路径会在预览时明确拒绝。
            </p>
            <div className="space-y-3">
              {sources.objects.map((o) => (
                <article key={o.object_id} className="rounded border p-2">
                  <label className="flex items-start gap-2 text-sm">
                    <input
                      type="checkbox"
                      checked={value.examples.some(
                        (r) => r.id === o.object_id && r.revision === o.revision,
                      )}
                      onChange={(e) =>
                        toggle('examples', o.object_id, o.revision, e.target.checked)
                      }
                    />
                    {exampleName(o.object_id)} · 第{o.revision}版
                  </label>
                  {o.data && (
                    <details>
                      <summary className="cursor-pointer text-xs">查看示例内容</summary>
                      <TemplateData
                        data={o.data}
                        labels={Object.fromEntries(
                          Object.entries(
                            sources.definitions.find(
                              (d) =>
                                d.name === o.definition_name && d.revision === o.schema_revision,
                            )?.definition.fields ?? {},
                          ).map(([k, v]) => [k, v.title ?? k]),
                        )}
                      />
                    </details>
                  )}
                </article>
              ))}
              {sources.relations.map((r) => {
                const supported =
                  sources.objects.some((o) => o.object_id === r.source_id) &&
                  sources.objects.some((o) => o.object_id === r.target_id);
                return (
                  <label key={r.relation_id} className="flex items-start gap-2 text-sm">
                    <input
                      type="checkbox"
                      disabled={!supported}
                      checked={value.relations.includes(r.relation_id)}
                      onChange={(e) =>
                        onChange({
                          ...value,
                          relations: e.target.checked
                            ? [...value.relations, r.relation_id]
                            : value.relations.filter((id) => id !== r.relation_id),
                        })
                      }
                    />
                    <span>
                      {exampleName(r.source_id)} → {r.title} → {exampleName(r.target_id)}
                      {!supported && (
                        <small className="block">含不支持的专业或缺失端点，不能复制。</small>
                      )}
                    </span>
                  </label>
                );
              })}
            </div>
            {invalidExampleRelations(value, sources) && (
              <p role="alert" className="mt-2 text-sm text-destructive">
                关系两端必须同时明确选择为示例；未替你补选或删除。
              </p>
            )}
          </details>
        </>
      )}
    </fieldset>
  );
}
