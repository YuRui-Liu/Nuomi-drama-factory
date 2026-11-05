// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, expect, it, vi } from "vitest";

import {
  calculateFrameCaptureRect,
  calculateOutputPreviewCropRect,
  distanceSqPointToRay,
  normalizeThreeDSceneSnapshot,
  spotLightDirectionFromRotation,
  spotLightRotationForDirection,
  resolveSnapshotWorldTransform,
  resolveViewerDevicePixelRatio,
  sampleActorAnimationPoseFrame,
  shouldHideEditorOverlaysForCapture,
  syncOutputCameraFromViewForAction,
} from "@/features/viewer-kit/three-d/engine/viewerApp";
import {
  proxyLocalBottomForHint,
  proxyPartsForHint,
  SHAPE_HINT_NAMES,
} from "@/features/viewer-kit/three-d/engine/shapeHints";

describe("resolveViewerDevicePixelRatio", () => {
  it("caps high-density displays for smoother full-screen 3GS interaction", () => {
    expect(resolveViewerDevicePixelRatio(2)).toBe(1.5);
    expect(resolveViewerDevicePixelRatio(3)).toBe(1.5);
  });

  it("keeps normal density displays unchanged", () => {
    expect(resolveViewerDevicePixelRatio(1)).toBe(1);
    expect(resolveViewerDevicePixelRatio(1.25)).toBe(1.25);
  });
});

describe("calculateFrameCaptureRect", () => {
  it("uses the actual DOM frame guide rectangle when provided", () => {
    expect(calculateFrameCaptureRect({
      canvasWidth: 1920,
      canvasHeight: 1080,
      clientWidth: 960,
      clientHeight: 540,
      canvasRectCss: { left: 320, top: 64, width: 960, height: 540 },
      frameRectCss: { left: 520, top: 94, width: 360, height: 480 },
      frameAspect: "2:3",
    })).toEqual({
      sx: 400,
      sy: 60,
      sw: 720,
      sh: 960,
    });
  });

  it("falls back to the centered frame guide geometry when no DOM frame rect is provided", () => {
    expect(calculateFrameCaptureRect({
      canvasWidth: 1920,
      canvasHeight: 1080,
      clientWidth: 960,
      clientHeight: 540,
      frameAspect: "16:9",
      paddingCssPx: 16,
    })).toEqual({
      sx: 57,
      sy: 32,
      sw: 1806,
      sh: 1016,
    });
  });
});

describe("calculateOutputPreviewCropRect", () => {
  it("maps the same centered capture crop into the output preview for portrait delivery", () => {
    const input = {
      canvasWidth: 1000,
      canvasHeight: 600,
      clientWidth: 1000,
      clientHeight: 600,
      frameAspect: "9:16" as const,
      paddingCssPx: 16,
    };
    const captureRect = calculateFrameCaptureRect(input);
    expect(calculateOutputPreviewCropRect(input)).toEqual({
      left: captureRect.sx / input.canvasWidth,
      top: captureRect.sy / input.canvasHeight,
      width: captureRect.sw / input.canvasWidth,
      height: captureRect.sh / input.canvasHeight,
    });
    expect(captureRect.sw).toBeLessThan(input.canvasWidth * 0.4);
    expect(captureRect.sh).toBe(input.canvasHeight - input.paddingCssPx * 2);
  });
});

describe("shouldHideEditorOverlaysForCapture", () => {
  it("hides selection and transform overlays for director combined screenshots", () => {
    expect(shouldHideEditorOverlaysForCapture("combined")).toBe(true);
    expect(shouldHideEditorOverlaysForCapture("env_only")).toBe(true);
  });
});

describe("output camera initialization", () => {
  it("copies the editor view when loading a world but preserves the output camera on editor reset", () => {
    const syncOutputCamera = vi.fn();

    syncOutputCameraFromViewForAction("world-load", syncOutputCamera);
    expect(syncOutputCamera).toHaveBeenCalledTimes(1);

    syncOutputCamera.mockClear();
    syncOutputCameraFromViewForAction("editor-reset", syncOutputCamera);
    expect(syncOutputCamera).not.toHaveBeenCalled();
  });
});

describe("resolveSnapshotWorldTransform", () => {
  it("falls back to legacy position and yaw only when placement is missing", () => {
    expect(resolveSnapshotWorldTransform({
      label: "legacy",
      color: "#ffffff",
      position: [1, 2, 3],
      yawDeg: 45,
      scale: [1, 1, 1],
    })).toEqual({ position: [1, 2, 3], yawDeg: 45 });
  });

  it("does not convert explicit pano_view placement into a world transform", () => {
    expect(resolveSnapshotWorldTransform({
      label: "pano actor",
      color: "#ffffff",
      placement: { space: "pano_view", yawDeg: 30, pitchDeg: -5, distance: 6 },
      position: [1, 2, 3],
      yawDeg: 45,
      scale: [1, 1, 1],
    })).toBeNull();
  });
});

