import { lazy, Suspense } from "react";

import type { ThreeDDirectorDialogProps } from "./ThreeDDirectorDialog";

const ThreeDDirectorDialog = lazy(() =>
  import("./ThreeDDirectorDialog").then((module) => ({
    default: module.ThreeDDirectorDialog,
  })),
);

export function LazyThreeDDirectorDialog({
  open,
  ...props
}: ThreeDDirectorDialogProps) {
  if (!open) return null;

  return (
    <Suspense
      fallback={
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm">
          <div
            role="status"
            aria-live="polite"
            className="rounded-lg border border-white/10 bg-background/95 px-4 py-3 text-sm text-muted-foreground shadow-xl"
          >
            正在加载 3D 导演台…
          </div>
        </div>
      }
    >
      <ThreeDDirectorDialog open {...props} />
    </Suspense>
  );
}
