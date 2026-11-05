// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import type { DirectorStageOrientationMode } from "./directorManifest";
import type { DirectorWorldSourceTransform } from "./sourceTransform";
import {
  calculateOutputPreviewCropRect,
  createViewerApp,
  OUTPUT_CAMERA_PREVIEW_RECT,
  type CaptureFrameAspect,
  type FrameCaptureCssRect,
  type SplatLoadProgress,
  type ViewerApp,
} from "./engine/viewerApp";

const CLICK_DRAG_THRESHOLD_PX = 5;

export interface ThreeDStageCanvasProps {
  splatUrl: string | null;
  panoUrl?: string | null;
  orientationMode?: DirectorStageOrientationMode;
  sourceTransform?: Partial<DirectorWorldSourceTransform> | null;
  collisionUrl?: string | null;
  frameAspect?: CaptureFrameAspect;
  outputPreviewFrameRectCss?: FrameCaptureCssRect | null;
  interactionActive?: boolean;
  onInteractionActiveChange?: (active: boolean) => void;
  onReady?: (app: ViewerApp) => void;
  onError?: (error: Error) => void;
  onStatus?: (status: string) => void;
  onLoadProgress?: (progress: SplatLoadProgress) => void;
  onSourceReady?: () => void;
  onPlaceRequest?: () => void;
  showInteractionHint?: boolean;
}

