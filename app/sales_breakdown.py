"""Read-only Toast net sales by verified dining behavior and ordering source."""
import asyncio
import re
from datetime import date, datetime, timezone

from .calculations import ET, bounds, business_day, parsed, sales_summary
from .providers import ProviderError
from .storage import utcnow

ONLINE = {'Online', 'Google', 'Toast Pickup App', 'Toast Local', 'Branded Online Ordering',
          'Branded Mobile App', 'eCommerce Online ordering', 'Catering Online Ordering'}
IN_STORE = {'In Store', 'Caller Id'}
ONLINE_TAKEOUT_LABELS = {'online ordering takeout', 'online ordering take out',
                         'online takeout', 'online take out', 'online ordering pickup'}
UUID = re.compile(r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}')
LABELS = {'dine_in': 'Dine-in', 'regular_takeout': 'Regular takeout',
          'online_takeout': 'Online ordering takeout', 'other_takeout': 'Other/unknown-source takeout',
          'delivery': 'Delivery', 'unknown': 'Unclassified dining option'}


def summarize(orders, options, day, start_hour, end_hour, now, closeout):
    if type(start_hour) is not int or type(end_hour) is not int or not 0 <= start_hour < end_hour <= 24:
        raise ValueError('Use integer local hours from 0 to 24, with start before end; 4–9 PM is 16–21.')
    low, high = bounds(day, closeout)
    groups = {key: [] for key in LABELS}
    detail = {}; seen = set(); missing_time = 0
    for order in orders:
        key = order.get('guid')
        if key and key in seen: continue
        if key: seen.add(key)
        if order.get('voided') or order.get('deleted') or order.get('excessFood'): continue
        if not any(not c.get('voided') and not c.get('deleted') for c in order.get('checks', [])): continue
        promised = parsed(order.get('promisedDate') or order.get('estimatedFulfillmentDate'))
        if order.get('approvalStatus') == 'FUTURE' or (promised and promised > now): continue
        opened = parsed(order.get('openedDate'))
        if opened is None:
            missing_time += 1; continue
        local = opened.astimezone(ET)
        hour = local.hour + local.minute / 60 + local.second / 3600
        if not low <= local < high or not start_hour <= hour < end_hour: continue
        option = options.get((order.get('diningOption') or {}).get('guid'), {})
        behavior = option.get('behavior'); source = order.get('source')
        option_label = ' '.join(re.sub(r'[^a-z0-9]+', ' ', str(option.get('name', '')).casefold()).split())
        category = 'dine_in' if behavior == 'DINE_IN' else 'delivery' if behavior == 'DELIVERY' else 'unknown'
        if behavior == 'TAKE_OUT':
            category = ('online_takeout' if source in ONLINE or option_label in ONLINE_TAKEOUT_LABELS
                        else 'regular_takeout' if source in IN_STORE else 'other_takeout')
        groups[category].append(order)
        # Emit only aggregate category/source labels, never order or customer fields.
        detail.setdefault((category, source if source in ONLINE | IN_STORE | {'API', 'Kiosk', 'Order-and-Pay-at-Table'} else 'Other/unknown'), []).append(order)
    def aggregate(values):
        row = sales_summary(values, day, now, closeout)
        return {k: row[k] for k in ('net_sales', 'orders', 'checks', 'refunds', 'discounts')}
    rows = [{'key': key, 'label': LABELS[key], **aggregate(values)} for key, values in groups.items()]
    selected = [o for values in groups.values() for o in values]
    for row in rows:
        uncertain = missing_time or (groups['unknown'] and row['key'] != 'unknown') or (
            groups['other_takeout'] and row['key'] in ('regular_takeout', 'online_takeout'))
        row['exact'] = not bool(uncertain)
        row['observed_net_sales'] = row['net_sales']
        if uncertain: row['net_sales'] = None
    total = aggregate(selected)
    total['observed_net_sales'] = total['net_sales']
    if missing_time: total['net_sales'] = None
    return {'source': 'Toast orders + verified dining-option behaviors + order source',
            'business_date': day.isoformat(), 'timezone': 'America/New_York',
            'start_hour': start_hour, 'end_hour': end_hour, 'end_exclusive': True,
            'time_basis': 'Order opened time; revised current net check amounts',
            'fresh_at': utcnow(), 'cache_max_age_seconds': 120,
            'channels': rows, 'all_channels': total,
            'source_breakdown': [{'channel': c, 'order_source': s, **aggregate(v)} for (c, s), v in detail.items()],
            'missing_open_time_orders': missing_time,
            'complete': not missing_time and not groups['unknown'] and not groups['other_takeout'],
            'limitations': ['Net sales use existing discount/refund definitions; exclude tax and tips.',
                'Time window uses opened time, not payment time. End hour is exclusive.',
                'Online takeout and regular takeout are disjoint; API/unknown takeout is separate.',
                'An explicitly configured Online Ordering Takeout dining option identifies online pickup even when Toast source is API.',
                'Unknown dining options or timestamps are gaps, never evidence of zero sales.']}


async def sales_breakdown(toast, day_text, start_hour=0, end_hour=24, closeout=4, now=None):
    day = date.fromisoformat(day_text); now = now or datetime.now(timezone.utc)
    if day > business_day(now, closeout): raise ValueError('Actual sales cannot include a future business date.')
    # Validate before any vendor request.
    summarize([], {}, day, start_hour, end_hour, now, closeout)
    async with asyncio.timeout(60):
        orders = []; complete = False
        for page in range(1, 21):
            batch = await toast.get('/orders/v2/ordersBulk', {'businessDate': day.strftime('%Y%m%d'),
                'page': page, 'pageSize': 100}, ttl=120)
            if not isinstance(batch, list) or len(batch) > 100 or any(not isinstance(o, dict) for o in batch):
                raise ProviderError('Malformed Toast order response; sales breakdown withheld.')
            orders.extend(batch)
            if len(batch) < 100: complete = True; break
        if not complete: raise ProviderError('Toast page limit reached; incomplete sales breakdown withheld.')
        ids = {(o.get('diningOption') or {}).get('guid') for o in orders}
        ids.discard(None)
        if len(ids) > 32: raise ProviderError('Dining-option read budget reached; breakdown withheld.')
        options = {}; unavailable = 0
        for key in sorted(ids):
            if not isinstance(key, str) or not UUID.fullmatch(key):
                unavailable += 1; continue
            try:
                option = await toast.get('/config/v2/diningOptions/' + key, ttl=3600)
                if not isinstance(option, dict) or option.get('guid') != key: raise ValueError('Invalid option')
                options[key] = option
            except (ProviderError, ValueError): unavailable += 1
        result = summarize(orders, options, day, start_hour, end_hour, now, closeout)
        result['unavailable_dining_options'] = unavailable
        return result
