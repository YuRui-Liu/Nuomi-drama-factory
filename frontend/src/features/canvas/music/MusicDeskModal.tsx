import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { musicApi } from './api';
import type { MusicAsset, MusicClip, MusicJob, MusicPlan } from './types';
import { changeClip, duplicateClip, emptyPlan, formatTime, id, insertClip, newTrack, redo, remember, removeClip, splitClip, undo, type History } from './timeline';
import { MusicTimeline } from './MusicTimeline';
import { MusicPreview } from './MusicPreview';
import './music-desk.css';
interface Props {
    project: string;
    canvas: string;
    node: string;
    sourceUrl: string;
    onClose: () => void;
    onSaved: (sourceUrl: string) => void;
    onExport: (url: string) => void;
}
const message = (e: unknown) => e instanceof Error ? e.message : String(e);
const purposeTags: Record<string, string> = { clue: 'restrained cinematic suspense, sparse felt piano, low strings', conflict: 'rising tension, low percussion, controlled rhythmic pulse', reflection: 'gentle reflective underscore, soft piano, warm pads', bed: 'subtle atmospheric underscore, sparse arrangement' };
export function MusicDeskModal({ project, canvas, node, sourceUrl, onClose, onSaved, onExport }: Props) {
    const [history, setHistory] = useState<History | null>(null), [library, setLibrary] = useState<MusicAsset[]>([]), [favorites, setFavorites] = useState<MusicAsset[]>([]), [jobs, setJobs] = useState<MusicJob[]>([]);
    const [tab, setTab] = useState('个人音乐'), [query, setQuery] = useState(''), [trackId, setTrackId] = useState(''), [clipId, setClipId] = useState(''), [range, setRange] = useState<[
        number,
        number
    ]>([0, 0]);
    const [busy, setBusy] = useState(false), [error, setError] = useState(''), [notice, setNotice] = useState(''), [dirty, setDirty] = useState(false), [closeAsk, setCloseAsk] = useState(false);
    const [pending, setPending] = useState<MusicClip | null>(null), [audition, setAudition] = useState<MusicAsset | null>(null), [generation, setGeneration] = useState(false), [caps, setCaps] = useState<{
        generation: boolean;
        reason?: string;
        workflowId: string;
    }>({ generation: false, workflowId: '2059090557116440578' });
    const [tags, setTags] = useState('Instrumental, subtle atmospheric underscore, sparse arrangement, leave space for dialogue, no vocals'), [bpm, setBpm] = useState(75), [generateId, setGenerateId] = useState(id()), [gainPurpose, setPurpose] = useState('bed');
    const [preview, setPreview] = useState<{
        url: string;
        fingerprint: string;
    } | null>(null), [waiting, setWaiting] = useState<{
        id: string;
        fingerprint: string;
        preview: boolean;
    } | null>(null), [playhead, setPlayhead] = useState(0), [editedAsset, setEditedAsset] = useState<MusicAsset | null>(null);
    const [savedAssets, setSavedAssets] = useState<MusicAsset[]>([]), [generateSeed, setGenerateSeed] = useState('0');
    const current = useRef<MusicPlan | null>(null);
    current.current = history?.present || null;
    const plan = history?.present;
    const fingerprint = plan ? JSON.stringify({ ...plan, revision: 0 }) : '';
    const allAssets = [...savedAssets, ...library, ...favorites, ...jobs.flatMap(j => j.candidate ? [j.candidate] : [])];
    const names = Object.fromEntries(allAssets.map(a => [a.versionId, a.name]));
    const selected = plan?.tracks.flatMap(t => t.clips).find(c => c.id === clipId);
    const attempt = async (fn: () => Promise<void>) => { setBusy(true); setError(''); try {
        await fn();
    }
    catch (e) {
        setError(message(e));
    }
    finally {
        setBusy(false);
    } };
    function edit(next: MusicPlan) { setHistory(h => h ? remember(h, next) : h); setDirty(true); setNotice(''); }
    function operate(fn: () => MusicPlan) { try {
        edit(fn());
        setError('');
    }
    catch (e) {
        setError(message(e));
    } }
    async function refresh() { const [a, b, c] = await Promise.all([musicApi.library(), musicApi.favorites(project), musicApi.jobs(project)]); setLibrary(a); setFavorites(b); setJobs(c); }
    useEffect(() => { let alive = true; void (async () => { try {
        const [{ plan: saved, assets }, a, b, c, cap] = await Promise.all([musicApi.load(project, canvas, node), musicApi.library(), musicApi.favorites(project), musicApi.jobs(project), musicApi.capabilities(project)]);
        let initial = saved;
        if (!initial) {
            const source = await musicApi.source(project, sourceUrl);
            initial = emptyPlan({ assetVersionId: source.versionId, sha256: source.sha256!, durationMs: source.durationMs });
        }
        if (!alive)
            return;
        setHistory({ past: [], present: initial, future: [] });
        setTrackId(initial.tracks[0]?.id || '');
        setRange([0, initial.source.durationMs]);
        setSavedAssets(assets || []);
        setLibrary(a);
        setFavorites(b);
        setJobs(c);
        setCaps(cap);
    }
    catch (e) {
        if (alive)
            setError(message(e));
    } })(); return () => { alive = false; }; }, [project, canvas, node, sourceUrl]);
    useEffect(() => { const timer = setInterval(() => { void musicApi.jobs(project).then(setJobs).catch(() => { }); }, 3000); return () => clearInterval(timer); }, [project]);
    useEffect(() => { if (!waiting)
        return; const job = jobs.find(j => j.id === waiting.id); if (!job)
        return; if (job.status === 'completed') {
        if (job.audioUrl)
            setPreview({ url: job.audioUrl, fingerprint: waiting.fingerprint });
        if (!waiting.preview && job.videoUrl) {
            onExport(job.videoUrl);
            setNotice('配乐版已导出，原片已保留');
        }
        setWaiting(null);
    }
    else if (['failed', 'submission_unknown', 'dispatch_unknown', 'cancelled'].includes(job.status)) {
        setError(job.error || '任务未完成');
        setWaiting(null);
    } }, [jobs, waiting, onExport]);
    useEffect(() => { const before = (event: BeforeUnloadEvent) => { if (dirty) {
        event.preventDefault();
        event.returnValue = '';
    } }; window.addEventListener('beforeunload', before); return () => window.removeEventListener('beforeunload', before); }, [dirty]);
    async function save() { if (!current.current)
        return; const frozen = current.current; const saved = await musicApi.save(project, canvas, node, frozen); setHistory(h => h ? { ...h, present: { ...h.present, revision: saved.revision } } : h); if (current.current === frozen)
        setDirty(false); setNotice('已保存'); if (frozen.revision === 0)
        onSaved(sourceUrl); }
    function adopt(asset: MusicAsset) { if (!plan)
        return; const length = range[1] - range[0]; if (length <= 0) {
        setError('请选择有效范围');
        return;
    } if (length > asset.durationMs) {
        setError('音乐长度不足，请缩短选区；采用后可设置循环区间延长');
        return;
    } const clip: MusicClip = { id: id(), assetVersionId: asset.versionId, startMs: range[0], sourceInMs: 0, lengthMs: length, gainDb: -18, fadeInMs: Math.min(1000, Math.floor(length / 2)), fadeOutMs: Math.min(1000, Math.floor(length / 2)), loop: null }; try {
        edit(insertClip(plan, trackId, clip, 'ask'));
        setClipId(clip.id);
    }
    catch {
        setPending(clip);
    } }
    function patchClip(p: Partial<MusicClip>) { if (!plan || !selected)
        return; const next = { ...selected, ...p }; const asset = allAssets.find(a => a.versionId === selected.assetVersionId); if (asset && ((!next.loop && next.sourceInMs + next.lengthMs > asset.durationMs) || (next.loop && next.loop.endMs > asset.durationMs))) {
        setError('裁剪超出音乐长度');
        return;
    } operate(() => changeClip(plan, selected.id, p)); }
    const candidates = jobs.flatMap(j => j.candidate ? [j.candidate] : []);
    const shown = (tab === '个人音乐' ? library : tab === '项目收藏' ? favorites : candidates).filter(a => (a.name + ' ' + a.tags.join(' ') + ' ' + (a.description || '')).toLowerCase().includes(query.toLowerCase()));
    const sourceMedia = plan ? `/api/v1/projects/${encodeURIComponent(project)}/music/versions/${plan.source.assetVersionId}/media` : '';
    return createPortal(<div className="md-backdrop nodrag nopan nowheel" onPointerDown={e => e.stopPropagation()} onKeyDown={e => e.stopPropagation()}><div className="music-desk" role="dialog" aria-modal="true" aria-label="配乐台">
 <header><div><h2>♫ 配乐台</h2><small>成片后期 · 音乐库优先 · 多轨编排</small></div><div className="md-row"><span className="md-notice" role="status">{dirty ? '有未保存修改' : notice}</span><button disabled={busy || !plan} onClick={() => void attempt(save)}>保存草稿</button><button disabled={busy || !plan || !!waiting} onClick={() => void attempt(async () => { if (!plan)
        return; await save(); const job = await musicApi.render(project, id(), plan, false); setWaiting({ id: job.id, fingerprint, preview: false }); setNotice('正在导出新版本'); })} className="primary">导出配乐版</button><button aria-label="关闭配乐台" onClick={() => dirty ? setCloseAsk(true) : onClose()}>×</button></div></header>
 {error && <div className="md-error" role="alert">{error}<button onClick={() => setError('')}>关闭提示</button></div>}
 {!plan ? <div className="md-loading">{error ? '请关闭后重试，原始数据未修改' : '正在读取成片与配乐草稿…'}</div> : <>
 <div className="md-main"><aside className="md-library"><div className="md-row"><h3>音乐库</h3><label className="md-upload">＋ 上传<input type="file" accept="audio/*" disabled={busy} onChange={e => { const file = e.target.files?.[0]; if (file)
            void attempt(async () => { await musicApi.upload(file); await refresh(); }); e.target.value = ''; }}/></label></div><input aria-label="搜索音乐" placeholder="名称、情绪、乐器" value={query} onChange={e => setQuery(e.target.value)}/><nav>{['个人音乐', '项目收藏', '候选'].map(t => <button key={t} className={tab === t ? 'active' : ''} onClick={() => setTab(t)}>{t}</button>)}</nav>
 <div className="md-library-list">{shown.map(a => <article key={a.versionId}><strong>{a.name}</strong><small>{formatTime(a.durationMs)} · {a.vocals === 'none' ? '已标注无人声' : '人声状态待试听'}</small><p>{a.tags.join(' · ') || a.description || '添加标签方便下次检索'}</p><div className="md-row"><button onClick={() => setAudition(a)}>试听</button><button onClick={() => adopt(a)} disabled={!trackId}>放入选区</button><button aria-label={`编辑 ${a.name}`} onClick={() => setEditedAsset(a)}>⋯</button></div><div className="md-row"><button disabled={busy} onClick={() => void attempt(async () => { await musicApi.favorite(project, a.versionId); await refresh(); })}>收藏到项目</button>{tab === '候选' && <button disabled={busy} onClick={() => void attempt(async () => { await musicApi.patch(a, { library: true }); await refresh(); })}>存入个人库</button>}</div></article>)}{!shown.length && <p className="md-empty">这里还没有音乐。上传已有配乐，或手动生成候选。</p>}</div>
 {tab === '候选' && jobs.filter(j => j.kind === 'generate' && j.status !== 'completed').map(j => <article key={j.id}><small>配乐任务 {j.id.slice(0, 6)} · {({ queued: '排队中', running: '生成中', paused: '查询已暂停', failed: '失败', submission_unknown: '提交待核对', dispatch_unknown: '派发待核对' } as Record<string, string>)[j.status] || j.status}</small>{j.error && <p>{j.error}</p>}{j.status === 'paused' && j.remoteTaskId && <button disabled={busy} onClick={() => void attempt(async () => { await musicApi.resume(project, j.id); await refresh(); })}>恢复查询（不重新生成）</button>}</article>)}
 {audition && <div className="md-audition"><small>{audition.name}</small><audio controls src={audition.url}/></div>}<button disabled={!caps.generation || busy} onClick={() => { setGenerateId(id()); setGenerateSeed(String(Math.floor(Math.random() * Number.MAX_SAFE_INTEGER))); setGeneration(true); }}>生成配乐</button>{!caps.generation && <small className="md-warning">{caps.reason || '生成配置尚未就绪'}</small>}
 </aside><MusicPreview videoUrl={sourceMedia} audioUrl={preview?.url} stale={Boolean(preview && preview.fingerprint !== fingerprint)} onTime={setPlayhead}/>
 <aside className="md-inspector"><h3>{selected ? '片段属性' : '配乐范围'}</h3><div className="md-row"><button onClick={() => { setRange([0, plan.source.durationMs]); setClipId(''); }}>全局配乐</button><button onClick={() => setClipId('')}>自由选段</button></div><label>起点 / 终点（秒）</label><div className="md-row"><input aria-label="选区起点" type="number" min="0" step="0.1" value={range[0] / 1000} onChange={e => setRange([Math.max(0, Math.min(range[1] - 1, Math.round(Number(e.target.value) * 1000))), range[1]])}/><input aria-label="选区终点" type="number" step="0.1" max={plan.source.durationMs / 1000} value={range[1] / 1000} onChange={e => setRange([range[0], Math.max(range[0] + 1, Math.min(plan.source.durationMs, Math.round(Number(e.target.value) * 1000)))])}/></div><small>{formatTime(range[1] - range[0])} · 目标：{plan.tracks.find(t => t.id === trackId)?.name || '请选择轨道'}</small>
 {selected ? <><label>时间线起点 / 源音乐入点（秒）</label><div className="md-row"><input aria-label="片段起点" type="number" step="0.1" value={selected.startMs / 1000} onChange={e => patchClip({ startMs: Math.round(Number(e.target.value) * 1000) })}/><input aria-label="源音乐入点" type="number" step="0.1" value={selected.sourceInMs / 1000} onChange={e => patchClip({ sourceInMs: Math.round(Number(e.target.value) * 1000) })}/></div><label>片段时长（秒）</label><input aria-label="片段时长" type="number" step="0.1" value={selected.lengthMs / 1000} onChange={e => patchClip({ lengthMs: Math.round(Number(e.target.value) * 1000) })}/><label>片段音量 {selected.gainDb} dB</label><input aria-label="片段音量" type="range" min="-60" max="6" value={selected.gainDb} onChange={e => patchClip({ gainDb: Number(e.target.value) })}/><label>淡入 / 淡出（秒）</label><div className="md-row"><input aria-label="淡入" type="number" min="0" step="0.1" value={selected.fadeInMs / 1000} onChange={e => patchClip({ fadeInMs: Math.round(Number(e.target.value) * 1000) })}/><input aria-label="淡出" type="number" min="0" step="0.1" value={selected.fadeOutMs / 1000} onChange={e => patchClip({ fadeOutMs: Math.round(Number(e.target.value) * 1000) })}/></div><label><input type="checkbox" checked={!!selected.loop} onChange={e => patchClip({ loop: e.target.checked ? { startMs: selected.sourceInMs, endMs: Math.min(selected.sourceInMs + selected.lengthMs, allAssets.find(a => a.versionId === selected.assetVersionId)?.durationMs || selected.sourceInMs + selected.lengthMs) } : null })}/> 循环已选源区间</label>{selected.loop && <div className="md-row"><input aria-label="循环起点" type="number" step="0.1" value={selected.loop.startMs / 1000} onChange={e => patchClip({ loop: { ...selected.loop!, startMs: Math.round(Number(e.target.value) * 1000) } })}/><input aria-label="循环终点" type="number" step="0.1" value={selected.loop.endMs / 1000} onChange={e => patchClip({ loop: { ...selected.loop!, endMs: Math.round(Number(e.target.value) * 1000) } })}/></div>}<div className="md-row"><button onClick={() => operate(() => splitClip(plan, selected.id, Math.round(playhead)))}>播放头处分割</button><button onClick={() => operate(() => duplicateClip(plan, selected.id))}>复制到新轨</button><button onClick={() => { edit(removeClip(plan, selected.id)); setClipId(''); }}>删除</button></div></> : <><label>剧情用途</label><select value={gainPurpose} onChange={e => { setPurpose(e.target.value); setTags('Instrumental, ' + purposeTags[e.target.value] + ', leave space for dialogue, no vocals'); }}><option value="bed">整集氛围 · 铺底</option><option value="clue">发现线索 · 悬疑</option><option value="conflict">冲突升级 · 紧张</option><option value="reflection">情感回落 · 留白</option></select><p className="md-help">先选轨道，再从音乐库放入选区。音乐不够长时先放入较短范围，再设置循环片段。</p></>}
 <hr /><label><input type="checkbox" checked={plan.original.muted} onChange={e => edit({ ...plan, original: { ...plan.original, muted: e.target.checked } })}/> 静音原声</label><label>原声音量 {plan.original.gainDb} dB</label><input aria-label="原声音量" type="range" min="-60" max="6" value={plan.original.gainDb} onChange={e => edit({ ...plan, original: { ...plan.original, gainDb: Number(e.target.value) } })}/><label><input type="checkbox" checked={plan.ducking.enabled} onChange={e => edit({ ...plan, ducking: { ...plan.ducking, enabled: e.target.checked } })}/> 对白区间降低配乐</label>{plan.ducking.enabled && <><button onClick={() => edit({ ...plan, ducking: { ...plan.ducking, intervals: [...plan.ducking.intervals, { startMs: range[0], endMs: range[1] }] } })}>将选区标记为对白</button><small>{plan.ducking.intervals.length} 个手工对白区间 · 降低 {Math.abs(plan.ducking.gainDb)} dB</small><button onClick={() => edit({ ...plan, ducking: { ...plan.ducking, intervals: [] } })}>清除对白区间</button></>}
 </aside></div>
 <div className="md-timeline-tools"><div className="md-row"><h3>配乐时间线</h3><button onClick={() => { const t = newTrack('配乐 ' + (plan.tracks.length + 1)); edit({ ...plan, tracks: [...plan.tracks, t] }); setTrackId(t.id); }}>新增配乐轨</button><button aria-label="撤销" disabled={!history.past.length} onClick={() => { setHistory(undo(history)); setDirty(true); }}>↶ 撤销</button><button aria-label="重做" disabled={!history.future.length} onClick={() => { setHistory(redo(history)); setDirty(true); }}>↷ 重做</button></div><button disabled={busy || !!waiting} onClick={() => void attempt(async () => { await save(); const job = await musicApi.render(project, id(), plan, true); setWaiting({ id: job.id, fingerprint, preview: true }); })}>{waiting ? '处理中…' : '更新混音试听'}</button></div>
 <MusicTimeline plan={plan} trackId={trackId} clipId={clipId} range={range} names={names} urls={Object.fromEntries(allAssets.map(a => [a.versionId, a.url]))} onRange={setRange} onTrack={setTrackId} onClip={setClipId} onChangeTrack={(id, p) => edit({ ...plan, tracks: plan.tracks.map(t => t.id === id ? { ...t, ...p } : t) })} onRemoveTrack={id => { edit({ ...plan, tracks: plan.tracks.filter(t => t.id !== id) }); if (trackId === id)
            setTrackId(plan.tracks.find(t => t.id !== id)?.id || ''); }} onMove={(id, ms) => operate(() => changeClip(plan, id, { startMs: ms }))}/>
 <footer>多轨可叠加 · 同轨替换需确认 · 原片始终保留 <span>{jobs.filter(j => ['queued', 'running'].includes(j.status)).length} 个任务处理中</span></footer>
 </>}
 {pending && plan && <div className="md-subdialog" role="alertdialog" aria-label="配乐重叠"><h3>当前轨道已有配乐</h3><p>替换只影响选区内的音乐；叠加会放入新的轨道。</p><div className="md-row"><button onClick={() => setPending(null)}>取消</button><button onClick={() => { operate(() => insertClip(plan, trackId, pending, 'replace')); setPending(null); }}>替换选区</button><button className="primary" onClick={() => { operate(() => insertClip(plan, trackId, pending, 'layer')); setPending(null); }}>新轨叠加</button></div></div>}
 {generation && <div className="md-subdialog" role="dialog" aria-label="生成纯音乐"><h3>为选区生成纯音乐</h3><p>{formatTime(range[0])} → {formatTime(range[1])} · 1 个候选</p><label>音乐描述（可修改）</label><textarea value={tags} onChange={e => setTags(e.target.value)}/><label>BPM</label><input type="number" min="30" max="300" value={bpm} onChange={e => setBpm(Number(e.target.value))}/><p className="md-help">RunningHub · 工作流 {caps.workflowId}<br />费用由服务商结算，当前未提供预估。生成后不会自动采用。</p><div className="md-row"><button disabled={busy} onClick={() => setGeneration(false)}>取消</button><button disabled={busy} className="primary" onClick={() => void attempt(async () => { await musicApi.generate(project, generateId, { tags, bpm, durationSeconds: (range[1] - range[0]) / 1000, seed: generateSeed }); setGeneration(false); setTab('候选'); await refresh(); })}>确认生成 1 个候选</button></div></div>}
 {editedAsset && <div className="md-subdialog" role="dialog" aria-label="编辑音乐"><h3>音乐资料</h3><label>名称</label><input value={editedAsset.name} onChange={e => setEditedAsset({ ...editedAsset, name: e.target.value })}/><label>标签（逗号分隔）</label><input value={editedAsset.tags.join(',')} onChange={e => setEditedAsset({ ...editedAsset, tags: e.target.value.split(',') })}/><label>人声标注</label><select value={editedAsset.vocals} onChange={e => setEditedAsset({ ...editedAsset, vocals: e.target.value })}><option value="unknown">待确认</option><option value="none">无人声</option><option value="present">有人声</option></select><div className="md-row"><button onClick={() => setEditedAsset(null)}>取消</button><button disabled={busy} onClick={() => void attempt(async () => { await musicApi.patch(editedAsset, { archived: true }); setEditedAsset(null); await refresh(); })}>归档</button><button className="primary" disabled={busy} onClick={() => void attempt(async () => { await musicApi.patch(editedAsset, { name: editedAsset.name, tags: editedAsset.tags.map(t => t.trim()).filter(Boolean), vocals: editedAsset.vocals }); setEditedAsset(null); await refresh(); })}>保存资料</button></div></div>}
 {closeAsk && <div className="md-subdialog" role="alertdialog"><h3>草稿尚未保存</h3><div className="md-row"><button onClick={() => setCloseAsk(false)}>继续编辑</button><button onClick={onClose}>放弃本次修改</button><button disabled={busy} className="primary" onClick={() => void attempt(async () => { await save(); onClose(); })}>保存并关闭</button></div></div>}
 </div></div>, document.body);
}
