from datetime import datetime, timezone
from types import SimpleNamespace

from novelvideo.costs.models import CostAttempt
from novelvideo.costs.presets import install_presets, recommended_rules
from novelvideo.costs.pricing import quote
from novelvideo.costs.store import CostStore


def accounts():
    return [SimpleNamespace(id='images', provider_type='grsai', enabled=True),
            SimpleNamespace(id='video', provider_type='runninghub', enabled=True)]


def test_presets_are_idempotent_and_do_not_guess_credit_conversion(tmp_path):
    store = CostStore(tmp_path / 'costs.db')
    assert install_presets(store, accounts())['installed_count'] == 5
    assert install_presets(store, accounts())['installed_count'] == 0
    attempt = CostAttempt(attempt_id='a', project_id='p', account_id='images', provider='grsai',
                          model='gpt-image-2', media_type='image', occurred_at=datetime.now(timezone.utc), usage={'call': '1'})
    assert quote(attempt, store.list_price_rules()).cost.amount_micros == 30000
    rh = attempt.model_copy(update=dict(account_id='video', provider='runninghub', model='h3', media_type='video', usage={'credit': '17.4'}))
    assert quote(rh, store.list_price_rules()).cost.status == 'unpriced'


def test_presets_preserve_manual_pricing_and_disabled_accounts(tmp_path):
    store = CostStore(tmp_path / 'costs.db')
    custom = recommended_rules(accounts())[0].model_copy(update={'id': 'my-price'})
    store.add_price_rule(custom)
    result = install_presets(store, accounts())
    assert custom in store.list_price_rules()
    assert len(result['skipped']) == 1
    assert not recommended_rules([SimpleNamespace(id='off', provider_type='grsai', enabled=False)])
