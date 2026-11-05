// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, expect, it } from "vitest";

import { sceneSnapshotFromDirectorControlBundle } from "@/features/viewer-kit/three-d/directorControlBundleSnapshot";

describe("sceneSnapshotFromDirectorControlBundle", () => {
  it("restores the view camera, output camera, and proxy lights from a delivered frame", () => {
    const snapshot = sceneSnapshotFromDirectorControlBundle({
      schema_version: "director_control_bundle_v1",
      dir: "freezone/director-world",
      paths: {},
      rel_paths: {},
      frame_meta: {
        schema_version: "director_frame_meta_v1",
        source: { source_id: "scene-1", source_type: "sog", source_kind: "master" },
        camera: {
          mode: "sog",
          frame_aspect: "16:9",
          state: { azim: 25, elev: -12, distance: 4, focalPoint: [1, 2, 3], fov: 48 },
          view_state: { azim: -10, elev: 5, distance: 3, focalPoint: [0, 1, 0], fov: 72 },
        },
        layer: { source_id: "scene-1", actors: [], props: [], stagings: [] },
        lights: [{
          id: "key-1",
          label: "主灯",
          color: "#ffd166",
          intensity: 2.5,
          position: [1, 2, 3],
          direction: [0, -1, 0],
          enabled: true,
        }],
      },
    });

    expect(snapshot).toMatchObject({
      world: { activeSourceId: "scene-1" },
      camera: { azim: -10, fov: 72 },
      outputCamera: { azim: 25, fov: 48 },
      lights: [{ id: "key-1", label: "主灯", intensity: 2.5, direction: [0, -1, 0] }],
    });
  });
});
