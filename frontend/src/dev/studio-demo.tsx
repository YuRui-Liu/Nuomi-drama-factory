import { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import * as pc from 'playcanvas';
import { ThreeDStageCanvas } from '../features/viewer-kit/three-d/ThreeDStageCanvas';
import type { ViewerApp, ThreeDSceneSnapshot } from '../features/viewer-kit/three-d/engine/viewerApp';
import '../index.css';
import './studio-demo.css';

// Development-only entry: uses the production renderer, without a provider or project API.
function StudioDemo() {
  const viewer = useRef<ViewerApp | null>(null);
  const [ready, setReady] = useState(false);
  const [selection, setSelection] = useState('步知遥 · 人物替身');
  const [status, setStatus] = useState('正在启动 3D 工作区…');
  const [frame, setFrame] = useState<string | null>(null);
  const [saved, setSaved] = useState<ThreeDSceneSnapshot | null>(null);
  const [cameraSelected, setCameraSelected] = useState(false);
  const [fov, setFov] = useState(50);
  const [position, setPosition] = useState([-0.8, 0, 0]);
  const [intensity, setIntensity] = useState(2);
  const [frameAspect, setFrameAspect] = useState<'16:9' | '9:16'>('16:9');
  const unsubscribe = useRef<(() => void) | null>(null);
  useEffect(() => () => { unsubscribe.current?.(); viewer.current = null; }, []);

  const onReady = (app: ViewerApp) => {
    viewer.current = app;
    const material = new pc.StandardMaterial();
    material.diffuse = new pc.Color(0.22, 0.25, 0.28);
    material.update();
    const floor = new pc.Entity('demo-floor');
    floor.addComponent('render', { type: 'box', material });
    floor.setPosition(0, -0.08, 0);
    floor.setLocalScale(12, 0.1, 10);
    app.app.root.addChild(floor);
    app.placeMarker('actor', { position: [-0.8, 0, 0], label: '步知遥 · 人物替身', color: '#a1c2d4' });
    app.placeMarker('staging', { position: [1, 0.5, 0], scale: [2, 1, 0.9], label: '修简铺 · 书案', color: '#87745d' });
    app.placeMarker('prop', { position: [0.8, 1.1, 0], scale: [0.45, 0.3, 0.3], label: '木匣', color: '#d1b682' });
    app.placeMarker('light', { position: [-1.8, 2, 0.5], label: '窗边主光', color: '#ffe0ac' });
    app.fly.setCameraState({ azim: 15, elev: -10, distance: 2, focalPoint: [0, 0.8, 0] });
    app.setOutputCameraFromView();
    app.setOutputCameraState({ fov: 50 });
    app.fly.setCameraState({ azim: 55, elev: -35, distance: 4, focalPoint: [0, 0.8, 1] });
    app.selectByIndex('actor', 0);
    const offSelection = app.onSelectionChange(s => {
      setCameraSelected(app.isOutputCameraSelected());
      setSelection(s?.label ?? (app.isOutputCameraSelected() ? '输出摄影机' : '未选中对象'));
      if (s) setPosition(s.position);
    });
    const offCamera = app.onOutputCameraChange(s => setFov(s.fov));
    const offLight = app.onSelectedLightChange(s => { if (s) setIntensity(s.intensity); });
    unsubscribe.current = () => { offSelection(); offCamera(); offLight(); };
    setReady(true);
    setStatus('拖动坐标轴调整站位。当前仅为调度示意，不调用模型。');
  };

  const capture = () => {
    const data = viewer.current?.captureScreenshot({ markers: true, frameAspect, renderCamera: 'output' });
    if (data) { setFrame(data); setStatus('已截取调度参考图，未生成最终分镜。'); }
  };
  const save = () => {
    const next = viewer.current?.exportSceneSnapshot();
    if (!next) return;
    setSaved(next);
    setStatus('已保存本次演示的调度快照，可继续调整后恢复。');
  };
  const move = (x: number, y: number, z: number) => {
    const app = viewer.current;
    if (!app) return;
    if (app.isOutputCameraSelected()) {
      const p = app.getOutputCameraState().focalPoint;
      app.setOutputCameraState({ focalPoint: [p[0] + x, p[1] + y, p[2] + z] });
    } else app.nudgeSelected(x, y, z);
  };

  return <main>
    <header><div className="brand">NUOMI<span>创作工作室</span></div><div className="breadcrumb">山海拾遗 <span>/</span> 第一集 <span>/</span> 修简铺</div><span className="badge">本地交互演示</span></header>
    <section className="heading"><div><p className="eyebrow">镜头调度</p><h1>先把这场戏摆清楚。</h1><p>真实 3D 编辑器 · 简化人物与场景 · 拖动不会触发生成</p></div><div className="actions"><button disabled={!ready} onClick={save}>保存示意</button><button disabled={!saved} onClick={() => { if (saved) { viewer.current?.loadSceneSnapshot(saved); setStatus('已恢复保存的调度。'); } }}>恢复</button><button className="primary" disabled={!ready} onClick={capture}>截取参考图</button></div></section>
    <section className="workspace">
      <aside><p className="eyebrow">场景对象</p>
        <button disabled={!ready} onClick={() => viewer.current?.selectByIndex('actor', 0)}>人物　步知遥</button>
        <button disabled={!ready} onClick={() => viewer.current?.selectByIndex('staging', 0)}>场景　书案</button>
        <button disabled={!ready} onClick={() => viewer.current?.selectByIndex('prop', 0)}>道具　木匣</button>
        <button disabled={!ready} onClick={() => viewer.current?.selectByIndex('light', 0)}>灯光　窗边主光</button>
        <button disabled={!ready} onClick={() => { viewer.current?.setOutputCameraSelected(true); setCameraSelected(true); setSelection('输出摄影机'); }}>摄影机　输出机位</button>
        <div className="note"><strong>本镜目的</strong><p>保护残简，带上朏朏，准备前往兽医棚。</p><p>人物替身与书案仅用于验证空间关系，不代表最终美术。</p></div>
      </aside>
      <div className="stage"><div className="stage-label"><span>调度视角</span><span>拖动彩色轴 · 右键环视 · 滚轮缩放</span></div><ThreeDStageCanvas splatUrl={null} collisionUrl={null} frameAspect={frameAspect} interactionActive onReady={onReady} onError={e => setStatus(e.message)} onStatus={setStatus}/><div className="stage-footer">当前：{selection}</div></div>
      <aside className="inspector"><p className="eyebrow">局部调整</p><h2>{selection}</h2><p>拖动彩色坐标轴，或按 0.25 米步进。</p>
        {!cameraSelected && <output aria-label="对象位置">{position.map(n => n.toFixed(2)).join(' / ')} m</output>}
        <div className="step-grid">{([[-0.25,0,0,'← 左'],[0.25,0,0,'右 →'],[0,0.25,0,'↑ 上'],[0,-0.25,0,'↓ 下'],[0,0,-0.25,'前移'],[0,0,0.25,'后移']] as const).map(([x,y,z,label]) => <button key={label} disabled={!ready} onClick={() => move(x,y,z)}>{label}</button>)}</div>
        <button disabled={!ready} onClick={() => { const app = viewer.current; if (!app) return; if (cameraSelected) app.setOutputCameraState({ azim: app.getOutputCameraState().azim + 15 }); else app.rotateSelected(15); }}>朝向 +15°</button>
        {cameraSelected && <label>摄影机视角 {Math.round(fov)}°<input aria-label="摄影机视角" type="range" min="20" max="100" value={fov} onChange={e => viewer.current?.setOutputCameraState({ fov: Number(e.target.value) })}/></label>}
        {selection === '窗边主光' && <label>灯光强度 {intensity.toFixed(1)}<input aria-label="灯光强度" type="range" min="0" max="8" step="0.1" value={intensity} onChange={e => viewer.current?.setSelectedLightIntensity(Number(e.target.value))}/></label>}
        <button disabled={!ready} onClick={() => viewer.current?.setOutputCameraFromView()}>用当前视角取景</button>
        <button onClick={() => { setFrameAspect(value => value === '16:9' ? '9:16' : '16:9'); setFrame(null); }}>画幅：{frameAspect} · 切换</button>
        <div className="note"><strong>连续性检查</strong><p>离开前交代木匣去向；角色状态应承接前镜。</p><p>正式生成入口保留在项目画布，本页不提交生产任务。</p></div>
      </aside>
    </section>
    <footer aria-live="polite">{status}</footer>
    {frame && <section className="capture"><h2>调度参考图</h2><img src={frame} alt="当前真实 3D 调度截图，非生成分镜"/><a href={frame} download="nuomi-blocking-reference.png">下载参考图</a></section>}
  </main>;
}

const root = import.meta.hot?.data.root ?? createRoot(document.getElementById('root')!);
if (import.meta.hot) import.meta.hot.data.root = root;
root.render(<StudioDemo />);
