import { useEffect, useRef, useState } from 'react';
import * as api from '@/api/creativeTemplates';
import { control } from '@/features/creative-settings/DefinitionEditor';
import { blankSelection, invalidExampleRelations, TemplateEditor } from './TemplateEditor';
import { ActivationSummary, TemplateSummary } from './TemplateSummary';
import {
  createTemplateOperation,
  finishTemplateIntent,
  readTemplateIntent,
  templateOwnerKey,
  type TemplateOwner,
  type TemplateIntent,
} from './templateOperation';

type Runtime = { flush: () => Promise<boolean>; isCurrent: () => boolean };
type Draft = {
  key: string;
  mode: 'new' | 'patch' | 'replace' | 'override';
  value: api.TemplateSelection;
  sources: api.TemplateSources;
  fixed?: api.PersonalTemplate;
  adoption?: api.TemplateAdoption;
  unset: string[];
};
type Prepared = {
  channel: api.TemplateChannel;
  request: api.TemplateCommand;
  label: string;
  configuration: api.FrozenTemplate;
};
type Installation = {
  fixed: api.PersonalTemplate;
  adoption?: api.TemplateAdoption;
  names: Record<string, string>;
  plan?: api.TemplatePlan;
  upgrade?: api.TemplateUpgrade;
  resolutions: Record<string, 'keep' | 'drop'>;
};
const empty: api.TemplateSources = {
  definitions: [],
  constraints: [],
  agents: [],
  objects: [],
  relations: [],
};
const button = 'rounded-md border border-border px-3 py-1.5 text-sm disabled:opacity-50';
const primary = `${button} bg-primary text-primary-foreground`;
const ref = (v: api.TemplateRef) => ({ id: v.id, revision: v.revision });
const effective = (
  base: api.FrozenTemplate,
  overrides: api.TemplateAdoption['overrides'],
  resolutions: Record<string, string> = {},
) =>
  ({
    ...base,
    ...Object.fromEntries(
      Object.entries(overrides)
        .filter(([k]) => resolutions[k] !== 'drop')
        .map(([k, v]) => [k, v.value]),
    ),
  }) as api.FrozenTemplate;
