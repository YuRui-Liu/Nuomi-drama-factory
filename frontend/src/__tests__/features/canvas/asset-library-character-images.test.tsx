// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { AssetLibraryModal, type AssetLibrarySelection } from '@/features/canvas/ui/AssetLibraryModal';
import { fetchFreezoneVideoCharacterLibrary, syncFreezoneAssetLibraryFromMainline } from '@/api/ops';
import type { FreezoneVideoCharacterLibraryItem } from '@/api/ops';

vi.mock('@/api/ops', () => ({
  fetchFreezoneVideoCharacterLibrary: vi.fn(),
  syncFreezoneAssetLibraryFromMainline: vi.fn(),
  deleteFreezoneVideoCharacterLibraryItem: vi.fn(),
  submitFreezoneAddVideoCharacterLibraryItem: vi.fn(),
  uploadFreezoneImage: vi.fn(),
  uploadFreezoneVideo: vi.fn(),
}));

const characters = [
  {
    id: 'character:alice', name: 'Alice', media: 'image', source: 'character',
    image_urls: ['/alice-base.png'],
    images: [
      { image_id: 'alice-base', character_id: 'alice', kind: 'base', asset_kind: 'portrait', variant_id: null, variant_label: null, url: '/alice-base.png' },
      { image_id: 'alice-coat', character_id: 'alice', kind: 'variant', asset_kind: 'identity_costume', variant_id: 'coat', variant_label: '红外套', url: '/alice-coat.png' },
      { image_id: 'alice-identity', character_id: 'alice', kind: 'variant', asset_kind: 'identity', variant_id: 'coat', variant_label: '红外套', url: '/alice-identity.png' },
    ],
  },
  {
    id: 'character:bob', name: 'Bob', media: 'image', source: 'character',
    image_urls: ['/bob-base.png'],
    images: [{ image_id: 'bob-base', character_id: 'bob', kind: 'base', asset_kind: 'portrait', variant_id: null, variant_label: null, url: '/bob-base.png' }],
  },
] satisfies FreezoneVideoCharacterLibraryItem[];

function show(props: Partial<React.ComponentProps<typeof AssetLibraryModal>> = {}) {
  const onConfirm = vi.fn<(selections: AssetLibrarySelection[]) => void>();
  const result = render(<AssetLibraryModal open project="demo" onClose={vi.fn()} onConfirm={onConfirm} {...props} />);
  return { ...result, onConfirm };
}

async function openCharacter(name: string) {
  fireEvent.click(await screen.findByRole('button', { name: `查看${name}的图片` }));
}

