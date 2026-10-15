import { useEffect, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Button } from '@/components/ui/button';
import {
  getAgentCapabilities,
  listAgentAdoptions,
  listPersonalAgents,
  previewAgent,
  previewAgentOverride,
  previewAgentPatch,
  previewAgentUpgrade,
  type AgentAdoption,
  type AgentChanges,
  type AgentChannel,
  type AgentCommand,
  type AgentConfiguration,
  type AgentRef,
  type PersonalAgent,
  type UpgradePreview,
} from '@/api/reusableAgents';
import { AgentEditor, AgentSummary, agentFields, emptyAgent } from './AgentEditor';
import {
  agentScopeKey,
  createAgentOperation,
  finishAgentIntent,
  readAgentIntent,
  type AgentIntent,
  type AgentOwner,
} from './agentOperation';
interface Edit {
  identity: string;
  mode: 'new' | 'personal' | 'override';
  base: AgentConfiguration;
  changes: AgentChanges;
  ref?: AgentRef;
  adoption?: AgentAdoption;
}
interface Prepared {
  channel: AgentChannel;
  request: AgentCommand;
  label: string;
  configuration: AgentConfiguration;
  button: string;
}
const draftKey = (owner: AgentOwner, id: string) =>
  `agent-config-draft:v1:${agentScopeKey(owner)}:${encodeURIComponent(id)}`;
