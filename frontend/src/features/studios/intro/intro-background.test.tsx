import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import { IntroBackground } from './intro-background';

const mocks = vi.hoisted(() => ({ request: vi.fn(), quote: vi.fn() }));
vi.mock('./intro-api', () => ({ introRequest: mocks.request, introCreditCost: mocks.quote, introMedia: (_p: string, path: string) => path }));
beforeEach(() => {
  vi.clearAllMocks();
  mocks.quote.mockResolvedValue({cost:12, display:'12'});
  mocks.request.mockImplementation((_p, route, body) => Promise.resolve(route === 'background-models' ? [{id:'actual-model',label:'Actual Model',provider:'GRSAI',account:'main'}] : body ? {id:body.request_id, input:body, status:'completed', output:'generated.png', task_id:'task-real', provider:'GRSAI'} : []));
});

it('shows live platform and quote, generates once and explicitly adopts the returned image', async () => {
  const onUse = vi.fn();
  render(<IntroBackground project="p" ratio="16:9" onUse={onUse} />);
  expect(screen.getByText('可选 AI 背景').closest('details')).not.toHaveAttribute('open');
  fireEvent.click(screen.getByText('可选 AI 背景'));
  await screen.findByText(/12 平台积分/);
  expect(screen.getByText(/GRSAI/)).toBeVisible();
  fireEvent.change(screen.getByLabelText('AI 背景描述'), {target:{value:'海边黎明，不含文字'}});
  fireEvent.click(screen.getByRole('button', {name:/生成一张 AI 背景/}));
  await screen.findByText(/task-real/);
  expect(onUse).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', {name:'使用此背景'}));
  expect(onUse).toHaveBeenCalledWith('generated.png');
  expect(mocks.request.mock.calls.filter(call => call[1] === 'background-jobs' && call[2])).toHaveLength(1);
});

it('does not invent a model when the actual catalog is empty', async () => {
  mocks.request.mockResolvedValue([]);
  render(<IntroBackground project="p" ratio="1:1" onUse={vi.fn()} />);
  await waitFor(() => expect(screen.getByRole('button', {name:/生成一张 AI 背景/})).toBeDisabled());
  expect(mocks.quote).not.toHaveBeenCalled();
});

it('does not present the platform zero quote as a free supplier generation', async () => {
  mocks.quote.mockResolvedValue({cost:0, display:'0'});
  render(<IntroBackground project="p" ratio="16:9" onUse={vi.fn()} />);
  await screen.findByText(/应用平台计价接口返回 0 积分；图像服务费用尚未确认/);
  expect(screen.queryByText(/预计 0 平台积分/)).not.toBeInTheDocument();
});
