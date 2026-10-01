"""Reviewed platform defaults; never fabricate exchange rates or historical usage."""
from datetime import datetime

from .models import PriceRule
from .pricing import validate_rules

SOURCE = 'https://grsai.com/zh/dashboard/models'
GRSAI_PRICES = {
    'gpt-image-2': ('0.03', '2026-04-22T00:00:00+08:00'),
    'gpt-image-2-vip': ('0.10', '2026-08-12T09:00:00+08:00'),
}


def recommended_rules(accounts):
    rules = []
    for account in accounts:
        if not account.enabled:
            continue
        if account.provider_type == 'grsai':
            for model, (price, start) in GRSAI_PRICES.items():
                rules.append(PriceRule(
                    id=f'preset:grsai:{account.id}:{model}', version='2026-09-24',
                    provider='grsai', account_id=account.id, model=model,
                    media_type='image', starts_at=datetime.fromisoformat(start),
                    items=({'unit': 'call', 'unit_price': price},),
                ))
        elif account.provider_type == 'runninghub':
            for media in ('image', 'audio', 'video'):
                rules.append(PriceRule(
                    id=f'preset:runninghub:{account.id}:{media}', version='2026-09-24',
                    provider='runninghub', account_id=account.id, media_type=media,
                    starts_at=datetime.fromisoformat('2026-09-24T00:00:00+08:00'),
                    currency='RH_CREDIT', items=({'unit': 'credit', 'unit_price': '1', 'step': '0.000001'},),
                ))
    return rules


def preset_status(store, accounts):
    recommendations = recommended_rules(accounts)
    existing = {(r.id, r.version) for r in store.list_price_rules()}
    providers = {a.provider_type for a in accounts if a.enabled}
    platforms = []
    if 'grsai' in providers:
        platforms.append(dict(provider='grsai', title='Grsai 图片', description='按官方公开单价估算；实际账单优先。已预填普通版和 VIP，无需填写规则字段。'))
    if 'runninghub' in providers:
        platforms.append(dict(provider='runninghub', title='RunningHub 视频 / 配音 / 放大', description='按套餐独立计费，自动记录实际扣除的 RH 币，不折算人民币，也不合并到人民币费用。'))
    platforms.append(dict(provider='codex', title='Codex 文本', description='订阅费用单独登记，不把订阅内调用虚构成逐次 API 消费。'))
    return dict(platforms=platforms, installed=bool(recommendations) and all((r.id, r.version) in existing for r in recommendations),
                rules=[dict(id=r.id, model=r.model or f'RunningHub {r.media_type}', price=str(r.items[0].unit_price),
                            unit=r.items[0].unit, currency=r.currency,
                            source_url=SOURCE if r.provider == 'grsai' else 'https://www.runninghub.cn/call-api/bill-task?tab=keys&type=exclusive')
                       for r in recommendations])


def install_presets(store, accounts):
    """Atomic and idempotent. Keep user rules; skip a conflicting recommendation."""
    installed, skipped = [], []
    with store.transaction():
        existing = store.list_price_rules()
        for rule in recommended_rules(accounts):
            if any((r.id, r.version) == (rule.id, rule.version) for r in existing):
                continue
            # A scoped custom rule must not be silently overridden by a preset.
            if any(r.provider in (None, rule.provider) and r.account_id in (None, rule.account_id)
                   and r.model in (None, rule.model) and r.media_type == rule.media_type
                   and not r.id.startswith('preset:') and (r.ends_at is None or r.ends_at > rule.starts_at)
                   for r in existing):
                skipped.append(rule.id)
                continue
            validate_rules([*existing, rule])
            store.add_price_rule(rule)
            existing.append(rule)
            installed.append(rule.id)
    return dict(**preset_status(store, accounts), installed_count=len(installed), skipped=skipped)
