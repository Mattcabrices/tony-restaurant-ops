"""Trusted read-only broker. Only numeric, nonpersonal aggregates cross the boundary."""
import asyncio
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3

import httpx

from ..calculations import business_day, labor_summary, sales_summary
from ..item_sales import menu_names, item_sales_summary
from ..pmix import product_mix, product
from ..providers import Toast, ProviderError
from ..whole_pizzas import whole_pizza_summary
from .item_quality import save_whole_pizzas
from .store import now


class RequestBudget:
    def __init__(self, maximum=60, store=None):
        self.maximum, self.used, self.store = maximum, 0, store

    async def before_request(self, request):
        if self.store:
            with self.store.connect() as connection:
                connection.execute('BEGIN IMMEDIATE')
                key = now()[:10]
                record = connection.execute("SELECT used FROM budgets WHERE day=? AND kind='http'", (key,)).fetchone()
                used = record[0] if record else 0
                if used >= self.maximum:
                    raise ProviderError('Corner daily HTTP request budget exhausted.')
                connection.execute("INSERT INTO budgets VALUES(?,'http',1) ON CONFLICT(day,kind) DO UPDATE SET used=used+1", (key,))
        if self.used >= self.maximum:
            raise ProviderError('Corner HTTP request budget exhausted; no further requests sent.')
        self.used += 1


def import_clover(store, database):
    """Open production history using SQLite read-only mode; do not copy its DB."""
    path = Path(database).resolve()
    if not path.exists():
        return {'status': 'pending', 'reason': 'Clover history database is absent.'}
    connection = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
    connection.row_factory = sqlite3.Row
    try:
        days = connection.execute('SELECT day,paid_orders,gross_line_cents,custom_lines,imported_at FROM clover_days ORDER BY day').fetchall()
        rows = []
        for day in days:
            quality = 'complete' if day['paid_orders'] else 'unconfirmed_zero'
            rows += [dict(day=day['day'], metric='clover.gross_line_sales', value=day['gross_line_cents']/100, quality=quality),
                     dict(day=day['day'], metric='clover.paid_orders', value=day['paid_orders'], quality=quality)]
        totals = {}
        for record in connection.execute('SELECT day,name,quantity FROM clover_item_days ORDER BY day'):
            category, _ = product(record['name'])
            if category:
                key = record['day'], category
                totals[key] = totals.get(key, 0) + record['quantity']
        rows += [dict(day=day, metric='clover.item_portions', dimension=category, value=value)
                 for (day, category), value in totals.items()]
        if not rows:
            return {'status': 'pending', 'reason': 'No Clover aggregate history is available.'}
        result = store.ingest('Clover', rows, {'definition': 'Imported approximate gross line sales and inventory-linked portions; not Toast net sales.',
            'original_import_latest': max(d['imported_at'] for d in days),
            'limitations': ['No hourly, channel or labor history exists in this export.', 'Custom lines are excluded from named item counts.',
                            'Source totals are revised actuals, not original point-in-time vintages.']})
        return {'status': 'imported', **result, 'covered_days': len(days)}
    finally:
        connection.close()


