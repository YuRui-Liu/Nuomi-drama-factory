import { useRef, useState } from "react";
import { ArrowRight, X } from "lucide-react";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { ScriptSettingsContent } from "./script-settings-content";
import type { ScriptSettings } from "./types";
import "./script-setter.css";

type Props = { initial: ScriptSettings; onSave: (settings: ScriptSettings) => void | Promise<void>; onClose: () => void };
const whole = (value: number, minimum: number, maximum = Number.MAX_SAFE_INTEGER) =>
  Math.min(maximum, Math.max(minimum, Number.isFinite(value) ? Math.round(value) : minimum));
const normalize = (draft: ScriptSettings): ScriptSettings => ({
  ...draft, episodeCount: draft.mode === "single" ? 1 : whole(draft.episodeCount, 2, 100),
  durationSeconds: whole(draft.durationSeconds, 15),
});

export function ScriptSetter({ initial, onSave, onClose }: Props) {
  const [draft, setDraft] = useState<ScriptSettings>(() => normalize(structuredClone(initial)));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const saving = useRef(false);
  const save = async () => {
    if (saving.current) return;
    saving.current = true; setBusy(true); setError("");
    try { await onSave(normalize(structuredClone(draft))); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "保存失败，请重试"); }
    finally { saving.current = false; setBusy(false); }
  };
  const close = () => { if (!saving.current) onClose(); };

  return <Dialog open onOpenChange={open => { if (!open) close(); }}>
    <DialogContent className="ns-modal" overlayClassName="ns-backdrop" showCloseButton={false} aria-label="剧本设定器">
      <header className="ns-header"><DialogTitle>剧本设定器</DialogTitle><button type="button" className="ns-close" aria-label="关闭剧本设定器" disabled={busy} onClick={close}><X size={19} /></button></header>
      <DialogDescription className="sr-only">选择创作预设，融合题材，精调六项设定并保存你的故事想法。</DialogDescription>
      <ScriptSettingsContent value={draft} onChange={setDraft} disabled={busy} />
      <footer className="ns-footer">
        {error && <span role="alert" className="ns-error">{error}</span>}
        <div className="ns-actions"><button type="button" disabled={busy} onClick={close}>取消</button><button type="button" className="ns-primary" disabled={busy} aria-label="保存创作设定" onClick={() => void save()}>{busy ? "保存中…" : "保存创作设定"}<ArrowRight size={14} /></button></div>
      </footer>
    </DialogContent>
  </Dialog>;
}
