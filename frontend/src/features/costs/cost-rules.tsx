// SPDX-License-Identifier: Elastic-2.0
import { useState, type FormEvent } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { CostPresets } from './cost-presets';
import { costDate } from './cost-format';
import { p } from '@/lib/api-path';
import { costRequest, decimal, errorText, Field, interval, StartFields, useCostText } from './cost-settings-common';
type Item = {
    unit: string;
    unit_price: string;
    basis: string;
    step: string;
    minimum: string;
};
type Rule = {
    id: string;
    version: string;
    provider: string | null;
    account_id: string | null;
    media_type: string;
    model: string | null;
    workflow: string | null;
    specifications: [
        string,
        string
    ][];
    starts_at: string;
    ends_at: string | null;
    currency: string;
    cny_rate: string | null;
    items: Item[];
};
const units = ['item', 'second', 'call', 'character', 'input_tokens', 'output_tokens', 'credit'];
const emptyItem = (): Item => ({ unit: 'item', unit_price: '', basis: '1', step: '1', minimum: '0' });
export function CostRules({ project }: { project?: string }) {
    const text = useCostText();
    return <div className="space-y-5"><CostPresets project={project}/><details className="rounded-lg border p-4"><summary className="cursor-pointer text-sm font-medium">{text('高级：自定义计价规则', 'Advanced: custom pricing rules')}</summary><div className="mt-4"><CustomCostRules /></div></details></div>;
}
function CustomCostRules() {
    const text = useCostText();
    const client = useQueryClient();
    const [items, setItems] = useState<Item[]>([emptyItem()]);
    const [specs, setSpecs] = useState<[
        string,
        string
    ][]>([]);
    const [invalid, setInvalid] = useState(false);
    const [stopInvalid, setStopInvalid] = useState(false);
    const list = useQuery({ queryKey: ['cost-settings', 'price-rules'], queryFn: () => costRequest<Rule[]>('api/v1/cost-settings/price-rules', undefined, 'get') });
    const refresh = async () => { await Promise.all([client.invalidateQueries({ queryKey: ['cost-settings', 'price-rules'] }), client.invalidateQueries({ queryKey: ['project-costs'] })]); };
    const create = useMutation({ mutationFn: (body: unknown) => costRequest('api/v1/cost-settings/price-rules', body), onSuccess: refresh });
    const stop = useMutation({ mutationFn: ({ rule, ends_at }: {
            rule: Rule;
            ends_at: string;
        }) => costRequest(p `api/v1/cost-settings/price-rules/${rule.id}/versions/${rule.version}/stop`, { ends_at }), onSuccess: refresh });
    function submit(e: FormEvent<HTMLFormElement>) { e.preventDefault(); setInvalid(false); const d = new FormData(e.currentTarget); try {
        const optional = (key: string) => String(d.get(key) || '').trim() || null;
        const currency = String(d.get('currency') || 'CNY').trim();
        const rate = optional('cny_rate');
        if (new Set(items.map(x => x.unit)).size !== items.length || new Set(specs.map(s => s[0])).size !== specs.length || specs.some(s => !s[0].trim()))
            throw Error();
        if (currency === 'CNY' && rate && Number(rate) !== 1)
            throw Error();
        create.mutate({ id: d.get('id'), version: d.get('version'), provider: optional('provider'), account_id: optional('account_id'), model: optional('model'), workflow: optional('workflow'), media_type: d.get('media_type'), ...interval(d), currency, cny_rate: rate === null ? null : decimal(rate, true), specifications: specs, items: items.map(item => ({ ...item, unit_price: decimal(item.unit_price), basis: decimal(item.basis, true), step: decimal(item.step, true), minimum: decimal(item.minimum) })) });
    }
    catch {
        setInvalid(true);
    } }
    return <div className="space-y-5"><Button variant="outline" disabled={list.isFetching} onClick={() => list.refetch()}>{text('刷新规则', 'Refresh rules')}</Button>{list.isPending && <p>{text('加载中…', 'Loading…')}</p>}{(list.error || create.error || stop.error) && <p role="alert">{errorText(list.error || create.error || stop.error, text)}</p>}{stopInvalid && <p role="alert">{text('未来版本尚未生效，当前时间不能早于生效时间，请在生效后停用。', 'Cannot stop a future version before its start. Stop it after it starts.')}</p>}{list.data?.map(rule => <article className="space-y-2 rounded-lg border p-3" key={`${rule.id}:${rule.version}`}><h3 className="font-medium">{rule.id} / {rule.version}</h3><p>{[rule.provider || text('全部渠道', 'All providers'), rule.account_id || text('全部账户', 'All accounts'), text(({ image: '图片', audio: '音频', video: '视频', text: '文本' } as Record<string, string>)[rule.media_type] || rule.media_type, rule.media_type), rule.model || text('全部模型', 'All models'), rule.workflow || text('全部工作流', 'All workflows')].join(' · ')} {rule.specifications.map(s => s.join('=')).join(', ')}</p><p>{costDate(rule.starts_at)} — {rule.ends_at ? costDate(rule.ends_at) : text('长期', 'Open ended')}</p>{rule.items.map(item => <p key={item.unit}>{text(['项', '秒', '调用', '字符', '输入词元', '输出词元', '积分'][units.indexOf(item.unit)] || item.unit, item.unit)}: {item.unit_price} {rule.currency} / {item.basis} · {text('步长', 'Step')} {item.step} · {text('最低用量', 'Minimum usage')} {item.minimum}</p>)}<p>{text('人民币汇率', 'CNY rate')}: {rule.currency === 'RH_CREDIT' ? text('无需折算，套餐独立计费', 'No conversion; package billing') : rule.cny_rate || (rule.currency === 'CNY' ? '1' : text('未知，待核算', 'Unknown; unpriced'))}</p><Button variant="outline" disabled={stop.isPending || Boolean(rule.ends_at && new Date(rule.ends_at) <= new Date())} onClick={() => { const now = new Date(); setStopInvalid(false); if (now <= new Date(rule.starts_at)) {
        setStopInvalid(true);
        return;
    } stop.mutate({ rule, ends_at: now.toISOString() }); }}>{text('停用未来匹配', 'Stop future matching')}</Button></article>)}
 <h3 className="font-medium">{text('新增价格版本', 'New price version')}</h3><form onSubmit={submit} className="grid gap-3 sm:grid-cols-2">{[['id', text('规则 ID', 'Rule ID')], ['version', text('版本', 'Version')], ['provider', text('渠道（可选）', 'Provider (optional)')], ['account_id', text('账户别名（可选）', 'Account alias (optional)')], ['model', text('模型（可选）', 'Model (optional)')], ['workflow', text('工作流（可选）', 'Workflow (optional)')]].map(([name, label]) => <Field key={name} name={name} label={label} required={name === 'id' || name === 'version'}/>)}<label className="grid gap-1">{text('媒体类型', 'Media type')}<select className="rounded border p-2" name="media_type" defaultValue="image">{[['image', '图片'], ['audio', '音频'], ['video', '视频'], ['text', '文本']].map(([v, zh]) => <option key={v} value={v}>{text(zh, v)}</option>)}</select></label><label>{text('币种', 'Currency')}<Input name="currency" required defaultValue="CNY"/></label><Field name="cny_rate" label={text('人民币汇率（未知留空）', 'CNY rate (blank if unknown)')}/><StartFields />
 <div className="sm:col-span-2 space-y-2"><p>{text('规格条件（可选；全部匹配）', 'Specifications (optional; all must match)')}</p>{specs.map(([key, value], index) => <div key={index} className="flex gap-2"><Field label={text('规格名称', 'Specification key')} value={key} onChange={v => setSpecs(specs.map((s, i) => i === index ? [v, s[1]] : s))}/><Field label={text('规格值', 'Specification value')} value={value} onChange={v => setSpecs(specs.map((s, i) => i === index ? [s[0], v] : s))}/><Button type="button" variant="outline" onClick={() => setSpecs(specs.filter((_, i) => i !== index))}>{text('移除', 'Remove')}</Button></div>)}<Button type="button" variant="outline" onClick={() => setSpecs([...specs, ['', '']])}>{text('添加规格条件', 'Add specification')}</Button></div>
 <div className="sm:col-span-2 space-y-3"><p>{text('计费项：按步长向上取整，并应用最低用量；单价按基数计费。', 'Usage rounds up to step and minimum usage; price is charged per basis.')}</p>{items.map((item, index) => <fieldset key={index} className="grid gap-2 rounded border p-3 sm:grid-cols-3"><legend>{text('计费项', 'Billing item')} {index + 1}</legend><label>{text('单位', 'Unit')}<select className="w-full rounded border p-2" value={item.unit} onChange={e => setItems(items.map((x, i) => i === index ? { ...x, unit: e.target.value } : x))}>{units.map((unit, i) => <option key={unit} value={unit}>{text(['项', '秒', '调用', '字符', '输入词元', '输出词元', '积分'][i], unit)}</option>)}</select></label>{(['unit_price', 'basis', 'step', 'minimum'] as const).map((key, i) => <Field key={key} required label={text(['单价', '计价基数', '用量步长', '最低用量'][i], ['Unit price', 'Pricing basis', 'Usage step', 'Minimum usage'][i])} value={item[key]} onChange={v => setItems(items.map((x, j) => j === index ? { ...x, [key]: v } : x))}/>)}<Button type="button" variant="outline" disabled={items.length === 1} onClick={() => setItems(items.filter((_, i) => i !== index))}>{text('移除计费项', 'Remove item')}</Button></fieldset>)}<Button type="button" variant="outline" onClick={() => setItems([...items, emptyItem()])}>{text('添加计费项', 'Add billing item')}</Button></div>
 {invalid && <p className="sm:col-span-2" role="alert">{text('请检查数字和时间：单价必填且非负，基数和步长须大于零，单位和规格名称不可重复，结束须晚于生效。', 'Check numbers and dates: price must be nonnegative; basis and step positive; units and specification keys unique; end after start.')}</p>}<Button type="submit" disabled={create.isPending}>{text('创建价格版本', 'Create price version')}</Button>{create.isSuccess && <p role="status">{text('价格版本已创建', 'Price version created')}</p>}</form></div>;
}
