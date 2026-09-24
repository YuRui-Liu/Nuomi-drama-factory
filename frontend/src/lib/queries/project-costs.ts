// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { p } from '@/lib/api-path';
import type { CostEntriesResponse, CostEntryDetail, CostFilters, ProjectCostSnapshot } from '@/types/project-costs';

async function read<T>(path: string, signal: AbortSignal, searchParams?: URLSearchParams): Promise<T> {
  const result = await api.get(path, { signal, searchParams }).json<{ ok: boolean; data: T; error?: string }>();
  if (!result.ok) throw new Error(result.error || 'Cost request failed');
  return result.data;
}
export function useProjectCostSnapshot(project: string) {
  const [visible, setVisible] = useState(() => document.visibilityState !== 'hidden');
  useEffect(() => {
    const update = () => setVisible(document.visibilityState !== 'hidden');
    document.addEventListener('visibilitychange', update);
    return () => document.removeEventListener('visibilitychange', update);
  }, []);
  return useQuery({ queryKey: ['project-costs', project, 'snapshot'], queryFn: ({ signal }) => read<ProjectCostSnapshot>(p`api/v1/projects/${project}/costs/snapshot`, signal), enabled: Boolean(project) && visible, refetchInterval: 15000, refetchIntervalInBackground: false });
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
