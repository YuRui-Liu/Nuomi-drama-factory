import { useEffect, useRef, useState } from 'react';
import { Button } from '@/components/ui/button';
import { listCreationCanvases } from '@/api/creation';
import { getFreezoneCanvas } from '@/api/canvas';
import {
  listDefinitions,
  getDefinition,
  listConstraints,
  getConstraint,
  listCreativeObjects,
  getEffectiveConstraints,
  type CreativeObject,
  type CreativeScope,
  type DefinitionVersion,
  type ConstraintRecord,
  type ConstraintEffect,
  type SettingsRequest,
  type SettingsDomain,
} from '@/api/creativeSettings';
import type { CreationCanvas } from '@/features/creation-workspace/contracts';
import {
  DefinitionEditor,
  DefinitionSummary,
  blankDefinition,
  definitionForm,
  definitionPayload,
  scalarValue,
  control,
  type DefinitionForm,
} from './DefinitionEditor';
import {
  ConstraintEditor,
  ConstraintSummary,
  blankConstraint,
  constraintForm,
  levelLabel,
  type ConstraintForm,
} from './ConstraintEditor';
import { ScopePicker, scopeTitle, validScope, type ScopeNode } from './ScopePicker';
import {
  createSettingsOperation,
  readSettingsIntent,
  finishSettingsIntent,
  settingsOwnerKey,
  type SettingsOwner,
  type SettingsIntent,
} from './settingsOperation';
type Draft =
  | { id: string; kind: 'definition'; form: DefinitionForm; baseline?: DefinitionVersion }
  | {
      id: string;
      kind: 'constraint';
      form: ConstraintForm;
      scope: CreativeScope | null;
      baseline?: ConstraintRecord;
    };
interface Props {
  owner: SettingsOwner;
  selected: { id: string; type?: string }[];
  runtime: { flush: () => Promise<boolean | null>; isCurrent: () => boolean };
  isCurrent: () => boolean;
}
const errorText = (error: unknown) => (error instanceof Error ? error.message : String(error));
const scopeKey = (value: CreativeScope) =>
  JSON.stringify(Object.entries(value).sort(([a], [b]) => a.localeCompare(b)));
const initialScope = (canvas: string, selected: Props['selected']): CreativeScope | null =>
  selected.length === 1 && selected[0].type
    ? selected[0].type === 'groupNode'
      ? { level: 'group', canvas_id: canvas, group_id: selected[0].id }
      : { level: 'node', canvas_id: canvas, node_id: selected[0].id }
    : selected.length
      ? null
      : { level: 'canvas', canvas_id: canvas };