const message = (error: unknown) => (error instanceof Error ? error.message : String(error));
function normalized(changes: AgentChanges) {
  return {
    ...changes,
    ...(changes.references
      ? { references: changes.references.map((v) => v.trim()).filter(Boolean) }
      : {}),
    ...(changes.skill_notes
      ? { skill_notes: changes.skill_notes.map((v) => v.trim()).filter(Boolean) }
      : {}),
  };
}
export function AgentConfigurationPanel({
  owner,
  isCurrent,
}: {
  owner: AgentOwner;
  isCurrent: () => boolean;
}) {
  const client = useQueryClient();
  const [tab, setTab] = useState<'project' | 'personal'>('project');
  const personal = useQuery({
    queryKey: ['personal-agent-library', owner.user],
    queryFn: () => listPersonalAgents(owner.project),
  });
  const projects = useQuery({
    queryKey: ['project-agent-adoptions', owner.user, owner.project],
    queryFn: () => listAgentAdoptions(owner.project),
  });
  const capabilities = useQuery({
    queryKey: ['agent-capabilities', owner.user, owner.project],
    queryFn: () => getAgentCapabilities(owner.project),
  });
  const [edit, setEdit] = useState<Edit | null>(null);
  const editSequence = useRef(0);
  const [prepared, setPrepared] = useState<Prepared | null>(null);
  const [upgrade, setUpgrade] = useState<UpgradePreview | null>(null);
  const [resolutions, setResolutions] = useState<Record<string, 'keep' | 'drop'>>({});
  const [intent, setIntent] = useState<AgentIntent | null>(null);
  const [error, setError] = useState('');
  const [storageError, setStorageError] = useState('');
  const [busy, setBusy] = useState(false);
  const [drafts, setDrafts] = useState<Edit[]>([]);
  function reloadDrafts() {
    const prefix = `agent-config-draft:v1:${agentScopeKey(owner)}:`;
    const saved: Edit[] = [];
    for (let i = 0; i < localStorage.length; i++) {
      const key = localStorage.key(i);
      if (!key?.startsWith(prefix)) continue;
      const draft = JSON.parse(localStorage.getItem(key)!) as Edit;
      if (
        key !== draftKey(owner, draft.identity) ||
        !['new', 'personal', 'override'].includes(draft.mode) ||
        !draft.base ||
        !draft.changes
      )
        throw new Error('配置草稿损坏，请保留记录后处理');
      saved.push(draft);
    }
    setDrafts(saved);
  }
  const live = useRef(true);
  useEffect(() => {
    live.current = true;
    try {
      setIntent(readAgentIntent(owner));
      reloadDrafts();
    } catch (e) {
      setStorageError(message(e));
    }
    return () => {
      live.current = false;
    };
  }, [owner.user, owner.project, owner.canvas]);
  const current = () => live.current && isCurrent();
  const refresh = () => {
    void client.invalidateQueries({ queryKey: ['personal-agent-library', owner.user] });
    void client.invalidateQueries({
      queryKey: ['project-agent-adoptions', owner.user, owner.project],
    });
  };
  function change(next: Edit) {
    editSequence.current++;
    setPrepared(null);
    setUpgrade(null);
    setEdit(next);
    try {
      localStorage.setItem(draftKey(owner, next.identity), JSON.stringify(next));
      reloadDrafts();
      setStorageError('');
    } catch (e) {
      setStorageError(`无法保存恢复草稿：${message(e)}。请先释放浏览器存储再发布。`);
    }
  }
  function open(next: Edit) {
    if (!current() || busy || intent) return;
    editSequence.current++;
    setPrepared(null);
    setUpgrade(null);
    setError('');
    try {
      const raw = localStorage.getItem(draftKey(owner, next.identity));
      if (raw) {
        const saved = JSON.parse(raw) as Edit;
        if (saved.identity !== next.identity || saved.mode !== next.mode)
          throw new Error('草稿身份不符');
        next = saved;
      }
      setEdit(next);
    } catch (e) {
      setStorageError(message(e));
    }
  }
  async function prepare() {
    if (!edit || busy || storageError || !current()) return;
    const selected = edit;
    const sequence = editSequence.current;
    setBusy(true);
    setError('');
    try {
      const changes = normalized(selected.changes);
      let configuration: AgentConfiguration;
      let payload: Record<string, unknown>;
      let action: string;
      let channel: AgentChannel;
      if (selected.mode === 'new') {
        const input = { ...emptyAgent, ...changes } as AgentConfiguration;
        const preview = await previewAgent(owner.project, input);
        configuration = preview.configuration;
        payload = { configuration: input, proof: preview.proof, expected_revision: 0 };
        action = 'save';
        channel = 'personal';
      } else if (selected.mode === 'personal') {
        const preview = await previewAgentPatch(owner.project, selected.ref!, changes);
        configuration = preview.configuration;
        payload = { ref: selected.ref, changes, proof: preview.proof };
        action = 'patch';
        channel = 'personal';
      } else {
        const preview = await previewAgentOverride(
          owner.project,
          selected.adoption!.adoption_id,
          changes,
        );
        configuration = {
          ...preview.before,
          ...Object.fromEntries(
            Object.entries(preview.changes).map(([k, v]) => [
              k,
              v === null
                ? selected.adoption!.fixed.configuration[k as keyof AgentConfiguration]
                : v,
            ]),
          ),
        } as AgentConfiguration;
        payload = {
          adoption_id: selected.adoption!.adoption_id,
          expected_revision: selected.adoption!.revision,
          changes,
          proof: preview.proof,
        };
        action = 'override';
        channel = 'project';
      }
      if (current() && sequence === editSequence.current)
        setPrepared({
          channel,
          request: { request_id: crypto.randomUUID(), action, payload },
          configuration,
          label: configuration.name,
          button:
            action === 'override'
              ? '保存项目覆盖'
              : action === 'save'
                ? '保存到个人库'
                : '保存个人新版',
        });
    } catch (e) {
      if (current() && sequence === editSequence.current) setError(message(e));
    } finally {
      if (current()) setBusy(false);
    }
  }
  async function run(value?: Prepared) {
    if (busy || storageError || !current()) return;
    setBusy(true);
    setError('');
    try {
      const outcome = await createAgentOperation(owner, current).run(value);
      if (!current()) return;
      setIntent(readAgentIntent(owner));
      if (outcome.status === 'applied') {
        setPrepared(null);
        setUpgrade(null);
        refresh();
      } else setError(outcome.message ?? '请求尚未完成');
    } catch (e) {
      if (current()) setError(message(e));
    } finally {
      if (current()) setBusy(false);
    }
  }
  async function finish() {
    if (!current() || busy || !intent) return;
    const expectedRequestId = intent.request.request_id;
    setBusy(true);
    try {
      await finishAgentIntent(owner, expectedRequestId, current);
      if (!current()) return;
      setIntent(null);
      setPrepared(null);
      setUpgrade(null);
      setEdit(null);
      setError('');
      refresh();
    } catch (e) {
      if (current()) setError(message(e));
    } finally {
      if (current()) setBusy(false);
    }
  }
  function adopt(fixed: PersonalAgent) {
    if (!current() || intent || busy) return;
    setEdit(null);
    setUpgrade(null);
    setPrepared({
      channel: 'project',
      request: {
        request_id: crypto.randomUUID(),
        action: 'adopt',
        payload: { ref: { id: fixed.id, revision: fixed.revision }, proof: fixed.proof },
      },
      label: fixed.configuration.name,
      configuration: fixed.configuration,
      button: '采用到当前项目',
    });
  }
  function copy(fixed: PersonalAgent) {
    if (!current() || intent || busy) return;
    setEdit(null);
    setUpgrade(null);
    const name = `${fixed.configuration.name} 副本`;
    setPrepared({
      channel: 'personal',
      request: {
        request_id: crypto.randomUUID(),
        action: 'copy',
        payload: { ref: { id: fixed.id, revision: fixed.revision }, name },
      },
      label: name,
      configuration: { ...fixed.configuration, name },
      button: '保存个人副本',
    });
  }
  async function compare(adoption: AgentAdoption, fixed: PersonalAgent) {
    if (!current() || busy || intent) return;
    setBusy(true);
    setError('');
    const sequence = ++editSequence.current;
    setEdit(null);
    setPrepared(null);
    try {
      const preview = await previewAgentUpgrade(owner.project, adoption.adoption_id, {
        id: fixed.id,
        revision: fixed.revision,
      });
      if (current() && sequence === editSequence.current) {
        setUpgrade(preview);
        setResolutions({});
      }
    } catch (e) {
      if (current()) setError(message(e));
    } finally {
      if (current()) setBusy(false);
    }
  }
  const value = edit
    ? {
        ...edit.base,
        ...Object.fromEntries(
          Object.entries(edit.changes).map(([k, v]) => [
            k,
            v === null
              ? (edit.adoption?.fixed.configuration ?? edit.base)[k as keyof AgentConfiguration]
              : v,
          ]),
        ),
      }
    : null;
  const valid =
    !!value &&
    ['name', 'responsibilities', 'methods'].every(
      (k) =>
        typeof value[k as keyof AgentConfiguration] === 'string' &&
        String(value[k as keyof AgentConfiguration]).trim(),
    ) &&
    (edit?.mode === 'new' || Object.keys(edit?.changes ?? {}).length > 0);
  const currentUpgradeConfiguration = upgrade
    ? ({
        ...upgrade.before,
        ...Object.fromEntries(
          Object.entries(upgrade.overrides).map(([field, override]) => [field, override.value]),
        ),
      } as AgentConfiguration)
    : null;
  const resolvedUpgradeConfiguration =
    upgrade && !upgrade.conflicts.some((field) => !resolutions[field])
      ? ({
          ...upgrade.after,
          ...Object.fromEntries(
            Object.entries(upgrade.overrides)
              .filter(
                ([field]) => !upgrade.conflicts.includes(field) || resolutions[field] === 'keep',
              )
              .map(([field, override]) => [field, override.value]),
          ),
        } as AgentConfiguration)
      : null;
  const latestPersonal =
    edit?.mode === 'personal' ? personal.data?.find((p) => p.id === edit.ref?.id) : undefined;
  const latestProject =
    edit?.mode === 'override'
      ? projects.data?.find((p) => p.adoption_id === edit.adoption?.adoption_id)
      : undefined;
  const newerEdit: Edit | null =
    edit && latestPersonal && latestPersonal.revision !== edit.ref?.revision
      ? {
          ...edit,
          identity: `personal:${latestPersonal.id}:${latestPersonal.revision}`,
          base: latestPersonal.configuration,
          ref: { id: latestPersonal.id, revision: latestPersonal.revision },
        }
      : edit && latestProject && latestProject.revision !== edit.adoption?.revision
        ? {
            ...edit,
            identity: `project:${latestProject.adoption_id}:${latestProject.revision}`,
            base: latestProject.effective,
            adoption: latestProject,
          }
        : null;
  return (
    <div className="space-y-4">
      <p className="text-sm text-muted-foreground">保存到个人库，或仅调整此项目的配置。</p>
      <div className="flex gap-1 rounded-lg bg-muted p-1 w-fit">
        {(['project', 'personal'] as const).map((v) => (
          <button
            type="button"
            key={v}
            aria-pressed={tab === v}
            className={`rounded-md px-4 py-2 text-sm ${tab === v ? 'bg-background shadow-sm' : 'text-muted-foreground'}`}
            onClick={() => setTab(v)}
          >
            {v === 'project' ? '项目采用' : '个人库'}
          </button>
        ))}
      </div>
      {storageError && (
        <p role="alert" className="text-sm text-destructive">
          {storageError}
        </p>
      )}
      {error && (
        <p role="alert" className="text-sm text-destructive">
          {error}；选择和草稿已保留。
        </p>
      )}
      {intent ? (
        <section className="space-y-3 rounded-lg border p-4">
          <h3 className="font-medium">
            {intent.label} ·{' '}
            {intent.phase === 'applied'
              ? intent.channel === 'personal'
                ? `已保存个人 v${intent.result?.revision}`
                : `已保存项目修订 ${intent.result?.revision}`
              : intent.phase === 'pending'
                ? '保存结果待恢复'
                : '保存未完成'}
          </h3>
          <p className="text-sm text-muted-foreground">
            {intent.channel === 'personal' ? '个人库版本' : '项目采用配置'}；
            {intent.phase === 'pending'
              ? '请恢复同一请求，勿重新创建。'
              : (intent.error ?? '此次结果只属于打开时的项目与配置。')}
          </p>
          {intent.phase === 'pending' ? (
            <Button disabled={busy} onClick={() => void run()}>
              恢复原请求
            </Button>
          ) : (
            <Button disabled={busy} onClick={() => void finish()}>完成，继续配置</Button>
          )}
        </section>
      ) : (
        <>
          {(personal.error || projects.error) && (
            <p role="alert">
              配置读取失败。
              <button className="underline" onClick={refresh}>
                重新读取
              </button>
            </p>
          )}
          {drafts.length > 0 && (
            <details className="rounded-lg border p-3">
              <summary className="cursor-pointer text-sm">已保留的配置草稿（含旧版本）</summary>
              <div className="mt-2 flex flex-col items-start gap-2">
                {drafts.map((draft) => (
                  <Button
                    key={draft.identity}
                    variant="outline"
                    size="sm"
                    disabled={busy}
                    onClick={() => open(draft)}
                  >
                    恢复草稿：{draft.base.name || '新建 Agent'} ·{' '}
                    {draft.mode === 'override'
                      ? `项目修订 ${draft.adoption?.revision}`
                      : draft.mode === 'personal'
                        ? `个人 v${draft.ref?.revision}`
                        : '未发布'}
                  </Button>
                ))}
              </div>
            </details>
          )}
          {edit && newerEdit && (
            <section aria-label="草稿原基线与最新配置" className="space-y-3 rounded-lg border p-4">
              <h3 className="font-medium">草稿原基线与最新配置</h3>
              <p className="text-sm text-muted-foreground">
                草稿仍使用原版本。比较后可明确保留修改并以最新版本重新预览；旧草稿继续保留，不会自动保存。
              </p>
              <div className="grid gap-4 sm:grid-cols-2">
                <details>
                  <summary>草稿原基线</summary>
                  <AgentSummary configuration={edit.base} />
                </details>
                <details open>
                  <summary>最新配置</summary>
                  <AgentSummary configuration={newerEdit.base} />
                </details>
              </div>
              <Button disabled={busy} variant="outline" onClick={() => change(newerEdit)}>
                保留我的修改，以最新版本重新预览
              </Button>
            </section>
          )}
          <div className="max-h-52 space-y-2 overflow-auto rounded-lg border p-3">
            {tab === 'personal' ? (
              <>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={busy}
                  onClick={() =>
                    open({ identity: 'new', mode: 'new', base: emptyAgent, changes: {} })
                  }
                >
                  新建 Agent
                </Button>
                {personal.isLoading && <p>正在读取个人库…</p>}
                {personal.data?.length === 0 && (
                  <p className="text-sm text-muted-foreground">
                    个人库暂无配置，新建后可复用到不同项目。
                  </p>
                )}
                {personal.data?.map((fixed) => (
                  <div
                    key={fixed.id}
                    className="flex flex-wrap items-center justify-between gap-2 border-b py-2 last:border-0"
                  >
                    <span className="text-sm">
                      {fixed.configuration.name} · v{fixed.revision}
                    </span>
                    <div className="flex gap-2">
                      <Button
                        variant="outline"
                        size="sm"
                        disabled={busy}
                        aria-label={`编辑 ${fixed.configuration.name} v${fixed.revision}`}
                        onClick={() =>
                          open({
                            identity: `personal:${fixed.id}:${fixed.revision}`,
                            mode: 'personal',
                            base: fixed.configuration,
                            changes: {},
                            ref: { id: fixed.id, revision: fixed.revision },
                          })
                        }
                      >
                        编辑
                      </Button>
                      <Button
                        variant="outline"
                        size="sm"
                        disabled={busy}
                        aria-label={`复制 ${fixed.configuration.name}`}
                        onClick={() => copy(fixed)}
                      >
                        复制
                      </Button>
                      <Button size="sm" disabled={busy} onClick={() => adopt(fixed)}>
                        采用到项目
                      </Button>
                    </div>
                  </div>
                ))}
              </>
            ) : (
              <>
                {projects.isLoading && <p>正在读取项目配置…</p>}
                {projects.data?.length === 0 && (
                  <p className="text-sm text-muted-foreground">
                    尚未采用 Agent 配置。可从个人库选择或新建，采用不会启动运行。
                  </p>
                )}
                {projects.data?.map((a) => {
                  const latest = personal.data?.find((p) => p.id === a.fixed.id);
                  return (
                    <article key={a.adoption_id} className="space-y-2 border-b py-2 last:border-0">
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <span className="text-sm font-medium">
                          {a.effective.name} · 个人 v{a.fixed.revision} · 项目修订 {a.revision}
                        </span>
                        <Button
                          variant="outline"
                          size="sm"
                          disabled={busy}
                          onClick={() =>
                            open({
                              identity: `project:${a.adoption_id}:${a.revision}`,
                              mode: 'override',
                              base: a.effective,
                              adoption: a,
                              changes: {},
                            })
                          }
                        >
                          查看与局部调整
                        </Button>
                      </div>
                      <p className="text-xs text-muted-foreground">
                        {Object.keys(a.overrides).length
                          ? `本项目覆盖：${Object.keys(a.overrides)
                              .map((k) => agentFields[k as keyof AgentConfiguration] ?? k)
                              .join('、')}`
                          : '使用固定个人版本，无局部覆盖'}
                      </p>
                      {latest && latest.revision !== a.fixed.revision && (
                        <Button
                          size="sm"
                          variant="outline"
                          disabled={busy}
                          onClick={() => void compare(a, latest)}
                        >
                          比较个人新版 v{latest.revision}
                        </Button>
                      )}
                    </article>
                  );
                })}
              </>
            )}
          </div>
          {edit && (
            <section className="space-y-4 rounded-lg border p-4">
              <div className="flex items-center justify-between">
                <h3 className="font-medium">
                  {edit.mode === 'new'
                    ? '新建个人 Agent'
                    : edit.mode === 'personal'
                      ? `编辑个人固定版本 v${edit.ref?.revision}`
                      : '项目局部调整'}
                </h3>
                <button
                  className="text-xs underline"
                  disabled={busy}
                  onClick={() => {
                    try {
                      localStorage.removeItem(draftKey(owner, edit.identity));
                      reloadDrafts();
                      editSequence.current++;
                      setEdit(null);
                      setPrepared(null);
                    } catch (e) {
                      setStorageError(message(e));
                    }
                  }}
                >
                  清除这份草稿
                </button>
              </div>
              <p className="text-xs text-muted-foreground">关闭或切换标签会保留这份草稿。</p>
              {edit.mode === 'override' && Object.keys(edit.adoption!.overrides).length > 0 && (
                <div className="flex flex-wrap gap-2">
                  {Object.keys(edit.adoption!.overrides).map((k) => (
                    <Button
                      key={k}
                      size="sm"
                      variant="outline"
                      disabled={busy}
                      onClick={() => change({ ...edit, changes: { ...edit.changes, [k]: null } })}
                    >
                      取消本级覆盖：{agentFields[k as keyof AgentConfiguration] ?? k}
                    </Button>
                  ))}
                </div>
              )}
              <AgentEditor
                owner={owner}
                base={edit.base}
                inherited={edit.adoption?.fixed.configuration}
                changes={edit.changes}
                capabilities={capabilities.data?.items ?? []}
                onChange={(changes) => change({ ...edit, changes })}
                disabled={busy}
              />
              {capabilities.error && <p role="alert">能力目录不可用，暂不能保存。</p>}
              <Button
                disabled={!valid || busy || !!storageError || !capabilities.data}
                onClick={() => void prepare()}
              >
                {busy ? '正在读取…' : edit.mode === 'override' ? '预览项目调整' : '预览发布内容'}
              </Button>
            </section>
          )}
          {prepared && (
            <section className="space-y-3 rounded-lg border p-4">
              <h3 className="font-medium">发布预览</h3>
              <p className="text-sm text-muted-foreground">
                {prepared.channel === 'personal'
                  ? '形成个人不可变版本，不自动修改任何项目采用。'
                  : '仅影响当前项目配置，不修改个人库或其他项目。'}
              </p>
              <details open>
                <summary className="cursor-pointer text-sm">实际配置与固定依赖</summary>
                <div className="max-h-72 overflow-auto py-3">
                  <AgentSummary configuration={prepared.configuration} />
                </div>
              </details>
              <Button disabled={busy || !!storageError} onClick={() => void run(prepared)}>
                {busy ? '正在保存…' : prepared.button}
              </Button>
            </section>
          )}
          {upgrade && (
            <section className="space-y-3 rounded-lg border p-4">
              <h3 className="font-medium">显式升级到个人 v{upgrade.ref.revision}</h3>
              <div className="grid gap-4 sm:grid-cols-2">
                <details>
                  <summary>原固定配置与依赖</summary>
                  <AgentSummary configuration={upgrade.before} />
                </details>
                <details>
                  <summary>新版本配置与依赖</summary>
                  <AgentSummary configuration={upgrade.after} />
                </details>
              </div>
              {upgrade.conflicts.length === 0 ? (
                <p className="text-sm">没有覆盖冲突，保留现有项目覆盖。</p>
              ) : (
                upgrade.conflicts.map((k) => (
                  <label key={k} className="block text-sm">
                    {agentFields[k as keyof AgentConfiguration] ?? k}：个人新版与项目覆盖均有变化
                    <select
                      className="mt-1 w-full rounded border bg-background p-2"
                      aria-label={`处理冲突：${agentFields[k as keyof AgentConfiguration] ?? k}`}
                      value={resolutions[k] ?? ''}
                      onChange={(e) =>
                        setResolutions({ ...resolutions, [k]: e.target.value as 'keep' | 'drop' })
                      }
                    >
                      <option value="">请选择处理方式</option>
                      <option value="keep">保留项目覆盖</option>
                      <option value="drop">取消覆盖，采用新版值</option>
                    </select>
                  </label>
                ))
              )}
            </section>
          )}
          {upgrade && currentUpgradeConfiguration && (
            <section aria-label="项目当前生效配置" className="space-y-3 rounded-lg border p-4">
              <h3 className="font-medium">项目当前生效配置</h3>
              <p className="text-sm text-muted-foreground">
                包含项目已有覆盖；选择保留时继续使用这些值。
              </p>
              <AgentSummary configuration={currentUpgradeConfiguration} />
            </section>
          )}
          {upgrade &&
            (resolvedUpgradeConfiguration ? (
              <section aria-label="升级后生效预览" className="space-y-3 rounded-lg border p-4">
                <h3 className="font-medium">升级后生效预览</h3>
                <p className="text-sm text-muted-foreground">
                  按当前冲突选择合成，未冲突的项目覆盖继续保留。
                </p>
                <AgentSummary configuration={resolvedUpgradeConfiguration} />
              </section>
            ) : (
              <p className="text-sm text-muted-foreground">
                请处理全部冲突，再查看升级后完整生效配置。
              </p>
            ))}
          {upgrade && (
            <Button
              disabled={busy || upgrade.conflicts.some((k) => !resolutions[k])}
              onClick={() =>
                void run({
                  channel: 'project',
                  label: upgrade.after.name,
                  configuration: upgrade.after,
                  button: '',
                  request: {
                    request_id: crypto.randomUUID(),
                    action: 'upgrade',
                    payload: {
                      adoption_id: upgrade.adoption_id,
                      expected_revision: upgrade.expected_revision,
                      ref: upgrade.ref,
                      proof: upgrade.proof,
                      resolutions,
                    },
                  },
                })
              }
            >
              确认升级，保留未冲突覆盖
            </Button>
          )}
        </>
      )}
    </div>
  );
}
