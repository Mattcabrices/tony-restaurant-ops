import pytest
pytest.importorskip('msal',reason='Operational Windows dependencies required.')
from datetime import date, datetime, timedelta

import pytest

from app.calculations import ET
from app.modifier_sales import modifier_sales_summary
from app.operations import Operations, TOOLS
from app.providers import ProviderError


def test_modifier_quantities_are_separate_and_not_multiplied():
    day = date(2026, 10, 16)
    orders = [{'guid': 'o1', 'checks': [{'selections': [
        {'guid': 'p1', 'displayName': 'Large Cheese', 'quantity': 2, 'modifiers': [
            {'displayName': 'Extra Cheese', 'quantity': 2, 'optionGroup': {'guid': 'toppings'},
             'modifiers': [{'displayName': 'Whole', 'quantity': 2}]},
            {'displayName': 'Extra Cheese', 'quantity': 2, 'optionGroup': {'guid': 'toppings'}},
            {'displayName': 'Extra Bleu Cheese', 'quantity': 2, 'optionGroup': {'guid': 'toppings'}},
            {'displayName': 'No Onion', 'quantity': 2, 'voided': True}]},
        {'guid': 'p2', 'displayName': 'Large Meatza', 'quantity': 1, 'voided': True,
         'modifiers': [{'displayName': 'Extra Cheese', 'quantity': 1}]},
    ]}]}]
    result = modifier_sales_summary([(day, orders)], {'toppings': 'Pizza Toppings'},
                                    datetime(2026, 10, 17, 10, tzinfo=ET), 'extra cheese')
    assert result['matched_selections'] == 4
    assert result['orders_with_matches'] == 1
    assert result['modifiers'] == [{'modifier': 'Extra Cheese', 'option_group': 'Pizza Toppings',
                                     'parent_item': 'Large Cheese', 'depth': 1, 'selections': 4}]
    nested = modifier_sales_summary([(day, orders)], {}, datetime(2026, 10, 17, 10, tzinfo=ET), 'whole')
    assert nested['matched_selections'] == 2 and nested['modifiers'][0]['depth'] == 2


@pytest.mark.asyncio
async def test_modifier_tool_uses_configured_wednesday_week(db):
    class FakeToast:
        async def modifier_sales(self, start, end, query, parent_query, closeout):
            return {'start': start.isoformat(), 'end': end.isoformat(), 'query': query,
                    'parent_query': parent_query, 'closeout': closeout}

    ops = Operations(db, FakeToast(), object())
    assert 'get_modifier_sales' in {t['name'] for t in TOOLS}
    with pytest.raises(ProviderError, match='demo mode'):
        await ops.modifier_sales('today', 'cheese')
    db.set('settings', {'demo': False, 'workweek_start': 2})
    result = await ops.modifier_sales('last_week', 'cheese', 'pizza')
    start = date.fromisoformat(result['start'])
    assert start.weekday() == 2
    assert date.fromisoformat(result['end']) == start + timedelta(days=6)
    assert result['query'] == 'cheese' and result['parent_query'] == 'pizza'
