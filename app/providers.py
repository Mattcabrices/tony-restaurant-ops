from __future__ import annotations

import asyncio
import hashlib
import hmac
import re
import time
from datetime import date, datetime, timedelta, timezone
from typing import Protocol
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel

from .calculations import ET, bounds, business_day, change, labor_summary, money, reporting_week_start, sales_summary, parsed
from .item_sales import item_sales_summary, menu_names
from .whole_pizzas import whole_pizza_summary,whole_pizza_query,load_mapping
from .pizza_splits import reconcile_pizza_splits
from .modifier_sales import modifier_sales_summary
from .pmix import product_mix
from .forecast import estimate_sales, game_kind
from .storage import utcnow


class ProviderError(Exception):
    pass


def week_window(week_of, start_weekday, now, closeout):
    current_day = business_day(now, closeout)
    if week_of == 'this':
        anchor = current_day
    elif week_of == 'last':
        anchor = current_day - timedelta(days=7)
    else:
        anchor = date.fromisoformat(week_of)
    start = reporting_week_start(anchor, start_weekday)
    end = start + timedelta(days=6)
    if start > current_day:
        raise ProviderError('Future sales weeks are unavailable.')
    return start, end, min(end, current_day), end < current_day


class Toast:
    def __init__(self, vault, http=None, interval=0.6):
        self.vault = vault
        self.http = http or httpx.AsyncClient(trust_env=False, timeout=30, follow_redirects=False)
        self.token = None
        self.expiry = 0
        self.lock = asyncio.Lock()
        self.last = 0
        self.interval = interval
        self.cache = {}
        self.health = {}

    def config(self):
        config = self.vault.read().get('toast', {})
        if not all(config.get(k) for k in ['client_id', 'client_secret', 'restaurant_guid']):
            raise ProviderError('Toast is not configured. Run the local configuration wizard.')
        host = config.get('host', 'https://ws-api.toasttab.com').rstrip('/')
        url = urlsplit(host)
        if url.scheme != 'https' or not url.hostname or not url.hostname.endswith('.toasttab.com') or url.username or url.path:
            raise ProviderError('Toast host must be an HTTPS toasttab.com API hostname.')
        config['host'] = host
        return config

    async def get(self, path, params=None, ttl=0):
        single_order=bool(re.fullmatch(r'/orders/v2/orders/[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}',path))
        if not single_order and not path.startswith(('/orders/v2/ordersBulk', '/labor/v1/', '/stock/v1/inventory', '/restaurants/v1/restaurants/', '/menus/v2/menus')):
            raise ProviderError('Endpoint is outside the read-only allowlist.')
        key = path + str(sorted((params or {}).items()))
        if key in self.cache and time.time() - self.cache[key][0] < ttl:
            return self.cache[key][1]
        cfg = self.config()
        async with self.lock:
            for attempt in range(4):
                await asyncio.sleep(max(0, self.interval - (time.monotonic() - self.last)))
                self.last = time.monotonic()
                try:
                    if not self.token or self.expiry < time.time() + 60:
                        response = await self.http.post(cfg['host'] + '/authentication/v1/authentication/login', json={
                            'clientId': cfg['client_id'], 'clientSecret': cfg['client_secret'], 'userAccessType': 'TOAST_MACHINE_CLIENT'})
                        if response.status_code != 200:
                            raise ProviderError(f'Toast authentication unavailable (HTTP {response.status_code}).')
                        token = response.json().get('token', {})
                        self.token = token.get('accessToken')
                        self.expiry = time.time() + int(token.get('expiresIn', 3600))
                        if not self.token:
                            raise ProviderError('Toast returned no access token.')
                    r = await self.http.get(cfg['host'] + path, params=params, headers={
                        'Authorization': 'Bearer ' + self.token, 'Toast-Restaurant-External-ID': cfg['restaurant_guid']})
                    if r.status_code == 401 and attempt == 0:
                        self.token = None
                        continue
                    if r.status_code == 429 or r.status_code >= 500:
                        if attempt < 3:
                            try:
                                delay = min(30, max(1, float(r.headers.get('Retry-After', 2 ** attempt))))
                            except ValueError:
                                delay = 2 ** attempt
                            await asyncio.sleep(delay)
                            continue
                    if r.status_code != 200:
                        raise ProviderError(f'Toast endpoint unavailable (HTTP {r.status_code}); check granted permissions.')
                    data = r.json()
                    self.cache[key] = (time.time(), data)
                    self.health[path] = {'status': 'Connected', 'checked_at': utcnow()}
                    return data
                except httpx.HTTPError:
                    if attempt < 3:
                        await asyncio.sleep(2 ** attempt)
                        continue
                    raise ProviderError('Toast network request failed.') from None
                except ValueError:
                    raise ProviderError('Toast returned an invalid response.') from None
        raise ProviderError('Toast retries exhausted.')

    async def orders(self, day):
        result = []
        seen = set()
        for page in range(1, 501):
            batch = await self.get('/orders/v2/ordersBulk', {'businessDate': day.strftime('%Y%m%d'), 'page': page, 'pageSize': 100}, ttl=120)
            if not isinstance(batch, list):
                raise ProviderError('Unexpected Toast orders response.')
            for order in batch:
                if order.get('guid') not in seen:
                    seen.add(order.get('guid'))
                    result.append(order)
            if len(batch) < 100:
                return result
        raise ProviderError('Toast pagination exceeded safety limit; report withheld.')

    async def item_sales(self, day=None, query='', fallback_closeout=4):
        cfg = self.config()
        restaurant = await self.optional('restaurant', '/restaurants/v1/restaurants/' + cfg['restaurant_guid'], ttl=3600)
        closeout = (restaurant or {}).get('general', {}).get('closeoutHour')
        fallback = closeout is None
        closeout = int(fallback_closeout if fallback else closeout)
        day = day or business_day(datetime.now(timezone.utc), closeout)
        orders = await self.orders(day)
        menus = await self.get('/menus/v2/menus', ttl=3600)
        names = menu_names(menus)
        if not names:
            raise ProviderError('Toast menu classification unavailable; item count withheld.')
        try:
            observed_at=datetime.now(timezone.utc)
            size=whole_pizza_query(query)
            if size is not None:
                verified=whole_pizza_summary(orders,day,observed_at,closeout,group_names=names)
                count=verified['observed_quantity'] if size=='all' else verified['by_size'][size]
                start,end=bounds(day,closeout)
                result={**verified,'period_start':start.isoformat(),'period_end':end.isoformat(),'query':query,
                    'exact':verified['quality']=='complete','matched_quantity':count if verified['quality']=='complete' else None,
                    'observed_matching_quantity':count,'items':[x for x in verified['items'] if size=='all' or x['size']==size],
                    'items_truncated':False}
            else:
                # The same proven split quantities must apply to flavor searches,
                # not just whole-pizza totals. Preserve the original order data.
                normalized=orders;reconciled={'groups':0,'source_lines':0,'whole_pizzas':0}
                try:
                    mapping,_=load_mapping();normalized=[]
                    for order in orders:
                        promised=parsed(order.get('promisedDate') or order.get('estimatedFulfillmentDate'))
                        if (order.get('deleted') or order.get('voided') or order.get('excessFood') or order.get('approvalStatus')=='FUTURE'
                            or (promised and promised>observed_at)):
                            normalized.append(order);continue
                        revised,counts=reconcile_pizza_splits(order,mapping);normalized.append(revised)
                        for key,value in counts.items():reconciled[key]+=value
                except (ValueError,OSError,KeyError,TypeError):
                    normalized=orders;reconciled={'groups':0,'source_lines':0,'whole_pizzas':0}
                result = item_sales_summary(normalized, names, day, observed_at, closeout, query)
                result['pizza_split_reconciliation']=reconciled
        except (ValueError, KeyError, TypeError, OSError):
            raise ProviderError('Toast item records are incomplete; item count withheld.') from None
        result.update({'source': 'Toast orders and menu groups', 'fresh_at': utcnow(), 'cache_max_age_seconds': 120})
        if fallback:
            result['limitations'].append(f'Restaurant cutoff unavailable; configured {closeout:02}:00 Eastern cutoff used.')
        return result

    async def modifier_sales(self, start, end, query='', parent_query='', fallback_closeout=4):
        cfg = self.config()
        restaurant = await self.optional('restaurant', '/restaurants/v1/restaurants/' + cfg['restaurant_guid'], ttl=3600)
        closeout = (restaurant or {}).get('general', {}).get('closeoutHour')
        fallback = closeout is None
        closeout = int(fallback_closeout if fallback else closeout)
        now = datetime.now(timezone.utc)
        today = business_day(now, closeout)
        if start > end or start < today - timedelta(days=31) or end > today or (end - start).days > 7:
            raise ProviderError('Modifier range must be within the past 31 days and no longer than 8 business days.')
        days = [(start + timedelta(days=n)) for n in range((end - start).days + 1)]
        orders_by_day = [(day, await self.orders(day)) for day in days]
        names = menu_names(await self.get('/menus/v2/menus', ttl=3600))
        try:
            result = modifier_sales_summary(orders_by_day, names, now, query, parent_query)
        except (ValueError, KeyError, TypeError):
            raise ProviderError('Toast modifier records are incomplete; modifier count withheld.') from None
        result.update({'source': 'Toast order modifiers', 'fresh_at': utcnow(), 'cache_max_age_seconds': 120})
        if fallback:
            result['limitations'].append(f'Restaurant cutoff unavailable; configured {closeout:02}:00 Eastern cutoff used.')
        return result

    async def weekly_sales(self, week_of='this', start_weekday=0, fallback_closeout=4):
        now = datetime.now(timezone.utc)
        cfg = self.config()
        restaurant = await self.optional('restaurant', '/restaurants/v1/restaurants/' + cfg['restaurant_guid'], ttl=3600)
        closeout = (restaurant or {}).get('general', {}).get('closeoutHour')
        fallback = closeout is None
        closeout = int(fallback_closeout if fallback else closeout)
        start, end, through, complete = week_window(week_of, start_weekday, now, closeout)
        daily = []
        for offset in range((through - start).days + 1):
            day = start + timedelta(days=offset)
            report = sales_summary(await self.orders(day), day, now, closeout)
            daily.append({key: report[key] for key in ('date', 'net_sales', 'orders', 'checks', 'discounts', 'refunds', 'voids')})
        total = money(sum(row['net_sales'] for row in daily))
        checks = sum(row['checks'] for row in daily)
        result = {'source': 'Toast orders', 'fresh_at': utcnow(), 'cache_max_age_seconds': 120,
                  'week_start': start.isoformat(), 'week_end': end.isoformat(),
                  'through_business_date': through.isoformat(), 'complete': complete,
                  'period_start': bounds(start, closeout)[0].isoformat(),
                  'period_end': (bounds(end, closeout)[1] if complete else now.astimezone(ET)).isoformat(),
                  'net_sales': total, 'orders': sum(row['orders'] for row in daily), 'checks': checks,
                  'average_check': money(total / checks) if checks else None,
                  'discounts': money(sum(row['discounts'] for row in daily)),
                  'refunds': money(sum(row['refunds'] for row in daily)),
                  'voids': sum(row['voids'] for row in daily), 'daily': daily,
                  'limitations': ['Net sales include open checks; refunds are attributed to the original order business day and may revise historical totals.',
                                  'Future orders are excluded from realized sales.']}
        if not complete:
            result['limitations'].append('Current week is partial through retrieval time; future business dates are not included.')
        if fallback:
            result['limitations'].append(f'Restaurant cutoff unavailable; configured {closeout:02}:00 Eastern cutoff used.')
        return result

    async def product_mix_week(self, week_of='this', compare_to='', start_weekday=0, fallback_closeout=4):
        now = datetime.now(timezone.utc)
        cfg = self.config()
        restaurant = await self.optional('restaurant', '/restaurants/v1/restaurants/' + cfg['restaurant_guid'], ttl=3600)
        closeout = (restaurant or {}).get('general', {}).get('closeoutHour')
        fallback = closeout is None
        closeout = int(fallback_closeout if fallback else closeout)
        names = menu_names(await self.get('/menus/v2/menus', ttl=3600))
        if not names:
            raise ProviderError('Toast menu classification unavailable; product mix withheld.')

        async def one_week(selector):
            start, end, through, complete = week_window(selector, start_weekday, now, closeout)
            orders = []
            for offset in range((through - start).days + 1):
                orders.extend(await self.orders(start + timedelta(days=offset)))
            try:
                summary = product_mix(orders, names, now)
            except (ValueError, KeyError, TypeError):
                raise ProviderError('Toast product-mix records are incomplete; counts withheld.') from None
            summary.update({'week_start': start.isoformat(), 'week_end': end.isoformat(),
                            'through_business_date': through.isoformat(), 'complete': complete,
                            'period_start': bounds(start, closeout)[0].isoformat(),
                            'period_end': (bounds(end, closeout)[1] if complete else now.astimezone(ET)).isoformat()})
            if not complete:
                summary['limitations'].append('Current week is partial through retrieval time.')
            return summary

        primary = await one_week(week_of)
        if compare_to == 'previous':
            compare_to = (date.fromisoformat(primary['week_start']) - timedelta(days=1)).isoformat()
        comparison = await one_week(compare_to) if compare_to else None
        result = {'source': 'Toast orders and menu groups', 'fresh_at': utcnow(),
                  'cache_max_age_seconds': 120, 'primary': primary, 'comparison': comparison}
        if comparison:
            changes = {}
            for category in ('wings', 'fingers'):
                a, b = primary['categories'][category], comparison['categories'][category]
                changes[category] = {'portion_orders': a['portion_orders'] - b['portion_orders']
                                     if a['portion_orders'] is not None and b['portion_orders'] is not None else None,
                                     'pieces': a['total_pieces'] - b['total_pieces']
                                     if a['total_pieces'] is not None and b['total_pieces'] is not None else None}
            result['change_primary_minus_comparison'] = changes
        if fallback:
            primary['limitations'].append(f'Restaurant cutoff unavailable; configured {closeout:02}:00 Eastern cutoff used.')
            if comparison:
                comparison['limitations'].append(f'Restaurant cutoff unavailable; configured {closeout:02}:00 Eastern cutoff used.')
        return result

    async def forecast_sales(self, target, football, fallback_closeout=4):
        now = datetime.now(timezone.utc)
        cfg = self.config()
        restaurant = await self.optional('restaurant', '/restaurants/v1/restaurants/' + cfg['restaurant_guid'], ttl=3600)
        closeout = (restaurant or {}).get('general', {}).get('closeoutHour')
        fallback = closeout is None
        closeout = int(fallback_closeout if fallback else closeout)
        current_day = business_day(now, closeout)
        target = target or current_day
        if not current_day <= target <= current_day + timedelta(days=30):
            raise ProviderError('Forecast dates must be today or within the next 30 business days.')
        target_game = await football.on(target)
        last = current_day - timedelta(days=(current_day.weekday() - target.weekday()) % 7 or 7)
        history = []
        unavailable, empty = [], []
        for index in range(10):
            day = last - timedelta(days=7 * index)
            try:
                summary = sales_summary(await self.orders(day), day, now, closeout)
            except (ProviderError, ValueError, KeyError, TypeError):
                unavailable.append(day.isoformat())
                continue
            if not summary['orders']:
                empty.append(day.isoformat())
                continue
            games = await football.on(day)
            summary['game_kind'] = game_kind({'date': day.isoformat(),
                                              'nfl_schedule_available': games['nfl_schedule_available'],
                                              **games['signals']})
            history.append(summary)
        current = None
        if target == current_day:
            summary = sales_summary(await self.orders(target), target, now, closeout)
            elapsed = (now.astimezone(ET).hour - closeout) % 24
            current = {'net_sales': summary['net_sales'], 'orders': summary['orders'],
                       'elapsed_hours': elapsed, 'completed_hour_sales': money(sum(summary['hourly'][:elapsed]))}
        forecast = estimate_sales(target, history, target_game, current)
        if unavailable:
            forecast.setdefault('limitations', []).append(f'{len(unavailable)} comparable Toast dates were unavailable and excluded.')
        if empty:
            forecast.setdefault('limitations', []).append(f'{len(empty)} comparable dates returned no orders and were excluded; this may mean closure or missing history.')
        if fallback:
            forecast.setdefault('limitations', []).append(f'Restaurant cutoff unavailable; configured {closeout:02}:00 Eastern cutoff used.')
        return {'source': 'Toast orders; nflverse NFL schedule; Georgia Athletics UGA schedule; ESPN college scoreboard',
                'fresh_at': utcnow(), 'target_business_date': target.isoformat(),
                'target_period_start': bounds(target, closeout)[0].isoformat(),
                'target_period_end': bounds(target, closeout)[1].isoformat(),
                'forecast': forecast, 'games': target_game, 'current_sales': current,
                'manager_context': 'the restaurant is a Bills bar; management says UGA and other strong college football games materially affect demand. No fixed uplift percentage has been supplied or validated.'}

    async def optional(self, name, path, params=None, ttl=120):
        try:
            return await self.get(path, params, ttl)
        except ProviderError as e:
            self.health[path] = {'status': 'Unavailable', 'detail': str(e), 'checked_at': utcnow()}
            return None

    async def overview(self, settings, report_day=None):
        now = datetime.now(timezone.utc)
        cfg = self.config()
        restaurant = await self.optional('restaurant', '/restaurants/v1/restaurants/' + cfg['restaurant_guid'], ttl=3600)
        closeout = (restaurant or {}).get('general', {}).get('closeoutHour')
        limitations = ['Net sales include open checks; refunds are attributed to the original order business day. Later refunds may revise historical totals.',
                       'Discount amounts include comps; comp classification is not inferred. Voids count orders/checks, not individual items.',
                       'Historical sales pace uses current order values grouped by opened hour; it is not a historical point-in-time snapshot.']
        if closeout is None:
            closeout = settings.get('closeout_hour', 4)
            limitations.append(f'Restaurant cutoff unavailable; configured {closeout:02}:00 Eastern cutoff used.')
        closeout = int(closeout)
        day = report_day or business_day(now, closeout)
        first = min(day.replace(day=1), day - timedelta(days=13))
        daily = []
        raw_today = None
        d = first
        while d <= day:
            orders = await self.orders(d)
            daily.append(sales_summary(orders, d, now, closeout))
            if d == day:
                raw_today = orders
            d += timedelta(days=1)
        today = daily[-1]
        prior = next(x for x in daily if x['date'] == (day - timedelta(days=7)).isoformat())
        elapsed = (now.astimezone(ET).hour - closeout) % 24
        # Full completed hours avoid comparing a partial current hour to a whole hour.
        current_pace = sum(today['hourly'][:elapsed]) if report_day is None else today['net_sales']
        previous_pace = sum(prior['hourly'][:elapsed]) if report_day is None else prior['net_sales']
        entries = await self.optional('labor', '/labor/v1/timeEntries', {'businessDate': day.strftime('%Y%m%d')})
        employees = await self.optional('employees', '/labor/v1/employees', ttl=3600) if entries is not None else None
        labor = labor_summary(entries, employees or [], now, today['net_sales'], settings.get('overtime_multiplier')) if entries is not None else None
        if labor:
            labor.pop('employee_hours', None)
            limitations.append('Labor is based on shifts clocked in on the selected business day, excluding employer taxes/benefits. Open shifts are estimates; cross-cutoff shifts require payroll reconciliation.')
        else:
            limitations.append('Labor is unavailable: time-entry retrieval failed. Clock-in and labor changes cannot be verified.')
        stock = await self.optional('stock', '/stock/v1/inventory', {'status': 'OUT_OF_STOCK'})
        stock = [{'id': x.get('guid'), 'name': x.get('guid'), 'status': x.get('status')} for x in stock] if stock is not None else None
        limitations.append('Stock is a current snapshot at retrieval time, including on historical reports; historical stock is not available.')
        if stock is None:
            limitations.append('Stock retrieval failed; stock changes cannot be verified.')
        if stock:
            menus = await self.optional('menu names', '/menus/v2/menus', ttl=3600)
            names = {}
            def walk(value):
                if isinstance(value, dict):
                    if value.get('guid') and value.get('name'):
                        names[value['guid']] = value['name']
                    for child in value.values():
                        walk(child)
                elif isinstance(value, list):
                    for child in value:
                        walk(child)
            walk(menus)
            for item in stock:
                item['name'] = names.get(item['id'], 'Menu item ' + str(item['id'])[:8])
        overtime = None
        if settings.get('workweek_start') is not None and entries is not None:
            start = reporting_week_start(day, settings['workweek_start'])
            week_entries = []
            complete = True
            for i in range((day - start).days + 1):
                rows = await self.optional('weekly labor', '/labor/v1/timeEntries', {'businessDate': (start + timedelta(days=i)).strftime('%Y%m%d')})
                if rows is None:
                    complete = False
                else:
                    week_entries.extend(rows)
            if complete:
                totals = labor_summary(week_entries, employees or [], now, today['net_sales'], settings.get('overtime_multiplier'))
                names = {x['guid']: x.get('firstName', 'Employee') for x in employees or []}
                if totals['hours_complete']:
                    overtime = [{'employee': names.get(k, 'Employee ' + k[:8]), 'hours': v} for k, v in totals['employee_hours'].items() if v >= 35]
                limitations.append(f'Overtime watch: 35+ recorded hours since {start}; advisory, not a payroll determination. Other locations are not included.')
        if overtime is None:
            limitations.append('Overtime watch unavailable until the payroll workweek start is configured and weekly time entries are readable.')
        upcoming = list(today['upcoming'])
        future_complete = True
        for offset in range(1, settings.get('future_days', 7) + 1):
            try:
                future = await self.orders(day + timedelta(days=offset))
                upcoming.extend(sales_summary(future, day + timedelta(days=offset), now, closeout)['upcoming'])
            except ProviderError:
                future_complete = False
        limitations.append(f'Upcoming orders cover {settings.get("future_days", 7)} days. ' + ('Scan complete.' if future_complete else 'Some future dates could not be retrieved.'))
        start_week = reporting_week_start(day, settings.get('workweek_start') if settings.get('workweek_start') is not None else 0)
        wtd = sum(x['net_sales'] for x in daily if x['date'] >= start_week.isoformat())
        mtd = sum(x['net_sales'] for x in daily if x['date'] >= day.replace(day=1).isoformat())
        seven = sum(x['net_sales'] for x in daily[-7:])
        prevseven = sum(x['net_sales'] for x in daily[-14:-7])
        return {'mode': 'live', 'source': 'Toast', 'fresh_at': utcnow(), 'cache_max_age_seconds': 120, 'business_date': day.isoformat(),
                'period_start': bounds(day, closeout)[0].isoformat(), 'period_end': bounds(day, closeout)[1].isoformat(),
                'today': today, 'daily': daily, 'wtd': money(wtd), 'week_start': start_week.isoformat(),
                'week_end': (start_week + timedelta(days=6)).isoformat(),
                'mtd': money(mtd), 'seven_day_change': change(seven, prevseven),
                'pace': {'previous': prior['hourly'], 'change': change(current_pace, previous_pace), 'through_hour': f'{(closeout+elapsed)%24:02}:00', 'label': 'Same weekday last week · completed hours'},
                'labor': labor, 'stock': stock, 'overtime': overtime,
                'upcoming': sorted({x['id']: x for x in upcoming}.values(), key=lambda x: x.get('when') or ''),
                'limitations': limitations, 'health': dict(self.health)}