async def collect_toast(store, vault, first, last, closeout=4, channel_mapping=None, budget=None):
    first, last = date.fromisoformat(first), date.fromisoformat(last)
    current = business_day(datetime.now(timezone.utc), closeout)
    if first > last or (last-first).days > 6 or last >= current:
        raise ValueError('Collect 1–7 fully closed Toast business dates; current/future days are excluded.')
    budget = budget or RequestBudget()
    async with httpx.AsyncClient(trust_env=False, timeout=20, follow_redirects=False, event_hooks={'request': [budget.before_request]}) as http:
        toast = Toast(vault, http=http)
        async def collect():
            cfg = toast.config()
            restaurant = await toast.get('/restaurants/v1/restaurants/' + cfg['restaurant_guid'], ttl=3600)
            source_closeout = restaurant.get('general', {}).get('closeoutHour')
            if source_closeout is None:
                raise ProviderError('Research collection requires the actual restaurant closeout hour.')
            cutoff = int(source_closeout)
            if last >= business_day(datetime.now(timezone.utc), cutoff):
                raise ValueError('Target day is not closed according to Toast cutoff.')
            names = menu_names(await toast.get('/menus/v2/menus', ttl=3600))
            rows, limitations, datasets = [], [], []
            for offset in range((last-first).days+1):
                day = first + timedelta(days=offset)
                day_start = len(rows)
                orders = await toast.orders(day)
                observed = datetime.now(timezone.utc)
                summary = sales_summary(orders, day, observed, cutoff)
                quality = 'complete' if summary['orders'] else 'unconfirmed_zero'
                def add(metric, value, dimension='all', quality_label=quality):
                    if value is not None:
                        rows.append(dict(day=day.isoformat(), metric=metric, dimension=dimension, value=value, quality=quality_label))
                add('toast.net_sales', summary['net_sales'])
                add('toast.orders', summary['orders'])
                for label, value in zip(summary['hour_labels'], summary['hourly']):
                    add('toast.hourly_sales', value, 'hour:' + label[:2], 'reconstructed' if summary['orders'] else quality)
                add('toast.evening_sales', sum(v for label, v in zip(summary['hour_labels'], summary['hourly']) if 17 <= int(label[:2]) <= 23),
                    quality_label='reconstructed' if summary['orders'] else quality)
                mix = product_mix(orders, names, observed)
                for category, values in mix['categories'].items():
                    add('toast.item_portions', values['portion_orders'], category)
                    add('toast.item_pieces', values['total_pieces'], category)
                pizzas = item_sales_summary(orders, names, day, observed, cutoff, 'pizza')
                add('toast.item_portions', pizzas['matched_quantity'], 'pizza')
                try:
                    whole_pizzas=whole_pizza_summary(orders,day,observed,cutoff,group_names=names)
                except (ValueError,OSError,KeyError,TypeError):
                    whole_pizzas=None
                    limitations.append(day.isoformat()+': verified whole-pizza mapping unavailable; total withheld.')
                try:
                    entries = await toast.get('/labor/v1/timeEntries', {'businessDate': day.strftime('%Y%m%d')})
                    labor = labor_summary(entries, [], observed, summary['net_sales'])
                    if labor['hours_complete'] and not labor['estimated']:
                        add('toast.labor_hours', labor['hours'])
                        add('toast.labor_cost', labor['cost'])
                    else:
                        limitations.append(day.isoformat() + ': labor incomplete/open; aggregate withheld.')
                except ProviderError:
                    limitations.append(day.isoformat() + ': labor retrieval unavailable.')
                # Dining option IDs become channels only through an approved mapping.
                if channel_mapping:
                    grouped = {}
                    for order in orders:
                        channel = channel_mapping.get((order.get('diningOption') or {}).get('guid'))
                        if channel:
                            grouped.setdefault(channel, []).append(order)
                    for channel, channel_orders in grouped.items():
                        add('toast.channel_sales', sales_summary(channel_orders, day, observed, cutoff)['net_sales'], channel)
                # Commit completed dates independently, so quota/time failures do
                # not discard previously collected dates or force duplicate reads.
                datasets.append(store.ingest('Toast', rows[day_start:], {'start': day.isoformat(), 'end': day.isoformat(), 'closeout_hour': cutoff,
                    'definition': 'Existing Tony Toast sales and product-mix calculations; active top-level items only.',
                    'limitations': limitations + ['Hourly/evening values use order-open hour and revised order totals.',
                        'Individual employee IDs, names, wages, customers, raw orders and credentials are not retained.',
                        'Labor hours forecast recorded hours, not a staffing recommendation.',
                        'Unmapped dining options are excluded from channel series; channels may not reconcile to total sales.']}))
                if whole_pizzas is not None:save_whole_pizzas(store,whole_pizzas)
            return {'datasets': datasets, 'rows': len(rows)}
        result = await asyncio.wait_for(collect(), timeout=120)
        return {**result, 'http_requests': budget.used}


