import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { jsonWithBackendError } from "@/lib/api-errors";
import { p } from "@/lib/api-path";
import { Button } from "@/components/ui/button";
import { DirectorReviewWorkbench } from "@/components/episode/director-review/director-review-workbench";
import { useEpisodes } from "@/lib/queries/episodes";
import { directorPlanKeys, useDirectorPlans, useEditDirectorPlan } from "@/lib/queries/director-plans";
import { directorPresets, directorSuggestions, emptyDirectorConfig, type DirectorConfig } from "./director-config";
import { readStudioContext } from "./studio-context";
import { useTasks } from "@/lib/queries/tasks";
import { DirectorAdaptations } from "./director-adaptations";
import { ArrowRight, Check, Clapperboard, Film, SlidersHorizontal, Sparkles } from "lucide-react";

type Doc = { id: string; name: string; data: DirectorConfig; revision: number };
type TeamOverview = { draft: { draft_revision: number; data: { template_id: string; template_revision: number; overrides: Record<string, Record<string, Record<string, unknown>>> } } | null; active: { active_revision: number } | null; effective: { director: { director_plan: { config: { director_preferences: Partial<DirectorConfig> } } } } };
const fieldClass = "w-full rounded-md border bg-background p-2 text-sm";
const labels = { pace: "叙事节奏", camera_motion: "运镜", composition: "构图", performance: "人物表演", method: "补充方法" } as const;

export function DirectorStudio({ project }: { project: string }) {
  return <DirectorWorkspace key={project} project={project} />;
}

