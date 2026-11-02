"""Owner-requested Toast guest search. No model, web search or durable guest index."""
import asyncio
import base64
import json
import re
import time
import unicodedata
import calendar
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from .calculations import business_day
from .providers import ProviderError

HELP = ('Toast guest lookup: /guest name Jane Smith (or email jane@example.com, '
        'or phone 4045550123). Searches the last 7 business dates including today. '
        'Use /guest name Jane Smith | 31 for a longer window, or '
        '/guest name Jane Smith | YYYY-MM-DD | YYYY-MM-DD (maximum 31 dates). '
        'You can also say "find any orders from Sample Guest on October 25, 2026", "guest named Jane on Sunday, 10/25", or "show orders for jane@example.com last week". '
        'Only guests attached to available Toast orders can be found; this is not the full Toast guest directory.')

MONTHS={name.casefold():number for number in range(1,13) for name in (calendar.month_name[number],calendar.month_abbr[number])}
MONTHS['sept']=9
WEEKDAYS={name.casefold():number for number in range(7) for name in (calendar.day_name[number],calendar.day_abbr[number])}
MONTH_PATTERN='(?:'+'|'.join(sorted(MONTHS,key=len,reverse=True))+r')\.?'
WEEKDAY_PATTERN='(?:'+'|'.join(sorted(WEEKDAYS,key=len,reverse=True))+r')\.?'
DATE_PATTERN=(r'(?:\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}(?:/(?:\d{4}|\d{2}))?|'+MONTH_PATTERN+r'\s+\d{1,2}(?:st|nd|rd|th)?(?:,?\s+\d{4})?)')


class LookupInputError(ValueError):
    """A safe, actionable clarification that contains no guest details."""


def named_subject(text):
    match=re.search(r'\b(?:guest|customer)\s+(?:(?:with\s+(?:the\s+)?name)|named|called)\s+([^?!;\n]+)',text,re.I)
    return match[1].strip().rstrip('.') if match else None


def order_subject(text):
    """Recognize owner requests without passing private search terms to a model."""
    if text.strip().startswith('/'):
        return None
    text = re.sub(r'\s+and\s+(?:what\s+were\s+they|what\s+did\s+they\s+(?:order|buy)|show\s+(?:me\s+)?(?:the\s+)?(?:items|details))[?!.]*$', '', text.strip(), flags=re.I)
    match = re.search(r'\b(?:(?:find|show|list|get|fetch|pull(?:\s+up)?|look\s*up|search(?:\s+for)?)\s+(?:me\s+)?|(?:were|are)\s+there\s+|have\s+there\s+been\s+)'
                      r'(?:(?:all|the|any|some|matching)\s+)*orders?\s+(?:placed\s+by|from|for|by|under(?:\s+the\s+name)?)\s+(.+?)[?!.]*$', text, re.I)
    if match:
        subject = re.sub(r'\s+please$', '', match[1].strip(), flags=re.I)
        if re.fullmatch(r'(?:today|yesterday|this week|last week|\d{4}-\d{2}-\d{2})', subject, re.I):
            return None  # Ordinary period-only order questions are not guest searches.
        if re.fullmatch(r'(?:on\s+)?(?:'+WEEKDAY_PATTERN+r',?\s+)?'+DATE_PATTERN,subject,re.I):
            return None
        if re.fullmatch(r'(?:(?:on|last|this|next)\s+)?'+WEEKDAY_PATTERN,subject,re.I):return None
        if re.match(r'^(?:doordash|uber(?: eats)?|toast|clover|takeout|delivery|dine[ -]?in)(?:\s|$)', subject, re.I):
            return None
        return subject
    for pattern in (
        r'\bwhat\s+did\s+(.+?)\s+(?:order|buy|purchase)(?:\s+(.+?))?[?!.]*$',
        r'\bdid\s+(.+?)\s+(?:place\s+(?:(?:an?|any|some)\s+)?orders?|order|buy|purchase)(?:\s+(.+?))?[?!.]*$',
        r'\b(?:show|find|get|pull(?:\s+up)?)\s+(?:me\s+)?(.+?)[\x27\u2019]s\s+orders?(?:\s+(.+?))?[?!.]*$'):
        alternative=re.search(pattern,text,re.I)
        if alternative:
            person=alternative[1].strip()
            if person.casefold() in {'i','we','you','they','he','she','our team','the restaurant'}:return None
            period=alternative[2] or ''
            period=re.sub(r'^(?:from|for)\s+','on ',period,flags=re.I)
            return (person+' '+period).strip()
    return None


