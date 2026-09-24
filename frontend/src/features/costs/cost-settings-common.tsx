// SPDX-License-Identifier: Elastic-2.0
import { useTranslation } from 'react-i18next';
import { HTTPError } from 'ky';
import { api } from '@/lib/api';
import { Input } from '@/components/ui/input';
export function useCostText() { const { i18n } = useTranslation(); return (zh: string, en: string) => i18n.language.startsWith('en') ? en : zh; }
export async function costRequest<T>(path: string, body?: unknown, method: 'get' | 'post' = 'post'): Promise<T> {
    const result = await (method === 'get' ? api.get(path) : api.post(path, body === undefined ? {} : { json: body })).json<{
        ok: boolean;
        data: T;
        error?: string;
    }>();
    if (!result.ok)
        throw new Error(result.error || 'Request failed');
    return result.data;
}
export function errorText(error: unknown, text: ReturnType<typeof useCostText>) {
    if (error instanceof HTTPError && error.response.status === 403)
        return text('只读权限：此操作需要项目编辑权限或全局管理员权限。', 'Read only: this operation requires project editor or global administrator access.');
    if (error instanceof HTTPError && error.response.status === 409)
        return text('数据或版本冲突，请刷新规则或重新生成预览后检查，再提交。', 'Data or version conflict. Refresh rules or regenerate and review the preview before submitting.');
    return text('操作失败，请检查输入和网络后重试。', 'Request failed. Check inputs and connection, then retry.');
}
export function yuanMicros(value: string): number | null {
    if (!value.trim())
        return null;
    if (!/^\d+(\.\d{1,6})?$/.test(value))
        throw new Error('invalid amount');
    const [whole, fraction = ''] = value.split('.');
    const micros = BigInt(whole) * 1000000n + BigInt(fraction.padEnd(6, '0'));
    if (micros > BigInt(Number.MAX_SAFE_INTEGER))
        throw new Error('amount too large');
    return Number(micros);
}
export function microsYuan(value: number): string {
    if (!Number.isSafeInteger(value) || value < 0) throw new Error('Unsafe stored amount');
    const amount = BigInt(value);
    return `${amount / 1000000n}.${String(amount % 1000000n).padStart(6, '0')}`;
}
export function decimal(value: string, positive = false) { if (!/^\d+(\.\d+)?$/.test(value) || value.replace('.', '').length > 50 || (positive && !/[1-9]/.test(value)))
    throw new Error('invalid decimal'); return value; }
export function Field({ label, name, value, onChange, required = false, type = 'text' }: {
    label: string;
    name?: string;
    value?: string;
    onChange?: (value: string) => void;
    required?: boolean;
    type?: string;
}) { return <label className="grid gap-1 text-sm">{label}<Input name={name} value={value} onChange={onChange ? e => onChange(e.target.value) : undefined} required={required} type={type}/></label>; }
export function interval(data: FormData) { const starts_at = new Date(String(data.get('starts_at'))).toISOString(); const end = String(data.get('ends_at') || ''); const ends_at = end ? new Date(end).toISOString() : null; if (ends_at && ends_at <= starts_at)
    throw new Error('invalid interval'); return { starts_at, ends_at }; }
export function StartFields() { const text = useCostText(); return <><label className="grid gap-1">{text('生效时间', 'Starts at')}<Input name="starts_at" type="datetime-local" required defaultValue={new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 16)}/></label><Field name="ends_at" type="datetime-local" label={text('结束时间（可选）', 'Ends at (optional)')}/></>; }
