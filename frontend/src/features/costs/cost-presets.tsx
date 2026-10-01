// SPDX-License-Identifier: Elastic-2.0
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Button } from '@/components/ui/button';
import { p } from '@/lib/api-path';
import { costRequest, errorText, useCostText } from './cost-settings-common';

export type CostPresetResponse = {
    platforms: { provider: string; title: string; description: string }[];
    rules: { id: string; model: string; price: string; unit: string; currency: string; source_url: string }[];
    installed: boolean;
};

export function CostPresets({ project }: { project?: string }) {
    const text = useCostText();
    const client = useQueryClient();
    const presets = useQuery({
        queryKey: ['cost-settings', 'presets'],
        queryFn: () => costRequest<CostPresetResponse>('api/v1/cost-settings/presets', undefined, 'get'),
    });
    const refresh = async () => {
            await Promise.all([
                client.invalidateQueries({ queryKey: ['cost-settings'] }),
                client.invalidateQueries({ queryKey: ['project-costs'] }),
            ]);
    };
    const recover = useMutation({
        mutationFn: () => costRequest<{ historical?: { measured: number; failed: number }; refresh?: { measured: number; failed: number; missing_account: number }; backfill?: { imported: number; existing: number } }>(p`api/v1/projects/${project!}/costs/recover`),
        onSuccess: refresh,
    });
    const install = useMutation({
        mutationFn: () => costRequest<{ skipped?: string[] }>('api/v1/cost-settings/presets'),
        onSuccess: async () => {
            await refresh();
            if (project) recover.mutate();
        },
    });
    return <section className="space-y-4 rounded-xl border border-primary/30 bg-primary/5 p-4" aria-label={text('平台推荐计费', 'Platform billing presets')}>
        <div><h3 className="font-medium">{text('按你的平台预填计费', 'Billing presets for your platforms')}</h3>
            <p className="mt-1 text-sm text-muted-foreground">{text('直接使用推荐配置，无需填写规则 ID、计价基数或工作流。', 'Use the recommended setup without entering rule IDs, pricing bases, or workflows.')}</p></div>
        {presets.isPending && <p>{text('正在读取平台配置…', 'Loading platform settings…')}</p>}
        {presets.data?.platforms.map(platform => <div key={platform.provider}><h4 className="text-sm font-medium">{platform.title}</h4><p className="text-sm text-muted-foreground">{platform.description}</p></div>)}
        {!!presets.data?.rules.length && <ul className="space-y-2 text-sm">{presets.data.rules.map(rule => <li key={rule.id} className="flex flex-wrap justify-between gap-2"><span>{rule.model}</span><span>{rule.currency === 'RH_CREDIT' ? text('按实际扣除 RH 币', 'Actual RH credits charged') : <>{rule.currency === 'CNY' ? '¥' : `${rule.currency} `}{rule.price} / {text(({ call: '次', item: '项', second: '秒', credit: '积分' } as Record<string, string>)[rule.unit] || rule.unit, rule.unit)}</>} <a className="ml-2 underline text-muted-foreground" href={rule.source_url} target="_blank" rel="noreferrer">{text('价格来源', 'Pricing source')}</a></span></li>)}</ul>}
        {presets.data && <p className="text-xs text-muted-foreground">{text('公开单价用于估算；平台实际账单优先。历史调用需补算后显示费用。', 'Public prices are estimates; actual billing takes precedence. Historical calls need repricing before costs appear.')}</p>}
        {(presets.error || install.error || recover.error) && <p role="alert" className="text-sm text-destructive">{recover.error ? text('计费配置已保留，历史消费同步失败，请重试同步。', 'Billing settings are preserved. Historical usage sync failed; retry syncing.') : errorText(presets.error || install.error, text)}</p>}
        {presets.error ? <Button variant="outline" onClick={() => presets.refetch()}>{text('重试读取平台', 'Retry loading platforms')}</Button> : <Button disabled={!presets.data || presets.data.installed || install.isPending} onClick={() => install.mutate()}>{install.isPending ? text('正在配置…', 'Applying…') : presets.data?.installed ? text('推荐计费已启用', 'Recommended billing enabled') : text('一键使用推荐计费', 'Use recommended billing')}</Button>}
        {project && (presets.data?.installed || install.isSuccess) && <Button variant="outline" disabled={recover.isPending || install.isPending} onClick={() => recover.mutate()}>{recover.isPending ? text('正在同步历史消费…', 'Syncing historical usage…') : text('同步历史消费', 'Sync historical usage')}</Button>}
        {recover.isSuccess ? <p role="status" className="text-sm">{text('已同步可恢复的消费，缺失历史凭据的费用仍待核算。', 'Recoverable usage has been synced. Costs without historical evidence remain unpriced.')}</p> : install.isSuccess && <p role="status" className="text-sm">{text('推荐计费已配置，可补算已有调用记录。', 'Recommended billing is configured. Existing calls can now be repriced.')}</p>}
        {!!install.data?.skipped?.length && <p className="text-sm text-muted-foreground">{text('已有自定义计价的项目已跳过，保留原有规则。', 'Existing custom pricing has been preserved without replacement.')}</p>}
        {recover.data?.refresh && <p className="text-xs text-muted-foreground">{text('已同步调用扣费记录', 'Synced attempt billing records')}: {recover.data.refresh.measured} · {text('已找回历史扣费记录', 'Recovered historical billing records')}: {recover.data.historical?.measured ?? 0}{recover.data.refresh.failed + recover.data.refresh.missing_account + (recover.data.historical?.failed ?? 0) > 0 && <> · {text('部分任务暂时无法查询，可重试同步。', 'Some tasks could not be queried. Retry syncing.')}</>}</p>}
    </section>;
}
