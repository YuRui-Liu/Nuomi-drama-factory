import { useEffect, useRef, useState } from 'react';
import { useRouter, useRouterState } from '@tanstack/react-router';
import { Dialog, DialogContent, DialogDescription, DialogTitle } from '@/components/ui/dialog';
import { useAuthStore } from '@/stores/auth-store';
import { useCanvasStore } from '@/stores/canvasStore';
import { captureFreezoneCanvasRuntime } from '@/features/freezone/canvasSyncRuntime';
import { CreativeSettingsPanel } from './CreativeSettingsPanel';
export function CreativeSettingsEntry({
  project,
  canvasId,
}: {
  project: string;
  canvasId: string;
}) {
  const router = useRouter(),
    location = useRouterState({ select: (s) => s.location }),
    user = useAuthStore((s) => s.username);
  const latest = useRef({ project, canvasId, user });
  latest.current = { project, canvasId, user };
  const [opened, setOpened] = useState<{
      user: string;
      project: string;
      canvas: string;
      runtime: NonNullable<ReturnType<typeof captureFreezoneCanvasRuntime>>;
      origin: typeof location;
      selected: { id: string; type?: string }[];
      instance: string;
    } | null>(null),
    [error, setError] = useState('');
  useEffect(() => {
    setOpened(null);
    setError('');
  }, [location, user, project, canvasId]);
  const current = () =>
    !!opened &&
    latest.current.project === opened.project &&
    latest.current.canvasId === opened.canvas &&
    latest.current.user === opened.user &&
    useAuthStore.getState().username === opened.user &&
    router.state.location === opened.origin &&
    opened.runtime.isCurrent();
  return (
    <>
      <button
        type="button"
        className="rounded-md border border-border bg-background px-3 py-1.5 text-xs text-foreground"
        onClick={(e) => {
          e.stopPropagation();
          if (!user) return;
          const runtime = captureFreezoneCanvasRuntime(project, canvasId);
          if (!runtime) {
            setError('画布尚未就绪，请稍后打开配置');
            return;
          }
          setError('');
          setOpened({
            user,
            project,
            canvas: canvasId,
            runtime,
            origin: router.state.location,
            selected: useCanvasStore
              .getState()
              .nodes.filter((n) => n.selected)
              .map((n) => ({ id: n.id, type: n.type })),
            instance: crypto.randomUUID(),
          });
        }}
      >
        创作对象与约定
      </button>
      {error && (
        <p role="alert" className="text-xs text-destructive">
          {error}
        </p>
      )}
      <Dialog
        open={!!opened}
        onOpenChange={(value) => {
          if (!value) setOpened(null);
        }}
      >
        <DialogContent
          className="max-h-[85dvh] overflow-auto sm:max-w-3xl"
          overlayClassName="z-[11000]"
          style={{ zIndex: 11001 }}
        >
          <DialogTitle>创作对象与项目约定</DialogTitle>
          <DialogDescription>在当前项目补充定义、明确约定，历史版本保持可查。</DialogDescription>
          {opened && (
            <CreativeSettingsPanel
              key={opened.instance}
              owner={{ user: opened.user, project: opened.project, canvas: opened.canvas }}
              selected={opened.selected}
              runtime={opened.runtime}
              isCurrent={current}
            />
          )}
        </DialogContent>
      </Dialog>
    </>
  );
}
