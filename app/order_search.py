"""Bounded amount-based order search; only nonpersonal receipt projections leave here."""
import asyncio
import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import re

from .calculations import business_day, check_metrics, money, parsed
from .providers import ProviderError

MAX_PAGES = 60
MAX_RESULTS = 50
MAX_OUTPUT_CHARS = 20000
BASES = {'order_total', 'check_total', 'net_sales'}


def amount(value):
    if isinstance(value, bool): raise ValueError('Invalid amount.')
    try: result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError): raise ValueError('Invalid amount.') from None
    if not result.is_finite() or not 0 <= result <= 10_000_000:
        raise ValueError('Amount must be finite and between 0 and 10,000,000.')
    return result


def number(value):
    text = str(value or '')
    return text if re.fullmatch(r'[0-9]{1,12}', text) else None


def project(order, day, now, minimum, maximum, basis):
    if order.get('deleted') or order.get('voided') or order.get('excessFood'): return None
    promised = parsed(order.get('promisedDate') or order.get('estimatedFulfillmentDate'))
    if order.get('approvalStatus') == 'FUTURE' or (promised and promised > now): return None
    checks = []; seen = set()
    if len(order.get('checks') or []) > 100: raise ValueError('Check count exceeds receipt projection limit.')
    for check in order.get('checks') or []:
        if not isinstance(check, dict): raise ValueError('Malformed check.')
        if check.get('deleted') or check.get('voided'): continue
        key = check.get('guid')
        if key and key in seen: continue
        if key: seen.add(key)
        total = amount(check['totalAmount']) if check.get('totalAmount') is not None else None
        net = check_metrics(check)[0] if basis == 'net_sales' else None
        checks.append({'check_number': number(check.get('displayNumber')),
                       'total': money(total) if total is not None else None,
                       'matched': bool(total is not None and minimum <= total <= maximum), '_net': net,
                       '_total': total})
    if not checks: return None
    complete_total = all(c['_total'] is not None for c in checks)
    combined = sum((c['_total'] for c in checks), Decimal(0)) if complete_total else None
    net_total = sum((c['_net'] for c in checks), Decimal(0)) if basis == 'net_sales' else None
    if basis == 'check_total':
        matched = any(c['matched'] for c in checks)
        if not matched and not complete_total: raise ValueError('Missing check total.')
    else:
        value = net_total if basis == 'net_sales' else combined
        if value is None: raise ValueError('Missing order total.')
        matched = minimum <= value <= maximum
    if not matched: return None
    return {'business_date': day.isoformat(), 'order_number': number(order.get('displayNumber')),
            'combined_order_total': money(combined) if combined is not None else None,
            'net_sales': money(net_total) if net_total is not None else None,
            'checks': [{k: v for k, v in c.items() if not k.startswith('_')} for c in checks]}


async def search_orders(toast, start, end, minimum, maximum, basis='order_total', closeout=4, now=None):
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    now = now or datetime.now(timezone.utc)
    minimum, maximum = amount(minimum), amount(maximum)
    if basis not in BASES or minimum > maximum: raise ValueError('Choose a valid amount basis and inclusive minimum/maximum.')
    if first > last or (last - first).days > 30 or last > business_day(now, closeout):
        raise ValueError('Choose 1–31 current or completed business dates, not future bookings.')
    result = {'source': 'Toast orders, nonpersonal receipt projection', 'start': start, 'end': end,
              'minimum': money(minimum), 'maximum': money(maximum), 'amount_basis': basis,
              'range_inclusive': True, 'checked_at': now.isoformat(), 'cache_max_age_seconds': 120,
              'complete_dates': [], 'incomplete_dates': [], 'requests': 0, 'matches': [],
              'partial': False, 'results_truncated': False, 'unsearchable_orders': 0,
              'notice': '', 'limitations': [
                  'Order/check total includes tax and tip and is not reduced by refunds (Toast totalAmount).',
                  'Net-sales searches use existing net sales/refund definitions, excluding tax and tips.',
                  'Split checks have individual totals plus the combined order total.',
                  'No guest names, contacts, tab names, card details, notes or raw orders are returned.']}
    seen = set(); day = last; result_chars = 0
    try:
        async with asyncio.timeout(90):
            while day >= first:
                day_matches = []; day_chars = 0; page = 1; completed = False
                while result['requests'] < MAX_PAGES:
                    result['requests'] += 1
                    rows = await toast.get('/orders/v2/ordersBulk', {'businessDate': day.strftime('%Y%m%d'),
                        'page': page, 'pageSize': 100}, ttl=120)
                    if not isinstance(rows, list) or len(rows) > 100 or any(not isinstance(o, dict) for o in rows):
                        raise ProviderError('Malformed Toast order response.')
                    for order in rows:
                        key = order.get('guid')
                        if key and key in seen: continue
                        if key: seen.add(key)
                        try: row = project(order, day, now, minimum, maximum, basis)
                        except (ValueError, TypeError, InvalidOperation, KeyError):
                            result['unsearchable_orders'] += 1; continue
                        if row:
                            if row['combined_order_total'] is None: result['unsearchable_orders'] += 1
                            size = len(json.dumps(row))
                            if len(result['matches']) + len(day_matches) < MAX_RESULTS and result_chars + day_chars + size <= MAX_OUTPUT_CHARS:
                                day_matches.append(row); day_chars += size
                            else: result['results_truncated'] = True
                    if len(rows) < 100: completed = True; break
                    page += 1
                if not completed:
                    result['notice'] = '60-page search budget reached; incomplete dates withheld. Narrow the range.'
                    break
                result['matches'].extend(day_matches)
                result_chars += day_chars
                result['complete_dates'].append(day.isoformat())
                day -= timedelta(days=1)
    except (ProviderError, TimeoutError):
        result['notice'] = 'Toast read failed or timed out; incomplete dates withheld. Retry or narrow the range.'
    result['incomplete_dates'] = [(first + timedelta(days=n)).isoformat() for n in range((last-first).days+1)
                                if (first + timedelta(days=n)).isoformat() not in result['complete_dates']]
    result['partial'] = bool(result['incomplete_dates'] or result['unsearchable_orders'] or result['results_truncated'])
    result['returned_matches'] = len(result['matches'])
    return result