def natural_period(body, today):
    spoken=re.search(r'\s+(?:(?P<weekday>'+WEEKDAY_PATTERN+r')\s+(?:the\s+)?)?(?P<day>\d{1,2})(?:st|nd|rd|th)?\s+of\s+(?P<month>'+MONTH_PATTERN+r')(?:,?\s+(?P<year>\d{4}))?$',body,re.I)
    if spoken:
        normalized=(spoken['weekday']+' ' if spoken['weekday'] else '')+spoken['month']+' '+spoken['day']+(', '+spoken['year'] if spoken['year'] else '')
        _,period=natural_period('context '+normalized,today)
        return body[:spoken.start()].strip(),period
    # Dictation often describes one date twice: "last Friday on the 23rd".
    # Resolve the weekday locally, then require the ordinal to agree.
    redundant=re.search(r'\s+(?P<weekday>(?:(?:on|last|this|next)\s+)?'+WEEKDAY_PATTERN+r')(?:\s*,?\s+(?:on\s+)?(?:the\s+)?|,\s*)(?P<day>\d{1,2})(?:st|nd|rd|th)$',body,re.I)
    if redundant:
        _,period=natural_period('context '+redundant['weekday'],today)
        if not period or period[0].day!=int(redundant['day']):
            raise LookupInputError('The weekday and day of the month do not agree. Please confirm the business date.')
        return body[:redundant.start()].strip(),period
    weekday=re.search(r'\s+(?:(on|last|this|next)\s+)?('+WEEKDAY_PATTERN+r')$',body,re.I)
    if weekday:
        if (weekday[1] or '').casefold() in {'this','next'}:
            raise LookupInputError('Please give the exact business date for this/next weekday, so I do not guess which week you mean.')
        offset=(today.weekday()-WEEKDAYS[weekday[2].rstrip('.').casefold()])%7
        if (weekday[1] or '').casefold()=='last' and offset==0:offset=7
        day=today-timedelta(days=offset)
        return body[:weekday.start()].strip(),(day,day)
    # Explicit formats are parsed locally, before guest identity normalization.
    explicit=re.search(r'\s+(?:on\s+)?(?:(?P<weekday>'+WEEKDAY_PATTERN+r'),?\s+)?(?P<date>'+DATE_PATTERN+r')$',body,re.I)
    if explicit and not re.search(r'\s+from \d{4}-\d{2}-\d{2} (?:to|through) \d{4}-\d{2}-\d{2}$',body,re.I):
        raw=explicit['date'];expected=explicit['weekday']
        try:
            if re.fullmatch(r'\d{4}-\d{2}-\d{2}',raw):
                day=date.fromisoformat(raw)
            elif '/' in raw:
                parts=[int(part) for part in raw.split('/')]
                year=parts[2] if len(parts)==3 else today.year
                if year<100:year+=2000
                day=date(year,parts[0],parts[1])
            else:
                parts=re.fullmatch(r'('+MONTH_PATTERN+r')\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s+(\d{4}))?',raw,re.I)
                day=date(int(parts[3]) if parts[3] else today.year,MONTHS[parts[1].rstrip('.').casefold()],int(parts[2]))
        except (ValueError,TypeError,AttributeError):
            raise LookupInputError('That is not a valid date. Please give the Toast business date as YYYY-MM-DD.') from None
        if expected and WEEKDAYS[expected.rstrip('.').casefold()]!=day.weekday():
            raise LookupInputError('The weekday does not match that date. Please confirm the business date, including its year.')
        if day>today:
            raise LookupInputError('That date is in the future. Dates without a year use the current business year; please confirm the year.')
        return body[:explicit.start()].strip().rstrip(','),(day,day)
    match = re.search(r'\s+(today|yesterday|this week|last week|(?:in the |over the |for the )?last \d+ days|'
                      r'(?:on )?\d{4}-\d{2}-\d{2}|from \d{4}-\d{2}-\d{2} (?:to|through) \d{4}-\d{2}-\d{2})$', body, re.I)
    if not match:
        if re.search(r'\s+(?:(?:last|this|next|on)\s+\w+|tomorrow|(?:since|between|during)\s+.+)$', body, re.I):
            raise ValueError('Please use today, yesterday, this week, last week, last N days, or an explicit YYYY-MM-DD date/range. ' + HELP)
        return body, None
    period = match[1].lower()
    start = end = today
    if period == 'yesterday':
        start = end = today - timedelta(days=1)
    elif period in ('this week','last week'):
        start = today - timedelta(days=(today.weekday()-2)%7)  # Wednesday-Tuesday.
        end = today
        if period == 'last week':
            start -= timedelta(days=7)
            end = start + timedelta(days=6)
    elif 'last ' in period and period.endswith(' days'):
        start = today - timedelta(days=int(re.search(r'\d+', period)[0])-1)
    elif period.startswith('from '):
        days = re.findall(r'\d{4}-\d{2}-\d{2}',period)
        start,end = (date.fromisoformat(d) for d in days)
    elif period not in ('today','yesterday','this week','last week'):
        start = end = date.fromisoformat(period.removeprefix('on '))
    return body[:match.start()].strip(), (start,end)


