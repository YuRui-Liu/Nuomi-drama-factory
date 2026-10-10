import { useState } from "react";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";

export function ProjectDisplayName({ name, displayName, save }: { name: string; displayName?: string; save: (value: { display_name: string }) => Promise<unknown> }) {
  const [draft, setDraft] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const [message, setMessage] = useState("");
  return <form className="flex flex-wrap items-center gap-2 text-sm" onSubmit={async (event) => {
    event.preventDefault();
    setPending(true);
    setMessage("");
    try { await save({ display_name: (draft ?? displayName ?? "").trim() }); setMessage("名称已保存"); }
    catch { setMessage("名称保存失败，请重试"); }
    finally { setPending(false); }
  }}>
    <label htmlFor="edit-project-display-name">作品显示名称</label>
    <Input id="edit-project-display-name" className="w-48" maxLength={120} placeholder={name}
      value={draft ?? displayName ?? ""} onChange={(event) => setDraft(event.target.value)} />
    <Button type="submit" size="sm" disabled={pending}>保存名称</Button>
    <span className="text-muted-foreground">内部标识：{name}</span>
    {message && <span role="status">{message}</span>}
  </form>;
}
