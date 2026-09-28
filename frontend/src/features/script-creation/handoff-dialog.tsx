import { useEffect, useMemo, useRef, useState } from "react";
import { scriptCreationApi } from "./api";
import type { ConsistencyIssue, ConsistencyRun, HandoffPrepareRequest, HandoffScope, NarrativeEntity, ScriptDocument, ScriptHandoff } from "./types";

function message(cause: unknown) { return cause instanceof Error ? cause.message : "操作失败，请重试"; }
const pending = new Set(["source_written", "dispatching", "dispatched"]);
const statusName: Record<ScriptHandoff["status"], string> = {
  prepared: "待确认", source_written: "正文已交接，待派发", dispatching: "派发中",
  dispatched: "制作校对中", completed: "已完成", failed: "校对失败", needs_rebase: "源版本变化，需重新预览",
};
function sameRevisions(a: Record<string, string>, b: Record<string, string>) {
  const keys = Object.keys(a);
  return keys.length === Object.keys(b).length && keys.every((key) => a[key] === b[key]);
}
function currentRun(run: ConsistencyRun, revisions: Record<string, string>) {
  return run.mode === "actual" && run.status === "completed" && !run.proposal_id &&
    Object.entries(run.context_revisions).every(([id, revision]) => revisions[id] === revision);
}