def is_guest_lookup(text):
    if re.match(r'^/guest(?:\s|$)', text.strip(), re.I):
        return True
    if text.strip().startswith('/'):
        return False
    if order_subject(text) is not None:
        return True
    if named_subject(text) is not None:
        return True
    return bool(re.search(r'\b(guest|customer)\b', text, re.I) and
                re.search(r'\b(look\s*up|lookup|find|search|check|fetch|pull)\b', text, re.I))


def clean(value, limit=100):
    return ''.join(c for c in str(value or '') if not unicodedata.category(c).startswith('C'))[:limit].strip()


def folded(value):
    return ' '.join(unicodedata.normalize('NFKC', value).casefold().split())


def phone_key(value):
    digits = re.sub(r'\D', '', value)
    return digits[1:] if len(digits) == 11 and digits.startswith('1') else digits


def parse(text, today):
    parts = text.strip().split('|')
    body = parts[0].strip()
    original=body.rstrip('?!.' )
    named=None
    subject = order_subject(body)
    if subject is not None:
        body = subject
        body = re.sub(r'^(?:the )?(?:guest|customer)\s+(?:named\s+)?', '', body, flags=re.I)
        body = re.sub(r'^(?:the )?(?:tab|check)(?:\s+(?:named|name))?\s+', '', body, flags=re.I)
    elif body.lower().startswith('/guest'):
        body = body[6:].strip()
    elif named_subject(body) is not None:
        named=named_subject(body)
        body=named
    else:
        found = re.search(r'\b(?:guest|customer)\b\s*(?:lookup\s*)?(.*)$', body, re.I)
        body = found.group(1).strip() if found else ''
    body = re.sub(r'^(?:by\s+)?(?:named\s+)', '', body, flags=re.I)
    body = body.rstrip('?!').strip()
    try:
        body, period = natural_period(body, today)
        if named and not period:
            _,period=natural_period(original,today)
    except LookupInputError:
        raise
    except (ValueError, OverflowError):
        raise ValueError('Use a valid supported date or a window of up to 31 business dates. ' + HELP) from None
    if period:
        if len(parts) != 1:
            raise ValueError('Specify the period once, using words or the | date syntax. ' + HELP)
        parts = [parts[0], period[0].isoformat(), period[1].isoformat()]
    match = re.match(r'^(?:by\s+)?(name|email|phone)\s*:?\s+(.+)$', body, re.I)
    kind, query = (match[1].lower(), match[2]) if match else ('', body)
    query = query.strip().rstrip('?').strip().strip('\"\x27\u201c\u201d')
    if not kind:
        kind = 'email' if '@' in query else 'phone' if re.fullmatch(r'[+()\d .-]+', query) else 'name'
    if not query or len(query) > 100 or clean(query, 101) != query:
        raise ValueError(HELP)
    if kind == 'email':
        if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', query):
            raise ValueError('Use a complete email address. ' + HELP)
        query = query.casefold()
    elif kind == 'phone':
        if not re.fullmatch(r'[+()\d .-]+', query) or not 8 <= len(phone_key(query)) <= 15:
            raise ValueError('Use a complete phone number, including area code. ' + HELP)
        query = phone_key(query)
    else:
        query = folded(query)
        if sum(c.isalpha() for c in query) < 3:
            raise ValueError('Enter at least three letters of a guest name. ' + HELP)
    start, end = today - timedelta(days=6), today
    try:
        if len(parts) == 2:
            days = int(parts[1].strip())
            if not 1 <= days <= 31:
                raise ValueError()
            start = today - timedelta(days=days - 1)
        elif len(parts) == 3:
            start, end = (date.fromisoformat(p.strip()) for p in parts[1:])
        elif len(parts) > 3:
            raise ValueError()
        if start > end or end > today or (end - start).days >= 31:
            raise ValueError()
    except ValueError:
        raise ValueError('Choose up to 31 business dates, ending no later than today. ' + HELP) from None
    return kind, query, start, end