def demo_overview(report_day=None, start_weekday=0):
    now = datetime.now(timezone.utc)
    day = report_day or business_day(now)
    daily = []
    for offset in range(30, -1, -1):
        d = day - timedelta(days=offset)
        orders = []
        for h in range(11, 23):
            if offset == 0 and report_day is None and h >= now.astimezone(ET).hour:
                continue
            for n in range(3 + (h * 7 + d.day) % 9):
                orders.append({'guid': f'demo-{d}-{h}-{n}', 'openedDate': datetime.combine(d, datetime.min.time(), ET).replace(hour=h).isoformat(),
                               'checks': [{'amount': 26 + (h * 3 + n * 11 + d.day) % 75, 'payments': [], 'selections': []}]})
        daily.append(sales_summary(orders, d, now))
    today = daily[-1]
    start_week = reporting_week_start(day, start_weekday)
    return {'mode': 'demo', 'source': 'Synthetic demo — not restaurant data', 'fresh_at': utcnow(), 'cache_max_age_seconds': 0,
            'business_date': day.isoformat(), 'period_start': bounds(day)[0].isoformat(), 'period_end': bounds(day)[1].isoformat(),
            'today': today, 'daily': daily, 'wtd': money(sum(x['net_sales'] for x in daily if x['date'] >= start_week.isoformat())),
            'week_start': start_week.isoformat(), 'week_end': (start_week + timedelta(days=6)).isoformat(),
            'mtd': money(sum(x['net_sales'] for x in daily if x['date'] >= day.replace(day=1).isoformat())),
            'seven_day_change': 8.4, 'pace': {'previous': daily[-8]['hourly'], 'change': None, 'label': 'Synthetic comparison', 'through_hour': 'demo'},
            'labor': {'hours': 26.5, 'cost': 465, 'percentage': None if not today['net_sales'] else money(465/today['net_sales']*100),
                      'estimated': True, 'complete': True, 'clocked_in': [{'employee': 'Demo team member', 'hours': 4.5, 'since': now.isoformat()}]},
            'stock': [{'id': 'demo-stock', 'name': 'Demo seasonal topping', 'status': 'OUT_OF_STOCK'}], 'overtime': [],
            'upcoming': [{'id': 'demo-catering', 'when': datetime.combine(day+timedelta(days=1), datetime.min.time(), ET).replace(hour=12).isoformat(), 'amount': 420, 'source': 'Demo'}],
            'limitations': ['All figures and people on this screen are synthetic demonstration data.'], 'health': {}}


