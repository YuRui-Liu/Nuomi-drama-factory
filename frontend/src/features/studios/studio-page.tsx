import { Component, lazy, Suspense, type ReactNode } from 'react';
import { Link, useRouterState } from '@tanstack/react-router';
import { Button, buttonVariants } from '@/components/ui/button';
import { StudioUnsavedGuard } from './studio-unsaved';
import { readStudioContext, studioLocation } from './studio-context';
import type { StudioModule } from './studio-api';
import './studio-workspace.css';

const CharacterStudio = lazy(() => import('./character-studio').then(m => ({ default: m.CharacterStudio })));
const DirectorStudio = lazy(() => import('./director-studio').then(m => ({ default: m.DirectorStudio })));
const PrevisStudio = lazy(() => import('./previs/previs-studio').then(m => ({ default: m.PrevisStudio })));
const IntroStudio = lazy(() => import('./intro/intro-studio').then(m => ({ default: m.IntroStudio })));
const TeamStudio = lazy(() => import('./agent-team/team-studio').then(m => ({ default: m.TeamStudio })));
const editors = { character: CharacterStudio, director: DirectorStudio, previs: PrevisStudio, intro: IntroStudio, 'agent-team': TeamStudio };
const tabs: { id: StudioModule; label: string }[] = [
  { id: 'agent-team', label: 'Agent Team' },
  { id: 'character', label: '角色造型室' }, { id: 'director', label: '导演自定义' },
  { id: 'previs', label: '3D 预演' }, { id: 'intro', label: '片头制作' },
];

class StudioErrorBoundary extends Component<{ children: ReactNode }, { error: boolean }> {
  state = { error: false };
  static getDerivedStateFromError() { return { error: true }; }
  render() {
    return this.state.error ? <div role="alert" className="rounded-xl border p-6">工作室加载失败，已保存的草稿仍保留。<Button className="ml-3" onClick={() => window.location.reload()}>重新加载</Button></div> : this.props.children;
  }
}

export function StudioPage({ project }: { project: string }) {
  const search = useRouterState({ select: state => state.location.searchStr });
  const context = readStudioContext(search, project);
  const Editor = editors[context.studio];
  return <div className="studio-shell">
    <header className="studio-switcher">
      <h1 className="shrink-0 text-sm font-semibold">创作工作室</h1>
    <nav aria-label="创作工作室" className="flex min-w-0 gap-1 overflow-x-auto">
      {tabs.map(tab => <Link key={tab.id} className={buttonVariants({variant:tab.id === context.studio ? 'default' : 'ghost'})} to={studioLocation(project, tab.id, context)} aria-current={tab.id === context.studio ? 'page' : undefined}>{tab.label}</Link>)}
    </nav>
      <div className="ml-auto flex shrink-0 items-center gap-3 text-xs text-muted-foreground">{context.episode && <span>第 {context.episode} 集{context.group ? ` · ${context.group}` : ''}</span>}{context.character && <span>{context.character}</span>}{context.returnTo && <Link className={buttonVariants({variant:'outline',size:'sm'})} to={context.returnTo}>返回来源</Link>}</div>
    </header>
    <div className="studio-editor"><StudioErrorBoundary key={`${project}:${context.studio}`}><Suspense fallback={<p role="status" className="p-8 text-muted-foreground">正在加载工作室…</p>}><Editor project={project} /></Suspense></StudioErrorBoundary></div>
    <StudioUnsavedGuard key={`guard:${project}:${context.studio}`} module={context.studio} />
  </div>;
}