def matches(customer, kind, query):
    if kind == 'email':
        return str(customer.get('email') or '').strip().casefold() == query
    if kind == 'phone':
        return phone_key(str(customer.get('phone') or '')) == query
    name = folded(' '.join(str(customer.get(k) or '') for k in ('firstName', 'lastName')))
    return all(token in name.split() for token in query.split())


def tab_matches(label,query):
    """Whole tokens with punctuation normalized; no fuzzy identity inference."""
    words=re.findall(r'[^\W_]+',folded(label))
    wanted=re.findall(r'[^\W_]+',folded(query))
    return bool(wanted) and any(words[i:i+len(wanted)]==wanted for i in range(len(words)-len(wanted)+1))


def guest_permission(toast):
    """Diagnostic only: never use unsigned JWT metadata to grant access."""
    try:
        token = toast.token
        payload = json.loads(base64.urlsafe_b64decode(token.split('.')[1] + '==='))
        scopes = payload.get('scope', payload.get('scopes'))
        if isinstance(scopes, str):
            scopes = re.split(r'[\s,]+', scopes)
        if isinstance(scopes, list) and all(isinstance(s, str) for s in scopes):
            return 'guest.pi:read' in scopes
    except (AttributeError, ValueError, IndexError, TypeError):
        pass
    return None


class GuestLookup:
    def __init__(self, toast):
        self.toast = toast
        self.lock = asyncio.Lock()
        self.last_started = 0

    async def search(self, text, closeout=4, now=None):
        now = now or datetime.now(timezone.utc)
        today = business_day(now, closeout)
        kind, query, start, end = parse(text, today)
        if self.lock.locked() or time.monotonic() - self.last_started < 10:
            raise ValueError('A guest lookup is running or just finished. Please retry in a few seconds.')
        async with self.lock:
            self.last_started = time.monotonic()
            result = {'source': 'Toast order-linked guests', 'start': start.isoformat(), 'end': end.isoformat(),
                      'checked_at': now.isoformat(), 'complete_dates': [], 'requests': 0, 'matches': [],
                      'partial': False, 'customer_field_seen': False, 'populated_customers': 0,
                      'searchable_customers': 0, 'blocked': False, 'notice': '', 'truncated': False,
                      'show_orders': order_subject(text) is not None, 'orders': [], 'orders_truncated': False,
                      'tab_matching_checks':0,'populated_tabs':0}
            found, seen = {}, set()
            matched_orders = {}
            try:
                async with asyncio.timeout(90):
                    day = end
                    while day >= start:
                        page = 1
                        while True:
                            if result['requests'] >= 60:
                                result['partial'] = True
                                result['notice'] = 'Stopped at the 60-page lookup limit; narrow the date range.'
                                break
                            result['requests'] += 1
                            rows = await self.toast.get('/orders/v2/ordersBulk',
                                {'businessDate': day.strftime('%Y%m%d'), 'page': page, 'pageSize': 100}, ttl=120)
                            if guest_permission(self.toast) is False:
                                result['blocked'] = True
                                result['notice'] = ('Guest lookup unavailable: the current Toast API token does not include guest.pi:read. '
                                    'Ask the Toast API administrator/support to grant guest.pi:read to this existing integration. '
                                    'Do not share a token or secret in chat. Retry after Toast activates access and the API token refreshes.')
                                break
                            if not isinstance(rows, list) or any(not isinstance(o, dict) for o in rows):
                                raise ProviderError('Unexpected Toast orders response.')
                            for order in rows:
                                oid = order.get('guid')
                                if not oid or oid in seen:
                                    continue
                                seen.add(oid)
                                seen_checks = set()
                                for check in order.get('checks') or []:
                                    if not isinstance(check, dict):
                                        raise ProviderError('Unexpected Toast check response.')
                                    check_id = check.get('guid')
                                    if check_id:
                                        if check_id in seen_checks:
                                            continue
                                        seen_checks.add(check_id)
                                    result['customer_field_seen'] |= 'customer' in check
                                    customer = check.get('customer')
                                    customer=customer if isinstance(customer,dict) else {}
                                    if customer:
                                        result['populated_customers'] += 1
                                        result['searchable_customers'] += bool(any(customer.get(k) for k in ('firstName','lastName','email','phone')))
                                    guest_match=bool(customer) and matches(customer, kind, query)
                                    label=check.get('tabName')
                                    result['populated_tabs']+=bool(label)
                                    tab_match=kind=='name' and isinstance(label,str) and tab_matches(label,query)
                                    result['tab_matching_checks']+=bool(tab_match)
                                    if not guest_match and not tab_match:
                                        continue
                                    if result['show_orders']:
                                        if oid in matched_orders or len(matched_orders) < 50:
                                            row = matched_orders.setdefault(oid, {
                                                'number': clean(order.get('displayNumber') or order.get('number') or 'unavailable', 30),
                                                'business_date': day.isoformat(), 'opened_at': clean(order.get('openedDate'), 40),
                                                'voided': bool(order.get('voided')), 'deleted': bool(order.get('deleted')),
                                                'guests': [], 'tab_names':[], 'match_types':[], 'matching_checks': 0, 'matching_check_total': Decimal(0),
                                                'check_totals':[], 'check_totals_truncated':False,
                                                'items': [], 'items_truncated': False})
                                            if guest_match and 'guest_profile' not in row['match_types']:row['match_types'].append('guest_profile')
                                            if tab_match:
                                                if 'tab_label_candidate' not in row['match_types']:row['match_types'].append('tab_label_candidate')
                                                tab=clean(label)
                                                if tab not in row['tab_names'] and len(row['tab_names'])<10:row['tab_names'].append(tab)
                                            add_check(row, check, customer if guest_match else {})
                                        else:
                                            result['orders_truncated'] = True
                                    if not guest_match:continue
                                    # Never merge two guests by name, shared contact info, or fuzzy matches.
                                    cid = customer.get('guid') or (oid, check.get('guid'))
                                    if cid not in found:
                                        if len(found) >= 10:
                                            result['truncated'] = True
                                            continue
                                        found[cid] = {'name': clean(' '.join(str(customer.get(k) or '') for k in ('firstName','lastName'))),
                                                      'email': clean(customer.get('email')), 'phone': clean(customer.get('phone')),
                                                      'last_seen': day.isoformat(), 'orders': set()}
                                    found[cid]['orders'].add(oid)
                            if len(rows) < 100:
                                result['complete_dates'].append(day.isoformat())
                                break
                            page += 1
                        if result['partial'] or result['blocked']:
                            break
                        day -= timedelta(days=1)
            except (ProviderError, TimeoutError, TypeError, ValueError):
                result['partial'] = True
                result['notice'] = 'Toast lookup interrupted or unavailable; any results below are partial. Retry with a shorter range.'
            result['matches'] = [{**row, 'orders': len(row['orders'])} for row in found.values()]
            result['orders'] = [{**row, 'matching_check_total': str(row['matching_check_total']) if row['matching_check_total'] is not None else None}
                                for row in matched_orders.values()]
            return result


