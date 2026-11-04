from datetime import datetime
import json
import pytest
pytest.importorskip('msal', reason='Operational Windows dependencies required.')

from app.calculations import ET
from app.order_search import search_orders
from app.operations import Operations
from app.code_proposals import allowed_path

NOW = datetime(2026, 11, 4, 15, tzinfo=ET)


def order(key, totals, **extra):
    return {'guid': key, 'displayNumber': '101', 'openedDate': '2026-11-03T18:00:00-05:00',
            'customer': {'firstName': 'PRIVATE_PERSON'}, 'notes': 'UNTRUSTED_INSTRUCTIONS',
            'checks': [{'guid': f'{key}-{i}', 'displayNumber': str(i + 1), 'amount': n - 10,
                        'totalAmount': n, 'tabName': 'PRIVATE_TAB', 'payments': [{'last4Digits': '1234'}]}
                       for i, n in enumerate(totals)], **extra}


class Toast:
    def __init__(self, rows): self.rows = rows; self.calls = []
    async def get(self, path, params=None, ttl=0):
        assert path == '/orders/v2/ordersBulk' and ttl == 120
        self.calls.append(params)
        return self.rows


@pytest.mark.asyncio
async def test_amount_search_combines_split_checks_and_excludes_private_data():
    split = order('split', [250, 350])
    split['checks'].append(dict(split['checks'][0]))
    rows = [split, split, order('low', [499.99]), order('high', [900.01]),
            order('minimum', [500]), order('maximum', [900]), order('void', [600], voided=True),
            order('future', [600], approvalStatus='FUTURE')]
    result = await search_orders(Toast(rows), '2026-11-03', '2026-11-03', 500, 900, now=NOW)
    assert [r['combined_order_total'] for r in result['matches']] == [600, 500, 900]
    assert [r['total'] for r in result['matches'][0]['checks']] == [250, 350]
    assert result['returned_matches'] == 3 and not result['partial']
    encoded = json.dumps(result)
    assert all(x not in encoded for x in ('PRIVATE_', 'UNTRUSTED_', 'last4Digits', 'guid', 'customer', 'tabName'))
    assert not allowed_path('app/order_search.py')


@pytest.mark.asyncio
async def test_check_total_and_net_sales_are_separate_search_bases():
    toast = Toast([order('split', [250, 350]), order('check', [600, 400])])
    result = await search_orders(toast, '2026-11-03', '2026-11-03', 500, 900, 'check_total', now=NOW)
    assert len(result['matches']) == 1 and result['matches'][0]['combined_order_total'] == 1000
    assert [x['matched'] for x in result['matches'][0]['checks']] == [True, False]
    refunded = order('refund', [600]); refunded['checks'][0]['payments'] = [{'refund': {'refundAmount': 100}}]
    total = await search_orders(Toast([refunded]), '2026-11-03', '2026-11-03', 500, 900, now=NOW)
    net = await search_orders(Toast([refunded]), '2026-11-03', '2026-11-03', 500, 900, 'net_sales', now=NOW)
    assert total['returned_matches'] == 1 and net['returned_matches'] == 0


@pytest.mark.asyncio
async def test_unknown_amounts_page_limits_and_result_caps_are_explicit(monkeypatch):
    missing = order('missing', [600]); missing['checks'][0].pop('totalAmount')
    r = await search_orders(Toast([missing]), '2026-11-03', '2026-11-03', 500, 900, now=NOW)
    assert r['unsearchable_orders'] == 1 and r['partial'] and r['returned_matches'] == 0
    monkeypatch.setattr('app.order_search.MAX_PAGES', 1)
    r = await search_orders(Toast([order(str(i), [600]) for i in range(100)]), '2026-11-02', '2026-11-03', 500, 900, now=NOW)
    assert r['partial'] and not r['matches'] and len(r['incomplete_dates']) == 2
    r = await search_orders(Toast([order(str(i), [600]) for i in range(60)]), '2026-11-03', '2026-11-03', 500, 900, now=NOW)
    assert r['results_truncated'] and r['partial'] and r['returned_matches'] == 50
    monkeypatch.setattr('app.order_search.MAX_OUTPUT_CHARS', 50)
    r = await search_orders(Toast([order('large', [600])]), '2026-11-03', '2026-11-03', 500, 900, now=NOW)
    assert r['results_truncated'] and not r['matches']
    for args in [('2026-11-03', '2026-11-03', 'NaN', 900), ('2026-10-01', '2026-11-03', 500, 900),
                 ('2026-11-05', '2026-11-05', 500, 900), ('2026-11-03', '2026-11-03', 900, 500)]:
        with pytest.raises(ValueError): await search_orders(None, *args, now=NOW)


@pytest.mark.asyncio
async def test_natural_amount_request_runs_read_tool_then_ai_summary(db):
    db.set('settings', {'demo': False})
    class Model:
        def __init__(self): self.calls = 0
        def status(self): return {'connected': True}
        async def response(self, inputs, tools):
            self.calls += 1
            if self.calls == 1:
                assert any(t['name'] == 'get_orders_by_amount' for t in tools)
                return {'output': [{'type': 'function_call', 'name': 'get_orders_by_amount', 'call_id': 'amounts',
                    'arguments': json.dumps({'start': '2026-11-03', 'end': '2026-11-03', 'minimum': 500, 'maximum': 900, 'basis': 'order_total'})}]}
            r = next(json.loads(x['output']) for x in inputs if x.get('call_id') == 'amounts' and x.get('type') == 'function_call_output')
            assert r['matches'][0]['combined_order_total'] == 600
            return {'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': 'Found one order totaling $600.'}]}]}
    ops = Operations(db, Toast([order('one', [600])]), Model())
    r = await ops._business_chat('Find orders between $500 and $900 yesterday', 'telegram')
    assert r['ai'] and '$600' in r['text']