def backfill_window(store, first, last):
    first, last = date.fromisoformat(first), date.fromisoformat(last)
    if first > last or (last-first).days > 120:
        raise ValueError('Research backfill scope is limited to 121 explicitly requested dates.')
    covered = {row['day'] for row in store.rows('''SELECT DISTINCT o.day FROM observations o JOIN datasets d ON d.id=o.dataset_id
        WHERE o.metric='toast.net_sales' AND json_extract(d.metadata,'$.closeout_hour') IS NOT NULL''')}
    missing = [first+timedelta(days=n) for n in range((last-first).days+1) if (first+timedelta(days=n)).isoformat() not in covered]
    if not missing:
        return None
    end = missing[-1]
    start = end
    while (end-start).days < 6 and start-timedelta(days=1) in missing:
        start -= timedelta(days=1)
    return start.isoformat(), end.isoformat()


async def backfill_toast(store, vault, first, last, closeout=4, channel_mapping=None, budget=None):
    window = backfill_window(store, first, last)
    if not window:
        return {'status': 'complete', 'scope': [first, last]}
    try:
        result = await collect_toast(store, vault, *window, closeout, channel_mapping, budget)
        return {'status': 'chunk_collected', 'window': window, 'result': result}
    except ProviderError as error:
        if 'budget' not in str(error):
            raise
        return {'status': 'paused_at_http_budget', 'next_window': backfill_window(store, first, last)}


def import_existing_events(store, production_db, football_file):
    from .connectors import save_events
    result = {}
    path = Path(production_db)
    if path.exists():
        with sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True) as connection:
            row = connection.execute("SELECT value FROM meta WHERE key='sheet_calendar_snapshot'").fetchone()
            if row:
                snapshot = json.loads(row[0])
                # Keep attribution to the existing shared workbook, never copy its raw cells or notes.
                events = [{'day': e['date'], 'label': e.get('text', e.get('title', 'Staff calendar event')),
                           'category': 'business'} for e in snapshot.get('events', [])]
                result['business'] = save_events(store, 'TonyRestaurant calendar snapshot', 'https://docs.google.com/spreadsheets/d/' + snapshot['source_id'] + '/edit',
                                                events, snapshot['fetched_at'])
    path = Path(football_file)
    if path.exists():
        archive = json.loads(path.read_text(encoding='utf-8'))
        count = 0
        for day, values in archive['days'].items():
            for category in ('uga_games', 'prominent_college_games', 'bills_games', 'national_nfl_games'):
                for game in values.get(category, []):
                    count += save_events(store, 'Football schedule snapshot', game.get('source') or archive['sources']['nfl'],
                        [{'day': day, 'label': game['label'], 'category': 'sports'}], archive['generated_at'])
        result['football'] = count
    return result


def import_toast_snapshot(store, database):
    path = Path(database)
    if not path.exists():
        return {'status': 'pending', 'reason': 'No existing Toast snapshot database.'}
    with sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True) as connection:
        record = connection.execute("SELECT value FROM meta WHERE key='briefing_baseline:live'").fetchone()
    if not record:
        return {'status': 'pending', 'reason': 'No saved live Toast baseline.'}
    report = json.loads(record[0])
    cutoff = datetime.fromisoformat(report['period_start']).hour
    current = business_day(datetime.now(timezone.utc), cutoff).isoformat()
    rows = []
    for row in report.get('daily', []):
        if row['date'] >= current:
            continue
        quality = 'complete' if row['orders'] else 'unconfirmed_zero'
        rows.extend([dict(day=row['date'], metric='toast.net_sales', value=row['net_sales'], quality=quality),
                     dict(day=row['date'], metric='toast.orders', value=row['orders'], quality=quality)])
        for label, value in zip(row['hour_labels'], row['hourly']):
            rows.append(dict(day=row['date'], metric='toast.hourly_sales', dimension='hour:'+label[:2], value=value,
                             quality='reconstructed' if row['orders'] else quality))
        rows.append(dict(day=row['date'], metric='toast.evening_sales', value=sum(value for label,value in zip(row['hour_labels'],row['hourly']) if 17<=int(label[:2])<=23),
                         quality='reconstructed' if row['orders'] else quality))
    return store.ingest('Toast', rows, {'source': 'Existing saved live briefing baseline; numeric closed-day rows only.',
        'limitations': ['Hourly/evening metrics reconstruct order-open hours using revised totals.', 'No employee/customer data was copied.']}, report['fresh_at']) if rows else {'status': 'pending'}
