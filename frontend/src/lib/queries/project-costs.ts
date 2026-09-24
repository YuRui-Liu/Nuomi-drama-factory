// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useEffect, useRef, useState } from 'react';
import { useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { p } from '@/lib/api-path';
import type { CostEntriesResponse, CostEntryDetail, CostFilters, ProjectCostSnapshot } from '@/types/project-costs';

async function read<T>(path: string, signal: AbortSignal, searchParams?: URLSearchParams): Promise<T> {
  const result = await api.get(path, { signal, searchParams }).json<{ ok: boolean; data: T; error?: string }>();
  if (!result.ok) throw new Error(result.error || 'Cost request failed');
  return result.data;
}
export function useProjectCostSnapshot(project: string) {
  const client = useQueryClient();
  const previousSnapshot = useRef(0);
  const manuallyRefreshing = useRef(false);
  const [visible, setVisible] = useState(() => document.visibilityState !== 'hidden');
  useEffect(() => {
    const update = () => setVisible(document.visibilityState !== 'hidden');
    document.addEventListener('visibilitychange', update);
    return () => document.removeEventListener('visibilitychange', update);
  }, []);
  const query = useQuery({ queryKey: ['project-costs', project, 'snapshot'], queryFn: ({ signal }) => read<ProjectCostSnapshot>(p`api/v1/projects/${project}/costs/snapshot`, signal), enabled: Boolean(project) && visible, refetchInterval: 15000, refetchIntervalInBackground: false });
  useEffect(() => {
    const previous = previousSnapshot.current;
    previousSnapshot.current = query.dataUpdatedAt;
    if (!visible || manuallyRefreshing.current || !previous || previous === query.dataUpdatedAt) return;
    // Snapshot polling is the only timer. Its successful updates refresh the
    // mounted ledger/detail without recursively invalidating the snapshot.
    void client.invalidateQueries({
      queryKey: ['project-costs', project],
      predicate: cached => cached.queryKey[2] === 'entries' || cached.queryKey[2] === 'entry',
    }, { cancelRefetch: false });
  }, [client, project, query.dataUpdatedAt, visible]);
  const refresh = async () => {
    manuallyRefreshing.current = true;
    try {
      await refreshProjectCosts(client, project);
    } finally {
      previousSnapshot.current = client.getQueryState(['project-costs', project, 'snapshot'])?.dataUpdatedAt ?? 0;
      manuallyRefreshing.current = false;
    }
  };
  return { ...query, refresh };
}
/** Also reusable after cost-setting mutations; inactive project data is marked stale. */
export function refreshProjectCosts(client: QueryClient, project: string) {
  return client.invalidateQueries({ queryKey: ['project-costs', project] }, { cancelRefetch: false });
}
export function useProjectCostEntries(project: string, filters: CostFilters, cursor: string | null) {
  return useQuery({ queryKey: ['project-costs', project, 'entries', filters, cursor], queryFn: ({ signal }) => {
    const params = new URLSearchParams({ limit: '25' });
    Object.entries(filters).forEach(([key, value]) => { if (value) params.set(key, value); });
    if (cursor) params.set('cursor', cursor);
    return read<CostEntriesResponse>(p`api/v1/projects/${project}/costs/entries`, signal, params);
  }, enabled: Boolean(project) });
}
export function useProjectCostEntry(project: string, id: string | null) {
  return useQuery({ queryKey: ['project-costs', project, 'entry', id], queryFn: ({ signal }) => read<CostEntryDetail>(p`api/v1/projects/${project}/costs/entries/${id!}`, signal), enabled: Boolean(project && id) });
}
