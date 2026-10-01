import { describe, it, expect } from 'vitest';
import { groupCompositionReadiness } from '@/lib/group-composition-readiness';
import type { NarrativeGroup } from '@/lib/queries/narrative-groups';

const group = (id: string, status: string, extra = {}) => ({id, stages:{video:{status,video_asset:'/movie.mp4',manifest_asset:'/manifest.json',...extra}}}) as NarrativeGroup;
describe('group composition readiness', () => {
  it('requires complete media but treats outdated inputs as advisory', () => {
    expect(groupCompositionReadiness([]).ready).toBe(false);
    expect(groupCompositionReadiness([group('a','completed'),group('b','failed')]).missing).toEqual(['b']);
    expect(groupCompositionReadiness([group('a','completed',{needs_regeneration:true})])).toEqual({ready:true,completed:1,missing:[],outdated:['a']});
    expect(groupCompositionReadiness([group('a','completed',{manifest_asset:null})]).ready).toBe(false);
    expect(groupCompositionReadiness([group('a','completed'),group('b','completed')])).toEqual({ready:true,completed:2,missing:[],outdated:[]});
  });
});