export function CreativeSettingsPanel(props: Props) {
  const [frozen] = useState(() => ({
    ...props,
    owner: { ...props.owner },
    selected: props.selected.map((n) => ({ ...n })),
  }));
  const { owner, runtime } = frozen;
  const alive = useRef(true),
    sequence = useRef(0);
  const current = () => alive.current && frozen.isCurrent() && runtime.isCurrent();
  const [tab, setTab] = useState<'definitions' | 'constraints'>('definitions'),
    [reload, setReload] = useState(0),
    [ready, setReady] = useState(false),
    [busy, setBusy] = useState(false),
    [error, setError] = useState('');
  const [definitions, setDefinitions] = useState<DefinitionVersion[]>([]),
    [activeDefinitions, setActiveDefinitions] = useState<DefinitionVersion[]>([]),
    [records, setRecords] = useState<ConstraintRecord[]>([]),
    [objects, setObjects] = useState<CreativeObject[]>([]),
    [canvases, setCanvases] = useState<CreationCanvas[]>([]),
    [nodes, setNodes] = useState<ScopeNode[]>([]);
  const [scope, setScope] = useState<CreativeScope | null>(() =>
      initialScope(owner.canvas, frozen.selected),
    ),
    [effect, setEffect] = useState<ConstraintEffect | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null),
    [drafts, setDrafts] = useState<Draft[]>([]),
    [detail, setDetail] = useState<DefinitionVersion | null>(null),
    [recordDetail, setRecordDetail] = useState<ConstraintRecord | null>(null),
    [basis, setBasis] = useState(''),
    [confirmLevel, setConfirmLevel] = useState<ConstraintRecord['level']>('preference'),
    [confirmValue, setConfirmValue] = useState('');
  const [intent, setIntent] = useState<SettingsIntent | null>(null),
    [storageError, setStorageError] = useState('');
  const [conflictRecords, setConflictRecords] = useState<ConstraintRecord[]>([]);
  const [conflictRequest, setConflictRequest] = useState<Pick<
    ConstraintRecord,
    'key' | 'value' | 'level' | 'scope'
  > | null>(null);
  useEffect(() => {
    let live = true;
    setConflictRecords([]);
    setConflictRequest(null);
    if (intent?.phase !== 'conflict') return;
    const refs = (intent.result?.exception_proposal?.conflicting_refs ??
      intent.result?.exception_proposal?.hard_refs ??
      []) as { constraint_id: string; revision: number }[];
    const requested = async () => {
      const proposal = intent.result?.exception_proposal?.requested as
        Partial<ConstraintRecord> | undefined;
      if (
        proposal?.key &&
        proposal.scope &&
        proposal.level &&
        ['string', 'number', 'boolean'].includes(typeof proposal.value)
      )
        return proposal as ConstraintRecord;
      const payload = intent.request.payload;
      if (intent.request.action === 'constraint.confirm') {
        const original = await getConstraint(
          owner.project,
          String(payload.constraint_id),
          Number(payload.expected_revision),
        );
        return { ...original, ...payload } as ConstraintRecord;
      }
      if (intent.request.action === 'constraint.save')
        return payload as unknown as ConstraintRecord;
      return null;
    };
    void Promise.all([
      Promise.all(refs.map((ref) => getConstraint(owner.project, ref.constraint_id, ref.revision))),
      requested(),
    ])
      .then(([values, request]) => {
        if (live && current()) {
          setConflictRecords(values);
          setConflictRequest(request);
        }
      })
      .catch((e) => {
        if (live && current()) setError(`冲突固定版本读取失败：${errorText(e)}`);
      });
    return () => {
      live = false;
    };
  }, [intent?.request.request_id, intent?.phase]);
  const editorElement = useRef<HTMLElement | null>(null);
  useEffect(() => {
    if (!draft || !current()) return;
    editorElement.current?.scrollIntoView?.({ block: 'nearest', behavior: 'smooth' });
    editorElement.current
      ?.querySelector<HTMLInputElement | HTMLTextAreaElement>(
        'input:not(:disabled),textarea:not(:disabled)',
      )
      ?.focus({ preventScroll: true });
  }, [draft?.id]);
  const prefix = `creative-settings-draft:v1:${settingsOwnerKey(owner)}:`;
  const scopeName = (value: CreativeScope) => scopeTitle(value, canvases, nodes, objects);
  function readDrafts() {
    const values: Draft[] = [];
    for (let i = 0; i < localStorage.length; i++) {
      const key = localStorage.key(i);
      if (!key?.startsWith(prefix)) continue;
      const value = JSON.parse(localStorage.getItem(key)!) as Draft;
      if (!value.id || !['constraint', 'definition'].includes(value.kind) || !value.form)
        throw new Error('草稿记录损坏，请保留后处理');
      values.push(value);
    }
    setDrafts(values);
  }
  useEffect(() => {
    alive.current = true;
    try {
      setIntent(readSettingsIntent(owner));
      readDrafts();
    } catch (e) {
      setStorageError(errorText(e));
    }
    return () => {
      alive.current = false;
    };
  }, []);
  useEffect(() => {
    let live = true;
    setReady(false);
    void (async () => {
      if ((await runtime.flush()) !== true) throw new Error('画布尚未保存，暂不能读取目标配置');
      if (!live || !current()) return;
      const [index, agreements, targets, registry, canvas] = await Promise.all([
        listDefinitions(owner.project),
        listConstraints(owner.project),
        listCreativeObjects(owner.project),
        listCreationCanvases(owner.project),
        getFreezoneCanvas(owner.project, owner.canvas),
      ]);
      const versions = await Promise.all(
        index.map((d) => getDefinition(owner.project, d.name, d.current_revision)),
      );
      const active = await Promise.all(
        index
          .filter((d) => d.active_revision > 0)
          .map((d) => getDefinition(owner.project, d.name, d.active_revision)),
      );
      if (!live || !current()) return;
      setDefinitions(versions);
      setActiveDefinitions(active);
      setRecords(agreements);
      setObjects(targets);
      setCanvases(registry.filter((c) => c.status === 'applied'));
      setNodes(canvas.nodes as ScopeNode[]);
      setReady(true);
    })().catch((e) => {
      if (live && current()) setError(errorText(e));
    });
    return () => {
      live = false;
    };
  }, [reload]);
  useEffect(() => {
    let live = true;
    setEffect(null);
    if (!ready || !validScope(scope)) return;
    void getEffectiveConstraints(owner.project, scope!)
      .then((value) => {
        if (live && current()) setEffect(value);
      })
      .catch((e) => {
        if (live && current()) setError(errorText(e));
      });
    return () => {
      live = false;
    };
  }, [JSON.stringify(scope), ready, reload]);
  function saveDraft(next: Draft) {
    if (!current()) return;
    sequence.current++;
    setDraft(next);
    setDetail(null);
    setRecordDetail(null);
    setBasis('');
    setError('');
    if (next.kind === 'constraint') setScope(next.scope);
    try {
      localStorage.setItem(prefix + encodeURIComponent(next.id), JSON.stringify(next));
      readDrafts();
      setStorageError('');
    } catch (e) {
      setStorageError(`无法保存草稿：${errorText(e)}；写入已暂停。`);
    }
  }
  function openDraft(value: Draft) {
    if (busy || intent || !current()) return;
    setTab(value.kind === 'definition' ? 'definitions' : 'constraints');
    saveDraft(value);
  }
  async function viewDefinition(value: DefinitionVersion, revision = value.revision) {
    if (busy || intent) return;
    const token = ++sequence.current;
    setBusy(true);
    try {
      const fixed = await getDefinition(owner.project, value.name, revision);
      if (current() && token === sequence.current) {
        setDetail(fixed);
        setRecordDetail(null);
        setDraft(null);
        setBasis('');
      }
    } catch (e) {
      if (current()) setError(errorText(e));
    } finally {
      if (current()) setBusy(false);
    }
  }
  async function viewRecord(value: ConstraintRecord, revision = value.revision) {
    if (busy || intent) return;
    const token = ++sequence.current;
    setBusy(true);
    try {
      const fixed = await getConstraint(owner.project, value.constraint_id, revision);
      if (current() && token === sequence.current) {
        setRecordDetail({ ...fixed, scope_status: value.scope_status });
        if (value.scope_status !== 'inactive') setScope(fixed.scope);
        setDetail(null);
        setDraft(null);
        setBasis('');
        setConfirmLevel(value.scope_status === 'inactive' ? 'draft' : fixed.level);
        setConfirmValue(String(fixed.value));
      }
    } catch (e) {
      if (current()) setError(errorText(e));
    } finally {
      if (current()) setBusy(false);
    }
  }
  async function run(
    domain?: SettingsDomain,
    request?: SettingsRequest,
    label?: string,
    checkScope?: CreativeScope,
  ) {
    if (busy || storageError || !current()) return;
    setBusy(true);
    setError('');
    try {
      if (request) {
        if (!ready) throw new Error('请先读取当前项目');
        if ((await runtime.flush()) !== true) throw new Error('画布尚未保存，未提交配置');
        if (!current()) return;
        if (checkScope) {
          const fresh = await getEffectiveConstraints(owner.project, checkScope);
          if (
            !effect ||
            scopeKey(effect.scope) !== scopeKey(checkScope) ||
            fresh.scope_proof !== effect.scope_proof
          )
            throw new Error('目标或分组归属已变化，请重新比较范围');
        }
      }
      if (!current()) return;
      const outcome = await createSettingsOperation(owner, current).run(
        request && domain ? { domain, request, label: label ?? '创作配置' } : undefined,
      );
      if (!current()) return;
      setIntent(readSettingsIntent(owner));
      if (outcome.message) setError(outcome.message);
      if (outcome.status === 'applied') setReload((v) => v + 1);
    } catch (e) {
      if (current()) setError(errorText(e));
    } finally {
      if (current()) setBusy(false);
    }
  }
  function request(action: string, payload: Record<string, unknown>): SettingsRequest {
    return { request_id: crypto.randomUUID(), action, payload };
  }
  function submitDraft() {
    if (!draft) return;
    try {
      if (draft.kind === 'definition') {
        void run(
          'ontology',
          request('definition.draft', {
            expected_revision: draft.baseline?.current_revision ?? 0,
            definition: definitionPayload(draft.form),
          }),
          draft.form.title,
        );
      } else {
        if (!validScope(draft.scope)) throw new Error('请选择明确范围');
        const form = draft.form;
        if (form.ruleDefinition && !form.ruleField) throw new Error('请选择规则属性');
        void run(
          'constraints',
          request('constraint.save', {
            key: form.key,
            value: scalarValue(form.value, form.type),
            level: form.level,
            citation: form.citation,
            scope: draft.scope,
            ...(draft.baseline
              ? {
                  constraint_id: draft.baseline.constraint_id,
                  expected_revision: draft.baseline.revision,
                }
              : {}),
            ...(form.ruleDefinition
              ? {
                  rule: {
                    kind: `custom:${form.ruleDefinition}`,
                    field: form.ruleField,
                    operator: 'equals',
                    schema_revision: form.ruleRevision,
                  },
                }
              : {}),
          }),
          form.key,
          draft.scope!,
        );
      }
    } catch (e) {
      setError(errorText(e));
    }
  }
  async function finish() {
    if (!current() || busy || !intent) return;
    const expectedRequestId = intent.request.request_id;
    setBusy(true);
    try {
      await finishSettingsIntent(owner, expectedRequestId, current);
      if (!current()) return;
      setIntent(null);
      setError('');
      setReload((v) => v + 1);
    } catch (e) {
      if (current()) setError(errorText(e));
    } finally {
      if (current()) setBusy(false);
    }
  }
  const blocked = busy || !!intent || !!storageError || !ready;
  const latestDefinition =
    draft?.kind === 'definition' && draft.baseline
      ? definitions.find((d) => d.name === draft.baseline!.name)
      : undefined;
  const latestConstraint =
    draft?.kind === 'constraint' && draft.baseline
      ? records.find((r) => r.constraint_id === draft.baseline!.constraint_id)
      : undefined;
  const staleDraft =
    draft?.kind === 'definition'
      ? !!latestDefinition && latestDefinition.current_revision !== draft.baseline?.current_revision
      : !!latestConstraint && latestConstraint.revision !== draft?.baseline?.revision;
  return (
    <div className="space-y-4">
      <p className="text-sm text-muted-foreground">按需补充创作对象与约定，不影响继续使用画布。</p>
      <div className="flex w-fit gap-1 rounded-lg bg-muted p-1">
        {(['definitions', 'constraints'] as const).map((value) => (
          <button
            key={value}
            className={`rounded-md px-4 py-2 text-sm ${tab === value ? 'bg-background shadow-sm' : 'text-muted-foreground'}`}
            aria-pressed={tab === value}
            onClick={() => setTab(value)}
          >
            {value === 'definitions' ? '创作对象与关系' : '项目创作约定'}
          </button>
        ))}
      </div>
      {(error || storageError) && (
        <p role="alert" className="text-sm text-destructive">
          {storageError || error}；原选择与草稿保留。
        </p>
      )}
      {intent ? (
        <section className="space-y-3 rounded-lg border p-4">
          <h3 className="font-medium">
            {intent.label} ·{' '}
            {intent.phase === 'pending'
              ? '结果待恢复'
              : intent.phase === 'applied'
                ? '已保存'
                : intent.phase === 'conflict'
                  ? '与已有约定冲突'
                  : '保存未完成'}
          </h3>
          {intent.phase === 'conflict' && (
            <div className="space-y-2 text-sm">
              <p>原约定未被覆盖。请查看冲突版本，修订或降低原约定后重新比较。</p>
              <p>例外提案仅供查看，目前不能批准例外。</p>
              <div className="grid gap-3 sm:grid-cols-2">
                <section aria-label="本次未应用的请求" className="space-y-2 rounded border p-3">
                  <h4 className="font-medium">本次未应用的请求</h4>
                  {conflictRequest ? (
                    <>
                      <p>
                        {conflictRequest.key}：{String(conflictRequest.value)}
                      </p>
                      <p>
                        {levelLabel[conflictRequest.level]} · {scopeName(conflictRequest.scope)}
                      </p>
                    </>
                  ) : (
                    <p className="text-muted-foreground">正在读取原请求证据…</p>
                  )}
                </section>
                <section aria-label="已有约定" className="space-y-2 rounded border p-3">
                  <h4 className="font-medium">已有约定</h4>
                  {conflictRecords.map((record) => (
                    <ConstraintSummary
                      key={`${record.constraint_id}:${record.revision}`}
                      record={record}
                      scope={scopeName(record.scope)}
                    />
                  ))}
                </section>
              </div>
              <details>
                <summary>冲突提案与固定版本</summary>
                <p>提案：{String(intent.result?.exception_proposal?.proposal_id ?? '')}</p>
                {(
                  (intent.result?.exception_proposal?.conflicting_refs ??
                    intent.result?.exception_proposal?.hard_refs ??
                    []) as { constraint_id: string; revision: number }[]
                ).map((ref) => (
                  <p key={ref.constraint_id}>
                    {records.find((r) => r.constraint_id === ref.constraint_id)?.key ??
                      ref.constraint_id}{' '}
                    · 第{ref.revision}版
                  </p>
                ))}
              </details>
            </div>
          )}
          {intent.phase === 'pending' ? (
            <Button disabled={busy} onClick={() => void run()}>
              恢复原请求
            </Button>
          ) : (
            <Button disabled={busy} onClick={() => void finish()}>保留草稿，继续编辑</Button>
          )}
        </section>
      ) : (
        <>
          {!ready && <p className="text-sm">正在读取可信项目配置…</p>}
          <Button
            variant="outline"
            size="sm"
            disabled={busy}
            onClick={() => {
              setError('');
              setReload((v) => v + 1);
            }}
          >
            重新比较当前配置
          </Button>
          {drafts.length > 0 && (
            <details className="rounded-lg border p-3">
              <summary className="cursor-pointer text-sm">保留的草稿（含旧版本）</summary>
              <div className="mt-2 flex flex-wrap gap-2">
                {drafts.map((d) => (
                  <Button
                    key={d.id}
                    variant="outline"
                    size="sm"
                    disabled={blocked}
                    onClick={() => openDraft(d)}
                  >
                    恢复草稿：
                    {d.kind === 'definition'
                      ? d.form.title || '新定义'
                      : d.form.key || '新约定'} ·{' '}
                    {d.baseline ? `第${d.baseline.revision}版` : '未保存'}
                  </Button>
                ))}
              </div>
            </details>
          )}
          {tab === 'definitions' ? (
            <section className="space-y-3">
              <div className="flex gap-2">
                <Button
                  disabled={blocked}
                  onClick={() =>
                    saveDraft({
                      id: `new-definition-${crypto.randomUUID()}`,
                      kind: 'definition',
                      form: blankDefinition('object'),
                    })
                  }
                >
                  新建对象定义
                </Button>
                <Button
                  disabled={blocked}
                  variant="outline"
                  onClick={() =>
                    saveDraft({
                      id: `new-relation-${crypto.randomUUID()}`,
                      kind: 'definition',
                      form: blankDefinition('relation'),
                    })
                  }
                >
                  新建关系类型
                </Button>
              </div>
              <div className="max-h-48 space-y-2 overflow-auto">
                {definitions.map((d) => (
                  <div
                    key={d.name}
                    className="flex items-center justify-between gap-2 rounded border p-3"
                  >
                    <div className="text-sm">
                      {d.definition.title} · {d.category === 'object' ? '创作对象' : '关系类型'}
                      <p className="text-xs text-muted-foreground">
                        {d.active_revision ? `已启用第${d.active_revision}版` : '尚未启用'}
                        {d.current_revision !== d.active_revision
                          ? ` · 第${d.current_revision}版草稿`
                          : ''}
                      </p>
                    </div>
                    <Button
                      variant="outline"
                      size="sm"
                      aria-label={`查看定义 ${d.name}`}
                      disabled={blocked}
                      onClick={() => void viewDefinition(d)}
                    >
                      详情与编辑
                    </Button>
                  </div>
                ))}
                {ready && !definitions.length && (
                  <p className="text-sm text-muted-foreground">
                    尚无扩展定义，基础角色与场景仍在资产中心编辑。
                  </p>
                )}
              </div>
            </section>
          ) : (
            <section className="space-y-3">
              <ScopePicker
                value={scope}
                onChange={(value) => {
                  setScope(value);
                  if (draft?.kind === 'constraint') saveDraft({ ...draft, scope: value });
                }}
                canvasId={owner.canvas}
                canvases={canvases}
                nodes={nodes}
                objects={objects}
                disabled={blocked}
              />
              <Button
                disabled={blocked || !validScope(scope)}
                onClick={() =>
                  saveDraft({
                    id: `new-constraint-${crypto.randomUUID()}`,
                    kind: 'constraint',
                    form: { ...blankConstraint },
                    scope,
                  })
                }
              >
                新增创作约定
              </Button>
              {effect && (
                <details open className="rounded-lg border p-3">
                  <summary className="cursor-pointer text-sm">此范围当前生效的约定</summary>
                  <div className="mt-3 space-y-3">
                    {Object.values(effect.effective).map((r) => (
                      <ConstraintSummary
                        key={r.constraint_id}
                        record={r}
                        scope={scopeName(r.scope)}
                      />
                    ))}
                    {!Object.keys(effect.effective).length && (
                      <p className="text-sm text-muted-foreground">没有已生效约定。</p>
                    )}
                    {effect.conflicts.map((c) => (
                      <p key={c.key} className="text-sm text-destructive">
                        {c.key} 有硬约定冲突，需明确处理。
                      </p>
                    ))}
                    <p className="text-xs text-muted-foreground">
                      文字约定供创作参考，不自动判断所有语义冲突；对象约定只在明确引用该对象时纳入，不按画面位置推测。
                    </p>
                  </div>
                </details>
              )}
              <div className="max-h-56 space-y-3 overflow-auto">
                {records.map((r) => (
                  <article key={r.constraint_id} className="space-y-2 rounded-lg border p-3">
                    <ConstraintSummary record={r} scope={scopeName(r.scope)} />
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={blocked}
                      onClick={() => void viewRecord(r)}
                    >
                      查看与调整 {r.key}
                    </Button>
                  </article>
                ))}
                {ready && !records.length && (
                  <p className="text-sm text-muted-foreground">
                    尚无项目约定，可先保存草稿再明确确认。
                  </p>
                )}
              </div>
            </section>
          )}
          {draft && (tab === 'definitions') === (draft.kind === 'definition') && (
            <section ref={editorElement} className="space-y-4 rounded-lg border p-4">
              <div className="flex items-center justify-between">
                <h3 className="font-medium">
                  {draft.kind === 'definition' ? '定义草稿' : '约定草稿'}
                </h3>
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={blocked}
                  onClick={() => {
                    try {
                      localStorage.removeItem(prefix + encodeURIComponent(draft.id));
                      readDrafts();
                      setDraft(null);
                    } catch (e) {
                      setStorageError(errorText(e));
                    }
                  }}
                >
                  清除这份草稿
                </Button>
              </div>
              {staleDraft && (
                <div className="space-y-2 rounded border p-3">
                  <p className="text-sm">原版本已有更新，当前草稿仍保留原比较版本。</p>
                  {latestDefinition && (
                    <DefinitionSummary value={latestDefinition} definitions={definitions} />
                  )}{' '}
                  {latestConstraint && (
                    <ConstraintSummary
                      record={latestConstraint}
                      scope={scopeName(latestConstraint.scope)}
                    />
                  )}
                  <Button
                    variant="outline"
                    disabled={blocked}
                    onClick={() => {
                      if (draft.kind === 'definition' && latestDefinition)
                        saveDraft({
                          ...draft,
                          id: `definition:${latestDefinition.name}:${latestDefinition.current_revision}`,
                          baseline: latestDefinition,
                        });
                      if (draft.kind === 'constraint' && latestConstraint)
                        saveDraft({
                          ...draft,
                          id: `constraint:${latestConstraint.constraint_id}:${latestConstraint.revision}`,
                          baseline: latestConstraint,
                        });
                    }}
                  >
                    按最新版本重新比较，保留我的内容
                  </Button>
                </div>
              )}
              {draft.kind === 'definition' ? (
                <DefinitionEditor
                  value={draft.form}
                  onChange={(form) => saveDraft({ ...draft, form })}
                  fixedName={!!draft.baseline}
                  definitions={activeDefinitions}
                  disabled={blocked}
                />
              ) : (
                <ConstraintEditor
                  value={draft.form}
                  onChange={(form) => saveDraft({ ...draft, form })}
                  definitions={activeDefinitions}
                  disabled={blocked}
                />
              )}
              <Button
                disabled={
                  blocked ||
                  staleDraft ||
                  (draft.kind === 'definition'
                    ? !draft.form.name.trim() || !draft.form.title.trim()
                    : !draft.form.key.trim() ||
                      !draft.form.citation.trim() ||
                      !validScope(draft.scope) ||
                      !effect)
                }
                onClick={submitDraft}
              >
                {draft.kind === 'definition'
                  ? '保存定义草稿'
                  : draft.form.level === 'draft'
                    ? '保存约定草稿'
                    : '保存为偏好'}
              </Button>
              <p className="text-xs text-muted-foreground">
                关闭会保留草稿。保存定义草稿不会自动启用，硬约定需要明确确认。
              </p>
            </section>
          )}
          {detail && tab === 'definitions' && (
            <section className="space-y-3 rounded-lg border p-4">
              <label className="block text-sm">
                固定历史版本
                <select
                  className={control}
                  value={detail.revision}
                  onChange={(e) => void viewDefinition(detail, Number(e.target.value))}
                >
                  {Array.from({ length: detail.current_revision }, (_, i) => (
                    <option key={i + 1} value={i + 1}>
                      第{i + 1}版
                    </option>
                  ))}
                </select>
              </label>
              <DefinitionSummary value={detail} definitions={definitions} />
              <Button
                disabled={blocked}
                variant="outline"
                onClick={() =>
                  saveDraft({
                    id: `definition:${detail.name}:${detail.current_revision}`,
                    kind: 'definition',
                    form: definitionForm(detail.definition),
                    baseline: detail,
                  })
                }
              >
                以此版本编辑草稿
              </Button>
              <label className="block text-sm">
                启用依据
                <textarea
                  className={control}
                  value={basis}
                  onChange={(e) => setBasis(e.target.value)}
                />
              </label>
              <Button
                disabled={blocked || !basis.trim() || detail.revision === detail.active_revision}
                onClick={() =>
                  void run(
                    'ontology',
                    request('definition.confirm', {
                      name: detail.name,
                      revision: detail.revision,
                      expected_active_revision: detail.active_revision,
                      basis,
                    }),
                    `${detail.definition.title} 第${detail.revision}版`,
                  )
                }
              >
                启用此版本
              </Button>
            </section>
          )}
          {recordDetail && tab === 'constraints' && (
            <section className="space-y-3 rounded-lg border p-4">
              <label className="block text-sm">
                约定固定版本
                <select
                  className={control}
                  value={recordDetail.revision}
                  onChange={(e) =>
                    void viewRecord(
                      records.find((r) => r.constraint_id === recordDetail.constraint_id) ??
                        recordDetail,
                      Number(e.target.value),
                    )
                  }
                >
                  {Array.from(
                    {
                      length:
                        records.find((r) => r.constraint_id === recordDetail.constraint_id)
                          ?.revision ?? recordDetail.revision,
                    },
                    (_, i) => (
                      <option key={i + 1} value={i + 1}>
                        第{i + 1}版
                      </option>
                    ),
                  )}
                </select>
              </label>
              <ConstraintSummary record={recordDetail} scope={scopeName(recordDetail.scope)} />
              {recordDetail.level !== 'hard' && recordDetail.scope_status !== 'inactive' && (
                <Button
                  disabled={blocked}
                  variant="outline"
                  onClick={() =>
                    saveDraft({
                      id: `constraint:${recordDetail.constraint_id}:${recordDetail.revision}`,
                      kind: 'constraint',
                      form: constraintForm(recordDetail),
                      scope: recordDetail.scope,
                      baseline: recordDetail,
                    })
                  }
                >
                  编辑内容草稿
                </Button>
              )}
              <label className="block text-sm">
                确认级别
                <select
                  className={control}
                  value={confirmLevel}
                  onChange={(e) => setConfirmLevel(e.target.value as ConstraintRecord['level'])}
                >
                  {(['draft', 'preference', 'hard'] as const)
                    .filter((v) => recordDetail.scope_status !== 'inactive' || v === 'draft')
                    .map((v) => (
                      <option key={v} value={v}>
                        {levelLabel[v]}
                        {recordDetail.scope_status === 'inactive' ? '（保留原内容归档）' : ''}
                      </option>
                    ))}
                </select>
              </label>
              {recordDetail.scope_status !== 'inactive' && (
                <label className="block text-sm">
                  确认内容
                  <input
                    className={control}
                    value={confirmValue}
                    onChange={(e) => setConfirmValue(e.target.value)}
                  />
                </label>
              )}
              <label className="block text-sm">
                确认依据
                <textarea
                  className={control}
                  value={basis}
                  onChange={(e) => setBasis(e.target.value)}
                />
              </label>
              <Button
                disabled={blocked || !basis.trim()}
                onClick={() => {
                  try {
                    void run(
                      'constraints',
                      request('constraint.confirm', {
                        constraint_id: recordDetail.constraint_id,
                        expected_revision: recordDetail.revision,
                        level: confirmLevel,
                        basis,
                        ...(recordDetail.scope_status !== 'inactive' &&
                        confirmValue !== String(recordDetail.value)
                          ? {
                              value: scalarValue(
                                confirmValue,
                                typeof recordDetail.value === 'boolean'
                                  ? 'boolean'
                                  : typeof recordDetail.value === 'number'
                                    ? 'number'
                                    : 'string',
                              ),
                            }
                          : {}),
                      }),
                      recordDetail.key,
                      recordDetail.scope_status === 'inactive' ? undefined : recordDetail.scope,
                    );
                  } catch (e) {
                    setError(errorText(e));
                  }
                }}
              >
                {recordDetail.scope_status === 'inactive' ? '归档为草稿' : '确认调整此约定'}
              </Button>
            </section>
          )}
        </>
      )}
    </div>
  );
}
