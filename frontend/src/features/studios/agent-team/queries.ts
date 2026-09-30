import { useEffect } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { getTeam } from './api';
export const teamKey = (project: string) => ['agent-team', project] as const;
export function announceTeamChange(project: string) { window.dispatchEvent(new CustomEvent('agent-team-changed', { detail: { project } })); }
export function useTeam(project: string) {
  const client = useQueryClient();
  useEffect(() => { const listener = (event: Event) => { if ((event as CustomEvent).detail?.project === project) void client.invalidateQueries({ queryKey: teamKey(project) }); }; window.addEventListener('agent-team-changed', listener); return () => window.removeEventListener('agent-team-changed', listener); }, [client, project]);
  return useQuery({ queryKey: teamKey(project), queryFn: ({ signal }) => getTeam(project, signal) });
}
