"""Import aggregate historical Clover sales and item counts; no raw orders retained."""
import argparse
import asyncio
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.clover import Clover, CloverError
from app.storage import DB, ROOT, Vault, utcnow


ET = ZoneInfo('America/New_York')


def next_month(day):
    return date(day.year + (day.month == 12), day.month % 12 + 1, 1)


def summarize(orders, start, end):
    days = {}
    current = start
    while current < end:
        days[current.isoformat()] = {'paid_orders': 0, 'gross_line_cents': 0,
                                     'custom_lines': 0, 'refunded_orders': 0}
        current += timedelta(days=1)
    items = defaultdict(lambda: {'quantity': 0, 'gross_line_cents': 0, 'name': ''})
    for order in orders:
        timestamp = order.get('clientCreatedTime')
        if not isinstance(timestamp, (int, float)):
            raise CloverError('Clover order missing client-created timestamp; period withheld.')
        day = datetime.fromtimestamp(timestamp / 1000, ET).date().isoformat()
        if day not in days:
            raise CloverError('Clover order fell outside the requested period; period withheld.')
        if order.get('testMode'):
            continue
        state = order.get('paymentState')
        if state == 'REFUNDED':
            days[day]['refunded_orders'] += 1
            continue
        if state != 'PAID':
            continue
        days[day]['paid_orders'] += 1
        line_field = order.get('lineItems')
        lines = line_field.get('elements') if isinstance(line_field, dict) else None
        if not isinstance(lines, list):
            raise CloverError('Clover line-item expansion was missing or possibly truncated; period withheld.')
        for line in lines:
            if not isinstance(line, dict):
                raise CloverError('Clover returned an invalid line item; period withheld.')
            price = line.get('price') or 0
            if not isinstance(price, int):
                raise CloverError('Clover line-item price was invalid; period withheld.')
            days[day]['gross_line_cents'] += price
            item = line.get('item') or {}
            item_id = item.get('id') if isinstance(item, dict) else None
            if not item_id:
                days[day]['custom_lines'] += 1
                continue
            if line.get('refunded') or line.get('isRevenue') is False or line.get('type') != 'REGULAR':
                continue
            row = items[(day, item_id)]
            row['quantity'] += 1
            row['gross_line_cents'] += price
            row['name'] = str(line.get('name') or 'Unnamed Clover item')[:200]
    return days, items


def save_period(db, start, end, orders, days, items):
    now = utcnow()
    with db.connect() as connection:
        connection.execute('DELETE FROM clover_item_days WHERE day>=? AND day<?', (start.isoformat(), end.isoformat()))
        connection.execute('DELETE FROM clover_days WHERE day>=? AND day<?', (start.isoformat(), end.isoformat()))
        connection.executemany('INSERT INTO clover_days VALUES (?,?,?,?,?,?)',
            [(day, row['paid_orders'], row['gross_line_cents'], row['custom_lines'], row['refunded_orders'], now)
             for day, row in days.items()])
        connection.executemany('INSERT INTO clover_item_days VALUES (?,?,?,?,?)',
            [(day, item_id, row['name'], row['quantity'], row['gross_line_cents'])
             for (day, item_id), row in items.items()])
        connection.execute('INSERT OR REPLACE INTO clover_periods VALUES (?,?,?,?)',
                           (start.isoformat(), end.isoformat(), len(orders), now))


async def import_range(start, end, db, provider):
    current = start
    while current < end:
        following = min(next_month(current), end)
        exists = db.rows('SELECT 1 FROM clover_periods WHERE start_day=? AND end_day=?',
                         (current.isoformat(), following.isoformat()))
        if exists:
            print(f'{current}–{following - timedelta(days=1)}: already imported', flush=True)
            current = following
            continue
        first = datetime.combine(current, time.min, ET)
        last = datetime.combine(following, time.min, ET)
        orders = await provider.orders_between(first, last, 'lineItems,payments,refunds')
        expanded = 0
        for order in orders:
            if order.get('paymentState') != 'PAID' or order.get('testMode'):
                continue
            field = order.get('lineItems')
            lines = field.get('elements') if isinstance(field, dict) else None
            if not isinstance(lines, list) or len(lines) >= 100:
                order['lineItems'] = {'elements': await provider.line_items(order['id'])}
                expanded += 1
        days, items = summarize(orders, current, following)
        save_period(db, current, following, orders, days, items)
        print(f'{current}–{following - timedelta(days=1)}: {len(orders)} orders, '
              f'{sum(x["paid_orders"] for x in days.values())} paid, {len(items)} item-day counts, '
              f'{expanded} separate line-item reads', flush=True)
        current = following


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--start', default='2024-08-24')
    parser.add_argument('--through', default='2026-07-19')
    args = parser.parse_args()
    start, end = date.fromisoformat(args.start), date.fromisoformat(args.through) + timedelta(days=1)
    if end <= start:
        raise SystemExit('End must be on or after start.')
    db, provider = DB(ROOT / 'data'), Clover(Vault(ROOT / 'data'))
    try:
        await import_range(start, end, db, provider)
    finally:
        await provider.http.aclose()


if __name__ == '__main__':
    asyncio.run(main())
