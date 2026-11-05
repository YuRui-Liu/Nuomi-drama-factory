// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import type { ThreeDSceneSnapshot } from "./engine/viewerApp";
import type { DirectorControlFrameBundle, DirectorPlacement } from "./directorManifest";

function numberTuple3(value: unknown, fallback: [number, number, number]): [number, number, number] {
  if (!Array.isArray(value) || value.length < 3) return fallback;
  const next = value.slice(0, 3).map((item) => Number(item));
  return next.every((item) => Number.isFinite(item))
    ? [next[0], next[1], next[2]]
    : fallback;
}

function snapshotMarkerFromDirectorLayerItem(item: unknown): ThreeDSceneSnapshot["actors"][number] {
  const data = item && typeof item === "object" ? item as Record<string, unknown> : {};
  const placementData = data.placement && typeof data.placement === "object"
    ? data.placement as Record<string, unknown>
    : {};
  const placement: DirectorPlacement = placementData.space === "pano_view"
    ? {
        space: "pano_view",
        yawDeg: Number(placementData.yaw_deg ?? 0),
        pitchDeg: Number(placementData.pitch_deg ?? 0),
        distance: Number(placementData.distance ?? 6),
      }
    : {
        space: "world",
        position: numberTuple3(placementData.position, [0, 0, 0]),
        yawDeg: Number(placementData.yaw_deg ?? 0),
      };
  const position = placement.space === "world" ? placement.position : [0, 0, 0] as [number, number, number];
  return {
    label: typeof data.label === "string" ? data.label : "导演元素",
    color: typeof data.color === "string" ? data.color : "#38bdf8",
    placement,
    position,
    yawDeg: placement.yawDeg,
    scale: numberTuple3(data.scale, [1, 1, 1]),
    ...(typeof data.pose === "string" ? { pose: data.pose as never } : {}),
    ...(typeof data.action_playing === "boolean" ? { actionPlaying: data.action_playing } : {}),
    ...(typeof data.shape_hint === "string" ? { shapeHint: data.shape_hint as never } : {}),
  };
}

export function sceneSnapshotFromDirectorControlBundle(
  bundle: DirectorControlFrameBundle | null,
): ThreeDSceneSnapshot | null {
  const frameMeta = bundle?.frame_meta;
  if (!frameMeta?.layer) return null;
  const outputCamera = frameMeta.camera?.state;
  const viewCamera = frameMeta.camera?.view_state ?? outputCamera;
  const sourceId = frameMeta.source?.source_id ?? frameMeta.layer.source_id ?? bundle?.source?.source_id;
  return {
    schemaVersion: 1,
    savedAt: Date.now(),
    actors: (frameMeta.layer.actors ?? []).map(snapshotMarkerFromDirectorLayerItem),
    props: (frameMeta.layer.props ?? []).map(snapshotMarkerFromDirectorLayerItem),
    stagings: (frameMeta.layer.stagings ?? []).map(snapshotMarkerFromDirectorLayerItem),
    world: { activeSourceId: sourceId ?? undefined },
    camera: viewCamera as unknown as ThreeDSceneSnapshot["camera"],
    outputCamera: outputCamera as unknown as ThreeDSceneSnapshot["outputCamera"],
    lights: frameMeta.lights?.map((light) => ({ ...light })),
  };
}
