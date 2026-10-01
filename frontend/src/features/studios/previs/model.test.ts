import { describe, expect, it } from 'vitest';
import { sampleActor, validateScene, parseActions, parseSceneCommand, emptyScene, importDirectorScene, sampleCamera, type Actor, type Clip } from './model';
const actor: Actor = { id: 'a', name: '测试演员', humanoid: true, position: [0, 0, 0], yaw: 0 };
const walk: Clip = { id: 'w', actorId: 'a', action: 'walk', start: 0, duration: 2, target: [4, 0, 0], yaw: 0 };
describe('previs timeline', () => {
  it('holds endpoints and continues later movement from prior endpoint', () => {
    const clips: Clip[] = [walk, { ...walk, id: 'next', start: 3, target: [8, 0, 0] }];
    expect(sampleActor(actor, clips, 2.5).position).toEqual([4, 0, 0]);
    expect(sampleActor(actor, clips, 4).position).toEqual([6, 0, 0]);
  });
  it('rejects overlap and nonhumanoid animation', () => {
    expect(validateScene({ ...emptyScene(), actors: [actor], clips: [walk, { ...walk, id: 'b', start: 1 }] })).toMatch(/重叠/);
    expect(validateScene({ ...emptyScene(), actors: [{ ...actor, humanoid: false }], clips: [walk] })).toMatch(/人形/);
  });
  it('keeps final sitting pose', () => {
    expect(sampleActor(actor, [{ ...walk, action: 'sit' }], 9).sit).toBe(1);
  });
  it('holds locomotion heading after reaching the destination', () => {
    expect(sampleActor(actor, [walk], 3).yaw).toBe(90);
  });
  it('interpolates camera path on the same timeline', () => {
    expect(sampleCamera([{ id: 'a', time: 0, position: [0, 2, 0], target: [0, 0, 0] }, { id: 'b', time: 2, position: [4, 2, 0], target: [0, 0, 0] }], 1).position).toEqual([2, 2, 0]);
  });
  it('imports actual director-world actor and prop positions', () => {
    const scene = importDirectorScene({ schemaVersion: 1, savedAt: 1, actors: [{ label: '演员甲', position: [3, 0, 4], yawDeg: 25, scale: [1, 1, 1], color: '#fff' }], props: [], stagings: [] });
    expect(scene.actors[0]).toMatchObject({ name: '演员甲', position: [3, 0, 4], yaw: 25, humanoid: false });
  });
  it('parses explicit local commands and rejects unknown text', () => {
    expect(parseActions('走 3 秒 到 4,2', actor, 0)[0]).toMatchObject({ action: 'walk', duration: 3, target: [4, 0, 2] });
    expect(() => parseActions('复杂打斗', actor, 0)).toThrow();
  });
  it('produces editable scene commands without changing original scene', () => {
    const original = { ...emptyScene(), actors: [actor] };
    expect(parseSceneCommand('演员站位 2,3', original, 'a', 0).actors[0].position).toEqual([2, 0, 3]);
    expect(original.actors[0].position).toEqual([0, 0, 0]);
    expect(parseSceneCommand('添加座椅 2,3', original, 'a', 0).props[0].position).toEqual([2, 0.3, 3]);
  });
});
