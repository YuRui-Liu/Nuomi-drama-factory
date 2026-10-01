import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import i18next from 'i18next';
import { I18nextProvider, initReactI18next } from 'react-i18next';
import { TechniqueCardPicker } from '@/features/canvas/director/TechniqueCardPicker';
import { DirectorHistory } from '@/features/canvas/director/DirectorHistory';
import type { DirectorAttempt } from '@/features/canvas/domain/canvasNodes';
import { createDirectorDraft } from '@/features/canvas/domain/videoDirectorDraft';
import { techniqueCompatibility, validateDirectorDraft } from '@/features/canvas/director/directorValidation';
import type { DirectorCapabilities, TechniqueCard } from '@/api/videoDirector';

const capabilities: DirectorCapabilities = { models: [{ id: 'minimax-h3', label: 'H3', adapter: 'h3', referenceAdapter: 'h3_ref' }],
  referenceLimit: 5, effectiveReferenceLimit: 5, configuredReferenceLimit: 5, fps: 24, frameStep: 17, frameOffset: 5,
  params: { resolution: ['720p'], aspectRatio: ['9:16'] }, sizes: [], modes: [] };
const card: TechniqueCard = { id: 'ending', version: '1', status: 'active', title: 'Ending', summary: 'End', category: 'ending',
  intent: 'End', content_hash: 'hash', applicability: { modes: ['fl2v'], min_duration_seconds: 5.5,
    max_duration_seconds: 8, last_frame_constraint: 'required' }, sources: [{ url: 'https://example.com',
      credit: 'Source', source_type: 'analysis_only', checked_at: '2026-09-30', basis: 'Example' }] };
const i18n = i18next.createInstance();
await i18n.use(initReactI18next).init({ lng: 'zh', resources: { zh: { translation: {} } } });

describe('director technique compatibility', () => {
  it('shows frozen card, projection and the optimized prompt from history', () => {
    const attempt = { id: 'a1', revision: 1, stage: 'completed', createdAt: 'now',
      snapshot: { segments: [{ id: 's1', prompt: 'Original' }] },
      optimized: { route: 'h3', profileId: 'p', profileVersion: 1,
        segments: [{ segmentId: 's1', prompt: 'Optimized action' }] },
      frozenTechniques: { s1: { card, projection: { intent: 'Frozen direction', content_hash: 'hash' } } },
    } as unknown as DirectorAttempt;
    render(<I18nextProvider i18n={i18n}><DirectorHistory attempts={[attempt]} activeId={null}
      onRetry={vi.fn()} onRefresh={vi.fn()} /></I18nextProvider>);
    fireEvent.click(screen.getByText(/completed|已完成|now/));
    expect(screen.getByText(/Frozen direction/)).toBeInTheDocument();
    expect(screen.getAllByText(/Optimized action/).length).toBeGreaterThan(0);
    expect(screen.getByText(/Ending v1/)).toBeInTheDocument();
  });
  it('shows incompatible choices and source while allowing selected invalid cards to be cleared', () => {
    const segment = createDirectorDraft('s1').segments[0];
    segment.technique = { id: 'ending', version: '1' };
    const onSelect = vi.fn();
    render(<I18nextProvider i18n={i18n}><TechniqueCardPicker segment={segment} hasReferences={true} capabilities={capabilities}
      techniques={[card]} error="techniqueModeUnsupported" onSelect={onSelect} /></I18nextProvider>);
    expect(screen.getByText('Ending')).toBeInTheDocument();
    expect(screen.queryByRole('combobox')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /更换手法/ })).toBeInTheDocument();
    expect(screen.getByRole('alert')).toHaveTextContent('模式');
    expect(screen.queryByRole('link', { name: 'Source' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /清除/ }));
    expect(onSelect).toHaveBeenCalledWith(null);
  });
  it('uses aligned H3 duration and effective input mode', () => {
    const draft = createDirectorDraft('s1');
    const segment = draft.segments[0];
    segment.firstFrame = { imageId: 'first', url: '/first.png' };
    segment.lastFrame = { imageId: 'last', url: '/last.png' };
    segment.durationSeconds = 5.1; // aligns to 5.1667s, still too short
    expect(techniqueCompatibility(card, segment, false, capabilities)).toBe('techniqueDurationShort');
    segment.durationSeconds = 5.4; // aligns to 5.875s
    expect(techniqueCompatibility(card, segment, false, capabilities)).toBeNull();
    expect(techniqueCompatibility(card, segment, true, capabilities)).toBe('techniqueModeUnsupported');
  });

  it('retains invalid selections and blocks them while leaving unselected drafts valid on catalog failure', () => {
    const draft = createDirectorDraft('s1');
    draft.segments[0].prompt = 'A shot';
    draft.references = [{ imageId: 'ref', url: '/ref.png' }];
    expect(validateDirectorDraft(draft, capabilities, null)).toEqual({});
    draft.segments[0].technique = { id: 'ending', version: '1' };
    expect(validateDirectorDraft(draft, capabilities, null)['segments[0].technique']).toBe('techniqueCatalogUnavailable');
    expect(validateDirectorDraft(draft, capabilities, [card])['segments[0].technique']).toBe('techniqueModeUnsupported');
    draft.segments[0].technique = { id: 'unknown', version: '1' };
    expect(validateDirectorDraft(draft, capabilities, [card])['segments[0].technique']).toBe('techniqueUnknown');
  });
});
