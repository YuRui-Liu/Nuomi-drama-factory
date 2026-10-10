import {getFreezoneCanvas, putFreezoneCanvas, generateClientSaveId, type FreezoneCanvasPayload} from '@/api/canvas';
import {musicApi} from './api';
import {emptyPlan} from './timeline';

/** Snapshot first: the final output URL may be overwritten by a later composition. */
export async function openEpisodeMusicDesk(project: string, episode: number, url: string): Promise<{canvas: string}> {
 const source = await musicApi.source(project, url);
 if (!source.sha256) throw new Error('成片版本缺少校验信息，请重试');
 const canvas = `music-ep${episode}-${source.sha256.slice(0,40)}`;
 let existing: FreezoneCanvasPayload = {nodes: [], edges: []};
 try { existing = await getFreezoneCanvas(project, canvas); }
 catch (error) { if ((error as {status?:number}).status !== 404) throw error; }
 if (existing.nodes.some(node => (node as {id?:string}).id === 'music-desk')) return {canvas};
 const loaded = await musicApi.load(project, canvas, 'music-desk');
 if (!loaded.plan) await musicApi.save(project, canvas, 'music-desk', emptyPlan({assetVersionId:source.versionId, sha256:source.sha256, durationMs:source.durationMs}));
 await putFreezoneCanvas(project, canvas, {
  ...existing, schema_version:2, canvas_id:canvas, project_id:project,
  base_revision:existing.revision ?? null, client_save_id:generateClientSaveId(), save_source:'manual_save',
  metadata:{...existing.metadata,display_name:`第 ${episode} 集配乐 · ${source.sha256.slice(0,8)}`},
  nodes:[...existing.nodes,{id:'music-desk',type:'musicDeskNode',position:{x:120,y:160},data:{displayName:`第 ${episode} 集配乐`,sourceUrl:url}}],
 });
 return {canvas};
}
