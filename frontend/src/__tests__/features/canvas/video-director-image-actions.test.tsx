import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { DirectorImageSlot } from '@/features/canvas/director/DirectorImageSlot';
import { useDirectorImageActions, type DirectorImageTarget } from '@/features/canvas/director/useDirectorImageActions';
import { createDirectorDraft } from '@/features/canvas/domain/videoDirectorDraft';
import { CANVAS_NODE_TYPES, type VideoDirectorNodeData } from '@/features/canvas/domain/canvasNodes';
import { useCanvasStore } from '@/stores/canvasStore';
import { VideoDirectorPanel } from '@/features/canvas/director/VideoDirectorPanel';
import type { useVideoDirectorTask } from '@/features/canvas/director/useVideoDirectorTask';

const uploadFreezoneImage = vi.hoisted(() => vi.fn());
vi.mock('@/api/ops', () => ({ uploadFreezoneImage }));
vi.mock('@/features/canvas/ui/AssetLibraryModal', () => ({ AssetLibraryModal: () => null }));
vi.mock('react-i18next', () => ({ useTranslation: () => ({
  t: (key: string, options?: { defaultValue?: string; label?: string }) => ({
    'node.videoDirector.imageSlot.preview': `${options?.label}预览`,
    'node.videoDirector.imageSlot.select': '选择图片',
    'node.videoDirector.imageSlot.upload': '上传图片',
    'node.videoDirector.imageSlot.remove': '移除',
    'node.videoDirector.imageSlot.uploadLabel': `${options?.label}上传图片`,
    'node.videoDirector.imageSlot.dropHint': '可拖入图片',
    'node.videoDirector.imageSlot.uploading': '上传中…',
    'node.videoDirector.errors.referenceUploadLimit': `参考图已达到上限（${(options as { count?: number })?.count} 张）`,
    'node.videoDirector.errors.projectMissing': '缺少项目，无法上传图片',
    'node.videoDirector.errors.uploadFailed': '图片上传失败',
  } as Record<string, string>)[key] ?? options?.defaultValue ?? key,
}) }));

const frame: DirectorImageTarget = { kind: 'frame', segmentId: 's1', field: 'firstFrame' };
const refs: DirectorImageTarget = { kind: 'references' };
const refA: DirectorImageTarget = { kind: 'reference', imageId: 'a' };
const refB: DirectorImageTarget = { kind: 'reference', imageId: 'b' };
let actions: ReturnType<typeof useDirectorImageActions>;
let cardActions: ReturnType<typeof useDirectorImageActions>;
let panelActions: ReturnType<typeof useDirectorImageActions>;
function TwoEditors() {
  cardActions = useDirectorImageActions('director', 2);
  panelActions = useDirectorImageActions('director', 2);
  return null;
}
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
function PanelHarness({ fieldErrors = {} }: { fieldErrors?: Record<string, string> }) {
  const node = useCanvasStore((state) => state.nodes.find((item) => item.id === 'director'))!;
  const task = { capabilities: { effectiveReferenceLimit: 2, models: [], params: { aspectRatio: [], resolution: [] } },
    attempts: [], error: '', fieldErrors, generate: vi.fn(), recoverPending: vi.fn(), retry: vi.fn(), refresh: vi.fn() };
  return <VideoDirectorPanel nodeId="director" data={node.data as VideoDirectorNodeData} task={task as unknown as ReturnType<typeof useVideoDirectorTask>}
    onDraftChange={(draft) => useCanvasStore.getState().updateNodeData('director', { draft })} onClose={vi.fn()} />;
}