def add_check(row, check, customer):
    """Only matching checks, never another guest's split check or payment data."""
    name = clean(' '.join(str(customer.get(k) or '') for k in ('firstName','lastName')))
    if name and name not in row['guests'] and len(row['guests']) < 10:
        row['guests'].append(name)
    row['matching_checks'] += 1
    check_total=None
    try:
        amount = Decimal(str(check['totalAmount']))
        if not amount.is_finite() or abs(amount) > Decimal('10000000'):
            raise ValueError()
        amount=amount.quantize(Decimal('0.01'));check_total=str(amount)
        if row['matching_check_total'] is not None:
            row['matching_check_total'] += amount
    except (KeyError, InvalidOperation, ValueError):
        row['matching_check_total'] = None
    totals=row.setdefault('check_totals',[])
    if len(totals)<50:totals.append(check_total)
    else:row['check_totals_truncated']=True
    for selection in check.get('selections') or []:
        if not isinstance(selection, dict):
            raise ProviderError('Unexpected Toast selection response.')
        if len(row['items']) >= 20:
            row['items_truncated'] = True
            break
        # Display item names only; quantities/modifiers are not normalized PMIX.
        row['items'].append({'name': clean(selection.get('displayName')) or '(item unnamed)',
                             'voided': bool(selection.get('voided'))})


def individual_check_totals(row):
    if 'check_totals' in row:values=row['check_totals']
    # Older/synthetic projections can establish an individual total only when
    # precisely one matching check is present. Never divide an order aggregate.
    elif row.get('matching_checks')==1:values=[row.get('matching_check_total')]
    else:return []
    out=[]
    for value in values[:50]:
        try:
            amount=Decimal(str(value))
            out.append(format(amount,'.2f') if amount.is_finite() and abs(amount)<=Decimal('10000000') else None)
        except (InvalidOperation,ValueError,TypeError):out.append(None)
    return out


