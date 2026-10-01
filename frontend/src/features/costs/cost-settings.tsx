// SPDX-License-Identifier: Elastic-2.0
import { useState, type FormEvent } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { p } from '@/lib/api-path';
import { useAuthStore } from '@/stores/auth-store';
import { refreshProjectCosts } from '@/lib/queries/project-costs';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogTitle, DialogDescription } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { money as costMoney, costDate } from './cost-format';
import { CostRules } from './cost-rules';
import { costRequest, decimal, errorText, interval, microsYuan, StartFields, useCostText, yuanMicros } from './cost-settings-common';
export { yuanMicros } from './cost-settings-common';
type Preview = {
    preview_id: string;
    affected_count: number;
    total_cents: number;
    records: {
        attempt_id: string;
        old_cents: number | null;
        new_cents: number | null;
        matched_rule_versions: {
            id: string;
            version: string;
        }[];
    }[];
};
export function CostSettingsActions({ project }: {
    project: string;
}) {
    const text = useCostText();
    const role = useAuthStore(s => s.role);
    const admin = role === 'owner' || role === 'admin';
    const client = useQueryClient();
    const [panel, setPanel] = useState<'rules' | 'subscriptions' | 'preview' | null>(null);
    const [preview, setPreview] = useState<Preview | null>(null);
    const generate = useMutation({ mutationFn: () => costRequest<Preview>(p `api/v1/projects/${project}/costs/reprice-preview`), onSuccess: data => { setPreview(data); apply.reset(); } });
    const apply = useMutation({ mutationFn: () => costRequest(p `api/v1/projects/${project}/costs/reprice-apply`, { preview_id: preview!.preview_id }), onSuccess: async () => { await refreshProjectCosts(client, project); setPreview(null); } });
    return <><div className="flex flex-wrap gap-2">{admin && <><Button variant="outline" onClick={() => setPanel('rules')}>{text('计价规则', 'Price rules')}</Button><Button variant="outline" onClick={() => setPanel('subscriptions')}>{text('费用设置', 'Cost settings')}</Button></>}<Button variant="outline" onClick={() => { setPanel('preview'); generate.mutate(); }}>{text('补算预览', 'Reprice preview')}</Button></div><Dialog open={panel !== null} onOpenChange={open => { if (!open)
        setPanel(null); }}><DialogContent className="sm:max-w-3xl max-h-[85vh] overflow-y-auto"><DialogTitle>{panel === 'rules' ? text('计价规则', 'Price rules') : panel === 'subscriptions' ? text('费用设置', 'Cost settings') : text('补算预览', 'Reprice preview')}</DialogTitle><DialogDescription>{text('推荐计费可一键配置并同步历史消费；没有扣费凭据的金额保持待核算。', 'Apply recommended billing and sync historical usage in one step. Amounts without billing evidence remain unpriced.')}</DialogDescription>{panel === 'rules' && admin && <CostRules project={project} />}{panel === 'subscriptions' && admin && <SubscriptionEditor />}{panel === 'preview' && <div className="space-y-4">{(generate.error || apply.error) && <p role="alert">{errorText(generate.error || apply.error, text)}</p>}{generate.isPending && <p>{text('正在生成…', 'Generating…')}</p>}{preview && <><p>{text('受影响记录', 'Affected records')}: {preview.affected_count} · {text('补算金额', 'Repriced amount')}: {costMoney(preview.total_cents)}</p><div className="overflow-x-auto"><table className="w-full text-sm"><thead><tr>{[text('调用', 'Attempt'), text('原金额', 'Before'), text('新金额', 'After'), text('规则版本', 'Rule versions')].map(x => <th key={x} className="p-2 text-left">{x}</th>)}</tr></thead><tbody>{preview.records.map(r => <tr key={r.attempt_id}><td className="p-2 break-all">{r.attempt_id}</td><td>{r.old_cents === null ? text('待核算', 'Unpriced') : costMoney(r.old_cents)}</td><td>{r.new_cents === null ? text('待核算', 'Unpriced') : costMoney(r.new_cents)}</td><td>{r.matched_rule_versions.map(v => `${v.id} / ${v.version}`).join(', ')}</td></tr>)}</tbody></table></div>{preview.records.length === 0 && <p>{text('没有可补算的记录。', 'No records to reprice.')}</p>}</>}<div className="flex gap-2"><Button variant="outline" disabled={generate.isPending || apply.isPending} onClick={() => generate.mutate()}>{text('重新生成预览', 'Regenerate preview')}</Button><Button disabled={!preview?.records.length || generate.isPending || apply.isPending || apply.isError} onClick={() => apply.mutate()}>{text('应用补算', 'Apply repricing')}</Button></div>{apply.isSuccess && <p role="status">{text('补算已应用，项目费用已刷新。', 'Repricing applied and project costs refreshed.')}</p>}</div>}</DialogContent></Dialog></>;
}
type Subscription = {
    id: string;
    provider: string;
    account_id: string;
    starts_at: string;
    ends_at: string | null;
    amount_micros: number | null;
    currency: string;
    original_amount: string | null;
    cny_rate: string | null;
    source: string | null;
};
function SubscriptionEditor() {
    const text = useCostText();
    const client = useQueryClient();
    const [validation, setValidation] = useState(false);
    const [editing, setEditing] = useState<Subscription | null>(null);
    const query = useQuery({ queryKey: ['cost-settings', 'subscriptions'], queryFn: () => costRequest<Subscription[]>('api/v1/cost-settings/subscriptions', undefined, 'get') });
    const save = useMutation({ mutationFn: (body: unknown) => costRequest('api/v1/cost-settings/subscriptions', body), onSuccess: async () => { await Promise.all([client.invalidateQueries({ queryKey: ['cost-settings', 'subscriptions'] }), client.invalidateQueries({ queryKey: ['project-costs'] })]); } });
    const submit = (event: FormEvent<HTMLFormElement>) => { event.preventDefault(); setValidation(false); const d = new FormData(event.currentTarget); try {
        const optional = (key: string) => String(d.get(key) || '').trim() || null;
        save.mutate({ id: d.get('id'), provider: d.get('provider'), account_id: d.get('account_id'), ...interval(d), amount_micros: yuanMicros(String(d.get('amount') || '')), currency: optional('currency') || 'CNY', original_amount: optional('original_amount') === null ? null : decimal(optional('original_amount')!), cny_rate: optional('cny_rate') === null ? null : decimal(optional('cny_rate')!, true), source: optional('source') });
    }
    catch {
        setValidation(true);
    } };
    return <div className="space-y-4">{query.isPending && <p>{text('加载中…', 'Loading…')}</p>}{(query.error || save.error) && <p role="alert">{errorText(query.error || save.error, text)}</p>}{query.data?.map(s => <article className="rounded border p-3" key={s.id}><p>{s.id} · {s.provider} / {s.account_id}</p><p>{costDate(s.starts_at)} — {s.ends_at ? costDate(s.ends_at) : text('长期', 'Open ended')} · {s.amount_micros === null ? text('未登记', 'Not recorded') : costMoney(s.amount_micros / 10000)}</p><Button variant="outline" onClick={() => { setEditing(s); save.reset(); }}>{text('编辑订阅', 'Edit subscription')}</Button></article>)}<form key={editing?.id || 'new'} onSubmit={submit} className="grid gap-3 sm:grid-cols-2">{[['id', text('订阅 ID', 'Subscription ID')], ['provider', text('渠道', 'Provider')], ['account_id', text('账户别名', 'Account alias')]].map(([name, label]) => <label className="grid gap-1" key={name}>{label}<Input name={name} required defaultValue={editing?.[name as 'id' | 'provider' | 'account_id'] || ''}/></label>)}{editing ? <><label>{text('生效时间', 'Starts at')}<Input name="starts_at" required defaultValue={editing.starts_at}/></label><label>{text('结束时间（可选）', 'Ends at (optional)')}<Input name="ends_at" defaultValue={editing.ends_at || ''}/></label></> : <StartFields />}{[['amount', text('人民币金额（元，可留空）', 'CNY amount (yuan, optional)'), editing?.amount_micros === null || !editing ? '' : microsYuan(editing.amount_micros)], ['currency', text('原始币种（仅供参考）', 'Original currency (reference only)'), editing?.currency || 'CNY'], ['original_amount', text('原始金额（可选）', 'Original amount (optional)'), editing?.original_amount || ''], ['cny_rate', text('人民币汇率（可选，不自动换算）', 'CNY rate (optional; no automatic conversion)'), editing?.cny_rate || ''], ['source', text('来源说明', 'Source'), editing?.source || '']].map(([name, label, value]) => <label key={name} className="grid gap-1">{label}<Input name={name} defaultValue={value}/></label>)}<p className="sm:col-span-2 text-muted-foreground">{text('人民币金额留空表示未登记，不代表免费。原始金额和汇率仅作为参考记录。', 'Blank CNY amount means not recorded, not free. Original amount and exchange rate are reference evidence only.')}</p>{validation && <p role="alert">{text('请填写有效金额（最多 6 位小数）及先后正确的时间。', 'Enter valid amounts (up to 6 decimals) and an end after the start.')}</p>}<Button type="submit" disabled={save.isPending}>{text('保存订阅', 'Save subscription')}</Button>{editing && <Button variant="outline" type="button" onClick={() => setEditing(null)}>{text('新增订阅', 'New subscription')}</Button>}{save.isSuccess && <p role="status">{text('订阅已保存', 'Subscription saved')}</p>}</form></div>;
}
