"""Receipt-number and UUID lookup without guest/contact/payment identity exposure."""
import asyncio
from datetime import date, datetime, timezone
from .calculations import business_day
from .order_search import project
from .providers import ProviderError


async def receipt_lookup(toast, day, reference, closeout=4, now=None):
    now = now or datetime.now(timezone.utc)
    if day > business_day(now, closeout): raise ValueError('Use booked orders for future receipts.')
    rows = []; pages = 0; complete = False
    async with asyncio.timeout(60):
        if '-' in reference:
            row = await toast.get('/orders/v2/orders/'+reference, ttl=120)
            if not isinstance(row, dict): raise ProviderError('Malformed receipt response.')
            # Never disclose another date under the requested date label.
            if str(row.get('businessDate') or '') != day.strftime('%Y%m%d'):
                return {'source':'Toast receipt lookup', 'day':day.isoformat(), 'matches':[], 'complete':False,
                        'notice':'Receipt business date differs or is unavailable; confirm the date.'}
            rows = [row]; pages = 1; complete = True
        else:
            for page in range(1, 21):
                batch = await toast.get('/orders/v2/ordersBulk', {'businessDate':day.strftime('%Y%m%d'), 'page':page, 'pageSize':100}, ttl=120)
                pages += 1
                if not isinstance(batch, list) or len(batch)>100 or any(not isinstance(r,dict) for r in batch): raise ProviderError('Malformed receipt search response.')
                rows.extend(r for r in batch if str(r.get('displayNumber') or '') == reference or any(
                    str(c.get('displayNumber') or '') == reference for c in r.get('checks',[]) if isinstance(c,dict)))
                if len(batch)<100: complete=True; break
        matches = []; seen = set(); unknown = 0
        for row in rows:
            key = row.get('guid')
            if key in seen: continue
            seen.add(key)
            try: value = project(row, day, now, 0, 10000000, 'order_total')
            except (ValueError, KeyError, TypeError): unknown+=1; continue
            if value:
                if len(matches)>=20: complete=False; break
                matches.append(value)
    return {'source':'Toast receipt lookup', 'day':day.isoformat(), 'checked_at':now.isoformat(),
            'matches':matches, 'complete':complete and not unknown, 'requests':pages,
            'unsearchable_orders':unknown, 'ambiguous':len(matches)>1,
            'notice':'Receipt numbers can repeat across orders/checks. Confirm the date and matched receipt; identities are not inferred.',
            'limitations':['Active checks only; combined totals include tax/tip and do not subtract refunds.',
                           'No guest, tab, employee, contact, card or raw-order data is returned.']}
