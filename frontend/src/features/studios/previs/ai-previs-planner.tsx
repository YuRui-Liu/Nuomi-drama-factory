import { useEffect, useRef, useState } from 'react';
import { api } from '@/lib/api';
import { Button } from '@/components/ui/button';
import { SceneProposalEditor } from './proposal-editor';
import { validateScene, type Scene } from './model';

type Result = { request_id: string; status: string; task_id?: string; error?: string; document?: { data: Scene } };
export function AiPrevisPlanner({ project, scene, disabled, onAdopt }: { project: string; scene: Scene; disabled: boolean; onAdopt: (scene: Scene) => void }) {
  const key = `previs-plan:${project}:${scene.source ? `${scene.source.type}:${scene.source.id}` : 'direct'}`;
  const [saved] = useState(() => { try { const raw = localStorage.getItem(key); if (!raw) return { requestId: '', instruction: '' }; if (raw.startsWith('{')) return JSON.parse(raw) as { requestId: string; instruction: string }; return { requestId: raw, instruction: '' }; } catch { return { requestId: '', instruction: '' }; } });
  const [requestId, setRequestId] = useState(saved.requestId);
  const [instruction, setInstruction] = useState(saved.instruction), [status, setStatus] = useState(''), [error, setError] = useState('');
  const [proposal, setProposal] = useState<Scene | null>(null), [busy, setBusy] = useState(false);
  const baseline = useRef(scene), editPending = useRef(false);
  const base = `api/v1/projects/${encodeURIComponent(project)}/studios/previs-tools/plans`;
  const applyResult = (result: Result) => { setStatus(result.status); if (result.error) setError(result.error); if (result.document && !editPending.current) { setProposal(result.document.data); editPending.current = true; } };
  useEffect(() => {
    if (!requestId) return;
    let stopped = false, timer: ReturnType<typeof setTimeout>;
    const poll = async () => { try { const { data } = await api.get(`${base}/${requestId}`).json<{ data: Result }>(); if (stopped) return; applyResult(data); if (['queued', 'pending', 'running', 'retrying'].includes(data.status)) timer = setTimeout(() => void poll(), 2500); } catch (e) { if (!stopped) setError(`任务状态查询失败：${String(e)}。保留任务标识，可重新查询。`); } };
    void poll(); return () => { stopped = true; clearTimeout(timer); };
  }, [base, requestId]);
  const submit = async () => {
    if (busy || disabled || !instruction.trim()) return;
    const id = crypto.randomUUID(); baseline.current = scene; editPending.current = false; setBusy(true); setError(''); setProposal(null); setStatus('submitting');
    try { localStorage.setItem(key, JSON.stringify({ requestId: id, instruction })); } catch { /* unavailable */ }
    try { const { data } = await api.post(`${base}/${id}`, { json: { scene, instruction }, retry: 0, timeout: 120000 }).json<{ data: Result }>(); applyResult(data); }
    catch (e) { setError(`提交结果未知：${String(e)}。请查询此任务，不要重复提交付费规划。`); }
    finally { setRequestId(id); setBusy(false); }
  };
  const active = busy || ['queued', 'pending', 'running', 'submitting', 'retrying'].includes(status);
  const problem = proposal ? validateScene(proposal) : null;
  return <section className="space-y-3 rounded border p-3" aria-label="AI 自然语言预演规划"><h3 className="font-medium">自然语言搭景与动作规划</h3><p className="text-xs text-muted-foreground">使用项目的导演文本任务模型，可能产生费用，价格按已配置服务结算。仅在点击生成时提交；结果先审阅，不修改剧本或当前预演。参考图片暂不发送给文本模型。</p><textarea aria-label="AI 预演需求" className="min-h-24 w-full rounded border bg-background p-2 text-sm" value={instruction} onChange={e => setInstruction(e.target.value)} placeholder="让演员先沿左侧缓步走到椅子旁，转身面对镜头后坐下。机位用五秒从全景推近。" /><Button disabled={active || disabled || !instruction.trim()} onClick={() => void submit()}>{requestId ? '明确提交一次新的 AI 规划' : '生成 AI 预演建议（可能计费）'}</Button>{requestId && <div className="text-xs"><p>请求：{requestId} · 状态：{status || '查询中'}</p><Button variant="outline" onClick={async () => { try { const { data } = await api.get(`${base}/${requestId}`).json<{ data: Result }>(); applyResult(data); } catch (e) { setError(String(e)); } }}>查询原任务</Button></div>}{error && <p role="alert" className="text-xs text-destructive">{error}</p>}{proposal && <><SceneProposalEditor value={proposal} onChange={setProposal} />{problem && <p className="text-xs text-destructive">{problem}</p>}<Button disabled={Boolean(problem) || disabled} onClick={() => { const original = baseline.current; if (scene !== original && !window.confirm('当前预演在规划后已变化。确认用已审阅建议替换？')) return; const identities = (s: Scene) => JSON.stringify(s.actors.map(a => [a.id, a.name, a.humanoid, a.characterRef]).sort()); if (identities(proposal) !== identities(scene)) { setError('建议中的演员身份或适配确认与当前预演不一致，请重新规划'); return; } onAdopt({ ...proposal, source: scene.source }); setProposal(null); }}>采纳已编辑的场景与动作</Button><Button variant="outline" onClick={() => setProposal(null)}>暂不采纳</Button></>}</section>;
}
