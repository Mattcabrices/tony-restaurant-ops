"""Order search across Toast and Clover, for the AI to reason over.

Returns who ordered, when, how, every item with modifiers, and whole-pizza counts.
Card/payment details, tokens and street addresses are never read into a result.
Finished days are kept in a small local cache so repeat questions are instant.
"""
import asyncio
import gzip
import json
import re
import sqlite3
import time
import unicodedata
from collections import defaultdict
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from difflib import SequenceMatcher
from pathlib import Path

import httpx

from .calculations import ET, bounds, business_day, parsed
from .clover import CloverError
from .clover_history import pizza_size
from .pizza_splits import reconcile_pizza_splits
from .providers import ProviderError
from .restaurant_profile import profile
from .whole_pizzas import EXCLUDED, PREFIX, load_mapping

SETTLE_DAYS = 4                   # Recent days are re-read: edits, refunds, tips.
MAX_DAYS = 800
OUTPUT_CHARS = 26000
CACHE_VERSION = 1
LIVE = ('ok', 'scheduled', 'needs approval')
UBER = re.compile(r'^UE-(.+?)[0-9A-F]{5}(?=\s|$)')  # Uber Eats prefixes names and appends a code: UE-Sample12ABC S.


def toast_start():
    """First Toast business day. Earlier dates use Clover; blank means Toast only."""
    value = profile()['toast_start_date']
    return date.fromisoformat(value) if value else date.min


def pizza_mapping():
    """(mapping, version). Each restaurant supplies its own; none is bundled."""
    try:
        mapping, digest = load_mapping()
        return mapping, digest[:12]
    except (OSError, ValueError):
        return None, 'none'


def folded(value):
    text = unicodedata.normalize('NFKD', str(value or ''))
    return ' '.join(''.join(c for c in text if not unicodedata.combining(c)).casefold().split())


def words(value):
    return re.findall(r"[^\W_]+", folded(value).replace("'", '').replace('’', ''))


def guest_name(person):
    name = ' '.join(str(person[k]).strip() for k in ('firstName', 'lastName') if person.get(k))
    return UBER.sub(lambda m: m[1], name).strip()[:80]


def digits(value):
    number = re.sub(r'\D', '', str(value or ''))
    return number[1:] if len(number) == 11 and number.startswith('1') else number


def similar_word(a, b):
    """Nicknames/typos: Sam~Samuel, Jon~Jonathan, Kasey~Casey, M~Morgan."""
    short, long = sorted((a, b), key=len)
    if len(short) == 1 or len(short) >= 3 and long.startswith(short):
        return long.startswith(short)
    return len(short) >= 4 and SequenceMatcher(None, a, b).ratio() >= 0.8


def match_person(query, fields):
    """'exact', 'similar' or None. Fields are names, tabs and order titles."""
    if '@' in query:
        return 'exact' if any(folded(query) == folded(v) for v in fields) else None
    number = digits(query)
    if len(number) >= 7 and not re.search('[a-z]', query, re.I):
        return 'exact' if any(digits(v).endswith(number) for v in fields if len(digits(v)) >= 7) else None
    wanted = words(query)
    best = None
    for value in fields if wanted else ():
        have = words(value)
        if all(w in have for w in wanted):
            return 'exact'
        # A lone initial may stand in for a last name, never for a first name.
        if (any(len(h) > 1 and similar_word(wanted[0], h) for h in have)
                and all(any(similar_word(w, h) for h in have) for w in wanted[1:])):
            best = 'similar'
    return best


def decimal(value):
    try:
        number = Decimal(str(value))
        return number if number.is_finite() else None
    except (InvalidOperation, TypeError, ValueError):
        return None


def cents(value):
    number = decimal(value)
    return None if number is None else float(round(number, 2))


def quantity(value):
    number = decimal(value)
    if number is None:
        return None
    return int(number) if number == number.to_integral_value() else round(float(number), 3)


