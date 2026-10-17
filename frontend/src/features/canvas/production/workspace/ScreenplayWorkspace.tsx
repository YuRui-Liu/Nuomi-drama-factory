import { useEffect, useState, type ReactNode } from 'react';
import { Button } from '@/components/ui/button';
import { ProductionWorkspaceLayout } from './ProductionWorkspaceLayout';

type ScreenplaySection = 'screenplay' | 'history' | 'handoff';

export interface ScreenplayWorkspaceProps {
  title: string;
  status: string;
  onBack: () => void;
  navigation: ReactNode;
  children: ReactNode;
  readingTarget?: { document_id: string; revision_id: string; offset: number } | null;
  history: ReactNode;
  handoff?: ReactNode;
  inspector?: ReactNode;
  actions: ReactNode;
  hasUnsavedChanges?: boolean;
  canDiscardChanges?: boolean;
  onDiscardChanges?: () => void;
}

export function ScreenplayWorkspace({
  title, status, onBack, navigation, children, readingTarget, history, handoff, inspector, actions,
  hasUnsavedChanges = false, canDiscardChanges = true, onDiscardChanges,
}: ScreenplayWorkspaceProps) {
  const [section, setSection] = useState<ScreenplaySection>('screenplay');
  const hasHandoff = Boolean(handoff);

  useEffect(() => {
    if (hasHandoff) setSection('handoff');
    else setSection(current => current === 'handoff' ? 'screenplay' : current);
  }, [hasHandoff]);

  useEffect(() => {
    if (readingTarget) setSection('screenplay');
  }, [readingTarget]);

  const tabs: Array<{ id: ScreenplaySection; label: string; content: ReactNode }> = [
    { id: 'screenplay', label: '正文与审阅', content: children },
    { id: 'history', label: '版本记录', content: history },
    ...(hasHandoff ? [{ id: 'handoff' as const, label: '交接预览', content: handoff }] : []),
  ];
  const active = tabs.find(tab => tab.id === section) ?? tabs[0];

  return <div className="screenplay-workspace-shell">
    <ProductionWorkspaceLayout title={title} status={status} onBack={onBack} navigation={navigation}
      inspector={inspector} actions={actions}>
      <div className="screenplay-workspace">
        <div className="screenplay-workspace-tabs" role="tablist" aria-label="剧本工作区视图">
          {tabs.map(tab => <button key={tab.id} type="button" role="tab" aria-selected={active.id === tab.id}
            aria-controls={`screenplay-workspace-panel-${tab.id}`} id={`screenplay-workspace-tab-${tab.id}`}
            className={active.id === tab.id ? 'screenplay-workspace-tab is-active' : 'screenplay-workspace-tab'}
            onClick={() => setSection(tab.id)}>{tab.label}</button>)}
        </div>
        {hasUnsavedChanges && onDiscardChanges && <div className="screenplay-workspace-dirty" role="status">
          <span>当前有未保存修改。取消修改会恢复到最近保存的版本。</span>
          <Button type="button" variant="outline" size="sm" disabled={!canDiscardChanges} onClick={onDiscardChanges}>取消修改并还原</Button>
        </div>}
        <section id={`screenplay-workspace-panel-${active.id}`} aria-labelledby={`screenplay-workspace-tab-${active.id}`}
          role="tabpanel" tabIndex={0} className="screenplay-workspace-panel">
          {active.content}
        </section>
      </div>
    </ProductionWorkspaceLayout>
  </div>;
}