export function ThreeDStageCanvas({
  splatUrl,
  panoUrl,
  orientationMode,
  sourceTransform,
  collisionUrl,
  frameAspect,
  outputPreviewFrameRectCss,
  interactionActive = false,
  onInteractionActiveChange,
  onReady,
  onError,
  onStatus,
  onLoadProgress,
  onSourceReady,
  onPlaceRequest,
  showInteractionHint = true,
}: ThreeDStageCanvasProps) {
  const { t } = useTranslation();
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const viewerRef = useRef<ViewerApp | null>(null);
  const [canvasLayout, setCanvasLayout] = useState<{
    canvasWidth: number;
    canvasHeight: number;
    clientWidth: number;
    clientHeight: number;
    canvasRectCss: FrameCaptureCssRect;
  } | null>(null);
  const onReadyRef = useRef<typeof onReady>(onReady);
  const onErrorRef = useRef<typeof onError>(onError);
  const onStatusRef = useRef<typeof onStatus>(onStatus);
  const onLoadProgressRef = useRef<typeof onLoadProgress>(onLoadProgress);
  const onSourceReadyRef = useRef<typeof onSourceReady>(onSourceReady);
  const onPlaceRequestRef = useRef<(() => void) | undefined>(onPlaceRequest);
  const sourceTransformRef = useRef<typeof sourceTransform>(sourceTransform);
  const interactionActiveRef = useRef(interactionActive);
  onReadyRef.current = onReady;
  onErrorRef.current = onError;
  onStatusRef.current = onStatus;
  onLoadProgressRef.current = onLoadProgress;
  onSourceReadyRef.current = onSourceReady;
  onPlaceRequestRef.current = onPlaceRequest;
  sourceTransformRef.current = sourceTransform;
  interactionActiveRef.current = interactionActive;

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return undefined;
    const updateLayout = () => {
      const rect = canvas.getBoundingClientRect();
      const next = {
        canvasWidth: canvas.width,
        canvasHeight: canvas.height,
        clientWidth: canvas.clientWidth,
        clientHeight: canvas.clientHeight,
        canvasRectCss: { left: rect.left, top: rect.top, width: rect.width, height: rect.height },
      };
      setCanvasLayout((current) => (
        current && current.canvasWidth === next.canvasWidth
        && current.canvasHeight === next.canvasHeight
        && current.clientWidth === next.clientWidth
        && current.clientHeight === next.clientHeight
        && current.canvasRectCss.left === next.canvasRectCss.left
        && current.canvasRectCss.top === next.canvasRectCss.top
        && current.canvasRectCss.width === next.canvasRectCss.width
        && current.canvasRectCss.height === next.canvasRectCss.height
          ? current
          : next
      ));
    };
    updateLayout();
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(updateLayout);
    observer?.observe(canvas);
    window.addEventListener("resize", updateLayout);
    return () => {
      observer?.disconnect();
      window.removeEventListener("resize", updateLayout);
    };
  }, []);

  const outputPreviewCrop = useMemo(() => {
    if (!canvasLayout || !frameAspect) return null;
    return calculateOutputPreviewCropRect({
      ...canvasLayout,
      frameAspect,
      frameRectCss: outputPreviewFrameRectCss ?? undefined,
    });
  }, [canvasLayout, frameAspect, outputPreviewFrameRectCss]);

  useEffect(() => {
    let disposed = false;
    let unsubStatus: (() => void) | undefined;
    let unsubProgress: (() => void) | undefined;
    let layoutFrame = 0;
    const canvas = canvasRef.current;
    if (!canvas) return undefined;

    let downX = 0;
    let downY = 0;
    let downButton = -1;
    let downShift = false;

    const onPointerDown = (event: PointerEvent) => {
      if (!interactionActiveRef.current) return;
      downX = event.clientX;
      downY = event.clientY;
      downButton = event.button;
      downShift = event.shiftKey;
    };
    const onPointerUp = (event: PointerEvent) => {
      if (!interactionActiveRef.current) return;
      if (downButton < 0) return;
      const dx = event.clientX - downX;
      const dy = event.clientY - downY;
      const isClick = Math.hypot(dx, dy) <= CLICK_DRAG_THRESHOLD_PX;
      const button = downButton;
      const shift = downShift;
      downButton = -1;
      downShift = false;
      if (event.button !== button || !isClick) return;

      const viewer = viewerRef.current;
      if (!viewer) return;
      if (button === 2 || (button === 0 && shift)) {
        onPlaceRequestRef.current?.();
        return;
      }
      if (button !== 0) return;
      const hit = viewer.selectAtScreen(event.clientX, event.clientY);
      if (!hit) viewer.clearSelection();
    };
    const onContextMenu = (event: MouseEvent) => event.preventDefault();

    canvas.addEventListener("pointerdown", onPointerDown);
    canvas.addEventListener("pointerup", onPointerUp);
    canvas.addEventListener("contextmenu", onContextMenu);

    const initFrame = window.requestAnimationFrame(() => {
      void (async () => {
        try {
          const viewer = await createViewerApp({ canvas });
          if (disposed) {
            viewer.destroy();
            return;
          }
          viewerRef.current = viewer;
          layoutFrame = window.requestAnimationFrame(() => {
            const bounds = canvas.getBoundingClientRect();
            setCanvasLayout({
              canvasWidth: canvas.width,
              canvasHeight: canvas.height,
              clientWidth: canvas.clientWidth,
              clientHeight: canvas.clientHeight,
              canvasRectCss: {
                left: bounds.left,
                top: bounds.top,
                width: bounds.width,
                height: bounds.height,
              },
            });
          });
          viewer.fly.setInputEnabled(interactionActiveRef.current);
          if (onStatusRef.current) {
            unsubStatus = viewer.onStatus((next) => onStatusRef.current?.(next));
          }
          if (onLoadProgressRef.current) {
            unsubProgress = viewer.onSplatProgress((next) =>
              onLoadProgressRef.current?.(next),
            );
          }
          onReadyRef.current?.(viewer);
        } catch (error) {
          if (disposed) return;
          onErrorRef.current?.(error instanceof Error ? error : new Error(String(error)));
        }
      })();
    });

    return () => {
      disposed = true;
      window.cancelAnimationFrame(initFrame);
      if (layoutFrame) window.cancelAnimationFrame(layoutFrame);
      unsubStatus?.();
      unsubProgress?.();
      canvas.removeEventListener("pointerdown", onPointerDown);
      canvas.removeEventListener("pointerup", onPointerUp);
      canvas.removeEventListener("contextmenu", onContextMenu);
      const viewer = viewerRef.current;
      viewerRef.current = null;
      viewer?.destroy();
    };
  }, []);

  useEffect(() => {
    if (!splatUrl) return undefined;
    const load = () => {
      const viewer = viewerRef.current;
      if (!viewer) return false;
      viewer
        .loadSplat(splatUrl, {
          orientationMode,
          sourceTransform: sourceTransformRef.current,
        })
        .then(() => onSourceReadyRef.current?.())
        .catch((error: unknown) => {
          onErrorRef.current?.(error instanceof Error ? error : new Error(String(error)));
        });
      return true;
    };
    if (load()) return undefined;
    const handle = window.setInterval(() => {
      if (load()) window.clearInterval(handle);
    }, 80);
    return () => window.clearInterval(handle);
  }, [orientationMode, splatUrl]);

  useEffect(() => {
    if (!panoUrl && !splatUrl) {
      const clear = () => {
        const viewer = viewerRef.current;
        if (!viewer) return false;
        viewer.clearWorldSource();
        onSourceReadyRef.current?.();
        return true;
      };
      if (clear()) return undefined;
      const handle = window.setInterval(() => {
        if (clear()) window.clearInterval(handle);
      }, 80);
      return () => window.clearInterval(handle);
    }
    if (!panoUrl && splatUrl) return undefined;
    const load = () => {
      const viewer = viewerRef.current;
      if (!viewer) return false;
      viewer
        .loadPano(panoUrl ?? null, {
          sourceTransform: sourceTransformRef.current,
        })
        .then(() => onSourceReadyRef.current?.())
        .catch((error: unknown) => {
          onErrorRef.current?.(error instanceof Error ? error : new Error(String(error)));
        });
      return true;
    };
    if (load()) return undefined;
    const handle = window.setInterval(() => {
      if (load()) window.clearInterval(handle);
    }, 80);
    return () => window.clearInterval(handle);
  }, [panoUrl, splatUrl]);

  useEffect(() => {
    const load = () => {
      const viewer = viewerRef.current;
      if (!viewer) return false;
      viewer.loadCollision(collisionUrl ?? null).catch((error: unknown) => {
        onErrorRef.current?.(error instanceof Error ? error : new Error(String(error)));
      });
      return true;
    };
    if (load()) return undefined;
    const handle = window.setInterval(() => {
      if (load()) window.clearInterval(handle);
    }, 80);
    return () => window.clearInterval(handle);
  }, [collisionUrl]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    viewerRef.current?.fly.setInputEnabled(interactionActive);
    if (interactionActive) {
      canvas.focus();
    } else {
      if (document.activeElement === canvas) {
        canvas.blur();
      }
      window.getSelection()?.removeAllRanges();
    }
  }, [interactionActive]);

  const activate = () => {
    onInteractionActiveChange?.(true);
    window.requestAnimationFrame(() => canvasRef.current?.focus());
  };

  return (
    <div className="relative h-full w-full overflow-hidden bg-black">
      <canvas
        ref={canvasRef}
        tabIndex={0}
        onFocus={() => onInteractionActiveChange?.(true)}
        onBlur={() => onInteractionActiveChange?.(false)}
        className="block h-full w-full cursor-crosshair outline-none"
      />
      {outputPreviewCrop && frameAspect && (
        <div
          aria-hidden="true"
          className="pointer-events-none absolute z-20 overflow-hidden"
          style={{
            left: `${OUTPUT_CAMERA_PREVIEW_RECT.left * 100}%`,
            top: `${(1 - OUTPUT_CAMERA_PREVIEW_RECT.bottom - OUTPUT_CAMERA_PREVIEW_RECT.height) * 100}%`,
            width: `${OUTPUT_CAMERA_PREVIEW_RECT.width * 100}%`,
            height: `${OUTPUT_CAMERA_PREVIEW_RECT.height * 100}%`,
          }}
        >
          {outputPreviewCrop.top > 0 && (
            <div className="absolute inset-x-0 top-0 bg-black/50" style={{ height: `${outputPreviewCrop.top * 100}%` }} />
          )}
          {outputPreviewCrop.top + outputPreviewCrop.height < 1 && (
            <div className="absolute inset-x-0 bottom-0 bg-black/50" style={{ height: `${(1 - outputPreviewCrop.top - outputPreviewCrop.height) * 100}%` }} />
          )}
          {outputPreviewCrop.left > 0 && (
            <div
              className="absolute bg-black/50"
              style={{
                left: 0,
                top: `${outputPreviewCrop.top * 100}%`,
                width: `${outputPreviewCrop.left * 100}%`,
                height: `${outputPreviewCrop.height * 100}%`,
              }}
            />
          )}
          {outputPreviewCrop.left + outputPreviewCrop.width < 1 && (
            <div
              className="absolute right-0 bg-black/50"
              style={{
                top: `${outputPreviewCrop.top * 100}%`,
                width: `${(1 - outputPreviewCrop.left - outputPreviewCrop.width) * 100}%`,
                height: `${outputPreviewCrop.height * 100}%`,
              }}
            />
          )}
          <div
            className="absolute border border-amber-200/90 shadow-[0_0_0_1px_rgba(0,0,0,0.38)]"
            style={{
              left: `${outputPreviewCrop.left * 100}%`,
              top: `${outputPreviewCrop.top * 100}%`,
              width: `${outputPreviewCrop.width * 100}%`,
              height: `${outputPreviewCrop.height * 100}%`,
            }}
          >
            <span className="absolute left-1 top-0.5 rounded bg-black/55 px-1 text-[9px] leading-[14px] text-white/80">
              {frameAspect}
            </span>
          </div>
        </div>
      )}
      {showInteractionHint && !interactionActive && (
        <button
          type="button"
          onClick={activate}
          className="absolute inset-0 z-10 flex items-center justify-center bg-black/35 text-[#fff8df] backdrop-blur-[1px]"
        >
          <span className="rounded-xl border border-[rgba(255,226,166,0.28)] bg-[rgba(20,16,11,0.86)] px-5 py-3 text-sm shadow-[0_12px_40px_rgba(0,0,0,0.45)]">
            {t("viewer.threeD.stageClickToEnter")} · {t("viewer.threeD.stageExitInteraction")}
          </span>
        </button>
      )}
    </div>
  );
}
