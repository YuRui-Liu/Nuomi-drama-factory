import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
const mocks = vi.hoisted(() => ({ save: vi.fn(), list: vi.fn() }));
vi.mock('@/lib/queries/characters', () => ({ useCharacters: () => ({ data: { data: [{ name: '项目演员' }] } }) }));
vi.mock('@/lib/api', () => ({ api: { get: () => ({ json: async () => ({ data: [] }) }) } }));
vi.mock('../studio-api', () => ({ listStudioDocuments: mocks.list, saveStudioDocument: mocks.save }));
vi.mock('./stage', () => ({ PrevisStage: ({ canvasRef, onStroke }: { canvasRef: React.Ref<HTMLCanvasElement>; onStroke?: (points: number[][]) => void }) => <><canvas ref={canvasRef} aria-label="3D test stage" />{onStroke && <button onClick={()=>onStroke([[0,0,0],[2,0,0],[4,0,3]])}>测试绘制路线</button>}</> }));
import { PrevisStudio } from './previs-studio';

beforeEach(() => { localStorage.clear(); mocks.list.mockResolvedValue([]); mocks.save.mockResolvedValue({ revision: 1 }); });
afterEach(() => { vi.unstubAllGlobals(); });
it('requires actor selection, protects unadapted project actors, and saves editable action data', async () => {
  render(<PrevisStudio project="test" />);
  expect(screen.getByRole('button', { name: '+ 走' })).toBeDisabled();
  fireEvent.change(screen.getByLabelText('添加项目角色'), { target: { value: '项目演员' } });
  expect(screen.getByRole('button', { name: '+ 走' })).toBeDisabled();
  fireEvent.click(screen.getByLabelText('确认适配人形动作'));
  fireEvent.click(screen.getByRole('button', { name: '+ 走' }));
  fireEvent.change(screen.getByLabelText('动作时长秒'), { target: { value: '3' } });
  fireEvent.click(screen.getByRole('button', { name: /保存版本/ }));
  await waitFor(() => expect(mocks.save).toHaveBeenCalled());
  expect(mocks.save.mock.calls[mocks.save.mock.calls.length - 1][3].data.clips[0]).toMatchObject({ action: 'walk', duration: 3 });
});
it('refuses negative times and emits save failure without discarding edits', async () => {
  mocks.save.mockRejectedValueOnce(new Error('409 conflict'));
  const failed = vi.fn(); window.addEventListener('studio-save-failed-previs', failed);
  render(<PrevisStudio project="other" />);
  fireEvent.change(screen.getByLabelText('简化演员名称'), { target: { value: '自建演员' } });
  fireEvent.click(screen.getByRole('button', { name: '创建简化人形演员' }));
  fireEvent.click(screen.getByRole('button', { name: '+ 走' }));
  fireEvent.change(screen.getByLabelText('动作开始秒'), { target: { value: '-1' } });
  expect(screen.getByRole('alert')).toHaveTextContent('开始时间不能小于 0');
  expect(screen.getByLabelText('动作开始秒')).toHaveValue(0);
  window.dispatchEvent(new Event('studio-save-previs'));
  await waitFor(() => expect(failed).toHaveBeenCalled());
  expect(screen.getByRole('alert')).toHaveTextContent('409 conflict');
  expect(screen.getByLabelText('动作时长秒')).toHaveValue(2);
  window.removeEventListener('studio-save-failed-previs', failed);
});
it('keeps later edits dirty if a prior save finishes', async () => {
  let finish!: (value: { revision: number }) => void;
  mocks.save.mockReturnValueOnce(new Promise(resolve => { finish = resolve; }));
  render(<PrevisStudio project="concurrent" />);
  fireEvent.change(screen.getByLabelText('预演名称'), { target: { value: '先保存' } });
  fireEvent.click(screen.getByRole('button', { name: /保存版本/ }));
  fireEvent.change(screen.getByLabelText('预演名称'), { target: { value: '保存过程中继续编辑' } });
  await act(async () => finish({ revision: 1 }));
  expect(screen.getByRole('button', { name: '保存版本 *' })).toBeInTheDocument();
});
it('restores the selected saved draft for the project and source', async () => {
  const { emptyScene } = await import('./model');
  localStorage.setItem('previs-selection:restore:direct', 'saved');
  mocks.list.mockResolvedValueOnce([{ id: 'saved', name: '已保存预演', data: emptyScene(), revision: 4 }]);
  render(<PrevisStudio project="restore" />);
  await waitFor(() => expect(screen.getByLabelText('预演名称')).toHaveValue('已保存预演'));
  expect(screen.getByText('当前版本 v4')).toBeInTheDocument();
});
it('locks draft changes from the start of recording and cleans media tracks on unmount', () => {
  const stopTrack = vi.fn();
  class Recorder {
    static isTypeSupported() { return true; }
    state = 'inactive';
    stream = { getTracks: () => [{ stop: stopTrack }] };
    start() { this.state = 'recording'; }
    stop() { this.state = 'inactive'; }
  }
  vi.stubGlobal('MediaRecorder', Recorder);
  Object.defineProperty(HTMLCanvasElement.prototype, 'captureStream', { configurable: true, value: () => ({ getTracks: () => [{ stop: stopTrack }] }) });
  const { unmount } = render(<PrevisStudio project="recording" />);
  fireEvent.click(screen.getByRole('button', { name: '导出白模动作视频' }));
  expect(screen.getByLabelText('加载预演草稿')).toBeDisabled();
  fireEvent.change(screen.getByLabelText('加载预演草稿'), { target: { value: 'another' } });
  expect(screen.getByRole('alert')).toHaveTextContent('录制期间不能切换');
  unmount();
  expect(stopTrack).toHaveBeenCalled();
});
it('lets users edit parsed actions before adoption', () => {
  render(<PrevisStudio project="proposal" />);
  fireEvent.change(screen.getByLabelText('简化演员名称'), { target: { value: '演员' } });
  fireEvent.click(screen.getByRole('button', { name: '创建简化人形演员' }));
  fireEvent.change(screen.getByLabelText('快速动作指令'), { target: { value: '走 3 秒 到 4,2' } });
  fireEvent.click(screen.getByRole('button', { name: '解析为可编辑动作' }));
  fireEvent.change(screen.getByLabelText('建议1-duration'), { target: { value: '4' } });
  expect(screen.queryByLabelText('动作时长秒')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '采纳已编辑动作' }));
  expect(screen.getByLabelText('动作时长秒')).toHaveValue(4);
});
it('requires applying the drawn route before it changes the saved camera track', async () => {
  render(<PrevisStudio project="drawing" />);
  fireEvent.click(screen.getByRole('button', {name:'画线运镜'}));
  fireEvent.click(screen.getByRole('button', {name:'测试绘制路线'}));
  fireEvent.click(screen.getByRole('button', {name:'保存版本'}));
  await waitFor(()=>expect(mocks.save).toHaveBeenCalled());
  expect(mocks.save.mock.calls[mocks.save.mock.calls.length-1][3].data.camera).toHaveLength(1);
  fireEvent.click(screen.getByRole('button', {name:'应用画线路径'}));
  await waitFor(()=>expect(screen.getByRole('button', {name:'保存版本 *'})).toBeEnabled());
  fireEvent.click(screen.getByRole('button', {name:'保存版本 *'}));
  await waitFor(()=>expect(mocks.save.mock.calls[mocks.save.mock.calls.length-1][3].data.camera).toHaveLength(3));
});
