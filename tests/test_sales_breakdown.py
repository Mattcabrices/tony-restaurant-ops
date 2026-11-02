from copy import deepcopy
from datetime import date, datetime
import json
import pytest

from app.calculations import ET
from app.providers import ProviderError
from app.sales_breakdown import sales_breakdown, summarize
from app.operations import Operations

DAY = date(2026, 10, 28)
NOW = datetime(2026, 11, 2, 17, tzinfo=ET)
DINE = '11111111-1111-1111-1111-111111111111'
TAKE = '22222222-2222-2222-2222-222222222222'
OPTIONS = {DINE: {'guid': DINE, 'behavior': 'DINE_IN'}, TAKE: {'guid': TAKE, 'behavior': 'TAKE_OUT'}}


def order(key, hour, amount, option=TAKE, source='In Store', **extra):
    return {'guid': key, 'openedDate': f'2026-10-28T{hour}:00-04:00',
            'diningOption': {'guid': option}, 'source': source,
            'checks': [{'amount': amount, 'customer': {'firstName': 'PRIVATE_GUEST'}}], **extra}


def test_channels_are_disjoint_and_match_requested_evening_window():
    rows = [order('dine', '16:00', 100, DINE), order('take', '17:00', 30),
            order('online', '20:59', 45, source='Online'), order('late', '21:00', 999),
            order('early', '15:59', 999), order('void', '17:00', 999, voided=True),
            order('future', '17:00', 999, approvalStatus='FUTURE')]
    rows[0]['checks'].append({'amount': 25})
    rows[1]['checks'][0]['payments'] = [{'refund': {'refundAmount': 5}}]
    rows.append(deepcopy(rows[0]))
    result = summarize(rows, OPTIONS, DAY, 16, 21, NOW, 4)
    channels = {r['key']: r for r in result['channels']}
    assert channels['dine_in']['net_sales'] == 125
    assert channels['regular_takeout']['net_sales'] == 25
    assert channels['online_takeout']['net_sales'] == 45
    assert result['all_channels']['net_sales'] == 195 and result['complete']
    assert result['all_channels']['orders'] == 3
    assert 'PRIVATE_GUEST' not in json.dumps(result) and 'guid' not in json.dumps(result)


def test_unknown_source_and_missing_option_are_not_silently_regular_takeout():
    result = summarize([order('api', '17:00', 50, source='API')], OPTIONS, DAY, 16, 21, NOW, 4)
    channels = {r['key']: r for r in result['channels']}
    assert channels['other_takeout']['net_sales'] == 50
    assert channels['online_takeout']['net_sales'] is None and not result['complete']
    named = deepcopy(OPTIONS)
    named[TAKE]['name'] = 'Online Ordering - Takeout'
    result = summarize([order('api-online', '17:00', 50, source='API')], named, DAY, 16, 21, NOW, 4)
    channels = {r['key']: r for r in result['channels']}
    assert channels['online_takeout']['net_sales'] == 50 and result['complete']
    assert channels['regular_takeout']['net_sales'] == 0
    result = summarize([order('unknown', '17:00', 50)], {}, DAY, 16, 21, NOW, 4)
    assert result['channels'][0]['net_sales'] is None
    assert result['all_channels']['net_sales'] == 50
    row = order('missing-time', '17:00', 50); row.pop('openedDate')
    result = summarize([row], OPTIONS, DAY, 16, 21, NOW, 4)
    assert result['all_channels']['net_sales'] is None and result['missing_open_time_orders'] == 1


@pytest.mark.asyncio
async def test_bounded_read_resolves_config_and_never_returns_raw_orders():
    class Toast:
        async def get(self, path, params=None, ttl=0):
            if path == '/orders/v2/ordersBulk': return [order('one', '17:00', 20)]
            assert path == '/config/v2/diningOptions/' + TAKE
            return OPTIONS[TAKE]
    result = await sales_breakdown(Toast(), DAY.isoformat(), 16, 21, now=NOW)
    assert result['complete'] and result['all_channels']['net_sales'] == 20
    with pytest.raises(ValueError): await sales_breakdown(None, DAY.isoformat(), 21, 16, now=NOW)
    class Endless:
        async def get(self, *args, **kwargs): return [order(str(i), '17:00', 1) for i in range(100)]
    with pytest.raises(ProviderError, match='page limit'): await sales_breakdown(Endless(), DAY.isoformat(), 16, 21, now=NOW)


@pytest.mark.asyncio
async def test_ai_can_route_unmodified_natural_request_to_time_channel_tool(db):
    db.set('settings', {'demo': False})
    class Model:
        def __init__(self): self.calls = 0
        def status(self): return {'connected': True}
        async def response(self, inputs, tools):
            self.calls += 1
            if self.calls == 1:
                assert any(t['name'] == 'get_sales_breakdown' for t in tools)
                return {'output': [{'type': 'function_call', 'name': 'get_sales_breakdown', 'call_id': 'sales',
                    'arguments': json.dumps({'date': DAY.isoformat(), 'start_hour': 16, 'end_hour': 21})}]}
            result = next(json.loads(x['output']) for x in inputs if x.get('type') == 'function_call_output' and x.get('call_id') == 'sales')
            assert result['all_channels']['net_sales'] == 20
            return {'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': 'Your requested channels, 4–9 PM.'}]}]}
    class Toast:
        async def get(self, path, params=None, ttl=0):
            return [order('one', '17:00', 20)] if path == '/orders/v2/ordersBulk' else OPTIONS[TAKE]
    ops = Operations(db, Toast(), Model())
    result = await ops._business_chat('What was total sales in takeout, dine-in and online ordering takeout on Wednesday from 4 to 9?', 'telegram')
    assert result['ai'] and '4–9 PM' in result['text']
