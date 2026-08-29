// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { lazy, Suspense, useEffect, useState } from 'react';

import { useCanvasStore } from '@/stores/canvasStore';

const NodeToolDialog = lazy(() =>
  import('./NodeToolDialog').then((module) => ({ default: module.NodeToolDialog })),
);

export function LazyNodeToolDialog() {
  const active = useCanvasStore((state) => Boolean(state.activeToolDialog));
  const [hasOpened, setHasOpened] = useState(active);

  useEffect(() => {
    if (active) {
      setHasOpened(true);
    }
  }, [active]);

  if (!hasOpened) {
    return null;
  }

  return (
    <Suspense
      fallback={(
        <div
          role="status"
          aria-live="polite"
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/55 text-sm"
        >
          正在加载节点工具…
        </div>
      )}
    >
      <NodeToolDialog />
    </Suspense>
  );
}