def combined_check_total(row):
    amounts=individual_check_totals(row)
    if (len(amounts)<2 or len(amounts)!=row.get('matching_checks') or
            row.get('check_totals_truncated') or any(v is None for v in amounts)):
        return None
    return format(sum((Decimal(v) for v in amounts),Decimal(0)),'.2f')


def render(result):
    lines = [f"Toast guest lookup | {result['start']} through {result['end']}",
             f"Checked: {result['checked_at']} | Complete business dates: {len(result['complete_dates'])}"]
    if result['notice']:
        lines.append(result['notice'])
    if result['matches']:
        lines.append('Matching guest records (separate people may share a name/contact):')
        for i, row in enumerate(result['matches'], 1):
            lines.append(f"{i}. {row['name'] or '(name unavailable)'}")
            if not result.get('show_orders'):
                lines.append(f"Email: {row['email'] or 'not supplied'} | Phone: {row['phone'] or 'not supplied'}")
            lines.append(f"Last order seen: {row['last_seen']} | Observed orders in searched period: {row['orders']}")
    elif not result['blocked']:
        lines.append('No matching guest found in the searched guest-profile records. This does not mean the guest is absent from Toast; tab-label candidates are separate from guest profiles.')
    if result.get('tab_matching_checks'):
        lines.append(f"Tab-label matching checks: {result['tab_matching_checks']}. Tab labels are candidates, not verified guest identities.")
        if not result.get('show_orders'):lines.append('Ask to show orders for that name to view matching tab-label candidates; tab names do not supply guest contact details.')
    if result['truncated']:
        lines.append('Only the first 10 matching guest records are shown. Narrow your search.')
    if result.get('show_orders') and not result['blocked']:
        lines.append(f"Matching orders shown: {len(result['orders'])} (maximum 50). Name matches are candidates, not proof of identity.")
        for row in result['orders']:
            flags = (' | VOIDED' if row['voided'] else '') + (' | DELETED' if row['deleted'] else '')
            lines.extend([f"Order #{row['number']} | {row['business_date']} | Opened: {row['opened_at'] or 'unavailable'}{flags}",
                          f"Guest: {', '.join(row['guests']) or 'unnamed'} | Matching checks: {row['matching_checks']}"])
            totals=individual_check_totals(row)
            lines.append(' | '.join(f"Check {i} total: {'$'+amount if amount is not None else 'unavailable'}" for i,amount in enumerate(totals,1)) if totals else 'Individual check totals unavailable.')
            if row['matching_checks']>1:
                combined=combined_check_total(row)
                lines.append('Combined total for these matching checks: '+('$'+combined if combined is not None else 'unavailable (not all individual totals are available).'))
            if row.get('check_totals_truncated'):lines.append('Check totals truncated at 50 matching checks for this order.')
            if row.get('tab_names'):lines.append('Tab-label candidate: '+', '.join(row['tab_names'])+' (not a verified guest identity)')
            if row['items']:
                lines.append('Items: '+', '.join(i['name']+(' (voided)' if i['voided'] else '') for i in row['items']))
            if row['items_truncated']:
                lines.append('Item list truncated at 20 selections.')
        if result['orders_truncated']:
            lines.append('Order list truncated at 50. Narrow the dates/name; this is not every matching order.')
        if result['partial']:
            lines.append('Order search coverage is partial; do not treat this as all orders for the period.')
        lines.append('Only checks matching a guest profile or tab label are shown, excluding unrelated split checks. Check totals use Toast totalAmount, are not adjusted for refunds, and are not net sales or proof of payment; modifiers are excluded. Tab labels do not establish who ordered or paid.')
    if result['blocked']:
        pass
    elif not result['customer_field_seen'] and not result['partial']:
        lines.append('No customer fields were returned. Check whether the Toast API client has guest.pi:read permission.')
    elif not result['searchable_customers']:
        lines.append('No populated guest records were returned in this sample; guest availability/permissions may be limited.')
    lines.append('Source: Toast Orders API; guest profiles when supplied and check-level tab-label candidates for name searches. Not the complete guest directory. Name/tab searches use whole words; email and phone require complete values. No addresses or payment details displayed. Records are matched locally; no guest results are sent to AI or public web search.')
    return '\n'.join(lines)
