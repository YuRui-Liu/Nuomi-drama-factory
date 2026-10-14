import { Button } from '@/components/ui/button';
import type {
  CreativeDefinition,
  DefinitionVersion,
  FieldType,
  Scalar,
} from '@/api/creativeSettings';
export const control = 'w-full rounded-md border border-input bg-background px-3 py-2 text-sm';
export interface DefinitionForm {
  name: string;
  title: string;
  category: 'object' | 'relation';
  fields: { name: string; title: string; type: FieldType; required: boolean; choices: string }[];
  source_kind: string;
  target_kind: string;
}
export const blankDefinition = (category: 'object' | 'relation'): DefinitionForm => ({
  name: `${category}_${crypto.randomUUID().replace(/-/g, '').slice(0, 12)}`,
  title: '',
  category,
  fields: [],
  source_kind: 'character',
  target_kind: 'character',
});
export function definitionForm(value: CreativeDefinition): DefinitionForm {
  return {
    ...value,
    source_kind: value.source_kind ?? 'character',
    target_kind: value.target_kind ?? 'character',
    fields: Object.entries(value.fields).map(([name, rule]) => ({
      name,
      title: rule.title ?? name,
      type: rule.type,
      required: !!rule.required,
      choices: rule.enum?.map(String).join('\n') ?? '',
    })),
  };
}
export function scalarValue(raw: string, type: FieldType): Scalar {
  if (type === 'string') return raw;
  if (type === 'boolean') {
    if (raw !== 'true' && raw !== 'false') throw new Error('布尔值请选择是或否');
    return raw === 'true';
  }
  if (!raw.trim()) throw new Error('请输入数值');
  const value = Number(raw);
  if (!Number.isFinite(value) || (type === 'integer' && !Number.isInteger(value)))
    throw new Error('数值类型不符');
  return value;
}
export function definitionPayload(form: DefinitionForm): CreativeDefinition {
  const fields: CreativeDefinition['fields'] = {};
  for (const row of form.fields) {
    if (!row.name.trim() || row.name in fields) throw new Error('属性名称不能为空或重复');
    fields[row.name] = {
      title: row.title,
      type: row.type,
      required: row.required,
      ...(row.choices.trim()
        ? { enum: row.choices.split('\n').map((v) => scalarValue(v.trim(), row.type)) }
        : {}),
    };
  }
  return {
    name: form.name,
    title: form.title,
    category: form.category,
    fields,
    ...(form.category === 'relation'
      ? { source_kind: form.source_kind, target_kind: form.target_kind }
      : {}),
  };
}
export function DefinitionEditor({
  value,
  onChange,
  fixedName,
  definitions,
  disabled,
}: {
  value: DefinitionForm;
  onChange: (value: DefinitionForm) => void;
  fixedName: boolean;
  definitions: DefinitionVersion[];
  disabled: boolean;
}) {
  const kinds = [
    { id: 'character', title: '角色' },
    { id: 'scene', title: '场景' },
    { id: 'episode', title: '集' },
    ...definitions
      .filter((d) => d.category === 'object' && d.active_revision > 0)
      .map((d) => ({ id: `custom:${d.name}`, title: d.definition.title })),
  ];
  return (
    <fieldset disabled={disabled} className="space-y-4">
      <label className="block text-sm">
        显示名称
        <input
          className={control}
          value={value.title}
          onChange={(e) => onChange({ ...value, title: e.target.value })}
        />
      </label>
      <details>
        <summary className="cursor-pointer text-sm">高级：稳定标识</summary>
        <label className="block text-sm">
          稳定名称
          <input
            className={control}
            disabled={fixedName}
            value={value.name}
            onChange={(e) => onChange({ ...value, name: e.target.value })}
          />
          <span className="text-xs text-muted-foreground">
            使用小写字母、数字和下划线；启用后用于识别此定义。
          </span>
        </label>
      </details>
      {value.category === 'relation' && (
        <div className="grid grid-cols-2 gap-3">
          {(['source_kind', 'target_kind'] as const).map((key) => (
            <label key={key} className="text-sm">
              {key === 'source_kind' ? '起点类型' : '终点类型'}
              <select
                className={control}
                value={value[key]}
                onChange={(e) => onChange({ ...value, [key]: e.target.value })}
              >
                {kinds.map((k) => (
                  <option key={k.id} value={k.id}>
                    {k.title}
                  </option>
                ))}
              </select>
            </label>
          ))}
        </div>
      )}
      <p className="text-sm font-medium">属性</p>
      {value.fields.map((row, index) => (
        <div key={index} className="space-y-2 rounded-lg border p-3">
          <div className="grid gap-2 sm:grid-cols-2">
            <label className="text-sm">
              属性名称 {index + 1}
              <input
                className={control}
                value={row.title}
                onChange={(e) =>
                  onChange({
                    ...value,
                    fields: value.fields.map((f, i) =>
                      i === index ? { ...f, title: e.target.value } : f,
                    ),
                  })
                }
              />
            </label>
            <label className="text-sm">
              属性类型 {index + 1}
              <select
                className={control}
                value={row.type}
                onChange={(e) =>
                  onChange({
                    ...value,
                    fields: value.fields.map((f, i) =>
                      i === index ? { ...f, type: e.target.value as FieldType, choices: '' } : f,
                    ),
                  })
                }
              >
                {Object.entries({
                  string: '文本',
                  integer: '整数',
                  number: '数值',
                  boolean: '是或否',
                }).map(([key, label]) => (
                  <option key={key} value={key}>
                    {label}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <details>
            <summary className="cursor-pointer text-xs">查看稳定属性标识</summary>
            <p className="text-xs text-muted-foreground">{row.name}</p>
          </details>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={row.required}
              onChange={(e) =>
                onChange({
                  ...value,
                  fields: value.fields.map((f, i) =>
                    i === index ? { ...f, required: e.target.checked } : f,
                  ),
                })
              }
            />
            必填
          </label>
          <label className="block text-sm">
            允许值 {index + 1}（可选，每行一项{row.type === 'boolean' ? '，使用 true / false' : ''}
            ）
            <textarea
              className={control}
              rows={2}
              value={row.choices}
              onChange={(e) =>
                onChange({
                  ...value,
                  fields: value.fields.map((f, i) =>
                    i === index ? { ...f, choices: e.target.value } : f,
                  ),
                })
              }
            />
          </label>
          <Button
            size="sm"
            variant="outline"
            onClick={() =>
              onChange({ ...value, fields: value.fields.filter((_, i) => i !== index) })
            }
          >
            移除属性 {index + 1}
          </Button>
        </div>
      ))}
      <Button
        variant="outline"
        onClick={() =>
          onChange({
            ...value,
            fields: [
              ...value.fields,
              {
                name: `field_${crypto.randomUUID().replace(/-/g, '').slice(0, 12)}`,
                title: '',
                type: 'string',
                required: false,
                choices: '',
              },
            ],
          })
        }
      >
        添加属性
      </Button>
    </fieldset>
  );
}
export function DefinitionSummary({
  value,
  definitions = [],
}: {
  value: DefinitionVersion;
  definitions?: DefinitionVersion[];
}) {
  const builtinTitles: Record<string, string> = { character: '角色', scene: '场景', episode: '集' };
  const kindTitle = (kind?: string) =>
    builtinTitles[kind ?? ''] ??
    definitions.find((d) => `custom:${d.name}` === kind)?.definition.title ??
    kind?.replace('custom:', '') ??
    '未记录类型';
  return (
    <div className="space-y-2 text-sm">
      <p>
        {value.definition.title} · 固定第 {value.revision} 版
      </p>
      <p>
        来源：{value.source.kind === 'user' ? '用户' : 'Agent 草案'} · {value.source.actor_id}
      </p>
      {value.definition.category === 'relation' && (
        <p>
          关系类型：{kindTitle(value.definition.source_kind)} → {value.definition.title} →{' '}
          {kindTitle(value.definition.target_kind)}
        </p>
      )}
      <ul className="space-y-1">
        {Object.entries(value.definition.fields).map(([name, rule]) => (
          <li key={name}>
            {rule.title ?? name} ·{' '}
            {{ string: '文本', integer: '整数', number: '数值', boolean: '是或否' }[rule.type]}
            {rule.required ? ' · 必填' : ''}
            {rule.enum ? ` · 允许值：${rule.enum.join('、')}` : ''}
          </li>
        ))}
      </ul>
      {value.confirmations.map((c) => (
        <p key={c.operation_id}>
          启用依据：{c.basis} · {c.actor_id}
        </p>
      ))}
    </div>
  );
}