def local(stamp):
    when = parsed(stamp) if stamp else None
    return when.astimezone(ET).strftime('%Y-%m-%d %H:%M') if when else None


def toast_pizza(line, mapping):
    """(size, certain). Same identity rules as the verified whole-pizza count."""
    name = line.get('displayName') or ''
    if EXCLUDED.search(name) or line.get('selectionType') not in (None, 'NONE'):
        return None, True
    identity = mapping['items'].get((line.get('item') or {}).get('guid'))
    prefix = PREFIX.get(name.split()[0].casefold()) if name.split() else None
    if identity:
        return (identity['size'], True) if prefix == identity['size'] else (None, False)
    group = mapping['groups'].get((line.get('itemGroup') or {}).get('guid'))
    return None, not (group or re.search(r'\bpizza\b', name, re.I))


def modifiers(line, depth=0):
    found = []
    for mod in line.get('modifiers') or []:
        if isinstance(mod, dict) and not mod.get('voided'):
            pre = mod.get('preModifier')
            pre = (pre.get('displayName') or '') if isinstance(pre, dict) else ''
            found.append((pre + ' ' + (mod.get('displayName') or '')).strip()[:60])
            if depth < 2:
                found += modifiers(mod, depth + 1)
    return [m for m in found if m][:25]


def project_toast(order, mapping):
    """One order, without payment, address or device data."""
    order, _ = reconcile_pizza_splits(order, mapping)
    checks = []
    for check in order.get('checks') or []:
        if not isinstance(check, dict) or check.get('deleted'):
            continue
        person = check.get('customer') if isinstance(check.get('customer'), dict) else {}
        lines, pizzas, unsure = [], defaultdict(Decimal), 0
        for line in check.get('selections') or []:
            if not isinstance(line, dict) or line.get('deleted'):
                continue
            row = {'qty': quantity(line.get('quantity')), 'item': (line.get('displayName') or '(unnamed)')[:80], 'price': cents(line.get('price'))}
            mods = modifiers(line)
            if mods:
                row['mods'] = mods
            if line.get('voided'):
                row['voided'] = True
            elif not line.get('deferred'):
                size, certain = toast_pizza(line, mapping)
                if size and decimal(line.get('quantity')) is not None:
                    pizzas[size] += decimal(line['quantity'])
                    row['whole_pizza'] = size
                elif not certain:
                    unsure += 1
                    row['maybe_pizza'] = True
            lines.append(row)
        checks.append({'check': str(check.get('displayNumber') or ''),
                       'name': guest_name(person),
                       'phone': str(person.get('phone') or '')[:20], 'email': str(person.get('email') or '')[:100],
                       'tab': str(check.get('tabName') or '')[:80], 'subtotal': cents(check.get('amount')), 'total': cents(check.get('totalAmount')),
                       'paid': check.get('paymentStatus'), 'voided': bool(check.get('voided')), 'items': lines,
                       'pizzas': {k: quantity(v) for k, v in pizzas.items()}, 'unsure_pizzas': unsure})
    approval = order.get('approvalStatus')
    status = ('voided' if order.get('voided') else 'deleted' if order.get('deleted') or order.get('excessFood') else 'test' if order.get('createdInTestMode')
              else {'NOT_APPROVED': 'not approved', 'NEEDS_APPROVAL': 'needs approval', 'FUTURE': 'scheduled'}.get(approval, 'ok'))
    day = str(order.get('businessDate') or '')
    return {'pos': 'Toast', 'id': order.get('guid'), 'order': str(order.get('displayNumber') or ''),
            'date': f'{day[:4]}-{day[4:6]}-{day[6:]}' if len(day) == 8 else None,
            'opened': local(order.get('openedDate')), 'promised': local(order.get('promisedDate')),
            'channel': (order.get('diningOption') or {}).get('guid'), 'order_source': order.get('source'),
            'server': (order.get('server') or {}).get('guid'), 'recipient': UBER.sub(lambda m: m[1], str((order.get('deliveryInfo') or {}).get('recipientName') or ''))[:80],
            'guests': order.get('numberOfGuests'), 'status': status, 'checks': checks}