describe("normalizeThreeDSceneSnapshot", () => {
  const fallbackCamera = {
    azim: -20,
    elev: 10,
    distance: 3,
    focalPoint: [1, 2, 3] as [number, number, number],
    fov: 72,
  };

  it("keeps old snapshots loadable and seeds the output camera from the saved editor view", () => {
    const snapshot = normalizeThreeDSceneSnapshot({
      schemaVersion: 1,
      savedAt: 1,
      actors: [],
      props: [],
      stagings: [],
      camera: { azim: 15, elev: -5, distance: 4, focalPoint: [4, 5, 6] },
    }, fallbackCamera);

    expect(snapshot.camera).toEqual({
      azim: 15,
      elev: -5,
      distance: 4,
      focalPoint: [4, 5, 6],
      fov: 72,
    });
    expect(snapshot.outputCamera).toEqual(snapshot.camera);
    expect(snapshot.lights).toEqual([]);
  });

  it("preserves a separate output camera and normalizes optional proxy light state", () => {
    const snapshot = normalizeThreeDSceneSnapshot({
      schemaVersion: 1,
      savedAt: 1,
      actors: [],
      props: [],
      stagings: [],
      camera: { azim: 10, elev: 20, distance: 2, focalPoint: [0, 1, 2], fov: 68 },
      outputCamera: { azim: 35, elev: -10, distance: 5, focalPoint: [3, 2, 1], fov: 44 },
      lights: [{
        id: "key-1",
        label: "Key light",
        color: "#ffcc88",
        intensity: 12,
        position: [1, 2, 3],
        direction: [0, -4, 0],
        enabled: true,
      }],
    }, fallbackCamera);

    expect(snapshot.camera?.fov).toBe(68);
    expect(snapshot.outputCamera).toEqual({
      azim: 35,
      elev: -10,
      distance: 5,
      focalPoint: [3, 2, 1],
      fov: 44,
    });
    expect(snapshot.lights).toEqual([{
      id: "key-1",
      label: "Key light",
      color: "#ffcc88",
      intensity: 8,
      position: [1, 2, 3],
      direction: [0, -1, 0],
      enabled: true,
    }]);
  });
});

describe("proxy spot light direction", () => {
  it("stores and restores the PlayCanvas spot light's local -Y emission axis", () => {
    for (const direction of [[0, -1, 0], [1, 0, 0], [0.2, -0.4, 0.8]] as const) {
      const rotation = spotLightRotationForDirection([...direction]);
      const restored = spotLightDirectionFromRotation(rotation);
      const length = Math.hypot(...direction);
      expect(restored[0]).toBeCloseTo(direction[0] / length, 5);
      expect(restored[1]).toBeCloseTo(direction[1] / length, 5);
      expect(restored[2]).toBeCloseTo(direction[2] / length, 5);
    }
  });
});

describe("sampleActorAnimationPoseFrame", () => {
  it("samples a paused action after pausing so the skeleton does not stay in bind pose", () => {
    const calls: string[] = [];
    let layerPlaying = false;
    const layer = {
      play: (name: string) => {
        layerPlaying = true;
        calls.push(`play:${name}`);
      },
      pause: () => {
        layerPlaying = false;
        calls.push("pause");
      },
      set activeStateCurrentTime(value: number) {
        calls.push(`sample:${value}:${layerPlaying ? "playing" : "paused"}`);
      },
      get activeStateCurrentTime() {
        return 0;
      },
    };
    const anim = { baseLayer: layer, playing: true };

    sampleActorAnimationPoseFrame(anim, "Idle_Loop", 0.25, false);

    expect(calls).toEqual(["play:Idle_Loop", "pause", "sample:0.25:paused"]);
    expect(anim.playing).toBe(false);
  });
});

describe("distanceSqPointToRay", () => {
  it("supports tolerant crosshair picking around shape-hint props", () => {
    expect(distanceSqPointToRay([0, 0, 0], [0, 0, -1], [0.5, 1, -4])).toEqual({
      along: 4,
      distanceSq: 1.25,
    });
    expect(distanceSqPointToRay([0, 0, 0], [0, 0, -1], [0, 0, 4])?.along).toBeLessThan(0);
  });
});

describe("shape hint proxy parts", () => {
  it("keeps the frontend shape hint registry aligned with AI staging presets", () => {
    expect(SHAPE_HINT_NAMES).toContain("sports_car");
  });

  it("turns semantic shape hints into distinct visible blocking silhouettes", () => {
    expect(proxyPartsForHint("box").map((part) => part.name)).toEqual(["body"]);
    expect(proxyPartsForHint("pile").map((part) => part.name)).toEqual([
      "base_left",
      "base_right",
      "top",
    ]);
    expect(proxyPartsForHint("quadruped_mount").map((part) => part.name)).toEqual([
      "body",
      "neck",
      "head",
      "leg_front_left",
      "leg_front_right",
      "leg_back_left",
      "leg_back_right",
    ]);
    expect(proxyPartsForHint("flying_craft").map((part) => part.name)).toEqual([
      "body",
      "wing_left",
      "wing_right",
      "tail",
    ]);
    expect(proxyPartsForHint("sports_car").map((part) => part.name)).toEqual([
      "body",
      "cabin",
      "hood",
      "wheel_front_left",
      "wheel_front_right",
      "wheel_back_left",
      "wheel_back_right",
    ]);
  });

  it("exposes the proxy bottom for ground placement", () => {
    expect(proxyLocalBottomForHint("box")).toBe(-0.5);
    expect(proxyLocalBottomForHint("quadruped_mount")).toBeCloseTo(-0.6);
  });
});
