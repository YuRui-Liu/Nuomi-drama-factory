import { useEffect, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Button } from '@/components/ui/button';
import { useProductionAction } from './useProductionAction';
import { productionActionErrorFallback } from './productionActionError';
import { episodeAssetSupplementsApi, resolveReusableAssetReferences, type EpisodeAssetSupplementItem, type EpisodeAssetSupplementRun, type EpisodeAssetType, type ReusableAssetReference } from './episodeAssetSupplementsApi';

const actionLabel: Record<EpisodeAssetSupplementItem['action'], string> = {
  new_asset: '新增资产需求',
  reuse: '沿用已有资产',
  stable_variant: '稳定变体',
  keep_as_text: '保留为剧本文字',
};

const assetLabels: Record<EpisodeAssetType, string> = {
  character: '人物', scene: '场景', prop: '道具',
};
const assetLabel = (type: EpisodeAssetSupplementItem['asset_type']) => type ? assetLabels[type] : '文字需求';

const activeRun = (run: EpisodeAssetSupplementRun) => run.status === 'pending' || run.status === 'running';

function SupplementItem({ item, onCreateAsset, missingImage = false }: {
  item: EpisodeAssetSupplementItem;
  onCreateAsset?: (item: EpisodeAssetSupplementItem) => void;
  missingImage?: boolean;
}) {
  return <article className="space-y-2 rounded border p-3 text-sm">
    <div className="flex flex-wrap items-baseline gap-2">
      <strong>{actionLabel[item.action]}</strong>
      <span className="text-xs text-muted-foreground">{assetLabel(item.asset_type)}</span>
      {item.name && <span>{item.name}</span>}
      {item.variant_name && <span>· {item.variant_name}</span>}
      {onCreateAsset && (item.action === 'new_asset' || item.action === 'stable_variant' ||
        (item.action === 'reuse' && missingImage)) &&
        <Button variant="outline" size="sm" onClick={() => onCreateAsset(item)}>生成资产图</Button>}
    </div>
    {item.action === 'reuse' && <p className="text-xs text-muted-foreground">
      {item.existing_variant_id
        ? `现有变体：${item.existing_asset_name || item.name} · ${item.existing_variant_name || item.variant_name}`
        : `现有资产：${item.existing_asset_name || item.name}`}
    </p>}
    {item.action === 'reuse' && missingImage && <p className="text-xs text-amber-700 dark:text-amber-300">
      已匹配到现有资产，但还没有可用的图片参考。
    </p>}
    {item.action === 'stable_variant' && <p className="text-xs text-muted-foreground">
      基础资产：{item.base_asset_name || item.name}
    </p>}
    <p>{item.reason || '根据剧本来源保留本项。'}</p>
    {item.action === 'new_asset' && <p className="text-xs text-muted-foreground">
      这是本集的资产需求记录；后续可单独生成图像。
    </p>}
    {item.evidence.length > 0 && <details className="rounded border px-2 py-1">
      <summary className="cursor-pointer text-xs text-muted-foreground">查看剧本依据</summary>
      {item.evidence.map((evidence, index) => <blockquote key={`${evidence.block_id}:${index}`} className="mt-2 whitespace-pre-wrap break-words border-l-2 pl-2 text-xs text-muted-foreground">
        {evidence.quote}
      </blockquote>)}
    </details>}
  </article>;
}

