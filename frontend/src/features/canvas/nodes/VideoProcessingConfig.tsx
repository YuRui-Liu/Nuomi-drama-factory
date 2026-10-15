import { useState } from 'react';
import { apiCall } from '@/api/client';

type Mode = 'extend' | 'upscale' | 'interpolate' | 'retake';
type Model = {model: string; enabled: boolean; resolutions?: string[]; frame_rates?: number[];
  output_kind?: 'full' | 'continuation'; duration_mode?: 'additional' | 'total'; max_input_seconds?: number};
type Config = {models: Partial<Record<Mode, Model>>};
const labels: Record<Mode, string> = {extend:'视频延长',upscale:'模型超分',interpolate:'智能插帧',retake:'片段重拍'};
const modes = Object.keys(labels) as Mode[];
const fieldClass = 'w-full rounded border border-white/15 bg-zinc-800 p-2';

export function VideoProcessingConfig({project, onSaved}: {project: string; onSaved: () => void}) {
  const [open, setOpen] = useState(false);
  const [config, setConfig] = useState<Config>({models:{}});
  const [status, setStatus] = useState('');
  const [busy, setBusy] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const path = `projects/${encodeURIComponent(project)}/freezone/video/processing-config`;
  const update = (mode: Mode, patch: Partial<Model>) => setConfig(previous => ({models:{...previous.models,
    [mode]:{model:'',enabled:false,...previous.models[mode],...patch}}}));
  async function load() {
    if (open) {setOpen(false); return;}
    setOpen(true); setBusy(true); setStatus(''); setLoaded(false);
    try {setConfig(await apiCall<Config>(path));setLoaded(true);}
    catch (error) {setStatus(`读取失败：${String(error)}`);}
    finally {setBusy(false);}
  }
  async function save() {
    if (!loaded || busy) return;
    const models = Object.fromEntries(modes.filter(mode=>config.models[mode]?.model.trim()).map(mode => {
      const model = config.models[mode]!;
      return [mode,{...model,model:model.model.trim()}];
    }));
    if (modes.some(mode=>config.models[mode]?.enabled && !config.models[mode]?.model.trim())) {
      setStatus('请为已启用的能力填写模型 ID');return;
    }
    setBusy(true); setStatus('');
    try {await apiCall(path,{method:'PUT',json:{models}});setStatus('配置已保存');onSaved();}
    catch (error) {setStatus(`保存失败：${String(error)}`);}
    finally {setBusy(false);}
  }
  return <div className="space-y-2 border-t border-white/10 pt-2">
    <button type="button" onClick={()=>void load()} className="text-blue-300">配置处理模型</button>
    {open && <div className="max-h-80 space-y-3 overflow-y-auto">
      <p>使用模型设置中的 NewAPI 网关与密钥。请填写供应商明确支持相应能力的模型 ID；可只配置需要的能力。</p>
      {modes.map(mode=>{
        const model = config.models[mode] ?? {model:'',enabled:false};
        return <fieldset key={mode} disabled={busy || !loaded} className="space-y-2 rounded border border-white/15 p-2">
          <legend>{labels[mode]}</legend>
          <label className="flex items-center gap-2"><input type="checkbox" checked={model.enabled} onChange={event=>update(mode,{enabled:event.target.checked})}/>启用{labels[mode]}</label>
          <input aria-label={`${labels[mode]}模型 ID`} placeholder="供应商模型 ID" className={fieldClass} value={model.model} onChange={event=>update(mode,{model:event.target.value})}/>
          {mode === 'extend' && <>
            <label className="block">供应商返回内容<select aria-label="延长返回内容" className={fieldClass} value={model.output_kind ?? 'full'} onChange={event=>update(mode,{output_kind:event.target.value as Model['output_kind']})}><option value="full">完整视频（原片与新增内容）</option><option value="continuation">仅新增片段（自动拼接原片）</option></select></label>
            <label className="block">供应商时长参数<select aria-label="延长时长含义" className={fieldClass} value={model.duration_mode ?? 'additional'} onChange={event=>update(mode,{duration_mode:event.target.value as Model['duration_mode']})}><option value="additional">新增时长</option><option value="total">完成后的总时长</option></select></label>
          </>}
          {mode === 'upscale' && <div>支持的分辨率<div className="flex gap-2">{['1080p','2k','4k'].map(resolution=><label key={resolution}><input type="checkbox" checked={model.resolutions?.includes(resolution) ?? false} onChange={event=>update(mode,{resolutions:event.target.checked?[...(model.resolutions ?? []),resolution]:(model.resolutions ?? []).filter(value=>value!==resolution)})}/>{resolution}</label>)}</div></div>}
          {mode === 'interpolate' && <div>支持的目标帧率<div className="flex flex-wrap gap-2">{[24,25,30,48,50,60,120].map(fps=><label key={fps}><input type="checkbox" checked={model.frame_rates?.includes(fps) ?? false} onChange={event=>update(mode,{frame_rates:event.target.checked?[...(model.frame_rates ?? []),fps]:(model.frame_rates ?? []).filter(value=>value!==fps)})}/>{fps} fps</label>)}</div></div>}
          <label className="block">最长输入时长（秒）<input aria-label={`${labels[mode]}最长输入秒数`} className={fieldClass} type="number" min="1" max="3600" value={model.max_input_seconds ?? 120} onChange={event=>update(mode,{max_input_seconds:Number(event.target.value)})}/></label>
        </fieldset>;
      })}
      <button type="button" disabled={busy || !loaded} onClick={()=>void save()} className="rounded bg-blue-700 px-2 py-1 disabled:opacity-40">{busy?'处理中…':'保存配置'}</button>
      {status && <p role="status">{status}</p>}
    </div>}
  </div>;
}
