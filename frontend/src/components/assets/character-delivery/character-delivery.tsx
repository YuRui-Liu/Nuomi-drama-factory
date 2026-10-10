import { useEffect, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { useCharacterCasting, useCastingCandidates, useCastingAction, castingTaskActive } from '@/lib/queries/character-casting';
import { useCreateIdentity } from '@/lib/queries/characters';
import { resolveMediaUrl } from '@/lib/media-url';
import type { CastingCandidate, CastingProposal } from '@/types/character-casting';
import { AvatarCropDialog } from './avatar-crop-dialog';
import { useCharacterDraft, useCharacterSwitch } from '@/features/studios/character-draft-bridge';

const field = 'w-full rounded-md border border-border bg-background p-2 text-sm';
const panel = 'rounded-lg border border-border bg-card/40 p-4 space-y-4';
const blockedCopy: Record<string, string> = { avatar_missing: '请框选头像后采用', review_not_completed: '等待设定图检查完成', sheet_review_failed: '检查发现问题，请提出修改', generation_not_succeeded: '图片尚未生成成功' };
function readLocalDraft(key: string): Record<string,string> {
  try { return JSON.parse(sessionStorage.getItem(key) || '{}') as Record<string,string>; } catch { return {}; }
}
function writeLocalDraft(key: string, value: Record<string,string>) {
  try { sessionStorage.setItem(key,JSON.stringify(value)); } catch { /* Private browsing may disable storage. */ }
}

function editProposal(proposal: CastingProposal, attribute: 'hair_style' | 'body_type' | 'face_shape' | 'outfit_states', value: string): CastingProposal {
  return { ...proposal, [attribute]: attribute === 'outfit_states' ? { default: value } : value,
    casting_decisions: [...proposal.casting_decisions.filter(d => d.attribute !== attribute), {
      schema_version: 1, decision_id: `user-${attribute}`, attribute, value, reason: '用户微调确认', basis: 'creative_choice', fact_ids: [],
    }],
  };
}

export function CharacterDelivery({ project, name, imageModel, identityId, onIdentityChange }: { project: string; name: string; imageModel?: string; identityId?: string | null; onIdentityChange?: (id: string | null) => void }) {
  const base = useCharacterCasting(project, name);
  const create = useCreateIdentity(project, name);
  const [localIdentity, setLocalIdentity] = useState<string | null>(null);
  const identity = identityId === undefined ? localIdentity : identityId;
  const setIdentity = onIdentityChange ?? setLocalIdentity;
  const draftKey=`character-variant/${project}/${name}`;
  const [adding, setAdding] = useState(()=>!!readLocalDraft(draftKey).name);
  const [variantName, setVariantName] = useState(()=>readLocalDraft(draftKey).name || '');
  const [description, setDescription] = useState(()=>readLocalDraft(draftKey).description || '');
  const [stage, setStage] = useState(()=>readLocalDraft(draftKey).stage || '');
  useEffect(()=>writeLocalDraft(draftKey,{name:variantName,description,stage}),[draftKey,variantName,description,stage]);
  const [error, setError] = useState('');
  const switching = useCharacterSwitch();
  return <div className="space-y-4">
    <div className="flex flex-wrap gap-2" aria-label="角色形象与变体">
      <Button variant={identity === null ? 'default' : 'outline'} onClick={() => switching.request(() => setIdentity(null))}>基础形象</Button>
      {(base.data?.data.identities ?? []).map(i => <Button key={i.identity_id} variant={identity === i.identity_id ? 'default' : 'outline'} onClick={() => switching.request(() => setIdentity(i.identity_id))}>{i.name}</Button>)}
      <Button variant="outline" disabled={!base.data?.data.can_edit} onClick={() => setAdding(!adding)}>＋ 新增变体</Button>
    </div>
    {adding && <section className={panel}><h3>新增变体</h3>
      <Input aria-label="变体名称" placeholder="例如：雨夜外出" value={variantName} onChange={e => setVariantName(e.target.value)} />
      <label className="block text-sm">年龄阶段（换装时保持不变）<select className={field} value={stage} onChange={e => setStage(e.target.value)}>
        <option value="">保持当前年龄</option><option value="child">幼年</option><option value="youth">青年</option><option value="middle">中年</option><option value="elder">老年</option>
      </select></label>
      <textarea className={field} aria-label="变体要求" placeholder="描述服装、剧情状态或身份变化" value={description} onChange={e => setDescription(e.target.value)} />
      {error && <p role="alert">{error}</p>}
      <Button disabled={!variantName.trim() || create.isPending} onClick={async () => {
        try { const result = await create.mutateAsync({ identity_name: variantName.trim(), appearance_details: description, ...(stage ? { age_group: stage } : {}) });
          await base.refetch(); setIdentity(result.data.identity_id); setAdding(false); setVariantName(''); setDescription(''); setStage(''); }
        catch (e) { setError(e instanceof Error ? e.message : '创建失败'); }
      }}>创建变体</Button>
    </section>}
    <DeliveryStage key={`${name}/${identity}`} project={project} name={name} identity={identity} imageModel={imageModel} />
    {switching.dialog}
  </div>;
}

function DeliveryStage({ project, name, identity, imageModel }: {project: string; name: string; identity: string | null; imageModel?: string}) {
  const workspace = useCharacterCasting(project, name, identity);
  const candidates = useCastingCandidates(project, name, identity);
  const action = useCastingAction(project, name, identity);
  const data = workspace.data?.data;
  const [selected, setSelected] = useState<CastingProposal | null>(null);
  const [editing, setEditing] = useState(false);
  const [error, setError] = useState('');
  const [crop, setCrop] = useState<CastingCandidate | null>(null);
  const [history, setHistory] = useState(false);
  const [modifying, setModifying] = useState<CastingCandidate | null>(null);
  const feedbackKey=`character-feedback/${project}/${name}/${identity}`;
  const [feedback, setFeedback] = useState(()=>readLocalDraft(feedbackKey).text || '');
  useEffect(()=>{
    const saved=readLocalDraft(feedbackKey);
    const source=candidates.data?.data.find(c=>c.candidate_id===saved.candidateId);
    if(source) setModifying(source);
  },[feedbackKey,candidates.data]);
  const dirty = !!selected && JSON.stringify(selected) !== JSON.stringify(data?.proposals?.find(p => p.proposal_id === selected.proposal_id));
  const saveDraft = async () => {
    if (!data?.revision || !selected) return undefined;
    return action.mutateAsync({kind:'draft',body:{expected_revision:data.revision.revision_id,
      selected_proposal_id:selected.proposal_id,proposals:data.proposals.map(p=>p.proposal_id===selected.proposal_id?selected:p)}});
  };
  useCharacterDraft(`${project}/${name}/${identity}/delivery`, dirty, () => {
    void saveDraft().catch(e=>{setError(e instanceof Error?e.message:'保存失败');window.dispatchEvent(new Event('studio-save-failed-character'));});
  });
  const tasks = data?.tasks ?? [];
  const running = tasks.some(castingTaskActive);
  useEffect(() => { if (running) { const timer = setInterval(() => void candidates.refetch(), 2000); return () => clearInterval(timer); } }, [running, candidates.refetch]);
  if (!data) return <p className="text-sm text-muted-foreground">{workspace.error ? '角色资料加载失败，请重试。' : '正在加载角色形象…'}</p>;
  const revision = data.revision?.revision_id;
  const all = candidates.data?.data ?? [];
  const currentIndex = all.findIndex(c => c.candidate_id === data.current?.candidate_id);
  const currentCandidate = currentIndex >= 0 ? all[currentIndex] : undefined;
  const latest = [...all.slice(currentIndex + 1)].reverse().find(c => !c.repair_child_id && !c.stale);
  const currentUrl = data.current?.sheet_url || data.current?.url || data.legacy_current?.url;
  const showPlans = editing || (!currentUrl && !latest && !running);
  const run = async (command: Parameters<typeof action.mutateAsync>[0]) => { setError(''); try { await action.mutateAsync(command); return true; } catch(e) { setError(e instanceof Error ? e.message : '操作失败'); return false; } };
  const picture = (url: string, alt: string) => <img src={resolveMediaUrl(url) ?? undefined} alt={alt} className="max-h-[480px] w-full rounded-md object-contain" />;
  const adopt = (candidate: CastingCandidate, version = candidate.asset_version) => action.mutateAsync({kind:'adopt',candidateId:candidate.candidate_id,body:{candidate_id:candidate.candidate_id,expected_revision:revision!,idempotency_key:crypto.randomUUID(),acknowledged_findings:[],override_reason:candidate.adoption_requirements.override_reason_required?'用户确认当前效果可用，人工通过并采用':null,expected_review_attempt_id:candidate.review_attempt_id,expected_asset_version:version}});
  return <div className="space-y-4">
    {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
    {running && <div className="rounded-md border border-border bg-card px-4 py-3 text-sm" role="status">正在制作与检查，可以继续处理其他角色。</div>}
    {!running && tasks.slice(-1).filter(t => t.error).map(t => <p key={t.request_id} role="alert" className="text-sm text-warning">{typeof t.error === 'string' ? t.error : t.error?.message}</p>)}
    {currentUrl && (showPlans || latest ? <details className={panel}><summary className="cursor-pointer text-sm">当前已采用形象 · 新版采用前继续使用</summary>{picture(currentUrl, '当前已采用设定图')}</details> :
      <section className={panel}><div className="flex flex-wrap items-center justify-between gap-2"><h3 className="font-medium">当前形象</h3><div className="flex gap-2"><Button variant="outline" disabled={!data.can_edit || running} onClick={() => setEditing(true)}>重新选方案</Button>{currentCandidate && <Button variant="outline" disabled={!data.can_edit || running || currentCandidate.stale} onClick={()=>setModifying(currentCandidate)}>提出修改</Button>}</div></div>
        <div className="grid gap-4 md:grid-cols-[minmax(0,1fr)_128px]">{picture(currentUrl, '当前角色形象')}{data.current?.sheet_url && <div className="space-y-2">{picture(data.current.url, '当前角色头像')}<p className="text-xs text-muted-foreground">头像从设定图裁切</p>{currentCandidate && <Button variant="outline" disabled={!data.can_edit || currentCandidate.stale || running} onClick={()=>setCrop(currentCandidate)}>调整裁切</Button>}</div>}</div>
      </section>)}
    {showPlans && <section className={panel}>
      <div className="flex flex-wrap items-center justify-between gap-3"><h3 className="font-medium">选择造型方向</h3><Button variant="outline" disabled={!data.can_edit || action.isPending || running} onClick={() => void run({kind:'recast',body:{idempotency_key:crypto.randomUUID(),expected_revision:revision ?? null}})}>{data.draft_stale?'更新方案':'生成三套方案'}</Button></div>
      {data.draft_stale && <p className="text-sm text-muted-foreground">角色资料或画风有更新。可以继续使用当前方案，也可以选择更新。</p>}
      {data.prerequisite_error && <p className="text-sm text-warning">{data.prerequisite_error}</p>}
      <div className="grid items-stretch gap-3 lg:grid-cols-3">{(data.proposals ?? []).map(p => <div key={p.proposal_id} className={`flex flex-col rounded-lg border ${ (selected?.proposal_id ?? data.selected_proposal_id)===p.proposal_id?'border-primary bg-primary/5':'border-border'}`}>
        <button disabled={!data.can_edit} aria-pressed={(selected?.proposal_id ?? data.selected_proposal_id)===p.proposal_id} className="flex w-full flex-1 flex-col items-stretch justify-start gap-3 p-4 text-left disabled:cursor-default" onClick={() => setSelected(structuredClone(p))}>
          <h4 className="font-semibold">{p.title}</h4><p className="text-sm text-muted-foreground"><span className="text-foreground">面貌 · </span>{p.face_shape || '待补充面貌描述'}</p>
          <p className="text-sm text-muted-foreground"><span className="text-foreground">发型 · </span>{p.hair_style}</p><p className="text-sm text-muted-foreground"><span className="text-foreground">服装 · </span>{Object.values(p.outfit_states ?? {}).join('；')}</p>
        </button><details className="px-4 pb-4 text-xs text-muted-foreground"><summary className="cursor-pointer">设计依据</summary><p className="mt-2 leading-relaxed">{p.rationale}</p></details>
      </div>)}</div>
      {selected && <div className="space-y-3 border-t border-border pt-4"><h4>微调：{selected.title}</h4>
        <label className="block text-sm">脸型<Input value={selected.face_shape ?? ''} onChange={e => setSelected(editProposal(selected,'face_shape',e.target.value))} /></label>
        <label className="block text-sm">发型<Input value={selected.hair_style ?? ''} onChange={e => setSelected(editProposal(selected,'hair_style',e.target.value))} /></label>
        <label className="block text-sm">体型<Input value={selected.body_type ?? ''} onChange={e => setSelected(editProposal(selected,'body_type',e.target.value))} /></label>
        <label className="block text-sm">服装与状态<textarea className={field} value={selected.outfit_states?.default ?? Object.values(selected.outfit_states ?? {}).join('；')} onChange={e => setSelected(editProposal(selected,'outfit_states',e.target.value))} /></label>
        <Button variant="outline" disabled={action.isPending || !revision} onClick={() => { void saveDraft().catch(e=>setError(e instanceof Error?e.message:'保存失败')); }}>保存方案</Button>
      </div>}
      <div className="flex justify-end"><Button disabled={!data.can_edit || !(selected || data.selected_proposal_id) || !revision || action.isPending || running} onClick={async()=>{
        try {
          let confirmedRevision = revision!;
          if (selected) { const saved = await saveDraft(); const value = saved?.data as {revision?:{revision_id:string}} | undefined;
            if (!value?.revision?.revision_id) throw new Error('方案保存未返回版本，请刷新后重试'); confirmedRevision=value.revision.revision_id; }
          if(await run({kind:'generate',body:{idempotency_key:crypto.randomUUID(),expected_revision:confirmedRevision,model:imageModel || undefined,sheet_contract:'character_sheet_v4',reuse_stale_draft:!!data.draft_stale}})){setEditing(false);setSelected(null);}
        } catch(e) {setError(e instanceof Error?e.message:'保存失败');}
      }}>确认方案并出图</Button></div>
    </section>}
    {latest && !running && <section className={panel}><h3 className="font-medium">{latest.generation_status==='succeeded'?'新形象交付':'制作候选'}</h3>
      {latest.url && <div className="grid gap-4 md:grid-cols-[minmax(0,1fr)_128px]">{picture(latest.url,latest.sheet_contract?'候选设定图':'历史肖像候选')}{latest.sheet_contract && <div className="space-y-2">{latest.avatar_url && picture(latest.avatar_url,'派生头像')}<p className="text-xs text-muted-foreground">头像从原图裁切</p><Button variant="outline" disabled={!data.can_edit} onClick={()=>setCrop(latest)}>调整裁切</Button></div>}</div>}
      {latest.error && <p className="text-sm text-warning">{latest.error}</p>}
      {latest.sheet_quality?.issues.map((i,index)=><p key={index} className="text-sm text-warning">{i.region}：{i.problem}；{i.suggestion}</p>)}
      {latest.review_status==='running' && <p role="status">正在审核设定图…</p>}
      {latest.generation_status==='succeeded' && ['failed','not_started'].includes(latest.review_status) && <Button variant="outline" disabled={running || action.isPending || !data.can_edit} onClick={()=>void run({kind:'review',candidateId:latest.candidate_id,body:{idempotency_key:crypto.randomUUID()}})}>重新检查原图</Button>}
      {latest.adoption_requirements.blocked_reason && <p className="text-xs text-muted-foreground">{blockedCopy[latest.adoption_requirements.blocked_reason] ?? '暂时无法采用，请检查处理结果'}</p>}
      <div className="flex justify-end gap-2"><Button variant="outline" disabled={running || !data.can_edit || !latest.url} onClick={()=>setModifying(latest)}>提出修改</Button>
        <Button disabled={!data.can_edit || action.isPending || !!latest.adoption_requirements.blocked_reason} onClick={()=>{void adopt(latest).catch(e=>setError(e instanceof Error?e.message:'采用失败'));}}>{latest.adoption_requirements.override_reason_required?'人工通过并采用':'采用'}</Button></div>
      {latest.generation_status==='failed' && <Button variant="outline" disabled={!data.can_edit} onClick={()=>setEditing(true)}>检查方案并重新出图</Button>}
    </section>}
    {modifying && <section className={panel}><label className="block text-sm">修改意见<textarea className={field} value={feedback} onChange={e=>{setFeedback(e.target.value);writeLocalDraft(feedbackKey,{candidateId:modifying.candidate_id,text:e.target.value});}} placeholder="保留面容和发型，只调整外套…" /></label>
      <p className="text-xs text-muted-foreground">原图、确认方案、原资料和修复记录会一起带入。</p>
      <div className="flex justify-end gap-2"><Button variant="outline" onClick={()=>{setModifying(null);setFeedback('');writeLocalDraft(feedbackKey,{});}}>取消修改</Button><Button disabled={!feedback.trim() || action.isPending || running || !data.can_edit || modifying.stale} onClick={async()=>{
        if(await run({kind:'generate',body:{idempotency_key:crypto.randomUUID(),expected_revision:revision!,model:imageModel || undefined,
          sheet_contract:'character_sheet_v4',reuse_stale_draft:!!data.draft_stale,source_candidate_id:modifying.candidate_id,change_request:feedback.trim()}})){setModifying(null);setFeedback('');writeLocalDraft(feedbackKey,{});}
      }}>确认修改并出图</Button></div></section>}
    <details className="text-sm text-muted-foreground"><summary className="cursor-pointer">原始资料与设计依据</summary><p className="mt-2 whitespace-pre-wrap">{data.dossier?.narrative?.biography}</p>{data.dossier?.narrative?.authoring_context?.map((source,index)=><p key={index} className="my-2 whitespace-pre-wrap">{source.title && <strong>{source.title}：</strong>}{source.text}</p>)}{data.dossier?.hard_constraints.map(f=><blockquote key={f.fact_id} className="my-2 border-l border-border pl-3">{f.value} — {f.evidence}</blockquote>)}</details>
    <Button variant="ghost" onClick={()=>setHistory(!history)}>历史与修复记录</Button>
    {history && <div className={panel}>{!all.length && <p className="text-sm text-muted-foreground">暂无历史版本</p>}{all.map(c=><details key={c.candidate_id}><summary>{c.stale?'基于旧方案 · ':''}{c.snapshot.proposal_snapshot.title as string || c.candidate_id} · {{queued:'等待制作',running:'制作中',succeeded:'已出图',failed:'制作未完成'}[c.generation_status]}</summary>{c.url && picture(c.url,'历史候选')}
      {c.repair_history?.map((entry,index)=><div key={index} className="my-3 space-y-2 border-l border-border pl-3 text-sm"><p>第 {index+1} 次修改{entry.user_change_request ? `：${entry.user_change_request}` : ''}</p>{entry.issues?.issues?.map((issue,i)=><p key={i}>{issue.problem}；建议：{issue.suggestion}</p>)}{entry.changes?.map((change,i)=><p key={i}>{change}</p>)}</div>)}
      {c.report?.findings.map(f=><p key={f.finding_id} className="my-2 text-sm text-muted-foreground">{f.description}</p>)}
    </details>)}</div>}
    {crop && <AvatarCropDialog candidate={crop} busy={action.isPending} onClose={()=>setCrop(null)} onSave={async box=>{
      const result = await action.mutateAsync({kind:'crop',candidateId:crop.candidate_id,body:{crop_box:box,expected_version:crop.asset_version ?? 0}});
      if(crop.candidate_id===data.current?.candidate_id) await adopt(crop,(result.data as {asset_version:number}).asset_version);
    }} />}
  </div>;
}
