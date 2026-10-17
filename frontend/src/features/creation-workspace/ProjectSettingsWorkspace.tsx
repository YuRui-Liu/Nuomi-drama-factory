import { useCallback, useEffect, useRef, useState } from 'react';
import { ArrowLeft, BookOpen, Boxes, Settings2, Users } from 'lucide-react';
import { DialogDescription, DialogTitle } from '@/components/ui/dialog';
import { AgentConfigurationPanel } from '@/features/agent-configuration/AgentConfigurationPanel';
import { CreativeSettingsPanel } from '@/features/creative-settings/CreativeSettingsPanel';
import { DirectionReferencePanel } from '@/features/direction-references/DirectionReferencePanel';
import { TemplateLibraryPanel } from '@/features/template-library/TemplateLibraryPanel';
import type { DirectionKind } from '@/api/directions';
import type { SettingsDraftGuard } from './projectSettingsDraftGuard';
import './project-settings-workspace.css';

export type ProjectSettingsCategory = 'agents' | 'direction' | 'creative' | 'templates';

export interface ProjectSettingsContext {
  instance: string;
  user: string;
  project: string;
  canvasId: string;
  runtime: {
    flush: () => Promise<boolean>;
    isCurrent: () => boolean;
  };
}

const categories: {
  id: ProjectSettingsCategory;
  title: string;
  summary: string;
  icon: typeof Users;
}[] = [
  { id: 'agents', title: '项目 Agent', summary: '成员与专业配置', icon: Users },
  { id: 'direction', title: '默认风格与手法', summary: '项目创作方向', icon: Settings2 },
  { id: 'creative', title: '创作对象与约定', summary: '项目定义与规则', icon: Boxes },
  { id: 'templates', title: '创作模板', summary: '固定模板与采用版本', icon: BookOpen },
];

type ExitTarget =
  | { kind: 'category'; category: ProjectSettingsCategory }
  | { kind: 'directionKind'; directionKind: DirectionKind }
  | { kind: 'close' };

