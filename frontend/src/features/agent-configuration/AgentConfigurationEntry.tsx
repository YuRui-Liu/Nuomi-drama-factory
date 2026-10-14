import { useEffect, useRef, useState } from 'react';
import { useRouter, useRouterState } from '@tanstack/react-router';
import { Dialog, DialogContent, DialogDescription, DialogTitle } from '@/components/ui/dialog';
import { useAuthStore } from '@/stores/auth-store';
import { captureFreezoneCanvasRuntime } from '@/features/freezone/canvasSyncRuntime';
import { AgentConfigurationPanel } from './AgentConfigurationPanel';
export function AgentConfigurationEntry({
  project,
  canvasId,
  compact = false,
}: {
  project: string;
  canvasId: string;
  compact?: boolean;
}) {
  const router = useRouter();
  const location = useRouterState({ select: (s) => s.location });
  const user = useAuthStore((s) => s.username);
  const latest = useRef({ project, canvasId, user });
  latest.current = { project, canvasId, user };
  const [opened, setOpened] = useState<{
    user: string;
    project: string;
    canvas: string;
    origin: typeof location;
    runtime: ReturnType<typeof captureFreezoneCanvasRuntime>;
    instance: string;
  } | null>(null);
  const [error, setError] = useState('');
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
    !!opened.runtime?.isCurrent();
  return (
    <>
      <button
        type="button"
        className={`rounded-md border border-border bg-background text-foreground ${compact ? 'px-2 py-1 text-xs' : 'px-3 py-1.5 text-xs'}`}
        onClick={(e) => {
          e.stopPropagation();
          if (!user) return;
          const runtime = captureFreezoneCanvasRuntime(project, canvasId);
          if (!runtime) {
            setError('画布尚未就绪，请稍后打开配置');
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
        项目 Agent 配置
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
          <DialogTitle>项目 Agent 配置</DialogTitle>
          <DialogDescription>管理个人版本和项目调整，保留当前画布与聊天。</DialogDescription>
          {opened && (
            <AgentConfigurationPanel
              key={opened.instance}
              owner={{ user: opened.user, project: opened.project, canvas: opened.canvas }}
              isCurrent={isCurrent}
            />
          )}
        </DialogContent>
      </Dialog>
    </>
  );
}
