import { useRef, useState } from 'react';
import { productionActionErrorMessage } from './productionActionError';

/** Retain the same mutation after uncertain responses; only a successful result clears it. */
export function useProductionAction() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const running = useRef(false);
  const mutations = useRef(new Map<string, string>());
  async function perform(key: string, action: (mutation: string) => Promise<void>) {
    if (running.current) return;
    running.current = true;
    setBusy(true); setError('');
    const mutation = mutations.current.get(key) ?? crypto.randomUUID();
    mutations.current.set(key, mutation);
    try { await action(mutation); mutations.current.delete(key); }
    catch (reason) { setError(await productionActionErrorMessage(reason)); }
    finally { running.current = false; setBusy(false); }
  }
  return { busy, error, perform };
}
