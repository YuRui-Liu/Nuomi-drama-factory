import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";

const sources = new Map<string, boolean>();
const isDirty = () => [...sources.values()].some(Boolean);
function publish(source: string) {
  window.dispatchEvent(new CustomEvent("studio-dirty", { detail: { module: "character", source, sourceDirty: sources.get(source) ?? false, dirty: isDirty() } }));
}
export function useCharacterDraft(source: string | undefined, dirty: boolean, save: () => void) {
  const current = useRef({ dirty, save }); current.current = { dirty, save };
  useEffect(() => {
    if (!source) return;
    const handler = () => { if (current.current.dirty) current.current.save(); };
    window.addEventListener("studio-save-character", handler);
    return () => { window.removeEventListener("studio-save-character", handler); sources.delete(source); publish(source); };
  }, [source]);
  useEffect(() => { if (source) { sources.set(source, dirty); publish(source); } }, [source, dirty]);
}
export function useCharacterSwitch(enabled = true) {
  const [pending, setPending] = useState<(() => void) | null>(null);
  const [saving, setSaving] = useState(false);
  const waiting = useRef(false);
  const next = useRef(pending); next.current = pending;
  useEffect(() => {
    const changed = () => { if (waiting.current && !isDirty()) { waiting.current = false; setSaving(false); const action = next.current; setPending(null); action?.(); } };
    const failed = () => { waiting.current = false; setSaving(false); };
    window.addEventListener("studio-dirty", changed); window.addEventListener("studio-save-failed-character", failed);
    return () => { window.removeEventListener("studio-dirty", changed); window.removeEventListener("studio-save-failed-character", failed); };
  }, []);
  return {
    request: (action: () => void) => { if (enabled && isDirty()) setPending(() => action); else action(); },
    dialog: pending ? <div role="dialog" aria-label="保存角色修改" className="rounded-xl border bg-card p-4 space-y-3"><p>当前角色有未保存修改。保存或放弃后再切换。</p><div className="flex gap-2"><Button disabled={saving} onClick={() => setPending(null)}>继续编辑</Button><Button disabled={saving} variant="outline" onClick={() => { const action = pending; setPending(null); action(); }}>放弃修改</Button><Button disabled={saving} onClick={() => { waiting.current = true; setSaving(true); window.dispatchEvent(new Event("studio-save-character")); }}>保存后切换</Button></div></div> : null,
  };
}