def project_clover(order, people):
    lines, pizzas, unsure = [], defaultdict(Decimal), 0
    for line in (order.get('lineItems') or {}).get('elements', []):
        if not isinstance(line, dict):
            continue
        row = {'qty': 1, 'item': (line.get('name') or '(unnamed)')[:80], 'price': (line.get('price') or 0) / 100}
        mods = [m['name'][:60] for m in (line.get('modifications') or {}).get('elements', []) if m.get('name')]
        if mods:
            row['mods'] = mods[:25]
        if line.get('refunded') or line.get('exchanged'):
            row['voided'] = True
        else:
            # Same rule as the imported counts: regular, inventory-linked lines only.
            size = pizza_size(line.get('name') or '') if line.get('isRevenue') is not False else None
            if size and (line.get('item') or {}).get('id') and line.get('type', 'REGULAR') == 'REGULAR':
                pizzas[size] += 1
                row['whole_pizza'] = size
            elif size:
                unsure += 1
                row['maybe_pizza'] = True
        lines.append(row)
    linked = [people.get(c.get('id')) or {} for c in (order.get('customers') or {}).get('elements', [])]
    opened = datetime.fromtimestamp((order.get('clientCreatedTime') or order.get('createdTime') or 0) / 1000, ET)
    check = {'check': '', 'name': ' / '.join(p['name'] for p in linked if p.get('name'))[:160],
             'phone': next((p['phone'] for p in linked if p.get('phone')), ''), 'email': next((p['email'] for p in linked if p.get('email')), ''),
             'tab': str(order.get('title') or '')[:80], 'note': str(order.get('note') or '')[:160], 'subtotal': None,
             'total': (order.get('total') or 0) / 100, 'paid': None, 'voided': False, 'items': lines,
             'pizzas': {k: quantity(v) for k, v in pizzas.items()}, 'unsure_pizzas': unsure}
    state = order.get('paymentState')
    return {'pos': 'Clover', 'id': order.get('id'), 'order': str(order.get('id') or ''), 'date': None,
            'opened': opened.strftime('%Y-%m-%d %H:%M'), 'promised': None, 'channel': (order.get('orderType') or {}).get('label'),
            'order_source': None, 'server': None, 'recipient': '', 'guests': None,
            'status': 'test' if order.get('testMode') else 'ok' if state == 'PAID' else 'refunded' if state == 'REFUNDED' else 'unpaid', 'checks': [check]}


class OrderCache:
    """Local day cache in the existing protected data directory."""

    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as c, c:
            c.execute('CREATE TABLE IF NOT EXISTS day_orders(pos TEXT, day TEXT, version TEXT, fetched TEXT, orders BLOB, PRIMARY KEY(pos,day))')
            c.execute('CREATE TABLE IF NOT EXISTS clover_people(id TEXT PRIMARY KEY, name TEXT, phone TEXT, email TEXT)')

    def connect(self):
        return sqlite3.connect(self.path, timeout=20)

    def get(self, pos, day, version=''):
        with closing(self.connect()) as c:
            row = c.execute('SELECT fetched,orders FROM day_orders WHERE pos=? AND day=? AND version=?',
                            (pos, day.isoformat(), f'{CACHE_VERSION}:{version}')).fetchone()
        return (row[0], json.loads(gzip.decompress(row[1]))) if row else None

    def put(self, pos, day, orders, version=''):
        blob = gzip.compress(json.dumps(orders, separators=(',', ':')).encode(), 6)
        with closing(self.connect()) as c, c:
            c.execute('INSERT OR REPLACE INTO day_orders VALUES(?,?,?,?,?)',
                      (pos, day.isoformat(), f'{CACHE_VERSION}:{version}', datetime.now(timezone.utc).isoformat(), blob))

    def people(self):
        with closing(self.connect()) as c:
            return {r[0]: {'name': r[1], 'phone': r[2], 'email': r[3]} for r in c.execute('SELECT * FROM clover_people')}

    def put_people(self, rows):
        with closing(self.connect()) as c, c:
            c.executemany('INSERT OR REPLACE INTO clover_people VALUES(?,?,?,?)', rows)


