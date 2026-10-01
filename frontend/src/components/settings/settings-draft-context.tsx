import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

const DraftContext = createContext<((id: string, dirty: boolean) => void) | null>(null);

export function SettingsDraftProvider({ children, onDirtyChange }: { children: ReactNode; onDirtyChange: (dirty: boolean) => void }) {
  const [drafts, setDrafts] = useState<Record<string, boolean>>({});
  const register = useCallback((id: string, dirty: boolean) => {
    setDrafts((previous) => previous[id] === dirty ? previous : { ...previous, [id]: dirty });
  }, []);
  const dirty = Object.values(drafts).some(Boolean);
  useEffect(() => { onDirtyChange(dirty); }, [dirty, onDirtyChange]);
  return <DraftContext.Provider value={register}>{children}</DraftContext.Provider>;
}

/** A saved snapshot changes only on initial load or a successful save. */
export function useSettingsSnapshot(id: string, values: unknown) {
  const register = useContext(DraftContext);
  const serialized = JSON.stringify(values);
  const [saved, setSaved] = useState(serialized);
  const dirty = serialized !== saved;
  useEffect(() => { register?.(id, dirty); }, [register, id, dirty]);
  useEffect(() => () => register?.(id, false), [register, id]);
  const markSaved = useCallback((next: unknown) => setSaved(JSON.stringify(next)), []);
  return useMemo(() => ({ dirty, markSaved }), [dirty, markSaved]);
}