export function EpisodeAssetSupplements({ project, documentId, revisionId, saved, onReferences, onUndo, onCreateAsset }: {
  project: string;
  documentId: string;
  revisionId: string;
  saved: boolean;
  onReferences?: (references: ReusableAssetReference[], runId: string) => void;
  onUndo?: (runId: string) => void;
  onCreateAsset?: (item: EpisodeAssetSupplementItem) => void;
}) {
  const queryClient = useQueryClient();
  const { busy, error, perform } = useProductionAction();
  const [referenceError, setReferenceError] = useState('');
  const [missingReferenceItemIds, setMissingReferenceItemIds] = useState<Set<string>>(new Set());
  const [referenceRetry, setReferenceRetry] = useState(0);
  const attemptedReferenceSignature = useRef('');
  const emittedReferenceSignature = useRef('');
  const referenceCallback = useRef(onReferences);
  const undoCallback = useRef(onUndo);
  referenceCallback.current = onReferences;
  undoCallback.current = onUndo;
  const queryKey = ['episode-asset-supplements', project, documentId];
  const runs = useQuery({
    queryKey,
    queryFn: () => episodeAssetSupplementsApi.list(project, documentId),
    refetchInterval: query => query.state.data?.some(run => run.source_revision_id === revisionId && activeRun(run)) ? 2000 : false,
  });
  const currentRuns = (runs.data ?? []).filter(run => run.source_revision_id === revisionId);
  const pending = currentRuns.some(activeRun);
  const latestReady = currentRuns.find(run => run.status === 'ready' && !run.stale);
  const referenceItems = latestReady?.items.filter(item => item.action === 'reuse') ?? [];
  const referenceSignature = latestReady
    ? JSON.stringify([latestReady.id, latestReady.source_revision_id,
      referenceItems.map(item => [item.id, item.existing_asset_id, item.existing_variant_id, item.variant_name])])
    : '';

  useEffect(() => {
    if (!saved || !latestReady || !referenceSignature ||
        attemptedReferenceSignature.current === referenceSignature ||
        emittedReferenceSignature.current === referenceSignature) return;
    attemptedReferenceSignature.current = referenceSignature;
    let current = true;
    setReferenceError('');
    setMissingReferenceItemIds(new Set());
    void resolveReusableAssetReferences(project, referenceItems).then(references => {
      if (!current) return;
      referenceCallback.current?.(references, latestReady.id);
      setMissingReferenceItemIds(new Set(references.filter(reference => !reference.reference_url.trim())
        .map(reference => reference.item_id)));
      emittedReferenceSignature.current = referenceSignature;
    }).catch(cause => {
      if (current) setReferenceError(productionActionErrorFallback(cause));
    });
    return () => {
      current = false;
      if (emittedReferenceSignature.current !== referenceSignature &&
          attemptedReferenceSignature.current === referenceSignature) attemptedReferenceSignature.current = '';
    };
  }, [project, latestReady, referenceSignature, referenceRetry, saved]);

  function start() {
    if (!saved || pending || busy) return;
    void perform(`episode-asset-supplement:${documentId}:${revisionId}`, async mutation => {
      await episodeAssetSupplementsApi.start(project, {
        document_id: documentId,
        base_revision_id: revisionId,
        client_mutation_id: mutation,
      });
      await queryClient.invalidateQueries({ queryKey });
    });
  }

  function undo(run: EpisodeAssetSupplementRun) {
    if (busy || activeRun(run)) return;
    void perform(`episode-asset-supplement-undo:${run.id}`, async () => {
      const result = await episodeAssetSupplementsApi.undo(project, run.id);
      if (result.deleted) undoCallback.current?.(run.id);
      await queryClient.invalidateQueries({ queryKey });
    });
  }

  return <section aria-label="本集资产补充" className="space-y-3 rounded-lg border p-3">
    <div className="flex flex-wrap items-center gap-3">
      <h3 className="font-medium">本集资产补充</h3>
      <Button variant="outline" disabled={busy || pending || !saved || !documentId || !revisionId} onClick={start}>
        {pending ? '正在补充…' : busy ? '处理中…' : '补充本集资产'}
      </Button>
      <span className="text-xs text-muted-foreground">
        {runs.isPending ? '正在读取本集资产记录…' : `${currentRuns.length} 条当前版本记录`}
      </span>
    </div>
    {!saved && <p className="text-sm text-amber-700 dark:text-amber-300">请先保存剧本，再补充本集资产。</p>}
    {(error || (saved && runs.error)) && <p role="alert" className="text-sm text-destructive">{error || productionActionErrorFallback(runs.error)}</p>}
    {referenceError && <div role="alert" className="flex flex-wrap items-center gap-2 text-sm text-destructive">
      <span>已有资产图片参考读取失败：{referenceError}</span>
      <Button variant="outline" size="sm" onClick={() => {
        attemptedReferenceSignature.current = '';
        setReferenceRetry(value => value + 1);
      }}>重试</Button>
    </div>}
    {runs.data?.filter(run => run.stale || run.source_revision_id !== revisionId).map(run => <p key={run.id} className="text-xs text-amber-700 dark:text-amber-300">
      有一份补充记录依据旧剧本版本；当前剧本已变化。
    </p>)}
    {currentRuns.map(run => <section key={run.id} className="space-y-3 rounded border p-3">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <p className="text-sm font-medium">
          {run.status === 'pending' || run.status === 'running' ? '正在分析当前保存版本' : run.status === 'ready' ? '已整理当前版本' : run.status === 'needs_rebase' ? '剧本版本已变化' : '补充任务失败'}
          {run.stale && ' · 依据已过期'}
        </p>
        <Button variant="outline" size="sm" disabled={busy || activeRun(run)} onClick={() => undo(run)}>撤销本次补充</Button>
      </div>
      {run.error && <p role="alert" className="text-sm">{run.error}</p>}
      {run.status === 'ready' && run.items.length === 0 && <p className="text-sm text-muted-foreground">没有识别出需独立补充的资产；剧本细节仍由原文保留。</p>}
      {run.items.map(item => <SupplementItem key={item.id} item={item} onCreateAsset={onCreateAsset}
        missingImage={run.id === latestReady?.id && missingReferenceItemIds.has(item.id)} />)}
      {run.rejected_items.map((item, index) => <p key={`${item.name}:${index}`} className="text-xs text-amber-700 dark:text-amber-300">
        {item.name || '未命名候选'}：{item.reason}
      </p>)}
    </section>)}
    <p className="text-xs text-muted-foreground">
      普通表情、动作、临时淋湿和阻挡继续留在剧本里；新增资产通过画布图像节点单独制作。
    </p>
  </section>;
}
