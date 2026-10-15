import { useEffect, useRef, useState } from 'react';
import { useRouter, useRouterState } from '@tanstack/react-router';
import { Dialog, DialogContent, DialogDescription, DialogTitle } from '@/components/ui/dialog';
import { useAuthStore } from '@/stores/auth-store';
import { captureFreezoneCanvasRuntime } from '@/features/freezone/canvasSyncRuntime';
import { TemplateLibraryPanel } from './TemplateLibraryPanel';
export function TemplateLibraryEntry({ project, canvasId }: { project: string; canvasId: string }) {
  const router = useRouter(),
    location = useRouterState({ select: (s) => s.location }),
    user = useAuthStore((s) => s.username);
  const latest = useRef({ project, canvasId, user });
  latest.current = { project, canvasId, user };
  const [opened, setOpened] = useState<{
      user: string;
      project: string;
      canvas: string;
      origin: typeof location;
      runtime: NonNullable<ReturnType<typeof captureFreezoneCanvasRuntime>>;
      instance: string;
    } | null>(null),
    [error, setError] = useState('');
  useEffect(() => {
    setOpened(null);
    setError('');
  }, [location, user, project, canvasId]);
  const isCurrent = () =>
    !!opened &&
    latest.current.user === opened.user &&
    latest.current.project === opened.project &&
    latest.current.canvasId === opened.canvas &&
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
            setError('画布尚未就绪，请稍后打开模板库');
            return;
          }
          setOpened({
            user,
            project,
            canvas: canvasId,
            origin: router.state.location,
            runtime,
            instance: crypto.randomUUID(),
          });
          setError('');
        }}
      >
        创作模板
      </button>
      {error && (
        <span role="alert" className="text-xs text-destructive">
          {error}
        </span>
      )}
      <Dialog
        open={!!opened}
        onOpenChange={(open) => {
          if (!open) setOpened(null);
        }}
      >
        <DialogContent
          className="max-h-[85dvh] overflow-auto sm:max-w-3xl"
          overlayClassName="z-[11000]"
          style={{ zIndex: 11001 }}
        >
          <DialogTitle>创作模板</DialogTitle>
          <DialogDescription>管理个人固定版本，明确选择项目采用与启用内容。</DialogDescription>
          {opened && (
            <TemplateLibraryPanel
              key={opened.instance}
              owner={{ user: opened.user, project: opened.project, canvas: opened.canvas }}
              runtime={opened.runtime}
              isCurrent={isCurrent}
            />
          )}
        </DialogContent>
      </Dialog>
    </>
  );
}