function DirectorWorkspace({ project }: { project: string }) {
  const queryClient = useQueryClient();
  const episodes = useEpisodes(project);
  const [episode, setEpisode] = useState(() => readStudioContext(window.location.search, project).episode ?? 0);
  const [name, setName] = useState("我的导演方法");
  const [config, setConfig] = useState<DirectorConfig>({ ...emptyDirectorConfig });
  const [doc, setDoc] = useState<Doc | null>(null);
  const [legacyImportId, setLegacyImportId] = useState('');
  const [dirty, setDirty] = useState(false);
  const [source, setSource] = useState("builtin");
  const [presetName, setPresetName] = useState("");
  const [workflow, setWorkflow] = useState<"existing" | "ai">("existing");
  const [text, setText] = useState("");
  const [message, setMessage] = useState("");
  const [revisionId, setRevisionId] = useState("");
  const [shotId, setShotId] = useState(() => { const group = readStudioContext(window.location.search, project).group; return group ? `group:${group}` : ""; });
  const [review, setReview] = useState(false);
  const [suggestions, setSuggestions] = useState<ReturnType<typeof directorSuggestions>>([]);
  const liveSignature = useRef("");
  liveSignature.current = JSON.stringify({ name, config });
  const docs = useQuery({ queryKey: ["studios", project, "director"], queryFn: () => api.get(p`api/v1/projects/${project}/studios/director`).json<{ data: Doc[] }>() });
  const team = useQuery({ queryKey: ["agent-team", project], queryFn: () => api.get(p`api/v1/projects/${project}/agent-team`).json<TeamOverview>() });
  const shared = team.data?.draft;
  const active = team.data?.active;
  const loadedRevision = useRef<number | null>(null);
  useEffect(() => {
    if (!shared || dirty || loadedRevision.current === shared.draft_revision) return;
    setConfig({ ...emptyDirectorConfig, ...team.data!.effective.director.director_plan.config.director_preferences });
    loadedRevision.current = shared.draft_revision;
    setSuggestions([]);
  }, [team.data, shared, dirty]);
  useEffect(() => { const refresh = () => { void team.refetch(); }; window.addEventListener('agent-team-changed', refresh); return () => window.removeEventListener('agent-team-changed', refresh); }, [team.refetch]);
  const plans = useDirectorPlans(project, episode);
  const tasks = useTasks({ project, episode });
  const completedPlans = tasks.data?.data.filter((task) => task.task_type === "director_plan" && task.status === "completed").map((task) => task.scope).join("|") ?? "";
  useEffect(() => { if (completedPlans) void queryClient.invalidateQueries({ queryKey: directorPlanKeys.all(project, episode) }); }, [completedPlans, project, episode, queryClient]);
  const edit = useEditDirectorPlan(project, episode);
  const revisions = plans.data?.ok ? plans.data.data : [];
  const selected = revisions.find((item) => item.revision_id === revisionId);
  const shots = selected?.groups.flatMap((group) => group.shots) ?? [];
  const scopeGroup = shotId.startsWith("group:") ? selected?.groups.find((group) => `group:${group.id}` === shotId) : undefined;
  const invalidScope = !!shotId && (shotId.startsWith("group:") ? !scopeGroup : !shots.some((shot) => shot.id === shotId));
  const scopedShots = scopeGroup ? scopeGroup.shots : shots.filter((shot) => !shotId || shot.id === shotId);
  const save = useMutation({ onMutate: () => liveSignature.current, mutationFn: async () => {
    if (!team.isSuccess) throw new Error('请先加载团队状态');
    if (shared) {
      const data = shared.data;
      const overrides = structuredClone(data.overrides);
      overrides.director ??= {};
      overrides.director.director_plan ??= {};
      overrides.director.director_plan.director_preferences = Object.fromEntries(Object.keys(labels).map(key => [key, config[key]]));
      const saved = await jsonWithBackendError<{ draft_revision: number }>(api.put(p`api/v1/projects/${project}/agent-team/draft`, { json: { data: { template_id: data.template_id, template_revision: data.template_revision, overrides }, expected_revision: loadedRevision.current ?? shared.draft_revision } }));
      loadedRevision.current = saved.draft_revision;
      await queryClient.invalidateQueries({ queryKey: ['agent-team', project] });
      window.dispatchEvent(new Event('agent-team-changed'));
      return { data: { id: 'shared-team', name, data: config, revision: saved.draft_revision } };
    }
    const id = doc?.id ?? crypto.randomUUID();
    return jsonWithBackendError<{ data: Doc }>(api.put(p`api/v1/projects/${project}/studios/director/${id}`, { json: { name, data: config, expected_revision: doc?.revision ?? 0 } }));
  }, onSuccess: (result, _variables, savedSignature) => { setDoc(result.data); const newerEdits = liveSignature.current !== savedSignature; setDirty(newerEdits); if (newerEdits) window.dispatchEvent(new Event("studio-save-failed-director")); setMessage(newerEdits ? "提交的版本已保存；保存期间新增的修改仍未保存，请再次保存。" : "配置版本已保存，可生成镜头建议。"); void docs.refetch(); }, onError: (error) => { setMessage(`保存失败（版本冲突请重新加载）：${error.message}`); window.dispatchEvent(new Event("studio-save-failed-director")); } });
  const importing = useMutation({ mutationFn: async (file?: File) => {
    const form = new FormData();
    if (file) form.append("file", file);
    return jsonWithBackendError<{ data: DirectorConfig }>(api.post(p`api/v1/projects/${project}/studios/director/${file ? "import-file" : "import-text"}`, file ? { body: form } : { json: { text } }));
  }, onSuccess: (result) => { setConfig(result.data); setDirty(true); setSuggestions([]); setMessage("文本已提取；已识别带标签的字段，请检查来源并补全配置后保存。"); }, onError: (error) => setMessage(`导入失败：${error.message}`) });
  const aiPlan = useMutation({ mutationFn: async (allowAdaptation: boolean) => {
    if (!team.isSuccess) throw new Error('请先加载团队状态');
    if (active && !allowAdaptation) return jsonWithBackendError<{ task_id: string; status: string; reused: boolean }>(api.post(p`api/v1/projects/${project}/agent-team/director-plan`, { json: { episode, expected_active_revision: active.active_revision } }));
    if (!doc) throw new Error("请先保存配置");
    return jsonWithBackendError<{ task_id: string; status: string; reused: boolean }>(api.post(p`api/v1/projects/${project}/studios/director/${doc.id}/plan`, { json: { episode, expected_revision: doc.revision, allow_adaptation: allowAdaptation } }));
  }, onSuccess: (result) => { setMessage(`${result.reused ? "已找回已有" : "已提交"}导演规划任务 ${result.task_id}（${result.status}）。配置已固定到提交时版本，结果在方案审阅中确认。`); void tasks.refetch(); }, onError: (error) => setMessage(`规划提交失败：${error.message}`) });
  const importLegacy = useMutation({ onMutate: () => liveSignature.current, mutationFn: async () => {
    if (!team.isSuccess) throw new Error('请先加载团队状态');
    const legacy = shared ? docs.data?.data.find(item => item.id === legacyImportId) : doc;
    if (!legacy) throw new Error('请先选择已保存的旧配置');
    return jsonWithBackendError(api.post(p`api/v1/projects/${project}/agent-team/import-director-method`, { json: { document_id: legacy.id, expected_document_revision: legacy.revision, expected_draft_revision: shared?.draft_revision ?? 0 } }));
  }, onSuccess: (_result, _variables, submittedSignature) => { const newerEdits = liveSignature.current !== submittedSignature; if (!newerEdits) loadedRevision.current = null; setDirty(newerEdits); void queryClient.invalidateQueries({ queryKey: ['agent-team', project] }); window.dispatchEvent(new Event('agent-team-changed')); setMessage(newerEdits ? '旧配置已导入团队草稿；导入期间的本地修改仍保留，请重新加载或处理版本冲突。' : '旧配置偏好已导入团队草稿，尚未启用；改编许可与原文保留在旧配置。'); }, onError: error => setMessage(`导入团队失败：${error.message}`) });
  useEffect(() => { window.dispatchEvent(new CustomEvent("studio-dirty", { detail: { dirty, module: "director" } })); }, [dirty]);
  useEffect(() => { const handler = () => save.mutate(); window.addEventListener("studio-save-director", handler); return () => window.removeEventListener("studio-save-director", handler); }, [save.mutate]);
  useEffect(() => { if (!dirty) return; const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; }; window.addEventListener("beforeunload", warn); return () => window.removeEventListener("beforeunload", warn); }, [dirty]);
  const update = (next: DirectorConfig) => { setConfig(next); setDirty(true); setSuggestions([]); };
  async function adopt(shotIds: string[]) {
    if (!selected) return;
    let current = selected.revision_id;
    try {
      for (const suggestion of suggestions.filter((item) => shotIds.includes(item.shot_id))) {
        const result = await edit.mutateAsync({ revisionId: current, command: { kind: "update_shot", ...suggestion } });
        if (!result.ok) throw new Error("导演方案保存失败");
        current = result.data.revision_id;
        setRevisionId(current);
        setSuggestions((items) => items.filter((item) => item.shot_id !== suggestion.shot_id));
      }
      setMessage("已创建导演方案版本；请在方案审阅中检查差异及资产影响后启用。已有视频保留。");
    } catch (error) { setMessage(`采纳中断，已成功版本保留：${error instanceof Error ? error.message : String(error)}`); }
  }
  return <section className="director-workspace" aria-label="导演工作室">
    {!team.isSuccess && <p role="alert">{team.isError ? '团队状态读取失败，暂不能保存或生成。' : '正在读取团队状态…'}<Button variant="outline" onClick={() => void team.refetch()}>重试读取团队状态</Button></p>}
    {shared && dirty && <Button variant="outline" onClick={() => { loadedRevision.current = null; setDirty(false); setSuggestions([]); void team.refetch(); }}>放弃本地修改并重新加载团队草稿</Button>}
    {shared && <p className="rounded-md border p-3 text-sm">团队共享草稿 v{shared.draft_revision} · {active ? `当前启用 v${active.active_revision}` : '尚未启用'}。保存只更新草稿；本地镜头建议使用此草稿，AI 使用启用版本。团队模式暂不支持改编建议，原有改编许可与来源原文保留在旧配置。<a className="underline" href={`/projects/${encodeURIComponent(project)}/studios?studio=agent-team`}>前往团队比较与启用</a></p>}
    <div className="flex flex-wrap items-end justify-between gap-3"><div><h2 className="text-xl font-semibold">导演自定义</h2><p className="mt-1 text-sm text-muted-foreground">选一套导演方法，让镜头有清晰的表达。所有预设都可以继续修改。</p></div><div className="flex items-center gap-2 text-xs text-muted-foreground"><span>选导演</span><ArrowRight className="size-3" /><span>选对象</span><ArrowRight className="size-3" /><span>预览与应用</span></div></div>
    <section className="director-presets space-y-3" aria-label="选择导演方法">
      <div className="flex flex-wrap items-center justify-between gap-3"><h3 className="flex items-center gap-2 font-medium"><Clapperboard className="size-4 text-primary" /><span className="text-xs text-muted-foreground">01</span> 选择导演</h3><label className="flex items-center gap-2 text-sm">配置来源<select className="rounded-md border bg-background px-3 py-2 text-sm" value={source} onChange={(event) => setSource(event.target.value)}><option value="builtin">内置导演预设</option><option value="custom">自建配置</option><option value="import">导入个人方法</option></select></label></div>
      {source === "builtin" && <><div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">{directorPresets.map((preset, index) => <button key={preset.name} type="button" aria-label={preset.name} aria-pressed={presetName === preset.name} className={`group relative overflow-hidden rounded-lg border p-4 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${presetName === preset.name ? 'border-primary bg-primary/10 ring-1 ring-primary' : 'border-border bg-background hover:border-primary/50 hover:bg-muted/50'}`} onClick={() => { setPresetName(preset.name); setName(preset.name); update({ ...preset.data }); }}><div aria-hidden="true" className="relative mb-4 flex h-16 items-center justify-between overflow-hidden rounded-md border border-border/50 bg-gradient-to-br from-muted to-background px-3"><span className="text-[10px] font-medium tracking-[0.18em] text-muted-foreground">{preset.code}</span><span className="relative flex h-10 w-14 items-center justify-center rounded border border-foreground/20"><span className={`h-6 w-px bg-primary/70 ${index % 2 ? 'rotate-45' : '-rotate-12'}`} /><span className="absolute inset-x-2 top-1/2 h-px bg-primary/50" /></span></div><div className="flex items-center justify-between gap-2"><span className="font-medium">{preset.name}</span>{presetName === preset.name && <Check className="size-4 text-primary" aria-hidden="true" />}</div><p className="mt-1 text-xs leading-relaxed text-muted-foreground">{preset.description}</p><div className="mt-3 flex flex-wrap gap-1.5">{preset.tags.map(tag => <span key={tag} className="rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">{tag}</span>)}</div></button>)}</div><p className="text-xs text-muted-foreground">本地创作配置 · 选择和编辑免费，不会调用模型。预设描述的是导演偏好，不代表特定平台模型或自动生成能力。</p></>}
      {source === "custom" && <div className="flex items-center gap-3 rounded-lg border border-dashed p-4"><SlidersHorizontal className="size-5 text-muted-foreground" /><div><p className="text-sm font-medium">你的方法，你来定义</p><p className="text-xs text-muted-foreground">在下方设置名称，展开高级导演参数，从当前配置继续调整。</p></div></div>}
      {source === "import" && <div className="space-y-2"><textarea aria-label="导演方法原文" className={fieldClass} value={text} onChange={(event) => setText(event.target.value)} placeholder="粘贴方法，可用 节奏：/运镜：/构图：/表演： 标注" /><Button disabled={!text.trim() || importing.isPending} onClick={() => importing.mutate(undefined)}>提取文字配置</Button><label className="block text-sm">导入 TXT / MD / PDF / DOCX<input type="file" accept=".txt,.md,.pdf,.docx" disabled={importing.isPending} onChange={(event) => { const file = event.target.files?.[0]; if (file) importing.mutate(file); event.target.value = ""; }} /></label><p className="text-xs text-muted-foreground">最多 10 MB；扫描件不执行 OCR。文档内容仅作为创作素材。</p></div>}
    </section>
    <div className="director-body"><div className="director-config space-y-4">
      <div className="flex items-center justify-between gap-2"><h3 className="font-medium">当前导演方法</h3><span className="rounded bg-muted px-2 py-1 text-xs text-muted-foreground">{dirty ? '未保存' : shared ? `共享草稿 v${shared.draft_revision}` : doc ? `v${doc.revision} 已保存` : '待配置'}</span></div>
      <label className="block text-sm">配置名称<input className={fieldClass} value={name} onChange={(event) => { setName(event.target.value); setDirty(true); }} /></label>
      <dl className="space-y-2 rounded-lg bg-muted/40 p-3 text-xs">{(['pace', 'camera_motion', 'composition'] as const).map(key => <div key={key}><dt className="text-muted-foreground">{labels[key]}</dt><dd className="mt-0.5 leading-relaxed">{config[key] || '待设置'}</dd></div>)}</dl>
      <details className="rounded-lg border p-3" open={source === 'custom' ? true : undefined}><summary className="cursor-pointer text-sm font-medium">高级导演参数</summary><div className="mt-3 space-y-3">{Object.entries(labels).map(([key, label]) => <label key={key} className="block text-sm">{label}<textarea className={fieldClass} rows={3} value={String(config[key] ?? "")} onChange={(event) => update({ ...config, [key]: event.target.value })} /></label>)}</div></details>
      {config.source_text && <details><summary>导入来源片段</summary><pre className="max-h-48 overflow-auto whitespace-pre-wrap text-xs">{config.source_text}</pre></details>}
      <p className="text-xs text-muted-foreground">默认保持剧情、人物动机和对白。本地建议应用运镜和构图；AI 规划会读取节奏、表演和补充方法。</p>
      <label className="flex items-center gap-2 text-sm"><input type="checkbox" disabled={!!shared || !!active} checked={config.allow_adaptation} onChange={(event) => update({ ...config, allow_adaptation: event.target.checked })} />允许提出故事与对白改编建议（单独审阅）</label>
      <Button disabled={!team.isSuccess || save.isPending || !name.trim()} onClick={() => save.mutate()}>保存配置版本{dirty ? " *" : ""}</Button>
      {!shared && doc && <Button variant="outline" disabled={!team.isSuccess || dirty || importLegacy.isPending} onClick={() => importLegacy.mutate()}>将旧配置导入团队草稿（不启用）</Button>}
      {shared && <div className="space-y-2"><label className="block text-sm">导入已保存导演方法<select className={fieldClass} value={legacyImportId} disabled={importLegacy.isPending} onChange={event => setLegacyImportId(event.target.value)}><option value="">选择旧配置</option>{docs.data?.data.filter(item => !item.data.purpose).map(item => <option key={item.id} value={item.id}>{item.name} · v{item.revision}</option>)}</select></label><Button variant="outline" disabled={!team.isSuccess || !legacyImportId || dirty || importLegacy.isPending || save.isPending} onClick={() => importLegacy.mutate()}>将所选旧配置导入团队草稿（不启用）</Button>{dirty && <p className="text-xs text-muted-foreground">请先保存或放弃本地修改，再导入旧配置。</p>}</div>}
      <label className="block text-sm">重新加载已保存配置<select disabled={save.isPending || !!shared} className={fieldClass} value={doc?.id ?? ""} onChange={(event) => { if (save.isPending) return; const value = docs.data?.data.find((item) => item.id === event.target.value); if (value && (!dirty || window.confirm("放弃未保存的配置修改并加载所选版本？"))) { setDoc(value); setConfig(value.data); setName(value.name); setDirty(false); setSuggestions([]); } }}><option value="">选择配置</option>{docs.data?.data.filter((item) => !item.data.purpose).map((item) => <option key={item.id} value={item.id}>{item.name} · v{item.revision}</option>)}</select></label>
      {docs.error && <p role="alert">配置列表读取失败：{docs.error.message}</p>}
    </div><div className="director-review space-y-4">
      <h3 className="flex items-center gap-2 font-medium"><Film className="size-4 text-primary" /><span className="text-xs text-muted-foreground">02</span> 选择创作对象</h3>
      <div className="grid gap-2 sm:grid-cols-2"><button type="button" aria-pressed={workflow === 'existing'} className={`rounded-lg border p-3 text-left ${workflow === 'existing' ? 'border-primary bg-primary/10' : 'hover:bg-muted/50'}`} onClick={() => setWorkflow('existing')}><span className="block text-sm font-medium">优化已有镜头</span><span className="mt-1 block text-xs text-muted-foreground">免费预览运镜与构图差异，逐镜头采纳</span></button><button type="button" aria-pressed={workflow === 'ai'} className={`rounded-lg border p-3 text-left ${workflow === 'ai' ? 'border-primary bg-primary/10' : 'hover:bg-muted/50'}`} onClick={() => setWorkflow('ai')}><span className="flex items-center gap-1 text-sm font-medium"><Sparkles className="size-3.5" />AI 整集规划</span><span className="mt-1 block text-xs text-muted-foreground">依据现有剧本与导演方法提交模型任务</span></button></div>
      <label className="block text-sm">应用到集<select aria-label="应用到集" className={fieldClass} value={episode} onChange={(event) => { setEpisode(Number(event.target.value)); setRevisionId(""); setShotId(""); setSuggestions([]); }}><option value={0}>请选择剧集</option>{episodes.data?.data.map((item) => <option key={item.number} value={item.number}>第 {item.number} 集 · {item.title}</option>)}</select></label>
      {episodes.error && <p role="alert">剧集加载失败：{episodes.error.message}</p>}
      {episode > 0 && episodes.data && !episodes.data.data.some((item) => item.number === episode) && <p role="alert">来源剧集已不存在，请重新选择。</p>}
      {workflow === 'existing' && <><label className="block text-sm">基于方案版本<select className={fieldClass} value={revisionId} onChange={(event) => { setRevisionId(event.target.value); setSuggestions([]); }}><option value="">选择已有方案</option>{revisions.map((item) => <option key={item.revision_id} value={item.revision_id}>{item.revision_id} · {item.status}</option>)}</select></label>
      <label className="block text-sm">镜头范围<select className={fieldClass} value={shotId} onChange={(event) => { setShotId(event.target.value); setSuggestions([]); }}><option value="">整集全部镜头</option>{selected?.groups.map((group) => <option key={group.id} value={`group:${group.id}`}>叙事组 {group.id}</option>)}{shots.map((shot) => <option key={shot.id} value={shot.id}>{shot.id} · {shot.subject}</option>)}</select></label>
      {selected && invalidScope && <p role="alert">来源叙事组或镜头不在此方案版本中，请明确重新选择范围。</p>}
      <Button disabled={!team.isSuccess || !selected || dirty || (!doc && !shared) || invalidScope} onClick={() => { const next = directorSuggestions(scopedShots, config); setSuggestions(next); setMessage(next.length ? `生成 ${next.length} 项本地配置建议，不调用模型。` : "所选镜头无需修改，或配置未指定运镜和构图。"); }}>生成可比较建议</Button><p className="text-xs text-muted-foreground">{(!doc && !shared) || dirty ? '先保存当前导演方法，再选择方案与镜头范围。' : !selected ? '选择已有方案后，可预览具体镜头的修改。' : '本地建议只调整运镜与构图，不产生模型费用。'}</p></>}
      {workflow === 'ai' && <div className="space-y-3 rounded-lg border border-primary/30 bg-primary/5 p-3"><p className="text-sm">基于所选剧集的现有剧本，读取已保存的节奏、运镜、构图、表演与补充方法，生成新的整集方案。</p><Button disabled={!team.isSuccess || !episode || (!active && (dirty || !doc || !!shared)) || aiPlan.isPending} onClick={() => aiPlan.mutate(false)}>{active ? `按团队启用 v${active.active_revision} 生成 AI 整集方案` : "按已保存方法生成 AI 整集方案"}</Button>{!shared && !active && config.allow_adaptation && <Button variant="outline" disabled={!team.isSuccess || !episode || dirty || !doc || aiPlan.isPending} onClick={() => aiPlan.mutate(true)}>单独生成故事与对白改编建议</Button>}<p className="text-xs text-muted-foreground">可能产生费用：使用项目已配置文本模型，价格以任务中心实际费用为准。同一配置和剧本版本重复提交查询已有任务。仅在点击生成时提交，结果需在方案审阅中确认。</p>{(!active && (!doc || dirty)) && <p className="text-xs text-muted-foreground">请先保存当前导演方法。</p>}</div>}
      {tasks.data?.data.filter((task) => task.task_type === "director_plan" || task.task_type === "director_studio_adaptation").map((task) => <p className="text-xs" key={`${task.task_type}/${task.episode}/${task.scope}`}>任务 {task.display_name ?? task.scope ?? "导演规划"} · {task.status}{task.error ? ` · ${task.error}` : ""}</p>)}
      <div className="border-t pt-4"><h3 className="flex items-center gap-2 font-medium"><span className="text-xs text-muted-foreground">03</span> 预览建议与应用</h3>{!suggestions.length && <p className="mt-2 text-xs text-muted-foreground">{workflow === 'existing' ? '生成建议后，这里展示修改前后对比。你决定采纳哪些镜头。' : 'AI 任务完成后，在下方打开方案审阅，比较版本并确认启用。'}</p>}</div>
      {suggestions.map((suggestion) => { const original = shots.find((shot) => shot.id === suggestion.shot_id); return <article className="space-y-3 rounded-lg border p-3" key={suggestion.shot_id}><h4 className="text-sm font-medium">{suggestion.shot_id} · {original?.subject}</h4>{Object.entries(suggestion.changes).map(([key, value]) => <div key={key}><p className="mb-1 text-xs text-muted-foreground">{labels[key as "camera_motion" | "composition"]}</p><div className="grid gap-2 text-sm sm:grid-cols-2"><div className="rounded bg-muted/50 p-2"><span className="mb-1 block text-[10px] text-muted-foreground">原方案</span>{original?.[key as "camera_motion" | "composition"] || '未指定'}</div><div className="rounded border border-primary/20 bg-primary/5 p-2"><span className="mb-1 block text-[10px] text-muted-foreground">导演建议</span>{value}</div></div></div>)}<Button variant="outline" disabled={edit.isPending} onClick={() => void adopt([suggestion.shot_id])}>采纳此镜头</Button></article>; })}
      {suggestions.length > 1 && <Button disabled={edit.isPending} onClick={() => void adopt(suggestions.map((item) => item.shot_id))}>采纳全部建议</Button>}
      <p className="text-xs text-muted-foreground">采纳创建新方案，不自动覆盖已有视频；启用与素材影响在方案审阅中确认。若无已有方案，可进入审阅发起真实导演规划任务，费用依据项目模型配置。</p>
      <Button variant="outline" disabled={!episode} onClick={() => setReview(true)}>打开方案审阅与生成</Button>
      {plans.error && <p role="alert">方案读取失败：{plans.error.message}</p>}
      {episode > 0 && <DirectorAdaptations project={project} episode={episode} />}
    </div></div>
    {message && <p role="status" className="rounded-md border p-3 text-sm">{message}</p>}
    {review && episode > 0 && <DirectorReviewWorkbench key={`${project}/${episode}`} project={project} episode={episode} onClose={() => setReview(false)} />}
  </section>;
}
