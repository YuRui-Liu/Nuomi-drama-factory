import type { ScriptDocument } from "./types";

export type DraftStatus = "saved" | "dirty" | "saving" | "error" | "conflict";
export interface DocumentDraft {
  document: ScriptDocument;
  markdown: string;
  revisionId: string;
  version: number;
  status: DraftStatus;
  error?: string;
}
type Save = (id: string, revisionId: string, markdown: string) => Promise<ScriptDocument>;

export class DraftManager {
  private drafts = new Map<string, DocumentDraft>();
  private timers = new Map<string, ReturnType<typeof setTimeout>>();
  private inFlight = new Map<string, Promise<void>>();
  private listeners = new Set<() => void>();
  constructor(private save: Save) {}

  subscribe = (listener: () => void) => { this.listeners.add(listener); return () => { this.listeners.delete(listener); }; };
  private emit() { this.listeners.forEach((listener) => listener()); }
  get(id: string) { return this.drafts.get(id); }
  all() { return [...this.drafts.values()]; }

  load(document: ScriptDocument) {
    const current = this.drafts.get(document.id);
    if (current && current.status !== "saved") return;
    if (current && current.revisionId === document.current_revision_id) return;
    this.drafts.set(document.id, { document, markdown: document.revision.markdown,
      revisionId: document.current_revision_id, version: 0, status: "saved" });
    this.emit();
  }

  edit(id: string, markdown: string) {
    const draft = this.drafts.get(id);
    if (!draft || draft.markdown === markdown) return;
    this.drafts.set(id, { ...draft, markdown, version: draft.version + 1, status: "dirty", error: undefined });
    this.schedule(id);
    this.emit();
  }

  private schedule(id: string) {
    const old = this.timers.get(id);
    if (old) clearTimeout(old);
    this.timers.set(id, setTimeout(() => { this.timers.delete(id); void this.flush(id); }, 800));
  }

  async flush(id: string): Promise<void> {
    const timer = this.timers.get(id);
    if (timer) clearTimeout(timer);
    this.timers.delete(id);
    if (this.inFlight.has(id)) return this.inFlight.get(id);
    const draft = this.drafts.get(id);
    if (!draft || !["dirty", "error"].includes(draft.status)) return;
    const markdown = draft.markdown;
    const version = draft.version;
    this.drafts.set(id, { ...draft, status: "saving", error: undefined });
    this.emit();
    const operation = this.save(id, draft.revisionId, markdown).then((document) => {
      const latest = this.drafts.get(id);
      if (!latest) return;
      const unchanged = latest.version === version;
      this.drafts.set(id, { ...latest, document, revisionId: document.current_revision_id,
        status: unchanged ? "saved" : "dirty" });
      if (!unchanged) this.schedule(id);
      this.emit();
    }).catch((error: unknown) => {
      const latest = this.drafts.get(id);
      if (!latest) return;
      const status = typeof error === "object" && error && "status" in error && error.status === 409 ? "conflict" : "error";
      this.drafts.set(id, { ...latest, status, error: error instanceof Error ? error.message : "保存失败" });
      this.emit();
    }).finally(() => { this.inFlight.delete(id); });
    this.inFlight.set(id, operation);
    return operation;
  }

  retry(id: string) { return this.flush(id); }

  resolveConflict(id: string, latest: ScriptDocument, keepLocal: boolean) {
    const draft = this.drafts.get(id);
    if (!draft) return;
    if (keepLocal) {
      this.drafts.set(id, { ...draft, document: latest, revisionId: latest.current_revision_id, status: "dirty", error: undefined });
      this.schedule(id);
    } else {
      this.drafts.set(id, { document: latest, markdown: latest.revision.markdown, revisionId: latest.current_revision_id,
        version: draft.version + 1, status: "saved" });
    }
    this.emit();
  }

  dispose() { this.timers.forEach(clearTimeout); this.timers.clear(); this.listeners.clear(); }
}