describe('director image actions', () => {
  beforeEach(() => {
    uploadFreezoneImage.mockReset();
    window.history.replaceState({}, '', '/projects/demo/freezone');
    useCanvasStore.getState().setCanvasData([{ id: 'director', type: CANVAS_NODE_TYPES.videoDirector,
      position: { x: 0, y: 0 }, data: data() }], []);
  });

  it('completes independent reference A and B uploads from separate editors', async () => {
    const draft = current().draft;
    useCanvasStore.getState().updateNodeData('director', { draft: { ...draft, references: [
      { imageId: 'a', url: '/a.png' }, { imageId: 'b', url: '/b.png' },
    ] } });
    let finishA!: (value: { url: string }) => void;
    let finishB!: (value: { url: string }) => void;
    uploadFreezoneImage.mockImplementation((_project, file: File) => new Promise((resolve) => {
      if (file.name === 'a-new.png') finishA = resolve;
      else finishB = resolve;
    }));
    render(<TwoEditors />);
    let uploadA!: Promise<void>;
    let uploadB!: Promise<void>;
    act(() => {
      uploadA = cardActions.uploadFile(refA, new File(['a'], 'a-new.png', { type: 'image/png' }));
      uploadB = panelActions.uploadFile(refB, new File(['b'], 'b-new.png', { type: 'image/png' }));
    });
    await act(async () => { finishB({ url: '/b-new.png' }); await uploadB; });
    await act(async () => { finishA({ url: '/a-new.png' }); await uploadA; });
    expect(current().draft.references.map((image) => image.url)).toEqual(['/a-new.png', '/b-new.png']);
    expect(current().draft.revision).toBe(2);
  });

  it('keeps a newer choice for the same reference when an older upload completes', async () => {
    const draft = current().draft;
    useCanvasStore.getState().updateNodeData('director', { draft: { ...draft, references: [{ imageId: 'a', url: '/a.png' }] } });
    let finish!: (value: { url: string }) => void;
    uploadFreezoneImage.mockReturnValue(new Promise((resolve) => { finish = resolve; }));
    render(<TwoEditors />);
    let upload!: Promise<void>;
    act(() => { upload = cardActions.uploadFile(refA, new File(['old'], 'old.png', { type: 'image/png' })); });
    act(() => panelActions.commitDirectorSlot(refA, [{ imageId: 'new', url: '/new.png' }]));
    await act(async () => { finish({ url: '/old.png' }); await upload; });
    expect(current().draft.references).toEqual([{ imageId: 'new', url: '/new.png' }]);
    expect(current().draft.revision).toBe(1);
  });

  it('allows a whole-list edit that preserves A while its replacement uploads', async () => {
    const draft = current().draft;
    useCanvasStore.getState().updateNodeData('director', { draft: { ...draft, references: [
      { imageId: 'a', url: '/a.png' }, { imageId: 'b', url: '/b.png' },
    ] } });
    let finish!: (value: { url: string }) => void;
    uploadFreezoneImage.mockReturnValue(new Promise((resolve) => { finish = resolve; }));
    render(<TwoEditors />);
    let upload!: Promise<void>;
    act(() => { upload = cardActions.uploadFile(refA, new File(['a'], 'a-new.png', { type: 'image/png' })); });
    act(() => panelActions.commitDirectorSlot(refs, [{ imageId: 'a', url: '/a.png' }, { imageId: 'c', url: '/c.png' }]));
    await act(async () => { finish({ url: '/a-new.png' }); await upload; });
    expect(current().draft.references.map((image) => image.url)).toEqual(['/a-new.png', '/c.png']);
    expect(current().draft.revision).toBe(2);
  });

  it('rejects an older A upload when a whole-list edit changes A itself', async () => {
    const draft = current().draft;
    useCanvasStore.getState().updateNodeData('director', { draft: { ...draft, references: [{ imageId: 'a', url: '/a.png' }] } });
    let finish!: (value: { url: string }) => void;
    uploadFreezoneImage.mockReturnValue(new Promise((resolve) => { finish = resolve; }));
    render(<TwoEditors />);
    let upload!: Promise<void>;
    act(() => { upload = cardActions.uploadFile(refA, new File(['old'], 'old.png', { type: 'image/png' })); });
    act(() => panelActions.commitDirectorSlot(refs, [{ imageId: 'a', url: '/a-selected.png' }]));
    await act(async () => { finish({ url: '/old.png' }); await upload; });
    expect(current().draft.references).toEqual([{ imageId: 'a', url: '/a-selected.png' }]);
    expect(current().draft.revision).toBe(1);
  });

  it('keeps a newer panel selection when an older card upload finishes later', async () => {
    let finish!: (value: { url: string }) => void;
    uploadFreezoneImage.mockReturnValue(new Promise((resolve) => { finish = resolve; }));
    render(<TwoEditors />);
    let upload!: Promise<void>;
    act(() => { upload = cardActions.uploadFile(frame, new File(['old'], 'old.png', { type: 'image/png' })); });
    act(() => panelActions.commitDirectorSlot(frame, [{ imageId: 'new', url: '/new.png' }]));
    await act(async () => { finish({ url: '/old.png' }); await upload; });
    expect(current().draft.segments[0].firstFrame).toEqual({ imageId: 'new', url: '/new.png' });
    expect(current().draft.revision).toBe(1);
  });

  it('keeps a newer panel upload when an older card upload finishes later', async () => {
    let finishOld!: (value: { url: string }) => void;
    let finishNew!: (value: { url: string }) => void;
    uploadFreezoneImage.mockImplementation((_project, file: File) => new Promise((resolve) => {
      if (file.name === 'old.png') finishOld = resolve;
      else finishNew = resolve;
    }));
    render(<TwoEditors />);
    let oldUpload!: Promise<void>;
    let newUpload!: Promise<void>;
    act(() => {
      oldUpload = cardActions.uploadFile(frame, new File(['old'], 'old.png', { type: 'image/png' }));
      newUpload = panelActions.uploadFile(frame, new File(['new'], 'new.png', { type: 'image/png' }));
    });
    await act(async () => { finishNew({ url: '/new.png' }); await newUpload; });
    await act(async () => { finishOld({ url: '/old.png' }); await oldUpload; });
    expect(current().draft.segments[0].firstFrame).toEqual({ imageId: '/new.png', url: '/new.png' });
    expect(current().draft.revision).toBe(1);
  });

  it('keeps a panel removal when an older card upload finishes later', async () => {
    let finish!: (value: { url: string }) => void;
    uploadFreezoneImage.mockReturnValue(new Promise((resolve) => { finish = resolve; }));
    render(<TwoEditors />);
    let upload!: Promise<void>;
    act(() => { upload = cardActions.uploadFile(frame, new File(['old'], 'old.png', { type: 'image/png' })); });
    act(() => panelActions.removeImage(frame));
    await act(async () => { finish({ url: '/old.png' }); await upload; });
    expect(current().draft.segments[0].firstFrame).toBeNull();
    expect(current().draft.revision).toBe(1);
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

  it('rejects an add-reference upload before transfer when the limit is full', async () => {
    const initial = data();
    initial.draft.references = [{ imageId: 'a', url: '/a.png' }, { imageId: 'b', url: '/b.png' }];
    useCanvasStore.getState().updateNodeData('director', initial);
    render(<Harness target={refs} />);
    await act(async () => actions.uploadFile(refs, new File(['x'], 'c.png', { type: 'image/png' })));
    expect(uploadFreezoneImage).not.toHaveBeenCalled();
    expect(screen.getByRole('alert')).toHaveTextContent('上限');
    expect(current().draft.references.map((image) => image.imageId)).toEqual(['a', 'b']);
    expect(current().draft.revision).toBe(0);
  });

  it('rejects an add-reference result if another image fills the slot during upload', async () => {
    const initial = data();
    initial.draft.references = [{ imageId: 'a', url: '/a.png' }];
    useCanvasStore.getState().updateNodeData('director', initial);
    let resolve!: (value: { url: string }) => void;
    uploadFreezoneImage.mockReturnValue(new Promise((done) => { resolve = done; }));
    render(<Harness target={refs} />);
    let pending!: Promise<void>;
    act(() => { pending = actions.uploadFile(refs, new File(['x'], 'c.png', { type: 'image/png' })); });
    act(() => useCanvasStore.getState().updateNodeData('director', { draft: {
      ...current().draft, revision: 1, references: [...current().draft.references, { imageId: 'b', url: '/b.png' }],
    } }));
    await act(async () => { resolve({ url: '/c.png' }); await pending; });
    expect(current().draft.references.map((image) => image.imageId)).toEqual(['a', 'b']);
    expect(screen.getByRole('alert')).toHaveTextContent('上限');
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

  it('rejects an old upload after only the canvas query changes', async () => {
    let resolve!: (value: { url: string }) => void;
    uploadFreezoneImage.mockReturnValue(new Promise((done) => { resolve = done; }));
    window.history.replaceState({}, '', '/projects/demo/freezone?canvas=one');
    render(<Harness />);
    let pending!: Promise<void>;
    act(() => { pending = actions.uploadFile(frame, new File(['x'], 'x.png', { type: 'image/png' })); });
    window.history.replaceState({}, '', '/projects/demo/freezone?canvas=two');
    await act(async () => { resolve({ url: '/new.png' }); await pending; });
    expect(current().draft.segments[0].firstFrame?.imageId).toBe('old');
  });

  it('rejects an old upload after the same node ID is rebuilt', async () => {
    let resolve!: (value: { url: string }) => void;
    uploadFreezoneImage.mockReturnValue(new Promise((done) => { resolve = done; }));
    render(<Harness />);
    let pending!: Promise<void>;
    act(() => { pending = actions.uploadFile(frame, new File(['x'], 'x.png', { type: 'image/png' })); });
    act(() => useCanvasStore.getState().setCanvasData([{ id: 'director', type: CANVAS_NODE_TYPES.videoDirector,
      position: { x: 0, y: 0 }, data: data() }], []));
    await act(async () => { resolve({ url: '/new.png' }); await pending; });
    expect(current().draft.segments[0].firstFrame?.imageId).toBe('old');
  });

  it('rejects an old upload when a same-ID replacement has a higher revision', async () => {
    let resolve!: (value: { url: string }) => void;
    uploadFreezoneImage.mockReturnValue(new Promise((done) => { resolve = done; }));
    render(<Harness />);
    let pending!: Promise<void>;
    act(() => { pending = actions.uploadFile(frame, new File(['x'], 'x.png', { type: 'image/png' })); });
    const replacement = data();
    replacement.draft.revision = 20;
    act(() => useCanvasStore.getState().setCanvasData([{ id: 'director', type: CANVAS_NODE_TYPES.videoDirector,
      position: { x: 0, y: 0 }, data: replacement }], []));
    await act(async () => { resolve({ url: '/new.png' }); await pending; });
    expect(current().draft.segments[0].firstFrame?.imageId).toBe('old');
    expect(current().draft.revision).toBe(20);
  });

  it('keeps a normal prompt edit made while a frame upload is pending', async () => {
    let resolve!: (value: { url: string }) => void;
    uploadFreezoneImage.mockReturnValue(new Promise((done) => { resolve = done; }));
    render(<Harness />);
    let pending!: Promise<void>;
    act(() => { pending = actions.uploadFile(frame, new File(['x'], 'x.png', { type: 'image/png' })); });
    act(() => useCanvasStore.getState().updateNodeData('director', { draft: {
      ...current().draft, revision: 1,
      segments: current().draft.segments.map((segment) => ({ ...segment, prompt: 'Edited while uploading' })),
    } }));
    await act(async () => { resolve({ url: '/new.png' }); await pending; });
    expect(current().draft.segments[0].firstFrame?.url).toBe('/new.png');
    expect(current().draft.segments[0].prompt).toBe('Edited while uploading');
    expect(current().draft.revision).toBe(2);
  });

  it('lets only the latest upload for a slot control its result and status', async () => {
    const resolvers: Array<(value: { url: string }) => void> = [];
    uploadFreezoneImage.mockImplementation(() => new Promise((done) => { resolvers.push(done); }));
    render(<Harness />);
    let first!: Promise<void>;
    let second!: Promise<void>;
    act(() => { first = actions.uploadFile(frame, new File(['a'], 'a.png', { type: 'image/png' })); });
    act(() => { second = actions.uploadFile(frame, new File(['b'], 'b.png', { type: 'image/png' })); });
    await act(async () => { resolvers[0]({ url: '/a.png' }); await first; });
    expect(current().draft.segments[0].firstFrame?.imageId).toBe('old');
    expect(screen.getByRole('status')).toHaveTextContent('上传中');
    await act(async () => { resolvers[1]({ url: '/b.png' }); await second; });
    expect(current().draft.segments[0].firstFrame?.imageId).toBe('/b.png');
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
  });

  it('keeps a manual library choice made while a frame upload is pending', async () => {
    let resolve!: (value: { url: string }) => void;
    uploadFreezoneImage.mockReturnValue(new Promise((done) => { resolve = done; }));
    render(<Harness />);
    let pending!: Promise<void>;
    act(() => { pending = actions.uploadFile(frame, new File(['x'], 'x.png', { type: 'image/png' })); });
    act(() => actions.openPicker(frame));
    act(() => actions.libraryProps?.onConfirm?.([{ media: 'image', name: 'manual', imageId: 'manual', url: '/manual.png' }]));
    await act(async () => { resolve({ url: '/late.png' }); await pending; });
    expect(current().draft.segments[0].firstFrame?.imageId).toBe('manual');
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
  });

  it('keeps a manual removal made while a frame upload is pending', async () => {
    let resolve!: (value: { url: string }) => void;
    uploadFreezoneImage.mockReturnValue(new Promise((done) => { resolve = done; }));
    render(<Harness />);
    let pending!: Promise<void>;
    act(() => { pending = actions.uploadFile(frame, new File(['x'], 'x.png', { type: 'image/png' })); });
    act(() => actions.removeImage(frame));
    await act(async () => { resolve({ url: '/late.png' }); await pending; });
    expect(current().draft.segments[0].firstFrame).toBeNull();
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
  });

  it('does not commit or update state after the image action component unmounts', async () => {
    let resolve!: (value: { url: string }) => void;
    uploadFreezoneImage.mockReturnValue(new Promise((done) => { resolve = done; }));
    const view = render(<Harness />);
    let pending!: Promise<void>;
    act(() => { pending = actions.uploadFile(frame, new File(['x'], 'x.png', { type: 'image/png' })); });
    view.unmount();
    await act(async () => { resolve({ url: '/new.png' }); await pending; });
    expect(current().draft.segments[0].firstFrame?.imageId).toBe('old');
  });

  it('activates reference mode when the same selected reference is confirmed', () => {
    const initial = data();
    initial.draft.references = [{ imageId: 'a', url: '/a.png' }];
    useCanvasStore.getState().updateNodeData('director', initial);
    render(<Harness target={refs} />);
    act(() => actions.openPicker(refs));
    act(() => actions.libraryProps?.onConfirm?.([{ media: 'image', name: 'a', imageId: 'a', url: '/a.png' }]));
    expect(current().activeInputMode).toBe('ref');
    expect(current().draft.revision).toBe(1);
  });

  it('replaces one existing reference at the limit after its panel upload succeeds', async () => {
    const initial = data();
    initial.draft.references = [{ imageId: 'a', url: '/a.png' }, { imageId: 'b', url: '/b.png' }];
    useCanvasStore.getState().updateNodeData('director', initial);
    let resolve!: (value: { url: string }) => void;
    uploadFreezoneImage.mockReturnValue(new Promise((done) => { resolve = done; }));
    render(<PanelHarness />);
    const slot = screen.getByLabelText('参考图 a');
    fireEvent.change(within(slot).getByLabelText('参考图 a上传图片'), { target: { files: [new File(['x'], 'new.png', { type: 'image/png' })] } });
    expect(within(slot).getByRole('status')).toHaveTextContent('上传中');
    expect(current().draft.references.map((image) => image.imageId)).toEqual(['a', 'b']);
    await act(async () => resolve({ url: '/new.png' }));
    expect(current().draft.references.map((image) => image.imageId)).toEqual(['/new.png', 'b']);
    expect(current().draft.revision).toBe(1);
  });

  it('keeps an existing reference and shows its panel slot error after upload fails', async () => {
    const initial = data();
    initial.draft.references = [{ imageId: 'a', url: '/a.png' }];
    useCanvasStore.getState().updateNodeData('director', initial);
    uploadFreezoneImage.mockRejectedValue(new Error('network down'));
    render(<PanelHarness />);
    const slot = screen.getByLabelText('参考图 a');
    fireEvent.change(within(slot).getByLabelText('参考图 a上传图片'), { target: { files: [new File(['x'], 'new.png', { type: 'image/png' })] } });
    await waitFor(() => expect(within(slot).getByRole('alert')).toHaveTextContent('network down'));
    expect(current().draft.references.map((image) => image.imageId)).toEqual(['a']);
    expect(current().draft.revision).toBe(0);
  });

  it('clears the prior frame validation error after a successful panel upload', async () => {
    uploadFreezoneImage.mockResolvedValue({ url: '/new.png' });
    render(<PanelHarness fieldErrors={{ 'segments[0].first_frame': '旧校验错误' }} />);
    expect(screen.getByText('旧校验错误')).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('首帧上传图片'), { target: { files: [new File(['x'], 'new.png', { type: 'image/png' })] } });
    await waitFor(() => expect(current().draft.segments[0].firstFrame?.url).toBe('/new.png'));
    expect(screen.queryByText('旧校验错误')).not.toBeInTheDocument();
  });
});
