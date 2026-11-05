import type { ReactNode } from 'react';

/** A mounted panel stays alive while hidden; visibility never controls its runtime. */
export function WorkspaceAgentPanel({ desktop, managed, open, visible, title, description, children }: {
  desktop: boolean; managed: boolean; open: boolean; visible: boolean;
  title: string; description: string; children: ReactNode;
}) {
  const inline = managed && desktop;
  return <aside
    hidden={inline && !open}
    inert={!open}
    aria-label={title}
    aria-hidden={!open}
    title={description}
    className={inline
      ? `z-40 h-full shrink-0 flex-col overflow-hidden border-l border-white/[0.12] bg-zinc-950/95 ${open ? 'flex' : 'hidden'}`
      : `absolute bottom-4 right-4 top-4 z-40 flex origin-right flex-col overflow-hidden rounded-[14px] border border-white/[0.12] bg-zinc-950/95 shadow-none backdrop-blur-2xl transition-[opacity,transform] duration-300 ease-[cubic-bezier(0.22,1,0.36,1)] ${visible ? 'translate-x-0 scale-100 opacity-100' : 'invisible pointer-events-none translate-x-10 scale-[0.985] opacity-0'}`}
    style={{ width: inline ? 380 : desktop ? 'clamp(500px, 34vw, 540px)' : 'calc(100vw - 32px)', maxWidth: inline ? undefined : desktop ? 'calc(100vw - 360px)' : '560px' }}
  >{children}</aside>;
}
