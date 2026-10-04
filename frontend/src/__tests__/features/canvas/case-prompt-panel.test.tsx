import { expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { CasePromptPanel } from '@/features/technique-library/CasePromptPanel';
import { CaseMedia } from '@/features/technique-library/CaseMedia';
import type { CasePrompt } from '@/api/techniqueLibrary';

const prompt: CasePrompt = { text: '原创测试镜头。\n\n人物回望。', language: 'zh', provenance: 'author',
  repository: 'example/cases', revision: 'a'.repeat(40), path: 'prompt.json', upstream_id: 'one',
  source_url: 'https://example.com/source', content_sha256: 'b'.repeat(64) };

it('switches source bodies and copies exact complete text', async () => {
  const user = userEvent.setup();
  const copy = vi.spyOn(navigator.clipboard, 'writeText').mockResolvedValue();
  render(<CasePromptPanel prompts={[prompt, { ...prompt, text: '重构测试。', provenance: 'reconstructed', repository: 'other/cases' }]} />);
  await user.click(screen.getByRole('button', { name: '复制完整提示词' }));
  expect(copy).toHaveBeenCalledWith(prompt.text);
  expect(await screen.findByText('已复制')).toBeInTheDocument();
  await user.selectOptions(screen.getByLabelText('提示词来源'), '1');
  expect(screen.getByText('重构测试。')).toBeInTheDocument();
  expect(screen.getByText('反推重构')).toBeInTheDocument();
});

it('shows copy failure without claiming success', async () => {
  const user = userEvent.setup();
  vi.spyOn(navigator.clipboard, 'writeText').mockRejectedValue(new Error('denied'));
  render(<CasePromptPanel prompts={[prompt]} />);
  await user.click(screen.getByRole('button', { name: '复制完整提示词' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('复制失败');
});

it('handles legacy records without inventing a prompt', () => {
  render(<CasePromptPanel />);
  expect(screen.getByText('此案例暂未收录原提示词，请查看原始出处。')).toBeInTheDocument();
  expect(screen.queryByRole('button')).not.toBeInTheDocument();
});

it('keeps source link after media fails and never embeds a webpage as video', () => {
  const { container, rerender } = render(<CaseMedia url="https://example.com/clip.mp4" />);
  fireEvent.error(container.querySelector('video')!);
  expect(screen.getByText('预览加载失败，可打开媒体来源查看。')).toBeInTheDocument();
  expect(screen.getByRole('link')).toHaveAttribute('href', 'https://example.com/clip.mp4');
  rerender(<CaseMedia url="https://example.com/post" />);
  expect(container.querySelector('video')).toBeNull();
});
