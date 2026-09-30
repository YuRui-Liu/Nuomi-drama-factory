import { describe, expect, it } from "vitest";

import {
  addSegment,
  cloneVideoDirectorData,
  copySegment,
  createDirectorDraft,
  deleteSegment,
  reorderSegments,
  updateDraft,
  updateSegment,
} from "@/features/canvas/domain/videoDirectorDraft";
import { CANVAS_NODE_TYPES } from "@/features/canvas/domain/canvasNodes";
import { extractUpstreamContent } from "@/features/canvas/application/graphContentResolver";
import { extractCanvasAssets } from "@/features/canvas/domain/canvasAssets";
import { useCanvasStore } from "@/stores/canvasStore";

describe("video director draft", () => {
  it("edits segments immutably and increments the revision", () => {
    const original = createDirectorDraft("first");
    const added = addSegment(original, "second");
    const updated = updateSegment(added, "second", { prompt: "Action" });
    const moved = reorderSegments(updated, 1, 0);
    const deleted = deleteSegment(moved, "first");
    const configured = updateDraft(deleted, { resolution: "1080p" });

    expect(original.revision).toBe(0);
    expect(original.segments).toHaveLength(1);
    expect([added.revision, updated.revision, moved.revision, deleted.revision, configured.revision]).toEqual([1, 2, 3, 4, 5]);
    expect(configured.segments.map((segment) => segment.id)).toEqual(["second"]);
    expect(configured.segments[0].prompt).toBe("Action");
    expect(configured.resolution).toBe("1080p");
  });

  it("copies a segment with a unique id and independent frame objects", () => {
    const original = updateSegment(createDirectorDraft("first"), "first", {
      firstFrame: { imageId: "image", url: "/image.png" },
    });
    const copied = copySegment(original, "first", "second");
    expect(copied.revision).toBe(original.revision + 1);
    expect(copied.segments[1]).toEqual({ ...copied.segments[0], id: "second" });
    expect(copied.segments[1].firstFrame).not.toBe(copied.segments[0].firstFrame);
    expect(() => copySegment(original, "first", "first")).toThrow(/duplicate/i);
  });

  it("keeps technique bound to segment identity through copy, reorder, deletion and clone", () => {
    const selected = updateSegment(createDirectorDraft('first'), 'first', { technique: { id: 'slow-push-in', version: '1.0.0' } });
    const copied = copySegment(selected, 'first', 'second');
    expect(copied.segments[1].technique).toEqual(selected.segments[0].technique);
    expect(copied.segments[1].technique).not.toBe(copied.segments[0].technique);
    const moved = reorderSegments(copied, 1, 0);
    expect(moved.segments.map((segment) => [segment.id, segment.technique?.id])).toEqual([
      ['second', 'slow-push-in'], ['first', 'slow-push-in'],
    ]);
    expect(deleteSegment(moved, 'first').segments.map((segment) => segment.id)).toEqual(['second']);
  });

  it("duplicates node data with independent draft and no active attempt or result", () => {
    const data = {
      draft: updateSegment(updateDraft(createDirectorDraft("first"), {
        references: [{ imageId: "reference", url: "/reference.png" }],
      }), "first", {
        firstFrame: { imageId: "start", url: "/start.png" },
        lastFrame: { imageId: "image", url: "/last.png" },
      }),
      activeInputMode: "frames" as const,
      visibleSegmentId: "first",
      activeAttemptId: "attempt-1",
      videoUrl: "/result.mp4",
      resultRevision: 1,
    };
    const clone = cloneVideoDirectorData(data);
    expect(clone).toMatchObject({ activeAttemptId: null, videoUrl: null, resultRevision: null });
    expect(clone.draft).toEqual(data.draft);
    expect(clone.draft).not.toBe(data.draft);
    expect(clone).toMatchObject({ activeInputMode: "frames", visibleSegmentId: "first" });
    expect(clone.draft.references[0]).not.toBe(data.draft.references[0]);
    expect(clone.draft.segments[0].firstFrame).not.toBe(data.draft.segments[0].firstFrame);
    expect(clone.draft.segments[0].lastFrame).not.toBe(data.draft.segments[0].lastFrame);
  });

  it("restores the nested draft and makes sibling clones independent", () => {
    const draft = updateSegment(createDirectorDraft("first"), "first", { prompt: "Pan left" });
    useCanvasStore.getState().setCanvasData([{ id: "director", type: CANVAS_NODE_TYPES.videoDirector,
      position: { x: 10, y: 20 }, data: { draft, activeAttemptId: "attempt", videoUrl: "/old.mp4", resultRevision: 1 } }], []);
    const restored = useCanvasStore.getState().nodes[0];
    expect(restored.data.draft).toEqual(draft);
    const cloneId = useCanvasStore.getState().duplicateNodeAsSibling("director", 1);
    const clone = useCanvasStore.getState().nodes.find((node) => node.id === cloneId);
    expect(clone?.data).toMatchObject({ activeAttemptId: null, videoUrl: null, resultRevision: null, draft });
    expect(clone?.data.draft).not.toBe(restored.data.draft);
    const [batchId] = useCanvasStore.getState().duplicateNodesAsSiblings(["director"]);
    const batchClone = useCanvasStore.getState().nodes.find((node) => node.id === batchId);
    expect(batchClone?.data).toMatchObject({ activeAttemptId: null, videoUrl: null, resultRevision: null, draft });
    expect(batchClone?.data.draft).not.toBe(restored.data.draft);

    const serialized = JSON.stringify({ nodes: [restored], edges: [] });
    const saved = JSON.parse(serialized);
    useCanvasStore.getState().setCanvasData(saved.nodes, saved.edges);
    expect(useCanvasStore.getState().nodes[0].data).toMatchObject({ draft, activeAttemptId: "attempt", videoUrl: "/old.mp4" });
  });

  it("offers generated director videos to graph and history consumers", () => {
    const node = { id: "director", type: CANVAS_NODE_TYPES.videoDirector,
      position: { x: 0, y: 0 }, data: { draft: createDirectorDraft("first"), activeAttemptId: null,
        videoUrl: "/result.mp4", resultRevision: 0 } };
    expect(extractUpstreamContent(node).videoUrl).toBe("/result.mp4");
    expect(extractCanvasAssets([node]).video.map((asset) => asset.url)).toEqual(["/result.mp4"]);
  });
});
