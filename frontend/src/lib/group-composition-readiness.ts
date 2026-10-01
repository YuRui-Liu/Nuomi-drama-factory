import type { NarrativeGroup } from '@/lib/queries/narrative-groups';

/** Completed existing videos remain composable when their inputs change. */
export function groupCompositionReadiness(groups: NarrativeGroup[]) {
  const missing = groups.filter(({stages:{video}}) => video.status !== 'completed'
    || !video.video_asset || !video.manifest_asset).map(group => group.id);
  const outdated = groups.filter(group => !missing.includes(group.id) && group.stages.video.needs_regeneration).map(group => group.id);
  return {ready: groups.length > 0 && missing.length === 0, completed: groups.length - missing.length, missing, outdated};
}