export function ProjectSettingsWorkspace({
  context,
  isCurrent,
  onClose,
  registerCloseRequest,
}: {
  context: ProjectSettingsContext;
  isCurrent: () => boolean;
  onClose: () => void;
  registerCloseRequest?: (request: () => void) => () => void;
}) {
  const [category, setCategory] = useState<ProjectSettingsCategory>('direction');
  const [directionKind, setDirectionKind] = useState<DirectionKind>('style');
  const [pendingExit, setPendingExit] = useState<ExitTarget | null>(null);
  const [guardError, setGuardError] = useState('');
  const [savingDraft, setSavingDraft] = useState(false);
  const draftGuardRef = useRef<SettingsDraftGuard | null>(null);
  const requestExitRef = useRef<() => void>(() => onClose());

  const applyExit = useCallback((target: ExitTarget) => {
    setPendingExit(null);
    setGuardError('');
    if (target.kind === 'close') onClose();
    else if (target.kind === 'category') setCategory(target.category);
    else setDirectionKind(target.directionKind);
  }, [onClose]);

  const requestExit = useCallback((target: ExitTarget) => {
    const guard = draftGuardRef.current;
    if (guard?.isDirty()) {
      setPendingExit(target);
      setGuardError('');
      return;
    }
    applyExit(target);
  }, [applyExit]);

  requestExitRef.current = () => requestExit({ kind: 'close' });

  useEffect(() => {
    const checkContext = () => {
      if (!isCurrent()) onClose();
    };
    checkContext();
    const interval = window.setInterval(checkContext, 500);
    return () => window.clearInterval(interval);
  }, [context.instance, isCurrent, onClose]);

  useEffect(() => {
    if (!registerCloseRequest) return;
    return registerCloseRequest(() => requestExitRef.current());
  }, [registerCloseRequest]);

  const resolveDraftExit = async (action: 'save' | 'discard' | 'continue') => {
    const target = pendingExit;
    if (!target || savingDraft) return;
    if (action === 'continue') {
      setPendingExit(null);
      setGuardError('');
      return;
    }
    const guard = draftGuardRef.current;
    if (action === 'discard') {
      guard?.discard();
      applyExit(target);
      return;
    }
    setSavingDraft(true);
    setGuardError('');
    try {
      const saved = await guard?.save();
      if (saved) applyExit(target);
      else setGuardError('草稿未能保存；当前分类和输入仍保留。');
    } catch {
      setGuardError('草稿未能保存；当前分类和输入仍保留。');
    } finally {
      setSavingDraft(false);
    }
  };

  const selected = categories.find(item => item.id === category)!;
  const CategoryIcon = selected.icon;

  return (
    <div className="project-settings-workspace">
      <header className="project-settings-header">
        <button type="button" className="project-settings-back" onClick={() => requestExit({ kind: 'close' })}>
          <ArrowLeft aria-hidden="true" />
          <span>返回画布</span>
        </button>
        <div className="project-settings-heading">
          <DialogTitle>项目设置</DialogTitle>
          <DialogDescription>管理项目创作默认值。节点局部设置仍在选中节点旁操作。</DialogDescription>
        </div>
        <div className="project-settings-owner"><span>当前项目</span><span>创作默认配置</span></div>
      </header>

      <div className="project-settings-layout">
        <nav className="project-settings-nav" aria-label="项目设置分类" role="tablist" aria-orientation="vertical">
          <p className="project-settings-nav-caption">创作配置</p>
          {categories.map(item => {
            const Icon = item.icon;
            return (
              <button
                key={item.id}
                type="button"
                role="tab"
                aria-selected={category === item.id}
                aria-controls="project-settings-panel"
                className="project-settings-nav-item"
                onClick={() => item.id !== category && requestExit({ kind: 'category', category: item.id })}
                onKeyDown={event => {
                  if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return;
                  event.preventDefault();
                  const index = categories.findIndex(value => value.id === category);
                  const delta = event.key === 'ArrowDown' ? 1 : -1;
                  const next = categories[(index + delta + categories.length) % categories.length];
                  document.getElementById(`settings-category-${next.id}`)?.focus();
                  requestExit({ kind: 'category', category: next.id });
                }}
                id={`settings-category-${item.id}`}
              >
                <Icon aria-hidden="true" />
                <span className="project-settings-nav-copy"><span>{item.title}</span><small>{item.summary}</small></span>
              </button>
            );
          })}
          <p className="project-settings-local-note">节点局部设置<br />仍在选中节点旁调整</p>
        </nav>

        <main id="project-settings-panel" role="tabpanel" aria-labelledby={`settings-category-${category}`} className="project-settings-main">
          <div className="project-settings-section-heading">
            <span className="project-settings-section-icon"><CategoryIcon aria-hidden="true" /></span>
            <div><h2>{selected.title}</h2><p>{selected.summary} · 仅影响后续创作，不改写已有产物。</p></div>
          </div>

          {category === 'agents' && (
            <AgentConfigurationPanel
              key={`${context.instance}:agents`}
              owner={{ user: context.user, project: context.project, canvas: context.canvasId }}
              isCurrent={isCurrent}
              draftGuardRef={draftGuardRef}
            />
          )}
          {category === 'direction' && (
            <section className="project-settings-category-content">
              <div className="project-settings-direction-tabs" role="tablist" aria-label="默认方向类型">
                {(['style', 'technique'] as const).map(kind => (
                  <button
                    key={kind}
                    type="button"
                    role="tab"
                    aria-selected={directionKind === kind}
                    onClick={() => kind !== directionKind && requestExit({ kind: 'directionKind', directionKind: kind })}
                  >
                    {kind === 'style' ? '项目默认风格' : '项目默认手法'}
                  </button>
                ))}
              </div>
              <DirectionReferencePanel
                key={`${context.instance}:direction:${directionKind}`}
                owner={{ user: context.user, project: context.project }}
                kind={directionKind}
                canvasId={context.canvasId}
                defaultProjectScope
                workspaceRuntime={context.runtime}
                isCurrent={isCurrent}
                draftGuardRef={draftGuardRef}
              />
            </section>
          )}
          {category === 'creative' && (
            <CreativeSettingsPanel
              key={`${context.instance}:creative`}
              owner={{ user: context.user, project: context.project, canvas: context.canvasId }}
              selected={[]}
              runtime={context.runtime}
              defaultScope="project"
              isCurrent={isCurrent}
              draftGuardRef={draftGuardRef}
            />
          )}
          {category === 'templates' && (
            <TemplateLibraryPanel
              key={`${context.instance}:templates`}
              owner={{ user: context.user, project: context.project, canvas: context.canvasId }}
              runtime={context.runtime}
              isCurrent={isCurrent}
              draftGuardRef={draftGuardRef}
            />
          )}

          {pendingExit && (
            <section className="project-settings-draft-prompt" role="alertdialog" aria-label="未保存的草稿">
              <div><strong>此分类有未保存的草稿</strong><p>保存草稿后继续，或放弃更改。继续编辑会留在当前分类。</p></div>
              {guardError && <p role="alert">{guardError}</p>}
              <div className="project-settings-draft-actions">
                <button type="button" disabled={savingDraft} onClick={() => void resolveDraftExit('save')}>保存草稿</button>
                <button type="button" disabled={savingDraft} onClick={() => void resolveDraftExit('discard')}>放弃更改</button>
                <button type="button" disabled={savingDraft} onClick={() => void resolveDraftExit('continue')}>继续编辑</button>
              </div>
            </section>
          )}
        </main>

        <aside className="project-settings-aside" aria-label="当前设置影响范围">
          <h3>这次修改影响什么</h3>
          <p className="project-settings-aside-highlight">项目默认值</p>
          <p>后续任务在没有更具体配置时使用此版本。</p>
          <h4>局部配置优先</h4>
          <p>节点 → 分组 → 画布 → 项目<br />使用最近一层明确配置。</p>
          <h4>已采用版本保留</h4>
          <p>已有剧本、分镜、图片和声音不会因默认设置自动替换。</p>
        </aside>
      </div>

      <footer className="project-settings-footer">
        <span>按类别保存，离开前保护草稿；配置不会自动启动生成。</span>
        <span>项目设置</span>
      </footer>
    </div>
  );
}