export function TemplateLibraryPanel({
  owner,
  runtime,
  isCurrent,
}: {
  owner: TemplateOwner;
  runtime: Runtime;
  isCurrent: () => boolean;
}) {
  const [tab, setTab] = useState<'project' | 'personal'>('project'),
    [personal, setPersonal] = useState<api.PersonalTemplate[]>([]),
    [adoptions, setAdoptions] = useState<api.TemplateAdoption[]>([]),
    [sources, setSources] = useState(empty),
    [ready, setReady] = useState(false),
    [busy, setBusy] = useState(false),
    [error, setError] = useState('');
  const [drafts, setDrafts] = useState<Draft[]>([]),
    [edit, setEdit] = useState<Draft | null>(null),
    [prepared, setPrepared] = useState<Prepared | null>(null),
    [installation, setInstallation] = useState<Installation | null>(null),
    [activation, setActivation] = useState<api.TemplateActivation | null>(null),
    [basis, setBasis] = useState(''),
    [detail, setDetail] = useState<api.FrozenTemplate | null>(null),
    [intent, setIntent] = useState<TemplateIntent | null>(null);
  const alive = useRef(true),
    sequence = useRef(0),
    editor = useRef<HTMLDivElement>(null),
    current = useRef(isCurrent);
  current.current = isCurrent;
  const active = () => alive.current && current.current() && runtime.isCurrent();
  const draftKey = `template-library-drafts:v1:${templateOwnerKey(owner)}`;
  const load = async () => {
    const ticket = ++sequence.current;
    const [p, a, s] = await Promise.all([
      api.listPersonalTemplates(owner.project),
      api.listTemplateAdoptions(owner.project),
      api.listTemplateSources(owner.project),
    ]);
    if (active() && ticket === sequence.current) {
      setPersonal(p);
      setAdoptions(a);
      setSources(s);
    }
  };
  useEffect(() => {
    alive.current = true;
    void (async () => {
      try {
        setIntent(readTemplateIntent(owner));
        const raw = localStorage.getItem(draftKey);
        if (raw) setDrafts(JSON.parse(raw));
        if ((await runtime.flush()) !== true)
          throw new Error('画布尚未保存，请先处理保存问题再打开模板库');
        await load();
        if (active()) setReady(true);
      } catch (e) {
        if (active()) setError(String(e instanceof Error ? e.message : e));
      }
    })();
    return () => {
      alive.current = false;
      sequence.current++;
    };
  }, []);
  useEffect(() => {
    if (edit) {
      editor.current?.scrollIntoView?.({ block: 'nearest' });
      editor.current?.querySelector<HTMLInputElement>('input')?.focus();
    }
  }, [edit?.key]);
  const guard = async (work: () => Promise<void>) => {
    if (!active() || busy) return;
    setBusy(true);
    setError('');
    try {
      await work();
    } catch (e) {
      if (active()) setError(e instanceof Error ? e.message : String(e));
    } finally {
      if (active()) setBusy(false);
    }
  };
  const saveDraft = (d: Draft) => {
    try {
      const next = [...drafts.filter((x) => x.key !== d.key), structuredClone(d)];
      localStorage.setItem(draftKey, JSON.stringify(next));
      setDrafts(next);
      setEdit(d);
      setPrepared(null);
    } catch {
      setError('无法保存本地草稿，未开始发布。请检查浏览器存储。');
    }
  };
  const start = (d: Draft) => {
    setInstallation(null);
    setActivation(null);
    setDetail(null);
    saveDraft(d);
  };
  const command = (action: string, payload: Record<string, unknown>): api.TemplateCommand => ({
    request_id: crypto.randomUUID(),
    action,
    payload,
  });
  const run = async (input?: Pick<TemplateIntent, 'channel' | 'request' | 'label'>) => {
    const result = await createTemplateOperation(owner, active).run(input);
    if (!active() || result.status === 'detached') return;
    setIntent(readTemplateIntent(owner));
    if (result.message) setError(result.message);
    if (result.status === 'applied') {
      setPrepared(null);
      setInstallation(null);
      setActivation(null);
      await load();
    }
  };
  const blocked = !ready || busy || !!intent;
  const preview = () =>
    guard(async () => {
      if (!edit) return;
      const d = structuredClone(edit);
      let result: api.TemplatePreview,
        payload: Record<string, unknown>,
        channel: api.TemplateChannel = 'personal',
        action = 'save';
      const changes = Object.fromEntries(
        (['name', 'description'] as const)
          .filter(
            (k) =>
              d.value[k] !== (d.adoption?.effective ?? d.fixed?.configuration)?.[k] ||
              d.unset.includes(k),
          )
          .map((k) => [k, d.unset.includes(k) ? null : d.value[k]]),
      );
      if (d.mode === 'override' && d.adoption) {
        const p = await api.previewTemplateOverride(owner.project, d.adoption.adoption_id, changes);
        if (p.expected_revision !== d.adoption.revision)
          throw new Error('项目修订已变化，原草稿已保留，请与最新版本比较');
        result = {
          configuration: {
            ...d.adoption.effective,
            ...Object.fromEntries(
              Object.entries(changes).map(([k, v]) => [
                k,
                v === null ? d.adoption!.fixed.configuration[k as 'name' | 'description'] : v,
              ]),
            ),
          },
          proof: p.proof,
        };
        channel = 'project';
        action = 'override';
        payload = {
          adoption_id: d.adoption.adoption_id,
          expected_revision: d.adoption.revision,
          changes,
          proof: p.proof,
        };
      } else if (d.mode === 'patch' && d.fixed) {
        result = await api.previewTemplatePatch(
          owner.project,
          ref(d.fixed),
          changes as { name?: string; description?: string },
        );
        action = 'patch';
        payload = { ref: ref(d.fixed), changes, proof: result.proof };
      } else {
        result = await api.previewTemplate(owner.project, d.value);
        payload = {
          configuration: d.value,
          proof: result.proof,
          expected_revision: d.fixed?.revision ?? 0,
          ...(d.fixed ? { id: d.fixed.id } : {}),
        };
      }
      if (active())
        setPrepared({
          channel,
          request: command(action, payload),
          label: d.value.name,
          configuration: result.configuration,
        });
    });
  const compareInstall = () =>
    guard(async () => {
      if (!installation) return;
      const chosen = structuredClone(installation);
      if (chosen.adoption) {
        const p = await api.previewTemplateUpgrade(
          owner.project,
          chosen.adoption.adoption_id,
          ref(chosen.fixed),
          chosen.names,
        );
        if (p.expected_revision !== chosen.adoption.revision)
          throw new Error('项目修订已变化，请保留选择后重新打开比较');
        if (active())
          setInstallation({ ...chosen, upgrade: p, plan: p.installation, resolutions: {} });
      } else {
        const p = await api.previewTemplateAdoption(owner.project, ref(chosen.fixed), chosen.names);
        if (active()) setInstallation({ ...chosen, plan: p });
      }
    });
  const applyInstall = () =>
    guard(async () => {
      if (!installation?.plan) return;
      const i = installation;
      await run({
        channel: 'project',
        label: i.fixed.configuration.name,
        request: command(i.adoption ? 'upgrade' : 'adopt', {
          ref: ref(i.fixed),
          proof: i.upgrade?.proof ?? i.plan!.proof,
          definition_names: i.names,
          ...(i.adoption
            ? {
                adoption_id: i.adoption.adoption_id,
                expected_revision: i.adoption.revision,
                resolutions: i.resolutions,
              }
            : {}),
        }),
      });
    });
  const openInstall = (fixed: api.PersonalTemplate, adoption?: api.TemplateAdoption) => {
    setEdit(null);
    setPrepared(null);
    setActivation(null);
    setInstallation({
      fixed: structuredClone(fixed),
      adoption: adoption ? structuredClone(adoption) : undefined,
      names: {},
      resolutions: {},
    });
  };
  const restore = (d: Draft) => {
    setEdit(structuredClone(d));
    setPrepared(null);
    setInstallation(null);
    setActivation(null);
  };
  const latestFor = (d: Draft) =>
    d.adoption
      ? adoptions.find((a) => a.adoption_id === d.adoption!.adoption_id)
      : personal.find((p) => p.id === d.fixed?.id);
  return (
    <div className="space-y-4 text-sm">
      <p className="text-muted-foreground">
        保存可复用的创作定义、约定和示例。采用保留固定版本，启用定义与确认约定由你明确决定。
      </p>
      <div role="tablist" className="flex gap-2">
        {(['project', 'personal'] as const).map((t) => (
          <button
            key={t}
            type="button"
            role="button"
            aria-pressed={tab === t}
            className={tab === t ? primary : button}
            onClick={() => setTab(t)}
          >
            {t === 'project' ? '项目模板' : '个人模板'}
          </button>
        ))}
      </div>
      {error && (
        <p role="alert" className="whitespace-pre-wrap text-destructive">
          {error}
        </p>
      )}
      {!ready && !error && <p>正在读取固定版本…</p>}
      {intent && (
        <section className="space-y-2 rounded-lg border p-3">
          <p role="status">
            {intent.label} ·{' '}
            {intent.phase === 'applied'
              ? `已保存${intent.channel === 'personal' ? '个人 v' : '项目修订 '}${intent.result?.revision}`
              : intent.phase === 'failed'
                ? '请求未应用'
                : '结果未知，保留原请求'}
          </p>
          {intent.error && <p>{intent.error}</p>}
          {intent.phase === 'pending' ? (
            <button className={primary} disabled={busy} onClick={() => void guard(() => run())}>
              恢复原请求
            </button>
          ) : (
            <button
              className={button}
              disabled={busy}
              onClick={() =>
                void guard(async () => {
                  // Refresh comparison candidates only. The editor/durable draft
                  // retains its original version until an explicit rebase.
                  await load();
                  if (!active()) return;
                  await finishTemplateIntent(owner, intent.request.request_id, active);
                  if (!active()) return;
                  setIntent(null);
                  setPrepared(null);
                })
              }
            >
              保留记录，继续比较
            </button>
          )}
        </section>
      )}
      {drafts.length > 0 && (
        <details className="rounded border p-3">
          <summary>保留的本地草稿（{drafts.length}）</summary>
          {drafts.map((d) => {
            const latest = latestFor(d);
            const old = d.adoption?.revision ?? d.fixed?.revision;
            return (
              <article className="my-2 space-y-2" key={d.key}>
                <p>
                  {d.value.name || '未命名模板'} ·{' '}
                  {old ? `原${d.adoption ? '项目修订' : '个人版本'} ${old}` : '新模板'}
                </p>
                <button className={button} disabled={blocked} onClick={() => restore(d)}>
                  恢复原草稿 {d.value.name || '未命名模板'}
                </button>
                {latest && latest.revision !== old && (
                  <>
                    <p>已有新版本 {latest.revision}；恢复仍保留原版本校验。</p>
                    <details>
                      <summary>与最新内容比较</summary>
                      <TemplateSummary
                        value={'effective' in latest ? latest.effective : latest.configuration}
                      />
                    </details>
                    <button
                      className={button}
                      disabled={blocked}
                      onClick={() => {
                        const base =
                          'effective' in latest ? latest.effective : latest.configuration;
                        const original = d.adoption?.effective ?? d.fixed!.configuration;
                        const value = {
                          ...d.value,
                          name: d.value.name === original.name ? base.name : d.value.name,
                          description:
                            d.value.description === original.description
                              ? base.description
                              : d.value.description,
                        };
                        start({
                          ...d,
                          key: `${d.mode}:${'adoption_id' in latest ? latest.adoption_id : latest.id}:${latest.revision}`,
                          value,
                          ...('adoption_id' in latest ? { adoption: latest } : { fixed: latest }),
                        });
                      }}
                    >
                      以最新版本重新比较此草稿
                    </button>
                  </>
                )}
              </article>
            );
          })}
        </details>
      )}
      {edit && (
        <div ref={editor} className="space-y-3 rounded-lg border p-4">
          <h3 className="font-medium">
            {edit.mode === 'override'
              ? '项目名称与说明调整'
              : edit.mode === 'replace'
                ? '重新选择模板内容'
                : '编辑个人模板'}
          </h3>
          {edit.mode === 'replace' && <p>此版本内容按下方明确选择重新建立；旧版本保持不变。</p>}
          <TemplateEditor
            value={edit.value}
            sources={edit.sources}
            content={edit.mode === 'new' || edit.mode === 'replace'}
            disabled={blocked}
            onChange={(value) => saveDraft({ ...edit, value })}
          />
          {edit.mode === 'override' &&
            (['name', 'description'] as const).map((k) => (
              <label key={k} className="mr-4 inline-flex gap-2">
                <input
                  type="checkbox"
                  disabled={blocked}
                  checked={edit.unset.includes(k)}
                  onChange={(e) =>
                    saveDraft({
                      ...edit,
                      unset: e.target.checked
                        ? [...edit.unset, k]
                        : edit.unset.filter((v) => v !== k),
                    })
                  }
                />
                取消{k === 'name' ? '名称' : '说明'}覆盖，使用采用版本
              </label>
            ))}
          <button
            className={primary}
            disabled={
              blocked ||
              !edit.value.name.trim() ||
              invalidExampleRelations(edit.value, edit.sources)
            }
            onClick={() => void preview()}
          >
            {edit.mode === 'override' ? '预览项目调整' : '预览发布内容'}
          </button>
        </div>
      )}
      {prepared && (
        <section className="space-y-3 rounded-lg border p-4">
          <h3 className="font-medium">
            {prepared.channel === 'personal' ? '本次实际发布内容' : '本次项目有效内容'}
          </h3>
          <TemplateSummary value={prepared.configuration} />
          <button
            className={primary}
            disabled={blocked}
            onClick={() => void guard(() => run(prepared))}
          >
            {prepared.channel === 'personal' ? '保存个人模板版本' : '保存项目调整'}
          </button>
        </section>
      )}
      {installation && (
        <section className="space-y-3 rounded-lg border p-4">
          <h3 className="font-medium">
            {installation.adoption ? '比较模板新版' : '采用固定模板'} · 个人 v
            {installation.fixed.revision}
          </h3>
          <TemplateSummary value={installation.fixed.configuration} />
          <div className="space-y-2">
            <h4>目标项目的定义名称</h4>
            <p className="text-muted-foreground">
              相同内容可复用。遇同名但内容不同时，明确选择独立定义后重新比较。
            </p>
            {installation.fixed.configuration.definitions.map((d) => (
              <label className="block" key={d.key}>
                {d.definition.title}
                <select
                  className={control}
                  disabled={blocked}
                  value={installation.names[d.key] ? 'independent' : 'original'}
                  onChange={(e) => {
                    const names = { ...installation.names };
                    if (e.target.value === 'original') delete names[d.key];
                    else
                      names[d.key] =
                        `template_${crypto.randomUUID().replace(/-/g, '').slice(0, 12)}`;
                    setInstallation({
                      ...installation,
                      names,
                      plan: undefined,
                      upgrade: undefined,
                      resolutions: {},
                    });
                  }}
                >
                  <option value="original">保留定义名称，核对是否可复用</option>
                  <option value="independent">在本项目使用独立定义</option>
                </select>
                {installation.names[d.key] && (
                  <details>
                    <summary>查看稳定标识</summary>
                    {installation.names[d.key]}
                  </details>
                )}
              </label>
            ))}
          </div>
          <button className={button} disabled={blocked} onClick={() => void compareInstall()}>
            比较目标与依赖
          </button>
          {installation.plan && (
            <>
              <p>
                {installation.plan.definitions.filter((d) => d.reuse).length}{' '}
                项定义复用；其余以草稿加入。默认约定保持草稿。
              </p>
              {installation.plan.conflicts.map((c) => (
                <p role="alert" key={c.source_key}>
                  定义「
                  {installation.fixed.configuration.definitions.find((d) => d.key === c.source_key)
                    ?.definition.title ?? c.target_name}
                  」与项目内容不同，请选择独立定义并重新比较。
                </p>
              ))}
              {installation.upgrade && (
                <>
                  <h4>当前项目有效内容（含覆盖）</h4>
                  <TemplateSummary
                    value={effective(installation.upgrade.before, installation.upgrade.overrides)}
                  />
                  {installation.upgrade.conflicts.map((k) => (
                    <label key={k} className="block">
                      {k === 'name' ? '名称' : '说明'}的覆盖冲突
                      <p className="whitespace-pre-wrap">
                        当前项目覆盖：{String(installation.upgrade!.overrides[k]?.value ?? '')}
                      </p>
                      <select
                        aria-label={`${k === 'name' ? '名称' : '说明'}冲突处理`}
                        className={control}
                        value={installation.resolutions[k] ?? ''}
                        disabled={blocked}
                        onChange={(e) =>
                          setInstallation({
                            ...installation,
                            resolutions: {
                              ...installation.resolutions,
                              [k]: e.target.value as 'keep' | 'drop',
                            },
                          })
                        }
                      >
                        <option value="">明确选择处理方式</option>
                        <option value="keep">保留项目覆盖</option>
                        <option value="drop">采用新版内容，取消此项覆盖</option>
                      </select>
                    </label>
                  ))}
                  <h4>选择后最终生效内容</h4>
                  <TemplateSummary
                    value={effective(
                      installation.upgrade.after,
                      installation.upgrade.overrides,
                      installation.resolutions,
                    )}
                  />
                  <p>既有示例与约定保留，不会被新版重写。新版定义与默认约定仍待明确启用或确认。</p>
                </>
              )}
              <button
                className={primary}
                disabled={
                  blocked ||
                  installation.plan.conflicts.length > 0 ||
                  !!installation.upgrade?.conflicts.some((k) => !installation.resolutions[k])
                }
                onClick={() => void applyInstall()}
              >
                {installation.adoption ? '按所选处理升级' : '采用到本项目（保持草稿）'}
              </button>
            </>
          )}
        </section>
      )}
      {activation && (
        <section className="space-y-3 rounded-lg border p-4">
          <h3 className="font-medium">本次启用与示例创建内容</h3>
          <ActivationSummary value={activation} />
          <label className="block">
            启用依据
            <textarea
              className={control}
              value={basis}
              disabled={blocked}
              onChange={(e) => setBasis(e.target.value)}
            />
          </label>
          <button
            className={primary}
            disabled={blocked || !basis.trim()}
            onClick={() =>
              void guard(() =>
                run({
                  channel: 'project',
                  label: '启用模板定义与示例',
                  request: command('activate_examples', {
                    adoption_id: activation.adoption_id,
                    expected_revision: activation.expected_revision,
                    definitions: activation.definitions.map((d) => ({
                      name: d.name,
                      revision: d.revision,
                    })),
                    basis,
                    proof: activation.proof,
                  }),
                }),
              )
            }
          >
            启用所列定义并创建示例
          </button>
        </section>
      )}
      {detail && (
        <section className="rounded-lg border p-4">
          <h3 className="mb-3 font-medium">固定版本详情</h3>
          <TemplateSummary value={detail} />
        </section>
      )}
      {tab === 'personal' ? (
        <section className="space-y-3">
          <button
            className={primary}
            disabled={blocked}
            onClick={() =>
              start({
                key: `new:${crypto.randomUUID()}`,
                mode: 'new',
                value: blankSelection(),
                sources: structuredClone(sources),
                unset: [],
              })
            }
          >
            新建模板
          </button>
          {personal.map((p) => (
            <article className="space-y-2 rounded-lg border p-3" key={p.id}>
              <h3>
                {p.configuration.name} · 个人 v{p.revision}
              </h3>
              <p>{p.configuration.description}</p>
              <div className="flex flex-wrap gap-2">
                <button
                  className={button}
                  disabled={busy}
                  onClick={() => setDetail(p.configuration)}
                >
                  查看固定内容 {p.configuration.name}
                </button>
                <button
                  className={button}
                  disabled={blocked}
                  onClick={() =>
                    start({
                      key: `patch:${p.id}:${p.revision}`,
                      mode: 'patch',
                      value: blankSelection(p.configuration.name, p.configuration.description),
                      fixed: structuredClone(p),
                      sources: empty,
                      unset: [],
                    })
                  }
                >
                  编辑名称与说明 {p.configuration.name}
                </button>
                <button
                  className={button}
                  disabled={blocked}
                  onClick={() =>
                    start({
                      key: `replace:${p.id}:${p.revision}`,
                      mode: 'replace',
                      value: blankSelection(p.configuration.name, p.configuration.description),
                      fixed: structuredClone(p),
                      sources: structuredClone(sources),
                      unset: [],
                    })
                  }
                >
                  重新选择内容发布新版
                </button>
                <button
                  className={button}
                  disabled={blocked}
                  onClick={() => {
                    setEdit(null);
                    setInstallation(null);
                    setPrepared({
                      channel: 'personal',
                      label: `${p.configuration.name} 副本`,
                      configuration: { ...p.configuration, name: `${p.configuration.name} 副本` },
                      request: command('copy', {
                        ref: ref(p),
                        name: `${p.configuration.name} 副本`,
                      }),
                    });
                  }}
                >
                  复制固定版本
                </button>
                <button className={primary} disabled={blocked} onClick={() => openInstall(p)}>
                  采用 {p.configuration.name}
                </button>
              </div>
              <label>
                查看个人历史版本
                <select
                  aria-label={`个人历史 ${p.configuration.name}`}
                  className={control}
                  value=""
                  disabled={busy}
                  onChange={(e) => {
                    if (e.target.value)
                      void guard(async () => {
                        const old = await api.getPersonalTemplate(owner.project, {
                          id: p.id,
                          revision: Number(e.target.value),
                        });
                        if (active()) setDetail(old.configuration);
                      });
                  }}
                >
                  <option value="">选择固定版本</option>
                  {Array.from({ length: p.revision }, (_, i) => (
                    <option key={i} value={i + 1}>
                      个人 v{i + 1}
                    </option>
                  ))}
                </select>
              </label>
            </article>
          ))}
          {ready && !personal.length && <p>个人库还没有模板。</p>}
        </section>
      ) : (
        <section className="space-y-3">
          {adoptions.map((a) => {
            const latest = personal.find((p) => p.id === a.fixed.id);
            return (
              <article key={a.adoption_id} className="space-y-2 rounded-lg border p-3">
                <h3>
                  {a.effective.name} · 项目修订 {a.revision}
                </h3>
                <p>
                  固定采用个人 v{a.fixed.revision} ·{' '}
                  {Object.keys(a.overrides).length ? '有项目名称或说明覆盖' : '无项目覆盖'}
                </p>
                <p>
                  {a.instantiated ? '所选定义已启用，示例已创建' : '定义与示例尚待明确启用'}
                  ；默认约定需在创作约定中确认。
                </p>
                <div className="flex flex-wrap gap-2">
                  <button className={button} onClick={() => setDetail(a.effective)}>
                    查看项目内容 {a.effective.name}
                  </button>
                  <button
                    className={button}
                    disabled={blocked}
                    onClick={() =>
                      start({
                        key: `override:${a.adoption_id}:${a.revision}`,
                        mode: 'override',
                        value: blankSelection(a.effective.name, a.effective.description),
                        adoption: structuredClone(a),
                        sources: empty,
                        unset: [],
                      })
                    }
                  >
                    调整本项目名称与说明
                  </button>
                  {!a.instantiated && (
                    <button
                      className={primary}
                      disabled={blocked}
                      onClick={() =>
                        void guard(async () => {
                          const value = await api.previewTemplateActivation(
                            owner.project,
                            a.adoption_id,
                          );
                          if (active()) {
                            setActivation(value);
                            setBasis('');
                            setEdit(null);
                            setPrepared(null);
                            setInstallation(null);
                          }
                        })
                      }
                    >
                      预览启用与示例
                    </button>
                  )}
                  {latest && latest.revision > a.fixed.revision && (
                    <button
                      className={primary}
                      disabled={blocked}
                      onClick={() => openInstall(latest, a)}
                    >
                      比较新版 v{latest.revision}
                    </button>
                  )}
                </div>
                <details>
                  <summary>采用来源</summary>
                  <p>
                    个人库用户：{a.source.user_id} · 固定个人 v{a.source.ref.revision}
                  </p>
                </details>
                <select
                  aria-label={`项目历史 ${a.effective.name}`}
                  className={control}
                  value=""
                  disabled={busy}
                  onChange={(e) => {
                    if (e.target.value)
                      void guard(async () => {
                        const old = await api.getTemplateAdoption(
                          owner.project,
                          a.adoption_id,
                          Number(e.target.value),
                        );
                        if (active()) setDetail(old.effective);
                      });
                  }}
                >
                  <option value="">查看项目固定历史</option>
                  {Array.from({ length: a.revision }, (_, i) => (
                    <option key={i} value={i + 1}>
                      项目修订 {i + 1}
                    </option>
                  ))}
                </select>
              </article>
            );
          })}
          {ready && !adoptions.length && <p>项目尚未采用模板。可从个人模板中明确选择。</p>}
        </section>
      )}
    </div>
  );
}