class OrderLookup:
    def __init__(self, toast, clover, cache, booking_index=None):
        self.toast, self.clover, self.cache, self.booking_index = toast, clover, cache, booking_index
        self.lock = asyncio.Lock()
        self.labels = {}
        self.staff = {}

    async def _channel(self, guid):
        if guid and guid not in self.labels:
            try:
                option = await self.toast.get('/config/v2/diningOptions/' + guid, ttl=86400)
                self.labels[guid] = option.get('name') if isinstance(option, dict) else None
            except ProviderError:
                return  # Retry on the next question.

    async def _servers(self):
        if not self.staff:
            try:
                for e in await self.toast.get('/labor/v1/employees', ttl=3600):
                    self.staff[e.get('guid')] = ' '.join(str(v) for v in (e.get('chosenName') or e.get('firstName'), e.get('lastName')) if v)
            except (ProviderError, TypeError, AttributeError):
                self.staff = {}
        return self.staff

    async def _toast_day(self, day, today):
        # A changed menu mapping must not reuse counts made under the old one.
        mapping, version = pizza_mapping()
        saved = self.cache.get('Toast', day, version)
        if saved and parsed(saved[0]).astimezone(ET).date() >= day + timedelta(days=SETTLE_DAYS):
            return saved[1]
        mapping = mapping or {'items': {}, 'groups': {}}
        raw = await self.toast.orders(day)
        if day > today and self.booking_index:
            seen = {o.get('guid') for o in raw}
            for identity in self.booking_index.ids(day)[:20]:
                if identity not in seen:
                    try:
                        raw = raw + [await self.toast.get('/orders/v2/orders/' + identity, ttl=300)]
                    except ProviderError:
                        continue
        orders = [project_toast(o, mapping) for o in raw if isinstance(o, dict)]
        for order in orders:
            if day > today or not order['date']:
                order['date'] = day.isoformat()
        if day < today and orders:  # An empty day is never saved as fact.
            self.cache.put('Toast', day, orders, version)
        return orders

    async def _clover_get(self, path, params):
        saved = self.clover.vault.read().get('clover', {})
        if not saved.get('token') or not saved.get('merchant_id'):
            raise CloverError('Clover is not connected.')
        url = f"https://api.clover.com/v3/merchants/{saved['merchant_id']}/{path}"
        for attempt in range(4):
            try:
                r = await self.clover.http.get(url, params=params, headers={'Authorization': 'Bearer ' + saved['token'], 'Accept': 'application/json'})
            except httpx.HTTPError:
                r = None
            if r is not None and r.status_code == 200:
                return r.json()
            if r is not None and r.status_code != 429 and r.status_code < 500:
                raise CloverError(f'Clover unavailable (HTTP {r.status_code}).')
            await asyncio.sleep(2 ** attempt * .5)
        raise CloverError('Clover unavailable.')

    async def _clover_people(self, ids):
        people = self.cache.people()
        if not people and ids:  # First use: load the directory in pages.
            for offset in range(0, 10000, 500):
                try:
                    page = (await self._clover_get('customers', {'limit': 500, 'offset': offset, 'expand': 'phoneNumbers,emailAddresses'})).get('elements', [])
                except CloverError:
                    break
                self.cache.put_people([self._person(p) for p in page if p.get('id')])
                if len(page) < 500:
                    break
            people = self.cache.people()
        for identity in sorted(i for i in ids if i and i not in people):
            if not re.fullmatch(r'[A-Za-z0-9]{8,32}', identity):
                continue
            try:
                row = self._person(await self._clover_get('customers/' + identity, {'expand': 'phoneNumbers,emailAddresses'}))
            except CloverError as error:
                if 'HTTP 404' not in str(error):
                    raise  # Never save a day whose customers could not be read.
                row = (identity, '', '', '')
            self.cache.put_people([row])
            people[identity] = {'name': row[1], 'phone': row[2], 'email': row[3]}
        return people

    @staticmethod
    def _person(p):
        phones = (p.get('phoneNumbers') or {}).get('elements') or [{}]
        emails = (p.get('emailAddresses') or {}).get('elements') or [{}]
        return (p['id'], guest_name(p),
                str(phones[0].get('phoneNumber') or ''), str(emails[0].get('emailAddress') or ''))

    async def _clover_day(self, day):
        saved = self.cache.get('Clover', day)
        if saved:
            return saved[1]
        if not self.clover:
            raise CloverError('Clover is not connected.')
        start, end = bounds(day, 0)  # Imported Clover history uses calendar days.
        raw = await self.clover.orders_between(start, end, 'lineItems.modifications,customers,orderType,payments,refunds')
        people = await self._clover_people({c.get('id') for o in raw for c in (o.get('customers') or {}).get('elements', [])})
        orders = [{**project_clover(o, people), 'date': day.isoformat()} for o in raw if (o.get('lineItems') or {}).get('elements')]
        if orders:
            self.cache.put('Clover', day, orders)
        return orders

    async def load(self, start, end, today, seconds):
        """(orders, missing days, problems). Newest first, so a time limit drops old days."""
        orders, missing, problems, deadline = [], [], set(), time.monotonic() + seconds
        day = end
        while day >= start:
            remaining = deadline - time.monotonic()
            try:
                if remaining <= 0:
                    raise TimeoutError('Time limit reached; ask again to continue from the days already saved.')
                async with asyncio.timeout(remaining):
                    batch = await (self._toast_day(day, today) if day >= toast_start() else self._clover_day(day))
                orders.extend(batch)
                if not batch and day <= today:
                    missing.append(day.isoformat())
            except (ProviderError, CloverError, TimeoutError, ValueError, KeyError, TypeError) as error:
                missing.append(day.isoformat())
                problems.add(str(error)[:160] or 'Time limit reached; ask again to continue from the days already saved.')
            day -= timedelta(days=1)
        return orders, sorted(missing), sorted(problems)

    async def search(self, args, closeout=4, now=None, seconds=150):
        now = now or datetime.now(timezone.utc)
        today = business_day(now, closeout)
        start, end = sorted((date.fromisoformat(args['start']), date.fromisoformat(args['end'])))
        if (end - start).days >= MAX_DAYS or end > today + timedelta(days=366):
            raise ValueError(f'Use up to {MAX_DAYS} days, no more than a year ahead.')
        person = str(args.get('person') or '').strip()[:100]
        nearby = []
        async with self.lock:
            orders, missing, problems = await self.load(start, end, today, seconds)
            found = self._filter(orders, args, person)
            # A single-date miss is usually the neighbouring day or a spelling.
            if person and not any(f['match'] == 'exact' for f in found) and (end - start).days <= 2:
                for low, high in ((start - timedelta(days=3), start - timedelta(days=1)), (end + timedelta(days=1), min(today, end + timedelta(days=3)))):
                    if low <= high:
                        more, _, _ = await self.load(low, high, today, 45)
                        nearby += self._filter(more, args, person)
            used = found + nearby
            for guid in {f['order']['channel'] for f in used if f['order']['pos'] == 'Toast'}:
                await self._channel(guid)
            servers = await self._servers() if any(f['order'].get('server') for f in used) else {}
        first = toast_start()
        result = {'source': 'Toast orders' if start >= first else 'Clover orders' if end < first else f'Clover orders (before {first}) and Toast orders',
                  'start': start.isoformat(), 'end': end.isoformat(), 'retrieved_at': now.astimezone(ET).strftime('%Y-%m-%d %H:%M'),
                  'orders_scanned': len(orders), 'complete': not missing, 'dates_without_data': missing[:40], 'problems': problems,
                  'today_is_partial': start <= today <= end,
                  'notes': ['total includes tax and excludes voluntary tips; Clover total is the order total. Voided, rejected, refunded or unpaid orders are marked by status and not counted.',
                            'whole_pizzas excludes slices, calzones, packages and modifiers. A maybe_pizza item could not be identified by the menu map.',
                            'Matches use guest name, tab name, delivery recipient or order title; one name can be different people.']}
        rank = {'person': 'orders', 'person_by_pizzas': 'whole_pizzas', 'person_by_spend': 'total_dollars'}.get(args.get('group_by'))
        if rank:
            result['summary'] = self._summary(found)
            result['people'] = self._people(found, 60, rank)
            result['people_ranked_by'] = rank
            result['notes'].append('Generic tabs (Here, To Go, table numbers) and catering/event tabs can appear as people; a name without contact details may be several guests.')
        else:
            exact = [f for f in found if f['match'] != 'similar']
            similar = [f for f in found if f['match'] == 'similar']
            result['summary'] = self._summary(exact)
            result['by_person'] = self._people(exact, 25) if len(exact) > 1 else []
            result['orders'] = [self._row(f, servers) for f in exact]
            if similar:
                result['similar_name_orders'] = [self._row(f, servers) for f in similar]
            if nearby:
                result['nearby_dates_orders'] = [self._row(f, servers) for f in nearby]
                result['notes'].append('nearby_dates_orders are OUTSIDE the requested dates: offer them, never count them as the requested date.')
        if not profile()['share_guest_contacts_with_ai']:
            self._hide_contacts(result)
        if end >= first and pizza_mapping()[0] is None:
            result['notes'].append('No whole-pizza menu mapping is configured, so Toast whole-pizza counts are unknown, not zero. Count from item names only as an estimate.')
            for row in [result['summary'], *result.get('by_person', []), *result.get('people', [])]:
                row['whole_pizzas'], row['pizzas_by_size'] = None, None
        return self._fit(result)

    @staticmethod
    def _hide_contacts(result):
        """Contacts still match searches locally; by default they never reach the AI."""
        for key in ('orders', 'similar_name_orders', 'nearby_dates_orders'):
            for row in result.get(key, []):
                for check in row['checks']:
                    check.pop('phone', None)
                    check.pop('email', None)
        for key in ('by_person', 'people'):
            for row in result.get(key, []):
                row.pop('phone', None)
                row.pop('email', None)
        result['notes'].append('Phone and email are withheld from AI by this installation; use /guest for a local contact lookup.')

    @staticmethod
    def _filter(orders, args, person):
        item = words(args.get('item'))
        low, high = decimal(args.get('min_total') or 0) or 0, decimal(args.get('max_total') or 0) or 0
        number = str(args.get('order_number') or '').strip().lstrip('#')
        # Names the model passes as blanks or zeros mean "no filter".
        number = '' if number == '0' else number

        def has_item(check):
            return any(all(any(w.startswith(t) or (len(t) > 3 and t.endswith('s') and w == t[:-1]) for w in words(line['item'])) for t in item)
                       for line in check['items'] if not line.get('voided'))

        found = []
        for order in orders:
            if order['status'] in ('deleted', 'test'):
                continue
            checks, best = [], 'filter'
            for check in order['checks']:
                if person:
                    match = match_person(person, [check['name'], check['tab'], check.get('note') or '', check['phone'], check['email']])
                    if match is None and len(order['checks']) == 1:
                        match = match_person(person, [order.get('recipient') or ''])
                    if match is None:
                        continue
                if number and number not in (order['order'], check['check']):
                    continue
                if item and not has_item(check):
                    continue
                total = decimal(check['total'])
                if (low or high) and (total is None or total < low or (high and total > high)):
                    continue
                checks.append(check)
                if person:
                    best = 'exact' if 'exact' in (best, match) else match
            if checks:
                found.append({'order': order, 'checks': checks, 'match': best})
        return found

    @staticmethod
    def _counts(found):
        """Only live checks on live orders count."""
        live = [f for f in found if f['order']['status'] in LIVE]
        sizes, unsure, total = defaultdict(Decimal), 0, Decimal(0)
        for f in live:
            for check in f['checks']:
                if check['voided']:
                    continue
                unsure += check['unsure_pizzas']
                total += decimal(check['total']) or 0
                for size, value in check['pizzas'].items():
                    sizes[size] += decimal(value)
        return {'orders': len({f['order']['id'] for f in live}), 'whole_pizzas': quantity(sum(sizes.values(), Decimal(0))),
                'pizzas_by_size': {k: quantity(v) for k, v in sizes.items()}, 'unidentified_pizza_lines': unsure, 'total_dollars': cents(total)}

    def _summary(self, found):
        return {**self._counts(found), 'void_or_rejected_orders': sum(f['order']['status'] not in LIVE for f in found)}

    def _people(self, found, limit, rank='orders'):
        groups, named = defaultdict(list), defaultdict(set)
        for f in found:
            for check in f['checks']:
                if check['name']:
                    named[digits(check['phone']) or folded(check['email'])].add(folded(check['name']))
        for f in found:
            for check in f['checks']:
                label = check['name'] or check['tab'] or f['order'].get('recipient') or ''
                if label:
                    # Delivery apps share proxy numbers: a contact used by several names
                    # is ignored, and contact alone never merges different first names.
                    contact = digits(check['phone']) or folded(check['email'])
                    contact = '' if len(named[contact]) > 3 else contact
                    groups[(contact, ''.join(words(label)[:1]) if contact else folded(label))].append((label, f['order'], check))
        rows = []
        for members in groups.values():
            label, _, first = members[0]
            rows.append({'name': label, 'also_written_as': sorted({m[0] for m in members} - {label})[:4], 'phone': first['phone'], 'email': first['email'],
                         'dates': sorted({o['date'] for _, o, _ in members})[-8:], **self._counts([{'order': o, 'checks': [c]} for _, o, c in members])})
        return sorted(rows, key=lambda r: (-r[rank], -r['orders'], -r['total_dollars'], r['name']))[:limit]

    def _row(self, found, servers):
        order = found['order']
        row = {'pos': order['pos'], 'order': order['order'], 'date': order['date'], 'opened': order['opened'], 'status': order['status'],
               'channel': self.labels.get(order['channel']) if order['pos'] == 'Toast' else order['channel'], 'match': found['match'],
               'checks': [{k: [dict(line) for line in v] if k == 'items' else v for k, v in check.items() if v not in ('', None, False, {}, 0)} for check in found['checks']]}
        for key in ('promised', 'recipient', 'guests', 'order_source'):
            if order.get(key):
                row[key] = order[key]
        if order.get('server') in servers:
            row['server'] = servers[order['server']]
        if len(found['checks']) < len(order['checks']):
            row['other_checks_on_order'] = len(order['checks']) - len(found['checks'])
        return row

    @staticmethod
    def _fit(result):
        """Drop whole rows, never cut JSON mid-record, and say what was left out."""
        def large():
            return len(json.dumps(result)) > OUTPUT_CHARS
        lists = [k for k in ('nearby_dates_orders', 'similar_name_orders', 'orders') if result.get(k)]
        if large() and lists:
            for key in lists:
                for row in result[key]:
                    for check in row['checks']:
                        for line in check.get('items', []):
                            line.pop('mods', None)
            result['modifiers_omitted'] = True
        for key in lists + ['by_person', 'people']:
            total = len(result.get(key) or [])
            while len(result.get(key) or []) > 3 and large():
                del result[key][-max(1, len(result[key]) // 10):]
            if total != len(result.get(key) or []):
                result[key + '_omitted'] = total - len(result[key])
        return result
