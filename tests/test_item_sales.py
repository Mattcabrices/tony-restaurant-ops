import pytest
pytest.importorskip('msal',reason='Operational Windows dependencies required.')
from datetime import date, datetime, timedelta
import json

import pytest

from app.calculations import ET
from app.item_sales import item_sales_summary, menu_names
from app.operations import Operations, TOOLS
from app.providers import ProviderError, demo_overview


def selection(name, group, quantity=1, **extra):
    return {'displayName': name, 'itemGroup': {'guid': group}, 'quantity': quantity,
            'selectionType': 'NONE', **extra}


def test_large_pizzas_are_item_quantities_not_large_named_products():
    groups = menu_names([{'guid': 'root', 'name': 'Menu', 'children': [
        {'guid': 'pizza', 'name': 'Large Pizzas'},
        {'guid': 'salad', 'name': 'Specials'},
        {'guid': 'package', 'name': 'Packages'}]}])
    orders = [
        {'guid': 'one', 'checks': [{'selections': [
            selection('Large Cheese', 'pizza', 2),
            selection('Large Ultimate Pepperoni', 'pizza', 3, refundDetails={'refundAmount': 10}),
            selection('Large Italian Chopped Salad', 'salad'),
            selection('Large Catering Package', 'package', modifiers=[selection('Cheese Pizza', 'pizza', 5)]),
            selection('Large Cheese', 'pizza', 4, voided=True)]}]},
        {'guid': 'two', 'checks': [{'voided': True, 'selections': [selection('Large Cheese', 'pizza', 7)]}]},
        {'guid': 'three', 'approvalStatus': 'FUTURE', 'checks': [{'selections': [selection('Large Cheese', 'pizza', 8)]}]},
        {'guid': 'four', 'excessFood': True, 'checks': [{'selections': [selection('Large Cheese', 'pizza', 9)]}]},
    ]
    result = item_sales_summary(orders, groups, date(2026, 10, 13), datetime(2026, 10, 14, 9, tzinfo=ET), 4, 'large pizzas')
    assert result['exact'] and result['matched_quantity'] == 5
    assert result['orders_with_matches'] == 1 and result['matching_selection_lines'] == 2
    assert [(row['name'], row['quantity']) for row in result['items']] == [
        ('Large Ultimate Pepperoni', 3), ('Large Cheese', 2)]
    assert result['period_start'] == '2026-10-13T04:00:00-04:00'
    assert result['period_end'] == '2026-10-14T04:00:00-04:00'
    assert item_sales_summary(orders, groups, date(2026, 10, 13), datetime(2026, 10, 14, 9, tzinfo=ET), 4, 'cheese pizza')['matched_quantity'] == 2


def test_unknown_menu_group_withholds_exact_count():
    orders = [{'guid': 'one', 'checks': [{'selections': [selection('Large Cheese', 'missing', 2)]}]}]
    result = item_sales_summary(orders, {}, date(2026, 10, 13), datetime(2026, 10, 14, 9, tzinfo=ET), 4, 'large pizzas')
    assert result['matched_quantity'] is None and not result['exact']
    assert result['unclassified_selection_lines'] == 1


def test_split_selection_withholds_exact_count():
    orders = [{'guid': 'one', 'checks': [{'selections': [
        selection('Large Cheese', 'pizza', 1, splitOrigin={'guid': 'original'})]}]}]
    result = item_sales_summary(orders, {'pizza': 'Large Pizzas'}, date(2026, 10, 13), datetime(2026, 10, 14, 9, tzinfo=ET), 4, 'large pizzas')
    assert result['matched_quantity'] is None and result['split_selection_lines'] == 1


def test_missing_quantity_withholds_item_report():
    orders = [{'guid': 'one', 'checks': [{'selections': [selection('Large Cheese', 'pizza', None)]}]}]
    with pytest.raises(ValueError, match='quantity'):
        item_sales_summary(orders, {'pizza': 'Large Pizzas'}, date(2026, 10, 13), datetime(2026, 10, 14, 9, tzinfo=ET), 4, 'large pizzas')


@pytest.mark.asyncio
async def test_item_tool_uses_requested_date_and_rejects_demo(db):
    class FakeToast:
        async def item_sales(self, day, query, closeout):
            return {'business_date': day.isoformat(), 'query': query, 'closeout': closeout, 'matched_quantity': 5}

    class FakeAI:
        pass

    ops = Operations(db, FakeToast(), FakeAI())
    yesterday = (datetime.now(ET).date() - timedelta(days=1)).isoformat()
    assert 'get_item_sales' in {tool['name'] for tool in TOOLS}
    with pytest.raises(ProviderError, match='demo mode'):
        await ops.tool('get_item_sales', {'date': yesterday, 'query': 'large pizzas'}, 'test')
    db.set('settings', {'demo': False})
    result = await ops.tool('get_item_sales', {'date': yesterday, 'query': 'large pizzas'}, 'test')
    assert result['business_date'] == yesterday and result['query'] == 'large pizzas'


@pytest.mark.asyncio
async def test_chat_can_answer_item_question_from_tool_data(db):
    class FakeToast:
        async def item_sales(self, day, query, closeout):
            assert query == 'large pizzas'
            return {'source': 'Toast', 'business_date': day.isoformat(), 'matched_quantity': 28,
                    'items': [{'name': 'Large Cheese', 'quantity': 11}], 'exact': True}

    class FakeAI:
        def status(self):
            return {'connected': True}

        async def response(self, inputs, tools):
            assert 'get_item_sales' in {tool['name'] for tool in tools}
            outputs = [item for item in inputs if item.get('type') == 'function_call_output' and item.get('call_id') == 'items']
            if not outputs:
                return {'output': [{'type': 'function_call', 'name': 'get_item_sales', 'call_id': 'items',
                                    'arguments': json.dumps({'date': day, 'query': 'large pizzas'})}]}
            assert json.loads(outputs[0]['output'])['matched_quantity'] == 28
            return {'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': '28 large pizzas.'}]}]}

    day = (datetime.now(ET).date() - timedelta(days=1)).isoformat()
    db.set('settings', {'demo': False})
    ops = Operations(db, FakeToast(), FakeAI())
    async def report(*args, **kwargs):
        return demo_overview()
    ops.report = report
    answer = await ops.chat('How many large pizzas did we sell yesterday?', 'test')
    assert answer['ai'] and answer['text'] == '28 large pizzas.'
