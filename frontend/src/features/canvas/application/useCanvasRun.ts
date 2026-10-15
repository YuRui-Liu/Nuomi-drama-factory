import { useCallback, useEffect, useState } from 'react';
import { controlCanvasRun, listCanvasRuns, refreshCanvasRunResults, startCanvasRun, type CanvasRun } from '../infrastructure/orchestrationGateway';

export function useCanvasRun(project: string, canvas: string, enabled = true) {
  const [run, setRun] = useState<CanvasRun | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (!enabled) return;
    let alive = true;
    let timer: ReturnType<typeof setTimeout>;
    let previous = '';
    setRun(null);
    const poll = async () => {
      try {
        const runs = await listCanvasRuns(project, canvas);
        if (!alive) return;
        const current = runs[0] ?? null;
        setRun(current);
        if (current) {
          const signature = JSON.stringify(runs.map(item => [item.id, item.status, item.attempts]));
          if (signature !== previous) {
            await refreshCanvasRunResults(project, canvas);
            previous = signature;
          }
        }
      } catch (e) {
        if (alive) setError(e instanceof Error ? e.message : String(e));
      } finally {
        if (alive) timer = setTimeout(poll, 4000);
      }
    };
    void poll();
    return () => { alive = false; clearTimeout(timer); };
  }, [project, canvas, enabled]);
  const execute = useCallback(async (target: string) => {
    setBusy(true); setError('');
    try { setRun(await startCanvasRun(project, canvas, target)); }
    catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  }, [project, canvas]);
  const control = useCallback(async (action: 'resume' | 'retry' | 'cancel') => {
    if (!run) return;
    setBusy(true); setError('');
    try { setRun(await controlCanvasRun(project, run.id, action)); }
    catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  }, [project, run]);
  return { run, error, busy, execute, control };
}
