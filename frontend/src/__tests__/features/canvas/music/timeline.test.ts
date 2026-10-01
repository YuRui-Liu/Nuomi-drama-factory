import { describe, it, expect } from 'vitest';
import { emptyPlan, insertClip, splitClip, duplicateClip, changeClip, undo, redo, remember } from '@/features/canvas/music/timeline';
import type { MusicClip } from '@/features/canvas/music/types';

const clip: MusicClip = { id:'c1',assetVersionId:'a',startMs:0,sourceInMs:0,lengthMs:90000,gainDb:0,fadeInMs:0,fadeOutMs:0,loop:null };
const create = () => { const p=emptyPlan({assetVersionId:'v',sha256:'a'.repeat(64),durationMs:90000}); p.tracks[0].clips=[clip]; return p; };

describe('music timeline', () => {
  it('replaces only the selected region, preserving right source offset', () => {
    const p=create(); const out=insertClip(p,p.tracks[0].id,{...clip,id:'new',startMs:18000,lengthMs:18000},'replace');
    expect(out.tracks[0].clips.map(c=>[c.startMs,c.sourceInMs,c.lengthMs])).toEqual([[0,0,18000],[18000,0,18000],[36000,36000,54000]]);
    expect(p.tracks[0].clips).toHaveLength(1);
  });
  it('requires explicit collision handling and can layer a new track',()=>{
    const p=create(); expect(()=>insertClip(p,p.tracks[0].id,{...clip,id:'new'},'ask')).toThrow();
    expect(insertClip(p,p.tracks[0].id,{...clip,id:'new'},'layer').tracks).toHaveLength(2);
  });
  it('splits with continuous source offset and duplicates independent ids',()=>{
    const p=create(); const out=splitClip(p,'c1',36000);
    expect(out.tracks[0].clips[1].sourceInMs).toBe(36000);
    const copied=duplicateClip(p,'c1');
    expect(copied.tracks[1].clips[0].id).not.toBe('c1');
  });
  it('rejects edits outside the video and undoes deletion',()=>{
    const p=create(); expect(()=>changeClip(p,'c1',{startMs:1})).toThrow();
    const history=remember({past:[],present:p,future:[]},{...p,tracks:[]});
    expect(undo(history).present.tracks).toHaveLength(1);
    expect(redo(undo(history)).present.tracks).toHaveLength(0);
  });
});