class VendorInvoice(BaseModel):
    id: str
    vendor: str
    amount: float
    due_date: str | None = None


class InventoryItem(BaseModel):
    id: str
    name: str
    quantity: float | None = None
    unit_cost: float | None = None


class R365Provider(Protocol):
    async def invoices(self) -> list[VendorInvoice]: ...
    async def inventory(self) -> list[InventoryItem]: ...
    async def recipe_costs(self) -> list[dict]: ...
    async def purchasing(self) -> list[dict]: ...
    async def vendors(self) -> list[dict]: ...
    async def products(self) -> list[dict]: ...
    async def accounting_summaries(self) -> list[dict]: ...


class OutlookProvider(Protocol):
    async def important_unread(self) -> list[dict]: ...
    async def vendor_correspondence(self) -> list[dict]: ...
    async def catering_correspondence(self) -> list[dict]: ...


class MondayProvider(Protocol):
    async def unresolved_tasks(self) -> list[dict]: ...
    async def upcoming_deadlines(self) -> list[dict]: ...


def validate_ezcater(body, signature, secret, now=None):
    """Official X-Ezcater-Signature: timestamp.hex(HMAC-SHA256(timestamp.body))."""
    try:
        stamp, supplied = signature.split('.', 1)
        if abs((now or time.time()) - int(stamp)) > 300:
            return False
        expected = hmac.new(secret.encode(), stamp.encode() + b'.' + body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, supplied)
    except (ValueError, AttributeError):
        return False