export function HandoffDialog({ project, document, documents, allSaved, onClose, onRefresh }: {
  project: string; document: ScriptDocument; documents: ScriptDocument[]; allSaved: boolean;
  onClose: () => void; onRefresh: () => Promise<unknown> | void;
}) {
  const [entities, setEntities] = useState<NarrativeEntity[]>([]);
  const [runs, setRuns] = useState<ConsistencyRun[]>([]);
  const [history, setHistory] = useState<ScriptHandoff[]>([]);
  const [selectedRefs, setSelectedRefs] = useState<string[]>([]);
  const [selectedEntities, setSelectedEntities] = useState<string[]>([]);
  const [scopeMode, setScopeMode] = useState<HandoffScope["mode"]>("all");
  const [sceneIds, setSceneIds] = useState<string[]>([]);
  const [availableSceneIds, setAvailableSceneIds] = useState<string[]>([]);
  const [factMode, setFactMode] = useState<"unchecked" | "checked">("unchecked");
  const [uncheckedReason, setUncheckedReason] = useState("");
  const [checkedRunId, setCheckedRunId] = useState("");
  const [issueReasons, setIssueReasons] = useState<Record<string, string>>({});
  const [prepared, setPrepared] = useState<ScriptHandoff | null>(null);
  const [preparedKey, setPreparedKey] = useState("");
  const [historyView, setHistoryView] = useState(false);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const requestSequence = useRef(0);
  const prepareMutation = useRef<{ key: string; id: string } | null>(null);
  const confirmMutations = useRef(new Map<string, string>());
  const mounted = useRef(true);

  const revisionMap = useMemo(() => Object.fromEntries(documents.map((item) => [item.id, item.current_revision_id])), [documents]);
  const selectedReferenceRevisions = useMemo(() => Object.fromEntries(selectedRefs.map((id) => [id, revisionMap[id]]).filter((entry) => entry[1])), [selectedRefs, revisionMap]);
  const contextRevisions = useMemo(() => ({ [document.id]: document.current_revision_id, ...selectedReferenceRevisions }), [document.id, document.current_revision_id, selectedReferenceRevisions]);
  const currentRuns = runs.filter((run) => currentRun(run, revisionMap) && run.context_revisions[document.id] === document.current_revision_id);
  const matchingRuns = currentRuns.filter((run) => sameRevisions(run.context_revisions, contextRevisions));
  const knownFacts = currentRuns.flatMap((run) => run.issues.filter((issue) => issue.category === "fact" && issue.mode === "actual" && !issue.proposal_id && !issue.stale));
  const uniqueFacts = [...new Map(knownFacts.map((issue) => [issue.id, issue])).values()];
  const designDocuments = documents.filter((item) => item.kind !== "episode_script" &&
    (item.episode_number === null || item.episode_number === document.episode_number));
  const selectedSet = new Set(selectedEntities);
  const selectedRefSet = new Set(selectedRefs);
  const relationErrors = selectedEntities.flatMap((id) => {
    const entity = entities.find((item) => item.entity_id === id);
    if (!entity) return [`实体 ${id} 已失效`];
    return entity.relations.flatMap((relation) => {
      const target = entities.find((item) => item.entity_id === relation.entity_id);
      if (relation.missing || relation.stale || !target || target.entry_missing || target.stale || target.asset_missing)
        return [`${entity.name} 的关联实体已失效，请先修复`];
      if (!selectedSet.has(target.entity_id) || !selectedRefSet.has(target.document_id))
        return [`${entity.name} 的关联需要同时选择 ${target.name} 及其设定文档`];
      return [];
    });
  });
  const scope: HandoffScope = { mode: scopeMode, scene_ids: scopeMode === "selected" ? sceneIds : [] };
  const acknowledgement = { mode: factMode,
    reason: factMode === "unchecked" ? uncheckedReason.trim() : null,
    run_id: factMode === "checked" ? checkedRunId : null,
    issue_reasons: Object.fromEntries(uniqueFacts.map((issue) => [issue.id, (issue.intentional_reason || issueReasons[issue.id] || "").trim()])),
  };
  const requestCore = { document_id: document.id, revision_id: document.current_revision_id,
    reference_revisions: selectedReferenceRevisions, selected_entity_ids: selectedEntities,
    update_scope: scope, fact_acknowledgement: acknowledgement };
  const requestKey = JSON.stringify(requestCore);
  const valid = allSaved && document.revision.markdown.trim() && !relationErrors.length &&
    (scopeMode !== "selected" || (sceneIds.length > 0 && sceneIds.every((id) => availableSceneIds.includes(id)))) &&
    (factMode === "unchecked" ? !!uncheckedReason.trim() : !!checkedRunId && matchingRuns.some((run) => run.id === checkedRunId)) &&
    uniqueFacts.every((issue) => !!(issue.intentional_reason || issueReasons[issue.id] || "").trim());
  const activePreview = prepared && (historyView || preparedKey === requestKey) ? prepared : null;
  const canConfirm = activePreview?.status === "prepared" && allSaved && document.current_revision_id === activePreview.revision_id &&
    Object.entries(activePreview.snapshot.reference_revisions).every(([id, revision]) => revisionMap[id] === revision);

  useEffect(() => {
    mounted.current = true;
    let alive = true;
    setLoading(true);
    void Promise.all([
      scriptCreationApi.listEntities(project),
      scriptCreationApi.listConsistencyRuns(project, document.id),
      scriptCreationApi.listHandoffs(project, document.episode_number ?? 0),
    ]).then(([nextEntities, nextRuns, nextHistory]) => {
      if (!alive) return;
      setEntities(nextEntities); setRuns(nextRuns); setHistory(nextHistory);
    }).catch((cause) => { if (alive) setError(message(cause)); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; mounted.current = false; requestSequence.current++; };
  }, [project, document.id, document.episode_number]);

  useEffect(() => {
    if (prepared && !historyView && preparedKey !== requestKey) setPrepared(null);
  }, [prepared, preparedKey, requestKey, historyView]);

  useEffect(() => {
    const active = history.filter((item) => pending.has(item.status));
    if (!active.length) return;
    let stopped = false;
    const timer = setTimeout(() => {
      void Promise.all(active.map((item) => scriptCreationApi.getHandoff(project, item.id)))
        .then((updates) => {
          if (stopped) return;
          const byId = new Map(updates.map((item) => [item.id, item]));
          setHistory((prior) => prior.map((item) => byId.get(item.id) ?? item));
          setPrepared((prior) => prior && byId.get(prior.id) || prior);
          if (updates.some((item) => item.source_revision)) void onRefresh();
        }).catch((cause) => { if (!stopped) setError(message(cause)); });
    }, 2000);
    return () => { stopped = true; clearTimeout(timer); };
  }, [history, project, onRefresh]);

  function mergeRecord(record: ScriptHandoff) {
    setHistory((prior) => [record, ...prior.filter((item) => item.id !== record.id)]);
    setPrepared(record);
    if (record.diff.available_scene_ids.length) setAvailableSceneIds(record.diff.available_scene_ids);
  }
  async function preview() {
    if (!valid || busy) return;
    const key = requestKey;
    const sequence = ++requestSequence.current;
    const mutationId = prepareMutation.current?.key === key ? prepareMutation.current.id : crypto.randomUUID();
    prepareMutation.current = { key, id: mutationId };
    setBusy(true); setError(""); setHistoryView(false);
    try {
      const record = await scriptCreationApi.prepareHandoff(project, { ...requestCore,
        client_mutation_id: mutationId } as HandoffPrepareRequest);
      if (!mounted.current || requestSequence.current !== sequence) return;
      mergeRecord(record); setPreparedKey(key);
    } catch (cause) { if (mounted.current && requestSequence.current === sequence) setError(message(cause)); }
    finally { if (mounted.current && requestSequence.current === sequence) setBusy(false); }
  }
  async function confirm() {
    const record = activePreview;
    if (!record || !canConfirm || busy) return;
    let mutationId = confirmMutations.current.get(record.id);
    if (!mutationId) { mutationId = crypto.randomUUID(); confirmMutations.current.set(record.id, mutationId); }
    const sequence = ++requestSequence.current;
    setBusy(true); setError("");
    try {
      const updated = await scriptCreationApi.confirmHandoff(project, record.id, {
        expected_source_project_revision: record.expected_source_project_revision, client_mutation_id: mutationId,
      });
      if (!mounted.current || sequence !== requestSequence.current) return;
      mergeRecord(updated); await onRefresh();
    } catch (cause) {
      if (mounted.current && sequence === requestSequence.current) setError(message(cause) + "；可以用同一确认记录重试");
    } finally { if (mounted.current && sequence === requestSequence.current) setBusy(false); }
  }
  async function retry(record: ScriptHandoff) {
    if (busy) return;
    setBusy(true); setError("");
    try { const updated = await scriptCreationApi.retryHandoff(project, record.id);
      if (mounted.current) { mergeRecord(updated); await onRefresh(); }
    } catch (cause) { if (mounted.current) setError(message(cause)); }
    finally { if (mounted.current) setBusy(false); }
  }
  function openHistory(record: ScriptHandoff) {
    mergeRecord(record); setHistoryView(true); setError("");
  }

  return <div role="dialog" aria-modal="true" aria-label="确认本集并交接制作" className="fixed inset-0 z-50 flex items-center justify-center bg-black/75 p-3 text-[#EDF0F2]">
    <div className="flex max-h-[94vh] w-full max-w-4xl flex-col overflow-hidden rounded-xl border border-white/15 bg-[#14161A] shadow-2xl">
      <div className="flex items-center justify-between border-b border-white/10 px-5 py-4">
        <div><h2 className="font-semibold">确认本集并交接制作</h2><p className="text-xs text-white/50">{document.title} · 当前草稿 {document.current_revision_id.slice(0, 8)} · 已交接 {document.adopted_revision_id?.slice(0, 8) ?? "无"}</p></div>
        <button onClick={onClose} aria-label="关闭交接窗口" className="rounded px-2 py-1 text-white/60 hover:text-white">关闭</button>
      </div>
      <div className="min-h-0 overflow-y-auto p-5 text-sm">
        {error && <p role="alert" className="mb-4 rounded border border-amber-400/30 bg-amber-400/10 p-3 text-amber-200">{error}</p>}
        {!allSaved && <p className="mb-4 rounded border border-amber-400/30 p-3 text-amber-200">存在未保存、保存中或冲突的文档，请先处理后再交接。</p>}
        {loading ? <p>正在读取交接资料…</p> : <>
          {historyView && <button className="mb-4 text-xs text-[#E5FF5C]" onClick={() => { setHistoryView(false); setPrepared(null); }}>返回新交接</button>}
          {!historyView && <section className="space-y-4">
            <h3 className="font-semibold">选择交接范围</h3>
            <div className="rounded border border-white/10 p-3"><p className="mb-2 text-xs text-white/60">明确选择本次采用的设定文档。未选择的后续集设定不会交接。</p>
              {designDocuments.map((item) => <label key={item.id} className="flex gap-2 py-1"><input type="checkbox" checked={selectedRefs.includes(item.id)} onChange={(event) => {
                setSelectedRefs((prior) => event.target.checked ? [...prior, item.id] : prior.filter((id) => id !== item.id));
                if (!event.target.checked) setSelectedEntities((prior) => prior.filter((id) => entities.find((entity) => entity.entity_id === id)?.document_id !== item.id));
              }} />{item.title} <span className="text-white/40">{item.current_revision_id.slice(0, 8)}</span></label>)}
              {!designDocuments.length && <p className="text-white/40">暂无设定文档；可以只交接本集正文。</p>}
            </div>
            {!!entities.length && <div className="rounded border border-white/10 p-3"><p className="mb-2 text-xs text-white/60">选择本次采用的实体和资产；空资产关联也可以交接。</p>
              {entities.filter((entity) => selectedRefs.includes(entity.document_id)).map((entity) => {
                const unavailable = entity.entry_missing || entity.stale || entity.asset_missing || entity.relations.some((relation) => relation.missing || relation.stale) || entity.appearances.some((appearance) => appearance.status === "written" && appearance.stale);
                return <label key={entity.entity_id} className="flex gap-2 py-1"><input type="checkbox" disabled={unavailable} checked={selectedEntities.includes(entity.entity_id)} onChange={(event) => setSelectedEntities((prior) => event.target.checked ? [...prior, entity.entity_id] : prior.filter((id) => id !== entity.entity_id))} />
                  {entity.name} · {entity.asset_name ?? "未关联资产"}{unavailable && <span className="text-amber-200">已失效或缺失，请先修复</span>}
                  {!!entity.relations.length && <span className="text-white/40">关联：{entity.relations.map((relation) => entities.find((entry) => entry.entity_id === relation.entity_id)?.name ?? relation.entity_id).join("、")}</span>}
                </label>;
              })}
              {relationErrors.map((reason) => <p key={reason} className="text-amber-200">{reason}</p>)}
            </div>}
            <fieldset className="rounded border border-white/10 p-3"><legend className="px-1 text-white/70">正文与场次校对</legend>
              <label className="mr-4"><input type="radio" name="handoff-scope" checked={scopeMode === "all"} onChange={() => setScopeMode("all")} /> 校对本集全部场次</label>
              <label className="mr-4"><input type="radio" name="handoff-scope" checked={scopeMode === "none"} onChange={() => setScopeMode("none")} /> 仅采用正文</label>
              <label><input type="radio" name="handoff-scope" checked={scopeMode === "selected"} onChange={() => setScopeMode("selected")} /> 仅校对所选场次</label>
              {scopeMode === "selected" && <div className="mt-2">{availableSceneIds.length ? availableSceneIds.map((id) => <label key={id} className="mr-3 inline-flex gap-1"><input type="checkbox" checked={sceneIds.includes(id)} onChange={(event) => setSceneIds((prior) => event.target.checked ? [...prior, id] : prior.filter((entry) => entry !== id))} />{id}</label>) : <p className="text-amber-200">先以“校对本集全部场次”预览，获取可选的解析场次；无法可靠解析时不可选择场次。</p>}</div>}
            </fieldset>
            <fieldset className="rounded border border-white/10 p-3"><legend className="px-1 text-white/70">事实关联确认</legend>
              {matchingRuns.length > 0 && <label className="block"><input type="radio" name="fact-mode" checked={factMode === "checked"} onChange={() => { setFactMode("checked"); setCheckedRunId(matchingRuns[0].id); }} /> 采用当前关联检查
                {factMode === "checked" && <select aria-label="关联检查记录" value={checkedRunId} onChange={(event) => setCheckedRunId(event.target.value)} className="ml-2 bg-[#1A1D22]">{matchingRuns.map((run) => <option key={run.id} value={run.id}>{run.id.slice(0, 8)}</option>)}</select>}</label>}
              <label className="block"><input type="radio" name="fact-mode" checked={factMode === "unchecked"} onChange={() => setFactMode("unchecked")} /> 明确说明未采用当前关联检查</label>
              {factMode === "unchecked" && <textarea aria-label="未运行关联检查的说明" value={uncheckedReason} onChange={(event) => setUncheckedReason(event.target.value)} placeholder="说明为何现在仍要交接" className="mt-2 w-full rounded border border-white/15 bg-[#0D0E10] p-2" />}
              {uniqueFacts.map((issue: ConsistencyIssue) => <label key={issue.id} className="mt-2 block border-t border-white/10 pt-2">已知事实问题：{issue.explanation}
                <input aria-label={`事实问题 ${issue.id} 的处理说明`} value={issue.intentional_reason || issueReasons[issue.id] || ""} readOnly={!!issue.intentional_reason} onChange={(event) => setIssueReasons((prior) => ({ ...prior, [issue.id]: event.target.value }))} placeholder="逐项说明处理原因" className="mt-1 w-full rounded border border-white/15 bg-[#0D0E10] p-2" />
              </label>)}
              <p className="mt-2 text-xs text-white/40">创意建议仅供参考，不阻止交接。</p>
            </fieldset>
            <button disabled={!valid || busy} onClick={() => void preview()} className="rounded bg-[#E5FF5C] px-4 py-2 font-semibold text-black disabled:opacity-40">{busy ? "正在准备…" : "预览交接差异"}</button>
          </section>}
          {activePreview && <section className="mt-5 space-y-3 border-t border-white/15 pt-5">
            <h3 className="font-semibold">冻结快照与差异 · {statusName[activePreview.status]}</h3>
            <p className="text-xs text-white/55">正文版本 {activePreview.snapshot.revision_id.slice(0, 8)}；参考文档 {activePreview.snapshot.references.map((ref) => `${ref.title} (${ref.revision_id.slice(0, 8)})`).join("、") || "无"}；实体 {activePreview.snapshot.entities.map((entity) => `${entity.name} (${entity.asset_name ?? "未关联资产"})`).join("、") || "无"}</p>
            <details><summary className="cursor-pointer text-[#E5FF5C]">查看冻结的完整正文</summary><pre className="mt-2 max-h-52 overflow-auto whitespace-pre-wrap rounded bg-[#0D0E10] p-3 text-xs">{activePreview.snapshot.markdown}</pre></details>
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="rounded border border-white/10 p-3"><h4>实际文本修改</h4>{activePreview.diff.text.length ? activePreview.diff.text.map((change, index) => <p key={index} className="mt-1 text-xs">{change.operation} · 原行 {change.old_lines.join("-")} → 新行 {change.new_lines.join("-")}：{change.after.join(" ") || "删除内容"}</p>) : <p className="text-xs text-white/45">正文相同</p>}</div>
              <div className="rounded border border-white/10 p-3"><h4>实际场次修改</h4>{activePreview.diff.scenes.map((change, index) => <p key={index} className="text-xs">{change.operation}：{change.old_scene_ids.join("、") || "无"} → {change.new_scene_ids.join("、") || "无"}</p>)}{!activePreview.diff.scenes.length && <p className="text-xs text-white/45">无</p>}</div>
              <div className="rounded border border-white/10 p-3"><h4>实际对白修改</h4>{activePreview.diff.dialogue.map((change, index) => <p key={index} className="text-xs">{change.operation}：{change.before.join("、") || "无"} → {change.after.join("、") || "无"}</p>)}{!activePreview.diff.dialogue.length && <p className="text-xs text-white/45">无</p>}</div>
              <div className="rounded border border-white/10 p-3"><h4>参考与资产变更</h4>{activePreview.diff.references.map((change) => <p key={change.document_id} className="text-xs">文档 {change.document_id}：{change.operation} {change.before_revision ?? "无"} → {change.after_revision ?? "无"}</p>)}{activePreview.diff.entity_references.map((change) => <p key={change.entity_id} className="text-xs">实体 {change.entity_id}：{change.operation} {change.before_asset_id ?? "无"} → {change.after_asset_id ?? "无"}</p>)}</div>
            </div>
            {activePreview.diff.needs_reparse && <p className="text-amber-200">场次无法可靠复用，需要重新解析；不可只选部分场次。</p>}
            {!!activePreview.diff.inferred_impacts.length && <p className="text-amber-200">可能受影响，需校对确认：{activePreview.diff.inferred_impacts.join("、")}（根据差异规则推断）</p>}
            {!!activePreview.diff.reused_scenes.length && <p className="text-xs text-white/60">未变化场次沿用原来源版本：{activePreview.diff.reused_scenes.map((scene) => `${scene.new_scene_id} ← ${scene.source_revision ?? "无"}`).join("、")}</p>}
            {!!activePreview.diff.affected_nonupdated_scene_ids.length && <p className="text-amber-200">改动但未校对的场次保持旧阶段结果：{activePreview.diff.affected_nonupdated_scene_ids.join("、")}</p>}
            {!!Object.keys(activePreview.snapshot.previous_stage_revisions).length && <p className="text-xs text-white/60">已有阶段：{Object.entries(activePreview.snapshot.previous_stage_revisions).map(([stage, value]) => `${stage} ${value.stale ? "已失效" : "待核对"}`).join("、")}</p>}
            {activePreview.status === "prepared" && <button disabled={!canConfirm || busy} onClick={() => void confirm()} className="rounded bg-[#E5FF5C] px-4 py-2 font-semibold text-black disabled:opacity-40">确认本集并交接制作</button>}
            {activePreview.status === "needs_rebase" && <p className="text-amber-200">源版本已变化，请返回新交接，重新预览当前版本。</p>}
            {activePreview.status === "completed" && <a className="text-[#E5FF5C] underline" href={`/projects/${encodeURIComponent(project)}/episodes/${activePreview.episode_number}/script?creationDocument=${encodeURIComponent(document.id)}`}>前往本集制作剧本</a>}
          </section>}
          <section className="mt-6 border-t border-white/10 pt-4"><h3 className="mb-2 font-semibold">本集交接记录</h3>
            {!history.length && <p className="text-xs text-white/45">暂无记录</p>}
            {history.map((record) => <div key={record.id} className="mb-2 flex flex-wrap items-center gap-2 rounded border border-white/10 p-2 text-xs">
              <span>{record.revision_id.slice(0, 8)} · {statusName[record.status]}</span><button onClick={() => openHistory(record)} className="text-[#E5FF5C]">查看快照</button>
              {(record.status === "failed" || record.status === "source_written") && <button disabled={busy} onClick={() => void retry(record)} className="text-[#E5FF5C]">重试原交接</button>}
              {record.status === "needs_rebase" && <span className="text-amber-200">请根据当前版本重新准备</span>}
              {record.error && <span className="text-amber-200">{record.error}</span>}
            </div>)}
          </section>
        </>}
      </div>
    </div>
  </div>;
}
