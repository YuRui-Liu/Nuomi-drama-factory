import { useEffect, useState, type ReactNode } from 'react';
import { Button } from '@/components/ui/button';
import './production-workspace.css';

export interface ProductionWorkspaceLayoutProps {
  title: string;
  onBack: () => void;
  navigation: ReactNode;
  children: ReactNode;
  inspector?: ReactNode;
  actions: ReactNode;
  status?: string;
}

export function ProductionWorkspaceLayout({ title, onBack, navigation, children, inspector, actions, status }: ProductionWorkspaceLayoutProps) {
  const [inspectorOpen, setInspectorOpen] = useState(() =>
    typeof window !== 'undefined' && typeof window.matchMedia === 'function'
      ? !window.matchMedia('(max-width: 1024px)').matches
      : true,
  );
  useEffect(() => {
    if (typeof window.matchMedia !== 'function') return;
    const media = window.matchMedia('(max-width: 1024px)');
    const update = (event: MediaQueryListEvent | MediaQueryList) => setInspectorOpen(!event.matches);
    update(media);
    media.addEventListener?.('change', update);
    return () => media.removeEventListener?.('change', update);
  }, []);

  return <div className="production-workspace-layout">
    <header className="production-workspace-header">
      <Button type="button" variant="ghost" className="production-workspace-back" onClick={onBack}>
        <span aria-hidden="true">←</span> 返回画布
      </Button>
      <h2 className="production-workspace-title">{title}</h2>
      <div className="production-workspace-status" role="status">{status ?? ''}</div>
    </header>
    <div className={inspector ? 'production-workspace-columns' : 'production-workspace-columns production-workspace-columns--no-inspector'}>
      <nav aria-label="工作区导航" className="production-workspace-navigation">{navigation}</nav>
      <main className="production-workspace-main" tabIndex={0}>{children}</main>
      {inspector && <aside className="production-workspace-inspector">
        <details open={inspectorOpen} onToggle={event => setInspectorOpen(event.currentTarget.open)}>
          <summary>详情</summary>
          <div className="production-workspace-inspector-content">{inspector}</div>
        </details>
      </aside>}
    </div>
    <footer className="production-workspace-actions">
      <div className="production-workspace-action-content">{actions}</div>
    </footer>
  </div>;
}
