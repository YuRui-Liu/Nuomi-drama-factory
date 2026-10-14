import type { ConstraintRecord, DefinitionVersion, FieldType } from '@/api/creativeSettings';
import { control } from './DefinitionEditor';
export interface ConstraintForm {
  key: string;
  value: string;
  type: FieldType;
  level: 'draft' | 'preference';
  citation: string;
  ruleDefinition: string;
  ruleField: string;
  ruleRevision: number;
}
export const blankConstraint: ConstraintForm = {
  key: '',
  value: '',
  type: 'string',
  level: 'draft',
  citation: '',
  ruleDefinition: '',
  ruleField: '',
  ruleRevision: 0,
};
export function constraintForm(record: ConstraintRecord): ConstraintForm {
  return {
    key: record.key,
    value: String(record.value),
    type:
      typeof record.value === 'boolean'
        ? 'boolean'
        : typeof record.value === 'number'
          ? 'number'
          : 'string',
    level: record.level === 'preference' ? 'preference' : 'draft',
    citation: record.citation,
    ruleDefinition: record.rule?.kind.replace('custom:', '') ?? '',
    ruleField: record.rule?.field ?? '',
    ruleRevision: record.rule?.schema_revision ?? 0,
  };
}
export function ConstraintEditor({
  value,
  onChange,
  definitions,
  disabled,
}: {
  value: ConstraintForm;
  onChange: (form: ConstraintForm) => void;
  definitions: DefinitionVersion[];
  disabled: boolean;
}) {
  const definition = definitions.find(
    (d) => d.name === value.ruleDefinition && d.revision === value.ruleRevision,
  );
  return (
    <fieldset disabled={disabled} className="space-y-3">
      <label className="block text-sm">
        约定主题
        <input
          className={control}
          value={value.key}
          onChange={(e) => onChange({ ...value, key: e.target.value })}
        />
      </label>
      <label className="block text-sm">
        约定类型
        <select
          className={control}
          value={value.type}
          disabled={!!value.ruleDefinition}
          onChange={(e) =>
            onChange({
              ...value,
              type: e.target.value as FieldType,
              value: e.target.value === 'boolean' ? 'true' : '',
            })
          }
        >
          {Object.entries({
            string: '文本',
            integer: '整数',
            number: '数值',
            boolean: '是或否',
          }).map(([k, v]) => (
            <option key={k} value={k}>
              {v}
            </option>
          ))}
        </select>
      </label>
      <label className="block text-sm">
        约定内容
        {value.type === 'boolean' ? (
          <select
            className={control}
            value={value.value}
            onChange={(e) => onChange({ ...value, value: e.target.value })}
          >
            <option value="true">是</option>
            <option value="false">否</option>
          </select>
        ) : (
          <textarea
            className={control}
            rows={3}
            value={value.value}
            onChange={(e) => onChange({ ...value, value: e.target.value })}
          />
        )}
      </label>
      <label className="block text-sm">
        保存级别
        <select
          className={control}
          value={value.level}
          onChange={(e) => onChange({ ...value, level: e.target.value as ConstraintForm['level'] })}
        >
          <option value="draft">草稿，尚不生效</option>
          <option value="preference">偏好</option>
        </select>
      </label>
      <label className="block text-sm">
        内容来源
        <textarea
          className={control}
          rows={2}
          value={value.citation}
          onChange={(e) => onChange({ ...value, citation: e.target.value })}
        />
      </label>
      <details className="rounded-lg border p-3">
        <summary className="cursor-pointer text-sm">可选：约束创作对象的属性</summary>
        <p className="my-2 text-xs text-muted-foreground">
          仅校验明确选择的属性等于约定值；普通文字约定不会自动变成执行规则。
        </p>
        <label className="block text-sm">
          对象定义
          <select
            className={control}
            value={value.ruleDefinition}
            onChange={(e) => {
              const selected = definitions.find((d) => d.name === e.target.value);
              onChange({
                ...value,
                ruleDefinition: e.target.value,
                ruleRevision: selected?.revision ?? 0,
                ruleField: '',
              });
            }}
          >
            <option value="">不设属性规则</option>
            {definitions
              .filter((d) => d.category === 'object' && d.active_revision === d.revision)
              .map((d) => (
                <option key={d.name} value={d.name}>
                  {d.definition.title} · 第{d.revision}版
                </option>
              ))}
          </select>
        </label>
        {value.ruleDefinition && (
          <label className="block text-sm">
            属性等于
            <select
              className={control}
              value={value.ruleField}
              onChange={(e) => {
                const type = definition?.definition.fields[e.target.value]?.type ?? 'string';
                onChange({
                  ...value,
                  ruleField: e.target.value,
                  type,
                  value: type === 'boolean' ? 'true' : '',
                });
              }}
            >
              <option value="">请选择属性</option>
              {Object.keys(definition?.definition.fields ?? {}).map((k) => (
                <option key={k} value={k}>
                  {definition?.definition.fields[k]?.title ?? k}
                </option>
              ))}
            </select>
          </label>
        )}
      </details>
    </fieldset>
  );
}
export const levelLabel = { draft: '草稿', preference: '偏好', hard: '硬约定' };
export function ConstraintSummary({ record, scope }: { record: ConstraintRecord; scope: string }) {
  return (
    <div className="space-y-1 text-sm">
      <p className="font-medium">
        {record.key}：{String(record.value)}
      </p>
      <p>
        {levelLabel[record.level]} · 第{record.revision}版 · {scope}
      </p>
      <p>
        来源：{record.source.kind === 'user' ? '用户' : 'Agent 草案'} · {record.source.actor_id}；
        {record.citation}
      </p>
      {record.confirmation && (
        <p>
          确认依据：{record.confirmation.basis} · {record.confirmation.actor_id}
        </p>
      )}
      {record.rule && (
        <p>
          属性规则：{record.rule.field} 等于 {String(record.value)} · 定义第
          {record.rule.schema_revision}版
        </p>
      )}
      {record.scope_status === 'inactive' && (
        <p className="text-amber-600">目标已失效，此约定不再生效；仅可保留原内容归档为草稿。</p>
      )}
    </div>
  );
}
