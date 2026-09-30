import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { DirectorImageSlot } from '@/features/canvas/director/DirectorImageSlot';
import { useDirectorImageActions, type DirectorImageTarget } from '@/features/canvas/director/useDirectorImageActions';
import { createDirectorDraft } from '@/features/canvas/domain/videoDirectorDraft';
import { CANVAS_NODE_TYPES, type VideoDirectorNodeData } from '@/features/canvas/domain/canvasNodes';
import { useCanvasStore } from '@/stores/canvasStore';

const uploadFreezoneImage = vi.hoisted(() => vi.fn());
vi.mock('@/api/ops', () => ({ uploadFreezoneImage }));

const frame: DirectorImageTarget = { kind: 'frame', segmentId: 's1', field: 'firstFrame' };
const refs: DirectorImageTarget = { kind: 'references' };
let actions: ReturnType<typeof useDirectorImageActions>;
function Harness({ target = frame }: { target?: DirectorImageTarget }) {
  actions = useDirectorImageActions('director', 2);
  const data = useCanvasStore((state) => state.nodes.find((node) => node.id === 'director')?.data as VideoDirectorNodeData);
  const image = target.kind === 'frame' ? data.draft.segments[0].firstFrame : data.draft.references[0] ?? null;
  return <DirectorImageSlot label="首帧" image={image} error={actions.errors[actions.slotKey(target)]}
    uploading={actions.uploading[actions.slotKey(target)]} onPick={() => actions.openPicker(target)}
    onUpload={(file) => void actions.uploadFile(target, file)} onRemove={() => actions.removeImage(target, image?.imageId)} />;
}
function data(): VideoDirectorNodeData {
  const draft = createDirectorDraft('s1');
  draft.segments[0].firstFrame = { imageId: 'old', url: '/old.png' };
  return { draft, activeInputMode: 'frames', activeAttemptId: null, videoUrl: null, resultRevision: null };
}
function current() { return useCanvasStore.getState().nodes.find((node) => node.id === 'director')!.data as VideoDirectorNodeData; }

describe('director image actions', () => {
  beforeEach(() => {
    uploadFreezoneImage.mockReset();
    window.history.replaceState({}, '', '/projects/demo/freezone');
    useCanvasStore.getState().setCanvasData([{ id: 'director', type: CANVAS_NODE_TYPES.videoDirector,
      position: { x: 0, y: 0 }, data: data() }], []);
  });

  it('keeps old frame until dropped image upload succeeds, then stores persistent URL', async () => {
    let resolve!: (value: { url: string }) => void;
    uploadFreezoneImage.mockReturnValue(new Promise((done) => { resolve = done; }));
    render(<Harness />);
    const file = new File(['image'], 'shot.png', { type: 'image/png' });
    fireEvent.dragOver(screen.getByLabelText('首帧'));
    fireEvent.drop(screen.getByLabelText('首帧'), { dataTransfer: { files: [file] } });
    expect(uploadFreezoneImage).toHaveBeenCalledWith('demo', file, 'shot.png');
    expect(current().draft.segments[0].firstFrame?.url).toBe('/old.png');
    expect(screen.getByText('上传中…')).toBeInTheDocument();
    await act(async () => resolve({ url: '/uploaded.png' }));
    expect(current().draft.segments[0].firstFrame).toEqual({ imageId: '/uploaded.png', url: '/uploaded.png' });
    expect(current().draft.revision).toBe(1);
    expect(current().activeInputMode).toBe('frames');
  });

  it('keeps old frame and displays field error when upload fails', async () => {
    uploadFreezoneImage.mockRejectedValue(new Error('upload failed'));
    render(<Harness />);
    fireEvent.drop(screen.getByLabelText('首帧'), { dataTransfer: { files: [new File(['x'], 'x.png', { type: 'image/png' })] } });
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('upload failed'));
    expect(current().draft.segments[0].firstFrame?.imageId).toBe('old');
    expect(current().draft.revision).toBe(0);
  });

  it('ignores non-image drops without uploading', () => {
    render(<Harness />);
    fireEvent.drop(screen.getByLabelText('首帧'), { dataTransfer: { files: [new File(['x'], 'x.txt', { type: 'text/plain' })] } });
    expect(uploadFreezoneImage).not.toHaveBeenCalled();
  });

  it('preserves character variant identity from library selection', () => {
    render(<Harness />);
    act(() => actions.openPicker(frame));
    expect(actions.libraryProps?.selectionMode).toBe('single');
    act(() => actions.libraryProps?.onConfirm?.([{ media: 'image', url: '/variant.png', name: 'variant', imageId: 'image-2',
      assetId: 'asset-1', characterId: 'character-1', variantId: 'variant-2', variantLabel: 'Side', assetKind: 'identity_costume' }]));
    expect(current().draft.segments[0].firstFrame).toMatchObject({ imageId: 'image-2', assetId: 'asset-1',
      characterId: 'character-1', variantId: 'variant-2', variantLabel: 'Side', url: '/variant.png' });
  });

  it('deduplicates reference selections, enforces limit, and switches mode after final removal', () => {
    render(<Harness target={refs} />);
    act(() => actions.openPicker(refs));
    expect(actions.libraryProps?.selectionMode).toBe('multiple');
    act(() => actions.libraryProps?.onConfirm?.(['a', 'a', 'b', 'c'].map((id) => ({ media: 'image', url: `/${id}.png`, name: id, imageId: id }))));
    expect(current().draft.references.map((image) => image.imageId)).toEqual(['a', 'b']);
    expect(current().activeInputMode).toBe('ref');
    act(() => actions.removeImage(refs, 'a'));
    act(() => actions.removeImage(refs, 'b'));
    expect(current().draft.references).toEqual([]);
    expect(current().activeInputMode).toBe('frames');
  });

  it('does not revise a full reference slot when an additional upload completes', async () => {
    const initial = data();
    initial.draft.references = [{ imageId: 'a', url: '/a.png' }, { imageId: 'b', url: '/b.png' }];
    useCanvasStore.getState().updateNodeData('director', initial);
    uploadFreezoneImage.mockResolvedValue({ url: '/c.png' });
    render(<Harness target={refs} />);
    await act(async () => actions.uploadFile(refs, new File(['x'], 'c.png', { type: 'image/png' })));
    expect(current().draft.references.map((image) => image.imageId)).toEqual(['a', 'b']);
    expect(current().draft.revision).toBe(0);
  });

  it('does not write an upload into a different canvas route', async () => {
    let resolve!: (value: { url: string }) => void;
    uploadFreezoneImage.mockReturnValue(new Promise((done) => { resolve = done; }));
    render(<Harness />);
    let pending!: Promise<void>;
    act(() => { pending = actions.uploadFile(frame, new File(['x'], 'x.png', { type: 'image/png' })); });
    window.history.replaceState({}, '', '/projects/demo/freezone/another-canvas');
    await act(async () => { resolve({ url: '/new.png' }); await pending; });
    expect(current().draft.segments[0].firstFrame?.imageId).toBe('old');
  });
});