describe('AssetLibraryModal character images', () => {
  beforeEach(() => {
    vi.mocked(fetchFreezoneVideoCharacterLibrary).mockResolvedValue(characters);
    vi.mocked(syncFreezoneAssetLibraryFromMainline).mockResolvedValue(characters);
  });

  it('selects exact base and variant images, preserving checks through back navigation', async () => {
    const { onConfirm } = show();
    await openCharacter('Alice');
    fireEvent.click(screen.getByRole('checkbox', { name: 'Alice 基础图 portrait' }));
    fireEvent.click(screen.getByRole('checkbox', { name: 'Alice 红外套 identity_costume' }));
    fireEvent.click(screen.getByRole('button', { name: '返回素材库' }));
    expect(screen.getByRole('status', { name: '已选 2/9' })).toBeInTheDocument();
    await openCharacter('Alice');
    expect(screen.getByRole('checkbox', { name: 'Alice 基础图 portrait' })).toBeChecked();
    expect(screen.getByRole('checkbox', { name: 'Alice 红外套 identity_costume' })).toBeChecked();
    fireEvent.click(screen.getByRole('button', { name: '确定' }));
    expect(onConfirm).toHaveBeenCalledWith([
      expect.objectContaining({ imageId: 'alice-base', assetId: 'character:alice', characterId: 'alice', url: '/alice-base.png', assetKind: 'portrait' }),
      expect.objectContaining({ imageId: 'alice-coat', assetId: 'character:alice', characterId: 'alice', variantId: 'coat', variantLabel: '红外套', url: '/alice-coat.png', assetKind: 'identity_costume' }),
    ]);
  });

  it('selects across characters and distinguishes image kinds for the same variant', async () => {
    const { onConfirm } = show();
    await openCharacter('Alice');
    fireEvent.click(screen.getByRole('checkbox', { name: 'Alice 红外套 identity' }));
    fireEvent.click(screen.getByRole('button', { name: '返回素材库' }));
    await openCharacter('Bob');
    fireEvent.click(screen.getByRole('checkbox', { name: 'Bob 基础图 portrait' }));
    fireEvent.click(screen.getByRole('button', { name: '确定' }));
    expect(onConfirm.mock.calls[0][0].map((item) => item.imageId)).toEqual(['alice-identity', 'bob-base']);
  });

  it('blocks extras at the maximum but permits unselecting', async () => {
    const { onConfirm } = show({ maxSelectable: 1 });
    await openCharacter('Alice');
    fireEvent.click(screen.getByRole('checkbox', { name: 'Alice 基础图 portrait' }));
    expect(screen.getByRole('checkbox', { name: 'Alice 红外套 identity_costume' })).toBeDisabled();
    fireEvent.click(screen.getByRole('checkbox', { name: 'Alice 基础图 portrait' }));
    expect(screen.getByRole('checkbox', { name: 'Alice 红外套 identity_costume' })).toBeEnabled();
    fireEvent.click(screen.getByRole('checkbox', { name: 'Alice 红外套 identity_costume' }));
    fireEvent.click(screen.getByRole('button', { name: '确定' }));
    expect(onConfirm.mock.calls[0][0].map((item) => item.imageId)).toEqual(['alice-coat']);
  });

  it('replaces a prior choice in single mode and restores it when reopened', async () => {
    const { onConfirm, rerender } = show({ selectionMode: 'single' });
    await openCharacter('Alice');
    fireEvent.click(screen.getByRole('checkbox', { name: 'Alice 基础图 portrait' }));
    fireEvent.click(screen.getByRole('checkbox', { name: 'Alice 红外套 identity_costume' }));
    fireEvent.click(screen.getByRole('button', { name: '确定' }));
    expect(onConfirm.mock.calls[0][0].map((item) => item.imageId)).toEqual(['alice-coat']);
    rerender(<AssetLibraryModal open={false} project="demo" onClose={vi.fn()} onConfirm={onConfirm} selectionMode="single" />);
    rerender(<AssetLibraryModal open project="demo" onClose={vi.fn()} onConfirm={onConfirm} selectionMode="single" initialSelections={onConfirm.mock.calls[0][0]} />);
    await openCharacter('Alice');
    await waitFor(() => expect(screen.getByRole('checkbox', { name: 'Alice 红外套 identity_costume' })).toBeChecked());
  });

  it('keeps legacy character cards selectable by their cover', async () => {
    vi.mocked(syncFreezoneAssetLibraryFromMainline).mockResolvedValue([{ id: 'legacy', name: 'Old', media: 'image', source: 'character', image_urls: ['/old.png'] }]);
    const { onConfirm } = show();
    fireEvent.click(await screen.findByRole('button', { name: '选择Old' }));
    fireEvent.click(screen.getByRole('button', { name: '确定' }));
    expect(onConfirm).toHaveBeenCalledWith([expect.objectContaining({ url: '/old.png', name: 'Old' })]);
  });

  it('omits missing child URLs and exposes an empty state when none are usable', async () => {
    vi.mocked(syncFreezoneAssetLibraryFromMainline).mockResolvedValue([{ id: 'empty', name: 'Empty', media: 'image', source: 'character', cover_url: '/cover.png', images: [{ image_id: 'missing', character_id: 'empty', kind: 'base', asset_kind: 'portrait', variant_id: null, variant_label: null, url: '' }] }]);
    show();
    await openCharacter('Empty');
    expect(screen.getByText('暂无可用图片')).toBeInTheDocument();
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
  });

  it('deduplicates repeated child image IDs', async () => {
    vi.mocked(syncFreezoneAssetLibraryFromMainline).mockResolvedValue([
      { ...characters[0], images: [...characters[0].images, characters[0].images[0]] },
    ]);
    const { onConfirm } = show();
    await openCharacter('Alice');
    expect(screen.getAllByRole('checkbox', { name: 'Alice 基础图 portrait' })).toHaveLength(1);
    fireEvent.click(screen.getByRole('checkbox', { name: 'Alice 基础图 portrait' }));
    fireEvent.click(screen.getByRole('button', { name: '确定' }));
    expect(onConfirm.mock.calls[0][0].map((item) => item.imageId)).toEqual(['alice-base']);
  });
});
