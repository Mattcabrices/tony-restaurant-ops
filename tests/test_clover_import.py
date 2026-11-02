import pytest
pytest.importorskip('msal',reason='Operational Windows dependencies required.')
from datetime import date, datetime

from app.clover_history import item_history, sales_history, seasonal_context
from scripts.clover_import import save_period, summarize


def test_clover_aggregate_import_and_wednesday_week(db):
    stamp = int(datetime.fromisoformat('2025-10-01T12:00:00-04:00').timestamp()*1000)
    orders = [
        {'id': 'paid', 'clientCreatedTime': stamp, 'paymentState': 'PAID',
         'lineItems': {'elements': [
             {'type': 'REGULAR', 'name': '10 Wings', 'item': {'id': 'w'}, 'price': 1400},
             {'type': 'REGULAR', 'name': 'Custom Item', 'price': 500},
             {'type': 'REGULAR', 'name': '10 Wings', 'item': {'id': 'w'}, 'price': 1400, 'refunded': True}]}},
        {'id': 'refunded', 'clientCreatedTime': stamp, 'paymentState': 'REFUNDED',
         'lineItems': {'elements': [{'type': 'REGULAR', 'name': '10 Wings', 'item': {'id': 'w'}, 'price': 1400}]}}
    ]
    first, end = date(2025, 10, 1), date(2025, 10, 2)
    days, items = summarize(orders, first, end)
    save_period(db, first, end, orders, days, items)
    sales = sales_history(db, '2025-10-01', '2025-10-01', 'week', 2)
    assert sales['complete'] and sales['periods'][0]['period'] == '2025-10-01'
    assert sales['periods'][0]['gross_line_sales'] == 33.0
    item = item_history(db, '2025-10-01', '2025-10-01', 'wings')
    assert item['matching_quantity'] == 1
    assert item['items'][0]['known_pieces'] == 10
    assert item['known_wing_or_finger_pieces'] == 10
    assert item['unlinked_custom_lines'] == 1


def test_clover_incomplete_coverage_is_flagged(db):
    assert not sales_history(db, '2025-10-01', '2025-10-02', 'day')['complete']
    assert seasonal_context(db, date(2026, 10, 1)) is None
